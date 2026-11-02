"""S04 F-049/F-050/F-051 投影层回归（不依赖 owner 服务）。

只验证 ``decision_review`` 的读行为：已记录项照实出来、缺失项 ``NOT_RECORDED``、
rationale 默认扣留、Decision/Adoption 三态分开、复核只经调用方给的正式读口。
真实 AG-1/AG-2/Activity 链由 ``scripts/s04_decision_review_demo.py`` 覆盖。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import decision_review as dr  # noqa: E402

CREATED = "2026-10-27T10:00:00+00:00"


def _decision(decision: str = "DEFER", *, rationale=None, selected=None) -> dict:
    return ag1.DecisionRecord(
        decision_id="dec:abc123",
        revision=1,
        subject_id="chiyo.global",
        decision_opportunity_ref="dopp:abc123",
        candidate_set_ref="cset:abc123",
        candidate_refs=("cand:a", "cand:b", "cand:c"),
        context_snapshot_ref="dctx:abc123",
        hard_constraint_result_refs=(),
        decision=decision,
        reason_codes=(ag1.REASON_TIMING_NOT_APPROPRIATE,),
        decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
        policy_version="v1",
        snapshot_fingerprint="sfp:abc123",
        created_at=CREATED,
        selected_candidate_ref=selected,
        deferred_candidate_refs=("cand:b",) if decision == "DEFER" else (),
        short_rationale=rationale,
    ).to_dict()


class _FakeDecisionRead:
    def __init__(self, records: dict, dispositions: dict) -> None:
        self._records = records
        self._dispositions = dispositions
        self.store = self

    def load_state(self, **_kwargs):
        return {"decisions_by_id": dict(self._records)}

    def get_decision(self, decision_id):
        return self._records.get(decision_id)

    def get_candidate_dispositions(self, decision_id):
        return dict(self._dispositions.get(decision_id) or {})


class _FakeIntegrationRead:
    def __init__(self, state: dict) -> None:
        self._state = state

    def read_state(self, **_kwargs):
        return dict(self._state)


DISPOSITIONS = {
    "dec:abc123": {
        "cand:a": {"candidate_id": "cand:a", "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
                   "source_kind": "WORLD", "source_ref": "world:spot",
                   "disposition": "NOT_SELECTED", "reason_codes": ["TIMING_NOT_APPROPRIATE"],
                   "executability": "EXEC_NOW"},
        "cand:b": {"candidate_id": "cand:b", "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
                   "source_kind": "WORLD", "source_ref": "world:other",
                   "disposition": "DEFERRED", "reason_codes": ["TIMING_NOT_APPROPRIATE"],
                   "executability": "EXEC_NOT_NOW"},
        "cand:c": {"candidate_id": "cand:c", "candidate_kind": "CONTINUE_CURRENT",
                   "source_kind": "CURRENT_ACTIVITY", "source_ref": "actv:x",
                   "disposition": "HARD_REJECTED", "reason_codes": ["PERMISSION_DENIED"],
                   "executability": None},
    }
}


class SummaryTest(unittest.TestCase):
    def test_missing_decision_is_named_not_invented(self):
        read = _FakeDecisionRead({}, {})
        out = dr.summarize_decision(read, _FakeIntegrationRead({}), "dec:nope")
        self.assertEqual(out["status"], dr.REVIEW_NOT_FOUND)
        self.assertFalse(out["formed_decision"])

    def test_unreadable_disposition_table_does_not_become_an_invented_alternative(self):
        dec = _decision()
        read = _FakeDecisionRead({"dec:abc123": dec}, {})
        # 处置表读不到：摘要仍然 OK，但未选项明确为空，而不是编。
        out = dr.summarize_decision(
            read, _FakeIntegrationRead({"adoption_id_by_decision": {}, "adoptions_by_id": {}}),
            "dec:abc123")
        self.assertEqual(out["status"], "OK")
        self.assertFalse(out["alternatives_recorded"])
        self.assertEqual(out["alternatives"], [])

    def test_recorded_alternatives_come_from_the_persisted_disposition_table(self):
        dec = _decision()
        read = _FakeDecisionRead({"dec:abc123": dec}, DISPOSITIONS)
        out = dr.summarize_decision(
            read, _FakeIntegrationRead({"adoption_id_by_decision": {}, "adoptions_by_id": {}}),
            "dec:abc123")
        self.assertEqual(len(out["alternatives"]), 3)
        self.assertEqual(len(out["deferred"]), 1)
        self.assertEqual(len(out["hard_rejected"]), 1)
        self.assertEqual(out["deferred"][0]["candidate_ref"], "cand:b")
        self.assertEqual(out["deferred"][0]["disposition"], "DEFERRED")

    def test_missing_rationale_is_not_recorded_and_text_is_withheld_by_default(self):
        dec = _decision(rationale="合成理由：这一轮先不动。")
        read = _FakeDecisionRead({"dec:abc123": dec}, DISPOSITIONS)
        out = dr.summarize_decision(
            read, _FakeIntegrationRead({"adoption_id_by_decision": {}, "adoptions_by_id": {}}),
            "dec:abc123")
        self.assertTrue(out["rationale"]["recorded"])
        self.assertTrue(out["rationale"]["withheld"])
        self.assertNotIn("text", out["rationale"])
        self.assertIn("扣留", out["summary"])

        shown = dr.summarize_decision(
            read, _FakeIntegrationRead({"adoption_id_by_decision": {}, "adoptions_by_id": {}}),
            "dec:abc123", include_rationale=True)
        self.assertEqual(shown["rationale"]["text"], "合成理由：这一轮先不动。")

    def test_decision_and_adoption_are_reported_separately(self):
        dec = _decision()
        read = _FakeDecisionRead({"dec:abc123": dec}, DISPOSITIONS)
        # 1) 从未采用
        not_adopted = dr.summarize_decision(
            read, _FakeIntegrationRead({"adoption_id_by_decision": {}, "adoptions_by_id": {}}),
            "dec:abc123")
        self.assertEqual(not_adopted["decision_state"], dr.STATE_DECIDED_NOT_ADOPTED)
        self.assertEqual(not_adopted["adoption"]["status"], "NOT_ADOPTED")
        # 2) 采纳被拒
        rejected = dr.summarize_decision(
            read, _FakeIntegrationRead({
                "adoption_id_by_decision": {"dec:abc123": "adp:1"},
                "adoptions_by_id": {"adp:1": {
                    "adoption_id": "adp:1", "decision_ref": "dec:abc123",
                    "adoption_status": "REJECTED_STALE",
                    "rejection_reason_code": "STALE_ACTIVITY_REVISION:0->1",
                    "created_activity_ref": None, "target_activity_ref": None,
                    "adopted_at": None, "recorded_at": CREATED, "revision": 1,
                    "decision_revision": 1}}}),
            "dec:abc123")
        self.assertEqual(rejected["decision_state"], dr.STATE_DECIDED_ADOPTION_REJECTED)
        self.assertEqual(rejected["adoption"]["rejection_reason_code"],
                         "STALE_ACTIVITY_REVISION:0->1")
        # 3) 已采纳
        adopted = dr.summarize_decision(
            read, _FakeIntegrationRead({
                "adoption_id_by_decision": {"dec:abc123": "adp:2"},
                "adoptions_by_id": {"adp:2": {
                    "adoption_id": "adp:2", "decision_ref": "dec:abc123",
                    "adoption_status": "APPLIED", "created_activity_ref": "actv:new",
                    "adopted_at": CREATED, "recorded_at": CREATED, "revision": 1,
                    "decision_revision": 1}}}),
            "dec:abc123")
        self.assertEqual(adopted["decision_state"], dr.STATE_DECIDED_AND_ADOPTED)
        self.assertEqual(adopted["adoption"]["activity_ref"], "actv:new")

    def test_unreadable_adoption_state_is_unknown_not_absent(self):
        dec = _decision()
        read = _FakeDecisionRead({"dec:abc123": dec}, DISPOSITIONS)

        class _Broken:
            def read_state(self, **_kwargs):
                raise OSError("boom")

        out = dr.summarize_decision(read, _Broken(), "dec:abc123")
        self.assertEqual(out["adoption"]["status"], "UNKNOWN")
        self.assertEqual(out["decision_state"], "ADOPTION_STATE_UNKNOWN")


class ReviewTest(unittest.TestCase):
    def test_without_a_formal_reader_it_says_not_evaluated(self):
        read = _FakeDecisionRead({"dec:abc123": _decision()}, DISPOSITIONS)
        out = dr.review_live_applicability(read, "dec:abc123")
        self.assertEqual(out["status"], dr.REVIEW_NOT_EVALUATED)
        self.assertEqual(out["reason_code"], "NO_LIVE_ADMISSION_READER")

    def test_the_callers_formal_validator_decides_and_is_never_copied(self):
        read = _FakeDecisionRead({"dec:abc123": _decision()}, DISPOSITIONS)
        seen = {}

        def validator(decision, *, check_at=None):
            seen["decision_id"] = decision.decision_id
            seen["check_at"] = check_at
            return False, "FOREGROUND_ACTIVITY_CHANGED:None->actv:1"

        out = dr.review_live_applicability(
            read, "dec:abc123", validator=validator, check_at=CREATED)
        self.assertEqual(out["status"], dr.REVIEW_REJECTED)
        self.assertEqual(out["reason_code"], "FOREGROUND_ACTIVITY_CHANGED:None->actv:1")
        self.assertEqual(seen["decision_id"], "dec:abc123")
        self.assertEqual(seen["check_at"], CREATED)


class ListingTest(unittest.TestCase):
    def test_listing_is_bounded_and_carries_no_body(self):
        records = {
            "dec:a": _decision(rationale="一句合成理由"),
            "dec:b": _decision(),
        }
        read = _FakeDecisionRead(records, {})
        rows = dr.list_recent_decisions(read, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("short_rationale", rows[0])
        self.assertTrue(rows[0]["has_rationale"])


if __name__ == "__main__":
    unittest.main()
