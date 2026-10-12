#!/usr/bin/env python3
"""程序化室内空间生成器（WorldGen v0.3）。

按建筑类型自动生成编辑器 v3 的室内场景：墙壁、门、地面分区与家具摆放。
只使用编辑器既有的九类家具（chair/bed/book/cup/cabinet/table/plant/
dispenser/lamp）、既有的几何规则（5% 网格、墙体阻挡、门窗依附）与既有的
动作模板——**不创造第二套室内格式**。

设计原则：

* 程序负责全部几何与合法性；GLM（可选）只提供语义建议——功能分区
  构成、家具组合、风格与细节文本（见 ``providers/glm.design_interior``），
  且建议必须落在本模块允许的家具种类与数量范围内，模型不能发明
  不存在的家具 ID、动作能力或地点引用；
* 同一建筑类型 + 同一种子 → 逐字节一致的布局；不同种子在分区位置、
  门窗偏移、家具数量与摆位上产生实际差异；
* 生成结果通过编辑器自己的 ``validate_space`` 校验，并复用编辑器的
  ``path``/``blocked`` 做通行性检查：入口可达每扇门的两侧、家具不占
  门格与入口邻域、家具不互相重叠（书可叠放在桌/柜上）。

边界（如实声明）：

* 生成的室内几何与家具供编辑器查看/编辑/试运行；rc9 World/Body 不消费
  室内几何，也没有墙体碰撞或家具交互（家具动作属于编辑器本地试运行）；
* 家具朝向用 ``visual.rotation`` 表达常见用途（如教室座椅面向讲台），
  仅为视觉语义。
"""

from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Any, Optional

from . import schema

#: 世界包地点 kind → 建筑类型（ outdoors 返回 None：无室内）。
BUILDING_BY_KIND = {
    "home": "home",
    "bookstore": "bookstore",
    "cafe": "cafe",
    "convenience_store": "convenience_store",
    "school": "classroom",
    "library": "library",
    "market": "market",
    "store": "shop_generic",
    "public": "hall",
    "shrine": "hall",
    "station": "hall",
}
BUILDING_LABELS = {
    "home": "住宅",
    "bookstore": "书店",
    "cafe": "咖啡馆",
    "convenience_store": "便利店",
    "classroom": "教室",
    "library": "图书馆",
    "market": "市场",
    "shop_generic": "商铺",
    "hall": "公共建筑",
}

#: GLM 建议可调整的家具种类（= 编辑器 KINDS 的子集）
DESIGNABLE_KINDS = ("bed", "book", "cabinet", "chair", "cup", "dispenser", "lamp", "plant", "table")

_WALL_COLORS = ("#8c8a7d", "#9a8f7f", "#877f74")
_DOOR_COLOR = "#bd976e"
_WINDOW_COLOR = "#9fc3cf"

#: 允许"叠放"的家具组合（书放桌/柜上、杯放吧台上；同一格，视觉堆叠而非错误重叠）
_STACKABLE = {"book": {"table", "cabinet"}, "cup": {"table"}}


class InteriorError(ValueError):
    """室内生成失败或校验不通过。"""


def _cell(x: int, y: int) -> tuple[int, int]:
    return (x, y)


def _percent(cell: tuple[int, int]) -> tuple[int, int]:
    """格子 → 摆放百分比坐标（格中心取整）。"""
    return (cell[0] * 5 + 2, cell[1] * 5 + 2)


