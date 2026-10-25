#!/usr/bin/env python3
"""B1 alignment: an OPEN ResumeEligibility on a PAUSED Activity is still resumable.

What is real here
-----------------
* the **owners** are real and in-process: ``ActivityAuthorityLease``,
  ``CanonicalActivityStore``, ``ActivityCommandService`` (LR-2) and
  ``WaitingCoordinator``/its three stores (LR-4), backed by an isolated temporary root;
* every Activity transition goes through LR-2's own admission issuer, which verifies the
  Decision *and* Adoption read ports on every command.  Those two read ports are the only
  stand-ins: they answer with the exact fields ``ProvenanceVerifier`` reads, so the
  command-scoped grant check, activity-scope check and fingerprint check all still run;
* the OPEN eligibility is **synthetic**: it is written through LR-4's real
  ``ResumeEligibility`` dataclass and real ``commit_records`` because no public API mints an
  eligibility for a PAUSED Activity.  It marks an artificially reachable state, not a
  naturally produced one -- in particular, nothing here claims that AG-2 would ever generate
  this eligibility, nor that the legacy interrupted instance can be resumed by this fix.

The AG-1 legal-set check is a real call into ``DecisionOutputValidator``; the CandidateRecord
and both snapshots are test-layer values, so this test shows that AG-1 already offered
RESUME for the PAUSED candidate, not that AG-2 ever assembled such a candidate set.

Run:  python3 -m unittest -v tests.test_paused_eligible_alignment
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
NATIVE = ROOT / "components" / "native"
sys.path[:0] = [
    str(ROOT),
    str(LIFE_SCRIPTS),
    str(NATIVE),
    str(NATIVE / "src"),
    str(NATIVE / "memory_runtime_v1"),
]

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import activity_waiting_agenda as awa  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

STAMP = "2026-10-23T00:00:00+00:00"
LATER = "2026-10-23T00:00:10+00:00"
DECISION_REF = "dec:b1fixture"


class _DecisionReader:
    """AG-1 owner read-port stand-in: only the fields ``ProvenanceVerifier`` reads."""

    def get_decision(self, decision_id: str):
        if decision_id != DECISION_REF:
            return None
        return {
            "decision_id": decision_id,
            "revision": 1,
            "snapshot_fingerprint": f"fp:{decision_id}",
        }


class _AdoptionReader:
    """AG-2 owner read-port stand-in: one command-scoped grant per idempotency key."""

    def __init__(self) -> None:
        self.grants: dict[str, tuple[str, str]] = {}

    def grant(self, command_id: str, mutation_kind: str, activity_ref: str) -> None:
        self.grants[command_id] = (mutation_kind, activity_ref)

    def get_adoption_by_id(self, adoption_id: str):
        command_id = adoption_id.split(":", 1)[1] if ":" in adoption_id else ""
        granted = self.grants.get(command_id)
        if granted is None:
            return None
        mutation_kind, activity_ref = granted
        return {
            "adoption_id": adoption_id,
            "decision_ref": DECISION_REF,
            "decision_revision": 1,
            "adoption_status": "APPLIED",
            "revision": 1,
            "authorized_mutation_kind": mutation_kind,
            "authorized_command_id": command_id,
            "created_activity_ref": activity_ref,
            "target_activity_ref": activity_ref,
            "authorized_activity_ref": activity_ref,
        }


class _Harness:
    """One isolated canonical root with the real LR-2/LR-4 owners wired together."""

    def __init__(self) -> None:
        # Kept on purpose: the fixture root is evidence for this run, not throwaway state.
        self.root = Path(tempfile.mkdtemp(prefix="paused-eligible-b1-"))
        self.adoptions = _AdoptionReader()
        issuer = adm.build_admission_issuer(
            _DecisionReader(), self.adoptions, runtime_epoch="b1-alignment-test"
        )
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.root,
            namespace=ac.NAMESPACE_ISOLATED_TEST,
            _admission_issuer=issuer,
        )
        capability = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.commands = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=capability
        )
        self.coordinator = awa.WaitingCoordinator(
            activity_store=self.store,
            lease=self.lease,
            command_service=self.commands,
            namespace=ac.NAMESPACE_ISOLATED_TEST,
        )

    def close(self) -> None:
        self.lease.release()

    # -- LR-2 transitions ---------------------------------------------------

    def _grant(self, command_id: str, mutation_kind: str, activity_ref: str) -> None:
        self.adoptions.grant(command_id, mutation_kind, activity_ref)

    def state_revision(self) -> int:
        state, _ = self.store.load_verified_state_and_journal()
        return int(state["revision"])

    def activity(self, activity_id: str) -> dict:
        state, _ = self.store.load_verified_state_and_journal()
        return dict((state.get("activities") or {})[activity_id])

    def start(self, activity_id: str, *, command_id: str = "b1-start") -> dict:
        self._grant(command_id, ac.TRANSITION_START, activity_id)
        result = self.commands.start_activity(
            activity_kind="READ_ARTIFACT",
            title="B1 alignment fixture",
            origin_ref="world:b1fixture",
            source_refs=["world:b1fixture"],
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
            activity_id=activity_id,
        )
        return dict(result.activity)

    def pause(self, activity_id: str, *, command_id: str = "b1-pause") -> dict:
        self._grant(command_id, ac.TRANSITION_PAUSE, activity_id)
        result = self.commands.pause_activity(
            activity_id=activity_id,
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            source_refs=["decision:b1pause"],
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
        )
        return dict(result.activity)

    def wait(
        self,
        activity_id: str,
        *,
        episode: str,
        command_id: str = "b1-wait",
        release_foreground: bool = False,
    ) -> dict:
        self._grant(command_id, ac.TRANSITION_WAIT, activity_id)
        result = self.commands.wait_activity(
            activity_id=activity_id,
            waiting_on_ref="wait_dep:b1fixture",
            resume_condition_ref="rcond:b1fixture",
            waiting_episode_id=episode,
            release_foreground=release_foreground,
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            source_refs=["decision:b1wait"],
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
        )
        return dict(result.activity)

    def complete(
        self, activity_id: str, *, command_id: str = "b1-complete"
    ) -> dict:
        self._grant(command_id, ac.TRANSITION_COMPLETE, activity_id)
        result = self.commands.complete_activity(
            activity_id=activity_id,
            completion_evidence_ref="evidence:b1completed",
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            source_refs=["decision:b1complete"],
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
        )
        return dict(result.activity)

    # -- LR-4 records -------------------------------------------------------

    def seed_eligibility(
        self,
        *,
        eligibility_id: str,
        activity: dict,
        status: str = awa.ELIG_STATUS_OPEN,
        activity_revision: int | None = None,
        waiting_episode_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict:
        record = awa.ResumeEligibility(
            eligibility_id=eligibility_id,
            schema_version=awa.SCHEMA_RESUME_ELIGIBILITY,
            revision=1,
            subject_id=ac.GLOBAL_SUBJECT_ID,
            activity_id=activity["activity_id"],
            activity_revision=(
                int(activity["revision"])
                if activity_revision is None
                else activity_revision
            ),
            waiting_episode_id=(
                waiting_episode_id
                or activity.get("waiting_episode_id")
                or "wep:b1fixture"
            ),
            resume_condition_ref="rcond:b1fixture",
            agenda_ref=None,
            source_event_refs=["evt:b1fixture"],
            causal_parent_refs=["rcond:b1fixture"],
            eligible_at=STAMP,
            recorded_at=STAMP,
            expires_at=expires_at,
            policy_version=awa.POLICY_VERSION_LR4,
            status=status,
            idempotency_key=f"elig:{eligibility_id}",
        ).to_dict()
        self.coordinator.eligibility_store.commit_records(
            lease=self.lease,
            capability=self.coordinator.eligibility_capability,
            records=[record],
            action="B1_FIXTURE_SEED_ELIGIBILITY",
            idempotency_key=f"seed:{eligibility_id}",
        )
        return record

    def seed_condition(self, *, condition_id: str, activity: dict) -> dict:
        record = awa.ResumeCondition(
            condition_id=condition_id,
            schema_version=awa.SCHEMA_RESUME_CONDITION,
            revision=1,
            subject_id=ac.GLOBAL_SUBJECT_ID,
            activity_id=activity["activity_id"],
            activity_revision_at_bind=int(activity["revision"]),
            waiting_episode_id="wep:b1condition",
            condition_kind=awa.COND_KIND_TIME_AT_OR_AFTER,
            source_refs=["evt:b1fixture"],
            causal_parent_refs=[],
            waiting_on_ref="wait_dep:b1fixture",
            parameters={"target_time": "2026-10-24T00:00:00+00:00"},
            status=awa.COND_STATUS_PENDING,
            created_at=STAMP,
            observed_at=STAMP,
            idempotency_key=f"cond:{condition_id}",
        ).to_dict()
        self.coordinator.condition_store.commit_records(
            lease=self.lease,
            capability=self.coordinator.condition_capability,
            records=[record],
            action="B1_FIXTURE_SEED_CONDITION",
            idempotency_key=f"seed:{condition_id}",
        )
        return record

    def seed_agenda(self, *, agenda_id: str, activity: dict) -> dict:
        record = awa.AgendaItem(
            agenda_id=agenda_id,
            schema_version=awa.SCHEMA_AGENDA_ITEM,
            revision=1,
            subject_id=ac.GLOBAL_SUBJECT_ID,
            activity_id=activity["activity_id"],
            activity_revision_at_bind=int(activity["revision"]),
            waiting_episode_id="wep:b1agenda",
            resume_condition_ref="rcond:b1fixture",
            trigger_kind=awa.TRIGGER_KIND_TIME_AT,
            due_at="2026-10-24T00:00:00+00:00",
            window_start=None,
            window_end=None,
            source_refs=["evt:b1fixture"],
            causal_parent_refs=[],
            status=awa.AGENDA_STATUS_SCHEDULED,
            created_at=STAMP,
            idempotency_key=f"agenda:{agenda_id}",
        ).to_dict()
        self.coordinator.agenda_store.commit_records(
            lease=self.lease,
            capability=self.coordinator.agenda_capability,
            records=[record],
            action="B1_FIXTURE_SEED_AGENDA",
            idempotency_key=f"seed:{agenda_id}",
        )
        return record

    # -- Reads / commands ---------------------------------------------------

    def eligibility(self, eligibility_id: str) -> dict:
        state = self.coordinator.eligibility_store.load_state()
        return dict((state.get("eligibilities") or {})[eligibility_id])

    def reconcile(self) -> dict:
        return self.coordinator.reconcile_with_canonical_activities(now_at=STAMP)

    def resume(
        self,
        eligibility_id: str,
        *,
        command_id: str,
        activity_id: str,
        expected_revision: int | None = None,
    ):
        self._grant(command_id, ac.TRANSITION_RESUME, activity_id)
        return self.coordinator.explicit_resume_from_eligibility(
            eligibility_id=eligibility_id,
            expected_revision=(
                self.state_revision() if expected_revision is None else expected_revision
            ),
            idempotency_key=command_id,
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
            occurred_at=LATER,
            observed_at=LATER,
        )


@POSIX_ONLY
class PausedEligibleAlignmentTest(unittest.TestCase):
    """PAUSED + OPEN eligibility resumes; every other door keeps its old predicate."""

    def make_harness(self) -> _Harness:
        harness = _Harness()
        self.addCleanup(harness.close)
        return harness

    def assert_refused(self, callable_, message: str) -> Exception:
        with self.assertRaises(awa.EligibilityNotOpenError, msg=message) as caught:
            callable_()
        return caught.exception

    # -- positive controls --------------------------------------------------

    def test_paused_with_matching_open_eligibility_survives_reconcile_then_resumes(self):
        harness = self.make_harness()
        activity = harness.start("actv:b1paused")
        paused = harness.pause("actv:b1paused")
        self.assertEqual(paused["status"], ac.STATUS_PAUSED)
        # Canonical PAUSE clears the waiting episode; the episode predicate cannot fire here.
        self.assertIsNone(paused.get("waiting_episode_id"))

        eligibility_id = "relig:b1paused"
        harness.seed_eligibility(
            eligibility_id=eligibility_id,
            activity=paused,
            waiting_episode_id="wep:b1synthetic",
        )

        result = harness.reconcile()
        self.assertEqual(result["invalidated_eligibilities"], 0)
        self.assertEqual(harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_OPEN)

        # AG-1 already offers RESUME for this state.  v0.4 background pause no longer leaves a
        # pointer on a newly paused Activity, but this fixture keeps the legacy pointer shape:
        # the compatibility predicate and LR-4/LR-2 resume path must still work for old records.
        legal = ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate(
            _resume_candidate(paused, eligibility_id),
            context_snapshot=_context(paused),
            capacity_snapshot=_capacity(),
        )
        self.assertIn(ag1.DECISION_RESUME, legal)
        self.assertEqual(activity["activity_id"], paused["activity_id"])

        resumed = harness.resume(
            eligibility_id, command_id="b1-resume", activity_id="actv:b1paused"
        )
        self.assertEqual(resumed.activity["status"], ac.STATUS_ACTIVE)
        after = harness.eligibility(eligibility_id)
        self.assertEqual(after["status"], awa.ELIG_STATUS_CONSUMED)
        self.assertEqual(
            after["resolution_reason_code"], "CONSUMED_BY_EXPLICIT_RESUME"
        )

    def test_waiting_with_matching_open_eligibility_still_resumes(self):
        harness = self.make_harness()
        harness.start("actv:b1waiting")
        waited = harness.wait("actv:b1waiting", episode="wep:b1waiting")
        self.assertEqual(waited["status"], ac.STATUS_WAITING)

        eligibility_id = "relig:b1waiting"
        harness.seed_eligibility(eligibility_id=eligibility_id, activity=waited)

        result = harness.reconcile()
        self.assertEqual(result["invalidated_eligibilities"], 0)
        self.assertEqual(harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_OPEN)

        resumed = harness.resume(
            eligibility_id, command_id="b1-resume-waiting", activity_id="actv:b1waiting"
        )
        self.assertEqual(resumed.activity["status"], ac.STATUS_ACTIVE)
        after = harness.eligibility(eligibility_id)
        self.assertEqual(after["status"], awa.ELIG_STATUS_CONSUMED)
        self.assertEqual(
            after["resolution_reason_code"], "CONSUMED_BY_EXPLICIT_RESUME"
        )

    # -- negative controls: reconcile keeps its old gates --------------------

    def test_reconcile_still_stales_paused_eligibility_with_stale_revision(self):
        harness = self.make_harness()
        harness.start("actv:b1stale")
        paused = harness.pause("actv:b1stale")
        eligibility_id = "relig:b1stale"
        harness.seed_eligibility(
            eligibility_id=eligibility_id,
            activity=paused,
            activity_revision=int(paused["revision"]) - 1,
        )

        result = harness.reconcile()
        self.assertEqual(result["invalidated_eligibilities"], 1)
        after = harness.eligibility(eligibility_id)
        self.assertEqual(after["status"], awa.ELIG_STATUS_STALE)
        self.assertEqual(after["resolution_reason_code"], "STALE_ACTIVITY_REVISION")

        self.assert_refused(
            lambda: harness.resume(
                eligibility_id, command_id="b1-resume-stale", activity_id="actv:b1stale"
            ),
            "a stale-revision eligibility on a PAUSED Activity must still be refused",
        )
        self.assertEqual(harness.activity("actv:b1stale")["status"], ac.STATUS_PAUSED)

    def test_reconcile_still_stales_eligibility_for_active_activity(self):
        harness = self.make_harness()
        active = harness.start("actv:b1active")
        eligibility_id = "relig:b1active"
        harness.seed_eligibility(eligibility_id=eligibility_id, activity=active)

        result = harness.reconcile()
        self.assertEqual(result["invalidated_eligibilities"], 1)
        after = harness.eligibility(eligibility_id)
        self.assertEqual(after["status"], awa.ELIG_STATUS_STALE)
        self.assertEqual(after["resolution_reason_code"], "STALE_ACTIVITY_REVISION")

        self.assert_refused(
            lambda: harness.resume(
                eligibility_id, command_id="b1-resume-active", activity_id="actv:b1active"
            ),
            "ACTIVE is not a resumable status, PAUSED support must not leak into it",
        )

    def test_reconcile_still_cancels_eligibility_for_terminal_activity(self):
        harness = self.make_harness()
        harness.start("actv:b1done")
        completed = harness.complete("actv:b1done")
        self.assertEqual(completed["status"], ac.STATUS_COMPLETED)
        eligibility_id = "relig:b1done"
        harness.seed_eligibility(eligibility_id=eligibility_id, activity=completed)

        result = harness.reconcile()
        self.assertEqual(result["invalidated_eligibilities"], 1)
        after = harness.eligibility(eligibility_id)
        self.assertEqual(after["status"], awa.ELIG_STATUS_CANCELLED)
        self.assertEqual(
            after["resolution_reason_code"], "TERMINAL_ACTIVITY_INVALIDATED"
        )

        self.assert_refused(
            lambda: harness.resume(
                eligibility_id, command_id="b1-resume-done", activity_id="actv:b1done"
            ),
            "a terminal Activity must not be resumable through an eligibility",
        )

    def test_reconcile_still_stales_waiting_eligibility_with_episode_mismatch(self):
        harness = self.make_harness()
        harness.start("actv:b1episode")
        waited = harness.wait("actv:b1episode", episode="wep:b1episode")
        self.assertEqual(waited.get("waiting_episode_id"), "wep:b1episode")

        eligibility_id = "relig:b1episode"
        harness.seed_eligibility(
            eligibility_id=eligibility_id,
            activity=waited,
            waiting_episode_id="wep:b1other",
        )

        result = harness.reconcile()
        self.assertEqual(result["invalidated_eligibilities"], 1)
        after = harness.eligibility(eligibility_id)
        self.assertEqual(after["status"], awa.ELIG_STATUS_STALE)
        self.assertEqual(after["resolution_reason_code"], "STALE_ACTIVITY_REVISION")

        self.assert_refused(
            lambda: harness.resume(
                eligibility_id,
                command_id="b1-resume-episode",
                activity_id="actv:b1episode",
            ),
            "an eligibility bound to another waiting episode must stay stale",
        )

    def test_paused_open_condition_and_agenda_are_still_invalidated(self):
        harness = self.make_harness()
        harness.start("actv:b1branches")
        paused = harness.pause("actv:b1branches")
        harness.seed_condition(condition_id="rcond:b1paused", activity=paused)
        harness.seed_agenda(agenda_id="agnd:b1paused", activity=paused)
        eligibility_id = "relig:b1branches"
        harness.seed_eligibility(eligibility_id=eligibility_id, activity=paused)

        result = harness.reconcile()
        # Only the eligibility branch was aligned; condition and agenda still require WAITING.
        self.assertEqual(result["invalidated_conditions"], 1)
        self.assertEqual(result["invalidated_agenda_items"], 1)
        self.assertEqual(result["invalidated_eligibilities"], 0)

        conditions = harness.coordinator.condition_store.load_state()["conditions"]
        condition = conditions["rcond:b1paused"]
        self.assertEqual(condition["status"], awa.COND_STATUS_STALE)
        self.assertEqual(condition["resolution_reason_code"], "STALE_ACTIVITY_REVISION")

        agenda = harness.coordinator.agenda_store.load_state()["agenda_items"]
        item = agenda["agnd:b1paused"]
        self.assertEqual(item["status"], awa.AGENDA_STATUS_CANCELLED)
        self.assertEqual(item["resolution_reason_code"], "STALE_ACTIVITY_REVISION")

        self.assertEqual(harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_OPEN)

    # -- negative controls: the other entry gates ---------------------------

    def test_expired_open_eligibility_on_paused_activity_is_rejected(self):
        harness = self.make_harness()
        harness.start("actv:b1expired")
        paused = harness.pause("actv:b1expired")
        eligibility_id = "relig:b1expired"
        harness.seed_eligibility(
            eligibility_id=eligibility_id,
            activity=paused,
            expires_at="2026-10-22T00:00:00+00:00",
        )

        result = harness.reconcile()
        # Reconcile has no expiry gate; refusal must come from the entry's own check.
        self.assertEqual(result["invalidated_eligibilities"], 0)
        self.assertEqual(harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_OPEN)

        error = self.assert_refused(
            lambda: harness.resume(
                eligibility_id, command_id="b1-resume-expired", activity_id="actv:b1expired"
            ),
            "an expired eligibility must stay refused even on a PAUSED Activity",
        )
        self.assertIn("expired", str(error))
        self.assertEqual(harness.activity("actv:b1expired")["status"], ac.STATUS_PAUSED)

    def test_consumed_eligibility_is_not_resurrected(self):
        harness = self.make_harness()
        harness.start("actv:b1consumed")
        paused = harness.pause("actv:b1consumed")
        eligibility_id = "relig:b1consumed"
        harness.seed_eligibility(
            eligibility_id=eligibility_id,
            activity=paused,
            status=awa.ELIG_STATUS_CONSUMED,
        )

        result = harness.reconcile()
        self.assertEqual(result["invalidated_eligibilities"], 0)
        self.assertEqual(
            harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_CONSUMED
        )

        self.assert_refused(
            lambda: harness.resume(
                eligibility_id, command_id="b1-resume-consumed", activity_id="actv:b1consumed"
            ),
            "a CONSUMED eligibility must not be reopened by the alignment",
        )
        self.assertEqual(
            harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_CONSUMED
        )

    def test_unknown_eligibility_ref_is_rejected(self):
        harness = self.make_harness()
        harness.start("actv:b1unknown")
        harness.pause("actv:b1unknown")

        error = self.assert_refused(
            lambda: harness.resume(
                "relig:b1missing",
                command_id="b1-resume-missing",
                activity_id="actv:b1unknown",
            ),
            "an unknown eligibility id must still be refused",
        )
        self.assertIn("not found", str(error))

    def test_foreground_held_by_another_activity_still_blocks_resume(self):
        harness = self.make_harness()
        harness.start("actv:b1parked")
        parked = harness.wait(
            "actv:b1parked", episode="wep:b1parked", release_foreground=True
        )
        self.assertEqual(parked["status"], ac.STATUS_WAITING)
        harness.start("actv:b1front", command_id="b1-start-other")

        eligibility_id = "relig:b1parked"
        harness.seed_eligibility(eligibility_id=eligibility_id, activity=parked)

        with self.assertRaises(ac.ForegroundConflictError):
            harness.resume(
                eligibility_id, command_id="b1-resume-parked", activity_id="actv:b1parked"
            )
        self.assertEqual(harness.activity("actv:b1parked")["status"], ac.STATUS_WAITING)
        self.assertEqual(harness.activity("actv:b1front")["status"], ac.STATUS_ACTIVE)
        self.assertEqual(harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_OPEN)

    def test_repeated_operation_does_not_transition_twice(self):
        harness = self.make_harness()
        harness.start("actv:b1repeat")
        paused = harness.pause("actv:b1repeat")
        eligibility_id = "relig:b1repeat"
        harness.seed_eligibility(eligibility_id=eligibility_id, activity=paused)

        first = harness.resume(
            eligibility_id, command_id="b1-resume-repeat", activity_id="actv:b1repeat"
        )
        self.assertEqual(first.activity["status"], ac.STATUS_ACTIVE)
        resumed_revision = int(first.activity["revision"])
        self.assertEqual(
            harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_CONSUMED
        )

        # The eligibility gate is checked before LR-2, so the replay is refused there; the
        # existing contract never offers a second transition for the same eligibility.
        self.assert_refused(
            lambda: harness.resume(
                eligibility_id,
                command_id="b1-resume-repeat",
                activity_id="actv:b1repeat",
                expected_revision=harness.state_revision(),
            ),
            "the same eligibility must not resume the Activity a second time",
        )
        after = harness.activity("actv:b1repeat")
        self.assertEqual(after["status"], ac.STATUS_ACTIVE)
        self.assertEqual(int(after["revision"]), resumed_revision)
        self.assertEqual(
            harness.eligibility(eligibility_id)["status"], awa.ELIG_STATUS_CONSUMED
        )


# ---------------------------------------------------------------------------
# AG-1 legal-set inputs (test-layer values; the validator call itself is real)
# ---------------------------------------------------------------------------


def _resume_candidate(activity: dict, eligibility_id: str) -> ag0.CandidateRecord:
    return ag0.CandidateRecord(
        candidate_id="cand:b1fixture",
        candidate_kind=ag0.CANDIDATE_KIND_RESUME_ACTIVITY,
        source_kind=ag0.SOURCE_RESUME_ELIGIBLE,
        source_ref=eligibility_id,
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=STAMP,
        observed_at=STAMP,
        recorded_at=STAMP,
        idempotency_key="cand:b1fixture",
        activity_ref=activity["activity_id"],
        resume_eligibility_ref=eligibility_id,
        causal_parent_refs=["rcond:b1fixture"],
        provenance_refs=["evt:b1fixture"],
    )


def _context(activity: dict) -> ag1.DecisionContextSnapshot:
    return ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:b1fixture",
        opportunity_ref="dopp:b1fixture",
        candidate_set_ref="cset:b1fixture",
        life_frame_ref="lframe:b1fixture",
        captured_at=STAMP,
        source_revisions={"snapshot": 1},
        snapshot_fingerprint="sfp:b1fixture",
        current_activity_ref=activity["activity_id"],
        current_activity_status=activity["status"],
        current_activity_revision=int(activity["revision"]),
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
    )


def _capacity() -> ag1.AgencyCapacitySnapshot:
    return ag1.AgencyCapacitySnapshot(
        capacity_snapshot_id="cap:b1fixture",
        captured_at=STAMP,
        source_revisions=["snapshot:b1fixture"],
        candidate_executability={"cand:b1fixture": {"executability": ag1.EXEC_NOW}},
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
