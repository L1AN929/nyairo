"""v06_world_roundtrip_check 的少量驱动边界测试。

正式往返链只跑 ``check`` 单入口（``scripts/v06_world_roundtrip_check.py``），
这里不重复执行整链，只把驱动自己的边界断清楚：

* CLI 的离线/时限前置拒绝；
* 新根守卫与路径守卫；
* 离线网络守卫（AF_INET 拒绝，AF_UNIX 允许）；
* off/on 判据对合成 payload 的真断言；
* 父级只读审计确实按本子根的真实文件布局读。

运行（WSL）：``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v06_world_roundtrip_check``
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import v06_world_roundtrip_check as v06  # noqa: E402

posix_only = unittest.skipIf(os.name == "nt", "AF_UNIX / POSIX 路径")


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = v06.main(list(argv))
    return code, out.getvalue()


class _TempRootCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo-v06-test-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class CliBoundaryTest(unittest.TestCase):
    def test_check_requires_offline_flag(self) -> None:
        code, text = _run(["check", "--max-wall-seconds", "60"])
        self.assertEqual(code, v06.EXIT_REFUSED)
        self.assertEqual(json.loads(text)["reason_code"], "OFFLINE_FLAG_REQUIRED")

    def test_check_rejects_wall_limit_over_600(self) -> None:
        code, text = _run(["check", "--offline", "--max-wall-seconds", "601"])
        self.assertEqual(code, v06.EXIT_REFUSED)
        self.assertEqual(json.loads(text)["reason_code"],
                         "MAX_WALL_SECONDS_OUT_OF_RANGE")

    def test_unknown_command_is_refused(self) -> None:
        code, _text = _run(["prepare"])
        self.assertEqual(code, v06.EXIT_REFUSED)


class RootGuardTest(_TempRootCase):
    def test_scratch_root_accepts_marked_ancestor(self) -> None:
        marked = Path(tempfile.mkdtemp(prefix=v06.FIXTURE_PREFIX, dir=str(self.root)))
        child = marked / "off-1234"
        child.mkdir()
        self.assertTrue(v06._is_scratch_root(child))
        self.assertTrue(v06._is_scratch_root(marked))

    def test_scratch_root_rejects_unmarked_path(self) -> None:
        plain = self.root / "plain-root"
        plain.mkdir()
        self.assertFalse(v06._is_scratch_root(plain))
        self.assertFalse(v06._is_scratch_root(None))

    def test_env_path_guard_requires_inside_existing_paths(self) -> None:
        env = {}
        for index, key in enumerate(v06.PATH_GUARDED_KEYS):
            path = self.root / ("p%d" % index)
            path.mkdir()
            env[key] = str(path)
        v06._assert_env_paths_under(self.root, env)  # 全部在本根内，不抛

        outside = Path(tempfile.mkdtemp(prefix="nyairo-v06-outside-"))
        try:
            broken = dict(env)
            broken["CHIYO_WORLD_HOME"] = str(outside)
            with self.assertRaises(v06.PhaseRefused) as caught:
                v06._assert_env_paths_under(self.root, broken)
            self.assertIn("ENV_PATH_OUTSIDE_ROOT", caught.exception.reason_code)

            missing = dict(env)
            missing.pop("WORLD_BODY_SOCKET")
            with self.assertRaises(v06.PhaseRefused) as caught:
                v06._assert_env_paths_under(self.root, missing)
            self.assertIn("ENV_PATH_MISSING", caught.exception.reason_code)
        finally:
            try:
                outside.rmdir()
            except OSError:
                pass


@posix_only
class NetworkGuardTest(unittest.TestCase):
    def test_guard_blocks_inet_and_allows_unix(self) -> None:
        original = socket.socket
        guard = v06._NetworkGuard()
        with guard:
            with self.assertRaises(v06.NetworkForbidden):
                socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with self.assertRaises(v06.NetworkForbidden):
                socket.create_connection(("127.0.0.1", 9), timeout=0.1)
            unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            unix.close()
        self.assertEqual(guard.attempts, 2)
        # 退出守卫后恢复原类，不把补丁留给后续代码。
        self.assertIs(socket.socket, original)


def _off_payload() -> dict:
    return {
        "ok": True, "gate": "off", "phase": "off", "submit_gate": "off",
        "materialized_under": "canonical",
        "rounds": [{
            "outcome": "STEP_REFUSED", "step_path": "WORLD_AR0",
            "action_status": "PREPARED",
            "pre_submit": {"reason_code": "GATE_OFF"},
            "attempt_status": "PRE_SUBMIT_FAILED",
            "attempt_error_class": "GATE_OFF",
            "attempts": 1, "receipts": 0,
        }],
        "tickets": [], "travels": [],
        "start_location": "home", "final_position": "home",
        "world_state_unchanged": True,
        "network_attempts": 0, "product_requests": 0,
        "home_isolated": True, "listener_after_close": False,
        "drain": {"runtime_closed": True, "supply_shutdown": True,
                  "world": {"lease_released": True}},
    }


def _on_payload() -> dict:
    return {
        "ok": True, "gate": "canonical", "phase": "on",
        "submit_gate": "canonical",
        "start_location": "home", "outdoor_target": "street_1",
        "return_target": "home", "target_kind": "street",
        "rounds": [
            {"outcome": "IN_TRANSIT", "step_path": "WORLD_AR0",
             "action_status": "SUCCEEDED", "position_after": "home",
             "journey": {"arrived": False}},
            {"outcome": "IN_TRANSIT", "step_path": "WORLD_AR0",
             "action_status": "SUCCEEDED", "position_after": "street_1",
             "journey": {"arrived": False}},
        ],
        "settlements": [
            {"arrived": True, "journey_status": "completed",
             "observed_location_id": "street_1", "activity_status": "COMPLETED",
             "execution_id": "exec-1"},
            {"arrived": True, "journey_status": "completed",
             "observed_location_id": "home", "activity_status": "COMPLETED",
             "execution_id": "exec-2"},
        ],
        "foreground_after_arrivals": [None, None],
        "tickets": [{"execution_id": "exec-1", "state": "CONSUMED"},
                    {"execution_id": "exec-2", "state": "CONSUMED"}],
        "travels": [{"execution_id": "exec-1", "status": "ARRIVED"},
                    {"execution_id": "exec-2", "status": "ARRIVED"}],
        "final_position": "home",
        "network_attempts": 0, "product_requests": 0,
        "home_isolated": True, "listener_after_close": False,
        "drain": {"runtime_closed": True, "supply_shutdown": True,
                  "world": {"lease_released": True}},
    }


class VerdictTest(unittest.TestCase):
    def test_off_checks_are_real(self) -> None:
        payload = _off_payload()
        self.assertTrue(all(v06._off_checks(payload).values()))

        leaked = dict(payload, tickets=[{"execution_id": "e", "state": "CONSUMED"}])
        self.assertFalse(v06._off_checks(leaked)["zero_tickets"])

        no_refusal = json.loads(json.dumps(payload))
        no_refusal["rounds"][0]["pre_submit"]["reason_code"] = "NO_ACTION"
        self.assertFalse(
            v06._off_checks(no_refusal)["explicit_gate_off_refusal"])

        not_prepared = json.loads(json.dumps(payload))
        not_prepared["rounds"][0]["action_status"] = "SUCCEEDED"
        self.assertFalse(v06._off_checks(not_prepared)["action_prepared"])

        changed = json.loads(json.dumps(payload))
        changed["world_state_unchanged"] = False
        self.assertFalse(v06._off_checks(changed)["world_state_unchanged"])

    def test_on_checks_are_real(self) -> None:
        payload = _on_payload()
        self.assertTrue(all(v06._on_checks(payload).values()))

        shared = json.loads(json.dumps(payload))
        shared["tickets"][1]["execution_id"] = "exec-1"
        self.assertFalse(v06._on_checks(shared)["distinct_executions"])

        mislinked = json.loads(json.dumps(payload))
        mislinked["settlements"][1]["execution_id"] = "exec-1"
        self.assertFalse(
            v06._on_checks(mislinked)["settlement_execution_links"])

        unfinished = json.loads(json.dumps(payload))
        unfinished["settlements"][0]["activity_status"] = "ACTIVE"
        self.assertFalse(
            v06._on_checks(unfinished)["outbound_activity_completed"])

        stuck = json.loads(json.dumps(payload))
        stuck["foreground_after_arrivals"] = ["actv-1", None]
        self.assertFalse(v06._on_checks(stuck)["foreground_released"])

        not_back = json.loads(json.dumps(payload))
        not_back["final_position"] = "street_1"
        self.assertFalse(v06._on_checks(not_back)["final_position_origin"])

        not_outdoor = json.loads(json.dumps(payload))
        not_outdoor["target_kind"] = "home"
        self.assertFalse(v06._on_checks(not_outdoor)["outdoor_target_is_outdoor"])


class AuditTest(_TempRootCase):
    def test_audit_reads_owner_records_from_disk(self) -> None:
        phase_root = self.root / "on-1"
        instance = phase_root / "instance"
        world_home = instance / "world-home"
        (world_home / "data" / "world").mkdir(parents=True)
        (world_home / "run").mkdir(parents=True)
        (instance / "life" / "state").mkdir(parents=True)
        (instance / "instance.json").write_text(json.dumps(
            {"world_home": str(world_home)}), encoding="utf-8")
        (world_home / "data" / "world" / "world_state.json").write_text(
            json.dumps({"location": {"place_id": "home"}, "revision": 3}),
            encoding="utf-8")
        (world_home / "run" / v06.REGISTRY_NAME).write_text(json.dumps(
            {"records": [{"execution_id": "exec-1", "state": "consumed"},
                         {"execution_id": "exec-2", "state": "consumed"}]}),
            encoding="utf-8")
        (world_home / "data" / "world" / "world_travel_delivery.json").write_text(
            json.dumps({"travels": [{"execution_id": "exec-1", "status": "ARRIVED"},
                                    {"execution_id": "exec-2", "status": "ARRIVED"}]}),
            encoding="utf-8")
        (world_home / "data" / "world" / "world_journey.json").write_text(
            json.dumps({"history": [{}, {}], "active": None}), encoding="utf-8")
        (instance / "life" / "state" / "canonical_life_state.json").write_text(
            json.dumps({"foreground_activity_ref": None,
                        "activities": {"a1": {"status": "COMPLETED"},
                                       "a2": {"status": "COMPLETED"}}}),
            encoding="utf-8")

        audit = v06._audit_root(phase_root)
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["position"], "home")
        self.assertEqual([row["state"] for row in audit["tickets"]],
                         ["CONSUMED", "CONSUMED"])
        self.assertEqual(len(audit["travels"]), 2)
        self.assertEqual(audit["journey_history"], 2)
        self.assertIsNone(audit["foreground_activity_ref"])
        self.assertEqual([row["status"] for row in audit["activities"]],
                         ["COMPLETED", "COMPLETED"])


if __name__ == "__main__":
    unittest.main()
