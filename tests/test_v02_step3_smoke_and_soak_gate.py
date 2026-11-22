#!/usr/bin/env python3
"""Offline unit tests for Step 3 smoke & real 24h wall-clock soak harness (llm_calls == 0)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "scripts", REPO_ROOT / "components" / "life" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import v02_real_llm_smoke as smoke_mod
import v02_wall_clock_soak as soak_mod


class TestV02Step3SmokeAndSoakGate(unittest.TestCase):
    def test_01_real_llm_smoke_dry_run_is_zero_llm_and_sanitizes_secrets(self) -> None:
        doc = smoke_mod.run_dry_check()
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["llm_calls"], 0)
        self.assertEqual(doc["invariants"]["life_update_surfaces"], 37)
        self.assertEqual(doc["invariants"]["max_lived_context_chars"], 1800)

        dirty = {
            "model": "demo",
            "api_key": "sk-secret-must-strip",
            "api_key_present": True,
            "nested": {"authorization": "Bearer abc", "total_tokens": 42, "csrf_token": "hide"},
        }
        clean = smoke_mod.sanitize_evidence(dirty)
        self.assertNotIn("api_key", clean)
        self.assertTrue(clean["api_key_present"])
        self.assertNotIn("authorization", clean["nested"])
        self.assertNotIn("csrf_token", clean["nested"])
        self.assertEqual(clean["nested"]["total_tokens"], 42)

    def test_02_wall_clock_soak_gate_rejects_simulated_and_short_and_unhealthy_runs(self) -> None:
        sim_verdict = soak_mod.evaluate_soak_evidence(
            {
                "continuous_wall_hours": 72.0,
                "simulated": True,
                "evidence_tier": "SIMULATED_90D",
                "samples_total": 100,
                "failures_total": 0,
                "aux_file_leaks_total": 0,
                "non_200_total": 0,
            }
        )
        self.assertFalse(sim_verdict["passed"])
        self.assertEqual(sim_verdict["reason_code"], "SIMULATED_EVIDENCE_CANNOT_SATISFY_WALL_CLOCK_GATE")
        self.assertEqual(sim_verdict["llm_calls"], 0)

        short_verdict = soak_mod.evaluate_soak_evidence(
            {
                "continuous_wall_hours": 1.25,
                "simulated": False,
                "evidence_tier": "WALL_CLOCK_IN_PROGRESS",
                "samples_total": 8,
                "failures_total": 0,
                "aux_file_leaks_total": 0,
                "non_200_total": 0,
            }
        )
        self.assertFalse(short_verdict["passed"])
        self.assertEqual(short_verdict["reason_code"], "INSUFFICIENT_WALL_CLOCK_HOURS")

        unhealthy_verdict = soak_mod.evaluate_soak_evidence(
            {
                "continuous_wall_hours": 24.5,
                "simulated": False,
                "evidence_tier": "WALL_CLOCK_VERIFIED",
                "samples_total": 150,
                "failures_total": 1,
                "aux_file_leaks_total": 0,
                "non_200_total": 1,
            }
        )
        self.assertFalse(unhealthy_verdict["passed"])
        self.assertEqual(unhealthy_verdict["reason_code"], "SOAK_HEALTH_FAILURES_DETECTED")

        ok_verdict = soak_mod.evaluate_soak_evidence(
            {
                "continuous_wall_hours": 24.05,
                "simulated": False,
                "evidence_tier": "WALL_CLOCK_VERIFIED",
                "samples_total": 146,
                "failures_total": 0,
                "aux_file_leaks_total": 0,
                "non_200_total": 0,
                "restart_continuity_verified": True,
            }
        )
        self.assertTrue(ok_verdict["passed"])
        self.assertEqual(ok_verdict["reason_code"], "WALL_CLOCK_SOAK_VERIFIED")
        self.assertTrue(ok_verdict["restart_continuity_verified"])

    def test_03_wall_clock_soak_cli_verify_reads_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(soak_mod.main(["verify", "--root", str(root)]), 1)
            (root / "soak_evidence.json").write_text(
                json.dumps(
                    {
                        "continuous_wall_hours": 0.1,
                        "simulated": False,
                        "evidence_tier": "WALL_CLOCK_IN_PROGRESS",
                        "samples_total": 2,
                        "failures_total": 0,
                        "aux_file_leaks_total": 0,
                        "non_200_total": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(soak_mod.main(["verify", "--root", str(root)]), 1)


if __name__ == "__main__":
    unittest.main()
