#!/usr/bin/env python3
"""Stage 2 Worker 3 Integration & Unit Tests:
Desire Arbitration, Acute Body-Care Loop Wiring, Outdoor Rain / sleep_like Hooks,
and Acute Somatic Context Surfacing.

Zero LLM calls, 100% deterministic, zero-write GET verified.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
PHONE_DIR = REPO_ROOT / "components" / "phone"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS), str(PHONE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import autonomous_life_cycle as cycle_mod  # noqa: E402
from components.life.scripts import everyday_desire_lifecycle as edl  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.life.scripts import v05_dreams_desires_foreshadow as spark_mod  # noqa: E402
from components.phone import somatic_comfort_view as scv  # noqa: E402
from components.world.scripts import body_runtime as br  # noqa: E402
from components.world.scripts import v08_composable_body as comp_body  # noqa: E402

NOW_ISO = "2026-11-20T17:30:00+08:00"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _make_runtime_doc(
    *,
    fatigue: float = 0.15,
    warmth: float = 0.50,
    comfort: float = 0.70,
    hunger: float = 0.0,
    thirst: float = 0.0,
    toilet: float = 0.0,
    recovery_state: str = "inactive",
    recovery_mode: str | None = None,
) -> dict[str, Any]:
    doc = br.initial_runtime(at=NOW_ISO)
    doc["somatic"]["fatigue"] = fatigue
    doc["somatic"]["warmth"] = warmth
    doc["somatic"]["comfort"] = comfort
    doc["recovery"]["state"] = recovery_state
    doc["recovery"]["mode"] = recovery_mode
    if recovery_state == "active":
        doc["recovery"]["started_at"] = NOW_ISO
        doc["recovery"]["last_advanced_at"] = NOW_ISO
    doc["schema_version"] = br.SCHEMA_RUNTIME_V2
    doc["lived_needs"] = {
        "schema_version": "body.lived.needs.v1",
        "levels": {"hunger": hunger, "thirst": thirst, "toilet": toilet},
        "rates": {"hunger": 0.08, "thirst": 0.12, "toilet": 0.07},
        "profile_ref": "test_profile_ref",
        "live_seconds": 0.0,
        "last_live_at": NOW_ISO,
        "offline_skipped_seconds": 0.0,
    }
    return doc


def _seed_managed_root(root: Path) -> Path:
    _write_json(root / "instance.json", {"schema": "nyairo.instance.v1", "id": "stage2-test"})
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "world").mkdir(parents=True, exist_ok=True)
    (root / "life").mkdir(parents=True, exist_ok=True)
    (root / "one-day" / "world-home" / "data" / "body").mkdir(parents=True, exist_ok=True)
    (root / "one-day" / "life" / "state").mkdir(parents=True, exist_ok=True)
    return root


class TestDesireArbitrationAndAcuteBodyCare(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s2-arb-")
        self.root = _seed_managed_root(Path(self._tmp.name) / "day")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_wallet_tightness_blocks_paid_desire_and_emits_zero_cost_home_alt(self) -> None:
        rec = spark_mod.record_subject_desire(
            self.root,
            layer="mid_itch",
            text="想去街角咖啡馆点一份季节限定甜点配手冲",
            category="appetite_craving",
            estimated_cost_jpy=980,
            requires_outing=False,
            intensity=0.80,
            now_iso=NOW_ISO,
        )
        desire_id = rec["desire"]["desire_id"]
        # Seed tight wallet in world economy
        _write_json(
            self.root / "one-day" / "world-home" / "state" / "world_property_economy.json",
            {"available_balance_cents": 2000, "living_floor_cents": 1500},
        )

        before = _snapshot_tree(self.root)
        emitted, notes = loop_bridge.arbitrate_desire_candidates_for_loop(
            self.root,
            desires=None,
            economy_info={"discretionary_margin_cents": 500, "budget_tight": False},
            illness_info={"is_actively_ill": False, "phase": "healthy"},
            body_snapshot={"fatigue": 0.20},
            weather="clear",
            now_iso=NOW_ISO,
        )
        self.assertEqual(_snapshot_tree(self.root), before, "arbitration must be 100% zero-write")
        self.assertEqual(len(emitted), 1)
        cand = emitted[0]
        self.assertEqual(cand["action_family"], "pursue_desire_alternative")
        self.assertEqual(cand["alternative_kind"], "zero_cost_home")
        self.assertEqual(cand["estimated_cost_jpy"], 0)
        self.assertFalse(cand["requires_outing"])
        self.assertFalse(cand["begs_partner_when_tight"])
        self.assertEqual(cand["friction_reason"], "budget_tight")
        self.assertEqual(cand["frustration_debt"], 0)

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["friction_reason"], "budget_tight")
        self.assertEqual(notes[0]["disposition"], "downscaled_zero_cost_home")
        self.assertEqual(notes[0]["frustration_debt"], 0)

        # Increment friction_count on settlement without frustration debt
        fr = loop_bridge.record_desire_arbitration_friction(
            self.root,
            desire_id=desire_id,
            friction_reason="budget_tight",
            now_iso=NOW_ISO,
        )
        self.assertTrue(fr["ok"])
        self.assertEqual(fr.get("frustration_debt", 0), 0)
        proj = spark_mod.project_v05_inner_spark_state(
            self.root, has_subject_capability=True, now_iso=NOW_ISO
        )
        matched = [d for d in proj["desires"]["items"] if d["desire_id"] == desire_id]
        self.assertEqual(len(matched), 1)
        self.assertGreaterEqual(int(matched[0].get("friction_count") or 0), 1)

        # When wallet has ample margin, paid desire is admitted cleanly
        admitted, notes_ok = loop_bridge.arbitrate_desire_candidates_for_loop(
            self.root,
            desires=None,
            economy_info={"discretionary_margin_cents": 250_000, "budget_tight": False},
            illness_info={"is_actively_ill": False, "phase": "healthy"},
            body_snapshot={"fatigue": 0.20},
            weather="clear",
            now_iso=NOW_ISO,
        )
        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0]["action_family"], "pursue_autonomous_desire")
        self.assertIsNone(admitted[0]["friction_reason"])
        self.assertEqual(notes_ok[0]["disposition"], "admitted")

    def test_02_rain_and_storm_substitute_indoor_sheltered_alternative(self) -> None:
        desire = {
            "desire_id": "des_outing_river",
            "layer": "mid_itch",
            "category": "restlessness_outing",
            "text": "想去河埠石阶走走吹风",
            "estimated_cost_jpy": 0,
            "requires_outing": True,
            "intensity": 0.78,
            "status": "active",
        }
        for w in ("rain", "storm", "heavy_rain", "thunder_blackout", "sea_gale"):
            emitted, notes = loop_bridge.arbitrate_desire_candidates_for_loop(
                self.root,
                desires=[desire],
                economy_info={"discretionary_margin_cents": 100_000},
                illness_info={"is_actively_ill": False, "phase": "healthy"},
                body_snapshot={"fatigue": 0.20},
                weather=w,
                now_iso=NOW_ISO,
            )
            self.assertEqual(len(emitted), 1, w)
            self.assertEqual(emitted[0]["action_family"], "pursue_desire_alternative", w)
            self.assertEqual(emitted[0]["alternative_kind"], "indoor_sheltered", w)
            self.assertFalse(emitted[0]["requires_outing"], w)
            self.assertEqual(emitted[0]["friction_reason"], "weather_rain", w)
            self.assertEqual(notes[0]["friction_reason"], "weather_rain", w)
            self.assertEqual(notes[0]["frustration_debt"], 0, w)

    def test_03_dedupes_across_v05_inner_spark_and_everyday_desires(self) -> None:
        # Record in v05_inner_spark.json AND sync + add a distinct concern in everyday_desires.json
        spark_mod.record_subject_desire(
            self.root,
            layer="micro_craving",
            text="想泡一壶热焙茶慢慢看窗外",
            category="cozy_comfort",
            estimated_cost_jpy=0,
            requires_outing=False,
            intensity=0.72,
            now_iso=NOW_ISO,
        )
        edl.apply_everyday_desire_action(
            self.root,
            {
                "action": "emerge_concern",
                "domain": "comfort",
                "origin_kind": "immediate_opportunity",
                "origin_refs": ["窗边茶壶"],
                "expression": "想泡一壶热焙茶慢慢看窗外",
                "persistence": "retained",
            },
            now_iso=NOW_ISO,
        )
        edl.apply_everyday_desire_action(
            self.root,
            {
                "action": "emerge_concern",
                "domain": "exploration",
                "origin_kind": "immediate_opportunity",
                "origin_refs": ["书架边角"],
                "expression": "想翻翻旧书架最底层那本画册",
                "persistence": "retained",
            },
            now_iso=NOW_ISO,
        )

        before = _snapshot_tree(self.root)
        emitted, notes = loop_bridge.arbitrate_desire_candidates_for_loop(
            self.root,
            desires=None,
            economy_info={"discretionary_margin_cents": 100_000},
            illness_info={"is_actively_ill": False, "phase": "healthy"},
            body_snapshot={"fatigue": 0.25},
            weather="clear",
            now_iso=NOW_ISO,
        )
        self.assertEqual(_snapshot_tree(self.root), before)
        # 2 unique desires (duplicate tea desire collapsed cleanly)
        self.assertEqual(len(emitted), 2)
        self.assertEqual(len(notes), 2)
        titles = [c["title_hint"] for c in emitted]
        self.assertEqual(sum("热焙茶" in t for t in titles), 1)
        self.assertEqual(sum("画册" in t for t in titles), 1)

    def test_04_high_fatigue_illness_defers_strenuous_desire_and_emits_acute_body_care(self) -> None:
        spark_mod.record_subject_desire(
            self.root,
            layer="mid_itch",
            text="想把整幅街景线稿一口气画完",
            category="craft_ambition",
            estimated_cost_jpy=0,
            requires_outing=False,
            intensity=0.82,
            now_iso=NOW_ISO,
        )
        # Seed acute runtime.json: fatigue 0.85, thirst 0.78, hunger 0.75, chill 0.28
        _write_json(
            self.root / "one-day" / "world-home" / "data" / "body" / "runtime.json",
            _make_runtime_doc(fatigue=0.85, warmth=0.28, hunger=0.75, thirst=0.78),
        )
        # Seed worn damp outerwear in somatic_comfort.json
        som_state = scv.empty_state()
        som_state, _, _ = scv.wear_outerwear(
            som_state,
            garment_id="rain_coat_01",
            label="浅灰防风外套",
            dryness="damp",
            now_iso=NOW_ISO,
        )
        scv.write_state(self.root, som_state)

        before = _snapshot_tree(self.root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=NOW_ISO)
        self.assertEqual(_snapshot_tree(self.root), before, "collect must be 100% zero-write")
        self.assertEqual(collected["llm_calls"], 0)

        descriptors = collected["descriptors"]
        families = {d["action_family"] for d in descriptors.values()}
        body_actions = set(collected["gating"].get("acute_body_care_emitted") or [])
        self.assertIn("hang_outerwear", families)
        self.assertIn("drink_warm_water", families)
        self.assertIn("acute_body_rest_sleep_like", families)
        self.assertIn("eat_warm_meal", families)
        self.assertIn("hang_outerwear", body_actions)
        self.assertIn("drink_warm_water", body_actions)
        self.assertIn("REST(sleep_like)", body_actions)

        # Craft desire was deferred for rest with friction_reason="fatigue_or_illness"
        arb_notes = collected["gating"].get("desire_arbitration_notes") or []
        self.assertTrue(
            any(n.get("friction_reason") == "fatigue_or_illness" and n.get("disposition") == "deferred_for_rest" for n in arb_notes),
            arb_notes,
        )
        self.assertNotIn("pursue_autonomous_desire", families)

        # Settle hang_outerwear through the real consequence bridge
        hang_desc = next(d for d in descriptors.values() if d["action_family"] == "hang_outerwear")
        ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, hang_desc["source_ref"], now_iso=NOW_ISO, decision_id="dec_hang_1"
        )
        self.assertIsNotNone(ev)
        settled = loop_bridge.settle_v02_v03_round_consequences(self.root, ev, now_iso=NOW_ISO)
        self.assertTrue(settled["settled"], settled)
        reloaded_som, err = scv.read_state(self.root)
        self.assertIsNone(err)
        self.assertEqual(reloaded_som["garment"]["state"], "hang")

        # Calm defaults emit zero acute body-care candidates
        calm_root = _seed_managed_root(Path(self._tmp.name) / "calm_day")
        _write_json(
            calm_root / "one-day" / "world-home" / "data" / "body" / "runtime.json",
            _make_runtime_doc(fatigue=0.15, warmth=0.50, hunger=0.0, thirst=0.0, toilet=0.0),
        )
        calm_col = loop_bridge.collect_v02_v03_loop_candidates(calm_root, now_iso=NOW_ISO)
        self.assertEqual(calm_col["gating"]["acute_body_care_emitted"], [])
        self.assertEqual(calm_col["resident_context_summary"]["acute_body_care_count"], 0)

    def test_05_settlement_metadata_populated_for_eat_drink_and_desire_friction(self) -> None:
        # Seed body runtime.json with elevated hunger & thirst (and profile.json for BodyStore)
        rt_path = self.root / "one-day" / "world-home" / "data" / "body" / "runtime.json"
        prof_path = self.root / "one-day" / "world-home" / "data" / "body" / "profile.json"
        _write_json(prof_path, br.build_profile())
        _write_json(rt_path, _make_runtime_doc(fatigue=0.25, warmth=0.50, hunger=0.80, thirst=0.78))

        # Seed a paid desire + tight wallet so arbitration emits a budget_tight alternative
        rec = spark_mod.record_subject_desire(
            self.root,
            layer="mid_itch",
            text="想去精品手冲店点一杯单品豆配手作蛋糕",
            category="appetite_craving",
            estimated_cost_jpy=1200,
            requires_outing=False,
            intensity=0.80,
            now_iso=NOW_ISO,
        )
        desire_id = rec["desire"]["desire_id"]
        _write_json(
            self.root / "one-day" / "world-home" / "state" / "world_property_economy.json",
            {"available_balance_cents": 2000, "living_floor_cents": 1500},
        )

        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=NOW_ISO)
        descriptors = collected["descriptors"]

        # 1. Settle eat_warm_meal -> hunger drops & slices_updated + details["lived_needs_relief"] populated
        eat_desc = next(d for d in descriptors.values() if d["action_family"] == "eat_warm_meal")
        eat_ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, eat_desc["source_ref"], now_iso=NOW_ISO, decision_id="dec_eat_meta_1"
        )
        self.assertIsNotNone(eat_ev)
        eat_res = loop_bridge.settle_v02_v03_round_consequences(self.root, eat_ev, now_iso=NOW_ISO)
        self.assertTrue(eat_res["settled"], eat_res)
        self.assertIn("body.somatic", eat_res["slices_updated"], eat_res)
        self.assertIn("lived_needs_relief", eat_res["details"], eat_res)
        self.assertTrue(eat_res["details"]["lived_needs_relief"].get("applied"))
        rt_after_eat = json.loads(rt_path.read_text(encoding="utf-8"))
        self.assertLess(rt_after_eat["lived_needs"]["levels"]["hunger"], 0.80)

        # 2. Settle drink_warm_water -> thirst drops & slices_updated + details["lived_needs_relief"] populated
        drink_desc = next(d for d in descriptors.values() if d["action_family"] == "drink_warm_water")
        drink_ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, drink_desc["source_ref"], now_iso=NOW_ISO, decision_id="dec_drink_meta_1"
        )
        self.assertIsNotNone(drink_ev)
        drink_res = loop_bridge.settle_v02_v03_round_consequences(self.root, drink_ev, now_iso=NOW_ISO)
        self.assertTrue(drink_res["settled"], drink_res)
        self.assertIn("body.somatic", drink_res["slices_updated"], drink_res)
        self.assertIn("lived_needs_relief", drink_res["details"], drink_res)
        self.assertTrue(drink_res["details"]["lived_needs_relief"].get("applied"))
        rt_after_drink = json.loads(rt_path.read_text(encoding="utf-8"))
        self.assertLess(rt_after_drink["lived_needs"]["levels"]["thirst"], 0.78)

        # 3. Settle budget_tight desire alternative -> friction_count increments & slices_updated populated
        alt_desc = next(
            d for d in descriptors.values()
            if d["action_family"] == "pursue_desire_alternative"
            and d.get("friction_reason") == "budget_tight"
        )
        alt_ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, alt_desc["source_ref"], now_iso=NOW_ISO, decision_id="dec_alt_meta_1"
        )
        self.assertIsNotNone(alt_ev)
        alt_res = loop_bridge.settle_v02_v03_round_consequences(self.root, alt_ev, now_iso=NOW_ISO)
        self.assertTrue(alt_res["settled"], alt_res)
        self.assertIn("life.v05_inner_spark", alt_res["slices_updated"], alt_res)
        self.assertIn("desire_friction", alt_res["details"], alt_res)
        self.assertTrue(alt_res["details"]["desire_friction"].get("updated"))
        self.assertEqual(alt_res["details"]["desire_friction"].get("frustration_debt", 0), 0)
        proj = spark_mod.project_v05_inner_spark_state(
            self.root, has_subject_capability=True, now_iso=NOW_ISO
        )
        matched = [d for d in proj["desires"]["items"] if d["desire_id"] == desire_id]
        self.assertEqual(len(matched), 1)
        self.assertGreaterEqual(int(matched[0].get("friction_count") or 0), 1)


class TestAutonomousLifeCycleStage2Hooks(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s2-cycle-hooks-")
        self.root = _seed_managed_root(Path(self._tmp.name) / "day")
        self.world_home = self.root / "one-day" / "world-home"
        self.state_root = self.root / "one-day" / "life" / "state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_stub_cycle(self) -> cycle_mod.AutonomousLifeCycle:
        obj = object.__new__(cycle_mod.AutonomousLifeCycle)
        obj.world_home = self.world_home
        obj.state_root = self.state_root
        obj.env = {}
        obj.last_world_observation = {"location_id": "street_1", "world_revision": 3}
        obj.last_world_target_ref = None
        obj.last_world_error = None
        obj.last_journey_settlement = None
        obj._v02_v03_day_root = self.root
        obj._now_iso = lambda: NOW_ISO  # type: ignore[assignment]
        return obj

    def test_01_outdoor_rain_move_wets_outerwear_and_umbrella_or_indoor_stays_dry(self) -> None:
        _write_json(
            self.root / "world" / "v04_neighborhood_state.json",
            {"environment": {"weather": "rain", "airflow": "medium", "season": None}},
        )
        cycle = self._make_stub_cycle()

        # 1. Indoor move in rain -> zero exposure write
        before_indoor = _snapshot_tree(self.root)
        res_indoor = cycle._maybe_record_outdoor_weather_exposure(
            place_id="place:bookstore",
            duration_seconds=900.0,
            observed_at=NOW_ISO,
            operation_key="op_indoor_1",
        )
        self.assertIsNone(res_indoor)
        self.assertEqual(_snapshot_tree(self.root), before_indoor)

        # 2. Outdoor move WITH umbrella -> no wetting
        cycle.last_world_observation = {
            "location_id": "street_1",
            "hands": {"right_hand": {"item_id": "fold_umbrella", "label": "折叠雨伞"}},
        }
        res_umb = cycle._maybe_record_outdoor_weather_exposure(
            place_id="place:street_1",
            duration_seconds=900.0,
            observed_at=NOW_ISO,
            operation_key="op_umb_1",
        )
        self.assertIsNotNone(res_umb)
        self.assertFalse(res_umb.get("exposed", False))
        self.assertFalse(scv.state_path(self.root).exists())

        # 3. Outdoor move WITHOUT umbrella in rain -> wets worn coat with VERIFIED_OUTDOOR_EXPOSURE_SOURCE
        cycle.last_world_observation = {"location_id": "street_1", "hands": {}}
        res_wet = cycle._maybe_record_outdoor_weather_exposure(
            place_id="place:street_1",
            duration_seconds=720.0,
            observed_at=NOW_ISO,
            operation_key="op_wet_1",
        )
        self.assertIsNotNone(res_wet)
        self.assertTrue(res_wet.get("exposed"))
        som_state, err = scv.read_state(self.root)
        self.assertIsNone(err)
        self.assertEqual(som_state["garment"]["state"], "wear")
        self.assertEqual(som_state["garment"]["dryness"], "wet")
        self.assertEqual(
            som_state["garment"]["dampness_source"],
            scv.VERIFIED_OUTDOOR_EXPOSURE_SOURCE,
        )

    def test_02_sleep_like_settlement_hook_zero_write_when_disabled_and_binds_when_enabled(self) -> None:
        cycle = self._make_stub_cycle()
        base: dict[str, Any] = {"activity_ref": "act:sleep_test_01", "observed_at": NOW_ISO}
        action = {
            "action_id": "world_act_sleep_01",
            "frozen_payload": {"verb": "REST", "target": "bodyact:rest:start:sleep_like"},
        }

        # 1. Pack absent/disabled -> 100% zero-write no-op!
        before_disabled = _snapshot_tree(self.root)
        res_off = cycle._maybe_auto_bind_world_sleep(base, action=action, observed_at=NOW_ISO)
        self.assertEqual(_snapshot_tree(self.root), before_disabled)
        self.assertIsNotNone(res_off)
        self.assertFalse(res_off.get("bound"))

        # 2. Enable nocturnal_emission pack -> sleep_like binds sleep_activity_id cleanly!
        comp_body.configure_body_pack(
            self.root,
            {
                "pack_id": "nocturnal_emission",
                "enabled": True,
                "probability_permille": 250,
                "operation_key": "op_enable_nocturnal_s2",
            },
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        base_on: dict[str, Any] = {"activity_ref": "act:sleep_test_02", "observed_at": NOW_ISO}
        res_on = cycle._maybe_auto_bind_world_sleep(base_on, action=action, observed_at=NOW_ISO)
        self.assertIsNotNone(res_on)
        self.assertTrue(res_on.get("bound"), res_on)
        self.assertEqual(res_on.get("sleep_activity_id"), "act:sleep_test_02")
        self.assertEqual(base_on.get("composable_body_sleep_bind"), res_on)

    def test_03_classify_body_action_sleep_like_and_stay_settlement_e2e(self) -> None:
        from unittest import mock

        # Seed rain weather & enable nocturnal_emission pack
        _write_json(
            self.root / "world" / "v04_neighborhood_state.json",
            {"environment": {"weather": "drizzle", "airflow": "medium", "season": None}},
        )
        comp_body.configure_body_pack(
            self.root,
            {
                "pack_id": "nocturnal_emission",
                "enabled": True,
                "probability_permille": 250,
                "operation_key": "op_enable_nocturnal_e2e",
            },
            actor_side="subject",
            now_iso=NOW_ISO,
        )
        cycle = self._make_stub_cycle()
        cycle._fresh_world_body_observation = lambda: {  # type: ignore[assignment]
            "ok": True,
            "reason_code": "WORLD_OBSERVATION_RE_READ",
            "snapshot": {"location_id": "home", "world_revision": 4},
            "rest_availability": {
                "verb": "REST",
                "current": {"recovery_state": "active", "recovery_mode": "sleep_like"},
            },
            "world_revision": 4,
            "observed_place": "home",
        }
        cycle._world_toilet_availability = lambda: None  # type: ignore[assignment]
        cycle._record_world_body_progress = lambda *a, **kw: None  # type: ignore[assignment]
        cycle._finish = lambda base, outcome, **kw: {**base, "outcome": outcome}  # type: ignore[assignment]

        base: dict[str, Any] = {"activity_ref": "act:sleep_e2e_01", "observed_at": NOW_ISO}
        out = cycle._classify_body_action(
            base,
            verb="REST",
            action={
                "action_id": "act_rest_sleep_1",
                "frozen_payload": {"verb": "REST", "target": "bodyact:rest:start:sleep_like"},
            },
        )
        self.assertEqual(out["outcome"], cycle_mod.OUTCOME_ACTED)
        self.assertTrue(out.get("composable_body_sleep_bind", {}).get("bound"))
        self.assertEqual(
            out["composable_body_sleep_bind"].get("sleep_activity_id"), "act:sleep_e2e_01"
        )

        # Outdoor stay settlement in drizzle without umbrella -> damp outerwear!
        cycle.last_world_observation = {"location_id": "yard", "world_revision": 5, "hands": {}}
        cycle._observe_position = lambda: ("yard", 5)  # type: ignore[assignment]
        cycle._stay_began_at = lambda _ref: ("2026-11-20T17:20:00+08:00", "ckpt:stay:1")  # type: ignore[assignment]
        cycle._place_availability = lambda _p: "OPEN"  # type: ignore[assignment]
        cycle._record_world_stay_progress = lambda *a, **kw: None  # type: ignore[assignment]
        step_spec = SimpleNamespace(
            activity_ref="act:stay_yard_01",
            target_ref="stay:yard:BREATHE:300",
            payload_spec={
                "stay_target_ref": "stay:yard:BREATHE:300",
                "stay_phase": cycle_mod.STAY_PHASE_END,
            },
        )
        stay_out = cycle._classify_stay_step(step_spec, {"observed_at": NOW_ISO}, NOW_ISO)
        self.assertEqual(stay_out["outcome"], cycle_mod.OUTCOME_ACTED)
        self.assertTrue(stay_out.get("outdoor_weather_exposure", {}).get("exposed"))
        som_after, err = scv.read_state(self.root)
        self.assertIsNone(err)
        self.assertEqual(som_after["garment"]["state"], "wear")
        self.assertEqual(som_after["garment"]["dryness"], "damp")
        self.assertEqual(
            som_after["garment"]["dampness_source"],
            scv.VERIFIED_OUTDOOR_EXPOSURE_SOURCE,
        )

        # Also test settle_world_journey arriving at outdoor street_1 in rain
        _write_json(
            self.root / "world" / "v04_neighborhood_state.json",
            {"environment": {"weather": "rain", "airflow": "medium", "season": None}},
        )
        cycle.pause_gate = lambda: {"blocked": False}  # type: ignore[assignment]
        cycle.pause_state_root = self.state_root
        cycle.runtime = SimpleNamespace(world_action_adapter=None)
        cycle._observe_position = lambda: ("street_1", 6)  # type: ignore[assignment]
        cycle._foreground_activity_ref = lambda: None  # type: ignore[assignment]
        fake_journey = SimpleNamespace(
            advance_journey=lambda **kw: {
                "journey_id": "jny_rain_1",
                "status": "completed",
                "location_id": "street_1",
                "target_location_id": "street_1",
                "segments_confirmed": 2,
                "elapsed_seconds": 660,
            }
        )
        with mock.patch.object(cycle_mod, "journey_module", return_value=fake_journey):
            jny_res = cycle.settle_world_journey(observed_at="2026-11-20T17:45:00+08:00")
        self.assertTrue(jny_res["arrived"])
        self.assertTrue(jny_res.get("outdoor_weather_exposure", {}).get("exposed"))
        som_wet, _ = scv.read_state(self.root)
        self.assertEqual(som_wet["garment"]["dryness"], "wet")


class TestLivedContextAcuteSomaticReality(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s2-ctx-somatic-")
        self.root = _seed_managed_root(Path(self._tmp.name) / "day")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cfg(self, root: Path) -> dict[str, Any]:
        return {
            "day_root": str(root),
            "phone": {"enabled": False, "private_artifacts": True},
        }

    def test_01_calm_gate_silent_on_empty_and_calm_default_runtime(self) -> None:
        empty_root = Path(self._tmp.name) / "never_exists"
        bd_empty = lcb.measure_context_budget_breakdown(empty_root, cfg={})
        self.assertEqual(bd_empty["rendered_text"], "")
        self.assertEqual(bd_empty["total_chars"], 0)
        self.assertTrue(bd_empty["calm_silent"])
        self.assertFalse(empty_root.exists())

        # Seed calm default runtime.json (0.15 fatigue, 0.50 warmth, 0.0 hunger/thirst/toilet)
        _write_json(
            self.root / "one-day" / "world-home" / "data" / "body" / "runtime.json",
            _make_runtime_doc(),
        )
        before = _snapshot_tree(self.root)
        rt_info = lcb._read_body_runtime_zero_write(self.root)
        self.assertIsNotNone(rt_info)
        self.assertFalse(rt_info["has_notable_acute"])
        self.assertEqual(rt_info["notable_cues"], [])

        bd_calm = lcb.measure_context_budget_breakdown(
            self.root, cfg=self._cfg(self.root), now_iso=NOW_ISO
        )
        self.assertEqual(_snapshot_tree(self.root), before, "GET context read must be 100% zero-write")
        self.assertEqual(bd_calm["per_section_chars"].get("sensory_line", 0), 0)
        self.assertEqual(bd_calm["per_section_chars"].get("compact_situation_line", 0), 0)
        self.assertLessEqual(bd_calm["total_chars"], lcb.MAX_LIVED_CONTEXT_CHARS)
        for noise in ("疲惫", "饿", "口渴", "发冷", "深睡", "洗手间"):
            self.assertNotIn(noise, bd_calm["rendered_text"])

    def test_02_acute_runtime_surfaces_in_sensory_and_compact_within_1800_and_zero_write(self) -> None:
        _write_json(
            self.root / "one-day" / "world-home" / "data" / "body" / "runtime.json",
            _make_runtime_doc(
                fatigue=0.84,
                warmth=0.20,
                hunger=0.76,
                thirst=0.72,
                toilet=0.80,
                recovery_state="active",
                recovery_mode="sleep_like",
            ),
        )
        before = _snapshot_tree(self.root)
        rt_info = lcb._read_body_runtime_zero_write(self.root)
        self.assertIsNotNone(rt_info)
        self.assertTrue(rt_info["has_notable_acute"])
        self.assertTrue(rt_info["sleep_like_active"])

        bd = lcb.measure_context_budget_breakdown(
            self.root,
            cfg=self._cfg(self.root),
            incoming_text="你现在感觉怎么样？",
            now_iso=NOW_ISO,
        )
        self.assertEqual(_snapshot_tree(self.root), before, "GET context read must be 100% zero-write")
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertLessEqual(bd["total_chars"], 1800)
        self.assertTrue(bd["within_budget"])
        self.assertGreater(bd["per_section_chars"].get("sensory_line", 0), 0)
        self.assertGreater(bd["per_section_chars"].get("compact_situation_line", 0), 0)
        rendered = bd["rendered_text"]
        self.assertIn("当前处境：", rendered)
        self.assertIn("五感与躯体体感：", rendered)
        self.assertIn("深睡休养", rendered)
        self.assertIn("困倦疲惫", rendered)
        self.assertIn("饿", rendered)

    def test_03_pronoun_and_file_hygiene_on_all_owned_files(self) -> None:
        for fn in (
            lcb._body_num_or_none,
            lcb._read_body_runtime_zero_write,
            lcb._format_sensory_somatic,
            lcb._format_compact_current_situation,
        ):
            src = inspect.getsource(fn)
            self.assertNotIn("她", src, fn.__name__)
            self.assertNotIn("他", src, fn.__name__)
            self.assertNotIn("千代", src, fn.__name__)

        owned = (
            REPO_ROOT / "components" / "life" / "scripts" / "v02_v03_daily_loop_bridge.py",
            REPO_ROOT / "components" / "life" / "scripts" / "autonomous_life_cycle.py",
            REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py",
            Path(__file__).resolve(),
        )
        for path in owned:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM in {path.name}")
            self.assertNotIn(b"\r\n", raw, f"CRLF in {path.name}")
            text = raw.decode("utf-8")
            bad_so = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(bad_so, [], f"unicode.So symbols in {path.name}: {bad_so[:5]}")


if __name__ == "__main__":
    unittest.main()