class _Plan:
    """一栋建筑内部布局的装配器：区域导航声明 + 墙/门/地面/家具 + 占格登记。"""

    def __init__(self, rng: random.Random, building: str):
        self.rng = rng
        self.building = building
        self.space = {
            "enabled": True,
            "entry": {"x": 10, "y": 17},
            "walls": [],
            "doors": [],
            "windows": [],
            "floors": [],
        }
        self.furniture: list[dict[str, Any]] = []
        self._occupied: set[tuple[int, int]] = set()
        self._areas: list[dict[str, Any]] = []
        self._area_ids: set[str] = set()
        self._connections: list[dict[str, str]] = []
        self.entrance_area: Optional[str] = None

    # ---- 几何 ------------------------------------------------------------

    def wall(self, x1: int, y1: int, x2: int, y2: int) -> str:
        wall_id = f"w{len(self.space['walls']) + 1}"
        self.space["walls"].append(
            {"id": wall_id, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
             "color": self.rng.choice(_WALL_COLORS), "width": self.rng.choice((5, 6, 7))}
        )
        return wall_id

    def door(self, wall_id: str, offset: int, length: int = 15, *, state: str = "closed") -> str:
        door_id = f"{wall_id}-door"
        self.space["doors"].append(
            {"id": door_id, "wall_id": wall_id, "offset": offset, "length": length,
             "color": _DOOR_COLOR, "state": state,
             "actions": ["open_door", "close_door"]}
        )
        # 门前两侧保持畅通（居民要能走到门边开门）。
        self._occupied.update(_door_cells_for_wall(self.space, wall_id))
        return door_id

    def window(self, wall_id: str, offset: int, length: int = 15) -> str:
        window_id = f"{wall_id}-win"
        self.space["windows"].append(
            {"id": window_id, "wall_id": wall_id, "offset": offset, "length": length,
             "color": _WINDOW_COLOR}
        )
        return window_id

    def floor(self, x: int, y: int, w: int, h: int, color: str, texture: str = "planks") -> str:
        floor_id = f"f{len(self.space['floors']) + 1}"
        self.space["floors"].append(
            {"id": floor_id, "x": x, "y": y, "w": w, "h": h,
             "color": color, "texture": texture, "asset_id": None}
        )
        return floor_id

    # ---- 导航区域声明（v0.4：室内离散导航） -------------------------------

    def area(self, area_id: str, name: str, kind: str) -> str:
        """声明一个功能区域（导航节点）。"""
        if area_id in self._area_ids:
            raise InteriorError(f"区域 {area_id!r} 重复声明")
        self._area_ids.add(area_id)
        self._areas.append({"area_id": area_id, "name": name, "kind": kind})
        return area_id

    def connect(self, a: str, b: str) -> None:
        """声明两个区域之间的合法通行连接（双向）。"""
        for area_id in (a, b):
            if area_id not in self._area_ids:
                raise InteriorError(f"连接引用未声明区域 {area_id!r}")
        self._connections.append({"from": a, "to": b})

    def set_entrance(self, area_id: str) -> None:
        if area_id not in self._area_ids:
            raise InteriorError(f"入口区域 {area_id!r} 未声明")
        self.entrance_area = area_id

    def navigation(self) -> dict[str, Any]:
        """导出导航声明；未显式设置入口时取第一个区域。"""
        entrance = self.entrance_area or (self._areas[0]["area_id"] if self._areas else "main")
        furniture_map: dict[str, list[str]] = {area["area_id"]: [] for area in self._areas}
        for index, item in enumerate(self.furniture, start=1):
            furniture_map.setdefault(item.get("_area", entrance), []).append(
                f"{item.get('_object_prefix', 'obj')}{index:02d}"
            )
        return {
            "areas": [dict(area) for area in self._areas],
            "connections": [dict(connection) for connection in self._connections],
            "entrance": entrance,
            "furniture_map": furniture_map,
        }

    # ---- 家具 ------------------------------------------------------------

    def furniture_at(
        self,
        *,
        kind: str,
        cell: tuple[int, int],
        label: str,
        rotation: int = 0,
        note: str = "",
        stack_on: Optional[tuple[int, int]] = None,
        area: Optional[str] = None,
        object_prefix: str = "obj",
    ) -> dict[str, Any]:
        """登记一件家具；占格检查（叠放组合除外）；归属导航区域。"""
        if area is not None and area not in self._area_ids:
            raise InteriorError(f"家具 {label} 归属未声明区域 {area!r}")
        if stack_on is None and cell in self._occupied:
            raise InteriorError(f"家具 {label} 的格位 {cell} 已被占用")
        if stack_on is not None and kind not in _STACKABLE:
            raise InteriorError(f"家具 {kind} 不允许叠放")
        defaults = {
            "bed": (["rest"], {"rest": {"minutes": 30, "energy_gain": 15}}, False, 0, "neutral"),
            "chair": (["sit"], {}, False, 0, "neutral"),
            "table": ([], {}, False, 0, "neutral"),
            "cabinet": (["store", "retrieve"], {}, False, 4, "neutral"),
            "book": (["pick_up", "place", "read"], {"read": {"minutes": 15, "energy_cost": 3}}, True, 0, "neutral"),
            "cup": (["pick_up", "place", "drink"], {"drink": {"hydration_gain": 20}}, True, 0, "empty"),
            # dispenser 的 toggle_device 有参数，behavior 必须包含它（编辑器严格校验）。
            "dispenser": (["fill", "toggle_device"], {"fill": {"minutes": 2, "servings": 1}, "toggle_device": {"minutes": 1}}, False, 0, "available"),
            "lamp": (["toggle"], {"toggle": {"minutes": 1}}, False, 0, "off"),
            "plant": ([], {}, False, 0, "neutral"),
        }[kind]
        actions, behavior, portable, capacity, initial_status = defaults
        px, py = _percent(stack_on if stack_on is not None else cell)
        item = {
            "kind": kind,
            "x": px,
            "y": py,
            "label": label,
            "note": note,
            "actions": list(actions),
            "behavior": {name: dict(params) for name, params in behavior.items()},
            "portable": portable,
            "capacity": capacity,
            "servings": 0,
            "initial_status": initial_status,
            "visual": {"scale": 100, "rotation": rotation % 360, "layer": 0, "show_name": True},
            "interaction": {"near": False, "radius": 2},
            "asset_id": None,
            "_cell": stack_on if stack_on is not None else cell,
            "_area": area,
            "_object_prefix": object_prefix,
        }
        self.furniture.append(item)
        self._occupied.add(cell)
        return item

    def free_cell(self, zone: tuple[int, int, int, int], *, tries: int = 24) -> tuple[int, int]:
        """在区格（cx0,cy0,cx1,cy1 闭区间）内找一个与现有家具保持间距的空格。"""
        cx0, cy0, cx1, cy1 = zone
        candidates = [
            (cx, cy)
            for cy in range(cy0, cy1 + 1)
            for cx in range(cx0, cx1 + 1)
            if (cx, cy) not in self._occupied
        ]
        self.rng.shuffle(candidates)
        for cell in candidates:
            if self._clearance_ok(cell):
                return cell
        if candidates:  # 兜底：放宽间距
            return candidates[0]
        raise InteriorError(f"区格 {zone} 没有可用格位（{self.building}）")

    def _clearance_ok(self, cell: tuple[int, int]) -> bool:
        ex, ey = self.space["entry"]["x"], self.space["entry"]["y"]
        if abs(cell[0] - ex) + abs(cell[1] - ey) <= 1:
            return False  # 入口及其邻域保持畅通
        for occupied in self._occupied:
            if max(abs(cell[0] - occupied[0]), abs(cell[1] - occupied[1])) < 2:
                return False  # 家具之间保持一格间距，避免视觉重叠
        return True

    def keep_clear(self, cells: set[tuple[int, int]]) -> None:
        """声明这些格位必须保持畅通（门前/通道），家具规划时避开。"""
        self._occupied.update(cells)  # 复用占格集合实现"不可放置"


