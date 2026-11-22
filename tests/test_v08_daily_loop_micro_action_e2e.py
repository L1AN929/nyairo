#!/usr/bin/env python3
"""Round C2b: daily-loop micro-action E2E, soft landing, un-wedged foreground, honest regret.

Covers, against the real bridge / universal-matter engine / phone service / lived-context
bridge (0 product-model calls):

* one single-instance runtime entry: subject authorization, a deterministic selector,
  micro-intent execution, a real World receipt, disk persistence, a cold reload, the
  partner ``PhoneService.v08_living_traces()`` projection and the subject's next-tick
  ``build_lived_turn_context`` read -- with the brush standing ``inside`` the ceramic
  vase and the coat ``draped_on`` the chair back;
* a follow-up ``TAKE_OBJECT`` plus a second cold reload, where the coat's
  ``draped_on`` relation and ``casual_drape`` posture are cleanly invalidated;
* the honest failure branch: an unmapped ``v02v03`` clue is refused *before* adoption
  (never falls through into the writing-artifact step), an unsupported micro-intent
  soft-lands with a cooldown + one compact log line, and a missing-spot action
  soft-lands as ``OBJECT_NOT_AT_EXPECTED_SPOT``;
* a soft-landed/refused transient ``V02_V03_DAILY`` foreground Activity is abandoned
  (status no longer ``ACTIVE``, foreground pointer ``None``), the round carries
  ``soft_landed=True`` / its reason code, and zero completion markers exist;
* the post-tick feedback owner records **zero** ``process_mistake`` regret for a soft
  landing while a genuine failure still records it;
* the surface/budget invariants (37 surfaces, guidance <= 1200, 1800-char context cap).

Run (WSL): ``python -B -m unittest tests.test_v08_daily_loop_micro_action_e2e -v``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import autonomous_life_cycle as cycle_mod  # noqa: E402
import v02_agency_depth_engine as ad_engine  # noqa: E402
import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from chiyo_bundle import lived_context_bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.phone.service import PhoneService  # noqa: E402
from components.world.scripts import v08_open_affordance as open_engine  # noqa: E402
from components.world.scripts import v08_universal_matter as engine  # noqa: E402
from plugins import chiyo as plugin_mod  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1H = "2026-11-16T10:00:00+08:00"
T2H = "2026-11-16T11:00:00+08:00"
T5H = "2026-11-16T14:00:00+08:00"
FAKE_SUCCESS_PHRASES = ("我已经摆好了",)


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _objects_by_id(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("object_id")): dict(row)
        for row in (space.get("objects") or [])
        if isinstance(row, dict)
    }


def _item(object_id: str, label: str, kind: str, parent: str, **extra: Any) -> dict[str, Any]:
    step = {
        "op": "INTRODUCE_ITEM",
        "object_id": object_id,
        "label_cn": label,
        "item_kind": kind,
        "target_parent_id": parent,
    }
    step.update(extra)
    return step


class _MicroE2EBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-micro-e2e-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def introduce(self, step: dict[str, Any]) -> dict[str, Any]:
        return engine.compose_open_act(
            self.root,
            {"intent_cn": "放入%s" % step["label_cn"], "steps": [step]},
            now_iso=T0,
        )

    def seed_room(self) -> None:
        # Standard container/object data only: no vase-specific code, no test-only engine hop.
        for step in (
            _item("ceramic_vase", "陶瓷花瓶", "vessel", "dining_table", capacity_slots=3),
            _item("paint_brush", "画笔", "tool", "kitchen_counter"),
            _item("coat", "外套", "everyday", "home_staging_corner"),
            _item("chair", "木椅", "everyday", "home_staging_corner"),
            _item("shared_mug", "白瓷杯", "vessel", "kitchen_counter", capacity_slots=2),
        ):
            out = self.introduce(step)
            self.assertEqual(out.get("status"), "applied", out)
            self.assertEqual(int(out.get("steps_applied") or 0),
                             int(out.get("steps_total") or 0), out)


class SingleInstanceRuntimeE2E(_MicroE2EBase):
    """Section 六 (success path): one instance, zero model calls, real receipts and reloads."""

    def _phone(self, tag: str = "") -> PhoneService:
        del tag
        return PhoneService({
            "root": str(self.root),
            "subject": "chiyo",
            "world_home": str(self.root / "world"),
            "supply_socket": "",
            "private_items_artifact_id": "",
            "artifact_grant_id": "",
            "workspace_id": "",
        })

    def _selector(self) -> list[dict[str, Any]]:
        """Deterministic selector stub: reads believed matter, picks the two seeded acts."""

        state = engine._read_state_zero_write(self.root)
        objects = state.get("matter_objects") or {}
        picks: list[dict[str, Any]] = []
        if "paint_brush" in objects and "ceramic_vase" in objects:
            picks.append({
                "intent_cn": "把画笔插进花瓶",
                "object_id": "paint_brush",
                "target_parent_id": "ceramic_vase",
                "posture_tag": "upright",
                "zone_id": "dining_table",
            })
        if "coat" in objects and "chair" in objects:
            picks.append({
                "intent_cn": "把外套搭在椅背上",
                "object_id": "coat",
                "target_parent_id": "chair",
                "zone_id": "living_room",
            })
        return picks

    def test_01_authorize_select_execute_reload_and_cross_surface_reads(self) -> None:
        self.seed_room()
        self.assertTrue(engine.state_path(self.root).is_file())
        self.assertEqual(
            engine.state_path(self.root).relative_to(self.root).as_posix(),
            "world/v08_matter_space.json",
        )

        picks = self._selector()
        self.assertEqual(len(picks), 2)
        brush_intent, coat_intent = picks

        # 1) Authorize: the subject door is the only one that may execute an open act.
        denied = bridge.execute_normalized_micro_intent(
            self.root, brush_intent, actor_side="partner", now_iso=T0,
            operation_key="e2e-unauthorized",
        )
        self.assertFalse(denied["settled"])
        self.assertTrue(denied["soft_landed"])
        self.assertEqual(denied["reason_code"], "UNAUTHORIZED_CONSEQUENCE_ACTOR")
        self.assertEqual(_objects_by_id(engine.read_matter_space_view(self.root))["paint_brush"]["parent_id"], "kitchen_counter")

        # 2) Select + 3) execute: both chosen intents run through the real engine.
        brush_out = bridge.execute_normalized_micro_intent(
            self.root, brush_intent, now_iso=T0, operation_key="e2e-brush-1")
        self.assertTrue(brush_out["settled"], brush_out)
        self.assertEqual(brush_out["classification"], "direct_supported")
        self.assertEqual(brush_out["llm_calls"], 0)
        self.assertTrue(brush_out["receipt"])

        coat_out = bridge.execute_normalized_micro_intent(
            self.root, coat_intent, now_iso=T0, operation_key="e2e-coat-1")
        self.assertTrue(coat_out["settled"], coat_out)
        self.assertEqual(coat_out["classification"], "direct_supported")
        self.assertEqual(coat_out["llm_calls"], 0)

        # Disk persistence survives a cold reload (no in-process cache).
        state = engine._read_state_zero_write(self.root)
        brush = state["matter_objects"]["paint_brush"]
        coat = state["matter_objects"]["coat"]
        self.assertEqual(brush["parent_id"], "ceramic_vase")
        self.assertEqual(brush["placement_relation"], "inside")
        self.assertEqual(brush["posture_tag"], "upright")
        self.assertEqual(coat["parent_id"], "chair")
        self.assertEqual(coat["placement_relation"], "draped_on")
        self.assertEqual(coat["posture_tag"], "casual_drape")

        # A partner leaves a visible trace on the chair; the subject's next turn reads it.
        open_engine.leave_partner_trace(
            self.root,
            {
                "object_id": "home_desk",
                "gesture_cn": "顺手把垂下来的袖子拨正",
                "state_sticker_cn": "椅背上搭着外套",
                "fade_mode": "until_touched",
            },
            now_iso=T0,
        )

        # Fresh phone instance (cold composition) -> partner projection sees the real space.
        code, body = self._phone().v08_living_traces()
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(body.get("perspective"), "partner")
        data = body.get("data") or {}
        space = data.get("matter_space") or {}
        objects = _objects_by_id(space)
        self.assertEqual(objects["paint_brush"]["parent_id"], "ceramic_vase")
        self.assertEqual(objects["paint_brush"]["placement_relation"], "inside")
        self.assertEqual(objects["paint_brush"]["posture_tag"], "upright")
        self.assertEqual(objects["coat"]["parent_id"], "chair")
        self.assertEqual(objects["coat"]["placement_relation"], "draped_on")
        self.assertEqual(objects["coat"]["posture_tag"], "casual_drape")
        traces = data.get("active_traces") or []
        self.assertTrue(traces, data.get("active_traces"))
        self.assertTrue(
            any("外套" in str(row.get("state_sticker_cn") or "") for row in traces), traces)

        # Subject lived-context next tick: bounded, real, and free of fabricated success.
        context = lived_context_bridge.build_lived_turn_context(
            self.root, None, "", now_iso=T1H)
        self.assertIsInstance(context, str)
        self.assertLessEqual(len(context), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("屋里动态", context)
        self.assertIn("外套", context)
        for phrase in FAKE_SUCCESS_PHRASES:
            self.assertNotIn(phrase, context)

        # Follow-up TAKE_OBJECT + second cold reload: the carrier binding is invalidated.
        take = bridge.execute_normalized_micro_intent(
            self.root,
            {"intent_cn": "把外套拿起来", "object_id": "coat", "zone_id": "living_room"},
            now_iso=T2H,
            operation_key="e2e-coat-take-1",
        )
        self.assertTrue(take["settled"], take)
        self.assertEqual(take["classification"], "direct_supported")

        after = engine._read_state_zero_write(self.root)
        coat_after = after["matter_objects"]["coat"]
        self.assertEqual(coat_after["held_by"], "subject")
        self.assertNotEqual(coat_after.get("placement_relation"), "draped_on")
        self.assertNotEqual(coat_after.get("posture_tag"), "casual_drape")

        code2, body2 = self._phone().v08_living_traces()
        self.assertEqual(code2, 200)
        coat_view = _objects_by_id((body2.get("data") or {}).get("matter_space") or {})["coat"]
        self.assertEqual(coat_view["held_by"], "subject")
        self.assertNotEqual(coat_view["placement_relation"], "draped_on")
        self.assertNotEqual(coat_view["posture_tag"], "casual_drape")
        # The brush was not disturbed by taking the coat.
        brush_view = _objects_by_id((body2.get("data") or {}).get("matter_space") or {})["paint_brush"]
        self.assertEqual(brush_view["parent_id"], "ceramic_vase")
        self.assertEqual(brush_view["placement_relation"], "inside")


class _FakePort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.record = {
            "activity_id": "act-v02v03-1",
            "activity_kind": cycle_mod.V02_V03_ACTIVITY_KIND,
            "status": "ACTIVE",
            "revision": 7,
        }
        self.foreground: str | None = "act-v02v03-1"

    def abandon_activity(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        self.record["status"] = "ABANDONED"
        self.record["revision"] = int(self.record["revision"]) + 1
        self.foreground = None
        return SimpleNamespace(idempotent_replay=False)


class _FakeReader:
    def __init__(self, port: _FakePort) -> None:
        self.port = port

    def get_activity(self, ref: str) -> dict[str, Any]:
        return dict(self.port.record) if str(ref) == self.port.record["activity_id"] else {}


class _FakeRuntime:
    def __init__(self, port: _FakePort) -> None:
        self.port = port
        self.ag2_activity_command_port = port
        self.activity_read = _FakeReader(port)

    def canonical_state(self) -> dict[str, Any]:
        return {"foreground_activity_ref": self.port.foreground}


class _RefusalBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-micro-fail-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cycle_with_candidate(self, source_ref: str,
                              origin_ref: str = "") -> tuple[Any, Any]:
        cycle = object.__new__(cycle_mod.AutonomousLifeCycle)
        candidate = {
            "source_ref": source_ref,
            "origin_source_ref": origin_ref or source_ref,
            "target_refs": [origin_ref or source_ref],
            "candidate_id": "cand-v02v03-1",
        }

        class _CandidateRead:
            def get_candidate(self, ref: str) -> dict[str, Any]:
                return dict(candidate) if str(ref) == "cand-v02v03-1" else {}

        cycle.runtime = SimpleNamespace(candidate_read=_CandidateRead())
        cycle.last_error = ""
        cycle.last_world_error = ""
        cycle.last_v02_v03_collected = {"descriptors": {}, "candidates": []}
        cycle._business_clock = lambda: datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)
        record = SimpleNamespace(selected_candidate_ref="cand-v02v03-1",
                                 decision_id="dec-v02v03-1")
        return cycle, record


class UnmappedClueRefusalTest(_RefusalBase):
    """Section 六 (failure path): an unmapped clue is refused, never adopted as writing work."""

    def test_01_cycle_never_falls_through_to_the_work_step(self) -> None:
        cycle, record = self._cycle_with_candidate(
            "fitem:v02v03-orphan-clue-token", origin_ref="v03:orphan-clue-1")

        def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("an unmapped v02v03 clue reached the writing-artifact path")

        cycle._ar0_step_spec = _forbidden
        cycle._adoption_spec = _forbidden
        cycle._project_step_spec = _forbidden

        with self.assertRaises(cycle_mod.V02V03ClueRefused) as caught:
            cycle._work_step_spec_for_decision(record, fallback=None, observed_at=T0)
        self.assertEqual(caught.exception.reason_code, "UNMAPPED_BRIDGE_CLUE")
        self.assertEqual(caught.exception.selected_ref, "fitem:v02v03-orphan-clue-token")

        with self.assertRaises(cycle_mod.V02V03ClueRefused):
            cycle._work_adoption_spec_for_decision(record, fallback=None)

        routed = cycle._routed_step_builder(None, observed_at=T0)
        with self.assertRaises(cycle_mod.V02V03ClueRefused):
            routed(record)

        # Nothing was written by any of the three refusals.
        self.assertFalse(engine.state_path(self.root).exists())
        self.assertFalse((self.root / "state" / "v02_agency_depth_state.json").exists())

    def test_02_soft_land_round_has_no_completion_and_releases_the_activity(self) -> None:
        cycle, _record = self._cycle_with_candidate(
            "fitem:v02v03-orphan-clue-token", origin_ref="v03:orphan-clue-1")
        port = _FakePort()
        cycle.runtime = _FakeRuntime(port)
        seen: dict[str, Any] = {}
        cycle._finish = lambda base, outcome, **kwargs: (  # type: ignore[assignment]
            seen.update({"outcome": outcome, **kwargs}) or base)

        base: dict[str, Any] = {"reason_codes": [], "activity_ref": "act-v02v03-1",
                                "selected_candidate_ref": "fitem:v02v03-orphan-clue-token"}
        out = cycle._soft_land_v02_v03_round(
            base,
            reason_code="UNMAPPED_BRIDGE_CLUE",
            selected_ref="fitem:v02v03-orphan-clue-token",
            raw_ref="v03:orphan-clue-1",
            detail="unit refusal",
        )

        self.assertIs(out, base)
        self.assertEqual(seen["outcome"], cycle_mod.OUTCOME_STEP_REFUSED)
        self.assertEqual(seen["llm_calls"], 1)
        self.assertTrue(base["soft_landed"])
        self.assertEqual(base["soft_landing"], "defer_and_switch")
        self.assertEqual(base["soft_land_reason_code"], "UNMAPPED_BRIDGE_CLUE")
        self.assertIn("UNMAPPED_BRIDGE_CLUE", base["reason_codes"])
        self.assertTrue(base["v02_v03_daily"]["soft_landed"])
        self.assertEqual(base["v02_v03_daily"]["reason_code"], "UNMAPPED_BRIDGE_CLUE")
        self.assertEqual(base["v02_v03_daily"]["settlement_status"], "SOFT_LANDED")
        self.assertEqual(base["soft_land_release"]["status"], "RELEASED")
        self.assertNotIn("completed_items", base)
        self.assertNotIn("step_receipt", base)
        self.assertNotIn("owner_version_created", base)
        blob = json.dumps(base, ensure_ascii=False)
        for phrase in FAKE_SUCCESS_PHRASES:
            self.assertNotIn(phrase, blob)

        # LR-2 ABANDON was really asked for, with the existing vocabulary.
        self.assertEqual(len(port.calls), 1)
        call = port.calls[0]
        self.assertEqual(call["activity_id"], "act-v02v03-1")
        self.assertEqual(call["expected_revision"], 7)
        self.assertEqual(call["reason_code"], "SPONTANEOUS_RECONSIDERATION")
        self.assertTrue(call["source_refs"])
        self.assertTrue(all(":" in str(ref) for ref in call["source_refs"]))
        self.assertEqual(port.record["status"], "ABANDONED")
        self.assertIsNone(port.foreground)
        # ``status()["current_activity_ref"]`` is this same canonical pointer: it is free.
        self.assertIsNone(cycle._foreground_activity_ref())

    def test_03_release_refuses_a_non_v02v03_or_inactive_activity(self) -> None:
        cycle = object.__new__(cycle_mod.AutonomousLifeCycle)
        port = _FakePort()
        port.record["activity_kind"] = "SOMETHING_ELSE"
        cycle.runtime = _FakeRuntime(port)
        cycle.last_error = ""
        cycle._business_clock = lambda: datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)
        base: dict[str, Any] = {}
        cycle._release_transient_v02_v03_activity(
            base, reason_code="UNMAPPED_BRIDGE_CLUE", raw_ref="v03:orphan-clue-1")
        self.assertEqual(base["soft_land_release"]["status"], "SKIPPED")
        self.assertEqual(base["soft_land_release"]["reason_code"], "NOT_V02_V03_ACTIVITY")
        self.assertEqual(port.calls, [])


class SoftLandingFeedbackTest(_RefusalBase):
    """Section 六: zero fake regret for soft landings, genuine failures preserved."""

    def test_01_soft_landed_round_records_zero_process_mistake(self) -> None:
        before = _snapshot(self.root)
        tick = {
            "tick": "tick-soft-1",
            "logical_at": T0,
            "due": {
                "status": "STEP_REFUSED",
                "step_refused": True,
                "extra": {
                    "step_refused": True,
                    "reason_codes": ["UNMAPPED_BRIDGE_CLUE"],
                    "v02_v03_daily": {
                        "soft_landed": True,
                        "reason_code": "UNMAPPED_BRIDGE_CLUE",
                        "settlement_status": "SOFT_LANDED",
                    },
                },
            },
        }
        out = ad_engine.record_post_tick_execution_feedback(
            self.root, tick_record=tick, now_iso=T1H)
        self.assertTrue(out["ok"])
        self.assertFalse(out["regret_recorded"])
        self.assertFalse(out["satiation_recorded"])
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["soft_landing_reason"], "UNMAPPED_BRIDGE_CLUE")
        self.assertEqual(out["llm_calls"], 0)
        self.assertFalse((self.root / "state" / "v02_agency_depth_state.json").exists())
        self.assertEqual(_snapshot(self.root), before)

    def test_02_unsupported_and_missing_spot_reasons_are_soft(self) -> None:
        for reason in ("UNSUPPORTED_CAPABILITY", "OBJECT_NOT_AT_EXPECTED_SPOT",
                       "COOLDOWN_ACTIVE"):
            tick = {
                "tick": "tick-soft-%s" % reason,
                "logical_at": T0,
                "due": {
                    "status": "STEP_REFUSED",
                    "step_refused": True,
                    "extra": {"step_refused": True, "reason_code": reason},
                },
            }
            out = ad_engine.record_post_tick_execution_feedback(
                self.root, tick_record=tick, now_iso=T1H)
            self.assertFalse(out["regret_recorded"], reason)
            self.assertTrue(out["soft_landed"], reason)
        self.assertFalse((self.root / "state" / "v02_agency_depth_state.json").exists())

    def test_03_genuine_failure_still_records_regret(self) -> None:
        tick = {
            "tick": "tick-hard-1",
            "logical_at": T0,
            "due": {
                "status": "STEP_REFUSED",
                "step_refused": True,
                "action_failed": True,
                "extra": {"step_refused": True, "action_failed": True},
            },
        }
        out = ad_engine.record_post_tick_execution_feedback(
            self.root, tick_record=tick, now_iso=T1H)
        self.assertTrue(out["regret_recorded"])
        self.assertFalse(out["soft_landed"])
        self.assertTrue((self.root / "state" / "v02_agency_depth_state.json").exists())


class BridgeSoftLandingE2E(_MicroE2EBase):
    """Section 六: unsupported cooldown/log and missing-spot refusals through the bridge."""

    def test_01_unsupported_intent_soft_lands_cools_down_and_logs(self) -> None:
        self.seed_room()
        intent = {"intent_cn": "把这张纸折成一只纸鹤", "zone_id": "home"}
        before = _snapshot(self.root / "world")

        out = bridge.execute_normalized_micro_intent(self.root, intent, now_iso=T0)
        self.assertFalse(out["settled"])
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["status"], "SOFT_LANDED")
        self.assertEqual(out["reason_code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(out["llm_calls"], 0)

        # No arrangement/posture change, no world write at all.
        self.assertEqual(_snapshot(self.root / "world"), before)

        log_path = self.root / "notes" / "unsupported_intents.jsonl"
        self.assertTrue(log_path.is_file())
        rows = [json.loads(line) for line in
                log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        for key in ("ts", "intent_key", "category", "summary", "zone_id", "reason_code"):
            self.assertIn(key, rows[0])
        self.assertEqual(rows[0]["reason_code"], "UNSUPPORTED_CAPABILITY")

        # Cooldown suppresses re-proposing on the next tick, and only until expiry.
        self.assertEqual(
            bridge.filter_cooled_down_intents([intent], root=self.root, now_iso=T1H), [])
        self.assertEqual(
            len(bridge.filter_cooled_down_intents([intent], root=self.root, now_iso=T5H)), 1)
        cooldown_doc = json.loads(
            (self.root / "life" / "v02_v03_intent_cooldowns.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cooldown_doc["cooldowns"]), 1)

    def test_02_missing_spot_action_soft_lands_without_writing(self) -> None:
        self.seed_room()
        # The subject last saw the mug on the desk; a partner then moves it unseen.
        engine.partner_move_shared_object(
            self.root,
            {"object_id": "shared_mug", "target_parent_id": "dining_table"},
            now_iso=T0,
        )
        moved_state = engine._read_state_zero_write(self.root)
        self.assertTrue(moved_state["matter_objects"]["shared_mug"]["impression_stale"])

        out = bridge.execute_normalized_micro_intent(
            self.root,
            {"intent_cn": "拿起杯子", "object_id": "shared_mug", "zone_id": "kitchen_counter"},
            now_iso=T1H,
            operation_key="e2e-missing-spot-1",
        )
        self.assertFalse(out["settled"], out)
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["status"], "SOFT_LANDED")
        self.assertEqual(out["reason_code"], "OBJECT_NOT_AT_EXPECTED_SPOT")
        self.assertEqual(out["llm_calls"], 0)
        self.assertNotIn("receipt", out)

        # The failed reach moved nothing: the mug stayed where the partner left it, no object
        # changed posture/arrangement, and no completion or receipt was fabricated.  The only
        # write is the engine's own honest reach-miss record (``not_at_believed_spot``).
        state = engine._read_state_zero_write(self.root)
        mug = state["matter_objects"]["shared_mug"]
        self.assertEqual(mug["parent_id"], "dining_table")
        self.assertFalse(mug.get("held_by"))
        self.assertTrue(mug.get("not_at_believed_spot"))
        for object_id in ("coat", "paint_brush", "chair"):
            self.assertFalse(state["matter_objects"][object_id].get("held_by"), object_id)
        self.assertNotIn("我已经摆好了", json.dumps(out, ensure_ascii=False))

    def test_03_settlement_of_a_soft_event_reports_soft_landing(self) -> None:
        event = {
            "actor_side": "subject",
            "effect_state": "APPLIED",
            "action_kind": "MICRO_INTENT",
            "v04_action": "micro_intent",
            "receipt_ref": "owner-receipt:micro-soft-1",
            "decision_id": "micro-soft-1",
            "micro_intent": {"intent_cn": "用镇纸压住被风吹起的纸", "zone_id": "home"},
        }
        out = bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T0)
        self.assertFalse(out["settled"])
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["reason_code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(out["optimistic_writes"], 0)
        self.assertEqual(out["llm_calls"], 0)


class SurfaceInvariantsTest(unittest.TestCase):
    def test_01_surface_count_is_37(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)

    def test_02_guidance_fits_the_budget(self) -> None:
        self.assertLessEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1200)

    def test_03_lived_context_cap_is_1800(self) -> None:
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)


if __name__ == "__main__":
    unittest.main()
