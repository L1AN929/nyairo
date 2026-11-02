#!/usr/bin/env python3
"""在**真实隔离**的 Life Supply owner 环境下跑一遍 ``components/check_life_v02.py``。

为什么需要这个 driver
---------------------
``scripts/test.py`` 里的 ``life-v02`` 组只传 ``PYTHONPATH=components/life/scripts``，没有
``NYA02_SUPPLY_SOCKET`` / ``NYA02_SUBJECT`` / ``NYA02_WORKSPACE_ID`` / ``NYA02_ARTIFACT_GRANT``，
所以 ``check_life_v02.py`` 每次都以 ``SKIP`` 退 0 —— 是"没跑"，不是"通过"。本 driver 补上这套
环境：起一个真实 ``LifeSupplyService``（自己的 Unix socket，全新 mkdtemp 数据根），用现成的
operator grant 流程建一个合法 workspace 与一个 REUSABLE ``COMMIT_MANAGED_ARTIFACT`` grant，
然后把这四个值**只通过子进程 env**交给检查脚本，父进程 env 不做任何 patch。

它**不改** ``check_life_v02.py`` 的任何断言，也**不改**生产组件。只做环境装配 + 执行 + 判定：

* 检查脚本里的认知/内容仍是**脚本替身**（``TestDoubleCognition`` / ``IdentityWriter``），
  0 产品模型调用；本 driver 不引入任何真实 provider。
* ``SKIP``、0 条 ``ok``/``FAIL`` 断言、子进程非 0/1 退出、没有 ``V02_CHECK:`` 终结行，一律
  **fail closed** 并非零退出 —— SKIP 不能算通过。
* owner 正规停止：``shutdown`` → ``server_close`` → ``drain`` → ``service.close``；不裸 unlink，
  不 shutil.rmtree，失败也保留 scratch 与日志。

退出码
------
0  检查真执行且全部断言通过（``V02_CHECK: PASS``）
1  检查真执行但有断言失败（``V02_CHECK: FAIL``）
2  SKIP（环境被认为没配好）—— 本 driver 视为失败
3  异常：无终结行 / 0 断言 / 子进程异常退出 / 装配失败

    python3 scripts/life_v02_isolated_check.py
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPONENTS = REPO / "components"
CHECK = COMPONENTS / "check_life_v02.py"
LIFE_SCRIPTS = COMPONENTS / "life" / "scripts"
NATIVE = COMPONENTS / "native"
SUPPLY = COMPONENTS / "supply"

for _p in (str(COMPONENTS), str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
           str(NATIVE / "memory_runtime_v1"), str(SUPPLY)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: The Supply workspace subject.  Deliberately NOT "chiyo": check_life_v02.py asserts the
#: workspace subject is not an M0 memory owner (the persona is).
SUBJECT = "test:life-v02-isolated"

#: Budget guard for the child check (it is a fast offline check; this only stops a hang).
CHILD_TIMEOUT_SECONDS = 600


class Owner:
    """A real Life Supply owner on its own socket over a fresh data root."""

    def __init__(self, data_root: Path, socket_path: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists() or socket_path.is_symlink():
            raise RuntimeError("REFUSING_TO_REUSE_SOCKET_PATH:%s" % socket_path)
        uid = os.geteuid()
        self.socket_path = socket_path
        self.service = LifeSupplyService(
            data_root, operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(), writer_instance="life-v02-isolated-check")
        self.server = LifeSupplySocketServer(str(socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(30)
            client.connect(str(self.socket_path))
            client.sendall(body + b"\n")
            line = client.makefile("rb").readline(1 << 22)
        if not line:
            raise RuntimeError("owner closed without answering")
        return json.loads(line.decode("utf-8"))

    def stop(self) -> dict:
        """Graceful stop: stop accepting, drain in-flight, close the service. No bare unlink."""
        drained = True
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            drained = self.server.drain(10.0)
            self.thread.join(timeout=10)
            self.service.close()
        return {"drained": bool(drained), "inflight": self.server.inflight_requests}


def _provision(owner: Owner) -> tuple[str, str]:
    """Issue a one-shot workspace grant, provision the workspace, then a reusable artifact grant."""
    op = uuid.uuid4().hex[:12]
    workspace_grant = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": "PROVISION_PERSONAL_WORKSPACE",
        "scope": "workspace:personal", "target": "workspace:%s" % SUBJECT,
        "authority_scope": "life-supply.workspace.v1", "use_mode": "ONE_SHOT",
        "operation_id": "iso:workspace-grant:%s" % op}})
    if workspace_grant.get("status") != "ALLOW":
        raise RuntimeError("workspace grant refused: %r" % workspace_grant)

    provisioned = owner.call({
        "op": "provision_workspace", "grant_id": workspace_grant["grant_id"],
        "operation_key": "iso:workspace:%s" % op,
        "workspace": {"subject_id": SUBJECT}})
    if provisioned.get("status") != "OK":
        raise RuntimeError("workspace provisioning refused: %r" % provisioned)
    workspace_id = str(provisioned["workspace"]["workspace_id"])

    artifact_grant = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": "COMMIT_MANAGED_ARTIFACT",
        "scope": "artifact:personal", "target": "workspace:%s" % workspace_id,
        "authority_scope": "life-supply.artifact.v1", "use_mode": "REUSABLE",
        "operation_id": "iso:artifact-grant:%s" % op}})
    if artifact_grant.get("status") != "ALLOW":
        raise RuntimeError("artifact grant refused: %r" % artifact_grant)
    return workspace_id, str(artifact_grant["grant_id"])


def _child_env(home: Path, life_state: Path, socket_path: Path, workspace_id: str,
               grant_id: str) -> dict:
    """Build the child env explicitly.  The parent env is never patched."""
    keep = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "PYTHONUTF8",
            "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["HOME"] = str(home)
    env["HERMES_HOME"] = str(home)
    env["PYTHONPATH"] = str(LIFE_SCRIPTS)          # mirrors scripts/test.py's life-v02 entry
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["NYA02_SUPPLY_SOCKET"] = str(socket_path)
    env["NYA02_SUBJECT"] = SUBJECT
    env["NYA02_WORKSPACE_ID"] = workspace_id
    env["NYA02_ARTIFACT_GRANT"] = grant_id
    env["NYA02_LIFE_STATE"] = str(life_state)
    env["NYA02_MEMORY_OWNER"] = "chiyo"
    return env


def _classify(returncode: int, stdout: str) -> tuple[int, str, int]:
    """Map the child's output to a fail-closed verdict: (exit_code, verdict, assertion_count)."""
    checks = sum(1 for line in stdout.splitlines()
                 if line.startswith("  ok ") or line.startswith("  FAIL "))
    if "SKIP: isolated Life Supply environment not configured" in stdout:
        return 2, "SKIP_NOT_RUN", checks
    if returncode not in (0, 1):
        return 3, "ABNORMAL_EXIT", checks
    if "V02_CHECK:" not in stdout:
        return 3, "NO_TERMINATOR", checks
    if checks == 0:
        return 3, "ZERO_ASSERTIONS", checks
    if "V02_CHECK: PASS" in stdout and returncode == 0:
        return 0, "EXECUTED_PASS", checks
    if "V02_CHECK: FAIL" in stdout and returncode == 1:
        return 1, "EXECUTED_FAIL", checks
    return 3, "INCONSISTENT", checks


