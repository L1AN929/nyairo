#!/usr/bin/env python3
"""Universal open-world storefront: shop scopes, tier price ranges & open-counter naming.

Design rule
-----------
Item **names** never need programmer hard-coding and work in two complementary ways:

1. **Open-counter slots (``shop_slot:<location>:<category>:<price_tier>``)** exposed by every
   commercial venue according to its business scope (allowed categories + tier price ranges
   ``[price_min, price_max]``). Chiyo (in her regular decision) or the user's tool call names
   the concrete item on the spot within the shop's legal categories.
2. **World local signature items (``shop_item:*``)** optionally declared in World Editor
   (``sale_spec``), WorldGen ``content.json["goods"]``, or automatically extracted from a
   package's place descriptions and fixture notes.

Prices stay bounded inside deterministic backend ranges (``TIER_PRICE_RANGES_JPY``), while
physical capabilities (``fixed`` vs handheld) and hunger/thirst relief (``TIER_NEED_EFFECTS``)
remain governed strictly by backend ``(category, price_tier)`` contracts and World opening
hours -- zero extra LLM calls, zero writes, and 100% compatible with bare ``shiomi_city``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

CURRENCY = "JPY"

COMMERCIAL_LOCATION_KINDS = frozenset({"store", "cafe", "market", "shop"})
SALE_CATEGORIES = ("food", "ingredient", "drink", "reading", "daily", "furnish")
PRICE_TIERS = ("budget", "standard", "hearty")

TIER_PRICE_RANGES_JPY: dict[tuple[str, str], tuple[int, int]] = {
    ("drink", "budget"): (110, 240),
    ("drink", "standard"): (320, 480),
    ("drink", "hearty"): (500, 760),
    ("food", "budget"): (150, 320),
    ("food", "standard"): (380, 650),
    ("food", "hearty"): (680, 1080),
    ("ingredient", "budget"): (120, 300),
    ("ingredient", "standard"): (350, 650),
    ("ingredient", "hearty"): (700, 1280),
    ("reading", "budget"): (220, 480),
    ("reading", "standard"): (520, 920),
    ("reading", "hearty"): (980, 1680),
    ("daily", "budget"): (150, 380),
    ("daily", "standard"): (420, 820),
    ("daily", "hearty"): (850, 1580),
    ("furnish", "budget"): (780, 1350),
    ("furnish", "standard"): (1500, 2600),
    ("furnish", "hearty"): (2800, 4800),
}


def _round_to_10(value: int) -> int:
    return int(((int(value) + 5) // 10) * 10)


TIER_PRICES_JPY: dict[tuple[str, str], int] = {
    key: _round_to_10((lo + hi) // 2)
    for key, (lo, hi) in TIER_PRICE_RANGES_JPY.items()
}

TIER_NEED_EFFECTS: dict[tuple[str, str], dict[str, float]] = {
    ("food", "budget"): {"hunger": 0.45},
    ("food", "standard"): {"hunger": 0.75},
    ("food", "hearty"): {"hunger": 0.95},
    ("ingredient", "budget"): {"hunger": 0.45},
    ("ingredient", "standard"): {"hunger": 0.75},
    ("ingredient", "hearty"): {"hunger": 0.95},
    ("drink", "budget"): {"thirst": 0.55},
    ("drink", "standard"): {"thirst": 0.80},
    ("drink", "hearty"): {"thirst": 0.95},
}

_CAPABILITY_FIXED: dict[str, Any] = {
    "takeable": False,
    "portable": False,
    "placeable": True,
    "fixed": True,
}
_CAPABILITY_HANDHELD: dict[str, Any] = {
    "takeable": True,
    "portable": True,
    "placeable": True,
    "fixed": False,
}

_SHOP_ITEM_ID_RE = re.compile(
    r"^shop_item:[A-Za-z0-9._-]+:(food|ingredient|drink|reading|daily|furnish):[0-9a-f]{8}$"
)
_SHOP_SLOT_ID_RE = re.compile(
    r"^shop_slot:[A-Za-z0-9._-]+:(food|ingredient|drink|reading|daily|furnish):(budget|standard|hearty)$"
)
_CUSTOM_PRICE_MIN = 10
_CUSTOM_PRICE_MAX = 50_000
_MAX_SHELF_ITEMS = 10
_MAX_CUSTOM_ITEM_NAME_CHARS = 32
_MAX_TEXT = 120

_EDITOR_WORLD_FILENAMES = (
    "nyairo-world.json",
    "edited_world.json",
    "editor-world.json",
    "editor_package.json",
)

# ---------------------------------------------------------------------------
# Pure category / tier / open-slot contract helpers
# ---------------------------------------------------------------------------


def _safe_loc_slug(location_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(location_id or "").strip()).strip("_")[:48] or "shop"


def parse_shop_slot_id(catalog_item_id: Any) -> Optional[tuple[str, str, str]]:
    """Return ``(loc_slug, category, price_tier)`` for an open ``shop_slot:*`` ID."""

    text = str(catalog_item_id or "").strip()
    if not _SHOP_SLOT_ID_RE.fullmatch(text):
        return None
    _prefix, loc_slug, category, price_tier = text.split(":", 3)
    return loc_slug, category, price_tier


def stable_shop_slot_id(location_id: str, category: str, price_tier: str) -> str:
    """Deterministic economy catalog ID for an open shop category/tier slot."""

    cat = category if category in SALE_CATEGORIES else "daily"
    tier = price_tier if price_tier in PRICE_TIERS else "standard"
    return f"shop_slot:{_safe_loc_slug(location_id)}:{cat}:{tier}"


def parse_shop_item_id(catalog_item_id: Any) -> Optional[tuple[str, str, str]]:
    """Return ``(loc_slug, category, tail)`` for dynamic ``shop_item:*`` or ``shop_slot:*`` IDs."""

    text = str(catalog_item_id or "").strip()
    if _SHOP_ITEM_ID_RE.fullmatch(text) or _SHOP_SLOT_ID_RE.fullmatch(text):
        _prefix, loc_slug, category, tail = text.split(":", 3)
        return loc_slug, category, tail
    return None


def stable_shop_item_id(location_id: str, name: str, category: str) -> str:
    """Deterministic economy catalog ID encoding location slug + validated category."""

    cat = category if category in SALE_CATEGORIES else "daily"
    safe_loc = _safe_loc_slug(location_id)
    digest = hashlib.sha256(f"{location_id}|{cat}|{name}".encode("utf-8")).hexdigest()[:8]
    return f"shop_item:{safe_loc}:{cat}:{digest}"


def capability_for_category(category: str) -> dict[str, Any]:
    if category == "furnish":
        return dict(_CAPABILITY_FIXED)
    if category in SALE_CATEGORIES:
        return dict(_CAPABILITY_HANDHELD)
    return {}


def declaration_for_category(category: str) -> dict[str, Any]:
    if category == "furnish":
        return {"surfaces": (), "relocatable": True, "usable_kind": "FURNISH"}
    if category in SALE_CATEGORIES:
        return {"surfaces": (), "relocatable": False, "usable_kind": "CARRY"}
    return {}


def consumable_declaration_for(category: str, price_tier: str) -> dict[str, Any]:
    effects = TIER_NEED_EFFECTS.get((category, price_tier))
    if not effects:
        return {}
    return {
        "consumable": True,
        "free_to_consume": False,
        "consumption_permit": "owned_purchase",
        "lived_need_effects": dict(effects),
    }


def price_range_for(category: str, price_tier: str) -> tuple[int, int]:
    return TIER_PRICE_RANGES_JPY.get(
        (category, price_tier),
        TIER_PRICE_RANGES_JPY[("daily", "standard")],
    )


def resolve_item_price_jpy(
    *,
    location_id: str,
    category: str,
    price_tier: str,
    item_name: str = "",
    custom_price: Optional[int] = None,
    estimated_price: Optional[int] = None,
) -> int:
    """Resolve a bounded price in JPY from explicit price, clamped estimate, or hashed name."""

    if (
        isinstance(custom_price, int)
        and not isinstance(custom_price, bool)
        and _CUSTOM_PRICE_MIN <= custom_price <= _CUSTOM_PRICE_MAX
    ):
        return custom_price
    lo, hi = price_range_for(category, price_tier)
    if isinstance(estimated_price, int) and not isinstance(estimated_price, bool):
        clamped = max(lo, min(hi, estimated_price))
        return max(lo, min(hi, _round_to_10(clamped)))
    clean_name = " ".join(str(item_name or "").split())
    if not clean_name:
        return int(TIER_PRICES_JPY.get((category, price_tier), lo))
    steps = max(1, ((hi - lo) // 10) + 1)
    digest = hashlib.sha256(
        f"{location_id}|{category}|{price_tier}|{clean_name}".encode("utf-8")
    ).digest()
    idx = int.from_bytes(digest[:4], "big") % steps
    return min(hi, lo + idx * 10)


def price_for(category: str, price_tier: str, custom_price: Optional[int] = None) -> int:
    if (
        isinstance(custom_price, int)
        and not isinstance(custom_price, bool)
        and _CUSTOM_PRICE_MIN <= custom_price <= _CUSTOM_PRICE_MAX
    ):
        return custom_price
    return int(TIER_PRICES_JPY.get((category, price_tier), TIER_PRICES_JPY[("daily", "standard")]))


def normalize_sale_spec(raw: Any) -> Optional[dict[str, Any]]:
    """Leniently normalize an explicit ``sale_spec`` dict, or ``None`` if invalid."""

    if not isinstance(raw, Mapping):
        return None
    category = str(raw.get("category") or "").strip()
    price_tier = str(raw.get("price_tier") or "standard").strip()
    if category not in SALE_CATEGORIES or price_tier not in PRICE_TIERS:
        return None
    spec: dict[str, Any] = {"category": category, "price_tier": price_tier}
    if raw.get("price") is not None:
        raw_price = raw["price"]
        if (
            isinstance(raw_price, bool)
            or not isinstance(raw_price, int)
            or not _CUSTOM_PRICE_MIN <= raw_price <= _CUSTOM_PRICE_MAX
        ):
            return None
        spec["price"] = raw_price
    return spec


def build_item_meta(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Compact metadata persisted with an owned dynamic item instance."""

    category = str(entry.get("category") or "daily")
    if category not in SALE_CATEGORIES:
        category = "daily"
    price_tier = str(entry.get("price_tier") or "standard")
    if price_tier not in PRICE_TIERS:
        price_tier = "standard"
    name = " ".join(str(entry.get("name") or "").split())[:80] or "商店物件"
    store_id = str(entry.get("store_id") or "").strip()[:96]
    meta: dict[str, Any] = {
        "name": name,
        "category": category,
        "price_tier": price_tier,
    }
    if store_id:
        meta["store_id"] = store_id
    return meta


