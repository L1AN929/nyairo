#!/usr/bin/env python3
"""v0.5 第三条：真实作品结果承接到有限本地表达（换版 / 不可读 / 草稿 CAS）。

本模块只守这一块的正式贯通与正负控，不重跑前三块（A43/B37、A44/B36、A67/B24）：

1. 真实作品 A 的一个版本由正式 Decision 写出并得到 owner 结果身份与 step receipt
   —— 不是只读种子作品。
2. 正式 Decision 从 A 的**当前 head** prepare 一份草稿：草稿是另一件 artifact，
   A 的 head 不变；来源精确绑定到那一版。
3. 正式 Decision CONTINUE 同一作品写到下一版：旧草稿仍绑定原版本；下一轮**实际请求**
   里 ``source_current.stale/matches_recorded`` 如实，旧正文仍可读（不伪称被删）；
   主动 send_local 被 owner 拒 ``stale_source``，草稿 head / state / receipt 全不变。
4. 再后续正式 Decision 对当前可读的 A 新 prepare：新草稿绑定新版本，旧草稿一字未改；
   新 Decision send_local 只产生**一条**本地 receipt，外部/已读/确认/理解全 false；
   重复发送与同一意图重放都不产生第二条。
5. 来源不可读：只对**来源读**拒绝的故障端口下，正文在最终请求里被扣留
   （``text_withheld`` / 原因码），仍发则 owner 拒且零写；sentinel 不出现在请求、
   拒绝答案或读回答案里 —— 不从旧 text/缓存/历史版本恢复。
6. 草稿 CAS 冲突与来源换版分开：Decision 读完之后另一个合法 owner writer 改了草稿
   head，旧 ``expected_head`` 的 send 是 ``conflict``（原因码不是 ``SOURCE_*``），
   零新 receipt，下一次正式请求看到的是那个新 head。

真实件：真实 Supply owner（AF_UNIX + 落盘 canonical db）、真实 M0 Memory、真实
ProductionLifeRuntime、真实 Sharing / Practice owner、AG-0/AG-1/AG-2/AR-0。
替身只有 Decision 与内容回答（脚本形状，明示），以及"来源不可读"的端口故障注入。
不联网、不外发、不碰任何真实实例；不宣称真模型自然采用、用户收到或 G1。

POSIX 专用（``fcntl`` + AF_UNIX）；原生 Windows 上整模块 skip。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: str | None = None
if os.name == "posix":  # pragma: no branch - native Windows skips every class below
    try:
        import life_sharing_service as lss  # noqa: E402
        import result_method_behavior_smoke as harness  # noqa: E402
        import v05_result_sharing_continuation_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        lss = harness = driver = None
else:  # pragma: no cover
    lss = harness = driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))

D1_TEXT = "这一段来自作品刚写完的那一版，先留在这里。"
D2_TEXT = "换过一版之后，这一句才是现在想说的。"
SENTINEL = driver.SENTINEL if driver is not None else "SENTINEL_UNAVAILABLE"


@POSIX_ONLY
@needs_fixture
class ResultSharingContinuationTest(unittest.TestCase):
    """一个隔离实例、一条正式链：真实作品 → 草稿 → 换版 → 新草稿 → 一条本地 receipt。"""

    def setUp(self) -> None:
        self.chain = harness.build_chain("v05-result-sharing-test")
        self.addCleanup(self._close)
        self.seed = harness.seed_method_after_first_write(self.chain)
        self.planner = driver.install_planner(self.chain)
        receipt = self.seed.round_out.get("step_receipt") or {}
        self.artifact = str(receipt.get("artifact_id")
                            or (self.seed.round_out.get("project_created") or {})
                            .get("artifact_id") or "")
        self.assertIsInstance(self.chain.cycle.sharing_service, lss.LifeSharingService)
        self.chain.cycle.work_artifact_id = self.artifact
        self.service = self.chain.cycle.sharing_service

    def _close(self) -> None:
        try:
            self.chain.runtime.close()
        except Exception as exc:  # noqa: BLE001 - a fixture close is not a test result
            print("  runtime close error:", type(exc).__name__, str(exc)[:200])
        try:
            self.chain.services.stop()
        except Exception as exc:  # noqa: BLE001
            print("  supply stop error:", type(exc).__name__, str(exc)[:200])

    # -- helpers ------------------------------------------------------------------

    def head(self) -> str:
        return driver.head_version(self.chain, self.artifact)

    def round(self, step: dict, *, trigger: str) -> dict:
        return driver.run_round(self.chain, self.planner, step, trigger=trigger)

    def prompt_block(self) -> dict:
        return driver.latest_sharing_block(self.planner)

    def prompt_row(self, share_id: str) -> dict:
        rows = driver.draft_rows(self.planner, share_id)
        self.assertTrue(rows, {"share_id": share_id, "block": self.prompt_block()})
        return rows[0]

    def prepare(self, text: str, title: str, *, trigger: str) -> dict:
        out = self.round({"op": "prepare", "text": text, "title": title}, trigger=trigger)
        share = out.get("sharing") or {}
        self.assertTrue(share.get("ok"), {"outcome": out.get("outcome"), "share": share,
                                          "rounds": driver.ROUNDS[-1:]})
        self.assertEqual(share.get("state"), lss.STATE_DRAFT, share)
        self.assertFalse(share.get("delivered"), share)
        return share

    def owner_bound_version(self, share_id: str) -> str:
        answer = self.service.read_share(share_id, include_text=False)
        self.assertTrue(answer.get("ok"), answer)
        source = (answer["data"]["share"] or {}).get("source") or {}
        return str(source.get("version_id") or "")

    def owner_share(self, share_id: str, *, include_text: bool = False) -> dict:
        answer = self.service.read_share(share_id, include_text=include_text)
        self.assertTrue(answer.get("ok"), answer)
        return answer["data"]

    def continue_work(self, body: str, *, trigger: str) -> dict:
        stamp = driver.next_instant()
        candidate = driver.continue_candidate(self.chain, self.artifact, observed_at=stamp)
        self.assertTrue(candidate, {"artifact": self.artifact})
        self.chain.client.payload = {"kind": "append_paragraph", "text": body,
                                     "self_note": "stub: next work version"}
        self.planner.plan = [{"script": {"decision": "CONTINUE",
                                         "selected_candidate_id": candidate,
                                         "short_rationale": "stub: continue the same work"}}]
        return self.chain.cycle.evaluate_once(trigger_ref=trigger, act=True,
                                              observed_at=stamp)

    # -- 1/2/3/4: 换版后的诚实与一次本地投递 ---------------------------------------

    def test_a_real_new_version_makes_the_old_draft_stale_then_a_new_draft_gets_one_receipt(self):
        receipt = self.seed.round_out.get("step_receipt") or {}
        handoff = self.seed.round_out.get("experience_handoff") or {}
        v2 = self.head()
        self.assertTrue(v2)
        self.assertTrue(receipt.get("artifact_id"), receipt)
        self.assertEqual(str(handoff.get("progress_result_ref") or ""),
                         "artifact:%s@artifactversion:%s" % (driver.bare(self.artifact), v2))
        self.assertGreaterEqual(driver.version(self.chain, self.artifact), 2)

        # 正式 Decision 从当前 head prepare：草稿是另一件 artifact，A 不动。
        d1 = self.prepare(D1_TEXT, "刚写完那版", trigger="evt:share-cont:prepare-d1")
        d1_id = str(d1["share_id"])
        d1_head = str(d1["head_version_id"])
        self.assertEqual(self.owner_bound_version(d1_id), v2)
        self.assertNotEqual(driver.bare(d1_id), driver.bare(self.artifact))
        self.assertEqual(self.head(), v2)

        # 正式 CONTINUE 写出同一作品的下一个真实版本。
        out_v3 = self.continue_work("下一版：把结尾收住。", trigger="evt:share-cont:continue")
        v3 = self.head()
        self.assertTrue(v3 and v3 != v2)
        self.assertEqual(driver.bare((out_v3.get("step_receipt") or {}).get("artifact_id")),
                         driver.bare(self.artifact))

        # 下一轮真实请求：来源换版如实，旧正文仍可读（不伪称被删）。
        preview = self.chain.cycle.sharing_decision_context()
        row = [item for item in (preview.get("shares") or [])
               if str(item.get("share_id") or "") == d1_id][0]
        current = row.get("source_current") or {}
        self.assertTrue(current.get("readable"))
        self.assertFalse(current.get("matches_recorded"))
        self.assertTrue(current.get("stale"))
        self.assertEqual(current.get("reason_code"), "SOURCE_REVISION_CHANGED")
        self.assertTrue(row.get("text_available"))
        self.assertTrue(row.get("text_excerpt"))

        # 主动按旧草稿发送：owner 拒 stale_source，零写。
        out_stale = self.round({"op": "send_local", "target": d1_id},
                               trigger="evt:share-cont:stale-send")
        stale = out_stale.get("sharing") or {}
        self.assertFalse(stale.get("ok"), stale)
        self.assertEqual(stale.get("status"), lss.STATUS_STALE_SOURCE, stale)
        self.assertFalse(stale.get("delivered"), stale)
        self.assertIsNone(stale.get("receipt"), stale)
        after = self.owner_share(d1_id)
        self.assertEqual(after["share"]["head_version_id"], d1_head)
        self.assertEqual(after["share"]["state"], lss.STATE_DRAFT)
        self.assertIsNone(after.get("receipt"))
        self.assertEqual(self.head(), v3)
        # 最终请求里确实带着"来源换版"这条事实。
        self.assertTrue(self.prompt_row(d1_id)["source_current"]["stale"])

        # 新 Decision 对当前可读的 A 新 prepare：新草稿绑定新版本，旧草稿不变。
        d2 = self.prepare(D2_TEXT, "换版之后", trigger="evt:share-cont:prepare-d2")
        d2_id = str(d2["share_id"])
        self.assertNotEqual(driver.bare(d2_id), driver.bare(d1_id))
        self.assertEqual(self.owner_bound_version(d2_id), v3)
        old = self.owner_share(d1_id)
        self.assertEqual(old["share"]["head_version_id"], d1_head)
        self.assertEqual(old["share"]["state"], lss.STATE_DRAFT)
        counts = self.service.list_shares(include_text=False)["data"]["counts"]
        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["local_delivered"], 0)

        # 新 Decision send_local：恰好一条本地 receipt，外部事实全 false。
        out_send = self.round({"op": "send_local", "target": d2_id},
                              trigger="evt:share-cont:send")
        sent = out_send.get("sharing") or {}
        self.assertTrue(sent.get("ok"), sent)
        self.assertEqual(sent.get("state"), lss.STATE_LOCAL_DELIVERED, sent)
        self.assertTrue(sent.get("delivered"))
        self.assertEqual(sent.get("delivery_count"), 1)
        owner_receipt = self.owner_share(d2_id)["receipt"] or {}
        self.assertTrue(owner_receipt.get("local_only"))
        self.assertFalse(owner_receipt.get("external_channel_sent"))
        self.assertFalse(owner_receipt.get("external_delivery_confirmed"))
        self.assertFalse(owner_receipt.get("read_by_audience"))
        self.assertFalse(owner_receipt.get("acknowledged"))
        self.assertFalse(owner_receipt.get("understood"))
        self.assertEqual(str(owner_receipt.get("delivery_id") or ""),
                         str((sent.get("receipt") or {}).get("delivery_id") or ""))
        self.assertEqual(self.head(), v3)
        # 分享件不是"可继续的作品"：草稿 id 不进 project options，作品 A 仍在。
        options = json.dumps(self.chain.cycle.project_options(), ensure_ascii=False, default=str)
        self.assertNotIn(driver.bare(d1_id), options)
        self.assertNotIn(driver.bare(d2_id), options)
        # 重复发送：owner 拒，仍是那一条 receipt。
        delivered_head = self.owner_share(d2_id)["share"]["head_version_id"]
        replay = self.service.apply_action(share_id=d2_id, action="send_local",
                                           expected_head=delivered_head)
        self.assertFalse(replay.get("ok"), replay)
        self.assertEqual(replay.get("reason_code"), lss.REASON_ALREADY_DELIVERED)
        self.assertEqual(str((replay.get("data") or {}).get("receipt", {})
                             .get("delivery_id") or ""),
                         str(owner_receipt.get("delivery_id") or ""))
        self.assertEqual(self.service.list_shares(include_text=False)["data"]["inbox"]
                         ["delivered_count"], 1)

        # NO_ACTION 合法：不分享、不自动 drop/重试/刷新来源。
        heads_before = {sid: self.owner_share(sid)["share"]["head_version_id"]
                        for sid in (d1_id, d2_id)}
        quiet = self.round({"op": "none"}, trigger="evt:share-cont:quiet")
        self.assertFalse(quiet.get("sharing"))
        heads_after = {sid: self.owner_share(sid)["share"]["head_version_id"]
                       for sid in (d1_id, d2_id)}
        self.assertEqual(heads_after, heads_before)
        self.assertEqual(self.owner_share(d1_id)["share"]["state"], lss.STATE_DRAFT)
        self.assertEqual(self.owner_share(d2_id)["share"]["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertEqual(self.head(), v3)

    # -- 5: 来源不可读 --------------------------------------------------------------

    def test_an_unreadable_source_withholds_the_words_and_the_owner_refuses_the_send(self):
        draft = self.prepare("这一句只用来验证来源不可读时不会漏出来。" + SENTINEL,
                             "来源不可读用例", trigger="evt:share-cont:prepare-d3")
        share_id = str(draft["share_id"])
        head = str(draft["head_version_id"])

        refusing = driver.SourceRefusingPort(self.service.projects, source_id=self.artifact)
        governed = lss.LifeSharingService(refusing, clock=self.service._clock)
        self.chain.cycle.sharing_service = governed

        self.round({"op": "none"}, trigger="evt:share-cont:governed-read")
        row = self.prompt_row(share_id)
        self.assertIs(row.get("text_withheld"), True, row)
        self.assertIs(row.get("text_available"), False, row)
        self.assertEqual(row.get("withheld_reason_code"), "SOURCE_GOVERNED", row)
        self.assertFalse(row.get("text_excerpt"), row)
        block_text = json.dumps(self.prompt_block(), ensure_ascii=False, default=str)
        bounded_text = json.dumps(self.planner.captured[-1]["bounded"], ensure_ascii=False,
                                  default=str)
        self.assertNotIn(SENTINEL, block_text)
        self.assertNotIn(SENTINEL, bounded_text)

        owner_read = governed.read_share(share_id, include_text=True)
        self.assertTrue(owner_read.get("ok"), owner_read)
        self.assertIs(owner_read["data"].get("text_withheld"), True)
        self.assertNotIn(SENTINEL, json.dumps(owner_read, ensure_ascii=False, default=str))

        out = self.round({"op": "send_local", "target": share_id},
                         trigger="evt:share-cont:governed-send")
        refused = out.get("sharing") or {}
        self.assertFalse(refused.get("ok"), refused)
        self.assertFalse(refused.get("delivered"), refused)
        self.assertEqual(refused.get("status"), lss.STATUS_STALE_SOURCE, refused)
        self.assertNotIn(str(refused.get("reason_code") or ""), ("", "SOURCE_NOT_FOUND"))
        self.assertNotIn(SENTINEL, json.dumps(refused, ensure_ascii=False, default=str))
        after = governed.read_share(share_id, include_text=False)["data"]
        self.assertEqual(after["share"]["head_version_id"], head)
        self.assertEqual(after["share"]["state"], lss.STATE_DRAFT)
        self.assertIsNone(after.get("receipt"))
        self.assertEqual(self.service.list_shares(include_text=False)["data"]["inbox"]
                         ["delivered_count"], 0)
        self.assertTrue(refusing.refused_reads > 0)

    # -- 6: 草稿 CAS 冲突 ≠ 来源换版 -------------------------------------------------

    def test_a_competing_draft_write_is_a_cas_conflict_not_a_stale_source(self):
        draft = self.prepare(D2_TEXT, "CAS 竞态用例", trigger="evt:share-cont:prepare-cas")
        share_id = str(draft["share_id"])
        head_before = str(draft["head_version_id"])

        def competing_defer(_step, *, target=share_id, head=head_before):
            other = lss.LifeSharingService(self.service.projects, clock=self.service._clock)
            answer = other.apply_action(share_id=target, action="defer",
                                        expected_head=head,
                                        opportunity_ref="quiet-evening",
                                        operation_key="share:op:share-cont:competing-defer")
            self.assertTrue(answer.get("ok"), answer)

        out = self.round({"op": "send_local", "target": share_id, "head": head_before,
                          "race": competing_defer},
                         trigger="evt:share-cont:cas-race")
        raced = out.get("sharing") or {}
        self.assertFalse(raced.get("ok"), raced)
        self.assertEqual(raced.get("status"), lss.STATUS_CONFLICT, raced)
        reason = str(raced.get("reason_code") or "")
        self.assertTrue(reason, raced)
        self.assertFalse(reason.startswith("SOURCE_"), raced)
        self.assertIsNone(raced.get("source_current"), raced)

        after = self.owner_share(share_id)
        self.assertEqual(after["share"]["state"], lss.STATE_DEFERRED)
        self.assertNotEqual(after["share"]["head_version_id"], head_before)
        self.assertIsNone(after.get("receipt"))
        self.assertEqual(self.service.list_shares(include_text=False)["data"]["inbox"]
                         ["delivered_count"], 0)

        # 下一次正式请求看到的是那个新 head（不是模型读到的旧 head）。
        self.round({"op": "none"}, trigger="evt:share-cont:after-race")
        row = self.prompt_row(share_id)
        self.assertEqual(row.get("head_version_id"), after["share"]["head_version_id"])
        self.assertEqual(row.get("state"), lss.STATE_DEFERRED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
