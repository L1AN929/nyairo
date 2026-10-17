#!/usr/bin/env python3
"""Fact-only daily rhythm, availability, and location affordance projection."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import world_runtime_data as wrd

FEATURE_ENV = "CHIYO_WORLD_DAILY_RHYTHM_ENABLED"
WORLD_ID = "shiomi_city"
WORLD_TIMEZONE = "Asia/Tokyo"
SCHEMA_VERSION = "world.daily.rhythm.v1"


@dataclass(frozen=True)
class _LocationRule:
    location_id: str
    open_hour: Optional[int]
    close_hour: Optional[int]
    weekdays_only: bool
    affordances: tuple[tuple[str, str], ...]


_RULES = {
    "home": _LocationRule(
        "home", None, None, False,
        (("REST", "\u5728\u8fd9\u91cc\u4f11\u606f"), ("READ", "\u5728\u8fd9\u91cc\u9605\u8bfb"), ("USE_PHONE", "\u5728\u8fd9\u91cc\u4f7f\u7528\u624b\u673a")),
    ),
    "nearby_street": _LocationRule(
        "nearby_street", None, None, False,
        (("WALK", "\u5728\u9644\u8fd1\u8857\u9053\u6563\u6b65"), ("LOOK_AROUND", "\u5728\u8fd9\u91cc\u770b\u770b\u5468\u56f4")),
    ),
    "school": _LocationRule(
        "school", 8, 18, True,
        (("ATTEND_CLASS", "\u5728\u5b66\u6821\u4e0a\u8bfe"), ("SELF_STUDY", "\u5728\u5b66\u6821\u81ea\u4e60"), ("STAY_AT_SCHOOL", "\u7559\u5728\u5b66\u6821")),
    ),
    "convenience_store": _LocationRule(
        "convenience_store", 7, 23, False,
        (("BROWSE_STORE", "\u5728\u4fbf\u5229\u5e97\u770b\u770b"),),
    ),
    "park": _LocationRule(
        "park", 6, 22, False,
        (("WALK", "\u5728\u516c\u56ed\u6563\u6b65"), ("SIT", "\u5728\u516c\u56ed\u5750\u4e00\u4f1a\u513f"), ("LOOK_AROUND", "\u5728\u8fd9\u91cc\u770b\u770b\u5468\u56f4")),
    ),
    "home_goods_store": _LocationRule(
        "home_goods_store", 10, 20, False,
        (),
    ),
}


class DailyRhythmError(ValueError):
    """Raised for malformed daily-rhythm inputs."""


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def feature_enabled(environment: Optional[Mapping[str, str]] = None) -> bool:
    return _truthy((environment or os.environ).get(FEATURE_ENV))


def _zone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise DailyRhythmError("timezone_name must be a non-empty string")
    try:
        return ZoneInfo(timezone_name.strip())
    except ZoneInfoNotFoundError as exc:
        raise DailyRhythmError("unknown timezone") from exc


def _local(now: Optional[datetime], timezone_name: str) -> datetime:
    instant = datetime.now(timezone.utc) if now is None else now
    if not isinstance(instant, datetime):
        raise DailyRhythmError("now must be a datetime")
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(_zone(timezone_name))


def daily_rhythm(
    now: Optional[datetime],
    *,
    timezone_name: str = WORLD_TIMEZONE,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    local = _local(now, timezone_name)
    world = wrd.active_world(environment)
    if 5 <= local.hour < 12:
        daypart = "morning"
    elif 12 <= local.hour < 18:
        daypart = "daytime"
    elif 18 <= local.hour < 22:
        daypart = "evening"
    else:
        daypart = "night"
    return {
        "schema_version": SCHEMA_VERSION,
        "world_id": world.world_id,
        "timezone": timezone_name,
        "local_date": local.date().isoformat(),
        "weekday": local.isoweekday(),
        "day_type": "weekend" if local.weekday() >= 5 else "weekday",
        "daypart": daypart,
    }


def _rule(
    location_id: str, environment: Optional[Mapping[str, str]] = None
) -> _LocationRule:
    """活动世界的地点规则（未配置世界包时即汐见市 _RULES）。"""
    if not isinstance(location_id, str):
        raise DailyRhythmError("unknown location_id")
    world = wrd.active_world(environment)
    try:
        data = world.location_rule(location_id)
    except wrd.WorldDataError:
        raise DailyRhythmError("unknown location_id") from None
    return _LocationRule(
        location_id,
        data["open_hour"],
        data["close_hour"],
        data["weekdays_only"],
        tuple(data["affordances"]),
    )


def location_availability(
    location_id: str,
    now: Optional[datetime],
    *,
    timezone_name: str = WORLD_TIMEZONE,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    rule = _rule(location_id, environment)
    local = _local(now, timezone_name)
    if rule.open_hour is None:
        status, reason = "OPEN", "ALWAYS_AVAILABLE"
    elif rule.weekdays_only and local.weekday() >= 5:
        status, reason = "CLOSED", "WEEKEND_CLOSED"
    elif rule.open_hour <= local.hour < (rule.close_hour or 0):
        status, reason = "OPEN", "WITHIN_OPEN_HOURS"
    else:
        status, reason = "CLOSED", "OUTSIDE_OPEN_HOURS"
    return {
        "location_id": rule.location_id,
        "status": status,
        "reason_code": reason,
    }


def affordances_for_location(
    location_id: str,
    now: Optional[datetime],
    *,
    timezone_name: str = WORLD_TIMEZONE,
    environment: Optional[Mapping[str, str]] = None,
) -> list[dict[str, str]]:
    rule = _rule(location_id, environment)
    if (
        location_availability(
            location_id, now, timezone_name=timezone_name, environment=environment
        )["status"]
        != "OPEN"
    ):
        return []
    return [
        {"affordance_id": affordance_id, "description": description}
        for affordance_id, description in rule.affordances
    ]


def all_location_availability(
    now: Optional[datetime],
    *,
    timezone_name: str = WORLD_TIMEZONE,
    location_ids: Optional[Iterable[str]] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> list[dict[str, str]]:
    selected = (
        list(_RULES) if location_ids is None else list(location_ids)
    )
    return [
        location_availability(
            location_id, now, timezone_name=timezone_name, environment=environment
        )
        for location_id in selected
    ]


# ---------------------------------------------------------------------------
# 有限未来营业预期（World 侧只读事实，供 Agenda 后续认领）
# ---------------------------------------------------------------------------

#: 结果 kind；与其它 World 投影一样带 kind + schema_version。
OPENING_KIND = "world_location_opening_expectation"
OPENING_SCHEMA_VERSION = "world.location.opening_expectation.v1"
#: 有限搜索上界（天）：再远的“下次开门”不当成可用的未来预期。
MAX_EXPECTATION_HORIZON_DAYS = 8
#: 规则来源标签；与 ``world_daily_opportunities`` 的 ``schedule_source`` 同一套词汇。
RULE_SOURCE_PACKAGE = "world_package_schedule"
RULE_SOURCE_BUILTIN = "world_daily_rhythm_builtin"
#: 指纹覆盖的规范化字段（只是变更识别，不是签名）。
_FINGERPRINT_SCOPE = (
    "world_id+location_id+timezone+open_hour+close_hour+weekdays_only"
)

_OPENING_NOTE = (
    "只按世界自带营业规则预计开放窗口，不保证真会营业；到点必须重读原规则"
    "（requires_recheck），也不构成动作授权（action_grant: false）。"
)


def _aware_instant(now: Optional[datetime]) -> datetime:
    """严格 aware 时钟：naive/非 datetime 一律拒绝，不替调用方猜时区。"""
    instant = datetime.now(timezone.utc) if now is None else now
    if (
        not isinstance(instant, datetime)
        or instant.tzinfo is None
        or instant.utcoffset() is None
    ):
        raise DailyRhythmError("now must be a timezone-aware datetime")
    return instant


def _opening_source(world: Any) -> str:
    custom = bool(getattr(world, "is_custom", False))
    return RULE_SOURCE_PACKAGE if custom else RULE_SOURCE_BUILTIN


def _canonical_opening_rule(rule_data: Any) -> Optional[dict[str, Any]]:
    """把 owner 规则规范成可比较形状；读不懂返回 None（不补默认值）。"""
    if not isinstance(rule_data, Mapping):
        return None
    open_hour = rule_data.get("open_hour")
    close_hour = rule_data.get("close_hour")
    weekdays_only = rule_data.get("weekdays_only", False)
    for value in (open_hour, close_hour):
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 23
        ):
            return None
    if not isinstance(weekdays_only, bool):
        return None
    return {
        "open_hour": open_hour,
        "close_hour": close_hour,
        "weekdays_only": weekdays_only,
    }


def _rule_fingerprint(
    world_id: str, location_id: str, timezone_name: str, rule: Mapping[str, Any]
) -> str:
    """规则源的规范化指纹：只用于变更识别，不是签名也不是权限。"""
    payload = {
        "location_id": location_id,
        "rule": {
            "close_hour": rule["close_hour"],
            "open_hour": rule["open_hour"],
            "weekdays_only": rule["weekdays_only"],
        },
        "timezone": timezone_name,
        "world_id": world_id,
    }
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _opening_unavailable(
    reason_code: str,
    *,
    world_id: Optional[str] = None,
    location_id: Optional[str] = None,
    source: Optional[str] = None,
    timezone_name: Optional[str] = None,
    as_of: Optional[datetime] = None,
    rule_kind: Optional[str] = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": OPENING_KIND,
        "schema_version": OPENING_SCHEMA_VERSION,
        "status": "unavailable",
        "reason_code": reason_code,
        "horizon_days": MAX_EXPECTATION_HORIZON_DAYS,
        "expected_open_at": None,
        "window_open_at": None,
        "window_end": None,
        "always_open": False,
        "window_state": None,
        "requires_recheck": True,
        "action_grant": False,
        "note": _OPENING_NOTE,
    }
    if world_id is not None:
        result["world_id"] = world_id
    if location_id is not None:
        result["location_id"] = location_id
    if source is not None:
        result["source"] = source
    if timezone_name is not None:
        result["timezone"] = timezone_name
    if as_of is not None:
        result["as_of"] = as_of.isoformat(timespec="seconds")
    if rule_kind is not None:
        result["rule_kind"] = rule_kind
    return result


def _hourly_window(
    as_of: datetime, rule: Mapping[str, Any], zone: ZoneInfo
) -> Optional[dict[str, Any]]:
    """按 ``location_availability`` 同一小时语义找当前/下一次窗口。

    窗口一律在同一天内（``open_hour < close_hour``），因此不会凭空造出跨午夜
    营业事实。搜索受 ``MAX_EXPECTATION_HORIZON_DAYS`` 限制；找不到返回 None。
    """
    open_hour = rule["open_hour"]
    close_hour = rule["close_hour"]
    weekdays_only = rule["weekdays_only"]
    if (
        not (weekdays_only and as_of.weekday() >= 5)
        and open_hour <= as_of.hour < close_hour
    ):
        return {
            "window_state": "open_now",
            "expected_open_at": as_of,
            "window_open_at": as_of.replace(
                hour=open_hour, minute=0, second=0, microsecond=0
            ),
            "window_end": as_of.replace(
                hour=close_hour, minute=0, second=0, microsecond=0
            ),
        }
    horizon_end = as_of + timedelta(days=MAX_EXPECTATION_HORIZON_DAYS)
    for offset in range(0, MAX_EXPECTATION_HORIZON_DAYS + 1):
        day = (as_of + timedelta(days=offset)).date()
        if weekdays_only and day.weekday() >= 5:
            continue
        start = datetime(day.year, day.month, day.day, open_hour, tzinfo=zone)
        end = datetime(day.year, day.month, day.day, close_hour, tzinfo=zone)
        if start <= as_of:
            continue
        if start > horizon_end:
            return None
        return {
            "window_state": "opens_later",
            "expected_open_at": start,
            "window_open_at": start,
            "window_end": end,
        }
    return None


def location_opening_expectation(
    location_id: str,
    now: Optional[datetime],
    *,
    timezone_name: Optional[str] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """活动世界里某个地点的有限未来营业预期（只读、非授权、非营业保证）。

    规则来源只有一个：``world_runtime_data.active_world().location_rule()``，
    即活动世界的 ``rules.location_rules``（未配置世界包时是汐见市内建规则）。
    判断“现在开没开”与 ``location_availability`` 同一小时语义
    （``open_hour <= hour < close_hour``，``weekdays_only`` 周末整天闭店，
    ``open_hour is None`` 视为常开）。

    返回 ``status: "available"`` 时给：

    * ``expected_open_at``：预计从这个时刻起可以进去（现在开着就等于 ``as_of``，
      即“可考虑的起点”）；``window_end``：该窗口结束（不含），常开为 ``None``；
    * ``window_state``：``open_now`` / ``opens_later`` / ``always_open``；
    * ``rule_fingerprint``：规则源（world+地点+时区+真实规则）的规范化摘要，
      只做变更识别，**不是签名，也不是营业或动作授权**；
    * ``requires_recheck: true`` / ``action_grant: false``：到点必须重读规则。

    规则读不懂、地点未知、时区未知、schema 表达不了的规则（缺 ``close_hour``、
    ``close_hour < open_hour`` 的跨午夜、``open_hour == close_hour`` 的空窗口）、
    搜索上界内找不到窗口，都返回 ``status: "unavailable"`` 加 ``reason_code``：
    不给“可能开着”的默认值，也不另造一套营业事实。

    ``now`` 必须是 timezone-aware（naive 直接抛 ``DailyRhythmError``）；
    ``timezone_name`` 省略时用活动世界的时区。室内区域节点（``place/area``）委托
    到所属地点的规则，返回的 ``location_id`` 是地点 id。
    """
    try:
        world = wrd.active_world(environment)
    except wrd.WorldDataError:
        return _opening_unavailable("WORLD_PACKAGE_UNREADABLE")
    except Exception:
        return _opening_unavailable("WORLD_DATA_UNAVAILABLE")

    world_id = getattr(world, "world_id", None)
    source = _opening_source(world)
    if not isinstance(location_id, str) or not location_id.strip():
        return _opening_unavailable(
            "LOCATION_UNKNOWN", world_id=world_id, source=source
        )
    place_id = wrd.WorldData.node_place(location_id.strip())
    try:
        rule_data = world.location_rule(location_id.strip())
    except wrd.WorldDataError:
        return _opening_unavailable(
            "LOCATION_UNKNOWN",
            world_id=world_id,
            location_id=place_id,
            source=source,
        )
    except Exception:
        return _opening_unavailable(
            "LOCATION_RULE_UNREADABLE",
            world_id=world_id,
            location_id=place_id,
            source=source,
        )
    rule = _canonical_opening_rule(rule_data)
    if rule is None:
        return _opening_unavailable(
            "LOCATION_RULE_UNREADABLE",
            world_id=world_id,
            location_id=place_id,
            source=source,
        )

    world_timezone = getattr(world, "timezone_name", WORLD_TIMEZONE)
    requested_timezone = timezone_name
    effective_timezone = (
        requested_timezone
        if isinstance(requested_timezone, str) and requested_timezone.strip()
        else world_timezone
    )
    try:
        zone = _zone(effective_timezone)
    except DailyRhythmError:
        return _opening_unavailable(
            "TIMEZONE_UNKNOWN",
            world_id=world_id,
            location_id=place_id,
            source=source,
            timezone_name=str(effective_timezone),
        )
    as_of = _aware_instant(now).astimezone(zone)

    open_hour = rule["open_hour"]
    close_hour = rule["close_hour"]
    if open_hour is None:
        rule_kind = "always_open"
    elif close_hour is None:
        return _opening_unavailable(
            "OPENING_RULE_UNSUPPORTED",
            world_id=world_id,
            location_id=place_id,
            source=source,
            timezone_name=effective_timezone,
            as_of=as_of,
            rule_kind="missing_close_hour",
        )
    elif close_hour < open_hour:
        return _opening_unavailable(
            "OPENING_RULE_UNSUPPORTED",
            world_id=world_id,
            location_id=place_id,
            source=source,
            timezone_name=effective_timezone,
            as_of=as_of,
            rule_kind="cross_midnight",
        )
    elif close_hour == open_hour:
        return _opening_unavailable(
            "NO_OPENING_WINDOW",
            world_id=world_id,
            location_id=place_id,
            source=source,
            timezone_name=effective_timezone,
            as_of=as_of,
            rule_kind="empty_window",
        )
    else:
        rule_kind = "hourly_window"

    fingerprint = _rule_fingerprint(
        world_id or "", place_id, effective_timezone, rule
    )
    horizon_end = as_of + timedelta(days=MAX_EXPECTATION_HORIZON_DAYS)
    timezone_source = (
        "caller_override" if effective_timezone != world_timezone else "world"
    )
    base: dict[str, Any] = {
        "kind": OPENING_KIND,
        "schema_version": OPENING_SCHEMA_VERSION,
        "status": "available",
        "world_id": world_id,
        "location_id": place_id,
        "source": source,
        "as_of": as_of.isoformat(timespec="seconds"),
        "as_of_utc": as_of.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "timezone": effective_timezone,
        "timezone_source": timezone_source,
        "world_timezone": world_timezone,
        "horizon_days": MAX_EXPECTATION_HORIZON_DAYS,
        "horizon_end": horizon_end.isoformat(timespec="seconds"),
        "rule_fingerprint": fingerprint,
        "rule_fingerprint_scope": _FINGERPRINT_SCOPE,
        "rule_fingerprint_purpose": "source_change_detection_only",
        "rule": dict(rule),
        "rule_kind": rule_kind,
        "requires_recheck": True,
        "action_grant": False,
        "note": _OPENING_NOTE,
    }

    if rule_kind == "always_open":
        base.update(
            {
                "window_state": "always_open",
                "always_open": True,
                "expected_open_at": base["as_of"],
                "window_open_at": None,
                "window_end": None,
            }
        )
        return base

    window = _hourly_window(as_of, rule, zone)
    if window is None:
        return _opening_unavailable(
            "NO_WINDOW_WITHIN_HORIZON",
            world_id=world_id,
            location_id=place_id,
            source=source,
            timezone_name=effective_timezone,
            as_of=as_of,
            rule_kind=rule_kind,
        )
    expected = window["expected_open_at"]
    window_end = window["window_end"]
    base.update(
        {
            "window_state": window["window_state"],
            "always_open": False,
            "expected_open_at": expected.isoformat(timespec="seconds"),
            "expected_open_at_utc": expected.astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "window_open_at": window["window_open_at"].isoformat(
                timespec="seconds"
            ),
            "window_end": window_end.isoformat(timespec="seconds"),
            "window_end_utc": window_end.astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ),
        }
    )
    return base


__all__ = [
    "DailyRhythmError",
    "FEATURE_ENV",
    "MAX_EXPECTATION_HORIZON_DAYS",
    "OPENING_KIND",
    "OPENING_SCHEMA_VERSION",
    "RULE_SOURCE_BUILTIN",
    "RULE_SOURCE_PACKAGE",
    "SCHEMA_VERSION",
    "WORLD_ID",
    "WORLD_TIMEZONE",
    "affordances_for_location",
    "all_location_availability",
    "daily_rhythm",
    "feature_enabled",
    "location_availability",
    "location_opening_expectation",
]
