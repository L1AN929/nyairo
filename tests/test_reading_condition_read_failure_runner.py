#!/usr/bin/env python3
"""本组唯一目标：owner head 读失败时，正式 runner 的 NOT_DUE 维护不猜“没变/已变”。

这条测试只做一件事：调用 ``scripts/reading_condition_read_failure_runner_smoke.py`` 的
``run_smoke()`` 一次，然后对本组要证明的具名事实逐条断言。它**不**继承任何旧 fixture
TestClass，也不会顺带跑整套旧测试。

覆盖（全部在同一个隔离根里一次跑完）：
* 真实阅读材料读到 ``EXHAUSTED``（真停点），显式 offered 的
  ``READING_MATERIAL_NEXT_VERSION`` 条件让 PAUSE 真正绑定且 PENDING、前台释放；
* 另一件作品 B 正式 START 持前台，A 仍 PAUSED，资格为空；
* 两个正式 ``AutonomousLifeRunner.run_due`` **NOT_DUE** 窗口分别注入两种只针对 A 的
  owner head 读失败形态——
  ``none``（``read_head`` 返回 ``None``，生产对 ``resume_point`` 的回退也抛真
  ``ArtifactOwnerRefused``）与 ``oserror``（``read_head`` 抛 ``OSError``）；
* 两个窗口都必须：``checked=1 / changed=0 / unchanged=0 / evaluated=0``、
  ``satisfied`` 与 ``emitted_eligibilities`` 为空、``errors`` 明确含
  ``OWNER_HEAD_UNREADABLE``、``llm_calls=0``、``auto_resumed=false``、认知替身计数不增；
* 外层 ``waiting["state"] == "OK"`` 同时记录，但**不**被当成“没有错误”——
  本组同时要求 ``errors`` 非空，两者不能互相顶替；
* 每个窗口之后 A 仍 PAUSED、B 仍 ACTIVE 且唯一前台，条件仍 PENDING，资格存储整体为空；
* 恢复负控：撤掉注入后下一 NOT_DUE 窗口真实读回 baseline，``errors`` 为空、
  ``unchanged=1 / changed=0``、条件仍 PENDING、无资格；owner head 与版本数不变。

边界：Decision/内容/阅读响应是脚本替身；读取失败是测试内的窄注入，不是真实撤权、
不是停服务、不是跨备份；不是真模型自然选择、不是 G1、不是 24h，也不重跑 l 或任何旧实例。

Run:  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_reading_condition_read_failure_runner
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

import reading_condition_read_failure_runner_smoke as smoke  # noqa: E402


@POSIX_ONLY
class ReadingConditionReadFailureRunnerTest(unittest.TestCase):
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

    def test_both_unreadable_forms_were_exercised_as_declared(self) -> None:
        windows = self.summary["fault_windows"]
        self.assertEqual([row["fault_mode"] for row in windows], ["none", "oserror"], windows)
        expected = {
            "none": [{"op": "read_head", "answer": "none"},
                     {"op": "resume_point", "answer": "raised:ArtifactOwnerRefused"}],
            "oserror": [{"op": "read_head", "answer": "raised:OSError"}],
        }
        for row in windows:
            observed = [{"op": item.get("op"), "answer": item.get("answer")}
                        for item in row["owner_head_attempts"]]
            self.assertEqual(observed, expected[row["fault_mode"]], row)

    def test_an_unreadable_head_is_never_counted_as_unchanged_or_changed(self) -> None:
        windows = self.summary["fault_windows"]
        self.assertEqual(len(windows), 2, windows)
        for row in windows:
            self.assertEqual(row["status"], "NOT_DUE", row)
            self.assertEqual(row["checked"], 1, row)
            self.assertEqual(row["changed"], 0, row)
            self.assertEqual(row["unchanged"], 0, row)
            self.assertEqual(row["evaluated"], 0, row)
            self.assertEqual(row["satisfied"], [], row)
            self.assertEqual(row["emitted_eligibilities"], [], row)

    def test_the_failure_is_reported_as_an_explicit_error_not_as_silence(self) -> None:
        windows = self.summary["fault_windows"]
        for row in windows:
            # 外层 state 仍可能报 OK：本组把它和 errors 一起断言，不把 OK 读成没错误。
            self.assertEqual(row["waiting_state"], "OK", row)
            self.assertTrue(row["errors"], row)
            self.assertTrue(any(str(code).startswith("OWNER_HEAD_UNREADABLE")
                                for code in row["errors"]), row)

    def test_no_condition_no_eligibility_and_no_cognition_was_changed_by_the_failure(self) -> None:
        for row in self.summary["fault_windows"]:
            self.assertEqual(row["llm_calls"], 0, row)
            self.assertFalse(row["auto_resumed"], row)
            self.assertEqual(row["cognition_delta"], 0, row)
            self.assertEqual(row["condition_status"], "PENDING", row)
            self.assertEqual(row["eligibility_count"], 0, row)
            self.assertEqual(row["activity_a_status"], "PAUSED", row)
            self.assertEqual(row["activity_b_status"], "ACTIVE", row)
            self.assertEqual(row["foreground"], self.summary["start_b"]["activity_b"], row)

    def test_the_fault_was_removed_and_the_real_owner_read_its_baseline_again(self) -> None:
        read = self.summary["read"]
        recovery = self.summary["recovery"]
        self.assertEqual(recovery["status"], "NOT_DUE", recovery)
        self.assertEqual(recovery["waiting_state"], "OK", recovery)
        self.assertEqual(recovery["checked"], 1, recovery)
        self.assertEqual(recovery["unchanged"], 1, recovery)
        self.assertEqual(recovery["changed"], 0, recovery)
        self.assertEqual(recovery["evaluated"], 0, recovery)
        self.assertEqual(recovery["satisfied"], [], recovery)
        self.assertEqual(recovery["emitted_eligibilities"], [], recovery)
        self.assertEqual(recovery["errors"], [], recovery)
        self.assertEqual(recovery["llm_calls"], 0, recovery)
        self.assertFalse(recovery["auto_resumed"], recovery)
        self.assertEqual(recovery["cognition_delta"], 0, recovery)
        self.assertEqual(recovery["owner_head_reads"], 1, recovery)
        self.assertEqual(recovery["owner_head_value"], read["baseline_head"], recovery)
        self.assertEqual(recovery["condition_status"], "PENDING", recovery)
        self.assertEqual(recovery["eligibility_count"], 0, recovery)
        self.assertEqual(recovery["activity_a_status"], "PAUSED", recovery)
        self.assertEqual(recovery["activity_b_status"], "ACTIVE", recovery)
        self.assertEqual(recovery["foreground"], self.summary["start_b"]["activity_b"], recovery)

    def test_the_injection_left_the_owner_state_untouched(self) -> None:
        read = self.summary["read"]
        same = self.summary["same_head"]
        self.assertTrue(read["baseline_head"], read)
        self.assertEqual(same["final_head"], read["baseline_head"], same)
        self.assertEqual(same["final_versions"], read["baseline_versions"], same)


if __name__ == "__main__":
    unittest.main()
