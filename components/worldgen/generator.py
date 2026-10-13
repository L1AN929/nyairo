#!/usr/bin/env python3
"""确定性随机世界生成器。

程序负责空间与规则（拓扑、连接、步行耗时、营业时间、NPC 出场、环境事件），
LLM 只负责内容丰富度（可选）；同一种子 + 模板 + 生成器版本产生的
基础结构完全一致。

生成策略：

* 主街 + 支街构成路网骨架；每个功能区挂在某条街上；
* 每个非街道地点都连接到其所属区的街道，街道之间互联；
* 住宅固定为 ``home``（内部区域 ``bedroom``），学校固定为 ``school``，
  与 rc9 既有世界模块的规范 ID 保持一致；其余 ID 由模板决定；
* 步行耗时由地图坐标距离确定，地图坐标由种子抖动。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Optional

from . import GENERATOR_VERSION
from . import schema
from .names import compose_description, compose_name, compose_world_name, pick_weighted

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
AVAILABLE_THEMES = ("coastal_town", "residential_town", "mountain_town", "rural_farming")

CANVAS_WIDTH = 1000.0
CANVAS_HEIGHT = 700.0
#: 地图坐标 100 单位 ≈ 30 秒步行，外加固定的出门/过路口开销。
WALK_SECONDS_PER_UNIT = 0.30
WALK_SECONDS_BASE = 90
WALK_SECONDS_MIN = 60
WALK_SECONDS_MAX = 900

_STREET_ANCHORS = [
    (0.50, 0.50),  # 主街：镇中心
    (0.25, 0.70),
    (0.75, 0.28),
    (0.75, 0.72),
    (0.25, 0.28),
]


def load_theme(theme_id: str) -> dict[str, Any]:
    if theme_id not in AVAILABLE_THEMES:
        raise ValueError(f"未知主题 {theme_id!r}，可用主题: {list(AVAILABLE_THEMES)}")
    return json.loads((TEMPLATES_DIR / f"{theme_id}.json").read_text(encoding="utf-8"))


def _slugify_streets(rng: random.Random, template: dict[str, Any], count: int) -> list[dict[str, Any]]:
    names = list(template["street_names"])
    rng.shuffle(names)
    streets = []
    for index in range(count):
        streets.append(
            {
                "location_id": f"street_{index + 1}",
                "name": names[index % len(names)] if index < len(names) else f"{names[0]}{index + 1}",
                "kind": "street",
                "zone": None,
            }
        )
    return streets


def _walk_seconds(a: dict[str, Any], b: dict[str, Any]) -> int:
    dx = a["map"]["x"] - b["map"]["x"]
    dy = a["map"]["y"] - b["map"]["y"]
    distance = (dx * dx + dy * dy) ** 0.5
    seconds = int(WALK_SECONDS_BASE + distance * WALK_SECONDS_PER_UNIT)
    return max(WALK_SECONDS_MIN, min(WALK_SECONDS_MAX, seconds))


def _jitter(rng: random.Random, center: tuple[float, float], radius: float = 70.0) -> dict[str, float]:
    return {
        "x": round(min(CANVAS_WIDTH - 40, max(40, center[0] + rng.uniform(-radius, radius))), 1),
        "y": round(min(CANVAS_HEIGHT - 40, max(40, center[1] + rng.uniform(-radius, radius))), 1),
    }


def generate_topology(
    rng: random.Random,
    template: dict[str, Any],
    world_name: str,
    *,
    target_venue_count: Optional[int] = None,
    proposal: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """生成地点、区域、连接与步行耗时。"""
    street_count = rng.randint(2, 3)
    zone_defs = list(template["zones"])
    rng.shuffle(zone_defs)
    streets = _slugify_streets(rng, template, street_count)

    # 街道锚点：主街固定镇中心，支街依次取周围锚点，保证连线不交叉感。
    street_nodes: dict[str, dict[str, Any]] = {}
    for index, street in enumerate(streets):
        anchor = _STREET_ANCHORS[index]
        node = {
            "location_id": street["location_id"],
            "name": street["name"],
            "kind": "street",
            "area_id": street["location_id"],
            "description": f"{street['name']}，镇上的主要街道之一。",
            "zone_id": "streets",
            "map": {
                "x": round(anchor[0] * CANVAS_WIDTH, 1),
                "y": round(anchor[1] * CANVAS_HEIGHT, 1),
            },
        }
        street_nodes[street["location_id"]] = node

    # 区 → 街道：按顺序轮转分配，让每个区都有归属街道。
    zone_street: dict[str, str] = {}
    for index, zone in enumerate(zone_defs):
        zone_street[zone["zone_id"]] = streets[index % street_count]["location_id"]

    # 选地点池：固定槽位 + 加权抽取（GLM 提案可加权部分槽位）。
    venue_slots = list(template["fixed_slots"])
    pool = list(template["slot_pool"])
    if proposal and proposal.get("building_emphasis"):
        for slot in pool:
            weight = proposal["building_emphasis"].get(slot.get("id"))
            if weight:
                slot["weight"] = max(1, int(slot.get("weight", 1)) * int(weight))
    budget = target_venue_count
    if budget is None:
        budget = rng.randint(9, 16)
    budget = max(1, min(budget, len(pool)))
    picked = pick_weighted(rng, pool, budget)
    venue_slots.extend(picked)

    zone_centers: dict[str, tuple[float, float]] = {}
    zone_list = [zone["zone_id"] for zone in template["zones"]]
    zone_anchors = [(0.20, 0.20), (0.80, 0.20), (0.20, 0.80), (0.80, 0.80)]
    for index, zone_id in enumerate(zone_list):
        anchor = zone_anchors[index % len(zone_anchors)]
        zone_centers[zone_id] = (anchor[0] * CANVAS_WIDTH, anchor[1] * CANVAS_HEIGHT)

    prefix_pool = template["world_name_fragments"]["prefix"]
    locations: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    rules: dict[str, Any] = {}

    for slot in venue_slots:
        location_id = slot["id"]
        zone_id = slot.get("zone") or "residential"
        home_street = zone_street.get(zone_id, streets[0]["location_id"])
        if location_id == "home":
            # 住宅挂在住宅区街道上；坐标围绕所属街道附近抖动。
            anchor = street_nodes[home_street]["map"]
            map_pos = _jitter(rng, (anchor["x"] + 90, anchor["y"] - 90), 40)
        elif location_id == "school":
            anchor = street_nodes[home_street]["map"]
            map_pos = _jitter(rng, (anchor["x"] - 120, anchor["y"] + 60), 50)
        else:
            center = zone_centers.get(zone_id, zone_centers[zone_list[0]])
            map_pos = _jitter(rng, center)
        name = compose_name(rng, slot.get("names") or [zone_id], world_name=world_name, prefix_pool=prefix_pool)
        description = compose_description(rng, slot.get("descriptions") or ["一座普通的镇上设施。"])
        location = {
            "location_id": location_id,
            "name": name,
            "kind": slot["kind"],
            "area_id": "bedroom" if location_id == "home" else location_id,
            "description": description,
            "reachable": [],
            "map": map_pos,
            "zone_id": zone_id,
        }
        if location_id == "home":
            location["rooms"] = [
                {
                    "room_id": "bedroom",
                    "name": "自己的房间",
                    "description": "自己的房间，有一张床和一张书桌。",
                }
            ]
        locations.append(location)

        open_raw, close_raw = slot.get("open_hour"), slot.get("close_hour")
        open_hour = rng.randint(*open_raw) if isinstance(open_raw, list) else open_raw
        close_hour = rng.randint(*close_raw) if isinstance(close_raw, list) else close_raw
        affordances = [
            {"affordance_id": item[0], "description": item[1]} for item in slot.get("affordances", [])
        ]
        rules[location_id] = {
            "open_hour": open_hour,
            "close_hour": close_hour,
            "weekdays_only": bool(slot.get("weekdays_only", False)),
            "affordances": affordances,
        }
    # 连接：街道星形互联 + 各区地点挂街道 + 住宅挂街道。
    main_street = streets[0]["location_id"]
    for street in streets[1:]:
        edges.append(
            {
                "from": main_street,
                "to": street["location_id"],
                "walk_seconds": _walk_seconds(street_nodes[main_street], street_nodes[street["location_id"]]),
            }
        )
    street_ids = [street["location_id"] for street in streets]
    for location in locations:
        zone_id = location["zone_id"]
        home_street_id = zone_street.get(zone_id, main_street)
        if home_street_id not in street_ids:  # pragma: no cover - 模板错误防护
            home_street_id = main_street
        edges.append(
            {
                "from": location["location_id"],
                "to": home_street_id,
                "walk_seconds": _walk_seconds(location, street_nodes[home_street_id]),
            }
        )
    # 少量区内捷径，让路线选择更有意思。
    shortcut_budget = rng.randint(0, 2)
    for _ in range(shortcut_budget):
        zone_id = rng.choice(zone_list)
        zone_members = [loc for loc in locations if loc["zone_id"] == zone_id]
        if len(zone_members) < 2:
            continue
        first, second = rng.sample(zone_members, 2)
        pair = {first["location_id"], second["location_id"]}
        if any({edge["from"], edge["to"]} == pair for edge in edges):
            continue
        edges.append(
            {
                "from": first["location_id"],
                "to": second["location_id"],
                "walk_seconds": _walk_seconds(first, second),
            }
        )

    # reachable 由边对称推导。
    adjacency: dict[str, list[str]] = {loc["location_id"]: [] for loc in locations}
    adjacency.update({street_id: [] for street_id in street_ids})
    for edge in edges:
        adjacency[edge["from"]].append(edge["to"])
        adjacency[edge["to"]].append(edge["from"])
    all_locations = locations + [street_nodes[street_id] for street_id in street_ids]
    for location in all_locations:
        location["reachable"] = sorted(adjacency[location["location_id"]])
    # 街道也要有规则（street: 全天开放，可散步）。
    street_rule = {
        "open_hour": None,
        "close_hour": None,
        "weekdays_only": False,
        "affordances": [
            {"affordance_id": item[0], "description": item[1]}
            for item in template["street_slot"]["affordances"]
        ],
    }
    for street in streets:
        rules[street["location_id"]] = dict(street_rule)

    return {
        "locations": all_locations,
        "edges": edges,
        "location_rules": rules,
        "zone_names": {zone["zone_id"]: zone["name"] for zone in template["zones"]},
        "zone_street": zone_street,
    }


def build_seed_material(seed: int, theme: str) -> str:
    return f"nyairo-worldgen|{GENERATOR_VERSION}|{theme}|{seed}"


def generate_world(
    *,
    seed: int,
    theme: str,
    world_name: Optional[str] = None,
    timezone_name: str = "Asia/Tokyo",
    target_venue_count: Optional[int] = None,
    proposal: Optional[dict[str, Any]] = None,
) -> dict[str, dict[str, Any]]:
    """生成一个完整世界包文档集合（未写盘）。

    ``proposal``（v0.4）是 GLM 世界设计提案（见 providers.glm.propose_world）：
    程序据此调整槽位权重、世界描述、环境事件候选与 NPC 作息——提案不承担
    拓扑权威，未知槽位建议被丢弃，几何与合法性仍完全由程序决定。
    """
    template = load_theme(theme)
    rng = random.Random(build_seed_material(seed, theme))
    name = world_name or compose_world_name(rng, template["world_name_fragments"])
    # 防御性去重：world_id 只允许 ASCII slug。
    slug = re.sub(r"[^a-z0-9]+", "_", theme.lower()).strip("_")
    world_id = f"{slug}_{seed}"

    topology = generate_topology(
        rng, template, name,
        target_venue_count=target_venue_count, proposal=proposal,
    )
    location_ids = {loc["location_id"] for loc in topology["locations"]}
    street_ids = [loc["location_id"] for loc in topology["locations"] if loc["kind"] == "street"]

    # ---- NPC -------------------------------------------------------------
    npc_pool = [role for role in template["npc_role_pool"] if role["slot"] in location_ids]
    rng.shuffle(npc_pool)
    npc_count = min(len(npc_pool), rng.randint(3, 6))
    used_names: set[str] = set()
    npcs: list[dict[str, Any]] = []
    name_pool = template["npc_name_pool"]
    npc_index = 0
    for role in npc_pool:
        if len(npcs) >= npc_count:
            break
        for _attempt in range(24):
            family = rng.choice(name_pool["family"])
            given = rng.choice(name_pool["given_adult" if role["age"] == "adult" else "given_minor"])
            display_name = f"{family}{given}"
            if display_name not in used_names:
                used_names.add(display_name)
                break
        else:  # pragma: no cover - 名字池耗尽
            display_name = f"{name_pool['family'][0]}{npc_index}"
        npc_index += 1
        slot_location = role["slot"]
        # 工作窗口与营业时间对齐（一致性：店员不会在打烊时段上班）。
        rule = topology["location_rules"].get(slot_location, {})
        open_hour = rule.get("open_hour")
        close_hour = rule.get("close_hour")
        start_hour = role["start_hour"]
        end_hour = role["end_hour"]
        if open_hour is not None:
            start_hour = max(start_hour, open_hour)
            end_hour = min(end_hour, close_hour if close_hour is not None else end_hour)
        if end_hour <= start_hour:
            start_hour, end_hour = role["start_hour"], role["end_hour"]
        windows = [
            {
                "weekdays": list(role["weekdays"]),
                "start_minute": start_hour * 60,
                "end_minute": end_hour * 60,
                "location_id": slot_location,
            }
        ]
        # 下班后/休息日的公共生活窗口（成人：公园/街道/咖啡馆）。
        if role["age"] == "adult" and rng.random() < 0.7:
            public_spots = [
                loc["location_id"]
                for loc in topology["locations"]
                if loc["kind"] in ("park", "street", "cafe") and loc["location_id"] != slot_location
            ]
            if public_spots:
                if rng.random() < 0.5:
                    windows.append(
                        {
                            "weekdays": [6, 7],
                            "start_minute": rng.choice((10, 11, 14)) * 60,
                            "end_minute": rng.choice((16, 17)) * 60,
                            "location_id": rng.choice(public_spots),
                        }
                    )
                else:
                    windows.append(
                        {
                            "weekdays": [1, 2, 3, 4, 5],
                            "start_minute": min(22, end_hour + 1) * 60,
                            "end_minute": min(23, end_hour + 2) * 60,
                            "location_id": rng.choice(public_spots),
                        }
                    )
        npcs.append(
            {
                "npc_id": f"{theme.split('_')[0]}_npc_{slot_location}_{npc_index:02d}",
                "display_name": display_name,
                "age_classification": role["age"],
                "public_role": role["role"],
                "home_base_area": slot_location,
                "public_description": f"{name}的{role['role']}。",
                "windows": windows,
            }
        )

    # ---- 环境事件 ----------------------------------------------------------
    ambient_candidates = [
        item
        for item in template["ambient_pool"]
        if item["slot"] == "home" or item["slot"] in location_ids or item["slot"] == "street"
    ]
    rng.shuffle(ambient_candidates)
    ambient_templates = []
    for item in ambient_candidates[:6]:
        target_location = (
            rng.choice(street_ids)
            if item["slot"] == "street"
            else ("home" if item["slot"] == "home" else item["slot"])
        )
        ambient_templates.append(
            {
                "event_type": item["event_type"],
                "location_id": target_location,
                "summary": rng.choice(item["summaries"]),
                "start_hour": item["start_hour"],
                "end_hour": item["end_hour"],
            }
        )
    # GLM 提案的公共活动 → 确定性事件候选（挂同主题地点）。
    if proposal and proposal.get("public_activities"):
        import hashlib as _hashlib

        kind_by_slot = {}
        for loc in topology["locations"]:
            kind_by_slot.setdefault(loc["kind"], loc["location_id"])
        hint_map = {"便利店": "convenience_store", "学校": "school", "公园": "park",
                    "书店": "bookstore", "咖啡": "cafe", "集市": "market",
                    "车站": "station", "神社": "shrine"}
        for activity in proposal["public_activities"][:4]:
            slug = "local_" + _hashlib.sha1(activity.encode("utf-8")).hexdigest()[:8]
            target = kind_by_slot.get("park") or next(iter(kind_by_slot.values()))
            for hint, kind in hint_map.items():
                if hint in activity and kind_by_slot.get(kind):
                    target = kind_by_slot[kind]
                    break
            ambient_templates.append(
                {"event_type": slug, "location_id": target,
                 "summary": activity, "start_hour": 0, "end_hour": 24}
            )
    # 至少保证一条 home 事件，观察里始终有生活气息。
    if not any(item["location_id"] == "home" for item in ambient_templates):
        for item in template["ambient_pool"]:
            if item["slot"] == "home":
                ambient_templates.append(
                    {
                        "event_type": item["event_type"],
                        "location_id": "home",
                        "summary": rng.choice(item["summaries"]),
                        "start_hour": item["start_hour"],
                        "end_hour": item["end_hour"],
                    }
                )
                break

    # ---- 对象目录 ----------------------------------------------------------
    objects = []
    for item in template["object_pool"]:
        location_id = item["location"] if item["location"] in location_ids else "home"
        objects.append(
            {
                "object_id": item["object_id"],
                "name": item["name"],
                "kind": item["kind"],
                "location_id": location_id,
                "note": item["note"],
            }
        )

    # ---- 本地内容（无 LLM 时的完整可运行内容） -----------------------------
    flavor = template["flavor_pool"]
    world_tagline = rng.choice(flavor["world_intro"])
    if proposal and proposal.get("npc_rhythm"):
        world_tagline = f"{world_tagline}{proposal['npc_rhythm']}"
    entries: dict[str, Any] = {
        "world": {
            "tagline": world_tagline,
            "festivals": rng.sample(flavor["festival_candidates"], k=min(2, len(flavor["festival_candidates"]))),
        }
    }
    if proposal and proposal.get("area_life"):
        for zone_name, life in proposal["area_life"].items():
            entries.setdefault("world", {}).setdefault("zone_life", []).append(f"{zone_name}：{life}")
    # v0.4 改进（批量实验 6.4）：intro 注入种子化风味句，避免同主题不同
    # 种子的介绍完全相同（本地模板模式的重复率问题在 40 世界实验中实测）。
    zone_flavor = flavor.get("zone_flavor", [])
    zone_items = (
        [item for items in zone_flavor.values() for item in items]
        if isinstance(zone_flavor, dict)
        else list(zone_flavor)
    )
    flavor_sentences = list(flavor["street_flavor"]) + zone_items
    # v0.4 第三轮改进（GLM 评审反馈）：风味句在世界内洗牌后逐地点分配，
    # 不重复使用，避免"杂货店冰柜"同时出现在书店与海滩。
    rng.shuffle(flavor_sentences)
    flavor_cursor = {"index": 0}

    def next_flavor() -> str:
        if not flavor_sentences:
            return ""
        sentence = flavor_sentences[flavor_cursor["index"] % len(flavor_sentences)]
        flavor_cursor["index"] += 1
        return sentence
    for location in topology["locations"]:
        intro = location["description"]
        if flavor_sentences and rng.random() < 0.65:
            flavor_bit = next_flavor()
            if flavor_bit and flavor_bit not in intro:
                intro = f"{intro}{flavor_bit}。"
        entry: dict[str, Any] = {"intro": intro}
        if location["kind"] == "street":
            entry["flavor"] = rng.choice(flavor["street_flavor"])
        entries[location["location_id"]] = entry
    for npc in npcs:
        entries[npc["npc_id"]] = {"public_note": f"{npc['display_name']}是这里的{npc['public_role']}。"}

    description = f"{name}——{template['theme_name']}主题的生成世界。"
    if proposal and proposal.get("style"):
        description = f"{name}——{proposal['style']}"
        if proposal.get("culture"):
            description += proposal["culture"]
    world_doc = {
        "schema_version": schema.WORLD_SCHEMA,
        "world_id": world_id,
        "name": name,
        "theme": theme,
        "seed": seed,
        "timezone": timezone_name,
        "generator_version": GENERATOR_VERSION,
        "description": description,
    }
    topology_doc = {
        "schema_version": schema.TOPOLOGY_SCHEMA,
        "locations": topology["locations"],
        "edges": topology["edges"],
    }
    rules_doc = {
        "schema_version": schema.RULES_SCHEMA,
        "location_rules": topology["location_rules"],
        "weather_candidates": list(template["weather"]),
    }
    npcs_doc = {"schema_version": schema.NPCS_SCHEMA, "npcs": npcs}
    ambient_doc = {"schema_version": schema.AMBIENT_SCHEMA, "templates": ambient_templates}
    objects_doc = {"schema_version": schema.OBJECTS_SCHEMA, "objects": objects}
    content_doc = {
        "schema_version": schema.CONTENT_SCHEMA,
        "source": "local",
        "model": None,
        "entries": entries,
    }

    docs = {
        "world.json": world_doc,
        "topology.json": topology_doc,
        "rules.json": rules_doc,
        "npcs.json": npcs_doc,
        "ambient.json": ambient_doc,
        "objects.json": objects_doc,
        "content.json": content_doc,
    }
    # 生成即自检：格式问题在写盘前就暴露。
    for name_key, validator in schema.DOC_VALIDATORS.items():
        docs[name_key] = validator(docs[name_key])
    return docs


__all__ = [
    "AVAILABLE_THEMES",
    "build_seed_material",
    "generate_topology",
    "generate_world",
    "load_theme",
]
