#!/usr/bin/env python3
"""``scripts/nyairo_day.py`` 的直接相关小测：入口文字、边界和默认值。

不建实例、不起服务、不调用模型；真实一遍使用流程在 WSL 里由验收命令跑。
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def _entry():
    return importlib.import_module("nyairo_day")


class NyairoDayEntryTest(unittest.TestCase):
    def test_default_memory_authority_is_outside_the_instance(self) -> None:
        entry = _entry()
        root = Path("/root/nyairo-day")
        authority = entry._default_authority(root)
        self.assertEqual(authority, Path("/root/nyairo-day-memory-authority/authority.db"))
        self.assertNotIn(root, authority.parents)
        self.assertFalse(str(authority).startswith(str(root) + "/"))

    def test_human_reasons_are_plain_chinese_and_keep_the_code(self) -> None:
        entry = _entry()
        self.assertIn("换一个还不存在的目录", entry._human("ROOT_NOT_EMPTY"))
        self.assertIn("没有发任何请求", entry._human("NO_MODEL_PROVIDER"))
        unknown = entry._human("SOMETHING_ELSE", "detail")
        self.assertIn("SOMETHING_ELSE", unknown)
        self.assertIn("detail", unknown)

    def test_start_text_says_ready_but_not_living(self) -> None:
        entry = _entry()
        text = entry._start_text(Path("/root/nyairo-day"), 3831, already=False)
        self.assertIn("服务就绪，尚未开始自主活动。", text)
        self.assertIn("/api/phone/today", text)
        self.assertIn("/api/phone/today/arrangements", text)
        self.assertIn("/api/phone/projects", text)

    def test_run_day_requires_the_explicit_live_model_switch(self) -> None:
        entry = _entry()
        args = entry.build_parser().parse_args([
            "run-day", "--root", "/root/nyairo-day",
            "--duration-seconds", "120", "--max-new-requests", "1"])
        self.assertFalse(args.live_model)
        self.assertEqual(args.duration_seconds, 120)
        self.assertEqual(args.max_new_requests, 1)
        with_switch = entry.build_parser().parse_args([
            "run-day", "--root", "/root/nyairo-day", "--live-model",
            "--duration-seconds", "120", "--max-new-requests", "1"])
        self.assertTrue(with_switch.live_model)

    def test_status_text_is_chinese_and_names_unbound_reading(self) -> None:
        entry = _entry()
        view = {
            "sentence": "现在没有在运行，也没有读到上次的停点。",
            "where": {"status": "UNKNOWN", "unknowns": ["live_owner_unread"]},
            "work": {},
            "reading": {},
            "memory": {"binding_status": "READY", "recall_ready": True,
                       "experience_count": 0},
            "budget": {"utc_day_calls": 0, "utc_day_max": 96,
                       "run_window_calls": 0, "run_window_max": 96},
            "next": {},
        }
        today = {"ok": True, "data": {"day": "2026-10-26", "activity_count": 0,
                                       "work_count": 0}}
        arrangements = {"ok": True, "data": {"plan": {"count": 0},
                                             "obligations": {"count": 0}}}
        projects = {"ok": True, "projects": []}
        text = entry._status_text(Path("/root/nyairo-day"), view, today, arrangements,
                                  projects, {})
        self.assertIn("此刻：", text)
        self.assertIn("今天（2026-10-26）：活动 0 件，作品版本 0 件", text)
        self.assertIn("阅读：还没有绑定材料", text)
        self.assertIn("0/96", text)

    def test_relative_root_is_refused_before_any_write(self) -> None:
        entry = _entry()
        with self.assertRaises(RuntimeError) as caught:
            entry._ensure_instance(Path("nyairo-day"))
        self.assertEqual(str(caught.exception), "ROOT_NOT_ABSOLUTE")

    def test_managed_marker_gates_adoption(self) -> None:
        entry = _entry()
        root = Path(tempfile.mkdtemp(prefix="nyairo-day-entry-"))
        (root / "run").mkdir(parents=True, exist_ok=True)
        self.assertFalse(entry._is_managed(root))
        (root / "run" / entry.MANAGED_NAME).write_text(
            json.dumps({"schema": entry.SCHEMA_MANAGED}), encoding="utf-8")
        self.assertTrue(entry._is_managed(root))


if __name__ == "__main__":
    unittest.main()
