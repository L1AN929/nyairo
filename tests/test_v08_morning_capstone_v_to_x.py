#!/usr/bin/env python3
"""v0.8 早间冲刺承上启下综合验收：Wave V + W + X 一条连续日常链。

本文件只做一件事：在**同一套连续的** ``self.root`` 目录上，按真实用户会走
的顺序把 Waves V / W / X 串起来跑一遍，证明它们不是三块孤立的演示：

阶段 1（Wave V 自由动作 + Ch.25 暂放角）
    往桌面零代码放入全新饮品「柠檬气泡水」(2 份) 和一个真的用脏了的玻璃杯；
    主体自由文本「喝一口柠檬气泡水」由真实引擎识别为 ``CONSUME_PORTION``；
    把若干物件顺手放进 ``home_staging_corner``，暂放角如实反映占用/空位，
    永远没有 ``obligation_debt`` / ``messy_guilt``；伴侣视角不露私密件与名字。

阶段 2（Wave W Ch.23 牵挂滤镜 + 局部保留新解）
    同一套 root 里初始化街区书店；同一架书在两个牵挂问题下注意到不同切面，
    全部 ``in_range=True`` / ``understood=False`` / ``full_text_dumped=False``；
    再经 ``v08_emergent_echoes`` 记一次带保留的重新认识：旧保留与新增欣赏面
    并存，``hypothesis_status == "revised_by_observation"``，未给页码时
    ``exact_page_known is False``。

阶段 3（Wave X Ch.49 当日方向 + 诚实离线断点）
    设可选方向 -> ``start_step`` -> ``interrupt`` 保留已写草稿 -> ``offline_pause``。
    ``completed_items`` 始终为空、``backfilled_to_diary is False``、
    ``guilt_debt == 0``，草稿安全留在 ``preserved_work_notes``。

阶段 4（上下文桥 + 硬门禁）
    空白 root 零注入（``""``）；有数据的 root 渲染 <= 1800 字符并真的带上
    前面阶段留下的今日方向；所有零写入读接口在两次调用之间目录快照 SHA256
    完全不变，全程 ``llm_calls == 0``，冷重载后磁盘状态一致。

Run (native Windows Python)::

    python -B -m unittest tests.test_v08_morning_capstone_v_to_x -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import v08_emergent_echoes as echoes_engine  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402
from components.phone import v08_emergent_echoes_view as echoes_view  # noqa: E402
from components.world.scripts import v04_neighborhood_living as neighborhood  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

#: 同一日 2026-11-17 从早上到午后的一条连续时间线。
T_0830 = "2026-11-17T08:30:00+08:00"
T_0900 = "2026-11-17T09:00:00+08:00"
T_0910 = "2026-11-17T09:10:00+08:00"
T_0920 = "2026-11-17T09:20:00+08:00"
T_0930 = "2026-11-17T09:30:00+08:00"
T_1000 = "2026-11-17T10:00:00+08:00"
T_1010 = "2026-11-17T10:10:00+08:00"
T_1020 = "2026-11-17T10:20:00+08:00"
T_1030 = "2026-11-17T10:30:00+08:00"
T_1100 = "2026-11-17T11:00:00+08:00"
T_1110 = "2026-11-17T11:10:00+08:00"
T_1120 = "2026-11-17T11:20:00+08:00"
T_1200 = "2026-11-17T12:00:00+08:00"
T_1210 = "2026-11-17T12:10:00+08:00"
T_1400 = "2026-11-17T14:00:00+08:00"

DRAFT_NOTE = "随手记的半段折纸思路"
DIRECTION = "上午随手记半段折纸思路"
OPTIONAL_DIRECTIONS = [DIRECTION, "午后翻两页书"]


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """Full directory snapshot: size + mtime_ns + sha256 for files, mtime for dirs."""

    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _snapshot_sha(root: Path) -> str:
    payload = json.dumps(
        sorted((rel, list(row)) for rel, row in _snapshot(root).items()),
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class V08MorningCapstoneVToXTest(unittest.TestCase):
    """One continuous root, four ordered phases, no reset between them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="v08-morning-capstone-")
        cls.root = Path(cls._tmp.name)
        (cls.root / "phone").mkdir()
        cls._llm_calls_observed: list[int] = []

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.root = type(self).root

    # -- shared helpers --------------------------------------------------

    def _zero_llm(self, label: str, value: Any) -> int:
        self.assertEqual(value, 0, f"{label}: expected llm_calls == 0, got {value!r}")
        type(self)._llm_calls_observed.append(int(value))
        return int(value)

    def _introduce(
        self,
        object_id: str,
        label: str,
        kind: str,
        target: str,
        *,
        now: str,
        **extra: Any,
    ) -> dict[str, Any]:
        step: dict[str, Any] = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return matter.compose_open_act(
            self.root,
            {"intent_cn": f"放入{label}", "steps": [step]},
            now_iso=now,
        )

    def _act(self, intent: str, steps: list[dict[str, Any]], *, now: str) -> dict[str, Any]:
        return matter.compose_open_act(
            self.root, {"intent_cn": intent, "steps": steps}, now_iso=now
        )

    def _freeform(self, text: str, *, now: str) -> dict[str, Any]:
        return bridge.execute_normalized_micro_intent(
            self.root, {"intent_cn": text}, now_iso=now
        )

    def _matter_view(self, *, actor_side: str = "subject", now: str) -> dict[str, Any]:
        return matter.read_matter_space_view(self.root, actor_side=actor_side, now_iso=now)

    def _matter_object(self, view: dict[str, Any], object_id: str) -> dict[str, Any]:
        for row in view["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object missing from matter view: {object_id}")

    def _cold_json(self, rel: str) -> dict[str, Any]:
        path = self.root / rel
        self.assertTrue(path.is_file(), f"expected persisted file: {rel}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _reinterpret_payload(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": "record_memory_reinterpretation",
            "episode_ref": "book-clay-01",
            "episode_title_cn": "《窑火笔记》",
            "original_fact_cn": "这本书讲的是窑变，可我前半本读得很不耐烦",
            "prior_view_cn": "觉得作者絮叨，翻了几页就搁下了",
            "new_view_cn": "还是觉得絮叨，暂时不想继续读",
            "trigger_summary_cn": "整理书架时又把它抽出来",
            "shared_with_partner": False,
        }
        payload.update(overrides)
        return payload

    def _echo_mutate(self, payload: dict[str, Any], *, now: str) -> dict[str, Any]:
        projected, _status = echoes_engine.apply_emergent_echoes_mutation(
            self.root, payload, actor_side="subject", now_iso=now
        )
        return projected

    def _direction_mutate(self, payload: dict[str, Any], *, now: str) -> dict[str, Any]:
        code, body = ep_view.mutate_v04_elastic_play_view(
            self.root, payload, actor_side="subject", now_iso=now
        )
        self.assertEqual(code, 200, body)
        self._zero_llm("elastic mutate envelope", body.get("llm_calls", 0))
        return body["data"]["day_direction"]

    # ---------------------------------------------------------------
    # 阶段 1 —— Wave V 自由动作 + Ch.25 暂放角
    # ---------------------------------------------------------------
    def test_01_wave_v_free_action_and_staging_corner(self) -> None:
        # 冷读：还没有任何世界状态时，读取不能建目录、不能留文件。
        cold_before = _snapshot_sha(self.root)
        empty_subject = self._matter_view(now=T_0830)
        empty_partner = self._matter_view(actor_side="partner", now=T_0830)
        self.assertEqual(_snapshot_sha(self.root), cold_before, "cold read must be zero-write")
        self._zero_llm("cold matter view (subject)", empty_subject["llm_calls"])
        self._zero_llm("cold matter view (partner)", empty_partner["llm_calls"])
        self.assertEqual(empty_subject["staging_corner"]["occupied_slots"], 0)
        self.assertEqual(empty_subject["staging_corner"]["free_slots"], 4)

        # 全新饮品：零新业务代码，只凭自己的标签被识别。
        self._introduce(
            "morning_lemon_soda", "柠檬气泡水", "drink", "dining_table",
            now=T_0830, portions=2,
        )

        sip = self._freeform("喝一口柠檬气泡水", now=T_0900)
        self.assertTrue(sip["ok"], sip)
        self.assertTrue(sip["settled"], sip)
        self.assertEqual(sip["classification"], "direct_supported")
        self._zero_llm("freeform consume", sip.get("llm_calls"))
        sip_step = sip["receipt"]["step_results"][0]
        self.assertEqual(sip_step["op"], "CONSUME_PORTION")
        self.assertEqual(sip_step["portions_left"], 1)

        after_sip = self._matter_view(now=T_0900)
        self._zero_llm("matter view after sip", after_sip["llm_calls"])
        self.assertEqual(
            self._matter_object(after_sip, "morning_lemon_soda")["portions_left"], 1
        )

        # 真的用脏一个玻璃杯：倒进一份水、喝掉最后一份，宿主被标成 used。
        self._introduce(
            "morning_glass_cup", "玻璃杯", "vessel", "dining_table",
            now=T_0900, capacity_slots=1,
        )
        self._introduce(
            "morning_glass_water", "杯里剩的水", "drink", "morning_glass_cup",
            now=T_0910, portions=1,
        )
        self._act(
            "喝掉杯里的水",
            [{"op": "CONSUME_PORTION", "object_id": "morning_glass_cup"}],
            now=T_0910,
        )
        used_view = self._matter_view(now=T_0910)
        self.assertEqual(
            self._matter_object(used_view, "morning_glass_cup")["cleanliness"], "used"
        )

        # 顺手放进暂放角：饮料、用脏的杯子、一张私密纸条。
        self._act(
            "把剩下的柠檬气泡水放到暂放角",
            [{
                "op": "PLACE_OR_MOVE",
                "object_id": "morning_lemon_soda",
                "target_parent_id": "home_staging_corner",
            }],
            now=T_0920,
        )
        self._act(
            "把玻璃杯顺手放到暂放角",
            [{
                "op": "PLACE_OR_MOVE",
                "object_id": "morning_glass_cup",
                "target_parent_id": "home_staging_corner",
            }],
            now=T_0920,
        )
        self._introduce(
            "morning_private_note", "私密手稿纸条", "paper",
            "home_staging_corner",
            now=T_0920, visibility="private", paper_like=True,
        )

        subject_view = self._matter_view(now=T_0930)
        partner_view = self._matter_view(actor_side="partner", now=T_0930)
        self._zero_llm("matter view (subject)", subject_view["llm_calls"])
        self._zero_llm("matter view (partner)", partner_view["llm_calls"])

        subject_corner = subject_view["staging_corner"]
        self.assertEqual(subject_corner["zone_id"], "home_staging_corner")
        self.assertEqual(subject_corner["occupied_slots"], 3)
        self.assertEqual(subject_corner["free_slots"], 1)
        self.assertEqual(
            set(subject_corner["item_ids"]),
            {"morning_lemon_soda", "morning_glass_cup", "morning_private_note"},
        )
        self.assertIn("私密手稿纸条", subject_corner["item_labels_cn"])
        self.assertEqual(subject_corner["obligation_debt"], 0)
        self.assertIs(subject_corner["messy_guilt"], False)

        # 伴侣视角：共享的饮料和脏杯子可见，私密纸条的件数与名字都不出现。
        partner_corner = partner_view["staging_corner"]
        self.assertEqual(partner_corner["occupied_slots"], 2)
        self.assertEqual(partner_corner["free_slots"], 2)
        self.assertEqual(
            set(partner_corner["item_ids"]), {"morning_lemon_soda", "morning_glass_cup"}
        )
        self.assertNotIn("morning_private_note", partner_corner["item_ids"])
        self.assertNotIn("私密手稿纸条", partner_corner["item_labels_cn"])
        # 暂放角投影本身是伴侣能看到的那一面：整块内容里都不许出现私密件。
        self.assertNotIn(
            "morning_private_note",
            json.dumps(partner_corner, ensure_ascii=False),
        )
        self.assertNotIn("私密手稿纸条", json.dumps(partner_corner, ensure_ascii=False))
        # 伴侣的物件清单与名字也都不含私密纸条。
        partner_object_ids = {row["object_id"] for row in partner_view["objects"]}
        self.assertNotIn("morning_private_note", partner_object_ids)
        self.assertNotIn(
            "私密手稿纸条",
            [row["label_cn"] for row in partner_view["objects"]],
        )
        self.assertEqual(partner_corner["obligation_debt"], 0)
        self.assertIs(partner_corner["messy_guilt"], False)

        # 冷读 + 重读：两次 subject / 两次 partner 都 100% 零磁盘写入。
        before_read = _snapshot_sha(self.root)
        first = self._matter_view(now=T_0930)
        second = self._matter_view(now=T_0930)
        partner_first = self._matter_view(actor_side="partner", now=T_0930)
        partner_second = self._matter_view(actor_side="partner", now=T_0930)
        after_read = _snapshot_sha(self.root)
        self.assertEqual(before_read, after_read, "repeated matter reads must be zero-write")
        self.assertEqual(first["staging_corner"], second["staging_corner"])
        self.assertEqual(partner_first["staging_corner"], partner_second["staging_corner"])

        # 冷重载：直接读盘上的状态文件，确认扣份与脏杯子已经真实落盘。
        cold_state = self._cold_json("world/v08_matter_space.json")
        self.assertEqual(cold_state["matter_objects"]["morning_lemon_soda"]["portions_left"], 1)
        self.assertEqual(cold_state["matter_objects"]["morning_glass_cup"]["cleanliness"], "used")
        self.assertEqual(
            cold_state["matter_objects"]["morning_private_note"]["visibility"], "private"
        )

    # ---------------------------------------------------------------
    # 阶段 2 —— Wave W Ch.23 牵挂滤镜 + 局部保留新解
    # ---------------------------------------------------------------
    def test_02_wave_w_concern_filter_and_partial_reinterpretation(self) -> None:
        # 同一套 root 里初始化街区书店（独立的 world/v04_neighborhood_state.json）。
        neighborhood.save_state_atomic(self.root, neighborhood.default_state())

        before_shelf = _snapshot_sha(self.root)
        paper = neighborhood.project_concern_tinted_shelf_zero_write(
            self.root, focus_question="纸盒折叠/修补", now_iso=T_1000
        )
        river = neighborhood.project_concern_tinted_shelf_zero_write(
            self.root, focus_question="河边长椅/雨天绕路", now_iso=T_1000
        )
        after_shelf = _snapshot_sha(self.root)
        self.assertEqual(before_shelf, after_shelf, "shelf projections must be zero-write")

        paper_ids = [row["book_id"] for row in paper["noticed_items"]]
        river_ids = [row["book_id"] for row in river["noticed_items"]]
        self.assertEqual(paper_ids, ["book_paper_joinery"])
        self.assertEqual(river_ids, ["book_river_sketches"])
        self.assertNotEqual(paper_ids, river_ids, "different concerns must notice differently")

        for projection in (paper, river):
            self.assertTrue(projection["zero_write"])
            self._zero_llm("shelf projection", projection["llm_calls"])
            self.assertTrue(projection["in_range_book_ids"])
            self.assertTrue(projection["noticed_items"])
            for item in projection["noticed_items"]:
                self.assertIs(item["in_range"], True)
                self.assertIs(item["understood"], False)
                self.assertIs(item["full_text_dumped"], False)
                self.assertTrue(item["noticed_angle_cn"])

        # 第一次：保留原来的不耐烦；第二次：局部修正，不把整段翻成夸奖。
        first = self._echo_mutate(self._reinterpret_payload(), now=T_1010)[
            "memory_reinterpretations"
        ][0]
        self.assertEqual(first["original_fact_cn"], self._reinterpret_payload()["original_fact_cn"])
        self.assertEqual(first["preserved_reservation_cn"], "")
        self.assertEqual(first["appreciation_facet_cn"], "")
        self.assertIs(first["exact_page_known"], False)

        second = self._echo_mutate(
            self._reinterpret_payload(
                prior_view_cn="这回翻到第三章",
                new_view_cn="第三章讲窑变缩尺那段其实有意思",
                trigger_summary_cn="雨天无事，随手翻到第三章",
                preserved_reservation_cn="前半本絮叨还是不太喜欢",
                appreciation_facet_cn="第三章讲窑变缩尺的那段其实很有意思",
                hypothesis_status="revised_by_observation",
            ),
            now=T_1020,
        )["memory_reinterpretations"][0]

        self.assertEqual(second["reinterpretation_id"], first["reinterpretation_id"])
        self.assertEqual(second["original_fact_cn"], first["original_fact_cn"])
        self.assertEqual(second["preserved_reservation_cn"], "前半本絮叨还是不太喜欢")
        self.assertEqual(
            second["appreciation_facet_cn"], "第三章讲窑变缩尺的那段其实很有意思"
        )
        self.assertEqual(second["hypothesis_status"], "revised_by_observation")
        self.assertEqual(len(second["revisions"]), 2)
        # 没传页码就绝不编出页码。
        self.assertIs(second["exact_page_known"], False)
        self.assertEqual(second["exact_page_ref"], "")
        self.assertIs(second["revisions"][-1]["exact_page_known"], False)

        # 视图读取零写入，并原样带出保留 + 新解；未共享前伴侣看不到。
        before_view = _snapshot_sha(self.root)
        code, body = echoes_view.read_v08_emergent_echoes_view(
            self.root, has_subject_capability=True, now_iso=T_1030
        )
        code_partner, partner_body = echoes_view.read_v08_emergent_echoes_view(
            self.root, has_subject_capability=False, now_iso=T_1030
        )
        after_view = _snapshot_sha(self.root)
        self.assertEqual(before_view, after_view, "echoes view must be zero-write")
        self.assertEqual(code, 200, body)
        self.assertEqual(code_partner, 200, partner_body)
        self._zero_llm("echoes view (subject)", body["llm_calls"])
        self._zero_llm("echoes view (partner)", partner_body["llm_calls"])

        rows = body["data"]["memory_reinterpretations"]
        view_row = next(row for row in rows if row["episode_ref"] == "book-clay-01")
        self.assertEqual(view_row["preserved_reservation_cn"], "前半本絮叨还是不太喜欢")
        self.assertEqual(
            view_row["appreciation_facet_cn"], "第三章讲窑变缩尺的那段其实很有意思"
        )
        self.assertEqual(view_row["hypothesis_status"], "revised_by_observation")
        self.assertIs(view_row["exact_page_known"], False)
        partner_rows = partner_body["data"]["memory_reinterpretations"]
        self.assertEqual(partner_rows, [])
        self.assertNotIn("窑变缩尺", json.dumps(partner_body, ensure_ascii=False))

        # 冷重载：读盘上第 1 版状态文件，确认保留与新解真的持久化。
        cold_echoes = self._cold_json("phone/v08_emergent_echoes.json")
        cold_row = next(
            row
            for row in cold_echoes["memory_reinterpretations"]
            if row["episode_ref"] == "book-clay-01"
        )
        self.assertEqual(cold_row["preserved_reservation_cn"], "前半本絮叨还是不太喜欢")
        self.assertEqual(
            cold_row["appreciation_facet_cn"], "第三章讲窑变缩尺的那段其实很有意思"
        )
        self.assertEqual(cold_row["hypothesis_status"], "revised_by_observation")
        self.assertIs(cold_row["exact_page_known"], False)

    # ---------------------------------------------------------------
    # 阶段 3 —— Wave X Ch.49 当日方向 + 诚实离线断点
    # ---------------------------------------------------------------
    def test_03_wave_x_direction_and_honest_offline_break(self) -> None:
        planned = self._direction_mutate(
            {
                "action": "set_day_direction",
                "mode": "set",
                "direction": DIRECTION,
                "optional_directions": list(OPTIONAL_DIRECTIONS),
            },
            now=T_1100,
        )
        self.assertEqual(planned["direction_stage"], "planned_or_optional")
        self.assertEqual(planned["completed_items"], [])
        self.assertIs(planned["backfilled_to_diary"], False)
        self.assertEqual(planned["guilt_debt"], 0)
        self.assertEqual(planned["optional_directions"], OPTIONAL_DIRECTIONS)

        started = self._direction_mutate(
            {
                "action": "set_day_direction",
                "mode": "start_step",
                "active_step_ref": "step-origami-1",
                "current_focus": "把折纸思路记到一半",
            },
            now=T_1110,
        )
        self.assertEqual(started["direction_stage"], "in_progress")
        self.assertEqual(started["active_step_ref"], "step-origami-1")
        self.assertEqual(started["completed_items"], [])
        self.assertEqual(started["optional_directions"], OPTIONAL_DIRECTIONS)
        self.assertEqual(started["guilt_debt"], 0)

        interrupted = self._direction_mutate(
            {
                "action": "set_day_direction",
                "mode": "interrupt",
                "interrupt_reason": "伴侣刚好来搭话，先停一下",
                "preserve_draft_note": DRAFT_NOTE,
            },
            now=T_1120,
        )
        self.assertEqual(interrupted["direction_stage"], "interrupted_preserved")
        self.assertEqual(interrupted["status"], "yielded_naturally")
        self.assertEqual(interrupted["completed_items"], [])
        self.assertIs(interrupted["backfilled_to_diary"], False)
        self.assertEqual(interrupted["guilt_debt"], 0)
        self.assertEqual(interrupted["preserved_work_notes"][-1]["note"], DRAFT_NOTE)
        self.assertEqual(interrupted["preserved_work_notes"][-1]["stage"], "interrupted")
        self.assertEqual(interrupted["optional_directions"], OPTIONAL_DIRECTIONS)

        paused = self._direction_mutate(
            {
                "action": "set_day_direction",
                "mode": "offline_pause",
                "offline_gap_hours": 2,
            },
            now=T_1200,
        )
        self.assertEqual(paused["direction_stage"], "offline_paused")
        self.assertIn("没有连着发生", paused["offline_gap_notice"])
        self.assertEqual(paused["offline_gap_hours"], 2.0)
        self.assertEqual(paused["completed_items"], [])
        self.assertIs(paused["backfilled_to_diary"], False)
        self.assertEqual(paused["guilt_debt"], 0)
        self.assertEqual(paused["preserved_work_notes"][-1]["note"], DRAFT_NOTE)
        self.assertEqual(paused["optional_directions"], OPTIONAL_DIRECTIONS)

        # 读视图零写入 + llm_calls == 0；草稿仍安全地在视图里。
        before_read = _snapshot_sha(self.root)
        code_subject, subject_body = ep_view.read_v04_elastic_play_view(
            self.root, has_subject_capability=True, now_iso=T_1210
        )
        code_partner, partner_body = ep_view.read_v04_elastic_play_view(
            self.root, has_subject_capability=False, now_iso=T_1210
        )
        after_read = _snapshot_sha(self.root)
        self.assertEqual(before_read, after_read, "elastic view must be zero-write")
        self.assertEqual(code_subject, 200, subject_body)
        self.assertEqual(code_partner, 200, partner_body)
        self._zero_llm("elastic view (subject)", subject_body["data"]["llm_calls"])
        self._zero_llm("elastic view (partner)", partner_body["data"]["llm_calls"])
        subject_dd = subject_body["data"]["day_direction"]
        self.assertEqual(subject_dd["direction_stage"], "offline_paused")
        self.assertEqual(subject_dd["preserved_work_notes"][-1]["note"], DRAFT_NOTE)
        self.assertEqual(subject_dd["completed_items"], [])
        self.assertIs(subject_dd["backfilled_to_diary"], False)

        # 冷重载：直接读盘上的方向状态。
        cold_elastic = self._cold_json("phone/v04_elastic_play.json")
        cold_dd = cold_elastic["day_direction"]
        self.assertEqual(cold_dd["direction_stage"], "offline_paused")
        self.assertEqual(cold_dd["completed_items"], [])
        self.assertIs(cold_dd["backfilled_to_diary"], False)
        self.assertEqual(cold_dd["guilt_debt"], 0)
        self.assertEqual(cold_dd["preserved_work_notes"][-1]["note"], DRAFT_NOTE)
        self.assertEqual(cold_dd["optional_directions"], OPTIONAL_DIRECTIONS)

    # ---------------------------------------------------------------
    # 阶段 4 —— 上下文桥 + 硬门禁
    # ---------------------------------------------------------------
    def test_04_context_bridge_and_hard_gates(self) -> None:
        # 空白 root：零注入，且调用前后一个字节都不变。
        blank_root = self.root / "morning_blank_home"
        blank_root.mkdir()
        before_blank = _snapshot_sha(blank_root)
        blank_text = lcb.build_lived_turn_context(blank_root, {}, now_iso=T_1400)
        after_blank = _snapshot_sha(blank_root)
        self.assertEqual(blank_text, "", "an uninitialized root must inject nothing")
        self.assertEqual(before_blank, after_blank, "blank-root bridge must be zero-write")

        cfg = {
            "day_root": str(self.root),
            "phone": {
                "enabled": False,
                "profile_home": str(self.root),
                "private_artifacts": True,
            },
        }
        before_data = _snapshot_sha(self.root)
        rendered = lcb.build_lived_turn_context(
            self.root, cfg, incoming_text="今天上午在忙什么？", now_iso=T_1400
        )
        budget = lcb.measure_context_budget_breakdown(self.root, cfg, now_iso=T_1400)
        after_data = _snapshot_sha(self.root)
        self.assertEqual(before_data, after_data, "populated-root bridge must be zero-write")

        self.assertIsInstance(rendered, str)
        self.assertNotEqual(rendered, "", "accumulated phases must surface real context")
        self.assertLessEqual(len(rendered), 1800)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertLessEqual(len(rendered), lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("今日弹性方向", rendered)
        self.assertEqual(budget["max_chars"], 1800)
        self.assertIs(budget["within_budget"], True)
        self.assertLessEqual(budget["total_chars"], 1800)

        # 冷重载：前面三个阶段写下的东西在新一次读取里仍然一致。
        staging = self._matter_view(now=T_1400)["staging_corner"]
        self.assertEqual(staging["occupied_slots"], 3)
        self.assertEqual(staging["free_slots"], 1)
        self.assertEqual(staging["obligation_debt"], 0)
        self.assertIs(staging["messy_guilt"], False)

        code, echoes_body = echoes_view.read_v08_emergent_echoes_view(
            self.root, has_subject_capability=True, now_iso=T_1400
        )
        self.assertEqual(code, 200, echoes_body)
        self._zero_llm("final echoes read", echoes_body["llm_calls"])
        final_echo = next(
            row
            for row in echoes_body["data"]["memory_reinterpretations"]
            if row["episode_ref"] == "book-clay-01"
        )
        self.assertEqual(final_echo["preserved_reservation_cn"], "前半本絮叨还是不太喜欢")
        self.assertIs(final_echo["exact_page_known"], False)

        code, elastic_body = ep_view.read_v04_elastic_play_view(
            self.root, has_subject_capability=True, now_iso=T_1400
        )
        self.assertEqual(code, 200, elastic_body)
        self._zero_llm("final elastic read", elastic_body["data"]["llm_calls"])
        final_dd = elastic_body["data"]["day_direction"]
        self.assertEqual(final_dd["direction_stage"], "offline_paused")
        self.assertEqual(final_dd["preserved_work_notes"][-1]["note"], DRAFT_NOTE)
        self.assertEqual(final_dd["completed_items"], [])

        # 全程硬门禁：所有观察到的读接口都没有调用外部模型。
        observed = list(type(self)._llm_calls_observed)
        self.assertEqual([v for v in observed if v != 0], [])
        self.assertTrue(all(v == 0 for v in observed))


if __name__ == "__main__":
    unittest.main()
