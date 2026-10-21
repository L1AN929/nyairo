#!/usr/bin/env python3
"""定点回归：来源**读被拒绝**时，分享正文（依赖来源的那段）不得出现在任何返回 JSON 里。

复现的具体缺陷（父级 scratch/sharing-source-projection-probe.py 已复现）：
``read_share(include_text=True)`` 与 ``list_shares(include_text=True)`` 在来源 artifact 被
Governance/owner 拒绝读取（``{"ok": false, "status": "rejected", "reason_code": "SOURCE_GOVERNED"}``）
之后，仍然把草稿正文原样发出来 —— 那一段正文正是从受治理的来源里摘出来的，等于从派生件把被挡下的
内容漏回来。

本文件断言的是**整份 JSON**（不是某一个字段）：用唯一 sentinel
``PRIVATE_SHARE_SENTINEL_DO_NOT_RETURN`` 扫 ``json.dumps(answer)``。同时要求：
* identity / state / receipt / source 绑定 / 长度这些**元数据照旧保留**；
* ``private_text_included`` 反映**真实投影**（真的没发正文就是 False）；
* 读拒绝**具名**（``SOURCE_GOVERNED`` / ``SOURCE_NOT_FOUND`` / ``SOURCE_UNREADABLE``），
  ``source_current.state`` 分清 ``GOVERNED`` / ``NOT_FOUND`` / ``UNAVAILABLE``；
* **不过度**：正常的来源换版（``REVISION_CHANGED``）与已归档（``ARCHIVED``）仍然可读、正文照发，
  只是 ``stale`` 与 ``lifecycle`` 说准；也不是把它们伪称删除。

复用 ``tests/test_v04_sharing_service_stage.py`` 的 ``SharingTestBase`` / ``FakeOwnerPort`` / ``Clock``
（同一套 owner 替身语义），只在自己这一层打 ``projects.read_project`` 的桩。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tests.test_v04_sharing_service_stage import (  # noqa: E402
    GRANT,
    SOURCE_TEXT,
    SUBJECT,
    WORKSPACE,
    Clock,
    FakeOwnerPort,
    SharingTestBase,
)

import life_sharing_service as lss  # noqa: E402

SENTINEL = "PRIVATE_SHARE_SENTINEL_DO_NOT_RETURN"
SHARE_TEXT = "留白重排完了，第二段还是挤 —— " + SENTINEL


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


class GovernedSourceProjectionTest(SharingTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.draft_answer = self.draft(share_text=SHARE_TEXT)
        self.share_id = self.draft_answer["share_id"]
        # Control: with a readable source the bounded draft text *is* projected.
        control = self.service.read_share(self.share_id)
        self.assertIn(SENTINEL, _dump(control))

    # -- helpers -----------------------------------------------------------------
    def _refuse_source(self, *, status: str = "rejected",
                       reason: str = "SOURCE_GOVERNED") -> None:
        original = self.projects.read_project

        def patched(artifact_id: str, **kwargs: Any) -> dict[str, Any]:
            if str(artifact_id) == self.source:
                return {"ok": False, "status": status, "reason_code": reason,
                        "detail": "owner refused this source read", "source": "ManagedArtifact"}
            return original(artifact_id, **kwargs)

        self.projects.read_project = patched  # type: ignore[assignment]

    def _assert_metadata_kept(self, share: dict[str, Any]) -> None:
        self.assertEqual(share["share_id"], self.share_id)
        self.assertEqual(share["state"], lss.STATE_DRAFT)
        self.assertEqual(share["head_version_id"],
                         self.port.head_version_id(self.share_id))
        self.assertEqual(share["source"]["artifact_id"], self.source)
        self.assertEqual(share["source"]["version_id"],
                         self.port.head_version_id(self.source))
        self.assertEqual(share["audience"], lss.AUDIENCE_LOCAL_OWNER)
        self.assertEqual(share["channel"], lss.CHANNEL_LOCAL_INBOX)
        self.assertEqual(share["share_revision"], 1)
        self.assertEqual(share["text_chars"], len(SHARE_TEXT))
        self.assertEqual(share["source"]["content_chars"], len(SOURCE_TEXT))

    # -- tests -------------------------------------------------------------------
    def test_read_share_withholds_the_draft_text_when_the_source_read_is_refused(self) -> None:
        self._refuse_source()
        answer = self.service.read_share(self.share_id, include_text=True)

        self.assertNotIn(SENTINEL, _dump(answer))
        self.assertTrue(answer["ok"], answer)
        share = answer["data"]["share"]
        self.assertEqual(share["text"], "")
        self.assertTrue(share["text_withheld"])
        self.assertFalse(share["private_text_included"])
        self.assertFalse(answer["data"]["private_text_included"])
        self._assert_metadata_kept(share)
        # The refusal is named, and it is not mistaken for "deleted" or for a stale revision.
        source = answer["data"]["source_current"]
        self.assertFalse(source["readable"])
        self.assertTrue(source["governed"])
        self.assertTrue(source["stale"])
        self.assertEqual(source["state"], lss.SOURCE_STATE_GOVERNED)
        self.assertEqual(source["reason_code"], lss.REASON_SOURCE_GOVERNED)
        self.assertEqual(source["owner_status"], "rejected")
        self.assertEqual(source["owner_reason_code"], "SOURCE_GOVERNED")
        # Nothing anywhere in the answer claims to have sent or delivered anything.
        self.assertNotIn("external", _dump(answer["data"]["receipt"]))

    def test_read_share_without_text_stays_text_free(self) -> None:
        self._refuse_source()
        answer = self.service.read_share(self.share_id, include_text=False)
        self.assertNotIn(SENTINEL, _dump(answer))
        self.assertFalse(answer["data"]["private_text_included"])
        self.assertEqual(answer["data"]["share"]["text"], "")

    def test_list_shares_with_text_withholds_refused_rows(self) -> None:
        control = self.service.list_shares(include_text=True)
        self.assertIn(SENTINEL, _dump(control))
        self.assertTrue(control["data"]["private_text_included"])
        self.assertEqual(control["data"]["withheld_count"], 0)

        self._refuse_source()
        answer = self.service.list_shares(include_text=True)

        self.assertNotIn(SENTINEL, _dump(answer))
        data = answer["data"]
        self.assertFalse(data["private_text_included"])
        self.assertEqual(data["withheld_count"], 1)
        self.assertEqual(data["counts"]["total"], 1)
        self.assertEqual(data["counts"]["draft"], 1)
        row = data["shares"][0]
        self.assertNotIn("text", row)
        self.assertTrue(row["text_withheld"])
        self.assertFalse(row["private_text_included"])
        self.assertEqual(row["share_id"], self.share_id)
        self.assertEqual(row["state"], lss.STATE_DRAFT)
        self.assertEqual(row["head_version_id"], self.port.head_version_id(self.share_id))
        self.assertEqual(row["source"]["version_id"], self.port.head_version_id(self.source))
        self.assertEqual(row["text_chars"], len(SHARE_TEXT))

    def test_a_delivered_receipt_survives_while_the_text_is_withheld(self) -> None:
        sent = self.service.apply_action(share_id=self.share_id,
                                         action=lss.ACTION_SEND_LOCAL,
                                         expected_head=self.draft_answer["data"]["head_version_id"])
        self.assertTrue(sent["ok"], sent)
        receipt = sent["data"]["receipt"]

        self._refuse_source()
        answer = self.service.read_share(self.share_id, include_text=True)
        self.assertNotIn(SENTINEL, _dump(answer))
        data = answer["data"]
        self.assertEqual(data["share"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertTrue(data["delivered"])
        self.assertEqual(data["receipt"], receipt)
        self.assertEqual(data["receipt"]["delivery_id"], receipt["delivery_id"])
        self.assertTrue(data["receipt"]["local_only"])
        self.assertFalse(data["receipt"]["read_by_audience"])
        self.assertTrue(data["share"]["text_withheld"])
        self.assertFalse(data["private_text_included"])

    def test_a_moved_source_is_stale_but_still_readable_and_still_shows_the_text(self) -> None:
        self.projects.commit_project(
            artifact_id=self.source, content=SOURCE_TEXT + "\n后来又补了一句。",
            expected_head=self.port.head_version_id(self.source),
            operation_key="source-next-governed-test")
        answer = self.service.read_share(self.share_id, include_text=True)

        self.assertIn(SENTINEL, _dump(answer))          # not over-withheld
        self.assertTrue(answer["data"]["private_text_included"])
        source = answer["data"]["source_current"]
        self.assertTrue(source["readable"])
        self.assertFalse(source["governed"])
        self.assertTrue(source["stale"])
        self.assertEqual(source["state"], lss.SOURCE_STATE_REVISION_CHANGED)
        self.assertEqual(source["reason_code"], lss.REASON_SOURCE_REVISION_CHANGED)

    def test_an_archived_source_reports_the_real_lifecycle_and_keeps_the_text(self) -> None:
        self.port.artifacts[self.source]["lifecycle"] = "ARCHIVED"
        answer = self.service.read_share(self.share_id, include_text=True)

        self.assertIn(SENTINEL, _dump(answer))
        source = answer["data"]["source_current"]
        self.assertTrue(source["readable"])
        self.assertFalse(source["governed"])
        self.assertEqual(source["lifecycle"], "ARCHIVED")
        self.assertEqual(source["state"], lss.SOURCE_STATE_ARCHIVED)
        self.assertEqual(source["reason_code"], lss.REASON_SOURCE_ARCHIVED)

    def test_a_missing_source_is_named_not_found_and_withholds_the_text(self) -> None:
        self.port.artifacts.pop(self.source, None)
        self.port.versions.pop(self.source, None)
        answer = self.service.read_share(self.share_id, include_text=True)

        self.assertNotIn(SENTINEL, _dump(answer))
        self.assertTrue(answer["data"]["share"]["text_withheld"])
        source = answer["data"]["source_current"]
        self.assertFalse(source["readable"])
        self.assertFalse(source["governed"])
        self.assertEqual(source["state"], lss.SOURCE_STATE_NOT_FOUND)
        self.assertEqual(source["reason_code"], lss.REASON_SOURCE_NOT_FOUND)

    def test_an_unavailable_owner_is_named_and_withholds_the_text(self) -> None:
        self._refuse_source(status="unavailable", reason="SUPPLY_UNAVAILABLE")
        answer = self.service.read_share(self.share_id, include_text=True)

        self.assertNotIn(SENTINEL, _dump(answer))
        self.assertTrue(answer["data"]["share"]["text_withheld"])
        source = answer["data"]["source_current"]
        self.assertFalse(source["readable"])
        self.assertFalse(source["governed"])
        self.assertEqual(source["state"], lss.SOURCE_STATE_UNAVAILABLE)
        self.assertEqual(source["reason_code"], lss.REASON_SOURCE_UNREADABLE)

    def test_prepare_refuses_a_refused_source_without_echoing_the_text(self) -> None:
        self._refuse_source()
        before = len(self.port.share_artifacts())
        answer = self.service.prepare_share(source_artifact_id=self.source,
                                            share_text="另一段也想说 —— " + SENTINEL)
        self.assertFalse(answer["ok"])
        self.assertNotIn(SENTINEL, _dump(answer))
        self.assertEqual(answer["reason_code"], lss.REASON_SOURCE_GOVERNED)
        self.assertTrue(answer["data"]["governed"])
        self.assertEqual(answer["data"]["source_artifact_id"], self.source)
        self.assertEqual(len(self.port.share_artifacts()), before)


if __name__ == "__main__":
    unittest.main()
