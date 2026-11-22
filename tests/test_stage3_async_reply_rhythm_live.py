#!/usr/bin/env python3
"""Stage 3 Pillar 3 tests: live World/Body/Desk context in 60s autonomy_rhythm heartbeat,
early DEFERRED_HOURS release, zero-write ready deferred reply outbox reader,
idempotent ack_deferred_reply_delivery lifecycle, and bare-dir zero-write invariants.

All tests run 100% offline with llm_calls == 0.
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "scripts"),
    str(REPO_ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import autonomy_rhythm_view as arv  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as v03_eco  # noqa: E402
from plugins import chiyo as chiyo_plugin  # noqa: E402
from scripts import nyairo_day  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        try:
            st = p.lstat()
            snap[rel] = (st.st_size, st.st_mtime_ns)
        except OSError:
            snap[rel] = (-1, -1)
    return snap


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _setup_managed_world_and_body(
    root: Path,
    *,
    place_id: str = "home",
    area_id: str = "bedroom",
    recovery_state: str = "inactive",
    recovery_mode: str | None = None,
) -> tuple[Path, Path]:
    world_home = root / "one-day" / "world-home"
    _write_json(
        root / "phone" / "instance.json",
        {"world_home": str(world_home), "subject": "chiyo"},
    )
    world_state_path = world_home / "data" / "world" / "world_state.json"
    _write_json(
        world_state_path,
        {
            "schema_version": "world.foundation.v0",
            "revision": 3,
            "world_id": "shiomi_city",
            "timezone": "Asia/Shanghai",
            "location": {"place_id": place_id, "area_id": area_id},
            "scene": {"scene_id": f"scene_{place_id}", "revision": 1},
            "facts": [],
        },
    )
    body_runtime_path = world_home / "data" / "body" / "runtime.json"
    _write_json(
        body_runtime_path,
        {
            "schema_version": "body.runtime.v2",
            "kind": "body_runtime",
            "runtime_version": "2.0",
            "body_id": "ta-body",
            "version": 4,
            "pose": "lying" if recovery_mode == "sleep_like" and recovery_state == "active" else "seated",
            "somatic": {
                "fatigue": 0.45,
                "activation": 0.30,
                "comfort": 0.72,
                "sensory_load": 0.15,
                "tension": 0.15,
                "warmth": 0.52,
            },
            "recovery": {
                "state": recovery_state,
                "mode": recovery_mode,
            },
            "signal_latch": {},
        },
    )
    return world_state_path, body_runtime_path


class Stage3AsyncReplyRhythmLiveTest(unittest.TestCase):
    def _tmp_dir(self, prefix: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return tmp

    def test_01_live_snapshots_zero_write_on_empty_and_populated_roots(self) -> None:
        empty_root = self._tmp_dir("s3-snap-empty-")
        before_empty = _snapshot_tree(empty_root)
        snaps_empty = arv.build_autonomy_live_snapshots_zero_write(
            empty_root, now_iso="2026-11-20T09:00:00+08:00"
        )
        self.assertEqual(
            snaps_empty,
            {
                "world_result": None,
                "body_view": None,
                "desk_result": None,
                "somatic_state": None,
            },
        )
        self.assertEqual(_snapshot_tree(empty_root), before_empty)

        pop_root = self._tmp_dir("s3-snap-pop-")
        _setup_managed_world_and_body(
            pop_root,
            place_id="bookstore",
            area_id="bookstore",
            recovery_state="inactive",
            recovery_mode=None,
        )
        _write_json(
            pop_root / "phone" / "somatic_comfort.json",
            {"schema_version": 1, "local_position": {"zone": "desk_side"}},
        )
        _write_json(
            pop_root / "phone" / "desk_presence.json",
            {"door_sign": "AT_HOME_OPEN", "interruptibility": {"value": "OPEN"}},
        )
        before_pop = _snapshot_tree(pop_root)
        snaps_pop = arv.build_autonomy_live_snapshots_zero_write(
            pop_root, now_iso="2026-11-20T14:00:00+08:00"
        )
        self.assertEqual(_snapshot_tree(pop_root), before_pop)
        self.assertIsNotNone(snaps_pop["world_result"])
        self.assertEqual(snaps_pop["world_result"]["data"]["location"]["place_id"], "bookstore")
        self.assertFalse(snaps_pop["world_result"]["data"]["location"]["home"])
        self.assertIsNotNone(snaps_pop["body_view"])
        self.assertEqual(snaps_pop["body_view"]["data"]["recovery"]["state"], "inactive")
        self.assertIsNotNone(snaps_pop["desk_result"])
        self.assertEqual(snaps_pop["desk_result"]["data"]["door_sign"], "AT_HOME_OPEN")
        self.assertIsNotNone(snaps_pop["somatic_state"])

    def test_02_sleep_like_deferral_and_early_release_on_wake_via_heartbeat(self) -> None:
        root = self._tmp_dir("s3-sleep-wake-")
        _, body_path = _setup_managed_world_and_body(
            root,
            place_id="home",
            area_id="bedroom",
            recovery_state="active",
            recovery_mode="sleep_like",
        )
        t_submit = "2026-11-20T01:00:00+08:00"
        ev = arv.apply_autonomy_rhythm_action(
            root,
            {
                "action": "evaluate_turn",
                "user_text": "明早记得帮我看看窗台那盆迷迭香有没有发新芽。",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            subject_view=False,
            now_iso=t_submit,
        )
        self.assertTrue(ev["ok"])
        td = ev["turn_decision"]
        self.assertEqual(td["delivery_mode"], "DEFERRED_HOURS")
        self.assertEqual(td["availability"], "DEFERRED_SLEEPING")
        self.assertEqual(td["defer_reason"], "SLEEPING")
        self.assertEqual(td["deliver_after"], "2026-11-20T05:00:00+08:00")
        msg_id = td["msg_id"]

        stored, reason = arv.read_autonomy_rhythm_state(root)
        self.assertIsNone(reason)
        self.assertEqual(len(stored["pending_messages"]), 1)
        self.assertEqual(stored["pending_messages"][0]["status"], "QUEUED_ON_PHONE")
        self.assertEqual(stored["pending_messages"][0]["defer_reason"], "SLEEPING")

        # While still sleeping at 02:00 (before 05:00 deliver_after): heartbeat MUST NOT promote!
        rhythm_file = arv.autonomy_rhythm_state_path(root)
        rev_before_still_asleep = stored["revision"]
        tick_asleep = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T02:00:00+08:00", force_init=False
        )
        self.assertEqual(tick_asleep["status"], "skipped_uninitialized")
        self.assertEqual(tick_asleep["llm_calls"], 0)
        still_state, _ = arv.read_autonomy_rhythm_state(root)
        self.assertEqual(still_state["revision"], rev_before_still_asleep)
        self.assertEqual(still_state["pending_messages"][0]["status"], "QUEUED_ON_PHONE")

        # Now TA wakes from sleep_like at 02:30 (2.5h BEFORE deliver_after=05:00)!
        body_doc = json.loads(body_path.read_text(encoding="utf-8"))
        body_doc["recovery"] = {"state": "completed", "mode": "sleep_like"}
        body_doc["pose"] = "seated"
        _write_json(body_path, body_doc)

        tick_wake = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T02:30:00+08:00", force_init=False
        )
        self.assertTrue(tick_wake["ok"])
        self.assertEqual(tick_wake["status"], "ticked")
        self.assertEqual(tick_wake["llm_calls"], 0)
        self.assertIn("autonomy_rhythm", tick_wake["slices_ticked"])
        rcpt = tick_wake["autonomy_rhythm_receipt"]
        self.assertEqual(rcpt["promoted_count"], 1)
        self.assertEqual(rcpt["promoted_msg_ids"], [msg_id])
        self.assertEqual(rcpt["early_released_count"], 1)
        self.assertEqual(rcpt["early_released_msg_ids"], [msg_id])
        self.assertEqual(rcpt["availability"], "AVAILABLE_NOW")

        after_state, _ = arv.read_autonomy_rhythm_state(root)
        self.assertEqual(after_state["pending_messages"][0]["status"], "READY_TO_REPLY")
        self.assertEqual(after_state["pending_messages"][0]["ready_at"], "2026-11-20T02:30:00+08:00")
        self.assertEqual(
            after_state["pending_messages"][0]["promoted_reason"],
            "early_live_context_release",
        )

        # Follow-up tick at 02:31 performs zero extra writes on autonomy_rhythm.json
        mtime_after = rhythm_file.stat().st_mtime_ns
        rev_after = after_state["revision"]
        tick_noop = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T02:31:00+08:00", force_init=False
        )
        self.assertEqual(tick_noop["status"], "skipped_uninitialized")
        self.assertEqual(rhythm_file.stat().st_mtime_ns, mtime_after)
        self.assertEqual(arv.read_autonomy_rhythm_state(root)[0]["revision"], rev_after)

    def test_03_returning_home_from_bookstore_and_in_transit_early_release(self) -> None:
        root = self._tmp_dir("s3-bookstore-home-")
        world_path, _ = _setup_managed_world_and_body(
            root,
            place_id="bookstore",
            area_id="bookstore",
            recovery_state="inactive",
            recovery_mode=None,
        )
        ev = arv.apply_autonomy_rhythm_action(
            root,
            {
                "action": "evaluate_turn",
                "user_text": "如果旧书铺有那本装帧小册子顺便看看价钱。",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            now_iso="2026-11-20T14:00:00+08:00",
        )
        self.assertEqual(ev["turn_decision"]["delivery_mode"], "DEFERRED_HOURS")
        self.assertEqual(ev["turn_decision"]["defer_reason"], "OUT_ON_ERRAND")
        self.assertEqual(ev["turn_decision"]["deliver_after"], "2026-11-20T17:00:00+08:00")
        errand_msg_id = ev["turn_decision"]["msg_id"]

        # Still at bookstore at 14:10 -> no premature promotion
        tick_still_out = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T14:10:00+08:00", force_init=False
        )
        self.assertEqual(tick_still_out["status"], "skipped_uninitialized")
        self.assertEqual(
            arv.read_autonomy_rhythm_state(root)[0]["pending_messages"][0]["status"],
            "QUEUED_ON_PHONE",
        )

        # In transit walking back home until 14:25 -> still held as IN_TRANSIT at 14:20!
        travel_path = world_path.parent / "world_travel_delivery.json"
        _write_json(
            travel_path,
            {
                "schema_version": "world.travel_delivery.v1",
                "world_id": "shiomi_city",
                "revision": 1,
                "travels": [
                    {
                        "travel_id": "trv-01",
                        "origin_location_id": "bookstore",
                        "destination_location_id": "home",
                        "started_at": "2026-11-20T14:15:00+08:00",
                        "expected_arrival_at": "2026-11-20T14:25:00+08:00",
                        "status": "IN_PROGRESS",
                    }
                ],
            },
        )
        ev_transit = arv.apply_autonomy_rhythm_action(
            root,
            {
                "action": "evaluate_turn",
                "user_text": "路上风大吗？",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            now_iso="2026-11-20T14:18:00+08:00",
        )
        self.assertEqual(ev_transit["turn_decision"]["defer_reason"], "IN_TRANSIT")
        transit_msg_id = ev_transit["turn_decision"]["msg_id"]

        # Move world_state to home while travel is STILL in progress at 14:20 -> must NOT promote yet
        wdoc = json.loads(world_path.read_text(encoding="utf-8"))
        wdoc["location"] = {"place_id": "home", "area_id": "living"}
        _write_json(world_path, wdoc)
        tick_mid_transit = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T14:20:00+08:00", force_init=False
        )
        self.assertEqual(tick_mid_transit["status"], "skipped_uninitialized")

        # At 14:30 (past expected_arrival_at 14:25, TA is home, 2.5h BEFORE 17:00 deliver_after):
        # BOTH OUT_ON_ERRAND and IN_TRANSIT messages promote immediately!
        tick_home = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T14:30:00+08:00", force_init=False
        )
        self.assertTrue(tick_home["ok"])
        self.assertIn("autonomy_rhythm", tick_home["slices_ticked"])
        rcpt = tick_home["autonomy_rhythm_receipt"]
        self.assertEqual(rcpt["promoted_count"], 2)
        self.assertEqual(set(rcpt["early_released_msg_ids"]), {errand_msg_id, transit_msg_id})
        final_msgs = arv.read_autonomy_rhythm_state(root)[0]["pending_messages"]
        self.assertTrue(all(m["status"] == "READY_TO_REPLY" for m in final_msgs))

    def test_04_phone_aside_override_never_promotes_early_and_live_deep_focus_releases(self) -> None:
        # Part A: set_phone_aside(minutes=30) stays DEFERRED_DEEP_FOCUS even when live world/body/desk say home+open!
        root = self._tmp_dir("s3-aside-compat-")
        _setup_managed_world_and_body(
            root,
            place_id="home",
            area_id="study",
            recovery_state="inactive",
            recovery_mode=None,
        )
        _write_json(
            root / "phone" / "desk_presence.json",
            {"door_sign": "AT_HOME_OPEN", "interruptibility": {"value": "OPEN"}},
        )
        aside = arv.apply_autonomy_rhythm_action(
            root,
            {
                "action": "set_phone_aside",
                "mode": "deep_focus",
                "minutes": 30,
                "note": "先专心把这页草稿写完",
            },
            subject_view=True,
            now_iso="2026-11-20T10:00:00+08:00",
        )
        self.assertTrue(aside["ok"])

        ev = arv.apply_autonomy_rhythm_action(
            root,
            {
                "action": "evaluate_turn",
                "user_text": "写完这页歇会儿喝口温水呀。",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            now_iso="2026-11-20T10:05:00+08:00",
        )
        self.assertEqual(ev["turn_decision"]["delivery_mode"], "DEFERRED_HOURS")
        self.assertEqual(ev["turn_decision"]["defer_reason"], "DEEP_FOCUS")

        # At 10:15 and 10:29 (override active until 10:30): MUST NOT promote early!
        for check_iso in ("2026-11-20T10:15:00+08:00", "2026-11-20T10:29:00+08:00"):
            tick_early = nyairo_day.run_v03_background_clock_tick(
                root, now_iso=check_iso, force_init=False
            )
            self.assertEqual(tick_early["status"], "skipped_uninitialized", check_iso)
            st_check, _ = arv.read_autonomy_rhythm_state(root)
            self.assertEqual(st_check["pending_messages"][0]["status"], "QUEUED_ON_PHONE")
            self.assertEqual(
                arv.read_ready_deferred_replies_zero_write(root, now_iso=check_iso),
                [],
            )

        # At 10:31 (after 30m override elapsed): promotes cleanly!
        tick_after = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T10:31:00+08:00", force_init=False
        )
        self.assertEqual(tick_after["status"], "ticked")
        self.assertIn("autonomy_rhythm", tick_after["slices_ticked"])
        self.assertEqual(
            arv.read_autonomy_rhythm_state(root)[0]["pending_messages"][0]["status"],
            "READY_TO_REPLY",
        )

        # Part B: live canonical_life_state FOCUSED_WORK (no subject_override) -> early release when focus ends
        root_b = self._tmp_dir("s3-desk-focus-")
        _setup_managed_world_and_body(root_b, place_id="home", area_id="study")
        can_path = root_b / "one-day" / "life" / "state" / "canonical_life_state.json"
        _write_json(
            can_path,
            {
                "foreground_activity_ref": "actv-1",
                "activities": {
                    "actv-1": {
                        "status": "ACTIVE",
                        "activity_kind": "CRAFT",
                        "interruptibility": "FOCUSED",
                    }
                },
            },
        )
        ev_b = arv.apply_autonomy_rhythm_action(
            root_b,
            {
                "action": "evaluate_turn",
                "user_text": "晚点一起看看窗外云吗？",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            now_iso="2026-11-20T11:00:00+08:00",
        )
        self.assertEqual(ev_b["turn_decision"]["defer_reason"], "DEEP_FOCUS")
        self.assertEqual(ev_b["turn_decision"]["deliver_after"], "2026-11-20T13:00:00+08:00")

        # Finish foreground activity at 11:20 (100m BEFORE deliver_after=13:00)
        _write_json(
            can_path,
            {
                "foreground_activity_ref": None,
                "activities": {
                    "actv-1": {
                        "status": "COMPLETED",
                        "activity_kind": "CRAFT",
                        "interruptibility": "OPEN",
                    }
                },
            },
        )
        tick_b = nyairo_day.run_v03_background_clock_tick(
            root_b, now_iso="2026-11-20T11:20:00+08:00", force_init=False
        )
        self.assertIn("autonomy_rhythm", tick_b["slices_ticked"])
        self.assertEqual(tick_b["autonomy_rhythm_receipt"]["early_released_count"], 1)
        self.assertEqual(
            arv.read_autonomy_rhythm_state(root_b)[0]["pending_messages"][0]["status"],
            "READY_TO_REPLY",
        )

    def test_05_read_ready_deferred_replies_zero_write_and_idempotent_ack_lifecycle(self) -> None:
        # 1. Empty root -> [] with 0 disk writes
        empty_root = self._tmp_dir("s3-outbox-empty-")
        before_empty = _snapshot_tree(empty_root)
        self.assertEqual(
            arv.read_ready_deferred_replies_zero_write(
                empty_root, now_iso="2026-11-20T09:00:00+08:00"
            ),
            [],
        )
        self.assertEqual(_snapshot_tree(empty_root), before_empty)

        # 2. Populated root with 3 queued messages (sleep_like -> wake)
        root = self._tmp_dir("s3-outbox-ack-")
        _, body_path = _setup_managed_world_and_body(
            root,
            place_id="home",
            area_id="bedroom",
            recovery_state="active",
            recovery_mode="sleep_like",
        )
        msg_ids: list[str] = []
        for idx, text in enumerate(
            (
                "第一条：醒来后记得喝杯温水。",
                "第二条：书铺的新刊到货了。",
                "第三条：晚上要不要煮热汤面？",
            )
        ):
            ev = arv.apply_autonomy_rhythm_action(
                root,
                {
                    "action": "evaluate_turn",
                    "user_text": text,
                    "urgent": False,
                    "session_id": "nyairo-desktop",
                },
                now_iso=f"2026-11-20T01:0{idx}:00+08:00",
            )
            msg_ids.append(ev["turn_decision"]["msg_id"])

        # While still sleeping: read_ready_deferred_replies_zero_write returns [] with 0 writes
        # and claiming a not-ready message fails with 409 DEFERRED_REPLY_NOT_READY
        before_read = _snapshot_tree(root)
        self.assertEqual(
            arv.read_ready_deferred_replies_zero_write(
                root, now_iso="2026-11-20T01:30:00+08:00"
            ),
            [],
        )
        self.assertEqual(_snapshot_tree(root), before_read)
        with self.assertRaises(arv.AutonomyRhythmConflictError) as not_ready_err:
            arv.ack_deferred_reply_delivery(
                root,
                item_id=msg_ids[0],
                action="claim",
                operation_key="op-not-ready",
                now_iso="2026-11-20T01:30:00+08:00",
            )
        self.assertEqual(not_ready_err.exception.reason_code, "DEFERRED_REPLY_NOT_READY")

        # Wake TA up and promote via heartbeat
        bdoc = json.loads(body_path.read_text(encoding="utf-8"))
        bdoc["recovery"] = {"state": "completed", "mode": "sleep_like"}
        _write_json(body_path, bdoc)
        nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T02:00:00+08:00", force_init=False
        )

        # Zero-write read returns all 3 READY_TO_REPLY items cleanly
        before_ready_read = _snapshot_tree(root)
        ready_items = arv.read_ready_deferred_replies_zero_write(
            root, now_iso="2026-11-20T02:01:00+08:00"
        )
        self.assertEqual(_snapshot_tree(root), before_ready_read)
        self.assertEqual(len(ready_items), 3)
        self.assertEqual([item["item_id"] for item in ready_items], sorted(msg_ids))
        first = [item for item in ready_items if item["item_id"] == msg_ids[0]][0]
        self.assertEqual(first["item_kind"], "deferred_reply_ready")
        self.assertEqual(first["channel"], "session_reply")
        self.assertEqual(first["session_id"], "nyairo-desktop")
        self.assertEqual(first["status"], "READY_TO_REPLY")
        self.assertEqual(first["user_full_text"], "第一条：醒来后记得喝杯温水。")
        self.assertNotIn("private_note", first)

        # Item 0: claim -> replay claim -> ack_delivered -> replay ack_delivered -> ack_read
        r_claim = arv.ack_deferred_reply_delivery(
            root,
            item_id=msg_ids[0],
            action="claim",
            operation_key="op-claim-0",
            transport_receipt_id="tx-desk-0",
            now_iso="2026-11-20T02:02:00+08:00",
        )
        self.assertTrue(r_claim["ok"])
        self.assertEqual(r_claim["status"], "CLAIMED_BY_TRANSPORT")
        self.assertFalse(r_claim["idempotent_replay"])
        self.assertEqual(r_claim["llm_calls"], 0)
        rev_after_claim = r_claim["revision"]

        # Idempotent replay on same operation_key -> zero revision bump
        r_claim_dup = arv.ack_deferred_reply_delivery(
            root,
            item_id=msg_ids[0],
            action="claim",
            operation_key="op-claim-0",
            transport_receipt_id="tx-desk-0",
            now_iso="2026-11-20T02:02:05+08:00",
        )
        self.assertTrue(r_claim_dup["replayed"])
        self.assertTrue(r_claim_dup["idempotent_replay"])
        self.assertFalse(r_claim_dup["changed"])
        self.assertEqual(r_claim_dup["status"], "CLAIMED_BY_TRANSPORT")
        self.assertEqual(arv.read_autonomy_rhythm_state(root)[0]["revision"], rev_after_claim)

        # Reusing same operation_key for different action -> 409 OPERATION_KEY_CONFLICT
        with self.assertRaises(arv.AutonomyRhythmConflictError) as conflict_err:
            arv.ack_deferred_reply_delivery(
                root,
                item_id=msg_ids[0],
                action="ack_delivered",
                operation_key="op-claim-0",
                now_iso="2026-11-20T02:02:10+08:00",
            )
        self.assertEqual(conflict_err.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # ack_delivered removes item 0 from pending outbox and sets delivered_at
        r_deliv = arv.ack_deferred_reply_delivery(
            root,
            item_id=msg_ids[0],
            action="ack_delivered",
            operation_key="op-deliv-0",
            transport_receipt_id="tx-desk-0",
            now_iso="2026-11-20T02:03:00+08:00",
        )
        self.assertEqual(r_deliv["status"], "DELIVERED")
        self.assertEqual(r_deliv["delivered_at"], "2026-11-20T02:03:00+08:00")
        self.assertEqual(r_deliv["pending_count"], 2)
        rev_after_deliv = r_deliv["revision"]

        # Replay ack_delivered is idempotent
        r_deliv_dup = arv.ack_deferred_reply_delivery(
            root,
            item_id=msg_ids[0],
            action="ack_delivered",
            operation_key="op-deliv-0",
            now_iso="2026-11-20T02:03:05+08:00",
        )
        self.assertTrue(r_deliv_dup["idempotent_replay"])
        self.assertEqual(r_deliv_dup["status"], "DELIVERED")
        self.assertEqual(arv.read_autonomy_rhythm_state(root)[0]["revision"], rev_after_deliv)

        # Delivered item no longer appears in read_ready_deferred_replies_zero_write
        remaining_ids = [
            it["item_id"]
            for it in arv.read_ready_deferred_replies_zero_write(
                root, now_iso="2026-11-20T02:03:10+08:00"
            )
        ]
        self.assertNotIn(msg_ids[0], remaining_ids)

        # ack_read transitions archived delivered item -> READ cleanly
        r_read = arv.ack_deferred_reply_delivery(
            root,
            item_id=msg_ids[0],
            action="ack_read",
            operation_key="op-read-0",
            now_iso="2026-11-20T02:04:00+08:00",
        )
        self.assertEqual(r_read["status"], "READ")
        self.assertEqual(r_read["read_at"], "2026-11-20T02:04:00+08:00")

        # Item 1 -> ack_failed; Item 2 -> ack_unknown (anti-blind-retry: removed from pending, never DELIVERED)
        r_fail = arv.ack_deferred_reply_delivery(
            root,
            item_id=msg_ids[1],
            action="ack_failed",
            operation_key="op-fail-1",
            now_iso="2026-11-20T02:05:00+08:00",
        )
        self.assertEqual(r_fail["status"], "FAILED")
        self.assertIsNone(r_fail["delivered_at"])

        r_unk = arv.ack_deferred_reply_delivery(
            root,
            item_id=msg_ids[2],
            action="ack_unknown",
            operation_key="op-unk-2",
            now_iso="2026-11-20T02:06:00+08:00",
        )
        self.assertEqual(r_unk["status"], "UNKNOWN")
        self.assertIsNone(r_unk["delivered_at"])
        self.assertEqual(
            arv.read_ready_deferred_replies_zero_write(
                root, now_iso="2026-11-20T02:06:10+08:00"
            ),
            [],
        )

        # Validation & 404 guards
        with self.assertRaises(arv.AutonomyRhythmValidationError):
            arv.ack_deferred_reply_delivery(
                root, item_id=msg_ids[0], action="bogus", operation_key="op-bad-act"
            )
        with self.assertRaises(arv.AutonomyRhythmValidationError):
            arv.ack_deferred_reply_delivery(
                root, item_id=msg_ids[0], action="claim", operation_key=""
            )
        with self.assertRaises(arv.AutonomyRhythmNotFoundError):
            arv.ack_deferred_reply_delivery(
                root, item_id="pmsg-missing-999", action="claim", operation_key="op-miss"
            )

    def test_06_bare_dir_skipped_uninitialized_and_heartbeat_proactive_outreach_hook(self) -> None:
        # 1. Bare unbootstrapped directory -> skipped_uninitialized with 0 disk writes
        bare = self._tmp_dir("s3-bare-uninit-")
        (bare / "run").mkdir(parents=True, exist_ok=True)
        (bare / "phone").mkdir(parents=True, exist_ok=True)
        (bare / "run" / nyairo_day.MANAGED_NAME).write_text(
            json.dumps({"schema": nyairo_day.SCHEMA_MANAGED}), encoding="utf-8"
        )
        before_bare = _snapshot_tree(bare)
        res_bare = nyairo_day.run_v03_background_clock_tick(
            bare, now_iso="2026-11-20T10:00:00+08:00", force_init=False
        )
        self.assertTrue(res_bare["ok"])
        self.assertEqual(res_bare["status"], "skipped_uninitialized")
        self.assertEqual(res_bare["slices_ticked"], [])
        self.assertEqual(res_bare["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(bare), before_bare)

        # 2. Initialized root with a lived vivid morning dream -> heartbeat hooks proactive_outreach_producer
        live_root = self._tmp_dir("s3-hb-outreach-")
        t_boot = "2026-11-20T08:00:00+08:00"
        nyairo_day.bootstrap_living_foundation_state(live_root, now_iso=t_boot)
        spark_path = live_root / "life" / "v05_inner_spark.json"
        spark_doc = json.loads(spark_path.read_text(encoding="utf-8"))
        spark_doc["sleep_wake_state"]["phase"] = "just_woke"
        spark_doc["sleep_wake_state"]["last_dream_roll_date"] = "2026-11-20"
        spark_doc["sleep_wake_state"]["morning_dream"] = {
            "dream_id": "dream-20261120-vivid-1",
            "date_key": "2026-11-20",
            "fades_after_hour": 12,
            "vivid": True,
            "fragment_cn": "梦见海边旧书铺屋檐挂着一串会发微光的铜风铃。",
            "source_echoes": ["old_bookshop"],
        }
        _write_json(spark_path, spark_doc)

        tick_res = nyairo_day.run_v03_background_clock_tick(
            live_root, now_iso="2026-11-20T09:15:00+08:00", force_init=False
        )
        self.assertTrue(tick_res["ok"])
        self.assertEqual(tick_res["llm_calls"], 0)
        self.assertIn("proactive_outreach", tick_res["slices_ticked"])
        out_rcpt = tick_res["proactive_outreach_receipt"]
        self.assertEqual(out_rcpt["enqueued_count"], 1)

        # Verify honest_outbox: APPROVED_FOR_DELIVERY (NEVER fake DELIVERED!)
        pending = v03_eco.read_pending_proactive_outbox_zero_write(
            live_root, now_iso="2026-11-20T09:16:00+08:00"
        )
        self.assertEqual(pending["pending_count"], 1)
        self.assertEqual(pending["items"][0]["status"], "APPROVED_FOR_DELIVERY")

    def test_07_hygiene_no_random_and_frozen_invariants(self) -> None:
        owned = (
            REPO_ROOT / "components" / "phone" / "autonomy_rhythm_view.py",
            REPO_ROOT / "scripts" / "nyairo_day.py",
            Path(__file__).resolve(),
        )
        for fpath in owned:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), str(fpath))
            self.assertNotIn(b"\r", raw, str(fpath))
            text = raw.decode("utf-8")
            so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(so_chars, [], f"unicode.So in {fpath}: {so_chars[:5]}")

        tree = ast.parse(
            (REPO_ROOT / "components" / "phone" / "autonomy_rhythm_view.py").read_text(
                encoding="utf-8"
            )
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "random")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "random")

        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(len(chiyo_plugin._PHONE_AND_WORLD_GUIDANCE), 1199)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertEqual(len(soak_runner.GET_ROUTES), 24)

    def test_08_ack_state_machine_not_ready_and_already_settled_guards(self) -> None:
        # 1. While sleeping (sleep_like) -> ALL 5 ack actions fail with 409 DEFERRED_REPLY_NOT_READY
        sleep_root = self._tmp_dir("s3-ack-not-ready-sleep-")
        _setup_managed_world_and_body(
            sleep_root,
            place_id="home",
            area_id="bedroom",
            recovery_state="active",
            recovery_mode="sleep_like",
        )
        ev_sleep = arv.apply_autonomy_rhythm_action(
            sleep_root,
            {
                "action": "evaluate_turn",
                "user_text": "明早记得帮我顺手收一下阳台上的衣夹。",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            now_iso="2026-11-20T01:00:00+08:00",
        )
        sleep_mid = ev_sleep["turn_decision"]["msg_id"]
        st_sleep_before, _ = arv.read_autonomy_rhythm_state(sleep_root)
        rev_sleep_before = st_sleep_before["revision"]

        for idx, act in enumerate(arv.DEFERRED_REPLY_ACK_ACTIONS):
            with self.assertRaises(arv.AutonomyRhythmConflictError) as caught:
                arv.ack_deferred_reply_delivery(
                    sleep_root,
                    item_id=sleep_mid,
                    action=act,
                    operation_key=f"op-sleep-unready-{idx}",
                    now_iso="2026-11-20T01:20:00+08:00",
                )
            self.assertEqual(caught.exception.reason_code, "DEFERRED_REPLY_NOT_READY", act)
            st_now, _ = arv.read_autonomy_rhythm_state(sleep_root)
            self.assertEqual(len(st_now["pending_messages"]), 1, act)
            self.assertEqual(st_now["pending_messages"][0]["status"], "QUEUED_ON_PHONE", act)
            self.assertEqual(st_now["revision"], rev_sleep_before, act)

        # 2. While set_phone_aside(minutes=120) is active -> ALL 5 ack actions fail with 409 DEFERRED_REPLY_NOT_READY
        aside_root = self._tmp_dir("s3-ack-not-ready-aside-")
        _setup_managed_world_and_body(aside_root, place_id="home", area_id="study")
        arv.apply_autonomy_rhythm_action(
            aside_root,
            {"action": "set_phone_aside", "mode": "aside", "minutes": 120},
            subject_view=True,
            now_iso="2026-11-20T10:00:00+08:00",
        )
        ev_aside = arv.apply_autonomy_rhythm_action(
            aside_root,
            {
                "action": "evaluate_turn",
                "user_text": "手机放一边时别被提前消掉。",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            now_iso="2026-11-20T10:05:00+08:00",
        )
        aside_mid = ev_aside["turn_decision"]["msg_id"]
        rev_aside_before = arv.read_autonomy_rhythm_state(aside_root)[0]["revision"]
        for idx, act in enumerate(arv.DEFERRED_REPLY_ACK_ACTIONS):
            with self.assertRaises(arv.AutonomyRhythmConflictError) as caught:
                arv.ack_deferred_reply_delivery(
                    aside_root,
                    item_id=aside_mid,
                    action=act,
                    operation_key=f"op-aside-unready-{idx}",
                    now_iso="2026-11-20T10:30:00+08:00",
                )
            self.assertEqual(caught.exception.reason_code, "DEFERRED_REPLY_NOT_READY", act)
            st_now, _ = arv.read_autonomy_rhythm_state(aside_root)
            self.assertEqual(len(st_now["pending_messages"]), 1, act)
            self.assertEqual(st_now["revision"], rev_aside_before, act)

        # 3. After DELIVERED -> new operation_key for claim/ack_delivered/ack_failed/ack_unknown raises 409
        settle_root = self._tmp_dir("s3-ack-settled-")
        _, body_path = _setup_managed_world_and_body(
            settle_root,
            place_id="home",
            area_id="bedroom",
            recovery_state="active",
            recovery_mode="sleep_like",
        )
        mids: list[str] = []
        for idx in range(4):
            ev = arv.apply_autonomy_rhythm_action(
                settle_root,
                {
                    "action": "evaluate_turn",
                    "user_text": f"状态机守护测试留言-{idx}",
                    "urgent": False,
                    "session_id": "nyairo-desktop",
                },
                now_iso=f"2026-11-20T01:0{idx}:00+08:00",
            )
            mids.append(ev["turn_decision"]["msg_id"])

        bdoc = json.loads(body_path.read_text(encoding="utf-8"))
        bdoc["recovery"] = {"state": "completed", "mode": "sleep_like"}
        _write_json(body_path, bdoc)
        nyairo_day.run_v03_background_clock_tick(
            settle_root, now_iso="2026-11-20T02:00:00+08:00", force_init=False
        )

        # msg 0: DELIVERED -> duplicate claim/ack_delivered/ack_failed/ack_unknown with NEW keys -> 409, zero rev bump
        r_deliv = arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[0],
            action="ack_delivered",
            operation_key="op-m0-deliv",
            now_iso="2026-11-20T02:01:00+08:00",
        )
        self.assertEqual(r_deliv["status"], "DELIVERED")
        rev_m0 = r_deliv["revision"]
        for bad_act in ("claim", "ack_delivered", "ack_failed", "ack_unknown"):
            with self.assertRaises(arv.AutonomyRhythmConflictError) as caught:
                arv.ack_deferred_reply_delivery(
                    settle_root,
                    item_id=mids[0],
                    action=bad_act,
                    operation_key=f"op-m0-dup-{bad_act}",
                    now_iso="2026-11-20T02:02:00+08:00",
                )
            self.assertEqual(caught.exception.reason_code, "DEFERRED_REPLY_ALREADY_SETTLED", bad_act)
            self.assertEqual(arv.read_autonomy_rhythm_state(settle_root)[0]["revision"], rev_m0)

        # msg 0: DELIVERED -> ack_read succeeds, then new-key ack_read raises 409 ALREADY_SETTLED
        r_m0_read = arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[0],
            action="ack_read",
            operation_key="op-m0-read",
            now_iso="2026-11-20T02:03:00+08:00",
        )
        self.assertEqual(r_m0_read["status"], "READ")
        rev_m0_read = r_m0_read["revision"]
        with self.assertRaises(arv.AutonomyRhythmConflictError) as caught_read_dup:
            arv.ack_deferred_reply_delivery(
                settle_root,
                item_id=mids[0],
                action="ack_read",
                operation_key="op-m0-read-new-key",
                now_iso="2026-11-20T02:04:00+08:00",
            )
        self.assertEqual(caught_read_dup.exception.reason_code, "DEFERRED_REPLY_ALREADY_SETTLED")
        self.assertEqual(arv.read_autonomy_rhythm_state(settle_root)[0]["revision"], rev_m0_read)

        # msg 1: UNKNOWN -> ack_read raises 409, ack_unknown raises 409, reconcile -> ack_delivered succeeds!
        arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[1],
            action="ack_unknown",
            operation_key="op-m1-unk",
            now_iso="2026-11-20T02:05:00+08:00",
        )
        rev_m1_unk = arv.read_autonomy_rhythm_state(settle_root)[0]["revision"]
        with self.assertRaises(arv.AutonomyRhythmConflictError) as unk_read_err:
            arv.ack_deferred_reply_delivery(
                settle_root,
                item_id=mids[1],
                action="ack_read",
                operation_key="op-m1-unk-read",
                now_iso="2026-11-20T02:05:10+08:00",
            )
        self.assertEqual(unk_read_err.exception.reason_code, "DEFERRED_REPLY_NOT_DELIVERED")
        with self.assertRaises(arv.AutonomyRhythmConflictError) as unk_unk_err:
            arv.ack_deferred_reply_delivery(
                settle_root,
                item_id=mids[1],
                action="ack_unknown",
                operation_key="op-m1-unk-dup",
                now_iso="2026-11-20T02:05:20+08:00",
            )
        self.assertEqual(unk_unk_err.exception.reason_code, "DEFERRED_REPLY_ALREADY_SETTLED")
        self.assertEqual(arv.read_autonomy_rhythm_state(settle_root)[0]["revision"], rev_m1_unk)

        r_m1_rec = arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[1],
            action="ack_delivered",
            operation_key="op-m1-reconcile-deliv",
            transport_receipt_id="tx-reconcile-1",
            now_iso="2026-11-20T02:06:00+08:00",
        )
        self.assertEqual(r_m1_rec["status"], "DELIVERED")
        self.assertEqual(r_m1_rec["delivered_at"], "2026-11-20T02:06:00+08:00")

        # msg 2: FAILED -> ack_read raises 409, ack_failed raises 409, reconcile -> ack_delivered succeeds!
        arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[2],
            action="ack_failed",
            operation_key="op-m2-fail",
            now_iso="2026-11-20T02:07:00+08:00",
        )
        rev_m2_fail = arv.read_autonomy_rhythm_state(settle_root)[0]["revision"]
        with self.assertRaises(arv.AutonomyRhythmConflictError) as fail_read_err:
            arv.ack_deferred_reply_delivery(
                settle_root,
                item_id=mids[2],
                action="ack_read",
                operation_key="op-m2-fail-read",
                now_iso="2026-11-20T02:07:10+08:00",
            )
        self.assertEqual(fail_read_err.exception.reason_code, "DEFERRED_REPLY_NOT_DELIVERED")
        with self.assertRaises(arv.AutonomyRhythmConflictError) as fail_dup_err:
            arv.ack_deferred_reply_delivery(
                settle_root,
                item_id=mids[2],
                action="ack_failed",
                operation_key="op-m2-fail-dup",
                now_iso="2026-11-20T02:07:20+08:00",
            )
        self.assertEqual(fail_dup_err.exception.reason_code, "DEFERRED_REPLY_ALREADY_SETTLED")
        self.assertEqual(arv.read_autonomy_rhythm_state(settle_root)[0]["revision"], rev_m2_fail)

        r_m2_rec = arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[2],
            action="ack_delivered",
            operation_key="op-m2-reconcile-deliv",
            now_iso="2026-11-20T02:08:00+08:00",
        )
        self.assertEqual(r_m2_rec["status"], "DELIVERED")

        # msg 3: UNKNOWN -> reconcile -> ack_failed succeeds cleanly!
        arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[3],
            action="ack_unknown",
            operation_key="op-m3-unk",
            now_iso="2026-11-20T02:09:00+08:00",
        )
        r_m3_fail = arv.ack_deferred_reply_delivery(
            settle_root,
            item_id=mids[3],
            action="ack_failed",
            operation_key="op-m3-reconcile-fail",
            transport_receipt_id="tx-reconcile-fail-3",
            now_iso="2026-11-20T02:10:00+08:00",
        )
        self.assertEqual(r_m3_fail["status"], "FAILED")
        self.assertEqual(r_m3_fail["transport_receipt_id"], "tx-reconcile-fail-3")


if __name__ == "__main__":
    unittest.main()
