#!/usr/bin/env python3
"""经历交接跨进程恢复：Action 已成功、没有 UNKNOWN，也能用正式 CLI 重投原 handoff。

这是一个**可重复的操作示例**，不是测试矩阵。全部在本机隔离夹具内：0 产品模型、
0 外网、0 旧实例、0 外发。Decision 与内容回答是脚本替身（文件里明示）；真实件是
Supply/World/Activity/Memory owner、``ProductionLifeRuntime``、AG-0/1/2/AR-0/AR-1。

流程（一个新隔离根、同一件作品 A、每个阶段各自独立 PID）：

1. **write**（PID 1）：正式 ``evaluate_once`` START 写出作品 A 的下一版，记忆交接正常
   交付；再用受控故障包住真实 Memory consumer 跑一轮 CONTINUE——作品真的多一版、
   Action ``SUCCEEDED``、AR-1 已结算，但 AG-2 经历交接 ``FAILED_PENDING_RETRY``
   留在原 ``pending_handoff_ids`` 里。此时**没有 UNKNOWN Action**。正规 close 后退出。
2. **CLI 恢复 · Memory 不可用**（另一个 PID）：``infra_runtime_recovery_cli.py --recover``
   装配同一实例、取得正规 lease，``recover_all_and_reconcile`` 会重投原交接；这次 Memory
   写不进去，交接保持原身份待重试。入口不得回答 NOTHING_TO_DO/RECOVERED，必须如实
   说还有待重试交接。
3. **CLI 恢复 · Memory 恢复**（另一个 PID）：同一命令，原 handoff 完成一次
   （``ACCEPT`` → M0 一条记录）；不重发 Action、不新增作品版本、不重置 Activity。
4. **再恢复一次**（另一个 PID）：没有待重试交接时明确 ``NOTHING_TO_DO``。
5. **verify**（另一个 PID）：重新装配同一根，只读核对版本/attempt/Activity/Memory 记录。

失败路径（真 UNKNOWN、写者占租约等）在既有定点测试里，不把这个 demo 重复多遍。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/life_recovery_handoff_demo.py

成功时打印 ``RESULT: PASS``，并始终打印 ``NEW_ROOT=...`` 方便只核本轮进程。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

FAULT_TEXT = "这一次真的写进了作品，但记忆交接暂时没送进去。"
B_BODY = "另一件作品的正文，只用来验证恢复不会串到它。"

CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-78s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def _emit(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# 正式 evaluate_once 辅助（真实 AG-1/AG-2；Decision/内容是脚本替身）
# ---------------------------------------------------------------------------------------------


def candidate_refs(chain, observed_at: str) -> list[str]:
    return [str(ref) for ref in chain.runtime.candidate_materializer
            .build_candidate_set(observed_at=observed_at).candidate_refs]


def candidate_payload(chain, ref: str) -> dict[str, Any]:
    return dict(chain.runtime.candidate_read.get_candidate(str(ref)) or {})


def find_work_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    want = str(artifact_id).split(":")[-1]

    def matches(payload: Mapping[str, Any]) -> bool:
        refs = [str(value) for value in (payload.get("target_refs") or [])]
        return (any(want in value for value in refs)
                and not any(value.startswith("closeout:") for value in refs))

    for ref in candidate_refs(chain, observed_at):
        if matches(candidate_payload(chain, ref)):
            return ref
    return None


def find_current_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    rows = []
    for ref in candidate_refs(chain, observed_at):
        payload = candidate_payload(chain, ref)
        if str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            rows.append((ref, payload))
    if not rows:
        return None
    wanted = str(chain.port.resume_point(str(artifact_id)).head_version_id or "").split(":")[-1]
    if wanted:
        for ref, payload in rows:
            if any(wanted in str(value) for value in (payload.get("target_refs") or [])):
                return ref
    return rows[-1][0]


def choose(chain, decision: str, candidate_id: Optional[str]) -> None:
    chain.cognition.script = {"decision": decision, "selected_candidate_id": candidate_id,
                              "deferred_candidate_ids": []}


def round_out(chain, tag: str, observed_at: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:handoff:%s" % tag, observed_at=observed_at, act=True) or {})


def handoff_id_of(chain, action_id: str) -> Optional[str]:
    state = dict(chain.runtime.integration_read.read_state() or {})
    for hid, event in (state.get("handoff_events_by_id") or {}).items():
        if action_id in [str(ref) for ref in (dict(event).get("action_refs") or [])]:
            return str(hid)
    return None


class FlakyExperienceConsumer:
    """The real Memory consumer with a controlled, switchable failure.

    It never writes and is not a second owner: when ``down`` it raises before calling the
    real consumer, which is the AG-2 contract (FAILED_PENDING_RETRY, the handoff stays
    pending, the work is not rolled back).
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.down = False

    def receive_experience_handoff(self, event: Mapping[str, Any]) -> dict[str, Any]:
        if self.down:
            raise RuntimeError("CONTROLLED_MEMORY_UNAVAILABLE")
        return self.inner.receive_experience_handoff(event)


