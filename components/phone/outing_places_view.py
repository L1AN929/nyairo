#!/usr/bin/env python3
"""房间外六项日常切片：河边长椅与书店橱窗 / 散步路线与虚拟预算集市 / 可重访作品展与共同新地点草图。

它是什么
--------
一个**纯投影 + 轻量显式状态**模块，把 `15-产品记忆与自主机制完整设计.md §2.8` 的六项「房间外」
生活细节落成三份 `0600` 原子状态文件（位于 `<root>/phone/`）与三条薄投影读面：

1. **熟悉河边长椅**（`outing_bench_bookstore.json`）：核对真实 World 非 `home` 地点（并如实标出
   水岸 / 河边 / 海滩 / 公园 / 高台等长椅候选点），记录最多 12 条重访留痕。`no_streak_score=True`，
   不刷打卡连击分；`partner_default` 只公开 `shared=True` 的留痕，绝不泄露私密备注、`operation_key`
   或未公开条数。
2. **会换橱窗且影响阅读的书店**（同上）：按本地日期 `YYYY-MM-DD` 从 9 本固定文学/节令书目中确定性轮换
   3 个橱窗展位（`slot_index` `0..2`），如实标注当前 World 是否存在真实书店地点；本人可记入待读灵感，
   伴侣可留共读短笺；`reading_completed=False`、`model_calls=False`，绝不伪造已读完或触发模型。
3. **自己的散步路线和随手拍**（`outing_routes_market.json`）：保存最多 12 条含 `1..6` 个不重复真实
   World 非 `home` 地点的散步路线，并可选绑定真实 `PhoneAlbum` 照片引用；每次读取都向真实 World 与
   `album_detail` 复核可达/可读性。`partner_default` 只返回 `shared=True` 的路线摘要与
   `photo_status`（`available` / `withheld` / `none`），**绝不泄露底层 `album_artifact_id`、
   未公开路线或私人计数**。
4. **虚拟预算节日集市**（同上）：现读 `world_property_economy` 真实虚拟币余额（未初始化则如实报
   `GENESIS_NOT_INITIALIZED` 与 `balance_jpy=None`，绝不编造余额），按日期确定性轮换 3 个节令摊位，
   记录她量入为出的预算心愿与伴侣逛集小贴士；明确 `virtual_currency_only=True`、
   `does_not_debit_wallet=True`、`real_money_involved=False`、`changes_relationship=False`。
5. **可重访作品展**（`outing_gallery_blueprint.json`）：布置最多 8 件来自真实 `PrivateProjectService`
   指定 `(artifact_id, version_id)` 的小展品。每次 GET 都重读源作品与版本：源已 `ARCHIVED` 则双视角均
   `ARCHIVED_SOURCE` 扣留；本人视角给 `owner_version_excerpt`（`<=400` 字）；伴侣默认视角逐条核对
   该精确 `(artifact_id, version_id)` 是否存在真实 `LOCAL_DELIVERED` 本地分享，有才展示已投递分享正文
   摘要，否则 `VERSION_NOT_DELIVERED` 扣留（连展签一并扣留）。
6. **共同画图并定义功能的新地点**（同上）：双人分署名（`author_side: "her" | "partner"`）维护最多
   6 张活跃的新地点草图与用途定义卡；跨作者改卡或收起一律 `409 AUTHOR_MISMATCH`；明确
   `blueprint_only=True`、`does_not_mutate_world_topology=True`、`no_obligation=True`。

它不是什么
----------
* 纯 GET 绝不创建 `<root>/phone/` 或任何状态文件，绝不推进/修改任何底层 owner。
* 状态文件若是符号链接、损坏 JSON 或非法结构，读面具名报错并失败关闭，写面拒绝覆盖。
* 零产品模型调用、零真实扣款、零关系分改动、零私密 ID 旁路泄露。
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping, Optional

try:  # package import (tests / phone service import ``components.phone``)
    from ..life.scripts import state_tx_guard as _tx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat import fallback (components/phone on sys.path)
    import sys as _sys

    _LIFE_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "life" / "scripts"
    if str(_LIFE_SCRIPTS_DIR) not in _sys.path:
        _sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
    import state_tx_guard as _tx  # type: ignore[no-redef]

BENCH_BOOKSTORE_SCHEMA = "phone.outing.bench_bookstore.v1"
ROUTES_MARKET_SCHEMA = "phone.outing.routes_market.v1"
GALLERY_BLUEPRINT_SCHEMA = "phone.outing.gallery_blueprint.v1"

STATE_DIRNAME = "phone"
BENCH_BOOKSTORE_STATE_FILENAME = "outing_bench_bookstore.json"
ROUTES_MARKET_STATE_FILENAME = "outing_routes_market.json"
GALLERY_BLUEPRINT_STATE_FILENAME = "outing_gallery_blueprint.json"
STATE_SCHEMA_VERSION = 1

MAX_BENCH_VISITS = 12
MAX_WINDOW_BOOKMARKS = 9
MAX_WALK_ROUTES = 12
MAX_ROUTE_STOPS = 6
MAX_MARKET_WISHES = 9
MAX_GALLERY_EXHIBITS = 8
MAX_GALLERY_EXCERPT = 400
MAX_ACTIVE_BLUEPRINTS = 6
MAX_STORED_BLUEPRINTS = 24

MAX_NOTE = 120
MAX_ROUTE_TITLE = 60
MAX_BLUEPRINT_TITLE = 40
MAX_BLUEPRINT_SKETCH = 160
MAX_BLUEPRINT_USE = 120
MAX_BUDGET_CAP_JPY = 10_000_000
MAX_ID_LENGTH = 128
MAX_PLACE_ID_LENGTH = 96
MAX_OPERATION_KEY = 120

AUTHOR_SIDES = ("her", "partner")
BLUEPRINT_STATUSES = ("ACTIVE", "ARCHIVED")

GENESIS_NOT_INITIALIZED = "GENESIS_NOT_INITIALIZED"
ECONOMY_STATE_ERROR = "ECONOMY_STATE_ERROR"
ECONOMY_UNAVAILABLE = "ECONOMY_UNAVAILABLE"

VERSION_NOT_DELIVERED = "VERSION_NOT_DELIVERED"
VERSION_NOT_IN_ARTIFACT = "VERSION_NOT_IN_ARTIFACT"
ARCHIVED_SOURCE = "ARCHIVED_SOURCE"
SOURCE_NOT_READABLE = "SOURCE_NOT_READABLE"
VERSION_UNREADABLE = "VERSION_UNREADABLE"
NOT_A_PROJECT = "NOT_A_PROJECT"
SHARE_ENVELOPE_NOT_A_PROJECT = "SHARE_ENVELOPE_NOT_A_PROJECT"

OWNER_EXCERPT_BASIS = "owner_version_excerpt"
DELIVERED_SHARE_BASIS = "delivered_share_text"

#: 9-book seasonal / literary catalog for deterministic daily 3-slot window rotation.
BOOKSTORE_CATALOG: tuple[dict[str, str], ...] = (
    {
        "book_code": "tide_almanac_notes",
        "title": "潮汐与岸边小历",
        "window_line": "写给喜欢在傍晚看水面反光的人，按节气记下海风和河岸的声音。",
        "theme": "seasonal_nature",
    },
    {
        "book_code": "small_town_sketchbook",
        "title": "街角屋檐速写集",
        "window_line": "用钢笔和淡彩记下老书店、花铺与车站台阶的安静午后。",
        "theme": "walk_observation",
    },
    {
        "book_code": "letters_under_lamplight",
        "title": "灯下慢信选",
        "window_line": "不急着寄出的短笺与回信，适合睡前翻两页。",
        "theme": "epistolary_essays",
    },
    {
        "book_code": "autumn_bakery_fables",
        "title": "秋日果酱与小寓言",
        "window_line": "柑橘、肉桂和热茶香里的九篇短故事，读起来没有负担。",
        "theme": "warm_fiction",
    },
    {
        "book_code": "botanical_pocket_guide",
        "title": "路边野草图鉴",
        "window_line": "教人认出石缝、坡道与河堤边常见的小花与叶片。",
        "theme": "field_guide",
    },
    {
        "book_code": "rainy_window_poems",
        "title": "听雨窗台短诗",
        "window_line": "每首都不超过十行，留白比字多，像路过橱窗时停下的一分钟。",
        "theme": "poetry",
    },
    {
        "book_code": "paper_craft_architectures",
        "title": "纸上小建筑草图",
        "window_line": "凉亭、树屋、看海木台与秘密阁楼的手绘构造笔记。",
        "theme": "spatial_imagination",
    },
    {
        "book_code": "night_train_travelogue",
        "title": "慢车靠窗座位纪行",
        "window_line": "沿着海岸线小站一路停靠的散文，把风景留给不赶时间的人。",
        "theme": "travel_essay",
    },
    {
        "book_code": "winter_tea_utensils",
        "title": "器物与热饮日常",
        "window_line": "关于粗陶杯、木托盘和四季茶席的安静随笔。",
        "theme": "everyday_craft",
    },
)

BOOK_BY_CODE: dict[str, dict[str, str]] = {
    entry["book_code"]: dict(entry) for entry in BOOKSTORE_CATALOG
}

#: 9-stall curated festival market vocabulary (purely virtual budget planning, zero debit).
MARKET_STALL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "stall_code": "pressed_leaf_bookmark",
        "title": "压花叶脉书签摊",
        "scene_desc": "透明薄片里夹着银杏和海边小草，适合夹在正在读的那一页。",
        "suggested_virtual_jpy": 220,
    },
    {
        "stall_code": "warm_yuzu_tea_stand",
        "title": "热柚子茶小推车",
        "scene_desc": "木勺舀起温热果茶，路过时整个街口都是清爽柑橘香。",
        "suggested_virtual_jpy": 280,
    },
    {
        "stall_code": "sea_glass_windchime",
        "title": "海玻璃风铃手作摊",
        "scene_desc": "用磨圆的浅蓝与乳白碎玻璃串成小挂饰，风一吹就轻响。",
        "suggested_virtual_jpy": 540,
    },
    {
        "stall_code": "roasted_chestnut_cart",
        "title": "纸袋糖炒栗子摊",
        "scene_desc": "牛皮纸袋装着刚出锅的暖栗子，适合散步时揣在口袋里捂手。",
        "suggested_virtual_jpy": 320,
    },
    {
        "stall_code": "hand_dyed_coaster",
        "title": "草木染布杯垫铺",
        "scene_desc": "茜草与蓝染拼成的小方垫，摆在书桌或茶几上都很耐看。",
        "suggested_virtual_jpy": 450,
    },
    {
        "stall_code": "mini_lantern_charm",
        "title": "和纸小提灯挂件摊",
        "scene_desc": "掌心大小的折纸提灯，不刺眼，只作节令纪念。",
        "suggested_virtual_jpy": 390,
    },
    {
        "stall_code": "citrus_jam_jar",
        "title": "小瓶季节果酱摊",
        "scene_desc": "试吃勺旁摆着写有采摘月份的手写标签。",
        "suggested_virtual_jpy": 480,
    },
    {
        "stall_code": "sketch_postcard_rack",
        "title": "水彩风景明信片架",
        "scene_desc": "画着本地河岸、灯塔和老街坡道的厚卡纸明信片。",
        "suggested_virtual_jpy": 200,
    },
    {
        "stall_code": "ceramic_chopstick_rest",
        "title": "小陶偶筷架摊",
        "scene_desc": "排成一排的小云朵、贝壳与猫耳陶粒，挑一对也不超预算。",
        "suggested_virtual_jpy": 360,
    },
)

STALL_BY_CODE: dict[str, dict[str, Any]] = {
    entry["stall_code"]: dict(entry) for entry in MARKET_STALL_CATALOG
}
STALL_CODES: frozenset[str] = frozenset(STALL_BY_CODE)

_BENCH_ID_KEYWORDS = (
    "bench", "river", "riverside", "coast", "beach", "water", "harbor", "port",
    "park", "viewpoint", "hill", "promenade", "bridge", "lake", "shrine",
)
_BENCH_TEXT_KEYWORDS = (
    "河", "川", "海", "岸", "滩", "港", "水", "公园", "长椅", "展望", "高台", "神社", "步道",
)
_BOOKSTORE_ID_KEYWORDS = ("bookstore", "book_shop", "bookshop")
_BOOKSTORE_TEXT_KEYWORDS = ("书店", "书房", "古书")


class OutingStateError(ValueError):
    """Explicit pure-layer refusal with a stable reason code."""

    def __init__(self, reason_code: str, *, conflict: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.conflict = bool(conflict)


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_body(value: Any, limit: int) -> str:
    return str(value or "")[:limit].strip()


def _iso_text(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:40]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _text_field(value: Any, limit: int, reason: str) -> str:
    if not isinstance(value, str):
        raise OutingStateError(reason)
    text = value.strip()
    if not text or len(text) > limit:
        raise OutingStateError(reason)
    if any(ch in text for ch in ("\x00", "\r")):
        raise OutingStateError(reason)
    return _one_line(text, limit)


def _optional_text_field(value: Any, limit: int, reason: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OutingStateError(reason)
    if any(ch in value for ch in ("\x00", "\r")):
        raise OutingStateError(reason)
    text = _one_line(value, limit)
    if len(value.strip()) > limit:
        raise OutingStateError(reason)
    return text or None


def _local_id(value: Any, reason: str) -> str:
    if not isinstance(value, str):
        raise OutingStateError(reason)
    text = value.strip()
    if not text or len(text) > 80 or not re.match(r"^[A-Za-z0-9._:\-]+$", text):
        raise OutingStateError(reason)
    return text


def _place_id(value: Any, reason: str = "PLACE_ID_INVALID") -> str:
    if not isinstance(value, str):
        raise OutingStateError(reason)
    text = value.strip()
    if not text or len(text) > MAX_PLACE_ID_LENGTH:
        raise OutingStateError(reason)
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "\\", "?", "#", " ")):
        raise OutingStateError(reason)
    return text


def _artifact_ref(value: Any, reason: str) -> str:
    if not isinstance(value, str):
        raise OutingStateError(reason)
    text = value.strip()
    if not text or len(text) > MAX_ID_LENGTH:
        raise OutingStateError(reason)
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "\\", "?", "#", " ")):
        raise OutingStateError(reason)
    return text


def _operation_key(value: Any, reason: str = "OPERATION_KEY_INVALID") -> str:
    if not isinstance(value, str):
        raise OutingStateError(reason)
    text = value.strip()
    if not text or len(text) > MAX_OPERATION_KEY or not re.match(r"^[A-Za-z0-9._:\-]+$", text):
        raise OutingStateError(reason)
    return text


def _strict_bool(value: Any, reason: str) -> bool:
    if not isinstance(value, bool):
        raise OutingStateError(reason)
    return bool(value)


def _slot_index(value: Any, reason: str = "WINDOW_SLOT_INVALID") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2:
        raise OutingStateError(reason)
    return int(value)


def _stable_id(prefix: str, *parts: str) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


# ---------------------------------------------------------------------------------------------
# atomic 0600 JSON state files: GET never creates anything; corrupt/symlink fails closed
# ---------------------------------------------------------------------------------------------


def bench_bookstore_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / BENCH_BOOKSTORE_STATE_FILENAME


def routes_market_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / ROUTES_MARKET_STATE_FILENAME


def gallery_blueprint_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / GALLERY_BLUEPRINT_STATE_FILENAME


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent),
                                             text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path: Path) -> tuple[Any, Optional[str]]:
    if path.is_symlink():
        return None, "STATE_SYMLINK"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        return None, "STATE_UNREADABLE"


# ---------------------------------------------------------------------------------------------
# deterministic date rotation & World outdoor place classification
# ---------------------------------------------------------------------------------------------


def _day_seed(local_date: str) -> int:
    text = str(local_date or "").strip()
    try:
        return date.fromisoformat(text).toordinal()
    except ValueError:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big")


def bookstore_window_for_date(local_date: str) -> list[dict[str, Any]]:
    """Deterministically pick 3 distinct featured books (`slot_index` 0..2) for `YYYY-MM-DD`."""

    total = len(BOOKSTORE_CATALOG)
    offset = _day_seed(local_date) % total
    slots: list[dict[str, Any]] = []
    # Step 2 is coprime with 9, so all 3 slots are distinct and rotate smoothly day by day.
    for slot_idx in range(3):
        entry = BOOKSTORE_CATALOG[(offset + slot_idx * 2) % total]
        slots.append({
            "slot_index": slot_idx,
            "book_code": entry["book_code"],
            "title": entry["title"],
            "window_line": entry["window_line"],
            "theme": entry["theme"],
        })
    return slots


def market_stalls_for_date(local_date: str) -> list[dict[str, Any]]:
    """Deterministically pick 3 distinct festival market stalls for `YYYY-MM-DD`."""

    total = len(MARKET_STALL_CATALOG)
    offset = (_day_seed(local_date) * 4) % total
    stalls: list[dict[str, Any]] = []
    for idx in range(3):
        entry = MARKET_STALL_CATALOG[(offset + idx * 2) % total]
        stalls.append({
            "stall_code": entry["stall_code"],
            "title": entry["title"],
            "scene_desc": entry["scene_desc"],
            "suggested_virtual_jpy": int(entry["suggested_virtual_jpy"]),
        })
    return stalls


def _bench_reason_for_place(place_id: str, name: str, kind: str,
                            zone_id: str, description: str) -> Optional[str]:
    lowered_id = f"{place_id} {zone_id} {kind}".lower()
    for token in _BENCH_ID_KEYWORDS:
        if token in lowered_id:
            return f"outdoor_{token}"
    combined = f"{name} {description}"
    for keyword in _BENCH_TEXT_KEYWORDS:
        if keyword in combined:
            return "scenic_outdoor"
    return None


def _is_bookstore_place(place_id: str, name: str) -> bool:
    lowered = place_id.lower()
    if any(token in lowered for token in _BOOKSTORE_ID_KEYWORDS):
        return True
    return any(keyword in name for keyword in _BOOKSTORE_TEXT_KEYWORDS)


def classify_world_places(raw_places: Iterable[Mapping[str, Any]], *,
                          home_place_id: str = "home") -> dict[str, Any]:
    """Extract non-`home` real World places, bench candidates, and bookstore presence."""

    outdoor_places: list[dict[str, Any]] = []
    bench_candidates: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    bookstore_place_id: Optional[str] = None

    for item in raw_places:
        if not isinstance(item, Mapping):
            continue
        pid = str(item.get("place_id") or item.get("location_id") or "").strip()
        if not pid or pid == "home" or pid == str(home_place_id or "home").strip():
            continue
        kind = str(item.get("kind") or "").strip()
        if kind == "residence":
            continue
        if pid in by_id:
            continue
        name = _one_line(item.get("name") or pid, 80) or pid
        zone_id = str(item.get("zone_id") or "").strip()
        description = _one_line(item.get("description") or "", 160)
        bench_reason = _bench_reason_for_place(pid, name, kind, zone_id, description)
        row = {
            "place_id": pid,
            "name": name,
            "kind": kind or "outdoor",
            "is_bench_candidate": bench_reason is not None,
        }
        outdoor_places.append(row)
        by_id[pid] = row
        if bench_reason is not None:
            bench_candidates.append({
                "place_id": pid,
                "name": name,
                "kind": kind or "outdoor",
                "bench_highlight_reason": bench_reason,
            })
        if bookstore_place_id is None and _is_bookstore_place(pid, name):
            bookstore_place_id = pid

    return {
        "outdoor_places": outdoor_places,
        "bench_candidates": bench_candidates,
        "places_by_id": by_id,
        "bookstore_in_world": bookstore_place_id is not None,
        "bookstore_place_id": bookstore_place_id,
    }


# ---------------------------------------------------------------------------------------------
# File 1: outing_bench_bookstore.json (河边长椅 + 书店橱窗)
# ---------------------------------------------------------------------------------------------


def empty_bench_bookstore_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "bench_visits": [],
        "window_bookmarks": [],
        "partner_window_note": None,
    }


def _validate_bench_visit(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    allowed = {"visit_id", "place_id", "note", "shared", "visited_at",
               "updated_at", "operation_key"}
    if set(raw) - allowed:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    visit_id = _local_id(raw.get("visit_id"), "BENCH_BOOKSTORE_STATE_INVALID")
    place_id = _place_id(raw.get("place_id"), "BENCH_BOOKSTORE_STATE_INVALID")
    if place_id == "home":
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    note = _optional_text_field(raw.get("note"), MAX_NOTE, "BENCH_BOOKSTORE_STATE_INVALID")
    shared = _strict_bool(raw.get("shared"), "BENCH_BOOKSTORE_STATE_INVALID")
    visited_at = _iso_text(raw.get("visited_at"))
    updated_at = _iso_text(raw.get("updated_at"))
    op_key = _operation_key(raw.get("operation_key"), "BENCH_BOOKSTORE_STATE_INVALID")
    return {
        "visit_id": visit_id,
        "place_id": place_id,
        "note": note,
        "shared": shared,
        "visited_at": visited_at,
        "updated_at": updated_at,
        "operation_key": op_key,
    }


def _validate_window_bookmark(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    allowed = {"bookmark_id", "slot_index", "book_code", "title", "note",
               "shared", "saved_at", "updated_at"}
    if set(raw) - allowed:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    bookmark_id = _local_id(raw.get("bookmark_id"), "BENCH_BOOKSTORE_STATE_INVALID")
    slot_idx = _slot_index(raw.get("slot_index"), "BENCH_BOOKSTORE_STATE_INVALID")
    book_code = str(raw.get("book_code") or "").strip()
    if book_code not in BOOK_BY_CODE:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    title = _text_field(raw.get("title"), 80, "BENCH_BOOKSTORE_STATE_INVALID")
    note = _optional_text_field(raw.get("note"), MAX_NOTE, "BENCH_BOOKSTORE_STATE_INVALID")
    shared = _strict_bool(raw.get("shared"), "BENCH_BOOKSTORE_STATE_INVALID")
    return {
        "bookmark_id": bookmark_id,
        "slot_index": slot_idx,
        "book_code": book_code,
        "title": title,
        "note": note,
        "shared": shared,
        "saved_at": _iso_text(raw.get("saved_at")),
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_partner_window_note(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    allowed = {"slot_index", "book_code", "title", "note", "partner_contributed", "updated_at"}
    if set(raw) - allowed:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    slot_idx = _slot_index(raw.get("slot_index"), "BENCH_BOOKSTORE_STATE_INVALID")
    book_code = str(raw.get("book_code") or "").strip()
    if book_code not in BOOK_BY_CODE:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    title = _text_field(raw.get("title"), 80, "BENCH_BOOKSTORE_STATE_INVALID")
    note = _text_field(raw.get("note"), MAX_NOTE, "BENCH_BOOKSTORE_STATE_INVALID")
    if raw.get("partner_contributed") is not True:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    return {
        "slot_index": slot_idx,
        "book_code": book_code,
        "title": title,
        "note": note,
        "partner_contributed": True,
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_bench_bookstore_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    allowed = {"schema_version", "revision", "updated_at",
               "bench_visits", "window_bookmarks", "partner_window_note"}
    if set(raw) - allowed:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    raw_visits = raw.get("bench_visits")
    raw_bookmarks = raw.get("window_bookmarks")
    if not isinstance(raw_visits, list) or len(raw_visits) > MAX_BENCH_VISITS:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    if not isinstance(raw_bookmarks, list) or len(raw_bookmarks) > MAX_WINDOW_BOOKMARKS:
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    visits = [_validate_bench_visit(item) for item in raw_visits]
    bookmarks = [_validate_window_bookmark(item) for item in raw_bookmarks]
    if len({v["visit_id"] for v in visits}) != len(visits):
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    if len({v["operation_key"] for v in visits}) != len(visits):
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    if len({b["book_code"] for b in bookmarks}) != len(bookmarks):
        raise OutingStateError("BENCH_BOOKSTORE_STATE_INVALID")
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_text(raw.get("updated_at")),
        "bench_visits": visits,
        "window_bookmarks": bookmarks,
        "partner_window_note": _validate_partner_window_note(raw.get("partner_window_note")),
    }


def read_bench_bookstore_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(bench_bookstore_state_path(root))
    if reason is not None:
        return empty_bench_bookstore_state(), "BENCH_BOOKSTORE_" + reason
    if raw is None:
        return empty_bench_bookstore_state(), None
    try:
        return _validate_bench_bookstore_state(raw), None
    except OutingStateError:
        return empty_bench_bookstore_state(), "BENCH_BOOKSTORE_STATE_INVALID"


def write_bench_bookstore_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    with _tx.state_file_lock(bench_bookstore_state_path(root)):
        normalized = _validate_bench_bookstore_state(dict(state))
        _write_json(bench_bookstore_state_path(root), normalized)
        return normalized


def _bump_bench_bookstore(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_bench_bookstore_state(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def apply_log_bench_visit(
    state: Mapping[str, Any],
    *,
    place_id: str,
    valid_place_ids: Iterable[str],
    operation_key: str,
    note: Optional[str] = None,
    shared: Optional[bool] = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_bench_bookstore_state(dict(state))
    clean_place = _place_id(place_id)
    clean_key = _operation_key(operation_key)
    clean_note = _optional_text_field(note, MAX_NOTE, "BENCH_NOTE_INVALID")
    clean_shared = False if shared is None else _strict_bool(shared, "BENCH_SHARED_INVALID")

    if clean_place == "home" or clean_place not in set(valid_place_ids):
        raise OutingStateError("PLACE_NOT_IN_WORLD", conflict=True)

    for existing in current["bench_visits"]:
        if existing["operation_key"] == clean_key:
            return current, dict(existing), False

    if len(current["bench_visits"]) >= MAX_BENCH_VISITS:
        raise OutingStateError("BENCH_VISITS_FULL", conflict=True)

    visit_id = _stable_id("bench", clean_key)
    entry = {
        "visit_id": visit_id,
        "place_id": clean_place,
        "note": clean_note,
        "shared": clean_shared,
        "visited_at": _iso_text(now_iso),
        "updated_at": _iso_text(now_iso),
        "operation_key": clean_key,
    }
    current["bench_visits"].append(entry)
    return _bump_bench_bookstore(current, now_iso), dict(entry), True


def apply_set_bench_visibility(
    state: Mapping[str, Any],
    *,
    visit_id: str,
    shared: bool,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_bench_bookstore_state(dict(state))
    clean_id = _local_id(visit_id, "BENCH_VISIT_ID_INVALID")
    clean_shared = _strict_bool(shared, "BENCH_SHARED_INVALID")
    found = next((row for row in current["bench_visits"] if row["visit_id"] == clean_id), None)
    if found is None:
        raise OutingStateError("BENCH_VISIT_NOT_FOUND", conflict=True)
    if found["shared"] == clean_shared:
        return current, dict(found), False
    found["shared"] = clean_shared
    found["updated_at"] = _iso_text(now_iso)
    return _bump_bench_bookstore(current, now_iso), dict(found), True


def apply_bookmark_window_book(
    state: Mapping[str, Any],
    *,
    slot_index: int,
    local_date: str,
    shared: Optional[bool] = None,
    note: Optional[str] = None,
    note_supplied: bool = False,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_bench_bookstore_state(dict(state))
    idx = _slot_index(slot_index)
    slots = bookstore_window_for_date(local_date)
    slot_book = slots[idx]
    clean_note = _optional_text_field(note, MAX_NOTE, "WINDOW_NOTE_INVALID")
    if shared is not None:
        shared = _strict_bool(shared, "WINDOW_SHARED_INVALID")

    existing = next((b for b in current["window_bookmarks"]
                     if b["book_code"] == slot_book["book_code"]), None)
    if existing is not None:
        target_shared = existing["shared"] if shared is None else shared
        target_note = clean_note if note_supplied else existing["note"]
        if (existing["slot_index"] == idx
                and existing["shared"] == target_shared
                and existing["note"] == target_note):
            return current, dict(existing), False
        existing["slot_index"] = idx
        existing["shared"] = target_shared
        existing["note"] = target_note
        existing["updated_at"] = _iso_text(now_iso)
        return _bump_bench_bookstore(current, now_iso), dict(existing), True

    if len(current["window_bookmarks"]) >= MAX_WINDOW_BOOKMARKS:
        raise OutingStateError("WINDOW_BOOKMARKS_FULL", conflict=True)

    entry = {
        "bookmark_id": _stable_id("wbk", slot_book["book_code"]),
        "slot_index": idx,
        "book_code": slot_book["book_code"],
        "title": slot_book["title"],
        "note": clean_note,
        "shared": False if shared is None else shared,
        "saved_at": _iso_text(now_iso),
        "updated_at": _iso_text(now_iso),
    }
    current["window_bookmarks"].append(entry)
    return _bump_bench_bookstore(current, now_iso), dict(entry), True


def apply_set_partner_window_note(
    state: Mapping[str, Any],
    *,
    slot_index: int,
    local_date: str,
    note: str,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_bench_bookstore_state(dict(state))
    idx = _slot_index(slot_index)
    clean_note = _text_field(note, MAX_NOTE, "WINDOW_NOTE_INVALID")
    slot_book = bookstore_window_for_date(local_date)[idx]
    existing = current.get("partner_window_note")
    if (isinstance(existing, Mapping)
            and existing.get("slot_index") == idx
            and existing.get("book_code") == slot_book["book_code"]
            and existing.get("note") == clean_note):
        return current, dict(existing), False
    record = {
        "slot_index": idx,
        "book_code": slot_book["book_code"],
        "title": slot_book["title"],
        "note": clean_note,
        "partner_contributed": True,
        "updated_at": _iso_text(now_iso),
    }
    current["partner_window_note"] = record
    return _bump_bench_bookstore(current, now_iso), dict(record), True


def apply_clear_partner_window_note(
    state: Mapping[str, Any],
    *,
    now_iso: str,
) -> tuple[dict[str, Any], bool]:
    current = _validate_bench_bookstore_state(dict(state))
    if current.get("partner_window_note") is None:
        return current, False
    current["partner_window_note"] = None
    return _bump_bench_bookstore(current, now_iso), True


def bench_bookstore_block(
    *,
    state: Mapping[str, Any],
    state_reason: Optional[str],
    world_places: Mapping[str, Any],
    world_available: bool,
    world_reason: Optional[str],
    local_date: str,
    subject_view: bool,
) -> dict[str, Any]:
    current = _validate_bench_bookstore_state(dict(state))
    places_by_id = world_places.get("places_by_id") \
        if isinstance(world_places.get("places_by_id"), Mapping) else {}

    visits_out: list[dict[str, Any]] = []
    private_included = False
    for row in current["bench_visits"]:
        place_info = places_by_id.get(row["place_id"])
        place_name = place_info["name"] if isinstance(place_info, Mapping) else row["place_id"]
        in_world = isinstance(place_info, Mapping)
        if subject_view:
            if not row["shared"] or row.get("note"):
                private_included = True
            visits_out.append({
                "visit_id": row["visit_id"],
                "place_id": row["place_id"],
                "place_name": place_name,
                "in_world": in_world,
                "note": row["note"],
                "visited_at": row["visited_at"],
                "updated_at": row["updated_at"],
                "shared": row["shared"],
                "operation_key": row["operation_key"],
            })
        elif row["shared"]:
            visits_out.append({
                "visit_id": row["visit_id"],
                "place_id": row["place_id"],
                "place_name": place_name,
                "note": row["note"],
                "visited_at": row["visited_at"],
                "shared": True,
            })

    bookmarks_out: list[dict[str, Any]] = []
    for item in current["window_bookmarks"]:
        if subject_view:
            if not item["shared"] or item.get("note"):
                private_included = True
            bookmarks_out.append(dict(item))
        elif item["shared"]:
            bookmarks_out.append({
                "book_code": item["book_code"],
                "title": item["title"],
                "shared": True,
                "updated_at": item["updated_at"],
            })

    featured_slots = bookstore_window_for_date(local_date)
    partner_note = (dict(current["partner_window_note"])
                    if isinstance(current.get("partner_window_note"), Mapping) else None)

    return {
        "schema": BENCH_BOOKSTORE_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {
            "revision": _int_or_none(current.get("revision")),
            "updated_at": current.get("updated_at"),
            "reason_code": state_reason,
        },
        "world_status": {
            "available": bool(world_available),
            "local_date": local_date,
            "reason_code": world_reason,
        },
        "bench": {
            "candidate_places": [dict(p) for p in (world_places.get("bench_candidates") or [])],
            "outdoor_places": [dict(p) for p in (world_places.get("outdoor_places") or [])],
            "visits": visits_out,
            "no_streak_score": True,
            "no_obligation": True,
        },
        "bookstore_window": {
            "local_date": local_date,
            "bookstore_in_world": bool(world_places.get("bookstore_in_world")),
            "bookstore_place_id": world_places.get("bookstore_place_id"),
            "featured_slots": featured_slots,
            "her_bookmarks": bookmarks_out,
            "partner_window_note": partner_note,
            "reading_completed": False,
            "model_calls": False,
        },
        "private_text_included": bool(subject_view and private_included),
        "local_only": True,
        "note": "长椅重访只记留痕不记打卡连击分；书店橱窗按本地日期确定性轮换，"
                "记入待读灵感不等于已读完，也不调用模型。",
    }


# ---------------------------------------------------------------------------------------------
# File 2: outing_routes_market.json (散步路线与随手拍 + 虚拟预算节日集市)
# ---------------------------------------------------------------------------------------------


def empty_routes_market_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "routes": [],
        "market_wishes": [],
        "partner_market_tip": None,
    }


def _validate_place_list(raw: Any, reason: str) -> list[str]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ROUTE_STOPS:
        raise OutingStateError(reason)
    cleaned: list[str] = []
    for item in raw:
        pid = _place_id(item, reason)
        if pid == "home" or pid in cleaned:
            raise OutingStateError(reason)
        cleaned.append(pid)
    return cleaned


def _validate_walk_route(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    allowed = {"route_id", "title", "place_ids", "album_artifact_id",
               "caption", "shared", "created_at", "updated_at"}
    if set(raw) - allowed:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    route_id = _local_id(raw.get("route_id"), "ROUTES_MARKET_STATE_INVALID")
    title = _text_field(raw.get("title"), MAX_ROUTE_TITLE, "ROUTES_MARKET_STATE_INVALID")
    place_ids = _validate_place_list(raw.get("place_ids"), "ROUTES_MARKET_STATE_INVALID")
    album_id = raw.get("album_artifact_id")
    if album_id is not None:
        album_id = _artifact_ref(album_id, "ROUTES_MARKET_STATE_INVALID")
    caption = _optional_text_field(raw.get("caption"), MAX_NOTE, "ROUTES_MARKET_STATE_INVALID")
    shared = _strict_bool(raw.get("shared"), "ROUTES_MARKET_STATE_INVALID")
    return {
        "route_id": route_id,
        "title": title,
        "place_ids": place_ids,
        "album_artifact_id": album_id,
        "caption": caption,
        "shared": shared,
        "created_at": _iso_text(raw.get("created_at")),
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_market_wish(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    allowed = {"wish_id", "stall_code", "stall_title", "memo",
               "budget_cap_jpy", "shared", "updated_at"}
    if set(raw) - allowed:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    wish_id = _local_id(raw.get("wish_id"), "ROUTES_MARKET_STATE_INVALID")
    stall_code = str(raw.get("stall_code") or "").strip()
    if stall_code not in STALL_BY_CODE:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    stall_title = _text_field(raw.get("stall_title"), 80, "ROUTES_MARKET_STATE_INVALID")
    memo = _text_field(raw.get("memo"), MAX_NOTE, "ROUTES_MARKET_STATE_INVALID")
    cap = raw.get("budget_cap_jpy")
    if cap is not None:
        if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= MAX_BUDGET_CAP_JPY:
            raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
        cap = int(cap)
    shared = _strict_bool(raw.get("shared"), "ROUTES_MARKET_STATE_INVALID")
    return {
        "wish_id": wish_id,
        "stall_code": stall_code,
        "stall_title": stall_title,
        "memo": memo,
        "budget_cap_jpy": cap,
        "shared": shared,
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_partner_market_tip(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    allowed = {"stall_code", "tip", "partner_contributed", "updated_at"}
    if set(raw) - allowed:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    stall_code = str(raw.get("stall_code") or "").strip()
    if stall_code not in STALL_BY_CODE:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    tip = _text_field(raw.get("tip"), MAX_NOTE, "ROUTES_MARKET_STATE_INVALID")
    if raw.get("partner_contributed") is not True:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    return {
        "stall_code": stall_code,
        "tip": tip,
        "partner_contributed": True,
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_routes_market_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    allowed = {"schema_version", "revision", "updated_at",
               "routes", "market_wishes", "partner_market_tip"}
    if set(raw) - allowed:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    raw_routes = raw.get("routes")
    raw_wishes = raw.get("market_wishes")
    if not isinstance(raw_routes, list) or len(raw_routes) > MAX_WALK_ROUTES:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    if not isinstance(raw_wishes, list) or len(raw_wishes) > MAX_MARKET_WISHES:
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    routes = [_validate_walk_route(item) for item in raw_routes]
    wishes = [_validate_market_wish(item) for item in raw_wishes]
    if len({r["route_id"] for r in routes}) != len(routes):
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    if len({w["stall_code"] for w in wishes}) != len(wishes):
        raise OutingStateError("ROUTES_MARKET_STATE_INVALID")
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_text(raw.get("updated_at")),
        "routes": routes,
        "market_wishes": wishes,
        "partner_market_tip": _validate_partner_market_tip(raw.get("partner_market_tip")),
    }


def read_routes_market_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(routes_market_state_path(root))
    if reason is not None:
        return empty_routes_market_state(), "ROUTES_MARKET_" + reason
    if raw is None:
        return empty_routes_market_state(), None
    try:
        return _validate_routes_market_state(raw), None
    except OutingStateError:
        return empty_routes_market_state(), "ROUTES_MARKET_STATE_INVALID"


def write_routes_market_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    with _tx.state_file_lock(routes_market_state_path(root)):
        normalized = _validate_routes_market_state(dict(state))
        _write_json(routes_market_state_path(root), normalized)
        return normalized


def _bump_routes_market(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_routes_market_state(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def apply_save_walk_route(
    state: Mapping[str, Any],
    *,
    route_id: Optional[str] = None,
    title: str,
    place_ids: Iterable[Any],
    valid_place_ids: Iterable[str],
    album_artifact_id: Optional[str] = None,
    album_supplied: bool = False,
    caption: Optional[str] = None,
    caption_supplied: bool = False,
    shared: Optional[bool] = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_routes_market_state(dict(state))
    clean_title = _text_field(title, MAX_ROUTE_TITLE, "ROUTE_TITLE_INVALID")
    if not isinstance(place_ids, list) or not 1 <= len(place_ids) <= MAX_ROUTE_STOPS:
        raise OutingStateError("ROUTE_PLACES_INVALID")
    clean_places: list[str] = []
    for raw_pid in place_ids:
        if not isinstance(raw_pid, str) or not raw_pid.strip():
            raise OutingStateError("ROUTE_PLACES_INVALID")
        pid = _place_id(raw_pid, "ROUTE_PLACES_INVALID")
        if pid in clean_places:
            raise OutingStateError("ROUTE_PLACES_DUPLICATED")
        clean_places.append(pid)

    known_world = set(valid_place_ids)
    for pid in clean_places:
        if pid == "home" or pid not in known_world:
            raise OutingStateError("PLACE_NOT_IN_WORLD", conflict=True)

    clean_album: Optional[str] = None
    if album_artifact_id is not None:
        clean_album = _artifact_ref(album_artifact_id, "ALBUM_ID_INVALID")
    clean_caption = _optional_text_field(caption, MAX_NOTE, "ROUTE_CAPTION_INVALID")
    if shared is not None:
        shared = _strict_bool(shared, "ROUTE_SHARED_INVALID")

    resolved_id = (_local_id(route_id, "ROUTE_ID_INVALID")
                   if route_id is not None else _stable_id("route", clean_title))
    existing = next((r for r in current["routes"] if r["route_id"] == resolved_id), None)
    if existing is not None:
        target_album = clean_album if (album_supplied or route_id is None) \
            else existing["album_artifact_id"]
        target_caption = clean_caption if (caption_supplied or route_id is None) \
            else existing["caption"]
        target_shared = existing["shared"] if shared is None else shared
        if (existing["title"] == clean_title
                and existing["place_ids"] == clean_places
                and existing["album_artifact_id"] == target_album
                and existing["caption"] == target_caption
                and existing["shared"] == target_shared):
            return current, dict(existing), False
        existing["title"] = clean_title
        existing["place_ids"] = clean_places
        existing["album_artifact_id"] = target_album
        existing["caption"] = target_caption
        existing["shared"] = target_shared
        existing["updated_at"] = _iso_text(now_iso)
        return _bump_routes_market(current, now_iso), dict(existing), True

    if len(current["routes"]) >= MAX_WALK_ROUTES:
        raise OutingStateError("WALK_ROUTES_FULL", conflict=True)

    entry = {
        "route_id": resolved_id,
        "title": clean_title,
        "place_ids": clean_places,
        "album_artifact_id": clean_album,
        "caption": clean_caption,
        "shared": False if shared is None else shared,
        "created_at": _iso_text(now_iso),
        "updated_at": _iso_text(now_iso),
    }
    current["routes"].append(entry)
    return _bump_routes_market(current, now_iso), dict(entry), True


def apply_remove_walk_route(
    state: Mapping[str, Any],
    *,
    route_id: str,
    now_iso: str,
) -> tuple[dict[str, Any], bool]:
    current = _validate_routes_market_state(dict(state))
    clean_id = _local_id(route_id, "ROUTE_ID_INVALID")
    kept = [r for r in current["routes"] if r["route_id"] != clean_id]
    if len(kept) == len(current["routes"]):
        return current, False
    current["routes"] = kept
    return _bump_routes_market(current, now_iso), True


def apply_note_market_wish(
    state: Mapping[str, Any],
    *,
    stall_code: str,
    memo: str,
    budget_cap_jpy: Optional[int] = None,
    budget_supplied: bool = False,
    shared: Optional[bool] = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_routes_market_state(dict(state))
    code = str(stall_code or "").strip()
    if code not in STALL_BY_CODE:
        raise OutingStateError("MARKET_STALL_INVALID")
    stall = STALL_BY_CODE[code]
    clean_memo = _text_field(memo, MAX_NOTE, "MARKET_MEMO_INVALID")
    clean_cap: Optional[int] = None
    if budget_cap_jpy is not None:
        if (isinstance(budget_cap_jpy, bool) or not isinstance(budget_cap_jpy, int)
                or not 0 <= budget_cap_jpy <= MAX_BUDGET_CAP_JPY):
            raise OutingStateError("MARKET_BUDGET_CAP_INVALID")
        clean_cap = int(budget_cap_jpy)
    if shared is not None:
        shared = _strict_bool(shared, "MARKET_SHARED_INVALID")

    existing = next((w for w in current["market_wishes"] if w["stall_code"] == code), None)
    if existing is not None:
        target_cap = clean_cap if budget_supplied else existing["budget_cap_jpy"]
        target_shared = existing["shared"] if shared is None else shared
        if (existing["memo"] == clean_memo
                and existing["budget_cap_jpy"] == target_cap
                and existing["shared"] == target_shared):
            return current, dict(existing), False
        existing["memo"] = clean_memo
        existing["budget_cap_jpy"] = target_cap
        existing["shared"] = target_shared
        existing["updated_at"] = _iso_text(now_iso)
        return _bump_routes_market(current, now_iso), dict(existing), True

    if len(current["market_wishes"]) >= MAX_MARKET_WISHES:
        raise OutingStateError("MARKET_WISHES_FULL", conflict=True)

    entry = {
        "wish_id": _stable_id("wish", code),
        "stall_code": code,
        "stall_title": stall["title"],
        "memo": clean_memo,
        "budget_cap_jpy": clean_cap,
        "shared": False if shared is None else shared,
        "updated_at": _iso_text(now_iso),
    }
    current["market_wishes"].append(entry)
    return _bump_routes_market(current, now_iso), dict(entry), True


def apply_set_partner_market_tip(
    state: Mapping[str, Any],
    *,
    stall_code: str,
    tip: str,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_routes_market_state(dict(state))
    code = str(stall_code or "").strip()
    if code not in STALL_BY_CODE:
        raise OutingStateError("MARKET_STALL_INVALID")
    clean_tip = _text_field(tip, MAX_NOTE, "MARKET_TIP_INVALID")
    existing = current.get("partner_market_tip")
    if (isinstance(existing, Mapping)
            and existing.get("stall_code") == code
            and existing.get("tip") == clean_tip):
        return current, dict(existing), False
    record = {
        "stall_code": code,
        "tip": clean_tip,
        "partner_contributed": True,
        "updated_at": _iso_text(now_iso),
    }
    current["partner_market_tip"] = record
    return _bump_routes_market(current, now_iso), dict(record), True


def apply_clear_partner_market_tip(
    state: Mapping[str, Any],
    *,
    now_iso: str,
) -> tuple[dict[str, Any], bool]:
    current = _validate_routes_market_state(dict(state))
    if current.get("partner_market_tip") is None:
        return current, False
    current["partner_market_tip"] = None
    return _bump_routes_market(current, now_iso), True


def routes_market_block(
    *,
    state: Mapping[str, Any],
    state_reason: Optional[str],
    world_places: Mapping[str, Any],
    world_available: bool,
    world_reason: Optional[str],
    album_readability: Mapping[str, bool],
    wallet: Mapping[str, Any],
    local_date: str,
    subject_view: bool,
) -> dict[str, Any]:
    current = _validate_routes_market_state(dict(state))
    places_by_id = world_places.get("places_by_id") \
        if isinstance(world_places.get("places_by_id"), Mapping) else {}

    routes_out: list[dict[str, Any]] = []
    private_included = False
    for route in current["routes"]:
        album_id = route.get("album_artifact_id")
        if album_id is None:
            photo_status = "none"
        else:
            photo_status = "available" if bool(album_readability.get(album_id)) else "withheld"

        if subject_view:
            if not route["shared"] or album_id:
                private_included = True
            stops_subject = []
            all_in_world = True
            for pid in route["place_ids"]:
                p_info = places_by_id.get(pid)
                in_world = isinstance(p_info, Mapping)
                if not in_world:
                    all_in_world = False
                stops_subject.append({
                    "place_id": pid,
                    "name": p_info["name"] if in_world else pid,
                    "in_world": in_world,
                })
            routes_out.append({
                "route_id": route["route_id"],
                "title": route["title"],
                "stops": stops_subject,
                "all_places_in_world": all_in_world,
                "album_artifact_id": album_id,
                "photo_status": photo_status,
                "caption": route["caption"],
                "shared": route["shared"],
                "created_at": route["created_at"],
                "updated_at": route["updated_at"],
            })
        elif route["shared"]:
            stops_partner = []
            for pid in route["place_ids"]:
                p_info = places_by_id.get(pid)
                stops_partner.append({
                    "place_id": pid,
                    "name": p_info["name"] if isinstance(p_info, Mapping) else pid,
                })
            routes_out.append({
                "route_id": route["route_id"],
                "title": route["title"],
                "stops": stops_partner,
                "photo_status": photo_status,
                "caption": route["caption"],
                "shared": True,
                "updated_at": route["updated_at"],
            })

    wallet_status = str(wallet.get("wallet_status") or "unavailable")
    balance_jpy = _int_or_none(wallet.get("balance_jpy")) \
        if wallet_status == "available" else None
    wallet_reason = None if wallet_status == "available" \
        else str(wallet.get("reason_code") or GENESIS_NOT_INITIALIZED)

    wishes_out: list[dict[str, Any]] = []
    for wish in current["market_wishes"]:
        cap = wish.get("budget_cap_jpy")
        within_balance: Optional[bool] = None
        if balance_jpy is not None and cap is not None:
            within_balance = cap <= balance_jpy
        if subject_view:
            private_included = True
            wishes_out.append({
                "wish_id": wish["wish_id"],
                "stall_code": wish["stall_code"],
                "stall_title": wish["stall_title"],
                "memo": wish["memo"],
                "budget_cap_jpy": cap,
                "within_current_balance": within_balance,
                "shared": wish["shared"],
                "updated_at": wish["updated_at"],
            })
        elif wish["shared"]:
            wishes_out.append({
                "wish_id": wish["wish_id"],
                "stall_code": wish["stall_code"],
                "stall_title": wish["stall_title"],
                "budget_cap_jpy": cap,
                "shared": True,
                "updated_at": wish["updated_at"],
            })

    partner_tip = (dict(current["partner_market_tip"])
                   if isinstance(current.get("partner_market_tip"), Mapping) else None)

    return {
        "schema": ROUTES_MARKET_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {
            "revision": _int_or_none(current.get("revision")),
            "updated_at": current.get("updated_at"),
            "reason_code": state_reason,
        },
        "world_status": {
            "available": bool(world_available),
            "local_date": local_date,
            "reason_code": world_reason,
        },
        "routes": routes_out,
        "festival_market": {
            "local_date": local_date,
            "wallet_status": wallet_status,
            "balance_jpy": balance_jpy,
            "wallet_reason_code": wallet_reason,
            "currency": "JPY",
            "stalls": market_stalls_for_date(local_date),
            "her_wishes": wishes_out,
            "partner_market_tip": partner_tip,
            "virtual_currency_only": True,
            "does_not_debit_wallet": True,
            "real_money_involved": False,
            "changes_relationship": False,
        },
        "private_text_included": bool(subject_view and private_included),
        "local_only": True,
        "note": "散步路线默认私密，公开时也绝不暴露相册底层 artifact_id；"
                "节日集市只读真实虚拟币余额做量入为出参考，不扣款、不涉真钱或亲密分。",
    }


# ---------------------------------------------------------------------------------------------
# File 3: outing_gallery_blueprint.json (可重访作品展 + 共同新地点草图)
# ---------------------------------------------------------------------------------------------


def empty_gallery_blueprint_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "exhibits": [],
        "blueprints": [],
    }


def _validate_exhibit(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    allowed = {"exhibit_id", "artifact_id", "version_id", "wall_caption",
               "curated_at", "updated_at"}
    if set(raw) - allowed:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    exhibit_id = _local_id(raw.get("exhibit_id"), "GALLERY_BLUEPRINT_STATE_INVALID")
    artifact_id = _artifact_ref(raw.get("artifact_id"), "GALLERY_BLUEPRINT_STATE_INVALID")
    version_id = _artifact_ref(raw.get("version_id"), "GALLERY_BLUEPRINT_STATE_INVALID")
    caption = _optional_text_field(raw.get("wall_caption"), MAX_NOTE,
                                   "GALLERY_BLUEPRINT_STATE_INVALID")
    return {
        "exhibit_id": exhibit_id,
        "artifact_id": artifact_id,
        "version_id": version_id,
        "wall_caption": caption,
        "curated_at": _iso_text(raw.get("curated_at")),
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_blueprint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    allowed = {"blueprint_id", "title", "sketch_note", "intended_use",
               "author_side", "status", "created_at", "updated_at"}
    if set(raw) - allowed:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    blueprint_id = _local_id(raw.get("blueprint_id"), "GALLERY_BLUEPRINT_STATE_INVALID")
    title = _text_field(raw.get("title"), MAX_BLUEPRINT_TITLE,
                        "GALLERY_BLUEPRINT_STATE_INVALID")
    sketch_note = _text_field(raw.get("sketch_note"), MAX_BLUEPRINT_SKETCH,
                              "GALLERY_BLUEPRINT_STATE_INVALID")
    intended_use = _text_field(raw.get("intended_use"), MAX_BLUEPRINT_USE,
                               "GALLERY_BLUEPRINT_STATE_INVALID")
    author_side = raw.get("author_side")
    if author_side not in AUTHOR_SIDES:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    status = raw.get("status")
    if status not in BLUEPRINT_STATUSES:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    return {
        "blueprint_id": blueprint_id,
        "title": title,
        "sketch_note": sketch_note,
        "intended_use": intended_use,
        "author_side": str(author_side),
        "status": str(status),
        "created_at": _iso_text(raw.get("created_at")),
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_gallery_blueprint_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    allowed = {"schema_version", "revision", "updated_at", "exhibits", "blueprints"}
    if set(raw) - allowed:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    raw_exhibits = raw.get("exhibits")
    raw_blueprints = raw.get("blueprints")
    if not isinstance(raw_exhibits, list) or len(raw_exhibits) > MAX_GALLERY_EXHIBITS:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    if not isinstance(raw_blueprints, list) or len(raw_blueprints) > MAX_STORED_BLUEPRINTS:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    exhibits = [_validate_exhibit(item) for item in raw_exhibits]
    blueprints = [_validate_blueprint(item) for item in raw_blueprints]
    if len({e["exhibit_id"] for e in exhibits}) != len(exhibits):
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    if len({(e["artifact_id"], e["version_id"]) for e in exhibits}) != len(exhibits):
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    if len({b["blueprint_id"] for b in blueprints}) != len(blueprints):
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    active_count = sum(1 for b in blueprints if b["status"] == "ACTIVE")
    if active_count > MAX_ACTIVE_BLUEPRINTS:
        raise OutingStateError("GALLERY_BLUEPRINT_STATE_INVALID")
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_text(raw.get("updated_at")),
        "exhibits": exhibits,
        "blueprints": blueprints,
    }


def read_gallery_blueprint_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(gallery_blueprint_state_path(root))
    if reason is not None:
        return empty_gallery_blueprint_state(), "GALLERY_BLUEPRINT_" + reason
    if raw is None:
        return empty_gallery_blueprint_state(), None
    try:
        return _validate_gallery_blueprint_state(raw), None
    except OutingStateError:
        return empty_gallery_blueprint_state(), "GALLERY_BLUEPRINT_STATE_INVALID"


def write_gallery_blueprint_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    with _tx.state_file_lock(gallery_blueprint_state_path(root)):
        normalized = _validate_gallery_blueprint_state(dict(state))
        _write_json(gallery_blueprint_state_path(root), normalized)
        return normalized


def _bump_gallery_blueprint(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_gallery_blueprint_state(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def apply_curate_exhibit(
    state: Mapping[str, Any],
    *,
    artifact_id: str,
    version_id: str,
    wall_caption: Optional[str] = None,
    caption_supplied: bool = False,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_gallery_blueprint_state(dict(state))
    clean_artifact = _artifact_ref(artifact_id, "ARTIFACT_ID_INVALID")
    clean_version = _artifact_ref(version_id, "VERSION_ID_INVALID")
    clean_caption = _optional_text_field(wall_caption, MAX_NOTE, "WALL_CAPTION_INVALID")

    existing = next((e for e in current["exhibits"]
                     if e["artifact_id"] == clean_artifact
                     and e["version_id"] == clean_version), None)
    if existing is not None:
        target_caption = clean_caption if caption_supplied else existing["wall_caption"]
        if existing["wall_caption"] == target_caption:
            return current, dict(existing), False
        existing["wall_caption"] = target_caption
        existing["updated_at"] = _iso_text(now_iso)
        return _bump_gallery_blueprint(current, now_iso), dict(existing), True

    if len(current["exhibits"]) >= MAX_GALLERY_EXHIBITS:
        raise OutingStateError("GALLERY_FULL", conflict=True)

    entry = {
        "exhibit_id": _stable_id("exhibit", clean_artifact, clean_version),
        "artifact_id": clean_artifact,
        "version_id": clean_version,
        "wall_caption": clean_caption,
        "curated_at": _iso_text(now_iso),
        "updated_at": _iso_text(now_iso),
    }
    current["exhibits"].append(entry)
    return _bump_gallery_blueprint(current, now_iso), dict(entry), True


def apply_remove_exhibit(
    state: Mapping[str, Any],
    *,
    exhibit_id: str,
    now_iso: str,
) -> tuple[dict[str, Any], bool]:
    current = _validate_gallery_blueprint_state(dict(state))
    clean_id = _local_id(exhibit_id, "EXHIBIT_ID_INVALID")
    kept = [e for e in current["exhibits"] if e["exhibit_id"] != clean_id]
    if len(kept) == len(current["exhibits"]):
        return current, False
    current["exhibits"] = kept
    return _bump_gallery_blueprint(current, now_iso), True


def apply_upsert_blueprint(
    state: Mapping[str, Any],
    *,
    blueprint_id: Optional[str] = None,
    title: str,
    sketch_note: str,
    intended_use: str,
    author_side: str,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_gallery_blueprint_state(dict(state))
    if author_side not in AUTHOR_SIDES:
        raise OutingStateError("BLUEPRINT_AUTHOR_INVALID")
    clean_title = _text_field(title, MAX_BLUEPRINT_TITLE, "BLUEPRINT_TITLE_INVALID")
    clean_sketch = _text_field(sketch_note, MAX_BLUEPRINT_SKETCH, "BLUEPRINT_SKETCH_INVALID")
    clean_use = _text_field(intended_use, MAX_BLUEPRINT_USE, "BLUEPRINT_USE_INVALID")

    resolved_id = (_local_id(blueprint_id, "BLUEPRINT_ID_INVALID")
                   if blueprint_id is not None
                   else _stable_id("bp", author_side, clean_title))
    existing = next((b for b in current["blueprints"] if b["blueprint_id"] == resolved_id), None)
    active_count = sum(1 for b in current["blueprints"] if b["status"] == "ACTIVE")

    if existing is not None:
        if existing["author_side"] != author_side:
            raise OutingStateError("AUTHOR_MISMATCH", conflict=True)
        if existing["status"] == "ARCHIVED" and active_count >= MAX_ACTIVE_BLUEPRINTS:
            raise OutingStateError("BLUEPRINTS_FULL", conflict=True)
        if (existing["status"] == "ACTIVE"
                and existing["title"] == clean_title
                and existing["sketch_note"] == clean_sketch
                and existing["intended_use"] == clean_use):
            return current, dict(existing), False
        existing["title"] = clean_title
        existing["sketch_note"] = clean_sketch
        existing["intended_use"] = clean_use
        existing["status"] = "ACTIVE"
        existing["updated_at"] = _iso_text(now_iso)
        return _bump_gallery_blueprint(current, now_iso), dict(existing), True

    if active_count >= MAX_ACTIVE_BLUEPRINTS:
        raise OutingStateError("BLUEPRINTS_FULL", conflict=True)
    if len(current["blueprints"]) >= MAX_STORED_BLUEPRINTS:
        # Evict the oldest archived card only if the bounded archive history is full.
        oldest_archived = next(
            (idx for idx, b in enumerate(current["blueprints"]) if b["status"] == "ARCHIVED"),
            None,
        )
        if oldest_archived is None:
            raise OutingStateError("BLUEPRINTS_FULL", conflict=True)
        current["blueprints"].pop(oldest_archived)

    entry = {
        "blueprint_id": resolved_id,
        "title": clean_title,
        "sketch_note": clean_sketch,
        "intended_use": clean_use,
        "author_side": str(author_side),
        "status": "ACTIVE",
        "created_at": _iso_text(now_iso),
        "updated_at": _iso_text(now_iso),
    }
    current["blueprints"].append(entry)
    return _bump_gallery_blueprint(current, now_iso), dict(entry), True


def apply_archive_blueprint(
    state: Mapping[str, Any],
    *,
    blueprint_id: str,
    author_side: str,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_gallery_blueprint_state(dict(state))
    if author_side not in AUTHOR_SIDES:
        raise OutingStateError("BLUEPRINT_AUTHOR_INVALID")
    clean_id = _local_id(blueprint_id, "BLUEPRINT_ID_INVALID")
    existing = next((b for b in current["blueprints"] if b["blueprint_id"] == clean_id), None)
    if existing is None:
        raise OutingStateError("BLUEPRINT_NOT_FOUND", conflict=True)
    if existing["author_side"] != author_side:
        raise OutingStateError("AUTHOR_MISMATCH", conflict=True)
    if existing["status"] == "ARCHIVED":
        return current, dict(existing), False
    existing["status"] = "ARCHIVED"
    existing["updated_at"] = _iso_text(now_iso)
    return _bump_gallery_blueprint(current, now_iso), dict(existing), True


def format_exhibit_projection(
    exhibit: Mapping[str, Any],
    *,
    subject_view: bool,
    source_status: str,
    reason_code: Optional[str] = None,
    title: Optional[str] = None,
    version_number: Optional[int] = None,
    owner_content: Optional[str] = None,
    delivered_share_id: Optional[str] = None,
    delivered_text: Optional[str] = None,
    delivered_at: Optional[str] = None,
) -> dict[str, Any]:
    """Project one gallery exhibit for `subject` or `partner_default` view."""

    basis = OWNER_EXCERPT_BASIS if subject_view else DELIVERED_SHARE_BASIS
    if source_status != "available":
        row: dict[str, Any] = {
            "exhibit_id": exhibit.get("exhibit_id"),
            "status": "withheld",
            "reason_code": str(reason_code or SOURCE_NOT_READABLE),
            "excerpt_basis": basis,
            "excerpt": None,
            "wall_caption": None,
            "delivered_share_id": None,
            "curated_at": exhibit.get("curated_at"),
        }
        if subject_view:
            row["artifact_id"] = exhibit.get("artifact_id")
            row["version_id"] = exhibit.get("version_id")
            row["version_number"] = _int_or_none(version_number)
        return row

    if subject_view:
        raw_text = str(owner_content or "")
        excerpt = _bounded_body(raw_text, MAX_GALLERY_EXCERPT)
        return {
            "exhibit_id": exhibit.get("exhibit_id"),
            "artifact_id": exhibit.get("artifact_id"),
            "version_id": exhibit.get("version_id"),
            "version_number": _int_or_none(version_number),
            "title": _one_line(title or "", 80) or None,
            "status": "available",
            "reason_code": None,
            "excerpt_basis": OWNER_EXCERPT_BASIS,
            "excerpt": excerpt,
            "excerpt_truncated": len(raw_text) > MAX_GALLERY_EXCERPT,
            "wall_caption": exhibit.get("wall_caption"),
            "delivered_share_id": None,
            "curated_at": exhibit.get("curated_at"),
        }

    raw_share = str(delivered_text or "")
    excerpt = _bounded_body(raw_share, MAX_GALLERY_EXCERPT)
    return {
        "exhibit_id": exhibit.get("exhibit_id"),
        "artifact_id": exhibit.get("artifact_id"),
        "version_id": exhibit.get("version_id"),
        "version_number": _int_or_none(version_number),
        "title": _one_line(title or "", 80) or None,
        "status": "available",
        "reason_code": None,
        "excerpt_basis": DELIVERED_SHARE_BASIS,
        "excerpt": excerpt,
        "excerpt_truncated": len(raw_share) > MAX_GALLERY_EXCERPT,
        "wall_caption": exhibit.get("wall_caption"),
        "delivered_share_id": str(delivered_share_id or ""),
        "delivered_at": _iso_text(delivered_at),
        "curated_at": exhibit.get("curated_at"),
    }


def gallery_blueprint_block(
    *,
    state: Mapping[str, Any],
    state_reason: Optional[str],
    exhibits_projected: list[dict[str, Any]],
    subject_view: bool,
) -> dict[str, Any]:
    current = _validate_gallery_blueprint_state(dict(state))
    cards = [dict(card) for card in current["blueprints"]]
    active_cards = [card for card in cards if card["status"] == "ACTIVE"]
    available_exhibits = sum(1 for item in exhibits_projected if item.get("status") == "available")
    withheld_exhibits = len(exhibits_projected) - available_exhibits

    return {
        "schema": GALLERY_BLUEPRINT_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {
            "revision": _int_or_none(current.get("revision")),
            "updated_at": current.get("updated_at"),
            "reason_code": state_reason,
        },
        "gallery": {
            "exhibits": exhibits_projected,
            "available_count": available_exhibits,
            "withheld_count": withheld_exhibits,
            "excerpt_basis": OWNER_EXCERPT_BASIS if subject_view else DELIVERED_SHARE_BASIS,
            "max_exhibits": MAX_GALLERY_EXHIBITS,
            "new_share_created": False,
            "permission_granted": False,
        },
        "place_blueprints": {
            "cards": cards,
            "active_count": len(active_cards),
            "max_active_cards": MAX_ACTIVE_BLUEPRINTS,
            "blueprint_only": True,
            "does_not_mutate_world_topology": True,
            "no_obligation": True,
        },
        "private_text_included": bool(subject_view and available_exhibits > 0),
        "local_only": True,
        "note": "小展廊本人读真实版本摘要，伴侣只读该精确版本已本地投递的分享正文；"
                "共同新地点草图只存双人蓝图与用途设想，绝不改动真实 World 拓扑。",
    }


__all__ = [
    "ARCHIVED_SOURCE",
    "AUTHOR_SIDES",
    "BENCH_BOOKSTORE_SCHEMA",
    "BENCH_BOOKSTORE_STATE_FILENAME",
    "BLUEPRINT_STATUSES",
    "BOOKSTORE_CATALOG",
    "BOOK_BY_CODE",
    "DELIVERED_SHARE_BASIS",
    "ECONOMY_STATE_ERROR",
    "ECONOMY_UNAVAILABLE",
    "GALLERY_BLUEPRINT_SCHEMA",
    "GALLERY_BLUEPRINT_STATE_FILENAME",
    "GENESIS_NOT_INITIALIZED",
    "MARKET_STALL_CATALOG",
    "MAX_ACTIVE_BLUEPRINTS",
    "MAX_BENCH_VISITS",
    "MAX_BLUEPRINT_SKETCH",
    "MAX_BLUEPRINT_TITLE",
    "MAX_BLUEPRINT_USE",
    "MAX_GALLERY_EXCERPT",
    "MAX_GALLERY_EXHIBITS",
    "MAX_MARKET_WISHES",
    "MAX_NOTE",
    "MAX_ROUTE_STOPS",
    "MAX_ROUTE_TITLE",
    "MAX_WALK_ROUTES",
    "MAX_WINDOW_BOOKMARKS",
    "NOT_A_PROJECT",
    "OWNER_EXCERPT_BASIS",
    "OutingStateError",
    "ROUTES_MARKET_SCHEMA",
    "ROUTES_MARKET_STATE_FILENAME",
    "SHARE_ENVELOPE_NOT_A_PROJECT",
    "SOURCE_NOT_READABLE",
    "STALL_BY_CODE",
    "STALL_CODES",
    "VERSION_NOT_DELIVERED",
    "VERSION_NOT_IN_ARTIFACT",
    "VERSION_UNREADABLE",
    "apply_archive_blueprint",
    "apply_bookmark_window_book",
    "apply_clear_partner_market_tip",
    "apply_clear_partner_window_note",
    "apply_curate_exhibit",
    "apply_log_bench_visit",
    "apply_note_market_wish",
    "apply_remove_exhibit",
    "apply_remove_walk_route",
    "apply_save_walk_route",
    "apply_set_bench_visibility",
    "apply_set_partner_market_tip",
    "apply_set_partner_window_note",
    "apply_upsert_blueprint",
    "bench_bookstore_block",
    "bench_bookstore_state_path",
    "bookstore_window_for_date",
    "classify_world_places",
    "empty_bench_bookstore_state",
    "empty_gallery_blueprint_state",
    "empty_routes_market_state",
    "format_exhibit_projection",
    "gallery_blueprint_block",
    "gallery_blueprint_state_path",
    "market_stalls_for_date",
    "read_bench_bookstore_state",
    "read_gallery_blueprint_state",
    "read_routes_market_state",
    "routes_market_block",
    "routes_market_state_path",
    "write_bench_bookstore_state",
    "write_gallery_blueprint_state",
    "write_routes_market_state",
]
