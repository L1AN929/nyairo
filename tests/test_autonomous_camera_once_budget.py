#!/usr/bin/env python3
"""Offline guard for the one-shot autonomous-camera budget: reserve before every send.

No model, no network, no services: a stub budget and a stub send stand in for Hermes.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load():
    path = _REPO / "scripts" / "autonomous_camera_once.py"
    spec = importlib.util.spec_from_file_location("autonomous_camera_once_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


autocam = _load()


class _StubBudget:
    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.calls = 0

    def reserve(self, kind: str):
        self.calls += 1
        if self.calls > self.cap:
            raise RuntimeError("BUDGET_EXHAUSTED")
        return {"reservation": {"kind": kind},
                "utc_day_calls": self.calls, "run_window_calls": self.calls}


class ReserveBeforeSendTest(unittest.TestCase):
    def test_every_of_three_sends_reserves_first_and_fourth_sends_nothing(self):
        budget = _StubBudget(3)
        events = []

        def send(index: int) -> None:
            events.append(("send", index))

        def reserve(record) -> None:
            events.append(("reserve", record["run_window_calls"]))

        for index in range(3):
            autocam._reserve_then_send(budget, reserve, lambda i=index: send(i))

        self.assertEqual(events, [
            ("reserve", 1), ("send", 0),
            ("reserve", 2), ("send", 1),
            ("reserve", 3), ("send", 2),
        ])

        with self.assertRaises(RuntimeError):
            autocam._reserve_then_send(budget, reserve, lambda: send(3))

        # The refused fourth attempt records no reservation callback and sends nothing.
        self.assertEqual(events, [
            ("reserve", 1), ("send", 0),
            ("reserve", 2), ("send", 1),
            ("reserve", 3), ("send", 2),
        ])
        self.assertEqual(budget.calls, 4)


class AttemptEntryGuardTest(unittest.TestCase):
    def test_completed_failed_run_with_started_false_still_refuses(self):
        # The real first run wrote a result while leaving ``started`` false.
        self.assertTrue(autocam._previous_attempt(
            {"started": False, "finished": True}, {"api_calls": 1}))
        self.assertTrue(autocam._previous_attempt(
            {"started": False}, {"api_calls": 1}))

    def test_fresh_state_without_result_is_allowed(self):
        self.assertFalse(autocam._previous_attempt(
            {"started": False, "finished": False}, None))

    def test_attempt_marker_is_written_before_any_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            original = autocam.STATE_PATH
            autocam.STATE_PATH = path
            try:
                path.write_text(json.dumps({
                    "schema": autocam.SCHEMA_STATE, "started": False,
                    "started_at": None, "finished": False}), encoding="utf-8")
                autocam._mark_attempt_started({"schema": autocam.SCHEMA_STATE,
                                               "started": False, "started_at": None,
                                               "finished": False})
                written = json.loads(path.read_text(encoding="utf-8"))
            finally:
                autocam.STATE_PATH = original
        self.assertTrue(written["started"])
        self.assertTrue(written["started_at"])
        self.assertTrue(written["attempt_entered_at"])


if __name__ == "__main__":
    unittest.main()