# ---------------------------------------------------------------------------------------------
# 阶段进程
# ---------------------------------------------------------------------------------------------


def _activity(chain, activity_ref: str) -> dict:
    if not activity_ref:
        return {}
    row = chain.runtime.activity_read.get_activity(activity_ref) or {}
    return {"activity_id": row.get("activity_id") or activity_ref,
            "status": row.get("status"), "revision": row.get("revision")}


def phase_write(config: dict) -> int:
    import v05_restart_continuation_smoke as base

    root = Path(config["root"])
    chain = base.open_chain(root, "handoff-write")
    report: dict = {"phase": "write", "pid": os.getpid(), "root": str(root)}
    try:
        work = str(chain.data.get("artifact_id") or config.get("artifact_id") or "")
        if not work:
            raise SystemExit("the prepared instance has no work artifact")
        created_b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件作品",
            content=B_BODY, grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="handoff:b-create:%d" % os.getpid())
        if not getattr(created_b, "applied", False):
            raise SystemExit("artifact B was not created (status=%s)"
                             % getattr(created_b, "status", None))
        artifact_b = str(created_b.artifact_id)

        # 1. 健康的一轮：真实版本 + 正常交付的记忆交接。
        moment = base.next_instant()
        candidate = find_work_candidate(chain, work, moment)
        if not candidate:
            raise SystemExit("no START candidate for the prepared work")
        choose(chain, "START", candidate)
        first = round_out(chain, "start", moment)
        action_a = str(first.get("action_id") or "")
        handoff_a = handoff_id_of(chain, action_a)
        versions_after_start = base.version_count(chain, work)

        # 2. 真实写入成功，紧接着的记忆交接受控失败：Action 没有 UNKNOWN。
        flaky = FlakyExperienceConsumer(chain.runtime.coordinator.experience_consumer)
        chain.runtime.coordinator.experience_consumer = flaky
        chain.runtime.experience_consumer = flaky
        flaky.down = True
        moment = base.next_instant()
        chain.client.payload = {"kind": "append_paragraph", "text": FAULT_TEXT,
                                "self_note": "stub: write once, Memory handoff fails"}
        candidate = find_current_candidate(chain, work, moment)
        if not candidate:
            raise SystemExit("no exact CONTINUE candidate for the Memory-fault round")
        choose(chain, "CONTINUE", candidate)
        second = round_out(chain, "memory-fault", moment)
        action_b = str(second.get("action_id") or "")
        handoff_b = handoff_id_of(chain, action_b)
        state = dict(chain.runtime.integration_read.read_state() or {})
        delivery_b = dict((state.get("handoff_deliveries_by_id") or {}).get(str(handoff_b)) or {})
        pending = list(state.get("pending_handoff_ids") or [])
        activity_ref = str(second.get("activity_ref") or first.get("activity_ref") or "")

        report.update({
            "artifact_id": work,
            "artifact_b": artifact_b,
            "b_head": base.head_version(chain, artifact_b),
            "b_versions": base.version_count(chain, artifact_b),
            "action_start": action_a,
            "handoff_start": handoff_a,
            "action_fault": action_b,
            "handoff_fault": handoff_b,
            "outcome_start": first.get("outcome"),
            "outcome_fault": second.get("outcome"),
            "action_fault_status": str(
                (chain.runtime.action_read.get_action(action_b) or {}).get("status") or ""),
            "versions_after_start": versions_after_start,
            "versions_after_fault": base.version_count(chain, work),
            "head_after_fault": base.head_version(chain, work),
            "handoff_delivery": delivery_b,
            "pending_handoff_ids": pending,
            "handoff_event_count": len(state.get("handoff_events_by_id") or {}),
            "action_attempts": {
                aid: int((chain.runtime.action_read.get_action(aid) or {}).get("attempt_count")
                         or 0)
                for aid in (action_a, action_b) if aid},
            "activity_ref": activity_ref,
            "activity": _activity(chain, activity_ref),
        })
        if first.get("outcome") != "ACTED" or not action_a:
            raise SystemExit("the first formal START did not act: %r" % (first,))
        if second.get("outcome") != "ACTED" or report["action_fault_status"] != "SUCCEEDED":
            raise SystemExit("the Memory-fault round did not succeed as an Action: %r"
                             % (second,))
        if not handoff_b or handoff_b == handoff_a:
            raise SystemExit("the Memory-fault round produced no distinct handoff: %r"
                             % {"a": handoff_a, "b": handoff_b})
        if delivery_b.get("status") != "FAILED_PENDING_RETRY" or str(handoff_b) not in pending:
            raise SystemExit("the handoff was not left FAILED_PENDING_RETRY: %r"
                             % {"delivery": delivery_b, "pending": pending})
    finally:
        report.update(base.close_chain(chain))
        _emit(root / "handoff-phase-write.json", report)
    return 0


