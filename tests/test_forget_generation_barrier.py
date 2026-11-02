#!/usr/bin/env python3
"""F-153/F-154: explicit forget requests, the retained barrier, and owner acknowledgments.

The owner is real for every assertion: a real M0 ``EvidenceStore``, the real
``MemoryControlLedger``, the real retained ``MemoryDeletionAuthority`` and the real
``LifeMemoryService`` run on one isolated fixture root.  No model, no network, no live instance.

The fixture root lives under ``$NYAIRO_FORGET_FIXTURE_ROOT`` (default
``.agents/worker-runs/forget-tests/<uuid>``) and is **kept**: nothing is deleted or unlinked.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "components" / "life" / "scripts",
              ROOT / "components" / "native",
              ROOT / "components" / "native" / "src",
              ROOT / "components" / "native" / "memory_runtime_v1",
              ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.evidence import EvidenceStore  # noqa: E402
from forget_request import (  # noqa: E402
    BLOCKED,
    COMPLETE,
    OWNER_PENDING,
    ForgetCaseStore,
    ForgetGate,
    ForgetRefused,
)
from forget_request_cli import BindingError, bind_store, validate_instance_root  # noqa: E402
from life_experience_port import (  # noqa: E402
    LifeExperiencePort,
    MemoryReadUnavailable,
    build_outcome_event,
    life_conversation_id,
)
from life_memory_service import LifeMemoryService, memory_service_from_env  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

OWNER = "chiyo"
CONVERSATION = life_conversation_id(OWNER)
POSIX_ONLY = unittest.skipIf(os.name == "nt", "POSIX fixture paths only")


def _fixture_root() -> Path:
    configured = os.environ.get("NYAIRO_FORGET_FIXTURE_ROOT")
    if configured:
        base = Path(configured) / str(uuid.uuid4())
    else:
        base = ROOT / ".agents" / "worker-runs" / "forget-tests" / str(uuid.uuid4())
    (base / "memory").mkdir(parents=True, exist_ok=True)
    (base / "retained").mkdir(parents=True, exist_ok=True)
    (base / "forget").mkdir(parents=True, exist_ok=True)
    return base


def _append(path: Path, text: str, source: str,
            extra_refs: tuple[tuple[str, str], ...] = ()) -> str:
    event = build_outcome_event(subject=OWNER, source_ref=source, text=text,
                                occurred_at=datetime.now(timezone.utc).isoformat(),
                                extra_refs=extra_refs)
    EvidenceStore(path).append(event)
    return event.event_id


class ForgetBarrierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _fixture_root()
        self.evidence = self.base / "memory" / "evidence.db"
        self.controls = self.base / "memory" / "controls.db"
        self.authority = self.base / "retained" / "authority.db"
        self.cases = self.base / "forget" / "cases.db"
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=CONVERSATION).initialize()
        MemoryDeletionAuthority(self.authority, owner=OWNER,
                                conversation=CONVERSATION).initialize()
        self.store = ForgetCaseStore(self.cases, evidence_db=self.evidence,
                                     controls_db=self.controls,
                                     authority_db=self.authority, owner=OWNER,
                                     conversation=CONVERSATION)
        self.store.initialize()
        self.gate = ForgetGate(self.store)

    def _port(self, gate=None) -> LifeExperiencePort:
        return LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                                  authority_db=self.authority,
                                  forget_gate=gate if gate is not None else self.gate)

    def _service(self) -> LifeMemoryService:
        return LifeMemoryService(self._port(), owner=OWNER, database=str(self.evidence),
                                 controls_database=str(self.controls),
                                 authority_database=str(self.authority), source="test",
                                 forget_gate=self.gate)

    def _seq(self) -> int:
        return MemoryDeletionAuthority(self.authority, owner=OWNER,
                                       conversation=CONVERSATION).projection().seq

    def test_request_is_idempotent_and_scope_bound(self) -> None:
        event_id = _append(self.evidence, "synthetic-a", "reading:demo:1")
        first = self.store.request(operation_key="op-1", event_ids=[event_id])
        again = self.store.request(operation_key="op-1", event_ids=[event_id])
        # The governed erasure clears the body, so the case reaches COMPLETE; identity remains.
        self.assertEqual(first["status"], COMPLETE)
        connection = sqlite3.connect(self.evidence)
        try:
            row = connection.execute(
                "SELECT payload_erased,length(content) FROM evidence_events WHERE event_id=?",
                (event_id,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(int(row[0]), 1)
        self.assertEqual(int(row[1]), 0)
        self.assertEqual(again["status"], "DUPLICATE")
        self.assertEqual(self._seq(), 1)
        with self.assertRaises(ForgetRefused) as caught:
            self.store.request(operation_key="op-1", event_ids=[event_id],
                               source_refs=["reading:other"])
        self.assertEqual(caught.exception.code, "FORGET_OPERATION_IDENTITY_COLLISION")

    def test_unclear_scope_is_held_without_withholding(self) -> None:
        _append(self.evidence, "synthetic-a", "reading:demo:1")
        held = self.store.request(operation_key="op-hold", hold_only=True)
        self.assertEqual(held["status"], "HELD")
        self.assertEqual(self._service().list_experiences()["data"]["count"], 1)
        self.assertEqual(self._seq(), 0)

    def test_barrier_precedes_owner_and_survives_restore(self) -> None:
        event_id = _append(self.evidence, "synthetic-a", "reading:demo:1")
        before = self.base / "memory" / "evidence.before.db"
        controls_before = self.base / "memory" / "controls.before.db"
        shutil.copy2(self.evidence, before)
        shutil.copy2(self.controls, controls_before)
        broken = ForgetCaseStore(self.cases, evidence_db=self.evidence,
                                 controls_db=self.base / "missing" / "controls.db",
                                 authority_db=self.authority, owner=OWNER,
                                 conversation=CONVERSATION)
        pending = broken.request(operation_key="op-broken", event_ids=[event_id])
        self.assertEqual(pending["status"], OWNER_PENDING)
        self.assertEqual(self._seq(), 1)
        self.assertIsNone(self._port().read(event_id))
        shutil.copy2(before, self.evidence)
        shutil.copy2(controls_before, self.controls)
        self.assertIsNone(self._port().read(event_id))
        retried = self.store.retry(pending["case_id"])
        # Retry runs the governed erasure; with the body cleared the case can complete.
        self.assertEqual(retried["status"], COMPLETE)
        self.assertEqual(self._seq(), 1)
        self.assertIsNone(self._port().read(event_id))

    def test_untouched_experience_stays_readable_after_restore(self) -> None:
        forgotten = _append(self.evidence, "synthetic-forgotten", "reading:demo:1")
        before = self.base / "memory" / "evidence.before.db"
        controls_before = self.base / "memory" / "controls.before.db"
        shutil.copy2(self.evidence, before)
        shutil.copy2(self.controls, controls_before)
        self.store.request(operation_key="op-a", source_refs=["reading:demo:1"],
                           retention_pending=True)
        kept = _append(self.evidence, "synthetic-kept", "practice:demo:keep")
        after = self.base / "memory" / "evidence.after.db"
        controls_after = self.base / "memory" / "controls.after.db"
        shutil.copy2(self.evidence, after)
        shutil.copy2(self.controls, controls_after)
        shutil.copy2(before, self.evidence)
        shutil.copy2(controls_before, self.controls)
        port = self._port()
        self.assertIsNone(port.read(forgotten))
        self.assertEqual(self._service().list_experiences()["data"]["count"], 0)
        shutil.copy2(after, self.evidence)
        shutil.copy2(controls_after, self.controls)
        self.assertIsNone(port.read(forgotten))
        self.assertIsNotNone(port.read(kept))

    def test_hold_with_scope_withholds_without_deleting_and_refuses_reingest(self) -> None:
        event_id = _append(self.evidence, "synthetic-a", "reading:demo:1")
        held = self.store.request(operation_key="op-hold", source_refs=["reading:demo:1"],
                                  hold_only=True)
        self.assertEqual(held["status"], "HELD")
        self.assertIsNone(self._port().read(event_id))       # withheld immediately
        self.assertEqual(self._seq(), 0)                     # no DELETE committed
        # A reopen (fresh store + gate over the same durable case record) still withholds.
        reopened = ForgetCaseStore(self.cases, evidence_db=self.evidence,
                                   controls_db=self.controls, authority_db=self.authority,
                                   owner=OWNER, conversation=CONVERSATION)
        self.assertIsNone(LifeExperiencePort(
            self.evidence, controls_db=self.controls, subject=OWNER,
            authority_db=self.authority, forget_gate=ForgetGate(reopened)).read(event_id))
        connection = sqlite3.connect(self.evidence)
        try:
            before = connection.execute("SELECT count(*) FROM evidence_events").fetchone()[0]
        finally:
            connection.close()
        reingested = build_outcome_event(
            subject=OWNER, source_ref="reading:demo:1:new", text="synthetic-reingest",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            extra_refs=[("forgotten_source", "reading:demo:1")])
        answer = self._port().append_outcome(reingested)
        connection = sqlite3.connect(self.evidence)
        try:
            after = connection.execute("SELECT count(*) FROM evidence_events").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(answer["status"], "refused")
        self.assertEqual(before, after)
        # The explicit case-contract confirmation is the only way from a hold to a deletion.
        confirmed = self.store.confirm(held["case_id"])
        self.assertEqual(confirmed["status"], COMPLETE)
        self.assertEqual(self._seq(), 1)
        self.assertIsNone(self._port().read(event_id))

    def test_missing_target_is_held_not_complete(self) -> None:
        _append(self.evidence, "synthetic-other", "practice:other")
        missing = self.store.request(operation_key="op-missing", event_ids=["evt:absent"])
        self.assertEqual(missing["status"], "HELD")
        self.assertEqual(missing["reason"], "FORGET_TARGET_NOT_FOUND")
        self.assertEqual(self._seq(), 0)

    def test_erasure_clears_body_and_keeps_identity(self) -> None:
        event_id = _append(self.evidence, "synthetic-a", "reading:demo:1")
        answer = self.store.request(operation_key="op-a", event_ids=[event_id])
        connection = sqlite3.connect(self.evidence)
        try:
            row = connection.execute(
                "SELECT payload_erased,length(content) FROM evidence_events WHERE event_id=?",
                (event_id,)).fetchone()
        finally:
            connection.close()
        # The identity row remains (audit/turn), but no payable body survives -- which is exactly
        # why the boolean physical read may now answer COMPLETE.
        self.assertIsNotNone(row)
        self.assertEqual(int(row[0]), 1)
        self.assertEqual(int(row[1]), 0)
        self.assertEqual(answer["status"], COMPLETE)

    def test_missing_authority_fails_closed(self) -> None:
        _append(self.evidence, "synthetic-a", "reading:demo:1")
        hidden = self.base / "retained" / "authority.hidden.db"
        shutil.move(str(self.authority), str(hidden))
        try:
            self.assertFalse(self.gate.state()["healthy"])
            self.assertEqual(self._service().status()["status"], BLOCKED)
            with self.assertRaises(MemoryReadUnavailable):
                self._port().search(query="")
            blocked = self.store.request(operation_key="op-x", source_refs=["reading:demo:1"])
            self.assertEqual(blocked["status"], BLOCKED)
        finally:
            shutil.move(str(hidden), str(self.authority))

    def test_env_factory_shares_one_gate_between_service_and_port(self) -> None:
        event_id = _append(self.evidence, "synthetic-a", "reading:demo:1")
        environment = {
            "NYA02_MEMORY_DB": str(self.evidence),
            "NYA02_MEMORY_CONTROLS_DB": str(self.controls),
            "NYA02_MEMORY_AUTHORITY_DB": str(self.authority),
            "NYA02_MEMORY_SUBJECT": OWNER,
            "NYA02_FORGET_CASE_DB": str(self.cases),
        }
        service = memory_service_from_env(environment)
        self.assertIsNotNone(service.forget_gate)
        self.assertIs(service.port.forget_gate, service.forget_gate)
        self.store.request(operation_key="op-hold", source_refs=["reading:demo:1"],
                           hold_only=True)
        self.assertIsNone(service.port.read(event_id))
        self.assertEqual(service.list_experiences()["data"]["count"], 0)

    def test_owner_and_uid_mismatch_are_refused(self) -> None:
        _append(self.evidence, "synthetic-a", "reading:demo:1")
        with self.assertRaises(ForgetRefused) as wrong_owner:
            self.store.request(operation_key="op-owner", source_refs=["reading:demo:1"],
                               requesting_owner="mallory")
        self.assertEqual(wrong_owner.exception.code, "FORGET_OWNER_MISMATCH")
        getuid = getattr(os, "getuid", None)
        if getuid is not None:
            with self.assertRaises(ForgetRefused) as wrong_uid:
                self.store.request(operation_key="op-uid", source_refs=["reading:demo:1"],
                                   requesting_uid=int(getuid()) + 1)
            self.assertEqual(wrong_uid.exception.code, "FORGET_OWNER_MISMATCH")


@POSIX_ONLY
class OperatorEntryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _fixture_root()
        self.instance = self.base / "instance"
        self.instance.mkdir()
        self.evidence = self.base / "memory" / "evidence.db"
        self.controls = self.base / "memory" / "controls.db"
        self.authority = self.base / "retained" / "authority.db"
        self.factory = EvidenceStore(self.evidence)
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=CONVERSATION).initialize()
        MemoryDeletionAuthority(self.authority, owner=OWNER,
                                conversation=CONVERSATION).initialize()
        self.cases = self.base / "forget" / "cases.db"
        self.environment = {
            "NYA02_MEMORY_DB": str(self.evidence),
            "NYA02_MEMORY_CONTROLS_DB": str(self.controls),
            "NYA02_MEMORY_AUTHORITY_DB": str(self.authority),
            "NYA02_MEMORY_SUBJECT": OWNER,
            "NYA02_FORGET_CASE_DB": str(self.cases),
        }

    def test_symlinked_instance_is_refused(self) -> None:
        link = self.base / "instance-link"
        link.symlink_to(self.instance, target_is_directory=True)
        with self.assertRaises(BindingError) as caught:
            validate_instance_root(link)
        self.assertEqual(caught.exception.code, "FORGET_INSTANCE_SYMLINK")

    def test_retained_files_inside_the_instance_are_refused(self) -> None:
        environment = dict(self.environment)
        environment["NYA02_FORGET_CASE_DB"] = str(self.instance / "cases.db")
        with self.assertRaises(BindingError) as caught:
            bind_store(self.instance, environment=environment)
        self.assertEqual(caught.exception.code, "FORGET_RETAINED_PATH_INSIDE_INSTANCE")

    def test_missing_authority_binding_is_refused(self) -> None:
        environment = dict(self.environment)
        environment.pop("NYA02_MEMORY_AUTHORITY_DB")
        with self.assertRaises(BindingError) as caught:
            bind_store(self.instance, environment=environment)
        self.assertEqual(caught.exception.code, "FORGET_AUTHORITY_REQUIRED")

    def test_case_db_must_match_the_instance_configuration(self) -> None:
        with self.assertRaises(BindingError) as caught:
            bind_store(self.instance, case_db=str(self.base / "forget" / "other.db"),
                       environment=self.environment)
        self.assertEqual(caught.exception.code, "FORGET_CASE_DB_MISMATCH")

    def test_operator_binding_creates_the_coordination_record(self) -> None:
        store = bind_store(self.instance, environment=self.environment)
        answer = store.request(operation_key="op-cli", hold_only=True)
        self.assertEqual(answer["status"], "HELD")
        self.assertEqual(len(store.list_cases()), 1)


if __name__ == "__main__":
    unittest.main()
