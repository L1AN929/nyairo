"""Versioned world content and deterministic, isolated editor test runtime.

This is a new runtime for creator acceptance tests, not a production nyairo writer.
No model, personal memory store, or live World state is accessed here.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from datetime import datetime
from assets import ASSETS

FORMAT = "nyairo.world-package.v2"
ENGINE = "nyairo.editor-runtime.v2"
SAVE_FORMAT = "nyairo.editor-save.v2"
LEGACY_FORMAT = "nyairo.world-package.v1"
LEGACY_ENGINE = "nyairo.editor-runtime.v1"
LEGACY_SAVE = "nyairo.editor-save.v1"
ACTIONS = {"sit", "rest", "read", "pick_up", "place", "store", "retrieve", "drink"}
KINDS = {"chair", "bed", "book", "cup", "cabinet", "table", "plant"}
DEFAULT_ACTIONS = {"chair": ["sit"], "bed": ["rest"], "book": ["pick_up", "place", "read"],
                   "cup": ["pick_up", "place", "drink"], "cabinet": ["store", "retrieve"],
                   "table": [], "plant": []}

THEMES = {
    "bedroom": {"name": "温暖卧室", "floor": "#f0e3d3", "texture": "planks", "border": "#b99d89", "border_style": "solid", "background": "#f8f0e7"},
    "bookstore": {"name": "雨天书店", "floor": "#e8e8dc", "texture": "planks", "border": "#a4ac97", "border_style": "solid", "background": "#f3f3e9"},
    "cafe": {"name": "简约咖啡馆", "floor": "#eee7df", "texture": "tiles", "border": "#b7a79a", "border_style": "solid", "background": "#faf6ef"},
    "street": {"name": "夜晚街道", "floor": "#d0d9e8", "texture": "tiles", "border": "#8a9cb7", "border_style": "dashed", "background": "#e4eaf4"},
}
PARAMETERS = {
    "read": {"minutes": (15, 1, 240, "阅读分钟"), "energy_cost": (3, 0, 100, "精力消耗")},
    "rest": {"minutes": (30, 1, 720, "休息分钟"), "energy_gain": (15, 0, 100, "精力恢复")},
    "drink": {"hydration_gain": (20, 0, 100, "水分补充")},
}
ACTION_SPECS = {
    "pick_up": {"label": "拿起", "condition": "同区域且开放、可拿取、空手、物件未收纳", "result": "原位隐藏 → 居民持有；时间 +1 分钟"},
    "place": {"label": "放下", "condition": "同区域且开放、居民持有此物", "result": "在当前区域重新出现 → 空手；时间 +1 分钟"},
    "read": {"label": "阅读", "condition": "同区域且开放、先拿起这本书", "result": "推进配置分钟 → 阅读次数 +1 → 精力减少"},
    "rest": {"label": "休息", "condition": "同区域且开放、未使用其他家具", "result": "躺下并占用床 → 推进配置分钟 → 精力恢复"},
    "sit": {"label": "坐下", "condition": "同区域且开放、未使用其他家具", "result": "坐下并占用椅子 → 使用次数 +1；时间 +1 分钟"},
    "drink": {"label": "喝饮品", "condition": "同区域且开放、持有杯子、至少剩余一份", "result": "份数 −1 → 水分增加；时间 +1 分钟"},
    "store": {"label": "收纳", "condition": "同区域且开放、持有物件、柜子未满", "result": "持有物进入柜子 → 空手；时间 +1 分钟"},
    "retrieve": {"label": "取出", "condition": "同区域且开放、空手、柜内存在所选物件", "result": "柜内物件减少 → 居民持有；时间 +1 分钟"},
}


def default_visual(kind):
    theme = "street" if kind == "outdoor" else "bookstore" if kind == "shop" else "bedroom"
    return {"theme": theme, **{k: v for k, v in THEMES[theme].items() if k != "name"}}


def default_behavior(kind):
    return {action: {key: value[0] for key, value in PARAMETERS[action].items()}
            for action in DEFAULT_ACTIONS[kind] if action in PARAMETERS}


def color(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        fail(f"{label}需要是六位十六进制颜色，例如 #f8f0e7。")


def templates():
    return {a: {**spec, "parameters": [{"key": key, "default": val[0], "min": val[1], "max": val[2], "label": val[3]}
                                    for key, val in PARAMETERS.get(a, {}).items()]}
            for a, spec in ACTION_SPECS.items()}


class WorldError(ValueError):
    def __init__(self, message, code="INVALID", status=422):
        super().__init__(message)
        self.code, self.status = code, status


def fail(message, code="INVALID", status=422):
    raise WorldError(message, code, status)


def text(value, label, maximum=160, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        fail(f"{label}需要是{'非空' if not empty else ''}文字，最长 {maximum} 个字。")
    return value


def ident(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        fail(f"{label}只能包含字母、数字、下划线或连字符（1–64 字符）。")
    return value


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        fail(f"{label}需要是 {low}–{high} 之间的整数。")
    return value


def fields(obj, expected, label):
    if not isinstance(obj, dict) or set(obj) != set(expected):
        fail(f"{label}字段不完整或含有不支持的字段。")


def canonical(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        fail("内容不是有效的有限 JSON 数据。")


def digest(package):
    return hashlib.sha256(canonical(package).encode()).hexdigest()


def validate_package(raw):
    if not isinstance(raw, dict):
        fail("世界包需要是 JSON 对象。")
    legacy = raw.get("format") == LEGACY_FORMAT and raw.get("requires") == LEGACY_ENGINE
    if not legacy and (raw.get("format") != FORMAT or raw.get("requires") != ENGINE):
        fail("世界包版本不兼容。支持编辑器 v1 升级导入和 v2；不支持生产运行时包。", "INCOMPATIBLE")
    fields(raw, ["format", "requires", "meta", "rooms", "connections", "objects", "initial"] + ([] if legacy else ["scene"]), "世界包")
    p = copy.deepcopy(raw)
    if legacy:
        if not isinstance(p["rooms"], list) or not isinstance(p["objects"], list):
            fail("区域和物件需要是列表。")
        p.update(format=FORMAT, requires=ENGINE, scene={"background": "#f8f9fc", "grid": True, "decorations": True})
        for r in p["rooms"]:
            fields(r, ["id", "name", "description", "kind", "x", "y", "w", "h", "hours"], "旧区域")
            r["visual"] = default_visual(r["kind"])
        for o in p["objects"]:
            fields(o, ["id", "name", "description", "kind", "room_id", "x", "y", "actions", "portable", "capacity", "servings"], "旧物件")
            if o["kind"] not in KINDS:
                fail("旧物件类型不支持。")
            o["visual"] = {"scale": 100, "rotation": 0, "layer": 0, "show_name": True}
            o["behavior"] = default_behavior(o["kind"])
    fields(p["scene"], ["background", "grid", "decorations"], "场景视觉")
    color(p["scene"]["background"], "场景背景")
    if any(type(p["scene"][k]) is not bool for k in ("grid", "decorations")):
        fail("网格和装饰开关需要是布尔值。")
    fields(p["meta"], ["id", "name", "author", "version", "description", "license", "cover"], "作品资料")
    if p["meta"]["cover"] != "generated-floorplan":
        fail("此版本支持自动平面图封面。")
    ident(p["meta"]["id"], "作品 ID")
    for k in ("name", "author", "license"):
        text(p["meta"][k], k, 120)
    text(p["meta"]["description"], "世界说明", 2000, True)
    if not isinstance(p["meta"]["version"], str) or not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", p["meta"]["version"]):
        fail("版本请使用三个数字，例如 0.1.0。")
    if not isinstance(p["rooms"], list) or not 1 <= len(p["rooms"]) <= 60:
        fail("一个世界需要 1–60 个区域。")
    rooms = {}
    for r in p["rooms"]:
        fields(r, ["id", "name", "description", "kind", "x", "y", "w", "h", "hours", "visual"], "区域")
        v = r["visual"]
        fields(v, ["theme", "floor", "texture", "border", "border_style", "background"], "区域视觉")
        if v["theme"] not in THEMES or v["texture"] not in ("none", "planks", "tiles") or v["border_style"] not in ("solid", "dashed", "none"):
            fail("区域主题、纹理或边框样式不支持。")
        for key in ("floor", "border", "background"):
            color(v[key], "区域" + key)
        ident(r["id"], "区域 ID")
        if r["id"] in rooms:
            fail("区域 ID 重复。")
        rooms[r["id"]] = r
        text(r["name"], "区域名称", 80)
        text(r["description"], "区域说明", 1000, True)
        if r["kind"] not in ("room", "shop", "outdoor"):
            fail("不支持的区域类型。")
        for k, lo, hi in (("x", 0, 900), ("y", 0, 540), ("w", 100, 1000), ("h", 80, 620)):
            integer(r[k], k, lo, hi)
        if r["x"] + r["w"] > 1000 or r["y"] + r["h"] > 620:
            fail(f"{r['name']}超出了地图边界。")
        if r["hours"] is not None:
            fields(r["hours"], ["open", "close"], "开放时间")
            integer(r["hours"]["open"], "开始小时", 0, 23)
            integer(r["hours"]["close"], "结束小时", 0, 23)
            if r["hours"]["open"] == r["hours"]["close"]:
                fail("开放和关闭时间不能相同；全天开放请关闭时间限制。")
    if not isinstance(p["connections"], list) or len(p["connections"]) > 300:
        fail("连接数量不能超过 300。")
    edges = set()
    for e in p["connections"]:
        fields(e, ["from", "to"], "连接")
        if not isinstance(e["from"], str) or not isinstance(e["to"], str) or e["from"] not in rooms or e["to"] not in rooms or e["from"] == e["to"]:
            fail("连接需要指向两个不同的现有区域。")
        key = tuple(sorted((e["from"], e["to"])))
        if key in edges:
            fail("区域连接重复。")
        edges.add(key)
    if not isinstance(p["objects"], list) or len(p["objects"]) > 300:
        fail("物件数量不能超过 300。")
    ids = set()
    for o in p["objects"]:
        fields(o, ["id", "name", "description", "kind", "room_id", "x", "y", "actions", "portable", "capacity", "servings", "visual", "behavior"], "物件")
        ident(o["id"], "物件 ID")
        if o["id"] in ids:
            fail("物件 ID 重复。")
        ids.add(o["id"])
        text(o["name"], "物件名称", 80)
        text(o["description"], "物件说明", 1000, True)
        if not isinstance(o["room_id"], str) or o["room_id"] not in rooms:
            fail("物件所属区域不存在。")
        if o["kind"] not in KINDS:
            fail("不支持的物件类型。")
        fields(o["visual"], ["scale", "rotation", "layer", "show_name"], "物件视觉")
        for key, low, high in (("scale", 40, 200), ("rotation", 0, 359), ("layer", -100, 100)):
            integer(o["visual"][key], key, low, high)
        if type(o["visual"]["show_name"]) is not bool:
            fail("名称显示需要是布尔值。")
        expected = default_behavior(o["kind"])
        fields(o["behavior"], expected.keys(), "行为参数")
        for action in expected:
            fields(o["behavior"][action], PARAMETERS[action].keys(), "行为" + action)
            for key, (_, low, high, label) in PARAMETERS[action].items():
                integer(o["behavior"][action][key], label, low, high)
        integer(o["x"], "物件横向位置", 5, 95)
        integer(o["y"], "物件纵向位置", 15, 90)
        if type(o["portable"]) is not bool:
            fail("可拿取必须是布尔值。")
        integer(o["capacity"], "容量", 0, 20)
        integer(o["servings"], "饮品份数", 0, 20)
        allowed = set(DEFAULT_ACTIONS[o["kind"]])
        if not isinstance(o["actions"], list) or any(not isinstance(a, str) or a not in allowed for a in o["actions"]) or len(set(o["actions"])) != len(o["actions"]):
            fail(f"{o['name']}的用途需要来自该类物件支持的模板。")
        if any(a in o["actions"] for a in ("pick_up", "place")) and not o["portable"]:
            fail(f"{o['name']}设置了拿取或放下用途，需要同时启用可拿取。")
        if any(a in o["actions"] for a in ("store", "retrieve")) and o["capacity"] < 1:
            fail("储物柜需要至少一个储物位置。")
    fields(p["initial"], ["room_id", "time", "resident_name"], "起始设置")
    if not isinstance(p["initial"]["room_id"], str) or p["initial"]["room_id"] not in rooms:
        fail("出生区域不存在。")
    text(p["initial"]["resident_name"], "测试居民名称", 80)
    try:
        if not isinstance(p["initial"]["time"], str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", p["initial"]["time"]):
            fail("起始时间需要是 YYYY-MM-DDTHH:MM。")
        datetime.strptime(p["initial"]["time"], "%Y-%m-%dT%H:%M")
    except ValueError:
        fail("起始时间无效。")
    seen = {p["initial"]["room_id"]}
    while True:
        before = len(seen)
        for a, b in edges:
            if a in seen or b in seen:
                seen.update((a, b))
        if len(seen) == before:
            break
    warnings = [f"{r['name']}无法从出生区域到达。" for r in p["rooms"] if r["id"] not in seen]
    if not is_open(rooms[p["initial"]["room_id"]], start_minute(p)):
        warnings.append("出生区域在起始时间尚未开放，建议调整出生区域或开放时间。")
    return p, warnings


def start_minute(p):
    dt = datetime.strptime(p["initial"]["time"], "%Y-%m-%dT%H:%M")
    return dt.hour * 60 + dt.minute


def cover_svg(package):
    """Portable cover made only from validated content; contains no active SVG."""
    p, _ = validate_package(package)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="720" viewBox="0 0 1000 720">',
           f'<rect width="1000" height="720" fill="{p["scene"]["background"]}"/>',
           f'<text x="32" y="44" font-size="26" fill="#243047" font-family="sans-serif">{html.escape(p["meta"]["name"])}</text>',
           '<text x="32" y="72" font-size="13" fill="#738094" font-family="sans-serif">nyairo · World Studio</text>', '<g transform="translate(0,90)">']
    rooms = {r["id"]: r for r in p["rooms"]}
    for e in p["connections"]:
        a, b = rooms[e["from"]], rooms[e["to"]]
        out.append(f'<path d="M{a["x"]+a["w"]/2} {a["y"]+a["h"]/2}L{b["x"]+b["w"]/2} {b["y"]+b["h"]/2}" stroke="#aebcce" stroke-width="3" stroke-dasharray="8 6"/>')
    for r in p["rooms"]:
        v = r["visual"]
        pattern = f'floor-{r["id"]}'
        if v["texture"] != "none":
            lines = '<path d="M0 0H60M0 30H60M30 0V30M0 30V60"/>' if v["texture"] == "planks" else '<path d="M0 0H30V30H0Z"/>'
            size = 60 if v["texture"] == "planks" else 30
            out.append(f'<defs><pattern id="{pattern}" width="{size}" height="{size}" patternUnits="userSpaceOnUse"><rect width="{size}" height="{size}" fill="{v["floor"]}"/><g fill="none" stroke="{v["border"]}" opacity=".18" stroke-width="1">{lines}</g></pattern></defs>')
        fill = v["floor"] if v["texture"] == "none" else f'url(#{pattern})'
        stroke = 'none' if v["border_style"] == "none" else v["border"]
        dash = ' stroke-dasharray="8 5"' if v["border_style"] == "dashed" else ''
        out.append(f'<rect x="{r["x"]}" y="{r["y"]}" width="{r["w"]}" height="{r["h"]}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>')
        if p["scene"]["decorations"]:
            out.append(f'<rect x="{r["x"]+10}" y="{r["y"]+10}" width="{r["w"]-20}" height="38" rx="5" fill="{v["background"]}"/>')
        label = r["name"][:max(3, min(20, (r["w"]-40)//17))]
        out.append(f'<text x="{r["x"]+18}" y="{r["y"]+30}" fill="#526078" font-size="16" font-family="sans-serif">{html.escape(label)}</text>')
    for o in sorted(p["objects"], key=lambda o: o["visual"]["layer"]):
        r = rooms[o["room_id"]]
        x, y = r["x"] + r["w"]*o["x"]/100, r["y"] + r["h"]*o["y"]/100
        v = o["visual"]
        out.append(f'<g transform="translate({x},{y}) rotate({v["rotation"]}) scale({v["scale"]/100})">{ASSETS[o["kind"]]}</g>')
        if v["show_name"]:
            out.append(f'<text x="{x}" y="{y+32*v["scale"]/100+15}" text-anchor="middle" fill="#526078" font-size="11" font-family="sans-serif">{html.escape(o["name"][:10])}</text>')
    return (''.join(out) + '</g></svg>').encode('utf-8')


def is_open(room, minute):
    h = room["hours"]
    if h is None:
        return True
    current, start, end = (minute // 60) % 24, h["open"], h["close"]
    return start <= current < end if start < end else current >= start or current < end


def initial_state(p):
    return {"revision": 0, "minutes": 0,
            "resident": {"name": p["initial"]["resident_name"], "room_id": p["initial"]["room_id"], "pose": "standing",
                         "energy": 65, "hydration": 60, "held": None, "using": None},
            "objects": {o["id"]: {"room_id": o["room_id"], "placement": "room", "container_id": None,
                                     "read_count": 0, "use_count": 0, "servings": o["servings"]} for o in p["objects"]},
            "events": []}


def apply_action(p, current, command):
    """All actions check prerequisites, then commit one copy of state."""
    if not isinstance(command, dict) or set(command) - {"action", "target", "item", "minutes"}:
        fail("不支持的操作字段。")
    action = command.get("action")
    if not isinstance(action, str) or action not in ACTIONS | {"move", "stand", "wait"}:
        fail("不支持的动作。", "UNKNOWN_ACTION")
    s = copy.deepcopy(current)
    r = s["resident"]
    room_map = {o["id"]: o for o in p["rooms"]}
    object_map = {o["id"]: o for o in p["objects"]}
    minute = start_minute(p) + s["minutes"]
    duration = 1
    target = command.get("target")
    if target is not None and not isinstance(target, str):
        fail("目标 ID 必须是文字。")
    message = ""
    if action == "move":
        if target not in room_map:
            fail("目标区域不存在。", "UNKNOWN_TARGET")
        if r["using"]:
            fail("请先站起，再离开当前家具。", "IN_USE")
        if not any({e["from"], e["to"]} == {r["room_id"], target} for e in p["connections"]):
            fail("两个区域没有直接连接。请先在编辑模式连接它们。", "NOT_REACHABLE")
        if not is_open(room_map[target], minute):
            fail("目标区域当前未开放。可等待开放，或修改世界规则后新建试运行。", "CLOSED")
        r["room_id"] = target
        for state in s["objects"].values():
            if state["placement"] == "held":
                state["room_id"] = target
        message = f"移动到{room_map[target]['name']}，持有物随居民移动。"
        duration = 5
        r["energy"] = max(0, r["energy"] - 2)
    elif action == "stand":
        r["using"] = None
        r["pose"] = "standing"
        message = "站起，释放正在使用的家具。"
    elif action == "wait":
        duration = integer(command.get("minutes", 30), "等待分钟", 1, 1440)
        message = f"世界时间推进 {duration} 分钟。"
    else:
        if target not in object_map:
            fail("目标物件不存在。", "UNKNOWN_TARGET")
        o, os = object_map[target], s["objects"][target]
        if action not in o["actions"]:
            fail("这个物件没有启用此用途。", "NOT_ALLOWED")
        if os["room_id"] != r["room_id"]:
            fail("物件不在当前区域。", "NOT_REACHABLE")
        if not is_open(room_map[r["room_id"]], minute):
            fail("当前区域的使用时间已结束。仍可离开，或等待开放。", "CLOSED")
        if os["placement"] == "container":
            fail("物件已收纳，请先从储物柜取出。", "STORED")
        if action == "pick_up":
            if not o["portable"]:
                fail("这个物件不可拿取。", "NOT_TAKEABLE")
            if r["held"] is not None:
                fail("手中已经有物件，请先放下或收纳。", "OCCUPIED")
            if os["placement"] != "room":
                fail("物件已被持有。", "OCCUPIED")
            r["held"] = target
            os["placement"] = "held"
            message = f"拿起{o['name']}；物件现在由测试居民持有。"
        elif action == "place":
            if r["held"] != target:
                fail("只能放下当前持有的物件。", "NOT_HELD")
            r["held"] = None
            os["placement"] = "room"
            message = f"把{o['name']}放到当前区域。"
        elif action in ("sit", "rest"):
            if r["using"] is not None:
                fail("请先站起，释放当前家具。", "OCCUPIED")
            r["using"] = target
            r["pose"] = "sitting" if action == "sit" else "lying"
            os["use_count"] += 1
            if action == "rest":
                duration = o["behavior"]["rest"]["minutes"]
                r["energy"] = min(100, r["energy"] + o["behavior"]["rest"]["energy_gain"])
            message = f"{'坐在' if action == 'sit' else '躺在'}{o['name']}上" + (f"，休息 {duration} 分钟后精力为 {r['energy']}。" if action == "rest" else "，家具占用已登记。")
        elif action == "read":
            if r["held"] != target:
                fail("需要先拿起这本书，才能阅读。", "NOT_HELD")
            duration = o["behavior"]["read"]["minutes"]
            os["read_count"] += 1
            r["energy"] = max(0, r["energy"] - o["behavior"]["read"]["energy_cost"])
            message = f"阅读{o['name']} {duration} 分钟；阅读次数变为 {os['read_count']}，精力为 {r['energy']}。"
        elif action == "drink":
            if r["held"] != target:
                fail("需要先拿起杯子。", "NOT_HELD")
            if os["servings"] <= 0:
                fail("杯中已经没有饮品。", "EMPTY")
            os["servings"] -= 1
            r["hydration"] = min(100, r["hydration"] + o["behavior"]["drink"]["hydration_gain"])
            message = f"喝了一份{o['name']}，杯中还剩 {os['servings']} 份，水分为 {r['hydration']}。"
        elif action == "store":
            held = r["held"]
            if not held:
                fail("需要先拿起待收纳的物件。", "NOT_HELD")
            if sum(v["container_id"] == target for v in s["objects"].values()) >= o["capacity"]:
                fail("储物柜已满。", "OCCUPIED")
            s["objects"][held].update(placement="container", container_id=target)
            r["held"] = None
            message = f"将{object_map[held]['name']}收纳到{o['name']}。"
        elif action == "retrieve":
            item = command.get("item")
            if not isinstance(item, str) or item not in object_map or s["objects"][item]["container_id"] != target:
                fail("请选择这个柜子里实际存放的物件。", "UNKNOWN_TARGET")
            if r["held"]:
                fail("手中已有物件，请先放下。", "OCCUPIED")
            s["objects"][item].update(placement="held", container_id=None)
            r["held"] = item
            message = f"从{o['name']}取出{object_map[item]['name']}。"
    s["minutes"] += duration
    if s["minutes"] > 5256000:
        fail("本次试运行超过支持的时间范围，请新建试运行。")
    s["revision"] += 1
    event = {"revision": s["revision"], "minutes": s["minutes"], "action": action, "target": target, "message": message}
    s["events"] = (s["events"] + [event])[-200:]
    return s, event


def validate_save(raw):
    fields(raw, ["format", "package", "package_hash", "state"], "试运行存档")
    if raw["format"] not in (SAVE_FORMAT, LEGACY_SAVE):
        fail("存档格式不兼容。", "INCOMPATIBLE")
    if not isinstance(raw["package"], dict):
        fail("存档内容包需要是对象。")
    if (raw["format"] == LEGACY_SAVE and raw["package"].get("format") != LEGACY_FORMAT) or (raw["format"] == SAVE_FORMAT and raw["package"].get("format") != FORMAT):
        fail("存档与世界包格式版本不一致。", "INCOMPATIBLE")
    if digest(raw["package"]) != raw["package_hash"]:
        fail("存档的内容包摘要不匹配。", "CORRUPT")
    p, _ = validate_package(raw["package"])
    s = raw["state"]
    fields(s, ["revision", "minutes", "resident", "objects", "events"], "居民状态")
    integer(s["revision"], "存档版本", 0, 10000000)
    integer(s["minutes"], "经过分钟", 0, 5256000)
    r = s["resident"]
    fields(r, ["name", "room_id", "pose", "energy", "hydration", "held", "using"], "居民")
    rooms = {o["id"] for o in p["rooms"]}
    objects = {o["id"]: o for o in p["objects"]}
    text(r["name"], "居民名称", 80)
    if not isinstance(r["room_id"], str) or r["room_id"] not in rooms or r["pose"] not in ("standing", "sitting", "lying"):
        fail("居民的位置或姿态无效。")
    for k in ("energy", "hydration"):
        integer(r[k], k, 0, 100)
    for key in ("held", "using"):
        if r[key] is not None and (not isinstance(r[key], str) or r[key] not in objects):
            fail("居民引用了不存在的物件。")
    if not isinstance(s["objects"], dict) or set(s["objects"]) != set(objects):
        fail("存档物件与内容包不一致。")
    held = []
    for oid, os in s["objects"].items():
        fields(os, ["room_id", "placement", "container_id", "read_count", "use_count", "servings"], "物件状态")
        if not isinstance(os["room_id"], str) or os["room_id"] not in rooms or os["placement"] not in ("room", "held", "container"):
            fail("物件的位置无效。")
        for k in ("read_count", "use_count"):
            integer(os[k], k, 0, 10000000)
        integer(os["servings"], "饮品份数", 0, objects[oid]["servings"])
        if os["placement"] == "held":
            if not objects[oid]["portable"] or os["room_id"] != r["room_id"]:
                fail("持有物的属性或位置不一致。")
            held.append(oid)
        if os["placement"] == "container":
            cid = os["container_id"]
            if not isinstance(cid, str) or cid not in objects or cid == oid or objects[cid]["kind"] != "cabinet" or not objects[oid]["portable"]:
                fail("储物关系无效。")
            if os["room_id"] != s["objects"][cid]["room_id"] or s["objects"][cid]["placement"] != "room":
                fail("柜子和收纳物件的位置不一致。")
        elif os["container_id"] is not None:
            fail("非收纳物件不能引用储物柜。")
    if held != ([r["held"]] if r["held"] else []):
        fail("手中物件与物件持有状态不一致。")
    if r["using"]:
        used = objects[r["using"]]
        state = s["objects"][r["using"]]
        action = "sit" if r["pose"] == "sitting" else "rest" if r["pose"] == "lying" else None
        if action not in used["actions"] or state["room_id"] != r["room_id"] or state["placement"] != "room":
            fail("姿态和家具占用关系不一致。")
    elif r["pose"] != "standing":
        fail("坐下或躺下需要对应家具。")
    for oid, o in objects.items():
        if sum(v["container_id"] == oid for v in s["objects"].values()) > o["capacity"]:
            fail("储物容量超限。")
    if not isinstance(s["events"], list) or len(s["events"]) > 200:
        fail("存档事件数量超限。")
    previous = 0
    previous_minutes = 0
    for e in s["events"]:
        fields(e, ["revision", "minutes", "action", "target", "message"], "事件")
        integer(e["revision"], "事件版本", 1, s["revision"])
        integer(e["minutes"], "事件时间", previous_minutes, s["minutes"])
        if e["revision"] <= previous or e["action"] not in ACTIONS | {"move", "stand", "wait"}:
            fail("事件顺序或动作无效。")
        if e["target"] is not None and (not isinstance(e["target"], str) or e["target"] not in rooms | set(objects)):
            fail("事件目标不存在。")
        text(e["message"], "事件说明", 500)
        previous, previous_minutes = e["revision"], e["minutes"]
    if s["events"] and (previous != s["revision"] or previous_minutes != s["minutes"]):
        fail("最后事件与存档版本或时间不一致。")
    if s["revision"] > 0 and not s["events"]:
        fail("非初始存档缺少事件记录。")
    return p, copy.deepcopy(s)
