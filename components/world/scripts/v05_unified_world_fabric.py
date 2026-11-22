#!/usr/bin/env python3
"""0.5 统一空间树只读门面、微气候涟漪与灵光巧思物理引擎（零大模型调用）。

对应设计章节：第 54 / 66 / 74 / 86 / 90 / 109–122 章。
职责：
1. 以 ``SpatialNode`` + ``WorldFabric`` 把 place -> microzone -> container -> object/actor 四级单一父指针树
   与开窗淋雨/热饮随包散热/昼夜光斑挪移/停电小夜灯涟漪合为一层；
2. 提供 100% 零写盘 ``project_unified_world_fabric_zero_write(day_root, now_iso=...)`` 只读门面，聚合
   ``v04_neighborhood_living`` / ``v08_universal_matter`` / ``v08_microzone_ecology`` 的当前世界与物态事实；
3. 纳入无算力灵光巧思与深层活力物理纯函数（上下坡脚力、远眺遮蔽、水洼/残雪、烘衣房、伏笔夹页、窗雾末次重现、
   PNG 明信片邮戳块、离线月相等）。
"""
from __future__ import annotations

import base64
import json
import re
import struct
import sys
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = Path(__file__).resolve().parent
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WINDOW_STATES: tuple[str, ...] = ("closed", "cracked", "half", "wide")
WEATHERS: tuple[str, ...] = ("clear", "breezy_cool", "shower", "humid_plum_rain", "snow", "thunder_blackout")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_ALLOWED_POSTCARD_GIFTS = frozenset({"wild_mountain_tea", "sea_glass_shard", "pressed_osmanthus", "none"})


@dataclass
class SpatialNode:
    node_id: str
    kind: str  # "place" | "microzone" | "container" | "object" | "actor"
    name_cn: str
    parent_id: Optional[str]
    sheltered: bool = True
    is_paper: bool = False
    note_legibility: str = "clear"
    temp_c: Optional[float] = None
    pocket_insulated: bool = False
    moisture_pct: int = 50
    provenance_cn: str = ""
    usage_touches: int = 0
    preferred_home_slot: Optional[str] = None
    consecutive_returns: int = 0


