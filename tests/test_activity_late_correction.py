#!/usr/bin/env python3
"""Scene 7 wiring: a late correction on a COMPLETED Activity.

What this file pins down (and nothing more)
-------------------------------------------
Two narrow facts belong to LR-5 itself and are cheap to pin here; the end-to-end behaviour
(real Supply owner, real AG-1/AG-2/AR-0/AR-1/LR-5, real CLI, same-root restart) is covered by
``scripts/activity_late_correction_demo.py``:

1. a binding that admits ``SETTLED_RESULT_EVENT`` also admits the AR-1
   ``SETTLED_RESULT_CORRECTION_EVENT`` for the same Action -- otherwise a real correction is
   dropped as ``SOURCE_KIND_NOT_ALLOWED_BY_BINDING`` before the terminal handler sees it;
2. a correction may only be attached to a COMPLETED Activity when its supersession chain
   reaches that Activity's own completion settlement.  An unprovable basis and an unrelated
   settlement are named refusals; neither records a conflict.

The cases are pure: no store, no lease, no model, no network, no clock.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_activity_late_correction.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts")]

import activity_progress_completion_lr5 as lr5  # noqa: E402

_TS = "2026-10-29T10:00:00Z"


def _evidence(**overrides):
    fields = dict(
        progress_evidence_id="pevid:late1",
        source_kind=lr5.SOURCE_KIND_CORRECTION_EVENT,
        source_ref="scor:late1",
        authority_domain="result_settlement_authority",
        epistemic_kind=lr5.EPISTEMIC_CONFLICTED_CLAIM,
        occurred_at=_TS, observed_at=_TS, recorded_at=_TS,
        idempotency_key="idem:late1",
        activity_id="actv_1",
        target_ref="artifact:a",
        settlement_ref="rset:new",
        supersedes_settlement_ref="rset:base",
    )
    fields.update(overrides)
    return lr5.LifeProgressEvidence(**fields)


def _binding(**overrides):
    binding = {
        "activity_id": "actv_1",
        "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"],
        "authority_domains": ["result_settlement_authority", "artifact_authority"],
        "target_refs": ["artifact:a"],
        "correlation_ids": [],
    }
    binding.update(overrides)
    return binding


class _Probe(lr5.ActivityProgressCoordinator):
    """Only the read port the relation check uses; no stores, lease or capability."""

    def __init__(self, settlement_read=None):
        self.settlement_read = settlement_read


class _Settlements:
    def __init__(self, rows):
        self._rows = dict(rows)

    def get_settlement_by_id(self, settlement_id):
        return self._rows.get(settlement_id)


class BindingSourceKindAliasTest(unittest.TestCase):
    def test_a_settled_binding_also_admits_the_actions_correction_event(self):
        ok, reason = lr5.ActivityProgressPolicyRegistry.match_evidence_to_binding(
            evidence=_evidence(), binding=_binding())
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "BOUND_AND_MATCHED")

    def test_the_alias_does_not_admit_an_unrelated_source_kind(self):
        evidence = _evidence(source_kind=lr5.SOURCE_KIND_DOCUMENT_STATE, source_ref="doc:1")
        ok, reason = lr5.ActivityProgressPolicyRegistry.match_evidence_to_binding(
            evidence=evidence, binding=_binding())
        self.assertFalse(ok)
        self.assertEqual(reason, "SOURCE_KIND_NOT_ALLOWED_BY_BINDING")

    def test_a_correction_still_has_to_name_the_bound_target(self):
        ok, reason = lr5.ActivityProgressPolicyRegistry.match_evidence_to_binding(
            evidence=_evidence(target_ref="artifact:other"), binding=_binding())
        self.assertFalse(ok)
        self.assertEqual(reason, "IRRELEVANT_TARGET_REF")


class CompletionRelationTest(unittest.TestCase):
    def _state(self):
        return {
            "completed_once_by_activity": {"actv_1": {"eligibility_id": "celig:1"}},
            "completion_eligibilities_by_id": {
                "celig:1": {"status": "CONSUMED", "settlement_refs": ["rset:base"],
                            "evidence_refs": []},
            },
            "consumed_evidences_by_id": {},
        }

    def test_the_direct_completion_settlement_is_related(self):
        probe = _Probe()
        self.assertIsNone(probe._correction_relation_rejection(
            self._state(), {"activity_id": "actv_1"}, _evidence()))

    def test_a_correction_of_a_later_correction_is_still_related(self):
        probe = _Probe(_Settlements({"rset:mid": {"supersedes_ref": "rset:base"}}))
        evidence = _evidence(supersedes_settlement_ref="rset:mid")
        self.assertIsNone(probe._correction_relation_rejection(
            self._state(), {"activity_id": "actv_1"}, evidence))

    def test_an_unrelated_settlement_is_refused_by_name(self):
        probe = _Probe(_Settlements({"rset:elsewhere": {"supersedes_ref": None}}))
        evidence = _evidence(supersedes_settlement_ref="rset:elsewhere")
        self.assertEqual(
            probe._correction_relation_rejection(
                self._state(), {"activity_id": "actv_1"}, evidence),
            "CORRECTION_TARGETS_UNRELATED_COMPLETION_EVIDENCE")

    def test_an_unreadable_completion_basis_fails_closed(self):
        probe = _Probe()
        state = {"completed_once_by_activity": {}, "completion_eligibilities_by_id": {},
                 "consumed_evidences_by_id": {}}
        self.assertEqual(
            probe._correction_relation_rejection(
                state, {"activity_id": "actv_1", "completion_evidence_ref": "aevid:x"},
                _evidence()),
            "COMPLETION_BASIS_UNVERIFIABLE")

    def test_the_progress_evidence_route_also_resolves_the_basis(self):
        probe = _Probe()
        state = {
            "completed_once_by_activity": {},
            "completion_eligibilities_by_id": {},
            "consumed_evidences_by_id": {
                "pevid:base": {"evidence": {"settlement_ref": "rset:base",
                                            "causal_parent_refs": []}}},
        }
        activity = {"activity_id": "actv_1", "completion_evidence_ref": "pevid:base"}
        self.assertIsNone(probe._correction_relation_rejection(state, activity, _evidence()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
