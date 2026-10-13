#!/usr/bin/env python3
"""本地地图预览：世界包 → 单文件 HTML（内联 SVG + 中文界面）。

功能：世界名称/主题/种子、区域划分、地点与道路、点击地点查看
介绍/营业时间/可做活动/出场 NPC；可叠加隔离实例中的当前位置。
不引入任何前端工程，输出一个自包含 HTML 文件。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Optional

from . import GENERATOR_VERSION, schema
from .loader import WorldPackage, load

_KIND_COLORS = {
    "home": "#4f7c4f",
    "street": "#8a8a8a",
    "school": "#7b5ea7",
    "store": "#b07a2a",
    "park": "#3f8f5f",
    "beach": "#2f8fa8",
    "cafe": "#a05a3c",
    "library": "#3c6fa0",
    "shrine": "#a03c5a",
    "market": "#2a8f7a",
    "station": "#5a6a8a",
    "public": "#7a7a5a",
}
_KIND_LABELS = {
    "home": "住宅",
    "street": "街道",
    "school": "学校",
    "store": "商店",
    "park": "公园",
    "beach": "海边",
    "cafe": "咖啡馆",
    "library": "图书馆",
    "shrine": "神社",
    "market": "市场",
    "station": "车站",
    "public": "公共设施",
}


def _zone_regions(package: WorldPackage) -> dict[str, dict[str, Any]]:
    zones: dict[str, dict[str, Any]] = {}
    for location in package.locations.values():
        zone_id = location["zone_id"]
        entry = zones.setdefault(zone_id, {"min_x": 1e9, "min_y": 1e9, "max_x": -1e9, "max_y": -1e9, "members": []})
        entry["min_x"] = min(entry["min_x"], location["map"]["x"])
        entry["min_y"] = min(entry["min_y"], location["map"]["y"])
        entry["max_x"] = max(entry["max_x"], location["map"]["x"])
        entry["max_y"] = max(entry["max_y"], location["map"]["y"])
        entry["members"].append(location["location_id"])
    return zones


def _svg_map(package: WorldPackage, current_location: Optional[str]) -> str:
    width, height = 1000, 700
    parts: list[str] = []
    # 区块底色（成员外扩矩形，视觉分区用）。
    for zone_id, region in _zone_regions(package).items():
        pad = 60
        x0 = max(10, region["min_x"] - pad)
        y0 = max(10, region["min_y"] - pad)
        x1 = min(width - 10, region["max_x"] + pad)
        y1 = min(height - 10, region["max_y"] + pad)
        if x1 <= x0 or y1 <= y0:
            continue
        parts.append(
            f'<rect x="{x0:.0f}" y="{y0:.0f}" width="{x1 - x0:.0f}" height="{y1 - y0:.0f}" '
            f'rx="18" fill="#f2f5f2" stroke="#d8e0d8" stroke-dasharray="6 4"/>'
        )
    # 道路。
    for edge in package._topology["edges"]:
        a = package.location(edge["from"])["map"]
        b = package.location(edge["to"])["map"]
        parts.append(
            f'<line x1="{a["x"]:.0f}" y1="{a["y"]:.0f}" x2="{b["x"]:.0f}" y2="{b["y"]:.0f}" '
            f'stroke="#b9b9b9" stroke-width="5" stroke-linecap="round"/>'
        )
        mid_x, mid_y = (a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2
        parts.append(
            f'<text x="{mid_x:.0f}" y="{mid_y - 6:.0f}" font-size="11" fill="#8a8a8a" text-anchor="middle">'
            f'{edge["walk_seconds"] // 60}分{edge["walk_seconds"] % 60:02d}秒</text>'
        )
    # 地点。
    for location in sorted(package.locations.values(), key=lambda item: item["location_id"]):
        loc_id = html.escape(location["location_id"], quote=True)
        x, y = location["map"]["x"], location["map"]["y"]
        color = _KIND_COLORS.get(location["kind"], "#666666")
        shape = (
            f'<rect x="{x - 16:.0f}" y="{y - 16:.0f}" width="32" height="32" rx="7"'
            if location["kind"] == "home"
            else f'<circle cx="{x:.0f}" cy="{y:.0f}" r="15"'
        )
        closing = "/>" if location["kind"] == "home" else '/>'
        parts.append(
            f'<a href="#" class="loc" data-id="{loc_id}">{shape} fill="{color}" stroke="#ffffff" '
            f'stroke-width="2.5"{closing}'
            f'<text x="{x:.0f}" y="{y + 34:.0f}" font-size="13" fill="#333" text-anchor="middle" '
            f'pointer-events="none">{html.escape(location["name"])}</text></a>'
        )
        if current_location == location["location_id"]:
            parts.append(
                f'<circle cx="{x:.0f}" cy="{y:.0f}" r="24" fill="none" stroke="#d43f3f" '
                f'stroke-width="3" stroke-dasharray="5 4"><animate attributeName="r" values="20;27;20" '
                f'dur="2.2s" repeatCount="indefinite"/></circle>'
            )
    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="世界地图" style="width:100%;height:auto;background:'
        f'linear-gradient(180deg,#fbfdfb 0%,#eef4f6 100%);border-radius:12px;">'
        + "".join(parts)
        + "</svg>"
    )


def _interior_svg(package: WorldPackage, location_id: str, assets: dict[str, str]) -> str:
    """渲染一处室内场景（编辑器 v3 几何 + 编辑器家具素材）。"""
    scene = package.scene
    if not scene:
        return ""
    space = scene["spaces"].get(location_id)
    if not space:
        return ""
    parts: list[str] = ['<rect width="100" height="100" fill="#fbfbf7"/>']
    for floor in space["floors"]:
        parts.append(
            f'<rect x="{floor["x"]}" y="{floor["y"]}" width="{floor["w"]}" height="{floor["h"]}" '
            f'fill="{floor["color"]}"/>'
        )
    for wall in space["walls"]:
        parts.append(
            f'<line x1="{wall["x1"]}" y1="{wall["y1"]}" x2="{wall["x2"]}" y2="{wall["y2"]}" '
            f'stroke="{wall["color"]}" stroke-width="{max(0.6, wall["width"] / 8)}" stroke-linecap="round"/>'
        )
    for kind in ("windows", "doors"):
        for opening in space[kind]:
            wall = next(w for w in space["walls"] if w["id"] == opening["wall_id"])
            if wall["x1"] == wall["x2"]:
                x1 = x2 = wall["x1"]
                y1 = wall["y1"] + opening["offset"]
                y2 = y1 + opening["length"]
            else:
                x1 = wall["x1"] + opening["offset"]
                x2 = x1 + opening["length"]
                y1 = y2 = wall["y1"]
            dash = ' stroke-dasharray="2 1.5"' if kind == "doors" and opening.get("state") == "open" else ""
            color = {"doors": "#bd976e", "windows": "#9fc3cf"}[kind]
            parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#ffffff" stroke-width="2"/>'
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.2"{dash}/>'
            )
    objects_by_id = {obj["object_id"]: obj for obj in package.objects}
    placements = scene.get("placements", {})
    location_object_ids = {
        obj["object_id"] for obj in package.objects if obj["location_id"] == location_id
    }
    for object_id in sorted(location_object_ids & set(placements)):
        placement = placements[object_id]
        art = assets.get(placement["kind"], "")
        if not art:
            continue
        x, y = placement["x"], placement["y"]
        visual = placement.get("visual", {})
        rotation = visual.get("rotation", 0)
        scale = visual.get("scale", 100) / 100 * 0.14
        parts.append(
            f'<g transform="translate({x},{y}) rotate({rotation}) scale({scale:.3f})">{art}</g>'
        )
        if visual.get("show_name", True):
            name = objects_by_id[object_id]["name"]
            parts.append(
                f'<text x="{x}" y="{y + 7}" font-size="2.6" fill="#555" text-anchor="middle">{html.escape(name)}</text>'
            )
    entry = space["entry"]
    parts.append(
        f'<circle cx="{entry["x"] * 5 + 2.5}" cy="{entry["y"] * 5 + 2.5}" r="2.4" fill="none" '
        f'stroke="#d43f3f" stroke-width="0.7" stroke-dasharray="1.4 1"/>'
    )
    return (
        '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;max-width:420px;height:auto;background:#fff;'
        'border:1px solid #e2e8e2;border-radius:8px;">' + "".join(parts) + "</svg>"
    )


def render_preview(
    package_dir: Path | str,
    *,
    instance_home: Optional[Path | str] = None,
    output_path: Optional[Path] = None,
) -> Path:
    package = load(package_dir)
    current_location: Optional[str] = None
    instance_info: dict[str, Any] = {}
    if instance_home is not None:
        from .instance import read_instance_meta

        meta = read_instance_meta(instance_home)
        if meta["world_id"] != package.world_id:
            raise schema.WorldPackageError(
                f"实例属于世界 {meta['world_id']!r}，与世界包 {package.world_id!r} 不一致"
            )
        instance_home = Path(instance_home).resolve()
        state_path = instance_home / "data" / "world" / "world_state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            current_location = (state.get("location") or {}).get("place_id")
        instance_info = {
            "home": str(instance_home),
            "world_id": meta["world_id"],
            "current_location": current_location,
        }

    locations_payload = []
    for location in sorted(package.locations.values(), key=lambda item: item["location_id"]):
        rule = package.location_rule(location["location_id"]) or {}
        content = package.content_entry(location["location_id"])
        present_npcs = []
        for npc in package.npcs:
            if npc["home_base_area"] == location["location_id"]:
                present_npcs.append(
                    {
                        "display_name": npc["display_name"],
                        "public_role": npc["public_role"],
                        "windows": npc["windows"],
                    }
                )
        locations_payload.append(
            {
                **location,
                "rule": {
                    "open_hour": rule.get("open_hour"),
                    "close_hour": rule.get("close_hour"),
                    "weekdays_only": rule.get("weekdays_only", False),
                    "affordances": [
                        {"affordance_id": item[0], "description": item[1]}
                        for item in rule.get("affordances", ())
                    ],
                },
                "content": content,
                "npcs": present_npcs,
            }
        )
    world_content = package.content_entry("world")

    payload = {
        "world": {
            "world_id": package.world_id,
            "name": package.display_name,
            "theme": package.theme,
            "seed": package.seed,
            "timezone": package.timezone_name,
            "generator_version": package.generator_version,
            "content_source": package.content_source,
            "tagline": world_content.get("tagline", ""),
            "festivals": world_content.get("festivals", []),
        },
        "instance": instance_info,
        "locations": locations_payload,
        "kind_labels": _KIND_LABELS,
    }

    svg = _svg_map(package, current_location)

    # 室内预览（scene.json 存在时）：复用编辑器家具素材渲染。
    interiors_html = ""
    if package.scene and package.scene.get("spaces"):
        try:
            from .editor_bridge import _editor as _bridge_editor

            assets = dict(_bridge_editor().ASSETS)
        except Exception:
            assets = {}
        cards = []
        for location_id in sorted(package.scene["spaces"]):
            location = package.location(location_id)
            if location is None:
                continue
            svg_interior = _interior_svg(package, location_id, assets)
            if not svg_interior:
                continue
            furniture_count = sum(
                1 for obj in package.objects
                if obj["location_id"] == location_id and obj["object_id"] in package.scene.get("placements", {})
            )
            cards.append(
                f'<div class="card"><h2>{html.escape(location["name"])}'
                f'<span class="tag">室内 · {furniture_count} 件家具</span></h2>'
                f'{svg_interior}</div>'
            )
        if cards:
            interiors_html = (
                '<section style="padding:0 28px 10px"><h2 style="font-size:17px;margin:0 0 10px">室内布局</h2>'
                '<div style="display:flex;gap:14px;flex-wrap:wrap">' + "".join(cards) + "</div></section>"
            )

    body = _HTML_TEMPLATE.format(
        world_id=html.escape(package.world_id),
        title=html.escape(f"{package.display_name} · 世界预览"),
        name=html.escape(package.display_name),
        theme=html.escape(_THEME_NAMES.get(package.theme, package.theme)),
        seed=package.seed,
        timezone=html.escape(package.timezone_name),
        content_source=("GLM 内容" if package.content_source == "glm" else "本地模板内容"),
        tagline=html.escape(str(world_content.get("tagline", ""))),
        festivals="、".join(html.escape(str(item)) for item in world_content.get("festivals", [])) or "—",
        stats=(
            f"{len(package.locations)} 个地点 · {len(package._topology['edges'])} 条道路 · "
            f"{len(package.npcs)} 位公共角色"
        ),
        version=GENERATOR_VERSION,
        svg=svg,
        interiors=interiors_html,
        instance=(
            f'<div class="instance-chip">实例：{html.escape(instance_info.get("home", ""))}'
            f'{(" · 当前位置：" + html.escape(_loc_name(package, current_location))) if current_location else ""}</div>'
            if instance_info
            else '<div class="instance-chip instance-none">未关联实例（用 --instance 叠加当前位置）</div>'
        ),
        payload=json.dumps(payload, ensure_ascii=False),
    )
    out = output_path or (package.package_dir / "preview.html")
    out.write_text(body, encoding="utf-8")
    return out


_THEME_NAMES = {"coastal_town": "海滨小镇", "residential_town": "普通生活小镇"}


def _loc_name(package: WorldPackage, location_id: Optional[str]) -> str:
    if not location_id:
        return "未知"
    location = package.location(location_id)
    return f"{location['name']}（{location_id}）" if location else location_id


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin:0; font-family:"Microsoft YaHei","PingFang SC","Noto Sans SC",sans-serif;
         background:#f4f6f4; color:#2b2f2b; }}
  header {{ padding:22px 28px 12px; }}
  h1 {{ margin:0 0 4px; font-size:22px; }}
  .meta {{ color:#5a655a; font-size:13px; line-height:1.7; }}
  .meta b {{ color:#2b2f2b; }}
  .chip, .instance-chip {{ display:inline-block; margin:6px 8px 0 0; padding:3px 10px;
       border-radius:999px; background:#e6efe6; font-size:12px; color:#3c5a3c; }}
  .instance-none {{ background:#eee; color:#888; }}
  main {{ display:flex; gap:18px; padding:0 28px 28px; align-items:flex-start; flex-wrap:wrap; }}
  .map-panel {{ flex:3 1 640px; min-width:320px; }}
  .side {{ flex:1 1 280px; min-width:260px; max-width:360px; }}
  .card {{ background:#fff; border:1px solid #e2e8e2; border-radius:12px; padding:16px 18px;
           margin-bottom:14px; box-shadow:0 1px 3px rgba(0,0,0,.04); }}
  .card h2 {{ margin:0 0 8px; font-size:15px; }}
  .card .sub {{ color:#5a655a; font-size:12.5px; line-height:1.8; }}
  .tag {{ display:inline-block; margin:2px 4px 2px 0; padding:2px 8px; border-radius:6px;
          background:#eef3ee; font-size:12px; }}
  .tag.warn {{ background:#f7ece4; color:#8a5a2a; }}
  .loc {{ cursor:pointer; }}
  .loc:hover > * {{ filter:brightness(1.15); }}
  .legend {{ display:flex; flex-wrap:wrap; gap:6px 12px; margin-top:8px; }}
  .legend span {{ font-size:12px; color:#4a554a; }}
  .legend i {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:4px; }}
  #detail-name {{ font-size:17px; font-weight:600; }}
  #detail-kind {{ margin-left:8px; }}
  .row {{ margin:6px 0; font-size:13px; }}
  .row .k {{ color:#7a857a; margin-right:6px; }}
  footer {{ padding:0 28px 26px; color:#8a958a; font-size:12px; }}
</style>
</head>
<body>
<header>
  <h1>{name} <span class="chip" id="detail-kind">{theme}</span></h1>
  <div class="meta">
    <b>world_id</b> {world_id} &nbsp; <b>种子</b> {seed} &nbsp; <b>时区</b> {timezone}
    &nbsp; <b>内容来源</b> {content_source}<br>
    {tagline}
  </div>
  <div>{stats}</div>
  <div class="meta">当地节庆候选：{festivals}</div>
  {instance}
</header>
<main>
  <div class="map-panel">
    {svg}
    <div class="legend" id="legend"></div>
  </div>
  <aside class="side">
    <div class="card">
      <h2>地点详情</h2>
      <div id="detail">
        <div class="sub">点击地图上的地点查看介绍、营业时间、可做活动与出场角色。</div>
      </div>
    </div>
  </aside>
</main>
{interiors}
<footer>由 NyAiro WorldGen {version} 生成的静态预览；运行时状态（位置、持有物、已发生事件）由 rc9 World Runtime 管理，不在此文件中。室内几何与家具是编辑器场景数据，rc9 不消费。</footer>
<script>
const DATA = {payload};
const VERSION = "{version}";
const KIND_LABELS = DATA.kind_labels;
const byId = Object.fromEntries(DATA.locations.map(l => [l.location_id, l]));
const legend = document.getElementById('legend');
const seen = new Set();
for (const l of DATA.locations) {{
  if (seen.has(l.kind)) continue;
  seen.add(l.kind);
}}
const colors = {{"home":"#4f7c4f","street":"#8a8a8a","school":"#7b5ea7","store":"#b07a2a",
  "park":"#3f8f5f","beach":"#2f8fa8","cafe":"#a05a3c","library":"#3c6fa0",
  "shrine":"#a03c5a","market":"#2a8f7a","station":"#5a6a8a","public":"#7a7a5a"}};
for (const kind of seen) {{
  const item = document.createElement('span');
  item.innerHTML = `<i style="background:${{colors[kind]}}"></i>${{KIND_LABELS[kind] || kind}}`;
  legend.appendChild(item);
}}
function hours(rule) {{
  if (rule.open_hour == null) return '全天开放';
  const end = rule.close_hour == null ? '未设' : rule.close_hour + '点';
  return `${{rule.open_hour}}:00 – ${{rule.close_hour}}:00${{rule.weekdays_only ? '（周末休息）' : ''}}`;
}}
function fmtMinute(m) {{
  return String(Math.floor(m/60)).padStart(2,'0') + ':' + String(m%60).padStart(2,'0');
}}
function show(id) {{
  const l = byId[id];
  if (!l) return;
  const detail = document.getElementById('detail');
  const npcs = (l.npcs || []).map(n => {{
    const win = n.windows.map(w => `${{fmtMinute(w.start_minute)}}–${{fmtMinute(w.end_minute)}}（周${{w.weekdays.join('、')}}）`).join('；');
    return `<div class="row"><span class="k">出场</span>${{n.display_name}}（${{n.public_role}}）<br>
            <span class="sub">${{win}}</span></div>`;
  }}).join('');
  const flavor = l.content && l.content.flavor ? `<div class="row sub">${{l.content.flavor}}</div>` : '';
  const afford = (l.rule.affordances || []).map(a => `<span class="tag">${{a.description}}</span>`).join('');
  detail.innerHTML = `
    <div id="detail-name">${{l.name}}<span class="tag" id="detail-kind">${{KIND_LABELS[l.kind] || l.kind}}</span></div>
    <div class="row"><span class="k">ID</span>${{l.location_id}}</div>
    <div class="row"><span class="k">介绍</span>${{l.content.intro || l.description}}</div>
    ${{flavor}}
    <div class="row"><span class="k">营业</span>${{hours(l.rule)}}</div>
    <div class="row"><span class="k">活动</span>${{afford || '<span class="sub">（无）</span>'}}</div>
    ${{npcs || '<div class="row sub">（此地没有常驻公共角色）</div>'}}
  `;
}}
document.querySelectorAll('a.loc').forEach(a => {{
  a.addEventListener('click', ev => {{ ev.preventDefault(); show(a.dataset.id); }});
}});
if (DATA.instance && DATA.instance.current_location) {{
  const note = document.createElement('div');
  note.className = 'row';
  note.innerHTML = `<span class="tag warn">当前选中实例位于：${{byId[DATA.instance.current_location] ? byId[DATA.instance.current_location].name : DATA.instance.current_location}}</span>`;
  document.querySelector('#detail').appendChild(note);
}}
</script>
</body>
</html>
"""


__all__ = ["render_preview"]