@dataclass
class WorldFabric:
    nodes: dict[str, SpatialNode] = field(default_factory=dict)
    window_state: str = "half"
    weather: str = "clear"
    hour: int = 9
    lamp_on: bool = False

    def add_node(self, node: SpatialNode) -> None:
        if node.parent_id is not None and node.parent_id not in self.nodes:
            raise ValueError(f"PARENT_NOT_FOUND:{node.parent_id}")
        self.nodes[node.node_id] = node

    def move_node(self, node_id: str, new_parent_id: str) -> None:
        if node_id not in self.nodes or new_parent_id not in self.nodes:
            raise ValueError("NODE_OR_TARGET_NOT_FOUND")
        cur: Optional[str] = new_parent_id
        while cur is not None:
            if cur == node_id:
                raise ValueError("CYCLE_DETECTED")
            cur = self.nodes[cur].parent_id
        obj = self.nodes[node_id]
        obj.parent_id = new_parent_id
        if obj.kind == "object":
            obj.usage_touches += 1
            if obj.preferred_home_slot == new_parent_id:
                obj.consecutive_returns += 1
            else:
                obj.consecutive_returns = 1
                obj.preferred_home_slot = new_parent_id

    def ancestor_chain(self, node_id: str) -> list[str]:
        chain: list[str] = []
        cur = self.nodes[node_id].parent_id
        while cur is not None:
            chain.append(cur)
            cur = self.nodes[cur].parent_id
        return chain

    def effective_place(self, node_id: str) -> Optional[str]:
        cur: Optional[str] = node_id
        while cur is not None:
            n = self.nodes[cur]
            if n.kind == "place":
                return n.node_id
            cur = n.parent_id
        return None

    def is_habitual_slot(self, node_id: str) -> bool:
        return self.nodes[node_id].consecutive_returns >= 3

    def active_sunbeam_zone(self) -> Optional[str]:
        if self.weather in ("shower", "humid_plum_rain", "thunder_blackout", "rain", "heavy_rain"):
            return None
        if 7 <= int(self.hour) <= 11:
            return "zone_window"
        if 13 <= int(self.hour) <= 16:
            return "zone_sofa"
        return None

    def nightlight_active(self) -> bool:
        return self.weather == "thunder_blackout" and (int(self.hour) >= 18 or int(self.hour) < 6)

    def advance_ripple(self, minutes: int) -> dict[str, Any]:
        """推进指定分钟数的纯本地微气候与物态涟漪，不修改子物件父指针。"""
        if minutes <= 0:
            return {
                "sunbeam_zone": self.active_sunbeam_zone(),
                "nightlight_auto_on": self.nightlight_active(),
                "changed_objects": [],
                "llm_calls": 0,
            }
        changed: list[str] = []
        rain_splash_window = (
            self.weather in ("shower", "thunder_blackout", "rain", "heavy_rain")
            and self.window_state == "wide"
        )
        outdoor_cold = self.weather in ("breezy_cool", "snow", "shower", "rain")

        for nid, node in self.nodes.items():
            if node.kind != "object":
                continue
            ancestors = self.ancestor_chain(nid)
            in_window_zone = "zone_window" in ancestors or node.parent_id == "zone_window"
            in_closed_drawer = any(
                self.nodes[a].kind == "container" and self.nodes[a].sheltered
                for a in ancestors
            )
            eff_place = self.effective_place(nid)
            outdoors_uninsulated = (
                eff_place is not None
                and not self.nodes[eff_place].sheltered
                and not node.pocket_insulated
            )

            if rain_splash_window and in_window_zone and not in_closed_drawer:
                if node.is_paper and node.note_legibility != "smudged":
                    node.note_legibility = "smudged"
                    changed.append(nid)
                if "plant" in nid and node.moisture_pct < 90:
                    node.moisture_pct = min(100, node.moisture_pct + 35)
                    changed.append(nid)

            if node.temp_c is not None and node.temp_c > 22.0:
                rate_per_min = 0.45
                if outdoors_uninsulated and outdoor_cold:
                    rate_per_min = 0.95
                elif in_window_zone and self.window_state in ("half", "wide") and outdoor_cold:
                    rate_per_min = 0.68
                elif node.pocket_insulated:
                    rate_per_min = 0.22
                new_temp = max(22.0, round(node.temp_c - rate_per_min * minutes, 1))
                if new_temp != node.temp_c:
                    node.temp_c = new_temp
                    changed.append(nid)

        return {
            "sunbeam_zone": self.active_sunbeam_zone(),
            "nightlight_auto_on": self.nightlight_active(),
            "changed_objects": changed,
            "llm_calls": 0,
        }


def _safe_hour_from_iso(now_iso: Optional[str]) -> int:
    if isinstance(now_iso, str) and now_iso.strip():
        txt = now_iso.strip()
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(txt).hour
        except ValueError:
            pass
    return 10


