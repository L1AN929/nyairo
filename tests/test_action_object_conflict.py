#!/usr/bin/env python3
"""The two existing rules scene 6 relies on: AR-1 flags an object re-check conflict, and
LR-5 refuses to complete an Activity whose completion contract has a bound conflict.

These are unit-level facts about rules the tree already owns, not a new protocol:

* an AR-1 ``FILE_ACTION`` settlement sees an executor success receipt *and* an object
  re-check whose hash disagrees, and answers ``CONFLICT`` / ``UNKNOWN_EFFECT`` -- this is the
  formal place a second observation enters (``additional_evidences``);
* a CONFLICT progress evidence bound to the Activity makes
  ``NaturalCompletionEvaluator.evaluate_completion`` answer ``CONFLICT`` with
  ``CONFLICTING_EVIDENCE_BLOCKS_COMPLETION``;
* that rule can only fire for an Activity that owns a real CompletionContract (without one
  the evaluator answers ``NO_AUTOMATIC_COMPLETION_WITHOUT_CONTRACT``);
* the production closeout shape really carries such a contract, and the progress read
  projection exposes the evaluation reason/evidence refs.

The end-to-end chain (real owner archive, real receipt, Activity stays ACTIVE) is exercised by
``scripts/action_object_conflict_demo.py``; keeping it out of here keeps this suite fast.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_progress_completion_lr5 as lr5  # noqa: E402
import artifact_closeout  # noqa: E402
import result_settlement_ar1 as ar1  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _contract() -> lr5.CompletionContract:
    return lr5.CompletionContract(
        contract_id="ccont:%s" % hashlib.sha256(b"object-check-unit").hexdigest()[:16],
        activity_id="actv_unit",
        criteria_kind=lr5.CRITERIA_ACTION_RESULT_MATCH,
        target_refs=["artifact:unit"],
        criteria={"required_predicate": "artifact_written_verified",
                  "expected_artifact_ref": "artifact:unit"},
        required_evidence_kinds=[lr5.SOURCE_KIND_SETTLED_EVENT],
        required_authority_domains=["result_settlement_authority"],
        created_at=_now(),
        policy_version=lr5.POLICY_ARTIFACT_V1)


def _conflict_evidence() -> dict:
    return lr5.LifeProgressEvidence(
        progress_evidence_id="pevid:%s" % hashlib.sha256(b"unit-conflict").hexdigest()[:16],
        source_kind=lr5.SOURCE_KIND_SETTLED_EVENT,
        source_ref="srev:unit-conflict",
        authority_domain="result_settlement_authority",
        epistemic_kind=lr5.EPISTEMIC_CONFLICTED_CLAIM,
        settlement_ref="rset:unit-conflict",
        settlement_status=lr5.SETTLEMENT_STATUS_CONFLICT,
        outcome_kind=lr5.OUTCOME_UNKNOWN_EFFECT,
        activity_id="actv_unit",
        target_ref="artifact:unit",
        occurred_at=_now(), observed_at=_now(), recorded_at=_now(),
        idempotency_key="idem_pevid:unit-conflict").to_dict()


def _settlement_ev(evidence_id: str, *, grade: str, verified: bool,
                   expected: str | None, observed: str | None,
                   details: dict | None = None) -> ar1.SettlementEvidenceInput:
    return ar1.SettlementEvidenceInput(
        evidence_id=evidence_id,
        root_evidence_id="rootev:%s" % hashlib.sha256(evidence_id.encode()).hexdigest()[:16],
        action_id="actn:unit",
        source_ref="artifact_authority:%s" % evidence_id,
        authority_domain="artifact_authority",
        evidence_kind="filesystem_observed",
        authority_grade=grade,
        claim_kind="observer:artifact_observer",
        verified=verified,
        occurred_at=_now(), observed_at=_now(), recorded_at=_now(),
        target_ref="artifact:unit",
        expected_hash=expected, observed_hash=observed,
        details=dict(details or {}))


class ConflictBlocksCompletionTest(unittest.TestCase):
    def test_a_bound_conflict_blocks_completion_by_name(self):
        result = lr5.NaturalCompletionEvaluator.evaluate_completion(
            activity={"revision": 1}, completion_contract=_contract().to_dict(),
            active_evidences=[_conflict_evidence()], current_checkpoint_ref=None)
        self.assertEqual(result["eval_status"], lr5.EVAL_CONFLICT, result)
        self.assertEqual(result["reason"], "CONFLICTING_EVIDENCE_BLOCKS_COMPLETION", result)

    def test_without_a_contract_the_rule_is_not_evaluated(self):
        result = lr5.NaturalCompletionEvaluator.evaluate_completion(
            activity={"revision": 1}, completion_contract=None,
            active_evidences=[_conflict_evidence()], current_checkpoint_ref=None)
        self.assertEqual(result["reason"], "NO_AUTOMATIC_COMPLETION_WITHOUT_CONTRACT", result)
        self.assertNotEqual(result["eval_status"], lr5.EVAL_CONFLICT, result)


class ActionObjectRecheckConflictTest(unittest.TestCase):
    """AR-1 is the formal intake for a second observation of the Action's object."""

    def _evaluate(self, evidences):
        registry = ar1.SettlementPolicyRegistry()
        action = {"action_id": "actn:unit", "action_kind": ar1.ACTION_KIND_FILE,
                  "status": ar1.ACTION_STATUS_SUCCEEDED, "frozen_payload": {}}
        return registry.evaluate(
            action=action, attempts=[], evidences=evidences,
            settlement_id="rset:unit-eval", recorded_at=_now())

    def test_success_receipt_plus_a_conflicting_recheck_answers_conflict(self):
        success = _settlement_ev(
            "rechk:executor", grade=ar1.AUTH_GRADE_EXECUTOR_RECEIPT, verified=True,
            expected="sha256:same", observed="sha256:same")
        mismatch = _settlement_ev(
            "rechk:observer", grade=ar1.AUTH_GRADE_OBSERVER_VERIFIED, verified=False,
            expected="sha256:expected", observed="sha256:different",
            details={"hash_mismatch": True})
        result = self._evaluate([success, mismatch])
        self.assertEqual(result["settlement_status"], ar1.SETTLEMENT_STATUS_CONFLICT, result)
        self.assertEqual(result["outcome_kind"], ar1.OUTCOME_UNKNOWN_EFFECT, result)

    def test_a_matching_recheck_settles_success(self):
        success = _settlement_ev(
            "rechk:executor2", grade=ar1.AUTH_GRADE_EXECUTOR_RECEIPT, verified=True,
            expected="sha256:same", observed="sha256:same")
        observer_ok = _settlement_ev(
            "rechk:observer2", grade=ar1.AUTH_GRADE_OBSERVER_VERIFIED, verified=True,
            expected="sha256:same", observed="sha256:same")
        result = self._evaluate([success, observer_ok])
        self.assertNotEqual(result["settlement_status"], ar1.SETTLEMENT_STATUS_CONFLICT, result)


class CloseoutContractShapeTest(unittest.TestCase):
    def test_the_production_closeout_shape_really_carries_the_contract(self):
        spec = artifact_closeout.build_closeout_adoption_spec("artifact:unit")
        contract = dict(spec.get("completion_contract_spec") or {})
        self.assertEqual(contract.get("criteria_kind"), "ACTION_RESULT_MATCH", contract)
        self.assertEqual(contract.get("criteria", {}).get("required_predicate"),
                         artifact_closeout.CLOSEOUT_SETTLED_PREDICATE, contract)
        self.assertIn("SETTLED_RESULT_EVENT", contract.get("required_evidence_kinds") or [])
        self.assertIn("result_settlement_authority",
                      contract.get("required_authority_domains") or [])


class ProgressReadProjectionTest(unittest.TestCase):
    def test_the_progress_read_carries_the_evaluation_reason_keys(self):
        """The projection is a pure read of what the evaluator already returned."""
        import inspect

        source = inspect.getsource(lr5)
        self.assertIn('"completion_reason": comp_eval.get("reason")', source)
        self.assertIn('"completion_evidence_refs": list(', source)


if __name__ == "__main__":
    unittest.main()
