#!/usr/bin/env python3
"""局部环境感知适配器（v0.4 第二阶段）。

回答一个问题：**AI 身处什么地方、周围有什么？**

数据边界（三层信息，明确区分）：

* ``static_world_data``——世界包里的全部静态事实（地点/区域/家具/规则），
  编辑与预览可见，但不等于角色当前感知；
* ``allowed_observation``——按感知边界**允许观察**的信息：当前建筑、
  功能区域、可见家具、可通行的相邻区域、当前有效的环境事实、可用活动；
  全部来自世界包与既有运行模块，不含想象内容；
* ``runtime_dependent``——需要运行时状态才能确定的信息（角色当前位置、
  持有物、已发生事件），本适配器只在提供隔离实例时读取真实
  ``world_state.json``，绝不推断。

本模块只读：不写世界状态，不创建动作，不冒充 World/Body 观察回执。
供编辑器「AI 视角预览」与测试使用；聊天侧的观察走
``world_living_skeleton.observe_world``（其 ``local_environment`` 块与本
适配器同源）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .loader import WorldPackage, load


class PerceptionError(ValueError):
    """感知请求无效。"""


def _read_world_state(instance_home: Path) -> Optional[dict[str, Any]]:
    state_path = instance_home / "data" / "world" / "world_state.json"
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def observe_local(
    package_dir: Path | str,
    *,
    place_id: str,
    area_id: Optional[str] = None,
    instance_home: Optional[Path | str] = None,
    now=None,
) -> dict[str, Any]:
    """生成一个位置的局部感知视图。

    * ``place_id`` 必须是世界包中的真实地点；
    * ``area_id`` 缺省时取导航入口区（无导航则地点默认区域）；
    * ``instance_home`` 提供时读取真实运行状态确定当前位置（runtime 层），
      否则 runtime 层标注为"需要运行时状态"。
    """
    package = load(package_dir)
    world = _world_data(package)
    location = package.location(place_id)
    if location is None:
        raise PerceptionError(f"地点不存在: {place_id!r}")
    nav = world.navigation_of(place_id) or {
        "areas": [{"area_id": location["area_id"], "name": "主要区域", "kind": "main"}],
        "connections": [],
        "entrance": location["area_id"],
        "furniture_map": {},
    }
    resolved_area = area_id or nav["entrance"]
    area_info = next((a for a in nav["areas"] if a["area_id"] == resolved_area), None)
    if area_info is None:
        raise PerceptionError(
            f"区域不存在: {place_id}/{resolved_area}（可用: {[a['area_id'] for a in nav['areas']]}）"
        )

    node = f"{place_id}/{resolved_area}" if resolved_area != nav["entrance"] else place_id
    furniture_ids = nav["furniture_map"].get(resolved_area, [])
    objects_by_id = {obj["object_id"]: obj for obj in package.objects}
    furniture_view = [
        {
            "object_id": object_id,
            "name": (objects_by_id.get(object_id) or {}).get("name", object_id),
            "kind": (objects_by_id.get(object_id) or {}).get("kind", ""),
            "note": (objects_by_id.get(object_id) or {}).get("note", ""),
            "editor_interactions": (objects_by_id.get(object_id) or {}).get("kind"),
        }
        for object_id in furniture_ids
    ]
    adjacent = []
    for node_neighbor in world.reachable_of(node):
        projection = world.location(node_neighbor) or {}
        adjacent.append({"node": node_neighbor, "name": projection.get("name", node_neighbor)})

    # 环境事实：复用既有环境事件模块（地点级、按当日小时过滤）。
    ambient_facts: list[dict[str, Any]] = []
    if now is not None:
        try:
            import world_ambient_events as wae

            ambient_facts = wae.active_ambient_events(
                now.astimezone().date(),
                now,
                timezone_name=package.timezone_name,
                allowed_location_ids={place_id},
                relevant_location_ids={place_id},
            )
        except Exception:
            ambient_facts = []

    allowed = {
        "building": {"location_id": place_id, "name": location["name"]},
        "area": dict(area_info),
        "furniture": furniture_view,
        "adjacent_areas": adjacent,
        "ambient_facts": [
            {"summary": item.get("payload", {}).get("summary", "")} for item in ambient_facts
        ],
        "opening_rule": _opening_rule(package, place_id),
        "note": "家具交互用途属于编辑器本地试运行能力；rc9 侧仅供观察，不冒充已执行的动作。",
    }
    static_world = {
        "locations": len(package.locations),
        "areas_in_building": [a["area_id"] for a in nav["areas"]],
        "area_connections": [
            f"{c['from']}<->{c['to']}" for c in nav["connections"]
        ],
        "furniture_total_in_building": sum(
            len(items) for items in nav["furniture_map"].values()
        ),
        "npcs_based_here": [
            npc["npc_id"] for npc in package.npcs if npc["home_base_area"] == place_id
        ],
    }
    runtime: dict[str, Any] = {
        "current_position": "需要运行时状态（world_state.json）",
        "held_objects": "需要运行时状态",
        "past_events": "需要运行时状态（执行账本）",
    }
    source = "static_only"
    if instance_home is not None:
        state = _read_world_state(Path(instance_home))
        if state is not None:
            runtime["current_position"] = state.get("location")
            runtime["world_revision"] = state.get("revision")
            source = "real_instance"
        else:
            runtime["current_position"] = "实例状态不可读"
            source = "instance_unreadable"

    return {
        "kind": "worldgen_local_perception",
        "node": node,
        "allowed_observation": allowed,
        "static_world_data": static_world,
        "runtime_dependent": runtime,
        "source": source,
        "note": "本结果由世界包与运行状态只读推导；不是 World/Body 的观察回执。",
    }


def _world_data(package: WorldPackage):
    """构造 WorldData（复用运行时解析器，保证导航语义一致）。"""
    import sys
    from pathlib import Path as _Path

    scripts_dir = _Path(__file__).resolve().parents[1] / "world" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import world_runtime_data as wrd

    return wrd.WorldData(
        world_id=package.world_id,
        timezone_name=package.timezone_name,
        display_name=package.display_name,
        locations={key: dict(value) for key, value in package.locations.items()},
        rules={
            loc_id: package.location_rule(loc_id)
            for loc_id in package.locations
            if package.location_rule(loc_id) is not None
        },
        npcs=tuple(package.npcs),
        ambient_templates=tuple(package.ambient_templates),
        walking_durations=dict(package.walking_durations),
        route_revision=package.route_revision,
        home_location_id=package.home_location_id,
        home_area_id=package.home_area_id,
        is_custom=True,
        package_dir=str(package.package_dir),
        navigation=(package.scene or {}).get("navigation"),
    )


def _opening_rule(package: WorldPackage, place_id: str) -> dict[str, Any]:
    rule = package.location_rule(place_id)
    if rule is None:
        return {"status": "unknown"}
    if rule["open_hour"] is None:
        return {"status": "always_open"}
    return {
        "status": "hours",
        "open_hour": rule["open_hour"],
        "close_hour": rule["close_hour"],
        "weekdays_only": rule["weekdays_only"],
    }


__all__ = ["PerceptionError", "observe_local"]
