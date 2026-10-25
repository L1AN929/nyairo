#!/usr/bin/env python3
"""``scripts/usable_day.py`` 的本机入口正负控（离线、零产品调用）。

覆盖的是启动器自己的守卫，不是生活语义：

* 没有本监督记录时，``stop`` 不去停任何进程；
* 记录和实例不匹配、监督进程已不在时，``stop`` 具名拒绝；
* ``window`` 的越界参数在启动前就被拒绝；
* ``view`` 对不存在的实例也不崩，并且明确 ``model_called=false``。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
                str(ROOT / "components" / "life" / "scripts")]

import usable_day  # noqa: E402


def run_cli(*argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = usable_day.main(list(argv))
    text = out.getvalue().strip()
    payload = {}
    if text:
        try:
            payload = json.loads(text.splitlines()[-1])
        except ValueError:
            payload = {"raw": text}
    return code, payload


class UsableDayCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="usable-day-")) / "phone"
        self.root.mkdir(parents=True)

    def test_usage_errors(self):
        self.assertEqual(usable_day.main([]), usable_day.EXIT_USAGE)
        self.assertEqual(usable_day.main(["not-a-command"]), usable_day.EXIT_USAGE)

    def test_pid_probe_is_honest(self):
        self.assertTrue(usable_day._pid_alive(os.getpid()))
        self.assertFalse(usable_day._pid_alive(99999999))
        self.assertFalse(usable_day._pid_alive(None))
        self.assertFalse(usable_day._pid_alive("not-a-pid"))

    def test_stop_refuses_when_it_has_no_supervised_child(self):
        code, payload = run_cli("stop", "--root", str(self.root))
        self.assertEqual(code, usable_day.EXIT_REFUSED)
        self.assertEqual(payload.get("reason"), "NO_SUPERVISED_WINDOW")
        self.assertFalse(payload.get("ok"))

    def test_stop_refuses_a_record_for_another_root(self):
        from components.phone import living

        living._atomic_write(living.supervisor_path(self.root), {
            "pid": os.getpid(), "root": "/somewhere/else",
            "child": {"pid": os.getpid(), "alive": True}})
        code, payload = run_cli("stop", "--root", str(self.root))
        self.assertEqual(code, usable_day.EXIT_REFUSED)
        self.assertEqual(payload.get("reason"), "SUPERVISOR_RECORD_ROOT_MISMATCH")

    def test_stop_refuses_when_the_supervisor_is_gone(self):
        from components.phone import living

        living._atomic_write(living.supervisor_path(self.root), {
            "pid": 99999999, "root": str(self.root),
            "child": {"pid": 99999998, "alive": True}})
        code, payload = run_cli("stop", "--root", str(self.root))
        self.assertEqual(code, usable_day.EXIT_REFUSED)
        self.assertEqual(payload.get("reason"), "SUPERVISOR_PROCESS_GONE")

    def test_window_refuses_out_of_range_before_starting(self):
        for argv, reason in (
            (("window", "--root", str(self.root), "--duration-seconds", "601"), "DURATION_OUT_OF_RANGE"),
            (("window", "--root", str(self.root), "--max-new-requests", "5"), "REQUEST_CAP_OUT_OF_RANGE"),
        ):
            code, payload = run_cli(*argv)
            self.assertEqual(code, usable_day.EXIT_REFUSED)
            self.assertEqual(payload.get("reason"), reason)
            self.assertFalse(payload.get("model_called"))

    def test_view_of_a_missing_instance_is_read_only_and_does_not_crash(self):
        code, payload = run_cli("view", "--root", str(self.root / "missing"))
        self.assertEqual(code, usable_day.EXIT_OK)
        self.assertTrue(payload.get("read_only"))
        self.assertFalse(payload.get("model_called"))
        self.assertEqual(payload.get("services_started"), 0)
        self.assertEqual(payload.get("schema"), "phone.living.v1")

    def test_pause_without_a_nested_life_instance_is_refused(self):
        code, payload = run_cli("pause", "--root", str(self.root))
        self.assertEqual(code, usable_day.EXIT_REFUSED)
        self.assertEqual(payload.get("reason"), "NESTED_LIFE_INSTANCE_MISSING")


class ServeFailureReasonTest(unittest.TestCase):
    """A failed ``serve`` must say *why*, without echoing paths or command lines."""

    def test_safe_reason_codes_keeps_tokens_and_drops_paths(self):
        from components.phone import demo

        codes = demo.safe_reason_codes(
            "CHILD_SERVICE_TIMEOUT:supply:/root/nyairo-day-20261022/logs/supply.log")
        self.assertEqual(codes, ["CHILD_SERVICE_TIMEOUT"])
        codes = demo.safe_reason_codes(
            "CHILD_EXITED:supply:1:WRITE_FENCE_HELD:Governance")
        self.assertEqual(codes, ["CHILD_EXITED", "WRITE_FENCE_HELD"])

    def test_cli_serve_failure_preserves_the_owner_reason(self):
        from unittest import mock

        from components.phone import demo

        root = Path(tempfile.mkdtemp(prefix="usable-day-serve-")) / "phone"
        root.mkdir(parents=True)
        with mock.patch.object(
                demo, "serve_instance",
                side_effect=demo.PhoneDemoError(
                    "CHILD_EXITED:supply:1:WRITE_FENCE_HELD:Governance")):
            code, payload = run_cli("serve", "--root", str(root))
        self.assertEqual(code, usable_day.EXIT_REFUSED)
        self.assertEqual(payload.get("reason_code"), "USABLE_SERVE_FAILED")
        self.assertIn("WRITE_FENCE_HELD", payload.get("serve_reasons") or [])
        self.assertNotIn("/", json.dumps(payload))
        self.assertNotIn("Governance", json.dumps(payload.get("serve_reasons")))


@unittest.skipUnless(sys.platform.startswith("linux"), "proves ownership via /proc")
class PreviousGenerationTest(unittest.TestCase):
    """Only the marker's own, argv-verified children may ever be signalled."""

    def setUp(self) -> None:
        from components.phone import demo

        self.demo = demo
        self.root = Path(tempfile.mkdtemp(prefix="usable-day-reclaim-"))
        (self.root / "run").mkdir(parents=True)
        self.children: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()

    def _marker(self, **overrides) -> None:
        payload = {"instance": str(self.root), "pid": 99999999,
                   "supply_socket": str(self.root / "run" / "supply-gen.sock"),
                   "children": {}}
        payload.update(overrides)
        (self.root / "run" / "phone.serve.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def _spawn(self, *tail: str) -> subprocess.Popen:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", *tail],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def test_reclaims_only_the_recorded_child_after_argv_proof(self):
        socket_path = str(self.root / "run" / "supply-gen.sock")
        child = self._spawn("owner-supply", "--socket", socket_path)
        self._marker(children={"supply": child.pid})
        reclaimed = self.demo._reclaim_previous_generation(
            self.root, {"world_home": str(self.root / "world-home")})
        self.assertEqual([row["child"] for row in reclaimed], ["supply"])
        self.assertIsNotNone(child.poll())

    def test_refuses_an_unverified_child_without_signalling_it(self):
        child = self._spawn("something-else", "--socket", "/tmp/other.sock")
        self._marker(children={"supply": child.pid})
        with self.assertRaises(self.demo.PhoneDemoError) as caught:
            self.demo._reclaim_previous_generation(
                self.root, {"world_home": str(self.root / "world-home")})
        self.assertIn("PREVIOUS_CHILD_OWNERSHIP_UNVERIFIED", str(caught.exception))
        self.assertIsNone(child.poll())

    def test_refuses_while_a_previous_serve_is_still_recorded_alive(self):
        child = self._spawn("usable_day.py", "serve", "--root", str(self.root))
        self._marker(pid=child.pid, children={})
        with self.assertRaises(self.demo.PhoneDemoError) as caught:
            self.demo._reclaim_previous_generation(
                self.root, {"world_home": str(self.root / "world-home")})
        self.assertIn("PREVIOUS_SERVE_STILL_RUNNING", str(caught.exception))
        self.assertIsNone(child.poll())

    def test_child_that_exits_says_why_without_waiting_the_ready_timeout(self):
        log_path = self.root / "logs" / "supply.log"
        child = self.demo.ChildService(
            "supply",
            [sys.executable, "-c",
             "import sys; sys.stderr.write('life-supply startup error: FenceHeld: "
             "WRITE_FENCE_HELD:Governance\\n'); sys.exit(1)"],
            log_path, {})
        started = time.monotonic()
        child.start()
        try:
            with self.assertRaises(self.demo.PhoneDemoError) as caught:
                child.wait_ready(self.root / "run" / "missing.sock",
                                 timeout=60, probe="supply")
        finally:
            child.stop()
        elapsed = time.monotonic() - started
        message = str(caught.exception)
        self.assertIn("CHILD_EXITED", message)
        self.assertIn("WRITE_FENCE_HELD", message)
        self.assertNotIn(str(self.root), message)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
