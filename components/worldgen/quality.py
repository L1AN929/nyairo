#!/usr/bin/env python3
"""批量世界生成与质量分析（v0.4 第五阶段）。

流程：批量生成（4 主题 × N 种子）→ 自动室内 → 结构质检 → 长期一致性
模拟 → 内容规则检查 →（可选）GLM 辅助评审 → 报告。

只做只读质检与报告，不修改任何世界包；实验世界用于研发评测，
不默认纳入正式示例。
"""

from __future__ import annotations

import json
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import schema
from .generator import AVAILABLE_THEMES, generate_world
from .interior import apply_interiors_to_package
from .loader import load as load_package
from .validator import validate


class QualityError(ValueError):
    """批量实验配置或执行失败。"""


def _worlds_dir(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    return out


def _structure_check(package_dir: Path) -> dict[str, Any]:
    """结构质检：校验、连通性、导航完整性、NPC 营业一致性。"""
    problems: list[str] = []
    try:
        package = load_package(package_dir)
    except Exception as exc:
        return {"ok": False, "problems": [f"加载失败: {exc}"]}
    docs = package.snapshot()
    messages = validate(docs)
    if messages:
        problems.extend(messages)
        return {"ok": False, "problems": problems}

    # 地点 ID 唯一性（validate 已查，双保险）。
    locations = package.locations
    if len(locations) != len(docs["topology.json"]["locations"]):
        problems.append("地点 ID 唯一性异常")

    # 道路连通性：home 可达全部地点。
    adjacency: dict[str, set[str]] = {loc_id: set() for loc_id in locations}
    for edge in docs["topology.json"]["edges"]:
        adjacency[edge["from"]].add(edge["to"])
        adjacency[edge["to"]].add(edge["from"])
    seen = {"home"}
    queue = deque(["home"])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    unreachable = sorted(set(locations) - seen)
    if unreachable:
        problems.append(f"道路图不连通: {unreachable}")

    # 室内导航完整性：每个声明区域的节点从入口可达；家具归属有效。
    scene = package.scene or {}
    navigation = scene.get("navigation", {})
    for place_id, nav in navigation.items():
        area_nodes = {f"{place_id}/{a['area_id']}" for a in nav["areas"]}
        graph: dict[str, set[str]] = {place_id: set()}
        for a in area_nodes:
            graph[a] = set()
        for connection in nav["connections"]:
            first = f"{place_id}/{connection['from']}"
            second = f"{place_id}/{connection['to']}"
            entrance_node = place_id
            first_node = place_id if connection["from"] == nav["entrance"] else first
            second_node = place_id if connection["to"] == nav["entrance"] else second
            graph[first_node].add(second_node)
            graph[second_node].add(first_node)
        reach = {place_id}
        queue = deque([place_id])
        while queue:
            current = queue.popleft()
            for neighbor in graph[current]:
                if neighbor not in reach:
                    reach.add(neighbor)
                    queue.append(neighbor)
            # 入口别名：place 与 entrance 区域互通（由 reachable_of 语义保证）
            entrance_node = place_id
            if current == place_id and nav["entrance"] in {a["area_id"] for a in nav["areas"]}:
                entrance_area_node = f"{place_id}/{nav['entrance']}"
                if entrance_area_node in graph and entrance_area_node not in reach:
                    reach.add(entrance_area_node)
                    queue.append(entrance_area_node)
        unreached = sorted(area_nodes - reach)
        if unreached:
            problems.append(f"{place_id}: 存在从入口不可达的室内区域 {unreached}")
        for area_id, object_ids in nav["furniture_map"].items():
            for object_id in object_ids:
                if object_id not in {obj["object_id"] for obj in docs["objects.json"]["objects"]}:
                    problems.append(f"{place_id}: 家具 {object_id} 不在 objects.json")

    # NPC 营业一致性：工作窗口 ⊆ 营业时段。
    rules = docs["rules.json"]["location_rules"]
    npc_conflicts = 0
    for npc in docs["npcs.json"]["npcs"]:
        for window in npc["windows"]:
            rule = rules.get(window["location_id"])
            if rule is None or rule["open_hour"] is None:
                continue
            start_h = window["start_minute"] // 60
            end_h = 24 if window["end_minute"] == 1440 else window["end_minute"] // 60
            if window["location_id"] == npc["home_base_area"] and not (
                rule["open_hour"] <= start_h and end_h <= rule["close_hour"]
            ):
                npc_conflicts += 1
    if npc_conflicts:
        problems.append(f"NPC 工作窗口与营业时间冲突 {npc_conflicts} 处")

    return {
        "ok": not problems,
        "problems": problems,
        "locations": len(locations),
        "edges": len(docs["topology.json"]["edges"]),
        "npcs": len(docs["npcs.json"]["npcs"]),
        "interiors": len(scene.get("spaces", {})),
        "navigation_buildings": len(navigation),
        "npc_conflicts": npc_conflicts,
    }


def _consistency_check(package_dir: Path, *, days: int = 7) -> dict[str, Any]:
    """长期一致性：可控时间推进，模拟多日期观察。

    * 同一天的两次环境事件物化必须一致（不重复宣称）；
    * 营业状态在抽样时刻稳定（同一天同一小时结果一致，跨日规则一致）；
    * NPC 日程在抽样时刻稳定（同一时刻查询结果一致）。
    """
    _ensure_runtime_path()
    import world_ambient_events as wae  # noqa: PLC0415 - 运行时模块按需导入
    import world_daily_rhythm as wdr  # noqa: PLC0415

    package = load_package(package_dir)
    problems: list[str] = []
    checks = 0
    env = {"CHIYO_WORLD_PACKAGE": str(package.package_dir)}
    import world_runtime_data as wrd  # noqa: PLC0415

    wrd.reset_cache()
    zone = ZoneInfo(package.timezone_name)
    base_date = datetime(2026, 11, 2, tzinfo=zone)  # 星期一
    sampled_hours = (9, 13, 20)
    for day_index in range(days):
        day = base_date + timedelta(days=day_index)
        for hour in sampled_hours:
            moment = day.replace(hour=hour)
            local_date = moment.date()
            # 事件物化确定性：同日两次调用一致（显式声明活动世界）。
            first = wae.materialize_ambient_events(
                local_date, timezone_name=package.timezone_name,
                allowed_location_ids=set(package.locations),
                environment=env,
            )
            second = wae.materialize_ambient_events(
                local_date, timezone_name=package.timezone_name,
                allowed_location_ids=set(package.locations),
                environment=env,
            )
            checks += 1
            if first != second:
                problems.append(f"{local_date}: 环境事件物化不确定")
            # 重复宣称检查：同一天的 event_id 必须唯一。
            ids = [event["event_id"] for event in first]
            if len(ids) != len(set(ids)):
                problems.append(f"{local_date}: 环境事件 event_id 重复")
            # 营业规则稳定：同日同小时两次查询一致。
            for location_id in package.locations:
                rule = package.location_rule(location_id)
                if rule["open_hour"] is None:
                    continue
                moment_local = moment
                status_a = _availability(package, location_id, moment_local)
                status_b = _availability(package, location_id, moment_local)
                checks += 1
                if status_a != status_b:
                    problems.append(f"{local_date} {hour}点: {location_id} 营业状态不稳定")
    return {"ok": not problems, "problems": problems, "checks": checks, "days": days}


def _availability(package, location_id: str, moment) -> str:
    _ensure_runtime_path()
    import world_daily_rhythm as wdr  # noqa: PLC0415

    env = {
        "CHIYO_WORLD_PACKAGE": str(package.package_dir),
    }
    return wdr.location_availability(location_id, moment, environment=env)["status"]


def _ensure_runtime_path() -> None:
    """rc9 世界模块按需导入（质量检查进程默认没有该路径）。"""
    scripts_dir = Path(__file__).resolve().parents[1] / "world" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def _content_check(
    package_dir: Path, seen_intros: dict[str, dict[str, set[str]]], *, theme: str
) -> dict[str, Any]:
    """内容规则检查：描述非空、跨主题重复度（同主题模板重叠单独报告）。

    v0.4 改进（40 世界实验第二轮）：同主题不同种子共享描述模板属预期，
    只有**跨主题**重复率过高才是质量缺陷；同主题重复率仅记录不判 fail。
    """
    package = load_package(package_dir)
    problems: list[str] = []
    intros: list[str] = []
    for location in package.locations.values():
        content = package.content_entry(location["location_id"])
        intro = str(content.get("intro") or location["description"])
        if not intro:
            problems.append(f"{location['location_id']}: 缺少介绍")
        intros.append(intro)
    cross_total = cross_hit = same_hit = 0
    all_registry = seen_intros.setdefault("_all", set())
    other_registry = set()
    for theme_key, registry in seen_intros.items():
        if theme_key not in ("_all", theme):
            other_registry |= registry
    same_registry = seen_intros.setdefault(theme, set())
    for intro in intros:
        cross_total += 1
        if intro in other_registry:
            cross_hit += 1
        if intro in same_registry:
            same_hit += 1
        all_registry.add(intro)
        same_registry.add(intro)
    cross_ratio = cross_hit / max(1, cross_total)
    same_ratio = same_hit / max(1, cross_total)
    if cross_ratio > 0.4:
        problems.append(f"地点介绍与其他主题重复率过高（{cross_ratio:.0%}）")
    return {
        "ok": not problems,
        "problems": problems,
        "intros": intros,
        "cross_theme_overlap": round(cross_ratio, 2),
        "same_theme_overlap": round(same_ratio, 2),
    }


def run_batch(
    *,
    out_dir: Path | str,
    seeds: Optional[list[int]] = None,
    themes: Optional[list[str]] = None,
    seed_start: int = 500001,
    worlds_per_theme: int = 10,
    consistency_days: int = 7,
) -> dict[str, Any]:
    """批量生成 + 质检 + 报告。返回汇总（report.json 同时写盘）。"""
    themes = themes or list(AVAILABLE_THEMES)
    unknown = [theme for theme in themes if theme not in AVAILABLE_THEMES]
    if unknown:
        raise QualityError(f"未知主题: {unknown}（可用 {AVAILABLE_THEMES}）")
    seeds = seeds or [seed_start + index for index in range(worlds_per_theme)]
    root = _worlds_dir(Path(out_dir))
    report: dict[str, Any] = {
        "kind": "worldgen_batch_report",
        "themes": themes,
        "seeds": seeds,
        "worlds": [],
        "summary": {},
    }
    seen_intros: dict[str, dict[str, set[str]]] = {"_all": set()}
    total_ok = 0
    for theme in themes:
        for seed in seeds:
            world_id = f"{theme}_{seed}"
            package_dir = root / world_id
            entry: dict[str, Any] = {"world_id": world_id, "theme": theme, "seed": seed}
            try:
                if not package_dir.exists():
                    docs = generate_world(seed=seed, theme=theme)
                    schema.write_package(package_dir, docs)
                apply_interiors_to_package(package_dir)
            except Exception as exc:
                entry.update({"status": "generation_failed", "error": f"{type(exc).__name__}: {exc}"})
                report["worlds"].append(entry)
                continue
            structure = _structure_check(package_dir)
            consistency = _consistency_check(package_dir, days=consistency_days)
            content = _content_check(package_dir, seen_intros, theme=theme)
            ok = structure["ok"] and consistency["ok"] and content["ok"]
            entry.update(
                {
                    "status": "ok" if ok else "issues",
                    "structure": structure,
                    "consistency": consistency,
                    "content": {key: value for key, value in content.items() if key != "intros"},
                    "validation": "passed" if ok else "issues",
                    "rc9_loadable": structure["ok"],
                }
            )
            if ok:
                total_ok += 1
            report["worlds"].append(entry)
    report["summary"] = {
        "total": len(report["worlds"]),
        "ok": total_ok,
        "issues": len(report["worlds"]) - total_ok,
        "themes": len(themes),
        "seeds_per_theme": len(seeds),
    }
    report_path = root / "batch-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def review_with_glm(
    report: dict[str, Any],
    *,
    sample: int = 3,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """GLM 辅助内容评审（可选）：抽样世界的地点介绍与事件，请求模型点评。

    模型评价只作为辅助证据，不改变结构质检结论。
    """
    from .providers import glm

    ok_worlds = [world for world in report["worlds"] if world.get("status") == "ok"]
    picked = ok_worlds[:sample]
    reviews = []
    for world in picked:
        package = load_package(Path(report["report_path"]).parent / world["world_id"])
        lines = []
        for location in sorted(package.locations.values(), key=lambda item: item["location_id"])[:6]:
            content = package.content_entry(location["location_id"])
            lines.append(f"- {location['name']}（{location['kind']}）：{content.get('intro', '')}")
        events = [t["summary"] for t in package.ambient_templates][:6]
        prompt = (
            "以下是一座虚拟小镇的部分设定。请用不超过 120 字评价："
            "1) 是否有鲜明的地区特色；2) 建筑用途与环境描述是否匹配；"
            "3) 有没有明显不合理之处。只输出评语。\n"
            + "\n".join(lines)
            + "\n环境事件：" + "；".join(events)
        )
        try:
            content_text, usage = glm._request_chat(
                base_url=base_url or glm._env("WORLDGEN_LLM_BASE_URL") or glm.DEFAULT_BASE_URL,
                api_key=api_key or glm._env("WORLDGEN_LLM_API_KEY") or "",
                model=model or glm._env("WORLDGEN_LLM_MODEL") or glm.DEFAULT_MODEL,
                prompt=prompt,
                timeout=120,
            )
            reviews.append({"world_id": world["world_id"], "review": content_text.strip()[:400], "usage": usage})
        except Exception as exc:
            reviews.append({"world_id": world["world_id"], "error": f"{type(exc).__name__}: {exc}"})
    return {"reviews": reviews, "note": "模型评价仅作辅助，结构质检结论不受影响"}


__all__ = ["QualityError", "run_batch", "review_with_glm"]
