#!/usr/bin/env python3
"""v0.4 阶段 B：分享生命周期与本地投递（只测本轮新增的这一层）。

这个文件测什么
--------------
``components/life/scripts/life_sharing_service.py`` 把「她想分享的一小段」当成**现有
ManagedArtifact owner 里的一件 artifact**：草稿、来源绑定、受众、渠道、投递记录全部编码在这件
artifact 的 head 正文里，每次状态变化是一次 owner 版本（CAS + operation_key 幂等）。

证据分级（照实写）
------------------
* ``FakeOwnerPort`` 是**内存里的 owner 替身**，只按 ``private_project_service`` 允许依赖的冻结语义
  回答：head 上的 CAS、operation_key 幂等、``NO_CHANGE``、ARCHIVED，以及「答复丢了、要先
  ``resolve_operation`` 对账」的 UNKNOWN。**服务本身是真的**（真的 ``PrivateProjectService`` +
  真的 ``LifeSharingService``），替身只在最底下那一层。
* 真实 ``ManagedArtifactStore``（真 sqlite、不可变版本、真实 operation/receipt 账本）在
  ``tests/test_v04_sharing_supply_owner.py``。
* 不读凭据、不跑真实模型、不碰任何实例目录、不建立 socket、不删除任何文件。

覆盖：必须有明确分享文本/明确有界长度、来源换版就不发、受众与渠道只能是固定本地身份、
重复意图不产生第二份草稿、DEFERRED 不按 tick 改版本、DROPPED/EXPIRED 不复活、重复发送不产生
第二条投递、CAS 陈旧是 conflict、UNKNOWN 先对账（落地/没落地两路）、本地投递记录重启后仍在、
列表不带正文、非信封 artifact 不被猜成分享。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import life_sharing_service as lss  # noqa: E402
import private_project_service as pps  # noqa: E402

SUBJECT = "chiyo"
WORKSPACE = "ws-sharing-b"
GRANT = "grant-sharing-b"

SOURCE_TEXT = "第一页写了一半，留白比字更难看懂。" * 6


def _outcome(state: str, **kwargs: Any) -> lap.ArtifactOutcome:
    base = dict(effect_state=state, status="OK")
    base.update(kwargs)
    return lap.ArtifactOutcome(**base)


class FakeOwnerPort:
    """In-memory owner with exactly the semantics the projects owner is allowed to rely on."""

    def __init__(self) -> None:
        self.subject_id = SUBJECT
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._serial = 0
        self.conflict_next = False
        self.unknown_next = False
        self.land_before_unknown = False

    # -- helpers ---------------------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._serial += 1
        return "%s%032x" % (prefix, self._serial)

    def seed(self, *, artifact_id: str, title: str, content: str,
             lifecycle: str = "ACTIVE") -> str:
        version_id = "version-%s-1" % artifact_id.split(":", 1)[-1]
        self.artifacts[artifact_id] = {
            "artifact_id": artifact_id, "subject_id": SUBJECT, "workspace_id": WORKSPACE,
            "title": title, "lifecycle": lifecycle, "head_version_id": version_id,
            "created_at": 1760000000.0, "updated_at": 1760000100.0, "revision": 1,
        }
        self.versions[artifact_id] = [{
            "version_id": version_id, "version_number": 1, "parent_version_id": None,
            "content": content, "created_at": 1760000000.0,
            "operation_key": "seed:%s" % artifact_id,
        }]
        return version_id

    def _head(self, artifact_id: str) -> Optional[dict[str, Any]]:
        row = self.artifacts.get(artifact_id)
        if not row or not row.get("head_version_id"):
            return None
        for version in self.versions.get(artifact_id, []):
            if version["version_id"] == row["head_version_id"]:
                return version
        return None

    def head_version_id(self, artifact_id: str) -> Optional[str]:
        row = self.artifacts.get(artifact_id) or {}
        return row.get("head_version_id")

    def version_count(self, artifact_id: str) -> int:
        return len(self.versions.get(artifact_id, []))

    def write_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls
                if call[0] in ("create_artifact", "commit_version", "archive_artifact")]

    def share_artifacts(self) -> list[str]:
        return sorted(aid for aid, row in self.artifacts.items()
                      if str(row.get("title") or "").startswith(lss.TITLE_PREFIX))

    # -- port surface ----------------------------------------------------------------
    def list_artifacts(self) -> list[dict[str, Any]]:
        self.calls.append(("list_artifacts", {}))
        return [dict(row) for row in self.artifacts.values()]

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        self.calls.append(("read_artifact", {"artifact_id": artifact_id}))
        row = self.artifacts.get(artifact_id)
        if row is None:
            raise lap.ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "no such artifact")
        head = self._head(artifact_id)
        return {"artifact": dict(row), "head": dict(head) if head else None,
                "content": None if head is None else head["content"]}

    def create_artifact(self, *, workspace_id: str, title: str, content: str, grant_id: str,
                        operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("create_artifact", {"title": title, "content": content,
                                               "operation_key": operation_key}))
        if operation_key in self.operations:
            replay = self.operations[operation_key]
            return _outcome(lap.EffectState.APPLIED, artifact_id=replay["artifact_id"],
                            version_id=replay["version_id"], version_number=1)
        if self.unknown_next and not self.land_before_unknown:
            self.unknown_next = False
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        artifact_id = self._next_id("artifact:")
        version_id = self.seed(artifact_id=artifact_id, title=title, content=content)
        self.operations[operation_key] = {"artifact_id": artifact_id, "version_id": version_id,
                                          "version_number": 1}
        if self.unknown_next:
            self.unknown_next = False
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id, version_id=version_id,
                        version_number=1)

    def commit_version(self, *, artifact_id: str, expected_head: Optional[str], content: str,
                       grant_id: str, operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("commit_version", {"artifact_id": artifact_id,
                                              "expected_head": expected_head,
                                              "operation_key": operation_key}))
        row = self.artifacts.get(artifact_id)
        if row is None:
            return _outcome(lap.EffectState.NOT_FOUND, status="NOT_FOUND",
                            reason="ARTIFACT_NOT_FOUND")
        # Owner's frozen rule: an operation replay/conflict is decided before any CAS check.
        if operation_key in self.operations:
            replay = self.operations[operation_key]
            return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id,
                            version_id=replay["version_id"],
                            version_number=replay["version_number"])
        if self.conflict_next or str(expected_head) != str(row.get("head_version_id")):
            self.conflict_next = False
            return _outcome(lap.EffectState.CONFLICT, status="CONFLICT",
                            detail="stale expected_head")
        if self.unknown_next and not self.land_before_unknown:
            self.unknown_next = False
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        head = self._head(artifact_id)
        if head is not None and head["content"] == content:
            return _outcome(lap.EffectState.NO_CHANGE, status="NO_CHANGE", artifact_id=artifact_id,
                            version_created=False)
        versions = self.versions[artifact_id]
        number = len(versions) + 1
        version_id = "version-%s-%d" % (artifact_id.split(":", 1)[-1], number)
        versions.append({"version_id": version_id, "version_number": number,
                         "parent_version_id": row.get("head_version_id"), "content": content,
                         "created_at": 1760000200.0 + number, "operation_key": operation_key})
        row["head_version_id"] = version_id
        row["revision"] = int(row.get("revision") or 1) + 1
        self.operations[operation_key] = {"artifact_id": artifact_id, "version_id": version_id,
                                          "version_number": number}
        if self.unknown_next:
            self.unknown_next = False
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id, version_id=version_id,
                        version_number=number, parent_version_id=versions[-2]["version_id"])

    def archive_artifact(self, *, artifact_id: str, expected_revision: int, grant_id: str,
                         operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("archive_artifact", {"artifact_id": artifact_id,
                                                "operation_key": operation_key}))
        row = self.artifacts.get(artifact_id)
        if row is None:
            return _outcome(lap.EffectState.NOT_FOUND, status="NOT_FOUND")
        if row.get("lifecycle") == "ARCHIVED":
            return _outcome(lap.EffectState.NO_CHANGE, status="NO_CHANGE")
        if int(expected_revision) != int(row.get("revision") or 0):
            return _outcome(lap.EffectState.CONFLICT, status="CONFLICT")
        row["lifecycle"] = "ARCHIVED"
        row["revision"] = int(row.get("revision") or 0) + 1
        return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id, lifecycle="ARCHIVED",
                        revision=row["revision"])

    def resolve_operation(self, operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("resolve_operation", {"operation_key": operation_key}))
        row = self.operations.get(operation_key)
        if row is None:
            # The owner positively knows nothing landed: a retry with the same key is safe.
            return _outcome(lap.EffectState.NO_CHANGE, status="NOT_FOUND",
                            reason="OPERATION_NEVER_LANDED")
        return _outcome(lap.EffectState.APPLIED, artifact_id=row["artifact_id"],
                        version_id=row["version_id"])


class Clock:
    """A controllable clock (the service takes ``clock=`` exactly for this)."""

    def __init__(self, start: float = 1760000000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class SharingTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.port = FakeOwnerPort()
        self.source = "artifact:source-work"
        self.port.seed(artifact_id=self.source, title="她的留白练习", content=SOURCE_TEXT)
        self.other = "artifact:other-work"
        self.port.seed(artifact_id=self.other, title="她自己的日常记录", content="旧的一页。")
        self.clock = Clock()
        self.projects = self._projects()
        self.service = lss.LifeSharingService(self.projects, clock=self.clock)

    def _projects(self) -> pps.PrivateProjectService:
        return pps.PrivateProjectService(self.port, WORKSPACE, GRANT)

    def restart(self) -> lss.LifeSharingService:
        """A brand-new service over the same owner (what a process restart looks like here)."""
        self.projects = self._projects()
        self.service = lss.LifeSharingService(self.projects, clock=self.clock)
        return self.service

    def draft(self, **kwargs: Any) -> dict[str, Any]:
        defaults = dict(source_artifact_id=self.source, share_text="今天把那页留白读完了。",
                        title="今天的留白")
        defaults.update(kwargs)
        answer = self.service.prepare_share(**defaults)
        self.assertTrue(answer["ok"], answer)
        return answer


class PrepareShareTest(SharingTestBase):
    def test_share_text_is_required_and_nothing_is_written_without_it(self) -> None:
        before = len(self.port.write_calls())
        answer = self.service.prepare_share(source_artifact_id=self.source)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], lss.STATUS_REJECTED)
        self.assertEqual(answer["reason_code"], lss.REASON_SHARE_TEXT_REQUIRED)
        self.assertEqual(len(self.port.write_calls()), before)
        self.assertEqual(self.port.share_artifacts(), [])
        self.assertNotIn(SOURCE_TEXT, str(answer))

    def test_draft_binds_the_real_source_version_and_keeps_private_state(self) -> None:
        answer = self.draft()
        self.assertEqual(answer["status"], lss.STATUS_APPLIED)
        self.assertEqual(answer["state"], lss.STATE_DRAFT)
        data = answer["data"]
        self.assertEqual(data["source"]["artifact_id"], self.source)
        self.assertEqual(data["source"]["version_id"], self.port.head_version_id(self.source))
        self.assertEqual(data["source"]["content_chars"], len(SOURCE_TEXT))
        self.assertEqual(data["audience"], lss.AUDIENCE_LOCAL_OWNER)
        self.assertEqual(data["channel"], lss.CHANNEL_LOCAL_INBOX)
        self.assertEqual(data["share_revision"], 1)
        self.assertEqual(data["text"], "今天把那页留白读完了。")
        # A draft is not "sent": the artifact exists, DRAFT, and nothing was delivered.
        self.assertEqual(answer["share_id"], self.port.share_artifacts()[0])
        read = self.service.read_share(answer["share_id"])
        self.assertEqual(read["data"]["share"]["state"], lss.STATE_DRAFT)
        self.assertFalse(read["data"]["delivered"])
        self.assertIsNone(read["data"]["receipt"])

    def test_excerpt_must_be_explicitly_bounded_and_never_the_whole_material(self) -> None:
        bound = 12
        answer = self.draft(share_text=None, excerpt_chars=bound)
        self.assertEqual(answer["data"]["text"], SOURCE_TEXT[:bound])
        self.assertEqual(answer["data"]["excerpt"]["kind"], "SOURCE_PREFIX")
        self.assertLess(len(answer["data"]["text"]), len(SOURCE_TEXT))
        with self.assertRaises(lss.SharingContractRefused):
            self.service.prepare_share(source_artifact_id=self.source,
                                       excerpt_chars=lss.MAX_EXCERPT_CHARS + 1)
        with self.assertRaises(lss.SharingContractRefused):
            self.service.prepare_share(source_artifact_id=self.source,
                                       share_text="x", excerpt_chars=3)

    def test_unreadable_or_archived_source_never_leaks_and_never_drafts(self) -> None:
        missing = self.service.prepare_share(source_artifact_id="artifact:nope",
                                             share_text="一段想给她看的话")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason_code"], lss.REASON_SOURCE_NOT_FOUND)
        self.assertNotIn(SOURCE_TEXT, str(missing))

        self.port.artifacts[self.other]["lifecycle"] = "ARCHIVED"
        archived = self.service.prepare_share(source_artifact_id=self.other,
                                              share_text="一段想给她看的话")
        self.assertFalse(archived["ok"])
        self.assertEqual(archived["reason_code"], lss.REASON_SOURCE_ARCHIVED)
        self.assertEqual(self.port.share_artifacts(), [])

    def test_free_form_audience_and_channel_are_refused(self) -> None:
        before = len(self.port.write_calls())
        mail = self.service.prepare_share(source_artifact_id=self.source, share_text="给她看",
                                          audience="mailto:someone@example.com")
        self.assertEqual(mail["reason_code"], lss.REASON_AUDIENCE_REFUSED)
        telegram = self.service.prepare_share(source_artifact_id=self.source, share_text="给她看",
                                              channel="telegram")
        self.assertEqual(telegram["reason_code"], lss.REASON_CHANNEL_REFUSED)
        self.assertEqual(len(self.port.write_calls()), before)
        self.assertEqual(self.port.share_artifacts(), [])

    def test_the_same_intent_never_creates_a_second_draft(self) -> None:
        first = self.draft()
        second = self.draft()
        self.assertEqual(first["share_id"], second["share_id"])
        self.assertEqual(len(self.port.share_artifacts()), 1)

    def test_a_plane_artifact_is_never_guessed_into_a_share(self) -> None:
        # A normal work item that merely has the machine title prefix but no envelope.
        plain = "artifact:not-a-share"
        self.port.seed(artifact_id=plain, title=lss.TITLE_PREFIX + "旧东西", content="真正的一页")
        answer = self.service.read_share(plain)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], lss.REASON_NOT_A_SHARE)
        self.assertNotIn("真正的一页", str(answer))
        listing = self.service.list_shares()
        self.assertEqual(listing["data"]["shares"], [])
        self.assertEqual([entry["artifact_id"] for entry in listing["data"]["ignored"]], [plain])


class ShareLifecycleTest(SharingTestBase):
    def test_defer_needs_an_explicit_opportunity_and_does_not_churn_on_reads(self) -> None:
        draft = self.draft()
        head = draft["data"]["head_version_id"]
        blocked = self.service.apply_action(share_id=draft["share_id"], action=lss.ACTION_DEFER,
                                            expected_head=head)
        self.assertEqual(blocked["reason_code"], lss.REASON_DEFER_CONDITION_REQUIRED)
        self.assertEqual(self.port.head_version_id(draft["share_id"]), head)

        deferred = self.service.apply_action(
            share_id=draft["share_id"], action=lss.ACTION_DEFER, expected_head=head,
            opportunity_ref="evening-quiet-when-she-asks")
        self.assertTrue(deferred["ok"], deferred)
        self.assertEqual(deferred["data"]["state"], lss.STATE_DEFERRED)
        self.assertEqual(deferred["data"]["deferred"]["opportunity_ref"],
                         "evening-quiet-when-she-asks")
        head_after = self.port.head_version_id(draft["share_id"])
        writes = len(self.port.write_calls())
        # Polling is free: no new version, no new write, no model call -- just facts.
        for _ in range(5):
            read = self.service.read_share(draft["share_id"])
            self.assertEqual(read["data"]["share"]["state"], lss.STATE_DEFERRED)
            self.service.list_shares()
        self.assertEqual(self.port.head_version_id(draft["share_id"]), head_after)
        self.assertEqual(len(self.port.write_calls()), writes)

    def test_dropped_share_is_terminal_and_never_revives(self) -> None:
        draft = self.draft()
        head = draft["data"]["head_version_id"]
        dropped = self.service.apply_action(share_id=draft["share_id"], action=lss.ACTION_DROP,
                                           expected_head=head, reason="不想说了")
        self.assertEqual(dropped["data"]["state"], lss.STATE_DROPPED)
        self.assertEqual(dropped["data"]["dropped"]["reason"], "不想说了")
        head_after = self.port.head_version_id(draft["share_id"])
        versions = self.port.version_count(draft["share_id"])

        send = self.service.apply_action(share_id=draft["share_id"],
                                         action=lss.ACTION_SEND_LOCAL,
                                         expected_head=head_after)
        self.assertFalse(send["ok"])
        self.assertEqual(send["reason_code"], lss.REASON_TERMINAL_STATE)
        defer = self.service.apply_action(share_id=draft["share_id"], action=lss.ACTION_DEFER,
                                          expected_head=head_after, opportunity_ref="later")
        self.assertEqual(defer["reason_code"], lss.REASON_TERMINAL_STATE)
        # The old timer cannot revive it: no version, no delivery.
        self.assertEqual(self.port.head_version_id(draft["share_id"]), head_after)
        self.assertEqual(self.port.version_count(draft["share_id"]), versions)
        self.assertFalse(self.service.read_share(draft["share_id"])["data"]["delivered"])

    def test_stale_head_is_a_conflict_and_writes_nothing(self) -> None:
        draft = self.draft()
        first_head = draft["data"]["head_version_id"]
        self.service.apply_action(share_id=draft["share_id"], action=lss.ACTION_DEFER,
                                  expected_head=first_head, opportunity_ref="later")
        conflict = self.service.apply_action(share_id=draft["share_id"],
                                             action=lss.ACTION_SEND_LOCAL,
                                             expected_head=first_head)
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["status"], lss.STATUS_CONFLICT)
        self.assertEqual(conflict["reason_code"], lss.REASON_CAS_CONFLICT)
        self.assertEqual(conflict["data"]["current_head_version_id"],
                         self.port.head_version_id(draft["share_id"]))
        self.assertEqual(self.service.read_share(draft["share_id"])["data"]["share"]["state"],
                         lss.STATE_DEFERRED)
        self.assertIsNone(self.service.read_share(draft["share_id"])["data"]["receipt"])

    def test_a_moved_source_blocks_the_send_until_the_share_is_reprepared(self) -> None:
        draft = self.draft()
        head = draft["data"]["head_version_id"]
        # The subject keeps working: the source gets a new version (owner CAS, real commit).
        self.projects.commit_project(artifact_id=self.source,
                                     content=SOURCE_TEXT + "\n后来又补了一句。",
                                     expected_head=self.port.head_version_id(self.source),
                                     operation_key="source-next")
        stale = self.service.apply_action(share_id=draft["share_id"],
                                          action=lss.ACTION_SEND_LOCAL, expected_head=head)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], lss.STATUS_STALE_SOURCE)
        self.assertEqual(stale["reason_code"], lss.REASON_SOURCE_REVISION_CHANGED)
        self.assertEqual(self.port.head_version_id(draft["share_id"]), head)
        self.assertFalse(self.service.read_share(draft["share_id"])["data"]["delivered"])

    def test_an_archived_source_blocks_the_send(self) -> None:
        draft = self.draft()
        self.port.artifacts[self.source]["lifecycle"] = "ARCHIVED"
        stale = self.service.apply_action(share_id=draft["share_id"],
                                          action=lss.ACTION_SEND_LOCAL,
                                          expected_head=draft["data"]["head_version_id"])
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], lss.STATUS_STALE_SOURCE)
        self.assertEqual(stale["reason_code"], lss.REASON_SOURCE_ARCHIVED)

    def test_unknown_result_is_reconciled_before_any_retry(self) -> None:
        draft = self.draft()
        head = draft["data"]["head_version_id"]
        self.port.unknown_next = True  # the answer is lost and nothing landed
        lost = self.service.apply_action(share_id=draft["share_id"],
                                         action=lss.ACTION_SEND_LOCAL, expected_head=head)
        self.assertFalse(lost["ok"])
        self.assertEqual(lost["status"], lss.STATUS_UNKNOWN)
        self.assertTrue(lost["retryable"])
        self.assertEqual(lost["reason_code"], lss.REASON_OPERATION_NEVER_LANDED)
        self.assertEqual(self.port.head_version_id(draft["share_id"]), head)
        self.assertFalse(self.service.read_share(draft["share_id"])["data"]["delivered"])

        # The caller retries with the very same operation key: one delivery, not two.
        retried = self.service.apply_action(share_id=draft["share_id"],
                                            action=lss.ACTION_SEND_LOCAL, expected_head=head,
                                            operation_key=lost["operation_key"])
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["data"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertEqual(retried["data"]["delivery_count"], 1)

    def test_unknown_that_actually_landed_is_reported_once(self) -> None:
        draft = self.draft()
        head = draft["data"]["head_version_id"]
        self.port.unknown_next = True
        self.port.land_before_unknown = True
        lost = self.service.apply_action(share_id=draft["share_id"],
                                         action=lss.ACTION_SEND_LOCAL, expected_head=head)
        self.assertTrue(lost["ok"], lost)
        self.assertTrue(lost["reconciled"])
        self.assertEqual(lost["data"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertEqual(lost["data"]["delivery_count"], 1)
        self.assertEqual(self.port.version_count(draft["share_id"]), 2)


class LocalDeliveryTest(SharingTestBase):
    def test_local_delivery_writes_one_receipt_and_a_second_send_is_refused(self) -> None:
        draft = self.draft()
        head = draft["data"]["head_version_id"]
        sent = self.service.apply_action(share_id=draft["share_id"],
                                         action=lss.ACTION_SEND_LOCAL, expected_head=head)
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["data"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertEqual(sent["data"]["previous_state"], lss.STATE_DRAFT)
        receipt = sent["data"]["receipt"]
        self.assertTrue(receipt["local_only"])
        self.assertFalse(receipt["external_channel_sent"])
        self.assertFalse(receipt["external_delivery_confirmed"])
        self.assertFalse(receipt["read_by_audience"])
        self.assertFalse(receipt["acknowledged"])
        self.assertEqual(receipt["channel"], lss.CHANNEL_LOCAL_INBOX)
        self.assertEqual(receipt["audience"], lss.AUDIENCE_LOCAL_OWNER)
        self.assertEqual(sent["data"]["delivery_count"], 1)

        head_after = sent["data"]["head_version_id"]
        versions = self.port.version_count(draft["share_id"])
        again = self.service.apply_action(share_id=draft["share_id"],
                                          action=lss.ACTION_SEND_LOCAL, expected_head=head_after)
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason_code"], lss.REASON_ALREADY_DELIVERED)
        self.assertEqual(again["data"]["receipt"]["delivery_id"], receipt["delivery_id"])
        self.assertEqual(self.port.version_count(draft["share_id"]), versions)

    def test_repeating_a_send_never_makes_a_second_delivery(self) -> None:
        draft = self.draft()
        head = draft["data"]["head_version_id"]
        first = self.service.apply_action(share_id=draft["share_id"],
                                          action=lss.ACTION_SEND_LOCAL, expected_head=head,
                                          operation_key="share:explicit-send-key")
        self.assertTrue(first["ok"], first)
        # The same intent, replayed by the same key and the same draft version: the owner's key
        # idempotency and this service's state gate both refuse a second delivery.
        replay = self.service.apply_action(share_id=draft["share_id"],
                                           action=lss.ACTION_SEND_LOCAL, expected_head=head,
                                           operation_key="share:explicit-send-key")
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["reason_code"], lss.REASON_ALREADY_DELIVERED)
        self.assertEqual(replay["data"]["receipt"]["delivery_id"],
                         first["data"]["receipt"]["delivery_id"])
        self.assertEqual(self.port.version_count(draft["share_id"]), 2)
        read = self.service.read_share(draft["share_id"])
        self.assertEqual(read["data"]["receipt"]["delivery_id"],
                         first["data"]["receipt"]["delivery_id"])
        self.assertEqual(read["data"]["receipt"]["delivered_at"],
                         first["data"]["receipt"]["delivered_at"])

    def test_the_inbox_record_survives_a_restart(self) -> None:
        draft = self.draft()
        sent = self.service.apply_action(share_id=draft["share_id"],
                                         action=lss.ACTION_SEND_LOCAL,
                                         expected_head=draft["data"]["head_version_id"])
        receipt = sent["data"]["receipt"]
        service = self.restart()
        read = service.read_share(draft["share_id"])
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["data"]["share"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertTrue(read["data"]["delivered"])
        self.assertEqual(read["data"]["receipt"], receipt)
        listing = service.list_shares()
        self.assertEqual(listing["data"]["counts"]["local_delivered"], 1)
        self.assertEqual(listing["data"]["inbox"]["delivered_count"], 1)
        self.assertTrue(listing["data"]["inbox"]["local_only"])
        # The inbox text is the bounded share text, not the whole private material.
        self.assertEqual(read["data"]["share"]["text"], "今天把那页留白读完了。")

    def test_list_withholds_text_and_reports_states(self) -> None:
        deferred = self.draft(title="延后的一件")
        self.service.apply_action(share_id=deferred["share_id"], action=lss.ACTION_DEFER,
                                  expected_head=deferred["data"]["head_version_id"],
                                  opportunity_ref="later")
        dropped = self.draft(title="放弃的一件")
        self.service.apply_action(share_id=dropped["share_id"], action=lss.ACTION_DROP,
                                  expected_head=dropped["data"]["head_version_id"])
        awaited = self.draft(title="准备发的一件")

        listing = self.service.list_shares()
        self.assertFalse(listing["data"]["private_text_included"])
        for entry in listing["data"]["shares"]:
            self.assertNotIn("text", entry)
        self.assertEqual(listing["data"]["counts"]["total"], 3)
        self.assertEqual(listing["data"]["counts"]["deferred"], 1)
        self.assertEqual(listing["data"]["counts"]["dropped"], 1)
        self.assertEqual(listing["data"]["counts"]["draft"], 1)

        only = self.service.list_shares(states=[lss.STATE_DROPPED])
        self.assertEqual([entry["share_id"] for entry in only["data"]["shares"]],
                         [dropped["share_id"]])
        with self.assertRaises(lss.SharingContractRefused):
            self.service.list_shares(states=["SENT"])

        with_text = self.service.list_shares(include_text=True)
        texts = {entry["share_id"]: entry["text"] for entry in with_text["data"]["shares"]}
        self.assertEqual(texts[awaited["share_id"]], "今天把那页留白读完了。")

    def test_expiry_window_is_derived_from_the_clock_then_written_by_explicit_action(self) -> None:
        draft = self.draft(expires_after_s=60)
        self.assertIsNotNone(draft["data"]["expires_at"])
        head = draft["data"]["head_version_id"]

        self.clock.advance(61)
        derived = self.service.read_share(draft["share_id"])
        self.assertEqual(derived["data"]["share"]["state"], lss.STATE_EXPIRED)
        self.assertTrue(derived["data"]["share"]["state_derived"])
        refused = self.service.apply_action(share_id=draft["share_id"],
                                            action=lss.ACTION_SEND_LOCAL, expected_head=head)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], lss.REASON_EXPIRED)
        self.assertFalse(self.service.read_share(draft["share_id"])["data"]["delivered"])

        written = self.service.apply_action(share_id=draft["share_id"],
                                            action=lss.ACTION_EXPIRE, expected_head=head,
                                            reason="时机过了")
        self.assertTrue(written["ok"], written)
        self.assertEqual(written["data"]["state"], lss.STATE_EXPIRED)
        self.assertFalse(written["data"]["state_derived"])
        after = self.service.read_share(draft["share_id"])
        self.assertEqual(after["data"]["share"]["state"], lss.STATE_EXPIRED)
        self.assertFalse(after["data"]["share"]["state_derived"])
        # Terminal: no revival by a later send.
        dead = self.service.apply_action(share_id=draft["share_id"],
                                         action=lss.ACTION_SEND_LOCAL,
                                         expected_head=written["data"]["head_version_id"])
        self.assertEqual(dead["reason_code"], lss.REASON_TERMINAL_STATE)


class WiringTest(unittest.TestCase):
    def test_constructor_refuses_an_incomplete_projects_surface(self) -> None:
        class HalfOwner:
            subject_id = SUBJECT
            workspace_id = WORKSPACE

            def list_projects(self) -> dict[str, Any]:
                return {"ok": True, "projects": []}

            def read_project(self, artifact_id: str, **kwargs: Any) -> dict[str, Any]:
                return {"ok": False, "status": "not_found"}

        with self.assertRaises(lss.SharingUnavailable) as caught:
            lss.LifeSharingService(HalfOwner())
        self.assertIn("PROJECTS_METHODS_MISSING", str(caught.exception))
        with self.assertRaises(lss.SharingUnavailable):
            lss.LifeSharingService(None)

    def test_env_factory_refuses_a_half_wired_environment(self) -> None:
        with self.assertRaises(lss.SharingUnavailable):
            lss.sharing_from_env({})
        with self.assertRaises(lss.SharingUnavailable):
            lss.sharing_from_env({"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE})
        with self.assertRaises(lss.SharingUnavailable):
            # workspace + grant but no socket/subject: still refused, never a stand-in store.
            lss.sharing_from_env({"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE,
                                  "NYAIRO_ARTIFACT_GRANT": GRANT})

    def test_describe_reports_no_store_and_no_external_send(self) -> None:
        port = FakeOwnerPort()
        projects = pps.PrivateProjectService(port, WORKSPACE, GRANT)
        described = lss.LifeSharingService(projects).describe()
        self.assertTrue(described["owns_no_store"])
        self.assertFalse(described["external_send"])
        self.assertEqual(described["channels"], [lss.CHANNEL_LOCAL_INBOX])
        self.assertEqual(described["audiences"], [lss.AUDIENCE_LOCAL_OWNER])
        self.assertNotIn("content", described)

    def test_list_reports_an_unreadable_owner_instead_of_an_empty_inbox(self) -> None:
        class BrokenReadPort(FakeOwnerPort):
            def read_artifact(self, artifact_id: str) -> dict[str, Any]:
                raise RuntimeError("owner went away")

        port = BrokenReadPort()
        port.seed(artifact_id="artifact:share-ish", title=lss.TITLE_PREFIX + "一件",
                  content="不是信封")
        service = lss.LifeSharingService(pps.PrivateProjectService(port, WORKSPACE, GRANT))
        listing = service.list_shares()
        self.assertFalse(listing["ok"])
        self.assertEqual(listing["status"], lss.STATUS_UNAVAILABLE)
        self.assertEqual(listing["data"]["failed_artifact_id"], "artifact:share-ish")


if __name__ == "__main__":
    unittest.main()
