#!/usr/bin/env python3
"""C package tests: experience -> the next choice (MEM-GATE / CHOICE / FUTURE).

What is real here
-----------------
* the **past** column is real end to end: real ``app.evidence.EvidenceStore`` M0 rows,
  written by the real ``MemoryAdmissionExperienceConsumer`` through the real
  ``LifeExperiencePort``, governed by the real ``MemoryControlLedger``, and read back
  through that same port.  The handoff events are real ``LifeExperienceHandoffEvent``
  objects from ``life_integration_ag2`` (the AG-2 producer type), not hand-rolled dicts;
* the **unfinished / waiting** column is a *fixture*: no Activity can legally be created
  without the owner-backed Decision+Adoption admission pair, so this file does not fake an
  Activity write.  Instead it feeds read-only fixture records built from the **owning
  domain's own dataclasses** (``activity_waiting_agenda.AgendaItem`` /
  ``ResumeEligibility``, which validate themselves) and from the exact key set the real
  ``ActivityCommandService.start_activity`` writes (pinned by ``OwnerShapeDriftGuards``).
  The fixture objects expose only the read methods the seam is allowed to call: anything
  else raises, so a hidden write cannot pass.
* the **waiting-ref** data uses references from the legal intersection of the two
  vocabularies that used to disagree (LR-2 ``activity_continuity`` and admission
  ``activity_admission``), both now derived from ``waiting_ref_vocab``.  The whitelist is
  not touched to suit these tests; if the two ever diverge again, ``test_waiting_refs...``
  fails instead of the data being widened.

Nothing here claims a live model, a deployment, a 24h run or real model causality.

Run:  python3 tests/test_life_continuity_inputs.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
NATIVE = ROOT / "components" / "native"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
                str(NATIVE / "memory_runtime_v1")]

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import activity_waiting_agenda as awa  # noqa: E402
import life_continuity_inputs as lci  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import waiting_ref_vocab as wrv  # noqa: E402
from app.evidence import EvidenceStore  # noqa: E402
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402
from life_experience_port import LifeExperiencePort, life_conversation_id  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

#: The Memory owner is the persona ("chiyo"), not a Supply workspace subject: M0's own rule
#: requires the LIFE_ORIGIN provenance triple's speaker to be "chiyo" and the AG-2 handoff
#: carries the global subject ("chiyo.global"), which the boundary normalizes to it.
MEMORY_OWNER = "chiyo"
HOME = "home:life-v1-c-fixture"
NOW = "2026-10-16T12:00:00+00:00"

#: Session fixture root (unique per work package).  Nothing pre-existing is removed.
NATIVE_FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")


def _fixture_dir(prefix: str) -> tempfile.TemporaryDirectory:
    base = NATIVE_FIXTURE_ROOT if NATIVE_FIXTURE_ROOT.is_dir() else None
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(base) if base else None)


def binding(**overrides) -> "lci.ContinuityBinding":
    payload = {"subject": MEMORY_OWNER, "home": HOME, "now": NOW,
               "owner_refs": {"activity": "life:activity", "artifact": "life:artifact",
                              "agenda": "life:agenda", "world": "life:world"}}
    payload.update(overrides)
    return lci.ContinuityBinding(**payload)


# ---------------------------------------------------------------------------------------
# real Memory fixture
# ---------------------------------------------------------------------------------------


class MemoryFixture:
    """Real M0 + real control ledger + the real boundary port and admission consumer."""

    def __init__(self, root: Path) -> None:
        os.chmod(root, 0o700)
        self.root = root
        self.evidence_db = root / "evidence.sqlite"
        self.controls_db = root / "controls.sqlite"
        self.store = EvidenceStore(self.evidence_db)
        self.ledger = MemoryControlLedger(self.controls_db, self.evidence_db, owner=MEMORY_OWNER,
                                          conversation=life_conversation_id(MEMORY_OWNER))
        self.ledger.initialize()
        self.port = LifeExperiencePort(self.evidence_db, controls_db=self.controls_db,
                                       subject=MEMORY_OWNER)
        self.consumer = MemoryAdmissionExperienceConsumer(self.port, enabled=True)

    def admit(self, event: dict) -> dict:
        return self.consumer.receive_experience_handoff(event)

    def rows(self, limit: int = 10) -> list[dict]:
        return self.store.recent_verified_outcomes(memory_owner=MEMORY_OWNER, limit=limit)

    def governance_probe(self) -> dict:
        return {"healthy": self.ledger.projection().healthy}

    def reader(self, **kwargs) -> "lci.LifeContinuityInputs":
        kwargs.setdefault("binding", binding())
        kwargs.setdefault("past_source", self.port)
        kwargs.setdefault("governance_probe", self.governance_probe)
        return lci.LifeContinuityInputs(**kwargs)


def handoff(handoff_id: str, *, activity_ref: str, revision: int = 3,
            kind: str = "SIGNIFICANT_PROGRESS", occurred_at: str = NOW,
            settlement_refs: tuple = (), artifact_refs: tuple = ()) -> dict:
    """A real AG-2 ``LifeExperienceHandoffEvent`` (the producer type), as the consumer sees it."""
    return ag2.LifeExperienceHandoffEvent(
        handoff_id=handoff_id,
        correlation_id="corr:c-fixture",
        activity_ref=activity_ref,
        activity_revision=revision,
        activity_kind="PERSONAL_WORK",
        terminal_or_milestone_kind=kind,
        decision_refs=["dec:fixture"],
        adoption_refs=["adopt:fixture"],
        action_refs=["ar0act:fixture"],
        settlement_refs=list(settlement_refs),
        progress_refs=["prog:fixture"],
        completion_evidence_ref=None,
        fact_refs=[],
        artifact_refs=list(artifact_refs),
        world_refs=[],
        occurred_at=occurred_at,
        observed_at=occurred_at,
        recorded_at=occurred_at,
        causal_parent_refs=[activity_ref],
    ).to_dict()


# ---------------------------------------------------------------------------------------
# read-only fixture sources (Agenda / Activity).  No owner write is ever attempted.
# ---------------------------------------------------------------------------------------


class ReadOnlySource:
    """Forwards exactly the named reads; any other attribute access is a test failure."""

    def __init__(self, name: str, target, allowed: set[str]) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_allowed", set(allowed))

    def __getattr__(self, item: str):
        if item in object.__getattribute__(self, "_allowed"):
            return getattr(object.__getattribute__(self, "_target"), item)
        raise AssertionError(
            f"{object.__getattribute__(self, '_name')}.{item} is not a read this seam may "
            "call: the continuity inputs are read-only")


def activity(activity_id: str, *, status: str = "ACTIVE", updated_at: str = NOW,
             checkpoint_ref: str = "chk:stage:001", waiting_on_ref=None,
             resume_condition_ref=None, revision: int = 5, title: str = "夜里的手记") -> dict:
    """One canonical Activity record in exactly the owner's own shape.

    The keys used here are the ones ``ActivityCommandService.start_activity`` writes; see
    ``OwnerShapeDriftGuards`` for the guard that keeps them honest.
    """
    return {
        "activity_id": activity_id,
        "schema_version": "chiyo.life.activity_instance.v1",
        "revision": revision,
        "subject_id": awa.GLOBAL_SUBJECT_ID,
        "activity_kind": "PERSONAL_WORK",
        "title": title,
        "subject_refs": [],
        "status": status,
        "interruptibility": "INTERRUPTIBLE",
        "origin_ref": None,
        "decision_ref": "dec:fixture",
        "adoption_ref": "adopt:fixture",
        "started_at": updated_at,
        "updated_at": updated_at,
        "paused_at": None,
        "waiting_since": updated_at if status == "WAITING" else None,
        "ended_at": None,
        "waiting_on_ref": waiting_on_ref,
        "resume_condition_ref": resume_condition_ref,
        "checkpoint_ref": checkpoint_ref,
        "last_action_ref": None,
        "completion_evidence_ref": None,
        "last_transition_ref": "trn_fixture",
        "terminal_reason_code": None,
        "terminal_source_ref": None,
        "source_refs": [],
    }


def eligibility(eligibility_id: str, *, activity_ref: str, eligible_at: str,
                resume_condition_ref: str = "rcond:fixture",
                waiting_episode_id: str = "wep:fixture",
                status: str = awa.ELIG_STATUS_OPEN,
                source_event_refs: tuple = ("event:cond-met",)) -> dict:
    """A real LR-4 ``ResumeEligibility`` (validated by the owner's own dataclass)."""
    return awa.ResumeEligibility(
        eligibility_id=eligibility_id,
        schema_version=awa.SCHEMA_RESUME_ELIGIBILITY,
        revision=1,
        subject_id=awa.GLOBAL_SUBJECT_ID,
        activity_id=activity_ref,
        activity_revision=4,
        waiting_episode_id=waiting_episode_id,
        resume_condition_ref=resume_condition_ref,
        agenda_ref=None,
        source_event_refs=list(source_event_refs),
        causal_parent_refs=[resume_condition_ref],
        eligible_at=eligible_at,
        recorded_at=eligible_at,
        policy_version=awa.POLICY_VERSION_LR4,
        status=status,
        idempotency_key=f"elig:{eligibility_id}",
    ).to_dict()


def agenda_item(agenda_id: str, *, activity_ref: str, due_at: str,
                resume_condition_ref: str = "rcond:fixture",
                waiting_episode_id: str = "wep:fixture",
                status: str = awa.AGENDA_STATUS_SCHEDULED) -> dict:
    """A real LR-4 ``AgendaItem`` (validated by the owner's own dataclass)."""
    return awa.AgendaItem(
        agenda_id=agenda_id,
        schema_version=awa.SCHEMA_AGENDA_ITEM,
        revision=1,
        subject_id=awa.GLOBAL_SUBJECT_ID,
        activity_id=activity_ref,
        activity_revision_at_bind=4,
        waiting_episode_id=waiting_episode_id,
        resume_condition_ref=resume_condition_ref,
        trigger_kind=awa.TRIGGER_KIND_TIME_AT,
        due_at=due_at,
        window_start=None,
        window_end=None,
        source_refs=[resume_condition_ref],
        causal_parent_refs=[],
        status=status,
        created_at=due_at,
        idempotency_key=f"agenda:{agenda_id}",
    ).to_dict()


class AgendaFixture:
    """Read-only Agenda view: the three reads the real ``WaitingCoordinator`` also offers."""

    def __init__(self, *, eligibilities=(), items=()) -> None:
        self._eligibilities = [dict(e) for e in eligibilities]
        self._items = [dict(i) for i in items]
        self.calls: list[str] = []

    def list_agenda_items(self, *, activity_id=None, only_open: bool = False) -> list[dict]:
        self.calls.append("list_agenda_items")
        out = list(self._items)
        if activity_id is not None:
            out = [i for i in out if i.get("activity_id") == activity_id]
        if only_open:
            out = [i for i in out if i.get("status") in awa.OPEN_AGENDA_STATUSES]
        return out

    def list_eligibilities(self, *, activity_id=None, only_open: bool = False) -> list[dict]:
        self.calls.append("list_eligibilities")
        out = list(self._eligibilities)
        if activity_id is not None:
            out = [e for e in out if e.get("activity_id") == activity_id]
        if only_open:
            out = [e for e in out if e.get("status") in awa.OPEN_ELIGIBILITY_STATUSES]
        return out

    def get_eligibility(self, eligibility_id: str):
        self.calls.append("get_eligibility")
        for e in self._eligibilities:
            if e.get("eligibility_id") == eligibility_id:
                return dict(e)
        return None


class ActivityFixture:
    """Read-only Activity view: the read the real ``ActivityReadService`` also offers."""

    def __init__(self, records=()) -> None:
        self._records = [dict(r) for r in records]

    def list_open_activities(self) -> list[dict]:
        return [dict(r) for r in self._records
                if r.get("status") in lci.UNFINISHED_ACTIVITY_STATUSES]


def source(name: str, target, allowed: set[str]) -> ReadOnlySource:
    return ReadOnlySource(name, target, allowed)


AGENDA_READS = {"list_agenda_items", "list_eligibilities", "get_eligibility"}
ACTIVITY_READS = {"list_open_activities", "get_activity"}
PAST_READS = {"recent", "subject"}


class ContinuityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dirs: list[tempfile.TemporaryDirectory] = []

    def tearDown(self) -> None:
        for handle in self._dirs:
            handle.cleanup()

    def make_dir(self, prefix: str) -> Path:
        handle = _fixture_dir(prefix)
        self._dirs.append(handle)
        return Path(handle.name)

    def memory(self) -> MemoryFixture:
        return MemoryFixture(self.make_dir("c-continuity-memory-"))


# ---------------------------------------------------------------------------------------
# 1. a real admitted consequence is read back
# ---------------------------------------------------------------------------------------


class RealAdmittedPast(ContinuityTestCase):
    def test_admitted_consequence_is_read_back_with_its_owner_refs(self):
        memory = self.memory()
        event = handoff("lexp:aaa111", activity_ref="actv_a", revision=3,
                        settlement_refs=("settle:owner-1",),
                        artifact_refs=("artifact:artifact:abc",))
        self.assertEqual(memory.admit(event)["disposition"], "ACCEPT")
        self.assertEqual(len(memory.rows()), 1, memory.rows())

        inputs = memory.reader().read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_OK)
        self.assertEqual(inputs.evidence_roots, 1)
        row = inputs.past[0]
        self.assertEqual(row["source_ref"], "lexp:aaa111")
        self.assertEqual(row["subject"], MEMORY_OWNER)
        self.assertEqual(row["home"], HOME)
        self.assertEqual(row["bound_at"], NOW)
        self.assertEqual(row["activity_ref"], "actv_a")
        self.assertEqual(row["artifact_ref"], "artifact:artifact:abc")
        self.assertEqual(row["owner_result"], "settle:owner-1")
        self.assertEqual(row["outcome_kind"], "SIGNIFICANT_PROGRESS")
        self.assertIn("所有者回执", row["text"])

    def test_nothing_admitted_is_not_a_failure_and_never_a_narration(self):
        memory = self.memory()
        inputs = memory.reader().read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_NO_HISTORY)
        self.assertEqual(inputs.past, [])
        self.assertEqual(inputs.evidence_roots, 0)
        self.assertEqual(memory.rows(), [])

    def test_a_long_downtime_gap_invents_no_experience(self):
        memory = self.memory()
        memory.admit(handoff("lexp:gap1", activity_ref="actv_a", revision=2,
                             occurred_at="2026-10-14T03:00:00+00:00"))
        # Ten days later, with no activity in between: exactly the admitted rows, nothing else.
        inputs = memory.reader(binding=binding(now="2026-10-26T09:00:00+00:00")).read()
        real_refs = [r["primary_source_ref_id"] for r in memory.rows()]
        self.assertEqual([row["source_ref"] for row in inputs.past], real_refs)
        self.assertEqual(inputs.evidence_roots, len(real_refs))
        self.assertNotIn("停机", json.dumps(inputs.to_dict(), ensure_ascii=False))
        self.assertEqual(inputs.source_states[lci.OWNER_MEMORY], lci.PAST_STATE_OK)

    def test_the_read_itself_writes_nothing_to_m0(self):
        memory = self.memory()
        memory.admit(handoff("lexp:ro1", activity_ref="actv_a"))
        before = memory.rows()
        memory.reader(activity_source=source("activity", ActivityFixture([activity("actv_a")]),
                                             ACTIVITY_READS)).read()
        self.assertEqual(memory.rows(), before)


