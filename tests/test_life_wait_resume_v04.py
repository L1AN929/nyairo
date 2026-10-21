#!/usr/bin/env python3
"""v0.4 C: the WAIT basis, its LR-4 upkeep, and the RESUME-only-by-owner chain.

What this pins down (exactly this, nothing more)
-----------------------------------------------
AG-1 offers ``WAIT`` only when ``DecisionContextSnapshot.known_waiting_basis_ref`` is present
(``agency_decision_ag1.py:2316-2321``) and AG-2 adopts it only with a concrete ``waiting_spec``
(``life_integration_ag2.py:3321-3380``).  Both were dead code because nothing ever fed them.

So the cases below are:

* **pure** (``life_wait_basis``): which real owner facts may become a basis at all -- a work at a
  head its writer confirmed ``no_change`` on, and a reading material that is *exhausted* on its
  current revision.  A revoked source, an unreadable owner, an UNKNOWN state and a step that is
  merely unavailable are **not** dependencies and must never produce one.
* **satisfaction**: an LR-4 event is formed only when the owner's current head really differs
  from the recorded baseline.  The same head, no head, and a refused read all produce ``None`` --
  a condition cannot be satisfied by a string that happens to match.
* **cycle**: the basis and its ``waiting_spec`` are what the round hands the owners, a PAUSED
  foreground never gets a fresh WAIT basis, and the bounded upkeep emits exactly one event when
  (and only when) the head moved -- with ``auto_resumed`` always false.

No model, no network, no Supply socket.  The owner reads are fakes, and they are described as
fakes; LR-4, AG-1 and AG-2 are not simulated here (their own suites own them).

Fixtures live under ``/tmp/nyairo-v04-c-fixtures`` and are **kept** (no TemporaryDirectory, no
unlink): a failing run must leave its state on disk.

Run (POSIX host -- the life modules import ``fcntl``)::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_life_wait_resume_v04
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import life_wait_basis as lwb  # noqa: E402
import waiting_ref_vocab  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")
TS = "2026-10-28T05:00:00+00:00"
OWNER_ID = "artifact:0f8f9a1c2b3d4e5f60718293a4b5c6d7"
HEAD_V3 = "artifact-version:3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c"
HEAD_V4 = "artifact-version:4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4"
HEAD_V5 = "artifact-version:5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e"

FIXTURE_ROOT = Path(os.environ.get("NYAIRO_C_FIXTURE_ROOT") or "/tmp/nyairo-v04-c-fixtures")


def fixture_dir(name: str) -> Path:
    """A per-run, per-case directory under the kept fixture root (never cleaned up)."""

    path = FIXTURE_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Readiness:
    """The one shape ``life_wait_basis`` reads off ``lsr.StepReadiness`` (no import needed)."""

    def __init__(self, *, available, reason_code, target_ref=None, source_revision=None,
                 activity_ref="actv:work1"):
        self.available = available
        self.reason_code = reason_code
        self.target_ref = target_ref
        self.source_revision = source_revision
        self.activity_ref = activity_ref

    @property
    def bound(self) -> bool:
        return bool(self.activity_ref) and bool(self.target_ref)


# ---------------------------------------------------------------------------------
# pure: which facts may become a basis
# ---------------------------------------------------------------------------------


class WaitBasisSourceTest(unittest.TestCase):
    def test_a_work_no_change_on_the_owner_head_is_a_basis(self):
        readiness = _Readiness(available=False, reason_code="WORK_NO_CHANGE_CONFIRMED",
                               target_ref="artifact:" + OWNER_ID, source_revision=HEAD_V3)
        basis = lwb.basis_from_readiness(readiness)
        self.assertIsNotNone(basis)
        self.assertEqual(basis["source"], lwb.SOURCE_WORK_HEAD)
        self.assertEqual(basis["baseline_head_ref"], HEAD_V3)
        # The owner's own id is kept verbatim (one ``artifact:`` prefix, not two): this is the
        # same string the AR-0 payload and the evidence carry.
        self.assertEqual(basis["artifact_ref"], OWNER_ID)
        self.assertEqual(basis["artifact_id"], OWNER_ID.split(":", 1)[1])
        self.assertEqual(basis["basis_ref"].split(":")[0], lwb.BASIS_REF_PREFIX)
        self.assertEqual(basis["waiting_on_ref"].split(":")[0], lwb.WAITING_ON_PREFIX)

    def test_a_basis_is_bound_to_its_baseline_not_to_the_object_alone(self):
        first = lwb.work_head_basis(artifact_id=OWNER_ID, baseline_head=HEAD_V3)
        second = lwb.work_head_basis(artifact_id=OWNER_ID, baseline_head=HEAD_V4)
        self.assertNotEqual(first["basis_ref"], second["basis_ref"])
        # A stale waiting ref therefore cannot satisfy a condition bound after a newer head.
        self.assertNotEqual(first["waiting_on_ref"], second["waiting_on_ref"])

    def test_an_available_step_is_not_a_dependency(self):
        for available, reason in ((True, "ARTIFACT_STEP_AVAILABLE"), (None, "NO_FOREGROUND_ACTIVITY"),
                                  (None, "FOREGROUND_NOT_ACTIVE"), (None, "CALLER_CANNOT_ACT")):
            readiness = _Readiness(available=available, reason_code=reason,
                                   target_ref="artifact:" + OWNER_ID, source_revision=HEAD_V3)
            self.assertIsNone(lwb.basis_from_readiness(readiness), reason)

    def test_a_world_step_or_an_unknown_target_is_not_a_dependency(self):
        for reason in ("WORLD_STEP_NOT_WIRED", "TARGET_NAMESPACE_UNKNOWN", "NO_BOUND_TARGET"):
            readiness = _Readiness(available=False, reason_code=reason,
                                   target_ref="place:somewhere", source_revision=None)
            self.assertIsNone(lwb.basis_from_readiness(readiness), reason)

    def test_only_an_exhausted_material_waits_for_a_next_version(self):
        exhausted = _Readiness(available=False, reason_code="READING_EXHAUSTED",
                               target_ref="artifact:" + OWNER_ID, source_revision=HEAD_V3)
        basis = lwb.basis_from_readiness(exhausted, state="EXHAUSTED")
        self.assertIsNotNone(basis)
        self.assertEqual(basis["source"], lwb.SOURCE_READING)
        # Everything that is not "read to the end of this revision" is *not* a wait: a revoked
        # source will never deliver a new version, and an unknown state is not a known dependency.
        for reason, state in (("READING_SOURCE_REVOKED", "SOURCE_REVOKED"),
                              ("READING_MATERIAL_NOT_ACTIVE", "MATERIAL_NOT_ACTIVE"),
                              ("READING_OWNER_UNAVAILABLE", "OWNER_UNAVAILABLE"),
                              ("READING_OWNER_REFUSED", "OWNER_REFUSED"),
                              ("READING_PRIVATE_ITEMS_UNAVAILABLE", "PRIVATE_ITEMS_UNAVAILABLE"),
                              ("READING_UNKNOWN", "UNKNOWN")):
            readiness = _Readiness(available=False, reason_code=reason,
                                   target_ref="artifact:" + OWNER_ID, source_revision=HEAD_V3)
            self.assertIsNone(lwb.basis_from_readiness(readiness, state=state), reason)
            # ... and the state token inside the reason is judged on its own, too.
            self.assertIsNone(lwb.basis_from_readiness(readiness), reason + " (from reason)")

    def test_a_basis_without_an_owner_revision_is_refused(self):
        readiness = _Readiness(available=False, reason_code="WORK_NO_CHANGE_CONFIRMED",
                               target_ref="artifact:" + OWNER_ID, source_revision=None)
        self.assertIsNone(lwb.basis_from_readiness(readiness))
        self.assertIsNone(lwb.work_head_basis(artifact_id=OWNER_ID, baseline_head=""))

    def test_the_waiting_ref_is_in_the_lr2_waiting_vocabulary(self):
        """LR-2 validates ``waiting_on_ref`` against the waiting-domain vocabulary."""

        basis = lwb.work_head_basis(artifact_id=OWNER_ID, baseline_head=HEAD_V3)
        parsed = waiting_ref_vocab.parse_waiting_ref(basis["waiting_on_ref"])
        self.assertIsNotNone(parsed, "LR-2 would refuse this waiting ref outright")
        self.assertEqual(parsed[0], waiting_ref_vocab.WaitingRefKind.WAIT_DEP)
        # ... and it is *not* an artifact-result namespace, which LR-2 does not accept as waiting
        # evidence (``waiting_ref_vocab.EXCLUDED_LEGACY_PREFIXES``).
        self.assertNotIn("artifact", waiting_ref_vocab.VALID_WAITING_REF_PREFIXES)


# ---------------------------------------------------------------------------------
# pure: what may satisfy it
# ---------------------------------------------------------------------------------


class WaitSatisfactionTest(unittest.TestCase):
    def setUp(self):
        self.basis = lwb.work_head_basis(artifact_id=OWNER_ID, baseline_head=HEAD_V3)

    def test_the_same_head_never_satisfies_the_condition(self):
        self.assertIsNone(lwb.head_change_event(self.basis, current_head=HEAD_V3,
                                                observed_at=TS))
        # An unreadable owner (no head at all) is not a change either.
        self.assertIsNone(lwb.head_change_event(self.basis, current_head=None, observed_at=TS))
        self.assertIsNone(lwb.head_change_event(self.basis, current_head="", observed_at=TS))

    def test_a_really_new_head_becomes_one_owner_evaluable_event(self):
        event = lwb.head_change_event(self.basis, current_head=HEAD_V4, observed_at=TS)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_category"], lwb.EVENT_CATEGORY)
        # exact string equality is how LR-4 matches, so the ref must be the basis' own ref
        self.assertEqual(event["waiting_on_ref"], self.basis["waiting_on_ref"])
        self.assertEqual(event["attributes"]["baseline_head_ref"], HEAD_V3)
        self.assertEqual(event["attributes"]["observed_head_ref"], HEAD_V4)
        self.assertTrue(event["event_id"].startswith("evt:"))
        self.assertIn(self.basis["basis_ref"], event["source_refs"])

    def test_the_waiting_spec_carries_no_deadline_and_keeps_the_foreground(self):
        spec = lwb.waiting_spec(self.basis)
        self.assertEqual(spec["condition_kind"], lwb.CONDITION_KIND)
        self.assertNotIn("condition_expires_at", spec)
        self.assertNotIn("schedule_agenda", spec)
        self.assertIs(spec["release_foreground"], False)
        params = spec["parameters"]
        self.assertEqual(params[lwb.PARAM_BASELINE_HEAD], HEAD_V3)
        self.assertEqual(params[lwb.PARAM_SOURCE], lwb.SOURCE_WORK_HEAD)
        self.assertEqual(params["waiting_on_ref"], self.basis["waiting_on_ref"])

    def test_the_condition_it_binds_can_be_read_back_to_the_same_basis(self):
        condition = {"condition_id": "rcond:abc", "activity_id": "actv:work1",
                     "status": "PENDING",
                     "parameters": lwb.condition_parameters(self.basis)}
        self.assertTrue(lwb.is_artifact_head_condition(condition))
        rebuilt = lwb.basis_from_condition(condition)
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt["baseline_head_ref"], HEAD_V3)
        self.assertEqual(rebuilt["waiting_on_ref"], self.basis["waiting_on_ref"])
        # A condition this module did not write is never re-interpreted.
        foreign = {"condition_id": "rcond:x", "parameters": {"fact_key": "door_open"}}
        self.assertFalse(lwb.is_artifact_head_condition(foreign))
        self.assertIsNone(lwb.basis_from_condition(foreign))


# ---------------------------------------------------------------------------------
# the cycle: basis -> owners, and the bounded upkeep
# ---------------------------------------------------------------------------------


class _FakePort:
    """A fake ManagedArtifact read port.  It is a stand-in, and it says so."""

    def __init__(self, head=HEAD_V3):
        self.head = head
        self.head_reads = 0
        self.refuse = None

    def list_artifacts(self):
        return [{"artifact_id": OWNER_ID, "lifecycle": "ACTIVE", "revision": 3,
                 "title": "草稿"}]

    def read_head(self, artifact_id):
        self.head_reads += 1
        if self.refuse is not None:
            raise RuntimeError(self.refuse)
        return {"version_id": self.head, "version_number": 3}

    def resume_point(self, artifact_id):
        from life_artifact_port import ArtifactResumePoint
        return ArtifactResumePoint(artifact_id=OWNER_ID, title="草稿",
                                   head_version_id=self.head, version_number=3,
                                   content="一段", lifecycle="ACTIVE", revision=3)


class _ClosedPort:
    """A port whose only artifact is already ARCHIVED (the owner's own answer, not a guess)."""

    def list_artifacts(self):
        return [{"artifact_id": OWNER_ID, "lifecycle": "ARCHIVED", "revision": 4,
                 "title": "收尾了"}]

    def read_head(self, artifact_id):
        return {"version_id": HEAD_V3, "version_number": 3}

    def resume_point(self, artifact_id):
        from life_artifact_port import ArtifactResumePoint
        return ArtifactResumePoint(artifact_id=OWNER_ID, title="收尾了", head_version_id=HEAD_V3,
                                   version_number=3, content="一段", lifecycle="ARCHIVED",
                                   revision=4)


class _FakeCoordinator:
    def __init__(self):
        self.kwargs = None

    def run_decision_and_adoption_cycle(self, **kwargs):
        self.kwargs = dict(kwargs)
        return {"correlation_id": "corr:test", "decision_result": {"decision_record": _NoAction()},
                "adoption_result": None, "integration_record": None}


class _NoAction:
    decision = "NO_ACTION"
    decision_id = "dec:test"
    reason_codes = ["NO_ACTION_CHOSEN"]
    short_rationale = "nothing to do"
    selected_candidate_ref = None
    target_activity_ref = None


class _FakeActivityRead:
    def __init__(self, activity):
        self.activity = activity

    def list_open_activities(self):
        return [self.activity]

    def get_activity(self, activity_id):
        return self.activity


class _FakeRegistry:
    def list_adapters(self):
        return []


class _FakeRuntime:
    """Just enough owner surface for one round.  Every member is a read."""

    def __init__(self, state_root, activity):
        self.state_root = str(state_root)
        self.activity_read = _FakeActivityRead(activity)
        self.candidate_registry = _FakeRegistry()
        self.coordinator = _FakeCoordinator()
        self.action_read = None
        self.condition_store = None
        self.waiting_coordinator = None

    def canonical_state(self):
        return {"foreground_activity_ref": "actv:work1"}


def _activity(status="ACTIVE"):
    return {"activity_ref": "actv:work1", "activity_id": "actv:work1", "status": status,
            "revision": 3, "activity_kind": "CONTINUE_PERSONAL_ARTIFACT",
            "target_ref": "artifact:" + OWNER_ID}


@POSIX_ONLY
class WaitBasisCycleTest(unittest.TestCase):
    def _cycle(self, *, head=HEAD_V3, status="ACTIVE", seed_no_change=True, name="cycle"):
        import autonomous_life_cycle as alc
        import life_step_readiness as lsr

        root = fixture_dir(name)
        runtime = _FakeRuntime(root, _activity(status))
        port = _FakePort(head=head)
        cycle = alc.AutonomousLifeCycle(runtime, artifact_port=port, enabled=True,
                                        env={"CHIYA_TEST": "1"}, pause_state_root=root,
                                        continuity_state_root=root)
        if seed_no_change:
            # A real recorded no_change at the owner's current head (the ledger the cycle reads).
            seeded = lsr.StepReadiness(
                available=False, reason_code=lsr.REASON_WORK_NO_CHANGE,
                activity_ref="actv:work1", activity_kind="CONTINUE_PERSONAL_ARTIFACT",
                target_ref="artifact:" + OWNER_ID, source_revision=head)
            cycle.reconsideration.record(readiness=seeded, outcome="NO_ACTION", at=TS)
        return cycle, runtime, port

    def test_no_basis_before_the_owner_says_there_is_no_step(self):
        cycle, _runtime, _port = self._cycle(seed_no_change=False, name="no-basis")
        readiness = cycle.step_readiness(act=True)
        self.assertTrue(readiness.available)
        self.assertIsNone(cycle.wait_basis(readiness=readiness))

    def test_a_paused_foreground_gets_no_fresh_wait_basis(self):
        """PAUSED keeps the foreground and has its own RESUME question; never a new WAIT."""

        cycle, _runtime, _port = self._cycle(status="PAUSED", name="paused")
        readiness = cycle.step_readiness(act=True)
        self.assertIsNone(cycle.wait_basis(readiness=readiness))

    def test_the_round_hands_the_basis_and_waiting_spec_to_the_owners(self):
        cycle, runtime, _port = self._cycle(name="round")
        out = cycle.evaluate_once(trigger_ref="evt:test", observed_at=TS)
        sent = runtime.coordinator.kwargs
        self.assertIsNotNone(sent, out)
        basis = lwb.work_head_basis(artifact_id=OWNER_ID, baseline_head=HEAD_V3)
        self.assertEqual(sent["known_waiting_basis_ref"], basis["basis_ref"])
        self.assertEqual(sent["waiting_spec"]["waiting_on_ref"], basis["waiting_on_ref"])
        self.assertEqual(sent["waiting_spec"]["parameters"][lwb.PARAM_BASELINE_HEAD], HEAD_V3)
        self.assertEqual(out["wait_basis"]["basis_ref"], basis["basis_ref"])
        # A WAIT basis is never a target or an action: the round still reports her own decision.
        self.assertEqual(out["decision"], "NO_ACTION")

    def test_a_round_without_a_dependency_sends_no_basis_at_all(self):
        cycle, runtime, _port = self._cycle(seed_no_change=False, name="no-basis-round")
        out = cycle.evaluate_once(trigger_ref="evt:test", observed_at=TS)
        sent = runtime.coordinator.kwargs
        self.assertIsNotNone(sent, out)
        self.assertIsNone(sent["known_waiting_basis_ref"])
        self.assertIsNone(sent["waiting_spec"])
        self.assertIsNone(out["wait_basis"])

    def _waiting_cycle(self, head_after, *, refuse=False, name="maintain"):
        cycle, runtime, port = self._cycle(name=name)
        basis = lwb.work_head_basis(artifact_id=OWNER_ID, baseline_head=HEAD_V3)
        condition = {"condition_id": "rcond:wait1", "activity_id": "actv:work1",
                     "status": "PENDING", "condition_kind": lwb.CONDITION_KIND,
                     "waiting_on_ref": basis["waiting_on_ref"],
                     "activity_revision_at_bind": 4,
                     "parameters": lwb.condition_parameters(basis)}

        class _Store:
            def load_state(self):
                return {"conditions": {"rcond:wait1": dict(condition)}}

        class _Waiting:
            def __init__(self):
                self.events = []
                self.scans = []
                #: LR-4's own OPEN set, as the eligibility feed would read it back.
                self.eligibilities = []

            def scan_due_agenda(self, *, now_at=None):
                self.scans.append(now_at)
                return {"due_agenda_ids": [], "expired_agenda_ids": [],
                        "emitted_eligibilities": [], "auto_resumed": False,
                        "llm_calls": 0}

            def evaluate_event(self, event):
                self.events.append(event)
                emitted = [{"eligibility_id": "relig:open1"}]
                self.eligibilities.extend(emitted)
                return {"event_id": event["event_id"], "satisfied_condition_ids": ["rcond:wait1"],
                        "expired_condition_ids": [],
                        "emitted_eligibilities": emitted,
                        "llm_calls": 0, "auto_resumed": False}

        waiting = _Waiting()
        runtime.condition_store = _Store()
        runtime.waiting_coordinator = waiting
        port.head = head_after
        port.refuse = "LIFE_SUPPLY_UNREACHABLE" if refuse else None
        return cycle, waiting, basis

    def test_an_unchanged_head_emits_no_event(self):
        cycle, waiting, _basis = self._waiting_cycle(HEAD_V3, name="same-head")
        report = cycle.maintain_waiting(observed_at=TS)
        self.assertEqual(waiting.events, [])
        self.assertEqual(report["checked"], 1)
        self.assertEqual(report["unchanged"], 1)
        self.assertEqual(report["evaluated"], 0)
        self.assertEqual(waiting.scans, [TS])
        self.assertFalse(report["due_agenda"]["auto_resumed"])

    def test_a_really_new_head_becomes_one_event_and_the_owner_decides(self):
        cycle, waiting, basis = self._waiting_cycle(HEAD_V4, name="new-head")
        report = cycle.maintain_waiting(observed_at=TS)
        self.assertEqual(len(waiting.events), 1, report)
        event = waiting.events[0]
        self.assertEqual(event["waiting_on_ref"], basis["waiting_on_ref"])
        self.assertEqual(event["attributes"]["observed_head_ref"], HEAD_V4)
        self.assertEqual(report["satisfied"], ["rcond:wait1"])
        self.assertEqual(report["emitted_eligibilities"], ["relig:open1"])
        # The upkeep never resumes anything itself.
        self.assertEqual(report["llm_calls"], 0)
        self.assertFalse(report["due_agenda"]["auto_resumed"])

    def test_an_unreadable_owner_satisfies_nothing(self):
        cycle, waiting, _basis = self._waiting_cycle(HEAD_V4, refuse=True, name="refused")
        report = cycle.maintain_waiting(observed_at=TS)
        self.assertEqual(waiting.events, [])
        self.assertEqual(report["evaluated"], 0)
        self.assertTrue(any(str(e).startswith("OWNER_HEAD_UNREADABLE")
                            for e in report["errors"]), report)

    def test_a_closed_work_is_not_selected_as_a_continuation_point(self):
        """The §1 defect this package fixes: a bound work the owner no longer reports as
        resumable must not keep producing a step the owner refuses every round."""

        cycle, _runtime, _port = self._cycle(name="not-resumable")
        cycle.artifact_port = _ClosedPort()
        # The *bound work* main path (not the recency fallback) is the one that ignored
        # ``resumable`` before this package.
        cycle.work_artifact_id = OWNER_ID
        point, selection = cycle.resume_selection()
        self.assertIsNone(point)
        self.assertEqual(selection["selected_by"], "NOT_RESUMABLE")
        self.assertEqual(selection["lifecycle"], "ARCHIVED")
        self.assertIs(selection["resumable"], False)
        # Nothing is retargeted: the selection still names *this* work, not another one.
        self.assertEqual(selection["artifact_id"], OWNER_ID)

    def test_a_condition_this_module_did_not_bind_is_never_re_checked(self):
        """Only this instance's own dependency conditions are re-read; LR-4's others are its own."""

        cycle, waiting, _basis = self._waiting_cycle(HEAD_V4, name="foreign-condition")

        class _ForeignStore:
            def load_state(self):
                return {"conditions": {
                    "rcond:foreign": {"condition_id": "rcond:foreign", "status": "PENDING",
                                      "activity_id": "actv:other",
                                      "parameters": {"fact_key": "door_open",
                                                     "expected_value": True}},
                    "rcond:done": {"condition_id": "rcond:done", "status": "SATISFIED",
                                   "activity_id": "actv:work1",
                                   "parameters": {}},
                }}

        cycle.runtime.condition_store = _ForeignStore()
        report = cycle.maintain_waiting(observed_at=TS)
        self.assertEqual(waiting.events, [])
        self.assertEqual(report["checked"], 0)
        self.assertEqual(report["changed"], 0)


@POSIX_ONLY
class WaitTickMaintenanceTest(unittest.TestCase):
    """The *tick* order (assembly.runner_tick): maintain -> poll feeds -> due.

    The parent's gen1 evidence: the tick used to poll first and only reach ``maintain_waiting``
    inside ``runner.wake``, so (a) a NOT_DUE window with no event scanned nothing at all and never
    re-read the dependency it was waiting on, and (b) an eligibility LR-4 had just opened could
    not be seen by the eligibility feed until the *next* tick.  Both cases below fail on that old
    shape and pass on the fixed one.
    """

    def _runner(self, *, name: str, head=HEAD_V4):
        from datetime import datetime, timezone

        import autonomous_life_runner as alr

        # The same real readiness/ledger harness the cycle cases use (a fake *owner*, not a fake
        # readiness): only the port and the LR-4 coordinator are stand-ins.
        cycle, waiting, _basis = WaitBasisCycleTest("run")._waiting_cycle(head, name=name)
        fixed = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)
        runner = alr.AutonomousLifeRunner(cycle, state_root=str(cycle.state_root), env={},
                                          enabled=True, interval_minutes=30,
                                          clock=lambda: fixed)
        return cycle, waiting, runner, fixed

    @staticmethod
    def _poller(waiting, *, emit: bool):
        class _Poller:
            """The real eligibility feed reads LR-4's OPEN set -- it can only see what exists."""

            def __init__(self):
                self.last_errors = ()
                self.polls = 0
                self.seen = []
                #: How many upkeeps had run when the poll happened (order evidence).
                self.scans_at_poll = []

            def poll(self):
                self.polls += 1
                self.scans_at_poll.append(len(waiting.scans))
                rows = ([{"kind": "RESUME_ELIGIBILITY_OPENED",
                          "ref": str(row["eligibility_id"])} for row in waiting.eligibilities]
                        if emit else [])
                self.seen.append(len(rows))
                return rows

        return _Poller()

    def test_the_tick_maintains_before_it_polls_so_the_same_tick_eligibility_is_visible(self):
        import autonomous_life_assembly as asm

        cycle, waiting, runner, _fixed = self._runner(name="tick-order")
        poller = self._poller(waiting, emit=True)
        report = asm.runner_tick({"state": asm.READY, "runner": runner,
                                  "feed_poller": poller})
        self.assertTrue(report["ok"], report)
        # The upkeep ran exactly once (the wake below was told not to run it again) ...
        self.assertEqual(len(waiting.scans), 1, waiting.scans)
        # ... and it ran *before* the poll, which is what makes the same-tick eligibility visible.
        self.assertEqual(poller.scans_at_poll, [1], poller.scans_at_poll)
        self.assertEqual(poller.seen, [1], poller.seen)
        self.assertEqual(report["waiting"]["emitted_eligibilities"], ["relig:open1"])
        self.assertEqual(len(report["events"]), 1, report["events"])
        self.assertEqual(report["events"][0]["reason"], "RESUME_ELIGIBILITY_OPENED")
        # The event really became a considered round in this same tick (not silence, not blocked):
        # the eligibility the upkeep opened was usable immediately instead of one tick later.
        self.assertEqual(report["events"][0]["status_class"], "choice", report["events"][0])

    def test_a_closed_window_still_maintains_without_spending_a_request_or_a_round(self):
        import autonomous_life_assembly as asm
        import autonomous_life_runner as alr

        cycle, waiting, runner, fixed = self._runner(name="tick-not-due")
        poller = self._poller(waiting, emit=False)
        # The window is closed for this tick: the old code returned NOT_DUE and never maintained.
        runner._mark_checked(fixed)
        self.assertFalse(runner.due(now=fixed)[0])
        spent_before = cycle.budget.spent_today()
        report = asm.runner_tick({"state": asm.READY, "runner": runner,
                                  "feed_poller": poller})
        self.assertEqual(report["due"]["status"], alr.WAKE_NOT_DUE, report["due"])
        # The upkeep still ran, still first, and still produced the owner's eligibility ...
        self.assertEqual(poller.scans_at_poll, [1], poller.scans_at_poll)
        self.assertEqual(len(waiting.scans), 1)
        self.assertEqual(report["waiting"]["checked"], 1, report["waiting"])
        self.assertEqual(report["waiting"]["emitted_eligibilities"], ["relig:open1"])
        self.assertEqual(cycle.last_waiting_maintenance["llm_calls"], 0)
        # ... and nothing was considered or spent: no event wake happened in this tick.
        self.assertEqual(report["events"], [])
        self.assertEqual(cycle.budget.spent_today(), spent_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
