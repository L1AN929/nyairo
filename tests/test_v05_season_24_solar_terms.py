#!/usr/bin/env python3
"""Comprehensive tests for True Four-Season & 24 Solar Terms Cross-Stack Backend Linkage.

Covers:
1. Exact 24 solar-term calendar dates across 2024-2030 (including Jan wrap-around to
   previous Dec Dongzhi, Dec wrap-around to next Jan Xiaohan, timezone conversion, and
   5-bit shortwave callsign compatibility).
2. 4-season microclimate profiles, daylight windows, tea cooling multipliers, window frost
   thresholds and melt speed multipliers (winter lingers significantly longer than summer).
3. Living town 5 regulars' seasonal items, blank grove seasonal tones, chalkboard seasonal
   specials, seasonal snowbank shade factors, and 4 seasonal rare spectacles.
4. Inner spark seasonal dream fallback, seasonal frost condensation/melt, and read-only
   season_solar summary with zero auto-created desires.
5. v04_neighborhood_living season_ecology additive fields & somatic_comfort_view
   seasonal_context preserving COOLING_SECONDS == 1800 and 4 closed modalities.
6. 100% zero-write GET across nonexistent, empty, and populated day roots.
7. lived_context_bridge Calm Gate (empty root == 0 chars, calm non-term day silent,
   v04_line untouched), <= 60-char seasonal cue, <= 1800-char cap, commitment priority,
   llm_calls == 0, anti-chore contract (zero check-in debt / affection score), and
   encoding/emoji/neutral-pronoun hygiene.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import v05_dreams_desires_foreshadow as dd_engine  # noqa: E402
from components.phone import somatic_comfort_view as somatic_view  # noqa: E402
from components.phone import v04_neighborhood_view as nb_view  # noqa: E402
from components.phone import v05_dreams_desires_view as spark_view  # noqa: E402
from components.phone import v05_living_town_view as town_view  # noqa: E402
from components.world.scripts import v04_neighborhood_living as nb_engine  # noqa: E402
from components.world.scripts import v05_living_town_engine as lt_engine  # noqa: E402
from components.world.scripts import v05_season_solar_pulse as ssp  # noqa: E402
from components.world.scripts import v05_unified_world_fabric as fabric  # noqa: E402
from components.world.scripts import world_living_skeleton as wls  # noqa: E402

# Authoritative 2024-2030 24 solar-term calendar days (UTC+8, Purple Mountain Observatory).
EXPECTED_2024_2030_DAYS: dict[int, tuple[int, ...]] = {
    2024: (6, 20, 4, 19, 5, 20, 4, 19, 5, 20, 5, 21, 6, 22, 7, 22, 7, 22, 8, 23, 7, 22, 6, 21),
    2025: (5, 20, 3, 18, 5, 20, 4, 20, 5, 21, 5, 21, 7, 22, 7, 23, 7, 23, 8, 23, 7, 22, 7, 21),
    2026: (5, 20, 4, 18, 5, 20, 5, 20, 5, 21, 5, 21, 7, 23, 7, 23, 7, 23, 8, 23, 7, 22, 7, 22),
    2027: (5, 20, 4, 19, 6, 21, 5, 20, 6, 21, 6, 21, 7, 23, 8, 23, 8, 23, 8, 23, 7, 22, 7, 22),
    2028: (6, 20, 4, 19, 5, 20, 4, 19, 5, 20, 5, 21, 6, 22, 7, 22, 7, 22, 8, 23, 7, 22, 6, 21),
    2029: (5, 20, 3, 18, 5, 20, 4, 20, 5, 21, 5, 21, 7, 22, 7, 23, 7, 23, 8, 23, 7, 22, 7, 21),
    2030: (5, 20, 4, 18, 5, 20, 5, 20, 5, 21, 5, 21, 7, 23, 7, 23, 7, 23, 8, 23, 7, 22, 7, 22),
}

SAMPLE_WORLD = {
    "ok": True,
    "status": "applied",
    "data": {
        "location": {"status": "available", "place_id": "home", "area_id": "bedroom", "home": True},
        "world": {"local_time": "2026-12-22T17:00:00+08:00", "local_date": "2026-12-22", "revision": 5},
        "local_environment": {
            "status": "available",
            "furniture": [],
            "light": {"status": "available", "power": "on", "band": "warm"},
        },
        "travel": {"status": "idle"},
    },
}

SAMPLE_BODY = {
    "ok": True,
    "status": "available",
    "data": {
        "pose": "seated",
        "orientation": "north",
        "recovery": {"state": "idle", "mode": None},
        "signals": [],
        "needs": {"hunger": "none", "thirst": "mild", "toilet": "none"},
        "rest_evidence": None,
        "signals_status": "available",
    },
}


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


class TestSolarTermsAndMicroclimateTruthSource(unittest.TestCase):
    def test_01_2024_to_2030_exact_24_solar_terms(self) -> None:
        self.assertEqual(len(ssp.SOLAR_TERMS_24), 24)
        for year, expected_days in EXPECTED_2024_2030_DAYS.items():
            actual = tuple(ssp.solar_term_day_of_month(year, idx) for idx in range(24))
            self.assertEqual(actual, expected_days, f"solar-term day mismatch for year {year}")

        # Key 2026 dates specified in blueprint
        key_2026 = [
            ("2026-02-04", 2, "lichun", "立春"),
            ("2026-02-18", 3, "yushui", "雨水"),
            ("2026-03-20", 5, "chunfen", "春分"),
            ("2026-06-21", 11, "xiazhi", "夏至"),
            ("2026-08-07", 14, "liqiu", "立秋"),
            ("2026-09-23", 17, "qiufen", "秋分"),
            ("2026-11-07", 20, "lidong", "立冬"),
            ("2026-11-22", 21, "xiaoxue", "小雪"),
            ("2026-12-22", 23, "dongzhi", "冬至"),
        ]
        for iso_day, exp_idx, exp_id, exp_cn in key_2026:
            pulse = ssp.project_season_solar_pulse(now_iso=f"{iso_day}T10:00:00+08:00")
            st = pulse["solar_term"]
            self.assertEqual(st["solar_term_idx"], exp_idx, iso_day)
            self.assertEqual(st["term_id"], exp_id, iso_day)
            self.assertEqual(st["term_name_cn"], exp_cn, iso_day)
            self.assertTrue(st["is_term_day"], iso_day)
            self.assertEqual(st["days_since_term"], 0, iso_day)
            self.assertEqual(st["term_date"], iso_day, iso_day)
            self.assertEqual(pulse["llm_calls"], 0)
            self.assertTrue(pulse["zero_write"])

            # 5-bit shortwave callsign round-trip parity
            code = fabric.encode_shortwave_weather_callsign(1, st["solar_term_idx"], 0, 2)
            decoded = fabric.decode_shortwave_weather_callsign(code)
            self.assertEqual(decoded["solar_term_idx"], exp_idx)

    def test_02_year_boundary_wrap_around_and_timezone(self) -> None:
        # Early January wraps back to previous December Dongzhi
        jan_pulse = ssp.project_season_solar_pulse(now_iso="2026-01-01T09:00:00+08:00")
        jan_st = jan_pulse["solar_term"]
        self.assertEqual(jan_st["solar_term_idx"], 23)
        self.assertEqual(jan_st["term_name_cn"], "冬至")
        self.assertEqual(jan_st["term_date"], "2025-12-21")
        self.assertFalse(jan_st["is_term_day"])
        self.assertEqual(jan_st["days_since_term"], 11)
        self.assertEqual(jan_st["next_term_idx"], 0)
        self.assertEqual(jan_st["next_term_name_cn"], "小寒")
        self.assertEqual(jan_st["next_term_date"], "2026-01-05")
        self.assertEqual(jan_st["days_until_next_term"], 4)

        # Late December looks ahead to next year's January Xiaohan
        dec_pulse = ssp.project_season_solar_pulse(now_iso="2026-12-25T20:00:00+08:00")
        dec_st = dec_pulse["solar_term"]
        self.assertEqual(dec_st["term_name_cn"], "冬至")
        self.assertEqual(dec_st["term_date"], "2026-12-22")
        self.assertEqual(dec_st["days_since_term"], 3)
        self.assertEqual(dec_st["next_term_name_cn"], "小寒")
        self.assertEqual(dec_st["next_term_date"], "2027-01-05")
        self.assertEqual(dec_st["days_until_next_term"], 11)

        # UTC timestamp converts into UTC+8 calendar day (2026-12-21T16:30Z == 2026-12-22T00:30+08)
        utc_pulse = ssp.project_season_solar_pulse(now_iso="2026-12-21T16:30:00Z")
        self.assertEqual(utc_pulse["solar_term"]["term_name_cn"], "冬至")
        self.assertTrue(utc_pulse["solar_term"]["is_term_day"])

    def test_03_microclimate_profiles_multipliers_and_skeleton_season(self) -> None:
        for month in range(1, 13):
            self.assertEqual(wls._season(month), ssp.calendar_season_for_month(month))

        profiles = ssp.SEASON_MICROCLIMATE_PROFILES
        self.assertEqual(profiles["spring"]["sunlight_window"], "06:30-18:00")
        self.assertEqual(profiles["summer"]["sunlight_window"], "05:30-19:30")
        self.assertEqual(profiles["autumn"]["sunlight_window"], "06:30-17:30")
        self.assertEqual(profiles["winter"]["sunlight_window"], "07:45-16:30")

        # Tea cooling speed order: summer < spring < autumn < winter
        self.assertEqual(profiles["summer"]["tea_cooling_multiplier"], 0.78)
        self.assertEqual(profiles["spring"]["tea_cooling_multiplier"], 1.00)
        self.assertEqual(profiles["autumn"]["tea_cooling_multiplier"], 1.28)
        self.assertEqual(profiles["winter"]["tea_cooling_multiplier"], 1.45)
        self.assertLess(profiles["summer"]["tea_cooling_multiplier"], profiles["spring"]["tea_cooling_multiplier"])
        self.assertLess(profiles["spring"]["tea_cooling_multiplier"], profiles["autumn"]["tea_cooling_multiplier"])
        self.assertLess(profiles["autumn"]["tea_cooling_multiplier"], profiles["winter"]["tea_cooling_multiplier"])

        # Frost melt speed order: winter < autumn < spring < summer
        self.assertEqual(profiles["winter"]["frost_melt_multiplier"], 0.62)
        self.assertEqual(profiles["autumn"]["frost_melt_multiplier"], 0.95)
        self.assertEqual(profiles["spring"]["frost_melt_multiplier"], 1.15)
        self.assertEqual(profiles["summer"]["frost_melt_multiplier"], 1.65)

        # Explicit season override vs live calendar
        p_live = ssp.project_season_solar_pulse(now_iso="2026-07-15T12:00:00+08:00")
        self.assertEqual(p_live["season"], "summer")
        self.assertEqual(p_live["season_source"], "calendar_live")
        p_ov = ssp.project_season_solar_pulse(now_iso="2026-07-15T12:00:00+08:00", explicit_season="winter")
        self.assertEqual(p_ov["season"], "winter")
        self.assertEqual(p_ov["season_source"], "world_state_override")
        self.assertEqual(p_ov["calendar_season"], "summer")
        self.assertLessEqual(len(p_ov["compact_context_cue_cn"]), 56)
        self.assertEqual(
            p_ov["anti_chore_contract"],
            {"check_in_debt": False, "forced_greeting": False, "affection_points": False},
        )


class TestCrossModuleSeasonalLinkage(unittest.TestCase):
    def test_01_living_town_four_seasons_regulars_grove_chalkboard_and_spectacles(self) -> None:
        season_samples = [
            ("spring", "2026-03-20T10:30:00+08:00", "春分", "spring_petal_drift", "春巷落瓣微风", "艾草盐卷与软餐包", "春笋若竹汤"),
            ("summer", "2026-06-21T20:30:00+08:00", "夏至", "summer_firefly_pier_breeze", "夏夜渡口流萤晚风", "冰镇柠檬盐面包", "冷麦茶配梅子冷面"),
            ("autumn", "2026-09-23T16:00:00+08:00", "秋分", "autumn_ginkgo_golden_steps", "深秋石阶金叶斜阳", "烤栗子吐司与肉桂苹果卷", "秋刀鱼配热味增汤"),
            ("winter", "2026-12-22T08:00:00+08:00", "冬至", "winter_rime_roof_glint", "冬晨屋脊薄霜晴光", "热乎红豆奶油包与厚吐司", "萝卜牛腩热锅与热玉米浓汤"),
        ]
        with tempfile.TemporaryDirectory(prefix="v05-season-town-") as td:
            root = Path(td)
            for exp_season, iso_ts, exp_term, exp_spec_id, exp_spec_title, exp_lin_item, exp_diner in season_samples:
                code, body = town_view.read_v05_living_town_view(root, has_subject_capability=False, now_iso=iso_ts)
                self.assertEqual(code, 200)
                self.assertEqual(body["llm_calls"], 0)
                self.assertTrue(body["zero_write"])
                self.assertEqual(body["world_context"]["season"], exp_season)
                self.assertEqual(body["season_solar"]["season"], exp_season)
                self.assertEqual(body["data"]["season_solar"]["solar_term"]["term_name_cn"], exp_term)
                self.assertEqual(body["chalkboard_and_favors"]["seasonal_special_cn"], exp_diner)
                self.assertEqual(body["chalkboard_and_favors"]["solar_term_name_cn"], exp_term)

                exp_tone = ssp.SEASON_MICROCLIMATE_PROFILES[exp_season]["blank_grove_tone_cn"]
                self.assertEqual(body["ground_strata"]["seasonal_tone_cn"], exp_tone)
                self.assertEqual(body["third_spaces"]["blank_grove"]["seasonal_tone_cn"], exp_tone)

                reg_by_id = {r["regular_id"]: r for r in body["regulars"]}
                self.assertEqual(len(reg_by_id), 5)
                self.assertEqual(reg_by_id["auntie_lin"]["seasonal_item_cn"], exp_lin_item)
                for rid, rrow in reg_by_id.items():
                    self.assertEqual(rrow["season"], exp_season)
                    self.assertEqual(rrow["seasonal_item_cn"], ssp.REGULAR_SEASONAL_TWEAKS[exp_season][rid])

                spec = body["rare_spectacle"]
                self.assertIsNotNone(spec, f"expected seasonal spectacle on {iso_ts}")
                assert spec is not None
                self.assertEqual(spec["spectacle_id"], exp_spec_id)
                self.assertEqual(spec["title_cn"], exp_spec_title)

            # Ordinary non-term day does not spam spectacles
            _, calm_body = town_view.read_v05_living_town_view(root, now_iso="2026-11-19T14:00:00+08:00")
            self.assertIsNone(calm_body["rare_spectacle"])

            # Winter snowbank retains more crust than summer after equal dry hours
            state = lt_engine._default_state(
                lt_engine._extract_world_context_zero_write(root, lt_engine._parse_now("2026-12-22T10:00:00+08:00"))
            )
            state["ground_strata"]["snowbank_depth"] = 20.0
            state["ground_strata"]["hours_since_stop"] = 4.0
            win_ctx = lt_engine._extract_world_context_zero_write(root, lt_engine._parse_now("2026-12-22T10:00:00+08:00"))
            sum_ctx = lt_engine._extract_world_context_zero_write(root, lt_engine._parse_now("2026-07-15T10:00:00+08:00"))
            win_strata = lt_engine._project_ground_strata(state, win_ctx)
            sum_strata = lt_engine._project_ground_strata(state, sum_ctx)
            self.assertGreater(win_strata["snowbank_crust_mm"], sum_strata["snowbank_crust_mm"])

    def test_02_inner_spark_frost_season_threshold_melt_and_dream_fallback(self) -> None:
        # Threshold differentiation: 15.5C clear normal condenses in winter (<=16.5) but not summer (<=11.0)
        self.assertTrue(dd_engine._infer_window_misty(weather="clear", temp_c=15.5, humidity_hint="normal", season="winter"))
        self.assertFalse(dd_engine._infer_window_misty(weather="clear", temp_c=15.5, humidity_hint="normal", season="summer"))
        # Legacy default threshold 14.0 preserved when season is None
        self.assertFalse(dd_engine._infer_window_misty(weather="clear", temp_c=15.5, humidity_hint="normal"))
        self.assertTrue(dd_engine._infer_window_misty(weather="clear", temp_c=13.5, humidity_hint="normal"))

        # Melt speed: winter lingers significantly longer than summer over 2.5h morning sun
        rec = {
            "frost_id": "frst_test",
            "created_at": "2026-12-22T09:00:00+08:00",
            "created_epoch_hour": 1000.0,
            "author_side": "partner",
            "text": "冬至早安",
            "weather": "clear",
            "temp_c": 8.0,
            "humidity_hint": "misty",
            "strokes": [[0.2, 0.3], [0.7, 0.6]],
            "canvas_phase": "FRESH",
            "latent_since_hour": 0.0,
        }
        check_dt = datetime(2026, 12, 22, 11, 30, 0, tzinfo=ssp.DEFAULT_TZ)
        win_proj = dd_engine.project_window_frost(rec, check_dt, season="winter")
        sum_proj = dd_engine.project_window_frost(rec, check_dt, season="summer")
        assert win_proj is not None and sum_proj is not None
        self.assertEqual(win_proj["season"], "winter")
        self.assertEqual(win_proj["frost_melt_multiplier"], 0.62)
        self.assertEqual(win_proj["solar_term_name_cn"], "冬至")
        self.assertEqual(sum_proj["season"], "summer")
        self.assertEqual(sum_proj["frost_melt_multiplier"], 1.65)
        self.assertGreater(win_proj["visibility_ratio"], sum_proj["visibility_ratio"] + 0.3)
        self.assertFalse(win_proj["melted"])
        self.assertTrue(sum_proj["melted"])

        # Seasonal morning dream fallback differs across seasons when echoes are empty
        digest = hashlib.sha256(b"season-dream-test").digest()
        frags = {s: dd_engine._compose_dream_fragment_cn([], digest, season=s) for s in ssp.VALID_SEASONS}
        self.assertEqual(len(set(frags.values())), 4)
        for text in frags.values():
            self.assertNotIn("她", text)
            self.assertNotIn("千代", text)

        # Inner spark view mirrors season_solar and never auto-creates desires
        with tempfile.TemporaryDirectory(prefix="v05-season-spark-") as td:
            root = Path(td)
            code, body = spark_view.read_v05_inner_spark_view(root, has_subject_capability=False, now_iso="2026-12-22T08:30:00+08:00")
            self.assertEqual(code, 200)
            self.assertEqual(body["desires"]["items"], [])
            self.assertEqual(body["season_solar"]["season"], "winter")
            self.assertEqual(body["season_solar"]["solar_term"]["term_name_cn"], "冬至")
            self.assertEqual(body["data"]["season_solar"]["frost_melt_multiplier"], 0.62)
            self.assertTrue(body["season_solar"]["seasonal_spark_note_cn"])

    def test_03_v04_neighborhood_and_somatic_comfort_seasonal_linkage(self) -> None:
        # v04_neighborhood_living additive fields with zero private/moisture_pct leaks
        dt_win = datetime(2026, 12, 22, 10, 0, 0, tzinfo=ssp.DEFAULT_TZ)
        eco = nb_engine._derive_season_ecology("winter", "clear", "medium", dt_win)
        self.assertEqual(eco["solar_term"]["term_name_cn"], "冬至")
        self.assertEqual(eco["solar_term"]["solar_term_idx"], 23)
        self.assertTrue(eco["solar_term"]["is_term_day"])
        self.assertEqual(eco["tea_cooling_multiplier"], 1.45)
        self.assertEqual(eco["soundscape_palette"], "winter_muffled_glass_hum")
        self.assertEqual(eco["window_motif"], "early_dusk_warm_lamp_frost_rim_snow")
        for key in eco:
            self.assertFalse(key.startswith("private_"))
            self.assertNotEqual(key, "moisture_pct")

        # somatic_comfort_view preserves COOLING_SECONDS == 1800 and 4 modalities
        self.assertEqual(somatic_view.COOLING_SECONDS, 1800)
        self.assertEqual(somatic_view.MODALITIES, ("thermal", "moisture", "posture", "tactile"))
        st = somatic_view.empty_state()
        sb = somatic_view.somatic_block(
            world_result=SAMPLE_WORLD,
            body_view=SAMPLE_BODY,
            state=st,
            state_reason=None,
            now_iso="2026-12-22T17:00:00+08:00",
        )
        cb = somatic_view.care_offering_block(
            world_result=SAMPLE_WORLD,
            body_view=SAMPLE_BODY,
            state=st,
            state_reason=None,
            now_iso="2026-12-22T17:00:00+08:00",
        )
        for blk in (sb, cb):
            sc = blk["seasonal_context"]
            self.assertEqual(sc["season"], "winter")
            self.assertEqual(sc["solar_term_name_cn"], "冬至")
            self.assertEqual(sc["sunlight_window"], "07:45-16:30")
            self.assertEqual(sc["tea_cooling_multiplier"], 1.45)
            self.assertTrue(sc["seasonal_thermal_note_cn"])

    def test_04_fresh_day_root_never_locks_autumn_in_v04_neighborhood_or_context_bridge(self) -> None:
        """Regression for Defect #1: empty/fresh day_root derives live calendar season year-round."""
        cases = [
            ("2026-03-20T10:00:00+08:00", "spring", "春日", "春分", "floating_petals_pale_green_beam", 1.00),
            ("2026-06-21T10:00:00+08:00", "summer", "夏时", "夏至", "high_sun_swaying_leaf_shadow", 0.78),
            ("2026-12-22T10:00:00+08:00", "winter", "冬景", "冬至", "early_dusk_warm_lamp_frost_rim_snow", 1.45),
        ]
        with tempfile.TemporaryDirectory(prefix="v05-fresh-season-") as td:
            root = Path(td)
            before_empty = _snapshot_tree(root)
            for iso_ts, exp_season, exp_label_cn, exp_term_cn, exp_motif, exp_tea_mult in cases:
                code, nb_body = nb_view.read_v04_neighborhood_view(root, has_subject_capability=False, now_iso=iso_ts)
                self.assertEqual(code, 200)
                env = nb_body["environment"]
                eco = env["season_ecology"]
                self.assertEqual(env["season"], exp_season, iso_ts)
                self.assertEqual(eco["season"], exp_season, iso_ts)
                self.assertEqual(eco["solar_term"]["term_name_cn"], exp_term_cn, iso_ts)
                self.assertEqual(eco["window_motif"], exp_motif, iso_ts)
                self.assertEqual(eco["tea_cooling_multiplier"], exp_tea_mult, iso_ts)

                # Even if an unpersisted default v04_nb dict (revision=0, updated_at=None, season="autumn")
                # is passed to lived_context_bridge, it MUST NOT override live calendar season.
                stale_default_nb = nb_engine.default_state()
                self.assertEqual(stale_default_nb["revision"], 0)
                self.assertIsNone(stale_default_nb["environment"]["updated_at"])
                cue = lcb._format_v05_season_solar_cue(
                    now_dt=ssp.parse_iso_to_default_tz(iso_ts),
                    v04_nb=stale_default_nb,
                    town_doc=None,
                    clean_incoming="你好",
                )
                self.assertIn(f"今日{exp_term_cn}·{exp_label_cn}", cue, (iso_ts, cue))
                self.assertNotIn("秋意", cue, (iso_ts, cue))
                self.assertLessEqual(len(cue), 60)

            self.assertEqual(_snapshot_tree(root), before_empty, "v04 fresh read must remain 100% zero-write")

            # Persisted v04_nb (revision > 0 or updated_at set) still honors explicit override
            persisted_nb = nb_engine.default_state()
            persisted_nb["revision"] = 1
            persisted_nb["environment"]["season"] = "winter"
            persisted_nb["environment"]["updated_at"] = "2026-06-21T09:00:00+08:00"
            cue_override = lcb._format_v05_season_solar_cue(
                now_dt=ssp.parse_iso_to_default_tz("2026-06-21T10:00:00+08:00"),
                v04_nb=persisted_nb,
                town_doc=None,
                clean_incoming="你好",
            )
            self.assertIn("今日夏至·冬景", cue_override)


