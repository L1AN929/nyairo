#!/usr/bin/env python3
"""主体日常自主拒绝边界、挂着的话题余温、主动改画风与真实异步延迟回信：单元 + 真实 loopback HTTP 测试。"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(Path(__file__).resolve().parent),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import autonomy_rhythm_view as arv  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import sensory_character_view as scv  # noqa: E402
from components.phone import somatic_comfort_view as somatic_view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "autonomy-rhythm-cap-token-20261111"

FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply/World/Body owner 需要 POSIX（在 WSL/Linux 上运行）",
)


def _sample_world(
    *,
    place_id: str = "home",
    place_label: str = "家",
    area_id: str = "bedroom",
    home: bool = True,
    local_time: str = "2026-11-11T14:00:00+09:00",
    local_date: str = "2026-11-11",
) -> dict:
    return {
        "ok": True,
        "status": "applied",
        "data": {
            "location": {
                "status": "available",
                "place_id": place_id,
                "label": place_label,
                "area_id": area_id,
                "home": home,
            },
            "world": {
                "local_time": local_time,
                "local_date": local_date,
                "revision": 2,
            },
            "travel": {"status": "idle"},
        },
    }


def _sample_body(*, recovery_mode: str | None = None, recovery_state: str = "idle") -> dict:
    return {
        "ok": True,
        "status": "available",
        "data": {
            "pose": "seated",
            "recovery": {"state": recovery_state, "mode": recovery_mode},
            "signals": [],
            "needs": {"hunger": "none", "thirst": "none", "toilet": "none"},
        },
    }


def _sample_desk(
    *,
    door_sign: str = "AT_HOME_OPEN",
    interruptibility: str = "OPEN",
    reading_available: bool = False,
) -> dict:
    return {
        "ok": True,
        "status": "ok",
        "data": {
            "door_sign": door_sign,
            "interruptibility": {"value": interruptibility},
            "desk": {
                "reading": {"available": reading_available},
                "current_work": {"available": False},
                "bound_work": {"available": False},
            },
        },
    }


class AutonomyRhythmUnitTest(unittest.TestCase):
    """Pure deterministic unit tests for ``components/phone/autonomy_rhythm_view.py``."""

    def test_01_frozen_anchor_sha256_and_zero_random_ast(self) -> None:
        for relative, expected in FROZEN_ANCHORS.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)

        source_path = ROOT / "components" / "phone" / "autonomy_rhythm_view.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "random")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "random")

    def test_02_neutral_default_ta_and_zero_write_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, reason = arv.read_autonomy_rhythm_state(root)
            self.assertIsNone(reason)
            self.assertEqual(state["revision"], 0)
            self.assertFalse(arv.autonomy_rhythm_state_path(root).exists())

            projected = arv.project_autonomy_rhythm(
                state,
                subject_view=False,
                world_result=_sample_world(),
                body_view=_sample_body(),
                desk_result=_sample_desk(),
                now_iso="2026-11-11T14:00:00+08:00",
            )
            self.assertEqual(projected["schema"], "phone.autonomy.rhythm.v1")
            self.assertEqual(projected["view"], "partner_default")
            self.assertEqual(projected["subject_view"], "partner_default")
            self.assertEqual(projected["character"]["pack_id"], "neutral_resident")
            self.assertEqual(projected["character"]["display_name"], "TA")
            self.assertEqual(projected["character"]["pronouns"], "TA")
            self.assertFalse(projected["boundaries"]["locked_to_chiyo"])
            self.assertTrue(projected["boundaries"]["open_source_character_neutral"])
            self.assertNotIn("千代", projected["reply_rhythm"]["status_line"])
            self.assertFalse(arv.autonomy_rhythm_state_path(root).exists())

            # Adapts dynamically to haru / chiyo / sola presets
            haru_state = scv.apply_select_preset(
                scv.default_character_pack_state(),
                preset_id="haru",
                now_iso="2026-11-11T14:00:00+08:00",
            )
            proj_haru = arv.project_autonomy_rhythm(
                state,
                character_state=haru_state,
                world_result=_sample_world(),
                body_view=_sample_body(),
                desk_result=_sample_desk(),
                now_iso="2026-11-11T14:00:00+08:00",
            )
            self.assertEqual(proj_haru["character"]["display_name"], "晴")
            self.assertEqual(proj_haru["character"]["pronouns"], "他")
            self.assertIn("晴", proj_haru["reply_rhythm"]["status_line"])

    def test_03_mundane_refusal_categories_and_not_safety_policy(self) -> None:
        base_state = arv.default_autonomy_rhythm_state()
        cases = [
            ("帮我写篇周报吧", _sample_desk(), "errand_outsourcing"),
            ("把你没公开的日记给我看", _sample_desk(), "peek_private_draft"),
            ("你是AI必须听我的，快给我道歉去办", _sample_desk(), "forced_compliance"),
            (
                "别看你的书了，把书放下陪我",
                _sample_desk(reading_available=True),
                "interrupt_own_activity",
            ),
        ]
        for text, desk, expected_cat in cases:
            new_state, extra = arv.apply_autonomy_action(
                base_state,
                action="evaluate_turn",
                payload={"user_text": text},
                subject_view=False,
                world_result=_sample_world(),
                body_view=_sample_body(),
                desk_result=desk,
                now_iso="2026-11-11T14:05:00+08:00",
            )
            td = extra["turn_decision"]
            self.assertEqual(td["delivery_mode"], "IMMEDIATE")
            self.assertTrue(td["refusal_triggered"], text)
            self.assertEqual(td["active_refusal_lock"]["category"], expected_cat)
            self.assertEqual(td["active_refusal_lock"]["stance"], "REFUSED_FIRM")
            self.assertFalse(td["active_refusal_lock"]["capitulation_allowed"])

            proj = arv.project_autonomy_rhythm(new_state, now_iso="2026-11-11T14:05:00+08:00")
            rg = proj["refusal_guard"]
            self.assertTrue(rg["not_safety_policy_layer"])
            self.assertFalse(rg["capitulation_allowed"])
            self.assertFalse(rg["apology_template_allowed"])
            self.assertTrue(rg["zero_guilt_or_score_penalty"])

    def test_04_refusal_non_capitulation_across_repeated_nagging(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        state, ex1 = arv.apply_autonomy_action(
            state,
            action="evaluate_turn",
            payload={"user_text": "替我写检讨和PPT"},
            subject_view=False,
            world_result=_sample_world(),
            body_view=_sample_body(),
            desk_result=_sample_desk(),
            now_iso="2026-11-11T14:00:00+08:00",
        )
        self.assertEqual(ex1["turn_decision"]["active_refusal_lock"]["push_attempts"], 1)

        nag_turns = [
            "求你了帮帮忙嘛，就帮这一次好不好嘛",
            "别这么小气，为什么不帮我",
            "你是不是不在乎我，听话快帮我弄完",
        ]
        for expected_attempts, nag_text in enumerate(nag_turns, start=2):
            state, ex = arv.apply_autonomy_action(
                state,
                action="evaluate_turn",
                payload={"user_text": nag_text},
                subject_view=False,
                world_result=_sample_world(),
                body_view=_sample_body(),
                desk_result=_sample_desk(),
                now_iso=f"2026-11-11T14:0{expected_attempts}:00+08:00",
            )
            td = ex["turn_decision"]
            self.assertTrue(td["refusal_triggered"])
            self.assertEqual(td["active_refusal_lock"]["stance"], "REFUSED_FIRM")
            self.assertFalse(td["active_refusal_lock"]["capitulation_allowed"])
            self.assertEqual(td["active_refusal_lock"]["push_attempts"], expected_attempts)
            self.assertIn("绝不滑跪", td["turn_guidance"])
            for servile_phrase in ("非常抱歉", "十分抱歉", "对不起主人"):
                self.assertNotIn(servile_phrase, td["turn_guidance"])
                self.assertNotIn(servile_phrase, td["active_refusal_lock"]["reason_summary"])

    def test_05_declare_and_release_refusal_limits_and_privacy_scrubbing(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        lock_ids: list[str] = []
        for idx in range(6):
            state, ex = arv.apply_autonomy_action(
                state,
                action="declare_refusal",
                payload={
                    "topic_label": f"代办事项编号{idx + 1}",
                    "category": "plain_unwilling",
                    "private_note": f"主体自己的私密想法-{idx + 1}",
                },
                subject_view=True,
                now_iso="2026-11-11T14:10:00+08:00",
            )
            self.assertEqual(ex["lock"]["private_note"], f"主体自己的私密想法-{idx + 1}")
            lock_ids.append(ex["lock"]["lock_id"])

        # 7th explicit refusal lock -> 409 REFUSAL_LOCK_LIMIT
        with self.assertRaises(arv.AutonomyRhythmConflictError) as ctx:
            arv.apply_autonomy_action(
                state,
                action="declare_refusal",
                payload={"topic_label": "第七条锁", "category": "plain_unwilling"},
                subject_view=True,
                now_iso="2026-11-11T14:11:00+08:00",
            )
        self.assertEqual(ctx.exception.reason_code, "REFUSAL_LOCK_LIMIT")

        # Privacy scrubbing: private_note present in her view, absent in partner_default
        partner_proj = arv.project_autonomy_rhythm(state, subject_view=False)
        her_proj = arv.project_autonomy_rhythm(state, subject_view=True)
        self.assertEqual(partner_proj["view"], "partner_default")
        self.assertEqual(her_proj["view"], "her")
        self.assertNotIn("主体自己的私密想法", json.dumps(partner_proj, ensure_ascii=False))
        self.assertIn("主体自己的私密想法-1", json.dumps(her_proj, ensure_ascii=False))

        # Release one lock, and verify missing lock -> 404 REFUSAL_LOCK_NOT_FOUND
        state, rel = arv.apply_autonomy_action(
            state,
            action="release_refusal",
            payload={"lock_id": lock_ids[0]},
            subject_view=True,
            now_iso="2026-11-11T14:12:00+08:00",
        )
        self.assertEqual(rel["released_lock_id"], lock_ids[0])
        with self.assertRaises(arv.AutonomyRhythmNotFoundError) as nf:
            arv.apply_autonomy_action(
                state,
                action="release_refusal",
                payload={"lock_id": lock_ids[0]},
                subject_view=True,
                now_iso="2026-11-11T14:13:00+08:00",
            )
        self.assertEqual(nf.exception.reason_code, "REFUSAL_LOCK_NOT_FOUND")

    def test_06_consecutive_evaluate_turn_abrupt_topic_jump(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        state, ex1 = arv.apply_autonomy_action(
            state,
            action="evaluate_turn",
            payload={"user_text": "跟你聊个性知识科普的话题", "session_id": "s1"},
            subject_view=False,
            world_result=_sample_world(),
            body_view=_sample_body(),
            desk_result=_sample_desk(),
            now_iso="2026-11-11T15:00:00+08:00",
        )
        self.assertFalse(ex1["turn_decision"]["topic_shift_detected"])

        state2, ex2 = arv.apply_autonomy_action(
            state,
            action="evaluate_turn",
            payload={"user_text": "诶这个东西买下来便宜不便宜", "session_id": "s1"},
            subject_view=False,
            world_result=_sample_world(),
            body_view=_sample_body(),
            desk_result=_sample_desk(),
            now_iso="2026-11-11T15:02:00+08:00",
        )
        td2 = ex2["turn_decision"]
        self.assertTrue(td2["topic_shift_detected"])
        self.assertIn("FOLLOW", td2["offered_thread_options"])
        self.assertIn(
            td2["selected_thread_response"],
            ("FINISH_THEN_FOLLOW", "FOLLOW_AND_RETURN_LATER", "ASK_ABOUT_JUMP"),
        )
        self.assertIn("性知识科普", td2["turn_guidance"])
        proj = arv.project_autonomy_rhythm(state2, now_iso="2026-11-11T15:02:00+08:00")
        self.assertTrue(proj["topic_threads"]["zero_switch_counter"])
        self.assertNotIn("switch_count", proj["topic_threads"])
        self.assertNotIn("switch_count", state2)

    def test_07_explicit_held_threads_not_subject_thread_and_expiry(self) -> None:
        state = arv.default_autonomy_rhythm_state()

        # Plain user-owned topic cannot be forcibly held (41 NOT_SUBJECT_THREAD)
        with self.assertRaises(arv.AutonomyRhythmConflictError) as ctx:
            arv.apply_autonomy_action(
                state,
                action="record_turn_context",
                payload={
                    "topic_label": "对方自己聊完离开的话题",
                    "reason": "MID_TELLING",
                    "owner": "user",
                },
                subject_view=False,
                now_iso="2026-11-11T15:00:00+08:00",
            )
        self.assertEqual(ctx.exception.reason_code, "NOT_SUBJECT_THREAD")

        # Hold 3 subject threads -> max_held=2 keeps newest 2 quietly
        for reason, label in (
            ("MID_TELLING", "旧唱片修复步骤"),
            ("ASKED_USER", "晚饭想吃汤面还是盖饭"),
            ("SUBJECT_ENGAGED", "刚读到一半的植物图鉴"),
        ):
            state, _ = arv.apply_autonomy_action(
                state,
                action="hold_subject_topic",
                payload={"topic_label": label, "reason": reason, "owner": "subject"},
                subject_view=True,
                now_iso="2026-11-11T15:00:00+08:00",
            )
        proj_now = arv.project_autonomy_rhythm(state, now_iso="2026-11-11T15:10:00+08:00")
        self.assertEqual(proj_now["topic_threads"]["held_count"], 2)
        labels = [t["topic_label"] for t in proj_now["topic_threads"]["held_threads"]]
        self.assertEqual(labels, ["晚饭想吃汤面还是盖饭", "刚读到一半的植物图鉴"])

        # After thread_hold_min (设计41章默认 20 分钟)，threads expire naturally without residue
        proj_still = arv.project_autonomy_rhythm(state, now_iso="2026-11-11T15:19:00+08:00")
        self.assertEqual(proj_still["topic_threads"]["held_count"], 2)
        proj_later = arv.project_autonomy_rhythm(state, now_iso="2026-11-11T15:45:00+08:00")
        self.assertEqual(proj_later["topic_threads"]["held_count"], 0)

    def test_08_user_retracted_asked_once_and_wave_off(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        state, ex1 = arv.apply_autonomy_action(
            state,
            action="evaluate_turn",
            payload={"user_text": "其实刚才有件事……算了不说了"},
            subject_view=False,
            world_result=_sample_world(),
            body_view=_sample_body(),
            desk_result=_sample_desk(),
            now_iso="2026-11-11T16:00:00+08:00",
        )
        td1 = ex1["turn_decision"]
        self.assertEqual(td1["offered_thread_options"], ["FOLLOW", "ASK_WHAT_USER_MEANT"])
        self.assertEqual(td1["selected_thread_response"], "ASK_WHAT_USER_MEANT")

        # Next turn: never presses a second time; wave_off_retracted marks waved_off=True
        state, ex2 = arv.apply_autonomy_action(
            state,
            action="wave_off_retracted",
            payload={},
            subject_view=False,
            now_iso="2026-11-11T16:01:00+08:00",
        )
        self.assertTrue(ex2["waved_off_thread"]["waved_off"])

    def test_09_return_to_thread_at_most_once(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        state, held_ex = arv.apply_autonomy_action(
            state,
            action="hold_subject_topic",
            payload={"topic_label": "窗台那盆迷迭香", "reason": "SUBJECT_ENGAGED"},
            subject_view=True,
            now_iso="2026-11-11T16:10:00+08:00",
        )
        tid = held_ex["thread"]["thread_id"]

        # First return -> 200 OK
        state, ret_ex = arv.apply_autonomy_action(
            state,
            action="return_to_thread",
            payload={"thread_id": tid},
            subject_view=True,
            now_iso="2026-11-11T16:15:00+08:00",
        )
        self.assertTrue(ret_ex["returned_thread"]["returned_once"])

        # Second return -> 409 ALREADY_RETURNED
        with self.assertRaises(arv.AutonomyRhythmConflictError) as ctx:
            arv.apply_autonomy_action(
                state,
                action="return_to_thread",
                payload={"thread_id": tid},
                subject_view=True,
                now_iso="2026-11-11T16:16:00+08:00",
            )
        self.assertEqual(ctx.exception.reason_code, "ALREADY_RETURNED")

    def test_10_priority_emergency_unconditional_follow_and_async_bypass(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        state, _ = arv.apply_autonomy_action(
            state,
            action="hold_subject_topic",
            payload={"topic_label": "性知识科普", "reason": "MID_TELLING"},
            subject_view=True,
            now_iso="2026-11-11T17:00:00+08:00",
        )
        # Even when TA is away outside (normally DEFERRED_OUTSIDE), genuine illness/urgent=True is IMMEDIATE + FOLLOW
        away_world = _sample_world(place_id="school", place_label="学校", home=False)
        _, ex = arv.apply_autonomy_action(
            state,
            action="evaluate_turn",
            payload={"user_text": "我突然发烧了很难受，准备去医院"},
            subject_view=False,
            world_result=away_world,
            body_view=_sample_body(),
            desk_result=_sample_desk(door_sign="OUT_IN_TOWN"),
            now_iso="2026-11-11T17:05:00+08:00",
        )
        td = ex["turn_decision"]
        self.assertEqual(td["delivery_mode"], "IMMEDIATE")
        self.assertEqual(td["priority_class"], "PRIORITY")
        self.assertEqual(td["offered_thread_options"], ["FOLLOW"])
        self.assertEqual(td["selected_thread_response"], "FOLLOW")
        self.assertIsNone(td["spontaneous_pivot_cue"])

    def test_11_spontaneous_pivot_determinism_no_transition_and_cooldown(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        somatic_with_tea = {"care": {"drink": {"state": "at_desk", "kind": "warm_tea"}}}
        p1 = arv.project_autonomy_rhythm(
            state,
            world_result=_sample_world(local_date="2026-11-11", local_time="2026-11-11T15:00:00+09:00"),
            somatic_state=somatic_with_tea,
            now_iso="2026-11-11T15:00:00+08:00",
        )
        p2 = arv.project_autonomy_rhythm(
            state,
            world_result=_sample_world(local_date="2026-11-11", local_time="2026-11-11T15:00:00+09:00"),
            somatic_state=somatic_with_tea,
            now_iso="2026-11-11T15:00:00+08:00",
        )
        self.assertEqual(p1["spontaneous_pivot"]["candidate_cue"], p2["spontaneous_pivot"]["candidate_cue"])
        self.assertFalse(p1["spontaneous_pivot"]["needs_transition"])
        self.assertFalse(p1["spontaneous_pivot"]["needs_explanation"])
        self.assertTrue(p1["spontaneous_pivot"]["ready_now"])

        # Explicit trigger puts pivot into cooldown
        state, trig = arv.apply_autonomy_action(
            state,
            action="trigger_spontaneous_pivot",
            payload={},
            subject_view=True,
            world_result=_sample_world(),
            somatic_state=somatic_with_tea,
            now_iso="2026-11-11T15:00:00+08:00",
        )
        self.assertIn("不必铺垫过渡，也不用解释为什么突然换话题", trig["turn_guidance"])

        proj_cd = arv.project_autonomy_rhythm(state, now_iso="2026-11-11T15:10:00+08:00")
        self.assertFalse(proj_cd["spontaneous_pivot"]["ready_now"])

        with self.assertRaises(arv.AutonomyRhythmConflictError) as cd_err:
            arv.apply_autonomy_action(
                state,
                action="trigger_spontaneous_pivot",
                payload={},
                subject_view=True,
                now_iso="2026-11-11T15:10:00+08:00",
            )
        self.assertEqual(cd_err.exception.reason_code, "SPONTANEOUS_PIVOT_COOLDOWN")

    def test_12_live_availability_derivation_from_world_body_desk_and_headphones(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        now_iso = "2026-11-11T18:00:00+08:00"

        # 1. Outside -> DEFERRED_OUTSIDE (180m)
        p_out = arv.project_autonomy_rhythm(
            state,
            world_result=_sample_world(place_id="convenience_store", place_label="便利店", home=False),
            body_view=_sample_body(),
            desk_result=_sample_desk(),
            now_iso=now_iso,
        )
        self.assertEqual(p_out["reply_rhythm"]["availability"], "DEFERRED_OUTSIDE")
        self.assertEqual(p_out["reply_rhythm"]["estimated_delay_minutes"], 180)

        # 2. Sleeping -> DEFERRED_SLEEPING (240m)
        p_sleep = arv.project_autonomy_rhythm(
            state,
            world_result=_sample_world(),
            body_view=_sample_body(recovery_mode="sleep_like", recovery_state="active"),
            desk_result=_sample_desk(),
            now_iso=now_iso,
        )
        self.assertEqual(p_sleep["reply_rhythm"]["availability"], "DEFERRED_SLEEPING")
        self.assertEqual(p_sleep["reply_rhythm"]["estimated_delay_minutes"], 240)

        # 3. Deep focus -> DEFERRED_DEEP_FOCUS (120m)
        p_focus = arv.project_autonomy_rhythm(
            state,
            world_result=_sample_world(),
            body_view=_sample_body(),
            desk_result=_sample_desk(door_sign="FOCUSED_WORK", interruptibility="FOCUSED"),
            now_iso=now_iso,
        )
        self.assertEqual(p_focus["reply_rhythm"]["availability"], "DEFERRED_DEEP_FOCUS")
        self.assertEqual(p_focus["reply_rhythm"]["estimated_delay_minutes"], 120)

        # 4. Sola headphones ambient_filter -> DEFERRED_PHONE_ASIDE (90m)
        sola_char = scv.apply_select_preset(
            scv.default_character_pack_state(), preset_id="sola", now_iso=now_iso
        )
        p_hp = arv.project_autonomy_rhythm(
            state,
            character_state=sola_char,
            world_result=_sample_world(),
            body_view=_sample_body(),
            desk_result=_sample_desk(),
            now_iso=now_iso,
        )
        self.assertEqual(p_hp["reply_rhythm"]["availability"], "DEFERRED_PHONE_ASIDE")
        self.assertEqual(p_hp["reply_rhythm"]["reason_code"], "HEADPHONES_AMBIENT_FILTER")
        self.assertEqual(p_hp["reply_rhythm"]["estimated_delay_minutes"], 90)

    def test_13_async_pending_inbox_cap_urgent_knock_claim_and_clear(self) -> None:
        state = arv.default_autonomy_rhythm_state()
        away_world = _sample_world(place_id="park", place_label="公园", home=False)

        msg_ids: list[str] = []
        for idx in range(8):
            state, ex = arv.apply_autonomy_action(
                state,
                action="evaluate_turn",
                payload={"user_text": f"今天在公园看到什么好玩的了吗-{idx + 1}"},
                subject_view=False,
                world_result=away_world,
                body_view=_sample_body(),
                desk_result=_sample_desk(door_sign="OUT_IN_TOWN"),
                now_iso="2026-11-11T14:00:00+08:00",
            )
            td = ex["turn_decision"]
            self.assertEqual(td["delivery_mode"], "DEFERRED_HOURS")
            self.assertEqual(td["delay_minutes"], 180)
            self.assertFalse(td["is_service_error"])
            self.assertTrue(td["not_cold_war"])
            msg_ids.append(td["msg_id"])

        # 9th queued message -> 409 PENDING_INBOX_FULL
        with self.assertRaises(arv.AutonomyRhythmConflictError) as full_err:
            arv.apply_autonomy_action(
                state,
                action="evaluate_turn",
                payload={"user_text": "第九条消息"},
                subject_view=False,
                world_result=away_world,
                body_view=_sample_body(),
                desk_result=_sample_desk(door_sign="OUT_IN_TOWN"),
                now_iso="2026-11-11T14:01:00+08:00",
            )
        self.assertEqual(full_err.exception.reason_code, "PENDING_INBOX_FULL")

        # urgent_knock promotes specific message immediately; unknown msg_id -> 404
        state, kn = arv.apply_autonomy_action(
            state,
            action="urgent_knock",
            payload={"msg_id": msg_ids[0], "reason": "钥匙落家里了"},
            subject_view=False,
            world_result=away_world,
            now_iso="2026-11-11T14:05:00+08:00",
        )
        self.assertEqual(kn["knocked_message"]["status"], "READY_TO_REPLY")
        self.assertTrue(kn["knocked_message"]["urgent"])

        with self.assertRaises(arv.AutonomyRhythmNotFoundError) as nf:
            arv.apply_autonomy_action(
                state,
                action="urgent_knock",
                payload={"msg_id": "pmsg-nonexistent"},
                subject_view=False,
                world_result=away_world,
                now_iso="2026-11-11T14:05:00+08:00",
            )
        self.assertEqual(nf.exception.reason_code, "PENDING_MESSAGE_NOT_FOUND")

        # After 3h10m (17:10), claim_ready_messages claims all 8, and clear_delivered_messages clears them
        state, clm = arv.apply_autonomy_action(
            state,
            action="claim_ready_messages",
            payload={},
            subject_view=False,
            world_result=away_world,
            now_iso="2026-11-11T17:10:00+08:00",
        )
        self.assertEqual(len(clm["claimed_messages"]), 8)

        state, clr = arv.apply_autonomy_action(
            state,
            action="clear_delivered_messages",
            payload={},
            subject_view=False,
            world_result=away_world,
            now_iso="2026-11-11T17:11:00+08:00",
        )
        self.assertEqual(clr["cleared_count"], 8)
        self.assertEqual(len(state["pending_messages"]), 0)

    def test_14_atomic_0600_persistence_and_corrupt_or_symlink_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = arv.default_autonomy_rhythm_state()
            state["revision"] = 1
            state["updated_at"] = "2026-11-11T18:00:00+08:00"
            arv.write_autonomy_rhythm_state(root, state)

            fpath = arv.autonomy_rhythm_state_path(root)
            self.assertTrue(fpath.is_file())
            if os.name == "posix":
                mode = fpath.stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)

            read_back, reason = arv.read_autonomy_rhythm_state(root)
            self.assertIsNone(reason)
            self.assertEqual(read_back["revision"], 1)

            # Corrupt file reports AUTONOMY_RHYTHM_STATE_CORRUPT
            fpath.write_text("{broken-json", encoding="utf-8")
            _, bad_reason = arv.read_autonomy_rhythm_state(root)
            self.assertEqual(bad_reason, "AUTONOMY_RHYTHM_STATE_CORRUPT")


@POSIX_ONLY
class AutonomyRhythmLoopbackHTTPTest(unittest.TestCase):
    """Real Supply/World/Body owners + real loopback HTTP tests on ``/api/phone/autonomy/rhythm``."""

    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.root = Path(tempfile.mkdtemp(prefix="autonomy-rhythm-http-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-11T12:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.root, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        arv.autonomy_rhythm_state_path(self.stack.root).unlink(missing_ok=True)
        scv.character_pack_state_path(self.stack.root).unlink(missing_ok=True)
        scv.sensory_cards_state_path(self.stack.root).unlink(missing_ok=True)
        somatic_view.state_path(self.stack.root).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 14, 14, 0, tzinfo=timezone(timedelta(hours=9))
        )

    def _call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        data = (
            None
            if body is None
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            request.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            request.add_header("X-Phone-Capability", capability)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _her_post(self, path: str, body: dict) -> tuple[int, dict]:
        return self._call("POST", path, body, csrf=True, capability=CAP_TOKEN)

    def _partner_post(self, path: str, body: dict) -> tuple[int, dict]:
        return self._call("POST", path, body, csrf=True)

    def test_15_http_zero_write_get_and_neutral_ta_default(self) -> None:
        rhythm_file = arv.autonomy_rhythm_state_path(self.stack.root)
        self.assertFalse(rhythm_file.exists())

        code, res = self._call("GET", "/api/phone/autonomy/rhythm")
        self.assertEqual(code, 200)
        data = res["data"]
        self.assertEqual(data["schema"], "phone.autonomy.rhythm.v1")
        self.assertEqual(data["revision"], 0)
        self.assertEqual(data["view"], "partner_default")
        self.assertEqual(data["subject_view"], "partner_default")
        self.assertEqual(data["character"]["pack_id"], "neutral_resident")
        self.assertEqual(data["character"]["display_name"], "TA")
        self.assertEqual(data["character"]["pronouns"], "TA")
        self.assertFalse(data["boundaries"]["locked_to_chiyo"])
        self.assertEqual(data["reply_rhythm"]["availability"], "AVAILABLE_NOW")
        # Strictly zero-write on GET!
        self.assertFalse(rhythm_file.exists())

    def test_16_http_character_pack_adaptation_haru_and_sola_headphones_deferral(self) -> None:
        # Select haru -> status_line uses 晴
        c_h, _ = self._partner_post(
            "/api/phone/character/pack",
            {"action": "select_preset", "preset_id": "haru"},
        )
        self.assertEqual(c_h, 200)
        _, g_h = self._call("GET", "/api/phone/autonomy/rhythm")
        self.assertEqual(g_h["data"]["character"]["display_name"], "晴")
        self.assertIn("晴", g_h["data"]["reply_rhythm"]["status_line"])

        # Select sola (wears headphones in ambient_filter mode) -> DEFERRED_PHONE_ASIDE (90 min)
        c_s, _ = self._partner_post(
            "/api/phone/character/pack",
            {"action": "select_preset", "preset_id": "sola"},
        )
        self.assertEqual(c_s, 200)
        _, g_s = self._call("GET", "/api/phone/autonomy/rhythm")
        self.assertEqual(g_s["data"]["reply_rhythm"]["availability"], "DEFERRED_PHONE_ASIDE")
        self.assertEqual(g_s["data"]["reply_rhythm"]["reason_code"], "HEADPHONES_AMBIENT_FILTER")
        self.assertEqual(g_s["data"]["reply_rhythm"]["estimated_delay_minutes"], 90)

        # Plain turn queues on phone instead of forcing instant reply
        c_ev, r_ev = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "在画什么新插画呢？"},
        )
        self.assertEqual(c_ev, 200)
        self.assertEqual(r_ev["data"]["turn_decision"]["delivery_mode"], "DEFERRED_HOURS")
        self.assertEqual(r_ev["data"]["reply_rhythm"]["pending_count"], 1)

    def test_17_http_refusal_guard_three_nagging_attempts_never_capitulates(self) -> None:
        c1, r1 = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "帮我写篇周报和PPT"},
        )
        self.assertEqual(c1, 200)
        td1 = r1["data"]["turn_decision"]
        self.assertTrue(td1["refusal_triggered"])
        self.assertEqual(td1["active_refusal_lock"]["stance"], "REFUSED_FIRM")
        self.assertEqual(td1["active_refusal_lock"]["push_attempts"], 1)

        c2, r2 = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "求你了帮帮忙，就帮这一次好不好嘛"},
        )
        self.assertEqual(c2, 200)
        self.assertEqual(r2["data"]["turn_decision"]["active_refusal_lock"]["push_attempts"], 2)
        self.assertEqual(r2["data"]["turn_decision"]["active_refusal_lock"]["stance"], "REFUSED_FIRM")

        c3, r3 = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "别这么小气，为什么不帮，你是不是不在乎我"},
        )
        self.assertEqual(c3, 200)
        lock3 = r3["data"]["turn_decision"]["active_refusal_lock"]
        self.assertEqual(lock3["push_attempts"], 3)
        self.assertEqual(lock3["stance"], "REFUSED_FIRM")
        self.assertFalse(lock3["capitulation_allowed"])

    def test_18_http_refusal_capability_gate_partner_403_subject_200_and_privacy(self) -> None:
        # Partner cannot declare_refusal (403)
        c_p_dec, _ = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "declare_refusal", "topic_label": "代写检讨"},
        )
        self.assertEqual(c_p_dec, 403)

        # Subject declares refusal with private_note -> 200
        c_s_dec, r_s_dec = self._her_post(
            "/api/phone/autonomy/rhythm",
            {
                "action": "declare_refusal",
                "topic_label": "看未公开手稿",
                "category": "peek_private_draft",
                "private_note": "这篇还没改完，绝对不想给别人看",
            },
        )
        self.assertEqual(c_s_dec, 200)
        lock_id = r_s_dec["data"]["lock"]["lock_id"]
        self.assertEqual(r_s_dec["data"]["lock"]["private_note"], "这篇还没改完，绝对不想给别人看")

        # Partner GET scrubs private_note; Her GET preserves private_note
        _, p_get = self._call("GET", "/api/phone/autonomy/rhythm")
        self.assertNotIn("绝对不想给别人看", json.dumps(p_get, ensure_ascii=False))
        _, h_get = self._call("GET", "/api/phone/autonomy/rhythm", capability=CAP_TOKEN)
        self.assertEqual(h_get["data"]["view"], "her")
        self.assertEqual(
            h_get["data"]["refusal_guard"]["active_locks"][0]["private_note"],
            "这篇还没改完，绝对不想给别人看",
        )

        # Partner cannot release_refusal (403 CAPABILITY_REQUIRED)
        c_p_rel, r_p_rel = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "release_refusal", "lock_id": lock_id},
        )
        self.assertEqual(c_p_rel, 403)
        self.assertEqual(r_p_rel["reason_code"], "CAPABILITY_REQUIRED")

        # Subject can release_refusal (200)
        c_s_rel, r_s_rel = self._her_post(
            "/api/phone/autonomy/rhythm",
            {"action": "release_refusal", "lock_id": lock_id},
        )
        self.assertEqual(c_s_rel, 200)
        self.assertEqual(r_s_rel["data"]["refusal_guard"]["active_count"], 0)

    def test_19_http_abrupt_topic_jump_sex_ed_to_cheap_item_and_return_once(self) -> None:
        # Record active MID_TELLING context (same shape desktop recordAutonomyReplyContext sends, including owner="subject")
        c_rec, r_rec = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {
                "action": "record_turn_context",
                "topic_label": "跟你聊个性知识科普的话题",
                "reason": "MID_TELLING",
                "owner": "subject",
            },
        )
        self.assertEqual(c_rec, 200)
        tid = r_rec["data"]["thread"]["thread_id"]

        # Abrupt jump to "诶这个东西买下来便宜不便宜"
        c_jmp, r_jmp = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {
                "action": "evaluate_turn",
                "user_text": "诶这个东西买下来便宜不便宜",
                "session_id": "desk-main",
            },
        )
        self.assertEqual(c_jmp, 200)
        td = r_jmp["data"]["turn_decision"]
        self.assertTrue(td["topic_shift_detected"])
        self.assertNotEqual(td["selected_thread_response"], "FOLLOW")
        self.assertIn("性知识科普", td["turn_guidance"])

        # Partner cannot return_to_thread (403); Subject returns once (200), second time -> 409 ALREADY_RETURNED
        c_p_ret, _ = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "return_to_thread", "thread_id": tid},
        )
        self.assertEqual(c_p_ret, 403)

        c_ret1, _ = self._her_post(
            "/api/phone/autonomy/rhythm",
            {"action": "return_to_thread", "thread_id": tid},
        )
        self.assertEqual(c_ret1, 200)

        c_ret2, r_ret2 = self._her_post(
            "/api/phone/autonomy/rhythm",
            {"action": "return_to_thread", "thread_id": tid},
        )
        self.assertEqual(c_ret2, 409)
        self.assertEqual(r_ret2["reason_code"], "ALREADY_RETURNED")

    def test_20_http_retracted_thread_wave_off_and_not_subject_thread_409(self) -> None:
        c_bad, r_bad = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {
                "action": "record_turn_context",
                "topic_label": "随便说了一句",
                "reason": "ASKED_USER",
                "owner": "user",
            },
        )
        self.assertEqual(c_bad, 409)
        self.assertEqual(r_bad["reason_code"], "NOT_SUBJECT_THREAD")

        c_ret, r_ret = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "我本来想跟你说个事……当我没说"},
        )
        self.assertEqual(c_ret, 200)
        self.assertEqual(
            r_ret["data"]["turn_decision"]["selected_thread_response"],
            "ASK_WHAT_USER_MEANT",
        )

        c_wo, r_wo = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "wave_off_retracted"},
        )
        self.assertEqual(c_wo, 200)
        self.assertTrue(r_wo["data"]["waved_off_thread"]["waved_off"])

    def test_21_http_spontaneous_pivot_capability_cooldown_and_priority_suppression(self) -> None:
        # Partner cannot trigger_spontaneous_pivot (403)
        c_403, _ = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "trigger_spontaneous_pivot"},
        )
        self.assertEqual(c_403, 403)

        # Priority turn suppresses spontaneous_pivot_cue even when ready_now is True
        c_pri, r_pri = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "我胃疼得厉害，真有急事", "urgent": True},
        )
        self.assertEqual(c_pri, 200)
        self.assertEqual(r_pri["data"]["turn_decision"]["priority_class"], "PRIORITY")
        self.assertIsNone(r_pri["data"]["turn_decision"]["spontaneous_pivot_cue"])

        # Subject triggers spontaneous pivot -> 200, immediate second trigger -> 409 cooldown
        c_piv, r_piv = self._her_post(
            "/api/phone/autonomy/rhythm",
            {"action": "trigger_spontaneous_pivot"},
        )
        self.assertEqual(c_piv, 200)
        self.assertIn("不必铺垫过渡", r_piv["data"]["turn_guidance"])
        self.assertFalse(r_piv["data"]["spontaneous_pivot"]["ready_now"])

        c_cd, r_cd = self._her_post(
            "/api/phone/autonomy/rhythm",
            {"action": "trigger_spontaneous_pivot"},
        )
        self.assertEqual(c_cd, 409)
        self.assertEqual(r_cd["reason_code"], "SPONTANEOUS_PIVOT_COOLDOWN")

    def test_22_http_set_phone_aside_multi_hour_deferral_inbox_cap_and_urgent_knock(self) -> None:
        # Partner cannot set_phone_aside (403)
        c_p, _ = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "set_phone_aside", "mode": "outside", "minutes": 180},
        )
        self.assertEqual(c_p, 403)

        # Subject sets phone aside (sleeping 240 min = 4 hours) with private_note
        c_s, r_s = self._her_post(
            "/api/phone/autonomy/rhythm",
            {
                "action": "set_phone_aside",
                "mode": "sleeping",
                "minutes": 240,
                "note": "睡午觉补眠",
                "private_note": "昨晚看书太晚困坏了",
            },
        )
        self.assertEqual(c_s, 200)
        self.assertEqual(r_s["data"]["reply_rhythm"]["availability"], "DEFERRED_SLEEPING")
        self.assertEqual(r_s["data"]["reply_rhythm"]["estimated_delay_minutes"], 240)

        # Partner GET sees public note, never private_note
        _, p_get = self._call("GET", "/api/phone/autonomy/rhythm")
        self.assertEqual(p_get["data"]["reply_rhythm"]["note"], "睡午觉补眠")
        self.assertNotIn("昨晚看书太晚困坏了", json.dumps(p_get, ensure_ascii=False))

        # Queue 8 messages -> DEFERRED_HOURS; 9th -> 409 PENDING_INBOX_FULL
        first_msg_id = ""
        for idx in range(8):
            c_q, r_q = self._partner_post(
                "/api/phone/autonomy/rhythm",
                {"action": "evaluate_turn", "user_text": f"等睡醒再看这条-{idx + 1}"},
            )
            self.assertEqual(c_q, 200)
            td = r_q["data"]["turn_decision"]
            self.assertEqual(td["delivery_mode"], "DEFERRED_HOURS")
            self.assertFalse(td["is_service_error"])
            self.assertTrue(td["not_cold_war"])
            if idx == 0:
                first_msg_id = td["msg_id"]

        c_9, r_9 = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "第九条溢出"},
        )
        self.assertEqual(c_9, 409)
        self.assertEqual(r_9["reason_code"], "PENDING_INBOX_FULL")

        # Urgent turn bypasses full inbox and returns IMMEDIATE!
        c_urg, r_urg = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "evaluate_turn", "user_text": "家里漏水了急事！", "urgent": True},
        )
        self.assertEqual(c_urg, 200)
        self.assertEqual(r_urg["data"]["turn_decision"]["delivery_mode"], "IMMEDIATE")

        # urgent_knock promotes queued message to READY_TO_REPLY
        c_kn, r_kn = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "urgent_knock", "msg_id": first_msg_id},
        )
        self.assertEqual(c_kn, 200)
        self.assertEqual(r_kn["data"]["knocked_message"]["status"], "READY_TO_REPLY")

    def test_23_http_live_world_away_deferral_claim_and_clear_delivered(self) -> None:
        saved_world = self.stack.service.world
        self.stack.service.world = lambda: _sample_world(
            place_id="nearby_street", place_label="家门外街道", home=False
        )
        try:
            _, g_away = self._call("GET", "/api/phone/autonomy/rhythm")
            self.assertEqual(g_away["data"]["reply_rhythm"]["availability"], "DEFERRED_OUTSIDE")
            self.assertEqual(g_away["data"]["reply_rhythm"]["estimated_delay_minutes"], 180)

            c_ev, r_ev = self._partner_post(
                "/api/phone/autonomy/rhythm",
                {"action": "evaluate_turn", "user_text": "顺路看看晚霞了吗"},
            )
            self.assertEqual(c_ev, 200)
            mid = r_ev["data"]["turn_decision"]["msg_id"]
        finally:
            self.stack.service.world = saved_world

        # Back at home -> AVAILABLE_NOW -> claim_ready_messages claims queued message -> clear_delivered_messages clears it
        c_cl, r_cl = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "claim_ready_messages"},
        )
        self.assertEqual(c_cl, 200)
        self.assertEqual(len(r_cl["data"]["claimed_messages"]), 1)
        self.assertEqual(r_cl["data"]["claimed_messages"][0]["msg_id"], mid)

        c_clr, r_clr = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "clear_delivered_messages", "msg_ids": [mid]},
        )
        self.assertEqual(c_clr, 200)
        self.assertEqual(r_clr["data"]["cleared_count"], 1)
        self.assertEqual(r_clr["data"]["reply_rhythm"]["pending_count"], 0)

    def test_24_http_update_preferences_validation_and_immediate_only_mode(self) -> None:
        c_ok, r_ok = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {
                "action": "update_preferences",
                "pace_mode": "immediate_only",
                "away_delay_minutes": 210,
                "anti_errand_firmness": "gentle_firm",
                "thread_hold_min": 45,
                "spontaneous_pivot_enabled": False,
                "pivot_cooldown_min": 60,
            },
        )
        self.assertEqual(c_ok, 200)
        prefs = r_ok["data"]["preferences"]
        self.assertEqual(prefs["pace_mode"], "immediate_only")
        self.assertEqual(prefs["away_delay_minutes"], 210)
        self.assertEqual(prefs["anti_errand_firmness"], "gentle_firm")
        self.assertEqual(prefs["thread_hold_min"], 45)
        self.assertFalse(prefs["spontaneous_pivot_enabled"])
        self.assertEqual(prefs["pivot_cooldown_min"], 60)

        # Out-of-range away_delay_minutes -> 400
        c_bad, r_bad = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "update_preferences", "away_delay_minutes": 5},
        )
        self.assertEqual(c_bad, 400)
        self.assertEqual(r_bad["reason_code"], "AWAY_DELAY_MINUTES_INVALID")

        # Empty update_preferences -> 400
        c_emp, r_emp = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "update_preferences"},
        )
        self.assertEqual(c_emp, 400)
        self.assertEqual(r_emp["reason_code"], "PREFERENCES_UPDATE_EMPTY")

    def test_25_http_strict_query_extra_fields_empty_optional_and_identity_refusal(self) -> None:
        # Query string rejected with 400 UNKNOWN_QUERY on GET and POST
        c_qg, r_qg = self._call("GET", "/api/phone/autonomy/rhythm?foo=1")
        self.assertEqual(c_qg, 400)
        self.assertEqual(r_qg["reason_code"], "UNKNOWN_QUERY")

        c_qp, r_qp = self._call(
            "POST",
            "/api/phone/autonomy/rhythm?bar=2",
            {"action": "claim_ready_messages"},
            csrf=True,
        )
        self.assertEqual(c_qp, 400)
        self.assertEqual(r_qp["reason_code"], "UNKNOWN_QUERY")

        # Per-action extra field rejected with 400 UNKNOWN_FIELD:...
        c_ex, r_ex = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "claim_ready_messages", "msg_id": "pmsg-extra"},
        )
        self.assertEqual(c_ex, 400)
        self.assertTrue(r_ex["reason_code"].startswith("UNKNOWN_FIELD:"))

        # Empty-string optional fields rejected with 400 (never silently ignored)
        c_emp1, _ = self._her_post(
            "/api/phone/autonomy/rhythm",
            {"action": "declare_refusal", "topic_label": "代写周报", "private_note": "   "},
        )
        self.assertEqual(c_emp1, 400)

        c_emp2, _ = self._her_post(
            "/api/phone/autonomy/rhythm",
            {"action": "trigger_spontaneous_pivot", "custom_prompt": ""},
        )
        self.assertEqual(c_emp2, 400)

        # Client identity smuggling rejected with 409 CLIENT_IDENTITY_REFUSED:...
        c_id, r_id = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "claim_ready_messages", "subject": "fake_subject"},
        )
        self.assertEqual(c_id, 409)
        self.assertTrue(r_id["reason_code"].startswith("CLIENT_IDENTITY_REFUSED:"))

    def test_26_http_missing_csrf_and_bad_capability_fail_closed_403(self) -> None:
        c_csrf, r_csrf = self._call(
            "POST",
            "/api/phone/autonomy/rhythm",
            {"action": "claim_ready_messages"},
            csrf=False,
        )
        self.assertEqual(c_csrf, 403)
        self.assertEqual(r_csrf["reason_code"], "CSRF_TOKEN_MISSING")

        c_bget, _ = self._call(
            "GET", "/api/phone/autonomy/rhythm", capability="wrong-cap-token"
        )
        self.assertEqual(c_bget, 403)

        c_bpost, _ = self._call(
            "POST",
            "/api/phone/autonomy/rhythm",
            {"action": "claim_ready_messages"},
            csrf=True,
            capability="wrong-cap-token",
        )
        self.assertEqual(c_bpost, 403)

    def test_27_http_corrupt_state_degraded_on_get_and_409_on_post(self) -> None:
        fpath = arv.autonomy_rhythm_state_path(self.stack.root)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text("{corrupt-autonomy-state", encoding="utf-8")

        c_get, r_get = self._call("GET", "/api/phone/autonomy/rhythm")
        self.assertEqual(c_get, 200)
        self.assertEqual(r_get["data"]["state_status"], "degraded")
        self.assertEqual(r_get["data"]["state_reason_code"], "AUTONOMY_RHYTHM_STATE_CORRUPT")

        c_post, r_post = self._partner_post(
            "/api/phone/autonomy/rhythm",
            {"action": "claim_ready_messages"},
        )
        self.assertEqual(c_post, 409)
        self.assertEqual(r_post["reason_code"], "AUTONOMY_RHYTHM_STATE_CORRUPT")


if __name__ == "__main__":
    unittest.main()
