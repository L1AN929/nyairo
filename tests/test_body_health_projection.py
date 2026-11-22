#!/usr/bin/env python3
"""S02 health projection tests: real body runtime -> health record -> envelope.

The runtime state here is built by the real ``body_runtime`` owner
(``initial_runtime`` + ``advance``), not by a hand-written dict, so the join
is exercised against the shape the product actually persists.

Run:  python3 tests/test_body_health_projection.py
"""

from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from components.world.scripts import body_health as bh  # noqa: E402
from components.world.scripts import body_health_projection as proj  # noqa: E402
from components.world.scripts import body_runtime as runtime  # noqa: E402

AT = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)


def live_runtime(at=AT, hours=0.0, load_hours=0.0):
    state = runtime.initial_runtime(at=at)
    if hours:
        inputs = []
        if load_hours:
            inputs.append({
                "class": "physical",
                "kind": "walk",
                "hours": load_hours,
                "intensity": 0.8,
                "occurred_at": at.isoformat(),
            })
        state = runtime.advance(state, at, at + timedelta(hours=hours), inputs)
    return state


def fresh_record():
    return proj.initial_record(
        body_id="chiyo_body", subject_id="sub-1", home_ref="home-1", at=AT
    )


class ProjectionBasicsTest(unittest.TestCase):
    def test_fresh_record_without_runtime_is_unattested(self):
        snapshot, envelope = proj.project(fresh_record(), None, at=AT)
        self.assertIs(snapshot.status, bh.HealthStatus.UNATTESTED)
        self.assertEqual(envelope.capacity_state, "UNKNOWN")
        self.assertEqual(envelope.candidate_cap, 0)

    def test_fresh_record_with_a_real_runtime_signal_is_healthy(self):
        # The runtime is the current body evidence; the record is not required
        # to have been advanced first.  No runtime signal still means UNATTESTED.
        snapshot, envelope = proj.project(fresh_record(), live_runtime(), at=AT)
        self.assertIs(snapshot.status, bh.HealthStatus.HEALTHY)
        self.assertTrue(snapshot.attested)
        self.assertFalse(envelope.creative_block)

    def test_advanced_record_with_low_fatigue_is_healthy(self):
        record = proj.advance_record(fresh_record(), at=AT, hours=1.0, rested=True)
        state = live_runtime()
        snapshot, envelope = proj.project(record, state, at=AT)
        self.assertIs(snapshot.status, bh.HealthStatus.HEALTHY)
        self.assertEqual(envelope.capacity_index, 1.0)
        self.assertEqual(envelope.candidate_cap, 12)
        self.assertFalse(envelope.creative_block)

    def test_runtime_without_somatic_is_never_healthy(self):
        record = proj.advance_record(fresh_record(), at=AT, hours=1.0, rested=True)
        snapshot, envelope = proj.project(record, {"schema_version": "body.runtime.v2"}, at=AT)
        self.assertIs(snapshot.status, bh.HealthStatus.UNATTESTED)
        self.assertEqual(envelope.capacity_state, "UNKNOWN")

    def test_stray_seconds(self):
        record = fresh_record()
        self.assertIsNone(proj.stray_seconds(record, AT))
        moved = proj.advance_record(record, at=AT, hours=1.0)
        self.assertEqual(proj.stray_seconds(moved, AT + timedelta(seconds=90)), 90.0)


class StrainTest(unittest.TestCase):
    def test_sustained_load_becomes_strained(self):
        record = fresh_record()
        for _ in range(8):
            record = proj.advance_record(record, at=AT, hours=1.0, load_hours=1.0)
        # Runtime is the authority for acute fatigue; here the load lives in the
        # record only, so the projection reads without a runtime override.
        snapshot, envelope = proj.project(record, None, at=AT, attested=True)
        self.assertIs(snapshot.status, bh.HealthStatus.STRAINED)
        self.assertTrue(envelope.action_verify_required)
        self.assertGreaterEqual(envelope.capacity_index, 0.60)
        self.assertLessEqual(envelope.capacity_index, 0.80)


