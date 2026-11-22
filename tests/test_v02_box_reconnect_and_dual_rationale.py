#!/usr/bin/env python3
"""0.2 Ch.15 §10 (A / B / E / J) & Ch.13 §3 专项验收单测：

1. §10.A 还没给你看的盒子（unshown_work_box）整理生命周期、撤回展示意图、
   源删除/recall 墓碑零写读屏障，以及严格伴侣侧隐私隔离；
2. §10.B 共同决定保留双份可改理由（dual_rationale_decisions）：
   各自仅可修订己方理由，跨侧拦截（403），绝不晋升永久偏好；
3. §10.E “当时没看到”的温暖重接（missed_moment_reconnections）：
   伴侣补材料仅推进 MATERIAL_SUPPLIED，不可单方面强制 RECONNECTED_NOW_KNOWN；
   主体补看后永久保留 missed_originally=True；
4. §10.J 共同作者异步贡献（async_coauthor_contributions）与
   Ch.13 §3 纯零写只读联系人通道名录（contact_directory），
   含真回执状态映射（不假装未说已懂）与模拟联系人严格标识（is_simulated=True）。
"""

from __future__ import annotations

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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_common_ground as cg  # noqa: E402
from components.life.scripts import v02_crossmodal_perception as cm_engine  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import v02_crossmodal_view as cm_view  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04_nb  # noqa: E402

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.name.endswith((".sqlite-shm", ".sqlite-wal")):
            continue
        rel = p.relative_to(root).as_posix()
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


class Test00CodeHygieneAndPersonaNeutrality(unittest.TestCase):
    def test_pure_lf_no_emoji_and_persona_neutral_across_write_scope(self) -> None:
        targets = (
            ROOT / "components" / "life" / "scripts" / "v02_common_ground.py",
            ROOT / "components" / "life" / "scripts" / "v02_crossmodal_perception.py",
            ROOT / "components" / "phone" / "v02_crossmodal_view.py",
            ROOT / "tests" / "test_v02_box_reconnect_and_dual_rationale.py",
        )
        for path in targets:
            self.assertTrue(path.is_file(), f"Missing file: {path}")
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, f"CR byte found in {path}; must use pure LF")
            text = raw.decode("utf-8")
            self.assertNotIn("\u5343\u4ee3", text, f"Hardcoded persona token found in {path}")
            self.assertNotIn("\u5979", text, f"Gendered pronoun found in {path}")
            for ch in text:
                code = ord(ch)
                is_emoji = (
                    0x1F300 <= code <= 0x1FAFF
                    or 0x2600 <= code <= 0x27BF
                )
                self.assertFalse(is_emoji, f"Emoji U+{code:X} found in {path}")


class Test01UnshownWorkBoxCurationLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.day_root = Path(self._tmp.name) / "day_root"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_empty_root_zero_write_and_partner_strict_privacy(self) -> None:
        self.assertFalse(self.day_root.exists())
        c_p, v_p = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=False)
        c_s, v_s = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        ctx_p = cm_view.summarize_crossmodal_for_context(self.day_root, include_private=False)
        self.assertEqual(c_p, 200)
        self.assertEqual(c_s, 200)
        self.assertEqual(v_p["llm_calls"], 0)
        self.assertEqual(v_s["llm_calls"], 0)
        self.assertEqual(ctx_p["llm_calls"], 0)
        self.assertFalse(self.day_root.exists(), "GET and summarize must not create day_root")

        box_p = v_p["unshown_work_box"]
        self.assertEqual(set(box_p.keys()), {"visible_items"})
        self.assertEqual(box_p["visible_items"], [])

    def test_02_five_curation_states_reuse_modes_and_strict_partner_privacy(self) -> None:
        entries = [
            {
                "box_item_id": "box_keep",
                "artifact_id": "artifact:draft_keep",
                "version_id": "v1",
                "title_cn": "未公开窗边素描一",
                "curation_state": "kept",
                "reuse_mode": "none",
                "note_cn": "先自己留着",
                "operation_key": "box-op-keep",
            },
            {
                "box_item_id": "box_reuse",
                "artifact_id": "artifact:draft_reuse",
                "version_id": "v2",
                "title_cn": "局部笔触复用稿",
                "curation_state": "reused_fragment",
                "reuse_mode": "adapt",
                "note_cn": "把阴影过渡改用到新画里",
                "operation_key": "box-op-reuse",
            },
            {
                "box_item_id": "box_shelve",
                "artifact_id": "artifact:draft_shelve",
                "version_id": "v1",
                "title_cn": "暂时搁置的海风笔记",
                "curation_state": "shelved",
                "reuse_mode": "structure_ref",
                "note_cn": "下周再看",
                "operation_key": "box-op-shelve",
            },
            {
                "box_item_id": "box_drop",
                "artifact_id": "artifact:draft_drop",
                "version_id": "v1",
                "title_cn": "弃用的草稿线",
                "curation_state": "dropped",
                "reuse_mode": "quote",
                "note_cn": "不用了",
                "operation_key": "box-op-drop",
            },
            {
                "box_item_id": "box_show",
                "artifact_id": "artifact:draft_show",
                "version_id": "v3",
                "title_cn": "雨后街角水彩卡片",
                "curation_state": "selected_to_show",
                "reuse_mode": "direct_reuse",
                "note_cn": "这张想给你看看",
                "operation_key": "box-op-show",
            },
        ]
        for pl in entries:
            code, res = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {"action": "curate_unshown_box_item", **pl},
                actor_side="subject",
                now_iso="2026-11-18T10:00:00+08:00",
            )
            self.assertEqual(code, 200, res)
            self.assertEqual(res["llm_calls"], 0)

        # Idempotent replay and conflict check
        c_dup, res_dup = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "curate_unshown_box_item", **entries[0]},
            actor_side="subject",
        )
        self.assertEqual(c_dup, 200)
        self.assertEqual(res_dup["command_status"], "DUPLICATE")

        c_conf, res_conf = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "curate_unshown_box_item",
                **entries[0],
                "title_cn": "冲突改名",
            },
            actor_side="subject",
        )
        self.assertEqual(c_conf, 409)
        self.assertEqual(res_conf["reason_code"], "OPERATION_KEY_CONFLICT")

        # Strict partner privacy on read
        snap_before = _snapshot_tree(self.day_root)
        c_p, v_p = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=False)
        self.assertEqual(c_p, 200)
        self.assertEqual(_snapshot_tree(self.day_root), snap_before, "Partner GET must be zero-write")

        box_p = v_p["unshown_work_box"]
        self.assertEqual(set(box_p.keys()), {"visible_items"})
        for forbidden_key in (
            "kept",
            "reused_fragment",
            "shelved",
            "dropped",
            "items",
            "invalidated_items",
            "counts_by_state",
            "total_count",
            "hidden_count",
        ):
            self.assertNotIn(forbidden_key, box_p)

        self.assertEqual(len(box_p["visible_items"]), 1)
        self.assertEqual(box_p["visible_items"][0]["box_item_id"], "box_show")
        self.assertEqual(box_p["visible_items"][0]["title_cn"], "雨后街角水彩卡片")

        partner_dump = json.dumps(box_p, ensure_ascii=False)
        for secret_title in (
            "未公开窗边素描一",
            "局部笔触复用稿",
            "暂时搁置的海风笔记",
            "弃用的草稿线",
        ):
            self.assertNotIn(secret_title, partner_dump)

        # Subject view sees all 5 categories & counts
        c_s, v_s = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        self.assertEqual(c_s, 200)
        self.assertEqual(_snapshot_tree(self.day_root), snap_before, "Subject GET must be zero-write")
        box_s = v_s["unshown_work_box"]
        self.assertEqual(box_s["total_count"], 5)
        self.assertEqual(box_s["hidden_count"], 4)
        self.assertEqual(len(box_s["kept"]), 1)
        self.assertEqual(len(box_s["reused_fragment"]), 1)
        self.assertEqual(len(box_s["shelved"]), 1)
        self.assertEqual(len(box_s["dropped"]), 1)
        self.assertEqual(len(box_s["selected_to_show"]), 1)
        self.assertEqual(box_s["reused_fragment"][0]["reuse_mode"], "adapt")

    def test_03_withdraw_box_show_intent_keeps_work_intact(self) -> None:
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "curate_unshown_box_item",
                "box_item_id": "box_01",
                "artifact_id": "artifact:watercolor_01",
                "version_id": "v2",
                "title_cn": "夜雨路灯水彩",
                "curation_state": "selected_to_show",
                "operation_key": "box-w-1",
            },
            actor_side="subject",
        )

        # Partner cannot withdraw
        c_forbid, err_forbid = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "withdraw_box_show_intent", "box_item_id": "box_01"},
            actor_side="partner",
        )
        self.assertEqual(c_forbid, 403)
        self.assertEqual(err_forbid["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Subject withdraws show intent: selected_to_show -> kept, work_intact: True
        c_wd, res_wd = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "withdraw_box_show_intent",
                "box_item_id": "box_01",
                "operation_key": "box-w-2",
            },
            actor_side="subject",
        )
        self.assertEqual(c_wd, 200, res_wd)
        box_s = res_wd["unshown_work_box"]
        self.assertEqual(box_s["visible_items"], [])
        self.assertEqual(len(box_s["kept"]), 1)
        kept_item = box_s["kept"][0]
        self.assertEqual(kept_item["curation_state"], "kept")
        self.assertTrue(kept_item["work_intact"])

        # Partner view no longer sees it, zero counts leaked
        _, v_p = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=False)
        self.assertEqual(v_p["unshown_work_box"], {"visible_items": []})

    def test_04_invalidate_deleted_box_source_and_read_time_recall_tombstone_barrier(self) -> None:
        # Item 1: explicitly invalidated via invalidate_deleted_box_source
        # Item 2: tombstoned externally via phone/v02_recall_projection.json (artifact_id)
        # Item 3: tombstoned externally via phone/v02_recall_projection.json (box_item_id)
        # Item 4: curated with source_deleted=True
        # Item 5: live selected_to_show item
        seed_items = [
            ("box_del_api", "artifact:del_api", "selected_to_show", False),
            ("box_tomb_art", "artifact:tomb_art", "selected_to_show", False),
            ("box_tomb_id", "artifact:live_for_id_test", "selected_to_show", False),
            ("box_src_del", "artifact:src_del_flag", "selected_to_show", True),
            ("box_live", "artifact:still_live", "selected_to_show", False),
        ]
        for idx, (bid, aid, st, src_del) in enumerate(seed_items, start=1):
            c, r = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {
                    "action": "curate_unshown_box_item",
                    "box_item_id": bid,
                    "artifact_id": aid,
                    "version_id": "v1",
                    "title_cn": f"条目_{bid}",
                    "curation_state": st,
                    "source_deleted": src_del,
                    "operation_key": f"box-seed-{idx}",
                },
                actor_side="subject",
            )
            self.assertEqual(c, 200, r)

        # Partner forbidden from invalidate_deleted_box_source
        c_p_inv, _ = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "invalidate_deleted_box_source", "artifact_id": "artifact:del_api"},
            actor_side="partner",
        )
        self.assertEqual(c_p_inv, 403)

        # Subject invalidates artifact:del_api
        c_inv, r_inv = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "invalidate_deleted_box_source",
                "artifact_id": "artifact:del_api",
                "operation_key": "box-inv-1",
            },
            actor_side="subject",
        )
        self.assertEqual(c_inv, 200, r_inv)
        by_id_after_inv = {x["box_item_id"]: x for x in r_inv["unshown_work_box"]["items"]}
        self.assertTrue(by_id_after_inv["box_del_api"]["source_deleted"])
        self.assertTrue(by_id_after_inv["box_del_api"]["candidate_invalidated"])
        self.assertFalse(by_id_after_inv["box_del_api"]["selected_to_show_eligible"])
        self.assertNotEqual(by_id_after_inv["box_del_api"]["curation_state"], "selected_to_show")

        self.assertTrue(by_id_after_inv["box_src_del"]["source_deleted"])
        self.assertTrue(by_id_after_inv["box_src_del"]["candidate_invalidated"])
        self.assertFalse(by_id_after_inv["box_src_del"]["selected_to_show_eligible"])

        # External tombstone in phone/v02_recall_projection.json
        rp_path = self.day_root / "phone" / "v02_recall_projection.json"
        rp_path.parent.mkdir(parents=True, exist_ok=True)
        rp_path.write_text(
            json.dumps(
                {
                    "schema": "nyairo.v02.recall_projection.v1",
                    "tombstoned_ids": ["artifact:tomb_art"],
                    "items": [
                        {"id": "box_tomb_id", "deleted": True},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Read-time zero-write lineage check
        snap = _snapshot_tree(self.day_root)
        _, v_s = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        _, v_p = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=False)
        self.assertEqual(_snapshot_tree(self.day_root), snap, "Read-time lineage check must be 100% zero-write")

        by_id_s = {x["box_item_id"]: x for x in v_s["unshown_work_box"]["items"]}
        for dead_id in ("box_del_api", "box_tomb_art", "box_tomb_id", "box_src_del"):
            self.assertTrue(by_id_s[dead_id]["candidate_invalidated"], dead_id)
            self.assertFalse(by_id_s[dead_id]["selected_to_show_eligible"], dead_id)

        self.assertEqual(
            [x["box_item_id"] for x in v_s["unshown_work_box"]["selected_to_show"]],
            ["box_live"],
        )
        self.assertEqual(
            [x["box_item_id"] for x in v_p["unshown_work_box"]["visible_items"]],
            ["box_live"],
        )


class Test02DualRationaleMissedMomentsCoauthorAndContacts(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.day_root = Path(self._tmp.name) / "day_root"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_dual_rationale_decisions_and_cross_side_isolation(self) -> None:
        rec = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "record_dual_rationale_decision",
                "decision_id": "dec_cover_01",
                "topic_cn": "纪念画册封面定版",
                "scope_cn": "本次春季画册首印版",
                "adopted_choice_cn": "采用热闹集市插画封面",
                "subject_rationale_cn": "尊重共同逛街的记忆，不过内心扉页想留一片空白纸",
                "partner_rationale_cn": "外面看起来有烟火气，翻开第一眼就想起那条巷子",
                "subject_kept_detail_cn": "保留安静纯白扉页与淡灰页码",
                "operation_key": "dec-op-1",
            },
            now_iso="2026-11-18T11:00:00+08:00",
        )
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(rec["status_code"], 200)
        dec = rec["decision"]
        self.assertFalse(dec["promotes_permanent_preference"])
        self.assertEqual(dec["subject_kept_detail_cn"], "保留安静纯白扉页与淡灰页码")

        # Idempotent replay + conflict
        dup = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "record_dual_rationale_decision",
                "decision_id": "dec_cover_01",
                "topic_cn": "纪念画册封面定版",
                "scope_cn": "本次春季画册首印版",
                "adopted_choice_cn": "采用热闹集市插画封面",
                "subject_rationale_cn": "尊重共同逛街的记忆，不过内心扉页想留一片空白纸",
                "partner_rationale_cn": "外面看起来有烟火气，翻开第一眼就想起那条巷子",
                "subject_kept_detail_cn": "保留安静纯白扉页与淡灰页码",
                "operation_key": "dec-op-1",
            },
        )
        self.assertTrue(dup["ok"])
        self.assertTrue(dup.get("already_applied"))

        conf = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "record_dual_rationale_decision",
                "decision_id": "dec_cover_01",
                "topic_cn": "改题目触发幂等键冲突",
                "scope_cn": "s",
                "adopted_choice_cn": "c",
                "subject_rationale_cn": "sr",
                "partner_rationale_cn": "pr",
                "operation_key": "dec-op-1",
            },
        )
        self.assertFalse(conf["ok"])
        self.assertEqual(conf["status_code"], 409)
        self.assertEqual(conf["error_code"], "OPERATION_KEY_CONFLICT")

        # Subject revises ONLY subject_rationale_cn
        rev_subj = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "revise_own_decision_rationale",
                "decision_id": "dec_cover_01",
                "actor_side": "subject",
                "new_rationale_cn": "后来发现封面热闹一点配合内页留白反而更有反差",
                "operation_key": "dec-rev-s1",
            },
            now_iso="2026-11-18T11:30:00+08:00",
        )
        self.assertTrue(rev_subj["ok"], rev_subj)
        self.assertEqual(
            rev_subj["decision"]["partner_rationale_cn"],
            "外面看起来有烟火气，翻开第一眼就想起那条巷子",
        )
        self.assertFalse(rev_subj["decision"]["promotes_permanent_preference"])

        # Partner revises ONLY partner_rationale_cn
        rev_part = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "revise_own_decision_rationale",
                "decision_id": "dec_cover_01",
                "actor_side": "partner",
                "new_rationale_cn": "主要是那盏橘色路灯画得很温暖",
                "operation_key": "dec-rev-p1",
            },
            now_iso="2026-11-18T11:45:00+08:00",
        )
        self.assertTrue(rev_part["ok"], rev_part)
        self.assertEqual(
            rev_part["decision"]["subject_rationale_cn"],
            "后来发现封面热闹一点配合内页留白反而更有反差",
        )
        self.assertEqual(
            rev_part["decision"]["partner_rationale_cn"],
            "主要是那盏橘色路灯画得很温暖",
        )
        self.assertFalse(rev_part["decision"]["promotes_permanent_preference"])

        # Cross-side edit attempts MUST fail with 403 and leave both rationales untouched
        cross_cases = [
            {
                "action": "revise_own_decision_rationale",
                "decision_id": "dec_cover_01",
                "actor_side": "partner",
                "target_side": "subject",
                "new_rationale_cn": "越权篡改对方理由",
            },
            {
                "action": "revise_own_decision_rationale",
                "decision_id": "dec_cover_01",
                "actor_side": "subject",
                "target_side": "partner",
                "new_rationale_cn": "越权篡改伴侣理由",
            },
            {
                "action": "revise_own_decision_rationale",
                "decision_id": "dec_cover_01",
                "actor_side": "partner",
                "subject_rationale_cn": "偷塞对侧字段",
                "new_rationale_cn": "看似正常",
            },
            {
                "action": "revise_own_decision_rationale",
                "decision_id": "dec_cover_01",
                "actor_side": "subject",
                "target_field": "partner_rationale_cn",
                "new_rationale_cn": "越权改字段",
            },
        ]
        for bad in cross_cases:
            err = cg.mutate_v02_common_ground(self.day_root, bad)
            self.assertFalse(err["ok"], bad)
            self.assertEqual(err["status_code"], 403, err)
            self.assertEqual(err["error_code"], "FORBIDDEN_ACTOR_SIDE", err)

        direct_cross = cg.revise_own_decision_rationale(
            self.day_root,
            decision_id="dec_cover_01",
            actor_side="partner",
            target_side="subject",
            new_rationale_cn="direct cross attempt",
        )
        self.assertFalse(direct_cross["ok"])
        self.assertEqual(direct_cross["status_code"], 403)

        view = cg.read_v02_common_ground_view(self.day_root, viewer="subject")
        self.assertEqual(len(view["dual_rationale_decisions"]), 1)
        self.assertEqual(
            view["dual_rationale_decisions"][0]["subject_rationale_cn"],
            "后来发现封面热闹一点配合内页留白反而更有反差",
        )
        self.assertEqual(
            view["dual_rationale_decisions"][0]["partner_rationale_cn"],
            "主要是那盏橘色路灯画得很温暖",
        )

    def test_02_missed_moment_warm_reconnection_anti_retroactive_omniscience(self) -> None:
        m1 = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "record_missed_moment",
                "moment_id": "mom_rain_rainbow",
                "summary_cn": "傍晚出门买纸错过了窗外那道双彩虹",
                "actor_side": "subject",
                "operation_key": "mom-op-1",
            },
            now_iso="2026-11-18T17:10:00+08:00",
        )
        self.assertTrue(m1["ok"], m1)
        self.assertTrue(m1["missed_moment"]["missed_originally"])
        self.assertEqual(m1["missed_moment"]["status"], cg.MISSED_STATUS_UNSEEN)
        self.assertIsNone(m1["missed_moment"]["known_since"])

        # Partner cannot unilaterally jump straight to RECONNECTED_NOW_KNOWN
        forbid_force = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "acknowledge_missed_moment_catchup",
                "moment_id": "mom_rain_rainbow",
                "catchup_note_cn": "替主体直接确认已知道",
                "actor_side": "partner",
            },
        )
        self.assertFalse(forbid_force["ok"])
        self.assertEqual(forbid_force["status_code"], 403)
        self.assertEqual(forbid_force["error_code"], "FORBIDDEN_ACTOR_SIDE")

        forbid_smuggle = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "supply_missed_moment_material",
                "moment_id": "mom_rain_rainbow",
                "material_note_cn": "补发照片",
                "actor_side": "partner",
                "status": "RECONNECTED_NOW_KNOWN",
            },
        )
        self.assertFalse(forbid_smuggle["ok"])
        self.assertEqual(forbid_smuggle["status_code"], 403)

        # Partner supplies material -> MATERIAL_SUPPLIED (still not RECONNECTED_NOW_KNOWN)
        sup = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "supply_missed_moment_material",
                "moment_id": "mom_rain_rainbow",
                "material_note_cn": "当时随手在阳台拍了两张照片和一段说明",
                "material_ref": "album:rainbow_02",
                "actor_side": "partner",
                "operation_key": "mom-op-2",
            },
            now_iso="2026-11-18T18:00:00+08:00",
        )
        self.assertTrue(sup["ok"], sup)
        self.assertEqual(sup["missed_moment"]["status"], cg.MISSED_STATUS_MATERIAL_SUPPLIED)
        self.assertIsNone(sup["missed_moment"]["known_since"])
        self.assertTrue(sup["missed_moment"]["missed_originally"])

        # Subject catches up -> RECONNECTED_NOW_KNOWN, known_since set, missed_originally STILL True
        ack = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "acknowledge_missed_moment_catchup",
                "moment_id": "mom_rain_rainbow",
                "catchup_note_cn": "看到照片了，现在才知道原来还挂在旧书店屋檐那边",
                "actor_side": "subject",
                "operation_key": "mom-op-3",
            },
            now_iso="2026-11-18T19:15:00+08:00",
        )
        self.assertTrue(ack["ok"], ack)
        mm = ack["missed_moment"]
        self.assertEqual(mm["status"], cg.MISSED_STATUS_RECONNECTED)
        self.assertEqual(mm["known_since"], "2026-11-18T19:15:00+08:00")
        self.assertTrue(mm["missed_originally"], "Anti-retroactive-omniscience: missed_originally must stay True")

    def test_03_async_coauthor_contributions_and_simulated_identity_preservation(self) -> None:
        sub_sim = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "submit_async_coauthor_contribution",
                "contrib_id": "c_sim_01",
                "work_ref": "zine:spring_issue_02",
                "author_id": "sim_saeki",
                "author_label_cn": "佐伯葵（模拟共创作者）",
                "is_simulated": True,
                "diff_summary_cn": "在第三页补充了咖啡屋手冲记录的边栏草图",
                "operation_key": "co-op-1",
            },
            now_iso="2026-11-18T14:00:00+08:00",
        )
        self.assertTrue(sub_sim["ok"], sub_sim)
        self.assertTrue(sub_sim["contribution"]["is_simulated"])

        sub_real = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "submit_async_coauthor_contribution",
                "contrib_id": "c_real_01",
                "work_ref": "zine:spring_issue_02",
                "author_id": "ext_friend_lin",
                "author_label_cn": "授权外部协作者林某",
                "is_simulated": False,
                "diff_summary_cn": "校对了第四页铅字排版间距",
                "operation_key": "co-op-2",
            },
            now_iso="2026-11-18T14:05:00+08:00",
        )
        self.assertTrue(sub_real["ok"], sub_real)
        self.assertFalse(sub_real["contribution"]["is_simulated"])

        sub_sim_3 = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "submit_async_coauthor_contribution",
                "contrib_id": "c_sim_02",
                "work_ref": "zine:spring_issue_02",
                "author_id": "sim_nukui",
                "author_label_cn": "温井慎一（模拟共创作者）",
                "is_simulated": True,
                "diff_summary_cn": "添了一张旧书脊配色小样",
                "operation_key": "co-op-3",
            },
        )
        self.assertTrue(sub_sim_3["ok"])

        # Partner cannot resolve coauthor diffs
        forbid_res = cg.mutate_v02_common_ground(
            self.day_root,
            {
                "action": "resolve_async_contribution",
                "contrib_id": "c_sim_01",
                "resolution": "accept_diff",
                "actor_side": "partner",
            },
        )
        self.assertFalse(forbid_res["ok"])
        self.assertEqual(forbid_res["status_code"], 403)

        # Subject tests all three resolutions while preserving is_simulated=True
        for cid, res_kind, opk in (
            ("c_sim_01", "accept_diff", "co-res-1"),
            ("c_sim_02", "propose_edit", "co-res-2"),
            ("c_real_01", "keep_own_branch", "co-res-3"),
        ):
            r = cg.mutate_v02_common_ground(
                self.day_root,
                {
                    "action": "resolve_async_contribution",
                    "contrib_id": cid,
                    "resolution": res_kind,
                    "note_cn": f"处理决定:{res_kind}",
                    "actor_side": "subject",
                    "operation_key": opk,
                },
                now_iso="2026-11-18T15:00:00+08:00",
            )
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["contribution"]["resolution"], res_kind)

        view = cg.read_v02_common_ground_view(self.day_root, viewer="subject")
        by_cid = {c["contrib_id"]: c for c in view["async_coauthor_contributions"]}
        self.assertTrue(by_cid["c_sim_01"]["is_simulated"])
        self.assertTrue(by_cid["c_sim_02"]["is_simulated"])
        self.assertFalse(by_cid["c_real_01"]["is_simulated"])

    def test_04_contact_directory_zero_write_no_fabrication_and_receipt_honesty(self) -> None:
        # 1) Empty missing root: 100% zero write, zero fabricated contacts, honest NONE receipt
        empty_root = Path(self._tmp.name) / "missing_root"
        self.assertFalse(empty_root.exists())
        view_empty = cg.read_v02_common_ground_view(empty_root, viewer="partner")
        self.assertFalse(empty_root.exists(), "GET contact_directory must not create missing root")

        cd_empty = view_empty["contact_directory"]
        self.assertTrue(cd_empty["zero_write"])
        self.assertTrue(cd_empty["address_is_not_identity"])
        self.assertEqual(cd_empty["simulated_contacts"], [])
        self.assertEqual(len(cd_empty["partner_channels"]), 1)
        pchan = cd_empty["partner_channels"][0]
        self.assertEqual(pchan["channel_id"], "local_partner")
        self.assertTrue(pchan["address_is_not_identity"])
        self.assertEqual(pchan["latest_receipt_state"], "NONE")
        self.assertFalse(pchan["understood_claimed"])

        # 2) phone/v02_common_ground.json with SENT receipt -> never claims UNDERSTOOD
        phone_cg = self.day_root / "phone" / "v02_common_ground.json"
        phone_cg.parent.mkdir(parents=True, exist_ok=True)
        phone_cg.write_text(
            json.dumps(
                {
                    "schema": "nyairo.v02.common_ground.v1",
                    "receipts": [
                        {"receipt_id": "rcpt_1", "delivery_state": "SENT", "at": "2026-11-18T10:00:00+08:00"},
                        {"receipt_id": "rcpt_2", "delivery_state": "DELIVERED", "at": "2026-11-18T10:02:00+08:00"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 3) Real world/v04_neighborhood_state.json + simulated coauthor author
        nb_state = v04_nb.default_state()
        v04_nb.save_state_atomic(self.day_root, nb_state)
        cg.submit_async_coauthor_contribution(
            self.day_root,
            contrib_id="c_dir_sim",
            work_ref="zine:issue_03",
            author_id="sim_illustrator_01",
            author_label_cn="模拟插画师",
            is_simulated=True,
            diff_summary_cn="加了两张线稿",
        )
        cg.submit_async_coauthor_contribution(
            self.day_root,
            contrib_id="c_dir_real",
            work_ref="zine:issue_03",
            author_id="real_author_01",
            author_label_cn="真实授权作者",
            is_simulated=False,
            diff_summary_cn="修改了前言",
        )

        snap_before = _snapshot_tree(self.day_root)
        view_pop = cg.read_v02_common_ground_view(self.day_root, viewer="subject")
        self.assertEqual(_snapshot_tree(self.day_root), snap_before, "GET must be 100% zero-write on populated root")

        cd_pop = view_pop["contact_directory"]
        p_card = cd_pop["partner_channels"][0]
        self.assertEqual(p_card["channel_id"], "local_partner")
        self.assertTrue(p_card["address_is_not_identity"])
        self.assertEqual(p_card["latest_receipt_state"], "DELIVERED")
        self.assertFalse(p_card["understood_claimed"])

        sim_contacts = cd_pop["simulated_contacts"]
        sim_ids = {c["contact_id"] for c in sim_contacts}
        self.assertIn("nukui_shinichi", sim_ids)
        self.assertIn("saeki_aoi", sim_ids)
        self.assertIn("sim_illustrator_01", sim_ids)
        self.assertNotIn("real_author_01", sim_ids)
        for c in sim_contacts:
            self.assertTrue(c["is_simulated"])
            self.assertTrue(c["address_is_not_identity"])


@POSIX_ONLY
class Test03HTTPIntegrationForCommonGroundAndCrossmodalBox(unittest.TestCase):
    CAP_TOKEN = "v02-box-reconnect-http-cap-20261118"

    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        cls.subject = fixture.SUBJECT
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = cls.subject
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v02-w2-http-"))
        cls.capability = phone_capability.Capability(
            token=cls.CAP_TOKEN,
            subject=cls.subject,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-18T08:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.temp_dir / "inst", port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        for rel in (
            "phone/v02_crossmodal.json",
            "phone/v02_common_ground.json",
            "memory/v02_common_ground_state.json",
        ):
            (self.stack.root / rel).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 18, 16, 0, tzinfo=timezone(timedelta(hours=8))
        )

    def _http(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=raw, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if raw is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_http_common_ground_dual_rationale_reconnect_coauthor_and_zero_write_gets(self) -> None:
        # 1. Record dual rationale decision via HTTP POST /api/phone/v02/common-ground
        c1, r1 = self._http(
            "POST",
            "/api/phone/v02/common-ground",
            {
                "action": "record_dual_rationale_decision",
                "decision_id": "http_dec_1",
                "topic_cn": "周末散步路线",
                "scope_cn": "本周六下午",
                "adopted_choice_cn": "走河堤旧桥线",
                "subject_rationale_cn": "河堤风比较静",
                "partner_rationale_cn": "路过那家老面包店",
                "operation_key": "http-dec-1",
            },
            csrf=True,
            capability=self.CAP_TOKEN,
        )
        self.assertEqual(c1, 200, r1)
        self.assertFalse(r1["data"]["decision"]["promotes_permanent_preference"])

        # Cross-side revision rejected with 403
        c_cross, r_cross = self._http(
            "POST",
            "/api/phone/v02/common-ground",
            {
                "action": "revise_own_decision_rationale",
                "decision_id": "http_dec_1",
                "actor_side": "partner",
                "target_side": "subject",
                "new_rationale_cn": "越权修改",
            },
            csrf=True,
        )
        self.assertEqual(c_cross, 403, r_cross)
        self.assertEqual(r_cross["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # 2. Missed moment flow via HTTP
        c_m1, _ = self._http(
            "POST",
            "/api/phone/v02/common-ground",
            {
                "action": "record_missed_moment",
                "moment_id": "http_mom_1",
                "summary_cn": "错过了楼下橘猫打盹",
                "actor_side": "subject",
                "operation_key": "http-mom-1",
            },
            csrf=True,
            capability=self.CAP_TOKEN,
        )
        self.assertEqual(c_m1, 200)

        c_m2, r_m2 = self._http(
            "POST",
            "/api/phone/v02/common-ground",
            {
                "action": "supply_missed_moment_material",
                "moment_id": "http_mom_1",
                "material_note_cn": "拍了照片放在相册",
                "actor_side": "partner",
                "operation_key": "http-mom-2",
            },
            csrf=True,
        )
        self.assertEqual(c_m2, 200, r_m2)
        self.assertEqual(r_m2["data"]["missed_moment"]["status"], "MATERIAL_SUPPLIED")

        c_m3_bad, _ = self._http(
            "POST",
            "/api/phone/v02/common-ground",
            {
                "action": "acknowledge_missed_moment_catchup",
                "moment_id": "http_mom_1",
                "catchup_note_cn": "越权确认",
                "actor_side": "partner",
            },
            csrf=True,
        )
        self.assertEqual(c_m3_bad, 403)

        c_m3, r_m3 = self._http(
            "POST",
            "/api/phone/v02/common-ground",
            {
                "action": "acknowledge_missed_moment_catchup",
                "moment_id": "http_mom_1",
                "catchup_note_cn": "看到小橘猫照片了",
                "actor_side": "subject",
                "operation_key": "http-mom-3",
            },
            csrf=True,
            capability=self.CAP_TOKEN,
        )
        self.assertEqual(c_m3, 200, r_m3)
        self.assertEqual(r_m3["data"]["missed_moment"]["status"], "RECONNECTED_NOW_KNOWN")
        self.assertTrue(r_m3["data"]["missed_moment"]["missed_originally"])

        # 3. Seed box directly via view and verify zero-write HTTP GET /api/phone/v02/crossmodal
        cm_view.mutate_v02_crossmodal_view(
            self.stack.root,
            {
                "action": "curate_unshown_box_item",
                "box_item_id": "http_box_priv",
                "artifact_id": "artifact:http_priv",
                "title_cn": "HTTP私密手稿",
                "curation_state": "kept",
            },
            actor_side="subject",
        )
        cm_view.mutate_v02_crossmodal_view(
            self.stack.root,
            {
                "action": "curate_unshown_box_item",
                "box_item_id": "http_box_show",
                "artifact_id": "artifact:http_show",
                "title_cn": "HTTP可看草图",
                "curation_state": "selected_to_show",
            },
            actor_side="subject",
        )

        snap = _snapshot_tree(self.stack.root)
        g_cg_p, b_cg_p = self._http("GET", "/api/phone/v02/common-ground")
        g_cg_s, b_cg_s = self._http("GET", "/api/phone/v02/common-ground", capability=self.CAP_TOKEN)
        g_cm_p, b_cm_p = self._http("GET", "/api/phone/v02/crossmodal")
        g_cm_s, b_cm_s = self._http("GET", "/api/phone/v02/crossmodal", capability=self.CAP_TOKEN)
        self.assertEqual(_snapshot_tree(self.stack.root), snap, "All HTTP GETs must be 100% zero-write")

        self.assertEqual(g_cg_p, 200)
        self.assertEqual(g_cg_s, 200)
        self.assertIn("contact_directory", b_cg_p["data"])
        self.assertIn("dual_rationale_decisions", b_cg_s["data"])

        self.assertEqual(g_cm_p, 200)
        self.assertEqual(g_cm_s, 200)
        self.assertEqual(set(b_cm_p["data"]["unshown_work_box"].keys()), {"visible_items"})
        self.assertNotIn("HTTP私密手稿", json.dumps(b_cm_p, ensure_ascii=False))
        self.assertIn("HTTP可看草图", json.dumps(b_cm_p, ensure_ascii=False))
        self.assertEqual(b_cm_s["data"]["unshown_work_box"]["total_count"], 2)


if __name__ == "__main__":
    unittest.main()
