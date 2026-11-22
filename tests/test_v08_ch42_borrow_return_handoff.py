#!/usr/bin/env python3
"""v0.4 Ch.42: physical borrow/return and public display version handoff.

Deterministic only (``llm_calls == 0`` throughout).  Coverage:

* ``BORROW_NEIGHBORHOOD_ITEM`` mirrors one shared-shelf loan as exactly one home
  object carrying the honest ``loan_meta``; a second borrow of the same lender
  item is refused with ``INVALID_TARGET`` 409; ``request_loan_extension`` moves
  only the due date (``affection_score_coupled == False``); ``RETURN_BORROWED_ITEM``
  hands it back and the shelf item returns to ``on_shelf``;
* a bookstore book borrowed while open and returned after closing never claims
  the shopkeeper received it -- it waits in the return box
  (``loan_status == "return_box_pending"``, ``shopkeeper_received == False``);
* a real split card, transcribed from ``draft://sec`` at version ``v3``, is
  delivered to the public board carrying only the frozen physical snapshot; the
  card leaves home custody and no private draft text crosses over;
* a quiet room with only ``v08_microzone_state.json`` injects no default-desk
  scaffold hints (Background-Silence seam stays byte-identical to absent).

Run (WSL)::

    /tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_ch42_borrow_return_handoff -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
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

from components.world.scripts import v08_universal_matter as engine  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04  # noqa: E402
from components.phone import v08_living_traces_view as view  # noqa: E402

T_OPEN = "2026-11-16T11:00:00+08:00"
T_LATER = "2026-11-16T13:00:00+08:00"
T_CLOSED = "2026-11-16T20:30:00+08:00"


class Ch42HandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08ch42-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "phone").mkdir()
        (self.root / "world").mkdir()

    # -- helpers ---------------------------------------------------------

    def _act(self, now_iso: str, steps: list[dict], *, intent: str = "按部就班地做这件事") -> dict:
        receipt = engine.compose_open_act(
            self.root,
            {"intent_cn": intent, "steps": steps},
            now_iso=now_iso,
        )
        self.assertEqual(receipt["llm_calls"], 0)
        return receipt

    def _active_objects(self, now_iso: str) -> dict[str, dict]:
        space = engine.read_matter_space_view(self.root, now_iso=now_iso)
        return {row["object_id"]: row for row in space["objects"]}

    def _raw_object(self, object_id: str) -> dict:
        return dict(engine._read_state_zero_write(self.root)["matter_objects"][object_id])

    def _projection(self, now_iso: str, *, subject: bool = True) -> dict:
        return v04.project_v04_neighborhood(
            self.root, has_subject_capability=subject, now_iso=now_iso
        )

    def _free_desk_slot(self) -> None:
        # home_desk starts full (4/4); make room for the borrowed ruler honestly.
        state = engine._default_state()
        state["matter_objects"].pop("pen_02")
        engine._atomic_write_state(self.root, state)

    # -- 1. shared shelf: borrow / extension / return --------------------

    def test_borrow_extend_return_shared_shelf_loan(self) -> None:
        self._free_desk_slot()
        shelf_before = self._projection(T_OPEN)["data"]["public_life"]["shared_shelf"]
        expected_label = shelf_before["items_by_id"]["paper_ruler"]["name"]

        borrow = self._act(T_OPEN, [{
            "op": "BORROW_NEIGHBORHOOD_ITEM",
            "source_ring": "shared_shelf",
            "neighborhood_item_id": "paper_ruler",
            "target_parent_id": "home_desk",
            "loan_hours": 1,
        }])
        self.assertEqual(borrow["status"], "applied")
        step = borrow["step_results"][0]
        oid = step["object_id"]
        self.assertEqual(oid, "nb_shared_shelf_paper_ruler")
        self.assertEqual(step["label_cn"], expected_label)
        meta = step["loan_meta"]
        self.assertEqual(meta["source_ring"], "shared_shelf")
        self.assertEqual(meta["lender_label_cn"], "街角共享架")
        self.assertEqual(meta["return_target_id"], "shared_shelf")
        self.assertEqual(meta["loan_status"], "borrowed")
        self.assertTrue(meta["ownership_preserved"])
        self.assertFalse(meta["affection_score_coupled"])

        home = self._active_objects(T_OPEN)[oid]
        self.assertEqual(home["parent_id"], "home_desk")
        self.assertEqual(home["derived_role_cn"], "借来的物件 · 用完需归还")
        self.assertEqual(home["loan_meta"]["neighborhood_item_id"], "paper_ruler")

        # The due time is inside 12h, so one calm "take it back" cue appears.
        candidates = engine.scan_matter_opportunities_zero_write(self.root, now_iso=T_OPEN)
        due_cues = [c for c in candidates if c["target_ref"] == f"v08_matter:return_loan:{oid}"]
        self.assertEqual(len(due_cues), 1)
        self.assertEqual(due_cues[0]["title_hint"], f"顺手把借来的「{expected_label}」拿去还")

        # The same lender item cannot be carried home twice.
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act(T_OPEN, [{
                "op": "BORROW_NEIGHBORHOOD_ITEM",
                "source_ring": "shared_shelf",
                "neighborhood_item_id": "paper_ruler",
                "target_parent_id": "home_staging_corner",
            }])
        self.assertEqual(ctx.exception.reason_code, "INVALID_TARGET")
        self.assertEqual(ctx.exception.status_code, 409)

        # Extension is the subject's own calm date move, never affection.
        code, envelope = view.mutate_v08_living_traces_view(
            self.root,
            {"action": "request_loan_extension", "object_id": oid, "extra_hours": 24},
            actor_side="subject",
            now_iso=T_OPEN,
        )
        self.assertEqual(code, 200)
        data = envelope["data"]
        self.assertEqual(data["extension_status"], "extended")
        self.assertEqual(data["extension_count"], 1)
        self.assertFalse(data["affection_score_coupled"])
        self.assertGreater(
            datetime.fromisoformat(data["due_at"]),
            datetime.fromisoformat(step["due_at"]),
        )
        self.assertEqual(self._raw_object(oid)["loan_meta"]["extension_status"], "extended")

        # The partner door may not extend the subject's loan.
        code, refused = view.mutate_v08_living_traces_view(
            self.root,
            {"action": "request_loan_extension", "object_id": oid},
            actor_side="partner",
            now_iso=T_OPEN,
        )
        self.assertEqual(code, 403)
        self.assertFalse(refused["ok"])

        # Return: the home object leaves custody, the shelf item is back on the shelf.
        returned = self._act(T_LATER, [{
            "op": "RETURN_BORROWED_ITEM",
            "object_id": oid,
        }])
        back = returned["step_results"][0]
        self.assertEqual(back["loan_status"], "returned_received")
        self.assertTrue(back["returned_out"])
        self.assertFalse(back["shopkeeper_received"])
        self.assertNotIn(oid, self._active_objects(T_LATER))
        self.assertTrue(self._raw_object(oid)["returned_out"])
        shelf_after = self._projection(T_LATER)["data"]["public_life"]["shared_shelf"]
        self.assertEqual(shelf_after["items_by_id"]["paper_ruler"]["status"], "on_shelf")

    # -- 2. bookstore: borrow open, return closed ------------------------

    def test_bookstore_return_when_closed_waits_in_box(self) -> None:
        books = self._projection(T_OPEN)["data"]["public_life"]["bookstore"]["books_by_id"]
        expected_title = books["book_paper_joinery"]["title"]

        borrow = self._act(T_OPEN, [{
            "op": "BORROW_NEIGHBORHOOD_ITEM",
            "source_ring": "bookstore",
            "neighborhood_item_id": "book_paper_joinery",
            "target_parent_id": "home_staging_corner",
            "loan_days": 7,
        }])
        step = borrow["step_results"][0]
        oid = step["object_id"]
        self.assertEqual(oid, "nb_bookstore_book_paper_joinery")
        self.assertEqual(step["label_cn"], expected_title)
        self.assertEqual(step["item_kind"], "paper")
        self.assertTrue(step["paper_like"])
        self.assertEqual(step["loan_meta"]["lender_label_cn"], "街角旧书店")
        self.assertEqual(step["loan_meta"]["return_target_id"], "bookstore_return_box")
        self.assertIsNotNone(step["due_at"])

        # 20:30 -- the shop is closed, so the return waits in the box honestly.
        returned = self._act(T_CLOSED, [{
            "op": "RETURN_BORROWED_ITEM",
            "object_id": oid,
        }])
        back = returned["step_results"][0]
        self.assertEqual(back["loan_status"], "return_box_pending")
        self.assertFalse(back["shopkeeper_received"])
        self.assertTrue(back["returned_out"])
        self.assertNotIn(oid, self._active_objects(T_CLOSED))
        raw_meta = self._raw_object(oid)["loan_meta"]
        self.assertEqual(raw_meta["loan_status"], "return_box_pending")
        self.assertFalse(raw_meta["shopkeeper_received"])
        self.assertFalse(raw_meta["affection_score_coupled"])

    # -- 3. physical card -> public board (version handoff) --------------

    def test_card_delivery_hands_over_only_physical_snapshot(self) -> None:
        excerpt = "门廊檐下的风比院心慢半拍。"
        receipt = self._act(T_OPEN, [
            {
                "op": "INTRODUCE_ITEM",
                "object_id": "paper_stock_01",
                "label_cn": "一叠草稿纸",
                "item_kind": "paper",
                "paper_like": True,
                "material_units": 4,
                "target_parent_id": "home_staging_corner",
            },
            {
                "op": "SPLIT_MATERIAL",
                "object_id": "paper_stock_01",
                "take_units": 1,
                "output_object_id": "card_01",
                "output_label_cn": "一张裁好的卡纸",
                "target_parent_id": "home_staging_corner",
                "item_kind": "paper",
                "paper_like": True,
            },
            {
                "op": "TRANSCRIBE_TO_PAPER",
                "object_id": "card_01",
                "artifact_id": "draft://sec",
                "version_id": "v3",
                "snapshot_excerpt_cn": excerpt,
            },
            {
                "op": "DELIVER_CARD_TO_PUBLIC_BOARD",
                "object_id": "card_01",
                "target_board": "public_notice_board",
            },
        ])
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["steps_applied"], 4)

        delivered = receipt["step_results"][3]
        self.assertEqual(delivered["target_board"], "public_notice_board")
        self.assertEqual(delivered["version_id"], "v3")
        self.assertEqual(delivered["snapshot_excerpt_cn"], excerpt)
        self.assertFalse(delivered["private_draft_exposed"])
        self.assertTrue(delivered["returned_out"])

        # The physical card has left home custody for the board.
        self.assertNotIn("card_01", self._active_objects(T_OPEN))
        raw_card = self._raw_object("card_01")
        self.assertTrue(raw_card["returned_out"])
        self.assertIsNone(raw_card["parent_id"])
        self.assertEqual(raw_card["delivered_to_board"], "public_notice_board")

        # The board carries exactly the frozen snapshot plus its version badge.
        notices = self._projection(T_OPEN, subject=False)["data"]["public_life"]["public_notices"]
        posted = [n for n in notices if n["notice_id"] == delivered["notice_id"]]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["body"], f"{excerpt}\n\n（草稿版本 v3）")
        self.assertNotIn("draft://", posted[0]["body"])
        self.assertNotIn("private_draft", posted[0]["body"])

    # -- 4. quiet room stays silent --------------------------------------

    def test_quiet_room_summary_stays_silent(self) -> None:
        (self.root / "world" / "v08_microzone_state.json").write_text("{}", encoding="utf-8")

        absent_root = Path(tempfile.mkdtemp(prefix="v08ch42-absent-"))
        (absent_root / "phone").mkdir()
        (absent_root / "world").mkdir()

        quiet = view.summarize_living_traces_for_context(
            self.root, include_private=True, now_iso=T_OPEN
        )
        absent = view.summarize_living_traces_for_context(
            absent_root, include_private=True, now_iso=T_OPEN
        )
        self.assertEqual(quiet["matter_hints"], [])
        self.assertEqual(quiet["matter_hints"], absent["matter_hints"])

        # The read views still project the cue lists -- only the summary is silent.
        space = engine.read_matter_space_view(self.root, now_iso=T_OPEN)
        self.assertIn("workspace_scaffolds", space)
        self.assertIn("familiar_absence_cues", space)
        code, envelope = view.read_v08_living_traces_view(
            self.root, has_subject_capability=True, now_iso=T_OPEN
        )
        self.assertEqual(code, 200)
        self.assertIn("workspace_scaffolds", envelope["data"]["matter_space"])


if __name__ == "__main__":
    unittest.main()