# ---------------------------------------------------------------------------------------
# 2. the same root never adds evidence
# ---------------------------------------------------------------------------------------


class DuplicateRoots(ContinuityTestCase):
    def test_the_same_delivery_twice_is_one_experience(self):
        memory = self.memory()
        event = handoff("lexp:dup1", activity_ref="actv_a", settlement_refs=("settle:owner-1",))
        self.assertEqual(memory.admit(event)["disposition"], "ACCEPT")
        self.assertEqual(memory.admit(event)["disposition"], "DUPLICATE")
        self.assertEqual(len(memory.rows()), 1, memory.rows())
        inputs = memory.reader().read()
        self.assertEqual(inputs.evidence_roots, 1)
        self.assertEqual(len(inputs.past), 1)

    def test_the_same_root_at_a_later_revision_is_not_extra_evidence(self):
        memory = self.memory()
        memory.admit(handoff("lexp:root3", activity_ref="actv_a", revision=3,
                             settlement_refs=("settle:owner-1",)))
        memory.admit(handoff("lexp:root4", activity_ref="actv_a", revision=4,
                             settlement_refs=("settle:owner-1",)))
        self.assertEqual(len(memory.rows()), 2, memory.rows())
        inputs = memory.reader().read()
        self.assertEqual(inputs.evidence_roots, 1, inputs.past)
        self.assertEqual(len(inputs.past), 1)

    def test_a_different_root_is_extra_evidence(self):
        memory = self.memory()
        memory.admit(handoff("lexp:r1", activity_ref="actv_a", settlement_refs=("settle:owner-1",)))
        memory.admit(handoff("lexp:r2", activity_ref="actv_a", settlement_refs=("settle:owner-2",)))
        self.assertEqual(memory.reader().read().evidence_roots, 2)


