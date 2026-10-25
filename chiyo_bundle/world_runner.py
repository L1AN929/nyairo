"""按显式 world_home 装配的前台常驻推进器（W03）与 CLI。

职责
====
* 前台常驻：用 **monotonic** 计算调度等待，把**带时区 UTC 真实时间**传给旅程引擎，
  持续调用 ``world_journey.advance_journey``，让用户已经接受的旅程在没有新消息时
  真实到达并续行。
* 单实例：每个 world_home 一个排它 run lease（POSIX ``flock``）；另一个 run 进程
  **快速非零退出**，不等待、不成为第二个写者。lease 随进程死亡由内核释放，
  不靠删除 PID 文件、不把 PID 当作存活证明。
* 只续行已有用户旅程：不生成目标、不调用 LLM、不做宵禁/漫游/推送。
* CLI：``run`` / ``once`` / ``status`` / ``pause`` / ``resume``（``once`` 兼容 W02 入口）。

锁序（与 W02 一致，禁止反向）
=============================
``world_journey.json.lock`` → ``world_state.json.move.lock`` → ``world_travel_delivery.json.lock``

run lease 是**独立**的一把锁（``world_runner.lock``），只在进程生命周期内持有，
从不与上面三把锁嵌套获取，因此控制命令（pause/resume/status）不会被 lease 反压，
也不存在停止/暂停死锁。控制状态（pause）在 journey 锁内写入，与"启动新段"线性化。

只读保证
========
``status`` 不创建任何目录/锁/状态文件、不写 mtime、不调用 realize/read_move_state；
lease 探测使用已存在锁文件上的 ``LOCK_EX|LOCK_NB``（缺失即视为无 runner），
因此探测本身也不会创建锁文件。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import signal
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - production is POSIX
    fcntl = None

try:
    from . import world_control, world_journey
except ImportError:  # pragma: no cover - 直接以脚本路径导入时的回退
    import world_control  # type: ignore
    import world_journey  # type: ignore

RUNNER_SCHEMA = "world.runner.v1"
RUNNER_FILENAME = "world_runner.json"
LEASE_FILENAME = "world_runner.lock"
STATUS_KIND = "shiomi_world_runner_status"

DEFAULT_INTERVAL = 5.0
MIN_INTERVAL = 0.05
DEFAULT_STALE_AFTER = 90.0
MAX_BACKOFF = 60.0
WAIT_CHUNK = 0.25

RUNNER_RUNNING = "running"
RUNNER_STOPPED = "stopped"

CLASS_RUNNING = "running"
CLASS_STALE = "stale"
CLASS_STOPPED = "stopped"
CLASS_CRASHED = "crashed"
CLASS_NOT_RUNNING = "not_running"
CLASS_UNKNOWN = "unknown"

LIVENESS_HELD = "held"
LIVENESS_FREE = "free"
LIVENESS_UNKNOWN = "unknown"

TICK_OK = "ok"
TICK_IDLE = "idle"
TICK_PAUSED = "paused"
TICK_CONTROL_BLOCKED = "control_blocked"
TICK_FEATURE_DISABLED = "feature_disabled"
TICK_DEGRADED = "degraded"
TICK_SEVERE = "severe"

HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_STALE = "stale"
HEALTH_STOPPED = "stopped"
HEALTH_CRASHED = "crashed"
HEALTH_UNKNOWN = "unknown"
HEALTH_NOT_RUNNING = "not_running"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_SEVERE = 3
EXIT_LEASE_HELD = 4

_ENV_HOME_KEYS = ("CHIYO_WORLD_HOME", "HERMES_HOME")
_ENV_PACKAGE_KEY = "CHIYO_WORLD_PACKAGE"
_SEVERE_REASON_CODES = frozenset({"JOURNEY_STATE_CORRUPT"})
_DEGRADED_JOURNEY_STATUSES = frozenset({"blocked", "unknown", "rejected"})
# 连续"不健康"的 tick：退化、严重、以及控制状态损坏（fail closed 拒绝一切新段）。
_UNHEALTHY_TICK_STATUSES = frozenset({TICK_DEGRADED, TICK_SEVERE, TICK_CONTROL_BLOCKED})
_RUNTIME_KEYS = frozenset(
    {
        "schema_version",
        "instance_id",
        "pid",
        "world_id",
        "started_at",
        "interval_seconds",
        "stale_after_seconds",
        "status",
        "tick_count",
        "last_tick_at",
        "last_tick_status",
        "last_tick_reason",
        "consecutive_degraded",
        "next_tick_at",
        "stopped_at",
        "stop_reason",
    }
)
_MAX_TEXT = 160


class RunnerError(ValueError):
    """运行入口拒绝或运行状态损坏（fail closed）。"""

    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _timestamp(value: Any, field: str = "timestamp") -> str:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RunnerError("INVALID_TIMESTAMP", f"{field} is not an ISO timestamp") from exc
    else:
        raise RunnerError("INVALID_TIMESTAMP", f"{field} must be a datetime or ISO string")
    if moment.tzinfo is None:
        raise RunnerError("INVALID_TIMESTAMP", f"{field} must be timezone-aware")
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, field: str, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise RunnerError("INVALID_RUNTIME_STATE", f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > maximum:
        raise RunnerError("INVALID_RUNTIME_STATE", f"{field} is invalid")
    return text


def _home(value: Optional[Path | str]) -> Path:
    if value is None:
        raise RunnerError("INVALID_WORLD_HOME", "an explicit world home is required")
    path = Path(value).expanduser()
    if not str(value).strip() or not path.is_absolute():
        raise RunnerError("INVALID_WORLD_HOME", "world home must be an absolute path")
    return path


def runtime_path(hermes_home: Optional[Path | str]) -> Path:
    return _home(hermes_home) / "data" / "world" / RUNNER_FILENAME


def lease_path(hermes_home: Optional[Path | str]) -> Path:
    return _home(hermes_home) / "data" / "world" / LEASE_FILENAME


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def validate_interval(value: Any) -> float:
    """有限正数，且不低于 MIN_INTERVAL；拒绝 NaN/Infinity/字符串/布尔。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerError("INVALID_INTERVAL", "interval must be a number")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise RunnerError("INVALID_INTERVAL", "interval must be finite")
    if number < MIN_INTERVAL:
        raise RunnerError(
            "INVALID_INTERVAL", f"interval must be >= {MIN_INTERVAL} seconds"
        )
    return number


