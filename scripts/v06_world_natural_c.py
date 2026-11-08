#!/usr/bin/env python3
"""v0.6 C：三小时自然观察的封闭入口（offline-check / prepare / audit / run）。

这是 [C 预登记](../../docs/engineering/v06-world-natural-c-prereg-20261102.md) 在
「批准离线实现」之后的**薄包装**，沿 B 已验收的同一条单 natural phase，不另造生活循环：

* 身份固定为 ``v06-natural-c``（``g1_real_adoption.C_PROFILE``）：
  ``/tmp/nyairo-v06-natural-20261102-c``、独立 authority、``run:v06-world-natural-20261102-c``、
  ``v06:world-natural:20261102-c``、history 192、20 请求 / **10800 真实秒**、
  绝对截止 2026-11-02 23:05 CST、最晚启动 20:00 CST。
* 窗口 10800、tick 循环上限 3000、trace 行数上限 6000 都是 C 自己的局部参数；
  旧批仍是 1800/900/4000，默认与 CLI 枚举不动。
* **真实 prepare/audit/run 只实现、不执行**：本轮没有 endpoint 调查与真实开跑权限。
  真实 base URL 只从运行期 ``NYA02_MODEL_BASE_URL`` 取，无默认；缺失、脱敏占位
  （``.invalid``）、非 http(s) 协议或 URL 自带凭据都在写任何东西之前拒绝。
* ``offline-check`` 只在本次 ``mkdtemp`` 根里跑：父进程与 phase 子进程都硬拒
  AF_INET/AF_INET6 与出站连接，AF_UNIX 只供本根 Supply/World owner；HTTP/模型请求为零。

安全字段与 B/旧 harness 一致：只输出 id/枚举/布尔/计数/路径；不落 prompt、正文、
reasoning、headers、token 或异常原文。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import g1_real_adoption as g1  # noqa: E402

BATCH = g1.C_PROFILE.key
PROFILE = g1.C_PROFILE
OFFLINE_PREFIX = "v06-natural-c-offline-"
MAX_OFFLINE_SECONDS = 900.0
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_FAILED = 3

#: 真实入口只接受这些错误码在写前拒绝；其余交给既有 g1 入口。
REASON_ENDPOINT_SCHEME = "ENDPOINT_SCHEME_REFUSED"
REASON_ENDPOINT_PLACEHOLDER = "ENDPOINT_PLACEHOLDER_REFUSED"
REASON_ENDPOINT_CREDENTIALS = "ENDPOINT_CREDENTIALS_REFUSED"

#: 仓库默认 World 写面：离线检查前后必须零新增（只比较存在/大小/mtime）。
REPO_LIFECYCLE = ROOT / "data" / "world" / "run" / "action_lifecycle.jsonl"


# ---------------------------------------------------------------------------------------------
# 安全输出
# ---------------------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, indent=1,
                                sort_keys=True, default=str) + "\n")
    sys.stdout.flush()
    return int(code)


_SAFE_RESULT_FIELDS = (
    "ok", "reason_code", "batch", "run_id", "domain_id", "root", "state_root",
    "started_at", "stop_reason", "result", "ledger", "window", "latest_start",
    "deadline", "model_called", "request_sent", "window_seconds", "max_ticks",
    "trace_max_records",
)


def _safe_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {key: result.get(key) for key in _SAFE_RESULT_FIELDS if key in result}


def _safe_natural(natural: Mapping[str, Any]) -> dict[str, Any]:
    """自然 phase 的窄视图：只留计数/布尔/固定枚举，不转发 observation 明细。"""

    world = dict(natural.get("world") or {})
    isolation = dict(natural.get("isolation") or {})
    observation = dict(natural.get("observation") or {})
    return {
        "phase": natural.get("phase"),
        "pid": natural.get("pid"),
        "offline": natural.get("offline"),
        "stop_reason": natural.get("stop_reason"),
        "round_count": natural.get("round_count"),
        "world": {
            "started": world.get("started"),
            "ready": world.get("ready"),
            "status": world.get("status"),
            "gate": world.get("gate"),
            "child_env_gate": world.get("child_env_gate"),
        },
        "isolation": {
            "gate": isolation.get("gate"),
            "world_home_is_instance": isolation.get("world_home_is_instance"),
            "world_home_is_within_root": isolation.get("world_home_is_within_root"),
            "registry_is_within_root": isolation.get("registry_is_within_root"),
            "lifecycle_is_within_root": isolation.get("lifecycle_is_within_root"),
            "lifecycle_exists": isolation.get("lifecycle_exists"),
        },
        "observation": {
            "status": observation.get("status"),
            "request_rows": observation.get("request_rows"),
            "round_rows": observation.get("round_rows"),
            "world_candidates_per_request": observation.get("world_candidates_per_request"),
            "pause_choice_opportunity": observation.get("pause_choice_opportunity"),
        },
        "close_runtime": natural.get("close_runtime"),
        "close_supply": natural.get("close_supply"),
        "close_world": natural.get("close_world"),
    }


# ---------------------------------------------------------------------------------------------
# 真实 endpoint 的非秘密前置检查（只读进程环境，不猜地址、不探测）
# ---------------------------------------------------------------------------------------------


def _endpoint_refusal(environment: Mapping[str, str]) -> Optional[dict[str, Any]]:
    raw = str(environment.get("NYA02_MODEL_BASE_URL") or "").strip()
    if not raw:
        return {"ok": False, "reason_code": g1.REASON_NO_MODEL_CONFIG}
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        return {"ok": False, "reason_code": REASON_ENDPOINT_SCHEME}
    host = str(parts.hostname or "").lower()
    # ``.invalid`` 是 RFC 2606 保留的不可解析 TLD；预登记里的脱敏占位也在这个范围。
    if not host or host.endswith(".invalid"):
        return {"ok": False, "reason_code": REASON_ENDPOINT_PLACEHOLDER}
    if parts.username or parts.password:
        return {"ok": False, "reason_code": REASON_ENDPOINT_CREDENTIALS}
    return None


# ---------------------------------------------------------------------------------------------
# 命令（真实路径只实现、不执行）
# ---------------------------------------------------------------------------------------------


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_c prepare")
    parser.parse_args(list(argv))
    refusal = _endpoint_refusal(os.environ)
    if refusal is not None:
        payload = dict(refusal, batch=BATCH, root=str(PROFILE.root))
        return _emit(payload, code=EXIT_REFUSED)
    with g1._use_batch(BATCH):
        result = g1.prepare(PROFILE.root)
    payload = _safe_result(result)
    payload["batch"] = BATCH
    payload["latest_start"] = PROFILE.latest_start.isoformat()
    payload["deadline"] = PROFILE.deadline.isoformat()
    payload["window_seconds"] = g1._profile_window_seconds(PROFILE)
    payload["max_ticks"] = g1._profile_max_ticks(PROFILE)
    payload["trace_max_records"] = g1._profile_trace_max_records(PROFILE)
    return _emit(payload, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_c audit")
    parser.parse_args(list(argv))
    with g1._use_batch(BATCH):
        result = g1.audit(PROFILE.root)
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_c run")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--capture-candidates", action="store_true")
    args = parser.parse_args(list(argv))
    refusal = _endpoint_refusal(os.environ)
    if refusal is not None:
        payload = dict(refusal, batch=BATCH, root=str(PROFILE.root))
        return _emit(payload, code=EXIT_REFUSED)
    with g1._use_batch(BATCH):
        report = g1.start_run(PROFILE.root,
                              execute_approved=bool(args.execute_approved),
                              capture_candidates=bool(args.capture_candidates))
    payload = _safe_result(report)
    payload["batch"] = BATCH
    payload["window_seconds"] = g1._profile_window_seconds(PROFILE)
    payload["max_ticks"] = g1._profile_max_ticks(PROFILE)
    payload["trace_max_records"] = g1._profile_trace_max_records(PROFILE)
    if report.get("natural") is not None:
        payload["natural"] = _safe_natural(report.get("natural") or {})
    return _emit(payload, code=EXIT_OK if report.get("ok") else EXIT_REFUSED)


# ---------------------------------------------------------------------------------------------
# 离线检查
# ---------------------------------------------------------------------------------------------


def _stat(path: Path) -> Optional[tuple[int, int]]:
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return int(info.st_size), int(info.st_mtime_ns)


class _Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, name: str, condition: Any, detail: Any = None) -> bool:
        ok = bool(condition)
        self.rows.append({"name": name, "ok": ok})
        sys.stderr.write("CHECK %-72s %s\n" % (name, "PASS" if ok else "FAIL"))
        if not ok and detail is not None:
            sys.stderr.write("  detail: %s\n" % json.dumps(
                detail, ensure_ascii=False, default=str)[:600])
        sys.stderr.flush()
        return ok

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(row["ok"] for row in self.rows)


class _Inner:
    """发送前门的正控替身：只有真的走到它才算一次发送。"""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, _request: Any, *, schema_retry_index: int = 0) -> Any:
        self.calls += 1
        return {"failure_class": None}


def _artifact_view(root: Path) -> dict[str, Any]:
    """只读本 scratch 根的正式 Action/Attempt/Receipt 与 World lifecycle。"""

    env = g1._runtime_env(root, offline=True)
    state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
    state = g1._read_json(state_root / "action_reality_state.json")
    phase = g1._read_json(g1._experiment_dir(root) / (g1.PHASE_FILENAME % g1.PHASE_NATURAL))
    isolation = dict(phase.get("isolation") or {})
    lifecycle_path = Path(str(isolation.get("lifecycle_path")
                              or (Path(str(env.get("CHIYO_WORLD_HOME") or "")) / "run"
                                  / "action_lifecycle.jsonl")))
    return {
        "actions": [dict(row) for row in (state.get("actions") or {}).values()],
        "attempts": dict(state.get("attempts") or {}),
        "receipts": dict(state.get("receipts") or {}),
        "proposals": dict(state.get("proposals") or {}),
        "lifecycle": (g1._lifecycle_safe_records(lifecycle_path)
                      if lifecycle_path.exists() else []),
    }


def _formal_effect(view: Mapping[str, Any]) -> dict[str, Any]:
    """至少一条正式 Action：SUCCEEDED + attempt + receipt + 同一 execution 的 APPLIED。"""

    for action in view.get("actions") or ():
        if str(action.get("status") or "") != "SUCCEEDED":
            continue
        action_id = str(action.get("action_id") or "")
        attempts = [dict(row) for row in (view.get("attempts") or {}).values()
                    if str(row.get("action_id") or "") == action_id]
        receipts = [dict(row) for row in (view.get("receipts") or {}).values()
                    if str(row.get("action_id") or "") == action_id]
        if not action_id or not attempts or not receipts:
            continue
        execution_id = str(receipts[0].get("provider_operation_ref") or "")
        lifecycle = [row for row in (view.get("lifecycle") or ())
                     if str(row.get("execution_id") or "") == execution_id]
        proposal = dict((view.get("proposals") or {}).get(
            str(action.get("proposal_ref") or "")) or {})
        payload = dict(proposal.get("payload_data") or {})
        verb = str(payload.get("verb") or "")
        verbs = {str(row.get("verb")) for row in lifecycle if row.get("verb")}
        return {
            "action_status": "SUCCEEDED",
            "attempts": len(attempts),
            "attempt_statuses": sorted({str(row.get("status") or "") for row in attempts}),
            "receipts": len(receipts),
            "execution_id_present": bool(execution_id),
            "lifecycle_applied": any(str(row.get("state") or "") == "APPLIED"
                                     for row in lifecycle),
            "verb": verb or None,
            "verb_matches_lifecycle": bool(verb) and verb in verbs,
            "payload_target_present": bool(payload.get("target")),
        }
    return {"action_status": None, "attempts": 0, "receipts": 0,
            "execution_id_present": False, "lifecycle_applied": False,
            "verb": None, "verb_matches_lifecycle": False,
            "payload_target_present": False}


def _identity_checks(checks: _Checks) -> dict[str, Any]:
    old = {
        "a": ("/root/nyairo-g1-real-20261030", "run:g1-real-20261030",
              "g1:real:20261030", 98),
        "b": ("/root/nyairo-g1-real-20261030-b", "run:g1-real-20261030-b",
              "g1:real:20261030-b", 101),
        "c": ("/root/nyairo-g1-real-20261030-c", "run:g1-real-20261030-c",
              "g1:real:20261030-c", 121),
        "d": ("/root/nyairo-pause-observe-20261030-d", "run:pause-observe-20261030-d",
              "pause-observe:20261030-d", 141),
        "e": ("/root/nyairo-world-observe-20261031-e", "run:world-observe-20261031-e",
              "world-observe:20261031-e", 148),
        "f": ("/root/nyairo-g1-real-20261031-f", "run:g1-real-20261031-f",
              "g1:real:20261031-f", 168),
    }
    actual = {key: (str(MODE.root), MODE.run_id, MODE.domain_id, MODE.expected_history)
              for key, MODE in g1.MODE_PROFILES.items() if key in old}
    checks.add("old a-f identities are unchanged", actual == old, actual)
    checks.add("C is the new closed identity", (
        str(PROFILE.root) == "/tmp/nyairo-v06-natural-20261102-c"
        and str(PROFILE.authority_dir) == "/tmp/nyairo-v06-natural-20261102-c-memory-authority"
        and str(PROFILE.authority_db)
        == "/tmp/nyairo-v06-natural-20261102-c-memory-authority/authority.db"
        and PROFILE.run_id == "run:v06-world-natural-20261102-c"
        and PROFILE.domain_id == "v06:world-natural:20261102-c"
        and PROFILE.expected_history == 192
        and PROFILE.latest_start is not None
        and PROFILE.deadline.isoformat() == "2026-11-02T23:05:00+08:00"
        and PROFILE.latest_start.isoformat() == "2026-11-02T20:00:00+08:00"
        and g1._profile_window_seconds(PROFILE) == 10800
        and g1._profile_max_ticks(PROFILE) == 3000
        and g1._profile_trace_max_records(PROFILE) == 6000),
        {"root": str(PROFILE.root), "run": PROFILE.run_id, "domain": PROFILE.domain_id,
         "history": PROFILE.expected_history,
         "window": g1._profile_window_seconds(PROFILE),
         "ticks": g1._profile_max_ticks(PROFILE),
         "trace": g1._profile_trace_max_records(PROFILE)})
    checks.add("B keeps its earlier closed values; C does not overwrite them", (
        str(g1.B_PROFILE.root) == "/tmp/nyairo-v06-natural-20261102-b"
        and g1.B_PROFILE.expected_history == 191
        and g1.B_PROFILE.deadline.isoformat() == "2026-11-02T20:00:00+08:00"
        and g1.B_PROFILE.latest_start.isoformat() == "2026-11-02T19:25:00+08:00"
        and g1._profile_window_seconds(g1.B_PROFILE) == g1.WINDOW_SECONDS == 1800
        and g1._profile_max_ticks(g1.B_PROFILE) == g1.MAX_TICKS == 900
        and g1._profile_trace_max_records(g1.B_PROFILE)
        == g1.CANDIDATE_TRACE_MAX_RECORDS == 4000),
        {"b_history": g1.B_PROFILE.expected_history,
         "b_window": g1._profile_window_seconds(g1.B_PROFILE),
         "b_ticks": g1._profile_max_ticks(g1.B_PROFILE),
         "b_trace": g1._profile_trace_max_records(g1.B_PROFILE)})
    checks.add("C covers the whole 10800s / 5s tick window before its loop cap",
               g1._profile_max_ticks(PROFILE) * g1.TICK_INTERVAL_SECONDS >= 10800,
               {"ticks": g1._profile_max_ticks(PROFILE),
                "tick_seconds": g1.TICK_INTERVAL_SECONDS})
    checks.add("C is canonical like e; B remains canonical; d stays off; a stays unset",
               g1._world_intent_gate(PROFILE) == "canonical"
               and g1._world_intent_gate(g1.E_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.B_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.D_PROFILE) == "off"
               and g1._world_intent_gate(g1.BATCH_PROFILES["a"]) is None,
               {"c": g1._world_intent_gate(PROFILE)})
    checks.add("the old natural set and CLI tuples did not grow",
               g1.NATURAL_PROFILES == frozenset({"d", "e"})
               and g1.BATCH_CHOICES == ("a", "b", "c", "d")
               and g1.CLI_BATCH_CHOICES == ("a", "b", "c", "d", "e")
               and BATCH not in g1.BATCH_CHOICES
               and BATCH not in g1.CLI_BATCH_CHOICES,
               {"natural": sorted(g1.NATURAL_PROFILES),
                "cli": list(g1.CLI_BATCH_CHOICES)})
    return {"identity": actual, "c_root": str(PROFILE.root), "c_run_id": PROFILE.run_id,
            "c_domain_id": PROFILE.domain_id, "c_history": PROFILE.expected_history,
            "c_deadline": PROFILE.deadline.isoformat(),
            "c_latest_start": PROFILE.latest_start.isoformat(),
            "c_window_seconds": g1._profile_window_seconds(PROFILE),
            "c_max_ticks": g1._profile_max_ticks(PROFILE),
            "c_trace_max_records": g1._profile_trace_max_records(PROFILE)}


def _ledger_scope_checks(checks: _Checks) -> dict[str, Any]:
    """纯替身负控：坏账 fail-closed 只作用于 B/C，旧非 B/C 路径与原 HEAD 语义相同。"""

    class _LedgerStub:
        def __init__(self, state: str) -> None:
            self.state = state

        def view(self) -> dict[str, Any]:
            return {"state": self.state, "run_window_calls": 7}

    with g1._use_batch(g1.E_PROFILE.key):
        old_lower = g1._ledger_calls(_LedgerStub("corrupt"))
        old_upper = g1._ledger_calls(_LedgerStub("CORRUPT"))
        old_unreadable = g1._ledger_calls(_LedgerStub("UNREADABLE"))
    with g1._use_batch(BATCH):
        new_lower = g1._ledger_calls(_LedgerStub("corrupt"))
        new_upper = g1._ledger_calls(_LedgerStub("CORRUPT"))
        new_unreadable = g1._ledger_calls(_LedgerStub("UNREADABLE"))
    with g1._use_batch(g1.B_PROFILE.key):
        b_lower = g1._ledger_calls(_LedgerStub("corrupt"))
        b_upper = g1._ledger_calls(_LedgerStub("CORRUPT"))
        b_unreadable = g1._ledger_calls(_LedgerStub("UNREADABLE"))
    checks.add("the corrupt-ledger fail-closed covers B/C; old batches keep HEAD semantics",
               old_lower == 7 and old_upper is None and old_unreadable == 7
               and new_lower is None and new_upper is None and new_unreadable is None
               and b_lower is None and b_upper is None and b_unreadable is None,
               {"old": [old_lower, old_upper, old_unreadable],
                "c": [new_lower, new_upper, new_unreadable],
                "b": [b_lower, b_upper, b_unreadable]})
    return {"old_lower": old_lower, "old_upper": old_upper,
            "old_unreadable": old_unreadable, "c_lower": new_lower,
            "c_upper": new_upper, "c_unreadable": new_unreadable}


def _budget_checks(checks: _Checks, scratch: Path) -> dict[str, Any]:
    """共享真实预算 20 → 21 发送前拒；坏账同样在发送前拒。"""

    cap_root = scratch / "cap-state"
    cap_root.mkdir(parents=True, exist_ok=True)
    budget = g1._budget(cap_root)
    budget.ensure_window(allow_new=True)
    reserved = 0
    for _index in range(g1.REQUEST_CAP):
        budget.reserve("decision")
        reserved += 1
    third_refused = False
    try:
        budget.reserve("decision")
    except Exception:  # noqa: BLE001 - the refusal itself is the evidence
        third_refused = True
    reader = g1._budget(cap_root)
    calls = g1._ledger_calls(reader)
    inner = _Inner()
    gate = g1.G1CognitionGate(inner, latch=g1.SendLatch(lambda: False), budget=reader)
    send_refused = ""
    try:
        gate.invoke(object())
    except g1.GateClosed as exc:
        send_refused = exc.reason_code
    checks.add("20 reservations fill the shared cap and the 21st is refused",
               reserved == g1.REQUEST_CAP and third_refused and calls == g1.REQUEST_CAP,
               {"reserved": reserved, "third_refused": third_refused, "calls": calls})
    checks.add("a full ledger refuses the next send before the inner transport",
               send_refused == g1.REASON_REQUEST_CAP and inner.calls == 0,
               {"raised": send_refused, "inner_calls": inner.calls})
    checks.add("a second reader sees the same 20 and there is no refund on refusal",
               calls == g1.REQUEST_CAP
               and (reader.view() or {}).get("refund_policy"),
               {"calls": calls})

    corrupt_root = scratch / "corrupt-state"
    corrupt_root.mkdir(parents=True, exist_ok=True)
    (corrupt_root / "life_request_budget.json").write_text("{not json", encoding="utf-8")
    corrupt = g1._budget(corrupt_root)
    corrupt_view = corrupt.view()
    corrupt_inner = _Inner()
    corrupt_gate = g1.G1CognitionGate(corrupt_inner, latch=g1.SendLatch(lambda: False),
                                      budget=corrupt)
    corrupt_raised = ""
    try:
        corrupt_gate.invoke(object())
    except g1.GateClosed as exc:
        corrupt_raised = exc.reason_code
    checks.add("a corrupt ledger refuses the send before the inner transport",
               str(corrupt_view.get("state") or "") == "corrupt"
               and g1._ledger_calls(corrupt) is None
               and corrupt_raised == g1.REASON_LEDGER_UNREADABLE
               and corrupt_inner.calls == 0,
               {"state": corrupt_view.get("state"), "raised": corrupt_raised,
                "inner_calls": corrupt_inner.calls})
    return {"reserved": reserved, "calls": calls, "third_refused": third_refused,
            "send_refused": send_refused, "corrupt_state": corrupt_view.get("state"),
            "corrupt_send_refused": corrupt_raised}


def _send_gate_checks(checks: _Checks, scratch: Path) -> dict[str, Any]:
    """每次发送前先看截止；过期窗口一次也不发给内层。"""

    expired = scratch / "expired-window"
    now = g1._wall_now()
    g1._write_json(g1._window_path(expired), {
        "schema": "g1.window.v1", "run_id": PROFILE.run_id,
        "window_seconds": g1._profile_window_seconds(PROFILE),
        "started_monotonic": 0.0, "deadline_monotonic": 0.0,
        "started_wall": g1._iso(now - timedelta(minutes=30)),
        "deadline_wall": g1._iso(now - timedelta(seconds=5)),
    }, private=True)
    inner = _Inner()
    budget_root = scratch / "gate-budget"
    budget_root.mkdir(parents=True, exist_ok=True)
    budget = g1._budget(budget_root)
    budget.ensure_window(allow_new=True)
    gate = g1.G1CognitionGate(inner, latch=g1.SendLatch(lambda: g1._deadline_passed(expired)),
                              budget=budget)
    raised = ""
    try:
        gate.invoke(object())
    except g1.GateClosed as exc:
        raised = exc.reason_code
    checks.add("an expired window refuses every send before the inner transport",
               raised == g1.REASON_DEADLINE_PASSED and inner.calls == 0,
               {"raised": raised, "inner_calls": inner.calls})
    return {"raised": raised, "inner_calls": inner.calls}


def _identity_negative_checks(checks: _Checks, scratch: Path, root: Path) -> dict[str, Any]:
    """错 root / 错批 prereg / 缺 latest_start 都在读取后失败关闭。"""

    wrong_root = (g1._check_root(g1.E_PROFILE.root) or {}).get("reason_code")
    checks.add("another batch's root is refused as an identity mismatch",
               wrong_root == g1.REASON_BATCH_IDENTITY_MISMATCH, wrong_root)

    fake = scratch / "wrong-batch"
    g1._write_json(g1._experiment_dir(fake) / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": g1.E_PROFILE.key,
        "run_id": g1.E_PROFILE.run_id, "domain_id": g1.E_PROFILE.domain_id,
        "root": str(fake), "state_root": str(fake / "one-day"),
        "deadline": g1.E_PROFILE.deadline.isoformat(),
    }, private=True)
    wrong_prereg = g1.audit(fake, expected_root=fake)
    checks.add("a prereg written for another batch is refused",
               wrong_prereg.get("reason_code") == g1.REASON_BATCH_IDENTITY_MISMATCH,
               wrong_prereg.get("reason_code"))

    missing = scratch / "missing-latest-start"
    g1._write_json(g1._experiment_dir(missing) / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": PROFILE.key,
        "run_id": PROFILE.run_id, "domain_id": PROFILE.domain_id,
        "root": str(missing), "state_root": str(missing / "one-day"),
        "deadline": PROFILE.deadline.isoformat(),
    }, private=True)
    missing_latest = g1.audit(missing, expected_root=missing)
    checks.add("a prereg without the C latest-start line is refused",
               missing_latest.get("reason_code") == g1.REASON_BATCH_IDENTITY_MISMATCH,
               missing_latest.get("reason_code"))

    checks.add("the correct real root is still absent after prepare on scratch",
               not Path(PROFILE.root).exists()
               and not Path(PROFILE.authority_dir).exists()
               and not Path(PROFILE.authority_db).exists(),
               {"root": str(PROFILE.root), "authority": str(PROFILE.authority_dir)})
    return {"wrong_root": wrong_root,
            "wrong_prereg": wrong_prereg.get("reason_code"),
            "missing_latest_start": missing_latest.get("reason_code"),
            "real_root_present": Path(PROFILE.root).exists(),
            "authority_present": Path(PROFILE.authority_dir).exists()}


def _time_window_checks(checks: _Checks, root: Path) -> dict[str, Any]:
    """最晚启动线与绝对截止都在写窗口/started 之前拒绝。"""

    with g1.offline_clock_at(PROFILE.latest_start + timedelta(seconds=1)):
        after_latest = g1.start_run(root, expected_root=root, execute_approved=True,
                                    offline=True)
    window_written = g1._window_path(root).exists()
    with g1.offline_clock_at(PROFILE.deadline + timedelta(seconds=1)):
        after_deadline = g1.start_run(root, expected_root=root, execute_approved=True,
                                      offline=True)
    window_written_after = g1._window_path(root).exists()
    checks.add("starting after 20:00 CST is refused before the window is written",
               after_latest.get("reason_code") == g1.REASON_LATEST_START_PASSED
               and not window_written,
               {"reason": after_latest.get("reason_code"), "window": window_written})
    checks.add("starting after 23:05 CST is refused before the window is written",
               after_deadline.get("reason_code") == g1.REASON_DEADLINE_PASSED
               and not window_written_after,
               {"reason": after_deadline.get("reason_code"),
                "window": window_written_after})
    return {"after_latest": after_latest.get("reason_code"),
            "after_deadline": after_deadline.get("reason_code"),
            "window_written": window_written}


def _trace_capacity_checks(checks: _Checks, scratch: Path) -> dict[str, Any]:
    """trace 行数上限按批解析：C 是 6000，旧批仍是 4000，且到达上限后只计 dropped。"""

    trace_root = scratch / "trace-root"
    c_trace = g1.CandidateTrace(trace_root, phase=g1.PHASE_NATURAL, enabled=True)
    with g1._use_batch(g1.E_PROFILE.key):
        e_trace = g1.CandidateTrace(trace_root, phase=g1.PHASE_NATURAL, enabled=True)
    c_trace.records = c_trace.max_records - 1
    c_trace._append(g1.STAGE_ROUND, {"trigger": "natural"})
    c_trace._append(g1.STAGE_ROUND, {"trigger": "natural"})
    checks.add("C trace capacity is 6000 and old batches stay at 4000",
               c_trace.max_records == 6000 and e_trace.max_records == 4000,
               {"c": c_trace.max_records, "e": e_trace.max_records})
    checks.add("the C trace drops surplus rows past its own cap instead of growing",
               c_trace.records == 6000 and c_trace.dropped_records == 1,
               {"records": c_trace.records, "dropped": c_trace.dropped_records})
    return {"c_max_records": c_trace.max_records, "e_max_records": e_trace.max_records,
            "records": c_trace.records, "dropped": c_trace.dropped_records}


def _cleanup_deadline_checks(checks: _Checks) -> dict[str, Any]:
    """C 的总收尾冗余是 60 秒，窗口是 10800；旧 d/e 仍是 1800+120。附受控超时负控。"""

    now = 1000.0
    window = float(g1._profile_window_seconds(PROFILE))
    c_limit = g1._natural_phase_timeout(PROFILE, now + window, now=now)
    e_limit = g1._natural_phase_timeout(g1.E_PROFILE, now + g1.WINDOW_SECONDS, now=now)
    checks.add("C stops its phase tree 60s after a 10800s window; old batches keep 120s",
               abs(c_limit - (window + 60.0)) < 1e-9
               and abs(e_limit - (g1.WINDOW_SECONDS + 120.0)) < 1e-9,
               {"c": c_limit, "e": e_limit})
    started = time.monotonic()
    timed_out = False
    try:
        subprocess.run([sys.executable, "-B", "-c", "import time; time.sleep(0.4)"],
                       timeout=0.15, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        timed_out = True
    elapsed = time.monotonic() - started
    checks.add("a phase process over its bounded timeout is actually terminated",
               timed_out and elapsed < 5.0, {"elapsed": round(elapsed, 3)})
    return {"c_seconds": c_limit, "e_seconds": e_limit, "timed_out": timed_out,
            "elapsed": round(elapsed, 3)}


def _window_checks(checks: _Checks, root: Path) -> dict[str, Any]:
    """真实窗口文件必须写 C 自己的 10800 秒，不是旧 1800。"""

    window = g1._read_json(g1._window_path(root))
    try:
        span = (float(window.get("deadline_monotonic") or 0.0)
                - float(window.get("started_monotonic") or 0.0))
    except (TypeError, ValueError):
        span = -1.0
    checks.add("the run window is written with C's 10800s, not the old 1800s",
               int(window.get("window_seconds") or 0) == 10800
               and abs(span - 10800.0) < 1.0,
               {"window_seconds": window.get("window_seconds"), "span": round(span, 3)})
    checks.add("the window's wall deadline is capped by the 23:05 absolute line",
               PROFILE.deadline.isoformat() in str(window.get("deadline_basis") or ""),
               {"deadline_basis": window.get("deadline_basis"),
                "deadline_wall": window.get("deadline_wall")})
    return {"window_seconds": window.get("window_seconds"), "span": round(span, 3),
            "deadline_wall": window.get("deadline_wall")}


def _offline_check() -> dict[str, Any]:
    started = time.monotonic()
    checks = _Checks()
    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    sys.stderr.write("scratch root: %s\n" % scratch)
    sys.stderr.flush()
    repo_before = _stat(REPO_LIFECYCLE)
    payload: dict[str, Any] = {"ok": False, "scratch_root": str(scratch),
                               "checks": [], "batch": BATCH}
    with g1._use_batch(BATCH), g1.offline_clock(PROFILE), g1._offline_network_guard(scratch):
        parent_guard = g1._offline_guard_selfcheck()
        payload["network_guard"] = parent_guard
        checks.add("the parent entry refuses AF_INET/AF_INET6/DNS/connect but keeps AF_UNIX",
                   all(parent_guard.get(name) is True for name in
                       ("inet_refused", "inet6_refused", "dns_refused",
                        "connect_refused", "unix_ok")),
                   parent_guard)
        payload["identity"] = _identity_checks(checks)
        payload["ledger_scope"] = _ledger_scope_checks(checks)
        payload["cleanup_deadline"] = _cleanup_deadline_checks(checks)
        payload["trace_capacity"] = _trace_capacity_checks(checks, scratch)

        root = scratch / "instance-root"
        prepared = g1.prepare(root, expected_root=root, offline=True)
        prereg = g1._read_json(g1._experiment_dir(root) / g1.PREREG_FILENAME)
        caps = dict(prereg.get("caps") or {})
        checks.add("prepare reports zero model calls and zero requests",
                   prepared.get("ok") is True
                   and prepared.get("model_called") is False
                   and prepared.get("request_sent") is False,
                   prepared.get("reason_code"))
        checks.add("prepare records the C identity, 192 history and 10800s caps",
                   prepared.get("batch") == BATCH
                   and prepared.get("run_id") == PROFILE.run_id
                   and prepared.get("domain_id") == PROFILE.domain_id
                   and prereg.get("batch") == BATCH
                   and prereg.get("deadline") == PROFILE.deadline.isoformat()
                   and prereg.get("latest_start") == PROFILE.latest_start.isoformat()
                   and int(caps.get("expected_history") or 0) == PROFILE.expected_history
                   and int(caps.get("request_cap") or 0) == g1.REQUEST_CAP
                   and int(caps.get("window_seconds") or 0) == 10800
                   and int(caps.get("max_ticks") or 0) == 3000
                   and int(caps.get("trace_max_records") or 0) == 6000
                   and int(caps.get("check_interval_minutes") or 0) == g1.CHECK_INTERVAL_MINUTES,
                   {"caps": caps, "prereg": {k: prereg.get(k) for k in
                                             ("batch", "deadline", "latest_start")}})
        no_flag = g1.start_run(root, expected_root=root, execute_approved=False, offline=True)
        checks.add("run without --execute-approved is refused",
                   no_flag.get("reason_code") == g1.REASON_EXECUTE_FLAG_MISSING,
                   no_flag.get("reason_code"))
        payload["prepare"] = _safe_result(prepared)
        payload["caps"] = caps
        payload["budget"] = _budget_checks(checks, scratch)
        payload["send_gate"] = _send_gate_checks(checks, scratch)
        payload["identity_negative"] = _identity_negative_checks(checks, scratch, root)
        payload["time_window"] = _time_window_checks(checks, root)
        if time.monotonic() - started > MAX_OFFLINE_SECONDS:
            checks.add("offline check stayed inside its own wall budget", False,
                       {"limit": MAX_OFFLINE_SECONDS})
        else:
            checks.add("offline check stayed inside its own wall budget", True)

        # 正式 phase：真实子进程里的标准 Supply/World/runtime，脚本 Decision/内容。
        run = g1.start_run(root, expected_root=root, execute_approved=True, offline=True,
                           capture_candidates=True)
        natural = dict(run.get("natural") or {})
        world = dict(natural.get("world") or {})
        isolation = dict(natural.get("isolation") or {})
        rounds = [dict(row) for row in (natural.get("rounds") or ())]
        wakes = [dict(row.get("wake") or {}) for row in rounds]
        start_wakes = [row for row in wakes if str(row.get("decision") or "") == "START"]
        checks.add("the parent entry ran exactly one natural phase",
                   run.get("ok") is True and natural.get("phase") == g1.PHASE_NATURAL
                   and run.get("p1") is None and run.get("p2") is None,
                   {"ok": run.get("ok"), "stop": run.get("stop_reason")})
        checks.add("the offline phase used scripted transports with zero product requests",
                   natural.get("offline") is True and "model" not in natural,
                   {"offline": natural.get("offline"), "model_key": "model" in natural})
        checks.add("the run reports the C window and tick/trace bounds",
                   int((run.get("window") or {}).get("window_seconds") or 0) == 10800
                   and g1._profile_max_ticks(PROFILE) == 3000
                   and g1._profile_trace_max_records(PROFILE) == 6000,
                   {"window": (run.get("window") or {}).get("window_seconds")})
        payload["product_requests"] = 0
        child_guard = dict(natural.get("offline_child_guard") or {})
        payload["child_network_guard"] = child_guard
        checks.add("both own owner children self-report the same network refusal",
                   child_guard.get("ok") is True
                   and int(child_guard.get("found") or 0) == 2
                   and all(all(row.get(name) is True for name in
                               ("inet_refused", "inet6_refused", "dns_refused",
                                "connect_refused", "unix_ok"))
                           for row in (child_guard.get("children") or ())),
                   child_guard)
        close_world = dict(natural.get("close_world") or {})
        close_supply = dict(natural.get("close_supply") or {})
        close_runtime = dict(natural.get("close_runtime") or {})
        checks.add("the phase released its runtime and stopped its own owners",
                   close_runtime.get("closed") is True
                   and close_world.get("socket_live_after") is False
                   and close_supply.get("socket_live_after") is False,
                   {"runtime": close_runtime.get("closed"),
                    "world_live": close_world.get("socket_live_after"),
                    "supply_live": close_supply.get("socket_live_after")})
        checks.add("the phase child used the standard World with canonical gate",
                   world.get("started") is True and world.get("ready") is True
                   and world.get("status") == "READY" and world.get("gate") == "canonical"
                   and world.get("child_env_gate") == "canonical",
                   world)
        checks.add("the phase child wrote only inside the scratch root",
                   isolation.get("gate") == "canonical"
                   and isolation.get("world_home_is_instance") is True
                   and isolation.get("world_home_is_within_root") is True
                   and isolation.get("registry_is_within_root") is True
                   and isolation.get("lifecycle_is_within_root") is True
                   and isolation.get("lifecycle_exists") is True,
                   isolation)
        checks.add("the scripted START produced a formal action in this phase",
                   bool(start_wakes) and all(bool(row.get("action_id")) for row in start_wakes),
                   {"start_rounds": len(start_wakes)})
        effect = _formal_effect(_artifact_view(root))
        checks.add("one formal action succeeded with an owner receipt and APPLIED execution",
                   effect.get("action_status") == "SUCCEEDED"
                   and effect.get("attempts", 0) >= 1
                   and effect.get("receipts", 0) >= 1
                   and effect.get("execution_id_present") is True
                   and effect.get("lifecycle_applied") is True
                   and effect.get("verb_matches_lifecycle") is True,
                   effect)
        payload["formal_effect"] = effect
        payload["window_file"] = _window_checks(checks, root)
        payload["world"] = _safe_natural(natural)["world"]
        payload["isolation"] = _safe_natural(natural)["isolation"]
        payload["observation"] = _safe_natural(natural)["observation"]
        payload["natural"] = _safe_natural(natural)
        payload["run"] = _safe_result(run)

        again = g1.start_run(root, expected_root=root, execute_approved=True, offline=True)
        checks.add("a second run on the same root is refused",
                   again.get("reason_code") == g1.REASON_ALREADY_STARTED,
                   again.get("reason_code"))
        audited = g1.audit(root, expected_root=root)
        checks.add("audit sees the C identity, gate, latest start, window and one phase",
                   audited.get("ok") is True and audited.get("batch") == BATCH
                   and audited.get("run_id") == PROFILE.run_id
                   and audited.get("world_intent_gate") == "canonical"
                   and audited.get("latest_start") == PROFILE.latest_start.isoformat()
                   and audited.get("window_seconds") == 10800
                   and audited.get("max_ticks") == 3000
                   and audited.get("trace_max_records") == 6000
                   and list((audited.get("phases") or {})) == [g1.PHASE_NATURAL],
                   {"ok": audited.get("ok"), "batch": audited.get("batch"),
                    "phases": list((audited.get("phases") or {})),
                    "window_seconds": audited.get("window_seconds")})
        ledger = dict(audited.get("ledger") or {})
        checks.add("the audit ledger belongs to the C run and its own cap",
                   ledger.get("run_id") == PROFILE.run_id
                   and int(ledger.get("run_window_max") or 0) == g1.REQUEST_CAP,
                   ledger)
        payload["audit"] = {"ok": audited.get("ok"), "batch": audited.get("batch"),
                            "run_id": audited.get("run_id"),
                            "world_intent_gate": audited.get("world_intent_gate"),
                            "latest_start": audited.get("latest_start"),
                            "window_seconds": audited.get("window_seconds"),
                            "max_ticks": audited.get("max_ticks"),
                            "trace_max_records": audited.get("trace_max_records"),
                            "phases": list((audited.get("phases") or {}))}
        payload["again"] = {"reason_code": again.get("reason_code")}

    repo_after = _stat(REPO_LIFECYCLE)
    checks.add("the repository default World lifecycle got zero new writes",
               repo_before == repo_after, {"before": repo_before, "after": repo_after})
    checks.add("the real root and authority were never created",
               not Path(PROFILE.root).exists()
               and not Path(PROFILE.authority_dir).exists()
               and not Path(PROFILE.authority_db).exists(),
               {"root": str(PROFILE.root), "authority": str(PROFILE.authority_dir)})

    payload["checks"] = checks.rows
    payload["ok"] = checks.ok
    payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
    # 输出安全扫描：白名单对象里不得出现密钥/提示词/正文/异常原文 token。
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    forbidden = ["offline-not-a-key", "sk-", "Authorization", "Bearer ",
                 "system_prompt", "user_prompt", "completion", "reasoning"]
    hits = [token for token in forbidden if token in blob]
    checks.add("the offline report contains no key, prompt or body text", not hits, hits)
    payload["checks"] = checks.rows
    payload["ok"] = checks.ok
    return payload


def cmd_offline_check(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_c offline-check")
    parser.parse_args(list(argv))
    payload = _offline_check()
    return _emit(payload, code=EXIT_OK if payload.get("ok") else EXIT_FAILED)


COMMANDS = {
    "offline-check": cmd_offline_check,
    "prepare": cmd_prepare,
    "audit": cmd_audit,
    "run": cmd_run,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write(__doc__ or "")
        return EXIT_OK if arguments else EXIT_REFUSED
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % (arguments[0],))
        return EXIT_REFUSED
    return int(handler(arguments[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