def validate_item_meta(raw: Any) -> tuple[bool, dict[str, Any]]:
    """Strict validator for ``world_property_economy._validate_item``."""

    if not isinstance(raw, Mapping):
        return False, {}
    allowed = frozenset({"name", "category", "price_tier", "store_id"})
    if not set(raw).issubset(allowed):
        return False, {}
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        return False, {}
    if any(ch in name for ch in "\r\n\x00"):
        return False, {}
    category = raw.get("category")
    price_tier = raw.get("price_tier")
    if category not in SALE_CATEGORIES or price_tier not in PRICE_TIERS:
        return False, {}
    cleaned: dict[str, Any] = {
        "name": name.strip(),
        "category": category,
        "price_tier": price_tier,
    }
    if "store_id" in raw and raw["store_id"] is not None:
        sid = raw["store_id"]
        if not isinstance(sid, str) or not sid.strip() or len(sid.strip()) > 96:
            return False, {}
        if any(ch in sid for ch in "\r\n\x00"):
            return False, {}
        cleaned["store_id"] = sid.strip()
    return True, cleaned


# ---------------------------------------------------------------------------
# Zero-write package & editor world discovery
# ---------------------------------------------------------------------------


def _read_json_dict(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _is_package_dir(path: Path) -> bool:
    return (path / "topology.json").is_file() or (path / "objects.json").is_file()


def _resolve_day_root_zero_write(home_path: Path) -> Path:
    try:
        import sys

        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import instance_paths as _ip

        return _ip.resolve_instance_paths(home_path).day_root
    except Exception:
        return home_path


def _dynamic_catalog_module() -> Any:
    try:
        import sys

        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import world_content_catalog as _wcc

        return _wcc
    except Exception:
        return None


def _has_dynamic_catalog_file(day_root: Optional[Path]) -> bool:
    if day_root is None:
        return False
    try:
        if day_root.is_symlink():
            return False
        world_dir = day_root / "world"
        if world_dir.is_symlink():
            return False
        cat_file = world_dir / "dynamic_content_catalog.json"
        return cat_file.is_file() and not cat_file.is_symlink()
    except OSError:
        return False


def _dynamic_catalog_shelf_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    loc_id = str(item.get("store_id") or "").strip()
    cat = str(item.get("category") or "daily")
    if cat not in SALE_CATEGORIES:
        cat = "daily"
    tier = str(item.get("price_tier") or "standard")
    if tier not in PRICE_TIERS:
        tier = "standard"
    name = str(item.get("name_cn") or item.get("name") or "商店物件")
    desc = str(item.get("description_cn") or item.get("description") or f"在售的{name}")
    price = int(item.get("price") or price_for(cat, tier))
    raw_decl = item.get("declaration") if isinstance(item.get("declaration"), Mapping) else declaration_for_category(cat)
    decl = dict(raw_decl)
    decl["surfaces"] = tuple(decl.get("surfaces") or ())
    if "usable_kind" not in decl:
        decl["usable_kind"] = str(item.get("usable_kind") or declaration_for_category(cat).get("usable_kind", "CARRY"))
    if "relocatable" not in decl:
        decl["relocatable"] = bool(cat == "furnish")
    cap = dict(item.get("capability") or capability_for_category(cat))
    consumable = (
        dict(item.get("consumable_declaration"))
        if isinstance(item.get("consumable_declaration"), Mapping)
        else consumable_declaration_for(cat, tier)
    )
    stock_rem = int(item.get("stock_remaining", 0))
    entry: dict[str, Any] = {
        "catalog_item_id": str(item.get("catalog_item_id") or stable_shop_item_id(loc_id, name, cat)),
        "name": name,
        "name_cn": name,
        "description": desc,
        "price": price,
        "price_min": price,
        "price_max": price,
        "custom_price": price,
        "currency": CURRENCY,
        "category": cat,
        "price_tier": tier,
        "store_id": loc_id,
        "usable_kind": str(item.get("usable_kind") or decl.get("usable_kind", "CARRY")),
        "capability": cap,
        "declaration": decl,
        "consumable_declaration": consumable,
        "channels": (loc_id,),
        "is_open_store_item": True,
        "is_dynamic_catalog_item": True,
        "open_order": False,
        "template_id": str(item.get("template_id") or ""),
        "stock_initial": int(item.get("stock_initial", stock_rem)),
        "stock_remaining": stock_rem,
        "out_of_stock": stock_rem <= 0,
    }
    if isinstance(item.get("matter_profile"), Mapping):
        entry["matter_profile"] = dict(item["matter_profile"])
    if isinstance(item.get("reusable_container_spec"), Mapping):
        entry["reusable_container_spec"] = dict(item["reusable_container_spec"])
    if item.get("light_band") is not None:
        entry["light_band"] = item["light_band"]
    return entry


def resolve_world_sources(
    hermes_home: Optional[Path | str] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> Optional[dict[str, Any]]:
    """Locate active world package / editor world / dynamic catalog cleanly with zero writes.

    Returns ``None`` when neither a packaged world nor an editor world nor a
    ``dynamic_content_catalog.json`` file is bound (preserving legacy built-in
    ``shiomi_city`` test behaviour byte-for-byte).
    """

    env = environment if environment is not None else os.environ
    home_path: Optional[Path] = None
    if hermes_home is not None:
        try:
            home_path = Path(hermes_home).expanduser()
        except (TypeError, ValueError):
            home_path = None
    elif env.get("HERMES_HOME"):
        home_path = Path(str(env.get("HERMES_HOME"))).expanduser()

    resolved_day_root: Optional[Path] = (
        _resolve_day_root_zero_write(home_path) if home_path is not None else None
    )
    has_dynamic_catalog = _has_dynamic_catalog_file(resolved_day_root)

    package_dir: Optional[Path] = None
    env_pkg = str(env.get("CHIYO_WORLD_PACKAGE") or "").strip()
    if env_pkg:
        cand = Path(env_pkg).expanduser()
        if cand.is_dir() and _is_package_dir(cand):
            package_dir = cand

    if package_dir is None and home_path is not None:
        meta = _read_json_dict(home_path / "instance.json")
        if meta and isinstance(meta.get("package_dir"), str):
            cand = Path(meta["package_dir"]).expanduser()
            if cand.is_dir() and _is_package_dir(cand):
                package_dir = cand

    if package_dir is None and home_path is not None:
        candidates = (
            home_path / "world-package",
            home_path / "world_package",
            home_path.parent / "world-package",
            home_path.parent / "world_package",
        )
        for cand in candidates:
            if cand.is_dir() and _is_package_dir(cand):
                package_dir = cand
                break

    editor_doc: Optional[dict[str, Any]] = None
    search_dirs: list[Path] = []
    if home_path is not None:
        search_dirs.extend(
            (
                home_path,
                home_path / "world",
                home_path / "world-package",
                home_path / "world_package",
                home_path.parent,
            )
        )
    if package_dir is not None:
        search_dirs.append(package_dir)
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for filename in _EDITOR_WORLD_FILENAMES:
            loaded = _read_json_dict(directory / filename)
            if loaded and (isinstance(loaded.get("rooms"), list) or isinstance(loaded.get("objects"), list)):
                editor_doc = loaded
                break
        if editor_doc is not None:
            break

    if package_dir is None and editor_doc is None and not has_dynamic_catalog:
        return None

    locations: dict[str, dict[str, Any]] = {}
    content_entries: dict[str, Any] = {}
    package_objects: list[dict[str, Any]] = []
    has_rules = False

    if package_dir is not None:
        topo = _read_json_dict(package_dir / "topology.json")
        if topo and isinstance(topo.get("locations"), list):
            for loc in topo["locations"]:
                if isinstance(loc, Mapping) and loc.get("location_id"):
                    locations[str(loc["location_id"])] = dict(loc)
        content = _read_json_dict(package_dir / "content.json")
        if content and isinstance(content.get("entries"), Mapping):
            content_entries = dict(content["entries"])
        objs = _read_json_dict(package_dir / "objects.json")
        if objs and isinstance(objs.get("objects"), list):
            package_objects = [dict(o) for o in objs["objects"] if isinstance(o, Mapping)]
        has_rules = (package_dir / "rules.json").is_file()

    editor_rooms: dict[str, dict[str, Any]] = {}
    editor_objects: list[dict[str, Any]] = []
    if editor_doc is not None:
        for room in editor_doc.get("rooms") or []:
            if isinstance(room, Mapping) and room.get("id"):
                editor_rooms[str(room["id"])] = dict(room)
        for obj in editor_doc.get("objects") or []:
            if isinstance(obj, Mapping) and (obj.get("id") or obj.get("object_id")):
                editor_objects.append(dict(obj))

    return {
        "package_dir": str(package_dir) if package_dir is not None else None,
        "day_root": str(resolved_day_root) if resolved_day_root is not None else None,
        "has_dynamic_catalog": has_dynamic_catalog,
        "has_rules": has_rules,
        "locations": locations,
        "content_entries": content_entries,
        "objects": package_objects,
        "editor_rooms": editor_rooms,
        "editor_objects": editor_objects,
    }


def room_hours_for(
    location_id: str,
    *,
    sources: Mapping[str, Any],
) -> tuple[bool, Optional[tuple[Optional[int], Optional[int]]]]:
    """Fallback opening hours when only editor world JSON defines a custom room."""

    room = (sources.get("editor_rooms") or {}).get(str(location_id))
    if not isinstance(room, Mapping):
        return False, None
    hours = room.get("hours")
    if hours is None:
        return True, (None, None)
    if isinstance(hours, Mapping) and "open" in hours and "close" in hours:
        try:
            return True, (int(hours["open"]), int(hours["close"]))
        except (TypeError, ValueError):
            return True, (None, None)
    return True, (None, None)


# ---------------------------------------------------------------------------
# Layer C: Automatic extraction from place descriptions & fixture objects
# ---------------------------------------------------------------------------

_SELLING_CLAUSES_RE = re.compile(
    r"(?:提供|售卖|卖着|卖过|供应|摆着|摆满|收纳着(?:不少)?|陈列着|放着|门口摆着|摆出了|架上有|货架上有|摊位上都是|摊位上有|招牌是|收着|也卖|最受欢迎的组合是)"
    r"([^，。；！？]{2,36})"
)
_SELL_PATTERN_RE = re.compile(
    r"(?<![不没])卖([^，。；！？]{2,24}?)(?:的(?:店|杂货铺|小店|铺)|[，。；！？]|$)"
)
_BEFORE_SELL_RE = re.compile(r"([^，。；！？]{2,18})(?:在此售卖|在售)")
_BEFORE_AROMA_RE = re.compile(r"([^，。；！？]{2,10})的香味")
_CONTAINER_DISPLAY_RE = re.compile(r"([^\s，。；！？的]{0,6}?(?:饮料|酒|糕点|点心|面包|杂志))(?:柜|架)")
_ON_COUNTER_DISPLAY_RE = re.compile(
    r"(?:摆着|摆满|塞满|放着|摞在[^上]*上?的|贴着)([^，。；！？的]{2,10})(?:的(?:收银台|摊位|柜台|书架|货架|架)|$)"
)
_PREFIX_STRIP_RE = re.compile(
    r"^(?:不少|一些|一点|各种|各类|全村的|自家种的|当地[^的]{0,8}的|刚卸船的|手写的|主人手写的|带着[^的]{1,12}的|一小[桶捆排碗盘袋份盒个]?|成捆的|一排|几[捆个份张本条只]?|简单[的]?|待售的?)"
)

_FIXTURE_STOP_RE = re.compile(
    r"(?:^.*?\d+$|音响|露台|柜台|收银台|吧台|货架|书架|摊位|摊柜|陈列柜|架子|阅读桌|阅读椅|座位桌|矮桌|长桌|桌子|桌面|椅子|壁灯|台灯|门帘|楼梯|招牌|香味|味道|声响|影子|印痕|提示音|日光灯|绿植|盆栽|窗|雾气|水洼|主人|店主|常客|白鹭|路线图|空气|泡沫箱|碎冰|冰块|海盐|冰柜|冷藏柜|饮水机|咖啡机|砖墙|白光|白烟|杂货铺|手写价目|价目|轻音乐|音乐|书店|咖啡馆|便利店|花店|面包房)"
)

_DRINK_KEYWORDS = (
    "咖啡", "茶", "可可", "奶茶", "汽水", "饮料", "饮品", "饮", "牛奶", "鲜奶",
    "豆浆", "果汁", "汁", "水", "酒", "啤酒", "清酒", "苏打", "乌龙", "拿铁", "美式",
)
_FOOD_KEYWORDS = (
    "三明治", "关东煮", "饭团", "便当", "面包", "红豆包", "点心", "蛋糕", "甜点", "大福",
    "布丁", "曲奇", "饼干", "糕", "包子", "饺", "面", "拉面", "乌冬", "汤", "粥", "盒饭",
    "饭", "派", "吐司", "可颂", "简餐", "热食", "小吃", "熟食", "烤", "煮", "炸", "方糖",
)
_INGREDIENT_KEYWORDS = (
    "海产", "鲜鱼", "鱼", "蔬果", "蔬菜", "水果", "菜", "腌菜", "鸡蛋", "蛋", "米", "面粉",
    "肉", "虾", "贝", "海鲜", "豆腐", "调味", "酱", "食材", "农产品", "瓜", "果",
)
_READING_KEYWORDS = (
    "书", "文库本", "地方志", "杂志", "画册", "报纸", "绘本", "小说", "诗集", "日记",
    "手账", "明信片", "书签", "信纸", "本", "刊", "册", "志", "农事历", "疗养记", "手册",
)
_FURNISH_KEYWORDS = (
    "简单家具", "家具", "木椅", "藤椅", "边桌", "坐垫", "台灯", "落地灯", "床头灯", "矮几", "小书架",
)

_HEARTY_HINTS = ("套餐", "便当", "礼盒", "画册", "精装", "家具", "铁皮", "水桶", "草帽", "陶器", "木雕", "整")
_BUDGET_HINTS = ("小", "旧", "杂志", "关东煮", "饭团", "面包", "红豆", "饮料", "汽水", "方糖", "暖宝宝", "麻绳", "切花", "绿枝", "蔬果", "鸡蛋", "菜", "泡菜", "腌菜", "文库本")


def classify_good_phrase(
    phrase: str,
    loc_kind: str = "store",
    loc_name: str = "",
) -> tuple[str, str]:
    """Deterministically map a Chinese item name into ``(category, price_tier)``."""

    text = str(phrase or "").strip()
    if any(k in text for k in _FURNISH_KEYWORDS):
        cat = "furnish"
    elif any(k in text for k in _READING_KEYWORDS):
        cat = "reading"
    elif any(k in text for k in _DRINK_KEYWORDS) and "水桶" not in text:
        cat = "drink"
    elif any(k in text for k in _FOOD_KEYWORDS) and "面包机" not in text:
        cat = "food"
    elif any(k in text for k in _INGREDIENT_KEYWORDS):
        cat = "ingredient"
    elif loc_kind == "market":
        cat = "ingredient"
    elif loc_kind == "cafe":
        cat = "drink"
    else:
        cat = "daily"

    if any(k in text for k in _HEARTY_HINTS):
        tier = "hearty"
    elif any(k in text for k in _BUDGET_HINTS):
        tier = "budget"
    else:
        tier = "standard"
    return cat, tier


def _clean_candidate_phrase(raw: str) -> str:
    text = raw.strip(" ：:，。；！？、\"'“”《》（）()")
    if not text:
        return ""
    for pat in (_PREFIX_STRIP_RE,):
        prev = None
        while prev != text:
            prev = text
            text = pat.sub("", text).strip(" ：:，。；！？、")
    if "的" in text:
        before_de, after_de = text.rsplit("的", 1)
        if len(after_de) >= 2 and after_de not in (
            "店", "铺", "馆", "屋", "处", "人", "猫", "味", "味道", "香味",
            "声音", "声响", "日常", "需求", "生活", "组合", "角落", "地方",
        ):
            text = after_de
        elif len(before_de) >= 2 and any(w in after_de for w in ("香味", "味道", "组合")):
            text = before_de
    text = _PREFIX_STRIP_RE.sub("", text).strip(" ：:，。；！？、")
    if len(text) < 2 or len(text) > 12:
        return ""
    if _FIXTURE_STOP_RE.search(text):
        return ""
    if any(bad in text for bad in ("没有", "只是", "无论", "已经", "如果", "虽然", "里面", "对面", "路口", "一直", "每天")):
        return ""
    return text


def _split_phrases(segment: str) -> list[str]:
    parts = re.split(r"(?:以及|和|与|及|、|配|还有)", segment)
    cleaned: list[str] = []
    for part in parts:
        candidate = _clean_candidate_phrase(part)
        if candidate and candidate not in cleaned:
            cleaned.append(candidate)
    return cleaned


def extract_goods_from_place_text(
    loc_id: str,
    loc_name: str,
    loc_kind: str,
    intro: str,
    object_rows: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str, str, str]]:
    """Extract sellable item candidates from a place's intro and shop fixtures."""

    found_names: list[str] = []

    def _add(phrase: str) -> None:
        if phrase and phrase not in found_names:
            found_names.append(phrase)

    text_sources: list[str] = [str(intro or "")]
    for obj in object_rows:
        note = str(obj.get("note") or obj.get("description") or "")
        obj_name = str(obj.get("name") or "")
        if note:
            text_sources.append(note)
        for match in _CONTAINER_DISPLAY_RE.finditer(obj_name):
            cand = _clean_candidate_phrase(match.group(1))
            _add(cand)
        if "待售" in obj_name:
            cand = _clean_candidate_phrase(obj_name.replace("待售", ""))
            _add(cand)

    for text in text_sources:
        if not text:
            continue
        for regex in (
            _SELLING_CLAUSES_RE,
            _SELL_PATTERN_RE,
            _BEFORE_SELL_RE,
            _BEFORE_AROMA_RE,
            _ON_COUNTER_DISPLAY_RE,
            _CONTAINER_DISPLAY_RE,
        ):
            for match in regex.finditer(text):
                for cand in _split_phrases(match.group(1)):
                    _add(cand)
        if "热可可" in text:
            _add("热可可")
        if "红豆面包" in text or "红豆包" in text:
            _add("红豆面包" if "红豆面包" in text else "红豆包")
        if "烤面包" in text and "红豆" not in text:
            _add("现烤面包")
        if "冷" in text and "饮料" in text and "冷藏饮料" not in found_names:
            _add("冷藏饮料")

    rows: list[tuple[str, str, str, str]] = []
    place_label = loc_name or loc_id
    for name in found_names[:_MAX_SHELF_ITEMS]:
        cat, tier = classify_good_phrase(name, loc_kind=loc_kind, loc_name=place_label)
        rows.append((name, cat, tier, f"{place_label}在售的{name}"))
    return rows


def contextual_fallback_goods(
    loc_id: str,
    loc_name: str,
    loc_kind: str,
    intro: str = "",
) -> list[tuple[str, str, str, str]]:
    """Contextual fallback derived from shop identity when no text goods matched."""

    title = (loc_name or loc_id or "小店").strip()[:20]
    hint = f"{loc_id} {title} {intro}".lower()
    if loc_kind == "cafe" or any(k in hint for k in ("咖啡", "珈琲", "喫茶", "茶屋", "茶店")):
        return [
            (f"{title}招牌热饮", "drink", "standard", f"{title}现做的热饮"),
            (f"{title}点心简餐", "food", "budget", f"{title}搭配茶饮的小食"),
        ]
    if "书" in hint or "book" in hint:
        return [
            (f"{title}选书", "reading", "standard", f"{title}架上的推荐读物"),
            (f"{title}文库本", "reading", "budget", f"{title}里的平装小书"),
        ]
    if any(k in hint for k in ("面包", "bakery", "甜品", "蛋糕")):
        return [
            (f"{title}面包", "food", "budget", f"{title}新出炉的面点"),
            (f"{title}甜点", "food", "standard", f"{title}柜台里的点心"),
        ]
    if any(k in hint for k in ("花", "flower")):
        return [
            ("当季鲜切花", "daily", "budget", f"{title}门口桶里插着的花束"),
            ("小盆栽绿植", "daily", "standard", f"{title}里照料好的小植物"),
        ]
    if loc_kind == "market" or any(k in hint for k in ("鱼", "早市", "朝市", "市场", "农")):
        return [
            (f"{title}时令生鲜", "ingredient", "budget", f"{title}摊位上的新鲜食材"),
            (f"{title}风味熟食", "food", "standard", f"{title}现做的小吃"),
        ]
    if loc_kind == "store" or loc_kind == "shop":
        return [
            (f"{title}日常饮品", "drink", "budget", f"{title}冰柜里的饮品"),
            (f"{title}热食饭团", "food", "budget", f"{title}摆着的小吃主食"),
            (f"{title}日用小物", "daily", "standard", f"{title}货架上的日常物件"),
        ]
    return []


# ---------------------------------------------------------------------------
# WorldGen content.json "goods" formatting / parsing / generation
# ---------------------------------------------------------------------------


def parse_content_good_spec(
    raw: Any,
    *,
    loc_name: str,
    loc_kind: str,
) -> Optional[tuple[str, str, str, str]]:
    """Parse ``\"name|category|price_tier|description\"`` (or plain ``\"name\"``)."""

    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > _MAX_TEXT or any(ch in text for ch in "\r\n\x00"):
        return None
    parts = [p.strip() for p in text.split("|")]
    name = parts[0][:40]
    if not name:
        return None
    inferred_cat, inferred_tier = classify_good_phrase(name, loc_kind=loc_kind, loc_name=loc_name)
    cat = parts[1] if len(parts) > 1 and parts[1] in SALE_CATEGORIES else inferred_cat
    tier = parts[2] if len(parts) > 2 and parts[2] in PRICE_TIERS else inferred_tier
    desc = parts[3][:80] if len(parts) > 3 and parts[3] else f"{loc_name}在售的{name}"
    return name, cat, tier, desc


def format_content_good_spec(name: str, category: str, price_tier: str, description: str) -> str:
    clean_name = str(name).replace("|", " ").strip()[:32]
    cat = category if category in SALE_CATEGORIES else "daily"
    tier = price_tier if price_tier in PRICE_TIERS else "standard"
    clean_desc = str(description or f"在售的{clean_name}").replace("|", " ").strip()[:56]
    line = f"{clean_name}|{cat}|{tier}|{clean_desc}"
    return line[:_MAX_TEXT]


def generate_package_goods_for_location(
    loc_id: str,
    loc_name: str,
    loc_kind: str,
    intro: str,
    object_rows: Sequence[Mapping[str, Any]],
    theme: str,
    rng: Any,
) -> list[str]:
    """Deterministically extract/derive 2-4 local signature ``goods`` lines for a WorldGen shop."""

    del theme, rng  # No hand-coded per-theme item tables: names come strictly from the world text.
    extracted = extract_goods_from_place_text(loc_id, loc_name, loc_kind, intro, object_rows)
    seen_names: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    for name, cat, tier, desc in extracted:
        if name not in seen_names:
            seen_names.add(name)
            rows.append((name, cat, tier, desc))

    if len(rows) < 2:
        for name, cat, tier, desc in contextual_fallback_goods(loc_id, loc_name, loc_kind, intro):
            if name not in seen_names:
                seen_names.add(name)
                rows.append((name, cat, tier, desc))
            if len(rows) >= 3:
                break

    return [
        format_content_good_spec(name, cat, tier, desc)
        for name, cat, tier, desc in rows[:6]
    ]


# ---------------------------------------------------------------------------
# Open-counter shop scopes, head-noun validation & on-the-spot customization
# ---------------------------------------------------------------------------

_TIER_CN_LABELS: dict[str, str] = {
    "budget": "平价",
    "standard": "常规",
    "hearty": "精选",
}

_SLOT_CATEGORY_INFO: dict[str, tuple[str, str]] = {
    "drink": ("饮品", "饮品茶水咖啡果汁"),
    "food": ("餐食", "餐点面包小吃便当"),
    "ingredient": ("食材", "生鲜蔬果鱼肉粮蛋"),
    "reading": ("书刊", "书籍杂志诗集手账"),
    "daily": ("百货", "日常随身小物与花礼器皿"),
    "furnish": ("家具", "房间家具与灯具坐垫"),
}

# Store-business scope table: defines what *categories & price tiers* a venue carries,
# never hard-coding concrete products.
_STORE_SLOTS_CAFE: tuple[tuple[str, str], ...] = (
    ("drink", "budget"),
    ("drink", "standard"),
    ("food", "budget"),
    ("food", "standard"),
    ("drink", "hearty"),
)
_STORE_SLOTS_BAKERY: tuple[tuple[str, str], ...] = (
    ("food", "budget"),
    ("food", "standard"),
    ("drink", "budget"),
    ("drink", "standard"),
    ("food", "hearty"),
)
_STORE_SLOTS_BOOKSTORE: tuple[tuple[str, str], ...] = (
    ("reading", "budget"),
    ("reading", "standard"),
    ("daily", "budget"),
    ("reading", "hearty"),
    ("daily", "standard"),
)
_STORE_SLOTS_FLOWER_OR_CRAFT: tuple[tuple[str, str], ...] = (
    ("daily", "budget"),
    ("daily", "standard"),
    ("daily", "hearty"),
    ("furnish", "budget"),
)
_STORE_SLOTS_MARKET: tuple[tuple[str, str], ...] = (
    ("ingredient", "budget"),
    ("ingredient", "standard"),
    ("food", "budget"),
    ("ingredient", "hearty"),
    ("food", "standard"),
)
_STORE_SLOTS_HOME_GOODS: tuple[tuple[str, str], ...] = (
    ("daily", "budget"),
    ("daily", "standard"),
    ("furnish", "budget"),
    ("daily", "hearty"),
    ("furnish", "standard"),
    ("furnish", "hearty"),
)
_STORE_SLOTS_GENERAL: tuple[tuple[str, str], ...] = (
    ("drink", "budget"),
    ("food", "budget"),
    ("food", "standard"),
    ("drink", "standard"),
    ("daily", "budget"),
    ("reading", "budget"),
    ("food", "hearty"),
    ("daily", "standard"),
)


def store_allowed_slots(
    loc_id: str,
    loc_name: str = "",
    loc_kind: str = "store",
    intro: str = "",
) -> list[tuple[str, str]]:
    """Return allowed ``(category, price_tier)`` open-counter slots for a commercial venue."""

    primary = f"{loc_id} {loc_name}".lower()
    hint = f"{primary} {intro}".lower()

    if loc_kind == "cafe" or any(k in primary for k in ("咖啡", "珈琲", "喫茶", "茶屋", "茶室", "茶馆", "cafe", "coffee", "tea")):
        return list(_STORE_SLOTS_CAFE)
    if any(k in primary for k in ("面包", "bakery", "甜品", "蛋糕", "和菓子", "点心", "sweet")):
        return list(_STORE_SLOTS_BAKERY)
    if any(k in primary for k in ("书", "book")):
        return list(_STORE_SLOTS_BOOKSTORE)
    if any(k in primary for k in ("花", "flower", "craft", "手作", "工艺", "陶")):
        return list(_STORE_SLOTS_FLOWER_OR_CRAFT)
    if loc_kind == "market" or any(k in primary for k in ("鱼", "海鲜", "早市", "朝市", "市场", "菜", "农", "farm", "barn", "market")):
        return list(_STORE_SLOTS_MARKET)
    if any(k in primary for k in ("home_goods", "家具", "器皿", "五金", "家居")):
        return list(_STORE_SLOTS_HOME_GOODS)

    # Secondary check on intro only when id/name didn't specialize the shop
    if any(k in hint for k in ("咖啡馆", "茶室", "喫茶")):
        return list(_STORE_SLOTS_CAFE)
    if any(k in hint for k in ("旧书店", "书店")):
        return list(_STORE_SLOTS_BOOKSTORE)
    if any(k in hint for k in ("早市", "朝市", "鲜鱼", "渔船")):
        return list(_STORE_SLOTS_MARKET)
    if any(k in hint for k in ("日常器皿", "五金件")):
        return list(_STORE_SLOTS_HOME_GOODS)

    return list(_STORE_SLOTS_GENERAL)


_TRAILING_MEASURE_RE = re.compile(
    r"(?:（[^）]{1,12}）|\([^)]{1,12}\)|[一二两三四五六七八九十半\d]+(?:杯|瓶|罐|份|碗|盘|盒|袋|本|张|把|件|只|颗|块|个|条|片|束|支|听))+$"
)

_HEAD_SUFFIX_MAP: dict[str, tuple[str, ...]] = {
    "furnish": (
        "书桌", "矮桌", "长桌", "边桌", "茶几", "矮几", "木桌", "桌子",
        "木椅", "藤椅", "餐椅", "转椅", "椅子", "沙发", "木床", "单人床", "床",
        "衣柜", "书柜", "床头柜", "抽屉柜", "柜子", "小书架", "书架", "花架", "置物架", "收纳架",
        "落地灯", "台灯", "床头灯", "壁灯", "吊灯", "坐垫", "抱枕", "靠枕", "地毯", "窗帘", "家具",
    ),
    "reading": (
        "文库本", "笔记本", "手账本", "绘本", "画册", "诗集", "散文集", "随笔集", "选集", "文集", "全集",
        "风物记", "游记", "日记", "传记", "随笔记", "手记", "杂记", "札记", "笔记", "见闻录",
        "地方志", "植物志", "图鉴", "词典", "字典", "指南", "手册", "杂志", "周刊", "月刊",
        "报刊", "报纸", "早报", "晚报", "周报", "画报", "明信片集", "小说", "散文", "诗歌",
        "书刊", "读物", "小册", "旧书", "新书", "书签", "信纸", "书", "刊", "册", "志",
    ),
    "drink": (
        "美式咖啡", "手冲咖啡", "拿铁咖啡", "焦糖咖啡", "黑咖啡", "冰咖啡", "热咖啡", "咖啡",
        "卡布奇诺", "拿铁", "美式", "摩卡", "抹茶", "乌龙茶", "玄米茶", "烘青绿茶",
        "红茶", "绿茶", "麦茶", "花茶", "果茶", "奶茶", "热茶", "冰茶", "茶水", "茶",
        "热可可", "冰可可", "可可", "热牛奶", "冰牛奶", "鲜牛奶", "牛奶", "鲜奶", "酸奶",
        "豆浆", "豆奶", "柠檬汽水", "橙味汽水", "汽水", "苏打水", "气泡水", "苏打", "可乐",
        "苹果汁", "柠檬汁", "橙汁", "番茄汁", "果汁", "椰汁", "矿泉水", "清水", "冰水", "热水",
        "啤酒", "清酒", "果酒", "米酒", "红酒", "白酒", "甜酒", "热饮", "冷饮", "饮品", "饮料", "酒",
    ),
    "food": (
        "三明治", "关东煮", "温泉馒头", "蛋包饭", "盖浇饭", "卤肉饭", "炒饭", "便当", "盒饭",
        "饭团", "拉面", "乌冬面", "荞麦面", "意面", "拌面", "汤面", "汤豆腐",
        "红豆面包", "菠萝面包", "牛角面包", "全麦面包", "现烤面包", "红豆包", "菠萝包", "肉包", "面包",
        "可颂", "吐司", "贝果", "法棍", "司康", "松饼", "华夫饼", "蛋挞", "蛋糕", "大福",
        "布丁", "泡芙", "糯米糕", "年糕", "甜点", "甜品", "点心", "小吃", "熟食", "热食", "简餐",
        "曲奇", "饼干", "月饼", "煎饼", "抓饼", "烤饼", "玉米饼", "鱼饼", "鱼丸", "烤鱼", "炸鱼",
        "饺子", "蒸饺", "水饺", "包子", "馒头", "天妇罗", "章鱼烧", "寿司", "刺身", "汉堡", "薯条",
        "沙拉", "玉米浓汤", "浓汤", "海鲜汤", "味增汤", "米粥", "白粥", "饭", "面", "粥", "汤", "派",
    ),
    "ingredient": (
        "海产", "海鲜", "鲜鱼", "活鱼", "竹荚鱼", "秋刀鱼", "三文鱼", "金枪鱼", "青花鱼", "鳕鱼", "比目鱼", "鱼",
        "鲜虾", "大虾", "虾", "螃蟹", "蟹", "鲜贝", "扇贝", "贝类", "蛤蜊", "牡蛎", "生蚝",
        "鱿鱼", "章鱼", "海带", "紫菜", "蔬菜", "青菜", "白菜", "卷心菜", "生菜", "菠菜",
        "西红柿", "小番茄", "番茄", "土豆", "洋葱", "胡萝卜", "萝卜", "黄瓜", "茄子", "玉米", "蘑菇", "香菇",
        "蔬果", "水果", "苹果", "柑橘", "橘子", "橙子", "柠檬", "草莓", "葡萄", "桃子", "香蕉", "西瓜",
        "鸡蛋", "鸭蛋", "豆腐", "大米", "新米", "面粉", "五花肉", "猪肉", "牛肉", "鸡肉", "肉",
        "酱油", "味噌", "调料", "食材", "渍菜", "腌菜",
    ),
    "daily": (
        "塑料水桶", "水桶", "咖啡杯", "马克杯", "玻璃杯", "保温杯", "茶杯", "水杯", "冷水壶", "茶壶", "水壶",
        "陶瓷碗", "餐盘", "托盘", "碗", "盘子", "筷子", "勺子", "器皿",
        "毛巾", "手帕", "厚袜", "袜子", "草帽", "帽子", "围巾", "手套",
        "折叠伞", "晴雨伞", "雨伞", "折伞", "伞", "帆布包", "手提袋", "帆布袋", "布袋", "双肩包", "钱包",
        "钥匙扣", "创可贴", "湿巾", "面巾纸", "纸巾", "香皂", "肥皂", "牙刷", "梳子", "镜子",
        "香薰蜡烛", "蜡烛", "香薰", "鲜切花", "干花", "鲜花", "切花", "花束", "绿枝",
        "小盆栽", "盆栽", "多肉", "水培瓶", "玻璃瓶", "花瓶", "相框",
        "圆珠笔", "中性笔", "钢笔", "铅笔", "橡皮", "剪刀", "胶带", "麻绳", "暖宝宝",
        "明信片", "信封", "挂件", "陶器", "木雕", "竹筐", "五金", "百货", "小物", "日用品",
    ),
}

_SORTED_HEAD_SUFFIXES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((suffix, cat) for cat, suffixes in _HEAD_SUFFIX_MAP.items() for suffix in suffixes),
        key=lambda row: (-len(row[0]), row[1], row[0]),
    )
)

