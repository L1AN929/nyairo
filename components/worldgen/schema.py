#!/usr/bin/env python3
"""世界包（World Package）格式定义与读写。

世界包是一个目录，包含若干 JSON 文件，静态描述一座小镇：

* ``world.json``     世界信息（world_id、名称、种子、时区、主题、生成器版本）
* ``topology.json``  空间信息（地点、连接、步行耗时、地图坐标、区域）
* ``rules.json``     地点规则（营业时间、允许活动）与环境候选（天气）
* ``npcs.json``      公共角色（公开身份与出场时间窗，不含私人事实）
* ``ambient.json``   环境事件模板（确定性每日环境事实）
* ``objects.json``   初始对象目录（声明性的家具/物品种类）
* ``content.json``   文本内容（本地模板或 GLM 生成，生成后固化）
* ``MANIFEST.json``  各文件 SHA256 清单

世界包是静态或相对稳定的世界定义。角色当前位置、持有物、已发生事件等
运行时状态由既有 World Runtime 管理，不属于世界包。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from . import GENERATOR_VERSION, PACKAGE_SCHEMA

WORLD_SCHEMA = "worldgen.world.v1"
TOPOLOGY_SCHEMA = "worldgen.topology.v1"
RULES_SCHEMA = "worldgen.rules.v1"
NPCS_SCHEMA = "worldgen.npcs.v1"
AMBIENT_SCHEMA = "worldgen.ambient.v1"
#: v2 显式版本：条目必须带 kind（single_day / finite），有限跨天事件带固定
#: 身份、明确起止与世界日期阶段。旧读端只认 v1，遇到 v2 明确拒绝。
AMBIENT_SCHEMA_V2 = "worldgen.ambient.v2"
MAX_AMBIENT_TEMPLATES_V1 = 16
MAX_AMBIENT_TEMPLATES_V2 = 24
OBJECTS_SCHEMA = "worldgen.objects.v1"
CONTENT_SCHEMA = "worldgen.content.v1"
SCENE_SCHEMA = "worldgen.scene.v1"
MANIFEST_SCHEMA = "worldgen.manifest.v1"

PACKAGE_FILES = (
    "world.json",
    "topology.json",
    "rules.json",
    "npcs.json",
    "ambient.json",
    "objects.json",
    "content.json",
)

#: 可选场景文件：编辑器产出的室内空间 / 物件摆位 / 图片素材。
#: 世界包可以没有它（纯程序生成）；一旦存在就纳入 MANIFEST 覆盖。
SCENE_FILE = "scene.json"

#: 地点/世界 ID 约束：与 world_travel_delivery._ID_RE 保持一致，
#: 保证生成的 ID 能直接通过运行时校验。
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$")

#: rc9 骨架对 location_id 的边界（_bounded_text，最长 96、无空白）。
MAX_LOCATION_ID_LENGTH = 96

VALID_LOCATION_KINDS = (
    "home",
    "street",
    "school",
    "store",
    "park",
    "beach",
    "cafe",
    "library",
    "shrine",
    "market",
    "station",
    "public",
)

VALID_TIMEZONES_HINT = "IANA 时区名，例如 Asia/Tokyo"


class WorldPackageError(ValueError):
    """世界包格式错误。所有格式问题都以明确的中文消息报告。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorldPackageError(f"无法读取 {path.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WorldPackageError(f"{path.name} 不是有效的 JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorldPackageError(f"{path.name} 的顶层必须是 JSON 对象")
    return raw


def _require_text(value: Any, field: str, *, maximum: int = 240, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise WorldPackageError(f"{field} 必须是字符串")
    text = value.strip()
    if not text and not allow_empty:
        raise WorldPackageError(f"{field} 不能为空")
    if len(text) > maximum:
        raise WorldPackageError(f"{field} 超过最大长度 {maximum}")
    return text


def _require_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorldPackageError(f"{field} 必须是整数")
    if not minimum <= value <= maximum:
        raise WorldPackageError(f"{field} 必须在 {minimum}..{maximum} 之间")
    return value


def _require_optional_int(
    value: Any, field: str, *, minimum: int, maximum: int
) -> Optional[int]:
    if value is None:
        return None
    return _require_int(value, field, minimum=minimum, maximum=maximum)


def validate_id(value: Any, field: str, *, maximum: int = 64) -> str:
    text = _require_text(value, field, maximum=maximum)
    if not ID_RE.fullmatch(text):
        raise WorldPackageError(
            f"{field} {text!r} 不符合 ID 规则（字母数字开头，"
            "只允许字母、数字、点、下划线、斜杠、连字符）"
        )
    return text


# ---------------------------------------------------------------------------
# 分文件校验
# ---------------------------------------------------------------------------


def validate_world_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise WorldPackageError("world.json 顶层必须是 JSON 对象")
    required = {
        "schema_version",
        "world_id",
        "name",
        "theme",
        "seed",
        "timezone",
        "generator_version",
        "description",
    }
    missing = required - set(doc)
    if missing:
        raise WorldPackageError(f"world.json 缺少字段: {sorted(missing)}")
    if doc["schema_version"] != WORLD_SCHEMA:
        raise WorldPackageError(
            f"world.json schema_version 必须是 {WORLD_SCHEMA}，"
            f"实际是 {doc['schema_version']!r}"
        )
    world_id = validate_id(doc["world_id"], "world_id")
    name = _require_text(doc["name"], "name", maximum=80)
    theme = validate_id(doc["theme"], "theme")
    seed = _require_int(doc["seed"], "seed", minimum=0, maximum=2**31 - 1)
    timezone_name = _require_text(doc["timezone"], "timezone", maximum=64)
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        def _database_available() -> bool:
            try:
                ZoneInfo("UTC")
                return True
            except (ZoneInfoNotFoundError, ValueError):
                return False

        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if _database_available():
                raise WorldPackageError(
                    f"timezone {timezone_name!r} 不是有效 IANA 时区"
                ) from None
            # 本平台没有任何时区数据库（例如未装 tzdata 的 Windows）：
            # 降级为 IANA 名称格式校验，交由有 tz 数据库的运行环境最终把关。
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+\-]*/[A-Za-z0-9_+\-/]+", timezone_name):
                raise WorldPackageError(
                    f"timezone {timezone_name!r} 不是有效 IANA 时区名"
                ) from None
        except ValueError as exc:
            raise WorldPackageError(f"timezone {timezone_name!r} 不是有效 IANA 时区") from exc
    except ImportError:  # pragma: no cover - 极老环境
        pass
    if doc["generator_version"] != GENERATOR_VERSION:
        raise WorldPackageError(
            f"world.json 的 generator_version 是 {doc['generator_version']!r}，"
            f"当前生成器是 {GENERATOR_VERSION}；版本不匹配，拒绝静默替换世界"
        )
    _require_text(doc["description"], "description", maximum=400)
    return {
        "schema_version": doc["schema_version"],
        "world_id": world_id,
        "name": name,
        "theme": theme,
        "seed": seed,
        "timezone": timezone_name,
        "generator_version": doc["generator_version"],
        "description": doc["description"].strip(),
    }


def validate_location(entry: Any, index: int) -> dict[str, Any]:
    where = f"topology.locations[{index}]"
    if not isinstance(entry, dict):
        raise WorldPackageError(f"{where} 必须是 JSON 对象")
    required = {"location_id", "name", "kind", "area_id", "description", "reachable", "map", "zone_id"}
    missing = required - set(entry)
    if missing:
        raise WorldPackageError(f"{where} 缺少字段: {sorted(missing)}")
    location_id = validate_id(entry["location_id"], f"{where}.location_id", maximum=MAX_LOCATION_ID_LENGTH)
    name = _require_text(entry["name"], f"{where}.name", maximum=80)
    kind = _require_text(entry["kind"], f"{where}.kind", maximum=32)
    if kind not in VALID_LOCATION_KINDS:
        raise WorldPackageError(
            f"{where}.kind {kind!r} 不在支持列表 {list(VALID_LOCATION_KINDS)} 中"
        )
    area_id = validate_id(entry["area_id"], f"{where}.area_id", maximum=MAX_LOCATION_ID_LENGTH)
    _require_text(entry["description"], f"{where}.description", maximum=240)
    reachable = entry["reachable"]
    if not isinstance(reachable, list) or not all(isinstance(item, str) for item in reachable):
        raise WorldPackageError(f"{where}.reachable 必须是字符串列表")
    if len(set(reachable)) != len(reachable):
        raise WorldPackageError(f"{where}.reachable 存在重复项")
    if location_id in reachable:
        raise WorldPackageError(f"{where}.reachable 包含自身")
    map_data = entry["map"]
    if not isinstance(map_data, dict) or set(map_data) != {"x", "y"}:
        raise WorldPackageError(f"{where}.map 必须是 {{x, y}} 对象")
    for axis in ("x", "y"):
        value = map_data[axis]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorldPackageError(f"{where}.map.{axis} 必须是数字")
    zone_id = validate_id(entry["zone_id"], f"{where}.zone_id", maximum=64)
    result = {
        "location_id": location_id,
        "name": name,
        "kind": kind,
        "area_id": area_id,
        "description": entry["description"].strip(),
        "reachable": list(reachable),
        "map": {"x": map_data["x"], "y": map_data["y"]},
        "zone_id": zone_id,
    }
    rooms = entry.get("rooms", [])
    if not isinstance(rooms, list):
        raise WorldPackageError(f"{where}.rooms 必须是列表")
    normalized_rooms = []
    for room_index, room in enumerate(rooms):
        if not isinstance(room, dict) or set(room) != {"room_id", "name", "description"}:
            raise WorldPackageError(f"{where}.rooms[{room_index}] 必须包含 room_id/name/description")
        normalized_rooms.append(
            {
                "room_id": validate_id(room["room_id"], f"{where}.rooms[{room_index}].room_id"),
                "name": _require_text(room["name"], f"{where}.rooms[{room_index}].name", maximum=80),
                "description": _require_text(
                    room["description"], f"{where}.rooms[{room_index}].description", maximum=240
                ),
            }
        )
    result["rooms"] = normalized_rooms
    return result


def validate_edge(entry: Any, index: int) -> dict[str, Any]:
    where = f"topology.edges[{index}]"
    if not isinstance(entry, dict) or set(entry) != {"from", "to", "walk_seconds"}:
        raise WorldPackageError(f"{where} 必须包含 from/to/walk_seconds")
    origin = validate_id(entry["from"], f"{where}.from", maximum=MAX_LOCATION_ID_LENGTH)
    destination = validate_id(entry["to"], f"{where}.to", maximum=MAX_LOCATION_ID_LENGTH)
    if origin == destination:
        raise WorldPackageError(f"{where} 的起点和终点相同")
    seconds = _require_int(entry["walk_seconds"], f"{where}.walk_seconds", minimum=30, maximum=3600)
    return {"from": origin, "to": destination, "walk_seconds": seconds}


def validate_topology_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("schema_version") != TOPOLOGY_SCHEMA:
        raise WorldPackageError(f"topology.json schema_version 必须是 {TOPOLOGY_SCHEMA}")
    locations_raw = doc.get("locations")
    edges_raw = doc.get("edges")
    if not isinstance(locations_raw, list) or not isinstance(edges_raw, list):
        raise WorldPackageError("topology.json 必须包含 locations 与 edges 列表")
    if not 2 <= len(locations_raw) <= 64:
        raise WorldPackageError(f"locations 数量必须在 2..64 之间，实际 {len(locations_raw)}")
    locations = [validate_location(item, index) for index, item in enumerate(locations_raw)]
    ids = [item["location_id"] for item in locations]
    duplicates = {item for item in ids if ids.count(item) > 1}
    if duplicates:
        raise WorldPackageError(f"存在重复的地点 ID: {sorted(duplicates)}")
    edges = [validate_edge(item, index) for index, item in enumerate(edges_raw)]
    known = set(ids)
    for index, item in enumerate(locations):
        unknown = set(item["reachable"]) - known
        if unknown:
            raise WorldPackageError(
                f"topology.locations[{index}] ({item['location_id']}) 的 reachable "
                f"包含不存在的地点: {sorted(unknown)}"
            )
    for index, edge in enumerate(edges):
        for side in ("from", "to"):
            if edge[side] not in known:
                raise WorldPackageError(f"topology.edges[{index}] 引用了不存在的地点 {edge[side]!r}")
    normalized_edges = []
    seen: set[tuple[str, str]] = set()
    for index, edge in enumerate(edges):
        pair = (edge["from"], edge["to"])
        reverse = (edge["to"], edge["from"])
        if pair in seen or reverse in seen:
            raise WorldPackageError(f"topology.edges[{index}] 与其他边重复（含反向）")
        seen.add(pair)
        normalized_edges.append(edge)
    return {"schema_version": TOPOLOGY_SCHEMA, "locations": locations, "edges": normalized_edges}


def validate_rules_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("schema_version") != RULES_SCHEMA:
        raise WorldPackageError(f"rules.json schema_version 必须是 {RULES_SCHEMA}")
    rules_raw = doc.get("location_rules")
    weather_raw = doc.get("weather_candidates")
    if not isinstance(rules_raw, dict) or not isinstance(weather_raw, list):
        raise WorldPackageError("rules.json 必须包含 location_rules 对象与 weather_candidates 列表")
    rules: dict[str, Any] = {}
    for location_id, rule in rules_raw.items():
        validate_id(location_id, f"rules.location_rules 键 {location_id!r}", maximum=MAX_LOCATION_ID_LENGTH)
        if not isinstance(rule, dict):
            raise WorldPackageError(f"rules.location_rules[{location_id}] 必须是对象")
        open_hour = _require_optional_int(rule.get("open_hour"), f"rules[{location_id}].open_hour", minimum=0, maximum=23)
        close_hour = _require_optional_int(rule.get("close_hour"), f"rules[{location_id}].close_hour", minimum=0, maximum=24)
        if open_hour is not None and close_hour is not None and close_hour <= open_hour:
            raise WorldPackageError(
                f"rules[{location_id}] 的 close_hour 必须晚于 open_hour"
            )
        weekdays_only = rule.get("weekdays_only", False)
        if not isinstance(weekdays_only, bool):
            raise WorldPackageError(f"rules[{location_id}].weekdays_only 必须是布尔值")
        affordances_raw = rule.get("affordances", [])
        if not isinstance(affordances_raw, list):
            raise WorldPackageError(f"rules[{location_id}].affordances 必须是列表")
        affordances = []
        for index, item in enumerate(affordances_raw):
            if not isinstance(item, dict) or set(item) != {"affordance_id", "description"}:
                raise WorldPackageError(
                    f"rules[{location_id}].affordances[{index}] 必须包含 affordance_id/description"
                )
            affordances.append(
                {
                    "affordance_id": _require_text(
                        item["affordance_id"],
                        f"rules[{location_id}].affordances[{index}].affordance_id",
                        maximum=64,
                    ),
                    "description": _require_text(
                        item["description"],
                        f"rules[{location_id}].affordances[{index}].description",
                        maximum=120,
                    ),
                }
            )
        rules[location_id] = {
            "open_hour": open_hour,
            "close_hour": close_hour,
            "weekdays_only": weekdays_only,
            "affordances": affordances,
        }
    weather = []
    for index, item in enumerate(weather_raw):
        if not isinstance(item, dict) or set(item) != {"condition", "description"}:
            raise WorldPackageError(f"rules.weather_candidates[{index}] 必须包含 condition/description")
        weather.append(
            {
                "condition": _require_text(item["condition"], f"weather_candidates[{index}].condition", maximum=40),
                "description": _require_text(item["description"], f"weather_candidates[{index}].description", maximum=80),
            }
        )
    if not weather:
        raise WorldPackageError("rules.weather_candidates 不能为空")
    return {"schema_version": RULES_SCHEMA, "location_rules": rules, "weather_candidates": weather}


def validate_npcs_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("schema_version") != NPCS_SCHEMA:
        raise WorldPackageError(f"npcs.json schema_version 必须是 {NPCS_SCHEMA}")
    npcs_raw = doc.get("npcs")
    if not isinstance(npcs_raw, list):
        raise WorldPackageError("npcs.json 必须包含 npcs 列表")
    if len(npcs_raw) > 24:
        raise WorldPackageError("npcs 数量不能超过 24")
    npcs = []
    ids: set[str] = set()
    for index, npc in enumerate(npcs_raw):
        where = f"npcs.npcs[{index}]"
        if not isinstance(npc, dict):
            raise WorldPackageError(f"{where} 必须是对象")
        required = {"npc_id", "display_name", "age_classification", "public_role", "home_base_area", "public_description", "windows"}
        missing = required - set(npc)
        if missing:
            raise WorldPackageError(f"{where} 缺少字段: {sorted(missing)}")
        npc_id = validate_id(npc["npc_id"], f"{where}.npc_id", maximum=96)
        if npc_id in ids:
            raise WorldPackageError(f"{where} 的 npc_id 重复: {npc_id}")
        ids.add(npc_id)
        age = npc["age_classification"]
        if age not in ("minor", "adult"):
            raise WorldPackageError(f"{where}.age_classification 必须是 minor 或 adult")
        windows_raw = npc["windows"]
        if not isinstance(windows_raw, list) or not windows_raw:
            raise WorldPackageError(f"{where}.windows 必须是非空列表")
        windows = []
        for window_index, window in enumerate(windows_raw):
            if not isinstance(window, dict) or set(window) != {"weekdays", "start_minute", "end_minute", "location_id"}:
                raise WorldPackageError(
                    f"{where}.windows[{window_index}] 必须包含 weekdays/start_minute/end_minute/location_id"
                )
            weekdays = window["weekdays"]
            if (
                not isinstance(weekdays, list)
                or not weekdays
                or not all(isinstance(day, int) and not isinstance(day, bool) and 1 <= day <= 7 for day in weekdays)
            ):
                raise WorldPackageError(
                    f"{where}.windows[{window_index}].weekdays 必须是 1..7 的整数列表"
                )
            start = _require_int(window["start_minute"], f"{where}.windows[{window_index}].start_minute", minimum=0, maximum=1439)
            end = _require_int(window["end_minute"], f"{where}.windows[{window_index}].end_minute", minimum=1, maximum=1440)
            if end <= start:
                raise WorldPackageError(f"{where}.windows[{window_index}] 的结束时间必须晚于开始时间")
            windows.append(
                {
                    "weekdays": sorted(set(weekdays)),
                    "start_minute": start,
                    "end_minute": end,
                    "location_id": validate_id(
                        window["location_id"], f"{where}.windows[{window_index}].location_id", maximum=96
                    ),
                }
            )
        npcs.append(
            {
                "npc_id": npc_id,
                "display_name": _require_text(npc["display_name"], f"{where}.display_name", maximum=40),
                "age_classification": age,
                "public_role": _require_text(npc["public_role"], f"{where}.public_role", maximum=60),
                "home_base_area": validate_id(npc["home_base_area"], f"{where}.home_base_area", maximum=96),
                "public_description": _require_text(npc["public_description"], f"{where}.public_description", maximum=160),
                "windows": windows,
            }
        )
    return {"schema_version": NPCS_SCHEMA, "npcs": npcs}


def _validate_iana_timezone(value: Any, field: str) -> str:
    """IANA 时区名校验；与 world.json 的降级规则一致（无 tz 库时校验名字格式）。"""
    timezone_name = _require_text(value, field, maximum=64)
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            try:
                ZoneInfo("UTC")
            except (ZoneInfoNotFoundError, ValueError):
                if not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_+\-]*/[A-Za-z0-9_+\-/]+", timezone_name
                ):
                    raise WorldPackageError(
                        f"{field} {timezone_name!r} 不是有效 IANA 时区名"
                    ) from None
            else:
                raise WorldPackageError(
                    f"{field} {timezone_name!r} 不是有效 IANA 时区"
                ) from None
        except ValueError as exc:
            raise WorldPackageError(f"{field} {timezone_name!r} 不是有效 IANA 时区") from exc
    except ImportError:  # pragma: no cover - 极老环境
        pass
    return timezone_name


