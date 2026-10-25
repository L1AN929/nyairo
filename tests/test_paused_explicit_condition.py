#!/usr/bin/env python3
"""Explicit-condition PAUSE: PAUSE -> owner-checked condition -> OPEN -> new RESUME Decision.

What is real here
-----------------
* the **owners** are real and in-process: ``ActivityAuthorityLease``,
  ``CanonicalActivityStore``, ``ActivityCommandService`` (LR-2) and
  ``WaitingCoordinator`` with its three stores (LR-4), all on an isolated temporary root;
* every Activity transition goes through LR-2's own admission issuer, which verifies the
  Decision and Adoption read ports.  Those two ports are the only stand-ins: they answer with
  exactly the fields ``ProvenanceVerifier`` reads, so grant/scope/fingerprint checks still run;
* the condition is created **from a real PAUSE** through the new
  ``WaitingCoordinator.bind_pause_condition``; its baseline comes from the real
  ``life_wait_basis`` vocabulary (the same one the cycle offers for WAIT), and the satisfying
  event is the module's own ``head_change_event`` (a different owner head), never a hand-made
  OPEN eligibility.

What this does not claim
------------------------
* no model and no product instance: the Decision payloads below are test values, and this file
  does not show that a real model naturally chooses a conditional PAUSE;
* there is no timing/retirement case: a PAUSE condition carries no deadline by construction, and
  practices/retirement are not part of this condition's vocabulary, so they are not tested here;
* WAITING itself is unchanged: the existing suites are the regression, and one case below still
  exercises a WAITING condition end to end.

Run:  python3 -B -m unittest -q tests.test_paused_explicit_condition
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
import life_wait_basis as lwb  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

STAMP = "2026-10-24T00:00:00+00:00"
OBSERVED = "2026-10-24T00:00:05+00:00"
LATER = "2026-10-24T00:00:10+00:00"
DECISION_REF = "dec:pausecondfixture"
ADOPTION_REF = "adp:pausecondfixture"
ACTIVITY_ID = "actv:pausecondfixture"
BASIS = lwb.work_head_basis(
    artifact_id="artifact:pausecondfixture", baseline_head="version:1",
    activity_ref=ACTIVITY_ID)


class _DecisionReader:
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
    """One isolated root with the real LR-2/LR-4 owners wired together."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="paused-explicit-condition-"))
        self.adoptions = _AdoptionReader()
        issuer = adm.build_admission_issuer(
            _DecisionReader(), self.adoptions, runtime_epoch="paused-condition-test")
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.root, namespace=ac.NAMESPACE_ISOLATED_TEST, _admission_issuer=issuer)
        capability = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.commands = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=capability)
        self.coordinator = self.new_coordinator()

    def new_coordinator(self) -> awa.WaitingCoordinator:
        """A *fresh* LR-4 object over the same canonical root/stores (restart shape)."""

        return awa.WaitingCoordinator(
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

    def activity(self, activity_id: str = ACTIVITY_ID) -> dict:
        state, _ = self.store.load_verified_state_and_journal()
        return dict((state.get("activities") or {})[activity_id])

    def start(self, activity_id: str = ACTIVITY_ID, *, command_id: str = "pc-start") -> dict:
        self._grant(command_id, ac.TRANSITION_START, activity_id)
        result = self.commands.start_activity(
            activity_kind="READ_ARTIFACT",
            title="explicit-condition pause fixture",
            origin_ref="artifact:pausecondfixture",
            source_refs=["artifact:pausecondfixture"],
            subject_refs=["artifact:pausecondfixture"],
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
            activity_id=activity_id,
        )
        return dict(result.activity)

    def pause(self, activity_id: str = ACTIVITY_ID, *, command_id: str = "pc-pause") -> dict:
        self._grant(command_id, ac.TRANSITION_PAUSE, activity_id)
        result = self.commands.pause_activity(
            activity_id=activity_id,
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            source_refs=["decision:pause-cond"],
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
        )
        return dict(result.activity)

    def abandon(self, activity_id: str = ACTIVITY_ID, *, command_id: str = "pc-abandon") -> dict:
        self._grant(command_id, ac.TRANSITION_ABANDON, activity_id)
        result = self.commands.abandon_activity(
            activity_id=activity_id,
            expected_revision=self.state_revision(),
            idempotency_key=command_id,
            source_refs=["decision:pause-cond"],
            decision_ref=DECISION_REF,
            adoption_ref=f"adp:{command_id}",
        )
        return dict(result.activity)

    # -- LR-4 condition binding --------------------------------------------

    def bind(self, *, coordinator=None, activity: dict | None = None,
             idempotency_key: str = "pause-cond-bind", **overrides) -> dict:
        coordinator = coordinator or self.coordinator
        activity = activity or self.activity()
        payload = {
            "activity_id": activity["activity_id"],
            "expected_revision": int(activity["revision"]),
            "waiting_on_ref": BASIS["waiting_on_ref"],
            "condition_kind": lwb.CONDITION_KIND,
            "parameters": lwb.condition_parameters(BASIS),
            "source_refs": [DECISION_REF, ADOPTION_REF],
            "idempotency_key": idempotency_key,
            "causal_parent_refs": [DECISION_REF],
            "occurred_at": STAMP,
            "observed_at": STAMP,
            "decision_ref": DECISION_REF,
            "adoption_ref": ADOPTION_REF,
        }
        payload.update(overrides)
        return coordinator.bind_pause_condition(**payload)

    # -- reads --------------------------------------------------------------

    def conditions(self, coordinator=None) -> dict:
        state = (coordinator or self.coordinator).condition_store.load_state()
        return dict(state.get("conditions") or {})

    def eligibilities(self, coordinator=None) -> dict:
        state = (coordinator or self.coordinator).eligibility_store.load_state()
        return dict(state.get("eligibilities") or {})

    def head_event(self, observed: str = "version:2", at: str = OBSERVED) -> dict:
        event = lwb.head_change_event(BASIS, current_head=observed, observed_at=at)
        assert event is not None
        return event

    def resume(self, eligibility_id: str, *, command_id: str = "pc-resume"):
        self._grant(command_id, ac.TRANSITION_RESUME, ACTIVITY_ID)
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
# AG-1 fixtures: only the raw PAUSE payload matters
# ---------------------------------------------------------------------------


def _context(*, status: str = ac.STATUS_ACTIVE, basis: str | None = None):
    return ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:pausecond",
        opportunity_ref="dopp:pausecond",
        candidate_set_ref="cset:pausecond",
        life_frame_ref="lframe:pausecond",
        captured_at=STAMP,
        source_revisions={},
        snapshot_fingerprint="sfp:pausecond",
        current_activity_ref=ACTIVITY_ID,
        current_activity_status=status,
        current_activity_revision=2,
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
        known_waiting_basis_ref=basis,
        observed_facts={},
    )


