#!/usr/bin/env python3
"""Carrier chains and honest outing packs (v0.4 Ch.39 §二 & §六).

Four guarantees are exercised here:

1. **Transitive location** -- a packed thing inherits its carrier's effective
   zone/place through the real ``parent_id`` chain (``note -> bag -> chair ->
   desk``) without any child row being rewritten on disk.
2. **Partial-pack honesty** -- one unreachable item never rolls back the items
   that already went in, and a subject-side reach miss never learns where a
   partner actually hid the thing (before or after a reload).
3. **Journey cross-read** -- a carried carrier and everything inside it follow
   the real journey record (in transit / at the bookstore / back home) through
   100% zero-write reads.
4. **Replay** -- the same ``operation_key`` never double-packs and never errors.

Run (WSL)::

    PYTHONPATH=components/life/scripts:components/world/scripts:components/phone:. \
        python3 -m unittest tests.test_v08_carrier_chain_and_outing_pack -v
"""

from __future__ import annotations

import json
import re
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
SNAKE_TOKEN = re.compile(r"[A-Za-z0-9]+_[A-Za-z0-9_]+")


def _stable(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _zone(zone_id: str, label_cn: str, *, capacity: int = 4) -> dict:
    return engine._zone_row(
        zone_id, label_cn, ambient_zone="room", water_source=False, capacity_slots=capacity,
    )


def _write_state(root: Path, objects: dict, *, zones: dict | None = None) -> None:
    zone_rows = engine._default_zones()
    zone_rows["study_desk"] = _zone("study_desk", "书桌台面")
    zone_rows["entry_door"] = _zone("entry_door", "门口", capacity=2)
    if zones:
        zone_rows.update(zones)
    doc = {
        "schema_version": engine.SCHEMA_VERSION,
        "revision": 1,
        "subject_view_revision": 1,
        "next_op_seq": 0,
        "committed_op_ring": [],
        "evicted_up_to_revision": 0,
        "created_at": T0,
        "updated_at": T0,
        "zones": zone_rows,
        "matter_objects": objects,
        "arrangements": {},
        "recent_acts": [],
        "operations": {},
        "discards": [],
    }
    path = engine.state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_journey(root: Path, journey: dict) -> Path:
    path = root / "world" / "v04_neighborhood_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"journey": journey}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def _file_fingerprint(path: Path) -> tuple[int, bytes]:
    stat = path.stat()
    return stat.st_mtime_ns, path.read_bytes()


class CarrierChainAndOutingPackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-carrier-chain-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _bag(self, parent_id: str = "entry_door") -> dict:
        return engine._carrier_object_row(
            "canvas_bag", engine.OUTING_CARRIER_DEFAULTS["canvas_bag"], parent_id=parent_id,
        )

    def _item(self, object_id: str, label_cn: str, *, parent_id: str = "study_desk", **extra) -> dict:
        row = engine._object_row(object_id, label_cn, item_kind="everyday", parent_id=parent_id)
        row["updated_at"] = T0
        row.update(extra)
        return row

    def _child_ids(self, space: dict, parent_id: str) -> list[str]:
        row = next(r for r in space["objects"] if r["object_id"] == parent_id)
        return list(row.get("children_ids") or [])

    def _row(self, space: dict, object_id: str) -> dict:
        return next(r for r in space["objects"] if r["object_id"] == object_id)

    # -- 1. packing + transitive location -------------------------------

    def test_01_pack_keeps_intrinsic_posture_and_derives_zone_without_rewriting_children(self) -> None:
        note = self._item(
            "folded_note", "折好的便条", item_kind="paper",
            paper_like=True, posture_tag="folded", placement_relation="tucked_in",
        )
        mug = self._item(
            "ceramic_mug", "陶杯", item_kind="vessel",
            capacity_slots=1, posture_tag="casual_drape", placement_relation="draped_on",
        )
        chair = engine._object_row(
            "wooden_chair", "木椅", item_kind="furniture", parent_id="study_desk",
        )
        _write_state(self.root, {
            "canvas_bag": self._bag(),
            "folded_note": note,
            "ceramic_mug": mug,
            "wooden_chair": chair,
        })

        receipt = engine.compose_open_act(self.root, {
            "intent_cn": "把折好的便条和陶杯装进帆布挎包",
            "steps": [{
                "op": "PACK_FOR_OUTING",
                "carrier_id": "canvas_bag",
                "item_ids": ["folded_note", "ceramic_mug"],
            }],
        }, now_iso=T0)
        self.assertEqual(receipt["status"], "applied")
        pack = receipt["step_results"][0]
        self.assertEqual(pack["status"], "packed")
        self.assertEqual(sorted(pack["packed_item_ids"]), ["ceramic_mug", "folded_note"])
        self.assertEqual(pack["unpacked_items"], [])
        self.assertEqual(pack["carrier_effective_zone_id"], "entry_door")

        space = engine.read_matter_space_view(self.root, actor_side="subject", now_iso=T0)
        self.assertEqual(sorted(self._child_ids(space, "canvas_bag")), ["ceramic_mug", "folded_note"])
        packed_note = self._row(space, "folded_note")
        packed_mug = self._row(space, "ceramic_mug")
        # Extrinsic drape goes away; intrinsic folded trace survives.
        self.assertEqual(packed_note["parent_id"], "canvas_bag")
        self.assertEqual(packed_note["placement_relation"], "inside")
        self.assertEqual(packed_note["posture_tag"], "folded")
        self.assertEqual(packed_mug["parent_id"], "canvas_bag")
        self.assertEqual(packed_mug["placement_relation"], "inside")
        self.assertEqual(packed_mug["posture_tag"], "")

        before = engine._read_state_zero_write(self.root)["matter_objects"]

        # Move the carrier itself twice: desk, then the chair standing on the desk.
        engine.compose_open_act(self.root, {
            "intent_cn": "把挎包拎到书桌边",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "canvas_bag",
                       "target_parent_id": "study_desk"}],
        }, now_iso=T0)
        engine.compose_open_act(self.root, {
            "intent_cn": "把挎包搁到木椅上",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "canvas_bag",
                       "target_parent_id": "wooden_chair", "placement_relation": "leaning_on"}],
        }, now_iso=T0)

        after = engine._read_state_zero_write(self.root)["matter_objects"]
        for child_id in ("folded_note", "ceramic_mug"):
            self.assertEqual(after[child_id]["parent_id"], "canvas_bag")
            self.assertEqual(after[child_id]["updated_at"], before[child_id]["updated_at"])
            self.assertEqual(after[child_id]["state_clock_iso"], before[child_id]["state_clock_iso"])

        moved = engine.read_matter_space_view(self.root, actor_side="subject", now_iso=T0)
        note_row = self._row(moved, "folded_note")
        self.assertEqual(note_row["effective_zone_id"], "study_desk")
        self.assertEqual(note_row["carrier_chain"], ["canvas_bag", "wooden_chair"])
        self.assertFalse(note_row["carried_on_journey"])
        self.assertEqual(note_row["effective_place_id"], "home")

        summaries = traces_view.carrier_travel_summaries(moved)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["summary_cn"], "帆布挎包靠在木椅旁，里面装着2件东西")
        self.assertIsNone(SNAKE_TOKEN.search(summaries[0]["summary_cn"]))

    # -- 2. partial-pack honesty ----------------------------------------

    def test_02_partial_pack_is_honest_and_never_learns_the_hidden_spot(self) -> None:
        book = self._item("book_01", "旧书")
        ruler = self._item("ruler_01", "木尺", item_kind="tool")
        umbrella = self._item("umbrella_01", "折叠伞")
        _write_state(self.root, {
            "book_01": book,
            "ruler_01": ruler,
            "umbrella_01": umbrella,
        })

        # The partner hides the umbrella in the fridge before she packs.
        engine.partner_move_shared_object(
            self.root,
            {"object_id": "umbrella_01", "target_parent_id": "home_fridge"},
            now_iso=T0,
        )

        receipt = engine.compose_open_act(self.root, {
            "intent_cn": "把书、尺子和伞都装进帆布挎包",
            "steps": [{
                "op": "PACK_FOR_OUTING",
                "carrier_id": "canvas_bag",
                "item_ids": ["book_01", "ruler_01", "umbrella_01"],
                "expected_item_zones": {
                    "book_01": "study_desk",
                    "ruler_01": "study_desk",
                    "umbrella_01": "study_desk",
                },
            }],
        }, now_iso=T0)
        self.assertEqual(receipt["status"], "applied")
        pack = receipt["step_results"][0]
        self.assertEqual(pack["status"], "partially_packed")
        self.assertEqual(pack["packed_item_ids"], ["book_01", "ruler_01"])
        self.assertEqual([row["object_id"] for row in pack["unpacked_items"]], ["umbrella_01"])
        self.assertEqual(pack["unpacked_items"][0]["reason_code"], "OBJECT_NOT_AT_EXPECTED_SPOT")
        self.assertIn("没摸到", pack["unpacked_items"][0]["reason_cn"])

        # Nothing in the receipt names the real hiding spot.
        blob = _stable(pack) + _stable(receipt)
        self.assertNotIn("home_fridge", blob)
        self.assertNotIn("共享冰箱", blob)
        self.assertNotIn("冰箱", blob)

        # The two honest items really persisted inside the carrier.
        raw = engine._read_state_zero_write(self.root)["matter_objects"]
        self.assertEqual(raw["book_01"]["parent_id"], "canvas_bag")
        self.assertEqual(raw["ruler_01"]["parent_id"], "canvas_bag")
        self.assertEqual(raw["umbrella_01"]["parent_id"], "home_fridge")
        self.assertTrue(raw["umbrella_01"]["not_at_believed_spot"])
        self.assertEqual(raw["umbrella_01"]["last_miss_parent"], "study_desk")

        # Subject surfaces stay quiet about the fridge, even after a reload.
        for _ in range(2):
            space = engine.read_matter_space_view(self.root, actor_side="subject", now_iso=T0)
            ids = {row["object_id"] for row in space["objects"]}
            self.assertNotIn("umbrella_01", ids)
            self.assertNotIn("umbrella_01", _stable(space))
            self.assertFalse(any(row.get("parent_id") == "home_fridge" for row in space["objects"]))
            summaries = traces_view.carrier_travel_summaries(space)
            self.assertEqual(len(summaries), 1)
            self.assertIn("帆布挎包", summaries[0]["summary_cn"])
            self.assertIsNone(SNAKE_TOKEN.search(summaries[0]["summary_cn"]))

        # The partner God-view still sees the truth.
        partner = engine.read_matter_space_view(self.root, actor_side="partner", now_iso=T0)
        partner_umbrella = self._row(partner, "umbrella_01")
        self.assertEqual(partner_umbrella["parent_id"], "home_fridge")

    # -- 3. journey cross-read, zero-write ------------------------------

    def test_03_journey_moves_whole_carrier_and_never_writes_on_read(self) -> None:
        note = self._item("folded_note", "折好的便条", item_kind="paper", paper_like=True)
        bag = self._bag()
        note["parent_id"] = "canvas_bag"
        note["subject_last_seen_parent"] = "canvas_bag"
        _write_state(self.root, {"canvas_bag": bag, "folded_note": note})

        matter_path = engine.state_path(self.root)
        journey_path = _write_journey(self.root, {
            "state": "at_place",
            "current_place_id": "home",
            "origin_place_id": "home",
            "carried_item_ids": ["canvas_bag"],
        })

        def read_everything() -> dict:
            space = engine.read_matter_space_view(self.root, actor_side="subject", now_iso=T0)
            status, payload = traces_view.read_v08_living_traces_view(
                self.root, has_subject_capability=True, now_iso=T0,
            )
            self.assertEqual(status, 200)
            summary = traces_view.summarize_living_traces_for_context(
                self.root, include_private=True, now_iso=T0,
            )
            return {"space": space, "payload": payload, "summary": summary}

        def assert_zero_write():
            before = {
                "matter": _file_fingerprint(matter_path),
                "journey": _file_fingerprint(journey_path),
            }
            result = read_everything()
            after = {
                "matter": _file_fingerprint(matter_path),
                "journey": _file_fingerprint(journey_path),
            }
            self.assertEqual(before, after, "a read wrote to disk")
            return result

        at_home = assert_zero_write()
        self.assertTrue(self._row(at_home["space"], "canvas_bag")["carried_on_journey"])
        self.assertEqual(self._row(at_home["space"], "folded_note")["effective_place_id"], "home")
        home_summary = at_home["payload"]["data"]["carrier_summaries"][0]["summary_cn"]
        self.assertIn("帆布挎包", home_summary)
        self.assertIsNone(SNAKE_TOKEN.search(home_summary))

        _write_journey(self.root, {
            "state": "in_transit",
            "current_place_id": None,
            "origin_place_id": "home",
            "carried_item_ids": ["canvas_bag"],
        })
        in_transit = assert_zero_write()
        bag_row = self._row(in_transit["space"], "canvas_bag")
        note_row = self._row(in_transit["space"], "folded_note")
        self.assertTrue(bag_row["carried_on_journey"])
        self.assertTrue(note_row["carried_on_journey"])
        self.assertEqual(bag_row["effective_place_id"], "in_transit")
        self.assertEqual(note_row["effective_place_id"], "in_transit")
        transit_summary = in_transit["payload"]["data"]["carrier_summaries"][0]["summary_cn"]
        self.assertIn("路上", transit_summary)
        self.assertIsNone(SNAKE_TOKEN.search(transit_summary))

        _write_journey(self.root, {
            "state": "at_place",
            "current_place_id": "bookstore",
            "origin_place_id": "home",
            "carried_item_ids": ["canvas_bag"],
        })
        at_bookstore = assert_zero_write()
        self.assertEqual(
            self._row(at_bookstore["space"], "folded_note")["effective_place_id"], "bookstore",
        )
        store_summary = at_bookstore["payload"]["data"]["carrier_summaries"][0]["summary_cn"]
        self.assertIn("街角旧书店", store_summary)
        self.assertIsNone(SNAKE_TOKEN.search(store_summary))
        # The summary also reaches the lived-context hints channel.
        hint_texts = [h["text_cn"] for h in at_bookstore["summary"]["matter_hints"]]
        self.assertTrue(any("街角旧书店" in text for text in hint_texts))

        _write_journey(self.root, {
            "state": "at_place",
            "current_place_id": "home",
            "origin_place_id": "home",
            "carried_item_ids": ["canvas_bag"],
        })
        back_home = assert_zero_write()
        self.assertEqual(
            self._row(back_home["space"], "folded_note")["effective_place_id"], "home",
        )

        # A journey that does not carry the bag leaves it at home.
        _write_journey(self.root, {
            "state": "in_transit",
            "current_place_id": None,
            "origin_place_id": "home",
            "carried_item_ids": [],
        })
        left_behind = assert_zero_write()
        self.assertFalse(self._row(left_behind["space"], "canvas_bag")["carried_on_journey"])
        self.assertEqual(
            self._row(left_behind["space"], "folded_note")["effective_place_id"], "home",
        )

    # -- 4. op_key replay ------------------------------------------------

    def test_04_replaying_same_operation_key_never_double_packs(self) -> None:
        _write_state(self.root, {
            "canvas_bag": self._bag(),
            "book_01": self._item("book_01", "旧书"),
            "ruler_01": self._item("ruler_01", "木尺", item_kind="tool"),
        })
        payload = {
            "intent_cn": "把书和尺子装进帆布挎包",
            "steps": [{
                "op": "PACK_FOR_OUTING",
                "carrier_id": "canvas_bag",
                "item_ids": ["book_01", "ruler_01"],
            }],
            "operation_key": "pack-outing-replay-1",
        }

        first = engine.compose_open_act(self.root, payload, now_iso=T0)
        self.assertEqual(first["status"], "applied")
        self.assertFalse(first["already_applied"])
        revision_after_first = engine._read_state_zero_write(self.root)["revision"]

        second = engine.compose_open_act(self.root, payload, now_iso=T0)
        self.assertTrue(second["already_applied"])
        self.assertEqual(second["step_results"][0]["packed_item_ids"], ["book_01", "ruler_01"])
        self.assertEqual(
            engine._read_state_zero_write(self.root)["revision"], revision_after_first,
            "a replayed op_key committed a second time",
        )

        space = engine.read_matter_space_view(self.root, actor_side="subject", now_iso=T0)
        self.assertEqual(sorted(self._child_ids(space, "canvas_bag")), ["book_01", "ruler_01"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