def _validate_ambient_v1(doc: dict[str, Any]) -> dict[str, Any]:
    templates_raw = doc.get("templates")
    if not isinstance(templates_raw, list) or not templates_raw:
        raise WorldPackageError("ambient.json 必须包含非空 templates 列表")
    if len(templates_raw) > MAX_AMBIENT_TEMPLATES_V1:
        raise WorldPackageError(
            f"ambient.templates 数量不能超过 {MAX_AMBIENT_TEMPLATES_V1}"
        )
    templates = []
    for index, template in enumerate(templates_raw):
        where = f"ambient.templates[{index}]"
        if not isinstance(template, dict) or set(template) != {"event_type", "location_id", "summary", "start_hour", "end_hour"}:
            raise WorldPackageError(
                f"{where} 必须包含 event_type/location_id/summary/start_hour/end_hour"
            )
        start = _require_int(template["start_hour"], f"{where}.start_hour", minimum=0, maximum=24)
        end = _require_int(template["end_hour"], f"{where}.end_hour", minimum=0, maximum=24)
        if end == start:
            raise WorldPackageError(f"{where} 的事件窗口为空")
        templates.append(
            {
                "event_type": validate_id(template["event_type"], f"{where}.event_type", maximum=64),
                "location_id": validate_id(template["location_id"], f"{where}.location_id", maximum=96),
                "summary": _require_text(template["summary"], f"{where}.summary", maximum=240),
                "start_hour": start,
                "end_hour": end,
            }
        )
    return {"schema_version": AMBIENT_SCHEMA, "templates": templates}


