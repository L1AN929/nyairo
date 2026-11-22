#!/usr/bin/env python3
"""v0.2 Ch.15 §7 (D01/D08/D09/D10) & §10 (C/D/H) 数字原生与日常自主机制验收测试。

覆盖：
1. D01 记忆场所（placed_memory_cues）：摆放、遮挡、移走（spot_id 移除/改换）、移除后立即撤出 active_cues 与背景摘要；
2. D08 塑造信息环境（info_environment_curations）：pin 置顶 / normal 在后 / mute & filter 屏蔽，且强制保留未过滤对照入口；
3. D09 未来自己的时间胶囊（future_self_capsules）：到时只变为 AVAILABLE（零写且不自动认领），可 open / decline_no_debt / keep_closed，零亏欠债；
4. §10.C 可拒绝的小把手（optional_next_gestures）：gesture_cn <= 80 字符；peek 只增计数绝不等于认领/恢复；零百分比/红色截止日字段；
5. D10 / §10.D 衰老工具三结尾（legacy_tool_endings）：keep_repaired / adapt_to_static_card / bid_farewell 均保持旧作品可读且零失败污名；
6. §10.H 温和责任回访（gentle_responsibility_reviews）：数周未碰无红警、无亏欠债；shared_partner_commitment 缺伴侣同意时退场拦截 409 EXTERNAL_COMMITMENT_REQUIRES_HANDOFF；
7. 100% 零写读（缺失/空/有数据根下文件树 + mtime + sha256 不变）、伴侣视角零私有项与零隐藏计数、403/404/409/400 边界、幂等与并发锁、纯 LF 与人物中立性。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "scripts"),
    str(REPO_ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_agency_depth_engine as ad_engine  # noqa: E402
from components.phone import v02_agency_depth_view as ad_view  # noqa: E402

T0 = "2026-11-18T09:00:00Z"
T1 = "2026-11-18T10:00:00Z"
T_WEEK_LATER = "2026-11-26T09:00:00Z"
T_MONTH_LATER = "2026-12-20T09:00:00Z"

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class TestV02Ch15MomentsAndDigitalMechanisms(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo_v02_ch15_")
        self.day_root = Path(self._tmp.name) / "day_root"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mutate(
        self,
        payload: dict[str, Any],
        *,
        actor_side: str = "subject",
        now_iso: str = T0,
    ) -> tuple[int, dict[str, Any]]:
        return ad_view.mutate_v02_agency_depth_view(
            self.day_root,
            payload,
            actor_side=actor_side,
            now_iso=now_iso,
        )

    def test_01_d01_placed_memory_cues_visibility_occlusion_move_and_remove(self) -> None:
        code, doc = self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_desk_sketch",
            "artifact_ref": "art:rainy_window_01",
            "spot_id": "desk_corner",
            "cue_text_cn": "桌角摆着去年雨天画的窗边速写",
            "visible": True,
            "shared": True,
        })
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["llm_calls"], 0)
        cues_view = doc["placed_memory_cues"]
        self.assertEqual(len(cues_view["active_cues"]), 1)
        self.assertEqual(cues_view["active_cues"][0]["cue_id"], "cue_desk_sketch")
        self.assertEqual(len(cues_view["items"]), 1)

        summary = ad_engine.summarize_agency_depth_state_for_context(
            self.day_root, include_private=True, now_iso=T1
        )
        self.assertEqual(len(summary["active_cues"]), 1)
        self.assertEqual(summary["active_cues"][0]["cue_id"], "cue_desk_sketch")

        # 遮挡后立即退出 active_cues 与背景摘要
        c_occ, d_occ = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_desk_sketch",
            "occluded": True,
        }, now_iso=T1)
        self.assertEqual(c_occ, 200)
        self.assertEqual(d_occ["placed_memory_cues"]["active_cues"], [])
        self.assertEqual(len(d_occ["placed_memory_cues"]["items"]), 1)
        self.assertEqual(
            ad_engine.summarize_agency_depth_state_for_context(
                self.day_root, include_private=True, now_iso=T1
            )["active_cues"],
            [],
        )

        # 取消遮挡后恢复
        _, d_unocc = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_desk_sketch",
            "occluded": False,
        }, now_iso=T1)
        self.assertEqual(len(d_unocc["placed_memory_cues"]["active_cues"]), 1)

        # 移走（变更 spot_id）后立即退出 active_cues，不发生幻觉触发
        _, d_moved = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_desk_sketch",
            "spot_id": "storage_drawer",
        }, now_iso=T1)
        self.assertEqual(d_moved["placed_memory_cues"]["active_cues"], [])
        self.assertEqual(
            ad_engine.summarize_agency_depth_state_for_context(
                self.day_root, include_private=True, now_iso=T1
            )["active_cues"],
            [],
        )

        # spot_id=None 同样撤出 active_cues
        self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_desk_sketch",
            "artifact_ref": "art:rainy_window_01",
            "spot_id": "desk_corner",
            "cue_text_cn": "桌角摆着去年雨天画的窗边速写",
            "visible": True,
            "shared": True,
        }, now_iso=T1)
        _, d_none_spot = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_desk_sketch",
            "spot_id": None,
        }, now_iso=T1)
        self.assertEqual(d_none_spot["placed_memory_cues"]["active_cues"], [])

        # 重新放回另一位置，再试清空 spot_id / visible=False / removed=True
        self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_shelf_card",
            "artifact_ref": "art:leaf_card",
            "spot_id": "window_sill",
            "cue_text_cn": "窗台干叶书签",
            "visible": True,
            "shared": False,
        })
        _, d_blank_spot = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_shelf_card",
            "spot_id": "",
        })
        self.assertEqual(d_blank_spot["placed_memory_cues"]["active_cues"], [])

        self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_shelf_card",
            "artifact_ref": "art:leaf_card",
            "spot_id": "window_sill",
            "cue_text_cn": "窗台干叶书签",
            "visible": True,
            "shared": False,
        })
        _, d_hidden = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_shelf_card",
            "visible": False,
        })
        self.assertEqual(d_hidden["placed_memory_cues"]["active_cues"], [])

        _, d_removed = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_shelf_card",
            "visible": True,
            "removed": True,
        })
        self.assertEqual(d_removed["placed_memory_cues"]["active_cues"], [])

        # 不存在的 cue_id 返回 404；place_memory_cue 空 spot_id 返回 400
        c_404, d_404 = self._mutate({
            "action": "update_placed_cue_visibility",
            "cue_id": "cue_missing",
            "removed": True,
        })
        self.assertEqual(c_404, 404)
        self.assertEqual(d_404["reason_code"], "CUE_NOT_FOUND")

        c_400, d_400 = self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_bad",
            "artifact_ref": "art:1",
            "spot_id": "   ",
            "cue_text_cn": "测试",
        })
        self.assertEqual(c_400, 400)
        self.assertEqual(d_400["reason_code"], "INVALID_FIELDS")

    def test_02_d08_info_environment_curation_preserves_unfiltered_control(self) -> None:
        sources = [
            ("src_norm_1", "日常插画小报", "normal", "保留常规更新", True),
            ("src_mute_1", "高频热榜推送", "mute", "太吵暂时静音", False),
            ("src_pin_1", "木版画工艺季刊", "pin", "最近常看置顶", True),
            ("src_filter_1", "营销折扣列表", "filter", "过滤干扰信息", True),
            ("src_pin_2", "雨声声景电台", "pin", "工作时常放置顶", False),
        ]
        for sid, title, mode, reason, shared in sources:
            code, doc = self._mutate({
                "action": "curate_info_source",
                "source_id": sid,
                "title_cn": title,
                "mode": mode,
                "reason_cn": reason,
                "shared": shared,
            })
            self.assertEqual(code, 200, doc)

        info_view = doc["info_environment_curations"]
        self.assertTrue(info_view["unfiltered_control_available"])
        self.assertEqual(
            [e["source_id"] for e in info_view["curated_feed"]],
            ["src_pin_1", "src_pin_2", "src_norm_1"],
        )
        self.assertEqual(
            [e["source_id"] for e in info_view["unfiltered_control_entries"]],
            ["src_norm_1", "src_mute_1", "src_pin_1", "src_filter_1", "src_pin_2"],
        )

        # 更新已有 source_id 的模式（mute -> pin）应进入置顶前列
        c_upd, d_upd = self._mutate({
            "action": "curate_info_source",
            "source_id": "src_mute_1",
            "title_cn": "高频热榜推送",
            "mode": "pin",
            "reason_cn": "临时要看置顶",
        })
        self.assertEqual(c_upd, 200)
        self.assertEqual(
            [e["source_id"] for e in d_upd["info_environment_curations"]["curated_feed"]],
            ["src_mute_1", "src_pin_1", "src_pin_2", "src_norm_1"],
        )

        # 非法 mode 拒绝 400
        bad_code, bad_doc = self._mutate({
            "action": "curate_info_source",
            "source_id": "src_bad",
            "title_cn": "测试",
            "mode": "boost_forever",
            "reason_cn": "非法模式",
        })
        self.assertEqual(bad_code, 400)
        self.assertEqual(bad_doc["reason_code"], "INVALID_CURATION_MODE")

    def test_03_d09_future_self_capsules_unlock_and_refuse_without_debt(self) -> None:
        unlock_at = "2026-11-25T09:00:00Z"
        c1, d1 = self._mutate({
            "action": "create_future_capsule",
            "capsule_id": "cap_winter_tea",
            "unlock_after": unlock_at,
            "title_cn": "一周后冬天再看的泡茶便签",
            "note_cn": "到时候如果还想尝试桂花焙茶就打开",
            "artifact_ref": "note:tea_blend_01",
            "shared": False,
        }, now_iso=T0)
        self.assertEqual(c1, 200, d1)
        cap = d1["future_self_capsules"][0]
        self.assertEqual(cap["status"], "LOCKED")
        self.assertFalse(cap["auto_claimed"])
        self.assertEqual(cap["omission_debt"], 0)

        # 未到解锁时间尝试 open -> 409 CAPSULE_LOCKED
        c_early, d_early = self._mutate({
            "action": "resolve_future_capsule",
            "capsule_id": "cap_winter_tea",
            "decision": "open",
        }, now_iso=T1)
        self.assertEqual(c_early, 409)
        self.assertEqual(d_early["reason_code"], "CAPSULE_LOCKED")

        # 时间越过 unlock_after：纯零写读投影变为 AVAILABLE，不自动认领且零亏欠
        snap_before = _snapshot_tree(self.day_root)
        proj = ad_engine.project_v02_agency_depth(
            self.day_root, has_subject_capability=True, now=T_WEEK_LATER
        )
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)
        avail = proj["future_self_capsules"][0]
        self.assertEqual(avail["status"], "AVAILABLE")
        self.assertFalse(avail["auto_claimed"])
        self.assertEqual(avail["omission_debt"], 0)

        # 先 keep_closed，再 decline_no_debt，全程零亏欠债
        c_keep, d_keep = self._mutate({
            "action": "resolve_future_capsule",
            "capsule_id": "cap_winter_tea",
            "decision": "keep_closed",
        }, now_iso=T_WEEK_LATER)
        self.assertEqual(c_keep, 200)
        self.assertEqual(d_keep["future_self_capsules"][0]["status"], "KEPT_CLOSED")
        self.assertEqual(d_keep["future_self_capsules"][0]["omission_debt"], 0)

        c_dec, d_dec = self._mutate({
            "action": "resolve_future_capsule",
            "capsule_id": "cap_winter_tea",
            "decision": "decline_no_debt",
        }, now_iso=T_WEEK_LATER)
        self.assertEqual(c_dec, 200)
        dec_cap = d_dec["future_self_capsules"][0]
        self.assertEqual(dec_cap["status"], "DECLINED")
        self.assertEqual(dec_cap["omission_debt"], 0)
        self.assertFalse(dec_cap["auto_claimed"])

        # 第二只胶囊到时后正式打开
        self._mutate({
            "action": "create_future_capsule",
            "capsule_id": "cap_open_2",
            "unlock_after": unlock_at,
            "title_cn": "可以打开的草图提示",
            "note_cn": "想看再看",
            "shared": True,
        }, now_iso=T0)
        c_open, d_open = self._mutate({
            "action": "resolve_future_capsule",
            "capsule_id": "cap_open_2",
            "decision": "open",
        }, now_iso=T_WEEK_LATER)
        self.assertEqual(c_open, 200)
        opened = next(c for c in d_open["future_self_capsules"] if c["capsule_id"] == "cap_open_2")
        self.assertEqual(opened["status"], "OPENED")
        self.assertEqual(opened["omission_debt"], 0)

        # 重复创建同 capsule_id -> 409；不存在 resolve -> 404；非法 unlock_after -> 400
        c_dup, d_dup = self._mutate({
            "action": "create_future_capsule",
            "capsule_id": "cap_open_2",
            "unlock_after": unlock_at,
            "title_cn": "重复",
            "note_cn": "重复",
        }, now_iso=T0)
        self.assertEqual(c_dup, 409)
        self.assertEqual(d_dup["reason_code"], "CAPSULE_ALREADY_EXISTS")

        c_miss, d_miss = self._mutate({
            "action": "resolve_future_capsule",
            "capsule_id": "cap_missing",
            "decision": "decline_no_debt",
        }, now_iso=T_WEEK_LATER)
        self.assertEqual(c_miss, 404)
        self.assertEqual(d_miss["reason_code"], "CAPSULE_NOT_FOUND")

        c_bad_ts, _ = self._mutate({
            "action": "create_future_capsule",
            "capsule_id": "cap_bad_ts",
            "unlock_after": "not-a-timestamp",
            "title_cn": "坏时间",
            "note_cn": "坏时间",
        }, now_iso=T0)
        self.assertEqual(c_bad_ts, 400)

    def test_04_ch15_s10c_optional_next_gesture_peek_is_not_reclaim_and_no_progress_bars(self) -> None:
        # 超过 80 字符必须被拒绝
        too_long = "看" * 81
        c_long, d_long = self._mutate({
            "action": "set_optional_next_gesture",
            "gesture_id": "ges_long",
            "artifact_ref": "work:sketch_blue",
            "gesture_cn": too_long,
        })
        self.assertEqual(c_long, 400)
        self.assertEqual(d_long["reason_code"], "INVALID_FIELDS")

        c1, d1 = self._mutate({
            "action": "set_optional_next_gesture",
            "gesture_id": "ges_blue",
            "artifact_ref": "work:sketch_blue",
            "gesture_cn": "看看这片蓝色",
            "shared": False,
        })
        self.assertEqual(c1, 200, d1)
        g1 = d1["optional_next_gestures"][0]
        self.assertFalse(g1["claimed"])
        self.assertFalse(g1["activity_resumed"])
        self.assertEqual(g1["peek_count"], 0)

        # 连续 peek 两次：只递增 peek_count，绝不认领或恢复旧活动
        for expected_count in (1, 2):
            cp, dp = self._mutate({
                "action": "peek_optional_next_gesture",
                "gesture_id": "ges_blue",
            }, now_iso=T1)
            self.assertEqual(cp, 200)
            gp = dp["optional_next_gestures"][0]
            self.assertEqual(gp["peek_count"], expected_count)
            self.assertFalse(gp["claimed"])
            self.assertFalse(gp["activity_resumed"])

        # 绝不允许出现完成百分比或红色 deadline 字段
        serialized = json.dumps(dp, ensure_ascii=False)
        for forbidden_key in ("progress_pct", "completion_pct", "red_deadline", "deadline", "overdue_alert"):
            self.assertNotIn(f'"{forbidden_key}"', serialized)

        # reframe_question 缺新问题时拒绝 400；传入则改写问题并继续保持未认领
        c_noq, _ = self._mutate({
            "action": "act_or_archive_gesture",
            "gesture_id": "ges_blue",
            "decision": "reframe_question",
        })
        self.assertEqual(c_noq, 400)

        c_ref, d_ref = self._mutate({
            "action": "act_or_archive_gesture",
            "gesture_id": "ges_blue",
            "decision": "reframe_question",
            "new_question_cn": "换个角度看看傍晚的阴影层次",
        })
        self.assertEqual(c_ref, 200)
        g_ref = d_ref["optional_next_gestures"][0]
        self.assertEqual(g_ref["status"], "REFRAMED")
        self.assertFalse(g_ref["claimed"])
        self.assertFalse(g_ref["activity_resumed"])

        # archive_no_debt 归档无债
        c_arch, d_arch = self._mutate({
            "action": "act_or_archive_gesture",
            "gesture_id": "ges_blue",
            "decision": "archive_no_debt",
        })
        self.assertEqual(c_arch, 200)
        g_arch = d_arch["optional_next_gestures"][0]
        self.assertEqual(g_arch["status"], "ARCHIVED")
        self.assertFalse(g_arch["claimed"])
        self.assertFalse(g_arch["activity_resumed"])
        self.assertEqual(g_arch["omission_debt"], 0)

        # 新建并 adopt_step 才真正认领与恢复
        self._mutate({
            "action": "set_optional_next_gesture",
            "gesture_id": "ges_sound",
            "artifact_ref": "work:rain_loop",
            "gesture_cn": "再听这段声音",
            "shared": True,
        })
        c_adopt, d_adopt = self._mutate({
            "action": "act_or_archive_gesture",
            "gesture_id": "ges_sound",
            "decision": "adopt_step",
        })
        self.assertEqual(c_adopt, 200)
        g_adopt = next(g for g in d_adopt["optional_next_gestures"] if g["gesture_id"] == "ges_sound")
        self.assertEqual(g_adopt["status"], "ADOPTED")
        self.assertTrue(g_adopt["claimed"])
        self.assertTrue(g_adopt["activity_resumed"])

        c_peek_adopted, d_peek_adopted = self._mutate({
            "action": "peek_optional_next_gesture",
            "gesture_id": "ges_sound",
        })
        self.assertEqual(c_peek_adopted, 409)
        self.assertEqual(d_peek_adopted["reason_code"], "GESTURE_ALREADY_ADOPTED")

        c_gmiss, d_gmiss = self._mutate({
            "action": "peek_optional_next_gesture",
            "gesture_id": "ges_missing",
        })
        self.assertEqual(c_gmiss, 404)
        self.assertEqual(d_gmiss["reason_code"], "GESTURE_NOT_FOUND")

    def test_05_d10_s10d_legacy_tool_three_endings_keep_works_readable_zero_stigma(self) -> None:
        cases = (
            ("tool_repaired", "老调色器 v1", "旧色轮插件缺依赖", ["work:a1"], "keep_repaired", None, True, True),
            ("tool_static", "渐变调色脚本", "Python 图像接口已移除", ["work:b2", "work:b3"], "adapt_to_static_card", "card:palette_static_01", False, True),
            ("tool_bye", "旧滤镜小工具", "上游格式不再支持", ["work:c4"], "bid_farewell", None, False, False),
        )
        for tid, title, err, refs, ending, fallback, exp_exec, shared in cases:
            payload: dict[str, Any] = {
                "action": "settle_legacy_tool",
                "tool_id": tid,
                "title_cn": title,
                "dependency_error_cn": err,
                "linked_work_refs": refs,
                "ending": ending,
                "shared": shared,
            }
            if fallback is not None:
                payload["fallback_artifact_ref"] = fallback
            code, doc = self._mutate(payload)
            self.assertEqual(code, 200, doc)
            row = next(t for t in doc["legacy_tool_endings"] if t["tool_id"] == tid)
            self.assertEqual(row["executable"], exp_exec)
            self.assertTrue(row["linked_works_readable"])
            self.assertEqual(row["failure_penalty"], 0)
            self.assertEqual(row["linked_work_refs"], refs)

    def test_06_ch15_s10h_gentle_responsibility_no_red_alert_and_409_handoff(self) -> None:
        # 登记四周未碰的共同承诺 + 私人关切
        c1, d1 = self._mutate({
            "action": "register_gentle_responsibility",
            "resp_id": "resp_shared_booklist",
            "title_cn": "共享秋冬书单整理",
            "target_kind": "shared_partner_commitment",
            "last_touched_at": T0,
            "shared": True,
        }, now_iso=T0)
        self.assertEqual(c1, 200, d1)

        c2, _ = self._mutate({
            "action": "register_gentle_responsibility",
            "resp_id": "resp_priv_mint",
            "title_cn": "窗台薄荷分株小记",
            "target_kind": "private_concern",
            "last_touched_at": T0,
            "shared": False,
        }, now_iso=T0)
        self.assertEqual(c2, 200)

        # 一个月未碰：无红警、无亏欠债
        _, month_doc = ad_view.read_v02_agency_depth_view(
            self.day_root, has_subject_capability=True, now=T_MONTH_LATER
        )
        for row in month_doc["gentle_responsibility_reviews"]:
            self.assertGreaterEqual(row["untouched_days"], 30.0)
            self.assertFalse(row["overdue_alert"])
            self.assertEqual(row["omission_debt"], 0)

        # 共同承诺未获伴侣同意时，单方面 retire_keep_history -> 409 EXTERNAL_COMMITMENT_REQUIRES_HANDOFF
        c_block, d_block = self._mutate({
            "action": "review_gentle_responsibility",
            "resp_id": "resp_shared_booklist",
            "decision": "retire_keep_history",
            "partner_agreed": False,
        }, now_iso=T_MONTH_LATER)
        self.assertEqual(c_block, 409)
        self.assertEqual(d_block["reason_code"], "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF")

        # 缺省 partner_agreed 也视作 False -> 409
        c_block2, d_block2 = self._mutate({
            "action": "review_gentle_responsibility",
            "resp_id": "resp_shared_booklist",
            "decision": "retire_keep_history",
        }, now_iso=T_MONTH_LATER)
        self.assertEqual(c_block2, 409)
        self.assertEqual(d_block2["reason_code"], "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF")

        # 私人关切可以不经伴侣同意直接退役并保留历史
        c_priv_ret, d_priv_ret = self._mutate({
            "action": "review_gentle_responsibility",
            "resp_id": "resp_priv_mint",
            "decision": "retire_keep_history",
            "partner_agreed": False,
        }, now_iso=T_MONTH_LATER)
        self.assertEqual(c_priv_ret, 200, d_priv_ret)
        priv_row = next(r for r in d_priv_ret["gentle_responsibility_reviews"] if r["resp_id"] == "resp_priv_mint")
        self.assertEqual(priv_row["status"], "RETIRED_HISTORY_KEPT")
        self.assertTrue(priv_row["history_preserved"])
        self.assertFalse(priv_row["overdue_alert"])
        self.assertEqual(priv_row["omission_debt"], 0)

        # 共同承诺先做 one_small_step，再 pause_auto_check，最后伴侣同意后退场
        c_step, d_step = self._mutate({
            "action": "review_gentle_responsibility",
            "resp_id": "resp_shared_booklist",
            "decision": "one_small_step",
        }, now_iso=T_MONTH_LATER)
        self.assertEqual(c_step, 200)
        shared_row = next(r for r in d_step["gentle_responsibility_reviews"] if r["resp_id"] == "resp_shared_booklist")
        self.assertEqual(shared_row["status"], "ACTIVE_GENTLE")
        self.assertEqual(shared_row["last_touched_at"], T_MONTH_LATER)

        c_pause, d_pause = self._mutate({
            "action": "review_gentle_responsibility",
            "resp_id": "resp_shared_booklist",
            "decision": "pause_auto_check",
        }, now_iso=T_MONTH_LATER)
        self.assertEqual(c_pause, 200)
        paused_row = next(r for r in d_pause["gentle_responsibility_reviews"] if r["resp_id"] == "resp_shared_booklist")
        self.assertEqual(paused_row["status"], "AUTO_CHECK_PAUSED")
        self.assertTrue(paused_row["auto_check_paused"])

        c_ok, d_ok = self._mutate({
            "action": "review_gentle_responsibility",
            "resp_id": "resp_shared_booklist",
            "decision": "retire_keep_history",
            "partner_agreed": True,
        }, now_iso=T_MONTH_LATER)
        self.assertEqual(c_ok, 200, d_ok)
        done_row = next(r for r in d_ok["gentle_responsibility_reviews"] if r["resp_id"] == "resp_shared_booklist")
        self.assertEqual(done_row["status"], "RETIRED_HISTORY_KEPT")
        self.assertTrue(done_row["partner_agreed"])
        self.assertTrue(done_row["history_preserved"])

    def test_07_partner_privacy_zero_hidden_counts_and_actor_gate_403(self) -> None:
        # 写入各集合私有条目（shared=False）与共享条目（shared=True）
        self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_priv",
            "artifact_ref": "art:priv",
            "spot_id": "desk",
            "cue_text_cn": "SECRET_CUE_TEXT",
            "visible": True,
            "shared": False,
        })
        self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_pub",
            "artifact_ref": "art:pub",
            "spot_id": "window",
            "cue_text_cn": "PUBLIC_CUE_TEXT",
            "visible": True,
            "shared": True,
        })
        self._mutate({
            "action": "curate_info_source",
            "source_id": "src_priv",
            "title_cn": "SECRET_INFO_SOURCE",
            "mode": "pin",
            "reason_cn": "SECRET_INFO_REASON",
            "shared": False,
        })
        self._mutate({
            "action": "create_future_capsule",
            "capsule_id": "cap_priv",
            "unlock_after": T_WEEK_LATER,
            "title_cn": "SECRET_CAPSULE_TITLE",
            "note_cn": "SECRET_CAPSULE_NOTE",
            "shared": False,
        })
        self._mutate({
            "action": "set_optional_next_gesture",
            "gesture_id": "ges_priv",
            "artifact_ref": "art:priv",
            "gesture_cn": "SECRET_GESTURE",
            "shared": False,
        })
        self._mutate({
            "action": "settle_legacy_tool",
            "tool_id": "tool_priv",
            "title_cn": "SECRET_TOOL_TITLE",
            "dependency_error_cn": "SECRET_TOOL_ERR",
            "linked_work_refs": ["SECRET_WORK_REF"],
            "ending": "bid_farewell",
            "shared": False,
        })
        self._mutate({
            "action": "register_gentle_responsibility",
            "resp_id": "resp_priv",
            "title_cn": "SECRET_RESP_TITLE",
            "target_kind": "private_concern",
            "last_touched_at": T0,
            "shared": False,
        })

        # 伴侣视角：全面剥离 shared=False 项，且绝对不暴露隐藏计数
        p_code, p_doc = ad_view.read_v02_agency_depth_view(
            self.day_root, has_subject_capability=False, now_iso=T1
        )
        self.assertEqual(p_code, 200)
        p_blob = json.dumps(p_doc, ensure_ascii=False)
        for secret in (
            "SECRET_CUE_TEXT",
            "SECRET_INFO_SOURCE",
            "SECRET_INFO_REASON",
            "SECRET_CAPSULE_TITLE",
            "SECRET_CAPSULE_NOTE",
            "SECRET_GESTURE",
            "SECRET_TOOL_TITLE",
            "SECRET_TOOL_ERR",
            "SECRET_WORK_REF",
            "SECRET_RESP_TITLE",
        ):
            self.assertNotIn(secret, p_blob)
        self.assertIn("PUBLIC_CUE_TEXT", p_blob)

        for forbidden_count_key in (
            "total_private_count",
            "private_count",
            "hidden_count",
            "withheld_count",
        ):
            self.assertNotIn(forbidden_count_key, p_blob)

        # 背景摘要 include_private=False 也不含私有线索
        ctx_pub = ad_view.summarize_agency_depth_for_context(
            self.day_root, include_private=False, now_iso=T1
        )
        self.assertEqual(len(ctx_pub["active_cues"]), 1)
        self.assertEqual(ctx_pub["active_cues"][0]["cue_id"], "cue_pub")
        self.assertNotIn("SECRET_CUE_TEXT", json.dumps(ctx_pub, ensure_ascii=False))

        # 伴侣越权调用所有 11 个主体动作均返回 403 FORBIDDEN_ACTOR_SIDE
        partner_forbidden_payloads = [
            {"action": "place_memory_cue", "cue_id": "c", "artifact_ref": "a", "spot_id": "s", "cue_text_cn": "t"},
            {"action": "update_placed_cue_visibility", "cue_id": "cue_pub", "occluded": True},
            {"action": "curate_info_source", "source_id": "s", "title_cn": "t", "mode": "mute", "reason_cn": "r"},
            {"action": "create_future_capsule", "capsule_id": "c", "unlock_after": T_WEEK_LATER, "title_cn": "t", "note_cn": "n"},
            {"action": "resolve_future_capsule", "capsule_id": "c", "decision": "decline_no_debt"},
            {"action": "set_optional_next_gesture", "gesture_id": "g", "artifact_ref": "a", "gesture_cn": "看看"},
            {"action": "peek_optional_next_gesture", "gesture_id": "g"},
            {"action": "act_or_archive_gesture", "gesture_id": "g", "decision": "archive_no_debt"},
            {"action": "settle_legacy_tool", "tool_id": "t", "title_cn": "x", "dependency_error_cn": "e", "linked_work_refs": ["w"], "ending": "keep_repaired"},
            {"action": "register_gentle_responsibility", "resp_id": "r", "title_cn": "x", "target_kind": "private_concern", "last_touched_at": T0},
            {"action": "review_gentle_responsibility", "resp_id": "r", "decision": "pause_auto_check"},
        ]
        for pl in partner_forbidden_payloads:
            fc, fdoc = self._mutate(pl, actor_side="partner")
            self.assertEqual(fc, 403, f"Expected 403 for {pl['action']}, got {fc}: {fdoc}")
            self.assertEqual(fdoc["reason_code"], "FORBIDDEN_ACTOR_SIDE")

    def test_08_zero_write_reads_on_missing_empty_and_populated_roots(self) -> None:
        # 1. 缺失根目录
        self.assertFalse(self.day_root.exists())
        ad_engine.project_v02_agency_depth(self.day_root, has_subject_capability=True, now_iso=T0)
        ad_engine.summarize_agency_depth_state_for_context(self.day_root, include_private=True, now_iso=T0)
        ad_view.read_v02_agency_depth_view(self.day_root, has_subject_capability=False, now_iso=T0)
        self.assertFalse(self.day_root.exists())

        # 2. 空目录
        self.day_root.mkdir(parents=True, exist_ok=True)
        empty_snap = _snapshot_tree(self.day_root)
        ad_engine.project_v02_agency_depth(self.day_root, has_subject_capability=False, now_iso=T0)
        ad_engine.summarize_agency_depth_state_for_context(self.day_root, include_private=False, now_iso=T0)
        ad_view.read_v02_agency_depth_view(self.day_root, has_subject_capability=True, now_iso=T0)
        self.assertEqual(_snapshot_tree(self.day_root), empty_snap)

        # 3. 已落盘有数据根目录（覆盖所有新增结构）
        self._mutate({
            "action": "place_memory_cue",
            "cue_id": "cue_z1",
            "artifact_ref": "art:z1",
            "spot_id": "desk",
            "cue_text_cn": "零写检查",
            "shared": True,
        })
        self._mutate({
            "action": "create_future_capsule",
            "capsule_id": "cap_z1",
            "unlock_after": T_WEEK_LATER,
            "title_cn": "胶囊零写检查",
            "note_cn": "无写入",
        })
        pop_snap = _snapshot_tree(self.day_root)
        for cap in (False, True):
            c, _ = ad_view.read_v02_agency_depth_view(
                self.day_root, has_subject_capability=cap, now_iso=T_MONTH_LATER
            )
            self.assertEqual(c, 200)
            ad_engine.project_v02_agency_depth(
                self.day_root, has_subject_capability=cap, now=T_MONTH_LATER
            )
            ad_engine.summarize_agency_depth_state_for_context(
                self.day_root, include_private=cap, now=T_MONTH_LATER
            )
        self.assertEqual(_snapshot_tree(self.day_root), pop_snap)

    def test_09_idempotency_and_concurrent_state_file_lock_consistency(self) -> None:
        # operation_key 幂等重放与冲突拦截
        payload = {
            "action": "set_optional_next_gesture",
            "gesture_id": "ges_idem",
            "artifact_ref": "art:idem",
            "gesture_cn": "看看这片蓝色",
            "operation_key": "ch15-op-1",
        }
        c1, d1 = self._mutate(payload)
        self.assertEqual(c1, 200)
        self.assertEqual(d1["command_status"], "APPLIED")

        c2, d2 = self._mutate(payload)
        self.assertEqual(c2, 200)
        self.assertEqual(d2["command_status"], "DUPLICATE")
        self.assertEqual(d2["revision"], d1["revision"])

        c3, d3 = self._mutate({
            "action": "set_optional_next_gesture",
            "gesture_id": "ges_idem",
            "artifact_ref": "art:idem",
            "gesture_cn": "换个文案冲突",
            "operation_key": "ch15-op-1",
        })
        self.assertEqual(c3, 409)
        self.assertEqual(d3["reason_code"], "OPERATION_KEY_CONFLICT")

        # 并发写保持 state_file_lock 串行一致，无更新丢失
        workers = 6
        barrier = threading.Barrier(workers)
        errors: list[str] = []

        def _worker(idx: int) -> None:
            try:
                barrier.wait(timeout=10)
                code, doc = self._mutate({
                    "action": "place_memory_cue",
                    "cue_id": f"cue_conc_{idx}",
                    "artifact_ref": f"art:{idx}",
                    "spot_id": f"spot_{idx}",
                    "cue_text_cn": f"并发线索 {idx}",
                    "operation_key": f"ch15-conc-{idx}",
                })
                if code != 200:
                    errors.append(f"worker {idx}: {code} {doc}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(f"worker {idx}: {exc!r}")

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(workers)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=15)

        self.assertEqual(errors, [])
        _, final_doc = ad_view.read_v02_agency_depth_view(
            self.day_root, has_subject_capability=True
        )
        self.assertEqual(final_doc["revision"], 1 + workers)
        self.assertEqual(len(final_doc["placed_memory_cues"]["items"]), workers)

    def test_10_hygiene_pure_lf_no_emojis_and_persona_neutral(self) -> None:
        emoji_re = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
        forbidden_tokens = (
            "\u5979",
            "\u5343\u4ee3",
            "Ch" + "iyo",
            "ch" + "iyo",
            "Kaz" + "umi",
            "kaz" + "umi",
            "\u548c\u6cc9",
        )
        owned = (
            Path(ad_engine.__file__),
            Path(ad_view.__file__),
            Path(__file__),
        )
        for path in owned:
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, f"CR byte found in {path}")
            text = raw.decode("utf-8")
            self.assertIsNone(emoji_re.search(text), f"Emoji found in {path}")
            for tok in forbidden_tokens:
                self.assertNotIn(tok, text, f"Forbidden persona token {tok!r} in {path}")


@POSIX_ONLY
class TestV02Ch15PhoneHTTPSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture
        from components.phone import capability as phone_capability

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        cls.subject = "resident_subject"
        cls.cap_token = "v02-ch15-http-cap-token-20261118"
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = cls.subject
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v02-ch15-http-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=cls.cap_token,
            subject=cls.subject,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-18T08:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.server_root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        (self.stack.root / "state" / "v02_agency_depth_state.json").unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 18, 9, 30, tzinfo=timezone(timedelta(hours=8))
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
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_01_http_409_handoff_and_403_partner_gate_and_zero_write_get(self) -> None:
        path = "/api/phone/v02/agency-depth"
        c_reg, r_reg = self._http(
            "POST",
            path,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "http_resp_shared",
                "title_cn": "共同书单整理",
                "target_kind": "shared_partner_commitment",
                "last_touched_at": "2026-10-01T09:00:00Z",
                "shared": True,
            },
            csrf=True,
            capability=self.cap_token,
        )
        self.assertEqual(c_reg, 200, r_reg)

        c_409, r_409 = self._http(
            "POST",
            path,
            {
                "action": "review_gentle_responsibility",
                "resp_id": "http_resp_shared",
                "decision": "retire_keep_history",
                "partner_agreed": False,
            },
            csrf=True,
            capability=self.cap_token,
        )
        self.assertEqual(c_409, 409, r_409)
        self.assertEqual(r_409["reason_code"], "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF")

        c_403, r_403 = self._http(
            "POST",
            path,
            {
                "action": "peek_optional_next_gesture",
                "gesture_id": "g1",
            },
            csrf=True,
        )
        self.assertEqual(c_403, 403, r_403)
        self.assertEqual(r_403["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        snap_before = _snapshot_tree(self.stack.root)
        c_get, r_get = self._http("GET", path, capability=self.cap_token)
        self.assertEqual(c_get, 200, r_get)
        self.assertEqual(_snapshot_tree(self.stack.root), snap_before)
        self.assertEqual(r_get["data"]["llm_calls"], 0)
        self.assertEqual(len(r_get["data"]["gentle_responsibility_reviews"]), 1)


if __name__ == "__main__":
    unittest.main()
