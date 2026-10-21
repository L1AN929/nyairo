#!/usr/bin/env python3
"""v0.4 R: the 24h report reads the coverage ledger, and silence is never downtime.

What this module pins
---------------------
``one_day_life.build_report`` used to answer "was she up for a day?" from the resident
journal: first wake to last wake, minus every interval longer than ``poll * 2.5``, which it
called downtime.  Two things are wrong with that:

1. a long quiet stretch is not a stopped process -- a blocked loop, a slow owner call or a
   suspended host all leave a hole between two journal records while the process is up, and
   the arithmetic above reads every one of them as downtime;
2. a wall span is not a measurement -- two processes, one crash and one reboot are
   indistinguishable from it, so it can never prove a day.

The batch that added ``life_runtime_coverage`` (R, ``resident_coverage.json``) already
separates wall / covered / offline / unknown / ambiguity, and each process writes its own
``time.monotonic()``.  These tests hold the report to that ledger:

1. a long journal gap with a measured process is *not* downtime (``QUIET`` is not "stopped");
2. a journal with no ledger is reported as unverified and can never PASS;
3. a real stop and restart is measured offline time, not life;
4. a crash leaves an unknown tail and no passing day;
5. a running process is an open tail, not a closed day;
6. a legacy summary's wall span is unknown, and its old ``SOAK_THRESHOLD_REACHED_24H`` label
   cannot survive into the report;
7. two records that disagree about wall time are ambiguity, and their measurements are not
   reduced;
8. a reached threshold is not ``SOAK_VERIFIED`` (no field carries that label);
9. zero owner output and zero requests are neither "not alive" nor a chosen rest;
10. an unreachable model is a technical failure, never "she chose to rest";
11. an injected clock can place a record a year away -- the year adds no life, and
    ``soak_verified`` stays ``NOT_VERIFIED``;
12. an instance with no record at all is insufficient evidence, not a pass.

Everything is clock-injected: no process is started, no runtime is built, no provider is
called, no socket is opened and no test waits.  The fixture directories are kept (``mkdtemp``
without cleanup), because the ledger written into them is the evidence under test.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_runtime_coverage as cov  # noqa: E402
import one_day_life as odl  # noqa: E402

BASE = datetime(2026, 10, 27, 0, 0, 0, tzinfo=timezone.utc)
#: The report's own target: one real day.
TARGET = 24 * 3600


def _at(seconds: float) -> str:
    """One wall instant, ``seconds`` after the fixture's base, in the record's own format."""

    return (BASE + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _moment(seconds: float) -> datetime:
    return BASE + timedelta(seconds=float(seconds))


def _instance(prefix: str = "v04-life-report-") -> Path:
    """A prepared-looking instance directory.  Kept after the test: it holds the evidence."""

    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "instance.json").write_text(json.dumps({
        "schema_version": odl.SCHEMA_VERSION,
        "kind": "life.one_day_instance",
        "subject_id": "subject-fixture",
        # An empty socket path is refused by the port before any connection is attempted, so
        # the work view reports a read error and nothing is contacted.
        "supply_socket": "",
        "environment": {},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return root


def _state_root(root: Path) -> Path:
    state_root = odl.life_state_root(root)
    state_root.mkdir(parents=True, exist_ok=True)
    return state_root


def _write_journal(state_root: Path, instants: list[float]) -> None:
    """The resident's tick journal: one line per tick, at the instants given."""

    rows = [{"at": _at(second), "tick": index + 1, "events": [], "reconcile": [],
             "due": {"kind": "CHECK_WINDOW", "status": "QUIET", "status_class": "silence"}}
            for index, second in enumerate(instants)]
    (state_root / odl.resident_module.RESIDENT_JOURNAL_FILENAME).write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _write_summary(state_root: Path, *, running: bool = False, counters: dict | None = None,
                   poll_seconds: int = 1800, model: dict | None = None,
                   detail: dict | None = None, soak_label: str | None = None,
                   extra: dict | None = None) -> None:
    payload = {
        "kind": "autonomous_life_resident",
        "state_root": str(state_root),
        "running": running,
        "poll_seconds": poll_seconds,
        "starts": 1,
        "ticks": 1,
        "started_at": _at(0),
        "counters": dict(counters or {}),
    }
    if model is not None:
        payload["model"] = model
    if detail is not None:
        payload["model_calls_detail"] = detail
    if soak_label is not None:
        payload["soak_24h"] = soak_label
    payload.update(extra or {})
    (state_root / odl.resident_module.RESIDENT_SUMMARY_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _process(pid: int, started: float, stopped: float, online: int) -> dict:
    """One process that really stopped, written through the coverage module's own builders."""

    row = cov.new_process_record(process_id="host:%s:%s" % (pid, _at(started)), pid=pid,
                                 started_at=_at(started), monotonic_started=10.0)
    return cov.close_process_record(row, closed_at=_at(stopped), online_seconds=online)


def _open_process(pid: int, started: float, heartbeat: float, online: int | None) -> dict:
    """One process that never closed: the interval it measured was never handed over."""

    row = cov.new_process_record(process_id="host:%s:%s" % (pid, _at(started)), pid=pid,
                                 started_at=_at(started), monotonic_started=10.0)
    return cov.heartbeat(row, at=_at(heartbeat), online_seconds=online)


def _write_ledger(state_root: Path, rows: list[dict]) -> None:
    (state_root / odl.resident_module.RESIDENT_COVERAGE_FILENAME).write_text(
        json.dumps({"kind": cov.KIND, "schema_version": cov.SCHEMA_VERSION,
                    "state_root": str(state_root), "processes": rows},
                   ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def _report(root: Path, *, seconds: float, target: int = TARGET) -> dict:
    return odl.build_report(root, target_seconds=target, now=_moment(seconds))


def _strings(node) -> list:
    """Every string *value* in the report, so a label is judged on its own terms.

    A note that denies verification ("a reached threshold is not SOAK_VERIFIED") is not a
    label; only an actual value may never be ``SOAK_VERIFIED``.
    """

    found: list = []
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        for value in node.values():
            found.extend(_strings(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            found.extend(_strings(value))
    return found


class LedgerAuthorityTest(unittest.TestCase):
    """The ledger decides; the journal may not."""

    def test_a_long_journal_gap_is_not_downtime_when_the_ledger_measured_the_process(self):
        root = _instance()
        state_root = _state_root(root)
        # 12 hours between two ticks: a slow owner call or a blocked loop, while the process
        # itself measured 24 hours online (its own monotonic clock).
        _write_journal(state_root, [0, 12 * 3600])
        _write_ledger(state_root, [_process(100, 0, 24 * 3600, 24 * 3600)])
        _write_summary(state_root, counters={"actions": 2, "owner_confirmed_versions": 2})

        report = _report(root, seconds=24 * 3600)

        self.assertEqual(report["window"]["coverage_basis"], odl.COVERAGE_BASIS_LEDGER)
        self.assertEqual(report["window"]["coverage_seconds"], 24 * 3600)
        self.assertEqual(report["window"]["observed_seconds"], 24 * 3600)
        # The journal gap is still reported as a journal gap -- it is just not downtime and it
        # is not fed into the verdict.
        self.assertEqual(report["window"]["journal_span_seconds"], 12 * 3600)
        self.assertEqual(report["window"]["gaps"], [])
        self.assertEqual(report["window"]["downtime_seconds"], 0)
        self.assertEqual(report["window"]["downtime_seconds_basis"], "MEASURED_OFFLINE_GAP")
        self.assertEqual(report["window"]["unknown_seconds"], 0)
        self.assertEqual(report["verdict"], odl.VERDICT_PASS)

    def test_a_journal_without_a_ledger_cannot_pass_and_says_why(self):
        root = _instance()
        state_root = _state_root(root)
        _write_journal(state_root, [0, 12 * 3600])
        _write_summary(state_root, counters={"actions": 1, "owner_confirmed_versions": 1})

        report = _report(root, seconds=24 * 3600)

        self.assertEqual(report["window"]["coverage_basis"], odl.COVERAGE_BASIS_JOURNAL)
        self.assertEqual(report["verdict"], odl.VERDICT_NOT_PASS)
        self.assertIn("unverified", report["verdict_basis"])
        self.assertIn("cannot pass", report["verdict_basis"])
        # The old keys survive, with the basis named beside each of them.
        self.assertEqual(report["window"]["coverage_seconds"], 12 * 3600)
        self.assertEqual(report["window"]["downtime_seconds"], 12 * 3600)
        self.assertEqual(report["window"]["observed_seconds"], 0)
        self.assertEqual(report["window"]["observed_seconds_basis"],
                         "JOURNAL_WAKE_SPAN_UNVERIFIED")
        self.assertEqual(report["window"]["downtime_seconds_basis"],
                         "JOURNAL_WAKE_GAP_UNVERIFIED_NOT_DOWNTIME")
        self.assertEqual(len(report["window"]["gaps"]), 1)
        self.assertEqual(report["soak"]["soak_verified"], cov.SOAK_VERIFIED)

    def test_a_real_stop_and_start_is_measured_offline_time_not_life(self):
        root = _instance()
        state_root = _state_root(root)
        _write_ledger(state_root, [_process(100, 0, 12 * 3600, 12 * 3600),
                                   _process(200, 14 * 3600, 26 * 3600, 12 * 3600)])
        _write_summary(state_root, counters={"actions": 3, "owner_confirmed_versions": 3})

        report = _report(root, seconds=26 * 3600)
        window = report["window"]

        self.assertEqual(window["coverage_seconds"], 24 * 3600)
        self.assertEqual(window["downtime_seconds"], 2 * 3600)
        self.assertEqual([gap["kind"] for gap in window["gaps"]], ["OFFLINE"])
        self.assertEqual(window["closed_process_seconds"], 24 * 3600)
        self.assertEqual(window["open_tail_seconds"], 0)
        self.assertTrue(window["coverage_complete"])
        self.assertFalse(window["continuous"])
        self.assertEqual(report["verdict"], odl.VERDICT_INTERRUPTED)
        self.assertIn("offline", report["verdict_basis"])
        # 24 measured hours broken by a stop are not a 24h day.
        self.assertEqual(report["soak"]["soak_24h"], "SOAK_RUNNING_24H")
        self.assertEqual(report["soak"]["soak_verified"], cov.SOAK_VERIFIED)

    def test_a_crash_leaves_an_unknown_tail_and_no_passing_day(self):
        root = _instance()
        state_root = _state_root(root)
        _write_ledger(state_root, [_process(100, 0, 12 * 3600, 12 * 3600),
                                   _open_process(200, 14 * 3600, 15 * 3600, 3600)])
        _write_summary(state_root, counters={"actions": 2, "owner_confirmed_versions": 2})

        report = _report(root, seconds=24 * 3600)
        window = report["window"]

        # The crashed row measured one hour; from its last heartbeat to now is a hole.
        self.assertEqual(window["coverage_seconds"], 12 * 3600 + 3600)
        self.assertEqual(window["closed_process_seconds"], 12 * 3600)
        self.assertEqual(window["open_tail_seconds"], 3600)
        self.assertEqual(window["unknown_seconds"], 24 * 3600 - 15 * 3600)
        self.assertIn(cov.REASON_UNCLOSED, [entry["reason"] for entry in window["unknown"]])
        self.assertTrue(window["current_tail"]["open"])
        self.assertEqual(window["current_tail"]["unmeasured_seconds"], 24 * 3600 - 15 * 3600)
        self.assertFalse(window["coverage_complete"])
        self.assertEqual(report["verdict"], odl.VERDICT_NOT_PASS)
        self.assertIn("unknown", report["verdict_basis"])

    def test_a_running_process_is_reported_as_an_open_tail_not_a_closed_day(self):
        root = _instance()
        state_root = _state_root(root)
        _write_ledger(state_root, [_open_process(100, 0, 1800, 1800)])
        _write_summary(state_root, running=True,
                       counters={"actions": 0, "owner_confirmed_versions": 0})

        report = _report(root, seconds=3600)
        window = report["window"]

        self.assertEqual(report["verdict"], odl.VERDICT_RUNNING)
        self.assertEqual(window["closed_process_seconds"], 0)
        self.assertEqual(window["open_tail_seconds"], 1800)
        self.assertTrue(window["current_tail"]["open"])
        self.assertEqual(window["current_tail"]["unmeasured_after"], _at(1800))
        self.assertEqual(window["current_tail"]["unmeasured_seconds"], 1800)
        self.assertEqual(window["unknown_seconds"], 1800)
        self.assertFalse(window["coverage_complete"])
        self.assertEqual(window["soak_label"], "SOAK_RUNNING_24H")

    def test_a_legacy_summary_wall_span_is_unknown_and_cannot_pass(self):
        root = _instance()
        state_root = _state_root(root)
        # An old record: a wall span, and the false label the old wall arithmetic produced.
        (state_root / odl.resident_module.RESIDENT_SUMMARY_FILENAME).write_text(json.dumps({
            "kind": "autonomous_life_resident", "started_at": _at(0), "starts": 3,
            "ticks": 288, "observed_seconds": 90000,
            "soak_24h": "SOAK_THRESHOLD_REACHED_24H"}), encoding="utf-8")

        report = _report(root, seconds=90000)
        window = report["window"]

        self.assertEqual(window["coverage_basis"], odl.COVERAGE_BASIS_LEGACY)
        self.assertEqual(window["coverage_seconds"], 0)
        self.assertEqual(window["observed_seconds"], 0)
        self.assertEqual(window["unknown_seconds"], 90000)
        self.assertEqual(report["verdict"], odl.VERDICT_NOT_PASS)
        self.assertIn("unverified", report["verdict_basis"])
        # The old label never survives into the report.
        self.assertEqual(report["soak"]["soak_24h"], "SOAK_RUNNING_24H")
        self.assertNotEqual(window["soak_label"], "SOAK_THRESHOLD_REACHED_24H")

    def test_two_records_that_disagree_about_wall_time_are_ambiguity_not_a_day(self):
        root = _instance()
        state_root = _state_root(root)
        # Appended in real start order: 00:00-12:00, then a record claiming 06:00-18:00.
        _write_ledger(state_root, [_process(100, 0, 12 * 3600, 12 * 3600),
                                   _process(200, 6 * 3600, 18 * 3600, 12 * 3600)])
        _write_summary(state_root, counters={"actions": 4, "owner_confirmed_versions": 4})

        report = _report(root, seconds=18 * 3600)
        window = report["window"]

        # Both measurements stand: a clock disagreement never shrinks real monotonic time.
        self.assertEqual(window["coverage_seconds"], 24 * 3600)
        self.assertEqual(window["overlap_seconds"], 6 * 3600)
        self.assertEqual([gap["kind"] for gap in window["gaps"]], ["OVERLAP"])
        self.assertTrue(window["time_ambiguity"])
        self.assertTrue(window["covered_exceeds_wall_span"])
        self.assertFalse(window["continuous"])
        self.assertEqual(report["verdict"], odl.VERDICT_INTERRUPTED)
        self.assertIn("overlapping", report["verdict_basis"])
        self.assertEqual(report["soak"]["soak_24h"], "SOAK_RUNNING_24H")


class ThresholdAndQuietTest(unittest.TestCase):
    """A reached threshold is arithmetic; a quiet window is not a verdict."""

    def test_a_reached_threshold_is_not_soak_verified(self):
        root = _instance()
        state_root = _state_root(root)
        _write_ledger(state_root, [_process(100, 0, 25 * 3600, 25 * 3600)])
        _write_summary(state_root, counters={"actions": 1, "owner_confirmed_versions": 1})

        report = _report(root, seconds=25 * 3600)

        self.assertEqual(report["verdict"], odl.VERDICT_PASS)
        self.assertEqual(report["soak"]["soak_24h"], "SOAK_THRESHOLD_REACHED_24H")
        self.assertEqual(report["soak"]["soak_verified"], "NOT_VERIFIED")
        self.assertEqual(report["soak"]["soak_basis"], cov.SOAK_BASIS)
        self.assertIn("reviewer", report["soak"]["note"])
        # No field anywhere carries a verification label.
        self.assertNotIn("SOAK_VERIFIED", _strings(report))
        self.assertNotIn("SOAK_VERIFIED_24H", _strings(report))

    def test_zero_owner_output_and_zero_requests_are_not_read_as_dead_or_as_rest(self):
        root = _instance()
        state_root = _state_root(root)
        _write_ledger(state_root, [_process(100, 0, 24 * 3600, 24 * 3600)])
        _write_summary(state_root, counters={"actions": 0, "owner_confirmed_versions": 0,
                                             "no_action": 288, "model_calls": 0},
                       detail={"total_requests": 0, "total_source": "PERSISTED_REQUEST_LEDGER"})

        report = _report(root, seconds=24 * 3600)

        # A quiet day is still a measured day: the absence of output is not absence of life.
        self.assertEqual(report["verdict"], odl.VERDICT_PASS)
        quiet = report["quiet"]
        self.assertEqual(quiet["reading"], odl.QUIET_NO_OWNER_OUTPUT)
        self.assertEqual(quiet["requests"], 0)
        self.assertTrue(quiet["requests_known"])
        self.assertFalse(quiet["reads_as_selected_rest"])
        self.assertIn("zero-action", report["verdict_basis"])
        self.assertIn("not evidence that she was not alive", report["verdict_basis"])
        self.assertIn("not a recorded choice to rest", report["verdict_basis"])

    def test_an_unreachable_model_is_technical_failure_not_a_chosen_rest(self):
        root = _instance()
        state_root = _state_root(root)
        _write_ledger(state_root, [_process(100, 0, 3600, 3600)])
        _write_summary(state_root, counters={
            "actions": 0, "owner_confirmed_versions": 0,
            "blocked_reasons": {"BLOCKED: MODEL_CONNECTION": 5}},
            model={"probe": {"status": "UNREACHABLE", "host": "example.invalid"}})

        report = _report(root, seconds=3600)

        self.assertEqual(report["verdict"], odl.VERDICT_NOT_PASS)
        quiet = report["quiet"]
        self.assertEqual(quiet["reading"], odl.QUIET_MODEL_UNAVAILABLE)
        self.assertEqual(quiet["model_probe"], "UNREACHABLE")
        self.assertIn("BLOCKED: MODEL_CONNECTION", quiet["blocked_reasons"])
        self.assertIn("not her choosing to rest", quiet["reading_basis"])
        self.assertFalse(quiet["reads_as_selected_rest"])
        self.assertNotIn("chose to rest", report["verdict_basis"])

    def test_an_injected_clock_proves_nothing_about_a_real_day(self):
        root = _instance()
        state_root = _state_root(root)
        _write_ledger(state_root, [_process(100, 0, 1800, 1800)])
        _write_summary(state_root, counters={"actions": 0, "owner_confirmed_versions": 0})

        # A year after the record: the year adds no coverage, and the distance from the
        # record's last edge to now is reported as a distance, not as life.
        report = odl.build_report(root, target_seconds=TARGET, now=_moment(365 * 24 * 3600))
        window = report["window"]

        self.assertEqual(report["generated_at"], _at(365 * 24 * 3600))
        self.assertEqual(window["wall_span_seconds"], 1800)
        self.assertEqual(window["coverage_seconds"], 1800)
        self.assertEqual(window["current_tail"]["since_last_edge_seconds"],
                         365 * 24 * 3600 - 1800)
        self.assertFalse(window["current_tail"]["open"])
        self.assertEqual(report["verdict"], odl.VERDICT_NOT_PASS)
        self.assertEqual(report["soak"]["soak_verified"], "NOT_VERIFIED")
        self.assertIn("an injected clock proves nothing about a real run",
                      " ".join(report["not_claimed"]))

    def test_a_silent_instance_has_no_evidence(self):
        root = _instance()

        report = _report(root, seconds=0)

        self.assertEqual(report["verdict"], odl.VERDICT_NO_EVIDENCE)
        self.assertEqual(report["window"]["coverage_basis"], odl.COVERAGE_BASIS_ABSENT)
        self.assertEqual(report["window"]["coverage_seconds"], 0)
        self.assertEqual(report["window"]["unknown_seconds"], 0)
        self.assertTrue(report["read_only"])
        self.assertIn("nothing to judge", report["verdict_basis"])
        self.assertTrue(report["not_claimed"])


if __name__ == "__main__":
    unittest.main()
