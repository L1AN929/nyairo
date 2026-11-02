"""S13 F-141 身份接缝：撤销命令的协调日志必须记下它所属的 subject。

真实 ``LifeSupplyService``（GovernanceAuthority + WriterFence + 协调 UoW）在隔离 root 上
接受正式 ``dispatch`` 请求；subject 只能从被撤的 grant 行推导，不能由请求声明。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from lifesupply.identity import (  # noqa: E402
    SERVICE_PRINCIPAL,
    assert_matches_fence,
    owner_writer_for_fence,
)
from lifesupply.service.life_supply import LifeSupplyService  # noqa: E402
from lifesupply.service.switches import FeatureSwitches  # noqa: E402

SUBJECT = "test:s13-identity"
OTHER_SUBJECT = "test:s13-identity-other"
WORKSPACE_SCOPE = "workspace:personal"
WORKSPACE_AUTHORITY = "life-supply.workspace.v1"


@unittest.skipUnless(hasattr(os, "geteuid"), "POSIX-only: deduplicated uid identities")
class IdentitySeamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="s13identity_"))
        uid = os.geteuid()
        self.service = LifeSupplyService(
            self.root / "data", operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(), writer_instance="s13-identity-test-%d" % os.getpid())
        self.uid = uid

    def tearDown(self) -> None:
        self.service.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, request):
        return self.service.dispatch(request, peer_uid=self.uid)

    def issue(self, subject: str, operation_id: str) -> str:
        answer = self.call({"op": "issue_grant", "grant": {
            "subject": subject,
            "capability": "PROVISION_PERSONAL_WORKSPACE",
            "scope": WORKSPACE_SCOPE,
            "target": "workspace:%s" % subject,
            "authority_scope": WORKSPACE_AUTHORITY,
            "operation_id": operation_id,
        }})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        return str(answer["grant_id"])

    def journal(self, operation_id: str):
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT command,subject_id,caller_principal,outcome"
                " FROM life_supply_uow_journal WHERE operation_id=?", (operation_id,)).fetchone()
            return dict(row) if row else None

    def journal_count(self) -> int:
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            return int(con.execute("SELECT COUNT(*) FROM life_supply_uow_journal").fetchone()[0])

    def revoke(self, grant_id: str, operation_id: str, revision: int):
        return self.call({"op": "revoke_grant", "grant_id": grant_id,
                          "expected_revision": int(revision), "operation_id": operation_id})

    def test_revoke_journal_records_the_grant_subject(self) -> None:
        grant_id = self.issue(SUBJECT, "s13:issue:subject")
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            con.row_factory = sqlite3.Row
            revision = int(con.execute(
                "SELECT revision FROM permission_grants WHERE grant_id=?", (grant_id,)
            ).fetchone()[0])
        answer = self.revoke(grant_id, "s13:revoke:subject", revision)
        self.assertEqual(answer.get("status"), "REVOKED", answer)
        row = self.journal("s13:revoke:subject")
        self.assertIsNotNone(row)
        self.assertEqual(row["command"], "REVOKE_GRANT")
        self.assertEqual(row["subject_id"], SUBJECT)
        self.assertEqual(row["caller_principal"], SERVICE_PRINCIPAL)
        self.assertEqual(row["outcome"], "COMMITTED")

    def test_request_cannot_supply_the_subject_and_writes_nothing(self) -> None:
        grant_id = self.issue(OTHER_SUBJECT, "s13:issue:forged")
        before = self.journal_count()
        answer = self.call({"op": "revoke_grant", "grant_id": grant_id,
                            "expected_revision": 1, "operation_id": "s13:forged",
                            "subject": SUBJECT})
        self.assertEqual(answer.get("status"), "DENY", answer)
        self.assertEqual(self.journal_count(), before)
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            state = con.execute("SELECT revocation_state FROM permission_grants WHERE grant_id=?",
                                (grant_id,)).fetchone()[0]
        self.assertEqual(state, "ACTIVE")

    def test_stale_revision_writes_nothing_and_writer_is_the_live_fence(self) -> None:
        grant_id = self.issue(SUBJECT, "s13:issue:stale")
        before = self.journal_count()
        answer = self.revoke(grant_id, "s13:stale", 999)
        self.assertNotEqual(answer.get("status"), "REVOKED", answer)
        self.assertEqual(self.journal_count(), before)
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT writer_owner,writer_instance,writer_epoch"
                              " FROM permission_grants WHERE grant_id=?", (grant_id,)).fetchone()
        fence = self.service.fences["Governance"]
        self.assertEqual(row["writer_owner"], "Governance")
        self.assertEqual(row["writer_instance"], fence.instance_id)
        self.assertEqual(int(row["writer_epoch"]), int(fence.epoch))
        # The same recorded writer identity is not interchangeable with another owner's fence.
        identity = owner_writer_for_fence("Governance", fence)
        assert_matches_fence(identity, fence)
        with self.assertRaises(Exception):
            assert_matches_fence(identity, self.service.fences["Workspace"])


if __name__ == "__main__":
    unittest.main()