def _validate_finite_spec(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {
        "identity",
        "starts_on",
        "ends_on",
        "end_reason",
        "phases",
    }:
        raise WorldPackageError(
            f"{where}.finite 必须包含 identity/starts_on/ends_on/end_reason/phases"
        )
    identity = validate_id(raw["identity"], f"{where}.finite.identity", maximum=64)
    try:
        from datetime import date as _date

        starts_on = _date.fromisoformat(raw["starts_on"])
        ends_on = _date.fromisoformat(raw["ends_on"])
    except (TypeError, ValueError) as exc:
        raise WorldPackageError(f"{where}.finite 的日期范围无效") from exc
    if starts_on > ends_on:
        raise WorldPackageError(f"{where}.finite 的起始日期晚于结束日期")
    end_reason = _require_text(
        raw["end_reason"], f"{where}.finite.end_reason", maximum=120
    )
    phases_raw = raw["phases"]
    if not isinstance(phases_raw, list) or not phases_raw:
        raise WorldPackageError(f"{where}.finite.phases 必须是非空列表")
    phases = []
    previous = None
    for index, phase in enumerate(phases_raw):
        phase_where = f"{where}.finite.phases[{index}]"
        if not isinstance(phase, dict) or set(phase) != {
            "local_date",
            "start_hour",
            "end_hour",
            "summary",
        }:
            raise WorldPackageError(
                f"{phase_where} 必须包含 local_date/start_hour/end_hour/summary"
            )
        try:
            from datetime import date as _date

            local_date = _date.fromisoformat(phase["local_date"])
        except (TypeError, ValueError) as exc:
            raise WorldPackageError(f"{phase_where}.local_date 无效") from exc
        if not starts_on <= local_date <= ends_on:
            raise WorldPackageError(f"{phase_where}.local_date 超出事件起止范围")
        if previous is not None and local_date <= previous:
            raise WorldPackageError(f"{where}.finite.phases 必须按日期严格递增")
        previous = local_date
        start = _require_int(
            phase["start_hour"], f"{phase_where}.start_hour", minimum=0, maximum=24
        )
        end = _require_int(
            phase["end_hour"], f"{phase_where}.end_hour", minimum=0, maximum=24
        )
        if start == end:
            raise WorldPackageError(f"{phase_where} 的事件窗口为空")
        phases.append(
            {
                "local_date": local_date.isoformat(),
                "start_hour": start,
                "end_hour": end,
                "summary": _require_text(
                    phase["summary"], f"{phase_where}.summary", maximum=240
                ),
            }
        )
    return {
        "identity": identity,
        "starts_on": starts_on.isoformat(),
        "ends_on": ends_on.isoformat(),
        "end_reason": end_reason,
        "phases": phases,
    }


def _validate_ambient_v2(doc: dict[str, Any]) -> dict[str, Any]:
    unknown = set(doc) - {"schema_version", "templates", "timezone"}
    if unknown:
        raise WorldPackageError(f"ambient.json v2 包含未知字段: {sorted(unknown)}")
    templates_raw = doc.get("templates")
    if not isinstance(templates_raw, list) or not templates_raw:
        raise WorldPackageError("ambient.json 必须包含非空 templates 列表")
    if len(templates_raw) > MAX_AMBIENT_TEMPLATES_V2:
        raise WorldPackageError(
            f"ambient.templates 数量不能超过 {MAX_AMBIENT_TEMPLATES_V2}"
        )
    templates = []
    event_types: set[str] = set()
    identities: set[str] = set()
    for index, template in enumerate(templates_raw):
        where = f"ambient.templates[{index}]"
        if not isinstance(template, dict):
            raise WorldPackageError(f"{where} 必须是 JSON 对象")
        kind = template.get("kind")
        if kind == "single_day":
            if set(template) != {
                "kind",
                "event_type",
                "location_id",
                "summary",
                "start_hour",
                "end_hour",
            }:
                raise WorldPackageError(
                    f"{where} single_day 必须包含 kind/event_type/location_id/summary/start_hour/end_hour"
                )
            start = _require_int(
                template["start_hour"], f"{where}.start_hour", minimum=0, maximum=24
            )
            end = _require_int(
                template["end_hour"], f"{where}.end_hour", minimum=0, maximum=24
            )
            if end == start:
                raise WorldPackageError(f"{where} 的事件窗口为空")
            event_type = validate_id(
                template["event_type"], f"{where}.event_type", maximum=64
            )
            normalized = {
                "kind": "single_day",
                "event_type": event_type,
                "location_id": validate_id(
                    template["location_id"], f"{where}.location_id", maximum=96
                ),
                "summary": _require_text(
                    template["summary"], f"{where}.summary", maximum=240
                ),
                "start_hour": start,
                "end_hour": end,
            }
        elif kind == "finite":
            if set(template) != {
                "kind",
                "event_type",
                "location_id",
                "summary",
                "finite",
            }:
                raise WorldPackageError(
                    f"{where} finite 必须包含 kind/event_type/location_id/summary/finite"
                )
            event_type = validate_id(
                template["event_type"], f"{where}.event_type", maximum=64
            )
            finite = _validate_finite_spec(template["finite"], where)
            if finite["identity"] in identities:
                raise WorldPackageError(
                    f"{where}.finite.identity 重复: {finite['identity']}"
                )
            identities.add(finite["identity"])
            normalized = {
                "kind": "finite",
                "event_type": event_type,
                "location_id": validate_id(
                    template["location_id"], f"{where}.location_id", maximum=96
                ),
                "summary": _require_text(
                    template["summary"], f"{where}.summary", maximum=240
                ),
                "finite": finite,
            }
        else:
            raise WorldPackageError(
                f"{where}.kind 必须是 single_day 或 finite（v2 显式类型）"
            )
        if normalized["event_type"] in event_types:
            raise WorldPackageError(
                f"{where}.event_type 重复: {normalized['event_type']}"
            )
        event_types.add(normalized["event_type"])
        templates.append(normalized)
    result: dict[str, Any] = {
        "schema_version": AMBIENT_SCHEMA_V2,
        "templates": templates,
    }
    if "timezone" in doc:
        result["timezone"] = _validate_iana_timezone(
            doc["timezone"], "ambient.json.timezone"
        )
    return result


def validate_ambient_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise WorldPackageError("ambient.json 顶层必须是 JSON 对象")
    version = doc.get("schema_version")
    if version == AMBIENT_SCHEMA:
        return _validate_ambient_v1(doc)
    if version == AMBIENT_SCHEMA_V2:
        return _validate_ambient_v2(doc)
    raise WorldPackageError(
        f"ambient.json schema_version 必须是 {AMBIENT_SCHEMA} 或 {AMBIENT_SCHEMA_V2}，"
        f"实际是 {version!r}；未知版本拒绝加载，不自动降级"
    )


def validate_objects_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("schema_version") != OBJECTS_SCHEMA:
        raise WorldPackageError(f"objects.json schema_version 必须是 {OBJECTS_SCHEMA}")
    objects_raw = doc.get("objects")
    if not isinstance(objects_raw, list):
        raise WorldPackageError("objects.json 必须包含 objects 列表")
    if len(objects_raw) > 128:
        raise WorldPackageError("objects.objects 数量不能超过 128")
    objects = []
    ids: set[str] = set()
    for index, obj in enumerate(objects_raw):
        where = f"objects.objects[{index}]"
        if not isinstance(obj, dict) or set(obj) != {"object_id", "name", "kind", "location_id", "note"}:
            raise WorldPackageError(f"{where} 必须包含 object_id/name/kind/location_id/note")
        object_id = validate_id(obj["object_id"], f"{where}.object_id", maximum=96)
        if object_id in ids:
            raise WorldPackageError(f"{where} 的 object_id 重复: {object_id}")
        ids.add(object_id)
        objects.append(
            {
                "object_id": object_id,
                "name": _require_text(obj["name"], f"{where}.name", maximum=80),
                "kind": _require_text(obj["kind"], f"{where}.kind", maximum=40),
                "location_id": validate_id(obj["location_id"], f"{where}.location_id", maximum=96),
                "note": _require_text(obj["note"], f"{where}.note", maximum=200, allow_empty=True),
            }
        )
    return {"schema_version": OBJECTS_SCHEMA, "objects": objects}


def validate_content_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("schema_version") != CONTENT_SCHEMA:
        raise WorldPackageError(f"content.json schema_version 必须是 {CONTENT_SCHEMA}")
    source = doc.get("source")
    if source not in ("local", "glm"):
        raise WorldPackageError("content.source 必须是 local 或 glm")
    model = doc.get("model")
    if model is not None and not isinstance(model, str):
        raise WorldPackageError("content.model 必须是字符串或 null")
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        raise WorldPackageError("content.entries 必须是对象")
    normalized: dict[str, Any] = {}
    for key, entry in entries.items():
        validate_id(key, f"content.entries 键 {key!r}", maximum=96)
        if not isinstance(entry, dict):
            raise WorldPackageError(f"content.entries[{key}] 必须是对象")
        normalized_entry: dict[str, Any] = {}
        for field, value in entry.items():
            if not isinstance(field, str) or not field.strip():
                raise WorldPackageError(f"content.entries[{key}] 的字段名必须是字符串")
            if isinstance(value, str):
                if len(value.strip()) > 400:
                    raise WorldPackageError(f"content.entries[{key}].{field} 超过 400 字符")
                normalized_entry[field.strip()] = value.strip()
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                if len(value) > 8 or any(len(item) > 120 for item in value):
                    raise WorldPackageError(f"content.entries[{key}].{field} 列表越界")
                normalized_entry[field.strip()] = [item.strip() for item in value]
            else:
                raise WorldPackageError(
                    f"content.entries[{key}].{field} 必须是字符串或字符串列表"
                )
        normalized[key] = normalized_entry
    return {"schema_version": CONTENT_SCHEMA, "source": source, "model": model, "entries": normalized}


def validate_scene_doc(doc: Any) -> dict[str, Any]:
    """场景文件校验（轻结构校验）。

    ``spaces``/``placements``/``assets`` 的内部结构是世界编辑器（v3）的
    原生形状：这里只校验外层结构与键类型、对地点/对象的存在性做交叉检查
    （在 load_package 中进行）；编辑器几何本身的完整校验在正向转换消费
    场景时由编辑器自己的校验器执行。

    ``navigation``（v0.4 可选）：室内离散导航声明——
    ``{place_id: {areas: [{area_id, name, kind}], connections: [{from, to}],
    entrance: area_id, furniture_map: {area_id: [object_id]}}}``。
    声明是权威数据；无 navigation 的旧场景视为单区域（向后兼容）。
    """
    if not isinstance(doc, dict) or doc.get("schema_version") != SCENE_SCHEMA:
        raise WorldPackageError(f"scene.json schema_version 必须是 {SCENE_SCHEMA}")
    for field in ("spaces", "placements", "assets"):
        if not isinstance(doc.get(field), dict):
            raise WorldPackageError(f"scene.{field} 必须是对象")
    spaces = doc["spaces"]
    placements = doc["placements"]
    assets = doc["assets"]
    if len(spaces) > 64 or len(placements) > 300 or len(assets) > 32:
        raise WorldPackageError("scene 超出大小限制（区域 64 / 物件 300 / 素材 32）")
    normalized = {
        "schema_version": SCENE_SCHEMA,
        "spaces": dict(spaces),
        "placements": dict(placements),
        "assets": dict(assets),
    }
    for location_id, space in normalized["spaces"].items():
        validate_id(location_id, f"scene.spaces 键 {location_id!r}", maximum=MAX_LOCATION_ID_LENGTH)
        if not isinstance(space, dict) or set(space) != {"enabled", "entry", "walls", "doors", "windows", "floors"}:
            raise WorldPackageError(
                f"scene.spaces[{location_id}] 必须是编辑器室内空间形状"
                "（enabled/entry/walls/doors/windows/floors）"
            )
    for object_id, placement in normalized["placements"].items():
        validate_id(object_id, f"scene.placements 键 {object_id!r}", maximum=96)
        required = {
            "kind", "x", "y", "visual", "actions", "behavior",
            "portable", "capacity", "servings", "initial_status",
            "interaction", "asset_id",
        }
        if not isinstance(placement, dict) or not required.issubset(set(placement)):
            raise WorldPackageError(
                f"scene.placements[{object_id}] 缺少编辑器物件字段"
            )
    for asset_id, asset in normalized["assets"].items():
        validate_id(asset_id, f"scene.assets 键 {asset_id!r}", maximum=96)
        if not isinstance(asset, dict) or asset.get("id") != asset_id:
            raise WorldPackageError(f"scene.assets[{asset_id}] 的 id 与键不一致")
    if doc.get("navigation") is not None:
        normalized["navigation"] = validate_navigation_doc(doc["navigation"])
    return normalized


def validate_navigation_doc(doc: Any) -> dict[str, Any]:
    """室内离散导航声明校验（每栋建筑的区域与连接）。"""
    if not isinstance(doc, dict) or not doc:
        raise WorldPackageError("scene.navigation 必须是非空对象")
    normalized: dict[str, Any] = {}
    for place_id, nav in doc.items():
        validate_id(place_id, f"scene.navigation 键 {place_id!r}", maximum=MAX_LOCATION_ID_LENGTH)
        if not isinstance(nav, dict):
            raise WorldPackageError(f"scene.navigation[{place_id}] 必须是对象")
        required = {"areas", "connections", "entrance", "furniture_map"}
        missing = required - set(nav)
        if missing:
            raise WorldPackageError(
                f"scene.navigation[{place_id}] 缺少字段: {sorted(missing)}"
            )
        areas_raw = nav["areas"]
        if not isinstance(areas_raw, list) or not 1 <= len(areas_raw) <= 16:
            raise WorldPackageError(f"scene.navigation[{place_id}].areas 需要 1..16 个区域")
        area_ids: set[str] = set()
        areas = []
        for index, area in enumerate(areas_raw):
            if not isinstance(area, dict) or set(area) != {"area_id", "name", "kind"}:
                raise WorldPackageError(
                    f"scene.navigation[{place_id}].areas[{index}] 必须包含 area_id/name/kind"
                )
            area_id = validate_id(area["area_id"], f"navigation[{place_id}].areas[{index}].area_id", maximum=96)
            if area_id in area_ids:
                raise WorldPackageError(f"scene.navigation[{place_id}] 区域 ID 重复: {area_id}")
            area_ids.add(area_id)
            areas.append(
                {
                    "area_id": area_id,
                    "name": _require_text(area["name"], f"navigation[{place_id}].areas[{index}].name", maximum=40),
                    "kind": _require_text(area["kind"], f"navigation[{place_id}].areas[{index}].kind", maximum=32),
                }
            )
        entrance = validate_id(nav["entrance"], f"navigation[{place_id}].entrance", maximum=96)
        if entrance not in area_ids:
            raise WorldPackageError(
                f"scene.navigation[{place_id}].entrance {entrance!r} 不是已声明区域"
            )
        connections_raw = nav["connections"]
        if not isinstance(connections_raw, list):
            raise WorldPackageError(f"scene.navigation[{place_id}].connections 必须是列表")
        connections = []
        seen_pairs: set[frozenset] = set()
        for index, connection in enumerate(connections_raw):
            if not isinstance(connection, dict) or set(connection) != {"from", "to"}:
                raise WorldPackageError(
                    f"scene.navigation[{place_id}].connections[{index}] 必须包含 from/to"
                )
            for side in ("from", "to"):
                if connection[side] not in area_ids:
                    raise WorldPackageError(
                        f"scene.navigation[{place_id}].connections[{index}] 引用未声明区域 {connection[side]!r}"
                    )
            if connection["from"] == connection["to"]:
                raise WorldPackageError(
                    f"scene.navigation[{place_id}].connections[{index}] 的两端相同"
                )
            pair = frozenset((connection["from"], connection["to"]))
            if pair in seen_pairs:
                continue  # 双向连接的重复声明直接忽略
            seen_pairs.add(pair)
            connections.append({"from": connection["from"], "to": connection["to"]})
        furniture_map_raw = nav["furniture_map"]
        if not isinstance(furniture_map_raw, dict):
            raise WorldPackageError(f"scene.navigation[{place_id}].furniture_map 必须是对象")
        furniture_map: dict[str, list[str]] = {}
        for area_id, object_ids in furniture_map_raw.items():
            if area_id not in area_ids:
                raise WorldPackageError(
                    f"scene.navigation[{place_id}].furniture_map 引用未声明区域 {area_id!r}"
                )
            if not isinstance(object_ids, list) or not all(isinstance(item, str) for item in object_ids):
                raise WorldPackageError(
                    f"scene.navigation[{place_id}].furniture_map[{area_id}] 必须是字符串列表"
                )
            furniture_map[area_id] = list(object_ids)
        normalized[place_id] = {
            "areas": areas,
            "connections": connections,
            "entrance": entrance,
            "furniture_map": furniture_map,
        }
    return normalized


DOC_VALIDATORS = {
    "world.json": validate_world_doc,
    "topology.json": validate_topology_doc,
    "rules.json": validate_rules_doc,
    "npcs.json": validate_npcs_doc,
    "ambient.json": validate_ambient_doc,
    "objects.json": validate_objects_doc,
    "content.json": validate_content_doc,
}


# ---------------------------------------------------------------------------
# 清单与读写
# ---------------------------------------------------------------------------


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(package_dir: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    covered = [*PACKAGE_FILES, SCENE_FILE]
    for name in covered:
        target = package_dir / name
        if not target.exists():
            if name in PACKAGE_FILES:
                raise WorldPackageError(f"世界包缺少 {name}")
            continue  # 场景文件可选
        files[name] = file_digest(target)
    return {"schema_version": MANIFEST_SCHEMA, "package_schema": PACKAGE_SCHEMA, "files": files}


def verify_manifest(package_dir: Path) -> None:
    manifest_path = package_dir / "MANIFEST.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise WorldPackageError(f"MANIFEST.json schema_version 必须是 {MANIFEST_SCHEMA}")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise WorldPackageError("MANIFEST.json 缺少 files 映射")
    for name in PACKAGE_FILES:
        expected = files.get(name)
        target = package_dir / name
        if not isinstance(expected, str):
            raise WorldPackageError(f"MANIFEST.json 缺少 {name} 的校验值")
        if not target.exists():
            raise WorldPackageError(f"世界包缺少 {name}（清单声明了它）")
        actual = file_digest(target)
        if actual != expected:
            raise WorldPackageError(
                f"{name} 的 SHA256 与 MANIFEST.json 不一致：世界包文件被修改过。"
                "请重新生成或重新导出世界包，运行时拒绝加载被篡改的世界。"
            )
    # 场景文件可选：存在就必须声明且匹配，声明了就必须存在。
    scene_target = package_dir / SCENE_FILE
    scene_expected = files.get(SCENE_FILE)
    if scene_target.exists():
        if not isinstance(scene_expected, str):
            raise WorldPackageError(
                f"{SCENE_FILE} 存在但未列入 MANIFEST.json；请重新导出世界包"
            )
        if file_digest(scene_target) != scene_expected:
            raise WorldPackageError(
                f"{SCENE_FILE} 的 SHA256 与 MANIFEST.json 不一致：场景数据被修改过。"
            )
    elif scene_expected is not None:
        raise WorldPackageError(f"MANIFEST.json 声明了 {SCENE_FILE} 但文件不存在")
    extra = set(files) - set(PACKAGE_FILES) - {SCENE_FILE}
    if extra:
        raise WorldPackageError(f"MANIFEST.json 声明了未知文件: {sorted(extra)}")


def write_json(path: Path, doc: dict[str, Any]) -> None:
    # 显式 LF：世界包是跨平台工件，SHA256 清单要求字节级平台一致
    # （仓库 .gitattributes 也会在提交时把文本归一为 LF）。
    payload = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


def write_package(package_dir: Path, docs: dict[str, dict[str, Any]]) -> None:
    """把校验过的一组文档写入目录并生成 MANIFEST。"""
    package_dir = Path(package_dir)
    missing = set(DOC_VALIDATORS) - set(docs)
    if missing:
        raise WorldPackageError(f"缺少文档: {sorted(missing)}")
    for name, validator in DOC_VALIDATORS.items():
        docs[name] = validator(docs[name])
    scene = docs.get(SCENE_FILE)
    if scene is not None:
        docs[SCENE_FILE] = validate_scene_doc(scene)
    package_dir.mkdir(parents=True, exist_ok=True)
    for name in PACKAGE_FILES:
        write_json(package_dir / name, docs[name])
    if scene is not None:
        write_json(package_dir / SCENE_FILE, docs[SCENE_FILE])
    write_json(package_dir / "MANIFEST.json", build_manifest(package_dir))


def load_package(package_dir: Path, *, verify: bool = True) -> dict[str, Any]:
    """读取并校验一个世界包，返回 {name: doc} 字典。"""
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        raise WorldPackageError(f"世界包目录不存在: {package_dir}")
    if verify:
        verify_manifest(package_dir)
    docs: dict[str, Any] = {}
    for name, validator in DOC_VALIDATORS.items():
        docs[name] = validator(_read_json(package_dir / name))
    scene_path = package_dir / SCENE_FILE
    if scene_path.exists():
        docs[SCENE_FILE] = validate_scene_doc(_read_json(scene_path))
    # v2 环境事件可显式声明时区；不允许与 world.json 的权威时区混用。
    ambient_doc = docs["ambient.json"]
    if ambient_doc.get("schema_version") == AMBIENT_SCHEMA_V2:
        declared_timezone = ambient_doc.get("timezone")
        if (
            declared_timezone is not None
            and declared_timezone != docs["world.json"]["timezone"]
        ):
            raise WorldPackageError(
                f"ambient.json.timezone {declared_timezone!r} 与 world.json 的时区 "
                f"{docs['world.json']['timezone']!r} 不一致；事件时间只有一个权威时区"
            )
    # 交叉校验：地点规则 / NPC / 环境事件 / 对象都必须引用真实地点。
    locations = {item["location_id"] for item in docs["topology.json"]["locations"]}
    for location_id in docs["rules.json"]["location_rules"]:
        if location_id not in locations:
            raise WorldPackageError(f"rules.location_rules 引用了不存在的地点 {location_id!r}")
    for index, npc in enumerate(docs["npcs.json"]["npcs"]):
        if npc["home_base_area"] not in locations:
            raise WorldPackageError(
                f"npcs.npcs[{index}] ({npc['npc_id']}) 的 home_base_area 引用了不存在的地点"
            )
        for window in npc["windows"]:
            if window["location_id"] not in locations:
                raise WorldPackageError(
                    f"npcs.npcs[{index}] ({npc['npc_id']}) 的出场地点引用了不存在的地点"
                )
    for index, template in enumerate(docs["ambient.json"]["templates"]):
        if template["location_id"] not in locations:
            raise WorldPackageError(
                f"ambient.templates[{index}] ({template['event_type']}) 引用了不存在的地点"
            )
    for index, obj in enumerate(docs["objects.json"]["objects"]):
        if obj["location_id"] not in locations:
            raise WorldPackageError(
                f"objects.objects[{index}] ({obj['object_id']}) 引用了不存在的地点"
            )
    if SCENE_FILE in docs:
        scene = docs[SCENE_FILE]
        objects = {item["object_id"] for item in docs["objects.json"]["objects"]}
        for location_id in scene["spaces"]:
            if location_id not in locations:
                raise WorldPackageError(
                    f"scene.spaces 引用了不存在的地点 {location_id!r}"
                )
        for object_id in scene["placements"]:
            if object_id not in objects:
                raise WorldPackageError(
                    f"scene.placements 引用了不存在的对象 {object_id!r}"
                )
        if "navigation" in scene:
            for place_id, nav in scene["navigation"].items():
                if place_id not in locations:
                    raise WorldPackageError(
                        f"scene.navigation 引用了不存在的地点 {place_id!r}"
                    )
                referenced_objects = {
                    object_id
                    for area_objects in nav["furniture_map"].values()
                    for object_id in area_objects
                }
                unknown = referenced_objects - objects
                if unknown:
                    raise WorldPackageError(
                        f"scene.navigation[{place_id}].furniture_map 引用了不存在的对象: {sorted(unknown)}"
                    )
        used_assets = {
            placement["asset_id"]
            for placement in scene["placements"].values()
            if placement.get("asset_id")
        }
        for space in scene["spaces"].values():
            used_assets.update(
                floor.get("asset_id") for floor in space["floors"] if floor.get("asset_id")
            )
        missing = used_assets - set(scene["assets"])
        if missing:
            raise WorldPackageError(f"scene 缺少被引用的素材: {sorted(missing)}")
    return docs


def package_summary(docs: dict[str, Any]) -> dict[str, Any]:
    """简短摘要（CLI 列表/预览用）。"""
    world = docs["world.json"]
    topology = docs["topology.json"]
    return {
        "world_id": world["world_id"],
        "name": world["name"],
        "theme": world["theme"],
        "seed": world["seed"],
        "timezone": world["timezone"],
        "generator_version": world["generator_version"],
        "locations": len(topology["locations"]),
        "edges": len(topology["edges"]),
        "npcs": len(docs["npcs.json"]["npcs"]),
        "objects": len(docs["objects.json"]["objects"]),
        "content_source": docs["content.json"]["source"],
    }


__all__ = [
    "AMBIENT_SCHEMA",
    "AMBIENT_SCHEMA_V2",
    "MAX_AMBIENT_TEMPLATES_V1",
    "MAX_AMBIENT_TEMPLATES_V2",
    "CONTENT_SCHEMA",
    "ID_RE",
    "MANIFEST_SCHEMA",
    "NPCS_SCHEMA",
    "OBJECTS_SCHEMA",
    "PACKAGE_FILES",
    "PACKAGE_SCHEMA",
    "RULES_SCHEMA",
    "SCENE_FILE",
    "SCENE_SCHEMA",
    "TOPOLOGY_SCHEMA",
    "VALID_LOCATION_KINDS",
    "WorldPackageError",
    "WORLD_SCHEMA",
    "build_manifest",
    "load_package",
    "package_summary",
    "validate_ambient_doc",
    "validate_content_doc",
    "validate_id",
    "validate_location",
    "validate_npcs_doc",
    "validate_navigation_doc",
    "validate_objects_doc",
    "validate_rules_doc",
    "validate_scene_doc",
    "validate_topology_doc",
    "validate_world_doc",
    "verify_manifest",
    "write_package",
]
