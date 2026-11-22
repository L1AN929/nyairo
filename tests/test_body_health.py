#!/usr/bin/env python3
"""S02 body health tests: the illness state machine and the capacity envelope.

Covers design v3.0 §05 §1–2:

* no attestation never degrades to HEALTHY;
* fatigue integrates load and decays back to the baseline;
* severity obeys ds/dt = α·u·(1-s) - γ·s and strictly decreases with no exposure;
* double hysteresis (0.35 in / 0.25 out) prevents flapping;
* a course only closes after 18h out of the acute phase with fatigue at baseline;
* conversation is not an allowed illness origin;
* the capability envelope matches the per-status table and narrows monotonically.

Run:  python3 tests/test_body_health.py
"""

from __future__ import annotations

import pathlib
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from components.world.scripts import body_health as bh  # noqa: E402

AT = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


def snapshot(**overrides):
    base = dict(
        body_id="chiyo_body",
        subject_id="sub-1",
        home_ref="home-1",
        revision=1,
        observed_at=AT,
        attested=True,
        status=bh.HealthStatus.HEALTHY,
        health_since=AT,
        fatigue=bh.FatigueAccumulator(),
        episode=None,
        hours_out_of_acute=0.0,
    )
    base.update(overrides)
    return bh.HealthSnapshot(**base)


class AttestationTest(unittest.TestCase):
    def test_no_signal_is_not_health(self):
        status = bh.select_status(
            attested=False,
            previous=bh.HealthStatus.HEALTHY,
            severity=0.0,
            fatigue=bh.FatigueAccumulator(),
            hours_out_of_acute=999.0,
            episode_open=False,
        )
        self.assertIs(status, bh.HealthStatus.UNATTESTED)
        envelope = bh.derive_envelope(snapshot(attested=False, status=status))
        self.assertEqual(envelope.capacity_state, "UNKNOWN")
        self.assertEqual(envelope.candidate_cap, 0)
        self.assertEqual(envelope.max_plan_steps, 0)
        self.assertTrue(envelope.creative_block)

    def test_healthy_requires_attestation_and_no_load(self):
        status = bh.select_status(
            attested=True,
            previous=bh.HealthStatus.UNATTESTED,
            severity=0.0,
            fatigue=bh.FatigueAccumulator(),
            hours_out_of_acute=0.0,
            episode_open=False,
        )
        self.assertIs(status, bh.HealthStatus.HEALTHY)


class FatigueTest(unittest.TestCase):
    def test_load_raises_acute_and_decay_returns_to_baseline(self):
        start = bh.FatigueAccumulator()
        loaded = bh.accumulate_fatigue(start, load_hours=1.0, hours=1.0, recovery_multiplier=0.9)
        self.assertGreater(loaded.acute, start.acute)

        rested = loaded
        for _ in range(12):
            rested = bh.accumulate_fatigue(rested, load_hours=0.0, hours=4.0, recovery_multiplier=1.0)
        self.assertLess(rested.acute, loaded.acute)
        self.assertGreaterEqual(rested.acute, bh.BASELINE_FATIGUE - 1e-9)

    def test_chronic_accumulates_slower_than_acute(self):
        start = bh.FatigueAccumulator()
        loaded = bh.accumulate_fatigue(start, load_hours=1.0, hours=1.0, recovery_multiplier=0.9)
        self.assertLess(loaded.chronic, loaded.acute)

    def test_negative_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            bh.accumulate_fatigue(bh.FatigueAccumulator(), load_hours=-1.0, hours=1.0)
        with self.assertRaises(ValueError):
            bh.accumulate_fatigue(bh.FatigueAccumulator(), load_hours=0.0, hours=-1.0)


class SeverityTest(unittest.TestCase):
    def test_no_exposure_strictly_decreases(self):
        value = 0.60
        for _ in range(10):
            nxt = bh.advance_severity(value, hours=6.0, exposure=0.0, mode="rest")
            self.assertLess(nxt, value)
            value = nxt

    def test_exposure_raises_severity(self):
        grown = bh.advance_severity(0.10, hours=6.0, exposure=1.0, mode="none")
        self.assertGreater(grown, 0.10)

    def test_recovery_mode_changes_clearance(self):
        sleep = bh.advance_severity(0.50, hours=12.0, exposure=0.0, mode="sleep_like")
        rest = bh.advance_severity(0.50, hours=12.0, exposure=0.0, mode="rest")
        push = bh.advance_severity(0.50, hours=12.0, exposure=0.0, mode="none")
        self.assertLess(sleep, rest)
        self.assertLess(rest, push)

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            bh.advance_severity(0.5, hours=1.0, mode="medicine")


