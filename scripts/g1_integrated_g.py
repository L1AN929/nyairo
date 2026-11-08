#!/usr/bin/env python3
"""G1 完整端到端的新封闭入口 g（2026-11-06 监工直接授权）。

它不另造生活循环：身份固定为 ``g1-integrated-g``，prepare/audit/run 全部薄包装
``g1_real_adoption`` 现成的双 PID p1/p2 链、``SubmissionInjector``、``_apply_round``、
``_coverage_from_states`` 与提前武装的 ``_arm_unknown_pre_round``。本文件只做三件事：

1. **封闭身份**：root ``/tmp/nyairo-g1-real-20261106-g``、独立 authority
   ``/tmp/nyairo-g1-real-20261106-g-memory-authority/authority.db``、
   run ``run:g1-real-20261106-g``、域 ``g1:real:20261106-g``、history 507、
   本 run 新增 20、UTC 日 300、累计上限 1000（507 + 20 最坏 527）、
   窗口 3600 真实秒、tick 5 秒、max_ticks 1000/trace 10000、最晚 23:30 启动、
   00:50 停止新请求。任意别的 root / 身份 / 预算参数都没有入口。
2. **完整门投影**：不只按状态顺序倒推，也不只看「曾经 CONTINUE + 曾经 NO_ACTION」。
   它要求 P1/P2 精确同 A/actA、跨 PID 同根同 run、暂停轮零请求、UNKNOWN 在第一轮前
   武装且只被一次 A 提交消费并只对原 operation 对账、CAS 必须真被 owner 以 CONFLICT 拒、
   NO_ACTION 必须是模型自己的枚举且零附带写，**之后还必须真的再续同一件 A/同一 Activity
   并产出新 Action/新 owner 版本**。缺一项或顺序不对就 ``NOT_COVERED``。
3. **离线验收**：``offline-check`` 只在新 ``mkdtemp`` scratch 根里跑，父进程 AF_INET/
   AF_INET6/DNS/create_connection 硬拒，phase 子进程与自有 Supply owner 各装同一份
   局部守卫（AF_UNIX 放行）。它包含最多八个聚合新 case 与一条正式双 PID 同根完整链；
   不重跑旧 37/28/18 套件，也不另起一套原 offline-check。

真实入口仍由父级执行；worker 不 prepare/run 真实根，不加载密钥。stdout 只输出 id、
固定枚举、布尔和计数。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import g1_real_adoption as g1  # noqa: E402

BATCH = g1.G_PROFILE.key
PROFILE = g1.G_PROFILE
EXIT_OK = 0
EXIT_REFUSED = 2
OFFLINE_PREFIX = "g1-integrated-g-offline-"

RESULT_COVERED = "COVERED"
RESULT_NOT_COVERED = "NOT_COVERED"

#: 状态机必须按这个顺序逐段出现；只查出现、不查顺序的拼凑会被 phase_order 挡住。
_ORDER = (
    g1.STATE_AWAIT_CONTINUE,
    g1.STATE_AWAIT_UNKNOWN,
    g1.STATE_AWAIT_CAS,
    g1.STATE_AWAIT_NO_ACTION,
    g1.STATE_AWAIT_CONTINUE_AFTER_QUIET,
    g1.STATE_COVERED,
)


def _bare(value: Any) -> str:
    return g1._bare(value)


def _ordered_subsequence(states: Sequence[Any], required: Sequence[str]) -> bool:
    index = 0
    for state in states:
        if index < len(required) and str(state) == required[index]:
            index += 1
    return index == len(required)


def _find_no_action(rounds: Sequence[Any]) -> Optional[int]:
    """模型自己枚举的 NO_ACTION 轮：无 Action、无 receipt、无附带写。"""

    for index, row in enumerate(rounds):
        wake = dict((row or {}).get("wake") or {})
        if (str(wake.get("decision") or "") == "NO_ACTION"
                and str(wake.get("outcome") or "") == "NO_ACTION"
                and not str(wake.get("action_id") or "")
                and not str(wake.get("step_artifact") or "")):
            return index
    return None


def _post_quiet_continue(rounds: Sequence[Any], start: int, artifact_a: str,
                         activity_ref: str) -> Optional[dict[str, Any]]:
    """NO_ACTION 之后同一 A/同一 actA 的新一次 CONTINUE；没有就是没续。"""

    for row in list(rounds)[start:]:
        wake = dict((row or {}).get("wake") or {})
        if str(wake.get("decision") or "") != "CONTINUE":
            continue
        if _bare(wake.get("step_artifact")) != artifact_a:
            continue
        if str(wake.get("step_activity") or "") != activity_ref:
            continue
        if not str(wake.get("action_id") or ""):
            continue
        if wake.get("owner_version_created") is not True:
            continue
        return wake
    return None


def gate_projection(p1: Mapping[str, Any], p2: Mapping[str, Any],
                    report: Mapping[str, Any]) -> dict[str, Any]:
    """把完整 G1 六段各自摊成独立证据；缺一项就是 NOT_COVERED。

    入参是 phase 子进程**完整**落盘报告（不是裁剪后的安全视图），这样
    ``unknown_armed_before_first_round`` 等字段不会被读丢。输出只含布尔/固定枚举/安全 id。
    """

    artifact_a = _bare(p1.get("artifact_a"))
    activity_ref = str(p1.get("activity_ref") or "")
    first_action = str(p1.get("first_action_id") or "")
    rounds = list(p2.get("rounds") or [])
    states = [str(state) for state in (p2.get("states") or [])]
    final_state = str(p2.get("final_state") or "")
    injections = dict(p2.get("injections") or {})
    concurrent = dict(injections.get("concurrent") or {})
    reconcile = dict(p2.get("reconcile") or {})
    cas_owner = dict(p2.get("cas_owner") or {})

    no_action_index = _find_no_action(rounds)
    post = (_post_quiet_continue(rounds, no_action_index + 1, artifact_a, activity_ref)
            if no_action_index is not None else None)
    post_action = str((post or {}).get("action_id") or "")
    paused = dict(p2.get("paused_round") or {})

    gates = {
        "p1_first_owner_write": bool(
            artifact_a
            and p1.get("write_confirmed") is True
            and p1.get("owner_version_created") is True
            and bool(p1.get("owner_version_id"))
            and _bare(p1.get("receipt_artifact")) == artifact_a
            and bool(p1.get("receipt_activity"))
            and int(p1.get("versions_after_write") or 0) >= 1),
        "operator_interrupt_set": bool(
            (p1.get("pause") or {}).get("paused")
            and (p2.get("pause_at_start") or {}).get("paused")),
        "cross_pid_same_root_same_run": bool(
            p1.get("pid") and p2.get("pid") and str(p1.get("pid")) != str(p2.get("pid"))
            and p1.get("run_id") == p2.get("run_id") == report.get("run_id")
            and str(p1.get("root") or "") == str(p2.get("root") or "") != ""),
        "paused_round_zero_requests": bool(
            p2.get("paused_round_zero_requests") is True
            and str(paused.get("outcome") or "") == "PAUSED_BY_OPERATOR"
            and int(paused.get("llm_calls") or 0) == 0),
        "same_work_anchor": bool(
            artifact_a and _bare(p2.get("artifact_a")) == artifact_a
            and activity_ref and str(p2.get("activity_ref") or "") == activity_ref),
        "unknown_armed_before_first_round": (
            p2.get("unknown_armed_before_first_round") is True),
        "unknown_dropped_and_reconciled": bool(
            int(injections.get("unknown_fired") or 0) == 1
            and str(reconcile.get("result") or "") in g1.RECONCILE_SUCCESS_RESULTS
            and str(reconcile.get("previous_status") or "") == "UNKNOWN"
            and reconcile.get("evidence_verified") is True
            and int(reconcile.get("evidence_count") or 0) >= 1
            and p2.get("unknown_versions_after") == reconcile.get("versions_after")),
        "cas_real_conflict": bool(
            int(injections.get("concurrent_fired") or 0) == 1
            and concurrent.get("applied") is True
            and bool(concurrent.get("head_after"))
            and str(concurrent.get("head_after")) != str(concurrent.get("head_before") or "")
            and str(cas_owner.get("owner_status") or "") == g1.CAS_OWNER_CONFLICT),
        "no_action_zero_write": no_action_index is not None,
        "continue_after_no_action": bool(
            post is not None and final_state == g1.STATE_COVERED
            and bool(post_action) and post_action != first_action),
        "phase_order_observed": _ordered_subsequence(states, _ORDER),
        "harness_coverage_complete": bool(
            (p2.get("coverage") or {}).get("g1_chain_complete") is True),
    }
    missing = [name for name, ok in gates.items() if not ok]
    return {
        "result": RESULT_COVERED if not missing else RESULT_NOT_COVERED,
        "gates": {name: bool(ok) for name, ok in gates.items()},
        "missing": [str(name) for name in missing if name in _GATE_NAMES],
        "anchor": {"artifact_a": _safe_id(artifact_a),
                   "activity_ref": _safe_id(activity_ref),
                   "first_action_id": _safe_id(first_action),
                   "continue_after_quiet_action_id": _safe_id(post_action),
                   "cas_action_status": _safe_enum((cas_owner or {}).get("owner_status"))},
        "pids": {"p1": _safe_int(p1.get("pid")), "p2": _safe_int(p2.get("pid"))},
        "states": [state if state in _KNOWN_STATES else "UNKNOWN" for state in states],
        "injections": {"unknown_fired": _safe_int(injections.get("unknown_fired")) or 0,
                       "concurrent_fired": _safe_int(injections.get("concurrent_fired")) or 0,
                       "note": "按既有已登记接缝各一次；人为故障，不是自然发生"},
        "note": "完整 G1 门投影；离线绿不等于真实模型自然采用",
    }


# ---------------------------------------------------------------------------------------------
# 递归白名单：所有终端输出只经过这些定型投影，绝不透传任意 dict / 正文 / 嵌套 tokens
# ---------------------------------------------------------------------------------------------

_KNOWN_STATES = frozenset(_ORDER)
_GATE_NAMES = frozenset({
    "p1_first_owner_write", "operator_interrupt_set", "cross_pid_same_root_same_run",
    "paused_round_zero_requests", "same_work_anchor", "unknown_armed_before_first_round",
    "unknown_dropped_and_reconciled", "cas_real_conflict", "no_action_zero_write",
    "continue_after_no_action", "phase_order_observed", "harness_coverage_complete",
})
_COVERAGE_KEYS = (
    "p1_real_write", "p1_formal_activity", "operator_pause_set",
    "p2_paused_zero_requests", "continue_same_activity", "unknown_reconciled",
    "cas_refused", "no_action_chosen_zero_write", "continue_after_quiet",
    "g1_chain_complete",
)
_ENUM_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_PATH_RE = re.compile(r"^(?:/[A-Za-z0-9._-]+)+$|^[A-Za-z]:[\\/][A-Za-z0-9._\\/-]+$")
_SAFE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_EXC_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ID_RES = (
    re.compile(r"^[a-z]$"),
    re.compile(r"^g1-[a-z0-9-]{1,40}$"),
    re.compile(r"^run:[A-Za-z0-9._-]{1,80}$"),
    re.compile(r"^(?:g1|v06):[A-Za-z0-9:._-]{1,80}$"),
    re.compile(r"^(?:activity|artifact|artifact-version|artifactversion|action|dec|step|"
               r"grant|workspace|subject|owner|op|conversation|actn)(?::[A-Za-z0-9._:-]{1,120})$"),
    re.compile(r"^actv_[0-9a-f]{8,64}$"),
    re.compile(r"^[0-9a-f]{32}$"),
)
_REFUSAL_FIELDS = frozenset({
    "batch", "run_id", "domain_id", "root", "state_root", "deadline", "latest_start",
    "batch.run_id", "batch.domain_id",
})


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _safe_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _safe_enum(value: Any, allowed: Optional[set[str]] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if not _ENUM_RE.match(text):
        return None
    if allowed is not None and text not in allowed:
        return None
    return text


def _safe_choice(value: Any, allowed: Sequence[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text in allowed else None


def _safe_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if _TS_RE.match(text) else None


def _safe_path(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if _PATH_RE.match(text) else None


def _is_safe_text(value: str) -> bool:
    return bool(_ENUM_RE.match(value) or _TS_RE.match(value) or _HEX64_RE.match(value)
                or _PATH_RE.match(value) or any(pattern.match(value) for pattern in _ID_RES))


def _safe_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if _is_safe_text(text) else None


def _safe_exc_type(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if _EXC_TYPE_RE.match(name) else "Exception"


def _safe_coverage(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    out: dict[str, Any] = {key: _safe_bool(row.get(key))
                           for key in _COVERAGE_KEYS if key in row}
    not_covered = row.get("not_covered")
    if isinstance(not_covered, (list, tuple)):
        out["not_covered"] = [str(item) for item in not_covered
                              if str(item) in _COVERAGE_KEYS]
    return out


def _safe_ledger(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    state = str(row.get("state") or "").upper()
    readable = state in {"OK", "ABSENT"}
    return {
        "state": "OK" if readable else "UNKNOWN",
        "run_window_calls": _safe_int(row.get("run_window_calls")) if readable else None,
        "utc_day_calls": _safe_int(row.get("utc_day_calls")) if readable else None,
        "run_window_max": int(g1.REQUEST_CAP),
        "utc_day_max": int(g1.UTC_DAY_CAP),
    }


def _read_ledger(state_root: Any, *,
                 profile: Optional[g1.BatchProfile] = None) -> dict[str, Any]:
    """纯读账本文件本体：不构造 budget、不建目录/锁、不推进任何窗口。"""

    profile = profile or PROFILE

    unknown = {"state": "UNKNOWN", "run_window_calls": None, "utc_day_calls": None,
               "run_window_max": int(g1.REQUEST_CAP),
               "utc_day_max": int(g1.UTC_DAY_CAP)}
    root = _safe_path(state_root)
    if root is None:
        return dict(unknown)
    try:
        text = (Path(root) / "life_request_budget.json").read_text(encoding="utf-8")
    except OSError:
        return dict(unknown)
    try:
        data = json.loads(text)
    except ValueError:
        return dict(unknown)
    if (not isinstance(data, dict)
            or data.get("schema_version") != "life.request_budget.v1"
            or data.get("kind") != "life_request_budget"):
        return dict(unknown)
    days = data.get("days")
    windows = data.get("windows")
    active = data.get("active_day")
    if not isinstance(days, dict) or not isinstance(windows, dict) \
            or not isinstance(active, str):
        return dict(unknown)
    window = windows.get(profile.run_id)
    if window is not None and not isinstance(window, dict):
        return dict(unknown)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day = max(active, today) if active else today

    def _count(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("bad count")
        return value

    try:
        utc_calls = _count(days.get(day, 0))
        run_calls = _count((window or {}).get("calls", 0))
    except ValueError:
        return dict(unknown)
    return {"state": "OK", "run_window_calls": run_calls, "utc_day_calls": utc_calls,
            "run_window_max": int(g1.REQUEST_CAP), "utc_day_max": int(g1.UTC_DAY_CAP)}


def _safe_window(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    if not row:
        return {"present": False}
    present = _safe_bool(row.get("present"))
    return {"present": bool(row) if present is None else present,
            "run_id": _safe_id(row.get("run_id")),
            "window_seconds": _safe_int(row.get("window_seconds")),
            "started_wall": _safe_timestamp(row.get("started_wall")),
            "deadline_wall": _safe_timestamp(row.get("deadline_wall"))}


def _safe_injections(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    out: dict[str, Any] = {"unknown_fired": _safe_int(row.get("unknown_fired")),
                           "concurrent_fired": _safe_int(row.get("concurrent_fired"))}
    concurrent = row.get("concurrent")
    if isinstance(concurrent, Mapping):
        out["concurrent"] = {"applied": _safe_bool(concurrent.get("applied")),
                             "head_before": _safe_id(concurrent.get("head_before")),
                             "head_after": _safe_id(concurrent.get("head_after"))}
    return out


def _safe_pause(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {"paused": _safe_bool(row.get("paused")),
            "revision": _safe_int(row.get("revision"))}


def _safe_reconcile(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {"action_id": _safe_id(row.get("action_id")),
            "result": _safe_enum(row.get("result")),
            "previous_status": _safe_enum(row.get("previous_status")),
            "evidence_count": _safe_int(row.get("evidence_count")),
            "evidence_verified": _safe_bool(row.get("evidence_verified")),
            "versions_before": _safe_int(row.get("versions_before")),
            "versions_after": _safe_int(row.get("versions_after"))}


def _safe_cas_owner(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {"action_id": _safe_id(row.get("action_id")),
            "owner_status": _safe_enum(row.get("owner_status"))}


def _safe_cas_evidence(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {"injected": _safe_bool(row.get("injected")),
            "applied": _safe_bool(row.get("applied")),
            "head_advanced": _safe_bool(row.get("head_advanced")),
            "owner_conflict": _safe_bool(row.get("owner_conflict"))}


def _safe_model(value: Any) -> dict[str, Any]:
    """模型参数只回固定布尔；endpoint / key / provider 原值一律不出现在终端。"""

    row = value if isinstance(value, Mapping) else {}
    return {"present": bool(row),
            "ok": _safe_bool(row.get("ok")),
            "provider_matches": str(row.get("provider") or "") == str(g1.MODEL_PROVIDER),
            "model_matches": str(row.get("model") or "") == str(g1.MODEL_NAME),
            "api_key_present": _safe_bool(row.get("api_key_present"))}


def _safe_close(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    out: dict[str, Any] = {}
    for key in ("pid", "exit_code"):
        number = _safe_int(row.get(key))
        if number is not None:
            out[key] = number
    for key in ("socket_live_after", "socket_exists_after", "closed"):
        flag = _safe_bool(row.get(key))
        if flag is not None:
            out[key] = flag
    socket_path = _safe_path(row.get("socket_path"))
    if socket_path is not None:
        out["socket_path"] = socket_path
    stop_line = row.get("graceful_stop_line")
    if isinstance(stop_line, Mapping):
        out["graceful_stop_line"] = {
            "seen": _safe_bool(stop_line.get("seen")),
            "signal": _safe_int(stop_line.get("signal")),
            "socket_handling": _safe_enum(stop_line.get("socket_handling")),
            "drained": _safe_bool(stop_line.get("drained")),
            "inflight": _safe_int(stop_line.get("inflight")),
        }
    return out


def _safe_wake(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    out: dict[str, Any] = {}
    for key in ("decision", "outcome", "action_status", "step_outcome"):
        enum = _safe_enum(row.get(key))
        if enum is not None:
            out[key] = enum
    for key in ("action_id", "step_artifact", "step_activity", "owner_version_id",
                "decision_id"):
        ident = _safe_id(row.get(key))
        if ident is not None:
            out[key] = ident
    for key in ("owner_version_created",):
        flag = _safe_bool(row.get(key))
        if flag is not None:
            out[key] = flag
    # The writer's legal ``no_change`` block: only its reason enum and two booleans are known
    # shapes; anything else is dropped rather than passed through.
    work_no_change = row.get("work_no_change")
    if isinstance(work_no_change, Mapping):
        shaped: dict[str, Any] = {}
        reason = _safe_enum(work_no_change.get("reason_code"))
        if reason is not None:
            shaped["reason_code"] = reason
        for flag_key in ("owner_called", "version_created"):
            flag = _safe_bool(work_no_change.get(flag_key))
            if flag is not None:
                shaped[flag_key] = flag
        if shaped:
            out["work_no_change"] = shaped
    calls = _safe_int(row.get("llm_calls"))
    if calls is not None:
        out["llm_calls"] = calls
    return out


def _safe_round(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    out: dict[str, Any] = {"wake": _safe_wake(row.get("wake"))}
    index = _safe_int(row.get("index"))
    if index is not None:
        out["index"] = index
    state_before = _safe_enum(row.get("state_before"), set(_KNOWN_STATES))
    if state_before is not None:
        out["state_before"] = state_before
    return out


_WORK_OBSERVATION_MODE = "g1_integrated_i"
_WORK_OBSERVATION_LIMIT = 20
_WORK_STATES = frozenset({"OK", "PARTIAL", "UNAVAILABLE", "NO_BOUND_WORK", "UNKNOWN"})


def _safe_bounded_count(value: Any) -> Any:
    number = _safe_int(value)
    if number is None or number < 0 or number > 1000:
        return "UNKNOWN"
    return number


def _safe_work_observation(value: Any) -> dict[str, Any]:
    """i 专属 summary 的定型投影：只回允许字段，任何别的东西都不透传。

    旧 profile 的 phase 报告没有这个键，调用方也不会走到这里；g/h 输出形状不变。
    """

    row = value if isinstance(value, Mapping) else {}
    raw_calls = row.get("calls")
    calls: list[dict[str, Any]] = []
    for item in (raw_calls if isinstance(raw_calls, list) else [])[:_WORK_OBSERVATION_LIMIT]:
        if not isinstance(item, Mapping):
            continue
        matches = item.get("expected_work_matches")
        calls.append({
            "phase": _safe_choice(item.get("phase"), ("p1", "p2", "UNKNOWN")),
            "call_index": _safe_int(item.get("call_index")),
            "parse_ok": _safe_bool(item.get("parse_ok")),
            "work_present": _safe_bool(item.get("work_present")),
            "work_state": _safe_choice(item.get("work_state"), tuple(_WORK_STATES)),
            "expected_work_matches": matches if isinstance(matches, bool) else "UNKNOWN",
            "research_present": _safe_bool(item.get("research_present")),
            "practice_present": _safe_bool(item.get("practice_present")),
            "earlier_count": _safe_bounded_count(item.get("earlier_count")),
            "experience_count": _safe_bounded_count(item.get("experience_count")),
        })
    return {
        "mode": _safe_choice(row.get("mode"), (_WORK_OBSERVATION_MODE,)),
        "state": _safe_choice(row.get("state"), ("OBSERVED", "NOT_EXERCISED", "UNKNOWN")),
        "calls": calls,
    }


def _safe_phase(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    out: dict[str, Any] = {}
    phase = _safe_choice(row.get("phase"), ("p1", "p2", "natural"))
    if phase is not None:
        out["phase"] = phase
    for key in ("pid",):
        number = _safe_int(row.get(key))
        if number is not None:
            out[key] = number
    path = _safe_path(row.get("root"))
    if path is not None:
        out["root"] = path
    for key in ("batch", "run_id"):
        ident = _safe_id(row.get(key))
        if ident is not None:
            out[key] = ident
    for key in ("offline", "paused_round_zero_requests", "unknown_armed_before_first_round",
                "write_confirmed", "owner_version_created", "p1_confirmed"):
        flag = _safe_bool(row.get(key))
        if flag is not None:
            out[key] = flag
    for key in ("stop_reason", "final_state"):
        enum = _safe_enum(row.get(key))
        if enum is not None:
            out[key] = enum
    for key in ("artifact_a", "activity_ref", "owner_version_id", "receipt_artifact",
                "receipt_activity", "first_action_id", "state_at_pause", "state_at_start"):
        ident = _safe_id(row.get(key))
        if ident is not None:
            out[key] = ident
    for key in ("versions_after_write", "versions_at_start", "versions_at_end",
                "unknown_versions_after", "round_count"):
        number = _safe_int(row.get(key))
        if number is not None:
            out[key] = number
    if "ledger" in row:
        out["ledger"] = _safe_ledger(row.get("ledger"))
    if "window" in row:
        out["window"] = _safe_window(row.get("window"))
    if "coverage" in row:
        out["coverage"] = _safe_coverage(row.get("coverage"))
    if "injections" in row:
        out["injections"] = _safe_injections(row.get("injections"))
    if "rounds" in row:
        out["rounds"] = [_safe_round(item) for item in (row.get("rounds") or [])
                         if isinstance(item, Mapping)]
    if "paused_round" in row:
        out["paused_round"] = _safe_wake(row.get("paused_round"))
    for key in ("pause", "pause_at_start", "pause_after_resume"):
        if key in row:
            out[key] = _safe_pause(row.get(key))
    if "reconcile" in row and row.get("reconcile") is not None:
        out["reconcile"] = _safe_reconcile(row.get("reconcile"))
    if "cas_owner" in row and row.get("cas_owner") is not None:
        out["cas_owner"] = _safe_cas_owner(row.get("cas_owner"))
    if "cas_evidence" in row and row.get("cas_evidence") is not None:
        out["cas_evidence"] = _safe_cas_evidence(row.get("cas_evidence"))
    if "model" in row:
        out["model"] = _safe_model(row.get("model"))
    if "states" in row:
        out["states"] = [state for state in
                         (_safe_enum(item, set(_KNOWN_STATES))
                          for item in (row.get("states") or [])) if state is not None]
    if "candidate_trace" in row:
        trace = row.get("candidate_trace")
        out["candidate_trace"] = {
            "enabled": _safe_bool((trace or {}).get("enabled")
                                  if isinstance(trace, Mapping) else None)}
    for key in ("close_runtime", "close_supply", "close_world"):
        if key in row:
            out[key] = _safe_close(row.get(key))
    if "work_observation" in row:
        # 只有 i 的 phase 报告有新键；旧 g/h/a..f 走不到这里，输出不变。
        out["work_observation"] = _safe_work_observation(row.get("work_observation"))
    return out


def _safe_material(value: Any) -> dict[str, Any]:
    if value is None:
        return {"present": False}
    row = value if isinstance(value, Mapping) else {}
    digest = str(row.get("sha256") or "")
    return {"present": bool(row),
            "chars": _safe_int(row.get("chars")),
            "sha256": digest if _HEX64_RE.match(digest) else None,
            "artifact_id": _safe_id(row.get("artifact_id")),
            "source_revision": _safe_id(row.get("source_revision"))}


def _expected_caps(profile: Optional[g1.BatchProfile] = None) -> dict[str, int]:
    profile = profile or PROFILE
    return {
        "request_cap": int(g1.REQUEST_CAP),
        "utc_day_cap": int(g1.UTC_DAY_CAP),
        "expected_history": int(profile.expected_history),
        "window_seconds": int(profile.window_seconds),
        "max_ticks": int(profile.max_ticks),
        "trace_max_records": int(profile.trace_max_records),
        "check_interval_minutes": int(g1.CHECK_INTERVAL_MINUTES),
        "total_accounting_cap": int(g1.G_TOTAL_ACCOUNTING_CAP),
    }


def _safe_caps(_value: Any,
               *, profile: Optional[g1.BatchProfile] = None) -> dict[str, int]:
    # 只回本批的固定值；文件里的原始 caps 不作为透传来源。
    return _expected_caps(profile)


def _safe_refusal(refusal: Any, *,
                  batch: Optional[str] = None) -> dict[str, Any]:
    row = refusal if isinstance(refusal, Mapping) else {}
    out: dict[str, Any] = {"ok": False,
                           "reason_code": _safe_enum(row.get("reason_code")),
                           "batch": batch or BATCH, "files_written": []}
    root = _safe_path(row.get("root"))
    if root is not None:
        out["root"] = root
    field = row.get("field")
    if isinstance(field, str) and field in _REFUSAL_FIELDS:
        out["field"] = field
    return out


def _caps_refusal(prereg: Mapping[str, Any], *,
                  profile: Optional[g1.BatchProfile] = None) -> Optional[dict[str, Any]]:
    profile = profile or PROFILE
    batch = profile.key
    caps = prereg.get("caps")
    if not isinstance(caps, Mapping):
        return _safe_refusal({"reason_code": g1.REASON_BATCH_IDENTITY_MISMATCH},
                             batch=batch)
    for key, wanted in _expected_caps(profile).items():
        if caps.get(key) != wanted:
            return _safe_refusal({"reason_code": g1.REASON_BATCH_IDENTITY_MISMATCH},
                                 batch=batch)
    if str(prereg.get("world_intent_gate") or "") != "off":
        return _safe_refusal({"reason_code": g1.REASON_BATCH_IDENTITY_MISMATCH},
                             batch=batch)
    return None


def _safe_detail(value: Any, depth: int = 0) -> Any:
    """``_check`` 失败时只放固定 enum / 布尔 / 计数 / 已知形状 id；不截断正文充当安全。"""

    if depth > 8:
        return "<depth>"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value if _is_safe_text(value) else "<text>"
    if isinstance(value, (list, tuple)):
        return [_safe_detail(item, depth + 1) for item in value]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SAFE_KEY_RE.match(key):
                out[key] = _safe_detail(item, depth + 1)
        return out
    return "<obj>"


# ---------------------------------------------------------------------------------------------
# 纯数据反例：投影层必须拒绝错答案；不重跑链
# ---------------------------------------------------------------------------------------------


def _complete_fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """一条形状完整的 p1/p2 证据，只用于**投影逻辑**的正控与变体，不冒真实链。"""

    p1 = {
        "artifact_a": "art-a", "activity_ref": "activity:act-a",
        "first_action_id": "act-1", "write_confirmed": True,
        "owner_version_created": True, "owner_version_id": "artifact-version:aaaa",
        "receipt_artifact": "art-a", "receipt_activity": "activity:act-a",
        "versions_after_write": 1, "pause": {"paused": True},
        "pid": 111, "root": "/tmp/g1-fixture", "run_id": PROFILE.run_id,
    }
    rounds = [
        {"wake": {"decision": "CONTINUE", "outcome": "ACTED", "action_id": "act-2",
                  "action_status": "UNKNOWN", "step_artifact": "art-a",
                  "step_activity": "activity:act-a", "owner_version_created": True,
                  "llm_calls": 1}},
        {"wake": {"decision": "CONTINUE", "outcome": "ACTED", "action_id": "act-3",
                  "action_status": "FAILED", "step_artifact": "art-a",
                  "step_activity": "activity:act-a", "owner_version_created": False,
                  "llm_calls": 1}},
        {"wake": {"decision": "NO_ACTION", "outcome": "NO_ACTION", "action_id": "",
                  "step_artifact": "", "step_activity": "", "llm_calls": 1}},
        {"wake": {"decision": "CONTINUE", "outcome": "ACTED", "action_id": "act-4",
                  "step_artifact": "art-a", "step_activity": "activity:act-a",
                  "owner_version_created": True, "llm_calls": 1}},
    ]
    p2 = {
        "artifact_a": "art-a", "activity_ref": "activity:act-a",
        "pause_at_start": {"paused": True},
        "paused_round": {"outcome": "PAUSED_BY_OPERATOR", "llm_calls": 0},
        "paused_round_zero_requests": True,
        "unknown_armed_before_first_round": True,
        "injections": {"unknown_fired": 1, "concurrent_fired": 1,
                       "concurrent": {"applied": True, "head_before": "h1",
                                      "head_after": "h2"}},
        "reconcile": {"action_id": "act-2", "result": "CONFIRMED_SUCCEEDED",
                      "previous_status": "UNKNOWN", "evidence_count": 1,
                      "evidence_verified": True, "versions_after": 2},
        "unknown_versions_after": 2,
        "cas_owner": {"owner_status": "CONFLICT"},
        "states": list(_ORDER),
        "final_state": g1.STATE_COVERED,
        "coverage": {"g1_chain_complete": True},
        "rounds": rounds, "pid": 222, "root": "/tmp/g1-fixture",
        "run_id": PROFILE.run_id,
    }
    report = {"run_id": PROFILE.run_id, "root": "/tmp/g1-fixture"}
    return p1, p2, report


def _negative_result(mutate: Callable[[dict[str, Any], dict[str, Any]], None]) -> str:
    p1, p2, report = _complete_fixture()
    p1, p2 = copy.deepcopy(p1), copy.deepcopy(p2)
    mutate(p1, p2)
    return gate_projection(p1, p2, report)["result"]


def _neg_other_work(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["artifact_a"] = "artifact:art-b"
    for row in p2["rounds"]:
        row["wake"]["step_artifact"] = "artifact:art-b"


def _neg_other_activity(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["activity_ref"] = "activity:other"
    for row in p2["rounds"]:
        row["wake"]["step_activity"] = "activity:other"


def _neg_old_receipt(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["rounds"][-1]["wake"]["owner_version_created"] = False


def _neg_not_armed(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["unknown_armed_before_first_round"] = False


def _neg_no_action_wrote(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["rounds"][2]["wake"]["action_id"] = "act-attached"


def _neg_wrong_order(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["states"] = [g1.STATE_AWAIT_CONTINUE, g1.STATE_AWAIT_UNKNOWN,
                    g1.STATE_AWAIT_NO_ACTION, g1.STATE_AWAIT_CONTINUE_AFTER_QUIET,
                    g1.STATE_COVERED]


def _neg_no_post_quiet(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["rounds"] = p2["rounds"][:-1]
    p2["final_state"] = g1.STATE_AWAIT_CONTINUE_AFTER_QUIET
    p2["states"] = list(_ORDER[:-1])


def _neg_cas_not_conflict(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["cas_owner"]["owner_status"] = "FAILED"


def _neg_unreconciled(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["reconcile"]["result"] = "UNKNOWN"


def _neg_no_harness_coverage(_p1: dict[str, Any], p2: dict[str, Any]) -> None:
    p2["coverage"]["g1_chain_complete"] = False


def gate_negatives() -> dict[str, str]:
    """投影层的十个负控；每个都必须 NOT_COVERED。"""

    return {
        "complete_control": _negative_result(lambda _a, _b: None),
        "other_work": _negative_result(_neg_other_work),
        "other_activity": _negative_result(_neg_other_activity),
        "old_receipt_replay": _negative_result(_neg_old_receipt),
        "unknown_not_armed": _negative_result(_neg_not_armed),
        "no_action_with_attached_write": _negative_result(_neg_no_action_wrote),
        "phase_order_broken": _negative_result(_neg_wrong_order),
        "no_continue_after_quiet": _negative_result(_neg_no_post_quiet),
        "cas_not_conflict": _negative_result(_neg_cas_not_conflict),
        "unknown_unreconciled": _negative_result(_neg_unreconciled),
        "harness_coverage_missing": _negative_result(_neg_no_harness_coverage),
    }


# ---------------------------------------------------------------------------------------------
# 只读元字段
# ---------------------------------------------------------------------------------------------


def _root_path(root: Path | str) -> Path:
    return Path(str(root)).expanduser()


def _full_phase(root: Path | str, phase: str) -> dict[str, Any]:
    return g1._read_json(g1._experiment_dir(root) / (g1.PHASE_FILENAME % phase))


def _projection_for_root(root: Path | str,
                         report: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    p1 = _full_phase(root, "p1")
    p2 = _full_phase(root, "p2")
    run_report = dict(report or g1._read_json(
        g1._experiment_dir(root) / g1.RUN_REPORT_FILENAME))
    return gate_projection(p1, p2, run_report)


def status(root: Path | str, *,
           expected_root: Optional[Path | str] = None,
           profile: Optional[g1.BatchProfile] = None) -> dict[str, Any]:
    """纯读元字段：不构造预算、不推进实例、不创建任何锁。

    直接读 prereg / batch / phase / 运行报告和账本文件本体；``budget.view`` 那种
    会 ``mkdir`` 锁文件的路径一次都不走。``expected_root`` 只给离线 scratch 夹具
    收窄期望根；真实入口缺省仍按所选身份的预登记根判定，不接受任意 formal root。
    ``profile`` 只给 h 入口复用同一读面；缺省仍是 g。调用方须已在对应
    ``g1._use_batch`` 里，否则嵌套的身份校验会按默认 a 批拒绝。
    """

    profile = profile or PROFILE
    batch_key = profile.key
    root_path = _root_path(root)
    refusal = g1._check_root(root_path, expected=expected_root)
    if refusal is not None:
        return _safe_refusal(refusal, batch=batch_key)
    experiment = g1._experiment_dir(root_path)
    prereg = g1._read_json(experiment / g1.PREREG_FILENAME)
    if not prereg:
        return {"ok": False, "reason_code": g1.REASON_NOT_PREPARED,
                "batch": batch_key, "root": _safe_path(root_path), "files_written": []}
    identity_refusal = g1._verify_instance_identity(root_path)
    if identity_refusal is not None:
        return _safe_refusal(identity_refusal, batch=batch_key)
    caps_refusal = _caps_refusal(prereg, profile=profile)
    if caps_refusal is not None:
        return caps_refusal
    batch = g1._read_json(experiment / g1.BATCH_FILENAME)
    report = g1._read_json(experiment / g1.RUN_REPORT_FILENAME)
    phases = {}
    for phase in ("p1", "p2"):
        full = _full_phase(root_path, phase)
        phases[phase] = _safe_phase(full) if full else None
    projection = _projection_for_root(root_path, report) if report else None
    return {
        "ok": True,
        "root": _safe_path(root_path),
        "batch": batch_key,
        "run_id": profile.run_id,
        "domain_id": profile.domain_id,
        "state": _safe_choice(batch.get("state"), ("prepared", "running", "finished")),
        "prepared_at": _safe_timestamp(batch.get("prepared_at")),
        "started_at": _safe_timestamp((batch.get("run") or {}).get("started_at")),
        "result": _safe_choice(report.get("result") or batch.get("result"),
                               (RESULT_COVERED, RESULT_NOT_COVERED, "OBSERVED")),
        "stop_reason": _safe_enum(report.get("stop_reason")),
        "coverage": _safe_coverage(report.get("coverage") or batch.get("coverage")),
        "ledger": _read_ledger(prereg.get("state_root"), profile=profile),
        "window": _safe_window(g1._window_view(root_path)),
        "caps": _safe_caps(prereg.get("caps"), profile=profile),
        "phases": phases,
        "g1_gate": projection,
        "deadline": profile.deadline.isoformat(),
        "latest_start": (profile.latest_start.isoformat()
                         if profile.latest_start is not None else None),
        "window_seconds": int(profile.window_seconds),
        "max_ticks": int(profile.max_ticks),
        "trace_max_records": int(profile.trace_max_records),
        "total_accounting_cap": int(g1.G_TOTAL_ACCOUNTING_CAP),
        "world_intent_gate": "off",
    }


def audit_readonly(root: Path | str, *,
                   expected_root: Optional[Path | str] = None,
                   profile: Optional[g1.BatchProfile] = None) -> dict[str, Any]:
    """新入口的 audit 与 status 共用同一条纯读路径；旧 ``g1.audit`` 默认不动。"""

    return status(root, expected_root=expected_root, profile=profile)


def _safe_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """prepare / run 的真实元结果：逐字段定型，detail / model_params / 任意嵌套不出现。"""

    row = result if isinstance(result, Mapping) else {}
    out: dict[str, Any] = {}
    flag = _safe_bool(row.get("ok"))
    if flag is not None:
        out["ok"] = flag
    reason = _safe_enum(row.get("reason_code"))
    if reason is not None:
        out["reason_code"] = reason
    for key in ("batch", "run_id", "domain_id"):
        ident = _safe_id(row.get(key))
        if ident is not None:
            out[key] = ident
    for key in ("root", "state_root"):
        path = _safe_path(row.get(key))
        if path is not None:
            out[key] = path
    state = _safe_enum(row.get("state"))
    if state is not None:
        out["state"] = state
    for key in ("started_at", "deadline", "latest_start"):
        stamp = _safe_timestamp(row.get(key))
        if stamp is not None:
            out[key] = stamp
    stop_reason = _safe_enum(row.get("stop_reason"))
    if stop_reason is not None:
        out["stop_reason"] = stop_reason
    verdict = _safe_choice(row.get("result"),
                           (RESULT_COVERED, RESULT_NOT_COVERED, "OBSERVED"))
    if verdict is not None:
        out["result"] = verdict
    for key in ("model_called", "request_sent", "offline"):
        bool_flag = _safe_bool(row.get(key))
        if bool_flag is not None:
            out[key] = bool_flag
    if "coverage" in row:
        out["coverage"] = _safe_coverage(row.get("coverage"))
    if "ledger" in row:
        out["ledger"] = _safe_ledger(row.get("ledger"))
    if "window" in row:
        out["window"] = _safe_window(row.get("window"))
    if "injections" in row:
        out["injections"] = _safe_injections(row.get("injections"))
    if "material" in row:
        out["material"] = _safe_material(row.get("material"))
    if "control" in row:
        out["control"] = _safe_material(row.get("control"))
    for key in ("p1", "p2", "natural"):
        if key in row:
            out[key] = _safe_phase(row.get(key))
    if "candidate_trace" in row:
        trace = row.get("candidate_trace")
        out["candidate_trace"] = {
            "enabled": _safe_bool(trace.get("enabled") if isinstance(trace, Mapping) else None)}
    return out


# ---------------------------------------------------------------------------------------------
# 离线夹具根守卫
# ---------------------------------------------------------------------------------------------


def _is_scratch_root(path: Any, *, prefix: str = OFFLINE_PREFIX) -> bool:
    if not path:
        return False
    try:
        resolved = str(Path(str(path)).resolve())
        temp_root = str(Path(tempfile.gettempdir()).resolve())
    except OSError:
        return False
    # 与 g1_next_live 同一收紧方式：路径必须落在系统临时目录下，且祖先名带本批前缀。
    return (resolved.startswith(temp_root + os.sep)
            and prefix.strip("-") in resolved)


def _expected_root(root: Path, offline: bool) -> Optional[Path]:
    return root if offline else None


def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, indent=1,
                                sort_keys=True, default=str) + "\n")
    sys.stdout.flush()
    return int(code)


def _refuse(reason: str, *, root: Any = None,
            exception_type: Optional[str] = None) -> int:
    payload: dict[str, Any] = {"ok": False,
                               "reason_code": _safe_enum(reason) or "REFUSED",
                               "batch": BATCH, "files_written": []}
    path = _safe_path(root)
    if path is not None:
        payload["root"] = path
    if isinstance(exception_type, str) and _EXC_TYPE_RE.match(exception_type):
        payload["exception_type"] = exception_type
    return _emit(payload, code=EXIT_REFUSED)


# ---------------------------------------------------------------------------------------------
# CLI：prepare / audit / run / status
# ---------------------------------------------------------------------------------------------


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_g prepare")
    parser.add_argument("--root", required=True)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = _root_path(args.root)
    if args.offline_fixture and not _is_scratch_root(root):
        return _refuse("OFFLINE_PATH_REFUSED", root=root)
    with g1._use_batch(BATCH):
        if args.offline_fixture:
            with g1.offline_clock(), g1._http_forbidden():
                result = g1.prepare(root, expected_root=_expected_root(root, True),
                                    offline=True)
        else:
            result = g1.prepare(root)
    payload = _safe_result(result)
    payload["batch"] = BATCH
    return _emit(payload, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_g audit")
    parser.add_argument("--root", required=True)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = _root_path(args.root)
    if args.offline_fixture and not _is_scratch_root(root):
        return _refuse("OFFLINE_PATH_REFUSED", root=root)
    with g1._use_batch(BATCH):
        result = audit_readonly(root, expected_root=_expected_root(root,
                                                                   bool(args.offline_fixture)))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_g run")
    parser.add_argument("--root", required=True)
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = _root_path(args.root)
    if args.offline_fixture and not _is_scratch_root(root):
        return _refuse("OFFLINE_PATH_REFUSED", root=root)
    with g1._use_batch(BATCH):
        if args.offline_fixture:
            with g1.offline_clock(), g1._http_forbidden():
                report = g1.start_run(root, expected_root=_expected_root(root, True),
                                      execute_approved=bool(args.execute_approved),
                                      offline=True)
        else:
            report = g1.start_run(root, execute_approved=bool(args.execute_approved),
                                  offline=False)
    payload = _safe_result(report)
    payload["batch"] = BATCH
    payload["g1_gate"] = _projection_for_root(root, report)
    return _emit(payload, code=EXIT_OK if report.get("ok") else EXIT_REFUSED)


def cmd_status(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_g status")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(list(argv))
    with g1._use_batch(BATCH):
        result = status(_root_path(args.root))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


# ---------------------------------------------------------------------------------------------
# offline-check：8 个聚合新 case + 一条正式双 PID 同根完整链
# ---------------------------------------------------------------------------------------------

_CHECKS: list[tuple[str, bool, Any]] = []


def _check(name: str, condition: Any, detail: Any = None) -> None:
    _CHECKS.append((name, bool(condition), detail))
    print("CHECK %-72s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        # 只放定型后的枚举/布尔/计数/已知 id，不靠截断正文充当安全。
        print("  detail:", json.dumps(_safe_detail(detail), ensure_ascii=False,
                                      sort_keys=True))


def _run_new_tests(pattern: str = "test_g1_integrated_g.py") -> dict[str, Any]:
    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(ROOT / "tests"), pattern=pattern)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - only a fixed code + exception type
        return {"ok": False, "run": 0, "skipped": 0,
                "reason": "TEST_COLLECTION_FAILED",
                "exception_type": _safe_exc_type(exc)}
    try:
        with open(os.devnull, "w") as sink:
            result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - only a fixed code + exception type
        return {"ok": False, "run": 0, "skipped": 0,
                "reason": "TEST_RUNNER_FAILED",
                "exception_type": _safe_exc_type(exc)}
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "skipped": len(result.skipped), "failures": len(result.failures),
            "errors": len(result.errors)}


def _corrupt_ledger_refused(scratch: Path) -> dict[str, Any]:
    """坏账本必须 fail-closed：读不出来不等于 0 次，不能因此放行一次发送。"""

    from life_request_budget import budget_path

    state = scratch / "corrupt-ledger"
    state.mkdir(parents=True, exist_ok=True)
    budget_path(state).write_text("{not-json", encoding="utf-8")
    budget = g1._budget(state)
    calls = g1._ledger_calls(budget)
    sends: list[int] = []
    gate_reason = ""

    def _inner(request: Any, *, schema_retry_index: int = 0) -> Any:
        sends.append(1)
        raise AssertionError("inner must not be called")

    latch = g1.SendLatch(lambda: False)
    gate = g1.G1CognitionGate(_inner, latch=latch, budget=budget)
    try:
        gate.invoke(object())
    except g1.GateClosed as exc:
        gate_reason = exc.reason_code
    except Exception as exc:  # noqa: BLE001 - the refusal reason is the evidence
        gate_reason = type(exc).__name__
    return {"calls": calls, "gate_reason": gate_reason, "inner_calls": len(sends),
            "pure": _read_ledger(state)}


def _cross_phase_stop(scratch: Path) -> dict[str, Any]:
    """P1 落盘的失败停因对 P2 的新锁同样生效。"""

    stop_root = scratch / "cross-phase-stop"
    g1._live_window(stop_root)
    g1._record_batch_stop(stop_root, g1.REASON_PROVIDER_FAILURE)
    latch = g1.SendLatch(lambda: False)
    stop = g1._loop_stop(stop_root, g1._budget(stop_root / "state"), latch)
    return {"recorded": g1._durable_stop(stop_root), "stop": stop,
            "latch_closed": latch.closed}


def _identity_refusals(scratch: Path) -> dict[str, Any]:
    """错 root / 别的批 prereg / 已占用 authority：都必须在写入前失败关闭。"""

    wrong_root = (g1._check_root(g1.BATCH_PROFILES["a"].root) or {}).get("reason_code")
    a = g1.BATCH_PROFILES["a"]
    foreign_root = scratch / "foreign-prereg"
    g1._write_json(g1._experiment_dir(foreign_root) / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": "a", "run_id": a.run_id,
        "domain_id": a.domain_id, "root": str(foreign_root),
        "state_root": str(foreign_root / "one-day"), "deadline": a.deadline.isoformat(),
    }, private=True)
    foreign = audit_readonly(foreign_root, expected_root=foreign_root)
    taken_root = scratch / "authority-taken" / "instance-root"
    authority = taken_root.parent / ("%s-memory-authority" % taken_root.name)
    authority.mkdir(parents=True, exist_ok=True)
    (authority / "authority.db").write_text("pre-existing authority", encoding="utf-8")
    taken = g1.prepare(taken_root, expected_root=taken_root, offline=True)
    return {"wrong_root": wrong_root,
            "foreign_prereg": foreign.get("reason_code"),
            "authority_taken": {"reason_code": taken.get("reason_code"),
                                "root_created": taken_root.exists()}}


def _default_profiles_unchanged() -> dict[str, Any]:
    """只做机械断言：a/b/c 原始映射、旧 CLI 元组、f 的封闭值一个都不动。"""

    a = g1.BATCH_PROFILES["a"]
    f = g1.F_PROFILE
    return {
        "abc_keys": sorted(g1.BATCH_PROFILES),
        "batch_choices": list(g1.BATCH_CHOICES),
        "cli_batch_choices": list(g1.CLI_BATCH_CHOICES),
        "a_root": str(a.root), "a_run": a.run_id, "a_domain": a.domain_id,
        "a_history": a.expected_history, "a_deadline": a.deadline.isoformat(),
        "f_root": str(f.root), "f_run": f.run_id, "f_domain": f.domain_id,
        "f_history": f.expected_history, "f_deadline": f.deadline.isoformat(),
        "f_window": g1._profile_window_seconds(f),
        "f_max_ticks": g1._profile_max_ticks(f),
        "g_key": PROFILE.key,
    }


def _offline_body() -> bool:
    checks_before = len(_CHECKS)
    tests = _run_new_tests()
    _check("the new module really collects tests (run > 0, skip = 0)",
           tests.get("ok") is True and int(tests.get("run") or 0) > 0
           and int(tests.get("skipped") or 0) == 0, tests)

    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    root = scratch / "instance-root"
    print("scratch root:", scratch)
    with g1._offline_network_guard(scratch), g1._http_forbidden():
        # -- 1. 封闭身份 + 默认 a/b/c/f 不动 ------------------------------------------
        prepared = g1.prepare(root, expected_root=root, offline=True)
        prereg = g1._read_json(g1._experiment_dir(root) / g1.PREREG_FILENAME)
        caps = dict(prereg.get("caps") or {})
        identity = _default_profiles_unchanged()
        refusals = _identity_refusals(scratch)
        _check("g is the only new closed identity and a/b/c/f are unchanged",
               PROFILE.key == "g1-integrated-g"
               and str(PROFILE.root) == "/tmp/nyairo-g1-real-20261106-g"
               and str(PROFILE.authority_dir)
               == "/tmp/nyairo-g1-real-20261106-g-memory-authority"
               and PROFILE.run_id == "run:g1-real-20261106-g"
               and PROFILE.domain_id == "g1:real:20261106-g"
               and PROFILE.expected_history == 507
               and PROFILE.deadline.isoformat() == "2026-11-07T00:50:00+08:00"
               and PROFILE.latest_start is not None
               and PROFILE.latest_start.isoformat() == "2026-11-06T23:30:00+08:00"
               and identity["abc_keys"] == ["a", "b", "c"]
               and identity["batch_choices"] == ["a", "b", "c", "d"]
               and identity["cli_batch_choices"] == ["a", "b", "c", "d", "e"]
               and identity["a_history"] == 98
               and identity["f_history"] == 168
               and identity["f_window"] == 1800
               and identity["f_max_ticks"] == 900
               and refusals["wrong_root"] == g1.REASON_BATCH_IDENTITY_MISMATCH
               and refusals["foreign_prereg"] == g1.REASON_BATCH_IDENTITY_MISMATCH
               and refusals["authority_taken"]["reason_code"]
               == g1.REASON_AUTHORITY_NOT_FRESH
               and refusals["authority_taken"]["root_created"] is False,
               {"identity": identity, "refusals": refusals})

        # -- 2. prepare 零模型/零请求；run 必须显式 --execute-approved ----------------
        gate_off = g1.start_run(root, expected_root=root, execute_approved=False,
                                offline=True)
        _check("prepare is zero-model/zero-request and run needs --execute-approved",
               prepared.get("ok") is True
               and prepared.get("model_called") is False
               and prepared.get("request_sent") is False
               and prepared.get("batch") == BATCH
               and prepared.get("run_id") == PROFILE.run_id
               and prepared.get("domain_id") == PROFILE.domain_id
               and prereg.get("batch") == BATCH
               and prereg.get("deadline") == PROFILE.deadline.isoformat()
               and prereg.get("latest_start") == PROFILE.latest_start.isoformat()
               and prereg.get("memory_authority")
               == str(root.parent / ("%s-memory-authority" % root.name) / "authority.db")
               and caps.get("request_cap") == 20
               and caps.get("utc_day_cap") == 300
               and caps.get("expected_history") == 507
               and caps.get("window_seconds") == 3600
               and caps.get("max_ticks") == 1000
               and caps.get("trace_max_records") == 10000
               and caps.get("total_accounting_cap") == 1000
               and prereg.get("world_intent_gate") == "off"
               and (prereg.get("observation") or {}).get("mode") == "g1_integrated_g"
               and gate_off.get("ok") is False
               and gate_off.get("reason_code") == g1.REASON_EXECUTE_FLAG_MISSING,
               {"prepare": _safe_result(prepared), "gate_off": gate_off, "caps": caps,
                "prereg": {key: prereg.get(key) for key in
                           ("batch", "deadline", "latest_start", "world_intent_gate")}})

        # -- 4. 两个进程共享 20，第 21 次在 reserve 前拒 ------------------------------
        cap_root = scratch / "cap-state"
        cap_root.mkdir(parents=True, exist_ok=True)
        cap = g1._cross_pid_cap(cap_root, batch=BATCH)
        _check("two independent processes share one 20-request cap (21st refused)",
               cap["first_rc"] == 0 and cap["second_rc"] == 0
               and (cap["first_out"] or {}).get("reserved") == 12
               and (cap["second_out"] or {}).get("reserved") == 8
               and cap["third"] != "reserved" and cap["calls"] == 20
               and cap["run_id"] == PROFILE.run_id, cap)

        # -- 5. 坏账 fail-closed + 跨 phase 失败锁不重启 ------------------------------
        corrupt = _corrupt_ledger_refused(scratch)
        durable = _cross_phase_stop(scratch)
        _check("a corrupt ledger is fail-closed and a P1 stop blocks a fresh P2 latch",
               corrupt["calls"] is None and corrupt["inner_calls"] == 0
               and corrupt["gate_reason"] == g1.REASON_LEDGER_UNREADABLE
               and (corrupt.get("pure") or {}).get("state") == "UNKNOWN"
               and (corrupt.get("pure") or {}).get("run_window_calls") is None
               and durable["recorded"] == g1.REASON_PROVIDER_FAILURE
               and durable["stop"] == g1.REASON_PROVIDER_FAILURE
               and durable["latch_closed"] is True, {"corrupt": corrupt,
                                                     "durable": durable})

        # -- 6. 正式双 PID 同根完整链 -------------------------------------------------
        chain = g1.start_run(root, expected_root=root, execute_approved=True,
                             offline=True)
        p1_full = _full_phase(root, "p1")
        p2_full = _full_phase(root, "p2")
        projection = gate_projection(p1_full, p2_full, chain)
        guard = g1._offline_child_guard_report(root)
        again = g1.start_run(root, expected_root=root, execute_approved=True,
                             offline=True)
        _check("the formal dual-PID same-root chain covers every complete G1 gate",
               chain.get("ok") is True
               and projection.get("result") == RESULT_COVERED
               and not projection.get("missing")
               and guard.get("ok") is True and int(guard.get("found") or 0) >= 2
               and again.get("reason_code") == g1.REASON_ALREADY_STARTED,
               {"stop_reason": chain.get("stop_reason"),
                "projection": projection, "guard": guard,
                "no_restart": again.get("reason_code")})

        # -- 7. 投影层负控：错作品/错 Activity/旧 receipt/未武装/顺序/未续 ----------
        negatives = gate_negatives()
        bad = {name: value for name, value in negatives.items()
               if name != "complete_control" and value != RESULT_NOT_COVERED}
        _check("every wrong answer is NOT_COVERED (work/activity/receipt/order/quiet)",
               negatives.get("complete_control") == RESULT_COVERED and not bad,
               {"negatives": negatives, "bad": bad})

        # -- 8. status/audit 纯读安全字段；报告不含正文/密钥/提示词 ------------------
        audited = audit_readonly(root, expected_root=root)
        read_status = status(root, expected_root=root)
        blob = json.dumps({"audit": audited, "status": read_status},
                          ensure_ascii=False, default=str)
        forbidden = [g1.MATERIAL_TEXT, g1.CONTROL_TEXT, "offline-not-a-key", "sk-",
                     "api_key", "prompt", "completion"]
        hits = [token for token in forbidden if token and token in blob]
        sentinel = "PRIVATE_SENTINEL_7f3a"
        injected = {
            "ok": True, "detail": sentinel,
            "model_params": {"base_url": sentinel, "api_key": sentinel},
            "ledger": {"state": "ok", "run_window_calls": 3, "utc_day_calls": 3,
                       "run_window_max": 20, "utc_day_max": 300,
                       "recent_tokens": [sentinel]},
            "window": {"present": True, "deadline_basis": sentinel},
            "p1": {"phase": "p1", "model": {"base_url": sentinel, "api_key": sentinel},
                   "rounds": [{"wake": {"decision": "CONTINUE", "detail": sentinel}}],
                   "reconcile": {"detail": sentinel}},
        }
        leaked = sentinel in json.dumps(_safe_result(injected), ensure_ascii=False,
                                        default=str)
        ledger_keys = sorted(audited.get("ledger") or {})
        _check("status/audit only carry safe fields and the g ledger stays at 0..20",
               audited.get("ok") is True
               and read_status.get("ok") is True
               and read_status.get("batch") == BATCH
               and ledger_keys == ["run_window_calls", "run_window_max", "state",
                                   "utc_day_calls", "utc_day_max"]
               and int((audited.get("ledger") or {}).get("run_window_max") or 0) == 20
               and int((audited.get("ledger") or {}).get("run_window_calls") or 0)
               <= 20
               and not hits and not leaked, {"hits": hits, "leaked": leaked,
                                             "ledger": audited.get("ledger"),
                                             "status_keys": sorted(read_status)})

    failed = [name for name, ok, _detail in _CHECKS if not ok]
    print()
    print("scratch kept:", scratch)
    for name in failed:
        print("FAILED:", name)
    passed = not failed and tests.get("ok") is True
    print("RESULT: %s" % ("PASS" if passed else "FAIL"))
    print("checks: %d (new %d)" % (len(_CHECKS), len(_CHECKS) - checks_before))
    return passed


def offline_check() -> bool:
    with g1._use_batch(BATCH):
        with g1.offline_clock():
            return _offline_body()


def cmd_offline_check(_argv: Sequence[str]) -> int:
    return EXIT_OK if offline_check() else EXIT_REFUSED


COMMANDS = {
    "offline-check": cmd_offline_check,
    "prepare": cmd_prepare,
    "audit": cmd_audit,
    "run": cmd_run,
    "status": cmd_status,
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
    try:
        return int(handler(arguments[1:]))
    except SystemExit:
        # argparse 的正常退出路径原样透传，不吞成成功。
        raise
    except Exception as exc:  # noqa: BLE001 - fixed code + exception type only
        return _refuse("INTERNAL_ERROR", exception_type=type(exc).__name__)


if __name__ == "__main__":
    raise SystemExit(main())
