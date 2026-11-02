#!/usr/bin/env python3
"""Activity FAILED / BLOCKED outcome contract (pure pieces).

The real owner chain is ``scripts/activity_outcome_demo.py``; these cases pin the gate and the
replay vocabulary without a socket, a model or an instance:

1. the canonical status sets treat FAILED as terminal and BLOCKED as open/suspended;
2. new records/transitions may use schema v2 while v1 stays readable;
3. a failure assessment must name this Activity, this revision, its own goal scope, a known
   failure criterion and owner evidence that re-reads -- a model ``verified: true`` is refused;
4. a BLOCK basis must be a structured, revision-bound owner basis;
5. deterministic replay recognises BLOCKED (releases the slot) and FAILED (terminal), and
   still rejects a bad revision chain.

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v tests.test_activity_outcome
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

import activity_continuity as ac  # noqa: E402
import activity_outcome_assessment as ao  # noqa: E402

TS = "2026-10-28T06:00:00+00:00"
ARTIFACT_ID = "0123456789abcdef0123456789abcdef"
ARTIFACT = "artifact:" + ARTIFACT_ID
ARTIFACT_VERSION = "artifact-version:fedcba9876543210fedcba9876543210"
ARTIFACT_ACTIVE = "artifact:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
#: A different piece owned by another Activity.  It is archived, but it is *not* A's goal.
ARTIFACT_OTHER_ID = "88888888888888888888888888888888"
ARTIFACT_OTHER = "artifact:" + ARTIFACT_OTHER_ID
ACTIVITY_ID = "actv_0123456789abcdef"
OTHER_ACTIVITY_ID = "actv_fedcba9876543210"
ACTION_UNKNOWN = "actn:bbbbbbbbbbbbbbbb"
ACTION_FAILED = "actn:cccccccccccccccc"
ACTION_UNKNOWN_OTHER = "actn:1111111111111111"
ACTION_FAILED_OTHER = "actn:2222222222222222"
RECEIPT_UNKNOWN = "act_receipt:1111111111111111"
RECEIPT_FAILED = "act_receipt:3333333333333333"
RECEIPT_FAILED_OTHER = "act_receipt:2222222222222222"
SETTLEMENT_UNKNOWN = "rset:dddddddddddddddd"
SETTLEMENT_FAILED = "rset:eeeeeeeeeeeeeeee"


def _activity(*, revision: int = 3, status: str = "ACTIVE") -> dict:
    return {
        "activity_id": ACTIVITY_ID,
        "schema_version": ac.SCHEMA_ACTIVITY_INSTANCE_V2,
        "revision": revision,
        "subject_id": ac.GLOBAL_SUBJECT_ID,
        "activity_kind": "CONTINUE_PERSONAL_ARTIFACT",
        "title": "她的作品",
        "subject_refs": [ARTIFACT],
        "status": status,
        "interruptibility": "LIGHT",
        "origin_ref": ARTIFACT,
        "decision_ref": "dec:aaaaaaaaaaaaaaaa",
        "adoption_ref": "adp:bbbbbbbbbbbbbbbb",
        "started_at": TS,
        "updated_at": TS,
        "checkpoint_ref": ARTIFACT_VERSION,
        "last_action_ref": "actn:cccccccccccccccc",
        "completion_evidence_ref": None,
        "source_refs": ["dec:aaaaaaaaaaaaaaaa"],
        "terminal_reason_code": None,
    }


def _resolver(ref: str) -> dict:
    if ref == ARTIFACT:
        return {"verified": True, "owner": "lifesupply.managed_artifact",
                "artifact_id": ARTIFACT_ID, "lifecycle": "ARCHIVED"}
    if ref == ARTIFACT_OTHER:
        return {"verified": True, "owner": "lifesupply.managed_artifact",
                "artifact_id": ARTIFACT_OTHER_ID, "lifecycle": "ARCHIVED"}
    if ref == ARTIFACT_ACTIVE:
        return {"verified": True, "owner": "lifesupply.managed_artifact",
                "artifact_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "lifecycle": "ACTIVE"}
    if ref == ACTION_UNKNOWN:
        return {"verified": True, "owner": "action_reality_ledger",
                "action_id": ACTION_UNKNOWN, "status": "UNKNOWN",
                "activity_ref": ACTIVITY_ID}
    if ref == ACTION_FAILED:
        return {"verified": True, "owner": "action_reality_ledger",
                "action_id": ACTION_FAILED, "status": "FAILED",
                "activity_ref": ACTIVITY_ID}
    if ref == ACTION_UNKNOWN_OTHER:
        return {"verified": True, "owner": "action_reality_ledger",
                "action_id": ACTION_UNKNOWN_OTHER, "status": "UNKNOWN",
                "activity_ref": OTHER_ACTIVITY_ID}
    if ref == ACTION_FAILED_OTHER:
        return {"verified": True, "owner": "action_reality_ledger",
                "action_id": ACTION_FAILED_OTHER, "status": "FAILED",
                "activity_ref": OTHER_ACTIVITY_ID}
    if ref == RECEIPT_UNKNOWN:
        return {"verified": True, "owner": "action_reality_ledger",
                "action_id": ACTION_UNKNOWN, "status": "UNKNOWN",
                "activity_ref": ACTIVITY_ID}
    if ref == RECEIPT_FAILED:
        return {"verified": True, "owner": "action_reality_ledger",
                "action_id": ACTION_FAILED, "status": "FAILED",
                "activity_ref": ACTIVITY_ID}
    if ref == RECEIPT_FAILED_OTHER:
        return {"verified": True, "owner": "action_reality_ledger",
                "action_id": ACTION_FAILED_OTHER, "status": "FAILED",
                "activity_ref": OTHER_ACTIVITY_ID}
    if ref == SETTLEMENT_UNKNOWN:
        return {"verified": True, "owner": "result_settlement_ledger",
                "settlement_id": SETTLEMENT_UNKNOWN, "status": "UNKNOWN",
                "activity_ref": ACTIVITY_ID}
    if ref == SETTLEMENT_FAILED:
        return {"verified": True, "owner": "result_settlement_ledger",
                "settlement_id": SETTLEMENT_FAILED, "status": "FAILED",
                "activity_ref": ACTIVITY_ID, "outcome_kind": "FAILURE_NO_EFFECT"}
    raise RuntimeError("owner has no such record")


def _valid_payload(**overrides) -> dict:
    payload = {
        "schema_version": ao.SCHEMA_FAILURE_ASSESSMENT,
        "assessment_id": "foassess:1111111111111111",
        "activity_id": ACTIVITY_ID,
        "activity_revision": 3,
        "assessed_by": ao.OWNER_LABEL,
        "verdict": "FAILED",
        "goal_scope_refs": [ARTIFACT],
        "criterion_refs": ["criterion:ARTIFACT_ARCHIVED"],
        "evidence_refs": [ARTIFACT],
        "unresolved_action_refs": [],
        "assessed_at": TS,
    }
    payload.update(overrides)
    return payload


@POSIX_ONLY
class StatusVocabularyTest(unittest.TestCase):
    def test_failed_is_terminal_and_blocked_is_open(self):
        self.assertIn(ac.STATUS_FAILED, ac.TERMINAL_ACTIVITY_STATUSES)
        self.assertIn(ac.STATUS_BLOCKED, ac.OPEN_ACTIVITY_STATUSES)
        self.assertNotIn(ac.STATUS_BLOCKED, ac.TERMINAL_ACTIVITY_STATUSES)

    def test_v1_and_v2_schemas_are_readable(self):
        self.assertIn(ac.SCHEMA_ACTIVITY_INSTANCE, ac.READABLE_ACTIVITY_SCHEMAS)
        self.assertIn(ac.SCHEMA_ACTIVITY_INSTANCE_V2, ac.READABLE_ACTIVITY_SCHEMAS)
        self.assertIn(ac.SCHEMA_LIFE_TRANSITION_V2, ac.READABLE_TRANSITION_SCHEMAS)


@POSIX_ONLY
class FailureAssessmentTest(unittest.TestCase):
    def test_valid_payload_passes_and_returns_owner_summary(self):
        summary = ao.validate_failure_payload_against_activity(
            _activity(), _valid_payload(), evidence_resolver=_resolver)
        self.assertEqual(summary["assessment_id"], "foassess:1111111111111111")
        self.assertEqual(summary["evidence_owners"][ARTIFACT], "lifesupply.managed_artifact")
        self.assertEqual(summary["criterion_names"], ["ARTIFACT_ARCHIVED"])

    def test_stale_revision_is_refused(self):
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(revision=4), _valid_payload(), evidence_resolver=_resolver)

    def test_scope_outside_the_activity_is_refused(self):
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), _valid_payload(goal_scope_refs=["artifact:someone-else"]),
                evidence_resolver=_resolver)

    def test_unknown_criterion_name_is_refused(self):
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), _valid_payload(criterion_refs=["criterion:NOT_A_REAL_CRITERION"]),
                evidence_resolver=_resolver)

    def test_self_asserted_or_model_fields_are_refused(self):
        for key, value in (("verified", True), ("model_id", "gpt"), ("http_status", 500)):
            with self.subTest(key=key):
                with self.assertRaises(ao.FailureAssessmentError):
                    ao.validate_failure_payload_against_activity(
                        _activity(), _valid_payload(**{key: value}),
                        evidence_resolver=_resolver)

    def test_unreadable_owner_evidence_is_refused(self):
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), _valid_payload(evidence_refs=["artifact:" + "9" * 32]),
                evidence_resolver=_resolver)

    def test_archived_criterion_needs_an_archived_owner_fact(self):
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), _valid_payload(evidence_refs=[ARTIFACT_ACTIVE]),
                evidence_resolver=_resolver)

    def test_unknown_settlement_is_not_a_terminal_refusal(self):
        payload = _valid_payload(
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL],
            evidence_refs=[SETTLEMENT_UNKNOWN])
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), payload, evidence_resolver=_resolver)

    def test_unknown_receipt_is_not_a_terminal_refusal(self):
        # The ref uses a legal evidence prefix, so it is NOT refused early by grammar: the
        # semantic check has to see the UNKNOWN status and refuse it on the facts.
        payload = _valid_payload(
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL],
            evidence_refs=[RECEIPT_UNKNOWN])
        with self.assertRaises(ao.FailureAssessmentError) as ctx:
            ao.validate_failure_payload_against_activity(
                _activity(), payload, evidence_resolver=_resolver)
        self.assertIn("UNKNOWN", str(ctx.exception))

    def test_failed_settlement_alone_is_not_provable_as_terminal_refusal(self):
        # A FAILED settlement/action is one failed attempt, not "the goal can never be made".
        # Current owner records carry no explicit terminal-refusal fact, so the criterion is
        # refused by name instead of being read as "close enough".
        payload = _valid_payload(
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL],
            evidence_refs=[SETTLEMENT_FAILED])
        with self.assertRaises(ao.FailureAssessmentError) as ctx:
            ao.validate_failure_payload_against_activity(
                _activity(), payload, evidence_resolver=_resolver)
        self.assertIn("EXTERNAL_TERMINAL_REFUSAL", str(ctx.exception))

    def test_failed_action_of_this_activity_is_still_not_a_terminal_refusal(self):
        payload = _valid_payload(
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL],
            evidence_refs=[RECEIPT_FAILED])
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), payload, evidence_resolver=_resolver)

    def test_archived_artifact_of_another_activity_is_refused(self):
        # B is archived; A's own bound artifact is not.  B's archive cannot sign A's failure.
        payload = _valid_payload(evidence_refs=[ARTIFACT_OTHER])
        with self.assertRaises(ao.FailureAssessmentError) as ctx:
            ao.validate_failure_payload_against_activity(
                _activity(), payload, evidence_resolver=_resolver)
        self.assertIn("goal scope", str(ctx.exception))

    def test_failed_action_of_another_activity_is_refused(self):
        payload = _valid_payload(
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL],
            evidence_refs=[RECEIPT_FAILED_OTHER])
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), payload, evidence_resolver=_resolver)

    def test_unresolved_obligation_must_really_be_unresolved(self):
        with self.assertRaises(ao.FailureAssessmentError):
            ao.validate_failure_payload_against_activity(
                _activity(), _valid_payload(unresolved_action_refs=[ACTION_FAILED]),
                evidence_resolver=_resolver)
        summary = ao.validate_failure_payload_against_activity(
            _activity(), _valid_payload(unresolved_action_refs=[ACTION_UNKNOWN]),
            evidence_resolver=_resolver)
        self.assertEqual(summary["unresolved_action_refs"], [ACTION_UNKNOWN])

    def test_admission_verifier_refuses_another_activitys_archived_artifact(self):
        import activity_admission as adm  # imported lazily: POSIX-only module

        verify = adm.build_failure_evidence_verifier(object(), _resolver)
        canonical = {"revision": 3, "activities": {ACTIVITY_ID: _activity()}}
        transition = {
            "activity_id": ACTIVITY_ID,
            "expected_revision": 3,
            "failure_assessment": _valid_payload(evidence_refs=[ARTIFACT_OTHER]),
        }
        with self.assertRaises(adm.ConsequenceRejected):
            verify(transition, canonical)
        # The same verifier accepts A's own bound archived artifact.
        accepted = verify(
            {**transition,
             "failure_assessment": _valid_payload(evidence_refs=[ARTIFACT])},
            canonical)
        self.assertEqual(accepted["activity_id"], ACTIVITY_ID)

    def test_unresolved_action_of_another_activity_is_refused(self):
        with self.assertRaises(ao.FailureAssessmentError) as ctx:
            ao.validate_failure_payload_against_activity(
                _activity(), _valid_payload(unresolved_action_refs=[ACTION_UNKNOWN_OTHER]),
                evidence_resolver=_resolver)
        self.assertIn("Activity", str(ctx.exception))

    def test_build_rejects_scope_this_activity_never_named(self):
        with self.assertRaises(ao.FailureAssessmentError):
            ao.build_failure_assessment(
                _activity(), goal_scope_refs=["artifact:nope"],
                criterion_refs=["criterion:ARTIFACT_ARCHIVED"],
                evidence_refs=[ARTIFACT], assessed_at=TS)


@POSIX_ONLY
class BlockedBasisTest(unittest.TestCase):
    def test_valid_basis_is_revision_bound(self):
        payload = ao.build_blocked_payload(
            _activity(status="BLOCKED"), blocked_on_ref="wait_dep:artifact.x.y",
            blocked_reason_code="BLOCKED_BY_CONDITION", observed_at=TS)
        summary = ao.validate_blocked_payload_against_activity(
            _activity(status="BLOCKED"), payload)
        self.assertEqual(summary["blocked_on_ref"], "wait_dep:artifact.x.y")

    def test_stale_revision_is_refused(self):
        payload = ao.build_blocked_payload(
            _activity(status="BLOCKED"), blocked_on_ref="wait_dep:artifact.x.y",
            blocked_reason_code="BLOCKED_BY_CONDITION", observed_at=TS)
        with self.assertRaises(ao.BlockedBasisError):
            ao.validate_blocked_payload_against_activity(
                _activity(status="BLOCKED", revision=4), payload)

    def test_unstructured_basis_is_refused(self):
        with self.assertRaises(ao.BlockedBasisError):
            ao.build_blocked_payload(
                _activity(), blocked_on_ref="not a ref",
                blocked_reason_code="BLOCKED_BY_CONDITION", observed_at=TS)


def _transitioned(entries, *, activity_id, transition_type, from_status, to_status,
                  snapshot, expected_revision, result_revision):
    entry = {
        "schema_version": ac.SCHEMA_LIFE_TRANSITION_V2,
        "transition_id": "trn_%016d" % result_revision,
        "activity_id": activity_id,
        "subject_id": ac.GLOBAL_SUBJECT_ID,
        "transition_type": transition_type,
        "from_status": from_status,
        "to_status": to_status,
        "expected_revision": expected_revision,
        "result_revision": result_revision,
        "occurred_at": TS,
        "activity_snapshot": copy.deepcopy(snapshot),
    }
    prev = entries[-1]["entry_hash"] if entries else ac.GENESIS_HASH
    entry["prev_hash"] = prev
    entry["entry_hash"] = ac.sha256_hex(ac.canonical_json_line(entry))
    entries.append(entry)
    return entry


@POSIX_ONLY
class ReplayTest(unittest.TestCase):
    def test_replay_recognises_blocked_and_failed(self):
        aid = "actv_0123456789abcdef"
        active = _activity(revision=1, status="ACTIVE")
        blocked = _activity(revision=2, status="BLOCKED")
        blocked["foreground_released"] = True
        blocked["blocked_on_ref"] = "wait_dep:artifact.x.y"
        blocked["blocked_reason_code"] = "BLOCKED_BY_CONDITION"
        failed = _activity(revision=3, status="FAILED")
        failed["terminal_reason_code"] = "FAILED_BY_OWNER_ASSESSMENT"
        failed["failure_assessment_ref"] = "foassess:1111111111111111"
        entries: list[dict] = []
        _transitioned(entries, activity_id=aid, transition_type="START",
                      from_status="IDLE", to_status="ACTIVE", snapshot=active,
                      expected_revision=0, result_revision=1)
        _transitioned(entries, activity_id=aid, transition_type="BLOCK",
                      from_status="ACTIVE", to_status="BLOCKED", snapshot=blocked,
                      expected_revision=1, result_revision=2)
        _transitioned(entries, activity_id=aid, transition_type="FAIL",
                      from_status="BLOCKED", to_status="FAILED", snapshot=failed,
                      expected_revision=2, result_revision=3)
        state = ac.ReplayRuntime.project_from_transitions(entries)
        self.assertEqual(state["revision"], 3)
        self.assertIsNone(state["foreground_activity_ref"])
        self.assertEqual(state["activities"][aid]["status"], "FAILED")

    def test_replay_still_rejects_a_bad_revision_chain(self):
        aid = "actv_0123456789abcdef"
        entries: list[dict] = []
        _transitioned(entries, activity_id=aid, transition_type="START",
                      from_status="IDLE", to_status="ACTIVE",
                      snapshot=_activity(revision=1, status="ACTIVE"),
                      expected_revision=0, result_revision=1)
        _transitioned(entries, activity_id=aid, transition_type="BLOCK",
                      from_status="ACTIVE", to_status="BLOCKED",
                      snapshot=_activity(revision=2, status="BLOCKED"),
                      expected_revision=5, result_revision=6)
        with self.assertRaises(ac.RecoveryRequiredError):
            ac.ReplayRuntime.project_from_transitions(entries)


class _FakeActivityRead:
    def __init__(self, activities):
        self.activities = [dict(row) for row in activities]

    def list_open_activities(self):
        return [dict(row) for row in self.activities]

    def get_activity(self, activity_id):
        for row in self.activities:
            if str(row.get("activity_id")) == str(activity_id):
                return dict(row)
        return None


def _blocked_activity() -> dict:
    row = _activity(status="BLOCKED", revision=5)
    row["blocked_on_ref"] = "block_basis:test.unconditional"
    row["blocked_reason_code"] = "BLOCKED_BY_CONDITION"
    row["foreground_released"] = True
    return row


@POSIX_ONLY
class BlockedExitCandidateTest(unittest.TestCase):
    def setUp(self):
        import candidate_sources_ag0 as ag0
        self.ag0 = ag0

    def _source(self, activity, provider):
        return self.ag0.BlockedActivityCandidateSource(
            _FakeActivityRead([activity]), unresolved_effect_provider=provider)

    def test_blocked_activity_is_offered_for_its_owner_exit(self):
        source = self._source(_blocked_activity(),
                              lambda _aid: {"readable": True, "unresolved": False})
        cands, _snap, meta = source.materialize(observed_at=TS)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].candidate_kind, self.ag0.CANDIDATE_KIND_BLOCKED_ACTIVITY)
        self.assertEqual(cands[0].source_kind, self.ag0.SOURCE_BLOCKED_ACTIVITY)
        self.assertEqual(cands[0].availability_status, self.ag0.CANDIDATE_STATUS_OPEN)
        self.assertEqual(cands[0].activity_ref, "actv_0123456789abcdef")

    def test_unresolved_effect_withholds_the_exit_by_name(self):
        source = self._source(
            _blocked_activity(),
            lambda _aid: {"readable": True, "unresolved": True, "refs": [ACTION_UNKNOWN]})
        cands, _snap, _meta = source.materialize(observed_at=TS)
        self.assertEqual(cands, [])
        self.assertEqual(source.last_withheld[0]["reason_code"],
                         source.WITHHELD_UNRESOLVED)

    def test_unreadable_safety_is_never_safe(self):
        source = self._source(_blocked_activity(), lambda _aid: {"readable": False})
        cands, _snap, _meta = source.materialize(observed_at=TS)
        self.assertEqual(cands, [])
        self.assertEqual(source.last_withheld[0]["reason_code"],
                         source.WITHHELD_SAFETY_UNREAD)
        no_provider = self.ag0.BlockedActivityCandidateSource(
            _FakeActivityRead([_blocked_activity()]))
        cands, _snap, _meta = no_provider.materialize(observed_at=TS)
        self.assertEqual(cands, [])
        self.assertEqual(no_provider.last_withheld[0]["reason_code"],
                         no_provider.WITHHELD_SAFETY_UNREAD)

    def test_refresh_drops_a_resumed_activity(self):
        source = self._source(_blocked_activity(),
                              lambda _aid: {"readable": True, "unresolved": False})
        cands, _snap, _meta = source.materialize(observed_at=TS)
        active = _blocked_activity()
        active["status"] = "ACTIVE"
        source.activity_read.activities = [active]
        refreshed = source.refresh(cands[0], observed_at=TS)
        self.assertEqual(refreshed.availability_status, self.ag0.CANDIDATE_STATUS_STALE)


class BlockedContinuityTest(unittest.TestCase):
    def test_blocked_is_unfinished_but_not_continuable(self):
        import life_continuity_inputs as lci
        binding = lci.ContinuityBinding(subject="chiyo", home="/tmp/home", now=TS)
        reader = lci.LifeContinuityInputs(
            binding=binding, activity_source=_FakeActivityRead([_blocked_activity()]),
            agenda_source=None)
        view = reader.read()
        item = view.unfinished[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertFalse(item["continue_allowed"])
        self.assertFalse(item["resumable"])
        self.assertEqual(view.next_step["kind"], lci.NEXT_STEP_BLOCKED)
        choice = next(row for row in view.choices
                      if row["subject_ref"] == item["item_ref"])
        self.assertEqual(choice["kind"], lci.CHOICE_BLOCKED)
        self.assertEqual(choice["reason_code"], "BLOCKED_REQUIRES_CONDITION")

    def test_blocked_with_an_open_eligibility_is_resumable(self):
        import life_continuity_inputs as lci

        class _Agenda:
            def list_agenda_items(self, only_open=True):
                return []

            def list_eligibilities(self, only_open=True):
                return [{"eligibility_id": "relig:0000000000000001",
                         "activity_id": "actv_0123456789abcdef", "status": "OPEN",
                         "activity_revision": 5, "eligible_at": TS,
                         "resume_condition_ref": "rcond:0000000000000001"}]

        binding = lci.ContinuityBinding(subject="chiyo", home="/tmp/home", now=TS)
        view = lci.LifeContinuityInputs(
            binding=binding, activity_source=_FakeActivityRead([_blocked_activity()]),
            agenda_source=_Agenda()).read()
        item = view.unfinished[0]
        self.assertTrue(item["resumable"])
        self.assertEqual(item["resume_eligibility_ref"], "relig:0000000000000001")
        self.assertEqual(view.next_step["kind"], lci.NEXT_STEP_RESUME_WAIT)


if __name__ == "__main__":
    unittest.main()
