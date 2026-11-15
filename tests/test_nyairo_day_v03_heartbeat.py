#!/usr/bin/env python3
"""Unit & CLI integration tests for 0.3 zero-LLM deterministic background clock heartbeat
and ``clock-tick`` subcommand in ``scripts/nyairo_day.py``.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)


def _entry_mod():
    return importlib.import_module("nyairo_day")


class NyairoDayV03HeartbeatTest(unittest.TestCase):
    def _make_tmp_dir(self, prefix: str = "nyairo-v03-hb-") -> Path:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(lambda: _cleanup_dir(tmp))
        return tmp

    def test_uninitialized_directory_skips_with_zero_disk_writes(self) -> None:
        entry = _entry_mod()
        root = self._make_tmp_dir("v03-uninit-")
        # Simulate a clean post-init skeleton without any 0.3 state files
        (root / "run").mkdir(parents=True, exist_ok=True)
        (root / "phone").mkdir(parents=True, exist_ok=True)
        marker = root / "run" / entry.MANAGED_NAME
        marker.write_text(json.dumps({"schema": entry.SCHEMA_MANAGED}), encoding="utf-8")
        before_files = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

        res = entry.run_v03_background_clock_tick(
            root, now_iso="2026-11-12T10:00:00+00:00", force_init=False
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "skipped_uninitialized")
        self.assertEqual(res["llm_calls"], 0)
        self.assertFalse(res["model_called"])
        self.assertEqual(res["slices_ticked"], [])

        after_files = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        self.assertEqual(after_files, before_files)

    def test_nonexistent_root_with_force_init_false_creates_nothing(self) -> None:
        entry = _entry_mod()
        parent = self._make_tmp_dir("v03-absent-parent-")
        absent_root = parent / "never-created"

        res = entry.run_v03_background_clock_tick(absent_root, force_init=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "skipped_uninitialized")
        self.assertEqual(res["llm_calls"], 0)
        self.assertEqual(res["slices_ticked"], [])
        self.assertFalse(absent_root.exists())

    def test_tick_advances_outerwear_evaporates_thoughts_and_never_guesses_unknown(self) -> None:
        from components.life.scripts.v03_clock_evaporation import (
            mutate_v03_clock_view,
            read_v03_clock_state,
            read_v03_clock_view,
        )

        entry = _entry_mod()
        root = self._make_tmp_dir("v03-advance-7h-")
        t0 = "2026-11-12T08:00:00+00:00"
        t_plus_7h = "2026-11-12T15:00:00+00:00"

        # 1. Declare damp outerwear (6h drying curve)
        st1, env1 = mutate_v03_clock_view(
            root,
            {
                "action": "declare_slow_variable",
                "var_id": "coat-rain-01",
                "var_kind": "damp_outerwear",
                "label": "淋过小雨的风衣",
                "started_at": t0,
            },
            actor_side="subject",
            now_iso=t0,
        )
        self.assertEqual(st1, 200, env1)

        # 2. Record an EVAPORABLE fleeting thought (30m TTL)
        st2, env2 = mutate_v03_clock_view(
            root,
            {
                "action": "record_evaporable",
                "thought_id": "evap-cloud-01",
                "domain_kind": "fleeting_thought",
                "accountability_tier": "EVAPORABLE",
                "text": "窗外云影晃了一下",
                "ttl_seconds": 1800,
            },
            actor_side="subject",
            now_iso=t0,
        )
        self.assertEqual(st2, 200, env2)

        # 3. Register an unresolved discrete UNKNOWN action
        st3, env3 = mutate_v03_clock_view(
            root,
            {
                "action": "register_unknown_action",
                "action_id": "unk-delivery-01",
                "domain": "outbound_delivery",
                "summary": "跨渠道慢信回执未明",
                "submitted_at": t0,
            },
            actor_side="subject",
            now_iso=t0,
        )
        self.assertEqual(st3, 200, env3)

        # Advance +7h via nyairo_day background tick helper
        res = entry.run_v03_background_clock_tick(root, now_iso=t_plus_7h, force_init=False)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["status"], "ticked")
        self.assertEqual(res["llm_calls"], 0)
        self.assertFalse(res["model_called"])
        self.assertIn("v03_clock", res["slices_ticked"])
        self.assertEqual(res["settled_slow_vars"], 1)
        self.assertEqual(res["evaporated_count"], 1)
        self.assertEqual(res["unknown_actions_guessed"], 0)
        self.assertEqual(res["cognitive_backfill_count"], 0)

        # Verify persisted state & pure GET view
        stored, exists = read_v03_clock_state(root)
        self.assertTrue(exists)
        self.assertEqual(stored["evaporable_pool"], [])
        coat_rows = [
            sv for sv in stored["world_clock"]["slow_variables"] if sv["var_id"] == "coat-rain-01"
        ]
        self.assertEqual(len(coat_rows), 1)
        self.assertEqual(coat_rows[0]["current_state"], "dry")

        unk_rows = stored["world_clock"]["pending_unknown_actions"]
        self.assertEqual(len(unk_rows), 1)
        self.assertEqual(unk_rows[0]["action_id"], "unk-delivery-01")
        self.assertEqual(unk_rows[0]["effect_state"], "UNKNOWN")
        self.assertFalse(unk_rows[0]["guessed_by_clock"])
        self.assertEqual(stored["cognitive_clock"]["causal_ticks"], 0)

        code, view_env = read_v03_clock_view(root, has_subject_capability=True, now_iso=t_plus_7h)
        self.assertEqual(code, 200)
        view_data = view_env["data"]
        self.assertEqual(
            view_data["slow_variables"]["outerwear_drying"][0]["projected_dryness"], "dry"
        )
        self.assertEqual(
            view_data["accountability_partitions"]["evaporable_pool"]["active_count"], 0
        )
        self.assertEqual(
            view_data["accountability_partitions"]["evaporation_guarantees"]["tombstone_count"], 0
        )

    def test_monotonic_clock_violation_returns_structured_409_without_exception(self) -> None:
        from components.life.scripts.v03_clock_evaporation import read_v03_clock_state

        entry = _entry_mod()
        root = self._make_tmp_dir("v03-monotonic-")

        first = entry.run_v03_background_clock_tick(
            root, now_iso="2026-11-12T16:00:00+00:00", force_init=True
        )
        self.assertTrue(first["ok"])
        state_before, _ = read_v03_clock_state(root)

        # Rewind 4 hours earlier -> must not raise, must preserve state
        backwards = entry.run_v03_background_clock_tick(
            root, now_iso="2026-11-12T12:00:00+00:00", force_init=False
        )
        self.assertFalse(backwards["ok"])
        self.assertEqual(backwards["http_status"], 409)
        self.assertEqual(backwards["reason_code"], "WORLD_CLOCK_MONOTONIC_VIOLATION")
        self.assertEqual(backwards["llm_calls"], 0)
        self.assertEqual(backwards["slices_ticked"], [])
        self.assertEqual(backwards["unknown_actions_guessed"], 0)

        state_after, _ = read_v03_clock_state(root)
        self.assertEqual(state_after["revision"], state_before["revision"])
        self.assertEqual(
            state_after["world_clock"]["last_committed_at"],
            "2026-11-12T16:00:00+00:00",
        )

    def test_background_heartbeat_thread_ticks_and_exits_within_300ms_on_stop(self) -> None:
        from components.life.scripts.v03_clock_evaporation import (
            mutate_v03_clock_view,
            read_v03_clock_state,
        )

        entry = _entry_mod()
        root = self._make_tmp_dir("v03-thread-")
        past_dt = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=7)
        past_iso = past_dt.isoformat(timespec="seconds")

        mutate_v03_clock_view(
            root,
            {
                "action": "declare_slow_variable",
                "var_id": "coat-bg",
                "var_kind": "damp_outerwear",
                "label": "后台晾干测试外套",
                "started_at": past_iso,
            },
            actor_side="subject",
            now_iso=past_iso,
        )
        initial_state, _ = read_v03_clock_state(root)
        initial_rev = initial_state["revision"]

        stop = threading.Event()
        worker = entry._start_v03_clock_heartbeat(root, stop, interval_seconds=0.05)
        self.assertIsNotNone(worker)
        assert worker is not None
        self.assertTrue(worker.daemon)
        self.assertTrue(worker.is_alive())

        try:
            deadline = time.monotonic() + 1.5
            ticked_state = initial_state
            while time.monotonic() < deadline:
                ticked_state, _ = read_v03_clock_state(root)
                if ticked_state["revision"] > initial_rev:
                    break
                time.sleep(0.02)

            self.assertGreater(ticked_state["revision"], initial_rev)
            self.assertEqual(
                ticked_state["world_clock"]["slow_variables"][0]["current_state"], "dry"
            )
        finally:
            t0 = time.monotonic()
            stop.set()
            worker.join(timeout=0.3)
            elapsed_stop = time.monotonic() - t0

        self.assertFalse(worker.is_alive())
        self.assertLess(elapsed_stop, 0.3)

    def test_background_heartbeat_thread_leaves_uninitialized_dir_untouched(self) -> None:
        entry = _entry_mod()
        root = self._make_tmp_dir("v03-thread-uninit-")
        stop = threading.Event()
        worker = entry._start_v03_clock_heartbeat(root, stop, interval_seconds=0.03)
        self.assertIsNotNone(worker)
        assert worker is not None
        try:
            time.sleep(0.10)
        finally:
            stop.set()
            worker.join(timeout=0.3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(list(root.rglob("*")), [])

    def test_non_positive_interval_or_zero_env_disables_heartbeat_thread(self) -> None:
        entry = _entry_mod()
        root = self._make_tmp_dir("v03-disabled-")
        stop = threading.Event()

        self.assertIsNone(entry._start_v03_clock_heartbeat(root, stop, interval_seconds=0.0))
        self.assertIsNone(entry._start_v03_clock_heartbeat(root, stop, interval_seconds=-10.0))
        with mock.patch.dict(os.environ, {"NYAIRO_V03_CLOCK_INTERVAL": "0"}):
            self.assertEqual(entry._resolve_v03_clock_interval(None), 0.0)
            self.assertIsNone(entry._start_v03_clock_heartbeat(root, stop))

    def test_serve_forever_and_spawn_background_wire_v03_clock_interval(self) -> None:
        from components.life.scripts.v03_clock_evaporation import (
            mutate_v03_clock_view,
            read_v03_clock_state,
        )

        entry = _entry_mod()
        root = self._make_tmp_dir("v03-serve-wire-")
        past_dt = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=7)
        past_iso = past_dt.isoformat(timespec="seconds")
        mutate_v03_clock_view(
            root,
            {
                "action": "declare_slow_variable",
                "var_id": "coat-serve",
                "var_kind": "damp_outerwear",
                "label": "serve期间晾干外套",
                "started_at": past_iso,
            },
            actor_side="subject",
            now_iso=past_iso,
        )
        before_rev = read_v03_clock_state(root)[0]["revision"]

        def fake_serve(_root_path: Path, **kwargs):
            stop_ev = kwargs["stop_event"]
            # Allow at least one 0.04s heartbeat cycle, then trigger normal stop
            time.sleep(0.11)
            stop_ev.set()

        with mock.patch("components.phone.demo.serve_instance", side_effect=fake_serve):
            rc = entry._serve_forever(root, 3899, v03_clock_interval=0.04)
        self.assertEqual(rc, 0)
        after_state, _ = read_v03_clock_state(root)
        self.assertGreater(after_state["revision"], before_rev)
        self.assertEqual(
            after_state["world_clock"]["slow_variables"][0]["current_state"], "dry"
        )

        captured_cmds: list[list[str]] = []

        class FakePopen:
            pid = 43210

            def __init__(self, command, **_kwargs):
                captured_cmds.append(list(command))

            def poll(self):
                return 0

        with mock.patch("subprocess.Popen", FakePopen):
            entry._spawn_background(root, 3899, v03_clock_interval=12.5)
            entry._spawn_background(root, 3899)

        self.assertIn("--v03-clock-interval", captured_cmds[0])
        idx = captured_cmds[0].index("--v03-clock-interval")
        self.assertEqual(captured_cmds[0][idx + 1], "12.5")
        self.assertNotIn("--v03-clock-interval", captured_cmds[1])

    def test_tick_also_advances_existing_v03_ecology_illness_and_expired_outbound(self) -> None:
        from components.world.scripts import v03_economy_illness_bridge as v03_eco

        entry = _entry_mod()
        root = self._make_tmp_dir("v03-eco-bridge-")
        t0 = "2026-11-12T10:00:00+08:00"
        t_plus_26h = "2026-11-13T12:00:00+08:00"
        # 2026-11-14 反骚扰配额对齐：主动出站最小间隔 48h，两条种子出站错开 49h
        t_minus_49h = "2026-11-10T09:00:00+08:00"

        # Seed strain into prodrome/acute + 1 DELIVERED desktop note + 1 UNKNOWN slow mail
        v03_eco.record_strain_or_rest(
            root, event_kind="caught_in_rain", intensity=1.5, unchanged_damp_clothes=True, now_iso=t0
        )
        v03_eco.attempt_proactive_outbound(
            root,
            text="顺手记下明日书铺开门时间。",
            channel="slow_mail",
            reason_kind="fulfilled_promise",
            simulated_transport_outcome="UNKNOWN",
            now_iso=t_minus_49h,
        )
        v03_eco.attempt_proactive_outbound(
            root,
            text="桌角留了一杯温麦茶。",
            channel="desktop_note",
            reason_kind="genuine_life_share",
            simulated_transport_outcome="DELIVERED",
            now_iso=t0,
        )

        # Advance +26h via background clock tick (v03_clock_state absent, v03_ecology present)
        res = entry.run_v03_background_clock_tick(root, now_iso=t_plus_26h, force_init=False)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["status"], "ticked")
        self.assertEqual(res["llm_calls"], 0)
        self.assertIn("v03_ecology", res["slices_ticked"])
        # v03_clock_state.json was NOT created because force_init=False
        self.assertFalse((root / "phone" / "v03_clock_state.json").exists())

        eco_state, reason = v03_eco.read_stored_ecology_zero_write(root)
        self.assertIsNone(reason)
        records = eco_state["reality_bridge"]["outbound_records"]
        by_channel = {r["channel"]: r for r in records}
        # Expired DELIVERED note withdrawn; UNKNOWN slow_mail untouched
        self.assertEqual(by_channel["desktop_note"]["status"], "WITHDRAWN")
        self.assertEqual(by_channel["slow_mail"]["status"], "UNKNOWN")

    def test_cli_clock_tick_force_init_json_exits_zero_with_zero_llm_calls(self) -> None:
        root = self._make_tmp_dir("v03-cli-force-")
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "nyairo_day.py"),
                "clock-tick",
                "--root",
                str(root),
                "--now",
                "2026-11-12T09:30:00+00:00",
                "--force-init",
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout.strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ticked")
        self.assertEqual(payload["llm_calls"], 0)
        self.assertFalse(payload["model_called"])
        self.assertIn("v03_clock", payload["slices_ticked"])
        self.assertEqual(payload["unknown_actions_guessed"], 0)
        self.assertTrue((root / "phone" / "v03_clock_state.json").is_file())

    def test_cli_clock_tick_without_force_init_preserves_empty_dir_and_exits_zero(self) -> None:
        root = self._make_tmp_dir("v03-cli-uninit-")
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "nyairo_day.py"),
                "clock-tick",
                "--root",
                str(root),
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout.strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "skipped_uninitialized")
        self.assertEqual(payload["llm_calls"], 0)
        self.assertEqual(payload["slices_ticked"], [])
        self.assertEqual(list(root.rglob("*")), [])

    def test_cli_clock_tick_reports_monotonic_violation_cleanly(self) -> None:
        root = self._make_tmp_dir("v03-cli-mono-")
        first = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "nyairo_day.py"),
                "clock-tick",
                "--root",
                str(root),
                "--now",
                "2026-11-12T14:00:00+00:00",
                "--force-init",
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)

        rewind = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "nyairo_day.py"),
                "clock-tick",
                "--root",
                str(root),
                "--now",
                "2026-11-12T11:00:00+00:00",
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(rewind.returncode, 1)
        payload = json.loads(rewind.stdout.strip())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason_code"], "WORLD_CLOCK_MONOTONIC_VIOLATION")
        self.assertEqual(payload["llm_calls"], 0)


def _cleanup_dir(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
