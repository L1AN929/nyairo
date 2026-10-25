#!/usr/bin/env python3
"""运行时世界数据解析器（WorldGen 与 rc9 World Runtime 的接线层）。

解析规则：

1. 环境变量 ``CHIYO_WORLD_PACKAGE`` 指向一个世界包目录时，加载该世界包
   作为本进程的活动世界（结果按文件 mtime 缓存，改包即生效）；
2. 未设置时，返回内建汐见市（shiomi_city）数据——行为与本文件引入前
   的 rc9 完全一致，不改变任何默认路径。

五个世界模块（living_skeleton / travel_delivery / daily_rhythm /
social_presence / ambient_events）在运行时通过本模块读取世界数据，
替代直接读取各自的模块级硬编码常量。原常量保留：它们就是汐见市
默认数据的权威来源，也是既有测试的锚点。

本模块不做任何状态写入，没有第二权威：它只回答"当前活动世界是什么"。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

CHIYO_WORLD_PACKAGE_ENV = "CHIYO_WORLD_PACKAGE"

SHIOMI_WORLD_ID = "shiomi_city"
SHIOMI_TIMEZONE = "Asia/Tokyo"
SHIOMI_ROUTE_REVISION = "shiomi-walk-v1"
SHIOMI_HOME_LOCATION_ID = "home"
SHIOMI_HOME_AREA_ID = "bedroom"


class WorldDataError(ValueError):
    """活动世界数据不可用。"""


class WorldData:
    """一个活动世界的只读数据快照（统一形状，与来源无关）。

    v0.4 室内离散导航：世界包 scene.navigation 声明的建筑内部区域以
    **复合节点 ID**（``place_id/area_id``）并入导航图——place 节点即建筑
    入口，区域节点之间的连接来自显式声明；移动/旅行/授权全部复用既有
    机制，不引入第二套位置存储。
    """

    #: 建筑内部区域间步行耗时（离散导航，格内步行）。
    AREA_MOVE_SECONDS = 30

    def __init__(
        self,
        *,
        world_id: str,
        timezone_name: str,
        display_name: str,
        locations: dict[str, dict[str, Any]],
        rules: dict[str, dict[str, Any]],
        npcs: tuple[dict[str, Any], ...],
        ambient_templates: tuple[dict[str, Any], ...],
        walking_durations: dict[tuple[str, str], int],
        route_revision: str,
        home_location_id: str,
        home_area_id: str,
        is_custom: bool,
        package_dir: Optional[str] = None,
        navigation: Optional[dict[str, Any]] = None,
    ) -> None:
        self.world_id = world_id
        self.timezone_name = timezone_name
        self.display_name = display_name
        self.locations = locations
        self.rules = rules
        self.npcs = npcs
        self.ambient_templates = ambient_templates
        self.walking_durations = walking_durations
        self.route_revision = route_revision
        self.home_location_id = home_location_id
        self.home_area_id = home_area_id
        self.is_custom = is_custom
        self.package_dir = package_dir
        self._area_nav: dict[str, dict[str, Any]] = dict(navigation or {})
        self._inject_area_edges()

    def _inject_area_edges(self) -> None:
        """把区域连接注入步行耗时表（键即复合节点 ID）。"""
        for place_id, nav in self._area_nav.items():
            entrance = nav["entrance"]
            for connection in nav["connections"]:
                first = f"{place_id}/{connection['from']}"
                second = f"{place_id}/{connection['to']}"
                self.walking_durations[(first, second)] = self.AREA_MOVE_SECONDS
                self.walking_durations[(second, first)] = self.AREA_MOVE_SECONDS
            for area in nav["areas"]:
                area_id = area["area_id"]
                if area_id == entrance:
                    continue
                area_node = f"{place_id}/{area_id}"
                self.walking_durations[(place_id, area_node)] = self.AREA_MOVE_SECONDS
                self.walking_durations[(area_node, place_id)] = self.AREA_MOVE_SECONDS

    # -- 室内离散导航（v0.4） ----------------------------------------------

    def navigation_of(self, place_id: str) -> Optional[dict[str, Any]]:
        """建筑的导航声明；无导航（含汐见市）返回 None。"""
        return self._area_nav.get(place_id)

    def navigation_of_all(self) -> dict[str, dict[str, Any]]:
        """全部导航声明（只读视图）。"""
        return self._area_nav

    @staticmethod
    def node_place(node: str) -> str:
        """节点 → 所属地点 ID（复合节点取斜杠前部分）。"""
        return node.split("/", 1)[0]

    def split_node(self, node: str) -> tuple[str, str]:
        """节点 → (place_id, area_id)；place 节点返回 (place, 入口区/默认区)。"""
        if "/" not in node:
            nav = self._area_nav.get(node)
            if nav:
                return node, nav["entrance"]
            location = self.locations.get(node)
            return node, (location["area_id"] if location else node)
        place_id, area_id = node.split("/", 1)
        return place_id, area_id

    def state_node(self, place_id: str, area_id: str) -> Optional[str]:
        """world_state 的 location → 导航节点；非法组合返回 None。

        * 无导航的地点：节点 = place（area 仅随行，不影响导航）；
        * area == 入口区：节点 = place（入口与 place 同义）；
        * area 是已声明区域：节点 = ``place/area``；
        * 其他：None。
        """
        if place_id not in self.locations:
            return None
        nav = self._area_nav.get(place_id)
        if not nav:
            return place_id
        if area_id == nav["entrance"]:
            return place_id
        if any(area["area_id"] == area_id for area in nav["areas"]):
            return f"{place_id}/{area_id}"
        default_area = self.locations[place_id]["area_id"]
        if area_id == default_area:
            return place_id
        return None

    def node_exists(self, node: str) -> bool:
        if "/" not in node:
            return node in self.locations
        place_id, area_id = node.split("/", 1)
        nav = self._area_nav.get(place_id)
        return bool(nav) and any(area["area_id"] == area_id for area in nav["areas"])

    def node_projection(self, node: str) -> Optional[dict[str, Any]]:
        """节点的地点投影形状（骨架 _location 兼容）。"""
        if "/" not in node:
            location = self.locations.get(node)
            return dict(location) if location else None
        place_id, area_id = node.split("/", 1)
        nav = self._area_nav.get(place_id)
        location = self.locations.get(place_id)
        if not nav or not location:
            return None
        area = next((a for a in nav["areas"] if a["area_id"] == area_id), None)
        if area is None:
            return None
        return {
            "location_id": node,
            "name": f"{location['name']}·{area['name']}",
            "kind": location["kind"],
            "area_id": area_id,
            "description": f"{location['name']}的{area['name']}。",
        }

    def areas_of(self, place_id: str) -> list[dict[str, Any]]:
        nav = self._area_nav.get(place_id)
        return [dict(area) for area in nav["areas"]] if nav else []

    def furniture_in_area(self, place_id: str, area_id: str) -> list[str]:
        nav = self._area_nav.get(place_id)
        if not nav:
            return []
        return list(nav["furniture_map"].get(area_id, []))

    # -- 常用查询 ----------------------------------------------------------

    @property
    def known_location_ids(self) -> frozenset[str]:
        return frozenset(self.locations)

    def location(self, location_id: str) -> Optional[dict[str, Any]]:
        """地点/导航节点的投影（区域节点返回虚拟投影）。"""
        return self.node_projection(location_id)

    def area_id_of(self, location_id: str) -> str:
        """节点 → 写入 world_state 的 area_id。"""
        _, area_id = self.split_node(location_id)
        return area_id

    def location_catalog(self) -> list[dict[str, Any]]:
        return [dict(self.locations[key]) for key in self.locations]

    def reachable_of(self, node: str) -> list[str]:
        """节点的合法可达目标（地点邻接 + 室内区域导航）。

        * place 节点（= 建筑入口）：街道邻居 + 与入口相连的区域节点；
        * 区域节点：相邻区域节点；入口区域另含街道邻居（可离开建筑）。
        """
        if "/" not in node:
            location = self.locations.get(node)
            if location is None:
                return []
            neighbors = list(location["reachable"])
            nav = self._area_nav.get(node)
            if nav:
                entrance = nav["entrance"]
                for connection in nav["connections"]:
                    other = connection["to"] if connection["from"] == entrance else (
                        connection["from"] if connection["to"] == entrance else None
                    )
                    if other is not None:
                        neighbors.append(f"{node}/{other}")
            return sorted(set(neighbors))
        place_id, area_id = node.split("/", 1)
        nav = self._area_nav.get(place_id)
        if not nav or place_id not in self.locations:
            return []
        neighbors: list[str] = []
        for connection in nav["connections"]:
            other = (
                connection["to"] if connection["from"] == area_id
                else connection["from"] if connection["to"] == area_id
                else None
            )
            if other is None:
                continue
            if other == nav["entrance"]:
                # 入口区有双重表达：place 节点与 place/entrance 复合节点，
                # 两者写入 world_state 的 (place_id, area_id) 完全一致。
                neighbors.append(place_id)
                neighbors.append(f"{place_id}/{other}")
            else:
                neighbors.append(f"{place_id}/{other}")
        return sorted(set(neighbors))

    def route_duration_seconds(self, origin: str, destination: str) -> Optional[int]:
        return self.walking_durations.get((origin, destination))

    def canonical_node(self, node: str) -> Optional[str]:
        """节点规范化：入口区与 place 同义（``state_node`` 语义）；非法返回 None。"""
        if not isinstance(node, str) or not node:
            return None
        try:
            place_id, area_id = self.split_node(node)
        except Exception:
            return None
        return self.state_node(place_id, area_id)

    def all_nodes(self) -> list[str]:
        """全部合法导航节点（地点 + 已声明室内区域），稳定排序。"""
        nodes = set(self.locations)
        for place_id, nav in self._area_nav.items():
            for area in nav["areas"]:
                node = self.state_node(place_id, area["area_id"])
                if node:
                    nodes.add(node)
        return sorted(nodes)

    def _edge_seconds(self, origin: str, destination: str) -> Optional[int]:
        """一条合法边的非负耗时；缺耗时或非法耗时返回 None（视为不可通行）。"""
        seconds = self.route_duration_seconds(origin, destination)
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            return None
        return seconds

    def route_to(self, origin: str, destination: str) -> Optional[list[str]]:
        """世界导航图上的确定性最短路线（v0.3 通用导航）。

        返回 ``origin`` 之后依次经过的节点（含终点）；同节点返回 ``[]``；
        不可达或节点非法返回 ``None``。语义：

        * 边长 = ``route_duration_seconds``（非负整数）；缺耗时或负耗时的边
          视为不可通行，不会为了"能走"而发明耗时；
        * 最优 = 总耗时最小；等成本时按节点序列字典序取最小，保证同一世界
          同一对节点上的选路结果稳定可复现；
        * 只考虑简单路径（不重复节点）：非负边下重复节点不会更优；
        * 结果不区分室内/室外：室内区域节点与街道节点同在图上，连通关系
          由 ``reachable_of`` 决定。
        """
        start = self.canonical_node(origin)
        goal = self.canonical_node(destination)
        if start is None or goal is None:
            return None
        if start == goal:
            return []
        nodes = self.all_nodes()
        if start not in nodes or goal not in nodes:
            return None
        best_cost: dict[str, int] = {start: 0}
        best_path: dict[str, tuple[str, ...]] = {start: (start,)}
        for _ in range(len(nodes) + 1):
            improved = False
            for node in nodes:
                current_path = best_path.get(node)
                if current_path is None:
                    continue
                for neighbor in self.reachable_of(node):
                    nxt = self.canonical_node(neighbor)
                    if nxt is None or nxt not in nodes or nxt in current_path:
                        continue
                    seconds = self._edge_seconds(node, nxt)
                    if seconds is None:
                        continue
                    candidate = current_path + (nxt,)
                    total = best_cost[node] + seconds
                    existing = best_path.get(nxt)
                    if existing is None or (total, candidate) < (best_cost[nxt], existing):
                        best_cost[nxt] = total
                        best_path[nxt] = candidate
                        improved = True
            if not improved:
                break
        path = best_path.get(goal)
        return list(path[1:]) if path is not None else None
    def location_rule(self, location_id: str) -> dict[str, Any]:
        """返回骨架 ``_LocationRule`` 形状的规则；区域节点委托所属地点。"""
        rule = self.rules.get(location_id)
        if rule is None and "/" in location_id:
            rule = self.rules.get(self.node_place(location_id))
        if rule is None:
            raise WorldDataError(f"unknown location_id: {location_id}")
        return rule

    def summary(self) -> dict[str, Any]:
        return {
            "world_id": self.world_id,
            "timezone": self.timezone_name,
            "display_name": self.display_name,
            "is_custom": self.is_custom,
            "locations": len(self.locations),
            "package_dir": self.package_dir,
        }


# ---------------------------------------------------------------------------
# 汐见市默认世界（惰性读取模块常量，避免导入环）
# ---------------------------------------------------------------------------


def _shiomi_world() -> WorldData:
    import world_ambient_events as wae
    import world_daily_rhythm as wdr
    import world_living_skeleton as wls
    import world_social_presence as wsp
    import world_travel_delivery as wtd

    rules: dict[str, dict[str, Any]] = {}
    for location_id, rule in wdr._RULES.items():
        rules[location_id] = {
            "open_hour": rule.open_hour,
            "close_hour": rule.close_hour,
            "weekdays_only": rule.weekdays_only,
            "affordances": tuple(rule.affordances),
        }
    npcs: list[dict[str, Any]] = []
    for npc in wsp._NPCS:
        npcs.append(
            {
                "npc_id": npc.npc_id,
                "display_name": npc.display_name,
                "age_classification": npc.age_classification,
                "public_role": npc.public_role,
                "home_base_area": npc.home_base_area,
                "public_description": npc.public_description,
                "windows": tuple(
                    {
                        "weekdays": frozenset(window.weekdays),
                        "start_minute": window.start_minute,
                        "end_minute": window.end_minute,
                        "location_id": window.location_id,
                    }
                    for window in npc.windows
                ),
            }
        )
    ambient_templates = tuple(
        {
            "kind": "finite" if template.finite else "single_day",
            "event_type": template.event_type,
            "location_id": template.location_id,
            "summary": template.summary,
            "start_hour": template.start_hour,
            "end_hour": template.end_hour,
            # 目录元信息：类别与有限跨天定义（不进入事件投影字段）。
            # 世界包 v1 模板没有这些键，由 ambient_events 按单日日常模板处理。
            "category": template.category,
            **(
                {
                    "finite": {
                        "identity": template.finite.identity,
                        "starts_on": template.finite.starts_on.isoformat(),
                        "ends_on": template.finite.ends_on.isoformat(),
                        "end_reason": template.finite.end_reason,
                        "phases": tuple(
                            {
                                "local_date": phase.local_date.isoformat(),
                                "start_hour": phase.start_hour,
                                "end_hour": phase.end_hour,
                                "summary": phase.summary,
                            }
                            for phase in template.finite.phases
                        ),
                    }
                }
                if template.finite
                else {}
            ),
        }
        for template in wae._TEMPLATES
    )
    return WorldData(
        world_id=wls.WORLD_ID,
        timezone_name=wls.WORLD_TIMEZONE,
        display_name="汐见市",
        locations={key: dict(value) for key, value in wls._LOCATIONS.items()},
        rules=rules,
        npcs=tuple(npcs),
        ambient_templates=ambient_templates,
        walking_durations=dict(wtd.WALKING_DURATIONS),
        route_revision=wtd.ROUTE_REVISION,
        home_location_id=wls._HOME_LOCATION_ID,
        home_area_id=wls._LOCATIONS[wls._HOME_LOCATION_ID]["area_id"],
        is_custom=False,
        package_dir=None,
    )


# ---------------------------------------------------------------------------
# 世界包世界
# ---------------------------------------------------------------------------


def _import_worldgen():
    """worldgen 包位于本文件所在 components/ 目录下；确保可导入。"""
    try:
        import worldgen  # noqa: F401
    except ImportError:
        components_dir = str(Path(__file__).resolve().parents[2])
        if components_dir not in sys.path:
            sys.path.insert(0, components_dir)
    from worldgen import loader

    return loader


def _package_world(package_dir: Path) -> WorldData:
    loader = _import_worldgen()
    try:
        package = loader.load(package_dir, verify=True)
    except Exception as exc:
        raise WorldDataError(f"世界包 {package_dir} 无法加载: {exc}") from exc

    rules: dict[str, dict[str, Any]] = {}
    for location_id in package.locations:
        rule = package.location_rule(location_id)
        if rule is None:
            raise WorldDataError(f"世界包缺少地点 {location_id} 的营业规则")
        rules[location_id] = rule
    npcs: list[dict[str, Any]] = []
    for npc in package.npcs:
        npcs.append(
            {
                "npc_id": npc["npc_id"],
                "display_name": npc["display_name"],
                "age_classification": npc["age_classification"],
                "public_role": npc["public_role"],
                "home_base_area": npc["home_base_area"],
                "public_description": npc["public_description"],
                "windows": tuple(
                    {
                        "weekdays": frozenset(window["weekdays"]),
                        "start_minute": window["start_minute"],
                        "end_minute": window["end_minute"],
                        "location_id": window["location_id"],
                    }
                    for window in npc["windows"]
                ),
            }
        )
    home = package.location(loader.HOME_LOCATION_ID)
    if home is None:
        raise WorldDataError("世界包缺少 id 为 home 的住宅地点")
    if home["area_id"] != loader.HOME_AREA_ID:
        raise WorldDataError(
            f"世界包 home 的 area_id 必须是 {loader.HOME_AREA_ID!r}，实际 {home['area_id']!r}"
        )
    return WorldData(
        world_id=package.world_id,
        timezone_name=package.timezone_name,
        display_name=package.display_name,
        locations={key: dict(value) for key, value in package.locations.items()},
        rules=rules,
        npcs=tuple(npcs),
        ambient_templates=tuple(package.ambient_templates),
        walking_durations=dict(package.walking_durations),
        route_revision=package.route_revision,
        home_location_id=home["location_id"],
        home_area_id=home["area_id"],
        is_custom=True,
        package_dir=str(package_dir),
        navigation=(package.scene or {}).get("navigation"),
    )


# ---------------------------------------------------------------------------
# 活动世界解析（按 (包路径, world.json mtime, size) 缓存）
# ---------------------------------------------------------------------------

_CACHE: dict[tuple[str, int, int], WorldData] = {}
_SHIOMI_CACHE: list[WorldData] = []


def _package_key(path: Path) -> tuple[str, int, int]:
    marker = path / "world.json"
    try:
        stat = marker.stat()
    except OSError as exc:
        raise WorldDataError(f"CHIYO_WORLD_PACKAGE 指向的世界包不可读: {path} ({exc})") from exc
    return (str(path), stat.st_mtime_ns, stat.st_size)


def package_root(environment: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    env = environment if environment is not None else os.environ
    raw = env.get(CHIYO_WORLD_PACKAGE_ENV)
    if not raw or not str(raw).strip():
        return None
    return Path(str(raw).strip()).resolve()


def active_world(environment: Optional[Mapping[str, str]] = None) -> WorldData:
    """返回当前活动世界。未配置世界包时返回内建汐见市。"""
    root = package_root(environment)
    if root is None:
        if not _SHIOMI_CACHE:
            _SHIOMI_CACHE.append(_shiomi_world())
        return _SHIOMI_CACHE[0]
    key = _package_key(root)
    cached = _CACHE.get(key)
    if cached is None:
        cached = _package_world(root)
        _CACHE.clear()
        _CACHE[key] = cached
    return cached


def reset_cache() -> None:
    """测试辅助：清空全部缓存。"""
    _CACHE.clear()
    _SHIOMI_CACHE.clear()


__all__ = [
    "CHIYO_WORLD_PACKAGE_ENV",
    "SHIOMI_HOME_AREA_ID",
    "SHIOMI_HOME_LOCATION_ID",
    "SHIOMI_ROUTE_REVISION",
    "SHIOMI_TIMEZONE",
    "SHIOMI_WORLD_ID",
    "WorldData",
    "WorldDataError",
    "active_world",
    "package_root",
    "reset_cache",
]
