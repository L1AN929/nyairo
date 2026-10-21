#!/usr/bin/env python3
"""v0.4 阶段 B：分享服务跑在**真实 Supply ManagedArtifact owner** 上的用例。

证据分级（照实写）
------------------
* 这里的 owner 是真的：``components/supply/lifesupply/artifact/store.py::ManagedArtifactStore``，
  **真 sqlite 文件**、不可变版本（``artifact_versions`` 有 no-update/no-delete 触发器）、真实
  ``artifact_operations`` / ``artifact_effect_receipts`` 账本、真实 CAS（``expected_head`` /
  ``expected_revision``）。
* 中间只有一层测试运输替身（``SupplyStorePort``）：它把 store 的字典答复翻译成
  ``life_artifact_port.ArtifactOutcome``，语义与 ``life_artifact_port.LifeArtifactPort`` 一致。
  真实部署里这一层是 Unix socket + Governance 预留，本轮**没有**起 socket、**没有**走
  Governance（父级协议：真实生活链才走那两段）。
* ``WsAuthority`` 只是 ``workspace_authority`` 的测试替身（store 要求一个 workspace 权威）。
* 夹具根：``$NYAIRO_SHARE_FIXTURE_ROOT`` 或系统临时目录下的 ``nyairo-share-b``；**只创建不删除**，
  每个用例一个独立 sqlite 文件名（带进程与时间戳），重复运行也不会撞车。
* 不读凭据、不跑真实模型、不碰任何实例目录。store 需要 ``fcntl``，因此本文件在 POSIX
  （WSL 真实 owner）上运行，Windows 上整文件跳过；Windows 上的内存 owner 用例见
  ``tests/test_v04_sharing_service_stage.py``。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import life_sharing_service as lss  # noqa: E402
import private_project_service as pps  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                "ManagedArtifactStore imports fcntl (real owner runs on WSL)")

SUBJECT = "chiyo"
WORKSPACE = "ws-share-supply"
GRANT = "grant-share-supply"

# The store imports ``fcntl``: only import it where it can work.  On Windows the whole module is
# skipped anyway, but collection must not explode.
try:
    from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402
except Exception:  # pragma: no cover - Windows (no fcntl)
    ManagedArtifactStore = None  # type: ignore[assignment]

SOURCE_TEXT = "今天把那一页的留白重新排了一遍，第二段还是太挤。" * 4
SHARE_TEXT = "留白重排完了，第二段还是挤，但她觉得这样更像当天。"


class WsAuthority:
    """The store's ``workspace_authority`` test double: one ACTIVE workspace of this subject."""

    def get_workspace_by_id(self, workspace_id: str) -> Optional[dict[str, Any]]:
        if workspace_id == WORKSPACE:
            return {"subject_id": SUBJECT, "lifecycle": "ACTIVE"}
        return None
def _outcome(answer: dict[str, Any], *, operation_key: str) -> lap.ArtifactOutcome:
    """Translate a real store answer into the port's outcome (same mapping as the real port)."""
    status = str(answer.get("status") or "")
    states = {
        "OK": lap.EffectState.APPLIED,
        "NO_CHANGE": lap.EffectState.NO_CHANGE,
        "NOT_FOUND": lap.EffectState.NOT_FOUND,
        "CONFLICT": lap.EffectState.CONFLICT,
        "BLOCKED": lap.EffectState.REFUSED,
        "DENY": lap.EffectState.REFUSED,
    }
    return lap.ArtifactOutcome(
        effect_state=states.get(status, lap.EffectState.UNKNOWN),
        status=status or "UNKNOWN",
        reason=str(answer.get("reason") or ""),
        detail=str(answer.get("detail") or ""),
        artifact_id=answer.get("artifact_id"),
        version_id=answer.get("version_id"),
        version_number=answer.get("version_number"),
        head_version_id=answer.get("head_version_id"),
        parent_version_id=answer.get("parent_version_id"),
        operation_key=operation_key,
        lifecycle=answer.get("lifecycle"),
        revision=answer.get("revision"),
        raw=dict(answer),
    )