# Edible fruit/produce & paper stationery naturally straddle two adjacent categories.
_SOFT_OVERLAP_TERMS: dict[frozenset[str], tuple[str, ...]] = {
    frozenset({"reading", "daily"}): ("笔记本", "手账本", "明信片", "书签", "信纸"),
    frozenset({"food", "ingredient"}): ("水果", "蔬果", "苹果", "番茄", "小番茄", "西红柿", "草莓", "柠檬", "柑橘", "橘子", "橙子", "桃子", "玉米", "香蕉", "葡萄", "西瓜", "渍菜", "腌菜"),
}


def infer_open_order_head_category(name: str) -> Optional[str]:
    """Infer the semantic category from the rightmost head noun of a product name."""

    text = " ".join(str(name or "").split())
    if not text:
        return None
    stem = _TRAILING_MEASURE_RE.sub("", text).strip(" ：:，。；！？、\"'“”《》（）()") or text
    for suffix, cat in _SORTED_HEAD_SUFFIXES:
        if stem.endswith(suffix):
            return cat
    best_pos = -1
    best_len = -1
    best_cat: Optional[str] = None
    for suffix, cat in _SORTED_HEAD_SUFFIXES:
        pos = stem.rfind(suffix)
        if pos >= 0 and (pos > best_pos or (pos == best_pos and len(suffix) > best_len)):
            best_pos = pos
            best_len = len(suffix)
            best_cat = cat
    return best_cat


