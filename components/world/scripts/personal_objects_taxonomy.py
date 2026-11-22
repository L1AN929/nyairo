#!/usr/bin/env python3
"""v0.9 Pillar D -- Emergent Object Reuse, Self-Invented Taxonomy & Blocked-Capability Collector.

Architecture & Contracts (Master Blueprint Section 5.1-5.4):
- State file: ``<day_root>/world/personal_objects_taxonomy.json``
  (``SCHEMA = "world.v09.personal_objects_taxonomy.v1"``), rooted strictly via
  ``instance_paths.resolve_instance_paths(day_root).day_root``.
- Three bounded sub-domains:
  1. ``repurposed_objects`` (max 48): owned economy items, starter room vessels
     (``ceramic_mug``, ``pen_cup``, ``paper_card_box``), or emptied consumable
     containers (``cookie_tin``, ``tea_canister``, ``jam_jar``) reused within
     stable physical capabilities (``STORE_ITEMS``, ``ATTACH_LABEL``,
     ``ADD_NOTE``, ``BREW_OR_DRINK``). Labeling an object (e.g. naming a box
     "收音机") changes only ``custom_label_cn`` and NEVER alters its physical
     ``supported_capabilities``. Drinkware holding stored items blocks brewing
     via ``VESSEL_OCCUPIED_BY_STORED_ITEMS`` until emptied.
  2. ``custom_categories`` (max 24) + ``categorized_items`` (max 96): starts
     strictly empty (``{}``) with zero pre-baked categories so TA's personal
     shelves emerge solely from lived reading, drafts, and objects. Moving an
     item between categories preserves dated ``revision_history[]`` entries.
  3. ``blocked_capability_log`` (max 48 clusters): distinguishes normal world
     friction (``TEMPORARY_WORLD_BLOCKER_CODES`` ->
     ``WORLD_CONDITION_TEMPORARY``, ``logged_as_capability_gap=False``) from
     genuine missing capability primitives clustered into canonical groups
     (``ATTACH_MEDIA_OR_CLIPPING``, ``SPATIAL_MAP_ANNOTATION``,
     ``TEMPORAL_CRAFT_PROCESS``, ``LEND_OR_EXCHANGE_PROTOCOL``,
     ``AUDIO_PLAYBACK_DEVICE``, or ``CUSTOM:<slug>``) with deduplicated
     ``sample_attempts`` (max 5) and ranked frequency ordering.
- All read/check helpers are 100% zero-write (0 dirs, 0 files, 0 ``.lock``),
  ``llm_calls == 0``, and enforce
  ``v02_representation_lease_forget.load_unified_forget_barrier_readonly``.
- All write paths acquire ``state_tx_guard.state_file_lock``, reject symlinks
  on ``day_root`` / parent / target / lock, write atomic ``0600`` UTF-8 LF JSON
  (``fsync`` + ``os.replace``), and enforce ``operation_key`` idempotency.
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
except ImportError:  # pragma: no cover - flat script import fallback
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover - flat script import fallback
    import state_tx_guard as _tx  # type: ignore[no-redef]

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - flat script import fallback
    import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Public Schema, Constants & Error Type
# ---------------------------------------------------------------------------

SCHEMA = "world.v09.personal_objects_taxonomy.v1"
STATE_SCHEMA = SCHEMA
STATE_REL_PATH: tuple[str, ...] = ("world", "personal_objects_taxonomy.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

MAX_REPURPOSED_OBJECTS = 48
MAX_CUSTOM_CATEGORIES = 24
MAX_CATEGORIZED_ITEMS = 96
MAX_BLOCKED_CAPABILITY_CLUSTERS = 48
MAX_SAMPLE_ATTEMPTS_PER_CLUSTER = 5
MAX_STORED_ITEMS_PER_OBJECT = 12
MAX_NOTES_PER_OBJECT = 24
MAX_USE_HISTORY_PER_OBJECT = 32
MAX_REVISION_HISTORY_PER_ITEM = 32
MAX_TITLE_HISTORY_PER_CATEGORY = 16
MAX_OPERATION_RECEIPTS = 200

ALLOWED_VISIBILITIES: tuple[str, ...] = ("shared", "private")
ALLOWED_ACTOR_SIDES: tuple[str, ...] = ("subject", "partner")

REUSABLE_OBJECT_CAPABILITIES: tuple[str, ...] = (
    "STORE_ITEMS",
    "ATTACH_LABEL",
    "ADD_NOTE",
    "BREW_OR_DRINK",
)
_REUSABLE_CAPS_SET: frozenset[str] = frozenset(REUSABLE_OBJECT_CAPABILITIES)

EMPTIED_CONSUMABLE_KINDS: frozenset[str] = frozenset({
    "cookie_tin",
    "tea_canister",
    "jam_jar",
})

STARTER_VESSEL_DEFAULTS: dict[str, dict[str, Any]] = {
    "ceramic_mug": {
        "base_name_cn": "陶瓷马克杯",
        "drinkware": True,
        "capacity_slots": 3,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE", "BREW_OR_DRINK"),
        "origin_kind": "starter_vessel",
    },
    "pen_cup": {
        "base_name_cn": "桌面笔筒",
        "drinkware": False,
        "capacity_slots": 6,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"),
        "origin_kind": "starter_vessel",
    },
    "paper_card_box": {
        "base_name_cn": "牛皮纸卡片盒",
        "drinkware": False,
        "capacity_slots": 8,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"),
        "origin_kind": "starter_vessel",
    },
    "cookie_tin": {
        "base_name_cn": "空饼干铁盒",
        "drinkware": False,
        "capacity_slots": 6,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"),
        "origin_kind": "emptied_consumable",
    },
    "tea_canister": {
        "base_name_cn": "空茶叶铁罐",
        "drinkware": False,
        "capacity_slots": 4,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"),
        "origin_kind": "emptied_consumable",
    },
    "jam_jar": {
        "base_name_cn": "洗净果酱玻璃瓶",
        "drinkware": False,
        "capacity_slots": 4,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"),
        "origin_kind": "emptied_consumable",
    },
    "glass_cup": {
        "base_name_cn": "玻璃水杯",
        "drinkware": True,
        "capacity_slots": 2,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE", "BREW_OR_DRINK"),
        "origin_kind": "starter_vessel",
    },
    "desk_tray": {
        "base_name_cn": "桌面托盘",
        "drinkware": False,
        "capacity_slots": 6,
        "supported_capabilities": ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"),
        "origin_kind": "starter_vessel",
    },
}

TEMPORARY_WORLD_BLOCKER_CODES: frozenset[str] = frozenset({
    "ITEM_OUT_OF_STOCK",
    "STORE_CLOSED",
    "LOCATION_CLOSED",
    "INSUFFICIENT_FUNDS",
    "WEATHER_CANCELLED",
    "VESSEL_OCCUPIED_BY_STORED_ITEMS",
    "CUP_OCCUPIED_BY_STATIONERY",
    "OBJECT_CAPACITY_FULL",
    "CONTAINER_FULL",
    "HOLD_EXPIRED",
    "ILLNESS_REST_REQUIRED",
    "SCALDING_HOT_WAIT",
})

CANONICAL_CAPABILITY_CLUSTERS: tuple[str, ...] = (
    "ATTACH_MEDIA_OR_CLIPPING",
    "SPATIAL_MAP_ANNOTATION",
    "TEMPORAL_CRAFT_PROCESS",
    "LEND_OR_EXCHANGE_PROTOCOL",
    "AUDIO_PLAYBACK_DEVICE",
)

_CLUSTER_TITLES_CN: dict[str, str] = {
    "ATTACH_MEDIA_OR_CLIPPING": "图文剪报与附件粘贴能力",
    "SPATIAL_MAP_ANNOTATION": "空间地图标注与路线记录能力",
    "TEMPORAL_CRAFT_PROCESS": "跨时段手作与慢工计时能力",
    "LEND_OR_EXCHANGE_PROTOCOL": "借出与物品交换协议能力",
    "AUDIO_PLAYBACK_DEVICE": "音频播放与收音设备能力",
}

_CLUSTER_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ATTACH_MEDIA_OR_CLIPPING",
        (
            "attach_media",
            "attach_image",
            "attach_photo",
            "attach_clipping",
            "clipping",
            "media_clip",
            "photo",
            "image",
            "picture",
            "screenshot",
            "sticker",
            "scrapbook",
            "press_flower",
            "attachment",
            "剪报",
            "贴图",
            "贴照片",
            "贴剪报",
            "照片",
            "图片",
            "插图",
            "贴纸",
            "附图",
            "夹贴",
            "贴进",
            "粘贴图片",
            "压花书签贴",
        ),
    ),
    (
        "SPATIAL_MAP_ANNOTATION",
        (
            "spatial_map",
            "map_annotation",
            "map_pin",
            "pin_map",
            "route_mark",
            "waypoint",
            "floorplan",
            "地图标注",
            "标点",
            "路线标记",
            "手绘地图",
            "画路线",
            "平面图",
            "路线图",
            "标注地点",
        ),
    ),
    (
        "TEMPORAL_CRAFT_PROCESS",
        (
            "temporal_craft",
            "ferment",
            "brew_days",
            "steep_days",
            "curing",
            "kiln",
            "bake_timer",
            "dry_flower",
            "pickling",
            "aged_tea",
            "发酵",
            "腌制",
            "风干",
            "晾晒",
            "慢炖",
            "多日手工",
            "窑烧",
            "浸泡数日",
            "定时工艺",
            "阴干",
        ),
    ),
    (
        "LEND_OR_EXCHANGE_PROTOCOL",
        (
            "lend",
            "borrow",
            "exchange",
            "swap_book",
            "trade_item",
            "return_due",
            "借书",
            "借出",
            "出借",
            "互借",
            "交换协议",
            "以物换物",
            "归还期限",
            "转借",
        ),
    ),
    (
        "AUDIO_PLAYBACK_DEVICE",
        (
            "audio",
            "radio",
            "play_music",
            "playback",
            "speaker",
            "cassette",
            "vinyl",
            "record_player",
            "podcast",
            "收音机",
            "播放音频",
            "播放音乐",
            "播放广播",
            "放歌",
            "听广播",
            "音响",
            "音箱",
            "磁带",
            "黑胶",
            "音频",
        ),
    ),
)

_GENERIC_MISSING_BLOCKERS: frozenset[str] = frozenset({
    "CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
    "UNSUPPORTED_CAPABILITY_TEMPLATE",
    "MISSING_CAPABILITY",
    "UNSUPPORTED_ACTION",
    "UNSUPPORTED_PRIMITIVE",
})

FORBIDDEN_COMMAND_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "author_side",
    "uid",
    "capability",
    "clear_reservations",
    "force_capabilities",
    "grant_capability",
    "affection_score",
})

SUPPORTED_COMMAND_ACTIONS: tuple[str, ...] = (
    "register_or_repurpose_object",
    "repurpose_object",
    "store_item_in_object",
    "attach_label_to_object",
    "add_note_to_object",
    "remove_stored_item_from_object",
    "empty_stored_items",
    "create_custom_category",
    "assign_or_move_category_item",
    "revise_custom_category",
    "record_blocked_attempt",
    "use_object_capability",
)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9_]+")
_PRIVATE_CATEGORY_REDACTED_CN = "未公开分类"


class PersonalObjectsTaxonomyError(Exception):
    """Structured error carrying HTTP status code and machine reason code."""

    def __init__(self, status_code: int, reason_code: str, detail: str = "") -> None:
        super().__init__(detail or reason_code)
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": self.status_code,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "llm_calls": 0,
        }


# ---------------------------------------------------------------------------
# Sanitization, Time, Path & Atomic 0600 Write Helpers
# ---------------------------------------------------------------------------


def _strip_emojis_and_ctrl(text: str, *, max_len: int = 240) -> str:
    cleaned_chars: list[str] = []
    for ch in str(text or ""):
        cat = unicodedata.category(ch)
        if cat == "So":
            continue
        if cat in ("Cc", "Cf") and ch not in ("\n", "\t"):
            continue
        if ch in ("\r", "\n", "\t"):
            cleaned_chars.append(" ")
        else:
            cleaned_chars.append(ch)
    collapsed = " ".join("".join(cleaned_chars).split())
    return collapsed[:max_len]


def _clean_text_field(
    value: Any,
    *,
    field_name: str,
    min_len: int = 0,
    max_len: int = 160,
) -> str:
    if value is None:
        if min_len > 0:
            raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", f"Missing required field '{field_name}'")
        return ""
    if not isinstance(value, str):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", f"Field '{field_name}' must be a string")
    cleaned = _strip_emojis_and_ctrl(value, max_len=max_len)
    if len(cleaned) < min_len:
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", f"Field '{field_name}' is too short or empty")
    return cleaned


def _clean_id_token(
    value: Any,
    *,
    field_name: str,
    required: bool = True,
    max_len: int = 96,
) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", f"Missing required identifier '{field_name}'")
        return ""
    if not isinstance(value, str):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", f"Identifier '{field_name}' must be a string")
    token = _strip_emojis_and_ctrl(value, max_len=max_len)
    if not token or not _SAFE_ID_RE.fullmatch(token):
        raise PersonalObjectsTaxonomyError(
            400,
            "INVALID_FIELDS",
            f"Identifier '{field_name}' has unsupported characters: {value!r}",
        )
    return token


def _validate_operation_key(operation_key: Optional[str]) -> Optional[str]:
    if operation_key is None:
        return None
    if not isinstance(operation_key, str):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "Field 'operation_key' must be a string")
    raw = operation_key.strip()
    if not raw:
        return None
    if len(raw) > 128:
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "Field 'operation_key' is too long")
    cleaned = _strip_emojis_and_ctrl(raw, max_len=128)
    return cleaned or None


def _normalize_actor_side(actor_side: Any, *, require_subject: bool = False) -> str:
    norm = str(actor_side or "partner").strip().lower()
    if norm not in ALLOWED_ACTOR_SIDES:
        raise PersonalObjectsTaxonomyError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Unsupported actor_side: {actor_side!r}",
        )
    if require_subject and norm != "subject":
        raise PersonalObjectsTaxonomyError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "Personal object and taxonomy mutations are subject-only",
        )
    return norm


def _normalize_visibility(visibility: Any, *, default: str = "shared") -> str:
    if visibility is None or (isinstance(visibility, str) and not visibility.strip()):
        return default
    if not isinstance(visibility, str):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "Field 'visibility' must be a string")
    norm = visibility.strip().lower()
    if norm not in ALLOWED_VISIBILITIES:
        raise PersonalObjectsTaxonomyError(
            400,
            "INVALID_FIELDS",
            f"Unsupported visibility {visibility!r}; expected one of {ALLOWED_VISIBILITIES}",
        )
    return norm


def _parse_dt(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=DEFAULT_TZ)
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(DEFAULT_TZ).isoformat(timespec="seconds")


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    if now_iso is not None:
        dt = _parse_dt(now_iso)
        if dt is None:
            raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", f"Invalid ISO timestamp: {now_iso!r}")
        return dt, _format_iso(dt)
    dt = datetime.now(tz=DEFAULT_TZ)
    return dt, _format_iso(dt)


def _is_regular_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _has_symlink_in_read_chain(raw_input: Path | str, resolved_day_root: Path, target_file: Path) -> bool:
    raw_path = Path(str(raw_input)).expanduser()
    for cand in (raw_path, resolved_day_root, target_file.parent, target_file):
        try:
            if cand.is_symlink():
                return True
        except OSError:
            return True
    return False


def _read_json_readonly(path: Path) -> Optional[dict[str, Any]]:
    if not _is_regular_file(path):
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
        return dict(doc) if isinstance(doc, Mapping) else None
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None


def _resolve_day_root(day_root: Path | str) -> Path:
    return _instance_paths.resolve_instance_paths(day_root).day_root


def personal_objects_taxonomy_state_path(day_root: Path | str) -> Path:
    """Return canonical ``<day_root>/world/personal_objects_taxonomy.json`` path (zero-write)."""
    return _resolve_day_root(day_root).joinpath(*STATE_REL_PATH)


taxonomy_state_path = personal_objects_taxonomy_state_path


def _assert_no_symlink_chain(raw_input: Path | str, resolved_day_root: Path, target_file: Path) -> None:
    raw_path = Path(str(raw_input)).expanduser()
    lock_path = Path(str(target_file) + ".lock")
    candidates: list[tuple[str, Path]] = [
        ("input_path", raw_path),
        ("day_root", resolved_day_root),
        ("world_dir", target_file.parent),
        ("state_file", target_file),
        ("lock_file", lock_path),
    ]
    # Also inspect intermediate path components between day_root and target_file if nested
    for parent in target_file.parents:
        if parent == resolved_day_root:
            break
        candidates.append(("parent_dir", parent))
    for label, cand in candidates:
        try:
            if cand.is_symlink():
                raise PersonalObjectsTaxonomyError(400, "UNSAFE_SYMLINK", f"{label} must not be a symlink")
        except OSError as exc:
            raise PersonalObjectsTaxonomyError(400, "UNSAFE_SYMLINK", f"Cannot inspect {label}: {exc}") from exc


def _atomic_write_json_0600(
    raw_input: Path | str,
    resolved_day_root: Path,
    target_file: Path,
    payload: Mapping[str, Any],
) -> None:
    _assert_no_symlink_chain(raw_input, resolved_day_root, target_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_chain(raw_input, resolved_day_root, target_file)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    raw_bytes = serialized.replace("\r\n", "\n").encode("utf-8")
    tmp_name = f".{target_file.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    tmp_path = target_file.parent / tmp_name
    try:
        if os.name == "posix":
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        else:
            with open(tmp_path, "wb") as fh:
                fh.write(raw_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        os.replace(str(tmp_path), str(target_file))
    except Exception:
        try:
            if tmp_path.exists() or tmp_path.is_symlink():
                tmp_path.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Internal State Shape & Idempotency Helpers
# ---------------------------------------------------------------------------


def _empty_state(now_str: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "revision": 0,
        "updated_at": now_str,
        "next_object_seq": 0,
        "next_stored_item_seq": 0,
        "next_note_seq": 0,
        "next_category_seq": 0,
        "next_categorized_item_seq": 0,
        "op_seq": 0,
        "next_op_seq": 0,
        "repurposed_objects": {},
        "custom_categories": {},
        "categorized_items": {},
        "blocked_capability_log": {},
        "operation_receipts": {},
        "committed_op_ring": [],
    }


def _ensure_state_hydrated(raw: Optional[Mapping[str, Any]], now_str: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return _empty_state(now_str)
    state = copy.deepcopy(dict(raw))
    base = _empty_state(now_str)
    for key, default_val in base.items():
        if key not in state or state[key] is None:
            state[key] = copy.deepcopy(default_val)
    for dict_field in (
        "repurposed_objects",
        "custom_categories",
        "categorized_items",
        "blocked_capability_log",
        "operation_receipts",
    ):
        if not isinstance(state.get(dict_field), dict):
            state[dict_field] = {}
    if not isinstance(state.get("committed_op_ring"), list):
        state["committed_op_ring"] = []
    state["schema"] = SCHEMA
    return state


def _check_op_replay(
    resolved_root: Path,
    state: Mapping[str, Any],
    operation_key: Optional[str],
    payload_hash: str,
) -> Optional[dict[str, Any]]:
    if not operation_key:
        return None
    reconcile_kind, hit = _tx.reconcile_evicted_or_active_op(
        resolved_root,
        state,
        operation_key,
        dict_key="operation_receipts",
        payload_hash=payload_hash,
    )
    if reconcile_kind == "conflict":
        raise PersonalObjectsTaxonomyError(
            409,
            "OPERATION_KEY_CONFLICT",
            f"operation_key already used with a different payload: {operation_key}",
        )
    if reconcile_kind == "expired":
        raise PersonalObjectsTaxonomyError(
            409,
            "RETRY_WINDOW_EXPIRED",
            f"operation_key retry window expired: {operation_key}",
        )
    if reconcile_kind in ("hit_full", "hit_evicted") and isinstance(hit, Mapping):
        replay = copy.deepcopy(dict(hit))
        replay["ok"] = True
        replay["already_applied"] = True
        replay["replayed"] = True
        replay["status"] = "already_applied"
        replay["command_status"] = "DUPLICATE"
        replay["llm_calls"] = 0
        return replay
    return None


def _finalize_and_persist(
    raw_input: Path | str,
    resolved_root: Path,
    state_path: Path,
    state: dict[str, Any],
    result: dict[str, Any],
    *,
    operation_key: Optional[str],
    payload_hash: str,
    now_str: str,
) -> dict[str, Any]:
    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_str
    result["revision"] = state["revision"]
    result["llm_calls"] = 0
    if operation_key:
        _tx.stamp_receipt(
            state,
            operation_key,
            result,
            revision=state["revision"],
            dict_key="operation_receipts",
            payload_hash=payload_hash,
            committed_at=now_str,
        )
        _tx.trim_receipts_by_op_seq(
            state,
            dict_key="operation_receipts",
            max_keep=MAX_OPERATION_RECEIPTS,
        )
        state["op_seq"] = int(state.get("next_op_seq") or 0)
    _atomic_write_json_0600(raw_input, resolved_root, state_path, state)
    return result


# ---------------------------------------------------------------------------
# Unified Forget Barrier Helpers (Zero-Write)
# ---------------------------------------------------------------------------


def _is_blocked_with_ids(
    entity: Mapping[str, Any],
    extra_ids: Sequence[Any],
    barrier: Mapping[str, Any],
) -> bool:
    if _rlf.is_entity_forget_blocked(entity, barrier):
        return True
    for raw_id in extra_ids:
        if isinstance(raw_id, str) and raw_id.strip():
            if _rlf.is_entity_forget_blocked(raw_id.strip(), barrier):
                return True
        elif isinstance(raw_id, Sequence) and not isinstance(raw_id, (str, bytes)):
            for elem in raw_id:
                if isinstance(elem, str) and elem.strip():
                    if _rlf.is_entity_forget_blocked(elem.strip(), barrier):
                        return True
    return False


def _object_extra_ids(obj: Mapping[str, Any]) -> tuple[Any, ...]:
    prov = obj.get("origin_provenance")
    prov_map: Mapping[str, Any] = prov if isinstance(prov, Mapping) else {}
    return (
        obj.get("object_id"),
        prov_map.get("economy_item_instance_id"),
        prov_map.get("catalog_item_id"),
        prov_map.get("source_ref"),
        prov_map.get("receipt_ref"),
    )


def _find_object_in_map(
    objects_map: Mapping[str, Any],
    object_id: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(objects_map, Mapping) or not object_id:
        return None
    direct = objects_map.get(object_id)
    if isinstance(direct, Mapping):
        return dict(direct)
    for row in objects_map.values():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("object_id") or "") == object_id:
            return dict(row)
        prov = row.get("origin_provenance")
        if isinstance(prov, Mapping):
            if str(prov.get("economy_item_instance_id") or "") == object_id:
                return dict(row)
    return None


def _enrich_object_view(
    obj: Mapping[str, Any],
    *,
    actor_side: str,
    barrier: Mapping[str, Any],
) -> dict[str, Any]:
    out = copy.deepcopy(dict(obj))
    stored_raw = out.get("stored_items")
    stored_clean: list[dict[str, Any]] = []
    if isinstance(stored_raw, list):
        for item in stored_raw:
            if not isinstance(item, Mapping):
                continue
            if _is_blocked_with_ids(
                item,
                (item.get("stored_item_id"), item.get("source_ref")),
                barrier,
            ):
                continue
            stored_clean.append(copy.deepcopy(dict(item)))
    out["stored_items"] = stored_clean

    notes_raw = out.get("notes")
    notes_clean: list[dict[str, Any]] = []
    if isinstance(notes_raw, list):
        for note in notes_raw:
            if not isinstance(note, Mapping):
                continue
            if _is_blocked_with_ids(
                note,
                (note.get("note_id"), note.get("source_ref")),
                barrier,
            ):
                continue
            if actor_side == "partner" and str(note.get("visibility") or "shared") != "shared":
                continue
            notes_clean.append(copy.deepcopy(dict(note)))
    out["notes"] = notes_clean

    history_raw = out.get("use_history")
    history_clean: list[dict[str, Any]] = []
    if isinstance(history_raw, list):
        for entry in history_raw:
            if not isinstance(entry, Mapping):
                continue
            if actor_side == "partner" and str(entry.get("visibility") or "shared") != "shared":
                continue
            if _is_blocked_with_ids(entry, (entry.get("ref_id"),), barrier):
                continue
            history_clean.append(copy.deepcopy(dict(entry)))
    out["use_history"] = history_clean

    cap = int(out.get("capacity_slots") or 1)
    caps = list(out.get("supported_capabilities") or [])
    is_drinkware = bool(out.get("drinkware"))
    stored_count = len(stored_clean)
    occupied_for_drink = bool(is_drinkware and stored_count > 0)
    drink_ready = bool(is_drinkware and "BREW_OR_DRINK" in caps and stored_count == 0)
    if occupied_for_drink:
        drink_blocker: Optional[str] = "VESSEL_OCCUPIED_BY_STORED_ITEMS"
    elif not is_drinkware or "BREW_OR_DRINK" not in caps:
        drink_blocker = "CAPABILITY_NOT_SUPPORTED_BY_OBJECT"
    else:
        drink_blocker = None

    out["stored_count"] = stored_count
    out["available_slots"] = max(0, cap - stored_count)
    out["occupied_for_drink"] = occupied_for_drink
    out["drink_ready"] = drink_ready
    out["drink_blocker_code"] = drink_blocker
    out["shared_with_partner"] = str(out.get("visibility") or "shared") == "shared"
    return out


# ---------------------------------------------------------------------------
# Sub-Domain 1: Emergent Object Reuse & Real Occupancy Physics
# ---------------------------------------------------------------------------


def _append_use_history(
    obj: dict[str, Any],
    *,
    at_iso: str,
    event: str,
    detail_cn: str,
    visibility: str = "shared",
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    history = obj.setdefault("use_history", [])
    entry: dict[str, Any] = {
        "at": at_iso,
        "event": event,
        "detail_cn": _strip_emojis_and_ctrl(detail_cn, max_len=200),
        "visibility": visibility,
    }
    if extra:
        for k, v in extra.items():
            entry[str(k)] = v
    history.append(entry)
    if len(history) > MAX_USE_HISTORY_PER_OBJECT:
        del history[: len(history) - MAX_USE_HISTORY_PER_OBJECT]


def _validate_capabilities_for_object(
    raw_caps: Optional[Sequence[str]],
    *,
    drinkware: bool,
    default_caps: Sequence[str],
) -> list[str]:
    if raw_caps is None:
        caps = [c for c in default_caps if c in _REUSABLE_CAPS_SET]
        if drinkware and "BREW_OR_DRINK" not in caps:
            caps.append("BREW_OR_DRINK")
        if not drinkware:
            caps = [c for c in caps if c != "BREW_OR_DRINK"]
        return caps or ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"]

    if isinstance(raw_caps, (str, bytes)) or not isinstance(raw_caps, Sequence):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "supported_capabilities must be a sequence")
    cleaned: list[str] = []
    for item in raw_caps:
        if not isinstance(item, str):
            raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "capability entries must be strings")
        norm = item.strip().upper()
        if norm not in _REUSABLE_CAPS_SET:
            raise PersonalObjectsTaxonomyError(
                409,
                "CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
                f"Unsupported physical object capability: {item!r}",
            )
        if norm == "BREW_OR_DRINK" and not drinkware:
            raise PersonalObjectsTaxonomyError(
                409,
                "CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
                "Non-drinkware containers cannot declare BREW_OR_DRINK",
            )
        if norm not in cleaned:
            cleaned.append(norm)
    if not cleaned:
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "supported_capabilities must not be empty")
    return cleaned


def _sanitize_origin_provenance(
    raw_prov: Optional[Mapping[str, Any]],
    *,
    origin_kind: str,
    emptied_from_consumable: bool,
    economy_item_instance_id: Optional[str],
    catalog_item_id: Optional[str],
    material_cn: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "origin_kind": origin_kind,
        "emptied_from_consumable": bool(emptied_from_consumable),
    }
    if isinstance(raw_prov, Mapping):
        if raw_prov.get("consumable_still_full") is True:
            raise PersonalObjectsTaxonomyError(
                409,
                "CONTAINER_NOT_EMPTY_YET",
                "Consumable container must be emptied before repurposing",
            )
        rem = raw_prov.get("portions_remaining")
        if isinstance(rem, (int, float)) and not isinstance(rem, bool) and rem > 0:
            raise PersonalObjectsTaxonomyError(
                409,
                "CONTAINER_NOT_EMPTY_YET",
                "Consumable container still has portions remaining",
            )
        for key in (
            "origin_kind",
            "economy_item_instance_id",
            "catalog_item_id",
            "source_ref",
            "receipt_ref",
            "source_item_name_cn",
            "acquired_from_store_id",
            "material_cn",
        ):
            val = raw_prov.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = _strip_emojis_and_ctrl(val, max_len=120)
    if economy_item_instance_id:
        out["economy_item_instance_id"] = _clean_id_token(
            economy_item_instance_id,
            field_name="economy_item_instance_id",
            required=False,
        )
    if catalog_item_id:
        out["catalog_item_id"] = _clean_id_token(
            catalog_item_id,
            field_name="catalog_item_id",
            required=False,
        )
    if material_cn and not out.get("material_cn"):
        out["material_cn"] = _strip_emojis_and_ctrl(material_cn, max_len=32)
    return out


def register_or_repurpose_object(
    day_root: Path | str,
    *,
    object_id: Optional[str] = None,
    object_kind: Optional[str] = None,
    base_name_cn: str = "",
    custom_label_cn: str = "",
    supported_capabilities: Optional[Sequence[str]] = None,
    capacity_slots: Optional[int] = None,
    drinkware: Optional[bool] = None,
    emptied_from_consumable: bool = False,
    consumable_portions_remaining: Optional[int] = None,
    origin_provenance: Optional[Mapping[str, Any]] = None,
    economy_item_instance_id: Optional[str] = None,
    catalog_item_id: Optional[str] = None,
    reusable_container_spec: Optional[Mapping[str, Any]] = None,
    location_hint_cn: str = "书桌一角",
    visibility: Optional[str] = None,
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Bind an owned economy item, starter room vessel, or emptied consumable container for reuse."""
    _normalize_actor_side(actor_side, require_subject=True)
    if isinstance(consumable_portions_remaining, int) and not isinstance(consumable_portions_remaining, bool):
        if consumable_portions_remaining > 0:
            raise PersonalObjectsTaxonomyError(
                409,
                "CONTAINER_NOT_EMPTY_YET",
                "Consumable container must be emptied before repurposing",
            )
    if reusable_container_spec is not None and not isinstance(reusable_container_spec, Mapping):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "reusable_container_spec must be an object")

    spec_map: Mapping[str, Any] = reusable_container_spec if isinstance(reusable_container_spec, Mapping) else {}
    inferred_kind = (
        object_kind
        or (str(spec_map.get("container_kind") or "").strip() or None)
        or (object_id if isinstance(object_id, str) and object_id.strip() in STARTER_VESSEL_DEFAULTS else None)
        or "cookie_tin"
    )
    clean_kind = _clean_id_token(inferred_kind, field_name="object_kind", required=True, max_len=48)
    defaults = STARTER_VESSEL_DEFAULTS.get(clean_kind, {})

    eff_drinkware = (
        bool(drinkware)
        if drinkware is not None
        else (
            bool(spec_map["drinkware"])
            if "drinkware" in spec_map and isinstance(spec_map["drinkware"], bool)
            else bool(defaults.get("drinkware", False))
        )
    )
    if drinkware is not None and not isinstance(drinkware, bool):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "drinkware must be a boolean")

    raw_cap = (
        capacity_slots
        if capacity_slots is not None
        else (
            spec_map.get("capacity_slots")
            if "capacity_slots" in spec_map
            else defaults.get("capacity_slots", 4)
        )
    )
    if isinstance(raw_cap, bool) or not isinstance(raw_cap, int) or not (1 <= raw_cap <= MAX_STORED_ITEMS_PER_OBJECT):
        raise PersonalObjectsTaxonomyError(
            400,
            "INVALID_FIELDS",
            f"capacity_slots must be an integer in 1..{MAX_STORED_ITEMS_PER_OBJECT}",
        )

    spec_caps = spec_map.get("supported_capabilities")
    default_caps = defaults.get(
        "supported_capabilities",
        ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE", "BREW_OR_DRINK")
        if eff_drinkware
        else ("STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"),
    )
    requested_caps = supported_capabilities if supported_capabilities is not None else spec_caps
    clean_caps = _validate_capabilities_for_object(
        requested_caps,
        drinkware=eff_drinkware,
        default_caps=default_caps,
    )

    clean_base_name = _clean_text_field(
        base_name_cn or spec_map.get("label_cn") or defaults.get("base_name_cn") or clean_kind,
        field_name="base_name_cn",
        min_len=1,
        max_len=64,
    )
    clean_custom_label = _clean_text_field(
        custom_label_cn,
        field_name="custom_label_cn",
        min_len=0,
        max_len=64,
    )
    clean_location = _clean_text_field(
        location_hint_cn or "书桌一角",
        field_name="location_hint_cn",
        min_len=1,
        max_len=64,
    )
    explicit_vis = _normalize_visibility(visibility) if visibility is not None else None
    eff_emptied = bool(emptied_from_consumable) or (
        isinstance(origin_provenance, Mapping) and bool(origin_provenance.get("emptied_from_consumable"))
    )
    if eff_emptied:
        origin_kind = "emptied_consumable"
    elif economy_item_instance_id or (
        isinstance(origin_provenance, Mapping) and origin_provenance.get("economy_item_instance_id")
    ):
        origin_kind = "owned_economy_item"
    else:
        origin_kind = str(defaults.get("origin_kind") or "starter_vessel")

    clean_prov = _sanitize_origin_provenance(
        origin_provenance,
        origin_kind=origin_kind,
        emptied_from_consumable=eff_emptied,
        economy_item_instance_id=economy_item_instance_id,
        catalog_item_id=catalog_item_id,
        material_cn=str(spec_map.get("material_cn") or ""),
    )

    clean_obj_id = _clean_id_token(object_id, field_name="object_id", required=False)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "register_or_repurpose_object",
        "object_id": clean_obj_id,
        "object_kind": clean_kind,
        "base_name_cn": clean_base_name,
        "custom_label_cn": clean_custom_label,
        "supported_capabilities": clean_caps,
        "capacity_slots": int(raw_cap),
        "drinkware": eff_drinkware,
        "emptied_from_consumable": eff_emptied,
        "origin_provenance": clean_prov,
        "location_hint_cn": clean_location,
        "visibility": explicit_vis,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        objects = state["repurposed_objects"]
        if not clean_obj_id:
            seq = int(state.get("next_object_seq") or 0) + 1
            state["next_object_seq"] = seq
            clean_obj_id = f"obj_{clean_kind}_{seq:03d}"

        existing = objects.get(clean_obj_id)
        if isinstance(existing, dict):
            # Repurposing an existing object never expands physical capabilities beyond what it was born with.
            existing_caps = list(existing.get("supported_capabilities") or [])
            if supported_capabilities is not None and not set(clean_caps).issubset(set(existing_caps)):
                raise PersonalObjectsTaxonomyError(
                    409,
                    "CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
                    "Repurposing cannot add unsupported physical capabilities to an existing object",
                )
            if clean_custom_label:
                existing["custom_label_cn"] = clean_custom_label
            existing["location_hint_cn"] = clean_location
            if explicit_vis is not None:
                existing["visibility"] = explicit_vis
            eff_vis = str(existing.get("visibility") or "shared")
            existing["origin_provenance"] = {**dict(existing.get("origin_provenance") or {}), **clean_prov}
            existing["updated_at"] = now_str
            _append_use_history(
                existing,
                at_iso=now_str,
                event="repurpose",
                detail_cn=f"重新安排用途（{clean_custom_label or existing.get('base_name_cn', clean_obj_id)}）",
                visibility=eff_vis,
            )
            obj_record = existing
            created = False
        else:
            if len(objects) >= MAX_REPURPOSED_OBJECTS:
                raise PersonalObjectsTaxonomyError(
                    409,
                    "REPURPOSED_OBJECTS_LIMIT_REACHED",
                    f"Maximum {MAX_REPURPOSED_OBJECTS} repurposed objects reached",
                )
            eff_vis = explicit_vis or "shared"
            obj_record = {
                "object_id": clean_obj_id,
                "object_kind": clean_kind,
                "base_name_cn": clean_base_name,
                "custom_label_cn": clean_custom_label,
                "supported_capabilities": list(clean_caps),
                "capacity_slots": int(raw_cap),
                "drinkware": bool(eff_drinkware),
                "emptied_from_consumable": bool(eff_emptied),
                "origin_provenance": clean_prov,
                "location_hint_cn": clean_location,
                "visibility": eff_vis,
                "stored_items": [],
                "notes": [],
                "use_history": [],
                "created_at": now_str,
                "updated_at": now_str,
            }
            _append_use_history(
                obj_record,
                at_iso=now_str,
                event="registered",
                detail_cn=(
                    f"登记空容器用于收纳与留用：{clean_base_name}"
                    if eff_emptied
                    else f"登记可用物件：{clean_base_name}"
                ),
                visibility=eff_vis,
            )
            objects[clean_obj_id] = obj_record
            created = True

        enriched = _enrich_object_view(obj_record, actor_side="subject", barrier={})
        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "created": created,
            "object_id": clean_obj_id,
            "object": enriched,
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