class SupplyStorePort:
    """The projects owner's port shape over the **real** ``ManagedArtifactStore``.

    Only transport differs from production (a direct store call instead of the Life Supply socket);
    every effect, CAS check, version and receipt is the owner's own.
    """

    def __init__(self, store: Any, *, subject_id: str = SUBJECT) -> None:
        self.store = store
        self.subject_id = subject_id

    # -- reads ------------------------------------------------------------------------
    def list_artifacts(self) -> list[dict[str, Any]]:
        rows = self.store.list_artifacts(self.subject_id)
        if rows is None:
            raise lap.LifeArtifactUnavailable("ARTIFACT_LIST_UNAVAILABLE")
        return [dict(row) for row in rows]

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        row = self.store.read_artifact(artifact_id)
        if row is None:
            raise lap.ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND",
                                           "no such artifact for this owner")
        if str(row.get("subject_id")) != self.subject_id:
            raise lap.ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND",
                                           "artifact belongs to another subject")
        head = self.store.read_head(artifact_id)
        return {"artifact": dict(row), "head": dict(head) if head else None,
                "content": None if head is None else head["content"]}

    # -- writes -----------------------------------------------------------------------
    def create_artifact(self, *, workspace_id: str, title: str, content: str, grant_id: str,
                        operation_key: str) -> lap.ArtifactOutcome:
        created = self.store.create_artifact(
            subject_id=self.subject_id, workspace_id=workspace_id, title=title,
            operation_key=operation_key, grant_reservation_id=grant_id)
        if str(created.get("status")) != "OK":
            return _outcome(created, operation_key=operation_key)
        artifact_id = str(created["artifact_id"])
        # Mirrors the owner's own create: the artifact exists, then its first version is
        # committed -- the port's "create with first text" pair.
        committed = self.store.commit_markdown(
            artifact_id=artifact_id, authorized_subject_id=self.subject_id, expected_head=None,
            content=content, operation_key="%s:v1" % operation_key,
            grant_reservation_id=grant_id)
        outcome = _outcome(committed, operation_key=operation_key)
        out = lap.ArtifactOutcome(
            effect_state=outcome.effect_state, status=outcome.status, reason=outcome.reason,
            detail=outcome.detail, artifact_id=artifact_id, version_id=outcome.version_id,
            version_number=outcome.version_number, parent_version_id=outcome.parent_version_id,
            operation_key=operation_key, revision=created.get("revision"), raw=dict(committed))
        return out

    def commit_version(self, *, artifact_id: str, expected_head: Optional[str], content: str,
                       grant_id: str, operation_key: str) -> lap.ArtifactOutcome:
        answer = self.store.commit_markdown(
            artifact_id=artifact_id, authorized_subject_id=self.subject_id,
            expected_head=expected_head, content=content, operation_key=operation_key,
            grant_reservation_id=grant_id)
        return _outcome(answer, operation_key=operation_key)

    def archive_artifact(self, *, artifact_id: str, expected_revision: int, grant_id: str,
                         operation_key: str) -> lap.ArtifactOutcome:
        answer = self.store.archive(
            artifact_id, authorized_subject_id=self.subject_id,
            expected_revision=expected_revision, operation_key=operation_key,
            grant_reservation_id=grant_id)
        return _outcome(answer, operation_key=operation_key)

    def resolve_operation(self, operation_key: str) -> lap.ArtifactOutcome:
        receipt = self.store.read_effect_receipt(operation_key)
        if isinstance(receipt, dict):
            return lap.ArtifactOutcome(effect_state=lap.EffectState.APPLIED, status="OK",
                                       reason="EFFECT_RECEIPT_FOUND",
                                       artifact_id=receipt.get("artifact_id"),
                                       version_id=receipt.get("version_id"),
                                       operation_key=operation_key, raw={"receipt": receipt})
        row = self.store.lookup_operation(operation_key)
        if isinstance(row, dict):
            return lap.ArtifactOutcome(effect_state=lap.EffectState.APPLIED, status="OK",
                                       reason="OPERATION_ROW_FOUND",
                                       operation_key=operation_key, raw={"operation": row})
        return lap.ArtifactOutcome(effect_state=lap.EffectState.NO_CHANGE, status="NOT_FOUND",
                                   reason="OPERATION_NEVER_LANDED", operation_key=operation_key)


