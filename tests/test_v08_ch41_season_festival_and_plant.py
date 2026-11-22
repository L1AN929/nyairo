#!/usr/bin/env python3
"""v0.8 Ch.41: season daylight ecology, tolerant plant milestones & missable markets.

Covers ``41-天气季节与小尺度生态`` §四–§五 over the real owner
(``components/world/scripts/v04_neighborhood_living.py``):

* daylight windows shorten in winter and the indoor window spot is recommended
  warmer; a summer evening makes the yard comfortably usable;
* a winter plant over-winters gently (0.55x water loss, 0.15x growth), a dry
  plant recovers when watered (``wilt_recovered_count``), steady water + light
  cross a new-leaf milestone, and public care keeps it alive while away;
* the street-corner seasonal market runs on the 12th/24th, leaves real physical
  traces for 48 hours afterwards, and a missed market is never turned into a
  fake shared experience (``attended_firsthand`` stays False);
* every projection read stays 100% zero-write, and a legacy state file that
  never carried the new fields still derives them in memory without a save.

Run (native Python on Windows)::

    python -B -m unittest tests.test_v08_ch41_season_festival_and_plant -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(REPO_ROOT / "components"), str(_WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as engine  # noqa: E402

TZ8 = timezone(timedelta(hours=8))


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return out


class Ch41SeasonFestivalAndPlantTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08ch41-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    # ------------------------------------------------------------------
    # 1. Zero-write reads & new projection keys
    # ------------------------------------------------------------------

    def test_01_projections_are_zero_write_and_carry_new_keys(self) -> None:
        self.assertEqual(list(self.root.iterdir()), [])
        empty_snap = _tree_snapshot(self.root)

        code_p, body_p = read_v04_neighborhood_view(
            self.root, has_subject_capability=False, now_iso="2026-11-12T11:00:00+08:00"
        )
        code_s, body_s = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso="2026-11-12T11:00:00+08:00"
        )
        self.assertEqual(_tree_snapshot(self.root), empty_snap)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(code_p, 200)
        self.assertEqual(code_s, 200)

        for body in (body_p, body_s):
            self.assertEqual(body["llm_calls"], 0)
            self.assertIn("season_ecology", body["environment"])
            self.assertIn("festival_traces", body["environment"])
            self.assertIn("seasonal_festival", body["public_life"])
            plant = body["potted_plant"]
            for key in (
                "leaf_count",
                "seasonal_state",
                "new_leaf_note_cn",
                "last_new_leaf_at",
                "wilt_recovered_count",
                "recovery_note_cn",
                "public_care_note_cn",
            ):
                self.assertIn(key, plant)

        # With a real state file on disk, a later read still settles in memory only.
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso="2026-11-12T09:00:00+08:00",
        )
        self.assertEqual(code, 200)
        populated_snap = _tree_snapshot(self.root)
        _, later = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso="2026-11-12T15:30:00+08:00"
        )
        self.assertEqual(_tree_snapshot(self.root), populated_snap)
        self.assertGreater(later["revision"], 0)

    # ------------------------------------------------------------------
    # 2. Season daylight ecology
    # ------------------------------------------------------------------

    def test_02_winter_short_daylight_and_summer_evening_yard(self) -> None:
        winter = engine._derive_season_ecology(
            "winter", "clear", "medium", datetime(2026, 12, 15, 10, 0, tzinfo=TZ8)
        )
        self.assertEqual(winter["sunlight_window"], "08:00-16:30")
        self.assertTrue(winter["sunlight_active"])
        self.assertTrue(winter["window_warmth_recommended"])
        self.assertTrue(winter["yard_suitable_now"])

        summer = engine._derive_season_ecology(
            "summer", "clear", "medium", datetime(2026, 7, 15, 10, 0, tzinfo=TZ8)
        )
        self.assertEqual(summer["sunlight_window"], "05:30-19:30")
        self.assertGreater(summer["daylight_minutes"], winter["daylight_minutes"])

        summer_evening = engine._derive_season_ecology(
            "summer", "clear", "medium", datetime(2026, 7, 15, 18, 0, tzinfo=TZ8)
        )
        self.assertTrue(summer_evening["yard_suitable_now"])
        self.assertIn("夏傍晚", summer_evening["yard_hint_cn"])

        winter_night = engine._derive_season_ecology(
            "winter", "clear", "medium", datetime(2026, 12, 15, 20, 0, tzinfo=TZ8)
        )
        self.assertFalse(winter_night["yard_suitable_now"])
        self.assertTrue(winter_night["window_warmth_recommended"])

        # The projection reflects the same winter recommendation.
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "winter"},
            actor_side="partner",
            now_iso="2026-12-15T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        _, body = read_v04_neighborhood_view(
            self.root, now_iso="2026-12-15T10:00:00+08:00"
        )
        ecology = body["environment"]["season_ecology"]
        self.assertTrue(ecology["window_warmth_recommended"])
        self.assertEqual(ecology["season"], "winter")

    # ------------------------------------------------------------------
    # 3. Winter dormancy saves water
    # ------------------------------------------------------------------

    def test_03_winter_plant_dormancy_saves_water(self) -> None:
        with tempfile.TemporaryDirectory() as winter_dir, tempfile.TemporaryDirectory() as autumn_dir:
            for root, season in ((winter_dir, "winter"), (autumn_dir, "autumn")):
                code, _ = mutate_v04_neighborhood_view(
                    root,
                    {"action": "set_local_weather", "weather": "clear", "season": season},
                    actor_side="partner",
                    now_iso="2026-12-15T08:00:00+08:00",
                )
                self.assertEqual(code, 200)

            adv_w = engine.advance_v04_neighborhood_processes(
                winter_dir, now_iso="2026-12-15T18:00:00+08:00"
            )
            adv_a = engine.advance_v04_neighborhood_processes(
                autumn_dir, now_iso="2026-12-15T18:00:00+08:00"
            )
            self.assertTrue(adv_w["advanced"])
            self.assertTrue(adv_a["advanced"])

            _, v_w = read_v04_neighborhood_view(
                winter_dir, now_iso="2026-12-15T18:00:00+08:00"
            )
            _, v_a = read_v04_neighborhood_view(
                autumn_dir, now_iso="2026-12-15T18:00:00+08:00"
            )
            plant_w = v_w["potted_plant"]
            plant_a = v_a["potted_plant"]

            # Porch plant loses 1.0/hr: autumn 70 -> 60, winter 0.55x -> 64.5.
            self.assertAlmostEqual(plant_a["hydration"], 60.0, places=3)
            self.assertAlmostEqual(plant_w["hydration"], 64.5, places=3)
            self.assertGreater(plant_w["hydration"], plant_a["hydration"])
            # Winter growth is gentle, not punished.
            self.assertLess(plant_w["growth_points"], plant_a["growth_points"])
            self.assertEqual(plant_w["seasonal_state"], "winter_dormant")
            self.assertEqual(plant_a["seasonal_state"], "active_growth")
            self.assertIn(plant_w["stage"], engine.PLANT_STAGES)

    # ------------------------------------------------------------------
    # 4. Wilt recovery, new-leaf milestone, public care
    # ------------------------------------------------------------------

    def test_04_wilting_plant_recovers_when_watered(self) -> None:
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "water_or_move_plant", "operation": "move", "target_place": "yard"},
            actor_side="subject",
            now_iso="2026-09-01T10:00:00+08:00",
        )
        self.assertEqual(code, 200)

        engine.advance_v04_neighborhood_processes(
            self.root, now_iso="2026-09-16T10:00:00+08:00"
        )
        _, wilted = read_v04_neighborhood_view(
            self.root, now_iso="2026-09-16T10:00:00+08:00"
        )
        plant = wilted["potted_plant"]
        self.assertEqual(plant["stage"], "wilting_recoverable")
        self.assertLessEqual(plant["hydration"], 10.0)
        self.assertTrue(plant["never_dies_offline"])

        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "water_or_move_plant", "operation": "water", "water_amount": 80},
            actor_side="partner",
            now_iso="2026-09-16T10:05:00+08:00",
        )
        self.assertEqual(code, 200)
        _, recovered = read_v04_neighborhood_view(
            self.root, now_iso="2026-09-16T10:05:00+08:00"
        )
        plant = recovered["potted_plant"]
        self.assertGreaterEqual(plant["wilt_recovered_count"], 1)
        self.assertEqual(plant["recovery_note_cn"], "浇水后垂下去的叶片慢慢舒展回来了")
        self.assertIn(plant["stage"], ("seedling", "leafy", "thriving"))
        self.assertTrue(plant["never_dies_offline"])

    def test_05_steady_water_and_light_cross_new_leaf_milestone(self) -> None:
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso="2026-09-01T08:00:00+08:00",
        )
        self.assertEqual(code, 200)
        _, before = read_v04_neighborhood_view(
            self.root, now_iso="2026-09-01T08:00:00+08:00"
        )
        self.assertEqual(before["potted_plant"]["leaf_count"], engine.PLANT_LEAF_BASE_COUNT)

        # 20h on the porch at 1.32 growth/hr -> 26.4 growth points -> one new leaf.
        adv = engine.advance_v04_neighborhood_processes(
            self.root, now_iso="2026-09-02T04:00:00+08:00"
        )
        self.assertTrue(adv["advanced"])
        _, after = read_v04_neighborhood_view(
            self.root, now_iso="2026-09-02T04:00:00+08:00"
        )
        plant = after["potted_plant"]
        self.assertGreater(plant["leaf_count"], engine.PLANT_LEAF_BASE_COUNT)
        self.assertIsNotNone(plant["last_new_leaf_at"])
        self.assertIn("新绿叶", plant["new_leaf_note_cn"])

    def test_06_public_care_keeps_plant_alive_while_away(self) -> None:
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso="2026-09-01T08:00:00+08:00",
        )
        self.assertEqual(code, 200)
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {
                "action": "water_or_move_plant",
                "operation": "toggle_public_care",
                "public_care_enabled": True,
            },
            actor_side="subject",
            now_iso="2026-09-01T08:05:00+08:00",
        )
        self.assertEqual(code, 200)

        engine.advance_v04_neighborhood_processes(
            self.root, now_iso="2026-09-06T08:05:00+08:00"
        )
        _, cared = read_v04_neighborhood_view(
            self.root, now_iso="2026-09-06T08:05:00+08:00"
        )
        plant = cared["potted_plant"]
        self.assertTrue(plant["never_dies_offline"])
        self.assertGreaterEqual(plant["hydration"], 55.0)
        self.assertIn(plant["stage"], ("leafy", "thriving"))
        self.assertTrue(plant["public_care_note_cn"])

    # ------------------------------------------------------------------
    # 5. Missable seasonal market & honest physical traces
    # ------------------------------------------------------------------

    def test_07_missed_market_leaves_only_physical_traces(self) -> None:
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso="2026-11-12T09:00:00+08:00",
        )
        self.assertEqual(code, 200)

        # Do NOT go on the 12th. Arrive at the corner on the 13th instead.
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "start_journey", "target_place": "street_corner"},
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T10:06:00+08:00",
        )
        self.assertEqual(code, 200)

        _, view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso="2026-11-13T10:07:00+08:00"
        )
        fest = view["public_life"]["seasonal_festival"]
        self.assertEqual(fest["phase"], "residual_traces")
        self.assertFalse(fest["attended_firsthand"])
        traces = "".join(fest["residual_traces"])
        self.assertIn("纸灯笼串", traces)
        self.assertIn("回收箱", traces)
        env_traces = view["environment"]["festival_traces"]
        self.assertEqual(env_traces["phase"], "residual_traces")
        self.assertTrue(env_traces["residual_traces"])

        state, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        self.assertEqual(state["public_life"]["attended_festivals"], [])

    def test_08_active_market_arrival_is_firsthand(self) -> None:
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso="2026-11-12T09:30:00+08:00",
        )
        self.assertEqual(code, 200)

        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "start_journey", "target_place": "street_corner"},
            actor_side="subject",
            now_iso="2026-11-12T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        code, _ = mutate_v04_neighborhood_view(
            self.root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-12T10:06:00+08:00",
        )
        self.assertEqual(code, 200)

        _, view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso="2026-11-12T10:07:00+08:00"
        )
        fest = view["public_life"]["seasonal_festival"]
        self.assertEqual(fest["phase"], "active")
        self.assertTrue(fest["attended_firsthand"])
        self.assertEqual(fest["festival_name_cn"], "秋晒叶市")
        self.assertEqual(fest["residual_traces"], [])

        state, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        self.assertIn("festival_20261112", state["public_life"]["attended_festivals"])

    # ------------------------------------------------------------------
    # 6. Backwards compatibility & legacy derivation
    # ------------------------------------------------------------------

    def test_09_stage_and_cloth_rates_unchanged(self) -> None:
        for hydration, growth in (
            (70.0, 0.0),
            (20.0, 0.0),
            (5.0, 0.0),
            (60.0, 30.0),
            (70.0, 80.0),
        ):
            self.assertIn(engine._derive_plant_stage(hydration, growth), engine.PLANT_STAGES)
        self.assertEqual(engine.compute_cloth_hourly_rate("yard_line", "clear", "medium"), -28.0)
        self.assertEqual(engine.compute_cloth_hourly_rate("home_rack", "clear", "medium"), -12.0)
        self.assertEqual(engine.compute_cloth_hourly_rate("porch_hook", "rain", "medium"), -6.0)

    def test_10_legacy_state_derives_new_fields_without_writing(self) -> None:
        legacy = engine.default_state()
        for key in (
            "leaf_count",
            "seasonal_state",
            "new_leaf_note_cn",
            "last_new_leaf_at",
            "wilt_recovered_count",
            "recovery_note_cn",
            "public_care_note_cn",
        ):
            legacy["potted_plant"].pop(key, None)
        legacy["public_life"].pop("attended_festivals", None)
        legacy["potted_plant"]["hydration"] = 70.0
        legacy["potted_plant"]["growth_points"] = 50.0
        legacy["potted_plant"]["segment_start_hydration"] = 70.0
        legacy["potted_plant"]["segment_start_growth"] = 50.0
        legacy["potted_plant"]["segment_started_at"] = None
        engine.save_state_atomic(self.root, legacy)

        snap = _tree_snapshot(self.root)
        code, body = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso="2026-10-12T11:00:00+08:00"
        )
        self.assertEqual(code, 200)
        self.assertEqual(_tree_snapshot(self.root), snap)

        plant = body["potted_plant"]
        self.assertEqual(plant["leaf_count"], engine._plant_leaf_count_for_growth(50.0))
        self.assertEqual(plant["seasonal_state"], "active_growth")
        self.assertEqual(plant["wilt_recovered_count"], 0)
        self.assertEqual(plant["recovery_note_cn"], "")
        self.assertEqual(plant["public_care_note_cn"], "")
        self.assertIn("seasonal_festival", body["public_life"])
        self.assertIn("season_ecology", body["environment"])


if __name__ == "__main__":
    unittest.main()