class CourseTest(unittest.TestCase):
    def test_exposure_opens_an_acute_course(self):
        record = proj.admit_exposure(
            fresh_record(), episode_id="ep-1", at=AT, origin="world_exposure", severity=0.6
        )
        self.assertIsNotNone(record.episode)
        self.assertTrue(record.episode.open)
        snapshot, envelope = proj.project(record, live_runtime(), at=AT)
        self.assertIs(snapshot.status, bh.HealthStatus.ACUTE_ILLNESS)
        self.assertTrue(envelope.creative_block)

    def test_conversation_origin_is_refused(self):
        with self.assertRaises(ValueError):
            proj.admit_exposure(
                fresh_record(), episode_id="ep-1", at=AT, origin="conversation", severity=0.6
            )

    def test_course_heals_only_with_time_and_rest(self):
        record = proj.admit_exposure(
            fresh_record(), episode_id="ep-1", at=AT, origin="world_exposure", severity=0.5
        )
        moment = AT
        steps = 0
        while record.episode is not None and record.episode.open and steps < 400:
            moment = moment + timedelta(hours=2)
            record = proj.advance_record(
                record, at=moment, hours=2.0, mode="sleep_like", rested=True
            )
            steps += 1
        self.assertLess(steps, 400, "a course must close on its own clock")
        self.assertIsNotNone(record.episode.closed_at)
        self.assertLess(record.episode.severity, bh.RECOVERED_SEVERITY)

    def test_care_alone_never_skips_the_course(self):
        record = proj.admit_exposure(
            fresh_record(), episode_id="ep-1", at=AT, origin="world_exposure", severity=0.5
        )
        after_rest = proj.advance_record(record, at=AT, hours=1.0, mode="rest", rested=True)
        self.assertGreater(after_rest.episode.severity, 0.08)
        self.assertTrue(after_rest.episode.open)

    def test_second_exposure_does_not_stack(self):
        record = proj.admit_exposure(
            fresh_record(), episode_id="ep-1", at=AT, origin="world_exposure", severity=0.4
        )
        again = proj.admit_exposure(
            record, episode_id="ep-2", at=AT, origin="carrier_selfcheck", severity=0.9
        )
        self.assertEqual(again.episode.episode_id, "ep-1")


class V03CourseSourceTest(unittest.TestCase):
    """The v03 file owns identity and time; a read must not invent either."""

    SOURCE = {
        "phase": "acute_illness",
        "severity": 0.6,
        "episode_id": "ep:real-1",
        "episode_opened_at": "2026-11-14T20:00:00+00:00",
        "acute_entered_at": "2026-11-14T21:00:00+00:00",
        "phase_entered_at": "2026-11-15T06:00:00+00:00",
    }

    def test_identity_and_time_are_stable_across_reads(self):
        first = proj.record_from_illness(
            self.SOURCE, body_id="chiyo_body", subject_id="self", home_ref="", at=AT)
        later = proj.record_from_illness(
            self.SOURCE, body_id="chiyo_body", subject_id="self", home_ref="",
            at=AT + timedelta(hours=5))
        self.assertEqual(first.episode.episode_id, "ep:real-1")
        self.assertEqual(later.episode.episode_id, "ep:real-1")
        self.assertEqual(first.episode.opened_at, later.episode.opened_at)
        self.assertEqual(first.health_since, later.health_since)

    def test_prodrome_stays_strained_and_never_convalescent(self):
        record = proj.record_from_illness(
            dict(self.SOURCE, phase="prodrome", severity=0.20),
            body_id="chiyo_body", subject_id="self", home_ref="", at=AT)
        self.assertIs(record.status, bh.HealthStatus.STRAINED)
        snapshot, _ = proj.project(record, live_runtime(), at=AT)
        self.assertIs(snapshot.status, bh.HealthStatus.STRAINED)
        self.assertIsNone(record.episode)

    def test_an_unsourced_course_is_a_named_unknown(self):
        record = proj.record_from_illness(
            {"phase": "acute_illness", "severity": 0.6},
            body_id="chiyo_body", subject_id="self", home_ref="", at=AT)
        self.assertIs(record.status, bh.HealthStatus.UNATTESTED)
        self.assertEqual(record.source_reason, "ILLNESS_EPISODE_UNSOURCED")
        snapshot, envelope = proj.project(record, live_runtime(), at=AT)
        self.assertIs(snapshot.status, bh.HealthStatus.UNATTESTED)
        self.assertEqual(envelope.capacity_state, "UNKNOWN")


