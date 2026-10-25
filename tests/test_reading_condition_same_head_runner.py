#!/usr/bin/env python3
"""本组唯一目标：同 head 的后台 PAUSE 阅读，NOT_DUE 仍复查但不发资格。

这条测试只做一件事：调用 ``scripts/reading_condition_same_head_runner_smoke.py`` 的
``run_smoke()`` 一次，然后对本组要证明的具名事实逐条断言。它**不**继承任何旧 fixture
TestClass，也不会顺带跑整套旧测试。

覆盖（全部在同一个隔离根里一次跑完）：
* 真实阅读材料读到 ``EXHAUSTED``（真停点），显式 offered 的
  ``READING_MATERIAL_NEXT_VERSION`` 条件让 PAUSE 真正绑定且 PENDING、前台释放；
* 另一件作品 B 正式 START 持前台，A 仍 PAUSED；
* **A 的 owner head 保持 baseline**（本组从不提交 A 的新版本）；
* 连续两个正式 ``AutonomousLifeRunner.run_due`` **NOT_DUE** 窗口仍跑维护：
  ``checked=1 / changed=0 / unchanged=1``、无事件评估、无 SATISFIED、无资格、``errors``
  为空、``llm_calls=0``、不自动恢复，认知替身计数每个窗口前后不变；
* 窄 spy 证明每个窗口都真实读了一次 owner head，读到的就是 baseline（不是 ``None``
  被当成 ``unchanged``）；
* 两个窗口之后 A 仍 PAUSED、B 仍 ACTIVE 且是唯一前台，条件仍 PENDING，资格仍为空。

边界：Decision/内容/阅读响应是脚本替身；不是真模型自然选择、不是 G1、不是 24h，也不
重跑 l 或任何旧实例。时间只用注入的 Life 时刻保持既有窗口关闭，不睡墙钟。

Run:  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_reading_condition_same_head_runner
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for entry in (str(ROOT), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

import reading_condition_same_head_runner_smoke as smoke  # noqa: E402


@POSIX_ONLY
class ReadingConditionSameHeadRunnerTest(unittest.TestCase):
    """一条链只跑一次；断言只看本组要证明的事实。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = smoke.run_smoke()

    def test_the_chain_reports_ok(self) -> None:
        self.assertTrue(self.summary.get("ok"), self.summary)
        self.assertTrue(all(bool(value) for value in self.summary["checks"].values()),
                        self.summary["checks"])

    def test_reading_paused_and_another_work_holds_the_slot(self) -> None:
        read = self.summary["read"]
        self.assertEqual(read["state"], "EXHAUSTED", read)
        pause = self.summary["pause_a"]
        self.assertEqual(pause["decision"], "PAUSE", pause)
        self.assertEqual(pause["offered_source"], "READING_MATERIAL_NEXT_VERSION", pause)
        self.assertEqual(pause["condition_status"], "PENDING", pause)
        self.assertEqual(pause["eligibilities"], 0, pause)
        start_b = self.summary["start_b"]
        self.assertEqual(start_b["decision"], "START", start_b)
        self.assertEqual(start_b["activity_a_status"], "PAUSED", start_b)
        self.assertEqual(start_b["foreground"], start_b["activity_b"], start_b)

    def test_the_owner_head_stayed_at_its_baseline(self) -> None:
        read = self.summary["read"]
        same = self.summary["same_head"]
        self.assertTrue(read["baseline_head"], read)
        self.assertEqual(same["final_head"], read["baseline_head"], same)
        self.assertEqual(same["final_versions"], read["baseline_versions"], same)

    def test_every_not_due_window_rechecks_the_same_head_without_an_event(self) -> None:
        windows = self.summary["not_due_windows"]
        self.assertEqual(len(windows), 2, windows)
        for row in windows:
            self.assertEqual(row["status"], "NOT_DUE", row)
            self.assertEqual(row["waiting_state"], "OK", row)
            self.assertEqual(row["checked"], 1, row)
            self.assertEqual(row["changed"], 0, row)
            self.assertEqual(row["unchanged"], 1, row)
            self.assertEqual(row["evaluated"], 0, row)
            self.assertEqual(row["satisfied"], [], row)
            self.assertEqual(row["emitted_eligibilities"], [], row)
            self.assertEqual(row["errors"], [], row)
            self.assertEqual(row["llm_calls"], 0, row)
            self.assertFalse(row["auto_resumed"], row)

    def test_the_real_owner_head_was_read_once_per_window(self) -> None:
        read = self.summary["read"]
        for row in self.summary["not_due_windows"]:
            self.assertEqual(row["owner_head_reads"], 1, row)
            self.assertEqual(row["owner_head_value"], read["baseline_head"], row)

    def test_no_eligibility_no_resume_and_no_cognition_was_spent(self) -> None:
        for row in self.summary["not_due_windows"]:
            self.assertEqual(row["cognition_delta"], 0, row)
            self.assertEqual(row["eligibility_count"], 0, row)
            self.assertEqual(row["condition_status"], "PENDING", row)
            self.assertEqual(row["activity_a_status"], "PAUSED", row)
            self.assertEqual(row["activity_b_status"], "ACTIVE", row)
            self.assertEqual(row["foreground"], self.summary["start_b"]["activity_b"], row)


if __name__ == "__main__":
    unittest.main()