def store_item_in_object(
    day_root: Path | str,
    *,
    object_id: str,
    item_label_cn: str,
    item_kind: str = "keepsake",
    stored_item_id: Optional[str] = None,
    source_ref: str = "",
    note_cn: str = "",
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Store one small item (ticket stub, paper slip, stationery, dried leaf) in a container."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_obj_id = _clean_id_token(object_id, field_name="object_id", required=True)
    clean_label = _clean_text_field(item_label_cn, field_name="item_label_cn", min_len=1, max_len=64)
    clean_item_kind = _clean_id_token(item_kind or "keepsake", field_name="item_kind", required=True, max_len=48)
    clean_stored_id = _clean_id_token(stored_item_id, field_name="stored_item_id", required=False)
    clean_source_ref = _clean_text_field(source_ref, field_name="source_ref", min_len=0, max_len=120)
    clean_note = _clean_text_field(note_cn, field_name="note_cn", min_len=0, max_len=160)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "store_item_in_object",
        "object_id": clean_obj_id,
        "item_label_cn": clean_label,
        "item_kind": clean_item_kind,
        "stored_item_id": clean_stored_id,
        "source_ref": clean_source_ref,
        "note_cn": clean_note,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        obj = state["repurposed_objects"].get(clean_obj_id)
        if not isinstance(obj, dict):
            raise PersonalObjectsTaxonomyError(404, "OBJECT_NOT_FOUND", f"Object not found: {clean_obj_id}")
        caps = list(obj.get("supported_capabilities") or [])
        if "STORE_ITEMS" not in caps:
            raise PersonalObjectsTaxonomyError(
                409,
                "CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
                f"Object '{clean_obj_id}' does not support STORE_ITEMS",
            )

        stored_items = obj.setdefault("stored_items", [])
        cap_slots = int(obj.get("capacity_slots") or 1)
        if len(stored_items) >= cap_slots:
            raise PersonalObjectsTaxonomyError(
                409,
                "OBJECT_CAPACITY_FULL",
                f"Object '{clean_obj_id}' is full ({len(stored_items)}/{cap_slots})",
            )

        if clean_stored_id:
            if any(str(it.get("stored_item_id") or "") == clean_stored_id for it in stored_items if isinstance(it, Mapping)):
                raise PersonalObjectsTaxonomyError(
                    409,
                    "STORED_ITEM_ALREADY_PRESENT",
                    f"Stored item '{clean_stored_id}' is already inside '{clean_obj_id}'",
                )
        else:
            seq = int(state.get("next_stored_item_seq") or 0) + 1
            state["next_stored_item_seq"] = seq
            clean_stored_id = f"si_{seq:04d}_{secrets.token_hex(3)}"

        stored_entry: dict[str, Any] = {
            "stored_item_id": clean_stored_id,
            "label_cn": clean_label,
            "item_kind": clean_item_kind,
            "source_ref": clean_source_ref,
            "note_cn": clean_note,
            "stored_at": now_str,
        }
        stored_items.append(stored_entry)
        obj["updated_at"] = now_str
        _append_use_history(
            obj,
            at_iso=now_str,
            event="store_item",
            detail_cn=f"收入「{clean_label}」",
            visibility=str(obj.get("visibility") or "shared"),
            extra={"ref_id": clean_stored_id},
        )

        enriched = _enrich_object_view(obj, actor_side="subject", barrier={})
        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "object_id": clean_obj_id,
            "stored_item": copy.deepcopy(stored_entry),
            "object": enriched,
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


def attach_label_to_object(
    day_root: Path | str,
    *,
    object_id: str,
    custom_label_cn: str,
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Attach or update ``custom_label_cn`` without EVER changing ``supported_capabilities``."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_obj_id = _clean_id_token(object_id, field_name="object_id", required=True)
    clean_label = _clean_text_field(custom_label_cn, field_name="custom_label_cn", min_len=1, max_len=64)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "attach_label_to_object",
        "object_id": clean_obj_id,
        "custom_label_cn": clean_label,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        obj = state["repurposed_objects"].get(clean_obj_id)
        if not isinstance(obj, dict):
            raise PersonalObjectsTaxonomyError(404, "OBJECT_NOT_FOUND", f"Object not found: {clean_obj_id}")
        caps = list(obj.get("supported_capabilities") or [])
        if "ATTACH_LABEL" not in caps:
            raise PersonalObjectsTaxonomyError(
                409,
                "CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
                f"Object '{clean_obj_id}' does not support ATTACH_LABEL",
            )

        prior_label = str(obj.get("custom_label_cn") or "")
        # Labeling changes ONLY custom_label_cn, NEVER supported_capabilities or drinkware physics.
        obj["custom_label_cn"] = clean_label
        obj["updated_at"] = now_str
        _append_use_history(
            obj,
            at_iso=now_str,
            event="attach_label",
            detail_cn=f"贴上标签「{clean_label}」",
            visibility=str(obj.get("visibility") or "shared"),
            extra={"prior_label_cn": prior_label, "new_label_cn": clean_label},
        )

        enriched = _enrich_object_view(obj, actor_side="subject", barrier={})
        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "object_id": clean_obj_id,
            "prior_label_cn": prior_label,
            "custom_label_cn": clean_label,
            "supported_capabilities": list(obj["supported_capabilities"]),
            "object": enriched,
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


def add_note_to_object(
    day_root: Path | str,
    *,
    object_id: str,
    note_cn: str,
    visibility: str = "shared",
    source_ref: str = "",
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Attach a dated ``shared`` or ``private`` note to an object supporting ``ADD_NOTE``."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_obj_id = _clean_id_token(object_id, field_name="object_id", required=True)
    clean_note = _clean_text_field(note_cn, field_name="note_cn", min_len=1, max_len=240)
    clean_vis = _normalize_visibility(visibility)
    clean_source_ref = _clean_text_field(source_ref, field_name="source_ref", min_len=0, max_len=120)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "add_note_to_object",
        "object_id": clean_obj_id,
        "note_cn": clean_note,
        "visibility": clean_vis,
        "source_ref": clean_source_ref,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        obj = state["repurposed_objects"].get(clean_obj_id)
        if not isinstance(obj, dict):
            raise PersonalObjectsTaxonomyError(404, "OBJECT_NOT_FOUND", f"Object not found: {clean_obj_id}")
        caps = list(obj.get("supported_capabilities") or [])
        if "ADD_NOTE" not in caps:
            raise PersonalObjectsTaxonomyError(
                409,
                "CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
                f"Object '{clean_obj_id}' does not support ADD_NOTE",
            )

        seq = int(state.get("next_note_seq") or 0) + 1
        state["next_note_seq"] = seq
        note_id = f"note_{seq:04d}_{secrets.token_hex(3)}"
        note_entry: dict[str, Any] = {
            "note_id": note_id,
            "note_cn": clean_note,
            "visibility": clean_vis,
            "source_ref": clean_source_ref,
            "created_at": now_str,
        }
        notes = obj.setdefault("notes", [])
        notes.append(note_entry)
        if len(notes) > MAX_NOTES_PER_OBJECT:
            del notes[: len(notes) - MAX_NOTES_PER_OBJECT]

        obj["updated_at"] = now_str
        hist_vis = "private" if (clean_vis == "private" or str(obj.get("visibility") or "shared") == "private") else "shared"
        _append_use_history(
            obj,
            at_iso=now_str,
            event="add_note",
            detail_cn=f"记下一条便签：{clean_note[:48]}",
            visibility=hist_vis,
            extra={"ref_id": note_id},
        )

        enriched = _enrich_object_view(obj, actor_side="subject", barrier={})
        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "object_id": clean_obj_id,
            "note": copy.deepcopy(note_entry),
            "object": enriched,
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


def remove_stored_item_from_object(
    day_root: Path | str,
    *,
    object_id: str,
    stored_item_id: Optional[str] = None,
    item_label_cn: Optional[str] = None,
    remove_all: bool = False,
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Remove one or all stored items from a container, freeing drinkware occupancy when empty."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_obj_id = _clean_id_token(object_id, field_name="object_id", required=True)
    clean_stored_id = _clean_id_token(stored_item_id, field_name="stored_item_id", required=False)
    clean_label = _clean_text_field(item_label_cn, field_name="item_label_cn", min_len=0, max_len=64)
    if not remove_all and not clean_stored_id and not clean_label:
        raise PersonalObjectsTaxonomyError(
            400,
            "INVALID_FIELDS",
            "Provide stored_item_id, item_label_cn, or remove_all=True",
        )
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "remove_stored_item_from_object",
        "object_id": clean_obj_id,
        "stored_item_id": clean_stored_id,
        "item_label_cn": clean_label,
        "remove_all": bool(remove_all),
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
        if bool(barrier.get("fail_closed")):
            raise PersonalObjectsTaxonomyError(
                503,
                "FORGET_BARRIER_FAIL_CLOSED",
                "Unified forget barrier failed closed",
            )
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        obj = state["repurposed_objects"].get(clean_obj_id)
        if not isinstance(obj, dict) or _is_blocked_with_ids(
            obj, (clean_obj_id, *_object_extra_ids(obj)), barrier
        ):
            raise PersonalObjectsTaxonomyError(404, "OBJECT_NOT_FOUND", f"Object not found: {clean_obj_id}")

        stored_items = obj.setdefault("stored_items", [])
        removed: list[dict[str, Any]] = []
        if remove_all:
            removed = copy.deepcopy(stored_items)
            obj["stored_items"] = []
        else:
            match_idx: Optional[int] = None
            for idx, it in enumerate(stored_items):
                if not isinstance(it, Mapping):
                    continue
                if clean_stored_id and str(it.get("stored_item_id") or "") == clean_stored_id:
                    match_idx = idx
                    break
                if not clean_stored_id and clean_label and str(it.get("label_cn") or "") == clean_label:
                    match_idx = idx
                    break
            if match_idx is None:
                raise PersonalObjectsTaxonomyError(
                    404,
                    "STORED_ITEM_NOT_FOUND",
                    f"Stored item not found in '{clean_obj_id}'",
                )
            removed = [copy.deepcopy(stored_items.pop(match_idx))]

        visible_removed = [
            it
            for it in removed
            if isinstance(it, Mapping)
            and not _is_blocked_with_ids(
                it, (it.get("stored_item_id"), it.get("source_ref")), barrier
            )
        ]

        obj["updated_at"] = now_str
        detail_cn = (
            f"清空容器内全部 {len(removed)} 件物品"
            if remove_all
            else f"取出「{removed[0].get('label_cn', '')}」"
        )
        _append_use_history(
            obj,
            at_iso=now_str,
            event="empty_stored_items" if remove_all else "remove_stored_item",
            detail_cn=detail_cn,
            visibility=str(obj.get("visibility") or "shared"),
        )

        enriched = _enrich_object_view(obj, actor_side="subject", barrier=barrier)
        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "object_id": clean_obj_id,
            "removed_items": visible_removed,
            "remaining_count": len(obj["stored_items"]),
            "drink_ready": enriched["drink_ready"],
            "object": enriched,
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


def check_vessel_ready_for_drink_zero_write(
    day_root: Path | str,
    object_id: str,
) -> tuple[bool, str, list[str]]:
    """100% zero-write physical occupancy check for drinkware vessels.

    Returns ``(False, "VESSEL_OCCUPIED_BY_STORED_ITEMS", stored_labels)`` when
    the registered object has ``drinkware is True`` and holds >= 1 non-forgotten
    stored item. Enforces ``load_unified_forget_barrier_readonly(day_root)`` and
    creates 0 directories, 0 files, and 0 ``.lock`` files.
    """
    clean_id = str(object_id or "").strip()
    if not clean_id:
        return True, "UNTRACKED_VESSEL", []
    try:
        resolved_root = _resolve_day_root(day_root)
    except Exception:
        return True, "UNTRACKED_VESSEL", []

    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    if _has_symlink_in_read_chain(day_root, resolved_root, state_path):
        return True, "STATE_SYMLINK_IGNORED", []
    if not _is_regular_file(state_path):
        return True, "NO_TAXONOMY_RECORD", []

    barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
    if bool(barrier.get("fail_closed")):
        return True, "FORGET_BARRIER_FAIL_CLOSED", []

    raw_state = _read_json_readonly(state_path)
    if not isinstance(raw_state, Mapping):
        return True, "STATE_UNREADABLE", []

    objects_map = raw_state.get("repurposed_objects")
    if not isinstance(objects_map, Mapping):
        return True, "NO_TAXONOMY_RECORD", []

    obj = _find_object_in_map(objects_map, clean_id)
    if obj is None:
        return True, "UNTRACKED_VESSEL", []

    if _is_blocked_with_ids(obj, _object_extra_ids(obj), barrier):
        return True, "UNTRACKED_VESSEL", []

    stored_raw = obj.get("stored_items")
    stored_labels: list[str] = []
    if isinstance(stored_raw, list):
        for item in stored_raw:
            if not isinstance(item, Mapping):
                continue
            if _is_blocked_with_ids(item, (item.get("stored_item_id"), item.get("source_ref")), barrier):
                continue
            lbl = str(item.get("label_cn") or item.get("stored_item_id") or "").strip()
            if lbl:
                stored_labels.append(lbl)

    is_drinkware = bool(obj.get("drinkware"))
    caps = list(obj.get("supported_capabilities") or [])
    if is_drinkware and len(stored_labels) > 0:
        return False, "VESSEL_OCCUPIED_BY_STORED_ITEMS", stored_labels
    if not is_drinkware or "BREW_OR_DRINK" not in caps:
        return False, "CAPABILITY_NOT_SUPPORTED_BY_OBJECT", []
    return True, "READY", []


def is_vessel_occupied_non_drink_zero_write(day_root: Path | str, object_id: str) -> bool:
    """Zero-write boolean convenience wrapper around ``check_vessel_ready_for_drink_zero_write``."""
    ready, reason, _labels = check_vessel_ready_for_drink_zero_write(day_root, object_id)
    return (not ready) and reason == "VESSEL_OCCUPIED_BY_STORED_ITEMS"


def check_object_capability_zero_write(
    day_root: Path | str,
    object_id: str,
    capability: str,
) -> tuple[bool, str]:
    """100% zero-write check verifying whether ``object_id`` physically supports ``capability``."""
    clean_id = str(object_id or "").strip()
    clean_cap = str(capability or "").strip().upper()
    if not clean_id or not clean_cap:
        return False, "INVALID_FIELDS"

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    if _has_symlink_in_read_chain(day_root, resolved_root, state_path):
        return False, "UNSAFE_SYMLINK"

    barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
    if bool(barrier.get("fail_closed")):
        return False, "FORGET_BARRIER_FAIL_CLOSED"

    raw_state = _read_json_readonly(state_path)
    if not isinstance(raw_state, Mapping):
        return False, "OBJECT_NOT_FOUND"
    objects_map = raw_state.get("repurposed_objects")
    if not isinstance(objects_map, Mapping):
        return False, "OBJECT_NOT_FOUND"
    obj = _find_object_in_map(objects_map, clean_id)
    if obj is None or _is_blocked_with_ids(obj, _object_extra_ids(obj), barrier):
        return False, "OBJECT_NOT_FOUND"

    caps = [str(c).upper() for c in (obj.get("supported_capabilities") or [])]
    if clean_cap not in _REUSABLE_CAPS_SET or clean_cap not in caps:
        return False, "CAPABILITY_NOT_SUPPORTED_BY_OBJECT"

    if clean_cap == "BREW_OR_DRINK":
        ready, reason, _ = check_vessel_ready_for_drink_zero_write(resolved_root, clean_id)
        if not ready:
            return False, reason
    return True, "OK"


# ---------------------------------------------------------------------------
# Sub-Domain 2: Self-Invented Evolving Categories (Starts Strictly Empty {})
# ---------------------------------------------------------------------------


def _clean_scope_kinds(raw_scopes: Optional[Sequence[str]]) -> list[str]:
    if raw_scopes is None:
        return ["mixed"]
    if isinstance(raw_scopes, (str, bytes)) or not isinstance(raw_scopes, Sequence):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "scope_kinds must be a list of tokens")
    cleaned: list[str] = []
    for raw in raw_scopes:
        tok = _clean_id_token(raw, field_name="scope_kinds[]", required=True, max_len=48).lower()
        if tok not in cleaned:
            cleaned.append(tok)
        if len(cleaned) >= 8:
            break
    if not cleaned:
        return ["mixed"]
    return cleaned


def create_custom_category(
    day_root: Path | str,
    *,
    title_cn: str,
    scope_kinds: Optional[Sequence[str]] = None,
    description_cn: str = "",
    visibility: str = "shared",
    category_id: Optional[str] = None,
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Create a brand-new self-invented category (starts blank on every new instance)."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_title = _clean_text_field(title_cn, field_name="title_cn", min_len=1, max_len=64)
    clean_scopes = _clean_scope_kinds(scope_kinds)
    clean_desc = _clean_text_field(description_cn, field_name="description_cn", min_len=0, max_len=240)
    clean_vis = _normalize_visibility(visibility)
    clean_cat_id = _clean_id_token(category_id, field_name="category_id", required=False)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "create_custom_category",
        "category_id": clean_cat_id,
        "title_cn": clean_title,
        "scope_kinds": clean_scopes,
        "description_cn": clean_desc,
        "visibility": clean_vis,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        categories = state["custom_categories"]
        if len(categories) >= MAX_CUSTOM_CATEGORIES:
            raise PersonalObjectsTaxonomyError(
                409,
                "CUSTOM_CATEGORIES_LIMIT_REACHED",
                f"Maximum {MAX_CUSTOM_CATEGORIES} custom categories reached",
            )
        if clean_cat_id and clean_cat_id in categories:
            raise PersonalObjectsTaxonomyError(
                409,
                "CATEGORY_ALREADY_EXISTS",
                f"Category id already exists: {clean_cat_id}",
            )
        for existing in categories.values():
            if (
                isinstance(existing, Mapping)
                and not bool(existing.get("archived"))
                and str(existing.get("title_cn") or "") == clean_title
            ):
                raise PersonalObjectsTaxonomyError(
                    409,
                    "CATEGORY_TITLE_ALREADY_EXISTS",
                    f"Active category with title '{clean_title}' already exists",
                )

        if not clean_cat_id:
            seq = int(state.get("next_category_seq") or 0) + 1
            state["next_category_seq"] = seq
            clean_cat_id = f"cat_{seq:04d}_{secrets.token_hex(3)}"

        category_record: dict[str, Any] = {
            "category_id": clean_cat_id,
            "title_cn": clean_title,
            "scope_kinds": clean_scopes,
            "description_cn": clean_desc,
            "visibility": clean_vis,
            "archived": False,
            "title_history": [],
            "created_at": now_str,
            "updated_at": now_str,
        }
        categories[clean_cat_id] = category_record

        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "category_id": clean_cat_id,
            "category": {**copy.deepcopy(category_record), "item_count": 0, "item_ids": [], "shared_with_partner": clean_vis == "shared"},
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


def assign_or_move_category_item(
    day_root: Path | str,
    *,
    category_id: str,
    target_kind: str,
    target_ref: str,
    target_title_cn: str,
    annotation_cn: str = "",
    reason_for_move_cn: str = "",
    visibility: str = "shared",
    item_id: Optional[str] = None,
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Assign an item to a custom category or move it from a prior category with full ``revision_history``."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_cat_id = _clean_id_token(category_id, field_name="category_id", required=True)
    clean_target_kind = _clean_id_token(target_kind, field_name="target_kind", required=True, max_len=48).lower()
    clean_target_ref = _clean_text_field(target_ref, field_name="target_ref", min_len=1, max_len=120)
    clean_target_title = _clean_text_field(target_title_cn, field_name="target_title_cn", min_len=1, max_len=96)
    clean_annotation = _clean_text_field(annotation_cn, field_name="annotation_cn", min_len=0, max_len=240)
    clean_reason = _clean_text_field(reason_for_move_cn, field_name="reason_for_move_cn", min_len=0, max_len=240)
    clean_vis = _normalize_visibility(visibility)
    clean_item_id = _clean_id_token(item_id, field_name="item_id", required=False)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "assign_or_move_category_item",
        "item_id": clean_item_id,
        "category_id": clean_cat_id,
        "target_kind": clean_target_kind,
        "target_ref": clean_target_ref,
        "target_title_cn": clean_target_title,
        "annotation_cn": clean_annotation,
        "reason_for_move_cn": clean_reason,
        "visibility": clean_vis,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        categories = state["custom_categories"]
        target_cat = categories.get(clean_cat_id)
        if not isinstance(target_cat, dict):
            raise PersonalObjectsTaxonomyError(404, "CATEGORY_NOT_FOUND", f"Category not found: {clean_cat_id}")
        if bool(target_cat.get("archived")):
            raise PersonalObjectsTaxonomyError(
                409,
                "CATEGORY_ARCHIVED",
                f"Cannot assign items into archived category: {clean_cat_id}",
            )

        items = state["categorized_items"]
        existing_key: Optional[str] = None
        if clean_item_id and clean_item_id in items:
            existing_key = clean_item_id
        else:
            for k, row in items.items():
                if (
                    isinstance(row, Mapping)
                    and str(row.get("target_kind") or "") == clean_target_kind
                    and str(row.get("target_ref") or "") == clean_target_ref
                ):
                    existing_key = k
                    break

        target_cat_title = str(target_cat.get("title_cn") or "")
        moved = False
        revision_entry: Optional[dict[str, Any]] = None

        if existing_key is not None and isinstance(items.get(existing_key), dict):
            item_record = items[existing_key]
            old_cat_id = str(item_record.get("category_id") or "")
            old_cat = categories.get(old_cat_id) if isinstance(categories.get(old_cat_id), Mapping) else {}
            old_cat_title = str(old_cat.get("title_cn") or item_record.get("category_title_cn") or "")
            prior_annotation = str(item_record.get("annotation_cn") or "")

            if old_cat_id != clean_cat_id:
                moved = True
                revision_entry = {
                    "from_category_id": old_cat_id,
                    "from_category_title_cn": old_cat_title,
                    "to_category_id": clean_cat_id,
                    "to_category_title_cn": target_cat_title,
                    "prior_annotation_cn": prior_annotation,
                    "new_annotation_cn": clean_annotation,
                    "reason_for_move_cn": clean_reason,
                    "moved_at": now_str,
                }
                rev_hist = item_record.setdefault("revision_history", [])
                rev_hist.append(revision_entry)
                if len(rev_hist) > MAX_REVISION_HISTORY_PER_ITEM:
                    del rev_hist[: len(rev_hist) - MAX_REVISION_HISTORY_PER_ITEM]

            item_record["category_id"] = clean_cat_id
            item_record["category_title_cn"] = target_cat_title
            item_record["target_title_cn"] = clean_target_title
            item_record["annotation_cn"] = clean_annotation
            item_record["visibility"] = clean_vis
            item_record["updated_at"] = now_str
            resolved_item_id = existing_key
        else:
            if len(items) >= MAX_CATEGORIZED_ITEMS:
                raise PersonalObjectsTaxonomyError(
                    409,
                    "CATEGORIZED_ITEMS_LIMIT_REACHED",
                    f"Maximum {MAX_CATEGORIZED_ITEMS} categorized items reached",
                )
            if clean_item_id:
                resolved_item_id = clean_item_id
            else:
                seq = int(state.get("next_categorized_item_seq") or 0) + 1
                state["next_categorized_item_seq"] = seq
                resolved_item_id = f"citem_{seq:04d}_{secrets.token_hex(3)}"
            item_record = {
                "item_id": resolved_item_id,
                "category_id": clean_cat_id,
                "category_title_cn": target_cat_title,
                "target_kind": clean_target_kind,
                "target_ref": clean_target_ref,
                "target_title_cn": clean_target_title,
                "annotation_cn": clean_annotation,
                "visibility": clean_vis,
                "revision_history": [],
                "created_at": now_str,
                "updated_at": now_str,
            }
            items[resolved_item_id] = item_record

        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "moved": moved,
            "item_id": resolved_item_id,
            "category_id": clean_cat_id,
            "revision_entry": copy.deepcopy(revision_entry) if revision_entry is not None else None,
            "item": copy.deepcopy(item_record),
            "category": copy.deepcopy(target_cat),
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


def revise_custom_category(
    day_root: Path | str,
    *,
    category_id: str,
    title_cn: Optional[str] = None,
    description_cn: Optional[str] = None,
    scope_kinds: Optional[Sequence[str]] = None,
    visibility: Optional[str] = None,
    archived: Optional[bool] = None,
    reason_cn: str = "",
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Rename, update metadata, or archive a custom category while preserving item revision history."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_cat_id = _clean_id_token(category_id, field_name="category_id", required=True)
    clean_title = (
        _clean_text_field(title_cn, field_name="title_cn", min_len=1, max_len=64)
        if title_cn is not None
        else None
    )
    clean_desc = (
        _clean_text_field(description_cn, field_name="description_cn", min_len=0, max_len=240)
        if description_cn is not None
        else None
    )
    clean_scopes = _clean_scope_kinds(scope_kinds) if scope_kinds is not None else None
    clean_vis = _normalize_visibility(visibility) if visibility is not None else None
    if archived is not None and not isinstance(archived, bool):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "archived must be a boolean")
    clean_reason = _clean_text_field(reason_cn, field_name="reason_cn", min_len=0, max_len=200)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "revise_custom_category",
        "category_id": clean_cat_id,
        "title_cn": clean_title,
        "description_cn": clean_desc,
        "scope_kinds": clean_scopes,
        "visibility": clean_vis,
        "archived": archived,
        "reason_cn": clean_reason,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        categories = state["custom_categories"]
        cat = categories.get(clean_cat_id)
        if not isinstance(cat, dict):
            raise PersonalObjectsTaxonomyError(404, "CATEGORY_NOT_FOUND", f"Category not found: {clean_cat_id}")

        if clean_title is not None and clean_title != str(cat.get("title_cn") or ""):
            for other_id, other in categories.items():
                if (
                    other_id != clean_cat_id
                    and isinstance(other, Mapping)
                    and not bool(other.get("archived"))
                    and str(other.get("title_cn") or "") == clean_title
                ):
                    raise PersonalObjectsTaxonomyError(
                        409,
                        "CATEGORY_TITLE_ALREADY_EXISTS",
                        f"Active category with title '{clean_title}' already exists",
                    )
            prior_title = str(cat.get("title_cn") or "")
            title_hist = cat.setdefault("title_history", [])
            title_hist.append({
                "prior_title_cn": prior_title,
                "new_title_cn": clean_title,
                "reason_cn": clean_reason,
                "revised_at": now_str,
            })
            if len(title_hist) > MAX_TITLE_HISTORY_PER_CATEGORY:
                del title_hist[: len(title_hist) - MAX_TITLE_HISTORY_PER_CATEGORY]
            cat["title_cn"] = clean_title
            # Refresh live display title on items currently in this category without touching revision_history.
            for item in state["categorized_items"].values():
                if isinstance(item, dict) and str(item.get("category_id") or "") == clean_cat_id:
                    item["category_title_cn"] = clean_title

        if clean_desc is not None:
            cat["description_cn"] = clean_desc
        if clean_scopes is not None:
            cat["scope_kinds"] = clean_scopes
        if clean_vis is not None:
            cat["visibility"] = clean_vis
        if archived is not None:
            cat["archived"] = bool(archived)
        cat["updated_at"] = now_str

        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "category_id": clean_cat_id,
            "category": copy.deepcopy(cat),
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


# ---------------------------------------------------------------------------
# Sub-Domain 3: Blocked-Attempt Capability Collector
# ---------------------------------------------------------------------------


def _normalize_blocker_code(raw: Any) -> str:
    cleaned = _clean_text_field(raw, field_name="blocker_code", min_len=1, max_len=96)
    return cleaned.upper().replace(" ", "_")


def _resolve_capability_cluster(
    *,
    blocker_code: str,
    goal_cn: str,
    attempted_action_cn: str,
    blocker_detail_cn: str,
) -> tuple[str, str]:
    upper_code = blocker_code.upper()
    if upper_code in _CLUSTER_TITLES_CN:
        return upper_code, _CLUSTER_TITLES_CN[upper_code]
    if upper_code.startswith("MISSING_PRIMITIVE:"):
        suffix = upper_code.split(":", 1)[1].strip()
        if suffix in _CLUSTER_TITLES_CN:
            return suffix, _CLUSTER_TITLES_CN[suffix]

    haystack = f"{upper_code} {goal_cn} {attempted_action_cn} {blocker_detail_cn}".lower()
    for cluster_id, cues in _CLUSTER_CUES:
        if any(cue in haystack for cue in cues):
            return cluster_id, _CLUSTER_TITLES_CN[cluster_id]

    if upper_code.startswith("CUSTOM:"):
        raw_slug = upper_code.split(":", 1)[1].lower()
    elif upper_code.startswith("MISSING_PRIMITIVE:"):
        raw_slug = upper_code.split(":", 1)[1].lower()
    elif upper_code not in _GENERIC_MISSING_BLOCKERS:
        raw_slug = upper_code.lower()
    else:
        ascii_words = _SLUG_STRIP_RE.sub("_", attempted_action_cn.lower()).strip("_")
        if len(ascii_words) >= 3:
            raw_slug = ascii_words
        else:
            digest = hashlib.sha256(f"{upper_code}|{attempted_action_cn}".encode("utf-8")).hexdigest()[:8]
            raw_slug = f"cap_{digest}"

    slug = _SLUG_STRIP_RE.sub("_", raw_slug).strip("_")[:48]
    if not slug:
        slug = "cap_" + hashlib.sha256(f"{upper_code}|{goal_cn}|{attempted_action_cn}".encode("utf-8")).hexdigest()[:8]
    return f"CUSTOM:{slug}", f"待补足自定义能力（{slug}）"


def _build_ranked_capability_gaps(clusters: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        clusters,
        key=lambda row: (-int(row.get("occurrence_count") or 0), str(row.get("cluster_id") or "")),
    )
    ranked: list[dict[str, Any]] = []
    for row in ordered:
        ranked.append({
            "cluster_id": str(row.get("cluster_id") or ""),
            "cluster_title_cn": str(row.get("cluster_title_cn") or ""),
            "occurrence_count": int(row.get("occurrence_count") or 0),
            "blocker_codes": list(row.get("blocker_codes") or []),
            "sample_count": len(row.get("sample_attempts") or []),
            "last_seen_at": row.get("last_seen_at"),
        })
    return ranked


def record_blocked_attempt(
    day_root: Path | str,
    *,
    goal_cn: str,
    attempted_action_cn: str,
    blocker_code: str,
    blocker_detail_cn: str = "",
    target_ref: str = "",
    visibility: str = "shared",
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Classify a blocked attempt: filter temporary world conditions or cluster genuine missing capabilities."""
    _normalize_actor_side(actor_side, require_subject=True)
    clean_goal = _clean_text_field(goal_cn, field_name="goal_cn", min_len=1, max_len=200)
    clean_action = _clean_text_field(attempted_action_cn, field_name="attempted_action_cn", min_len=1, max_len=200)
    norm_code = _normalize_blocker_code(blocker_code)
    clean_detail = _clean_text_field(blocker_detail_cn, field_name="blocker_detail_cn", min_len=0, max_len=240)
    clean_target_ref = _clean_text_field(target_ref, field_name="target_ref", min_len=0, max_len=120)
    clean_vis = _normalize_visibility(visibility)
    eff_op_key = _validate_operation_key(operation_key)
    _, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    # Temporary world friction NEVER pollutes blocked_capability_log (and performs 0 state writes).
    if norm_code in TEMPORARY_WORLD_BLOCKER_CODES:
        return {
            "ok": True,
            "status": "ignored_temporary_world_condition",
            "command_status": "IGNORED",
            "already_applied": False,
            "replayed": False,
            "classification": "WORLD_CONDITION_TEMPORARY",
            "logged_as_capability_gap": False,
            "blocker_code": norm_code,
            "cluster_id": None,
            "occurrence_count": 0,
            "zero_write": True,
            "llm_calls": 0,
        }

    cluster_id, cluster_title_cn = _resolve_capability_cluster(
        blocker_code=norm_code,
        goal_cn=clean_goal,
        attempted_action_cn=clean_action,
        blocker_detail_cn=clean_detail,
    )

    payload_hash = _tx.payload_fingerprint({
        "op": "record_blocked_attempt",
        "goal_cn": clean_goal,
        "attempted_action_cn": clean_action,
        "blocker_code": norm_code,
        "blocker_detail_cn": clean_detail,
        "target_ref": clean_target_ref,
        "visibility": clean_vis,
        "cluster_id": cluster_id,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _ensure_state_hydrated(_read_json_readonly(state_path), now_str)
        replay = _check_op_replay(resolved_root, state, eff_op_key, payload_hash)
        if replay is not None:
            return replay

        log_map = state["blocked_capability_log"]
        sample_sig = hashlib.sha256(
            f"{clean_goal}|{clean_action}|{norm_code}|{clean_target_ref}".encode("utf-8")
        ).hexdigest()[:16]
        sample_entry: dict[str, Any] = {
            "sample_sig": sample_sig,
            "goal_cn": clean_goal,
            "attempted_action_cn": clean_action,
            "blocker_code": norm_code,
            "blocker_detail_cn": clean_detail,
            "target_ref": clean_target_ref,
            "visibility": clean_vis,
            "seen_count": 1,
            "first_seen_at": now_str,
            "last_seen_at": now_str,
        }

        cluster = log_map.get(cluster_id)
        if isinstance(cluster, dict):
            cluster["occurrence_count"] = int(cluster.get("occurrence_count") or 0) + 1
            cluster["last_seen_at"] = now_str
            codes = list(cluster.get("blocker_codes") or [])
            if norm_code not in codes:
                codes.append(norm_code)
                codes.sort()
            cluster["blocker_codes"] = codes

            samples = cluster.setdefault("sample_attempts", [])
            matched_sample = next(
                (s for s in samples if isinstance(s, dict) and s.get("sample_sig") == sample_sig),
                None,
            )
            if matched_sample is not None:
                matched_sample["seen_count"] = int(matched_sample.get("seen_count") or 1) + 1
                matched_sample["last_seen_at"] = now_str
                if clean_vis == "shared":
                    matched_sample["visibility"] = "shared"
            else:
                samples.append(sample_entry)
                if len(samples) > MAX_SAMPLE_ATTEMPTS_PER_CLUSTER:
                    del samples[: len(samples) - MAX_SAMPLE_ATTEMPTS_PER_CLUSTER]
            if clean_vis == "shared":
                cluster["visibility"] = "shared"
        else:
            if len(log_map) >= MAX_BLOCKED_CAPABILITY_CLUSTERS:
                evict_id = min(
                    log_map.keys(),
                    key=lambda cid: (
                        int(log_map[cid].get("occurrence_count") or 0),
                        str(log_map[cid].get("last_seen_at") or ""),
                        str(cid),
                    ),
                )
                log_map.pop(evict_id, None)
            cluster = {
                "cluster_id": cluster_id,
                "cluster_title_cn": cluster_title_cn,
                "classification": "MISSING_CAPABILITY",
                "occurrence_count": 1,
                "blocker_codes": [norm_code],
                "sample_attempts": [sample_entry],
                "visibility": clean_vis,
                "first_seen_at": now_str,
                "last_seen_at": now_str,
            }
            log_map[cluster_id] = cluster

        ranked = _build_ranked_capability_gaps(list(log_map.values()))
        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "replayed": False,
            "classification": "MISSING_CAPABILITY",
            "logged_as_capability_gap": True,
            "blocker_code": norm_code,
            "cluster_id": cluster_id,
            "cluster_title_cn": cluster_title_cn,
            "occurrence_count": int(cluster["occurrence_count"]),
            "cluster": copy.deepcopy(cluster),
            "ranked_capability_gaps": ranked,
        }
        return _finalize_and_persist(
            day_root,
            resolved_root,
            state_path,
            state,
            result,
            operation_key=eff_op_key,
            payload_hash=payload_hash,
            now_str=now_str,
        )


# ---------------------------------------------------------------------------
# Unified Command Dispatcher (Subject-Only)
# ---------------------------------------------------------------------------


def _parse_bool_field(raw: Any, *, default: bool, field_name: str) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise PersonalObjectsTaxonomyError(
        400,
        "INVALID_FIELDS",
        f"Field '{field_name}' must be a boolean",
    )


def apply_personal_objects_taxonomy_command(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Validate and dispatch a Pillar D command payload (strictly subject-only)."""
    if not isinstance(payload, Mapping):
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "Payload must be a JSON object")
    for raw_key in payload.keys():
        if str(raw_key).strip().lower() in FORBIDDEN_COMMAND_KEYS:
            raise PersonalObjectsTaxonomyError(
                400,
                "FORBIDDEN_PAYLOAD_FIELD",
                f"Forbidden smuggled payload field: {raw_key}",
            )
    _normalize_actor_side(actor_side, require_subject=True)

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        raise PersonalObjectsTaxonomyError(400, "INVALID_FIELDS", "Field 'action' is required")
    action = raw_action.strip().lower()
    if action not in SUPPORTED_COMMAND_ACTIONS:
        raise PersonalObjectsTaxonomyError(
            400,
            "UNSUPPORTED_ACTION",
            f"Unsupported action: {action!r}",
        )

    op_key = payload.get("operation_key")
    eff_now = payload.get("now_iso") if now_iso is None else now_iso

    if action in ("register_or_repurpose_object", "repurpose_object"):
        return register_or_repurpose_object(
            day_root,
            object_id=payload.get("object_id"),
            object_kind=payload.get("object_kind"),
            base_name_cn=str(payload.get("base_name_cn") or ""),
            custom_label_cn=str(payload.get("custom_label_cn") or ""),
            supported_capabilities=payload.get("supported_capabilities"),
            capacity_slots=payload.get("capacity_slots"),
            drinkware=payload.get("drinkware"),
            emptied_from_consumable=_parse_bool_field(
                payload.get("emptied_from_consumable"),
                default=False,
                field_name="emptied_from_consumable",
            ),
            consumable_portions_remaining=payload.get("consumable_portions_remaining"),
            origin_provenance=payload.get("origin_provenance"),
            economy_item_instance_id=payload.get("economy_item_instance_id"),
            catalog_item_id=payload.get("catalog_item_id"),
            reusable_container_spec=payload.get("reusable_container_spec"),
            location_hint_cn=str(payload.get("location_hint_cn") or "书桌一角"),
            visibility=payload.get("visibility"),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action == "store_item_in_object":
        return store_item_in_object(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            item_label_cn=str(payload.get("item_label_cn") or ""),
            item_kind=str(payload.get("item_kind") or "keepsake"),
            stored_item_id=payload.get("stored_item_id"),
            source_ref=str(payload.get("source_ref") or ""),
            note_cn=str(payload.get("note_cn") or ""),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action == "attach_label_to_object":
        return attach_label_to_object(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            custom_label_cn=str(payload.get("custom_label_cn") or ""),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action == "add_note_to_object":
        return add_note_to_object(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            note_cn=str(payload.get("note_cn") or ""),
            visibility=str(payload.get("visibility") or "shared"),
            source_ref=str(payload.get("source_ref") or ""),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action in ("remove_stored_item_from_object", "empty_stored_items"):
        return remove_stored_item_from_object(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            stored_item_id=payload.get("stored_item_id"),
            item_label_cn=payload.get("item_label_cn"),
            remove_all=_parse_bool_field(
                payload.get("remove_all"),
                default=(action == "empty_stored_items"),
                field_name="remove_all",
            ),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action == "create_custom_category":
        return create_custom_category(
            day_root,
            title_cn=str(payload.get("title_cn") or ""),
            scope_kinds=payload.get("scope_kinds"),
            description_cn=str(payload.get("description_cn") or ""),
            visibility=str(payload.get("visibility") or "shared"),
            category_id=payload.get("category_id"),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action == "assign_or_move_category_item":
        return assign_or_move_category_item(
            day_root,
            category_id=str(payload.get("category_id") or ""),
            target_kind=str(payload.get("target_kind") or ""),
            target_ref=str(payload.get("target_ref") or ""),
            target_title_cn=str(payload.get("target_title_cn") or ""),
            annotation_cn=str(payload.get("annotation_cn") or ""),
            reason_for_move_cn=str(payload.get("reason_for_move_cn") or ""),
            visibility=str(payload.get("visibility") or "shared"),
            item_id=payload.get("item_id"),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action == "revise_custom_category":
        return revise_custom_category(
            day_root,
            category_id=str(payload.get("category_id") or ""),
            title_cn=payload.get("title_cn"),
            description_cn=payload.get("description_cn"),
            scope_kinds=payload.get("scope_kinds"),
            visibility=payload.get("visibility"),
            archived=payload.get("archived"),
            reason_cn=str(payload.get("reason_cn") or ""),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    if action == "record_blocked_attempt":
        return record_blocked_attempt(
            day_root,
            goal_cn=str(payload.get("goal_cn") or ""),
            attempted_action_cn=str(payload.get("attempted_action_cn") or ""),
            blocker_code=str(payload.get("blocker_code") or ""),
            blocker_detail_cn=str(payload.get("blocker_detail_cn") or ""),
            target_ref=str(payload.get("target_ref") or ""),
            visibility=str(payload.get("visibility") or "shared"),
            actor_side=actor_side,
            operation_key=op_key,
            now_iso=eff_now,
        )

    # action == "use_object_capability"
    obj_id = _clean_id_token(payload.get("object_id"), field_name="object_id", required=True)
    req_cap = _clean_text_field(payload.get("requested_capability"), field_name="requested_capability", min_len=1, max_len=64).upper()
    ok_cap, reason = check_object_capability_zero_write(day_root, obj_id, req_cap)
    if not ok_cap:
        status_code = 404 if reason == "OBJECT_NOT_FOUND" else (400 if reason in ("INVALID_FIELDS", "UNSAFE_SYMLINK") else 409)
        raise PersonalObjectsTaxonomyError(
            status_code,
            reason,
            f"Object '{obj_id}' cannot perform capability '{req_cap}': {reason}",
        )
    return {
        "ok": True,
        "status": "verified",
        "command_status": "VERIFIED",
        "object_id": obj_id,
        "requested_capability": req_cap,
        "zero_write": True,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Zero-Write Read View with Unified Forget Barrier & Visibility Filtering
# ---------------------------------------------------------------------------


def read_personal_objects_taxonomy_zero_write(
    day_root: Path | str,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write read projection of repurposed objects, custom categories & capability gaps.

    Enforces:
    1. Unified Forget Barrier via ``load_unified_forget_barrier_readonly(day_root)``
       (fail-closed when control/barrier integrity fails, stripping blocked objects,
       stored items, notes, categories, categorized items, and capability samples).
    2. Partner visibility boundary: when ``actor_side == "partner"``, only
       ``visibility == "shared"`` objects, notes, categories, items, and capability
       attempts are exposed (and private historical category titles inside shared
       items' ``revision_history`` are redacted).
    3. Strictly 0 disk writes, 0 ``.lock`` creation, and 0 LLM calls.
    """
    side = _normalize_actor_side(actor_side)
    _, now_str = _resolve_now(now_iso)
    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)

    symlink_unsafe = _has_symlink_in_read_chain(day_root, resolved_root, state_path)
    state_exists = (not symlink_unsafe) and _is_regular_file(state_path)

    barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
    if bool(barrier.get("fail_closed")) or symlink_unsafe:
        return {
            "ok": True,
            "schema": SCHEMA,
            "state_exists": state_exists,
            "revision": 0,
            "updated_at": None,
            "observed_at": now_str,
            "actor_side": side,
            "repurposed_objects": [],
            "custom_categories": [],
            "categorized_items": [],
            "blocked_capability_log": [],
            "ranked_capability_gaps": [],
            "capability_gaps_summary": {
                "total_clusters": 0,
                "total_occurrences": 0,
                "top_gaps": [],
            },
            "counts": {
                "repurposed_objects": 0,
                "custom_categories": 0,
                "categorized_items": 0,
                "occupied_drinkware": 0,
                "capability_gap_clusters": 0,
            },
            "forget_barrier_active": bool(barrier.get("barrier_active")) or bool(barrier.get("fail_closed")),
            "forget_barrier_fail_closed": bool(barrier.get("fail_closed")),
            "symlink_rejected": symlink_unsafe,
            "zero_write": True,
            "llm_calls": 0,
        }

    raw_state = _read_json_readonly(state_path)
    state: Mapping[str, Any] = raw_state if isinstance(raw_state, Mapping) else {}

    # 1. Repurposed objects
    projected_objects: list[dict[str, Any]] = []
    raw_objects = state.get("repurposed_objects")
    if isinstance(raw_objects, Mapping):
        for oid in sorted(raw_objects.keys()):
            row = raw_objects[oid]
            if not isinstance(row, Mapping):
                continue
            if _is_blocked_with_ids(row, (oid, *_object_extra_ids(row)), barrier):
                continue
            if side == "partner" and str(row.get("visibility") or "shared") != "shared":
                continue
            projected_objects.append(_enrich_object_view(row, actor_side=side, barrier=barrier))

    # 2. Custom categories (blank-start!) & categorized items
    raw_categories = state.get("custom_categories")
    all_categories_map: dict[str, Mapping[str, Any]] = (
        {str(k): v for k, v in raw_categories.items() if isinstance(v, Mapping)}
        if isinstance(raw_categories, Mapping)
        else {}
    )
    visible_cat_ids: set[str] = set()
    blocked_cat_ids: set[str] = set()
    private_cat_ids: set[str] = set()
    projected_categories_by_id: dict[str, dict[str, Any]] = {}

    for cid in sorted(all_categories_map.keys()):
        cat = all_categories_map[cid]
        if _is_blocked_with_ids(cat, (cid, cat.get("category_id")), barrier):
            blocked_cat_ids.add(cid)
            continue
        is_shared = str(cat.get("visibility") or "shared") == "shared"
        if not is_shared:
            private_cat_ids.add(cid)
            if side == "partner":
                continue
        visible_cat_ids.add(cid)
        cat_copy = copy.deepcopy(dict(cat))
        cat_copy["shared_with_partner"] = is_shared
        cat_copy["item_count"] = 0
        cat_copy["item_ids"] = []
        projected_categories_by_id[cid] = cat_copy

    projected_items: list[dict[str, Any]] = []
    raw_items = state.get("categorized_items")
    if isinstance(raw_items, Mapping):
        for iid in sorted(raw_items.keys()):
            item = raw_items[iid]
            if not isinstance(item, Mapping):
                continue
            cur_cid = str(item.get("category_id") or "")
            if cur_cid in blocked_cat_ids:
                continue
            if _is_blocked_with_ids(
                item,
                (iid, item.get("item_id"), cur_cid, item.get("target_ref")),
                barrier,
            ):
                continue
            if cur_cid not in visible_cat_ids:
                continue
            item_shared = str(item.get("visibility") or "shared") == "shared"
            if side == "partner" and not item_shared:
                continue

            item_copy = copy.deepcopy(dict(item))
            item_copy["shared_with_partner"] = item_shared
            raw_hist = item_copy.get("revision_history")
            clean_hist: list[dict[str, Any]] = []
            if isinstance(raw_hist, list):
                for h in raw_hist:
                    if not isinstance(h, Mapping):
                        continue
                    from_cid = str(h.get("from_category_id") or "")
                    to_cid = str(h.get("to_category_id") or "")
                    if from_cid in blocked_cat_ids or to_cid in blocked_cat_ids:
                        continue
                    if _is_blocked_with_ids(h, (from_cid, to_cid), barrier):
                        continue
                    h_copy = copy.deepcopy(dict(h))
                    if side == "partner":
                        if from_cid in private_cat_ids:
                            h_copy["from_category_title_cn"] = _PRIVATE_CATEGORY_REDACTED_CN
                        if to_cid in private_cat_ids:
                            h_copy["to_category_title_cn"] = _PRIVATE_CATEGORY_REDACTED_CN
                    clean_hist.append(h_copy)
            item_copy["revision_history"] = clean_hist
            projected_items.append(item_copy)

            parent_cat = projected_categories_by_id.get(cur_cid)
            if parent_cat is not None:
                parent_cat["item_count"] = int(parent_cat["item_count"]) + 1
                parent_cat["item_ids"].append(str(item_copy.get("item_id") or iid))

    projected_categories = [projected_categories_by_id[cid] for cid in sorted(projected_categories_by_id.keys())]

    # 3. Blocked capability log
    projected_clusters: list[dict[str, Any]] = []
    raw_log = state.get("blocked_capability_log")
    if isinstance(raw_log, Mapping):
        for cl_id in sorted(raw_log.keys()):
            cl = raw_log[cl_id]
            if not isinstance(cl, Mapping):
                continue
            if _is_blocked_with_ids(cl, (cl_id, cl.get("cluster_id")), barrier):
                continue
            raw_samples = cl.get("sample_attempts")
            kept_samples: list[dict[str, Any]] = []
            if isinstance(raw_samples, list):
                for sample in raw_samples:
                    if not isinstance(sample, Mapping):
                        continue
                    if _is_blocked_with_ids(sample, (sample.get("target_ref"), sample.get("sample_sig")), barrier):
                        continue
                    if side == "partner" and str(sample.get("visibility") or "shared") != "shared":
                        continue
                    kept_samples.append(copy.deepcopy(dict(sample)))
            if side == "partner" and str(cl.get("visibility") or "shared") != "shared" and not kept_samples:
                continue
            if side == "partner" and not kept_samples and isinstance(raw_samples, list) and len(raw_samples) > 0:
                continue
            # If all samples were stripped by forget barrier, drop cluster so forgotten targets don't leak.
            if isinstance(raw_samples, list) and len(raw_samples) > 0 and not kept_samples:
                continue
            cl_copy = copy.deepcopy(dict(cl))
            cl_copy["sample_attempts"] = kept_samples
            projected_clusters.append(cl_copy)

    projected_clusters.sort(
        key=lambda row: (-int(row.get("occurrence_count") or 0), str(row.get("cluster_id") or ""))
    )
    ranked_gaps = _build_ranked_capability_gaps(projected_clusters)
    total_occurrences = sum(int(row.get("occurrence_count") or 0) for row in projected_clusters)
    occupied_drinkware_count = sum(1 for obj in projected_objects if bool(obj.get("occupied_for_drink")))

    return {
        "ok": True,
        "schema": SCHEMA,
        "state_exists": state_exists,
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "observed_at": now_str,
        "actor_side": side,
        "repurposed_objects": projected_objects,
        "custom_categories": projected_categories,
        "categorized_items": projected_items,
        "blocked_capability_log": projected_clusters,
        "ranked_capability_gaps": ranked_gaps,
        "capability_gaps_summary": {
            "total_clusters": len(projected_clusters),
            "total_occurrences": total_occurrences,
            "top_gaps": ranked_gaps[:10],
        },
        "counts": {
            "repurposed_objects": len(projected_objects),
            "custom_categories": len(projected_categories),
            "categorized_items": len(projected_items),
            "occupied_drinkware": occupied_drinkware_count,
            "capability_gap_clusters": len(projected_clusters),
        },
        "forget_barrier_active": bool(barrier.get("barrier_active")),
        "forget_barrier_fail_closed": False,
        "symlink_rejected": False,
        "zero_write": True,
        "llm_calls": 0,
    }


__all__ = [
    "ALLOWED_ACTOR_SIDES",
    "ALLOWED_VISIBILITIES",
    "CANONICAL_CAPABILITY_CLUSTERS",
    "EMPTIED_CONSUMABLE_KINDS",
    "FORBIDDEN_COMMAND_KEYS",
    "MAX_BLOCKED_CAPABILITY_CLUSTERS",
    "MAX_CATEGORIZED_ITEMS",
    "MAX_CUSTOM_CATEGORIES",
    "MAX_NOTES_PER_OBJECT",
    "MAX_OPERATION_RECEIPTS",
    "MAX_REPURPOSED_OBJECTS",
    "MAX_REVISION_HISTORY_PER_ITEM",
    "MAX_SAMPLE_ATTEMPTS_PER_CLUSTER",
    "MAX_STORED_ITEMS_PER_OBJECT",
    "MAX_USE_HISTORY_PER_OBJECT",
    "PersonalObjectsTaxonomyError",
    "REUSABLE_OBJECT_CAPABILITIES",
    "SCHEMA",
    "STARTER_VESSEL_DEFAULTS",
    "STATE_REL_PATH",
    "STATE_SCHEMA",
    "SUPPORTED_COMMAND_ACTIONS",
    "TEMPORARY_WORLD_BLOCKER_CODES",
    "add_note_to_object",
    "apply_personal_objects_taxonomy_command",
    "assign_or_move_category_item",
    "attach_label_to_object",
    "check_object_capability_zero_write",
    "check_vessel_ready_for_drink_zero_write",
    "create_custom_category",
    "is_vessel_occupied_non_drink_zero_write",
    "personal_objects_taxonomy_state_path",
    "read_personal_objects_taxonomy_zero_write",
    "record_blocked_attempt",
    "register_or_repurpose_object",
    "remove_stored_item_from_object",
    "revise_custom_category",
    "store_item_in_object",
    "taxonomy_state_path",
]
