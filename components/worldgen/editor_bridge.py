#!/usr/bin/env python3
"""worldgen 城镇包 ↔ 世界编辑器 v3 场景 双向桥接。

## 正向（worldgen → 编辑器）

把 ``worldgen.package.v1`` 城镇转换成 ``nyairo.world-package.v3``：
每个地点 → 一个区域（矩形中心取自地图坐标）、每条道路 → 一条双向区域连接、
营业时间 → ``hours``、``objects.json`` 家居 → 编辑器物件。
若世界包带有 ``scene.json``（编辑器回写产物），则同时恢复室内空间
（墙/门/地面）、物件摆位与图片素材——**已编辑的世界再次进入编辑器，
不会退回空白房间**。

## 反向（编辑器 → worldgen）

``import_from_editor`` 以**原始世界包为基础**，把编辑器导出的 v3 与
"原始包的正向投影（期望视图）"做差量，只把用户实际修改的字段回写：

    原始 WorldGen 包 + 编辑器修改 = 新 WorldGen 包

因此编辑器不管理的信息（NPC 排班、环境事件、content 文本、生成元数据、
seed、时区、weekdays_only 等）**原样保留**；不做名称匹配——编辑器实体 ID
与世界包实体 ID 的对应关系由"正向转换可重推"决定：ID 映射是纯函数，
回写时用原始包重算正向映射再求逆；新建实体直接沿用编辑器 ID
（编辑器 ID 字符集是世界包 ID 字符集的子集，天然稳定）。

边界（重要，均如实执行）：

* 编辑器的 ``editor-runtime.v3`` 是本地测试运行时，与 rc9 World/Body 无关；
* 室内几何（墙/门/地板）作为 ``scene.json`` 随世界包保存，供再次进入编辑器
  使用；**rc9 不消费这些几何，也没有墙体碰撞**；
* 家具的交互用途（休息/阅读/饮水等）属于编辑器本地试运行能力；世界包的
  ``objects.json`` 保持声明性目录，rc9 侧物体动作仍由既有经济/基座机制管理
  （自定义世界当前未接经济，见 v0.1 交付文档）；
* 删除地点时检查全部引用（NPC/环境事件/对象），有引用即拒绝并逐条列出，
  不自动搬迁或删除被引用事实；
* 编辑器没有道路耗时概念：新建连接的耗时按世界包地图几何确定性地推导，
  既有耗时在编辑器中不可修改（调整耗时请直接编辑世界包）；
* 编辑器的跨夜营业时段（如 22–6）无法用世界包规则表达，回写时明确拒绝。
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from . import schema
from .loader import WorldPackage, load

_EDITOR_DIR = Path(__file__).resolve().parents[1] / "world-editor"

#: 编辑器画布 1000×620；worldgen 地图 1000×700，纵向等比缩放。
EDITOR_CANVAS_WIDTH = 1000
EDITOR_CANVAS_HEIGHT = 620
SOURCE_MAP_HEIGHT = 700
REGION_WIDTH = 170
REGION_HEIGHT = 110

#: 新建连接的步行耗时推导（与 generator 同参数：地图单位 → 秒）。
WALK_SECONDS_PER_UNIT = 0.30
WALK_SECONDS_BASE = 90
WALK_SECONDS_MIN = 60
WALK_SECONDS_MAX = 900

#: 我们的地点 kind → 编辑器（kind, 主题）
_KIND_MAP = {
    "home": ("room", "bedroom"),
    "school": ("room", "bookstore"),
    "library": ("room", "bookstore"),
    "store": ("shop", "cafe"),
    "cafe": ("shop", "cafe"),
    "market": ("shop", "cafe"),
    "street": ("outdoor", "street"),
    "park": ("outdoor", "street"),
    "beach": ("outdoor", "street"),
    "shrine": ("outdoor", "street"),
    "station": ("outdoor", "street"),
    "public": ("outdoor", "street"),
}

#: 编辑器区域 kind → 新建世界包地点的 kind 与默认活动
_EDITOR_KIND_MAP = {
    "room": ("public", (("REST", "在这里休息"), ("READ", "在这里阅读"))),
    "shop": ("store", (("BROWSE_STORE", "在这里看看"),)),
    "outdoor": ("park", (("WALK", "在这里散步"), ("LOOK_AROUND", "在这里看看周围"))),
}

#: 编辑器物件 kind → 世界包 objects.json 的 kind（声明性目录）
_EDITOR_OBJECT_KIND_MAP = {
    "bed": "bed",
    "book": "book",
    "cabinet": "cabinet",
    "chair": "chair",
    "cup": "cup",
    "dispenser": "dispenser",
    "lamp": "lamp",
    "plant": "plant",
    "table": "table",
}

#: 家具在区域内的固定落点（百分比整数，编辑器要求 5–95 / 15–90）
_OBJECT_SPOTS = [(25, 35), (70, 30), (70, 70), (25, 70), (50, 50), (15, 55), (85, 55)]

#: 世界包对象 → 编辑器物件的默认映射（按 object_id 前缀，其次名称关键词）
_OBJECT_MAP = {
    "bed": ("bed", ["rest"], {"rest": {"minutes": 30, "energy_gain": 15}}, "neutral"),
    "desk": ("table", [], {}, "neutral"),
    "table": ("table", [], {}, "neutral"),
    "shelf": ("cabinet", ["store", "retrieve"], {}, "neutral"),
    "cabinet": ("cabinet", ["store", "retrieve"], {}, "neutral"),
    "lamp": ("lamp", ["toggle"], {"toggle": {"minutes": 1}}, "off"),
}
_OBJECT_NAME_HINTS = (
    ("床", "bed"), ("桌", "table"), ("柜", "cabinet"), ("架", "cabinet"), ("灯", "lamp"),
)


class EditorBridgeError(ValueError):
    """worldgen 包与编辑器 v3 之间的转换失败。所有问题都以明确中文报告。"""


_editor_model = None


def _editor():
    """加载编辑器的 model.py（含其内部对 legacy/space/resources 的依赖）。"""
    global _editor_model
    if _editor_model is not None:
        return _editor_model
    if not _EDITOR_DIR.is_dir():
        raise EditorBridgeError(f"未找到世界编辑器组件: {_EDITOR_DIR}")
    if str(_EDITOR_DIR) not in sys.path:
        sys.path.insert(0, str(_EDITOR_DIR))
    spec = importlib.util.spec_from_file_location("nyairo_world_editor_model", _EDITOR_DIR / "model.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["nyairo_world_editor_model"] = module
    spec.loader.exec_module(module)
    _editor_model = module
    return _editor_model


def _sanitize_id(value: str, label: str, *, seen: Optional[set[str]] = None) -> str:
    """我们的 ID 允许点/斜杠；编辑器只允许字母数字下划线连字符（≤64）。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value)[:64].strip("_")
    if not cleaned:
        raise EditorBridgeError(f"{label} 无法转换为编辑器 ID")
    if seen is not None and cleaned in seen:
        index = 2
        while f"{cleaned[:58]}_{index}" in seen:
            index += 1
        cleaned = f"{cleaned[:58]}_{index}"
    if seen is not None:
        seen.add(cleaned)
    return cleaned


