#!/usr/bin/env python3
"""v0.4 第五段：损失要真的改变下一轮可用的做法（而不是只把正文藏起来）。

这个文件测什么
--------------
``life_practice_context.PracticeSeam`` 在个人实践工作台（B 的真实服务）之上的**可用性投影**：

* 退休（``RETIRED``）是 owner 已经定义的损失。退休之后，方法仍留在她的档案里（文档、正文、
  版本、反例都在），但下一轮的决定上下文与写作上下文**不能再把它当作可命名 / 可评价 /
  可跟随的方法**；损失被**具名**（``withheld_methods``），不是用空数组冒充"她从来没有过做法"。
* 证据读不回来保持第三段既有合同：行仍被读出，但正文 / 指令由 owner 扣住；本文件不把它
  改写成"退休移除"，并验证恢复只来自 owner 的新状态。
* ``TRIAL`` / ``CONTESTED`` 不是损失：反例是状态，方法仍然可用；
* 读失败与"没有做法"分开；读上下文本身零写。

证据分级
--------
* 本文件用 ``test_v04_personal_practice_stage`` 的 ``FakePort``（内存 owner 替身）与
  ``FakeMemory``（按 A 的答复形状冻结的替身），配合 **B 的真实**
  ``PersonalPracticeService``。测的是接缝自己的可用性规则与 B 已定义状态的组合。
* 真实 Supply owner + 真实 M0 的端到端对照在
  ``scripts/loss_continuation_behavior_smoke.py``；那里损失由真实 owner 写入，旧手柄的拒绝
  来自正式 ``evaluate_once`` → AG-1 的本轮允许清单（不直接调用 validator）。
* 不调用产品模型、不开服务、不碰任何既有实例。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_practice_context as lpc  # noqa: E402
import personal_practice_service as pps  # noqa: E402
from test_v04_personal_practice_stage import (  # noqa: E402
    FakeMemory,
    seeded_port,
    service_for,
)

EVIDENCE_TEXT = "那次先写结论，结果真的写完了。"
INSTRUCTION_SENTINEL = "先写一句结论 SENTINEL_INSTRUCTION_9f3，再补三段理由"
METHOD_TITLE = "先写结论"


def bare_id(value) -> str:
    text = str(value or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


class LossContinuationBehaviorTest(unittest.TestCase):
    """一条做法从可用到损失的一整条读侧行为。"""

    def setUp(self) -> None:
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.exp = self.memory.add("exp:loss:1", EVIDENCE_TEXT)
        self.service = service_for(self.port, self.memory)
        review = self.service.create_review(
            question="怎样把一页写完？", experience_ids=[self.exp],
            operation_key="practice:loss:review")
        self.assertTrue(review["ok"], review)
        self.review_id = str(review["data"]["artifact_id"])
        self.method = self._create_method(title=METHOD_TITLE)
        self.seam = lpc.PracticeSeam(self.service)

    def _create_method(self, *, title: str, instruction: str = INSTRUCTION_SENTINEL) -> dict:
        created = self.service.create_method(
            title=title, instruction=instruction, scope="长文", uncertainty="短文未知",
            experience_ids=[self.exp], review_refs=[self.review_id],
            operation_key="practice:loss:method:%s" % title)
        self.assertTrue(created["ok"], created)
        return created["data"]

    def _head(self, artifact_id: str) -> str:
        return str(self.service.read_method(artifact_id)["data"]["head_version_id"])

    # -- 正常路径 ---------------------------------------------------------------

    def test_baseline_offers_the_method_and_the_reads_write_nothing(self) -> None:
        before = self.service.read_method(str(self.method["artifact_id"]))["data"]
        decision = self.seam.decision_context(query="")
        writing = self.seam.writing_context("", query="")
        after = self.service.read_method(str(self.method["artifact_id"]))["data"]

        ref = lpc.method_ref_for(self.method["artifact_id"])
        self.assertEqual(decision["status"], "READY", decision)
        self.assertIn(ref, decision["method_refs"], decision)
        self.assertEqual([row["method_ref"] for row in decision["methods"]], [ref])
        self.assertNotIn("withheld_methods", decision)
        self.assertIsNotNone(writing, writing)
        self.assertEqual([row["method_ref"] for row in writing.get("methods") or []], [ref])
        self.assertEqual(before["head_version_id"], after["head_version_id"])
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(self.seam.last_withheld_methods, [])

    def test_contested_stays_usable_and_keeps_the_counterexample(self) -> None:
        head = self._head(str(self.method["artifact_id"]))
        trial = self.service.record_trial(
            str(self.method["artifact_id"]), outcome="NOT_HELPED",
            detail="这次先写结论反而卡住", experience_ids=[self.exp],
            expected_head=head, operation_key="practice:loss:counterexample")
        self.assertTrue(trial["ok"], trial)
        self.assertEqual(trial["data"]["method_state"], pps.METHOD_STATE_CONTESTED)

        decision = self.seam.decision_context(query="")
        rows = [row for row in decision["methods"]
                if row["method_ref"] == lpc.method_ref_for(self.method["artifact_id"])]
        self.assertEqual(len(rows), 1, decision)
        self.assertEqual(rows[0]["method_state"], pps.METHOD_STATE_CONTESTED)
        self.assertEqual(int(rows[0]["counterexample_count"] or 0), 1)
        self.assertNotIn("withheld_methods", decision)

    # -- 损失 1：退休 ------------------------------------------------------------

    def test_retirement_removes_the_offer_but_keeps_the_document(self) -> None:
        method_id = str(self.method["artifact_id"])
        ref = lpc.method_ref_for(method_id)
        retired = self.service.retire_method(
            method_id, expected_head=self._head(method_id),
            operation_key="practice:loss:retire")
        self.assertTrue(retired["ok"], retired)
        self.assertEqual(retired["data"]["method_state"], pps.METHOD_STATE_RETIRED)

        rows = self.seam.methods(query="")
        mine = [row for row in rows if row["artifact_id"] == method_id]
        self.assertEqual(len(mine), 1, rows)
        self.assertFalse(mine[0]["available"], mine[0])
        self.assertEqual(mine[0]["loss_reason"], lpc.LOSS_METHOD_RETIRED)
        self.assertEqual(mine[0]["method_state"], pps.METHOD_STATE_RETIRED)
        self.assertTrue(mine[0]["head_version_id"], mine[0])

        decision = self.seam.decision_context(query="")
        self.assertEqual(decision["method_refs"], [], decision)
        self.assertEqual(decision["methods"], [], decision)
        withheld = decision.get("withheld_methods") or {}
        self.assertEqual(withheld.get("count"), 1, decision)
        self.assertIn(lpc.LOSS_METHOD_RETIRED, withheld.get("reason_codes") or [], decision)
        self.assertIn(METHOD_TITLE, withheld.get("titles") or [], decision)
        self.assertNotIn("last_error", decision)

        self.assertIsNone(self.seam.method_by_ref(ref))
        self.assertEqual(self.seam.method_refs(), [])

        # 文档仍然是 owner 的历史：正文、指令、版本都在；退休不是删除。
        stored = self.service.read_method(method_id)["data"]
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_RETIRED)
        self.assertTrue(str(stored["body"]).strip())
        self.assertEqual(stored["instruction"], INSTRUCTION_SENTINEL)
        # 但它仍然是"方法文档"，不能因为退休就掉进"可以续写的作品"。
        self.assertIn(bare_id(method_id), self.seam.method_artifact_ids())

        # 写作提示不再把退休做法当参考（本夹具只有这一条方法，因此没有方法上下文）。
        writing = self.seam.writing_context("", query="")
        self.assertIsNone(writing, writing)

        fingerprint = self.seam.fingerprint_parts()
        self.assertIn(pps.METHOD_STATE_RETIRED,
                      fingerprint.get("practice_methods"), fingerprint)

    def test_retirement_is_idempotent_and_still_stops_the_offer(self) -> None:
        method_id = str(self.method["artifact_id"])
        first = self.service.retire_method(
            method_id, expected_head=self._head(method_id),
            operation_key="practice:loss:retire:1")
        second = self.service.retire_method(
            method_id, expected_head=self._head(method_id),
            operation_key="practice:loss:retire:2")
        self.assertTrue(first["ok"], first)
        self.assertEqual(second["status"], "already_applied", second)
        self.assertEqual(self.seam.decision_context(query="")["method_refs"], [])

    # -- 损失 2：证据读不回来 ----------------------------------------------------

    def test_withdrawn_evidence_keeps_its_existing_contract_and_is_reread_fresh(self) -> None:
        method_id = str(self.method["artifact_id"])
        ref = lpc.method_ref_for(method_id)
        self.memory.withdrawn.add(self.exp)

        decision = self.seam.decision_context(query="")
        # 第三段既有合同：行仍在，但正文 / 指令已被 owner 扣住；这不是"退休移除"。
        rows = [row for row in decision["methods"] if row["method_ref"] == ref]
        self.assertEqual(len(rows), 1, decision)
        self.assertEqual(rows[0]["instruction"], "", rows[0])
        self.assertNotIn("withheld_methods", decision)
        # 依赖受治理内容的指令不能从上下文或核对结果里重新透出。
        self.assertNotIn("SENTINEL_INSTRUCTION_9f3", json.dumps(decision, ensure_ascii=False))

        blocked = self.service.read_method(method_id)["data"]
        self.assertFalse(blocked["content_available"], blocked)
        self.assertEqual(blocked["body"], "")

        # 恢复只能是 owner 的新状态：撤掉替身 Memory 的撤回后，下一轮重新问 owner 才看得到，
        # 不是这个 seam 记住了"曾经被扣住"。
        self.memory.withdrawn.discard(self.exp)
        restored = self.seam.decision_context(query="")
        rows = [row for row in restored["methods"] if row["method_ref"] == ref]
        self.assertEqual(len(rows), 1, restored)
        self.assertIn("SENTINEL_INSTRUCTION_9f3", rows[0]["instruction"], rows[0])
        self.assertTrue(self.service.read_method(method_id)["data"]["content_available"])

    def test_retired_and_unreadable_are_refused_by_the_owner_by_their_own_names(self) -> None:
        retired_id = str(self.method["artifact_id"])
        other = self._create_method(title="先列提纲")
        other_id = str(other["artifact_id"])

        self.service.retire_method(retired_id, expected_head=self._head(retired_id),
                                   operation_key="practice:loss:retire:trial")
        retired_trial = self.service.record_trial(
            retired_id, outcome="UNKNOWN", detail="", experience_ids=[],
            expected_head=self._head(retired_id), operation_key="practice:loss:trial:retired")
        self.assertEqual(retired_trial["reason_code"], "PRACTICE_METHOD_RETIRED", retired_trial)

        self.memory.withdrawn.add(self.exp)
        blocked_trial = self.service.record_trial(
            other_id, outcome="UNKNOWN", detail="", experience_ids=[],
            expected_head=self._head(other_id), operation_key="practice:loss:trial:blocked")
        self.assertEqual(blocked_trial["reason_code"], "PRACTICE_EVIDENCE_UNAVAILABLE",
                         blocked_trial)

    # -- 混合与未知 --------------------------------------------------------------

    def test_a_mixed_workspace_offers_the_live_method_and_names_the_lost_one(self) -> None:
        retired_id = str(self.method["artifact_id"])
        live = self._create_method(title="先列提纲")
        live_ref = lpc.method_ref_for(live["artifact_id"])
        self.service.retire_method(retired_id, expected_head=self._head(retired_id),
                                   operation_key="practice:loss:retire:mixed")

        decision = self.seam.decision_context(query="")
        self.assertEqual(decision["method_refs"], [live_ref], decision)
        withheld = decision.get("withheld_methods") or {}
        self.assertEqual(withheld.get("count"), 1, decision)
        self.assertIn(METHOD_TITLE, withheld.get("titles") or [], decision)

    def test_an_unreadable_owner_is_reported_not_called_empty(self) -> None:
        class BrokenList:
            """真实服务的窄代理：只有列表读失败，其余方法照旧。"""

            def __init__(self, inner) -> None:
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def list_methods(self, **kwargs):
                raise RuntimeError("owner socket closed")

        seam = lpc.PracticeSeam(BrokenList(self.service))
        decision = seam.decision_context(query="")
        self.assertEqual(decision["methods"], [], decision)
        self.assertNotIn("withheld_methods", decision)
        self.assertIn(lpc.REASON_SERVICE_UNUSABLE, str(decision.get("last_error") or ""),
                      decision)
        self.assertEqual(seam.last_withheld_methods, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
