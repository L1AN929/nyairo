#!/usr/bin/env python3
"""G1 下一次限定采用：离线 harness 的最小单元回归。

完整行为链由 ``scripts/g1_next_offline.py --offline-check`` 自己跑；这里只钉住三件
最容易悄悄退化的判据：A/actA 锚、反例必须 NOT_COVERED、真实入口必须拒绝且不写文件。
不重复跑整条链，不访问任何旧实例根。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import posix_only  # noqa: E402


def _good_p1() -> dict:
    return {
        "artifact_a": "artifact:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "artifact_b": "artifact:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "activity_ref": "actv_anchor",
        "pid": 1001,
        "run_id": "run:anchor",
        "first_outcome": "ACTED",
        "first_owner_receipt": True,
        "first_owner_version": True,
        "first_action_id": "actn:first",
        "head_at_end": "artifact-version:11111111111111111111111111111111",
        "versions_before_exit": 2,
        "memory_rows_at_end": 1,
        "budget_after_first": {"cognition_used_today": 1, "spent_today": 1},
    }


def _good_p2() -> dict:
    return {
        "artifact_a": "artifact:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "artifact_b": "artifact:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "activity_ref": "actv_anchor",
        "pid": 2002,
        "run_id": "run:anchor",
        "head_at_start": "artifact-version:11111111111111111111111111111111",
        "versions_at_start": 2,
        "memory_at_start": 1,
        "budget_at_start": {"cognition_used_today": 1, "spent_today": 1},
        "paused_round": {"outcome": "PAUSED_BY_OPERATOR", "llm_calls": 0},
        "versions_after_continue_1": 3,
        "rounds": {
            "g1next:p2:continue-1": {
                "outcome": "ACTED",
                "decision": "CONTINUE",
                "action_id": "actn:second",
                "artifact_ref": "artifact:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "activity_ref": "actv_anchor",
                "owner_version_created": True,
                "owner_version_id": "artifact-version:22222222222222222222222222222222",
            }
        },
    }


@posix_only
class G1NextOfflineCriteriaTest(unittest.TestCase):
    def setUp(self):
        from g1_next_offline import _evaluate_next_g1, _evaluator_negatives

        self.evaluate = _evaluate_next_g1
        self.negatives = _evaluator_negatives
        self.p1 = _good_p1()
        self.p2 = _good_p2()

    def test_the_anchor_continuation_is_the_only_covered_case(self):
        verdict = self.evaluate(self.p1, self.p2)
        self.assertEqual(verdict["result"], "COVERED")
        self.assertTrue(verdict["core_covered"])
        # 离线替身永远不冒充真实 G1 证据。
        self.assertEqual(verdict["real_g1_evidence"], "NOT_COVERED")
        self.assertEqual(verdict["reason"], "OFFLINE_SCRIPTED_STANDIN")

    def test_wrong_artifact_wrong_activity_and_replay_are_not_covered(self):
        rows = self.negatives(self.p1, self.p2)
        self.assertEqual(rows["baseline"], "COVERED")
        for name in ("wrong_artifact", "wrong_activity",
                     "replayed_old_success", "no_model_choice"):
            self.assertEqual(rows[name], "NOT_COVERED", name)

    def test_a_write_on_another_activity_does_not_advance(self):
        p2 = _good_p2()
        p2["rounds"]["g1next:p2:continue-1"]["activity_ref"] = "actv_elsewhere"
        self.assertNotEqual(self.evaluate(self.p1, p2)["result"], "COVERED")

    def test_a_reset_budget_does_not_count_as_the_same_run(self):
        p2 = _good_p2()
        p2["budget_at_start"] = {"cognition_used_today": 0, "spent_today": 0}
        self.assertEqual(self.evaluate(self.p1, p2)["result"], "NOT_COVERED")


@posix_only
class G1NextOfflineEntryTest(unittest.TestCase):
    def test_real_entry_is_refused_and_writes_nothing(self):
        # 数据保留：用 mkdtemp 而不是 TemporaryDirectory，测试结束不删。
        cwd = tempfile.mkdtemp(prefix="g1-next-offline-refusal-")
        for flags in (["--execute-approved"], ["--run", "--execute-approved"]):
            done = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts" / "g1_next_offline.py"),
                 *flags],
                capture_output=True, text=True, timeout=120, cwd=cwd,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(done.returncode, 2, (done.returncode, done.stderr))
            payload = json.loads(done.stdout)
            self.assertEqual(payload["reason_code"], "OFFLINE_ONLY_NO_REAL_ENTRY")
            self.assertEqual(payload["files_written"], [])
        self.assertEqual(os.listdir(cwd), [])


if __name__ == "__main__":
    unittest.main()
