#!/usr/bin/env python3
"""S10 read-surface tests: request/attempt/receipt/reconcile stay separate facts.

The full real-owner chain (formal success, lost answer -> restart -> reconcile, payload
conflict, no-mapping content) is exercised by ``scripts/s10_action_outcomes_demo.py``.
These cases pin the projection rules that make the CLI honest, plus the pure-read guard.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import action_outcome_review as review  # noqa: E402


def _action(**overrides):
    row = {
        "action_id": "actn:abc", "idempotency_key": "idem:one", "action_kind": "file",
        "adapter_kind": "supply_managed_artifact_adapter", "status": "PREPARED",
        "revision": 2, "target_ref": "artifact:work", "decision_ref": "dec:1",
        "activity_ref": "actv_1", "created_at": "2026-10-27T00:00:00+00:00",
        "updated_at": "2026-10-27T00:00:00+00:00", "prepared_at": "2026-10-27T00:00:01+00:00",
        "frozen_payload": {"op": "commit", "content": "TOP-SECRET-BODY"},
        "payload_hash": "hash", "attempt_count": 0,
        "delivery_state": "NONE",
    }
    row.update(overrides)
    return row


class ProjectionTest(unittest.TestCase):
    def test_namespace_matches_the_production_runtime(self) -> None:
        import life_runtime_production as lrp

        self.assertEqual(review.NAMESPACE_AGENCY, lrp.NAMESPACE_AGENCY)

    def test_prepared_is_not_sent_and_does_not_imply_an_attempt(self) -> None:
        view = review.project_action(_action())
        self.assertEqual(view["phase"], review.PHASE_REQUESTED_FROZEN)
        self.assertEqual(view["outcome"], review.OUTCOME_PENDING)
        self.assertEqual(view["attempts"], [])
        self.assertEqual(view["facts"]["dispatch_admitted"], review.NOT_RECORDED)
        self.assertEqual(view["facts"]["attempt_started"], review.ATTEMPT_NOT_STARTED)
        self.assertEqual(view["not_sent"]["value"], review.NOT_RECORDED)
        self.assertIsNone(view["not_sent"]["not_sent"])
        self.assertTrue(view["payload"]["frozen"])
        self.assertFalse(view["payload"]["body_included"])

    def test_payload_body_never_appears_in_the_projection(self) -> None:
        view = review.project_action(_action())
        self.assertNotIn("TOP-SECRET-BODY", json.dumps(view, ensure_ascii=False))
        self.assertNotIn("TOP-SECRET-BODY", review.render_text(
            {"ok": True, "state_root": "x", "count": 1, "actions": [view]}))

    def test_unknown_stays_unknown_and_is_never_failed(self) -> None:
        attempt = {"attempt_id": "aatt:1", "attempt_no": 1, "status": "UNKNOWN",
                   "started_at": "2026-10-27T00:00:02+00:00",
                   "submitted_at": "2026-10-27T00:00:03+00:00",
                   "transport_result": "AMBIGUOUS_POST_SUBMISSION_FAILURE",
                   "error_class": "SUPPLY_RESPONSE_LOST", "receipt_refs": []}
        view = review.project_action(
            _action(status="UNKNOWN", attempt_count=1, unknown_since="2026-10-27T00:00:04+00:00"),
            attempts=[attempt])
        self.assertEqual(view["outcome"], review.OUTCOME_UNKNOWN)
        self.assertEqual(view["phase"], review.PHASE_UNRESOLVED)
        self.assertEqual(view["facts"]["attempt_started"], review.ATTEMPT_STARTED_LOCAL)
        self.assertEqual(view["facts"]["user_received"], review.NOT_RECORDED)
        self.assertEqual(view["not_sent"]["value"], review.NOT_RECORDED)

    def test_only_a_definite_pre_submit_failure_supports_not_sent(self) -> None:
        attempt = {"attempt_id": "aatt:1", "attempt_no": 1, "status": "PRE_SUBMIT_FAILED",
                   "started_at": "2026-10-27T00:00:02+00:00",
                   "transport_result": "DEFINITE_PRE_SUBMISSION_FAILURE",
                   "error_class": "GATE_OFF", "receipt_refs": []}
        view = review.project_action(_action(status="PREPARED"), attempts=[attempt])
        self.assertEqual(view["not_sent"]["value"], "DEFINITE_PRE_SUBMIT_FAILURE")
        self.assertTrue(view["not_sent"]["not_sent"])
        self.assertEqual(view["not_sent"]["error_class"], "GATE_OFF")

    def test_accepted_is_not_delivered_or_read(self) -> None:
        receipt = {"receipt_id": "arcpt:1", "status_claim": "ACCEPTED",
                   "evidence_kind": "provider-reported", "provider": "p",
                   "authority_domain": "d", "receipt_kind": "k"}
        view = review.project_action(_action(status="ACKNOWLEDGED"), receipts=[receipt])
        self.assertEqual(view["facts"]["provider_accepted"], review.ACCEPTANCE_RECEIPT)
        self.assertEqual(view["facts"]["provider_delivered"], review.NOT_RECORDED)
        self.assertEqual(view["facts"]["provider_read"], review.NOT_RECORDED)
        self.assertEqual(view["facts"]["user_received"], review.NOT_RECORDED)

    def test_reconciled_success_keeps_the_reconciliation_row_visible(self) -> None:
        action = _action(status="SUCCEEDED", attempt_count=1,
                         unknown_since="2026-10-27T00:00:04+00:00")
        receipt = {"receipt_id": "arcpt:1", "status_claim": "SUCCEEDED"}
        evidence = {"evidence_id": "aevid:1", "observer_kind": "artifact_observer",
                    "artifact_ref": "artifact:work", "verified": True, "source_refs": ["a"]}
        recon = {"reconciliation_id": "arec:1", "strategy": "provider_lookup",
                 "result": "CONFIRMED_SUCCEEDED", "previous_status": "UNKNOWN",
                 "resolved_status": "SUCCEEDED", "evidence_refs": ["supply_op:k"]}
        view = review.project_action(action, receipts=[receipt], evidences=[evidence],
                                     reconciliations=[recon])
        self.assertEqual(view["phase"], review.PHASE_CLOSED)
        self.assertEqual(view["outcome"], review.OUTCOME_SUCCEEDED)
        self.assertTrue(view["facts"]["reconciliation_recorded"])
        self.assertEqual(len(view["reconciliation"]), 1)

    def test_cancelled_before_submit_is_not_executed(self) -> None:
        view = review.project_action(_action(status="CANCELLED"))
        self.assertEqual(view["outcome"], review.OUTCOME_NOT_EXECUTED)
        submitted = review.project_action(
            _action(status="CANCELLED", submitted_at="2026-10-27T00:00:05+00:00"))
        self.assertEqual(submitted["outcome"], review.OUTCOME_CANCELLED_AFTER_SUBMIT)


class ReadGuardTest(unittest.TestCase):
    def test_a_pending_wal_is_refused_instead_of_replayed(self) -> None:
        import action_reality_ledger as ar0

        root = Path(tempfile.mkdtemp(prefix="s10-review-wal-"))
        (root / ar0.ActionStore.WAL_FILENAME).write_text("pending\n", encoding="utf-8")
        envelope = review.build_action_review(root)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["reason"], "WAL_PENDING_RECOVERY")

    def test_named_gaps_are_reported_not_invented(self) -> None:
        gaps = review.named_gaps()
        self.assertEqual(gaps["child_dag_partial"], review.NOT_IMPLEMENTED)
        self.assertEqual(gaps["bounded_query_scheduler"], review.NOT_IMPLEMENTED)
        self.assertEqual(gaps["quiesced_unresolved"], review.NOT_IMPLEMENTED)


if __name__ == "__main__":
    unittest.main()