def phase_verify(config: dict) -> int:
    import life_memory_review as memreview
    import life_memory_service as lms
    import v05_restart_continuation_smoke as base
    from life_experience_port import LifeExperiencePort

    root = Path(config["root"])
    work = str(config["artifact_id"])
    chain = base.open_chain(root, "handoff-verify")
    report: dict = {"phase": "verify", "pid": os.getpid(), "root": str(root)}
    try:
        handoff = str(config.get("handoff_fault") or "")
        state = dict(chain.runtime.integration_read.read_state() or {})
        delivery = dict((state.get("handoff_deliveries_by_id") or {}).get(handoff) or {})
        actions = {str(aid): dict(chain.runtime.action_read.get_action(str(aid)) or {})
                   for aid in (config.get("action_start"), config.get("action_fault")) if aid}
        memory_port = LifeExperiencePort(str(chain.evidence_db),
                                         controls_db=str(chain.controls_db), subject="chiyo")
        memory_service = lms.LifeMemoryService(
            memory_port, owner="chiyo", database=str(chain.evidence_db),
            controls_database=str(chain.controls_db), source="demo")
        memory = memreview.find_memory_records(memory_service, handoff)
        report.update({
            "artifact_id": work,
            "b_head": base.head_version(chain, str(config["artifact_b"])),
            "b_versions": base.version_count(chain, str(config["artifact_b"])),
            "versions": base.version_count(chain, work),
            "head": base.head_version(chain, work),
            "handoff_delivery": delivery,
            "pending_handoff_ids": list(state.get("pending_handoff_ids") or []),
            "handoff_event_count": len(state.get("handoff_events_by_id") or {}),
            "actions": {aid: {"status": row.get("status"),
                              "attempt_count": int(row.get("attempt_count") or 0)}
                        for aid, row in actions.items()},
            "activity": _activity(chain, str(config.get("activity_ref") or "")),
            "memory": {"status": memory.get("status"),
                       "records": len(memory.get("records") or [])},
        })
    finally:
        report.update(base.close_chain(chain))
        _emit(root / "handoff-phase-verify.json", report)
    return 0


# ---------------------------------------------------------------------------------------------
# 父调度器
# ---------------------------------------------------------------------------------------------


def _run_phase(phase: str, root: Path, payload: dict, *, timeout: float = 900.0) -> dict:
    config_path = root / ("handoff-config-%s.json" % phase)
    _emit(config_path, dict(payload, root=str(root)))
    done = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--phase", phase,
         "--config", str(config_path)],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if done.returncode != 0:
        log_path = root / ("handoff-stderr-%s.log" % phase)
        log_path.write_text(done.stderr, encoding="utf-8", errors="replace")
        last = [line for line in done.stderr.strip().splitlines() if line.strip()]
        print("  %s exited %d; full stderr: %s" % (phase, done.returncode, log_path))
        if last:
            print("  %s last stderr line: %s" % (phase, last[-1][:400]))
        raise SystemExit("%s exited %d" % (phase, done.returncode))
    return json.loads((root / ("handoff-phase-%s.json" % phase)).read_text(encoding="utf-8"))


