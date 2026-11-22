#!/usr/bin/env python3
"""Stage 2 Pillar 1 tests: Unified Body Authority, Outdoor Rain Exposure, Composable Sleep & Overlays.

100% deterministic, zero-network, zero-LLM (``llm_calls == 0``), and verifies
100% zero-write GET invariants plus LF/no-BOM/no-emoji hygiene across all owned files.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "native" / "integration"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import adult_intimacy_view as aiv  # noqa: E402
from components.phone import somatic_comfort_view as scv  # noqa: E402
from components.phone import v03_embodied_social_view as esv  # noqa: E402
from components.world.scripts import body_health_projection as bhp  # noqa: E402
from components.world.scripts import body_lived_needs as bln  # noqa: E402
from components.world.scripts import instance_paths as ip  # noqa: E402
from components.world.scripts import v08_composable_body as v08_cb  # noqa: E402
import body_capacity_source as bcs  # noqa: E402
import life_body_context as lbc  # noqa: E402

try:
    from components.world.scripts import body_runtime as br  # noqa: E402
except Exception:  # pragma: no cover - non-POSIX fallback
    br = None  # type: ignore[assignment]

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix" and br is not None,
    "BodyStore/WorldBodyService POSIX flock tests run on WSL/Linux",
)

NOW_ISO = "2026-11-20T14:00:00+08:00"
NOW_DT = datetime(2026, 11, 20, 6, 0, 0, tzinfo=timezone.utc)


def _tree_manifest(root: Path) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_dir():
            rows.append((rel + "/", 0, "dir"))
        elif path.is_file():
            blob = path.read_bytes()
            rows.append((rel, len(blob), hashlib.sha256(blob).hexdigest()))
    return rows


def _make_managed_root(
    base: Path,
    *,
    fatigue: float = 0.20,
    warmth: float = 0.50,
    hunger: float = 0.60,
    thirst: float = 0.78,
    toilet: float = 0.20,
    recovery_state: str = "inactive",
    recovery_mode: str | None = None,
) -> tuple[Path, Path]:
    day_root = base / "managed-day"
    world_home = day_root / "one-day" / "world-home"
    body_dir = world_home / "data" / "body"
    body_dir.mkdir(parents=True, exist_ok=True)
    (day_root / "phone").mkdir(parents=True, exist_ok=True)
    (day_root / "instance.json").write_text(
        json.dumps({"world_home": str(world_home)}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    prof = bln.build_profile(hunger=hunger, thirst=thirst, toilet=toilet)
    bln.save_profile(body_dir, prof)
    if br is not None:
        rt = br.initial_runtime(at=NOW_DT, lived_needs=bln.initial_state(prof))
        rt["somatic"]["fatigue"] = float(fatigue)
        rt["somatic"]["warmth"] = float(warmth)
        rt["recovery"]["state"] = recovery_state
        rt["recovery"]["mode"] = recovery_mode
        if recovery_state == "active":
            rt["recovery"]["started_at"] = NOW_DT.isoformat()
            rt["recovery"]["last_advanced_at"] = NOW_DT.isoformat()
        br.BodyStore(body_dir).save_runtime(rt)
        br.BodyStore(body_dir).save_profile(br.build_profile())
    else:
        rt = {
            "schema_version": "body.runtime.v2",
            "kind": "body_runtime_state",
            "runtime_version": "embodiment.runtime.v1",
            "body_id": "chiyo_body",
            "version": 0,
            "valid_from": NOW_DT.isoformat(),
            "last_advanced_at": NOW_DT.isoformat(),
            "somatic": {
                "fatigue": float(fatigue),
                "activation": 0.50,
                "comfort": 0.70,
                "sensory_load": 0.10,
                "tension": 0.15,
                "warmth": float(warmth),
            },
            "recovery": {
                "state": recovery_state,
                "started_at": NOW_DT.isoformat() if recovery_state == "active" else None,
                "last_advanced_at": NOW_DT.isoformat() if recovery_state == "active" else None,
                "mode": recovery_mode,
                "progress": 0.0,
                "trend": "stalled",
            },
            "signal_latch": {},
            "lived_needs": bln.initial_state(prof),
        }
        (body_dir / "runtime.json").write_text(
            json.dumps(rt, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return day_root, world_home


class Stage2SoleAcuteBodyAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="stage2-body-auth-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @POSIX_ONLY
    def test_01_managed_root_relieves_runtime_json_and_never_creates_shadow_lived_needs_file(self) -> None:
        day_root, world_home = _make_managed_root(self.tmp, thirst=0.80, hunger=0.65)
        paths = ip.resolve_instance_paths(day_root)
        self.assertEqual(paths.body_runtime_json, world_home / "data" / "body" / "runtime.json")

        # Verify _candidate_runtime_paths resolves canonical runtime.json first (fixes phone_instance.json typo).
        cands = scv._candidate_runtime_paths(day_root)
        self.assertEqual(cands[0], paths.body_runtime_json)

        ack = scv.apply_lived_needs_relief(
            day_root,
            thirst_relief=0.35,
            hunger_relief=0.15,
            receipt_id="rcpt-stage2-001",
            now_iso=NOW_ISO,
        )
        self.assertTrue(ack["applied"])
        self.assertFalse(ack["replayed"])
        self.assertEqual(ack["authority"], "body_runtime")
        self.assertAlmostEqual(ack["before_levels"]["thirst"], 0.80, places=4)
        self.assertAlmostEqual(ack["after_levels"]["thirst"], 0.45, places=4)
        self.assertAlmostEqual(ack["after_levels"]["hunger"], 0.50, places=4)
        self.assertIsNotNone(ack["transition_id"])

        # Shadow fork <day_root>/body/lived_needs.json MUST NEVER be created on a managed root!
        self.assertFalse((day_root / "body" / "lived_needs.json").exists())

        # Canonical runtime.json has bumped version and updated levels.
        rt_after = br.BodyStore(paths.body_dir).load_runtime()
        self.assertEqual(rt_after["version"], 1)
        self.assertAlmostEqual(rt_after["lived_needs"]["levels"]["thirst"], 0.45, places=4)
        self.assertAlmostEqual(rt_after["lived_needs"]["levels"]["hunger"], 0.50, places=4)

        # CAUSE_LIVED_NEEDS transition appended to transitions.jsonl.
        transitions = br.BodyStore(paths.body_dir).read_transitions()
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]["cause_class"], br.CAUSE_LIVED_NEEDS)
        self.assertEqual(transitions[0]["from_version"], 0)
        self.assertEqual(transitions[0]["to_version"], 1)

        # Idempotent replay does NOT double-relieve or append another transition.
        replay = scv.apply_lived_needs_relief(
            day_root,
            thirst_relief=0.35,
            hunger_relief=0.15,
            receipt_id="rcpt-stage2-001",
            now_iso=NOW_ISO,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(br.BodyStore(paths.body_dir).read_transitions()), 1)
        self.assertAlmostEqual(
            br.BodyStore(paths.body_dir).load_runtime()["lived_needs"]["levels"]["thirst"],
            0.45,
            places=4,
        )

    def test_02_standalone_unit_root_without_world_home_uses_legacy_fallback(self) -> None:
        standalone = self.tmp / "standalone"
        standalone.mkdir()
        ack = scv.apply_lived_needs_relief(
            standalone,
            thirst_relief=0.30,
            receipt_id="rcpt-standalone-001",
            now_iso=NOW_ISO,
        )
        self.assertTrue(ack["applied"])
        self.assertEqual(ack["authority"], "legacy_lived_needs_file")
        self.assertTrue((standalone / "body" / "lived_needs.json").is_file())
        snap = scv.read_lived_needs_snapshot(standalone)
        self.assertEqual(snap["status"], "available")
        self.assertAlmostEqual(snap["levels"]["thirst"], 0.45, places=4)

    @POSIX_ONLY
    def test_03_body_health_projection_enforces_v03_sole_illness_fsm_and_zero_write(self) -> None:
        day_root, world_home = _make_managed_root(
            self.tmp, fatigue=0.25, warmth=0.22, thirst=0.76
        )
        # Put a stale open illness episode in health.json, while v03_ecology.json says healthy.
        stale = bhp.admit_exposure(
            bhp.initial_record(body_id="chiyo_body", subject_id="self", home_ref="", at=NOW_DT),
            episode_id="stale-ep",
            at=NOW_DT,
            origin="world_exposure",
            severity=0.70,
        )
        bhp.HealthStore(world_home / "data" / "body").save(stale)
        eco_path = day_root / "phone" / "v03_ecology.json"
        eco_path.write_text(
            json.dumps({"illness": {"phase": "healthy", "severity": 0.0}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        before = _tree_manifest(day_root)
        view_from_day = bhp.body_health_view(day_root, at=NOW_DT)
        view_from_wh = bhp.body_health_view(world_home, at=NOW_DT)
        self.assertEqual(_tree_manifest(day_root), before)
        self.assertEqual(view_from_day["writes_performed"], 0)
        self.assertEqual(view_from_day["health_status"], "HEALTHY")
        self.assertEqual(view_from_wh["health_status"], "HEALTHY")
        self.assertIsNone(view_from_day["episode"])
        self.assertEqual(view_from_day["illness_authority"], "v03_ecology")
        self.assertEqual(view_from_day["acute_body_authority"], "body_runtime")
        self.assertEqual(view_from_day["thermal_band"], "cold_tendency")
        self.assertEqual(view_from_day["lived_needs_bands"]["thirst"], "noticeable")

        # Now v03_ecology transitions to acute_illness -> sole illness FSM wins immediately.
        eco_path.write_text(
            json.dumps(
                {
                    "illness": {
                        "phase": "acute_illness",
                        "severity": 0.65,
                        "episode_id": "ep:v03-rain",
                        "episode_opened_at": NOW_DT.isoformat(),
                    }
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        acute_view = bhp.body_health_view(day_root, at=NOW_DT)
        self.assertEqual(acute_view["health_status"], "ACUTE_ILLNESS")
        self.assertTrue(acute_view["episode"]["open"])
        self.assertEqual(acute_view["writes_performed"], 0)

    @POSIX_ONLY
    def test_04_world_body_service_refreshes_newer_disk_runtime_without_clobbering(self) -> None:
        import world_body_service as wbsvc

        day_root, world_home = _make_managed_root(self.tmp, thirst=0.80)
        store = br.BodyStore(world_home / "data" / "body")
        old_mem_state = store.load_runtime()
        self.assertEqual(old_mem_state["version"], 0)

        # Simulate phone-side relief updating runtime.json to version 1.
        ack = scv.apply_lived_needs_relief(
            day_root, thirst_relief=0.35, receipt_id="rcpt-sync-1", now_iso=NOW_ISO
        )
        self.assertTrue(ack["applied"])

        shim = SimpleNamespace(body_store=store, body_state=old_mem_state, br=br)
        wbsvc.WorldBodyService._refresh_body_state_if_newer_on_disk(shim)
        self.assertEqual(shim.body_state["version"], 1)
        self.assertAlmostEqual(shim.body_state["lived_needs"]["levels"]["thirst"], 0.45, places=4)

    @POSIX_ONLY
    def test_05_freshly_inited_managed_root_before_world_boot_uses_body_runtime_no_shadow(self) -> None:
        # Case A: Real `nyairo_day.py init` with explicit lived-needs initial values, BEFORE owner-world boots
        # (only `lived_needs_profile.json` exists; neither `runtime.json` nor `profile.json` exists yet).
        init_root = self.tmp / "fresh-init-lived"
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "nyairo_day.py"),
                "init",
                "--root",
                str(init_root),
                "--lived-needs",
                "--lived-needs-thirst",
                "0.75",
                "--lived-needs-hunger",
                "0.55",
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=f"init failed: stdout={proc.stdout}\nstderr={proc.stderr}")
        paths = ip.resolve_instance_paths(init_root)
        self.assertTrue(paths.is_managed_instance)
        self.assertTrue((paths.body_dir / "lived_needs_profile.json").is_file())
        self.assertFalse(paths.body_runtime_json.exists())
        self.assertFalse((paths.body_dir / "profile.json").exists())

        # Before relief: zero-write snapshot reads profile_ready from lived_needs_profile.json.
        snap_before = scv.read_lived_needs_snapshot(init_root)
        self.assertEqual(snap_before["status"], "profile_ready")
        self.assertAlmostEqual(snap_before["levels"]["thirst"], 0.75, places=4)

        ack = scv.apply_lived_needs_relief(
            init_root,
            thirst_relief=0.25,
            hunger_relief=0.15,
            receipt_id="rcpt-fresh-init-001",
            now_iso=NOW_ISO,
        )
        self.assertTrue(ack["applied"])
        self.assertFalse(ack["replayed"])
        self.assertEqual(ack["authority"], "body_runtime")
        self.assertAlmostEqual(ack["before_levels"]["thirst"], 0.75, places=4)
        self.assertAlmostEqual(ack["after_levels"]["thirst"], 0.50, places=4)
        self.assertAlmostEqual(ack["after_levels"]["hunger"], 0.40, places=4)
        self.assertIsNotNone(ack["transition_id"])

        # Shadow fork MUST NEVER be created on a freshly init'd managed root!
        self.assertFalse((init_root / "body" / "lived_needs.json").exists())

        # Canonical runtime.json + CAUSE_LIVED_NEEDS transition are written under world_home/data/body.
        self.assertTrue(paths.body_runtime_json.is_file())
        rt_after = br.BodyStore(paths.body_dir).load_runtime()
        self.assertEqual(rt_after["schema_version"], br.SCHEMA_RUNTIME_V2)
        self.assertEqual(rt_after["version"], 1)
        self.assertAlmostEqual(rt_after["lived_needs"]["levels"]["thirst"], 0.50, places=4)
        self.assertAlmostEqual(rt_after["lived_needs"]["levels"]["hunger"], 0.40, places=4)
        transitions = br.BodyStore(paths.body_dir).read_transitions()
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]["cause_class"], br.CAUSE_LIVED_NEEDS)

        # Case B: Real `nyairo_day.py init --no-lived-needs` (no lived_needs_profile.json, no runtime.json,
        # no profile.json): still recognized as managed world_home authority; auto_initialize=False refuses
        # cleanly without creating either runtime.json or shadow body/lived_needs.json.
        init_root_off = self.tmp / "fresh-init-no-ln"
        proc_off = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "nyairo_day.py"),
                "init",
                "--root",
                str(init_root_off),
                "--no-lived-needs",
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc_off.returncode, 0, msg=f"init off failed: stdout={proc_off.stdout}\nstderr={proc_off.stderr}")
        paths_off = ip.resolve_instance_paths(init_root_off)
        self.assertTrue(paths_off.is_managed_instance)
        self.assertFalse((paths_off.body_dir / "lived_needs_profile.json").exists())
        self.assertFalse(paths_off.body_runtime_json.exists())

        refused = scv.apply_lived_needs_relief(
            init_root_off,
            thirst_relief=0.25,
            receipt_id="rcpt-fresh-init-off-001",
            now_iso=NOW_ISO,
            auto_initialize=False,
        )
        self.assertFalse(refused["applied"])
        self.assertEqual(refused["reason_code"], "LIVED_NEEDS_NOT_ENABLED")
        self.assertEqual(refused["authority"], "body_runtime")
        self.assertFalse((init_root_off / "body" / "lived_needs.json").exists())
        self.assertFalse(paths_off.body_runtime_json.exists())


class Stage2OutdoorRainExposureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="stage2-rain-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_01_outdoor_rain_wets_outerwear_and_is_idempotent(self) -> None:
        self.assertEqual(scv.VERIFIED_OUTDOOR_EXPOSURE_SOURCE, "verified_outdoor_rain_exposure")
        self.assertEqual(scv.AMBIENT_IS_TEMPLATE, "AMBIENT_IS_TEMPLATE_NOT_WETNESS_EVIDENCE")

        # 1. Drizzle for 5 minutes outdoors without umbrella -> damp daily_coat
        res1 = scv.record_outdoor_weather_exposure(
            self.tmp,
            place_id="nearby_street",
            weather="drizzle",
            duration_seconds=300.0,
            has_umbrella=False,
            operation_key="walk-drizzle-1",
            now_iso="2026-11-20T10:00:00+08:00",
        )
        self.assertTrue(res1["ok"])
        self.assertTrue(res1["exposed"])
        self.assertTrue(res1["changed"])
        self.assertFalse(res1["replayed"])
        self.assertEqual(res1["garment_id"], "daily_coat")
        self.assertEqual(res1["dryness"], "damp")
        self.assertEqual(res1["dampness_source"], scv.VERIFIED_OUTDOOR_EXPOSURE_SOURCE)
        self.assertEqual(res1["wetted_at"], "2026-11-20T10:00:00+08:00")
        self.assertEqual(res1["llm_calls"], 0)
        rev1 = res1["revision"]

        # Replay with same operation_key -> idempotent, revision unchanged!
        res_dup = scv.record_outdoor_weather_exposure(
            self.tmp,
            place_id="nearby_street",
            weather="drizzle",
            duration_seconds=300.0,
            has_umbrella=False,
            operation_key="walk-drizzle-1",
            now_iso="2026-11-20T10:05:00+08:00",
        )
        self.assertTrue(res_dup["replayed"])
        self.assertFalse(res_dup["changed"])
        self.assertEqual(res_dup["revision"], rev1)

        # 2. Heavy rain >= 600s outdoors without umbrella -> upgrades to "wet" keeping earliest wetted_at
        res2 = scv.record_outdoor_weather_exposure(
            self.tmp,
            place_id="riverside",
            weather="rain",
            duration_seconds=720.0,
            has_umbrella=False,
            operation_key="walk-rain-2",
            now_iso="2026-11-20T10:15:00+08:00",
        )
        self.assertTrue(res2["exposed"])
        self.assertTrue(res2["changed"])
        self.assertEqual(res2["dryness"], "wet")
        self.assertEqual(res2["wetted_at"], "2026-11-20T10:00:00+08:00")

        state, reason = scv.read_state(self.tmp)
        self.assertIsNone(reason)
        block = scv.somatic_block(
            world_result={
                "ok": True,
                "data": {
                    "location": {"status": "available", "place_id": "home", "area_id": "entryway", "home": True},
                    "world": {"local_time": "2026-11-20T10:20:00+08:00", "local_date": "2026-11-20", "revision": 1},
                    "local_environment": {"light": {"status": "available", "power": "on", "band": "moderate"}},
                    "travel": {"status": "idle"},
                },
            },
            body_view={"ok": True, "status": "available", "data": {"pose": "standing", "signals": []}},
            state=state,
            state_reason=None,
            now_iso="2026-11-20T10:20:00+08:00",
            soundscape={"available": True, "events": ["rain_on_window"]},
        )
        self.assertEqual(block["modalities"]["moisture"]["quality"], "wet")
        self.assertEqual(block["modalities"]["moisture"]["salience"], "high")
        self.assertEqual(block["modalities"]["moisture"]["dampness_source"], scv.VERIFIED_OUTDOOR_EXPOSURE_SOURCE)
        self.assertFalse(block["modalities"]["moisture"]["simulation"])
        self.assertTrue(block["ambient"]["not_evidence_of_wetness"])
        self.assertTrue(block["ambient"]["template_projection"])

        # 3. Hang wet coat -> dries to "dry" after 6h curve
        hung, changed, err = scv.hang_outerwear(
            state,
            garment_id="daily_coat",
            hang_position="entry_hook",
            dryness=None,
            now_iso="2026-11-20T10:20:00+08:00",
        )
        self.assertTrue(changed)
        self.assertIsNone(err)
        self.assertEqual(scv.derive_garment(hung, "2026-11-20T11:20:00+08:00")["dryness"], "wet")
        self.assertEqual(scv.derive_garment(hung, "2026-11-20T14:00:00+08:00")["dryness"], "damp")
        self.assertEqual(scv.derive_garment(hung, "2026-11-20T16:25:00+08:00")["dryness"], "dry")

    def test_02_indoor_umbrella_clear_brief_and_ambient_never_wet_and_zero_write(self) -> None:
        cases = [
            ("home/bedroom", "storm", 900.0, False, "INDOOR_OR_SHELTERED"),
            ("bookstore", "rain", 900.0, False, "INDOOR_OR_SHELTERED"),
            ("porch", "rain", 900.0, False, "INDOOR_OR_SHELTERED"),
            ("nearby_street", "clear", 900.0, False, "WEATHER_NOT_RAIN"),
            ("nearby_street", "rain", 900.0, True, "PROTECTED_BY_UMBRELLA"),
            ("nearby_street", "rain", 120.0, False, "EXPOSURE_TOO_BRIEF"),
        ]
        for idx, (place_id, weather, dur, umbrella, expected_reason) in enumerate(cases):
            case_root = self.tmp / f"noop-{idx}"
            case_root.mkdir()
            before = _tree_manifest(case_root)
            res = scv.record_outdoor_weather_exposure(
                case_root,
                place_id=place_id,
                weather=weather,
                duration_seconds=dur,
                has_umbrella=umbrella,
                operation_key=f"op-{idx}",
                now_iso=NOW_ISO,
            )
            self.assertFalse(res["exposed"], (place_id, weather, dur, umbrella, res))
            self.assertFalse(res["changed"])
            self.assertEqual(res["reason_code"], expected_reason)
            self.assertEqual(_tree_manifest(case_root), before)

        # Indoor ambient soundscape alone keeps moisture unknown (AMBIENT_IS_TEMPLATE_NOT_WETNESS_EVIDENCE).
        clean_state, _ = scv.read_state(self.tmp / "noop-0")
        block = scv.somatic_block(
            world_result={
                "ok": True,
                "data": {
                    "location": {"status": "available", "place_id": "home", "area_id": "bedroom", "home": True},
                    "world": {"local_time": NOW_ISO, "local_date": "2026-11-20", "revision": 1},
                    "local_environment": {"light": {"status": "available", "power": "on", "band": "moderate"}},
                    "travel": {"status": "idle"},
                },
            },
            body_view={"ok": True, "status": "available", "data": {"pose": "seated", "signals": []}},
            state=clean_state,
            state_reason=None,
            now_iso=NOW_ISO,
            soundscape={"available": True, "events": ["heavy_rain_ambient"]},
        )
        self.assertEqual(block["modalities"]["moisture"]["status"], "unknown")
        self.assertTrue(block["ambient"]["not_evidence_of_wetness"])


class Stage2ComposableSleepAndHandSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="stage2-sleep-hands-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_01_auto_bind_world_sleep_is_zero_write_when_disabled_and_binds_when_enabled(self) -> None:
        day_root, world_home = _make_managed_root(self.tmp)
        before = _tree_manifest(day_root)

        # Pack file absent -> pure 100% zero-write no-op (even when called with world_home!).
        noop = v08_cb.auto_bind_world_sleep_if_enabled(
            world_home,
            sleep_activity_id="world_sleep:exec-01",
            started_at_iso="2026-11-20T23:00:00+08:00",
            planned_wake_iso="2026-11-21T06:30:00+08:00",
        )
        self.assertTrue(noop["ok"])
        self.assertFalse(noop["bound"])
        self.assertTrue(noop["skipped"])
        self.assertEqual(noop["reason_code"], "PACK_DISABLED")
        self.assertEqual(noop["writes_performed"], 0)
        self.assertEqual(_tree_manifest(day_root), before)

        # Pack exists but disabled -> still pure zero-write no-op.
        v08_cb.configure_body_pack(
            day_root,
            {"pack_id": v08_cb.PACK_NOCTURNAL, "enabled": False, "operation_key": "cfg-off"},
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        before_off = _tree_manifest(day_root)
        noop_off = v08_cb.auto_bind_world_sleep_if_enabled(
            world_home,
            sleep_activity_id="world_sleep:exec-02",
            started_at_iso="2026-11-20T23:00:00+08:00",
            planned_wake_iso="2026-11-21T06:30:00+08:00",
        )
        self.assertFalse(noop_off["bound"])
        self.assertEqual(noop_off["writes_performed"], 0)
        self.assertEqual(_tree_manifest(day_root), before_off)

        # Enable nocturnal_emission -> auto_bind binds cleanly and idempotently!
        v08_cb.configure_body_pack(
            day_root,
            {
                "pack_id": v08_cb.PACK_NOCTURNAL,
                "enabled": True,
                "probability_permille": 200,
                "operation_key": "cfg-on",
            },
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        bound = v08_cb.auto_bind_world_sleep_if_enabled(
            world_home,
            sleep_activity_id="world_sleep:exec-03",
            started_at_iso="2026-11-20T23:00:00+08:00",
            planned_wake_iso="2026-11-21T06:30:00+08:00",
        )
        self.assertTrue(bound["ok"])
        self.assertTrue(bound["bound"])
        self.assertFalse(bound["skipped"])
        self.assertEqual(bound["sleep_activity_id"], "world_sleep:exec-03")

        # Repeat bind -> idempotent replay!
        replay = v08_cb.auto_bind_world_sleep_if_enabled(
            world_home,
            sleep_activity_id="world_sleep:exec-03",
            started_at_iso="2026-11-20T23:00:00+08:00",
            planned_wake_iso="2026-11-21T06:30:00+08:00",
        )
        self.assertTrue(replay["bound"])
        self.assertTrue(replay["already_applied"])
        self.assertEqual(replay["writes_performed"], 0)

        view = v08_cb.read_composable_body_zero_write(
            day_root, has_subject_capability=True, now_iso="2026-11-20T23:10:00+08:00"
        )
        self.assertEqual(len(view["sleep_activities"]), 1)
        self.assertEqual(view["sleep_activities"][0]["sleep_activity_id"], "world_sleep:exec-03")
        self.assertNotIn("hidden_scheduled_at_iso", view["sleep_activities"][0])

    @POSIX_ONLY
    def test_02_world_body_service_sleep_like_hook_helper_binds_when_enabled(self) -> None:
        import world_body_service as wbsvc

        day_root, world_home = _make_managed_root(self.tmp)
        v08_cb.configure_body_pack(
            day_root,
            {"pack_id": v08_cb.PACK_NOCTURNAL, "enabled": True, "operation_key": "cfg-svc-on"},
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        shim = SimpleNamespace(config=SimpleNamespace(world_home=world_home))
        res = wbsvc.WorldBodyService._maybe_auto_bind_composable_sleep(
            shim, execution_id="exec-sleep-99", started_at=NOW_DT
        )
        self.assertIsNotNone(res)
        self.assertTrue(res["bound"])
        self.assertEqual(res["sleep_activity_id"], "world_sleep:exec-sleep-99")

    def test_03_embodied_social_view_reads_world_substrate_hands_zero_write_and_enforces_interlock(self) -> None:
        day_root, world_home = _make_managed_root(self.tmp)
        sub_path = world_home / "data" / "world" / "body_substrate.json"
        sub_path.parent.mkdir(parents=True, exist_ok=True)
        sub_path.write_text(
            json.dumps(
                {
                    "schema_version": "world.body.substrate.v1.3",
                    "kind": "world_body_substrate",
                    "revision": 2,
                    "world_id": "shiomi_city",
                    "body_id": "chiyo_body",
                    "world_entity_id": "chiyo_body",
                    "pose": "standing",
                    "orientation": "south",
                    "placements": {
                        "item-umbrella": {"kind": "hand", "slot_id": "left_hand"},
                        "item-teacup": {"kind": "hand", "slot_id": "right_hand"},
                    },
                    "reservations": {},
                    "consumptions": {},
                    "fixtures": {},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        # GET must be 100% zero-write and expose World physical hands!
        before = _tree_manifest(day_root)
        code, body = esv.read_v03_embodied_social_view(
            day_root, has_subject_capability=True, now_iso=NOW_ISO
        )
        self.assertEqual(code, 200)
        self.assertEqual(_tree_manifest(day_root), before)
        ec = body["embodied_contact"]
        self.assertTrue(ec["world_substrate_hands"]["present"])
        self.assertEqual(ec["world_substrate_hands"]["left_hand"], "item-umbrella")
        self.assertEqual(ec["world_substrate_hands"]["right_hand"], "item-teacup")
        self.assertEqual(ec["free_hand_count"], 0)
        self.assertEqual(ec["non_object_hand_count"], 0)
        self.assertEqual(ec["hand_slots"]["left_hand"]["object_id"], "item-umbrella")
        self.assertEqual(ec["hand_slots"]["right_hand"]["object_id"], "item-teacup")

        # Bring partner within reach and attempt hold_one_hand -> refused (409) because World hands are full!
        code_p, _ = esv.mutate_v03_embodied_social_view(
            day_root,
            {"action": "set_partner_presence", "presence_mode": "beside_at_desk"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code_p, 200)
        code_c, res_c = esv.mutate_v03_embodied_social_view(
            day_root,
            {"action": "propose_contact", "contact_kind": "hold_one_hand"},
            actor_side="partner",
            now_iso=NOW_ISO,
        )
        self.assertEqual(code_c, 409)
        self.assertIn("HAND", str(res_c["reason_code"]).upper())


class Stage2AdultIntimacyLiveOverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="stage2-intimacy-overlay-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _unlock_intimacy_root(self, root: Path) -> None:
        aiv.apply_adult_intimacy_action(
            root,
            {
                "action": "configure_adult_gate",
                "adult_confirmed": True,
                "attested_by": "deployer",
                "life_stage": "working_adult",
                "feature_enabled": True,
                "deploy_tier_cap": "SUGGESTIVE",
            },
            now=NOW_ISO,
        )
        aiv.apply_adult_intimacy_action(
            root,
            {
                "action": "confirm_preference_seed",
                "attested_by": "deployer",
            },
            now=NOW_ISO,
        )
        aiv.apply_adult_intimacy_action(
            root,
            {
                "action": "update_partner_limits",
                "allow_subject_initiation": True,
                "preferred_tier": "SUGGESTIVE",
                "stances": {"KISSING": "YES"},
            },
            now=NOW_ISO,
        )

    def test_01_live_runtime_illness_affect_overlay_is_zero_write_and_gates_readiness(self) -> None:
        day_root, world_home = _make_managed_root(
            self.tmp, fatigue=0.78, warmth=0.28, thirst=0.82
        )
        self._unlock_intimacy_root(day_root)

        # Write S07 low-mood affect record.
        (day_root / "life").mkdir(parents=True, exist_ok=True)
        (day_root / "life" / "s07_affect_records.json").write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "record_id": "affect:live_low_1",
                            "kind": "mood_shift",
                            "intensity": 60,
                            "observed_at": NOW_DT.isoformat(),
                            "expires_at": (NOW_DT + timedelta(hours=4)).isoformat(),
                            "note": "有点低落",
                        }
                    ]
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        intimacy_file = aiv.adult_intimacy_state_path(day_root)
        before_bytes = intimacy_file.read_bytes()
        before_manifest = _tree_manifest(day_root)

        proj = aiv.read_adult_intimacy(day_root, now=NOW_ISO, subject_view=True)
        self.assertEqual(intimacy_file.read_bytes(), before_bytes)
        self.assertEqual(_tree_manifest(day_root), before_manifest)

        body = proj["body_and_desire"]
        self.assertAlmostEqual(body["fatigue"], 0.78, places=4)
        self.assertAlmostEqual(body["thirst"], 0.82, places=4)
        self.assertAlmostEqual(body["warmth"], 0.28, places=4)
        self.assertEqual(body["mood"], "low")
        self.assertEqual(body["subject_candidates"], ["COMPANIONSHIP"])
        self.assertTrue(body["live_body_overlay"]["applied"])
        self.assertIn("body_runtime", body["live_body_overlay"]["sources"])
        self.assertIn("s07_affect", body["live_body_overlay"]["sources"])

        # Stored adult_intimacy.json itself was NOT mutated by GET!
        stored = json.loads(intimacy_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["body"]["fatigue"], 0.0)
        self.assertEqual(stored["body"]["mood"], "calm")

        # Acute illness in v03_ecology.json blocks propose_scene readiness!
        (day_root / "phone" / "v03_ecology.json").write_text(
            json.dumps({"illness": {"phase": "acute_illness", "severity": 0.6}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        ill_proj = aiv.read_adult_intimacy(day_root, now=NOW_ISO, subject_view=True)
        self.assertTrue(ill_proj["body_and_desire"]["acute_illness"])
        self.assertFalse(ill_proj["body_and_desire"]["available_for_intimacy"])
        with self.assertRaises(aiv.AdultIntimacyConflictError) as ctx:
            aiv.apply_adult_intimacy_action(
                day_root,
                {
                    "action": "propose_scene",
                    "operation_key": "propose-ill-1",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                    "desired_tier": "SUGGESTIVE",
                },
                now=NOW_ISO,
            )
        self.assertEqual(ctx.exception.reason_code, "SUBJECT_UNAVAILABLE")

        # Clear illness, put runtime.json into active sleep_like -> sleeping incapacity blocks propose_scene!
        (day_root / "phone" / "v03_ecology.json").write_text(
            json.dumps({"illness": {"phase": "healthy", "severity": 0.0}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        rt_path = world_home / "data" / "body" / "runtime.json"
        rt_doc = json.loads(rt_path.read_text(encoding="utf-8"))
        rt_doc["recovery"] = {
            "state": "active",
            "started_at": NOW_DT.isoformat(),
            "last_advanced_at": NOW_DT.isoformat(),
            "mode": "sleep_like",
            "progress": 0.2,
            "trend": "improving",
        }
        rt_path.write_text(json.dumps(rt_doc, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

        sleep_proj = aiv.read_adult_intimacy(day_root, now=NOW_ISO, subject_view=True)
        self.assertFalse(sleep_proj["body_and_desire"]["awake"])
        self.assertEqual(sleep_proj["body_and_desire"]["impairment"], "sleeping")
        self.assertFalse(sleep_proj["body_and_desire"]["available_for_intimacy"])
        with self.assertRaises(aiv.AdultIntimacyConflictError) as ctx_sleep:
            aiv.apply_adult_intimacy_action(
                day_root,
                {
                    "action": "propose_scene",
                    "operation_key": "propose-sleep-1",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                    "desired_tier": "SUGGESTIVE",
                },
                now=NOW_ISO,
            )
        self.assertEqual(ctx_sleep.exception.reason_code, "SUBJECT_UNAVAILABLE")

    def test_02_absent_runtime_preserves_manual_fixtures_untouched(self) -> None:
        standalone = self.tmp / "manual-only"
        standalone.mkdir()
        overlay = aiv._read_live_body_and_affect_zero_write(standalone, NOW_DT)
        self.assertFalse(overlay["runtime_present"])
        proj = aiv.read_adult_intimacy(standalone, now=NOW_ISO, subject_view=True)
        self.assertNotIn("live_body_overlay", proj["body_and_desire"])
        self.assertEqual(proj["body_and_desire"]["fatigue"], 0.0)


class Stage2ThermalSignalsAndHygieneTest(unittest.TestCase):
    def test_01_thermal_signals_exposed_in_life_body_context_without_altering_capacity_burden(self) -> None:
        # NON_BURDEN_SIGNAL_TYPES in body_capacity_source stays intact!
        self.assertIn("warmth_high", bcs.NON_BURDEN_SIGNAL_TYPES)
        self.assertIn("cold_tendency", bcs.NON_BURDEN_SIGNAL_TYPES)
        self.assertNotIn("warmth_high", bcs.BURDEN_SIGNAL_TYPES)
        self.assertNotIn("cold_tendency", bcs.BURDEN_SIGNAL_TYPES)

        # Fake port returning ONLY a thermal signal -> capacity stays UNKNOWN, thermal_signals carries it!
        class _ThermalOnlyPort:
            def body(self):
                return {
                    "outcome": "OK",
                    "degraded": False,
                    "data": {
                        "available": True,
                        "recovery": {"state": "inactive", "mode": None, "trend": "stalled"},
                        "signals": [
                            {
                                "signal_type": "cold_tendency",
                                "qualitative_band": "mild",
                                "body_id": "chiyo_body",
                                "source_body_version": 4,
                                "occurred_at": NOW_DT.isoformat(),
                                "carries_raw_values": False,
                            }
                        ],
                    },
                }

        home = Path(tempfile.mkdtemp(prefix="stage2-thermal-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        cap_view = bcs.read_body_capacity(_ThermalOnlyPort(), home=home, now=NOW_DT.isoformat())
        self.assertEqual(cap_view["body_capacity_state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(cap_view["evidence"], [])
        self.assertEqual(cap_view["thermal_signals"], ["cold_tendency:mild"])

        # life_body_context exposes dedicated thermal_signals while keeping burden & easing clean!
        ctx = lbc.build(
            body_column={
                "body_capacity_state": "LIMITED",
                "evidence": ["fatigue_noticeable:mild", "warmth_high:strong", "recovery_underway:mild"],
                "thermal_signals": ["cold_tendency:mild"],
                "observed_at": NOW_DT.isoformat(),
                "observed_body_id": "chiyo_body",
                "observed_revision": "bodyrev:4",
                "freshness": "fresh",
                "identity_bound": True,
                "revision_bound": True,
                "observation_time_bound": True,
            },
            rest=None,
        )
        self.assertEqual(ctx["burden"], [{"signal": "fatigue_noticeable", "band": "mild"}])
        self.assertEqual(ctx["easing"], [{"signal": "recovery_underway", "band": "mild"}])
        self.assertEqual(
            ctx["thermal_signals"],
            [
                {"signal": "cold_tendency", "band": "mild"},
                {"signal": "warmth_high", "band": "strong"},
            ],
        )
        self.assertEqual(lbc.empty_context("BODY_SITUATION_NOT_READ")["thermal_signals"], [])

    def test_02_owned_files_hygiene_lf_no_bom_no_emoji_and_sealed_baseline_intact(self) -> None:
        owned_files = [
            "components/phone/somatic_comfort_view.py",
            "components/world/scripts/body_health_projection.py",
            "components/world/service/world_body_service.py",
            "components/world/scripts/v08_composable_body.py",
            "components/phone/v03_embodied_social_view.py",
            "components/phone/adult_intimacy_view.py",
            "components/life/scripts/body_capacity_source.py",
            "components/life/scripts/life_body_context.py",
            "tests/test_stage2_unified_body_authority.py",
        ]
        for rel in owned_files:
            raw = (ROOT / rel).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {rel}")
            self.assertNotIn(b"\r", raw, f"CRLF found in {rel}")
            text = raw.decode("utf-8")
            bad_so = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(bad_so, [], f"unicode.So symbols found in {rel}: {bad_so[:5]}")

        manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
        manifest_files = manifest.get("files") or {}
        for rel in (
            "components/life/scripts/autonomous_life_assembly.py",
            "components/life/scripts/life_sharing_service.py",
            "components/native/native_recall/readonly_recall_adapter.py",
            "NOW.md",
        ):
            expected = manifest_files[rel]
            actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"sealed file changed vs MANIFEST.json: {rel}")


if __name__ == "__main__":
    unittest.main()
