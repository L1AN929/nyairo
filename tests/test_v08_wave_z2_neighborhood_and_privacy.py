#!/usr/bin/env python3
"""Wave Z2A regression & closure suite:
1. Ch.23 ``concern_tinted_shelf`` exposed on ``GET /api/phone/v04/neighborhood``
   (full concern-tinted projection for subject, deep-scrubbed public shelf for partner).
2. Ch.42 & Ch.46 Batch E second independent neighborhood resident ``saeki_aoi``
   (``佐伯葵``, ``街角焙煎咖啡屋店主``, home venue ``cafe``) with non-lockstep 3-hour
   schedule offset from ``温井慎一``, weather/season/market reactions,
   ``public_counter_only`` epistemics, sourced ``hearsay_unverified`` rumors, and
   idempotent ``interact_cafe_resident`` action.
3. Ch.49 Wave X1 partner-privacy closure on ``preserved_work_notes``
   (unshared subject notes scrubbed to ``[]`` with boolean ``preserved_work_notes_withheld``,
   zero hidden count leak, honest ``direction_stage`` / ``offline_gap_notice``).
4. v3.0 GAP-10 / ERR-09 explicit ``PRIVATE`` vs ``EMPTY`` vs ``UNAVAILABLE`` storage
   read contract in ``v08_living_traces_view`` (zero count/mtime leak on private
   drawer, ``UNAVAILABLE`` on corrupt or symlinked ``v08_matter_space.json``).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "scripts"),
    str(REPO_ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v04_elastic_play_learning as ep_engine  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402
from components.phone import v08_living_traces_view as traces_view  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    PARTNER_ALLOWED_ACTIONS,
    SUBJECT_ALLOWED_ACTIONS,
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as nb_engine  # noqa: E402
from components.world.scripts import v08_universal_matter as matter_engine  # noqa: E402

FORBIDDEN_PRONOUN = chr(0x5979)
FORBIDDEN_NAME = chr(0x5343) + chr(0x4EE3)


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
        elif path.is_symlink():
            snap[rel] = (0, int(st.st_mtime_ns), "symlink")
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class WaveZ2NeighborhoodAndPrivacyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-wave-z2a-")
        self.base_dir = Path(self._tmp.name)
        self.day_root = self.base_dir / "instance"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # =========================================================================
    # 1. Ch.23 concern_tinted_shelf on GET /api/phone/v04/neighborhood
    # =========================================================================

    def test_01_empty_and_populated_get_concern_tinted_shelf_is_zero_write(self) -> None:
        self.assertFalse(self.day_root.exists())

        code_s, body_s = read_v04_neighborhood_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-17T11:00:00+08:00",
        )
        code_p, body_p = read_v04_neighborhood_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-17T11:00:00+08:00",
        )
        self.assertFalse(self.day_root.exists(), "GET on uncreated root must create 0 bytes")
        self.assertEqual(code_s, 200)
        self.assertEqual(code_p, 200)
        self.assertEqual(body_s["llm_calls"], 0)
        self.assertEqual(body_p["llm_calls"], 0)

        shelf_s = body_s["data"]["concern_tinted_shelf"]
        shelf_p = body_p["data"]["concern_tinted_shelf"]
        self.assertEqual(shelf_s, body_s["concern_tinted_shelf"])
        self.assertEqual(shelf_p, body_p["concern_tinted_shelf"])
        self.assertTrue(shelf_s["zero_write"])
        self.assertTrue(shelf_p["zero_write"])
        self.assertIs(shelf_s["private_concerns_scrubbed"], False)
        self.assertIs(shelf_p["private_concerns_scrubbed"], True)
        self.assertEqual(shelf_s["noticed_items"], [])
        self.assertEqual(shelf_p["noticed_items"], [])
        self.assertEqual(len(shelf_p["public_shelf_books"]), 3)

    def test_02_subject_concern_tinted_shelf_vs_partner_deep_scrub_zero_leak(self) -> None:
        # Baseline partner shelf on a populated root with a public display card, 0 concerns
        t_open = "2026-11-17T11:00:00+08:00"
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": "card_pub_1"},
            actor_side="subject",
            now_iso=t_open,
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "card_pub_1",
                "title": "纸艺展签",
                "text": "风琴折页留半毫米余量更稳妥。",
                "draft_text": "私人底稿不公开",
            },
            actor_side="subject",
            now_iso=t_open,
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "submit_display_card",
                "card_id": "card_pub_1",
            },
            actor_side="subject",
            now_iso=t_open,
        )

        _, partner_before_concerns = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=False, now_iso=t_open
        )
        baseline_partner_shelf = partner_before_concerns["data"]["concern_tinted_shelf"]

        # Seed multiple private inner concerns across stores
        secret_q = "十字隔板折边偏紧怎么修补"
        secret_kw = "河边长椅水声"
        secret_inq = "常春藤门廊散射光储水"

        ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "direction": secret_q,
                "current_focus": secret_q,
            },
            actor_side="subject",
            now_iso=t_open,
        )
        echoes_path = self.day_root / "phone" / "v08_emergent_echoes.json"
        echoes_path.parent.mkdir(parents=True, exist_ok=True)
        echoes_path.write_text(
            json.dumps(
                {
                    "future_self_questions": [
                        {
                            "question_id": "q_priv_river_01",
                            "status": "open",
                            "question_cn": secret_kw,
                            "keywords": ["河边", "长椅"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        agency_path = self.day_root / "state" / "v02_agency_depth_state.json"
        agency_path.parent.mkdir(parents=True, exist_ok=True)
        agency_path.write_text(
            json.dumps(
                {
                    "inquiries": [
                        {
                            "inquiry_id": "inq_priv_fern_01",
                            "status": "INVESTIGATING",
                            "shared": False,
                            "question": secret_inq,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        snap_before = _snapshot_tree(self.day_root)
        code_s, view_s = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso=t_open
        )
        code_p, view_p = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=False, now_iso=t_open
        )
        snap_after = _snapshot_tree(self.day_root)
        self.assertEqual(snap_before, snap_after, "populated GET must be 100% zero-write")
        self.assertEqual(code_s, 200)
        self.assertEqual(code_p, 200)

        shelf_s = view_s["data"]["concern_tinted_shelf"]
        self.assertIs(shelf_s["private_concerns_scrubbed"], False)
        self.assertGreaterEqual(len(shelf_s["active_concerns"]), 3)
        noticed_ids = {row["book_id"] for row in shelf_s["noticed_items"]}
        self.assertIn("book_paper_joinery", noticed_ids)
        self.assertIn("book_river_sketches", noticed_ids)
        self.assertIn("book_botanical_plates", noticed_ids)
        for row in shelf_s["noticed_items"]:
            self.assertIs(row["in_range"], True)
            self.assertIs(row["noticed"], True)
            self.assertIs(row["understood"], False)
            self.assertIs(row["full_text_dumped"], False)

        # Partner default view: deep-scrubbed, zero private concern text/id/count leak!
        shelf_p = view_p["data"]["concern_tinted_shelf"]
        self.assertIs(shelf_p["private_concerns_scrubbed"], True)
        self.assertEqual(shelf_p["active_concerns"], [])
        self.assertEqual(shelf_p["noticed_items"], [])
        self.assertEqual(shelf_p["tinted_books"], [])
        self.assertEqual(shelf_p["focus_question"], "")
        self.assertEqual(shelf_p["concern_keywords"], [])
        self.assertEqual(len(shelf_p["untinted_books"]), 3)
        self.assertEqual(shelf_p["public_display_cards"][0]["displayed_text"], "风琴折页留半毫米余量更稳妥。")
        # Exact structural & content equality with baseline before any private concerns existed!
        self.assertEqual(shelf_p, baseline_partner_shelf)

        partner_blob = json.dumps(view_p, ensure_ascii=False)
        for secret in (secret_q, secret_kw, secret_inq, "q_priv_river_01", "inq_priv_fern_01", "私人底稿不公开"):
            self.assertNotIn(secret, partner_blob)

    # =========================================================================
    # 2. Ch.42 & Ch.46 Batch E Second Independent Neighborhood Resident
    # =========================================================================

    def test_03_second_resident_independent_schedule_and_environment_reactions(self) -> None:
        # Prove non-lockstep phase transitions vs bookstore shopkeeper Nukui Shinichi:
        # 10:30 -> 11:30: Saeki Aoi rotates (morning_roast -> counter_pour_over), Nukui stays in 09-12 slot!
        _, v_1030 = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-17T10:30:00+08:00"
        )
        _, v_1130 = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-17T11:30:00+08:00"
        )
        _, v_1230 = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-17T12:30:00+08:00"
        )
        self.assertFalse(self.day_root.exists(), "schedule projections must stay 100% zero-write")

        aoi_1030 = v_1030["data"]["second_resident"]
        nukui_1030 = v_1030["data"]["bookstore"]["shopkeeper_life"]
        self.assertEqual(aoi_1030["resident_id"], "saeki_aoi")
        self.assertEqual(aoi_1030["resident_name"], "佐伯葵")
        self.assertEqual(aoi_1030["role"], "街角焙煎咖啡屋店主")
        self.assertEqual(aoi_1030["home_venue"], "cafe")
        self.assertEqual(len(v_1030["data"]["independent_residents"]), 2)
        self.assertEqual(v_1030["data"]["independent_residents"][0]["shopkeeper_name"], "温井慎一")
        self.assertEqual(v_1030["data"]["independent_residents"][1]["resident_id"], "saeki_aoi")

        # 10:30 -> 11:30: Aoi changes phase, Nukui does NOT
        self.assertEqual(aoi_1030["current_phase"], "morning_roast")
        self.assertEqual(v_1130["data"]["second_resident"]["current_phase"], "counter_pour_over")
        self.assertEqual(
            nukui_1030["current_task"],
            v_1130["data"]["bookstore"]["shopkeeper_life"]["current_task"],
        )

        # 11:30 -> 12:30: Nukui changes task, Aoi does NOT
        self.assertNotEqual(
            v_1130["data"]["bookstore"]["shopkeeper_life"]["current_task"],
            v_1230["data"]["bookstore"]["shopkeeper_life"]["current_task"],
        )
        self.assertEqual(
            v_1130["data"]["second_resident"]["current_phase"],
            v_1230["data"]["second_resident"]["current_phase"],
        )

        # Verify all 5 distinct phases across the day
        expected_phases = [
            ("2026-11-17T09:30:00+08:00", "morning_roast", True),
            ("2026-11-17T11:30:00+08:00", "counter_pour_over", True),
            ("2026-11-17T15:00:00+08:00", "stepped_out_errand", False),
            ("2026-11-17T18:30:00+08:00", "evening_cleanup", True),
            ("2026-11-17T22:30:00+08:00", "closed_rest", False),
        ]
        for ts, exp_phase, exp_at_counter in expected_phases:
            _, view_ts = read_v04_neighborhood_view(self.day_root, now_iso=ts)
            res = view_ts["data"]["second_resident"]
            self.assertEqual(res["current_phase"], exp_phase, ts)
            self.assertIs(res["at_counter"], exp_at_counter, ts)
            self.assertEqual(res["privilege_boundary"], "public_counter_only")
            self.assertIs(res["private_room_knowledge"], False)
            self.assertIsNone(res["favorability_score"])
            self.assertEqual(res["omission_debt"], 0)

        # Weather & seasonal market day reactions (12th of month 10:00-18:00 is market day)
        t_market = "2026-11-12T11:30:00+08:00"
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "set_local_weather", "weather": "rain", "season": "winter"},
            actor_side="partner",
            now_iso=t_market,
        )
        _, v_rain_market = read_v04_neighborhood_view(self.day_root, now_iso=t_market)
        aoi_rm = v_rain_market["data"]["second_resident"]
        self.assertIn("雨", aoi_rm["weather_reaction_cn"])
        self.assertIn("手冲", aoi_rm["market_reaction_cn"])
        self.assertIn("冬", aoi_rm["season_reaction_cn"])

    def test_04_interact_cafe_resident_coaster_rumor_idempotency_and_rbac(self) -> None:
        self.assertEqual(len(PARTNER_ALLOWED_ACTIONS), 6)
        self.assertEqual(len(SUBJECT_ALLOWED_ACTIONS), 12)

        t_counter = "2026-11-17T11:30:00+08:00"
        t_stepped_out = "2026-11-17T15:00:00+08:00"

        # 1. Partner forbidden (403 FORBIDDEN_ACTOR_SIDE)
        c403, r403 = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "interact_cafe_resident", "operation": "check_neighbor_schedule"},
            actor_side="partner",
            now_iso=t_counter,
        )
        self.assertEqual(c403, 403)
        self.assertEqual(r403["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # 2. Forbidden score/identity/unknown fields rejected (400 INVALID_FIELDS)
        for bad in (
            {"action": "interact_cafe_resident", "coaster_note": "好喝", "favorability": 10},
            {"action": "interact_cafe_resident", "coaster_note": "好喝", "affection_score": 5},
            {"action": "interact_cafe_resident", "coaster_note": "好喝", "uid": "root"},
            {"action": "interact_cafe_resident", "coaster_note": "好喝", "unknown_xyz": 1},
        ):
            c400, r400 = mutate_v04_neighborhood_view(
                self.day_root, bad, actor_side="subject", now_iso=t_counter
            )
            self.assertEqual(c400, 400, bad)
            self.assertEqual(r400["reason_code"], "INVALID_FIELDS")

        # 3. Coaster note while at counter -> received_in_person=True + operation_key idempotency
        coaster_cmd = {
            "action": "interact_cafe_resident",
            "operation": "leave_cafe_coaster_note",
            "coaster_note": "浅烘豆香气很清爽，下次再来试手冲。",
            "operation_key": "op-cafe-coaster-1",
        }
        c1, r1 = mutate_v04_neighborhood_view(
            self.day_root, coaster_cmd, actor_side="subject", now_iso=t_counter
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["status"], "applied")
        self.assertIs(r1["result"]["received_in_person"], True)
        self.assertIsNone(r1["result"]["favorability_score"])
        self.assertEqual(r1["result"]["omission_debt"], 0)

        # Idempotent replay
        c1_dup, r1_dup = mutate_v04_neighborhood_view(
            self.day_root, coaster_cmd, actor_side="subject", now_iso=t_counter
        )
        self.assertEqual(c1_dup, 200)
        self.assertEqual(r1_dup["status"], "already_applied")
        self.assertEqual(len(r1_dup["second_resident"]["coaster_notes"]), 1)

        # Conflicting payload on same operation_key -> 409 OPERATION_KEY_CONFLICT
        c_conf, r_conf = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "interact_cafe_resident",
                "operation": "leave_cafe_coaster_note",
                "coaster_note": "换一句不同内容",
                "operation_key": "op-cafe-coaster-1",
            },
            actor_side="subject",
            now_iso=t_counter,
        )
        self.assertEqual(c_conf, 409)
        self.assertEqual(r_conf["reason_code"], "OPERATION_KEY_CONFLICT")

        # 4. Coaster note while stepped_out_errand (15:00) -> never fakes received_in_person!
        c_out, r_out = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "interact_cafe_resident",
                "operation": "leave_cafe_coaster_note",
                "coaster_note": "路过看到出去跑腿了，便签压在木托盘边。",
            },
            actor_side="subject",
            now_iso=t_stepped_out,
        )
        self.assertEqual(c_out, 200)
        self.assertIs(r_out["result"]["received_in_person"], False)
        self.assertEqual(r_out["result"]["current_phase"], "stepped_out_errand")

        # 5. Shared errand board + sourced rumor (hearsay_unverified until visited firsthand!)
        c_err_b, r_err_b = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "interact_cafe_resident",
                "operation": "post_shared_errand",
                "errand_note": "谁顺路去街角共享架可以捎一把裁纸尺参考尺寸",
            },
            actor_side="subject",
            now_iso=t_counter,
        )
        self.assertEqual(c_err_b, 200)
        self.assertEqual(r_err_b["result"]["omission_debt"], 0)

        c_rum, r_rum = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "interact_cafe_resident",
                "operation": "share_cafe_rumor",
                "route_id": "street_to_riverside",
                "claim_cn": "听说河边步道入口堆了修剪下来的树枝",
            },
            actor_side="subject",
            now_iso=t_counter,
        )
        self.assertEqual(c_rum, 200)
        self.assertEqual(r_rum["result"]["source_resident_id"], "saeki_aoi")
        self.assertEqual(r_rum["result"]["source_name"], "佐伯葵")
        self.assertEqual(r_rum["result"]["epistemic_status"], "hearsay_unverified")
        self.assertIs(r_rum["result"]["verified_firsthand"], False)
        self.assertEqual(r_rum["journey"]["discovered_roadblocks"], [])

        # Walk to riverside firsthand (route is physically open) -> refutes hearsay firsthand!
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "riverside"},
            actor_side="subject",
            now_iso="2026-11-17T15:10:00+08:00",
        )
        _, walked = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-17T15:30:00+08:00",
        )
        cafe_rumor = walked["second_resident"]["heard_rumors"][0]
        self.assertIs(cafe_rumor["verified_firsthand"], True)
        self.assertEqual(cafe_rumor["epistemic_status"], "refuted_firsthand")

    # =========================================================================
    # 3. Ch.49 Wave X1 Partner-Privacy Closure on preserved_work_notes
    # =========================================================================

    def test_05_preserved_work_notes_scrubbed_for_partner_without_count_leak(self) -> None:
        ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "direction": "上午整理手稿第二章",
                "optional_directions": ["上午整理手稿第二章", "傍晚散步"],
            },
            actor_side="subject",
            now_iso="2026-11-17T09:00:00+08:00",
        )
        ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "start_step",
                "active_step_ref": "ch2-opening",
            },
            actor_side="subject",
            now_iso="2026-11-17T09:20:00+08:00",
        )
        secret_note_1 = "私密半截手稿一：不想被提前看见的句子"
        ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "interrupt",
                "interrupt_reason": "临时停笔喝水",
                "preserve_draft_note": secret_note_1,
            },
            actor_side="subject",
            now_iso="2026-11-17T09:40:00+08:00",
        )
        _, p_after_one = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-17T10:30:00+08:00"
        )

        # Add two more private notes + offline_pause; partner view must leak neither text nor count!
        secret_note_2 = "私密半截手稿二：第二段未定稿"
        secret_note_3 = "私密半截手稿三：结尾备选草句"
        for idx, note in enumerate((secret_note_2, secret_note_3), start=1):
            ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {
                    "action": "set_day_direction",
                    "mode": "interrupt",
                    "interrupt_reason": f"再次停笔 {idx}",
                    "preserve_draft_note": note,
                },
                actor_side="subject",
                now_iso=f"2026-11-17T09:5{idx}:00+08:00",
            )

        # Compare partner withheld representation with 1 vs 3 private notes
        _, p_after_three = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-17T10:30:00+08:00"
        )
        self.assertEqual(p_after_one["data"]["day_direction"]["preserved_work_notes"], [])
        self.assertEqual(p_after_three["data"]["day_direction"]["preserved_work_notes"], [])
        self.assertIs(p_after_one["data"]["day_direction"]["preserved_work_notes_withheld"], True)
        self.assertIs(p_after_three["data"]["day_direction"]["preserved_work_notes_withheld"], True)

        # Offline pause keeps honest non-guilt stage & notice visible to partner while notes stay hidden
        ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "offline_pause",
                "offline_gap_hours": 3.5,
            },
            actor_side="subject",
            now_iso="2026-11-17T13:30:00+08:00",
        )

        snap_before = _snapshot_tree(self.day_root)
        _, s_view = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-17T13:35:00+08:00"
        )
        _, p_view = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-17T13:35:00+08:00"
        )
        self.assertEqual(snap_before, _snapshot_tree(self.day_root), "elastic GET must be zero-write")

        s_dd = s_view["data"]["day_direction"]
        p_dd = p_view["data"]["day_direction"]
        self.assertEqual(len(s_dd["preserved_work_notes"]), 3)
        self.assertIs(s_dd["preserved_work_notes_withheld"], False)

        self.assertEqual(p_dd["preserved_work_notes"], [])
        self.assertIs(p_dd["preserved_work_notes_withheld"], True)
        self.assertEqual(p_dd["direction_stage"], "offline_paused")
        self.assertIn("没有连着发生", p_dd["offline_gap_notice"])
        self.assertEqual(p_dd["guilt_debt"], 0)
        self.assertIs(p_dd["backfilled_to_diary"], False)

        p_blob = json.dumps(p_view, ensure_ascii=False)
        for secret in (secret_note_1, secret_note_2, secret_note_3):
            self.assertNotIn(secret, p_blob)

        # Explicitly shared note via engine command is allowed through while private ones stay scrubbed
        ep_engine.apply_elastic_play_mutation(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "interrupt",
                "interrupt_reason": "把公开进度留个便条",
                "preserve_draft_note": "公开告知：第二章小标题定好了",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-17T14:00:00+08:00",
        )
        _, p_shared = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-17T14:05:00+08:00"
        )
        p_notes = p_shared["data"]["day_direction"]["preserved_work_notes"]
        self.assertEqual(len(p_notes), 1)
        self.assertEqual(p_notes[0]["note"], "公开告知：第二章小标题定好了")
        self.assertIs(p_shared["data"]["day_direction"]["preserved_work_notes_withheld"], True)

    # =========================================================================
    # 4. GAP-10 / ERR-09 Explicit PRIVATE vs EMPTY vs UNAVAILABLE Storage Contract
    # =========================================================================

    def test_06_living_traces_private_vs_empty_vs_populated_zero_count_or_mtime_leak(self) -> None:
        t0 = "2026-11-17T09:00:00+08:00"
        self.assertFalse(self.day_root.exists())

        code_p0, body_p0 = traces_view.read_v08_living_traces_view(
            self.day_root, has_subject_capability=False, now_iso=t0
        )
        code_s0, body_s0 = traces_view.read_v08_living_traces_view(
            self.day_root, has_subject_capability=True, now_iso=t0
        )
        self.assertFalse(self.day_root.exists(), "GET on absent root must be 100% zero-write")
        self.assertEqual(code_p0, 200)
        self.assertEqual(code_s0, 200)
        self.assertEqual(body_p0["data"]["matter_space_read_status"], "OK")

        # Partner zones still omit subject_private_drawer, while zone_boundary_states marks it PRIVATE
        p0_space = body_p0["data"]["matter_space"]
        self.assertNotIn("subject_private_drawer", {z["zone_id"] for z in p0_space["zones"]})
        for z in p0_space["zones"]:
            self.assertIn(z["storage_status"], ("EMPTY", "POPULATED"))
            self.assertTrue(z["storage_status_cn"])
            self.assertIsInstance(z["visible_item_count"], int)

        p0_bounds = {b["zone_id"]: b for b in body_p0["data"]["zone_boundary_states"]}
        s0_bounds = {b["zone_id"]: b for b in body_s0["data"]["zone_boundary_states"]}
        self.assertEqual(p0_bounds["dining_table"]["storage_status"], "EMPTY")
        self.assertEqual(p0_bounds["dining_table"]["item_count"], 0)
        self.assertEqual(p0_bounds["home_desk"]["storage_status"], "POPULATED")
        self.assertGreater(p0_bounds["home_desk"]["item_count"], 0)

        # Private drawer for partner vs subject when empty
        drawer_partner_before = p0_bounds["subject_private_drawer"]
        self.assertEqual(drawer_partner_before["storage_status"], "PRIVATE")
        self.assertEqual(drawer_partner_before["storage_status_cn"], "私人抽屉 · 不对外展开")
        self.assertIsNone(drawer_partner_before["item_count"])
        self.assertIsNone(drawer_partner_before["visible_item_count"])
        self.assertIsNone(drawer_partner_before["updated_at"])

        self.assertEqual(s0_bounds["subject_private_drawer"]["storage_status"], "EMPTY")
        self.assertEqual(s0_bounds["subject_private_drawer"]["item_count"], 0)

        # Subject places 2 private items into subject_private_drawer at a later timestamp
        matter_engine.compose_open_act(
            self.day_root,
            {
                "intent_cn": "把私密信笺与旧钥匙收进私人小抽屉",
                "steps": [
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "priv_letter_01",
                        "label_cn": "未寄出的私密信笺",
                        "item_kind": "paper",
                        "target_parent_id": "subject_private_drawer",
                        "paper_like": True,
                    },
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "priv_key_01",
                        "label_cn": "旧木匣小铜匙",
                        "item_kind": "keepsake",
                        "target_parent_id": "subject_private_drawer",
                    },
                ],
            },
            now_iso="2026-11-17T10:15:00+08:00",
        )

        snap_before = _snapshot_tree(self.day_root)
        _, body_p1 = traces_view.read_v08_living_traces_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-17T10:20:00+08:00"
        )
        _, body_s1 = traces_view.read_v08_living_traces_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-17T10:20:00+08:00"
        )
        self.assertEqual(snap_before, _snapshot_tree(self.day_root), "populated GET must be zero-write")

        p1_bounds = {b["zone_id"]: b for b in body_p1["data"]["zone_boundary_states"]}
        s1_bounds = {b["zone_id"]: b for b in body_s1["data"]["zone_boundary_states"]}

        # Partner's view of subject_private_drawer is 100% byte-identical before & after!
        self.assertEqual(p1_bounds["subject_private_drawer"], drawer_partner_before)
        p1_blob = json.dumps(body_p1, ensure_ascii=False)
        self.assertNotIn("未寄出的私密信笺", p1_blob)
        self.assertNotIn("旧木匣小铜匙", p1_blob)
        self.assertNotIn("hidden_count", p1_blob)

        # Subject sees their own drawer as POPULATED with item_count == 2
        self.assertEqual(s1_bounds["subject_private_drawer"]["storage_status"], "POPULATED")
        self.assertEqual(s1_bounds["subject_private_drawer"]["item_count"], 2)

    def test_07_corrupt_or_symlink_matter_file_surfaces_unavailable_never_empty(self) -> None:
        matter_file = self.day_root / "world" / "v08_matter_space.json"
        matter_file.parent.mkdir(parents=True, exist_ok=True)
        corrupt_text = "{corrupt-matter-space-do-not-pretend-empty"
        matter_file.write_text(corrupt_text, encoding="utf-8")

        snap_before = _snapshot_tree(self.day_root)
        code_p, body_p = traces_view.read_v08_living_traces_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-17T11:00:00+08:00"
        )
        code_s, body_s = traces_view.read_v08_living_traces_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-17T11:00:00+08:00"
        )
        self.assertEqual(snap_before, _snapshot_tree(self.day_root), "corrupt GET must not overwrite file")
        self.assertEqual(code_p, 200)
        self.assertEqual(code_s, 200)

        for body in (body_p, body_s):
            self.assertEqual(body["matter_space_read_status"], "UNAVAILABLE")
            self.assertEqual(body["data"]["matter_space_read_status"], "UNAVAILABLE")
            space = body["data"]["matter_space"]
            self.assertEqual(space["matter_space_read_status"], "UNAVAILABLE")
            self.assertEqual(space["storage_status"], "UNAVAILABLE")
            self.assertEqual(space["storage_status_cn"], "暂不可读 · 绝不冒充空置")
            self.assertTrue(body["data"]["zone_boundary_states"])
            for b in body["data"]["zone_boundary_states"]:
                self.assertEqual(b["storage_status"], "UNAVAILABLE")
                self.assertEqual(b["storage_status_cn"], "暂不可读 · 绝不冒充空置")
                self.assertIsNone(b["item_count"])

        if hasattr(os, "symlink") and os.name == "posix":
            matter_file.unlink()
            outside = self.base_dir / "outside_matter.json"
            outside.write_text("{}", encoding="utf-8")
            os.symlink(outside, matter_file)
            _, sym_body = traces_view.read_v08_living_traces_view(
                self.day_root, has_subject_capability=True, now_iso="2026-11-17T11:00:00+08:00"
            )
            self.assertEqual(sym_body["data"]["matter_space_read_status"], "UNAVAILABLE")

    # =========================================================================
    # 5. Source Hygiene: Pure LF, Zero Hardcoded Persona Leaks, Zero Emojis
    # =========================================================================

    def test_08_wave_z2a_files_pure_lf_and_persona_neutral(self) -> None:
        all_five = (
            "components/world/scripts/v04_neighborhood_living.py",
            "components/phone/v04_neighborhood_view.py",
            "components/life/scripts/v04_elastic_play_learning.py",
            "components/phone/v08_living_traces_view.py",
            "tests/test_v08_wave_z2_neighborhood_and_privacy.py",
        )
        strict_pronoun_files = (
            "components/world/scripts/v04_neighborhood_living.py",
            "components/phone/v04_neighborhood_view.py",
            "components/phone/v08_living_traces_view.py",
            "tests/test_v08_wave_z2_neighborhood_and_privacy.py",
        )
        for rel in all_five:
            path = REPO_ROOT / rel
            raw = path.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF line ending found in {rel}")
            text = raw.decode("utf-8")
            self.assertNotIn(FORBIDDEN_NAME, text, f"Hardcoded name found in {rel}")
            if rel in strict_pronoun_files:
                self.assertNotIn(FORBIDDEN_PRONOUN, text, f"Hardcoded pronoun found in {rel}")
            for ch in text:
                cp = ord(ch)
                is_emoji = (
                    0x1F300 <= cp <= 0x1FAFF
                    or 0x2600 <= cp <= 0x27BF
                    or 0xFE00 <= cp <= 0xFE0F
                )
                self.assertFalse(is_emoji, f"Emoji U+{cp:04X} found in {rel}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