def _cli_env(root: Path, *, memory_db: Optional[str] = None) -> dict:
    memory = root / "memory"
    return {**os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "NYA02_MEMORY_DB": str(memory_db or (memory / "memory.sqlite3")),
            "NYA02_MEMORY_CONTROLS_DB": str(memory / "controls.sqlite3"),
            "NYA02_MEMORY_SUBJECT": "chiyo"}


def _run_cli(root: Path, *, memory_db: Optional[str] = None,
             timeout: float = 180.0) -> tuple[int, dict, str]:
    command = [sys.executable, "-B",
               str(ROOT / "scripts" / "infra_runtime_recovery_cli.py"),
               "--instance", str(root / "instance"), "--recover", "--json"]
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          env=_cli_env(root, memory_db=memory_db))
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


def _recover_with_owner(root: Path, *, memory_db: Optional[str] = None
                        ) -> tuple[int, dict, str]:
    """Start the fixture's own Supply owner for the CLI, then stop it in the same order."""

    import v05_restart_continuation_smoke as base

    services = base._start_services(root / "supply")
    try:
        return _run_cli(root, memory_db=memory_db)
    finally:
        base._stop_services(services)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="experience handoff recovery demo")
    parser.add_argument("--phase", choices=["write", "verify"])
    parser.add_argument("--config")
    args = parser.parse_args(argv)

    if args.phase:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        return phase_write(config) if args.phase == "write" else phase_verify(config)

    import v05_restart_continuation_smoke as base

    root = base.prepare_fixture()
    print("NEW_ROOT=%s" % root)
    _init_memory(root)

    write = _run_phase("write", root, {})
    unavailable = root / "memory-unavailable"
    unavailable.mkdir(exist_ok=True)

    rc1, down, stderr1 = _recover_with_owner(root, memory_db=str(unavailable))
    rc2, delivered, stderr2 = _recover_with_owner(root)
    rc3, again, stderr3 = _recover_with_owner(root)
    verify = _run_phase("verify", root, {
        "artifact_id": write["artifact_id"], "artifact_b": write["artifact_b"],
        "action_start": write["action_start"], "action_fault": write["action_fault"],
        "handoff_fault": write["handoff_fault"], "activity_ref": write["activity_ref"]})

    handoff = str(write["handoff_fault"])
    check("write 阶段：两次真实写入，第二次 Action SUCCEEDED，没有 UNKNOWN",
          write.get("outcome_start") == "ACTED" and write.get("outcome_fault") == "ACTED"
          and write.get("action_fault_status") == "SUCCEEDED"
          and write.get("versions_after_fault") == int(write.get("versions_after_start") or 0) + 1,
          {key: write.get(key) for key in
           ("outcome_start", "outcome_fault", "action_fault_status",
            "versions_after_start", "versions_after_fault")})
    check("原经历交接以 FAILED_PENDING_RETRY 留在 pending（M0 尚未准入）",
          (write.get("handoff_delivery") or {}).get("status") == "FAILED_PENDING_RETRY"
          and handoff in (write.get("pending_handoff_ids") or [])
          and handoff != str(write.get("handoff_start") or ""),
          {"delivery": write.get("handoff_delivery"),
           "pending": write.get("pending_handoff_ids")})
    check("Memory 不可用时 CLI 不报已完成：保留原 handoff、如实说要重试",
          rc1 == 4 and down.get("status") == "PARTIAL"
          and int(down.get("pid") or 0) not in {0, int(write.get("pid") or -1)}
          and (down.get("result_delivery") or {}).get("delivered") == 0
          and int((down.get("result_delivery") or {}).get("deferred") or 0) >= 1
          and handoff in ((down.get("result_delivery") or {}).get("pending_handoff_ids") or [])
          and not (down.get("handoff_recovery") or {}).get("settled"),
          {"exit": rc1, "status": down.get("status"), "delivery": down.get("result_delivery"),
           "handoff": down.get("handoff_recovery"), "stderr": stderr1})
    check("Memory 恢复后同一 CLI 完成原交接一次（ACCEPT），不重发 Action",
          rc2 == 0 and delivered.get("status") == "RECOVERED"
          and int(delivered.get("pid") or 0) not in {0, int(write.get("pid") or -1),
                                                     int(down.get("pid") or -1)}
          and (delivered.get("result_delivery") or {}).get("delivered") == 1
          and ((delivered.get("result_delivery") or {}).get("consumer_dispositions")
               or {}).get("ACCEPT") == 1
          and not (delivered.get("result_delivery") or {}).get("pending_handoff_ids")
          and int(delivered.get("adapter_submit_calls")
                  if delivered.get("adapter_submit_calls") is not None else -1) == 0
          and not delivered.get("actions"),
          {"exit": rc2, "status": delivered.get("status"),
           "delivery": delivered.get("result_delivery"), "actions": delivered.get("actions"),
           "submit": delivered.get("adapter_submit_calls"), "stderr": stderr2})
    check("再恢复一次明确 NOTHING_TO_DO，不新增交接/投递",
          rc3 == 0 and again.get("status") == "NOTHING_TO_DO"
          and (again.get("result_delivery") or {}).get("dispatched") == 0
          and not (again.get("result_delivery") or {}).get("pending_handoff_ids")
          and not (again.get("handoff_recovery") or {}).get("attempted"),
          {"exit": rc3, "status": again.get("status"),
           "delivery": again.get("result_delivery"), "stderr": stderr3})
    check("恢复没有写第二版、没有新 Action、Activity 终态未变、作品 B 不动",
          verify.get("versions") == write.get("versions_after_fault")
          and verify.get("head") == write.get("head_after_fault")
          and all((verify.get("actions") or {}).get(aid, {}).get("status") == "SUCCEEDED"
                  and (verify.get("actions") or {}).get(aid, {}).get("attempt_count")
                  == (write.get("action_attempts") or {}).get(aid)
                  for aid in (write["action_start"], write["action_fault"]))
          and (verify.get("activity") or {}) == (write.get("activity") or {})
          and verify.get("b_head") == write.get("b_head")
          and verify.get("b_versions") == write.get("b_versions"),
          {"versions": [write.get("versions_after_fault"), verify.get("versions")],
           "head": [write.get("head_after_fault"), verify.get("head")],
           "actions": verify.get("actions"), "attempts": write.get("action_attempts"),
           "activity": [write.get("activity"), verify.get("activity")]})
    check("M0 按原准入接纳了这条经历：一条记录，交接已不在 pending",
          (verify.get("handoff_delivery") or {}).get("status") == "DELIVERED"
          and (verify.get("handoff_delivery") or {}).get("consumer_disposition") == "ACCEPT"
          and handoff not in (verify.get("pending_handoff_ids") or [])
          and (verify.get("memory") or {}).get("records") == 1
          and verify.get("handoff_event_count") == write.get("handoff_event_count"),
          {"delivery": verify.get("handoff_delivery"),
           "pending": verify.get("pending_handoff_ids"), "memory": verify.get("memory"),
           "handoff_events": [write.get("handoff_event_count"),
                              verify.get("handoff_event_count")]})

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print()
    print("handoff=%s; recover-down pid=%s status=%s pending=%s; "
          "recover-up pid=%s status=%s delivered=%s; again pid=%s status=%s" % (
              handoff, down.get("pid"), down.get("status"),
              (down.get("result_delivery") or {}).get("pending_handoff_ids"),
              delivered.get("pid"), delivered.get("status"),
              (delivered.get("result_delivery") or {}).get("delivered"),
              again.get("pid"), again.get("status")))
    print("work A versions: start=%s fault=%s verify=%s; attempts: %s; Memory: %s" % (
        write.get("versions_after_start"), write.get("versions_after_fault"),
        verify.get("versions"), write.get("action_attempts"), verify.get("memory")))
    print("NEW_ROOT=%s" % root)
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed else "PASS"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
