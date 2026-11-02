#!/usr/bin/env python3
"""未决写入跨重启：用正式 CLI 恢复原 operation，再继续同一作品。

这是一个**可重复的操作示例**，不是测试矩阵。全部在本机隔离夹具内：0 产品模型、
0 外网、0 旧实例、0 外发。Decision 与内容回答是脚本替身（文件里明示）；真实件是
Supply/World/Activity/Memory owner、``ProductionLifeRuntime``、AG-0/1/2/AR-0。

流程（一个新隔离根、同一件作品 A、每个阶段各自独立 PID）：

1. **write**（PID 1）：正式 ``evaluate_once`` 写出作品 A 的下一版并产生 Activity；
   再用适配器既有的测试故障注入 ``fault_injection_drop_response_after_write`` 跑一轮
   CONTINUE——owner 真的写入成功、回执丢失，AR-0 保持 ``UNKNOWN``，Activity 不假完成。
   另建作品 B 作“不串目标”的对照。保存原 action id / operation key，正规 close/drain 后退出。
2. **CLI 恢复**（另一个 PID，真正的子进程）：在还没发任何新 Decision 之前，
   ``infra_runtime_recovery_cli.py --recover`` 装配同一实例、取得正规 lease，走既有
   ``recover_all_and_reconcile`` 与 AR-0 ``reconcile_action``，按**原 operation** 确认成功；
   版本不再增加、adapter 提交次数为 0、Activity 与预算不被重置，然后正规释放。
3. **continue**（PID 3）：新进程正式 CONTINUE 同一作品 A，owner 真出新版本；B 不变。
4. **再恢复一次**（PID 4）：没有未决动作时明确 ``NOTHING_TO_DO``，不新增版本/结果、不重放动作。

失败路径（owner 不可查询 → STILL_UNKNOWN、写者占租约 → BLOCKED）放在定点测试里，
不把这个主 demo 重复多遍。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/life_unresolved_recovery_demo.py

成功时打印 ``RESULT: PASS``，并始终打印 ``NEW_ROOT=...`` 方便只核本轮进程。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

WRITE_TEXT = "回执丢了，但这一段真的写进了作品。"
CONTINUE_TEXT = "恢复之后，同一个作品又往前走了一段。"
B_BODY = "另一件作品的正文，只用来验证恢复与续写不会串到它。"

CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-74s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def _activity(chain, activity_ref: str) -> dict:
    if not activity_ref:
        return {}
    row = chain.runtime.activity_read.get_activity(activity_ref) or {}
    return {"activity_id": row.get("activity_id") or activity_ref,
            "status": row.get("status"), "revision": row.get("revision")}


def _budget_lines(root: Path) -> int:
    total = 0
    for path in Path(root).rglob("cognition_budget_ledger.jsonl"):
        try:
            total += sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                         if line.strip())
        except OSError:
            continue
    return total


def _emit(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# 阶段进程
# ---------------------------------------------------------------------------------------------


def phase_write(config: dict) -> int:
    import result_method_behavior_smoke as harness
    import v05_restart_continuation_smoke as base
    import v05_result_sharing_continuation_smoke as sharing

    root = Path(config["root"])
    chain = base.open_chain(root, "unresolved-write")
    report: dict = {"phase": "write", "pid": os.getpid(), "root": str(root)}
    try:
        seed = harness.seed_method_after_first_write(chain)
        receipt = seed.round_out.get("step_receipt") or {}
        artifact_a = str(receipt.get("artifact_id")
                         or (seed.round_out.get("project_created") or {}).get("artifact_id") or "")
        activity_ref = str(receipt.get("activity_ref")
                           or seed.round_out.get("activity_ref") or "")
        if not artifact_a:
            raise SystemExit("the first real write created no work artifact: %r"
                             % (seed.round_out,))
        chain.cycle.work_artifact_id = artifact_a
        versions_first = base.version_count(chain, artifact_a)
        activity_before_unknown = _activity(chain, activity_ref)

        # 作品 B：另一件真实作品，只做“不串目标”的对照。
        created_b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件作品",
            content=B_BODY, grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="unresolved:b-create:%d" % os.getpid())
        if not getattr(created_b, "applied", False):
            raise SystemExit("artifact B was not created (status=%s)"
                             % getattr(created_b, "status", None))
        artifact_b = str(created_b.artifact_id)

        # 真实写入已落地、回执丢失：AR-0 保持 UNKNOWN，Activity 不假完成。
        adapter = chain.runtime.artifact_action_adapter
        adapter.fault_injection_drop_response_after_write = True
        moment = base.next_instant()
        candidate = sharing.continue_candidate(chain, artifact_a, observed_at=moment)
        if not candidate:
            raise SystemExit("no exact CONTINUE candidate for the dropped-receipt round")
        chain.client.payload = {"kind": "append_paragraph", "text": WRITE_TEXT,
                                "self_note": "stub: write with a dropped receipt"}
        chain.cognition.script = {"decision": "CONTINUE", "selected_candidate_id": candidate,
                                  "deferred_candidate_ids": []}
        out = chain.cycle.evaluate_once(trigger_ref="unresolved:dropped-receipt", act=True,
                                        observed_at=moment)
        adapter.fault_injection_drop_response_after_write = False
        action_id = str(out.get("action_id") or "")
        action = dict(chain.runtime.action_read.get_action(action_id) or {})
        operation_key = adapter.operation_key_for(str(action.get("idempotency_key") or ""))
        report.update({
            "artifact_a": artifact_a,
            "artifact_b": artifact_b,
            "activity_ref": activity_ref or str(action.get("activity_ref") or ""),
            "versions_first": versions_first,
            "versions_after_unknown": base.version_count(chain, artifact_a),
            "head_after_unknown": base.head_version(chain, artifact_a),
            "unknown_outcome": out.get("outcome"),
            "action_id": action_id,
            "action_status": action.get("status"),
            "operation_key": operation_key,
            "activity_before_unknown": activity_before_unknown,
            "activity_after_unknown": _activity(chain, activity_ref),
            "b_head": base.head_version(chain, artifact_b),
            "b_versions": base.version_count(chain, artifact_b),
            "budget_lines": _budget_lines(root),
            "llm_calls": out.get("llm_calls"),
        })
        if out.get("outcome") != "STEP_UNKNOWN" or action.get("status") != "UNKNOWN":
            raise SystemExit("the dropped-receipt round did not stay UNKNOWN (outcome=%s "
                             "status=%s)" % (out.get("outcome"), action.get("status")))
    finally:
        report.update(base.close_chain(chain))
        _emit(root / "unresolved-phase-write.json", report)
    return 0


def phase_continue(config: dict) -> int:
    import v05_restart_continuation_smoke as base
    import v05_result_sharing_continuation_smoke as sharing

    root = Path(config["root"])
    artifact_a = str(config["artifact_a"])
    artifact_b = str(config["artifact_b"])
    chain = base.open_chain(root, "unresolved-continue")
    chain.cycle.work_artifact_id = artifact_a
    report: dict = {"phase": "continue", "pid": os.getpid(), "root": str(root),
                    "artifact_a": artifact_a, "artifact_b": artifact_b}
    try:
        versions_before = base.version_count(chain, artifact_a)
        b_head_before = base.head_version(chain, artifact_b)
        b_versions_before = base.version_count(chain, artifact_b)
        moment = base.next_instant()
        candidate = sharing.continue_candidate(chain, artifact_a, observed_at=moment)
        if not candidate:
            raise SystemExit("no exact CONTINUE candidate after recovery")
        chain.client.payload = {"kind": "append_paragraph", "text": CONTINUE_TEXT,
                                "self_note": "stub: continue the same work after recovery"}
        chain.cognition.script = {"decision": "CONTINUE", "selected_candidate_id": candidate,
                                  "deferred_candidate_ids": []}
        out = chain.cycle.evaluate_once(trigger_ref="unresolved:continue-after-recovery",
                                        act=True, observed_at=moment)
        receipt = out.get("step_receipt") or {}
        action_row = dict(chain.runtime.action_read.get_action(
            str(out.get("action_id") or "")) or {})
        activity_ref = str(receipt.get("activity_ref") or out.get("activity_ref")
                           or action_row.get("activity_ref") or "")
        report.update({
            "versions_before": versions_before,
            "versions_after": base.version_count(chain, artifact_a),
            "head_after": base.head_version(chain, artifact_a),
            "outcome": out.get("outcome"),
            "decision": out.get("decision"),
            "owner_version_created": out.get("owner_version_created"),
            "activity_ref": activity_ref,
            "activity_after": _activity(chain, activity_ref),
            "b_head_after": base.head_version(chain, artifact_b),
            "b_versions_after": base.version_count(chain, artifact_b),
            "b_head_before": b_head_before,
            "b_versions_before": b_versions_before,
            "llm_calls": out.get("llm_calls"),
        })
    finally:
        report.update(base.close_chain(chain))
        _emit(root / "unresolved-phase-continue.json", report)
    return 0


# ---------------------------------------------------------------------------------------------
# 父调度器
# ---------------------------------------------------------------------------------------------


def _run_phase(phase: str, root: Path, payload: dict, *, timeout: float = 900.0) -> dict:
    config_path = root / ("unresolved-config-%s.json" % phase)
    _emit(config_path, dict(payload, root=str(root)))
    done = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--phase", phase,
         "--config", str(config_path)],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if done.returncode != 0:
        log_path = root / ("unresolved-stderr-%s.log" % phase)
        log_path.write_text(done.stderr, encoding="utf-8", errors="replace")
        last = [line for line in done.stderr.strip().splitlines() if line.strip()]
        print("  %s exited %d; full stderr: %s" % (phase, done.returncode, log_path))
        if last:
            print("  %s last stderr line: %s" % (phase, last[-1][:400]))
        raise SystemExit("%s exited %d" % (phase, done.returncode))
    return json.loads((root / ("unresolved-phase-%s.json" % phase)).read_text(encoding="utf-8"))


def _cli_env(root: Path) -> dict:
    memory = root / "memory"
    return {**os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "NYA02_MEMORY_DB": str(memory / "memory.sqlite3"),
            "NYA02_MEMORY_CONTROLS_DB": str(memory / "controls.sqlite3"),
            "NYA02_MEMORY_SUBJECT": "chiyo"}


def _run_cli(root: Path, *, timeout: float = 180.0) -> tuple[int, dict, str]:
    command = [sys.executable, "-B",
               str(ROOT / "scripts" / "infra_runtime_recovery_cli.py"),
               "--instance", str(root / "instance"), "--recover", "--json"]
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          env=_cli_env(root))
    try:
        payload = json.loads(done.stdout or "{}")
    except ValueError:
        payload = {"raw_stdout": done.stdout[-800:], "raw_stderr": done.stderr[-800:]}
    return int(done.returncode), payload, done.stderr[-600:]


def _init_memory(root: Path) -> None:
    from life_experience_port import life_conversation_id
    from memory_control import MemoryControlLedger

    memory = root / "memory"
    memory.mkdir(exist_ok=True)
    MemoryControlLedger(str(memory / "controls.sqlite3"), str(memory / "memory.sqlite3"),
                        owner="chiyo", conversation=life_conversation_id("chiyo")).initialize()


def _recover_with_owner(root: Path) -> tuple[int, dict, str]:
    """Start the fixture's own Supply owner for the CLI, then stop it through the same order."""

    import v05_restart_continuation_smoke as base

    services = base._start_services(root / "supply")
    try:
        return _run_cli(root)
    finally:
        base._stop_services(services)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="unresolved write recovery demo")
    parser.add_argument("--phase", choices=["write", "continue"])
    parser.add_argument("--config")
    args = parser.parse_args(argv)

    if args.phase:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        return phase_write(config) if args.phase == "write" else phase_continue(config)

    import v05_restart_continuation_smoke as base

    root = base.prepare_fixture()
    print("NEW_ROOT=%s" % root)
    _init_memory(root)

    write = _run_phase("write", root, {})
    budget_before_recovery = _budget_lines(root)
    rc1, first, stderr1 = _recover_with_owner(root)
    budget_after_recovery = _budget_lines(root)

    cont = _run_phase("continue", root, {
        "artifact_a": write["artifact_a"], "artifact_b": write["artifact_b"]})
    rc2, second, stderr2 = _recover_with_owner(root)

    check("write 阶段真的写出一版作品，随后丢回执的那次也落了一版",
          write.get("versions_after_unknown") == int(write.get("versions_first") or 0) + 1,
          {"versions_first": write.get("versions_first"),
           "versions_after_unknown": write.get("versions_after_unknown")})
    check("丢回执后 AR-0 保持 UNKNOWN，Activity 没有假完成",
          write.get("unknown_outcome") == "STEP_UNKNOWN"
          and write.get("action_status") == "UNKNOWN"
          and (write.get("activity_after_unknown") or {}).get("status") == "ACTIVE"
          and (write.get("activity_after_unknown") or {}).get("revision")
          == (write.get("activity_before_unknown") or {}).get("revision"),
          {"unknown": write.get("unknown_outcome"),
           "action": write.get("action_status"),
           "activity": write.get("activity_after_unknown")})
    check("CLI --recover 是另一个进程，按原 operation 确认成功",
          rc1 == 0 and first.get("status") == "RECOVERED"
          and int(first.get("pid") or 0) not in {0, int(write.get("pid") or -1)}
          and len(first.get("actions") or []) == 1
          and first["actions"][0].get("action_id") == write.get("action_id")
          and first["actions"][0].get("operation_key") == write.get("operation_key")
          and first["actions"][0].get("reconciliation_result") == "CONFIRMED_SUCCEEDED",
          {"exit": rc1, "status": first.get("status"), "pid": first.get("pid"),
           "actions": first.get("actions"), "stderr": stderr1})
    row1 = (first.get("actions") or [{}])[0]
    check("恢复没有写第二次版本，也没有调用 adapter 提交",
          row1.get("artifact_versions_before") == write.get("versions_after_unknown")
          and row1.get("artifact_versions_after") == write.get("versions_after_unknown")
          and int(first.get("adapter_submit_calls") if first.get("adapter_submit_calls")
                  is not None else -1) == 0
          and int((first.get("recovery") or {}).get("llm_calls")
                  if (first.get("recovery") or {}).get("llm_calls") is not None else -1) == 0,
          {"versions": [row1.get("artifact_versions_before"),
                        row1.get("artifact_versions_after")],
           "submit": first.get("adapter_submit_calls"),
           "recovery": first.get("recovery")})
    check("恢复没有重置 Activity 或预算，且正规释放了租约",
          (row1.get("activity") or {}).get("status")
          == (write.get("activity_after_unknown") or {}).get("status")
          and (row1.get("activity") or {}).get("revision")
          == (write.get("activity_after_unknown") or {}).get("revision")
          and budget_after_recovery == budget_before_recovery
          and (first.get("writer") or {}).get("released") is True,
          {"activity": row1.get("activity"),
           "budget": [budget_before_recovery, budget_after_recovery],
           "writer": first.get("writer")})
    check("恢复后新 Decision 继续同一作品 A，并真的写出新版本",
          cont.get("outcome") == "ACTED" and cont.get("decision") == "CONTINUE"
          and cont.get("versions_after") == int(cont.get("versions_before") or 0) + 1
          and cont.get("activity_ref") == write.get("activity_ref"),
          {"outcome": cont.get("outcome"), "decision": cont.get("decision"),
           "versions": [cont.get("versions_before"), cont.get("versions_after")],
           "activity": [write.get("activity_ref"), cont.get("activity_ref")]})
    check("续写与恢复都没有碰到作品 B",
          cont.get("b_head_after") == write.get("b_head")
          and cont.get("b_versions_after") == write.get("b_versions"),
          {"b_head": [write.get("b_head"), cont.get("b_head_after")],
           "b_versions": [write.get("b_versions"), cont.get("b_versions_after")]})
    check("没有未决动作时再次显式恢复明确说无事可做，且不新增版本/提交",
          rc2 == 0 and second.get("status") == "NOTHING_TO_DO"
          and not second.get("actions")
          and int(second.get("adapter_submit_calls") if second.get("adapter_submit_calls")
                  is not None else -1) == 0
          and int(second.get("pid") or 0) not in {0, int(write.get("pid") or -1),
                                                  int(cont.get("pid") or -1)},
          {"exit": rc2, "status": second.get("status"),
           "actions": second.get("actions"), "pid": second.get("pid"),
           "submit": second.get("adapter_submit_calls"), "stderr": stderr2})

    failed = [name for name, ok, _detail in CHECKS if not ok]
    delivery = dict(first.get("result_delivery") or {})
    consequence = dict(first.get("consequence_delivery") or {})
    memory = dict(first.get("memory") or {})
    print()
    print("恢复后投递：结果 handoff 已交付=%s 待重试=%s；后果 outbox 待投递=%s；"
          "Memory=%s accepted=%s deferred=%s" % (
              delivery.get("delivered"), delivery.get("failed_pending_retry"),
              consequence.get("pending_outbox"), memory.get("status"),
              memory.get("accepted"), memory.get("deferred")))
    print("PIDs: write=%s recover=%s continue=%s recover2=%s (parent %s)" % (
        write.get("pid"), first.get("pid"), cont.get("pid"), second.get("pid"), os.getpid()))
    print("work A: %s versions %s -> %s; work B head unchanged: %s" % (
        write.get("artifact_a"), write.get("versions_after_unknown"),
        cont.get("versions_after"),
        cont.get("b_head_after") == write.get("b_head")))
    print("NEW_ROOT=%s" % root)
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed else "PASS"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
