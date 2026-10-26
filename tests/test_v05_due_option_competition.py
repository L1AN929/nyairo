#!/usr/bin/env python3
"""v0.5：无对象到期事项在 review/question 占满两个名额时是否被挤出（只做最小正式反例）。

本模块只守这一块：

1. 纯域：``life_project_options.build_options`` 当前默认 ``MAX_OPTIONS = 2``，构造顺序是
   review → question → due，最后 ``options[:limit]``；因此前两类各一条时，第三条无对象
   due 选项被裁掉。这里只复述静态规则，不当作正式链证据。
2. 真 owner 正式链（POSIX）：隔离真 Supply + 真 M0 Memory（正常准入消费者）+ B 的
   practice/private-project gateway + 真实 private items + 正式 ``AutonomousLifeRunner``
   check-window + AG-0/AG-1 原始 JSON/AG-2/AR-0。脚本 Decision 与内容回答是替身，明示。
   对照两场景：
   ① 只有该无对象 due 时，最终请求确有对应 ``popt:`` 候选且可 START；
   ② review + question 真候选占满两名额时，due 的 ``popt:`` 不在源选项也不在最终请求里，
      但事项仍以 OPEN/due 只读可见；带作品对象的另一条 due 仍有独立 ``fitem:`` 候选。

不修改业务规则、不默认改名额、不声称「永远饥饿」或真实模型自然采用。脚本 Decision 选择
NO_ACTION 不构成实现失败；测试也不强迫主体必须选 due。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_project_options as lpo  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: str | None = None
if os.name == "posix":  # pragma: no branch - native Windows skips the class below
    try:
        import v05_due_option_competition_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        driver = None
else:  # pragma: no cover
    driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


class StaticOptionRuleTest(unittest.TestCase):
    """当前默认上限与构造顺序的纯域复述；不是正式链证据。"""

    @staticmethod
    def _inputs():
        experiences = [{
            "experience_id": "experience:static-1",
            "occurred_at": "2026-11-10T09:00:00+00:00",
            "excerpt": "上次写到这里停住了",
        }]
        questions = [{
            "marker_id": "unclear:static-1",
            "note": "为什么会卡住？",
            "source_id": "artifact:static-source",
            "source_revision": "1",
        }]
        due = [{"item_ref": "fitem:static-due", "title": "把水井那段补记一笔",
                "due_at": "2026-11-10T09:05:00+00:00"}]
        return experiences, questions, due

    def test_the_default_limit_is_two(self):
        self.assertEqual(lpo.MAX_OPTIONS, 2)

    def test_review_question_then_due_and_the_clip(self):
        experiences, questions, due = self._inputs()
        clipped = lpo.build_options(questions=questions, due_items=due,
                                    experiences=experiences, practice_available=True,
                                    limit=lpo.MAX_OPTIONS)
        self.assertEqual([row["kind"] for row in clipped],
                         [lpo.KIND_REVIEW, lpo.KIND_QUESTION])
        unclipped = lpo.build_options(questions=questions, due_items=due,
                                      experiences=experiences, practice_available=True,
                                      limit=lpo.MAX_OPTIONS + 1)
        self.assertEqual([row["kind"] for row in unclipped],
                         [lpo.KIND_REVIEW, lpo.KIND_QUESTION, lpo.KIND_FUTURE_ITEM])
        self.assertEqual(unclipped[-1]["basis_ref"], "fitem:static-due")

    def test_only_a_due_item_still_fits(self):
        _experiences, _questions, due = self._inputs()
        options = lpo.build_options(due_items=due, limit=lpo.MAX_OPTIONS)
        self.assertEqual([row["kind"] for row in options], [lpo.KIND_FUTURE_ITEM])
        self.assertEqual(options[0]["basis_ref"], "fitem:static-due")


@POSIX_ONLY
@needs_fixture
class RealOwnerOptionCompetitionTest(unittest.TestCase):
    """正式链：对照「只有 due」与「review+question 占满」两种布置。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 一次真实链同时给出两场景；失败时保留具名 blocker，不修改条件求绿。
        cls.facts = driver.run_competition("unittest")

    def test_no_blocker_was_reported(self):
        self.assertNotIn("blocker", self.facts, self.facts.get("blocker"))

    def test_control_round_offers_the_due_option_and_start_is_legal(self):
        facts = self.facts
        control = facts["control"]
        refs = facts["refs"]
        self.assertTrue(control["asked"])
        self.assertEqual(control["status"], "NO_ACTION")
        self.assertEqual(control["decision"], "NO_ACTION")
        self.assertIn(refs["item_a"], control["due_refs"])
        self.assertEqual([row["option_ref"] for row in control["options"]],
                         [refs["option_a"]])
        self.assertEqual(control["options"][0]["kind"], lpo.KIND_FUTURE_ITEM)
        self.assertEqual(control["options"][0]["basis_ref"], refs["item_a"])
        # 同一真实 due 行交给生产纯函数（limit=1）也得到同一个选项：来源本身可用。
        self.assertEqual([row["option_ref"] for row in control["due_option_from_source"]],
                         [refs["option_a"]])
        matches = [row for row in control["candidates"]
                   if row["source_ref"] == refs["option_a"]]
        self.assertEqual(len(matches), 1, control["candidates"])
        self.assertIn("START", matches[0]["legal_decisions"])
        self.assertEqual(control["item_a_status"], "OPEN")

    def test_competition_round_clips_the_due_option_from_options_and_request(self):
        facts = self.facts
        comp = facts["competition"]
        refs = facts["refs"]
        self.assertTrue(comp["asked"])
        self.assertEqual(comp["status"], "NO_ACTION")
        self.assertEqual(comp["decision"], "NO_ACTION")
        # 事项仍到期且只读可见，没有被 NO_ACTION 取消/完成。
        self.assertIn(refs["item_a"], comp["due_refs"])
        self.assertIn(refs["item_b"], comp["due_refs"])
        self.assertIn(refs["item_a"], comp["due_view"]["due_refs"])
        self.assertEqual(comp["item_a_status"], "OPEN")
        self.assertEqual(comp["item_b_status"], "OPEN")
        # 源选项里没有以 A 的 item_ref 为依据的选项；最终允许集的 popt 恰好只有那两个
        # 选项，所以 A 对应的 popt 也不在请求里（按 basis/source_ref，不做字符串扫描）。
        self.assertFalse(comp["a_option_in_source"])
        self.assertFalse(comp["a_item_in_candidates"])
        self.assertEqual(comp["popt_sources"], comp["option_refs"])
        # 两个名额确实由 review + question 占据。
        kinds = [row["kind"] for row in comp["options"]]
        self.assertEqual(len(comp["options"]), lpo.MAX_OPTIONS)
        self.assertIn(lpo.KIND_REVIEW, kinds)
        self.assertTrue(any(kind != lpo.KIND_REVIEW and kind != lpo.KIND_FUTURE_ITEM
                            for kind in kinds), kinds)
        # 带作品对象的另一条 due 拥有独立 fitem 候选，不随 popt 名额一起消失。
        fitem = comp["fitem_b_candidate"]
        self.assertIsNotNone(fitem)
        self.assertEqual(fitem["source_ref"], refs["item_b"])
        self.assertIn(refs["seed_artifact"], fitem["target_refs"])
        # 无被动写：没有内容写、作品头不变、前台不变。
        self.assertEqual(comp["writer_calls_delta"], 0)
        self.assertTrue(comp["seed_head_same"])
        self.assertTrue(comp["foreground_same"])
        # 裁剪来自上限，不是来源缺失：源侧可构成 2 条已展示 + 2 条到期。
        self.assertEqual(comp["limit"], lpo.MAX_OPTIONS)
        self.assertTrue(comp["clipped_derived"])
        self.assertGreater(comp["pre_clip_derived"], comp["limit"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
