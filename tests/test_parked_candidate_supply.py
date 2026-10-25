#!/usr/bin/env python3
"""Valid parked Activities become material *before* the quiet gate.

What this pins down
-------------------
A due check window used to answer ``NO_MATERIAL`` whenever the only real offer was "the thing
she set down and can now come back to": a WAITING Activity that released the foreground, with an
OPEN LR-4 eligibility, and nothing else on offer.  ``AutonomousLifeCycle.materials`` did not count
that as material, so AG-0 never ran and the offer could never be considered.

The supply added here is the **existing** AG-0 ``RESUME_ELIGIBLE`` source asked one step earlier
(``AutonomousLifeCycle.parked_resume_candidates``).  It consumes nothing, resumes nothing, and
does not change the quiet predicate: the same eligibility id is still part of the situation
fingerprint, so a second identical round stays ``QUIET``.

What is real here
-----------------
* LR-2's canonical Activity store and command service, and LR-4's eligibility/condition/agenda
  stores, all in an isolated temporary root (borrowed from ``test_paused_eligible_alignment``);
* the AG-0 source, registry, materializer and read service are the production classes;
* for the target-admission cases, a **real** ``LifeSupplyService`` on an isolated Unix socket,
  a real ``LifeArtifactPort`` and a real ManagedArtifact bound through the Activity's
  ``subject_refs`` -- tombstone/unreachable negatives are the owner's own answers;
* the Decision/adoption coordinator and the capacity snapshots are **test stand-ins** -- no model
  is called and no real Decision is committed.  The AG-1 legality call itself is real.
* the OPEN eligibilities are **synthetic** (LR-4 has no public "mint an eligibility" API for a
  WAITING Activity in this fixture), so nothing here claims a natural eligibility source; the
  smoke script covers the real ``scan_due_agenda`` path instead.
* the Supply read path is subject-scoped, not grant-scoped: revoking a write grant is recorded
  as an owner fact, never relabelled as "the source was withdrawn".

Run:  python3 -m unittest -v tests.test_parked_candidate_supply
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_continuity as ac  # noqa: E402
import activity_waiting_agenda as awa  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_runner as runner  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_artifact_port as lap  # noqa: E402
from test_paused_eligible_alignment import DECISION_REF, _Harness, STAMP  # noqa: E402

OBSERVED_AT = "2026-10-23T00:05:00+00:00"
LATER = "2026-10-23T00:20:00+00:00"
CLOCK_AT = datetime(2026, 10, 23, 0, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Cycle-side harness: real owners + real AG-0, scripted Decision/adoption
# ---------------------------------------------------------------------------


class _ActionRead:
    def list_receipts(self, action_id):
        return []

    def list_actions(self, status=None):
        return []


class _ProgressPort:
    def ingest_progress_evidence(self, evidence):
        return {}


class _ScriptedCoordinator:
    """The Decision/adoption seam: records the *production* candidate set, then says NO_ACTION."""

    def __init__(self, candidate_read) -> None:
        self.candidate_read = candidate_read
        self.calls: list[dict] = []
        self.candidate_sets: list[dict] = []

    def run_decision_and_adoption_cycle(self, **kwargs):
        self.calls.append(kwargs)
        candidate_set = self.candidate_read.build_candidate_set(
            observed_at=kwargs["observed_at"])
        self.candidate_sets.append(dict(candidate_set))
        record = SimpleNamespace(decision="NO_ACTION", selected_candidate_ref=None,
                                 reason_codes=[], short_rationale="scripted")
        adoption = SimpleNamespace(adoption_status="APPLIED", rejection_reason_code=None)
        return {
            "correlation_id": "corr:parked:1",
            "decision_result": {"decision_record": record},
            "adoption_result": {"adoption_record": adoption},
            "integration_record": SimpleNamespace(activity_ref=None, integration_id="integ:1"),
            "step_execution_result": {},
        }

    def emit_experience_handoff(self, **kwargs):
        return {"dispatch_result": {"deliveries": []}, "handoff_event": None}


class _Runtime:
    def __init__(self, harness: _Harness, *, source=None) -> None:
        self.state_root = Path(harness.root)
        self.activity_read = ac.ActivityReadService(harness.store)
        self.eligibility_store = harness.coordinator.eligibility_store
        self.waiting_coordinator = harness.coordinator
        self.action_read = _ActionRead()
        self.action_command = harness.commands
        self.ag2_progress_port = _ProgressPort()
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})
        self.last_error = None
        if source is None:
            source = ag0.ResumeEligibleCandidateSource(harness.coordinator, self.activity_read)
        self.source = source
        self.candidate_registry = ag0.CandidateSourceRegistry(
            {ag0.SOURCE_RESUME_ELIGIBLE: source})
        capability = ag0.CandidateProjectionGuard.issue_capability(
            caller_module="candidate_materializer", namespace=ag0.NAMESPACE_PRODUCTION)
        materializer = ag0.CandidateMaterializer(
            self.candidate_registry,
            capability=capability,
            audit_journal=ag0.CandidateAuditJournal(
                Path(harness.root) / "candidate_audit_journal.jsonl"),
            record_audit=False,
        )
        self.candidate_read = ag0.CandidateReadService(materializer)
        self.coordinator = _ScriptedCoordinator(self.candidate_read)

    def canonical_state(self):
        view = self.activity_read.get_current_life_view()
        return {"foreground_activity_ref": view.get("foreground_activity_ref")}


class _EmptyStore:
    """A reader that answers "no records" -- the owner-side deletion case."""

    def load_state(self):
        return {"revision": 0, "eligibilities": {}}


class _BrokenStore:
    """A reader that fails -- "cannot prove it is resumable" must never become an offer."""

    def load_state(self):
        raise OSError("eligibility owner unreachable")


def _cycle(harness: _Harness, *, source=None):
    runtime = _Runtime(harness, source=source)
    cycle = alc.AutonomousLifeCycle(runtime, enabled=True, max_rounds_per_day=10, env={})
    return cycle, runtime


class _Clock:
    """A movable wall clock, so "the next check window" is a real interval later."""

    def __init__(self, moment: datetime = CLOCK_AT) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, *, minutes: int) -> None:
        from datetime import timedelta
        self.moment = self.moment + timedelta(minutes=minutes)


def _runner(cycle, harness: _Harness, *, clock=None):
    return runner.AutonomousLifeRunner(
        cycle, state_root=harness.root, env={}, interval_minutes=60,
        clock=clock or _Clock())


def _waiting_parked(harness: _Harness, activity_id="actv:parked1", *, release=True):
    harness.start(activity_id, command_id="s-" + activity_id)
    return harness.wait(activity_id, episode="wep:parked", command_id="w-" + activity_id,
                        release_foreground=release)


def _paused_parked(harness: _Harness, activity_id="actv:parked1"):
    harness.start(activity_id, command_id="s-" + activity_id)
    return harness.pause(activity_id, command_id="p-" + activity_id)


def _transition(harness: _Harness, method: str, activity_id: str, *,
                command_id: str, mutation_kind: str, **kwargs):
    harness._grant(command_id, mutation_kind, activity_id)
    command = getattr(harness.commands, method)
    return dict(command(
        activity_id=activity_id,
        expected_revision=harness.state_revision(),
        idempotency_key=command_id,
        source_refs=["decision:parkedfixture"],
        decision_ref=DECISION_REF,
        adoption_ref="adp:" + command_id,
        **kwargs,
    ).activity)


def _legal_decisions(candidate: ag0.CandidateRecord, *, current_activity=None,
                     current_status=None, revision=0):
    context = ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:parkedfixture",
        opportunity_ref="dopp:parkedfixture",
        candidate_set_ref="cset:parkedfixture",
        life_frame_ref="lframe:parkedfixture",
        captured_at=STAMP,
        source_revisions={"snapshot": 1},
        snapshot_fingerprint="sfp:parkedfixture",
        current_activity_ref=current_activity,
        current_activity_status=current_status,
        current_activity_revision=int(revision),
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
    )
    capacity = ag1.AgencyCapacitySnapshot(
        capacity_snapshot_id="cap:parkedfixture",
        captured_at=STAMP,
        source_revisions=["snapshot:parkedfixture"],
        candidate_executability={
            candidate.candidate_id: {"executability": ag1.EXEC_NOW}},
    )
    return ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate(
        candidate, context_snapshot=context, capacity_snapshot=capacity)


class ParkedCandidateSupplyTest(unittest.TestCase):
    """The supply chain itself: AG-0 read predicates and the cycle's material gate."""

    # -- 1. valid parked Activities are supplied ---------------------------------

    def test_a_waiting_activity_that_released_the_foreground_is_material(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:parked", activity=activity)
            cycle, runtime = _cycle(harness)

            material = cycle.materials(now=OBSERVED_AT)
            self.assertTrue(material["has_material"], material)
            parked = material["parked_candidates"]
            self.assertEqual(parked["count"], 1, parked)
            self.assertEqual(parked["refs"], ["elig:parked"])
            self.assertEqual(parked["source_kind"], ag0.SOURCE_RESUME_ELIGIBLE)

            source = runtime.source
            items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
            self.assertEqual([row["eligibility_id"] for row in items], ["elig:parked"])
        finally:
            harness.close()

    def test_a_paused_activity_with_a_matching_eligibility_is_material(self):
        harness = _Harness()
        try:
            activity = _paused_parked(harness)
            harness.seed_eligibility(eligibility_id="elig:paused", activity=activity)
            cycle, runtime = _cycle(harness)

            material = cycle.materials(now=OBSERVED_AT)
            self.assertTrue(material["has_material"])
            self.assertEqual(material["parked_candidates"]["count"], 1)

            candidates, _snap, _meta = runtime.source.materialize(observed_at=OBSERVED_AT)
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate.candidate_kind, ag0.CANDIDATE_KIND_RESUME_ACTIVITY)
            self.assertIn("RESUME",
                          _legal_decisions(candidate, current_activity=activity["activity_id"],
                                           current_status=activity["status"],
                                           revision=activity["revision"]))
        finally:
            harness.close()

    # -- 2. nothing valid on offer ------------------------------------------------

    def test_an_empty_instance_stays_no_material(self):
        harness = _Harness()
        try:
            cycle, runtime = _cycle(harness)
            material = cycle.materials(now=OBSERVED_AT)
            self.assertFalse(material["has_material"], material)
            self.assertEqual(material["parked_candidates"]["count"], 0)
            out = cycle.evaluate_once(trigger_ref="evt:parked", observed_at=OBSERVED_AT)
            self.assertEqual(out["outcome"], alc.OUTCOME_NO_MATERIAL, out)
            self.assertEqual(runtime.coordinator.calls, [])
        finally:
            harness.close()

    def test_an_existing_foreground_path_is_unchanged(self):
        """The previous material path (a running Activity) is not replaced by the new supply."""

        harness = _Harness()
        try:
            harness.start("actv:running", command_id="s-running")
            cycle, runtime = _cycle(harness)
            material = cycle.materials(now=OBSERVED_AT)
            self.assertTrue(material["has_material"])
            self.assertEqual(material["foreground_activity"], "actv:running")
            self.assertEqual(material["parked_candidates"]["count"], 0)
            out = cycle.evaluate_once(trigger_ref="evt:parked", observed_at=OBSERVED_AT)
            self.assertEqual(out["outcome"], alc.OUTCOME_NO_ACTION, out)
            self.assertEqual(len(runtime.coordinator.calls), 1)
        finally:
            harness.close()

    def test_a_waiting_activity_without_an_eligibility_is_not_material(self):
        harness = _Harness()
        try:
            _waiting_parked(harness, release=True)
            cycle, runtime = _cycle(harness)
            material = cycle.materials(now=OBSERVED_AT)
            self.assertFalse(material["has_material"], material)
            self.assertEqual(material["parked_candidates"]["count"], 0)
            out = cycle.evaluate_once(trigger_ref="evt:parked", observed_at=OBSERVED_AT)
            self.assertEqual(out["outcome"], alc.OUTCOME_NO_MATERIAL, out)
            self.assertEqual(runtime.coordinator.calls, [])
        finally:
            harness.close()

    def test_an_expired_eligibility_is_not_supplied(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:expired", activity=activity,
                                     expires_at="2026-10-23T00:04:00+00:00")
            _cycle_obj, runtime = _cycle(harness)
            items, _snap, _meta = runtime.source.discover(observed_at=OBSERVED_AT)
            self.assertEqual(items, [])
            self.assertFalse(_cycle_obj.materials(now=OBSERVED_AT)["has_material"])
        finally:
            harness.close()

    def test_a_non_open_eligibility_is_not_supplied(self):
        for status in (awa.ELIG_STATUS_CONSUMED, awa.ELIG_STATUS_STALE,
                       awa.ELIG_STATUS_CANCELLED, awa.ELIG_STATUS_EXPIRED):
            harness = _Harness()
            try:
                activity = _waiting_parked(harness, release=True)
                harness.seed_eligibility(eligibility_id="elig:" + status, activity=activity,
                                         status=status)
                _cycle_obj, runtime = _cycle(harness)
                items, _snap, _meta = runtime.source.discover(observed_at=OBSERVED_AT)
                self.assertEqual(items, [], status)
            finally:
                harness.close()

    def test_a_revision_mismatch_is_not_supplied(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:stale", activity=activity,
                                     activity_revision=int(activity["revision"]) - 1)
            _cycle_obj, runtime = _cycle(harness)
            ok, reason = runtime.source.validate_source(
                harness.eligibility("elig:stale"), observed_at=OBSERVED_AT)
            self.assertFalse(ok)
            self.assertEqual(reason, "STALE_ACTIVITY_REVISION")
        finally:
            harness.close()

    def test_an_episode_mismatch_is_not_supplied(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:episode", activity=activity,
                                     waiting_episode_id="wep:another")
            _cycle_obj, runtime = _cycle(harness)
            ok, reason = runtime.source.validate_source(
                harness.eligibility("elig:episode"), observed_at=OBSERVED_AT)
            self.assertFalse(ok)
            self.assertEqual(reason, "WAITING_EPISODE_MISMATCH")
        finally:
            harness.close()

    def test_a_deleted_eligibility_is_not_supplied(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:deleted", activity=activity)
            read = ac.ActivityReadService(harness.store)
            source = ag0.ResumeEligibleCandidateSource(_EmptyStore(), read)
            items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
            self.assertEqual(items, [])
        finally:
            harness.close()

    def test_an_owner_read_failure_is_reported_and_counts_as_no_material(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:unreadable", activity=activity)
            read = ac.ActivityReadService(harness.store)
            source = ag0.ResumeEligibleCandidateSource(_BrokenStore(), read)
            cycle, _runtime = _cycle(harness, source=source)
            material = cycle.materials(now=OBSERVED_AT)
            self.assertFalse(material["has_material"], material)
            self.assertEqual(material["parked_candidates"]["reason_code"],
                             "RESUME_SOURCE_READ_FAILED")
            self.assertEqual(material["parked_candidates"]["count"], 0)
        finally:
            harness.close()

    # -- 3. terminal states are never resurrected ---------------------------------

    def test_terminal_activities_are_not_supplied(self):
        cases = (("COMPLETED", "complete_activity", "COMPLETE",
                  {"completion_evidence_ref": "evidence:done"}),
                 ("ABANDONED", "abandon_activity", "ABANDON", {}),
                 ("CANCELLED", "cancel_activity", "CANCEL", {}))
        for label, method, mutation_kind, extra in cases:
            harness = _Harness()
            try:
                harness.start("actv:terminal", command_id="s-terminal")
                terminal = _transition(harness, method, "actv:terminal",
                                       command_id="t-" + label,
                                       mutation_kind=mutation_kind,
                                       **extra)
                self.assertEqual(terminal["status"], label)
                harness.seed_eligibility(eligibility_id="elig:" + label, activity=terminal)
                _cycle_obj, runtime = _cycle(harness)
                items, _snap, _meta = runtime.source.discover(observed_at=OBSERVED_AT)
                self.assertEqual(items, [], label)
            finally:
                harness.close()

    # -- 4. the foreground rule is LR-2's, not a new one -------------------------

    def test_another_waiting_foreground_still_allows_the_candidate(self):
        harness = _Harness()
        try:
            first = _waiting_parked(harness, activity_id="actv:first", release=True)
            harness.seed_eligibility(eligibility_id="elig:first", activity=first)
            harness.start("actv:front", command_id="s-front")
            harness.wait("actv:front", episode="wep:front", command_id="w-front",
                         release_foreground=False)
            _cycle_obj, runtime = _cycle(harness)
            items, _snap, _meta = runtime.source.discover(observed_at=OBSERVED_AT)
            self.assertEqual([row["eligibility_id"] for row in items], ["elig:first"])
        finally:
            harness.close()

    def test_a_paused_activity_releases_the_slot_while_a_running_one_is_a_conflict(self):
        # v0.4 background pause: the PAUSE itself frees the execution slot, so the parked
        # candidate is no longer refused as a "paused foreground".  The conflict gate is not
        # weakened -- an Activity that is really executing still refuses it.
        harness = _Harness()
        try:
            first = _waiting_parked(harness, activity_id="actv:first", release=True)
            harness.seed_eligibility(eligibility_id="elig:first", activity=first)
            harness.start("actv:front", command_id="s-front")
            harness.pause("actv:front", command_id="p-front")
            _cycle_obj, runtime = _cycle(harness)
            ok, reason = runtime.source.validate_source(
                harness.eligibility("elig:first"), observed_at=OBSERVED_AT)
            self.assertTrue(ok)
            self.assertIsNone(reason)

            harness.start("actv:running", command_id="s-running")
            ok, reason = runtime.source.validate_source(
                harness.eligibility("elig:first"), observed_at=OBSERVED_AT)
            self.assertFalse(ok)
            self.assertEqual(reason, "FOREGROUND_CONFLICT")
        finally:
            harness.close()

    # -- 5. the due window: material -> official candidate -> AG-1 legality ------

    def test_a_due_check_window_materializes_the_official_resume_candidate(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:parked", activity=activity)
            cycle, runtime = _cycle(harness)
            wake_runner = _runner(cycle, harness)
            record = wake_runner.run_due()

            self.assertEqual(record.outcome, alc.OUTCOME_NO_ACTION, record.to_dict())
            self.assertEqual(len(runtime.coordinator.calls), 1)
            self.assertEqual(len(runtime.coordinator.candidate_sets), 1)
            refs = runtime.coordinator.candidate_sets[0]["candidate_refs"]
            self.assertEqual(len(refs), 1, refs)
            candidate_dict = runtime.candidate_read.get_candidate(refs[0])
            self.assertEqual(candidate_dict["candidate_kind"],
                             ag0.CANDIDATE_KIND_RESUME_ACTIVITY)
            self.assertEqual(candidate_dict["resume_eligibility_ref"], "elig:parked")
            candidate = ag0.CandidateRecord.from_dict(candidate_dict)
            self.assertIn("RESUME", _legal_decisions(candidate))

            # supply only: the Activity is still parked and the eligibility still OPEN.
            self.assertEqual(harness.activity("actv:parked1")["status"], ac.STATUS_WAITING)
            self.assertEqual(harness.eligibility("elig:parked")["status"],
                             awa.ELIG_STATUS_OPEN)
        finally:
            harness.close()

    def test_the_same_eligibility_is_not_replayed_in_the_next_window(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:parked", activity=activity)
            cycle, runtime = _cycle(harness)
            clock = _Clock()
            wake_runner = _runner(cycle, harness, clock=clock)

            first = wake_runner.run_due()
            clock.advance(minutes=61)
            second = wake_runner.run_due()
            self.assertEqual(first.outcome, alc.OUTCOME_NO_ACTION)
            self.assertEqual(second.status, runner.WAKE_QUIET, second.to_dict())
            self.assertEqual(second.llm_calls, 0)
            self.assertEqual(len(runtime.coordinator.calls), 1)
            self.assertEqual(harness.eligibility("elig:parked")["status"],
                             awa.ELIG_STATUS_OPEN)
        finally:
            harness.close()

    def test_a_not_due_window_neither_considers_nor_spends(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:parked", activity=activity)
            cycle, runtime = _cycle(harness)
            wake_runner = _runner(cycle, harness)
            wake_runner.run_due()
            record = wake_runner.run_due()
            self.assertEqual(record.status, runner.WAKE_NOT_DUE, record.to_dict())
            self.assertEqual(len(runtime.coordinator.calls), 1)
            self.assertEqual(harness.eligibility("elig:parked")["status"],
                             awa.ELIG_STATUS_OPEN)
        finally:
            harness.close()

    # -- 6. use-time revocation/change is still refused ---------------------------

    def test_a_revoked_eligibility_is_refused_at_use_time(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:parked", activity=activity)
            _cycle_obj, runtime = _cycle(harness)
            candidates, _snap, _meta = runtime.source.materialize(observed_at=OBSERVED_AT)
            self.assertEqual(len(candidates), 1)
            record = harness.eligibility("elig:parked")
            record["status"] = awa.ELIG_STATUS_CANCELLED
            harness.coordinator.eligibility_store.commit_records(
                lease=harness.lease,
                capability=harness.coordinator.eligibility_capability,
                records=[record],
                action="PARKED_FIXTURE_WITHDRAW",
                idempotency_key="withdraw:elig:parked",
            )
            refreshed = runtime.source.refresh(candidates[0], observed_at=OBSERVED_AT)
            self.assertNotEqual(refreshed.availability_status, ag0.CANDIDATE_STATUS_OPEN)
            self.assertEqual(refreshed.invalidation_reason, "RESUME_ELIGIBILITY_CANCELLED")
        finally:
            harness.close()

    def test_an_activity_that_changes_after_the_decision_is_refused(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:changed", activity=activity)
            _cycle_obj, runtime = _cycle(harness)
            candidates, _snap, _meta = runtime.source.materialize(observed_at=OBSERVED_AT)
            self.assertEqual(len(candidates), 1)

            _transition(harness, "complete_activity", activity["activity_id"],
                        command_id="c-changed", mutation_kind="COMPLETE",
                        completion_evidence_ref="evidence:done")
            refreshed = runtime.source.refresh(candidates[0], observed_at=OBSERVED_AT)
            self.assertNotEqual(refreshed.availability_status, ag0.CANDIDATE_STATUS_OPEN)
            self.assertIn("ACTIVITY_BECAME_TERMINAL_COMPLETED",
                          refreshed.invalidation_reason)
        finally:
            harness.close()

    def test_an_eligibility_that_expires_after_the_decision_is_refused(self):
        harness = _Harness()
        try:
            activity = _waiting_parked(harness, release=True)
            harness.seed_eligibility(eligibility_id="elig:window", activity=activity,
                                     expires_at="2026-10-23T00:10:00+00:00")
            _cycle_obj, runtime = _cycle(harness)
            candidates, _snap, _meta = runtime.source.materialize(observed_at=OBSERVED_AT)
            self.assertEqual(len(candidates), 1)
            refreshed = runtime.source.refresh(candidates[0], observed_at=LATER)
            self.assertNotEqual(refreshed.availability_status, ag0.CANDIDATE_STATUS_OPEN)
        finally:
            harness.close()


# ---------------------------------------------------------------------------
# Target-source admission: a real Supply owner proves the Artifact target
# ---------------------------------------------------------------------------


def _owner_call(socket_path: Path, request: dict, *, timeout: float = 20.0) -> dict:
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise RuntimeError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise RuntimeError("owner answer is not an object")
    return answer


class _SupplyOwner:
    """A real ``LifeSupplyService`` on its own Unix socket.  Stopped in cleanup."""

    def __init__(self) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(tempfile.mkdtemp(prefix="parked-target-supply-"))
        self.socket_path = self.root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(
            self.root / "data", operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(), writer_instance="parked-target-test")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._stopped = False

    def call(self, request: dict) -> dict:
        return _owner_call(self.socket_path, request)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.thread.join(timeout=5)


def _supply_provision(owner: _SupplyOwner) -> tuple[str, str]:
    subject = ac.GLOBAL_SUBJECT_ID
    workspace_grant = owner.call({"op": "issue_grant", "grant": {
        "subject": subject, "capability": "PROVISION_PERSONAL_WORKSPACE",
        "scope": "workspace:personal", "target": "workspace:%s" % subject,
        "authority_scope": "life-supply.workspace.v1", "use_mode": "ONE_SHOT",
        "operation_id": "issue:workspace:%s" % subject}})
    if workspace_grant.get("status") != "ALLOW":
        raise RuntimeError("workspace grant refused: %r" % (workspace_grant,))
    answer = owner.call({"op": "provision_workspace",
                         "grant_id": str(workspace_grant["grant_id"]),
                         "operation_key": "parked-target:workspace:%s" % subject,
                         "workspace": {"subject_id": subject}})
    if answer.get("status") != "OK":
        raise RuntimeError("workspace provision failed: %r" % (answer,))
    workspace_id = str(answer["workspace"]["workspace_id"])
    grant = owner.call({"op": "issue_grant", "grant": {
        "subject": subject, "capability": "COMMIT_MANAGED_ARTIFACT",
        "scope": "artifact:personal", "target": "workspace:%s" % workspace_id,
        "authority_scope": "life-supply.artifact.v1", "use_mode": "REUSABLE",
        "operation_id": "issue:artifact:%s" % subject}})
    if grant.get("status") != "ALLOW":
        raise RuntimeError("artifact grant refused: %r" % (grant,))
    return workspace_id, str(grant["grant_id"])


def _supply_create(port: lap.LifeArtifactPort, workspace_id: str, grant_id: str,
                   tag: str) -> str:
    outcome = port.create_artifact(
        workspace_id=workspace_id, title="绑定作品", content="一页正文", grant_id=grant_id,
        operation_key=lap.operation_key_for(
            "parked-target-test", "create-%s" % tag, ac.GLOBAL_SUBJECT_ID))
    if not outcome.applied:
        raise RuntimeError("artifact create failed: %s" % (outcome.to_dict(),))
    return str(outcome.artifact_id)


def _supply_tombstone(owner: _SupplyOwner, port: lap.LifeArtifactPort,
                      artifact_id: str, grant_id: str, tag: str) -> None:
    view = port.read_artifact(artifact_id)
    revision = int((view.get("artifact") or {}).get("revision") or 0)
    answer = owner.call({
        "op": "tombstone", "grant_id": grant_id,
        "operation_key": lap.operation_key_for(
            "parked-target-test", "tombstone-%s" % tag, ac.GLOBAL_SUBJECT_ID),
        "artifact": {"subject_id": ac.GLOBAL_SUBJECT_ID, "artifact_id": artifact_id,
                     "expected_revision": revision},
    })
    if answer.get("status") != "OK":
        raise RuntimeError("tombstone refused: %r" % (answer,))


def _bound_waiting(harness: _Harness, *, origin_ref: str, subject_refs: list[str],
                   activity_id: str = "actv:bound1") -> dict:
    """A real LR-2 START whose recorded target binding is exactly ``subject_refs``."""

    start_key = "s-" + activity_id
    harness._grant(start_key, ac.TRANSITION_START, activity_id)
    harness.commands.start_activity(
        activity_kind="CONTINUE_PERSONAL_ARTIFACT",
        title="target admission fixture",
        origin_ref=origin_ref,
        source_refs=[origin_ref],
        subject_refs=subject_refs,
        expected_revision=harness.state_revision(),
        idempotency_key=start_key,
        decision_ref=DECISION_REF,
        adoption_ref="adp:" + start_key,
        activity_id=activity_id,
    )
    return harness.wait(activity_id, episode="wep:bound",
                        command_id="w-" + activity_id, release_foreground=True)


@unittest.skipUnless(hasattr(socket, "AF_UNIX") and os.name == "posix",
                     "real Supply socket and euid are POSIX-only")
class ArtifactTargetAdmissionTest(unittest.TestCase):
    """The target's *source* is proved by the real Supply owner, separately from eligibility."""

    def setUp(self) -> None:
        self.owner = _SupplyOwner()
        self.addCleanup(self.owner.stop)
        self.workspace_id, self.grant_id = _supply_provision(self.owner)
        self.port = lap.LifeArtifactPort(str(self.owner.socket_path), ac.GLOBAL_SUBJECT_ID)
        # The ManagedArtifact owner mints its ids *with* the ``artifact:`` prefix; adding a
        # second one here would be exactly the guessed mapping this adapter refuses to make.
        self.artifact_id = _supply_create(
            self.port, self.workspace_id, self.grant_id, "main")
        self.artifact_ref = self.artifact_id
        self.harness = _Harness()
        self.addCleanup(self.harness.close)

    def _source(self, *, target_read: object = ...) -> ag0.ResumeEligibleCandidateSource:
        if target_read is ...:
            target_read = self.port
        return ag0.ResumeEligibleCandidateSource(
            self.harness.coordinator,
            ac.ActivityReadService(self.harness.store),
            target_read=target_read,
        )

    def _bound(self, *, subject_refs=None, origin_ref=None) -> dict:
        activity = _bound_waiting(
            self.harness,
            origin_ref=origin_ref or self.artifact_ref,
            subject_refs=([self.artifact_ref] if subject_refs is None else subject_refs),
        )
        self.harness.seed_eligibility(eligibility_id="elig:bound", activity=activity)
        return activity

    def test_a_real_supply_target_is_readable_and_the_candidate_is_offered(self):
        self._bound()
        source = self._source()
        items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
        self.assertEqual(len(items), 1)
        ok, reason = source.validate_source(items[0], observed_at=OBSERVED_AT)
        self.assertTrue(ok, reason)

    def test_an_unwired_target_read_fails_closed(self):
        self._bound()
        source = self._source(target_read=None)
        items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
        self.assertEqual(items, [])
        ok, reason = source.validate_source(
            self.harness.eligibility("elig:bound"), observed_at=OBSERVED_AT)
        self.assertFalse(ok)
        self.assertEqual(reason, "TARGET_SOURCE_READ_NOT_WIRED")

    def test_a_deleted_target_fails_closed_while_the_eligibility_stays_open(self):
        self._bound()
        source = self._source()
        self.assertEqual(len(source.discover(observed_at=OBSERVED_AT)[0]), 1)
        _supply_tombstone(self.owner, self.port, self.artifact_id, self.grant_id, "main")
        # The eligibility is untouched and still OPEN: this negative control is about the
        # *source*, not about eligibility validity.
        self.assertEqual(self.harness.eligibility("elig:bound")["status"],
                         awa.ELIG_STATUS_OPEN)
        items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
        self.assertEqual(items, [])
        ok, reason = source.validate_source(
            self.harness.eligibility("elig:bound"), observed_at=OBSERVED_AT)
        self.assertFalse(ok)
        self.assertEqual(reason, "TARGET_SOURCE_UNREADABLE")

    def test_a_target_deleted_after_the_decision_is_refused_at_pre_adoption(self):
        self._bound()
        source = self._source()
        candidates, _snap, _meta = source.materialize(observed_at=OBSERVED_AT)
        self.assertEqual(len(candidates), 1)
        _supply_tombstone(self.owner, self.port, self.artifact_id, self.grant_id, "main")
        refreshed = source.refresh(candidates[0], observed_at=OBSERVED_AT)
        self.assertNotEqual(refreshed.availability_status, ag0.CANDIDATE_STATUS_OPEN)
        self.assertEqual(refreshed.invalidation_reason, "TARGET_SOURCE_UNREADABLE")

    def test_an_unreachable_owner_fails_closed(self):
        self._bound()
        source = self._source()
        self.assertEqual(len(source.discover(observed_at=OBSERVED_AT)[0]), 1)
        self.owner.stop()
        items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
        self.assertEqual(items, [])
        ok, reason = source.validate_source(
            self.harness.eligibility("elig:bound"), observed_at=OBSERVED_AT)
        self.assertFalse(ok)
        self.assertEqual(reason, "TARGET_SOURCE_UNREADABLE")

    def test_an_artifact_origin_without_a_bound_target_fails_closed(self):
        self._bound(subject_refs=[], origin_ref=self.artifact_ref)
        source = self._source()
        items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
        self.assertEqual(items, [])
        ok, reason = source.validate_source(
            self.harness.eligibility("elig:bound"), observed_at=OBSERVED_AT)
        self.assertFalse(ok)
        self.assertEqual(reason, "TARGET_BINDING_MISSING")

    def test_a_non_artifact_activity_is_not_blocked_by_the_supply_read(self):
        # The existing path: a World-origin Activity binds no Artifact.  This source does not
        # invent a World read and must not delete the legal candidate either.
        self._bound(subject_refs=[], origin_ref="world:parkedtarget")
        source = self._source()
        items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
        self.assertEqual(len(items), 1)
        ok, reason = source.validate_source(items[0], observed_at=OBSERVED_AT)
        self.assertTrue(ok, reason)

    def test_a_revoked_write_grant_does_not_change_the_read_contract(self):
        """Owner fact, not a pass: the Supply read path is subject-scoped, not grant-scoped.

        Revoking ``COMMIT_MANAGED_ARTIFACT`` removes the ability to *write*; it does not remove
        the subject's ability to read its own artifact.  This test records that boundary so the
        candidate supply is never claimed to have proved a "read permission" the owner does not
        model -- a withdrawn *source* is the tombstone/unreadable case above.
        """

        self._bound()
        grant = self.owner.call({"op": "get_grant", "grant_id": self.grant_id})["grant"]
        revoked = self.owner.call({
            "op": "revoke_grant", "grant_id": self.grant_id,
            "expected_revision": int(grant["revision"]),
            "operation_id": "revoke:parked-target:%s" % self.grant_id,
        })
        self.assertEqual(revoked.get("status"), "REVOKED", revoked)
        self.assertIsNotNone(self.port.read_artifact(self.artifact_id))
        source = self._source()
        items, _snap, _meta = source.discover(observed_at=OBSERVED_AT)
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
