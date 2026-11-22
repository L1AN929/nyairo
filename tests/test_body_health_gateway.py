#!/usr/bin/env python3
"""v0.8 gateway test: ``get_body_health`` on the real GatewaySurface.

The runtime state is produced by the real ``body_runtime`` owner, and the
surface under test is the real ``GatewaySurface``; only the *service object*
is a shim, exactly as in ``test_body_capacity_source``.

Run:  python3 tests/test_body_health_gateway.py
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"

sys.path[:0] = [str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE)]

import body_runtime as br  # noqa: E402
from world_body_gateway_api import GATEWAY_READ_COMMANDS, GatewaySurface  # noqa: E402
from world_body_service import WorldBodyService  # noqa: E402


def service_shim(state, moment):
    shim = NS(body_state=state, _now=moment, _clock=None)
    shim._moment = types.MethodType(WorldBodyService._moment, shim)
    shim.body_summary = types.MethodType(WorldBodyService.body_summary, shim)
    return shim


def loaded_state(hours: int = 4):
    state = br.initial_runtime(at="2026-11-15T00:00:00+00:00")
    moment = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc)
    for _ in range(hours):
        report = br.advance(
            state,
            moment.isoformat(),
            (moment + timedelta(hours=1)).isoformat(),
            {
                "class": "PHYSICAL_RESULT",
                "movement_duration_seconds": 3600,
                "effort": "heavy",
                "pose": "standing",
            },
        )
        state = report["state"]
        moment += timedelta(hours=1)
    return state, moment


class GatewayHealthTest(unittest.TestCase):
    def test_command_is_read_only_and_registered(self):
        self.assertIn("get_body_health", GATEWAY_READ_COMMANDS)
        self.assertNotIn("get_body_health", ("submit_action", "submit_local_owner_action"))

    def test_no_body_state_is_unattested(self):
        surface = GatewaySurface(service_shim(None, datetime(2026, 11, 15, tzinfo=timezone.utc)))
        payload = surface.handle("get_body_health")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["result"]["available"])
        self.assertEqual(payload["result"]["health"]["status"], "UNATTESTED")
        self.assertEqual(payload["result"]["capacity"]["state"], "UNKNOWN")
        self.assertEqual(payload["result"]["writes_performed"], 0)

    def test_loaded_body_reports_status_and_envelope(self):
        state, moment = loaded_state()
        surface = GatewaySurface(service_shim(state, moment))
        payload = surface.handle("get_body_health")
        self.assertTrue(payload["ok"])
        result = payload["result"]
        self.assertTrue(result["available"])
        self.assertIn(result["health"]["status"], {"HEALTHY", "STRAINED"})
        capacity = result["capacity"]
        self.assertIn(capacity["state"], {"LIMITED"})
        self.assertGreaterEqual(capacity["candidate_cap"], 8)
        self.assertLessEqual(capacity["candidate_cap"], 12)
        self.assertGreaterEqual(capacity["latency_floor_ms"], 400)
        self.assertLessEqual(capacity["latency_floor_ms"], 980)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["writes_performed"], 0)

    def test_persisted_course_is_reported(self):
        import body_health_projection as bhp

        state, moment = loaded_state()
        record = bhp.initial_record(
            body_id="chiyo_body", subject_id="self", home_ref="", at=moment
        )
        record = bhp.admit_exposure(
            record, episode_id="ep-1", at=moment, origin="world_exposure", severity=0.6
        )
        shim = service_shim(state, moment)
        shim.health_state = record
        payload = GatewaySurface(shim).handle("get_body_health")["result"]
        self.assertEqual(payload["health"]["status"], "ACUTE_ILLNESS")
        self.assertTrue(payload["health"]["episode"]["open"])
        self.assertIn(payload["health"]["episode"]["severity_band"], {"mild", "noticeable", "strong"})
        self.assertTrue(payload["capacity"]["creative_block"])
        self.assertLessEqual(payload["capacity"]["candidate_cap"], 6)

    def test_service_advance_moves_and_persists_the_course(self):
        import tempfile

        import body_health_projection as bhp

        state, moment = loaded_state()
        with tempfile.TemporaryDirectory() as tmp:
            store = bhp.HealthStore(tmp)
            record = bhp.admit_exposure(
                bhp.initial_record(body_id="chiyo_body", subject_id="self",
                                   home_ref="", at=moment),
                episode_id="ep-1", at=moment, origin="world_exposure", severity=0.6,
            )
            store.save(record)
            shim = NS(
                body_state={"recovery": {"state": "active", "mode": "sleep_like"}},
                health_state=record,
                health_store=store,
                _now=moment,
                _clock=None,
            )
            shim._moment = types.MethodType(WorldBodyService._moment, shim)
            shim._advance_health_record = types.MethodType(
                WorldBodyService._advance_health_record, shim
            )
            before = record.episode.severity
            shim._advance_health_record(hours=8.0)
            self.assertLess(shim.health_state.episode.severity, before)
            on_disk = store.load()
            self.assertIsNotNone(on_disk.episode)
            self.assertAlmostEqual(on_disk.episode.severity, shim.health_state.episode.severity)

    def test_signals_carry_the_health_gate(self):
        state, moment = loaded_state()
        surface = GatewaySurface(service_shim(state, moment))
        payload = surface.handle("get_body_signals")["result"]
        self.assertIn("health", payload)
        self.assertIn(payload["health"]["status"], {"HEALTHY", "STRAINED"})
        self.assertEqual(payload["health"]["capacity_state"], "LIMITED")

    def test_response_carries_no_raw_somatic_floats(self):
        state, moment = loaded_state()
        surface = GatewaySurface(service_shim(state, moment))
        text = repr(surface.handle("get_body_health")["result"])
        self.assertNotIn("somatic", text)
        self.assertNotIn("fatigue", text)


if __name__ == "__main__":
    unittest.main()
