#!/usr/bin/env python3
"""Ch.65 七项“真过一周”验收总标尺 + Ch.109–125 连续七天跨层合流验证（零付费模型调用）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import autonomous_life_cycle as cycle_mod
import v02_antipuppet_scorecard as scorecard_mod
import v02_recall_projection as rp_engine
import v02_runtime_governance_migration as rm_engine
import life_dwell as v03_dwell
import v04_neighborhood_living as nb_mod
import v05_unified_world_fabric as fabric_mod
import world_open_storefront as storefront_mod
import world_phone_shopping as shopping_mod
from chiyo_bundle import lived_context_bridge as bridge_mod


class TestV05Ch65SevenBarsWeekCapstone(unittest.TestCase):
    def test_ch65_all_seven_bars_across_one_continuous_week(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "world").mkdir(parents=True)
            (root / "phone").mkdir(parents=True)
            (root / "world" / "v04_neighborhood_state.json").write_text(
                json.dumps({"environment": {"weather": "shower", "season": "autumn"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "phone" / "home_window.json").write_text(
                json.dumps({"window_state": "wide"}, ensure_ascii=False),
                encoding="utf-8",
            )

            # Bar 1: 3-second glanceable room state with strict zero writes
            before_files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
            snap = fabric_mod.project_unified_world_fabric_zero_write(root, now_iso="2026-11-18T09:15:00+08:00")
            after_files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
            self.assertEqual(before_files, after_files)
            self.assertEqual(snap["sunbeam_zone"], None)  # rain hides sunbeam
            self.assertEqual(snap["window_state"], "wide")
            self.assertEqual(snap["llm_calls"], 0)

            # Bar 2: Outings + quiet blank space over 7 days (56 slots)
            week_sim = fabric_mod.run_v05_week_simulation(legacy_mode=False)
            self.assertEqual(week_sim["total_slots"], 56)
            self.assertLessEqual(week_sim["max_consecutive_note_streak"], 2)
            self.assertGreaterEqual(week_sim["outings_total"], 5)
            self.assertGreaterEqual(week_sim["rest_or_dwell_total"], 10)
            self.assertTrue(any("rest_or_tea" in a for a in week_sim["day_logs"][3]["actions"]))

            # Bar 3: World physics coherence (rain smudge vs drawer, outdoor cooling, passing cart)
            w = fabric_mod.WorldFabric(window_state="wide", weather="shower", hour=9)
            w.add_node(fabric_mod.SpatialNode("home", "place", "小家", None, sheltered=True))
            w.add_node(fabric_mod.SpatialNode("street", "place", "街头", None, sheltered=False))
            w.add_node(fabric_mod.SpatialNode("zone_window", "microzone", "东窗", "home", sheltered=True))
            w.add_node(fabric_mod.SpatialNode("drawer", "container", "抽屉", "zone_window", sheltered=True))
            w.add_node(fabric_mod.SpatialNode("actor", "actor", "住客", "home"))
            w.add_node(fabric_mod.SpatialNode("bag", "container", "布包", "actor", sheltered=False))
            w.add_node(fabric_mod.SpatialNode("loose_note", "object", "散放便签", "zone_window", is_paper=True))
            w.add_node(fabric_mod.SpatialNode("safe_letter", "object", "抽屉信", "drawer", is_paper=True))
            w.add_node(fabric_mod.SpatialNode("hot_tea", "object", "纸杯热茶", "bag", temp_c=70.0, pocket_insulated=False))
            w.add_node(fabric_mod.SpatialNode("pocket_potato", "object", "揣兜红薯", "bag", temp_c=70.0, pocket_insulated=True))
            w.advance_ripple(20)
            self.assertEqual(w.nodes["loose_note"].note_legibility, "smudged")
            self.assertEqual(w.nodes["safe_letter"].note_legibility, "clear")
            w.move_node("actor", "street")
            w.weather = "breezy_cool"
            w.advance_ripple(15)
            self.assertLess(w.nodes["hot_tea"].temp_c, w.nodes["pocket_potato"].temp_c)
            cart = storefront_mod.compute_daily_passing_cart_window("ch65_seed", 2, "autumn")
            self.assertIsNotNone(cart)
            assert cart is not None
            self.assertFalse(cart["outbound_push_allowed"])
            self.assertTrue(storefront_mod.is_passing_cart_active(cart, int(cart["start_minute_of_day"]) + 4))

            # Bar 4: Remote WeChat vs In-Room vs Workbench non-split
            self.assertEqual(bridge_mod.classify_channel_mode("agent:main:weixin:dm:owner"), "REMOTE_PHONE_MESSAGE")
            session_bridge = bridge_mod.RuntimeSessionBridge(active_book_title="《海堤风物记》", active_book_page=7)
            session_bridge.refused_firm_topics.add("peek_private_draft")
            self.assertFalse(
                session_bridge.check_request_allowed("close_window_by_hand", "agent:main:weixin:dm:owner", requires_in_room=True)["allowed"]
            )
            parked = session_bridge.enter_workbench()
            self.assertEqual(parked["parked_bookmark"], ("《海堤风物记》", 7))
            self.assertEqual(
                session_bridge.check_request_allowed("peek_private_draft", "IN_ROOM_TOGETHER")["reason"],
                "REFUSED_FIRM_NON_CAPITULATION",
            )
            with self.assertRaises(ValueError):
                session_bridge.exit_workbench(r"看了 C:\secret\token.txt")
            resumed = session_bridge.exit_workbench("一起排查并修好本地接口超时")
            self.assertEqual(resumed["resumed_bookmark"], ("《海堤风物记》", 7))

            # Bar 5: Clean compute bill & Calm Gate (>= 3 quiet days per week, <= 8 tools, < 6 RMB/mo)
            calm_week = nb_mod.schedule_week_with_calm_gate("ch65_calm", 1)
            self.assertEqual(len(calm_week), 7)
            self.assertGreaterEqual(sum(1 for x in calm_week if x is None), 3)
            for sc in ("indoor_desk", "outdoor_street", "in_room_copresence", "private_intimate", "workbench_collab"):
                self.assertLessEqual(len(bridge_mod.visible_tools_for_scene(sc)), 8)
            monthly = bridge_mod.estimate_monthly_rmb_cost() + fabric_mod.estimate_world_ink_monthly_rmb()["combined_rmb"]
            self.assertLess(monthly, 6.0)

            # Bar 6: Graceful timeout soft landing + honest offline gap + V-C barrier
            landing = cycle_mod.handle_round_timeout_soft_landing(foreground_locked=True)
            self.assertEqual(landing["status"], "IDLE_GLANCE_WINDOW")
            self.assertEqual(landing["llm_retries"], 0)
            self.assertFalse(landing["foreground_locked"])
            gap = v03_dwell.evaluate_host_gap("2026-11-16T08:00:00+08:00", "2026-11-17T20:00:00+08:00")
            self.assertIsNotNone(gap)

            vc_root = root / "vc_instance"
            vc_root.mkdir(parents=True)
            rp_engine.record_episodic_trace(
                vc_root,
                memory_id="mem_ch65",
                topic="old",
                exact_detail="MUST_STAY_TOMBSTONED",
                gist_summary="old gist",
                now_iso="2026-11-12T08:00:00+08:00",
            )
            mem_file = vc_root / "memory" / "v02_recall_projection_state.json"
            backup_bytes = mem_file.read_bytes()
            rp_engine.tombstone_memory(vc_root, memory_id="mem_ch65", reason="user_deleted", now_iso="2026-11-13T08:00:00+08:00")
            seal = dict(rm_engine.read_vc_authority_watermark_readonly(vc_root))
            mem_file.write_bytes(backup_bytes)
            rm_engine.reconcile_backup_restore_vc_barrier(
                vc_root,
                external_watermark_seal=seal,
                operation_key="ch65_vc",
                now_iso="2026-11-18T11:20:00+08:00",
            )
            vis = rp_engine.read_v02_recall_projection_view(vc_root, now_iso="2026-11-18T11:20:00+08:00")
            self.assertNotIn("mem_ch65", [m.get("memory_id") for m in (vis.get("visible_memories") or [])])

            # Bar 7: Day 7 tangible traces (parcel + cat box + habitual slot + corkboard + PNG postcard + 6th dim LOW)
            shopping_mod.place_next_day_parcel_order(root, order_id="ch65_box", item_name_cn="24色水彩盒", current_day=1)
            self.assertEqual(shopping_mod.settle_arrived_entryway_parcels(root, current_day=1, current_hour=18), [])
            self.assertEqual(shopping_mod.settle_arrived_entryway_parcels(root, current_day=2, current_hour=16), ["ch65_box"])
            unpack = shopping_mod.unpack_arrived_entryway_parcel(root, order_id="ch65_box", cat_present=True)
            self.assertTrue(unpack["delivery"]["empty_box_occupied_by_cat"])

            for _ in range(3):
                w.move_node("hot_tea", "zone_window")
            self.assertTrue(w.is_habitual_slot("hot_tea"))

            minimal_png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
            stamped = fabric_mod.embed_postcard_png_chunk(
                minimal_png,
                origin_town="凪浦港町",
                stamp_code="rain_lighthouse",
                gift_slot="wild_mountain_tea",
                FrontNoteCn="海堤散步回来顺手夹进笔记本里的野山茶",
            )
            parsed = fabric_mod.extract_postcard_png_chunk(stamped)
            assert parsed is not None
            self.assertEqual(parsed["gift"], "wild_mountain_tea")

            sixth = scorecard_mod.evaluate_v05_antipuppet_sixth_dim(
                acquired_spot_items=[{"name": "24色水彩盒", "required_place": "home"}],
                visited_places=["home", "bookstore", "street"],
                public_postcards_and_board=["海堤散步回来顺手夹进笔记本里的野山茶"],
                rolling_7d_quiet_days=sum(1 for x in calm_week if x is None),
                min_rare_event_gap_days=15,
                familiarity_ledger={"bookstore_visits": 4},
            )
            self.assertEqual(sixth["risk"], "LOW")
            receipt = scorecard_mod.format_plain_chinese_scorecard_receipt(sixth, item_count=6, quiet_days=4)
            self.assertIn("6 件物品都有真实来处", receipt)


if __name__ == "__main__":
    unittest.main()
