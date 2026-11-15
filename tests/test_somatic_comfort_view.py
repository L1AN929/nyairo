#!/usr/bin/env python3
"""具身冷暖/干湿/坐姿/触觉、窗扇与外衣、桌边温饮靠垫：纯投影 + 真实 owner loopback。

上半部分只喂真实形状的 World/Body 快照给纯投影函数：窗侧 cool 只在显式位置与开窗
同时成立；关窗同地消失；没有姿态持续证据不说久坐；没有靠垫不诊断紧痛；温杯拿起与
真实饮用分开；微潮干燥只按 elapsed 派生，时钟回退保守 unknown；冷却不因重放刷新。

下半部分（POSIX）用 ``somatic_comfort_fixture.build_stack``：真实 Supply owner、
``one_day_life.prepare``、真实 World/Body gateway、显式 lived-needs profile、真实相册/
分享/购买/阅读停点，再走真实 HTTP：CSRF / capability / 字段存在性 / 幂等 / 零写 /
本人开关窗同位置 cool 变化 / 跨重开干燥连续 / drink 负控。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import somatic_comfort_view as view  # noqa: E402

SUBJECT = "chiyo"
CSRF = "somatic-comfort-csrf"
CAP_TOKEN = "{{SECRET_somatic9cap}}"

#: The two frozen load-bearing files must stay byte-identical for this slice.
FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}

WORLD = {
    "ok": True, "status": "applied",
    "data": {
        "location": {"status": "available", "place_id": "home", "area_id": "bedroom",
                     "home": True},
        "world": {"local_time": "2026-11-11T12:00:00+08:00",
                  "local_date": "2026-11-11", "revision": 7},
        "local_environment": {
            "status": "available",
            "furniture": [{"object_id": "plant-1", "kind": "plant", "name": "盆栽"}],
            "light": {"status": "available", "power": "on", "band": "moderate"},
        },
        "travel": {"status": "idle"},
    },
}

BODY = {
    "ok": True, "status": "available",
    "data": {"pose": "seated", "orientation": "north",
             "recovery": {"state": "idle", "mode": None},
             "signals": [{"signal_type": "fatigue", "qualitative_band": "mild"}],
             "needs": {"hunger": "mild", "thirst": "noticeable", "toilet": "none"},
             "rest_evidence": None, "signals_status": "available"},
}


def _somatic(state: dict, *, now: str = "2026-11-11T12:00:00+08:00",
             world: dict | None = None, body: dict | None = None) -> dict:
    return view.somatic_block(world_result=world or WORLD, body_view=body or BODY,
                              state=state, state_reason=None, now_iso=now,
                              soundscape={"available": False,
                                          "reason_code": "FEATURE_DISABLED",
                                          "events": [], "template_projection": True,
                                          "is_weather": False})


def _window_side_state(window: str = "open") -> dict:
    state = view.empty_state()
    state, _changed, error = view.set_window(state, window, "2026-11-11T12:00:00+08:00")
    assert error is None
    state, _changed, error = view.set_local_position(
        state, zone="window_side", place_id="home", area_id="bedroom",
        now_iso="2026-11-11T12:00:00+08:00")
    assert error is None
    return state


class SomaticProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="somatic-state-"))

    def test_missing_state_is_empty_and_never_creates_a_file(self):
        state, reason = view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(state["revision"], 0)
        self.assertFalse(view.state_path(self.root).exists())
        self.assertFalse((self.root / "phone").exists())
        block = _somatic(state)
        self.assertEqual(block["modality_count"], 4)
        self.assertIn("thermal", block["modalities"])
        self.assertFalse(block["layers"]["bounded_projection"]["writes_body"])
        self.assertFalse(block["layers"]["cognition_choice"]["in_scope"])

    def test_window_state_is_idempotent_and_bounded(self):
        state = view.empty_state()
        state, changed, error = view.set_window(state, "open", "2026-11-11T12:00:00+08:00")
        self.assertTrue(changed)
        self.assertIsNone(error)
        again, changed, error = view.set_window(state, "open",
                                                "2026-11-11T12:05:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(again["revision"], 1)
        _bad, changed, error = view.set_window(state, "slammed",
                                               "2026-11-11T12:05:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "WINDOW_STATE_INVALID")

    def test_window_side_cool_only_with_declared_position_and_open_window(self):
        open_side = _somatic(_window_side_state("open"))
        self.assertEqual(open_side["modalities"]["thermal"]["quality"], "cool")
        self.assertTrue(open_side["modalities"]["thermal"]["simulation"])

        closed = _somatic(_window_side_state("closed"))
        self.assertEqual(closed["modalities"]["thermal"]["status"], "none")
        self.assertEqual(closed["modalities"]["thermal"]["reason_code"],
                         "WINDOW_CLOSED_NO_THERMAL_SOURCE")

        # The bedroom name alone is not a window side.
        no_position = view.empty_state()
        no_position, _changed, _error = view.set_window(
            no_position, "open", "2026-11-11T12:00:00+08:00")
        unknown = _somatic(no_position)
        self.assertEqual(unknown["modalities"]["thermal"]["status"], "unknown")
        self.assertEqual(unknown["modalities"]["thermal"]["reason_code"],
                         "NO_WINDOW_PROXIMITY_EVIDENCE")

        # A declared position bound to another area is not this room's cool signal.
        other = view.empty_state()
        other, _c, _e = view.set_window(other, "open", "2026-11-11T12:00:00+08:00")
        other, _c, _e = view.set_local_position(
            other, zone="window_side", place_id="home", area_id="living",
            now_iso="2026-11-11T12:00:00+08:00")
        moved = _somatic(other)
        self.assertEqual(moved["modalities"]["thermal"]["status"], "unknown")

    def test_outerwear_is_exclusive_and_new_instance_is_unknown(self):
        state = view.empty_state()
        self.assertEqual(view.derive_garment(state, "2026-11-11T12:00:00+08:00")["status"],
                         "unknown")
        state, changed, error = view.wear_outerwear(
            state, garment_id="coat-1", label="外套", dryness="damp",
            now_iso="2026-11-11T12:00:00+08:00")
        self.assertTrue(changed)
        self.assertIsNone(error)
        self.assertEqual(state["garment"]["state"], "wear")
        self.assertIsNone(state["garment"]["hang_position"])
        state, changed, error = view.hang_outerwear(
            state, garment_id="coat-1", hang_position="entry_hook", dryness="damp",
            now_iso="2026-11-11T12:01:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(state["garment"]["state"], "hang")
        self.assertEqual(state["garment"]["hang_position"], "entry_hook")
        # Only one state exists: it cannot be worn and hung at the same time.
        self.assertNotEqual(state["garment"]["state"], "wear")

    def test_hanging_damp_outerwear_dries_by_elapsed_only(self):
        state = view.empty_state()
        state, _c, _e = view.wear_outerwear(state, garment_id="coat-1", label="外套",
                                            dryness="damp",
                                            now_iso="2026-11-11T12:00:00+08:00")
        state, _c, _e = view.hang_outerwear(
            state, garment_id="coat-1", hang_position="hook", dryness="damp",
            now_iso="2026-11-11T12:00:00+08:00")
        started = state["garment"]["drying"]["started_at"]
        self.assertEqual(view.derive_garment(
            state, "2026-11-11T14:00:00+08:00")["dryness"], "damp")
        # The same intent never resets the clock or the revision.
        again, changed, _error = view.hang_outerwear(
            state, garment_id="coat-1", hang_position="hook", dryness="damp",
            now_iso="2026-11-11T18:05:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(again["garment"]["drying"]["started_at"], started)
        self.assertEqual(view.derive_garment(again, "2026-11-11T18:05:00+08:00")["dryness"],
                         "dry")
        # A backwards clock is conservative: unknown, not "instant dry".
        rolled = view.derive_garment(again, "2026-11-11T08:00:00+08:00")
        self.assertEqual(rolled["dryness"], "unknown")
        self.assertEqual(rolled["drying"]["reason_code"], "CLOCK_ROLLBACK")

    def test_posture_never_claims_sustained_sitting_or_pain(self):
        state = _window_side_state()
        seated = _somatic(state)
        posture = seated["modalities"]["posture"]
        self.assertEqual(posture["quality"], "seated_support_unverified")
        self.assertIsNone(posture["sustained_duration_seconds"])
        self.assertEqual(posture["duration_reason_code"], "NO_POSTURE_DURATION_EVIDENCE")
        self.assertIsNone(posture["pain_claim"])
        self.assertFalse(posture["diagnoses_pain"])

        lying = dict(BODY)
        lying["data"] = dict(BODY["data"], pose="lying",
                             rest_evidence="pose:lying@bed_surface")
        supported = _somatic(state, body=lying)["modalities"]["posture"]
        self.assertEqual(supported["quality"], "supported_world_surface")
        self.assertIn("body:rest_evidence:pose:lying@bed_surface",
                      supported["source_refs"])

    def test_warm_object_is_reachable_then_held_only_after_taking(self):
        state = view.empty_state()
        state, changed, error, offer = view.offer_care(
            state, slot="drink", kind="warm_water", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00",
            bound_item={"item_instance_id": "item-water", "label": "温水"})
        self.assertTrue(changed)
        self.assertIsNone(error)
        reachable = _somatic(state, now="2026-11-11T12:01:00+08:00")
        self.assertEqual(reachable["modalities"]["tactile"]["quality"],
                         "reachable_warm_object")
        state, changed, error, _offer = view.take_drink(
            state, place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:02:00+08:00")
        self.assertTrue(changed)
        held = _somatic(state, now="2026-11-11T12:03:00+08:00")
        self.assertEqual(held["modalities"]["tactile"]["quality"], "held_warm_contact")
        # The local offer itself changes no need / relationship / Activity fact.
        drink = held and view.derive_care(state, now_iso="2026-11-11T12:03:00+08:00",
                                          place_id="home", area_id="bedroom",
                                          pose="seated")["drink"]
        self.assertFalse(drink["changes_needs"])

    def test_drink_temperature_cools_by_elapsed_and_replay_never_resets(self):
        state = view.empty_state()
        state, _changed, _error, offer = view.offer_care(
            state, slot="drink", kind="hot_tea", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00")
        placed = offer["offered_at"]
        self.assertEqual(view.temperature_band(offer, "2026-11-11T12:05:00+08:00")["band"],
                         "warm")
        self.assertEqual(view.temperature_band(offer, "2026-11-11T12:15:00+08:00")["band"],
                         "tepid")
        self.assertEqual(view.temperature_band(offer, "2026-11-11T12:40:00+08:00")["band"],
                         "room_temp")
        rollback = view.temperature_band(offer, "2026-11-11T11:00:00+08:00")
        self.assertEqual(rollback["band"], "unknown")
        self.assertEqual(rollback["reason_code"], "CLOCK_ROLLBACK")
        again, changed, _error, same = view.offer_care(
            state, slot="drink", kind="hot_tea", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:30:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(same["offered_at"], placed)

    def test_care_slots_replace_only_while_untouched(self):
        state = view.empty_state()
        state, _c, _e, _offer = view.offer_care(
            state, slot="drink", kind="warm_water", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00")
        replaced, changed, error, offer = view.offer_care(
            state, slot="drink", kind="hot_tea", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:01:00+08:00")
        self.assertTrue(changed)
        self.assertIsNone(error)
        self.assertEqual(offer["kind"], "hot_tea")
        state, _c, _e, _offer = view.take_drink(
            replaced, place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:02:00+08:00")
        locked, changed, error, _offer = view.offer_care(
            state, slot="drink", kind="warm_water", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:03:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "CARE_SLOT_LOCKED_BY_SUBJECT")
        self.assertEqual(locked["care"]["drink"]["state"], "taken")

    def test_cushion_support_requires_seated_and_same_area(self):
        state = view.empty_state()
        state, _c, _e, _offer = view.offer_care(
            state, slot="cushion", kind="floor_cushion", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00")
        _s, changed, error, _offer = view.use_cushion(
            state, pose="standing", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:01:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "POSTURE_NOT_SEATED")
        used, changed, error, _offer = view.use_cushion(
            state, pose="seated", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:02:00+08:00")
        self.assertTrue(changed)
        self.assertIsNone(error)
        active = view.derive_care(used, now_iso="2026-11-11T12:03:00+08:00",
                                  place_id="home", area_id="bedroom", pose="seated")
        self.assertTrue(active["cushion"]["support_active"])
        stood = view.derive_care(used, now_iso="2026-11-11T12:04:00+08:00",
                                 place_id="home", area_id="bedroom", pose="standing")
        self.assertFalse(stood["cushion"]["support_active"])
        self.assertEqual(stood["cushion"]["support_reason_code"], "POSTURE_CHANGED")
        # GET never rewrote the state: the file still says in_use.
        self.assertEqual(used["care"]["cushion"]["state"], "in_use")

    def test_partner_cannot_withdraw_touched_or_subject_offer(self):
        state = view.empty_state()
        state, _c, _e, _offer = view.offer_care(
            state, slot="cushion", kind="floor_cushion", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00")
        withdrawn, changed, error, _offer = view.withdraw_care(
            state, slot="cushion", now_iso="2026-11-11T12:01:00+08:00")
        self.assertTrue(changed)
        self.assertIsNone(error)

        state2, _c, _e, _offer = view.offer_care(
            view.empty_state(), slot="drink", kind="warm_water", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00")
        state2, _c, _e, _offer = view.take_drink(
            state2, place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:02:00+08:00")
        _s, changed, error, _offer = view.withdraw_care(
            state2, slot="drink", now_iso="2026-11-11T12:03:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "SUBJECT_HAS_TOUCHED")

        her = view.empty_state()
        her, _c, _e, _offer = view.offer_care(
            her, slot="drink", kind="warm_water", actor_side="her",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00")
        _s, changed, error, _offer = view.withdraw_care(
            her, slot="drink", now_iso="2026-11-11T12:01:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "WITHDRAW_NOT_OWNER")

    def test_marked_drink_consumed_never_bumps_twice(self):
        state = view.empty_state()
        state, _c, _e, _offer = view.offer_care(
            state, slot="drink", kind="warm_water", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:00:00+08:00",
            bound_item={"item_instance_id": "item-water", "label": "温水"})
        state, _c, _e, _offer = view.take_drink(
            state, place_id="home", area_id="bedroom",
            now_iso="2026-11-11T12:01:00+08:00")
        consumed, changed = view.mark_drink_result(
            state, outcome="consumed", receipt_ref="receipt-1",
            operation_key="somatic-drink:one", now_iso="2026-11-11T12:02:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(consumed["care"]["drink"]["state"], "consumed")
        self.assertEqual(consumed["care"]["drink"]["receipt_ref"], "receipt-1")
        revision = consumed["revision"]
        replay, changed = view.mark_drink_result(
            consumed, outcome="consumed", receipt_ref="receipt-1",
            operation_key="somatic-drink:one", now_iso="2026-11-11T12:03:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(replay["revision"], revision)

    def test_corrupt_state_fails_closed_without_writing(self):
        path = view.state_path(self.root)
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        before = path.read_bytes()
        state, reason = view.read_state(self.root)
        self.assertEqual(reason, "SOMATIC_STATE_UNREADABLE")
        self.assertEqual(state["revision"], 0)
        block = view.somatic_block(world_result=WORLD, body_view=BODY, state=state,
                                   state_reason=reason,
                                   now_iso="2026-11-11T12:00:00+08:00",
                                   soundscape=None)
        self.assertEqual(block["local_state"]["status"], "unavailable")
        self.assertEqual(block["temporal_alignment"]["status"], "partial")
        self.assertEqual(path.read_bytes(), before)

    def test_frozen_anchor_bytes_match_the_baseline(self):
        for relative, expected in FROZEN_ANCHORS.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply/World/Body owner 需要 POSIX（在 WSL/Linux 上运行）")


@POSIX_ONLY
class SomaticOwnerHTTPTest(unittest.TestCase):
    """Real Supply/World/Body owners and real loopback HTTP."""

    @classmethod
    def setUpClass(cls) -> None:
        import somatic_comfort_fixture as fixture

        cls.fixture = fixture
        # The real World/Body service binds its local-owner (phone) command surface only
        # when this env is set at service construction -- the same explicit binding the
        # phone demo uses.  It is what lets the existing formal POSE command reach the
        # owner; without it the owner truthfully refuses LOCAL_OWNER_PHONE_NOT_BOUND.
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.root = Path(tempfile.mkdtemp(prefix="somatic-comfort-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN, subject=SUBJECT, endpoint="http://127.0.0.1:1",
            issued_at="2026-11-11T12:00:00+08:00")
        cls.stack = fixture.build_stack(cls.root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        view.write_state(self.stack.root, view.empty_state())

    # -- helpers -----------------------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None, *,
              csrf: bool = False, capability: str | None = None,
              headers: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            request.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            request.add_header("X-Phone-Capability", capability)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _her(self, path: str, body: dict) -> tuple[int, dict]:
        return self._call("POST", path, body, csrf=True, capability=CAP_TOKEN)

    def _partner(self, path: str, body: dict) -> tuple[int, dict]:
        return self._call("POST", path, body, csrf=True)

    def _state_bytes(self) -> bytes:
        return view.state_path(self.stack.root).read_bytes()

    def _owner_fingerprint(self) -> dict:
        import world_body_substrate as substrate
        import world_execution_ledger as ledger

        world_home = Path(str(self.stack.data["world_home"]))
        canonical = Path(self.stack.state_root) / "canonical_life_state.json"
        runtime = world_home / "data" / "body" / "runtime.json"
        return {
            "canonical": canonical.read_bytes(),
            "runtime": runtime.read_bytes() if runtime.exists() else b"",
            "ledger": ledger.ledger_path(world_home).read_bytes()
            if ledger.ledger_path(world_home).exists() else b"",
            "substrate": substrate.substrate_path(world_home).read_bytes()
            if substrate.substrate_path(world_home).exists() else b"",
        }

    def _advance_business_clock(self, iso: str) -> None:
        path = Path(self.stack.state_root) / "fastday" / "fastday_clock.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["logical_checkpoint"] = iso
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    # -- somatic GET -------------------------------------------------------------------
    def test_somatic_get_reads_real_body_and_world_without_writing(self):
        before = self._owner_fingerprint()
        first_status, first = self._call("GET", "/api/phone/body/somatic")
        second_status, second = self._call("GET", "/api/phone/body/somatic")
        self.assertEqual(first_status, 200, first)
        self.assertEqual(second_status, 200, second)
        self.assertEqual(first, second)
        self.assertEqual(self._owner_fingerprint(), before)
        data = first["data"]
        self.assertEqual(data["world"]["place_id"], self.stack.location["place_id"])
        self.assertEqual(data["body"]["pose"], "standing")
        self.assertIsInstance(data["body"]["needs"], dict)
        self.assertIn("thirst", data["body"]["needs"])
        self.assertFalse(data["raw_body_values_exposed"])
        self.assertFalse(data["temporal_alignment"]["atomic_snapshot"])
        self.assertEqual(set(data["modalities"]), set(view.MODALITIES))
        self.assertFalse(data["modalities"]["posture"]["diagnoses_pain"])

    # -- garment / window --------------------------------------------------------------
    def test_window_and_local_position_posts_are_her_only_and_idempotent(self):
        path = "/api/phone/body/garment-window"
        status, payload = self._call("POST", path, {"action": "set_window",
                                                    "state": "open"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")
        status, payload = self._call("POST", path, {"action": "set_window", "state": "open"},
                                     csrf=True, capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
        # Actor declaration is not identity.
        status, payload = self._call("POST", path, {"action": "set_window", "state": "open"},
                                     csrf=True, headers={"X-Phone-Actor": "her"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")

        status, payload = self._her(path, {"action": "set_window", "state": "open"})
        self.assertEqual(status, 200, payload)
        first = self._state_bytes()
        self.assertEqual(json.loads(first)["window"]["state"], "open")
        status, payload = self._her(path, {"action": "set_window", "state": "open"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(self._state_bytes(), first)

        status, payload = self._her(path, {"action": "set_local_position",
                                           "zone": "window_side"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["local_position"]["zone"], "window_side")

    def test_real_window_toggle_changes_cool_at_the_same_position(self):
        path = "/api/phone/body/garment-window"
        self.assertEqual(self._her(path, {"action": "set_local_position",
                                          "zone": "window_side"})[0], 200)
        self.assertEqual(self._her(path, {"action": "set_window", "state": "open"})[0], 200)
        status, payload = self._call("GET", "/api/phone/body/somatic")
        self.assertEqual(status, 200, payload)
        thermal = payload["data"]["modalities"]["thermal"]
        self.assertEqual(thermal["quality"], "cool")
        self.assertEqual(thermal["region"]["area_id"],
                         self.stack.location["area_id"])
        self.assertEqual(thermal["region"]["local_zone"], "window_side")
        self.assertTrue(thermal["simulation"])

        state_before = self._state_bytes()
        self.assertEqual(self._her(path, {"action": "set_window", "state": "closed"})[0], 200)
        status, payload = self._call("GET", "/api/phone/body/somatic")
        self.assertEqual(status, 200, payload)
        thermal = payload["data"]["modalities"]["thermal"]
        self.assertEqual(thermal["status"], "none")
        self.assertEqual(thermal["reason_code"], "WINDOW_CLOSED_NO_THERMAL_SOURCE")
        self.assertNotEqual(self._state_bytes(), state_before)

    def test_outerwear_hang_drying_continues_across_service_reopen(self):
        path = "/api/phone/body/garment-window"
        status, payload = self._her(path, {"action": "wear_outerwear", "garment_id": "coat-1",
                                           "label": "外套", "dryness": "damp"})
        self.assertEqual(status, 200, payload)
        status, payload = self._her(path, {"action": "hang_outerwear",
                                           "garment_id": "coat-1",
                                           "hang_position": "entry_hook",
                                           "dryness": "damp"})
        self.assertEqual(status, 200, payload)
        first = self._state_bytes()
        started = json.loads(first)["garment"]["drying"]["started_at"]
        status, payload = self._her(path, {"action": "hang_outerwear",
                                           "garment_id": "coat-1",
                                           "hang_position": "entry_hook",
                                           "dryness": "damp"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(self._state_bytes(), first)
        status, payload = self._call("GET", "/api/phone/body/garment-window")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["garment"]["dryness"], "damp")

        self._advance_business_clock("2026-11-11T20:00:00+08:00")
        status, payload = self._call("GET", "/api/phone/body/garment-window")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["garment"]["dryness"], "dry")
        self.assertEqual(payload["data"]["garment"]["drying"]["started_at"], started)

        reopened = phone_service.PhoneService(
            self.stack.instance,
            environment=dict(self.stack.data["environment"]), world_subject=SUBJECT)
        payload = reopened.body_garment_window()
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["data"]["garment"]["dryness"], "dry")
        self.assertEqual(payload["data"]["garment"]["drying"]["started_at"], started)

    # -- care offering -----------------------------------------------------------------
    def test_partner_offer_take_and_withdraw_permissions_without_body_writes(self):
        path = "/api/phone/body/care-offering"
        before = self._owner_fingerprint()
        status, payload = self._partner(path, {
            "action": "offer", "slot": "drink", "kind": "warm_water",
            "actor_side": "partner", "position": "hand_side"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["offer"]["state"], "available")
        first = self._state_bytes()
        status, payload = self._partner(path, {
            "action": "offer", "slot": "drink", "kind": "warm_water",
            "actor_side": "partner", "position": "hand_side"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(self._state_bytes(), first)
        self.assertEqual(self._owner_fingerprint(), before)

        status, payload = self._call("POST", path, {"action": "take", "slot": "drink"},
                                     csrf=True)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")
        status, payload = self._her(path, {"action": "take", "slot": "drink"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["offer"]["state"], "taken")
        self.assertEqual(self._owner_fingerprint(), before)

        status, payload = self._partner(path, {"action": "withdraw", "slot": "drink"})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "SUBJECT_HAS_TOUCHED")
        status, payload = self._call("GET", "/api/phone/body/care-offering")
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["data"]["changes_needs"])
        self.assertFalse(payload["data"]["changes_relationship"])
        self.assertFalse(payload["data"]["changes_activity"])

    def test_drink_without_real_thirst_consumable_is_named_unsupported(self):
        path = "/api/phone/body/care-offering"
        self.assertEqual(self._partner(path, {
            "action": "offer", "slot": "drink", "kind": "warm_water",
            "actor_side": "partner", "position": "hand_side"})[0], 200)
        self.assertEqual(self._her(path, {"action": "take", "slot": "drink"})[0], 200)
        before = self._owner_fingerprint()
        status, payload = self._her(path, {"action": "drink", "slot": "drink"})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "DRINK_UNSUPPORTED")
        self.assertFalse(payload["data"]["consumed"])
        self.assertEqual(self._owner_fingerprint(), before)
        state = json.loads(self._state_bytes())
        self.assertEqual(state["care"]["drink"]["state"], "taken")

    def test_cushion_offer_use_requires_real_seated_pose(self):
        path = "/api/phone/body/care-offering"
        status, payload = self._partner(path, {
            "action": "offer", "slot": "cushion", "kind": "floor_cushion",
            "actor_side": "partner", "position": "hand_side"})
        self.assertEqual(status, 200, payload)
        # Negative control: a standing body cannot claim floor-cushion support, and the
        # refusal writes nothing.
        before = self._owner_fingerprint()
        status, payload = self._her(path, {"action": "use", "slot": "cushion"})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "POSTURE_NOT_SEATED")
        self.assertEqual(self._owner_fingerprint(), before)

        # Positive control through the real owner chain: ask the World/Body service
        # itself (the existing formal POSE command) to seat the body.  No fake body and
        # no runtime edit.
        seated = self.stack.service.world_action(
            "POSE", {"pose": "seated", "orientation": "north"},
            "op:somatic-cushion-seated")
        self.assertEqual(seated.get("status"), "applied", seated)
        body_status, body_payload = self._call("GET", "/api/phone/body/somatic")
        self.assertEqual(body_status, 200, body_payload)
        self.assertEqual(body_payload["data"]["body"]["pose"], "seated")

        status, payload = self._her(path, {"action": "use", "slot": "cushion"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["offer"]["state"], "in_use")
        status, offering = self._call("GET", path)
        self.assertEqual(status, 200, offering)
        self.assertTrue(offering["data"]["cushion"]["support_active"])
        self.assertIsNone(offering["data"]["cushion"]["support_reason_code"])

        # The real owner changes posture again; the GET re-derives support instead of
        # remembering the earlier claim.
        stood = self.stack.service.world_action(
            "POSE", {"pose": "standing", "orientation": "north"},
            "op:somatic-cushion-standing")
        self.assertEqual(stood.get("status"), "applied", stood)
        status, offering = self._call("GET", path)
        self.assertEqual(status, 200, offering)
        self.assertFalse(offering["data"]["cushion"]["support_active"])
        self.assertEqual(offering["data"]["cushion"]["support_reason_code"],
                         "POSTURE_CHANGED")

        status, payload = self._her(path, {"action": "stow", "slot": "cushion"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["offer"]["state"], "stowed")

    # -- gates / zero write ------------------------------------------------------------
    def test_unknown_or_empty_extra_fields_are_400_and_zero_write(self):
        self.assertEqual(self._partner("/api/phone/body/care-offering", {
            "action": "offer", "slot": "drink", "kind": "warm_water",
            "actor_side": "partner", "position": "hand_side"})[0], 200)
        before = self._owner_fingerprint()
        state_before = self._state_bytes()
        cases = (
            ("/api/phone/body/garment-window",
             {"action": "set_window", "state": "open", "zone": None},
             "UNKNOWN_FIELD:zone"),
            ("/api/phone/body/garment-window",
             {"action": "wear_outerwear", "garment_id": "coat-1", "hang_position": ""},
             "UNKNOWN_FIELD:hang_position"),
            ("/api/phone/body/care-offering",
             {"action": "offer", "slot": "cushion", "kind": "floor_cushion",
              "actor_side": "partner", "position": "hand_side", "label": []},
             "UNKNOWN_FIELD:label"),
            ("/api/phone/body/care-offering",
             {"action": "stow", "slot": "drink", "actor_side": ""},
             "UNKNOWN_FIELD:actor_side"),
            ("/api/phone/body/care-offering",
             {"action": "take", "slot": "drink", "kind": None},
             "UNKNOWN_FIELD:kind"),
        )
        for path, body, reason in cases:
            status, payload = self._call("POST", path, body, csrf=True,
                                         capability=CAP_TOKEN)
            self.assertEqual(status, 400, (path, body, payload))
            self.assertEqual(payload["reason_code"], reason)
        # Required values may not be empty either.
        status, payload = self._her("/api/phone/body/garment-window",
                                    {"action": "set_window", "state": ""})
        self.assertEqual(status, 400, payload)
        status, payload = self._her("/api/phone/body/care-offering",
                                    {"action": "offer", "slot": "drink", "kind": "",
                                     "actor_side": "partner", "position": "hand_side"})
        self.assertEqual(status, 400, payload)
        self.assertEqual(self._owner_fingerprint(), before)
        self.assertEqual(self._state_bytes(), state_before)

    def test_all_gets_are_zero_write_over_local_state_and_owner_files(self):
        self.assertEqual(self._her("/api/phone/body/garment-window",
                                   {"action": "set_window", "state": "open"})[0], 200)
        paths = ("/api/phone/body/somatic", "/api/phone/body/garment-window",
                 "/api/phone/body/care-offering")
        before = self._owner_fingerprint()
        state_before = self._state_bytes()
        first = [self._call("GET", path)[1] for path in paths]
        second = [self._call("GET", path)[1] for path in paths]
        self.assertEqual(first, second)
        self.assertEqual(self._owner_fingerprint(), before)
        self.assertEqual(self._state_bytes(), state_before)

    def test_wrong_capability_never_degrades_and_actor_is_not_identity(self):
        paths = ("/api/phone/body/somatic", "/api/phone/body/garment-window",
                 "/api/phone/body/care-offering")
        for path in paths:
            # No credential: the safe default read stays available.
            status, payload = self._call("GET", path)
            self.assertEqual(status, 200, (path, payload))
            # An explicit wrong credential is refused for both presented forms, never a
            # quiet fallback to the default read -- and X-Phone-Actor is not identity.
            for headers in (
                    {"X-Phone-Capability": "wrong-token", "X-Phone-Actor": "her"},
                    {"Authorization": "Bearer wrong-token",
                     "X-Phone-Actor": "her"}):
                status, payload = self._call("GET", path, headers=headers)
                self.assertEqual(status, 403, (path, headers, payload))
                self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
            # The real serve credential still reads.
            status, payload = self._call("GET", path, capability=CAP_TOKEN)
            self.assertEqual(status, 200, (path, payload))
        # Her writes remain CSRF + capability; a missing credential is not "her".
        path = "/api/phone/body/care-offering"
        status, payload = self._call("POST", path, {"action": "take", "slot": "drink"},
                                     csrf=True, capability="wrong-token",
                                     headers={"X-Phone-Actor": "her"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
        status, payload = self._call("POST", path, {"action": "take", "slot": "drink"},
                                     csrf=True, headers={"X-Phone-Actor": "her"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
