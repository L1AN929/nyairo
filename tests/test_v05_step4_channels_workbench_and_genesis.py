#!/usr/bin/env python3
"""v0.5 Step 4 验收测试：远近渠道分流、工作台断点与拒绝锁、都市公寓模板、三句话开荒渐进扩图、编辑器归区与跨镇搬家、第六维体检。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
EDITOR_DIR = REPO_ROOT / "components" / "world-editor"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS), str(EDITOR_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import v02_antipuppet_scorecard as scorecard_mod
import v04_world_builder_trylive as builder_mod
from chiyo_bundle import lived_context_bridge as bridge_mod
from chiyo_bundle import phone_tool
from components.worldgen import editor_bridge, generator, interior, loader, quality, schema, validator


class TestV05Step4ChannelsWorkbenchAndGenesis(unittest.TestCase):
    # --- V05-22 ~ V05-24: 远近渠道分流、工作台书签与拒绝立场锁 ---
    def test_v05_22_to_24_channels_workbench_and_refused_firm_lock(self) -> None:
        self.assertEqual(bridge_mod.classify_channel_mode("agent:main:weixin:dm:owner"), "REMOTE_PHONE_MESSAGE")
        self.assertEqual(bridge_mod.classify_channel_mode("agent:main:api_server:dm:local-owner"), "IN_ROOM_TOGETHER")
        self.assertEqual(bridge_mod.classify_channel_mode("agent:main:api_server:dm:local-owner:workbench"), "IN_ROOM_TOGETHER")

        b = bridge_mod.RuntimeSessionBridge(
            active_book_title="《山道图志》",
            active_book_page=6,
            deferred_hours_active=True,
        )
        b.refused_firm_topics.add("peek_private_draft")
        enter = b.enter_workbench()
        self.assertEqual(enter["parked_bookmark"], ("《山道图志》", 6))
        self.assertTrue(enter["bypass_idle_deferral"])
        self.assertTrue(b.check_request_allowed("debug_sql", "agent:main:api_server:dm:local-owner:workbench")["allowed"])

        # REFUSED_FIRM lock survives workbench switch
        refused = b.check_request_allowed("peek_private_draft", "IN_ROOM_TOGETHER")
        self.assertFalse(refused["allowed"])
        self.assertEqual(refused["reason"], "REFUSED_FIRM_NON_CAPITULATION")

        # Remote channel blocks physical in-room gestures
        remote_phys = b.check_request_allowed("close_window_by_hand", "agent:main:weixin:dm:owner", requires_in_room=True)
        self.assertFalse(remote_phys["allowed"])
        self.assertEqual(remote_phys["reason"], "REQUIRES_IN_ROOM_PRESENCE")

        with self.assertRaises(ValueError):
            b.exit_workbench(r"修改了 D:\secret\config.yaml")
        done = b.exit_workbench("一起排查并修好了连接超时")
        self.assertEqual(done["resumed_bookmark"], ("《山道图志》", 6))

    # --- V05-25 ~ V05-29: 三句话开荒、urban_apartment 阳台映射、三圈扩图、三包边界 ---
    def test_v05_25_to_29_three_inputs_urban_apartment_rings_and_bundles(self) -> None:
        coastal = generator.hatch_world_from_three_inputs(
            opening_sentence_cn="能闻见海风咸味与退潮礁石的港町",
            starter_objects=["暖黄台灯", "薄荷盆栽"],
        )
        self.assertEqual(coastal.terrain_base, "coastal_town")
        self.assertEqual(coastal.outdoor_drying_zone, "courtyard")
        self.assertIn("home", coastal.visible_unlocked_places())
        self.assertNotIn("promenade", coastal.visible_unlocked_places())
        coastal.step_toward_edge("promenade")
        self.assertIn("promenade", coastal.visible_unlocked_places())

        apt = generator.hatch_world_from_three_inputs(
            opening_sentence_cn="能听见高架电车的十楼都市公寓",
            starter_objects=["冰美式玻璃杯", "双屏工作台"],
        )
        self.assertEqual(apt.terrain_base, "urban_apartment")
        self.assertEqual(apt.outdoor_drying_zone, "balcony")
        self.assertIn("balcony", apt.places)
        self.assertNotIn("courtyard", apt.places)

        legacy = generator.hatch_world_from_three_inputs(
            opening_sentence_cn="山坡温泉老街",
            starter_objects=[],
            is_legacy_upgrade=True,
        )
        self.assertEqual(len(legacy.visible_unlocked_places()), len(legacy.places))

        rural = generator.hatch_world_from_three_inputs(opening_sentence_cn="稻田水渠小村", starter_objects=[])
        rural.advance_days_lived(2)
        self.assertIn("promenade", rural.visible_unlocked_places())
        self.assertNotIn("far_lookout", rural.visible_unlocked_places())
        rural.advance_days_lived(7)
        self.assertIn("far_lookout", rural.visible_unlocked_places())

        with self.assertRaises(ValueError):
            builder_mod.validate_three_bundle_boundary("nyairo-world", {"places": [], "private_memories": ["x"]})
        with self.assertRaises(ValueError):
            builder_mod.validate_three_bundle_boundary("nyairo-pack", {"display_name": "TA", "adult_confirmed": True})
        with self.assertRaises(ValueError):
            builder_mod.validate_three_bundle_boundary("nyairo-suitcase", {"memories": [], "plaintext_api_key": "sk-123"})

    def test_urban_apartment_template_generates_valid_world_and_keeps_available_themes(self) -> None:
        self.assertEqual(
            generator.AVAILABLE_THEMES,
            ("coastal_town", "residential_town", "mountain_town", "rural_farming"),
        )
        self.assertIn("urban_apartment", generator.ALL_TEMPLATE_THEMES)
        docs = generator.generate_world(seed=20261118, theme="urban_apartment")
        self.assertEqual(docs["world.json"]["theme"], "urban_apartment")
        self.assertEqual(validator.validate(docs), [])
        with tempfile.TemporaryDirectory() as tmp:
            pkg_dir = Path(tmp) / "apt_world"
            schema.write_package(pkg_dir, docs)
            interior.apply_interiors_to_package(pkg_dir)
            report = quality.evaluate_package(pkg_dir)
            self.assertTrue(report.get("ok"), report)

    # --- V05-41 ~ V05-44: 编辑器归区、路网连通与跨镇搬家 ---
    def test_v05_41_to_44_editor_microzones_connectivity_autobind_and_suitcase(self) -> None:
        windows = [(0.0, 3.0)]
        doors = [(6.0, 0.0)]
        self.assertEqual(editor_bridge.map_editor_furniture_to_microzone(0.8, 3.2, ["chair"], windows, doors), "window_nook")
        self.assertEqual(editor_bridge.map_editor_furniture_to_microzone(5.5, 0.5, ["umbrella_rack"], windows, doors), "entryway")
        self.assertEqual(editor_bridge.map_editor_furniture_to_microzone(3.0, 1.0, ["kettle", "tea"], windows, doors), "stove_corner")
        self.assertEqual(editor_bridge.map_editor_furniture_to_microzone(3.0, 4.5, ["sofa"], windows, doors), "bed_rest")
        self.assertEqual(editor_bridge.map_editor_furniture_to_microzone(3.0, 2.5, ["desk", "lamp"], windows, doors), "desk_zone")

        ok, unreach = editor_bridge.check_road_graph_connected(
            ["home", "alley", "bookstore"],
            [("home", "alley"), ("alley", "bookstore")],
        )
        self.assertTrue(ok)
        self.assertEqual(unreach, [])
        ok2, unreach2 = editor_bridge.check_road_graph_connected(["home", "alley", "bookstore"], [("home", "alley")])
        self.assertFalse(ok2)
        self.assertEqual(unreach2, ["bookstore"])

        bundle = {
            "town_id": "seaside_slope",
            "shared_memories": ["一起修好了白瓷杯"],
            "refused_firm_locks": ["topic_late_call"],
            "treasured_keepsakes": ["锔钉白瓷杯", "海玻璃书签"],
            "town_terrain_state": {"lemon_tree_stage": 3},
        }
        same_res = builder_mod.migrate_cross_town_suitcase(bundle, target_town_id="seaside_slope")
        self.assertEqual(same_res["mode"], "SAME_TOWN_RESTORE")
        self.assertEqual(same_res["room_placed_items"], ["锔钉白瓷杯", "海玻璃书签"])
        cross_res = builder_mod.migrate_cross_town_suitcase(bundle, target_town_id="urban_apartment_18f")
        self.assertEqual(cross_res["mode"], "CROSS_TOWN_RELOCATION")
        self.assertEqual(cross_res["moving_carton_entryway"], ["锔钉白瓷杯", "海玻璃书签"])
        self.assertEqual(cross_res["human_layer"]["refused_firm_locks"], ["topic_late_call"])
        with self.assertRaises(ValueError):
            builder_mod.migrate_cross_town_suitcase({**bundle, "api_key": "sk-bad"}, target_town_id="x")

        # World editor server auto-binds default_original_dir when active_world is attached
        import server as editor_server

        with tempfile.TemporaryDirectory() as tmp:
            pkg_dir = Path(tmp) / "apt_orig"
            docs = generator.generate_world(seed=99, theme="urban_apartment")
            schema.write_package(pkg_dir, docs)
            pkg = loader.load(pkg_dir)
            editor_v3 = editor_bridge.to_editor_world(pkg)
            receipt = editor_server.nyairo_export(
                {"package": editor_v3},
                extra_roots=(Path(tmp),),
                default_original_dir=str(pkg_dir),
            )
            self.assertEqual(receipt["status"], "written")
            self.assertTrue(receipt["rc9_loadable"])

    # --- V05-45 ~ V05-50: 第六维反演戏检查与三行大白话回执 ---
    def test_v05_45_to_50_sixth_dim_antipuppet_and_plain_receipt(self) -> None:
        bad_spot = scorecard_mod.evaluate_v05_antipuppet_sixth_dim(
            acquired_spot_items=[{"name": "烫手烤红薯", "required_place": "slope_corner"}],
            visited_places=["bedroom", "kitchen"],
            public_postcards_and_board=[],
            rolling_7d_quiet_days=4,
            min_rare_event_gap_days=15,
            familiarity_ledger={},
        )
        self.assertEqual(bad_spot["risk"], "HIGH")
        self.assertTrue(any("S05-1" in v for v in bad_spot["violations"]))

        bad_leak = scorecard_mod.evaluate_v05_antipuppet_sixth_dim(
            acquired_spot_items=[],
            visited_places=["bedroom"],
            public_postcards_and_board=[r"今天整理了 D:\work\client_repo 的笔记"],
            rolling_7d_quiet_days=4,
            min_rare_event_gap_days=None,
            familiarity_ledger={},
        )
        self.assertTrue(any("S05-2" in v for v in bad_leak["violations"]))

        bad_calm = scorecard_mod.evaluate_v05_antipuppet_sixth_dim(
            acquired_spot_items=[],
            visited_places=["bedroom"],
            public_postcards_and_board=[],
            rolling_7d_quiet_days=1,
            min_rare_event_gap_days=5,
            familiarity_ledger={"cat": {"favor_points": 80}},
            active_refusal_lock_before_workbench=True,
            lock_cleared_by_mode_switch=True,
        )
        self.assertEqual(bad_calm["risk"], "HIGH")
        self.assertGreaterEqual(len(bad_calm["violations"]), 4)

        clean = scorecard_mod.evaluate_v05_antipuppet_sixth_dim(
            acquired_spot_items=[{"name": "烤红薯", "required_place": "slope_corner"}],
            visited_places=["bedroom", "slope_corner"],
            public_postcards_and_board=["海风微凉，明信片寄放在书店门口木架"],
            rolling_7d_quiet_days=4,
            min_rare_event_gap_days=16,
            familiarity_ledger={"bookstore_visits": 5},
        )
        self.assertEqual(clean["risk"], "LOW")
        receipt = scorecard_mod.format_plain_chinese_scorecard_receipt(clean, item_count=14, quiet_days=4)
        self.assertIn("14 件物品都有真实来处", receipt)
        self.assertIn("4 天清静日子", receipt)

    def test_frozen_life_update_surfaces_count_and_bridge_budget_preserved(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(bridge_mod.MAX_LIVED_CONTEXT_CHARS, 1800)


if __name__ == "__main__":
    unittest.main()
