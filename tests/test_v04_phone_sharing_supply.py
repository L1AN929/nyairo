#!/usr/bin/env python3
"""v0.4 阶段 B：手机分享面跑在**真实 Supply ManagedArtifact owner** 上的短链。

证据分级（照实写）
------------------
* owner 是真的：``components/supply/lifesupply/artifact/store.py::ManagedArtifactStore``，真 sqlite、
  不可变版本、真实 ``artifact_operations`` / ``artifact_effect_receipts`` 账本、真实 CAS。
* 中间只有一层测试运输替身（``SupplyStorePort``，来自 ``tests/test_v04_sharing_supply_owner.py``）：
  语义与 ``life_artifact_port.LifeArtifactPort`` 一致；真实部署里这一层是 Unix socket + Governance
  预留，本文件**没有**起 socket、**没有**走 Governance。
* 被测的是 **P 的分享门面**（``components/phone/sharing.py``）：它建在**实例自己的** subject /
  workspace / grant 上（这里就是本夹具那一个），不新开 store、不建表。
* 验收链：``prepare → defer → send_local → 重开（新 store + 新 service）``，外加同 key 不重复、
  来源换版阻断。sqlite 夹具在 ``$NYAIRO_SHARE_FIXTURE_ROOT`` 或系统临时目录下**只创建不删除**，
  每个用例一个文件名（带进程与时间戳）。
* ``ManagedArtifactStore`` 需要 ``fcntl``：本文件在 POSIX（WSL）上运行，Windows 上整文件跳过。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "life" / "scripts"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_service as pps  # noqa: E402
from components.phone import sharing as phone_sharing  # noqa: E402

from test_v04_sharing_supply_owner import (  # noqa: E402
    GRANT,
    SOURCE_TEXT,
    SUBJECT,
    WORKSPACE,
    Clock,
    SupplyStorePort,
    WsAuthority,
    fixture_root,
)

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                "ManagedArtifactStore imports fcntl (real owner runs on WSL)")

try:
    from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402
except Exception:  # pragma: no cover - Windows (no fcntl)
    ManagedArtifactStore = None  # type: ignore[assignment]

SHARE_TEXT = "留白重排完了，第二段还是挤。"


@POSIX_ONLY
class PhoneSharingRealOwnerTest(unittest.TestCase):
    """One real sqlite owner per test; the share surface is P's own facade."""

    def setUp(self) -> None:
        if ManagedArtifactStore is None:  # pragma: no cover
            self.skipTest("ManagedArtifactStore unavailable (needs fcntl)")
        stamp = "%s-%d-%d" % (self._testMethodName.replace("test_", ""), os.getpid(),
                              int(time.time() * 1000))
        self.db_path = str(fixture_root() / ("phone-%s.sqlite" % stamp))
        self.clock = Clock()
        self.store = self._open_store()
        self.port = SupplyStorePort(self.store)
        self.sharing = self._open_sharing()
        self.source = self._seed_source()

    # -- fixtures ---------------------------------------------------------------------

    def _open_store(self):
        return ManagedArtifactStore(self.db_path, workspace_authority=WsAuthority(),
                                    clock=self.clock)

    def _open_sharing(self) -> phone_sharing.PhoneSharing:
        projects = pps.PrivateProjectService(SupplyStorePort(self.store), WORKSPACE, GRANT,
                                            subject_id=SUBJECT)
        # 注入这份夹具的 Clock：让「同一意图 + 同一 key」的信封逐字节相同（真实部署里时钟在走，
        # owner 会以 CAS_CONFLICT「operation key reused with different request」具名拒绝 —— 那也是
        # 只有一条草稿，见 test_the_same_prepare_key_never_makes_a_second_draft 的注释）。
        return phone_sharing.PhoneSharing(projects, subject=SUBJECT, workspace_id=WORKSPACE,
                                          clock=self.clock)

    def _seed_source(self) -> str:
        created = self.store.create_artifact(
            subject_id=SUBJECT, workspace_id=WORKSPACE, title="她的留白练习",
            operation_key="phone-seed-create", grant_reservation_id=GRANT)
        self.assertEqual(created.get("status"), "OK", created)
        artifact_id = str(created["artifact_id"])
        committed = self.store.commit_markdown(
            artifact_id=artifact_id, authorized_subject_id=SUBJECT, expected_head=None,
            content=SOURCE_TEXT, operation_key="phone-seed-v1", grant_reservation_id=GRANT)
        self.assertEqual(committed.get("status"), "OK", committed)
        return artifact_id

    def versions_of(self, artifact_id: str) -> int:
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute("SELECT count(*) FROM artifact_versions WHERE artifact_id=?",
                              (artifact_id,)).fetchone()
        finally:
            con.close()
        return int(row[0])

    def shares_on_owner(self) -> list[str]:
        return [str(row["artifact_id"]) for row in self.store.list_artifacts(SUBJECT)
                if phone_sharing.is_share_title(row.get("title"))]

    def draft(self, **kwargs):
        options = {"source_artifact_id": self.source, "share_text": SHARE_TEXT,
                   "title": "留白重排"}
        options.update(kwargs)
        answer = self.sharing.prepare(**options)
        self.assertTrue(answer["ok"], answer)
        return answer

    # -- tests ------------------------------------------------------------------------

    def test_short_chain_prepare_defer_send_and_reopen(self) -> None:
        sources = self.sharing.sources()
        self.assertTrue(sources["ok"], sources)
        offered = {row["artifact_id"]: row for row in sources["data"]["sources"]}
        self.assertTrue(offered[self.source]["readable"])

        draft = self.draft()
        share_id = draft["data"]["share_id"]
        self.assertEqual(self.versions_of(share_id), 1)
        self.assertEqual(self.shares_on_owner(), [share_id])

        deferred = self.sharing.act(share_id=share_id, action="defer",
                                    expected_head=draft["data"]["head_version_id"],
                                    opportunity_ref="夜里安静下来的时候")
        self.assertTrue(deferred["ok"], deferred)
        self.assertEqual(deferred["data"]["state"], "DEFERRED")

        sent = self.sharing.act(share_id=share_id, action="send_local",
                               expected_head=deferred["data"]["head_version_id"])
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["data"]["state"], "LOCAL_DELIVERED")
        self.assertEqual(sent["data"]["delivery_count"], 1)
        receipt = sent["data"]["receipt"]
        self.assertTrue(receipt["local_only"])
        self.assertFalse(receipt["external_channel_sent"])
        self.assertFalse(receipt["read_by_audience"])
        self.assertIn("本地存档", sent["data"]["note"])
        self.assertEqual(self.versions_of(share_id), 3)

        # 真「重开」：新的 store 句柄（同一 sqlite）+ 新的 projects + 新的门面。
        self.store = self._open_store()
        self.sharing = self._open_sharing()
        after = self.sharing.detail(share_id)
        self.assertTrue(after["ok"], after)
        self.assertEqual(after["data"]["share"]["state"], "LOCAL_DELIVERED")
        self.assertEqual(after["data"]["receipt"]["delivery_id"], receipt["delivery_id"])
        listing = self.sharing.listing()
        self.assertEqual(listing["data"]["counts"]["local_delivered"], 1)
        self.assertEqual(listing["data"]["inbox"]["delivered_count"], 1)
        self.assertEqual(self.versions_of(share_id), 3)

    def test_the_same_prepare_key_never_makes_a_second_draft(self) -> None:
        """同一 key 的两种诚实结局：幂等回原条，或具名拒绝 —— 都不会有第二条草稿。

        这份夹具的时钟是冻结的，所以信封逐字节相同 → owner 按 key 幂等回原结果（同一个 artifact）。
        真实部署里时钟在走，同样的重放会得到 ``conflict`` + ``CAS_CONFLICT``
        （``operation key reused with different request``）；那一刻同样**只有一条草稿**、没有版本增长。
        """
        first = self.draft(operation_key="phone-prepare-key")
        second = self.sharing.prepare(source_artifact_id=self.source, share_text=SHARE_TEXT,
                                      title="留白重排", operation_key="phone-prepare-key")
        if second["ok"]:
            self.assertEqual(second["data"]["share_id"], first["data"]["share_id"])
        else:
            self.assertEqual(second["status"], "conflict")
            self.assertEqual(second["reason_code"], "CAS_CONFLICT")
        self.assertEqual(len(self.shares_on_owner()), 1)
        self.assertEqual(self.versions_of(first["data"]["share_id"]), 1)

    def test_a_second_send_is_refused_and_keeps_one_delivery(self) -> None:
        draft = self.draft()
        sent = self.sharing.act(share_id=draft["data"]["share_id"], action="send_local",
                               expected_head=draft["data"]["head_version_id"])
        self.assertTrue(sent["ok"], sent)
        receipt_id = sent["data"]["receipt"]["delivery_id"]
        versions = self.versions_of(draft["data"]["share_id"])

        again = self.sharing.act(share_id=draft["data"]["share_id"], action="send_local",
                                 expected_head=sent["data"]["head_version_id"])
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason_code"], "SHARE_ALREADY_DELIVERED")
        self.assertFalse(again["data"]["delivered"])
        self.assertEqual(self.versions_of(draft["data"]["share_id"]), versions)
        current = self.sharing.detail(draft["data"]["share_id"])
        self.assertEqual(current["data"]["receipt"]["delivery_id"], receipt_id)

    def test_a_source_that_moved_blocks_the_send(self) -> None:
        draft = self.draft()
        moved = self.store.commit_markdown(
            artifact_id=self.source, authorized_subject_id=SUBJECT,
            expected_head=self.store.read_head(self.source)["version_id"],
            content="改过的一页。", operation_key="phone-source-moved",
            grant_reservation_id=GRANT)
        self.assertEqual(moved.get("status"), "OK", moved)
        versions = self.versions_of(draft["data"]["share_id"])
        blocked = self.sharing.act(share_id=draft["data"]["share_id"], action="send_local",
                                   expected_head=draft["data"]["head_version_id"])
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["status"], "stale_source")
        self.assertEqual(blocked["reason_code"], "SOURCE_REVISION_CHANGED")
        self.assertEqual(self.versions_of(draft["data"]["share_id"]), versions)
        detail = self.sharing.detail(draft["data"]["share_id"])
        self.assertEqual(detail["data"]["share"]["state"], "DRAFT")
        self.assertEqual(detail["data"]["source_current"]["state"], "REVISION_CHANGED")

        # 重新准备一条（新的意图）就能发。
        fresh = self.draft(share_text="改过之后又读了一遍。", title="第二遍")
        sent = self.sharing.act(share_id=fresh["data"]["share_id"], action="send_local",
                               expected_head=fresh["data"]["head_version_id"])
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["data"]["state"], "LOCAL_DELIVERED")
        self.assertEqual(len(self.shares_on_owner()), 2)

    def test_an_archived_source_cannot_be_sent(self) -> None:
        draft = self.draft()
        archived = self.store.archive(
            self.source, authorized_subject_id=SUBJECT,
            expected_revision=int(self.store.read_artifact(self.source)["revision"]),
            operation_key="phone-source-archive", grant_reservation_id=GRANT)
        self.assertEqual(archived.get("status"), "OK", archived)
        blocked = self.sharing.act(share_id=draft["data"]["share_id"], action="send_local",
                                   expected_head=draft["data"]["head_version_id"])
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["status"], "stale_source")
        self.assertEqual(blocked["reason_code"], "SOURCE_ARCHIVED")
        # 归档不是删除：草稿与它记录的来源绑定都还在，正文也照实读得到。
        detail = self.sharing.detail(draft["data"]["share_id"])
        self.assertEqual(detail["data"]["share"]["state"], "DRAFT")
        self.assertTrue(detail["data"]["source_current"]["readable"])
        self.assertEqual(detail["data"]["share"]["text"], SHARE_TEXT)


if __name__ == "__main__":
    unittest.main()
