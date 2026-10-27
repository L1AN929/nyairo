#!/usr/bin/env python3
"""Virtual lived needs: opt-in only, bounded drift, explicit relief.

The feature is deliberately small; these tests pin the boundaries that matter:

* a v1 body has no needs block and rejects a relief input (old roots unchanged);
* an explicit profile creates v2 with operator initial values;
* drift is capped per advance (a stopped process is not "starved" later);
* relief only comes from a World declaration's explicit ``lived_need_effects``;
* a facility must be declared here, with explicit effects, and the place open;
* the ``bodyact:toilet`` encoding round-trips and its completion fact reads the
  owner's band, never the requested target.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import body_consequence as bcon  # noqa: E402
import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402
import life_body_action_targets as targets  # noqa: E402
import world_lived_need_facilities as facilities  # noqa: E402

NOW = "2026-10-27T08:00:00+00:00"


def _profile(**overrides):
    values = {"hunger": 0.72, "thirst": 0.78, "toilet": 0.66}
    values.update(overrides)
    return bln.build_profile(**values)


def _v2(profile=None):
    profile = profile or _profile()
    return br.initial_runtime(
        at=NOW, lived_needs=bln.initial_state(profile))


class V1UnchangedTests(unittest.TestCase):
    def test_a_v1_body_has_no_needs_block(self):
        state = br.initial_runtime(at=NOW)
        self.assertEqual(state["schema_version"], br.SCHEMA_RUNTIME)
        self.assertNotIn("lived_needs", state)
        self.assertIsNone(bln.bands(state))

    def test_a_v1_body_rejects_a_relief_input(self):
        state = br.initial_runtime(at=NOW)
        with self.assertRaises(br.BodyRuntimeError):
            br.advance(state, NOW, "2026-10-27T08:01:00+00:00", {
                "class": "LIVED_NEED_RELIEF", "need": "thirst", "amount": 0.9})

    def test_a_consumable_without_effects_grants_no_relief(self):
        result = {"execution_id": "exec:one", "action": "CONSUME",
                  "status": "applied", "object_id": "riceball"}
        record = bcon.build_consequence(
            result, body_id="chiyo_body", lived_needs_enabled=True)
        self.assertEqual(record["inputs"][0]["class"], "PHYSICAL_RESULT")
        self.assertFalse(any(item["class"] == "LIVED_NEED_RELIEF"
                             for item in record["inputs"]))


class ProfileAndDriftTests(unittest.TestCase):
    def test_explicit_initial_values_become_v2(self):
        state = _v2()
        self.assertEqual(state["schema_version"], br.SCHEMA_RUNTIME_V2)
        self.assertEqual(state["lived_needs"]["levels"],
                         {"hunger": 0.72, "thirst": 0.78, "toilet": 0.66})
        self.assertEqual(bln.bands(state),
                         {"hunger": "noticeable", "thirst": "noticeable",
                          "toilet": "mild"})

    def test_an_idle_gap_does_not_backfill_needs(self):
        state = _v2()
        result = br.advance(state, NOW, "2026-10-27T20:00:00+00:00")
        # A stopped/idle process must not wake up hungrier.  Time drift belongs
        # to the service's live window; a plain advance only carries relief.
        self.assertEqual(result["lived_needs_after"]["levels"],
                         {"hunger": 0.72, "thirst": 0.78, "toilet": 0.66})

    def test_relief_subtracts_and_clamps(self):
        state = _v2()
        result = br.advance(state, NOW, "2026-10-27T08:00:00+00:00", {
            "class": "LIVED_NEED_RELIEF", "need": "thirst",
            "amount": 0.95, "source_ref": "exec:water"})
        self.assertEqual(result["lived_needs_after"]["levels"]["thirst"], 0.0)
        self.assertLess(result["lived_need_deltas"]["thirst"], 0.0)
        self.assertTrue(result["advanced"])

    def test_v2_roundtrips_through_validation(self):
        state = _v2()
        self.assertEqual(br.validate_runtime(state)["schema_version"],
                         br.SCHEMA_RUNTIME_V2)
        upgraded = br.upgrade_to_v2(br.initial_runtime(at=NOW),
                                    bln.initial_state(_profile()))
        self.assertEqual(upgraded["schema_version"], br.SCHEMA_RUNTIME_V2)
        self.assertEqual(upgraded["version"],
                         br.initial_runtime(at=NOW)["version"])


class LiveWindowTests(unittest.TestCase):
    """Only a service-witnessed interval moves the levels; downtime is skipped."""

    def test_first_call_only_anchors(self):
        state = _v2()["lived_needs"]
        outcome = bln.advance_live_window(state, now=NOW)
        self.assertEqual(outcome["reason"], "ANCHORED")
        self.assertEqual(outcome["counted_seconds"], 0.0)
        self.assertEqual(outcome["state"]["levels"], state["levels"])
        self.assertEqual(outcome["state"]["last_live_at"], NOW)

    def test_a_witnessed_tick_gap_accumulates(self):
        state = bln.advance_live_window(_v2()["lived_needs"], now=NOW)["state"]
        outcome = bln.advance_live_window(
            state, now="2026-10-27T08:01:00+00:00")
        self.assertEqual(outcome["reason"], "LIVE_WINDOW")
        self.assertEqual(outcome["counted_seconds"], 60.0)
        self.assertAlmostEqual(outcome["state"]["levels"]["thirst"],
                               0.78 + 0.12 * (60.0 / 3600.0), places=9)
        self.assertAlmostEqual(outcome["state"]["live_seconds"], 60.0)
        self.assertEqual(outcome["state"]["offline_skipped_seconds"], 0.0)

    def test_ten_hours_of_downtime_is_skipped_not_backfilled(self):
        state = bln.advance_live_window(_v2()["lived_needs"], now=NOW)["state"]
        outcome = bln.advance_live_window(
            state, now="2026-10-27T18:00:00+00:00")
        self.assertEqual(outcome["reason"], "OFFLINE_GAP")
        self.assertEqual(outcome["counted_seconds"], 0.0)
        self.assertEqual(outcome["skipped_seconds"], 36000.0)
        self.assertEqual(outcome["state"]["levels"], state["levels"])
        self.assertEqual(outcome["state"]["live_seconds"], 0.0)
        self.assertEqual(outcome["state"]["offline_skipped_seconds"], 36000.0)
        self.assertEqual(outcome["state"]["last_live_at"],
                         "2026-10-27T18:00:00+00:00")

    def test_a_clock_rewind_is_skipped(self):
        state = bln.advance_live_window(_v2()["lived_needs"], now=NOW)["state"]
        outcome = bln.advance_live_window(
            state, now="2026-10-27T07:00:00+00:00")
        self.assertEqual(outcome["reason"], "CLOCK_REWOUND")
        self.assertEqual(outcome["state"]["levels"], state["levels"])

    def test_a_legacy_state_without_window_fields_is_read(self):
        legacy = dict(_v2()["lived_needs"])
        for key in ("live_seconds", "last_live_at", "offline_skipped_seconds"):
            legacy.pop(key)
        upgraded = bln.validate_state(legacy)
        self.assertEqual(upgraded["live_seconds"], 0.0)
        self.assertIsNone(upgraded["last_live_at"])
        outcome = bln.advance_live_window(upgraded, now=NOW)
        self.assertEqual(outcome["reason"], "ANCHORED")

    def test_relief_survives_a_long_advance_gap(self):
        state = _v2()
        result = br.advance(state, NOW, "2026-10-27T20:00:00+00:00", {
            "class": "LIVED_NEED_RELIEF", "need": "hunger",
            "amount": 0.3, "source_ref": "exec:rice"})
        self.assertAlmostEqual(result["lived_needs_after"]["levels"]["hunger"],
                               0.42, places=9)


class ConsumeDeclarationTests(unittest.TestCase):
    def test_declared_effects_become_relief_inputs(self):
        result = {"execution_id": "exec:water", "action": "CONSUME",
                  "status": "applied", "object_id": "water",
                  "lived_need_effects": {"thirst": 0.8}}
        record = bcon.build_consequence(
            result, body_id="chiyo_body", lived_needs_enabled=True)
        relief = [item for item in record["inputs"]
                  if item["class"] == "LIVED_NEED_RELIEF"]
        self.assertEqual(len(relief), 1)
        self.assertEqual(relief[0]["need"], "thirst")
        self.assertEqual(relief[0]["amount"], 0.8)
        self.assertEqual(record["lived_need_effects"], {"thirst": 0.8})

    def test_the_same_result_adds_nothing_to_a_v1_body(self):
        result = {"execution_id": "exec:water", "action": "CONSUME",
                  "status": "applied", "object_id": "water",
                  "lived_need_effects": {"thirst": 0.8}}
        record = bcon.build_consequence(result, body_id="chiyo_body")
        self.assertFalse(any(item["class"] == "LIVED_NEED_RELIEF"
                             for item in record["inputs"]))

    def test_effect_values_are_bounded_and_optional(self):
        self.assertEqual(bln.declaration_effects(
            {"lived_need_effects": {"hunger": 0.5, "thirst": 2.0}}), {})
        self.assertEqual(bln.declaration_effects(
            {"lived_need_effects": {"unknown": 0.5}}), {})


class ToiletEncodingTests(unittest.TestCase):
    def test_encode_parse_and_fact(self):
        ref = targets.encode_toilet_target("home-toilet-01")
        parsed = targets.parse_body_action_target(ref)
        self.assertEqual(parsed["verb"], "TOILET")
        self.assertEqual(parsed["params"], {"facility_id": "home-toilet-01"})
        self.assertEqual(parsed["expected_value"],
                         "world_fact:body:need:toilet:relieved")
        fact = targets.body_action_observed_fact(
            parsed, {}, toilet_availability={"current": {"toilet_band": "none"}})
        self.assertTrue(fact["satisfied"])
        still_needed = targets.body_action_observed_fact(
            parsed, {}, toilet_availability={"current": {"toilet_band": "mild"}})
        self.assertFalse(still_needed["satisfied"])
        unknown = targets.body_action_observed_fact(parsed, {})
        self.assertFalse(unknown["satisfied"])
        self.assertIsNone(unknown["value"])

    def test_facility_verdict_requires_this_place_and_explicit_effects(self):
        declaration = {"facility_kind": "toilet", "location_id": "home",
                       "lived_need_effects": {"toilet": 0.9}}
        verdict = facilities.facility_verdict(
            facility_id="home-toilet-01", declaration=declaration,
            current_place="home",
            now=datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc),
            environment={})
        # Whether "home" is open depends on the world rhythm, not on this test;
        # if it is open the verdict carries the declared effect, and if it is
        # not the refusal must be explicit.
        if verdict["allowed"]:
            self.assertEqual(verdict["effects"], {"toilet": 0.9})
        else:
            self.assertIn(verdict["reason_code"],
                          {"RHYTHM_UNAVAILABLE", "LOCATION_CLOSED"})
        wrong_place = facilities.facility_verdict(
            facility_id="home-toilet-01", declaration=declaration,
            current_place="street", now=None)
        self.assertEqual(wrong_place["reason_code"], facilities.NOT_HERE)
        no_effects = facilities.facility_verdict(
            facility_id="home-toilet-01",
            declaration={"facility_kind": "toilet", "location_id": "home"},
            current_place="home", now=None)
        self.assertEqual(no_effects["reason_code"], facilities.EFFECT_MISSING)


if __name__ == "__main__":
    unittest.main()
