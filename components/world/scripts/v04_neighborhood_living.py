#!/usr/bin/env python3
"""v0.4 街区院子、分段干燥、耐受盆栽、有过程出行、纸布手作守恒与旧书店公共生活领域引擎。

对标规划章节：第 38–43、45、46 章及第 47 章做减法原则。
单一确定性状态文件：``<day_root>/world/v04_neighborhood_state.json``。

核心不变式：
1. 纯读投影（``project_v04_neighborhood``）与无状态文件时的 ``advance_v04_neighborhood_processes``
   100% 零磁盘写入（零 mkdir、零临时文件、零 mtime 变化）。
2. 状态落盘采用 ``0600`` 权限同目录临时文件 + ``fsync`` + ``os.replace`` 原子替换；
   拒绝符号链接（``503 STATE_SYMLINK_REFUSED``），损坏 JSON 失败关闭报错（``503 STATE_CORRUPTED``）。
3. 100% 纯标准库确定性运算，零随机数，零付费模型调用（``llm_calls == 0``），
   开源角色包中性默认（回退为 ``"TA"``）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ENGINE_SCHEMA = "world.v04.neighborhood_living.v1"
STATE_SCHEMA_VERSION = 1
STATE_DIRNAME = "world"
STATE_FILENAME = "v04_neighborhood_state.json"

DEFAULT_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# Batch A: Three-Ring Topology, Weather/Drying & Tolerant Potted Plant
# ---------------------------------------------------------------------------

WEATHER_KINDS: tuple[str, ...] = ("clear", "drizzle", "rain")
AIRFLOW_KINDS: tuple[str, ...] = ("weak", "medium", "strong")
SEASON_KINDS: tuple[str, ...] = ("spring", "summer", "autumn", "winter")
SHELTER_KINDS: tuple[str, ...] = ("indoor", "sheltered", "open_air")

BOOKSTORE_OPEN_START_MIN = 10 * 60  # 10:00
BOOKSTORE_OPEN_END_MIN = 19 * 60    # 19:00

# v3.0 Ch.02 Threshold Manifestation Contract: one-time physical states a slow
# variable leaves on a POI (e.g. "旧书店门上贴出转让告示").  Written only through
# ``record_poi_physical_state`` (owner submission mode, receipted & idempotent).
PERCEPTION_TIERS: tuple[str, ...] = ("distant", "mid", "near", "interactive")
MAX_POI_PHYSICAL_STATES = 32

PLACES_BLUEPRINT: tuple[dict[str, Any], ...] = (
    {
        "place_id": "home",
        "name": "家 · 起居与暂放台",
        "ring": "home",
        "shelter": "indoor",
        "supports": ["home_desk", "home_staging_table", "home_rack"],
        "open_window": None,
        "initial_knowledge_status": "visited",
        "clue_note": None,
    },
    {
        "place_id": "porch",
        "name": "家门口 · 有檐门廊与归还箱/留言板",
        "ring": "doorstep",
        "shelter": "sheltered",
        "supports": ["porch_hook", "porch_return_box", "porch_message_board"],
        "open_window": None,
        "initial_knowledge_status": "visited",
        "clue_note": None,
    },
    {
        "place_id": "yard",
        "name": "家门口 · 露天小院、晾晒绳与木长椅",
        "ring": "doorstep",
        "shelter": "open_air",
        "supports": ["yard_line", "yard_bench"],
        "open_window": None,
        "initial_knowledge_status": "visited",
        "clue_note": None,
    },
    {
        "place_id": "street_corner",
        "name": "步行街区 · 坡道街角与共享架/公共告示栏",
        "ring": "neighborhood",
        "shelter": "open_air",
        "supports": ["shared_shelf", "public_notice_board"],
        "open_window": None,
        "initial_knowledge_status": "visited",
        "clue_note": None,
    },
    {
        "place_id": "bookstore",
        "name": "步行街区 · 街角旧书店",
        "ring": "neighborhood",
        "shelter": "indoor",
        "supports": [
            "bookstore_shelf",
            "bookstore_counter",
            "bookstore_display_board",
            "bookstore_return_box",
        ],
        "open_window": "10:00-19:00",
        "initial_knowledge_status": "told_clue",
        "clue_note": "听说坡道街角往石桥方向有一家营业到傍晚的街角旧书店。",
    },
    {
        "place_id": "riverside",
        "name": "步行街区 · 河边长椅步道",
        "ring": "neighborhood",
        "shelter": "open_air",
        "supports": ["riverside_bench"],
        "open_window": None,
        "initial_knowledge_status": "unknown",
        "clue_note": None,
    },
)

PLACES_BY_ID: dict[str, dict[str, Any]] = {
    row["place_id"]: copy.deepcopy(row) for row in PLACES_BLUEPRINT
}

CLOTH_SUPPORTS: dict[str, dict[str, str]] = {
    "home_rack": {"place_id": "home", "shelter": "indoor", "label": "室内晾衣架"},
    "porch_hook": {"place_id": "porch", "shelter": "sheltered", "label": "门廊檐下挂钩"},
    "yard_line": {"place_id": "yard", "shelter": "open_air", "label": "小院露天晾晒绳"},
    "hand_bag": {"place_id": "carried", "shelter": "enclosed_bag", "label": "随身帆布袋内"},
}

DRY_SLEEP_THRESHOLD = 5.0
DAMP_UPPER_THRESHOLD = 40.0

# Hourly moisture delta at medium airflow (negative = drying, positive = wetting by rain)
BASE_MOISTURE_RATE_PER_HOUR: dict[tuple[str, str], float] = {
    ("open_air", "clear"): -28.0,
    ("open_air", "drizzle"): 18.0,
    ("open_air", "rain"): 45.0,
    ("sheltered", "clear"): -18.0,
    ("sheltered", "drizzle"): -10.0,
    ("sheltered", "rain"): -6.0,
    ("indoor", "clear"): -12.0,
    ("indoor", "drizzle"): -9.0,
    ("indoor", "rain"): -7.5,
    ("enclosed_bag", "clear"): -1.5,
    ("enclosed_bag", "drizzle"): -1.2,
    ("enclosed_bag", "rain"): -1.0,
}

AIRFLOW_DRYING_MULTIPLIER: dict[str, float] = {
    "weak": 0.7,
    "medium": 1.0,
    "strong": 1.35,
}
AIRFLOW_WETTING_MULTIPLIER: dict[str, float] = {
    "weak": 0.9,
    "medium": 1.0,
    "strong": 1.15,
}

PLANT_ALLOWED_PLACES: frozenset[str] = frozenset({"home", "porch", "yard"})
PLANT_STAGES: tuple[str, ...] = (
    "seedling",
    "leafy",
    "thriving",
    "paused",
    "wilting_recoverable",
)

# ---------------------------------------------------------------------------
# Batch B: Routes & Process-Based Journey
# ---------------------------------------------------------------------------

ROUTES_CATALOG: dict[str, dict[str, Any]] = {
    "home_to_porch": {
        "route_id": "home_to_porch",
        "endpoints": ("home", "porch"),
        "duration_minutes": 1,
        "duration_seconds": 60,
        "sheltered": True,
        "label": "玄关至有檐门廊",
    },
    "porch_to_yard": {
        "route_id": "porch_to_yard",
        "endpoints": ("porch", "yard"),
        "duration_minutes": 1,
        "duration_seconds": 60,
        "sheltered": False,
        "label": "门廊台阶至露天小院",
    },
    "open_shortcut": {
        "route_id": "open_shortcut",
        "endpoints": ("yard", "street_corner"),
        "duration_minutes": 4,
        "duration_seconds": 240,
        "sheltered": False,
        "label": "院门至街角露天近路",
    },
    "eaves_walkway": {
        "route_id": "eaves_walkway",
        "endpoints": ("yard", "street_corner"),
        "duration_minutes": 7,
        "duration_seconds": 420,
        "sheltered": True,
        "label": "院门至街角有檐长廊",
    },
    "street_to_bookstore": {
        "route_id": "street_to_bookstore",
        "endpoints": ("street_corner", "bookstore"),
        "duration_minutes": 5,
        "duration_seconds": 300,
        "sheltered": False,
        "label": "街角至旧书店石板路",
    },
    "street_to_riverside": {
        "route_id": "street_to_riverside",
        "endpoints": ("street_corner", "riverside"),
        "duration_minutes": 6,
        "duration_seconds": 360,
        "sheltered": False,
        "label": "街角至河边长椅步道",
    },
}

JOURNEY_STATES: tuple[str, ...] = ("at_place", "in_transit", "paused_at_node", "arrived")
KNOWLEDGE_STATUSES: tuple[str, ...] = ("visited", "told_clue", "unknown")

# ---------------------------------------------------------------------------
# Batch C: Craft Bench & Card Organizer Box
# ---------------------------------------------------------------------------

INITIAL_PAPER_UNITS = 8
BOX_STEPS: tuple[str, ...] = ("prepared", "divider_cut", "trial_fitted", "finished", "set_aside")

# ---------------------------------------------------------------------------
# Batch D/E/F: Bookstore, Shared Shelf, Public Notices & Place Booklet
# ---------------------------------------------------------------------------

BOOKSTORE_STARTER_BOOKS: tuple[dict[str, Any], ...] = (
    {
        "book_id": "book_botanical_plates",
        "title": "路边耐阴植物图谱",
        "author_name": "佐野木实",
        "cover_summary": "亚麻布脊旧画册，封皮印着常春藤与石韦叶脉",
        "toc": [
            "一、有檐门廊的散射光",
            "二、雨季青苔与石阶",
            "三、离家数日的储水托盘",
        ],
        "sample_excerpt": "门廊檐下的风比院心慢半拍，常春藤在半干的陶土里反而生得结实。",
    },
    {
        "book_id": "book_paper_joinery",
        "title": "纸盒与风琴折手工志",
        "author_name": "森田澄",
        "cover_summary": "牛皮纸装帧小开本，书口夹着刻度纸样",
        "toc": [
            "一、卡纸纹理方向",
            "二、十字隔板试装余量",
            "三、松脱折角的补丁贴法",
        ],
        "sample_excerpt": "试装偏紧时不必拆毁重裁，留出半毫米折边反而更稳。",
    },
    {
        "book_id": "book_river_sketches",
        "title": "旧街坡道与河岸散记",
        "author_name": "白石周",
        "cover_summary": "浅蓝封面薄册，扉页画着石桥与旧书店橱窗",
        "toc": [
            "一、坡道转角的长椅",
            "二、午后河风的声响",
            "三、雨天绕去长廊的路",
        ],
        "sample_excerpt": "下午四点以后河边水声会盖过说话声，想安静读字的人多半退回坡道上。",
    },
)

SHARED_SHELF_STARTER_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "item_id": "paper_ruler",
        "name": "钢边裁纸尺",
        "category": "craft_tool",
    },
    {
        "item_id": "craft_clips",
        "name": "榉木固定夹（四枚装）",
        "category": "craft_tool",
    },
)

QUIETNESS_BANDS: tuple[str, ...] = ("quiet", "moderate", "noisy")

ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,119}$")
MAX_CARDS = 64
MAX_NOTICES = 48
MAX_OPERATION_RECEIPTS = 128

INJECTION_STRIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\s*/?\s*(?:system|assistant|developer|user|tool_call|function_call)\b[^>]*>", re.I),
    re.compile(r"(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?", re.I),
    re.compile(r"忽略(?:之前|以上|所有)(?:的)?(?:指令|规则|约束)"),
)


class V04NeighborhoodError(ValueError):
    """Structured domain error carrying exact HTTP status and machine-readable reason_code."""

    def __init__(
        self,
        reason_code: str,
        detail: str = "",
        *,
        http_status: int = 400,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(detail or reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.http_status = int(http_status)
        self.extra: dict[str, Any] = dict(extra or {})


# ---------------------------------------------------------------------------
# Pure Deterministic Time, Hash & Sanitization Helpers
# ---------------------------------------------------------------------------


def parse_iso_moment(value: Any, *, field_name: str = "now_iso") -> datetime:
    """Parse ISO-8601 datetime while preserving declared timezone offset for wall-clock checks."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=DEFAULT_TZ)
        return dt.replace(microsecond=0)
    if not isinstance(value, str) or not value.strip():
        raise V04NeighborhoodError(
            "INVALID_TIMESTAMP",
            f"{field_name} must be a non-empty ISO-8601 string",
            http_status=400,
        )
    raw = value.strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise V04NeighborhoodError(
            "INVALID_TIMESTAMP",
            f"Malformed ISO-8601 timestamp for {field_name}: {value!r}",
            http_status=400,
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt.replace(microsecond=0)


def format_iso(dt: datetime) -> str:
    if dt.utcoffset() == timedelta(0):
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return dt.replace(microsecond=0).isoformat()


def resolve_now_readonly(day_root: Path | str, now_iso: Optional[str]) -> datetime:
    """Resolve current logical instant with 100% zero disk writes."""
    if now_iso is not None:
        return parse_iso_moment(now_iso, field_name="now_iso")
    root = Path(day_root)
    clock_path = root / "fastday" / "fastday_clock.json"
    try:
        if not clock_path.is_symlink() and clock_path.is_file():
            doc = json.loads(clock_path.read_text(encoding="utf-8"))
            if isinstance(doc, Mapping):
                for key in ("logical_at", "now_iso", "current_iso"):
                    cand = doc.get(key)
                    if isinstance(cand, str) and cand.strip():
                        return parse_iso_moment(cand, field_name=key)
    except (OSError, ValueError, V04NeighborhoodError):
        pass
    return datetime.now(DEFAULT_TZ).replace(microsecond=0)


def canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _stable_id(prefix: str, *parts: Any) -> str:
    joined = "\x00".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _clean_text(
    value: Any,
    *,
    field_name: str,
    min_len: int = 1,
    max_len: int = 400,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise V04NeighborhoodError(
            "INVALID_FIELDS", f"{field_name} must be a valid string", http_status=400
        )
    if allow_newlines:
        cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    else:
        cleaned = " ".join(value.split())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            f"{field_name} length must be within {min_len}..{max_len}",
            http_status=400,
        )
    return cleaned


def _optional_text(
    value: Any,
    *,
    field_name: str,
    max_len: int = 400,
    allow_newlines: bool = False,
) -> Optional[str]:
    if value is None:
        return None
    return _clean_text(
        value, field_name=field_name, min_len=1, max_len=max_len, allow_newlines=allow_newlines
    )


def _sanitize_public_text(text: str) -> str:
    out = text
    for pattern in INJECTION_STRIP_PATTERNS:
        out = pattern.sub("[filtered]", out)
    return out.strip() or "[filtered]"


def _require_id(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise V04NeighborhoodError(
            "INVALID_FIELDS", f"{field_name} must be a string identifier", http_status=400
        )
    cleaned = value.strip()
    if not ID_TOKEN_RE.fullmatch(cleaned):
        raise V04NeighborhoodError(
            "INVALID_FIELDS", f"Malformed identifier in {field_name}", http_status=400
        )
    return cleaned


def _positive_int(value: Any, *, field_name: str, maximum: int = 1000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            f"{field_name} must be a positive integer in 1..{maximum}",
            http_status=400,
        )
    return int(value)


def _nonneg_int(value: Any, *, field_name: str, maximum: int = 10000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            f"{field_name} must be a non-negative integer in 0..{maximum}",
            http_status=400,
        )
    return int(value)


def poi_open_window_minutes(place_id: str) -> Optional[tuple[int, int]]:
    """Declared business-hours window of a POI as ``(start_min, end_min)`` wall-clock minutes.

    ``None`` means the POI has no declared closing time (outdoor / doorstep places
    are always available) or the place id is unknown (callers that dispatch into
    the place will surface ``PLACE_NOT_FOUND`` through the normal path).
    """
    row = PLACES_BY_ID.get(str(place_id))
    window = row.get("open_window") if row else None
    if not window:
        return None
    start_text, end_text = str(window).split("-", 1)
    start_h, start_m = start_text.strip().split(":", 1)
    end_h, end_m = end_text.strip().split(":", 1)
    return int(start_h) * 60 + int(start_m), int(end_h) * 60 + int(end_m)


def is_poi_open(place_id: str, now_dt: datetime) -> bool:
    """Whether the POI is open at ``now_dt`` (caller passes neighborhood-local wall-clock time).

    v3.0 Ch.02 §1.3: a slow-variable manifestation bound to a POI (``binds``) may
    only physically land while that POI is open; the v03 kernel consults this
    helper and holds the contract (``POI_CLOSED``) until the next opening.
    """
    window = poi_open_window_minutes(place_id)
    if window is None:
        return True
    start_min, end_min = window
    cur_min = now_dt.hour * 60 + now_dt.minute
    return start_min <= cur_min < end_min


def _is_bookstore_open(now_dt: datetime) -> bool:
    return is_poi_open("bookstore", now_dt)


def read_character_identity_readonly(day_root: Path | str) -> dict[str, Any]:
    """Read character display_name and pronouns with zero disk writes (neutral default 'TA')."""
    root = Path(day_root)
    pack_path = root / "phone" / "character_pack_state.json"
    default_id = {
        "pack_id": "neutral_resident",
        "display_name": "TA",
        "pronouns": "TA",
        "locked_to_chiyo": False,
    }
    try:
        if pack_path.is_symlink() or not pack_path.is_file():
            return default_id
        raw = json.loads(pack_path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping):
            pack = raw.get("pack") or raw.get("pack_doc")
            if isinstance(pack, Mapping):
                display_name = str(pack.get("display_name") or "TA").strip() or "TA"
                pronouns = str(pack.get("pronouns") or "TA").strip() or "TA"
                pack_id = str(pack.get("pack_id") or "neutral_resident").strip() or "neutral_resident"
                return {
                    "pack_id": pack_id,
                    "display_name": display_name,
                    "pronouns": pronouns,
                    "locked_to_chiyo": False,
                }
    except (OSError, ValueError):
        pass
    return default_id


# ---------------------------------------------------------------------------
# Default State & Persistence (<day_root>/world/v04_neighborhood_state.json)
# ---------------------------------------------------------------------------


def state_file_path(day_root: Path | str) -> Path:
    return Path(day_root) / STATE_DIRNAME / STATE_FILENAME


def default_state() -> dict[str, Any]:
    places: dict[str, dict[str, Any]] = {}
    for row in PLACES_BLUEPRINT:
        pid = row["place_id"]
        places[pid] = {
            "place_id": pid,
            "name": row["name"],
            "ring": row["ring"],
            "shelter": row["shelter"],
            "supports": list(row["supports"]),
            "open_window": row["open_window"],
            "knowledge_status": row["initial_knowledge_status"],
            "clue_note": row["clue_note"],
            "last_visited_at": None,
            "visit_count": 1 if row["initial_knowledge_status"] == "visited" else 0,
            "physical_states": [],
        }

    books: dict[str, dict[str, Any]] = {}
    for b in BOOKSTORE_STARTER_BOOKS:
        bid = b["book_id"]
        books[bid] = {
            "book_id": bid,
            "title": b["title"],
            "author_name": b["author_name"],
            "cover_summary": b["cover_summary"],
            "toc": list(b["toc"]),
            "sample_excerpt": b["sample_excerpt"],
            "item_owner": "bookstore",
            "holder": "bookstore",
            "custody_place_id": "bookstore",
            "status": "available",
            "reserved_by": None,
            "reserved_at": None,
            "reservation_expires_at": None,
            "last_expired_reservation_at": None,
            "borrowed_at": None,
            "due_at": None,
            "returned_at": None,
            "return_receipt_kind": None,
            "shopkeeper_received": False,
            "ownership_transferred": False,
            "paid": False,
        }

    shelf_items: dict[str, dict[str, Any]] = {}
    for s in SHARED_SHELF_STARTER_ITEMS:
        sid = s["item_id"]
        shelf_items[sid] = {
            "item_id": sid,
            "name": s["name"],
            "category": s["category"],
            "item_owner": "community_shared_shelf",
            "holder": "shared_shelf",
            "place_id": "street_corner",
            "status": "on_shelf",
            "borrowed_by": None,
            "borrowed_at": None,
            "due_at": None,
            "returned_at": None,
            "extension_count": 0,
            "extension_history": [],
        }

    return {
        "schema": ENGINE_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "environment": {
            "weather": "clear",
            "airflow": "medium",
            "season": "autumn",
            "updated_at": None,
        },
        "places": places,
        "world_truth": {
            "roadblocks": {},
        },
        "journey": {
            "state": "at_place",
            "current_place_id": "home",
            "origin_place_id": "home",
            "target_place_id": None,
            "active_route_id": None,
            "active_leg_index": 0,
            "legs": [],
            "completed_route_ids": [],
            "is_turning_back": False,
            "turned_back_at": None,
            "started_at": None,
            "segment_started_at": None,
            "expected_arrival_at": None,
            "arrived_at": None,
            "accumulated_walk_seconds": 0.0,
            "paused_walked_seconds": 0.0,
            "halt_reason": None,
            "blocked_route_id": None,
            "carried_item_ids": [],
            "discovered_roadblocks": [],
        },
        "drying_items": {
            "wet_cloth_main": {
                "item_id": "wet_cloth_main",
                "name": "棉麻布巾",
                "support_id": "home_rack",
                "moisture": 75.0,
                "segment_start_moisture": 75.0,
                "segment_started_at": None,
                "last_anchor_at": None,
                "segment_count": 0,
                "sleeping_dry": False,
                "dried_at": None,
            }
        },
        "potted_plant": {
            "plant_id": "porch_fern",
            "name": "小叶常春藤",
            "place_id": "porch",
            "hydration": 70.0,
            "growth_points": 0.0,
            "segment_start_hydration": 70.0,
            "segment_start_growth": 0.0,
            "segment_started_at": None,
            "last_anchor_at": None,
            "last_watered_at": None,
            "stage": "seedling",
            "public_care_enabled": False,
            "never_dies_offline": True,
        },
        "craft_bench": {
            "paper_units": INITIAL_PAPER_UNITS,
            "scrap_units": 0,
            "total_stocked_units": INITIAL_PAPER_UNITS,
            "consumed_in_cards_units": 0,
            "consumed_in_box_units": 0,
            "card_seq": 0,
            "cards": {},
            "card_organizer_box": {
                "box_id": "card_organizer_box",
                "name": "旧纸盒改卡片收纳盒",
                "step": "prepared",
                "last_active_step": "prepared",
                "dividers_cut": 0,
                "dividers": [],
                "fit_note": None,
                "visible_patch": False,
                "patch_count": 0,
                "patches": [],
                "updated_at": None,
            },
        },
        "public_life": {
            "bookstore": {
                "books": books,
                "display_cards": [],
            },
            "shared_shelf": {
                "items": shelf_items,
            },
            "public_notices": [],
            "notice_seq": 0,
            "place_booklet": {
                "booklet_id": "quiet_reading_places_booklet",
                "title": "家附近能安静读书的地点小册",
                "purpose": "记录亲自去过的几处座位在不同时段的安静程度与光线感受",
                "status": "idle",
                "shared_with_partner": False,
                "revision": 0,
                "paused_at": None,
                "pause_note": None,
                "entries": {},
                "source_artifact_id": None,
                "work_ref": None,
                "updated_at": None,
            },
        },
        "operation_receipts": {},
    }


def _check_path_symlinks(day_root: Path) -> Path:
    if day_root.exists() and day_root.is_symlink():
        raise V04NeighborhoodError(
            "STATE_SYMLINK_REFUSED", "day_root must not be a symlink", http_status=503
        )
    world_dir = day_root / STATE_DIRNAME
    if world_dir.exists() and world_dir.is_symlink():
        raise V04NeighborhoodError(
            "STATE_SYMLINK_REFUSED", "world directory must not be a symlink", http_status=503
        )
    path = world_dir / STATE_FILENAME
    if path.is_symlink():
        raise V04NeighborhoodError(
            "STATE_SYMLINK_REFUSED", "state file must not be a symlink", http_status=503
        )
    return path


def load_state_readonly(day_root: Path | str) -> tuple[dict[str, Any], bool]:
    """Load persisted state with 100% zero disk writes. Fails closed on corrupt JSON or symlink."""
    root = Path(day_root)
    path = _check_path_symlinks(root)
    if not path.exists():
        return default_state(), False
    if not path.is_file():
        raise V04NeighborhoodError(
            "STATE_CORRUPTED", "State path is not a regular file", http_status=503
        )
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
    except (OSError, ValueError) as exc:
        raise V04NeighborhoodError(
            "STATE_CORRUPTED", "State file is unreadable or invalid JSON", http_status=503
        ) from exc

    if (
        not isinstance(raw, Mapping)
        or raw.get("schema") != ENGINE_SCHEMA
        or raw.get("schema_version") != STATE_SCHEMA_VERSION
    ):
        raise V04NeighborhoodError(
            "STATE_CORRUPTED", "State file schema mismatch or corrupted root object", http_status=503
        )
    required_sections = (
        "environment",
        "places",
        "world_truth",
        "journey",
        "drying_items",
        "potted_plant",
        "craft_bench",
        "public_life",
        "operation_receipts",
    )
    for sec in required_sections:
        if not isinstance(raw.get(sec), Mapping):
            raise V04NeighborhoodError(
                "STATE_CORRUPTED", f"Missing or corrupted section: {sec}", http_status=503
            )
    rev = raw.get("revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise V04NeighborhoodError("STATE_CORRUPTED", "Invalid state revision", http_status=503)

    state = copy.deepcopy(dict(raw))
    state["potted_plant"]["never_dies_offline"] = True
    return state, True


def save_state_atomic(day_root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically persist state to <day_root>/world/v04_neighborhood_state.json with 0600 perms."""
    root = Path(day_root)
    path = _check_path_symlinks(root)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise V04NeighborhoodError(
            "STATE_SYMLINK_REFUSED", "world directory must not be a symlink", http_status=503
        )

    doc = copy.deepcopy(dict(state))
    doc["schema"] = ENGINE_SCHEMA
    doc["schema_version"] = STATE_SCHEMA_VERSION
    doc["potted_plant"]["never_dies_offline"] = True

    encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return doc


# ---------------------------------------------------------------------------
# Piecewise Environmental Drying & Tolerant Plant Math (Batch A)
# ---------------------------------------------------------------------------


def compute_cloth_hourly_rate(support_id: str, weather: str, airflow: str) -> float:
    spec = CLOTH_SUPPORTS.get(support_id, CLOTH_SUPPORTS["home_rack"])
    shelter = spec["shelter"]
    base = BASE_MOISTURE_RATE_PER_HOUR.get((shelter, weather), -10.0)
    if base < 0:
        mult = AIRFLOW_DRYING_MULTIPLIER.get(airflow, 1.0)
    else:
        mult = AIRFLOW_WETTING_MULTIPLIER.get(airflow, 1.0)
    return round(base * mult, 6)


def moisture_to_band(moisture: float) -> str:
    if moisture <= DRY_SLEEP_THRESHOLD:
        return "dry"
    if moisture <= DAMP_UPPER_THRESHOLD:
        return "damp"
    return "wet"


def _settle_single_cloth_in_place(
    item: dict[str, Any],
    *,
    weather: str,
    airflow: str,
    now_dt: datetime,
) -> bool:
    """Advance one drying item along its current constant-condition segment.

    Uses (segment_start_moisture, segment_started_at) so arbitrary intermediate
    reads or advances yield bit-identical moisture values with zero float drift.
    """
    seg_start_raw = item.get("segment_started_at") or item.get("last_anchor_at")
    if not seg_start_raw:
        return False

    rate = compute_cloth_hourly_rate(item["support_id"], weather, airflow)
    # If already sleeping_dry and rate <= 0 (not being rained on in open_air), stay asleep!
    if item.get("sleeping_dry") and rate <= 0:
        return False

    seg_dt = parse_iso_moment(seg_start_raw, field_name="segment_started_at")
    anchor_raw = item.get("last_anchor_at") or seg_start_raw
    anchor_dt = parse_iso_moment(anchor_raw, field_name="last_anchor_at")
    if now_dt <= anchor_dt:
        return False

    changed = False
    # If sleeping_dry was True on open_air and rain started without explicit wake, wake at anchor_dt
    if item.get("sleeping_dry") and rate > 0:
        item["sleeping_dry"] = False
        item["dried_at"] = None
        item["segment_start_moisture"] = float(item.get("moisture", DRY_SLEEP_THRESHOLD))
        item["segment_started_at"] = format_iso(anchor_dt)
        seg_dt = anchor_dt
        changed = True

    total_hours = max(0.0, (now_dt - seg_dt).total_seconds() / 3600.0)
    start_m = float(item.get("segment_start_moisture", item.get("moisture", 75.0)))
    raw_m = start_m + rate * total_hours

    if rate < 0:
        if start_m <= DRY_SLEEP_THRESHOLD:
            new_m = start_m
            if not item.get("sleeping_dry"):
                item["sleeping_dry"] = True
                item["dried_at"] = item.get("dried_at") or format_iso(seg_dt)
                changed = True
        elif raw_m <= DRY_SLEEP_THRESHOLD:
            new_m = DRY_SLEEP_THRESHOLD
            hours_to_dry = (start_m - DRY_SLEEP_THRESHOLD) / (-rate)
            dry_dt = seg_dt + timedelta(seconds=round(hours_to_dry * 3600.0))
            item["sleeping_dry"] = True
            item["dried_at"] = format_iso(dry_dt)
            item["last_anchor_at"] = format_iso(dry_dt if dry_dt <= now_dt else now_dt)
            if abs(float(item.get("moisture", 0.0)) - new_m) > 1e-9:
                item["moisture"] = round(new_m, 6)
            return True
        else:
            new_m = min(100.0, raw_m)
    else:
        new_m = min(100.0, max(0.0, raw_m))
        if new_m > DRY_SLEEP_THRESHOLD and item.get("sleeping_dry"):
            item["sleeping_dry"] = False
            item["dried_at"] = None

    new_m_rounded = round(new_m, 6)
    now_str = format_iso(now_dt)
    if abs(float(item.get("moisture", 0.0)) - new_m_rounded) > 1e-9 or item.get("last_anchor_at") != now_str:
        item["moisture"] = new_m_rounded
        item["last_anchor_at"] = now_str
        changed = True
    return changed


def _cut_cloth_segment_for_regime_change(
    item: dict[str, Any],
    *,
    old_weather: str,
    old_airflow: str,
    new_weather: str,
    new_airflow: str,
    new_support_id: str,
    now_dt: datetime,
) -> None:
    """Settle old segment up to now_dt, then cut a new segment anchor at now_dt."""
    now_str = format_iso(now_dt)
    if item.get("segment_started_at") is not None:
        _settle_single_cloth_in_place(item, weather=old_weather, airflow=old_airflow, now_dt=now_dt)

    new_rate = compute_cloth_hourly_rate(new_support_id, new_weather, new_airflow)
    was_sleeping = bool(item.get("sleeping_dry"))
    support_changed = item["support_id"] != new_support_id
    env_changed = (old_weather != new_weather) or (old_airflow != new_airflow)

    item["support_id"] = new_support_id
    if was_sleeping and new_rate <= 0:
        # Remains dry & asleep; only increment segment_count if physically moved to a new support
        if support_changed:
            item["segment_count"] = int(item.get("segment_count", 0)) + 1
            item["last_anchor_at"] = now_str
        return

    if was_sleeping and new_rate > 0:
        # Open-air rain wakes up a dry cloth!
        item["sleeping_dry"] = False
        item["dried_at"] = None

    if support_changed or env_changed or item.get("segment_started_at") is None:
        item["segment_count"] = int(item.get("segment_count", 0)) + 1

    item["segment_start_moisture"] = float(item.get("moisture", 75.0))
    item["segment_started_at"] = now_str
    item["last_anchor_at"] = now_str


def _derive_plant_stage(hydration: float, growth_points: float) -> str:
    if hydration <= 10.0:
        return "wilting_recoverable"
    if hydration <= 25.0:
        return "paused"
    if growth_points >= 72.0:
        return "thriving"
    if growth_points >= 24.0:
        return "leafy"
    return "seedling"


def _settle_plant_in_place(
    plant: dict[str, Any],
    *,
    weather: str,
    now_dt: datetime,
) -> bool:
    plant["never_dies_offline"] = True
    seg_raw = plant.get("segment_started_at") or plant.get("last_anchor_at")
    if not seg_raw:
        plant["stage"] = _derive_plant_stage(
            float(plant.get("hydration", 70.0)), float(plant.get("growth_points", 0.0))
        )
        return False

    seg_dt = parse_iso_moment(seg_raw, field_name="plant.segment_started_at")
    anchor_raw = plant.get("last_anchor_at") or seg_raw
    anchor_dt = parse_iso_moment(anchor_raw, field_name="plant.last_anchor_at")
    if now_dt <= anchor_dt:
        return False

    hours = max(0.0, (now_dt - seg_dt).total_seconds() / 3600.0)
    start_h = float(plant.get("segment_start_hydration", plant.get("hydration", 70.0)))
    start_g = float(plant.get("segment_start_growth", plant.get("growth_points", 0.0)))
    place_id = str(plant.get("place_id") or "porch")

    if plant.get("public_care_enabled"):
        # Neighbor public care keeps soil comfortably moist while away
        target_h = max(start_h, 60.0) if start_h < 60.0 else max(55.0, start_h - 0.15 * hours)
        new_h = min(100.0, target_h)
        new_g = start_g + 0.8 * hours
    else:
        if place_id == "yard" and weather in ("drizzle", "rain"):
            rain_gain = 14.0 if weather == "drizzle" else 28.0
            new_h = min(98.0, start_h + rain_gain * hours)
        else:
            loss_rate = {"yard": 1.5, "porch": 1.0, "home": 0.8}.get(place_id, 1.0)
            new_h = max(0.0, start_h - loss_rate * hours)

        # Growth accumulates only while well-hydrated (> 25.0)
        if start_h > 25.0:
            loss_rate = {"yard": 1.5, "porch": 1.0, "home": 0.8}.get(place_id, 1.0)
            if new_h > 25.0 or (place_id == "yard" and weather in ("drizzle", "rain")):
                productive_hours = hours
            else:
                productive_hours = max(0.0, (start_h - 25.0) / loss_rate)
            light_factor = {"yard": 1.25, "porch": 1.1, "home": 0.85}.get(place_id, 1.0)
            new_g = start_g + 1.2 * light_factor * productive_hours
        else:
            new_g = start_g

    new_h_r = round(new_h, 4)
    new_g_r = round(new_g, 4)
    new_stage = _derive_plant_stage(new_h_r, new_g_r)
    now_str = format_iso(now_dt)

    changed = (
        abs(float(plant.get("hydration", 0.0)) - new_h_r) > 1e-6
        or abs(float(plant.get("growth_points", 0.0)) - new_g_r) > 1e-6
        or plant.get("stage") != new_stage
    )
    plant["hydration"] = new_h_r
    plant["growth_points"] = new_g_r
    plant["stage"] = new_stage
    plant["last_anchor_at"] = now_str
    return changed


def _reanchor_plant_segment(plant: dict[str, Any], *, old_weather: str, now_dt: datetime) -> None:
    if plant.get("segment_started_at") is not None:
        _settle_plant_in_place(plant, weather=old_weather, now_dt=now_dt)
    now_str = format_iso(now_dt)
    plant["segment_start_hydration"] = float(plant.get("hydration", 70.0))
    plant["segment_start_growth"] = float(plant.get("growth_points", 0.0))
    plant["segment_started_at"] = now_str
    plant["last_anchor_at"] = now_str
    plant["stage"] = _derive_plant_stage(plant["hydration"], plant["growth_points"])


# ---------------------------------------------------------------------------
# Graph Pathfinding & Journey Settlement (Batch B)
# ---------------------------------------------------------------------------


def _build_route_legs(
    src: str,
    dst: str,
    *,
    branch_route_id: str = "open_shortcut",
) -> list[dict[str, Any]]:
    """Compute unique shortest path legs between src and dst using selected yard<->street branch."""
    if src == dst:
        return []
    if branch_route_id not in ("open_shortcut", "eaves_walkway"):
        branch_route_id = "open_shortcut"

    active_routes = [
        r for rid, r in ROUTES_CATALOG.items()
        if rid not in ("open_shortcut", "eaves_walkway") or rid == branch_route_id
    ]
    adj: dict[str, list[tuple[str, dict[str, Any]]]] = {pid: [] for pid in PLACES_BY_ID}
    for r in active_routes:
        a, b = r["endpoints"]
        adj[a].append((b, r))
        adj[b].append((a, r))

    queue: list[tuple[str, list[dict[str, Any]]]] = [(src, [])]
    seen = {src}
    while queue:
        node, path_so_far = queue.pop(0)
        if node == dst:
            return path_so_far
        for nxt, r in adj.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            leg = {
                "from_place": node,
                "to_place": nxt,
                "route_id": r["route_id"],
                "duration_seconds": int(r["duration_seconds"]),
            }
            queue.append((nxt, path_so_far + [leg]))

    raise V04NeighborhoodError(
        "UNREACHABLE_PLACE", f"No path from {src!r} to {dst!r}", http_status=409
    )


def _mark_place_visited(state: dict[str, Any], place_id: str, visited_iso: str) -> None:
    place = state["places"].get(place_id)
    if place is not None:
        place["knowledge_status"] = "visited"
        place["last_visited_at"] = visited_iso
        place["visit_count"] = int(place.get("visit_count") or 0) + 1


def _add_discovered_roadblock(journey: dict[str, Any], route_id: str) -> None:
    discovered = list(journey.get("discovered_roadblocks") or [])
    if route_id not in discovered:
        discovered.append(route_id)
        journey["discovered_roadblocks"] = discovered


def _remove_discovered_roadblock(journey: dict[str, Any], route_id: str) -> None:
    discovered = [r for r in (journey.get("discovered_roadblocks") or []) if r != route_id]
    journey["discovered_roadblocks"] = discovered


def _is_route_really_blocked(state: Mapping[str, Any], route_id: str) -> bool:
    rb = (state.get("world_truth") or {}).get("roadblocks", {}).get(route_id)
    return bool(isinstance(rb, Mapping) and rb.get("blocked") is True)


def _settle_journey_in_place(state: dict[str, Any], now_dt: datetime) -> bool:
    """Advance in-transit journey up to now_dt, stopping at undiscovered roadblocks or arriving."""
    journey = state["journey"]
    if journey.get("state") != "in_transit":
        return False

    legs: list[dict[str, Any]] = list(journey.get("legs") or [])
    idx = int(journey.get("active_leg_index") or 0)
    seg_raw = journey.get("segment_started_at") or journey.get("started_at")
    if not legs or idx >= len(legs) or not seg_raw:
        dst = journey.get("target_place_id") or journey.get("origin_place_id") or "home"
        journey["state"] = "arrived"
        journey["current_place_id"] = dst
        journey["active_route_id"] = None
        journey["arrived_at"] = format_iso(now_dt)
        _mark_place_visited(state, dst, format_iso(now_dt))
        return True

    cursor_dt = parse_iso_moment(seg_raw, field_name="journey.segment_started_at")
    if now_dt < cursor_dt:
        return False

    changed = False
    is_turning_back = bool(journey.get("is_turning_back"))

    while idx < len(legs):
        leg = legs[idx]
        route_id = leg["route_id"]
        dur_sec = max(1, int(leg["duration_seconds"]))

        # Real-world roadblock check when entering or traversing a forward leg
        if not is_turning_back and _is_route_really_blocked(state, route_id):
            _add_discovered_roadblock(journey, route_id)
            rb_entry = state["world_truth"]["roadblocks"][route_id]
            rb_dt = parse_iso_moment(rb_entry["updated_at"], field_name="roadblock.updated_at")
            if rb_dt <= cursor_dt:
                # Blocked before stepping onto this leg -> halt right at leg["from_place"]
                journey["state"] = "paused_at_node"
                journey["current_place_id"] = leg["from_place"]
                journey["active_leg_index"] = idx
                journey["active_route_id"] = None
                journey["paused_walked_seconds"] = 0.0
                journey["halt_reason"] = "ROADBLOCK_ENCOUNTERED"
                journey["blocked_route_id"] = route_id
                _mark_place_visited(state, leg["from_place"], format_iso(cursor_dt))
                return True
            if rb_dt <= now_dt and rb_dt < cursor_dt + timedelta(seconds=dur_sec):
                # Blocked mid-leg while walking -> pause safely with walked progress preserved
                walked_sec = max(0.0, (rb_dt - cursor_dt).total_seconds())
                journey["state"] = "paused_at_node"
                journey["current_place_id"] = None
                journey["active_leg_index"] = idx
                journey["active_route_id"] = route_id
                journey["paused_walked_seconds"] = round(walked_sec, 3)
                journey["halt_reason"] = "ROADBLOCK_ENCOUNTERED"
                journey["blocked_route_id"] = route_id
                return True

        # Route is physically open -> clear any stale roadblock belief upon traversing
        if route_id in (journey.get("discovered_roadblocks") or []):
            _remove_discovered_roadblock(journey, route_id)
            changed = True

        leg_end_dt = cursor_dt + timedelta(seconds=dur_sec)
        if now_dt < leg_end_dt:
            if journey.get("active_leg_index") != idx or journey.get("active_route_id") != route_id:
                journey["active_leg_index"] = idx
                journey["active_route_id"] = route_id
                journey["segment_started_at"] = format_iso(cursor_dt)
                changed = True
            return changed

        # Completed this leg!
        completed = list(journey.get("completed_route_ids") or [])
        completed.append(route_id)
        journey["completed_route_ids"] = completed
        journey["accumulated_walk_seconds"] = round(
            float(journey.get("accumulated_walk_seconds") or 0.0) + float(dur_sec), 3
        )
        _mark_place_visited(state, leg["to_place"], format_iso(leg_end_dt))
        cursor_dt = leg_end_dt
        idx += 1
        journey["active_leg_index"] = idx
        journey["segment_started_at"] = format_iso(cursor_dt)
        changed = True

    # All legs completed -> arrived!
    final_place = legs[-1]["to_place"]
    journey["state"] = "arrived"
    journey["current_place_id"] = final_place
    journey["active_route_id"] = None
    journey["paused_walked_seconds"] = 0.0
    journey["halt_reason"] = None
    journey["blocked_route_id"] = None
    journey["arrived_at"] = format_iso(cursor_dt)
    return True


# ---------------------------------------------------------------------------
# Expirations & Unified Process Settlement (Batch D/E + Ch.43)
# ---------------------------------------------------------------------------


def _settle_bookstore_and_notices_in_place(state: dict[str, Any], now_dt: datetime) -> dict[str, int]:
    expired_reservations = 0
    books = state["public_life"]["bookstore"]["books"]
    for book in books.values():
        if book.get("status") == "reserved" and book.get("reservation_expires_at"):
            exp_dt = parse_iso_moment(
                book["reservation_expires_at"], field_name="reservation_expires_at"
            )
            if now_dt >= exp_dt:
                book["status"] = "available"
                book["reserved_by"] = None
                book["last_expired_reservation_at"] = book["reservation_expires_at"]
                book["reservation_expires_at"] = None
                expired_reservations += 1

    expired_notices = 0
    for notice in state["public_life"]["public_notices"]:
        if not notice.get("expired") and notice.get("expires_at"):
            exp_dt = parse_iso_moment(notice["expires_at"], field_name="notice.expires_at")
            if now_dt >= exp_dt:
                notice["expired"] = True
                expired_notices += 1

    return {
        "expired_reservations": expired_reservations,
        "expired_notices": expired_notices,
    }


def settle_all_processes_in_place(state: dict[str, Any], now_dt: datetime) -> dict[str, Any]:
    """Deterministically settle drying, plant, in-transit journey, reservations & notices."""
    env = state["environment"]
    weather = str(env.get("weather") or "clear")
    airflow = str(env.get("airflow") or "medium")

    cloths_advanced = 0
    for item in state["drying_items"].values():
        if _settle_single_cloth_in_place(item, weather=weather, airflow=airflow, now_dt=now_dt):
            cloths_advanced += 1

    plant_advanced = _settle_plant_in_place(state["potted_plant"], weather=weather, now_dt=now_dt)
    journey_advanced = _settle_journey_in_place(state, now_dt)
    pub_counts = _settle_bookstore_and_notices_in_place(state, now_dt)

    any_changed = (
        cloths_advanced > 0
        or plant_advanced
        or journey_advanced
        or pub_counts["expired_reservations"] > 0
        or pub_counts["expired_notices"] > 0
    )
    return {
        "changed": any_changed,
        "cloths_advanced": cloths_advanced,
        "plant_advanced": plant_advanced,
        "journey_advanced": journey_advanced,
        "expired_reservations": pub_counts["expired_reservations"],
        "expired_notices": pub_counts["expired_notices"],
    }


def advance_v04_neighborhood_processes(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Advance existing drying segments, in-transit arrival boundaries, reservations & notices.

    If no state file exists yet, returns quietly with 100% zero disk writes.
    """
    root = Path(day_root)
    state, exists = load_state_readonly(root)
    if not exists:
        return {"ok": True, "advanced": False, "llm_calls": 0}

    now_dt = resolve_now_readonly(root, now_iso)
    now_str = format_iso(now_dt)
    summary = settle_all_processes_in_place(state, now_dt)
    settled_count = (
        int(summary["cloths_advanced"])
        + (1 if summary["plant_advanced"] else 0)
        + (1 if summary["journey_advanced"] else 0)
        + int(summary["expired_reservations"])
        + int(summary["expired_notices"])
    )
    if not summary["changed"]:
        return {
            "ok": True,
            "advanced": False,
            "changed": False,
            "settled_count": 0,
            "changes_count": 0,
            "revision": int(state.get("revision") or 0),
            "observed_at": now_str,
            "llm_calls": 0,
            "model_called": False,
        }

    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_str
    save_state_atomic(root, state)
    return {
        "ok": True,
        "advanced": True,
        "changed": True,
        "settled_count": settled_count,
        "changes_count": settled_count,
        "revision": state["revision"],
        "observed_at": now_str,
        "changes": summary,
        "llm_calls": 0,
        "model_called": False,
    }


# ---------------------------------------------------------------------------
# Pure Projection (100% Zero Disk Writes, Perspective Redaction)
# ---------------------------------------------------------------------------


def _effective_carried_location(journey: Mapping[str, Any]) -> str:
    if journey.get("state") == "in_transit":
        rid = journey.get("active_route_id") or "transit"
        return f"in_transit:{rid}"
    return str(journey.get("current_place_id") or journey.get("origin_place_id") or "home")


def project_v04_neighborhood(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
    preloaded_state: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Project full v0.4 neighborhood view in memory with 100% zero disk writes."""
    root = Path(day_root)
    now_dt = resolve_now_readonly(root, now_iso)
    now_str = format_iso(now_dt)
    identity = read_character_identity_readonly(root)

    if preloaded_state is None:
        raw_state, _ = load_state_readonly(root)
    else:
        raw_state = copy.deepcopy(dict(preloaded_state))

    working = copy.deepcopy(raw_state)
    settle_all_processes_in_place(working, now_dt)

    perspective = "her_private" if has_subject_capability else "partner_default"
    env_raw = working["environment"]
    weather = str(env_raw.get("weather") or "clear")
    airflow = str(env_raw.get("airflow") or "medium")
    season = str(env_raw.get("season") or "autumn")
    bookstore_open = _is_bookstore_open(now_dt)

    # 1. Places & local ground exposure
    places_out: dict[str, dict[str, Any]] = {}
    ground_by_place: dict[str, str] = {}
    for pid, p_raw in working["places"].items():
        shelter = p_raw["shelter"]
        if shelter == "open_air":
            ground = "wet" if weather == "rain" else ("damp" if weather == "drizzle" else "dry")
        else:
            ground = "dry"
        ground_by_place[pid] = ground
        is_open_now = bookstore_open if pid == "bookstore" else True
        places_out[pid] = {
            "place_id": pid,
            "name": p_raw["name"],
            "ring": p_raw["ring"],
            "shelter": shelter,
            "supports": list(p_raw.get("supports") or []),
            "open_window": p_raw.get("open_window"),
            "is_open_now": is_open_now,
            "ground_state": ground,
            "knowledge_status": p_raw["knowledge_status"],
            "clue_note": p_raw.get("clue_note"),
            "last_visited_at": p_raw.get("last_visited_at"),
            "visit_count": int(p_raw.get("visit_count") or 0),
            "physical_states": [
                dict(s) for s in (p_raw.get("physical_states") or []) if isinstance(s, Mapping)
            ],
        }

    environment_out: dict[str, Any] = {
        "weather": weather,
        "airflow": airflow,
        "season": season,
        "yard_bench_damp": weather in ("drizzle", "rain"),
        "porch_sheltered_dry": True,
        "ground_by_place": ground_by_place,
        "updated_at": env_raw.get("updated_at"),
    }

    # 2. Journey & finite map knowledge
    j_raw = working["journey"]
    legs: list[dict[str, Any]] = list(j_raw.get("legs") or [])
    idx = int(j_raw.get("active_leg_index") or 0)
    total_trip_sec = sum(int(l["duration_seconds"]) for l in legs)
    done_legs_sec = sum(int(l["duration_seconds"]) for l in legs[:idx])
    in_leg_sec = 0.0
    if j_raw.get("state") == "in_transit" and idx < len(legs) and j_raw.get("segment_started_at"):
        seg_dt = parse_iso_moment(j_raw["segment_started_at"], field_name="segment_started_at")
        in_leg_sec = max(0.0, min(float(legs[idx]["duration_seconds"]), (now_dt - seg_dt).total_seconds()))
    elif j_raw.get("state") == "paused_at_node":
        in_leg_sec = float(j_raw.get("paused_walked_seconds") or 0.0)

    if j_raw.get("state") == "arrived":
        progress_ratio = 1.0
    elif total_trip_sec > 0 and j_raw.get("state") in ("in_transit", "paused_at_node"):
        progress_ratio = round(min(1.0, (done_legs_sec + in_leg_sec) / float(total_trip_sec)), 4)
    else:
        progress_ratio = 0.0

    discovered_rbs = list(j_raw.get("discovered_roadblocks") or [])
    routes_view: dict[str, dict[str, Any]] = {}
    for rid, rspec in ROUTES_CATALOG.items():
        routes_view[rid] = {
            "route_id": rid,
            "endpoints": list(rspec["endpoints"]),
            "duration_minutes": rspec["duration_minutes"],
            "duration_seconds": rspec["duration_seconds"],
            "sheltered": rspec["sheltered"],
            "label": rspec["label"],
            "known_blocked_to_subject": rid in discovered_rbs,
        }

    carried_ids = list(j_raw.get("carried_item_ids") or [])
    carried_loc = _effective_carried_location(j_raw)

    journey_out: dict[str, Any] = {
        "state": j_raw["state"],
        "current_place_id": j_raw.get("current_place_id"),
        "origin_place_id": j_raw.get("origin_place_id"),
        "target_place_id": j_raw.get("target_place_id"),
        "active_route_id": j_raw.get("active_route_id"),
        "planned_route_ids": [l["route_id"] for l in legs],
        "completed_route_ids": list(j_raw.get("completed_route_ids") or []),
        "is_turning_back": bool(j_raw.get("is_turning_back")),
        "turned_back_at": j_raw.get("turned_back_at"),
        "started_at": j_raw.get("started_at"),
        "segment_started_at": j_raw.get("segment_started_at"),
        "expected_arrival_at": j_raw.get("expected_arrival_at"),
        "arrived_at": j_raw.get("arrived_at"),
        "progress_ratio": progress_ratio,
        "elapsed_current_trip_seconds": round(done_legs_sec + in_leg_sec, 3),
        "accumulated_walk_seconds": round(
            float(j_raw.get("accumulated_walk_seconds") or 0.0)
            + (in_leg_sec if j_raw.get("state") in ("in_transit", "paused_at_node") else 0.0),
            3,
        ),
        "halt_reason": j_raw.get("halt_reason"),
        "blocked_route_id": j_raw.get("blocked_route_id"),
        "carried_item_ids": carried_ids,
        "carried_effective_location": carried_loc,
        "discovered_roadblocks": discovered_rbs,
        "routes_catalog": routes_view,
        "visited_place_ids": [
            pid for pid, p in places_out.items() if p["knowledge_status"] == "visited"
        ],
        "known_map_place_ids": [
            pid for pid, p in places_out.items() if p["knowledge_status"] in ("visited", "told_clue")
        ],
    }

    # 3. Drying items & Potted plant (Courtyard & Domestic)
    drying_out: dict[str, dict[str, Any]] = {}
    for iid, d_raw in working["drying_items"].items():
        sup_id = d_raw["support_id"]
        sup_meta = CLOTH_SUPPORTS.get(sup_id, CLOTH_SUPPORTS["home_rack"])
        eff_place = carried_loc if sup_id == "hand_bag" else sup_meta["place_id"]
        m_val = round(float(d_raw.get("moisture", 75.0)), 4)
        rate_now = compute_cloth_hourly_rate(sup_id, weather, airflow)
        m_band = moisture_to_band(m_val)
        drying_out[iid] = {
            "item_id": iid,
            "cloth_id": iid,
            "name": d_raw["name"],
            "label": d_raw["name"],
            "support_id": sup_id,
            "spot_id": sup_id,
            "support_label": sup_meta["label"],
            "shelter": sup_meta["shelter"],
            "effective_place_id": eff_place,
            "moisture": m_val,
            "moisture_band": m_band,
            "state": m_band,
            "status": m_band,
            "hourly_moisture_rate": rate_now,
            "sleeping_dry": bool(d_raw.get("sleeping_dry")),
            "dried_at": d_raw.get("dried_at"),
            "last_anchor_at": d_raw.get("last_anchor_at"),
            "segment_count": int(d_raw.get("segment_count") or 0),
        }

    plant_raw = working["potted_plant"]
    plant_out: dict[str, Any] = {
        "plant_id": plant_raw["plant_id"],
        "name": plant_raw["name"],
        "place_id": plant_raw["place_id"],
        "hydration": round(float(plant_raw.get("hydration", 70.0)), 4),
        "growth_points": round(float(plant_raw.get("growth_points", 0.0)), 4),
        "stage": plant_raw["stage"],
        "public_care_enabled": bool(plant_raw.get("public_care_enabled")),
        "never_dies_offline": True,
        "last_watered_at": plant_raw.get("last_watered_at"),
        "last_anchor_at": plant_raw.get("last_anchor_at"),
    }

    courtyard_out: dict[str, Any] = {
        "plants": [copy.deepcopy(plant_out)],
        "drying_rack": [copy.deepcopy(v) for v in drying_out.values()],
        "potted_plant": copy.deepcopy(plant_out),
        "drying_items": copy.deepcopy(drying_out),
        "yard_bench_damp": environment_out["yard_bench_damp"],
        "porch_sheltered_dry": True,
    }

    # 4. Craft bench & perspective-isolated cards
    cb_raw = working["craft_bench"]
    paper_u = int(cb_raw["paper_units"])
    scrap_u = int(cb_raw["scrap_units"])
    stocked_u = int(cb_raw["total_stocked_units"])
    cards_u = int(cb_raw["consumed_in_cards_units"])
    box_u = int(cb_raw["consumed_in_box_units"])
    conservation_ok = (paper_u + scrap_u + cards_u + box_u) == stocked_u and min(
        paper_u, scrap_u, cards_u, box_u
    ) >= 0

    cards_list: list[dict[str, Any]] = []
    cards_by_id: dict[str, dict[str, Any]] = {}
    for cid, c_raw in cb_raw["cards"].items():
        shared_flag = bool(c_raw.get("shared_or_displayed"))
        eff_loc = carried_loc if cid in carried_ids else str(c_raw.get("place_id") or "home")
        card_proj: dict[str, Any] = {
            "card_id": cid,
            "title": c_raw["title"],
            "material_units": int(c_raw["material_units"]),
            "inscribed_revision": int(c_raw.get("inscribed_revision") or 0),
            "shared_or_displayed": shared_flag,
            "displayed_at_bookstore": bool(c_raw.get("displayed_at_bookstore")),
            "displayed_revision": c_raw.get("displayed_revision"),
            "effective_place_id": eff_loc,
            "source_artifact_id": c_raw.get("source_artifact_id"),
            "work_ref": c_raw.get("work_ref"),
            "created_at": c_raw.get("created_at"),
            "inscribed_at": c_raw.get("inscribed_at"),
        }
        if has_subject_capability:
            card_proj["inscribed_text"] = c_raw.get("inscribed_text")
            card_proj["draft_text"] = c_raw.get("draft_text")
            card_proj["withheld"] = False
            card_proj["draft_withheld"] = False
        else:
            # Partner default: private cards hide inscribed_text; drafts are ALWAYS isolated
            if shared_flag:
                # If displayed at bookstore at a specific revision, show that revision's text or current shared text
                shown_text = (
                    c_raw.get("displayed_text")
                    if c_raw.get("displayed_at_bookstore") and c_raw.get("displayed_text") is not None
                    else c_raw.get("inscribed_text")
                )
                card_proj["inscribed_text"] = shown_text
                card_proj["withheld"] = False
            else:
                card_proj["inscribed_text"] = None
                card_proj["withheld"] = True
            card_proj["draft_text"] = None
            card_proj["draft_withheld"] = c_raw.get("draft_text") is not None

        cards_list.append(card_proj)
        cards_by_id[cid] = card_proj

    box_out = copy.deepcopy(cb_raw["card_organizer_box"])
    craft_bench_out: dict[str, Any] = {
        "paper_units": paper_u,
        "scrap_units": scrap_u,
        "total_stocked_units": stocked_u,
        "consumed_in_cards_units": cards_u,
        "consumed_in_box_units": box_u,
        "integer_conservation_holds": conservation_ok,
        "cards": cards_list,
        "cards_by_id": cards_by_id,
        "card_organizer_box": box_out,
    }

    # 5. Public life: Bookstore, Shared Shelf, Public Notices, Roadblocks & Place Booklet
    pl_raw = working["public_life"]
    b_raw = pl_raw["bookstore"]
    books_list: list[dict[str, Any]] = []
    books_by_id: dict[str, dict[str, Any]] = {}
    for bid, bk in b_raw["books"].items():
        custody_loc = (
            carried_loc
            if (bk.get("holder") == "subject" and bid in carried_ids)
            else bk.get("custody_place_id")
        )
        bk_proj: dict[str, Any] = {
            "book_id": bid,
            "title": bk["title"],
            "author_name": bk["author_name"],
            "cover_summary": bk["cover_summary"],
            "toc": list(bk["toc"]),
            "full_text_dumped": False,
            "item_owner": bk["item_owner"],
            "holder": bk["holder"],
            "custody_place_id": custody_loc,
            "status": bk["status"],
            "reserved_by": bk.get("reserved_by"),
            "reserved_at": bk.get("reserved_at"),
            "reservation_expires_at": bk.get("reservation_expires_at"),
            "last_expired_reservation_at": bk.get("last_expired_reservation_at"),
            "borrowed_at": bk.get("borrowed_at"),
            "due_at": bk.get("due_at"),
            "returned_at": bk.get("returned_at"),
            "return_receipt_kind": bk.get("return_receipt_kind"),
            "shopkeeper_received": bool(bk.get("shopkeeper_received")),
            "ownership_transferred": False,
            "paid": False,
        }
        books_list.append(bk_proj)
        books_by_id[bid] = bk_proj

    bookstore_out: dict[str, Any] = {
        "place_id": "bookstore",
        "shop_name": "街角旧书店",
        "shopkeeper_name": "温井慎一",
        "open_window": "10:00-19:00",
        "is_open_now": bookstore_open,
        "return_box_available": True,
        "books": books_list,
        "books_by_id": books_by_id,
        "display_cards": copy.deepcopy(b_raw.get("display_cards") or []),
    }

    shelf_list: list[dict[str, Any]] = []
    shelf_by_id: dict[str, dict[str, Any]] = {}
    for sid, s_item in pl_raw["shared_shelf"]["items"].items():
        eff_loc = (
            carried_loc
            if (s_item.get("holder") == "subject" and sid in carried_ids)
            else s_item.get("place_id")
        )
        s_proj = copy.deepcopy(s_item)
        s_proj["tool_id"] = sid
        s_proj["label"] = s_item.get("name")
        s_proj["effective_place_id"] = eff_loc
        s_proj["affection_score_coupled"] = False
        shelf_list.append(s_proj)
        shelf_by_id[sid] = s_proj

    shared_shelf_out: dict[str, Any] = {
        "place_id": "street_corner",
        "items": shelf_list,
        "tools": copy.deepcopy(shelf_list),
        "items_by_id": shelf_by_id,
        "tools_by_id": copy.deepcopy(shelf_by_id),
        "affection_score_coupled": False,
    }

    notices_out: list[dict[str, Any]] = []
    for n_raw in pl_raw["public_notices"]:
        is_read = bool(n_raw.get("read_by_subject"))
        is_exp = bool(n_raw.get("expired"))
        show_body = bool(is_read or not has_subject_capability)
        notices_out.append({
            "notice_id": n_raw["notice_id"],
            "title": n_raw["title"],
            "passing_summary": "有一张已过期的旧纸" if is_exp else "有一张新纸",
            "body": n_raw["body"] if show_body else None,
            "body_withheld_until_read": not is_read,
            "author_name": n_raw["author_name"],
            "posted_at": n_raw["posted_at"],
            "expires_at": n_raw["expires_at"],
            "expired": is_exp,
            "read_by_subject": is_read,
            "read_at": n_raw.get("read_at"),
            "related_route_id": n_raw.get("related_route_id") if show_body else None,
            "roadblock_edge": n_raw.get("related_route_id") if show_body else None,
            "roadblock_claim": n_raw.get("roadblock_claim") if show_body else None,
            "text_origin": "public_notice_board",
        })

    # Roadblocks view: Subject view NEVER leaks undiscovered roadblocks!
    roadblocks_out: list[dict[str, Any]] = []
    if has_subject_capability:
        for rid in discovered_rbs:
            rb_truth = working["world_truth"]["roadblocks"].get(rid)
            roadblocks_out.append({
                "route_id": rid,
                "known_blocked": True,
                "reason": rb_truth.get("reason") if isinstance(rb_truth, Mapping) else "已知封路线索",
            })
    else:
        for rid, rb_info in working["world_truth"]["roadblocks"].items():
            if isinstance(rb_info, Mapping) and rb_info.get("blocked"):
                roadblocks_out.append({
                    "route_id": rid,
                    "blocked": True,
                    "reason": rb_info.get("reason"),
                    "updated_at": rb_info.get("updated_at"),
                    "discovered_by_subject": rid in discovered_rbs,
                })

    # Place booklet with strict privacy default
    bk_raw = pl_raw["place_booklet"]
    bk_shared = bool(bk_raw.get("shared_with_partner"))
    if has_subject_capability or bk_shared:
        entries_list = [copy.deepcopy(v) for v in (bk_raw.get("entries") or {}).values()]
        booklet_out: dict[str, Any] = {
            "booklet_id": bk_raw["booklet_id"],
            "title": bk_raw["title"],
            "purpose": bk_raw["purpose"],
            "status": bk_raw["status"],
            "shared_with_partner": bk_shared,
            "withheld": False,
            "revision": int(bk_raw.get("revision") or 0),
            "paused_at": bk_raw.get("paused_at"),
            "pause_note": bk_raw.get("pause_note"),
            "entries": entries_list,
            "entries_by_place": copy.deepcopy(bk_raw.get("entries") or {}),
            "source_artifact_id": bk_raw.get("source_artifact_id"),
            "work_ref": bk_raw.get("work_ref"),
            "zero_failure_penalty": True,
            "zero_nag_reminders": True,
            "updated_at": bk_raw.get("updated_at"),
        }
    else:
        booklet_out = {
            "booklet_id": bk_raw["booklet_id"],
            "title": None,
            "purpose": None,
            "status": "private_withheld",
            "shared_with_partner": False,
            "withheld": True,
            "revision": 0,
            "paused_at": None,
            "pause_note": None,
            "entries": [],
            "entries_by_place": {},
            "source_artifact_id": None,
            "work_ref": None,
            "zero_failure_penalty": True,
            "zero_nag_reminders": True,
            "updated_at": None,
        }

    public_life_out: dict[str, Any] = {
        "bookstore": bookstore_out,
        "shared_shelf": shared_shelf_out,
        "public_notices": notices_out,
        "roadblocks": roadblocks_out,
        "place_booklet": booklet_out,
        "place_booklets": [copy.deepcopy(booklet_out)],
    }

    rev_int = int(working.get("revision") or 0)
    data_block: dict[str, Any] = {
        "revision": rev_int,
        "identity": identity,
        "environment": environment_out,
        "weather_and_ground": copy.deepcopy(environment_out),
        "places": places_out,
        "journey": journey_out,
        "drying_items": drying_out,
        "potted_plant": plant_out,
        "courtyard": courtyard_out,
        "craft_bench": craft_bench_out,
        "crafts": copy.deepcopy(craft_bench_out),
        "public_notices": copy.deepcopy(notices_out),
        "roadblocks": copy.deepcopy(roadblocks_out),
        "shared_shelf": copy.deepcopy(shared_shelf_out),
        "bookstore": copy.deepcopy(bookstore_out),
        "place_booklets": [copy.deepcopy(booklet_out)],
        "public_life": public_life_out,
    }

    return {
        "perspective": perspective,
        "has_subject_capability": bool(has_subject_capability),
        "revision": rev_int,
        "observed_at": now_str,
        "identity": copy.deepcopy(identity),
        "character": copy.deepcopy(identity),
        "llm_calls": 0,
        "data": data_block,
    }


# ---------------------------------------------------------------------------
# 12 Domain Command Handlers (Batches A–F)
# ---------------------------------------------------------------------------


def _handle_set_local_weather(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    weather = _clean_text(payload.get("weather"), field_name="weather", max_len=24)
    if weather not in WEATHER_KINDS:
        raise V04NeighborhoodError(
            "INVALID_FIELDS", f"weather must be one of {WEATHER_KINDS}", http_status=400
        )
    env = state["environment"]
    old_weather = str(env.get("weather") or "clear")
    old_airflow = str(env.get("airflow") or "medium")

    new_airflow = old_airflow
    if payload.get("airflow") is not None:
        new_airflow = _clean_text(payload.get("airflow"), field_name="airflow", max_len=24)
        if new_airflow not in AIRFLOW_KINDS:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"airflow must be one of {AIRFLOW_KINDS}", http_status=400
            )

    new_season = str(env.get("season") or "autumn")
    if payload.get("season") is not None:
        new_season = _clean_text(payload.get("season"), field_name="season", max_len=24)
        if new_season not in SEASON_KINDS:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"season must be one of {SEASON_KINDS}", http_status=400
            )

    # Cut segment for all drying items & reanchor plant under old weather first
    for item in state["drying_items"].values():
        _cut_cloth_segment_for_regime_change(
            item,
            old_weather=old_weather,
            old_airflow=old_airflow,
            new_weather=weather,
            new_airflow=new_airflow,
            new_support_id=item["support_id"],
            now_dt=now_dt,
        )
    _reanchor_plant_segment(state["potted_plant"], old_weather=old_weather, now_dt=now_dt)

    now_str = format_iso(now_dt)
    env["weather"] = weather
    env["airflow"] = new_airflow
    env["season"] = new_season
    env["updated_at"] = now_str
    return {
        "weather": weather,
        "airflow": new_airflow,
        "season": new_season,
        "updated_at": now_str,
    }


def _handle_hang_or_collect_cloth(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    item_id = (
        _require_id(payload["item_id"], field_name="item_id")
        if payload.get("item_id") is not None
        else "wet_cloth_main"
    )
    items = state["drying_items"]
    now_str = format_iso(now_dt)
    env = state["environment"]
    weather = str(env.get("weather") or "clear")
    airflow = str(env.get("airflow") or "medium")

    raw_op = payload.get("operation")
    if raw_op is not None:
        op = _clean_text(raw_op, field_name="operation", max_len=32)
    elif payload.get("target_support") is not None:
        op = "hang"
    elif payload.get("moisture") is not None:
        op = "wet"
    else:
        op = "collect"

    if op not in ("hang", "move", "collect", "wet"):
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            "operation must be one of ('hang', 'move', 'collect', 'wet')",
            http_status=400,
        )

    if item_id not in items:
        label = _optional_text(payload.get("name"), field_name="name", max_len=80) or "棉麻布巾"
        items[item_id] = {
            "item_id": item_id,
            "name": label,
            "support_id": "home_rack",
            "moisture": 75.0,
            "segment_start_moisture": 75.0,
            "segment_started_at": now_str,
            "last_anchor_at": now_str,
            "segment_count": 0,
            "sleeping_dry": False,
            "dried_at": None,
        }

    item = items[item_id]
    if payload.get("name") is not None:
        item["name"] = _clean_text(payload["name"], field_name="name", max_len=80)

    if op == "collect":
        target_sup = (
            _clean_text(payload["target_support"], field_name="target_support", max_len=32)
            if payload.get("target_support") is not None
            else "home_rack"
        )
    elif op in ("hang", "move"):
        if payload.get("target_support") is None:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "target_support is required for hang/move", http_status=400
            )
        target_sup = _clean_text(payload["target_support"], field_name="target_support", max_len=32)
    else:
        target_sup = (
            _clean_text(payload["target_support"], field_name="target_support", max_len=32)
            if payload.get("target_support") is not None
            else item["support_id"]
        )

    if target_sup not in CLOTH_SUPPORTS:
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            f"target_support must be one of {tuple(CLOTH_SUPPORTS)}",
            http_status=400,
        )

    if payload.get("moisture") is not None:
        m_in = payload["moisture"]
        if isinstance(m_in, bool) or not isinstance(m_in, (int, float)):
            raise V04NeighborhoodError("INVALID_FIELDS", "moisture must be numeric", http_status=400)
        m_val = float(m_in)
        if m_val < 0.0 or m_val > 100.0:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "moisture must be within 0.0..100.0", http_status=400
            )
        if item.get("segment_started_at") is not None:
            _settle_single_cloth_in_place(item, weather=weather, airflow=airflow, now_dt=now_dt)
        item["moisture"] = round(m_val, 6)
        item["segment_start_moisture"] = round(m_val, 6)
        item["sleeping_dry"] = m_val <= DRY_SLEEP_THRESHOLD
        item["dried_at"] = now_str if item["sleeping_dry"] else None
        item["support_id"] = target_sup
        item["segment_started_at"] = now_str
        item["last_anchor_at"] = now_str
        item["segment_count"] = int(item.get("segment_count", 0)) + 1
    elif op == "wet":
        if item.get("segment_started_at") is not None:
            _settle_single_cloth_in_place(item, weather=weather, airflow=airflow, now_dt=now_dt)
        item["moisture"] = 80.0
        item["segment_start_moisture"] = 80.0
        item["sleeping_dry"] = False
        item["dried_at"] = None
        item["support_id"] = target_sup
        item["segment_started_at"] = now_str
        item["last_anchor_at"] = now_str
        item["segment_count"] = int(item.get("segment_count", 0)) + 1
    else:
        _cut_cloth_segment_for_regime_change(
            item,
            old_weather=weather,
            old_airflow=airflow,
            new_weather=weather,
            new_airflow=airflow,
            new_support_id=target_sup,
            now_dt=now_dt,
        )

    # Sync carried_item_ids parent-chain with hand_bag
    carried = list(state["journey"].get("carried_item_ids") or [])
    if item["support_id"] == "hand_bag":
        if item_id not in carried:
            carried.append(item_id)
    else:
        carried = [x for x in carried if x != item_id]
    state["journey"]["carried_item_ids"] = carried

    return {
        "item_id": item_id,
        "support_id": item["support_id"],
        "moisture": round(float(item["moisture"]), 4),
        "moisture_band": moisture_to_band(float(item["moisture"])),
        "sleeping_dry": bool(item["sleeping_dry"]),
        "segment_count": int(item["segment_count"]),
    }


def _handle_water_or_move_plant(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    plant = state["potted_plant"]
    if payload.get("plant_id") is not None:
        pid = _require_id(payload["plant_id"], field_name="plant_id")
        if pid != plant["plant_id"]:
            raise V04NeighborhoodError("PLANT_NOT_FOUND", f"Unknown plant {pid!r}", http_status=404)

    weather = str(state["environment"].get("weather") or "clear")
    _reanchor_plant_segment(plant, old_weather=weather, now_dt=now_dt)
    now_str = format_iso(now_dt)

    raw_op = payload.get("operation")
    if raw_op is not None:
        op = _clean_text(raw_op, field_name="operation", max_len=32)
    elif payload.get("target_place") is not None:
        op = "move"
    elif payload.get("public_care_enabled") is not None:
        op = "toggle_public_care"
    else:
        op = "water"

    if op not in ("water", "move", "toggle_public_care"):
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            "operation must be one of ('water', 'move', 'toggle_public_care')",
            http_status=400,
        )

    if op == "water":
        amt_raw = payload.get("water_amount", 45.0)
        if isinstance(amt_raw, bool) or not isinstance(amt_raw, (int, float)) or amt_raw <= 0 or amt_raw > 100:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "water_amount must be within (0, 100]", http_status=400
            )
        new_h = min(100.0, float(plant["hydration"]) + float(amt_raw))
        plant["hydration"] = round(new_h, 4)
        plant["segment_start_hydration"] = plant["hydration"]
        plant["last_watered_at"] = now_str
        plant["stage"] = _derive_plant_stage(plant["hydration"], float(plant["growth_points"]))
    elif op == "move":
        if payload.get("target_place") is None:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "target_place is required for move", http_status=400
            )
        target_place = _clean_text(payload["target_place"], field_name="target_place", max_len=32)
        if target_place not in PLANT_ALLOWED_PLACES:
            raise V04NeighborhoodError(
                "INVALID_PLANT_PLACE",
                f"target_place must be one of {sorted(PLANT_ALLOWED_PLACES)}",
                http_status=400,
            )
        plant["place_id"] = target_place
    elif op == "toggle_public_care":
        if payload.get("public_care_enabled") is None:
            plant["public_care_enabled"] = not bool(plant.get("public_care_enabled"))
        else:
            val = payload["public_care_enabled"]
            if not isinstance(val, bool):
                raise V04NeighborhoodError(
                    "INVALID_FIELDS", "public_care_enabled must be boolean", http_status=400
                )
            plant["public_care_enabled"] = val

    plant["never_dies_offline"] = True
    plant["segment_started_at"] = now_str
    plant["last_anchor_at"] = now_str
    return {
        "plant_id": plant["plant_id"],
        "place_id": plant["place_id"],
        "hydration": plant["hydration"],
        "stage": plant["stage"],
        "public_care_enabled": plant["public_care_enabled"],
        "never_dies_offline": True,
    }


def _handle_set_roadblock(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    route_id = _clean_text(payload.get("route_id"), field_name="route_id", max_len=48)
    if route_id not in ROUTES_CATALOG:
        raise V04NeighborhoodError(
            "INVALID_FIELDS", f"Unknown route_id {route_id!r}", http_status=400
        )
    blocked_raw = payload.get("blocked", True)
    if not isinstance(blocked_raw, bool):
        raise V04NeighborhoodError("INVALID_FIELDS", "blocked must be boolean", http_status=400)
    reason = _optional_text(payload.get("reason"), field_name="reason", max_len=160) or "临时围栏施工"
    now_str = format_iso(now_dt)

    # IMPORTANT (Ch. 39 §四): Only updates world_truth! Never auto-pushes into journey.discovered_roadblocks!
    rbs = state["world_truth"]["roadblocks"]
    if blocked_raw:
        rbs[route_id] = {
            "route_id": route_id,
            "blocked": True,
            "reason": reason,
            "updated_at": now_str,
        }
    else:
        rbs.pop(route_id, None)

    return {
        "route_id": route_id,
        "blocked": blocked_raw,
        "reason": reason if blocked_raw else None,
        "subject_omniscient_notified": False,
    }


def _validate_carriable_item(state: Mapping[str, Any], item_id: str) -> None:
    if item_id in state["drying_items"]:
        return
    if item_id in state["craft_bench"]["cards"]:
        return
    bk = state["public_life"]["bookstore"]["books"].get(item_id)
    if bk is not None and bk.get("holder") == "subject":
        return
    sh = state["public_life"]["shared_shelf"]["items"].get(item_id)
    if sh is not None and sh.get("holder") == "subject":
        return
    if item_id == state["public_life"]["place_booklet"]["booklet_id"]:
        return
    raise V04NeighborhoodError(
        "ITEM_NOT_CARRIABLE", f"Item {item_id!r} does not exist or is not in subject custody", http_status=409
    )


def _handle_start_journey(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    journey = state["journey"]
    if journey.get("state") == "in_transit":
        raise V04NeighborhoodError(
            "ALREADY_IN_TRANSIT",
            "Subject is currently in transit; advance or turn_back before starting a new journey",
            http_status=409,
        )
    if journey.get("state") == "paused_at_node" and journey.get("current_place_id") is None:
        raise V04NeighborhoodError(
            "PAUSED_MID_SEGMENT",
            "Subject is paused mid-segment at a roadblock; call advance_or_turnback_journey with turn_back first",
            http_status=409,
        )

    src = str(journey.get("current_place_id") or journey.get("origin_place_id") or "home")
    raw_dst = payload.get("target_place") or payload.get("destination")
    raw_route = payload.get("route_id")
    raw_branch = payload.get("preferred_branch")

    chosen_route: Optional[str] = None
    if raw_route is not None:
        chosen_route = _clean_text(raw_route, field_name="route_id", max_len=48)
        if chosen_route not in ROUTES_CATALOG:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"Unknown route_id {chosen_route!r}", http_status=400
            )
    if raw_branch is not None:
        branch_clean = _clean_text(raw_branch, field_name="preferred_branch", max_len=48)
        if branch_clean not in ("open_shortcut", "eaves_walkway"):
            raise V04NeighborhoodError(
                "INVALID_FIELDS",
                "preferred_branch must be 'open_shortcut' or 'eaves_walkway'",
                http_status=400,
            )
        chosen_route = chosen_route or branch_clean

    if raw_dst is not None:
        dst = _clean_text(raw_dst, field_name="target_place", max_len=48)
        if dst not in PLACES_BY_ID:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"Unknown target_place {dst!r}", http_status=400
            )
    elif chosen_route is not None:
        ep_a, ep_b = ROUTES_CATALOG[chosen_route]["endpoints"]
        if src == ep_a:
            dst = ep_b
        elif src == ep_b:
            dst = ep_a
        else:
            raise V04NeighborhoodError(
                "ROUTE_NOT_ADJACENT",
                f"Current place {src!r} is not an endpoint of route {chosen_route!r}",
                http_status=409,
            )
    else:
        raise V04NeighborhoodError(
            "INVALID_FIELDS", "Provide target_place (or destination) or adjacent route_id", http_status=400
        )

    if dst == src:
        raise V04NeighborhoodError(
            "ALREADY_AT_PLACE", f"Already at {dst!r}", http_status=409
        )

    # Choose branch between yard and street_corner
    discovered = list(journey.get("discovered_roadblocks") or [])
    if chosen_route in ("open_shortcut", "eaves_walkway"):
        branch = chosen_route
    elif "open_shortcut" in discovered and "eaves_walkway" not in discovered:
        branch = "eaves_walkway"
    else:
        branch = "open_shortcut"

    legs = _build_route_legs(src, dst, branch_route_id=branch)
    if chosen_route is not None and all(l["route_id"] != chosen_route for l in legs):
        raise V04NeighborhoodError(
            "ROUTE_MISMATCH",
            f"Route {chosen_route!r} is not part of path from {src!r} to {dst!r}",
            http_status=409,
        )

    # Carry items update
    if payload.get("carry_item_ids") is not None:
        raw_carry = payload["carry_item_ids"]
        if not isinstance(raw_carry, list):
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "carry_item_ids must be a list of item IDs", http_status=400
            )
        dedup_carry: list[str] = []
        for entry in raw_carry:
            cid = _require_id(entry, field_name="carry_item_ids[]")
            _validate_carriable_item(state, cid)
            if cid not in dedup_carry:
                dedup_carry.append(cid)
        # Keep borrowed items & sync cloth support if wet_cloth_main carried
        for cloth_id, cloth in state["drying_items"].items():
            if cloth_id in dedup_carry and cloth["support_id"] != "hand_bag":
                env = state["environment"]
                _cut_cloth_segment_for_regime_change(
                    cloth,
                    old_weather=str(env.get("weather") or "clear"),
                    old_airflow=str(env.get("airflow") or "medium"),
                    new_weather=str(env.get("weather") or "clear"),
                    new_airflow=str(env.get("airflow") or "medium"),
                    new_support_id="hand_bag",
                    now_dt=now_dt,
                )
        journey["carried_item_ids"] = dedup_carry

    now_str = format_iso(now_dt)
    first_leg = legs[0]
    first_route = first_leg["route_id"]

    # Standing at src (entrance of first_route), real roadblock is directly observable!
    if _is_route_really_blocked(state, first_route):
        _add_discovered_roadblock(journey, first_route)
        journey["state"] = "paused_at_node"
        journey["current_place_id"] = src
        journey["origin_place_id"] = src
        journey["target_place_id"] = dst
        journey["active_route_id"] = None
        journey["active_leg_index"] = 0
        journey["legs"] = legs
        journey["paused_walked_seconds"] = 0.0
        journey["halt_reason"] = "ROADBLOCK_ENCOUNTERED"
        journey["blocked_route_id"] = first_route
        return {
            "outcome": "halted_by_roadblock_at_entrance",
            "state": "paused_at_node",
            "at_place_id": src,
            "encountered_roadblock": first_route,
            "discovered_roadblocks": list(journey["discovered_roadblocks"]),
        }

    # Clear stale false rumor if real entrance is open
    if first_route in discovered:
        _remove_discovered_roadblock(journey, first_route)

    total_sec = sum(int(l["duration_seconds"]) for l in legs)
    exp_dt = now_dt + timedelta(seconds=total_sec)
    journey["state"] = "in_transit"
    journey["current_place_id"] = None
    journey["origin_place_id"] = src
    journey["target_place_id"] = dst
    journey["active_route_id"] = first_route
    journey["active_leg_index"] = 0
    journey["legs"] = legs
    journey["completed_route_ids"] = []
    journey["is_turning_back"] = False
    journey["turned_back_at"] = None
    journey["started_at"] = now_str
    journey["segment_started_at"] = now_str
    journey["expected_arrival_at"] = format_iso(exp_dt)
    journey["arrived_at"] = None
    journey["paused_walked_seconds"] = 0.0
    journey["halt_reason"] = None
    journey["blocked_route_id"] = None

    return {
        "outcome": "departed",
        "state": "in_transit",
        "origin_place_id": src,
        "target_place_id": dst,
        "active_route_id": first_route,
        "planned_route_ids": [l["route_id"] for l in legs],
        "expected_arrival_at": journey["expected_arrival_at"],
        "carried_item_ids": list(journey.get("carried_item_ids") or []),
    }


def _handle_advance_or_turnback_journey(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    journey = state["journey"]
    raw_op = payload.get("operation")
    op = _clean_text(raw_op, field_name="operation", max_len=32) if raw_op is not None else "advance"
    if op not in ("advance", "turn_back", "pause", "resume"):
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            "operation must be one of ('advance', 'turn_back', 'pause', 'resume')",
            http_status=400,
        )

    now_str = format_iso(now_dt)

    if op == "advance":
        # Note: pre-command settlement already ran _settle_journey_in_place(state, now_dt)
        return {
            "operation": "advance",
            "state": journey["state"],
            "current_place_id": journey.get("current_place_id"),
            "active_route_id": journey.get("active_route_id"),
            "halt_reason": journey.get("halt_reason"),
            "discovered_roadblocks": list(journey.get("discovered_roadblocks") or []),
        }

    if op == "pause":
        if journey.get("state") != "in_transit":
            raise V04NeighborhoodError("NOT_IN_TRANSIT", "Journey is not in transit", http_status=409)
        legs = list(journey.get("legs") or [])
        idx = int(journey.get("active_leg_index") or 0)
        seg_dt = parse_iso_moment(journey["segment_started_at"], field_name="segment_started_at")
        walked_on_leg = max(0.0, (now_dt - seg_dt).total_seconds())
        journey["state"] = "paused_at_node"
        journey["paused_walked_seconds"] = round(walked_on_leg, 3)
        journey["halt_reason"] = "USER_PAUSED"
        return {
            "operation": "pause",
            "state": "paused_at_node",
            "paused_walked_seconds": journey["paused_walked_seconds"],
        }

    if op == "resume":
        if journey.get("state") != "paused_at_node" or journey.get("halt_reason") == "ROADBLOCK_ENCOUNTERED":
            raise V04NeighborhoodError(
                "CANNOT_RESUME", "No user-paused journey available to resume", http_status=409
            )
        legs = list(journey.get("legs") or [])
        idx = int(journey.get("active_leg_index") or 0)
        already = float(journey.get("paused_walked_seconds") or 0.0)
        rem_on_leg = max(1, round(float(legs[idx]["duration_seconds"]) - already))
        legs[idx] = dict(legs[idx], duration_seconds=rem_on_leg)
        journey["accumulated_walk_seconds"] = round(
            float(journey.get("accumulated_walk_seconds") or 0.0) + already, 3
        )
        journey["legs"] = legs
        journey["state"] = "in_transit"
        journey["segment_started_at"] = now_str
        journey["paused_walked_seconds"] = 0.0
        journey["halt_reason"] = None
        rem_total = sum(int(l["duration_seconds"]) for l in legs[idx:])
        journey["expected_arrival_at"] = format_iso(now_dt + timedelta(seconds=rem_total))
        return {
            "operation": "resume",
            "state": "in_transit",
            "expected_arrival_at": journey["expected_arrival_at"],
        }

    # op == "turn_back"
    if journey.get("state") not in ("in_transit", "paused_at_node"):
        raise V04NeighborhoodError(
            "NOT_IN_TRANSIT",
            "turn_back requires journey to be in_transit or paused_at_node",
            http_status=409,
        )

    legs = list(journey.get("legs") or [])
    idx = int(journey.get("active_leg_index") or 0)
    return_target = (
        _clean_text(payload["return_to_place"], field_name="return_to_place", max_len=48)
        if payload.get("return_to_place") is not None
        else str(journey.get("origin_place_id") or "home")
    )
    if return_target not in PLACES_BY_ID:
        raise V04NeighborhoodError(
            "INVALID_FIELDS", f"Unknown return_to_place {return_target!r}", http_status=400
        )

    walked_on_current_leg = 0.0
    if journey.get("state") == "in_transit" and idx < len(legs) and journey.get("segment_started_at"):
        seg_dt = parse_iso_moment(journey["segment_started_at"], field_name="segment_started_at")
        walked_on_current_leg = max(
            0.0, min(float(legs[idx]["duration_seconds"]), (now_dt - seg_dt).total_seconds())
        )
    elif journey.get("state") == "paused_at_node":
        walked_on_current_leg = float(journey.get("paused_walked_seconds") or 0.0)

    # Preserve forward walked time in accumulated_walk_seconds (never erase or teleport!)
    journey["accumulated_walk_seconds"] = round(
        float(journey.get("accumulated_walk_seconds") or 0.0) + walked_on_current_leg, 3
    )

    return_legs: list[dict[str, Any]] = []
    if walked_on_current_leg > 0 and idx < len(legs):
        cur_leg = legs[idx]
        return_legs.append({
            "from_place": cur_leg["to_place"],
            "to_place": cur_leg["from_place"],
            "route_id": cur_leg["route_id"],
            "duration_seconds": max(1, round(walked_on_current_leg)),
        })
        anchor_node = cur_leg["from_place"]
    else:
        anchor_node = (
            str(journey.get("current_place_id"))
            if journey.get("current_place_id")
            else (legs[idx]["from_place"] if idx < len(legs) else return_target)
        )

    if anchor_node != return_target:
        # Reverse previously walked legs or build clean path from anchor_node back to return_target
        discovered = list(journey.get("discovered_roadblocks") or [])
        branch = "eaves_walkway" if ("open_shortcut" in discovered and "eaves_walkway" not in discovered) else "open_shortcut"
        return_legs.extend(_build_route_legs(anchor_node, return_target, branch_route_id=branch))

    if not return_legs:
        journey["state"] = "at_place"
        journey["current_place_id"] = return_target
        journey["target_place_id"] = None
        journey["active_route_id"] = None
        journey["legs"] = []
        journey["active_leg_index"] = 0
        journey["is_turning_back"] = True
        journey["turned_back_at"] = now_str
        journey["paused_walked_seconds"] = 0.0
        journey["halt_reason"] = None
        return {
            "operation": "turn_back",
            "state": "at_place",
            "current_place_id": return_target,
            "return_duration_seconds": 0,
            "accumulated_walk_seconds": journey["accumulated_walk_seconds"],
        }

    total_ret_sec = sum(int(l["duration_seconds"]) for l in return_legs)
    journey["state"] = "in_transit"
    journey["current_place_id"] = None
    journey["target_place_id"] = return_target
    journey["legs"] = return_legs
    journey["active_leg_index"] = 0
    journey["active_route_id"] = return_legs[0]["route_id"]
    journey["is_turning_back"] = True
    journey["turned_back_at"] = now_str
    journey["segment_started_at"] = now_str
    journey["expected_arrival_at"] = format_iso(now_dt + timedelta(seconds=total_ret_sec))
    journey["arrived_at"] = None
    journey["paused_walked_seconds"] = 0.0
    journey["halt_reason"] = None
    journey["blocked_route_id"] = None

    return {
        "operation": "turn_back",
        "state": "in_transit",
        "target_place_id": return_target,
        "active_route_id": journey["active_route_id"],
        "return_duration_seconds": total_ret_sec,
        "expected_arrival_at": journey["expected_arrival_at"],
        "accumulated_walk_seconds": journey["accumulated_walk_seconds"],
    }


def _handle_craft_paper_or_box(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    cb = state["craft_bench"]
    op = _clean_text(payload.get("operation"), field_name="operation", max_len=40)
    now_str = format_iso(now_dt)

    if op in BOX_STEPS:
        target_step_shortcut: Optional[str] = op
        op = "box_step"
    else:
        target_step_shortcut = None

    if op == "restock_paper":
        units = _positive_int(payload.get("units", 4), field_name="units", maximum=500)
        cb["paper_units"] = int(cb["paper_units"]) + units
        cb["total_stocked_units"] = int(cb["total_stocked_units"]) + units
        return {
            "operation": "restock_paper",
            "paper_units": cb["paper_units"],
            "total_stocked_units": cb["total_stocked_units"],
        }

    if op == "cut_card":
        units = _positive_int(payload.get("units", 1), field_name="units", maximum=64)
        scrap_loss = _nonneg_int(payload.get("scrap_loss", 0), field_name="scrap_loss", maximum=64)
        if scrap_loss >= units:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "scrap_loss must be strictly less than units consumed", http_status=400
            )
        if int(cb["paper_units"]) < units:
            raise V04NeighborhoodError(
                "INSUFFICIENT_MATERIAL",
                f"Need {units} paper_units, only {cb['paper_units']} available",
                http_status=409,
            )
        if len(cb["cards"]) >= MAX_CARDS:
            raise V04NeighborhoodError("CARD_LIMIT_REACHED", "Too many cards on bench", http_status=409)

        if payload.get("card_id") is not None:
            cid = _require_id(payload["card_id"], field_name="card_id")
            if cid in cb["cards"]:
                raise V04NeighborhoodError("CARD_ID_EXISTS", f"Card {cid!r} already exists", http_status=409)
        else:
            cb["card_seq"] = int(cb.get("card_seq") or 0) + 1
            cid = f"card_{cb['card_seq']:03d}"

        card_units = units - scrap_loss
        cb["paper_units"] = int(cb["paper_units"]) - units
        cb["scrap_units"] = int(cb["scrap_units"]) + scrap_loss
        cb["consumed_in_cards_units"] = int(cb["consumed_in_cards_units"]) + card_units

        title = _optional_text(payload.get("title"), field_name="title", max_len=80) or "手裁纸卡"
        draft_text = _optional_text(
            payload.get("draft_text"), field_name="draft_text", max_len=600, allow_newlines=True
        )
        src_art = _optional_text(
            payload.get("source_artifact_id"), field_name="source_artifact_id", max_len=160
        )
        work_ref = _optional_text(payload.get("work_ref"), field_name="work_ref", max_len=160)

        card_doc = {
            "card_id": cid,
            "title": title,
            "material_units": card_units,
            "inscribed_text": None,
            "inscribed_revision": 0,
            "draft_text": draft_text,
            "shared_or_displayed": False,
            "displayed_at_bookstore": False,
            "displayed_revision": None,
            "displayed_text": None,
            "place_id": "home",
            "source_artifact_id": src_art,
            "work_ref": work_ref,
            "created_at": now_str,
            "inscribed_at": None,
        }
        cb["cards"][cid] = card_doc
        return {
            "operation": "cut_card",
            "card_id": cid,
            "material_units": card_units,
            "paper_units": cb["paper_units"],
            "scrap_units": cb["scrap_units"],
        }

    if op == "edit_card_draft":
        cid = _require_id(payload.get("card_id"), field_name="card_id")
        card = cb["cards"].get(cid)
        if card is None:
            raise V04NeighborhoodError("CARD_NOT_FOUND", f"Unknown card {cid!r}", http_status=404)
        draft = _clean_text(
            payload.get("draft_text") if payload.get("draft_text") is not None else payload.get("text"),
            field_name="draft_text",
            max_len=600,
            allow_newlines=True,
        )
        card["draft_text"] = draft
        # Crucial (Ch.40 §五): editing draft NEVER mutates inscribed_text or inscribed_revision!
        return {
            "operation": "edit_card_draft",
            "card_id": cid,
            "inscribed_revision": card["inscribed_revision"],
            "inscribed_text_unchanged": True,
        }

    if op == "inscribe_card":
        cid = _require_id(payload.get("card_id"), field_name="card_id")
        card = cb["cards"].get(cid)
        if card is None:
            raise V04NeighborhoodError("CARD_NOT_FOUND", f"Unknown card {cid!r}", http_status=404)
        if int(card.get("inscribed_revision") or 0) >= 1:
            raise V04NeighborhoodError(
                "CARD_ALREADY_INSCRIBED",
                "Card already has inscribed_revision >= 1; use transcribe_card to re-label/transcribe",
                http_status=409,
            )
        text = _clean_text(
            payload.get("text"), field_name="text", max_len=600, allow_newlines=True
        )
        card["inscribed_text"] = text
        card["inscribed_revision"] = 1
        card["inscribed_at"] = now_str
        if payload.get("title") is not None:
            card["title"] = _clean_text(payload["title"], field_name="title", max_len=80)
        if payload.get("draft_text") is not None:
            card["draft_text"] = _clean_text(
                payload["draft_text"], field_name="draft_text", max_len=600, allow_newlines=True
            )
        if payload.get("shared_or_displayed") is not None:
            if not isinstance(payload["shared_or_displayed"], bool):
                raise V04NeighborhoodError(
                    "INVALID_FIELDS", "shared_or_displayed must be boolean", http_status=400
                )
            card["shared_or_displayed"] = payload["shared_or_displayed"]
        if payload.get("source_artifact_id") is not None:
            card["source_artifact_id"] = _clean_text(
                payload["source_artifact_id"], field_name="source_artifact_id", max_len=160
            )
        if payload.get("work_ref") is not None:
            card["work_ref"] = _clean_text(payload["work_ref"], field_name="work_ref", max_len=160)

        return {
            "operation": "inscribe_card",
            "card_id": cid,
            "inscribed_revision": 1,
            "shared_or_displayed": card["shared_or_displayed"],
        }

    if op == "transcribe_card":
        cid = _require_id(payload.get("card_id"), field_name="card_id")
        card = cb["cards"].get(cid)
        if card is None:
            raise V04NeighborhoodError("CARD_NOT_FOUND", f"Unknown card {cid!r}", http_status=404)
        raw_txt = payload.get("text") if payload.get("text") is not None else card.get("draft_text")
        text = _clean_text(raw_txt, field_name="text", max_len=600, allow_newlines=True)
        card["inscribed_text"] = text
        card["inscribed_revision"] = int(card.get("inscribed_revision") or 0) + 1
        card["inscribed_at"] = now_str
        if payload.get("shared_or_displayed") is not None:
            if not isinstance(payload["shared_or_displayed"], bool):
                raise V04NeighborhoodError(
                    "INVALID_FIELDS", "shared_or_displayed must be boolean", http_status=400
                )
            card["shared_or_displayed"] = payload["shared_or_displayed"]
        return {
            "operation": "transcribe_card",
            "card_id": cid,
            "inscribed_revision": card["inscribed_revision"],
            "shared_or_displayed": card["shared_or_displayed"],
        }

    if op == "share_card":
        cid = _require_id(payload.get("card_id"), field_name="card_id")
        card = cb["cards"].get(cid)
        if card is None:
            raise V04NeighborhoodError("CARD_NOT_FOUND", f"Unknown card {cid!r}", http_status=404)
        if int(card.get("inscribed_revision") or 0) < 1:
            raise V04NeighborhoodError(
                "CARD_NOT_INSCRIBED", "Inscribe card before sharing", http_status=409
            )
        flag = payload.get("shared_or_displayed", True)
        if not isinstance(flag, bool):
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "shared_or_displayed must be boolean", http_status=400
            )
        card["shared_or_displayed"] = flag
        return {
            "operation": "share_card",
            "card_id": cid,
            "shared_or_displayed": flag,
        }

    if op in ("box_step", "advance_box", "set_aside_box"):
        box = cb["card_organizer_box"]
        if op == "set_aside_box":
            target_step = "set_aside"
        else:
            raw_step = target_step_shortcut or payload.get("target_step")
            target_step = _clean_text(raw_step, field_name="target_step", max_len=32)
        if target_step not in BOX_STEPS:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"target_step must be one of {BOX_STEPS}", http_status=400
            )

        if target_step == "divider_cut":
            need_cut = int(box.get("dividers_cut") or 0) == 0 or payload.get("units") is not None
            if need_cut:
                units = _positive_int(payload.get("units", 1), field_name="units", maximum=32)
                scrap_loss = _nonneg_int(payload.get("scrap_loss", 0), field_name="scrap_loss", maximum=32)
                if scrap_loss >= units:
                    raise V04NeighborhoodError(
                        "INVALID_FIELDS", "scrap_loss must be less than units", http_status=400
                    )
                if int(cb["paper_units"]) < units:
                    raise V04NeighborhoodError(
                        "INSUFFICIENT_MATERIAL",
                        f"Need {units} paper_units for divider, only {cb['paper_units']} available",
                        http_status=409,
                    )
                div_units = units - scrap_loss
                cb["paper_units"] = int(cb["paper_units"]) - units
                cb["scrap_units"] = int(cb["scrap_units"]) + scrap_loss
                cb["consumed_in_box_units"] = int(cb["consumed_in_box_units"]) + div_units
                box["dividers_cut"] = int(box.get("dividers_cut") or 0) + 1
                dividers = list(box.get("dividers") or [])
                dividers.append({
                    "divider_id": f"divider_{box['dividers_cut']:02d}",
                    "material_units": div_units,
                    "cut_at": now_str,
                })
                box["dividers"] = dividers
        elif target_step in ("trial_fitted", "finished") and int(box.get("dividers_cut") or 0) == 0:
            raise V04NeighborhoodError(
                "DIVIDER_REQUIRED",
                "Cut at least one divider ('divider_cut') before trial_fitted or finished",
                http_status=409,
            )

        if target_step == "set_aside":
            if box.get("step") != "set_aside":
                box["last_active_step"] = box.get("step")
            box["step"] = "set_aside"
        else:
            box["step"] = target_step
            box["last_active_step"] = target_step

        if payload.get("fit_note") is not None:
            box["fit_note"] = _clean_text(payload["fit_note"], field_name="fit_note", max_len=240)
        box["updated_at"] = now_str
        return {
            "operation": "box_step",
            "step": box["step"],
            "dividers_cut": box["dividers_cut"],
            "fit_note": box["fit_note"],
            "paper_units": cb["paper_units"],
        }

    if op == "repair_patch":
        box = cb["card_organizer_box"]
        if payload.get("units") is not None:
            units = _nonneg_int(payload["units"], field_name="units", maximum=16)
            if units > 0:
                if int(cb["paper_units"]) < units:
                    raise V04NeighborhoodError(
                        "INSUFFICIENT_MATERIAL",
                        f"Need {units} paper_units for patch, only {cb['paper_units']} available",
                        http_status=409,
                    )
                cb["paper_units"] = int(cb["paper_units"]) - units
                cb["consumed_in_box_units"] = int(cb["consumed_in_box_units"]) + units
        patch_note = (
            _optional_text(payload.get("patch_note"), field_name="patch_note", max_len=240)
            or "局部修补松脱标签与纸隔板折角"
        )
        box["visible_patch"] = True
        box["patch_count"] = int(box.get("patch_count") or 0) + 1
        patches = list(box.get("patches") or [])
        patches.append({
            "patch_id": f"patch_{box['patch_count']:02d}",
            "note": patch_note,
            "visible_patch": True,
            "patched_at": now_str,
        })
        box["patches"] = patches
        box["updated_at"] = now_str
        return {
            "operation": "repair_patch",
            "visible_patch": True,
            "patch_count": box["patch_count"],
            "patch_note": patch_note,
        }

    raise V04NeighborhoodError(
        "INVALID_FIELDS", f"Unsupported craft operation {op!r}", http_status=400
    )


def _handle_bookstore_service(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    raw_svc = payload.get("service") if payload.get("service") is not None else payload.get("operation")
    svc = _clean_text(raw_svc, field_name="service", max_len=40)
    b_store = state["public_life"]["bookstore"]
    books = b_store["books"]
    open_now = _is_bookstore_open(now_dt)
    now_str = format_iso(now_dt)

    if svc == "browse_book":
        if not open_now:
            raise V04NeighborhoodError(
                "BOOKSTORE_CLOSED", "Bookstore is only open 10:00-19:00", http_status=409
            )
        bid = _require_id(payload.get("book_id"), field_name="book_id")
        book = books.get(bid)
        if book is None:
            raise V04NeighborhoodError("BOOK_NOT_FOUND", f"Unknown book {bid!r}", http_status=404)
        return {
            "service": "browse_book",
            "book_id": bid,
            "title": book["title"],
            "author_name": book["author_name"],
            "cover_summary": book["cover_summary"],
            "toc": list(book["toc"]),
            "sample_excerpt": book["sample_excerpt"],
            "full_text_dumped": False,
        }

    if svc == "reserve_book":
        if not open_now:
            raise V04NeighborhoodError(
                "BOOKSTORE_CLOSED", "Bookstore is only open 10:00-19:00", http_status=409
            )
        bid = _require_id(payload.get("book_id"), field_name="book_id")
        book = books.get(bid)
        if book is None:
            raise V04NeighborhoodError("BOOK_NOT_FOUND", f"Unknown book {bid!r}", http_status=404)
        if book["status"] != "available":
            raise V04NeighborhoodError(
                "BOOK_NOT_AVAILABLE",
                f"Book {bid!r} is currently {book['status']!r}",
                http_status=409,
            )
        if payload.get("expires_at") is not None:
            exp_dt = parse_iso_moment(payload["expires_at"], field_name="expires_at")
            if exp_dt <= now_dt:
                raise V04NeighborhoodError(
                    "INVALID_FIELDS", "expires_at must be in the future", http_status=400
                )
        else:
            hold_h_raw = payload.get("hold_hours", 24)
            if isinstance(hold_h_raw, bool) or not isinstance(hold_h_raw, (int, float)) or hold_h_raw <= 0 or hold_h_raw > 168:
                raise V04NeighborhoodError(
                    "INVALID_FIELDS", "hold_hours must be within (0, 168]", http_status=400
                )
            exp_dt = now_dt + timedelta(seconds=round(float(hold_h_raw) * 3600.0))

        book["status"] = "reserved"
        book["reserved_by"] = "subject"
        book["reserved_at"] = now_str
        book["reservation_expires_at"] = format_iso(exp_dt)
        book["ownership_transferred"] = False
        book["paid"] = False
        return {
            "service": "reserve_book",
            "book_id": bid,
            "status": "reserved",
            "expires_at": book["reservation_expires_at"],
            "ownership_transferred": False,
            "paid": False,
        }

    if svc == "borrow_book":
        if not open_now:
            raise V04NeighborhoodError(
                "BOOKSTORE_CLOSED", "Bookstore is only open 10:00-19:00", http_status=409
            )
        bid = _require_id(payload.get("book_id"), field_name="book_id")
        book = books.get(bid)
        if book is None:
            raise V04NeighborhoodError("BOOK_NOT_FOUND", f"Unknown book {bid!r}", http_status=404)
        if book["status"] not in ("available", "reserved"):
            raise V04NeighborhoodError(
                "BOOK_NOT_AVAILABLE",
                f"Book {bid!r} cannot be borrowed in state {book['status']!r}",
                http_status=409,
            )
        loan_days = _positive_int(payload.get("loan_days", 7), field_name="loan_days", maximum=30)
        book["status"] = "borrowed"
        book["holder"] = "subject"
        book["custody_place_id"] = str(state["journey"].get("current_place_id") or "bookstore")
        book["reserved_by"] = None
        book["reservation_expires_at"] = None
        book["borrowed_at"] = now_str
        book["due_at"] = format_iso(now_dt + timedelta(days=loan_days))
        book["return_receipt_kind"] = None
        book["shopkeeper_received"] = False
        book["ownership_transferred"] = False

        carried = list(state["journey"].get("carried_item_ids") or [])
        if bid not in carried:
            carried.append(bid)
        state["journey"]["carried_item_ids"] = carried

        return {
            "service": "borrow_book",
            "book_id": bid,
            "status": "borrowed",
            "item_owner": "bookstore",
            "holder": "subject",
            "due_at": book["due_at"],
            "ownership_transferred": False,
        }

    if svc == "return_book":
        bid = _require_id(payload.get("book_id"), field_name="book_id")
        book = books.get(bid)
        if book is None:
            raise V04NeighborhoodError("BOOK_NOT_FOUND", f"Unknown book {bid!r}", http_status=404)
        if book["status"] != "borrowed" or book.get("holder") != "subject":
            raise V04NeighborhoodError(
                "BOOK_NOT_BORROWED", f"Book {bid!r} is not currently borrowed by subject", http_status=409
            )
        via_box = payload.get("via_return_box", False)
        if not isinstance(via_box, bool):
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "via_return_box must be boolean", http_status=400
            )
        ret_loc = "bookstore"
        if payload.get("return_location") is not None:
            ret_loc = _clean_text(payload["return_location"], field_name="return_location", max_len=32)
            if ret_loc not in ("porch", "bookstore"):
                raise V04NeighborhoodError(
                    "INVALID_FIELDS", "return_location must be 'porch' or 'bookstore'", http_status=400
                )

        # Remove from carried items
        state["journey"]["carried_item_ids"] = [
            x for x in (state["journey"].get("carried_item_ids") or []) if x != bid
        ]
        book["returned_at"] = now_str
        book["due_at"] = None

        # Crucial (Ch.42 §三): closed store or return box NEVER fakes shopkeeper_received!
        if (not open_now) or via_box or ret_loc == "porch":
            book["status"] = "return_box_pending"
            book["holder"] = "bookstore_return_box"
            book["custody_place_id"] = ret_loc
            book["return_receipt_kind"] = "return_box_pending"
            book["shopkeeper_received"] = False
        else:
            book["status"] = "available"
            book["holder"] = "bookstore"
            book["custody_place_id"] = "bookstore"
            book["return_receipt_kind"] = "shopkeeper_received"
            book["shopkeeper_received"] = True

        return {
            "service": "return_book",
            "book_id": bid,
            "status": book["status"],
            "return_receipt_kind": book["return_receipt_kind"],
            "shopkeeper_received": book["shopkeeper_received"],
            "custody_place_id": book["custody_place_id"],
        }

    if svc == "submit_display_card":
        if not open_now:
            raise V04NeighborhoodError(
                "BOOKSTORE_CLOSED", "Bookstore is only open 10:00-19:00", http_status=409
            )
        cid = _require_id(payload.get("card_id"), field_name="card_id")
        card = state["craft_bench"]["cards"].get(cid)
        if card is None:
            raise V04NeighborhoodError("CARD_NOT_FOUND", f"Unknown card {cid!r}", http_status=404)
        rev = int(card.get("inscribed_revision") or 0)
        if rev < 1 or not card.get("inscribed_text"):
            raise V04NeighborhoodError(
                "CARD_NOT_INSCRIBED", "Only an inscribed card version can be submitted for display", http_status=409
            )
        exact_text = str(card["inscribed_text"])
        card["shared_or_displayed"] = True
        card["displayed_at_bookstore"] = True
        card["displayed_revision"] = rev
        card["displayed_text"] = exact_text
        card["place_id"] = "bookstore"

        state["journey"]["carried_item_ids"] = [
            x for x in (state["journey"].get("carried_item_ids") or []) if x != cid
        ]

        display_cards = [
            d for d in (b_store.get("display_cards") or []) if d.get("card_id") != cid
        ]
        display_entry = {
            "card_id": cid,
            "title": card["title"],
            "displayed_revision": rev,
            "displayed_text": exact_text,
            "private_draft_exposed": False,
            "submitted_at": now_str,
        }
        display_cards.append(display_entry)
        b_store["display_cards"] = display_cards
        return {
            "service": "submit_display_card",
            "card_id": cid,
            "displayed_revision": rev,
            "displayed_text": exact_text,
            "private_draft_exposed": False,
        }

    raise V04NeighborhoodError(
        "INVALID_FIELDS", f"Unsupported bookstore service {svc!r}", http_status=400
    )


def _handle_shared_shelf_loan(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_dt: datetime,
) -> dict[str, Any]:
    iid = _require_id(payload.get("item_id"), field_name="item_id")
    shelf = state["public_life"]["shared_shelf"]["items"]
    item = shelf.get(iid)
    if item is None:
        raise V04NeighborhoodError(
            "SHARED_ITEM_NOT_FOUND", f"Unknown shared shelf item {iid!r}", http_status=404
        )
    op = _clean_text(payload.get("operation"), field_name="operation", max_len=32)
    now_str = format_iso(now_dt)

    if op == "borrow":
        if item["status"] != "on_shelf":
            raise V04NeighborhoodError(
                "ITEM_ALREADY_BORROWED", f"Item {iid!r} is already borrowed", http_status=409
            )
        loan_h = _positive_int(payload.get("loan_hours", 48), field_name="loan_hours", maximum=336)
        item["status"] = "borrowed"
        item["holder"] = actor_side
        item["borrowed_by"] = actor_side
        item["borrowed_at"] = now_str
        item["due_at"] = format_iso(now_dt + timedelta(hours=loan_h))
        item["returned_at"] = None
        if actor_side == "subject":
            carried = list(state["journey"].get("carried_item_ids") or [])
            if iid not in carried:
                carried.append(iid)
            state["journey"]["carried_item_ids"] = carried
        return {
            "operation": "borrow",
            "item_id": iid,
            "holder": actor_side,
            "due_at": item["due_at"],
            "affection_score_coupled": False,
        }

    if op == "return":
        if item["status"] != "borrowed":
            raise V04NeighborhoodError(
                "ITEM_NOT_BORROWED", f"Item {iid!r} is not currently borrowed", http_status=409
            )
        item["status"] = "on_shelf"
        item["holder"] = "shared_shelf"
        item["borrowed_by"] = None
        item["due_at"] = None
        item["returned_at"] = now_str
        item["place_id"] = "street_corner"
        state["journey"]["carried_item_ids"] = [
            x for x in (state["journey"].get("carried_item_ids") or []) if x != iid
        ]
        return {
            "operation": "return",
            "item_id": iid,
            "status": "on_shelf",
            "affection_score_coupled": False,
        }

    if op == "request_extension":
        if item["status"] != "borrowed":
            raise V04NeighborhoodError(
                "ITEM_NOT_BORROWED", f"Item {iid!r} is not currently borrowed", http_status=409
            )
        extra_h = _positive_int(payload.get("extra_hours", 24), field_name="extra_hours", maximum=168)
        reason = _optional_text(
            payload.get("reason") if payload.get("reason") is not None else payload.get("note"),
            field_name="reason",
            max_len=200,
        ) or "手头裁剪尚未收尾，顺延归还"
        base_due = parse_iso_moment(item["due_at"], field_name="due_at") if item.get("due_at") else now_dt
        new_due = format_iso(max(base_due, now_dt) + timedelta(hours=extra_h))
        item["due_at"] = new_due
        item["extension_count"] = int(item.get("extension_count") or 0) + 1
        hist = list(item.get("extension_history") or [])
        hist.append({
            "extended_at": now_str,
            "extra_hours": extra_h,
            "new_due_at": new_due,
            "reason": reason,
            "requested_by": actor_side,
            "affection_penalty": 0,
        })
        item["extension_history"] = hist
        return {
            "operation": "request_extension",
            "item_id": iid,
            "extension_count": item["extension_count"],
            "new_due_at": new_due,
            "affection_penalty": 0,
            "affection_score_coupled": False,
        }

    raise V04NeighborhoodError(
        "INVALID_FIELDS",
        "operation must be one of ('borrow', 'return', 'request_extension')",
        http_status=400,
    )


def _handle_post_public_notice(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_dt: datetime,
) -> dict[str, Any]:
    pl = state["public_life"]
    notices = pl["public_notices"]
    if len(notices) >= MAX_NOTICES:
        notices.pop(0)

    title = _sanitize_public_text(_clean_text(payload.get("title"), field_name="title", max_len=100))
    body = _sanitize_public_text(
        _clean_text(payload.get("body"), field_name="body", max_len=600, allow_newlines=True)
    )
    default_author = "街区维护栏" if actor_side == "partner" else "街坊便签"
    author_name = _sanitize_public_text(
        _optional_text(payload.get("author_name"), field_name="author_name", max_len=60) or default_author
    )
    now_str = format_iso(now_dt)

    if payload.get("notice_id") is not None:
        nid = _require_id(payload["notice_id"], field_name="notice_id")
        if any(n["notice_id"] == nid for n in notices):
            raise V04NeighborhoodError("NOTICE_ID_EXISTS", f"Notice {nid!r} already exists", http_status=409)
    else:
        pl["notice_seq"] = int(pl.get("notice_seq") or 0) + 1
        nid = f"notice_{pl['notice_seq']:03d}"

    if payload.get("expires_at") is not None:
        exp_dt = parse_iso_moment(payload["expires_at"], field_name="expires_at")
    else:
        vh_raw = payload.get("valid_hours", 48)
        if isinstance(vh_raw, bool) or not isinstance(vh_raw, (int, float)) or vh_raw <= 0 or vh_raw > 720:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "valid_hours must be within (0, 720]", http_status=400
            )
        exp_dt = now_dt + timedelta(seconds=round(float(vh_raw) * 3600.0))

    rel_route: Optional[str] = None
    rb_claim: Optional[str] = None
    if payload.get("related_route_id") is not None:
        rel_route = _clean_text(payload["related_route_id"], field_name="related_route_id", max_len=48)
        if rel_route not in ROUTES_CATALOG:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"Unknown related_route_id {rel_route!r}", http_status=400
            )
        raw_claim = payload.get("roadblock_claim", "blocked")
        rb_claim = _clean_text(raw_claim, field_name="roadblock_claim", max_len=24)
        if rb_claim not in ("blocked", "clear"):
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "roadblock_claim must be 'blocked' or 'clear'", http_status=400
            )
    elif payload.get("roadblock_claim") is not None:
        raise V04NeighborhoodError(
            "INVALID_FIELDS", "roadblock_claim requires related_route_id", http_status=400
        )

    is_expired = now_dt >= exp_dt
    notice_doc = {
        "notice_id": nid,
        "title": title,
        "body": body,
        "author_name": author_name,
        "posted_at": now_str,
        "expires_at": format_iso(exp_dt),
        "expired": is_expired,
        "read_by_subject": False,
        "read_at": None,
        "related_route_id": rel_route,
        "roadblock_claim": rb_claim,
    }
    notices.append(notice_doc)
    return {
        "notice_id": nid,
        "title": title,
        "passing_summary": "有一张新纸",
        "read_by_subject": False,
        "expired": is_expired,
    }


def _handle_read_public_notice(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    notices = state["public_life"]["public_notices"]
    now_str = format_iso(now_dt)
    journey = state["journey"]

    target_notices: list[dict[str, Any]] = []
    if payload.get("notice_id") is not None:
        nid = _require_id(payload["notice_id"], field_name="notice_id")
        found = next((n for n in notices if n["notice_id"] == nid), None)
        if found is None:
            raise V04NeighborhoodError("NOTICE_NOT_FOUND", f"Notice {nid!r} not found", http_status=404)
        target_notices.append(found)
    else:
        target_notices = list(notices)

    read_items: list[dict[str, Any]] = []
    synced_routes: list[str] = []
    for notice in target_notices:
        exp_dt = parse_iso_moment(notice["expires_at"], field_name="expires_at")
        if now_dt >= exp_dt:
            notice["expired"] = True
        notice["read_by_subject"] = True
        notice["read_at"] = notice.get("read_at") or now_str

        rel_route = notice.get("related_route_id")
        claim = notice.get("roadblock_claim")
        if rel_route and not notice["expired"]:
            if claim == "blocked":
                _add_discovered_roadblock(journey, rel_route)
                synced_routes.append(rel_route)
            elif claim == "clear":
                _remove_discovered_roadblock(journey, rel_route)
                synced_routes.append(rel_route)

        read_items.append({
            "notice_id": notice["notice_id"],
            "title": notice["title"],
            "body": notice["body"],
            "expired": notice["expired"],
            "related_route_id": rel_route,
            "roadblock_claim": claim,
        })

    return {
        "read_notices": read_items,
        "synced_roadblock_routes": synced_routes,
        "discovered_roadblocks": list(journey.get("discovered_roadblocks") or []),
    }


def _handle_record_poi_physical_state(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    """v3.0 Ch.02: receive ONE one-time physical state left by a slow-variable manifestation.

    Owner submission mode: this handler is only reachable through
    ``apply_v04_neighborhood_command`` (operation_key idempotency + receipt), so a
    replay of the same manifestation can never write a second physical state.
    Never touches knowledge_status upward past ``told_clue`` (in-person visits own
    ``visited``), and never records quietness — the place-booklet firsthand rule
    (``亲历前不得记安静度``) stays intact.
    """
    places = state["places"]
    place_id = (
        _clean_text(payload.get("place_id"), field_name="place_id", max_len=48)
        if payload.get("place_id") is not None
        else "bookstore"
    )
    if place_id not in places:
        raise V04NeighborhoodError(
            "PLACE_NOT_FOUND", f"Place {place_id!r} is not part of the neighborhood", http_status=404
        )
    place = places[place_id]
    physical_states = place.setdefault("physical_states", [])
    if not isinstance(physical_states, list):
        raise V04NeighborhoodError(
            "STATE_CORRUPTED", "physical_states must be a list", http_status=503
        )
    if len(physical_states) >= MAX_POI_PHYSICAL_STATES:
        raise V04NeighborhoodError(
            "POI_PHYSICAL_STATES_FULL",
            f"Place {place_id!r} already carries {MAX_POI_PHYSICAL_STATES} one-time physical states",
            http_status=409,
        )

    state_id = _require_id(payload.get("state_id"), field_name="state_id")
    if any(s.get("state_id") == state_id for s in physical_states if isinstance(s, Mapping)):
        raise V04NeighborhoodError(
            "POI_STATE_EXISTS", f"Physical state {state_id!r} already exists", http_status=409
        )
    label = _sanitize_public_text(_clean_text(payload.get("label"), field_name="label", max_len=120))
    source_ref = _clean_text(payload.get("source_ref"), field_name="source_ref", max_len=128)
    perception_tier = _clean_text(payload.get("perception_tier"), field_name="perception_tier", max_len=16)
    if perception_tier not in PERCEPTION_TIERS:
        raise V04NeighborhoodError(
            "INVALID_FIELDS", f"perception_tier must be one of {PERCEPTION_TIERS}", http_status=400
        )
    manifested_at = (
        format_iso(parse_iso_moment(payload["manifested_at"], field_name="manifested_at"))
        if payload.get("manifested_at") is not None
        else format_iso(now_dt)
    )

    doc = {
        "state_id": state_id,
        "label": label,
        "source_ref": source_ref,
        "perception_tier": perception_tier,
        "manifested_at": manifested_at,
        "one_time": True,
        "retracted": False,
    }
    physical_states.append(doc)

    # knowledge_status bridge: an unwitnessed manifestation is available as rumor
    # (told_clue); "visited" stays exclusive to in-person journeys.
    knowledge_after = place.get("knowledge_status")
    if knowledge_after == "unknown":
        place["knowledge_status"] = "told_clue"
        place["clue_note"] = f"街坊传闻：{label}"
        place["told_clue_from"] = source_ref
        place["told_clue_at"] = manifested_at
        knowledge_after = "told_clue"

    return {
        "operation": "record_poi_physical_state",
        "place_id": place_id,
        "state_id": state_id,
        "perception_tier": perception_tier,
        "manifested_at": manifested_at,
        "one_time": True,
        "knowledge_status_after": knowledge_after,
        "physical_state_count": len(physical_states),
    }


def _handle_update_place_booklet(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    booklet = state["public_life"]["place_booklet"]
    now_str = format_iso(now_dt)

    raw_op = payload.get("operation")
    if raw_op is not None:
        op = _clean_text(raw_op, field_name="operation", max_len=32)
    elif payload.get("pause") is True:
        op = "pause_booklet"
    elif payload.get("place_id") is None and payload.get("shared_with_partner") is not None:
        op = "set_sharing"
    else:
        op = "record_entry"

    if op in ("add_entry", "update_entry"):
        op = "record_entry"

    if payload.get("title") is not None:
        booklet["title"] = _clean_text(payload["title"], field_name="title", max_len=100)
    if payload.get("purpose") is not None:
        booklet["purpose"] = _clean_text(payload["purpose"], field_name="purpose", max_len=240)
    if payload.get("source_artifact_id") is not None:
        booklet["source_artifact_id"] = _clean_text(
            payload["source_artifact_id"], field_name="source_artifact_id", max_len=160
        )
    if payload.get("work_ref") is not None:
        booklet["work_ref"] = _clean_text(payload["work_ref"], field_name="work_ref", max_len=160)
    if payload.get("shared_with_partner") is not None:
        if not isinstance(payload["shared_with_partner"], bool):
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "shared_with_partner must be boolean", http_status=400
            )
        booklet["shared_with_partner"] = payload["shared_with_partner"]

    if op == "pause_booklet":
        pause_note = (
            _optional_text(
                payload.get("note") if payload.get("note") is not None else payload.get("impression"),
                field_name="note",
                max_len=200,
            )
            or "暂时不想做了"
        )
        booklet["status"] = "paused"
        booklet["paused_at"] = now_str
        booklet["pause_note"] = pause_note
        booklet["updated_at"] = now_str
        return {
            "operation": "pause_booklet",
            "status": "paused",
            "pause_note": pause_note,
            "failure_penalty": 0,
            "reminders_active": False,
        }

    if op == "resume_booklet":
        booklet["status"] = "active"
        booklet["paused_at"] = None
        booklet["updated_at"] = now_str
        return {
            "operation": "resume_booklet",
            "status": "active",
        }

    if op == "set_sharing":
        booklet["updated_at"] = now_str
        return {
            "operation": "set_sharing",
            "shared_with_partner": booklet["shared_with_partner"],
        }

    if op == "record_entry":
        pid = _clean_text(payload.get("place_id"), field_name="place_id", max_len=48)
        if pid not in PLACES_BY_ID:
            raise V04NeighborhoodError("INVALID_FIELDS", f"Unknown place_id {pid!r}", http_status=400)

        # Strict firsthand visited guard (Ch.45 §二)
        place_state = state["places"][pid]
        if place_state.get("knowledge_status") != "visited":
            raise V04NeighborhoodError(
                "PLACE_NOT_VISITED_YET",
                f"Place {pid!r} has knowledge_status={place_state.get('knowledge_status')!r}; visit in person before recording firsthand quietness",
                http_status=409,
            )

        q_band = (
            _clean_text(payload["quietness_band"], field_name="quietness_band", max_len=24)
            if payload.get("quietness_band") is not None
            else "quiet"
        )
        if q_band not in QUIETNESS_BANDS:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"quietness_band must be one of {QUIETNESS_BANDS}", http_status=400
            )

        raw_imp = payload.get("impression") if payload.get("impression") is not None else payload.get("note")
        impression = _clean_text(raw_imp, field_name="impression", max_len=500, allow_newlines=True)
        next_hint = _optional_text(payload.get("next_step_hint"), field_name="next_step_hint", max_len=200)

        entry = {
            "place_id": pid,
            "place_name": place_state["name"],
            "quietness_band": q_band,
            "impression": impression,
            "next_step_hint": next_hint,
            "recorded_at": now_str,
        }
        booklet["entries"][pid] = entry
        booklet["status"] = "active"
        booklet["paused_at"] = None
        booklet["revision"] = int(booklet.get("revision") or 0) + 1
        booklet["updated_at"] = now_str
        return {
            "operation": "record_entry",
            "place_id": pid,
            "quietness_band": q_band,
            "booklet_revision": booklet["revision"],
            "shared_with_partner": booklet["shared_with_partner"],
        }

    raise V04NeighborhoodError(
        "INVALID_FIELDS", f"Unsupported booklet operation {op!r}", http_status=400
    )


# ---------------------------------------------------------------------------
# Unified Command Application with Idempotency & CAS
# ---------------------------------------------------------------------------


def apply_v04_neighborhood_command(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Apply one validated neighborhood command with operation_key idempotency & CAS.

    Returns ``(status_label, action_result, projected_envelope_core)`` where
    ``status_label`` is ``"applied"`` or ``"already_applied"``.
    """
    root = Path(day_root)
    now_dt = resolve_now_readonly(root, now_iso)
    now_str = format_iso(now_dt)
    state, _ = load_state_readonly(root)

    action = str(payload["action"]).strip()
    op_key: Optional[str] = None
    if payload.get("operation_key") is not None:
        op_key = _clean_text(payload["operation_key"], field_name="operation_key", max_len=128)

    semantic_payload = {
        k: v
        for k, v in payload.items()
        if k not in ("operation_key", "intent_fingerprint", "expected_revision")
    }
    explicit_fp = (
        _clean_text(payload["intent_fingerprint"], field_name="intent_fingerprint", max_len=128)
        if payload.get("intent_fingerprint") is not None
        else ""
    )
    intent_fp = canonical_digest({
        "action": action,
        "actor_side": actor_side,
        "semantic": semantic_payload,
        "explicit_fp": explicit_fp,
    })

    # 1. Idempotent replay check
    if op_key is not None and op_key in state["operation_receipts"]:
        prev = state["operation_receipts"][op_key]
        if prev.get("intent_fingerprint") != intent_fp:
            raise V04NeighborhoodError(
                "OPERATION_KEY_CONFLICT",
                "Same operation_key reused with a different command payload",
                http_status=409,
            )
        projected = project_v04_neighborhood(
            root,
            has_subject_capability=(actor_side == "subject"),
            now_iso=now_str,
            preloaded_state=state,
        )
        return "already_applied", copy.deepcopy(prev.get("result") or {}), projected

    # 2. Optional expected_revision CAS check
    if payload.get("expected_revision") is not None:
        exp_rev = payload["expected_revision"]
        if isinstance(exp_rev, bool) or not isinstance(exp_rev, int) or exp_rev < 0:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "expected_revision must be a non-negative integer", http_status=400
            )
        if int(state["revision"]) != int(exp_rev):
            raise V04NeighborhoodError(
                "REVISION_CONFLICT",
                f"Expected revision {exp_rev}, current revision is {state['revision']}",
                http_status=409,
            )

    # 3. Settle existing due boundaries up to now_dt BEFORE applying new command (Ch.43 §五)
    settle_all_processes_in_place(state, now_dt)

    # 4. Dispatch domain action
    if action == "set_local_weather":
        result = _handle_set_local_weather(state, payload, now_dt=now_dt)
    elif action == "hang_or_collect_cloth":
        result = _handle_hang_or_collect_cloth(state, payload, now_dt=now_dt)
    elif action == "water_or_move_plant":
        result = _handle_water_or_move_plant(state, payload, now_dt=now_dt)
    elif action == "set_roadblock":
        result = _handle_set_roadblock(state, payload, now_dt=now_dt)
    elif action == "start_journey":
        result = _handle_start_journey(state, payload, now_dt=now_dt)
    elif action == "advance_or_turnback_journey":
        result = _handle_advance_or_turnback_journey(state, payload, now_dt=now_dt)
    elif action == "craft_paper_or_box":
        result = _handle_craft_paper_or_box(state, payload, now_dt=now_dt)
    elif action == "bookstore_service":
        result = _handle_bookstore_service(state, payload, now_dt=now_dt)
    elif action == "shared_shelf_loan":
        result = _handle_shared_shelf_loan(state, payload, actor_side=actor_side, now_dt=now_dt)
    elif action == "post_public_notice":
        result = _handle_post_public_notice(state, payload, actor_side=actor_side, now_dt=now_dt)
    elif action == "read_public_notice":
        result = _handle_read_public_notice(state, payload, now_dt=now_dt)
    elif action == "record_poi_physical_state":
        result = _handle_record_poi_physical_state(state, payload, now_dt=now_dt)
    elif action == "update_place_booklet":
        result = _handle_update_place_booklet(state, payload, now_dt=now_dt)
    else:
        raise V04NeighborhoodError("INVALID_FIELDS", f"Unknown action {action!r}", http_status=400)

    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_str

    if op_key is not None:
        receipts = state["operation_receipts"]
        receipts[op_key] = {
            "intent_fingerprint": intent_fp,
            "action": action,
            "actor_side": actor_side,
            "revision": state["revision"],
            "applied_at": now_str,
            "result": copy.deepcopy(result),
        }
        while len(receipts) > MAX_OPERATION_RECEIPTS:
            oldest = next(iter(receipts))
            del receipts[oldest]

    save_state_atomic(root, state)

    projected = project_v04_neighborhood(
        root,
        has_subject_capability=(actor_side == "subject"),
        now_iso=now_str,
        preloaded_state=state,
    )
    return "applied", result, projected


__all__ = [
    "AIRFLOW_KINDS",
    "BOOKSTORE_STARTER_BOOKS",
    "BOX_STEPS",
    "CLOTH_SUPPORTS",
    "DRY_SLEEP_THRESHOLD",
    "ENGINE_SCHEMA",
    "INITIAL_PAPER_UNITS",
    "JOURNEY_STATES",
    "KNOWLEDGE_STATUSES",
    "MAX_POI_PHYSICAL_STATES",
    "PERCEPTION_TIERS",
    "PLACES_BLUEPRINT",
    "PLACES_BY_ID",
    "PLANT_ALLOWED_PLACES",
    "PLANT_STAGES",
    "ROUTES_CATALOG",
    "SEASON_KINDS",
    "SHARED_SHELF_STARTER_ITEMS",
    "STATE_DIRNAME",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "V04NeighborhoodError",
    "WEATHER_KINDS",
    "advance_v04_neighborhood_processes",
    "apply_v04_neighborhood_command",
    "canonical_digest",
    "compute_cloth_hourly_rate",
    "default_state",
    "format_iso",
    "load_state_readonly",
    "moisture_to_band",
    "parse_iso_moment",
    "project_v04_neighborhood",
    "read_character_identity_readonly",
    "resolve_now_readonly",
    "save_state_atomic",
    "settle_all_processes_in_place",
    "state_file_path",
]
