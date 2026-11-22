#!/usr/bin/env python3
"""Unified Single-Definition Dynamic Content Catalog (v0.9 Pillar B).

One validated record in ``<day_root>/world/dynamic_content_catalog.json`` drives:
1. Store shelf display (``world_open_storefront.store_shelf_for_location``),
2. Counter & verified purchase resolution + finite stock enforcement
   (``world_property_economy``),
3. Owned item capability & consumable relief (``world_consumable_contract``),
4. Declared room light source (``world_room_environment.declaration_of_catalog``
   when ``template_id == "decor_fixture"`` and ``light_band`` is set),
5. Atomic finite stock decrement persisted across restarts.

All read helpers are 100% zero-write (0 dirs, 0 files, 0 ``.lock`` files) and
enforce ``v02_representation_lease_forget.load_unified_forget_barrier_readonly``.
All write helpers reject symlinks, acquire ``state_tx_guard.state_file_lock``,
enforce ``operation_key`` idempotency, and persist atomic UTF-8 LF JSON with
``0600`` permissions + ``fsync`` + ``os.replace``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_WORLD_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORLD_SCRIPTS_DIR.parents[2]
_LIFE_SCRIPTS_DIR = _REPO_ROOT / "components" / "life" / "scripts"
for _p in (str(_REPO_ROOT), str(_WORLD_SCRIPTS_DIR), str(_LIFE_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.world.scripts import world_open_storefront as _storefront
except ImportError:  # pragma: no cover
    import world_open_storefront as _storefront  # type: ignore[no-redef]

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover
    import state_tx_guard as _tx  # type: ignore[no-redef]

try:
    from components.life.scripts import v02_representation_lease_forget as _forget
except ImportError:  # pragma: no cover
    import v02_representation_lease_forget as _forget  # type: ignore[no-redef]


SCHEMA = "world.v09.dynamic_content_catalog.v1"
STATE_REL_PATH: tuple[str, ...] = ("world", "dynamic_content_catalog.json")
DEFAULT_TZ = timezone(timedelta(hours=8))
CURRENCY = "JPY"

MAX_CATALOG_ITEMS = 128
MAX_CATALOG_EVENTS = 64
MAX_OP_RECEIPTS = 200
MAX_STOCK_UNITS = 999
DEFAULT_INITIAL_STOCK = 3

_CUSTOM_PRICE_MIN = 10
_CUSTOM_PRICE_MAX = 50_000

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_ISO_RE = re.compile(r"^[1-9]\d{3}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

_CAPABILITY_HANDHELD: dict[str, bool] = {
    "takeable": True,
    "portable": True,
    "placeable": True,
    "fixed": False,
}
_CAPABILITY_FIXED: dict[str, bool] = {
    "takeable": False,
    "portable": False,
    "placeable": True,
    "fixed": True,
}

ALLOWED_LIGHT_BANDS: tuple[Optional[str], ...] = (None, "dim", "moderate", "bright")
ALLOWED_FIXTURE_SURFACES: frozenset[str] = frozenset({"desk_surface", "chair_surface"})
ALLOWED_CONTAINER_CAPABILITIES: frozenset[str] = frozenset(
    {"STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE", "BREW_OR_DRINK"}
)

ITEM_CAPABILITY_TEMPLATES: dict[str, dict[str, Any]] = {
    "drink_warm": {
        "template_id": "drink_warm",
        "category": "drink",
        "usable_kind": "CARRY",
        "capability": dict(_CAPABILITY_HANDHELD),
        "declaration": {"surfaces": (), "relocatable": False, "usable_kind": "CARRY"},
        "matter_defaults": {
            "item_kind": "drink",
            "thermal": "warm",
            "portions": 1,
            "shelf_life_hours": 12,
        },
        "portions_range": (1, 4),
        "optional_fields": ("aroma_cn", "taste_note_cn", "portions", "shelf_life_hours"),
    },
    "meal_light": {
        "template_id": "meal_light",
        "category": "food",
        "usable_kind": "CARRY",
        "capability": dict(_CAPABILITY_HANDHELD),
        "declaration": {"surfaces": (), "relocatable": False, "usable_kind": "CARRY"},
        "matter_defaults": {
            "item_kind": "prepared_food",
            "cook_state": "cooked",
            "thermal": "warm",
            "portions": 1,
            "shelf_life_hours": 24,
        },
        "portions_range": (1, 4),
        "optional_fields": ("taste_note_cn", "portions", "shelf_life_hours"),
    },
    "snack_portable": {
        "template_id": "snack_portable",
        "category": "food",
        "usable_kind": "CARRY",
        "capability": dict(_CAPABILITY_HANDHELD),
        "declaration": {"surfaces": (), "relocatable": False, "usable_kind": "CARRY"},
        "matter_defaults": {
            "item_kind": "prepared_food",
            "thermal": "room",
            "portions": 2,
            "shelf_life_hours": 72,
        },
        "portions_range": (1, 6),
        "optional_fields": (
            "taste_note_cn",
            "portions",
            "shelf_life_hours",
            "reusable_container_spec",
        ),
    },
    "desk_vessel": {
        "template_id": "desk_vessel",
        "category": "daily",
        "usable_kind": "CARRY",
        "capability": dict(_CAPABILITY_HANDHELD),
        "declaration": {"surfaces": (), "relocatable": False, "usable_kind": "CARRY"},
        "matter_defaults": {
            "item_kind": "vessel",
            "capacity_slots": 3,
            "drinkware": True,
            "material_cn": "陶瓷",
        },
        "capacity_slots_range": (1, 6),
        "optional_fields": ("capacity_slots", "drinkware", "material_cn"),
    },
    "decor_fixture": {
        "template_id": "decor_fixture",
        "category": "furnish",
        "usable_kind": "FURNISH",
        "capability": dict(_CAPABILITY_FIXED),
        "declaration": {"surfaces": (), "relocatable": True, "usable_kind": "FURNISH"},
        "matter_defaults": {
            "item_kind": "fixture",
            "surfaces": [],
            "light_band": None,
        },
        "optional_fields": ("surfaces", "light_band", "material_cn"),
    },
    "reading_booklet": {
        "template_id": "reading_booklet",
        "category": "reading",
        "usable_kind": "CARRY",
        "capability": dict(_CAPABILITY_HANDHELD),
        "declaration": {"surfaces": (), "relocatable": False, "usable_kind": "CARRY"},
        "matter_defaults": {
            "item_kind": "paper",
            "paper_like": True,
            "pages_count": 8,
            "topic_tags": [],
            "argument_thesis_cn": "",
            "sample_excerpt_cn": "",
        },
        "pages_count_range": (1, 32),
        "optional_fields": (
            "pages_count",
            "topic_tags",
            "argument_thesis_cn",
            "sample_excerpt_cn",
        ),
    },
}

EVENT_TEMPLATES: dict[str, dict[str, Any]] = {
    "book_swap": {
        "template_id": "book_swap",
        "open_air": False,
        "weather_sensitive": False,
        "required_fields": ("venue_id", "swap_titles", "start_at", "end_at"),
    },
    "tea_tasting": {
        "template_id": "tea_tasting",
        "open_air": False,
        "weather_sensitive": False,
        "required_fields": (
            "venue_id",
            "featured_catalog_item_id",
            "sample_stock",
            "start_at",
            "end_at",
        ),
    },
    "early_closing": {
        "template_id": "early_closing",
        "open_air": False,
        "weather_sensitive": False,
        "required_fields": ("venue_id", "closed_from_hhmm", "reason_notice_cn", "date_iso"),
    },
    "porch_market": {
        "template_id": "porch_market",
        "open_air": True,
        "weather_sensitive": True,
        "required_fields": ("venue_id", "featured_item_ids", "start_at", "end_at"),
    },
}

FORBIDDEN_CANDIDATE_FIELDS: frozenset[str] = frozenset(
    {
        "custom_code",
        "affection_delta",
        "affection_score",
        "force_subject_preference",
        "grant_capability",
        "script",
        "eval",
        "exec",
        "python_code",
        "shell_command",
        "override_need",
        "force_liking",
        "subject_preference_override",
        "bypass_stock",
        "free_money",
    }
)

_COMMON_ITEM_KEYS: frozenset[str] = frozenset(
    {
        "template_id",
        "store_id",
        "name_cn",
        "name",
        "description_cn",
        "description",
        "category",
        "price_tier",
        "price",
        "custom_price",
        "stock_initial",
        "initial_stock",
        "stock",
        "stock_remaining",
        "season",
        "available_from",
        "available_until",
        "source_kind",
        "trigger_reason",
        "attributes",
        "matter_profile",
    }
)

_COMMON_EVENT_KEYS: frozenset[str] = frozenset(
    {
        "template_id",
        "event_id",
        "venue_id",
        "store_id",
        "title_cn",
        "description_cn",
        "start_at",
        "end_at",
        "date_iso",
        "closed_from_hhmm",
        "reason_notice_cn",
        "swap_titles",
        "featured_catalog_item_id",
        "sample_stock",
        "featured_item_ids",
        "open_air",
        "weather_sensitive",
        "season",
        "source_kind",
        "trigger_reason",
    }
)

_GENERATION_SOURCE_KINDS: frozenset[str] = frozenset(
    {"llm_scoped_generation", "deterministic_generation"}
)


class WorldCatalogError(ValueError):
    """Domain or safety error for the dynamic content catalog."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code or "CATALOG_ERROR")
        self.detail = str(detail or self.reason_code)
        super().__init__(self.detail)


