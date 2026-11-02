#!/usr/bin/env python3
"""PAUSE 条件提示文案：成功 PAUSE 释放执行槽，条件满足只发资格。

这个文件只钉住 ``AutonomousLifeCycle.pause_condition_view`` 这一处纯描述输出的形状与措辞：
可绑条件的 PAUSE 成功后会释放执行槽（另一件与此同时可以开始），条件满足只产生 OPEN 资格、
不自动恢复，回来仍要新的 Decision 选 RESUME，后台 RESUME 不抢占另一个前台活动。没有 basis 时
沿用原有 ``available: false`` 与原因码，不加“永远回不来”这类绝对断言。

不涉及资格生成、排序、TTL、owner 或恢复方案；字段键与结构保持原样，只有
``keeps_foreground`` 的描述值随已批准的“释放前台”规则由 True 改为 False。

Run:  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_paused_condition_copy
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
NATIVE = ROOT / "components" / "native"
sys.path[:0] = [
    str(ROOT),
    str(LIFE_SCRIPTS),
    str(NATIVE),
    str(NATIVE / "src"),
    str(NATIVE / "memory_runtime_v1"),
]

import autonomous_life_cycle as alc  # noqa: E402
import life_future_items as lfi  # noqa: E402
import life_wait_basis as lwb  # noqa: E402

BODY_BASIS = {
    "basis_ref": "waitbasis:body_rest",
    "source": lwb.SOURCE_BODY_REST,
    "activity_ref": "actv:copy_body",
    "waiting_on_ref": "body:copy",
}
WORK_BASIS = {
    "basis_ref": "waitbasis:work_head",
    "source": lwb.SOURCE_READING,
    "activity_ref": "actv:copy_work",
    "waiting_on_ref": "artifact:copy_work",
}
PARK_BASIS = {
    "basis_ref": "waitbasis:park",
    "source": lfi.PARK_SOURCE,
    "activity_ref": "actv:copy_park",
    "waiting_on_ref": "later:copy_park",
}


class PauseConditionCopyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="nyairo-pause-copy-")
        self.cycle = alc.AutonomousLifeCycle(
            SimpleNamespace(state_root=tmp), enabled=False)

    def test_available_view_says_slot_released_and_never_auto_resumes(self):
        for basis in (BODY_BASIS, WORK_BASIS, PARK_BASIS):
            with self.subTest(source=basis["source"]):
                view = self.cycle.pause_condition_view(basis)
                self.assertTrue(view["available"])
                self.assertEqual(view["schema_version"], "chiyo.life.pause_condition.v1")
                self.assertEqual(view["condition_ref"], basis["basis_ref"])
                self.assertEqual(view["source"], basis["source"])
                self.assertIs(view["keeps_foreground"], False)
                self.assertIn("releases the execution slot", view["how"])
                self.assertIn("RESUME", view["how"])
                self.assertIn("never preempts", view["how"])
                self.assertTrue(view["not_automatic"])

    def test_work_basis_waits_for_a_new_owner_head(self):
        view = self.cycle.pause_condition_view(WORK_BASIS)
        self.assertIn("new head version", view["satisfied_by"])
        self.assertNotIn("default timer", view["satisfied_by"])

    def test_park_basis_uses_only_the_instant_the_decision_names(self):
        view = self.cycle.pause_condition_view(PARK_BASIS)
        self.assertIn("no default timer", view["satisfied_by"])
        self.assertIn("park_current=true", view["how"])

    def test_no_basis_stays_unavailable_without_a_foreground_claim(self):
        for basis in (None, {}, {"basis_ref": ""}):
            with self.subTest(basis=basis):
                view = self.cycle.pause_condition_view(basis)
                self.assertFalse(view["available"])
                self.assertEqual(view["why_not"], "NO_OFFERED_CONDITION")
                self.assertNotIn("condition_ref", view)
                self.assertNotIn("keeps_foreground", view)

    def test_docstring_no_longer_claims_pause_keeps_the_foreground(self):
        doc = alc.AutonomousLifeCycle.pause_condition_view.__doc__ or ""
        self.assertNotIn("a PAUSE keeps the foreground", doc)
        self.assertIn("releases the execution slot", doc)


if __name__ == "__main__":
    unittest.main()
