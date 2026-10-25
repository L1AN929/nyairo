#!/usr/bin/env python3
"""本组唯一目标：新 head -> 正式 runner NOT_DUE 维护 -> 资格 OPEN -> 回候选。

这条测试只做一件事：调用 ``scripts/reading_condition_new_head_runner_smoke.py`` 的
``run_smoke()`` 一次，然后对本组要证明的具名事实逐条断言。它**不**继承任何旧 fixture
TestClass，也不会顺带跑整套旧测试。

覆盖（全部在同一个隔离根里一次跑完）：
* 真实阅读材料读到 ``EXHAUSTED``（真停点），显式 offered 的
  ``READING_MATERIAL_NEXT_VERSION`` 条件让 PAUSE 真正绑定且 PENDING、前台释放；
* 另一件作品 B 正式 START 持前台，A 仍 PAUSED；
* 真 Supply owner 提交 A 的新 head 后，正式 ``AutonomousLifeRunner.run_due`` 本窗
  ``NOT_DUE`` 仍跑维护：条件 SATISFIED、恰好一条 OPEN 资格、认知替身计数不增；
* B 执行期间 A 的 RESUME 不可执行（候选面里没有），A/资格不消费、B 不被打断；
* B 合法 PAUSE 释放前台后，A 以 ``RESUME_ELIGIBLE`` 回到候选；脚本 Decision 选
  NO_ACTION 时 A 保持 PAUSED、资格保持 OPEN。

边界：Decision/内容/阅读响应是脚本替身；不是真模型自然选择、不是 G1、不是 24h，也不
重跑 l 或任何旧实例。只读/推进的都是注入的 Life 时刻，不睡墙钟。

Run:  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_reading_condition_new_head_runner
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

import reading_condition_new_head_runner_smoke as smoke  # noqa: E402


@POSIX_ONLY
class ReadingConditionNewHeadRunnerTest(unittest.TestCase):
    """一条链只跑一次；断言只看本组要证明的事实。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = smoke.run_smoke()

    def test_the_chain_reports_ok(self) -> None:
        self.assertTrue(self.summary.get("ok"), self.summary)
        self.assertTrue(all(bool(value) for value in self.summary["checks"].values()),
                        self.summary["checks"])

    def test_reading_paused_on_its_own_exhausted_revision(self) -> None:
        read = self.summary["read"]
        self.assertEqual(read["state"], "EXHAUSTED", read)
        pause = self.summary["pause_a"]
        self.assertEqual(pause["decision"], "PAUSE", pause)
        self.assertEqual(pause["offered_source"], "READING_MATERIAL_NEXT_VERSION", pause)
        self.assertEqual(pause["condition_status"], "PENDING", pause)

    def test_another_work_runs_while_a_is_held(self) -> None:
        start_b = self.summary["start_b"]
        self.assertEqual(start_b["decision"], "START", start_b)
        self.assertEqual(start_b["foreground"], start_b["activity_b"], start_b)
        conflict = self.summary["background_conflict"]
        self.assertFalse(conflict["resume_offered"], conflict)
        self.assertNotEqual(conflict["decision"], "RESUME", conflict)
        self.assertEqual(conflict["activity_a_status"], "PAUSED", conflict)
        self.assertEqual(conflict["activity_b_status"], "ACTIVE", conflict)
        self.assertEqual(conflict["eligibility_status"], "OPEN", conflict)

    def test_not_due_window_still_maintains_without_a_cognition_call(self) -> None:
        waiting = self.summary["not_due_maintenance"]
        self.assertEqual(waiting["status"], "NOT_DUE", waiting)
        self.assertEqual(waiting["waiting_state"], "OK", waiting)
        self.assertEqual(waiting["changed"], 1, waiting)
        self.assertEqual(waiting["llm_calls"], 0, waiting)
        self.assertEqual(waiting["cognition_delta"], 0, waiting)
        self.assertFalse(waiting["auto_resumed"], waiting)
        self.assertEqual(waiting["condition_status"], "SATISFIED", waiting)
        self.assertEqual(len(waiting["satisfied"]), 1, waiting)
        self.assertEqual(len(waiting["emitted_eligibilities"]), 1, waiting)
        self.assertEqual(waiting["eligibility_status"], "OPEN", waiting)

    def test_the_freed_slot_returns_a_as_candidate_not_as_a_resume(self) -> None:
        pause_b = self.summary["pause_b"]
        self.assertEqual(pause_b["decision"], "PAUSE", pause_b)
        self.assertEqual(pause_b["foreground"], None, pause_b)
        returned = self.summary["return_candidate"]
        self.assertEqual(returned["candidate_kind"], "RESUME_ACTIVITY", returned)
        self.assertEqual(returned["source_kind"], "RESUME_ELIGIBLE", returned)
        self.assertTrue(returned["target_artifact_matches_a"], returned)
        self.assertEqual(returned["decision"], "NO_ACTION", returned)
        self.assertEqual(returned["activity_a_status"], "PAUSED", returned)
        self.assertEqual(returned["eligibility_status"], "OPEN", returned)
        self.assertEqual(returned["foreground"], None, returned)


if __name__ == "__main__":
    unittest.main()
