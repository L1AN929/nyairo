#!/usr/bin/env python3
"""S01 F-011/F-012/F-014: Markdown projection, external observation and explicit import.

The owner is the real ``LifeSupplyService`` over a unique fixture root; commands go through its
own ``dispatch`` with the test process's uid as the operator/service peer.  No socket is needed,
but the owner imports POSIX-only modules, so the module is skipped on Windows.

Covered:
* create writes canonical version and managed Markdown separately; both health domains are read
  independently (``AVAILABLE`` payload vs ``CURRENT``/``MISSING``/``DIVERGED``/``UNBOUND`` file);
* a missing projection never blocks reading the canonical payload; explicit rebuild repairs it;
* an external edit stays an observation + candidate (author UNKNOWN), head does not move;
* import needs ``confirm=true``, CAS on the observed base version, and mints one new version with
  ``EXPLICIT_IMPORT`` provenance; same-key retry is one version, different intent conflicts;
* a stale base after a real second writer conflicts without overwriting or rebasing;
* symlink/oversize/non-UTF8 projections are refused; rebuild never overwrites DIVERGED;
* same-root restart keeps head/binding/provenance and the same candidate id.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

SUBJECT = "test:s01-projection"
WORKSPACE_CAP = "PROVISION_PERSONAL_WORKSPACE"
WORKSPACE_SCOPE = "workspace:personal"
WORKSPACE_AUTHORITY = "life-supply.workspace.v1"
ARTIFACT_CAP = "COMMIT_MANAGED_ARTIFACT"
ARTIFACT_SCOPE = "artifact:personal"
ARTIFACT_AUTHORITY = "life-supply.artifact.v1"
V1 = "# 夜航手记\n\n她写下的第一版。\n"
V1_EDIT = "# 夜航手记\n\n她写下的第一版。\n\n外面有人补了一句。\n"

posix_only = unittest.skipIf(os.name == "nt", "owner imports POSIX-only modules")


def projection_rel(artifact_id: str) -> Path:
    digest = sha256(artifact_id.encode()).hexdigest()
    return Path(digest[:2]) / (digest + ".md")


class ProjectionFixture:
    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.uid = os.getuid()
        self.service = LifeSupplyService(
            self.root, operator_uids={self.uid}, service_uids={self.uid},
            switches=FeatureSwitches.all_on(), writer_instance="s01-test")
        if self.service.state.state != "READY":
            raise AssertionError("service not ready: %r" % (self.service.startup_error,))

    def call(self, request: dict) -> dict:
        return self.service.dispatch(request, peer_uid=self.uid)

    def issue(self, capability: str, scope: str, target: str, authority: str,
              use_mode: str, operation_id: str) -> str:
        answer = self.call({"op": "issue_grant", "grant": {
            "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
            "authority_scope": authority, "use_mode": use_mode, "operation_id": operation_id}})
        if answer.get("status") != "ALLOW":
            raise AssertionError("grant refused: %r" % (answer,))
        return str(answer["grant_id"])

    def provision(self) -> tuple[str, str]:
        grant = self.issue(WORKSPACE_CAP, WORKSPACE_SCOPE, "workspace:%s" % SUBJECT,
                           WORKSPACE_AUTHORITY, "ONE_SHOT", "s01:workspace")
        answer = self.call({"op": "provision_workspace", "grant_id": grant,
                            "operation_key": "s01:workspace",
                            "workspace": {"subject_id": SUBJECT}})
        if answer.get("status") != "OK":
            raise AssertionError("workspace refused: %r" % (answer,))
        workspace_id = str(answer["workspace"]["workspace_id"])
        artifact_grant = self.issue(ARTIFACT_CAP, ARTIFACT_SCOPE,
                                    "workspace:%s" % workspace_id,
                                    ARTIFACT_AUTHORITY, "REUSABLE", "s01:artifact-grant")
        return workspace_id, artifact_grant

    def create(self, workspace_id: str, grant: str, content: str = V1,
               key: str = "s01:create") -> dict:
        answer = self.call({
            "op": "create_artifact", "grant_id": grant, "operation_key": key,
            "artifact": {"subject_id": SUBJECT, "workspace_id": workspace_id,
                         "title": "夜航手记", "content": content}})
        if answer.get("status") != "OK":
            raise AssertionError("create refused: %r" % (answer,))
        return answer

    def commit(self, artifact_id: str, expected_head: str, content: str, grant: str,
               key: str) -> dict:
        answer = self.call({
            "op": "commit_markdown", "grant_id": grant, "operation_key": key,
            "artifact": {"subject_id": SUBJECT, "artifact_id": artifact_id,
                         "expected_head": expected_head, "content": content}})
        if answer.get("status") != "OK":
            raise AssertionError("commit refused: %r" % (answer,))
        return answer

    def observe(self, artifact_id: str) -> dict:
        answer = self.call({"op": "observe_projection", "subject": SUBJECT,
                            "artifact_id": artifact_id})
        if answer.get("status") != "OK":
            raise AssertionError("observe refused: %r" % (answer,))
        return answer

    def import_candidate(self, artifact_id: str, candidate_id: str,
                         base_version_id: str | None, grant: str, key: str,
                         confirm: bool = True) -> dict:
        return self.call({
            "op": "import_projection", "grant_id": grant, "operation_key": key,
            "import": {"subject_id": SUBJECT, "artifact_id": artifact_id,
                       "candidate_id": candidate_id, "base_version_id": base_version_id,
                       "confirm": confirm}})

    def path(self, artifact_id: str) -> Path:
        return self.root / "projection" / projection_rel(artifact_id)

    def close(self) -> None:
        self.service.close()


class ProjectionImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.owner = ProjectionFixture(Path(self.tmp.name))
        self.workspace_id, self.grant = self.owner.provision()
        created = self.owner.create(self.workspace_id, self.grant)
        self.artifact_id = str(created["artifact_id"])
        self.v1 = str(created["version_id"])

    def tearDown(self) -> None:
        self.owner.close()
        self.tmp.cleanup()

    # -- creation, health domains, missing/rebuild ---------------------------------------

    def test_create_writes_projection_and_reports_both_health_domains(self):
        view = self.owner.observe(self.artifact_id)
        self.assertEqual(view["content_health"]["state"], "AVAILABLE")
        self.assertEqual(view["representation_health"]["state"], "CURRENT")
        self.assertTrue(self.owner.path(self.artifact_id).is_file())
        self.assertIsNone(view["candidate"])

    def test_missing_projection_does_not_block_canonical_and_rebuild_repairs_it(self):
        self.owner.path(self.artifact_id).unlink()
        view = self.owner.observe(self.artifact_id)
        self.assertEqual(view["representation_health"]["state"], "MISSING")
        self.assertEqual(view["content_health"]["state"], "AVAILABLE")
        read = self.owner.call({"op": "read_artifact", "subject": SUBJECT,
                                "artifact_id": self.artifact_id})
        self.assertEqual(read["status"], "OK")
        self.assertEqual(read["content"], V1)
        repaired = self.owner.call({"op": "rebuild_projection", "subject": SUBJECT,
                                    "artifact_id": self.artifact_id})
        self.assertEqual(repaired["status"], "OK")
        self.assertEqual(repaired["representation_health"]["state"], "CURRENT")

    # -- external observation and explicit import ---------------------------------------

    def test_external_edit_is_candidate_not_canonical_and_import_is_explicit(self):
        self.owner.path(self.artifact_id).write_text(V1_EDIT, encoding="utf-8")
        view = self.owner.observe(self.artifact_id)
        self.assertEqual(view["representation_health"]["state"], "DIVERGED")
        self.assertEqual(view["head"]["version_id"], self.v1)
        candidate = view["candidate"]
        self.assertEqual(candidate["authority"], "EXTERNAL_UNKNOWN")
        self.assertEqual(candidate["base_projection_version"], self.v1)
        encoded = json.dumps(view)
        self.assertNotIn(V1_EDIT, encoded)  # no body in the observation
        self.assertNotIn("_candidate_content", encoded)

        denied = self.owner.import_candidate(self.artifact_id, candidate["candidate_id"],
                                             self.v1, self.grant, "s01:import-no-confirm",
                                             confirm=False)
        self.assertEqual(denied["status"], "DENY")
        self.assertEqual(denied["reason"], "EXPLICIT_CONFIRMATION_REQUIRED")

        imported = self.owner.import_candidate(self.artifact_id, candidate["candidate_id"],
                                               self.v1, self.grant, "s01:import")
        self.assertEqual(imported["status"], "OK")
        self.assertEqual(imported["source_kind"], "EXPLICIT_IMPORT")
        self.assertEqual(imported["provenance"]["external_contributor"], "UNKNOWN")
        self.assertEqual(imported["provenance"]["base_projection_version"], self.v1)
        self.assertEqual(imported["representation_health"]["state"], "CURRENT")
        self.assertNotEqual(imported["version_id"], self.v1)

        old = self.owner.call({"op": "read_version", "subject": SUBJECT,
                               "artifact_id": self.artifact_id, "version_id": self.v1})
        self.assertEqual(old["content"], V1)
        self.assertIsNone(old["provenance"])
        new = self.owner.call({"op": "read_version", "subject": SUBJECT,
                               "artifact_id": self.artifact_id,
                               "version_id": imported["version_id"]})
        self.assertEqual(new["provenance"]["source_kind"], "EXPLICIT_IMPORT")

        # Same-key retry is the same import, not a second version.
        replay = self.owner.import_candidate(self.artifact_id, candidate["candidate_id"],
                                             self.v1, self.grant, "s01:import")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["version_id"], imported["version_id"])
        versions = self.owner.call({"op": "list_versions", "subject": SUBJECT,
                                    "artifact_id": self.artifact_id})
        self.assertEqual(versions["total_versions"], 2)

        # Same key with a different candidate is a conflict, never a silent rebind.
        other = self.owner.call({"op": "observe_projection", "subject": SUBJECT,
                                 "artifact_id": self.artifact_id})
        self.assertEqual(other["representation_health"]["state"], "CURRENT")
        conflict = self.owner.import_candidate(self.artifact_id, "projection-candidate:other",
                                               self.v1, self.grant, "s01:import")
        self.assertEqual(conflict["status"], "CONFLICT")

    def test_stale_base_is_a_conflict_and_does_not_overwrite(self):
        self.owner.path(self.artifact_id).write_text(V1_EDIT, encoding="utf-8")
        view = self.owner.observe(self.artifact_id)
        candidate = view["candidate"]
        # A real second writer advances the head first.
        second = self.owner.commit(self.artifact_id, self.v1, V1 + "\nsecond writer\n",
                                   self.grant, "s01:second-writer")
        self.assertEqual(second["status"], "OK")
        refused = self.owner.import_candidate(self.artifact_id, candidate["candidate_id"],
                                              self.v1, self.grant, "s01:stale-import")
        self.assertEqual(refused["status"], "CONFLICT")
        self.assertEqual(refused["reason"], "HEAD_MOVED")
        head = self.owner.call({"op": "read_head", "subject": SUBJECT,
                                "artifact_id": self.artifact_id})["head"]
        self.assertEqual(head["version_id"], second["version_id"])
        self.assertNotEqual(head["content"], V1_EDIT)

    def test_projection_changed_after_observation_is_stale(self):
        self.owner.path(self.artifact_id).write_text(V1_EDIT, encoding="utf-8")
        candidate = self.owner.observe(self.artifact_id)["candidate"]
        self.owner.path(self.artifact_id).write_text(V1_EDIT + "later\n", encoding="utf-8")
        refused = self.owner.import_candidate(self.artifact_id, candidate["candidate_id"],
                                              self.v1, self.grant, "s01:late-import")
        self.assertEqual(refused["status"], "BLOCKED")
        self.assertEqual(refused["reason"], "CANDIDATE_STALE")

    # -- safety and independent health ---------------------------------------------------

    def test_symlink_oversize_and_bad_utf8_are_refused(self):
        path = self.owner.path(self.artifact_id)
        outside = self.owner.root / "outside.txt"
        outside.write_text("outside secret\n", encoding="utf-8")
        path.unlink()
        path.symlink_to(outside)
        view = self.owner.observe(self.artifact_id)
        self.assertEqual(view["representation_health"]["state"], "UNBOUND")
        self.assertEqual(view["representation_health"]["reason_code"], "SYMLINK_REFUSED")
        self.assertIsNone(view["candidate"])
        path.unlink()

        path.write_bytes(b"x" * (256 * 1024 + 1))
        view = self.owner.observe(self.artifact_id)
        self.assertEqual(view["representation_health"]["state"], "DIVERGED")
        self.assertEqual(view["representation_health"]["reason_code"], "PROJECTION_TOO_LARGE")

        path.write_bytes(b"\xff\xfe\x00bad utf8")
        view = self.owner.observe(self.artifact_id)
        self.assertEqual(view["representation_health"]["state"], "DIVERGED")
        self.assertEqual(view["representation_health"]["reason_code"], "PROJECTION_NOT_UTF8")

        rebuilt = self.owner.call({"op": "rebuild_projection", "subject": SUBJECT,
                                   "artifact_id": self.artifact_id})
        self.assertEqual(rebuilt["status"], "BLOCKED")
        self.assertEqual(rebuilt["reason"], "PROJECTION_DIVERGED")

    def test_binding_path_escape_is_unbound_and_not_rebuilt(self):
        store = self.owner.service.artifact
        original = store.read_projection_binding(self.artifact_id)["relative_path"]
        try:
            with store._connection() as con:
                con.execute("UPDATE artifact_projection_bindings SET relative_path=?"
                            " WHERE artifact_id=?", ("../escape.md", self.artifact_id))
                con.commit()
            view = self.owner.observe(self.artifact_id)
            self.assertEqual(view["representation_health"]["state"], "UNBOUND")
            self.assertEqual(view["representation_health"]["reason_code"],
                             "UNSAFE_BINDING_PATH")
            rebuilt = self.owner.call({"op": "rebuild_projection", "subject": SUBJECT,
                                       "artifact_id": self.artifact_id})
            self.assertEqual(rebuilt["status"], "BLOCKED")
            self.assertEqual(rebuilt["reason"], "PROJECTION_BINDING_UNSAFE")
        finally:
            with store._connection() as con:
                con.execute("UPDATE artifact_projection_bindings SET relative_path=?"
                            " WHERE artifact_id=?", (original, self.artifact_id))
                con.commit()

    def test_corrupt_payload_is_reported_independently_of_the_file(self):
        path = self.owner.path(self.artifact_id)
        self.assertTrue(path.is_file())
        store = self.owner.service.artifact
        head = store.read_head(self.artifact_id)
        corrupt = dict(head)
        corrupt["content"] = "tampered body"
        view = store.observe_projection(self.artifact_id, _head_override=corrupt)
        self.assertEqual(view["content_health"]["state"], "CORRUPT")
        self.assertEqual(view["content_health"]["reason_code"], "STORED_HASH_MISMATCH")
        # The file exists and is readable, yet the payload is corrupt: a file is not the payload.
        self.assertEqual(view["representation_health"]["state"], "DIVERGED")
        self.assertIsNone(view["candidate"])

    # -- restart -------------------------------------------------------------------------

    def test_same_root_restart_keeps_head_binding_provenance_and_candidate(self):
        self.owner.path(self.artifact_id).write_text(V1_EDIT, encoding="utf-8")
        candidate = self.owner.observe(self.artifact_id)["candidate"]
        imported = self.owner.import_candidate(self.artifact_id, candidate["candidate_id"],
                                               self.v1, self.grant, "s01:restart-import")
        self.assertEqual(imported["status"], "OK")
        # Leave a fresh external edit so candidate state must survive the restart too.
        self.owner.path(self.artifact_id).write_text(V1_EDIT + "after restart\n", encoding="utf-8")
        before = self.owner.observe(self.artifact_id)
        self.owner.close()

        restarted = ProjectionFixture(self.owner.root)
        try:
            after = restarted.observe(self.artifact_id)
            self.assertEqual(after["head"]["version_id"], imported["version_id"])
            self.assertEqual(after["representation_health"]["state"], "DIVERGED")
            self.assertEqual(after["candidate"]["candidate_id"],
                             before["candidate"]["candidate_id"])
            version = restarted.call({"op": "read_version", "subject": SUBJECT,
                                      "artifact_id": self.artifact_id,
                                      "version_id": imported["version_id"]})
            self.assertEqual(version["provenance"]["source_kind"], "EXPLICIT_IMPORT")
        finally:
            restarted.close()


if __name__ == "__main__":
    unittest.main()