class EpisodeTest(unittest.TestCase):
    def test_conversation_can_never_open_a_course(self):
        with self.assertRaises(ValueError):
            bh.open_episode("ep-1", opened_at=AT, origin="conversation", severity=0.4)

    def test_allowed_origins_open(self):
        for origin in bh.ILLNESS_ORIGINS:
            episode = bh.open_episode(f"ep-{origin}", opened_at=AT, origin=origin, severity=0.4)
            self.assertTrue(episode.open)
            self.assertEqual(episode.peak, 0.4)

    def test_closed_episode_does_not_move(self):
        episode = bh.open_episode("ep-1", opened_at=AT, origin="world_exposure", severity=0.5)
        closed = bh.close_episode(episode, at=AT + timedelta(hours=2))
        frozen = bh.advance_episode(closed, at=AT + timedelta(hours=3), hours=1.0)
        self.assertEqual(frozen.severity, closed.severity)
        self.assertFalse(frozen.open)


class HysteresisTest(unittest.TestCase):
    def test_enter_at_035_and_exit_only_below_025(self):
        healthy = bh.select_status(
            attested=True, previous=bh.HealthStatus.HEALTHY, severity=0.34,
            fatigue=bh.FatigueAccumulator(), hours_out_of_acute=0.0, episode_open=True,
        )
        self.assertIs(healthy, bh.HealthStatus.CONVALESCENT)

        acute = bh.select_status(
            attested=True, previous=bh.HealthStatus.HEALTHY, severity=0.36,
            fatigue=bh.FatigueAccumulator(), hours_out_of_acute=0.0, episode_open=True,
        )
        self.assertIs(acute, bh.HealthStatus.ACUTE_ILLNESS)

        # Inside the hysteresis band the previous acute state is kept.
        still_acute = bh.select_status(
            attested=True, previous=bh.HealthStatus.ACUTE_ILLNESS, severity=0.30,
            fatigue=bh.FatigueAccumulator(), hours_out_of_acute=2.0, episode_open=True,
        )
        self.assertIs(still_acute, bh.HealthStatus.ACUTE_ILLNESS)

        recovered_phase = bh.select_status(
            attested=True, previous=bh.HealthStatus.ACUTE_ILLNESS, severity=0.24,
            fatigue=bh.FatigueAccumulator(), hours_out_of_acute=3.0, episode_open=True,
        )
        self.assertIs(recovered_phase, bh.HealthStatus.CONVALESCENT)

    def test_strained_latches_until_fatigue_settles(self):
        strained = bh.select_status(
            attested=True, previous=bh.HealthStatus.HEALTHY, severity=0.0,
            fatigue=bh.FatigueAccumulator(chronic=0.25), hours_out_of_acute=0.0, episode_open=False,
        )
        self.assertIs(strained, bh.HealthStatus.STRAINED)
        settled = bh.select_status(
            attested=True, previous=bh.HealthStatus.STRAINED, severity=0.0,
            fatigue=bh.FatigueAccumulator(chronic=0.05), hours_out_of_acute=0.0, episode_open=False,
        )
        self.assertIs(settled, bh.HealthStatus.HEALTHY)


class RecoveryTest(unittest.TestCase):
    def test_closing_needs_severity_time_and_baseline(self):
        self.assertFalse(bh.episode_closed(
            severity=0.05, hours_out_of_acute=17.9, fatigue=bh.FatigueAccumulator(chronic=0.0)))
        self.assertFalse(bh.episode_closed(
            severity=0.05, hours_out_of_acute=20.0, fatigue=bh.FatigueAccumulator(chronic=0.30)))
        self.assertTrue(bh.episode_closed(
            severity=0.07, hours_out_of_acute=18.0, fatigue=bh.FatigueAccumulator(chronic=0.1)))


