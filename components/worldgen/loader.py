#!/usr/bin/env python3
"""世界包加载器：把世界包解析成运行时可用的只读数据视图。

这是 worldgen 与 rc9 World Runtime 之间的数据边界：
``components/world/scripts/world_runtime_data`` 用本模块把
``CHIYO_WORLD_PACKAGE`` 指向的世界包变成五个世界模块的数据源。
加载结果是不可变的快照；修改世界包文件会使缓存失效并重新加载。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional

from . import schema

#: 生成世界的路线权威版本号（进入 travel 记录，必须稳定）。
ROUTE_REVISION = "worldgen-walk-v1"

#: 生成世界的住宅/卧室规范 ID（与 rc9 既有世界模块约定一致）。
HOME_LOCATION_ID = "home"
HOME_AREA_ID = "bedroom"


class WorldPackage:
    """一个已加载、已校验的世界包的只读视图。"""

    def __init__(self, package_dir: Path | str, docs: dict[str, Any]):
        self.package_dir = Path(package_dir)
        self._world = docs["world.json"]
        self._topology = docs["topology.json"]
        self._rules = docs["rules.json"]
        self._npcs = docs["npcs.json"]
        self._ambient = docs["ambient.json"]
        self._objects = docs["objects.json"]
        self._content = docs["content.json"]
        self._scene = docs.get(schema.SCENE_FILE)
        self._locations: dict[str, dict[str, Any]] = {
            loc["location_id"]: loc for loc in self._topology["locations"]
        }
        self._walking_durations: dict[tuple[str, str], int] = {}
        for edge in self._topology["edges"]:
            self._walking_durations[(edge["from"], edge["to"])] = edge["walk_seconds"]
            self._walking_durations[(edge["to"], edge["from"])] = edge["walk_seconds"]

    # ---- 世界信息 ---------------------------------------------------------

    @property
    def world_id(self) -> str:
        return self._world["world_id"]

    @property
    def display_name(self) -> str:
        return self._world["name"]

    @property
    def theme(self) -> str:
        return self._world["theme"]

    @property
    def seed(self) -> int:
        return self._world["seed"]

    @property
    def timezone_name(self) -> str:
        return self._world["timezone"]

    @property
    def generator_version(self) -> str:
        return self._world["generator_version"]

    @property
    def route_revision(self) -> str:
        return ROUTE_REVISION

    # ---- 空间 ------------------------------------------------------------

    @property
    def locations(self) -> dict[str, dict[str, Any]]:
        """骨架形状的地点字典（location_id/name/kind/area_id/description/reachable）。"""
        return self._locations

    def location(self, location_id: str) -> Optional[dict[str, Any]]:
        return self._locations.get(location_id)

    def area_id_of(self, location_id: str) -> str:
        location = self._locations.get(location_id)
        if location is None:
            return location_id
        return location["area_id"]

    def location_catalog(self) -> list[dict[str, Any]]:
        return [dict(self._locations[key]) for key in sorted(self._locations)]

    @property
    def home_location_id(self) -> str:
        return HOME_LOCATION_ID

    @property
    def home_area_id(self) -> str:
        return HOME_AREA_ID

    @property
    def walking_durations(self) -> dict[tuple[str, str], int]:
        """与 world_travel_delivery.WALKING_DURATIONS 相同形状的路线权威。"""
        return self._walking_durations

    def route_duration_seconds(self, origin: str, destination: str) -> Optional[int]:
        return self._walking_durations.get((origin, destination))

    # ---- 规则 ------------------------------------------------------------

    def location_rule(self, location_id: str) -> Optional[dict[str, Any]]:
        rule = self._rules["location_rules"].get(location_id)
        if rule is None:
            return None
        return {
            "open_hour": rule["open_hour"],
            "close_hour": rule["close_hour"],
            "weekdays_only": rule["weekdays_only"],
            "affordances": tuple(
                (item["affordance_id"], item["description"]) for item in rule["affordances"]
            ),
        }

    @property
    def weather_candidates(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._rules["weather_candidates"]]

    # ---- 公共角色 ----------------------------------------------------------

    @property
    def npcs(self) -> list[dict[str, Any]]:
        return self._npcs["npcs"]

    # ---- 环境事件 ----------------------------------------------------------

    @property
    def ambient_templates(self) -> list[dict[str, Any]]:
        return self._ambient["templates"]

    # ---- 内容 ------------------------------------------------------------

    def content_entry(self, key: str) -> dict[str, Any]:
        return dict(self._content["entries"].get(key, {}))

    @property
    def content_source(self) -> str:
        return self._content["source"]

    # ---- 对象 ------------------------------------------------------------

    @property
    def objects(self) -> list[dict[str, Any]]:
        return self._objects["objects"]

    # ---- 场景（可选；编辑器回写产物） --------------------------------------

    @property
    def scene(self) -> Optional[dict[str, Any]]:
        """编辑器场景数据（室内空间/物件摆位/素材）；纯程序生成的包为 None。"""
        return self._scene

    def snapshot(self) -> dict[str, Any]:
        """七份核心文档的深拷贝（回写合并的基础）。"""
        return {
            "world.json": copy.deepcopy(self._world),
            "topology.json": copy.deepcopy(self._topology),
            "rules.json": copy.deepcopy(self._rules),
            "npcs.json": copy.deepcopy(self._npcs),
            "ambient.json": copy.deepcopy(self._ambient),
            "objects.json": copy.deepcopy(self._objects),
            "content.json": copy.deepcopy(self._content),
        }

    # ---- 摘要 ------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        return {
            "package_dir": str(self.package_dir),
            "world_id": self.world_id,
            "name": self.display_name,
            "theme": self.theme,
            "seed": self.seed,
            "timezone": self.timezone_name,
            "locations": len(self._locations),
            "edges": len(self._topology["edges"]),
            "npcs": len(self.npcs),
            "content_source": self.content_source,
        }


def load(package_dir: Path | str, *, verify: bool = True) -> WorldPackage:
    """加载并校验世界包；格式或结构问题直接抛 WorldPackageError。"""
    resolved = Path(package_dir).resolve()
    docs = schema.load_package(resolved, verify=verify)
    from .validator import validate

    messages = validate(docs)
    if messages:
        raise schema.WorldPackageError(
            "世界包结构校验失败：\n- " + "\n- ".join(messages)
        )
    return WorldPackage(resolved, docs)


def load_if_compatible(
    package_dir: Path | str, *, expected_world_id: Optional[str] = None
) -> WorldPackage:
    """加载世界包并核对 world_id；不匹配时明确拒绝（不静默替换世界）。"""
    package = load(package_dir)
    if expected_world_id is not None and package.world_id != expected_world_id:
        raise schema.WorldPackageError(
            f"世界包 world_id 不匹配：包是 {package.world_id!r}，"
            f"运行环境期望 {expected_world_id!r}。拒绝加载，避免世界交叉污染。"
        )
    return package


__all__ = [
    "HOME_AREA_ID",
    "HOME_LOCATION_ID",
    "ROUTE_REVISION",
    "WorldPackage",
    "load",
    "load_if_compatible",
]