# ---------------------------------------------------------------------------------------
# 3. fail closed: unreadable governance, wrong subject, unwired source
# ---------------------------------------------------------------------------------------


class FailClosed(ContinuityTestCase):
    def test_unreadable_control_ledger_refuses_instead_of_reporting_a_quiet_day(self):
        memory = self.memory()
        memory.admit(handoff("lexp:blind", activity_ref="actv_a",
                             settlement_refs=("settle:owner-1",)))
        blind = LifeExperiencePort(memory.evidence_db, controls_db=memory.root / "missing.sqlite",
                                   subject=MEMORY_OWNER)
        inputs = memory.reader(past_source=blind,
                               governance_probe=lambda: {"healthy": False,
                                                         "reason": "control ledger missing"}
                               ).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_UNREADABLE)
        self.assertEqual(inputs.past, [])
        self.assertEqual(inputs.evidence_roots, 0)
        self.assertEqual(inputs.refusals[0]["reason_code"], lci.REFUSAL_GOVERNANCE_UNHEALTHY)
        # ...and the refusal is never dressed up as history: the next step is not "continue
        # from an owner-confirmed object".
        self.assertNotEqual((inputs.next_step or {}).get("reason_code"), lci.NEXT_STEP_HISTORY)

    def test_a_probe_that_raises_is_not_a_healthy_answer(self):
        memory = self.memory()
        memory.admit(handoff("lexp:boom", activity_ref="actv_a"))

        def explode():
            raise RuntimeError("control ledger unreadable")

        inputs = memory.reader(governance_probe=explode).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_UNREADABLE)
        self.assertEqual(inputs.past, [])
        self.assertEqual(inputs.refusals[0]["reason_code"], lci.REFUSAL_GOVERNANCE_UNHEALTHY)

    def test_a_past_source_bound_to_another_subject_is_refused(self):
        memory = self.memory()
        memory.admit(handoff("lexp:other", activity_ref="actv_a"))
        other = LifeExperiencePort(memory.evidence_db, controls_db=memory.controls_db,
                                   subject="someone-else")
        inputs = memory.reader(past_source=other).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_WRONG_SUBJECT)
        self.assertEqual(inputs.past, [])
        self.assertEqual(inputs.refusals[0]["reason_code"], lci.REFUSAL_WRONG_SUBJECT)
        self.assertEqual(inputs.refusals[0]["detail"]["binding_subject"], MEMORY_OWNER)

    def test_a_foreign_row_is_dropped_even_when_the_source_declares_no_subject(self):
        class StubPast:
            def recent(self, *, limit=5):
                return [{"experience_id": "ev-foreign", "source_ref": "lexp:foreign",
                         "occurred_at": NOW, "subject": "someone-else", "text": "别人的事",
                         "activity_ref": "actv_a", "owner_result": "settle:not-mine"},
                        {"experience_id": "ev-mine", "source_ref": "lexp:mine",
                         "occurred_at": NOW, "subject": MEMORY_OWNER, "text": "我的事",
                         "activity_ref": "actv_a", "owner_result": "settle:mine"}]

        inputs = memory_reader_with(StubPast(), activity_source=ActivityFixture([activity("actv_a")])).read()
        self.assertEqual([row["source_ref"] for row in inputs.past], ["lexp:mine"])
        self.assertEqual(inputs.refusals[0]["reason_code"], lci.REFUSAL_FOREIGN_ROW)

    def test_a_source_that_raises_is_reported_not_swallowed(self):
        class BrokenPast:
            subject = MEMORY_OWNER

            def recent(self, *, limit=5):
                raise OSError("memory database is gone")

        inputs = memory_reader_with(BrokenPast()).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_UNREADABLE)
        self.assertEqual(inputs.refusals[0]["reason_code"], lci.REFUSAL_SOURCE_RAISED)

    def test_an_unwired_past_source_is_distinct_from_no_history(self):
        inputs = lci.LifeContinuityInputs(binding=binding()).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_NOT_WIRED)
        self.assertEqual(inputs.refusals[0]["reason_code"], lci.REFUSAL_NOT_WIRED)
        self.assertNotEqual(inputs.past_state, lci.PAST_STATE_NO_HISTORY)

    def test_an_unreadable_agenda_is_not_reported_as_nothing_open(self):
        class BrokenAgenda:
            def list_agenda_items(self, *, activity_id=None, only_open=False):
                raise OSError("agenda store unreadable")

            def list_eligibilities(self, *, activity_id=None, only_open=False):
                raise OSError("agenda store unreadable")

            def get_eligibility(self, eligibility_id):
                return None

        inputs = lci.LifeContinuityInputs(
            binding=binding(), agenda_source=source("agenda", BrokenAgenda(), AGENDA_READS)).read()
        self.assertEqual(inputs.source_states[lci.OWNER_AGENDA], lci.SOURCE_UNREADABLE)
        self.assertEqual(inputs.unfinished, [])
        self.assertTrue(any(r["reason_code"] == lci.REFUSAL_SOURCE_RAISED
                            for r in inputs.refusals), inputs.refusals)


