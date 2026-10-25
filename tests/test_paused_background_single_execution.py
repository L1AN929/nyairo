#!/usr/bin/env python3
"""v0.4 background pause: a PAUSE frees the single slot; resume never displaces.

What this file pins down (and nothing more)
-------------------------------------------
* a successful PAUSE releases ``foreground_activity_ref`` while keeping the Activity
  record, its stop point and its (LR-4 owned) condition;
* another Activity may then START, so several Activities can be suspended at once -- but
  at most one is ACTIVE and it is the only foreground;
* a background RESUME is refused with zero side effects while another Activity executes:
  no pause of that other Activity, no consumed/cancelled eligibility, no extra transition;
* with the slot free the same RESUME returns to the same Activity;
* an unconditional PAUSE releases the slot but emits no condition and no eligibility;
* a wrong revision or a SAFE_BOUNDARY PAUSE does not release anything early;
* a conditional PAUSE whose LR-4 binding failed is completed by the *same* Decision on the
  suspended Activity even if another Activity has since taken the foreground;
* a same-root restart keeps several suspended Activities and their conditions without
  replaying a transition.
* AG-1 offers START/RESUME only when the owner's slot is free (including the legacy
  PAUSED-pointer compatibility case, which is not migrated behind the scenes).

Every Activity and condition transition here goes through the real owners (LR-2
``ActivityCommandService``, LR-4 ``WaitingCoordinator``, AG-2 ``DecisionAdoptionCoordinator``)
on an isolated temporary root.  Decision/Adoption read ports, the eligibility rows used for
the conflict cases, and the AG-1 snapshots are test-layer values -- explicitly not evidence
that a real model chooses any of this, nor that an eligibility was generated naturally in
those particular negative controls.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_paused_background_single_execution
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
import life_integration_ag2 as ag2  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

STAMP = "2026-10-24T12:00:00+00:00"
LATER = "2026-10-24T12:10:00+00:00"
DECISION_REF = "dec:paused_background_fixture"
ACT_A = "actv:background_a"
ACT_B = "actv:background_b"
TS = "2026-10-24T12:00:00+00:00"


class _DecisionReader:
    """AG-1/ADM read-port stand-in: only the fields ``ProvenanceVerifier`` reads."""

    def get_decision(self, decision_id: str):
        if decision_id != DECISION_REF:
            return None
        return {
            "decision_id": decision_id,
            "revision": 1,
            "snapshot_fingerprint": f"fp:{decision_id}",
        }


class _AdoptionReader:
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
    """One isolated canonical root with the real LR-2 and LR-4 owners wired together."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(
            tempfile.mkdtemp(prefix="paused-background-"))
        self.adoptions = _AdoptionReader()
        issuer = adm.build_admission_issuer(
            _DecisionReader(), self.adoptions, runtime_epoch="paused-background-test")
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.root,
            namespace=ac.NAMESPACE_ISOLATED_TEST,
            _admission_issuer=issuer,
        )
        capability = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.commands = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=capability)
        self.read = ac.ActivityReadService(self.store)
        self.coordinator = awa.WaitingCoordinator(
            activity_store=self.store,
            lease=self.lease,
            command_service=self.commands,
            namespace=ac.NAMESPACE_ISOLATED_TEST,
        )

    def close(self) -> None:
        if self.lease.held:
            self.lease.release()

    def state(self) -> dict:
        state, _ = self.store.load_verified_state_and_journal()
        return state

    def state_revision(self) -> int:
        return int(self.state()["revision"])

    def foreground(self):
        return self.state().get("foreground_activity_ref")

    def activity(self, activity_id: str) -> dict:
        return dict((self.state().get("activities") or {})[activity_id])

    def transitions(self, kind: str | None = None) -> list[dict]:
        _state, journal = self.store.load_verified_state_and_journal()
        return [row for row in journal if kind is None or row.get("transition_type") == kind]

    def start(self, activity_id: str, *, command_id: str | None = None) -> dict:
        command_id = command_id or "bg-start-" + activity_id.split(":")[-1]
        self.adoptions.grant(command_id, ac.TRANSITION_START, activity_id)
        result = self.commands.start_activity(
            activity_kind="READ_ARTIFACT",
            title="background pause fixture",
            origin_ref="artifact:backgroundfixture",
            source_refs=["artifact:backgroundfixture"],
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
            activity_id=activity_id,
        )
        return dict(result.activity)

    def pause(self, activity_id: str, *, command_id: str | None = None,
              expected_revision: int | None = None) -> dict:
        command_id = command_id or "bg-pause-" + activity_id.split(":")[-1]
        self.adoptions.grant(command_id, ac.TRANSITION_PAUSE, activity_id)
        result = self.commands.pause_activity(
            activity_id=activity_id,
            expected_revision=(self.state_revision()
                               if expected_revision is None else expected_revision),
            idempotency_key=command_id,
            source_refs=["decision:bgpause"],
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
        )
        return dict(result.activity)

    def resume(self, activity_id: str, *, command_id: str | None = None) -> dict:
        command_id = command_id or "bg-resume-" + activity_id.split(":")[-1]
        self.adoptions.grant(command_id, ac.TRANSITION_RESUME, activity_id)
        result = self.commands.resume_activity(
            activity_id=activity_id,
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            source_refs=["decision:bgresume"],
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
        )
        return dict(result.activity)

    def bind_time_condition(self, activity: dict, condition_id: str) -> dict:
        return self.coordinator.bind_pause_condition(
            activity_id=activity["activity_id"],
            expected_revision=int(activity["revision"]),
            waiting_on_ref="wait_dep:backgroundfixture",
            condition_kind=awa.COND_KIND_TIME_AT_OR_AFTER,
            parameters={"target_time": LATER},
            source_refs=["decision:bgcondition"],
            idempotency_key=f"bind:{condition_id}",
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:bind:{condition_id}",
            occurred_at=STAMP,
            observed_at=STAMP,
        )

    def seed_open_eligibility(self, activity: dict, eligibility_id: str) -> dict:
        record = awa.ResumeEligibility(
            eligibility_id=eligibility_id,
            schema_version=awa.SCHEMA_RESUME_ELIGIBILITY,
            revision=1,
            subject_id=ac.GLOBAL_SUBJECT_ID,
            activity_id=activity["activity_id"],
            activity_revision=int(activity["revision"]),
            waiting_episode_id="wep:backgroundfixture",
            resume_condition_ref="rcond:backgroundfixture",
            agenda_ref=None,
            source_event_refs=["evt:backgroundfixture"],
            causal_parent_refs=["rcond:backgroundfixture"],
            eligible_at=STAMP,
            recorded_at=STAMP,
            expires_at=None,
            policy_version=awa.POLICY_VERSION_LR4,
            status=awa.ELIG_STATUS_OPEN,
            idempotency_key=f"elig:{eligibility_id}",
        ).to_dict()
        self.coordinator.eligibility_store.commit_records(
            lease=self.lease,
            capability=self.coordinator.eligibility_capability,
            records=[record],
            action="BACKGROUND_FIXTURE_SEED_ELIGIBILITY",
            idempotency_key=f"seed:{eligibility_id}",
        )
        return record

    def eligibility(self, eligibility_id: str) -> dict:
        state = self.coordinator.eligibility_store.load_state() or {}
        return dict((state.get("eligibilities") or {})[eligibility_id])

    def conditions(self) -> dict:
        state = self.coordinator.condition_store.load_state() or {}
        return dict(state.get("conditions") or {})

    def reconcile(self) -> dict:
        return self.coordinator.reconcile_with_canonical_activities(now_at=STAMP)

    def resume_via_lr4(self, eligibility_id: str, activity_id: str, *,
                       command_id: str):
        self.adoptions.grant(command_id, ac.TRANSITION_RESUME, activity_id)
        return self.coordinator.explicit_resume_from_eligibility(
            eligibility_id=eligibility_id,
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
            occurred_at=LATER,
            observed_at=LATER,
        )


