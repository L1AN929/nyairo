#!/usr/bin/env python3
"""S09 read-only projection tests: recovery facts come from the LR-4 owner stores.

The full continuation chain is exercised by ``scripts/s09_continuation_demo.py``.
These cases pin the one new read surface: an OPEN eligibility is the only source of a
``RESUME`` entry; a PENDING condition is not a resume; terminal/active states are
distinct; and an unreadable store is *unknown*, never "no eligibility".
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from components.phone import own_day_read as odr


class RecoveryProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="s09-recovery-"))
        (self.root / "waiting_agenda" / "eligibility").mkdir(parents=True)
        (self.root / "waiting_agenda" / "resume_conditions").mkdir(parents=True)

    def _write(self, relative: str, payload) -> None:
        path = self.root / "waiting_agenda" / relative
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _eligibility(self, activity_id: str, *, status: str = "OPEN",
                     expires_at=None) -> None:
        self._write("eligibility/resume_eligibility_state.json", {
            "eligibilities": {
                "relig:1": {"eligibility_id": "relig:1", "activity_id": activity_id,
                            "status": status, "expires_at": expires_at,
                            "activity_revision": 3}}})

    def _condition(self, activity_id: str, *, status: str = "PENDING") -> None:
        self._write("resume_conditions/resume_conditions_state.json", {
            "conditions": {
                "rcond:1": {"condition_id": "rcond:1", "activity_id": activity_id,
                            "status": status, "waiting_on_ref": "wait_dep:artifact.x.y"}}})

    def _view(self) -> dict:
        facts, _source = odr.activity_recovery_projection(self.root)
        return facts

    def test_an_open_eligibility_is_the_only_resume_entry(self) -> None:
        self._eligibility("actv_a")
        row = odr.recovery_row({"ref": "actv_a", "status": "WAITING"}, self._view())
        self.assertEqual(row["recoverable"], True)
        self.assertEqual(row["reason_code"], odr.RECOVERY_ELIGIBILITY_OPEN)
        self.assertEqual(row["next_legal_entry"], "RESUME")
        self.assertEqual(row["eligibility_ref"], "relig:1")

    def test_a_pending_condition_is_not_a_resume(self) -> None:
        self._condition("actv_b")
        row = odr.recovery_row({"ref": "actv_b", "status": "PAUSED"}, self._view())
        self.assertEqual(row["recoverable"], False)
        self.assertEqual(row["reason_code"], odr.RECOVERY_CONDITION_PENDING)
        self.assertIsNone(row["next_legal_entry"])
        self.assertEqual(row["condition_ref"], "rcond:1")

    def test_no_open_facts_is_no_entry_not_an_invented_timer(self) -> None:
        row = odr.recovery_row({"ref": "actv_c", "status": "PAUSED"}, self._view())
        self.assertEqual(row["recoverable"], False)
        self.assertEqual(row["reason_code"], odr.RECOVERY_NO_OPEN_ELIGIBILITY)
        self.assertIsNone(row["next_legal_entry"])

    def test_terminal_and_active_are_distinct_from_recoverable(self) -> None:
        self._eligibility("actv_d")
        terminal = odr.recovery_row({"ref": "actv_c", "status": "ABANDONED"}, self._view())
        active = odr.recovery_row({"ref": "actv_d", "status": "ACTIVE"}, self._view())
        self.assertEqual(terminal["reason_code"], "TERMINAL_ABANDONED")
        self.assertIsNone(terminal["next_legal_entry"])
        self.assertEqual(active["reason_code"], odr.RECOVERY_ACTIVE)
        self.assertIsNone(active["next_legal_entry"])

    def test_an_expired_eligibility_is_not_open(self) -> None:
        self._eligibility("actv_e", expires_at="2020-01-01T00:00:00+00:00")
        row = odr.recovery_row({"ref": "actv_e", "status": "WAITING"}, self._view())
        self.assertEqual(row["reason_code"], odr.RECOVERY_NO_OPEN_ELIGIBILITY)

    def test_an_unreadable_store_is_unknown_not_none(self) -> None:
        (self.root / "waiting_agenda" / "eligibility" /
         "resume_eligibility_state.json").write_text("{not json", encoding="utf-8")
        row = odr.recovery_row({"ref": "actv_f", "status": "WAITING"}, self._view())
        self.assertIsNone(row["recoverable"])
        self.assertEqual(row["reason_code"], odr.RECOVERY_ELIGIBILITY_UNREADABLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
