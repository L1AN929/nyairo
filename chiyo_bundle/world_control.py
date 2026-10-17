"""持久暂停控制状态（W03）：私有、版本化、原子、只读探测零创建。

职责边界
========
* 只写自己的控制文件 ``<world_home>/data/world/world_control.json``；
* **读取永不创建**任何目录/文件（缺文件即 "absent"，损坏即 "corrupt"，两者不混淆）；
* 写入者必须持有 journey 锁（与 ``world_journey`` 的提交在同一把锁内线性化），
  因此本模块不自己取锁，也不引入新的锁序。

暂停语义（W03 核心）
====================
``paused=True`` 表示**不再接受新的移动、不启动新段**，但允许已经真正出发的一段
按真实时间到达并被确认（结算不是"新移动"）。控制状态损坏（corrupt）与读失败一律
**禁止启动新段**（fail closed），不默认为"未暂停"。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = "world.control.v1"
CONTROL_FILENAME = "world_control.json"

STATE_ABSENT = "absent"
STATE_OK = "ok"
STATE_CORRUPT = "corrupt"

GATE_OK = "ok"
GATE_PAUSED = "paused"
GATE_CONTROL_BLOCKED = "control_blocked"

REASON_JOURNEY_PAUSED = "JOURNEY_PAUSED"
REASON_CONTROL_STATE_CORRUPT = "CONTROL_STATE_CORRUPT"

_CONTROL_KEYS = frozenset(
    {"schema_version", "revision", "paused", "paused_at", "resumed_at", "updated_at", "reason"}
)
_MAX_REASON = 120


class ControlError(ValueError):
    """控制状态损坏或不可用（fail closed）。"""


def _timestamp(value: Any, field: str = "timestamp") -> str:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ControlError(f"{field} is not an ISO timestamp") from exc
    else:
        raise ControlError(f"{field} must be a datetime or ISO string")
    if moment.tzinfo is None:
        raise ControlError(f"{field} must be timezone-aware")
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _home(hermes_home: Optional[Path | str]) -> Path:
    if hermes_home is None:
        raise ControlError("control state requires an explicit world home")
    path = Path(hermes_home).expanduser()
    if not path.is_absolute():
        raise ControlError("world home must be absolute")
    return path


def control_path(hermes_home: Optional[Path | str]) -> Path:
    return _home(hermes_home) / "data" / "world" / CONTROL_FILENAME


def _validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _CONTROL_KEYS:
        raise ControlError("control state has an invalid strict shape")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ControlError("control state schema is unsupported")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ControlError("control state revision is invalid")
    paused = value.get("paused")
    if not isinstance(paused, bool):
        raise ControlError("control state paused flag is invalid")
    paused_at = value.get("paused_at")
    if paused_at is not None:
        paused_at = _timestamp(paused_at, "paused_at")
    resumed_at = value.get("resumed_at")
    if resumed_at is not None:
        resumed_at = _timestamp(resumed_at, "resumed_at")
    if paused and paused_at is None:
        raise ControlError("paused control state requires paused_at")
    reason = value.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > _MAX_REASON:
            raise ControlError("control state reason is invalid")
        reason = reason.strip()
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "paused": paused,
        "paused_at": paused_at,
        "resumed_at": resumed_at,
        "updated_at": _timestamp(value.get("updated_at"), "updated_at"),
        "reason": reason,
    }


def read_control(hermes_home: Optional[Path | str]) -> Optional[dict[str, Any]]:
    """只读：文件不存在返回 None，损坏抛 ControlError。绝不创建任何东西。"""
    path = control_path(hermes_home)
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ControlError("control state cannot be read") from exc
    return _validate(raw)


def write_control(
    hermes_home: Optional[Path | str], record: Mapping[str, Any]
) -> dict[str, Any]:
    """原子写入；调用者必须已持有 journey 锁。"""
    normalized = _validate(record)
    path = control_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary: Optional[str] = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent), text=False
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ControlError("control state could not be atomically written") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return normalized


def set_paused(
    hermes_home: Optional[Path | str],
    *,
    paused: bool,
    now: Any = None,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """切换暂停位并耐久落盘；调用者必须已持有 journey 锁（线性化点）。"""
    stamp = _timestamp(now if now is not None else datetime.now(timezone.utc), "now")
    existing = read_control(hermes_home)
    revision = 1 if existing is None else existing["revision"] + 1
    record = {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "paused": bool(paused),
        "paused_at": stamp if paused else (existing or {}).get("paused_at"),
        "resumed_at": None if paused else stamp,
        "updated_at": stamp,
        "reason": reason if reason is not None else (existing or {}).get("reason"),
    }
    return write_control(hermes_home, record)


def gate(hermes_home: Optional[Path | str]) -> dict[str, Any]:
    """提交边界使用的闸门（只读，零创建，fail closed）。

    * ``absent`` → 未暂停（文档化默认：没有暂停记录即视为运行中）；
    * ``ok`` + paused → 阻止启动新段（``paused``）；
    * ``ok`` + 未暂停 → 放行；
    * ``corrupt`` → 阻止启动新段（``control_blocked``），不默认未暂停。
    """
    try:
        record = read_control(hermes_home)
    except ControlError:
        return {
            "state": STATE_CORRUPT,
            "blocked": True,
            "status": GATE_CONTROL_BLOCKED,
            "reason_code": REASON_CONTROL_STATE_CORRUPT,
            "control": None,
        }
    if record is None:
        return {
            "state": STATE_ABSENT,
            "blocked": False,
            "status": GATE_OK,
            "reason_code": None,
            "control": None,
        }
    if record["paused"]:
        return {
            "state": STATE_OK,
            "blocked": True,
            "status": GATE_PAUSED,
            "reason_code": REASON_JOURNEY_PAUSED,
            "control": record,
        }
    return {
        "state": STATE_OK,
        "blocked": False,
        "status": GATE_OK,
        "reason_code": None,
        "control": record,
    }


def projection(hermes_home: Optional[Path | str]) -> dict[str, Any]:
    """只读控制状态投影（供 status/工具使用，零创建）。"""
    try:
        record = read_control(hermes_home)
    except ControlError:
        return {
            "state": STATE_CORRUPT,
            "paused": None,
            "revision": None,
            "paused_at": None,
            "resumed_at": None,
            "updated_at": None,
            "reason": None,
            "reason_code": REASON_CONTROL_STATE_CORRUPT,
        }
    if record is None:
        return {
            "state": STATE_ABSENT,
            "paused": False,
            "revision": None,
            "paused_at": None,
            "resumed_at": None,
            "updated_at": None,
            "reason": None,
            "reason_code": None,
        }
    return {"state": STATE_OK, "reason_code": None, **record}


__all__ = [
    "CONTROL_FILENAME",
    "ControlError",
    "GATE_CONTROL_BLOCKED",
    "GATE_OK",
    "GATE_PAUSED",
    "REASON_CONTROL_STATE_CORRUPT",
    "REASON_JOURNEY_PAUSED",
    "SCHEMA_VERSION",
    "STATE_ABSENT",
    "STATE_CORRUPT",
    "STATE_OK",
    "control_path",
    "gate",
    "projection",
    "read_control",
    "set_paused",
    "write_control",
]
