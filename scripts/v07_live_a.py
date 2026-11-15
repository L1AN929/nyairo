#!/usr/bin/env python3
"""0.7 唯一真实短日 A：薄封闭入口。

    python -B scripts/v07_live_a.py offline-check
    python -B scripts/v07_live_a.py prepare
    python -B scripts/v07_live_a.py audit
    python -B scripts/v07_live_a.py status
    python -B scripts/v07_live_a.py run --execute-approved

这是什么
--------
一个固定身份、固定预算、固定起点的**一次性**短日入口。它不另造生活循环，也不复制
G1 的多 phase：prepare / run 全部薄包装已有的 ``scripts/fast_day.py``（全新隔离实例、
持久业务时钟、``ExperimentRequestCap`` 每次预留前的硬上限、``bounded_failstop``、
真时间 watchdog 与正规 stop-close），审计只读盘上的正式记录。

固定身份（没有 --root，不能换旧根）
----------------------------------
* root ``/tmp/nyairo-v07-live-20261108-a``
* authority ``/tmp/nyairo-v07-live-20261108-a-authority/authority.db``
* run ``run:v07-live:20261108-a``，域 ``v07:live:20261108-a``
* history 557、本 run 新增上限 20、UTC 日 300、共同总限 1000（最坏 557+20=577）
* 逻辑起点 ``2026-11-09T11:00:00+09:00``，1:1（rate=1.0，逻辑秒=真实秒=1800）
* 最晚启动 ``2026-11-09T00:15:00+08:00``，绝对停 ``2026-11-09T00:50:00+08:00``
* 默认新生成 coastal_town、canonical、room_living（rhythm=1）、economy=1；ambient 默认

一次性
------
``run`` 在真正拉起监督进程之前用 ``O_CREAT|O_EXCL`` 写一个持久的消费标记；没有删除
路径，进程中断也不会复活，重复启动直接拒绝。这不是日常永久 failstop：``bounded_
failstop.json`` 只属于本实例这次有界实验。

输出安全
--------
stdout/audit/status 只输出固定枚举、布尔、计数、安全 id、时间与地点/Activity 状态；
不输出 prompt/completion/reasoning/short_rationale/作品正文/env/token/header/endpoint，
也不抄异常原文。读不出来的东西保留 ``UNKNOWN``，不猜 0。

不做什么
--------
不改 0.7 生活状态机、World owner、Memory 准入、权限、模型 cap/超时/重试；不注入
Decision/writer，不强制首选/轮换，不灌合成经历；不跑旧套件、不算 SHA、不删数据。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
for _entry in (REPO, REPO / "scripts", REPO / "tests", REPO / "components",
               REPO / "components" / "life" / "scripts",
               REPO / "components" / "supply",
               REPO / "components" / "world" / "scripts"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import demo_business_clock as dbc  # noqa: E402
import fast_day as fast  # noqa: E402
from life_request_budget import FAILSTOP_FILENAME  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
OFFLINE_PREFIX = "v07-live-a-offline-"
TEST_PATTERN = "test_v07_live_a.py"
CONSUME_FILENAME = "v07-live-a.consume.json"
SCHEMA_AUDIT = "v07.live.a.audit.v1"

_ENUM_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_ID_RE = re.compile(r"^(?:[a-z]+:){0,3}[A-Za-z0-9][A-Za-z0-9:_.-]{0,159}$")


@dataclass(frozen=True)
class Identity:
    """一个批次固定的身份、额度与起止线；所有字段显式，不可从命令行改。"""

    root: Path
    authority: Path
    run_name: str
    run_id: str
    domain: str
    anchor: datetime
    latest_start: datetime
    deadline: datetime
    utc_day_cap: int = 300
    cumulative_total: int = 1000
    historical_requests: int = 557
    new_cap: int = 20
    real_seconds: int = 1800
    logical_seconds: int = 1800
    cleanup_seconds: int = 60
    max_cognition_calls_per_day: int = 300
    world_gate: str = "canonical"
    room_living: bool = True
    ambient_events: bool = False
    theme: str = "coastal_town"

    def profile(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "domain_id": self.domain,
            "utc_day_cap": self.utc_day_cap,
            "cumulative_total": self.cumulative_total,
            "historical_requests": self.historical_requests,
            "new_cap": self.new_cap,
            "worst_case_cumulative": self.historical_requests + self.new_cap,
            "remaining_after_worst_case": self.cumulative_total
            - (self.historical_requests + self.new_cap),
            "real_seconds": self.real_seconds,
            "logical_seconds": self.logical_seconds,
            "rate": float(self.logical_seconds) / float(self.real_seconds),
            "logical_anchor": self.anchor.isoformat(),
            "latest_start": self.latest_start.isoformat(),
            "request_deadline": self.deadline.isoformat(),
            "cleanup_seconds": self.cleanup_seconds,
            "world_gate": self.world_gate,
            "room_living": self.room_living,
            "ambient_events": self.ambient_events,
            "theme": self.theme,
        }


IDENTITY = Identity(
    root=Path("/tmp/nyairo-v07-live-20261108-a"),
    authority=Path("/tmp/nyairo-v07-live-20261108-a-authority/authority.db"),
    run_name="v07-live:20261108-a",
    run_id="run:v07-live:20261108-a",
    domain="v07:live:20261108-a",
    anchor=datetime.fromisoformat("2026-11-09T11:00:00+09:00"),
    latest_start=datetime.fromisoformat("2026-11-09T00:15:00+08:00"),
    deadline=datetime.fromisoformat("2026-11-09T00:50:00+08:00"),
)
PROFILE = IDENTITY.profile()


# ---------------------------------------------------------------------------------------------
# 小工具：纯读与安全投影
# ---------------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path | str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _read_jsonl_tail(path: Path | str, limit: int, *, max_bytes: int = 262144
                     ) -> list[dict[str, Any]]:
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return []
    start = max(0, size - max(1, int(max_bytes)))
    try:
        with target.open("rb") as handle:
            if start:
                handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", "replace").splitlines()
    if start and lines:
        lines = lines[1:]
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, Mapping):
            rows.append(dict(row))
    return rows[-max(0, int(limit)):]


def _safe_enum(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    return text if _ENUM_RE.match(text) else default


def _safe_id(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    return text if _SAFE_ID_RE.match(text) else default


def _safe_int(value: Any, *, low: int = 0, high: int = 1_000_000_000
              ) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        return "UNKNOWN"
    return value if low <= value <= high else "UNKNOWN"


def _safe_bool(value: Any) -> Any:
    return value if isinstance(value, bool) else "UNKNOWN"


def _safe_env_int(value: Any) -> Any:
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() and len(text) < 10 else "UNKNOWN"


def _safe_feature(value: Any) -> Any:
    text = str(value if value is not None else "").strip()
    return text if text in {"0", "1"} else "UNKNOWN"


def _nonneg_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and value >= 0 else 0


def _safe_seconds(value: Any) -> Any:
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "UNKNOWN"
    number = float(value)
    return round(number, 3) if math.isfinite(number) and number >= 0 else "UNKNOWN"


def _safe_time(value: Any) -> Any:
    moment = _parse(value)
    return _iso(moment) if moment is not None else None


def _is_scratch_root(path: Path | str) -> bool:
    try:
        resolved = Path(str(path)).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False
    return resolved != temp_root and temp_root in resolved.parents


# ---------------------------------------------------------------------------------------------
# 身份 / 实例读取
# ---------------------------------------------------------------------------------------------


def _instance_view(root: Path | str) -> dict[str, Any]:
    from components.phone import life as phone_life

    phone = phone_life.load_phone_instance(root)
    nested = phone_life.read_nested_life(root)
    if nested is None:
        raise RuntimeError("NESTED_LIFE_INSTANCE_MISSING")
    state_root = Path(str(phone_life.life_state_root(root, nested)))
    return {"phone": dict(phone), "nested": dict(nested),
            "state_root": state_root,
            "world_home": Path(str(phone.get("world_home") or "")),
            "package_dir": Path(str(phone.get("world_package") or ""))}


def configure(root: Path | str, authority: Path | str, *, identity: Identity = IDENTITY,
              provision: bool = True, deadline: Optional[datetime] = None,
              allow_night_start: bool = True) -> dict[str, Any]:
    """建一个新隔离实例 + 时钟 + 实验额度；复用 fast_day 的正式 prepare。"""

    root_path = Path(str(root))
    if root_path != identity.root and not _is_scratch_root(root_path):
        return {"ok": False, "reason_code": "OFFLINE_PATH_REFUSED"}
    if identity.authority != Path(str(authority)) and not _is_scratch_root(
            Path(str(authority))):
        return {"ok": False, "reason_code": "OFFLINE_PATH_REFUSED"}
    result = fast.prepare(
        root_path, run_id=identity.run_name,
        real_seconds=identity.real_seconds,
        logical_seconds=identity.logical_seconds,
        max_new_requests=identity.new_cap,
        request_total_limit=identity.cumulative_total,
        historical_requests=identity.historical_requests,
        request_utc_day_max=identity.utc_day_cap,
        max_cognition_calls_per_day=identity.max_cognition_calls_per_day,
        bounded_failstop=True,
        request_deadline=(deadline or identity.deadline).isoformat(),
        logical_anchor=identity.anchor,
        domain_id=identity.domain,
        provision_instance=bool(provision),
        memory_authority=Path(str(authority)),
        allow_night_start=bool(allow_night_start))
    if isinstance(result, dict):
        result.setdefault("model_called", False)
        result.setdefault("request_sent", False)
    return result


# ---------------------------------------------------------------------------------------------
# 只读审计：不建锁、不推高水位、不补账
# ---------------------------------------------------------------------------------------------


def _ledger_view_pure(state_root: Path, run_id: str,
                      experiment: Mapping[str, Any]) -> dict[str, Any]:
    """直接读账本 JSON；缺账只在本实例实验闸门明确记 0 时才报 0，否则 UNKNOWN。"""

    path = Path(state_root) / "life_request_budget.json"
    if not path.exists():
        reserved = experiment.get("reserved_calls")
        high_water = experiment.get("used_high_water")
        if experiment and reserved == 0 and high_water in (0, None):
            return {"state": "absent", "utc_day_calls": 0, "run_window_calls": 0,
                    "run_window_max": None, "utc_day_max": None,
                    "source": "EXPERIMENT_CAP_RESERVED_ZERO"}
        return {"state": "absent", "utc_day_calls": "UNKNOWN",
                "run_window_calls": "UNKNOWN", "run_window_max": None,
                "utc_day_max": None, "source": "NO_ZERO_PROOF"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "corrupt", "utc_day_calls": "UNKNOWN",
                "run_window_calls": "UNKNOWN", "run_window_max": None,
                "utc_day_max": None, "source": "UNREADABLE"}
    if not isinstance(data, Mapping) or data.get("kind") != "life_request_budget" \
            or data.get("schema_version") != "life.request_budget.v1":
        return {"state": "corrupt", "utc_day_calls": "UNKNOWN",
                "run_window_calls": "UNKNOWN", "run_window_max": None,
                "utc_day_max": None, "source": "SCHEMA_MISMATCH"}
    stored_day = str(data.get("active_day") or "")
    today = _now().astimezone(timezone.utc).strftime("%Y-%m-%d")
    active = max(stored_day, today) if stored_day else today
    days = dict(data.get("days") or {})
    windows = dict(data.get("windows") or {})
    window = windows.get(run_id)
    return {
        "state": "ok",
        "utc_day": active,
        "utc_day_calls": _safe_int(days.get(active)),
        "run_id": run_id,
        "run_window_calls": (0 if window is None
                             else _safe_int((window or {}).get("calls"))),
        "window_present": run_id in windows,
        "source": "PERSISTED_LEDGER_DIRECT_READ",
    }


def _safe_observation(state_root: Path) -> dict[str, Any]:
    summary = _read_json(Path(state_root) / "resident_summary.json")
    wakes = _read_jsonl_tail(Path(state_root) / "autonomous_wakes.jsonl", 12)
    canonical = _read_json(Path(state_root) / "canonical_life_state.json")
    progress = _read_json(Path(state_root) / "activity_progress_state.json")
    activities: list[dict[str, Any]] = []
    for activity_id, activity in sorted((canonical.get("activities") or {}).items()):
        if not isinstance(activity, Mapping):
            continue
        activities.append({
            "activity_id": _safe_id(activity_id),
            "kind": _safe_enum(activity.get("kind") or activity.get("activity_kind")),
            "status": _safe_enum(activity.get("status")),
            "decision_ref": _safe_id(activity.get("decision_ref")),
        })
        if len(activities) >= 12:
            break
    rows = []
    for wake in wakes:
        rows.append({
            "at": _safe_time(wake.get("at")),
            "kind": _safe_enum(wake.get("kind")),
            "status": _safe_enum(wake.get("status")),
            "status_class": _safe_enum(wake.get("status_class")),
            "decision": _safe_enum(wake.get("decision")),
            "outcome": _safe_enum(wake.get("outcome")),
            "activity_ref": _safe_id(wake.get("activity_ref")),
            "action_id": _safe_id(wake.get("action_id")),
            "action_status": _safe_enum(wake.get("action_status")),
            "llm_calls": _safe_int(wake.get("llm_calls")),
        })
    consumed = progress.get("consumed_evidences_by_id")
    return {
        "ticks": _safe_int(summary.get("ticks")),
        "running": _safe_bool(summary.get("running")),
        "stop_reason": _safe_enum(summary.get("stop_reason")),
        "covered_seconds": _safe_int(summary.get("covered_seconds")),
        "wall_span_seconds": _safe_int(summary.get("wall_span_seconds")),
        "wakes": rows,
        "activities": activities,
        "consumed_evidences": (len(consumed) if isinstance(consumed, Mapping)
                               else "UNKNOWN"),
        "source": "RECORDED_FILES_ONLY",
        "not_claimed": ["Action成功不等于Activity完成", "缺失事实保留UNKNOWN"],
    }


def audit(root: Path | str = IDENTITY.root, *, identity: Identity = IDENTITY) -> dict[str, Any]:
    """纯读盘上的准备/时钟/额度/开关；不启动服务、不建锁、不写任何文件。"""

    root_path = Path(str(root))
    mode = _read_json(fast.mode_path(root_path))
    if not mode:
        return {"schema": SCHEMA_AUDIT, "ok": False, "prepared": False,
                "reason_code": "NOT_PREPARED", "read_only": True,
                "model_called": False, "profile": identity.profile()}

    state_root = Path(str(mode.get("state_root") or ""))
    has_state = bool(str(mode.get("state_root") or "").strip())
    try:
        inside = (has_state and state_root.resolve() != root_path.resolve()
                  and root_path.resolve() in state_root.resolve().parents)
    except OSError:
        inside = False
    clock = _read_json(dbc.clock_path(state_root)) if has_state else {}
    experiment = _read_json(dbc.experiment_path(state_root)) if has_state else {}
    run = _read_json(fast.run_path(state_root)) if has_state else {}
    marker = (root_path / "run" / CONSUME_FILENAME)
    failstop = _read_json(state_root / FAILSTOP_FILENAME) if has_state else {}
    nested = _read_json(root_path / "one-day" / "instance.json")
    env = dict(nested.get("environment") or {})
    from components.phone import service as phone_service

    phone = _read_json(phone_service.instance_path(root_path))
    features = dict(phone.get("features") or {})
    package_dir = Path(str(phone.get("world_package") or env.get("CHIYO_WORLD_PACKAGE") or ""))
    locations: list[str] = []
    location_rules: dict[str, Any] = {}
    if package_dir and package_dir.exists():
        try:
            from components.worldgen.loader import load as load_package

            locations = sorted(str(name) for name in load_package(package_dir).locations)
            location_rules = dict(_read_json(package_dir / "rules.json").get(
                "location_rules") or {})
        except Exception:
            locations = []
    ledger = _ledger_view_pure(state_root, identity.run_id, experiment) \
        if has_state else {"state": "absent", "run_window_calls": "UNKNOWN",
                           "utc_day_calls": "UNKNOWN"}
    anchor = _parse(clock.get("logical_anchor"))
    horizon = _parse(clock.get("horizon_logical"))
    rate_value = clock.get("rate")
    rate = (float(rate_value) if isinstance(rate_value, (int, float))
            and not isinstance(rate_value, bool) else None)
    checklist = {
        "run_id": str(mode.get("run_id") or ""),
        "ledger_run_id": str(mode.get("ledger_run_id") or ""),
        "domain_id": str(mode.get("domain_id") or ""),
        "clock_run_id": str(clock.get("run_id") or ""),
        "clock_domain_id": str(clock.get("domain_id") or ""),
    }
    identity_ok = (checklist["run_id"] == identity.run_name
                   and checklist["ledger_run_id"] == identity.run_id
                   and checklist["domain_id"] == identity.domain
                   and checklist["clock_run_id"] == identity.run_id
                   and checklist["clock_domain_id"] == identity.domain
                   and anchor is not None
                   and abs((anchor - identity.anchor).total_seconds()) < 1.0
                   and horizon is not None
                   and abs((horizon - (identity.anchor + timedelta(
                       seconds=identity.logical_seconds))).total_seconds()) < 1.0
                   and rate == 1.0)
    caps_ok = (experiment.get("max_new_requests") == identity.new_cap
               and experiment.get("authorized_total_limit") == identity.cumulative_total
               and experiment.get("historical_requests") == identity.historical_requests
               and env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")
               == str(identity.utc_day_cap)
               and env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")
               == str(identity.cumulative_total))
    failure_locked = bool(failstop) or (state_root / FAILSTOP_FILENAME).exists()
    report = {
        "schema": SCHEMA_AUDIT,
        "ok": bool(mode and inside and identity_ok and caps_ok),
        "prepared": True,
        "identity_ok": bool(identity_ok),
        "caps_ok": bool(caps_ok),
        "state_root_inside_root": bool(inside),
        "identity": checklist,
        "clock": {
            "mode": _safe_enum(clock.get("mode")),
            "rate": (rate if rate is not None else "UNKNOWN"),
            "no_compression": (rate == 1.0),
            "state": _safe_enum(clock.get("state")),
            "logical_anchor": _safe_time(clock.get("logical_anchor")),
            "logical_checkpoint": _safe_time(clock.get("logical_checkpoint")),
            "horizon_logical": _safe_time(clock.get("horizon_logical")),
            "real_at": _iso(_now()),
        },
        "caps": {
            "new_cap": _safe_int(experiment.get("max_new_requests")),
            "historical_requests": _safe_int(experiment.get("historical_requests")),
            "authorized_total_limit": _safe_int(experiment.get("authorized_total_limit")),
            "utc_day_cap": _safe_env_int(env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")),
            "run_window_cap": _safe_env_int(
                env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")),
            "worst_case_cumulative": (identity.historical_requests + identity.new_cap),
            "reserved_calls": _safe_int(experiment.get("reserved_calls")),
            "used_high_water": _safe_int(experiment.get("used_high_water")),
        },
        "ledger": ledger,
        "window": {"present": bool(ledger.get("window_present")),
                   "open": bool(ledger.get("window_present"))},
        "start": {
            "started_at": _safe_time(run.get("started_utc")),
            "started": bool(run.get("started_utc")),
            "stopped_at": _safe_time(run.get("stopped_utc")),
            "stop_reason": _safe_enum(run.get("stop_reason")),
            "consumed": marker.exists(),
        },
        "failure": {
            "locked": bool(failure_locked),
            "category": _safe_enum(failstop.get("failure_category")),
        },
        "switches": {
            "world_gate": identity.world_gate,
            "room_living": identity.room_living,
            "economy": (str(env.get("CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED") or "1") == "1"),
            "rhythm": identity.room_living,
            "ambient": identity.ambient_events,
            "source": "IDENTITY_RUN_SWITCHES",
            "instance_features": {
                "CHIYO_WORLD_DAILY_RHYTHM_ENABLED":
                    _safe_feature(features.get("CHIYO_WORLD_DAILY_RHYTHM_ENABLED")),
                "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED":
                    _safe_feature(features.get("CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED")),
            },
        },
        "instance": {
            "world_id": _safe_id(phone.get("world_id")),
            "theme": identity.theme,
            "locations": [name for name in locations if _SAFE_ID_RE.match(name)][:32],
            "cafe_open_at_11": _open_at(location_rules, "cafe", 11),
            "bookstore_open_at_11": _open_at(location_rules, "bookstore", 11),
            "flower_shop_open_at_11": _open_at(location_rules, "flower_shop", 11),
            "beach_open_at_11": _open_at(location_rules, "beach", 11),
            "home_goods_store_present": "home_goods_store" in locations,
            "reading_bound": bool(phone.get("reading_artifact_id")),
        },
        "observation": _safe_observation(state_root) if has_state else {},
        "read_only": True,
        "model_called": False,
        "writes": False,
        "profile": identity.profile(),
    }
    return report


def _open_at(rules: Mapping[str, Any], location_id: str, hour: int) -> Any:
    rule = rules.get(location_id)
    if not isinstance(rule, Mapping):
        return "UNKNOWN"
    try:
        open_hour = int(rule.get("open_hour"))
        close_hour = int(rule.get("close_hour"))
    except (TypeError, ValueError):
        return "UNKNOWN"
    return bool(open_hour <= hour < close_hour)


# ---------------------------------------------------------------------------------------------
# prepare / run
# ---------------------------------------------------------------------------------------------


def prepare(*, identity: Identity = IDENTITY) -> dict[str, Any]:
    now = _now()
    if now > identity.latest_start:
        return {"ok": False, "reason_code": "LATEST_START_PASSED",
                "profile": identity.profile()}
    if identity.root.is_symlink() or identity.authority.parent.is_symlink():
        return {"ok": False, "reason_code": "ROOT_NOT_FRESH"}
    if identity.root.exists() or identity.authority.parent.exists():
        return {"ok": False, "reason_code": "FORMAL_ROOT_NOT_FRESH"}
    result = configure(identity.root, identity.authority, identity=identity)
    if not result.get("ok"):
        return {"ok": False, "reason_code": _safe_enum(result.get("reason_code"),
                                                       "PREPARE_REFUSED"),
                "profile": identity.profile()}
    report = audit(identity.root, identity=identity)
    report["created"] = True
    report["model_called"] = False
    report["request_sent"] = False
    return report


def _consume_once(identity: Identity) -> bool:
    """在首 send 之前独占创建一次性标记；没有删除路径，第二次永远 False。"""

    path = identity.root / "run" / CONSUME_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        payload = {"run_id": identity.run_id, "profile": identity.profile(),
                   "consumed_at": _iso(_now()), "pid": os.getpid(),
                   "note": "one-shot: no delete/refund/replay path"}
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return False
    return True


def _run_precheck(identity: Identity, report: Mapping[str, Any],
                  now: datetime) -> Optional[str]:
    if now > identity.latest_start:
        return "LATEST_START_PASSED"
    if now + timedelta(seconds=identity.real_seconds) > identity.deadline:
        return "WINDOW_WOULD_OVERRUN"
    if not report.get("ok") or not report.get("prepared"):
        return "NOT_PREPARED"
    start = dict(report.get("start") or {})
    if start.get("started"):
        return "ALREADY_STARTED"
    if (report.get("failure") or {}).get("locked"):
        return "FAILURE_LOCKED"
    if start.get("consumed"):
        return "ALREADY_CONSUMED"
    if (report.get("window") or {}).get("present"):
        return "WINDOW_ALREADY_OPEN"
    return None


def _model_preflight(identity: Identity) -> dict[str, Any]:
    from components.phone import life as phone_life

    try:
        view = _instance_view(identity.root)
        env = phone_life.map_life_environment(view["phone"], view["nested"],
                                             base_env=dict(os.environ))
        state = fast.real_model_state(env)
    except Exception as exc:  # 只报类型名，不抄异常正文
        return {"usable": False, "reason": "MODEL_PREFLIGHT_FAILED",
                "exception_type": type(exc).__name__}
    return {"usable": bool(state.get("usable")),
            "reason": _safe_enum(state.get("reason"), "MODEL_NOT_CONFIGURED")}


def _install_cap_watch(supervisor: Any, identity: Identity) -> None:
    """到 20 次新增预留时用既有外部 stop 位收口，不新造第三个停止机制。

    只写本实例时钟目录里既有的 ``fastday_control.json``（``dbc.write_control``）；
    supervisor 的循环下一轮读时钟时会自己 ``stop()``，时钟落到 STOPPED，World/Supply/
    resident 仍由 supervisor 的 ``finally`` 正规收尾。到顶后不再有发送面。
    """

    mode = _read_json(fast.mode_path(identity.root))
    state_root = Path(str(mode.get("state_root") or ""))
    if not str(mode.get("state_root") or "").strip():
        return
    original = supervisor._sleep

    def sleep(seconds: float) -> None:
        original(seconds)
        experiment = _read_json(dbc.experiment_path(state_root))
        used = max(_nonneg_int(experiment.get("reserved_calls")),
                   _nonneg_int(experiment.get("used_high_water")))
        if used >= identity.new_cap and not dbc.read_control(state_root).get(
                "stop_requested"):
            dbc.write_control(state_root, stop_requested=True,
                              reason="EXPERIMENT_CAP_REACHED")

    supervisor._sleep = sleep


def _run_projection(result: Mapping[str, Any], post: Mapping[str, Any],
                    identity: Identity) -> dict[str, Any]:
    supervisor = dict(result.get("supervisor") or {})
    child = dict(supervisor.get("child") or {})
    owners = dict(supervisor.get("owners") or {})
    supply = dict(owners.get("supply") or {})
    world = dict(owners.get("world") or {})
    stopped = dict(supervisor.get("owners_stopped") or {})
    supply_stop = dict(stopped.get("supply") or {})
    world_stop = dict(stopped.get("world") or {})
    stop_reason = _safe_enum(supervisor.get("stop_reason"))
    resident_exit = _safe_int(child.get("returncode"), low=-255, high=255) \
        if child.get("returncode") is not None else None
    failure_locked = bool((post.get("failure") or {}).get("locked"))
    ok = (bool(result.get("ok")) and bool(post.get("ok"))
          and not failure_locked and stop_reason != "BOUNDED_RUN_FAILSTOP")
    return {
        "ok": bool(ok),
        "status": ("COMPLETED" if ok else
                   "STOPPED_WITH_FAILURE_OR_UNKNOWN" if supervisor else "UNKNOWN"),
        "run_id": identity.run_id,
        "stop_reason": stop_reason,
        "resident_exit_code": resident_exit,
        "pids": {
            "supervisor": _safe_int(supervisor.get("pid")),
            "resident": _safe_int(child.get("pid")),
            "supply": _safe_int(supply.get("pid")),
            "world": _safe_int(world.get("pid")),
        },
        "exited": {
            "resident": _safe_bool(child.get("exited")),
            "supply": bool(supply_stop) and not supply_stop.get("error"),
            "world": bool(world_stop) and not world_stop.get("error"),
        },
        "exit_codes": {
            "resident": resident_exit,
            "supply": (_safe_int(supply_stop.get("exit_code"), low=-255, high=255)
                       if supply_stop.get("exit_code") is not None else None),
            "world": (_safe_int(world_stop.get("exit_code"), low=-255, high=255)
                      if world_stop.get("exit_code") is not None else None),
        },
        "logical": {
            "start": _safe_time(supervisor.get("logical_start")),
            "end": _safe_time(supervisor.get("logical_end")),
            "start_real": _safe_time(supervisor.get("real_start")),
            "end_real": _safe_time(supervisor.get("real_end")),
            "duration_limit_seconds": _safe_seconds(
                supervisor.get("duration_limit_seconds")),
            "horizon_reached": _safe_bool((result.get("ran") or {}).get("horizon_reached")),
        },
        "wall_budget": {
            "consumed_seconds": _safe_seconds(
                (result.get("wall_budget") or {}).get("consumed_seconds")),
            "remaining_seconds": _safe_seconds(
                (result.get("wall_budget") or {}).get("remaining_seconds")),
        },
        "ledger": post.get("ledger"),
        "failure": post.get("failure"),
        "observation": post.get("observation"),
        "cap_reached": (isinstance((post.get("ledger") or {}).get("run_window_calls"), int)
                        and (post.get("ledger") or {}).get("run_window_calls")
                        >= identity.new_cap),
        "coverage": ("NOT_COVERED"
                     if not ((post.get("observation") or {}).get("wakes")
                             or (post.get("observation") or {}).get("activities"))
                     else "RECORDED"),
        "model_called": bool((post.get("ledger") or {}).get("run_window_calls") not in
                             (0, "UNKNOWN", None)),
        "read_only": False,
        "note": ("唯一一次有界运行；失败停不包装成成功，预算失败照扣、不退不补"),
    }


def run(*, identity: Identity = IDENTITY, execute_approved: bool = False,
        now: Optional[datetime] = None) -> dict[str, Any]:
    if not execute_approved:
        return {"ok": False, "reason_code": "EXECUTE_FLAG_MISSING",
                "model_called": False, "profile": identity.profile()}
    moment = now or _now()
    report = audit(identity.root, identity=identity)
    refusal = _run_precheck(identity, report, moment)
    if refusal:
        return {"ok": False, "reason_code": refusal, "model_called": False,
                "profile": identity.profile()}
    model = _model_preflight(identity)
    if not model.get("usable"):
        return {"ok": False, "reason_code": "MODEL_NOT_CONFIGURED",
                "model": {"usable": False, "reason": model.get("reason")},
                "model_called": False, "profile": identity.profile()}
    if not _consume_once(identity):
        return {"ok": False, "reason_code": "ALREADY_CONSUMED",
                "model_called": False, "profile": identity.profile()}
    previous = os.environ.get(fast.WORLD_GATE_ENV)
    os.environ[fast.WORLD_GATE_ENV] = identity.world_gate
    try:
        supervisor = fast.FastDaySupervisor(
            identity.root, poll_seconds=1, room_living=identity.room_living,
            ambient_events=identity.ambient_events)
        supervisor.child_stop_timeout = 15
        supervisor.owner_stop_timeout = 15
        _install_cap_watch(supervisor, identity)
        result = supervisor.start(duration_seconds=identity.real_seconds)
    finally:
        if previous is None:
            os.environ.pop(fast.WORLD_GATE_ENV, None)
        else:
            os.environ[fast.WORLD_GATE_ENV] = previous
    post = audit(identity.root, identity=identity)
    return _run_projection(result, post, identity)


# ---------------------------------------------------------------------------------------------
# 离线验收：新测试 + 唯一最短正式装配（零模型）+ 网络硬拒
# ---------------------------------------------------------------------------------------------


class _Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool]] = []

    def check(self, name: str, condition: Any) -> None:
        self.rows.append((name, bool(condition)))
        print("CHECK %-72s %s" % (name, "PASS" if condition else "FAIL"))

    @property
    def ok(self) -> bool:
        return all(ok for _name, ok in self.rows)


def _run_new_tests() -> dict[str, Any]:
    try:
        suite = unittest.TestLoader().discover(str(REPO / "tests"), pattern=TEST_PATTERN)
    except Exception as exc:
        return {"ok": False, "run": 0, "skipped": 0,
                "exception_type": type(exc).__name__}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "skipped": len(result.skipped), "failures": len(result.failures),
            "errors": len(result.errors)}


_ENV = {
    "NYA02_MODEL_PROVIDER": "custom",
    "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
    "NYA02_MODEL_NAME": "scripted-offline",
    "NYA02_MODEL_API_KEY": "offline-placeholder",
    "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "300",
    "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "1000",
}


def _request() -> Any:
    from agency_decision_ag1 import CognitiveDecisionRequest

    return CognitiveDecisionRequest(
        opportunity_id="dopp:offline", candidate_set_id="cset:offline",
        context_snapshot_id="dctx:offline", snapshot_fingerprint="sfp:offline",
        candidates=(), current_activity_summary=None, capacity_summary={},
        legal_decision_enums=("NO_ACTION",), allowed_reason_codes=("NO_ACTION_CHOSEN",),
        observed_fact_keys=(), policy_version="offline")


class _ScriptedFailureTransport:
    """只替 transport；正式 client/adapter/预算包装器仍真实执行。"""

    send_split_capable = True
    provider = "offline"
    model = "offline"
    sends = 0
    failure = "TRANSPORT_TIMEOUT"
    pause_guard = None

    def __init__(self, **kwargs: Any) -> None:
        self.guard = kwargs.get("pause_guard")

    def complete(self, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        if self.guard is not None:
            with self.guard.sending("model"):
                type(self).sends += 1
        else:
            type(self).sends += 1
        if self.failure:
            from agency_model_client import AgencyModelClientError

            raise AgencyModelClientError(self.failure, "PRIVATE_SENTINEL", status=400)
        return SimpleNamespace(text='{"decision":"NO_ACTION"}', latency_ms=1)


def _formal_scratch_chain(scratch: Path, checks: _Checks) -> None:
    """唯一一条最短正式装配：真实 prepare + 真实 supervisor + 注入一次脚本失败。"""

    import autonomous_life_assembly as assembly
    import g1_real_adoption as g1
    import life_pause
    from unittest import mock

    future = _now() + timedelta(days=1)
    identity = replace(IDENTITY, root=scratch / "instance",
                       authority=scratch / "authority" / "authority.db",
                       deadline=future)
    prepared = configure(identity.root, identity.authority, identity=identity,
                         deadline=future)
    checks.check("prepare creates one real isolated instance with zero model calls",
                 prepared.get("ok") is True
                 and prepared.get("model_called") is False
                 and prepared.get("request_sent") is False)
    if not prepared.get("ok"):
        return
    initialized = dict((prepared.get("provision") or {}).get("initialized") or {})
    world_init = dict(initialized.get("world") or {})
    checks.check("default coastal_town carries furniture and a takeable object",
                 bool(world_init.get("rest_furniture"))
                 and int(world_init.get("takeable_objects") or 0) >= 1
                 and int(world_init.get("substrate_placements") or 0) >= 1
                 and initialized.get("reading_artifact") is True
                 and bool(initialized.get("work_artifact") == "none_seed_disabled"))
    from components.worldgen.loader import load as load_package

    package_value = (prepared.get("provision") or {}).get("package_dir")
    try:
        package_dir = (Path(str(package_value)) if package_value
                       else _instance_view(identity.root)["package_dir"])
    except Exception:
        package_dir = Path("/nonexistent")
    try:
        places = set(str(name) for name in load_package(package_dir).locations)
    except Exception:
        places = set()
    checks.check("default coastal_town contains bookstore/cafe/beach/flower/home-goods",
                 {"bookstore", "cafe", "beach", "flower_shop",
                  "home_goods_store"} <= places)
    try:
        from components.phone import demo as phone_demo
        from components.phone import life as phone_life

        view = _instance_view(identity.root)
        overrides = {"WORLD_BODY_INTENT_MUTATION_GATE": identity.world_gate}
        overrides.update(phone_demo._serve_feature_overrides(
            room_living=identity.room_living))
        life_env = phone_life.map_life_environment(
            view["phone"], view["nested"], base_env={}, feature_overrides=overrides)
        world_child = phone_demo._world_child(
            identity.root, view["phone"], room_living=identity.room_living,
            world_intent_gate=identity.world_gate)
        binding_ok = (
            life_env.get("WORLD_BODY_INTENT_MUTATION_GATE") == "canonical"
            and life_env.get("CHIYO_WORLD_DAILY_RHYTHM_ENABLED") == "1"
            and life_env.get("CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED") == "1"
            and life_env.get("CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED") == "1"
            and world_child.environment.get("WORLD_BODY_INTENT_MUTATION_GATE") == "canonical"
            and world_child.environment.get("CHIYO_WORLD_DAILY_RHYTHM_ENABLED") == "1"
            and world_child.environment.get("CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED") == "1"
            and world_child.environment.get("CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED") == "1")
    except Exception:
        binding_ok = False
    checks.check("real prepared binding carries canonical/room/rhythm/economy to Life+World",
                 binding_ok)
    state = Path(str(prepared["state_root"]))
    pre = audit(identity.root, identity=identity)
    checks.check("audit is pure-read, prepared, start null, window absent, ledger zero",
                 pre.get("ok") is True
                 and pre["start"]["started_at"] is None
                 and pre["window"]["present"] is False
                 and pre["ledger"]["run_window_calls"] == 0
                 and pre["ledger"]["utc_day_calls"] == 0
                 and pre["caps"]["new_cap"] == 20
                 and pre["caps"]["historical_requests"] == 557
                 and pre["caps"]["authorized_total_limit"] == 1000
                 and pre["caps"]["utc_day_cap"] == 300
                 and pre["clock"]["rate"] == 1.0
                 and pre["clock"]["no_compression"] is True
                 and pre["switches"]["world_gate"] == "canonical"
                 and pre["switches"]["rhythm"] is True)
    experiment_file = dbc.experiment_path(state)
    experiment_before = (experiment_file.read_text(encoding="utf-8")
                         if experiment_file.exists() else None)
    lock_before = (state / "life_request_budget.lock").exists()
    before = sorted(str(path.relative_to(identity.root))
                    for path in identity.root.rglob("*") if path.is_file())
    audit(identity.root, identity=identity)
    audit(identity.root, identity=identity)
    after = sorted(str(path.relative_to(identity.root))
                   for path in identity.root.rglob("*") if path.is_file())
    experiment_after = (experiment_file.read_text(encoding="utf-8")
                        if experiment_file.exists() else None)
    checks.check("audit twice creates no file, no high-water and no ledger entry",
                 before == after
                 and experiment_before == experiment_after
                 and (state / "life_request_budget.lock").exists() == lock_before
                 and not (state / "life_request_budget.json").exists())

    env = dict(_ENV, CHIYO_AUTONOMOUS_RUN_ID=identity.run_id,
               NYA02_BOUNDED_FAILSTOP="1",
               NYA02_EXPERIMENT_CUMULATIVE_TOTAL="1000",
               NYA02_FASTDAY_CLOCK=str(dbc.clock_path(state)))
    supervisor = fast.FastDaySupervisor(identity.root, poll_seconds=0.1,
                                        no_act=True, offline_scripted=True,
                                        room_living=identity.room_living)
    original_sleep = supervisor._sleep
    failed: list[bool] = []

    def sleep(seconds: float) -> None:
        original_sleep(seconds)
        if not failed and (state / "resident_journal.jsonl").exists():
            with mock.patch("profile_model_transport.OpenAICompatibleTransport",
                            _ScriptedFailureTransport):
                budget = assembly.request_budget(env, state)
                guard = assembly.pause_request_admission(state, budget)
                cognition = assembly.model_cognition(
                    env, requests=budget, admission=guard, pause_guard=guard)
                try:
                    cognition.invoke(_request())
                except Exception:
                    pass
                failed.append(True)

    supervisor._sleep = sleep
    _ScriptedFailureTransport.sends = 0
    report = supervisor.start(duration_seconds=25)
    checks.check("first provider failure stops the bounded run and charges one request",
                 bool(failed)
                 and report.get("ok") is False
                 and (report.get("supervisor") or {}).get("stop_reason")
                 == "BOUNDED_RUN_FAILSTOP"
                 and _ScriptedFailureTransport.sends == 1
                 and bool((report.get("supervisor") or {}).get("child", {}).get("exited"))
                 and bool((report.get("supervisor") or {}).get("owners_stopped"))
                 and _read_json(dbc.clock_path(state)).get("state") == "STOPPED"
                 and bool(life_pause.gate(state).get("paused")))
    again = supervisor.start(duration_seconds=1)
    post = audit(identity.root, identity=identity)
    charged = post["ledger"]["run_window_calls"]
    checks.check("stopped run cannot revive and the failure stays recorded",
                 again.get("ok") is False
                 and post["failure"]["locked"] is True
                 and post["failure"]["category"] == "PROVIDER_OR_TRANSPORT_FAILURE"
                 and isinstance(charged, int) and 1 <= charged <= 20)
    consume_identity = replace(identity, root=scratch / "one-shot")
    (consume_identity.root / "run").mkdir(parents=True, exist_ok=True)
    checks.check("one-shot marker: first consumes, second refuses, none deletes it",
                 _consume_once(consume_identity) is True
                 and _consume_once(consume_identity) is False
                 and (consume_identity.root / "run" / CONSUME_FILENAME).exists())


def _formal_root_fingerprint() -> tuple:
    """Bounded before/after fingerprint of the formal root and authority parent.

    The offline entry must never *write* to the formal identity.  Once the one real bounded
    run has happened, that root necessarily exists, so "it does not exist" is no longer the
    invariant; "this invocation left it byte-for-byte where it was" is.  Paths, sizes and
    mtimes are enough for that (this is not a release manifest or a hash audit); an absent
    path reports ``None``.
    """

    def one(path: Path) -> Any:
        base = Path(path)
        if not base.exists():
            return None
        rows: list[tuple[str, bool, int, int]] = []
        try:
            for item in sorted(base.rglob("*")):
                try:
                    stat = item.stat()
                except OSError:
                    continue
                rows.append((str(item.relative_to(base)), bool(item.is_dir()),
                             int(stat.st_size), int(stat.st_mtime_ns)))
        except OSError:
            return "UNREADABLE"
        return rows

    return (one(IDENTITY.root), one(IDENTITY.authority.parent))


def offline_check() -> bool:
    import g1_real_adoption as g1
    import g2_explore_c as shared

    checks = _Checks()
    # Capture before any work: a real bounded run may already own this identity, and the
    # offline entry still must not write into it.
    formal_before = _formal_root_fingerprint()
    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    guard = shared._install_child_guard(scratch / "guard")
    try:
        with g1._offline_network_guard(scratch):
            selfcheck = shared._guard_selfcheck()
            checks.check("network is hard-refused while AF_UNIX stays available",
                         all(v is True for v in selfcheck.values()))
            tests = _run_new_tests()
            checks.check("new v07 live-a tests collect and pass",
                         tests.get("ok") is True and int(tests.get("run") or 0) > 0
                         and int(tests.get("skipped") or 0) == 0)
            _formal_scratch_chain(scratch, checks)
            child = shared._child_guard_status(guard["status_dir"])
            checks.check("owner children inherit the offline network guard",
                         child.get("all_refuse") is True)
            checks.check("offline check never touches the formal root",
                         _formal_root_fingerprint() == formal_before)
    finally:
        shared._restore_child_guard(guard)
    print()
    print("scratch kept:", scratch)
    print("RESULT: %s" % ("PASS" if checks.ok else "FAIL"))
    print("checks: %d" % len(checks.rows))
    return checks.ok


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any], code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, indent=1,
                                sort_keys=True, default=str) + "\n")
    sys.stdout.flush()
    return int(code)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="v07_live_a.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("offline-check")
    sub.add_parser("prepare")
    sub.add_parser("audit")
    sub.add_parser("status")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--execute-approved", action="store_true",
                            dest="execute_approved")
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        if args.command == "offline-check":
            return EXIT_OK if offline_check() else EXIT_REFUSED
        if args.command == "prepare":
            result = prepare()
        elif args.command in {"audit", "status"}:
            result = audit()
        else:
            result = run(execute_approved=bool(getattr(args, "execute_approved", False)))
    except Exception as exc:  # 只报类型名，不抄异常正文
        result = {"ok": False, "reason_code": "ENTRY_FAILED",
                  "exception_type": type(exc).__name__, "model_called": False}
    return _emit(result, EXIT_OK if result.get("ok") else EXIT_REFUSED)


if __name__ == "__main__":
    raise SystemExit(main())
