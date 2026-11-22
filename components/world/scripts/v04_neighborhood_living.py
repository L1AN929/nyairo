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
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:  # ``components/life/scripts`` is on sys.path in tests and the life service
    from state_tx_guard import (  # type: ignore[import-not-found]
        reconcile_evicted_or_active_op,
        stamp_receipt,
        state_file_lock,
        trim_receipts_by_op_seq,
    )
except ImportError:  # pragma: no cover - world service bootstrap
    _LIFE_SCRIPTS = Path(__file__).resolve().parents[2] / "life" / "scripts"
    if str(_LIFE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LIFE_SCRIPTS))
    from state_tx_guard import (  # type: ignore[import-not-found,no-redef]
        reconcile_evicted_or_active_op,
        stamp_receipt,
        state_file_lock,
        trim_receipts_by_op_seq,
    )

try:
    from components.world.scripts import v05_season_solar_pulse as _season_solar_pulse
except ImportError:  # pragma: no cover - flat script import fallback
    _WORLD_SCRIPTS_SELF = Path(__file__).resolve().parent
    if str(_WORLD_SCRIPTS_SELF) not in sys.path:
        sys.path.insert(0, str(_WORLD_SCRIPTS_SELF))
    import v05_season_solar_pulse as _season_solar_pulse  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat script import fallback
    _WORLD_SCRIPTS_SELF = Path(__file__).resolve().parent
    if str(_WORLD_SCRIPTS_SELF) not in sys.path:
        sys.path.insert(0, str(_WORLD_SCRIPTS_SELF))
    import instance_paths as _instance_paths  # type: ignore[no-redef]

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
# Ch.41 §四/§五: Season daylight ecology, tolerant-plant milestones & seasonal market
# ---------------------------------------------------------------------------
# Every value below is a plain deterministic function of the world clock and the
# declared environment.  Zero randomness, zero model calls and zero writes when
# read through the projection: a legacy state file that never carried these keys
# still derives them in memory and is never rewritten by a plain GET.

# Sunlight window per season as ``(start_min, end_min)`` wall-clock minutes.
SEASON_SUNLIGHT_WINDOWS: dict[str, tuple[int, int]] = {
    "winter": (8 * 60, 16 * 60 + 30),
    "summer": (5 * 60 + 30, 19 * 60 + 30),
    "spring": (6 * 60 + 30, 18 * 60),
    "autumn": (6 * 60 + 30, 18 * 60),
}
SEASON_SUNLIGHT_LABELS: dict[str, str] = {
    "winter": "08:00-16:30",
    "summer": "05:30-19:30",
    "spring": "06:30-18:00",
    "autumn": "06:30-18:00",
}
SEASON_SUMMARY_CN: dict[str, str] = {
    "spring": "春日天亮得早，院里光线柔和，坐一会儿正合适。",
    "summer": "夏日白昼很长，傍晚院里有风，适合在外头多待。",
    "autumn": "秋日白昼收短，傍晚凉意渐起，靠窗更暖和些。",
    "winter": "冬日白昼很短，晒得到太阳的时间不多，靠窗更暖。",
}
WINTER_DORMANT_MOISTURE_RATE = 0.55
WINTER_DORMANT_GROWTH_RATE = 0.15
PLANT_LEAF_BASE_COUNT = 3
PLANT_LEAF_MAX_BONUS = 12
PLANT_LEAF_GROWTH_PER_LEAF = 24.0

