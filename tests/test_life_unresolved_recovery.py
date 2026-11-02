"""未决写入恢复入口的定点测试。

主链在 ``scripts/life_unresolved_recovery_demo.py``；这里只覆盖三件事：

1. 不带 ``--recover`` 时 CLI 仍只读，不改任何文件；
2. 实例缺少 owner 绑定时 ``--recover`` 具名 BLOCKED，且未装配、未写；
3. owner 不可查询时，真实 UNKNOWN 动作保持 STILL_UNKNOWN、adapter 零提交，
   版本数在 owner 恢复后仍只有那一次落地。

真实夹具根全部保留（不 TemporaryDirectory / rmtree）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import posix_only  # noqa: E402

CLI = ROOT / "scripts" / "infra_runtime_recovery_cli.py"
DEMO = ROOT / "scripts" / "life_unresolved_recovery_demo.py"


def _clean_env() -> dict:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    for name in list(env):
        if (name.startswith("NYAIRO_") or name in {
                "LIFE_SUPPLY_SOCKET", "NYA02_SUBJECT", "NYA02_MEMORY_DB",
                "NYA02_MEMORY_CONTROLS_DB", "NYA02_MEMORY_SUBJECT",
                "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET", "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT",
                "CHIYO_LIFE_SUPPLY_WORKSPACE_ID"}):
            env.pop(name, None)
    return env


def _run_cli(instance: Path, *, recover: bool, env: dict | None = None,
             timeout: float = 120.0) -> tuple[int, dict, str]:
    command = [sys.executable, "-B", str(CLI), "--instance", str(instance), "--json"]
    if recover:
        command.insert(-1, "--recover")
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          env=env or _clean_env())
    try:
        payload = json.loads(done.stdout or "{}")
    except ValueError:
        payload = {"raw_stdout": done.stdout[-800:], "raw_stderr": done.stderr[-800:]}
    return int(done.returncode), payload, done.stderr[-600:]


def _snapshot(root: Path) -> list[tuple[str, int]]:
    return [(str(path.relative_to(root)), path.stat().st_size if path.is_file() else -1)
            for path in sorted(root.rglob("*"))]


def _instance(root: Path, *, with_bindings: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": "life.one_day.v1", "subject_id": "test:unresolved-recovery",
            "environment": {}}
    if with_bindings:
        data.update({"workspace_id": "ws:test", "artifact_grant_id": "grant:test",
                     "supply_socket": str(root / "missing.sock")})
        data["environment"] = {"LIFE_SUPPLY_SOCKET": str(root / "missing.sock")}
    (root / "instance.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "life" / "state").mkdir(parents=True, exist_ok=True)
    return root


class RecoveryEntryTest(unittest.TestCase):
    def test_default_cli_stays_read_only(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst", with_bindings=True)
            before = _snapshot(root)
            code, payload, _stderr = _run_cli(root, recover=False)
            self.assertEqual(code, 0, payload)
            self.assertIn("class", payload)
            self.assertEqual(_snapshot(root), before)

    def test_missing_owner_binding_is_blocked_without_assembly(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst", with_bindings=False)
            before = _snapshot(root)
            code, payload, stderr = _run_cli(root, recover=True)
            self.assertEqual(code, 3, (payload, stderr))
            self.assertEqual(payload.get("status"), "BLOCKED", payload)
            self.assertEqual(payload.get("reason"), "OWNER_CONFIG_INCOMPLETE", payload)
            self.assertIn("supply_socket", payload.get("missing") or [])
            self.assertEqual(_snapshot(root), before)


@posix_only
class OwnerOfflineRecoveryTest(unittest.TestCase):
    def test_unknown_stays_unknown_with_zero_submit_and_no_second_version(self) -> None:
        import result_method_behavior_smoke as harness
        import v05_restart_continuation_smoke as base
        from life_artifact_port import LifeArtifactPort

        root = harness._fresh_root("unresolved-recovery")
        base.prepare_fixture(root)
        config = root / "config-write.json"
        config.write_text(json.dumps({"root": str(root)}), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, "-B", str(DEMO), "--phase", "write", "--config", str(config)],
            capture_output=True, text=True, timeout=900, env=_clean_env())
        self.assertEqual(done.returncode, 0, (done.stdout[-2000:], done.stderr[-2000:]))
        write = json.loads((root / "unresolved-phase-write.json").read_text(encoding="utf-8"))
        self.assertEqual(write.get("action_status"), "UNKNOWN")

        # The owner is stopped here: the configured socket is offline.
        code, payload, stderr = _run_cli(root / "instance", recover=True)
        self.assertEqual(code, 4, (payload, stderr))
        self.assertEqual(payload.get("status"), "STILL_UNKNOWN", payload)
        rows = payload.get("actions") or []
        self.assertEqual(len(rows), 1, payload)
        self.assertEqual(rows[0].get("status_after"), "UNKNOWN", rows[0])
        self.assertEqual(rows[0].get("operation_key"), write.get("operation_key"))
        self.assertEqual(payload.get("adapter_submit_calls"), 0, payload)
        self.assertIsNone(rows[0].get("artifact_versions_after"), rows[0])

        # Owner comes back: the dropped-receipt write still landed exactly once.
        services = base._start_services(root / "supply")
        try:
            instance = json.loads((root / "instance" / "instance.json").read_text(encoding="utf-8"))
            port = LifeArtifactPort(str(services.supply_path), str(instance["subject_id"]))
            total = int(port.list_versions(str(write["artifact_a"]), limit=1)["total_versions"])
        finally:
            base._stop_services(services)
        self.assertEqual(total, write.get("versions_after_unknown"))


@posix_only
class LiveWriterRecoveryTest(unittest.TestCase):
    def test_live_writer_refuses_recovery_with_zero_effect(self) -> None:
        import result_method_behavior_smoke as harness
        import v05_restart_continuation_smoke as base

        root = harness._fresh_root("unresolved-live-writer")
        base.prepare_fixture(root)
        chain = base.open_chain(root, "lease-hold")
        try:
            instance = root / "instance"
            before = _snapshot(instance)
            code, payload, stderr = _run_cli(instance, recover=True)
            self.assertEqual(code, 3, (payload, stderr))
            self.assertEqual(payload.get("status"), "BLOCKED", payload)
            self.assertEqual(payload.get("reason"), "PRECHECK_MANUAL", payload)
            self.assertFalse((payload.get("writer") or {}).get("lease_acquired"), payload)
            self.assertEqual(_snapshot(instance), before)
        finally:
            base.close_chain(chain)


if __name__ == "__main__":
    unittest.main()