class PhoneViewEvidenceTest(unittest.TestCase):
    """Missing or corrupt phone-side evidence is never reported as health."""

    def _root(self, tmp):
        import pathlib

        return pathlib.Path(tmp)

    def test_missing_runtime_is_unattested(self):
        import tempfile

        # Kept on purpose: the root is evidence, not throwaway state.
        tmp = tempfile.mkdtemp(prefix="v08-proj-missing-")
        view = proj.body_health_view(self._root(tmp), at=AT)
        self.assertEqual(view["health_status"], "UNATTESTED")
        self.assertFalse(view["attested"])
        self.assertEqual(view["reason_code"], "RUNTIME_UNAVAILABLE")
        # No evidence is not a refusal: the panel must not claim "no intense
        # creative work" or cap her to zero before any reading exists.
        self.assertFalse(view["capacity"]["creative_block"])
        self.assertIsNone(view["capacity"]["candidate_cap"])
        self.assertEqual(view["capacity"]["state"], "UNKNOWN")

    def test_corrupt_health_record_is_not_reset_to_healthy(self):
        import json
        import tempfile

        # Kept on purpose: the root is evidence, not throwaway state.
        tmp = tempfile.mkdtemp(prefix="v08-proj-corrupt-")
        root = self._root(tmp)
        body = root / "data" / "body"
        body.mkdir(parents=True)
        (body / proj.HEALTH_FILE_NAME).write_text(
            json.dumps({"schema_version": "nope"}), encoding="utf8")
        view = proj.body_health_view(root, at=AT)
        self.assertEqual(view["health_status"], "UNATTESTED")
        self.assertEqual(view["reason_code"], "HEALTH_RECORD_UNREADABLE")
        self.assertEqual(view["writes_performed"], 0)
        self.assertFalse(view["capacity"]["creative_block"])
        self.assertIsNone(view["capacity"]["candidate_cap"])
        self.assertEqual(view["capacity"]["state"], "UNKNOWN")


class StoreTest(unittest.TestCase):
    def test_round_trip(self):
        import tempfile

        # Kept on purpose: the root is evidence, not throwaway state.
        tmp = tempfile.mkdtemp(prefix="v08-proj-store-")
        store = proj.HealthStore(tmp)
        self.assertIsNone(store.load())
        record = proj.admit_exposure(
            fresh_record(), episode_id="ep-1", at=AT, origin="world_exposure", severity=0.5
        )
        store.save(record)
        again = store.load()
        self.assertEqual(again.body_id, record.body_id)
        self.assertEqual(again.status, record.status)
        self.assertEqual(again.episode.severity, record.episode.severity)
        self.assertEqual(again.episode.opened_at, record.episode.opened_at)
        self.assertEqual(again.fatigue.acute, record.fatigue.acute)

    def test_corrupt_record_is_refused(self):
        import tempfile
        from pathlib import Path

        # Kept on purpose: the root is evidence, not throwaway state.
        tmp = tempfile.mkdtemp(prefix="v08-proj-refuse-")
        store = proj.HealthStore(tmp)
        Path(tmp, proj.HEALTH_FILE_NAME).write_text('{"schema_version": "nope"}', encoding="utf8")
        with self.assertRaises(ValueError):
            store.load()

    def test_unknown_status_or_origin_is_refused(self):
        base = proj.record_to_dict(fresh_record())
        bad_status = dict(base, status="RECOVERED")
        with self.assertRaises(ValueError):
            proj.record_from_dict(bad_status)
        with_episode = dict(base, episode={
            "episode_id": "ep-1",
            "opened_at": AT.isoformat(),
            "origin": "conversation",
            "severity": 0.4,
            "peak": 0.4,
        })
        with self.assertRaises(ValueError):
            proj.record_from_dict(with_episode)


if __name__ == "__main__":
    unittest.main()