# Street-corner seasonal markets: fixed calendar dates, missable by design.
FESTIVAL_BY_SEASON: dict[str, str] = {
    "spring": "春种子市",
    "summer": "夏纳凉夜市",
    "autumn": "秋晒叶市",
    "winter": "冬纸灯市",
}
FESTIVAL_EVENT_DAYS: tuple[int, ...] = (12, 24)
FESTIVAL_ACTIVE_START_HOUR = 10
FESTIVAL_ACTIVE_END_HOUR = 18
FESTIVAL_RESIDUAL_HOURS = 48
FESTIVAL_TRACE_ITEMS: tuple[str, ...] = (
    "街角檐下还没撤下的纸灯笼串",
    "巷口集市撤场后的旧木分类回收箱",
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

# ---------------------------------------------------------------------------
# Ch.26: Living Shopkeeper Substrate (backwards-compatible inside ``bookstore``)
# ---------------------------------------------------------------------------
# One shopkeeper with a current task, a bounded memory of concrete public
# counter events, and a hard privilege boundary.  Everything here is a plain
# deterministic rotation / bounded append driven by the clock: zero LLM calls,
# zero global friendliness score, zero crisis debt.

SHOPKEEPER_ID = "nukui_shinichi"
SHOPKEEPER_NAME = "温井慎一"
SHOPKEEPER_ROLE = "街角旧书店店主"
SHOPKEEPER_TASK_ROTATION_HOURS = 3
SHOPKEEPER_TASKS: tuple[tuple[str, str], ...] = (
    ("sorting_new_crate", "正在拆箱整理新到的旧画册"),
    ("labeling_spines", "正在给几本旧书脊贴手写价签"),
    ("rearranging_window_seat", "把靠窗那张旧木凳往里挪了半寸"),
    ("counter_display_arrangement", "在柜台边整理小卡与画册陈设"),
)
MAX_SHOPKEEPER_MEMORIES = 12
MAX_SHOPKEEPER_AMBIENT_CHANGES = 8
SHOPKEEPER_PRIVILEGE_BOUNDARY = "public_counter_only"
CARD_SPOT_AUTO_DECIDE_HOURS = 24
DEFAULT_RUMOR_CLAIM_CN = "听说往河边那条近道最近好像在修路"
CARD_SPOT_ADOPTED_REASON_CN = "他按你当时指的位置摆在了靠窗那一格"
CARD_SPOT_RELOCATED_REASON_CN = "原先挑的位置刚好挡住摄影集书脊，他顺手挪到了柜台斜架上"
SHOPKEEPER_DEFAULT_AMBIENT_CHANGES: tuple[str, ...] = (
    "靠窗新摆了一张能晒到午后太阳的旧木凳",
    "摄影画册挪到了顺手的低一层书架",
)

# Ch.42 & Ch.46 §三 Batch E: Second Independent Neighborhood Resident (Cafe Roaster)
CAFE_RESIDENT_ID = "saeki_aoi"
CAFE_RESIDENT_NAME = "佐伯葵"
CAFE_RESIDENT_ROLE = "街角焙煎咖啡屋店主"
CAFE_HOME_VENUE = "cafe"
CAFE_VENUE_NAME_CN = "街角焙煎咖啡屋"
CAFE_OPEN_WINDOW = "08:00-18:00"
CAFE_OPEN_START_MIN = 8 * 60
CAFE_OPEN_END_MIN = 18 * 60
CAFE_TASK_ROTATION_HOURS = 3
# +2h wall-clock phase offset (02/05/08/11/14/17/20/23) vs bookstore's 00/03/06/09/12/15/18/21
CAFE_SCHEDULE_OFFSET_HOURS = 2
CAFE_RESIDENT_PHASES: tuple[tuple[str, str], ...] = (
    ("morning_roast", "正在烘豆机旁照看晨间烘焙豆"),
    ("counter_pour_over", "正在木吧台后闷蒸手冲咖啡"),
    ("stepped_out_errand", "吧台立着小木牌：去巷口补充滤纸与牛奶，稍后回来"),
    ("evening_cleanup", "正在擦拭磨豆机并收整门口手写黑板"),
    ("closed_rest", "咖啡屋已打烊熄灯，门缝留着微弱烘豆余香"),
)
CAFE_DEFAULT_RUMOR_CLAIM_CN = "听说街角往河边的石阶傍晚风有点急"
CAFE_SEASON_REACTIONS_CN: dict[str, str] = {
    "spring": "春日在窗台玻璃瓶里插了一支新剪的迷迭香",
    "summer": "夏日吧台备着冰镇麦茶与冷萃玻璃壶",
    "autumn": "秋日换上了偏醇厚的中深烘拼配豆单",
    "winter": "冬日门口挂上了厚棉布门帘挡风保温",
}

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
            "season": None,
            "season_explicit": False,
            "weather_slot_key": None,
            "manual_pin_until": None,
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
            "leaf_count": PLANT_LEAF_BASE_COUNT,
            "seasonal_state": "active_growth",
            "new_leaf_note_cn": None,
            "last_new_leaf_at": None,
            "wilt_recovered_count": 0,
            "recovery_note_cn": None,
            "public_care_note_cn": None,
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
            "attended_festivals": [],
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


def _derive_season_ecology(
    season: str, weather: str, airflow: str, now_dt: datetime
) -> dict[str, Any]:
    """Pure, zero-write daylight / yard-comfort read for the current instant.

    A short honest summary of whether the yard is usable right now and whether
    the indoor spot by the window is the warmer choice.  No model calls, no
    abbreviations and no terms leaking into the Chinese summary line.
    """
    season_key = season if season in SEASON_SUNLIGHT_WINDOWS else "autumn"
    start_min, end_min = SEASON_SUNLIGHT_WINDOWS[season_key]
    cur_min = now_dt.hour * 60 + now_dt.minute
    in_window = start_min <= cur_min < end_min
    clear_sky = weather == "clear"
    rainy = weather in ("drizzle", "rain")
    sunlight_active = bool(in_window and clear_sky)

    yard_suitable = False
    yard_hint_cn = ""
    if rainy:
        yard_hint_cn = "雨丝里院子湿滑，先在有檐处待一会儿更安稳"
    elif season_key == "winter":
        if (not in_window) or airflow == "strong":
            yard_hint_cn = "冬夜露天微寒，室内靠窗更安稳"
        else:
            yard_suitable = True
            yard_hint_cn = "冬日有阳光时院里能短坐，风大就回檐下"
    elif season_key == "summer":
        if 17 * 60 <= cur_min < 20 * 60 + 30:
            yard_suitable = True
            yard_hint_cn = "夏傍晚院风凉爽适宜停留"
        elif in_window:
            yard_suitable = True
            yard_hint_cn = "夏日白天阳光足，院里略晒但有荫可躲"
        else:
            yard_hint_cn = "夏夜院里蚊虫多，回屋更安稳"
    else:
        if in_window:
            yard_suitable = True
            yard_hint_cn = "白天院里光线好，坐着不冷不热"
        else:
            yard_hint_cn = "入夜后院子里凉下来，回室内更舒服"

    if season_key == "winter":
        window_warmth = True
    elif season_key == "autumn" and (rainy or not in_window):
        window_warmth = True
    else:
        window_warmth = False

    pulse = _season_solar_pulse.project_season_solar_pulse(
        now_iso=now_dt,
        explicit_season=season_key,
        weather=weather,
    )
    st = pulse["solar_term"]
    micro = pulse["microclimate"]

    return {
        "season": season_key,
        "sunlight_window": SEASON_SUNLIGHT_LABELS[season_key],
        "daylight_minutes": end_min - start_min,
        "is_daylight": bool(in_window),
        "sunlight_active": bool(sunlight_active),
        "yard_suitable_now": bool(yard_suitable),
        "yard_hint_cn": yard_hint_cn,
        "window_warmth_recommended": bool(window_warmth),
        "season_summary_cn": SEASON_SUMMARY_CN[season_key],
        "solar_term": {
            "solar_term_idx": int(st["solar_term_idx"]),
            "term_id": str(st["term_id"]),
            "term_name_cn": str(st["term_name_cn"]),
            "is_term_day": bool(st["is_term_day"]),
            "next_term_name_cn": str(st["next_term_name_cn"]),
            "days_until_next_term": int(st["days_until_next_term"]),
            "phenology_note_cn": str(st["phenology_note_cn"]),
        },
        "tea_cooling_multiplier": float(micro["tea_cooling_multiplier"]),
        "soundscape_palette": str(micro["soundscape_palette"]),
        "window_motif": str(micro["window_motif"]),
    }


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


def _plant_leaf_count_for_growth(growth_points: float) -> int:
    """Deterministic leaf-count milestone read from accumulated growth points.

    ``3 + min(12, growth // 24)`` -> stays within ``3..15`` leaves.  The number
    is a world-process result (water + light accumulated over time), never a
    mood effect.
    """
    bonus = int(max(0.0, float(growth_points)) // PLANT_LEAF_GROWTH_PER_LEAF)
    return PLANT_LEAF_BASE_COUNT + min(PLANT_LEAF_MAX_BONUS, bonus)


def _plant_seasonal_state(season: str, hydration: float) -> str:
    """Derive the plant's seasonal mode: dry rest, winter dormancy or growth."""
    if hydration <= 25.0:
        return "resting_dry"
    if season == "winter":
        return "winter_dormant"
    return "active_growth"


def _advance_plant_leaf_milestone(
    plant: dict[str, Any], *, previous_growth: float, now_dt: datetime
) -> bool:
    """Record a new-leaf milestone when growth crosses to a higher leaf count."""
    new_count = _plant_leaf_count_for_growth(float(plant.get("growth_points", 0.0)))
    stored = plant.get("leaf_count")
    if isinstance(stored, int) and not isinstance(stored, bool):
        prev_count = int(stored)
    else:
        prev_count = _plant_leaf_count_for_growth(previous_growth)
    plant["leaf_count"] = new_count
    if new_count > prev_count:
        plant["last_new_leaf_at"] = format_iso(now_dt)
        plant["new_leaf_note_cn"] = "抽出了新绿叶（水分与光照累积所致）"
        return True
    return False


def _settle_plant_in_place(
    plant: dict[str, Any],
    *,
    weather: str,
    now_dt: datetime,
    season: str = "autumn",
) -> bool:
    plant["never_dies_offline"] = True
    season_key = season if season in SEASON_KINDS else "autumn"
    seg_raw = plant.get("segment_started_at") or plant.get("last_anchor_at")
    if not seg_raw:
        hydration_now = float(plant.get("hydration", 70.0))
        growth_now = float(plant.get("growth_points", 0.0))
        plant["stage"] = _derive_plant_stage(hydration_now, growth_now)
        plant["seasonal_state"] = _plant_seasonal_state(season_key, hydration_now)
        _advance_plant_leaf_milestone(plant, previous_growth=growth_now, now_dt=now_dt)
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
    # A winter plant that is still comfortably hydrated rests rather than grows:
    # it transpires at 0.55x and only adds 0.15x growth, so gentle over-wintering
    # never punishes an absent caretaker.
    winter_dormant = season_key == "winter" and start_h > 25.0

    if plant.get("public_care_enabled"):
        # Neighbor public care keeps soil comfortably moist while away
        target_h = max(start_h, 60.0) if start_h < 60.0 else max(55.0, start_h - 0.15 * hours)
        new_h = min(100.0, target_h)
        growth_rate = 0.8 * (WINTER_DORMANT_GROWTH_RATE if winter_dormant else 1.0)
        new_g = start_g + growth_rate * hours
        if hours >= 12.0:
            plant["public_care_note_cn"] = "远出期间托街坊顺手添过一次水"
    else:
        if place_id == "yard" and weather in ("drizzle", "rain"):
            rain_gain = 14.0 if weather == "drizzle" else 28.0
            new_h = min(98.0, start_h + rain_gain * hours)
        else:
            loss_rate = {"yard": 1.5, "porch": 1.0, "home": 0.8}.get(place_id, 1.0)
            if winter_dormant:
                loss_rate *= WINTER_DORMANT_MOISTURE_RATE
            new_h = max(0.0, start_h - loss_rate * hours)

        # Growth accumulates only while well-hydrated (> 25.0)
        if start_h > 25.0:
            loss_rate = {"yard": 1.5, "porch": 1.0, "home": 0.8}.get(place_id, 1.0)
            if winter_dormant:
                loss_rate *= WINTER_DORMANT_MOISTURE_RATE
            if new_h > 25.0 or (place_id == "yard" and weather in ("drizzle", "rain")):
                productive_hours = hours
            else:
                productive_hours = max(0.0, (start_h - 25.0) / loss_rate)
            light_factor = {"yard": 1.25, "porch": 1.1, "home": 0.85}.get(place_id, 1.0)
            growth_rate = 1.2 * light_factor
            if winter_dormant:
                growth_rate *= WINTER_DORMANT_GROWTH_RATE
            new_g = start_g + growth_rate * productive_hours
        else:
            new_g = start_g

    new_h_r = round(new_h, 4)
    new_g_r = round(new_g, 4)
    new_stage = _derive_plant_stage(new_h_r, new_g_r)
    new_seasonal = _plant_seasonal_state(season_key, new_h_r)
    now_str = format_iso(now_dt)
    prev_seasonal = plant.get("seasonal_state")

    changed = (
        abs(float(plant.get("hydration", 0.0)) - new_h_r) > 1e-6
        or abs(float(plant.get("growth_points", 0.0)) - new_g_r) > 1e-6
        or plant.get("stage") != new_stage
        or prev_seasonal != new_seasonal
    )
    plant["hydration"] = new_h_r
    plant["growth_points"] = new_g_r
    plant["stage"] = new_stage
    plant["seasonal_state"] = new_seasonal
    plant["last_anchor_at"] = now_str
    if _advance_plant_leaf_milestone(plant, previous_growth=start_g, now_dt=now_dt):
        changed = True
    return changed


def _reanchor_plant_segment(
    plant: dict[str, Any],
    *,
    old_weather: str,
    now_dt: datetime,
    season: str = "autumn",
) -> None:
    season_key = season if season in SEASON_KINDS else "autumn"
    if plant.get("segment_started_at") is not None:
        _settle_plant_in_place(plant, weather=old_weather, now_dt=now_dt, season=season_key)
    now_str = format_iso(now_dt)
    plant["segment_start_hydration"] = float(plant.get("hydration", 70.0))
    plant["segment_start_growth"] = float(plant.get("growth_points", 0.0))
    plant["segment_started_at"] = now_str
    plant["last_anchor_at"] = now_str
    plant["stage"] = _derive_plant_stage(plant["hydration"], plant["growth_points"])
    plant["seasonal_state"] = _plant_seasonal_state(
        season_key, float(plant.get("hydration", 70.0))
    )


# ---------------------------------------------------------------------------
# Ch.41 §四: Missable street-corner seasonal markets & physical residual traces
# ---------------------------------------------------------------------------


def extract_explicit_season(env: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return an explicit season override, or ``None`` for dynamic calendar season."""
    if not isinstance(env, Mapping):
        return None
    norm = _season_solar_pulse.normalize_season(env.get("season"))
    if norm is None:
        return None
    if env.get("season_explicit") is True:
        return norm
    if env.get("season_explicit") is False:
        return None
    if env.get("updated_at") is not None:
        return norm
    if "season_explicit" not in env:
        return norm
    return None


def _resolve_effective_season(env: Optional[Mapping[str, Any]], now_dt: datetime) -> str:
    explicit = extract_explicit_season(env)
    if explicit is not None:
        return explicit
    return _season_solar_pulse.resolve_effective_season(now_dt)[0]


def _weather_slot_key(local_dt: datetime) -> str:
    return f"{local_dt.date().isoformat()}:S{int(local_dt.hour) // 6}"


def _derive_seasonal_festival(
    state: Mapping[str, Any], now_dt: datetime
) -> dict[str, Any]:
    """Pure, zero-write seasonal-market read driven only by the world calendar.

    A street-corner market runs every month on the 12th and 24th, 10:00–18:00
    local.  For 48 hours after it closes, real physical traces remain (an un-taken-
    down lantern string and an old sorting/recycling crate).  Attendance is never
    fabricated: ``attended_firsthand`` only becomes True when the subject actually
    stood at ``street_corner`` during an active window and that festival key was
    recorded on real state by the journey commands.
    """
    env = state.get("environment")
    season = _resolve_effective_season(env if isinstance(env, Mapping) else None, now_dt)
    if season not in FESTIVAL_BY_SEASON:
        season = _season_solar_pulse.resolve_effective_season(now_dt)[0]

    tz = now_dt.tzinfo
    year, month = now_dt.year, now_dt.month
    phase = "quiet"
    festival_key: Optional[str] = None
    residual_traces: list[str] = []

    for day in FESTIVAL_EVENT_DAYS:
        event_day = datetime(year, month, day, tzinfo=tz)
        active_start = event_day.replace(hour=FESTIVAL_ACTIVE_START_HOUR)
        active_end = event_day.replace(hour=FESTIVAL_ACTIVE_END_HOUR)
        residual_end = active_end + timedelta(hours=FESTIVAL_RESIDUAL_HOURS)
        candidate_key = f"festival_{year:04d}{month:02d}{day:02d}"
        if active_start <= now_dt < active_end:
            phase = "active"
            festival_key = candidate_key
            break
        if active_end <= now_dt < residual_end:
            phase = "residual_traces"
            festival_key = candidate_key
            residual_traces = list(FESTIVAL_TRACE_ITEMS)
            break

    festival_name_cn = (
        FESTIVAL_BY_SEASON[season] if phase in ("active", "residual_traces") else None
    )

    public_life = state.get("public_life")
    attended_raw = (
        public_life.get("attended_festivals") if isinstance(public_life, Mapping) else None
    )
    attended = [str(x) for x in attended_raw] if isinstance(attended_raw, list) else []
    attended_firsthand = bool(festival_key is not None and festival_key in attended)

    if phase == "active":
        note_cn = f"街角正在办{festival_name_cn}"
        window_label_cn = f"每月十二日与二十四日上午十点到傍晚六点，街角有{festival_name_cn}"
    elif phase == "residual_traces":
        note_cn = "街角集市已经散了，檐下还留着纸灯笼串与回收箱"
        window_label_cn = "集市散了不到两天，街角还留着一点没撤干净的痕迹"
    else:
        note_cn = "街角这段时间没有集市"
        window_label_cn = "眼下街角没有集市，安静照常"

    return {
        "phase": phase,
        "season": season,
        "place_id": "street_corner",
        "festival_key": festival_key,
        "festival_name_cn": festival_name_cn,
        "residual_traces": residual_traces,
        "attended_firsthand": attended_firsthand,
        "attended_festivals": attended,
        "window_label_cn": window_label_cn,
        "note_cn": note_cn,
    }


def _record_festival_attendance_if_active(
    state: dict[str, Any], now_dt: datetime
) -> bool:
    """Record a firsthand festival visit only while standing at an active market.

    Called exclusively from the real journey commands (``start_journey`` /
    ``advance_or_turnback_journey``) so a plain read can never manufacture a
    shared experience.  Arriving during the leftover-trace window records
    nothing; the subject only sees the physical remains.
    """
    journey = state.get("journey")
    if not isinstance(journey, dict):
        return False
    if str(journey.get("current_place_id") or "") != "street_corner":
        return False
    info = _derive_seasonal_festival(state, now_dt)
    if info.get("phase") != "active":
        return False
    festival_key = info.get("festival_key")
    if not festival_key:
        return False
    public_life = state.get("public_life")
    if not isinstance(public_life, dict):
        return False
    attended = public_life.get("attended_festivals")
    if not isinstance(attended, list):
        attended = []
    if festival_key in attended:
        return False
    attended.append(festival_key)
    public_life["attended_festivals"] = attended
    return True


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


# ---------------------------------------------------------------------------
# Ch.26 §一 / §三 / §四 / §七: Living Shopkeeper Helpers
# ---------------------------------------------------------------------------


def _default_shopkeeper_life() -> dict[str, Any]:
    task_key, task_cn = SHOPKEEPER_TASKS[0]
    return {
        "shopkeeper_name": SHOPKEEPER_NAME,
        "current_task": task_key,
        "current_task_cn": task_cn,
        "current_task_started_at": None,
        "ambient_changes": list(SHOPKEEPER_DEFAULT_AMBIENT_CHANGES),
        "privilege_boundary": SHOPKEEPER_PRIVILEGE_BOUNDARY,
        "private_room_knowledge": False,
        "remembered_interactions": [],
        "card_spot_suggestions": [],
        "heard_rumors": [],
    }


def _ensure_shopkeeper_life(bookstore: dict[str, Any]) -> dict[str, Any]:
    """Lazily normalize ``bookstore["shopkeeper_life"]`` on a mutable state copy.

    Legacy state files never gain this key on a read: projections operate on a
    deep copy, and only a real mutation persists the normalized substrate.
    """
    life = bookstore.get("shopkeeper_life")
    if not isinstance(life, dict):
        life = _default_shopkeeper_life()
        bookstore["shopkeeper_life"] = life
        return life
    for key, val in _default_shopkeeper_life().items():
        if key not in life:
            life[key] = copy.deepcopy(val)
    life["shopkeeper_name"] = SHOPKEEPER_NAME
    life["privilege_boundary"] = SHOPKEEPER_PRIVILEGE_BOUNDARY
    life["private_room_knowledge"] = False
    if not isinstance(life.get("ambient_changes"), list):
        life["ambient_changes"] = list(SHOPKEEPER_DEFAULT_AMBIENT_CHANGES)
    for key in ("remembered_interactions", "card_spot_suggestions", "heard_rumors"):
        if not isinstance(life.get(key), list):
            life[key] = []
    return life


def _shopkeeper_task_for(now_dt: datetime) -> tuple[str, str, datetime]:
    """Deterministic task rotation keyed purely on neighborhood wall-clock time."""
    abs_hours = now_dt.toordinal() * 24 + now_dt.hour
    slot = abs_hours // SHOPKEEPER_TASK_ROTATION_HOURS
    slot_abs_hours = slot * SHOPKEEPER_TASK_ROTATION_HOURS
    start_dt = datetime.fromordinal(slot_abs_hours // 24).replace(tzinfo=now_dt.tzinfo)
    start_dt = start_dt + timedelta(hours=slot_abs_hours % 24)
    key, task_cn = SHOPKEEPER_TASKS[slot % len(SHOPKEEPER_TASKS)]
    return key, task_cn, start_dt


def _append_shopkeeper_ambient(life: dict[str, Any], note_cn: str) -> None:
    changes = [str(x) for x in (life.get("ambient_changes") or []) if str(x).strip()]
    if note_cn in changes:
        changes.remove(note_cn)
    changes.append(note_cn)
    life["ambient_changes"] = changes[-MAX_SHOPKEEPER_AMBIENT_CHANGES:]


def _remember_shopkeeper_interaction(
    life: dict[str, Any], *, kind: str, ref_id: str, note_cn: str, at_iso: str
) -> None:
    """Append one concrete public-counter event; asymmetric facts only, no praise."""
    rows = [r for r in (life.get("remembered_interactions") or []) if isinstance(r, Mapping)]
    rows.append({"kind": kind, "ref_id": ref_id, "note_cn": note_cn, "at": at_iso})
    life["remembered_interactions"] = rows[-MAX_SHOPKEEPER_MEMORIES:]


def _card_spot_auto_outcome(suggestion_id: str) -> str:
    digest = hashlib.sha256(str(suggestion_id).encode("utf-8")).hexdigest()
    return "adopted" if int(digest[:8], 16) % 2 == 0 else "relocated"


def _resolve_card_spot_suggestion(
    suggestion: dict[str, Any],
    now_dt: datetime,
    *,
    life: dict[str, Any],
    outcome: Optional[str] = None,
    reason_cn: Optional[str] = None,
) -> bool:
    """Resolve one pending suggestion with a grounded physical reason only."""
    chosen = outcome or _card_spot_auto_outcome(str(suggestion.get("suggestion_id") or ""))
    if chosen not in ("adopted", "relocated"):
        return False
    if not reason_cn:
        reason_cn = (
            CARD_SPOT_ADOPTED_REASON_CN
            if chosen == "adopted"
            else CARD_SPOT_RELOCATED_REASON_CN
        )
    suggestion["status"] = chosen
    suggestion["outcome_reason_cn"] = str(reason_cn)
    suggestion["resolved_at"] = format_iso(now_dt)
    if chosen == "adopted":
        _append_shopkeeper_ambient(
            life, f"柜台边的说明卡摆进了{suggestion.get('spot_note') or '他挑的位置'}"
        )
    else:
        _append_shopkeeper_ambient(life, "说明卡从原位置挪到了柜台斜架，免得挡住摄影集书脊")
    return True


def _settle_shopkeeper_life_in_place(
    state: dict[str, Any], now_dt: datetime
) -> dict[str, int]:
    """Rotate the shopkeeper's current task and auto-resolve card spots after 24h.

    Only ever touches an already-present ``shopkeeper_life`` substrate so a
    legacy file stays byte-identical on plain advance/projection reads.
    """
    counts = {"task_rotated": 0, "card_spots_decided": 0}
    b_store = (state.get("public_life") or {}).get("bookstore")
    if not isinstance(b_store, dict) or not isinstance(b_store.get("shopkeeper_life"), dict):
        return counts
    life = _ensure_shopkeeper_life(b_store)

    task_key, task_cn, start_dt = _shopkeeper_task_for(now_dt)
    start_iso = format_iso(start_dt)
    if life.get("current_task") != task_key:
        life["current_task"] = task_key
        life["current_task_cn"] = task_cn
        life["current_task_started_at"] = start_iso
        counts["task_rotated"] = 1
    elif life.get("current_task_started_at") != start_iso:
        life["current_task_started_at"] = start_iso

    if _is_bookstore_open(now_dt):
        for suggestion in life.get("card_spot_suggestions") or []:
            if not isinstance(suggestion, dict):
                continue
            if suggestion.get("status") != "pending_shopkeeper_review":
                continue
            raw_at = suggestion.get("suggested_at")
            if not raw_at:
                continue
            try:
                suggested_dt = parse_iso_moment(raw_at, field_name="suggested_at")
            except V04NeighborhoodError:
                continue
            if now_dt - suggested_dt >= timedelta(hours=CARD_SPOT_AUTO_DECIDE_HOURS):
                if _resolve_card_spot_suggestion(suggestion, now_dt, life=life):
                    counts["card_spots_decided"] += 1
    return counts


def _is_cafe_open(now_dt: datetime) -> bool:
    cur_min = now_dt.hour * 60 + now_dt.minute
    return CAFE_OPEN_START_MIN <= cur_min < CAFE_OPEN_END_MIN


def _cafe_phase_for(now_dt: datetime) -> tuple[str, str, datetime, bool]:
    """Independent 3-hour schedule for Saeki Aoi, offset from Nukui Shinichi's clock."""
    shifted_hours = now_dt.toordinal() * 24 + now_dt.hour - CAFE_SCHEDULE_OFFSET_HOURS
    slot = shifted_hours // CAFE_TASK_ROTATION_HOURS
    slot_abs_hours = slot * CAFE_TASK_ROTATION_HOURS + CAFE_SCHEDULE_OFFSET_HOURS
    start_dt = datetime.fromordinal(slot_abs_hours // 24).replace(tzinfo=now_dt.tzinfo)
    start_dt = start_dt + timedelta(hours=slot_abs_hours % 24)
    hour = now_dt.hour
    if 8 <= hour < 11:
        phase_key, phase_cn = CAFE_RESIDENT_PHASES[0]  # morning_roast
        at_counter = True
    elif 11 <= hour < 14:
        phase_key, phase_cn = CAFE_RESIDENT_PHASES[1]  # counter_pour_over
        at_counter = True
    elif 14 <= hour < 17:
        phase_key, phase_cn = CAFE_RESIDENT_PHASES[2]  # stepped_out_errand
        at_counter = False
    elif 17 <= hour < 20:
        phase_key, phase_cn = CAFE_RESIDENT_PHASES[3]  # evening_cleanup
        at_counter = True
    else:
        phase_key, phase_cn = CAFE_RESIDENT_PHASES[4]  # closed_rest
        at_counter = False
    return phase_key, phase_cn, start_dt, at_counter


def _cafe_environment_notes(
    weather: str, season: str, festival_info: Mapping[str, Any]
) -> dict[str, str]:
    if weather in ("drizzle", "rain"):
        weather_cn = "雨天屋檐下挂出了避雨暖歇木牌，门缝飘着深烘豆的热气"
    else:
        weather_cn = "晴天推开了半扇木格窗通风，烘焙香气顺着街角散开"
    season_key = season if season in CAFE_SEASON_REACTIONS_CN else "autumn"
    season_cn = CAFE_SEASON_REACTIONS_CN[season_key]
    fest_phase = str(festival_info.get("phase") or "quiet")
    if fest_phase == "active":
        fest_name = str(festival_info.get("festival_name_cn") or "街角集市")
        market_cn = f"趁{fest_name}在店门口支起了手冲试饮小木架"
    elif fest_phase == "residual_traces":
        market_cn = "集市散场后把门口手冲小木架收进了檐下"
    else:
        market_cn = ""
    parts = [p for p in (weather_cn, market_cn, season_cn) if p]
    return {
        "weather_reaction_cn": weather_cn,
        "season_reaction_cn": season_cn,
        "market_reaction_cn": market_cn,
        "environment_reaction_cn": "；".join(parts),
    }


def _default_cafe_resident_life() -> dict[str, Any]:
    phase_key, phase_cn = CAFE_RESIDENT_PHASES[0]
    return {
        "resident_id": CAFE_RESIDENT_ID,
        "resident_name": CAFE_RESIDENT_NAME,
        "role": CAFE_RESIDENT_ROLE,
        "home_venue": CAFE_HOME_VENUE,
        "current_phase": phase_key,
        "current_phase_cn": phase_cn,
        "current_phase_started_at": None,
        "privilege_boundary": SHOPKEEPER_PRIVILEGE_BOUNDARY,
        "private_room_knowledge": False,
        "favorability_score": None,
        "omission_debt": 0,
        "coaster_notes": [],
        "shared_errand_board": [],
        "remembered_interactions": [],
        "heard_rumors": [],
    }


def _ensure_cafe_resident_life(public_life: dict[str, Any]) -> dict[str, Any]:
    """Lazily normalize ``public_life["cafe_resident"]`` on a mutable state copy."""
    life = public_life.get("cafe_resident")
    if not isinstance(life, dict):
        life = _default_cafe_resident_life()
        public_life["cafe_resident"] = life
        return life
    for key, val in _default_cafe_resident_life().items():
        if key not in life:
            life[key] = copy.deepcopy(val)
    life["resident_id"] = CAFE_RESIDENT_ID
    life["resident_name"] = CAFE_RESIDENT_NAME
    life["role"] = CAFE_RESIDENT_ROLE
    life["home_venue"] = CAFE_HOME_VENUE
    life["privilege_boundary"] = SHOPKEEPER_PRIVILEGE_BOUNDARY
    life["private_room_knowledge"] = False
    life["favorability_score"] = None
    life["omission_debt"] = 0
    for key in ("coaster_notes", "shared_errand_board", "remembered_interactions", "heard_rumors"):
        if not isinstance(life.get(key), list):
            life[key] = []
    return life


def _settle_cafe_resident_in_place(state: dict[str, Any], now_dt: datetime) -> int:
    """Rotate Saeki Aoi's 3h phase only if ``cafe_resident`` already exists on state."""
    pub = state.get("public_life")
    if not isinstance(pub, dict) or not isinstance(pub.get("cafe_resident"), dict):
        return 0
    life = _ensure_cafe_resident_life(pub)
    phase_key, phase_cn, start_dt, _at_counter = _cafe_phase_for(now_dt)
    start_iso = format_iso(start_dt)
    if life.get("current_phase") != phase_key:
        life["current_phase"] = phase_key
        life["current_phase_cn"] = phase_cn
        life["current_phase_started_at"] = start_iso
        return 1
    if life.get("current_phase_started_at") != start_iso:
        life["current_phase_started_at"] = start_iso
    return 0


def _flip_rumors_for_route_in_list(
    rumors_list: Any,
    route_id: str,
    *,
    verified_blocked: bool,
    at_iso: str,
) -> int:
    if not isinstance(rumors_list, list):
        return 0
    flipped = 0
    for rumor in rumors_list:
        if not isinstance(rumor, dict) or rumor.get("route_id") != route_id:
            continue
        if rumor.get("verified_firsthand"):
            continue
        rumor["verified_firsthand"] = True
        rumor["verified_at"] = at_iso
        if verified_blocked:
            rumor["epistemic_status"] = "verified_firsthand"
            rumor["verification_note_cn"] = "亲眼看见了路口的围挡，不只是听说"
        else:
            rumor["epistemic_status"] = "refuted_firsthand"
            rumor["verification_note_cn"] = "走到路口时路面是通的，那条闲话没有应验"
            rumor["refutation_note_cn"] = "走到路口时路面是通的，那条闲话没有应验"
        flipped += 1
    return flipped


def _record_firsthand_rumor_check(
    state: Mapping[str, Any],
    route_id: str,
    *,
    verified_blocked: bool,
    at_iso: str,
) -> int:
    """Flip unverified rumors for ``route_id`` only when the subject stands there.

    Hearing a rumor never itself grants route knowledge; this helper is called
    exclusively on real entrance encounters (roadblock halt or free passage).
    """
    pub = state.get("public_life")
    if not isinstance(pub, Mapping):
        return 0
    flipped = 0
    b_store = pub.get("bookstore")
    if isinstance(b_store, Mapping):
        life = b_store.get("shopkeeper_life")
        if isinstance(life, Mapping):
            flipped += _flip_rumors_for_route_in_list(
                life.get("heard_rumors"),
                route_id,
                verified_blocked=verified_blocked,
                at_iso=at_iso,
            )
    cafe_life = pub.get("cafe_resident")
    if isinstance(cafe_life, Mapping):
        flipped += _flip_rumors_for_route_in_list(
            cafe_life.get("heard_rumors"),
            route_id,
            verified_blocked=verified_blocked,
            at_iso=at_iso,
        )
    return flipped


# ---------------------------------------------------------------------------
# Ch.39 §三–§六: Route Closure Epistemics, Sheltered Detour & Portable Matter
# ---------------------------------------------------------------------------

V08_MATTER_FILENAME = "v08_matter_space.json"
V08_SUBJECT_UNKNOWN_PARENT = "__subject_unknown__"

# Compact, human-readable place names used in the honest transit narration.
_PLACE_SHORT_NAMES: dict[str, str] = {
    "home": "家",
    "porch": "门廊",
    "yard": "小院",
    "street_corner": "街角",
    "bookstore": "旧书店",
    "riverside": "河边",
}


def _place_short_name(place_id: Any) -> str:
    pid = str(place_id or "").strip()
    if pid in _PLACE_SHORT_NAMES:
        return _PLACE_SHORT_NAMES[pid]
    row = PLACES_BY_ID.get(pid)
    if row is not None:
        tail = str(row.get("name") or "").split(" · ")[-1].strip()
        if tail:
            return tail
    return pid or "路上"


def _route_is_sheltered(route_id: Any) -> bool:
    """Whether a catalogued route keeps rain off the walker (honest, no guessing).

    Catalog ``sheltered`` wins; an unknown route id falls back to a plain
    shelter/roof/eaves/indoor keyword read so future sheltered walkways still
    project truthfully instead of defaulting to open air.
    """
    rid = str(route_id or "")
    spec = ROUTES_CATALOG.get(rid)
    if spec is not None:
        return bool(spec.get("sheltered"))
    hay = rid.lower()
    return any(
        tok in hay for tok in ("shelter", "roof", "eaves", "indoor", "porch", "檐", "廊", "屋", "门廊")
    )


def _journey_location_cn(journey: Mapping[str, Any]) -> str:
    """Plain-Chinese honest location line for the current journey state.

    Never claims arrival early: while ``in_transit`` this describes the active
    leg and its shelter cue; a roadblock pause names the junction it stopped at.
    """
    state = str(journey.get("state") or "")
    legs = list(journey.get("legs") or [])
    idx = int(journey.get("active_leg_index") or 0)
    leg = legs[idx] if 0 <= idx < len(legs) else None

    if state == "in_transit":
        if leg is not None:
            frm = _place_short_name(leg.get("from_place"))
            to = _place_short_name(leg.get("to_place"))
        else:
            frm = _place_short_name(journey.get("origin_place_id"))
            to = _place_short_name(journey.get("target_place_id"))
        if bool(journey.get("is_turning_back")):
            if frm and to:
                return f"在从{frm}折返{to}的路上"
            return f"在返回{to or '原处'}的路上"
        cue = "有檐长廊" if _route_is_sheltered(journey.get("active_route_id")) else "露天近道"
        if frm and to:
            return f"在从{frm}前往{to}的路上（{cue}）"
        return f"在路上（{cue}）"

    if state == "paused_at_node":
        anchor = journey.get("current_place_id")
        if not anchor and leg is not None:
            anchor = leg.get("from_place")
        anchor = _place_short_name(anchor or journey.get("origin_place_id"))
        if journey.get("halt_reason") == "ROADBLOCK_ENCOUNTERED":
            return f"停在{anchor}路口（发现前方临时围栏）"
        return f"停在{anchor}附近（稍作停留）"

    pid = journey.get("current_place_id")
    if pid:
        return f"在{_place_short_name(pid)}"
    return ""


def _read_v08_matter_object_readonly(root: Path, item_id: str) -> Optional[dict[str, Any]]:
    """Read one owned ``v08_universal_matter`` object with zero writes.

    Two-Worlds boundary: this owner only ever reads ``v08_matter_space.json``;
    it never creates, migrates or mutates it.  A missing/symlinked/corrupt file
    simply yields ``None`` so the caller rejects the carry (fail closed).
    """
    path = Path(root) / STATE_DIRNAME / V08_MATTER_FILENAME
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, Mapping):
        return None
    for key in ("matter_objects", "objects"):
        objects = doc.get(key)
        if isinstance(objects, Mapping):
            row = objects.get(item_id)
            if isinstance(row, Mapping):
                return dict(row)
        elif isinstance(objects, list):
            for row in objects:
                if not isinstance(row, Mapping):
                    continue
                oid = row.get("object_id") or row.get("id")
                if str(oid or "") == item_id:
                    return dict(row)
    return None


def _v08_object_portability(row: Mapping[str, Any]) -> str:
    profile = row.get("matter_profile")
    for source in (row, profile if isinstance(profile, Mapping) else None):
        if not isinstance(source, Mapping):
            continue
        raw = source.get("portability")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return ""


def _v08_object_hidden_from_subject(row: Mapping[str, Any]) -> bool:
    """Whether the subject has no legitimate known spot for this v08 object."""
    if row.get("not_at_believed_spot") is True:
        return True
    snapshot = row.get("subject_last_seen_snapshot")
    if isinstance(snapshot, Mapping):
        snap_parent = snapshot.get("parent_id")
        if not snap_parent or str(snap_parent) == V08_SUBJECT_UNKNOWN_PARENT:
            return True
        return False
    believed_parent = row.get("subject_last_seen_parent")
    if not believed_parent or str(believed_parent) == V08_SUBJECT_UNKNOWN_PARENT:
        return True
    return False


def _legs_walk_seconds(legs: list[dict[str, Any]], start_index: int = 0) -> int:
    return sum(int(l["duration_seconds"]) for l in legs[start_index:])


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
            # Ch.26 §四: standing at the entrance now makes the secondhand claim firsthand.
            _record_firsthand_rumor_check(
                state, route_id, verified_blocked=True, at_iso=format_iso(cursor_dt)
            )
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
        # Ch.26 §四: reaching the open entrance refutes the shopkeeper's hearsay.
        if _record_firsthand_rumor_check(
            state, route_id, verified_blocked=False, at_iso=format_iso(cursor_dt)
        ):
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
    season = _resolve_effective_season(env, now_dt)

    cloths_advanced = 0
    for item in state["drying_items"].values():
        if _settle_single_cloth_in_place(item, weather=weather, airflow=airflow, now_dt=now_dt):
            cloths_advanced += 1

    plant_advanced = _settle_plant_in_place(
        state["potted_plant"], weather=weather, now_dt=now_dt, season=season
    )
    journey_advanced = _settle_journey_in_place(state, now_dt)
    pub_counts = _settle_bookstore_and_notices_in_place(state, now_dt)
    shopkeeper_counts = _settle_shopkeeper_life_in_place(state, now_dt)
    cafe_phase_rotated = _settle_cafe_resident_in_place(state, now_dt)

    any_changed = (
        cloths_advanced > 0
        or plant_advanced
        or journey_advanced
        or pub_counts["expired_reservations"] > 0
        or pub_counts["expired_notices"] > 0
        or shopkeeper_counts["task_rotated"] > 0
        or shopkeeper_counts["card_spots_decided"] > 0
        or cafe_phase_rotated > 0
    )
    return {
        "changed": any_changed,
        "cloths_advanced": cloths_advanced,
        "plant_advanced": plant_advanced,
        "journey_advanced": journey_advanced,
        "expired_reservations": pub_counts["expired_reservations"],
        "expired_notices": pub_counts["expired_notices"],
        "shopkeeper_task_rotated": shopkeeper_counts["task_rotated"],
        "card_spots_decided": shopkeeper_counts["card_spots_decided"],
        "cafe_phase_rotated": cafe_phase_rotated,
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
        + int(summary.get("card_spots_decided") or 0)
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
    journey_after = state.get("journey") if isinstance(state.get("journey"), Mapping) else {}
    if summary.get("journey_advanced") and str(journey_after.get("state") or "") == "arrived":
        dest = str(journey_after.get("current_place_id") or "street_corner")
        arr_ts = str(journey_after.get("arrived_at") or now_str)
        env_w = str((state.get("environment") or {}).get("weather") or "clear")
        is_rain = env_w in ("rain", "heavy_rain", "storm", "drizzle")
        dest_label = _place_short_name(dest)
        _emit_v04_lived_episode(
            root,
            episode_key=f"v04_outing:arrived:{dest}:{arr_ts}",
            topic=f"{'雨中' if is_rain else ''}步行抵达{dest_label}",
            exact_detail=f"在{env_w}天气里一路步行到了{dest_label}（抵达时间 {arr_ts}）",
            gist_summary=f"{'冒雨' if is_rain else '出门'}走到了{dest_label}",
            occurred_at=arr_ts,
            emotional_salience=0.58 if is_rain else 0.48,
            affect_kind="mood_shift" if is_rain else "affect_pulse",
            affect_intensity=46 if is_rain else 38,
            shared=True,
            source_refs=(("encounter_id", f"v04_arrived_{dest}_{arr_ts}"), ("place_id", dest)),
        )
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


def _draw_autonomous_weather(
    *,
    world_id: str,
    slot_key: str,
    effective_season: str,
    solar_term_id: str,
    prev_weather: str,
) -> tuple[str, str]:
    """Deterministic SHA-256 draw for 6-hour autonomous weather & airflow."""
    seed = f"{world_id}|{slot_key}|{effective_season}|{solar_term_id}|{prev_weather}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    roll_w = int.from_bytes(digest[:4], "big") % 100
    roll_a = int.from_bytes(digest[4:8], "big") % 100

    # Seasonal realism weights: (clear, drizzle, rain) summing to 100.
    if effective_season == "spring":
        if solar_term_id in ("qingming", "guyu"):
            w_clear, w_drizzle = 45, 32
        else:
            w_clear, w_drizzle = 55, 27
    elif effective_season == "summer":
        if solar_term_id in ("mangzhong", "xiazhi"):
            w_clear, w_drizzle = 40, 25
        else:
            w_clear, w_drizzle = 58, 17
    elif effective_season == "winter":
        w_clear, w_drizzle = 70, 18
    else:  # autumn
        w_clear, w_drizzle = 66, 20

    # Mild weather continuity: rain tapers toward drizzle rather than strobing abruptly.
    if prev_weather == "rain":
        w_clear = max(25, w_clear - 8)
        w_drizzle = min(50, w_drizzle + 8)
    elif prev_weather == "drizzle":
        w_clear = max(30, w_clear - 4)

    if roll_w < w_clear:
        weather = "clear"
    elif roll_w < w_clear + w_drizzle:
        weather = "drizzle"
    else:
        weather = "rain"

    if weather == "rain":
        a_weak, a_med = 15, 45
    elif weather == "drizzle":
        a_weak, a_med = 25, 55
    elif effective_season in ("winter", "autumn"):
        a_weak, a_med = 25, 50
    else:
        a_weak, a_med = 30, 52

    if roll_a < a_weak:
        airflow = "weak"
    elif roll_a < a_weak + a_med:
        airflow = "medium"
    else:
        airflow = "strong"
    return weather, airflow


def _parse_optional_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_iso_moment(value, field_name="manual_pin_until")
    except V04NeighborhoodError:
        return None


def _resolve_world_id_zero_write(paths: _instance_paths.InstancePaths) -> str:
    loc_info = _instance_paths.read_canonical_world_location_zero_write(paths.day_root)
    wid = str(loc_info.get("world_id") or "").strip()
    if wid:
        return wid
    if paths.instance_json is not None and paths.instance_json.is_file() and not paths.instance_json.is_symlink():
        try:
            doc = json.loads(paths.instance_json.read_text(encoding="utf-8"))
            if isinstance(doc, Mapping):
                wid = str(doc.get("world_id") or "").strip()
                if wid:
                    return wid
        except (OSError, ValueError):
            pass
    return "shiomi_city"


def advance_autonomous_weather_pulse(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Advance deterministic 6-hour autonomous weather pulse (S0..S3, zero-LLM).

    Zero-write when ``v04_neighborhood_state.json`` is absent, when the current
    6-hour slot was already settled (and ``force=False``), or while an active
    ``manual_pin_until`` pin holds.
    """
    raw_root = Path(day_root)
    if raw_root.exists() and raw_root.is_symlink():
        raise V04NeighborhoodError(
            "STATE_SYMLINK_REFUSED", "day_root must not be a symlink", http_status=503
        )
    paths = _instance_paths.resolve_instance_paths(raw_root)
    canonical_root = paths.day_root
    local_dt = resolve_now_readonly(canonical_root, now_iso)
    now_str = format_iso(local_dt)
    slot_key = _weather_slot_key(local_dt)

    state, exists = load_state_readonly(canonical_root)
    if not exists:
        return {
            "ok": True,
            "changed": False,
            "slot_key": slot_key,
            "reason": "state_missing",
            "llm_calls": 0,
            "model_called": False,
        }

    env = state["environment"]
    prev_weather = str(env.get("weather") or "clear")
    prev_airflow = str(env.get("airflow") or "medium")
    effective_season = _resolve_effective_season(env, local_dt)

    pin_dt = _parse_optional_iso(env.get("manual_pin_until"))
    if pin_dt is not None and pin_dt > local_dt:
        return {
            "ok": True,
            "changed": False,
            "slot_key": slot_key,
            "weather": prev_weather,
            "airflow": prev_airflow,
            "season": effective_season,
            "manual_pin_until": env.get("manual_pin_until"),
            "reason": "manual_pin_active",
            "llm_calls": 0,
            "model_called": False,
        }

    if not force and env.get("weather_slot_key") == slot_key:
        return {
            "ok": True,
            "changed": False,
            "slot_key": slot_key,
            "weather": prev_weather,
            "airflow": prev_airflow,
            "season": effective_season,
            "reason": "same_slot",
            "llm_calls": 0,
            "model_called": False,
        }

    with state_file_lock(state_file_path(canonical_root)):
        state, exists = load_state_readonly(canonical_root)
        if not exists:
            return {
                "ok": True,
                "changed": False,
                "slot_key": slot_key,
                "reason": "state_missing",
                "llm_calls": 0,
                "model_called": False,
            }
        env = state["environment"]
        prev_weather = str(env.get("weather") or "clear")
        prev_airflow = str(env.get("airflow") or "medium")
        explicit_season = extract_explicit_season(env)
        pulse = _season_solar_pulse.project_season_solar_pulse(
            now_iso=local_dt, explicit_season=explicit_season
        )
        effective_season = str(pulse["season"])
        solar_term_id = str(pulse["solar_term"]["term_id"])

        pin_dt = _parse_optional_iso(env.get("manual_pin_until"))
        if pin_dt is not None and pin_dt > local_dt:
            return {
                "ok": True,
                "changed": False,
                "slot_key": slot_key,
                "weather": prev_weather,
                "airflow": prev_airflow,
                "season": effective_season,
                "manual_pin_until": env.get("manual_pin_until"),
                "reason": "manual_pin_active",
                "llm_calls": 0,
                "model_called": False,
            }
        if not force and env.get("weather_slot_key") == slot_key:
            return {
                "ok": True,
                "changed": False,
                "slot_key": slot_key,
                "weather": prev_weather,
                "airflow": prev_airflow,
                "season": effective_season,
                "reason": "same_slot",
                "llm_calls": 0,
                "model_called": False,
            }

        world_id = _resolve_world_id_zero_write(paths)
        new_weather, new_airflow = _draw_autonomous_weather(
            world_id=world_id,
            slot_key=slot_key,
            effective_season=effective_season,
            solar_term_id=solar_term_id,
            prev_weather=prev_weather,
        )

        if new_weather != prev_weather or new_airflow != prev_airflow:
            for item in state["drying_items"].values():
                _cut_cloth_segment_for_regime_change(
                    item,
                    old_weather=prev_weather,
                    old_airflow=prev_airflow,
                    new_weather=new_weather,
                    new_airflow=new_airflow,
                    new_support_id=item["support_id"],
                    now_dt=local_dt,
                )
            _reanchor_plant_segment(
                state["potted_plant"],
                old_weather=prev_weather,
                now_dt=local_dt,
                season=effective_season,
            )

        env["weather"] = new_weather
        env["airflow"] = new_airflow
        env["weather_slot_key"] = slot_key
        if pin_dt is not None and pin_dt <= local_dt:
            env["manual_pin_until"] = None
        if explicit_season is None:
            env["season"] = None
            env["season_explicit"] = False
        env["updated_at"] = now_str
        state["potted_plant"]["seasonal_state"] = _plant_seasonal_state(
            effective_season, float(state["potted_plant"].get("hydration", 70.0))
        )
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str
        save_state_atomic(canonical_root, state)

    return {
        "ok": True,
        "changed": True,
        "weather_changed": bool(new_weather != prev_weather or new_airflow != prev_airflow),
        "slot_key": slot_key,
        "weather": new_weather,
        "airflow": new_airflow,
        "previous_weather": prev_weather,
        "previous_airflow": prev_airflow,
        "season": effective_season,
        "solar_term_id": solar_term_id,
        "revision": int(state["revision"]),
        "updated_at": now_str,
        "llm_calls": 0,
        "model_called": False,
    }


# ---------------------------------------------------------------------------
# Pure Projection (100% Zero Disk Writes, Perspective Redaction)
# ---------------------------------------------------------------------------


def _project_shopkeeper_life(life: Mapping[str, Any]) -> dict[str, Any]:
    """Project the public-counter shopkeeper view with private drafts strictly withheld."""
    suggestions: list[dict[str, Any]] = []
    for row in life.get("card_spot_suggestions") or []:
        if not isinstance(row, Mapping):
            continue
        suggestions.append({
            "suggestion_id": row.get("suggestion_id"),
            "card_id": row.get("card_id"),
            "spot_note": row.get("spot_note"),
            "suggested_at": row.get("suggested_at"),
            "status": row.get("status"),
            "outcome_reason_cn": row.get("outcome_reason_cn") or "",
            "resolved_at": row.get("resolved_at"),
        })
    rumors: list[dict[str, Any]] = []
    for row in life.get("heard_rumors") or []:
        if not isinstance(row, Mapping):
            continue
        vf = bool(row.get("verified_firsthand"))
        ep_status = str(row.get("epistemic_status") or "").strip()
        if not ep_status:
            if vf and row.get("refutation_note_cn"):
                ep_status = "refuted_firsthand"
            elif vf:
                ep_status = "verified_firsthand"
            else:
                ep_status = "hearsay_unverified"
        rumors.append({
            "rumor_id": row.get("rumor_id"),
            "source_resident_id": SHOPKEEPER_ID,
            "source_name": row.get("source_name"),
            "route_id": row.get("route_id"),
            "claim_cn": row.get("claim_cn"),
            "heard_at": row.get("heard_at"),
            "epistemic_status": ep_status,
            "verified_firsthand": vf,
            "refutation_note_cn": row.get("refutation_note_cn") or "",
            "verification_note_cn": row.get("verification_note_cn") or "",
        })
    memories = [
        copy.deepcopy(dict(r))
        for r in (life.get("remembered_interactions") or [])
        if isinstance(r, Mapping)
    ]
    return {
        "resident_id": SHOPKEEPER_ID,
        "resident_name": SHOPKEEPER_NAME,
        "shopkeeper_name": SHOPKEEPER_NAME,
        "role": SHOPKEEPER_ROLE,
        "home_venue": "bookstore",
        "venue_name_cn": "街角旧书店",
        "open_window": "10:00-19:00",
        "schedule_rotation_hours": SHOPKEEPER_TASK_ROTATION_HOURS,
        "schedule_offset_hours": 0,
        "current_phase": life.get("current_task"),
        "current_phase_cn": life.get("current_task_cn"),
        "current_phase_started_at": life.get("current_task_started_at"),
        "current_task": life.get("current_task"),
        "current_task_cn": life.get("current_task_cn"),
        "current_task_started_at": life.get("current_task_started_at"),
        "ambient_changes": [
            str(x) for x in (life.get("ambient_changes") or [])
        ][-MAX_SHOPKEEPER_AMBIENT_CHANGES:],
        "privilege_boundary": SHOPKEEPER_PRIVILEGE_BOUNDARY,
        "private_room_knowledge": False,
        "warehouse_access": False,
        "inventory_rewrite_authority": False,
        "private_drafts_exposed": False,
        "favorability_score": None,
        "omission_debt": 0,
        "remembered_interactions": memories[-MAX_SHOPKEEPER_MEMORIES:],
        "card_spot_suggestions": suggestions[-MAX_SHOPKEEPER_MEMORIES:],
        "heard_rumors": rumors[-MAX_SHOPKEEPER_MEMORIES:],
    }


def _project_cafe_resident_life(
    life: Mapping[str, Any],
    *,
    weather: str,
    season: str,
    festival_info: Mapping[str, Any],
    now_dt: datetime,
) -> dict[str, Any]:
    """Project second independent resident (Saeki Aoi) under public_counter_only laws."""
    phase_key, phase_cn, start_dt, at_counter = _cafe_phase_for(now_dt)
    start_iso = format_iso(start_dt)
    env_notes = _cafe_environment_notes(weather, season, festival_info)
    rumors: list[dict[str, Any]] = []
    for row in life.get("heard_rumors") or []:
        if not isinstance(row, Mapping):
            continue
        vf = bool(row.get("verified_firsthand"))
        ep_status = str(row.get("epistemic_status") or "").strip()
        if not ep_status:
            if vf and row.get("refutation_note_cn"):
                ep_status = "refuted_firsthand"
            elif vf:
                ep_status = "verified_firsthand"
            else:
                ep_status = "hearsay_unverified"
        rumors.append({
            "rumor_id": row.get("rumor_id"),
            "source_resident_id": CAFE_RESIDENT_ID,
            "source_name": row.get("source_name") or CAFE_RESIDENT_NAME,
            "route_id": row.get("route_id"),
            "claim_cn": row.get("claim_cn"),
            "heard_at": row.get("heard_at"),
            "epistemic_status": ep_status,
            "verified_firsthand": vf,
            "refutation_note_cn": row.get("refutation_note_cn") or "",
            "verification_note_cn": row.get("verification_note_cn") or "",
        })
    coaster_notes = [
        copy.deepcopy(dict(r))
        for r in (life.get("coaster_notes") or [])
        if isinstance(r, Mapping)
    ][-MAX_SHOPKEEPER_MEMORIES:]
    errand_board = [
        copy.deepcopy(dict(r))
        for r in (life.get("shared_errand_board") or [])
        if isinstance(r, Mapping)
    ][-MAX_SHOPKEEPER_MEMORIES:]
    memories = [
        copy.deepcopy(dict(r))
        for r in (life.get("remembered_interactions") or [])
        if isinstance(r, Mapping)
    ][-MAX_SHOPKEEPER_MEMORIES:]
    return {
        "resident_id": CAFE_RESIDENT_ID,
        "resident_name": CAFE_RESIDENT_NAME,
        "shopkeeper_name": CAFE_RESIDENT_NAME,
        "role": CAFE_RESIDENT_ROLE,
        "home_venue": CAFE_HOME_VENUE,
        "venue_name_cn": CAFE_VENUE_NAME_CN,
        "open_window": CAFE_OPEN_WINDOW,
        "is_open_now": _is_cafe_open(now_dt),
        "at_counter": bool(at_counter),
        "schedule_rotation_hours": CAFE_TASK_ROTATION_HOURS,
        "schedule_offset_hours": CAFE_SCHEDULE_OFFSET_HOURS,
        "current_phase": phase_key,
        "current_phase_cn": phase_cn,
        "current_phase_started_at": start_iso,
        "current_task": phase_key,
        "current_task_cn": phase_cn,
        "current_task_started_at": start_iso,
        "weather_reaction_cn": env_notes["weather_reaction_cn"],
        "season_reaction_cn": env_notes["season_reaction_cn"],
        "market_reaction_cn": env_notes["market_reaction_cn"],
        "environment_reaction_cn": env_notes["environment_reaction_cn"],
        "privilege_boundary": SHOPKEEPER_PRIVILEGE_BOUNDARY,
        "private_room_knowledge": False,
        "warehouse_access": False,
        "inventory_rewrite_authority": False,
        "private_drafts_exposed": False,
        "favorability_score": None,
        "omission_debt": 0,
        "coaster_notes": coaster_notes,
        "shared_errand_board": errand_board,
        "remembered_interactions": memories,
        "heard_rumors": rumors[-MAX_SHOPKEEPER_MEMORIES:],
    }


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
    raw_root = Path(day_root)
    if raw_root.exists() and raw_root.is_symlink():
        raise V04NeighborhoodError(
            "STATE_SYMLINK_REFUSED", "day_root must not be a symlink", http_status=503
        )
    paths = _instance_paths.resolve_instance_paths(raw_root)
    root = paths.day_root
    now_dt = resolve_now_readonly(root, now_iso)
    now_str = format_iso(now_dt)
    identity = read_character_identity_readonly(root)

    if preloaded_state is None:
        raw_state, state_exists = load_state_readonly(root)
    else:
        raw_state = copy.deepcopy(dict(preloaded_state))
        state_exists = True

    working = copy.deepcopy(raw_state)
    env_raw = working["environment"]
    # Ch.26 & Ch.42: normalize living residents inside the in-memory copy only.
    _ensure_shopkeeper_life(working["public_life"]["bookstore"])
    _ensure_cafe_resident_life(working["public_life"])
    settle_all_processes_in_place(working, now_dt)

    # Canonical world -> v04 location projection (in-memory only, zero-write) when no
    # v04-internal journey has moved or is currently in transit.
    j_work = working.get("journey")
    if (
        isinstance(j_work, dict)
        and j_work.get("state") == "at_place"
        and j_work.get("arrived_at") is None
        and j_work.get("started_at") is None
    ):
        world_loc = _instance_paths.read_canonical_world_location_zero_write(root)
        if world_loc.get("found") and world_loc.get("v04_place_id") in working["places"]:
            j_work["current_place_id"] = str(world_loc["v04_place_id"])

    perspective = "her_private" if has_subject_capability else "partner_default"
    weather = str(env_raw.get("weather") or "clear")
    airflow = str(env_raw.get("airflow") or "medium")
    season = _resolve_effective_season(env_raw, now_dt)
    bookstore_open = _is_bookstore_open(now_dt)
    season_ecology = _derive_season_ecology(season, weather, airflow, now_dt)
    festival_info = _derive_seasonal_festival(working, now_dt)

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
        "season_ecology": season_ecology,
        "festival_traces": {
            "phase": festival_info["phase"],
            "festival_name_cn": festival_info.get("festival_name_cn"),
            "residual_traces": list(festival_info.get("residual_traces") or []),
        },
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
    active_route_id = j_raw.get("active_route_id")
    # Honest shelter cue is only meaningful while actually walking an active leg.
    active_route_sheltered = bool(
        j_raw.get("state") == "in_transit"
        and active_route_id
        and _route_is_sheltered(active_route_id)
    )

    journey_out: dict[str, Any] = {
        "state": j_raw["state"],
        "current_place_id": j_raw.get("current_place_id"),
        "origin_place_id": j_raw.get("origin_place_id"),
        "target_place_id": j_raw.get("target_place_id"),
        "active_route_id": active_route_id,
        "active_route_sheltered": active_route_sheltered,
        "transit_location_cn": _journey_location_cn(j_raw),
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
    plant_hydration = round(float(plant_raw.get("hydration", 70.0)), 4)
    plant_growth = round(float(plant_raw.get("growth_points", 0.0)), 4)
    stored_leaf_count = plant_raw.get("leaf_count")
    if isinstance(stored_leaf_count, int) and not isinstance(stored_leaf_count, bool):
        plant_leaf_count = int(stored_leaf_count)
    else:
        plant_leaf_count = _plant_leaf_count_for_growth(plant_growth)
    plant_out: dict[str, Any] = {
        "plant_id": plant_raw["plant_id"],
        "name": plant_raw["name"],
        "place_id": plant_raw["place_id"],
        "hydration": plant_hydration,
        "growth_points": plant_growth,
        "stage": plant_raw["stage"],
        "public_care_enabled": bool(plant_raw.get("public_care_enabled")),
        "never_dies_offline": True,
        "last_watered_at": plant_raw.get("last_watered_at"),
        "last_anchor_at": plant_raw.get("last_anchor_at"),
        # Ch.41 §五: honest, derived plant milestones (never written by a plain read).
        "leaf_count": plant_leaf_count,
        "seasonal_state": _plant_seasonal_state(season, plant_hydration),
        "new_leaf_note_cn": plant_raw.get("new_leaf_note_cn") or "",
        "last_new_leaf_at": plant_raw.get("last_new_leaf_at"),
        "wilt_recovered_count": int(plant_raw.get("wilt_recovered_count") or 0),
        "recovery_note_cn": plant_raw.get("recovery_note_cn") or "",
        "public_care_note_cn": plant_raw.get("public_care_note_cn") or "",
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
        "shopkeeper_name": SHOPKEEPER_NAME,
        "open_window": "10:00-19:00",
        "is_open_now": bookstore_open,
        "return_box_available": True,
        "books": books_list,
        "books_by_id": books_by_id,
        "display_cards": copy.deepcopy(b_raw.get("display_cards") or []),
        "shopkeeper_life": _project_shopkeeper_life(b_raw.get("shopkeeper_life") or {}),
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

    living_shopkeeper_out = copy.deepcopy(bookstore_out["shopkeeper_life"])
    second_resident_out = _project_cafe_resident_life(
        pl_raw.get("cafe_resident") or {},
        weather=weather,
        season=season,
        festival_info=festival_info,
        now_dt=now_dt,
    )
    independent_residents_out = [
        copy.deepcopy(living_shopkeeper_out),
        copy.deepcopy(second_resident_out),
    ]
    if has_subject_capability:
        concern_tinted_shelf_out = project_concern_tinted_shelf_zero_write(
            root,
            now_iso=now_str,
            preloaded_state=working,
            auto_discover_concerns=True,
        )
    else:
        concern_tinted_shelf_out = _project_partner_scrubbed_shelf_zero_write(working)

    bookstore_out["concern_tinted_shelf"] = copy.deepcopy(concern_tinted_shelf_out)

    public_life_out: dict[str, Any] = {
        "bookstore": bookstore_out,
        "shared_shelf": shared_shelf_out,
        "public_notices": notices_out,
        "roadblocks": roadblocks_out,
        "place_booklet": booklet_out,
        "place_booklets": [copy.deepcopy(booklet_out)],
        "seasonal_festival": copy.deepcopy(festival_info),
        "living_shopkeeper": copy.deepcopy(living_shopkeeper_out),
        "second_resident": copy.deepcopy(second_resident_out),
        "independent_residents": copy.deepcopy(independent_residents_out),
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
        "concern_tinted_shelf": concern_tinted_shelf_out,
        "living_shopkeeper": living_shopkeeper_out,
        "second_resident": second_resident_out,
        "independent_residents": independent_residents_out,
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
    old_season = _resolve_effective_season(env, now_dt)

    new_airflow = old_airflow
    if payload.get("airflow") is not None:
        new_airflow = _clean_text(payload.get("airflow"), field_name="airflow", max_len=24)
        if new_airflow not in AIRFLOW_KINDS:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"airflow must be one of {AIRFLOW_KINDS}", http_status=400
            )

    explicit_season_requested = payload.get("season") is not None
    if explicit_season_requested:
        new_season = _clean_text(payload.get("season"), field_name="season", max_len=24)
        if new_season not in SEASON_KINDS:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"season must be one of {SEASON_KINDS}", http_status=400
            )
        effective_season = new_season
    else:
        existing_explicit = extract_explicit_season(env)
        new_season = existing_explicit
        effective_season = existing_explicit or _season_solar_pulse.resolve_effective_season(
            now_dt
        )[0]

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
    _reanchor_plant_segment(
        state["potted_plant"], old_weather=old_weather, now_dt=now_dt, season=old_season
    )

    now_str = format_iso(now_dt)
    pin_until_dt = now_dt + timedelta(hours=6)
    if payload.get("manual_pin_until") is not None:
        pin_until_dt = parse_iso_moment(
            payload.get("manual_pin_until"), field_name="manual_pin_until"
        )
    env["weather"] = weather
    env["airflow"] = new_airflow
    env["season"] = new_season
    env["season_explicit"] = bool(new_season is not None)
    env["weather_slot_key"] = _weather_slot_key(now_dt)
    env["manual_pin_until"] = format_iso(pin_until_dt)
    env["updated_at"] = now_str
    state["potted_plant"]["seasonal_state"] = _plant_seasonal_state(
        effective_season, float(state["potted_plant"].get("hydration", 70.0))
    )
    return {
        "weather": weather,
        "airflow": new_airflow,
        "season": effective_season,
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
    season = _resolve_effective_season(state["environment"], now_dt)
    _reanchor_plant_segment(plant, old_weather=weather, now_dt=now_dt, season=season)
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
        was_wilting = (
            plant.get("stage") == "wilting_recoverable"
            or float(plant.get("hydration", 0.0)) <= 10.0
        )
        new_h = min(100.0, float(plant["hydration"]) + float(amt_raw))
        plant["hydration"] = round(new_h, 4)
        plant["segment_start_hydration"] = plant["hydration"]
        plant["last_watered_at"] = now_str
        plant["stage"] = _derive_plant_stage(plant["hydration"], float(plant["growth_points"]))
        if was_wilting and plant["hydration"] > 10.0:
            plant["wilt_recovered_count"] = int(plant.get("wilt_recovered_count") or 0) + 1
            plant["recovery_note_cn"] = "浇水后垂下去的叶片慢慢舒展回来了"
        plant["seasonal_state"] = _plant_seasonal_state(
            season, float(plant["hydration"])
        )
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


def _validate_carriable_item(
    state: Mapping[str, Any], item_id: str, *, root: Optional[Path] = None
) -> None:
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
    # Cross-domain portable matter (Ch.39 §六): recognise an active v08 object
    # strictly read-only, without ever writing the matter owner's file.
    if root is not None:
        row = _read_v08_matter_object_readonly(Path(root), item_id)
        if row is not None:
            if row.get("consumed") or row.get("discarded") or row.get("returned_out"):
                raise V04NeighborhoodError(
                    "ITEM_NOT_CARRIABLE",
                    f"Item {item_id!r} is no longer physically present",
                    http_status=409,
                )
            if _v08_object_portability(row) == "fixed":
                raise V04NeighborhoodError(
                    "ITEM_NOT_CARRIABLE",
                    f"Item {item_id!r} is fixed in place and cannot be carried",
                    http_status=409,
                )
            # Carrying is a subject capability: Two-Worlds safety forbids
            # picking up matter the subject has never legitimately seen.
            if _v08_object_hidden_from_subject(row):
                raise V04NeighborhoodError(
                    "ITEM_NOT_CARRIABLE",
                    f"Item {item_id!r} is not at a spot the subject knows",
                    http_status=409,
                )
            return
    raise V04NeighborhoodError(
        "ITEM_NOT_CARRIABLE", f"Item {item_id!r} does not exist or is not in subject custody", http_status=409
    )


def _handle_start_journey(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_dt: datetime,
    root: Optional[Path] = None,
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

    # Standing at the street corner while a market is active makes the visit firsthand.
    _record_festival_attendance_if_active(state, now_dt)

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
            _validate_carriable_item(state, cid, root=root)
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
        _record_firsthand_rumor_check(
            state, first_route, verified_blocked=True, at_iso=now_str
        )
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
    # Standing at the open entrance refutes the shopkeeper's hearsay firsthand.
    _record_firsthand_rumor_check(
        state, first_route, verified_blocked=False, at_iso=now_str
    )

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
    if op not in ("advance", "turn_back", "pause", "resume", "reroute"):
        raise V04NeighborhoodError(
            "INVALID_FIELDS",
            "operation must be one of ('advance', 'turn_back', 'pause', 'resume', 'reroute')",
            http_status=400,
        )

    now_str = format_iso(now_dt)

    # Arriving at street_corner while a market is active is a firsthand visit; a
    # later arrival only sees the leftover physical traces and records nothing.
    _record_festival_attendance_if_active(state, now_dt)

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

    if op == "reroute":
        state_label = journey.get("state")
        if state_label not in ("in_transit", "paused_at_node"):
            raise V04NeighborhoodError(
                "NOT_IN_TRANSIT",
                "reroute requires journey to be in_transit or paused_at_node",
                http_status=409,
            )
        target = journey.get("target_place_id")
        if not target:
            raise V04NeighborhoodError(
                "NO_TARGET", "reroute requires an active journey target", http_status=409
            )

        legs = list(journey.get("legs") or [])
        idx = int(journey.get("active_leg_index") or 0)
        walked_now = 0.0
        if state_label == "in_transit":
            if idx < len(legs) and journey.get("segment_started_at"):
                seg_dt = parse_iso_moment(
                    journey["segment_started_at"], field_name="segment_started_at"
                )
                walked_now = max(
                    0.0,
                    min(float(legs[idx]["duration_seconds"]), (now_dt - seg_dt).total_seconds()),
                )
            anchor = (
                str(legs[idx]["from_place"])
                if idx < len(legs)
                else str(journey.get("current_place_id") or journey.get("origin_place_id") or "home")
            )
        elif journey.get("current_place_id"):
            anchor = str(journey["current_place_id"])
        else:
            walked_now = float(journey.get("paused_walked_seconds") or 0.0)
            anchor = (
                str(legs[idx]["from_place"])
                if idx < len(legs)
                else str(journey.get("origin_place_id") or "home")
            )

        # Body effort already spent is never rolled back: bank any partial walk
        # into the accumulated total before rebuilding the remaining legs.
        if walked_now > 0:
            journey["accumulated_walk_seconds"] = round(
                float(journey.get("accumulated_walk_seconds") or 0.0) + walked_now, 3
            )
        journey["paused_walked_seconds"] = 0.0

        raw_pref = payload.get("preferred_branch")
        branch: Optional[str] = None
        if raw_pref is not None:
            branch = _clean_text(raw_pref, field_name="preferred_branch", max_len=48)
            if branch not in ("open_shortcut", "eaves_walkway"):
                raise V04NeighborhoodError(
                    "INVALID_FIELDS",
                    "preferred_branch must be 'open_shortcut' or 'eaves_walkway'",
                    http_status=400,
                )

        discovered = list(journey.get("discovered_roadblocks") or [])
        blocked = str(journey.get("blocked_route_id") or "")
        if branch is None:
            if blocked == "open_shortcut":
                branch = "eaves_walkway"
            elif blocked == "eaves_walkway":
                branch = "open_shortcut"
            elif "open_shortcut" in discovered:
                branch = "eaves_walkway"
            else:
                branch = "eaves_walkway"

        if anchor == target:
            journey["state"] = "arrived"
            journey["current_place_id"] = anchor
            journey["active_route_id"] = None
            journey["active_leg_index"] = 0
            journey["legs"] = []
            journey["paused_walked_seconds"] = 0.0
            journey["halt_reason"] = None
            journey["blocked_route_id"] = None
            journey["arrived_at"] = now_str
            _mark_place_visited(state, anchor, now_str)
            return {
                "operation": "reroute",
                "outcome": "already_at_target",
                "state": "arrived",
                "current_place_id": anchor,
                "accumulated_walk_seconds": journey["accumulated_walk_seconds"],
            }

        new_legs = _build_route_legs(anchor, target, branch_route_id=branch)
        first_route = new_legs[0]["route_id"] if new_legs else None

        # Standing at the junction, a real closure on the chosen entrance is
        # directly observable. Discover it honestly and stay put -- never loop.
        if first_route is not None and _is_route_really_blocked(state, first_route):
            _add_discovered_roadblock(journey, first_route)
            journey["state"] = "paused_at_node"
            journey["current_place_id"] = anchor
            journey["active_route_id"] = None
            journey["active_leg_index"] = 0
            journey["legs"] = new_legs
            journey["paused_walked_seconds"] = 0.0
            journey["halt_reason"] = "ROADBLOCK_ENCOUNTERED"
            journey["blocked_route_id"] = first_route
            _mark_place_visited(state, anchor, now_str)
            return {
                "operation": "reroute",
                "outcome": "halted_by_roadblock_at_entrance",
                "state": "paused_at_node",
                "at_place_id": anchor,
                "encountered_roadblock": first_route,
                "discovered_roadblocks": list(journey.get("discovered_roadblocks") or []),
                "accumulated_walk_seconds": journey["accumulated_walk_seconds"],
            }

        total_sec = _legs_walk_seconds(new_legs)
        journey["state"] = "in_transit"
        journey["current_place_id"] = None
        journey["target_place_id"] = target
        journey["active_route_id"] = first_route
        journey["active_leg_index"] = 0
        journey["legs"] = new_legs
        journey["is_turning_back"] = False
        journey["turned_back_at"] = None
        journey["segment_started_at"] = now_str
        journey["expected_arrival_at"] = format_iso(now_dt + timedelta(seconds=total_sec))
        journey["arrived_at"] = None
        journey["halt_reason"] = None
        journey["blocked_route_id"] = None
        return {
            "operation": "reroute",
            "outcome": "rerouted",
            "state": "in_transit",
            "target_place_id": target,
            "active_route_id": first_route,
            "preferred_branch": branch,
            "planned_route_ids": [l["route_id"] for l in new_legs],
            "expected_arrival_at": journey["expected_arrival_at"],
            "accumulated_walk_seconds": journey["accumulated_walk_seconds"],
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


_CONCERN_SEGMENT_SPLIT_RE = re.compile(r"[^0-9A-Za-z一-鿿]+")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def _book_concern_corpus(book: Mapping[str, Any]) -> str:
    """Flatten the browseable non-full-text fields of one book into one haystack."""
    parts: list[str] = [
        str(book.get("title") or ""),
        str(book.get("cover_summary") or ""),
    ]
    toc = book.get("toc")
    if isinstance(toc, (list, tuple)):
        parts.extend(str(entry) for entry in toc)
    parts.append(str(book.get("sample_excerpt") or ""))
    return "\n".join(parts)


def _iter_concern_segments(cue: str) -> list[str]:
    return [seg for seg in _CONCERN_SEGMENT_SPLIT_RE.split(cue) if seg]


def _match_concern_cues(
    book: Mapping[str, Any],
    focus_question: str,
    concern_keywords: Sequence[str],
) -> list[str]:
    """Return the cue fragments that catch the eye for this book (never mutates the book)."""
    corpus = _book_concern_corpus(book)
    hay = corpus.casefold()
    cues: list[str] = []
    if isinstance(focus_question, str) and focus_question.strip():
        cues.append(focus_question.strip())
    for kw in concern_keywords or ():
        if isinstance(kw, str) and kw.strip():
            cues.append(kw.strip())

    matched: list[str] = []
    for cue in cues:
        for seg in _iter_concern_segments(cue):
            seg_fold = seg.casefold()
            if seg_fold in hay:
                matched.append(seg)
                continue
            # Chinese cues are compound words; a 2-character landmark is enough to
            # let the eye catch the shelf without pretending the book was understood.
            if len(seg) >= 2 and _CJK_RUN_RE.fullmatch(seg):
                for i in range(len(seg) - 1):
                    gram = seg[i : i + 2]
                    if gram in corpus:
                        matched.append(gram)
                        break

    deduped: list[str] = []
    for item in matched:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _concern_noticed_angle(
    book: Mapping[str, Any],
    focus_question: str,
    matched_cues: Sequence[str],
) -> str:
    """Explain, in-world, why the gaze stopped on this spine (not what the whole book says)."""
    title = str(book.get("title") or "")
    question = focus_question.strip() if isinstance(focus_question, str) else ""
    if matched_cues:
        cue_text = "、".join(list(matched_cues)[:3])
        if question:
            return (
                f"刚才眼角停在这本《{title}》上：书里“{cue_text}”这一角，"
                f"正好接上了你心里惦记的“{question}”。"
            )
        return f"刚才眼角停在这本《{title}》上：书里“{cue_text}”这一角和你此刻惦记的事对上了。"
    if question:
        return f"你翻着《{title}》，心里惦记的“{question}”暂时没在封面、目录和试读里落定。"
    return f"你翻着《{title}》，暂时没有哪一角特别勾住你此刻的念头。"


def _book_in_shelf_view_range(book: Mapping[str, Any]) -> bool:
    """Whether the book currently sits inside the shelf field of view."""
    status = str(book.get("status") or "")
    if status in ("available", "reserved"):
        return True
    return (
        str(book.get("custody_place_id") or "") == "bookstore"
        and str(book.get("holder") or "") == "bookstore"
    )


def _read_optional_json_readonly(path: Path) -> Optional[Mapping[str, Any]]:
    """Read an optional JSON mapping with 100% zero disk writes (symlink-safe)."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, Mapping) else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _gather_subject_shelf_concerns_readonly(
    root: Path, state: Mapping[str, Any]
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Gather subject-side active concerns in memory with 100% zero disk writes."""
    focus_candidates: list[str] = []
    keyword_candidates: list[str] = []
    concern_rows: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    def _add_concern(text: Any, *, source: str, cid: str = "", as_focus: bool = False) -> None:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned or cleaned in seen_texts:
            return
        seen_texts.add(cleaned)
        concern_id = str(cid or _stable_id("concern", source, cleaned))
        concern_rows.append({
            "concern_id": concern_id,
            "concern_text": cleaned,
            "source": source,
        })
        if as_focus and not focus_candidates:
            focus_candidates.append(cleaned)
        else:
            keyword_candidates.append(cleaned)

    pub = state.get("public_life")
    b_store = pub.get("bookstore") if isinstance(pub, Mapping) else None
    shelf_focus = b_store.get("last_shelf_focus") if isinstance(b_store, Mapping) else None
    if isinstance(shelf_focus, Mapping):
        fq = shelf_focus.get("focus_question")
        if isinstance(fq, str) and fq.strip():
            _add_concern(fq, source="bookstore_focus", as_focus=True)
        for kw in shelf_focus.get("concern_keywords") or ():
            if isinstance(kw, str) and kw.strip():
                _add_concern(kw, source="bookstore_keyword")

    echoes_doc = _read_optional_json_readonly(root / "phone" / "v08_emergent_echoes.json")
    if isinstance(echoes_doc, Mapping):
        for row in echoes_doc.get("future_self_questions") or []:
            if not isinstance(row, Mapping) or str(row.get("status") or "") != "open":
                continue
            qid = str(row.get("question_id") or "")
            _add_concern(
                row.get("question_cn"),
                source="future_self_question",
                cid=qid,
                as_focus=True,
            )
            for kw in row.get("keywords") or ():
                if isinstance(kw, str) and kw.strip():
                    _add_concern(kw, source="future_self_keyword")
        for row in echoes_doc.get("day_directions") or []:
            if not isinstance(row, Mapping) or str(row.get("status") or "") == "faded":
                continue
            _add_concern(row.get("horizon_note_cn"), source="emergent_day_direction")

    elastic_doc = _read_optional_json_readonly(root / "phone" / "v04_elastic_play.json")
    if isinstance(elastic_doc, Mapping):
        dd = elastic_doc.get("day_direction")
        if isinstance(dd, Mapping) and str(dd.get("status") or "") not in ("idle", "cleared", "dropped"):
            _add_concern(
                dd.get("current_focus") or dd.get("direction"),
                source="elastic_day_direction",
                cid=str(dd.get("direction_id") or ""),
                as_focus=True,
            )
            _add_concern(dd.get("direction"), source="elastic_day_direction")
        for proj in elastic_doc.get("personal_projects") or []:
            if not isinstance(proj, Mapping):
                continue
            if str(proj.get("status") or "") in ("paused", "retired", "dropped"):
                continue
            _add_concern(
                proj.get("purpose_cn") or proj.get("next_step_cn"),
                source="personal_project",
                cid=str(proj.get("project_id") or ""),
            )

    resolved_paths = _instance_paths.resolve_instance_paths(root)
    agency_candidates = (
        resolved_paths.agency_depth_state_json,
        root / "state" / "v02_agency_depth_state.json",
        root / "phone" / "v02_agency_depth_state.json",
        resolved_paths.legacy_nested_agency_depth_json,
    )
    seen_agency: set[str] = set()
    for agency_path in agency_candidates:
        akey = str(agency_path)
        if akey in seen_agency:
            continue
        seen_agency.add(akey)
        agency_doc = _read_optional_json_readonly(agency_path)
        if not isinstance(agency_doc, Mapping):
            continue
        for inq in agency_doc.get("inquiries") or []:
            if not isinstance(inq, Mapping):
                continue
            if str(inq.get("status") or "").upper() not in ("OPEN", "INVESTIGATING", "ACTIVE"):
                continue
            _add_concern(
                inq.get("question") or inq.get("topic") or inq.get("title"),
                source="agency_inquiry",
                cid=str(inq.get("inquiry_id") or ""),
                as_focus=True,
            )

    cb = state.get("craft_bench")
    box = cb.get("card_organizer_box") if isinstance(cb, Mapping) else None
    if isinstance(box, Mapping) and box.get("fit_note"):
        _add_concern(box.get("fit_note"), source="craft_box_fit_note")

    primary_focus = focus_candidates[0] if focus_candidates else ""
    extra_keywords = [kw for kw in keyword_candidates if kw != primary_focus]
    return primary_focus, extra_keywords, concern_rows


def _build_public_shelf_books(
    books: Mapping[str, Any], display_cards: Sequence[Any]
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    public_cards: list[dict[str, Any]] = []
    for card in display_cards or ():
        if not isinstance(card, Mapping):
            continue
        public_cards.append({
            "card_id": str(card.get("card_id") or ""),
            "title": str(card.get("title") or ""),
            "displayed_revision": card.get("displayed_revision"),
            "displayed_text": str(card.get("displayed_text") or ""),
            "private_draft_exposed": False,
        })
    public_notes = [c["displayed_text"] for c in public_cards if c["displayed_text"]]

    in_range_ids: list[str] = []
    public_books: list[dict[str, Any]] = []
    for raw_bid, book in books.items():
        if not isinstance(book, Mapping) or not _book_in_shelf_view_range(book):
            continue
        bid = str(raw_bid)
        in_range_ids.append(bid)
        public_books.append({
            "book_id": bid,
            "title": str(book.get("title") or ""),
            "author_name": str(book.get("author_name") or ""),
            "cover_summary": str(book.get("cover_summary") or ""),
            "status": str(book.get("status") or "available"),
            "public_display_notes": list(public_notes),
            "in_range": True,
            "full_text_dumped": False,
        })
    return in_range_ids, public_books, public_cards


def _project_partner_scrubbed_shelf_zero_write(state: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-scrubbed partner shelf view: zero private concerns, zero matched cues, zero counts."""
    public_life = state.get("public_life")
    bookstore = public_life.get("bookstore") if isinstance(public_life, Mapping) else None
    books = bookstore.get("books") if isinstance(bookstore, Mapping) else None
    if not isinstance(books, Mapping):
        books = {}
    display_cards = (
        bookstore.get("display_cards") if isinstance(bookstore, Mapping) else None
    ) or []
    in_range_ids, public_books, public_cards = _build_public_shelf_books(books, display_cards)
    return {
        "in_range_book_ids": in_range_ids,
        "noticed_items": [],
        "tinted_books": [],
        "untinted_books": copy.deepcopy(public_books),
        "public_shelf_books": public_books,
        "public_display_cards": public_cards,
        "active_concerns": [],
        "focus_question": "",
        "concern_keywords": [],
        "private_concerns_scrubbed": True,
        "llm_calls": 0,
        "zero_write": True,
    }


def project_concern_tinted_shelf_zero_write(
    day_root: Path | str,
    *,
    focus_question: str = "",
    concern_keywords: Sequence[str] = (),
    now_iso: str | None = None,
    preloaded_state: Optional[Mapping[str, Any]] = None,
    auto_discover_concerns: bool = False,
) -> dict[str, Any]:
    """Zero-write projection of the bookstore shelf under one concern filter (Ch.23).

    Only books inside the shelf field of view are considered.  A book is ``noticed``
    when its title / cover summary / TOC / sample excerpt matches the current
    ``focus_question`` or ``concern_keywords``; noticing one slice of the cover or
    TOC never means the whole book was understood or dumped.  When nothing matches,
    ``noticed_items`` is an honest empty list.

    Reads ``load_state_readonly`` only: no mkdir, no temp file, no mtime change,
    ``llm_calls == 0``.
    """
    root = Path(day_root)
    if preloaded_state is None:
        state, _exists = load_state_readonly(root)
    else:
        state = dict(preloaded_state)

    public_life = state.get("public_life")
    bookstore = public_life.get("bookstore") if isinstance(public_life, Mapping) else None
    books = bookstore.get("books") if isinstance(bookstore, Mapping) else None
    if not isinstance(books, Mapping):
        books = {}
    display_cards = (
        bookstore.get("display_cards") if isinstance(bookstore, Mapping) else None
    ) or []

    focus_clean = " ".join(str(focus_question).split()) if focus_question else ""
    keywords_clean: list[str] = []
    for kw in concern_keywords or ():
        if isinstance(kw, str):
            cleaned_kw = " ".join(kw.split())
            if cleaned_kw:
                keywords_clean.append(cleaned_kw)

    active_concerns: list[dict[str, Any]] = []
    if auto_discover_concerns and not focus_clean and not keywords_clean:
        focus_clean, keywords_clean, active_concerns = _gather_subject_shelf_concerns_readonly(
            root, state
        )
    else:
        if focus_clean:
            active_concerns.append({
                "concern_id": _stable_id("concern", "explicit_focus", focus_clean),
                "concern_text": focus_clean,
                "source": "explicit_focus",
            })
        for kw in keywords_clean:
            active_concerns.append({
                "concern_id": _stable_id("concern", "explicit_keyword", kw),
                "concern_text": kw,
                "source": "explicit_keyword",
            })

    in_range_book_ids, public_books, public_cards = _build_public_shelf_books(books, display_cards)
    public_by_id = {b["book_id"]: b for b in public_books}
    noticed_items: list[dict[str, Any]] = []
    untinted_books: list[dict[str, Any]] = []
    for book_id in in_range_book_ids:
        book = books.get(book_id)
        if not isinstance(book, Mapping):
            continue
        matched = _match_concern_cues(book, focus_clean, keywords_clean)
        if not matched:
            untinted_books.append(copy.deepcopy(public_by_id[book_id]))
            continue
        noticed_items.append(
            {
                "book_id": book_id,
                "title": str(book.get("title") or ""),
                "noticed_angle_cn": _concern_noticed_angle(book, focus_clean, matched),
                "matched_cues": matched,
                "in_range": True,
                "noticed": True,
                "understood": False,
                "full_text_dumped": False,
            }
        )

    return {
        "in_range_book_ids": in_range_book_ids,
        "noticed_items": noticed_items,
        "tinted_books": copy.deepcopy(noticed_items),
        "untinted_books": untinted_books,
        "public_shelf_books": public_books,
        "public_display_cards": public_cards,
        "active_concerns": active_concerns,
        "focus_question": focus_clean,
        "concern_keywords": keywords_clean,
        "private_concerns_scrubbed": False,
        "llm_calls": 0,
        "zero_write": True,
    }


def _handle_bookstore_service(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    raw_svc = payload.get("service") if payload.get("service") is not None else payload.get("operation")
    svc = _clean_text(raw_svc, field_name="service", max_len=40)
    b_store = state["public_life"]["bookstore"]
    books = b_store["books"]
    open_now = _is_bookstore_open(now_dt)
    now_str = format_iso(now_dt)
    life = _ensure_shopkeeper_life(b_store)
    # Keep the persisted task block aligned with the clock after lazy normalization.
    _settle_shopkeeper_life_in_place(state, now_dt)

    if svc == "browse_book":
        if not open_now:
            raise V04NeighborhoodError(
                "BOOKSTORE_CLOSED", "Bookstore is only open 10:00-19:00", http_status=409
            )
        bid = _require_id(payload.get("book_id"), field_name="book_id")
        book = books.get(bid)
        if book is None:
            raise V04NeighborhoodError("BOOK_NOT_FOUND", f"Unknown book {bid!r}", http_status=404)
        browse_result: dict[str, Any] = {
            "service": "browse_book",
            "book_id": bid,
            "title": book["title"],
            "author_name": book["author_name"],
            "cover_summary": book["cover_summary"],
            "toc": list(book["toc"]),
            "sample_excerpt": book["sample_excerpt"],
            "full_text_dumped": False,
        }
        if (
            payload.get("focus_question") is not None
            or payload.get("concern_keywords") is not None
        ):
            focus_clean = (
                _clean_text(payload["focus_question"], field_name="focus_question", max_len=200)
                if payload.get("focus_question") is not None
                else ""
            )
            keywords_clean: list[str] = []
            raw_keywords = payload.get("concern_keywords")
            if raw_keywords is not None:
                if isinstance(raw_keywords, (str, bytes)) or not isinstance(
                    raw_keywords, (list, tuple)
                ):
                    raise V04NeighborhoodError(
                        "INVALID_FIELDS",
                        "concern_keywords must be a list of strings",
                        http_status=400,
                    )
                for item in raw_keywords:
                    keywords_clean.append(
                        _clean_text(item, field_name="concern_keywords", max_len=80)
                    )
            matched = _match_concern_cues(book, focus_clean, keywords_clean)
            b_store["last_shelf_focus"] = {
                "focus_question": focus_clean,
                "concern_keywords": list(keywords_clean),
                "updated_at": now_str,
            }
            browse_result["focus_question"] = focus_clean
            browse_result["noticed_angle_cn"] = _concern_noticed_angle(book, focus_clean, matched)
            browse_result["understood"] = False
            browse_result["full_text_dumped"] = False
        return browse_result

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
        _remember_shopkeeper_interaction(
            life,
            kind="borrow_book",
            ref_id=bid,
            note_cn=f"借走了《{book['title']}》",
            at_iso=now_str,
        )

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
        _remember_shopkeeper_interaction(
            life,
            kind="return_book",
            ref_id=bid,
            note_cn=f"把《{book['title']}》还回了{'归还箱' if book['status'] == 'return_box_pending' else '柜台'}",
            at_iso=now_str,
        )

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
        _remember_shopkeeper_interaction(
            life,
            kind="submit_display_card",
            ref_id=cid,
            note_cn=f"把《{card['title']}》的说明卡留在了柜台展示面",
            at_iso=now_str,
        )
        return {
            "service": "submit_display_card",
            "card_id": cid,
            "displayed_revision": rev,
            "displayed_text": exact_text,
            "private_draft_exposed": False,
        }

    if svc in ("share_shopkeeper_rumor", "note_rumor"):
        # Explicit rumor exchange is allowed whenever the subject is at the shop
        # counter (the shop owner may pass on hearsay outside posted hours too);
        # unlike card/borrow actions it is not gated on 10:00-19:00.
        route_id = _clean_text(payload.get("route_id"), field_name="route_id", max_len=48)
        if route_id not in ROUTES_CATALOG:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"Unknown route_id {route_id!r}", http_status=400
            )
        claim_cn = (
            _optional_text(payload.get("claim_cn"), field_name="claim_cn", max_len=200)
            or DEFAULT_RUMOR_CLAIM_CN
        )
        rumors = [r for r in (life.get("heard_rumors") or []) if isinstance(r, dict)]
        existing = next(
            (
                r
                for r in rumors
                if r.get("route_id") == route_id
                and r.get("claim_cn") == claim_cn
                and not r.get("verified_firsthand")
            ),
            None,
        )
        if existing is not None:
            rumor = existing
            replay = True
        else:
            rumor = {
                "rumor_id": _stable_id("rumor", route_id, claim_cn, now_str),
                "source_name": SHOPKEEPER_NAME,
                "route_id": route_id,
                "claim_cn": claim_cn,
                "heard_at": now_str,
                "verified_firsthand": False,
                "refutation_note_cn": "",
            }
            rumors.append(rumor)
            life["heard_rumors"] = rumors[-MAX_SHOPKEEPER_MEMORIES:]
            replay = False
        # CRITICAL: hearsay NEVER writes journey.discovered_roadblocks or world_truth.
        return {
            "service": "share_shopkeeper_rumor",
            "rumor_id": rumor["rumor_id"],
            "route_id": route_id,
            "claim_cn": claim_cn,
            "source_name": SHOPKEEPER_NAME,
            "verified_firsthand": bool(rumor.get("verified_firsthand")),
            "known_blocked_to_subject": route_id
            in (state["journey"].get("discovered_roadblocks") or []),
            "replay": replay,
        }

    if svc == "suggest_card_spot":
        if not open_now:
            raise V04NeighborhoodError(
                "BOOKSTORE_CLOSED", "Bookstore is only open 10:00-19:00", http_status=409
            )
        cid = _require_id(payload.get("card_id"), field_name="card_id")
        card = state["craft_bench"]["cards"].get(cid)
        if card is None:
            raise V04NeighborhoodError("CARD_NOT_FOUND", f"Unknown card {cid!r}", http_status=404)
        if int(card.get("inscribed_revision") or 0) < 1:
            raise V04NeighborhoodError(
                "CARD_NOT_INSCRIBED",
                "Only an inscribed card version can be suggested for a display spot",
                http_status=409,
            )
        spot_note = _clean_text(payload.get("spot_note"), field_name="spot_note", max_len=120)
        suggestions = [
            s for s in (life.get("card_spot_suggestions") or []) if isinstance(s, dict)
        ]
        suggestion_id = _stable_id("sugg", cid, spot_note, now_str, len(suggestions))
        suggestion = {
            "suggestion_id": suggestion_id,
            "card_id": cid,
            "spot_note": spot_note,
            "suggested_at": now_str,
            "status": "pending_shopkeeper_review",
            "outcome_reason_cn": "",
            "resolved_at": None,
        }
        suggestions.append(suggestion)
        life["card_spot_suggestions"] = suggestions[-MAX_SHOPKEEPER_MEMORIES:]
        _remember_shopkeeper_interaction(
            life,
            kind="suggest_card_spot",
            ref_id=suggestion_id,
            note_cn=f"指着{spot_note}，说这张卡也许能摆在那里",
            at_iso=now_str,
        )
        return {
            "service": "suggest_card_spot",
            "suggestion_id": suggestion_id,
            "card_id": cid,
            "spot_note": spot_note,
            "status": "pending_shopkeeper_review",
            "outcome_reason_cn": "",
        }

    if svc == "decide_card_spot":
        suggestions = [
            s
            for s in (life.get("card_spot_suggestions") or [])
            if isinstance(s, dict)
        ]
        raw_sid = payload.get("suggestion_id")
        if raw_sid is not None:
            sid = _require_id(raw_sid, field_name="suggestion_id")
            target = next(
                (s for s in suggestions if s.get("suggestion_id") == sid), None
            )
            if target is None:
                raise V04NeighborhoodError(
                    "SUGGESTION_NOT_FOUND", f"Unknown suggestion {sid!r}", http_status=404
                )
        else:
            pending = [
                s
                for s in suggestions
                if s.get("status") == "pending_shopkeeper_review"
            ]
            if not pending:
                raise V04NeighborhoodError(
                    "SUGGESTION_NOT_FOUND", "No pending card spot suggestion to decide", http_status=404
                )
            target = pending[-1]

        if target.get("status") != "pending_shopkeeper_review":
            # Idempotent replay: an already resolved suggestion keeps its outcome.
            return {
                "service": "decide_card_spot",
                "suggestion_id": target.get("suggestion_id"),
                "card_id": target.get("card_id"),
                "status": target.get("status"),
                "outcome_reason_cn": target.get("outcome_reason_cn"),
                "resolved_at": target.get("resolved_at"),
                "replay": True,
            }

        outcome = _clean_text(payload.get("outcome"), field_name="outcome", max_len=24)
        if outcome not in ("adopted", "relocated"):
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "outcome must be 'adopted' or 'relocated'", http_status=400
            )
        reason_cn = _optional_text(payload.get("reason_cn"), field_name="reason_cn", max_len=200)
        _resolve_card_spot_suggestion(
            target, now_dt, life=life, outcome=outcome, reason_cn=reason_cn
        )
        return {
            "service": "decide_card_spot",
            "suggestion_id": target["suggestion_id"],
            "card_id": target["card_id"],
            "status": target["status"],
            "outcome_reason_cn": target["outcome_reason_cn"],
            "resolved_at": target["resolved_at"],
            "replay": False,
        }

    if svc == "shop_ambient_shift":
        # Gentle non-crisis shelf/seat update; no business-hours gate.
        change_note = _clean_text(
            payload.get("change_note"), field_name="change_note", max_len=200
        )
        _append_shopkeeper_ambient(life, change_note)
        return {
            "service": "shop_ambient_shift",
            "change_note": change_note,
            "crisis": False,
            "ambient_changes": list(life.get("ambient_changes") or []),
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


def project_second_resident_zero_write(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Zero-write projection of the second independent neighborhood resident (Saeki Aoi)."""
    root = Path(day_root)
    now_dt = resolve_now_readonly(root, now_iso)
    raw_state, _ = load_state_readonly(root)
    working = copy.deepcopy(raw_state)
    _ensure_cafe_resident_life(working["public_life"])
    env = working["environment"]
    weather = str(env.get("weather") or "clear")
    season = _resolve_effective_season(env, now_dt)
    festival_info = _derive_seasonal_festival(working, now_dt)
    return _project_cafe_resident_life(
        working["public_life"]["cafe_resident"],
        weather=weather,
        season=season,
        festival_info=festival_info,
        now_dt=now_dt,
    )


def _handle_interact_cafe_resident(
    state: dict[str, Any], payload: Mapping[str, Any], *, now_dt: datetime
) -> dict[str, Any]:
    """Handle public-counter interactions with Saeki Aoi at the roastery cafe."""
    pub = state["public_life"]
    cafe_life = _ensure_cafe_resident_life(pub)
    _settle_cafe_resident_in_place(state, now_dt)
    now_str = format_iso(now_dt)
    phase_key, phase_cn, start_dt, at_counter = _cafe_phase_for(now_dt)
    open_now = _is_cafe_open(now_dt)
    env = state["environment"]
    weather = str(env.get("weather") or "clear")
    season = _resolve_effective_season(env, now_dt)
    festival_info = _derive_seasonal_festival(state, now_dt)
    env_notes = _cafe_environment_notes(weather, season, festival_info)

    raw_op = payload.get("operation") if payload.get("operation") is not None else payload.get("service")
    if raw_op is not None:
        op = _clean_text(raw_op, field_name="operation", max_len=40)
    elif payload.get("coaster_note") is not None or payload.get("note") is not None:
        op = "leave_cafe_coaster_note"
    elif payload.get("errand_note") is not None:
        op = "post_shared_errand"
    elif payload.get("route_id") is not None:
        op = "share_cafe_rumor"
    else:
        op = "check_neighbor_schedule"

    if op in ("leave_cafe_coaster_note", "coaster_note"):
        raw_note = (
            payload.get("coaster_note")
            if payload.get("coaster_note") is not None
            else payload.get("note")
        )
        note_text = _sanitize_public_text(
            _clean_text(raw_note, field_name="coaster_note", max_len=240)
        )
        coasters = [c for c in (cafe_life.get("coaster_notes") or []) if isinstance(c, dict)]
        coaster_id = _stable_id("coaster", note_text, now_str, len(coasters))
        received_in_person = bool(open_now and at_counter)
        entry = {
            "coaster_id": coaster_id,
            "note_cn": note_text,
            "left_at": now_str,
            "resident_phase_at_drop": phase_key,
            "received_in_person": received_in_person,
            "custody_spot": "counter_hand_received" if received_in_person else "counter_tray_pending",
            "privilege_boundary": SHOPKEEPER_PRIVILEGE_BOUNDARY,
            "favorability_score": None,
            "omission_debt": 0,
        }
        coasters.append(entry)
        cafe_life["coaster_notes"] = coasters[-MAX_SHOPKEEPER_MEMORIES:]
        _remember_shopkeeper_interaction(
            cafe_life,
            kind="leave_cafe_coaster_note",
            ref_id=coaster_id,
            note_cn=(
                "在吧台杯垫夹上留了一张便签（店主当面收在木托盘边）"
                if received_in_person
                else "在吧台门口托盘夹上留了一张杯垫便签（待店主回吧台查看）"
            ),
            at_iso=now_str,
        )
        return {
            "operation": "leave_cafe_coaster_note",
            "resident_id": CAFE_RESIDENT_ID,
            "resident_name": CAFE_RESIDENT_NAME,
            "coaster_id": coaster_id,
            "note_cn": note_text,
            "received_in_person": received_in_person,
            "current_phase": phase_key,
            "privilege_boundary": SHOPKEEPER_PRIVILEGE_BOUNDARY,
            "favorability_score": None,
            "omission_debt": 0,
        }

    if op in ("post_shared_errand", "shared_errand_board"):
        raw_err = (
            payload.get("errand_note")
            if payload.get("errand_note") is not None
            else payload.get("note")
        )
        errand_text = _sanitize_public_text(
            _clean_text(raw_err, field_name="errand_note", max_len=240)
        )
        board = [e for e in (cafe_life.get("shared_errand_board") or []) if isinstance(e, dict)]
        errand_id = _stable_id("errand", errand_text, now_str, len(board))
        item = {
            "errand_id": errand_id,
            "note_cn": errand_text,
            "posted_at": now_str,
            "status": "posted_optional",
            "favorability_score": None,
            "omission_debt": 0,
        }
        board.append(item)
        cafe_life["shared_errand_board"] = board[-MAX_SHOPKEEPER_MEMORIES:]
        _remember_shopkeeper_interaction(
            cafe_life,
            kind="post_shared_errand",
            ref_id=errand_id,
            note_cn="在咖啡屋门口街坊顺手板上贴了一条便签",
            at_iso=now_str,
        )
        return {
            "operation": "post_shared_errand",
            "resident_id": CAFE_RESIDENT_ID,
            "errand_id": errand_id,
            "note_cn": errand_text,
            "status": "posted_optional",
            "favorability_score": None,
            "omission_debt": 0,
        }

    if op in ("share_cafe_rumor", "note_rumor"):
        route_id = _clean_text(payload.get("route_id"), field_name="route_id", max_len=48)
        if route_id not in ROUTES_CATALOG:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", f"Unknown route_id {route_id!r}", http_status=400
            )
        claim_cn = (
            _optional_text(payload.get("claim_cn"), field_name="claim_cn", max_len=200)
            or CAFE_DEFAULT_RUMOR_CLAIM_CN
        )
        rumors = [r for r in (cafe_life.get("heard_rumors") or []) if isinstance(r, dict)]
        existing = next(
            (
                r
                for r in rumors
                if r.get("route_id") == route_id
                and r.get("claim_cn") == claim_cn
                and not r.get("verified_firsthand")
            ),
            None,
        )
        if existing is not None:
            rumor = existing
            replay = True
        else:
            rumor = {
                "rumor_id": _stable_id("cafe_rumor", route_id, claim_cn, now_str),
                "source_resident_id": CAFE_RESIDENT_ID,
                "source_name": CAFE_RESIDENT_NAME,
                "route_id": route_id,
                "claim_cn": claim_cn,
                "heard_at": now_str,
                "epistemic_status": "hearsay_unverified",
                "verified_firsthand": False,
                "refutation_note_cn": "",
                "verification_note_cn": "",
            }
            rumors.append(rumor)
            cafe_life["heard_rumors"] = rumors[-MAX_SHOPKEEPER_MEMORIES:]
            replay = False
        # CRITICAL: hearsay NEVER modifies journey.discovered_roadblocks or world_truth.roadblocks!
        return {
            "operation": "share_cafe_rumor",
            "rumor_id": rumor["rumor_id"],
            "source_resident_id": CAFE_RESIDENT_ID,
            "source_name": CAFE_RESIDENT_NAME,
            "route_id": route_id,
            "claim_cn": claim_cn,
            "epistemic_status": rumor.get("epistemic_status") or "hearsay_unverified",
            "verified_firsthand": bool(rumor.get("verified_firsthand")),
            "known_blocked_to_subject": route_id
            in (state["journey"].get("discovered_roadblocks") or []),
            "replay": replay,
        }

    if op == "check_neighbor_schedule":
        return {
            "operation": "check_neighbor_schedule",
            "resident_id": CAFE_RESIDENT_ID,
            "resident_name": CAFE_RESIDENT_NAME,
            "role": CAFE_RESIDENT_ROLE,
            "home_venue": CAFE_HOME_VENUE,
            "open_window": CAFE_OPEN_WINDOW,
            "is_open_now": open_now,
            "at_counter": bool(at_counter),
            "current_phase": phase_key,
            "current_phase_cn": phase_cn,
            "current_phase_started_at": format_iso(start_dt),
            "weather_reaction_cn": env_notes["weather_reaction_cn"],
            "market_reaction_cn": env_notes["market_reaction_cn"],
            "privilege_boundary": SHOPKEEPER_PRIVILEGE_BOUNDARY,
            "private_room_knowledge": False,
            "favorability_score": None,
            "omission_debt": 0,
        }

    raise V04NeighborhoodError(
        "INVALID_FIELDS", f"Unsupported cafe resident operation {op!r}", http_status=400
    )


# ---------------------------------------------------------------------------
# Unified Command Application with Idempotency & CAS
# ---------------------------------------------------------------------------


def _emit_v04_lived_episode(
    day_root: Path | str,
    *,
    episode_key: str,
    topic: str,
    exact_detail: str,
    gist_summary: str,
    occurred_at: str,
    emotional_salience: float = 0.48,
    affect_kind: Optional[str] = "affect_pulse",
    affect_intensity: int = 38,
    shared: bool = True,
    source_refs: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Best-effort canonical memory sink hook for notable v04 neighborhood outings (write-path only)."""
    try:
        try:
            from components.life.scripts import lived_memory_sink as _lms
        except ImportError:
            _life_dir = str(Path(__file__).resolve().parents[2] / "life" / "scripts")
            if _life_dir not in sys.path:
                sys.path.insert(0, _life_dir)
            import lived_memory_sink as _lms  # type: ignore[no-redef]
        ev = _lms.LivedEpisodeEvent(
            episode_key=episode_key,
            domain="v04_outing",
            topic=topic,
            exact_detail=exact_detail,
            gist_summary=gist_summary,
            occurred_at=occurred_at,
            emotional_salience=emotional_salience,
            affect_kind=affect_kind,
            affect_intensity=affect_intensity,
            shared=shared,
            source_refs=source_refs,
        )
        return _lms.emit_lived_episode_safe(day_root, ev, now_iso=occurred_at)
    except Exception as exc:
        return {"ok": False, "recorded": False, "reason": f"SINK_IMPORT:{type(exc).__name__}", "llm_calls": 0}


def _maybe_emit_v04_command_episode(
    day_root: Path | str,
    *,
    action: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    projected: Mapping[str, Any],
    now_iso: str,
) -> Optional[dict[str, Any]]:
    env = projected.get("environment") if isinstance(projected.get("environment"), Mapping) else {}
    weather = str(env.get("weather") or "clear").strip().lower()
    is_rainy = weather in ("rain", "heavy_rain", "storm", "drizzle")
    rev = projected.get("revision")
    op_tok = str(payload.get("operation_key") or f"rev_{rev}").strip()[:64]

    if action == "start_journey":
        dest = str(
            result.get("target_place_id")
            or result.get("target_place")
            or payload.get("target_place")
            or payload.get("destination")
            or payload.get("target_place_id")
            or "street_corner"
        )
        route_id = str(result.get("active_route_id") or "")
        dest_label = _place_short_name(dest)
        refs: list[tuple[str, str]] = [("encounter_id", f"v04_start_{op_tok}"), ("place_id", dest)]
        if route_id:
            refs.append(("route_id", route_id))
        return _emit_v04_lived_episode(
            day_root,
            episode_key=f"v04_outing:start_journey:{dest}:{op_tok}",
            topic=f"{'雨天' if is_rainy else '街区'}出门前往{dest_label}",
            exact_detail=f"在{weather}天气从住处出发前往{dest_label}" + (f"（经由 {route_id}）" if route_id else ""),
            gist_summary=f"{'冒雨' if is_rainy else '出门'}去了一趟{dest_label}",
            occurred_at=now_iso,
            emotional_salience=0.58 if is_rainy else 0.46,
            affect_kind="mood_shift" if is_rainy else "affect_pulse",
            affect_intensity=46 if is_rainy else 36,
            shared=True,
            source_refs=tuple(refs),
        )

    if action == "advance_or_turnback_journey":
        st = str(result.get("state") or "")
        halt = str(result.get("halt_reason") or "")
        op_name = str(result.get("operation") or "advance")
        if st == "arrived" or halt == "ROADBLOCK_ENCOUNTERED" or op_name == "turn_back":
            place_id = str(
                result.get("current_place_id")
                or result.get("target_place_id")
                or result.get("at_place_id")
                or "street_corner"
            )
            place_label = _place_short_name(place_id)
            blocked_r = str(result.get("encountered_roadblock") or "")
            refs = [("encounter_id", f"v04_jstep_{op_tok}"), ("place_id", place_id)]
            if blocked_r:
                refs.append(("route_id", blocked_r))
            if halt == "ROADBLOCK_ENCOUNTERED":
                topic = f"街区路阻发现：{place_label}"
                detail = f"途经{place_label}时发现路线受阻（{blocked_r or '前方施工'}）"
                gist = f"出门走到{place_label}附近发现前路临时受阻"
            elif op_name == "turn_back":
                topic = f"街区折返：{place_label}"
                detail = f"在{weather}天气决定中途折返前往{place_label}"
                gist = f"半路决定折返回{place_label}"
            else:
                topic = f"{'雨中' if is_rainy else ''}抵达{place_label}"
                detail = f"在{weather}天气步行到达了{place_label}"
                gist = f"{'冒雨' if is_rainy else '步行'}到了{place_label}"
            return _emit_v04_lived_episode(
                day_root,
                episode_key=f"v04_outing:journey_step:{op_name}:{place_id}:{op_tok}",
                topic=topic,
                exact_detail=detail,
                gist_summary=gist,
                occurred_at=now_iso,
                emotional_salience=0.56 if (is_rainy or halt == "ROADBLOCK_ENCOUNTERED") else 0.48,
                affect_kind="mood_shift" if is_rainy else "affect_pulse",
                affect_intensity=44 if is_rainy else 38,
                shared=True,
                source_refs=tuple(refs),
            )
        return None

    if action == "interact_cafe_resident":
        op_name = str(result.get("operation") or "")
        if op_name not in ("leave_cafe_coaster_note", "post_shared_errand", "share_cafe_rumor"):
            return None
        item_id = str(
            result.get("coaster_id")
            or result.get("errand_id")
            or result.get("rumor_id")
            or f"cafe_{op_tok}"
        )
        note_body = str(result.get("note_cn") or result.get("claim_cn") or "").strip()
        return _emit_v04_lived_episode(
            day_root,
            episode_key=f"v04_outing:cafe:{op_name}:{item_id}",
            topic=f"咖啡屋邻里互动：{CAFE_RESIDENT_NAME}",
            exact_detail=f"在烘焙咖啡屋与{CAFE_RESIDENT_NAME}互动（{op_name}）：{note_body}",
            gist_summary=f"在街角咖啡屋和{CAFE_RESIDENT_NAME}留了便签或聊了几句",
            occurred_at=now_iso,
            emotional_salience=0.50,
            affect_kind="affect_pulse",
            affect_intensity=40,
            shared=True,
            source_refs=(("encounter_id", item_id), ("resident_id", CAFE_RESIDENT_ID)),
        )

    if action in ("bookstore_service", "shared_shelf_loan"):
        item_id = str(
            result.get("reservation_id")
            or result.get("loan_id")
            or result.get("suggestion_id")
            or result.get("book_id")
            or f"{action}_{op_tok}"
        )
        b_refs: list[tuple[str, str]] = [("encounter_id", item_id), ("place_id", "bookstore")]
        if result.get("book_id"):
            b_refs.append(("book_id", str(result["book_id"])))
        detail_txt = str(result.get("note_cn") or result.get("title_cn") or result.get("service") or action)
        return _emit_v04_lived_episode(
            day_root,
            episode_key=f"v04_outing:{action}:{item_id}",
            topic="旧书店借阅与翻看",
            exact_detail=f"在旧书店办理了{action}：{detail_txt}",
            gist_summary=f"在街角旧书店翻看或借了书（{detail_txt[:60]}）",
            occurred_at=now_iso,
            emotional_salience=0.48,
            affect_kind="affect_pulse",
            affect_intensity=38,
            shared=True,
            source_refs=tuple(b_refs),
        )

    if action in ("post_public_notice", "read_public_notice"):
        nid = str(result.get("notice_id") or payload.get("notice_id") or f"notice_{op_tok}")
        ntitle = str(result.get("title_cn") or result.get("content_cn") or "街区布告栏消息")
        return _emit_v04_lived_episode(
            day_root,
            episode_key=f"v04_outing:{action}:{nid}:{op_tok}",
            topic=f"街区布告栏：{ntitle[:40]}",
            exact_detail=f"在街区布告栏{('贴出' if action == 'post_public_notice' else '留意到')}：{ntitle}",
            gist_summary=f"留意到街区布告栏消息：{ntitle[:60]}",
            occurred_at=now_iso,
            emotional_salience=0.46,
            affect_kind="affect_pulse",
            affect_intensity=36,
            shared=True,
            source_refs=(("notice_id", nid), ("encounter_id", f"{action}_{nid}")),
        )

    if action in ("record_poi_physical_state", "update_place_booklet"):
        pid = str(result.get("place_id") or payload.get("place_id") or "street_corner")
        plabel = _place_short_name(pid)
        note_s = str(result.get("note_cn") or result.get("entry_cn") or payload.get("note_cn") or plabel)
        return _emit_v04_lived_episode(
            day_root,
            episode_key=f"v04_outing:{action}:{pid}:{op_tok}",
            topic=f"街区角落发现：{plabel}",
            exact_detail=f"记下了{plabel}的一处真实见闻：{note_s}",
            gist_summary=f"在{plabel}有了新的街区小发现",
            occurred_at=now_iso,
            emotional_salience=0.48,
            affect_kind="affect_pulse",
            affect_intensity=38,
            shared=True,
            source_refs=(("encounter_id", f"v04_disc_{pid}_{op_tok}"), ("place_id", pid)),
        )

    if action == "craft_paper_or_box" and str(result.get("step") or "").strip().lower() == "finished":
        proj_id = f"v04_box_{op_tok}"
        return _emit_v04_lived_episode(
            day_root,
            episode_key=f"v04_outing:craft_finished:{proj_id}",
            topic="完成手工分格收纳盒",
            exact_detail="在书桌边一步步完成了手工分格收纳盒的裁切与装订",
            gist_summary="做好了一只手工分格收纳盒",
            occurred_at=now_iso,
            emotional_salience=0.58,
            affect_kind="mood_shift",
            affect_intensity=46,
            shared=True,
            source_refs=(("project_id", proj_id),),
        )

    return None


def apply_v04_neighborhood_command(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Apply one validated neighborhood command with operation_key idempotency & CAS.

    Returns ``(status_label, action_result, projected_envelope_core)`` where
    ``status_label`` is ``"applied"`` or ``"already_applied"``.  The whole
    Read -> reconcile -> validate -> mutate -> save cycle is serialized by the
    shared file lock; zero-write projections never take it.
    """

    with state_file_lock(state_file_path(day_root)):
        status_label, result, projected = _apply_v04_neighborhood_command_locked(
            day_root, payload, actor_side=actor_side, now_iso=now_iso
        )
    if status_label == "applied":
        eff_iso = str(projected.get("updated_at") or projected.get("observed_at") or now_iso or "")
        _maybe_emit_v04_command_episode(
            day_root,
            action=str(payload.get("action") or "").strip(),
            payload=payload,
            result=result,
            projected=projected,
            now_iso=eff_iso,
        )
    return status_label, result, projected


def _apply_v04_neighborhood_command_locked(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
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

    # 2. Optional expected_revision CAS check (parsed before replay reconciliation
    # so an evicted key can be classified as a stale retry).
    exp_rev: Optional[int] = None
    if payload.get("expected_revision") is not None:
        raw_rev = payload["expected_revision"]
        if isinstance(raw_rev, bool) or not isinstance(raw_rev, int) or raw_rev < 0:
            raise V04NeighborhoodError(
                "INVALID_FIELDS", "expected_revision must be a non-negative integer", http_status=400
            )
        exp_rev = int(raw_rev)

    # 1. Idempotent replay check (active receipt first, then the eviction ring
    # and the ``phone/operations.jsonl`` journal for long-horizon retries).
    receipts = state["operation_receipts"]
    if op_key is not None and op_key in receipts:
        prev = receipts[op_key]
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

    if op_key is not None:
        outcome, detail = reconcile_evicted_or_active_op(
            root,
            state,
            op_key,
            dict_key="operation_receipts",
            expected_revision=exp_rev,
            payload_hash=intent_fp,
        )
        if outcome == "conflict":
            raise V04NeighborhoodError(
                "OPERATION_KEY_CONFLICT",
                "Same operation_key reused with a different command payload",
                http_status=409,
            )
        if outcome == "expired":
            raise V04NeighborhoodError(
                "RETRY_WINDOW_EXPIRED",
                f"operation_key {op_key!r} is older than the retained retry window",
                http_status=409,
                extra=detail or {},
            )
        if outcome in ("hit_full", "hit_evicted"):
            projected = project_v04_neighborhood(
                root,
                has_subject_capability=(actor_side == "subject"),
                now_iso=now_str,
                preloaded_state=state,
            )
            return "already_applied", copy.deepcopy(detail or {}), projected

    if exp_rev is not None and int(state["revision"]) != exp_rev:
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
        result = _handle_start_journey(state, payload, now_dt=now_dt, root=root)
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
    elif action == "interact_cafe_resident":
        result = _handle_interact_cafe_resident(state, payload, now_dt=now_dt)
    else:
        raise V04NeighborhoodError("INVALID_FIELDS", f"Unknown action {action!r}", http_status=400)

    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_str

    if op_key is not None:
        stamp_receipt(
            state,
            op_key,
            {
                "intent_fingerprint": intent_fp,
                "action": action,
                "actor_side": actor_side,
                "status": "applied",
                "ok": True,
                "revision": state["revision"],
                "applied_at": now_str,
                "result": copy.deepcopy(result),
            },
            revision=int(state["revision"]),
            dict_key="operation_receipts",
            payload_hash=intent_fp,
            committed_at=now_str,
        )
        trim_receipts_by_op_seq(
            state,
            dict_key="operation_receipts",
            max_keep=MAX_OPERATION_RECEIPTS,
        )

    save_state_atomic(root, state)

    projected = project_v04_neighborhood(
        root,
        has_subject_capability=(actor_side == "subject"),
        now_iso=now_str,
        preloaded_state=state,
    )
    return "applied", result, projected


# ---------------------------------------------------------------------------
# 0.5 Step 3: Consignment Craft Micro-Income, 4-Stage Street Project & Calm Gate
# ---------------------------------------------------------------------------

V05_MOMENTS_FILENAME = "v05_neighborhood_moments.json"
V05_CONSIGNMENT_VENUES: tuple[str, ...] = ("bookstore_bookband", "cafe_postcard")
CALM_GATE_LIGHT_EVENTS: tuple[str, ...] = (
    "巷口烤红薯推车",
    "旧书店木箱新孤品",
    "面包房傍晚打折袋",
    "布告栏寻猫启事更新",
)
RARE_INTERLUDE_MIN_GAP_DAYS = 12


def _v05_hmod(seed_text: str, mod: int) -> int:
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % mod


def v05_moments_state_path(day_root: Path | str) -> Path:
    return Path(day_root) / STATE_DIRNAME / V05_MOMENTS_FILENAME


def _default_v05_moments_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "revision": 0,
        "earned_wallet_jpy": 0,
        "consignments": [],
        "notice_followups": [],
        "slow_project_start_day": 1,
        "last_rare_day": None,
        "rare_interlude_log": [],
        "operation_receipts": {},
    }


def _load_v05_moments_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    path = v05_moments_state_path(day_root)
    if path.is_symlink():
        raise V04NeighborhoodError("STATE_SYMLINK_REFUSED", "Refusing symlink v05 moments state", http_status=503)
    if not path.is_file():
        return False, _default_v05_moments_state()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise V04NeighborhoodError("STATE_CORRUPTED", f"Corrupt v05 moments state: {exc}", http_status=503) from exc
    if not isinstance(doc, Mapping):
        raise V04NeighborhoodError("STATE_CORRUPTED", "v05 moments state must be a JSON object", http_status=503)
    merged = _default_v05_moments_state()
    for k in merged:
        if k in doc:
            merged[k] = copy.deepcopy(doc[k])
    return True, merged


def _save_v05_moments_atomic(day_root: Path | str, state: Mapping[str, Any]) -> None:
    path = v05_moments_state_path(day_root)
    if path.is_symlink():
        raise V04NeighborhoodError("STATE_SYMLINK_REFUSED", "Refusing symlink v05 moments state", http_status=503)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".v05_moments_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def street_slow_project_stage(day_index: int, start_day: int = 1) -> str:
    """分四阶段自然演进的花店门口木花架慢工程（零大模型调用）。"""
    elapsed = int(day_index) - int(start_day)
    if elapsed < 0:
        return "巷口如常，花店门口空着"
    if elapsed == 0:
        return "花店门口堆着几块新松木板和工具箱"
    if elapsed in (1, 2):
        return "两层阶梯木架已经钉好，还没刷漆，带一点木屑香"
    return "豆绿色新木花架已刷好漆，摆满了刚到的秋海棠"


def schedule_week_with_calm_gate(world_seed: str, week_index: int) -> list[Optional[str]]:
    """第 104 章平静闸门：每周 7 天至多每天 1 件轻偶遇，且至少保底 3 个纯平淡日（None）。"""
    plan: list[Optional[str]] = []
    active_days = 0
    for dow in range(7):
        if active_days >= 4:
            plan.append(None)
            continue
        roll = _v05_hmod(f"{world_seed}:w{int(week_index)}:d{dow}", 10)
        if roll < 5:
            ev = CALM_GATE_LIGHT_EVENTS[_v05_hmod(f"{world_seed}:ev:w{int(week_index)}:d{dow}", len(CALM_GATE_LIGHT_EVENTS))]
            plan.append(ev)
            active_days += 1
        else:
            plan.append(None)
    return plan


def can_trigger_rare_interlude(
    last_rare_day: Optional[int],
    current_day: int,
    min_gap_days: int = RARE_INTERLUDE_MIN_GAP_DAYS,
) -> bool:
    """停电夜、初雪、清晨半句梦影等稀有插曲两次间隔不少于 12 天。"""
    if last_rare_day is None:
        return True
    return (int(current_day) - int(last_rare_day)) >= int(min_gap_days)


def consign_work_at_venue(
    day_root: Path | str,
    *,
    consignment_id: str,
    venue: str,
    work_title_cn: str,
    work_ref: str,
    unit_price_jpy: int = 420,
    posted_day: int = 1,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """登记在旧书店荐书腰封或咖啡馆明信片架的真实寄售作品（慢卖零奇迹瞬销）。"""
    cid = _clean_text(consignment_id, field_name="consignment_id", max_len=64)
    v_clean = _clean_text(venue, field_name="venue", max_len=40)
    if v_clean not in V05_CONSIGNMENT_VENUES:
        raise V04NeighborhoodError("INVALID_FIELDS", f"venue must be one of {V05_CONSIGNMENT_VENUES}", http_status=400)
    title = _clean_text(work_title_cn, field_name="work_title_cn", max_len=120)
    wref = _clean_text(work_ref, field_name="work_ref", max_len=160)
    price = max(100, min(5000, int(unit_price_jpy)))
    now_str = now_iso or format_iso(resolve_now_readonly(Path(day_root), None))

    with state_file_lock(v05_moments_state_path(day_root)):
        _, st = _load_v05_moments_readonly(day_root)
        op_k = _clean_text(operation_key, field_name="operation_key", max_len=96) if operation_key else None
        if op_k and op_k in st["operation_receipts"]:
            return copy.deepcopy(st["operation_receipts"][op_k])
        if any(str(row.get("consignment_id")) == cid for row in st["consignments"]):
            raise V04NeighborhoodError("CONSIGNMENT_EXISTS", f"Consignment {cid!r} already exists", http_status=409)
        entry = {
            "consignment_id": cid,
            "venue": v_clean,
            "work_title_cn": title,
            "work_ref": wref,
            "unit_price_jpy": price,
            "posted_day": int(posted_day),
            "status": "CONSIGNED",
            "sold_day": None,
            "sold_at": None,
            "created_at": now_str,
        }
        st["consignments"].append(entry)
        st["revision"] = int(st.get("revision") or 0) + 1
        out = {
            "ok": True,
            "status": "CONSIGNED",
            "consignment": copy.deepcopy(entry),
            "earned_wallet_jpy": int(st["earned_wallet_jpy"]),
            "earned_wallet": int(st["earned_wallet_jpy"]),
            "outbound_push_allowed": False,
            "llm_calls": 0,
        }
        if op_k:
            st["operation_receipts"][op_k] = copy.deepcopy(out)
        _save_v05_moments_atomic(day_root, st)
        return out


def settle_consignment_sales(
    day_root: Path | str,
    *,
    world_seed: str = "seed_town",
    current_day: int,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """按日期确定性缓慢成交寄售作品并入账 ``earned_wallet``（慢卖、绝不在上架当天瞬售）。"""
    now_str = now_iso or format_iso(resolve_now_readonly(Path(day_root), None))
    with state_file_lock(v05_moments_state_path(day_root)):
        _, st = _load_v05_moments_readonly(day_root)
        op_k = _clean_text(operation_key, field_name="operation_key", max_len=96) if operation_key else None
        if op_k and op_k in st["operation_receipts"]:
            return copy.deepcopy(st["operation_receipts"][op_k])
        newly_sold: list[dict[str, Any]] = []
        for row in st["consignments"]:
            if row.get("status") != "CONSIGNED":
                continue
            elapsed = int(current_day) - int(row.get("posted_day") or 0)
            if elapsed < 2:
                continue
            cid = str(row.get("consignment_id") or "")
            if elapsed >= 4 or _v05_hmod(f"{world_seed}:consign:{cid}:{int(current_day)}", 3) == 0:
                row["status"] = "SOLD"
                row["sold_day"] = int(current_day)
                row["sold_at"] = now_str
                st["earned_wallet_jpy"] = int(st.get("earned_wallet_jpy") or 0) + int(row.get("unit_price_jpy") or 0)
                newly_sold.append(copy.deepcopy(row))
        st["revision"] = int(st.get("revision") or 0) + 1
        out = {
            "ok": True,
            "newly_sold": newly_sold,
            "earned_wallet_jpy": int(st["earned_wallet_jpy"]),
            "earned_wallet": int(st["earned_wallet_jpy"]),
            "llm_calls": 0,
        }
        if op_k:
            st["operation_receipts"][op_k] = copy.deepcopy(out)
        _save_v05_moments_atomic(day_root, st)
        return out


def record_rare_interlude_check(
    day_root: Path | str,
    *,
    interlude_kind: str,
    current_day: int,
    min_gap_days: int = RARE_INTERLUDE_MIN_GAP_DAYS,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """第 104 章稀有插曲调度检查：距上次稀有插曲不足 12 天时具名拒绝，不连演戏剧。"""
    kind = _clean_text(interlude_kind, field_name="interlude_kind", max_len=64)
    now_str = now_iso or format_iso(resolve_now_readonly(Path(day_root), None))
    with state_file_lock(v05_moments_state_path(day_root)):
        _, st = _load_v05_moments_readonly(day_root)
        op_k = _clean_text(operation_key, field_name="operation_key", max_len=96) if operation_key else None
        if op_k and op_k in st["operation_receipts"]:
            return copy.deepcopy(st["operation_receipts"][op_k])
        last_day = st.get("last_rare_day")
        if not can_trigger_rare_interlude(last_day, current_day, min_gap_days=min_gap_days):
            raise V04NeighborhoodError(
                "CALM_GATE_RARE_GAP_ACTIVE",
                f"Rare interlude {kind!r} blocked within {min_gap_days}-day calm gap",
                http_status=409,
            )
        st["last_rare_day"] = int(current_day)
        st["rare_interlude_log"].append({
            "kind": kind,
            "day_index": int(current_day),
            "triggered_at": now_str,
        })
        st["rare_interlude_log"] = st["rare_interlude_log"][-64:]
        st["revision"] = int(st.get("revision") or 0) + 1
        out = {
            "ok": True,
            "allowed": True,
            "interlude_kind": kind,
            "day_index": int(current_day),
            "last_rare_day": int(current_day),
            "llm_calls": 0,
        }
        if op_k:
            st["operation_receipts"][op_k] = copy.deepcopy(out)
        _save_v05_moments_atomic(day_root, st)
        return out


def read_v05_world_moments_zero_write(
    day_root: Path | str,
    *,
    world_seed: str = "seed_town",
    day_index: int = 1,
    week_index: int = 0,
) -> dict[str, Any]:
    """100% 零写入投影 0.5 Step 3 小镇烟火与平静闸门状态。"""
    exists, st = _load_v05_moments_readonly(day_root)
    start_day = int(st.get("slow_project_start_day") or 1)
    week_plan = schedule_week_with_calm_gate(world_seed, week_index)
    return {
        "state_exists": bool(exists),
        "revision": int(st.get("revision") or 0),
        "earned_wallet_jpy": int(st.get("earned_wallet_jpy") or 0),
        "earned_wallet": int(st.get("earned_wallet_jpy") or 0),
        "consignments": copy.deepcopy(st.get("consignments") or []),
        "street_slow_project_stage": street_slow_project_stage(day_index, start_day=start_day),
        "calm_gate_week_plan": week_plan,
        "calm_gate_quiet_days_count": sum(1 for item in week_plan if item is None),
        "last_rare_day": st.get("last_rare_day"),
        "rare_interlude_log": copy.deepcopy(st.get("rare_interlude_log") or []),
        "outbound_push_allowed": False,
        "llm_calls": 0,
    }


__all__ = [
    "AIRFLOW_KINDS",
    "BOOKSTORE_STARTER_BOOKS",
    "BOX_STEPS",
    "CAFE_HOME_VENUE",
    "CAFE_RESIDENT_ID",
    "CAFE_RESIDENT_NAME",
    "CAFE_RESIDENT_PHASES",
    "CAFE_RESIDENT_ROLE",
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
    "SHOPKEEPER_ID",
    "SHOPKEEPER_NAME",
    "STATE_DIRNAME",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "V04NeighborhoodError",
    "V05_CONSIGNMENT_VENUES",
    "WEATHER_KINDS",
    "advance_autonomous_weather_pulse",
    "advance_v04_neighborhood_processes",
    "apply_v04_neighborhood_command",
    "can_trigger_rare_interlude",
    "canonical_digest",
    "compute_cloth_hourly_rate",
    "consign_work_at_venue",
    "default_state",
    "extract_explicit_season",
    "format_iso",
    "load_state_readonly",
    "moisture_to_band",
    "parse_iso_moment",
    "project_concern_tinted_shelf_zero_write",
    "project_second_resident_zero_write",
    "project_v04_neighborhood",
    "read_character_identity_readonly",
    "read_v05_world_moments_zero_write",
    "record_rare_interlude_check",
    "resolve_now_readonly",
    "save_state_atomic",
    "schedule_week_with_calm_gate",
    "settle_all_processes_in_place",
    "settle_consignment_sales",
    "state_file_path",
    "street_slow_project_stage",
    "v05_moments_state_path",
]
