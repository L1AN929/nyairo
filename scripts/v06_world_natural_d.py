#!/usr/bin/env python3
"""v0.6 D：ambient 背景自然观察的封闭入口（offline-check / prepare / audit / run）。

这是 [D 预登记](../../docs/engineering/v06-world-natural-d-prereg-20261103.md) 在
「完整入口离线适配」之后的**薄包装**，沿 B/C 已验收的同一条单 natural phase，不另造生活循环：

* 身份固定为 ``v06-natural-d``（``g1_real_adoption.V06_D_PROFILE``）：
  ``/tmp/nyairo-v06-natural-20261103-d``、独立 authority、``run:v06-world-natural-20261103-d``、
  ``v06:world-natural:20261103-d``、history 212、20 请求 / **1800 真实秒**、
  最晚启动 2026-11-04 00:15 CST、绝对截止 00:50 CST、收尾额外 60 秒。
* 只在 D 自己的 phase/runtime/World 子环境里打开 ``CHIYO_WORLD_AMBIENT_EVENTS_ENABLED=1``，
  并把 ``WORLD_BODY_INTENT_MUTATION_GATE=off`` 显式写死：本批只观察 ambient 背景，真实
  世界动作走既有 gate-off 预提交拒绝链。父 shell、实例默认和旧批都不动；不靠 shell export
  覆盖代码映射。
* 离线检查在**脚本 client 真正收到的输入**上核对 ambient 透传：只输出开关/事件数量/
  事件 ID，以及 ID 与公开 summary 是否以原文精确出现在同一份输入里；prompt 正文不进报告。
  当前 World 没有事件时如实报 0，不造事件；固定非产品 fixture 在单元测试里只验边界透传。
* **真实 prepare/run 只实现、不执行**：本轮由父级唯一运行。真实 base URL 只从运行期
  ``NYA02_MODEL_BASE_URL`` 取，无默认；缺失、脱敏占位（``.invalid``）、非 http(s) 协议或
  URL 自带凭据都在写任何东西之前拒绝。

安全字段与 B/C/旧 harness 一致：只输出 id/枚举/布尔/计数/路径；不落 prompt、正文、
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

BATCH = g1.V06_D_PROFILE.key
PROFILE = g1.V06_D_PROFILE
OFFLINE_PREFIX = "v06-natural-d-offline-"
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
    ambient = dict(natural.get("ambient_client") or {})
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
            "child_env_ambient": world.get("child_env_ambient"),
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
        "ambient_client": {key: ambient.get(key) for key in (
            "ambient_env", "requests", "status", "ambient_key_present", "event_count",
            "event_ids", "ids_exact_in_input", "summaries_exact_in_input", "reason_code")},
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
    parser = argparse.ArgumentParser(prog="v06_world_natural_d prepare")
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
    parser = argparse.ArgumentParser(prog="v06_world_natural_d audit")
    parser.parse_args(list(argv))
    with g1._use_batch(BATCH):
        result = g1.audit(PROFILE.root)
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_d run")
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
    """只读本 scratch 根的正式 Action/Attempt/Receipt、World lifecycle、票与位置。

    票只读 World 自己的 registry 状态文件（和 roundtrip 检查同一路径/同一形状）；
    位置与姿态直接取 phase 落盘的 ``world_before``/``world_after``（也在本 scratch 内）。
    """

    env = g1._runtime_env(root, offline=True)
    state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
    state = g1._read_json(state_root / "action_reality_state.json")
    phase = g1._read_json(g1._experiment_dir(root) / (g1.PHASE_FILENAME % g1.PHASE_NATURAL))
    isolation = dict(phase.get("isolation") or {})
    lifecycle_path = Path(str(isolation.get("lifecycle_path")
                              or (Path(str(env.get("CHIYO_WORLD_HOME") or "")) / "run"
                                  / "action_lifecycle.jsonl")))
    registry_dir = Path(str(isolation.get("auth_registry_dir") or ""))
    tickets: list[dict[str, Any]] = []
    if str(registry_dir):
        try:
            registry = json.loads((registry_dir / "action_authorizations.json")
                                  .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            registry = {}
        tickets = [{"execution_id": row.get("execution_id"),
                    "state": str(row.get("state") or "").upper()}
                   for row in (dict(registry).get("records") or ())
                   if isinstance(row, Mapping)]
    return {
        "actions": [dict(row) for row in (state.get("actions") or {}).values()],
        "attempts": dict(state.get("attempts") or {}),
        "receipts": dict(state.get("receipts") or {}),
        "proposals": dict(state.get("proposals") or {}),
        "lifecycle": (g1._lifecycle_safe_records(lifecycle_path)
                      if lifecycle_path.exists() else []),
        "tickets": tickets,
        "world_before": dict(phase.get("world_before") or {}),
        "world_after": dict(phase.get("world_after") or {}),
    }


def _gate_off_refusal(view: Mapping[str, Any]) -> dict[str, Any]:
    """把本 scratch 的正式事实投影成 gate-off 预提交拒绝的形状。

    ``qualifying`` 只在至少一条正式 Action 的 attempt 明确是
    ``PRE_SUBMIT_FAILED``/``GATE_OFF``、零回执、无 APPLIED、零消费票、无 UNKNOWN 时为真。
    只看到 action 引用/非空 id 不够——同一次拒绝还要世界位置与 Body 姿态不变。
    """

    actions = [dict(row) for row in (view.get("actions") or ())]
    summary: dict[str, Any] = {
        "actions": len(actions), "action_status": None, "attempts": 0,
        "attempt_statuses": [], "attempt_error_classes": [], "receipts": 0,
        "lifecycle_applied": False, "tickets_total": len(view.get("tickets") or ()),
        "tickets_consumed": 0, "pending_unknown": 0,
    }
    for action in actions:
        action_id = str(action.get("action_id") or "")
        if str(action.get("status") or "") == "UNKNOWN":
            summary["pending_unknown"] += 1
        attempts = [dict(row) for row in (view.get("attempts") or {}).values()
                    if str(row.get("action_id") or "") == action_id]
        receipts = [dict(row) for row in (view.get("receipts") or {}).values()
                    if str(row.get("action_id") or "") == action_id]
        if not action_id or not attempts:
            continue
        summary.update({
            "action_status": str(action.get("status") or "") or None,
            "attempts": len(attempts),
            "attempt_statuses": sorted({str(row.get("status") or "") for row in attempts}),
            "attempt_error_classes": sorted({str(row.get("error_class") or "")
                                             for row in attempts}),
            "receipts": len(receipts),
            "lifecycle_applied": any(str(row.get("state") or "") == "APPLIED"
                                     for row in (view.get("lifecycle") or ())),
        })
        break
    summary["tickets_consumed"] = sum(
        1 for row in (view.get("tickets") or ())
        if str(row.get("state") or "") == "CONSUMED")
    before = dict(view.get("world_before") or {})
    after = dict(view.get("world_after") or {})
    summary["world_position_unchanged"] = bool(
        before.get("world_state_present") and after.get("world_state_present")
        and before.get("place_id") == after.get("place_id") is not None
        and before.get("area_id") == after.get("area_id"))
    summary["body_unchanged"] = bool(
        before.get("body_present") and after.get("body_present")
        and before.get("pose") == after.get("pose") is not None
        and before.get("orientation") == after.get("orientation"))
    summary["qualifying"] = bool(
        summary["action_status"] == "PREPARED"
        and "PRE_SUBMIT_FAILED" in summary["attempt_statuses"]
        and "GATE_OFF" in summary["attempt_error_classes"]
        and summary["receipts"] == 0
        and summary["lifecycle_applied"] is False
        and summary["tickets_consumed"] == 0
        and summary["pending_unknown"] == 0
        and summary["world_position_unchanged"] is True
        and summary["body_unchanged"] is True)
    return summary


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
    checks.add("D is the new closed identity", (
        str(PROFILE.root) == "/tmp/nyairo-v06-natural-20261103-d"
        and str(PROFILE.authority_dir) == "/tmp/nyairo-v06-natural-20261103-d-memory-authority"
        and str(PROFILE.authority_db)
        == "/tmp/nyairo-v06-natural-20261103-d-memory-authority/authority.db"
        and PROFILE.run_id == "run:v06-world-natural-20261103-d"
        and PROFILE.domain_id == "v06:world-natural:20261103-d"
        and PROFILE.expected_history == 212
        and PROFILE.latest_start is not None
        and PROFILE.deadline.isoformat() == "2026-11-04T00:50:00+08:00"
        and PROFILE.latest_start.isoformat() == "2026-11-04T00:15:00+08:00"
        and g1._profile_window_seconds(PROFILE) == 1800
        and g1._profile_max_ticks(PROFILE) == 900
        and g1._profile_trace_max_records(PROFILE) == 4000),
        {"root": str(PROFILE.root), "run": PROFILE.run_id, "domain": PROFILE.domain_id,
         "history": PROFILE.expected_history,
         "window": g1._profile_window_seconds(PROFILE),
         "ticks": g1._profile_max_ticks(PROFILE),
         "trace": g1._profile_trace_max_records(PROFILE)})
    checks.add("B and C keep their earlier closed values; D does not overwrite them", (
        str(g1.B_PROFILE.root) == "/tmp/nyairo-v06-natural-20261102-b"
        and g1.B_PROFILE.expected_history == 191
        and g1.B_PROFILE.deadline.isoformat() == "2026-11-02T20:00:00+08:00"
        and g1.B_PROFILE.latest_start.isoformat() == "2026-11-02T19:25:00+08:00"
        and g1._profile_window_seconds(g1.B_PROFILE) == g1.WINDOW_SECONDS == 1800
        and g1._profile_max_ticks(g1.B_PROFILE) == g1.MAX_TICKS == 900
        and g1._profile_trace_max_records(g1.B_PROFILE)
        == g1.CANDIDATE_TRACE_MAX_RECORDS == 4000
        and str(g1.C_PROFILE.root) == "/tmp/nyairo-v06-natural-20261102-c"
        and g1.C_PROFILE.expected_history == 192
        and g1.C_PROFILE.deadline.isoformat() == "2026-11-02T23:05:00+08:00"
        and g1.C_PROFILE.latest_start.isoformat() == "2026-11-02T20:00:00+08:00"
        and g1._profile_window_seconds(g1.C_PROFILE) == 10800
        and g1._profile_max_ticks(g1.C_PROFILE) == 3000
        and g1._profile_trace_max_records(g1.C_PROFILE) == 6000),
        {"b_history": g1.B_PROFILE.expected_history,
         "c_history": g1.C_PROFILE.expected_history,
         "c_window": g1._profile_window_seconds(g1.C_PROFILE)})
    checks.add("D covers the whole 1800s / 5s tick window before its loop cap",
               g1._profile_max_ticks(PROFILE) * g1.TICK_INTERVAL_SECONDS >= 1800,
               {"ticks": g1._profile_max_ticks(PROFILE),
                "tick_seconds": g1.TICK_INTERVAL_SECONDS})
    checks.add("D is off like old d/f; e/B/C stay canonical; a stays unset",
               g1._world_intent_gate(PROFILE) == "off"
               and g1._world_intent_gate(g1.E_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.B_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.C_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.D_PROFILE) == "off"
               and g1._world_intent_gate(g1.F_PROFILE) == "off"
               and g1._world_intent_gate(g1.BATCH_PROFILES["a"]) is None,
               {"d": g1._world_intent_gate(PROFILE)})
    checks.add("the ambient switch is D-only; old batches keep no such value",
               g1._ambient_events_env(PROFILE) == "1"
               and g1._ambient_events_env(g1.B_PROFILE) is None
               and g1._ambient_events_env(g1.C_PROFILE) is None
               and g1._ambient_events_env(g1.E_PROFILE) is None
               and g1._ambient_events_env(g1.D_PROFILE) is None,
               {"d": g1._ambient_events_env(PROFILE),
                "c": g1._ambient_events_env(g1.C_PROFILE)})
    checks.add("the old natural set and CLI tuples did not grow",
               g1.NATURAL_PROFILES == frozenset({"d", "e"})
               and g1.BATCH_CHOICES == ("a", "b", "c", "d")
               and g1.CLI_BATCH_CHOICES == ("a", "b", "c", "d", "e")
               and BATCH not in g1.BATCH_CHOICES
               and BATCH not in g1.CLI_BATCH_CHOICES,
               {"natural": sorted(g1.NATURAL_PROFILES),
                "cli": list(g1.CLI_BATCH_CHOICES)})
    return {"identity": actual, "d_root": str(PROFILE.root), "d_run_id": PROFILE.run_id,
            "d_domain_id": PROFILE.domain_id, "d_history": PROFILE.expected_history,
            "d_deadline": PROFILE.deadline.isoformat(),
            "d_latest_start": PROFILE.latest_start.isoformat(),
            "d_window_seconds": g1._profile_window_seconds(PROFILE),
            "d_max_ticks": g1._profile_max_ticks(PROFILE),
            "d_trace_max_records": g1._profile_trace_max_records(PROFILE)}


def _ledger_scope_checks(checks: _Checks) -> dict[str, Any]:
    """纯替身负控：坏账 fail-closed 作用于 B/C/D，旧非 B/C/D 路径与原 HEAD 语义相同。"""

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
    with g1._use_batch(g1.C_PROFILE.key):
        c_lower = g1._ledger_calls(_LedgerStub("corrupt"))
        c_upper = g1._ledger_calls(_LedgerStub("CORRUPT"))
        c_unreadable = g1._ledger_calls(_LedgerStub("UNREADABLE"))
    checks.add("the corrupt-ledger fail-closed covers B/C/D; old batches keep HEAD semantics",
               old_lower == 7 and old_upper is None and old_unreadable == 7
               and new_lower is None and new_upper is None and new_unreadable is None
               and b_lower is None and b_upper is None and b_unreadable is None
               and c_lower is None and c_upper is None and c_unreadable is None,
               {"old": [old_lower, old_upper, old_unreadable],
                "d": [new_lower, new_upper, new_unreadable],
                "b": [b_lower, b_upper, b_unreadable],
                "c": [c_lower, c_upper, c_unreadable]})
    return {"old_lower": old_lower, "old_upper": old_upper,
            "old_unreadable": old_unreadable, "d_lower": new_lower,
            "d_upper": new_upper, "d_unreadable": new_unreadable}


def _budget_checks(checks: _Checks, scratch: Path) -> dict[str, Any]:
    """共享真实预算 20 → 21 发送前拒；坏账同样在发送前拒；累计记账上限是局部算术。"""

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

    accounting = {
        "expected_history": int(PROFILE.expected_history),
        "request_cap": int(g1.REQUEST_CAP),
        "worst_total": int(PROFILE.expected_history) + int(g1.REQUEST_CAP),
        "accounting_cap": int(g1.V06_D_TOTAL_ACCOUNTING_CAP),
        "utc_day_cap": int(g1.UTC_DAY_CAP),
    }
    checks.add("212 history + 20 cap stays under the 1000 accounting ceiling",
               accounting["worst_total"] <= accounting["accounting_cap"]
               and accounting["utc_day_cap"] == 300,
               accounting)
    checks.add("the accounting guard trips only at the ceiling and never for old batches",
               g1._total_accounting_exceeded(787) is False
               and g1._total_accounting_exceeded(788) is True
               and g1._total_accounting_exceeded(788, profile=g1.C_PROFILE) is False,
               {"at_787": g1._total_accounting_exceeded(787),
                "at_788": g1._total_accounting_exceeded(788)})
    return {"reserved": reserved, "calls": calls, "third_refused": third_refused,
            "send_refused": send_refused, "corrupt_state": corrupt_view.get("state"),
            "corrupt_send_refused": corrupt_raised, "accounting": accounting}


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

    wrong_root = (g1._check_root(g1.C_PROFILE.root) or {}).get("reason_code")
    checks.add("another batch's root is refused as an identity mismatch",
               wrong_root == g1.REASON_BATCH_IDENTITY_MISMATCH, wrong_root)

    fake = scratch / "wrong-batch"
    g1._write_json(g1._experiment_dir(fake) / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": g1.C_PROFILE.key,
        "run_id": g1.C_PROFILE.run_id, "domain_id": g1.C_PROFILE.domain_id,
        "root": str(fake), "state_root": str(fake / "one-day"),
        "deadline": g1.C_PROFILE.deadline.isoformat(),
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
    checks.add("a prereg without the D latest-start line is refused",
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
    checks.add("starting after 2026-11-04 00:15 CST is refused before the window is written",
               after_latest.get("reason_code") == g1.REASON_LATEST_START_PASSED
               and not window_written,
               {"reason": after_latest.get("reason_code"), "window": window_written})
    checks.add("starting after 2026-11-04 00:50 CST is refused before the window is written",
               after_deadline.get("reason_code") == g1.REASON_DEADLINE_PASSED
               and not window_written_after,
               {"reason": after_deadline.get("reason_code"),
                "window": window_written_after})
    return {"after_latest": after_latest.get("reason_code"),
            "after_deadline": after_deadline.get("reason_code"),
            "window_written": window_written}


def _trace_capacity_checks(checks: _Checks, scratch: Path) -> dict[str, Any]:
    """trace 行数上限按批解析：D 是 4000，C 仍是 6000，且到达上限后只计 dropped。"""

    trace_root = scratch / "trace-root"
    d_trace = g1.CandidateTrace(trace_root, phase=g1.PHASE_NATURAL, enabled=True)
    with g1._use_batch(g1.C_PROFILE.key):
        c_trace = g1.CandidateTrace(trace_root, phase=g1.PHASE_NATURAL, enabled=True)
    d_trace.records = d_trace.max_records - 1
    d_trace._append(g1.STAGE_ROUND, {"trigger": "natural"})
    d_trace._append(g1.STAGE_ROUND, {"trigger": "natural"})
    checks.add("D trace capacity is 4000 and C stays at 6000",
               d_trace.max_records == 4000 and c_trace.max_records == 6000,
               {"d": d_trace.max_records, "c": c_trace.max_records})
    checks.add("the D trace drops surplus rows past its own cap instead of growing",
               d_trace.records == 4000 and d_trace.dropped_records == 1,
               {"records": d_trace.records, "dropped": d_trace.dropped_records})
    return {"d_max_records": d_trace.max_records, "c_max_records": c_trace.max_records,
            "records": d_trace.records, "dropped": d_trace.dropped_records}


def _cleanup_deadline_checks(checks: _Checks) -> dict[str, Any]:
    """D 的总收尾冗余是 60 秒，窗口是 1800；旧 e 仍是 1800+120。附受控超时负控。"""

    now = 1000.0
    window = float(g1._profile_window_seconds(PROFILE))
    d_limit = g1._natural_phase_timeout(PROFILE, now + window, now=now)
    e_limit = g1._natural_phase_timeout(g1.E_PROFILE, now + g1.WINDOW_SECONDS, now=now)
    checks.add("D stops its phase tree 60s after a 1800s window; old batches keep 120s",
               abs(d_limit - (window + 60.0)) < 1e-9
               and abs(e_limit - (g1.WINDOW_SECONDS + 120.0)) < 1e-9,
               {"d": d_limit, "e": e_limit})
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
    return {"d_seconds": d_limit, "e_seconds": e_limit, "timed_out": timed_out,
            "elapsed": round(elapsed, 3)}


def _window_checks(checks: _Checks, root: Path) -> dict[str, Any]:
    """真实窗口文件必须写 D 自己的 1800 秒，截止以 00:50 为界。"""

    window = g1._read_json(g1._window_path(root))
    try:
        span = (float(window.get("deadline_monotonic") or 0.0)
                - float(window.get("started_monotonic") or 0.0))
    except (TypeError, ValueError):
        span = -1.0
    checks.add("the run window is written with D's 1800s",
               int(window.get("window_seconds") or 0) == 1800
               and abs(span - 1800.0) < 1.0,
               {"window_seconds": window.get("window_seconds"), "span": round(span, 3)})
    checks.add("the window's wall deadline is capped by the 00:50 absolute line",
               PROFILE.deadline.isoformat() in str(window.get("deadline_basis") or ""),
               {"deadline_basis": window.get("deadline_basis"),
                "deadline_wall": window.get("deadline_wall")})
    return {"window_seconds": window.get("window_seconds"), "span": round(span, 3),
            "deadline_wall": window.get("deadline_wall")}


def _ambient_boundary_checks(checks: _Checks, natural: Mapping[str, Any]) -> dict[str, Any]:
    """client 边界上的 ambient 透传：开关/数量/ID，以及 ID 与公开 summary 的原文匹配。"""

    ambient = dict(natural.get("ambient_client") or {})
    count = int(ambient.get("event_count") or 0)
    checks.add("the scripted client actually received at least one D input",
               int(ambient.get("requests") or 0) >= 1, ambient)
    checks.add("the D client input carries the explicit ambient switch",
               ambient.get("ambient_env") == "1", ambient)
    checks.add("the D client input carries the ambient background key",
               ambient.get("ambient_key_present") is True, ambient)
    if count > 0:
        checks.add("the client input carries every ambient id and public summary verbatim",
                   ambient.get("ids_exact_in_input") is True
                   and ambient.get("summaries_exact_in_input") is True
                   and bool(ambient.get("event_ids")),
                   {"count": count, "ids": ambient.get("event_ids"),
                    "ids_ok": ambient.get("ids_exact_in_input"),
                    "summaries_ok": ambient.get("summaries_exact_in_input")})
    else:
        checks.add("no current World ambient event is reported as an honest zero",
                   count == 0 and ambient.get("ids_exact_in_input") is None
                   and ambient.get("summaries_exact_in_input") is None,
                   {"count": count, "status": ambient.get("status")})
    return ambient


def _offline_check() -> dict[str, Any]:
    started = time.monotonic()
    checks = _Checks()
    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    sys.stderr.write("scratch root: %s\n" % scratch)
    sys.stderr.flush()
    repo_before = _stat(REPO_LIFECYCLE)
    # 父进程的 ambient/gate 只能经返回的 env 映射进入子进程树，本进程 `os.environ` 不许被改。
    parent_env_before = {name: os.environ.get(name)
                         for name in (g1.V06_D_AMBIENT_ENV,
                                      "WORLD_BODY_INTENT_MUTATION_GATE")}
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
        observation = dict(prereg.get("observation") or {})
        checks.add("prepare reports zero model calls and zero requests",
                   prepared.get("ok") is True
                   and prepared.get("model_called") is False
                   and prepared.get("request_sent") is False,
                   prepared.get("reason_code"))
        checks.add("prepare records the D identity, 212 history and 1800s caps",
                   prepared.get("batch") == BATCH
                   and prepared.get("run_id") == PROFILE.run_id
                   and prepared.get("domain_id") == PROFILE.domain_id
                   and prereg.get("batch") == BATCH
                   and prereg.get("deadline") == PROFILE.deadline.isoformat()
                   and prereg.get("latest_start") == PROFILE.latest_start.isoformat()
                   and int(caps.get("expected_history") or 0) == PROFILE.expected_history
                   and int(caps.get("request_cap") or 0) == g1.REQUEST_CAP
                   and int(caps.get("window_seconds") or 0) == 1800
                   and int(caps.get("max_ticks") or 0) == 900
                   and int(caps.get("trace_max_records") or 0) == 4000
                   and int(caps.get("check_interval_minutes") or 0) == g1.CHECK_INTERVAL_MINUTES
                   and int(caps.get("utc_day_cap") or 0) == g1.UTC_DAY_CAP
                   and int(caps.get("total_accounting_cap") or 0)
                   == g1.V06_D_TOTAL_ACCOUNTING_CAP,
                   {"caps": caps, "prereg": {k: prereg.get(k) for k in
                                             ("batch", "deadline", "latest_start")}})
        checks.add("prepare records the D ambient switch and off gate as closed facts",
                   observation.get("ambient_events") == "enabled"
                   and observation.get("ambient_env") == g1.V06_D_AMBIENT_ENV
                   and prereg.get("world_intent_gate") == "off",
                   observation)
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
        checks.add("the run reports the D window and tick/trace bounds",
                   int((run.get("window") or {}).get("window_seconds") or 0) == 1800
                   and g1._profile_max_ticks(PROFILE) == 900
                   and g1._profile_trace_max_records(PROFILE) == 4000,
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
        checks.add("the phase child used the standard World with an explicit off gate",
                   world.get("started") is True and world.get("ready") is True
                   and world.get("status") == "READY" and world.get("gate") == "off"
                   and world.get("child_env_gate") == "off",
                   world)
        checks.add("the World child itself carries the D ambient switch",
                   world.get("child_env_ambient") == "1", world)
        checks.add("the phase child wrote only inside the scratch root",
                   isolation.get("gate") == "off"
                   and isolation.get("world_home_is_instance") is True
                   and isolation.get("world_home_is_within_root") is True
                   and isolation.get("registry_is_within_root") is True
                   and isolation.get("lifecycle_is_within_root") is True
                   and isolation.get("lifecycle_exists") is True,
                   isolation)
        checks.add("the scripted START produced a formal action in this phase",
                   bool(start_wakes) and all(bool(row.get("action_id")) for row in start_wakes),
                   {"start_rounds": len(start_wakes)})
        checks.add("the scripted START is refused before submission, not run in the background",
                   bool(start_wakes)
                   and all(str(row.get("outcome") or "") == "STEP_REFUSED"
                           and str(row.get("action_status") or "") == "PREPARED"
                           for row in start_wakes),
                   [{"outcome": row.get("outcome"), "action_status": row.get("action_status")}
                    for row in start_wakes])
        effect = _gate_off_refusal(_artifact_view(root))
        checks.add("the attempt is a PRE_SUBMIT_FAILED/GATE_OFF refusal with zero world effect",
                   effect.get("qualifying") is True, effect)
        payload["formal_effect"] = effect
        payload["ambient_client"] = _ambient_boundary_checks(checks, natural)
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
        checks.add("audit sees the D identity, gate, latest start, window and one phase",
                   audited.get("ok") is True and audited.get("batch") == BATCH
                   and audited.get("run_id") == PROFILE.run_id
                   and audited.get("world_intent_gate") == "off"
                   and audited.get("latest_start") == PROFILE.latest_start.isoformat()
                   and audited.get("window_seconds") == 1800
                   and audited.get("max_ticks") == 900
                   and audited.get("trace_max_records") == 4000
                   and list((audited.get("phases") or {})) == [g1.PHASE_NATURAL],
                   {"ok": audited.get("ok"), "batch": audited.get("batch"),
                    "phases": list((audited.get("phases") or {})),
                    "window_seconds": audited.get("window_seconds")})
        ledger = dict(audited.get("ledger") or {})
        checks.add("the audit ledger belongs to the D run and its own cap",
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
    parent_env_after = {name: os.environ.get(name) for name in parent_env_before}
    checks.add("the parent process environment keeps its own ambient/gate values",
               parent_env_after == parent_env_before,
               {"before": parent_env_before, "after": parent_env_after})
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
    parser = argparse.ArgumentParser(prog="v06_world_natural_d offline-check")
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