def _capacity():
    return ag1.AgencyCapacitySnapshot(
        capacity_snapshot_id="cap:pausecond",
        captured_at=STAMP,
        source_revisions=["snapshot:pausecond"],
        candidate_executability={},
    )


def _candidate_set():
    return ag0.CandidateSet(
        candidate_set_id="cset:pausecond",
        subject_id=ag0.GLOBAL_SUBJECT_ID,
        candidate_refs=[],
        source_snapshot_refs=[],
        observed_at=STAMP,
        built_at=STAMP,
    )


def _pause_payload(hint: str | None) -> dict:
    payload = {
        "decision": "PAUSE",
        "selected_candidate_id": None,
        "reason_codes": [ag1.REASON_SAFE_BOUNDARY_PAUSE_RECORDED],
        "short_rationale": "pause until the offered condition holds",
        "deferred_candidate_ids": [],
    }
    if hint is not None:
        payload["reevaluation_hint_ref"] = hint
    return payload


def _validate(payload: dict, *, status: str = ac.STATUS_ACTIVE, basis: str | None = None):
    return ag1.DecisionOutputValidator.validate_decision_output(
        payload,
        candidate_set=_candidate_set(),
        candidates_by_id={},
        constraint_results_by_id={},
        context_snapshot=_context(status=status, basis=basis),
        capacity_snapshot=_capacity(),
        allowed_method_refs=None,
    )