class Clock:
    def __init__(self, start: float = 1760000000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now


def fixture_root() -> Path:
    override = os.environ.get("NYAIRO_SHARE_FIXTURE_ROOT", "").strip()
    base = Path(override) if override else Path(os.environ.get("TMPDIR", "/tmp")) / "nyairo-share-b"
    base.mkdir(parents=True, exist_ok=True)
    return base


@POSIX_ONLY
class SupplyOwnerSharingTest(unittest.TestCase):
    def setUp(self) -> None:
        if ManagedArtifactStore is None:  # pragma: no cover
            self.skipTest("ManagedArtifactStore unavailable (needs fcntl)")
        stamp = "%s-%d-%d" % (self._testMethodName.replace("test_", ""), os.getpid(),
                              int(time.time() * 1000))
        self.db_path = str(fixture_root() / ("%s.sqlite" % stamp))
        self.clock = Clock()
        self.store = self._open_store()
        self.port = SupplyStorePort(self.store)
        self.projects = pps.PrivateProjectService(self.port, WORKSPACE, GRANT)
        self.service = lss.LifeSharingService(self.projects, clock=self.clock)
        self.source = self._seed_source()

    # -- fixtures ---------------------------------------------------------------------
    def _open_store(self) -> Any:
        return ManagedArtifactStore(self.db_path, workspace_authority=WsAuthority(),
                                    clock=self.clock)

    def _seed_source(self) -> str:
        created = self.store.create_artifact(
            subject_id=SUBJECT, workspace_id=WORKSPACE, title="她的留白练习",
            operation_key="seed-source-create", grant_reservation_id=GRANT)
        self.assertEqual(created.get("status"), "OK", created)
        artifact_id = str(created["artifact_id"])
        committed = self.store.commit_markdown(
            artifact_id=artifact_id, authorized_subject_id=SUBJECT, expected_head=None,
            content=SOURCE_TEXT, operation_key="seed-source-v1", grant_reservation_id=GRANT)
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

    def draft(self, **kwargs: Any) -> dict[str, Any]:
        defaults = dict(source_artifact_id=self.source, share_text=SHARE_TEXT,
                        title="留白重排")
        defaults.update(kwargs)
        answer = self.service.prepare_share(**defaults)
        self.assertTrue(answer["ok"], answer)
        return answer

    # -- tests ------------------------------------------------------------------------
    def test_draft_defer_and_local_delivery_on_the_real_owner(self) -> None:
        draft = self.draft()
        share_id = draft["share_id"]
        # The draft is a real artifact with a real immutable first version.
        self.assertEqual(self.versions_of(share_id), 1)
        self.assertEqual(self.store.read_artifact(share_id)["title"],
                         lss.TITLE_PREFIX + "留白重排")

        deferred = self.service.apply_action(share_id=share_id, action=lss.ACTION_DEFER,
                                            expected_head=draft["data"]["head_version_id"],
                                            opportunity_ref="evening-quiet")
        self.assertTrue(deferred["ok"], deferred)
        self.assertEqual(deferred["data"]["state"], lss.STATE_DEFERRED)
        sent = self.service.apply_action(share_id=share_id, action=lss.ACTION_SEND_LOCAL,
                                        expected_head=deferred["data"]["head_version_id"])
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["data"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertEqual(sent["data"]["delivery_count"], 1)
        self.assertEqual(self.versions_of(share_id), 3)
        receipt = sent["data"]["receipt"]
        self.assertEqual(receipt["channel"], lss.CHANNEL_LOCAL_INBOX)
        self.assertEqual(receipt["audience"], lss.AUDIENCE_LOCAL_OWNER)
        self.assertTrue(receipt["local_only"])
        self.assertFalse(receipt["read_by_audience"])
        # The owner's own operation ledger holds this send key -- evidence, not a fake counter.
        row = self.store.lookup_operation(sent["operation_key"])
        self.assertIsInstance(row, dict)
        self.assertEqual(row["artifact_id"], share_id)
        self.assertEqual(row["operation_kind"], "COMMIT_MARKDOWN")

    def test_the_receipt_survives_a_real_restart(self) -> None:
        draft = self.draft()
        sent = self.service.apply_action(share_id=draft["share_id"],
                                        action=lss.ACTION_SEND_LOCAL,
                                        expected_head=draft["data"]["head_version_id"])
        receipt = sent["data"]["receipt"]

        # Real restart: new store object on the same sqlite file, new projects owner, new service.
        self.store = self._open_store()
        self.port = SupplyStorePort(self.store)
        self.projects = pps.PrivateProjectService(self.port, WORKSPACE, GRANT)
        self.service = lss.LifeSharingService(self.projects, clock=self.clock)

        read = self.service.read_share(draft["share_id"])
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["data"]["share"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertEqual(read["data"]["receipt"], receipt)
        self.assertEqual(read["data"]["share"]["text"], SHARE_TEXT)
        listing = self.service.list_shares()
        self.assertEqual(listing["data"]["counts"]["local_delivered"], 1)
        self.assertEqual(listing["data"]["inbox"]["delivered_count"], 1)
        self.assertEqual(self.versions_of(draft["share_id"]), 2)

    def test_cas_conflict_on_the_real_owner_writes_nothing(self) -> None:
        draft = self.draft()
        first_head = draft["data"]["head_version_id"]
        self.service.apply_action(share_id=draft["share_id"], action=lss.ACTION_DEFER,
                                  expected_head=first_head, opportunity_ref="later")
        conflict = self.service.apply_action(share_id=draft["share_id"],
                                             action=lss.ACTION_SEND_LOCAL,
                                             expected_head=first_head)
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["status"], lss.STATUS_CONFLICT)
        self.assertEqual(conflict["data"]["current_head_version_id"],
                         self.store.read_artifact(draft["share_id"])["head_version_id"])
        self.assertEqual(self.versions_of(draft["share_id"]), 2)
        self.assertFalse(self.service.read_share(draft["share_id"])["data"]["delivered"])

    def test_a_second_send_is_refused_and_adds_no_version(self) -> None:
        draft = self.draft()
        sent = self.service.apply_action(share_id=draft["share_id"],
                                        action=lss.ACTION_SEND_LOCAL,
                                        expected_head=draft["data"]["head_version_id"])
        versions = self.versions_of(draft["share_id"])
        again = self.service.apply_action(share_id=draft["share_id"],
                                          action=lss.ACTION_SEND_LOCAL,
                                          expected_head=sent["data"]["head_version_id"])
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason_code"], lss.REASON_ALREADY_DELIVERED)
        self.assertEqual(again["data"]["receipt"]["delivery_id"],
                         sent["data"]["receipt"]["delivery_id"])
        self.assertEqual(self.versions_of(draft["share_id"]), versions)

    def test_the_source_moving_on_the_real_owner_blocks_the_send(self) -> None:
        draft = self.draft()
        committed = self.store.commit_markdown(
            artifact_id=self.source, authorized_subject_id=SUBJECT,
            expected_head=self.store.read_artifact(self.source)["head_version_id"],
            content=SOURCE_TEXT + "\n后来又补了一句。", operation_key="source-next",
            grant_reservation_id=GRANT)
        self.assertEqual(committed.get("status"), "OK", committed)
        stale = self.service.apply_action(share_id=draft["share_id"],
                                          action=lss.ACTION_SEND_LOCAL,
                                          expected_head=draft["data"]["head_version_id"])
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], lss.STATUS_STALE_SOURCE)
        self.assertEqual(stale["reason_code"], lss.REASON_SOURCE_REVISION_CHANGED)
        self.assertEqual(self.versions_of(draft["share_id"]), 1)

    def test_dropped_on_the_real_owner_is_terminal(self) -> None:
        draft = self.draft()
        dropped = self.service.apply_action(share_id=draft["share_id"],
                                           action=lss.ACTION_DROP,
                                           expected_head=draft["data"]["head_version_id"],
                                           reason="不说了")
        self.assertEqual(dropped["data"]["state"], lss.STATE_DROPPED)
        versions = self.versions_of(draft["share_id"])
        send = self.service.apply_action(share_id=draft["share_id"],
                                        action=lss.ACTION_SEND_LOCAL,
                                        expected_head=dropped["data"]["head_version_id"])
        self.assertEqual(send["reason_code"], lss.REASON_TERMINAL_STATE)
        self.assertEqual(self.versions_of(draft["share_id"]), versions)
        self.assertFalse(self.service.read_share(draft["share_id"])["data"]["delivered"])

    def test_a_replayed_prepare_key_makes_exactly_one_artifact(self) -> None:
        first = self.service.prepare_share(source_artifact_id=self.source, share_text=SHARE_TEXT,
                                           title="只此一件", operation_key="share:fixed-prepare")
        second = self.service.prepare_share(source_artifact_id=self.source, share_text=SHARE_TEXT,
                                            title="只此一件", operation_key="share:fixed-prepare")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["share_id"], second["share_id"])
        shares = [row for row in self.store.list_artifacts(SUBJECT)
                  if str(row.get("title") or "").startswith(lss.TITLE_PREFIX)]
        self.assertEqual(len(shares), 1)
        self.assertEqual(self.versions_of(first["share_id"]), 1)

    def test_no_draft_is_created_without_explicit_share_text(self) -> None:
        before = len(self.store.list_artifacts(SUBJECT))
        answer = self.service.prepare_share(source_artifact_id=self.source)
        self.assertEqual(answer["reason_code"], lss.REASON_SHARE_TEXT_REQUIRED)
        self.assertEqual(len(self.store.list_artifacts(SUBJECT)), before)


if __name__ == "__main__":
    unittest.main()
