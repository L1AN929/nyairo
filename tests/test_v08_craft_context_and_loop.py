#!/usr/bin/env python3
"""Worker 7C: the four craft/conservation primitives reach hints, lived context
and the daily loop without breaking any existing contract.

The suite is deliberately resilient: Worker 7A lands ``SPLIT_MATERIAL`` /
``ASSEMBLE_OR_DETACH_PART`` / ``TRANSCRIBE_TO_PAPER`` / ``PATCH_OR_REPAIR`` in
``v08_universal_matter`` concurrently, so engine-level assertions skip cleanly
until the primitives exist, while the phone-hint, lived-context and
daily-loop-pass-through invariants always run.

Run (WSL): ``python -B -m unittest tests.test_v08_craft_context_and_loop -v``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402

try:  # Worker 7A may or may not have landed yet; never fail collection on this.
    from components.world.scripts import v08_universal_matter as engine  # noqa: E402
except Exception:  # noqa: BLE001
    engine = None  # type: ignore[assignment]

T0 = "2026-11-16T09:00:00+08:00"
CRAFT_OPS = (
    "SPLIT_MATERIAL",
    "ASSEMBLE_OR_DETACH_PART",
    "TRANSCRIBE_TO_PAPER",
    "PATCH_OR_REPAIR",
)
BASE_OPS = (
    "PLACE_OR_MOVE",
    "INTRODUCE_ITEM",
    "TRANSFORM_CREATE",
    "CONSUME_PORTION",
    "CLEAN_SURFACE_OR_VESSEL",
    "DISCARD_ITEM",
    "OBSERVE_OBJECT",
)


def _doc(objects, zones=None, traces=None, routines=None):
    return {
        "perspective": "subject",
        "active_traces": traces or [],
        "routines": routines or [],
        "matter_hints": [],
        "matter_space": {"zones": zones or [], "objects": objects},
    }


class PhoneHintInvariants(unittest.TestCase):
    """``phone_tool`` hints stay backward-compatible while naming the new ops."""

    def test_v08_surface_still_registered_and_key_count_frozen(self):
        self.assertIn("v08_living_traces", phone_tool.LIFE_UPDATE_SURFACES)
        # ``test_hermes_hot_reconnect_and_surfaces`` pins this exact 37-key set.
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)

    def test_supported_hint_keeps_existing_subject_actions_and_core_fields(self):
        hint = phone_tool._surface_supported_hint("v08_living_traces")
        for action in (
            "execute_universal_verb",
            "apply_living_trace",
            "compose_open_act",
            "respond_proposed_use",
            "preview_placement",
        ):
            self.assertIn(action, hint["subject_actions"])
        self.assertEqual(hint["core_fields"]["compose_open_act"], ["intent_cn", "steps"])
        self.assertEqual(
            hint["core_fields"]["preview_placement"],
            ["target_parent_id", "object_id", "label_cn", "item_kind"],
        )

    def test_supported_hint_names_all_eleven_step_ops(self):
        hint = phone_tool._surface_supported_hint("v08_living_traces")
        self.assertEqual(tuple(hint["step_ops"][:7]), BASE_OPS)
        for op in CRAFT_OPS:
            self.assertIn(op, hint["step_ops"])
        self.assertIn("零义务", hint["notes_cn"])

    def test_additive_keys_do_not_leak_to_other_surfaces(self):
        hint = phone_tool._surface_supported_hint("v02_common_ground")
        self.assertNotIn("step_ops", hint)
        self.assertNotIn("notes_cn", hint)


class LivedContextCraftCue(unittest.TestCase):
    """``_format_v08_living_traces_micro`` folds at most one half-finished craft cue."""

    def test_max_lived_context_chars_is_unchanged(self):
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

    def test_oversized_unfitted_garment_cue(self):
        doc = _doc(
            [{
                "object_id": "coat",
                "label_cn": "外套",
                "parent_id": "home_bed",
                "fit_status": "oversized_unfitted",
            }],
            zones=[{"zone_id": "home_bed", "visibility": "shared"}],
        )
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertTrue(line.startswith("- 屋里动态："))
        self.assertIn("不合身", line)
        self.assertLessEqual(len(line), 160)

    def test_multi_unit_material_on_home_desk_cue(self):
        doc = _doc([{
            "object_id": "cloth_stack",
            "label_cn": "布料",
            "parent_id": "home_desk",
            "material_units": 3,
        }])
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertIn("材料", line)

    def test_single_unit_material_is_not_a_cue(self):
        doc = _doc([{
            "object_id": "cloth_stack",
            "label_cn": "布料",
            "parent_id": "home_desk",
            "material_units": 1,
        }])
        self.assertEqual(lcb._format_v08_living_traces_micro(doc, include_private=True), "")

    def test_transcribed_source_cue(self):
        doc = _doc([{
            "object_id": "paper",
            "label_cn": "纸片",
            "parent_id": "home_desk",
            "transcribed_source": "旧信",
        }])
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertIn("抄", line)

    def test_patched_wear_state_cue(self):
        doc = _doc([{
            "object_id": "sweater",
            "label_cn": "毛衣",
            "parent_id": "home_bed",
            "wear_state": "patched",
        }])
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertIn("补过", line)

    def test_only_one_craft_phrase_even_with_several_cues(self):
        doc = _doc([
            {"object_id": "coat", "label_cn": "外套", "parent_id": "home_bed",
             "fit_status": "oversized_unfitted"},
            {"object_id": "sweater", "label_cn": "毛衣", "parent_id": "home_bed",
             "wear_state": "patched"},
        ])
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertIn("不合身", line)
        self.assertNotIn("补过", line)

    def test_private_object_never_leaks_without_capability(self):
        doc = _doc([{
            "object_id": "coat",
            "label_cn": "外套",
            "parent_id": "home_bed",
            "visibility": "private",
            "fit_status": "oversized_unfitted",
        }], zones=[{"zone_id": "home_bed", "visibility": "shared"}])
        self.assertEqual(
            lcb._format_v08_living_traces_micro(doc, include_private=False), ""
        )
        self.assertIn(
            "不合身",
            lcb._format_v08_living_traces_micro(doc, include_private=True),
        )

    def test_object_in_private_zone_never_leaks_without_capability(self):
        doc = _doc([{
            "object_id": "coat",
            "label_cn": "外套",
            "parent_id": "subject_private_drawer",
            "fit_status": "oversized_unfitted",
        }], zones=[{"zone_id": "subject_private_drawer", "visibility": "private"}])
        self.assertEqual(
            lcb._format_v08_living_traces_micro(doc, include_private=False), ""
        )
        self.assertIn(
            "不合身",
            lcb._format_v08_living_traces_micro(doc, include_private=True),
        )

    def test_inactive_object_is_ignored(self):
        doc = _doc([{
            "object_id": "coat",
            "label_cn": "外套",
            "parent_id": "home_bed",
            "fit_status": "oversized_unfitted",
            "active": False,
        }])
        self.assertEqual(lcb._format_v08_living_traces_micro(doc, include_private=True), "")

    def test_line_is_capped_at_160_with_cue_and_traces(self):
        doc = _doc(
            [{"object_id": "coat", "label_cn": "外套", "parent_id": "home_bed",
              "fit_status": "oversized_unfitted"}],
            zones=[{"zone_id": "home_bed", "visibility": "shared"}],
            traces=[
                {"object_label_cn": "窗台", "state_sticker_cn": "留着一点暖意",
                 "author_side": "partner", "fade_mode": "timed", "remaining_minutes": 30},
                {"object_label_cn": "书桌", "state_sticker_cn": "摊着没写完的纸",
                 "author_side": "subject", "fade_mode": "until_touched"},
            ],
            routines=[{"title_cn": "睡前把杯子收到水池"}],
        )
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertLessEqual(len(line), 160)
        self.assertIn("不合身", line)

    def test_summary_without_matter_space_still_renders(self):
        # The disk fallback summary carries ``matter_hints`` but no ``matter_space``.
        doc = {
            "active_traces": [{"object_label_cn": "窗台", "state_sticker_cn": "温的"}],
            "routines": [],
            "matter_hints": ["窗台的杯子还能用"],
        }
        line = lcb._format_v08_living_traces_micro(doc, include_private=False)
        self.assertIn("屋里动态", line)


class DailyLoopCraftPassThrough(unittest.TestCase):
    """A craft ``open_act_payload`` reaches ``OPEN_MATTER_ACT`` untouched."""

    def test_event_builder_maps_craft_payload(self):
        payload = {
            "intent_cn": "把两份材料拆开",
            "steps": [{"op": "SPLIT_MATERIAL", "object_id": "mat1", "split_into": 2}],
        }
        event = bridge._v02v03_event_from_descriptor(
            "cand:v08_matter_craft_split",
            {
                "source_ref": "v08_matter:craft_split",
                "action_family": "compose_open_act",
                "open_act_payload": payload,
            },
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["action_kind"], "OPEN_MATTER_ACT")
        self.assertEqual(event["open_act_payload"]["steps"][0]["op"], "SPLIT_MATERIAL")
        # deep copy: the descriptor payload is never aliased into the event
        self.assertIsNot(event["open_act_payload"], payload)

    def test_settle_forwards_craft_steps_and_names_slice(self):
        captured = {}

        def fake_compose(root, composed, *, actor_side, now_iso):
            captured["payload"] = dict(composed)
            captured["actor_side"] = actor_side
            return {
                "status": "applied",
                "intent_cn": "把两份材料拆开",
                "steps_total": 1,
                "steps_applied": 1,
            }

        event = {
            "open_act_payload": {
                "intent_cn": "把两份材料拆开",
                "steps": [{"op": "SPLIT_MATERIAL", "object_id": "mat1", "split_into": 2}],
            },
            "matter_target_ref": "v08_matter:craft_split",
            "operation_key": "op-craft-1",
            "decision_id": "dec-craft-1",
        }
        slices: list[str] = []
        details: dict = {}
        inst_root = Path(tempfile.mkdtemp(prefix="v08craft-inst-"))
        life_root = Path(tempfile.mkdtemp(prefix="v08craft-life-"))
        with mock.patch.object(bridge.v08_matter, "compose_open_act", fake_compose):
            out = bridge._settle_v08_matter_open_act(
                inst_root,
                life_root,
                event,
                now_iso=T0,
                slices_updated=slices,
                details=details,
            )
        self.assertIsNone(out)
        self.assertEqual(captured["actor_side"], "subject")
        self.assertEqual(captured["payload"]["steps"][0]["op"], "SPLIT_MATERIAL")
        self.assertTrue(captured["payload"]["operation_key"].startswith("op-craft-1"))
        self.assertIn("world.v08_matter_space", slices)
        self.assertEqual(details["v08_matter_act"]["status"], "applied")

    def test_missing_steps_refuses_cleanly(self):
        event = {"open_act_payload": {"intent_cn": "空的"}, "operation_key": "op-empty"}
        out = bridge._settle_v08_matter_open_act(
            Path(tempfile.mkdtemp(prefix="v08craft-inst-")),
            Path(tempfile.mkdtemp(prefix="v08craft-life-")),
            event,
            now_iso=T0,
            slices_updated=[],
            details={},
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["status"], "REFUSED")
        self.assertEqual(out["reason_code"], "V08_MATTER_PAYLOAD_MISSING")
        self.assertEqual(out["llm_calls"], 0)


class EnginePrimitiveAvailability(unittest.TestCase):
    """Resilient integration: only asserts what Worker 7A has actually landed."""

    def test_craft_primitives_present_when_engine_exposes_them(self):
        if engine is None:
            self.skipTest("v08_universal_matter not importable yet")
        primitives = tuple(getattr(engine, "OPEN_PRIMITIVES", ()) or ())
        if "SPLIT_MATERIAL" not in primitives:
            self.skipTest("Worker 7A has not landed craft primitives yet")
        for op in CRAFT_OPS:
            self.assertIn(op, primitives)
        # the original seven must never regress
        for op in BASE_OPS:
            self.assertIn(op, primitives)

    @unittest.skipUnless(
        engine is not None
        and "SPLIT_MATERIAL" in tuple(getattr(engine, "OPEN_PRIMITIVES", ()) or ()),
        "craft primitives not landed yet",
    )
    def test_real_scan_to_daily_loop_settlement_roundtrip(self):
        """A real splittable paper stock -> scan -> descriptor -> settled receipt."""

        tmp = tempfile.TemporaryDirectory(prefix="v08craft-e2e-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()

        state = engine._default_state()
        # Free one desk slot so the split's new unit has an honest place to land.
        state["matter_objects"].pop("pen_02", None)
        paper_id = None
        for oid, obj in state["matter_objects"].items():
            if obj.get("parent_id") == "home_desk" and str(obj.get("item_kind")) == "paper":
                obj["material_units"] = 3
                obj["paper_like"] = True
                paper_id = oid
                break
        self.assertIsNotNone(paper_id)
        engine._atomic_write_state(root, state)

        opps = engine.scan_matter_opportunities_zero_write(root, now_iso=T0)
        craft = [
            opp for opp in opps
            if str(opp.get("target_ref") or "").startswith("v08_matter:craft_split:")
        ]
        self.assertTrue(craft, "a 3-unit paper stock on home_desk must offer a split")
        payload = dict(craft[0]["open_act_payload"])
        self.assertEqual(payload["steps"][0]["op"], "SPLIT_MATERIAL")

        event = bridge._v02v03_event_from_descriptor(
            "cand:v08_matter_craft_split",
            {
                "source_ref": craft[0]["target_ref"],
                "action_family": "compose_open_act",
                "open_act_payload": payload,
            },
        )
        self.assertEqual(event["action_kind"], "OPEN_MATTER_ACT")

        slices: list[str] = []
        details: dict = {}
        out = bridge._settle_v08_matter_open_act(
            root,
            root,
            event,
            now_iso=T0,
            slices_updated=slices,
            details=details,
        )
        self.assertIsNone(out)
        self.assertEqual(details["v08_matter_act"]["status"], "applied")
        self.assertIn("world.v08_matter_space", slices)

        after = engine._read_state_zero_write(root)
        source_units = int(after["matter_objects"][paper_id].get("material_units") or 0)
        # take_units defaults to 1 in the scan payload: 3 -> 2, never invented away.
        self.assertEqual(source_units, 2)



if __name__ == "__main__":
    unittest.main()
