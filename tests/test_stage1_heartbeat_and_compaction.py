#!/usr/bin/env python3
"""Stage 1 verification suite: living-foundation bootstrap, 60s heartbeat,
body transitions compaction & saturation guard, v02_agency_depth path unification,
and life_runtime_production canonical WAL probe.

All tests run 100% offline with llm_calls == 0.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "service"),
    str(REPO_ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402
import nyairo_day  # noqa: E402
import world_body_service as wbs  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import life_runtime_production as lrp  # noqa: E402
from components.life.scripts import s03_claims as s03  # noqa: E402
from components.life.scripts import v02_agency_depth_engine as ad_engine  # noqa: E402
from components.life.scripts import v02_crash_and_partial_reconciler as cpr  # noqa: E402
from components.life.scripts import v05_dreams_desires_foreshadow as v05_spark  # noqa: E402
from components.world.scripts import instance_paths  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04_nb  # noqa: E402
from components.world.scripts import v05_living_town_engine as v05_town  # noqa: E402


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        try:
            st = p.lstat()
            snap[rel] = (st.st_size, st.st_mtime_ns)
        except OSError:
            snap[rel] = (-1, -1)
    return snap


def _make_sample_transition(state: dict, at_iso: str, seq: int) -> dict:
    return br.build_transition(
        state,
        state,
        at=at_iso,
        cause_class=br.CAUSE_TIME_ADVANCE,
        input_summary={"seq": seq},
    )


class Stage1BootstrapAndHeartbeatTest(unittest.TestCase):
    def _tmp_dir(self, prefix: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return tmp

    def test_01_bare_uninitialized_root_skips_with_zero_writes(self) -> None:
        root = self._tmp_dir("s1-bare-")
        (root / "run").mkdir(parents=True, exist_ok=True)
        (root / "run" / nyairo_day.MANAGED_NAME).write_text(
            json.dumps({"schema": nyairo_day.SCHEMA_MANAGED}), encoding="utf-8"
        )
        before = _snapshot_tree(root)

        res = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-06-21T09:00:00+08:00", force_init=False
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "skipped_uninitialized")
        self.assertEqual(res["slices_ticked"], [])
        self.assertEqual(res["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(root), before)

    def test_02_bootstrap_living_foundation_idempotent_calm_and_no_autumn_lock(self) -> None:
        root = self._tmp_dir("s1-boot-")
        t0 = "2026-06-21T08:00:00+08:00"
        res1 = nyairo_day.bootstrap_living_foundation_state(root, now_iso=t0)
        self.assertTrue(res1["ok"])
        self.assertEqual(res1["llm_calls"], 0)
        self.assertEqual(res1["created_count"], 7)

        expected_files = [
            "phone/v03_clock_state.json",
            "phone/v03_ecology.json",
            "world/v04_neighborhood_state.json",
            "phone/autonomy_rhythm.json",
            "life/everyday_desires.json",
            "world/v05_living_town.json",
            "life/v05_inner_spark.json",
        ]
        for rel in expected_files:
            p = root / rel
            self.assertTrue(p.is_file(), f"missing bootstrapped file: {rel}")

        eco = json.loads((root / "phone" / "v03_ecology.json").read_text(encoding="utf-8"))
        self.assertEqual(eco["illness"]["phase"], "healthy")
        self.assertTrue(eco["proactive_policy"]["enabled"])
        self.assertTrue(eco["reality_bridge"]["outbound_policy"]["enabled"])

        nb = json.loads((root / "world" / "v04_neighborhood_state.json").read_text(encoding="utf-8"))
        self.assertIsNone(nb["environment"]["season"])
        self.assertFalse(nb["environment"]["season_explicit"])
        self.assertEqual(nb["environment"]["weather"], "clear")

        # Dynamic summer season resolves on June 21 (never locked to autumn!)
        spark_view = v05_spark.project_v05_inner_spark_state(
            root, has_subject_capability=True, now_iso="2026-06-21T10:00:00+08:00"
        )
        self.assertEqual(spark_view["season_solar"]["season"], "summer")

        # Calm Gate: empty unbootstrapped root stays 0 chars; bootstrapped calm root stays quiet & zero-write
        empty_root = self._tmp_dir("s1-calm-empty-")
        empty_bd = lcb.measure_context_budget_breakdown(
            empty_root, cfg={}, incoming_text="你好", now_iso="2026-11-16T11:00:00+08:00"
        )
        self.assertTrue(empty_bd["calm_silent"])
        self.assertEqual(empty_bd["total_chars"], 0)

        before_ctx = _snapshot_tree(root)
        cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
        bd = lcb.measure_context_budget_breakdown(
            root, cfg=cfg, incoming_text="你好", now_iso="2026-07-15T11:00:00+08:00"
        )
        self.assertEqual(bd["per_section_chars"].get("v04_line", 0), 0)
        self.assertLessEqual(bd["total_chars"], lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertNotIn("秋意", bd["rendered_text"])
        self.assertEqual(_snapshot_tree(root), before_ctx)

        # Idempotency: modify one file and re-run bootstrap -> must NOT overwrite
        spark_path = root / "life" / "v05_inner_spark.json"
        spark_doc = json.loads(spark_path.read_text(encoding="utf-8"))
        spark_doc["revision"] = 9
        spark_path.write_text(json.dumps(spark_doc, ensure_ascii=False) + "\n", encoding="utf-8")

        res2 = nyairo_day.bootstrap_living_foundation_state(root, now_iso="2026-06-21T09:00:00+08:00")
        self.assertEqual(res2["created_count"], 0)
        self.assertEqual(len(res2["skipped_existing"]), 7)
        reloaded_spark = json.loads(spark_path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded_spark["revision"], 9)

    def test_03_ensure_instance_defaults_lived_needs_and_bootstraps_managed_only(self) -> None:
        # 1. --lived-needs-thirst without --lived-needs MUST still raise LIVED_NEEDS_INVALID
        args_bad = nyairo_day.build_parser().parse_args(
            ["init", "--root", "/tmp/x", "--lived-needs-thirst", "0.4"]
        )
        with self.assertRaises(RuntimeError) as caught:
            nyairo_day._lived_needs_from_args(args_bad)
        self.assertIn("LIVED_NEEDS_INVALID", str(caught.exception))

        # 2. --no-lived-needs flag parses cleanly and conflicts with --lived-needs
        args_no = nyairo_day.build_parser().parse_args(
            ["init", "--root", "/tmp/x", "--no-lived-needs"]
        )
        self.assertTrue(args_no.no_lived_needs)
        self.assertIsNone(nyairo_day._lived_needs_from_args(args_no))

        args_both = nyairo_day.build_parser().parse_args(
            ["init", "--root", "/tmp/x", "--lived-needs", "--no-lived-needs"]
        )
        with self.assertRaises(RuntimeError):
            nyairo_day._lived_needs_from_args(args_both)

        # 3. Brand-new managed instance defaults lived_needs to enabled(0,0,0) and runs bootstrap
        parent = self._tmp_dir("s1-ensure-new-")
        new_root = parent / "fresh-day"
        captured_prepare_calls: list[dict] = []
        captured_enable_calls: list[dict] = []

        def fake_prepare_demo(root_arg, **kwargs):
            captured_prepare_calls.append({"root": root_arg, **kwargs})
            root_arg.mkdir(parents=True, exist_ok=True)
            world_home = root_arg / "one-day" / "world-home"
            (world_home / "data" / "body").mkdir(parents=True, exist_ok=True)
            (root_arg / "phone").mkdir(parents=True, exist_ok=True)
            (root_arg / "phone" / "instance.json").write_text(
                json.dumps({"world_home": str(world_home), "subject": "chiyo"}),
                encoding="utf-8",
            )
            return {"world_home": str(world_home), "world_package": {}}

        def fake_enable_profile(body_dir, **kwargs):
            captured_enable_calls.append({"body_dir": body_dir, **kwargs})
            return {"ok": True}

        with (
            mock.patch("components.phone.demo.prepare_demo", side_effect=fake_prepare_demo),
            mock.patch("body_lived_needs_enable.enable_profile", side_effect=fake_enable_profile),
            mock.patch(
                "components.phone.life.enable_instance_memory",
                return_value={"status": "READY", "owner": "chiyo"},
            ),
            mock.patch(
                "components.phone.service.load_instance",
                side_effect=lambda r: json.loads((Path(r) / "phone" / "instance.json").read_text(encoding="utf-8")),
            ),
        ):
            report = nyairo_day._ensure_instance(new_root)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "PREPARED")
        self.assertEqual(
            captured_prepare_calls[0]["lived_needs"],
            {"enabled": True, "hunger": 0.0, "thirst": 0.0, "toilet": 0.0},
        )
        self.assertEqual(len(captured_enable_calls), 1)
        self.assertEqual(report["living_foundation_bootstrap"]["created_count"], 7)
        self.assertTrue((new_root / "phone" / "v03_clock_state.json").is_file())

        # 4. Brand-new root with no_lived_needs=True keeps lived_needs=None
        opt_out_root = parent / "opt-out-day"
        captured_prepare_calls.clear()
        captured_enable_calls.clear()
        with (
            mock.patch("components.phone.demo.prepare_demo", side_effect=fake_prepare_demo),
            mock.patch("body_lived_needs_enable.enable_profile", side_effect=fake_enable_profile),
            mock.patch(
                "components.phone.life.enable_instance_memory",
                return_value={"status": "READY", "owner": "chiyo"},
            ),
            mock.patch(
                "components.phone.service.load_instance",
                side_effect=lambda r: json.loads((Path(r) / "phone" / "instance.json").read_text(encoding="utf-8")),
            ),
        ):
            opt_report = nyairo_day._ensure_instance(opt_out_root, no_lived_needs=True)
        self.assertTrue(opt_report["ok"])
        self.assertIsNone(captured_prepare_calls[0]["lived_needs"])
        self.assertEqual(len(captured_enable_calls), 0)

        # 5. ALREADY_PREPARED existing managed root (like /root/.nyairo-day) gets missing foundation seeded
        existing_root = self._tmp_dir("s1-existing-managed-")
        (existing_root / "phone").mkdir(parents=True, exist_ok=True)
        (existing_root / "run").mkdir(parents=True, exist_ok=True)
        (existing_root / "phone" / "instance.json").write_text(
            json.dumps({"world_home": str(existing_root / "one-day" / "world-home"), "subject": "chiyo"}),
            encoding="utf-8",
        )
        (existing_root / "run" / nyairo_day.MANAGED_NAME).write_text(
            json.dumps({"schema": nyairo_day.SCHEMA_MANAGED}), encoding="utf-8"
        )
        with (
            mock.patch(
                "components.phone.life.enable_instance_memory",
                return_value={"status": "READY", "owner": "chiyo"},
            ),
            mock.patch(
                "components.phone.service.load_instance",
                return_value={"world_home": str(existing_root / "one-day" / "world-home"), "subject": "chiyo"},
            ),
        ):
            upgrade_rep = nyairo_day._ensure_instance(existing_root)
        self.assertEqual(upgrade_rep["status"], "ALREADY_PREPARED")
        self.assertEqual(upgrade_rep["living_foundation_bootstrap"]["created_count"], 7)
        self.assertTrue((existing_root / "world" / "v05_living_town.json").is_file())

        # 6. Foreign unmanaged instance is blocked and receives ZERO bootstrap files
        foreign_root = self._tmp_dir("s1-foreign-")
        (foreign_root / "phone").mkdir(parents=True, exist_ok=True)
        (foreign_root / "phone" / "instance.json").write_text(
            json.dumps({"world_home": str(foreign_root), "subject": "chiyo"}),
            encoding="utf-8",
        )
        before_foreign = _snapshot_tree(foreign_root)
        with mock.patch(
            "components.phone.service.load_instance",
            return_value={"world_home": str(foreign_root), "subject": "chiyo"},
        ):
            blocked = nyairo_day._ensure_instance(foreign_root)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "INSTANCE_NOT_CREATED_BY_THIS_ENTRY")
        self.assertEqual(_snapshot_tree(foreign_root), before_foreign)

    def test_04_heartbeat_ticks_weather_living_town_and_frost_without_spurious_dream(self) -> None:
        root = self._tmp_dir("s1-hb-full-")
        t0 = "2026-06-21T06:00:00+08:00"
        nyairo_day.bootstrap_living_foundation_state(root, now_iso=t0)

        # Sketch early-morning frost at 06:00 (cool & misty)
        v05_spark.sketch_window_frost(
            root,
            text="清晨薄雾",
            weather="clear",
            temp_c=8.0,
            humidity_hint="misty",
            window_misty=True,
            now_iso=t0,
            actor_side="partner",
        )

        # Verify weather pulse is called BEFORE advance_v04_neighborhood_processes
        call_order: list[str] = []
        orig_proc = v04_nb.advance_v04_neighborhood_processes
        orig_weather = getattr(v04_nb, "advance_autonomous_weather_pulse", None)

        def fake_weather(day_root, *, now_iso=None, force=False):
            call_order.append("weather_pulse")
            if callable(orig_weather):
                return orig_weather(day_root, now_iso=now_iso, force=force)
            return {"changed": True, "slot_key": "2026-06-21:S1", "weather": "drizzle", "airflow": "medium", "llm_calls": 0}

        def wrapped_proc(day_root, *, now_iso=None):
            call_order.append("neighborhood_processes")
            return orig_proc(day_root, now_iso=now_iso)

        t_plus_20m = "2026-06-21T06:20:00+08:00"
        town_before = json.loads((root / "world" / "v05_living_town.json").read_text(encoding="utf-8"))
        with (
            mock.patch.object(v04_nb, "advance_autonomous_weather_pulse", side_effect=fake_weather, create=True),
            mock.patch.object(v04_nb, "advance_v04_neighborhood_processes", side_effect=wrapped_proc),
        ):
            res_20m = nyairo_day.run_v03_background_clock_tick(root, now_iso=t_plus_20m, force_init=False)

        self.assertTrue(res_20m["ok"])
        self.assertEqual(res_20m["llm_calls"], 0)
        self.assertEqual(call_order, ["weather_pulse", "neighborhood_processes"])
        self.assertIn("v03_clock", res_20m["slices_ticked"])
        self.assertIn("v04_neighborhood", res_20m["slices_ticked"])
        self.assertIn("v05_living_town", res_20m["slices_ticked"])
        self.assertIn("weather_pulse", res_20m["v04_neighborhood_receipt"])

        town_after = json.loads((root / "world" / "v05_living_town.json").read_text(encoding="utf-8"))
        self.assertGreater(town_after["revision"], town_before["revision"])

        # No spurious morning dream during awake daytime ticks!
        spark_after_20m = json.loads((root / "life" / "v05_inner_spark.json").read_text(encoding="utf-8"))
        self.assertIsNone(spark_after_20m["sleep_wake_state"]["last_dream_roll_date"])
        self.assertIsNone(spark_after_20m["sleep_wake_state"]["morning_dream"])

        # Tick at +1m (06:21, <900s since town pulse, no weather change): v05_living_town skips with 0 churn
        with mock.patch.object(
            v04_nb,
            "advance_autonomous_weather_pulse",
            return_value={"changed": False, "slot_key": "2026-06-21:S1", "llm_calls": 0},
            create=True,
        ):
            res_21m = nyairo_day.run_v03_background_clock_tick(
                root, now_iso="2026-06-21T06:21:00+08:00", force_init=False
            )
        self.assertNotIn("v05_living_town", res_21m["slices_ticked"])

        # Noon tick at 13:00 -> melts window frost on daylight warmth, still NO spurious dream!
        res_noon = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-06-21T13:00:00+08:00", force_init=False
        )
        self.assertIn("v05_inner_spark", res_noon["slices_ticked"])
        self.assertTrue(res_noon["v05_inner_spark_receipt"]["frost_melted"])
        self.assertFalse(res_noon["v05_inner_spark_receipt"]["morning_dream_settled"])

        spark_noon = json.loads((root / "life" / "v05_inner_spark.json").read_text(encoding="utf-8"))
        self.assertTrue(spark_noon["window_frost"]["melted"])
        self.assertIsNone(spark_noon["sleep_wake_state"]["last_dream_roll_date"])

        # Subsequent tick at 13:01 -> v05_inner_spark has nothing changed -> zero write
        spark_mtime_before = (root / "life" / "v05_inner_spark.json").stat().st_mtime_ns
        res_1301 = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-06-21T13:01:00+08:00", force_init=False
        )
        self.assertNotIn("v05_inner_spark", res_1301["slices_ticked"])
        self.assertEqual((root / "life" / "v05_inner_spark.json").stat().st_mtime_ns, spark_mtime_before)

    def test_05_inner_spark_heartbeat_sleep_to_wake_transition_and_explicit_wake(self) -> None:
        root = self._tmp_dir("s1-sleep-wake-")
        (root / "phone").mkdir(parents=True, exist_ok=True)
        world_home = root / "one-day" / "world-home"
        body_dir = world_home / "data" / "body"
        body_dir.mkdir(parents=True, exist_ok=True)
        (root / "phone" / "instance.json").write_text(
            json.dumps({"world_home": str(world_home), "subject": "chiyo"}),
            encoding="utf-8",
        )
        nyairo_day.bootstrap_living_foundation_state(root, now_iso="2026-06-21T23:00:00+08:00")

        store = br.BodyStore(body_dir)
        # 1. Enter active sleep_like recovery
        asleep_rt = br.initial_runtime(at="2026-06-21T23:00:00+08:00")
        asleep_rt["recovery"] = {
            "state": "active",
            "started_at": "2026-06-21T23:00:00+08:00",
            "last_advanced_at": "2026-06-21T23:30:00+08:00",
            "mode": "sleep_like",
            "progress": 0.2,
            "trend": "improving",
        }
        store.save_runtime(asleep_rt)

        hb_sleep = v05_spark.advance_inner_spark_heartbeat(
            root, now_iso="2026-06-21T23:35:00+08:00", woke_from_sleep=False
        )
        self.assertTrue(hb_sleep["changed"])
        self.assertTrue(hb_sleep["sleep_phase_updated"])
        self.assertFalse(hb_sleep["morning_dream_settled"])

        # While still asleep, next tick performs 0 writes
        mtime_asleep = (root / "life" / "v05_inner_spark.json").stat().st_mtime_ns
        hb_still_sleep = v05_spark.advance_inner_spark_heartbeat(
            root, now_iso="2026-06-22T02:00:00+08:00", woke_from_sleep=False
        )
        self.assertFalse(hb_still_sleep["changed"])
        self.assertEqual((root / "life" / "v05_inner_spark.json").stat().st_mtime_ns, mtime_asleep)

        # 2. Transition out of sleep_like -> completed
        awake_rt = dict(asleep_rt)
        awake_rt["recovery"] = {
            "state": "completed",
            "started_at": "2026-06-21T23:00:00+08:00",
            "last_advanced_at": "2026-06-22T07:00:00+08:00",
            "mode": "sleep_like",
            "progress": 1.0,
            "trend": "improving",
        }
        store.save_runtime(awake_rt)

        hb_wake = v05_spark.advance_inner_spark_heartbeat(
            root, now_iso="2026-06-22T07:05:00+08:00", woke_from_sleep=False
        )
        self.assertTrue(hb_wake["changed"])
        self.assertTrue(hb_wake["morning_dream_settled"])
        self.assertIsNotNone(hb_wake["dream_receipt"])

        spark_doc = json.loads((root / "life" / "v05_inner_spark.json").read_text(encoding="utf-8"))
        self.assertEqual(spark_doc["sleep_wake_state"]["phase"], "just_woke")
        self.assertEqual(spark_doc["sleep_wake_state"]["last_dream_roll_date"], "2026-06-22")

        # 3. Explicit woke_from_sleep=True on next morning also settles cleanly
        hb_explicit = v05_spark.advance_inner_spark_heartbeat(
            root, now_iso="2026-06-23T07:30:00+08:00", woke_from_sleep=True
        )
        self.assertTrue(hb_explicit["changed"])
        self.assertTrue(hb_explicit["morning_dream_settled"])
        spark_doc2 = json.loads((root / "life" / "v05_inner_spark.json").read_text(encoding="utf-8"))
        self.assertEqual(spark_doc2["sleep_wake_state"]["last_dream_roll_date"], "2026-06-23")


class Stage1BodyCompactionAndSaturationTest(unittest.TestCase):
    def _tmp_dir(self, prefix: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return tmp

    def _make_stub_service(self, body_dir: Path, initial_state: dict, start_dt: datetime):
        clock = {"now": start_dt}
        svc = wbs.WorldBodyService.__new__(wbs.WorldBodyService)
        svc.br = br
        svc.body_store = br.BodyStore(body_dir)
        svc.lived_needs_enabled = True
        svc.body_state = initial_state
        svc.counters = {"ticks": 0, "idle_ticks": 0, "writes": 0, "reconciliations": 0}
        svc._lived_needs_last_runtime_saved_at = None
        svc._clock = lambda: clock["now"]
        svc._now = None
        return svc, clock

    def test_06_saturated_lived_needs_skips_transitions_and_refreshes_runtime_at_900s(self) -> None:
        body_dir = self._tmp_dir("s1-sat-needs-")
        t0 = datetime(2026, 6, 21, 8, 0, 0, tzinfo=timezone.utc)
        prof = bln.build_profile(hunger=1.0, thirst=1.0, toilet=1.0)
        ln_state = bln.initial_state(prof)
        ln_state["last_live_at"] = t0.isoformat()
        rt = br.initial_runtime(at=t0.isoformat(), lived_needs=ln_state)

        svc, clock = self._make_stub_service(body_dir, rt, t0)
        svc.body_store.save_runtime(rt)
        svc._lived_needs_last_runtime_saved_at = t0

        # Ticks every 60s up to 840s -> 0 transitions appended, 0 runtime saves!
        for step in range(1, 15):
            clock["now"] = t0 + timedelta(seconds=60 * step)
            res = svc._advance_lived_needs_window()
            self.assertIsNotNone(res)
            self.assertFalse(res["levels_changed"])
            self.assertTrue(res["transition_skipped"])
            self.assertFalse(res["wrote"], f"unexpected runtime write at +{60 * step}s")
        self.assertEqual(svc.counters["writes"], 0)
        self.assertEqual(svc.body_store.read_transitions(), [])

        # Tick at +900s -> runtime.json refreshed once, STILL 0 transitions appended!
        clock["now"] = t0 + timedelta(seconds=900)
        res_900 = svc._advance_lived_needs_window()
        self.assertIsNotNone(res_900)
        self.assertFalse(res_900["levels_changed"])
        self.assertTrue(res_900["transition_skipped"])
        self.assertTrue(res_900["wrote"])
        self.assertEqual(svc.counters["writes"], 1)
        self.assertEqual(svc.body_store.read_transitions(), [])

        # Next tick at +960s -> back to 0 writes
        clock["now"] = t0 + timedelta(seconds=960)
        res_960 = svc._advance_lived_needs_window()
        self.assertFalse(res_960["wrote"])
        self.assertEqual(svc.counters["writes"], 1)

        # Un-saturated needs DO append transitions normally
        prof_grow = bln.build_profile(hunger=0.2, thirst=0.2, toilet=0.2)
        ln_grow = bln.initial_state(prof_grow)
        ln_grow["last_live_at"] = t0.isoformat()
        rt_grow = br.initial_runtime(at=t0.isoformat(), lived_needs=ln_grow)
        grow_dir = self._tmp_dir("s1-grow-needs-")
        svc2, clock2 = self._make_stub_service(grow_dir, rt_grow, t0)
        clock2["now"] = t0 + timedelta(seconds=60)
        res_grow = svc2._advance_lived_needs_window()
        self.assertTrue(res_grow["levels_changed"])
        self.assertFalse(res_grow["transition_skipped"])
        self.assertTrue(res_grow["wrote"])
        self.assertEqual(len(svc2.body_store.read_transitions()), 1)

    def test_07_compact_transitions_shrinks_over_4096_lines_atomically_and_heartbeat_wires_it(self) -> None:
        # 1. Zero-write on absent file (must not even create directory or .lock)
        empty_root = self._tmp_dir("s1-compact-empty-")
        absent_body = empty_root / "never_body"
        res_abs = br.BodyStore(absent_body).compact_transitions_if_needed()
        self.assertFalse(res_abs["compacted"])
        self.assertEqual(res_abs["reason"], "absent")
        self.assertFalse(absent_body.exists())

        # 2. Managed instance with >4096 valid lines + corrupt lines -> atomically keeps latest 4096
        root = self._tmp_dir("s1-compact-live-")
        world_home = root / "one-day" / "world-home"
        body_dir = world_home / "data" / "body"
        body_dir.mkdir(parents=True, exist_ok=True)
        (root / "phone").mkdir(parents=True, exist_ok=True)
        (root / "phone" / "instance.json").write_text(
            json.dumps({"world_home": str(world_home), "subject": "chiyo"}),
            encoding="utf-8",
        )
        t0 = datetime(2026, 6, 21, 0, 0, 0, tzinfo=timezone.utc)
        rt = br.initial_runtime(at=t0.isoformat())
        total_valid = br.MAX_TRANSITIONS + 184  # 4280 > 4096
        lines: list[str] = []
        for idx in range(total_valid):
            if idx in (10, 500, 4200):
                lines.append("{corrupt json line\n")
            tr = _make_sample_transition(rt, (t0 + timedelta(seconds=idx)).isoformat(), idx)
            lines.append(json.dumps(tr, ensure_ascii=False, sort_keys=True) + "\n")
        tr_path = body_dir / br.TRANSITIONS_NAME
        tr_path.write_text("".join(lines), encoding="utf-8")

        # Run via nyairo_day heartbeat compaction step!
        hb = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-06-21T10:00:00+08:00", force_init=False
        )
        self.assertTrue(hb["ok"])
        self.assertIn("body_transitions_compaction", hb["slices_ticked"])
        rcpt = hb["body_transitions_compaction_receipt"]
        self.assertTrue(rcpt["compacted"])
        self.assertEqual(rcpt["before_records"], total_valid)
        self.assertEqual(rcpt["after_records"], br.MAX_TRANSITIONS)
        self.assertEqual(rcpt["dropped_records"], 184)
        self.assertEqual(rcpt["invalid_lines"], 3)
        self.assertLess(rcpt["after_bytes"], rcpt["before_bytes"])

        # Verify exact latest 4096 records preserved in order and zero leftover .tmp files
        store = br.BodyStore(body_dir)
        kept = store.read_transitions()
        self.assertEqual(len(kept), br.MAX_TRANSITIONS)
        self.assertEqual(kept[0]["input_summary"]["seq"], 184)
        self.assertEqual(kept[-1]["input_summary"]["seq"], total_valid - 1)
        self.assertEqual(list(body_dir.glob("*.tmp")), [])

        # Calling compact again on already-capped 4096 records does NOT rewrite
        mtime_after = tr_path.stat().st_mtime_ns
        res_again = store.compact_transitions_if_needed()
        self.assertFalse(res_again["compacted"])
        self.assertEqual(tr_path.stat().st_mtime_ns, mtime_after)


class Stage1AgencyDepthAndWalProbeTest(unittest.TestCase):
    def _tmp_dir(self, prefix: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return tmp

    def test_08_v02_agency_depth_legacy_double_state_migration_and_s03_discovery(self) -> None:
        day_root = self._tmp_dir("s1-agency-paths-")
        life_state = day_root / "one-day" / "life" / "state"
        legacy_nested = life_state / "state" / "v02_agency_depth_state.json"
        legacy_nested.parent.mkdir(parents=True, exist_ok=True)
        (day_root / "phone").mkdir(parents=True, exist_ok=True)
        (day_root / "phone" / "instance.json").write_text(
            json.dumps({"world_home": str(day_root / "one-day" / "world-home"), "subject": "chiyo"}),
            encoding="utf-8",
        )
        (day_root / "run").mkdir(parents=True, exist_ok=True)
        (day_root / "run" / nyairo_day.MANAGED_NAME).write_text(
            json.dumps({"schema": nyairo_day.SCHEMA_MANAGED}), encoding="utf-8"
        )
        # Seed existing S03 DB under one-day/s03/claims.sqlite3
        s03_live_db = day_root / "one-day" / "s03" / "claims.sqlite3"
        s03_live_db.parent.mkdir(parents=True, exist_ok=True)

        # Seed legacy double state/state/ file with a preference
        legacy_doc = ad_engine._empty_raw_state()
        legacy_doc["revision"] = 3
        legacy_doc["preferences"] = [
            {
                "item_key": "oolong_tea",
                "label": "炭焙乌龙",
                "status": "SUPPORTED",
                "spontaneous_count": 3,
                "satiation_score": 0.1,
                "last_touched_at": "2026-06-20T10:00:00+08:00",
                "updated_at": "2026-06-20T10:00:00+08:00",
            }
        ]
        legacy_nested.write_text(json.dumps(legacy_doc, ensure_ascii=False) + "\n", encoding="utf-8")

        canonical_path = day_root / "state" / "v02_agency_depth_state.json"
        self.assertFalse(canonical_path.exists())

        # 1. Zero-write read from BOTH day_root and nested life_state sees legacy data without writing!
        before_read = _snapshot_tree(day_root)
        view_root = ad_engine.project_v02_agency_depth(
            day_root, has_subject_capability=True, now_iso="2026-06-21T10:00:00+08:00"
        )
        view_nested = ad_engine.project_v02_agency_depth(
            life_state, has_subject_capability=True, now_iso="2026-06-21T10:00:00+08:00"
        )
        self.assertEqual(len(view_root["preferences"]), 1)
        self.assertEqual(view_root["preferences"][0]["item_key"], "oolong_tea")
        self.assertEqual(len(view_nested["preferences"]), 1)
        self.assertEqual(view_nested["preferences"][0]["item_key"], "oolong_tea")
        self.assertFalse(canonical_path.exists())
        self.assertEqual(_snapshot_tree(day_root), before_read)

        # 2. Write mutation via nested life_state writes ONLY to canonical <day_root>/state/v02_agency_depth_state.json
        #    and persists concluded inquiry to <day_root>/one-day/s03/claims.sqlite3!
        open_data, open_status = ad_engine.apply_agency_depth_mutation(
            life_state,
            {
                "action": "open_bounded_inquiry",
                "topic_tag": "roof_rain_sound",
                "question": "檐角滴水声是从哪边来的",
                "shared": True,
                "candidate_explanations": [
                    {"summary_cn": "以为是空调排水管", "stance": "ruled_out"},
                    {"summary_cn": "其实是屋檐铜链汇水", "stance": "plausible"},
                ],
                "operation_key": "op_s1_open_inq",
            },
            actor_side="subject",
            now_iso="2026-06-21T10:05:00+08:00",
        )
        self.assertEqual(open_status, "APPLIED")
        self.assertTrue(canonical_path.is_file())
        canonical_doc = json.loads(canonical_path.read_text(encoding="utf-8"))
        self.assertEqual(len(canonical_doc["preferences"]), 1)
        inq_id = open_data["inquiries"][0]["inquiry_id"]

        ad_engine.apply_agency_depth_mutation(
            life_state,
            {
                "action": "record_inquiry_evidence",
                "inquiry_id": inq_id,
                "note": "开窗看了一眼，是屋檐铜链汇下的雨水",
                "source_ref": "obs:eaves_chain_01",
                "operation_key": "op_s1_ev_inq",
            },
            actor_side="subject",
            now_iso="2026-06-21T10:10:00+08:00",
        )
        con_data, con_status = ad_engine.apply_agency_depth_mutation(
            life_state,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": inq_id,
                "outcome": "CONCLUDED",
                "conclusion": "雨声来自屋檐铜链引水，并非空调漏水",
                "operation_key": "op_s1_conclude_inq",
            },
            actor_side="subject",
            now_iso="2026-06-21T10:15:00+08:00",
        )
        self.assertEqual(con_status, "APPLIED")
        self.assertTrue(s03_live_db.is_file(), "S03 claim should persist to one-day/s03/claims.sqlite3")
        rev = con_data["belief_revisions"][0]
        self.assertTrue(str(rev["s03_claim_id"]).startswith("claim:"))
        svc = s03.S03ClaimService(s03_live_db, subject="inquiry:roof_rain_sound", create=False)
        cur = svc.current_view(now="2026-06-21T10:15:00+08:00")
        self.assertEqual(cur["counts"]["claims"], 1)
        self.assertEqual(cur["claims"][0]["claim_id"], rev["s03_claim_id"])

    def test_09_life_runtime_production_finds_v02_canonical_life_sqlite_on_startup(self) -> None:
        day_root = self._tmp_dir("s1-wal-probe-")
        life_state = day_root / "one-day" / "life" / "state"
        life_state.mkdir(parents=True, exist_ok=True)
        (day_root / "phone").mkdir(parents=True, exist_ok=True)
        (day_root / "phone" / "instance.json").write_text(
            json.dumps({"world_home": str(day_root / "one-day" / "world-home"), "subject": "chiyo"}),
            encoding="utf-8",
        )
        (day_root / "run").mkdir(parents=True, exist_ok=True)
        (day_root / "run" / nyairo_day.MANAGED_NAME).write_text(
            json.dumps({"schema": nyairo_day.SCHEMA_MANAGED}), encoding="utf-8"
        )

        v02_dir = day_root / "v02"
        rec_store = cpr.CrashAndPartialReconcilerStore(v02_dir)
        rec_store.initialize()
        self.assertTrue((v02_dir / "canonical_life.sqlite3").is_file())
        self.assertEqual(
            instance_paths.resolve_instance_paths(life_state).canonical_ledger_db,
            v02_dir / "canonical_life.sqlite3",
        )

        # Call reconcile_canonical_wal_on_startup on a lightweight ProductionLifeRuntime instance
        runtime_stub = lrp.ProductionLifeRuntime.__new__(lrp.ProductionLifeRuntime)
        runtime_stub.state_root = life_state
        recovery = runtime_stub.reconcile_canonical_wal_on_startup()
        self.assertTrue(recovery["reconciler_present"])
        self.assertTrue(recovery["recovered"])
        self.assertEqual(Path(recovery["reconciler_root"]), v02_dir)
        self.assertEqual(recovery["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()
