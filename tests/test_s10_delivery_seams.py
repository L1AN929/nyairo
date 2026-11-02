"""S10 F113-F115: accepted-then-lost is UNKNOWN, layered, and stage-named."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTACT = ROOT / "components" / "alpha" / "chiyo" / "contact"
if str(CONTACT) not in sys.path:
    sys.path.insert(0, str(CONTACT))

from contact_receipt_model import ContactTransportReceiptEvidence  # noqa: E402
from contact_receipt_store import ContactReceiptStore  # noqa: E402
from contact_reconciliation import ContactReconciliationAuthority  # noqa: E402
from contact_result_projection import ContactActionResultProjector  # noqa: E402

NOW = "2026-10-27T12:00:00Z"
ACTION = "actn:" + "a" * 40
KEY = "ar0_act:" + "b" * 40
PAYLOAD_HASH = hashlib.sha256(b"payload").hexdigest()
CONTENT_HASH = hashlib.sha256(b"content").hexdigest()


def _evidence(kind: str, status: str, *, effect: bool, count: int,
              action: str = ACTION, key: str = KEY) -> ContactTransportReceiptEvidence:
    return ContactTransportReceiptEvidence.create(
        action_ref=action, submission_key=key, namespace="sandbox.telegram",
        transport_record_ref="tlog:" + hashlib.sha256((action + kind).encode()).hexdigest(),
        observation_kind=kind, provider_status=status,
        recipient_ref="recipient:local", channel="channel:local_inbox",
        payload_hash=PAYLOAD_HASH, content_hash=CONTENT_HASH,
        observed_at=NOW, recorded_at=NOW,
        provenance_refs=("source:test", "request:mreq-test"),
        effect_applied=effect, effect_count_for_key=count)


class UnknownStageReasonTest(unittest.TestCase):
    def test_project_kinds_names_the_stage_without_raising_the_state(self) -> None:
        authority = ContactReconciliationAuthority._project_kinds
        cases = [
            (frozenset({"PROVIDER_ACCEPTED", "STATUS_UNKNOWN"}), "UNKNOWN_AFTER_ACCEPT"),
            (frozenset({"PROVIDER_ACCEPTED", "TRANSPORT_ERROR"}), "UNKNOWN_AFTER_ACCEPT"),
            (frozenset({"SUBMIT_OBSERVED", "DELIVERY_FAILED"}), "UNKNOWN_AFTER_SUBMIT"),
            (frozenset({"STATUS_UNKNOWN"}), "UNKNOWN_PROVIDER_STATE"),
            (frozenset({"TRANSPORT_ERROR"}), "UNKNOWN_PROVIDER_STATE"),
        ]
        for kinds, reason in cases:
            with self.subTest(kinds=sorted(kinds)):
                state, inferred = authority(kinds, False)
                self.assertEqual(state, "UNKNOWN")
                self.assertEqual(inferred, reason)

    def test_non_unknown_states_keep_their_reasons(self) -> None:
        authority = ContactReconciliationAuthority._project_kinds
        self.assertEqual(authority(frozenset({"PROVIDER_ACCEPTED"}), False),
                         ("ACKNOWLEDGED", "EVIDENCE_OBSERVED"))
        self.assertEqual(authority(frozenset({"FAILED_BEFORE_ACCEPT"}), False),
                         ("FAILURE", "EVIDENCE_OBSERVED"))
        self.assertEqual(
            authority(frozenset({"DELIVERY_CONFIRMED", "DELIVERY_FAILED"}), False),
            ("CONFLICT", "CONTRADICTORY_EVIDENCE"))


class AcceptedThenLostTest(unittest.TestCase):
    def _chain(self, *rows):
        store = ContactReceiptStore()
        for row in rows:
            store.append(row)
        record = ContactReconciliationAuthority(store=store).reconcile(
            action_ref=ACTION, recorded_at=NOW)
        projection = ContactActionResultProjector(store=store).project(
            action_ref=ACTION, reconciliations=(record,))
        return store, record, projection

    def test_lost_answer_after_accept_is_unknown_and_stage_named(self) -> None:
        store, record, projection = self._chain(
            _evidence("PROVIDER_ACCEPTED", "ACKNOWLEDGED", effect=True, count=1),
            _evidence("STATUS_UNKNOWN", "UNKNOWN", effect=True, count=1))
        self.assertEqual(projection.current_state, "UNKNOWN")
        self.assertTrue(projection.unknown)
        self.assertIsNone(projection.failure)
        self.assertTrue(projection.submitted)
        self.assertTrue(projection.acknowledged)
        self.assertIsNone(projection.delivered)
        # The stage reason is the reconciliation authority's own output.
        self.assertEqual(record.state, "UNKNOWN")
        self.assertEqual(record.reason, "UNKNOWN_AFTER_ACCEPT")

        # The projection shape is unchanged: no reason key leaks into handoff facts.
        facts = ContactActionResultProjector(store=store).handoff_facts(projection)
        self.assertNotIn("reconciliation_reason", facts)
        self.assertEqual(facts["state"], "UNKNOWN")
        self.assertIsNone(facts["delivered"])

    def test_submit_observed_then_lost_is_unknown_after_submit(self) -> None:
        _store, record, projection = self._chain(
            _evidence("SUBMIT_OBSERVED", "SUBMITTED", effect=True, count=1),
            _evidence("TRANSPORT_ERROR", "UNKNOWN", effect=False, count=0))
        self.assertEqual(projection.current_state, "UNKNOWN")
        self.assertEqual(record.reason, "UNKNOWN_AFTER_SUBMIT")
        self.assertTrue(projection.submitted)
        self.assertIsNone(projection.acknowledged)

    def test_pre_accept_failure_is_the_only_failure(self) -> None:
        _store, record, projection = self._chain(
            _evidence("FAILED_BEFORE_ACCEPT", "FAILED_BEFORE_ACCEPT", effect=False, count=0))
        self.assertEqual(projection.current_state, "FAILURE")
        self.assertEqual(record.state, "FAILURE")
        self.assertTrue(projection.failure)
        self.assertFalse(projection.submitted)
        self.assertFalse(projection.delivered)

    def test_positive_delivery_plus_failure_is_conflict(self) -> None:
        _store, record, projection = self._chain(
            _evidence("DELIVERY_CONFIRMED", "DELIVERED", effect=True, count=1),
            _evidence("DELIVERY_FAILED", "UNKNOWN", effect=True, count=1))
        self.assertEqual(projection.current_state, "CONFLICT")
        self.assertEqual(record.state, "CONFLICT")
        self.assertTrue(projection.delivered)


if __name__ == "__main__":
    unittest.main()
