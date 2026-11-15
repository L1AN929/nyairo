#!/usr/bin/env python3
"""长程陪伴六项日常切片：纯投影状态机 + 真实 owner loopback HTTP 验证。

上半部分覆盖纯函数与原子文件边界：首日房间基线一次性不可变对比、室内盆栽手账连结与脱敏、
共同习惯册修订历史与跨作者拦截、礼物草稿到成品隐私与单向流转、回忆空间双层灯光（`then_only`
与 `with_later_context`）逐版本披露核对、一键续接总览、损坏/符号链接失败关闭拒绝覆盖与冻结锚点 SHA256。
下半部分（POSIX）通过 ``long_companionship_fixture.build_stack`` 启动真实 World / Supply /
economy / 阅读 / 相册 / 多版本作品、真实 ``derived`` 派生作品与本地投递分享，走真实 loopback HTTP 验证。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import long_companionship_view as view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "cap_longterm_test_token_v02_slice_b"
NOW = "2026-11-11T15:00:00+08:00"
LATER = "2026-11-12T19:30:00+08:00"

FROZEN_SHA256 = {
    "components/life/scripts/autonomous_life_assembly.py":
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}


class PureLongCompanionshipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="longterm-pure-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_first_room_capture_once_only_immutability_and_live_delta(self) -> None:
        state = view.empty_room_habits_state()
        day1_snap = {
            "location": "home",
            "furniture_count": 4,
            "item_count": 1,
            "item_names": ["粗陶茶杯"],
            "plant_names": ["窗台薄荷"],
            "reading_page": 2,
            "delivered_share_count": 1,
        }
        state, sealed, changed = view.apply_capture_first_room(
            state, snapshot=day1_snap, album_artifact_id="alb-day1",
            note="刚搬进来第一天的桌面", now_iso=NOW)
        self.assertTrue(changed)
        self.assertTrue(sealed["captured"])
        self.assertEqual(state["revision"], 1)

        # Second capture attempt MUST NOT overwrite Day-1 memory
        later_snap = {
            "location": "home",
            "furniture_count": 9,
            "item_count": 5,
            "item_names": ["粗陶茶杯", "羊毛毯"],
            "plant_names": ["窗台薄荷", "迷迭香"],
            "reading_page": 18,
            "delivered_share_count": 6,
        }
        replay_state, replay_item, replay_changed = view.apply_capture_first_room(
            state, snapshot=later_snap, album_artifact_id="alb-overwrite",
            note="试图覆盖第一天", now_iso=LATER)
        self.assertFalse(replay_changed)
        self.assertEqual(replay_state["revision"], 1)
        self.assertEqual(replay_item["album_artifact_id"], "alb-day1")
        self.assertEqual(replay_item["note"], "刚搬进来第一天的桌面")
        self.assertEqual(replay_item["furniture_count"], 4)

        subj_proj = view.first_room_habits_block(
            state=replay_state, state_reason=None, live_room=later_snap,
            home_plants_by_id={"plant-1": {"plant_id": "plant-1", "name": "窗台薄荷"}},
            album_readability={"alb-day1": True}, project_targets={}, subject_view=True)
        fr_subj = subj_proj["first_room"]
        self.assertEqual(fr_subj["furniture_delta"], 5)
        self.assertEqual(fr_subj["item_delta"], 4)
        self.assertEqual(fr_subj["new_item_names"], ["羊毛毯"])
        self.assertEqual(fr_subj["new_plant_names"], ["迷迭香"])
        self.assertEqual(fr_subj["reading_page_delta"], 16)
        self.assertEqual(fr_subj["delivered_share_delta"], 5)
        self.assertEqual(fr_subj["album_artifact_id"], "alb-day1")
        self.assertEqual(fr_subj["note"], "刚搬进来第一天的桌面")
        self.assertEqual(fr_subj["photo_status"], "available")
        self.assertTrue(fr_subj["no_absence_guilt"])

        partner_proj = view.first_room_habits_block(
            state=replay_state, state_reason=None, live_room=later_snap,
            home_plants_by_id={"plant-1": {"plant_id": "plant-1", "name": "窗台薄荷"}},
            album_readability={"alb-day1": True}, project_targets={}, subject_view=False)
        fr_partner = partner_proj["first_room"]
        self.assertNotIn("album_artifact_id", fr_partner)
        self.assertNotIn("note", fr_partner)
        self.assertEqual(fr_partner["photo_status"], "available")
        self.assertEqual(fr_partner["furniture_delta"], 5)
        self.assertTrue(fr_partner["no_absence_guilt"])

    def test_plant_sketch_links_cap_replay_and_partner_stripping(self) -> None:
        state = view.empty_room_habits_state()
        with self.assertRaises(view.LongtermStateError) as bad_plant:
            view.apply_link_plant_sketch(
                state, plant_id="ghost-plant", valid_plant_ids={"mint-1"},
                target_kind="project", target_artifact_id="proj-1",
                observation_note="新长了两片叶子", shared=True, now_iso=NOW)
        self.assertEqual(bad_plant.exception.reason_code, "PLANT_NOT_IN_HOME")

        # Link 1: shared album target (partner strips target_artifact_id)
        state, l_alb, c1 = view.apply_link_plant_sketch(
            state, plant_id="mint-1", plant_name="薄荷盆栽", valid_plant_ids={"mint-1"},
            target_kind="album", target_artifact_id="alb-secret-1",
            observation_note="薄荷叶尖带一点浅绿锯齿", shared=True, now_iso=NOW)
        self.assertTrue(c1)
        # Replay identical -> no-op
        state, _, c_replay = view.apply_link_plant_sketch(
            state, plant_id="mint-1", plant_name="薄荷盆栽", valid_plant_ids={"mint-1"},
            target_kind="album", target_artifact_id="alb-secret-1",
            observation_note="薄荷叶尖带一点浅绿锯齿", shared=True, now_iso=LATER)
        self.assertFalse(c_replay)

        # Link 2: shared undelivered project (partner strips target_artifact_id, status withheld)
        state, _, _ = view.apply_link_plant_sketch(
            state, plant_id="mint-1", plant_name="薄荷盆栽", valid_plant_ids={"mint-1"},
            target_kind="project", target_artifact_id="proj-undelivered",
            observation_note="铅笔起稿还没寄出", shared=True, now_iso=NOW)
        # Link 3: shared delivered project (partner sees target_artifact_id, status available)
        state, _, _ = view.apply_link_plant_sketch(
            state, plant_id="mint-1", plant_name="薄荷盆栽", valid_plant_ids={"mint-1"},
            target_kind="project", target_artifact_id="proj-delivered",
            observation_note="水彩上色完成的叶片速写", shared=True, now_iso=NOW)
        # Link 4: private link (partner omits completely)
        state, l_priv, _ = view.apply_link_plant_sketch(
            state, plant_id="mint-1", plant_name="薄荷盆栽", valid_plant_ids={"mint-1"},
            target_kind="project", target_artifact_id="proj-private-only",
            observation_note="私密手账小记", shared=False, now_iso=NOW)

        partner_proj = view.first_room_habits_block(
            state=state, state_reason=None, live_room={},
            home_plants_by_id={"mint-1": {"plant_id": "mint-1", "name": "薄荷盆栽"}},
            album_readability={"alb-secret-1": True},
            project_targets={
                "proj-undelivered": {"active": True, "delivered": False},
                "proj-delivered": {"active": True, "delivered": True},
            },
            subject_view=False)
        items = partner_proj["plant_sketch_links"]["items"]
        self.assertEqual(len(items), 3)
        raw_partner = json.dumps(items, ensure_ascii=False)
        self.assertNotIn("alb-secret-1", raw_partner)
        self.assertNotIn("proj-undelivered", raw_partner)
        self.assertNotIn("proj-private-only", raw_partner)
        self.assertIn("proj-delivered", raw_partner)
        self.assertEqual(items[0]["target_status"], "available")
        self.assertEqual(items[1]["target_status"], "withheld")
        self.assertEqual(items[2]["target_status"], "available")

        # Fill up to MAX_PLANT_SKETCH_LINKS (8)
        for idx in range(4):
            state, _, _ = view.apply_link_plant_sketch(
                state, plant_id="mint-1", target_kind="project",
                target_artifact_id=f"proj-extra-{idx}",
                observation_note=f"速写页-{idx}", shared=False, now_iso=NOW)
        with self.assertRaises(view.LongtermStateError) as full_err:
            view.apply_link_plant_sketch(
                state, plant_id="mint-1", target_kind="project",
                target_artifact_id="proj-overflow",
                observation_note="第九条超限", shared=False, now_iso=NOW)
        self.assertEqual(full_err.exception.reason_code, "PLANT_SKETCH_LINKS_FULL")

        state, removed = view.apply_remove_plant_sketch(
            state, link_id=l_priv["link_id"], now_iso=LATER)
        self.assertTrue(removed)
        _, removed_again = view.apply_remove_plant_sketch(
            state, link_id=l_priv["link_id"], now_iso=LATER)
        self.assertFalse(removed_again)

    def test_shared_habits_revision_history_bound_retire_and_author_mismatch(self) -> None:
        state = view.empty_room_habits_state()
        state, habit, changed = view.apply_upsert_habit(
            state, title="睡前留一盏小灯", habit_text="十一点后把吸顶灯关掉只留桌角暖灯",
            author_side="her", now_iso=NOW)
        self.assertTrue(changed)
        hid = habit["habit_id"]

        with self.assertRaises(view.LongtermStateError) as edit_mismatch:
            view.apply_upsert_habit(
                state, habit_id=hid, title="篡改", habit_text="越权改她的习惯",
                author_side="partner", now_iso=LATER)
        self.assertEqual(edit_mismatch.exception.reason_code, "AUTHOR_MISMATCH")

        with self.assertRaises(view.LongtermStateError) as retire_mismatch:
            view.apply_retire_habit(
                state, habit_id=hid, retired_reason="越权停用",
                author_side="partner", now_iso=LATER)
        self.assertEqual(retire_mismatch.exception.reason_code, "AUTHOR_MISMATCH")

        # 6 revisions -> revision_count=6, bounded last 5 revisions kept
        for rev in range(1, 7):
            state, updated, ch = view.apply_upsert_habit(
                state, habit_id=hid, title="睡前留一盏小灯",
                habit_text=f"第{rev}次调整：按当天困意灵活留灯",
                revised_reason=f"调整原因-{rev}", author_side="her", now_iso=LATER)
            self.assertTrue(ch)
        self.assertEqual(updated["revision_count"], 6)
        self.assertEqual(len(updated["revisions"]), 5)
        self.assertEqual([r["version"] for r in updated["revisions"]], [2, 3, 4, 5, 6])
        self.assertEqual(updated["revisions"][-1]["revised_reason"], "调整原因-6")

        # Retire preserves revision history and sets flags
        state, retired, r_changed = view.apply_retire_habit(
            state, habit_id=hid, retired_reason="换了新台灯，自然养成不用特意记了",
            author_side="her", now_iso=LATER)
        self.assertTrue(r_changed)
        self.assertEqual(retired["status"], "RETIRED")
        self.assertEqual(len(retired["revisions"]), 5)

        proj = view.first_room_habits_block(
            state=state, state_reason=None, live_room={}, home_plants_by_id={},
            album_readability={}, project_targets={}, subject_view=False)
        self.assertTrue(proj["shared_habits"]["no_hard_constraint"])
        self.assertTrue(proj["shared_habits"]["does_not_hijack_decision"])
        self.assertEqual(proj["shared_habits"]["retired_count"], 1)

    def test_shared_habits_active_12_and_stored_24_caps(self) -> None:
        state = view.empty_room_habits_state()
        ids: list[str] = []
        for idx in range(view.MAX_ACTIVE_HABITS):
            state, item, _ = view.apply_upsert_habit(
                state, title=f"习惯-{idx}", habit_text=f"内容-{idx}",
                author_side="partner", now_iso=NOW)
            ids.append(item["habit_id"])
        with self.assertRaises(view.LongtermStateError) as active_full:
            view.apply_upsert_habit(
                state, title="第13条活跃", habit_text="超活跃上限",
                author_side="partner", now_iso=NOW)
        self.assertEqual(active_full.exception.reason_code, "ACTIVE_HABITS_FULL")

        # Retire all 12, add 12 more active -> reaches stored 24 cap
        for hid in ids:
            state, _, _ = view.apply_retire_habit(
                state, habit_id=hid, author_side="partner", now_iso=NOW)
        for idx in range(12):
            state, item, _ = view.apply_upsert_habit(
                state, title=f"二期习惯-{idx}", habit_text=f"二期内容-{idx}",
                author_side="her", now_iso=LATER)
            if idx == 0:
                first_second_batch = item["habit_id"]
        # Retire one so active < 12, but stored == 24 -> STORED_HABITS_FULL
        state, _, _ = view.apply_retire_habit(
            state, habit_id=first_second_batch, author_side="her", now_iso=LATER)
        with self.assertRaises(view.LongtermStateError) as stored_full:
            view.apply_upsert_habit(
                state, title="第25条总存", habit_text="超总存上限",
                author_side="her", now_iso=LATER)
        self.assertEqual(stored_full.exception.reason_code, "STORED_HABITS_FULL")

    def test_shared_gifts_her_draft_wrapped_invisibility_and_gifted_journey(self) -> None:
        state = view.empty_gifts_lantern_state()
        state, her_gift, _ = view.apply_upsert_gift(
            state, author_side="her", stage="draft", title="手绘节气书签",
            draft_seed_note="最开始只是在草稿纸边角画了一片银杏叶",
            artifact_id="proj-1", version_id="ver-1", now_iso=NOW)
        state, partner_gift, _ = view.apply_upsert_gift(
            state, author_side="partner", stage="draft", title="热可可配料罐",
            draft_seed_note="想找不那么甜的可可粉和肉桂棒", now_iso=NOW)

        # Wrap her gift (still unsent -> MUST remain completely invisible to partner!)
        state, her_gift, _ = view.apply_upsert_gift(
            state, gift_id=her_gift["gift_id"], author_side="her", stage="wrapped",
            title="手绘节气书签",
            draft_seed_note="最开始只是在草稿纸边角画了一片银杏叶",
            finished_note="压了干花封膜，夹在常用的尺子旁边",
            finished_note_supplied=True, now_iso=NOW)

        partner_before = view.gifts_lantern_block(
            state=state, state_reason=None, layer="then_only",
            version_inspections={
                ("proj-1", "ver-1"): {
                    "readable": True, "lifecycle": "ACTIVE", "delivered": True,
                    "title": "银杏叶草图", "delivered_excerpt": "一片金黄银杏叶的淡彩",
                },
            },
            derived_lineage=[], subject_view=False)
        visible_before = partner_before["shared_gifts"]["gifts"]
        self.assertEqual(len(visible_before), 1)
        self.assertEqual(visible_before[0]["gift_id"], partner_gift["gift_id"])
        raw_before = json.dumps(partner_before["shared_gifts"], ensure_ascii=False)
        self.assertNotIn("手绘节气书签", raw_before)
        self.assertNotIn("银杏叶", raw_before)

        # Cross-author send rejected
        with self.assertRaises(view.LongtermStateError) as cross_send:
            view.apply_send_gift(
                state, gift_id=her_gift["gift_id"], author_side="partner", now_iso=LATER)
        self.assertEqual(cross_send.exception.reason_code, "AUTHOR_MISMATCH")

        # Her sends gift -> both draft_seed_note and finished_note + gifted_at become visible!
        state, sent, _ = view.apply_send_gift(
            state, gift_id=her_gift["gift_id"], author_side="her",
            finished_note="冬至快乐，愿翻书时总有暖意", now_iso=LATER)
        self.assertEqual(sent["stage"], "gifted")
        self.assertEqual(sent["gifted_at"], LATER)

        partner_after = view.gifts_lantern_block(
            state=state, state_reason=None, layer="then_only",
            version_inspections={
                ("proj-1", "ver-1"): {
                    "readable": True, "lifecycle": "ACTIVE", "delivered": True,
                    "title": "银杏叶草图", "delivered_excerpt": "一片金黄银杏叶的淡彩",
                },
            },
            derived_lineage=[], subject_view=False)
        her_visible = [
            g for g in partner_after["shared_gifts"]["gifts"]
            if g["gift_id"] == her_gift["gift_id"]
        ][0]
        self.assertEqual(her_visible["draft_seed_note"], "最开始只是在草稿纸边角画了一片银杏叶")
        self.assertEqual(her_visible["finished_note"], "冬至快乐，愿翻书时总有暖意")
        self.assertEqual(her_visible["gifted_at"], LATER)
        self.assertEqual(her_visible["linked_work_status"], "available")
        self.assertEqual(her_visible["linked_work_excerpt"], "一片金黄银杏叶的淡彩")

        # Cannot regress stage or re-send once gifted
        with self.assertRaises(view.LongtermStateError) as regress_err:
            view.apply_upsert_gift(
                state, gift_id=her_gift["gift_id"], author_side="her", stage="wrapped",
                title="回退", draft_seed_note="回退", now_iso=LATER)
        self.assertEqual(regress_err.exception.reason_code, "GIFT_ALREADY_SENT")
        with self.assertRaises(view.LongtermStateError) as resend_err:
            view.apply_send_gift(
                state, gift_id=her_gift["gift_id"], author_side="her", now_iso=LATER)
        self.assertEqual(resend_err.exception.reason_code, "GIFT_ALREADY_SENT")

    def test_shared_gifts_cap_12_and_linked_work_withholding(self) -> None:
        state = view.empty_gifts_lantern_state()
        with self.assertRaises(view.LongtermStateError) as probe_err:
            view.apply_upsert_gift(
                state, author_side="partner", stage="draft", title="试探链接",
                draft_seed_note="伴侣不可挂载她的私有作品ID",
                artifact_id="proj-secret", version_id="v1", now_iso=NOW)
        self.assertEqual(probe_err.exception.reason_code, "PARTNER_CANNOT_LINK_OWNER_WORK")

        state, g1, _ = view.apply_upsert_gift(
            state, author_side="her", stage="wrapped", title="未投递画稿礼物",
            draft_seed_note="初稿灵感", artifact_id="proj-secret", version_id="v3", now_iso=NOW)
        state, _, _ = view.apply_send_gift(
            state, gift_id=g1["gift_id"], author_side="her",
            finished_note="先把礼物送出", now_iso=LATER)

        partner_proj = view.gifts_lantern_block(
            state=state, state_reason=None, layer="then_only",
            version_inspections={
                ("proj-secret", "v3"): {
                    "readable": True, "lifecycle": "ACTIVE", "delivered": False,
                    "title": "私密未投递标题", "owner_excerpt": "私密未投递正文",
                },
            },
            derived_lineage=[], subject_view=False)
        g_row = partner_proj["shared_gifts"]["gifts"][0]
        self.assertEqual(g_row["linked_work_status"], "withheld")
        self.assertEqual(g_row["linked_work_reason"], view.VERSION_NOT_DELIVERED)
        self.assertIsNone(g_row["linked_work_excerpt"])
        self.assertNotIn("artifact_id", g_row)

        for idx in range(11):
            state, _, _ = view.apply_upsert_gift(
                state, author_side="partner", stage="draft",
                title=f"礼物-{idx}", draft_seed_note="草稿", now_iso=NOW)
        with self.assertRaises(view.LongtermStateError) as full_err:
            view.apply_upsert_gift(
                state, author_side="partner", stage="draft",
                title="第13件超限礼物", draft_seed_note="超限", now_iso=NOW)
        self.assertEqual(full_err.exception.reason_code, "SHARED_GIFTS_FULL")

    def test_memory_lantern_dual_layer_lighting_and_exact_version_disclosure(self) -> None:
        state = view.empty_gifts_lantern_state()
        state, _, _ = view.apply_record_later_reflection(
            state, source_artifact_id="orig-1", source_version_id="v1",
            reflection_note="现在回头看，那时候的线条虽然生涩却很真诚",
            shared=True, now_iso=LATER)

        lineage = [
            {
                "derived_artifact_id": "remake-pub",
                "derived_version_id": "rv1",
                "source_artifact_id": "orig-1",
                "source_version_id": "v1",
                "purpose": "adapt",
                "taken_at": LATER,
                "provenance_note": "一年后重画",
            },
            {
                "derived_artifact_id": "remake-priv",
                "derived_version_id": "rv2",
                "source_artifact_id": "orig-1",
                "source_version_id": "v1",
                "purpose": "quote",
                "taken_at": LATER,
                "provenance_note": "私密练习",
            },
        ]
        inspections = {
            ("orig-1", "v1"): {
                "readable": True, "lifecycle": "ACTIVE", "delivered": True,
                "title": "海堤初稿", "owner_excerpt": "初稿原文",
                "delivered_excerpt": "初稿已分享摘要", "share_id": "sh-1",
            },
            ("remake-pub", "rv1"): {
                "readable": True, "lifecycle": "ACTIVE", "delivered": True,
                "title": "一年后重画", "owner_excerpt": "重画原文",
                "delivered_excerpt": "重画已分享摘要", "share_id": "sh-2",
            },
            ("remake-priv", "rv2"): {
                "readable": True, "lifecycle": "ACTIVE", "delivered": False,
                "title": "不该泄露的私密重画标题", "owner_excerpt": "私密重画正文",
            },
        }

        # Layer 1: then_only -> past never knows the future!
        then_proj = view.gifts_lantern_block(
            state=state, state_reason=None, layer="then_only",
            version_inspections=inspections, derived_lineage=lineage, subject_view=True)
        ml_then = then_proj["memory_lantern"]
        self.assertFalse(ml_then["later_context_included"])
        self.assertFalse(ml_then["past_knows_future"])
        self.assertEqual(len(ml_then["entries"]), 1)
        self.assertEqual(ml_then["entries"][0]["then_snapshot"]["status"], "available")
        self.assertEqual(ml_then["entries"][0]["later_remakes"], [])
        self.assertIsNone(ml_then["entries"][0]["later_reflection"])

        # Layer 2: with_later_context in partner_default -> checks exact-version delivery!
        later_partner = view.gifts_lantern_block(
            state=state, state_reason=None, layer="with_later_context",
            version_inspections=inspections, derived_lineage=lineage, subject_view=False)
        ml_later = later_partner["memory_lantern"]
        self.assertTrue(ml_later["later_context_included"])
        entry = ml_later["entries"][0]
        self.assertEqual(entry["then_snapshot"]["excerpt"], "初稿已分享摘要")
        self.assertEqual(len(entry["later_remakes"]), 2)
        self.assertEqual(entry["later_remakes"][0]["status"], "available")
        self.assertEqual(entry["later_remakes"][0]["excerpt"], "重画已分享摘要")
        self.assertEqual(entry["later_remakes"][1]["status"], "withheld")
        self.assertEqual(entry["later_remakes"][1]["reason_code"], view.VERSION_NOT_DELIVERED)
        self.assertNotIn("不该泄露的私密重画标题", json.dumps(later_partner, ensure_ascii=False))
        self.assertEqual(
            entry["later_reflection"]["reflection_note"],
            "现在回头看，那时候的线条虽然生涩却很真诚",
        )

    def test_memory_lantern_archived_source_withheld_in_both_views(self) -> None:
        state = view.empty_gifts_lantern_state()
        lineage = [{
            "derived_artifact_id": "remake-1",
            "derived_version_id": "rv1",
            "source_artifact_id": "arch-1",
            "source_version_id": "v1",
            "purpose": "reuse",
            "taken_at": LATER,
        }]
        inspections = {
            ("arch-1", "v1"): {
                "readable": False, "lifecycle": "ARCHIVED",
                "reason_code": view.ARCHIVED_SOURCE,
            },
            ("remake-1", "rv1"): {
                "readable": False, "lifecycle": "ARCHIVED",
                "reason_code": view.ARCHIVED_SOURCE,
            },
        }
        for subj in (True, False):
            proj = view.gifts_lantern_block(
                state=state, state_reason=None, layer="with_later_context",
                version_inspections=inspections, derived_lineage=lineage, subject_view=subj)
            e0 = proj["memory_lantern"]["entries"][0]
            self.assertEqual(e0["then_snapshot"]["status"], "withheld")
            self.assertEqual(e0["then_snapshot"]["reason_code"], view.ARCHIVED_SOURCE)
            self.assertEqual(e0["later_remakes"][0]["reason_code"], view.ARCHIVED_SOURCE)

    def test_later_reflections_cap_12_and_upsert_idempotency(self) -> None:
        state = view.empty_gifts_lantern_state()
        state, r0, changed = view.apply_record_later_reflection(
            state, source_artifact_id="art-0", source_version_id="v1",
            reflection_note="第一条回望", shared=False, now_iso=NOW)
        self.assertTrue(changed)
        state, _, replay = view.apply_record_later_reflection(
            state, source_artifact_id="art-0", source_version_id="v1",
            reflection_note="第一条回望", shared=False, now_iso=LATER)
        self.assertFalse(replay)
        state, r0_upd, upd = view.apply_record_later_reflection(
            state, source_artifact_id="art-0", source_version_id="v1",
            reflection_note="改写后的第一条回望", shared=True, now_iso=LATER)
        self.assertTrue(upd)
        self.assertEqual(r0_upd["reflection_id"], r0["reflection_id"])

        for idx in range(1, view.MAX_LATER_REFLECTIONS):
            state, _, _ = view.apply_record_later_reflection(
                state, source_artifact_id=f"art-{idx}", source_version_id="v1",
                reflection_note=f"回望-{idx}", shared=False, now_iso=NOW)
        with self.assertRaises(view.LongtermStateError) as full_err:
            view.apply_record_later_reflection(
                state, source_artifact_id="art-overflow", source_version_id="v1",
                reflection_note="第十三条超限", shared=False, now_iso=NOW)
        self.assertEqual(full_err.exception.reason_code, "LATER_REFLECTIONS_FULL")

    def test_resume_hub_projection_disclosure_and_no_nagging(self) -> None:
        active = [{"artifact_id": "act-priv", "title": "未公开小说草稿",
                   "head_version_id": "v4", "updated_at": LATER}]
        archived = [{"artifact_id": "arch-priv", "title": "去年已完结旧手账",
                     "updated_at": NOW}]
        delivered_proj = {
            "share_id": "sh-pub-1", "title": "已分享的水彩小卡",
            "artifact_id": "act-pub", "version_id": "v1", "delivered_at": NOW,
        }
        reading = {
            "has_book": True, "current_page": 5, "total_pages": 12,
            "updated_at": NOW, "title": "私人读书章节首行",
        }
        topic = {"share_id": "sh-pub-1", "title": "已分享的水彩小卡", "updated_at": NOW}
        recovery = {
            "status": "OK", "recovery_class": "CLEAN_RECOVERABLE",
            "backup_restore_status": "PASS",
            "forget_barrier_status": "ENFORCED_BY_BACKUP_GATE",
        }

        partner_hub = view.resume_hub_block(
            active_projects=active, archived_projects=archived,
            latest_delivered_project_share=delivered_proj,
            reading_bookmark=reading, latest_topic_share=topic,
            recovery_summary=recovery, subject_view=False)
        raw_partner = json.dumps(partner_hub, ensure_ascii=False)
        self.assertNotIn("未公开小说草稿", raw_partner)
        self.assertNotIn("去年已完结旧手账", raw_partner)
        self.assertNotIn("arch-priv", raw_partner)
        self.assertNotIn("私人读书章节首行", raw_partner)
        self.assertTrue(partner_hub["project_cue"]["has_active_project"])
        self.assertEqual(partner_hub["project_cue"]["share_id"], "sh-pub-1")
        self.assertEqual(partner_hub["closed_projects"]["archived_count"], 1)
        self.assertTrue(partner_hub["closed_projects"]["closed_cleanly"])
        self.assertTrue(partner_hub["closed_projects"]["no_nagging"])
        self.assertTrue(partner_hub["recovery_overview"]["read_only"])

        subj_hub = view.resume_hub_block(
            active_projects=active, archived_projects=archived,
            latest_delivered_project_share=delivered_proj,
            reading_bookmark=reading, latest_topic_share=topic,
            recovery_summary=recovery, subject_view=True)
        self.assertEqual(subj_hub["project_cue"]["title"], "未公开小说草稿")
        self.assertEqual(subj_hub["reading_cue"]["title"], "私人读书章节首行")
        self.assertEqual(subj_hub["closed_projects"]["items"][0]["title"], "去年已完结旧手账")

    def test_atomic_0600_permissions_and_get_never_creates_phone_dir(self) -> None:
        phone_dir = self.root / "phone"
        self.assertFalse(phone_dir.exists())
        s1, r1 = view.read_room_habits_state(self.root)
        s2, r2 = view.read_gifts_lantern_state(self.root)
        self.assertIsNone(r1)
        self.assertIsNone(r2)
        self.assertFalse(phone_dir.exists())

        s1, _, _ = view.apply_upsert_habit(
            s1, title="早起开窗", habit_text="透气五分钟", author_side="partner", now_iso=NOW)
        view.write_room_habits_state(self.root, s1)
        s2, _, _ = view.apply_upsert_gift(
            s2, author_side="partner", stage="draft", title="手写卡片",
            draft_seed_note="买厚棉纸", now_iso=NOW)
        view.write_gifts_lantern_state(self.root, s2)

        if os.name == "posix":
            for path in (view.room_habits_state_path(self.root),
                         view.gifts_lantern_state_path(self.root)):
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o600)

    def test_corrupt_and_invalid_json_fail_closed_and_refuse_overwrite(self) -> None:
        p_room = view.room_habits_state_path(self.root)
        p_room.parent.mkdir(parents=True, exist_ok=True)
        p_room.write_text("{corrupt-json", encoding="utf-8")

        fallback, reason = view.read_room_habits_state(self.root)
        self.assertEqual(reason, "ROOM_HABITS_STATE_CORRUPT")
        self.assertEqual(fallback, view.empty_room_habits_state())
        with self.assertRaises(view.LongtermStateError) as refusal:
            view.write_room_habits_state(self.root, view.empty_room_habits_state())
        self.assertEqual(refusal.exception.reason_code, "ROOM_HABITS_STATE_CORRUPT")
        self.assertEqual(p_room.read_text(encoding="utf-8"), "{corrupt-json")

        p_gifts = view.gifts_lantern_state_path(self.root)
        p_gifts.write_text('{"schema_version": 999}', encoding="utf-8")
        _, g_reason = view.read_gifts_lantern_state(self.root)
        self.assertEqual(g_reason, "GIFTS_LANTERN_STATE_INVALID")
        with self.assertRaises(view.LongtermStateError) as g_refusal:
            view.write_gifts_lantern_state(self.root, view.empty_gifts_lantern_state())
        self.assertEqual(g_refusal.exception.reason_code, "GIFTS_LANTERN_STATE_INVALID")

    def test_symlink_file_and_parent_dir_fail_closed_and_refuse_overwrite(self) -> None:
        real_file = self.root / "outside.json"
        real_file.write_text("{}", encoding="utf-8")
        link_file = view.room_habits_state_path(self.root)
        link_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(real_file, link_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not permitted in this environment")

        _, reason = view.read_room_habits_state(self.root)
        self.assertEqual(reason, "ROOM_HABITS_STATE_SYMLINK")
        with self.assertRaises(view.LongtermStateError) as sym_write:
            view.write_room_habits_state(self.root, view.empty_room_habits_state())
        self.assertEqual(sym_write.exception.reason_code, "ROOM_HABITS_STATE_SYMLINK")

        # Symlinked <root>/phone directory also fails closed
        root2 = self.root / "symdir_case"
        root2.mkdir()
        outside_dir = self.root / "outside_dir"
        outside_dir.mkdir()
        os.symlink(outside_dir, root2 / "phone")
        _, dir_reason = view.read_gifts_lantern_state(root2)
        self.assertEqual(dir_reason, "GIFTS_LANTERN_STATE_SYMLINK")
        with self.assertRaises(view.LongtermStateError):
            view.write_gifts_lantern_state(root2, view.empty_gifts_lantern_state())

    def test_invalid_lantern_layer_rejected_in_pure_projection(self) -> None:
        with self.assertRaises(view.LongtermStateError) as err:
            view.gifts_lantern_block(
                state=view.empty_gifts_lantern_state(), state_reason=None,
                layer="future_spoiler", version_inspections={},
                derived_lineage=[], subject_view=False)
        self.assertEqual(err.exception.reason_code, "LANTERN_LAYER_INVALID")


class FrozenAnchorsTest(unittest.TestCase):
    def test_frozen_anchor_files_keep_exact_sha256(self) -> None:
        for relative, expected in FROZEN_SHA256.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 World / Supply / economy / Life socket 需要 POSIX（WSL/Linux）")


@POSIX_ONLY
class LongCompanionshipOwnerHTTPTest(unittest.TestCase):
    """Real owners + real loopback HTTP across all 3 `/api/phone/longterm/*` surfaces."""

    @classmethod
    def setUpClass(cls) -> None:
        import long_companionship_fixture as fixture

        cls.fixture = fixture
        cls.root = Path(tempfile.mkdtemp(prefix="longterm-http-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN, subject=SUBJECT, endpoint="http://127.0.0.1:1",
            issued_at="2026-11-11T12:00:00+08:00")
        cls.stack = fixture.build_stack(cls.root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        for path in (
            view.room_habits_state_path(self.stack.root),
            view.gifts_lantern_state_path(self.stack.root),
        ):
            if path.is_symlink() or path.exists():
                path.unlink()

    def _call(self, method: str, path: str, body: dict | None = None, *,
              csrf: bool = False, capability: str | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_http_gets_never_create_state_files_and_enforce_query_and_capability_gates(self) -> None:
        for ep in (
            "/api/phone/longterm/first-room-habits",
            "/api/phone/longterm/gifts-lantern",
            "/api/phone/longterm/resume-hub",
        ):
            st, partner_body = self._call("GET", ep)
            self.assertEqual(st, 200, partner_body)
            self.assertEqual(partner_body["data"]["view"], "partner_default")

            st_sub, subj_body = self._call("GET", ep, capability=CAP_TOKEN)
            self.assertEqual(st_sub, 200, subj_body)
            self.assertEqual(subj_body["data"]["view"], "subject")

            st_bad, _ = self._call("GET", ep, capability="wrong-capability-token")
            self.assertEqual(st_bad, 403)

        self.assertFalse(view.room_habits_state_path(self.stack.root).exists())
        self.assertFalse(view.gifts_lantern_state_path(self.stack.root).exists())

        for bad_url in (
            "/api/phone/longterm/first-room-habits?extra=1",
            "/api/phone/longterm/resume-hub?extra=1",
            "/api/phone/longterm/gifts-lantern?foo=bar",
            "/api/phone/longterm/gifts-lantern?layer=invalid",
            "/api/phone/longterm/gifts-lantern?layer=",
            "/api/phone/longterm/gifts-lantern?layer=then_only&layer=with_later_context",
        ):
            st_q, err_q = self._call("GET", bad_url)
            self.assertEqual(st_q, 400, (bad_url, err_q))

        # Resume-hub has no POST
        st_post_hub, _ = self._call("POST", "/api/phone/longterm/resume-hub",
                                    {"action": "noop"}, csrf=True)
        self.assertEqual(st_post_hub, 404)

    def test_http_post_csrf_capability_and_strict_unknown_field_rejection(self) -> None:
        # Missing CSRF -> 403
        st_no_csrf, _ = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "upsert_habit", "title": "喝水", "habit_text": "早起一杯温水",
             "author_side": "partner"},
            csrf=False)
        self.assertEqual(st_no_csrf, 403)

        # Bad optional capability on partner action -> 403 (never silently downgraded)
        st_bad_cap, _ = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "upsert_habit", "title": "喝水", "habit_text": "早起一杯温水",
             "author_side": "partner"},
            csrf=True, capability="forged-cap")
        self.assertEqual(st_bad_cap, 403)

        # Her-only actions require capability -> 403 without capability
        for endpoint, payload in (
            ("/api/phone/longterm/first-room-habits", {"action": "capture_first_room"}),
            ("/api/phone/longterm/first-room-habits", {
                "action": "link_plant_sketch", "plant_id": "p1",
                "target_kind": "project", "target_artifact_id": "a1",
                "observation_note": "观察"}),
            ("/api/phone/longterm/first-room-habits", {
                "action": "remove_plant_sketch", "link_id": "l1"}),
            ("/api/phone/longterm/first-room-habits", {
                "action": "upsert_habit", "title": "她的习惯",
                "habit_text": "内容", "author_side": "her"}),
            ("/api/phone/longterm/gifts-lantern", {
                "action": "upsert_gift", "author_side": "her", "stage": "draft",
                "title": "她的礼物", "draft_seed_note": "初稿"}),
            ("/api/phone/longterm/gifts-lantern", {
                "action": "record_later_reflection",
                "source_artifact_id": self.stack.revisit["artifact_id"],
                "source_version_id": self.stack.revisit["v1"],
                "reflection_note": "回望"}),
        ):
            st_her, _ = self._call("POST", endpoint, payload, csrf=True)
            self.assertEqual(st_her, 403, payload)

        # Extra field (even empty string "") -> 400 UNKNOWN_FIELD:<name>
        st_extra1, body_extra1 = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "capture_first_room", "habit_text": ""},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_extra1, 400, body_extra1)
        self.assertTrue(str(body_extra1["reason_code"]).startswith("UNKNOWN_FIELD:"))

        st_extra2, body_extra2 = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "send_gift", "gift_id": "gift-1", "author_side": "partner",
             "draft_seed_note": ""},
            csrf=True)
        self.assertEqual(st_extra2, 400, body_extra2)
        self.assertEqual(body_extra2["reason_code"], "UNKNOWN_FIELD:draft_seed_note")

    def test_http_first_room_capture_once_only_immutability_and_live_comparison(self) -> None:
        # Bogus album_artifact_id rejected before sealing
        st_bad_alb, bad_alb = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "capture_first_room", "album_artifact_id": "nonexistent_album_999"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_bad_alb, 409, bad_alb)
        self.assertEqual(bad_alb["reason_code"], "FIRST_ROOM_ALBUM_NOT_FOUND")

        # Seal Day-1 room baseline
        st_cap, cap_res = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "capture_first_room",
             "album_artifact_id": self.stack.album_artifact_id,
             "note": "搬进海边房间第一天的阳光"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_cap, 200, cap_res)
        self.assertEqual(cap_res["status"], "applied")
        self.assertTrue(cap_res["data"]["changed"])
        self.assertFalse(cap_res["data"]["already_applied"])

        # Deliver one new local share so live delivered_share_count increases by +1
        new_aid, _ = self.fixture.pocket_fixture._create_work(
            self.stack, title="搬家后新写的小随笔", text="窗外能听见海鸥声。",
            key="longterm-test-delta-proj")
        self.fixture.pocket_fixture._send_local(
            self.stack, source_id=new_aid, text="今天在窗边听见海鸥了。",
            key="longterm-test-delta-send")

        # Attempting a second capture_first_room MUST return already_applied without overwriting!
        st_cap2, cap_res2 = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "capture_first_room", "note": "不该覆盖第一天的备注"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_cap2, 200, cap_res2)
        self.assertEqual(cap_res2["status"], "already_applied")
        self.assertTrue(cap_res2["data"]["already_applied"])
        self.assertFalse(cap_res2["data"]["changed"])

        _, partner_get = self._call("GET", "/api/phone/longterm/first-room-habits")
        fr_p = partner_get["data"]["first_room"]
        self.assertTrue(fr_p["captured"])
        self.assertEqual(fr_p["delivered_share_delta"], 1)
        self.assertEqual(fr_p["photo_status"], "available")
        self.assertNotIn("album_artifact_id", fr_p)
        self.assertNotIn("note", fr_p)
        self.assertTrue(fr_p["no_absence_guilt"])

        _, subj_get = self._call("GET", "/api/phone/longterm/first-room-habits",
                                 capability=CAP_TOKEN)
        fr_s = subj_get["data"]["first_room"]
        self.assertEqual(fr_s["album_artifact_id"], self.stack.album_artifact_id)
        self.assertEqual(fr_s["note"], "搬进海边房间第一天的阳光")

    def test_http_plant_sketch_world_and_owner_verification_and_partner_stripping(self) -> None:
        self.assertGreaterEqual(len(self.stack.home_plant_ids), 1,
                                "coastal_town home interior must expose at least one plant")
        real_plant_id = self.stack.home_plant_ids[0]

        # Non-existent plant rejected 409 PLANT_NOT_IN_HOME
        st_p, err_p = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "link_plant_sketch", "plant_id": "not_a_real_home_plant",
             "target_kind": "album", "target_artifact_id": self.stack.album_artifact_id,
             "observation_note": "新叶观察"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_p, 409, err_p)
        self.assertEqual(err_p["reason_code"], "PLANT_NOT_IN_HOME")

        # Non-existent & archived sketch targets rejected 409 SKETCH_TARGET_NOT_FOUND
        for bad_kind, bad_target in (
            ("album", "missing_album_id"),
            ("project", "missing_project_id"),
            ("project", self.stack.archived["artifact_id"]),
        ):
            st_t, err_t = self._call(
                "POST", "/api/phone/longterm/first-room-habits",
                {"action": "link_plant_sketch", "plant_id": real_plant_id,
                 "target_kind": bad_kind, "target_artifact_id": bad_target,
                 "observation_note": "新叶观察"},
                csrf=True, capability=CAP_TOKEN)
            self.assertEqual(st_t, 409, err_t)
            self.assertEqual(err_t["reason_code"], "SKETCH_TARGET_NOT_FOUND")

        # Link 1: shared album photo; Link 2: shared undelivered project; Link 3: shared delivered project
        undelivered_proj = self.stack.derived_lineage["private_remake"]["artifact_id"]
        delivered_proj = self.stack.revisit["artifact_id"]

        _, r_alb = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "link_plant_sketch", "plant_id": real_plant_id,
             "target_kind": "album", "target_artifact_id": self.stack.album_artifact_id,
             "observation_note": "晨光下的叶片水珠", "shared": True},
            csrf=True, capability=CAP_TOKEN)
        _, _ = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "link_plant_sketch", "plant_id": real_plant_id,
             "target_kind": "project", "target_artifact_id": undelivered_proj,
             "observation_note": "还没寄出的叶脉线稿", "shared": True},
            csrf=True, capability=CAP_TOKEN)
        _, _ = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "link_plant_sketch", "plant_id": real_plant_id,
             "target_kind": "project", "target_artifact_id": delivered_proj,
             "observation_note": "已经分享过的窗边速写", "shared": True},
            csrf=True, capability=CAP_TOKEN)

        _, partner_get = self._call("GET", "/api/phone/longterm/first-room-habits")
        links = partner_get["data"]["plant_sketch_links"]["items"]
        self.assertEqual(len(links), 3)
        raw = json.dumps(links, ensure_ascii=False)
        self.assertNotIn(self.stack.album_artifact_id, raw)
        self.assertNotIn(undelivered_proj, raw)
        self.assertIn(delivered_proj, raw)

        # Remove first link cleanly
        st_rm, _ = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "remove_plant_sketch", "link_id": r_alb["data"]["link_id"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_rm, 200)

    def test_http_shared_habits_co_authoring_revisions_author_mismatch_and_retire(self) -> None:
        st_p, p_res = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "upsert_habit", "title": "周末一起泡茶",
             "habit_text": "周日下午煮一壶焙茶配小点心", "author_side": "partner"},
            csrf=True)
        self.assertEqual(st_p, 200, p_res)
        p_hid = p_res["data"]["habit_id"]

        st_h, h_res = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "upsert_habit", "title": "雨天留白",
             "habit_text": "下雨傍晚不催进度，各自安静看书", "author_side": "her"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_h, 200, h_res)
        h_hid = h_res["data"]["habit_id"]

        # Cross-author edit & retire -> 409 AUTHOR_MISMATCH
        st_ce, ce_res = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "upsert_habit", "habit_id": h_hid, "title": "改她的",
             "habit_text": "越权修改", "author_side": "partner"},
            csrf=True)
        self.assertEqual(st_ce, 409, ce_res)
        self.assertEqual(ce_res["reason_code"], "AUTHOR_MISMATCH")

        st_cr, cr_res = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "retire_habit", "habit_id": p_hid, "author_side": "her"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_cr, 409, cr_res)
        self.assertEqual(cr_res["reason_code"], "AUTHOR_MISMATCH")

        # Revise partner's own habit twice, then retire
        self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "upsert_habit", "habit_id": p_hid, "title": "周末一起泡茶",
             "habit_text": "天冷改煮热麦茶，不挑固定钟点",
             "revised_reason": "入冬换温和的无咖啡因茶", "author_side": "partner"},
            csrf=True)
        st_ret, ret_res = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "retire_habit", "habit_id": p_hid,
             "retired_reason": "已经成了自然默契", "author_side": "partner"},
            csrf=True)
        self.assertEqual(st_ret, 200, ret_res)
        self.assertEqual(ret_res["data"]["habit"]["status"], "RETIRED")
        self.assertEqual(ret_res["data"]["habit"]["revision_count"], 1)
        self.assertEqual(len(ret_res["data"]["habit"]["revisions"]), 1)

    def test_http_shared_gifts_her_draft_wrapped_invisibility_until_send_gift(self) -> None:
        # Her creates draft gift linked to delivered revisit v1
        st_g, g_res = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "upsert_gift", "author_side": "her", "stage": "draft",
             "title": "海风手绘明信片",
             "draft_seed_note": "散步捡到贝壳时突然想画一张明信片",
             "artifact_id": self.stack.revisit["artifact_id"],
             "version_id": self.stack.revisit["v1"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_g, 200, g_res)
        gid = g_res["data"]["gift_id"]

        # Wrap it — still MUST be completely invisible in partner_default!
        st_w, _ = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "upsert_gift", "gift_id": gid, "author_side": "her",
             "stage": "wrapped", "title": "海风手绘明信片",
             "draft_seed_note": "散步捡到贝壳时突然想画一张明信片",
             "finished_note": "系上了浅蓝色棉线"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_w, 200)

        _, partner_hidden = self._call("GET", "/api/phone/longterm/gifts-lantern")
        self.assertEqual(partner_hidden["data"]["shared_gifts"]["gifts"], [])
        self.assertEqual(partner_hidden["data"]["shared_gifts"]["visible_count"], 0)
        self.assertNotIn("海风手绘明信片", json.dumps(partner_hidden, ensure_ascii=False))

        # Subject view sees her wrapped gift
        _, subj_seen = self._call("GET", "/api/phone/longterm/gifts-lantern",
                                  capability=CAP_TOKEN)
        self.assertEqual(len(subj_seen["data"]["shared_gifts"]["gifts"]), 1)

        # Send gift -> partner_default now sees BOTH draft_seed_note and finished_note + excerpt!
        st_send, send_res = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "send_gift", "gift_id": gid, "author_side": "her",
             "finished_note": "系上了浅蓝色棉线，放在你的书桌角啦"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_send, 200, send_res)

        _, partner_revealed = self._call("GET", "/api/phone/longterm/gifts-lantern")
        gifts = partner_revealed["data"]["shared_gifts"]["gifts"]
        self.assertEqual(len(gifts), 1)
        self.assertEqual(gifts[0]["stage"], "gifted")
        self.assertEqual(gifts[0]["draft_seed_note"], "散步捡到贝壳时突然想画一张明信片")
        self.assertEqual(gifts[0]["finished_note"], "系上了浅蓝色棉线，放在你的书桌角啦")
        self.assertEqual(gifts[0]["linked_work_status"], "available")
        self.assertTrue(bool(gifts[0]["linked_work_excerpt"]))

        # Regression to wrapped -> 409 GIFT_ALREADY_SENT
        st_reg, reg_res = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "upsert_gift", "gift_id": gid, "author_side": "her",
             "stage": "wrapped", "title": "海风手绘明信片",
             "draft_seed_note": "试图撤回包装"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_reg, 409, reg_res)
        self.assertEqual(reg_res["reason_code"], "GIFT_ALREADY_SENT")

        # Her gift linked to undelivered v3 -> gifted metadata shown, work excerpt withheld!
        _, g_undeliv = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "upsert_gift", "author_side": "her", "stage": "wrapped",
             "title": "夹着未公开草稿的信封",
             "draft_seed_note": "最初想把第三版草稿附在里面",
             "artifact_id": self.stack.revisit["artifact_id"],
             "version_id": self.stack.revisit["v3"]},
            csrf=True, capability=CAP_TOKEN)
        self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "send_gift", "gift_id": g_undeliv["data"]["gift_id"],
             "author_side": "her", "finished_note": "先送出信封本身"},
            csrf=True, capability=CAP_TOKEN)
        _, after_undeliv = self._call("GET", "/api/phone/longterm/gifts-lantern")
        g2 = [
            g for g in after_undeliv["data"]["shared_gifts"]["gifts"]
            if g["gift_id"] == g_undeliv["data"]["gift_id"]
        ][0]
        self.assertEqual(g2["linked_work_status"], "withheld")
        self.assertEqual(g2["linked_work_reason"], "VERSION_NOT_DELIVERED")
        self.assertIsNone(g2["linked_work_excerpt"])
        self.assertNotIn("artifact_id", g2)

    def test_http_memory_lantern_default_then_only_vs_with_later_context(self) -> None:
        # Omitted query defaults to layer=then_only -> past never knows the future
        st_def, def_body = self._call("GET", "/api/phone/longterm/gifts-lantern")
        self.assertEqual(st_def, 200, def_body)
        ml_def = def_body["data"]["memory_lantern"]
        self.assertEqual(ml_def["layer"], "then_only")
        self.assertFalse(ml_def["later_context_included"])
        self.assertGreaterEqual(ml_def["entry_count"], 1)
        for entry in ml_def["entries"]:
            self.assertEqual(entry["later_remakes"], [])
            self.assertIsNone(entry["later_reflection"])

        # Explicit ?layer=with_later_context in partner_default vs subject
        _, p_later = self._call(
            "GET", "/api/phone/longterm/gifts-lantern?layer=with_later_context")
        e_partner = p_later["data"]["memory_lantern"]["entries"][0]
        self.assertEqual(e_partner["then_snapshot"]["status"], "available")
        statuses_p = [r["status"] for r in e_partner["later_remakes"]]
        self.assertIn("available", statuses_p)
        self.assertIn("withheld", statuses_p)
        raw_p = json.dumps(p_later, ensure_ascii=False)
        self.assertNotIn(self.fixture.DERIVED_PRIVATE_TITLE, raw_p)
        self.assertNotIn(self.fixture.DERIVED_PRIVATE_TEXT, raw_p)
        self.assertIn(self.fixture.DERIVED_SHARED_EXCERPT, raw_p)

        _, s_later = self._call(
            "GET", "/api/phone/longterm/gifts-lantern?layer=with_later_context",
            capability=CAP_TOKEN)
        e_subj = s_later["data"]["memory_lantern"]["entries"][0]
        statuses_s = [r["status"] for r in e_subj["later_remakes"]]
        self.assertEqual(statuses_s, ["available", "available"])
        raw_s = json.dumps(s_later, ensure_ascii=False)
        self.assertIn(self.fixture.DERIVED_PRIVATE_TITLE, raw_s)

    def test_http_memory_lantern_reflections_and_archived_or_bad_version_refusal(self) -> None:
        # Archived source refused 409 ARCHIVED_SOURCE
        st_arch, arch_err = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "record_later_reflection",
             "source_artifact_id": self.stack.archived["artifact_id"],
             "source_version_id": self.stack.archived["v1"],
             "reflection_note": "归档作品不可挂新回望"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_arch, 409, arch_err)
        self.assertEqual(arch_err["reason_code"], "ARCHIVED_SOURCE")

        # Cross-artifact version refused 409 VERSION_NOT_IN_ARTIFACT
        st_ver, ver_err = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "record_later_reflection",
             "source_artifact_id": self.stack.revisit["artifact_id"],
             "source_version_id": self.stack.other_work_version,
             "reflection_note": "版本不属于该作品"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_ver, 409, ver_err)
        self.assertEqual(ver_err["reason_code"], "VERSION_NOT_IN_ARTIFACT")

        # Record private reflection -> hidden in partner_default, visible in subject
        st_priv, _ = self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "record_later_reflection",
             "source_artifact_id": self.stack.revisit["artifact_id"],
             "source_version_id": self.stack.revisit["v1"],
             "reflection_note": "只写给自己看的当年心境", "shared": False},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(st_priv, 200)

        _, p_priv = self._call(
            "GET", "/api/phone/longterm/gifts-lantern?layer=with_later_context")
        self.assertIsNone(p_priv["data"]["memory_lantern"]["entries"][0]["later_reflection"])

        # Update to shared=True -> visible in partner_default ONLY under with_later_context
        self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "record_later_reflection",
             "source_artifact_id": self.stack.revisit["artifact_id"],
             "source_version_id": self.stack.revisit["v1"],
             "reflection_note": "一年后一起翻看第一版时的笑意", "shared": True},
            csrf=True, capability=CAP_TOKEN)
        _, p_then = self._call(
            "GET", "/api/phone/longterm/gifts-lantern?layer=then_only")
        self.assertIsNone(p_then["data"]["memory_lantern"]["entries"][0]["later_reflection"])
        _, p_shared = self._call(
            "GET", "/api/phone/longterm/gifts-lantern?layer=with_later_context")
        self.assertEqual(
            p_shared["data"]["memory_lantern"]["entries"][0]["later_reflection"]["reflection_note"],
            "一年后一起翻看第一版时的笑意",
        )

    def test_http_corrupt_state_file_fails_closed_and_refuses_overwrite_on_post(self) -> None:
        room_path = view.room_habits_state_path(self.stack.root)
        room_path.parent.mkdir(parents=True, exist_ok=True)
        room_path.write_text("{broken-json", encoding="utf-8")

        st_get, get_res = self._call("GET", "/api/phone/longterm/first-room-habits")
        self.assertEqual(st_get, 200, get_res)
        self.assertFalse(get_res["data"]["state"]["available"])
        self.assertEqual(get_res["data"]["state"]["reason_code"], "ROOM_HABITS_STATE_CORRUPT")

        st_post, post_res = self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "upsert_habit", "title": "不覆盖损坏文件",
             "habit_text": "拒绝写入", "author_side": "partner"},
            csrf=True)
        self.assertEqual(st_post, 409, post_res)
        self.assertEqual(post_res["reason_code"], "ROOM_HABITS_STATE_CORRUPT")
        self.assertEqual(room_path.read_text(encoding="utf-8"), "{broken-json")

    def test_http_resume_hub_cues_archived_projects_and_recovery_overview(self) -> None:
        st_p, p_hub = self._call("GET", "/api/phone/longterm/resume-hub")
        self.assertEqual(st_p, 200, p_hub)
        pdata = p_hub["data"]
        self.assertTrue(pdata["read_only"])
        self.assertTrue(pdata["project_cue"]["has_active_project"])
        self.assertIsNotNone(pdata["project_cue"]["share_id"])
        self.assertTrue(pdata["reading_cue"]["has_book"])
        self.assertNotIn("title", pdata["reading_cue"])
        self.assertTrue(pdata["topic_cue"]["has_recent_topic"])
        self.assertGreaterEqual(pdata["closed_projects"]["archived_count"], 1)
        self.assertTrue(pdata["closed_projects"]["closed_cleanly"])
        self.assertTrue(pdata["closed_projects"]["no_nagging"])
        self.assertNotIn("items", pdata["closed_projects"])
        self.assertNotIn(self.fixture.outing_fixture.ARCHIVE_TITLE,
                         json.dumps(pdata, ensure_ascii=False))
        self.assertNotIn(self.fixture.DERIVED_PRIVATE_TITLE,
                         json.dumps(pdata, ensure_ascii=False))
        self.assertTrue(pdata["recovery_overview"]["read_only"])
        self.assertIn("forget_barrier_status", pdata["recovery_overview"])

        st_s, s_hub = self._call("GET", "/api/phone/longterm/resume-hub",
                                 capability=CAP_TOKEN)
        self.assertEqual(st_s, 200, s_hub)
        sdata = s_hub["data"]
        self.assertIn("items", sdata["closed_projects"])
        archived_titles = [row["title"] for row in sdata["closed_projects"]["items"]]
        self.assertIn(self.fixture.outing_fixture.ARCHIVE_TITLE, archived_titles)

    def _snapshot_tree(self) -> dict[str, str]:
        snap: dict[str, str] = {}
        for base in (self.stack.instance_dir, self.stack.root / "data",
                     self.stack.root / "phone"):
            if not Path(base).exists():
                continue
            for path in sorted(Path(base).rglob("*")):
                if not path.is_file() or path.name.endswith(".sock"):
                    continue
                snap[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return snap

    def test_http_repeated_gets_across_all_three_endpoints_are_strictly_zero_write(self) -> None:
        self._call(
            "POST", "/api/phone/longterm/first-room-habits",
            {"action": "capture_first_room", "note": "零写快照测试"},
            csrf=True, capability=CAP_TOKEN)
        self._call(
            "POST", "/api/phone/longterm/gifts-lantern",
            {"action": "upsert_gift", "author_side": "partner", "stage": "draft",
             "title": "零写测试礼物", "draft_seed_note": "测试草稿"},
            csrf=True)

        before = self._snapshot_tree()
        endpoints = (
            "/api/phone/longterm/first-room-habits",
            "/api/phone/longterm/gifts-lantern",
            "/api/phone/longterm/gifts-lantern?layer=then_only",
            "/api/phone/longterm/gifts-lantern?layer=with_later_context",
            "/api/phone/longterm/resume-hub",
        )
        first = [self._call("GET", ep)[1] for ep in endpoints] + [
            self._call("GET", ep, capability=CAP_TOKEN)[1] for ep in endpoints
        ]
        second = [self._call("GET", ep)[1] for ep in endpoints] + [
            self._call("GET", ep, capability=CAP_TOKEN)[1] for ep in endpoints
        ]
        self.assertEqual(first, second)
        self.assertEqual(before, self._snapshot_tree())


if __name__ == "__main__":
    unittest.main()
