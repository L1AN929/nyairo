#!/usr/bin/env python3
"""F-154 production seam: typed lineage over the *real* Life Supply socket.

What this file proves, and with what evidence
---------------------------------------------
The subject side writes through ``LifeArtifactPort`` onto a real
``LifeSupplyService`` socket -- the same transport the resident uses -- over one canonical
database in a unique fixture root.  The assertions read the owner's own tables back with an
operator-side read-only store, so a local stand-in port cannot pass them:

* a commit that carries ``source_refs`` registers the edges in the *same* owner transaction,
  and ``source_closure`` finds the document from the Memory experience it was built from;
* a malformed lineage fact is refused before anything is written (silently dropping lineage
  would be the failure this seam exists to prevent);
* an operator-side writer must take the owner's real writer fence: while the service holds it
  the open is refused by name, and after a clean stop it writes and the service can be restarted.

POSIX only (AF_UNIX).  Zero model calls, no network.  The fixture root is kept.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_service as pps  # noqa: E402

SUBJECT = "test:f154-seam"
posix_only = unittest.skipIf(os.name == "nt",
                             "needs POSIX AF_UNIX sockets (run under WSL/Linux)")


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_F154_SEAM_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-f154-seam") if override
            else (Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-f154-seam"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root() -> Path:
    root = fixture_base() / ("seam-%d-%d" % (os.getpid(), time.time_ns()))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def owner_call(socket_path, request: dict, *, timeout: float = 20.0) -> dict:
    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise AssertionError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise AssertionError("owner answer is not an object")
    return answer


class SupplyOwner:
    """The real owner on its own socket over one canonical database."""

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import (
            LifeSupplyService,
            LifeSupplySocketServer,
            prepare_socket_path,
        )
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.socket_path = self.root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="f154-seam-test")
        prepare_socket_path(self.socket_path)
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        return owner_call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


@posix_only
class SourceLineageSocketTest(unittest.TestCase):

    def setUp(self) -> None:
        self.root = unique_root()
        self.owner = SupplyOwner(self.root)
        uid = os.geteuid()
        answer = self.owner.call({"op": "issue_grant", "grant": {
            "subject": SUBJECT, "capability": "PROVISION_PERSONAL_WORKSPACE",
            "scope": "workspace:personal", "target": "workspace:%s" % SUBJECT,
            "authority_scope": "life-supply.workspace.v1", "use_mode": "ONE_SHOT",
            "operation_id": "issue:workspace:%s" % SUBJECT}})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        answer = self.owner.call({"op": "provision_workspace",
                                  "grant_id": answer["grant_id"],
                                  "operation_key": "f154-seam:workspace",
                                  "workspace": {"subject_id": SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        self.workspace_id = str(answer["workspace"]["workspace_id"])
        answer = self.owner.call({"op": "issue_grant", "grant": {
            "subject": SUBJECT, "capability": "COMMIT_MANAGED_ARTIFACT",
            "scope": "artifact:personal", "target": "workspace:%s" % self.workspace_id,
            "authority_scope": "life-supply.artifact.v1", "use_mode": "REUSABLE",
            "operation_id": "issue:artifact:%s" % SUBJECT}})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        self.grant_id = str(answer["grant_id"])
        self.port = lap.LifeArtifactPort(str(self.owner.socket_path), SUBJECT)
        self.db_path = self.root / "data" / "life_supply.sqlite"
        self.fences_root = self.root / "data" / "recovery" / "fences"
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        try:
            self.owner.stop()
        except Exception:
            pass

    def _create(self, title: str, tag: str, source_refs=()):
        return self.port.create_artifact(
            workspace_id=self.workspace_id, title=title, content="正文：" + title,
            grant_id=self.grant_id, operation_key=lap.operation_key_for("seam", tag, SUBJECT),
            source_refs=source_refs)

    def _reader(self):
        from lifesupply.artifact.store import ManagedArtifactStore

        return ManagedArtifactStore(self.db_path, read_only=True,
                                    projection_root=self.root / "data" / "projection")

    def test_a_socket_commit_records_lineage_and_closure_finds_it(self):
        method = self._create("做法", "method", source_refs=[
            {"kind": "memory", "namespace": "memory", "ref": "lexp:one"}])
        self.assertTrue(method.applied, method.status)
        draft = self._create("草稿", "draft", source_refs=[
            {"kind": "artifact", "namespace": "supply", "ref": str(method.artifact_id)}])
        self.assertTrue(draft.applied, draft.status)
        closure = self._reader().source_closure(
            [{"kind": "memory", "namespace": "memory", "ref": "lexp:one"}])
        self.assertEqual(closure["status"], "OK", closure)
        self.assertIn(str(method.artifact_id), closure["artifacts"])
        self.assertIn(str(draft.artifact_id), closure["artifacts"])

    def test_practice_and_sharing_use_the_socket_not_a_local_store(self):
        projects = pps.PrivateProjectService(self.port, self.workspace_id, self.grant_id,
                                             subject_id=SUBJECT)
        method = projects.create_project(title="做法", content="正文：做法",
                                         operation_key="f154-seam:practice",
                                         source_refs=[{"kind": "memory", "namespace": "memory",
                                                       "ref": "lexp:practice"}])
        self.assertTrue(method.get("ok"), method)
        method_id = str(method.get("artifact_id") or "")
        share = projects.create_project(
            title="草稿", content="正文：草稿", operation_key="f154-seam:share",
            source_refs=[{"kind": "artifact", "namespace": "supply", "ref": method_id}])
        self.assertTrue(share.get("ok"), share)
        closure = self._reader().source_closure(
            [{"kind": "memory", "namespace": "memory", "ref": "lexp:practice"}])
        self.assertIn(method_id, closure["artifacts"])
        self.assertIn(str(share.get("artifact_id")), closure["artifacts"])

    def test_malformed_lineage_is_refused_before_any_write(self):
        with self.assertRaises(lap.LifeArtifactError):
            self.port.create_artifact(
                workspace_id=self.workspace_id, title="坏血缘", content="正文",
                grant_id=self.grant_id, operation_key="f154-seam:malformed",
                source_refs=[{"kind": "memory"}])
        # The owner refuses the same shape on the wire, and no artifact is created.
        answer = self.owner.call({
            "op": "create_artifact", "grant_id": self.grant_id,
            "operation_key": "f154-seam:malformed-wire",
            "artifact": {"subject_id": SUBJECT, "workspace_id": self.workspace_id,
                         "title": "坏血缘", "content": "正文",
                         "source_refs": ["lexp:not-an-object"]}})
        self.assertEqual(answer.get("status"), "DENY", answer)
        self.assertEqual(answer.get("reason"), "FIELD_TYPE:source_refs", answer)
        self.assertEqual(self.port.list_artifacts(), [])

    def _counts(self) -> tuple[int, int]:
        import sqlite3

        connection = sqlite3.connect(
            self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            versions = int(connection.execute(
                "SELECT COUNT(*) FROM artifact_versions").fetchone()[0])
            edges = int(connection.execute(
                "SELECT COUNT(*) FROM artifact_source_edges").fetchone()[0])
        finally:
            connection.close()
        return versions, edges

    def test_identical_content_with_source_refs_is_refused_by_name(self):
        """A source-carrying commit must not answer NO_CHANGE and silently drop its lineage.

        Same body bytes are already at head, so no version is written by either answer.  The
        negative control is that the refusal happens *before* the owner commits anything: the
        version row, its lineage edges and the head all stay exactly as they were.
        """

        artifact = self._create("草稿", "draft", source_refs=[
            {"kind": "memory", "namespace": "memory", "ref": "lexp:first"}])
        self.assertTrue(artifact.applied, artifact.to_dict())
        head = self.port.read_head(artifact.artifact_id)
        before = self._counts()
        refused = self.port.commit_version(
            artifact_id=artifact.artifact_id, expected_head=head["version_id"],
            content="正文：草稿", grant_id=self.grant_id,
            operation_key=lap.operation_key_for("seam", "nochange-refs", SUBJECT),
            source_refs=[{"kind": "memory", "namespace": "memory", "ref": "lexp:second"}])
        self.assertEqual(refused.status, "BLOCKED", refused.to_dict())
        self.assertEqual(refused.reason, "SOURCE_REFS_REQUIRE_NEW_VERSION", refused.to_dict())
        self.assertEqual(self._counts(), before, "no version and no lineage edge was written")
        self.assertEqual(self.port.read_head(artifact.artifact_id)["version_id"],
                         head["version_id"], "the head did not move")
        # A plain replay without source_refs keeps the old read-only NO_CHANGE answer.
        plain = self.port.commit_version(
            artifact_id=artifact.artifact_id, expected_head=head["version_id"],
            content="正文：草稿", grant_id=self.grant_id,
            operation_key=lap.operation_key_for("seam", "nochange-plain", SUBJECT))
        self.assertEqual(plain.status, "NO_CHANGE", plain.to_dict())
        self.assertEqual(self._counts(), before, "a plain replay still writes nothing")

    def test_operator_writer_must_take_the_owner_fence(self):
        from lifesupply.artifact.store import ManagedArtifactStore
        from lifesupply.fencing import FenceHeld

        with self.assertRaises(FenceHeld):
            ManagedArtifactStore(self.db_path, projection_root=self.root / "data" / "projection",
                                 fences_root=self.fences_root,
                                 writer_instance="seam-operator-while-live")
        self._stop()
        writer = ManagedArtifactStore(self.db_path,
                                      projection_root=self.root / "data" / "projection",
                                      fences_root=self.fences_root,
                                      writer_instance="seam-operator-offline")
        try:
            self.assertGreaterEqual(writer.content_erased_count(), 0)
        finally:
            writer.close()
        # The service can be started again on the same file after the operator let go.
        again = SupplyOwner(self.root)
        try:
            self.assertEqual(again.call({"op": "health"}).get("status"), "OK")
        finally:
            again.stop()


if __name__ == "__main__":
    unittest.main()