def _door_cells_for_wall(space: dict[str, Any], wall_id: str) -> set[tuple[int, int]]:
    """墙上所有门覆盖的格位（近似：门跨度内的格子两侧）。"""
    wall = next(w for w in space["walls"] if w["id"] == wall_id)
    vertical = wall["x1"] == wall["x2"]
    cells: set[tuple[int, int]] = set()
    for door in space["doors"]:
        if door["wall_id"] != wall_id:
            continue
        start, end = door["offset"], door["offset"] + door["length"]
        for offset in range(start, end, 5):
            if vertical:
                px, py = wall["x1"], wall["y1"] + offset + 2
                cells.add((min(19, int(px // 5) - 1), min(19, int(py // 5))))
                cells.add((min(19, int(px // 5)), min(19, int(py // 5))))
            else:
                px, py = wall["x1"] + offset + 2, wall["y1"]
                cells.add((min(19, int(px // 5)), min(19, int(py // 5) - 1)))
                cells.add((min(19, int(px // 5)), min(19, int(py // 5))))
    return {cell for cell in cells if 0 <= cell[0] < 20 and 0 <= cell[1] < 20}


# ---------------------------------------------------------------------------
# 各建筑类型模板
# ---------------------------------------------------------------------------


def _build_home(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    partition_y = rng.choice((45, 50, 55))
    kitchen_x = rng.choice((55, 60, 65))
    wall = plan.wall(0, partition_y, 100, partition_y)
    plan.door(wall, rng.choice((35, 40, 50)))
    kitchen_wall = plan.wall(kitchen_x, partition_y, kitchen_x, 100)
    plan.door(kitchen_wall, rng.choice((20, 30)))
    plan.floor(0, 0, 100, partition_y, "#f0e3d3", "planks")
    plan.floor(0, partition_y, kitchen_x, 100 - partition_y, "#e9e2d3", "planks")
    plan.floor(kitchen_x, partition_y, 100 - kitchen_x, 100 - partition_y, "#dbe5e2", "tiles")
    plan.space["entry"] = {"x": 8, "y": 17}
    # 导航区域：客厅（入口）↔ 卧室 / 厨房（与墙门几何一致）
    plan.area("living", "客厅", "living")
    plan.area("bedroom", "卧室", "bedroom")
    plan.area("kitchen", "厨房", "kitchen")
    plan.connect("living", "bedroom")
    plan.connect("living", "kitchen")
    plan.set_entrance("living")
    # 卧室（上半区）
    plan.furniture_at(kind="bed", cell=(rng.choice((3, 4)), 3), label="床", rotation=0, note="临窗的单人床。", area="bedroom", object_prefix="bed")
    plan.furniture_at(kind="cabinet", cell=(16, 3), label="衣柜", rotation=90, note="靠墙的衣柜。", area="bedroom", object_prefix="wardrobe")
    plan.furniture_at(kind="lamp", cell=(2, 6), label="床头灯", rotation=0, area="bedroom", object_prefix="lamp")
    if rng.random() < 0.5:
        plan.furniture_at(kind="plant", cell=(18, 6), label="盆栽", rotation=0, area="bedroom", object_prefix="plant")
    # 客厅（左下区）
    plan.furniture_at(kind="lamp", cell=(1, 9), label="落地灯", rotation=0, area="living", object_prefix="lamp")
    table_cell = plan.free_cell((3, 10, 8, 14))
    plan.furniture_at(kind="table", cell=table_cell, label="餐桌", rotation=0, area="living", object_prefix="table")
    for _ in range(rng.choice((1, 2))):
        chair_cell = plan.free_cell((max(1, table_cell[0] - 2), table_cell[1] - 1, min(9, table_cell[0] + 2), table_cell[1] + 1))
        plan.furniture_at(kind="chair", cell=chair_cell, label="椅子", rotation=rng.choice((0, 90, 180, 270)), area="living", object_prefix="chair")
    # 厨房（右下区）
    plan.furniture_at(kind="cabinet", cell=(17, 15), label="橱柜", rotation=0, note="料理台边的橱柜。", area="kitchen", object_prefix="cabinet")
    plan.furniture_at(kind="dispenser", cell=(14, 13), label="饮水机", rotation=270, area="kitchen", object_prefix="dispenser")
    if rng.random() < 0.4:
        plan.furniture_at(kind="plant", cell=(18, 12), label="香草盆栽", rotation=0, area="kitchen", object_prefix="plant")


def _build_bookstore(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    wall_x = rng.choice((55, 60, 65))
    wall = plan.wall(wall_x, 0, wall_x, 100)
    door_offset = rng.choice((70, 80))
    plan.door(wall, door_offset)
    if rng.random() < 0.5:
        plan.window(wall, rng.choice((15, 25)), 15)
    plan.floor(0, 0, wall_x, 100, "#e8e8dc", "planks")
    plan.floor(wall_x, 0, 100 - wall_x, 100, "#e4e9df", "planks")
    plan.space["entry"] = {"x": 3, "y": 17}
    # 导航区域：店前区（入口/书架/收银）↔ 阅读区
    plan.area("shopfront", "店前区", "entrance")
    plan.area("reading_area", "阅读区", "reading")
    plan.connect("shopfront", "reading_area")
    plan.set_entrance("shopfront")
    # 门前通道保持畅通。
    plan.keep_clear({(2, 17), (3, 17), (4, 17), (3, 16), (3, 15), (3, 14)})
    # 书架（前区靠墙一排）
    shelf_count = rng.choice((2, 3, 4))
    shelf_cells = [(2 + i * 3, 4) for i in range(shelf_count)]
    for i, cell in enumerate(shelf_cells):
        plan.furniture_at(kind="cabinet", cell=cell, label=f"书架{i + 1}", rotation=0, note="塞满旧书的书架。", area="shopfront", object_prefix="shelf")
    # 收银台（门边）
    plan.furniture_at(kind="table", cell=(7, 13), label="收银台", rotation=90, note="贴着手写价目的柜台。", area="shopfront", object_prefix="counter")
    plan.furniture_at(kind="book", cell=(7, 13), label="待售旧书", stack_on=(7, 13), note="摞在柜台上的待售书。", area="shopfront", object_prefix="book")
    # 阅读区（后区靠窗）
    table_cell = plan.free_cell((12, 9, 17, 12))
    plan.furniture_at(kind="table", cell=table_cell, label="阅读桌", rotation=0, note="靠窗的阅读位。", area="reading_area", object_prefix="table")
    chair_cell = plan.free_cell((table_cell[0] - 2, table_cell[1] + 1, table_cell[0] + 2, min(18, table_cell[1] + 2)))
    plan.furniture_at(kind="chair", cell=chair_cell, label="阅读椅", rotation=180, area="reading_area", object_prefix="chair")
    plan.furniture_at(kind="lamp", cell=(plan.free_cell((11, 4, 18, 7))), label="阅读灯", rotation=0, area="reading_area", object_prefix="lamp")
    if rng.random() < 0.6:
        plan.furniture_at(kind="plant", cell=(plan.free_cell((11, 15, 18, 18))), label="蕨类盆栽", rotation=0, area="reading_area", object_prefix="plant")


def _build_cafe(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    if rng.random() < 0.55:
        bar_wall = rng.choice((45, 50, 55))
        plan.wall(bar_wall, 0, bar_wall, rng.choice((35, 40, 45)))  # 半隔断：吧台区，端点可绕行
    floor_pair = rng.choice((
        ("#e8ddd0", "#efe6da"), ("#e3d8c8", "#f0e8dd"), ("#e8e0d4", "#ece2d2"),
    ))
    plan.floor(0, 0, 100, rng.choice((40, 45, 50)), floor_pair[0], "tiles")
    plan.floor(0, 45, 100, 55, floor_pair[1], "planks")
    plan.space["entry"] = {"x": rng.choice((9, 10, 11)), "y": 17}
    plan.keep_clear({(9, 17), (10, 17), (11, 17), (10, 16), (10, 15)})
    # 导航区域：吧台区（入口）↔ 座位区（开放式连通，半隔断不阻挡）
    plan.area("bar", "吧台区", "counter")
    plan.area("seating", "座位区", "seating")
    plan.connect("bar", "seating")
    plan.set_entrance("bar")
    # 吧台（上方）：两段桌 + 咖啡机 + 杯子
    bar_x = rng.choice((3, 4, 5))
    plan.furniture_at(kind="table", cell=(bar_x, 3), label="吧台", rotation=0, note="手写菜单板下的吧台。", area="bar", object_prefix="counter")
    plan.furniture_at(kind="table", cell=(bar_x + 2, 3), label="吧台", rotation=0, note="手写菜单板下的吧台。", area="bar", object_prefix="counter")
    plan.furniture_at(kind="dispenser", cell=(bar_x + 3, 3), label="咖啡机", rotation=0, note="嗞嗞作响的意式咖啡机。", area="bar", object_prefix="machine")
    plan.furniture_at(kind="cup", cell=(bar_x, 3), label="干净杯子", stack_on=(bar_x, 3), note="倒扣在吧台上的杯子。", area="bar", object_prefix="cup")
    # 座位区
    seats = rng.sample(((13, 8), (4, 10), (15, 13), (11, 11)), k=rng.choice((2, 3, 4)))
    for index, base_cell in enumerate(seats):
        plan.furniture_at(kind="table", cell=base_cell, label=f"座位桌{index + 1}", rotation=0, area="seating", object_prefix="table")
        chair_cell = plan.free_cell((base_cell[0] - 2, base_cell[1] - 1, base_cell[0] + 2, base_cell[1] + 1))
        plan.furniture_at(kind="chair", cell=chair_cell, label="椅子", rotation=rng.choice((0, 90, 180, 270)), area="seating", object_prefix="chair")
    plan.furniture_at(kind="plant", cell=(18, 17), label="绿植", rotation=0, area="seating", object_prefix="plant")
    plan.furniture_at(kind="lamp", cell=(1, 5), label="暖色壁灯", rotation=0, area="bar", object_prefix="lamp")


def _build_convenience_store(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    plan.floor(0, 0, 100, 100, rng.choice(("#e6ecee", "#e8ecee", "#e3ecee")), "tiles")
    entry_x = rng.choice((9, 10, 11))
    plan.floor(entry_x * 5 - 15, 70, 30, 30, "#eef3f4", "tiles")  # 入口区
    plan.space["entry"] = {"x": entry_x, "y": 17}
    plan.keep_clear({(entry_x - 1, 17), (entry_x, 17), (entry_x + 1, 17),
                     (entry_x, 16), (entry_x, 15), (entry_x, 14), (entry_x, 13)})
    plan.area("sales_floor", "营业厅", "sales")
    plan.set_entrance("sales_floor")
    # 商品架两排（行位置/数量/朝向有变化）
    rows = rng.choice((3, 4))
    first_row_y = rng.choice((3, 4, 5))
    gap = rng.choice((2, 3))
    for i in range(rows):
        plan.furniture_at(kind="cabinet", cell=(2 + i * gap, first_row_y), label=f"货架{i + 1}", rotation=0, note="码得整整齐齐的商品架。", area="sales_floor", object_prefix="shelf")
    second_rows = rng.choice((1, 2, 3))
    for i in range(second_rows):
        plan.furniture_at(kind="cabinet", cell=(2 + i * gap, first_row_y + 4), label=f"中岛货架{i + 1}", rotation=180, area="sales_floor", object_prefix="shelf")
    # 收银台（门侧，位置随入口）与冷藏柜
    plan.furniture_at(kind="table", cell=(min(18, entry_x + 5), 14), label="收银台", rotation=270, note="摆着关东煮的收银台。", area="sales_floor", object_prefix="counter")
    plan.furniture_at(kind="dispenser", cell=(18, rng.choice((6, 8))), label="冷藏柜", rotation=270, note="嗡嗡作响的冷藏饮料柜。", area="sales_floor", object_prefix="cooler")
    if rng.random() < 0.6:
        plan.furniture_at(kind="plant", cell=(1, 17), label="门口绿植", rotation=0, area="sales_floor", object_prefix="plant")
    if rng.random() < 0.4:
        plan.furniture_at(kind="book", cell=(min(17, entry_x + 4), 14), label="杂志架", stack_on=(min(17, entry_x + 4), 14), note="收银台边的杂志。", area="sales_floor", object_prefix="magazine")


def _build_classroom(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    plan.floor(0, 0, 100, 100, rng.choice(("#eae4d6", "#e7e2d4", "#ece6da")), "planks")
    plan.floor(0, 0, 100, rng.choice((15, 20)), "#dfe4e8", "tiles")  # 讲台区
    # 黑板：用一段深色短墙表达（素材库暂无黑板物件，见未适配清单）
    board_left = rng.choice((30, 35, 40))
    board = plan.wall(board_left, 10, board_left + 30, 10)
    plan.space["walls"][-1]["color"] = "#3d4a5c"
    del board
    plan.space["entry"] = {"x": rng.choice((2, 3, 4)), "y": 17}
    plan.keep_clear({(1, 17), (2, 17), (3, 17), (4, 17), (5, 17), (3, 16)})
    plan.area("classroom", "教室", "classroom")
    plan.set_entrance("classroom")
    # 讲台
    plan.furniture_at(kind="table", cell=(rng.choice((8, 9, 10)), rng.choice((3, 4))), label="讲台", rotation=0, note="放着粉笔盒的讲台。", area="classroom", object_prefix="podium")
    # 课桌椅阵列（椅面向讲台；行列数与起始位置随种子变化）
    # rows=3 时行距固定 3，保证椅位不落入入口避让区（y≤15）。
    rows, cols = rng.choice(((2, 3), (3, 3), (2, 4), (3, 4), (2, 5)))
    row_gap = 3 if rows == 3 else rng.choice((3, 4))
    start_x = rng.choice((2, 3, 4)) + max(0, (18 - (cols - 1) * 3) // 2 - 4)
    for r in range(rows):
        for c in range(cols):
            cx = start_x + c * 3
            cy = 7 + r * row_gap
            plan.furniture_at(kind="table", cell=(cx, cy), label=f"课桌{r * cols + c + 1}", rotation=0, area="classroom", object_prefix="desk")
            plan.furniture_at(kind="chair", cell=(cx, cy + 1), label=f"座椅{r * cols + c + 1}", rotation=180, area="classroom", object_prefix="chair")
    if rng.random() < 0.6:
        plan.furniture_at(kind="cabinet", cell=(18, 4), label="教具柜", rotation=270, area="classroom", object_prefix="cabinet")
    if rng.random() < 0.7:
        plan.furniture_at(kind="plant", cell=(1, 3), label="窗台绿植", rotation=0, area="classroom", object_prefix="plant")


def _build_library(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    plan.floor(0, 0, 100, 100, "#e6e6da", "planks")
    plan.space["entry"] = {"x": 10, "y": 17}
    plan.keep_clear({(9, 17), (10, 17), (11, 17), (10, 16)})
    plan.area("reading_hall", "阅览区", "reading")
    plan.area("stacks", "书库区", "stacks")
    plan.connect("reading_hall", "stacks")
    plan.set_entrance("reading_hall")
    for i in range(rng.choice((3, 4))):
        plan.furniture_at(kind="cabinet", cell=(2 + i * 3, 4), label=f"书库架{i + 1}", rotation=0, note="按索号排列的书库书架。", area="stacks", object_prefix="shelf")
    for i in range(rng.choice((2, 3))):
        cell = plan.free_cell((3 + i * 5, 9, 8 + i * 5, 12))
        plan.furniture_at(kind="table", cell=cell, label=f"自习桌{i + 1}", rotation=0, area="reading_hall", object_prefix="table")
        plan.furniture_at(kind="chair", cell=plan.free_cell((cell[0] - 1, cell[1] + 1, cell[0] + 1, cell[1] + 1)), label="自习椅", rotation=180, area="reading_hall", object_prefix="chair")
    plan.furniture_at(kind="lamp", cell=(17, 5), label="阅览灯", rotation=0, area="reading_hall", object_prefix="lamp")


def _build_market(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    plan.floor(0, 0, 100, 100, "#e9e6da", "tiles")
    plan.space["entry"] = {"x": 10, "y": 17}
    plan.keep_clear({(9, 17), (10, 17), (11, 17), (10, 16)})
    plan.area("stalls", "摊位区", "market")
    plan.set_entrance("stalls")
    for i in range(rng.choice((3, 4))):
        plan.furniture_at(kind="table", cell=(2 + i * 3, 5), label=f"摊位{i + 1}", rotation=0, note="摆满当季蔬果的摊位。", area="stalls", object_prefix="stall")
        plan.furniture_at(kind="cabinet", cell=(2 + i * 3, 9), label=f"摊柜{i + 1}", rotation=180, area="stalls", object_prefix="cabinet")
    plan.furniture_at(kind="dispenser", cell=(17, 4), label="冰柜", rotation=270, area="stalls", object_prefix="cooler")
    plan.furniture_at(kind="plant", cell=(1, 16), label="摊边盆栽", rotation=0, area="stalls", object_prefix="plant")


def _build_hall(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    plan.floor(0, 0, 100, 100, "#e7e3d8", "planks")
    plan.space["entry"] = {"x": 10, "y": 17}
    plan.keep_clear({(9, 17), (10, 17), (11, 17), (10, 16)})
    plan.area("hall", "门厅", "hall")
    plan.set_entrance("hall")
    for i, base_cell in enumerate(((3, 4), (9, 4), (15, 4))):
        plan.furniture_at(kind="chair", cell=base_cell, label=f"靠墙椅{i + 1}", rotation=0, area="hall", object_prefix="chair")
    cell = plan.free_cell((7, 8, 12, 11))
    plan.furniture_at(kind="table", cell=cell, label="长桌", rotation=0, note="社区公告与杂物桌。", area="hall", object_prefix="table")
    plan.furniture_at(kind="cabinet", cell=(17, 13), label="储物柜", rotation=270, area="hall", object_prefix="cabinet")
    plan.furniture_at(kind="plant", cell=(2, 12), label="盆栽", rotation=0, area="hall", object_prefix="plant")
    plan.furniture_at(kind="lamp", cell=(17, 3), label="角灯", rotation=0, area="hall", object_prefix="lamp")


def _build_shop_generic(plan: _Plan, design: Optional[dict[str, Any]]) -> None:
    rng = plan.rng
    plan.floor(0, 0, 100, 100, "#ece7dc", "tiles")
    plan.space["entry"] = {"x": 10, "y": 17}
    plan.keep_clear({(9, 17), (10, 17), (11, 17), (10, 16)})
    plan.area("shop_floor", "店面", "sales")
    plan.set_entrance("shop_floor")
    for i in range(rng.choice((2, 3))):
        plan.furniture_at(kind="cabinet", cell=(3 + i * 4, 4), label=f"陈列柜{i + 1}", rotation=0, area="shop_floor", object_prefix="cabinet")
    plan.furniture_at(kind="table", cell=(15, 12), label="柜台", rotation=90, area="shop_floor", object_prefix="counter")
    plan.furniture_at(kind="dispenser", cell=(18, 5), label="饮水机", rotation=270, area="shop_floor", object_prefix="dispenser")
    plan.furniture_at(kind="plant", cell=(2, 15), label="门口盆栽", rotation=0, area="shop_floor", object_prefix="plant")


_BUILDERS = {
    "home": _build_home,
    "bookstore": _build_bookstore,
    "cafe": _build_cafe,
    "convenience_store": _build_convenience_store,
    "classroom": _build_classroom,
    "library": _build_library,
    "market": _build_market,
    "hall": _build_hall,
    "shop_generic": _build_shop_generic,
}

#: GLM 语义建议允许调整的家具数量上下限（每类）
_FURNITURE_COUNT_BOUNDS = {
    "book": (0, 4), "chair": (0, 8), "table": (1, 6), "cabinet": (0, 8),
    "plant": (0, 3), "lamp": (0, 3), "cup": (0, 3), "dispenser": (0, 2), "bed": (0, 2),
}


def _apply_design(plan: _Plan, design: dict[str, Any]) -> list[str]:
    """把 GLM 语义建议落到布局上：家具数量微调（不越界）。

    返回采纳说明；几何与合法性仍完全由程序决定。
    """
    rng = plan.rng
    notes: list[str] = []
    template_counts: dict[str, int] = {}
    for item in plan.furniture:
        template_counts[item["kind"]] = template_counts.get(item["kind"], 0) + 1
    for suggestion in design.get("furniture", []):
        kind = suggestion["kind"]
        target = min(suggestion["count"], _FURNITURE_COUNT_BOUNDS.get(kind, (0, 0))[1])
        current = template_counts.get(kind, 0)
        if kind == "table" or target <= current:
            continue  # 桌子是结构性的；只做增量调整
        for _ in range(target - current):
            try:
                cell = plan.free_cell((2, 3, 17, 15))
            except InteriorError:
                break
            label = suggestion.get("label") or {"book": "书", "chair": "椅子", "cabinet": "柜子",
                                                 "plant": "盆栽", "lamp": "灯", "cup": "杯子",
                                                 "dispenser": "饮水机", "bed": "床"}.get(kind, kind)
            plan.furniture_at(kind=kind, cell=cell, label=label, rotation=rng.choice((0, 90, 180, 270)))
            template_counts[kind] = template_counts.get(kind, 0) + 1
        notes.append(f"{kind} ×{template_counts.get(kind, 0)}（建议 {suggestion.get('purpose', '')}）")
    return notes


def _validate_passability(plan: _Plan) -> dict[str, Any]:
    """复用编辑器的 path/blocked 做通行性检查。"""
    from importlib import import_module
    import sys as _sys
    from pathlib import Path as _Path

    editor_dir = _Path(__file__).resolve().parents[1] / "world-editor"
    if str(editor_dir) not in _sys.path:
        _sys.path.insert(0, str(editor_dir))
    space_mod = import_module("space")
    space = plan.space
    door_states = {door["id"]: "open" for door in space["doors"]}
    entry = (space["entry"]["x"], space["entry"]["y"])

    furniture_cells = {
        item["_cell"] for item in plan.furniture if not item.get("_stacked")
    }
    free_cells = {
        (cx, cy) for cx in range(20) for cy in range(20)
        if (cx, cy) not in furniture_cells
    }

    def reachable_from(start: tuple[int, int]) -> set[tuple[int, int]]:
        seen = {start}
        queue = [start]
        while queue:
            a = queue.pop()
            for b in ((a[0] + 1, a[1]), (a[0] - 1, a[1]), (a[0], a[1] + 1), (a[0], a[1] - 1)):
                if 0 <= b[0] < 20 and 0 <= b[1] < 20 and b not in seen and not space_mod.blocked(space, door_states, a, b):
                    seen.add(b)
                    queue.append(b)
        return seen

    reach = reachable_from(entry)
    free_reach = {cell for cell in reach if cell not in furniture_cells}
    free_total = len(free_cells)
    # 每扇门两侧都可达（居民能够走到门边开门）。
    for door in space["doors"]:
        for cell in space_mod.door_cells(space, door):
            if cell in free_cells and cell not in reach:
                raise InteriorError(f"门 {door['id']} 的格位 {cell} 从入口不可达")
    ratio = len(free_reach) / max(1, free_total)
    if ratio < 0.55:
        raise InteriorError(
            f"通行性不足：入口可达的自由格 {len(free_reach)}/{free_total}（{ratio:.0%}）"
        )
    return {"reachable_free_cells": len(free_reach), "free_cells": free_total, "ratio": round(ratio, 2)}


def generate_interior(
    *,
    building: str,
    seed: int,
    location_id: str = "",
    location_name: str = "",
    design: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """为一栋建筑生成室内场景与家具。

    返回::

        {
          "building": 建筑类型,
          "space": 编辑器 v3 space 字典,
          "furniture": [家具描述（kind/label/note/几何/动作/视觉...，无 ID）],
          "passability": {...通行性统计...},
          "design_applied": [GLM 建议采纳说明],
        }

    同一 (building, seed, design) 组合的结果确定性一致。
    """
    if building not in _BUILDERS:
        raise InteriorError(
            f"未知建筑类型 {building!r}；支持 {sorted(_BUILDERS)}"
        )
    rng = random.Random(f"nyairo-interior|v0.3|{building}|{seed}|{location_id}")
    plan = _Plan(rng, building)
    _BUILDERS[building](plan, design)
    design_applied: list[str] = []
    if design:
        design_applied = _apply_design(plan, design)
    plan.space["enabled"] = True
    passability = _validate_passability(plan)

    # 编辑器几何校验（使用编辑器自己的规则）。
    editor = _editor_module()
    editor.validate_space(plan.space, {})
    # 门格与家具占格不得冲突（叠放书除外）。
    occupied = {item["_cell"] for item in plan.furniture}
    for door in plan.space["doors"]:
        clash = _door_cells_for_wall(plan.space, door["wall_id"]) & occupied
        if clash:
            raise InteriorError(f"家具占据了门 {door['id']} 的格位: {sorted(clash)}")

    furniture = []
    furniture_map: dict[str, list[str]] = {
        area["area_id"]: [] for area in plan._areas
    }
    for index, item in enumerate(plan.furniture, start=1):
        entry = {
            "kind": item["kind"],
            "x": item["x"],
            "y": item["y"],
            "label": item["label"],
            "note": item["note"],
            "actions": item["actions"],
            "behavior": item["behavior"],
            "portable": item["portable"],
            "capacity": item["capacity"],
            "servings": item["servings"],
            "initial_status": item["initial_status"],
            "visual": item["visual"],
            "interaction": item["interaction"],
            "asset_id": item["asset_id"],
            # v0.4 导航归属：区域 ID 与对象 ID 片段（全局序号；调用方拼接完整 ID）。
            "zone": item["_area"] or plan.entrance_area,
            "prefix": item["_object_prefix"],
        }
        furniture.append(entry)
        furniture_map.setdefault(entry["zone"], []).append(f"{entry['prefix']}{index:02d}")
    navigation = plan.navigation()
    navigation["furniture_map"] = furniture_map
    return {
        "building": building,
        "building_label": BUILDING_LABELS[building],
        "space": plan.space,
        "furniture": furniture,
        "navigation": navigation,
        "passability": passability,
        "design_applied": design_applied,
    }


def _editor_module():
    from .editor_bridge import _editor

    return _editor()


def building_for_kind(kind: str) -> Optional[str]:
    """世界包地点 kind → 建筑类型；户外地点返回 None。"""
    return BUILDING_BY_KIND.get(kind)


#: 精确地点 ID → 建筑类型（生成器给这些槽位固定的 ID）
_EXACT_ID_BUILDINGS = {
    "home": "home",
    "bookstore": "bookstore",
    "cafe": "cafe",
    "convenience_store": "convenience_store",
    "school": "classroom",
    "library": "library",
    "market": "market",
}
#: 名称关键词 → 建筑类型（二级推断）
_NAME_HINTS = (
    ("书店", "bookstore"), ("书房", "bookstore"), ("书架", "bookstore"),
    ("咖啡", "cafe"), ("喫茶", "cafe"),
    ("便利店", "convenience_store"),
    ("学校", "classroom"), ("学园", "classroom"),
    ("图书馆", "library"), ("图书", "library"), ("文库", "library"),
    ("市场", "market"), ("鱼", "market"), ("早市", "market"),
    ("道具店", "shop_generic"), ("家居店", "shop_generic"),
)


def infer_building(location: dict[str, Any]) -> Optional[str]:
    """由地点 kind、ID 与名称推断建筑类型；户外地点返回 None。

    推断顺序：kind 精确映射 → 地点 ID 精确匹配 → 名称关键词。
    （生成器模板中书店/咖啡馆等槽位的地点 kind 是 store/cafe 这类大类，
    需要二级推断才能落到正确的建筑模板。）
    """
    if location["kind"] in ("street", "park", "beach"):
        return None
    by_kind = BUILDING_BY_KIND.get(location["kind"])
    location_id = location["location_id"]
    if by_kind in ("home", "classroom"):
        return by_kind
    if location_id in _EXACT_ID_BUILDINGS:
        return _EXACT_ID_BUILDINGS[location_id]
    name = location.get("name", "")
    for hint, building in _NAME_HINTS:
        if hint in name or hint in location_id:
            return building
    if by_kind == "store":
        return "shop_generic"
    return by_kind


def interior_seed(world_seed: int, location_id: str) -> int:
    """由世界种子与地点 ID 确定性派生建筑种子。"""
    import hashlib

    digest = hashlib.sha256(f"{world_seed}|{location_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _furniture_object_id(location_id: str, prefix: str, index: int) -> str:
    candidate = f"{location_id}_{prefix}{index:02d}"
    # 世界包 ID 只允许字母数字._/-；地点 ID 与前缀均已合规。
    return candidate[:96]


def apply_interiors_to_package(
    package_dir,
    *,
    locations: Optional[list[str]] = None,
    seed_override: Optional[int] = None,
    use_glm: bool = False,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """为一栋或多栋建筑生成室内，并把结果写入世界包（scene.json + objects.json）。

    * 未指定 ``locations`` 时处理全部室内建筑（户外地点跳过）；
    * 生成的家具**替换**该地点原有家具（objects.json 是声明性目录，
      随室内重新生成是"生成室内布局"的既定语义），其他地点的对象不动；
    * 种子默认由世界种子 + 地点 ID 派生（``--seed`` 可覆盖），同种子复现；
    * ``use_glm=True`` 时先请求 GLM 语义设计（失败回退程序化并记录警告），
      用量写入 ``glm-usage.jsonl``；
    * 回写前运行世界包结构校验，失败则不写盘。
    """
    from .editor_bridge import _editor
    from .loader import load
    from .providers import glm
    from .validator import validate

    package = load(package_dir)
    editor = _editor()
    world = package._world
    targets: list[tuple[str, str, int]] = []  # (location_id, building, seed)
    for location in sorted(package.locations.values(), key=lambda item: item["location_id"]):
        if locations is not None and location["location_id"] not in locations:
            continue
        building = infer_building(location)
        if building is None:
            continue
        seed = seed_override if seed_override is not None else interior_seed(world["seed"], location["location_id"])
        targets.append((location["location_id"], building, seed))
    if locations is not None:
        known = set(package.locations)
        unknown = set(locations) - known
        if unknown:
            raise InteriorError(f"地点不存在: {sorted(unknown)}")

    docs = package.snapshot()
    old_scene = package.scene or {
        "schema_version": schema.SCENE_SCHEMA, "spaces": {}, "placements": {}, "assets": {}
    }
    new_scene = {
        "schema_version": schema.SCENE_SCHEMA,
        "spaces": copy.deepcopy(old_scene.get("spaces", {})),
        "placements": copy.deepcopy(old_scene.get("placements", {})),
        "assets": copy.deepcopy(old_scene.get("assets", {})),
        "navigation": copy.deepcopy(old_scene.get("navigation", {})),
    }
    # 待重新生成的地点：先移除其现有家具（objects + placements）。
    removed_by_location: dict[str, list[str]] = {}
    kept_objects = []
    for obj in docs["objects.json"]["objects"]:
        if obj["location_id"] in {loc for loc, _, _ in targets}:
            removed_by_location.setdefault(obj["location_id"], []).append(obj["object_id"])
            new_scene["placements"].pop(obj["object_id"], None)
        else:
            kept_objects.append(obj)

    theme_desc = f"{world['name']}（{world.get('description', '')}）"
    warnings: list[str] = []
    receipts: list[dict[str, Any]] = []
    for location_id, building, seed in targets:
        location = package.location(location_id)
        design: Optional[dict[str, Any]] = None
        design_source = "procedural"
        if use_glm:
            try:
                design, usage = glm.design_interior(
                    building_label=BUILDING_LABELS[building],
                    location_name=location["name"],
                    theme_desc=theme_desc,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                )
                design_source = "glm"
                glm.append_usage_log(
                    package.package_dir, usage, purpose=f"interior:{location_id}"
                )
            except Exception as exc:  # GLM 失败回退程序化，不阻断生成
                warnings.append(
                    f"{location_id}: GLM 语义设计失败，回退程序化布局（{type(exc).__name__}: {exc}）"
                )
        result = generate_interior(
            building=building,
            seed=seed,
            location_id=location_id,
            design=design,
        )
        # 家具 → objects.json + placements；导航归属 → scene.navigation。
        navigation = result["navigation"]
        furniture_map: dict[str, list[str]] = {
            area["area_id"]: [] for area in navigation["areas"]
        }
        for index, item in enumerate(result["furniture"], start=1):
            object_id = _furniture_object_id(location_id, item["prefix"], index)
            kept_objects.append(
                {
                    "object_id": object_id,
                    "name": item["label"],
                    "kind": item["kind"],
                    "location_id": location_id,
                    "note": item["note"],
                }
            )
            new_scene["placements"][object_id] = {
                key: item[key]
                for key in (
                    "kind", "x", "y", "visual", "actions", "behavior",
                    "portable", "capacity", "servings", "initial_status",
                    "interaction", "asset_id",
                )
            }
            furniture_map.setdefault(item["zone"], []).append(object_id)
        navigation["furniture_map"] = furniture_map
        new_scene["navigation"][location_id] = navigation
        new_scene["spaces"][location_id] = result["space"]
        receipts.append(
            {
                "location_id": location_id,
                "location_name": location["name"],
                "building": building,
                "building_label": BUILDING_LABELS[building],
                "seed": seed,
                "design_source": design_source,
                "furniture": len(result["furniture"]),
                "walls": len(result["space"]["walls"]),
                "doors": len(result["space"]["doors"]),
                "passability": result["passability"],
                "design_applied": result.get("design_applied", []),
                "replaced_objects": removed_by_location.get(location_id, []),
                "design_warnings": [w for w in warnings if w.startswith(location_id)],
            }
        )
    docs["objects.json"]["objects"] = kept_objects

    merged = dict(docs)
    merged[schema.SCENE_FILE] = new_scene
    messages = validate(merged)
    if messages:
        raise InteriorError("生成后的世界包未通过结构校验（未写盘）：\n- " + "\n- ".join(messages))

    schema.write_package(package_dir, docs)
    schema.write_json(Path(package_dir) / schema.SCENE_FILE, schema.validate_scene_doc(new_scene))
    schema.write_json(Path(package_dir) / "MANIFEST.json", schema.build_manifest(Path(package_dir)))
    return {
        "status": "written",
        "package": str(package_dir),
        "interiors": receipts,
        "warnings": warnings,
        "scene_locations": sorted(new_scene["spaces"]),
        "scene_placements": len(new_scene["placements"]),
    }


__all__ = [
    "BUILDING_BY_KIND",
    "BUILDING_LABELS",
    "DESIGNABLE_KINDS",
    "InteriorError",
    "apply_interiors_to_package",
    "building_for_kind",
    "generate_interior",
    "infer_building",
    "interior_seed",
]