def memory_reader_with(past_source, **kwargs) -> "lci.LifeContinuityInputs":
    kwargs.setdefault("binding", binding())
    kwargs.setdefault("past_source", source("memory", past_source, PAST_READS))
    return lci.LifeContinuityInputs(**kwargs)


# ---------------------------------------------------------------------------------------
# 4. unfinished items and the WAIT gate
# ---------------------------------------------------------------------------------------


class UnfinishedAndWaiting(ContinuityTestCase):
    def test_open_activity_and_agenda_item_both_show_up_with_their_owners(self):
        item = agenda_item("agnd:1", activity_ref="actv_a",
                           due_at="2026-10-16T13:00:00+00:00")
        fixture = AgendaFixture(items=[item])
        inputs = lci.LifeContinuityInputs(
            binding=binding(), activity_source=source("activity", ActivityFixture([activity("actv_a")]),
                                                      ACTIVITY_READS),
            agenda_source=source("agenda", fixture, AGENDA_READS)).read()
        kinds = sorted(item["kind"] for item in inputs.unfinished)
        self.assertEqual(kinds, ["ACTIVITY", "AGENDA_ITEM"])
        activity_item = next(i for i in inputs.unfinished if i["kind"] == "ACTIVITY")
        self.assertEqual(activity_item["owner"], lci.OWNER_ACTIVITY)
        self.assertEqual(activity_item["checkpoint_ref"], "chk:stage:001")
        agenda_entry = next(i for i in inputs.unfinished if i["kind"] == "AGENDA_ITEM")
        self.assertEqual(agenda_entry["owner"], lci.OWNER_AGENDA)
        # the owning dataclass normalizes the timestamp to its own zone; compare with its value
        self.assertEqual(agenda_entry["due_at"], item["due_at"])
        self.assertEqual(inputs.next_recheck_at, item["due_at"])
        self.assertEqual(fixture.calls, ["list_agenda_items", "list_eligibilities"])

    def test_closed_agenda_items_and_terminal_activities_are_not_unfinished(self):
        fixture = AgendaFixture(items=[agenda_item("agnd:done", activity_ref="actv_a",
                                                   due_at="2026-10-16T13:00:00+00:00",
                                                   status=awa.AGENDA_STATUS_CONSUMED)])
        inputs = lci.LifeContinuityInputs(
            binding=binding(),
            activity_source=source("activity", ActivityFixture([
                activity("actv_a"), activity("actv_done", status="COMPLETED")]), ACTIVITY_READS),
            agenda_source=source("agenda", fixture, AGENDA_READS)).read()
        self.assertEqual([i["item_ref"] for i in inputs.unfinished], ["actv_a"])

    def test_a_wait_without_a_new_eligibility_grants_no_resume(self):
        waiting = activity("actv_w", status="WAITING", waiting_on_ref="event:reply",
                           resume_condition_ref="rcond:fixture")
        seen = eligibility("elig:old", activity_ref="actv_w",
                           eligible_at="2026-10-16T09:00:00+00:00")
        inputs = lci.LifeContinuityInputs(
            binding=binding(),
            activity_source=source("activity", ActivityFixture([waiting]), ACTIVITY_READS),
            agenda_source=source("agenda", AgendaFixture(eligibilities=[seen]), AGENDA_READS)
        ).read(known_eligibility_refs=["elig:old"])
        self.assertEqual(len(inputs.waiting), 1)
        entry = inputs.waiting[0]
        self.assertIsNone(entry["resume_eligibility"])
        self.assertEqual(entry["resume_refused_reason"], "NO_NEW_ELIGIBILITY")
        self.assertFalse(inputs.unfinished[0]["resumable"])
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_CHECKPOINT)
        self.assertEqual(inputs.observed_eligibility_refs, ["elig:old"])

    def test_a_new_eligibility_is_the_only_thing_that_grants_resume(self):
        waiting = activity("actv_w", status="WAITING", waiting_on_ref="event:reply",
                           resume_condition_ref="rcond:fixture")
        fresh = eligibility("elig:new", activity_ref="actv_w",
                            eligible_at="2026-10-16T11:30:00+00:00")
        inputs = lci.LifeContinuityInputs(
            binding=binding(),
            activity_source=source("activity", ActivityFixture([waiting]), ACTIVITY_READS),
            agenda_source=source("agenda", AgendaFixture(eligibilities=[fresh]), AGENDA_READS)
        ).read(known_eligibility_refs=[])
        entry = inputs.waiting[0]
        self.assertIsNotNone(entry["resume_eligibility"])
        self.assertEqual(entry["resume_eligibility"]["eligibility_ref"], "elig:new")
        self.assertEqual(entry["resume_eligibility"]["source_event_refs"], ["event:cond-met"])
        self.assertTrue(inputs.unfinished[0]["resumable"])
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_RESUME_WAIT)
        self.assertEqual(inputs.next_step["continue_from_ref"], "rcond:fixture")
        self.assertIn("elig:new", inputs.next_step["evidence_refs"])
        self.assertFalse(inputs.next_step["requires_model_call"])

    def test_the_since_watermark_gates_the_wait_too(self):
        waiting = activity("actv_w", status="WAITING", resume_condition_ref="rcond:fixture")
        older = eligibility("elig:older", activity_ref="actv_w",
                            eligible_at="2026-10-16T10:00:00+00:00")
        newer = eligibility("elig:newer", activity_ref="actv_w",
                            eligible_at="2026-10-16T11:30:00+00:00")
        fixture = AgendaFixture(eligibilities=[older, newer])
        before = lci.LifeContinuityInputs(
            binding=binding(),
            activity_source=source("activity", ActivityFixture([waiting]), ACTIVITY_READS),
            agenda_source=source("agenda", fixture, AGENDA_READS)
        ).read(since="2026-10-16T11:00:00+00:00")
        self.assertEqual(before.next_step["reason_code"], lci.NEXT_STEP_RESUME_WAIT)
        self.assertEqual(before.waiting[0]["resume_eligibility"]["eligibility_ref"], "elig:newer")
        after = lci.LifeContinuityInputs(
            binding=binding(),
            activity_source=source("activity", ActivityFixture([waiting]), ACTIVITY_READS),
            agenda_source=source("agenda", fixture, AGENDA_READS)
        ).read(since="2026-10-16T12:00:00+00:00")
        self.assertIsNone(after.waiting[0]["resume_eligibility"])
        self.assertEqual(after.next_step["reason_code"], lci.NEXT_STEP_CHECKPOINT)

    def test_re_reading_the_same_state_polls_no_model_and_changes_nothing(self):
        memory = self.memory()
        memory.admit(handoff("lexp:stable", activity_ref="actv_a",
                             settlement_refs=("settle:owner-1",)))
        waiting = activity("actv_w", status="WAITING", resume_condition_ref="rcond:fixture")
        fixture = AgendaFixture(eligibilities=[eligibility("elig:x", activity_ref="actv_w",
                                                           eligible_at="2026-10-16T11:00:00+00:00")])
        reader = memory.reader(
            activity_source=source("activity", ActivityFixture([activity("actv_a"), waiting]),
                                   ACTIVITY_READS),
            agenda_source=source("agenda", fixture, AGENDA_READS))
        first = reader.read().to_dict()
        second = reader.read().to_dict()
        self.assertEqual(first, second)
        self.assertEqual(first["llm_calls"], 0)
        self.assertFalse(first["requires_model_call"])

    def test_nothing_open_is_a_quiet_answer_not_an_error(self):
        memory = self.memory()
        inputs = memory.reader(
            activity_source=source("activity", ActivityFixture([]), ACTIVITY_READS),
            agenda_source=source("agenda", AgendaFixture(), AGENDA_READS)).read()
        self.assertEqual(inputs.unfinished, [])
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_NO_ACTION)
        self.assertEqual([c["kind"] for c in inputs.choices], [lci.CHOICE_NO_ACTION])
        self.assertTrue(inputs.choices[0]["preferred"])
        self.assertEqual(inputs.refusals, [])


