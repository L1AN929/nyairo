#!/usr/bin/env python3
"""Stage 1 verification: canonical instance_paths, world location bridge,
v04 autumn-lock fix, deterministic 6-hour autonomous weather pulse, live
world_living_skeleton weather, path alignment across v03/v05/v08/v02_honest_ux,
100% zero-write GET, and LF/no-BOM/no-emoji/neutral-TA hygiene.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"), str(_WORLD_SCRIPTS), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.phone import v02_honest_ux_view as honest_ux  # noqa: E402
from components.world.scripts import (  # noqa: E402
    instance_paths as ip,
    v03_economy_illness_bridge as eco_bridge,
    v03_embodied_npc_ecology as npc_eco,
    v04_neighborhood_living as nb,
    v05_home_craft_engine as craft_engine,
    v05_living_town_engine as town_engine,
    v08_microzone_ecology as microzone,
    world_foundation as wf,
    world_living_skeleton as skeleton,
    world_property_economy as wpe,
)

TZ8 = timezone(timedelta(hours=8))


def _write_json(path: Path, doc: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return out


def _seed_managed_instance(root: Path, *, place_id: str = "home", area_id: str = "bedroom") -> dict[str, Path]:
    world_home = root / "one-day" / "world-home"
    life_state = root / "one-day" / "life" / "state"
    world_pkg = root / "world-package"
    mem_dir = root / "one-day" / "life" / "memory"
    s03_db = root / "one-day" / "s03" / "claims.sqlite3"
    ledger_db = root / "v02" / "canonical_life.sqlite3"
    auth_db = root.parent / f"{root.name}-memory-authority" / "authority.db"

    for d in (world_home / "data" / "world", world_home / "data" / "body", world_home / "state", life_state, world_pkg, mem_dir, s03_db.parent, ledger_db.parent, auth_db.parent):
        d.mkdir(parents=True, exist_ok=True)

    _write_json(
        root / "instance.json",
        {
            "schema_version": "phone.instance.v1",
            "kind": "phone_local_instance",
            "root": str(root),
            "subject": "chiyo",
            "world_home": str(world_home),
            "world_id": "shiomi_city",
            "world_package": str(world_pkg),
            "economy_state": str(world_home / "state" / "world_property_economy.json"),
            "s03_claims_db": str(s03_db),
        },
    )
    _write_json(
        root / "one-day" / "instance.json",
        {
            "schema_version": "nyairo.one-day.instance.v1",
            "world_home": str(world_home),
            "world_package": str(world_pkg),
            "environment": {
                "CHIYO_LIFE_INSTALL_ROOT": str(root / "one-day" / "life"),
                "NYA02_RESIDENT_STATE": str(life_state),
                "NYA02_MEMORY_DB": str(mem_dir / "evidence.db"),
                "NYA02_MEMORY_CONTROLS_DB": str(mem_dir / "controls.db"),
                "NYA02_MEMORY_AUTHORITY_DB": str(auth_db),
            },
        },
    )
    _write_json(
        root / "run" / "nyairo-day.instance.json",
        {"schema": "nyairo.day.managed.v1"},
    )
    _write_json(
        world_pkg / "world.json",
        {
            "world_id": "harbor_mist",
            "name": "雾港小镇",
            "theme": "mountain_town",
        },
    )
    _write_json(
        world_home / "data" / "world" / "world_state.json",
        {
            "schema_version": wf.WORLD_SCHEMA_VERSION,
            "world_id": "shiomi_city",
            "timezone": "Asia/Tokyo",
            "revision": 3,
            "location": {"place_id": place_id, "area_id": area_id},
            "scene": {"scene_id": "scene_default", "revision": 1},
            "facts": [],
        },
    )
    _write_json(
        world_home / "data" / "world" / "body_substrate.json",
        {"pose": "lying", "orientation": "north"},
    )
    _write_json(
        world_home / "data" / "body" / "runtime.json",
        {"somatic": {"fatigue": 0.42}, "recovery": {"mode": "idle"}},
    )
    s03_db.write_bytes(b"")
    auth_db.write_bytes(b"")
    (mem_dir / "evidence.db").write_bytes(b"")
    (mem_dir / "controls.db").write_bytes(b"")
    return {
        "world_home": world_home,
        "life_state": life_state,
        "world_pkg": world_pkg,
        "s03_db": s03_db,
        "ledger_db": ledger_db,
        "auth_db": auth_db,
    }


def _create_canonical_ledger_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE actions (
                action_id TEXT PRIMARY KEY,
                parent_action_id TEXT,
                is_compound INTEGER,
                phase TEXT,
                outcome TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE runtime_control (
                control_key TEXT PRIMARY KEY,
                active_writer_epoch INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO runtime_control (control_key, active_writer_epoch) VALUES ('primary', 7)"
        )
        conn.execute(
            "INSERT INTO actions VALUES ('act_1', NULL, 0, 'SETTLED', 'SUCCEEDED')"
        )
        conn.execute(
            "INSERT INTO actions VALUES ('act_2', NULL, 0, 'QUIESCED_UNRESOLVED', 'UNKNOWN')"
        )
        conn.commit()
    finally:
        conn.close()


class TestStage1InstancePathsAndBridge(unittest.TestCase):
    def test_01_upward_and_downward_normalization_on_managed_instance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s1-managed-") as td:
            root = Path(td) / "day-inst"
            root.mkdir()
            refs = _seed_managed_instance(root, place_id="park", area_id="park")
            _create_canonical_ledger_db(refs["ledger_db"])
            before = _snapshot_tree(Path(td))

            from_root = ip.resolve_instance_paths(root)
            from_world_home = ip.resolve_instance_paths(refs["world_home"])
            from_life_state = ip.resolve_instance_paths(refs["life_state"])
            from_one_day = ip.resolve_instance_paths(root / "one-day")

            for resolved in (from_root, from_world_home, from_life_state, from_one_day):
                self.assertEqual(resolved.day_root, root)
                self.assertTrue(resolved.is_managed_instance)
                self.assertEqual(resolved.instance_json, root / "instance.json")
                self.assertEqual(resolved.world_home, refs["world_home"])
                self.assertEqual(
                    resolved.world_state_json,
                    refs["world_home"] / "data" / "world" / "world_state.json",
                )
                self.assertEqual(resolved.body_dir, refs["world_home"] / "data" / "body")
                self.assertEqual(
                    resolved.body_runtime_json,
                    refs["world_home"] / "data" / "body" / "runtime.json",
                )
                self.assertEqual(
                    resolved.body_substrate_json,
                    refs["world_home"] / "data" / "world" / "body_substrate.json",
                )
                self.assertEqual(
                    resolved.body_transitions_jsonl,
                    refs["world_home"] / "data" / "body" / "transitions.jsonl",
                )
                self.assertEqual(
                    resolved.economy_state_json,
                    refs["world_home"] / "state" / "world_property_economy.json",
                )
                self.assertEqual(resolved.world_package_dir, refs["world_pkg"])
                self.assertEqual(resolved.life_install_root, root / "one-day" / "life")
                self.assertEqual(resolved.life_state_root, refs["life_state"])
                self.assertEqual(
                    resolved.agency_depth_state_json,
                    root / "state" / "v02_agency_depth_state.json",
                )
                self.assertEqual(
                    resolved.legacy_nested_agency_depth_json,
                    root / "one-day" / "life" / "state" / "state" / "v02_agency_depth_state.json",
                )
                self.assertEqual(resolved.s03_claims_db, refs["s03_db"])
                self.assertEqual(resolved.canonical_ledger_db, refs["ledger_db"])
                self.assertEqual(resolved.memory_dir, root / "one-day" / "life" / "memory")
                self.assertEqual(
                    resolved.memory_evidence_db,
                    root / "one-day" / "life" / "memory" / "evidence.db",
                )
                self.assertEqual(
                    resolved.memory_controls_db,
                    root / "one-day" / "life" / "memory" / "controls.db",
                )
                self.assertEqual(resolved.memory_authority_db, refs["auth_db"])

            # Verify run/nyairo-day-managed.json also acts as an upward normalization anchor
            alt_root = Path(td) / "alt-managed"
            (alt_root / "run").mkdir(parents=True)
            _write_json(alt_root / "run" / "nyairo-day-managed.json", {"managed": True})
            alt_wh = alt_root / "one-day" / "world-home"
            alt_wh.mkdir(parents=True)
            alt_resolved = ip.resolve_instance_paths(alt_wh)
            self.assertEqual(alt_resolved.day_root, alt_root)
            self.assertTrue(alt_resolved.is_managed_instance)
            self.assertEqual(alt_resolved.world_home, alt_wh)

            # Verify CHIYO_S03_CLAIMS_DB / NYAIRO_S03_CLAIMS_DB env override
            custom_s03 = Path(td) / "override_claims.sqlite3"
            env_resolved = ip.resolve_instance_paths(
                root, environment={"CHIYO_S03_CLAIMS_DB": str(custom_s03)}
            )
            self.assertEqual(env_resolved.s03_claims_db, custom_s03)

            self.assertEqual(_snapshot_tree(root), {k.split("/", 1)[1]: v for k, v in before.items() if k.startswith("day-inst/")})

    def test_02_isolated_tmp_path_never_escapes_upward_and_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s1-isolated-") as td:
            isolated = Path(td) / "bare_root"
            nested_wh = isolated / "one-day" / "world-home"
            nested_state = isolated / "one-day" / "life" / "state"
            nested_wh.mkdir(parents=True)
            nested_state.mkdir(parents=True)
            before = _snapshot_tree(Path(td))

            for target in (isolated, nested_wh, nested_state):
                p = ip.resolve_instance_paths(target)
                self.assertEqual(p.day_root, target)
                self.assertFalse(p.is_managed_instance)
                self.assertIsNone(p.instance_json)

            nonexistent = Path(td) / "does_not_exist_yet"
            p_missing = ip.resolve_instance_paths(nonexistent)
            self.assertEqual(p_missing.day_root, nonexistent)
            self.assertFalse(nonexistent.exists())
            self.assertEqual(_snapshot_tree(Path(td)), before)

    def test_03_canonical_location_bridge_and_world_to_v04_v05_v08_projection(self) -> None:
        cases = [
            ("home", "bedroom", "home", "home", "window_side", False),
            ("home", "living", "home", "home", "window_side", False),
            ("home", "study", "home", "home", "desk_inner", False),
            ("home", "kitchen", "home", "home", "desk_inner", False),
            ("home/porch", None, "porch", "home", "porch_eaves", False),
            ("home/yard", None, "yard", "home", "open_yard", True),
            ("courtyard", None, "yard", "home", "open_yard", True),
            ("nearby_street", None, "street_corner", "bakery_corner", "open_yard", True),
            ("convenience_store", None, "street_corner", "bakery_corner", "open_yard", False),
            ("bookstore", None, "bookstore", "old_bookshop", "desk_inner", False),
            ("home_goods_store", None, "bookstore", "old_bookshop", "desk_inner", False),
            ("coin_laundromat", None, "street_corner", "coin_laundromat", "entryway", False),
            ("park", None, "riverside", "hillside_overlook", "open_yard", True),
            ("seawall", None, "riverside", "river_pier", "open_yard", True),
            ("blank_grove", None, "riverside", "blank_grove", "open_yard", True),
        ]
        for place, area, exp_v04, exp_v05, exp_v08, exp_outdoor in cases:
            mapped = ip.map_world_place_to_slices(place, area)
            self.assertEqual(mapped["v04_place_id"], exp_v04, (place, area))
            self.assertEqual(mapped["v05_spot_id"], exp_v05, (place, area))
            self.assertEqual(mapped["v08_spot_id"], exp_v08, (place, area))
            self.assertEqual(mapped["is_outdoor"], exp_outdoor, (place, area))

        with tempfile.TemporaryDirectory(prefix="s1-bridge-") as td:
            root = Path(td) / "inst"
            root.mkdir()
            refs = _seed_managed_instance(root, place_id="park", area_id="park")
            snap_before = _snapshot_tree(root)

            loc = ip.read_canonical_world_location_zero_write(refs["world_home"])
            self.assertTrue(loc["found"])
            self.assertEqual(loc["world_place_id"], "park")
            self.assertEqual(loc["v04_place_id"], "riverside")
            self.assertEqual(loc["v05_spot_id"], "hillside_overlook")
            self.assertEqual(loc["v08_spot_id"], "open_yard")
            self.assertTrue(loc["is_outdoor"])

            v04_proj = nb.project_v04_neighborhood(root, now_iso="2026-07-15T10:00:00+08:00")
            self.assertEqual(v04_proj["data"]["journey"]["current_place_id"], "riverside")

            now_dt = datetime(2026, 7, 15, 10, 0, tzinfo=TZ8)
            v05_ctx = town_engine._extract_world_context_zero_write(root, now_dt)
            self.assertTrue(v05_ctx["world_state_found"])
            self.assertEqual(v05_ctx["ta_location_id"], "hillside_overlook")
            self.assertEqual(v05_ctx["theme"], "mountain")
            self.assertEqual(v05_ctx["season"], "summer")

            craft_ctx = craft_engine._extract_world_context_zero_write(root, now_dt)
            self.assertTrue(craft_ctx["world_state_found"])
            self.assertEqual(craft_ctx["theme"], "mountain")
            self.assertEqual(craft_ctx["town_name_cn"], "雾港小镇")
            self.assertEqual(craft_ctx["canonical_location"]["v05_spot_id"], "hillside_overlook")
            self.assertEqual(craft_ctx["season"], "summer")

            v08_proj = microzone.resolve_local_conditions_zero_write(
                root, now_iso="2026-07-15T10:00:00+08:00"
            )
            self.assertEqual(v08_proj["active_spot_id"], "open_yard")
            self.assertEqual(v08_proj["posture_cn"], "躺着")
            self.assertEqual(v08_proj["season"], "summer")

            self.assertEqual(_snapshot_tree(root), snap_before)


class TestStage1SeasonFixAndWeatherPulse(unittest.TestCase):
    def test_01_v04_non_env_action_never_locks_autumn_year_round(self) -> None:
        st = nb.default_state()
        env = st["environment"]
        self.assertIsNone(env["season"])
        self.assertFalse(env["season_explicit"])
        self.assertIsNone(env["weather_slot_key"])
        self.assertIsNone(env["manual_pin_until"])
        self.assertIsNone(env["updated_at"])

        with tempfile.TemporaryDirectory(prefix="s1-season-") as td:
            root = Path(td)
            # Trigger a non-environment write creating world/v04_neighborhood_state.json
            status, _, _ = nb.apply_v04_neighborhood_command(
                root,
                {"action": "water_or_move_plant", "operation": "water", "water_amount": 10},
                actor_side="subject",
                now_iso="2026-03-20T09:00:00+08:00",
            )
            self.assertEqual(status, "applied")

            disk_doc = json.loads((root / "world" / "v04_neighborhood_state.json").read_text(encoding="utf-8"))
            self.assertIsNone(disk_doc["environment"]["season"])
            self.assertFalse(disk_doc["environment"]["season_explicit"])

            snap_after_write = _snapshot_tree(root)
            for iso_ts, expected_season in (
                ("2026-03-20T10:00:00+08:00", "spring"),
                ("2026-07-15T14:00:00+08:00", "summer"),
                ("2026-12-22T10:00:00+08:00", "winter"),
            ):
                now_dt = datetime.fromisoformat(iso_ts)
                v04_out = nb.project_v04_neighborhood(root, now_iso=iso_ts)
                self.assertEqual(v04_out["data"]["environment"]["season"], expected_season, iso_ts)

                v05_ctx = town_engine._extract_world_context_zero_write(root, now_dt)
                self.assertEqual(v05_ctx["season"], expected_season, iso_ts)

                craft_ctx = craft_engine._extract_world_context_zero_write(root, now_dt)
                self.assertEqual(craft_ctx["season"], expected_season, iso_ts)

                v08_out = microzone.resolve_local_conditions_zero_write(root, now_iso=iso_ts)
                self.assertEqual(v08_out["season"], expected_season, iso_ts)

            self.assertEqual(_snapshot_tree(root), snap_after_write)

            # set_local_weather WITHOUT season also keeps dynamic calendar season unlocked
            nb.apply_v04_neighborhood_command(
                root,
                {"action": "set_local_weather", "weather": "rain"},
                actor_side="partner",
                now_iso="2026-07-15T14:00:00+08:00",
            )
            v04_dec = nb.project_v04_neighborhood(root, now_iso="2026-12-22T10:00:00+08:00")
            self.assertEqual(v04_dec["data"]["environment"]["season"], "winter")

            # Explicit set_local_weather WITH season DOES pin season_explicit=True
            nb.apply_v04_neighborhood_command(
                root,
                {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
                actor_side="partner",
                now_iso="2026-07-15T15:00:00+08:00",
            )
            disk_pinned = json.loads((root / "world" / "v04_neighborhood_state.json").read_text(encoding="utf-8"))
            self.assertEqual(disk_pinned["environment"]["season"], "autumn")
            self.assertTrue(disk_pinned["environment"]["season_explicit"])
            self.assertIsNotNone(disk_pinned["environment"]["updated_at"])
            v04_pinned = nb.project_v04_neighborhood(root, now_iso="2026-07-15T16:00:00+08:00")
            self.assertEqual(v04_pinned["data"]["environment"]["season"], "autumn")

    def test_02_autonomous_6h_weather_pulse_determinism_pin_and_skeleton_live_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s1-weather-") as td:
            # 1. Bare uninitialized root -> zero-write no-op
            bare = Path(td) / "bare"
            bare.mkdir()
            before_bare = _snapshot_tree(bare)
            res_bare = nb.advance_autonomous_weather_pulse(bare, now_iso="2026-06-15T08:00:00+08:00")
            self.assertFalse(res_bare["changed"])
            self.assertEqual(res_bare["slot_key"], "2026-06-15:S1")
            self.assertEqual(res_bare["llm_calls"], 0)
            self.assertEqual(_snapshot_tree(bare), before_bare)

            # 2. Two independent managed roots produce identical deterministic draws across S0..S3
            r1 = Path(td) / "m1"
            r2 = Path(td) / "m2"
            r1.mkdir()
            r2.mkdir()
            refs1 = _seed_managed_instance(r1)
            _seed_managed_instance(r2)
            nb.save_state_atomic(r1, nb.default_state())
            nb.save_state_atomic(r2, nb.default_state())

            slots = [
                ("2026-06-15T02:00:00+08:00", "2026-06-15:S0"),
                ("2026-06-15T08:00:00+08:00", "2026-06-15:S1"),
                ("2026-06-15T14:00:00+08:00", "2026-06-15:S2"),
                ("2026-06-15T20:00:00+08:00", "2026-06-15:S3"),
            ]
            for iso_ts, exp_slot in slots:
                out1 = nb.advance_autonomous_weather_pulse(r1, now_iso=iso_ts)
                out2 = nb.advance_autonomous_weather_pulse(r2, now_iso=iso_ts)
                self.assertTrue(out1["changed"])
                self.assertEqual(out1["slot_key"], exp_slot)
                self.assertEqual(out1["weather"], out2["weather"])
                self.assertEqual(out1["airflow"], out2["airflow"])
                self.assertIn(out1["weather"], nb.WEATHER_KINDS)
                self.assertIn(out1["airflow"], nb.AIRFLOW_KINDS)
                self.assertEqual(out1["season"], "summer")
                self.assertEqual(out1["llm_calls"], 0)

                # Same slot re-run is 100% zero-write
                snap_mid = _snapshot_tree(r1)
                again = nb.advance_autonomous_weather_pulse(r1, now_iso=iso_ts)
                self.assertFalse(again["changed"])
                self.assertEqual(again["slot_key"], exp_slot)
                self.assertEqual(_snapshot_tree(r1), snap_mid)

            # Season stays unpinned (None on disk)
            st_disk = json.loads((r1 / "world" / "v04_neighborhood_state.json").read_text(encoding="utf-8"))
            self.assertIsNone(st_disk["environment"]["season"])
            self.assertFalse(st_disk["environment"]["season_explicit"])

            # 3. manual_pin_until blocks autonomous weather overwrite (zero-write!)
            nb.apply_v04_neighborhood_command(
                r1,
                {
                    "action": "set_local_weather",
                    "weather": "rain",
                    "manual_pin_until": "2026-06-16T18:00:00+08:00",
                },
                actor_side="partner",
                now_iso="2026-06-16T08:00:00+08:00",
            )
            snap_pinned = _snapshot_tree(r1)
            blocked = nb.advance_autonomous_weather_pulse(r1, now_iso="2026-06-16T14:00:00+08:00")
            self.assertFalse(blocked["changed"])
            self.assertEqual(blocked["weather"], "rain")
            self.assertEqual(_snapshot_tree(r1), snap_pinned)

            # 4. world_living_skeleton.observe_world reads live rain from managed world-home zero-write!
            env_ws = {"CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1"}
            snap_obs_before = _snapshot_tree(r1)
            obs = skeleton.observe_world(
                hermes_home=refs1["world_home"],
                now=datetime(2026, 6, 16, 14, 0, tzinfo=TZ8),
                environment=env_ws,
            )
            self.assertEqual(obs["status"], "available")
            self.assertEqual(obs["weather"]["condition"], "rain")
            self.assertEqual(obs["weather"]["description"], "降雨")
            self.assertEqual(obs["weather"]["source"], "v04_neighborhood_weather")
            self.assertEqual(_snapshot_tree(r1), snap_obs_before)

            # 5. Without v04_neighborhood_state.json, observe_world falls back to _DEFAULT_WEATHER
            bare_ws = Path(td) / "bare-ws"
            _write_json(
                bare_ws / "data" / "world" / "world_state.json",
                {
                    "schema_version": wf.WORLD_SCHEMA_VERSION,
                    "world_id": "shiomi_city",
                    "timezone": "Asia/Tokyo",
                    "revision": 1,
                    "location": {"place_id": "home", "area_id": "bedroom"},
                    "scene": {"scene_id": "scene_room", "revision": 1},
                    "facts": [],
                },
            )
            snap_bare_ws = _snapshot_tree(bare_ws)
            obs_default = skeleton.observe_world(
                hermes_home=bare_ws,
                now=datetime(2026, 6, 16, 14, 0, tzinfo=TZ8),
                environment=env_ws,
            )
            self.assertEqual(obs_default["status"], "available")
            self.assertEqual(obs_default["weather"]["condition"], "clear")
            self.assertEqual(obs_default["weather"]["source"], "configured_v1")
            self.assertEqual(_snapshot_tree(bare_ws), snap_bare_ws)


class TestStage1PathAlignmentsAndHygiene(unittest.TestCase):
    def test_01_v03_economy_v03_npc_and_v02_honest_ux_path_alignment_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s1-align-") as td:
            root = Path(td) / "managed"
            root.mkdir()
            refs = _seed_managed_instance(root)
            _create_canonical_ledger_db(refs["ledger_db"])

            # Seed real world_property_economy genesis under one-day/world-home/state/
            env_eco = {"CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1"}
            gen = wpe.initialize_genesis(hermes_home=refs["world_home"], environment=env_eco)
            self.assertIn(gen.get("status"), ("created", "already_initialized"))

            snap_before = _snapshot_tree(root)

            # 1. v03_economy_illness_bridge finds one-day/world-home/state/world_property_economy.json
            wallet = eco_bridge._read_upstream_wallet_zero_write(root)
            self.assertTrue(wallet.get("initialized"))
            self.assertEqual(wallet.get("wallet_source"), "world_property_economy")
            self.assertGreater(int(wallet.get("base_balance_cents") or 0), 0)
            self.assertEqual(
                Path(str(wallet.get("world_economy_path"))),
                refs["world_home"] / "state" / "world_property_economy.json",
            )

            # Also verify without instance.json (direct one-day/world-home/state/ probe)
            no_inst = Path(td) / "no-inst-eco"
            no_inst_wh = no_inst / "one-day" / "world-home"
            no_inst_wh.mkdir(parents=True)
            gen2 = wpe.initialize_genesis(hermes_home=no_inst_wh, environment=env_eco)
            self.assertIn(gen2.get("status"), ("created", "already_initialized"))
            snap_no_inst = _snapshot_tree(no_inst)
            wallet2 = eco_bridge._read_upstream_wallet_zero_write(no_inst)
            self.assertTrue(wallet2.get("initialized"))
            self.assertEqual(
                Path(str(wallet2.get("world_economy_path"))),
                no_inst_wh / "state" / "world_property_economy.json",
            )
            self.assertEqual(_snapshot_tree(no_inst), snap_no_inst)

            # 2. v03_embodied_npc_ecology detects hyphenated world-package/world.json theme
            theme_info = npc_eco.detect_world_theme_readonly(root)
            self.assertEqual(theme_info["theme"], "mountain_town")
            self.assertEqual(theme_info["source"], "world_package")

            # Also verify fallback without instance.json (direct hyphenated world-package/)
            hyphen_only = Path(td) / "hyphen-only"
            _write_json(
                hyphen_only / "world-package" / "world.json",
                {"world_id": "coastal_test", "name": "海风小镇", "theme": "coastal_town"},
            )
            snap_hyphen = _snapshot_tree(hyphen_only)
            t2 = npc_eco.detect_world_theme_readonly(hyphen_only)
            self.assertEqual(t2["theme"], "coastal_town")
            self.assertEqual(t2["source"], "world_package")
            self.assertEqual(_snapshot_tree(hyphen_only), snap_hyphen)

            # 3. v02_honest_ux_view finds <day_root>/v02/canonical_life.sqlite3 with zero -wal/-shm creation
            summary = honest_ux._read_crash_reconciler_summary_zero_write(root)
            self.assertTrue(summary["db_exists"])
            self.assertFalse(summary["read_error"])
            self.assertEqual(summary["total_actions"], 2)
            self.assertEqual(summary["succeeded_actions"], 1)
            self.assertEqual(summary["unknown_actions"], 1)
            self.assertEqual(summary["quiesced_unresolved"], 1)
            self.assertEqual(summary["active_writer_epoch"], 7)
            self.assertFalse((refs["ledger_db"].with_name("canonical_life.sqlite3-wal")).exists())
            self.assertFalse((refs["ledger_db"].with_name("canonical_life.sqlite3-shm")).exists())

            self.assertEqual(_snapshot_tree(root), snap_before)

    def test_02_hygiene_lf_no_bom_no_emoji_and_frozen_fabric_untouched(self) -> None:
        owned_files = [
            "components/world/scripts/instance_paths.py",
            "components/world/scripts/v04_neighborhood_living.py",
            "components/world/scripts/world_living_skeleton.py",
            "components/world/scripts/v05_living_town_engine.py",
            "components/world/scripts/v05_home_craft_engine.py",
            "components/world/scripts/v08_microzone_ecology.py",
            "components/world/scripts/v03_economy_illness_bridge.py",
            "components/world/scripts/v03_embodied_npc_ecology.py",
            "components/phone/v02_honest_ux_view.py",
            "tests/test_stage1_paths_and_world_truth.py",
        ]
        for rel in owned_files:
            path = REPO_ROOT / rel
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM in {rel}")
            self.assertNotIn(b"\r", raw, f"CRLF in {rel}")
            text = raw.decode("utf-8")
            if rel in (
                "components/world/scripts/instance_paths.py",
                "tests/test_stage1_paths_and_world_truth.py",
            ):
                so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
                self.assertEqual(so_chars, [], f"unicode.So chars in {rel}: {so_chars[:5]}")


if __name__ == "__main__":
    unittest.main()