def _region_rect(x: float, y: float) -> dict[str, int]:
    scaled_y = y * EDITOR_CANVAS_HEIGHT / SOURCE_MAP_HEIGHT
    left = min(max(0, int(round(x)) - REGION_WIDTH // 2), EDITOR_CANVAS_WIDTH - REGION_WIDTH)
    top = min(max(0, int(round(scaled_y)) - REGION_HEIGHT // 2), EDITOR_CANVAS_HEIGHT - REGION_HEIGHT)
    return {"x": left, "y": top, "w": REGION_WIDTH, "h": REGION_HEIGHT}


def _rect_center_map(rect: Mapping[str, Any]) -> dict[str, float]:
    """编辑器区域矩形 → 世界包地图坐标（逆缩放）。"""
    return {
        "x": round(rect["x"] + rect["w"] / 2, 1),
        "y": round((rect["y"] + rect["h"] / 2) * SOURCE_MAP_HEIGHT / EDITOR_CANVAS_HEIGHT, 1),
    }


def _walk_seconds(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    dx = a["map"]["x"] - b["map"]["x"]
    dy = a["map"]["y"] - b["map"]["y"]
    seconds = int(WALK_SECONDS_BASE + (dx * dx + dy * dy) ** 0.5 * WALK_SECONDS_PER_UNIT)
    return max(WALK_SECONDS_MIN, min(WALK_SECONDS_MAX, seconds))


def _object_default_fields(obj: dict[str, Any]) -> dict[str, Any]:
    prefix = obj["object_id"].split("_", 1)[0]
    if prefix in _OBJECT_MAP:
        kind, actions, behavior, status = _OBJECT_MAP[prefix]
    else:
        kind, actions, behavior, status = ("table", [], {}, "neutral")
        for hint, key in _OBJECT_NAME_HINTS:
            if hint in obj["name"]:
                kind, actions, behavior, status = _OBJECT_MAP[key]
                break
    spot = _OBJECT_SPOTS[len(obj["object_id"]) % len(_OBJECT_SPOTS)]
    return {
        "kind": kind,
        "x": spot[0],
        "y": spot[1],
        "actions": list(actions),
        "behavior": {name: dict(params) for name, params in behavior.items()},
        "portable": False,
        "capacity": 4 if kind == "cabinet" else 0,
        "servings": 0,
        "visual": {"scale": 100, "rotation": 0, "layer": 0, "show_name": True},
        "initial_status": status,
        "interaction": {"near": False, "radius": 2},
        "asset_id": None,
    }


#: 摆位/差量比较涉及的编辑器物件字段
_PLACEMENT_KEYS = (
    "kind", "x", "y", "visual", "actions", "behavior",
    "portable", "capacity", "servings", "initial_status",
    "interaction", "asset_id",
)


def location_id_map(package: WorldPackage) -> dict[str, str]:
    """编辑器区域 ID → 世界包地点 ID（与 to_editor_world 同一确定性映射）。"""
    result: dict[str, str] = {}
    seen: set[str] = set()
    for location_id in sorted(package.locations):
        editor_id = _sanitize_id(location_id, f"地点 {location_id}", seen=seen)
        result[editor_id] = location_id
    return result


def object_id_map(package: WorldPackage) -> dict[str, str]:
    """编辑器物件 ID → 世界包对象 ID（与 to_editor_world 同一确定性映射）。"""
    result: dict[str, str] = {}
    seen: set[str] = set()
    for object_id in sorted(obj["object_id"] for obj in package.objects):
        editor_id = _sanitize_id(object_id, f"对象 {object_id}", seen=seen)
        result[editor_id] = object_id
    return result


def _hours_of(package: WorldPackage, location_id: str) -> Optional[dict[str, int]]:
    rule = package.location_rule(location_id) or {}
    if rule.get("open_hour") is None:
        return None
    return {"open": rule["open_hour"], "close": rule["close_hour"]}


def to_editor_world(
    package: WorldPackage,
    *,
    initial_time: str = "2026-10-14T08:00",
    resident_name: str = "试住居民",
) -> dict[str, Any]:
    """转换世界包为编辑器 v3 场景（已通过编辑器自身校验）。

    世界包带 scene.json 时，室内空间、物件摆位与被引用素材一并恢复。
    """
    editor = _editor()

    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", initial_time):
        raise EditorBridgeError(f"initial_time 需要 YYYY-MM-DDTHH:MM 格式，实际 {initial_time!r}")

    scene = package.scene or {"spaces": {}, "placements": {}, "assets": {}}
    scene_spaces = scene.get("spaces", {})
    scene_placements = scene.get("placements", {})

    rooms = []
    id_by_location: dict[str, str] = {}
    used_region_ids: set[str] = set()
    for location in sorted(package.locations.values(), key=lambda item: item["location_id"]):
        editor_id = _sanitize_id(
            location["location_id"], f"地点 {location['location_id']}", seen=used_region_ids
        )
        id_by_location[location["location_id"]] = editor_id
        kind, theme = _KIND_MAP.get(location["kind"], ("outdoor", "street"))
        content = package.content_entry(location["location_id"])
        stored_space = scene_spaces.get(location["location_id"])
        space = copy.deepcopy(stored_space) if stored_space else editor.empty_space()
        visual = editor.legacy.THEMES[theme]
        rooms.append(
            {
                "id": editor_id,
                "name": location["name"],
                "description": str(content.get("intro") or location["description"]),
                "kind": kind,
                **_region_rect(location["map"]["x"], location["map"]["y"]),
                "hours": _hours_of(package, location["location_id"]),
                "visual": {
                    "theme": theme,
                    "floor": visual["floor"],
                    "border": visual["border"],
                    "background": visual["background"],
                    "texture": visual["texture"],
                    "border_style": visual["border_style"],
                },
                "space": space,
            }
        )

    connections = [
        {"from": id_by_location[edge["from"]], "to": id_by_location[edge["to"]]}
        for edge in package._topology["edges"]
    ]

    objects = []
    used_object_ids: set[str] = set()
    for obj in sorted(package.objects, key=lambda item: item["object_id"]):
        editor_id = _sanitize_id(
            obj["object_id"], f"对象 {obj['object_id']}", seen=used_object_ids
        )
        room_id = id_by_location.get(obj["location_id"]) or id_by_location.get("home")
        if room_id is None:
            continue
        fields = _object_default_fields(obj)
        placement = scene_placements.get(obj["object_id"])
        if placement is not None:
            for key in _PLACEMENT_KEYS:
                if key in placement:
                    value = placement[key]
                    fields[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
        objects.append(
            {
                "id": editor_id,
                "name": obj["name"],
                "description": obj["note"],
                "room_id": room_id,
                **fields,
            }
        )

    world = package.world_id
    editor_world = {
        "format": editor.FORMAT,
        "requires": editor.ENGINE,
        "meta": {
            "id": _sanitize_id(world, f"world_id {world}"),
            "name": package.display_name,
            "author": "NyAiro WorldGen",
            "version": "0.1.0",
            "description": (
                f"{package._world['description']} "
                f"（由 worldgen 城镇包转换：seed {package.seed}，主题 {package.theme}。"
                "室内空间与物件交互需在编辑器中手动绘制与配置。）"
            ),
            "license": "CC0-1.0",
            "cover": "generated-floorplan",
        },
        "scene": {"background": "#f4f6f4", "grid": True, "decorations": True},
        "rooms": rooms,
        "connections": connections,
        "objects": objects,
        "initial": {
            "room_id": id_by_location["home"],
            "time": initial_time,
            "resident_name": resident_name,
        },
        "assets": copy.deepcopy(scene.get("assets", {})),
    }

    try:
        validated, warnings = editor.validate_package(editor_world)
    except editor.legacy.WorldError as exc:
        raise EditorBridgeError(f"编辑器校验拒绝转换结果：{exc}") from exc
    if warnings:
        # 校验警告（如个别区域不可达）随收据返回，不静默丢弃。
        validated["_bridge_warnings"] = warnings
    return validated


def write_editor_world(path: Path | str, editor_world: dict[str, Any]) -> Path:
    payload = json.dumps(editor_world, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return target


def export_for_editor(
    package_dir: Path | str,
    output_path: Optional[Path | str] = None,
    *,
    initial_time: str = "2026-10-14T08:00",
    resident_name: str = "试住居民",
) -> dict[str, Any]:
    """完整导出流程：加载世界包 → 转换 → 写盘 → 返回收据。"""
    package = load(package_dir)
    editor_world = to_editor_world(
        package, initial_time=initial_time, resident_name=resident_name
    )
    warnings = editor_world.pop("_bridge_warnings", [])
    out = Path(output_path) if output_path else package.package_dir / "editor-world.json"
    write_editor_world(out, editor_world)
    return {
        "status": "converted",
        "world_id": package.world_id,
        "editor_format": editor_world["format"],
        "editor_runtime": editor_world["requires"],
        "regions": len(editor_world["rooms"]),
        "connections": len(editor_world["connections"]),
        "objects": len(editor_world["objects"]),
        "scene_restored": bool(package.scene),
        "warnings": warnings,
        "output": str(out),
        "next_step": (
            "在 components/world-editor 下运行 python server.py，浏览器中"
            "「导入世界包」选择该 JSON；修改后用「导出 NyAiro 世界包」或"
            " CLI editor-import 回写为新的世界包。"
        ),
    }


# ---------------------------------------------------------------------------
# 反向：编辑器 v3 → worldgen 世界包（期望视图差量回写）
# ---------------------------------------------------------------------------


def _load_edited(edited: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    """读取并校验编辑器导出的 v3 数据（接受路径或已解析的 dict）。"""
    editor = _editor()
    if isinstance(edited, Mapping):
        raw = copy.deepcopy(dict(edited))
    else:
        raw = json.loads(Path(edited).read_text(encoding="utf-8"))
    try:
        validated, _warnings = editor.validate_package(raw)
    except editor.legacy.WorldError as exc:
        raise EditorBridgeError(f"编辑器 v3 数据无效：{exc}") from exc
    if validated.get("format") != editor.FORMAT:
        raise EditorBridgeError(
            "编辑器数据不是 v3（可能是旧格式升级结果）；请用编辑器 v0.4 重新导出"
        )
    return validated


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _referencing_entities(
    docs: Mapping[str, Any], location_ids: set[str]
) -> list[str]:
    """列出引用了将被删除地点的世界事实（拒绝删除时逐条报告）。"""
    conflicts: list[str] = []
    for npc in docs["npcs.json"]["npcs"]:
        if npc["home_base_area"] in location_ids:
            conflicts.append(
                f"NPC {npc['npc_id']}（{npc['display_name']}）的工作地点是 {npc['home_base_area']}"
            )
        for window in npc["windows"]:
            if window["location_id"] in location_ids:
                conflicts.append(
                    f"NPC {npc['npc_id']}（{npc['display_name']}）的出场时段在 {window['location_id']}"
                )
    for template in docs["ambient.json"]["templates"]:
        if template["location_id"] in location_ids:
            conflicts.append(
                f"环境事件 {template['event_type']} 绑定在 {template['location_id']}"
            )
    for obj in docs["objects.json"]["objects"]:
        if obj["location_id"] in location_ids:
            conflicts.append(
                f"对象 {obj['object_id']}（{obj['name']}）位于 {obj['location_id']}"
            )
    return conflicts


def import_from_editor(
    original_dir: Path | str,
    edited: Path | str | Mapping[str, Any],
    *,
    out_dir: Optional[Path | str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """以原始世界包为基础，把编辑器修改回写为一个新的完整世界包。

    原始世界包不被覆盖。数据语义：**原始包 + 编辑器实际修改 = 新包**；
    与正向投影相同的字段视为未修改，原样保留原始值。
    """
    package = load(original_dir)
    editor = _editor()
    editor_view = to_editor_world(package)
    editor_view.pop("_bridge_warnings", None)
    edited = _load_edited(edited)

    forward_location = location_id_map(package)
    forward_object = object_id_map(package)
    expected_meta_id = _sanitize_id(package.world_id, "world_id")
    if edited["meta"]["id"] != expected_meta_id:
        raise EditorBridgeError(
            f"当前编辑器草稿（meta.id={edited['meta']['id']!r}）不是世界包 "
            f"{package.world_id!r} 的转换结果；请确认导入的是对应世界"
        )

    docs = package.snapshot()
    old_scene = package.scene or {
        "schema_version": schema.SCENE_SCHEMA, "spaces": {}, "placements": {}, "assets": {}
    }
    new_scene: dict[str, Any] = {
        "schema_version": schema.SCENE_SCHEMA,
        "spaces": copy.deepcopy(old_scene.get("spaces", {})),
        "placements": copy.deepcopy(old_scene.get("placements", {})),
        "assets": copy.deepcopy(old_scene.get("assets", {})),
    }

    locations_by_id = {
        loc["location_id"]: loc for loc in docs["topology.json"]["locations"]
    }
    rules = docs["rules.json"]["location_rules"]
    contents = docs["content.json"]["entries"]
    receipt: dict[str, Any] = {
        "status": "merged",
        "world_id": package.world_id,
        "original_package": str(package.package_dir),
        "updated_locations": {},
        "added_locations": [],
        "removed_locations": [],
        "added_connections": [],
        "removed_connections": [],
        "added_objects": [],
        "removed_objects": [],
        "updated_objects": {},
        "warnings": [],
    }

    expected_rooms = {room["id"]: room for room in editor_view["rooms"]}
    actual_rooms = {room["id"]: room for room in edited["rooms"]}

    # ---- 删除的地点：先检查引用 -------------------------------------------
    removed_editor_ids = sorted(set(expected_rooms) - set(actual_rooms))
    removed_location_ids = {forward_location[editor_id] for editor_id in removed_editor_ids}
    if removed_location_ids:
        conflicts = _referencing_entities(docs, removed_location_ids)
        if conflicts:
            raise EditorBridgeError(
                "以下地点被删除，但仍被世界事实引用；请先在编辑器中恢复这些地点，"
                "或直接编辑世界包处理引用（不会自动搬迁 NPC 或对象）：\n- "
                + "\n- ".join(conflicts)
            )

    # ---- 地点字段差量 -------------------------------------------------------
    for editor_id, actual_room in sorted(actual_rooms.items()):
        location_id = forward_location.get(editor_id)
        if location_id is None:
            continue  # 新建地点，稍后统一处理
        location = locations_by_id[location_id]
        expected_room = expected_rooms[editor_id]
        updates: dict[str, Any] = {}
        if actual_room["name"] != expected_room["name"]:
            location["name"] = actual_room["name"]
            updates["name"] = actual_room["name"]
        if actual_room["description"] != expected_room["description"]:
            location["description"] = actual_room["description"]
            contents.setdefault(location_id, {})["intro"] = actual_room["description"]
            updates["description"] = actual_room["description"]
        if actual_room["hours"] != expected_room["hours"]:
            hours = actual_room["hours"]
            if hours is not None and hours["close"] <= hours["open"]:
                raise EditorBridgeError(
                    f"地点 {location_id} 的编辑器营业时间（{hours['open']}–{hours['close']}）"
                    "跨夜或为空；世界包规则无法表达跨夜时段，请调整为当日时段"
                )
            rule = rules[location_id]
            rule["open_hour"] = hours["open"] if hours else None
            rule["close_hour"] = hours["close"] if hours else None
            updates["hours"] = hours
        if (
            actual_room["x"] != expected_room["x"]
            or actual_room["y"] != expected_room["y"]
            or actual_room["w"] != expected_room["w"]
            or actual_room["h"] != expected_room["h"]
        ):
            location["map"] = _rect_center_map(actual_room)
            updates["map"] = dict(location["map"])
        if actual_room["space"] != expected_room["space"]:
            new_scene["spaces"][location_id] = _plain(actual_room["space"])
        if updates:
            receipt["updated_locations"][location_id] = updates

    # ---- 新建地点 -----------------------------------------------------------
    for editor_id in sorted(set(actual_rooms) - set(expected_rooms)):
        actual_room = actual_rooms[editor_id]
        if not schema.ID_RE.fullmatch(editor_id):
            raise EditorBridgeError(
                f"编辑器区域 ID {editor_id!r} 不能直接作为世界包地点 ID"
                "（世界包要求字母数字开头，且只含字母数字._/-）；请重命名该区域"
            )
        location_id = editor_id
        kind, affordances = _EDITOR_KIND_MAP[actual_room["kind"]]
        locations_by_id[location_id] = {
            "location_id": location_id,
            "name": actual_room["name"],
            "kind": kind,
            "area_id": location_id,
            "description": actual_room["description"],
            "reachable": [],
            "map": _rect_center_map(actual_room),
            "zone_id": "editor_added",
            "rooms": [],
        }
        docs["topology.json"]["locations"].append(copy.deepcopy(locations_by_id[location_id]))
        rules[location_id] = {
            "open_hour": actual_room["hours"]["open"] if actual_room["hours"] else None,
            "close_hour": actual_room["hours"]["close"] if actual_room["hours"] else None,
            "weekdays_only": False,
            "affordances": [
                {"affordance_id": item[0], "description": item[1]} for item in affordances
            ],
        }
        contents[location_id] = {"intro": actual_room["description"]}
        if actual_room["space"] != editor.empty_space():
            new_scene["spaces"][location_id] = _plain(actual_room["space"])
        forward_location[editor_id] = location_id
        receipt["added_locations"].append(location_id)

    # ---- 删除地点：移除本体与规则/内容/场景 --------------------------------
    for location_id in sorted(removed_location_ids):
        docs["topology.json"]["locations"] = [
            loc for loc in docs["topology.json"]["locations"]
            if loc["location_id"] != location_id
        ]
        locations_by_id.pop(location_id, None)
        rules.pop(location_id, None)
        contents.pop(location_id, None)
        new_scene["spaces"].pop(location_id, None)
        receipt["removed_locations"].append(location_id)

    # ---- 连接差量 -----------------------------------------------------------
    def pair_key(connection: Mapping[str, str]) -> frozenset:
        return frozenset((connection["from"], connection["to"]))

    expected_pairs = {pair_key(c) for c in editor_view["connections"]}
    actual_pairs = {pair_key(c) for c in edited["connections"]}
    for pair in sorted(actual_pairs - expected_pairs, key=sorted):
        first, second = sorted(pair)
        location_first = forward_location[first]
        location_second = forward_location[second]
        docs["topology.json"]["edges"].append(
            {
                "from": location_first,
                "to": location_second,
                "walk_seconds": _walk_seconds(
                    locations_by_id[location_first], locations_by_id[location_second]
                ),
            }
        )
        receipt["added_connections"].append(f"{location_first}<->{location_second}")
    for pair in sorted(expected_pairs - actual_pairs, key=sorted):
        first, second = sorted(pair)
        location_first = forward_location[first]
        location_second = forward_location[second]
        docs["topology.json"]["edges"] = [
            edge for edge in docs["topology.json"]["edges"]
            if {edge["from"], edge["to"]} != {location_first, location_second}
        ]
        receipt["removed_connections"].append(f"{location_first}<->{location_second}")

    # ---- reachable 由最终边集重建（保持与道路一致） -------------------------
    adjacency: dict[str, set[str]] = {loc_id: set() for loc_id in locations_by_id}
    for edge in docs["topology.json"]["edges"]:
        adjacency[edge["from"]].add(edge["to"])
        adjacency[edge["to"]].add(edge["from"])
    for location in docs["topology.json"]["locations"]:
        location["reachable"] = sorted(adjacency[location["location_id"]])

    # ---- 对象差量 -----------------------------------------------------------
    expected_objects = {obj["id"]: obj for obj in editor_view["objects"]}
    actual_objects = {obj["id"]: obj for obj in edited["objects"]}
    world_objects = {
        obj["object_id"]: obj for obj in docs["objects.json"]["objects"]
    }
    used_asset_ids: set[str] = set()

    for editor_id in sorted(set(actual_objects) - set(expected_objects)):
        actual_obj = actual_objects[editor_id]
        if not schema.ID_RE.fullmatch(editor_id):
            raise EditorBridgeError(
                f"编辑器物件 ID {editor_id!r} 不能直接作为世界包对象 ID；请重命名"
            )
        location_id = forward_location[actual_obj["room_id"]]
        world_objects[editor_id] = {
            "object_id": editor_id,
            "name": actual_obj["name"],
            "kind": _EDITOR_OBJECT_KIND_MAP.get(actual_obj["kind"], actual_obj["kind"]),
            "location_id": location_id,
            "note": actual_obj["description"],
        }
        docs["objects.json"]["objects"].append(copy.deepcopy(world_objects[editor_id]))
        new_scene["placements"][editor_id] = _plain(
            {key: actual_obj[key] for key in _PLACEMENT_KEYS}
        )
        if actual_obj["asset_id"]:
            used_asset_ids.add(actual_obj["asset_id"])
        receipt["added_objects"].append(editor_id)

    for editor_id, actual_obj in sorted(actual_objects.items()):
        if editor_id not in expected_objects:
            continue
        expected_obj = expected_objects[editor_id]
        object_id = forward_object[editor_id]
        world_obj = world_objects[object_id]
        updates: dict[str, Any] = {}
        if actual_obj["name"] != expected_obj["name"]:
            world_obj["name"] = actual_obj["name"]
            updates["name"] = actual_obj["name"]
        if actual_obj["description"] != expected_obj["description"]:
            world_obj["note"] = actual_obj["description"]
            updates["note"] = actual_obj["description"]
        if actual_obj["room_id"] != expected_obj["room_id"]:
            world_obj["location_id"] = forward_location[actual_obj["room_id"]]
            updates["location_id"] = world_obj["location_id"]
        if any(actual_obj[key] != expected_obj[key] for key in _PLACEMENT_KEYS):
            new_scene["placements"][editor_id] = _plain(
                {key: actual_obj[key] for key in _PLACEMENT_KEYS}
            )
            updates["placement"] = True
        if actual_obj["asset_id"]:
            used_asset_ids.add(actual_obj["asset_id"])
        if updates:
            receipt["updated_objects"][object_id] = updates

    for editor_id in sorted(set(expected_objects) - set(actual_objects)):
        object_id = forward_object[editor_id]
        docs["objects.json"]["objects"] = [
            obj for obj in docs["objects.json"]["objects"]
            if obj["object_id"] != object_id
        ]
        world_objects.pop(object_id, None)
        new_scene["placements"].pop(editor_id, None)
        receipt["removed_objects"].append(object_id)

    # ---- 场景素材：仅保留被室内/摆位引用的编辑器图片 ------------------------
    for space in new_scene["spaces"].values():
        for floor in space.get("floors", []):
            if floor.get("asset_id"):
                used_asset_ids.add(floor["asset_id"])
    for asset_id in sorted(used_asset_ids):
        if asset_id in edited["assets"]:
            new_scene["assets"][asset_id] = _plain(edited["assets"][asset_id])
    for asset_id in list(new_scene["assets"]):
        if asset_id not in used_asset_ids:
            new_scene["assets"].pop(asset_id)

    editor_id_by_object = {value: key for key, value in forward_object.items()}
    receipt["editor_only_content"] = {
        "interior_spaces": len(new_scene["spaces"]),
        "object_placements": len(new_scene["placements"]),
        "custom_assets": len(new_scene["assets"]),
        "furniture_with_actions": sum(
            1
            for obj in docs["objects.json"]["objects"]
            if editor_id_by_object.get(obj["object_id"]) in actual_objects
            and actual_objects[editor_id_by_object[obj["object_id"]]]["actions"]
        ),
        "note": "室内几何与家具交互属于编辑器本地能力，rc9 不消费（详见交付文档）",
    }
    receipt["scene_locations"] = sorted(new_scene["spaces"])
    receipt["scene_placements"] = sorted(new_scene["placements"])

    # ---- 场景引用一致性（写盘前） ------------------------------------------
    object_ids = {obj["object_id"] for obj in docs["objects.json"]["objects"]}
    missing_space = sorted(set(new_scene["spaces"]) - set(locations_by_id))
    missing_placement = sorted(set(new_scene["placements"]) - object_ids)
    if missing_space or missing_placement:
        raise EditorBridgeError(
            f"场景数据引用不一致：地点 {missing_space}，对象 {missing_placement}"
        )

    # ---- 世界包结构校验与写盘 ----------------------------------------------
    from .validator import validate

    merged = dict(docs)
    merged[schema.SCENE_FILE] = new_scene
    messages = validate(merged)
    if messages:
        raise EditorBridgeError(
            "回写后的世界包未通过结构校验（原始世界包未被修改）：\n- "
            + "\n- ".join(messages)
        )

    if out_dir is None:
        base = Path(original_dir)
        out_dir = base.parent / (base.name + "-edited")
        suffix = 2
        while Path(out_dir).exists():
            out_dir = base.parent / f"{base.name}-edited-{suffix}"
            suffix += 1
    out_path = Path(out_dir)
    if out_path.resolve() == package.package_dir:
        raise EditorBridgeError("输出目录不能与原始世界包相同")
    if out_path.exists() and not force:
        raise EditorBridgeError(
            f"输出目录已存在: {out_path}（用 --force 覆盖，或换一个目录）"
        )
    if out_path.exists() and force:
        import shutil

        shutil.rmtree(out_path)
    schema.write_package(out_path, docs)
    has_scene_content = bool(
        new_scene["spaces"] or new_scene["placements"] or new_scene["assets"]
    )
    if has_scene_content or package.scene is not None:
        schema.write_json(out_path / schema.SCENE_FILE, schema.validate_scene_doc(new_scene))
        schema.write_json(out_path / "MANIFEST.json", schema.build_manifest(out_path))

    receipt["status"] = "written"
    receipt["output"] = str(out_path.resolve())
    receipt["validation"] = "passed"
    receipt["rc9_loadable"] = True
    receipt["summary"] = schema.package_summary(schema.load_package(out_path, verify=True))
    return receipt


__all__ = [
    "EditorBridgeError",
    "export_for_editor",
    "import_from_editor",
    "location_id_map",
    "object_id_map",
    "to_editor_world",
    "write_editor_world",
]