# ---------------------------------------------------------------------------
# AG-1: only the offers the owner will accept
# ---------------------------------------------------------------------------


def _candidate(candidate_id: str, kind: str, source: str, *, activity_ref=None,
               eligibility_ref=None, world_opportunity_ref=None,
               perception_ref=None, world_revision_ref=None) -> ag0.CandidateRecord:
    anchor = candidate_id.split(":", 1)[-1]
    return ag0.CandidateRecord(
        candidate_id=candidate_id,
        candidate_kind=kind,
        source_kind=source,
        source_ref="src:%s" % anchor,
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=TS,
        observed_at=TS,
        recorded_at=TS,
        idempotency_key="idem:%s" % anchor,
        activity_ref=activity_ref,
        resume_eligibility_ref=eligibility_ref,
        world_opportunity_ref=world_opportunity_ref,
        perception_ref=perception_ref,
        world_revision_ref=world_revision_ref,
        causal_parent_refs=["src:%s" % anchor],
        provenance_refs=["src:%s" % anchor],
    )


def _world_candidate() -> ag0.CandidateRecord:
    return _candidate(
        "cand:bg_world", ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
        ag0.SOURCE_WORLD_OPPORTUNITY, world_opportunity_ref="wopp:background",
        perception_ref="percept:background", world_revision_ref="wrev:background:1")


