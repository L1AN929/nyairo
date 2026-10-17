#!/usr/bin/env python3
"""只读的“此刻这一带有什么机会”投影（World owner 侧）。

四件事，全部来自既有 owner，不新增第二套事实：

1. **可达性**来自世界包拓扑（``world_runtime_data`` 的走道图与 ``route_to``）；
2. **可用性/可做事项**来自 ``world_daily_rhythm``（OPEN/CLOSED/reason 与闭店
   时 ``affordances`` 为空），不在这里自造营业判断；
3. **可见物件**只对“此刻真的在这里”的地点投影，来自世界包场景声明的区域与
   摆位；别处的店内陈设不可见，只有公开时间表这一层事实。
4. **有限未来营业预期**（``opening_expectation``）同样由 ``world_daily_rhythm``
   的 ``location_opening_expectation`` 给出（同一套规则、同样的“现在开没开”语义），
   只是“下次预计能进去的窗口”，带 ``requires_recheck`` 且 ``action_grant: false``；
   它不改本模块的 ``validity`` 语义（仍是 ``NOW_ONLY`` / ``future: not_promised``），
   也不是营业保证。

本模块不写任何状态：没有世界状态写入、没有 candidate、没有模型调用。投影
**不是动作授权**（每条都带 ``action_grant: false``），也不承诺未来营业——
``validity`` 只对本次 ``now`` 成立。远处地点营业只是“按公开时间表营业”，
不代表亲眼看到店内。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import world_daily_rhythm as wdr
import world_foundation as wf
import world_runtime_data as wrd

KIND = "world_daily_opportunities"
COMPACT_KIND = "world.daily.opportunities.compact.v1"
SCHEMA_VERSION = "world.daily.opportunities.v1"

SCHEDULE_SOURCE_PACKAGE = "world_package_schedule"
SCHEDULE_SOURCE_BUILTIN = "world_daily_rhythm_builtin"

#: 一个投影里最多列出的地点（远于上限的地点不列，不假装无限可达）。
MAX_OPPORTUNITIES = 12
#: 单程步行上限（秒）：30 分钟的街区范围之外不算“此刻的机会”。
MAX_WALK_SECONDS = 1800
#: 当前地点最多投影的可见物件数量（与骨架局部感知一致的关注预算）。
MAX_VISIBLE_FURNITURE = 8

_NOTE = (
    "affordances 是营业时间表事实，不是动作授权；observed 只覆盖当前地点；"
    "validity 只对本次 now 成立，未来营业不承诺。"
)


class DailyOpportunityError(ValueError):
    """投影输入不合法（不是世界数据错误）。"""


def _instant(now: Optional[datetime]) -> datetime:
    instant = datetime.now(timezone.utc) if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise DailyOpportunityError("now must be a timezone-aware datetime")
    return instant


def _schedule_source(world: wrd.WorldData) -> str:
    return SCHEDULE_SOURCE_PACKAGE if world.is_custom else SCHEDULE_SOURCE_BUILTIN


def _unavailable(reason_code: str, *, world_id: Optional[str] = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "status": "unavailable",
        "reason_code": reason_code,
        "opportunities": [],
        "action_grant": False,
        "note": _NOTE,
    }
    if world_id is not None:
        result["world_id"] = world_id
    return result


def _reachability(
    world: wrd.WorldData, origin: str, target: str
) -> Optional[dict[str, Any]]:
    """origin → target 的真实走道事实；不可达返回 None。"""
    if target == origin:
        return {
            "status": "here",
            "route": [],
            "walk_seconds": 0,
            "source": "world_state",
        }
    route = world.route_to(origin, target)
    if route is None:
        return None
    total = 0
    previous = origin
    for step in route:
        seconds = world.route_duration_seconds(previous, step)
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            return None
        total += seconds
        previous = step
    if total > MAX_WALK_SECONDS:
        return None
    return {
        "status": "reachable",
        "route": list(route),
        "walk_seconds": total,
        "source": "world_topology",
    }


def _availability(
    world: wrd.WorldData,
    location_id: str,
    local: datetime,
    environment: Optional[Mapping[str, str]],
    source: str,
) -> dict[str, Any]:
    try:
        raw = wdr.location_availability(
            location_id,
            local,
            timezone_name=world.timezone_name,
            environment=environment,
        )
    except Exception:
        return {
            "location_id": location_id,
            "status": "UNKNOWN",
            "reason_code": "DAILY_RHYTHM_UNAVAILABLE",
            "source": source,
        }
    return {
        "location_id": raw["location_id"],
        "status": raw["status"],
        "reason_code": raw["reason_code"],
        "source": source,
    }


def _opening_expectation(
    world: wrd.WorldData,
    location_id: str,
    local: datetime,
    environment: Optional[Mapping[str, str]],
    source: str,
) -> dict[str, Any]:
    """该地点的有限未来营业预期（同一 rhythm owner 算，投影不自己算）。

    失败时只给一个 ``unavailable`` 骨架，不升级成“可能开着”：预期本来就不是
    营业保证，也不是动作授权。
    """
    try:
        return wdr.location_opening_expectation(
            location_id,
            local,
            timezone_name=world.timezone_name,
            environment=environment,
        )
    except Exception:
        return {
            "kind": wdr.OPENING_KIND,
            "schema_version": wdr.OPENING_SCHEMA_VERSION,
            "status": "unavailable",
            "reason_code": "OPENING_EXPECTATION_UNAVAILABLE",
            "world_id": world.world_id,
            "location_id": location_id,
            "source": source,
            "horizon_days": wdr.MAX_EXPECTATION_HORIZON_DAYS,
            "expected_open_at": None,
            "window_open_at": None,
            "window_end": None,
            "always_open": False,
            "window_state": None,
            "requires_recheck": True,
            "action_grant": False,
        }


def _affordances(
    world: wrd.WorldData,
    location_id: str,
    local: datetime,
    availability: Mapping[str, Any],
    environment: Optional[Mapping[str, str]],
    source: str,
) -> list[dict[str, Any]]:
    """营业时才给出可做事项；闭店/未知一律空列表（不冒充店内能做）。"""
    if availability.get("status") != "OPEN":
        return []
    try:
        items = wdr.affordances_for_location(
            location_id,
            local,
            timezone_name=world.timezone_name,
            environment=environment,
        )
    except Exception:
        return []
    return [
        {
            "affordance_id": item["affordance_id"],
            "description": item["description"],
            "source": source,
            "action_grant": False,
        }
        for item in items
    ]


def _observed(
    world: wrd.WorldData,
    scene_snapshot: Optional[Mapping[str, Any]],
    place_id: str,
    area_id: str,
    current_place: str,
) -> dict[str, Any]:
    """可见物件只对当前位置有效；别处不假装看过店内。"""
    if place_id != current_place:
        return {
            "status": "not_observed",
            "reason_code": "OUT_OF_VIEW",
            "source": "world_state",
        }
    nav = world.navigation_of(place_id)
    if nav is None:
        return {
            "status": "visible_here",
            "area_id": area_id,
            "furniture": [],
            "scene_declared": False,
            "source": "world_package_scene",
        }
    placements = (scene_snapshot or {}).get("placements") or {}
    names = (scene_snapshot or {}).get("object_names") or {}
    notes = (scene_snapshot or {}).get("object_notes") or {}
    furniture: list[dict[str, Any]] = []
    for object_id in world.furniture_in_area(place_id, area_id)[:MAX_VISIBLE_FURNITURE]:
        placement = placements.get(object_id)
        furniture.append(
            {
                "object_id": object_id,
                "name": names.get(object_id, object_id),
                "kind": (placement or {}).get("kind"),
                "note": notes.get(object_id, ""),
            }
        )
    return {
        "status": "visible_here",
        "area_id": area_id,
        "furniture": furniture,
        "scene_declared": True,
        "source": "world_package_scene",
    }


def _scene_snapshot(environment: Optional[Mapping[str, str]]) -> Optional[dict[str, Any]]:
    try:
        import world_living_skeleton as wls

        return wls._active_scene_snapshot(environment)  # 同一份包场景快照，不另建缓存
    except Exception:
        return None


def location_opportunity_catalog(
    environment: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    *,
    hermes_home: Optional[Path | str] = None,
) -> dict[str, Any]:
    """活动世界在当前 ``now`` 的可达地点机会目录（只读，不授权动作）。

    返回的 ``opportunities`` 只包含“从世界状态记录的位置出发、走道图能走到、
    单程不超过 ``MAX_WALK_SECONDS``”的地点；当前位置本身以 ``status: "here"``
    列出。世界包不可读、世界未初始化或状态里的位置非法时返回 ``unavailable``
    并给出原因，不编造可用机会。
   每条机会另带 ``opening_expectation``：由 ``world_daily_rhythm.location_opening_expectation``
   按同一份营业规则算出的有限未来窗口（只预计、不保证、不授权；``status`` 可能是
   ``unavailable`` 并给 ``reason_code``）。目录顶层 ``validity`` 仍是 ``NOW_ONLY``。
    """
    try:
        world = wrd.active_world(environment)
    except wrd.WorldDataError:
        return _unavailable("WORLD_PACKAGE_UNREADABLE")
    except Exception:
        return _unavailable("WORLD_DATA_UNAVAILABLE")

    try:
        instant = _instant(now)
    except DailyOpportunityError:
        raise

    import world_living_skeleton as wls

    state = wls.read_world_state(hermes_home=hermes_home, environment=environment)
    if state is None:
        return _unavailable("WORLD_NOT_INITIALIZED", world_id=world.world_id)
    location_state = state["location"]
    origin = world.state_node(location_state["place_id"], location_state.get("area_id"))
    if origin is None:
        return _unavailable("WORLD_LOCATION_INVALID", world_id=world.world_id)
    local = wf.world_local_time(state, instant)

    source = _schedule_source(world)
    scene_snapshot = _scene_snapshot(environment)
    current_place = world.node_place(origin)
    _, current_area = world.split_node(origin)

    opportunities: list[dict[str, Any]] = []
    for place_id in sorted(world.locations):
        reachability = _reachability(world, origin, place_id)
        if reachability is None:
            continue
        availability = _availability(world, place_id, local, environment, source)
        location = world.location(place_id) or {}
        nav = world.navigation_of(place_id)
        opportunities.append(
            {
                "location_id": place_id,
                "name": location.get("name", place_id),
                "kind": location.get("kind", ""),
                "here": place_id == current_place,
                "reachability": reachability,
                "availability": availability,
                "opening_expectation": _opening_expectation(
                    world, place_id, local, environment, source
                ),
                "affordances": _affordances(
                    world, place_id, local, availability, environment, source
                ),
                "observed": _observed(
                    world, scene_snapshot, place_id, current_area, current_place
                ),
                "indoor_areas": [
                    {"area_id": area["area_id"], "name": area["name"], "kind": area["kind"]}
                    for area in (nav["areas"] if nav else [])
                ],
                "time": {
                    "local_time": local.isoformat(timespec="seconds"),
                    "local_date": local.date().isoformat(),
                    "weekday": local.isoweekday(),
                },
                "validity": {
                    "as_of": local.isoformat(timespec="seconds"),
                    "scope": "NOW_ONLY",
                    "future": "not_promised",
                },
                "action_grant": False,
            }
        )
    opportunities.sort(
        key=lambda item: (
            0 if item["here"] else 1,
            item["reachability"]["walk_seconds"],
            item["location_id"],
        )
    )
    return {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "status": "available",
        "world_id": world.world_id,
        "timezone": world.timezone_name,
        "local_time": local.isoformat(timespec="seconds"),
        "local_date": local.date().isoformat(),
        "weekday": local.isoweekday(),
        "current_location_id": origin,
        "current_place_id": current_place,
        "home_location_id": world.home_location_id,
        "schedule_source": source,
        "observed_scope": "current_location_only",
        "validity": {
            "as_of": local.isoformat(timespec="seconds"),
            "scope": "NOW_ONLY",
            "future": "not_promised",
        },
        "opportunities": opportunities[:MAX_OPPORTUNITIES],
        "truncated": len(opportunities) > MAX_OPPORTUNITIES,
        "action_grant": False,
        "note": _NOTE,
    }


def compact_projection(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """``observe_world`` 用的短视图：只留选择与接线需要的事实与来源。

    ``opening_expectation`` **故意不进短视图**：``observe_world`` 有 16KB 预算，
    超限会整块省略 ``reachable_opportunities``；把每个地点的未来窗口塞进来会挤掉
    当前事实。要未来预期就调 ``world_daily_rhythm.location_opening_expectation``
    或全量目录的 ``opening_expectation`` 字段。
    """
    if not isinstance(catalog, Mapping):
        raise DailyOpportunityError("catalog must be a mapping")
    if catalog.get("status") != "available":
        return {
            "kind": COMPACT_KIND,
            "status": "unavailable",
            "reason_code": catalog.get("reason_code", "WORLD_OPPORTUNITIES_UNAVAILABLE"),
            "source": "world_daily_opportunities",
        }
    items = []
    for item in catalog.get("opportunities", []):
        items.append(
            {
                "location_id": item["location_id"],
                "name": item["name"],
                "kind": item["kind"],
                "here": item["here"],
                "walk_seconds": item["reachability"]["walk_seconds"],
                "reach": item["reachability"]["status"],
                "availability": item["availability"]["status"],
                "availability_reason": item["availability"]["reason_code"],
                "affordance_ids": [
                    affordance["affordance_id"] for affordance in item["affordances"]
                ],
                "observed": item["observed"]["status"],
                "action_grant": False,
            }
        )
    return {
        "kind": COMPACT_KIND,
        "status": "available",
        "as_of": catalog["validity"]["as_of"],
        "scope": catalog["validity"]["scope"],
        "future": catalog["validity"]["future"],
        "schedule_source": catalog["schedule_source"],
        "current_location_id": catalog["current_location_id"],
        "opportunities": items,
        "note": _NOTE,
    }


__all__ = [
    "COMPACT_KIND",
    "DailyOpportunityError",
    "KIND",
    "MAX_OPPORTUNITIES",
    "MAX_WALK_SECONDS",
    "SCHEMA_VERSION",
    "compact_projection",
    "location_opportunity_catalog",
]