def _strip_emojis_and_ctrl(text: str) -> str:
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        out.append(ch)
    return " ".join("".join(out).split()).strip()


def _clean_text(raw: Any, *, max_len: int, allow_empty: bool = False) -> Optional[str]:
    if raw is None:
        return "" if allow_empty else None
    if not isinstance(raw, str):
        return None
    if any(ch in raw for ch in "\r\n\x00"):
        return None
    cleaned = _strip_emojis_and_ctrl(raw)
    if not cleaned:
        return "" if allow_empty else None
    if len(cleaned) > max_len:
        return None
    return cleaned


def _parse_iso(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=DEFAULT_TZ)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=DEFAULT_TZ)
    return dt


def _format_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    dt = _parse_iso(now_iso) if now_iso is not None else datetime.now(DEFAULT_TZ)
    if dt is None:
        raise WorldCatalogError("INVALID_TIMESTAMP", "now_iso must be a valid ISO timestamp")
    return dt, dt.isoformat(timespec="seconds")


def _find_forbidden_key(obj: Any, *, _depth: int = 0) -> Optional[str]:
    if _depth > 16:
        return "__MAX_DEPTH__"
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            key_norm = str(k).strip().lower()
            if key_norm in FORBIDDEN_CANDIDATE_FIELDS:
                return str(k)
            nested = _find_forbidden_key(v, _depth=_depth + 1)
            if nested is not None:
                return nested
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for elem in obj:
            nested = _find_forbidden_key(elem, _depth=_depth + 1)
            if nested is not None:
                return nested
    return None


