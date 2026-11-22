#!/usr/bin/env python3
"""Round C1: compositional micro-action causal lifecycle.

Covers the shared wire contract:

* zero-code new containers -- a ``ceramic_vase`` exists purely as data (no
  vase-specific code anywhere) and still accepts an inside placement, posture
  metadata and an attributed association, persisted and cold-reloadable;
* carrier-bound relational posture follows its carrier without leaking page
  text, and breaks honestly when the object is picked up or moved away
  (intrinsic ``folded`` traces survive a move);
* live-projected group arrangements -- one moved/picked-up member drops the
  whole arrangement, and a broken line never resurrects by merely putting the
  member back;
* the subject's pre-miss beliefs are never overwritten by a hidden partner move;
* attributed personal associations are strictly non-escalating.

Run (WSL): ``python -m unittest tests.test_v08_micro_action_causal_lifecycle -v``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.phone import v08_living_traces_view as traces_view  # noqa: E402
from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _row(object_id: str, label: str, kind: str, parent: str, *, capacity: int = 0, paper_like: bool = False, **extra):
    row = engine._object_row(
        object_id,
        label,
        item_kind=kind,
        parent_id=parent,
        capacity_slots=capacity,
        paper_like=paper_like,
    )
    row.update(extra)
    return row


class MicroActionCausalLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-c1-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _write_state(self, objects: dict, *, zones: dict | None = None, legacy_objects: bool = False) -> None:
        doc = {
            "schema_version": engine.SCHEMA_VERSION,
            "revision": 1,
            "subject_view_revision": 1,
            "next_op_seq": 0,
            "committed_op_ring": [],
            "evicted_up_to_revision": 0,
            "zones": {**engine._default_zones(), **(zones or {})},
            "arrangements": {},
            "recent_acts": [],
            "operations": {},
            "discards": [],
        }
        if legacy_objects:
            # The tolerant alias: rows keyed as ``objects`` in a fixture.
            doc["objects"] = objects
        else:
            doc["matter_objects"] = objects
        path = engine.state_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def _act(self, steps: list, *, intent: str = "做一个连续的小动作", now: str = T0, **extra) -> dict:
        payload = {"intent_cn": intent, "steps": steps}
        payload.update(extra)
        return engine.compose_open_act(self.root, payload, now_iso=now)

    def _view(self, side: str = "subject", *, now: str = T0) -> dict:
        return engine.read_matter_space_view(self.root, actor_side=side, now_iso=now)

    def _obj(self, object_id: str, side: str = "subject", *, now: str = T0) -> dict:
        for row in self._view(side, now=now)["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in {side} view: {object_id}")

    def _raw(self) -> dict:
        return json.loads(engine.state_path(self.root).read_text(encoding="utf-8"))

    # -- 1. zero-code ceramic vase --------------------------------------

    def test_01_zero_code_ceramic_vase_carries_inside_posture_and_association(self) -> None:
        # No vase-specific branch exists in the engine or the phone view.
        for module in (engine, traces_view):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("ceramic_vase", source)
            self.assertNotIn("paint_brush", source)

        self._write_state(
            {
                "ceramic_vase": _row(
                    "ceramic_vase", "青瓷花瓶", "vessel", "home_desk", capacity=2,
                    matter_profile={
                        "item_kind": "vessel",
                        "capacity_slots": 2,
                        "container": True,
                        "open_state": "open",
                    },
                ),
                "paint_brush": _row("paint_brush", "细头画笔", "tool", "home_desk"),
            },
            legacy_objects=True,
        )

        receipt = self._act([
            {
                "op": "PLACE_INSIDE",
                "object_id": "paint_brush",
                "target_parent_id": "ceramic_vase",
                "posture_tag": "upright",
            },
            {
                "op": "NOTE_ASSOCIATION",
                "target_id": "paint_brush",
                "text": "以后拿它插画笔",
            },
        ])
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["steps_applied"], 2)

        state = engine._read_state_zero_write(self.root)
        brush = state["matter_objects"]["paint_brush"]
        self.assertEqual(brush["parent_id"], "ceramic_vase")
        self.assertEqual(brush["placement_relation"], "inside")
        self.assertEqual(brush["posture_tag"], "upright")
        self.assertEqual(brush["subject_association"]["by"], "subject")
        self.assertEqual(brush["subject_association"]["text"], "以后拿它插画笔")
        self.assertTrue(engine.state_path(self.root).is_file())

        # Cold reload from the persisted document (a new read, not a cached state).
        cold = self._raw()
        cold_brush = cold["matter_objects"]["paint_brush"]
        self.assertEqual(cold_brush["placement_relation"], "inside")
        self.assertEqual(cold_brush["subject_association"]["text"], "以后拿它插画笔")

        for side in ("subject", "partner"):
            projected = self._obj("paint_brush", side)
            self.assertEqual(projected["parent_id"], "ceramic_vase")
            self.assertEqual(projected["placement_relation"], "inside")
            self.assertEqual(projected["posture_tag"], "upright")
            self.assertEqual(projected["subject_association"]["text"], "以后拿它插画笔")

        hints = traces_view._matter_hints(self._view("subject"), include_private=True)
        self.assertTrue(any("以后拿它插画笔" in row["text_cn"] for row in hints), hints)
        self.assertTrue(any("自己记的" in row["text_cn"] for row in hints), hints)

    # -- 2. coat draping follow/break -----------------------------------

    def test_02_coat_drape_persists_then_breaks_on_pickup_and_move(self) -> None:
        bed = engine._zone_row("home_bed", "床边", ambient_zone="room", water_source=False, capacity_slots=3)
        self._write_state(
            {
                "coat_01": _row("coat_01", "薄外套", "everyday", "home_desk"),
                "chair_back": _row(
                    "chair_back", "椅背", "everyday", "home_desk",
                    matter_profile={"kind": "chair", "surface": True},
                ),
            },
            zones={"home_bed": bed},
        )

        self._act([{
            "op": "PLACE_OR_MOVE",
            "object_id": "coat_01",
            "target_parent_id": "chair_back",
            "placement_relation": "draped_on",
            "posture_tag": "casual_drape",
        }])
        coat = self._obj("coat_01")
        self.assertEqual(coat["parent_id"], "chair_back")
        self.assertEqual(coat["placement_relation"], "draped_on")
        self.assertEqual(coat["posture_tag"], "casual_drape")

        # Survives a cold restart.
        cold = self._raw()["matter_objects"]["coat_01"]
        self.assertEqual(cold["placement_relation"], "draped_on")
        self.assertEqual(cold["posture_tag"], "casual_drape")

        # Picking it up immediately invalidates the carrier-bound relation.
        self._act([{"op": "TAKE_OBJECT", "object_id": "coat_01"}])
        held = self._obj("coat_01")
        self.assertEqual(held["held_by"], "subject")
        self.assertEqual(held["placement_relation"], "")
        self.assertEqual(held["posture_tag"], "")
        self.assertIsNone(held["anchor_page"])

        # A restart never resurrects the old drape.
        cold = self._raw()["matter_objects"]["coat_01"]
        self.assertNotEqual(cold["placement_relation"], "draped_on")
        self.assertNotEqual(cold["posture_tag"], "casual_drape")
        self.assertEqual(cold["held_by"], "subject")

        # Re-drape, then a plain move elsewhere also breaks the binding.
        self._act([{
            "op": "PLACE_OR_MOVE",
            "object_id": "coat_01",
            "target_parent_id": "chair_back",
            "placement_relation": "draped_on",
            "posture_tag": "casual_drape",
        }])
        self.assertEqual(self._obj("coat_01")["placement_relation"], "draped_on")
        self._act([{
            "op": "MOVE_OBJECT",
            "object_id": "coat_01",
            "target_parent_id": "home_bed",
        }])
        moved = self._obj("coat_01")
        self.assertEqual(moved["parent_id"], "home_bed")
        self.assertEqual(moved["placement_relation"], "on")
        self.assertEqual(moved["posture_tag"], "")
        self.assertIsNone(moved["held_by"])

    # -- 3. dry leaf tucked in book -------------------------------------

    def test_03_leaf_tucked_in_book_follows_carrier_without_page_text(self) -> None:
        secret = "第42页写着一段绝不该泄漏的正文"
        self._write_state({
            "book_01": _row(
                "book_01", "旧笔记本", "paper", "home_desk", capacity=2, paper_like=True,
                matter_profile={
                    "kind": "notebook",
                    "readable": True,
                    "pages": 300,
                    "open_state": "open",
                    "page_text": {42: secret},
                },
            ),
            "dry_leaf": _row("dry_leaf", "干树叶", "paper", "home_desk", paper_like=True),
        })

        self._act([{
            "op": "PLACE_OR_MOVE",
            "object_id": "dry_leaf",
            "target_parent_id": "book_01",
            "placement_relation": "tucked_in",
            "anchor_page": 42,
            "posture_tag": "half_showing",
        }])

        # The book itself moves to another zone and is closed.
        self._act([{
            "op": "PLACE_OR_MOVE",
            "object_id": "book_01",
            "target_parent_id": "home_staging_corner",
            "open_state": "closed",
        }])

        for side in ("subject", "partner"):
            book = self._obj("book_01", side)
            leaf = self._obj("dry_leaf", side)
            self.assertEqual(book["parent_id"], "home_staging_corner")
            self.assertEqual(book["open_state"], "closed")
            self.assertEqual(leaf["parent_id"], "book_01")
            self.assertEqual(leaf["placement_relation"], "tucked_in")
            self.assertEqual(leaf["anchor_page"], 42)
            self.assertEqual(leaf["posture_tag"], "half_showing")

        blob = json.dumps(self._view("subject"), ensure_ascii=False)
        self.assertNotIn(secret, blob)
        self.assertNotIn("page_text", blob)
        hints_blob = json.dumps(
            traces_view._matter_hints(self._view("subject"), include_private=True), ensure_ascii=False
        )
        self.assertNotIn(secret, hints_blob)

        # Cold restart keeps the mark, still without any page text.
        cold_leaf = self._raw()["matter_objects"]["dry_leaf"]
        self.assertEqual(cold_leaf["placement_relation"], "tucked_in")
        self.assertEqual(cold_leaf["anchor_page"], 42)
        cold_blob = json.dumps(self._view("subject"), ensure_ascii=False)
        self.assertNotIn(secret, cold_blob)
        self.assertNotIn("page_text", cold_blob)

        # Moving the leaf out clears the carrier-bound relation and page mark.
        self._act([{
            "op": "PLACE_OR_MOVE",
            "object_id": "dry_leaf",
            "target_parent_id": "home_desk",
        }])
        out = self._obj("dry_leaf")
        self.assertEqual(out["parent_id"], "home_desk")
        self.assertNotEqual(out["placement_relation"], "tucked_in")
        self.assertIsNone(out["anchor_page"])
        self.assertEqual(out["posture_tag"], "")

    # -- 4. intrinsic folded trace survives a surface move ---------------

    def test_04_folded_trace_survives_moving_to_another_surface(self) -> None:
        scarf = _row("scarf_01", "叠好的围巾", "everyday", "home_desk", posture_tag="folded")
        self._write_state({"scarf_01": scarf})

        self._act([{
            "op": "PLACE_OR_MOVE",
            "object_id": "scarf_01",
            "target_parent_id": "dining_table",
        }])
        moved = self._obj("scarf_01")
        self.assertEqual(moved["parent_id"], "dining_table")
        self.assertEqual(moved["placement_relation"], "on")
        self.assertEqual(moved["posture_tag"], "folded")

        cold = self._raw()["matter_objects"]["scarf_01"]
        self.assertEqual(cold["posture_tag"], "folded")

    # -- 5. paperclip row live projection --------------------------------

    def test_05_paperclip_row_is_live_projected_and_never_resurrects(self) -> None:
        self._write_state({
            "clip_1": _row("clip_1", "回形针一", "everyday", "home_desk"),
            "clip_2": _row("clip_2", "回形针二", "everyday", "home_desk"),
            "clip_3": _row("clip_3", "回形针三", "everyday", "home_desk"),
        })

        receipt = self._act([{
            "op": "ARRANGE_OBJECTS",
            "zone_or_carrier_id": "home_desk",
            "member_ids": ["clip_1", "clip_2", "clip_3"],
            "pattern": "row",
        }])
        arrangement_id = receipt["step_results"][0]["arrangement_id"]
        self.assertTrue(arrangement_id)

        active = self._view("subject")["active_arrangements"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["member_ids"], ["clip_1", "clip_2", "clip_3"])
        self.assertEqual(active[0]["pattern"], "row")
        # No dishonest stored "still intact" boolean anywhere.
        record = self._raw()["arrangements"][arrangement_id]
        self.assertNotIn("still_intact", record)

        before = engine._read_state_zero_write(self.root)["matter_objects"]["clip_1"]
        self._act([{
            "op": "NOTE_ASSOCIATION",
            "target_id": arrangement_id,
            "text": "排成一排像小鱼",
        }])
        after = engine._read_state_zero_write(self.root)["matter_objects"]["clip_1"]
        for key in ("parent_id", "thermal", "cleanliness", "item_kind", "capacity_slots"):
            self.assertEqual(before[key], after[key])
        active = self._view("subject")["active_arrangements"]
        self.assertEqual(active[0]["subject_association"]["text"], "排成一排像小鱼")

        # Picking one member up drops the arrangement immediately on both doors.
        self._act([{"op": "TAKE_OBJECT", "object_id": "clip_2"}])
        self.assertEqual(self._view("subject")["active_arrangements"], [])
        self.assertEqual(self._view("partner")["active_arrangements"], [])
        self.assertIsNotNone(self._raw()["arrangements"][arrangement_id]["broken_at"])

        # Cold restart agrees.
        self.assertEqual(self._view("subject")["active_arrangements"], [])

        # Putting the clip back casually does not fake the old line.
        self._act([{
            "op": "PLACE_OR_MOVE",
            "object_id": "clip_2",
            "target_parent_id": "home_desk",
        }])
        self.assertEqual(self._view("subject")["active_arrangements"], [])
        self.assertEqual(self._view("partner")["active_arrangements"], [])

        # A deliberate re-arrangement does restore an honest new line.
        self._act([{
            "op": "ARRANGE_OBJECTS",
            "arrangement_id": arrangement_id,
            "zone_or_carrier_id": "home_desk",
            "member_ids": ["clip_1", "clip_2", "clip_3"],
            "pattern": "row",
        }])
        active = self._view("subject")["active_arrangements"]
        self.assertEqual(len(active), 1)
        self.assertIsNone(self._raw()["arrangements"][arrangement_id]["broken_at"])

    # -- 6. attributed association is strictly non-escalating ------------

    def test_06_association_never_escalates_physics_and_is_attributed(self) -> None:
        self._write_state({
            "cup_01": _row("cup_01", "白瓷杯", "vessel", "home_desk", capacity=2),
            "home_door": _row("home_door", "房门", "everyday", "home_staging_corner", open_state="closed"),
        })

        self._act([{
            "op": "NOTE_ASSOCIATION",
            "target_id": "cup_01",
            "text": "温度100度",
        }])
        self._act([{
            "op": "NOTE_ASSOCIATION",
            "target_id": "home_door",
            "text": "门已打开",
        }])

        state = engine._read_state_zero_write(self.root)
        cup = state["matter_objects"]["cup_01"]
        door = state["matter_objects"]["home_door"]
        self.assertEqual(cup["thermal"], "room")
        self.assertEqual(cup["cleanliness"], "clean")
        self.assertEqual(cup["item_kind"], "vessel")
        self.assertEqual(cup["capacity_slots"], 2)
        self.assertEqual(cup["parent_id"], "home_desk")
        self.assertEqual(door["open_state"], "closed")
        self.assertEqual(door["parent_id"], "home_staging_corner")
        self.assertEqual(cup["subject_association"]["text"], "温度100度")

        hints = traces_view._matter_hints(self._view("subject"), include_private=True)
        attributed = [row for row in hints if "自己记的" in row["text_cn"]]
        self.assertTrue(attributed, hints)
        self.assertTrue(any("温度100度" in row["text_cn"] for row in attributed))

        # The real phone gate accepts the same step vocabulary.
        code, body = traces_view.mutate_v08_living_traces_view(
            self.root,
            {
                "action": "compose_open_act",
                "intent_cn": "给自己记一句话",
                "steps": [{
                    "op": "NOTE_ASSOCIATION",
                    "target_id": "cup_01",
                    "text": "这是自己的念想",
                }],
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "applied")

    # -- 7. pre-miss arrangement beliefs --------------------------------

    def test_07_hidden_partner_member_move_does_not_rewrite_subject_arrangement(self) -> None:
        self._write_state({
            "clip_1": _row("clip_1", "回形针一", "everyday", "home_desk"),
            "clip_2": _row("clip_2", "回形针二", "everyday", "home_desk"),
            "clip_3": _row("clip_3", "回形针三", "everyday", "home_desk"),
        })
        self._act([{
            "op": "ARRANGE_OBJECTS",
            "zone_or_carrier_id": "home_desk",
            "member_ids": ["clip_1", "clip_2", "clip_3"],
            "pattern": "row",
        }])

        engine.partner_move_shared_object(
            self.root,
            {"object_id": "clip_2", "target_parent_id": "home_staging_corner"},
            now_iso=T0,
        )

        # The subject still believes the line she made: no hidden move leaks.
        subject = self._view("subject")
        self.assertEqual(len(subject["active_arrangements"]), 1)
        believed_clip = next(row for row in subject["objects"] if row["object_id"] == "clip_2")
        self.assertEqual(believed_clip["parent_id"], "home_desk")

        # The partner God-view knows the line is already broken.
        self.assertEqual(self._view("partner")["active_arrangements"], [])

        # Real observation updates her belief, and the arrangement drops.
        self._act([{"op": "OBSERVE_OBJECT", "object_id": "clip_2"}])
        self.assertEqual(self._view("subject")["active_arrangements"], [])

    # -- 8. closed carrier refuses a new tuck ---------------------------

    def test_08_closed_book_refuses_a_new_tuck(self) -> None:
        self._write_state({
            "book_01": _row(
                "book_01", "旧笔记本", "paper", "home_desk", capacity=2, paper_like=True,
                matter_profile={"kind": "book", "readable": True, "pages": 200, "open_state": "closed"},
            ),
            "dry_leaf": _row("dry_leaf", "干树叶", "paper", "home_desk", paper_like=True),
        })
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act([{
                "op": "PLACE_OR_MOVE",
                "object_id": "dry_leaf",
                "target_parent_id": "book_01",
                "placement_relation": "tucked_in",
                "anchor_page": 12,
            }])
        self.assertEqual(ctx.exception.reason_code, "INVALID_CONTAINMENT")
        self.assertEqual(self._obj("dry_leaf")["parent_id"], "home_desk")

    # -- 9. flat compose payload inherits into steps --------------------

    def test_09_flat_compose_fields_reach_the_matching_step(self) -> None:
        self._write_state({
            "book_01": _row(
                "book_01", "旧笔记本", "paper", "home_desk", capacity=2, paper_like=True,
                matter_profile={"kind": "notebook", "readable": True, "pages": 200, "open_state": "open"},
            ),
            "dry_leaf": _row("dry_leaf", "干树叶", "paper", "home_desk", paper_like=True),
        })

        receipt = self._act(
            [
                {"op": "MOVE_OBJECT", "object_id": "dry_leaf", "target_parent_id": "book_01"},
                {"op": "NOTE_ASSOCIATION"},
            ],
            placement_relation="tucked_in",
            anchor_page=7,
            target_id="dry_leaf",
            text="夹在第七页",
        )
        self.assertEqual(receipt["status"], "applied")
        leaf = self._obj("dry_leaf")
        self.assertEqual(leaf["parent_id"], "book_01")
        self.assertEqual(leaf["placement_relation"], "tucked_in")
        self.assertEqual(leaf["anchor_page"], 7)
        self.assertEqual(leaf["subject_association"]["text"], "夹在第七页")


if __name__ == "__main__":
    unittest.main(verbosity=2)
