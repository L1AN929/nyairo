"""v0.6 B 新封闭入口的最小离线用例。

只测 B 自己的身份、共享 20 预算/发送前截止、phase 子环境与根内映射、真实入口的
非秘密 endpoint 前置拒绝。完整的 natural phase 由 ``scripts/v06_world_natural_b.py
offline-check`` 一次跑；这里不重跑 A 的往返矩阵，也不跑旧批套件。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"), str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "supply"),
               str(REPO_ROOT / "components" / "world" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402
import v06_world_natural_b as b  # noqa: E402

#: B 的绝对截止在 2026-11-02 20:00 CST；本文件所有根都在 mkdtemp 下，
#: 因此显式进入本进程的离线参考时刻（截止前 1 小时）。只改本进程内存。
g1.enter_offline_fixture(g1.B_PROFILE)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
WORLD_KEYS = ("CHIYO_WORLD_HOME", "HERMES_HOME", "WORLD_ACTION_AUTH_REGISTRY_DIR",
              "WORLD_ACTION_LIFECYCLE_PATH", "WORLD_ACTIVITY_STATE",
              "WORLD_EXPERIENCE_SPOOL", "WORLD_SELF_MAINTENANCE_STATE")


def _under(root: Path, value: Any) -> bool:
    try:
        Path(str(value)).resolve().relative_to(Path(str(root)).resolve())
        return True
    except (ValueError, OSError):
        return False


class BIdentityTest(unittest.TestCase):
    def test_b_is_the_closed_identity(self):
        self.assertEqual(str(b.PROFILE.root), "/tmp/nyairo-v06-natural-20261102-b")
        self.assertEqual(str(b.PROFILE.authority_dir),
                         "/tmp/nyairo-v06-natural-20261102-b-memory-authority")
        self.assertEqual(str(b.PROFILE.authority_db),
                         "/tmp/nyairo-v06-natural-20261102-b-memory-authority/authority.db")
        self.assertEqual(b.PROFILE.run_id, "run:v06-world-natural-20261102-b")
        self.assertEqual(b.PROFILE.domain_id, "v06:world-natural:20261102-b")
        self.assertEqual(b.PROFILE.expected_history, 191)
        self.assertEqual(b.PROFILE.deadline.isoformat(), "2026-11-02T20:00:00+08:00")
        self.assertEqual(b.PROFILE.latest_start.isoformat(), "2026-11-02T19:25:00+08:00")
        self.assertIs(g1.MODE_PROFILES[b.BATCH], b.PROFILE)

    def test_old_identities_and_cli_tuples_are_untouched(self):
        self.assertEqual(g1.BATCH_PROFILES["a"].expected_history, 98)
        self.assertEqual(g1.BATCH_PROFILES["b"].expected_history, 101)
        self.assertEqual(g1.BATCH_PROFILES["c"].expected_history, 121)
        self.assertEqual(g1.D_PROFILE.expected_history, 141)
        self.assertEqual(g1.E_PROFILE.expected_history, 148)
        self.assertEqual(g1.F_PROFILE.expected_history, 168)
        self.assertEqual(g1.NATURAL_PROFILES, frozenset({"d", "e"}))
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))
        self.assertNotIn(b.BATCH, g1.BATCH_CHOICES)
        self.assertNotIn(b.BATCH, g1.CLI_BATCH_CHOICES)

    def test_b_is_natural_with_canonical_gate_only_in_its_own_tree(self):
        self.assertTrue(g1._is_natural(b.PROFILE))
        self.assertEqual(g1._world_intent_gate(b.PROFILE), "canonical")
        self.assertEqual(g1._world_intent_gate(g1.E_PROFILE), "canonical")
        self.assertEqual(g1._world_intent_gate(g1.D_PROFILE), "off")
        self.assertIsNone(g1._world_intent_gate(g1.BATCH_PROFILES["a"]))

    def test_endpoint_preflight_refuses_missing_placeholder_and_credentials(self):
        cases = {
            "missing": {},
            "placeholder": {"NYA02_MODEL_BASE_URL": "https://gw.example.invalid/v1"},
            "scheme": {"NYA02_MODEL_BASE_URL": "ftp://models.example.test/v1"},
            "credentials": {"NYA02_MODEL_BASE_URL":
                            "https://dummy-user:" "dummy-secret"
                            "@models.example.test/v1"},
        }
        reasons = {name: (b._endpoint_refusal(env) or {}).get("reason_code")
                   for name, env in cases.items()}
        self.assertEqual(reasons["missing"], g1.REASON_NO_MODEL_CONFIG)
        self.assertEqual(reasons["placeholder"], b.REASON_ENDPOINT_PLACEHOLDER)
        self.assertEqual(reasons["scheme"], b.REASON_ENDPOINT_SCHEME)
        self.assertEqual(reasons["credentials"], b.REASON_ENDPOINT_CREDENTIALS)
        self.assertIsNone(b._endpoint_refusal(
            {"NYA02_MODEL_BASE_URL": "https://models.example.test/v1"}))

    def test_real_commands_refuse_without_an_endpoint(self):
        saved = os.environ.pop("NYA02_MODEL_BASE_URL", None)
        try:
            self.assertEqual(b.cmd_prepare([]), b.EXIT_REFUSED)
            self.assertEqual(b.cmd_run(["--execute-approved"]), b.EXIT_REFUSED)
        finally:
            if saved is not None:
                os.environ["NYA02_MODEL_BASE_URL"] = saved


class BEnvTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="v06-b-env-"))
        cls.root = cls.scratch / "instance-root"
        with g1._use_batch(b.BATCH):
            cls.prepared = g1.prepare(cls.root, expected_root=cls.root, offline=True)
            cls.env = g1._runtime_env(cls.root, offline=True)
            cls.child = g1._phase_child_env(g1.B_PROFILE, cls.root, offline=True)

    def test_prepare_is_natural_and_records_the_closed_values(self):
        self.assertTrue(self.prepared.get("ok"), self.prepared.get("reason_code"))
        prereg = g1._read_json(g1._experiment_dir(self.root) / g1.PREREG_FILENAME)
        self.assertEqual(prereg.get("batch"), b.BATCH)
        self.assertEqual(prereg.get("deadline"), b.PROFILE.deadline.isoformat())
        self.assertEqual(prereg.get("latest_start"), b.PROFILE.latest_start.isoformat())
        self.assertEqual(prereg.get("world_intent_gate"), "canonical")
        self.assertIsNone(prereg.get("operator_material"))
        self.assertIsNone(prereg.get("operator_control"))
        self.assertEqual((prereg.get("caps") or {}).get("expected_history"), 191)
        self.assertEqual((prereg.get("caps") or {}).get("request_cap"), 20)
        self.assertEqual((prereg.get("caps") or {}).get("window_seconds"), 1800)
        self.assertEqual((prereg.get("caps") or {}).get("check_interval_minutes"), 1)

    def test_runtime_and_child_env_are_the_same_canonical_rooted_mapping(self):
        self.assertEqual(self.env.get(GATE_ENV), "canonical")
        self.assertEqual(self.child.get(GATE_ENV), "canonical")
        for key in WORLD_KEYS:
            self.assertTrue(_under(self.root, self.env.get(key)), (key, self.env.get(key)))
            self.assertTrue(_under(self.root, self.child.get(key)), (key, self.child.get(key)))
            self.assertEqual(self.env.get(key), self.child.get(key))
        self.assertEqual(self.child.get(g1.V06_OFFLINE_GUARD_ENV), "1")
        self.assertNotIn(g1.V06_OFFLINE_GUARD_ENV, self.env)

    def test_the_child_env_is_built_in_a_real_subprocess(self):
        code = textwrap.dedent("""
            import json, sys
            sys.path[:0] = [sys.argv[1], sys.argv[2]]
            import g1_real_adoption as g1
            import v06_world_natural_b as b
            g1.enter_offline_fixture(b.PROFILE)
            env = g1._phase_child_env(b.PROFILE, sys.argv[3], offline=True)
            out = {name: env.get(name) for name in (
                "WORLD_BODY_INTENT_MUTATION_GATE", "CHIYO_WORLD_HOME", "HERMES_HOME",
                "WORLD_ACTION_AUTH_REGISTRY_DIR", "WORLD_ACTION_LIFECYCLE_PATH",
                "WORLD_ACTIVITY_STATE", "WORLD_EXPERIENCE_SPOOL",
                "WORLD_SELF_MAINTENANCE_STATE", g1.V06_OFFLINE_GUARD_ENV)}
            print(json.dumps(out))
        """)
        done = subprocess.run(
            [sys.executable, "-B", "-c", code, str(REPO_ROOT),
             str(REPO_ROOT / "scripts"), str(self.root)],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(out.get(GATE_ENV), "canonical")
        self.assertEqual(out.get(g1.V06_OFFLINE_GUARD_ENV), "1")
        for key in WORLD_KEYS:
            self.assertTrue(_under(self.root, out.get(key)), (key, out.get(key)))


class BGuardTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="v06-b-guard-"))

    def test_twenty_one_reservations_refuse_before_the_inner_transport(self):
        state = self.scratch / "cap"
        state.mkdir(parents=True, exist_ok=True)
        budget = g1._budget(state)
        budget.ensure_window(allow_new=True)
        for _index in range(g1.REQUEST_CAP):
            budget.reserve("decision")
        with self.assertRaises(Exception):
            budget.reserve("decision")
        inner = b._Inner()
        gate = g1.G1CognitionGate(inner, latch=g1.SendLatch(lambda: False), budget=budget)
        with self.assertRaises(g1.GateClosed) as caught:
            gate.invoke(object())
        self.assertEqual(caught.exception.reason_code, g1.REASON_REQUEST_CAP)
        self.assertEqual(inner.calls, 0)
        self.assertEqual(g1._ledger_calls(g1._budget(state)), g1.REQUEST_CAP)

    def test_a_corrupt_ledger_refuses_before_the_inner_transport(self):
        state = self.scratch / "corrupt"
        state.mkdir(parents=True, exist_ok=True)
        (state / "life_request_budget.json").write_text("{not json", encoding="utf-8")
        with g1._use_batch(b.BATCH):
            budget = g1._budget(state)
            self.assertEqual((budget.view() or {}).get("state"), "corrupt")
            inner = b._Inner()
            gate = g1.G1CognitionGate(inner, latch=g1.SendLatch(lambda: False), budget=budget)
            with self.assertRaises(g1.GateClosed) as caught:
                gate.invoke(object())
        self.assertEqual(caught.exception.reason_code, g1.REASON_LEDGER_UNREADABLE)
        self.assertEqual(inner.calls, 0)

    def test_corrupt_ledger_fail_closed_is_scoped_to_b(self):
        class _Stub:
            def __init__(self, state: str) -> None:
                self.state = state

            def view(self) -> dict[str, Any]:
                return {"state": self.state, "run_window_calls": 7}

        # 旧批保持原 HEAD 语义：只有精确大写 CORRUPT 才算读不出来。
        with g1._use_batch(g1.E_PROFILE.key):
            self.assertEqual(g1._ledger_calls(_Stub("corrupt")), 7)
            self.assertIsNone(g1._ledger_calls(_Stub("CORRUPT")))
            self.assertEqual(g1._ledger_calls(_Stub("UNREADABLE")), 7)
        with g1._use_batch(b.BATCH):
            self.assertIsNone(g1._ledger_calls(_Stub("corrupt")))
            self.assertIsNone(g1._ledger_calls(_Stub("CORRUPT")))
            self.assertIsNone(g1._ledger_calls(_Stub("UNREADABLE")))

    def test_b_phase_cleanup_grace_is_sixty_seconds(self):
        now = 1000.0
        self.assertEqual(g1._natural_phase_timeout(b.PROFILE, now + 1800, now=now), 1860.0)
        self.assertEqual(g1._natural_phase_timeout(g1.E_PROFILE, now + 1800, now=now), 1920.0)
        # 窗口已过时仍是 60 秒下限，不会被负剩余时间缩没。
        self.assertEqual(g1._natural_phase_timeout(b.PROFILE, now - 500, now=now), 60.0)

    def test_an_expired_window_refuses_before_the_inner_transport(self):
        expired = self.scratch / "expired"
        now = g1._wall_now()
        g1._write_json(g1._window_path(expired), {
            "schema": "g1.window.v1", "run_id": b.PROFILE.run_id, "window_seconds": 1800,
            "started_monotonic": 0.0, "deadline_monotonic": 0.0,
            "started_wall": g1._iso(now - timedelta(minutes=30)),
            "deadline_wall": g1._iso(now - timedelta(seconds=5)),
        }, private=True)
        state = self.scratch / "gate-budget"
        state.mkdir(parents=True, exist_ok=True)
        budget = g1._budget(state)
        budget.ensure_window(allow_new=True)
        inner = b._Inner()
        gate = g1.G1CognitionGate(
            inner, latch=g1.SendLatch(lambda: g1._deadline_passed(expired)), budget=budget)
        with self.assertRaises(g1.GateClosed) as caught:
            gate.invoke(object())
        self.assertEqual(caught.exception.reason_code, g1.REASON_DEADLINE_PASSED)
        self.assertEqual(inner.calls, 0)


class BChildGuardTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="v06-b-child-guard-"))
        self.root = self.scratch / "instance-root"
        self.root.mkdir(parents=True, exist_ok=True)

    def test_the_guard_is_argv_bootstrapped_into_the_scratch_root(self):
        class _Child:
            def __init__(self) -> None:
                self.command = [sys.executable, "/repo/scripts/phone.py",
                                "owner-world", "--world-home", "x"]
                self.environment = {"KEEP": "1"}

        child = g1._install_offline_child_guard(_Child(), self.root)
        self.assertEqual(child.command[1], "-c")
        self.assertEqual(child.command[2], g1._V06_OFFLINE_CHILD_BOOTSTRAP)
        self.assertEqual(child.command[3], "/repo/scripts/phone.py")
        self.assertEqual(child.command[-1], "x")
        self.assertEqual(child.environment["KEEP"], "1")
        self.assertEqual(child.environment[g1.V06_OFFLINE_GUARD_ENV], "1")
        module = (Path(child.environment[g1.V06_OFFLINE_GUARD_DIR_ENV])
                  / "v06_offline_guard.py")
        self.assertTrue(module.exists())
        self.assertTrue(_under(self.root, module))

    def test_the_generated_guard_refuses_inet_and_allows_unix_in_a_real_child(self):
        guard_dir = g1._offline_guard_dir(self.root)
        status_dir = guard_dir / "status"
        code = ("import os,sys;"
                "sys.path.insert(0, os.environ['NYAIRO_V06_OFFLINE_GUARD_DIR']);"
                "import v06_offline_guard")
        env = dict(os.environ)
        env.update({g1.V06_OFFLINE_GUARD_ENV: "1",
                    g1.V06_OFFLINE_GUARD_DIR_ENV: str(guard_dir),
                    g1.V06_OFFLINE_GUARD_STATUS_DIR_ENV: str(status_dir)})
        done = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-1000:])
        rows = [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(status_dir.glob("status-*.json"))]
        self.assertTrue(rows)
        row = rows[-1]
        for name in ("inet_refused", "inet6_refused", "dns_refused",
                     "connect_refused", "unix_ok"):
            self.assertIs(row.get(name), True, name)


class BRealEntryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="v06-b-entry-"))
        cls.root = cls.scratch / "instance-root"
        with g1._use_batch(b.BATCH):
            cls.prepared = g1.prepare(cls.root, expected_root=cls.root, offline=True)

    def test_the_latest_start_and_deadline_refuse_before_the_window(self):
        with g1._use_batch(b.BATCH):
            with g1.offline_clock_at(b.PROFILE.latest_start + timedelta(seconds=1)):
                latest = g1.start_run(self.root, expected_root=self.root,
                                      execute_approved=True, offline=True)
            self.assertEqual(latest.get("reason_code"), g1.REASON_LATEST_START_PASSED)
            self.assertFalse(g1._window_path(self.root).exists())
            with g1.offline_clock_at(b.PROFILE.deadline + timedelta(seconds=1)):
                deadline = g1.start_run(self.root, expected_root=self.root,
                                        execute_approved=True, offline=True)
        self.assertEqual(deadline.get("reason_code"), g1.REASON_DEADLINE_PASSED)
        self.assertFalse(g1._window_path(self.root).exists())

    def test_another_batchs_root_and_a_missing_latest_start_are_refused(self):
        with g1._use_batch(b.BATCH):
            self.assertEqual((g1._check_root(g1.E_PROFILE.root) or {}).get("reason_code"),
                             g1.REASON_BATCH_IDENTITY_MISMATCH)
        fake = self.scratch / "fake"
        g1._write_json(g1._experiment_dir(fake) / g1.PREREG_FILENAME, {
            "schema": "g1.real.prereg.v1", "batch": b.BATCH,
            "run_id": b.PROFILE.run_id, "domain_id": b.PROFILE.domain_id,
            "root": str(fake), "state_root": str(fake / "one-day"),
            "deadline": b.PROFILE.deadline.isoformat(),
        }, private=True)
        with g1._use_batch(b.BATCH):
            audited = g1.audit(fake, expected_root=fake)
        self.assertEqual(audited.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)

    def test_the_real_root_and_authority_stay_absent(self):
        self.assertFalse(Path(b.PROFILE.root).exists())
        self.assertFalse(Path(b.PROFILE.authority_dir).exists())
        self.assertFalse(Path(b.PROFILE.authority_db).exists())


if __name__ == "__main__":
    unittest.main()