class EnvelopeTest(unittest.TestCase):
    def test_healthy_envelope_matches_table(self):
        envelope = bh.derive_envelope(snapshot())
        self.assertEqual(envelope.capacity_index, 1.0)
        self.assertEqual(envelope.candidate_cap, 12)
        self.assertEqual(envelope.max_plan_steps, 6)
        self.assertEqual(envelope.latency_floor_ms, 400)
        self.assertFalse(envelope.creative_block)
        self.assertFalse(envelope.action_verify_required)
        self.assertEqual(envelope.concurrent_activity_cap, 2)

    def test_strained_band(self):
        envelope = bh.derive_envelope(snapshot(
            status=bh.HealthStatus.STRAINED,
            fatigue=bh.FatigueAccumulator(acute=0.4, chronic=0.5),
        ))
        self.assertGreaterEqual(envelope.capacity_index, 0.60)
        self.assertLessEqual(envelope.capacity_index, 0.80)
        self.assertGreaterEqual(envelope.candidate_cap, 8)
        self.assertLessEqual(envelope.candidate_cap, 10)
        self.assertTrue(envelope.action_verify_required)
        self.assertFalse(envelope.creative_block)
        self.assertGreaterEqual(envelope.latency_floor_ms, 540)
        self.assertLessEqual(envelope.latency_floor_ms, 980)

    def test_acute_band_blocks_creative_work(self):
        episode = bh.open_episode("ep-1", opened_at=AT, origin="world_exposure", severity=0.6)
        envelope = bh.derive_envelope(snapshot(status=bh.HealthStatus.ACUTE_ILLNESS, episode=episode))
        self.assertGreaterEqual(envelope.capacity_index, 0.10)
        self.assertLessEqual(envelope.capacity_index, 0.35)
        self.assertTrue(envelope.creative_block)
        self.assertTrue(envelope.action_verify_required)
        self.assertGreaterEqual(envelope.candidate_cap, 3)
        self.assertLessEqual(envelope.candidate_cap, 6)
        self.assertGreaterEqual(envelope.max_plan_steps, 1)
        self.assertLessEqual(envelope.max_plan_steps, 3)
        self.assertEqual(envelope.concurrent_activity_cap, 1)
        self.assertGreaterEqual(envelope.latency_floor_ms, 1920)
        self.assertLessEqual(envelope.latency_floor_ms, 3320)

    def test_convalescent_band(self):
        episode = bh.open_episode("ep-1", opened_at=AT, origin="world_exposure", severity=0.20)
        envelope = bh.derive_envelope(snapshot(status=bh.HealthStatus.CONVALESCENT, episode=episode))
        self.assertGreaterEqual(envelope.capacity_index, 0.45)
        self.assertLessEqual(envelope.capacity_index, 0.75)
        self.assertGreaterEqual(envelope.max_plan_steps, 3)
        self.assertLessEqual(envelope.max_plan_steps, 5)

    def test_envelope_is_read_only(self):
        envelope = bh.derive_envelope(snapshot())
        self.assertTrue(envelope.read_only)
        self.assertEqual(envelope.writes_performed, 0)


class DeterminismTest(unittest.TestCase):
    def test_same_inputs_same_outputs(self):
        first = bh.advance_severity(0.42, hours=7.5, exposure=0.3, mode="rest")
        second = bh.advance_severity(0.42, hours=7.5, exposure=0.3, mode="rest")
        self.assertEqual(first, second)

        a = bh.accumulate_fatigue(bh.FatigueAccumulator(acute=0.4, chronic=0.2), load_hours=0.7, hours=2.0)
        b = bh.accumulate_fatigue(bh.FatigueAccumulator(acute=0.4, chronic=0.2), load_hours=0.7, hours=2.0)
        self.assertEqual(a, b)

    def test_module_has_no_clock_or_randomness(self):
        source = pathlib.Path(bh.__file__).read_text(encoding="utf8")
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        for banned in ("datetime.now", "time.time", "import random", "random.", "urllib", "requests"):
            self.assertNotIn(banned, code, f"{banned} must not appear in body_health")


if __name__ == "__main__":
    unittest.main()
