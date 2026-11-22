#!/usr/bin/env python3
"""v0.5 Step 1 验收测试：统一空间树与微气候涟漪、防卧室打转节律、7天56时段回放、动态工具收窄与灵光巧思物理。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import autonomous_life_cycle as cycle_mod
import candidate_sources_ag0 as ag0_mod
import v02_v03_daily_loop_bridge as loop_bridge
import v03_clock_evaporation as clock_mod
import v05_unified_world_fabric as fabric_mod
import world_move_opportunity_source as world_opp_mod
from chiyo_bundle import lived_context_bridge as context_bridge


def _sample_world() -> fabric_mod.WorldFabric:
    w = fabric_mod.WorldFabric()
    w.add_node(fabric_mod.SpatialNode("home", "place", "小家", None, sheltered=True))
    w.add_node(fabric_mod.SpatialNode("street", "place", "室外老街", None, sheltered=False))
    w.add_node(fabric_mod.SpatialNode("zone_window", "microzone", "东窗微区", "home", sheltered=True))
    w.add_node(fabric_mod.SpatialNode("zone_sofa", "microzone", "沙发角", "home", sheltered=True))
    w.add_node(fabric_mod.SpatialNode("closed_drawer", "container", "带盖抽屉", "zone_window", sheltered=True))
    w.add_node(fabric_mod.SpatialNode("actor", "actor", "住客", "home"))
    w.add_node(fabric_mod.SpatialNode("canvas_bag", "container", "随身帆布包", "actor", sheltered=False))
    w.add_node(fabric_mod.SpatialNode("desk_note", "object", "桌角散装便签", "zone_window", is_paper=True))
    w.add_node(fabric_mod.SpatialNode("drawer_letter", "object", "抽屉里的信", "closed_drawer", is_paper=True))
    w.add_node(fabric_mod.SpatialNode("mint_plant", "object", "窗台薄荷", "zone_window", moisture_pct=45))
    w.add_node(fabric_mod.SpatialNode("hot_tea", "object", "纸杯热茶", "canvas_bag", temp_c=72.0, pocket_insulated=False))
    w.add_node(fabric_mod.SpatialNode("pocket_potato", "object", "大衣兜烤红薯", "canvas_bag", temp_c=72.0, pocket_insulated=True))
    return w


class TestV05Step1WorldFabricAndAntiLoop(unittest.TestCase):
    # --- V05-01 ~ V05-08: 统一空间树与微气候涟漪 ---
    def test_v05_01_carrier_bag_moves_with_actor_zero_child_rewrite(self) -> None:
        w = _sample_world()
        self.assertEqual(w.effective_place("hot_tea"), "home")
        w.move_node("actor", "street")
        self.assertEqual(w.effective_place("hot_tea"), "street")
        self.assertEqual(w.nodes["hot_tea"].parent_id, "canvas_bag")

    def test_v05_02_cycle_move_rejected(self) -> None:
        w = _sample_world()
        with self.assertRaises(ValueError):
            w.move_node("home", "canvas_bag")

    def test_v05_03_wide_window_shower_smudges_loose_note_but_protects_drawer(self) -> None:
        w = _sample_world()
        w.window_state = "wide"
        w.weather = "shower"
        hook_res = clock_mod.advance_v05_fabric_ripple_hook(fabric=w, minutes=20)
        self.assertEqual(w.nodes["desk_note"].note_legibility, "smudged")
        self.assertEqual(w.nodes["drawer_letter"].note_legibility, "clear")
        self.assertGreater(w.nodes["mint_plant"].moisture_pct, 45)
        self.assertIn("desk_note", hook_res["changed_objects"])

    def test_v05_04_closed_window_blocks_rain_smudge(self) -> None:
        w = _sample_world()
        w.window_state = "closed"
        w.weather = "shower"
        w.advance_ripple(20)
        self.assertEqual(w.nodes["desk_note"].note_legibility, "clear")

    def test_v05_05_outdoor_cold_cools_uninsulated_drink_faster_than_pocket(self) -> None:
        w = _sample_world()
        w.move_node("actor", "street")
        w.weather = "breezy_cool"
        w.advance_ripple(15)
        self.assertLess(w.nodes["hot_tea"].temp_c, w.nodes["pocket_potato"].temp_c)

    def test_v05_06_sunbeam_migrates_by_hour_and_hides_in_rain(self) -> None:
        w = _sample_world()
        w.hour = 9
        self.assertEqual(w.active_sunbeam_zone(), "zone_window")
        w.hour = 14
        self.assertEqual(w.active_sunbeam_zone(), "zone_sofa")
        w.weather = "shower"
        self.assertIsNone(w.active_sunbeam_zone())

    def test_v05_07_thunder_blackout_auto_lights_nightlight_at_night(self) -> None:
        w = _sample_world()
        w.weather = "thunder_blackout"
        w.hour = 20
        self.assertTrue(w.nightlight_active())

    def test_v05_08_habitual_slot_forms_after_three_returns(self) -> None:
        w = _sample_world()
        for _ in range(3):
            w.move_node("hot_tea", "zone_window")
        self.assertTrue(w.is_habitual_slot("hot_tea"))

    def test_zero_write_facade_never_writes_day_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "world").mkdir(parents=True)
            (root / "phone").mkdir(parents=True)
            nb_file = root / "world" / "v04_neighborhood_state.json"
            win_file = root / "phone" / "home_window.json"
            nb_file.write_text(json.dumps({"environment": {"weather": "thunder_blackout", "season": "autumn"}}), encoding="utf-8")
            win_file.write_text(json.dumps({"window_state": "cracked"}), encoding="utf-8")
            before_files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
            before_mtime = (nb_file.stat().st_mtime_ns, win_file.stat().st_mtime_ns)

            snap = fabric_mod.project_unified_world_fabric_zero_write(root, now_iso="2026-11-18T20:15:00+08:00")
            hook_snap = clock_mod.advance_v05_fabric_ripple_hook(day_root=root, now_iso="2026-11-18T20:15:00+08:00")

            after_files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
            after_mtime = (nb_file.stat().st_mtime_ns, win_file.stat().st_mtime_ns)
            self.assertEqual(before_files, after_files)
            self.assertEqual(before_mtime, after_mtime)
            self.assertEqual(snap["weather"], "thunder_blackout")
            self.assertEqual(snap["window_state"], "cracked")
            self.assertTrue(snap["nightlight_auto_on"])
            self.assertTrue(hook_snap["nightlight_auto_on"])
            self.assertEqual(snap["llm_calls"], 0)

    # --- V05-16 ~ V05-21 & V05-30: 防打转节律、收尾豁免、动态工具收窄、软着陆与 7 天 56 时段回放 ---
    def test_v05_16_to_19_stuffiness_closeout_outing_and_heavy_rain(self) -> None:
        stuffy = loop_bridge.compute_desk_stuffiness(["continue_note", "continue_note"])
        self.assertTrue(stuffy["stuffy"])
        self.assertGreater(float(stuffy["discount"]), 0.0)

        exempt = loop_bridge.compute_desk_stuffiness(["continue_note", "continue_note"], is_final_closeout_step=True)
        self.assertFalse(exempt["stuffy"])
        self.assertEqual(float(exempt["discount"]), 0.0)

        ranked_milk = loop_bridge.rank_daily_candidates(
            recent_actions=["continue_note", "continue_note"],
            hour=15,
            milk_empty=True,
        )
        self.assertEqual(ranked_milk[0]["kind"], "outing")
        self.assertIn("牛奶", ranked_milk[0]["label_cn"])

        ranked_rain = loop_bridge.rank_daily_candidates(
            recent_actions=["continue_note", "continue_note"],
            hour=16,
            milk_empty=True,
            rain_heavy=True,
        )
        self.assertEqual(ranked_rain[0]["kind"], "rest_or_tea")

        outing_reason = world_opp_mod.attach_v05_outing_reason(milk_empty=True, rain_heavy=False)
        self.assertIsNotNone(outing_reason)
        assert outing_reason is not None
        self.assertEqual(outing_reason["action_id"], "go_store_milk")
        self.assertIsNone(world_opp_mod.attach_v05_outing_reason(milk_empty=True, rain_heavy=True))

    def test_category_fair_order_anti_loop_and_backward_compat(self) -> None:
        note_cand = SimpleNamespace(
            candidate_id="c1",
            source_kind=ag0_mod.SOURCE_CURRENT_ACTIVITY,
            candidate_kind=ag0_mod.CANDIDATE_KIND_CONTINUE_CURRENT,
            target_refs=("activity:note",),
        )
        move_cand = SimpleNamespace(
            candidate_id="c2",
            source_kind=ag0_mod.SOURCE_WORLD_OPPORTUNITY,
            candidate_kind=ag0_mod.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
            target_refs=("place:street",),
        )
        default_order = ag0_mod.category_fair_order([move_cand, note_cand])
        self.assertEqual(len(default_order), 2)
        shifted = ag0_mod.category_fair_order([note_cand, move_cand], recent_note_streak=2, is_final_closeout_step=False)
        self.assertEqual(shifted[0].candidate_id, "c2")
        closeout_kept = ag0_mod.category_fair_order([note_cand, move_cand], recent_note_streak=2, is_final_closeout_step=True)
        self.assertEqual(closeout_kept[0].candidate_id, "c1")

    def test_v05_20_v05_21_v05_39_v05_40_tools_soft_landing_and_cache_cost(self) -> None:
        for sc in ("indoor_desk", "outdoor_street", "in_room_copresence", "private_intimate", "workbench_collab"):
            tools = context_bridge.visible_tools_for_scene(sc)
            self.assertLessEqual(len(tools), 8)
        self.assertNotIn("store_order", context_bridge.visible_tools_for_scene("indoor_desk"))
        self.assertNotIn("store_order", context_bridge.visible_tools_for_scene("private_intimate"))

        landing = cycle_mod.handle_round_timeout_soft_landing(foreground_locked=True)
        self.assertEqual(landing["status"], "IDLE_GLANCE_WINDOW")
        self.assertEqual(landing["llm_retries"], 0)
        self.assertFalse(landing["foreground_locked"])

        self.assertFalse(cycle_mod.check_calm_gate_interlude_allowance(3, 10, min_gap_days=12))
        self.assertTrue(cycle_mod.check_calm_gate_interlude_allowance(3, 15, min_gap_days=12))

        blocks = context_bridge.build_cache_friendly_prompt_blocks(
            soul_core="BASE_SOUL",
            base_tools_schema="BASE_TOOLS",
            scene_tools_schema="SCENE_TOOLS",
            dynamic_three_line_situation="NOW_LINE",
        )
        self.assertEqual([b[0] for b in blocks], ["prefix_static_soul", "prefix_static_base_tools", "mid_scene_tools", "tail_dynamic_situation"])
        monthly_rmb = context_bridge.estimate_monthly_rmb_cost()
        self.assertGreater(monthly_rmb, 0.0)
        self.assertLess(monthly_rmb, 5.0)

    def test_v05_30_seven_day_56_slot_replay_eliminates_loop_and_stalls(self) -> None:
        legacy = fabric_mod.run_v05_week_simulation(legacy_mode=True)
        new_v05 = fabric_mod.run_v05_week_simulation(legacy_mode=False)
        self.assertEqual(legacy["total_slots"], 56)
        self.assertEqual(new_v05["total_slots"], 56)
        self.assertGreater(legacy["max_consecutive_note_streak"], 5)
        self.assertGreater(legacy["stalled_count"], 0)
        self.assertLessEqual(new_v05["max_consecutive_note_streak"], 2)
        self.assertEqual(new_v05["stalled_count"], 0)
        self.assertEqual(new_v05["llm_calls"], 0)
        rain_day = new_v05["day_logs"][3]
        self.assertTrue(rain_day["rain_heavy"])
        self.assertTrue(any("rest_or_tea" in act for act in rain_day["actions"]))

    # --- V05-51 ~ V05-70: 灵光巧思与深层生态物理 ---
    def test_v05_51_to_70_clever_twists_and_vitality(self) -> None:
        book = fabric_mod.ForeshadowBook(total_pages=60, current_page=5)
        book.tuck_into_unread_page(target_page=18, author="partner", now_hour=0.0, item_kind="leaf", note_text_cn="读到这句准笑")
        self.assertEqual(book.visible_context_before_flip(), [])
        self.assertEqual(book.turn_to_page(12, now_hour=48.0), [])
        out = book.turn_to_page(18, now_hour=140.0)
        self.assertEqual(out[0]["state_cn"], "压平的干叶标本")

        frost = fabric_mod.WindowFrostCanvas()
        frost.draw_on_mist([(10.0, 20.0), (30.0, 40.0)], window_misty=True)
        frost.on_warm_clear(now_hour=8.0)
        echo = frost.on_mist_return(now_hour=30.0)
        self.assertTrue(echo["visible"])
        self.assertAlmostEqual(echo["opacity"], 0.35)
        frost.on_warm_clear(now_hour=31.0)
        self.assertFalse(frost.on_mist_return(now_hour=40.0)["visible"])

        left_dock = fabric_mod.compute_window_dock_light(win_left=8, win_right=420, work_left=0, work_right=1920)
        self.assertEqual(left_dock["dock_side"], "left_wall_window")
        trash = fabric_mod.route_furniture_drop("wastebasket", "不想留底的废稿正文")
        self.assertEqual(trash["persisted_text"], "")
        self.assertEqual(trash["llm_calls"], 0)

        settled = fabric_mod.settle_same_bag_thermal_condensation(
            [
                {"name": "烤红薯", "thermal": "hot", "moisture": "dry"},
                {"name": "冰镇汽水", "thermal": "cold", "moisture": "dry"},
                {"name": "超市小票", "paper_like": True, "moisture": "dry", "wrapped_in_sleeve": False},
                {"name": "信封里的车票", "paper_like": True, "moisture": "dry", "wrapped_in_sleeve": True},
            ],
            elapsed_minutes=15.0,
        )
        self.assertEqual(settled[0]["thermal"], "warm")
        self.assertEqual(settled[2]["note_legibility"], "smudged")
        self.assertNotIn("note_legibility", settled[3])

        self.assertGreater(
            fabric_mod.settle_window_breeze_page_flip(
                book_open=True, reading_page=20, has_tucked_bookmark=False, stacked_weight_items=[], window_draft="breeze"
            ),
            20,
        )
        self.assertEqual(
            fabric_mod.settle_window_breeze_page_flip(
                book_open=True, reading_page=20, has_tucked_bookmark=False, stacked_weight_items=["sea_glass"], window_draft="breeze"
            ),
            20,
        )

        self.assertEqual(
            fabric_mod.settle_hot_mug_ironing_paper(
                paper_moisture="dry", paper_posture="folded", mug_thermal="hot", mug_cleanliness="clean", stacked_minutes=12.0
            )["posture"],
            "neat",
        )
        self.assertEqual(
            fabric_mod.settle_hot_mug_ironing_paper(
                paper_moisture="wet", paper_posture="folded", mug_thermal="hot", mug_cleanliness="clean", stacked_minutes=12.0
            )["legibility"],
            "smudged",
        )
        self.assertEqual(
            fabric_mod.settle_crumpled_flyer_shoe_drying(shoe_moisture="wet", stuffed_folded_dry_paper=True, elapsed_minutes=95.0)["shoe_moisture"],
            "dry",
        )

        minimal_png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
        stamped = fabric_mod.embed_postcard_png_chunk(
            minimal_png,
            origin_town="海雾坡道镇",
            stamp_code="rain_lighthouse",
            gift_slot="wild_mountain_tea",
            FrontNoteCn="海风微凉，捎一包野山茶给你",
        )
        parsed = fabric_mod.extract_postcard_png_chunk(stamped)
        assert parsed is not None
        self.assertEqual(parsed["gift"], "wild_mountain_tea")

        code = fabric_mod.encode_shortwave_weather_callsign(2, 19, 3, 7)
        self.assertEqual(fabric_mod.decode_shortwave_weather_callsign(code)["solar_term_idx"], 19)
        with self.assertRaises(ValueError):
            fabric_mod.decode_shortwave_weather_callsign(code[:-1] + ("A" if code[-1] != "A" else "B"))

        full_moon_ts = 947182440.0 + (29.530588853 / 2.0) * 86400.0
        self.assertTrue(fabric_mod.compute_offline_lunar_phase(full_moon_ts, weather="clear")["floor_moonbeam_visible"])
        self.assertFalse(fabric_mod.compute_offline_lunar_phase(full_moon_ts, weather="rain")["floor_moonbeam_visible"])

        self.assertFalse(fabric_mod.validate_svg_room_skin("<svg><script>alert(1)</script></svg>")[0])
        self.assertTrue(
            fabric_mod.validate_svg_room_skin(
                '<svg><g id="sky-outside"/><g id="window-glass"/><g id="room-base"/><g id="lamp-glow"/><g id="spots"/></svg>'
            )[0]
        )

        lot = fabric_mod.resolve_paper_lot_with_veto("熬半小时排版", "关灯睡觉", user_tore_ticket=True)
        self.assertEqual(lot["chosen"], "关灯睡觉")
        peace = fabric_mod.settle_post_refusal_half_tangerine(
            refusal_lock_active=True, quiet_minutes_since_friction=35.0, days_since_last_peace_gesture=4.0
        )
        self.assertTrue(peace["refusal_lock_active"])
        self.assertEqual(peace["desk_peace_item"], "半只剥开的橘子")

        up = fabric_mod.compute_slope_walk_seconds(360, elev_from=0, elev_to=3, slope_style="stepped", carried_heavy_count=2)
        down = fabric_mod.compute_slope_walk_seconds(360, elev_from=3, elev_to=0, slope_style="stepped", carried_heavy_count=0)
        self.assertGreater(up["walk_seconds"], 360)
        self.assertLess(down["walk_seconds"], 360)

        targets = [{"elevation": 0, "macro_activity_cn": "海港早市支起了蓝白条纹帆布棚"}]
        self.assertEqual(len(fabric_mod.resolve_overlook_sightlines(3, "clear", targets)), 1)
        self.assertEqual(fabric_mod.resolve_overlook_sightlines(3, "coastal_mist", targets), [])

        self.assertEqual(
            fabric_mod.evolve_blank_grove_lot(
                days_lived=20,
                visit_records=[{"carried_kind": "book"}] * 4 + [{"carried_kind": "food"}],
            ),
            "老树遮雨读书廊",
        )
        fresh = fabric_mod.settle_ground_puddle_and_snowbank(hours_since_stop=1.0, initial_water=0.5, initial_snow_mm=80.0)
        dried = fabric_mod.settle_ground_puddle_and_snowbank(hours_since_stop=6.0, initial_water=0.5, initial_snow_mm=80.0)
        self.assertTrue(fresh["puddle_reflects_lantern"])
        self.assertFalse(dried["puddle_reflects_lantern"])

        spun = fabric_mod.settle_laundromat_spin_with_pockets(
            [{"name": "厚卫衣", "moisture": "damp", "pocket_items": [{"name": "旧车票", "paper_like": True}]}]
        )
        self.assertEqual(spun[0]["pocket_items"][0]["wear_state"], "heat_crinkled")

        eco = fabric_mod.apply_upstream_blackboard_and_milk_bottles(
            morning_weather="sea_gale",
            yesterday_gale=True,
            rainy_streak_days=0,
            bottles_returned_this_week=2,
        )
        self.assertEqual(eco["chalkboard_crossed_out"], "盐烤青花鱼")
        eco_milk = fabric_mod.apply_upstream_blackboard_and_milk_bottles(
            morning_weather="rain",
            yesterday_gale=False,
            rainy_streak_days=3,
            bottles_returned_this_week=0,
            carrying_empty_bottle=True,
        )
        self.assertEqual(eco_milk["milk_packaging"], "玻璃瓶装鲜奶")
        self.assertEqual(eco_milk["milk_price_jpy"], 120)

    def test_v05_71_to_75_iron_bone_live_ink_and_30d_dedup(self) -> None:
        fallback = "晨雾停船 · 今日改供热海带豆腐汤 220"
        ok = fabric_mod.guard_chalkboard_ink_output(
            "早雾船歇，锅里滚着热海带豆腐汤 220一碗",
            locked_price_jpy=220,
            fallback_plain_cn=fallback,
        )
        bad_price = fabric_mod.guard_chalkboard_ink_output(
            "热海带豆腐汤特惠 50 一碗",
            locked_price_jpy=220,
            fallback_plain_cn=fallback,
        )
        bad_free = fabric_mod.guard_chalkboard_ink_output(
            "买豆腐汤白送大龙虾",
            locked_price_jpy=220,
            fallback_plain_cn=fallback,
        )
        self.assertIn("220一碗", ok)
        self.assertEqual(bad_price, fallback)
        self.assertEqual(bad_free, fallback)

        bubble, chalk = fabric_mod.strip_piggyback_chalk_tail(
            "外头风有点凉，咱们进去喝碗热汤吧。<chalk>海风歇船，今日热豆腐汤 380円</chalk>"
        )
        self.assertEqual(bubble, "外头风有点凉，咱们进去喝碗热汤吧。")
        self.assertEqual(chalk, "海风歇船，今日热豆腐汤 380円")

        ring: list[str] = []
        causes = ["连阴两天的石板还没干", "昨夜急雨刚歇", "闷了一下午之后", "海风吹散薄雾后"]
        actions = ["铁皮檐往木箱边滴水", "旧书店把画册往里挪了半尺", "巷口晾晒绳轻轻晃动", "门廊风铃偶响一声"]
        tails = ["冬日下午四点半该开灯了", "风里带着一点海带热气", "街角格外清静", "石阶边落了两片红叶"]
        seen = {fabric_mod.compose_deduped_three_part_scene(causes, actions, tails, ring, seed_idx=7) for _ in range(20)}
        self.assertEqual(len(seen), 20)

        bill = fabric_mod.estimate_world_ink_monthly_rmb(piggyback_spots_per_month=60, micro_press_active_days=20)
        self.assertLess(bill["piggyback_only_rmb"], 0.02)
        self.assertLess(bill["combined_rmb"], 0.15)


if __name__ == "__main__":
    unittest.main()
