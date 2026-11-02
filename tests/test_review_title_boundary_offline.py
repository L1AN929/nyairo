#!/usr/bin/env python3
"""离线实测脚本的最小检查：标题收敛、同标题去重、相近不相等。

这里只调用 ``scripts/review_title_boundary_offline.py`` 的层次函数，全部是脚本合成的
测试数据；不启动服务、不读实例根、不调模型。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import review_title_boundary_offline as offline  # noqa: E402


class ReviewTitleBoundaryOfflineTest(unittest.TestCase):

    def test_string_recursion_reaches_a_stable_title_at_the_limit(self) -> None:
        result = offline.run_string_recursion(offline.SYNTH_SHORT_TITLE)
        self.assertTrue(result["converged"])
        self.assertEqual(result["final_len"], offline.TITLE_LIMIT)
        self.assertLessEqual(int(result["rounds_to_converge"]), 32)

    def test_real_projection_matches_the_string_recursion(self) -> None:
        string = offline.run_string_recursion(offline.SYNTH_LONG_TITLE)
        projected = offline.run_real_projection(offline.SYNTH_LONG_TITLE)
        self.assertTrue(projected["converged"])
        self.assertEqual(projected["rounds_to_converge"],
                         string["rounds_to_converge"])
        self.assertEqual(projected["final_len"], string["final_len"])

    def test_controls_show_positive_and_negative_dedupe(self) -> None:
        controls = offline.run_controls()
        self.assertTrue(controls["positive_review_skipped"])
        self.assertTrue(controls["negative_review_emitted"])
        self.assertTrue(controls["near_title_differs_from_wanted"])

    def test_owner_title_dedupes_and_other_kinds_still_return(self) -> None:
        owner = offline.run_owner_dedupe(offline.SYNTH_SHORT_TITLE)
        self.assertTrue(owner["dedupe_hit"])
        self.assertTrue(owner["owner_title_sourced"])
        self.assertGreater(owner["created_reviews"], 0)
        self.assertIn("QUESTION_FROM_MATERIAL", owner["kinds_when_skipped"])
        self.assertFalse(owner["existing_project_emitted_as_option"])


if __name__ == "__main__":
    unittest.main()