def _is_symlink_path(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _resolve_root_and_path(day_root: Path | str) -> tuple[Path, Path, Path]:
    raw_path = Path(str(day_root)).expanduser()
    resolved_root = _instance_paths.resolve_instance_paths(raw_path).day_root
    target = resolved_root.joinpath(*STATE_REL_PATH)
    return raw_path, resolved_root, target


def _assert_no_symlink_chain(raw_root: Path, resolved_root: Path, target_file: Path) -> None:
    lock_file = Path(str(target_file) + ".lock")
    for label, cand in (
        ("input_root", raw_root),
        ("day_root", resolved_root),
        ("world_dir", target_file.parent),
        ("catalog_file", target_file),
        ("catalog_lock", lock_file),
    ):
        if _is_symlink_path(cand):
            raise WorldCatalogError("UNSAFE_SYMLINK", f"{label} must not be a symlink")


def _has_symlink_readonly(raw_root: Path, resolved_root: Path, target_file: Path) -> bool:
    for cand in (raw_root, resolved_root, target_file.parent, target_file):
        if _is_symlink_path(cand):
            return True
    return False


def _empty_state(now_str: Optional[str] = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "revision": 0,
        "updated_at": now_str,
        "items": {},
        "events": {},
        "store_cooldowns": {},
        "operations": {},
        "committed_op_ring": [],
        "next_op_seq": 0,
        "evicted_up_to_revision": 0,
    }


def _read_state_file_zero_write(target: Path) -> tuple[Optional[dict[str, Any]], bool]:
    try:
        if _is_symlink_path(target) or not target.is_file():
            return None, False
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, False
    if not isinstance(raw, dict):
        return None, False
    if raw.get("schema") != SCHEMA:
        return None, False
    return raw, True


def _atomic_write_json_0600(
    raw_root: Path,
    resolved_root: Path,
    target_file: Path,
    payload: Mapping[str, Any],
) -> None:
    _assert_no_symlink_chain(raw_root, resolved_root, target_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    if _is_symlink_path(target_file.parent) or _is_symlink_path(target_file):
        raise WorldCatalogError("UNSAFE_SYMLINK", "symlink detected before atomic write")
    serialized = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    raw_bytes = serialized.replace("\r\n", "\n").encode("utf-8")
    tmp_name = f".{target_file.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    tmp_path = target_file.parent / tmp_name
    fd: Optional[int] = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY  # type: ignore[attr-defined]
        fd = os.open(str(tmp_path), flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(raw_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp_path, 0o600)
        os.replace(str(tmp_path), str(target_file))
        if os.name == "posix":
            os.chmod(target_file, 0o600)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def catalog_state_path(day_root: Path | str) -> Path:
    """Resolve canonical ``<day_root>/world/dynamic_content_catalog.json`` (zero-write)."""

    _raw, resolved_root, target = _resolve_root_and_path(day_root)
    return target


def _validate_reusable_container_spec(raw: Any) -> tuple[bool, Optional[dict[str, Any]], Optional[str]]:
    if raw is None:
        return True, None, None
    if not isinstance(raw, Mapping):
        return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
    forbidden = _find_forbidden_key(raw)
    if forbidden is not None:
        return False, None, "FORBIDDEN_CANDIDATE_FIELD"
    allowed_keys = frozenset(
        {
            "container_kind",
            "label_cn",
            "capacity_slots",
            "drinkware",
            "supported_capabilities",
            "material_cn",
        }
    )
    if not set(raw.keys()).issubset(allowed_keys):
        return False, None, "FORBIDDEN_CANDIDATE_FIELD"
    container_kind = _clean_text(raw.get("container_kind") or "cookie_tin", max_len=48)
    if not container_kind or not _SAFE_ID_RE.fullmatch(container_kind):
        return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
    label_cn = _clean_text(raw.get("label_cn") or "铁皮收纳盒", max_len=48)
    if not label_cn:
        return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
    raw_cap = raw.get("capacity_slots", 4)
    if isinstance(raw_cap, bool) or not isinstance(raw_cap, int) or not (1 <= raw_cap <= 12):
        return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
    drinkware = bool(raw.get("drinkware", False))
    if "drinkware" in raw and not isinstance(raw["drinkware"], bool):
        return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
    raw_caps = raw.get("supported_capabilities")
    if raw_caps is None:
        caps = ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"]
        if drinkware:
            caps.append("BREW_OR_DRINK")
    else:
        if not isinstance(raw_caps, Sequence) or isinstance(raw_caps, (str, bytes)):
            return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
        caps = []
        for c in raw_caps:
            if not isinstance(c, str) or c not in ALLOWED_CONTAINER_CAPABILITIES:
                return False, None, "UNSUPPORTED_CAPABILITY_TEMPLATE"
            if c not in caps:
                caps.append(c)
        if not caps:
            return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
    material_cn = _clean_text(raw.get("material_cn") or "", max_len=32, allow_empty=True)
    if material_cn is None:
        return False, None, "INVALID_REUSABLE_CONTAINER_SPEC"
    cleaned: dict[str, Any] = {
        "container_kind": container_kind,
        "label_cn": label_cn,
        "capacity_slots": int(raw_cap),
        "drinkware": drinkware,
        "supported_capabilities": caps,
    }
    if material_cn:
        cleaned["material_cn"] = material_cn
    return True, cleaned, None


def validate_candidate_item(
    raw: Mapping[str, Any],
    *,
    allowed_store_ids: Optional[Sequence[str]] = None,
) -> tuple[bool, Optional[dict[str, Any]], Optional[str]]:
    """Validate one candidate item against the 6 canonical capability templates."""

    if not isinstance(raw, Mapping):
        return False, None, "INVALID_CANDIDATE_ITEM"

    if _find_forbidden_key(raw) is not None:
        return False, None, "FORBIDDEN_CANDIDATE_FIELD"

    template_id = str(raw.get("template_id") or "").strip()
    template = ITEM_CAPABILITY_TEMPLATES.get(template_id)
    if template is None:
        return False, None, "UNSUPPORTED_CAPABILITY_TEMPLATE"

    allowed_top_keys = _COMMON_ITEM_KEYS | frozenset(template.get("optional_fields") or ())
    unknown_keys = set(raw.keys()) - allowed_top_keys
    if unknown_keys:
        return False, None, "FORBIDDEN_CANDIDATE_FIELD"

    store_id = str(raw.get("store_id") or "").strip()
    if not store_id or not _SAFE_ID_RE.fullmatch(store_id):
        return False, None, "INVALID_STORE_ID"
    if allowed_store_ids is not None:
        allowed_set = {str(s).strip() for s in allowed_store_ids if str(s).strip()}
        if store_id not in allowed_set:
            return False, None, "STORE_NOT_ALLOWED"

    name_cn = _clean_text(
        raw.get("name_cn") if raw.get("name_cn") is not None else raw.get("name"),
        max_len=40,
    )
    if not name_cn:
        return False, None, "INVALID_ITEM_NAME"

    raw_desc = (
        raw.get("description_cn")
        if raw.get("description_cn") is not None
        else raw.get("description")
    )
    if raw_desc is None:
        description_cn = f"在售的{name_cn}"
    else:
        description_cn = _clean_text(raw_desc, max_len=120)
        if not description_cn:
            return False, None, "INVALID_ITEM_DESCRIPTION"

    category = str(template["category"])
    explicit_cat = raw.get("category")
    if explicit_cat is not None and str(explicit_cat).strip() != category:
        return False, None, "TEMPLATE_CATEGORY_MISMATCH"

    price_tier = str(raw.get("price_tier") or "standard").strip()
    if price_tier not in _storefront.PRICE_TIERS:
        return False, None, "INVALID_PRICE_TIER"

    raw_price = raw.get("custom_price") if raw.get("custom_price") is not None else raw.get("price")
    custom_price: Optional[int] = None
    if raw_price is not None:
        if (
            isinstance(raw_price, bool)
            or not isinstance(raw_price, int)
            or not (_CUSTOM_PRICE_MIN <= raw_price <= _CUSTOM_PRICE_MAX)
        ):
            return False, None, "INVALID_ITEM_PRICE"
        custom_price = int(raw_price)

    price = _storefront.resolve_item_price_jpy(
        location_id=store_id,
        category=category,
        price_tier=price_tier,
        item_name=name_cn,
        custom_price=custom_price,
    )

    raw_stock_init = (
        raw.get("stock_initial")
        if raw.get("stock_initial") is not None
        else (
            raw.get("initial_stock")
            if raw.get("initial_stock") is not None
            else raw.get("stock", DEFAULT_INITIAL_STOCK)
        )
    )
    if (
        isinstance(raw_stock_init, bool)
        or not isinstance(raw_stock_init, int)
        or not (1 <= raw_stock_init <= MAX_STOCK_UNITS)
    ):
        return False, None, "INVALID_STOCK_VALUE"
    stock_initial = int(raw_stock_init)

    raw_stock_rem = raw.get("stock_remaining")
    if raw_stock_rem is None:
        stock_remaining = stock_initial
    else:
        if (
            isinstance(raw_stock_rem, bool)
            or not isinstance(raw_stock_rem, int)
            or not (0 <= raw_stock_rem <= stock_initial)
        ):
            return False, None, "INVALID_STOCK_VALUE"
        stock_remaining = int(raw_stock_rem)

    # Merge optional nested attributes / matter_profile cleanly without forbidden keys.
    extra_attr_sources: list[Mapping[str, Any]] = []
    for container_key in ("matter_profile", "attributes"):
        block = raw.get(container_key)
        if block is not None:
            if not isinstance(block, Mapping):
                return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
            allowed_sub = frozenset(template.get("optional_fields") or ()) | frozenset(
                (template.get("matter_defaults") or {}).keys()
            )
            if not set(block.keys()).issubset(allowed_sub):
                return False, None, "FORBIDDEN_CANDIDATE_FIELD"
            extra_attr_sources.append(block)

    def _pick(field: str, default: Any = None) -> Any:
        if field in raw and raw[field] is not None:
            return raw[field]
        for src in extra_attr_sources:
            if field in src and src[field] is not None:
                return src[field]
        return default

    matter_profile: dict[str, Any] = copy.deepcopy(template["matter_defaults"])
    declaration: dict[str, Any] = dict(template["declaration"])
    capability: dict[str, Any] = dict(template["capability"])
    reusable_container_spec: Optional[dict[str, Any]] = None
    light_band: Optional[str] = None
    surfaces_list: list[str] = []

    if template_id == "drink_warm":
        portions = _pick("portions", 1)
        if isinstance(portions, bool) or not isinstance(portions, int) or not (1 <= portions <= 4):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        shelf_life = _pick("shelf_life_hours", 12)
        if isinstance(shelf_life, bool) or not isinstance(shelf_life, int) or not (1 <= shelf_life <= 168):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        aroma_cn = _clean_text(_pick("aroma_cn", ""), max_len=60, allow_empty=True)
        taste_note_cn = _clean_text(_pick("taste_note_cn", ""), max_len=80, allow_empty=True)
        if aroma_cn is None or taste_note_cn is None:
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        matter_profile.update(
            {
                "portions": int(portions),
                "shelf_life_hours": int(shelf_life),
                "aroma_cn": aroma_cn,
                "taste_note_cn": taste_note_cn,
            }
        )
    elif template_id == "meal_light":
        portions = _pick("portions", 1)
        if isinstance(portions, bool) or not isinstance(portions, int) or not (1 <= portions <= 4):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        shelf_life = _pick("shelf_life_hours", 24)
        if isinstance(shelf_life, bool) or not isinstance(shelf_life, int) or not (1 <= shelf_life <= 168):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        taste_note_cn = _clean_text(_pick("taste_note_cn", ""), max_len=80, allow_empty=True)
        if taste_note_cn is None:
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        matter_profile.update(
            {
                "portions": int(portions),
                "shelf_life_hours": int(shelf_life),
                "taste_note_cn": taste_note_cn,
            }
        )
    elif template_id == "snack_portable":
        portions = _pick("portions", 2)
        if isinstance(portions, bool) or not isinstance(portions, int) or not (1 <= portions <= 6):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        shelf_life = _pick("shelf_life_hours", 72)
        if isinstance(shelf_life, bool) or not isinstance(shelf_life, int) or not (1 <= shelf_life <= 720):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        taste_note_cn = _clean_text(_pick("taste_note_cn", ""), max_len=80, allow_empty=True)
        if taste_note_cn is None:
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        ok_spec, cleaned_spec, spec_err = _validate_reusable_container_spec(
            _pick("reusable_container_spec", None)
        )
        if not ok_spec:
            return False, None, spec_err or "INVALID_REUSABLE_CONTAINER_SPEC"
        reusable_container_spec = cleaned_spec
        matter_profile.update(
            {
                "portions": int(portions),
                "shelf_life_hours": int(shelf_life),
                "taste_note_cn": taste_note_cn,
                "reusable_container_spec": copy.deepcopy(cleaned_spec),
            }
        )
    elif template_id == "desk_vessel":
        cap_slots = _pick("capacity_slots", 3)
        if isinstance(cap_slots, bool) or not isinstance(cap_slots, int) or not (1 <= cap_slots <= 6):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        raw_drinkware = _pick("drinkware", True)
        if not isinstance(raw_drinkware, bool):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        material_cn = _clean_text(_pick("material_cn", "陶瓷"), max_len=32)
        if not material_cn:
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        matter_profile.update(
            {
                "capacity_slots": int(cap_slots),
                "drinkware": bool(raw_drinkware),
                "material_cn": material_cn,
            }
        )
    elif template_id == "decor_fixture":
        raw_surfaces = _pick("surfaces", ())
        if raw_surfaces is None:
            raw_surfaces = ()
        if not isinstance(raw_surfaces, (list, tuple)):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        for surf in raw_surfaces:
            if not isinstance(surf, str) or surf not in ALLOWED_FIXTURE_SURFACES:
                return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
            if surf not in surfaces_list:
                surfaces_list.append(surf)
        raw_band = _pick("light_band", None)
        if raw_band is not None:
            if not isinstance(raw_band, str) or raw_band.strip() not in ("dim", "moderate", "bright"):
                return False, None, "INVALID_LIGHT_BAND"
            light_band = raw_band.strip()
        material_cn = _clean_text(_pick("material_cn", ""), max_len=32, allow_empty=True)
        if material_cn is None:
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        declaration = {
            "surfaces": list(surfaces_list),
            "relocatable": True,
            "usable_kind": "FURNISH",
        }
        if light_band is not None:
            declaration["light_band"] = light_band
        matter_profile.update(
            {
                "surfaces": list(surfaces_list),
                "light_band": light_band,
            }
        )
        if material_cn:
            matter_profile["material_cn"] = material_cn
    elif template_id == "reading_booklet":
        pages_count = _pick("pages_count", 8)
        if isinstance(pages_count, bool) or not isinstance(pages_count, int) or not (1 <= pages_count <= 32):
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        raw_tags = _pick("topic_tags", [])
        if not isinstance(raw_tags, (list, tuple)) or len(raw_tags) > 6:
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        tags: list[str] = []
        for tag in raw_tags:
            cleaned_tag = _clean_text(tag, max_len=24)
            if not cleaned_tag:
                return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
            if cleaned_tag not in tags:
                tags.append(cleaned_tag)
        thesis_cn = _clean_text(_pick("argument_thesis_cn", ""), max_len=160, allow_empty=True)
        excerpt_cn = _clean_text(_pick("sample_excerpt_cn", ""), max_len=240, allow_empty=True)
        if thesis_cn is None or excerpt_cn is None:
            return False, None, "INVALID_TEMPLATE_ATTRIBUTES"
        matter_profile.update(
            {
                "pages_count": int(pages_count),
                "topic_tags": tags,
                "argument_thesis_cn": thesis_cn,
                "sample_excerpt_cn": excerpt_cn,
            }
        )

    season = _clean_text(raw.get("season") or "", max_len=24, allow_empty=True)
    if season is None:
        return False, None, "INVALID_TEMPLATE_ATTRIBUTES"

    available_from = raw.get("available_from")
    available_until = raw.get("available_until")
    avail_from_iso: Optional[str] = None
    avail_until_iso: Optional[str] = None
    if available_from is not None:
        dt_from = _parse_iso(available_from)
        if dt_from is None:
            return False, None, "INVALID_TIMESTAMP"
        avail_from_iso = dt_from.isoformat(timespec="seconds")
    if available_until is not None:
        dt_until = _parse_iso(available_until)
        if dt_until is None:
            return False, None, "INVALID_TIMESTAMP"
        avail_until_iso = dt_until.isoformat(timespec="seconds")

    catalog_item_id = _storefront.stable_shop_item_id(store_id, name_cn, category)
    consumable_declaration = _storefront.consumable_declaration_for(category, price_tier)

    # Ensure declaration surfaces is JSON-serializable list while keeping canonical keys.
    decl_json = dict(declaration)
    decl_json["surfaces"] = list(decl_json.get("surfaces") or ())

    record: dict[str, Any] = {
        "catalog_item_id": catalog_item_id,
        "template_id": template_id,
        "store_id": store_id,
        "name_cn": name_cn,
        "name": name_cn,
        "description_cn": description_cn,
        "description": description_cn,
        "category": category,
        "price_tier": price_tier,
        "price": int(price),
        "currency": CURRENCY,
        "usable_kind": str(template["usable_kind"]),
        "capability": capability,
        "declaration": decl_json,
        "consumable_declaration": consumable_declaration,
        "matter_profile": matter_profile,
        "stock_initial": stock_initial,
        "stock_remaining": stock_remaining,
        "total_sold": 0,
        "season": season,
        "available_from": avail_from_iso,
        "available_until": avail_until_iso,
        "is_dynamic_catalog_item": True,
        "is_open_store_item": True,
        "open_order": False,
    }
    if reusable_container_spec is not None:
        record["reusable_container_spec"] = copy.deepcopy(reusable_container_spec)
    if template_id == "decor_fixture":
        record["surfaces"] = list(surfaces_list)
        record["light_band"] = light_band
    return True, record, None


def validate_candidate_event(
    raw: Mapping[str, Any],
    *,
    allowed_venue_ids: Optional[Sequence[str]] = None,
) -> tuple[bool, Optional[dict[str, Any]], Optional[str]]:
    """Validate one candidate event against the 4 canonical event templates."""

    if not isinstance(raw, Mapping):
        return False, None, "INVALID_CANDIDATE_EVENT"

    if _find_forbidden_key(raw) is not None:
        return False, None, "FORBIDDEN_CANDIDATE_FIELD"

    template_id = str(raw.get("template_id") or "").strip()
    template = EVENT_TEMPLATES.get(template_id)
    if template is None:
        return False, None, "UNSUPPORTED_CAPABILITY_TEMPLATE"

    unknown_keys = set(raw.keys()) - _COMMON_EVENT_KEYS
    if unknown_keys:
        return False, None, "FORBIDDEN_CANDIDATE_FIELD"

    venue_id = str(raw.get("venue_id") or raw.get("store_id") or "").strip()
    if not venue_id or not _SAFE_ID_RE.fullmatch(venue_id):
        return False, None, "INVALID_VENUE_ID"
    if allowed_venue_ids is not None:
        allowed_set = {str(v).strip() for v in allowed_venue_ids if str(v).strip()}
        if venue_id not in allowed_set:
            return False, None, "VENUE_NOT_ALLOWED"

    title_cn = _clean_text(raw.get("title_cn") or "", max_len=60, allow_empty=True)
    desc_cn = _clean_text(raw.get("description_cn") or "", max_len=160, allow_empty=True)
    if title_cn is None or desc_cn is None:
        return False, None, "INVALID_EVENT_TEXT"

    record: dict[str, Any] = {
        "template_id": template_id,
        "venue_id": venue_id,
        "open_air": bool(template["open_air"]),
        "weather_sensitive": bool(template["weather_sensitive"]),
    }

    if template_id == "book_swap":
        raw_titles = raw.get("swap_titles")
        if not isinstance(raw_titles, (list, tuple)) or not (1 <= len(raw_titles) <= 8):
            return False, None, "INVALID_EVENT_FIELDS"
        titles: list[str] = []
        for t in raw_titles:
            ct = _clean_text(t, max_len=48)
            if not ct:
                return False, None, "INVALID_EVENT_FIELDS"
            if ct not in titles:
                titles.append(ct)
        dt_s = _parse_iso(raw.get("start_at"))
        dt_e = _parse_iso(raw.get("end_at"))
        if dt_s is None or dt_e is None or dt_e <= dt_s:
            return False, None, "INVALID_EVENT_WINDOW"
        record.update(
            {
                "title_cn": title_cn or "店内旧书交换角",
                "description_cn": desc_cn or f"{venue_id}开展旧书交换",
                "swap_titles": titles,
                "start_at": dt_s.isoformat(timespec="seconds"),
                "end_at": dt_e.isoformat(timespec="seconds"),
            }
        )
    elif template_id == "tea_tasting":
        feat_id = str(raw.get("featured_catalog_item_id") or "").strip()
        if not feat_id or not _SAFE_ID_RE.fullmatch(feat_id):
            return False, None, "INVALID_EVENT_FIELDS"
        sample_stock = raw.get("sample_stock")
        if (
            isinstance(sample_stock, bool)
            or not isinstance(sample_stock, int)
            or not (1 <= sample_stock <= 50)
        ):
            return False, None, "INVALID_EVENT_FIELDS"
        dt_s = _parse_iso(raw.get("start_at"))
        dt_e = _parse_iso(raw.get("end_at"))
        if dt_s is None or dt_e is None or dt_e <= dt_s:
            return False, None, "INVALID_EVENT_WINDOW"
        record.update(
            {
                "title_cn": title_cn or "当季试茶席",
                "description_cn": desc_cn or f"{venue_id}提供当季茶饮试饮",
                "featured_catalog_item_id": feat_id,
                "sample_stock": int(sample_stock),
                "start_at": dt_s.isoformat(timespec="seconds"),
                "end_at": dt_e.isoformat(timespec="seconds"),
            }
        )
    elif template_id == "early_closing":
        closed_from = str(raw.get("closed_from_hhmm") or "").strip()
        if not _HHMM_RE.fullmatch(closed_from):
            return False, None, "INVALID_EVENT_FIELDS"
        date_iso = str(raw.get("date_iso") or "").strip()
        if not _DATE_ISO_RE.fullmatch(date_iso):
            return False, None, "INVALID_EVENT_FIELDS"
        reason_notice = _clean_text(raw.get("reason_notice_cn"), max_len=120)
        if not reason_notice:
            return False, None, "INVALID_EVENT_FIELDS"
        record.update(
            {
                "title_cn": title_cn or "临时提前打烊告示",
                "description_cn": desc_cn or reason_notice,
                "closed_from_hhmm": closed_from,
                "reason_notice_cn": reason_notice,
                "date_iso": date_iso,
            }
        )
    elif template_id == "porch_market":
        raw_ids = raw.get("featured_item_ids")
        if not isinstance(raw_ids, (list, tuple)) or len(raw_ids) > 12:
            return False, None, "INVALID_EVENT_FIELDS"
        feat_ids: list[str] = []
        for fid in raw_ids:
            f_str = str(fid or "").strip()
            if not f_str or not _SAFE_ID_RE.fullmatch(f_str):
                return False, None, "INVALID_EVENT_FIELDS"
            if f_str not in feat_ids:
                feat_ids.append(f_str)
        dt_s = _parse_iso(raw.get("start_at"))
        dt_e = _parse_iso(raw.get("end_at"))
        if dt_s is None or dt_e is None or dt_e <= dt_s:
            return False, None, "INVALID_EVENT_WINDOW"
        record.update(
            {
                "title_cn": title_cn or "檐下露天小集",
                "description_cn": desc_cn or f"{venue_id}门口摆出露天小摊",
                "featured_item_ids": feat_ids,
                "start_at": dt_s.isoformat(timespec="seconds"),
                "end_at": dt_e.isoformat(timespec="seconds"),
            }
        )

    season = _clean_text(raw.get("season") or "", max_len=24, allow_empty=True)
    if season is None:
        return False, None, "INVALID_EVENT_FIELDS"
    record["season"] = season

    raw_eid = raw.get("event_id")
    if raw_eid is not None:
        eid = str(raw_eid).strip()
        if not eid or not _SAFE_ID_RE.fullmatch(eid):
            return False, None, "INVALID_EVENT_ID"
        record["event_id"] = eid
    else:
        anchor = record.get("start_at") or record.get("date_iso") or ""
        digest = hashlib.sha256(
            f"{venue_id}|{template_id}|{anchor}|{record['title_cn']}".encode("utf-8")
        ).hexdigest()[:10]
        safe_venue = re.sub(r"[^A-Za-z0-9._-]+", "_", venue_id).strip("_")[:32] or "venue"
        record["event_id"] = f"evt:{safe_venue}:{template_id}:{digest}"

    return True, record, None


def _is_blocked_by_barrier(record: Mapping[str, Any], id_key: str, barrier: Mapping[str, Any]) -> bool:
    if barrier.get("fail_closed"):
        return True
    entity_id = str(record.get(id_key) or "").strip()
    if entity_id and _forget.is_entity_forget_blocked(entity_id, barrier):
        return True
    if _forget.is_entity_forget_blocked(record, barrier):
        return True
    return False


def read_dynamic_catalog_zero_write(
    day_root: Path | str,
    *,
    store_id: Optional[str] = None,
    include_out_of_stock: bool = True,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Read dynamic catalog items & events with 100% zero disk writes and forget-barrier enforcement."""

    raw_root, resolved_root, target = _resolve_root_and_path(day_root)
    if _has_symlink_readonly(raw_root, resolved_root, target):
        return {
            "schema": SCHEMA,
            "exists": False,
            "revision": 0,
            "updated_at": None,
            "items": [],
            "items_by_id": {},
            "events": [],
            "store_cooldowns": {},
            "barrier_fail_closed": True,
            "barrier_filtered_count": 0,
            "reason_code": "UNSAFE_SYMLINK",
            "read_is_pure": True,
            "llm_calls": 0,
        }

    barrier = _forget.load_unified_forget_barrier_readonly(resolved_root)
    if barrier.get("fail_closed"):
        return {
            "schema": SCHEMA,
            "exists": target.is_file(),
            "revision": 0,
            "updated_at": None,
            "items": [],
            "items_by_id": {},
            "events": [],
            "store_cooldowns": {},
            "barrier_fail_closed": True,
            "barrier_filtered_count": 0,
            "read_is_pure": True,
            "llm_calls": 0,
        }

    state, exists = _read_state_file_zero_write(target)
    if not exists or state is None:
        return {
            "schema": SCHEMA,
            "exists": False,
            "revision": 0,
            "updated_at": None,
            "items": [],
            "items_by_id": {},
            "events": [],
            "store_cooldowns": {},
            "barrier_fail_closed": False,
            "barrier_filtered_count": 0,
            "read_is_pure": True,
            "llm_calls": 0,
        }

    now_dt = _parse_iso(now_iso) if now_iso is not None else None
    store_filter = str(store_id).strip() if store_id is not None and str(store_id).strip() else None

    raw_items = state.get("items") if isinstance(state.get("items"), Mapping) else {}
    raw_events = state.get("events") if isinstance(state.get("events"), Mapping) else {}
    raw_cooldowns = (
        state.get("store_cooldowns") if isinstance(state.get("store_cooldowns"), Mapping) else {}
    )

    items_out: list[dict[str, Any]] = []
    items_by_id: dict[str, dict[str, Any]] = {}
    filtered_count = 0

    for item_id in sorted(raw_items.keys()):
        entry = raw_items[item_id]
        if not isinstance(entry, Mapping):
            continue
        if _is_blocked_by_barrier(entry, "catalog_item_id", barrier):
            filtered_count += 1
            continue
        if store_filter is not None and str(entry.get("store_id") or "") != store_filter:
            continue
        stock_rem = int(entry.get("stock_remaining", 0))
        if not include_out_of_stock and stock_rem <= 0:
            continue
        if now_dt is not None:
            avail_until = _parse_iso(entry.get("available_until"))
            if avail_until is not None and now_dt > avail_until and not include_out_of_stock:
                continue
        copied = copy.deepcopy(dict(entry))
        items_out.append(copied)
        items_by_id[str(copied.get("catalog_item_id") or item_id)] = copied

    events_out: list[dict[str, Any]] = []
    for event_id in sorted(raw_events.keys()):
        ev = raw_events[event_id]
        if not isinstance(ev, Mapping):
            continue
        if _is_blocked_by_barrier(ev, "event_id", barrier):
            filtered_count += 1
            continue
        if store_filter is not None and str(ev.get("venue_id") or "") != store_filter:
            continue
        events_out.append(copy.deepcopy(dict(ev)))

    return {
        "schema": SCHEMA,
        "exists": True,
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "items": items_out,
        "items_by_id": items_by_id,
        "events": events_out,
        "store_cooldowns": {str(k): str(v) for k, v in raw_cooldowns.items() if isinstance(v, str)},
        "barrier_fail_closed": False,
        "barrier_filtered_count": filtered_count,
        "read_is_pure": True,
        "llm_calls": 0,
    }


def _check_committed_operation_zero_write(
    day_root: Path | str,
    operation_key: Optional[str],
) -> Optional[dict[str, Any]]:
    """Zero-write check whether ``operation_key`` is already recorded on disk."""

    if not operation_key:
        return None
    raw_root, resolved_root, target = _resolve_root_and_path(day_root)
    if _has_symlink_readonly(raw_root, resolved_root, target):
        return None
    state, exists = _read_state_file_zero_write(target)
    if not exists or state is None:
        return None
    ops = state.get("operations")
    if isinstance(ops, Mapping) and operation_key in ops and isinstance(ops[operation_key], Mapping):
        return copy.deepcopy(dict(ops[operation_key]))
    return None


def _enforce_collection_bounds(state: dict[str, Any]) -> None:
    items = state.get("items")
    if isinstance(items, dict) and len(items) > MAX_CATALOG_ITEMS:
        ordered = sorted(
            items.items(),
            key=lambda kv: (
                0 if int((kv[1] or {}).get("stock_remaining", 0)) <= 0 else 1,
                str((kv[1] or {}).get("updated_at") or (kv[1] or {}).get("registered_at") or ""),
                kv[0],
            ),
        )
        drop_n = len(items) - MAX_CATALOG_ITEMS
        for key, _val in ordered[:drop_n]:
            items.pop(key, None)

    events = state.get("events")
    if isinstance(events, dict) and len(events) > MAX_CATALOG_EVENTS:
        ordered_ev = sorted(
            events.items(),
            key=lambda kv: (
                str((kv[1] or {}).get("updated_at") or (kv[1] or {}).get("registered_at") or ""),
                kv[0],
            ),
        )
        drop_n = len(events) - MAX_CATALOG_EVENTS
        for key, _val in ordered_ev[:drop_n]:
            events.pop(key, None)


def register_catalog_batch(
    day_root: Path | str,
    *,
    items: Sequence[Mapping[str, Any]] = (),
    events: Sequence[Mapping[str, Any]] = (),
    source_kind: str = "deterministic_seed",
    trigger_reason: str = "SCHEDULED_RESTOCK",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
    allowed_store_ids: Optional[Sequence[str]] = None,
    allowed_venue_ids: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Validate and atomically register a batch of dynamic catalog items & events."""

    _now_dt, now_str = _format_now(now_iso)
    clean_source = _clean_text(source_kind, max_len=48) or "deterministic_seed"
    clean_trigger = _clean_text(trigger_reason, max_len=64) or "SCHEDULED_RESTOCK"
    clean_op_key: Optional[str] = None
    if operation_key is not None:
        clean_op_key = _clean_text(operation_key, max_len=128)
        if not clean_op_key:
            raise WorldCatalogError("INVALID_OPERATION_KEY", "operation_key must be non-empty")

    valid_items: list[dict[str, Any]] = []
    valid_events: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []

    for idx, raw_item in enumerate(items or ()):
        ok, normalized, reason = validate_candidate_item(
            raw_item, allowed_store_ids=allowed_store_ids
        )
        if ok and normalized is not None:
            normalized["source_kind"] = clean_source
            normalized["trigger_reason"] = clean_trigger
            normalized["registered_at"] = now_str
            normalized["updated_at"] = now_str
            valid_items.append(normalized)
        else:
            rejected_candidates.append(
                {
                    "kind": "item",
                    "index": idx,
                    "reason_code": reason or "INVALID_CANDIDATE_ITEM",
                    "template_id": str(raw_item.get("template_id") or "")
                    if isinstance(raw_item, Mapping)
                    else "",
                    "name_cn": str(raw_item.get("name_cn") or raw_item.get("name") or "")
                    if isinstance(raw_item, Mapping)
                    else "",
                }
            )

    for idx, raw_event in enumerate(events or ()):
        ok, normalized_ev, reason = validate_candidate_event(
            raw_event, allowed_venue_ids=allowed_venue_ids
        )
        if ok and normalized_ev is not None:
            normalized_ev["source_kind"] = clean_source
            normalized_ev["trigger_reason"] = clean_trigger
            normalized_ev["registered_at"] = now_str
            normalized_ev["updated_at"] = now_str
            valid_events.append(normalized_ev)
        else:
            rejected_candidates.append(
                {
                    "kind": "event",
                    "index": idx,
                    "reason_code": reason or "INVALID_CANDIDATE_EVENT",
                    "template_id": str(raw_event.get("template_id") or "")
                    if isinstance(raw_event, Mapping)
                    else "",
                }
            )

    raw_root, resolved_root, target = _resolve_root_and_path(day_root)
    _assert_no_symlink_chain(raw_root, resolved_root, target)

    if not valid_items and not valid_events:
        has_input = bool(items) or bool(events)
        return {
            "ok": not has_input,
            "status": "rejected" if has_input else "noop",
            "reason_code": (
                rejected_candidates[0]["reason_code"] if rejected_candidates else None
            ),
            "catalog_path": str(target),
            "revision": 0,
            "registered_items": [],
            "registered_events": [],
            "rejected_candidates": rejected_candidates,
            "source_kind": clean_source,
            "trigger_reason": clean_trigger,
            "updated_at": now_str,
            "llm_calls": 0,
        }

    fp = _tx.payload_fingerprint(
        {
            "op": "register_catalog_batch",
            "items": [
                {
                    "catalog_item_id": row["catalog_item_id"],
                    "template_id": row["template_id"],
                    "store_id": row["store_id"],
                    "price": row["price"],
                    "stock_initial": row["stock_initial"],
                }
                for row in valid_items
            ],
            "events": [
                {
                    "event_id": ev["event_id"],
                    "template_id": ev["template_id"],
                    "venue_id": ev["venue_id"],
                }
                for ev in valid_events
            ],
            "source_kind": clean_source,
            "trigger_reason": clean_trigger,
        }
    )

    with _tx.state_file_lock(target):
        _assert_no_symlink_chain(raw_root, resolved_root, target)
        state, exists = _read_state_file_zero_write(target)
        if not exists or state is None:
            state = _empty_state(now_str)

        if clean_op_key is not None:
            kind, existing = _tx.reconcile_evicted_or_active_op(
                resolved_root,
                state,
                clean_op_key,
                dict_key="operations",
                payload_hash=fp,
            )
            if kind == "hit_full" and existing is not None:
                replay = copy.deepcopy(existing)
                replay["status"] = "already_applied"
                replay["idempotent_replay"] = True
                replay["ok"] = True
                return replay
            if kind == "hit_evicted" and existing is not None:
                return {
                    "ok": True,
                    "status": "already_applied",
                    "idempotent_replay": True,
                    "operation_key": clean_op_key,
                    "revision": existing.get("revision", state.get("revision", 0)),
                    "catalog_path": str(target),
                    "registered_items": [],
                    "registered_events": [],
                    "rejected_candidates": rejected_candidates,
                    "llm_calls": 0,
                }
            if kind in ("conflict", "expired") and existing is not None:
                return {
                    "ok": False,
                    "status": "rejected",
                    "reason_code": existing.get("reason_code", "OPERATION_KEY_CONFLICT"),
                    "operation_key": clean_op_key,
                    "catalog_path": str(target),
                    "registered_items": [],
                    "registered_events": [],
                    "rejected_candidates": rejected_candidates,
                    "llm_calls": 0,
                }

        items_map = state.setdefault("items", {})
        events_map = state.setdefault("events", {})
        cooldowns_map = state.setdefault("store_cooldowns", {})

        stored_items: list[dict[str, Any]] = []
        touched_stores: set[str] = set()
        for item in valid_items:
            cid = item["catalog_item_id"]
            prev = items_map.get(cid)
            to_store = copy.deepcopy(item)
            if isinstance(prev, Mapping):
                to_store["registered_at"] = prev.get("registered_at") or now_str
                to_store["total_sold"] = int(prev.get("total_sold", 0))
            items_map[cid] = to_store
            stored_items.append(copy.deepcopy(to_store))
            touched_stores.add(str(to_store["store_id"]))

        stored_events: list[dict[str, Any]] = []
        for ev in valid_events:
            eid = ev["event_id"]
            prev_ev = events_map.get(eid)
            to_store_ev = copy.deepcopy(ev)
            if isinstance(prev_ev, Mapping):
                to_store_ev["registered_at"] = prev_ev.get("registered_at") or now_str
            events_map[eid] = to_store_ev
            stored_events.append(copy.deepcopy(to_store_ev))
            touched_stores.add(str(to_store_ev["venue_id"]))

        if clean_source in _GENERATION_SOURCE_KINDS:
            for sid in sorted(touched_stores):
                cooldowns_map[sid] = now_str

        _enforce_collection_bounds(state)

        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str

        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "idempotent_replay": False,
            "revision": state["revision"],
            "catalog_path": str(target),
            "registered_items": stored_items,
            "registered_events": stored_events,
            "rejected_candidates": rejected_candidates,
            "source_kind": clean_source,
            "trigger_reason": clean_trigger,
            "updated_at": now_str,
            "llm_calls": 0,
        }
        if clean_op_key is not None:
            result["operation_key"] = clean_op_key
            _tx.stamp_receipt(
                state,
                clean_op_key,
                result,
                revision=state["revision"],
                dict_key="operations",
                payload_hash=fp,
                committed_at=now_str,
            )
            _tx.trim_receipts_by_op_seq(
                state,
                dict_key="operations",
                max_keep=MAX_OP_RECEIPTS,
            )

        _atomic_write_json_0600(raw_root, resolved_root, target, state)
        return result


def decrement_dynamic_item_stock(
    day_root: Path | str,
    *,
    catalog_item_id: str,
    purchase_intent_id: str,
    quantity: int = 1,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Atomically decrement ``stock_remaining`` idempotently by ``purchase_intent_id``.

    Refuses with ``ITEM_OUT_OF_STOCK`` when ``stock_remaining < quantity``.
    """

    _now_dt, now_str = _format_now(now_iso)
    cid = str(catalog_item_id or "").strip()
    intent = str(purchase_intent_id or "").strip()
    if not cid or not _SAFE_ID_RE.fullmatch(cid):
        return {
            "ok": False,
            "status": "rejected",
            "reason_code": "INVALID_CATALOG_ITEM_ID",
        }
    if not intent or len(intent) > 128 or any(ch in intent for ch in "\r\n\x00"):
        return {
            "ok": False,
            "status": "rejected",
            "reason_code": "INVALID_PURCHASE_INTENT_ID",
        }
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return {
            "ok": False,
            "status": "rejected",
            "reason_code": "INVALID_QUANTITY",
        }

    raw_root, resolved_root, target = _resolve_root_and_path(day_root)
    _assert_no_symlink_chain(raw_root, resolved_root, target)

    op_key = f"stock_dec:{intent}"
    fp = _tx.payload_fingerprint(
        {
            "op": "decrement_dynamic_item_stock",
            "catalog_item_id": cid,
            "purchase_intent_id": intent,
            "quantity": int(quantity),
        }
    )

    with _tx.state_file_lock(target):
        _assert_no_symlink_chain(raw_root, resolved_root, target)
        state, exists = _read_state_file_zero_write(target)
        if not exists or state is None:
            return {
                "ok": False,
                "status": "not_found",
                "reason_code": "DYNAMIC_ITEM_NOT_FOUND",
                "catalog_item_id": cid,
            }

        kind, existing = _tx.reconcile_evicted_or_active_op(
            resolved_root,
            state,
            op_key,
            dict_key="operations",
            payload_hash=fp,
        )
        if kind == "hit_full" and existing is not None:
            replay = copy.deepcopy(existing)
            replay["status"] = "already_applied"
            replay["idempotent_replay"] = True
            replay["ok"] = True
            return replay
        if kind == "hit_evicted" and existing is not None:
            cur_item = (state.get("items") or {}).get(cid)
            return {
                "ok": True,
                "status": "already_applied",
                "idempotent_replay": True,
                "catalog_item_id": cid,
                "purchase_intent_id": intent,
                "stock_remaining": (
                    int(cur_item.get("stock_remaining", 0))
                    if isinstance(cur_item, Mapping)
                    else 0
                ),
                "revision": existing.get("revision", state.get("revision", 0)),
            }
        if kind in ("conflict", "expired") and existing is not None:
            return {
                "ok": False,
                "status": "rejected",
                "reason_code": existing.get("reason_code", "OPERATION_KEY_CONFLICT"),
                "catalog_item_id": cid,
                "purchase_intent_id": intent,
            }

        items_map = state.get("items")
        item = items_map.get(cid) if isinstance(items_map, dict) else None
        if not isinstance(item, dict):
            return {
                "ok": False,
                "status": "not_found",
                "reason_code": "DYNAMIC_ITEM_NOT_FOUND",
                "catalog_item_id": cid,
            }

        current_stock = int(item.get("stock_remaining", 0))
        if current_stock < int(quantity):
            return {
                "ok": False,
                "status": "rejected",
                "reason_code": "ITEM_OUT_OF_STOCK",
                "catalog_item_id": cid,
                "purchase_intent_id": intent,
                "stock_remaining": current_stock,
                "requested_quantity": int(quantity),
            }

        new_stock = current_stock - int(quantity)
        item["stock_remaining"] = new_stock
        item["total_sold"] = int(item.get("total_sold", 0)) + int(quantity)
        item["updated_at"] = now_str
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str

        receipt: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "idempotent_replay": False,
            "catalog_item_id": cid,
            "purchase_intent_id": intent,
            "quantity": int(quantity),
            "stock_before": current_stock,
            "stock_remaining": new_stock,
            "revision": state["revision"],
            "updated_at": now_str,
        }
        _tx.stamp_receipt(
            state,
            op_key,
            receipt,
            revision=state["revision"],
            dict_key="operations",
            payload_hash=fp,
            committed_at=now_str,
        )
        _tx.trim_receipts_by_op_seq(
            state,
            dict_key="operations",
            max_keep=MAX_OP_RECEIPTS,
        )
        _atomic_write_json_0600(raw_root, resolved_root, target, state)
        return receipt


def restock_dynamic_item(
    day_root: Path | str,
    *,
    catalog_item_id: str,
    add_units: int,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Atomically add stock units to an existing dynamic catalog item."""

    _now_dt, now_str = _format_now(now_iso)
    cid = str(catalog_item_id or "").strip()
    if not cid or not _SAFE_ID_RE.fullmatch(cid):
        return {
            "ok": False,
            "status": "rejected",
            "reason_code": "INVALID_CATALOG_ITEM_ID",
        }
    if (
        isinstance(add_units, bool)
        or not isinstance(add_units, int)
        or not (1 <= add_units <= MAX_STOCK_UNITS)
    ):
        return {
            "ok": False,
            "status": "rejected",
            "reason_code": "INVALID_RESTOCK_UNITS",
        }
    clean_op_key: Optional[str] = None
    if operation_key is not None:
        clean_op_key = _clean_text(operation_key, max_len=128)
        if not clean_op_key:
            return {
                "ok": False,
                "status": "rejected",
                "reason_code": "INVALID_OPERATION_KEY",
            }

    raw_root, resolved_root, target = _resolve_root_and_path(day_root)
    _assert_no_symlink_chain(raw_root, resolved_root, target)

    fp = _tx.payload_fingerprint(
        {
            "op": "restock_dynamic_item",
            "catalog_item_id": cid,
            "add_units": int(add_units),
        }
    )

    with _tx.state_file_lock(target):
        _assert_no_symlink_chain(raw_root, resolved_root, target)
        state, exists = _read_state_file_zero_write(target)
        if not exists or state is None:
            return {
                "ok": False,
                "status": "not_found",
                "reason_code": "DYNAMIC_ITEM_NOT_FOUND",
                "catalog_item_id": cid,
            }

        if clean_op_key is not None:
            kind, existing = _tx.reconcile_evicted_or_active_op(
                resolved_root,
                state,
                clean_op_key,
                dict_key="operations",
                payload_hash=fp,
            )
            if kind == "hit_full" and existing is not None:
                replay = copy.deepcopy(existing)
                replay["status"] = "already_applied"
                replay["idempotent_replay"] = True
                replay["ok"] = True
                return replay
            if kind == "hit_evicted" and existing is not None:
                return {
                    "ok": True,
                    "status": "already_applied",
                    "idempotent_replay": True,
                    "catalog_item_id": cid,
                    "revision": existing.get("revision", state.get("revision", 0)),
                }
            if kind in ("conflict", "expired") and existing is not None:
                return {
                    "ok": False,
                    "status": "rejected",
                    "reason_code": existing.get("reason_code", "OPERATION_KEY_CONFLICT"),
                    "catalog_item_id": cid,
                }

        items_map = state.get("items")
        item = items_map.get(cid) if isinstance(items_map, dict) else None
        if not isinstance(item, dict):
            return {
                "ok": False,
                "status": "not_found",
                "reason_code": "DYNAMIC_ITEM_NOT_FOUND",
                "catalog_item_id": cid,
            }

        before = int(item.get("stock_remaining", 0))
        after = min(MAX_STOCK_UNITS, before + int(add_units))
        item["stock_remaining"] = after
        item["stock_initial"] = max(int(item.get("stock_initial", 0)), after)
        item["updated_at"] = now_str
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str

        receipt: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "idempotent_replay": False,
            "catalog_item_id": cid,
            "add_units": int(add_units),
            "stock_before": before,
            "stock_remaining": after,
            "revision": state["revision"],
            "updated_at": now_str,
        }
        if clean_op_key is not None:
            receipt["operation_key"] = clean_op_key
            _tx.stamp_receipt(
                state,
                clean_op_key,
                receipt,
                revision=state["revision"],
                dict_key="operations",
                payload_hash=fp,
                committed_at=now_str,
            )
            _tx.trim_receipts_by_op_seq(
                state,
                dict_key="operations",
                max_keep=MAX_OP_RECEIPTS,
            )

        _atomic_write_json_0600(raw_root, resolved_root, target, state)
        return receipt


def find_dynamic_fixture_light_declaration_zero_write(
    day_root_or_home: Optional[Path | str],
    catalog_item_id: str,
) -> Optional[dict[str, Any]]:
    """Zero-write room light fixture lookup for dynamic ``decor_fixture`` lamps."""

    if day_root_or_home is None or not isinstance(catalog_item_id, str):
        return None
    cid = catalog_item_id.strip()
    if not cid:
        return None
    snap = read_dynamic_catalog_zero_write(day_root_or_home, include_out_of_stock=True)
    if snap.get("barrier_fail_closed"):
        return None
    item = (snap.get("items_by_id") or {}).get(cid)
    if not isinstance(item, Mapping):
        return None
    if str(item.get("template_id") or "") != "decor_fixture":
        return None
    light_band = item.get("light_band")
    if light_band is None and isinstance(item.get("declaration"), Mapping):
        light_band = item["declaration"].get("light_band")
    if not isinstance(light_band, str) or light_band not in ("dim", "moderate", "bright"):
        return None
    return {
        "catalog_item_id": cid,
        "powerable": True,
        "light_band": light_band,
        "declared_source": f"catalog:{cid}",
        "label": str(item.get("name_cn") or item.get("name") or cid),
    }


__all__ = [
    "ALLOWED_CONTAINER_CAPABILITIES",
    "ALLOWED_FIXTURE_SURFACES",
    "ALLOWED_LIGHT_BANDS",
    "CURRENCY",
    "DEFAULT_INITIAL_STOCK",
    "EVENT_TEMPLATES",
    "FORBIDDEN_CANDIDATE_FIELDS",
    "ITEM_CAPABILITY_TEMPLATES",
    "MAX_CATALOG_EVENTS",
    "MAX_CATALOG_ITEMS",
    "SCHEMA",
    "STATE_REL_PATH",
    "WorldCatalogError",
    "catalog_state_path",
    "decrement_dynamic_item_stock",
    "find_dynamic_fixture_light_declaration_zero_write",
    "read_dynamic_catalog_zero_write",
    "register_catalog_batch",
    "restock_dynamic_item",
    "validate_candidate_event",
    "validate_candidate_item",
]
