#!/usr/bin/env python3
"""Deterministic, location-scoped ambient facts for the Shiomi World.

The daily set is materialized from the authoritative World local date. This
module deliberately has no store, Life authority, or background runner: an
observation can reconstruct the same bounded facts after a restart, while an
unobserved past day naturally expires.

有限跨天事件（finite）是世界包作者给出**: 固定身份、明确起止日期与阶段**的
环境事实：同一事件在相邻两天给出不同阶段的叙述，期限结束后自然消失，事后
（+8/+16 天、远未来）不会自动复活，也不会滚动生成新身份。阶段只复用既有
``local_date`` / ``valid_from`` / ``valid_until`` / ``payload.summary`` 字段，
不新增持久账本、不改对外事件 schema。

旧 ``worldgen.ambient.v1`` 包（五字段单日模板）与内建单日模板的行为不变；
v2 包由 ``components/worldgen/schema.py`` 显式分流，旧读端拒绝 v2、不自动降级。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import world_runtime_data as wrd


FEATURE_ENV = "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED"
WORLD_ID = "shiomi_city"
WORLD_TIMEZONE = "Asia/Tokyo"
SCHEMA_VERSION = "world.ambient.event.v1"
MAX_EVENTS_PER_DAY = 3
MAX_SUMMARY_LENGTH = 240

#: 模板目录的三个类别；只是目录元信息，不进入事件投影字段。
CATEGORY_DAILY = "daily"
CATEGORY_CHANGING = "changing"
CATEGORY_CROSS_DAY = "cross_day"
_TEMPLATE_CATEGORIES = frozenset(
    {CATEGORY_DAILY, CATEGORY_CHANGING, CATEGORY_CROSS_DAY}
)

KNOWN_LOCATION_IDS = frozenset(
    {"home", "nearby_street", "school", "convenience_store", "park", "home_goods_store"}
)
_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "world_id",
        "local_date",
        "location_id",
        "event_type",
        "payload",
        "valid_from",
        "valid_until",
        "provenance",
    }
)
_FORBIDDEN_FIELDS = frozenset(
    {
        "priority",
        "importance",
        "interest_score",
        "chiyo_should_act",
        "reward",
        "penalty",
        "quest",
        "goal",
        "emotion_effect",
    }
)


class AmbientWorldEventError(ValueError):
    """Raised when an ambient event contract is malformed."""


FINITE_PROVENANCE = "world_finite_event_v2"
#: 有限事件身份的字符边界（世界包作者指定，跨重启/重读必须稳定）。
_FINITE_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class _FinitePhase:
    """有限事件在某个明确世界日期上的一个阶段。"""

    local_date: date
    start_hour: int
    end_hour: int
    summary: str


@dataclass(frozen=True)
class _FiniteSpec:
    """作者指定的有限事件：固定身份 + 明确起止 + 阶段 + 结束原因。"""

    identity: str
    starts_on: date
    ends_on: date
    end_reason: str
    phases: tuple[_FinitePhase, ...]


@dataclass(frozen=True)
class _Template:
    event_type: str
    location_id: str
    summary: str
    start_hour: int
    end_hour: int
    category: str = CATEGORY_DAILY
    finite: Optional[_FiniteSpec] = None


#: 默认世界（汐见市）的有效模板目录：24 条，日常/会变/跨天各 8 条。
#:
#: * 日常：稳定的、不推进的环境小事；
#: * 会变：由既有 World 本地时间窗驱动的状态事实（不是真实动作结果）；
#: * 跨天：同一事件身份在周期内相邻两天给出不同阶段，周期结束即到期。
#: 不写具体价格、库存、气象权威、NPC 承诺或用户收到的消息；文案只是
#: 世界包环境叙述，不授予动作许可。
_TEMPLATES = (
    # -- 日常小事（8 条，含既有两条） -------------------------------------
    _Template("school_notice", "school", "今天公告栏贴了文化祭筹备通知", 0, 24),
    _Template("home_window_sound", "home", "窗外雨声变大", 0, 24),
    _Template(
        "convenience_store_morning_delivery",
        "convenience_store",
        "早班的送货车停在便利店后门",
        6,
        10,
    ),
    _Template("school_gate_sweeping", "school", "校门口有人在扫落叶", 7, 9),
    _Template(
        "home_goods_store_display",
        "home_goods_store",
        "家居店把新到的坐垫摆到门口",
        9,
        19,
    ),
    _Template("park_free_paper", "park", "长椅上放着一份被翻过的免费传单", 7, 19),
    _Template(
        "nearby_street_school_children",
        "nearby_street",
        "放学时段有几个学生从街口经过",
        15,
        17,
    ),
    _Template("home_morning_light", "home", "早晨的光从窗帘缝里照进来", 6, 9),
    # -- 会变（8 条，含既有三条；全部为时间窗事实） ------------------------
    _Template(
        "park_maintenance",
        "park",
        "草坪一部分正在维护",
        8,
        18,
        category=CATEGORY_CHANGING,
    ),
    _Template(
        "convenience_store_promotion",
        "convenience_store",
        "晚间饭团打折",
        18,
        22,
        category=CATEGORY_CHANGING,
    ),
    _Template(
        "street_construction",
        "nearby_street",
        "路边正在修排水管",
        6,
        20,
        category=CATEGORY_CHANGING,
    ),
    _Template(
        "school_afternoon_practice",
        "school",
        "操场上传来社团练习的声音",
        15,
        18,
        category=CATEGORY_CHANGING,
    ),
    _Template(
        "convenience_store_evening_queue",
        "convenience_store",
        "傍晚的便利店收银台前排着两三个人",
        17,
        20,
        category=CATEGORY_CHANGING,
    ),
    _Template(
        "park_sunset_shadows",
        "park",
        "太阳偏西，长椅的影子拉得很长",
        16,
        19,
        category=CATEGORY_CHANGING,
    ),
    _Template(
        "home_goods_store_closing",
        "home_goods_store",
        "家居店店员开始把门口的样品收回店里",
        17,
        19,
        category=CATEGORY_CHANGING,
    ),
    _Template(
        "nearby_street_evening_lights",
        "nearby_street",
        "街灯亮了，路面比白天暗",
        18,
        22,
        category=CATEGORY_CHANGING,
    ),
    # -- 有限跨天（8 条；固定世界日期 2026-10-24～2026-11-01，每天恰有两条
    #    处于阶段中；期限后消失，之后 +8/+16/远未来都不再复活） ----------------
    _Template(
        "street_drainage_notice",
        "nearby_street",
        "街口贴出排水管施工的预告",
        7,
        24,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="street_drainage_notice_20261024",
            starts_on=date(2026, 10, 24),
            ends_on=date(2026, 10, 25),
            end_reason="施工结束，围挡撤除",
            phases=(
                _FinitePhase(
                    date(2026, 10, 24), 7, 24, "街口贴出排水管施工的预告，写着明天开始"
                ),
                _FinitePhase(
                    date(2026, 10, 25), 6, 18, "排水管施工开始了，半条路面围了起来"
                ),
            ),
        ),
    ),
    _Template(
        "park_lawn_rest",
        "park",
        "草坪边立起轮休养护的牌子",
        8,
        20,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="park_lawn_rest_20261025",
            starts_on=date(2026, 10, 25),
            ends_on=date(2026, 10, 26),
            end_reason="轮休养护期满，围栏撤走",
            phases=(
                _FinitePhase(
                    date(2026, 10, 25), 8, 20, "公园草坪边立起牌子，说这周要轮休养护"
                ),
                _FinitePhase(
                    date(2026, 10, 26), 8, 18, "草坪的一半被围起来养护，另一半还能走"
                ),
            ),
        ),
    ),
    _Template(
        "school_culture_festival_prep",
        "school",
        "公告栏贴出文化祭筹备安排",
        9,
        17,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="school_culture_festival_prep_20261026",
            starts_on=date(2026, 10, 26),
            ends_on=date(2026, 10, 27),
            end_reason="筹备布置结束，场地恢复上课",
            phases=(
                _FinitePhase(
                    date(2026, 10, 26), 9, 17, "公告栏贴出文化祭筹备安排，周五开始布置"
                ),
                _FinitePhase(
                    date(2026, 10, 27), 9, 16, "几间教室开始搬桌椅，准备文化祭布置"
                ),
            ),
        ),
    ),
    _Template(
        "store_sidewalk_repair",
        "convenience_store",
        "便利店门前的人行道画了维修标记",
        8,
        20,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="store_sidewalk_repair_20261027",
            starts_on=date(2026, 10, 27),
            ends_on=date(2026, 10, 28),
            end_reason="人行道维修结束，窄道撤除",
            phases=(
                _FinitePhase(
                    date(2026, 10, 27), 8, 20, "便利店门前的人行道画了维修标记"
                ),
                _FinitePhase(
                    date(2026, 10, 28), 7, 17, "人行道维修开始，门口留出一条窄道"
                ),
            ),
        ),
    ),
    _Template(
        "home_goods_delivery_arrival",
        "home_goods_store",
        "家居店说新一批货明天到",
        10,
        19,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="home_goods_delivery_arrival_20261028",
            starts_on=date(2026, 10, 28),
            ends_on=date(2026, 10, 29),
            end_reason="新货上架完毕，纸箱清走",
            phases=(
                _FinitePhase(
                    date(2026, 10, 28), 10, 19, "家居店说新一批货明天到，今天先清出货架"
                ),
                _FinitePhase(
                    date(2026, 10, 29), 10, 18, "新货到了，店员在拆箱上架"
                ),
            ),
        ),
    ),
    _Template(
        "home_water_notice",
        "home",
        "楼下的告示说明天上午要检修水管",
        12,
        23,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="home_water_notice_20261029",
            starts_on=date(2026, 10, 29),
            ends_on=date(2026, 10, 30),
            end_reason="水管检修完成，供水恢复正常",
            phases=(
                _FinitePhase(
                    date(2026, 10, 29), 12, 23, "楼下的告示说明天上午要检修水管"
                ),
                _FinitePhase(
                    date(2026, 10, 30), 9, 15, "上午水管检修，水龙头的水流变细了"
                ),
            ),
        ),
    ),
    _Template(
        "nearby_street_leaf_cleanup",
        "nearby_street",
        "街道边堆着一袋袋落叶",
        8,
        18,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="nearby_street_leaf_cleanup_20261030",
            starts_on=date(2026, 10, 30),
            ends_on=date(2026, 10, 31),
            end_reason="落叶清运完成，街道恢复",
            phases=(
                _FinitePhase(
                    date(2026, 10, 30), 8, 18, "街道边堆着一袋袋落叶，还没人收走"
                ),
                _FinitePhase(
                    date(2026, 10, 31), 7, 16, "清运车来了，落叶袋被搬上车"
                ),
            ),
        ),
    ),
    _Template(
        "park_event_stage_setup",
        "park",
        "公园空地上开始搭周末活动用的小棚子",
        9,
        19,
        category=CATEGORY_CROSS_DAY,
        finite=_FiniteSpec(
            identity="park_event_stage_setup_20261031",
            starts_on=date(2026, 10, 31),
            ends_on=date(2026, 11, 1),
            end_reason="活动结束，棚子拆走",
            phases=(
                _FinitePhase(
                    date(2026, 10, 31), 9, 19, "公园空地上开始搭一个周末活动用的小棚子"
                ),
                _FinitePhase(
                    date(2026, 11, 1), 8, 20, "小棚子搭好了，有人在往里搬折叠椅"
                ),
            ),
        ),
    ),
)


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
        raise AmbientWorldEventError("ambient event is not bounded JSON") from exc


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def feature_enabled(environment: Optional[Mapping[str, str]] = None) -> bool:
    return _truthy((environment or os.environ).get(FEATURE_ENV))


def _require_date(value: Any) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise AmbientWorldEventError("local_date must be a date")
    return value


def _zone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise AmbientWorldEventError("timezone_name is invalid")
    try:
        return ZoneInfo(timezone_name.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AmbientWorldEventError("timezone_name is invalid") from exc


def _bounded_summary(value: Any) -> str:
    if not isinstance(value, str):
        raise AmbientWorldEventError("event summary must be text")
    result = value.strip()
    if not result or len(result) > MAX_SUMMARY_LENGTH:
        raise AmbientWorldEventError("event summary is outside its bound")
    return result


def _boundary(local_date: date, hour: int, zone: ZoneInfo) -> datetime:
    if hour == 24:
        local_date = local_date + timedelta(days=1)
        hour = 0
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise AmbientWorldEventError("event boundary is invalid")
    return datetime.combine(local_date, time(hour=hour), tzinfo=zone)


def _build_event(
    template: _Template,
    local_date: date,
    zone: ZoneInfo,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    valid_from = _boundary(local_date, template.start_hour, zone)
    valid_until = _boundary(local_date, template.end_hour, zone)
    event = {
        "event_id": f"ambient-{local_date.isoformat()}-{template.event_type}",
        "world_id": wrd.active_world(environment).world_id,
        "local_date": local_date.isoformat(),
        "location_id": template.location_id,
        "event_type": template.event_type,
        "payload": {"summary": template.summary},
        "valid_from": valid_from.isoformat(timespec="seconds"),
        "valid_until": valid_until.isoformat(timespec="seconds"),
        "provenance": "world_local_day_deterministic_v1",
    }
    return _validate_event(event, zone, environment)


def _finite_phase_for_day(
    template: _Template, day: date
) -> Optional[_FinitePhase]:
    """有限跨天模板在 ``day`` 的阶段；不在起止范围内或当天无阶段返回 None。"""

    spec = template.finite
    if spec is None:
        return None
    if day < spec.starts_on or day > spec.ends_on:
        return None
    for phase in spec.phases:
        if phase.local_date == day:
            return phase
    return None


def _build_finite_event(
    template: _Template,
    local_date: date,
    phase: _FinitePhase,
    zone: ZoneInfo,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """有限跨天阶段事件：身份由世界包作者固定，窗口与叙述用当天阶段。

    同一事件在起止范围内相邻日期生成同一个 ``event_id``；期限结束后不再生成，
    不会按观察日或进程启动日滚动出新身份。
    """

    spec = template.finite
    if spec is None:
        raise AmbientWorldEventError("finite event template is missing its spec")
    valid_from = _boundary(local_date, phase.start_hour, zone)
    valid_until = _boundary(local_date, phase.end_hour, zone)
    event = {
        "event_id": f"ambient-finite-{spec.identity}",
        "world_id": wrd.active_world(environment).world_id,
        "local_date": local_date.isoformat(),
        "location_id": template.location_id,
        "event_type": template.event_type,
        "payload": {"summary": phase.summary},
        "valid_from": valid_from.isoformat(timespec="seconds"),
        "valid_until": valid_until.isoformat(timespec="seconds"),
        "provenance": FINITE_PROVENANCE,
    }
    return _validate_event(event, zone, environment)


def _validate_event(
    event: Mapping[str, Any],
    zone: ZoneInfo,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    world = wrd.active_world(environment)
    if not isinstance(event, Mapping) or set(event) != _EVENT_FIELDS:
        raise AmbientWorldEventError("ambient event has an invalid schema")
    if not isinstance(event["event_id"], str) or not event["event_id"].strip():
        raise AmbientWorldEventError("event_id is invalid")
    if event["world_id"] != world.world_id:
        raise AmbientWorldEventError("ambient event world mismatch")
    try:
        parsed_date = date.fromisoformat(event["local_date"])
    except (TypeError, ValueError) as exc:
        raise AmbientWorldEventError("local_date is invalid") from exc
    if event["location_id"] not in world.known_location_ids:
        raise AmbientWorldEventError("ambient event location is invalid")
    if not isinstance(event["event_type"], str) or not event["event_type"].strip():
        raise AmbientWorldEventError("event_type is invalid")
    payload = event["payload"]
    if not isinstance(payload, Mapping) or set(payload) != {"summary"}:
        raise AmbientWorldEventError("ambient payload is not bounded factual data")
    _bounded_summary(payload["summary"])
    if not isinstance(event["provenance"], str) or not event["provenance"].strip():
        raise AmbientWorldEventError("provenance is invalid")
    try:
        valid_from = datetime.fromisoformat(event["valid_from"])
        valid_until = datetime.fromisoformat(event["valid_until"])
    except (TypeError, ValueError) as exc:
        raise AmbientWorldEventError("event validity window is invalid") from exc
    if (
        valid_from.tzinfo is None
        or valid_from.utcoffset() is None
        or valid_until.tzinfo is None
        or valid_until.utcoffset() is None
        or valid_from >= valid_until
        or valid_from.astimezone(zone).date() != parsed_date
        or valid_until.astimezone(zone).date()
        not in {parsed_date, parsed_date + timedelta(days=1)}
    ):
        raise AmbientWorldEventError("event validity window is inconsistent")
    if _FORBIDDEN_FIELDS.intersection(event) or _FORBIDDEN_FIELDS.intersection(payload):
        raise AmbientWorldEventError("ambient event contains a forbidden authority field")
    return _copy_json(dict(event))


def _finite_from_mapping(raw: Any) -> _FiniteSpec:
    if not isinstance(raw, Mapping) or set(raw) != {
        "identity",
        "starts_on",
        "ends_on",
        "end_reason",
        "phases",
    }:
        raise AmbientWorldEventError("finite event spec has an invalid schema")
    identity = raw["identity"]
    if not isinstance(identity, str) or not _FINITE_IDENTITY_RE.fullmatch(identity):
        raise AmbientWorldEventError("finite event identity is invalid")
    try:
        starts_on = date.fromisoformat(raw["starts_on"])
        ends_on = date.fromisoformat(raw["ends_on"])
    except (TypeError, ValueError) as exc:
        raise AmbientWorldEventError("finite event date range is invalid") from exc
    if isinstance(raw["starts_on"], datetime) or isinstance(raw["ends_on"], datetime):
        raise AmbientWorldEventError("finite event date range is invalid")
    if starts_on > ends_on:
        raise AmbientWorldEventError("finite event date range is reversed")
    end_reason = raw["end_reason"]
    if not isinstance(end_reason, str):
        raise AmbientWorldEventError("finite event end_reason is invalid")
    end_reason = end_reason.strip()
    if not end_reason or len(end_reason) > 120:
        raise AmbientWorldEventError("finite event end_reason is outside its bound")
    phases_raw = raw["phases"]
    if not isinstance(phases_raw, (list, tuple)) or not phases_raw:
        raise AmbientWorldEventError("finite event phases are required")
    phases: list[_FinitePhase] = []
    previous: Optional[date] = None
    for item in phases_raw:
        if not isinstance(item, Mapping) or set(item) != {
            "local_date",
            "start_hour",
            "end_hour",
            "summary",
        }:
            raise AmbientWorldEventError("finite event phase has an invalid schema")
        try:
            local_date = date.fromisoformat(item["local_date"])
        except (TypeError, ValueError) as exc:
            raise AmbientWorldEventError("finite event phase date is invalid") from exc
        if isinstance(item["local_date"], datetime) or not (
            starts_on <= local_date <= ends_on
        ):
            raise AmbientWorldEventError("finite event phase date is outside its range")
        if previous is not None and local_date <= previous:
            raise AmbientWorldEventError("finite event phases are not strictly ordered")
        previous = local_date
        start_hour = item["start_hour"]
        end_hour = item["end_hour"]
        if not isinstance(start_hour, int) or not 0 <= start_hour <= 24:
            raise AmbientWorldEventError("finite event phase start_hour is invalid")
        if not isinstance(end_hour, int) or not 0 <= end_hour <= 24:
            raise AmbientWorldEventError("finite event phase end_hour is invalid")
        if start_hour == end_hour:
            raise AmbientWorldEventError("finite event phase window is empty")
        phases.append(
            _FinitePhase(
                local_date=local_date,
                start_hour=start_hour,
                end_hour=end_hour,
                summary=_bounded_summary(item["summary"]),
            )
        )
    return _FiniteSpec(
        identity=identity,
        starts_on=starts_on,
        ends_on=ends_on,
        end_reason=end_reason,
        phases=tuple(phases),
    )


def _template_from_mapping(item: Mapping[str, Any]) -> _Template:
    if not isinstance(item, Mapping):
        raise AmbientWorldEventError("ambient template is invalid")
    category = item.get("category", CATEGORY_DAILY)
    if category not in _TEMPLATE_CATEGORIES:
        raise AmbientWorldEventError("ambient template category is invalid")
    kind = item.get("kind")
    finite_raw = item.get("finite")
    finite: Optional[_FiniteSpec] = None
    if kind is None:
        # v1 包（五字段）与内建单日模板：没有显式类型，也不允许跨天字段，
        # 不把普通模板隐式解释成有限事件。
        if finite_raw is not None:
            raise AmbientWorldEventError("ambient finite spec requires an explicit kind")
        if category == CATEGORY_CROSS_DAY:
            raise AmbientWorldEventError("cross_day ambient template requires a finite spec")
    elif kind == "single_day":
        if finite_raw is not None:
            raise AmbientWorldEventError("single_day ambient template cannot carry a finite spec")
        if category == CATEGORY_CROSS_DAY:
            raise AmbientWorldEventError("single_day ambient template cannot be cross_day")
    elif kind == "finite":
        if item.get("category", CATEGORY_CROSS_DAY) != CATEGORY_CROSS_DAY:
            raise AmbientWorldEventError("finite ambient template requires cross_day category")
        category = CATEGORY_CROSS_DAY
        finite = _finite_from_mapping(finite_raw)
    else:
        raise AmbientWorldEventError("ambient template kind is invalid")
    start_hour = item.get("start_hour")
    end_hour = item.get("end_hour")
    if finite is not None:
        # v2 包只声明阶段窗口；目录默认窗口取第一阶段（内建映射可显式覆盖）。
        start_hour = finite.phases[0].start_hour if start_hour is None else start_hour
        end_hour = finite.phases[0].end_hour if end_hour is None else end_hour
    elif start_hour is None or end_hour is None:
        raise AmbientWorldEventError("ambient template window is missing")
    return _Template(
        event_type=item["event_type"],
        location_id=item["location_id"],
        summary=item["summary"],
        start_hour=start_hour,
        end_hour=end_hour,
        category=category,
        finite=finite,
    )


def _active_templates(
    environment: Optional[Mapping[str, str]] = None,
) -> tuple[_Template, ...]:
    """活动世界的环境事件模板（未配置世界包时即汐见市 _TEMPLATES）。"""
    world = wrd.active_world(environment)
    return tuple(_template_from_mapping(item) for item in world.ambient_templates)


def _select_events(
    day: date,
    templates: tuple[_Template, ...],
    zone: ZoneInfo,
    environment: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """按既有每日上限选事件：跨天阶段优先占位，其余给单日轮转。"""

    cross_events: list[dict[str, Any]] = []
    daily_templates: list[_Template] = []
    for template in templates:
        if template.finite is None:
            daily_templates.append(template)
            continue
        phase = _finite_phase_for_day(template, day)
        if phase is None:
            continue
        cross_events.append(
            _build_finite_event(template, day, phase, zone, environment)
        )
    remaining = MAX_EVENTS_PER_DAY - len(cross_events)
    selected: list[_Template] = []
    if remaining > 0 and daily_templates:
        start = day.toordinal() % len(daily_templates)
        selected = [
            daily_templates[(start + offset) % len(daily_templates)]
            for offset in range(min(remaining, len(daily_templates)))
        ]
    events = cross_events + [
        _build_event(template, day, zone, environment) for template in selected
    ]
    return events[:MAX_EVENTS_PER_DAY]


def materialize_ambient_events(
    local_date: date,
    *,
    timezone_name: str = WORLD_TIMEZONE,
    allowed_location_ids: Optional[Iterable[str]] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """Derive the stable bounded event set for one World-local calendar day."""

    day = _require_date(local_date)
    zone = _zone(timezone_name)
    templates = _active_templates(environment)
    known = wrd.active_world(environment).known_location_ids
    allowed = known if allowed_location_ids is None else set(allowed_location_ids)
    if not known.issubset(allowed):
        raise AmbientWorldEventError("ambient template location catalog is incomplete")
    events = _select_events(day, templates, zone, environment)
    if len(events) > MAX_EVENTS_PER_DAY:
        raise AmbientWorldEventError("daily ambient event bound exceeded")
    return _copy_json(events)


def active_ambient_events(
    local_date: date,
    now: datetime,
    *,
    timezone_name: str = WORLD_TIMEZONE,
    allowed_location_ids: Optional[Iterable[str]] = None,
    relevant_location_ids: Optional[Iterable[str]] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """Return only current, location-relevant facts for one local day."""

    day = _require_date(local_date)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise AmbientWorldEventError("now must be timezone-aware")
    zone = _zone(timezone_name)
    local_now = now.astimezone(zone)
    if local_now.date() != day:
        return []
    known = wrd.active_world(environment).known_location_ids
    relevant = None
    if relevant_location_ids is not None:
        if isinstance(relevant_location_ids, (str, bytes)):
            raise AmbientWorldEventError("relevant_location_ids must be an iterable")
        relevant = set(relevant_location_ids)
        if not relevant.issubset(known):
            raise AmbientWorldEventError("relevant location is invalid")
    result: list[dict[str, Any]] = []
    for event in materialize_ambient_events(
        day,
        timezone_name=timezone_name,
        allowed_location_ids=allowed_location_ids,
        environment=environment,
    ):
        if relevant is not None and event["location_id"] not in relevant:
            continue
        valid_from = datetime.fromisoformat(event["valid_from"])
        valid_until = datetime.fromisoformat(event["valid_until"])
        if valid_from <= local_now < valid_until:
            result.append(event)
    return _copy_json(result)


def ambient_template_catalog(
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """活动世界的有效模板目录（元信息，不参与事件投影字段）。

    这是给测试与工程目录使用的只读视图：条目数、三类计数、有限跨天事件
    的固定身份/起止/阶段/结束原因。它不是事件实例，不产生候选，也不授予
    动作许可。
    """

    world = wrd.active_world(environment)
    templates = _active_templates(environment)
    counts = {CATEGORY_DAILY: 0, CATEGORY_CHANGING: 0, CATEGORY_CROSS_DAY: 0}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for template in templates:
        if template.event_type in seen:
            raise AmbientWorldEventError("ambient template event_type is duplicated")
        seen.add(template.event_type)
        counts[template.category] += 1
        finite = template.finite
        rows.append(
            {
                "event_type": template.event_type,
                "category": template.category,
                "location_id": template.location_id,
                "summary": template.summary,
                "start_hour": template.start_hour,
                "end_hour": template.end_hour,
                "cross_day": finite is not None,
                "finite": finite is not None,
                "identity": finite.identity if finite else None,
                "starts_on": finite.starts_on.isoformat() if finite else None,
                "ends_on": finite.ends_on.isoformat() if finite else None,
                "end_reason": finite.end_reason if finite else None,
                "phases": [
                    {
                        "local_date": phase.local_date.isoformat(),
                        "start_hour": phase.start_hour,
                        "end_hour": phase.end_hour,
                        "summary": phase.summary,
                    }
                    for phase in (finite.phases if finite else ())
                ],
            }
        )
    return {
        "world_id": world.world_id,
        "total": len(templates),
        "unique_total": len(seen),
        "categories": counts,
        "cross_day_supported": counts[CATEGORY_CROSS_DAY] > 0,
        "max_events_per_day": MAX_EVENTS_PER_DAY,
        "templates": rows,
    }


__all__ = [
    "AmbientWorldEventError",
    "CATEGORY_CHANGING",
    "CATEGORY_CROSS_DAY",
    "CATEGORY_DAILY",
    "FEATURE_ENV",
    "FINITE_PROVENANCE",
    "KNOWN_LOCATION_IDS",
    "MAX_EVENTS_PER_DAY",
    "SCHEMA_VERSION",
    "WORLD_ID",
    "WORLD_TIMEZONE",
    "active_ambient_events",
    "ambient_template_catalog",
    "feature_enabled",
    "materialize_ambient_events",
]
