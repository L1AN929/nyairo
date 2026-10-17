"""持久旅程（W02）：耐久意图 + 单段 owner 提交 + 证据重协调。

职责边界
========
* 本模块**只写自己的账本** ``<world_home>/data/world/world_journey.json``；位置与时间
  权威仍是单段 owner（``world_living_skeleton.apply_move_to`` 与
  ``world_travel_delivery`` 的 travel 记录）。
* 不写 ``world_state.json``、不写 ``world_travel_delivery.json``、不伪造回执。
* 只续行用户已经明确提出的目标：不生成新目标、不做自动重规划、不自动迁移。

锁顺序（全局唯一顺序，禁止反向）
================================
``world_journey.json.lock`` → ``world_state.json.move.lock`` → ``world_travel_delivery.json.lock``

旅程锁永远是最外层；owner 内部固定按「world → travel」取锁
（``apply_move_to`` → ``start_travel``；``realize_due_travel`` 显式 world→travel）。
任何持有 owner 锁的路径都不得再取旅程锁，否则会死锁；旅程锁只被本模块使用，
因此不存在反向依赖。本模块不在持有 owner 锁时调用任何会申请旅程锁的函数。

状态机
======
旅程级：``pending``（意图已落盘，当前段未提交）→ ``in_transit``（当前段已在 owner
侧启动）→ 段确认后回到 ``pending`` 并推进到下一段；全部段确认 → ``completed``。
证据缺失/矛盾 → ``unknown``（每次推进重新查证，不命令）；明确拒绝/冲突/损坏 →
``blocked``（停止状态，不再启动新段）。终态（completed/blocked）在新旅程被接受时
归档进 ``history``，历史不靠删除表示。

段级：``pending`` → ``started`` → ``confirmed``。

故障窗口
========
①意图落盘/owner 提交前 → 段仍是 pending 且无 travel 记录、位置在段起点 → 可安全重发。
②owner 提交/旅程落盘前 → travel 记录已存在 → 通过 execution_id 认领（不重复提交）。
③到达结算/段确认前 → travel 记录 ARRIVED 且位置在段终点 → 只补写账本，不再移动。
④段确认/下一段开始前 → 下一段 pending 且位置在其起点 → 只提交下一段。
⑤owner 提交回执丢失（transport fault）→ 端口报 ``uncertain``：段仍是 pending、位置在
段起点，但 travel 记录**可能已经存在**。此时旅程写非终态 ``unknown`` 并记下
``SEGMENT_SUBMIT_UNCERTAIN:<failure detail>``，先用同 execution_id 的证据认领
（匹配 origin/destination 的在途记录 → started），没有证据就等下一次推进再对账；
在该标记存在期间**绝不重复提交、绝不更换 execution id**。只有清晰的非模糊拒绝
（rejected/failed/disabled/noop）才写终态 ``blocked``。
证据不一致（位置与记录矛盾、出现外部在途、世界/图变化、账本损坏）→ unknown/blocked，
保留目标、不改真实状态、不继续。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - production is POSIX
    fcntl = None

try:
    from . import world_control
except ImportError:  # pragma: no cover - 直接以脚本路径导入时的回退
    import world_control  # type: ignore

SCHEMA_VERSION = "world.journey.v1"
STATE_FILENAME = "world_journey.json"
JOURNEY_KIND = "shiomi_world_journey"
LEGACY_INTENT_FILENAME = "intended_target.json"
MAX_ROUTE = 64
MAX_HISTORY = 16
MAX_ID_LENGTH = 160
MAX_TEXT_LENGTH = 240

STATUS_PENDING = "pending"
STATUS_IN_TRANSIT = "in_transit"
STATUS_COMPLETED = "completed"
STATUS_BLOCKED = "blocked"
STATUS_UNKNOWN = "unknown"
JOURNEY_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_IN_TRANSIT, STATUS_COMPLETED, STATUS_BLOCKED, STATUS_UNKNOWN}
)
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_BLOCKED})

SEGMENT_PENDING = "pending"
SEGMENT_STARTED = "started"
SEGMENT_CONFIRMED = "confirmed"
SEGMENT_STATUSES = frozenset({SEGMENT_PENDING, SEGMENT_STARTED, SEGMENT_CONFIRMED})

TRAVEL_IN_PROGRESS = "IN_PROGRESS"
TRAVEL_ARRIVED = "ARRIVED"


#: An owner submission whose reply was lost may already have committed.  The
#: journey records the doubt under this prefix and, while it is set on the
#: current segment, never re-submits: the next advance re-reads the owner's
#: evidence first, and only an exact match may claim the segment.
AMBIGUOUS_SUBMIT_PREFIX = "SEGMENT_SUBMIT_UNCERTAIN"
#: Same doubt, but the port proved the request never crossed the owner
#: boundary, so a later attempt under the same execution id is legitimate.
NOT_SENT_SUBMIT_PREFIX = "SEGMENT_SUBMIT_NOT_SENT"
#: Port reason codes that mean "nothing was submitted" (nothing to reconcile).
PRE_SUBMIT_REASONS = frozenset({"INTENT_SUBMIT_UNAVAILABLE", "INTENT_BUILD_FAILED"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_STATE_KEYS = frozenset({"schema_version", "revision", "active", "history"})
_JOURNEY_KEYS = frozenset(
    {
        "journey_id",
        "world_id",
        "graph_fingerprint",
        "route_revision",
        "origin_location_id",
        "target_location_id",
        "route",
        "segments",
        "current_segment",
        "status",
        "request_key",
        "request_digest",
        "created_at",
        "updated_at",
        "error",
    }
)
_SEGMENT_KEYS = frozenset(
    {
        "index",
        "destination_location_id",
        "execution_id",
        "travel_id",
        "status",
        "started_at",
        "expected_arrival_at",
        "confirmed_at",
    }
)
_HISTORY_KEYS = frozenset(
    {
        "journey_id",
        "status",
        "target_location_id",
        "created_at",
        "closed_at",
        "reason_code",
    }
)


class JourneyError(ValueError):
    """旅程契约损坏或不可用（fail closed）。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _copy_json(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise JourneyError("value is not bounded JSON") from exc


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise JourneyError("value is not bounded JSON") from exc


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _id(value: Any, field: str, maximum: int = MAX_ID_LENGTH) -> str:
    if not isinstance(value, str):
        raise JourneyError(f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > maximum or not _ID_RE.match(text):
        raise JourneyError(f"{field} is not a valid id")
    return text


def _text(value: Any, field: str, maximum: int = MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str):
        raise JourneyError(f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > maximum:
        raise JourneyError(f"{field} is invalid")
    return text


def _timestamp(value: Any, field: str = "timestamp") -> str:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise JourneyError(f"{field} is not an ISO timestamp") from exc
    else:
        raise JourneyError(f"{field} must be a datetime or ISO string")
    if moment.tzinfo is None:
        raise JourneyError(f"{field} must be timezone-aware")
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _instant(value: Any, field: str = "now") -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(_timestamp(value, field).replace("Z", "+00:00"))


def _home(hermes_home: Optional[Path | str]) -> Path:
    if hermes_home is None:
        raise JourneyError("journey state requires an explicit world home")
    path = Path(hermes_home).expanduser()
    if not path.is_absolute():
        raise JourneyError("world home must be absolute")
    return path


def state_path(hermes_home: Optional[Path | str]) -> Path:
    return _home(hermes_home) / "data" / "world" / STATE_FILENAME


def lock_path(hermes_home: Optional[Path | str]) -> Path:
    return state_path(hermes_home).with_name(STATE_FILENAME + ".lock")


@contextmanager
def journey_lock(hermes_home: Optional[Path | str]) -> Iterator[None]:
    """公开的 journey 锁（写控制状态等线性化操作使用；锁序见模块 docstring）。"""
    with _lock(lock_path(hermes_home)):
        yield


def _control_gate(home: Path) -> dict[str, Any]:
    """提交边界上的持久暂停闸门（只读、零创建、fail closed）。"""
    return world_control.gate(home)


def legacy_intent_path(hermes_home: Optional[Path | str]) -> Path:
    return _home(hermes_home) / "data" / "world" / LEGACY_INTENT_FILENAME


def legacy_intent_present(hermes_home: Optional[Path | str]) -> bool:
    """只读探测旧原型文件；W02 既不消费也不删除它。"""
    try:
        return legacy_intent_path(hermes_home).is_file()
    except (OSError, JourneyError):
        return False


def _world_modules():
    scripts = str(Path(__file__).resolve().parents[1] / "components/world/scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    return (
        importlib.import_module("world_living_skeleton"),
        importlib.import_module("world_travel_delivery"),
        importlib.import_module("world_runtime_data"),
        importlib.import_module("world_foundation"),
    )


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    if fcntl is None:
        raise JourneyError("journey lock unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# 世界图指纹与请求摘要
# ---------------------------------------------------------------------------


def graph_fingerprint(world: Any) -> str:
    """覆盖节点、连边与耗时的结构指纹；任何影响执行的变化都会改变它。"""
    lines = [f"world={world.world_id}", f"route_revision={world.route_revision}"]
    for node in world.all_nodes():
        edges = []
        for neighbor in world.reachable_of(node):
            target = world.canonical_node(neighbor)
            if target is None:
                continue
            edges.append(f"{target}:{world.route_duration_seconds(node, target)}")
        lines.append(f"{node}->{','.join(sorted(set(edges)))}")
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _request_digest(
    world: Any, origin: str, target: str, route: Sequence[str]
) -> str:
    material = _canonical(
        {
            "world_id": world.world_id,
            "origin_location_id": origin,
            "target_location_id": target,
            "route": list(route),
        }
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 校验（严格形状 + 状态机不变量）
# ---------------------------------------------------------------------------


def _validate_segment(value: Any, index: int, route: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _SEGMENT_KEYS:
        raise JourneyError("journey segment has an invalid strict shape")
    if value.get("index") != index:
        raise JourneyError("journey segment index is invalid")
    destination = _id(
        value.get("destination_location_id"), "segment.destination_location_id"
    )
    if destination != route[index]:
        raise JourneyError("journey segment destination does not match the route")
    execution_id = _id(value.get("execution_id"), "segment.execution_id")
    travel_id = value.get("travel_id")
    if travel_id is not None:
        travel_id = _id(travel_id, "segment.travel_id")
    status = value.get("status")
    if status not in SEGMENT_STATUSES:
        raise JourneyError("journey segment status is invalid")
    started_at = value.get("started_at")
    expected_arrival_at = value.get("expected_arrival_at")
    confirmed_at = value.get("confirmed_at")
    if started_at is not None:
        started_at = _timestamp(started_at, "segment.started_at")
    if expected_arrival_at is not None:
        expected_arrival_at = _timestamp(
            expected_arrival_at, "segment.expected_arrival_at"
        )
    if confirmed_at is not None:
        confirmed_at = _timestamp(confirmed_at, "segment.confirmed_at")
    if status == SEGMENT_PENDING:
        if (
            travel_id is not None
            or started_at is not None
            or expected_arrival_at is not None
            or confirmed_at is not None
        ):
            raise JourneyError("pending segment cannot carry travel evidence")
    else:
        if travel_id is None or started_at is None or expected_arrival_at is None:
            raise JourneyError("started segment requires travel evidence")
        if _instant(expected_arrival_at) <= _instant(started_at):
            raise JourneyError("segment expected arrival must be after start")
        if status == SEGMENT_STARTED and confirmed_at is not None:
            raise JourneyError("started segment cannot be confirmed")
        if status == SEGMENT_CONFIRMED and confirmed_at is None:
            raise JourneyError("confirmed segment requires confirmed_at")
    return {
        "index": index,
        "destination_location_id": destination,
        "execution_id": execution_id,
        "travel_id": travel_id,
        "status": status,
        "started_at": started_at,
        "expected_arrival_at": expected_arrival_at,
        "confirmed_at": confirmed_at,
    }


def _validate_journey(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _JOURNEY_KEYS:
        raise JourneyError("journey has an invalid strict shape")
    journey_id = _id(value.get("journey_id"), "journey_id")
    world_id = _text(value.get("world_id"), "world_id", 120)
    fingerprint = _text(value.get("graph_fingerprint"), "graph_fingerprint", 120)
    route_revision = _text(value.get("route_revision"), "route_revision", 120)
    origin = _id(value.get("origin_location_id"), "origin_location_id")
    target = _id(value.get("target_location_id"), "target_location_id")
    route_raw = value.get("route")
    if (
        not isinstance(route_raw, Sequence)
        or isinstance(route_raw, (str, bytes, bytearray))
        or not 1 <= len(route_raw) <= MAX_ROUTE
    ):
        raise JourneyError("journey route is invalid")
    route = [_id(item, "route entry") for item in route_raw]
    if len(set(route)) != len(route):
        raise JourneyError("journey route repeats a node")
    if route[-1] != target:
        raise JourneyError("journey route must end at the target")
    if origin == target:
        raise JourneyError("journey origin and target must differ")
    segments_raw = value.get("segments")
    if (
        not isinstance(segments_raw, Sequence)
        or isinstance(segments_raw, (str, bytes, bytearray))
        or len(segments_raw) != len(route)
    ):
        raise JourneyError("journey segments are invalid")
    segments = [
        _validate_segment(item, index, route)
        for index, item in enumerate(segments_raw)
    ]
    current = value.get("current_segment")
    if isinstance(current, bool) or not isinstance(current, int):
        raise JourneyError("current_segment is invalid")
    status = value.get("status")
    if status not in JOURNEY_STATUSES:
        raise JourneyError("journey status is invalid")
    if not 0 <= current <= len(route):
        raise JourneyError("current_segment is out of range")
    for index, segment in enumerate(segments):
        if index < current and segment["status"] != SEGMENT_CONFIRMED:
            raise JourneyError("journey confirmed prefix is inconsistent")
        if index >= current and segment["status"] == SEGMENT_CONFIRMED:
            raise JourneyError("journey confirmed suffix is inconsistent")
    if status == STATUS_COMPLETED:
        if current != len(route) or any(
            item["status"] != SEGMENT_CONFIRMED for item in segments
        ):
            raise JourneyError("completed journey requires every segment confirmed")
    else:
        if current >= len(route):
            raise JourneyError("unfinished journey must point at a segment")
        if segments[current]["status"] == SEGMENT_CONFIRMED:
            raise JourneyError(
                "unfinished journey cannot have a confirmed current segment"
            )
    if status == STATUS_IN_TRANSIT and segments[current]["status"] != SEGMENT_STARTED:
        raise JourneyError("in-transit journey requires a started current segment")
    if status == STATUS_PENDING and segments[current]["status"] != SEGMENT_PENDING:
        raise JourneyError("pending journey requires a pending current segment")
    request_key = value.get("request_key")
    if request_key is not None:
        request_key = _text(request_key, "request_key", 120)
    request_digest = _text(value.get("request_digest"), "request_digest", 120)
    created_at = _timestamp(value.get("created_at"), "created_at")
    updated_at = _timestamp(value.get("updated_at"), "updated_at")
    error = value.get("error")
    if error is not None:
        if not isinstance(error, Mapping) or set(error) != {"reason_code", "at"}:
            raise JourneyError("journey error has an invalid strict shape")
        error = {
            "reason_code": _id(error.get("reason_code"), "error.reason_code", 120),
            "at": _timestamp(error.get("at"), "error.at"),
        }
    return {
        "journey_id": journey_id,
        "world_id": world_id,
        "graph_fingerprint": fingerprint,
        "route_revision": route_revision,
        "origin_location_id": origin,
        "target_location_id": target,
        "route": route,
        "segments": segments,
        "current_segment": current,
        "status": status,
        "request_key": request_key,
        "request_digest": request_digest,
        "created_at": created_at,
        "updated_at": updated_at,
        "error": error,
    }


def _validate_history_entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _HISTORY_KEYS:
        raise JourneyError("journey history entry has an invalid strict shape")
    status = value.get("status")
    if status not in TERMINAL_STATUSES:
        raise JourneyError("journey history entry must be terminal")
    reason = value.get("reason_code")
    if reason is not None:
        reason = _id(reason, "history.reason_code", 120)
    return {
        "journey_id": _id(value.get("journey_id"), "history.journey_id"),
        "status": status,
        "target_location_id": _id(
            value.get("target_location_id"), "history.target_location_id"
        ),
        "created_at": _timestamp(value.get("created_at"), "history.created_at"),
        "closed_at": _timestamp(value.get("closed_at"), "history.closed_at"),
        "reason_code": reason,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _STATE_KEYS:
        raise JourneyError("journey state has an invalid strict shape")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise JourneyError("journey state schema is unsupported")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise JourneyError("journey state revision is invalid")
    active = value.get("active")
    if active is not None:
        active = _validate_journey(active)
    history_raw = value.get("history")
    if (
        not isinstance(history_raw, Sequence)
        or isinstance(history_raw, (str, bytes, bytearray))
        or len(history_raw) > MAX_HISTORY
    ):
        raise JourneyError("journey history is invalid")
    history = [_validate_history_entry(item) for item in history_raw]
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "active": active,
        "history": history,
    }


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "active": None,
        "history": [],
    }


def _read_state(home: Path) -> Optional[dict[str, Any]]:
    """只读；文件不存在返回 None，损坏抛 JourneyError。"""
    path = state_path(home)
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise JourneyError("journey state cannot be read") from exc
    return _validate_state(raw)


def _write_state(home: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_state(state)
    path = state_path(home)
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
        raise JourneyError("journey state could not be atomically written") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return normalized


def load_state(hermes_home: Optional[Path | str]) -> Optional[dict[str, Any]]:
    return _read_state(_home(hermes_home))


def _commit(
    home: Path, store: dict[str, Any], journey: dict[str, Any], *, updated_at: str
) -> bool:
    """内容真的变了才落盘（revision+1）；返回是否写入。"""
    if store["active"] is not None and _canonical(journey) == _canonical(
        store["active"]
    ):
        return False
    journey["updated_at"] = updated_at
    store["active"] = journey
    store["revision"] = store["revision"] + 1
    _write_state(home, store)
    return True


def _archive(
    store: dict[str, Any], journey: Mapping[str, Any], *, closed_at: str
) -> None:
    entry = {
        "journey_id": journey["journey_id"],
        "status": journey["status"],
        "target_location_id": journey["target_location_id"],
        "created_at": journey["created_at"],
        "closed_at": closed_at,
        "reason_code": (journey.get("error") or {}).get("reason_code"),
    }
    history = [*store["history"], entry]
    store["history"] = history[-MAX_HISTORY:]
    store["active"] = None


# ---------------------------------------------------------------------------
# owner 侧只读证据
# ---------------------------------------------------------------------------


def _world_position(home: Path, world: Any, wtd: Any, wf: Any) -> Optional[str]:
    """纯读世界状态（不 realize、不推进）。"""
    try:
        state = wf.WorldStateStore(wtd.world_state_path(home)).load()
    except Exception:
        return None
    if state is None:
        return None
    location = state.get("location") or {}
    return world.state_node(location.get("place_id"), location.get("area_id"))


def _travel_index(
    home: Path, env: Mapping[str, str], wtd: Any
) -> dict[str, dict[str, Any]]:
    try:
        state = wtd.load_state(home, env)
    except Exception as exc:
        raise JourneyError("travel state is unreadable") from exc
    if state is None:
        return {}
    return {item["execution_id"]: item for item in state["travels"]}


def _segment_origin(journey: Mapping[str, Any], index: int) -> str:
    return (
        journey["origin_location_id"]
        if index == 0
        else journey["route"][index - 1]
    )


def _evidence(
    state: str,
    reason_code: Optional[str] = None,
    *,
    current: Optional[str] = None,
    record: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "reason_code": reason_code,
        "current": current,
        "record": dict(record) if isinstance(record, Mapping) else None,
    }


def _reason_with_detail(prefix: str, detail: Any) -> str:
    """把端口给的 failure detail 拼进 reason_code（仍过严格 id 校验）。

    ``_validate_journey`` 只接受 ``[A-Za-z0-9._:/-]`` 且 ≤120 字符，所以细节是
    被清洗而不是被丢掉：账本里的原因必须能让人一眼看出这是
    ``transport_fault:TIMEOUT``，而不是又一个笼统的 UNKNOWN。
    """

    text = re.sub(r"[^A-Za-z0-9._:/-]+", "-", str(detail or "")).strip("-")
    text = text[: max(0, 120 - len(prefix) - 1)]
    return f"{prefix}:{text}" if text else prefix


def _submission_pending_reconcile(journey: Mapping[str, Any]) -> bool:
    """本段是否带着「可能已提交、只等对账」的标记（有则绝不再提交）。"""

    if journey.get("status") != STATUS_UNKNOWN:
        return False
    reason = str((journey.get("error") or {}).get("reason_code") or "")
    return reason == AMBIGUOUS_SUBMIT_PREFIX or reason.startswith(
        AMBIGUOUS_SUBMIT_PREFIX + ":"
    )


def _reconcile(
    journey: Mapping[str, Any],
    *,
    home: Path,
    env: Mapping[str, str],
    now: Any,
    world: Any,
    wtd: Any,
    wf: Any,
) -> dict[str, Any]:
    """先查证：只看持久证据与当前位置，不发起任何移动。"""
    index = journey["current_segment"]
    segment = journey["segments"][index]
    origin = _segment_origin(journey, index)
    destination = segment["destination_location_id"]
    current = _world_position(home, world, wtd, wf)
    if current is None:
        return _evidence("unavailable", "WORLD_NOT_INITIALIZED", current=None)
    records = _travel_index(home, env, wtd)
    mine = records.get(segment["execution_id"])
    foreign = [
        item
        for item in records.values()
        if item["status"] == TRAVEL_IN_PROGRESS
        and item["execution_id"] != segment["execution_id"]
    ]
    if foreign:
        return _evidence(
            "blocked", "EXTERNAL_TRAVEL_IN_PROGRESS", current=current, record=mine
        )
    if mine is None:
        if segment["status"] != SEGMENT_PENDING:
            return _evidence(
                "unknown", "SEGMENT_LEDGER_INCONSISTENT", current=current
            )
        if current == origin:
            return _evidence("not_started", None, current=current)
        return _evidence("unknown", "WORLD_LOCATION_CHANGED", current=current)
    if (
        mine["origin_location_id"] != origin
        or mine["destination_location_id"] != destination
    ):
        return _evidence(
            "unknown", "SEGMENT_EVIDENCE_MISMATCH", current=current, record=mine
        )
    if mine["status"] == TRAVEL_IN_PROGRESS:
        if _instant(now) < _instant(mine["expected_arrival_at"]) and current != destination:
            # 未到期时位置必须仍在段起点；被外部改动即证据矛盾，fail closed。
            if current != origin:
                return _evidence(
                    "unknown", "WORLD_LOCATION_CHANGED", current=current, record=mine
                )
            return _evidence("in_flight", None, current=current, record=mine)
        realized = wtd.realize_due_travel(hermes_home=home, environment=env, now=now)
        status = realized.get("status")
        if status == "arrived":
            records = _travel_index(home, env, wtd)
            mine = records.get(segment["execution_id"])
            current = _world_position(home, world, wtd, wf)
        elif status == "in_progress":
            return _evidence("in_flight", None, current=current, record=mine)
        elif status == "disabled":
            # feature gate 关闭不是旅程失败：不写账本、不标记终态，开关恢复后可继续。
            return _evidence(
                "feature_disabled", "TRAVEL_TIME_DISABLED", current=current, record=mine
            )
        elif status == "unavailable":
            return _evidence(
                "unavailable",
                realized.get("reason_code", "TRAVEL_UNAVAILABLE"),
                current=current,
                record=mine,
            )
        else:
            return _evidence(
                "blocked",
                realized.get("reason_code", "ARRIVAL_REALIZATION_FAILED"),
                current=current,
                record=mine,
            )
    if mine is not None and mine["status"] == TRAVEL_ARRIVED:
        if current == destination:
            return _evidence("arrived", None, current=current, record=mine)
        return _evidence("unknown", "EVIDENCE_CONFLICT", current=current, record=mine)
    return _evidence(
        "unknown", "SEGMENT_EVIDENCE_UNRESOLVED", current=current, record=mine
    )


# ---------------------------------------------------------------------------
# 状态投影
# ---------------------------------------------------------------------------


def _projection(
    store: Mapping[str, Any],
    journey: Mapping[str, Any],
    *,
    home: Path,
    now: Any,
    world: Any,
    wtd: Any,
    wf: Any,
    status: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> dict[str, Any]:
    instant = _instant(now)
    index = journey["current_segment"]
    segments = journey["segments"]
    segment = segments[index] if index < len(segments) else None
    remaining_seconds = None
    estimated_minutes = None
    if segment is not None and segment["expected_arrival_at"] is not None:
        remaining_seconds = max(
            0,
            int(
                math.ceil(
                    (_instant(segment["expected_arrival_at"]) - instant).total_seconds()
                )
            ),
        )
        estimated_minutes = max(
            1,
            int(
                math.ceil(
                    (
                        _instant(segment["expected_arrival_at"])
                        - _instant(segment["started_at"])
                    ).total_seconds()
                    / 60
                )
            ),
        )
    error = journey.get("error")
    return {
        "kind": JOURNEY_KIND,
        "status": status or journey["status"],
        "reason_code": (
            reason_code if reason_code is not None else (error or {}).get("reason_code")
        ),
        "journey_id": journey["journey_id"],
        "world_id": journey["world_id"],
        "origin_location_id": journey["origin_location_id"],
        "target_location_id": journey["target_location_id"],
        "route": list(journey["route"]),
        "current_segment": index,
        "segment_status": (
            segment["status"] if segment is not None else SEGMENT_CONFIRMED
        ),
        "destination_location_id": (
            segment["destination_location_id"]
            if segment is not None
            else journey["target_location_id"]
        ),
        "travel_id": segment["travel_id"] if segment is not None else None,
        "expected_arrival_at": (
            segment["expected_arrival_at"] if segment is not None else None
        ),
        "remaining_seconds": remaining_seconds,
        "remaining_minutes": (
            None if remaining_seconds is None else int(math.ceil(remaining_seconds / 60))
        ),
        "estimated_minutes": estimated_minutes,
        "segments_confirmed": sum(
            1 for item in segments if item["status"] == SEGMENT_CONFIRMED
        ),
        "location_id": _world_position(home, world, wtd, wf),
        "revision": store["revision"],
        "graph_matches": journey["graph_fingerprint"] == graph_fingerprint(world),
        "legacy_intent_present": legacy_intent_present(home),
        "error": error,
    }


def _rejection(status: str, reason_code: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": JOURNEY_KIND,
        "status": status,
        "reason_code": reason_code,
        **extra,
    }


# ---------------------------------------------------------------------------
# 一次推进
# ---------------------------------------------------------------------------


def _fail(
    store: dict[str, Any],
    journey: dict[str, Any],
    home: Path,
    status: str,
    reason_code: str,
    instant: Any,
) -> dict[str, Any]:
    journey["status"] = status
    journey["error"] = {"reason_code": reason_code, "at": _timestamp(instant)}
    _commit(home, store, journey, updated_at=_timestamp(instant))
    return _evidence(status, reason_code)


def _confirm_segment(
    journey: dict[str, Any], *, now: Any, record: Mapping[str, Any]
) -> None:
    index = journey["current_segment"]
    segment = journey["segments"][index]
    segment["status"] = SEGMENT_CONFIRMED
    segment["travel_id"] = record["travel_id"]
    segment["started_at"] = record["started_at"]
    segment["expected_arrival_at"] = record["expected_arrival_at"]
    segment["confirmed_at"] = record.get("arrived_at") or _timestamp(now)
    journey["current_segment"] = index + 1
    journey["error"] = None
    journey["status"] = (
        STATUS_COMPLETED
        if journey["current_segment"] >= len(journey["route"])
        else STATUS_PENDING
    )


def _uncertain_submission(
    store: dict[str, Any],
    journey: dict[str, Any],
    *,
    home: Path,
    env: Mapping[str, str],
    now: Any,
    world: Any,
    wtd: Any,
    wf: Any,
    moved: Mapping[str, Any],
) -> dict[str, Any]:
    """模糊提交（端口说 uncertain）：先对账，绝不把它当成拒绝，也不重发。

    三种结局：

    * 同 execution_id 的 travel 记录存在且起终点匹配 → 认领为 started，
      旅程 ``in_transit``（位置权威仍是 owner 记录，本模块不写世界状态）；
    * owner 已到达同 execution_id → 交给 ``_step`` 去确认该段；
    * 没有任何记录 / 读不到 / 只有别人的在途 → 保持 ``unknown``（非终态），
      并把端口的 failure detail 写进 ``error.reason_code``，下一次推进再对账。
    """

    instant = _instant(now)
    index = journey["current_segment"]
    segment = journey["segments"][index]
    port_reason = str(moved.get("reason_code") or "")
    detail = str(moved.get("detail") or port_reason or "owner_uncertain")
    if port_reason in PRE_SUBMIT_REASONS:
        # 端口自己证明请求没越过 owner 边界：仍然不算提交，但下一次推进可以
        # 用同一个 execution id 再试一次（此时没有任何东西需要对账）。
        marker = _reason_with_detail(NOT_SENT_SUBMIT_PREFIX, detail)
    else:
        marker = _reason_with_detail(AMBIGUOUS_SUBMIT_PREFIX, detail)
    try:
        outcome = _reconcile(
            journey, home=home, env=env, now=instant, world=world, wtd=wtd, wf=wf
        )
    except JourneyError:
        outcome = _evidence("unknown", "TRAVEL_STATE_UNREADABLE")
    state = outcome.get("state")
    if state == "blocked":
        # 外来在途 / 到达结算失败：证据真矛盾，这才是 blocked 该出现的地方。
        return _fail(
            store,
            journey,
            home,
            STATUS_BLOCKED,
            outcome.get("reason_code") or "JOURNEY_EVIDENCE_BLOCKED",
            instant,
        )
    if state == "arrived":
        return _evidence(
            "arrived", None, current=outcome.get("current"), record=outcome.get("record")
        )
    record = outcome.get("record")
    if (
        state == "in_flight"
        and isinstance(record, Mapping)
        and record.get("execution_id") == segment["execution_id"]
    ):
        # 认领：只升级到 started，绝不直接认到达；travel_id/时间全部来自 owner。
        segment["status"] = SEGMENT_STARTED
        segment["travel_id"] = record["travel_id"]
        segment["started_at"] = record["started_at"]
        segment["expected_arrival_at"] = record["expected_arrival_at"]
        journey["status"] = STATUS_IN_TRANSIT
        journey["error"] = None
        _commit(home, store, journey, updated_at=_timestamp(instant))
        return _evidence("started", "TRAVEL_STARTED", record=record)
    journey["status"] = STATUS_UNKNOWN
    journey["error"] = {"reason_code": marker, "at": _timestamp(instant)}
    _commit(home, store, journey, updated_at=_timestamp(instant))
    return _evidence(
        "unknown", marker, current=outcome.get("current"), record=record
    )


def _submit_segment(
    store: dict[str, Any],
    journey: dict[str, Any],
    *,
    home: Path,
    env: Mapping[str, str],
    now: Any,
    world: Any,
    wls: Any,
    wtd: Any,
    wf: Any,
    submit_port: Any = None,
) -> dict[str, Any]:
    """先耐久写意图，再调单段 owner，最后按持久证据落盘。"""
    index = journey["current_segment"]
    segment = journey["segments"][index]
    origin = _segment_origin(journey, index)
    destination = segment["destination_location_id"]
    instant = _instant(now)
    if segment["status"] != SEGMENT_PENDING:
        return _fail(
            store, journey, home, STATUS_UNKNOWN, "SEGMENT_LEDGER_INCONSISTENT", instant
        )
    journey["status"] = STATUS_PENDING
    journey["error"] = None
    _commit(home, store, journey, updated_at=_timestamp(instant))  # ① 耐久意图
    journey = _copy_json(store["active"])
    segment = journey["segments"][index]
    if submit_port is not None:
        # W05R: the caller supplied the World's own *authorization* entry
        # (intent gate -> fresh read -> one-shot authorization -> gateway ->
        # Resolver) instead of this module reaching the Resolver's commit
        # function directly.  The port returns the same owner-status shape
        # ``apply_move_to`` does, so the evidence read-back below is unchanged:
        # the position authority is still the Resolver, never this ledger.
        moved = submit_port.submit_move(
            destination=destination,
            origin=origin,
            execution_id=segment["execution_id"],
            hermes_home=home,
            environment=env,
            now=instant,
        )
    else:
        moved = wls.apply_move_to(
            destination,
            hermes_home=home,
            environment=env,
            now=instant,
            execution_id=segment["execution_id"],
        )
    if not isinstance(moved, Mapping):
        return _fail(
            store, journey, home, STATUS_BLOCKED, "SEGMENT_OWNER_UNAVAILABLE", instant
        )
    owner_status = moved.get("status")
    if owner_status == "noop":
        return _fail(
            store, journey, home, STATUS_UNKNOWN, "SEGMENT_POSITION_CHANGED", instant
        )
    if owner_status == "uncertain":
        # 模糊提交：owner 可能真的已经出发，回执只是丢了。这里**不写 blocked**
        # （blocked 是终态，会把一次已被 owner 接受的出发永久锁死），而是先用
        # 同 execution_id 的持久证据对账：有匹配记录就认领，没有就保持 unknown
        # 等待下一次推进。不重复提交、不换 execution id。
        return _uncertain_submission(
            store,
            journey,
            home=home,
            env=env,
            now=instant,
            world=world,
            wtd=wtd,
            wf=wf,
            moved=moved,
        )
    if owner_status != "applied":
        reason = moved.get("reason_code") or "SEGMENT_REJECTED"
        return _fail(store, journey, home, STATUS_BLOCKED, str(reason), instant)
    try:
        records = _travel_index(home, env, wtd)
    except JourneyError:
        return _fail(
            store, journey, home, STATUS_BLOCKED, "TRAVEL_STATE_UNREADABLE", instant
        )
    record = records.get(segment["execution_id"])
    if record is None:
        return _fail(
            store, journey, home, STATUS_UNKNOWN, "SEGMENT_EVIDENCE_MISSING", instant
        )
    if (
        record["origin_location_id"] != origin
        or record["destination_location_id"] != destination
    ):
        return _fail(
            store, journey, home, STATUS_UNKNOWN, "SEGMENT_EVIDENCE_MISMATCH", instant
        )
    if record["status"] == TRAVEL_ARRIVED:
        # owner 侧已经到达（同 execution_id 的重放）：只补写账本，不再移动。
        return _evidence(
            "arrived",
            None,
            current=_world_position(home, world, wtd, wf),
            record=record,
        )
    segment["status"] = SEGMENT_STARTED
    segment["travel_id"] = record["travel_id"]
    segment["started_at"] = record["started_at"]
    segment["expected_arrival_at"] = record["expected_arrival_at"]
    journey["status"] = STATUS_IN_TRANSIT
    _commit(home, store, journey, updated_at=_timestamp(instant))  # ② 落盘 owner 回执
    return _evidence("started", "TRAVEL_STARTED", record=record)


def _step(
    store: dict[str, Any],
    journey: dict[str, Any],
    *,
    home: Path,
    env: Mapping[str, str],
    now: Any,
    world: Any,
    wls: Any,
    wtd: Any,
    wf: Any,
    allow_start: bool = True,
    submit_port: Any = None,
) -> dict[str, Any]:
    """一次推进：至多提交一个新段；先查证再补写；提交前过持久暂停闸门。"""
    instant = _instant(now)
    started = False
    for _ in range(3):
        try:
            outcome = _reconcile(
                journey, home=home, env=env, now=instant, world=world, wtd=wtd, wf=wf
            )
        except JourneyError:
            return _fail(
                store, journey, home, STATUS_BLOCKED, "TRAVEL_STATE_UNREADABLE", instant
            )
        state = outcome["state"]
        if state == "arrived":
            _confirm_segment(journey, now=instant, record=outcome["record"])
            _commit(home, store, journey, updated_at=_timestamp(instant))
            journey = _copy_json(store["active"])
            if journey["status"] == STATUS_COMPLETED:
                return _evidence("completed", "JOURNEY_COMPLETED")
            continue
        if state == "in_flight":
            record = outcome["record"]
            segment = journey["segments"][journey["current_segment"]]
            segment["status"] = SEGMENT_STARTED
            segment["travel_id"] = record["travel_id"]
            segment["started_at"] = record["started_at"]
            segment["expected_arrival_at"] = record["expected_arrival_at"]
            journey["status"] = STATUS_IN_TRANSIT
            journey["error"] = None
            _commit(home, store, journey, updated_at=_timestamp(instant))
            return _evidence("in_transit", None)
        if state == "feature_disabled":
            # feature gate 关闭：不启动新段、不写账本，如实报告（可恢复）。
            return _evidence("feature_disabled", outcome["reason_code"] or "TRAVEL_TIME_DISABLED")
        if state in {"blocked", "unavailable", "unknown"}:
            target_status = (
                STATUS_UNKNOWN
                if state in {"unknown", "unavailable"}
                else STATUS_BLOCKED
            )
            return _fail(
                store,
                journey,
                home,
                target_status,
                outcome["reason_code"] or "JOURNEY_EVIDENCE_UNKNOWN",
                instant,
            )
        # not_started：当前段尚未提交
        if started:
            # 一次调用只提交一段；到达这里说明证据在本次调用内被外部改变。
            journey["status"] = STATUS_PENDING
            _commit(home, store, journey, updated_at=_timestamp(instant))
            return _evidence("pending", "SEGMENT_NOT_STARTED_THIS_CALL")
        if not allow_start:
            # settle-only：只结算已出发的段，绝不发起下一段（不写账本）。
            return _evidence("settle_only", "SEGMENT_HELD_BY_SETTLE_ONLY")
        if _submission_pending_reconcile(journey):
            # 上一次提交可能已经越过 owner 边界：这一轮只对账，不再提交。
            # 只有 owner 的证据（同 execution id 的 travel 记录）能把它推下去。
            return _evidence(
                "unknown",
                (journey.get("error") or {}).get("reason_code") or AMBIGUOUS_SUBMIT_PREFIX,
            )
        gate = _control_gate(home)
        if gate["blocked"]:
            # 持久暂停/控制状态损坏：在 journey 锁内拒绝启动新段（不写账本、不改目标）。
            return _evidence(gate["status"], gate["reason_code"])
        outcome = _submit_segment(
            store,
            journey,
            home=home,
            env=env,
            now=instant,
            world=world,
            wls=wls,
            wtd=wtd,
            wf=wf,
            submit_port=submit_port,
        )
        if outcome["state"] == "started":
            return outcome
        journey = _copy_json(store["active"])
        if outcome["state"] == "arrived":
            continue
        return outcome
    return _evidence("unknown", "STEP_LIMIT_REACHED")


def _finish(
    store: Mapping[str, Any],
    home: Path,
    instant: Any,
    world: Any,
    wtd: Any,
    wf: Any,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    journey = store["active"]
    state = outcome.get("state")
    if state == "completed":
        return _projection(
            store,
            journey,
            home=home,
            now=instant,
            world=world,
            wtd=wtd,
            wf=wf,
            status=STATUS_COMPLETED,
            reason_code=outcome.get("reason_code") or "JOURNEY_COMPLETED",
        )
    if state == "started":
        return _projection(
            store,
            journey,
            home=home,
            now=instant,
            world=world,
            wtd=wtd,
            wf=wf,
            status="started",
            reason_code=outcome.get("reason_code"),
        )
    if state == "in_transit":
        return _projection(
            store, journey, home=home, now=instant, world=world, wtd=wtd, wf=wf
        )
    if state in {"paused", "control_blocked", "settle_only", "feature_disabled"}:
        # 控制闸门/settle-only 拒绝启动新段：不写账本，如实报告当前旅程状态。
        return _projection(
            store,
            journey,
            home=home,
            now=instant,
            world=world,
            wtd=wtd,
            wf=wf,
            status=state,
            reason_code=outcome.get("reason_code"),
        )
    if state == "pending":
        return _projection(
            store,
            journey,
            home=home,
            now=instant,
            world=world,
            wtd=wtd,
            wf=wf,
            status=STATUS_PENDING,
            reason_code=outcome.get("reason_code"),
        )
    status = state if state in JOURNEY_STATUSES else STATUS_BLOCKED
    return _projection(
        store,
        journey,
        home=home,
        now=instant,
        world=world,
        wtd=wtd,
        wf=wf,
        status=status,
        reason_code=outcome.get("reason_code"),
    )


# ---------------------------------------------------------------------------
# 新旅程
# ---------------------------------------------------------------------------


def _new_journey(
    *,
    world: Any,
    origin: str,
    target: str,
    route: Sequence[str],
    now: Any,
    request_key: Optional[str],
) -> dict[str, Any]:
    fingerprint = graph_fingerprint(world)
    stamp = _timestamp(now)
    digest = _request_digest(world, origin, target, route)
    journey_id = "journey-" + hashlib.sha256(
        f"{world.world_id}|{origin}|{target}|{request_key or ''}|{stamp}".encode("utf-8")
    ).hexdigest()[:32]
    segments = [
        {
            "index": index,
            "destination_location_id": destination,
            "execution_id": f"world-journey-{journey_id}-{index}",
            "travel_id": None,
            "status": SEGMENT_PENDING,
            "started_at": None,
            "expected_arrival_at": None,
            "confirmed_at": None,
        }
        for index, destination in enumerate(route)
    ]
    return {
        "journey_id": journey_id,
        "world_id": world.world_id,
        "graph_fingerprint": fingerprint,
        "route_revision": world.route_revision,
        "origin_location_id": origin,
        "target_location_id": target,
        "route": list(route),
        "segments": segments,
        "current_segment": 0,
        "status": STATUS_PENDING,
        "request_key": request_key,
        "request_digest": digest,
        "created_at": stamp,
        "updated_at": stamp,
        "error": None,
    }


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def accept_journey(
    *,
    target: str,
    hermes_home: Optional[Path | str],
    environment: Mapping[str, str],
    now: Any = None,
    request_key: Optional[str] = None,
    submit_port: Any = None,
) -> dict[str, Any]:
    """接受一个计时旅程并开始首段（有副作用：先耐久意图，再调单段 owner）。"""
    try:
        home = _home(hermes_home)
    except JourneyError:
        return _rejection("unavailable", "INVALID_WORLD_HOME")
    env = environment
    wls, wtd, wrd, wf = _world_modules()
    if not wtd.travel_enabled(env):
        return _rejection("disabled", "TRAVEL_TIME_DISABLED")
    world = wrd.active_world(env)
    instant = _instant(now)
    try:
        target_id = _id(target, "target")
    except JourneyError:
        return _rejection("rejected", "INVALID_LOCATION")
    try:
        with _lock(lock_path(home)):
            try:
                store = _read_state(home)
            except JourneyError:
                return _rejection("unavailable", "JOURNEY_STATE_CORRUPT")
            if store is None:
                store = _empty_state()
            gate = _control_gate(home)
            if gate["blocked"]:
                # 持久暂停/控制状态损坏：不接受新的移动（不创建旅程、不启动新段）。
                active = store["active"]
                return _rejection(
                    gate["status"],
                    gate["reason_code"],
                    active_target_location_id=(
                        active["target_location_id"] if active is not None else None
                    ),
                )
            existing = store["active"]
            if existing is not None:
                fingerprint = graph_fingerprint(world)
                if existing["status"] not in TERMINAL_STATUSES:
                    if (
                        existing["world_id"] != world.world_id
                        or existing["graph_fingerprint"] != fingerprint
                    ):
                        return _rejection(
                            "blocked",
                            (
                                "WORLD_CHANGED"
                                if existing["world_id"] != world.world_id
                                else "GRAPH_CHANGED"
                            ),
                            journey_id=existing["journey_id"],
                            active_target_location_id=existing["target_location_id"],
                        )
                    if request_key is not None and existing["request_key"] == request_key:
                        route = world.route_to(
                            existing["origin_location_id"], target_id
                        )
                        digest = _request_digest(
                            world,
                            existing["origin_location_id"],
                            target_id,
                            route or [],
                        )
                        if existing["request_digest"] != digest:
                            return _rejection("rejected", "REQUEST_KEY_CONFLICT")
                        return _projection(
                            store,
                            existing,
                            home=home,
                            now=instant,
                            world=world,
                            wtd=wtd,
                            wf=wf,
                        )
                    if existing["target_location_id"] == target_id:
                        return _projection(
                            store,
                            existing,
                            home=home,
                            now=instant,
                            world=world,
                            wtd=wtd,
                            wf=wf,
                        )
                    return _rejection(
                        "rejected",
                        "JOURNEY_ALREADY_ACTIVE",
                        journey_id=existing["journey_id"],
                        active_target_location_id=existing["target_location_id"],
                    )
                if request_key is not None and existing["request_key"] == request_key:
                    if existing["target_location_id"] != target_id:
                        return _rejection("rejected", "REQUEST_KEY_CONFLICT")
                    return _projection(
                        store,
                        existing,
                        home=home,
                        now=instant,
                        world=world,
                        wtd=wtd,
                        wf=wf,
                    )
                # 终态：归档保留（历史不靠删除表示），再接受新目标。
                _archive(store, existing, closed_at=_timestamp(instant))
            current = _world_position(home, world, wtd, wf)
            if current is None:
                return _rejection("unavailable", "WORLD_NOT_INITIALIZED")
            if current == target_id:
                return _rejection(
                    "already_there", "ALREADY_AT_LOCATION", location_id=target_id
                )
            route = world.route_to(current, target_id)
            if not route:
                return _rejection(
                    "rejected",
                    "LOCATION_NOT_REACHABLE",
                    location_id=current,
                    target_location_id=target_id,
                )
            journey = _new_journey(
                world=world,
                origin=current,
                target=target_id,
                route=route,
                now=instant,
                request_key=request_key,
            )
            store["active"] = journey
            store["revision"] = store["revision"] + 1
            _write_state(home, store)  # ① 耐久意图（owner 之前）
            journey = store["active"]
            outcome = _step(
                store,
                journey,
                home=home,
                env=env,
                now=instant,
                world=world,
                wls=wls,
                wtd=wtd,
                wf=wf,
                submit_port=submit_port,
            )
            return _finish(store, home, instant, world, wtd, wf, outcome)
    except JourneyError:
        return _rejection("unavailable", "JOURNEY_STORE_WRITE_FAILED")
    except Exception:
        return _rejection("unavailable", "JOURNEY_UNAVAILABLE")


def advance_journey(
    *,
    hermes_home: Optional[Path | str],
    environment: Mapping[str, str],
    now: Any = None,
    settle_only: bool = False,
    submit_port: Any = None,
) -> dict[str, Any]:
    """显式推进：结算到期段、确认 owner 结果、至多发起一个新段。"""
    try:
        home = _home(hermes_home)
    except JourneyError:
        return _rejection("unavailable", "INVALID_WORLD_HOME")
    env = environment
    wls, wtd, wrd, wf = _world_modules()
    world = wrd.active_world(env)
    instant = _instant(now)
    try:
        with _lock(lock_path(home)):
            try:
                store = _read_state(home)
            except JourneyError:
                return _rejection("unavailable", "JOURNEY_STATE_CORRUPT")
            if store is None or store["active"] is None:
                return _rejection("none", "NO_JOURNEY")
            journey = _copy_json(store["active"])
            if journey["status"] in TERMINAL_STATUSES:
                # 停止状态真实阻止启动新段。
                return _projection(
                    store, journey, home=home, now=instant, world=world, wtd=wtd, wf=wf
                )
            if journey["world_id"] != world.world_id:
                _fail(store, journey, home, STATUS_BLOCKED, "WORLD_CHANGED", instant)
                return _projection(
                    store,
                    store["active"],
                    home=home,
                    now=instant,
                    world=world,
                    wtd=wtd,
                    wf=wf,
                )
            if journey["graph_fingerprint"] != graph_fingerprint(world):
                _fail(store, journey, home, STATUS_BLOCKED, "GRAPH_CHANGED", instant)
                return _projection(
                    store,
                    store["active"],
                    home=home,
                    now=instant,
                    world=world,
                    wtd=wtd,
                    wf=wf,
                )
            if not wtd.travel_enabled(env):
                # 计时关闭时不启动新段，也不把旅程写成终态：开关恢复后可继续，
                # 但工具/引擎都不会绕过它瞬移。
                return _rejection(
                    "feature_disabled",
                    "TRAVEL_TIME_DISABLED",
                    journey_id=journey["journey_id"],
                    target_location_id=journey["target_location_id"],
                    current_segment=journey["current_segment"],
                    destination_location_id=journey["segments"][
                        min(journey["current_segment"], len(journey["route"]) - 1)
                    ]["destination_location_id"],
                    route=list(journey["route"]),
                    location_id=_world_position(home, world, wtd, wf),
                )
            outcome = _step(
                store,
                journey,
                home=home,
                env=env,
                now=instant,
                world=world,
                wls=wls,
                wtd=wtd,
                wf=wf,
                allow_start=not settle_only,
                submit_port=submit_port,
            )
            return _finish(store, home, instant, world, wtd, wf, outcome)
    except JourneyError:
        return _rejection("unavailable", "JOURNEY_STORE_WRITE_FAILED")
    except Exception:
        return _rejection("unavailable", "JOURNEY_UNAVAILABLE")


def observe_journey(
    *,
    hermes_home: Optional[Path | str],
    environment: Mapping[str, str],
    now: Any = None,
) -> dict[str, Any]:
    """真正只读：不 realize、不推进、不创建目录/文件、不改任何字节。"""
    try:
        home = _home(hermes_home)
    except JourneyError:
        return _rejection("unavailable", "INVALID_WORLD_HOME")
    env = environment
    try:
        wls, wtd, wrd, wf = _world_modules()
        world = wrd.active_world(env)
        store = _read_state(home)
    except JourneyError:
        return _rejection("unavailable", "JOURNEY_STATE_CORRUPT")
    except Exception:
        return _rejection("unavailable", "JOURNEY_UNAVAILABLE")
    if store is None or store["active"] is None:
        return _rejection(
            "none",
            "NO_JOURNEY",
            legacy_intent_present=legacy_intent_present(home),
            history_count=len(store["history"]) if store is not None else 0,
        )
    projection = _projection(
        store, store["active"], home=home, now=now, world=world, wtd=wtd, wf=wf
    )
    projection["history_count"] = len(store["history"])
    return projection


__all__ = [
    "SCHEMA_VERSION",
    "STATE_FILENAME",
    "JOURNEY_KIND",
    "JourneyError",
    "accept_journey",
    "advance_journey",
    "graph_fingerprint",
    "legacy_intent_path",
    "legacy_intent_present",
    "load_state",
    "lock_path",
    "observe_journey",
    "state_path",
]
