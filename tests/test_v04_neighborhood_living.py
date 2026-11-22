#!/usr/bin/env python3
"""Comprehensive unit tests for v0.4 Neighborhood Living (Batches A–F, Ch. 38–43, 45–47)."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(_WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.phone.v04_neighborhood_view import (  # noqa: E402
    PARTNER_ALLOWED_ACTIONS,
    SUBJECT_ALLOWED_ACTIONS,
    V04_NEIGHBORHOOD_SCHEMA,
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
    v04_neighborhood_state_path,
)
from components.world.scripts.v04_neighborhood_living import (  # noqa: E402
    advance_v04_neighborhood_processes,
)

FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return out


class TestV04NeighborhoodLiving(unittest.TestCase):
    """28 tests covering zero-write GET, drying, plant, journey, crafts, bookstore, notices, booklets & RBAC."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v04-neighborhood-test-")
        self.day_root = Path(self._tmp.name)
        self.t0 = "2026-11-13T10:30:00+08:00"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # =========================================================================
    # 1. Zero-Write GET, Schema Alignment, Character Neutrality & Storage Safety
    # =========================================================================

    def test_01_empty_dir_pure_get_and_advance_are_100_percent_zero_disk_write(self) -> None:
        self.assertEqual(list(self.day_root.iterdir()), [])

        code_p, body_p = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=False, now_iso=self.t0
        )
        code_s, body_s = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso=self.t0
        )
        adv = advance_v04_neighborhood_processes(self.day_root, now_iso=self.t0)

        self.assertEqual(list(self.day_root.iterdir()), [])
        self.assertEqual(code_p, 200)
        self.assertEqual(code_s, 200)
        self.assertEqual(adv, {"ok": True, "advanced": False, "llm_calls": 0})

        for body, expected_persp in ((body_p, "partner_default"), (body_s, "her_private")):
            self.assertTrue(body["ok"])
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["schema"], V04_NEIGHBORHOOD_SCHEMA)
            self.assertTrue(body["read_only"])
            self.assertEqual(body["perspective"], expected_persp)
            self.assertEqual(body["revision"], 0)
            self.assertEqual(body["llm_calls"], 0)
            self.assertEqual(body["identity"]["pronouns"], "TA")
            self.assertEqual(body["identity"]["display_name"], "TA")

            # Check both 7 core keys and 11 UI/Hermes aligned keys exist in data and top-level
            required_keys = (
                "environment",
                "places",
                "journey",
                "drying_items",
                "potted_plant",
                "craft_bench",
                "public_life",
                "revision",
                "identity",
                "weather_and_ground",
                "courtyard",
                "public_notices",
                "roadblocks",
                "shared_shelf",
                "crafts",
                "bookstore",
                "place_booklets",
            )
            for k in required_keys:
                self.assertIn(k, body["data"], f"Missing key in data: {k}")
                self.assertIn(k, body, f"Missing mirrored top-level key: {k}")
            self.assertIn("plants", body["data"]["courtyard"])
            self.assertIn("drying_rack", body["data"]["courtyard"])

    def test_02_atomic_0600_state_file_and_populated_get_zero_mtime_change(self) -> None:
        code, res = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "set_local_weather", "weather": "clear", "airflow": "strong"},
            actor_side="partner",
            now_iso=self.t0,
        )
        self.assertEqual(code, 200)
        self.assertEqual(res["status"], "applied")

        spath = v04_neighborhood_state_path(self.day_root)
        self.assertTrue(spath.is_file())
        if os.name == "posix":
            mode = stat.S_IMODE(spath.stat().st_mode)
            self.assertEqual(mode, 0o600)

        snap_before = _tree_snapshot(self.day_root)
        g_code, g_body = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-13T12:00:00+08:00"
        )
        snap_after = _tree_snapshot(self.day_root)
        self.assertEqual(g_code, 200)
        self.assertEqual(g_body["revision"], 1)
        self.assertEqual(snap_before, snap_after)

    def test_03_corrupted_json_and_symlink_fail_closed_without_overwrite(self) -> None:
        spath = v04_neighborhood_state_path(self.day_root)
        spath.parent.mkdir(parents=True, exist_ok=True)
        bad_payload = "{corrupted-json-do-not-overwrite"
        spath.write_text(bad_payload, encoding="utf-8")

        r_code, r_err = read_v04_neighborhood_view(self.day_root, now_iso=self.t0)
        self.assertEqual(r_code, 503)
        self.assertEqual(r_err["reason_code"], "STATE_CORRUPTED")

        m_code, m_err = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "set_local_weather", "weather": "rain"},
            actor_side="partner",
            now_iso=self.t0,
        )
        self.assertEqual(m_code, 503)
        self.assertEqual(m_err["reason_code"], "STATE_CORRUPTED")
        self.assertEqual(spath.read_text(encoding="utf-8"), bad_payload)

        if hasattr(os, "symlink") and os.name == "posix":
            spath.unlink()
            target = self.day_root / "outside.json"
            target.write_text("{}", encoding="utf-8")
            os.symlink(target, spath)
            sym_code, sym_err = read_v04_neighborhood_view(self.day_root, now_iso=self.t0)
            self.assertEqual(sym_code, 503)
            self.assertEqual(sym_err["reason_code"], "STATE_SYMLINK_REFUSED")

    # =========================================================================
    # 2. Batch A: Three-Ring Topology, Piecewise Drying & Tolerant Potted Plant
    # =========================================================================

    def test_04_three_ring_places_initial_topology_and_knowledge_status(self) -> None:
        _, body = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso=self.t0
        )
        places = body["data"]["places"]
        self.assertEqual(set(places.keys()), {
            "home", "porch", "yard", "street_corner", "bookstore", "riverside"
        })
        self.assertEqual(places["home"]["ring"], "home")
        self.assertEqual(places["home"]["shelter"], "indoor")
        self.assertEqual(places["porch"]["ring"], "doorstep")
        self.assertEqual(places["porch"]["shelter"], "sheltered")
        self.assertEqual(places["yard"]["shelter"], "open_air")
        self.assertEqual(places["bookstore"]["ring"], "neighborhood")
        self.assertEqual(places["bookstore"]["open_window"], "10:00-19:00")
        self.assertTrue(places["bookstore"]["is_open_now"])

        # Author map vs Character known map
        for visited_p in ("home", "porch", "yard", "street_corner"):
            self.assertEqual(places[visited_p]["knowledge_status"], "visited")
        self.assertEqual(places["bookstore"]["knowledge_status"], "told_clue")
        self.assertEqual(places["riverside"]["knowledge_status"], "unknown")

    def test_05_open_air_yard_drying_then_rain_cuts_segment_and_rewets(self) -> None:
        # Hang wet cloth (75.0) on yard_line at 10:00 in clear medium weather (-28.0/hr)
        c1, r1 = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "hang_or_collect_cloth", "operation": "hang", "target_support": "yard_line"},
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["drying_items"]["wet_cloth_main"]["segment_count"], 1)

        # At 11:00 (1h clear): moisture = 75.0 - 28.0 = 47.0 -> switch weather to rain (+45.0/hr)
        c2, r2 = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "set_local_weather", "weather": "rain"},
            actor_side="partner",
            now_iso="2026-11-13T11:00:00+08:00",
        )
        self.assertEqual(c2, 200)
        cloth_11 = r2["drying_items"]["wet_cloth_main"]
        self.assertAlmostEqual(cloth_11["moisture"], 47.0, places=3)
        self.assertEqual(cloth_11["segment_count"], 2)

        # At 11:30 (0.5h rain on open_air yard_line): 47.0 + 22.5 = 69.5 (wetted by rain!)
        _, view_1130 = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-13T11:30:00+08:00"
        )
        cloth_1130 = view_1130["drying_items"]["wet_cloth_main"]
        self.assertAlmostEqual(cloth_1130["moisture"], 69.5, places=3)
        self.assertEqual(cloth_1130["moisture_band"], "wet")

    def test_06_sheltered_porch_hook_never_wets_in_rain_but_dries_slower_than_clear(self) -> None:
        with tempfile.TemporaryDirectory() as clear_dir, tempfile.TemporaryDirectory() as rain_dir:
            for d, w in ((clear_dir, "clear"), (rain_dir, "rain")):
                mutate_v04_neighborhood_view(
                    d,
                    {"action": "set_local_weather", "weather": w, "airflow": "medium"},
                    actor_side="partner",
                    now_iso="2026-11-13T10:00:00+08:00",
                )
                mutate_v04_neighborhood_view(
                    d,
                    {"action": "hang_or_collect_cloth", "target_support": "porch_hook"},
                    actor_side="subject",
                    now_iso="2026-11-13T10:00:00+08:00",
                )

            _, v_clear = read_v04_neighborhood_view(clear_dir, now_iso="2026-11-13T12:00:00+08:00")
            _, v_rain = read_v04_neighborhood_view(rain_dir, now_iso="2026-11-13T12:00:00+08:00")

            m_clear = v_clear["drying_items"]["wet_cloth_main"]["moisture"]
            m_rain = v_rain["drying_items"]["wet_cloth_main"]["moisture"]

            # Porch in rain STILL dries (< 75.0), never gets wetted, and dries slower than clear
            self.assertLess(m_rain, 75.0)
            self.assertGreater(m_rain, m_clear)

    def test_07_dry_cloth_enters_sleeping_dry_and_stops_idle_advancement(self) -> None:
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "hang_or_collect_cloth", "target_support": "yard_line"},
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        # 3 hours in clear sun (-28/hr): 75 -> <= 5.0 -> sleeping_dry=True
        adv1 = advance_v04_neighborhood_processes(
            self.day_root, now_iso="2026-11-13T13:00:00+08:00"
        )
        self.assertTrue(adv1["advanced"])

        _, view = read_v04_neighborhood_view(
            self.day_root, now_iso="2026-11-13T13:00:00+08:00"
        )
        cloth = view["drying_items"]["wet_cloth_main"]
        self.assertTrue(cloth["sleeping_dry"])
        self.assertEqual(cloth["moisture_band"], "dry")
        self.assertLessEqual(cloth["moisture"], 5.0)

        # Further process ticks while dry & clear must NOT spin or bump revision (plant also stabilized if same time or check cloth)
        # Move plant anchor or check cloth stays sleeping_dry and moisture == 5.0
        _, view_later = read_v04_neighborhood_view(
            self.day_root, now_iso="2026-11-13T16:00:00+08:00"
        )
        cloth_later = view_later["drying_items"]["wet_cloth_main"]
        self.assertTrue(cloth_later["sleeping_dry"])
        self.assertEqual(cloth_later["moisture"], 5.0)
        self.assertEqual(cloth_later["dried_at"], cloth["dried_at"])

        # Re-wetting wakes it up from sleeping_dry
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "hang_or_collect_cloth", "operation": "wet", "moisture": 82.0},
            actor_side="subject",
            now_iso="2026-11-13T16:10:00+08:00",
        )
        _, view_wet = read_v04_neighborhood_view(
            self.day_root, now_iso="2026-11-13T16:10:00+08:00"
        )
        self.assertFalse(view_wet["drying_items"]["wet_cloth_main"]["sleeping_dry"])
        self.assertEqual(view_wet["drying_items"]["wet_cloth_main"]["moisture_band"], "wet")

    def test_08_high_frequency_get_matches_multi_step_and_one_shot_advance_across_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            for r in (root_a, root_b):
                mutate_v04_neighborhood_view(
                    r,
                    {"action": "hang_or_collect_cloth", "target_support": "porch_hook"},
                    actor_side="subject",
                    now_iso="2026-11-13T09:00:00+08:00",
                )

            # root_a: high-frequency GET reads + multiple uneven advances (simulating restarts)
            for minute in (7, 19, 33, 51, 77, 105):
                ts = f"2026-11-13T{9 + minute // 60:02d}:{minute % 60:02d}:00+08:00"
                read_v04_neighborhood_view(root_a, now_iso=ts)
                if minute in (19, 77):
                    advance_v04_neighborhood_processes(root_a, now_iso=ts)
            advance_v04_neighborhood_processes(root_a, now_iso="2026-11-13T11:15:00+08:00")

            # root_b: single one-shot advance at 11:15
            advance_v04_neighborhood_processes(root_b, now_iso="2026-11-13T11:15:00+08:00")

            _, va = read_v04_neighborhood_view(root_a, now_iso="2026-11-13T11:15:00+08:00")
            _, vb = read_v04_neighborhood_view(root_b, now_iso="2026-11-13T11:15:00+08:00")

            ca = va["drying_items"]["wet_cloth_main"]
            cb = vb["drying_items"]["wet_cloth_main"]
            self.assertAlmostEqual(ca["moisture"], cb["moisture"], places=5)
            self.assertEqual(ca["moisture_band"], cb["moisture_band"])
            self.assertEqual(ca["segment_count"], cb["segment_count"])

    def test_09_potted_plant_never_dies_offline_recovers_on_water_and_supports_public_care(self) -> None:
        # Anchor plant at Day 1
        c1, _ = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "water_or_move_plant", "operation": "move", "target_place": "yard"},
            actor_side="subject",
            now_iso="2026-11-01T10:00:00+08:00",
        )
        self.assertEqual(c1, 200)

        # 15 days offline without water -> enters wilting_recoverable, NEVER dies!
        _, wilted_view = read_v04_neighborhood_view(
            self.day_root, now_iso="2026-11-16T10:00:00+08:00"
        )
        plant_w = wilted_view["potted_plant"]
        self.assertTrue(plant_w["never_dies_offline"])
        self.assertEqual(plant_w["stage"], "wilting_recoverable")

        # Watering immediately recovers it from wilting_recoverable!
        c2, recovered = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "water_or_move_plant", "operation": "water", "water_amount": 80},
            actor_side="partner",
            now_iso="2026-11-16T10:05:00+08:00",
        )
        self.assertEqual(c2, 200)
        self.assertIn(recovered["potted_plant"]["stage"], ("seedling", "leafy", "thriving"))

        # Turn on public_care_enabled -> another 15 days away keeps plant healthy
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "water_or_move_plant", "operation": "toggle_public_care", "public_care_enabled": True},
            actor_side="subject",
            now_iso="2026-11-16T10:10:00+08:00",
        )
        _, cared_view = read_v04_neighborhood_view(
            self.day_root, now_iso="2026-12-01T10:10:00+08:00"
        )
        self.assertGreaterEqual(cared_view["potted_plant"]["hydration"], 55.0)
        self.assertIn(cared_view["potted_plant"]["stage"], ("leafy", "thriving"))

    # =========================================================================
    # 3. Batch B: Branching Journey, Turn-Back, Non-Omniscient Roadblock & Map
    # =========================================================================

    def test_10_in_transit_never_fakes_early_arrival_and_moves_carried_items_via_parent_chain(self) -> None:
        # Cut a card and carry both cloth and card on journey to bookstore via eaves_walkway
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": "card_note_1"},
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        # home -> porch (1m) -> yard (1m) -> street_corner via eaves_walkway (7m) -> bookstore (5m) = 14m
        c_go, r_go = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "start_journey",
                "target_place": "bookstore",
                "preferred_branch": "eaves_walkway",
                "carry_item_ids": ["wet_cloth_main", "card_note_1"],
            },
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(c_go, 200)
        self.assertEqual(r_go["journey"]["state"], "in_transit")
        self.assertIsNone(r_go["journey"]["current_place_id"])

        # Mid-journey at 10:06 (still on eaves_walkway): NOT arrived! Carried items track transit!
        _, mid = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-13T10:06:00+08:00"
        )
        self.assertEqual(mid["journey"]["state"], "in_transit")
        self.assertIsNone(mid["journey"]["current_place_id"])
        self.assertEqual(mid["journey"]["active_route_id"], "eaves_walkway")
        self.assertTrue(mid["drying_items"]["wet_cloth_main"]["effective_place_id"].startswith("in_transit:"))
        self.assertTrue(mid["craft_bench"]["cards_by_id"]["card_note_1"]["effective_place_id"].startswith("in_transit:"))

        # Arrive at 10:14 -> bookstore upgraded from told_clue to visited, carried items at bookstore
        c_arr, r_arr = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T10:14:00+08:00",
        )
        self.assertEqual(c_arr, 200)
        self.assertEqual(r_arr["journey"]["state"], "arrived")
        self.assertEqual(r_arr["journey"]["current_place_id"], "bookstore")
        self.assertEqual(r_arr["places"]["bookstore"]["knowledge_status"], "visited")
        self.assertIsNotNone(r_arr["places"]["bookstore"]["last_visited_at"])
        self.assertEqual(r_arr["drying_items"]["wet_cloth_main"]["effective_place_id"], "bookstore")
        self.assertEqual(r_arr["craft_bench"]["cards_by_id"]["card_note_1"]["effective_place_id"], "bookstore")
        self.assertEqual(r_arr["journey"]["carried_item_ids"], ["wet_cloth_main", "card_note_1"])

    def test_11_midway_turn_back_preserves_walked_time_and_never_teleports(self) -> None:
        # Move to yard first (2 mins: home -> porch -> yard)
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "yard"},
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T10:02:00+08:00",
        )

        # Start 7-min eaves_walkway from yard -> street_corner at 10:10
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "street_corner", "route_id": "eaves_walkway"},
            actor_side="subject",
            now_iso="2026-11-13T10:10:00+08:00",
        )

        # At 10:13 (3 mins = 180s walked), call turn_back!
        c_tb, r_tb = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "turn_back"},
            actor_side="subject",
            now_iso="2026-11-13T10:13:00+08:00",
        )
        self.assertEqual(c_tb, 200)
        # Must NOT teleport back to yard at 10:13!
        self.assertEqual(r_tb["journey"]["state"], "in_transit")
        self.assertTrue(r_tb["journey"]["is_turning_back"])
        self.assertIsNone(r_tb["journey"]["current_place_id"])
        self.assertEqual(r_tb["journey"]["target_place_id"], "yard")
        self.assertEqual(r_tb["journey"]["expected_arrival_at"], "2026-11-13T10:16:00+08:00")

        # Still walking back at 10:14:30
        _, mid_back = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-13T10:14:30+08:00"
        )
        self.assertEqual(mid_back["journey"]["state"], "in_transit")

        # Arrives back at yard at 10:16:00 with total walked = 120s (to yard) + 180s (out) + 180s (back) = 480s
        _, back_done = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T10:16:00+08:00",
        )
        self.assertEqual(back_done["journey"]["state"], "arrived")
        self.assertEqual(back_done["journey"]["current_place_id"], "yard")
        self.assertAlmostEqual(back_done["journey"]["accumulated_walk_seconds"], 480.0, places=1)

    def test_12_roadblock_non_omniscience_only_discovered_at_route_boundary_or_via_notice(self) -> None:
        # Operator/partner blocks open_shortcut while subject is indoors at home
        c_rb, _ = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "set_roadblock", "route_id": "open_shortcut", "blocked": True, "reason": "石阶修补"},
            actor_side="partner",
            now_iso="2026-11-13T09:00:00+08:00",
        )
        self.assertEqual(c_rb, 200)

        # Subject view MUST NOT omnisciently know open_shortcut is blocked yet!
        _, s_view = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-13T09:05:00+08:00"
        )
        self.assertEqual(s_view["journey"]["discovered_roadblocks"], [])
        self.assertEqual(s_view["roadblocks"], [])

        # Subject departs home -> street_corner (unaware of roadblock on open_shortcut)
        c_go, r_go = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "street_corner", "route_id": "open_shortcut"},
            actor_side="subject",
            now_iso="2026-11-13T09:10:00+08:00",
        )
        self.assertEqual(c_go, 200)
        self.assertEqual(r_go["journey"]["state"], "in_transit")

        # After 2 mins (reaching yard, entrance of open_shortcut), subject sees barrier & halts at yard!
        _, r_halt = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T09:12:00+08:00",
        )
        self.assertEqual(r_halt["journey"]["state"], "paused_at_node")
        self.assertEqual(r_halt["journey"]["current_place_id"], "yard")
        self.assertEqual(r_halt["journey"]["halt_reason"], "ROADBLOCK_ENCOUNTERED")
        self.assertIn("open_shortcut", r_halt["journey"]["discovered_roadblocks"])

        # Now subject naturally takes eaves_walkway (auto-selected because open_shortcut is known blocked!)
        c_detour, r_detour = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "street_corner"},
            actor_side="subject",
            now_iso="2026-11-13T09:13:00+08:00",
        )
        self.assertEqual(c_detour, 200)
        self.assertEqual(r_detour["journey"]["active_route_id"], "eaves_walkway")

    # =========================================================================
    # 4. Batch C: Craft Integer Conservation, Inscription vs Draft & Semi-Finished Box
    # =========================================================================

    def test_13_paper_integer_conservation_and_409_insufficient_material(self) -> None:
        _, init_v = read_v04_neighborhood_view(self.day_root, has_subject_capability=True, now_iso=self.t0)
        self.assertEqual(init_v["craft_bench"]["paper_units"], 8)
        self.assertEqual(init_v["craft_bench"]["scrap_units"], 0)
        self.assertTrue(init_v["craft_bench"]["integer_conservation_holds"])

        # Cut 1 card consuming 3 paper_units with 1 scrap_loss -> card=2, scrap=1, paper=5
        c1, r1 = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "cut_card",
                "units": 3,
                "scrap_loss": 1,
                "card_id": "card_thick",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c1, 200)
        cb = r1["craft_bench"]
        self.assertEqual(cb["paper_units"], 5)
        self.assertEqual(cb["scrap_units"], 1)
        self.assertEqual(cb["consumed_in_cards_units"], 2)
        self.assertTrue(cb["integer_conservation_holds"])

        # Requesting 6 units when only 5 remain -> 409 INSUFFICIENT_MATERIAL, state unchanged
        c_err, err = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card", "units": 6},
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_err, 409)
        self.assertEqual(err["reason_code"], "INSUFFICIENT_MATERIAL")

        # Restock 4 units -> paper_units=9, total_stocked_units=12, conservation holds
        c_re, r_re = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "restock_paper", "units": 4},
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_re, 200)
        self.assertEqual(r_re["craft_bench"]["paper_units"], 9)
        self.assertEqual(r_re["craft_bench"]["total_stocked_units"], 12)
        self.assertTrue(r_re["craft_bench"]["integer_conservation_holds"])

    def test_14_card_inscribe_locks_revision_1_decouples_draft_and_transcribe_increments(self) -> None:
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": "c_poem"},
            actor_side="subject",
            now_iso=self.t0,
        )
        c_ins, r_ins = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "c_poem",
                "text": "院子雨停后的木头气味",
                "draft_text": "初稿备注",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_ins, 200)
        card = r_ins["craft_bench"]["cards_by_id"]["c_poem"]
        self.assertEqual(card["inscribed_revision"], 1)
        self.assertEqual(card["inscribed_text"], "院子雨停后的木头气味")

        # Second inscribe_card must be rejected (must use transcribe_card)
        c_dup_ins, err_ins = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "c_poem",
                "text": "试图直接覆盖",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_dup_ins, 409)
        self.assertEqual(err_ins["reason_code"], "CARD_ALREADY_INSCRIBED")

        # Editing digital/notebook draft must NOT alter the physical inscribed_text!
        c_dr, r_dr = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "edit_card_draft",
                "card_id": "c_poem",
                "draft_text": "改写草稿：檐角滴水与青苔",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_dr, 200)
        card_after_draft = r_dr["craft_bench"]["cards_by_id"]["c_poem"]
        self.assertEqual(card_after_draft["inscribed_revision"], 1)
        self.assertEqual(card_after_draft["inscribed_text"], "院子雨停后的木头气味")
        self.assertEqual(card_after_draft["draft_text"], "改写草稿：檐角滴水与青苔")

        # Explicit transcribe_card updates card face and increments inscribed_revision -> 2
        c_tr, r_tr = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "transcribe_card",
                "card_id": "c_poem",
                "text": "檐角滴水与青苔（重誊卡面）",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_tr, 200)
        card_tr = r_tr["craft_bench"]["cards_by_id"]["c_poem"]
        self.assertEqual(card_tr["inscribed_revision"], 2)
        self.assertEqual(card_tr["inscribed_text"], "檐角滴水与青苔（重誊卡面）")

    def test_15_card_organizer_box_preserves_semifinished_dividers_on_set_aside_and_supports_repair_patch(self) -> None:
        # Cut divider (consumes 2 paper_units)
        c1, r1 = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "box_step", "target_step": "divider_cut", "units": 2},
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["craft_bench"]["paper_units"], 6)
        self.assertEqual(r1["craft_bench"]["card_organizer_box"]["dividers_cut"], 1)

        # Trial fit slightly oversized -> set aside on desk edge! Dividers & material preserved!
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "box_step",
                "target_step": "trial_fitted",
                "fit_note": "十字隔板略偏宽半毫米",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        c_sa, r_sa = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "box_step", "target_step": "set_aside"},
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_sa, 200)
        box = r_sa["craft_bench"]["card_organizer_box"]
        self.assertEqual(box["step"], "set_aside")
        self.assertEqual(box["dividers_cut"], 1)
        self.assertEqual(len(box["dividers"]), 1)
        self.assertEqual(box["fit_note"], "十字隔板略偏宽半毫米")
        self.assertTrue(r_sa["craft_bench"]["integer_conservation_holds"])

        # Apply visible repair patch & finish box
        c_rp, r_rp = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "repair_patch",
                "patch_note": "用牛皮纸条加固隔板折角",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_rp, 200)
        self.assertTrue(r_rp["craft_bench"]["card_organizer_box"]["visible_patch"])
        self.assertEqual(r_rp["craft_bench"]["card_organizer_box"]["patch_count"], 1)

        c_fin, r_fin = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "box_step", "target_step": "finished"},
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_fin, 200)
        self.assertEqual(r_fin["craft_bench"]["card_organizer_box"]["step"], "finished")

    # =========================================================================
    # 5. Batch D/E/F: Bookstore 4 Services, Shared Shelf, Notices & Booklet
    # =========================================================================

    def test_16_bookstore_browse_excerpt_only_and_reserve_auto_expires(self) -> None:
        # Browse during open hours (11:00) -> cover, TOC & sample_excerpt only
        c_br, r_br = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "bookstore_service", "service": "browse_book", "book_id": "book_botanical_plates"},
            actor_side="subject",
            now_iso="2026-11-13T11:00:00+08:00",
        )
        self.assertEqual(c_br, 200)
        res = r_br["result"]
        self.assertIn("cover_summary", res)
        self.assertIn("toc", res)
        self.assertIn("sample_excerpt", res)
        self.assertFalse(res["full_text_dumped"])

        # Reserve for 3 hours (expires 14:00) -> not paid, ownership not transferred
        c_rv, r_rv = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "reserve_book",
                "book_id": "book_botanical_plates",
                "hold_hours": 3,
            },
            actor_side="subject",
            now_iso="2026-11-13T11:00:00+08:00",
        )
        self.assertEqual(c_rv, 200)
        bk = r_rv["bookstore"]["books_by_id"]["book_botanical_plates"]
        self.assertEqual(bk["status"], "reserved")
        self.assertFalse(bk["paid"])
        self.assertFalse(bk["ownership_transferred"])

        # At 14:30 (past 14:00), reservation automatically expires back to available!
        _, v_after = read_v04_neighborhood_view(
            self.day_root, now_iso="2026-11-13T14:30:00+08:00"
        )
        self.assertEqual(
            v_after["bookstore"]["books_by_id"]["book_botanical_plates"]["status"], "available"
        )

    def test_17_bookstore_borrow_and_closed_hours_return_goes_to_return_box_pending(self) -> None:
        # Borrow at 15:00 (open)
        c_bo, r_bo = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "bookstore_service", "service": "borrow_book", "book_id": "book_paper_joinery"},
            actor_side="subject",
            now_iso="2026-11-13T15:00:00+08:00",
        )
        self.assertEqual(c_bo, 200)
        bk = r_bo["bookstore"]["books_by_id"]["book_paper_joinery"]
        self.assertEqual(bk["item_owner"], "bookstore")
        self.assertEqual(bk["holder"], "subject")

        # Return at 20:30 (after 19:00 closing!) -> MUST enter return_box_pending, NEVER fake shopkeeper_received!
        c_ret, r_ret = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "bookstore_service", "service": "return_book", "book_id": "book_paper_joinery"},
            actor_side="subject",
            now_iso="2026-11-13T20:30:00+08:00",
        )
        self.assertEqual(c_ret, 200)
        bk_ret = r_ret["bookstore"]["books_by_id"]["book_paper_joinery"]
        self.assertEqual(bk_ret["status"], "return_box_pending")
        self.assertEqual(bk_ret["return_receipt_kind"], "return_box_pending")
        self.assertFalse(bk_ret["shopkeeper_received"])

        # Borrow another book at 11:00 next day and return at 16:00 (open, in person) -> shopkeeper_received
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "bookstore_service", "service": "borrow_book", "book_id": "book_river_sketches"},
            actor_side="subject",
            now_iso="2026-11-14T11:00:00+08:00",
        )
        _, r_open_ret = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "bookstore_service", "service": "return_book", "book_id": "book_river_sketches"},
            actor_side="subject",
            now_iso="2026-11-14T16:00:00+08:00",
        )
        bk_open = r_open_ret["bookstore"]["books_by_id"]["book_river_sketches"]
        self.assertEqual(bk_open["status"], "available")
        self.assertEqual(bk_open["return_receipt_kind"], "shopkeeper_received")
        self.assertTrue(bk_open["shopkeeper_received"])

    def test_18_bookstore_submit_display_card_publishes_exact_version_without_leaking_draft(self) -> None:
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": "card_disp"},
            actor_side="subject",
            now_iso="2026-11-13T11:00:00+08:00",
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "card_disp",
                "title": "画册荐书签",
                "text": "第三章关于石阶青苔的配图很耐看。",
                "draft_text": "私人练习废稿：这句不要贴在店里",
            },
            actor_side="subject",
            now_iso="2026-11-13T11:10:00+08:00",
        )
        c_sub, _ = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "bookstore_service", "service": "submit_display_card", "card_id": "card_disp"},
            actor_side="subject",
            now_iso="2026-11-13T11:20:00+08:00",
        )
        self.assertEqual(c_sub, 200)

        # Partner default view sees displayed card text, NEVER private draft_text!
        _, p_view = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-13T11:30:00+08:00"
        )
        p_card = p_view["craft_bench"]["cards_by_id"]["card_disp"]
        self.assertTrue(p_card["shared_or_displayed"])
        self.assertFalse(p_card["withheld"])
        self.assertEqual(p_card["inscribed_text"], "第三章关于石阶青苔的配图很耐看。")
        self.assertIsNone(p_card["draft_text"])
        self.assertTrue(p_card["draft_withheld"])
        self.assertEqual(len(p_view["bookstore"]["display_cards"]), 1)
        self.assertEqual(
            p_view["bookstore"]["display_cards"][0]["displayed_text"],
            "第三章关于石阶青苔的配图很耐看。",
        )

    def test_19_shared_shelf_loan_borrow_extension_and_return_zero_affection_coupling(self) -> None:
        c1, r1 = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "shared_shelf_loan", "item_id": "paper_ruler", "operation": "borrow", "loan_hours": 24},
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["shared_shelf"]["items_by_id"]["paper_ruler"]["status"], "borrowed")

        # Request extension -> recorded cleanly, zero affection penalty
        c2, r2 = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "shared_shelf_loan",
                "item_id": "paper_ruler",
                "operation": "request_extension",
                "extra_hours": 24,
                "reason": "还在裁收纳盒隔板",
            },
            actor_side="subject",
            now_iso="2026-11-14T09:00:00+08:00",
        )
        self.assertEqual(c2, 200)
        ruler = r2["shared_shelf"]["items_by_id"]["paper_ruler"]
        self.assertEqual(ruler["extension_count"], 1)
        self.assertEqual(r2["result"]["affection_penalty"], 0)
        self.assertFalse(r2["shared_shelf"]["affection_score_coupled"])

        # Return to shelf
        c3, r3 = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "shared_shelf_loan", "item_id": "paper_ruler", "operation": "return"},
            actor_side="subject",
            now_iso="2026-11-14T16:00:00+08:00",
        )
        self.assertEqual(c3, 200)
        self.assertEqual(r3["shared_shelf"]["items_by_id"]["paper_ruler"]["status"], "on_shelf")

    def test_20_public_notice_unread_glance_approach_read_syncs_roadblock_and_real_truth_wins(self) -> None:
        # Post notice warning open_shortcut is blocked (valid 2 hours)
        c_post, _ = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "post_public_notice",
                "notice_id": "n_road_1",
                "title": "近路台阶维护通知",
                "body": "今日上午院门至街角近路刷漆暂封，请绕行有檐长廊。",
                "valid_hours": 2,
                "related_route_id": "open_shortcut",
                "roadblock_claim": "blocked",
            },
            actor_side="partner",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(c_post, 200)

        # Passing glance before reading: sees "有一张新纸", body is None, roadblock NOT yet in discovered_roadblocks!
        _, unread_v = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-13T10:10:00+08:00"
        )
        notice_proj = unread_v["public_notices"][0]
        self.assertEqual(notice_proj["passing_summary"], "有一张新纸")
        self.assertIsNone(notice_proj["body"])
        self.assertEqual(unread_v["journey"]["discovered_roadblocks"], [])

        # Approach and call read_public_notice -> body revealed & open_shortcut synced!
        c_rd, r_rd = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "read_public_notice", "notice_id": "n_road_1"},
            actor_side="subject",
            now_iso="2026-11-13T10:15:00+08:00",
        )
        self.assertEqual(c_rd, 200)
        self.assertIn("open_shortcut", r_rd["journey"]["discovered_roadblocks"])
        self.assertIsNotNone(r_rd["public_notices"][0]["body"])

        # After 2 hours (12:30), notice auto-marks expired=True!
        # Also test conflict: notice said blocked, but real roadblock was never set -> explicit traversal on open_shortcut succeeds!
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "yard"},
            actor_side="subject",
            now_iso="2026-11-13T12:25:00+08:00",
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T12:27:00+08:00",
        )
        c_walk, r_walk = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "street_corner", "route_id": "open_shortcut"},
            actor_side="subject",
            now_iso="2026-11-13T12:30:00+08:00",
        )
        self.assertEqual(c_walk, 200)
        self.assertEqual(r_walk["journey"]["state"], "in_transit")
        self.assertNotIn("open_shortcut", r_walk["journey"]["discovered_roadblocks"])
        self.assertTrue(r_walk["public_notices"][0]["expired"])

    def test_21_place_booklet_rejects_unvisited_place_409_allows_visited_and_pause_zero_penalty(self) -> None:
        # riverside is "unknown", bookstore is "told_clue" -> both must reject 409 PLACE_NOT_VISITED_YET!
        for unvisited in ("riverside", "bookstore"):
            code, err = mutate_v04_neighborhood_view(
                self.day_root,
                {
                    "action": "update_place_booklet",
                    "place_id": unvisited,
                    "quietness_band": "quiet",
                    "impression": "凭空想象的安静度",
                },
                actor_side="subject",
                now_iso=self.t0,
            )
            self.assertEqual(code, 409, f"Expected 409 for unvisited {unvisited}")
            self.assertEqual(err["reason_code"], "PLACE_NOT_VISITED_YET")

        # Walk to riverside (home->porch->yard->street_corner->riverside = 1+1+4+6 = 12 mins)
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "riverside"},
            actor_side="subject",
            now_iso="2026-11-13T14:00:00+08:00",
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T14:12:00+08:00",
        )

        # Now riverside is visited -> recording firsthand quietness succeeds!
        c_ok, r_ok = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "update_place_booklet",
                "place_id": "riverside",
                "quietness_band": "noisy",
                "impression": "午后河风和水声偏大，看画册还可以，细读文字容易分神。",
            },
            actor_side="subject",
            now_iso="2026-11-13T14:20:00+08:00",
        )
        self.assertEqual(c_ok, 200)
        self.assertEqual(len(r_ok["place_booklets"][0]["entries"]), 1)

        # Pause booklet ("暂时不想做了") -> zero penalty, entries preserved
        c_p, r_p = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "update_place_booklet", "operation": "pause_booklet", "note": "这几天暂时不想做了"},
            actor_side="subject",
            now_iso="2026-11-14T10:00:00+08:00",
        )
        self.assertEqual(c_p, 200)
        self.assertEqual(r_p["place_booklets"][0]["status"], "paused")
        self.assertEqual(r_p["result"]["failure_penalty"], 0)
        self.assertFalse(r_p["result"]["reminders_active"])
        self.assertEqual(len(r_p["place_booklets"][0]["entries"]), 1)

    # =========================================================================
    # 6. Perspective Redaction, RBAC (400 / 403), Idempotency & Static Guards
    # =========================================================================

    def test_22_partner_default_perspective_redacts_private_cards_and_unshared_booklet(self) -> None:
        # Create one private inscribed card and one shared inscribed card + private booklet entry
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": "priv_card"},
            actor_side="subject",
            now_iso=self.t0,
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "priv_card",
                "text": "只写给自己看的随手记",
                "shared_or_displayed": False,
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": "pub_card"},
            actor_side="subject",
            now_iso=self.t0,
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "pub_card",
                "text": "公开给伴侣看的书签句",
                "shared_or_displayed": True,
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "update_place_booklet", "place_id": "porch", "impression": "雨天门廊很安静"},
            actor_side="subject",
            now_iso=self.t0,
        )

        # Partner default view: priv_card redacted, pub_card visible, unshared booklet redacted!
        _, p_view = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=False, now_iso=self.t0
        )
        self.assertIsNone(p_view["craft_bench"]["cards_by_id"]["priv_card"]["inscribed_text"])
        self.assertTrue(p_view["craft_bench"]["cards_by_id"]["priv_card"]["withheld"])
        self.assertEqual(
            p_view["craft_bench"]["cards_by_id"]["pub_card"]["inscribed_text"],
            "公开给伴侣看的书签句",
        )
        self.assertFalse(p_view["craft_bench"]["cards_by_id"]["pub_card"]["withheld"])
        self.assertTrue(p_view["place_booklets"][0]["withheld"])
        self.assertEqual(p_view["place_booklets"][0]["entries"], [])

        # Subject shares booklet -> now visible in partner_default!
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "update_place_booklet", "shared_with_partner": True},
            actor_side="subject",
            now_iso=self.t0,
        )
        _, p_view_shared = read_v04_neighborhood_view(
            self.day_root, has_subject_capability=False, now_iso=self.t0
        )
        self.assertFalse(p_view_shared["place_booklets"][0]["withheld"])
        self.assertEqual(len(p_view_shared["place_booklets"][0]["entries"]), 1)

    def test_23_partner_forbidden_on_subject_exclusive_actions_403_and_her_alias_works(self) -> None:
        self.assertEqual(len(PARTNER_ALLOWED_ACTIONS), 6)
        self.assertEqual(len(SUBJECT_ALLOWED_ACTIONS), 12)

        subject_only_samples = [
            {"action": "start_journey", "target_place": "porch"},
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            {"action": "read_public_notice"},
            {"action": "craft_paper_or_box", "operation": "cut_card"},
            {"action": "bookstore_service", "service": "browse_book", "book_id": "book_botanical_plates"},
            {"action": "update_place_booklet", "place_id": "home", "impression": "桌前安静"},
        ]
        for sample in subject_only_samples:
            code, err = mutate_v04_neighborhood_view(
                self.day_root, sample, actor_side="partner", now_iso=self.t0
            )
            self.assertEqual(code, 403, f"Expected 403 for partner calling {sample['action']}")
            self.assertEqual(err["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Unknown actor_side rejected with 403
        c_bad, e_bad = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "set_local_weather", "weather": "clear"},
            actor_side="admin_root",
            now_iso=self.t0,
        )
        self.assertEqual(c_bad, 403)
        self.assertEqual(e_bad["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # actor_side="her" normalizes cleanly to "subject"
        c_her, r_her = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "craft_paper_or_box", "operation": "cut_card"},
            actor_side="her",
            now_iso=self.t0,
        )
        self.assertEqual(c_her, 200)
        self.assertEqual(r_her["actor_side"], "subject")

    def test_24_strict_field_whitelist_rejects_unknown_identity_and_mood_keys_400(self) -> None:
        bad_payloads = [
            {},
            {"action": "unknown_magic_action"},
            {"": "blank", "action": "set_local_weather", "weather": "clear"},
            {"action": "set_local_weather", "weather": "clear", "extra_field": 123},
            {"action": "set_local_weather", "weather": "clear", "mood_delta": 5},
            {"action": "shared_shelf_loan", "item_id": "paper_ruler", "operation": "borrow", "affection_score": 99},
            {"action": "craft_paper_or_box", "operation": "cut_card", "artifact_id": "art:1"},
            {"action": "craft_paper_or_box", "operation": "cut_card", "uid": 0},
            {"action": "craft_paper_or_box", "operation": "cut_card", "owner": "chiyo"},
            {"action": "craft_paper_or_box", "operation": "cut_card", "actor_side": "subject"},
            {"action": "bookstore_service", "service": "browse_book", "book_id": "book_botanical_plates", "price": 0},
            {"action": "set_local_weather", "weather": "   "},
        ]
        for bad in bad_payloads:
            code, err = mutate_v04_neighborhood_view(
                self.day_root, bad, actor_side="subject", now_iso=self.t0
            )
            self.assertEqual(code, 400, f"Expected 400 INVALID_FIELDS for {bad}")
            self.assertEqual(err["reason_code"], "INVALID_FIELDS")

    def test_25_idempotent_operation_key_replay_and_conflict_plus_expected_revision_cas(self) -> None:
        payload = {
            "action": "craft_paper_or_box",
            "operation": "cut_card",
            "units": 2,
            "card_id": "card_idem",
            "operation_key": "op-cut-idem-001",
        }
        c1, r1 = mutate_v04_neighborhood_view(
            self.day_root, payload, actor_side="subject", now_iso=self.t0
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["status"], "applied")
        self.assertEqual(r1["craft_bench"]["paper_units"], 6)

        # Replay exact same operation_key -> status="already_applied", paper NOT deducted twice!
        c2, r2 = mutate_v04_neighborhood_view(
            self.day_root, payload, actor_side="subject", now_iso=self.t0
        )
        self.assertEqual(c2, 200)
        self.assertEqual(r2["status"], "already_applied")
        self.assertTrue(r2["already_applied"])
        self.assertEqual(r2["craft_bench"]["paper_units"], 6)

        # Same operation_key with different payload -> 409 OPERATION_KEY_CONFLICT
        c_conf, e_conf = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "craft_paper_or_box",
                "operation": "cut_card",
                "units": 1,
                "card_id": "card_other",
                "operation_key": "op-cut-idem-001",
            },
            actor_side="subject",
            now_iso=self.t0,
        )
        self.assertEqual(c_conf, 409)
        self.assertEqual(e_conf["reason_code"], "OPERATION_KEY_CONFLICT")

        # Stale expected_revision -> 409 REVISION_CONFLICT
        c_cas, e_cas = mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "set_local_weather", "weather": "drizzle", "expected_revision": 0},
            actor_side="partner",
            now_iso=self.t0,
        )
        self.assertEqual(c_cas, 409)
        self.assertEqual(e_cas["reason_code"], "REVISION_CONFLICT")

    def test_26_advance_processes_commits_due_arrival_reservation_and_notice_expiry(self) -> None:
        # Reserve a book for 1h, post a 1h notice, and start a 2m journey to yard
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "reserve_book",
                "book_id": "book_botanical_plates",
                "hold_hours": 1,
            },
            actor_side="subject",
            now_iso="2026-11-13T11:00:00+08:00",
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "post_public_notice",
                "notice_id": "n_short",
                "title": "午间短讯",
                "body": "共享架刚擦拭完毕。",
                "valid_hours": 1,
            },
            actor_side="partner",
            now_iso="2026-11-13T11:00:00+08:00",
        )
        mutate_v04_neighborhood_view(
            self.day_root,
            {"action": "start_journey", "target_place": "yard"},
            actor_side="subject",
            now_iso="2026-11-13T11:00:00+08:00",
        )

        adv = advance_v04_neighborhood_processes(
            self.day_root, now_iso="2026-11-13T12:05:00+08:00"
        )
        self.assertTrue(adv["ok"])
        self.assertTrue(adv["advanced"])
        self.assertEqual(adv["llm_calls"], 0)
        self.assertTrue(adv["changes"]["journey_advanced"])
        self.assertEqual(adv["changes"]["expired_reservations"], 1)
        self.assertEqual(adv["changes"]["expired_notices"], 1)

    def test_27_open_source_character_neutrality_no_hardcoded_instance_names(self) -> None:
        for rel in (
            "components/world/scripts/v04_neighborhood_living.py",
            "components/phone/v04_neighborhood_view.py",
        ):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("千代", src, f"Hardcoded instance name found in {rel}")
            self.assertNotIn("她", src, f"Non-neutral pronoun found in {rel}")

    def test_28_frozen_life_anchors_remain_byte_for_byte_untouched(self) -> None:
        for rel_path, expected_sha in FROZEN_ANCHORS.items():
            actual = hashlib.sha256((REPO_ROOT / rel_path).read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Frozen anchor modified: {rel_path}")


if __name__ == "__main__":
    unittest.main()
