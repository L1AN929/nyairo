"""F-154 scenario 9, projection surface: a file has no incarnation, canonical does.

Covers the two seams added for this case on an isolated synthetic root:

* ``ManagedArtifactStore.reconcile_registered_projections`` re-derives every *registered*
  projection from its canonical head (so a pre-deletion file put back on the path is
  overwritten), refuses a binding that is not a safely writable managed file, and never touches
  an unregistered file in the directory;
* the explicit import entry refuses by name when the canonical head was erased, so a restored
  projection cannot become private text again, while an ordinary external edit still imports.

The synthetic roots stay on disk (no ``TemporaryDirectory``).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app.evidence import EvidenceStore  # noqa: E402
from life_experience_port import build_outcome_event  # noqa: E402
from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402

OWNER = "chiyo"
CONVERSATION = "life:chiyo"
SENTINEL = "F154-SYNTHETIC-PROJECTION-SENTINEL"
SENTINEL_UNRELATED = "F154-SYNTHETIC-UNRELATED-BODY"


def _fixture_root(name: str) -> Path:
    base = ROOT / ".agents" / "worker-runs" / "f154-projection-tests" / (
        "%s-%s" % (name, uuid.uuid4()))
    (base / "retained").mkdir(parents=True, exist_ok=True)
    (base / "memory").mkdir(parents=True, exist_ok=True)
    (base / "supply").mkdir(parents=True, exist_ok=True)
    (base / "projection").mkdir(parents=True, exist_ok=True)
    return base


class _Workspace:
    def get_workspace_by_id(self, workspace_id: str) -> dict:
        return {"subject_id": OWNER, "lifecycle": "ACTIVE"}


def _version_rows(db: Path) -> int:
    connection = sqlite3.connect(str(db))
    try:
        return int(connection.execute("SELECT count(*) FROM artifact_versions").fetchone()[0])
    finally:
        connection.close()


def _authority(path: Path) -> MemoryDeletionAuthority:
    MemoryDeletionAuthority(path, owner=OWNER, conversation=CONVERSATION).initialize()
    return MemoryDeletionAuthority(path, owner=OWNER, conversation=CONVERSATION)


def _m0_event(evidence: Path, source: str, artifact_id: str) -> str:
    event = build_outcome_event(
        subject=OWNER, source_ref=source, text="F154 合成：被忘记的来源经历。",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        refs=[("artifact", artifact_id)])
    EvidenceStore(evidence).append(event)
    return event.event_id


def _forget_artifact(store: ManagedArtifactStore, authority: MemoryDeletionAuthority,
                     evidence: Path, artifact_id: str, case: str) -> dict:
    event_id = _m0_event(evidence, "reading:projection-test:" + case, artifact_id)
    authority.commit(operation_id="forget-case:" + case, target_event=case, turn_id=None,
                     blocked_event_ids=[event_id], fingerprint="fp:" + case,
                     blocked_sources=[{"kind": "artifact", "ref": artifact_id}])
    grant = authority.authorize(case_id=case, event_ids=[event_id],
                                sources=[{"kind": "artifact", "ref": artifact_id}])
    return store.erase_source(operation_key="erase:" + case, authorized_subject_id=OWNER,
                              grant=grant, authority=authority, artifact_ids=[artifact_id])


class ProjectionReconcileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _fixture_root("reconcile")
        self.db = self.base / "supply" / "artifacts.db"
        self.projection_root = self.base / "projection"
        self.authority_path = self.base / "retained" / "authority.db"
        self.store = ManagedArtifactStore(self.db, projection_root=self.projection_root,
                                          workspace_authority=_Workspace())

    def _artifact(self, text: str, key: str) -> tuple[str, str]:
        created = self.store.create_artifact(
            subject_id=OWNER, workspace_id="ws:test", title="doc:" + key,
            operation_key=key + ":create", grant_reservation_id="grant:test")
        self.assertEqual(created["status"], "OK")
        committed = self.store.commit_markdown(
            artifact_id=created["artifact_id"], authorized_subject_id=OWNER,
            expected_head=None, content=text, operation_key=key + ":v1",
            grant_reservation_id="grant:test")
        self.assertEqual(committed["status"], "OK")
        return str(created["artifact_id"]), str(committed["version_id"])

    def _path(self, artifact_id: str) -> Path:
        binding = self.store.read_projection_binding(artifact_id)
        self.assertIsNotNone(binding)
        return self.projection_root / str(binding["relative_path"])

    def test_reconcile_rebuilds_a_backfilled_projection_from_erased_canonical(self) -> None:
        artifact_id, version_id = self._artifact(SENTINEL, "backfill")
        unrelated_id, _ = self._artifact(SENTINEL_UNRELATED, "unrelated")
        path = self._path(artifact_id)
        original = path.read_bytes()
        erased = _forget_artifact(self.store, _authority(self.authority_path),
                                  self.base / "memory" / "evidence.db",
                                  artifact_id, "projection-backfill")
        self.assertEqual(erased["status"], "APPLIED")
        self.assertEqual(path.read_bytes(), b"")

        path.write_bytes(original)                       # a pre-deletion file put back
        self.assertIn(SENTINEL.encode("utf-8"), path.read_bytes())
        unrelated_bytes = self._path(unrelated_id).read_bytes()

        first = self.store.reconcile_registered_projections()
        self.assertEqual(first["status"], "OK")
        self.assertIn(artifact_id, first["reconciled"])
        self.assertEqual(path.read_bytes(), b"")
        checked = self.store.check_projection(artifact_id)
        self.assertEqual(checked["status"], "OK")
        # The unrelated representation already matched its head: it is not rewritten.
        self.assertIn(unrelated_id, first["unchanged"])
        self.assertEqual(self._path(unrelated_id).read_bytes(), unrelated_bytes)
        self.assertIn(SENTINEL_UNRELATED.encode("utf-8"), unrelated_bytes)

        again = self.store.reconcile_registered_projections()
        self.assertEqual(again["status"], "OK")
        self.assertIn(artifact_id, again["unchanged"])

    @unittest.skipIf(os.name == "nt", "symlinks are POSIX-only in this fixture")
    def test_a_symlinked_binding_is_named_and_neither_side_is_touched(self) -> None:
        artifact_id, _ = self._artifact(SENTINEL, "symlink")
        path = self._path(artifact_id)
        outside = self.base / "outside.txt"
        outside.write_text(SENTINEL, encoding="utf-8")
        stray = self.projection_root / "stray.txt"
        stray.write_text(SENTINEL, encoding="utf-8")
        replacement = self.base / "link.tmp"
        os.symlink(str(outside), str(replacement))
        os.replace(str(replacement), str(path))          # same path, a symlink now

        answer = self.store.reconcile_registered_projections()
        self.assertEqual(answer["status"], "BLOCKED")
        self.assertTrue(answer["blocked"])
        self.assertIn("SYMLINK_REFUSED", {item["reason"] for item in answer["blocked"]})
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), SENTINEL)
        # An unregistered file in the same directory is never guessed at or rewritten.
        self.assertEqual(stray.read_text(encoding="utf-8"), SENTINEL)


class _ServiceFixture:
    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.uid = os.getuid()
        self.service = LifeSupplyService(
            self.root, operator_uids={self.uid}, service_uids={self.uid},
            switches=FeatureSwitches.all_on(), writer_instance="f154-projection-test")
        if self.service.state.state != "READY":
            raise AssertionError("service not ready: %r" % (self.service.startup_error,))

    def call(self, request: dict) -> dict:
        return self.service.dispatch(request, peer_uid=self.uid)

    def grant(self, workspace_id: str, operation_id: str) -> str:
        answer = self.call({"op": "issue_grant", "grant": {
            "subject": OWNER, "capability": "COMMIT_MANAGED_ARTIFACT",
            "scope": "artifact:personal", "target": "workspace:%s" % workspace_id,
            "authority_scope": "life-supply.artifact.v1", "use_mode": "REUSABLE",
            "operation_id": operation_id}})
        if answer.get("status") != "ALLOW":
            raise AssertionError("grant refused: %r" % (answer,))
        return str(answer["grant_id"])

    def provision(self) -> tuple[str, str]:
        answer = self.call({"op": "issue_grant", "grant": {
            "subject": OWNER, "capability": "PROVISION_PERSONAL_WORKSPACE",
            "scope": "workspace:personal", "target": "workspace:%s" % OWNER,
            "authority_scope": "life-supply.workspace.v1", "use_mode": "ONE_SHOT",
            "operation_id": "f154:workspace"}})
        if answer.get("status") != "ALLOW":
            raise AssertionError("workspace grant refused: %r" % (answer,))
        provisioned = self.call({"op": "provision_workspace", "grant_id": answer["grant_id"],
                                 "operation_key": "f154:workspace",
                                 "workspace": {"subject_id": OWNER}})
        if provisioned.get("status") != "OK":
            raise AssertionError("workspace refused: %r" % (provisioned,))
        workspace_id = str(provisioned["workspace"]["workspace_id"])
        return workspace_id, self.grant(workspace_id, "f154:artifact")

    def create(self, workspace_id: str, grant: str, content: str, key: str) -> dict:
        answer = self.call({
            "op": "create_artifact", "grant_id": grant, "operation_key": key,
            "artifact": {"subject_id": OWNER, "workspace_id": workspace_id,
                         "title": "doc", "content": content}})
        if answer.get("status") != "OK":
            raise AssertionError("create refused: %r" % (answer,))
        return answer

    def observe(self, artifact_id: str) -> dict:
        answer = self.call({"op": "observe_projection", "subject": OWNER,
                            "artifact_id": artifact_id})
        if answer.get("status") != "OK":
            raise AssertionError("observe refused: %r" % (answer,))
        return answer

    def import_candidate(self, artifact_id: str, candidate: dict, grant: str,
                         key: str, base_version_id: str | None = None) -> dict:
        return self.call({
            "op": "import_projection", "grant_id": grant, "operation_key": key,
            "import": {"subject_id": OWNER, "artifact_id": artifact_id,
                       "candidate_id": str(candidate["candidate_id"]),
                       "base_version_id": (candidate.get("base_projection_version")
                                           if base_version_id is None else base_version_id),
                       "confirm": True}})

    def path(self, artifact_id: str) -> Path:
        binding = self.service.artifact_store.read_projection_binding(artifact_id)
        return self.root / "projection" / str(binding["relative_path"])

    def close(self) -> None:
        self.service.close()


class ProjectionImportGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _fixture_root("import-guard")
        self.owner = _ServiceFixture(self.base / "supply")
        self.workspace_id, self.grant = self.owner.provision()

    def tearDown(self) -> None:
        self.owner.close()

    def test_import_is_refused_when_the_canonical_head_was_erased(self) -> None:
        created = self.owner.create(self.workspace_id, self.grant, SENTINEL, "erased:create")
        artifact_id, version_id = str(created["artifact_id"]), str(created["version_id"])
        path = self.owner.path(artifact_id)
        original = path.read_bytes()
        authority = _authority(self.base / "retained" / "authority.db")
        erased = _forget_artifact(self.owner.service.artifact_store, authority,
                                  self.base / "memory" / "evidence.db",
                                  artifact_id, "import-guard")
        self.assertEqual(erased["status"], "APPLIED")
        self.assertEqual(path.read_bytes(), b"")

        path.write_bytes(original)                       # the old representation is back
        view = self.owner.observe(artifact_id)
        self.assertEqual(view["representation_health"]["state"], "DIVERGED")
        self.assertNotIn("_candidate_content", view)
        self.assertIsNotNone(view["candidate"])

        before = _version_rows(self.owner.service.db.path)
        answer = self.owner.import_candidate(artifact_id, view["candidate"], self.grant,
                                             "erased:import")
        self.assertEqual(answer["status"], "BLOCKED")
        self.assertEqual(answer["reason"], "CANONICAL_PAYLOAD_ERASED")
        self.assertEqual(_version_rows(self.owner.service.db.path), before)
        self.assertEqual(path.read_bytes(), original)    # the file itself is untouched here

    def test_an_ordinary_external_edit_still_imports(self) -> None:
        created = self.owner.create(self.workspace_id, self.grant, SENTINEL, "ordinary:create")
        artifact_id = str(created["artifact_id"])
        path = self.owner.path(artifact_id)
        edited = SENTINEL + "\n\n隔离演示：外部补写的一句。\n"
        path.write_text(edited, encoding="utf-8")
        view = self.owner.observe(artifact_id)
        self.assertEqual(view["representation_health"]["state"], "DIVERGED")
        answer = self.owner.import_candidate(artifact_id, view["candidate"], self.grant,
                                             "ordinary:import")
        self.assertEqual(answer["status"], "OK")
        self.assertTrue(answer.get("version_created"))
        self.assertEqual(_version_rows(self.owner.service.db.path), 2)
        after = self.owner.observe(artifact_id)
        self.assertEqual(after["representation_health"]["state"], "CURRENT")


if __name__ == "__main__":
    unittest.main()