_OPEN_ORDER_NOISE_RE = re.compile(
    r"(?:^\d+$|(?:收银台|柜台|吧台|摊位|摊柜|陈列柜|冰柜|冷藏柜|饮水机|咖啡机|砖墙|白墙|白光|日光灯|门帘|楼梯|招牌|香味|味道|声响|影子|印痕|提示音|雾气|水洼|路线图|空气|碎冰|冰块|轻音乐|背景音乐|手写价目|价目表|店主|老板|主人|常客|路人|音响|书店|咖啡馆|便利店|杂货铺|花店|面包房)$)"
)


def validate_open_order_name(
    raw_name: Any,
    *,
    category: str,
) -> tuple[bool, str, Optional[str]]:
    """Validate and clean a custom item name against syntactic bounds and slot category."""

    if not isinstance(raw_name, str):
        return False, "", "INVALID_OPEN_ITEM_NAME"
    if any(ch in raw_name for ch in "\r\n\x00"):
        return False, "", "INVALID_OPEN_ITEM_NAME"
    cleaned = " ".join(raw_name.split())
    if not cleaned or len(cleaned) > _MAX_CUSTOM_ITEM_NAME_CHARS:
        return False, "", "INVALID_OPEN_ITEM_NAME"
    if not any(ch.isalnum() for ch in cleaned):
        return False, "", "INVALID_OPEN_ITEM_NAME"
    if _OPEN_ORDER_NOISE_RE.search(cleaned):
        return False, "", "INVALID_OPEN_ITEM_NAME"
    inferred = infer_open_order_head_category(cleaned)
    if inferred is not None and inferred != category:
        pair = frozenset({inferred, category})
        overlap_terms = _SOFT_OVERLAP_TERMS.get(pair, ())
        if not any(term in cleaned for term in overlap_terms):
            return False, "", "ITEM_CATEGORY_MISMATCH"
    return True, cleaned, None


