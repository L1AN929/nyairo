#!/usr/bin/env python3
"""v0.4 R: the resident's runtime record tells proven time from wall time -- in seconds.

What this module pins
---------------------
``resident_summary.json`` used to answer "how long has she been alive" with
``now - first_start``: a wall span that counts every hour the process was down.  Two processes,
one crash and one reboot all look identical from that number, so it could pass 86400 without
the resident ever being up.  The record now reports four separate quantities, and these tests
hold each of them apart:

1. two processes that really stopped are not one continuous life: coverage is what each
   process measured itself, the hole between them is an offline gap, and neither is derived
   from the other (``time.monotonic()`` is never added across a restart);
2. an unclosed interval (a crash) leaves its tail *unknown*; it is never read as "up since
   the start" and never silently repaired by the next process;
3. a wall clock that moved backwards is not negative life;
4. a process with no measurement is unknown, never 0 health;
5. the threshold labels come from covered seconds, and no label mints ``SOAK_VERIFIED``;
6. a legacy summary's wall span is kept as unknown, so an old record cannot claim 24h;
7. the loop's deadline is wall clock and the wait is clipped to it, so a 60-second run does
   not take a whole ``poll_seconds`` longer to stop;
8. an ACTED wake is not a confirmed owner version unless the wake *names* one;
9. the model-request total includes the private reading response surface;
10. two records that disagree about wall time are named as ambiguity, never as continuity, and
    their real measurements are not reduced;
11. an unmeasured, unclosed row followed by a new process is one hole counted once, not two
    overlapping ones.

Everything is clock-injected: no process is started, no runtime is built, no provider is
called, and no test waits.  The fixture directories are kept (``mkdtemp`` without cleanup).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_resident as resident  # noqa: E402
import life_runtime_coverage as cov  # noqa: E402

BASE = datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> str:
    """One wall instant, ``seconds`` after the fixture's base, in the record's own format."""

    return (BASE + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class FakeClock:
    """One wall clock and one monotonic clock, advanced only when the test advances them.

    ``advance`` is also the resident's sleeper, so a wait of N seconds moves both clocks by N:
    a loop that oversleeps is then visible as a wall span larger than the deadline, without
    anything actually waiting.
    """

    def __init__(self, *, wall: datetime = BASE, monotonic: float = 1000.0) -> None:
        self.wall = wall
        self.monotonic = monotonic
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.wall

    def mono(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.wall = self.wall + timedelta(seconds=float(seconds))
        self.monotonic += float(seconds)


def _state_root(prefix: str = "v04-runtime-coverage-") -> Path:
    """A fixture state root.  Kept after the test: the ledger is the evidence under test."""

    return Path(tempfile.mkdtemp(prefix=prefix))


def _resident(state_root: Path, clock: FakeClock, *, poll_seconds: int = 180
              ) -> "resident.AutonomousLifeResident":
    return resident.AutonomousLifeResident(
        state_root=state_root, environment={}, poll_seconds=poll_seconds,
        clock=clock.now, sleeper=clock.advance, monotonic=clock.mono)


def _process(pid: int, started: float, stopped: float, online: int) -> dict:
    """One process that really stopped, as the ledger records it."""

    row = cov.new_process_record(process_id="host:%s:%s" % (pid, _at(started)), pid=pid,
                                 started_at=_at(started), monotonic_started=10.0)
    return cov.close_process_record(row, closed_at=_at(stopped), online_seconds=online)


def _without_journey_module(loop: "resident.AutonomousLifeResident") -> None:
    """Answer ``journey_view`` without importing the World journey engine.

    That engine imports POSIX ``fcntl``, which this Windows test host does not have, and the
    journey has nothing to do with the coverage arithmetic under test.  Only the view is
    replaced; nothing about the process ledger, the clocks or the summary changes.
    """

    loop.journey_view = lambda: {"configured": False,
                                 "reason": "NO_JOURNEY_MODULE_ON_THIS_HOST"}


class CoverageArithmeticTest(unittest.TestCase):
    """The four quantities, from the ledger alone -- no process, no resident."""

    def test_two_processes_that_stopped_are_not_one_continuous_life(self):
        first = _process(100, 0, 3600, 3600)
        second = _process(200, 3 * 3600, 3 * 3600 + 1800, 1800)

        view = cov.coverage([first, second], now=_at(4 * 3600))

        self.assertEqual(view["covered_seconds"], 5400)
        self.assertEqual(view["process_online_seconds"], 5400)
        self.assertEqual(view["offline_gap_seconds"], 2 * 3600)
        # The wall span counts the two hours she was down; coverage does not.
        self.assertEqual(view["wall_span_seconds"], 3 * 3600 + 1800)
        self.assertEqual(view["unknown_seconds"], 0)
        self.assertTrue(view["coverage_complete"])
        self.assertEqual(view["process_count"], 2)
        self.assertEqual([gap["kind"] for gap in view["gaps"]], ["OFFLINE"])
        self.assertNotEqual(view["covered_seconds"], view["wall_span_seconds"])
        # The accounting is complete (every interval is known) but the life was not continuous.
        self.assertFalse(view["continuous"])

    def test_a_crashed_process_tail_is_unknown_and_never_up_since_the_start(self):
        closed = _process(100, 0, 3600, 3600)
        crashed = cov.new_process_record(process_id="host:200:%s" % _at(3 * 3600), pid=200,
                                         started_at=_at(3 * 3600), monotonic_started=5.0)
        crashed = cov.heartbeat(crashed, at=_at(3 * 3600 + 600), online_seconds=600)

        view = cov.coverage([closed, crashed], now=_at(4 * 3600))

        # What the two processes measured is what is covered: 3600 + 600, not the wall span.
        self.assertEqual(view["covered_seconds"], 4200)
        self.assertEqual(view["wall_span_seconds"], 4 * 3600)
        self.assertEqual(view["offline_gap_seconds"], 2 * 3600)
        # The hour from the last heartbeat to now is a hole, not life.
        self.assertEqual(view["unknown_seconds"], 3000)
        self.assertFalse(view["coverage_complete"])
        reasons = [entry["reason"] for entry in view["unknown"]]
        self.assertIn(cov.REASON_UNCLOSED, reasons)
        self.assertEqual(view["processes"][1]["closed"], cov.CLOSED_UNCLOSED)

    def test_a_wall_rollback_is_not_negative_life(self):
        backwards = cov.new_process_record(process_id="host:100:%s" % _at(2 * 3600), pid=100,
                                           started_at=_at(2 * 3600), monotonic_started=1.0)
        backwards = cov.close_process_record(backwards, closed_at=_at(3600), online_seconds=1800)

        view = cov.coverage([backwards], now=_at(3600 + 1800))

        self.assertEqual(view["covered_seconds"], 1800)
        self.assertEqual(view["wall_span_seconds"], 0)
        self.assertTrue(view["wall_rollback"])
        self.assertGreaterEqual(view["wall_span_seconds"], 0)
        self.assertGreaterEqual(view["offline_gap_seconds"], 0)

    def test_a_process_without_a_measurement_is_unknown_not_zero_health(self):
        unmeasured = cov.new_process_record(process_id="host:100:%s" % _at(0), pid=100,
                                            started_at=_at(0))
        unmeasured = cov.close_process_record(unmeasured, closed_at=_at(1200),
                                              online_seconds=None)

        view = cov.coverage([unmeasured], now=_at(3600))

        self.assertEqual(view["covered_seconds"], 0)
        self.assertEqual(view["unknown_seconds"], 1200)
        self.assertFalse(view["coverage_complete"])
        self.assertEqual(view["unknown"][0]["reason"], cov.REASON_NO_MEASUREMENT)
        self.assertEqual(view["processes"][0]["online_seconds_source"], cov.SOURCE_UNKNOWN)

    def test_the_threshold_labels_never_mint_a_verification(self):
        complete = {"covered_seconds": 25 * 3600, "coverage_complete": True, "continuous": True}
        holed = {"covered_seconds": 25 * 3600, "coverage_complete": False, "continuous": False}
        interrupted = {"covered_seconds": 25 * 3600, "coverage_complete": True,
                       "continuous": False}

        reached = cov.soak_labels(complete)
        self.assertEqual(reached["soak_24h"], "SOAK_THRESHOLD_REACHED_24H")
        self.assertEqual(reached["soak_7d"], "SOAK_RUNNING_7D")
        self.assertEqual(reached["soak_verified"], cov.SOAK_VERIFIED)
        self.assertNotIn("VERIFIED_24H", reached["soak_24h"])

        # A threshold crossed with a hole in the coverage is not a day of life, and neither is
        # 24 measured hours broken by a stop.
        self.assertEqual(cov.soak_labels(holed)["soak_24h"], "SOAK_RUNNING_24H")
        self.assertEqual(cov.soak_labels(interrupted)["soak_24h"], "SOAK_RUNNING_24H")

    def test_a_legacy_summary_span_is_unknown_not_coverage(self):
        legacy = {"kind": "autonomous_life_resident", "observed_seconds": 90000, "starts": 3}

        self.assertEqual(cov.legacy_summary_seconds(legacy), 90000)
        self.assertEqual(cov.legacy_summary_seconds({"coverage": {"covered_seconds": 5}}), 0)

        view = cov.coverage([], now=_at(90000), legacy_seconds=90000)

        self.assertEqual(view["covered_seconds"], 0)
        self.assertEqual(view["unknown_seconds"], 90000)
        self.assertEqual(view["wall_span_seconds"], 0)
        self.assertFalse(view["coverage_complete"])
        self.assertEqual(view["sources"]["ledger"], "ABSENT")
        self.assertEqual(view["sources"]["legacy_summary"], "PRESENT_UNPROVEN")
        self.assertEqual(view["unknown"][0]["reason"], cov.REASON_LEGACY)


    def test_an_overlap_is_named_time_ambiguity_and_never_continuity(self):
        # Appended in real start order: 01:00-02:00, then a record claiming 00:30-01:30.
        later = cov.new_process_record(process_id="host:100:%s" % _at(3600), pid=100,
                                       started_at=_at(3600), monotonic_started=10.0)
        later = cov.close_process_record(later, closed_at=_at(2 * 3600), online_seconds=3600)
        earlier = cov.new_process_record(process_id="host:200:%s" % _at(1800), pid=200,
                                         started_at=_at(1800), monotonic_started=3.0)
        earlier = cov.close_process_record(earlier, closed_at=_at(5400), online_seconds=3600)

        view = cov.coverage([later, earlier], now=_at(4 * 3600))

        # Both measurements stand: 3600 + 3600.  A clock disagreement never shrinks real
        # monotonic time, and it is never silently turned into downtime either.
        self.assertEqual(view["covered_seconds"], 7200)
        self.assertEqual(view["wall_span_seconds"], 1800)
        self.assertEqual(view["offline_gap_seconds"], 0)
        # The negative gap is named, with its magnitude, as ambiguity.
        self.assertEqual(view["overlap_seconds"], 5400)
        self.assertEqual([gap["kind"] for gap in view["gaps"]], ["OVERLAP"])
        self.assertTrue(view["gaps"][0]["ambiguous"])
        self.assertEqual(view["gaps"][0]["seconds"], 5400)
        # The span itself is positive, so the rollback flag stays accurate at False; what makes
        # this unusable as continuity evidence is the ambiguity, and coverage above the span.
        self.assertFalse(view["wall_rollback"])
        self.assertTrue(view["covered_exceeds_wall_span"])
        self.assertTrue(view["time_ambiguity"])
        self.assertFalse(view["continuous"])
        # Every interval still has an account (both rows closed and measured), which is exactly
        # why completeness and continuity are reported as two different answers.
        self.assertTrue(view["coverage_complete"])
        self.assertEqual(cov.soak_labels(view)["soak_24h"], "SOAK_RUNNING_24H")

    def test_an_unmeasured_unclosed_row_is_not_counted_twice(self):
        silent = cov.new_process_record(process_id="host:100:%s" % _at(0), pid=100,
                                        started_at=_at(0))
        silent = cov.heartbeat(silent, at=_at(1200), online_seconds=None)
        after = _process(200, 3600, 4200, 600)

        view = cov.coverage([silent, after], now=_at(4 * 3600))

        # One hole of 3600s, from the silent row's start to the next start, named by two reasons
        # that cover two *different* parts of it -- never 1200 + 3600.
        self.assertEqual(view["unknown_seconds"], 3600)
        self.assertEqual([entry["reason"] for entry in view["unknown"]],
                         [cov.REASON_NO_MEASUREMENT, cov.REASON_UNCLOSED])
        self.assertEqual([entry["seconds"] for entry in view["unknown"]], [1200, 2400])
        self.assertEqual(sum(entry["seconds"] for entry in view["unknown"]), 3600)
        self.assertEqual(view["covered_seconds"], 600)
        # The hole is unknown time, not downtime and not an overlap.
        self.assertEqual(view["gaps"], [])
        self.assertEqual(view["offline_gap_seconds"], 0)
        self.assertEqual(view["overlap_seconds"], 0)
        self.assertFalse(view["time_ambiguity"])
        self.assertFalse(view["coverage_complete"])
        self.assertFalse(view["continuous"])


class ResidentRecordTest(unittest.TestCase):
    """The record itself: what the summary says, and what a second process can prove."""

    def test_an_old_summary_cannot_claim_a_day(self):
        state_root = _state_root()
        (state_root / "resident_summary.json").write_text(json.dumps({
            "kind": "autonomous_life_resident", "started_at": _at(0), "starts": 3,
            "ticks": 96, "observed_seconds": 90000, "soak_24h": "SOAK_THRESHOLD_REACHED_24H",
        }), encoding="utf-8")

        loop = _resident(state_root, FakeClock(wall=BASE + timedelta(seconds=90000)))
        _without_journey_module(loop)
        record = loop.summary()

        # The old key is still there, but it is no longer the old wall span.
        self.assertEqual(record["observed_seconds"], 0)
        self.assertEqual(record["covered_seconds"], 0)
        self.assertEqual(record["unknown_seconds"], 90000)
        self.assertEqual(record["soak_24h"], "SOAK_RUNNING_24H")
        self.assertEqual(record["soak_basis"], cov.SOAK_BASIS)
        self.assertEqual(record["soak_verified"], cov.SOAK_VERIFIED)
        self.assertFalse(record["coverage_complete"])
        self.assertEqual(record["coverage"]["sources"]["legacy_summary"], "PRESENT_UNPROVEN")

    def test_the_summary_reports_proven_coverage_not_the_wall_span(self):
        clock = FakeClock()
        state_root = _state_root()

        first = _resident(state_root, clock)
        first._open_process_record()
        clock.advance(100)
        first._update_process_record(close=True)
        first.process_open = False

        clock.advance(5000)
        second = _resident(state_root, clock)
        _without_journey_module(second)
        second._open_process_record()
        clock.advance(60)

        record = second.summary()
        self.assertEqual(record["covered_seconds"], 160)
        self.assertEqual(record["observed_seconds"], 160)
        self.assertEqual(record["observed_seconds_basis"], "PROVEN_PROCESS_ONLINE_SECONDS")
        self.assertEqual(record["wall_span_seconds"], 5160)
        self.assertEqual(record["offline_gap_seconds"], 5000)
        self.assertEqual(record["unknown_seconds"], 0)
        # Still running: the interval is measured, but the coverage is not closed yet.
        self.assertFalse(record["coverage_complete"])
        self.assertTrue(record["process_open"])
        self.assertEqual(record["soak_24h"], "SOAK_RUNNING_24H")

        second._update_process_record(close=True)
        second.process_open = False
        closed = second.summary()
        self.assertTrue(closed["coverage_complete"])
        self.assertFalse(closed["continuous"])
        self.assertEqual(closed["covered_seconds"], 160)

    def test_a_restart_recovers_the_first_process_identity_and_leaves_it_closed(self):
        clock = FakeClock()
        state_root = _state_root()

        first = _resident(state_root, clock)
        first._open_process_record()
        clock.advance(100)
        first._update_process_record(close=True)
        first.process_open = False
        first_id = first.process_id
        first_start = first.started_at

        second = _resident(state_root, clock)
        rows = second._coverage_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["process_id"], first_id)
        self.assertEqual(rows[0]["closed"], cov.CLOSED_STOP)
        self.assertEqual(rows[0]["online_seconds"], 100)
        self.assertEqual(second._first_recorded_start(), first_start)

        second._open_process_record()
        rows = second._coverage_rows()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["process_id"], rows[1]["process_id"])
        # The finished process is evidence, not something the restart rewrites.
        self.assertEqual(rows[0]["closed"], cov.CLOSED_STOP)
        self.assertEqual(rows[0]["online_seconds"], 100)
        self.assertEqual(rows[1]["closed"], cov.CLOSED_UNCLOSED)
        self.assertEqual(rows[1]["process_id"], second.process_id)

    def test_the_run_stops_at_the_wall_deadline_instead_of_sleeping_a_full_poll(self):
        clock = FakeClock()
        loop = _resident(_state_root(), clock, poll_seconds=180)

        result = loop.run(duration_seconds=60)

        # One 60-second wait, clipped to what was left, not a 180-second poll.
        self.assertLessEqual(sum(clock.sleeps), 60)
        self.assertLessEqual(len(clock.sleeps), 60)
        self.assertEqual(result["wall_seconds"], 60)
        self.assertEqual(result["deadline_seconds"], 60)
        self.assertEqual(result["process_online_seconds"], 60)
        self.assertFalse(result["stopped"])

    def test_an_acted_wake_without_an_owner_receipt_is_not_a_confirmed_version(self):
        loop = _resident(_state_root(), FakeClock())
        bare = {"kind": "CHECK_WINDOW", "status": "ACTED", "status_class": resident.CLASS_ACTED,
                "outcome": "ACTED", "extra": {"budget": {"state": "OK"}}}

        loop._count(bare)
        self.assertEqual(loop.counters["actions"], 1)
        self.assertEqual(loop.counters["owner_confirmed_versions"], 0)
        self.assertEqual(loop.counters["owner_version_unknown"], 1)
        self.assertEqual(loop.counters["owner_version_refs"], {})

        named = dict(bare, extra={"owner_version_created": True, "step_path": "steps/3"})
        loop._count(named)
        self.assertEqual(loop.counters["actions"], 2)
        self.assertEqual(loop.counters["owner_confirmed_versions"], 1)
        self.assertEqual(loop.counters["owner_version_unknown"], 1)
        self.assertEqual(loop.counters["owner_version_refs"], {"steps/3": 1})

    def test_the_model_request_total_includes_the_reading_response_surface(self):
        loop = _resident(_state_root(), FakeClock())
        cycle = SimpleNamespace(writer=SimpleNamespace(call_count=2),
                                reading_response_writer=SimpleNamespace(call_count=1),
                                budget=SimpleNamespace(requests=None))
        loop.assembled = {"cycle": cycle}
        loop.counters["model_calls"] = 4

        without_ledger = loop._model_calls_detail()
        self.assertEqual(without_ledger["content_generation_calls"], 3)
        self.assertEqual(without_ledger["reading_response_calls"], 1)
        self.assertEqual(without_ledger["total_requests"], 7)
        self.assertEqual(without_ledger["total_source"], "PROCESS_COUNTERS_ONLY")
        self.assertTrue(without_ledger["reading_response_counted"])

        ledger_view = {"state": "OK", "utc_day_calls": 11, "utc_day_max": 40}
        cycle.budget = SimpleNamespace(requests=SimpleNamespace(view=lambda: ledger_view))
        with_ledger = loop._model_calls_detail()
        self.assertEqual(with_ledger["total_requests"], 11)
        self.assertEqual(with_ledger["total_source"], "PERSISTED_REQUEST_LEDGER")

        # An unreadable ledger is unknown, never a cheap day of zero requests.
        cycle.budget = SimpleNamespace(requests=SimpleNamespace(
            view=lambda: {"state": "corrupt", "utc_day_calls": None, "reason_code": "CORRUPT"}))
        unreadable = loop._model_calls_detail()
        self.assertIsNone(unreadable["total_requests"])
        self.assertEqual(unreadable["total_source"], "LEDGER_UNREADABLE")

        # A cycle with no reading-response surface reports unknown, not 0.
        cycle.budget = SimpleNamespace(requests=None)
        cycle.reading_response_writer = None
        no_surface = loop._model_calls_detail()
        self.assertIsNone(no_surface["reading_response_calls"])
        self.assertFalse(no_surface["reading_response_counted"])


if __name__ == "__main__":
    unittest.main()
