#!/usr/bin/env python3
"""v0.8 general object affordances, living traces and self-built habit routines.

Coverage:
* six universal verbs on real reachable objects + named refusals (missing object,
  trait mismatch, illness gate, operation-key replay/conflict);
* both trace fade modes (timed natural evaporation through the real clock hook,
  until_touched removal when somebody really touches the object), author mismatch;
* personal routines: save / run all steps / stop honestly at a missing object /
  peaceful retirement;
* the real HTTP surface: zero-write GET (bytes + mtime), private stripping for
  the partner projection, CSRF/capability boundaries, identity refusal;
* the bridge + cycle + resident path: bounded candidates, descriptor resolution,
  settlement on disk, idempotent replay, ``llm_calls == 0``;
* person-neutral copy (no hard-coded pronoun/name) and the 1200-char guidance cap.

Run (WSL): ``python -m unittest tests.test_v08_open_living_traces -v``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_open_affordance as engine  # noqa: E402
from components.phone import v08_living_traces_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _iso_add(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (int(stat.st_size), int(stat.st_mtime_ns), hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _write_containment(root: Path) -> None:
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "phone" / "stance_containment.json").write_text(
        json.dumps({
            "schema_version": 1,
            "revision": 1,
            "containment_domain": {
                "containers": [{
                    "container_id": "pen_cup",
                    "label": "木质笔筒",
                    "place_id": "study_desk",
                }],
                "items": [
                    {"item_id": "fountain_pen", "label": "墨水钢笔", "item_class": "writing_tool", "place_id": "study_desk"},
                    {"item_id": "sketch_pencil", "label": "木杆铅笔", "item_class": "writing_tool", "place_id": "study_desk"},
                ],
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )


class V08EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-open-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- reads ---------------------------------------------------------------
    def test_01_discovery_and_read_are_zero_write(self) -> None:
        empty = Path(tempfile.mkdtemp(prefix="v08-empty-")) / "absent"
        engine.discover_reachable_objects(empty)
        engine.read_living_traces_view(empty, viewer="partner")
        self.assertFalse(empty.exists(), "reads must never create a directory")

        _write_containment(self.root)
        before = _snapshot(self.root)
        objects = engine.discover_reachable_objects(self.root)
        obj_ids = {row["object_id"] for row in objects}
        for required in ("home_window", "home_desk", "home_bed", "home_door_sign",
                         "home_shelf", "courtyard_rack", "indoor_plant"):
            self.assertIn(required, obj_ids)
        self.assertIn("containment_container:pen_cup", obj_ids)
        self.assertIn("containment_item:fountain_pen", obj_ids)
        projected = engine.read_living_traces_view(self.root, viewer="subject")
        self.assertEqual(projected["schema"], "nyairo.v08.living_traces.v1")
        self.assertEqual(projected["llm_calls"], 0)
        self.assertEqual(len(projected["universal_verbs"]), 6)
        for row in projected["universal_verbs"]:
            self.assertNotIn("_", row["label_cn"])
        self.assertEqual(before, _snapshot(self.root), "reads must not touch bytes or mtimes")

    # -- six verbs -----------------------------------------------------------
    def test_02_all_six_universal_verbs_and_replay(self) -> None:
        _write_containment(self.root)

        relocated = engine.execute_universal_verb(
            self.root,
            {"verb": "RELOCATE", "object_id": "home_bed", "target_spot": "窗边", "operation_key": "k-relocate"},
            now_iso=T0,
        )
        self.assertEqual(relocated["status"], "applied")
        replay = engine.execute_universal_verb(
            self.root,
            {"verb": "RELOCATE", "object_id": "home_bed", "target_spot": "窗边", "operation_key": "k-relocate"},
            now_iso=T0,
        )
        self.assertTrue(replay["already_applied"])
        with self.assertRaises(engine.V08OpenAffordanceError) as conflict:
            engine.execute_universal_verb(
                self.root,
                {"verb": "RELOCATE", "object_id": "home_bed", "target_spot": "门口", "operation_key": "k-relocate"},
                now_iso=T0,
            )
        self.assertEqual(conflict.exception.reason_code, "OPERATION_KEY_CONFLICT")

        inserted = engine.execute_universal_verb(
            self.root,
            {
                "verb": "INSERT_EXTRACT",
                "object_id": "containment_item:fountain_pen",
                "container_object_id": "containment_container:pen_cup",
                "mode": "insert",
            },
            now_iso=T0,
        )
        self.assertEqual(inserted["container_object_id"], "containment_container:pen_cup")
        state = engine._read_state_zero_write(self.root)
        self.assertIn("containment_item:fountain_pen", state["object_relations"])
        extracted = engine.execute_universal_verb(
            self.root,
            {
                "verb": "INSERT_EXTRACT",
                "object_id": "containment_item:fountain_pen",
                "container_object_id": "containment_container:pen_cup",
                "mode": "extract",
            },
            now_iso=T0,
        )
        self.assertEqual(extracted["mode"], "extract")
        ids = {row["object_id"] for row in engine.discover_reachable_objects(self.root)}
        self.assertIn("containment_item:fountain_pen", ids, "identity survives insert/extract")
        self.assertIn("containment_container:pen_cup", ids)

        engine.execute_universal_verb(
            self.root,
            {"verb": "INSCRIBE", "object_id": "home_window", "note_text": "今天风小", "visibility": "private"},
            now_iso=T0,
        )
        toggled = engine.execute_universal_verb(
            self.root,
            {"verb": "TOGGLE_ADJUST", "object_id": "home_window", "setting_value": "half_open"},
            now_iso=T0,
        )
        self.assertEqual(toggled["setting_value"], "half_open")
        engine.execute_universal_verb(
            self.root,
            {"verb": "USE_CONSUME", "object_id": "home_bed", "outcome_note": "靠着眯了一会儿"},
            now_iso=T0,
        )
        combined = engine.execute_universal_verb(
            self.root,
            {
                "verb": "COMBINE_SPLIT",
                "object_id": "home_shelf",
                "partner_object_id": "home_desk",
                "mode": "combine",
            },
            now_iso=T0,
        )
        self.assertEqual(combined["mode"], "combine")
        split = engine.execute_universal_verb(
            self.root,
            {
                "verb": "COMBINE_SPLIT",
                "object_id": "home_shelf",
                "partner_object_id": "home_desk",
                "mode": "split",
            },
            now_iso=T0,
        )
        self.assertEqual(split["mode"], "split")

    def test_03_named_refusals(self) -> None:
        with self.assertRaises(engine.V08OpenAffordanceError) as missing:
            engine.execute_universal_verb(
                self.root,
                {"verb": "RELOCATE", "object_id": "ghost_object", "target_spot": "窗边"},
                now_iso=T0,
            )
        self.assertEqual(missing.exception.reason_code, "OBJECT_NOT_FOUND")
        self.assertEqual(missing.exception.status_code, 404)

        with self.assertRaises(engine.V08OpenAffordanceError) as mismatch:
            engine.execute_universal_verb(
                self.root,
                {"verb": "TOGGLE_ADJUST", "object_id": "indoor_plant", "setting_value": "on"},
                now_iso=T0,
            )
        self.assertEqual(mismatch.exception.reason_code, "TRAIT_MISMATCH")
        self.assertEqual(mismatch.exception.status_code, 409)

        with self.assertRaises(engine.V08OpenAffordanceError) as self_insert:
            engine.execute_universal_verb(
                self.root,
                {
                    "verb": "INSERT_EXTRACT",
                    "object_id": "home_shelf",
                    "container_object_id": "home_shelf",
                    "mode": "insert",
                },
                now_iso=T0,
            )
        self.assertEqual(self_insert.exception.reason_code, "TRAIT_MISMATCH")

        (self.root / "phone" / "v03_ecology.json").write_text(
            json.dumps({
                "schema": "v03.ecology.bridge.v1",
                "illness": {"phase": "acute_illness", "severity": 0.6},
            }),
            encoding="utf-8",
        )
        with self.assertRaises(engine.V08OpenAffordanceError) as illness:
            engine.execute_universal_verb(
                self.root,
                {"verb": "RELOCATE", "object_id": "courtyard_rack", "target_spot": "廊下"},
                now_iso=T0,
            )
        self.assertEqual(illness.exception.reason_code, "ILLNESS_GATE_BLOCKED")
        self.assertEqual(illness.exception.status_code, 409)
        with self.assertRaises(engine.V08OpenAffordanceError) as heavy:
            engine.execute_universal_verb(
                self.root,
                {
                    "verb": "RELOCATE",
                    "object_id": "indoor_plant",
                    "target_spot": "院子",
                    "strenuous_outing": True,
                },
                now_iso=T0,
            )
        self.assertEqual(heavy.exception.reason_code, "ILLNESS_GATE_BLOCKED")

    # -- living traces -------------------------------------------------------
    def test_04_timed_trace_evaporates_through_clock_hook(self) -> None:
        import v03_clock_evaporation as clock_evap

        # No state file yet: evaporation is a zero-cost skip and never creates one.
        self.assertEqual(engine.evaporate_expired_living_traces(self.root, T0), 0)
        self.assertFalse(engine.state_path(self.root).exists())

        engine.apply_living_trace(
            self.root,
            {
                "object_id": "home_window",
                "gesture_cn": "擦了擦窗玻璃",
                "state_sticker_cn": "玻璃上擦出巴掌大一块透亮",
                "fade_mode": "timed",
                "ttl_minutes": 30,
            },
            now_iso=T0,
        )
        before = engine.read_living_traces_view(self.root, now_iso=T0)
        self.assertEqual(len(before["active_traces"]), 1)
        self.assertEqual(before["active_traces"][0]["remaining_minutes"], 30)

        code, envelope = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso=_iso_add(31),
        )
        self.assertEqual(code, 200, envelope)
        self.assertEqual(envelope["data"]["action_receipt"]["evaporated_living_traces"], 1)
        after = engine.read_living_traces_view(self.root, now_iso=_iso_add(31))
        self.assertEqual(after["active_traces"], [], "a timed trace leaves no tombstone")
        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["traces"], [])

    def test_05_until_touched_fades_on_a_real_touch_and_author_mismatch(self) -> None:
        partner_trace = engine.leave_partner_trace(
            self.root,
            {
                "object_id": "home_window",
                "gesture_cn": "把窗推开一条缝",
                "state_sticker_cn": "窗缝边压着一小片风",
                "fade_mode": "until_touched",
            },
            now_iso=T0,
        )
        engine.execute_universal_verb(
            self.root,
            {"verb": "TOGGLE_ADJUST", "object_id": "home_window", "setting_value": "closed"},
            now_iso=T0,
        )
        after = engine.read_living_traces_view(self.root, viewer="partner", now_iso=T0)
        self.assertEqual(after["active_traces"], [])
        self.assertTrue(partner_trace["trace_id"])

        subject_trace = engine.apply_living_trace(
            self.root,
            {
                "object_id": "home_desk",
                "gesture_cn": "把纸片摆正",
                "state_sticker_cn": "纸片一角对齐桌沿",
            },
            now_iso=T0,
        )
        with self.assertRaises(engine.V08OpenAffordanceError) as mismatch:
            engine.clear_partner_trace(self.root, {"trace_id": subject_trace["trace_id"]}, now_iso=T0)
        self.assertEqual(mismatch.exception.reason_code, "AUTHOR_MISMATCH")
        self.assertEqual(mismatch.exception.status_code, 409)
        with self.assertRaises(engine.V08OpenAffordanceError) as missing:
            engine.clear_partner_trace(self.root, {"trace_id": "trace_ghost"}, now_iso=T0)
        self.assertEqual(missing.exception.reason_code, "TRACE_NOT_FOUND")

        own = engine.leave_partner_trace(
            self.root,
            {
                "object_id": "home_desk",
                "gesture_cn": "放下杯垫",
                "state_sticker_cn": "杯垫压着一张小票",
            },
            now_iso=T0,
        )
        cleared = engine.clear_partner_trace(self.root, {"trace_id": own["trace_id"]}, now_iso=T0)
        self.assertEqual(cleared["cleared_trace_id"], own["trace_id"])

    def test_06_partner_projection_strips_private_rows(self) -> None:
        engine.apply_living_trace(
            self.root,
            {
                "object_id": "home_window",
                "gesture_cn": "悄悄擦了擦",
                "state_sticker_cn": "玻璃上有一小块亮",
                "visibility": "private",
            },
            now_iso=T0,
        )
        engine.execute_universal_verb(
            self.root,
            {"verb": "INSCRIBE", "object_id": "home_desk", "note_text": "只给自己看", "visibility": "private"},
            now_iso=T0,
        )
        engine.save_personal_routine(
            self.root,
            {
                "title_cn": "只给自己的小套路",
                "when_cue": "any",
                "visibility": "private",
                "steps": [
                    {"kind": "verb", "object_id": "home_window", "verb": "TOGGLE_ADJUST", "setting_value": "a"},
                    {"kind": "trace", "object_id": "home_desk", "gesture_cn": "摆正", "state_sticker_cn": "对齐"},
                ],
            },
            now_iso=T0,
        )
        subject_view = engine.read_living_traces_view(self.root, viewer="subject", now_iso=T0)
        partner_view = engine.read_living_traces_view(self.root, viewer="partner", now_iso=T0)
        self.assertEqual(len(subject_view["active_traces"]), 1)
        self.assertEqual(subject_view["inscriptions_by_object"].get("home_desk")[0]["visibility"], "private")
        self.assertEqual(len(subject_view["routines"]), 1)
        self.assertEqual(partner_view["active_traces"], [])
        self.assertEqual(partner_view["inscriptions_by_object"], {})
        self.assertEqual(partner_view["routines"], [])
        blob = json.dumps(partner_view, ensure_ascii=False)
        self.assertNotIn("只给自己看", blob)
        self.assertNotIn("hidden_count", blob)
        self.assertNotIn("private_", blob)

    def test_07_trace_caps_replace_oldest_peacefully(self) -> None:
        ids = []
        for idx in range(3):
            trace = engine.leave_partner_trace(
                self.root,
                {
                    "object_id": "home_desk",
                    "gesture_cn": "摆了摆第%d次" % idx,
                    "state_sticker_cn": "桌上有一点变化%d" % idx,
                },
                now_iso=T0,
            )
            ids.append(trace["trace_id"])
        active = engine.read_living_traces_view(self.root, now_iso=T0)["active_traces"]
        self.assertEqual(len(active), engine.MAX_TRACES_PER_OBJECT_AUTHOR)
        self.assertNotIn(ids[0], [row["trace_id"] for row in active])

    # -- routines ------------------------------------------------------------
    def test_08_routine_save_run_partial_stop_and_retire(self) -> None:
        _write_containment(self.root)
        routine = engine.save_personal_routine(
            self.root,
            {
                "title_cn": "雨天窗边小整理",
                "when_cue": "rainy",
                "steps": [
                    {"kind": "verb", "object_id": "home_window", "verb": "TOGGLE_ADJUST", "setting_value": "ajar"},
                    {"kind": "verb", "object_id": "containment_item:fountain_pen", "verb": "RELOCATE", "target_spot": "笔搁边"},
                    {"kind": "trace", "object_id": "home_desk", "gesture_cn": "把纸片摆正", "state_sticker_cn": "纸片对齐桌沿"},
                ],
            },
            now_iso=T0,
        )
        self.assertEqual(routine["step_count"], 3)
        ran = engine.run_personal_routine(self.root, {"routine_id": routine["routine_id"]}, now_iso=T0)
        self.assertEqual(ran["status"], "applied")
        self.assertEqual(ran["executed_count"], 3)
        self.assertEqual(ran["times_used"], 1)
        self.assertEqual(len(engine.read_living_traces_view(self.root, now_iso=T0)["routines"]), 1)

        # The pen is no longer reachable: the run stops at the real断点, no fake completion.
        (self.root / "phone" / "stance_containment.json").unlink()
        stopped = engine.run_personal_routine(
            self.root, {"routine_id": routine["routine_id"], "operation_key": "run-2"}, now_iso=_iso_add(1)
        )
        self.assertEqual(stopped["status"], "partial")
        self.assertEqual(stopped["executed_count"], 1)
        self.assertEqual(stopped["blocked_step"]["step_index"], 1)
        self.assertEqual(stopped["blocked_step"]["reason_code"], "OBJECT_NOT_FOUND")
        self.assertEqual(stopped["times_used"], 2)

        for idx in range(engine.MAX_ROUTINES - 1):
            engine.save_personal_routine(
                self.root,
                {
                    "title_cn": "第%d套顺手小套路" % idx,
                    "when_cue": "any",
                    "steps": [
                        {"kind": "verb", "object_id": "home_window", "verb": "TOGGLE_ADJUST", "setting_value": "a"},
                        {"kind": "trace", "object_id": "home_desk", "gesture_cn": "碰一碰", "state_sticker_cn": "有一点变化"},
                    ],
                },
                now_iso=T0,
            )
        with self.assertRaises(engine.V08OpenAffordanceError) as limit:
            engine.save_personal_routine(
                self.root,
                {
                    "title_cn": "第九套小套路",
                    "when_cue": "any",
                    "steps": [
                        {"kind": "verb", "object_id": "home_window", "verb": "TOGGLE_ADJUST", "setting_value": "a"},
                        {"kind": "trace", "object_id": "home_desk", "gesture_cn": "碰一碰", "state_sticker_cn": "有一点变化"},
                    ],
                },
                now_iso=T0,
            )
        self.assertEqual(limit.exception.reason_code, "ROUTINE_LIMIT_REACHED")

        retired = engine.retire_personal_routine(
            self.root, {"routine_id": routine["routine_id"]}, now_iso=_iso_add(2)
        )
        self.assertEqual(retired["status"], "applied")
        remaining = engine.read_living_traces_view(self.root, now_iso=_iso_add(2))["routines"]
        self.assertEqual(len(remaining), engine.MAX_ROUTINES - 1)


class V08WiringTest(unittest.TestCase):
    def test_01_hermes_guidance_and_phone_tool_registration(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "chiyo_plugin_init_v08", ROOT / "plugins" / "chiyo" / "__init__.py"
        )
        assert spec is not None and spec.loader is not None
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        self.assertLessEqual(len(plugin._PHONE_AND_WORLD_GUIDANCE), 1200)
        self.assertIn("v08_living_traces", plugin._PHONE_AND_WORLD_GUIDANCE)

        import chiyo_bundle.phone_tool as phone_tool

        for name in ("v08_living_traces", "living_traces"):
            self.assertEqual(phone_tool.READ_ACTIONS[name], "/api/phone/v08/living-traces")
            self.assertIn(name, phone_tool.ACTIONS)
            self.assertIn(name, phone_tool._SUBJECT_VIEW_READ_ACTIONS)
            self.assertIn(name, phone_tool.PRIVATE_ARTIFACT_ACTIONS)
            self.assertIn(name, phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)
        self.assertEqual(
            phone_tool.LIFE_UPDATE_SURFACES["v08_living_traces"],
            "/api/phone/v08/living-traces",
        )
        self.assertIn(
            "v08_living_traces",
            phone_tool.PHONE_TOOL_SCHEMA["parameters"]["properties"]["target_surface"]["enum"],
        )
        self.assertIn(
            "v08_living_traces",
            phone_tool.PHONE_TOOL_SCHEMA["parameters"]["properties"]["target_surface"]["description"],
        )

        with self.assertRaises(phone_tool.PhoneToolRefused) as impersonation:
            phone_tool._prepare_life_update(
                {
                    "target_surface": "v08_living_traces",
                    "payload": {
                        "action": "leave_partner_trace",
                        "object_id": "home_window",
                        "gesture_cn": "推开窗",
                        "state_sticker_cn": "窗缝有风",
                    },
                },
                "op-v08-partner",
            )
        self.assertEqual(impersonation.exception.reason_code, "PARTNER_IMPERSONATION_REFUSED")

        surface, path, body, cap_required = phone_tool._prepare_life_update(
            {
                "target_surface": "v08_living_traces",
                "payload": {
                    "action": "apply_living_trace",
                    "object_id": "home_window",
                    "gesture_cn": "擦了擦窗",
                    "state_sticker_cn": "玻璃上有一小块亮",
                },
            },
            "op-v08-subject",
        )
        self.assertEqual(surface, "v08_living_traces")
        self.assertEqual(path, "/api/phone/v08/living-traces")
        self.assertEqual(body["operation_key"], "op-v08-subject")
        self.assertTrue(cap_required)

    def test_02_person_neutral_copy_and_frozen_anchors(self) -> None:
        forbidden = ("她", "千代")
        for rel in (
            "components/world/scripts/v08_open_affordance.py",
            "components/phone/v08_living_traces_view.py",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{rel} must stay person-neutral")

        anchors = {
            "components/life/scripts/autonomous_life_assembly.py":
                "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
            "components/life/scripts/life_sharing_service.py":
                "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
            "components/native/native_recall/readonly_recall_adapter.py":
                "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a",
        }
        for rel, expected in anchors.items():
            actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"Frozen anchor mutated: {rel}")

    def test_03_bounded_bridge_candidates_and_settlement(self) -> None:
        import v02_v03_daily_loop_bridge as bridge

        tmp = tempfile.TemporaryDirectory(prefix="v08-bridge-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()
        _write_containment(root)

        partner_trace = engine.leave_partner_trace(
            root,
            {
                "object_id": "home_desk",
                "gesture_cn": "放下杯垫",
                "state_sticker_cn": "杯垫压着一张小票",
                "fade_mode": "until_touched",
            },
            now_iso=T0,
        )
        routine = engine.save_personal_routine(
            root,
            {
                "title_cn": "傍晚桌边收一收",
                "when_cue": "any",
                "steps": [
                    {"kind": "verb", "object_id": "home_window", "verb": "TOGGLE_ADJUST", "setting_value": "ajar"},
                    {"kind": "trace", "object_id": "home_shelf", "gesture_cn": "把笔搁摆正", "state_sticker_cn": "笔按长短排好"},
                ],
            },
            now_iso=T0,
        )

        collected = bridge.collect_v02_v03_loop_candidates(root, now_iso=T0)
        v08_descs = [
            desc for desc in collected["descriptors"].values()
            if str(desc.get("source_ref") or "").startswith(("v08_routine:", "v08_open_act:"))
        ]
        self.assertLessEqual(len(v08_descs), 2, "at most two v0.8 candidates per round")
        refs = {desc["source_ref"] for desc in v08_descs}
        self.assertIn("v08_routine:%s" % routine["routine_id"], refs)
        open_refs = [ref for ref in refs if ref.startswith("v08_open_act:")]
        self.assertEqual(len(open_refs), 1)
        self.assertIn(partner_trace["object_id"], open_refs[0])
        self.assertIn("respond_partner_trace", open_refs[0])

        for raw_ref in sorted(refs):
            event = bridge.v02_v03_round_event_for_selected_candidate(
                collected, raw_ref, now_iso=T0, decision_id="dec-v08-1"
            )
            self.assertIsInstance(event, dict, raw_ref)
            self.assertEqual(event["actor_side"], "life_resident")
            settled = bridge.settle_v02_v03_round_consequences(root, event, now_iso=T0)
            self.assertTrue(settled.get("settled") or settled.get("already_applied"), settled)
            self.assertEqual(settled["llm_calls"], 0)
            self.assertIn("v08_living_traces", settled.get("slices_updated") or [])

        # Replay of the same Decision is an idempotent ledger replay, not a second write.
        replay_ref = "v08_open_act:%s:respond_partner_trace" % partner_trace["object_id"]
        replay_event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, replay_ref, now_iso=T0, decision_id="dec-v08-1"
        )
        replay = bridge.settle_v02_v03_round_consequences(root, replay_event, now_iso=T0)
        self.assertTrue(replay.get("already_applied"), replay)

        # The real Cycle resolves the same descriptor and agrees the candidate is executable.
        candidate_payload = {
            "candidate_id": "cand:v08-test",
            "source_ref": "fitem:v02v03-%s" % engine._safe_token(replay_ref).replace("_", "-"),
            "origin_source_ref": replay_ref,
            "target_refs": [replay_ref],
        }
        cycle_stub = SimpleNamespace(
            _selected_candidate=lambda _record: candidate_payload,
            _v02_v03_bridge_collected=lambda: collected,
        )
        import autonomous_life_cycle as alc

        desc = alc.AutonomousLifeCycle._v02_v03_descriptor_for_decision(cycle_stub, object())
        self.assertIsInstance(desc, dict, "the Cycle must resolve the v0.8 descriptor")
        self.assertEqual(desc.get("source_ref"), replay_ref)
        self.assertTrue(alc.AutonomousLifeCycle._v02_v03_candidates_available(cycle_stub))

    def test_04_resident_hook_settles_selected_v08_candidate(self) -> None:
        import autonomous_life_resident as resident_mod
        import v02_v03_daily_loop_bridge as bridge

        tmp = tempfile.TemporaryDirectory(prefix="v08-resident-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()
        _write_containment(root)

        routine = engine.save_personal_routine(
            root,
            {
                "title_cn": "傍晚桌边收一收",
                "when_cue": "any",
                "steps": [
                    {"kind": "verb", "object_id": "home_window", "verb": "TOGGLE_ADJUST", "setting_value": "ajar"},
                    {"kind": "trace", "object_id": "home_shelf", "gesture_cn": "把笔搁摆正", "state_sticker_cn": "笔按长短排好"},
                ],
            },
            now_iso=T0,
        )
        collected = bridge.collect_v02_v03_loop_candidates(root, now_iso=T0)
        raw_ref = "v08_routine:%s" % routine["routine_id"]
        mapped_ref = "fitem:v02v03-%s" % engine._safe_token(raw_ref).replace("_", "-")

        class _Reader:
            def get_candidate(self, ref: str) -> dict[str, Any]:
                return {
                    "candidate_id": "cand:v08-resident",
                    "source_ref": mapped_ref,
                    "origin_source_ref": raw_ref,
                    "target_refs": [raw_ref],
                }

        class _Cycle:
            runtime = SimpleNamespace(candidate_read=_Reader())

        class _ResidentStub:
            enable_v02_v03_bridge = True
            assembled = {"cycle": _Cycle()}

            def _resolve_v02_v03_day_root(self) -> Path:
                return root

            def _resolve_practice_service_for_feedback(self) -> None:
                return None

        record = {
            "logical_at": T0,
            "due": {
                "decision": "START",
                "status": "ACTED",
                "selected_candidate_ref": mapped_ref,
                "extra": {"selected_candidate_ref": mapped_ref, "decision_id": "dec-v08-r1"},
            },
        }
        out = resident_mod.AutonomousLifeResident._run_v02_v03_bridge_hook(
            _ResidentStub(), record, None, pre_collected=collected
        )
        self.assertIsInstance(out, dict)
        self.assertNotIn("error", out, out)
        settlements = out.get("settlements") or []
        self.assertTrue(settlements, out)
        self.assertTrue(any(row.get("status") == "APPLIED" for row in settlements), settlements)
        state = engine._read_state_zero_write(root)
        self.assertEqual(state["routines"][0]["times_used"], 1)
        self.assertEqual(state["routines"][0]["last_used_at"], T0)


POSIX_ONLY = unittest.skipUnless(
    hasattr(os, "getuid") and os.name == "posix",
    "Requires a POSIX loopback server (run inside WSL)",
)


@POSIX_ONLY
class V08HTTPIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture
        import test_v02_phone_http_surfaces as v02http
        from components.phone import capability as phone_capability

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = "chiyo"
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v08-http-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=v02http.CAP_TOKEN,
            subject="chiyo",
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-16T12:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.server_root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base
        cls.CAP = v02http.CAP_TOKEN

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        import shutil
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        (self.stack.root / "phone" / "v08_living_traces.json").unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 16, 14, 30, tzinfo=timezone(timedelta(hours=8))
        )

    def _http(self, method: str, path: str, body: dict[str, Any] | None = None, *,
              csrf: bool = False, capability: str | None = None) -> tuple[int, dict[str, Any]]:
        import urllib.error
        import urllib.request

        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=raw, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if raw is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_01_get_zero_write_guards_and_partner_projection(self) -> None:
        path = "/api/phone/v08/living-traces"
        before = _snapshot(Path(self.stack.root))
        code_p, res_p = self._http("GET", path)
        self.assertEqual(code_p, 200, res_p)
        self.assertTrue(res_p.get("ok"))
        data = res_p.get("data") or {}
        self.assertEqual(data.get("schema"), "nyairo.v08.living_traces.v1")
        self.assertEqual(data.get("perspective"), "partner")
        self.assertEqual(data.get("llm_calls"), 0)
        self.assertEqual(len(data.get("universal_verbs") or []), 6)
        self.assertTrue(data.get("reachable_objects"))

        code_s, res_s = self._http("GET", path, capability=self.CAP)
        self.assertEqual(code_s, 200, res_s)
        self.assertEqual((res_s.get("data") or {}).get("perspective"), "subject")

        code_q, res_q = self._http("GET", path + "?unexpected=1")
        self.assertEqual(code_q, 400)
        self.assertEqual(res_q.get("reason_code"), "UNKNOWN_QUERY")
        code_bad, _ = self._http("GET", path, capability="forged-capability-token")
        self.assertEqual(code_bad, 403)
        after = _snapshot(Path(self.stack.root))
        self.assertEqual(before, after, "GET must be 100% zero-write (bytes and mtimes)")

    def test_02_post_permissions_identity_and_privacy(self) -> None:
        path = "/api/phone/v08/living-traces"

        self.assertEqual(self._http("POST", path, {"action": "leave_partner_trace"})[0], 403)
        self.assertEqual(
            self._http("POST", path + "?x=1", {"action": "leave_partner_trace"}, csrf=True)[0], 400
        )
        self.assertEqual(self._http("POST", path, {}, csrf=True)[0], 400)
        c_uid, res_uid = self._http(
            "POST", path,
            {"action": "leave_partner_trace", "object_id": "home_desk",
             "gesture_cn": "放下杯垫", "state_sticker_cn": "杯垫压着小票", "uid": "root"},
            csrf=True,
        )
        self.assertEqual(c_uid, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", str(res_uid.get("reason_code") or ""))
        c_author, res_author = self._http(
            "POST", path,
            {"action": "leave_partner_trace", "object_id": "home_desk",
             "gesture_cn": "放下杯垫", "state_sticker_cn": "杯垫压着小票", "author_side": "subject"},
            csrf=True,
        )
        self.assertEqual(c_author, 409)
        self.assertEqual(res_author.get("reason_code"), "CLIENT_IDENTITY_REFUSED")

        partner = {
            "action": "leave_partner_trace",
            "object_id": "home_door_sign",
            "gesture_cn": "把门口木牌转了个向",
            "state_sticker_cn": "木牌背面朝外，像随手翻过",
            "fade_mode": "until_touched",
        }
        c_partner, res_partner = self._http("POST", path, partner, csrf=True)
        self.assertEqual(c_partner, 200, res_partner)
        self.assertEqual((res_partner.get("data") or {}).get("visibility"), "shared")
        partner_trace_id = (res_partner.get("data") or {})["trace_id"]

        # Partner-only door does not open subject verbs.
        c_forbid, res_forbid = self._http(
            "POST", path,
            {"action": "execute_universal_verb", "object_id": "home_window",
             "verb": "TOGGLE_ADJUST", "setting_value": "half"},
            csrf=True,
        )
        self.assertEqual(c_forbid, 403, res_forbid)
        self.assertEqual(res_forbid.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

        c_ghost, res_ghost = self._http(
            "POST", path,
            {"action": "execute_universal_verb", "object_id": "ghost_object",
             "verb": "RELOCATE", "target_spot": "窗边"},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_ghost, 404, res_ghost)
        self.assertEqual(res_ghost.get("reason_code"), "OBJECT_NOT_FOUND")

        # Subject writes a private trace and a private inscription.
        c_priv, res_priv = self._http(
            "POST", path,
            {"action": "apply_living_trace", "object_id": "home_window",
             "gesture_cn": "悄悄擦了擦", "state_sticker_cn": "玻璃上有一小块亮",
             "visibility": "private"},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_priv, 200, res_priv)
        c_ins, res_ins = self._http(
            "POST", path,
            {"action": "execute_universal_verb", "object_id": "home_desk",
             "verb": "INSCRIBE", "note_text": "只给自己看", "visibility": "private"},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_ins, 200, res_ins)

        _, partner_view = self._http("GET", path)
        _, subject_view = self._http("GET", path, capability=self.CAP)
        partner_data = partner_view.get("data") or {}
        subject_data = subject_view.get("data") or {}
        self.assertEqual(len(partner_data.get("active_traces") or []), 1)
        self.assertEqual(len(subject_data.get("active_traces") or []), 2)
        self.assertEqual(partner_data.get("inscriptions_by_object"), {})
        self.assertIn("home_desk", subject_data.get("inscriptions_by_object") or {})
        self.assertNotIn("只给自己看", json.dumps(partner_view, ensure_ascii=False))

        # Partner may not clear the subject's sticker.
        subject_trace_id = next(
            row["trace_id"] for row in subject_data["active_traces"]
            if row.get("author_side") == "subject"
        )
        c_mismatch, res_mismatch = self._http(
            "POST", path, {"action": "clear_partner_trace", "trace_id": subject_trace_id}, csrf=True
        )
        self.assertEqual(c_mismatch, 409, res_mismatch)
        self.assertEqual(res_mismatch.get("reason_code"), "AUTHOR_MISMATCH")

        # Subject really touching the object fades the partner's until_touched sticker.
        c_touch, res_touch = self._http(
            "POST", path,
            {"action": "execute_universal_verb", "object_id": "home_door_sign",
             "verb": "TOGGLE_ADJUST", "setting_value": "turned_back"},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_touch, 200, res_touch)
        self.assertIn(partner_trace_id, res_touch.get("touched_cleared_traces") or [])
        self.assertFalse(
            any(
                row["trace_id"] == partner_trace_id
                for row in ((self._http("GET", path)[1].get("data") or {}).get("active_traces") or [])
            ),
            "an until_touched trace fades on a real touch",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
