#!/usr/bin/env python3
"""e 批（世界动作临时许可的一次自然观测）只读安全摘要。

它只做三件事：

1. 复用 ``g1_real_adoption.audit`` 读本批的 prereg/batch/window/phase/ledger；
2. 有 ``--capture-candidates`` 的 trace 时，按既有白名单字段数候选/选择/Action；
3. 只读本实例自己的 World 文件（``world_state.json`` / ``world_journey.json`` /
   ``body_substrate.json``）核对位置、出发/到达与姿态。

边界

* 默认只接受 e 的固定 root；离线测试必须显式 ``--offline-scratch``，否则拒绝。
* 全部只读：不启动/停止任何服务、不调用 maintain/reconcile、不写 owner、不改 ledger。
* 只输出已记录的结构化 ID/枚举/计数/布尔/时间；不输出 prompt、正文、标题、
  自由异常消息、密钥。拿不到的数据写 ``UNKNOWN``，不推理成"没发生"。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"), str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "supply"),
               str(REPO_ROOT / "components" / "world" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 2

SCHEMA = "world.observe.e.summary.v1"
MAX_ROWS = 200
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
WORLD_GATES = frozenset({"canonical", "off"})
WORLD_STATUSES = frozenset({"READY", "NOT_READY", "UNKNOWN"})
DECISIONS = getattr(g1, "_TRACE_DECISIONS", frozenset())
ACTION_STATUSES = getattr(g1, "_TRACE_ACTION_STATUSES", frozenset())
OUTCOMES = getattr(g1, "_TRACE_OUTCOMES", frozenset())
WAKE_STATUSES = getattr(g1, "_TRACE_WAKE_STATUSES", frozenset())
TRIGGERS = getattr(g1, "_TRACE_TRIGGERS", frozenset())


def _enum(value: Any, allowed: frozenset[str]) -> Optional[str]:
    return g1._trace_enum(value, allowed) if allowed else None


def _token(value: Any) -> Optional[str]:
    """位置/姿态这类短标识：只保留安全字符集里的短串，其余一律 None。"""

    text = str(value or "").strip()
    return text if SAFE_TOKEN_RE.match(text) else None


def _count(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _trace_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = g1._experiment_dir(root) / (g1.CANDIDATE_TRACE_FILENAME % g1.PHASE_NATURAL)
    meta: dict[str, Any] = {"path": str(path), "present": path.exists(), "bad_lines": 0,
                            "truncated": False}
    if not path.exists():
        return [], meta
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line_no, line in enumerate(handle):
                if line_no >= g1.CANDIDATE_TRACE_MAX_RECORDS:
                    meta["truncated"] = True
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except ValueError:
                    meta["bad_lines"] += 1
                    continue
                if isinstance(row, Mapping):
                    rows.append(dict(row))
                else:
                    meta["bad_lines"] += 1
    except OSError:
        meta["present"] = False
        meta["unreadable"] = True
    meta["rows"] = len(rows)
    return rows, meta


def _safe_selection(wake: Mapping[str, Any], trigger: Any = None) -> dict[str, Any]:
    """一轮 wake 的只读投影：只留固定枚举/已校验 id；不复制整行。"""

    row: dict[str, Any] = {
        "decision": _enum(wake.get("decision"), DECISIONS),
        "model_decision": wake.get("model_decision") is True,
        "llm_calls": _count(wake.get("llm_calls")),
        "selected_candidate": g1._trace_id(wake.get("selected_candidate_ref"),
                                           getattr(g1, "_TRACE_CANDIDATE_ID_PREFIXES")),
        "action_id": g1._trace_id(wake.get("action_id"), frozenset({"actn"})),
        "action_status": _enum(wake.get("action_status"), ACTION_STATUSES),
        "outcome": _enum(wake.get("outcome"), OUTCOMES),
        "status": _enum(wake.get("status"), WAKE_STATUSES),
        "activity_ref": g1._trace_id(wake.get("activity_ref"),
                                     getattr(g1, "_TRACE_ACTIVITY_ID_PREFIXES")),
    }
    trigger_token = _enum(trigger, TRIGGERS)
    if trigger_token:
        row["trigger"] = trigger_token
    return {name: value for name, value in row.items() if value not in (None, "")}


def _selections_from_trace(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("stage") != g1.STAGE_ROUND:
            continue
        for wake in list(row.get("wakes") or [])[:8]:
            if isinstance(wake, Mapping):
                out.append(_safe_selection(wake, row.get("trigger")))
                if len(out) >= MAX_ROWS:
                    return out
    return out


def _selections_from_phase(phase: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in list(phase.get("rounds") or [])[:MAX_ROWS]:
        if not isinstance(entry, Mapping):
            continue
        wake = entry.get("wake")
        if isinstance(wake, Mapping):
            out.append(_safe_selection(wake, None))
    return out


def _requests_from_trace(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("stage") != g1.STAGE_REQUEST:
            continue
        legal = []
        for raw in list(row.get("legal_decision_enums") or [])[:16]:
            code = _enum(raw, DECISIONS)
            if code:
                legal.append(code)
        world_candidates = sum(
            1 for candidate in (row.get("candidates") or [])
            if isinstance(candidate, Mapping)
            and candidate.get("source_kind") == "WORLD_OPPORTUNITY")
        out.append({"legal_decisions": legal,
                    "candidates": _count(row.get("count") or row.get("total")),
                    "world_candidates": world_candidates})
        if len(out) >= MAX_ROWS:
            break
    return out


def _position(world_home: Path) -> dict[str, Any]:
    path = world_home / "data" / "world" / "world_state.json"
    data = g1._read_json(path)
    if not data:
        return {"status": "UNKNOWN", "why": "world_state.json missing or unreadable"}
    location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
    return {"status": "OBSERVED", "place_id": _token(location.get("place_id")),
            "area_id": _token(location.get("area_id")),
            "world_id": _token(data.get("world_id")),
            "revision": _count(data.get("revision")) or None}


def _journey(world_home: Path) -> dict[str, Any]:
    try:
        import chiyo_bundle.world_journey as world_journey

        state = world_journey.load_state(world_home)
    except Exception as exc:  # noqa: BLE001 - 读不到只报状态
        return {"status": "UNKNOWN", "why": type(exc).__name__}
    if not state:
        return {"status": "ABSENT"}
    active = state.get("active")
    projection: dict[str, Any] = {"status": "OBSERVED",
                                  "revision": _count(state.get("revision")) or None,
                                  "active": None, "history": []}
    if isinstance(active, Mapping):
        segments = [{"status": _token(item.get("status")),
                     "destination": _token(item.get("destination_location_id"))}
                    for item in list(active.get("segments") or [])[:8]
                    if isinstance(item, Mapping)]
        projection["active"] = {
            "journey_id": _token(active.get("journey_id")),
            "status": _token(active.get("status")),
            "origin": _token(active.get("origin_location_id")),
            "target": _token(active.get("target_location_id")),
            "route": [token for token in (_token(item) for item in
                                          list(active.get("route") or [])[:8]) if token],
            "current_segment": _count(active.get("current_segment")),
            "segments": segments,
            "created_at": _token(active.get("created_at")),
            "updated_at": _token(active.get("updated_at")),
        }
    for item in list(state.get("history") or [])[-8:]:
        if isinstance(item, Mapping):
            projection["history"].append({
                "journey_id": _token(item.get("journey_id")),
                "status": _token(item.get("status")),
                "target": _token(item.get("target_location_id")),
                "closed_at": _token(item.get("closed_at")),
                "reason_code": _token(item.get("reason_code")),
            })
    return projection


def _body(world_home: Path) -> dict[str, Any]:
    path = world_home / "data" / "world" / "body_substrate.json"
    data = g1._read_json(path)
    if not data:
        return {"status": "UNKNOWN", "why": "body_substrate.json missing or unreadable"}
    return {"status": "OBSERVED", "pose": _token(data.get("pose")),
            "orientation": _token(data.get("orientation")),
            "world_id": _token(data.get("world_id"))}


def _world_views(root: Path) -> dict[str, Any]:
    from components.phone import service as phone_service

    try:
        instance = dict(phone_service.load_instance(root))
    except Exception as exc:  # noqa: BLE001 - 实例读不到就全部 UNKNOWN
        return {"status": "UNKNOWN", "why": type(exc).__name__,
                "position": {"status": "UNKNOWN"}, "journey": {"status": "UNKNOWN"},
                "body": {"status": "UNKNOWN"}}
    world_home = Path(str(instance.get("world_home") or ""))
    return {"status": "OBSERVED", "world_home": str(world_home),
            "position": _position(world_home), "journey": _journey(world_home),
            "body": _body(world_home)}


def build_summary(root: Path, *, offline_scratch: bool = False) -> dict[str, Any]:
    with g1._use_batch(g1.E_PROFILE.key):
        if not offline_scratch:
            refusal = g1._check_root(root)
            if refusal is not None:
                return dict(refusal)
            audited = g1.audit(root)
        else:
            audited = g1.audit(root, expected_root=root)
    if not audited.get("ok"):
        return dict(audited)

    phase = (audited.get("phases") or {}).get(g1.PHASE_NATURAL) or {}
    trace_rows, trace_meta = _trace_rows(root)
    world = phase.get("world") if isinstance(phase.get("world"), Mapping) else {}
    requests = _requests_from_trace(trace_rows)
    selections = _selections_from_trace(trace_rows)
    selection_basis = "trace"
    if not selections:
        selection_basis = "phase_report"
        selections = _selections_from_phase(phase)

    decisions = Counter(row["decision"] for row in selections
                        if row.get("model_decision") is True and row.get("decision"))
    action_ids = sorted({row["action_id"] for row in selections if row.get("action_id")})
    action_status = Counter(row["action_status"] for row in selections
                            if row.get("action_status"))
    outcomes = Counter(row["outcome"] for row in selections if row.get("outcome"))
    quiet_rounds = None
    if trace_rows:
        quiet_rounds = sum(
            1 for row in trace_rows
            if row.get("stage") == g1.STAGE_ROUND and row.get("request_present") is not True)

    observation = audited.get("observation") if isinstance(audited.get("observation"),
                                                           Mapping) else {}
    world_views = _world_views(root)
    close_world = phase.get("close_world") if isinstance(phase.get("close_world"),
                                                        Mapping) else {}
    close_supply = phase.get("close_supply") if isinstance(phase.get("close_supply"),
                                                          Mapping) else {}

    return {
        "schema": SCHEMA, "ok": True,
        "root": str(root), "batch": audited.get("batch"),
        "run_id": audited.get("run_id"), "domain_id": audited.get("domain_id"),
        "deadline": audited.get("deadline"),
        "world_intent_gate": _enum(audited.get("world_intent_gate"), WORLD_GATES),
        "lifecycle": {
            "state": audited.get("state"), "prepared_at": audited.get("prepared_at"),
            "started_at": audited.get("started_at"), "result": audited.get("result"),
            "stopped": audited.get("stopped"), "stop_reason": audited.get("stop_reason"),
            "ledger": audited.get("ledger"), "window": audited.get("window"),
        },
        "processes": {
            "phase_pid": phase.get("pid"),
            "world": {"pid": world.get("pid"), "gate": _enum(world.get("gate"), WORLD_GATES),
                      "ready": world.get("ready"),
                      "status": _enum(world.get("status"), WORLD_STATUSES),
                      "reason": _token(world.get("reason"))},
            "close_world": {name: close_world.get(name) for name in
                            ("pid", "exit_code", "socket_live_after", "graceful_stop_line")
                            if name in close_world},
            "close_supply": {name: close_supply.get(name) for name in
                             ("pid", "exit_code", "socket_live_after", "graceful_stop_line")
                             if name in close_supply},
            "close_runtime": phase.get("close_runtime"),
        },
        "candidates": {
            "requests": requests,
            "world_candidates_per_request": observation.get("world_candidates_per_request"),
            "selected": selections[:MAX_ROWS],
            "selection_basis": selection_basis,
        },
        "decisions": {
            "model_decisions": dict(sorted(decisions.items())),
            "rule_quiet_rounds": quiet_rounds,
            "no_decision_rounds": sum(1 for row in selections if not row.get("decision")),
        },
        "actions": {
            "ids": action_ids, "statuses": dict(sorted(action_status.items())),
            "outcomes": dict(sorted(outcomes.items())),
            "attempts": "UNKNOWN", "receipts": "UNKNOWN",
        },
        "world": world_views,
        "work": {
            "owner_versions_created": observation.get("owner_version_created"),
            "pause_chosen": observation.get("pause_chosen"),
            "abandon_chosen": observation.get("abandon_chosen"),
        },
        "trace": trace_meta,
        "unverified": [
            "attempts/receipts rows are not in the phase report; read via runtime only",
            "candidate target_refs are recorded as counts, not values",
            "prompt/reasoning/private text is never recorded",
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="world_observe_e_summary")
    parser.add_argument("--root", default=str(g1.E_PROFILE.root))
    parser.add_argument("--offline-scratch", action="store_true",
                        help="离线测试用：允许任意绝对 scratch 根；真实运行不要带")
    args = parser.parse_args(list(argv or sys.argv[1:]))
    root = Path(args.root).expanduser()
    if not root.is_absolute():
        print(json.dumps({"ok": False, "reason_code": "ROOT_NOT_ABSOLUTE",
                          "root": str(root)}, ensure_ascii=False))
        return EXIT_REFUSED
    summary = build_summary(root, offline_scratch=bool(args.offline_scratch))
    print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True, default=str))
    return EXIT_OK if summary.get("ok") else EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