# ---------------------------------------------------------------------------------------
# 5. the A/B: the same other inputs, different history -> a different next step
# ---------------------------------------------------------------------------------------


class HistoryChangesTheNextStep(ContinuityTestCase):
    def _activities(self) -> ActivityFixture:
        # "actv_a" was touched more recently, so without history it is the one continued.
        return ActivityFixture([
            activity("actv_a", updated_at="2026-10-16T11:50:00+00:00",
                     checkpoint_ref="chk:a:9"),
            activity("actv_b", updated_at="2026-10-16T10:00:00+00:00",
                     checkpoint_ref="chk:b:4"),
        ])

    def test_group_a_empty_history_continues_from_the_checkpoint(self):
        memory = self.memory()
        inputs = memory.reader(activity_source=source("activity", self._activities(),
                                                      ACTIVITY_READS)).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_NO_HISTORY)
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_CHECKPOINT)
        self.assertEqual(inputs.next_step["item_ref"], "actv_a")
        self.assertEqual(inputs.next_step["continue_from_ref"], "chk:a:9")
        self.assertEqual(inputs.next_step["evidence_refs"], ["actv_a"])

    def test_group_b_the_same_inputs_with_history_change_the_object_and_the_choice(self):
        memory = self.memory()
        memory.admit(handoff("lexp:histb", activity_ref="actv_b", revision=7,
                             settlement_refs=("settle:b-confirmed",),
                             artifact_refs=("artifact:artifact:b",)))
        inputs = memory.reader(activity_source=source("activity", self._activities(),
                                                      ACTIVITY_READS)).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_OK)
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_HISTORY)
        self.assertEqual(inputs.next_step["item_ref"], "actv_b")
        self.assertEqual(inputs.next_step["continue_from_ref"], "settle:b-confirmed")
        self.assertEqual(inputs.next_step["artifact_ref"], "artifact:artifact:b")
        self.assertIn("lexp:histb", inputs.next_step["evidence_refs"])
        item_b = next(i for i in inputs.unfinished if i["item_ref"] == "actv_b")
        self.assertEqual(item_b["continue_from_ref"], "settle:b-confirmed")
        self.assertEqual(item_b["continue_from_reason"], lci.NEXT_STEP_HISTORY)
        preferred = [c for c in inputs.choices if c["preferred"]]
        self.assertEqual([c["choice_id"] for c in preferred], ["cont:actv_b"])
        self.assertEqual(preferred[0]["reason_code"], "ADMITTED_CONSEQUENCE_FOR_THIS_ITEM")
        self.assertIn("lexp:histb", preferred[0]["evidence_refs"])

    def test_an_explicit_version_ref_is_preferred_over_the_result_ref(self):
        class StubPast:
            subject = MEMORY_OWNER

            def recent(self, *, limit=5):
                return [{"experience_id": "ev-v", "source_ref": "lexp:v",
                         "occurred_at": NOW, "subject": MEMORY_OWNER, "text": "改了一版",
                         "activity_ref": "actv_a", "artifact_ref": "artifact:artifact:a",
                         "version_ref": "artifact-version:v9",
                         "owner_result": "settle:a-confirmed",
                         "outcome_kind": "SIGNIFICANT_PROGRESS"}]

        inputs = memory_reader_with(StubPast(), activity_source=source(
            "activity", self._activities(), ACTIVITY_READS)).read()
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_HISTORY)
        self.assertEqual(inputs.next_step["object_ref"], "artifact-version:v9")
        self.assertEqual(inputs.next_step["continue_from_ref"], "artifact-version:v9")

    # -- weak-cue negative controls ----------------------------------------------------

    def test_a_consequence_for_an_activity_that_is_not_open_changes_nothing(self):
        memory = self.memory()
        memory.admit(handoff("lexp:elsewhere", activity_ref="actv_elsewhere",
                             settlement_refs=("settle:elsewhere",)))
        inputs = memory.reader(activity_source=source("activity", self._activities(),
                                                      ACTIVITY_READS)).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_OK)
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_CHECKPOINT)
        self.assertEqual(inputs.next_step["item_ref"], "actv_a")

    def test_a_real_admitted_consequence_with_no_confirmed_object_changes_nothing(self):
        # A CONFLICT handoff is genuinely admitted into M0 (it is a real event), but the owner
        # confirmed no object, so it is a weak cue and must not steer the next step.
        memory = self.memory()
        self.assertEqual(memory.admit(handoff("lexp:conflict", activity_ref="actv_b",
                                              kind="CONFLICT"))["disposition"], "ACCEPT")
        inputs = memory.reader(activity_source=source("activity", self._activities(),
                                                      ACTIVITY_READS)).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_OK)
        self.assertEqual(len(inputs.past), 1)
        self.assertIsNone(inputs.past[0]["owner_result"])
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_CHECKPOINT)
        self.assertEqual(inputs.next_step["item_ref"], "actv_a")
        self.assertEqual(inputs.next_step["continue_from_ref"], "chk:a:9")

    def test_free_text_alone_is_not_a_cue(self):
        class StubPast:
            subject = MEMORY_OWNER

            def recent(self, *, limit=5):
                return [{"experience_id": "ev-text", "source_ref": "lexp:text",
                         "occurred_at": NOW, "subject": MEMORY_OWNER,
                         "text": "好像还可以再写一点", "activity_ref": None,
                         "outcome_kind": "SIGNIFICANT_PROGRESS"}]

        inputs = memory_reader_with(StubPast(), activity_source=source(
            "activity", self._activities(), ACTIVITY_READS)).read()
        self.assertEqual(inputs.past_state, lci.PAST_STATE_OK)
        self.assertEqual(inputs.next_step["reason_code"], lci.NEXT_STEP_CHECKPOINT)
        self.assertEqual(inputs.next_step["item_ref"], "actv_a")


