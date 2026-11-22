#!/usr/bin/env python3
"""Ch.49 一致性回归：一天可以有方向，但不应提前发生。

覆盖四件事：
1. 设可选方向后，``completed_items`` 仍为空，不补写成已发生，不记亏欠；
2. ``start_step`` 只进入当下一步，``interrupt`` 保留已写草稿且不消耗其余可选方向；
3. ``offline_pause`` 诚实说明"没有连着发生"，不把任何条目转入已完成；
4. 越权仍 403，读取 100% 零写入且 ``llm_calls == 0``。
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v04_elastic_play_learning as ep_engine  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402


def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    out: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            out[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            out[rel] = (0, int(st.st_mtime_ns), "dir")
    return out


class Ch49DirectionConsistencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-ch49-direction-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_planned_direction_is_not_happened_yet(self) -> None:
        code, body = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "set",
                "direction": "上午先写点手稿",
                "optional_directions": ["上午先写点手稿", "午后翻两页书"],
            },
            actor_side="subject",
            now_iso="2026-11-17T09:00:00+08:00",
        )
        self.assertEqual(code, 200)
        dd = body["data"]["day_direction"]
        self.assertEqual(dd["direction_stage"], "planned_or_optional")
        self.assertEqual(dd["completed_items"], [])
        self.assertFalse(dd["backfilled_to_diary"])
        self.assertEqual(dd["guilt_debt"], 0)
        self.assertEqual(dd["optional_directions"], ["上午先写点手稿", "午后翻两页书"])
        self.assertFalse(dd["pre_scheduled"])

    def test_02_start_step_then_interrupt_keeps_draft_and_options(self) -> None:
        code, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "set",
                "direction": "上午先写点手稿",
                "optional_directions": ["上午先写点手稿", "午后翻两页书", "傍晚收一下桌面"],
            },
            actor_side="subject",
            now_iso="2026-11-17T09:00:00+08:00",
        )
        self.assertEqual(code, 200)

        # 显式进入当下一步：只激活 active_step_ref，不写已完成、不消耗可选方向。
        code, body = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "start_step",
                "active_step_ref": "step-draft-p2",
                "current_focus": "先写第二段开头",
            },
            actor_side="subject",
            now_iso="2026-11-17T09:30:00+08:00",
        )
        self.assertEqual(code, 200)
        dd = body["data"]["day_direction"]
        self.assertEqual(dd["direction_stage"], "in_progress")
        self.assertEqual(dd["status"], "active")
        self.assertEqual(dd["active_step_ref"], "step-draft-p2")
        self.assertEqual(dd["current_focus"], "先写第二段开头")
        self.assertEqual(dd["completed_items"], [])
        self.assertEqual(
            dd["optional_directions"],
            ["上午先写点手稿", "午后翻两页书", "傍晚收一下桌面"],
        )
        self.assertEqual(dd["guilt_debt"], 0)

        # 打断时把已写草稿完整留下，其余可选方向一条都不丢。
        draft = "手稿第二段写了一半"
        code, body = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "interrupt",
                "interrupt_reason": "伴侣来聊天，顺势把笔放下",
                "preserve_draft_note": draft,
            },
            actor_side="subject",
            now_iso="2026-11-17T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        dd = body["data"]["day_direction"]
        self.assertEqual(dd["direction_stage"], "interrupted_preserved")
        self.assertEqual(dd["status"], "yielded_naturally")
        self.assertEqual(dd["guilt_debt"], 0)
        self.assertFalse(dd["backfilled_to_diary"])
        self.assertEqual(dd["completed_items"], [])
        self.assertEqual(
            dd["optional_directions"],
            ["上午先写点手稿", "午后翻两页书", "傍晚收一下桌面"],
        )
        notes = dd["preserved_work_notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note"], draft)
        self.assertEqual(notes[0]["note"], "手稿第二段写了一半")
        self.assertEqual(notes[0]["stage"], "interrupted")

        # 再打断一次，草稿仍是完整原文，且只保留最近 8 条。
        for idx in range(9):
            code, _ = ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {
                    "action": "set_day_direction",
                    "mode": "interrupt",
                    "interrupt_reason": f"第 {idx} 次顺手停下",
                    "preserve_draft_note": f"草稿片段 {idx}",
                },
                actor_side="subject",
                now_iso=f"2026-11-17T10:{idx + 1:02d}:00+08:00",
            )
            self.assertEqual(code, 200)
        _, view = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=True
        )
        notes = view["data"]["day_direction"]["preserved_work_notes"]
        self.assertEqual(len(notes), 8)
        self.assertEqual(notes[-1]["note"], "草稿片段 8")

    def test_03_offline_pause_is_honest_and_not_backfilled(self) -> None:
        code, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "set",
                "direction": "上午把信写完",
                "optional_directions": ["上午把信写完", "下午去河堤走走"],
            },
            actor_side="subject",
            now_iso="2026-11-17T09:00:00+08:00",
        )
        self.assertEqual(code, 200)

        code, body = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "offline_pause",
                "offline_gap_hours": 6,
            },
            actor_side="subject",
            now_iso="2026-11-17T15:00:00+08:00",
        )
        self.assertEqual(code, 200)
        dd = body["data"]["day_direction"]
        self.assertEqual(dd["direction_stage"], "offline_paused")
        self.assertIn("没有连着发生", dd["offline_gap_notice"])
        self.assertEqual(dd["offline_gap_hours"], 6.0)
        self.assertEqual(dd["completed_items"], [])
        self.assertFalse(dd["backfilled_to_diary"])
        self.assertEqual(dd["guilt_debt"], 0)
        self.assertEqual(dd["optional_directions"], ["上午把信写完", "下午去河堤走走"])

        # 自定义离线说明原样保留，不替换成统一话术。
        code, body = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "offline_pause",
                "offline_gap_note": "手机没电了几小时，先不硬接上。",
            },
            actor_side="subject",
            now_iso="2026-11-17T16:00:00+08:00",
        )
        self.assertEqual(code, 200)
        dd = body["data"]["day_direction"]
        self.assertEqual(dd["offline_gap_notice"], "手机没电了几小时，先不硬接上。")
        self.assertEqual(dd["direction_stage"], "offline_paused")
        self.assertEqual(dd["completed_items"], [])
        self.assertFalse(dd["backfilled_to_diary"])

    def test_04_partner_forbidden_and_read_is_zero_write(self) -> None:
        code, body = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "set_day_direction", "direction": "偷设方向"},
            actor_side="partner",
        )
        self.assertEqual(code, 403)
        self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Seed a real state, then prove every read path writes nothing.
        code, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "set",
                "direction": "上午先写点手稿",
                "optional_directions": ["上午先写点手稿"],
            },
            actor_side="subject",
            now_iso="2026-11-17T09:00:00+08:00",
        )
        self.assertEqual(code, 200)

        before = _snap(self.tmp)
        c1, v1 = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=True
        )
        c2, v2 = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False
        )
        hint = ep_engine.build_v04_elastic_turn_hint(self.day_root)
        after = _snap(self.tmp)

        self.assertEqual(c1, 200)
        self.assertEqual(c2, 200)
        self.assertEqual(v1["data"]["llm_calls"], 0)
        self.assertEqual(v2["data"]["llm_calls"], 0)
        self.assertIn("今日弹性方向", hint)
        self.assertEqual(before, after, "read paths must be 100% zero-write")
        self.assertEqual(v1["data"]["day_direction"]["direction_stage"], "planned_or_optional")
        self.assertFalse(v1["data"]["day_direction"]["backfilled_to_diary"])
        self.assertEqual(v1["data"]["day_direction"]["guilt_debt"], 0)


if __name__ == "__main__":
    unittest.main()
