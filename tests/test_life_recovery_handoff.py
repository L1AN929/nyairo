"""经历交接纳入显式恢复入口的定点测试。

主链在 ``scripts/life_recovery_handoff_demo.py``（真实 CLI 子进程、新隔离根、跨 PID）。
这里只覆盖恢复入口自己的状态判定：

1. 没有待投递 handoff 时，状态完全沿用 Action 侧的结论；
2. Action 已成功但原 handoff 只是被重投/延迟/仍 pending 时，不得回答
   NOTHING_TO_DO 或 RECOVERED；
3. 同一次恢复真的把原 handoff 交付完，可以从 NOTHING_TO_DO 变成 RECOVERED；
4. 文本入口把待重试交接的 id 如实列出。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import infra_runtime_recovery_cli as cli  # noqa: E402
import runtime_recovery_review as review  # noqa: E402


class HandoffStatusTest(unittest.TestCase):
    def test_no_handoff_keeps_the_action_verdict(self) -> None:
        handoff = review._handoff_state({"dispatched": 0, "delivered": 0})
        self.assertFalse(handoff["attempted"])
        for action_status in (review.STATUS_NOTHING_TO_DO, review.STATUS_RECOVERED,
                              review.STATUS_PARTIAL, review.STATUS_STILL_UNKNOWN):
            self.assertEqual(review._decide_status(action_status, handoff), action_status)

    def test_action_succeeded_but_handoff_deferred_is_not_nothing_to_do(self) -> None:
        handoff = review._handoff_state({
            "dispatched": 1, "delivered": 0, "deferred": 1,
            "pending_handoff_ids": ["lexp:result:abc"]})
        self.assertTrue(handoff["attempted"])
        self.assertFalse(handoff["settled"])
        self.assertEqual(review._decide_status(review.STATUS_NOTHING_TO_DO, handoff),
                         review.STATUS_PARTIAL)
        self.assertEqual(review._decide_status(review.STATUS_RECOVERED, handoff),
                         review.STATUS_PARTIAL)

    def test_still_pending_handoff_without_attempt_is_partial(self) -> None:
        handoff = review._handoff_state({
            "dispatched": 0, "delivered": 0,
            "pending_handoff_ids": ["lexp:result:abc"]})
        self.assertTrue(handoff["attempted"])
        self.assertEqual(review._decide_status(review.STATUS_NOTHING_TO_DO, handoff),
                         review.STATUS_PARTIAL)

    def test_delivered_handoff_turns_nothing_to_do_into_recovered(self) -> None:
        handoff = review._handoff_state({
            "dispatched": 1, "delivered": 1, "deferred": 0,
            "consumer_dispositions": {"ACCEPT": 1}, "pending_handoff_ids": []})
        self.assertTrue(handoff["settled"])
        self.assertEqual(review._decide_status(review.STATUS_NOTHING_TO_DO, handoff),
                         review.STATUS_RECOVERED)

    def test_partial_action_stays_partial_either_way(self) -> None:
        settled = review._handoff_state({"dispatched": 1, "delivered": 1})
        pending = review._handoff_state({
            "dispatched": 1, "delivered": 0, "failed_pending_retry": 1,
            "pending_handoff_ids": ["lexp:result:abc"]})
        self.assertEqual(review._decide_status(review.STATUS_PARTIAL, settled),
                         review.STATUS_PARTIAL)
        self.assertEqual(review._decide_status(review.STATUS_PARTIAL, pending),
                         review.STATUS_PARTIAL)

    def test_text_entry_lists_pending_handoff_ids_only(self) -> None:
        text = cli._recover_text({
            "instance": "/tmp/root", "status": review.STATUS_PARTIAL,
            "result_delivery": {"delivered": 0, "deferred": 1,
                                "failed_pending_retry": 0,
                                "pending_handoff_ids": ["lexp:result:abc"]},
        })
        self.assertIn("lexp:result:abc", text)
        self.assertIn("待重试交接", text)
        self.assertNotIn("正文", text)


if __name__ == "__main__":
    unittest.main()