def _read_json_if_file(path: Path) -> Optional[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def project_unified_world_fabric_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% 零磁盘写入的统一空间树只读门面，焊合邻里天气、房间窗扇、微区光影与物态位置。"""
    root = Path(day_root)
    hour = _safe_hour_from_iso(now_iso)
    weather = "clear"
    season = "autumn"
    window_state = "half"
    lamp_on = False
    location_id = "home"
    objects_count = 0

    nb_doc = _read_json_if_file(root / "world" / "v04_neighborhood_state.json")
    if isinstance(nb_doc, Mapping):
        env = nb_doc.get("environment") if isinstance(nb_doc.get("environment"), Mapping) else {}
        weather = str(env.get("weather") or weather)
        season = str(env.get("season") or season)

    win_doc = _read_json_if_file(root / "phone" / "home_window.json")
    if isinstance(win_doc, Mapping):
        raw_win = str(win_doc.get("window_state") or win_doc.get("state") or "").strip().lower()
        if raw_win in WINDOW_STATES:
            window_state = raw_win

    matter_doc = _read_json_if_file(root / "world" / "v08_matter_space.json")
    if isinstance(matter_doc, Mapping):
        objs = matter_doc.get("matter_objects")
        if isinstance(objs, Mapping):
            objects_count = len(objs)
        elif isinstance(objs, list):
            objects_count = len(objs)

    world_doc = _read_json_if_file(root / "data" / "world" / "world_state.json") or _read_json_if_file(root / "world" / "world_state.json")
    if isinstance(world_doc, Mapping):
        loc = world_doc.get("location") if isinstance(world_doc.get("location"), Mapping) else {}
        location_id = str(loc.get("place_id") or location_id)

    fabric = WorldFabric(window_state=window_state, weather=weather, hour=hour, lamp_on=lamp_on)
    return {
        "schema_version": "world.v05.unified_fabric.v1",
        "day_root_exists": root.exists(),
        "location_id": location_id,
        "hour": hour,
        "season": season,
        "weather": weather,
        "window_state": window_state,
        "sunbeam_zone": fabric.active_sunbeam_zone(),
        "nightlight_auto_on": fabric.nightlight_active(),
        "objects_count": objects_count,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Deep Vitality & Clever Twists Pure Helpers (Ch.109–122, Zero-LLM)
# ---------------------------------------------------------------------------


def compute_slope_walk_seconds(
    base_seconds: int,
    elev_from: int,
    elev_to: int,
    *,
    slope_style: str = "gentle",
    carried_heavy_count: int = 0,
) -> dict[str, Any]:
    delta = int(elev_to) - int(elev_from)
    mult = 1.0
    body_tag = "steady"
    if delta > 0:
        mult += 0.15 * delta
        if carried_heavy_count > 0 and slope_style == "stepped":
            mult += 0.15
        mult = min(mult, 1.5)
        body_tag = "warmed_slightly_breathless"
    elif delta < 0:
        mult = max(0.75, 1.0 + 0.10 * delta)
        body_tag = "downhill_breeze_in_collar"
    actual = max(45, int(round(base_seconds * mult)))
    return {"walk_seconds": actual, "elevation_delta": delta, "body_tag": body_tag}


def resolve_overlook_sightlines(
    observer_elev: int,
    weather: str,
    targets: Sequence[Mapping[str, Any]],
) -> list[str]:
    if weather in ("coastal_mist", "fog", "heavy_rain", "rain"):
        return []
    seen: list[str] = []
    for t in targets[:3]:
        if observer_elev - int(t.get("elevation", 0)) >= 2 and t.get("macro_activity_cn"):
            seen.append(str(t["macro_activity_cn"]))
    return seen


def evolve_blank_grove_lot(days_lived: int, visit_records: Sequence[Mapping[str, str]]) -> str:
    if days_lived < 14 or len(visit_records) < 5:
        return "老树下的留白空坪"
    scores = {"老树遮雨读书廊": 0, "树荫青石野餐台": 0, "向阳木阶歇脚台": 0}
    for rec in visit_records:
        kind = rec.get("carried_kind", "empty")
        if kind in ("book", "stationery"):
            scores["老树遮雨读书廊"] += 1
        elif kind in ("food", "drink", "ingredient"):
            scores["树荫青石野餐台"] += 1
        else:
            scores["向阳木阶歇脚台"] += 1
    return max(scores.items(), key=lambda kv: kv[1])[0]


def settle_ground_puddle_and_snowbank(
    *,
    hours_since_stop: float,
    initial_water: float,
    initial_snow_mm: float,
    dip_factor: float = 1.5,
    shade_factor: float = 0.35,
) -> dict[str, Any]:
    rem_water = max(0.0, initial_water - hours_since_stop * 0.22) * dip_factor
    rem_snow = max(0.0, initial_snow_mm - (hours_since_stop * 0.85) / max(0.2, shade_factor))
    return {"puddle_reflects_lantern": rem_water > 0.25, "snowbank_crust_mm": round(rem_snow, 1)}


def settle_laundromat_spin_with_pockets(garments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for g in garments:
        item = dict(g)
        item["moisture"] = "dry"
        item["thermal"] = "warm"
        pockets_out = []
        for sub in g.get("pocket_items", []):
            p = dict(sub)
            p["moisture"] = "dry"
            p["thermal"] = "warm"
            if p.get("paper_like"):
                p["wear_state"] = "heat_crinkled"
            pockets_out.append(p)
        item["pocket_items"] = pockets_out
        result.append(item)
    return result


def apply_upstream_blackboard_and_milk_bottles(
    *,
    morning_weather: str,
    yesterday_gale: bool,
    rainy_streak_days: int,
    bottles_returned_this_week: int,
    carrying_empty_bottle: bool = False,
) -> dict[str, Any]:
    if morning_weather in ("rain", "sea_gale"):
        diner_special = "热海带豆腐汤"
        crossed_out = "盐烤青花鱼"
    else:
        diner_special = "盐烤青花鱼"
        crossed_out = ""
    bakery_extra = "肉桂风落苹果派" if yesterday_gale else ""
    milk_packaging = (
        "纸盒装鲜奶（连雨空瓶未收齐）"
        if (rainy_streak_days >= 2 and bottles_returned_this_week == 0 and not carrying_empty_bottle)
        else "玻璃瓶装鲜奶"
    )
    milk_price_jpy = 150 - (30 if carrying_empty_bottle else 0)
    return {
        "diner_special": diner_special,
        "chalkboard_crossed_out": crossed_out,
        "bakery_extra": bakery_extra,
        "milk_packaging": milk_packaging,
        "milk_price_jpy": milk_price_jpy,
    }


@dataclass
class TuckedPageNote:
    target_page: int
    author: str
    inserted_hour: float
    item_kind: str
    note_text_cn: str = ""


class ForeshadowBook:
    def __init__(self, total_pages: int = 60, current_page: int = 1) -> None:
        self.total_pages = total_pages
        self.current_page = current_page
        self.tucked: list[TuckedPageNote] = []

    def tuck_into_unread_page(
        self,
        *,
        target_page: int,
        author: str,
        now_hour: float,
        item_kind: str = "leaf",
        note_text_cn: str = "",
    ) -> None:
        if target_page <= self.current_page or target_page > self.total_pages:
            raise ValueError("只能夹进还没翻到的后续页码里")
        if len(self.tucked) >= 3:
            raise ValueError("同一本册子最多同时藏 3 处未拆伏笔")
        self.tucked.append(
            TuckedPageNote(
                target_page=target_page,
                author=author,
                inserted_hour=now_hour,
                item_kind=item_kind,
                note_text_cn=note_text_cn,
            )
        )

    def visible_context_before_flip(self) -> list[str]:
        return []

    def turn_to_page(self, new_page: int, now_hour: float) -> list[dict[str, Any]]:
        self.current_page = max(1, min(self.total_pages, new_page))
        revealed: list[dict[str, Any]] = []
        remaining: list[TuckedPageNote] = []
        for entry in self.tucked:
            if self.current_page >= entry.target_page:
                elapsed_h = max(0.0, now_hour - entry.inserted_hour)
                if entry.item_kind == "leaf":
                    if elapsed_h < 6.0:
                        leaf_state = "微凉鲜叶"
                    elif elapsed_h < 120.0:
                        leaf_state = "微卷半干叶"
                    else:
                        leaf_state = "压平的干叶标本"
                else:
                    leaf_state = "夹页便签"
                revealed.append({
                    "page": entry.target_page,
                    "author": entry.author,
                    "elapsed_hours": elapsed_h,
                    "state_cn": leaf_state,
                    "note_text_cn": entry.note_text_cn,
                })
            else:
                remaining.append(entry)
        self.tucked = remaining
        return revealed


class WindowFrostCanvas:
    def __init__(self) -> None:
        self.strokes: list[tuple[float, float]] = []
        self.phase: str = "NONE"
        self.latent_since_hour: float = 0.0

    def draw_on_mist(self, points: list[tuple[float, float]], window_misty: bool) -> None:
        if not window_misty:
            raise ValueError("只有窗玻璃起雾时才能用指尖画痕")
        self.strokes = list(points[:30])
        self.phase = "FRESH"

    def on_warm_clear(self, now_hour: float) -> None:
        if self.phase == "FRESH":
            self.phase = "LATENT"
            self.latent_since_hour = now_hour
        elif self.phase == "FINAL_ECHO":
            self.strokes = []
            self.phase = "NONE"

    def on_mist_return(self, now_hour: float) -> dict[str, Any]:
        if self.phase == "LATENT":
            if (now_hour - self.latent_since_hour) <= 36.0:
                self.phase = "FINAL_ECHO"
                return {"visible": True, "opacity": 0.35, "point_count": len(self.strokes)}
            self.strokes = []
            self.phase = "NONE"
        return {"visible": self.phase == "FRESH", "opacity": 1.0 if self.phase == "FRESH" else 0.0, "point_count": len(self.strokes)}


def compute_window_dock_light(
    win_left: int,
    win_right: int,
    work_left: int,
    work_right: int,
    is_secondary_monitor: bool = False,
) -> dict[str, Any]:
    if abs(win_left - work_left) <= 24:
        dock_side = "left_wall_window"
        light_deg = 32
    elif abs(work_right - win_right) <= 24:
        dock_side = "right_wall_window"
        light_deg = -32
    else:
        dock_side = "center_balanced"
        light_deg = 0
    return {"dock_side": dock_side, "light_angle_deg": light_deg, "soft_contrast": 0.88 if is_secondary_monitor else 1.0}


def route_furniture_drop(target_hotspot: str, text_payload: str) -> dict[str, Any]:
    if target_hotspot == "wastebasket":
        return {"mode": "CRUMPLE_AND_FORGET", "persisted_text": "", "llm_calls": 0, "event_signal": "crumpled_scrap_paper"}
    if target_hotspot == "tin_box":
        return {"mode": "TIMED_COASTER_MEMO", "persisted_text": text_payload.strip()[:120], "llm_calls": 0, "event_signal": "stored_in_tin_box"}
    if target_hotspot == "desk_surface":
        return {"mode": "WORKBENCH_INSPECT", "persisted_text": text_payload.strip(), "llm_calls": 1, "event_signal": "opened_on_workbench"}
    raise ValueError(f"未知的投放家具部位: {target_hotspot}")


def settle_same_bag_thermal_condensation(bag_items: list[dict[str, Any]], elapsed_minutes: float) -> list[dict[str, Any]]:
    out = [dict(it) for it in bag_items]
    has_hot = any(it.get("thermal") == "hot" for it in out)
    has_cold = any(it.get("thermal") in ("cold", "frozen") for it in out)
    if elapsed_minutes >= 15.0 and has_hot and has_cold:
        for it in out:
            if it.get("thermal") == "hot":
                it["thermal"] = "warm"
            elif it.get("thermal") in ("cold", "frozen"):
                it["thermal"] = "cool"
                it["moisture"] = "damp"
        for it in out:
            if it.get("paper_like") and not it.get("wrapped_in_sleeve"):
                it["moisture"] = "damp"
                it["note_legibility"] = "smudged"
    return out


def settle_window_breeze_page_flip(
    *,
    book_open: bool,
    reading_page: int,
    has_tucked_bookmark: bool,
    stacked_weight_items: list[str],
    window_draft: str,
) -> int:
    if not book_open or window_draft not in ("breeze", "chilly_draft"):
        return reading_page
    if has_tucked_bookmark or len(stacked_weight_items) > 0:
        return reading_page
    return reading_page + 5


def settle_hot_mug_ironing_paper(
    *,
    paper_moisture: str,
    paper_posture: str,
    mug_thermal: str,
    mug_cleanliness: str,
    stacked_minutes: float,
) -> dict[str, str]:
    if stacked_minutes < 10.0 or mug_thermal != "hot":
        return {"posture": paper_posture, "cleanliness": "clean", "legibility": "clear"}
    if paper_moisture != "dry":
        return {"posture": paper_posture, "cleanliness": "clean", "legibility": "smudged"}
    clean_out = "soiled" if mug_cleanliness == "soiled" else "clean"
    return {"posture": "neat", "cleanliness": clean_out, "legibility": "clear"}


def settle_crumpled_flyer_shoe_drying(
    *,
    shoe_moisture: str,
    stuffed_folded_dry_paper: bool,
    elapsed_minutes: float,
) -> dict[str, str]:
    if shoe_moisture in ("wet", "damp") and stuffed_folded_dry_paper and elapsed_minutes >= 90.0:
        return {"shoe_moisture": "dry", "paper_moisture": "wet"}
    return {"shoe_moisture": shoe_moisture, "paper_moisture": "dry"}


def embed_postcard_png_chunk(
    png_bytes: bytes,
    *,
    origin_town: str,
    stamp_code: str,
    gift_slot: str,
    FrontNoteCn: str,
    hops: int = 1,
) -> bytes:
    if not png_bytes.startswith(_PNG_SIG) or b"IEND" not in png_bytes:
        raise ValueError("不是合法的 PNG 图片字节流")
    if gift_slot not in _ALLOWED_POSTCARD_GIFTS:
        raise ValueError("夹带风物不在安全白名单内")
    if hops > 3:
        raise ValueError("往返叠盖邮戳最多不超过 3 枚")
    clean_note = re.sub(r"[\x00-\x1f\x7f]", "", FrontNoteCn).strip()[:60]
    payload = json.dumps(
        {"town": origin_town.strip()[:12], "stamp": stamp_code.strip()[:24], "gift": gift_slot, "note": clean_note, "hops": int(hops)},
        ensure_ascii=False,
    ).encode("utf-8")
    chunk_data = b"nyairo_postcard\x00" + payload
    crc = zlib.crc32(b"tEXt" + chunk_data) & 0xFFFFFFFF
    text_chunk = struct.pack(">I", len(chunk_data)) + b"tEXt" + chunk_data + struct.pack(">I", crc)
    iend_pos = png_bytes.rfind(b"\x00\x00\x00\x00IEND")
    return png_bytes[:iend_pos] + text_chunk + png_bytes[iend_pos:]


def extract_postcard_png_chunk(png_bytes: bytes) -> Optional[dict[str, Any]]:
    if not png_bytes.startswith(_PNG_SIG) or len(png_bytes) > 2 * 1024 * 1024:
        return None
    pos = 8
    while pos + 8 <= len(png_bytes):
        length = struct.unpack(">I", png_bytes[pos : pos + 4])[0]
        ctype = png_bytes[pos + 4 : pos + 8]
        data = png_bytes[pos + 8 : pos + 8 + length]
        crc_expected = struct.unpack(">I", png_bytes[pos + 8 + length : pos + 12 + length])[0]
        if (zlib.crc32(ctype + data) & 0xFFFFFFFF) != crc_expected:
            return None
        if ctype == b"tEXt" and data.startswith(b"nyairo_postcard\x00"):
            obj = json.loads(data.split(b"\x00", 1)[1].decode("utf-8"))
            if obj.get("gift") not in _ALLOWED_POSTCARD_GIFTS:
                return None
            obj["safe_display_quote"] = f"明信片正面手写字：「{obj.get('note', '')}」"
            return obj
        pos += 12 + length
    return None


def encode_shortwave_weather_callsign(theme_idx: int, solar_term_idx: int, weather_idx: int, spot_idx: int) -> str:
    packed = ((theme_idx & 0x07) << 13) | ((solar_term_idx & 0x1F) << 8) | ((weather_idx & 0x0F) << 4) | (spot_idx & 0x0F)
    raw = struct.pack(">H", packed)
    chk = zlib.crc32(raw) & 0xFF
    token = base64.b32encode(raw + bytes([chk])).decode("ascii").rstrip("=")
    return f"NY-{token[:3]}-{token[3:]}"


def decode_shortwave_weather_callsign(callsign: str) -> dict[str, int]:
    m = re.fullmatch(r"NY-([A-Z2-7]{3})-([A-Z2-7]{2,4})", callsign.strip())
    if not m:
        raise ValueError("短波呼号格式无效")
    b32 = m.group(1) + m.group(2)
    pad = "=" * ((8 - len(b32) % 8) % 8)
    buf = base64.b32decode(b32 + pad)
    if len(buf) != 3 or (zlib.crc32(buf[:2]) & 0xFF) != buf[2]:
        raise ValueError("短波呼号校验未通过")
    packed = struct.unpack(">H", buf[:2])[0]
    return {"theme_idx": (packed >> 13) & 0x07, "solar_term_idx": (packed >> 8) & 0x1F, "weather_idx": (packed >> 4) & 0x0F, "spot_idx": packed & 0x0F}


def compute_offline_lunar_phase(unix_seconds: float, weather: str = "clear") -> dict[str, Any]:
    known_new_moon_utc = 947182440.0
    synodic_days = 29.530588853
    age_days = ((unix_seconds - known_new_moon_utc) / 86400.0) % synodic_days
    is_full_window = abs(age_days - (synodic_days / 2.0)) <= 1.2
    visible = weather == "clear"
    return {
        "lunar_age_days": round(age_days, 2),
        "is_full_moon_window": is_full_window,
        "floor_moonbeam_visible": visible and is_full_window,
        "spring_tide_bonus": is_full_window,
    }


def validate_svg_room_skin(svg_text: str) -> tuple[bool, str]:
    lower = svg_text.lower()
    if "<script" in lower or "foreignobject" in lower or "onload=" in lower or "http://" in lower or "https://" in lower:
        return False, "检出脚本标签或外部网络地址，已拦截"
    for layer in ("sky-outside", "window-glass", "room-base", "lamp-glow", "spots"):
        if f'id="{layer}"' not in svg_text:
            return False, f"缺少约定的房间图层: {layer}"
    return True, "ok"


def check_midway_umbrella_reunion(
    *,
    partner_departing_from_home: bool,
    subject_route: Optional[tuple[str, str]],
    subject_progress_ratio: float,
    pre_agreed_together: bool = False,
) -> dict[str, Any]:
    if (
        partner_departing_from_home
        and not pre_agreed_together
        and subject_route is not None
        and subject_route[1] == "home"
        and 0.15 <= subject_progress_ratio <= 0.85
    ):
        return {"met_midway": True, "meet_node": "alley_midway", "remaining_ratio": round((1.0 - subject_progress_ratio) * 0.5, 2)}
    return {"met_midway": False, "meet_node": None, "remaining_ratio": 1.0}


def resolve_paper_lot_with_veto(drawn_option: str, other_option: str, user_tore_ticket: bool = False) -> dict[str, str]:
    if not user_tore_ticket:
        return {"chosen": drawn_option, "mode": "ACCEPTED_LOT"}
    return {"chosen": other_option, "mode": "TORN_TICKET_TRUE_WISH"}


def settle_post_refusal_half_tangerine(
    *,
    refusal_lock_active: bool,
    quiet_minutes_since_friction: float,
    days_since_last_peace_gesture: float,
    kitchen_has_fruit: bool = True,
) -> dict[str, Any]:
    if quiet_minutes_since_friction >= 30.0 and days_since_last_peace_gesture >= 3.0:
        gift = "半只剥开的橘子" if kitchen_has_fruit else "一杯刚兑好的温水"
        return {"refusal_lock_active": refusal_lock_active, "apology_issued": False, "desk_peace_item": gift}
    return {"refusal_lock_active": refusal_lock_active, "apology_issued": False, "desk_peace_item": None}


def run_v05_week_simulation(*, legacy_mode: bool = False, world_seed: str = "nagiura_v05") -> dict[str, Any]:
    """第 58 / 74 / 90 章：7 天 56 时段确定性回放对比，验证 0.5 彻底消除连续改笔记死循环与超时卡死。"""
    import v02_v03_daily_loop_bridge as loop_bridge
    import v08_universal_matter as matter
    import world_open_storefront as storefront

    slots_hours = (8, 10, 11, 13, 15, 17, 20, 22)
    history: list[str] = []
    day_logs: list[dict[str, Any]] = []
    foreground_locked = False
    milk_units = 1
    outings_total = 0
    note_turns_total = 0
    rest_or_dwell_total = 0
    max_consecutive_note = 0
    cur_streak = 0
    stalled_count = 0

    for day in range(1, 8):
        rain_heavy = (day == 4)
        potato_ev = storefront.compute_daily_passing_cart_window(world_seed, day, "autumn")
        today_acts: list[str] = []

        for hour in slots_hours:
            if hour == 8 and milk_units > 0:
                milk_units -= 1

            if day == 5 and hour == 15:
                foreground_locked = True
                if legacy_mode:
                    stalled_count += 1
                    today_acts.append("STALLED_LOCKED")
                    continue
                import autonomous_life_cycle as cycle_mod
                landing = cycle_mod.handle_round_timeout_soft_landing(foreground_locked=foreground_locked)
                foreground_locked = bool(landing["foreground_locked"])
                today_acts.append(str(landing["status"]))
                history.append("quiet_dwell")
                rest_or_dwell_total += 1
                cur_streak = 0
                continue

            minute_of_day = hour * 60 + 40
            cart_title = potato_ev["title_cn"] if storefront.is_passing_cart_active(potato_ev, minute_of_day) else None
            tide_open = matter.is_coastal_low_tide_open("coastal_town", day, hour)

            ranked = loop_bridge.rank_daily_candidates(
                recent_actions=history[-4:],
                hour=hour,
                has_active_note=True,
                milk_empty=(milk_units == 0),
                low_tide_open=tide_open,
                passing_cart_title=cart_title,
                rain_heavy=rain_heavy,
                legacy_mode=legacy_mode,
            )
            chosen = ranked[0]
            history.append(chosen["kind"])
            today_acts.append(f"{hour:02d}:00->{chosen['action_id']}")

            if chosen["kind"] == "continue_note":
                note_turns_total += 1
                cur_streak += 1
                max_consecutive_note = max(max_consecutive_note, cur_streak)
            else:
                cur_streak = 0
                if chosen["kind"] == "outing":
                    outings_total += 1
                    if chosen["action_id"] == "go_store_milk":
                        milk_units = 2
                else:
                    rest_or_dwell_total += 1

        day_logs.append({"day": day, "rain_heavy": rain_heavy, "actions": today_acts})

    return {
        "mode": "legacy_baseline" if legacy_mode else "v05_breathing_rhythm",
        "total_slots": 7 * len(slots_hours),
        "note_turns_total": note_turns_total,
        "outings_total": outings_total,
        "rest_or_dwell_total": rest_or_dwell_total,
        "max_consecutive_note_streak": max_consecutive_note,
        "stalled_count": stalled_count,
        "day_logs": day_logs,
        "llm_calls": 0,
    }


def guard_chalkboard_ink_output(
    candidate_ink: str,
    *,
    locked_price_jpy: int,
    fallback_plain_cn: str,
) -> str:
    """第 124 章：毫秒级门卫筛子——核对金额数字一致、拦截免单白送越界词与超长机器腔，失格即静默换朴实短句。"""
    txt = candidate_ink.strip()
    if not txt or len(txt) > 32:
        return fallback_plain_cn
    for bad in ("免费", "白送", "免单", "龙虾", "为您", "好的"):
        if bad in txt:
            return fallback_plain_cn
    nums = [int(x) for x in re.findall(r"\d+", txt)]
    if any(n != locked_price_jpy for n in nums):
        return fallback_plain_cn
    return txt


def strip_piggyback_chalk_tail(raw_stream_text: str) -> tuple[str, str]:
    """第 124 章：流式最末尾 <chalk>...</chalk> 静默剥离，聊天气泡零污染、首字延迟零增加。"""
    m = re.search(r"<chalk>(.*?)</chalk>\s*$", raw_stream_text, flags=re.S)
    if not m:
        return raw_stream_text.strip(), ""
    clean_reply = raw_stream_text[: m.start()].rstrip()
    return clean_reply, m.group(1).strip()


def compose_deduped_three_part_scene(
    causes: Sequence[str],
    actions: Sequence[str],
    tails: Sequence[str],
    recent_30d_ring: list[str],
    seed_idx: int = 0,
) -> str:
    """第 123 章：三段乐高碎片拼图 + 30 天圆环记忆锁，保证连下半月雨也绝不复读同一句。"""
    total = len(causes) * len(actions) * len(tails)
    if total <= 0:
        return ""
    for step in range(total):
        idx = (seed_idx + step) % total
        c = causes[idx % len(causes)]
        a = actions[(idx // len(causes)) % len(actions)]
        t = tails[(idx // (len(causes) * len(actions))) % len(tails)]
        key = f"{c}|{a}|{t}"
        if key not in recent_30d_ring:
            if len(recent_30d_ring) >= 30:
                recent_30d_ring.pop(0)
            recent_30d_ring.append(key)
            return f"{c}，{a}，{t}。"
    return f"{causes[0]}，{actions[0]}，{tails[0]}。"


def estimate_world_ink_monthly_rmb(
    *,
    piggyback_spots_per_month: int = 60,
    micro_press_active_days: int = 20,
) -> dict[str, float]:
    """第 124 / 125 章：核算顺风车捎带与单次微印整月新增人民币开销（精确到厘）。"""
    piggy_rmb = piggyback_spots_per_month * (22 * 2.0 / 1_000_000 + 18 * 8.0 / 1_000_000)
    press_rmb = micro_press_active_days * (220 * 2.0 / 1_000_000 + 180 * 8.0 / 1_000_000)
    return {
        "piggyback_only_rmb": round(piggy_rmb, 4),
        "micro_press_only_rmb": round(press_rmb, 4),
        "combined_rmb": round(piggy_rmb + press_rmb, 4),
    }


__all__ = [
    "ForeshadowBook",
    "SpatialNode",
    "TuckedPageNote",
    "WEATHERS",
    "WINDOW_STATES",
    "WindowFrostCanvas",
    "WorldFabric",
    "apply_upstream_blackboard_and_milk_bottles",
    "check_midway_umbrella_reunion",
    "compose_deduped_three_part_scene",
    "compute_offline_lunar_phase",
    "compute_slope_walk_seconds",
    "compute_window_dock_light",
    "decode_shortwave_weather_callsign",
    "embed_postcard_png_chunk",
    "encode_shortwave_weather_callsign",
    "estimate_world_ink_monthly_rmb",
    "evolve_blank_grove_lot",
    "extract_postcard_png_chunk",
    "guard_chalkboard_ink_output",
    "project_unified_world_fabric_zero_write",
    "resolve_overlook_sightlines",
    "resolve_paper_lot_with_veto",
    "route_furniture_drop",
    "run_v05_week_simulation",
    "settle_crumpled_flyer_shoe_drying",
    "settle_ground_puddle_and_snowbank",
    "settle_hot_mug_ironing_paper",
    "settle_laundromat_spin_with_pockets",
    "settle_post_refusal_half_tangerine",
    "settle_same_bag_thermal_condensation",
    "settle_window_breeze_page_flip",
    "strip_piggyback_chalk_tail",
    "validate_svg_room_skin",
]
