#!/usr/bin/env python3
"""rc1 room environment: declared light -> derived light -> one Body input.

The room-light feature is deliberately tiny, and so is its test surface: every
case below is about a *contract* that would otherwise be a lie.

* an undeclared room, an absent power record and another area's lamp are all
  ``unknown`` -- never ``off``, never intensity ``0``;
* the band table (``environment.light.v1``) is the only numeric hand-off, and
  ``temperature_tendency`` is never produced;
* a declared lamp that is ``on`` feeds ``ENVIRONMENT.sensory_intensity`` through
  the existing ``body_runtime`` rules, so a ``dim`` lamp legitimately stays below
  ``sensory_load_elevated`` (no thresholds are touched to make it visible);
* the owner tick advances the Body **at most once**, never together with a
  recovery advance, and a gap longer than the bound is reported as
  ``environment_gap_unknown`` with no write and no invented "it was on all
  night";
* the flag is deny-by-default.

Fixtures live under ``/tmp/nyaw05r.jdK3q/rc1-room-*`` (created once, retained).

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_environment.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
sys.path[:0] = [str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE)]

import body_consequence as bcon  # noqa: E402
import body_runtime as br  # noqa: E402
import body_signal as bsignal  # noqa: E402
import world_body_config as wbc  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as substrate  # noqa: E402
import world_property_economy as economy  # noqa: E402
import world_room_environment as room  # noqa: E402

FIXTURE_ROOT = pathlib.Path("/tmp/nyaw05r.jdK3q")
FIXTURE_PREFIX = "rc1-room-env-"
RUNTIME_ROOT = ROOT / "components" / "world"

LAMP = "bedside_lamp"
BASE = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)

ENVIRONMENT = {
    economy.FEATURE_ENV: "1",
    economy.WORLD_FEATURE_ENV: "1",
    economy.DAILY_FEATURE_ENV: "1",
    economy.DELIVERY_FEATURE_ENV: "0",
    substrate.FEATURE_ENV: "1",
}


def unique_home() -> pathlib.Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    home = FIXTURE_ROOT / (FIXTURE_PREFIX + uuid.uuid4().hex[:12])
    home.mkdir(parents=True, exist_ok=False)
    for relative in (("data", "world"), ("data", "body"), ("state",), ("run",)):
        home.joinpath(*relative).mkdir(parents=True, exist_ok=True)
    return home


def seed_world(home: pathlib.Path, *, place: str = "home", area: str = "bedroom") -> None:
    (home / "data" / "world" / "world_state.json").write_text(
        json.dumps(
            {
                "facts": [],
                "location": {"area_id": area, "place_id": place},
                "revision": 5,
                "scene": {"revision": 5, "scene_id": "%s.%s" % (place, area)},
                "schema_version": "world.foundation.v0",
                "timezone": "Asia/Tokyo",
                "world_id": "shiomi_city",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (home / "data" / "world" / "world_timeline.json").write_text(
        json.dumps(
            {
                "schema_version": "world.timeline.v1",
                "revision": 1,
                "world_id": "shiomi_city",
                "processes": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (home / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")
    (home / "state" / "world_object_capabilities.json").write_text(
        json.dumps(
            {"schema_version": substrate.CAPABILITIES_SCHEMA, "objects": {}, "consumables": {}},
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def seed_substrate(home: pathlib.Path, placements: dict | None = None) -> dict:
    state = substrate.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
    for object_id, target in (placements or {}).items():
        state = substrate.place_object(state, object_id, target)
    substrate.SubstrateStore(substrate.substrate_path(home)).save(state)
    return state


def file_fingerprint(home: pathlib.Path) -> dict:
    out = {}
    for path in sorted(home.rglob("*")):
        if path.is_file() and not path.name.endswith(".lock"):
            out[str(path)] = (path.stat().st_size, path.stat().st_mtime_ns)
    return out


def runtime_with_clock(at: datetime) -> dict:
    return br.initial_runtime(body_id="chiyo_body", at=at.isoformat())


# ---------------------------------------------------------------------------
# pure derivation
# ---------------------------------------------------------------------------


def _minutes_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


class DerivationTests(unittest.TestCase):
    def declaration(self) -> dict:
        return room.declaration_of_catalog(LAMP)

    def test_the_lamp_is_a_declared_dim_fixture(self):
        declaration = self.declaration()
        self.assertEqual(declaration["light_band"], "dim")
        self.assertTrue(declaration["powerable"])
        self.assertEqual(declaration["declared_source"], "catalog:bedside_lamp")
        self.assertIsNone(room.declaration_of_catalog("simple_desk"))
        self.assertIsNone(room.declaration_of_catalog("unknown_lamp"))

    def test_no_declared_light_is_unknown_with_a_reason(self):
        light = room.derive_light(
            placements={"item-lamp": {"kind": "room", "location_id": "home", "area_id": "living"}},
            fixtures={},
            catalog_by_instance={"item-lamp": LAMP},
            location_id="home",
            area_id="bedroom",
            now=BASE,
        )
        self.assertEqual(light["status"], "unknown")
        self.assertEqual(light["reason_code"], room.REASON_NO_DECLARED_LIGHT)
        self.assertIsNone(light["band"])
        self.assertIsNone(light["power"])
        self.assertIsNone(room.environment_input(light))

    def test_absent_power_is_unknown_and_never_off(self):
        light = room.derive_light(
            placements={"item-lamp": {"kind": "room", "location_id": "home", "area_id": "bedroom"}},
            fixtures={},
            catalog_by_instance={"item-lamp": LAMP},
            location_id="home",
            area_id="bedroom",
            now=BASE,
        )
        self.assertEqual(light["status"], "unknown")
        self.assertEqual(light["reason_code"], room.REASON_POWER_UNKNOWN)
        self.assertEqual(light["fixture_ids"], ["item-lamp"])
        self.assertIsNone(room.environment_input(light))

    def test_a_lamp_in_another_area_is_not_this_rooms_light(self):
        light = room.derive_light(
            placements={"item-lamp": {"kind": "room", "location_id": "home", "area_id": "living"}},
            fixtures={"item-lamp": {"power": "on", "since": BASE.isoformat(), "declared_source": "x", "revision": 1}},
            catalog_by_instance={"item-lamp": LAMP},
            location_id="home",
            area_id="bedroom",
            now=BASE,
        )
        self.assertEqual(light["status"], "unknown")
        self.assertIsNone(room.environment_input(light))

    def test_on_is_available_with_the_declared_band_and_a_real_since(self):
        since = (BASE - timedelta(minutes=12)).isoformat()
        light = room.derive_light(
            placements={"item-lamp": {"kind": "room", "location_id": "home", "area_id": "bedroom"}},
            fixtures={
                "item-lamp": {
                    "power": "on",
                    "since": since,
                    "declared_source": "catalog:bedside_lamp",
                    "revision": 2,
                }
            },
            catalog_by_instance={"item-lamp": LAMP},
            location_id="home",
            area_id="bedroom",
            now=BASE,
        )
        self.assertEqual(light["status"], "available")
        self.assertEqual(light["power"], "on")
        self.assertEqual(light["band"], "dim")
        self.assertEqual(light["on_seconds"], 12 * 60)
        self.assertEqual(light["declared_source"], "catalog:bedside_lamp")
        self.assertEqual(light["band_source"], room.ENVIRONMENT_MODEL)
        self.assertFalse(light["temperature_or_noise_inferred"])
        self.assertEqual(
            room.environment_input(light),
            {"class": "ENVIRONMENT", "sensory_intensity": 0.20},
        )

    def test_off_is_a_known_fact_but_produces_no_input(self):
        light = room.derive_light(
            placements={"item-lamp": {"kind": "room", "location_id": "home", "area_id": "bedroom"}},
            fixtures={
                "item-lamp": {
                    "power": "off",
                    "since": BASE.isoformat(),
                    "declared_source": "catalog:bedside_lamp",
                    "revision": 3,
                }
            },
            catalog_by_instance={"item-lamp": LAMP},
            location_id="home",
            area_id="bedroom",
            now=BASE,
        )
        self.assertEqual(light["status"], "available")
        self.assertEqual(light["power"], "off")
        self.assertIsNone(light["band"])
        self.assertIsNone(room.environment_input(light))

    def test_the_band_table_is_the_only_numeric_hand_off(self):
        self.assertEqual(
            room.LIGHT_BAND_INTENSITY, {"dim": 0.20, "moderate": 0.45, "bright": 0.70}
        )
        for band, intensity in room.LIGHT_BAND_INTENSITY.items():
            produced = room.environment_input(
                {"status": "available", "power": "on", "band": band}
            )
            self.assertEqual(produced, {"class": "ENVIRONMENT", "sensory_intensity": intensity})
            self.assertNotIn("temperature_tendency", produced)


# ---------------------------------------------------------------------------
# the Body input
# ---------------------------------------------------------------------------


class EnvironmentAdvanceTests(unittest.TestCase):
    def light(self, band: str = "dim") -> dict:
        return {
            "status": "available",
            "power": "on",
            "band": band,
            "source": room.KIND,
            "place_id": "home",
            "area_id": "bedroom",
        }

    def test_no_clock_means_no_advance(self):
        state = runtime_with_clock(BASE)
        state["last_advanced_at"] = None
        outcome = bcon.advance_environment(runtime_state=state, light=self.light(), at=BASE)
        self.assertFalse(outcome["advanced"])
        self.assertEqual(outcome["reason"], "no_body_clock")

    def test_an_interval_below_the_bound_waits_for_the_next_tick(self):
        state = runtime_with_clock(BASE)
        outcome = bcon.advance_environment(
            runtime_state=state, light=self.light(), at=BASE + timedelta(seconds=30)
        )
        self.assertFalse(outcome["advanced"])
        self.assertEqual(outcome["reason"], "interval_too_short")
        self.assertFalse(outcome["wrote"])

    def test_a_gap_after_a_stop_applies_no_light_but_unlocks_the_next_tick(self):
        state = runtime_with_clock(BASE)
        before = dict(state["somatic"])
        # the lamp was already on *before* the last body advance: the window in
        # between is genuinely unknown
        light = dict(self.light(), since=(BASE - timedelta(hours=1)).isoformat())
        outcome = bcon.advance_environment(
            runtime_state=state, light=light, at=BASE + timedelta(hours=3)
        )
        self.assertEqual(outcome["reason"], "environment_gap_unknown")
        self.assertTrue(outcome["gap_unknown"])
        # no light is claimed for the unknown window...
        self.assertIsNone(outcome["input"])
        self.assertTrue(outcome["wrote"])
        self.assertIn(
            br.CAUSE_TIME_ADVANCE, outcome["transition"]["cause_classes"]
        )
        self.assertTrue(
            outcome["transition"]["input_summary"]["environment_gap_unknown"]
        )
        # ...and the sensory variable did not move toward the lamp's band
        self.assertLess(outcome["state"]["somatic"]["sensory_load"], 0.20)
        self.assertGreaterEqual(
            outcome["state"]["somatic"]["sensory_load"],
            before["sensory_load"],
        )
        # the clock moved, so the next call is a normal observation window
        self.assertIsNotNone(outcome["state"]["last_advanced_at"])

    def test_turning_the_lamp_on_now_is_not_blocked_by_an_old_body_clock(self):
        # An old root: the body last advanced three hours ago, and the lamp was
        # switched on 30s ago.  The window we may speak about starts at `since`.
        state = runtime_with_clock(BASE)
        light = dict(self.light(), since=(BASE + timedelta(hours=3) - timedelta(seconds=30)).isoformat())
        short = bcon.advance_environment(
            runtime_state=state, light=light, at=BASE + timedelta(hours=3)
        )
        self.assertFalse(short["advanced"])
        self.assertEqual(short["reason"], "interval_too_short")
        later = bcon.advance_environment(
            runtime_state=state,
            light=light,
            at=BASE + timedelta(hours=3) + timedelta(seconds=120),
        )
        self.assertTrue(later["advanced"], later)
        self.assertEqual(later["reason"], "environment_applied")
        self.assertEqual(
            later["input"], {"class": "ENVIRONMENT", "sensory_intensity": 0.20}
        )

    def test_an_off_or_unknown_room_produces_no_input_and_no_write(self):
        state = runtime_with_clock(BASE)
        for light in (
            {"status": "available", "power": "off", "band": None},
            {"status": "unknown", "reason_code": room.REASON_POWER_UNKNOWN},
        ):
            outcome = bcon.advance_environment(
                runtime_state=state, light=light, at=BASE + timedelta(minutes=5)
            )
            self.assertFalse(outcome["advanced"])
            self.assertEqual(outcome["reason"], "no_environment_input")
            self.assertFalse(outcome["wrote"])

    def test_a_dim_lamp_feeds_the_existing_rule_without_touching_thresholds(self):
        state = runtime_with_clock(BASE)
        before = dict(state["somatic"])
        outcome = bcon.advance_environment(
            runtime_state=state, light=self.light(), at=BASE + timedelta(minutes=5)
        )
        self.assertTrue(outcome["advanced"], outcome)
        self.assertTrue(outcome["wrote"])
        self.assertEqual(outcome["input"], {"class": "ENVIRONMENT", "sensory_intensity": 0.20})
        transition = outcome["transition"]
        self.assertIn(br.CAUSE_ENVIRONMENT, transition["cause_classes"])
        self.assertIn(
            room.ENVIRONMENT_MODEL, transition["input_summary"]["environment_source"]
        )
        after = outcome["state"]["somatic"]
        # the existing rule rises toward the declared intensity but no more
        self.assertGreaterEqual(after["sensory_load"], before["sensory_load"])
        self.assertLess(after["sensory_load"], 0.50)
        # warmth is untouched: v1 never produces a temperature tendency
        self.assertEqual(after["warmth"], before["warmth"])
        signals = bsignal.produce_signals(outcome["state"], occurred_at=BASE)
        self.assertNotIn(
            "sensory_load_elevated",
            {signal["signal_type"] for signal in signals["signals"]},
        )
        # and the advance is not repeatable for the same interval
        again = bcon.advance_environment(
            runtime_state=outcome["state"], light=self.light(), at=BASE + timedelta(minutes=5)
        )
        self.assertFalse(again["advanced"])


# ---------------------------------------------------------------------------
# the owner tick
# ---------------------------------------------------------------------------


class OwnerTickTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        for key, value in ENVIRONMENT.items():
            os.environ[key] = value
        self.home = unique_home()
        seed_world(self.home, place="home_goods_store", area="counter")
        economy.initialize_genesis(hermes_home=self.home, environment=ENVIRONMENT)
        bought = economy.store_purchase_item(
            catalog_item_id=LAMP,
            operation_key="op-env-lamp",
            local_owner={
                "source": economy.LOCAL_OWNER_SOURCE,
                "uid": os.getuid(),
                "subject": economy.OWNER,
                "home": str(self.home),
            },
            hermes_home=self.home,
            environment={**ENVIRONMENT, economy.DELIVERY_FEATURE_ENV: "0"},
            now=BASE,
        )
        self.assertEqual(bought["status"], "applied", bought)
        self.lamp = bought["item_instance_id"]
        seed_world(self.home)
        seed_substrate(
            self.home,
            {
                self.lamp: {"kind": "room", "location_id": "home", "area_id": "bedroom"},
            },
        )
        self._bought = bought

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def config(self, *, flag: str):
        environment = dict(ENVIRONMENT)
        environment[room.FEATURE_ENV] = flag
        return wbc.load_config(
            environment,
            runtime_root=RUNTIME_ROOT,
            world_home=self.home,
            runtime_mode="canonical",
            authority="production",
            world_id="shiomi_city",
        )

    def service(self, *, flag: str = "0"):
        service = wbsvc.WorldBodyService(
            self.config(flag=flag), instance_id="rc1-room-env", tick_seconds=0.05
        )
        self.addCleanup(service.shutdown)
        service.startup()
        return service

    def set_lamp(self, power: str, *, since: str | None = None) -> None:
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = store.load()
        store.save(
            substrate.set_fixture(
                live,
                self.lamp,
                power,
                since=since or _minutes_ago(10),
                declared_source="catalog:bedside_lamp",
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )

    def test_the_flag_is_deny_by_default(self):
        self.assertFalse(room.feature_enabled({}))
        self.assertFalse(room.feature_enabled(ENVIRONMENT))
        config = self.config(flag="0")
        self.assertEqual(config.owned_environment()[wbc.ROOM_ENVIRONMENT_ENABLED_ENV], "0")
        enabled = self.config(flag="1")
        self.assertEqual(enabled.owned_environment()[wbc.ROOM_ENVIRONMENT_ENABLED_ENV], "1")

    def test_a_disabled_channel_writes_nothing_even_with_the_lamp_on(self):
        self.set_lamp("on")
        service = self.service(flag="0")
        service.body_state["last_advanced_at"] = _minutes_ago(5)
        before = file_fingerprint(self.home)
        self.assertIsNone(service._settle_environment())
        self.assertEqual(before, file_fingerprint(self.home))

    def test_the_tick_applies_the_light_once_and_reports_it(self):
        self.set_lamp("on")
        service = self.service(flag="1")
        service.body_state["last_advanced_at"] = _minutes_ago(5)
        outcome = service._settle_environment()
        self.assertTrue(outcome["advanced"], outcome)
        self.assertEqual(outcome["light"]["band"], "dim")
        self.assertEqual(outcome["light"]["power"], "on")
        # the same interval is not applied twice
        second = service._settle_environment()
        self.assertFalse(second["advanced"])
        self.assertIn(second["reason"], ("interval_too_short", "no_body_clock"))

    def test_a_recovery_advance_wins_the_one_advance_per_tick(self):
        self.set_lamp("on")
        service = self.service(flag="1")
        service.body_state["last_advanced_at"] = _minutes_ago(5)
        recovery_anchor = _minutes_ago(5)
        service.body_state["recovery"] = {
            **service.body_state["recovery"],
            "state": "active",
            "mode": "rest",
            "started_at": recovery_anchor,
        }
        report = service.tick()
        self.assertIsNotNone(report)
        self.assertIn("advance", report)
        self.assertTrue(report["advance"]["wrote"])
        # exactly one Body time advance: the light waits for the next wake
        self.assertNotIn("environment", report)

    def test_a_long_gap_claims_no_light_but_leaves_a_normal_next_tick(self):
        # the lamp was already on before the body clock stopped: the window in
        # between is genuinely unknown
        self.set_lamp("on", since=_hours_ago(5))
        service = self.service(flag="1")
        service.body_state["last_advanced_at"] = _hours_ago(3)
        before = dict(service.body_state["somatic"])
        outcome = service._settle_environment()
        self.assertEqual(outcome["reason"], "environment_gap_unknown")
        self.assertTrue(outcome["gap_unknown"])
        self.assertIsNone(outcome["input"])
        self.assertTrue(outcome["wrote"])
        # no light was applied for the unknown window
        self.assertLess(outcome["state"]["somatic"]["sensory_load"], 0.20)
        self.assertGreaterEqual(
            outcome["state"]["somatic"]["sensory_load"], before["sensory_load"]
        )
        # the body clock is now current, so the next wake is a normal window
        self.assertEqual(
            service.body_state["last_advanced_at"],
            outcome["state"]["last_advanced_at"],
        )
        follow_up = service._settle_environment()
        self.assertIn(follow_up["reason"], ("interval_too_short", "environment_applied"))
        self.assertNotEqual(follow_up.get("gap_unknown"), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