# ---------------------------------------------------------------------------
# Shelf assembly across local items & open-counter slots
# ---------------------------------------------------------------------------


def open_store_ids(sources: Optional[Mapping[str, Any]]) -> tuple[str, ...]:
    """All commercial location IDs discovered from world package + editor world + dynamic catalog."""

    if not isinstance(sources, Mapping):
        return ()
    ids: list[str] = []
    locations = sources.get("locations") or {}
    for loc_id, loc in sorted(locations.items()):
        if isinstance(loc, Mapping) and str(loc.get("kind") or "") in COMMERCIAL_LOCATION_KINDS:
            ids.append(str(loc_id))
    editor_rooms = sources.get("editor_rooms") or {}
    for room_id, room in sorted(editor_rooms.items()):
        if (
            isinstance(room, Mapping)
            and str(room.get("kind") or "") in COMMERCIAL_LOCATION_KINDS
            and str(room_id) not in ids
        ):
            ids.append(str(room_id))
    day_root = sources.get("day_root")
    if day_root and sources.get("has_dynamic_catalog"):
        if not sources.get("package_dir") and not editor_rooms and "home_goods_store" not in ids:
            ids.append("home_goods_store")
        wcc = _dynamic_catalog_module()
        if wcc is not None:
            try:
                snap = wcc.read_dynamic_catalog_zero_write(day_root, include_out_of_stock=True)
                for row in snap.get("items") or []:
                    if isinstance(row, Mapping):
                        sid = str(row.get("store_id") or "").strip()
                        if sid and sid not in ids:
                            ids.append(sid)
            except Exception:
                pass
    return tuple(ids)


