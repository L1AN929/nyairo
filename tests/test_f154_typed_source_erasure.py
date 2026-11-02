#!/usr/bin/env python3
"""F-154: typed source authority, governed M0 erasure and derived owner cleanup.

Every assertion runs against the real owners on an isolated fixture root: a real ``EvidenceStore``,
the real ``MemoryDeletionAuthority``/``MemoryControlLedger`` and a real ``ManagedArtifactStore``.
No model, no network, no live instance.  The fixture root is kept (nothing deleted or unlinked).
"""
from __future__ import annotations

import os
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
              ROOT / "components" / "supply",
              ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.evidence import EvidenceStore  # noqa: E402
from forget_request import ForgetGate, ForgetCaseStore  # noqa: E402
from life_experience_port import (  # noqa: E402
    LifeExperiencePort,
    build_outcome_event,
    life_conversation_id,
)
from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

OWNER = "chiyo"
CONVERSATION = life_conversation_id(OWNER)
SENTINEL = "F154-SYNTHETIC-ERASE-SENTINEL"


def _fixture_root() -> Path:
    configured = os.environ.get("NYAIRO_FORGET_FIXTURE_ROOT")
    base = (Path(configured) / str(uuid.uuid4()) if configured
            else ROOT / ".agents" / "worker-runs" / "f154-tests" / str(uuid.uuid4()))
    for name in ("memory", "retained", "forget", "supply", "projections"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


class _Workspace:
    def get_workspace_by_id(self, workspace_id: str) -> dict:
        return {"subject_id": OWNER, "lifecycle": "ACTIVE"}


class TypedSourceErasureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _fixture_root()
        self.evidence = self.base / "memory" / "evidence.db"
        self.controls = self.base / "memory" / "controls.db"
        self.authority = self.base / "retained" / "authority.db"
        self.cases = self.base / "forget" / "cases.db"
        self.supply_db = self.base / "supply" / "artifacts.db"
        self.projections = self.base / "projections"
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=CONVERSATION).initialize()
        MemoryDeletionAuthority(self.authority, owner=OWNER,
                                conversation=CONVERSATION).initialize()
        self.supply = ManagedArtifactStore(self.supply_db, projection_root=self.projections,
                                           workspace_authority=_Workspace())

    def _append(self, text: str, source: str,
                refs: tuple[tuple[str, str], ...] = ()) -> str:
        event = build_outcome_event(subject=OWNER, source_ref=source, text=text,
                                    occurred_at=datetime.now(timezone.utc).isoformat(),
                                    refs=refs)
        EvidenceStore(self.evidence).append(event)
        return event.event_id

    def _document(self, operation_key: str) -> tuple[str, str]:
        created = self.supply.create_artifact(
            subject_id=OWNER, workspace_id="ws:test", title="method:test",
            operation_key=operation_key + ":create", grant_reservation_id="grant:test")
        self.assertEqual(created["status"], "OK")
        committed = self.supply.commit_markdown(
            artifact_id=created["artifact_id"], authorized_subject_id=OWNER,
            expected_head=None, content=SENTINEL, operation_key=operation_key + ":v1",
            grant_reservation_id="grant:test")
        self.assertEqual(committed["status"], "OK")
        return created["artifact_id"], committed["version_id"]

    def test_typed_source_is_a_separate_verified_domain(self) -> None:
        event_id = self._append("synthetic", "reading:demo:1")
        authority = MemoryDeletionAuthority(self.authority, owner=OWNER,
                                            conversation=CONVERSATION)
        authority.commit(operation_id="case-1", target_event="case-1", turn_id=None,
                         blocked_event_ids=[event_id], fingerprint="fp-1",
                         blocked_sources=[{"kind": "life_outcome", "namespace": "",
                                           "ref": "reading:demo:1"}])
        projection = authority.projection()
        self.assertTrue(projection.healthy)
        self.assertIn("reading:demo:1", projection.blocked_source_refs)
        self.assertIn(("life_outcome", "", "reading:demo:1"), projection.blocked_sources)
        # A chain with sources still verifies after a reopen (digest covers the typed field).
        self.assertTrue(MemoryDeletionAuthority(
            self.authority, owner=OWNER, conversation=CONVERSATION).projection().healthy)

    def test_m0_erase_needs_owner_grant_and_is_idempotent(self) -> None:
        event_id = self._append("synthetic-body", "reading:demo:2")
        sibling_id = self._append("other-body", "reading:demo:2-sibling")
        authority = MemoryDeletionAuthority(self.authority, owner=OWNER,
                                            conversation=CONVERSATION)
        authority.commit(operation_id="forget-case:case-2", target_event="case-2", turn_id=None,
                         blocked_event_ids=[event_id], fingerprint="fp-2")
        grant = authority.authorize(case_id="case-2", event_ids=[event_id])
        ledger = MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                                     conversation=CONVERSATION, authority=str(self.authority))
        with self.assertRaises(PermissionError):
            ledger.erase(operation_id="bad-owner", event_ids=[event_id], actor="intruder",
                         grant=grant)
        with self.assertRaises(PermissionError):
            ledger.erase(operation_id="no-grant", event_ids=[event_id], actor=OWNER, grant=None)
        # Strict: the same owner, the same committed seq, a sibling event outside its closure.
        with self.assertRaises(PermissionError):
            ledger.erase(operation_id="sibling", event_ids=[sibling_id], actor=OWNER,
                         grant=grant)
        self.assertEqual(ledger.erase(operation_id="erase-1", event_ids=[event_id], actor=OWNER,
                                      grant=grant)["status"], "APPLIED")
        self.assertEqual(ledger.erase(operation_id="erase-1", event_ids=[event_id], actor=OWNER,
                                      grant=grant)["status"], "APPLIED")   # same identity: replay
        connection = sqlite3.connect(self.evidence)
        try:
            row = connection.execute(
                "SELECT payload_erased,length(content) FROM evidence_events WHERE event_id=?",
                (event_id,)).fetchone()
            sibling = connection.execute(
                "SELECT payload_erased,length(content) FROM evidence_events WHERE event_id=?",
                (sibling_id,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(int(row[0]), 1)
        self.assertEqual(int(row[1]), 0)
        self.assertEqual(int(sibling[0]), 0)
        self.assertEqual(int(sibling[1]), len("other-body"))

    def test_supply_erase_clears_versions_projection_and_is_idempotent(self) -> None:
        artifact_id, version_id = self._document("doc-1")
        unrelated_id, unrelated_version = self._document("doc-unrelated")
        authority = MemoryDeletionAuthority(self.authority, owner=OWNER,
                                            conversation=CONVERSATION)
        authority.commit(operation_id="forget-case:case-3", target_event="case-3", turn_id=None,
                         blocked_event_ids=["evt-3"], fingerprint="fp-3",
                         blocked_sources=[{"kind": "artifact", "namespace": "supply",
                                           "ref": artifact_id}])
        grant = authority.authorize(case_id="case-3", event_ids=["evt-3"])
        first = self.supply.erase_source(operation_key="doc-erase", authorized_subject_id=OWNER,
                                         grant=grant, authority=authority,
                                         artifact_ids=[artifact_id], version_ids=[version_id])
        self.assertEqual(first["status"], "APPLIED", first.get("detail"))
        body = self.supply.read_version(version_id)
        self.assertEqual(body["content"], "")
        self.assertEqual(int(body["content_erased"]), 1)
        self.assertEqual(self.supply.read_artifact(artifact_id)["title"], "[erased]")
        binding = self.supply.read_projection_binding(artifact_id)
        self.assertIsNotNone(binding)
        projection_path = self.projections / binding["relative_path"]
        self.assertTrue(projection_path.is_file())
        self.assertNotIn(SENTINEL.encode("utf-8"), projection_path.read_bytes())
        again = self.supply.erase_source(operation_key="doc-erase", authorized_subject_id=OWNER,
                                         grant=grant, authority=authority,
                                         artifact_ids=[artifact_id], version_ids=[version_id])
        self.assertEqual(again["status"], "APPLIED")
        # A sibling artifact of the same subject is outside the committed source closure.
        outside = self.supply.erase_source(operation_key="doc-other", authorized_subject_id=OWNER,
                                           grant=grant, authority=authority,
                                           artifact_ids=[unrelated_id], version_ids=[])
        self.assertEqual(outside["status"], "DENY")
        self.assertEqual(self.supply.read_version(unrelated_version)["content"], SENTINEL)
        refused = self.supply.erase_source(operation_key="doc-intruder",
                                           authorized_subject_id="other",
                                           grant=grant, authority=authority,
                                           artifact_ids=[artifact_id], version_ids=[])
        self.assertEqual(refused["status"], "DENY")

    def test_gate_refuses_reingest_under_same_typed_source(self) -> None:
        event_id = self._append("synthetic", "reading:demo:1")
        store = ForgetCaseStore(self.cases, evidence_db=self.evidence,
                                controls_db=self.controls, authority_db=self.authority,
                                owner=OWNER, conversation=CONVERSATION)
        store.initialize()
        answer = store.request(operation_key="op-typed", source_refs=["reading:demo:1"])
        self.assertEqual(answer["status"], "COMPLETE")
        port = LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                                  authority_db=self.authority, forget_gate=ForgetGate(store))
        reingest = build_outcome_event(
            subject=OWNER, source_ref="reading:demo:1:new", text="synthetic-reingest",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            refs=[("life_outcome", "reading:demo:1")])
        self.assertEqual(port.append_outcome(reingest)["status"], "refused")
        self.assertIsNone(port.read(event_id))


if __name__ == "__main__":
    unittest.main()
