#!/usr/bin/env python3
"""Unit & contract tests for v0.4 World Builder Diagnostics & 1-Hour Try-Live Sandbox (Ch. 44)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "phone"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.phone import v04_builder_trylive_view as view  # noqa: E402
from components.world.scripts import v04_world_builder_trylive as engine  # noqa: E402

NOW_ISO = "2026-11-13T14:00:00+08:00"

FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}


class V04WorldBuilderTryliveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_parent = Path(tempfile.mkdtemp(prefix="v04-builder-trylive-"))
        self.day_root = self.tmp_parent / "day_instance"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_parent, ignore_errors=True)

    def test_00_frozen_anchors_byte_unchanged(self) -> None:
        for rel_path, expected_sha in FROZEN_ANCHORS.items():
            full_path = ROOT / rel_path
            actual_sha = hashlib.sha256(full_path.read_bytes()).hexdigest()
            self.assertEqual(actual_sha, expected_sha, f"Frozen anchor modified: {rel_path}")

    def test_01_empty_nonexistent_day_root_get_zero_disk_writes_and_dual_keys(self) -> None:
        self.assertFalse(self.day_root.exists())
        code, env = view.read_v04_builder_trylive_view(
            self.day_root,
            has_subject_capability=False,
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200)
        self.assertTrue(env["ok"])
        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["schema"], "phone.v04.builder_trylive.v1")
        self.assertEqual(env["schema"], view.V04_BUILDER_TRYLIVE_SCHEMA)
        self.assertTrue(env["read_only"])
        self.assertEqual(env["perspective"], "partner_default")
        self.assertEqual(env["revision"], 0)
        self.assertEqual(env["observed_at"], NOW_ISO)
        self.assertEqual(env["llm_calls"], 0)
        self.assertEqual(env["identity"]["pronoun"], "TA")
        self.assertTrue(env["sandbox_isolated"])

        # Zero disk writes: day_root still does not exist!
        self.assertFalse(self.day_root.exists())

        # Backend 6 mirrored keys + Frontend 10 locked keys in data and top-level
        backend_keys = (
            "route_audit",
            "preset_scenarios",
            "last_rehearsal",
            "last_scene_gap_check",
            "last_trylive_run",
            "last_impact_preview",
        )
        frontend_keys = (
            "revision",
            "identity",
            "sandbox_isolated",
            "presets",
            "inspectable_objects",
            "last_route_audit",
            "last_prose_check",
            "last_trylive_run",
            "last_why_changed",
            "last_modification_preview",
        )
        for key in backend_keys:
            self.assertIn(key, env)
            self.assertIn(key, env["data"])
        for key in frontend_keys:
            self.assertIn(key, env["data"])
        self.assertEqual(len(env["data"]["preset_scenarios"]), 4)
        self.assertEqual(len(env["data"]["presets"]), 4)
        self.assertTrue(env["data"]["route_audit"]["healthy"])

    def test_02_existing_day_root_get_preserves_mtimes_and_perspective(self) -> None:
        world_dir = self.day_root / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        live_file = world_dir / "v04_neighborhood_state.json"
        live_file.write_text(json.dumps({"environment": {"weather": "cloudy"}}), encoding="utf-8")
        before_bytes = live_file.read_bytes()
        before_ns = live_file.stat().st_mtime_ns

        code, env = view.read_v04_builder_trylive_view(
            self.day_root,
            has_subject_capability=True,
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200)
        self.assertEqual(env["perspective"], "her_private")
        self.assertFalse(view.v04_builder_sandbox_state_path(self.day_root).exists())
        self.assertEqual(live_file.read_bytes(), before_bytes)
        self.assertEqual(live_file.stat().st_mtime_ns, before_ns)

    def test_03_route_audit_detects_isolated_one_way_trap_and_missing_key_door(self) -> None:
        code, res = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "audit_routes_or_rehearse",
                "custom_places": [
                    {"place_id": "home", "label": "家"},
                    {"place_id": "courtyard", "label": "院子"},
                    {"place_id": "sunken_alley", "label": "单向低洼巷"},
                    {"place_id": "locked_storeroom", "label": "上锁储物间"},
                    {"place_id": "island_pavilion", "label": "孤岛凉亭"},
                ],
                "custom_routes": [
                    {
                        "route_id": "r_home_yard",
                        "from_place": "home",
                        "to_place": "courtyard",
                        "duration_min": 2,
                        "bidirectional": True,
                    },
                    {
                        "route_id": "r_yard_sunken_oneway",
                        "from_place": "courtyard",
                        "to_place": "sunken_alley",
                        "duration_min": 3,
                        "bidirectional": False,
                    },
                    {
                        "route_id": "r_yard_storeroom_locked",
                        "from_place": "courtyard",
                        "to_place": "locked_storeroom",
                        "duration_min": 1,
                        "bidirectional": True,
                        "requires_key": "storeroom_key",
                    },
                ],
                "available_keys": [],
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200, res)
        audit = res["result"]["route_audit"]
        self.assertEqual(set(audit["reachable_from_home"]), {"home", "courtyard", "sunken_alley"})
        self.assertIn("island_pavilion", audit["isolated_places"])
        self.assertEqual(audit["one_way_traps"], ["sunken_alley"])
        self.assertFalse(audit["auto_added_reverse_edges"])
        self.assertEqual(len(audit["missing_key_doors"]), 1)
        self.assertEqual(audit["missing_key_doors"][0]["requires_key"], "storeroom_key")
        self.assertFalse(audit["healthy"])
        self.assertFalse(res["result"]["mutated_live_resident"])

    def test_04_route_rehearsal_roadblock_turnback_and_frontend_blocked_edges_no_live_mutation(self) -> None:
        world_dir = self.day_root / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        live_file = world_dir / "v04_neighborhood_state.json"
        live_payload = {"journey": {"status": "at_place", "current_place": "home"}}
        live_file.write_text(json.dumps(live_payload), encoding="utf-8")
        before_bytes = live_file.read_bytes()

        code, res = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "audit_routes_or_rehearse",
                "route_id": "",
                "blocked_edges": ["route_street_bookstore"],
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200, res)
        reh = res["result"]["rehearsal"]
        self.assertTrue(reh["blocked_midway"])
        self.assertFalse(reh["remote_omniscient_reroute"])
        self.assertFalse(reh["teleported_home_on_block"])
        self.assertFalse(res["result"]["mutated_live_resident"])
        phases = [s["phase"] for s in res["result"]["rehearsal_steps"]]
        self.assertIn("observe_roadblock_at_visible_node", phases)
        self.assertIn("turn_back_segment", phases)
        self.assertEqual(reh["final_rehearsal_place"], "home")
        self.assertEqual(live_file.read_bytes(), before_bytes)

    def test_05_route_rehearsal_pause_at_node_and_reroute_modes(self) -> None:
        c_pause, r_pause = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "audit_routes_or_rehearse",
                "destination_place_id": "bookstore",
                "inject_roadblock_route": "route_street_bookstore",
                "on_roadblock": "pause_at_node",
            },
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_pause, 200)
        self.assertEqual(r_pause["result"]["rehearsal"]["final_rehearsal_place"], "street_corner")

        c_reroute, r_reroute = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "audit_routes_or_rehearse",
                "route_path": ["home", "porch", "courtyard", "street_corner", "bookstore"],
                "inject_roadblock_route": "route_courtyard_street_open",
                "on_roadblock": "reroute",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_reroute, 200)
        self.assertEqual(r_reroute["result"]["rehearsal"]["final_rehearsal_place"], "bookstore")
        used_routes = [s.get("route_id") for s in r_reroute["result"]["rehearsal_steps"] if s.get("route_id")]
        self.assertIn("route_courtyard_covered_walkway", used_routes)

    def test_06_scene_prose_gap_check_porch_bench_card_and_dangling_exit(self) -> None:
        code, res = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "check_scene_prose_gaps",
                "place_id": "porch",
                "prose": "窗边有一张长椅，旁边的架子放着借阅卡，门通向后巷仓库",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200, res)
        out = res["result"]
        self.assertFalse(out["applied_to_live_world"])
        self.assertEqual(out["llm_calls"], 0)
        by_target = {g["target"]: g for g in out["gaps"]}
        self.assertEqual(by_target["长椅"]["severity"], "missing_affordance")
        self.assertIn("坐下能力", by_target["长椅"]["suggestion"])
        self.assertEqual(by_target["架子"]["severity"], "ready")
        self.assertEqual(by_target["借阅卡"]["severity"], "missing_content")
        self.assertIn("可读正文尚未提供", by_target["借阅卡"]["suggestion"])
        self.assertEqual(by_target["后巷仓库"]["severity"], "dangling_exit")
        self.assertIn("未定义区域", by_target["后巷仓库"]["suggestion"])
        self.assertGreaterEqual(len(out["draft_candidates"]), 3)
        self.assertIsNotNone(res["data"]["last_prose_check"])
        self.assertIsNotNone(res["data"]["last_scene_gap_check"])

    def test_07_scene_prose_gap_check_missing_return_box_rule_and_ready_bookstore(self) -> None:
        # On porch, no return box exists yet -> missing_object
        c1, r1 = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "check_scene_prose_gaps",
                "place_id": "porch",
                "scene_prose": "墙上钉着挂钩，门边新摆了一只归还箱，小门通向院子",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c1, 200)
        g1 = {g["target"]: g for g in r1["result"]["gaps"]}
        self.assertEqual(g1["挂钩"]["severity"], "ready")
        self.assertEqual(g1["归还箱"]["severity"], "missing_object")
        self.assertEqual(g1["院子"]["severity"], "ready")

        # On bookstore, return box IS configured with receive_rule -> ready
        c2, r2 = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "check_scene_prose_gaps",
                "place_id": "bookstore",
                "scene_prose": "门口设有归还箱，店里有书架与柜台",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c2, 200)
        g2 = {g["target"]: g for g in r2["result"]["gaps"]}
        self.assertEqual(g2["归还箱"]["severity"], "ready")
        self.assertEqual(g2["书架"]["severity"], "ready")
        self.assertEqual(g2["柜台"]["severity"], "ready")

    def test_08_trylive_preset_1_sunny_cloth_drying_and_frontend_go_out_and_return(self) -> None:
        for pid in ("sunny_cloth_drying", "go_out_and_return"):
            code, res = view.mutate_v04_builder_trylive_view(
                self.day_root,
                {"action": "run_trylive_hour", "preset_id": pid},
                actor_side="partner",
                now_iso=NOW_ISO,
            )
            self.assertEqual(code, 200, res)
            run = res["result"]
            self.assertEqual(run["scenario_id"], "sunny_cloth_drying")
            self.assertEqual(run["preset_id"], "go_out_and_return")
            self.assertEqual(run["llm_calls"], 0)
            self.assertFalse(run["polluted_live_memory"])
            cloth = run["final_sandbox_state"]["cloth"]
            self.assertEqual(cloth["final_state"], "dry")
            self.assertLess(cloth["final_moisture"], cloth["initial_moisture"])

    def test_09_trylive_preset_2_rain_midway_drying_piecewise_and_porch_comparison(self) -> None:
        code, res = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {"action": "run_trylive_hour", "scenario_id": "rain_midway_drying"},
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200, res)
        state = res["result"]["final_sandbox_state"]
        self.assertEqual(len(state["drying_segments"]), 2)
        self.assertEqual(state["drying_segments"][0]["weather"], "sunny")
        self.assertEqual(state["drying_segments"][1]["weather"], "rain")
        self.assertTrue(state["drying_segments"][1]["sheltered"])
        self.assertEqual(state["drying_segments"][1]["ventilation"], "weak")
        comp = state["cloth"]["porch_vs_open_yard_comparison"]
        self.assertLess(comp["porch_hook_final_moisture"], comp["open_courtyard_rain_moisture"])
        self.assertEqual(state["cloth"]["final_state"], "damp")

    def test_10_trylive_preset_3_roadblock_turnback_container_children_no_drift(self) -> None:
        code, res = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {"action": "run_trylive_hour", "scenario_id": "roadblock_turnback"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200, res)
        run = res["result"]
        bag = run["final_sandbox_state"]["bag"]
        self.assertFalse(bag["child_items_drifted"])
        self.assertEqual(bag["effective_place"], "home")
        arrive_step = next(s for s in run["timeline"] if s["event"] == "arrive_bookstore_via_detour")
        self.assertEqual(
            arrive_step["sandbox_delta"]["child_effective_places"],
            {"sketch_album_book": "bookstore", "library_card_note": "bookstore"},
        )

    def test_11_trylive_preset_4_bookstore_ruler_after_hours_return_box_no_forged_receipt(self) -> None:
        code, res = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {"action": "run_trylive_hour", "preset_id": "public_notice_and_borrow"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200, res)
        final_s = res["result"]["final_sandbox_state"]
        ruler = final_s["ruler"]
        craft = final_s["craft_bench"]
        self.assertEqual(craft["input_units"], 4)
        self.assertEqual(craft["divider_units"], 1)
        self.assertEqual(craft["remaining_units"], 3)
        self.assertTrue(craft["unit_conserved"])
        self.assertEqual(ruler["final_location"], "bookstore_return_box")
        self.assertEqual(ruler["loan_status"], "returned_to_box_pending_check")
        self.assertFalse(ruler["swallowed_on_closed_shop"])
        self.assertFalse(ruler["forged_shopkeeper_in_person_receipt"])

    def test_12_trylive_combined_fault_injections_and_frontend_seat_taken_and_booleans(self) -> None:
        c1, r1 = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "run_trylive_hour",
                "scenario_id": "sunny_cloth_drying",
                "inject_faults": ["rain", "seat_occupied", "roadblock", "restart_gap"],
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c1, 200, r1)
        fs1 = r1["result"]["final_sandbox_state"]
        self.assertEqual(fs1["chosen_seat"], "home_chair")
        self.assertTrue(fs1["fault_assertions"]["seat_collision_prevented"])
        self.assertTrue(fs1["fault_assertions"]["recovered_from_step_anchor"])
        self.assertFalse(r1["result"]["polluted_live_memory"])

        # Frontend injections list with "seat_taken" + boolean flag compatibility
        c2, r2 = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "run_trylive_hour",
                "preset_id": "go_out_and_return",
                "injections": ["seat_taken"],
                "rain": True,
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c2, 200, r2)
        self.assertIn("seat_occupied", r2["result"]["inject_faults"])
        self.assertIn("rain", r2["result"]["inject_faults"])
        self.assertIn("seat_taken", r2["result"]["injections"])

    def test_13_trylive_dual_lighting_version_diff_bright_vs_glare(self) -> None:
        code, res = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "run_trylive_hour",
                "scenario_id": "sunny_cloth_drying",
                "compare_lighting": "bright_vs_glare",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code, 200, res)
        vdiff = res["result"]["version_diff"]
        self.assertTrue(vdiff["enabled"])
        self.assertTrue(vdiff["observation_changed"])
        self.assertTrue(vdiff["behavior_changed"])
        self.assertEqual(vdiff["version_a"]["chosen_seat"], "window_reading_nook")
        self.assertEqual(vdiff["version_b"]["chosen_seat"], "home_chair")

    def test_14_why_changed_default_uninitialized_honest_missing_history_no_hallucination(self) -> None:
        targets = ("linen_cloth", "public_notice", "shared_ruler", "porch_plant", "crafted_box")
        for tok in targets:
            code, res = view.mutate_v04_builder_trylive_view(
                self.day_root,
                {"action": "inspect_why_changed", "object_id": tok},
                actor_side="partner",
                now_iso=NOW_ISO,
            )
            self.assertEqual(code, 200, tok)
            out = res["result"]
            self.assertEqual(out["data_source"], "default_initial_world_anchor")
            self.assertFalse(out["hallucinated_backstory"])
            self.assertFalse(out["leaked_private_diary"])
            self.assertEqual(out["builder_view"]["missing_history_note"], "无更早记录，不补写推测原因")
            self.assertFalse(out["subject_bounded_view"]["knows_admin_mechanism"])
            self.assertEqual(out["subject_bounded_view"]["pronoun"], "TA")

    def test_15_why_changed_reads_live_neighborhood_anchors_and_isolates_private_diary(self) -> None:
        world_dir = self.day_root / "world"
        life_dir = self.day_root / "life"
        world_dir.mkdir(parents=True, exist_ok=True)
        life_dir.mkdir(parents=True, exist_ok=True)

        secret_phrase = "SUPER_SECRET_PRIVATE_DIARY_ENTRY_DO_NOT_LEAK_9912"
        (life_dir / "private_diary.json").write_text(
            json.dumps({"private_diary": secret_phrase}, ensure_ascii=False),
            encoding="utf-8",
        )
        (world_dir / "v04_neighborhood_state.json").write_text(
            json.dumps(
                {
                    "drying_items": {
                        "wet_cloth_01": {
                            "item_id": "wet_cloth_01",
                            "label": "亚麻擦手巾",
                            "origin": "擦拭门廊雨痕后挂起",
                            "place_id": "porch",
                            "support_id": "porch_hook",
                            "sheltered": True,
                            "ventilation": "weak",
                            "moisture_ratio": 0.44,
                            "dryness_state": "damp",
                            "last_moved_by": "TA",
                            "last_moved_at": "2026-11-13T12:30:00+08:00",
                            "drying_started_at": "2026-11-13T12:30:00+08:00",
                            "weather_segments": [
                                {"weather": "light_rain", "sheltered": True, "ventilation": "weak"}
                            ],
                        }
                    },
                    "public_life": {
                        "notices": [
                            {
                                "notice_id": "notice_road_maintenance_01",
                                "title": "旧整修告示",
                                "place_id": "street_corner",
                                "real_author": "town_road_crew",
                                "posted_at": "2026-11-10T09:00:00+08:00",
                                "expires_at": "2026-11-11T18:00:00+08:00",
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        c_cloth, r_cloth = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {"action": "inspect_why_changed", "target_kind": "wet_cloth"},
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_cloth, 200)
        out_c = r_cloth["result"]
        self.assertEqual(out_c["data_source"], "live_neighborhood_state")
        self.assertEqual(out_c["builder_view"]["origin"], "擦拭门廊雨痕后挂起")
        self.assertIsNone(out_c["builder_view"]["missing_history_note"])
        self.assertIn("通风弱", out_c["builder_view"]["admin_mechanism_explanation"])
        self.assertNotIn("通风弱", out_c["subject_bounded_view"]["sensory_impression"])
        self.assertNotIn(secret_phrase, json.dumps(r_cloth, ensure_ascii=False))

        c_notice, r_notice = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {"action": "inspect_why_changed", "object_id": "public_notice"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_notice, 200)
        self.assertTrue(r_notice["result"]["builder_view"]["is_expired"])
        self.assertEqual(r_notice["result"]["builder_view"]["real_author"], "town_road_crew")

    def test_16_preview_modification_move_bench_occupied_blocks_instant_teleport_and_admin_migration(self) -> None:
        c_occ, r_occ = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "move_bench",
                "target_id": "courtyard_bench",
                "to_place_id": "porch",
                "simulate_occupied_by": "TA",
                "simulate_items_on_surface": ["canvas_tote_bag"],
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_occ, 200, r_occ)
        p_occ = r_occ["result"]
        self.assertFalse(p_occ["allowed_immediately"])
        self.assertEqual(p_occ["migration_mode"], "wait_until_idle")
        self.assertIn("TA", p_occ["affected_occupants"])
        self.assertIn("canvas_tote_bag", p_occ["affected_items"])
        self.assertTrue(p_occ["preserves_object_id"])
        self.assertFalse(p_occ["creates_false_self_moved_memory"])

        # Explicit admin_migrated while occupied still forbids silent instant teleport & preserves ID without false memory
        c_adm, r_adm = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "move_object",
                "target_id": "courtyard_bench",
                "to_place_id": "porch",
                "simulate_occupied_by": "TA",
                "resolution_mode": "admin_migrated",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_adm, 200)
        self.assertFalse(r_adm["result"]["allowed_immediately"])
        self.assertEqual(r_adm["result"]["migration_mode"], "admin_migrated")
        self.assertFalse(r_adm["result"]["creates_false_self_moved_memory"])

    def test_17_preview_modification_delete_referenced_place_blocked_vs_unreferenced_allowed(self) -> None:
        c_blk, r_blk = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "delete_place",
                "target_id": "bookstore",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_blk, 200, r_blk)
        out = r_blk["result"]
        self.assertFalse(out["allowed_immediately"])
        self.assertEqual(out["migration_mode"], "blocked_referenced_place")
        self.assertGreaterEqual(len(out["blocking_reasons"]), 3)
        self.assertIn("route_street_bookstore", out["referencing_routes"])

        c_free, r_free = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "delete_place",
                "target_id": "empty_unlinked_draft_node",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_free, 200)
        self.assertTrue(r_free["result"]["allowed_immediately"])
        self.assertEqual(r_free["result"]["migration_mode"], "safe_delete_unreferenced")

    def test_18_preview_modification_block_route_detour_vs_severed_home(self) -> None:
        c_safe, r_safe = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "block_route",
                "target_id": "route_courtyard_street_open",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_safe, 200)
        self.assertTrue(r_safe["result"]["allowed_immediately"])
        self.assertTrue(r_safe["result"]["alternate_routes_available"])

        c_cut, r_cut = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "block_route",
                "target_id": "route_home_porch",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_cut, 200)
        self.assertFalse(r_cut["result"]["allowed_immediately"])
        self.assertEqual(r_cut["result"]["migration_mode"], "blocked_severs_connectivity")
        self.assertIn("bookstore", r_cut["result"]["newly_isolated_places"])

    def test_19_preview_modification_apply_worldpack_diff_preserves_lived_state_and_blocks_unsafe_assets(self) -> None:
        c_ok, r_ok = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "apply_worldpack_diff",
                "worldpack_diff": {
                    "worldpack_id": "wp_autumn_porch_v2",
                    "added_places": ["tea_pavilion"],
                    "added_objects": ["porch_reed_curtain"],
                    "renamed_objects": {"courtyard_bench": "院子旧木长椅"},
                    "asset_paths": ["assets/porch_curtain.png"],
                },
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_ok, 200, r_ok)
        res_ok = r_ok["result"]
        self.assertTrue(res_ok["allowed_immediately"])
        self.assertEqual(res_ok["migration_mode"], "incremental_preserve_lived_state")
        self.assertFalse(res_ok["resets_ongoing_life"])
        self.assertFalse(res_ok["overwrites_lived_places"])
        self.assertTrue(res_ok["stable_object_ids_preserved"])
        self.assertFalse(res_ok["retroactive_formula_rewrite"])

        c_bad, r_bad = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "apply_worldpack_diff",
                "worldpack_diff": {
                    "asset_paths": ["../../etc/passwd"],
                    "arbitrary_scripts": True,
                },
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_bad, 200)
        self.assertFalse(r_bad["result"]["allowed_immediately"])
        self.assertEqual(r_bad["result"]["migration_mode"], "blocked_unsafe_worldpack")
        self.assertEqual(len(r_bad["result"]["blocking_reasons"]), 2)

    def test_20_strict_400_invalid_fields_unknown_keys_and_forged_identity_or_mood(self) -> None:
        bad_payloads = [
            {},
            {"action": "unknown_magic_action"},
            {"action": "run_trylive_hour", "unknown_extra_param": 123},
            {"action": "run_trylive_hour", "actor_side": "subject"},
            {"action": "run_trylive_hour", "uid": "u_1"},
            {"action": "run_trylive_hour", "owner": "chiyo"},
            {"action": "run_trylive_hour", "mood_delta": 10},
            {"action": "run_trylive_hour", "affection_score": 99},
            {"action": "run_trylive_hour", "artifact_id": "art_1"},
            {"action": "check_scene_prose_gaps", "prose": "   "},
            {"action": "run_trylive_hour", " padded_key ": "go_out_and_return"},
        ]
        for payload in bad_payloads:
            code, body = view.mutate_v04_builder_trylive_view(
                self.day_root,
                payload,
                actor_side="partner",
                now_iso=NOW_ISO,
            )
            self.assertEqual(code, 400, f"Expected 400 for {payload}, got {code}: {body}")
            self.assertFalse(body["ok"])
            self.assertEqual(body["reason_code"], "INVALID_FIELDS")

    def test_21_strict_403_forbidden_actor_side_and_partner_subject_allowed(self) -> None:
        for bad_actor in ("admin", "guest", "system", "stranger", "", None):
            code, body = view.mutate_v04_builder_trylive_view(
                self.day_root,
                {"action": "run_trylive_hour", "preset_id": "go_out_and_return"},
                actor_side=bad_actor,  # type: ignore[arg-type]
                now_iso=NOW_ISO,
            )
            self.assertEqual(code, 403, f"Expected 403 for actor_side={bad_actor!r}")
            self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        for good_actor in ("partner", "subject"):
            code, body = view.mutate_v04_builder_trylive_view(
                self.day_root,
                {"action": "run_trylive_hour", "preset_id": "go_out_and_return"},
                actor_side=good_actor,
                now_iso=NOW_ISO,
            )
            self.assertEqual(code, 200)
            self.assertTrue(body["ok"])

    def test_22_operation_key_idempotency_conflict_409_and_expected_revision_cas(self) -> None:
        c1, r1 = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "check_scene_prose_gaps",
                "prose": "窗边有一张长椅",
                "operation_key": "op_prose_001",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["revision"], 1)
        self.assertFalse(r1["already_applied"])
        self.assertEqual(r1["command_status"], "APPLIED")

        # Same operation_key + same payload -> idempotent duplicate, revision stays 1
        c2, r2 = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "check_scene_prose_gaps",
                "prose": "窗边有一张长椅",
                "operation_key": "op_prose_001",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c2, 200)
        self.assertEqual(r2["revision"], 1)
        self.assertTrue(r2["already_applied"])
        self.assertEqual(r2["command_status"], "DUPLICATE")

        # Same operation_key + different payload -> 409 OPERATION_KEY_CONFLICT
        c_conf, r_conf = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "check_scene_prose_gaps",
                "prose": "门通向后巷仓库",
                "operation_key": "op_prose_001",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_conf, 409)
        self.assertEqual(r_conf["reason_code"], "OPERATION_KEY_CONFLICT")

        # Wrong expected_revision -> 409 REVISION_CONFLICT
        c_rev, r_rev = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "inspect_why_changed",
                "object_id": "linen_cloth",
                "expected_revision": 99,
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_rev, 409)
        self.assertEqual(r_rev["reason_code"], "REVISION_CONFLICT")

    def test_23_sandbox_file_permissions_0600_and_zero_pollution_of_world_and_life_dirs(self) -> None:
        actions = [
            {"action": "audit_routes_or_rehearse", "blocked_edges": ["route_street_bookstore"]},
            {"action": "check_scene_prose_gaps", "prose": "窗边有一张长椅，门通向后巷仓库"},
            {"action": "run_trylive_hour", "preset_id": "yard_rain_rescue", "injections": ["seat_taken"]},
            {"action": "inspect_why_changed", "object_id": "linen_cloth"},
            {"action": "preview_world_modification", "mod_kind": "delete_place", "target_id": "porch"},
        ]
        for payload in actions:
            code, _ = view.mutate_v04_builder_trylive_view(
                self.day_root,
                payload,
                actor_side="partner",
                now_iso=NOW_ISO,
            )
            self.assertEqual(code, 200)

        sandbox_path = engine.builder_sandbox_state_path(self.day_root)
        self.assertTrue(sandbox_path.is_file())
        self.assertFalse((self.day_root / "world").exists())
        self.assertFalse((self.day_root / "life").exists())
        self.assertFalse((self.day_root / "memory").exists())

        if os.name == "posix":
            mode = stat.S_IMODE(sandbox_path.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_24_frontend_place_aliases_yard_street_and_blank_optional_fields(self) -> None:
        c_prose, r_prose = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "check_scene_prose_gaps",
                "place_id": "yard",
                "prose": "院子里有一张长椅和晾衣绳",
                "operation_key": "",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_prose, 200, r_prose)
        self.assertEqual(r_prose["result"]["place_id"], "courtyard")
        gaps_map = {g["target"]: g["severity"] for g in r_prose["result"]["gaps"]}
        self.assertEqual(gaps_map["长椅"], "ready")
        self.assertEqual(gaps_map["晾衣绳"], "ready")

        c_mod, r_mod = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "delete_place",
                "target_id": "yard",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_mod, 200, r_mod)
        self.assertEqual(r_mod["result"]["target_id"], "courtyard")
        self.assertFalse(r_mod["result"]["allowed_immediately"])
        self.assertEqual(r_mod["result"]["migration_mode"], "blocked_referenced_place")

    def test_25_desktop_ui_route_and_object_aliases_old_bridge_home_to_bookstore_park_bench(self) -> None:
        c_aud, r_aud = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "audit_routes_or_rehearse",
                "route_id": "home_to_bookstore",
                "blocked_edges": ["old_bridge"],
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_aud, 200, r_aud)
        self.assertIn("riverside_walk", r_aud["result"]["isolated_places"])
        self.assertEqual(r_aud["result"]["rehearsal"]["start_place_id"], "home")
        self.assertEqual(r_aud["result"]["rehearsal"]["destination_place_id"], "bookstore")

        c_blk, r_blk = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "block_route",
                "target_id": "old_bridge",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_blk, 200, r_blk)
        self.assertFalse(r_blk["result"]["allowed_immediately"])
        self.assertIn("riverside_walk", r_blk["result"]["newly_isolated_places"])

        c_bch, r_bch = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "preview_world_modification",
                "mod_kind": "move_bench",
                "target_id": "park_bench",
                "to_place_id": "courtyard",
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_bch, 200, r_bch)
        self.assertEqual(r_bch["result"]["target_id"], "riverside_bench")
        self.assertEqual(r_bch["result"]["from_place_id"], "riverside_walk")
        self.assertEqual(r_bch["result"]["to_place_id"], "courtyard")

    def test_26_cross_engine_live_neighborhood_state_no_duplicate_yard_or_riverside_islands(self) -> None:
        from components.phone import v04_neighborhood_view as nb_view

        c1, r1 = nb_view.mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "hang_or_collect_cloth", "operation": "wet_and_hang", "spot_id": "porch_hook"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c1, 200, r1)
        c2, r2 = nb_view.mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "water_or_move_plant", "operation": "water"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c2, 200, r2)
        c3, r3 = nb_view.mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "post_public_notice", "title": "门廊备伞便条", "body": "傍晚风大记得拿伞"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c3, 200, r3)

        c_aud, r_aud = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {
                "action": "audit_routes_or_rehearse",
                "route_id": "home_to_bookstore",
                "blocked_edges": ["old_bridge"],
            },
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_aud, 200, r_aud)
        self.assertEqual(r_aud["result"]["isolated_places"], ["riverside_walk"])

        c_cloth, r_cloth = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {"action": "inspect_why_changed", "object_id": "linen_cloth"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_cloth, 200, r_cloth)
        self.assertEqual(r_cloth["result"]["builder_view"]["current_place_id"], "porch")
        self.assertEqual(r_cloth["result"]["builder_view"]["current_support_id"], "porch_hook")

        c_box, r_box = view.mutate_v04_builder_trylive_view(
            self.day_root,
            {"action": "inspect_why_changed", "object_id": "crafted_box"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(c_box, 200, r_box)
        self.assertEqual(r_box["result"]["builder_view"]["material_conservation"]["input_units"], 8)
        self.assertEqual(r_box["result"]["builder_view"]["material_conservation"]["used_divider_units"], 0)
        self.assertEqual(r_box["result"]["builder_view"]["material_conservation"]["remaining_units"], 8)


if __name__ == "__main__":
    unittest.main()
