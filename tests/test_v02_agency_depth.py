#!/usr/bin/env python3
"""v0.2 品味演化、短期饱和、双向冲突搁置、三类后悔、二阶小规矩与开放慢孵化专项单测（30 条）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import v02_agency_depth_engine as ad_engine
from components.phone import v02_agency_depth_view as ad_view


class TestV02AgencyDepthEngineAndView(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo_v02_ad_")
        self.day_root = Path(self._tmp.name) / "day_root"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_empty_root_read_and_summarize_zero_disk_writes(self) -> None:
        self.assertFalse(self.day_root.exists())
        code, doc = ad_view.read_v02_agency_depth_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-13T10:00:00Z",
        )
        self.assertEqual(code, 200)
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["schema"], "nyairo.v02.agency_depth.v1")
        self.assertEqual(doc["endpoint"], "/api/phone/v02/agency-depth")
        self.assertFalse(doc["state_exists"])
        self.assertEqual(doc["perspective"], "partner")
        self.assertEqual(doc["llm_calls"], 0)
        self.assertEqual(doc["regret_reflections"], [])
        self.assertTrue(doc["regret_reflections_withheld"])
        for key in (
            "state_exists",
            "perspective",
            "revision",
            "observed_at",
            "preferences",
            "choice_episodes",
            "partner_options",
            "low_cost_options",
            "parked_conflicts",
            "open_incubations",
            "second_order_rules",
            "method_adjustments",
            "regret_reflections",
            "regret_reflections_withheld",
            "llm_calls",
        ):
            self.assertIn(key, doc)
            self.assertIn(key, doc["data"])
            self.assertEqual(doc[key], doc["data"][key])

        summary = ad_view.summarize_agency_depth_for_context(
            self.day_root,
            include_private=False,
            now_iso="2026-11-13T10:00:00Z",
        )
        self.assertFalse(summary["state_exists"])
        self.assertEqual(summary["llm_calls"], 0)
        self.assertFalse(self.day_root.exists())

    def test_02_existing_state_read_preserves_mtime_and_atomic_path(self) -> None:
        code, _ = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "offer_low_cost_option",
                "domain": "茶饮",
                "option_title": "玄米焙茶",
                "note": "放桌上随手泡",
            },
            actor_side="partner",
            now_iso="2026-11-13T10:00:00Z",
        )
        self.assertEqual(code, 200)
        state_file = self.day_root / "state" / "v02_agency_depth_state.json"
        self.assertTrue(state_file.is_file())
        fixed_ts = 1_700_000_000.0
        os.utime(state_file, (fixed_ts, fixed_ts))
        before_stat = state_file.stat()

        r_code, r_doc = ad_view.read_v02_agency_depth_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-13T12:00:00Z",
        )
        self.assertEqual(r_code, 200)
        self.assertTrue(r_doc["state_exists"])
        after_stat = state_file.stat()
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)
        self.assertEqual(list(state_file.parent.iterdir()), [state_file])

    def test_03_spontaneous_multi_option_promotes_unknown_tentative_supported(self) -> None:
        c1, d1 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "饮品",
                "chosen": "冷泡焙茶",
                "options": ["冷泡焙茶", "冰美式", "苏打水"],
                "choice_mode": "spontaneous",
                "forgone_cost": "放弃提神咖啡",
            },
            actor_side="subject",
            now_iso="2026-11-13T09:00:00Z",
        )
        self.assertEqual(c1, 200)
        self.assertEqual(d1["preferences"][0]["status"], "TENTATIVE")

        c2, d2 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "饮品",
                "chosen": "冷泡焙茶",
                "options": ["冷泡焙茶", "乌龙茶"],
                "choice_mode": "spontaneous",
            },
            actor_side="subject",
            now_iso="2026-11-13T15:00:00Z",
        )
        self.assertEqual(c2, 200)
        self.assertEqual(d2["preferences"][0]["status"], "SUPPORTED")
        self.assertEqual(d2["preferences"][0]["spontaneous_multi_count"], 2)

    def test_04_forced_single_or_duplicate_options_never_promote_supported(self) -> None:
        for idx, opts in enumerate((["白开水"], ["白开水", "白开水"], ["白开水", "  白开水  "])):
            code, doc = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_choice_episode",
                    "domain": "饮品",
                    "chosen": "白开水",
                    "options": opts,
                    "choice_mode": "spontaneous" if idx > 0 else "forced_single",
                },
                actor_side="subject",
                now_iso=f"2026-11-13T1{idx}:00:00Z",
            )
            self.assertEqual(code, 200)
            self.assertEqual(doc["preferences"][0]["status"], "UNKNOWN")

    def test_05_requested_or_constrained_choices_never_promote_to_supported(self) -> None:
        for mode in ("requested", "constrained", "requested"):
            code, doc = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_choice_episode",
                    "domain": "电影",
                    "chosen": "科幻动作片",
                    "options": ["科幻动作片", "温情纪录片", "公路片"],
                    "choice_mode": mode,
                },
                actor_side="subject",
                now_iso="2026-11-13T20:00:00Z",
            )
            self.assertEqual(code, 200)
            self.assertEqual(doc["preferences"][0]["status"], "UNKNOWN")

        # 即便已有 TENTATIVE，后续迎合选择也绝不升格为 SUPPORTED
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "音乐",
                "chosen": "室内弦乐",
                "options": ["室内弦乐", "电子乐"],
                "choice_mode": "spontaneous",
            },
            actor_side="subject",
            now_iso="2026-11-13T20:10:00Z",
        )
        code2, doc2 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "音乐",
                "chosen": "室内弦乐",
                "options": ["室内弦乐", "爵士乐"],
                "choice_mode": "requested",
            },
            actor_side="subject",
            now_iso="2026-11-13T20:30:00Z",
        )
        self.assertEqual(code2, 200)
        pref_music = [p for p in doc2["preferences"] if p["domain"] == "音乐"][0]
        self.assertEqual(pref_music["status"], "TENTATIVE")

    def test_06_exposure_satiation_lowers_weight_without_erasing_supported_status(self) -> None:
        for hr in ("09", "12"):
            ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_choice_episode",
                    "domain": "绘画题材",
                    "chosen": "雨夜街角",
                    "options": ["雨夜街角", "晴日海岸"],
                    "choice_mode": "spontaneous",
                },
                actor_side="subject",
                now_iso=f"2026-11-13T{hr}:00:00Z",
            )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_exposure_satiation",
                "domain": "绘画题材",
                "title": "雨夜街角",
                "intensity": 0.45,
            },
            actor_side="subject",
            now_iso="2026-11-13T13:00:00Z",
        )
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_exposure_satiation",
                "domain": "绘画题材",
                "title": "雨夜街角",
                "intensity": 0.45,
            },
            actor_side="subject",
            now_iso="2026-11-13T13:30:00Z",
        )
        self.assertEqual(code, 200)
        pref = doc["preferences"][0]
        self.assertEqual(pref["status"], "SUPPORTED")
        self.assertTrue(pref["saturated"])
        self.assertEqual(pref["satiation_state"], "saturated")
        self.assertGreaterEqual(pref["satiation_score"], 0.70)
        self.assertEqual(pref["candidate_weight_multiplier"], 0.25)

    def test_07_satiation_never_resets_across_midnight_boundary(self) -> None:
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_exposure_satiation",
                "domain": "甜点",
                "title": "栗子蒙布朗",
                "intensity": 0.88,
            },
            actor_side="subject",
            now_iso="2026-11-13T23:55:00Z",
        )
        code, doc = ad_view.read_v02_agency_depth_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-14T00:05:00Z",
        )
        self.assertEqual(code, 200)
        pref = doc["preferences"][0]
        self.assertTrue(pref["saturated"])
        self.assertEqual(pref["satiation_state"], "saturated")
        self.assertGreater(pref["satiation_score"], 0.85)
        self.assertEqual(pref["candidate_weight_multiplier"], 0.25)

    def test_08_continuous_decay_and_spontaneous_rediscovery_boosts_multiplier(self) -> None:
        for t in ("08:00:00Z", "10:00:00Z"):
            ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_choice_episode",
                    "domain": "茶饮",
                    "chosen": "桂花乌龙",
                    "options": ["桂花乌龙", "茉莉绿茶"],
                    "choice_mode": "spontaneous",
                },
                actor_side="subject",
                now_iso=f"2026-11-10T{t}",
            )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_exposure_satiation",
                "domain": "茶饮",
                "chosen": "桂花乌龙",
                "intensity": 0.85,
            },
            actor_side="subject",
            now_iso="2026-11-10T11:00:00Z",
        )
        # 冷却 36 小时后已退饱和
        _, cooled = ad_view.read_v02_agency_depth_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-11T23:00:00Z",
        )
        self.assertFalse(cooled["preferences"][0]["saturated"])
        self.assertLess(cooled["preferences"][0]["satiation_score"], 0.35)

        # 冷却后再次自发多选重拾 -> rediscovered 且权重 1.1
        code, redisc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "茶饮",
                "chosen": "桂花乌龙",
                "options": ["桂花乌龙", "红茶"],
                "choice_mode": "spontaneous",
            },
            actor_side="subject",
            now_iso="2026-11-12T09:00:00Z",
        )
        self.assertEqual(code, 200)
        pref = redisc["preferences"][0]
        self.assertEqual(pref["status"], "SUPPORTED")
        self.assertEqual(pref["satiation_state"], "rediscovered")
        self.assertFalse(pref["saturated"])
        self.assertEqual(pref["candidate_weight_multiplier"], 1.1)

    def test_09_partner_offer_and_withdraw_low_cost_option(self) -> None:
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "offer_low_cost_option",
                "domain": "茶饮",
                "option_title": "大麦茶",
                "note": "无咖啡因随时喝",
            },
            actor_side="partner",
            now_iso="2026-11-13T14:00:00Z",
        )
        self.assertEqual(code, 200)
        self.assertEqual(len(doc["partner_options"]), 1)
        self.assertEqual(doc["partner_options"], doc["low_cost_options"])
        opt = doc["partner_options"][0]
        self.assertEqual(opt["status"], "offered")
        self.assertTrue(opt["shared"])

        w_code, w_doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "withdraw_partner_option",
                "option_id": opt["option_id"],
            },
            actor_side="partner",
            now_iso="2026-11-13T15:00:00Z",
        )
        self.assertEqual(w_code, 200)
        self.assertEqual(w_doc["partner_options"][0]["status"], "withdrawn")

        miss_code, miss_doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {"action": "withdraw_partner_option", "option_id": "popt_9999"},
            actor_side="partner",
        )
        self.assertEqual(miss_code, 404)
        self.assertEqual(miss_doc["reason_code"], "OPTION_NOT_FOUND")

    def test_10_park_lean_and_settle_bidirectional_conflict(self) -> None:
        c1, d1 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "park_or_resolve_conflict",
                "conflict_op": "park",
                "title": "熬夜画完草稿还是早睡护嗓",
                "side_a": "趁手感把窗边速写收尾",
                "side_b": "早点休息明天精神好",
                "note_cn": "两边都真实，今晚先搁置不硬选",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-13T22:30:00Z",
        )
        self.assertEqual(c1, 200)
        cf = d1["parked_conflicts"][0]
        self.assertEqual(cf["status"], "parked")
        self.assertTrue(cf["both_motives_preserved"])

        c2, d2 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "park_or_resolve_conflict",
                "conflict_op": "lean",
                "conflict_id": cf["conflict_id"],
                "note_cn": "稍微偏向先睡",
            },
            actor_side="subject",
            now_iso="2026-11-13T22:45:00Z",
        )
        self.assertEqual(c2, 200)
        self.assertEqual(d2["parked_conflicts"][0]["status"], "leaning")

        c3, d3 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "park_or_resolve_conflict",
                "conflict_op": "settle",
                "conflict_id": cf["conflict_id"],
            },
            actor_side="subject",
            now_iso="2026-11-13T23:00:00Z",
        )
        self.assertEqual(c3, 200)
        self.assertEqual(d3["parked_conflicts"][0]["status"], "settled")

    def test_11_regret_outcome_disappointment_does_not_alter_method_policy(self) -> None:
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "outcome_disappointment",
                "summary": "带了伞出门结果傍晚没下雨，只是白背了一路",
                "method_adjustment_cn": "以后不看天气预报了",
            },
            actor_side="subject",
            now_iso="2026-11-13T19:00:00Z",
        )
        self.assertEqual(code, 200)
        reg = doc["regret_reflections"][0]
        self.assertFalse(reg["changes_method_policy"])
        self.assertEqual(reg["method_adjustment_cn"], "")
        self.assertEqual(doc["method_adjustments"], [])

    def test_12_regret_process_mistake_records_method_adjustment(self) -> None:
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "process_mistake",
                "summary": "水彩底稿没干透就叠色导致边缘晕染浑浊",
                "method_adjustment_cn": "叠深色前先用纸巾轻触确认底色干透",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-13T19:20:00Z",
        )
        self.assertEqual(code, 200)
        self.assertTrue(doc["regret_reflections"][0]["changes_method_policy"])
        self.assertEqual(len(doc["method_adjustments"]), 1)
        self.assertIn("确认底色干透", doc["method_adjustments"][0]["adjustment_cn"])

    def test_13_regret_opportunity_loss_without_counterfactual_rejected_409(self) -> None:
        c1, d1 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "opportunity_loss",
                "summary": "总觉得要是换一条路散步会更好",
                "has_observable_alternative": False,
                "counterfactual_note": "另一条街也许有猫",
            },
            actor_side="subject",
        )
        self.assertEqual(c1, 409)
        self.assertEqual(d1["reason_code"], "MISSING_OBSERVABLE_COUNTERFACTUAL")

        c2, d2 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "opportunity_loss",
                "summary": "错过了下午的旧书市集",
                "has_observable_alternative": True,
            },
            actor_side="subject",
        )
        self.assertEqual(c2, 409)
        self.assertEqual(d2["reason_code"], "MISSING_OBSERVABLE_COUNTERFACTUAL")

    def test_14_regret_opportunity_loss_with_observable_counterfactual_succeeds(self) -> None:
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "opportunity_loss",
                "summary": "下午整理书架错过了社区木工坊开放时段",
                "has_observable_alternative": True,
                "counterfactual_note": "木工坊公告写明周四 14:00-17:00 开放免费刨花体验",
            },
            actor_side="subject",
            now_iso="2026-11-13T18:00:00Z",
        )
        self.assertEqual(code, 200)
        self.assertEqual(doc["regret_reflections"][0]["regret_kind"], "opportunity_loss")
        self.assertTrue(doc["regret_reflections"][0]["has_observable_alternative"])

    def test_15_partner_view_strictly_withholds_all_regret_reflections(self) -> None:
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "process_mistake",
                "summary": "私人后悔测试文案不应被伴侣读取",
                "method_adjustment_cn": "公开的方法小总结",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-13T19:30:00Z",
        )
        p_code, p_doc = ad_view.read_v02_agency_depth_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-13T20:00:00Z",
        )
        self.assertEqual(p_code, 200)
        self.assertEqual(p_doc["regret_reflections"], [])
        self.assertTrue(p_doc["regret_reflections_withheld"])
        self.assertNotIn("私人后悔测试文案", str(p_doc))

    def test_16_partner_propose_shared_gentle_rule_never_auto_locks_endorsed(self) -> None:
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "propose_shared_gentle_rule",
                "title": "十一点半把台灯调暖",
                "trigger_window": "23:30-01:00",
                "reason_cn": "护眼放松不催睡",
            },
            actor_side="partner",
            now_iso="2026-11-13T21:00:00Z",
        )
        self.assertEqual(code, 200)
        rl = doc["second_order_rules"][0]
        self.assertEqual(rl["kind"], "shared_gentle")
        self.assertEqual(rl["author_side"], "partner")
        self.assertEqual(rl["status"], "proposed")
        self.assertFalse(rl["auto_locked"])
        self.assertTrue(rl["shared"])

    def test_17_subject_second_order_rule_lifecycle_always_unlocked(self) -> None:
        c1, d1 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "propose",
                "title": "深夜少刷碎片流",
                "trigger_window": "23:00-00:30",
                "reason_cn": "留整块安静时间听雨",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-13T21:10:00Z",
        )
        self.assertEqual(c1, 200)
        rid = d1["second_order_rules"][0]["rule_id"]

        for op, expected_status in (
            ("start_trial", "trialing"),
            ("endorse", "endorsed"),
            ("revise", "endorsed"),
            ("release", "released"),
        ):
            code, doc = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "manage_second_order_rule",
                    "rule_op": op,
                    "rule_id": rid,
                    "reason_cn": f"阶段更新：{op}",
                },
                actor_side="subject",
                now_iso="2026-11-13T22:00:00Z",
            )
            self.assertEqual(code, 200)
            rule_now = doc["second_order_rules"][0]
            self.assertEqual(rule_now["status"], expected_status)
            self.assertFalse(rule_now["auto_locked"])

    def test_18_open_incubation_elapsed_48h_72h_without_evidence_blocks_fake_epiphany(self) -> None:
        _, opened = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "老木窗隔音又保留风声的做法",
                "note_cn": "还不急着下结论",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-10T10:00:00Z",
        )
        qid = opened["open_incubations"][0]["question_id"]
        self.assertEqual(opened["open_incubations"][0]["status"], "incubating")

        # 48h 无新证据 -> 仍为 incubating
        _, c48 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "check_elapsed",
                "question_id": qid,
            },
            actor_side="subject",
            now_iso="2026-11-12T10:00:00Z",
        )
        self.assertEqual(c48["open_incubations"][0]["status"], "incubating")

        # 72h 无新证据 -> 仍为 incubating，绝不假顿悟
        _, c72 = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "check_elapsed",
                "question_id": qid,
            },
            actor_side="subject",
            now_iso="2026-11-13T10:00:00Z",
        )
        self.assertEqual(c72["open_incubations"][0]["status"], "incubating")
        self.assertGreaterEqual(c72["open_incubations"][0]["elapsed_hours"], 72.0)

    def test_19_open_incubation_novel_evidence_advances_ready_to_revisit_and_settle(self) -> None:
        _, opened = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "纸本水彩受潮卷边怎么压平",
                "evidence_ref": "note:initial_obs",
            },
            actor_side="subject",
            now_iso="2026-11-10T09:00:00Z",
        )
        qid = opened["open_incubations"][0]["question_id"]

        # 重复旧证据指纹不推进 ready_to_revisit
        _, dup_ev = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "add_evidence",
                "question_id": qid,
                "evidence_ref": "note:initial_obs",
            },
            actor_side="subject",
            now_iso="2026-11-11T09:00:00Z",
        )
        self.assertEqual(dup_ev["open_incubations"][0]["status"], "incubating")

        # 全新证据指纹推进 ready_to_revisit
        _, novel_ev = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "add_evidence",
                "question_id": qid,
                "evidence_ref": "book:watercolor_paper_care_p42",
                "clue_text": "微潮背刷清水夹吸水纸阴干",
            },
            actor_side="subject",
            now_iso="2026-11-12T09:00:00Z",
        )
        self.assertEqual(novel_ev["open_incubations"][0]["status"], "ready_to_revisit")

        _, settled = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "settle",
                "question_id": qid,
                "note_cn": "实测夹吸水纸一夜恢复平整",
            },
            actor_side="subject",
            now_iso="2026-11-13T09:00:00Z",
        )
        self.assertEqual(settled["open_incubations"][0]["status"], "settled")

    def test_20_partner_share_incubation_clue_matches_existing_and_updates_clues(self) -> None:
        _, opened = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "雨天阅读护眼灯色温",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-12T10:00:00Z",
        )
        qid = opened["open_incubations"][0]["question_id"]

        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "share_incubation_clue",
                "question_id": qid,
                "clue_text": "4000K 漫反射配磨砂灯罩不刺眼",
            },
            actor_side="partner",
            now_iso="2026-11-13T10:00:00Z",
        )
        self.assertEqual(code, 200)
        inc = doc["open_incubations"][0]
        self.assertEqual(inc["status"], "ready_to_revisit")
        self.assertEqual(inc["latest_clue"], "4000K 漫反射配磨砂灯罩不刺眼")
        self.assertEqual(len(inc["clues"]), 1)
        self.assertEqual(len(inc["partner_clues"]), 1)

    def test_21_partner_share_clue_creates_shared_topic_or_blocks_private_qid(self) -> None:
        # 未传已有 question_id 但传了新 topic -> 新建 shared=True, status=ready_to_revisit
        c_new, d_new = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "share_incubation_clue",
                "topic": "阳台薄荷越冬修剪",
                "clue_text": "留根部老茎剪掉徒长枝更容易过冬",
            },
            actor_side="partner",
            now_iso="2026-11-13T11:00:00Z",
        )
        self.assertEqual(c_new, 200)
        self.assertEqual(d_new["open_incubations"][0]["topic"], "阳台薄荷越冬修剪")
        self.assertEqual(d_new["open_incubations"][0]["status"], "ready_to_revisit")
        self.assertTrue(d_new["open_incubations"][0]["shared"])

        # 主体私密未共享问题不可被伴侣按 question_id 探测写入
        _, priv_open = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "未公开私密独处心事",
                "shared": False,
            },
            actor_side="subject",
            now_iso="2026-11-13T11:10:00Z",
        )
        priv_qid = [i for i in priv_open["open_incubations"] if not i["shared"]][0]["question_id"]
        c_priv, d_priv = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "share_incubation_clue",
                "question_id": priv_qid,
                "clue_text": "尝试刺探私密问题",
            },
            actor_side="partner",
        )
        self.assertEqual(c_priv, 404)
        self.assertEqual(d_priv["reason_code"], "INCUBATION_NOT_FOUND")

    def test_22_partner_view_filters_all_unshared_items_across_collections(self) -> None:
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "私密手作",
                "chosen": "SECRET_PREF_ITEM",
                "options": ["SECRET_PREF_ITEM", "备选项"],
                "choice_mode": "spontaneous",
                "shared": False,
            },
            actor_side="subject",
        )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "park_or_resolve_conflict",
                "conflict_op": "park",
                "title": "SECRET_CONFLICT_TITLE",
                "side_a": "A",
                "side_b": "B",
                "shared": False,
            },
            actor_side="subject",
        )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "endorse",
                "title": "SECRET_SELF_RULE",
                "shared": False,
            },
            actor_side="subject",
        )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "SECRET_INCUBATION_TOPIC",
                "shared": False,
            },
            actor_side="subject",
        )

        p_code, p_doc = ad_view.read_v02_agency_depth_view(
            self.day_root,
            has_subject_capability=False,
        )
        self.assertEqual(p_code, 200)
        serialized = str(p_doc)
        for secret_marker in (
            "SECRET_PREF_ITEM",
            "SECRET_CONFLICT_TITLE",
            "SECRET_SELF_RULE",
            "SECRET_INCUBATION_TOPIC",
        ):
            self.assertNotIn(secret_marker, serialized)

        s_code, s_doc = ad_view.read_v02_agency_depth_view(
            self.day_root,
            has_subject_capability=True,
        )
        self.assertEqual(s_code, 200)
        for secret_marker in (
            "SECRET_PREF_ITEM",
            "SECRET_CONFLICT_TITLE",
            "SECRET_SELF_RULE",
            "SECRET_INCUBATION_TOPIC",
        ):
            self.assertIn(secret_marker, str(s_doc))

    def test_23_partner_forbidden_from_all_subject_only_actions_403(self) -> None:
        subject_payloads = [
            {
                "action": "record_choice_episode",
                "domain": "茶饮",
                "chosen": "煎茶",
                "options": ["煎茶", "红茶"],
                "choice_mode": "spontaneous",
            },
            {
                "action": "record_exposure_satiation",
                "domain": "茶饮",
                "title": "煎茶",
            },
            {
                "action": "park_or_resolve_conflict",
                "conflict_op": "park",
                "side_a": "A",
                "side_b": "B",
            },
            {
                "action": "record_regret_reflection",
                "regret_kind": "outcome_disappointment",
                "summary": "测试越权",
            },
            {
                "action": "manage_second_order_rule",
                "rule_op": "propose",
                "title": "测试越权规矩",
            },
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "测试越权孵化",
            },
        ]
        for pl in subject_payloads:
            code, doc = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                pl,
                actor_side="partner",
            )
            self.assertEqual(code, 403)
            self.assertEqual(doc["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        bad_actor_code, bad_actor_doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {"action": "offer_low_cost_option", "domain": "茶", "option_title": "白茶"},
            actor_side="guest",
        )
        self.assertEqual(bad_actor_code, 403)
        self.assertEqual(bad_actor_doc["reason_code"], "FORBIDDEN_ACTOR_SIDE")

    def test_24_malformed_unknown_or_forged_identity_fields_rejected_400(self) -> None:
        bad_payloads = [
            {},
            None,
            ["not", "a", "dict"],
            {"domain": "茶"},
            {"action": ""},
            {"action": " offer_low_cost_option "},
            {"action": "unknown_magic_action"},
            {"action": "offer_low_cost_option", "domain": "茶", "option_title": "绿茶", "evil_extra": 1},
            {"action": "offer_low_cost_option", "domain": "   ", "option_title": "绿茶"},
            {"action": "offer_low_cost_option", "domain": "茶", "option_title": "绿茶", "actor_side": "subject"},
            {"action": "propose_shared_gentle_rule", "title": "早睡", "trigger_window": "23:00", "auto_locked": True},
        ]
        for bad in bad_payloads:
            code, doc = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                bad,
                actor_side="partner",
            )
            self.assertEqual(code, 400, f"Expected 400 for {bad!r}, got {code}")
            self.assertEqual(doc["reason_code"], "INVALID_FIELDS")

    def test_25_operation_key_idempotent_replay_returns_duplicate(self) -> None:
        payload = {
            "action": "offer_low_cost_option",
            "domain": "音乐",
            "option_title": "雨声白噪音",
            "operation_key": "opk-idem-001",
        }
        c1, d1 = ad_view.mutate_v02_agency_depth_view(self.day_root, payload, actor_side="partner")
        self.assertEqual(c1, 200)
        self.assertEqual(d1["command_status"], "APPLIED")
        self.assertEqual(d1["status"], "applied")
        self.assertEqual(d1["revision"], 1)

        c2, d2 = ad_view.mutate_v02_agency_depth_view(self.day_root, payload, actor_side="partner")
        self.assertEqual(c2, 200)
        self.assertEqual(d2["command_status"], "DUPLICATE")
        self.assertEqual(d2["status"], "already_applied")
        self.assertEqual(d2["revision"], 1)
        self.assertEqual(len(d2["partner_options"]), 1)

    def test_26_operation_key_conflict_rejected_409(self) -> None:
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "offer_low_cost_option",
                "domain": "音乐",
                "option_title": "木吉他",
                "operation_key": "opk-conflict-001",
            },
            actor_side="partner",
        )
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "offer_low_cost_option",
                "domain": "音乐",
                "option_title": "篡改后的钢琴曲",
                "operation_key": "opk-conflict-001",
            },
            actor_side="partner",
        )
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "OPERATION_KEY_CONFLICT")

    def test_27_expected_revision_mismatch_rejected_409(self) -> None:
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "offer_low_cost_option",
                "domain": "茶饮",
                "option_title": "薄荷茶",
                "expected_revision": 5,
            },
            actor_side="partner",
        )
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "REVISION_MISMATCH")

    def test_28_summarize_agency_depth_for_context_respects_privacy_boundary(self) -> None:
        for t in ("08:00:00Z", "09:00:00Z"):
            ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_choice_episode",
                    "domain": "茶饮",
                    "chosen": "玄米焙茶",
                    "options": ["玄米焙茶", "绿茶"],
                    "choice_mode": "spontaneous",
                    "shared": True,
                },
                actor_side="subject",
                now_iso=f"2026-11-13T{t}",
            )
            ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_choice_episode",
                    "domain": "独处手记",
                    "chosen": "PRIV_PREF_SECRET",
                    "options": ["PRIV_PREF_SECRET", "其他"],
                    "choice_mode": "spontaneous",
                    "shared": False,
                },
                actor_side="subject",
                now_iso=f"2026-11-13T{t}",
            )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_exposure_satiation",
                "domain": "茶饮",
                "title": "玄米焙茶",
                "intensity": 0.80,
            },
            actor_side="subject",
            now_iso="2026-11-13T10:00:00Z",
        )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "propose_shared_gentle_rule",
                "title": "睡前调暗屏幕",
                "trigger_window": "23:30-01:00",
            },
            actor_side="partner",
            now_iso="2026-11-13T10:05:00Z",
        )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "share_incubation_clue",
                "topic": "木窗隔音",
                "clue_text": "硅胶密封条不挡采光",
            },
            actor_side="partner",
            now_iso="2026-11-13T10:10:00Z",
        )
        ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "outcome_disappointment",
                "summary": "PRIV_REGRET_SECRET",
            },
            actor_side="subject",
            now_iso="2026-11-13T10:15:00Z",
        )

        pub_ctx = ad_view.summarize_agency_depth_for_context(
            self.day_root,
            include_private=False,
            now_iso="2026-11-13T10:20:00Z",
        )
        for required_key in (
            "state_exists",
            "supported_preferences",
            "saturated_domains",
            "satiation_multipliers",
            "active_second_order_rules",
            "ready_incubations",
            "parked_conflict_count",
            "summary_line_cn",
        ):
            self.assertIn(required_key, pub_ctx)
        self.assertTrue(pub_ctx["state_exists"])
        self.assertIn("茶饮", pub_ctx["saturated_domains"])
        self.assertEqual(pub_ctx["satiation_multipliers"]["茶饮:玄米焙茶"], 0.25)
        self.assertEqual(len(pub_ctx["active_second_order_rules"]), 1)
        self.assertEqual(len(pub_ctx["ready_incubations"]), 1)
        self.assertNotIn("PRIV_PREF_SECRET", str(pub_ctx))
        self.assertNotIn("PRIV_REGRET_SECRET", str(pub_ctx))

        priv_ctx = ad_view.summarize_agency_depth_for_context(
            self.day_root,
            include_private=True,
            now_iso="2026-11-13T10:20:00Z",
        )
        self.assertIn("PRIV_PREF_SECRET", str(priv_ctx))

    def test_29_corrupt_state_file_returns_503_unavailable(self) -> None:
        state_file = self.day_root / "state" / "v02_agency_depth_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{corrupt json", encoding="utf-8")

        code, doc = ad_view.read_v02_agency_depth_view(self.day_root)
        self.assertEqual(code, 503)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["reason_code"], "CORRUPT_STATE_FILE")

    def test_30_production_code_hygiene_no_hardcoded_names_or_pronouns(self) -> None:
        engine_src = Path(ad_engine.__file__).read_text(encoding="utf-8")
        view_src = Path(ad_view.__file__).read_text(encoding="utf-8")
        combined = engine_src + "\n" + view_src
        for forbidden_token in ("她", "千代", "Chiyo", "chiyo", "Kazumi", "kazumi", "和泉"):
            self.assertNotIn(forbidden_token, combined)


if __name__ == "__main__":
    unittest.main()