def _resume_candidate(activity_ref: str = ACT_A) -> ag0.CandidateRecord:
    return _candidate(
        "cand:bg_resume", ag0.CANDIDATE_KIND_RESUME_ACTIVITY,
        ag0.SOURCE_RESUME_ELIGIBLE, activity_ref=activity_ref,
        eligibility_ref="relig:background")


def _ag1_context(*, current_activity_ref=None, current_activity_status=None):
    return ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:background",
        opportunity_ref="dopp:background",
        candidate_set_ref="cset:background",
        life_frame_ref="lframe:background",
        captured_at=TS,
        source_revisions={},
        snapshot_fingerprint="sfp:background",
        current_activity_ref=current_activity_ref,
        current_activity_status=current_activity_status,
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
    )


def _ag1_capacity(candidates, context):
    return ag1.CapacityEvaluator.evaluate_capacity(
        allowed_candidates=list(candidates),
        context_snapshot=context,
        captured_at=TS,
        body_capacity_state="AVAILABLE",
        device_availability_state="AVAILABLE",
        resource_availability_state="AVAILABLE",
        privacy_availability_state="AVAILABLE",
        runtime_load_state="AVAILABLE",
    )


def _legal(candidate, context, capacity):
    return list(ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate(
        candidate, context_snapshot=context, capacity_snapshot=capacity))


# ---------------------------------------------------------------------------
# AG-2: real adoption coordinator with multiple Activities
# ---------------------------------------------------------------------------


class _Ag2DecisionReader:
    def __init__(self) -> None:
        self.decisions: dict[str, dict] = {}
        self.contexts: dict[str, dict] = {}
        self.opportunities: dict[str, dict] = {}

    def get_decision(self, decision_id: str):
        return dict(self.decisions.get(decision_id) or {}) or None

    def get_context_snapshot(self, context_id: str):
        return dict(self.contexts.get(context_id) or {}) or None

    def get_opportunity(self, opportunity_id: str):
        return dict(self.opportunities.get(opportunity_id) or {}) or None