def _shelf_entry(
    *,
    location_id: str,
    name: str,
    category: str,
    price_tier: str,
    description: str,
    custom_price: Optional[int] = None,
) -> dict[str, Any]:
    cat = category if category in SALE_CATEGORIES else "daily"
    tier = price_tier if price_tier in PRICE_TIERS else "standard"
    lo, hi = price_range_for(cat, tier)
    item_id = stable_shop_item_id(location_id, name, cat)
    cap = capability_for_category(cat)
    decl = declaration_for_category(cat)
    consumable = consumable_declaration_for(cat, tier)
    price = resolve_item_price_jpy(
        location_id=location_id,
        category=cat,
        price_tier=tier,
        item_name=name,
        custom_price=custom_price,
    )
    entry: dict[str, Any] = {
        "catalog_item_id": item_id,
        "name": name,
        "description": description or f"在售的{name}",
        "price": price,
        "price_min": custom_price if isinstance(custom_price, int) else lo,
        "price_max": custom_price if isinstance(custom_price, int) else hi,
        "currency": CURRENCY,
        "category": cat,
        "price_tier": tier,
        "store_id": location_id,
        "usable_kind": decl.get("usable_kind", "CARRY"),
        "capability": cap,
        "declaration": decl,
        "consumable_declaration": consumable,
        "channels": (location_id,),
        "is_open_store_item": True,
        "open_order": False,
    }
    if (
        isinstance(custom_price, int)
        and not isinstance(custom_price, bool)
        and _CUSTOM_PRICE_MIN <= custom_price <= _CUSTOM_PRICE_MAX
    ):
        entry["custom_price"] = custom_price
    return entry


def _slot_shelf_entry(
    *,
    location_id: str,
    loc_name: str,
    category: str,
    price_tier: str,
) -> dict[str, Any]:
    cat = category if category in SALE_CATEGORIES else "daily"
    tier = price_tier if price_tier in PRICE_TIERS else "standard"
    lo, hi = price_range_for(cat, tier)
    cat_label, cat_examples = _SLOT_CATEGORY_INFO.get(cat, ("百货", "日常物件"))
    tier_label = _TIER_CN_LABELS.get(tier, "常规")
    name = f"自选{tier_label}{cat_label}"
    cap = capability_for_category(cat)
    decl = declaration_for_category(cat)
    consumable = consumable_declaration_for(cat, tier)
    return {
        "catalog_item_id": stable_shop_slot_id(location_id, cat, tier),
        "name": name,
        "description": (
            f"在{loc_name}自由选购{tier_label}{cat_examples}（{lo}-{hi}日元），"
            "下单时写明具体商品名即可"
        ),
        "price": lo,
        "price_min": lo,
        "price_max": hi,
        "currency": CURRENCY,
        "category": cat,
        "price_tier": tier,
        "store_id": location_id,
        "usable_kind": decl.get("usable_kind", "CARRY"),
        "capability": cap,
        "declaration": decl,
        "consumable_declaration": consumable,
        "channels": (location_id,),
        "is_open_store_item": True,
        "open_order": True,
    }