@POSIX_ONLY
class PausedExplicitConditionTest(unittest.TestCase):
    def make_harness(self) -> _Harness:
        harness = _Harness()
        self.addCleanup(harness.close)
        return harness

    # -- binding ------------------------------------------------------------

    def test_a_real_pause_binds_one_owner_checkable_condition_without_transitioning(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        self.assertEqual(paused["status"], ac.STATUS_PAUSED)

        bound = harness.bind(activity=paused)
        self.assertFalse(bound["idempotent_replay"])
        conditions = harness.conditions()
        self.assertEqual(1, len(conditions))
        condition = next(iter(conditions.values()))
        self.assertEqual(awa.COND_STATUS_PENDING, condition["status"])
        self.assertEqual(int(paused["revision"]), condition["activity_revision_at_bind"])
        self.assertTrue(awa.is_pause_bound_condition(condition))
        self.assertEqual(BASIS["waiting_on_ref"], condition["waiting_on_ref"])
        self.assertEqual(lwb.condition_parameters(BASIS),
                         {k: v for k, v in condition["parameters"].items()
                          if k != awa.PARAM_PAUSE_BOUND})
        self.assertTrue(awa.condition_host_is_live(condition, harness.activity()))
        # The owner's Activity did not move: a condition is not a second transition.
        after = harness.activity()
        self.assertEqual(paused["status"], after["status"])
        self.assertEqual(paused["revision"], after["revision"])
        self.assertEqual([], list(harness.eligibilities()))

    def test_the_same_binding_twice_is_idempotent_and_creates_one_condition(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        first = harness.bind(activity=paused)
        second = harness.bind(activity=paused)
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["resume_condition"]["condition_id"],
                         second["resume_condition"]["condition_id"])
        self.assertEqual(1, len(harness.conditions()))

    def test_binding_needs_the_exact_paused_revision_and_a_decision_and_adoption(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        with self.assertRaises(awa.StaleResumeConditionError):
            harness.bind(activity=paused, expected_revision=int(paused["revision"]) - 1)
        with self.assertRaises(awa.WriterCapabilityError):
            harness.bind(activity=paused, decision_ref=None)
        with self.assertRaises(awa.WriterCapabilityError):
            harness.bind(activity=paused, adoption_ref=None)
        self.assertEqual({}, harness.conditions())

    def test_binding_refuses_active_and_waiting_activities(self):
        harness = self.make_harness()
        active = harness.start()
        with self.assertRaises(awa.WaitingAgendaError):
            harness.bind(activity=active)
        harness._grant("pc-wait", ac.TRANSITION_WAIT, ACTIVITY_ID)
        waited = harness.coordinator.enter_waiting_with_condition(
            activity_id=ACTIVITY_ID,
            expected_revision=int(active["revision"]),
            waiting_on_ref=BASIS["waiting_on_ref"],
            condition_kind=lwb.CONDITION_KIND,
            parameters=lwb.condition_parameters(BASIS),
            source_refs=[DECISION_REF, ADOPTION_REF],
            idempotency_key="pc-wait",
            causal_parent_refs=[DECISION_REF],
            occurred_at=STAMP,
            observed_at=STAMP,
            reason_kind=awa.REASON_KIND_AGENCY_CHOICE,
            decision_ref=DECISION_REF,
            adoption_ref="adp:pc-wait",
        )
        self.assertEqual(ac.STATUS_WAITING, waited["activity"]["status"])
        with self.assertRaises(awa.WaitingAgendaError):
            harness.bind(activity=waited["activity"])

    # -- the condition lifecycle -------------------------------------------

    def test_a_really_changed_owner_head_opens_one_eligibility_that_new_resume_consumes(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        bound = harness.bind(activity=paused)

        outcome = harness.coordinator.evaluate_event(harness.head_event())
        self.assertEqual([bound["resume_condition"]["condition_id"]],
                         outcome["satisfied_condition_ids"])
        self.assertEqual(1, len(outcome["emitted_eligibilities"]))
        self.assertFalse(outcome["auto_resumed"])
        self.assertEqual(0, outcome["llm_calls"])
        conditions = harness.conditions()
        self.assertEqual(awa.COND_STATUS_SATISFIED,
                         next(iter(conditions.values()))["status"])
        eligibilities = harness.eligibilities()
        self.assertEqual(1, len(eligibilities))
        eligibility = next(iter(eligibilities.values()))
        self.assertEqual(awa.ELIG_STATUS_OPEN, eligibility["status"])
        # The owner activity is still exactly PAUSED at the same revision.
        self.assertEqual(ac.STATUS_PAUSED, harness.activity()["status"])
        self.assertEqual(paused["revision"], harness.activity()["revision"])

        # Reconcile must not invalidate the PAUSED host's eligibility.
        reconciled = harness.coordinator.reconcile_with_canonical_activities(now_at=LATER)
        self.assertEqual(0, reconciled["invalidated_eligibilities"])
        self.assertEqual(awa.ELIG_STATUS_OPEN,
                         harness.eligibilities()[eligibility["eligibility_id"]]["status"])

        resumed = harness.resume(eligibility["eligibility_id"])
        self.assertEqual(ac.STATUS_ACTIVE, resumed.activity["status"])
        self.assertEqual(ACTIVITY_ID, resumed.activity["activity_id"])
        after = harness.eligibilities()[eligibility["eligibility_id"]]
        self.assertEqual(awa.ELIG_STATUS_CONSUMED, after["status"])
        self.assertEqual("CONSUMED_BY_EXPLICIT_RESUME", after["resolution_reason_code"])

    def test_a_same_head_or_unknown_read_or_wrong_ref_emits_nothing(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        harness.bind(activity=paused)

        self.assertIsNone(lwb.head_change_event(
            BASIS, current_head="version:1", observed_at=OBSERVED))
        self.assertIsNone(lwb.head_change_event(
            BASIS, current_head=None, observed_at=OBSERVED))
        # A latest body read that is not FRESH is never "eased".
        body = lwb.body_capacity_basis(
            body_id="body:fixture", revision="r1", capacity="LIMITED",
            burden=[{"signal": "fatigue", "band": "strong"}],
            observed_at=STAMP, activity_ref=ACTIVITY_ID)
        self.assertIsNone(lwb.body_easing_event(
            body, observed_body_id="body:fixture", observed_revision="r2",
            burden=[], recovery_progress="strong", freshness="STALE",
            observed_at=OBSERVED))

        wrong = dict(harness.head_event())
        wrong["event_id"] = "evt:wrongref"
        wrong["waiting_on_ref"] = "wait_dep:artifact.other.token"
        wrong["source_refs"] = ["evt:wrongref"]
        outcome = harness.coordinator.evaluate_event(wrong)
        self.assertEqual([], outcome["satisfied_condition_ids"])
        self.assertEqual([], outcome["emitted_eligibilities"])
        self.assertEqual({}, harness.eligibilities())
        self.assertEqual(awa.COND_STATUS_PENDING,
                         next(iter(harness.conditions().values()))["status"])

    def test_the_same_satisfying_event_twice_emits_no_second_eligibility(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        harness.bind(activity=paused)
        event = harness.head_event()

        first = harness.coordinator.evaluate_event(event)
        second = harness.coordinator.evaluate_event(event)
        self.assertEqual(1, len(first["emitted_eligibilities"]))
        self.assertEqual([], second["satisfied_condition_ids"])
        self.assertEqual([], second["emitted_eligibilities"])
        self.assertEqual(1, len(harness.eligibilities()))

    def test_restart_on_the_same_root_keeps_the_binding_and_does_not_replay_it(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        bound = harness.bind(activity=paused)

        restarted = harness.new_coordinator()
        conditions = harness.conditions(restarted)
        self.assertEqual(1, len(conditions))
        condition = next(iter(conditions.values()))
        self.assertEqual(bound["resume_condition"]["condition_id"], condition["condition_id"])
        self.assertTrue(awa.is_pause_bound_condition(condition))
        self.assertTrue(awa.condition_host_is_live(condition, harness.activity()))

        event = harness.head_event()
        first = restarted.evaluate_event(event)
        second = restarted.evaluate_event(event)
        self.assertEqual(1, len(first["emitted_eligibilities"]))
        self.assertEqual([], second["emitted_eligibilities"])
        self.assertEqual(1, len(harness.eligibilities(restarted)))

    def test_a_terminal_activity_cancels_the_open_pause_condition_and_emits_nothing(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        harness.bind(activity=paused)
        harness.abandon()

        reconciled = harness.coordinator.reconcile_with_canonical_activities(now_at=LATER)
        self.assertEqual(1, reconciled["invalidated_conditions"])
        condition = next(iter(harness.conditions().values()))
        self.assertEqual(awa.COND_STATUS_CANCELLED, condition["status"])
        self.assertEqual("TERMINAL_ACTIVITY_INVALIDATED",
                         condition["resolution_reason_code"])
        outcome = harness.coordinator.evaluate_event(harness.head_event())
        self.assertEqual([], outcome["emitted_eligibilities"])
        self.assertEqual({}, harness.eligibilities())

    def test_a_self_chosen_time_condition_is_evaluated_only_at_its_explicit_instant(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        target = "2026-10-24T00:05:00+00:00"
        harness.bind(
            activity=paused,
            waiting_on_ref="time:1792800300",
            condition_kind=awa.COND_KIND_TIME_AT_OR_AFTER,
            parameters={"target_time": target},
        )
        before = harness.coordinator.scan_due_agenda(now_at=STAMP)
        self.assertEqual([], before["emitted_eligibilities"])
        self.assertEqual(awa.COND_STATUS_PENDING,
                         next(iter(harness.conditions().values()))["status"])
        due = harness.coordinator.scan_due_agenda(now_at=target)
        self.assertEqual(1, len(due["emitted_eligibilities"]))
        self.assertEqual(ac.STATUS_PAUSED, harness.activity()["status"])
        eligibility = next(iter(harness.eligibilities().values()))
        self.assertEqual(awa.ELIG_STATUS_OPEN, eligibility["status"])

    def test_an_unreadable_target_source_supplies_no_resume_candidate(self):
        harness = self.make_harness()
        harness.start()
        paused = harness.pause()
        harness.bind(activity=paused)
        harness.coordinator.evaluate_event(harness.head_event())
        eligibility = next(iter(harness.eligibilities().values()))

        class _RefusingTargetRead:
            def read_artifact(self, artifact_id):
                raise RuntimeError("owner refused the read")

        source = ag0.ResumeEligibleCandidateSource(
            harness.coordinator, ac.ActivityReadService(harness.store),
            target_read=_RefusingTargetRead())
        ok, reason = source.validate_source(eligibility, observed_at=LATER)
        self.assertFalse(ok)
        self.assertEqual("TARGET_SOURCE_UNREADABLE", reason)
        items, _snap, _meta = source.discover(observed_at=LATER)
        self.assertEqual([], items)
        # The eligibility itself is untouched: this is a source refusal, not a consumed one.
        self.assertEqual(awa.ELIG_STATUS_OPEN,
                         harness.eligibilities()[eligibility["eligibility_id"]]["status"])

    def test_a_healthy_waiting_condition_still_uses_the_waiting_path(self):
        harness = self.make_harness()
        active = harness.start()
        harness._grant("pc-wait-2", ac.TRANSITION_WAIT, ACTIVITY_ID)
        waited = harness.coordinator.enter_waiting_with_condition(
            activity_id=ACTIVITY_ID,
            expected_revision=int(active["revision"]),
            waiting_on_ref=BASIS["waiting_on_ref"],
            condition_kind=lwb.CONDITION_KIND,
            parameters=lwb.condition_parameters(BASIS),
            source_refs=[DECISION_REF, ADOPTION_REF],
            idempotency_key="pc-wait-2",
            causal_parent_refs=[DECISION_REF],
            occurred_at=STAMP,
            observed_at=STAMP,
            reason_kind=awa.REASON_KIND_AGENCY_CHOICE,
            decision_ref=DECISION_REF,
            adoption_ref="adp:pc-wait-2",
        )
        self.assertEqual(ac.STATUS_WAITING, waited["activity"]["status"])
        condition = next(iter(harness.conditions().values()))
        self.assertFalse(awa.is_pause_bound_condition(condition))
        self.assertTrue(awa.condition_host_is_live(condition, harness.activity()))

        outcome = harness.coordinator.evaluate_event(harness.head_event())
        self.assertEqual(1, len(outcome["emitted_eligibilities"]))
        eligibility = next(iter(harness.eligibilities().values()))
        self.assertEqual(awa.ELIG_STATUS_OPEN, eligibility["status"])
        self.assertEqual(ac.STATUS_WAITING, harness.activity()["status"])

    # -- AG-1 explicit selection -------------------------------------------

    def test_ag1_accepts_the_offered_condition_and_carries_it_on_the_decision(self):
        validated = _validate(_pause_payload(BASIS["basis_ref"]), basis=BASIS["basis_ref"])
        self.assertEqual(ag1.DECISION_PAUSE, validated["decision"])
        self.assertEqual(BASIS["basis_ref"], validated["reevaluation_hint_ref"])

    def test_ag1_keeps_unconditional_pause_legal(self):
        validated = _validate(_pause_payload(None))
        self.assertEqual(ag1.DECISION_PAUSE, validated["decision"])
        self.assertIsNone(validated["reevaluation_hint_ref"])

    def test_ag1_refuses_a_condition_this_round_did_not_offer(self):
        cases = (
            (_pause_payload("waitbasis:notoffered"), BASIS["basis_ref"]),
            (_pause_payload(None) | {"reevaluation_hint_ref": BASIS["basis_ref"]}, None),
            (_pause_payload("not a structured ref"), BASIS["basis_ref"]),
        )
        for payload, basis in cases:
            with self.assertRaises(ag1.DecisionValidationError) as caught:
                _validate(payload, basis=basis)
            self.assertIn("PAUSE_CONDITION_NOT_OFFERED", str(caught.exception))


# ---------------------------------------------------------------------------
# AG-2 adoption edges: refusal before any mutation, and recovery of the one
# binding after the pause itself has already been committed.
# ---------------------------------------------------------------------------


PAUSE_SPEC = {
    "basis_ref": BASIS["basis_ref"],
    "waiting_on_ref": BASIS["waiting_on_ref"],
    "condition_kind": lwb.CONDITION_KIND,
    "parameters": lwb.condition_parameters(BASIS),
}
AG2_DECISION_ID = "dec:pausecondag2"
AG2_OPP_ID = "dopp:pausecondag2"
AG2_CTX_ID = "dctx:pausecondag2"


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
    """Live AG-2 authorisations first, plus the fixture's own START grant."""

    def __init__(self, integration_store) -> None:
        self.integration_store = integration_store
        self.prepared: dict[str, dict] = {}

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
    """PAUSE names no candidate; the freshness check still asks about the selected one."""

    def validate_candidate(self, candidate_id: str, observed_at=None):
        return {"valid": True, "candidate": {}, "source_revision": None}

    def get_candidate(self, candidate_id: str):
        return {}


class _Ag2InterruptionPort:
    def __init__(self) -> None:
        self.mode = ag1.INTERRUPTIBILITY_IMMEDIATE

    def get_effective_attention(self, activity):
        return (None, self.mode, None)


class _Ag2Harness:
    """Real AG-2 + real LR-2/LR-4 owners, one isolated root."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="paused-condition-ag2-"))
        self.integration_lease = ag2.IntegrationAuthorityLease(self.root)
        self.integration_lease.acquire()
        self.integration_store = ag2.IntegrationStore(
            self.root, namespace=ac.NAMESPACE_ISOLATED_TEST)
        integration_capability = ag2.LifeIntegrationAuthority.issue_capability(
            self.integration_lease, namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.integration_commit = ag2.IntegrationCommitPort(
            self.integration_store, self.integration_lease, integration_capability)

        self.decisions = _Ag2DecisionReader()
        self.adoptions = _Ag2AdoptionReader(self.integration_store)
        issuer = adm.build_admission_issuer(
            self.decisions, self.adoptions, runtime_epoch="paused-condition-ag2-test")
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.root, namespace=ac.NAMESPACE_ISOLATED_TEST, _admission_issuer=issuer)
        capability = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.commands = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=capability)
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
            candidate_read=_Ag2CandidateReader(),
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

    def state_revision(self) -> int:
        state, _ = self.store.load_verified_state_and_journal()
        return int(state["revision"])

    def activity(self) -> dict:
        state, _ = self.store.load_verified_state_and_journal()
        return dict((state.get("activities") or {})[ACTIVITY_ID])

    def start(self) -> dict:
        self.adoptions.grant("ag2-start", ac.TRANSITION_START, ACTIVITY_ID)
        result = self.commands.start_activity(
            activity_kind="READ_ARTIFACT",
            title="ag2 explicit-condition pause fixture",
            origin_ref="artifact:pausecondfixture",
            source_refs=["artifact:pausecondfixture"],
            expected_revision=self.state_revision(),
            idempotency_key="ag2-start",
            decision_ref=DECISION_REF,
            adoption_ref="adp:ag2-start",
            activity_id=ACTIVITY_ID,
        )
        return dict(result.activity)

    def pause_decision(self, *, hint: str | None) -> ag1.DecisionRecord:
        decision = ag1.DecisionRecord(
            subject_id=ag1.GLOBAL_SUBJECT_ID,
            decision_id=AG2_DECISION_ID,
            revision=1,
            decision_opportunity_ref=AG2_OPP_ID,
            candidate_set_ref="cset:pausecondag2",
            candidate_refs=(),
            context_snapshot_ref=AG2_CTX_ID,
            hard_constraint_result_refs=(),
            decision=ag1.DECISION_PAUSE,
            reason_codes=(ag1.REASON_SAFE_BOUNDARY_PAUSE_RECORDED,),
            decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
            policy_version=ag1.POLICY_VERSION_AG1_V1,
            snapshot_fingerprint="sfp:pausecondag2",
            created_at=STAMP,
            current_activity_ref=ACTIVITY_ID,
            capacity_snapshot_ref="cap:pausecondag2",
            target_activity_ref=ACTIVITY_ID,
            reevaluation_hint_ref=hint,
        )
        self.decisions.decisions[AG2_DECISION_ID] = decision.to_dict()
        self.decisions.contexts[AG2_CTX_ID] = {
            "current_activity_ref": ACTIVITY_ID,
            "current_activity_revision": self.state_revision(),
            "candidate_revisions": {},
        }
        self.decisions.opportunities[AG2_OPP_ID] = {"status": "OPEN"}
        return decision

    def adopt(self, decision, *, pause_spec=PAUSE_SPEC, fault_hook=None,
              pause_mode_override=None):
        return self.adoption.adopt_decision(
            decision,
            pause_condition_spec=pause_spec,
            fault_hook=fault_hook,
            pause_mode_override=pause_mode_override,
            occurred_at=STAMP,
        )

    def conditions(self) -> dict:
        return dict((self.waiting.condition_store.load_state() or {}).get("conditions") or {})

    def eligibilities(self) -> dict:
        return dict((self.waiting.eligibility_store.load_state() or {}).get(
            "eligibilities") or {})

    def adoption_record(self) -> dict:
        state = self.integration_store.load_state() or {}
        aid = (state.get("adoption_id_by_decision") or {}).get(AG2_DECISION_ID)
        return dict((state.get("adoptions_by_id") or {}).get(aid) or {})

    def pause_transitions(self) -> list[dict]:
        return [entry for entry in self.store.load_verified_state_and_journal()[1]
                if entry.get("transition_type") == ac.TRANSITION_PAUSE]


@POSIX_ONLY
class PausedExplicitConditionAg2Test(unittest.TestCase):
    def make_harness(self) -> _Ag2Harness:
        harness = _Ag2Harness()
        self.addCleanup(harness.close)
        return harness

    def test_a_conditional_pause_at_a_safe_boundary_is_refused_before_any_mutation(self):
        harness = self.make_harness()
        active = harness.start()
        decision = harness.pause_decision(hint=BASIS["basis_ref"])

        with self.assertRaises(ag2.AdoptionIllegalError) as caught:
            harness.adopt(decision, pause_mode_override=ag2.INTERRUPTIBILITY_SAFE_BOUNDARY)
        self.assertIn("PAUSE_CONDITION_REQUIRES_IMMEDIATE_PAUSE", str(caught.exception))

        after = harness.activity()
        self.assertEqual(ac.STATUS_ACTIVE, after["status"])
        self.assertEqual(active["revision"], after["revision"])
        self.assertEqual({}, harness.conditions())
        self.assertEqual({}, harness.eligibilities())
        self.assertEqual([], harness.pause_transitions())
        self.assertEqual({}, harness.adoption_record())

    def test_a_bad_or_unoffered_pause_spec_is_refused_before_any_mutation(self):
        harness = self.make_harness()
        active = harness.start()
        decision = harness.pause_decision(hint=BASIS["basis_ref"])
        mismatched = dict(PAUSE_SPEC, basis_ref="waitbasis:notoffered")

        for spec in (None, {}, mismatched):
            with self.assertRaises(ag2.AdoptionIllegalError) as caught:
                harness.adopt(decision, pause_spec=spec)
            self.assertIn("PAUSE_CONDITION_NOT_OFFERED", str(caught.exception))

        after = harness.activity()
        self.assertEqual(ac.STATUS_ACTIVE, after["status"])
        self.assertEqual(active["revision"], after["revision"])
        self.assertEqual({}, harness.conditions())
        self.assertEqual({}, harness.eligibilities())
        self.assertEqual([], harness.pause_transitions())

    def test_a_failed_binding_is_completed_by_retrying_the_same_committed_decision(self):
        harness = self.make_harness()
        harness.start()
        decision = harness.pause_decision(hint=BASIS["basis_ref"])

        def fault_hook(name: str) -> None:
            if name == "before_condition_commit":
                raise RuntimeError("CRASH_INJECTED:before_condition_commit")

        with self.assertRaises(RuntimeError):
            harness.adopt(decision, fault_hook=fault_hook)
        paused = harness.activity()
        self.assertEqual(ac.STATUS_PAUSED, paused["status"])
        self.assertEqual({}, harness.conditions())
        self.assertEqual({}, harness.eligibilities())
        self.assertEqual("AUTHORIZED", harness.adoption_record().get("adoption_status"))

        retried = harness.adopt(decision)
        self.assertFalse(retried["idempotent_replay"])
        self.assertEqual("APPLIED", retried["adoption_record"].adoption_status)
        after = harness.activity()
        self.assertEqual(ac.STATUS_PAUSED, after["status"])
        # The pause itself was not replayed: only the missing binding was written.
        self.assertEqual(paused["revision"], after["revision"])
        self.assertEqual(1, len(harness.pause_transitions()))
        conditions = harness.conditions()
        self.assertEqual(1, len(conditions))
        self.assertTrue(awa.is_pause_bound_condition(next(iter(conditions.values()))))
        self.assertEqual({}, harness.eligibilities())

        again = harness.adopt(decision)
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(1, len(harness.conditions()))
        self.assertEqual(1, len(harness.pause_transitions()))


if __name__ == "__main__":
    unittest.main()
