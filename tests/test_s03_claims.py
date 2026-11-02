#!/usr/bin/env python3
"""S03 claim lifecycle: protocol and persistence, without a World or a model."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import s03_claims as s03  # noqa: E402

CTX = {"local_date": "2026-11-20", "timezone": "Asia/Tokyo"}
NOW = "2026-11-20T09:00:00+00:00"


class S03ClaimLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db = Path(self._dir.name) / "claims.sqlite3"
        self.service = s03.S03ClaimService.initialize(self.db, subject="chiyo")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _report(self, source: str, value: str, *, scope: str = "bookstore",
                key: str | None = None, **kwargs):
        return self.service.report(
            predicate_id="place_closing_time", scope_id=scope, value=value,
            context=CTX, source_ref=source, now=NOW,
            operation_key=key or ("op:" + source + ":" + value), **kwargs)

    def _status(self, now: str = NOW) -> dict:
        return {(row["predicate"], row["scope_id"]): row["status"]
                for row in self.service.current_view(now=now)["claims"]}

    def test_unknown_slot_is_not_false(self) -> None:
        answer = self.service.slot_status(
            predicate_id="place_closing_time", scope_id="nowhere", context=CTX, now=NOW)
        self.assertEqual(answer["status"], s03.STATUS_UNKNOWN)
        self.assertEqual(answer["reason_code"], s03.REASON_NO_SLOT)

    def test_conflict_is_side_by_side_and_context_keeps_them_apart(self) -> None:
        self._report("operator_report:a", "22:00")
        self._report("operator_report:b", "20:00")
        view = self.service.current_view(now=NOW)
        self.assertEqual({row["status"] for row in view["claims"]},
                         {s03.STATUS_DISPUTED})
        self.assertEqual(view["conflicts"][0]["distinct_answers"], 2)

        # A different operating day is a different slot: no false conflict.
        self.service.report(
            predicate_id="place_closing_time", scope_id="bookstore", value="20:00",
            context={"local_date": "2026-11-21", "timezone": "Asia/Tokyo"},
            source_ref="operator_report:a", now=NOW, operation_key="op:next-day")
        next_day = self.service.slot_status(
            predicate_id="place_closing_time", scope_id="bookstore",
            context={"local_date": "2026-11-21", "timezone": "Asia/Tokyo"}, now=NOW)
        self.assertEqual(next_day["status"], s03.STATUS_SUPPORTED)

    def test_correction_supersedes_and_keeps_history(self) -> None:
        self._report("operator_report:a", "22:00")
        self._report("operator_report:b", "20:00")
        corrected = self.service.correct_source(
            source_ref="operator_report:b", now=NOW, operation_key="op:correct",
            replacement={"value": "22:00"})
        self.assertEqual(corrected["status"], "CORRECTED")
        view = self.service.current_view(now=NOW)
        self.assertNotIn({"time": "20:00"},
                         [row["object"] for row in view["claims"]])
        history = self.service.history(slot_id=view["slots"][0]["slot_id"], now=NOW)
        old = [row for row in history["claims"] if row["object"] == {"time": "20:00"}]
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0]["status"], s03.STATUS_SUPERSEDED)
        self.assertTrue(old[0]["superseded_by"])

    def test_inference_binds_evidence_and_follows_its_basis(self) -> None:
        self._report("operator_report:a", "22:00")
        view = self.service.current_view(now=NOW)
        basis = view["claims"][0]["claim_id"]
        self.service.infer(
            predicate_id="place_likely_open", scope_id="bookstore", value=True,
            basis_claim_ids=[basis], context=CTX, now=NOW, operation_key="op:infer")
        self.assertEqual(self._status()[("place_likely_open", "bookstore")],
                         s03.STATUS_SUPPORTED)
        self.service.maintenance(now="2026-11-21T10:00:00+00:00")
        self.assertEqual(self._status("2026-11-21T10:00:00+00:00")
                         [("place_likely_open", "bookstore")], s03.STATUS_STALE)

    def test_staleness_is_explicit_and_deadline_not_reset(self) -> None:
        self._report("operator_report:a", "22:00")
        later = "2026-11-21T10:00:00+00:00"
        marked = self.service.maintenance(now=later)
        self.assertEqual(len(marked["marked_stale"]), 1)
        self.assertEqual(self._status(later)[("place_closing_time", "bookstore")],
                         s03.STATUS_STALE)
        self.assertEqual(self.service.maintenance(now=later)["count"], 0)

    def test_retraction_withholds_payload_everywhere(self) -> None:
        self._report("operator_report:a", "22:00")
        self.service.retract_source(source_ref="operator_report:a", now=NOW,
                                    operation_key="op:retract")
        view = self.service.current_view(now=NOW)
        row = view["claims"][0]
        self.assertEqual(row["status"], s03.STATUS_UNKNOWN)
        self.assertIsNone(row["object"])
        self.assertTrue(row["withheld"])
        history = self.service.history(slot_id=view["slots"][0]["slot_id"], now=NOW)
        self.assertIsNone(history["claims"][0]["object"])
        self.assertTrue(history["claims"][0]["withheld"])

    def test_idempotency_and_source_guards(self) -> None:
        first = self._report("operator_report:a", "22:00", key="op:once")
        again = self._report("operator_report:a", "22:00", key="op:once")
        self.assertEqual(again["status"], "ALREADY_APPLIED")
        self.assertEqual(self.service.current_view(now=NOW)["counts"]["claims"], 1)
        with self.assertRaises(s03.S03ClaimsRefused) as caught:
            self._report("world:coastal_town", "22:00", key="op:fake")
        self.assertEqual(caught.exception.reason_code,
                         "S03_REPORT_SOURCE_NOT_EXPLICIT")
        with self.assertRaises(s03.S03ClaimsRefused) as caught:
            self.service.report(predicate_id="place_closing_time", scope_id="bookstore",
                                value="21:00", context=CTX,
                                source_ref="operator_report:a", now=NOW,
                                operation_key="op:change-without-correction")
        self.assertEqual(caught.exception.reason_code,
                         "S03_SOURCE_VALUE_CHANGE_REQUIRES_CORRECTION")

    def test_multi_value_members_coexist(self) -> None:
        for item in ("item:tea", "item:coffee"):
            self.service.report(
                predicate_id="place_sells_item", scope_id="cafe", value=item,
                source_ref="operator_report:menu:" + item.split(":")[1], now=NOW,
                operation_key="op:menu:" + item)
        status = self._status()
        self.assertEqual(status[("place_sells_item", "cafe")], s03.STATUS_SUPPORTED)
        objects = [row["object"] for row in self.service.current_view(now=NOW)["claims"]]
        self.assertIn({"item_ref": "item:tea"}, objects)
        self.assertIn({"item_ref": "item:coffee"}, objects)

    def test_rebuild_matches_materialized_tables(self) -> None:
        self._report("operator_report:a", "22:00")
        self._report("operator_report:b", "20:00")
        self.service.correct_source(
            source_ref="operator_report:b", now=NOW, operation_key="op:correct",
            replacement={"value": "22:00"})
        view = self.service.current_view(now=NOW)
        self.service.infer(
            predicate_id="place_likely_open", scope_id="bookstore", value=True,
            basis_claim_ids=[view["claims"][0]["claim_id"]], context=CTX, now=NOW,
            operation_key="op:infer")
        self.service.maintenance(now="2026-11-21T10:00:00+00:00")
        rebuilt = self.service.rebuild()
        self.assertTrue(rebuilt["matches"], rebuilt)
        self.assertGreaterEqual(rebuilt["claims"], 3)

    def test_reopening_the_same_root_preserves_state(self) -> None:
        self._report("operator_report:a", "22:00")
        self.service.maintenance(now="2026-11-21T10:00:00+00:00")
        reopened = s03.S03ClaimService(str(self.db), subject="chiyo")
        view = reopened.current_view(now="2026-11-21T10:00:00+00:00")
        self.assertEqual(view["claims"][0]["status"], s03.STATUS_STALE)
        self.assertGreaterEqual(view["claims"][0]["revision"], 2)


if __name__ == "__main__":
    unittest.main()
