#!/usr/bin/env python3
"""一个真实的自有进程根，让只读 CLI 把三种事实分开看到。

它不调用任何产品模型，也不写任何真实实例；只在一个 ``mkdtemp`` 根里按顺序走：

1. **安静但活**：正规启动一个既有 resident（``--no-act --offline-scripted``，不装模型、
   不发请求），让它真实 tick；CLI 应报 ``ALIVE_QUIET``，进程用启动时间核验为 MATCHED。
2. **已停**：对这个自有进程发 SIGTERM，走 resident 自己的 ``request_stop``/``stop``，
   记录写 ``running: false``；CLI 应报 ``STOPPED``。
3. **旧记录残留（模拟崩溃）**：把上一步已正规停止的 state 复制成独立 fixture，只把
   ``running`` 改回 true 并擦掉 ``stopped_at`` 来模拟“进程没机会写最后一份 summary”。
   这是明确标注的模拟，不 kill 任何用户进程；CLI 应报
   ``RECORD_STALE_PROCESS_ABSENT``，退出码 3。
4. **安静但有待处理**：再起一个 resident，并在它自己的 runtime 里通过既有 AR-0 写口
   （同一 lease/capability）提交一个提交意图；受控 adapter 在 fence 之后抛错（标 fixture），
   不发送任何东西。CLI 应报 ``ALIVE_WITH_PENDING`` 并显示积压数，且不得把它说成卡死。

所有临时根都保留（``mkdtemp``，不 cleanup）。被测 CLI 是子进程调用，不是 import helper。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "runtime_health_cli.py"

MODEL_ENV_NAMES = ("NYA02_MODEL_BASE_URL", "NYA02_MODEL_NAME", "NYA02_MODEL_API_KEY",
                   "NYA02_MODEL_PROVIDER")

#: 宿主进程：正规 resident + 可选的一条 AR-0 提交意图。通过 ``-c`` 传入，不新增仓库文件。
HOST_SOURCE = r'''
import json, os, signal, sys, time
root = os.environ["RH_REPO"]
for entry in (root, os.path.join(root, "components"),
              os.path.join(root, "components", "life", "scripts")):
    sys.path.insert(0, entry)
import autonomous_life_resident as resident

state = os.environ["RH_STATE"]
loop = resident.AutonomousLifeResident(
    state_root=state, environment=dict(os.environ), poll_seconds=1,
    act=False, offline_scripted=True)
started = loop.start()
print(json.dumps({"started_ok": bool(started.get("ok")),
                  "reason": started.get("reason"),
                  "detail": started.get("detail"),
                  "pid": os.getpid()}), flush=True)
if not started.get("ok"):
    sys.exit(3)

if os.environ.get("RH_FENCE") == "1":
    import action_reality_ledger as ar0

    class FixtureAdapter(ar0.ActionAdapterProtocol):
        capabilities = ar0.AdapterCapabilities(
            adapter_kind="demo_fixture_world", action_kind=ar0.ACTION_KIND_WORLD,
            submission_boundary="DEMO_ONLY", reconcile_strategy="DEMO_ONLY",
            supports_idempotency=False, supports_status_query=False,
            supports_receipt=False, supports_cancel=False, supports_observed_result=False)

        def validate_prepare(self, action, payload):
            return dict(payload)

        def submit(self, *, executor_capability, action, attempt, payload, fault_hook=None):
            raise RuntimeError("DEMO_FIXTURE_CONTROLLED_STOP: fence committed, nothing sent")

        def query_status_for_reconcile(self, *, action, attempt=None):
            return {"result": "STILL_UNKNOWN"}

    auth = ar0.ActionCommandService(
        store=loop.runtime.action_store, lease=loop.runtime.lease,
        capability=loop.runtime.action_capability, adapters={})
    auth.register_adapter(ar0.ACTION_KIND_WORLD, FixtureAdapter())
    proposal = auth.propose_action(
        action_kind=ar0.ACTION_KIND_WORLD, target_ref="fixture:demo-target",
        payload_ref="fixture:demo-payload",
        payload_data={"fixture": True, "note": "runtime-health demo only"},
        idempotency_key="demo:fence:%s" % os.getpid(),
        source_refs=["fixture:runtime-health-demo"])
    action_id = proposal["action"]["action_id"]
    auth.prepare_action(action_id=action_id)
    executor = ar0.ActionRealityAuthority.issue_sandbox_executor_capability()
    try:
        auth.submit_action(action_id=action_id, executor_capability=executor)
    except Exception as exc:
        print(json.dumps({"fence_open": True, "error": type(exc).__name__}), flush=True)
    else:
        print(json.dumps({"fence_open": False}), flush=True)

stop = {"flag": False}

def _handler(*_args):
    stop["flag"] = True

for name in ("SIGTERM", "SIGINT"):
    number = getattr(signal, name, None)
    if number is not None:
        signal.signal(number, _handler)

while not stop["flag"]:
    try:
        loop.tick()
    except Exception as exc:
        print(json.dumps({"tick_error": "%s: %s" % (type(exc).__name__, exc)}), flush=True)
    deadline = time.monotonic() + 1.0
    while not stop["flag"] and time.monotonic() < deadline:
        time.sleep(0.1)

stopped = loop.stop()
print(json.dumps({"stopped": True,
                  "record_running": bool((loop.read_status() or {}).get("running"))}),
      flush=True)
'''


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key not in MODEL_ENV_NAMES and not key.startswith("NYA02_MODEL_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_cli(state_root: Path) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-B", str(CLI), "--state-root", str(state_root), "--json"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO), env=_env())
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        payload = {"_stdout": proc.stdout[-2000:], "_stderr": proc.stderr[-2000:]}
    return proc.returncode, payload


def _read_line(stream, timeout: float) -> dict:
    """One JSON line from the host process, or an empty mapping on timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = stream.readline()
        if line == "":
            break  # EOF: the host exited without another line
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {}