class _Ag2AdoptionReader:
    def __init__(self, integration_store) -> None:
        self.integration_store = integration_store
        self.prepared: dict[str, dict] = {}
        self.next_id = 1

    def grant(self, command_id: str, mutation_kind: str, activity_ref: str) -> None:
        self.prepared["adp:" + command_id] = {
            "adoption_id": "adp:" + command_id,
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

    def get_adoption_by_id(self, adoption_id: str):
        state = self.integration_store.load_state() or {}
        live = (state.get("adoptions_by_id") or {}).get(adoption_id)
        if live is not None:
            return dict(live)
        return dict(self.prepared.get(adoption_id) or {}) or None


class _Ag2CandidateReader:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def validate_candidate(self, candidate_id: str, observed_at=None):
        return {"valid": True, "candidate": dict(self.rows.get(candidate_id) or {}),
                "source_revision": None}

    def get_candidate(self, candidate_id: str):
        return dict(self.rows.get(candidate_id) or {})


class _Ag2InterruptionPort:
    def __init__(self) -> None:
        self.mode = ag1.INTERRUPTIBILITY_IMMEDIATE

    def get_effective_attention(self, activity):
        return (None, self.mode, None)

    def configure_activity_attention(self, **kwargs):
        return None

    def assess_events(self, events, *, apply_immediate_pause=True):
        # The SAFE_BOUNDARY fixture records a pending interruption, not a PAUSE.
        return []


class _Ag2Harness:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="paused-background-ag2-"))
        self.integration_lease = ag2.IntegrationAuthorityLease(self.root)
        self.integration_lease.acquire()
        self.integration_store = ag2.IntegrationStore(
            self.root, namespace=ac.NAMESPACE_ISOLATED_TEST)
        capability = ag2.LifeIntegrationAuthority.issue_capability(
            self.integration_lease, namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.integration_commit = ag2.IntegrationCommitPort(
            self.integration_store, self.integration_lease, capability)
        self.decisions = _Ag2DecisionReader()
        self.adoptions = _Ag2AdoptionReader(self.integration_store)
        self.candidates = _Ag2CandidateReader()
        issuer = adm.build_admission_issuer(
            self.decisions, self.adoptions, runtime_epoch="paused-background-ag2-test")
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.root, namespace=ac.NAMESPACE_ISOLATED_TEST, _admission_issuer=issuer)
        cap = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.commands = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=cap)
        self.activity_read = ac.ActivityReadService(self.store)
        self.waiting = awa.WaitingCoordinator(
            activity_store=self.store,
            lease=self.lease,
            command_service=self.commands,
            namespace=ac.NAMESPACE_ISOLATED_TEST,
        )
        self.interruption = _Ag2InterruptionPort()
        self.adoption = ag2.DecisionAdoptionCoordinator(
            integration_commit=self.integration_commit,
            decision_read=self.decisions,
            candidate_read=self.candidates,
            activity_read=self.activity_read,
            activity_command=self.commands,
            interruption_coordinator=self.interruption,
            waiting_coordinator=self.waiting,
            progress_coordinator=None,
        )
        self.decisions.decisions[DECISION_REF] = {
            "decision_id": DECISION_REF,
            "revision": 1,
            "snapshot_fingerprint": f"fp:{DECISION_REF}",
        }

    def close(self) -> None:
        self.integration_store.flush_snapshot()
        if self.integration_lease.held:
            self.integration_lease.release()
        if self.lease.held:
            self.lease.release()

    def state(self) -> dict:
        state, _ = self.store.load_verified_state_and_journal()
        return state

    def state_revision(self) -> int:
        return int(self.state()["revision"])

    def foreground(self):
        return self.state().get("foreground_activity_ref")

    def activity(self, activity_id: str) -> dict:
        return dict((self.state().get("activities") or {})[activity_id])

    def transitions(self, kind: str) -> list[dict]:
        _state, journal = self.store.load_verified_state_and_journal()
        return [row for row in journal if row.get("transition_type") == kind]

    def start(self, activity_id: str) -> dict:
        command_id = "ag2-start-" + activity_id.split(":")[-1]
        self.adoptions.grant(command_id, ac.TRANSITION_START, activity_id)
        result = self.commands.start_activity(
            activity_kind="READ_ARTIFACT",
            title="ag2 background fixture",
            origin_ref="artifact:backgroundag2",
            source_refs=["artifact:backgroundag2"],
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            decision_ref=DECISION_REF,
            adoption_ref="adp:" + command_id,
            activity_id=activity_id,
        )
        return dict(result.activity)

    def conditions(self) -> dict:
        return dict((self.waiting.condition_store.load_state() or {}).get("conditions") or {})

    def eligibilities(self) -> dict:
        return dict((self.waiting.eligibility_store.load_state() or {}).get(
            "eligibilities") or {})

    def _register_decision(self, decision: ag1.DecisionRecord, *,
                            context_fg, context_rev: int,
                            candidate: dict | None = None) -> ag1.DecisionRecord:
        self.decisions.decisions[decision.decision_id] = decision.to_dict()
        self.decisions.contexts[decision.context_snapshot_ref] = {
            "current_activity_ref": context_fg,
            "current_activity_revision": context_rev,
            "candidate_revisions": {},
        }
        self.decisions.opportunities[decision.decision_opportunity_ref] = {"status": "OPEN"}
        if candidate is not None:
            self.candidates.rows[str(decision.selected_candidate_ref)] = dict(candidate)
        return decision

    def pause_decision(self, activity_id: str, *, decision_id: str, hint=None,
                       context_fg=None) -> ag1.DecisionRecord:
        decision = ag1.DecisionRecord(
            subject_id=ag1.GLOBAL_SUBJECT_ID,
            decision_id=decision_id,
            revision=1,
            decision_opportunity_ref="dopp:" + decision_id,
            candidate_set_ref="cset:" + decision_id,
            candidate_refs=(),
            context_snapshot_ref="dctx:" + decision_id,
            hard_constraint_result_refs=(),
            decision=ag1.DECISION_PAUSE,
            reason_codes=(ag1.REASON_SAFE_BOUNDARY_PAUSE_RECORDED,),
            decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
            policy_version=ag1.POLICY_VERSION_AG1_V1,
            snapshot_fingerprint="sfp:" + decision_id,
            created_at=STAMP,
            current_activity_ref=(
                activity_id if context_fg is None else context_fg),
            capacity_snapshot_ref="cap:" + decision_id,
            target_activity_ref=activity_id,
            reevaluation_hint_ref=hint,
        )
        return self._register_decision(
            decision,
            context_fg=(activity_id if context_fg is None else context_fg),
            context_rev=self.state_revision(),
        )

    def resume_decision(self, activity_id: str, eligibility_id: str, *,
                        decision_id: str, context_fg) -> ag1.DecisionRecord:
        candidate_id = "cand:" + decision_id
        candidate = {
            "candidate_id": candidate_id,
            "candidate_kind": ag0.CANDIDATE_KIND_RESUME_ACTIVITY,
            "source_kind": ag0.SOURCE_RESUME_ELIGIBLE,
            "activity_ref": activity_id,
            "resume_eligibility_ref": eligibility_id,
        }
        decision = ag1.DecisionRecord(
            subject_id=ag1.GLOBAL_SUBJECT_ID,
            decision_id=decision_id,
            revision=1,
            decision_opportunity_ref="dopp:" + decision_id,
            candidate_set_ref="cset:" + decision_id,
            candidate_refs=(candidate_id,),
            context_snapshot_ref="dctx:" + decision_id,
            hard_constraint_result_refs=(),
            decision=ag1.DECISION_RESUME,
            reason_codes=(ag1.REASON_RESUME_ELIGIBLE_READY,),
            decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
            policy_version=ag1.POLICY_VERSION_AG1_V1,
            snapshot_fingerprint="sfp:" + decision_id,
            created_at=STAMP,
            current_activity_ref=context_fg,
            selected_candidate_ref=candidate_id,
            capacity_snapshot_ref="cap:" + decision_id,
            target_activity_ref=activity_id,
        )
        return self._register_decision(
            decision, context_fg=context_fg, context_rev=self.state_revision(),
            candidate=candidate)

    def seed_open_eligibility(self, activity: dict, eligibility_id: str) -> None:
        record = awa.ResumeEligibility(
            eligibility_id=eligibility_id,
            schema_version=awa.SCHEMA_RESUME_ELIGIBILITY,
            revision=1,
            subject_id=ac.GLOBAL_SUBJECT_ID,
            activity_id=activity["activity_id"],
            activity_revision=int(activity["revision"]),
            waiting_episode_id="wep:backgroundag2",
            resume_condition_ref="rcond:backgroundag2",
            agenda_ref=None,
            source_event_refs=["evt:backgroundag2"],
            causal_parent_refs=["rcond:backgroundag2"],
            eligible_at=STAMP,
            recorded_at=STAMP,
            expires_at=None,
            policy_version=awa.POLICY_VERSION_LR4,
            status=awa.ELIG_STATUS_OPEN,
            idempotency_key=f"elig:{eligibility_id}",
        ).to_dict()
        self.waiting.eligibility_store.commit_records(
            lease=self.lease, capability=self.waiting.eligibility_capability,
            records=[record], action="AG2_BACKGROUND_FIXTURE_ELIGIBILITY",
            idempotency_key=f"seed:{eligibility_id}")

    def adopt(self, decision, *, pause_condition_spec=None, fault_hook=None,
              pause_mode_override=None):
        return self.adoption.adopt_decision(
            decision,
            pause_condition_spec=pause_condition_spec,
            fault_hook=fault_hook,
            pause_mode_override=pause_mode_override,
            occurred_at=STAMP,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@POSIX_ONLY
class BackgroundPauseMechanicsTest(unittest.TestCase):
    def make_harness(self) -> _Harness:
        harness = _Harness()
        self.addCleanup(harness.close)
        return harness

    def test_pause_releases_the_slot_and_two_suspended_activities_coexist(self):
        harness = self.make_harness()
        started_a = harness.start(ACT_A)
        self.assertEqual(harness.foreground(), ACT_A)

        paused_a = harness.pause(ACT_A)
        self.assertEqual(paused_a["status"], ac.STATUS_PAUSED)
        self.assertTrue(paused_a.get("foreground_released") is True)
        self.assertIsNone(harness.foreground())
        self.assertEqual(started_a["revision"] + 1, paused_a["revision"])
        self.assertTrue(paused_a.get("paused_at"))

        harness.start(ACT_B)
        state = harness.state()
        self.assertEqual(harness.foreground(), ACT_B)
        statuses = {aid: row["status"] for aid, row in state["activities"].items()}
        self.assertEqual(statuses, {ACT_A: ac.STATUS_PAUSED, ACT_B: ac.STATUS_ACTIVE})
        self.assertEqual(
            sum(1 for status in statuses.values() if status == ac.STATUS_ACTIVE), 1)

        paused_b = harness.pause(ACT_B)
        self.assertEqual(paused_b["status"], ac.STATUS_PAUSED)
        self.assertIsNone(harness.foreground())
        self.assertEqual(len(harness.transitions(ac.TRANSITION_PAUSE)), 2)
        self.assertEqual(harness.reconcile()["invalidated_conditions"], 0)

    def test_conditions_on_two_suspended_activities_survive_reconcile_and_restart(self):
        harness = self.make_harness()
        harness.start(ACT_A)
        paused_a = harness.pause(ACT_A)
        harness.bind_time_condition(paused_a, "rcond:background_a")
        harness.start(ACT_B)
        paused_b = harness.pause(ACT_B)
        harness.bind_time_condition(paused_b, "rcond:background_b")
        self.assertEqual(len(harness.conditions()), 2)
        self.assertEqual(harness.reconcile()["invalidated_conditions"], 0)
        self.assertEqual(
            {row["status"] for row in harness.conditions().values()}, {awa.COND_STATUS_PENDING})

        root = harness.root
        harness.close()
        restarted = _Harness(root)
        self.addCleanup(restarted.close)
        state = restarted.state()
        self.assertIsNone(state["foreground_activity_ref"])
        self.assertEqual(
            {row["status"] for row in state["activities"].values()}, {ac.STATUS_PAUSED})
        self.assertEqual(len(restarted.conditions()), 2)
        self.assertEqual(restarted.reconcile()["invalidated_conditions"], 0)
        self.assertEqual(restarted.transitions(ac.TRANSITION_PAUSE).__len__(), 2)

        restarted.seed_open_eligibility(restarted.activity(ACT_A), "relig:background_a")
        resumed = restarted.resume_via_lr4(
            "relig:background_a", ACT_A, command_id="bg-resume-a-after-restart")
        self.assertEqual(resumed.activity["status"], ac.STATUS_ACTIVE)
        self.assertEqual(restarted.foreground(), ACT_A)
        self.assertEqual(
            restarted.eligibility("relig:background_a")["status"], awa.ELIG_STATUS_CONSUMED)
        self.assertEqual(restarted.activity(ACT_B)["status"], ac.STATUS_PAUSED)

    def test_background_resume_is_refused_with_zero_side_effects_while_another_runs(self):
        harness = self.make_harness()
        harness.start(ACT_A)
        paused_a = harness.pause(ACT_A)
        harness.seed_open_eligibility(paused_a, "relig:background_a")
        harness.start(ACT_B)
        before = harness.state()
        transition_count = len(harness.transitions(ac.TRANSITION_RESUME))

        # The eligibility was "shown" (read) before B took the slot: a display is not a grant.
        self.assertEqual(
            harness.eligibility("relig:background_a")["status"], awa.ELIG_STATUS_OPEN)
        for attempt in ("bg-resume-a-1", "bg-resume-a-2"):
            with self.assertRaises(ac.ForegroundConflictError):
                harness.resume_via_lr4(
                    "relig:background_a", ACT_A, command_id=attempt)

        after = harness.state()
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(harness.foreground(), ACT_B)
        self.assertEqual(harness.activity(ACT_B)["status"], ac.STATUS_ACTIVE)
        self.assertEqual(harness.activity(ACT_A), before["activities"][ACT_A])
        self.assertEqual(
            harness.eligibility("relig:background_a")["status"], awa.ELIG_STATUS_OPEN)
        self.assertEqual(len(harness.transitions(ac.TRANSITION_RESUME)), transition_count)
        self.assertEqual(len(harness.transitions(ac.TRANSITION_PAUSE)), 1)

    def test_with_a_free_slot_the_same_resume_returns_to_the_same_activity(self):
        harness = self.make_harness()
        harness.start(ACT_A)
        paused_a = harness.pause(ACT_A)
        harness.seed_open_eligibility(paused_a, "relig:background_a")
        harness.start(ACT_B)
        paused_b = harness.pause(ACT_B)

        resumed = harness.resume_via_lr4(
            "relig:background_a", ACT_A, command_id="bg-resume-a-free")
        self.assertEqual(resumed.activity["activity_id"], ACT_A)
        self.assertEqual(resumed.activity["status"], ac.STATUS_ACTIVE)
        self.assertEqual(harness.foreground(), ACT_A)
        self.assertEqual(harness.activity(ACT_B)["status"], ac.STATUS_PAUSED)
        self.assertEqual(
            harness.eligibility("relig:background_a")["status"], awa.ELIG_STATUS_CONSUMED)

    def test_an_unconditional_pause_releases_but_emits_nothing(self):
        harness = self.make_harness()
        harness.start(ACT_A)
        harness.pause(ACT_A)
        self.assertIsNone(harness.foreground())
        self.assertEqual(harness.conditions(), {})
        self.assertEqual(harness.coordinator.eligibility_store.load_state().get(
            "eligibilities") or {}, {})
        self.assertEqual(harness.reconcile()["invalidated_eligibilities"], 0)

    def test_a_wrong_revision_pause_releases_nothing(self):
        harness = self.make_harness()
        started = harness.start(ACT_A)
        with self.assertRaises(ac.RevisionConflictError):
            harness.pause(ACT_A, expected_revision=started["revision"] - 1)
        self.assertEqual(harness.foreground(), ACT_A)
        after = harness.activity(ACT_A)
        self.assertEqual(after["status"], ac.STATUS_ACTIVE)
        self.assertEqual(after["revision"], started["revision"])
        self.assertFalse(after.get("foreground_released") is True)


@POSIX_ONLY
class BackgroundPauseAg1Test(unittest.TestCase):
    def test_start_and_resume_are_offered_only_when_the_slot_is_free(self):
        world = _world_candidate()
        # A different Activity from every foreground used below: resuming the foreground
        # itself is the old, still-legal path and is not what this case is about.
        resume = _resume_candidate("actv:background_c")

        idle = _ag1_context()
        idle_capacity = _ag1_capacity([world, resume], idle)
        self.assertIn(ag1.DECISION_START, _legal(world, idle, idle_capacity))
        self.assertIn(ag1.DECISION_RESUME, _legal(resume, idle, idle_capacity))

        busy = _ag1_context(current_activity_ref=ACT_B,
                            current_activity_status=ag1.STATUS_ACTIVE)
        busy_capacity = _ag1_capacity([world, resume], busy)
        self.assertNotIn(ag1.DECISION_START, _legal(world, busy, busy_capacity))
        self.assertNotIn(ag1.DECISION_RESUME, _legal(resume, busy, busy_capacity))

        legacy = _ag1_context(current_activity_ref=ACT_A,
                              current_activity_status=ag1.STATUS_PAUSED)
        legacy_capacity = _ag1_capacity([world, resume], legacy)
        # A legacy PAUSED pointer is not migrated by a read; it still occupies the slot.
        self.assertNotIn(ag1.DECISION_START, _legal(world, legacy, legacy_capacity))
        self.assertNotIn(ag1.DECISION_RESUME, _legal(resume, legacy, legacy_capacity))


@POSIX_ONLY
class BackgroundPauseAg2Test(unittest.TestCase):
    def make_harness(self) -> _Ag2Harness:
        harness = _Ag2Harness()
        self.addCleanup(harness.close)
        return harness

    def test_a_safe_boundary_pause_does_not_release_the_slot_early(self):
        harness = self.make_harness()
        active = harness.start(ACT_A)
        decision = harness.pause_decision(ACT_A, decision_id="dec:bg-safe")

        result = harness.adopt(decision, pause_mode_override=ag2.INTERRUPTIBILITY_SAFE_BOUNDARY)

        after = harness.activity(ACT_A)
        self.assertEqual(after["status"], ac.STATUS_ACTIVE)
        self.assertEqual(after["revision"], active["revision"])
        self.assertEqual(harness.foreground(), ACT_A)
        self.assertEqual([], harness.transitions(ac.TRANSITION_PAUSE))
        self.assertEqual({}, harness.conditions())
        # A pending boundary decision never becomes a PAUSE transition or a release.
        self.assertIsNotNone(result)

    def test_an_atomic_foreground_refuses_pause_before_any_mutation(self):
        harness = self.make_harness()
        active = harness.start(ACT_A)
        harness.interruption.mode = ag1.INTERRUPTIBILITY_ATOMIC
        decision = harness.pause_decision(ACT_A, decision_id="dec:bg-atomic")

        result = harness.adopt(decision)

        after = harness.activity(ACT_A)
        self.assertEqual(after["status"], ac.STATUS_ACTIVE)
        self.assertEqual(after["revision"], active["revision"])
        self.assertEqual(harness.foreground(), ACT_A)
        self.assertEqual([], harness.transitions(ac.TRANSITION_PAUSE))
        self.assertEqual({}, harness.conditions())
        self.assertNotEqual("APPLIED", result.get("adoption_record").adoption_status)

    def test_binding_recovery_targets_the_suspended_activity_not_the_new_foreground(self):
        harness = self.make_harness()
        harness.start(ACT_A)
        decision = harness.pause_decision(
            ACT_A, decision_id="dec:bg-recover", hint="waitbasis:bg-recover")
        spec = {
            "basis_ref": "waitbasis:bg-recover",
            "waiting_on_ref": "wait_dep:bg-recover",
            "condition_kind": awa.COND_KIND_TIME_AT_OR_AFTER,
            "parameters": {"target_time": LATER},
        }

        def fault_hook(name: str) -> None:
            if name == "before_condition_commit":
                raise RuntimeError("CRASH_INJECTED:before_condition_commit")

        with self.assertRaises(RuntimeError):
            harness.adopt(decision, pause_condition_spec=spec, fault_hook=fault_hook)
        paused_a = harness.activity(ACT_A)
        self.assertEqual(paused_a["status"], ac.STATUS_PAUSED)
        self.assertIsNone(harness.foreground())
        self.assertEqual({}, harness.conditions())

        # A different Activity takes the now-free slot before the same Decision is retried.
        harness.start(ACT_B)
        self.assertEqual(harness.foreground(), ACT_B)
        b_before = harness.activity(ACT_B)

        retried = harness.adopt(decision, pause_condition_spec=spec)
        self.assertEqual("APPLIED", retried["adoption_record"].adoption_status)
        self.assertEqual(harness.activity(ACT_B), b_before)
        self.assertEqual(harness.foreground(), ACT_B)
        self.assertEqual(harness.activity(ACT_A), paused_a)
        self.assertEqual(len(harness.transitions(ac.TRANSITION_PAUSE)), 1)
        conditions = harness.conditions()
        self.assertEqual(len(conditions), 1)
        bound = next(iter(conditions.values()))
        self.assertTrue(awa.is_pause_bound_condition(bound))
        self.assertEqual(bound["activity_id"], ACT_A)
        self.assertEqual(int(bound["activity_revision_at_bind"]), int(paused_a["revision"]))
        self.assertEqual({}, harness.eligibilities())

    def test_resume_adoption_refuses_instead_of_pausing_the_running_activity(self):
        harness = self.make_harness()
        harness.start(ACT_A)
        paused_a = harness.adopt(
            harness.pause_decision(ACT_A, decision_id="dec:bg-pause-a"))
        self.assertEqual(harness.activity(ACT_A)["status"], ac.STATUS_PAUSED)
        harness.seed_open_eligibility(paused_a["activity"], "relig:bg-a")

        harness.start(ACT_B)
        b_before = harness.activity(ACT_B)
        eligibility_before = dict(
            harness.eligibilities()["relig:bg-a"])
        decision = harness.resume_decision(
            ACT_A, "relig:bg-a", decision_id="dec:bg-resume-a", context_fg=ACT_B)

        with self.assertRaises(ag2.AdoptionIllegalError) as caught:
            harness.adopt(decision)
        self.assertIn("FOREGROUND_CONFLICT_ON_RESUME", str(caught.exception))

        self.assertEqual(harness.activity(ACT_B), b_before)
        self.assertEqual(harness.foreground(), ACT_B)
        self.assertEqual(harness.activity(ACT_A)["status"], ac.STATUS_PAUSED)
        self.assertEqual(harness.eligibilities()["relig:bg-a"], eligibility_before)
        self.assertEqual([], harness.transitions(ac.TRANSITION_RESUME))


if __name__ == "__main__":
    unittest.main(verbosity=2)