def main(argv=None) -> int:
    def _terminate(signum, _frame):
        raise SystemExit(128 + signum)

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _terminate)
        except (ValueError, OSError):
            pass

    base = Path(os.environ.get("TMPDIR") or "/tmp")
    root = Path(tempfile.mkdtemp(prefix="nyairo-life-v02-iso-", dir=str(base)))
    home = root / "home"
    life_state = root / "life-state"
    data = root / "data"
    for d in (home, life_state, data):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(life_state, 0o700)

    socket_path = root / ("life-supply-%d-%d.sock" % (os.getpid(), time.time_ns()))
    summary: dict = {
        "scratch_root": str(root),
        "subject": SUBJECT,
        "check_script": str(CHECK.relative_to(REPO)),
    }
    owner = None
    service_stop = None
    verdict_code, verdict = 3, "PRECONDITION_FAILED"
    child_stdout = ""
    child_stderr = ""
    child_returncode = None
    try:
        owner = Owner(data, socket_path)
        workspace_id, grant_id = _provision(owner)
        # Grant value is never echoed; only its presence is reported.
        summary.update({"workspace_id": workspace_id, "artifact_grant": "<issued>",
                        "socket": str(socket_path)})
        env = _child_env(home, life_state, socket_path, workspace_id, grant_id)
        summary["child_python"] = sys.executable

        try:
            proc = subprocess.run(
                [sys.executable, "-B", str(CHECK)], cwd=str(COMPONENTS), env=env,
                capture_output=True, text=True, timeout=CHILD_TIMEOUT_SECONDS)
            child_returncode, child_stdout, child_stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            child_returncode = None
            child_stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            child_stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            verdict_code, verdict = 3, "CHILD_TIMEOUT"
        else:
            verdict_code, verdict, checks = _classify(child_returncode, child_stdout)
            summary["assertions"] = checks
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - fail closed with a named reason, no private data
        verdict_code, verdict = 3, "DRIVER_ERROR"
        child_stderr = "%s: %s" % (type(exc).__name__, exc)
    finally:
        if owner is not None:
            try:
                service_stop = owner.stop()
            except BaseException as exc:  # noqa: BLE001
                service_stop = {"error": "%s: %s" % (type(exc).__name__, exc)}

    summary.update({
        "child_returncode": child_returncode,
        "verdict": verdict,
        "exit_code": verdict_code,
        "service_stop": service_stop,
        "cognitive_double": "TestDoubleCognition (脚本替身，0 产品模型)",
        "content_double": "IdentityWriter/RacingWriter (脚本替身)",
    })

    # Child output is assertion-level by design (labels, ids, statuses); no prompt/env/body dumps.
    if child_stdout:
        sys.stdout.write(child_stdout)
        if not child_stdout.endswith("\n"):
            sys.stdout.write("\n")
    if child_stderr:
        sys.stderr.write(child_stderr)
        if not child_stderr.endswith("\n"):
            sys.stderr.write("\n")

    out_json = root / "life-v02-isolated-result.json"
    try:
        out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["result_json"] = str(out_json)
    except OSError:
        pass

    print("LIFE_V02_ISOLATED: %s (exit=%d, checks=%s)"
          % (verdict, verdict_code, summary.get("assertions")))
    print("  scratch:", root)
    print("  service_stop:", service_stop)
    print("  result_json:", summary.get("result_json"))
    return verdict_code


if __name__ == "__main__":
    raise SystemExit(main())