class TestZeroWriteCalmGateAndHygiene(unittest.TestCase):
    def test_01_100_percent_zero_write_across_nonexistent_empty_and_populated_roots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-season-zw-") as td:
            base = Path(td)
            nonexistent = base / "never_exists"
            empty_root = base / "empty_dir"
            empty_root.mkdir()
            populated_root = base / "populated_dir"
            (populated_root / "phone").mkdir(parents=True)
            (populated_root / "world").mkdir(parents=True)
            _write_json(
                populated_root / "world" / "v04_neighborhood_state.json",
                nb_engine.default_state(),
            )
            town_view.handle_v05_living_town_action(
                populated_root,
                {"action": "greet_or_feed_kitten", "interaction_kind": "greet", "note_cn": "打个招呼"},
                has_subject_capability=False,
                now_iso="2026-12-22T09:00:00+08:00",
            )
            spark_view.handle_v05_inner_spark_action(
                populated_root,
                {"action": "sketch_window_frost", "text": "冬至雾窗", "weather": "clear", "temp_c": 12.0, "humidity_hint": "normal"},
                has_subject_capability=False,
                now_iso="2026-12-22T09:00:00+08:00",
            )

            for root in (nonexistent, empty_root, populated_root):
                before = _snapshot_tree(root)
                existed_before = root.exists()

                c1, _ = town_view.read_v05_living_town_view(root, now_iso="2026-12-22T10:00:00+08:00")
                c2, _ = spark_view.read_v05_inner_spark_view(root, now_iso="2026-12-22T10:00:00+08:00")
                c3, _ = nb_view.read_v04_neighborhood_view(root, now_iso="2026-12-22T10:00:00+08:00")
                somatic_view.somatic_block(
                    world_result=SAMPLE_WORLD,
                    body_view=SAMPLE_BODY,
                    state=somatic_view.empty_state(),
                    state_reason=None,
                    now_iso="2026-12-22T10:00:00+08:00",
                )
                lcb.build_lived_turn_context(root, cfg={}, incoming_text="今天冬至", now_iso="2026-12-22T10:00:00+08:00")
                lcb.measure_context_budget_breakdown(root, cfg={}, incoming_text="今天冬至", now_iso="2026-12-22T10:00:00+08:00")

                self.assertEqual(c1, 200)
                self.assertEqual(c2, 200)
                self.assertEqual(c3, 200)
                self.assertEqual(root.exists(), existed_before)
                self.assertEqual(_snapshot_tree(root), before)

    def test_02_lived_context_calm_gate_budget_cap_and_commitment_priority(self) -> None:
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)

        with tempfile.TemporaryDirectory(prefix="v05-season-calm-") as td:
            root = Path(td)
            # 1) Empty unconfigured root MUST stay 0 chars even on Dongzhi + seasonal keywords
            empty_bd = lcb.measure_context_budget_breakdown(
                root, cfg={}, incoming_text="今天冬至天黑好早", now_iso="2026-12-22T17:00:00+08:00"
            )
            self.assertTrue(empty_bd["calm_silent"])
            self.assertEqual(empty_bd["total_chars"], 0)
            self.assertEqual(empty_bd["rendered_text"], "")

            # 2) Configured calm root on non-term day with calm message -> v04_line == 0 and v05_town_spark_line == 0
            (root / "phone").mkdir(parents=True)
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            calm_bd = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="你好", now_iso="2026-11-16T11:00:00+08:00"
            )
            self.assertEqual(calm_bd["per_section_chars"].get("v04_line", 0), 0)
            self.assertEqual(calm_bd["per_section_chars"].get("v05_town_spark_line", 0), 0)
            self.assertNotIn("时令:", calm_bd["rendered_text"])

            # 3) Solar term day (Dongzhi) -> emits <=60-char seasonal cue, v04_line still 0
            term_cue = lcb._format_v05_season_solar_cue(
                now_dt=datetime(2026, 12, 22, 17, 0, 0, tzinfo=ssp.DEFAULT_TZ),
                v04_nb=None,
                town_doc=None,
                clean_incoming="你好",
            )
            self.assertTrue(term_cue)
            self.assertLessEqual(len(term_cue), 60)
            self.assertIn("冬至", term_cue)
            term_bd = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="你好", now_iso="2026-12-22T17:00:00+08:00"
            )
            self.assertEqual(term_bd["per_section_chars"].get("v04_line", 0), 0)
            self.assertIn("时令:今日冬至", term_bd["rendered_text"])
            self.assertLessEqual(term_bd["total_chars"], 1800)

            # 4) Non-term day with seasonal/daylight incoming words -> wakes cue & boosts v05_town_spark_line
            kw_bd = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="最近天黑得真早，快换季了吧", now_iso="2026-11-16T17:30:00+08:00"
            )
            self.assertIn("时令:立冬后", kw_bd["rendered_text"])
            self.assertEqual(lcb._situation_boost("v05_town_spark_line", "时令", "快冬至了吧"), 2)
            self.assertEqual(lcb._situation_boost("v05_town_spark_line", "时令", "你好"), 0)

            # 5) Commitments/boundaries ALWAYS outrank v05_town_spark_line even when boosted +2
            ranked = lcb._rank_middle_sections_by_situation(
                [
                    ("autonomy_line", "- 自主边界：正忙"),
                    ("interactive_line", "- 互动约定：今晚看书"),
                    ("v05_town_spark_line", "- 0.5小镇与心火线索：时令:今日冬至"),
                    ("anchor_line", "- 日常微锚点：家居服"),
                ],
                clean_incoming="今天冬至天黑真早",
            )
            names = [n for n, _ in ranked]
            self.assertEqual(names[0], "autonomy_line")
            self.assertLess(names.index("interactive_line"), names.index("v05_town_spark_line"))
            self.assertLess(names.index("v05_town_spark_line"), names.index("anchor_line"))

    def test_03_zero_chore_debt_affection_score_and_file_hygiene(self) -> None:
        pulse = ssp.project_season_solar_pulse(now_iso="2026-11-07T09:00:00+08:00")
        dumped = json.dumps(pulse, ensure_ascii=False)
        for banned in ("affection_score", "intimacy_score", "check_in_streak", "missed_penalty", "好感度", "打卡债"):
            self.assertNotIn(banned, dumped)

        # Hygiene check on newly created modules and test file
        for rel in (
            "components/world/scripts/v05_season_solar_pulse.py",
            "tests/test_v05_season_24_solar_terms.py",
        ):
            raw = (REPO_ROOT / rel).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {rel}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {rel}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"Emoji found in {rel}: {emojis[:5]}")

        pulse_text = (REPO_ROOT / "components/world/scripts/v05_season_solar_pulse.py").read_text(encoding="utf-8")
        self.assertNotIn("她", pulse_text)
        self.assertNotIn("千代", pulse_text)


if __name__ == "__main__":
    unittest.main()
