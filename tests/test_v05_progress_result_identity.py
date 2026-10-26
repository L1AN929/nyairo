#!/usr/bin/env python3
"""v0.5: an owner-confirmed progress result gets its own handoff identity.

The legacy AG-2 identity is ``activity_id + milestone_kind + Activity.revision``.  A CONTINUE
does not advance the Activity revision, so two *different* owner versions written inside one
Activity collapsed onto one handoff: the second round replayed the first round's event and the
consumer saw the same ``source_ref`` again.  This module pins the new contract:

* only a ``SIGNIFICANT_PROGRESS`` handoff that carries a real, owner-read-back
  ``owner_confirmed_context`` may pass ``progress_result_ref``;
* the ref is exactly ``artifact:<id>@artifactversion:<version>`` from that context and both
  halves must be carried by the same call;
* the new ``lexp:result:`` namespace keys on that ref, not on the Activity revision, so the same
  result replays after a revision change while two confirmed versions stay two handoffs;
* callers that omit the parameter get the legacy ``lexp:`` formula and payload unchanged;
* old/new juncture: a legacy event of the same Activity that already names the same result is
  reused (no second experience) -- including when a *legal* Activity transition has since moved
  the revision past it; a legacy event with no comparable context is refused rather than
  guessed, because an incomplete lookup must not be read as "not found".

No product model, no network, no instance root.  The store here is a bounded in-memory commit
double; the real on-disk owner chain is exercised by ``scripts/v05_progress_result_identity_smoke.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402

ISO = "2026-10-25T09:00:00+00:00"
INTEGRATION_ID = "lint:test-progress-result"
ACTIVITY_ID = "actv:test-progress-result"
#: A second Activity id whose revision moves through *real* LR-2 transitions (see
#: ``_RealActivityHarness``); the in-memory ``_ActivityRead`` above never fakes a revision move.
REAL_ACTIVITY_ID = "actv:test-progress-result-real"


def _confirmed(version: str = "v1", artifact: str = "artifact:aa") -> dict:
    return {
        "artifact_id": artifact,
        "version_ref": "artifactversion:%s" % version,
        "title": "fixture",
        "result_description": "the tail the owner holds at %s" % version,
        "outcome_confirmed": True,
    }


def _refs(confirmed: dict) -> list:
    return [confirmed["artifact_id"], confirmed["version_ref"]]


def _result_ref(version: str = "v1", artifact: str = "artifact:aa") -> str:
    return ag2.canonical_owner_result_ref(_confirmed(version, artifact))


def _new_state(activity_id: str = ACTIVITY_ID) -> dict:
    record = ag2.LifeIntegrationRecord(
        integration_id=INTEGRATION_ID,
        correlation_id="corr:test-progress-result",
        opportunity_ref="opp:test",
        candidate_set_ref="candset:test",
        decision_ref="dec:1",
        adoption_ref="adp:1",
        activity_ref=activity_id,
        action_refs=["act:1"],
        progress_refs=["pevid:1"],
        created_at=ISO,
        updated_at=ISO,
    )
    return {
        "handoff_events_by_id": {},
        "handoff_deliveries_by_id": {},
        "pending_handoff_ids": [],
        "integrations_by_id": {INTEGRATION_ID: record.to_dict()},
    }


class _Commit:
    """Bounded stand-in for the typed IntegrationCommitPort (persists to one JSON file)."""

    def __init__(self, state: dict | None = None, store_path: Path | None = None):
        self.store_path = store_path
        if store_path is not None and store_path.is_file():
            self.state = json.loads(store_path.read_text(encoding="utf-8"))
        else:
            self.state = state if state is not None else _new_state()
            self._persist()

    def _persist(self) -> None:
        if self.store_path is not None:
            self.store_path.write_text(json.dumps(self.state, ensure_ascii=False),
                                       encoding="utf-8")

    def read_state(self, *, mutable: bool = False) -> dict:
        return self.state

    def experience_handoff_enqueued(self, *, map_updates, list_replacements, journal_payload):
        for name, updates in map_updates.items():
            self.state.setdefault(name, {}).update(updates)
        for name, value in list_replacements.items():
            self.state[name] = value
        self._persist()

    def experience_handoff_dispatched(self, *, map_updates, list_replacements, journal_payload):
        self.experience_handoff_enqueued(
            map_updates=map_updates, list_replacements=list_replacements,
            journal_payload=journal_payload)


class _ActivityRead:
    def __init__(self, revision: int = 3):
        self.revision = revision

    def get_activity(self, activity_id):
        if activity_id != ACTIVITY_ID:
            return None
        return {"activity_id": ACTIVITY_ID, "revision": self.revision,
                "activity_kind": "READING", "completion_evidence_ref": None}


class _SettlementRead:
    def get_settlement_by_id(self, _ref):
        return None


class _Consumer:
    def __init__(self, disposition: str = "ACCEPT"):
        self.disposition = disposition
        self.seen: list = []

    def receive_experience_handoff(self, event: dict) -> dict:
        self.seen.append(event.get("handoff_id"))
        return {"disposition": self.disposition}


class _Harness:
    def __init__(self, *, store_path: Path | None = None, revision: int = 3,
                 disposition: str = "ACCEPT"):
        self.commit = _Commit(store_path=store_path)
        self.activity = _ActivityRead(revision)
        self.consumer = _Consumer(disposition)
        self.coordinator = object.__new__(ag2.LifeIntegrationCoordinator)
        self.coordinator.activity_read = self.activity
        self.coordinator.integration_commit = self.commit
        self.coordinator.settlement_read = _SettlementRead()
        self.coordinator.experience_consumer = self.consumer

    @property
    def events(self) -> dict:
        return self.commit.state["handoff_events_by_id"]

    def emit(self, *, kind: str = ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
             confirmed: dict | None = None, result_ref: str | None = None,
             artifact_refs: list | None = None):
        if artifact_refs is None and confirmed is not None:
            artifact_refs = _refs(confirmed)
        return self.coordinator.emit_experience_handoff(
            integration_id=INTEGRATION_ID,
            activity_id=ACTIVITY_ID,
            terminal_or_milestone_kind=kind,
            occurred_at=ISO,
            artifact_refs=artifact_refs,
            owner_confirmed_context=confirmed,
            progress_result_ref=result_ref,
        )


class LegacyIdentityTests(unittest.TestCase):
    """Callers that omit the new parameter keep the old identity and payload byte-for-byte."""

    def test_omitted_parameter_keeps_the_legacy_golden_formula(self):
        harness = _Harness(revision=7)
        out = harness.emit()
        expected = "lexp:%s" % ag2.sha256_hex(
            "%s:%s:v7" % (ACTIVITY_ID, ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS))[:16]
        self.assertEqual(out["handoff_event"].handoff_id, expected)
        self.assertFalse(out["idempotent_replay"])
        self.assertEqual(len(harness.events), 1)

    def test_legacy_event_has_no_new_field_and_replays_the_same_event(self):
        harness = _Harness(revision=7)
        first = harness.emit(confirmed=_confirmed("v1"))
        stored = harness.events[first["handoff_event"].handoff_id]
        self.assertNotIn("progress_result_ref", stored)
        self.assertEqual(stored["owner_confirmed_context"]["version_ref"], "artifactversion:v1")

        second = harness.emit(confirmed=_confirmed("v1"))
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["handoff_event"].handoff_id, first["handoff_event"].handoff_id)
        self.assertEqual(len(harness.events), 1)

    def test_terminal_identities_stay_unchanged_and_ignore_the_revision(self):
        for kind in (ag2.HANDOFF_KIND_COMPLETED, ag2.HANDOFF_KIND_ABANDONED):
            harness = _Harness(revision=2)
            expected = "lexp:%s" % ag2.sha256_hex("%s:%s" % (ACTIVITY_ID, kind))[:16]
            out = harness.emit(kind=kind)
            self.assertEqual(out["handoff_event"].handoff_id, expected)
            harness.activity.revision = 99
            replay = harness.emit(kind=kind)
            self.assertEqual(replay["handoff_event"].handoff_id, expected)
            self.assertTrue(replay["idempotent_replay"])

    def test_legacy_stored_event_reads_back_and_replays(self):
        harness = _Harness(revision=4)
        first = harness.emit(confirmed=_confirmed("v1"))
        raw = harness.events[first["handoff_event"].handoff_id]
        loaded = ag2.LifeExperienceHandoffEvent.from_dict(raw)
        self.assertEqual(loaded.handoff_id, first["handoff_event"].handoff_id)
        again = harness.emit(confirmed=_confirmed("v1"))
        self.assertEqual(again["handoff_event"].handoff_id, loaded.handoff_id)


class NewIdentityTests(unittest.TestCase):

    def test_two_confirmed_versions_in_one_revision_are_two_handoffs(self):
        harness = _Harness(revision=5)
        one = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        two = harness.emit(confirmed=_confirmed("v2"), result_ref=_result_ref("v2"))
        self.assertTrue(one["handoff_event"].handoff_id.startswith("lexp:result:"))
        self.assertTrue(two["handoff_event"].handoff_id.startswith("lexp:result:"))
        self.assertNotEqual(one["handoff_event"].handoff_id, two["handoff_event"].handoff_id)
        self.assertEqual(len(harness.events), 2)
        self.assertEqual(
            harness.events[two["handoff_event"].handoff_id]["owner_confirmed_context"]["version_ref"],
            "artifactversion:v2")

    def test_same_result_replays_even_after_the_activity_revision_moves(self):
        harness = _Harness(revision=5)
        first = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        harness.activity.revision = 6
        again = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(again["handoff_event"].handoff_id, first["handoff_event"].handoff_id)
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(len(harness.events), 1)

    def test_different_results_in_the_same_revision_stay_distinct(self):
        harness = _Harness(revision=6)
        one = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        two = harness.emit(confirmed=_confirmed("v2"), result_ref=_result_ref("v2"),
                           artifact_refs=_refs(_confirmed("v2")))
        self.assertNotEqual(one["handoff_event"].handoff_id, two["handoff_event"].handoff_id)
        self.assertEqual(len(harness.events), 2)

    def test_reassembly_reuses_the_same_result_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "integration.json"
            first = _Harness(store_path=path, revision=3).emit(
                confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
            second = _Harness(store_path=path, revision=3).emit(
                confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
            self.assertEqual(first["handoff_event"].handoff_id,
                             second["handoff_event"].handoff_id)
            self.assertTrue(second["idempotent_replay"])
            self.assertEqual(len(second["dispatch_result"]["deliveries"]), 1)

    def test_two_python_processes_on_one_store_do_not_add_a_second_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "integration.json"
            outputs = []
            for _ in range(2):
                code = (
                    "import sys;"
                    "sys.path[:0]=[r'%s', r'%s'];"
                    "import test_v05_progress_result_identity as m;"
                    "m._child_emit(r'%s')" % (ROOT, ROOT / "tests", path)
                )
                completed = subprocess.run([sys.executable, "-B", "-c", code],
                                           capture_output=True, text=True, timeout=120)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                outputs.append(json.loads(completed.stdout.strip().splitlines()[-1]))
            self.assertEqual(outputs[0]["handoff_id"], outputs[1]["handoff_id"])
            self.assertFalse(outputs[0]["idempotent_replay"])
            self.assertTrue(outputs[1]["idempotent_replay"])


class RefusalTests(unittest.TestCase):
    """A malformed/forged/out-of-scope ref is refused before anything is written."""

    def _assert_refused(self, harness: _Harness, **kwargs) -> None:
        before = json.dumps(harness.commit.state, sort_keys=True)
        with self.assertRaises(ag2.LifeIntegrationError):
            harness.emit(**kwargs)
        self.assertEqual(json.dumps(harness.commit.state, sort_keys=True), before)
        self.assertEqual(harness.events, {})

    def test_bare_string_is_not_a_result(self):
        harness = _Harness()
        self._assert_refused(harness, confirmed=_confirmed("v1"),
                             result_ref="artifact:aa")

    def test_forged_ref_that_disagrees_with_the_confirmed_context_is_refused(self):
        harness = _Harness()
        self._assert_refused(harness, confirmed=_confirmed("v1"),
                             result_ref=_result_ref("v2"))

    def test_ref_whose_owner_refs_are_not_carried_is_refused(self):
        harness = _Harness()
        self._assert_refused(harness, confirmed=_confirmed("v1"),
                             result_ref=_result_ref("v1"), artifact_refs=[])

    def test_ref_without_a_confirmed_context_is_refused(self):
        harness = _Harness()
        self._assert_refused(harness, confirmed=None, result_ref=_result_ref("v1"))

    def test_ref_on_a_terminal_kind_is_refused(self):
        harness = _Harness()
        self._assert_refused(harness, kind=ag2.HANDOFF_KIND_COMPLETED,
                             confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))

    def test_same_id_with_a_different_result_is_refused_not_replayed(self):
        harness = _Harness(revision=3)
        first = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        hid = first["handoff_event"].handoff_id
        # Corrupt only the test double: the stored event now claims another version under the
        # id that the v1 ref derives.  A replay must not silently hand back the wrong result.
        harness.events[hid]["owner_confirmed_context"] = _confirmed("v9")
        before = json.dumps(harness.commit.state, sort_keys=True)
        with self.assertRaises(ag2.LifeIntegrationError):
            harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(json.dumps(harness.commit.state, sort_keys=True), before)


class LegacyBoundaryTests(unittest.TestCase):
    """One confirmed result must not grow a second experience at the old/new juncture."""

    def test_legacy_event_with_the_same_result_is_reused(self):
        harness = _Harness(revision=4)
        legacy = harness.emit(confirmed=_confirmed("v1"))
        self.assertTrue(legacy["handoff_event"].handoff_id.startswith("lexp:"))
        new = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(new["handoff_event"].handoff_id, legacy["handoff_event"].handoff_id)
        self.assertTrue(new.get("reused_legacy_handoff"))
        self.assertEqual(len(harness.events), 1)

    def test_legacy_event_with_a_different_result_allows_an_independent_handoff(self):
        harness = _Harness(revision=4)
        legacy = harness.emit(confirmed=_confirmed("v1"))
        new = harness.emit(confirmed=_confirmed("v2"), result_ref=_result_ref("v2"))
        self.assertNotEqual(new["handoff_event"].handoff_id, legacy["handoff_event"].handoff_id)
        self.assertEqual(len(harness.events), 2)
        self.assertEqual(
            harness.events[legacy["handoff_event"].handoff_id]["owner_confirmed_context"]
            ["version_ref"], "artifactversion:v1")

    def test_legacy_event_without_a_comparable_context_is_refused(self):
        harness = _Harness(revision=4)
        harness.emit()  # legacy event, no owner_confirmed_context
        before = json.dumps(harness.commit.state, sort_keys=True)
        with self.assertRaises(ag2.LifeIntegrationError):
            harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(json.dumps(harness.commit.state, sort_keys=True), before)
        self.assertEqual(len(harness.events), 1)


FIXTURE_DECISION_REF = "dec:v05-result-fixture"


class _DecisionReader:
    """AG-1 owner read-port stand-in: only the fields ``ProvenanceVerifier`` reads."""

    def get_decision(self, decision_id: str):
        if decision_id != FIXTURE_DECISION_REF:
            return None
        return {"decision_id": decision_id, "revision": 1,
                "snapshot_fingerprint": "fp:%s" % decision_id}


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
            "decision_ref": FIXTURE_DECISION_REF,
            "decision_revision": 1,
            "adoption_status": "APPLIED",
            "revision": 1,
            "authorized_mutation_kind": mutation_kind,
            "authorized_command_id": command_id,
            "created_activity_ref": activity_ref,
            "target_activity_ref": activity_ref,
            "authorized_activity_ref": activity_ref,
        }


class _RealActivityHarness:
    """Real LR-2 owner (lease, canonical store, command service) + the AG-2 handoff store.

    The only stand-ins are the Decision and Adoption read ports -- the same two ports the B1
    alignment test leaves as doubles -- so the issue, scope and fingerprint checks of the
    admission issuer all still run.  The revision is moved by ``update_checkpoint`` through
    LR-2's own command service, never by editing a stored revision.
    """

    def __init__(self, *, store_path: Path | None = None) -> None:
        self.activity_root = Path(tempfile.mkdtemp(prefix="v05-result-activity-"))
        self.adoptions = _AdoptionReader()
        issuer = adm.build_admission_issuer(
            _DecisionReader(), self.adoptions, runtime_epoch="v05-progress-result-test")
        self.lease = ac.ActivityAuthorityLease(self.activity_root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.activity_root, namespace=ac.NAMESPACE_ISOLATED_TEST,
            _admission_issuer=issuer)
        capability = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.commands = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=capability)
        self.read = ac.ActivityReadService(self.store)
        #: CHECKPOINT has no Decision/Adoption form in LR-2, so it goes through the same
        #: composition-root consequence capability the production runtime mints for LR-5.
        self.checkpoint_port = adm.ActivityConsequencePort(
            issuer.build_consequence_capability(
                allowed_kinds=(ac.TRANSITION_CHECKPOINT,),
                owner_label="v05_result_fixture_checkpoint",
                evidence_verifier=adm.build_activity_lineage_verifier(self.read)),
            self.commands)
        self.commit = _Commit(state=_new_state(REAL_ACTIVITY_ID), store_path=store_path)
        self.coordinator = object.__new__(ag2.LifeIntegrationCoordinator)
        self.coordinator.activity_read = self.read
        self.coordinator.integration_commit = self.commit
        self.coordinator.settlement_read = _SettlementRead()
        self.coordinator.experience_consumer = _Consumer()

    def close(self) -> None:
        self.lease.release()

    @property
    def events(self) -> dict:
        return self.commit.state["handoff_events_by_id"]

    def store_revision(self) -> int:
        state, _ = self.store.load_verified_state_and_journal()
        return int(state["revision"])

    def start(self, *, command_id: str = "v05-result-start") -> dict:
        self.adoptions.grant(command_id, ac.TRANSITION_START, REAL_ACTIVITY_ID)
        result = self.commands.start_activity(
            activity_kind="READING",
            title="v0.5 result identity fixture",
            origin_ref="world:v05resultfixture",
            source_refs=["world:v05resultfixture"],
            expected_revision=self.store_revision(),
            idempotency_key=command_id,
            decision_ref=FIXTURE_DECISION_REF,
            adoption_ref="adp:%s" % command_id,
            activity_id=REAL_ACTIVITY_ID,
        )
        return dict(result.activity)

    def checkpoint(self, *, command_id: str = "v05-result-checkpoint") -> dict:
        """One legal LR-2 transition that advances the Activity revision."""

        result = self.checkpoint_port.propose_checkpoint_or_progress(
            "CHECKPOINT",
            activity_id=REAL_ACTIVITY_ID,
            checkpoint_ref="checkpoint:%s" % command_id,
            expected_revision=self.store_revision(),
            idempotency_key=command_id,
            source_refs=["evidence:v05resultcheckpoint"],
        )
        return dict(result.activity)

    def activity(self) -> dict:
        return dict(self.read.get_activity(REAL_ACTIVITY_ID) or {})

    def emit(self, *, confirmed: dict | None, result_ref: str | None):
        return self.coordinator.emit_experience_handoff(
            integration_id=INTEGRATION_ID,
            activity_id=REAL_ACTIVITY_ID,
            terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
            occurred_at=ISO,
            artifact_refs=_refs(confirmed) if confirmed is not None else [],
            owner_confirmed_context=confirmed,
            progress_result_ref=result_ref,
        )

    def emit_legacy(self, *, confirmed: dict | None):
        """The old call: no ``progress_result_ref``, identity from Activity revision."""

        return self.emit(confirmed=confirmed, result_ref=None)


class LegacyTransitionBoundaryTests(unittest.TestCase):
    """A legal Activity transition must not turn one confirmed result into two handoffs.

    This is the counterexample the first delivery missed: legacy identity keys on
    ``Activity.revision``, a checkpoint/PAUSE/RESUME moves that revision, and a result-level
    replay afterwards used to mint a second event because only the *current* revision was
    inspected.
    """

    POSIX_ONLY = unittest.skipUnless(
        os.name == "posix", "LR-2 canonical store imports fcntl (POSIX only)")

    def setUp(self) -> None:
        if os.name != "posix":
            self.skipTest("LR-2 canonical store imports fcntl (POSIX only)")
        self.harness = _RealActivityHarness()
        self.harness.start()
        self.first_activity = self.harness.activity()

    def tearDown(self) -> None:
        self.harness.close()

    def test_legacy_result_is_reused_after_a_legal_revision_change(self):
        legacy = self.harness.emit_legacy(confirmed=_confirmed("v1"))
        legacy_id = legacy["handoff_event"].handoff_id
        self.assertFalse(legacy["handoff_event"].handoff_id.startswith("lexp:result:"))
        self.assertEqual(len(self.harness.events), 1)
        stored = json.dumps(self.harness.events[legacy_id], sort_keys=True)

        moved = self.harness.checkpoint()
        self.assertGreater(int(moved["revision"]), int(self.first_activity["revision"]))

        upgraded = self.harness.emit(confirmed=_confirmed("v1"),
                                     result_ref=_result_ref("v1"))
        self.assertEqual(upgraded["handoff_event"].handoff_id, legacy_id)
        self.assertTrue(upgraded.get("reused_legacy_handoff"))
        self.assertTrue(upgraded.get("idempotent_replay"))
        self.assertEqual(len(self.harness.events), 1)
        self.assertEqual(json.dumps(self.harness.events[legacy_id], sort_keys=True), stored)

    def test_uncomparable_legacy_event_at_an_older_revision_is_refused(self):
        self.harness.emit_legacy(confirmed=None)
        self.harness.checkpoint()
        before = json.dumps(self.harness.commit.state, sort_keys=True)
        with self.assertRaises(ag2.LifeIntegrationError):
            self.harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(json.dumps(self.harness.commit.state, sort_keys=True), before)
        self.assertEqual(len(self.harness.events), 1)

    def test_another_result_in_an_older_legacy_event_does_not_block_a_new_one(self):
        legacy = self.harness.emit_legacy(confirmed=_confirmed("v1"))
        self.harness.checkpoint()
        other = self.harness.emit(confirmed=_confirmed("v2"), result_ref=_result_ref("v2"))
        self.assertNotEqual(other["handoff_event"].handoff_id,
                            legacy["handoff_event"].handoff_id)
        self.assertEqual(len(self.harness.events), 2)
        again = self.harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(again["handoff_event"].handoff_id, legacy["handoff_event"].handoff_id)
        self.assertEqual(len(self.harness.events), 2)

    def test_earliest_legacy_event_is_reused_and_no_third_is_made(self):
        first = self.harness.emit_legacy(confirmed=_confirmed("v1"))
        self.harness.checkpoint()
        second = self.harness.emit_legacy(confirmed=_confirmed("v1"))
        self.assertNotEqual(first["handoff_event"].handoff_id,
                            second["handoff_event"].handoff_id)
        self.assertEqual(len(self.harness.events), 2)
        upgraded = self.harness.emit(confirmed=_confirmed("v1"),
                                     result_ref=_result_ref("v1"))
        self.assertEqual(upgraded["handoff_event"].handoff_id,
                         first["handoff_event"].handoff_id)
        self.assertEqual(len(self.harness.events), 2)

    def test_a_result_level_event_wins_when_both_namespaces_name_the_same_result(self):
        new = self.harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        new_id = new["handoff_event"].handoff_id
        self.assertTrue(new_id.startswith("lexp:result:"))
        self.harness.emit_legacy(confirmed=_confirmed("v1"))
        self.assertEqual(len(self.harness.events), 2)

        self.harness.checkpoint()
        again = self.harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(again["handoff_event"].handoff_id, new_id)
        self.harness.checkpoint()
        still = self.harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        self.assertEqual(still["handoff_event"].handoff_id, new_id)
        self.assertEqual(len(self.harness.events), 2)

    def test_a_fresh_process_reuses_the_older_legacy_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "integration.json"
            harness = _RealActivityHarness(store_path=path)
            try:
                harness.start()
                legacy = harness.emit_legacy(confirmed=_confirmed("v1"))
                legacy_id = legacy["handoff_event"].handoff_id
                moved = harness.checkpoint()
                self.assertGreater(int(moved["revision"]), 1)
                code = (
                    "import sys;"
                    "sys.path[:0]=[r'%s', r'%s'];"
                    "import test_v05_progress_result_identity as m;"
                    "m._child_upgrade_emit(r'%s', r'%s')"
                    % (ROOT, ROOT / "tests", path, harness.activity_root)
                )
                completed = subprocess.run([sys.executable, "-B", "-c", code],
                                           capture_output=True, text=True, timeout=120)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                out = json.loads(completed.stdout.strip().splitlines()[-1])
            finally:
                harness.close()
            self.assertEqual(out["handoff_id"], legacy_id)
            self.assertTrue(out["reused_legacy_handoff"])
            self.assertTrue(out["idempotent_replay"])
            self.assertEqual(out["event_count"], 1)
            self.assertEqual(out["event_activity_revision"], 1)
            self.assertEqual(out["current_activity_revision"], int(moved["revision"]))


class ConsumerBoundaryTests(unittest.TestCase):

    def test_consumer_rejection_keeps_its_disposition_and_pending_event(self):
        harness = _Harness(revision=3, disposition="DEFER")
        out = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
        delivery = out["dispatch_result"]["deliveries"][0]
        self.assertEqual(delivery["consumer_disposition"], "DEFER")
        self.assertEqual(delivery["status"], "DEFERRED")
        pending = out["dispatch_result"]["pending_handoff_ids"]
        self.assertIn(out["handoff_event"].handoff_id, pending)

    def test_legacy_duplicate_disposition_is_still_a_delivered_replay(self):
        harness = _Harness(revision=3, disposition="DUPLICATE")
        out = harness.emit(confirmed=_confirmed("v1"))
        delivery = out["dispatch_result"]["deliveries"][0]
        self.assertEqual(delivery["consumer_disposition"], "DUPLICATE")
        self.assertEqual(delivery["status"], "DELIVERED")
        self.assertNotIn(out["handoff_event"].handoff_id,
                         out["dispatch_result"]["pending_handoff_ids"])

    def test_unknown_and_no_new_version_never_emit_a_progress_result(self):
        # The cycle calls ``_emit_progress_experience`` only on the SUCCEEDED-with-a-new-version
        # branch.  Both the UNKNOWN outcome and an owner "SUCCEEDED but no new version" answer
        # leave it at zero calls -- so neither can invent a result identity.
        def _cycle(action_read):
            cycle = object.__new__(alc.AutonomousLifeCycle)
            cycle.runtime = SimpleNamespace(action_read=action_read)
            cycle._now_iso = lambda: ISO
            cycle._journal = lambda *_a, **_k: None
            cycle._record_artifact_progress = lambda *_a, **_k: None
            cycle._emit_progress_experience = lambda *_a, **kwargs: calls.append(kwargs)
            cycle._finish = lambda base, outcome, **_k: dict(base, outcome=outcome)
            cycle.last_receipt = None
            return cycle

        calls: list = []
        base = {"artifact_id": "artifact:aa", "observed_at": ISO,
                "activity_ref": ACTIVITY_ID, "correlation_id": "corr:1",
                "decision_id": "dec:1"}
        unknown = _cycle(SimpleNamespace(list_receipts=lambda _a: []))
        unknown._classify_ar0_step({"step_execution_result": {"submit_result": {"action": {
            "status": "UNKNOWN", "action_id": "act:1"}}}}, base, ISO)
        self.assertEqual(calls, [])

        no_change = _cycle(SimpleNamespace(list_receipts=lambda _a: [{
            "details": {"version_created": False, "owner_status": "NO_CHANGE",
                        "version_id": "artifact-version:v1"}}]))
        no_change._classify_ar0_step({"step_execution_result": {"submit_result": {"action": {
            "status": "SUCCEEDED", "action_id": "act:2"}}}}, dict(base), ISO)
        self.assertEqual(calls, [])


class LoadedModuleTests(unittest.TestCase):

    def test_the_running_module_is_the_life_scripts_tree(self):
        self.assertEqual(Path(ag2.__file__).resolve(),
                         (ROOT / "components" / "life" / "scripts"
                          / "life_integration_ag2.py").resolve())

    def test_the_vendored_mirror_is_not_the_module_this_chain_loads(self):
        # The vendored flat tree is a frozen release snapshot for the chiyo CLI; it predates
        # ``owner_confirmed_context`` and is not what the Life runtime/residents import.  This
        # is recorded as load evidence, not as a silent "already synced" claim.
        vendored = (ROOT / "components" / "alpha" / "chiyo" / "life_runtime"
                    / "life_integration_ag2.py").read_text(encoding="utf-8")
        self.assertNotIn("owner_confirmed_context", vendored)
        self.assertNotIn("progress_result_ref", vendored)


def _child_upgrade_emit(store_path: str, activity_root: str) -> None:
    """Entry point for the cross-process upgrade test; prints one JSON line.

    The child opens the *real* canonical Activity store at ``activity_root`` (no synthetic
    revision) and the same AG-2 handoff file, then replays the confirmed result in the new
    result-level mode.  It must reuse the older legacy event -- not mint a second one because
    the current revision differs from the one that old event was written under.
    """

    store = ac.CanonicalActivityStore(Path(activity_root), namespace=ac.NAMESPACE_ISOLATED_TEST)
    coordinator = object.__new__(ag2.LifeIntegrationCoordinator)
    coordinator.activity_read = ac.ActivityReadService(store)
    coordinator.integration_commit = _Commit(store_path=Path(store_path))
    coordinator.settlement_read = _SettlementRead()
    coordinator.experience_consumer = _Consumer()
    confirmed = _confirmed("v1")
    out = coordinator.emit_experience_handoff(
        integration_id=INTEGRATION_ID,
        activity_id=REAL_ACTIVITY_ID,
        terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
        occurred_at=ISO,
        artifact_refs=_refs(confirmed),
        owner_confirmed_context=confirmed,
        progress_result_ref=_result_ref("v1"),
    )
    state = coordinator.integration_commit.read_state()
    current = coordinator.activity_read.get_activity(REAL_ACTIVITY_ID) or {}
    print(json.dumps({
        "handoff_id": out["handoff_event"].handoff_id,
        "reused_legacy_handoff": bool(out.get("reused_legacy_handoff")),
        "idempotent_replay": bool(out.get("idempotent_replay")),
        "event_count": len(state["handoff_events_by_id"]),
        # The reused event keeps the revision it was written under; the Activity itself has
        # moved on.  Both are reported so the test cannot mistake one for the other.
        "event_activity_revision": out["handoff_event"].activity_revision,
        "current_activity_revision": int(current.get("revision") or 0),
    }, ensure_ascii=False))


def _child_emit(store_path: str) -> None:
    """Entry point for the two-process test; prints one JSON line."""

    harness = _Harness(store_path=Path(store_path), revision=3)
    out = harness.emit(confirmed=_confirmed("v1"), result_ref=_result_ref("v1"))
    print(json.dumps({
        "handoff_id": out["handoff_event"].handoff_id,
        "idempotent_replay": bool(out["idempotent_replay"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