# ---------------------------------------------------------------------------------------
# 6. zero authority: recall signs nothing
# ---------------------------------------------------------------------------------------


class ZeroAuthority(ContinuityTestCase):
    def _read(self):
        memory = self.memory()
        memory.admit(handoff("lexp:auth", activity_ref="actv_a",
                             settlement_refs=("settle:owner-1",)))
        return memory.reader(activity_source=source(
            "activity", ActivityFixture([activity("actv_a")]), ACTIVITY_READS)).read()

    def test_the_payload_carries_no_authority_grant_or_candidate(self):
        payload = self._read().to_dict()
        self.assertIsNone(payload["authority"])
        self.assertIsNone(payload["location_authority"])
        self.assertEqual(payload["grants"], [])
        self.assertIsNone(payload["candidate"])
        self.assertIsNone(payload["decision"])
        self.assertIsNone(payload["adoption"])
        self.assertFalse(payload["may_start_activity"])
        self.assertFalse(payload["requires_model_call"])
        self.assertEqual(payload["llm_calls"], 0)
        for choice in payload["choices"]:
            self.assertIsNone(choice["authority"])
        self.assertIsNone(payload["next_step"]["authority"])

    def test_the_serialized_payload_has_no_truthy_permission_field(self):
        payload = self._read().to_dict()
        forbidden = ("authority", "grant", "capability", "permission", "credential",
                     "candidate", "location", "position")

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    here = f"{path}.{key}"
                    if any(word in key.lower() for word in forbidden):
                        self.assertIn(value, (None, [], False, {}, ""),
                                      f"{here} carries a signed value: {value!r}")
                    walk(value, here)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(payload)

    def test_recall_refuses_to_sign_a_candidate_a_position_or_a_grant(self):
        inputs = self._read()
        for call in (inputs.candidate_from_recall, inputs.location_authority_from_recall,
                     inputs.grant_from_recall):
            with self.assertRaises(lci.ContinuityRefused):
                call()
        reader = lci.LifeContinuityInputs(binding=binding())
        for call in (reader.candidate_from_recall, reader.location_authority_from_recall,
                     reader.grant_from_recall):
            with self.assertRaises(lci.ContinuityRefused):
                call()

    def test_the_seam_calls_only_reads_on_its_sources(self):
        memory = self.memory()
        memory.admit(handoff("lexp:onlyreads", activity_ref="actv_a",
                             settlement_refs=("settle:owner-1",)))
        # Every source is wrapped: any method outside the read allow-list raises.
        reader = lci.LifeContinuityInputs(
            binding=binding(),
            past_source=source("memory", memory.port, PAST_READS),
            activity_source=source("activity", ActivityFixture([activity("actv_a")]),
                                   ACTIVITY_READS),
            agenda_source=source("agenda", AgendaFixture(), AGENDA_READS))
        payload = reader.read().to_dict()
        self.assertEqual(payload["past_state"], lci.PAST_STATE_OK)
        with self.assertRaises(AssertionError):
            reader.past_source.commit_version()
        with self.assertRaises(AssertionError):
            reader.activity_source.start_activity()

    def test_a_binding_must_name_its_subject_home_time_and_owners(self):
        with self.assertRaises(lci.ContinuityInputsError):
            lci.ContinuityBinding(subject="", home=HOME, now=NOW)
        with self.assertRaises(lci.ContinuityInputsError):
            lci.ContinuityBinding(subject=MEMORY_OWNER, home="", now=NOW)
        with self.assertRaises(lci.ContinuityInputsError):
            lci.ContinuityBinding(subject=MEMORY_OWNER, home=HOME, now="not-a-time")
        with self.assertRaises(lci.ContinuityInputsError):
            lci.ContinuityBinding(subject=MEMORY_OWNER, home=HOME, now=NOW,
                                  owner_refs={"activity": ""})
        bound = binding().to_dict()
        self.assertEqual(bound["subject"], MEMORY_OWNER)
        self.assertEqual(bound["owner_refs"]["agenda"], "life:agenda")