def _location_context(location_id: str, sources: Mapping[str, Any]) -> tuple[str, str, str, list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    loc_row = (sources.get("locations") or {}).get(location_id) or (sources.get("editor_rooms") or {}).get(location_id) or {}
    loc_name = str(loc_row.get("name") or location_id)
    loc_kind = str(loc_row.get("kind") or "store")
    if loc_kind == "shop":
        loc_kind = "store"
    content_entry = (sources.get("content_entries") or {}).get(location_id)
    intro = str((content_entry or {}).get("intro") or loc_row.get("description") or "")
    object_rows: list[Mapping[str, Any]] = [
        o for o in (sources.get("objects") or ())
        if isinstance(o, Mapping) and str(o.get("location_id") or "") == location_id
    ]
    editor_rows: list[Mapping[str, Any]] = [
        o for o in (sources.get("editor_objects") or ())
        if isinstance(o, Mapping) and str(o.get("room_id") or o.get("location_id") or "") == location_id
    ]
    return loc_name, loc_kind, intro, object_rows, editor_rows


def all_open_slots_for_location(
    location_id: str,
    *,
    sources: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return every open-counter slot allowed for ``location_id`` (untruncated)."""

    loc_id = str(location_id or "").strip()
    if not loc_id or loc_id not in open_store_ids(sources):
        return []
    loc_name, loc_kind, intro, _obj_rows, _ed_rows = _location_context(loc_id, sources)
    return [
        _slot_shelf_entry(
            location_id=loc_id,
            loc_name=loc_name,
            category=cat,
            price_tier=tier,
        )
        for cat, tier in store_allowed_slots(loc_id, loc_name, loc_kind, intro)
    ]


def store_shelf_for_location(
    location_id: str,
    *,
    sources: Mapping[str, Any],
    max_items: int = _MAX_SHELF_ITEMS,
) -> list[dict[str, Any]]:
    """Assemble dynamic catalog items + local signature items + open-counter slots for ``location_id``."""

    loc_id = str(location_id or "").strip()
    if not loc_id:
        return []
    loc_name, loc_kind, intro, object_rows, editor_rows = _location_context(loc_id, sources)
    content_entry = (sources.get("content_entries") or {}).get(loc_id)

    dynamic_entries: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    day_root = sources.get("day_root")
    if day_root:
        wcc = _dynamic_catalog_module()
        if wcc is not None:
            try:
                dyn = wcc.read_dynamic_catalog_zero_write(
                    day_root,
                    store_id=loc_id,
                    include_out_of_stock=False,
                )
                for row in dyn.get("items") or []:
                    if not isinstance(row, Mapping):
                        continue
                    if len(dynamic_entries) >= max_items:
                        break
                    shelf_row = _dynamic_catalog_shelf_entry(row)
                    clean_dyn_name = " ".join(str(shelf_row.get("name") or "").split())[:80]
                    if clean_dyn_name and clean_dyn_name not in seen_names:
                        seen_names.add(clean_dyn_name)
                        dynamic_entries.append(shelf_row)
            except Exception:
                pass

    static_entries: list[dict[str, Any]] = []
    signature_cap = min(max_items, 4)

    def _add(name: str, cat: str, tier: str, desc: str, custom_price: Optional[int] = None, cap_limit: int = max_items) -> None:
        clean = " ".join(str(name or "").split())[:80]
        if not clean or clean in seen_names or len(static_entries) >= cap_limit:
            return
        seen_names.add(clean)
        static_entries.append(
            _shelf_entry(
                location_id=loc_id,
                name=clean,
                category=cat,
                price_tier=tier,
                description=desc,
                custom_price=custom_price,
            )
        )

    # Layer A: Editor / objects.json explicit sale_spec
    for obj in [*editor_rows, *object_rows]:
        spec = normalize_sale_spec(obj.get("sale_spec"))
        if spec is None:
            continue
        desc = str(obj.get("note") or obj.get("description") or "").strip()
        _add(
            str(obj.get("name") or obj.get("object_id") or ""),
            spec["category"],
            spec["price_tier"],
            desc,
            custom_price=spec.get("price"),
            cap_limit=signature_cap,
        )

    # Layer B: WorldGen content.json "goods"
    if isinstance(content_entry, Mapping) and isinstance(content_entry.get("goods"), list):
        for raw_good in content_entry["goods"]:
            parsed = parse_content_good_spec(raw_good, loc_name=loc_name, loc_kind=loc_kind)
            if parsed is not None:
                _add(*parsed, cap_limit=signature_cap)

    # Layer C: Extract concrete local items from intro + fixture notes
    if not static_entries:
        for extracted in extract_goods_from_place_text(
            loc_id, loc_name, loc_kind, intro, [*object_rows, *editor_rows]
        ):
            _add(*extracted, cap_limit=signature_cap)

    entries: list[dict[str, Any]] = (dynamic_entries + static_entries)[:max_items]

    # Always append open-counter category/tier slots so any shop supports open-ended naming.
    for slot_entry in all_open_slots_for_location(loc_id, sources=sources):
        if len(entries) >= max_items:
            break
        if slot_entry["name"] in seen_names:
            continue
        seen_names.add(slot_entry["name"])
        entries.append(slot_entry)

    return entries


def find_open_catalog_item(
    catalog_item_id: Any,
    *,
    location_id: Optional[str] = None,
    hermes_home: Optional[Path | str] = None,
    environment: Optional[Mapping[str, str]] = None,
    sources: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Resolve one dynamic ``shop_item:*`` or ``shop_slot:*`` entry against world sources."""

    item_id = str(catalog_item_id or "").strip()
    if not parse_shop_item_id(item_id):
        return None
    resolved = sources if sources is not None else resolve_world_sources(hermes_home, environment)
    if resolved is None:
        return None
    is_slot = parse_shop_slot_id(item_id) is not None
    if not is_slot and resolved.get("day_root"):
        wcc = _dynamic_catalog_module()
        if wcc is not None:
            try:
                dyn = wcc.read_dynamic_catalog_zero_write(
                    resolved["day_root"],
                    store_id=location_id,
                    include_out_of_stock=True,
                )
                matched_dyn = (dyn.get("items_by_id") or {}).get(item_id)
                if isinstance(matched_dyn, Mapping):
                    return _dynamic_catalog_shelf_entry(matched_dyn)
            except Exception:
                pass
    target_locs = [location_id] if location_id else list(open_store_ids(resolved))
    for loc_id in target_locs:
        if not loc_id:
            continue
        candidates = (
            all_open_slots_for_location(loc_id, sources=resolved)
            if is_slot
            else store_shelf_for_location(loc_id, sources=resolved)
        )
        for entry in candidates:
            if entry["catalog_item_id"] == item_id:
                return entry
    return None


def customize_open_catalog_entry(
    entry: Mapping[str, Any],
    *,
    custom_item_name: Any = None,
    estimated_price: Any = None,
    allow_out_of_stock: bool = False,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Specialize a ``shop_slot:*`` or ``shop_item:*`` catalog entry for one purchase."""

    result = dict(entry)
    if (
        not allow_out_of_stock
        and result.get("is_dynamic_catalog_item")
        and int(result.get("stock_remaining", 0)) <= 0
    ):
        return None, "ITEM_OUT_OF_STOCK"
    item_id = str(result.get("catalog_item_id") or "").strip()
    is_slot = parse_shop_slot_id(item_id) is not None
    cat = str(result.get("category") or "daily")
    tier = str(result.get("price_tier") or "standard")
    loc_id = str(result.get("store_id") or "")

    has_custom_name = custom_item_name is not None and (
        not isinstance(custom_item_name, str) or bool(custom_item_name.strip())
    )
    if has_custom_name:
        if not is_slot:
            clean_custom = (
                " ".join(custom_item_name.split())
                if isinstance(custom_item_name, str)
                else ""
            )
            clean_existing = " ".join(str(result.get("name") or "").split())
            if not clean_custom or clean_custom != clean_existing:
                return None, "CUSTOM_NAME_NOT_SUPPORTED"
            clean_name = clean_existing or "商店物件"
        else:
            ok, clean_name, err_code = validate_open_order_name(custom_item_name, category=cat)
            if not ok:
                return None, err_code or "INVALID_OPEN_ITEM_NAME"
            result["name"] = clean_name
    elif is_slot:
        clean_name = str(result.get("name") or "自选商品")
    else:
        clean_name = str(result.get("name") or "商店物件")

    est_int = (
        estimated_price
        if isinstance(estimated_price, int) and not isinstance(estimated_price, bool)
        else None
    )
    fixed_price = result.get("custom_price")
    result["price"] = resolve_item_price_jpy(
        location_id=loc_id,
        category=cat,
        price_tier=tier,
        item_name=clean_name,
        custom_price=fixed_price if isinstance(fixed_price, int) else None,
        estimated_price=est_int,
    )
    return result, None


# ---------------------------------------------------------------------------
# v0.5 Step 3: 15-minute passing alley cart (zero-write, zero outbound push)
# ---------------------------------------------------------------------------

PASSING_CART_SLOT_ID = "shop_slot:home_alley:snack:budget"
PASSING_CART_ITEM_KEY = "passed_cart:sweet_potato"
PASSING_CART_PRICE_JPY = 260
PASSING_CART_DURATION_MINUTES = 15
PASSING_CART_SEASONS = frozenset({"autumn", "winter"})
PASSING_CART_OUTDOOR_LOCATIONS = frozenset(
    {
        "home_alley",
        "alley",
        "street_corner",
        "nearby_street",
        "street",
        "outdoor_street",
        "outdoor",
    }
)
PASSING_CART_OTHER_OUTDOOR = frozenset({"yard", "porch", "park", "promenade", "riverside"})
PASSING_CART_AUDIBLE_WINDOW_STATES = frozenset({"cracked", "half", "wide", "ajar", "open"})
_PASSING_CART_TZ = timezone(timedelta(hours=8))


def _stable_cart_mod(seed_text: str, mod: int) -> int:
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % mod


def _parse_now_iso_for_stall(now_iso: Any) -> datetime:
    if isinstance(now_iso, datetime):
        dt = now_iso
    else:
        text = str(now_iso or "").strip()
        if not text:
            raise ValueError("now_iso must be a non-empty ISO timestamp")
        normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=_PASSING_CART_TZ)
    return dt


def compute_daily_passing_cart_window(
    world_seed: str,
    day_index: int,
    season: str,
) -> Optional[dict[str, Any]]:
    """Deterministically schedule a 15-minute autumn/winter evening alley cart window.

    Returns ``None`` outside autumn/winter or on ~40% quiet evenings.
    ``outbound_push_allowed`` is hardcoded ``False`` (no intrusive push alerts).
    """

    season_norm = str(season or "").strip().lower()
    if season_norm not in PASSING_CART_SEASONS:
        return None
    if isinstance(day_index, bool) or not isinstance(day_index, int):
        day = int(day_index)
    else:
        day = day_index
    seed = str(world_seed or "shiomi").strip() or "shiomi"
    if _stable_cart_mod(f"{seed}:potato:{day}", 10) >= 6:
        return None
    start_min = 17 * 60 + 20 + _stable_cart_mod(f"{seed}:potato_min:{day}", 45)
    end_min = start_min + PASSING_CART_DURATION_MINUTES
    return {
        "event_id": f"cart_potato_d{day}",
        "cart_kind": "sweet_potato",
        "title_cn": "巷口烤红薯推车",
        "item_name_cn": "烫手烤红薯",
        "location_id": "home_alley",
        "slot_id": PASSING_CART_SLOT_ID,
        "catalog_item_id": PASSING_CART_SLOT_ID,
        "item_key": PASSING_CART_ITEM_KEY,
        "category": "food",
        "price_tier": "budget",
        "start_minute_of_day": start_min,
        "end_minute_of_day": end_min,
        "duration_minutes": PASSING_CART_DURATION_MINUTES,
        "price_jpy": PASSING_CART_PRICE_JPY,
        "currency": CURRENCY,
        "outbound_push_allowed": False,
        "llm_calls": 0,
    }


def is_passing_cart_active(
    window: Optional[Mapping[str, Any]],
    minute_of_day: int,
) -> bool:
    """Return True iff ``minute_of_day`` falls within the 15-minute cart window."""

    if not isinstance(window, Mapping):
        return False
    start = int(window.get("start_minute_of_day", -1))
    duration = int(window.get("duration_minutes", PASSING_CART_DURATION_MINUTES))
    if start < 0 or duration <= 0:
        return False
    minute = int(minute_of_day)
    return start <= minute < (start + duration)


def _passing_cart_slot_entry() -> dict[str, Any]:
    cap = capability_for_category("food")
    decl = declaration_for_category("food")
    consumable = consumable_declaration_for("food", "budget")
    return {
        "catalog_item_id": PASSING_CART_SLOT_ID,
        "slot_id": PASSING_CART_SLOT_ID,
        "item_key": PASSING_CART_ITEM_KEY,
        "name": "烫手烤红薯",
        "description": "巷口流动推车现烤的热红薯（260日元，限时15分钟）",
        "price": PASSING_CART_PRICE_JPY,
        "price_jpy": PASSING_CART_PRICE_JPY,
        "price_min": PASSING_CART_PRICE_JPY,
        "price_max": PASSING_CART_PRICE_JPY,
        "currency": CURRENCY,
        "category": "food",
        "price_tier": "budget",
        "store_id": "home_alley",
        "usable_kind": decl.get("usable_kind", "CARRY"),
        "capability": cap,
        "declaration": decl,
        "consumable_declaration": consumable,
        "channels": ("home_alley",),
        "is_open_store_item": True,
        "is_passing_cart": True,
        "open_order": True,
        "outbound_push_allowed": False,
    }


def project_passing_street_stall(
    world_seed: str,
    *,
    now_iso: Any,
    season: str,
    current_location: str,
    window_state: str = "closed",
    day_index: Optional[int] = None,
) -> dict[str, Any]:
    """Pure zero-write projection of the 15-minute passing alley cart.

    - Indoors: only surfaces an audible far bell cue when ``window_state`` is in
      ``("cracked", "half", "wide")`` (or ``"ajar"`` / ``"open"``).
    - Outdoors at ``home_alley`` (or street junction): opens purchasable
      ``shop_slot:home_alley:snack:budget`` (``passed_cart:sweet_potato``) at
      ``260 JPY`` strictly within the 15-minute window.
    - Outside the 15-minute window or outside autumn/winter: cleanly unavailable.
    """

    dt = _parse_now_iso_for_stall(now_iso)
    resolved_day = int(day_index) if day_index is not None else int(dt.date().toordinal())
    minute_of_day = dt.hour * 60 + dt.minute
    season_norm = str(season or "").strip().lower()
    loc_norm = str(current_location or "").strip().lower()
    win_norm = str(window_state or "closed").strip().lower()

    window = compute_daily_passing_cart_window(world_seed, resolved_day, season_norm)
    active = is_passing_cart_active(window, minute_of_day)

    at_cart_junction = loc_norm in PASSING_CART_OUTDOOR_LOCATIONS
    outdoors_other = loc_norm in PASSING_CART_OTHER_OUTDOOR
    indoors = not at_cart_junction and not outdoors_other

    audible = False
    purchasable = False
    bell_cue_cn = ""
    catalog: list[dict[str, Any]] = []
    reason_code: Optional[str] = None

    if not active:
        if season_norm not in PASSING_CART_SEASONS:
            reason_code = "SEASON_UNAVAILABLE"
        elif window is None:
            reason_code = "NO_CART_TODAY"
        else:
            reason_code = "OUTSIDE_CART_WINDOW"
        status = "unavailable"
    elif at_cart_junction:
        audible = True
        purchasable = True
        bell_cue_cn = "巷口正推过烤红薯推车，炭火甜香混着铃铛声就在路口"
        catalog = [_passing_cart_slot_entry()]
        status = "available"
    elif outdoors_other:
        audible = True
        bell_cue_cn = "远处巷口隐约摇过一阵烤红薯推车的铃声"
        reason_code = "NOT_AT_HOME_ALLEY"
        status = "audible_far"
    elif win_norm in PASSING_CART_AUDIBLE_WINDOW_STATES:
        audible = True
        bell_cue_cn = "窗缝外隐约飘来巷口烤红薯推车的远铃声"
        reason_code = "HEARD_FROM_INDOORS"
        status = "audible_far"
    else:
        audible = False
        reason_code = "WINDOW_CLOSED_OR_INDOORS"
        status = "unavailable"

    return {
        "status": status,
        "available": bool(purchasable),
        "active": bool(active),
        "purchasable": bool(purchasable),
        "audible": bool(audible),
        "bell_cue_cn": bell_cue_cn,
        "season": season_norm,
        "day_index": resolved_day,
        "minute_of_day": minute_of_day,
        "current_location": str(current_location or ""),
        "window_state": win_norm,
        "slot_id": PASSING_CART_SLOT_ID if purchasable else None,
        "item_key": PASSING_CART_ITEM_KEY if purchasable else None,
        "price_jpy": PASSING_CART_PRICE_JPY if active else None,
        "catalog": catalog,
        "window": dict(window) if window is not None else None,
        "outbound_push_allowed": False,
        "reason_code": reason_code,
        "read_is_pure": True,
        "llm_calls": 0,
    }


def resolve_passing_cart_purchase(
    world_seed: str,
    *,
    now_iso: Any,
    season: str,
    current_location: str,
    custom_item_name: Any = None,
    day_index: Optional[int] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Zero-write purchase resolution for the 15-minute passing alley cart."""

    proj = project_passing_street_stall(
        world_seed,
        now_iso=now_iso,
        season=season,
        current_location=current_location,
        window_state="closed",
        day_index=day_index,
    )
    if not proj["purchasable"] or not proj["catalog"]:
        return None, str(proj.get("reason_code") or "PASSING_CART_UNAVAILABLE")
    entry = dict(proj["catalog"][0])
    if custom_item_name is not None and (
        not isinstance(custom_item_name, str) or bool(custom_item_name.strip())
    ):
        ok, clean_name, err_code = validate_open_order_name(custom_item_name, category="food")
        if not ok:
            return None, err_code or "INVALID_OPEN_ITEM_NAME"
        entry["name"] = clean_name
    return entry, None


__all__ = [
    "COMMERCIAL_LOCATION_KINDS",
    "CURRENCY",
    "PASSING_CART_DURATION_MINUTES",
    "PASSING_CART_ITEM_KEY",
    "PASSING_CART_PRICE_JPY",
    "PASSING_CART_SLOT_ID",
    "PRICE_TIERS",
    "SALE_CATEGORIES",
    "TIER_NEED_EFFECTS",
    "TIER_PRICES_JPY",
    "TIER_PRICE_RANGES_JPY",
    "all_open_slots_for_location",
    "build_item_meta",
    "capability_for_category",
    "classify_good_phrase",
    "compute_daily_passing_cart_window",
    "consumable_declaration_for",
    "contextual_fallback_goods",
    "customize_open_catalog_entry",
    "declaration_for_category",
    "extract_goods_from_place_text",
    "find_open_catalog_item",
    "format_content_good_spec",
    "generate_package_goods_for_location",
    "infer_open_order_head_category",
    "is_passing_cart_active",
    "normalize_sale_spec",
    "open_store_ids",
    "parse_content_good_spec",
    "parse_shop_item_id",
    "parse_shop_slot_id",
    "price_for",
    "price_range_for",
    "project_passing_street_stall",
    "resolve_item_price_jpy",
    "resolve_passing_cart_purchase",
    "resolve_world_sources",
    "room_hours_for",
    "stable_shop_item_id",
    "stable_shop_slot_id",
    "store_allowed_slots",
    "store_shelf_for_location",
    "validate_item_meta",
    "validate_open_order_name",
]
