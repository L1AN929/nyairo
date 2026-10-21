#!/usr/bin/env python3
"""v0.4 阶段 B：手机分享面（``components/phone/sharing.py``）的定向用例。

这个文件测什么
--------------
P 的分享门面在**一台实例自己的 owner 事实**上做的每一件事：列出她还读得到的来源、用显式短文
形成一个 private 草稿、延后 / 本地投递 / 放弃、CAS 与 UNKNOWN 的原 key 语义、来源换版阻断、
来源读被拒绝时整份 JSON 不漏正文、终态不重发。

* owner 用 ``test_v04_private_projects_stage.FakePort``（内存替身：CAS、operation_key 幂等、
  ARCHIVED、UNKNOWN 必须先对账）。真正的 Supply sqlite owner 用例见
  ``tests/test_v04_phone_sharing_supply.py``（POSIX）。
* 不新开 store、不建 sqlite、不删除任何夹具；本文件不落盘。
* 断言只读 P 与 owner 的答复，不把「HTTP 200」当证据（HTTP 面在
  ``tests/test_v04_phone_sharing_http.py``）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_service as pps  # noqa: E402
from components.phone import sharing as phone_sharing  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    FakePort,
    GRANT,
    ITEMS,
    READING,
    WORKSPACE,
)

SENTINEL = "PRIVATE_SHARE_SENTINEL_DO_NOT_RETURN"
SOURCE_TEXT = "第一段是留白。第二段还是太挤。" + SENTINEL
SHARE_TEXT = "留白重排完了，第二段还是挤。"


def projects_for(port: FakePort) -> pps.PrivateProjectService:
    """The share surface's projects owner: only the private-items doc is a non-source.

    The reading material is deliberately **not** excluded here: it is one of her real artifacts,
    so it may be a source of its own kind (the project screen keeps its own exclusion).
    """

    return pps.PrivateProjectService(port, WORKSPACE, GRANT, excluded_artifact_ids=(ITEMS,))


class PhoneSharingTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料", content="第一页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{}")
        self.source = "artifact:" + "a" * 32
        self.port.seed(artifact_id=self.source, title="她的留白练习", content=SOURCE_TEXT)
        self.sharing = phone_sharing.PhoneSharing(
            projects_for(self.port), subject="chiyo", workspace_id=WORKSPACE,
            reading_artifact_id=READING)

    # -- helpers -----------------------------------------------------------------------

    def prepare(self, **kwargs) -> dict:
        defaults = {"source_artifact_id": self.source, "share_text": SHARE_TEXT,
                    "title": "留白重排"}
        defaults.update(kwargs)
        return self.sharing.prepare(**defaults)

    def draft(self, **kwargs) -> dict:
        answer = self.prepare(**kwargs)
        self.assertTrue(answer["ok"], answer)
        return answer

    def writes(self) -> list:
        return [call for call in self.port.calls
                if call[0] in ("create_artifact", "commit_version", "archive_artifact")]


class PhoneSharingSurfaceTest(PhoneSharingTestBase):
    def test_describe_names_the_one_channel_and_owns_no_store(self) -> None:
        described = self.sharing.describe()
        self.assertEqual(described["kind"], "phone_sharing")
        self.assertTrue(described["owns_no_store"])
        self.assertFalse(described["external_send"])
        self.assertTrue(described["local_only"])
        self.assertEqual(described["audiences"], ["local:owner"])
        self.assertEqual(described["channels"], ["local_inbox"])
        self.assertEqual(tuple(described["actions"]), ("send_local", "defer", "drop"))
        self.assertIn("本地存档", described["note"])
        self.assertNotIn(SENTINEL, json.dumps(described, ensure_ascii=False))

    def test_sources_are_only_what_the_owner_really_reads(self) -> None:
        answer = self.sharing.sources()
        self.assertTrue(answer["ok"], answer)
        rows = {row["artifact_id"]: row for row in answer["data"]["sources"]}
        self.assertIn(self.source, rows)
        self.assertEqual(rows[self.source]["kind"], "project")
        self.assertTrue(rows[self.source]["readable"])
        # The reading binding is offered as its own kind, not disguised as a work.
        self.assertIn(READING, rows)
        self.assertEqual(rows[READING]["kind"], "reading")
        self.assertTrue(rows[READING]["readable"])
        # The private-items document is not a source at all.
        self.assertNotIn(ITEMS, rows)
        # A share draft is a derived thing: it never becomes its own source.
        draft = self.draft()
        after = self.sharing.sources()
        ids = {row["artifact_id"] for row in after["data"]["sources"]}
        self.assertNotIn(draft["data"]["share_id"], ids)
        self.assertFalse(after["data"]["private_text_included"])
        self.assertNotIn(SENTINEL, json.dumps(after, ensure_ascii=False))

    def test_prepare_forms_one_private_draft_with_an_explicit_text(self) -> None:
        draft = self.draft()
        self.assertEqual(draft["status"], "applied")
        data = draft["data"]
        self.assertEqual(data["state"], "DRAFT")
        self.assertTrue(data["share_id"].startswith("artifact:"))
        self.assertTrue(data["head_version_id"])
        self.assertEqual(data["audience"], "local:owner")
        self.assertEqual(data["channel"], "local_inbox")
        self.assertEqual(data["source"]["artifact_id"], self.source)
        self.assertTrue(data["source"]["version_id"])
        self.assertEqual(data["text"], SHARE_TEXT)
        self.assertFalse(data["delivered"])
        self.assertTrue(data["local_only"])
        self.assertIn("本地存档", data["note"])
        # 来源正文里那句话不在答案里：分享文本只有她自己写的那一句。
        self.assertNotIn(SENTINEL, json.dumps(draft, ensure_ascii=False))

    def test_prepare_needs_an_explicit_text_or_an_explicit_excerpt(self) -> None:
        nothing = self.sharing.prepare(source_artifact_id=self.source)
        self.assertFalse(nothing["ok"])
        self.assertEqual(nothing["reason_code"], "SHARE_TEXT_REQUIRED")
        self.assertEqual(self.writes(), [])

    def test_explicit_excerpt_is_a_bounded_prefix_of_the_source(self) -> None:
        answer = self.draft(share_text=None, excerpt_chars=5)
        self.assertEqual(answer["data"]["state"], "DRAFT")
        self.assertEqual(answer["data"]["excerpt"]["kind"], "SOURCE_PREFIX")
        detail = self.sharing.detail(answer["data"]["share_id"])
        self.assertEqual(detail["data"]["share"]["text"], SOURCE_TEXT[:5])

    def test_a_foreign_source_is_refused_before_anything_is_written(self) -> None:
        answer = self.sharing.prepare(source_artifact_id="artifact:" + "f" * 32,
                                      share_text=SHARE_TEXT)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "SHARE_SOURCE_NOT_OFFERED")
        self.assertEqual(self.writes(), [])

    def test_list_carries_no_text_at_all(self) -> None:
        self.draft()
        listing = self.sharing.listing()
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["data"]["counts"]["draft"], 1)
        self.assertFalse(listing["data"]["private_text_included"])
        self.assertTrue(listing["data"]["inbox"]["local_only"])
        self.assertEqual(listing["data"]["inbox"]["delivered_count"], 0)
        self.assertIn("本地存档", listing["data"]["note"])
        for row in listing["data"]["shares"]:
            self.assertNotIn("text", row)
        self.assertNotIn(SHARE_TEXT, json.dumps(listing, ensure_ascii=False))

    def test_same_intent_and_key_replays_into_one_draft(self) -> None:
        first = self.draft(operation_key="share-key-1")
        second = self.prepare(operation_key="share-key-1")
        self.assertEqual(second["data"]["share_id"], first["data"]["share_id"])
        draft_rows = [row for row in self.port.artifacts.values()
                      if phone_sharing.is_share_title(row.get("title"))]
        self.assertEqual(len(draft_rows), 1)


class PhoneSharingLifecycleTest(PhoneSharingTestBase):
    def test_defer_needs_a_basis_and_then_sends_locally(self) -> None:
        draft = self.draft()
        share_id = draft["data"]["share_id"]
        head = draft["data"]["head_version_id"]

        refused = self.sharing.act(share_id=share_id, action="defer", expected_head=head)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "DEFER_CONDITION_REQUIRED")
        self.assertEqual(self.sharing.detail(share_id)["data"]["share"]["state"], "DRAFT")

        deferred = self.sharing.act(share_id=share_id, action="defer", expected_head=head,
                                    opportunity_ref="夜里安静下来的时候")
        self.assertTrue(deferred["ok"], deferred)
        self.assertEqual(deferred["data"]["state"], "DEFERRED")
        self.assertEqual(deferred["data"]["deferred"]["opportunity_ref"], "夜里安静下来的时候")

        # Reading it again never moves the state (no timer, no tick, nothing written).
        writes_before = len(self.writes())
        for _ in range(3):
            current = self.sharing.detail(share_id)
            self.assertEqual(current["data"]["share"]["state"], "DEFERRED")
        self.assertEqual(len(self.writes()), writes_before)

        sent = self.sharing.act(share_id=share_id, action="send_local",
                                expected_head=deferred["data"]["head_version_id"])
        self.assertTrue(sent["ok"], sent)
        data = sent["data"]
        self.assertEqual(data["state"], "LOCAL_DELIVERED")
        self.assertTrue(data["delivered"])
        self.assertTrue(data["state_delivered"])
        self.assertEqual(data["delivery_count"], 1)
        receipt = data["receipt"]
        self.assertEqual(receipt["channel"], "local_inbox")
        self.assertEqual(receipt["audience"], "local:owner")
        self.assertTrue(receipt["local_only"])
        self.assertFalse(receipt["external_channel_sent"])
        self.assertFalse(receipt["external_delivery_confirmed"])
        self.assertFalse(receipt["read_by_audience"])
        self.assertIn("本地存档", data["note"])

        # 已投递不会被「再发一次」变成第二条投递。
        again = self.sharing.act(share_id=share_id, action="send_local",
                                 expected_head=data["head_version_id"])
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason_code"], "SHARE_ALREADY_DELIVERED")
        self.assertFalse(again["data"]["delivered"])
        current = self.sharing.detail(share_id)
        self.assertEqual(current["data"]["receipt"]["delivery_id"], receipt["delivery_id"])
        self.assertEqual(current["data"]["share"]["text"], SHARE_TEXT)

    def test_a_stale_head_is_a_conflict_that_keeps_the_draft(self) -> None:
        draft = self.draft()
        share_id = draft["data"]["share_id"]
        stale = self.sharing.act(share_id=share_id, action="defer", expected_head="version-old",
                                 defer_condition="以后再说")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], "conflict")
        self.assertEqual(stale["data"]["current_head_version_id"],
                         draft["data"]["head_version_id"])
        self.assertEqual(self.sharing.detail(share_id)["data"]["share"]["state"], "DRAFT")

    def test_drop_is_terminal_and_never_re_sends(self) -> None:
        draft = self.draft()
        share_id = draft["data"]["share_id"]
        dropped = self.sharing.act(share_id=share_id, action="drop",
                                   expected_head=draft["data"]["head_version_id"],
                                   reason="这段先不给她看")
        self.assertTrue(dropped["ok"], dropped)
        self.assertEqual(dropped["data"]["state"], "DROPPED")
        self.assertEqual(dropped["data"]["dropped"]["reason"], "这段先不给她看")
        head = dropped["data"]["head_version_id"]

        for action in ("send_local", "defer"):
            blocked = self.sharing.act(share_id=share_id, action=action, expected_head=head,
                                       defer_condition="随便")
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["reason_code"], "SHARE_TERMINAL_STATE")
        detail = self.sharing.detail(share_id)
        self.assertEqual(detail["data"]["share"]["state"], "DROPPED")
        self.assertEqual(detail["data"]["share"]["head_version_id"], head)
        self.assertTrue(detail["data"]["share"]["terminal"])
        self.assertEqual(detail["data"]["share"]["actions"], [])

    def test_a_moved_source_blocks_the_send_and_says_why(self) -> None:
        draft = self.draft()
        # 她后来改了那份来源（owner 记了一个新版本）。
        self.port.versions[self.source].append({
            "version_id": "version-moved", "version_number": 2,
            "parent_version_id": self.port.artifacts[self.source]["head_version_id"],
            "content": "改过的一页。", "created_at": 1760000300.0,
            "operation_key": "seed-moved"})
        self.port.artifacts[self.source]["head_version_id"] = "version-moved"
        writes_before = len(self.writes())
        blocked = self.sharing.act(share_id=draft["data"]["share_id"], action="send_local",
                                   expected_head=draft["data"]["head_version_id"])
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["status"], "stale_source")
        self.assertEqual(blocked["reason_code"], "SOURCE_REVISION_CHANGED")
        self.assertTrue(blocked["data"]["source_current"]["stale"])
        self.assertTrue(blocked["data"]["source_current"]["readable"])
        self.assertEqual(len(self.writes()), writes_before, "来源换版时不能写任何版本")
        detail = self.sharing.detail(draft["data"]["share_id"])
        self.assertEqual(detail["data"]["share"]["state"], "DRAFT")
        self.assertEqual(detail["data"]["source_current"]["state"], "REVISION_CHANGED")

    def test_unknown_keeps_the_same_key_and_reconciles_into_one_delivery(self) -> None:
        draft = self.draft()
        share_id = draft["data"]["share_id"]
        self.port.unknown_next = True
        self.port.resolve_to = lap.EffectState.NO_CHANGE
        unsure = self.sharing.act(share_id=share_id, action="send_local",
                                  expected_head=draft["data"]["head_version_id"],
                                  operation_key="send-key-unknown")
        self.assertFalse(unsure["ok"])
        self.assertEqual(unsure["status"], "unknown")
        self.assertTrue(unsure["data"]["retryable"])
        self.assertEqual(unsure["data"]["operation_key"], "send-key-unknown")
        self.assertFalse(unsure["data"]["delivered"])

        # 同一个 key 重试（不换键）：只产生一条投递。
        self.port.resolve_to = lap.EffectState.APPLIED
        retried = self.sharing.act(share_id=share_id, action="send_local",
                                   expected_head=draft["data"]["head_version_id"],
                                   operation_key="send-key-unknown")
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["data"]["operation_key"], "send-key-unknown")
        self.assertEqual(retried["data"]["delivery_count"], 1)
        self.assertEqual(self.sharing.detail(share_id)["data"]["share"]["state"],
                         "LOCAL_DELIVERED")


class PhoneSharingWithheldTextTest(PhoneSharingTestBase):
    """来源读被拒绝之后，**整份 JSON** 都不含那段正文（列表、详情一起）。"""

    def refuse_source(self) -> None:
        original = self.sharing.projects.read_project

        def refuse(artifact_id, **kwargs):
            if artifact_id == self.source:
                return {"ok": False, "status": "rejected",
                        "reason_code": "SOURCE_GOVERNED",
                        "detail": "governance refused"}
            return original(artifact_id, **kwargs)

        self.sharing.projects.read_project = refuse

    def test_detail_withholds_the_text_and_keeps_the_metadata(self) -> None:
        draft = self.draft(share_text="留白重排完了。" + SENTINEL)
        self.refuse_source()
        detail = self.sharing.detail(draft["data"]["share_id"])
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["data"]["share"]["text"], "")
        self.assertTrue(detail["data"]["share"]["text_withheld"])
        self.assertFalse(detail["data"]["share"]["text_available"])
        self.assertFalse(detail["data"]["private_text_included"])
        self.assertEqual(detail["data"]["withheld_reason_code"], "SOURCE_GOVERNED")
        self.assertEqual(detail["data"]["source_current"]["state"], "GOVERNED")
        self.assertTrue(detail["data"]["source_current"]["governed"])
        # 元数据照旧：身份、状态、head、来源绑定、受众、渠道、长度。
        share = detail["data"]["share"]
        self.assertEqual(share["share_id"], draft["data"]["share_id"])
        self.assertEqual(share["state"], "DRAFT")
        self.assertEqual(share["head_version_id"], draft["data"]["head_version_id"])
        self.assertEqual(share["source"]["artifact_id"], self.source)
        self.assertEqual(share["audience"], "local:owner")
        self.assertEqual(share["channel"], "local_inbox")
        self.assertNotIn(SENTINEL, json.dumps(detail, ensure_ascii=False))

    def test_list_does_not_leak_the_refused_text(self) -> None:
        self.draft(share_text="留白重排完了。" + SENTINEL)
        self.refuse_source()
        listing = self.sharing.listing()
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["data"]["withheld_count"], 1)
        self.assertFalse(listing["data"]["private_text_included"])
        self.assertEqual(listing["data"]["shares"][0]["source_state"], "GOVERNED")
        self.assertEqual(listing["data"]["shares"][0]["source_reason_code"], "SOURCE_GOVERNED")
        self.assertNotIn(SENTINEL, json.dumps(listing, ensure_ascii=False))

    def test_sending_from_a_refused_source_is_refused_by_name(self) -> None:
        draft = self.draft(share_text="留白重排完了。" + SENTINEL)
        self.refuse_source()
        blocked = self.sharing.act(share_id=draft["data"]["share_id"], action="send_local",
                                   expected_head=draft["data"]["head_version_id"])
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["status"], "stale_source")
        self.assertEqual(blocked["reason_code"], "SOURCE_GOVERNED")
        self.assertNotIn(SENTINEL, json.dumps(blocked, ensure_ascii=False))

    def test_prepare_from_a_refused_source_writes_nothing(self) -> None:
        self.refuse_source()
        answer = self.sharing.prepare(source_artifact_id=self.source,
                                      share_text="留白重排完了。" + SENTINEL)
        self.assertFalse(answer["ok"])
        self.assertEqual(self.writes(), [])
        self.assertNotIn(SENTINEL, json.dumps(answer, ensure_ascii=False))


class PhoneSharingBoundaryTest(PhoneSharingTestBase):
    def test_an_unknown_share_id_is_not_found(self) -> None:
        answer = self.sharing.detail("artifact:" + "e" * 32)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], "not_found")

    def test_only_three_actions_are_offered(self) -> None:
        draft = self.draft()
        refused = self.sharing.act(share_id=draft["data"]["share_id"], action="expire",
                                   expected_head=draft["data"]["head_version_id"])
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "SHARE_ACTION_REFUSED")

    def test_a_broken_owner_is_unavailable_not_an_empty_inbox(self) -> None:
        def explode() -> list:
            raise RuntimeError("owner down")

        self.sharing.projects.list_projects = explode
        listing = self.sharing.listing()
        # 死掉的 owner 不是「她的收件箱是空的」：这一面必须说自己读不到。
        self.assertFalse(listing["ok"])
        self.assertEqual(listing["status"], "unavailable")
        self.assertEqual(listing["reason_code"], "SHARE_PORT_ERROR")


if __name__ == "__main__":
    unittest.main()