# ---------------------------------------------------------------------------------------
# 7. wiring / shape drift guards (the fixture cannot silently rot)
# ---------------------------------------------------------------------------------------


class OwnerShapeDriftGuards(unittest.TestCase):
    def test_the_real_owners_still_expose_the_reads_this_seam_calls(self):
        self.assertTrue(callable(getattr(awa.WaitingCoordinator, "list_agenda_items", None)))
        self.assertTrue(callable(getattr(awa.WaitingCoordinator, "list_eligibilities", None)))
        self.assertTrue(callable(getattr(awa.WaitingCoordinator, "get_eligibility", None)))
        self.assertTrue(callable(getattr(ac.ActivityReadService, "list_open_activities", None)))
        self.assertTrue(callable(getattr(LifeExperiencePort, "recent", None)))

    def test_the_activity_fixture_uses_only_keys_the_owner_writes(self):
        import inspect

        source_text = inspect.getsource(ac.ActivityCommandService.start_activity)
        for key in ("activity_id", "status", "subject_id", "revision", "updated_at",
                    "checkpoint_ref", "waiting_on_ref", "resume_condition_ref",
                    "waiting_since", "title", "activity_kind"):
            self.assertIn(f'"{key}"', source_text,
                          f"the owner no longer writes {key!r}: the fixture has drifted")

    def test_the_fixture_waiting_refs_are_legal_in_both_vocabularies(self):
        # The two allow-sets were once 22-vs-13 with only 6 shared entries.  Tests must use
        # the legal intersection; the whitelist is never widened to suit them.
        intersection = set(wrv.VALID_WAITING_REF_PREFIXES) & set(adm.ALLOWED_WAITING_REF_PREFIXES)
        self.assertTrue(intersection, "the two waiting vocabularies share nothing")
        self.assertIn("event", intersection)
        self.assertIn("rcond", intersection)
        waiting = activity("actv_w", status="WAITING", waiting_on_ref="event:reply")
        self.assertEqual(wrv.waiting_ref_kind(waiting["waiting_on_ref"]),
                         wrv.WaitingRefKind.EVENT)
        round_tripped = awa.ResumeEligibility.from_dict(
            eligibility("elig:drift", activity_ref="actv_w", eligible_at=NOW))
        self.assertEqual(round_tripped.status, awa.ELIG_STATUS_OPEN)
        for ref in (waiting["waiting_on_ref"], "rcond:fixture"):
            parsed = wrv.parse_waiting_ref(ref)
            self.assertIsNotNone(parsed, ref)
            self.assertIn(parsed[0].value, intersection, ref)

    def test_the_schema_version_is_declared(self):
        inputs = lci.LifeContinuityInputs(binding=binding()).read()
        self.assertEqual(inputs.to_dict()["schema_version"], lci.CONTINUITY_INPUTS_SCHEMA)
        self.assertEqual(lci.CONTINUITY_INPUTS_SCHEMA, "chiyo.life.continuity_inputs.v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
