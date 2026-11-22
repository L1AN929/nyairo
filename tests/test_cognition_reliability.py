"""Runs the fixture-transport cognition reliability suite against the exported code.

The suite exercises crash windows, unknown send states, late results, budget accounting and
journal retention, using an in-process fixture transport: no socket is opened, no credential is
read and no paid call is made.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parent / "cognition_reliability_suite.py"
_CACHED_RESULT: tuple[str, int] | None = None


def _run() -> tuple[str, int]:
    global _CACHED_RESULT
    if _CACHED_RESULT is None:
        proc = subprocess.run(
            [sys.executable, "-B", str(SUITE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _CACHED_RESULT = ((proc.stdout or "") + (proc.stderr or ""), proc.returncode)
    return _CACHED_RESULT


def test_cognition_reliability_suite_reports_no_failed_checks():
    out, rc = _run()
    marker = "================ SUMMARY ================"
    assert marker in out, out[-4000:]
    tail = out.split(marker)[-1]
    payload = json.loads(tail[tail.index("{"):tail.rindex("}") + 1])
    assert payload["checks"]["failed"] == 0, out[-6000:]
    assert payload["provider_calls_made"] == 0
    assert payload["gates"]["DUPLICATE_PAID_CALL"] == 0
    assert payload["gates"]["UNKNOWN_SEND_STATE_AUTO_RETRY"] == 0
    assert payload["gates"]["LATE_RESULT_PROMOTION"] == 0
    assert payload["gates"]["canonical_activity_revision"] == 0
    assert rc == 0


class CognitionReliabilitySuiteTest(unittest.TestCase):
    def test_suite_reports_no_failed_checks(self) -> None:
        test_cognition_reliability_suite_reports_no_failed_checks()


if __name__ == "__main__":
    unittest.main()