def _start_host(root: Path, *, fence: bool) -> subprocess.Popen:
    env = _env()
    env.update({
        "RH_REPO": str(REPO),
        "RH_STATE": str(root / "state"),
        "RH_FENCE": "1" if fence else "0",
        # 既有 Supply 配置的三个变量；socket 不连接（--no-act 下没有 owner 调用）。
        "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET": str(root / "supply.sock"),
        "CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT": "chiyo",
        "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT": "grant:runtime-health-demo",
        "CHIYO_LIFE_SUPPLY_WORKSPACE_ID": "ws:runtime-health-demo",
    })
    return subprocess.Popen(
        [sys.executable, "-B", "-c", HOST_SOURCE], cwd=str(REPO), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)


def _stop_host(proc: subprocess.Popen, timeout: float = 20.0) -> None:
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5.0)


def _summary(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="runtime-health-distinction-"))
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # ---- 1. 安静但活 ---------------------------------------------------------------
    quiet = _start_host(root, fence=False)
    started = _read_line(quiet.stdout, 30.0)
    check("quiet_host_started", bool(started.get("started_ok")),
          json.dumps(started, ensure_ascii=False)[:300])
    time.sleep(1.5)  # let at least one real tick write the summary
    code, quiet_health = _run_cli(root / "state")
    check("quiet_verdict", quiet_health.get("verdict") == "ALIVE_QUIET",
          str(quiet_health.get("verdict")))
    check("quiet_process_matched",
          (quiet_health.get("process") or {}).get("status") == "MATCHED",
          str((quiet_health.get("process") or {}).get("basis")))
    check("quiet_process_is_the_host_pid",
          (quiet_health.get("process") or {}).get("pid") == quiet.pid,
          "%s vs %s" % ((quiet_health.get("process") or {}).get("pid"), quiet.pid))
    quiet_exit = code

    # ---- 2. 正规停止 -> 已停 -------------------------------------------------------
    _stop_host(quiet)
    stopped_line = _read_line(quiet.stdout, 2.0)  # the host's final line, if still buffered
    code, stopped_health = _run_cli(root / "state")
    check("stopped_verdict", stopped_health.get("verdict") == "STOPPED",
          str(stopped_health.get("verdict")))
    check("stopped_process_not_running",
          (stopped_health.get("process") or {}).get("status") == "NOT_RUNNING",
          str((stopped_health.get("process") or {}).get("basis")))
    check("stopped_record_not_running",
          (stopped_health.get("recorded") or {}).get("running_claim") is False,
          str((stopped_health.get("recorded") or {}).get("running_claim")))
    check("stopped_exit_zero", code == 0, str(code))
    stopped_exit = code

    # ---- 3. 旧记录残留（模拟崩溃，不 kill 用户进程） --------------------------------
    stale_state = root / "stale_state"
    stale_state.mkdir()
    for name in ("resident_summary.json", "resident_coverage.json"):
        source = root / "state" / name
        if source.exists():
            (stale_state / name).write_text(source.read_text(encoding="utf-8"),
                                            encoding="utf-8")
    stale_summary = _summary(stale_state / "resident_summary.json")
    stale_summary["running"] = True          # simulated crash: the final record was never written
    stale_summary["stopped_at"] = None
    (stale_state / "resident_summary.json").write_text(
        json.dumps(stale_summary, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    code, stale_health = _run_cli(stale_state)
    check("stale_verdict", stale_health.get("verdict") == "RECORD_STALE_PROCESS_ABSENT",
          str(stale_health.get("verdict")))
    check("stale_exit_three", code == 3, str(code))
    stale_exit = code
    check("stale_is_not_reported_alive",
          stale_health.get("verdict") not in {"ALIVE_QUIET", "ALIVE_WITH_PENDING"},
          str(stale_health.get("verdict")))

    # ---- 4. 安静但有积压（真实 AR-0 写口，受控 fixture 结局） -----------------------
    pending = _start_host(root / "pending", fence=True)
    pending_started = _read_line(pending.stdout, 30.0)
    check("pending_host_started", bool(pending_started.get("started_ok")),
          json.dumps(pending_started, ensure_ascii=False)[:300])
    fence_line = _read_line(pending.stdout, 30.0)
    check("pending_fence_committed", bool(fence_line.get("fence_open")),
          json.dumps(fence_line, ensure_ascii=False)[:300])
    time.sleep(1.0)
    code, pending_health = _run_cli(root / "pending" / "state")
    check("pending_verdict", pending_health.get("verdict") == "ALIVE_WITH_PENDING",
          str(pending_health.get("verdict")))
    queue = pending_health.get("queue") or {}
    check("pending_queue_counts", int(queue.get("open_submission_intents") or 0) >= 1,
          "open_submission_intents=%s" % queue.get("open_submission_intents"))
    check("pending_is_not_called_stalled",
          "STALLED" not in json.dumps(pending_health, ensure_ascii=False),
          "no STALLED label without a formal timeout fact")
    check("pending_process_matched",
          (pending_health.get("process") or {}).get("status") == "MATCHED",
          str((pending_health.get("process") or {}).get("basis")))
    _stop_host(pending)
    _read_line(pending.stdout, 2.0)

    for proc, label in ((quiet, "quiet"), (pending, "pending")):
        check("%s_host_exited" % label, proc.poll() is not None, str(proc.poll()))
        if proc.poll() not in (0, None):
            stderr = proc.stderr.read() if proc.stderr else ""
            check("%s_host_stderr" % label, False, stderr[-500:])

    failed = [row for row in checks if not row["ok"]]
    print(json.dumps({
        "ok": not failed,
        "root": str(root),
        "kept": True,
        "checks": checks,
        "observations": {
            "exit_codes": {"quiet": quiet_exit, "stopped": stopped_exit, "stale": stale_exit},
            "quiet": {"verdict": quiet_health.get("verdict"),
                      "process": (quiet_health.get("process") or {}).get("status"),
                      "queue": (quiet_health.get("queue") or {}).get("verdict")},
            "stopped": {"verdict": stopped_health.get("verdict"),
                        "process": (stopped_health.get("process") or {}).get("status")},
            "stale": {"verdict": stale_health.get("verdict"), "exit": stale_exit},
            "pending": {"verdict": pending_health.get("verdict"),
                        "queue": (pending_health.get("queue") or {}).get("verdict"),
                        "open_submission_intents":
                            (pending_health.get("queue") or {}).get("open_submission_intents")},
        },
        "boundaries": [
            "fixture 根：offline_scripted + act=False，0 模型请求；不是她的真实一天",
            "pending 的 adapter 是受控 fixture，fence 之后抛错，不发送任何东西",
            "stale 场景是复制已停记录再改 running 的模拟崩溃残留，不 kill 用户进程",
        ],
    }, ensure_ascii=False, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