def _world_modules():
    scripts = str(Path(__file__).resolve().parents[1] / "components/world/scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    return (
        importlib.import_module("world_runtime_data"),
        importlib.import_module("world_travel_delivery"),
    )


# ---------------------------------------------------------------------------
# 装配校验（零创建）
# ---------------------------------------------------------------------------


def build_environment(
    world_home: Path,
    *,
    overrides: Optional[Mapping[str, str]] = None,
    world_package: Optional[str] = None,
    base: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """按显式参数装配运行环境；不继承进程里的世界配置。"""
    home = _home(world_home)
    environment: dict[str, str] = {"CHIYO_WORLD_HOME": str(home)}
    source = dict(base or {})
    for key in ("CHIYO_WORLD_HOME", "HERMES_HOME", "CHIYO_WORLD_PACKAGE", "CHIYO_WORLD_ID"):
        source.pop(key, None)
    environment.update(source)
    for key, value in (overrides or {}).items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            raise RunnerError("INVALID_ENV_OVERRIDE", "environment overrides must be KEY=VALUE")
        if key in _ENV_HOME_KEYS:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute() or candidate != home:
                raise RunnerError(
                    "ENVIRONMENT_IDENTITY_CONFLICT",
                    f"{key} must not point at another world home",
                )
            continue
        environment[key] = value
    if world_package is not None:
        candidate = Path(world_package).expanduser()
        if not candidate.is_absolute():
            raise RunnerError("INVALID_WORLD_PACKAGE", "world package must be absolute")
        existing = environment.get(_ENV_PACKAGE_KEY)
        if existing is not None and Path(existing).expanduser() != candidate:
            raise RunnerError(
                "ENVIRONMENT_PACKAGE_CONFLICT",
                "world package is configured twice with different values",
            )
        environment[_ENV_PACKAGE_KEY] = str(candidate)
    return environment


def validate_world_home(
    hermes_home: Path, environment: Mapping[str, str]
) -> dict[str, Any]:
    """校验 world_home 已存在且属于当前 world；**任何失败都不创建文件/目录**。"""
    home = _home(hermes_home)
    root = _source_root()
    if home == root or root in home.parents:
        raise RunnerError("WORLD_HOME_IN_SOURCE_TREE", "world home must live outside the source tree")
    if not home.exists():
        raise RunnerError("WORLD_HOME_MISSING", "world home does not exist")
    if not home.is_dir():
        raise RunnerError("WORLD_HOME_NOT_DIRECTORY", "world home must be a directory")
    state_path = home / "data" / "world" / "world_state.json"
    if not state_path.is_file():
        raise RunnerError(
            "WORLD_HOME_NOT_INITIALIZED", "world home has no initialized world state"
        )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerError("WORLD_HOME_STATE_UNREADABLE", "world state cannot be read") from exc
    if not isinstance(state, Mapping):
        raise RunnerError("WORLD_HOME_STATE_UNREADABLE", "world state has an invalid shape")
    wrd, wtd = _world_modules()
    try:
        world = wrd.active_world(environment)
    except Exception as exc:  # 世界包不可解析 → 不猜、不创建
        raise RunnerError("WORLD_PACKAGE_UNRESOLVED", "active world cannot be resolved") from exc
    if state.get("world_id") != world.world_id:
        raise RunnerError(
            "WORLD_HOME_IDENTITY_MISMATCH",
            "world home belongs to a different world than the configured package",
        )
    if state.get("timezone") != world.timezone_name:
        raise RunnerError(
            "WORLD_HOME_IDENTITY_MISMATCH", "world home timezone does not match the active world"
        )
    return {
        "world_id": world.world_id,
        "timezone": world.timezone_name,
        "travel_enabled": bool(wtd.travel_enabled(environment)),
    }


def feature_gates(environment: Mapping[str, str]) -> dict[str, bool]:
    def _truthy(key: str) -> bool:
        return str(environment.get(key, "")).strip().lower() in {"1", "true", "yes", "on"}

    return {
        "living_skeleton": _truthy("CHIYO_WORLD_LIVING_SKELETON_ENABLED"),
        "travel_time": _truthy("CHIYO_WORLD_TRAVEL_TIME_ENABLED"),
        "delivery_time": _truthy("CHIYO_WORLD_DELIVERY_TIME_ENABLED"),
    }


# ---------------------------------------------------------------------------
# run lease（单实例）
# ---------------------------------------------------------------------------


@contextmanager
def run_lease(hermes_home: Optional[Path | str]) -> Iterator[str]:
    """排它 run lease；已被占用时立刻抛 RunnerError（不等待）。"""
    if fcntl is None:
        raise RunnerError("LEASE_UNAVAILABLE", "run lease requires POSIX file locks")
    path = lease_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RunnerError("RUN_LEASE_HELD", "another runner holds the lease") from exc
        try:
            yield str(path)
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:  # pragma: no cover - 解锁失败即进程退出释放
                pass


def probe_lease(hermes_home: Optional[Path | str]) -> str:
    """只读探测 lease 归属：held / free / unknown。**不创建任何文件。**"""
    if fcntl is None:
        return LIVENESS_UNKNOWN
    path = lease_path(hermes_home)
    try:
        if not path.is_file():
            return LIVENESS_FREE
    except OSError:
        return LIVENESS_UNKNOWN
    try:
        handle = path.open("r")
    except OSError:
        return LIVENESS_UNKNOWN
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return LIVENESS_HELD
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return LIVENESS_FREE
    except OSError:  # pragma: no cover
        return LIVENESS_UNKNOWN
    finally:
        handle.close()


# ---------------------------------------------------------------------------
# 运行状态记录
# ---------------------------------------------------------------------------


def read_runtime(hermes_home: Optional[Path | str]) -> Optional[dict[str, Any]]:
    """只读；缺失返回 None，损坏抛 RunnerError（不创建）。"""
    path = runtime_path(hermes_home)
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerError("RUNNER_STATE_UNREADABLE", "runner state cannot be read") from exc
    return _validate_runtime(raw)


def _validate_runtime(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RUNTIME_KEYS:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state has an invalid strict shape")
    if value.get("schema_version") != RUNNER_SCHEMA:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state schema is unsupported")
    status = value.get("status")
    if status not in {RUNNER_RUNNING, RUNNER_STOPPED}:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state status is invalid")
    pid = value.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state pid is invalid")
    tick_count = value.get("tick_count")
    if isinstance(tick_count, bool) or not isinstance(tick_count, int) or tick_count < 0:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state tick_count is invalid")
    degraded = value.get("consecutive_degraded")
    if isinstance(degraded, bool) or not isinstance(degraded, int) or degraded < 0:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state backoff counter is invalid")
    interval = value.get("interval_seconds")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state interval is invalid")
    interval = float(interval)
    if math.isnan(interval) or math.isinf(interval) or interval <= 0:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state interval is invalid")
    stale_after = value.get("stale_after_seconds")
    if isinstance(stale_after, bool) or not isinstance(stale_after, (int, float)):
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state stale window is invalid")
    stale_after = float(stale_after)
    if math.isnan(stale_after) or math.isinf(stale_after) or stale_after <= 0:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state stale window is invalid")
    for field in ("instance_id", "world_id", "started_at", "last_tick_at"):
        _text(value.get(field), field)
    last_status = value.get("last_tick_status")
    if last_status not in {
        TICK_OK,
        TICK_IDLE,
        TICK_PAUSED,
        TICK_CONTROL_BLOCKED,
        TICK_FEATURE_DISABLED,
        TICK_DEGRADED,
        TICK_SEVERE,
    }:
        raise RunnerError("RUNNER_STATE_CORRUPT", "runner state tick status is invalid")
    reason = value.get("last_tick_reason")
    if reason is not None:
        _text(reason, "last_tick_reason")
    next_tick = value.get("next_tick_at")
    if next_tick is not None:
        _timestamp(next_tick, "next_tick_at")
    stopped_at = value.get("stopped_at")
    if stopped_at is not None:
        stopped_at = _timestamp(stopped_at, "stopped_at")
    stop_reason = value.get("stop_reason")
    if stop_reason is not None:
        _text(stop_reason, "stop_reason")
    if status == RUNNER_STOPPED and stopped_at is None:
        raise RunnerError("RUNNER_STATE_CORRUPT", "stopped runner state requires stopped_at")
    return {
        "schema_version": RUNNER_SCHEMA,
        "instance_id": _text(value.get("instance_id"), "instance_id"),
        "pid": pid,
        "world_id": _text(value.get("world_id"), "world_id"),
        "started_at": _timestamp(value.get("started_at"), "started_at"),
        "interval_seconds": interval,
        "stale_after_seconds": stale_after,
        "status": status,
        "tick_count": tick_count,
        "last_tick_at": _timestamp(value.get("last_tick_at"), "last_tick_at"),
        "last_tick_status": last_status,
        "last_tick_reason": reason,
        "consecutive_degraded": degraded,
        "next_tick_at": next_tick,
        "stopped_at": stopped_at,
        "stop_reason": stop_reason,
    }


def write_runtime(hermes_home: Optional[Path | str], record: Mapping[str, Any]) -> dict[str, Any]:
    """原子写入运行状态（只有 runner 自己写；控制命令不写）。"""
    normalized = _validate_runtime(record)
    path = runtime_path(hermes_home)
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
        raise RunnerError("RUNNER_STATE_WRITE_FAILED", "runner state could not be written") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return normalized


# ---------------------------------------------------------------------------
# 一次推进与分类
# ---------------------------------------------------------------------------


def classify_journey_result(result: Mapping[str, Any]) -> tuple[str, Optional[str], bool]:
    """(tick_status, reason_code, severe)。"""
    status = result.get("status")
    reason = result.get("reason_code")
    if status in {"disabled", "feature_disabled"}:
        return TICK_FEATURE_DISABLED, reason or "TRAVEL_TIME_DISABLED", False
    if status == "paused":
        return TICK_PAUSED, reason or world_control.REASON_JOURNEY_PAUSED, False
    if status == "control_blocked":
        return TICK_CONTROL_BLOCKED, reason or world_control.REASON_CONTROL_STATE_CORRUPT, False
    if status == "unavailable":
        if reason in _SEVERE_REASON_CODES:
            return TICK_SEVERE, reason, True
        return TICK_DEGRADED, reason or "JOURNEY_UNAVAILABLE", False
    if status in _DEGRADED_JOURNEY_STATUSES:
        return TICK_DEGRADED, reason or "JOURNEY_DEGRADED", False
    if status in {"started", "in_transit", "completed", "pending", "settle_only"}:
        return TICK_OK, reason, False
    if status == "none":
        return TICK_IDLE, "NO_JOURNEY", False
    return TICK_DEGRADED, reason or "JOURNEY_STATUS_UNKNOWN", False


def tick(
    *,
    home: Path,
    environment: Mapping[str, str],
    now: Optional[datetime] = None,
    settle_only: bool = False,
) -> dict[str, Any]:
    """一次显式推进（有副作用；供 once 与常驻循环共用）。"""
    moment = now if now is not None else _utcnow()
    # 每一段都是新的 Action：runner 的推进也必须经 World 自己的正式授权入口。
    # 端口不可达时传 None，引擎会失败关闭而不是退回进程内直写。
    try:
        submit_port = importlib.import_module(
            "chiyo_bundle.journey_authorization"
        ).default_submit_port(environment=environment)
    except Exception:
        submit_port = None
    result = world_journey.advance_journey(
        hermes_home=home,
        environment=environment,
        now=moment,
        settle_only=settle_only,
        submit_port=submit_port,
    )
    status, reason, severe = classify_journey_result(result)
    if status == TICK_IDLE:
        # 没有活动旅程时也如实报告控制闸门：暂停/坏控制状态不等于"闲着呢"。
        gate = world_control.gate(home)
        if gate["blocked"]:
            status, reason = gate["status"], gate["reason_code"]
    return {
        "tick_status": status,
        "reason_code": reason,
        "severe": severe,
        "journey_status": result.get("status"),
        "journey": dict(result),
        "at": _timestamp(moment),
    }


# ---------------------------------------------------------------------------
# 只读状态投影
# ---------------------------------------------------------------------------


def runner_status(
    hermes_home: Optional[Path | str],
    *,
    environment: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """只读状态：零创建、零写入、不结算到达。"""
    moment = now if now is not None else _utcnow()
    try:
        home: Optional[Path] = _home(hermes_home)
    except RunnerError:
        home = None
    world_home_value = str(hermes_home) if hermes_home is not None else None

    record: Optional[dict[str, Any]] = None
    record_state = "absent"
    record_error: Optional[str] = None
    if home is not None:
        try:
            record = read_runtime(home)
        except RunnerError as exc:
            record_state = "corrupt"
            record_error = exc.reason_code
        else:
            if record is not None:
                record_state = "ok"
    liveness = probe_lease(home) if home is not None else LIVENESS_UNKNOWN

    age: Optional[float] = None
    stale = False
    if record is not None:
        try:
            age = max(0.0, (moment - _parse_instant(record["last_tick_at"])).total_seconds())
        except RunnerError:
            age = None
        window = record["stale_after_seconds"]
        stale = age is None or age > window
    classification = _classify(record, record_state, liveness, stale)
    runner_health = _health(record, classification, stale)
    reason_codes: list[str] = []
    if record_error:
        reason_codes.append(record_error)
    if record is not None and record["last_tick_status"] in _UNHEALTHY_TICK_STATUSES:
        reason_codes.append(record["last_tick_reason"] or "JOURNEY_DEGRADED")
    if classification == CLASS_STALE:
        reason_codes.append("RUNNER_TICKS_STALE")

    control = world_control.projection(home) if home is not None else {
        "state": world_control.STATE_ABSENT,
        "paused": None,
        "revision": None,
        "reason_code": None,
    }
    journey_projection = _journey_projection(home, environment, moment)
    world_info = _world_info(home, environment, journey_projection)
    gates = feature_gates(environment) if environment else {}

    return {
        "kind": STATUS_KIND,
        "world_home": world_home_value,
        "checked_at": _timestamp(moment),
        "runner": {
            "classification": classification,
            "health": runner_health,
            "liveness": liveness,
            "liveness_evidence": _liveness_evidence(liveness, record, record_state),
            "record_state": record_state,
            "record_error": record_error,
            "record_age_seconds": None if age is None else round(age, 3),
            "stale": stale,
            "instance_id": None if record is None else record["instance_id"],
            "pid": None if record is None else record["pid"],
            "pid_is_liveness_proof": False,
            "status": None if record is None else record["status"],
            "started_at": None if record is None else record["started_at"],
            "last_tick_at": None if record is None else record["last_tick_at"],
            "last_tick_status": None if record is None else record["last_tick_status"],
            "last_tick_reason": None if record is None else record["last_tick_reason"],
            "tick_count": None if record is None else record["tick_count"],
            "consecutive_degraded": None if record is None else record["consecutive_degraded"],
            "interval_seconds": None if record is None else record["interval_seconds"],
            "stale_after_seconds": None if record is None else record["stale_after_seconds"],
            "next_tick_at": None if record is None else record["next_tick_at"],
            "stopped_at": None if record is None else record["stopped_at"],
            "stop_reason": None if record is None else record["stop_reason"],
        },
        "control": control,
        "journey": journey_projection,
        "world": world_info,
        "feature_gates": gates,
        "reason_codes": reason_codes,
    }


def _parse_instant(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:  # pragma: no cover - 记录已被校验
        raise RunnerError("RUNNER_STATE_CORRUPT", "timestamp is invalid") from exc


def _classify(
    record: Optional[Mapping[str, Any]], record_state: str, liveness: str, stale: bool
) -> str:
    if record_state == "corrupt":
        return CLASS_UNKNOWN
    if record is None:
        return CLASS_NOT_RUNNING
    if liveness == LIVENESS_HELD:
        return CLASS_STALE if stale else CLASS_RUNNING
    if record["status"] == RUNNER_STOPPED:
        return CLASS_STOPPED
    if liveness == LIVENESS_FREE:
        return CLASS_CRASHED
    return CLASS_UNKNOWN


def _health(
    record: Optional[Mapping[str, Any]], classification: str, stale: bool
) -> str:
    if classification == CLASS_RUNNING:
        if record is not None and record["last_tick_status"] in _UNHEALTHY_TICK_STATUSES:
            return HEALTH_DEGRADED
        return HEALTH_HEALTHY
    if classification == CLASS_STALE:
        return HEALTH_STALE
    if classification == CLASS_STOPPED:
        return HEALTH_STOPPED
    if classification == CLASS_CRASHED:
        return HEALTH_CRASHED
    if classification == CLASS_UNKNOWN:
        return HEALTH_UNKNOWN
    return HEALTH_NOT_RUNNING


def _liveness_evidence(
    liveness: str, record: Optional[Mapping[str, Any]], record_state: str
) -> str:
    if record_state == "corrupt":
        return "runner state record is unreadable; liveness cannot be confirmed"
    if liveness == LIVENESS_HELD:
        return "run lease is held by a live process (flock probe failed to acquire)"
    if liveness == LIVENESS_FREE:
        return "run lease is not held; any pid in the record is historical only"
    if record is None:
        return "no run lease file and no runner record were found"
    return "run lease could not be probed (no POSIX file locks)"


def _journey_projection(
    home: Optional[Path], environment: Optional[Mapping[str, str]], moment: datetime
) -> dict[str, Any]:
    if home is None or not environment:
        return {"status": "unknown", "reason_code": "WORLD_ENVIRONMENT_UNAVAILABLE"}
    try:
        return world_journey.observe_journey(
            hermes_home=home, environment=environment, now=moment
        )
    except Exception:
        return {"status": "unknown", "reason_code": "JOURNEY_OBSERVE_FAILED"}


def _world_info(
    home: Optional[Path],
    environment: Optional[Mapping[str, str]],
    journey_projection: Mapping[str, Any],
) -> dict[str, Any]:
    info = {
        "world_id": journey_projection.get("world_id"),
        "graph_matches": journey_projection.get("graph_matches"),
        "legacy_intent_present": journey_projection.get("legacy_intent_present"),
        "world_home_state": "unknown",
    }
    if home is None:
        return info
    if not home.exists():
        info["world_home_state"] = "missing"
        return info
    if not home.is_dir():
        info["world_home_state"] = "not_directory"
        return info
    if not (home / "data" / "world" / "world_state.json").is_file():
        info["world_home_state"] = "not_initialized"
        return info
    info["world_home_state"] = "initialized"
    return info


# ---------------------------------------------------------------------------
# 常驻循环
# ---------------------------------------------------------------------------


def _default_sleeper(seconds: float) -> None:  # pragma: no cover - 真实等待
    time.sleep(seconds)


def run_forever(
    *,
    home: Path,
    environment: Mapping[str, str],
    interval: float,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = _default_sleeper,
    utcnow: Callable[[], datetime] = _utcnow,
    max_ticks: Optional[int] = None,
    settle_only: bool = False,
    install_signal_handlers: bool = True,
    stop_requested: Optional[Callable[[], bool]] = None,
    on_tick: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """前台常驻循环：monotonic 调度 + UTC 真实时间推进 + 有限退避 + 干净退出。"""
    base_interval = validate_interval(interval)
    world_id = environment.get("CHIYO_WORLD_ID") or "unknown"
    started = utcnow()
    stale_after = max(DEFAULT_STALE_AFTER, base_interval * 3)
    instance_id = "run-" + hashlib.sha256(
        f"{home}|{os.getpid()}|{_timestamp(started)}".encode("utf-8")
    ).hexdigest()[:24]
    stop_flag = {"requested": False, "signal": None}

    def _handle(signum, _frame):  # pragma: no cover - 真实信号
        stop_flag["requested"] = True
        stop_flag["signal"] = signal.Signals(signum).name

    previous_handlers: dict[int, Any] = {}
    if install_signal_handlers:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                previous_handlers[signum] = signal.signal(signum, _handle)
            except (ValueError, OSError):  # pragma: no cover - 非主线程
                pass

    def _external_stop() -> bool:
        return bool(stop_requested is not None and stop_requested())

    record = {
        "schema_version": RUNNER_SCHEMA,
        "instance_id": instance_id,
        "pid": os.getpid(),
        "world_id": world_id,
        "started_at": _timestamp(started),
        "interval_seconds": base_interval,
        "stale_after_seconds": stale_after,
        "status": RUNNER_RUNNING,
        "tick_count": 0,
        "last_tick_at": _timestamp(started),
        "last_tick_status": TICK_IDLE,
        "last_tick_reason": "STARTING",
        "consecutive_degraded": 0,
        "next_tick_at": None,
        "stopped_at": None,
        "stop_reason": None,
    }
    write_runtime(home, record)

    ticks = 0
    degraded_streak = 0
    last_status = TICK_IDLE
    last_reason: Optional[str] = "STARTING"
    stop_reason = "MAX_TICKS"
    severe_reason: Optional[str] = None
    exit_code = EXIT_OK
    try:
        deadline = clock()
        while True:
            if stop_flag["requested"] or _external_stop():
                stop_reason = stop_flag["signal"] or "STOP_REQUESTED"
                break
            if max_ticks is not None and ticks >= max_ticks:
                stop_reason = "MAX_TICKS"
                break
            outcome = tick(
                home=home,
                environment=environment,
                now=utcnow(),
                settle_only=settle_only,
            )
            ticks += 1
            last_status = outcome["tick_status"]
            last_reason = outcome["reason_code"]
            if outcome["tick_status"] in _UNHEALTHY_TICK_STATUSES:
                degraded_streak += 1
            else:
                degraded_streak = 0
            backoff = min(base_interval * (2 ** min(degraded_streak, 6)), MAX_BACKOFF)
            deadline = deadline + (base_interval if degraded_streak == 0 else backoff)
            now_monotonic = clock()
            if deadline < now_monotonic:
                # 迟到 tick 不补执行：重新以"当前 + 间隔"为基准，不忙等、不跳过真实步行时间。
                deadline = now_monotonic + base_interval
            next_tick_at = _timestamp(utcnow() + timedelta(seconds=max(0.0, deadline - now_monotonic)))
            record.update(
                {
                    "tick_count": ticks,
                    "last_tick_at": outcome["at"],
                    "last_tick_status": last_status,
                    "last_tick_reason": last_reason,
                    "consecutive_degraded": degraded_streak,
                    "next_tick_at": next_tick_at,
                }
            )
            write_runtime(home, record)
            if on_tick is not None:
                on_tick(dict(outcome))
            if outcome["severe"]:
                severe_reason = outcome["reason_code"] or "SEVERE_RUNTIME_STATE"
                stop_reason = "SEVERE_RUNTIME_STATE"
                exit_code = EXIT_SEVERE
                break
            _wait_until(
                deadline,
                clock=clock,
                sleeper=sleeper,
                stop=lambda: stop_flag["requested"] or _external_stop(),
            )
    finally:
        record.update(
            {
                "status": RUNNER_STOPPED,
                "stopped_at": _timestamp(utcnow()),
                "stop_reason": stop_reason,
                "last_tick_status": last_status,
                "last_tick_reason": last_reason,
                "tick_count": ticks,
                "consecutive_degraded": degraded_streak,
                "next_tick_at": None,
            }
        )
        try:
            write_runtime(home, record)
        except RunnerError:  # pragma: no cover - 停止时写失败也不能抛
            pass
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):  # pragma: no cover
                pass

    return {
        "kind": STATUS_KIND,
        "instance_id": instance_id,
        "world_id": world_id,
        "ticks": ticks,
        "last_tick_status": last_status,
        "last_tick_reason": last_reason,
        "stop_reason": stop_reason,
        "severe_reason": severe_reason,
        "exit_code": exit_code,
    }


def _wait_until(
    deadline: float,
    *,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
    stop: Callable[[], bool],
    chunk: float = WAIT_CHUNK,
) -> None:
    """有限步等待：每步最多 chunk 秒，便于信号在 <=chunk 内生效；不忙等。"""
    while True:
        if stop():
            return
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleeper(min(remaining, chunk))


# ---------------------------------------------------------------------------
# 控制命令（pause / resume）
# ---------------------------------------------------------------------------


def set_pause(
    hermes_home: Optional[Path | str],
    environment: Mapping[str, str],
    *,
    paused: bool,
    now: Optional[datetime] = None,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """在 journey 锁内切换暂停位（与"启动新段"线性化）。"""
    home = _home(hermes_home)
    validate_world_home(home, environment)
    moment = now if now is not None else _utcnow()
    with world_journey.journey_lock(home):
        record = world_control.set_paused(
            home, paused=paused, now=moment, reason=reason
        )
    return {
        "kind": STATUS_KIND,
        "command": "pause" if paused else "resume",
        "world_home": str(home),
        "control": {"state": world_control.STATE_OK, "reason_code": None, **record},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_env_pairs(items: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not key or not separator:
            raise RunnerError("INVALID_ENV_OVERRIDE", f"invalid --env {item!r}; expected KEY=VALUE")
        overrides[key] = value
    return overrides


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="life_heartbeat.py",
        description="NyAiro 前台常驻旅程推进器（不生成目标、不安装服务）",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="once",
        choices=["run", "once", "status", "pause", "resume"],
        help="默认 once（兼容 W02 旧入口：不带子命令即一次推进）",
    )
    parser.add_argument("--world-home", required=True, help="当前世界私有状态目录（绝对路径）")
    parser.add_argument("--world-package", default=None, help="活动世界包目录（CHIYO_WORLD_PACKAGE）")
    parser.add_argument(
        "--env", action="append", default=[], metavar="KEY=VALUE", help="额外环境覆盖，可重复"
    )
    parser.add_argument(
        "--interval", default=str(DEFAULT_INTERVAL), help=f"循环间隔秒数（>= {MIN_INTERVAL}）"
    )
    parser.add_argument("--max-ticks", type=int, default=None, help="只跑 N 个 tick 后正常退出（测试用）")
    parser.add_argument("--now", default=None, help="注入时间（ISO8601，仅 once 允许）")
    parser.add_argument("--reason", default=None, help="pause/resume 的原因标记")
    parser.add_argument("--settle-only", action="store_true", help="只结算已出发的段，不发起下一段")
    return parser.parse_args(argv)


def _load_environment(args: argparse.Namespace, home: Path) -> dict[str, str]:
    overrides = _parse_env_pairs(list(args.env))
    return build_environment(
        home, overrides=overrides, world_package=args.world_package
    )


def _emit_error(reason_code: str, message: str) -> None:
    """错误输出走 stderr：stdout 只承载命令结果（便于机器读取与兼容 W02 入口）。"""
    print(
        json.dumps(
            {"ok": False, "reason_code": reason_code, "message": message},
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        home = _home(args.world_home)
    except RunnerError as exc:
        _emit_error(exc.reason_code, str(exc))
        return EXIT_USAGE
    try:
        environment = _load_environment(args, home)
    except RunnerError as exc:
        _emit_error(exc.reason_code, str(exc))
        return EXIT_USAGE

    if args.command == "status":
        # 只读：缺目录/无 runner/坏记录都要如实分类，且不创建任何东西。
        _emit(runner_status(home, environment=environment))
        return EXIT_OK

    try:
        info = validate_world_home(home, environment)
    except RunnerError as exc:
        _emit_error(exc.reason_code, str(exc))
        return EXIT_USAGE
    environment.setdefault("CHIYO_WORLD_ID", info["world_id"])

    if args.command in {"pause", "resume"}:
        try:
            report = set_pause(
                home,
                environment,
                paused=args.command == "pause",
                reason=args.reason or f"cli:{args.command}",
            )
        except (RunnerError, world_control.ControlError) as exc:
            reason = getattr(exc, "reason_code", "CONTROL_COMMAND_FAILED")
            _emit_error(reason, str(exc))
            return EXIT_REFUSED
        report["ok"] = True
        _emit(report)
        return EXIT_OK

    if args.command == "once":
        moment: Optional[datetime] = None
        if args.now is not None:
            try:
                moment = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
            except ValueError:
                _emit_error("INVALID_NOW", "--now must be an ISO8601 timestamp")
                return EXIT_USAGE
            if moment.tzinfo is None:
                _emit_error("INVALID_NOW", "--now must include a timezone")
                return EXIT_USAGE
        outcome = tick(
            home=home, environment=environment, now=moment, settle_only=args.settle_only
        )
        payload = dict(outcome["journey"])
        payload["reason_code"] = payload.get("reason_code") or outcome["reason_code"]
        payload["tick_status"] = outcome["tick_status"]
        payload["severe"] = outcome["severe"]
        payload["at"] = outcome["at"]
        payload["ok"] = outcome["tick_status"] not in _UNHEALTHY_TICK_STATUSES
        _emit(payload)
        if outcome["severe"]:
            return EXIT_SEVERE
        return EXIT_OK if payload["ok"] else EXIT_REFUSED

    # run（前台常驻）
    if args.now is not None:
        _emit_error("FIXED_CLOCK_NOT_ALLOWED", "run refuses --now; use once for injected time")
        return EXIT_USAGE
    try:
        interval = validate_interval(float(args.interval))
    except (RunnerError, ValueError) as exc:
        _emit_error(getattr(exc, "reason_code", "INVALID_INTERVAL"), str(exc))
        return EXIT_USAGE
    if args.max_ticks is not None and args.max_ticks <= 0:
        _emit_error("INVALID_MAX_TICKS", "--max-ticks must be a positive integer")
        return EXIT_USAGE
    lease = None
    try:
        with run_lease(home) as lease:
            summary = run_forever(
                home=home,
                environment=environment,
                interval=interval,
                max_ticks=args.max_ticks,
                settle_only=args.settle_only,
            )
    except RunnerError as exc:
        code = EXIT_LEASE_HELD if exc.reason_code == "RUN_LEASE_HELD" else EXIT_REFUSED
        _emit_error(exc.reason_code, str(exc))
        return code
    summary["ok"] = summary["exit_code"] == EXIT_OK
    summary["lease"] = lease
    _emit(summary)
    return int(summary["exit_code"])


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
