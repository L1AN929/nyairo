#!/usr/bin/env python3
"""只读短日状态：多段许可的真实 route / action / travel / 票据 / 预算 / 自有 PID。

只读白名单
==========
* 只读 ``fastday.json``、实例自己的 ``instance.json``、``world_journey.json``、
  ``world_travel_delivery.json``、授权 registry 与既有的 resident/账本视图。
* 不写任何文件、不建 DB/owner/socket、不连模型、不改配置。
* 输出不含 prompt、私文正文、reasoning 与密钥；重复 id 只保留公开引用。

用法::

    python scripts/journey_authorization_live_status.py --root <实例根>

退出码：0 = 根可读并给出状态；1 = 根/模式不可读（原因具名）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _journey_view(world_home: Optional[Path]) -> dict[str, Any]:
    if world_home is None:
        return {"present": False, "reason_code": "WORLD_HOME_UNKNOWN"}
    data = _read_json(world_home / "data" / "world" / "world_journey.json")
    active = data.get("active") if isinstance(data.get("active"), dict) else None
    if not active:
        return {"present": False, "reason_code": "NO_ACTIVE_JOURNEY"}
    segments = [{
        "index": segment.get("index"),
        "destination_location_id": segment.get("destination_location_id"),
        "execution_id": segment.get("execution_id"),
        "status": segment.get("status"),
        "travel_id": segment.get("travel_id"),
        "expected_arrival_at": segment.get("expected_arrival_at"),
        "confirmed_at": segment.get("confirmed_at"),
    } for segment in (active.get("segments") or []) if isinstance(segment, dict)]
    history = [{
        "journey_id": item.get("journey_id"),
        "status": item.get("status"),
        "reason_code": item.get("reason_code"),
    } for item in (data.get("history") or []) if isinstance(item, dict)]
    return {
        "present": True,
        "journey_id": active.get("journey_id"),
        "status": active.get("status"),
        "route": list(active.get("route") or []),
        "current_segment": active.get("current_segment"),
        "origin_location_id": active.get("origin_location_id"),
        "target_location_id": active.get("target_location_id"),
        "updated_at": active.get("updated_at"),
        "error": active.get("error"),
        "segments": segments,
        "history": history,
    }


def _travel_view(world_home: Optional[Path]) -> dict[str, Any]:
    if world_home is None:
        return {"present": False, "count": 0, "reason_code": "WORLD_HOME_UNKNOWN"}
    data = _read_json(world_home / "data" / "world" / "world_travel_delivery.json")
    travels = [item for item in (data.get("travels") or []) if isinstance(item, dict)]
    return {
        "present": True,
        "count": len(travels),
        "records": [{
            "execution_id": item.get("execution_id"),
            "origin_location_id": item.get("origin_location_id"),
            "destination_location_id": item.get("destination_location_id"),
            "status": item.get("status"),
            "started_at": item.get("started_at"),
            "arrived_at": item.get("arrived_at"),
        } for item in travels],
    }


def _authorization_view(instance: Mapping[str, Any]) -> dict[str, Any]:
    socket_value = str(instance.get("world_body_socket") or "").strip()
    if not socket_value:
        return {"present": False, "reason_code": "WORLD_BODY_SOCKET_UNKNOWN"}
    path = Path(socket_value).parent / "action_authorizations.json"
    data = _read_json(path)
    records = [item for item in (data.get("records") or []) if isinstance(item, dict)]
    by_state: dict[str, int] = {}
    for record in records:
        state = str(record.get("state") or "UNKNOWN").upper()
        by_state[state] = by_state.get(state, 0) + 1
    return {
        "present": True,
        "count": len(records),
        "by_state": by_state,
        "records": [{
            "authorization_id": record.get("authorization_id"),
            "execution_id": record.get("execution_id"),
            "verb": record.get("verb"),
            "state": record.get("state"),
            "issued_at": record.get("issued_at"),
            "expires_at": record.get("expires_at"),
            "consumed_at": record.get("consumed_at"),
        } for record in records],
    }


def _process_view(status: Mapping[str, Any]) -> dict[str, Any]:
    process = dict(status.get("process") or {})
    kids = process.get("children") or []
    return {
        "supervisor_pid": process.get("supervisor_pid"),
        "supervisor_alive": process.get("supervisor_alive"),
        "child_pid": process.get("child_pid"),
        "child_alive": process.get("child_alive"),
        "children": kids,
        "resident_exit_code": process.get("resident_exit_code"),
        "stopped_utc": process.get("stopped_utc"),
        "stop_reason": process.get("stop_reason"),
    }


def collect(root: Path) -> dict[str, Any]:
    import fast_day

    status = fast_day._status_payload(root)
    if status.get("ok") is not True:
        return {"ok": False, "root": str(root),
                "reason_code": status.get("reason_code"),
                "detail": status.get("detail")}
    instance = _read_json(root / "instance.json")
    world_home_value = str(instance.get("world_home") or "").strip()
    world_home = Path(world_home_value) if world_home_value else None
    resident = dict(status.get("resident") or {})
    return {
        "ok": True,
        "root": str(root),
        "run_id": status.get("run_id"),
        "ledger_run_id": status.get("ledger_run_id"),
        "run_identity": instance.get("run_id") or instance.get("autonomous_run_id"),
        "execution_mode": status.get("execution_mode"),
        "not_real_24h": status.get("not_real_24h"),
        "clock": status.get("clock"),
        "logical": status.get("logical"),
        "real": status.get("real"),
        "control": status.get("control"),
        "request_limits": status.get("request_limits"),
        "experiment": status.get("experiment"),
        "wall_budget": status.get("wall_budget"),
        "process": _process_view(status),
        "health": status.get("health"),
        "world_home": world_home_value or None,
        "world": {
            "journey": _journey_view(world_home),
            "travel": _travel_view(world_home),
        },
        "authorizations": _authorization_view(instance),
        "resident": {
            "present": resident.get("present"),
            "running": resident.get("running"),
            "pid": resident.get("pid"),
            "ticks": resident.get("ticks"),
            "time_domain": resident.get("time_domain"),
            "wakes": resident.get("wakes"),
            "by_class": resident.get("by_class"),
            "actions": resident.get("actions"),
            "model_calls": resident.get("model_calls"),
            "budget": resident.get("budget"),
            "journey": resident.get("journey"),
            "last_error": resident.get("last_error"),
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="journey_authorization_live_status")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    payload = collect(Path(args.root))
    print(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
