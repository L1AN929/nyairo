#!/usr/bin/env python3
"""局部看法与观点修订链、物件容纳与位置印象链、双人书桌附着共在与餐桌份数守恒包。

它是什么
--------
本模块在 ``nyairo-rc1-body-backend`` 落地 ``phone.stance.containment.v1``
（``<root>/phone/stance_containment.json``），把第 30、31 及第 12/13/14 篇与总体审阅
（P0 #1、P1 #6、P1 #7）落成三块纯确定性、零模型调用、零随机数的后端逻辑与只读投影：

1. ``stance_revisions``（第 30 篇 + 审阅 P1 #6）：
   * 闭合 ``facet`` 枚举 ``("appearance", "content", "method_fit")``；
   * 每条看法绑定精确比较锚点 ``(scope_ref, source_revision, page_or_anchor, facet)``；
   * 严格区分新建 ``propose_stance``（默认私密 ``shared_with_partner=False``）、错别字修订
     ``fix_stance_typo``（CAS ``expected_version``，不改谱系）、观点真正改变 ``revise_stance``
     （生成新条目并双向写入 ``replaces_stance_id`` 与旧项 ``superseded_by``，旧项转为
     ``SUPERSEDED`` 绝不原地抹除）、来源换版 ``mark_source_updated``（标 ``SOURCE_REVISION_STALE``）
     与来源撤权 ``revoke_stance_source``（转为 ``SOURCE_WITHDRAWN``，清退缓存正文，重建列表绝不复活）。
2. ``object_containment``（第 31 篇 + 审阅 P1 #7）：
   * 支持房间桌面/书架容器（``pen_cup``、``ceramic_mug``、``desk_tray``、``tote_bag`` 等，带
     ``capacity`` 与 ``place_id``）、可放入小物件及双手槽 ``left_hand`` / ``right_hand``；
   * 复合动作 ``pick_and_put_in_container`` 执行真实部分完成语义：拿起成功但容器满额
     （``CONTAINER_FULL``）、不可达（``CONTAINER_NOT_REACHABLE``）或类别不符（``CONTAINER_CLASS_REJECTED``）
     时，物件真实留在手槽并持久化返回手槽现状，绝不假装回滚回原位；
   * 容器移动时内部子物件物理位置随之移动；伴侣在主体不在场/未目击时挪动容器（``partner_move_container``），
     主体位置印象 ``subject_last_seen_place`` 保持旧值（``impression_stale=True``），直到主体调用
     ``observe_current_room`` 到场观察才刷新印象，杜绝后台全知作弊。
3. ``shared_desk_table``（第 12/13/14 篇 + 审阅 P0 #1）：
   * 单前台附着式共在括号：支持 ``off`` / ``quiet_parallel`` / ``focused_do_not_disturb`` /
     ``break_chat`` 四态，双方均可提议、应答、切换或随时无债退出，退出绝不终止主体原本的手头作品活动
     （``hijacks_primary_activity=False``）；
   * 厨房餐桌份数守恒与共食：支持 ``prepare_table_dish`` / ``partner_bring_snack``（整数份数
     ``portions_remaining``，温热档位 ``hot -> warm -> cooled`` 随时间确定性变凉，``take_portion``
     扣减至 ``0`` 为空盘 ``EMPTY``，绝不无限刷吃，``no_hidden_affection_score=True``）。

它不是什么
----------
* 零大模型调用、零 ``random`` 随机数、不起后台线程或定时器；
* ``read_stance_containment`` 在文件不存在时 100% 零写入（``revision=0``）；
* 开源角色中性，动态读取角色包个体名称与人称代词（默认 ``"TA"``），绝不写死「千代」或「她」。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from . import sensory_character_view as char_view
from components.life.scripts import attention_view_projector as attn_proj
from components.life.scripts import life_attention_stance_containment as owner_substrate

try:  # package import (tests / phone service import ``components.phone``)
    from ..life.scripts import state_tx_guard as _tx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat import fallback (components/phone on sys.path)
    import sys as _sys

    _LIFE_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "life" / "scripts"
    if str(_LIFE_SCRIPTS_DIR) not in _sys.path:
        _sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
    import state_tx_guard as _tx  # type: ignore[no-redef]

STANCE_CONTAINMENT_SCHEMA = "phone.stance.containment.v1"
STATE_DIRNAME = "phone"
STANCE_CONTAINMENT_FILENAME = "stance_containment.json"
STATE_SCHEMA_VERSION = 1

STANCE_FACETS: tuple[str, ...] = ("appearance", "content", "method_fit")
STANCE_STATUSES: tuple[str, ...] = (
    "ACTIVE",
    "SUPERSEDED",
    "SOURCE_REVISION_STALE",
    "SOURCE_WITHDRAWN",
)

CONTAINER_OPEN_STATES: tuple[str, ...] = ("open_top", "open", "closed", "locked")
HAND_SLOTS: tuple[str, ...] = ("left_hand", "right_hand")
ITEM_CLASSES: tuple[str, ...] = (
    "writing_tool",
    "stationery",
    "eyewear",
    "keys",
    "small_item",
)

COPRESENCE_MODES: tuple[str, ...] = (
    "off",
    "quiet_parallel",
    "focused_do_not_disturb",
    "break_chat",
)
ACTIVE_COPRESENCE_MODES: tuple[str, ...] = (
    "quiet_parallel",
    "focused_do_not_disturb",
    "break_chat",
)
PARTICIPANT_ACTORS: tuple[str, ...] = ("subject", "partner")
INTERRUPTIBILITY_MODES: tuple[str, ...] = ("IMMEDIATE", "SAFE_BOUNDARY", "ATOMIC")
PRIMARY_ACTIVITY_STATUSES: tuple[str, ...] = ("ACTIVE", "PAUSED", "IDLE")

DISH_KINDS: tuple[str, ...] = ("tea_pot", "warm_drink", "meal_dish", "snack_plate")
THERMAL_BANDS: tuple[str, ...] = ("hot", "warm", "cooled")
DISH_STATUSES: tuple[str, ...] = ("AVAILABLE", "EMPTY")

HOT_TO_WARM_MINUTES = 20
HOT_TO_COOLED_MINUTES = 60
WARM_TO_COOLED_MINUTES = 40

MAX_STANCES = 64
MAX_STANCE_TEXT = 400
MAX_PRIVATE_NOTE = 200
MAX_PUBLIC_NOTE = 160
MAX_TITLE = 60
MAX_OPERATION_KEY = 120
MAX_OPERATION_RECEIPTS = 64
MAX_COMMITTED_OP_RING = 256
MAX_CONTAINERS = 24
MAX_ITEMS = 48
MAX_CONTAINER_CAPACITY = 12
MAX_CONTAINER_NESTING_DEPTH = 3
MAX_TABLE_DISHES = 6
MAX_PORTIONS = 12
MAX_COPRESENCE_MINUTES = 480
DEFAULT_COPRESENCE_MINUTES = 90

LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,120}$")

PLACE_ROOM_MAP: dict[str, str] = {
    "study_desk": "study_room",
    "desk_left": "study_room",
    "desk_right": "study_room",
    "bookshelf": "study_room",
    "window_sill": "study_room",
    "sofa_side": "living_room",
    "bed_side": "bedroom",
    "dining_table": "kitchen_dining",
    "kitchen_counter": "kitchen_dining",
    "entry_stand": "entryway",
    "entryway_tray": "entryway",
    "entryway": "entryway",
    "doorway_shoe_zone": "entryway",
    "outside": "outside",
}

FORBIDDEN_FOREIGN_KEYS: frozenset[str] = frozenset({
    "subject_id",
    "subject_ref",
    "actor_id",
    "actor_ref",
    "after_state",
    "superseded_by",
    "impression_stale",
    "subject_last_seen_place",
    "affection_score",
    "mood_delta",
    "relationship_score",
    "deploy_enabled",
    "adult_confirmed",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    # 1. Stance & revision lineage
    "propose_stance": frozenset({
        "action",
        "scope_ref",
        "source_revision",
        "page_or_anchor",
        "facet",
        "text",
        "cached_excerpt",
        "shared_with_partner",
        "private_note",
        "expected_revision",
        "operation_key",
    }),
    "fix_stance_typo": frozenset({
        "action",
        "stance_id",
        "expected_version",
        "text",
        "private_note",
        "expected_revision",
        "operation_key",
    }),
    "revise_stance": frozenset({
        "action",
        "replaces_stance_id",
        "text",
        "cached_excerpt",
        "source_revision",
        "page_or_anchor",
        "facet",
        "scope_ref",
        "shared_with_partner",
        "private_note",
        "expected_version",
        "expected_revision",
        "operation_key",
    }),
    "set_stance_sharing": frozenset({
        "action",
        "stance_id",
        "shared_with_partner",
        "expected_revision",
    }),
    "mark_source_updated": frozenset({
        "action",
        "scope_ref",
        "new_source_revision",
        "note",
        "expected_revision",
        "operation_key",
    }),
    "revoke_stance_source": frozenset({
        "action",
        "scope_ref",
        "reason",
        "expected_revision",
        "operation_key",
    }),
    "rebuild_stance_index": frozenset({
        "action",
        "expected_revision",
    }),
    "record_obtained_pages": frozenset({
        "action",
        "scope_ref",
        "obtained_pages",
        "max_obtained_page",
        "total_pages",
        "expected_revision",
        "operation_key",
    }),
    # 2. Object containment & spatial impression separation
    "register_container": frozenset({
        "action",
        "container_id",
        "label",
        "place_id",
        "parent_container_id",
        "capacity",
        "accepted_classes",
        "open_state",
        "portable",
        "drinkware",
        "actor",
        "expected_revision",
        "operation_key",
    }),
    "nest_container": frozenset({
        "action",
        "container_id",
        "parent_container_id",
        "to_place_id",
        "actor",
        "expected_revision",
        "operation_key",
    }),
    "set_container_open_state": frozenset({
        "action",
        "container_id",
        "open_state",
        "actor",
        "subject_witnessed",
        "expected_revision",
        "operation_key",
    }),
    "pick_up_item": frozenset({
        "action",
        "item_id",
        "hand_slot",
        "expected_revision",
        "operation_key",
    }),
    "take_from_container": frozenset({
        "action",
        "item_id",
        "container_id",
        "hand_slot",
        "expected_revision",
        "operation_key",
    }),
    "put_in_container": frozenset({
        "action",
        "item_id",
        "container_id",
        "expected_revision",
        "operation_key",
    }),
    "place_on_surface": frozenset({
        "action",
        "item_id",
        "place_id",
        "expected_revision",
        "operation_key",
    }),
    "pick_and_put_in_container": frozenset({
        "action",
        "item_id",
        "container_id",
        "hand_slot",
        "expected_revision",
        "operation_key",
    }),
    "subject_move_container": frozenset({
        "action",
        "container_id",
        "to_place_id",
        "follow_to_place",
        "expected_revision",
        "operation_key",
    }),
    "partner_move_container": frozenset({
        "action",
        "container_id",
        "to_place_id",
        "subject_witnessed",
        "expected_revision",
        "operation_key",
    }),
    "set_subject_location": frozenset({
        "action",
        "place_id",
        "present",
        "observe_on_arrival",
        "expected_revision",
    }),
    "observe_current_room": frozenset({
        "action",
        "actor",
        "container_id",
        "scope",
        "place_id",
        "room_id",
        "expected_revision",
        "operation_key",
    }),
    "set_item_impression_note": frozenset({
        "action",
        "target_id",
        "private_impression_note",
        "expected_revision",
    }),
    "remove_container": frozenset({
        "action",
        "container_id",
        "expected_revision",
    }),
    # Ch. 25: Cohabitation spatial negotiation
    "suggest_item_use": frozenset({
        "action",
        "actor",
        "target_id",
        "suggested_use",
        "target_container_id",
        "note",
        "expected_revision",
        "operation_key",
    }),
    "respond_item_use_suggestion": frozenset({
        "action",
        "suggestion_id",
        "decision",
        "note",
        "expected_revision",
        "operation_key",
    }),
    "lend_item": frozenset({
        "action",
        "actor",
        "item_id",
        "to_side",
        "mode",
        "note",
        "expected_revision",
        "operation_key",
    }),
    "preview_lamp_move": frozenset({
        "action",
        "actor",
        "from_place_id",
        "to_place_id",
        "commit",
        "note",
        "expected_revision",
        "operation_key",
    }),
    # 3. Attached desk co-presence & kitchen/table portion conservation
    "set_primary_activity": frozenset({
        "action",
        "activity_id",
        "label",
        "status",
        "interruptibility",
        "private_note",
        "expected_revision",
    }),
    "propose_copresence": frozenset({
        "action",
        "actor",
        "target_mode",
        "declared_minutes",
        "note",
        "expected_revision",
    }),
    "respond_copresence": frozenset({
        "action",
        "actor",
        "decision",
        "expected_revision",
    }),
    "switch_copresence_mode": frozenset({
        "action",
        "actor",
        "mode",
        "declared_minutes",
        "note",
        "expected_revision",
    }),
    "exit_copresence": frozenset({
        "action",
        "actor",
        "note",
        "expected_revision",
    }),
    "prepare_table_dish": frozenset({
        "action",
        "title",
        "kind",
        "portions",
        "place_id",
        "container_id",
        "initial_temperature",
        "private_note",
        "expected_revision",
        "operation_key",
    }),
    "partner_bring_snack": frozenset({
        "action",
        "title",
        "kind",
        "portions",
        "place_id",
        "container_id",
        "initial_temperature",
        "note",
        "expected_revision",
        "operation_key",
    }),
    "take_portion": frozenset({
        "action",
        "dish_id",
        "consumer",
        "portions",
        "expected_revision",
        "operation_key",
    }),
    "clear_table_dish": frozenset({
        "action",
        "dish_id",
        "actor",
        "discard_remaining",
        "expected_revision",
    }),
}

STANCE_CONTAINMENT_ACTIONS: frozenset[str] = frozenset(ACTION_ALLOWED_FIELDS.keys())

STANCE_CONTAINMENT_SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "propose_stance",
    "fix_stance_typo",
    "revise_stance",
    "set_stance_sharing",
    "record_obtained_pages",
    "pick_up_item",
    "take_from_container",
    "put_in_container",
    "place_on_surface",
    "pick_and_put_in_container",
    "subject_move_container",
    "set_subject_location",
    "observe_current_room",
    "set_item_impression_note",
    "respond_item_use_suggestion",
    "set_primary_activity",
    "prepare_table_dish",
})

STANCE_CONTAINMENT_FIELDS: frozenset[str] = frozenset().union(*ACTION_ALLOWED_FIELDS.values())

OWNER_SYNCED_ACTIONS: frozenset[str] = frozenset(owner_substrate.OWNER_ACTIONS)


# ---------------------------------------------------------------------------------------------
# Structured Exceptions
# ---------------------------------------------------------------------------------------------


class StanceContainmentError(ValueError):
    """Base structured error for stance / containment / desk-table actions."""

    http_status: int = 400

    def __init__(self, reason_code: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.details: dict[str, Any] = dict(details or {})


class StanceContainmentValidationError(StanceContainmentError):
    """400 Bad Request contract / schema / unknown-field error."""

    http_status = 400


class StanceContainmentPermissionError(StanceContainmentError):
    """403 Forbidden capability / actor impersonation error."""

    http_status = 403


class StanceContainmentNotFoundError(StanceContainmentError):
    """404 Not Found error."""

    http_status = 404


class StanceContainmentConflictError(StanceContainmentError):
    """409 Conflict / CAS mismatch / lifecycle / conservation error."""

    http_status = 409


# ---------------------------------------------------------------------------------------------
# Deterministic Helpers & Clock Normalization
# ---------------------------------------------------------------------------------------------


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _cps_session_id(*parts: Any) -> str:
    """ERR-03 canonical underscore ``cps_<hex16>`` co-presence session ID."""
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"cps_{digest}"


def _fingerprint(action: str, fields: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"action": action, "fields": dict(fields)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _normalize_now(now: str | datetime | None) -> tuple[datetime, str]:
    if now is None:
        dt = datetime.now(tz=timezone(timedelta(hours=8))).replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds")
    if isinstance(now, datetime):
        dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        dt = dt.replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds")
    if isinstance(now, str):
        stripped = now.strip()
        if not stripped or "\x00" in stripped:
            raise StanceContainmentValidationError("NOW_TIMESTAMP_INVALID")
        normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise StanceContainmentValidationError("NOW_TIMESTAMP_INVALID") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds")
    raise StanceContainmentValidationError("NOW_TIMESTAMP_INVALID")


def _parse_stored_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso_or_none(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:48]


def _require_id(value: Any, *, reason_code: str) -> str:
    if not isinstance(value, str):
        raise StanceContainmentValidationError(reason_code)
    cleaned = value.strip()
    if not LOCAL_ID_RE.match(cleaned):
        raise StanceContainmentValidationError(reason_code)
    return cleaned


def _require_place_id(value: Any, *, reason_code: str = "PLACE_ID_INVALID") -> str:
    pid = _require_id(value, reason_code=reason_code)
    if pid in HAND_SLOTS:
        raise StanceContainmentValidationError(reason_code)
    return pid


def _require_anchor(value: Any, *, reason_code: str = "PAGE_OR_ANCHOR_INVALID") -> str:
    if isinstance(value, bool):
        raise StanceContainmentValidationError(reason_code)
    if isinstance(value, int):
        if value < 1 or value > 100000:
            raise StanceContainmentValidationError(reason_code)
        return str(value)
    if isinstance(value, str):
        if "\x00" in value or "\r" in value or "\n" in value:
            raise StanceContainmentValidationError(reason_code)
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 80:
            raise StanceContainmentValidationError(reason_code)
        return cleaned
    raise StanceContainmentValidationError(reason_code)


def _clean_single_line(
    value: Any, *, min_len: int, max_len: int, reason_code: str
) -> str:
    if not isinstance(value, str) or "\x00" in value or "\r" in value or "\n" in value:
        raise StanceContainmentValidationError(reason_code)
    cleaned = " ".join(value.split())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise StanceContainmentValidationError(reason_code)
    return cleaned


def _clean_multiline(
    value: Any, *, min_len: int, max_len: int, reason_code: str
) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise StanceContainmentValidationError(reason_code)
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise StanceContainmentValidationError(reason_code)
    return cleaned


def _optional_single_line(
    payload: Mapping[str, Any], key: str, *, max_len: int, reason_code: str
) -> Optional[str]:
    if key not in payload:
        return None
    return _clean_single_line(
        payload.get(key), min_len=1, max_len=max_len, reason_code=reason_code
    )


def _optional_multiline(
    payload: Mapping[str, Any], key: str, *, max_len: int, reason_code: str
) -> Optional[str]:
    if key not in payload:
        return None
    return _clean_multiline(
        payload.get(key), min_len=1, max_len=max_len, reason_code=reason_code
    )


def _require_bool(value: Any, *, reason_code: str) -> bool:
    if not isinstance(value, bool):
        raise StanceContainmentValidationError(reason_code)
    return value


def _require_int_range(
    value: Any, *, minimum: int, maximum: int, reason_code: str
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StanceContainmentValidationError(reason_code)
    if value < minimum or value > maximum:
        raise StanceContainmentValidationError(reason_code)
    return int(value)


def _optional_expected_revision(payload: Mapping[str, Any]) -> Optional[int]:
    if "expected_revision" not in payload:
        return None
    return _require_int_range(
        payload.get("expected_revision"),
        minimum=0,
        maximum=10_000_000,
        reason_code="EXPECTED_REVISION_INVALID",
    )


def _room_for_place(place_id: str) -> str:
    if place_id in PLACE_ROOM_MAP:
        return PLACE_ROOM_MAP[place_id]
    lowered = place_id.lower()
    if "kitchen" in lowered or "dining" in lowered or "fridge" in lowered:
        return "kitchen_dining"
    if lowered in ("outside", "out_of_room", "outdoor"):
        return "outside"
    return "study_room"


# ---------------------------------------------------------------------------------------------
# Character Pack Identity (Zero Role Lock, Dynamic Subject Name / Pronouns)
# ---------------------------------------------------------------------------------------------


def _read_character_identity(root: Path | str) -> dict[str, str]:
    """Read active character pack dynamically without hardcoding Chiyo or feminine pronouns."""

    pack_info: Mapping[str, Any] = {}
    reader = getattr(char_view, "read_character_pack", None)
    if callable(reader):
        try:
            res = reader(Path(root), subject_view=True)
            if isinstance(res, Mapping):
                if isinstance(res.get("active_pack"), Mapping):
                    pack_info = res["active_pack"]
                elif isinstance(res.get("pack_doc"), Mapping):
                    pack_info = res["pack_doc"]
                else:
                    pack_info = res
        except Exception:
            pack_info = {}
    else:
        try:
            c_state, c_reason = char_view.read_character_pack_state(root)
            projected = char_view.project_character_pack(
                c_state, state_reason=c_reason, subject_view=True
            )
            if isinstance(projected.get("active_pack"), Mapping):
                pack_info = projected["active_pack"]
        except Exception:
            pack_info = {}

    pack_id = str(pack_info.get("pack_id") or char_view.DEFAULT_PRESET_ID).strip() or "neutral_resident"
    display_name = str(pack_info.get("display_name") or "TA").strip() or "TA"
    pronouns = str(pack_info.get("pronouns") or "TA").strip() or "TA"
    return {
        "pack_id": pack_id,
        "display_name": display_name,
        "pronouns": pronouns,
    }


# ---------------------------------------------------------------------------------------------
# Default State & Atomic 0600 Persistence (<root>/phone/stance_containment.json)
# ---------------------------------------------------------------------------------------------


def stance_containment_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / STANCE_CONTAINMENT_FILENAME


def _default_containers() -> list[dict[str, Any]]:
    return [
        {
            "container_id": "pen_cup",
            "label": "木质笔筒",
            "place_id": "study_desk",
            "room_id": "study_room",
            "parent_container_id": None,
            "capacity": 2,
            "accepted_classes": ["writing_tool", "stationery", "eyewear"],
            "open_state": "open_top",
            "contents_visibility": "open_top",
            "portable": True,
            "drinkware": False,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "container_id": "ceramic_mug",
            "label": "粗陶马克杯",
            "place_id": "study_desk",
            "room_id": "study_room",
            "parent_container_id": None,
            "capacity": 2,
            "accepted_classes": ["writing_tool", "stationery"],
            "open_state": "open_top",
            "contents_visibility": "open_top",
            "portable": True,
            "drinkware": True,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "container_id": "desk_tray",
            "label": "胡桃木收纳托盘",
            "place_id": "study_desk",
            "room_id": "study_room",
            "parent_container_id": None,
            "capacity": 3,
            "accepted_classes": ["writing_tool", "stationery", "eyewear", "keys", "small_item"],
            "open_state": "open_top",
            "contents_visibility": "open_top",
            "portable": True,
            "drinkware": False,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "container_id": "tote_bag",
            "label": "帆布随身袋",
            "place_id": "bookshelf",
            "room_id": "study_room",
            "parent_container_id": None,
            "capacity": 4,
            "accepted_classes": ["writing_tool", "stationery", "eyewear", "keys", "small_item"],
            "open_state": "open_top",
            "contents_visibility": "open_top",
            "portable": True,
            "drinkware": False,
            "subject_last_seen_place": "bookshelf",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "container_id": "entryway_tray",
            "label": "玄关收纳托盘",
            "place_id": "entry_stand",
            "room_id": "entryway",
            "parent_container_id": None,
            "capacity": 4,
            "accepted_classes": ["writing_tool", "stationery", "eyewear", "keys", "small_item"],
            "open_state": "open_top",
            "contents_visibility": "open_top",
            "portable": True,
            "drinkware": False,
            "subject_last_seen_place": "entry_stand",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
    ]


def _default_items() -> list[dict[str, Any]]:
    return [
        {
            "item_id": "fountain_pen",
            "label": "墨水钢笔",
            "item_class": "writing_tool",
            "owner": "subject",
            "placement_kind": "surface",
            "place_id": "study_desk",
            "parent_container_id": None,
            "held_in_hand": None,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "item_id": "gel_pen",
            "label": "速干中性笔",
            "item_class": "writing_tool",
            "owner": "subject",
            "placement_kind": "surface",
            "place_id": "study_desk",
            "parent_container_id": None,
            "held_in_hand": None,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "item_id": "sketch_pencil",
            "label": "木杆铅笔",
            "item_class": "writing_tool",
            "owner": "subject",
            "placement_kind": "surface",
            "place_id": "study_desk",
            "parent_container_id": None,
            "held_in_hand": None,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "item_id": "brass_bookmark",
            "label": "黄铜书签",
            "item_class": "stationery",
            "owner": "subject",
            "placement_kind": "surface",
            "place_id": "study_desk",
            "parent_container_id": None,
            "held_in_hand": None,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "item_id": "memo_clip",
            "label": "燕尾便签夹",
            "item_class": "stationery",
            "owner": "subject",
            "placement_kind": "surface",
            "place_id": "study_desk",
            "parent_container_id": None,
            "held_in_hand": None,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "item_id": "reading_glasses",
            "label": "细框平光镜",
            "item_class": "eyewear",
            "owner": "subject",
            "placement_kind": "surface",
            "place_id": "study_desk",
            "parent_container_id": None,
            "held_in_hand": None,
            "subject_last_seen_place": "study_desk",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
        {
            "item_id": "house_keys",
            "label": "黄铜钥匙串",
            "item_class": "keys",
            "owner": "shared",
            "placement_kind": "surface",
            "place_id": "bookshelf",
            "parent_container_id": None,
            "held_in_hand": None,
            "subject_last_seen_place": "bookshelf",
            "subject_last_seen_at": None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        },
    ]


def default_stance_containment_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "stance_domain": {
            "stances": [],
            "source_registry": {},
            "active_stance_ids": [],
            "obtained_pages_by_scope": {},
        },
        "containment_domain": {
            "subject_place_id": "study_desk",
            "subject_room_id": "study_room",
            "subject_present": True,
            "hand_slots": {"left_hand": None, "right_hand": None},
            "containers": _default_containers(),
            "items": _default_items(),
            "cohabitation": owner_substrate._default_cohabitation(),
        },
        "desk_table_domain": {
            "primary_activity": {
                "activity_id": "act-desk-work-01",
                "label": "整理手头练习稿",
                "status": "ACTIVE",
                "interruptibility": "SAFE_BOUNDARY",
                "private_note": None,
                "updated_at": None,
            },
            "copresence": {
                "session_id": None,
                "mode": "off",
                "attached_to_activity_id": None,
                "started_at": None,
                "expected_end_at": None,
                "declared_minutes": None,
                "initiated_by": None,
                "last_changed_by": None,
                "last_exit_by": None,
                "last_exit_at": None,
                "note": None,
                "pending_proposal": None,
            },
            "table_dishes": [],
        },
        "operation_receipts": [],
        "next_op_seq": 0,
        "committed_op_ring": [],
        "evicted_up_to_revision": 0,
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists() and path.is_symlink():
        raise StanceContainmentConflictError("STANCE_CONTAINMENT_STATE_SYMLINK")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise StanceContainmentConflictError("STANCE_CONTAINMENT_DIR_SYMLINK")
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(payload))
    encoded = (
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent)
    )
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
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return doc


def _validate_stored_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")

    stance_dom = raw.get("stance_domain")
    if not isinstance(stance_dom, Mapping):
        raise ValueError("invalid stance_domain")
    stances_raw = stance_dom.get("stances")
    src_reg_raw = stance_dom.get("source_registry")
    if not isinstance(stances_raw, list) or len(stances_raw) > MAX_STANCES:
        raise ValueError("invalid stances list")
    if not isinstance(src_reg_raw, Mapping):
        raise ValueError("invalid source_registry")

    validated_registry: dict[str, dict[str, Any]] = {}
    for raw_scope, info in src_reg_raw.items():
        scope = _require_id(raw_scope, reason_code="SCOPE_REF_INVALID")
        if not isinstance(info, Mapping):
            raise ValueError("invalid source_registry entry")
        cur_rev = _require_id(
            info.get("current_source_revision"), reason_code="SOURCE_REVISION_INVALID"
        )
        withdrawn = bool(info.get("withdrawn", False))
        validated_registry[scope] = {
            "scope_ref": scope,
            "current_source_revision": cur_rev,
            "withdrawn": withdrawn,
            "updated_at": _iso_or_none(info.get("updated_at")),
            "withdrawn_at": _iso_or_none(info.get("withdrawn_at")),
            "note": _iso_or_none(info.get("note"))
            if info.get("note") is None
            else str(info.get("note"))[:MAX_PUBLIC_NOTE],
            "withdraw_reason": None
            if info.get("withdraw_reason") is None
            else str(info.get("withdraw_reason"))[:MAX_PUBLIC_NOTE],
        }

    validated_stances: list[dict[str, Any]] = []
    seen_stance_ids: set[str] = set()
    for item in stances_raw:
        if not isinstance(item, Mapping):
            raise ValueError("invalid stance entry")
        sid = _require_id(item.get("stance_id"), reason_code="STANCE_ID_INVALID")
        if sid in seen_stance_ids:
            raise ValueError("duplicate stance_id")
        seen_stance_ids.add(sid)
        scope_ref = _require_id(item.get("scope_ref"), reason_code="SCOPE_REF_INVALID")
        src_rev = _require_id(
            item.get("source_revision"), reason_code="SOURCE_REVISION_INVALID"
        )
        anchor = _require_anchor(item.get("page_or_anchor"))
        facet = item.get("facet")
        if not isinstance(facet, str) or facet not in STANCE_FACETS:
            raise ValueError("invalid facet")
        status = item.get("status")
        if not isinstance(status, str) or status not in STANCE_STATUSES:
            raise ValueError("invalid stance status")
        # Enforce source withdrawal irreversibility on load
        if validated_registry.get(scope_ref, {}).get("withdrawn"):
            status = "SOURCE_WITHDRAWN"
        text_version = _require_int_range(
            item.get("text_version", 1),
            minimum=1,
            maximum=100000,
            reason_code="TEXT_VERSION_INVALID",
        )
        lineage_depth = _require_int_range(
            item.get("lineage_depth", 0),
            minimum=0,
            maximum=MAX_STANCES,
            reason_code="LINEAGE_DEPTH_INVALID",
        )
        root_id = _require_id(
            item.get("root_stance_id") or sid, reason_code="STANCE_ID_INVALID"
        )
        replaces_id = (
            None
            if item.get("replaces_stance_id") is None
            else _require_id(item.get("replaces_stance_id"), reason_code="STANCE_ID_INVALID")
        )
        superseded_by = (
            None
            if item.get("superseded_by") is None
            else _require_id(item.get("superseded_by"), reason_code="STANCE_ID_INVALID")
        )
        if status == "SOURCE_WITHDRAWN":
            text_val: Optional[str] = None
            pnote_val: Optional[str] = None
            shared_val = False
            purged = True
        else:
            text_val = _clean_multiline(
                item.get("text"),
                min_len=1,
                max_len=MAX_STANCE_TEXT,
                reason_code="STANCE_TEXT_INVALID",
            )
            pnote_val = (
                None
                if item.get("private_note") is None
                else _clean_multiline(
                    item.get("private_note"),
                    min_len=1,
                    max_len=MAX_PRIVATE_NOTE,
                    reason_code="PRIVATE_NOTE_INVALID",
                )
            )
            shared_val = bool(item.get("shared_with_partner", False))
            purged = False

        validated_stances.append({
            "stance_id": sid,
            "root_stance_id": root_id,
            "lineage_depth": lineage_depth,
            "scope_ref": scope_ref,
            "source_revision": src_rev,
            "page_or_anchor": anchor,
            "facet": facet,
            "status": status,
            "text": text_val,
            "text_version": text_version,
            "shared_with_partner": shared_val,
            "private_note": pnote_val,
            "replaces_stance_id": replaces_id,
            "superseded_by": superseded_by,
            "stale_due_to_revision": (
                None
                if item.get("stale_due_to_revision") is None
                else str(item.get("stale_due_to_revision"))[:120]
            ),
            "content_purged": purged,
            "created_at": _iso_or_none(item.get("created_at")),
            "updated_at": _iso_or_none(item.get("updated_at")),
            "superseded_at": _iso_or_none(item.get("superseded_at")),
            "stale_marked_at": _iso_or_none(item.get("stale_marked_at")),
            "withdrawn_at": _iso_or_none(item.get("withdrawn_at")),
            "last_edit_kind": str(item.get("last_edit_kind") or "propose")[:32],
        })

    rebuilt_active_ids = _compute_active_stance_ids(validated_stances, validated_registry)

    cont_dom = raw.get("containment_domain")
    if not isinstance(cont_dom, Mapping):
        raise ValueError("invalid containment_domain")
    subj_place = _require_place_id(cont_dom.get("subject_place_id"))
    subj_room = _require_id(
        cont_dom.get("subject_room_id") or _room_for_place(subj_place),
        reason_code="ROOM_ID_INVALID",
    )
    subj_present = _require_bool(
        cont_dom.get("subject_present", True), reason_code="SUBJECT_PRESENT_INVALID"
    )
    raw_hands = cont_dom.get("hand_slots")
    if not isinstance(raw_hands, Mapping):
        raise ValueError("invalid hand_slots")
    containers_raw = cont_dom.get("containers")
    items_raw = cont_dom.get("items")
    if not isinstance(containers_raw, list) or len(containers_raw) > MAX_CONTAINERS:
        raise ValueError("invalid containers")
    if not isinstance(items_raw, list) or len(items_raw) > MAX_ITEMS:
        raise ValueError("invalid items")

    validated_containers: list[dict[str, Any]] = []
    container_map: dict[str, dict[str, Any]] = {}
    for c in containers_raw:
        if not isinstance(c, Mapping):
            raise ValueError("invalid container")
        cid = _require_id(c.get("container_id"), reason_code="CONTAINER_ID_INVALID")
        if cid in container_map:
            raise ValueError("duplicate container_id")
        c_place = _require_place_id(c.get("place_id"))
        c_cap = _require_int_range(
            c.get("capacity"),
            minimum=1,
            maximum=MAX_CONTAINER_CAPACITY,
            reason_code="CAPACITY_INVALID",
        )
        acc_raw = c.get("accepted_classes")
        if not isinstance(acc_raw, list) or not acc_raw:
            raise ValueError("invalid accepted_classes")
        acc = [str(cls) for cls in acc_raw if str(cls) in ITEM_CLASSES]
        if not acc:
            raise ValueError("empty accepted_classes")
        open_st = str(c.get("open_state") or c.get("contents_visibility") or "open_top")
        if open_st not in CONTAINER_OPEN_STATES:
            raise ValueError("invalid open_state")
        parent_cid = (
            None
            if c.get("parent_container_id") is None
            else _require_id(c.get("parent_container_id"), reason_code="CONTAINER_ID_INVALID")
        )
        c_doc = {
            "container_id": cid,
            "label": _clean_single_line(
                c.get("label"), min_len=1, max_len=MAX_TITLE, reason_code="LABEL_INVALID"
            ),
            "place_id": c_place,
            "room_id": _require_id(
                c.get("room_id") or _room_for_place(c_place),
                reason_code="ROOM_ID_INVALID",
            ),
            "parent_container_id": parent_cid,
            "capacity": c_cap,
            "accepted_classes": acc,
            "open_state": open_st,
            "contents_visibility": "open_top" if open_st == "open_top" else open_st,
            "portable": bool(c.get("portable", True)),
            "drinkware": bool(c.get("drinkware", False)),
            "subject_last_seen_place": _require_place_id(
                c.get("subject_last_seen_place") or c_place
            ),
            "subject_last_seen_at": _iso_or_none(c.get("subject_last_seen_at")),
            "not_at_last_seen_place": bool(c.get("not_at_last_seen_place", False)),
            "private_impression_note": (
                None
                if c.get("private_impression_note") is None
                else _clean_multiline(
                    c.get("private_impression_note"),
                    min_len=1,
                    max_len=MAX_PRIVATE_NOTE,
                    reason_code="PRIVATE_NOTE_INVALID",
                )
            ),
        }
        validated_containers.append(c_doc)
        container_map[cid] = c_doc

    validated_items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
    child_counts: dict[str, int] = {cid: 0 for cid in container_map}
    for c_doc in validated_containers:
        eff_c_place = owner_substrate.effective_container_place(
            c_doc["container_id"], container_map
        )
        c_doc["place_id"] = eff_c_place
        c_doc["room_id"] = _room_for_place(eff_c_place)
        pcid_parent = c_doc["parent_container_id"]
        if pcid_parent is not None:
            child_counts[pcid_parent] += 1
            if child_counts[pcid_parent] > container_map[pcid_parent]["capacity"]:
                raise ValueError("nested container exceeds parent capacity")
    derived_hands: dict[str, Optional[str]] = {"left_hand": None, "right_hand": None}

    for it in items_raw:
        if not isinstance(it, Mapping):
            raise ValueError("invalid item")
        iid = _require_id(it.get("item_id"), reason_code="ITEM_ID_INVALID")
        if iid in seen_item_ids or iid in container_map:
            raise ValueError("duplicate or colliding item_id")
        seen_item_ids.add(iid)
        icls = it.get("item_class")
        if not isinstance(icls, str) or icls not in ITEM_CLASSES:
            raise ValueError("invalid item_class")
        pkind = it.get("placement_kind")
        if pkind not in ("surface", "container", "hand"):
            raise ValueError("invalid placement_kind")

        if pkind == "container":
            pcid = _require_id(
                it.get("parent_container_id"), reason_code="CONTAINER_ID_INVALID"
            )
            if pcid not in container_map:
                raise ValueError("unknown parent_container_id")
            child_counts[pcid] += 1
            if child_counts[pcid] > container_map[pcid]["capacity"]:
                raise ValueError("container capacity exceeded")
            eff_place = container_map[pcid]["place_id"]
            held_hand = None
        elif pkind == "hand":
            hslot = it.get("held_in_hand")
            if hslot not in HAND_SLOTS:
                raise ValueError("invalid held_in_hand")
            if derived_hands[hslot] is not None:
                raise ValueError("duplicate hand occupancy")
            derived_hands[hslot] = iid
            pcid = None
            held_hand = hslot
            eff_place = subj_place
        else:
            pcid = None
            held_hand = None
            eff_place = _require_place_id(it.get("place_id"))

        validated_items.append({
            "item_id": iid,
            "label": _clean_single_line(
                it.get("label"), min_len=1, max_len=MAX_TITLE, reason_code="LABEL_INVALID"
            ),
            "item_class": icls,
            "owner": str(it.get("owner") or "subject")[:24],
            "placement_kind": pkind,
            "place_id": eff_place,
            "parent_container_id": pcid,
            "held_in_hand": held_hand,
            "subject_last_seen_place": _require_place_id(
                it.get("subject_last_seen_place") or eff_place
            ),
            "subject_last_seen_at": _iso_or_none(it.get("subject_last_seen_at")),
            "not_at_last_seen_place": bool(it.get("not_at_last_seen_place", False)),
            "private_impression_note": (
                None
                if it.get("private_impression_note") is None
                else _clean_multiline(
                    it.get("private_impression_note"),
                    min_len=1,
                    max_len=MAX_PRIVATE_NOTE,
                    reason_code="PRIVATE_NOTE_INVALID",
                )
            ),
        })

    for slot in HAND_SLOTS:
        stored_slot_val = raw_hands.get(slot)
        if stored_slot_val != derived_hands[slot]:
            raise ValueError("hand_slots out of sync with items")

    dt_dom = raw.get("desk_table_domain")
    if not isinstance(dt_dom, Mapping):
        raise ValueError("invalid desk_table_domain")
    pa_raw = dt_dom.get("primary_activity")
    if not isinstance(pa_raw, Mapping):
        raise ValueError("invalid primary_activity")
    pa_status = pa_raw.get("status")
    if pa_status not in PRIMARY_ACTIVITY_STATUSES:
        raise ValueError("invalid primary_activity status")
    pa_intr = pa_raw.get("interruptibility")
    if pa_intr not in INTERRUPTIBILITY_MODES:
        raise ValueError("invalid primary_activity interruptibility")
    validated_pa = {
        "activity_id": _require_id(pa_raw.get("activity_id"), reason_code="ACTIVITY_ID_INVALID"),
        "label": _clean_single_line(
            pa_raw.get("label"), min_len=1, max_len=MAX_TITLE, reason_code="LABEL_INVALID"
        ),
        "status": pa_status,
        "interruptibility": pa_intr,
        "private_note": (
            None
            if pa_raw.get("private_note") is None
            else _clean_multiline(
                pa_raw.get("private_note"),
                min_len=1,
                max_len=MAX_PRIVATE_NOTE,
                reason_code="PRIVATE_NOTE_INVALID",
            )
        ),
        "updated_at": _iso_or_none(pa_raw.get("updated_at")),
    }

    cp_raw = dt_dom.get("copresence")
    if not isinstance(cp_raw, Mapping):
        raise ValueError("invalid copresence")
    cp_mode = cp_raw.get("mode")
    if cp_mode not in COPRESENCE_MODES:
        raise ValueError("invalid copresence mode")
    prop_raw = cp_raw.get("pending_proposal")
    validated_prop: Optional[dict[str, Any]] = None
    if prop_raw is not None:
        if not isinstance(prop_raw, Mapping):
            raise ValueError("invalid pending_proposal")
        p_actor = prop_raw.get("proposer")
        p_mode = prop_raw.get("target_mode")
        if p_actor not in PARTICIPANT_ACTORS or p_mode not in ACTIVE_COPRESENCE_MODES:
            raise ValueError("invalid pending_proposal values")
        validated_prop = {
            "proposal_id": _require_id(
                prop_raw.get("proposal_id"), reason_code="PROPOSAL_ID_INVALID"
            ),
            "proposer": p_actor,
            "target_mode": p_mode,
            "declared_minutes": _require_int_range(
                prop_raw.get("declared_minutes", DEFAULT_COPRESENCE_MINUTES),
                minimum=1,
                maximum=MAX_COPRESENCE_MINUTES,
                reason_code="DECLARED_MINUTES_INVALID",
            ),
            "note": (
                None
                if prop_raw.get("note") is None
                else _clean_single_line(
                    prop_raw.get("note"),
                    min_len=1,
                    max_len=MAX_PUBLIC_NOTE,
                    reason_code="NOTE_INVALID",
                )
            ),
            "proposed_at": _iso_or_none(prop_raw.get("proposed_at")),
        }

    validated_cp = {
        "session_id": (
            None
            if cp_raw.get("session_id") is None
            else _require_id(cp_raw.get("session_id"), reason_code="SESSION_ID_INVALID")
        ),
        "mode": cp_mode,
        "attached_to_activity_id": (
            None
            if cp_raw.get("attached_to_activity_id") is None
            else _require_id(
                cp_raw.get("attached_to_activity_id"), reason_code="ACTIVITY_ID_INVALID"
            )
        ),
        "started_at": _iso_or_none(cp_raw.get("started_at")),
        "expected_end_at": _iso_or_none(cp_raw.get("expected_end_at")),
        "declared_minutes": (
            None
            if cp_raw.get("declared_minutes") is None
            else _require_int_range(
                cp_raw.get("declared_minutes"),
                minimum=1,
                maximum=MAX_COPRESENCE_MINUTES,
                reason_code="DECLARED_MINUTES_INVALID",
            )
        ),
        "initiated_by": (
            None
            if cp_raw.get("initiated_by") is None
            else str(cp_raw.get("initiated_by"))
        ),
        "last_changed_by": (
            None
            if cp_raw.get("last_changed_by") is None
            else str(cp_raw.get("last_changed_by"))
        ),
        "last_exit_by": (
            None
            if cp_raw.get("last_exit_by") is None
            else str(cp_raw.get("last_exit_by"))
        ),
        "last_exit_at": _iso_or_none(cp_raw.get("last_exit_at")),
        "note": (
            None
            if cp_raw.get("note") is None
            else _clean_single_line(
                cp_raw.get("note"),
                min_len=1,
                max_len=MAX_PUBLIC_NOTE,
                reason_code="NOTE_INVALID",
            )
        ),
        "pending_proposal": validated_prop,
    }

    dishes_raw = dt_dom.get("table_dishes")
    if not isinstance(dishes_raw, list) or len(dishes_raw) > MAX_TABLE_DISHES:
        raise ValueError("invalid table_dishes")
    validated_dishes: list[dict[str, Any]] = []
    seen_dish_ids: set[str] = set()
    for d in dishes_raw:
        if not isinstance(d, Mapping):
            raise ValueError("invalid dish")
        did = _require_id(d.get("dish_id"), reason_code="DISH_ID_INVALID")
        if did in seen_dish_ids:
            raise ValueError("duplicate dish_id")
        seen_dish_ids.add(did)
        dkind = d.get("kind")
        if dkind not in DISH_KINDS:
            raise ValueError("invalid dish kind")
        init_p = _require_int_range(
            d.get("initial_portions"),
            minimum=1,
            maximum=MAX_PORTIONS,
            reason_code="PORTIONS_INVALID",
        )
        rem_p = _require_int_range(
            d.get("portions_remaining"),
            minimum=0,
            maximum=init_p,
            reason_code="PORTIONS_INVALID",
        )
        c_subj = _require_int_range(
            d.get("consumed_by_subject", 0),
            minimum=0,
            maximum=init_p,
            reason_code="PORTIONS_INVALID",
        )
        c_part = _require_int_range(
            d.get("consumed_by_partner", 0),
            minimum=0,
            maximum=init_p,
            reason_code="PORTIONS_INVALID",
        )
        disc_p = _require_int_range(
            d.get("discarded_portions", 0),
            minimum=0,
            maximum=init_p,
            reason_code="PORTIONS_INVALID",
        )
        if rem_p + c_subj + c_part + disc_p != init_p:
            raise ValueError("portion conservation violated")
        init_temp = d.get("initial_temperature", "hot")
        if init_temp not in THERMAL_BANDS:
            raise ValueError("invalid initial_temperature")
        prep_by = d.get("prepared_by")
        if prep_by not in PARTICIPANT_ACTORS:
            raise ValueError("invalid prepared_by")
        d_status = "EMPTY" if rem_p == 0 else "AVAILABLE"
        validated_dishes.append({
            "dish_id": did,
            "title": _clean_single_line(
                d.get("title"), min_len=1, max_len=MAX_TITLE, reason_code="TITLE_INVALID"
            ),
            "kind": dkind,
            "place_id": _require_place_id(d.get("place_id") or "dining_table"),
            "prepared_by": prep_by,
            "prepared_at": _iso_or_none(d.get("prepared_at")),
            "initial_temperature": init_temp,
            "initial_portions": init_p,
            "portions_remaining": rem_p,
            "consumed_by_subject": c_subj,
            "consumed_by_partner": c_part,
            "discarded_portions": disc_p,
            "status": d_status,
            "empty_at": _iso_or_none(d.get("empty_at")),
            "note": (
                None
                if d.get("note") is None
                else _clean_single_line(
                    d.get("note"),
                    min_len=1,
                    max_len=MAX_PUBLIC_NOTE,
                    reason_code="NOTE_INVALID",
                )
            ),
            "private_note": (
                None
                if d.get("private_note") is None
                else _clean_multiline(
                    d.get("private_note"),
                    min_len=1,
                    max_len=MAX_PRIVATE_NOTE,
                    reason_code="PRIVATE_NOTE_INVALID",
                )
            ),
        })

    receipts_raw = raw.get("operation_receipts") or []
    if not isinstance(receipts_raw, list) or len(receipts_raw) > MAX_OPERATION_RECEIPTS:
        raise ValueError("invalid operation_receipts")
    validated_receipts: list[dict[str, Any]] = []
    for rec in receipts_raw:
        if not isinstance(rec, Mapping):
            raise ValueError("invalid receipt")
        op_k = _clean_single_line(
            rec.get("operation_key"),
            min_len=1,
            max_len=MAX_OPERATION_KEY,
            reason_code="OPERATION_KEY_INVALID",
        )
        receipt_row = {
            "operation_key": op_k,
            "action": str(rec.get("action") or "")[:64],
            "fingerprint": str(rec.get("fingerprint") or "")[:64],
            "scope_ref": (
                None if rec.get("scope_ref") is None else str(rec.get("scope_ref"))[:120]
            ),
            "target_id": (
                None if rec.get("target_id") is None else str(rec.get("target_id"))[:120]
            ),
            "recorded_at": _iso_or_none(rec.get("recorded_at")),
        }
        rec_seq = rec.get("op_seq")
        if isinstance(rec_seq, int) and not isinstance(rec_seq, bool):
            receipt_row["op_seq"] = rec_seq
        rec_rev = rec.get("revision")
        if isinstance(rec_rev, int) and not isinstance(rec_rev, bool):
            receipt_row["revision"] = rec_rev
        validated_receipts.append(receipt_row)

    ring_raw = raw.get("committed_op_ring") or []
    if not isinstance(ring_raw, list):
        raise ValueError("invalid committed_op_ring")
    validated_ring: list[dict[str, Any]] = []
    for rec in ring_raw:
        if not isinstance(rec, Mapping):
            raise ValueError("invalid committed_op_ring entry")
        ring_row = {
            "operation_key": _clean_single_line(
                rec.get("operation_key"),
                min_len=1,
                max_len=MAX_OPERATION_KEY,
                reason_code="OPERATION_KEY_INVALID",
            ),
            "action": str(rec.get("action") or "")[:64],
            "fingerprint": str(rec.get("fingerprint") or "")[:64],
            "scope_ref": (
                None if rec.get("scope_ref") is None else str(rec.get("scope_ref"))[:120]
            ),
            "target_id": (
                None if rec.get("target_id") is None else str(rec.get("target_id"))[:120]
            ),
            "recorded_at": _iso_or_none(rec.get("recorded_at")),
        }
        for seq_field in ("op_seq", "revision"):
            seq_val = rec.get(seq_field)
            if isinstance(seq_val, int) and not isinstance(seq_val, bool):
                ring_row[seq_field] = seq_val
        validated_ring.append(ring_row)
    if len(validated_ring) > MAX_COMMITTED_OP_RING:
        validated_ring = validated_ring[-MAX_COMMITTED_OP_RING:]

    highest_seq = 0
    for receipt_row in validated_receipts + validated_ring:
        seq_val = receipt_row.get("op_seq")
        if isinstance(seq_val, int) and not isinstance(seq_val, bool) and seq_val > highest_seq:
            highest_seq = seq_val
    stored_next_seq = raw.get("next_op_seq")
    next_op_seq = (
        stored_next_seq
        if isinstance(stored_next_seq, int)
        and not isinstance(stored_next_seq, bool)
        and stored_next_seq >= 0
        else 0
    )
    next_op_seq = max(next_op_seq, highest_seq)
    stored_evicted_rev = raw.get("evicted_up_to_revision")
    evicted_up_to_revision = (
        stored_evicted_rev
        if isinstance(stored_evicted_rev, int)
        and not isinstance(stored_evicted_rev, bool)
        and stored_evicted_rev >= 0
        else 0
    )

    validated_obtained_pages = owner_substrate._validate_obtained_pages_map(
        stance_dom.get("obtained_pages_by_scope")
    )
    validated_cohabitation = owner_substrate._validate_cohabitation(
        cont_dom.get("cohabitation")
    )

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_or_none(raw.get("updated_at")),
        "stance_domain": {
            "stances": validated_stances,
            "source_registry": validated_registry,
            "active_stance_ids": rebuilt_active_ids,
            "obtained_pages_by_scope": validated_obtained_pages,
        },
        "containment_domain": {
            "subject_place_id": subj_place,
            "subject_room_id": subj_room,
            "subject_present": subj_present,
            "hand_slots": derived_hands,
            "containers": validated_containers,
            "items": validated_items,
            "cohabitation": validated_cohabitation,
        },
        "desk_table_domain": {
            "primary_activity": validated_pa,
            "copresence": validated_cp,
            "table_dishes": validated_dishes,
        },
        "operation_receipts": validated_receipts,
        "next_op_seq": next_op_seq,
        "committed_op_ring": validated_ring,
        "evicted_up_to_revision": evicted_up_to_revision,
    }


def _compute_active_stance_ids(
    stances: list[dict[str, Any]],
    source_registry: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Deterministically rebuild active stance list; withdrawn/superseded/stale NEVER resurrect."""

    active_ids: list[str] = []
    for st in stances:
        if st.get("status") != "ACTIVE":
            continue
        if st.get("superseded_by") is not None:
            continue
        scope_ref = str(st.get("scope_ref") or "")
        reg = source_registry.get(scope_ref)
        if isinstance(reg, Mapping):
            if reg.get("withdrawn"):
                continue
            if reg.get("current_source_revision") != st.get("source_revision"):
                continue
        active_ids.append(str(st["stance_id"]))
    return active_ids


def _overlay_owner_domains_into_phone_state(
    phone_state: dict[str, Any], owner_state: Mapping[str, Any]
) -> dict[str, Any]:
    merged = copy.deepcopy(phone_state)
    owner_stance = owner_state.get("stance_domain") or {}
    merged["stance_domain"] = {
        "stances": [
            {k: v for k, v in copy.deepcopy(dict(s)).items() if k != "cached_excerpt"}
            for s in (owner_stance.get("stances") or [])
        ],
        "source_registry": copy.deepcopy(dict(owner_stance.get("source_registry") or {})),
        "active_stance_ids": list(owner_stance.get("active_stance_ids") or []),
        "obtained_pages_by_scope": copy.deepcopy(
            dict(owner_stance.get("obtained_pages_by_scope") or {})
        ),
    }
    merged["containment_domain"] = copy.deepcopy(dict(owner_state["containment_domain"]))
    merged["revision"] = max(
        int(merged.get("revision") or 0), int(owner_state.get("revision") or 0)
    )
    if owner_state.get("updated_at"):
        merged["updated_at"] = owner_state["updated_at"]
    if not merged.get("operation_receipts") and owner_state.get("operation_receipts"):
        merged["operation_receipts"] = copy.deepcopy(
            list(owner_state["operation_receipts"])
        )[-MAX_OPERATION_RECEIPTS:]
    return _validate_stored_state(merged)


def read_stance_containment_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    """Pure zero-write state load. Absent file returns default ``revision=0``."""

    path = stance_containment_state_path(root)
    if path.exists() and path.is_symlink():
        return default_stance_containment_state(), "STANCE_CONTAINMENT_STATE_SYMLINK"
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        return default_stance_containment_state(), "STANCE_CONTAINMENT_DIR_SYMLINK"

    phone_exists = True
    raw_text = ""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        phone_exists = False
    except OSError:
        return default_stance_containment_state(), "STANCE_CONTAINMENT_STATE_UNREADABLE"

    if not phone_exists:
        owner_state, owner_reason, owner_exists = (
            owner_substrate.read_attention_stance_containment_state(root)
        )
        if owner_reason is not None:
            return default_stance_containment_state(), "STANCE_CONTAINMENT_STATE_CORRUPT"
        if owner_exists and owner_state.get("origin_writer") == "owner":
            try:
                return (
                    _overlay_owner_domains_into_phone_state(
                        default_stance_containment_state(), owner_state
                    ),
                    None,
                )
            except Exception:
                return default_stance_containment_state(), "STANCE_CONTAINMENT_STATE_CORRUPT"
        return default_stance_containment_state(), None

    try:
        parsed = json.loads(raw_text)
        validated = _validate_stored_state(parsed)
    except Exception:
        return default_stance_containment_state(), "STANCE_CONTAINMENT_STATE_CORRUPT"

    owner_state, owner_reason, owner_exists = (
        owner_substrate.read_attention_stance_containment_state(root)
    )
    if owner_exists and owner_reason is None and owner_state.get("origin_writer") == "owner":
        owner_ts = str(owner_state.get("updated_at") or "")
        phone_ts = str(validated.get("updated_at") or "")
        if owner_ts > phone_ts or int(owner_state.get("revision") or 0) > int(
            validated.get("revision") or 0
        ):
            try:
                validated = _overlay_owner_domains_into_phone_state(validated, owner_state)
            except Exception:
                return default_stance_containment_state(), "STANCE_CONTAINMENT_STATE_CORRUPT"
    return validated, None


def write_stance_containment_state(
    root: Path | str, state: Mapping[str, Any]
) -> dict[str, Any]:
    validated = _validate_stored_state(state)
    return _atomic_write_json(stance_containment_state_path(root), validated)


# ---------------------------------------------------------------------------------------------
# Pure Read-Only Derivations & Projection
# ---------------------------------------------------------------------------------------------


def _derive_dish_temperature(dish: Mapping[str, Any], now_dt: datetime) -> str:
    init_temp = str(dish.get("initial_temperature") or "hot")
    if init_temp == "cooled":
        return "cooled"
    prep_dt = _parse_stored_iso(dish.get("prepared_at"))
    if prep_dt is None:
        return init_temp if init_temp in THERMAL_BANDS else "warm"
    elapsed_min = max(0.0, (now_dt - prep_dt).total_seconds() / 60.0)
    if init_temp == "hot":
        if elapsed_min < HOT_TO_WARM_MINUTES:
            return "hot"
        if elapsed_min < HOT_TO_COOLED_MINUTES:
            return "warm"
        return "cooled"
    # init_temp == "warm"
    if elapsed_min < WARM_TO_COOLED_MINUTES:
        return "warm"
    return "cooled"


def _project_stance_row(
    st: Mapping[str, Any],
    *,
    subject_view: bool,
    all_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    replaces_id = st.get("replaces_stance_id")
    superseded_by = st.get("superseded_by")
    if not subject_view:
        if replaces_id is not None:
            prior = all_by_id.get(str(replaces_id))
            if (
                prior is None
                or not prior.get("shared_with_partner")
                or prior.get("status") == "SOURCE_WITHDRAWN"
            ):
                replaces_id = None
        if superseded_by is not None:
            nxt = all_by_id.get(str(superseded_by))
            if (
                nxt is None
                or not nxt.get("shared_with_partner")
                or nxt.get("status") == "SOURCE_WITHDRAWN"
            ):
                superseded_by = None

    row: dict[str, Any] = {
        "stance_id": st["stance_id"],
        "root_stance_id": st["root_stance_id"],
        "lineage_depth": st["lineage_depth"],
        "scope_ref": st["scope_ref"],
        "source_revision": st["source_revision"],
        "page_or_anchor": st["page_or_anchor"],
        "facet": st["facet"],
        "status": st["status"],
        "text": st["text"],
        "text_version": st["text_version"],
        "shared_with_partner": bool(st["shared_with_partner"]),
        "replaces_stance_id": replaces_id,
        "has_prior_revision": st.get("replaces_stance_id") is not None,
        "superseded_by": superseded_by,
        "stale_due_to_revision": st.get("stale_due_to_revision"),
        "created_at": st.get("created_at"),
        "updated_at": st.get("updated_at"),
        "superseded_at": st.get("superseded_at"),
        "stale_marked_at": st.get("stale_marked_at"),
        "last_edit_kind": st.get("last_edit_kind"),
    }
    if subject_view:
        row["private_note"] = st.get("private_note")
    return row


def project_stance_containment(
    state: Mapping[str, Any],
    *,
    character_info: Mapping[str, str],
    now_dt: datetime,
    now_iso: str,
    subject_view: bool,
    state_reason: Optional[str] = None,
    root: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Pure zero-write projection of all 3 cohesive subdomains."""

    name = character_info["display_name"]
    pron = character_info["pronouns"]

    # -- 1. Stance Revisions Projection ---------------------------------------------------
    stance_dom = state["stance_domain"]
    raw_stances: list[dict[str, Any]] = list(stance_dom["stances"])
    source_reg: dict[str, dict[str, Any]] = dict(stance_dom["source_registry"])
    obtained_pages_map = owner_substrate._validate_obtained_pages_map(
        stance_dom.get("obtained_pages_by_scope")
    )
    active_ids_set = set(_compute_active_stance_ids(raw_stances, source_reg))
    by_id = {st["stance_id"]: st for st in raw_stances}

    current_stances: list[dict[str, Any]] = []
    lineage_history: list[dict[str, Any]] = []
    stale_stances: list[dict[str, Any]] = []
    withdrawn_scopes = sorted(
        scope for scope, info in source_reg.items() if info.get("withdrawn")
    )

    for st in raw_stances:
        status = st["status"]
        if status == "SOURCE_WITHDRAWN" or st["scope_ref"] in withdrawn_scopes:
            # Never emit withdrawn stances to current stances, partner view, or active history!
            continue
        if not subject_view and not st.get("shared_with_partner"):
            # Partner view strictly strips unshared stances and private notes.
            continue
        projected_row = _project_stance_row(st, subject_view=subject_view, all_by_id=by_id)
        lineage_history.append(projected_row)
        if st["stance_id"] in active_ids_set and status == "ACTIVE":
            current_stances.append(projected_row)
        elif status == "SOURCE_REVISION_STALE":
            stale_stances.append(projected_row)

    revision_chains = attn_proj.build_misunderstanding_revision_chain(
        raw_stances, include_unshared=bool(subject_view)
    )
    stance_block: dict[str, Any] = {
        "facets": list(STANCE_FACETS),
        "current_stances": current_stances,
        "current_count": len(current_stances),
        "stale_stances": stale_stances,
        "lineage_history": lineage_history,
        "misunderstanding_revisions": revision_chains,
        "withdrawn_scopes": withdrawn_scopes,
        "single_write_path_enforced": True,
        "typo_fix_preserves_lineage": True,
        "withdrawn_never_resurrects": True,
    }
    if subject_view:
        stance_block["obtained_pages_by_scope"] = copy.deepcopy(obtained_pages_map)
        stance_block["total_non_withdrawn_count"] = sum(
            1 for st in raw_stances if st["status"] != "SOURCE_WITHDRAWN"
        )
        stance_block["withdrawn_tombstone_count"] = sum(
            1 for st in raw_stances if st["status"] == "SOURCE_WITHDRAWN"
        )

    # -- 2. Object Containment & Spatial Impressions Projection ---------------------------
    cont_dom = state["containment_domain"]
    subj_place = cont_dom["subject_place_id"]
    subj_room = cont_dom["subject_room_id"]
    subj_present = bool(cont_dom["subject_present"])
    local_hand_slots = dict(cont_dom["hand_slots"])
    ext_hand_locks = (
        owner_substrate.read_cross_module_hand_locks(
            root, now_iso=now_iso, local_hand_slots=local_hand_slots
        )
        if root is not None
        else {}
    )
    hand_slots = dict(local_hand_slots)
    for slot, lock_val in ext_hand_locks.items():
        if hand_slots.get(slot) is None:
            hand_slots[slot] = lock_val

    containers_by_id = {c["container_id"]: c for c in cont_dom["containers"]}
    children_by_cid: dict[str, list[str]] = {cid: [] for cid in containers_by_id}
    child_containers_by_cid: dict[str, list[str]] = {cid: [] for cid in containers_by_id}
    for c in cont_dom["containers"]:
        pcid = c.get("parent_container_id")
        if pcid in child_containers_by_cid:
            child_containers_by_cid[pcid].append(c["container_id"])
    for it in cont_dom["items"]:
        if it["placement_kind"] == "container" and it["parent_container_id"] in children_by_cid:
            children_by_cid[it["parent_container_id"]].append(it["item_id"])

    projected_containers: list[dict[str, Any]] = []
    max_observed_depth = 1
    for c in cont_dom["containers"]:
        cid = c["container_id"]
        chain = owner_substrate.container_ancestor_chain(cid, containers_by_id)
        if len(chain) > max_observed_depth:
            max_observed_depth = len(chain)
        c_children = list(children_by_cid[cid])
        c_nested = list(child_containers_by_cid[cid])
        occupied = len(c_children) + len(c_nested)
        cap = int(c["capacity"])
        phys_place = owner_substrate.effective_container_place(cid, containers_by_id)
        last_seen = c["subject_last_seen_place"]
        is_stale = phys_place != last_seen
        # Epistemic masking: until she reaches the spot and refreshes the
        # impression, a stale container is projected back to where the subject
        # believes it is.  The physical (moved) place is never disclosed.
        believed_hidden = bool(subject_view and is_stale)
        shown_place = last_seen if believed_hidden else phys_place
        open_st = str(c.get("open_state") or "open_top")
        gate_block = owner_substrate.check_container_access_gate(
            cid, containers_by_id, include_self=True
        )
        reachable = bool(
            subj_present and shown_place == subj_place and gate_block is None
        )

        if c.get("drinkware"):
            derived_use = (
                "empty_drinkable"
                if occupied == 0 and open_st in ("open_top", "open")
                else "holding_items"
            )
            can_pour_drink = occupied == 0 and open_st in ("open_top", "open")
        else:
            derived_use = "empty_container" if occupied == 0 else "holding_items"
            can_pour_drink = False

        c_row: dict[str, Any] = {
            "container_id": cid,
            "label": c["label"],
            "place_id": shown_place,
            "effective_place_id": shown_place,
            "room_id": _room_for_place(shown_place),
            "parent_container_id": c.get("parent_container_id"),
            "open_state": open_st,
            "capacity": cap,
            "occupied_slots": occupied,
            "remaining_capacity": cap - occupied,
            "is_full": occupied >= cap,
            "accepted_classes": list(c["accepted_classes"]),
            "child_item_ids": c_children,
            "child_container_ids": c_nested,
            "derived_use": derived_use,
            "can_pour_drink_now": can_pour_drink,
            "reachable_now": reachable,
            "access_blocked_reason": gate_block,
            "subject_last_seen_place": last_seen,
            "subject_last_seen_at": c.get("subject_last_seen_at"),
            "not_at_last_seen_place": bool(c.get("not_at_last_seen_place", False)),
        }
        if subject_view:
            # ``impression_stale`` is a God-view fact (physical place != believed
            # place); it is withheld from the subject and kept only for the
            # partner projection.
            c_row["private_impression_note"] = c.get("private_impression_note")
        else:
            c_row["impression_stale"] = is_stale
        projected_containers.append(c_row)

    projected_items: list[dict[str, Any]] = []
    subject_cognitive_cues: list[str] = []
    for it in cont_dom["items"]:
        pkind = it["placement_kind"]
        gate_block_it: Optional[str] = None
        if pkind == "container" and it["parent_container_id"] in containers_by_id:
            phys_place = owner_substrate.effective_container_place(
                it["parent_container_id"], containers_by_id
            )
            gate_block_it = owner_substrate.check_container_access_gate(
                it["parent_container_id"], containers_by_id, include_self=True
            )
        elif pkind == "hand":
            phys_place = subj_place
        else:
            phys_place = it["place_id"]

        last_seen = it["subject_last_seen_place"]
        is_stale = phys_place != last_seen
        # Same epistemic masking as containers: pre-discovery the subject sees
        # the item where she last remembers it, never the moved ground truth.
        believed_hidden = bool(subject_view and is_stale)
        shown_place = last_seen if believed_hidden else phys_place
        reachable = bool(
            pkind == "hand"
            or (subj_present and shown_place == subj_place and gate_block_it is None)
        )

        i_row: dict[str, Any] = {
            "item_id": it["item_id"],
            "label": it["label"],
            "item_class": it["item_class"],
            "owner": it["owner"],
            "placement_kind": pkind,
            "place_id": shown_place,
            "effective_place_id": shown_place,
            "room_id": _room_for_place(shown_place),
            "parent_container_id": it["parent_container_id"],
            "held_in_hand": it["held_in_hand"],
            "reachable_now": reachable,
            "access_blocked_reason": gate_block_it,
            "subject_last_seen_place": last_seen,
            "subject_last_seen_at": it.get("subject_last_seen_at"),
            "not_at_last_seen_place": bool(it.get("not_at_last_seen_place", False)),
        }
        if subject_view:
            i_row["private_impression_note"] = it.get("private_impression_note")
            if pkind == "hand":
                subject_cognitive_cues.append(
                    f"{name}当前在 {it['held_in_hand']} 拿着「{it['label']}」。"
                )
            elif it.get("not_at_last_seen_place"):
                subject_cognitive_cues.append(
                    f"{name}记得「{it['label']}」原本在 {last_seen}，但刚才看过去没找到（未知新位置）。"
                )
            else:
                subject_cognitive_cues.append(
                    f"{name}印象里「{it['label']}」在 {last_seen}。"
                )
        else:
            i_row["impression_stale"] = is_stale
        projected_items.append(i_row)

    cohabitation_proj = copy.deepcopy(
        cont_dom.get("cohabitation") or owner_substrate._default_cohabitation()
    )
    containment_block: dict[str, Any] = {
        "subject_location": {
            "place_id": subj_place,
            "room_id": subj_room,
            "present": subj_present,
        },
        "hand_slots": hand_slots,
        "local_hand_slots": local_hand_slots,
        "external_hand_locks": ext_hand_locks,
        "containers": projected_containers,
        "items": projected_items,
        "cohabitation": cohabitation_proj,
        # Subject view only counts misses she has actually witnessed; the raw
        # stale-impression count would itself leak that something moved unseen.
        "stale_impression_count": sum(
            1
            for row in (projected_containers + projected_items)
            if (
                bool(row.get("not_at_last_seen_place"))
                if subject_view
                else bool(row.get("impression_stale"))
            )
        ),
        "no_fake_rollback": True,
        "no_background_omniscience": True,
        "max_containment_depth": max(max_observed_depth, 1),
    }
    if subject_view:
        containment_block["subject_cognitive_cues"] = subject_cognitive_cues

    # -- 3. Shared Desk Attached Co-Presence & Table Consumables --------------------------
    dt_dom = state["desk_table_domain"]
    pa = dt_dom["primary_activity"]
    cp = dt_dom["copresence"]

    effective_cp_mode = cp["mode"]
    expired_by_clock = False
    end_dt = _parse_stored_iso(cp.get("expected_end_at"))
    if effective_cp_mode != "off" and end_dt is not None and now_dt >= end_dt:
        effective_cp_mode = "off"
        expired_by_clock = True

    pa_proj: dict[str, Any] = {
        "activity_id": pa["activity_id"],
        "label": pa["label"],
        "status": pa["status"],
        "interruptibility": pa["interruptibility"],
        "updated_at": pa.get("updated_at"),
    }
    if subject_view:
        pa_proj["private_note"] = pa.get("private_note")

    mode_descriptions = {
        "off": f"{name}与伴侣当前未开启书桌共在括号，{pron}按自己的节奏处理手头事务。",
        "quiet_parallel": f"{name}与伴侣正在书桌前安静并行陪伴，各自忙手头的事，不必没话找话。",
        "focused_do_not_disturb": f"{name}挂上了专注勿扰木牌，专心推进手头事务，仅紧急事项打断。",
        "break_chat": f"{name}与伴侣在桌边歇口气轻松闲聊，手头主活动保留安全恢复点而不被销毁。",
    }

    copresence_proj: dict[str, Any] = {
        "modes": list(COPRESENCE_MODES),
        "mode": effective_cp_mode,
        "active": effective_cp_mode != "off",
        "expired_by_clock": expired_by_clock,
        "session_id": cp.get("session_id") if effective_cp_mode != "off" else None,
        "attached_to_activity_id": (
            cp.get("attached_to_activity_id") if effective_cp_mode != "off" else None
        ),
        "started_at": cp.get("started_at") if effective_cp_mode != "off" else None,
        "expected_end_at": cp.get("expected_end_at") if effective_cp_mode != "off" else None,
        "declared_minutes": cp.get("declared_minutes") if effective_cp_mode != "off" else None,
        "initiated_by": cp.get("initiated_by"),
        "last_changed_by": cp.get("last_changed_by"),
        "last_exit_by": cp.get("last_exit_by"),
        "last_exit_at": cp.get("last_exit_at"),
        "note": cp.get("note"),
        "pending_proposal": copy.deepcopy(cp.get("pending_proposal")),
        "status_line": mode_descriptions[effective_cp_mode],
        "hijacks_primary_activity": False,
        "creates_second_foreground": False,
        "exit_anytime": True,
        "zero_exit_debt": True,
        "zero_silence_nudge": True,
        "no_hidden_affection_score": True,
    }

    projected_dishes: list[dict[str, Any]] = []
    for d in dt_dom["table_dishes"]:
        temp_band = _derive_dish_temperature(d, now_dt)
        d_row: dict[str, Any] = {
            "dish_id": d["dish_id"],
            "title": d["title"],
            "kind": d["kind"],
            "place_id": d["place_id"],
            "prepared_by": d["prepared_by"],
            "prepared_at": d.get("prepared_at"),
            "initial_temperature": d["initial_temperature"],
            "temperature_band": temp_band,
            "initial_portions": d["initial_portions"],
            "portions_remaining": d["portions_remaining"],
            "consumed_by_subject": d["consumed_by_subject"],
            "consumed_by_partner": d["consumed_by_partner"],
            "discarded_portions": d["discarded_portions"],
            "status": "EMPTY" if d["portions_remaining"] == 0 else "AVAILABLE",
            "is_empty_plate": d["portions_remaining"] == 0,
            "empty_at": d.get("empty_at"),
            "note": d.get("note"),
            "no_hidden_affection_score": True,
        }
        if subject_view:
            d_row["private_note"] = d.get("private_note")
        projected_dishes.append(d_row)

    desk_table_block: dict[str, Any] = {
        "primary_activity": pa_proj,
        "copresence": copresence_proj,
        "table_dishes": projected_dishes,
        "active_dish_count": sum(
            1 for d in projected_dishes if d["portions_remaining"] > 0
        ),
        "empty_plate_count": sum(
            1 for d in projected_dishes if d["portions_remaining"] == 0
        ),
        "hijacks_primary_activity": False,
        "portion_integer_conservation": True,
        "no_infinite_refill": True,
        "no_hidden_affection_score": True,
    }

    out_doc: dict[str, Any] = {
        "schema": STANCE_CONTAINMENT_SCHEMA,
        "read_only": True,
        "view": "subject" if subject_view else "partner_default",
        "subject_view": bool(subject_view),
        "observed_at": now_iso,
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "state_status": "available" if state_reason is None else "degraded",
        "state_reason_code": state_reason,
        "character": {
            "pack_id": character_info["pack_id"],
            "display_name": name,
            "pronouns": pron,
        },
        "stance_revisions": stance_block,
        "object_containment": containment_block,
        "cohabitation": cohabitation_proj,
        "shared_desk_table": desk_table_block,
        "boundaries": {
            "locked_to_chiyo": False,
            "open_source_character_neutral": True,
            "zero_llm_calls": True,
            "zero_rng_deterministic": True,
            "get_is_zero_write": True,
            "stance_revision_never_overwrites_old": True,
            "withdrawn_source_never_resurrects": True,
            "partial_containment_never_fake_rollbacks": True,
            "partner_move_never_grants_subject_omniscience": True,
            "copresence_is_attached_bracket": True,
            "hijacks_primary_activity": False,
            "portion_integer_conservation": True,
            "no_hidden_affection_score": True,
        },
        "private_text_included": bool(subject_view),
        "local_only": True,
    }
    if subject_view:
        merged_materials: dict[str, dict[str, Any]] = {}
        if root is not None:
            discovered = owner_substrate.discover_reading_materials(root)
            obtained_idx = attn_proj.discover_obtained_pages_index(
                root, extra_obtained_by_scope=obtained_pages_map
            )
            for sref, minfo in discovered.items():
                merged_materials[sref] = {
                    **minfo,
                    "obtained_pages": obtained_idx.get(sref, {}).get("obtained_pages", []),
                }
            for sref, oinfo in obtained_idx.items():
                if sref not in merged_materials:
                    merged_materials[sref] = dict(oinfo)
        else:
            for sref, oinfo in obtained_pages_map.items():
                merged_materials[sref] = dict(oinfo)
        out_doc["attention_view"] = attn_proj.build_attention_view(
            {
                "subject_ref": f"character:{character_info.get('pack_id') or 'ta'}",
                "stance_revisions": stance_block,
                "object_containment": containment_block,
                "reading_materials": merged_materials,
            },
            now=now_iso,
        )
    return out_doc


# ---------------------------------------------------------------------------------------------
# Public Entrypoint 1: Pure Zero-Write Read
# ---------------------------------------------------------------------------------------------


def read_stance_containment(
    root: Path | str,
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Read ``phone.stance.containment.v1`` with 100% zero disk writes."""

    now_dt, now_iso = _normalize_now(now)
    char_info = _read_character_identity(root)
    state, state_reason = read_stance_containment_state(root)
    return project_stance_containment(
        state,
        character_info=char_info,
        now_dt=now_dt,
        now_iso=now_iso,
        subject_view=bool(subject_view),
        state_reason=state_reason,
        root=root,
    )


# ---------------------------------------------------------------------------------------------
# Public Entrypoint 2: Explicit Deterministic Mutation
# ---------------------------------------------------------------------------------------------


def _lookup_receipt(
    receipts: list[dict[str, Any]],
    *,
    operation_key: str,
    action: str,
    fingerprint: str,
) -> Optional[dict[str, Any]]:
    for rec in receipts:
        if not isinstance(rec, Mapping):
            continue
        if rec.get("operation_key") == operation_key:
            if rec.get("action") != action or rec.get("fingerprint") != fingerprint:
                raise StanceContainmentConflictError("OPERATION_KEY_CONFLICT")
            return dict(rec)
    return None


def _lookup_ring_receipt(
    ring: Any,
    *,
    operation_key: str,
    action: str,
    fingerprint: str,
) -> Optional[dict[str, Any]]:
    """Match a receipt already evicted from the active ledger into the bounded ring."""

    if not isinstance(ring, list):
        return None
    for rec in reversed(ring):
        if not isinstance(rec, Mapping):
            continue
        if rec.get("operation_key") != operation_key:
            continue
        if rec.get("action") != action or rec.get("fingerprint") != fingerprint:
            raise StanceContainmentConflictError("OPERATION_KEY_CONFLICT")
        return dict(rec)
    return None


def _next_op_seq(state: Mapping[str, Any]) -> int:
    """Monotonic commit sequence; legacy rows without ``op_seq`` simply contribute nothing."""

    highest = 0
    raw = state.get("next_op_seq")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > highest:
        highest = raw
    for field in ("operation_receipts", "committed_op_ring"):
        rows = state.get(field)
        if not isinstance(rows, list):
            continue
        for rec in rows:
            if not isinstance(rec, Mapping):
                continue
            seq = rec.get("op_seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq > highest:
                highest = seq
    return highest + 1


def _record_receipt(
    state: dict[str, Any],
    *,
    operation_key: Optional[str],
    action: str,
    fingerprint: str,
    now_iso: str,
    scope_ref: Optional[str] = None,
    target_id: Optional[str] = None,
) -> None:
    """Append a receipt in commit order; evict the lowest ``op_seq`` into the bounded ring."""

    if operation_key is None:
        return
    receipts = state.setdefault("operation_receipts", [])
    ring = state.setdefault("committed_op_ring", [])
    if not isinstance(ring, list):
        ring = []
        state["committed_op_ring"] = ring

    op_seq = _next_op_seq(state)
    state["next_op_seq"] = op_seq
    receipts.append({
        "operation_key": operation_key,
        "action": action,
        "fingerprint": fingerprint,
        "scope_ref": scope_ref,
        "target_id": target_id,
        "recorded_at": now_iso,
        "op_seq": op_seq,
        "revision": int(state.get("revision") or 0),
    })

    while len(receipts) > MAX_OPERATION_RECEIPTS:
        oldest = min(
            receipts,
            key=lambda rec: (
                int(rec.get("op_seq") or 0) if isinstance(rec, Mapping) else -1
            ),
        )
        receipts.remove(oldest)
        if not isinstance(oldest, Mapping):
            continue
        ring.append({
            "operation_key": oldest.get("operation_key"),
            "action": oldest.get("action"),
            "fingerprint": oldest.get("fingerprint"),
            "scope_ref": oldest.get("scope_ref"),
            "target_id": oldest.get("target_id"),
            "recorded_at": oldest.get("recorded_at"),
            "op_seq": oldest.get("op_seq"),
            "revision": oldest.get("revision"),
        })
        evicted_rev = oldest.get("revision")
        if isinstance(evicted_rev, int) and not isinstance(evicted_rev, bool):
            current = state.get("evicted_up_to_revision")
            base = current if isinstance(current, int) and not isinstance(current, bool) else 0
            state["evicted_up_to_revision"] = max(base, evicted_rev)

    while len(ring) > MAX_COMMITTED_OP_RING:
        ring.pop(0)


def _resolve_actor(
    payload: Mapping[str, Any],
    *,
    key: str,
    subject_view: bool,
) -> str:
    if key not in payload:
        return "subject" if subject_view else "partner"
    actor = payload.get(key)
    if not isinstance(actor, str) or actor not in PARTICIPANT_ACTORS:
        raise StanceContainmentValidationError(f"{key.upper()}_INVALID")
    if actor == "subject" and not subject_view:
        raise StanceContainmentPermissionError("SUBJECT_CAPABILITY_REQUIRED")
    return actor


def apply_stance_containment_action(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Validate and apply one explicit action under one exclusive write lock.

    The whole read-check-mutate-write cycle runs inside ``state_file_lock`` so two
    writers can never interleave: the state is re-read from disk *after* the lock
    is held and ``expected_revision`` / idempotency receipts are checked against
    that fresh copy, never against a pre-lock snapshot.
    """

    if not isinstance(payload, Mapping):
        raise StanceContainmentValidationError("PAYLOAD_NOT_OBJECT")
    with _tx.state_file_lock(stance_containment_state_path(root)):
        return _apply_stance_containment_action_locked(
            root, payload, now=now, subject_view=subject_view
        )


def _apply_stance_containment_action_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Inner write body; callers must already hold ``state_file_lock``."""

    if not isinstance(payload, Mapping):
        raise StanceContainmentValidationError("PAYLOAD_NOT_OBJECT")

    now_dt, now_iso = _normalize_now(now)

    # 1. Foreign / spoofed field check before anything else
    for raw_k in payload.keys():
        if not isinstance(raw_k, str) or not raw_k.strip():
            raise StanceContainmentValidationError("EMPTY_FIELD_NAME")
        if raw_k in FORBIDDEN_FOREIGN_KEYS:
            raise StanceContainmentValidationError(f"UNKNOWN_FIELD:{raw_k}")

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        raise StanceContainmentValidationError("ACTION_REQUIRED")
    if raw_action != raw_action.strip() or "\x00" in raw_action:
        raise StanceContainmentValidationError("ACTION_INVALID")
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        raise StanceContainmentValidationError(f"ACTION_INVALID:{action}")

    allowed_fields = ACTION_ALLOWED_FIELDS[action]
    extra_fields = sorted(set(payload.keys()) - set(allowed_fields))
    if extra_fields:
        raise StanceContainmentValidationError(f"UNKNOWN_FIELD:{extra_fields[0]}")

    # Reject empty strings or None smuggled in optional/supplied parameters
    for k, v in payload.items():
        if k == "action":
            continue
        if k == "parent_container_id" and v is None and action in ("register_container", "nest_container"):
            continue
        if v is None:
            raise StanceContainmentValidationError(f"NULL_FIELD_NOT_ALLOWED:{k}")
        if isinstance(v, str) and not v.strip():
            raise StanceContainmentValidationError(f"EMPTY_STRING_FIELD:{k}")

    # 2. Subject capability enforcement
    if action in STANCE_CONTAINMENT_SUBJECT_ACTIONS and not subject_view:
        if not (action == "observe_current_room" and payload.get("actor") == "partner"):
            raise StanceContainmentPermissionError("SUBJECT_CAPABILITY_REQUIRED")

    # 3. Fail-closed load of existing state
    current_state, state_reason = read_stance_containment_state(root)
    if state_reason is not None:
        raise StanceContainmentConflictError(state_reason)

    state = copy.deepcopy(current_state)
    char_info = _read_character_identity(root)

    expected_rev = _optional_expected_revision(payload)
    op_key = _optional_single_line(
        payload,
        "operation_key",
        max_len=MAX_OPERATION_KEY,
        reason_code="OPERATION_KEY_INVALID",
    )
    intent_fields = {
        k: v for k, v in payload.items() if k not in ("action", "expected_revision")
    }
    fp = _fingerprint(action, intent_fields)

    if op_key is not None:
        existing_rec = _lookup_receipt(
            state["operation_receipts"],
            operation_key=op_key,
            action=action,
            fingerprint=fp,
        )
        if existing_rec is None:
            # The active ledger is bounded; an older key may still be safe to
            # replay from the commit ring instead of being applied a second time.
            existing_rec = _lookup_ring_receipt(
                state.get("committed_op_ring"),
                operation_key=op_key,
                action=action,
                fingerprint=fp,
            )
        if existing_rec is not None:
            rec_scope = existing_rec.get("scope_ref")
            if (
                action != "revoke_stance_source"
                and rec_scope
                and state["stance_domain"]["source_registry"]
                .get(rec_scope, {})
                .get("withdrawn")
            ):
                raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
            proj = project_stance_containment(
                state,
                character_info=char_info,
                now_dt=now_dt,
                now_iso=now_iso,
                subject_view=bool(subject_view),
                root=root,
            )
            out_replay = dict(proj)
            out_replay.update({
                "ok": True,
                "status": "already_applied",
                "action": action,
                "changed": False,
                "replayed": True,
                "reason_code": None,
                "target_id": existing_rec.get("target_id"),
            })
            return out_replay

        # Neither active nor ring holds the key.  If the caller's expected
        # revision is already below the eviction watermark it is a stale retry
        # from before the window, not a brand-new request: refuse explicitly.
        evicted_up_to = state.get("evicted_up_to_revision")
        current_rev = int(state["revision"])
        if (
            expected_rev is not None
            and isinstance(evicted_up_to, int)
            and not isinstance(evicted_up_to, bool)
            and evicted_up_to > 0
            and expected_rev <= evicted_up_to < current_rev
        ):
            raise StanceContainmentConflictError("RETRY_WINDOW_EXPIRED")

    if expected_rev is not None and int(state["revision"]) != expected_rev:
        raise StanceContainmentConflictError("REVISION_CONFLICT")

    def _commit(
        extra: Mapping[str, Any],
        *,
        status_label: str = "applied",
        ok_flag: bool = True,
        reason_code: Optional[str] = None,
        receipt_scope: Optional[str] = None,
        receipt_target: Optional[str] = None,
    ) -> dict[str, Any]:
        state["stance_domain"]["active_stance_ids"] = _compute_active_stance_ids(
            state["stance_domain"]["stances"],
            state["stance_domain"]["source_registry"],
        )
        state["revision"] = int(state["revision"]) + 1
        state["updated_at"] = now_iso
        _record_receipt(
            state,
            operation_key=op_key,
            action=action,
            fingerprint=fp,
            now_iso=now_iso,
            scope_ref=receipt_scope,
            target_id=receipt_target,
        )
        written = write_stance_containment_state(root, state)
        if (
            action in OWNER_SYNCED_ACTIONS
            or owner_substrate.attention_stance_containment_state_path(root).exists()
        ):
            try:
                owner_substrate.sync_owner_domains_from_phone_state(
                    root, written, now_iso=now_iso
                )
            except owner_substrate.LifeStanceContainmentError as exc:
                raise StanceContainmentConflictError(exc.reason_code) from exc
        owner_substrate.sync_embodied_social_hand_slots_if_present(
            root,
            hand_slots=written["containment_domain"]["hand_slots"],
            items=written["containment_domain"]["items"],
            now_iso=now_iso,
        )
        projected = project_stance_containment(
            written,
            character_info=char_info,
            now_dt=now_dt,
            now_iso=now_iso,
            subject_view=bool(subject_view),
            root=root,
        )
        result = dict(projected)
        result["ok"] = ok_flag
        result["status"] = status_label
        result["action"] = action
        result["changed"] = True
        result["replayed"] = False
        result["reason_code"] = reason_code
        result["action_receipt"] = {
            "action": action,
            "scope_ref": receipt_scope,
            "target_id": receipt_target,
            "at": now_iso,
        }
        result.update(dict(extra))
        return result

    stance_dom = state["stance_domain"]
    stances: list[dict[str, Any]] = stance_dom["stances"]
    src_reg: dict[str, dict[str, Any]] = stance_dom["source_registry"]
    obtained_pages_by_scope: dict[str, dict[str, Any]] = stance_dom.setdefault(
        "obtained_pages_by_scope", {}
    )

    cont_dom = state["containment_domain"]
    containers: list[dict[str, Any]] = cont_dom["containers"]
    items: list[dict[str, Any]] = cont_dom["items"]
    hand_slots: dict[str, Optional[str]] = cont_dom["hand_slots"]
    cohabitation: dict[str, Any] = cont_dom.setdefault(
        "cohabitation", owner_substrate._default_cohabitation()
    )

    dt_dom = state["desk_table_domain"]

    # =========================================================================================
    # SUBDOMAIN 1: STANCE REVISIONS (Doc 30 + Review P1 #6 + Unread Page Gate)
    # =========================================================================================

    if action == "record_obtained_pages":
        scope_ref = _require_id(payload.get("scope_ref"), reason_code="SCOPE_REF_INVALID")
        if src_reg.get(scope_ref, {}).get("withdrawn"):
            raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        prev_info = obtained_pages_by_scope.get(scope_ref) or {}
        pages_set: set[int] = set(prev_info.get("obtained_pages") or [])
        if "obtained_pages" in payload:
            raw_pages = payload.get("obtained_pages")
            if not isinstance(raw_pages, list):
                raise StanceContainmentValidationError("OBTAINED_PAGES_INVALID")
            for p in raw_pages:
                p_int = _require_int_range(
                    p, minimum=1, maximum=100000, reason_code="OBTAINED_PAGES_INVALID"
                )
                pages_set.add(p_int)
        if "max_obtained_page" in payload:
            max_p = _require_int_range(
                payload.get("max_obtained_page"),
                minimum=0,
                maximum=10000,
                reason_code="MAX_OBTAINED_PAGE_INVALID",
            )
            for p in range(1, max_p + 1):
                pages_set.add(p)
        total_p = prev_info.get("total_pages")
        if "total_pages" in payload:
            total_p = _require_int_range(
                payload.get("total_pages"),
                minimum=1,
                maximum=100000,
                reason_code="TOTAL_PAGES_INVALID",
            )
        obtained_pages_by_scope[scope_ref] = {
            "scope_ref": scope_ref,
            "obtained_pages": sorted(pages_set),
            "total_pages": total_p,
            "enforce_page_gate": True,
            "updated_at": now_iso,
        }
        return _commit(
            {
                "scope_ref": scope_ref,
                "obtained_pages": sorted(pages_set),
                "total_pages": total_p,
            },
            receipt_scope=scope_ref,
            receipt_target=scope_ref,
        )

    if action == "propose_stance":
        scope_ref = _require_id(payload.get("scope_ref"), reason_code="SCOPE_REF_INVALID")
        probed = owner_substrate.probe_reading_material_revision(root, scope_ref)
        if probed is not None and probed.get("withdrawn"):
            raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if "source_revision" in payload:
            source_revision = _require_id(
                payload.get("source_revision"), reason_code="SOURCE_REVISION_INVALID"
            )
        elif probed is not None:
            source_revision = str(probed["source_revision"])
        else:
            raise StanceContainmentValidationError("SOURCE_REVISION_INVALID")
        page_or_anchor = _require_anchor(payload.get("page_or_anchor"))
        facet = payload.get("facet")
        if not isinstance(facet, str) or facet not in STANCE_FACETS:
            raise StanceContainmentValidationError("STANCE_FACET_INVALID")
        text = _clean_multiline(
            payload.get("text"),
            min_len=1,
            max_len=MAX_STANCE_TEXT,
            reason_code="STANCE_TEXT_INVALID",
        )
        shared = (
            _require_bool(
                payload.get("shared_with_partner"),
                reason_code="SHARED_WITH_PARTNER_INVALID",
            )
            if "shared_with_partner" in payload
            else False
        )
        private_note = _optional_multiline(
            payload,
            "private_note",
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )

        reg_entry = src_reg.get(scope_ref)
        if reg_entry is not None:
            if reg_entry.get("withdrawn"):
                raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
            if reg_entry["current_source_revision"] != source_revision:
                raise StanceContainmentConflictError("STANCE_SOURCE_REVISION_MISMATCH")
        elif probed is not None and probed["source_revision"] != source_revision:
            raise StanceContainmentConflictError("STANCE_SOURCE_REVISION_MISMATCH")

        page_ok, page_reason, page_detail = attn_proj.verify_page_obtained(
            root,
            scope_ref=scope_ref,
            page_or_anchor=page_or_anchor,
            extra_obtained_by_scope=obtained_pages_by_scope,
        )
        if not page_ok:
            raise StanceContainmentConflictError(
                page_reason or "VIEW_PAGE_NOT_OBTAINED", details=page_detail
            )

        for existing in stances:
            if (
                existing["status"] == "ACTIVE"
                and existing["scope_ref"] == scope_ref
                and existing["source_revision"] == source_revision
                and existing["page_or_anchor"] == page_or_anchor
                and existing["facet"] == facet
            ):
                raise StanceContainmentConflictError("ACTIVE_STANCE_EXISTS_USE_REVISE")

        if len(stances) >= MAX_STANCES:
            raise StanceContainmentConflictError("STANCE_LIMIT_REACHED")

        if reg_entry is None:
            src_reg[scope_ref] = {
                "scope_ref": scope_ref,
                "current_source_revision": source_revision,
                "withdrawn": False,
                "updated_at": now_iso,
                "withdrawn_at": None,
                "note": None,
                "withdraw_reason": None,
            }

        stance_id = _stable_id(
            "stance",
            scope_ref,
            source_revision,
            page_or_anchor,
            facet,
            len(stances),
            now_iso,
        )
        new_stance: dict[str, Any] = {
            "stance_id": stance_id,
            "root_stance_id": stance_id,
            "lineage_depth": 0,
            "scope_ref": scope_ref,
            "source_revision": source_revision,
            "page_or_anchor": page_or_anchor,
            "facet": facet,
            "status": "ACTIVE",
            "text": text,
            "text_version": 1,
            "shared_with_partner": shared,
            "private_note": private_note,
            "replaces_stance_id": None,
            "superseded_by": None,
            "stale_due_to_revision": None,
            "content_purged": False,
            "created_at": now_iso,
            "updated_at": now_iso,
            "superseded_at": None,
            "stale_marked_at": None,
            "withdrawn_at": None,
            "last_edit_kind": "propose",
        }
        stances.append(new_stance)
        by_id = {s["stance_id"]: s for s in stances}
        return _commit(
            {
                "stance": _project_stance_row(
                    new_stance, subject_view=bool(subject_view), all_by_id=by_id
                )
            },
            receipt_scope=scope_ref,
            receipt_target=stance_id,
        )

    if action == "fix_stance_typo":
        stance_id = _require_id(payload.get("stance_id"), reason_code="STANCE_ID_INVALID")
        expected_version = _require_int_range(
            payload.get("expected_version"),
            minimum=1,
            maximum=100000,
            reason_code="EXPECTED_VERSION_INVALID",
        )
        text = _clean_multiline(
            payload.get("text"),
            min_len=1,
            max_len=MAX_STANCE_TEXT,
            reason_code="STANCE_TEXT_INVALID",
        )
        private_note = _optional_multiline(
            payload,
            "private_note",
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )

        target_st = next((s for s in stances if s["stance_id"] == stance_id), None)
        if target_st is None:
            raise StanceContainmentNotFoundError("STANCE_NOT_FOUND")
        if (
            target_st["status"] == "SOURCE_WITHDRAWN"
            or src_reg.get(target_st["scope_ref"], {}).get("withdrawn")
        ):
            raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if target_st["status"] == "SUPERSEDED":
            raise StanceContainmentConflictError("STANCE_ALREADY_SUPERSEDED")
        if target_st["status"] == "SOURCE_REVISION_STALE":
            raise StanceContainmentConflictError("STANCE_SOURCE_STALE")
        if int(target_st["text_version"]) != expected_version:
            raise StanceContainmentConflictError("STANCE_VERSION_CONFLICT")

        target_st["text"] = text
        target_st["text_version"] = int(target_st["text_version"]) + 1
        target_st["updated_at"] = now_iso
        target_st["last_edit_kind"] = "typo_fix"
        if private_note is not None:
            target_st["private_note"] = private_note

        by_id = {s["stance_id"]: s for s in stances}
        return _commit(
            {
                "stance": _project_stance_row(
                    target_st, subject_view=bool(subject_view), all_by_id=by_id
                )
            },
            receipt_scope=target_st["scope_ref"],
            receipt_target=stance_id,
        )

    if action == "revise_stance":
        old_id = _require_id(
            payload.get("replaces_stance_id"), reason_code="REPLACES_STANCE_ID_INVALID"
        )
        text = _clean_multiline(
            payload.get("text"),
            min_len=1,
            max_len=MAX_STANCE_TEXT,
            reason_code="STANCE_TEXT_INVALID",
        )
        old_st = next((s for s in stances if s["stance_id"] == old_id), None)
        if old_st is None:
            raise StanceContainmentNotFoundError("STANCE_NOT_FOUND")

        scope_ref = old_st["scope_ref"]
        if (
            old_st["status"] == "SOURCE_WITHDRAWN"
            or src_reg.get(scope_ref, {}).get("withdrawn")
        ):
            raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if old_st["status"] == "SUPERSEDED" or old_st["superseded_by"] is not None:
            raise StanceContainmentConflictError("STANCE_ALREADY_SUPERSEDED")

        if "scope_ref" in payload:
            req_scope = _require_id(
                payload.get("scope_ref"), reason_code="SCOPE_REF_INVALID"
            )
            if req_scope != scope_ref:
                raise StanceContainmentConflictError("STANCE_SCOPE_MISMATCH")

        if "facet" in payload:
            req_facet = payload.get("facet")
            if not isinstance(req_facet, str) or req_facet not in STANCE_FACETS:
                raise StanceContainmentValidationError("STANCE_FACET_INVALID")
            if req_facet != old_st["facet"]:
                raise StanceContainmentConflictError("STANCE_FACET_MISMATCH")

        if "expected_version" in payload:
            exp_ver = _require_int_range(
                payload.get("expected_version"),
                minimum=1,
                maximum=100000,
                reason_code="EXPECTED_VERSION_INVALID",
            )
            if int(old_st["text_version"]) != exp_ver:
                raise StanceContainmentConflictError("STANCE_VERSION_CONFLICT")

        probed = owner_substrate.probe_reading_material_revision(root, scope_ref)
        if probed is not None and probed.get("withdrawn"):
            raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        current_src_rev = (
            probed["source_revision"]
            if probed is not None
            else src_reg.get(scope_ref, {}).get(
                "current_source_revision", old_st["source_revision"]
            )
        )
        new_src_rev = (
            _require_id(
                payload.get("source_revision"), reason_code="SOURCE_REVISION_INVALID"
            )
            if "source_revision" in payload
            else current_src_rev
        )
        if new_src_rev != current_src_rev:
            raise StanceContainmentConflictError("STANCE_SOURCE_REVISION_MISMATCH")
        if scope_ref in src_reg:
            src_reg[scope_ref]["current_source_revision"] = current_src_rev

        new_anchor = (
            _require_anchor(payload.get("page_or_anchor"))
            if "page_or_anchor" in payload
            else old_st["page_or_anchor"]
        )
        page_ok, page_reason, page_detail = attn_proj.verify_page_obtained(
            root,
            scope_ref=scope_ref,
            page_or_anchor=new_anchor,
            extra_obtained_by_scope=obtained_pages_by_scope,
        )
        if not page_ok:
            raise StanceContainmentConflictError(
                page_reason or "VIEW_PAGE_NOT_OBTAINED", details=page_detail
            )

        shared = (
            _require_bool(
                payload.get("shared_with_partner"),
                reason_code="SHARED_WITH_PARTNER_INVALID",
            )
            if "shared_with_partner" in payload
            else False
        )
        private_note = _optional_multiline(
            payload,
            "private_note",
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )

        if len(stances) >= MAX_STANCES:
            raise StanceContainmentConflictError("STANCE_LIMIT_REACHED")

        new_id = _stable_id(
            "stance",
            scope_ref,
            new_src_rev,
            new_anchor,
            old_st["facet"],
            old_id,
            len(stances),
            now_iso,
        )
        # Bidirectional lineage link without mutating old_st["text"]!
        old_st["status"] = "SUPERSEDED"
        old_st["superseded_by"] = new_id
        old_st["superseded_at"] = now_iso

        revised_stance: dict[str, Any] = {
            "stance_id": new_id,
            "root_stance_id": old_st["root_stance_id"],
            "lineage_depth": int(old_st["lineage_depth"]) + 1,
            "scope_ref": scope_ref,
            "source_revision": new_src_rev,
            "page_or_anchor": new_anchor,
            "facet": old_st["facet"],
            "status": "ACTIVE",
            "text": text,
            "text_version": 1,
            "shared_with_partner": shared,
            "private_note": private_note,
            "replaces_stance_id": old_id,
            "superseded_by": None,
            "stale_due_to_revision": None,
            "content_purged": False,
            "created_at": now_iso,
            "updated_at": now_iso,
            "superseded_at": None,
            "stale_marked_at": None,
            "withdrawn_at": None,
            "last_edit_kind": "revise",
        }
        stances.append(revised_stance)
        by_id = {s["stance_id"]: s for s in stances}
        return _commit(
            {
                "stance": _project_stance_row(
                    revised_stance, subject_view=bool(subject_view), all_by_id=by_id
                ),
                "superseded_stance_id": old_id,
            },
            receipt_scope=scope_ref,
            receipt_target=new_id,
        )

    if action == "set_stance_sharing":
        stance_id = _require_id(payload.get("stance_id"), reason_code="STANCE_ID_INVALID")
        shared = _require_bool(
            payload.get("shared_with_partner"),
            reason_code="SHARED_WITH_PARTNER_INVALID",
        )
        target_st = next((s for s in stances if s["stance_id"] == stance_id), None)
        if target_st is None:
            raise StanceContainmentNotFoundError("STANCE_NOT_FOUND")
        if (
            target_st["status"] == "SOURCE_WITHDRAWN"
            or src_reg.get(target_st["scope_ref"], {}).get("withdrawn")
        ):
            raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        target_st["shared_with_partner"] = shared
        target_st["updated_at"] = now_iso
        by_id = {s["stance_id"]: s for s in stances}
        return _commit(
            {
                "stance": _project_stance_row(
                    target_st, subject_view=bool(subject_view), all_by_id=by_id
                )
            },
            receipt_scope=target_st["scope_ref"],
            receipt_target=stance_id,
        )

    if action == "mark_source_updated":
        scope_ref = _require_id(payload.get("scope_ref"), reason_code="SCOPE_REF_INVALID")
        new_src_rev = _require_id(
            payload.get("new_source_revision"), reason_code="SOURCE_REVISION_INVALID"
        )
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        reg_entry = src_reg.get(scope_ref)
        if reg_entry is not None and reg_entry.get("withdrawn"):
            raise StanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if reg_entry is not None and reg_entry["current_source_revision"] == new_src_rev:
            raise StanceContainmentConflictError("SOURCE_REVISION_UNCHANGED")

        src_reg[scope_ref] = {
            "scope_ref": scope_ref,
            "current_source_revision": new_src_rev,
            "withdrawn": False,
            "updated_at": now_iso,
            "withdrawn_at": None,
            "note": note,
            "withdraw_reason": None,
        }
        marked_ids: list[str] = []
        for st in stances:
            if (
                st["scope_ref"] == scope_ref
                and st["status"] == "ACTIVE"
                and st["source_revision"] != new_src_rev
            ):
                st["status"] = "SOURCE_REVISION_STALE"
                st["stale_due_to_revision"] = new_src_rev
                st["stale_marked_at"] = now_iso
                marked_ids.append(st["stance_id"])

        return _commit(
            {
                "scope_ref": scope_ref,
                "new_source_revision": new_src_rev,
                "stale_stance_ids": marked_ids,
            },
            receipt_scope=scope_ref,
            receipt_target=scope_ref,
        )

    if action == "revoke_stance_source":
        scope_ref = _require_id(payload.get("scope_ref"), reason_code="SCOPE_REF_INVALID")
        reason = _optional_single_line(
            payload, "reason", max_len=MAX_PUBLIC_NOTE, reason_code="REASON_INVALID"
        )
        prev_rev = src_reg.get(scope_ref, {}).get("current_source_revision", "revoked")
        src_reg[scope_ref] = {
            "scope_ref": scope_ref,
            "current_source_revision": prev_rev,
            "withdrawn": True,
            "updated_at": now_iso,
            "withdrawn_at": now_iso,
            "note": None,
            "withdraw_reason": reason,
        }
        withdrawn_ids: list[str] = []
        for st in stances:
            if st["scope_ref"] == scope_ref:
                st["status"] = "SOURCE_WITHDRAWN"
                st["withdrawn_at"] = now_iso
                st["shared_with_partner"] = False
                st["text"] = None
                st["private_note"] = None
                st["content_purged"] = True
                withdrawn_ids.append(st["stance_id"])

        return _commit(
            {
                "scope_ref": scope_ref,
                "withdrawn_stance_ids": withdrawn_ids,
                "withdrawn_count": len(withdrawn_ids),
            },
            receipt_scope=scope_ref,
            receipt_target=scope_ref,
        )

    if action == "rebuild_stance_index":
        rebuilt = _compute_active_stance_ids(stances, src_reg)
        stance_dom["active_stance_ids"] = rebuilt
        return _commit({"rebuilt_active_stance_ids": rebuilt})

    # =========================================================================================
    # SUBDOMAIN 2: OBJECT CONTAINMENT & SPATIAL IMPRESSIONS (Doc 31 + ADR-048)
    # =========================================================================================

    containers_by_id = {c["container_id"]: c for c in containers}
    ext_hand_locks = owner_substrate.read_cross_module_hand_locks(
        root, now_iso=now_iso, local_hand_slots=hand_slots
    )

    def _occupied_slots_of(cid: str) -> int:
        c_items = sum(
            1
            for i in items
            if i["placement_kind"] == "container" and i["parent_container_id"] == cid
        )
        c_nested = sum(
            1 for c in containers if c.get("parent_container_id") == cid
        )
        return c_items + c_nested

    def _effective_item_place(it_doc: Mapping[str, Any]) -> str:
        if it_doc["placement_kind"] == "container":
            pcid = it_doc["parent_container_id"]
            if pcid in containers_by_id:
                return owner_substrate.effective_container_place(pcid, containers_by_id)
            return str(it_doc["place_id"])
        if it_doc["placement_kind"] == "hand":
            return str(cont_dom["subject_place_id"])
        return str(it_doc["place_id"])

    def _select_free_hand(requested_slot: Optional[str]) -> str:
        if requested_slot is not None:
            if requested_slot not in HAND_SLOTS:
                raise StanceContainmentValidationError("HAND_SLOT_INVALID")
            if hand_slots[requested_slot] is not None or requested_slot in ext_hand_locks:
                raise StanceContainmentConflictError("HAND_SLOT_OCCUPIED")
            return requested_slot
        if hand_slots["right_hand"] is None and "right_hand" not in ext_hand_locks:
            return "right_hand"
        if hand_slots["left_hand"] is None and "left_hand" not in ext_hand_locks:
            return "left_hand"
        raise StanceContainmentConflictError("HAND_SLOT_OCCUPIED")

    def _check_first_reach_miss_and_raise(
        item_doc: dict[str, Any], *, expected_place: str, fallback_reason: str
    ) -> None:
        if (
            item_doc.get("subject_last_seen_place") == expected_place
            and not item_doc.get("not_at_last_seen_place")
        ):
            item_doc["not_at_last_seen_place"] = True
            state["revision"] = int(state["revision"]) + 1
            state["updated_at"] = now_iso
            written = write_stance_containment_state(root, state)
            if owner_substrate.attention_stance_containment_state_path(root).exists():
                owner_substrate.sync_owner_domains_from_phone_state(
                    root, written, now_iso=now_iso
                )
            raise StanceContainmentConflictError(
                "ITEM_NOT_AT_EXPECTED_SPOT",
                details={
                    "item_id": item_doc["item_id"],
                    "expected_place_id": expected_place,
                },
            )
        raise StanceContainmentConflictError(fallback_reason)

    if action == "register_container":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        cid = _require_id(payload.get("container_id"), reason_code="CONTAINER_ID_INVALID")
        if cid in containers_by_id or any(i["item_id"] == cid for i in items):
            raise StanceContainmentConflictError("CONTAINER_ID_ALREADY_EXISTS")
        if len(containers) >= MAX_CONTAINERS:
            raise StanceContainmentConflictError("CONTAINER_LIMIT_REACHED")
        label = _clean_single_line(
            payload.get("label"), min_len=1, max_len=MAX_TITLE, reason_code="LABEL_INVALID"
        )
        cap = _require_int_range(
            payload.get("capacity", 2),
            minimum=1,
            maximum=MAX_CONTAINER_CAPACITY,
            reason_code="CAPACITY_INVALID",
        )
        acc_raw = payload.get("accepted_classes") or list(ITEM_CLASSES)
        if not isinstance(acc_raw, list) or not acc_raw:
            raise StanceContainmentValidationError("ACCEPTED_CLASSES_INVALID")
        acc = [str(cls) for cls in acc_raw if str(cls) in ITEM_CLASSES]
        if len(acc) != len(acc_raw):
            raise StanceContainmentValidationError("ACCEPTED_CLASSES_INVALID")

        open_st = str(payload.get("open_state") or "open_top")
        if open_st not in CONTAINER_OPEN_STATES:
            raise StanceContainmentValidationError("CONTAINER_OPEN_STATE_INVALID")
        portable = (
            _require_bool(payload.get("portable"), reason_code="PORTABLE_INVALID")
            if "portable" in payload
            else True
        )
        drinkware = (
            _require_bool(payload.get("drinkware"), reason_code="DRINKWARE_INVALID")
            if "drinkware" in payload
            else False
        )

        parent_cid_raw = payload.get("parent_container_id")
        parent_cid: Optional[str] = None
        if parent_cid_raw is not None:
            parent_cid = _require_id(parent_cid_raw, reason_code="CONTAINER_ID_INVALID")
            if parent_cid not in containers_by_id:
                raise StanceContainmentNotFoundError("PARENT_CONTAINER_NOT_FOUND")
            parent_chain = owner_substrate.container_ancestor_chain(parent_cid, containers_by_id)
            if len(parent_chain) + 1 > MAX_CONTAINER_NESTING_DEPTH:
                raise StanceContainmentConflictError("CONTAINER_NESTING_DEPTH_EXCEEDED")
            gate_err = owner_substrate.check_container_access_gate(
                parent_cid, containers_by_id, include_self=True
            )
            if gate_err is not None:
                raise StanceContainmentConflictError(gate_err)
            if _occupied_slots_of(parent_cid) >= int(containers_by_id[parent_cid]["capacity"]):
                raise StanceContainmentConflictError("CONTAINER_FULL")
            c_place = owner_substrate.effective_container_place(parent_cid, containers_by_id)
        else:
            c_place = _require_place_id(
                payload.get("place_id") or cont_dom["subject_place_id"]
            )

        c_room = _room_for_place(c_place)
        seen_now = bool(
            actor == "subject"
            or (cont_dom["subject_present"] and cont_dom["subject_room_id"] == c_room)
        )
        new_c: dict[str, Any] = {
            "container_id": cid,
            "label": label,
            "place_id": c_place,
            "room_id": c_room,
            "parent_container_id": parent_cid,
            "capacity": cap,
            "accepted_classes": acc,
            "open_state": open_st,
            "contents_visibility": "open_top" if open_st == "open_top" else open_st,
            "portable": portable,
            "drinkware": drinkware,
            "subject_last_seen_place": c_place,
            "subject_last_seen_at": now_iso if seen_now else None,
            "not_at_last_seen_place": False,
            "private_impression_note": None,
        }
        containers.append(new_c)
        return _commit(
            {
                "container_id": cid,
                "parent_container_id": parent_cid,
                "open_state": open_st,
            },
            receipt_target=cid,
        )

    if action == "nest_container":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        cid = _require_id(payload.get("container_id"), reason_code="CONTAINER_ID_INVALID")
        cont_doc = containers_by_id.get(cid)
        if cont_doc is None:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        if not cont_doc["portable"]:
            raise StanceContainmentConflictError("CONTAINER_NOT_PORTABLE")

        old_parent = cont_doc.get("parent_container_id")
        if old_parent is not None:
            old_gate = owner_substrate.check_container_access_gate(
                old_parent, containers_by_id, include_self=True
            )
            if old_gate is not None:
                raise StanceContainmentConflictError(old_gate)

        cur_place = owner_substrate.effective_container_place(cid, containers_by_id)
        if actor == "subject" and (
            not cont_dom["subject_present"] or cur_place != cont_dom["subject_place_id"]
        ):
            raise StanceContainmentConflictError("CONTAINER_NOT_REACHABLE")

        parent_cid_raw = payload.get("parent_container_id")
        if parent_cid_raw is not None:
            parent_cid = _require_id(parent_cid_raw, reason_code="CONTAINER_ID_INVALID")
            if parent_cid == cid:
                raise StanceContainmentConflictError("SELF_NESTING_FORBIDDEN")
            if parent_cid not in containers_by_id:
                raise StanceContainmentNotFoundError("PARENT_CONTAINER_NOT_FOUND")
            desc_ids = set(owner_substrate.descendant_container_ids(cid, containers))
            if parent_cid in desc_ids:
                raise StanceContainmentConflictError("CONTAINER_NESTING_CYCLE")
            parent_chain = owner_substrate.container_ancestor_chain(parent_cid, containers_by_id)
            subtree_extra = 1 if desc_ids else 0
            if len(parent_chain) + 1 + subtree_extra > MAX_CONTAINER_NESTING_DEPTH:
                raise StanceContainmentConflictError("CONTAINER_NESTING_DEPTH_EXCEEDED")
            dest_place = owner_substrate.effective_container_place(parent_cid, containers_by_id)
            if actor == "subject" and dest_place != cont_dom["subject_place_id"]:
                raise StanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
            gate_err = owner_substrate.check_container_access_gate(
                parent_cid, containers_by_id, include_self=True
            )
            if gate_err is not None:
                raise StanceContainmentConflictError(gate_err)
            if parent_cid != old_parent and _occupied_slots_of(parent_cid) >= int(
                containers_by_id[parent_cid]["capacity"]
            ):
                raise StanceContainmentConflictError("CONTAINER_FULL")
            cont_doc["parent_container_id"] = parent_cid
        else:
            dest_place = _require_place_id(
                payload.get("to_place_id") or cur_place, reason_code="TO_PLACE_ID_INVALID"
            )
            if actor == "subject" and dest_place != cont_dom["subject_place_id"]:
                raise StanceContainmentConflictError("SURFACE_NOT_REACHABLE")
            cont_doc["parent_container_id"] = None

        affected_cids = [cid] + owner_substrate.descendant_container_ids(cid, containers)
        for acid in affected_cids:
            containers_by_id[acid]["place_id"] = dest_place
            containers_by_id[acid]["room_id"] = _room_for_place(dest_place)
            if actor == "subject":
                containers_by_id[acid]["subject_last_seen_place"] = dest_place
                containers_by_id[acid]["subject_last_seen_at"] = now_iso
                containers_by_id[acid]["not_at_last_seen_place"] = False
        for it in items:
            if it["placement_kind"] == "container" and it["parent_container_id"] in affected_cids:
                it["place_id"] = dest_place
                if actor == "subject":
                    it["subject_last_seen_place"] = dest_place
                    it["subject_last_seen_at"] = now_iso
                    it["not_at_last_seen_place"] = False

        return _commit(
            {
                "container_id": cid,
                "parent_container_id": cont_doc["parent_container_id"],
                "effective_place_id": dest_place,
            },
            receipt_target=cid,
        )

    if action == "set_container_open_state":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        cid = _require_id(payload.get("container_id"), reason_code="CONTAINER_ID_INVALID")
        new_open = payload.get("open_state")
        if not isinstance(new_open, str) or new_open not in CONTAINER_OPEN_STATES:
            raise StanceContainmentValidationError("CONTAINER_OPEN_STATE_INVALID")
        cont_doc = containers_by_id.get(cid)
        if cont_doc is None:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")

        eff_place = owner_substrate.effective_container_place(cid, containers_by_id)
        if actor == "subject" and (
            not cont_dom["subject_present"] or eff_place != cont_dom["subject_place_id"]
        ):
            raise StanceContainmentConflictError("CONTAINER_NOT_REACHABLE")

        ancestor_gate = owner_substrate.check_container_access_gate(
            cid, containers_by_id, include_self=False
        )
        if ancestor_gate is not None:
            raise StanceContainmentConflictError(ancestor_gate)

        cont_doc["open_state"] = new_open
        cont_doc["contents_visibility"] = "open_top" if new_open == "open_top" else new_open

        witnessed = (
            _require_bool(
                payload.get("subject_witnessed"),
                reason_code="SUBJECT_WITNESSED_INVALID",
            )
            if "subject_witnessed" in payload
            else (actor == "subject")
        )
        dest_room = _room_for_place(eff_place)
        if actor == "subject" or (
            witnessed
            and cont_dom["subject_present"]
            and cont_dom["subject_room_id"] == dest_room
        ):
            cont_doc["subject_last_seen_place"] = eff_place
            cont_doc["subject_last_seen_at"] = now_iso
            cont_doc["not_at_last_seen_place"] = False
            if new_open in ("open_top", "open"):
                for it in items:
                    if it["placement_kind"] == "container" and it["parent_container_id"] == cid:
                        it["subject_last_seen_place"] = eff_place
                        it["subject_last_seen_at"] = now_iso
                        it["not_at_last_seen_place"] = False

        return _commit(
            {
                "container_id": cid,
                "open_state": new_open,
                "changed_by": actor,
            },
            receipt_target=cid,
        )

    if action == "pick_up_item":
        item_id = _require_id(payload.get("item_id"), reason_code="ITEM_ID_INVALID")
        req_slot = _optional_single_line(
            payload, "hand_slot", max_len=24, reason_code="HAND_SLOT_INVALID"
        )
        if item_id in containers_by_id:
            raise StanceContainmentConflictError("NESTED_CONTAINER_FORBIDDEN")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise StanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if not cont_dom["subject_present"]:
            raise StanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")
        if item_doc["placement_kind"] == "hand":
            raise StanceContainmentConflictError("ITEM_ALREADY_IN_HAND")
        if _effective_item_place(item_doc) != cont_dom["subject_place_id"]:
            _check_first_reach_miss_and_raise(
                item_doc,
                expected_place=cont_dom["subject_place_id"],
                fallback_reason="ITEM_NOT_REACHABLE",
            )
        if item_doc["placement_kind"] == "container" and item_doc["parent_container_id"] in containers_by_id:
            src_gate = owner_substrate.check_container_access_gate(
                item_doc["parent_container_id"], containers_by_id, include_self=True
            )
            if src_gate is not None:
                raise StanceContainmentConflictError(src_gate)

        chosen_slot = _select_free_hand(req_slot)
        item_doc["placement_kind"] = "hand"
        item_doc["parent_container_id"] = None
        item_doc["held_in_hand"] = chosen_slot
        item_doc["place_id"] = cont_dom["subject_place_id"]
        item_doc["subject_last_seen_place"] = cont_dom["subject_place_id"]
        item_doc["subject_last_seen_at"] = now_iso
        item_doc["not_at_last_seen_place"] = False
        hand_slots[chosen_slot] = item_id
        return _commit(
            {"item_id": item_id, "held_in_hand": chosen_slot},
            receipt_target=item_id,
        )

    if action == "take_from_container":
        item_id = _require_id(payload.get("item_id"), reason_code="ITEM_ID_INVALID")
        container_id = _require_id(
            payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
        )
        req_slot = _optional_single_line(
            payload, "hand_slot", max_len=24, reason_code="HAND_SLOT_INVALID"
        )
        cont_doc = containers_by_id.get(container_id)
        if cont_doc is None:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise StanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if not cont_dom["subject_present"]:
            raise StanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")
        eff_c_place = owner_substrate.effective_container_place(container_id, containers_by_id)
        if eff_c_place != cont_dom["subject_place_id"]:
            raise StanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
        gate_err = owner_substrate.check_container_access_gate(
            container_id, containers_by_id, include_self=True
        )
        if gate_err is not None:
            raise StanceContainmentConflictError(gate_err)
        if (
            item_doc["placement_kind"] != "container"
            or item_doc["parent_container_id"] != container_id
        ):
            _check_first_reach_miss_and_raise(
                item_doc,
                expected_place=eff_c_place,
                fallback_reason="ITEM_NOT_IN_CONTAINER",
            )

        chosen_slot = _select_free_hand(req_slot)
        item_doc["placement_kind"] = "hand"
        item_doc["parent_container_id"] = None
        item_doc["held_in_hand"] = chosen_slot
        item_doc["place_id"] = cont_dom["subject_place_id"]
        item_doc["subject_last_seen_place"] = cont_dom["subject_place_id"]
        item_doc["subject_last_seen_at"] = now_iso
        item_doc["not_at_last_seen_place"] = False
        cont_doc["subject_last_seen_place"] = eff_c_place
        cont_doc["subject_last_seen_at"] = now_iso
        cont_doc["not_at_last_seen_place"] = False
        hand_slots[chosen_slot] = item_id
        return _commit(
            {
                "item_id": item_id,
                "from_container_id": container_id,
                "held_in_hand": chosen_slot,
            },
            receipt_target=item_id,
        )

    if action == "put_in_container":
        item_id = _require_id(payload.get("item_id"), reason_code="ITEM_ID_INVALID")
        container_id = _require_id(
            payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
        )
        cont_doc = containers_by_id.get(container_id)
        if cont_doc is None:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise StanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if item_doc["placement_kind"] != "hand" or item_doc["held_in_hand"] not in HAND_SLOTS:
            raise StanceContainmentConflictError("ITEM_NOT_IN_HAND")
        eff_c_place = owner_substrate.effective_container_place(container_id, containers_by_id)
        if not cont_dom["subject_present"] or eff_c_place != cont_dom["subject_place_id"]:
            raise StanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
        gate_err = owner_substrate.check_container_access_gate(
            container_id, containers_by_id, include_self=True
        )
        if gate_err is not None:
            raise StanceContainmentConflictError(gate_err)
        if item_doc["item_class"] not in cont_doc["accepted_classes"]:
            raise StanceContainmentConflictError("CONTAINER_CLASS_REJECTED")
        if _occupied_slots_of(container_id) >= int(cont_doc["capacity"]):
            raise StanceContainmentConflictError("CONTAINER_FULL")

        freed_slot = item_doc["held_in_hand"]
        hand_slots[freed_slot] = None
        item_doc["placement_kind"] = "container"
        item_doc["parent_container_id"] = container_id
        item_doc["held_in_hand"] = None
        item_doc["place_id"] = eff_c_place
        item_doc["subject_last_seen_place"] = eff_c_place
        item_doc["subject_last_seen_at"] = now_iso
        item_doc["not_at_last_seen_place"] = False
        cont_doc["subject_last_seen_place"] = eff_c_place
        cont_doc["subject_last_seen_at"] = now_iso
        cont_doc["not_at_last_seen_place"] = False
        return _commit(
            {
                "item_id": item_id,
                "container_id": container_id,
                "placed_in_container": True,
                "held_in_hand": None,
            },
            receipt_target=item_id,
        )

    if action == "place_on_surface":
        item_id = _require_id(payload.get("item_id"), reason_code="ITEM_ID_INVALID")
        target_place = (
            _require_place_id(payload.get("place_id"))
            if "place_id" in payload
            else cont_dom["subject_place_id"]
        )
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise StanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if item_doc["placement_kind"] != "hand" or item_doc["held_in_hand"] not in HAND_SLOTS:
            raise StanceContainmentConflictError("ITEM_NOT_IN_HAND")
        if not cont_dom["subject_present"] or target_place != cont_dom["subject_place_id"]:
            raise StanceContainmentConflictError("SURFACE_NOT_REACHABLE")

        freed_slot = item_doc["held_in_hand"]
        hand_slots[freed_slot] = None
        item_doc["placement_kind"] = "surface"
        item_doc["parent_container_id"] = None
        item_doc["held_in_hand"] = None
        item_doc["place_id"] = target_place
        item_doc["subject_last_seen_place"] = target_place
        item_doc["subject_last_seen_at"] = now_iso
        item_doc["not_at_last_seen_place"] = False
        return _commit(
            {"item_id": item_id, "place_id": target_place, "held_in_hand": None},
            receipt_target=item_id,
        )

    if action == "pick_and_put_in_container":
        item_id = _require_id(payload.get("item_id"), reason_code="ITEM_ID_INVALID")
        container_id = _require_id(
            payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
        )
        req_slot = _optional_single_line(
            payload, "hand_slot", max_len=24, reason_code="HAND_SLOT_INVALID"
        )
        if item_id == container_id or item_id in containers_by_id:
            raise StanceContainmentConflictError("NESTED_CONTAINER_FORBIDDEN")

        cont_doc = containers_by_id.get(container_id)
        if cont_doc is None:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise StanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if not cont_dom["subject_present"]:
            raise StanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")

        # Step 1: Pick up into hand (or reuse if already in requested/current hand)
        if item_doc["placement_kind"] == "hand":
            active_slot = str(item_doc["held_in_hand"])
            if req_slot is not None and req_slot != active_slot:
                raise StanceContainmentConflictError("ITEM_ALREADY_IN_OTHER_HAND")
        else:
            if _effective_item_place(item_doc) != cont_dom["subject_place_id"]:
                _check_first_reach_miss_and_raise(
                    item_doc,
                    expected_place=cont_dom["subject_place_id"],
                    fallback_reason="ITEM_NOT_REACHABLE",
                )
            if (
                item_doc["placement_kind"] == "container"
                and item_doc["parent_container_id"] in containers_by_id
            ):
                src_gate = owner_substrate.check_container_access_gate(
                    item_doc["parent_container_id"], containers_by_id, include_self=True
                )
                if src_gate is not None:
                    raise StanceContainmentConflictError(src_gate)
            active_slot = _select_free_hand(req_slot)
            item_doc["placement_kind"] = "hand"
            item_doc["parent_container_id"] = None
            item_doc["held_in_hand"] = active_slot
            item_doc["place_id"] = cont_dom["subject_place_id"]
            item_doc["subject_last_seen_place"] = cont_dom["subject_place_id"]
            item_doc["subject_last_seen_at"] = now_iso
            item_doc["not_at_last_seen_place"] = False
            hand_slots[active_slot] = item_id

        # Step 2: Try placing into container. On failure, NEVER fake rollback — persist item in hand!
        eff_c_place = owner_substrate.effective_container_place(container_id, containers_by_id)
        partial_reason: Optional[str] = None
        if eff_c_place != cont_dom["subject_place_id"]:
            partial_reason = "CONTAINER_NOT_REACHABLE"
        else:
            gate_err = owner_substrate.check_container_access_gate(
                container_id, containers_by_id, include_self=True
            )
            if gate_err is not None:
                partial_reason = gate_err
            elif item_doc["item_class"] not in cont_doc["accepted_classes"]:
                partial_reason = "CONTAINER_CLASS_REJECTED"
            elif _occupied_slots_of(container_id) >= int(cont_doc["capacity"]):
                partial_reason = "CONTAINER_FULL"

        if partial_reason is not None:
            proj_hands = dict(hand_slots)
            for s_k, l_v in ext_hand_locks.items():
                if proj_hands.get(s_k) is None:
                    proj_hands[s_k] = l_v
            return _commit(
                {
                    "item_id": item_id,
                    "container_id": container_id,
                    "partial_completed": True,
                    "partial_completion": True,
                    "rolled_back": False,
                    "picked_up": True,
                    "placed_in_container": False,
                    "held_in_hand": active_slot,
                    "hand_slots": proj_hands,
                },
                status_label="partial_completed",
                ok_flag=False,
                reason_code=partial_reason,
                receipt_target=item_id,
            )

        hand_slots[active_slot] = None
        item_doc["placement_kind"] = "container"
        item_doc["parent_container_id"] = container_id
        item_doc["held_in_hand"] = None
        item_doc["place_id"] = eff_c_place
        item_doc["subject_last_seen_place"] = eff_c_place
        item_doc["subject_last_seen_at"] = now_iso
        item_doc["not_at_last_seen_place"] = False
        cont_doc["subject_last_seen_place"] = eff_c_place
        cont_doc["subject_last_seen_at"] = now_iso
        cont_doc["not_at_last_seen_place"] = False
        return _commit(
            {
                "item_id": item_id,
                "container_id": container_id,
                "partial_completed": False,
                "partial_completion": False,
                "rolled_back": False,
                "picked_up": True,
                "placed_in_container": True,
                "held_in_hand": None,
                "hand_slots": copy.deepcopy(hand_slots),
            },
            receipt_target=item_id,
        )

    if action == "subject_move_container":
        container_id = _require_id(
            payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
        )
        to_place = _require_place_id(
            payload.get("to_place_id"), reason_code="TO_PLACE_ID_INVALID"
        )
        follow = (
            _require_bool(
                payload.get("follow_to_place"), reason_code="FOLLOW_TO_PLACE_INVALID"
            )
            if "follow_to_place" in payload
            else True
        )
        cont_doc = containers_by_id.get(container_id)
        if cont_doc is None:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        cur_c_place = owner_substrate.effective_container_place(container_id, containers_by_id)
        if not cont_dom["subject_present"] or cur_c_place != cont_dom["subject_place_id"]:
            raise StanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
        if not cont_doc["portable"]:
            raise StanceContainmentConflictError("CONTAINER_NOT_PORTABLE")
        ancestor_gate = owner_substrate.check_container_access_gate(
            container_id, containers_by_id, include_self=False
        )
        if ancestor_gate is not None:
            raise StanceContainmentConflictError(ancestor_gate)

        cont_doc["parent_container_id"] = None
        affected_cids = [container_id] + owner_substrate.descendant_container_ids(
            container_id, containers
        )
        dest_room = _room_for_place(to_place)
        for acid in affected_cids:
            c_sub = containers_by_id[acid]
            c_sub["place_id"] = to_place
            c_sub["room_id"] = dest_room
            c_sub["subject_last_seen_place"] = to_place
            c_sub["subject_last_seen_at"] = now_iso
            c_sub["not_at_last_seen_place"] = False

        moved_children: list[str] = []
        for it in items:
            if it["placement_kind"] == "container" and it["parent_container_id"] in affected_cids:
                it["place_id"] = to_place
                it["subject_last_seen_place"] = to_place
                it["subject_last_seen_at"] = now_iso
                it["not_at_last_seen_place"] = False
                moved_children.append(it["item_id"])

        if follow:
            cont_dom["subject_place_id"] = to_place
            cont_dom["subject_room_id"] = dest_room
            for it in items:
                if it["placement_kind"] == "hand":
                    it["place_id"] = to_place
                    it["subject_last_seen_place"] = to_place

        return _commit(
            {
                "container_id": container_id,
                "to_place_id": to_place,
                "moved_child_container_ids": affected_cids[1:],
                "moved_child_item_ids": moved_children,
            },
            receipt_target=container_id,
        )

    if action == "partner_move_container":
        container_id = _require_id(
            payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
        )
        to_place = _require_place_id(
            payload.get("to_place_id"), reason_code="TO_PLACE_ID_INVALID"
        )
        witnessed = (
            _require_bool(
                payload.get("subject_witnessed"),
                reason_code="SUBJECT_WITNESSED_INVALID",
            )
            if "subject_witnessed" in payload
            else False
        )
        cont_doc = containers_by_id.get(container_id)
        if cont_doc is None:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        if not cont_doc["portable"]:
            raise StanceContainmentConflictError("CONTAINER_NOT_PORTABLE")

        cont_doc["parent_container_id"] = None
        dest_room = _room_for_place(to_place)

        actually_seen = bool(
            witnessed
            and cont_dom["subject_present"]
            and cont_dom["subject_room_id"] == dest_room
        )
        affected_cids = [container_id] + owner_substrate.descendant_container_ids(
            container_id, containers
        )
        for acid in affected_cids:
            c_sub = containers_by_id[acid]
            c_sub["place_id"] = to_place
            c_sub["room_id"] = dest_room
            if actually_seen:
                c_sub["subject_last_seen_place"] = to_place
                c_sub["subject_last_seen_at"] = now_iso
                c_sub["not_at_last_seen_place"] = False

        moved_children = []
        for it in items:
            if it["placement_kind"] == "container" and it["parent_container_id"] in affected_cids:
                it["place_id"] = to_place
                if actually_seen:
                    it["subject_last_seen_place"] = to_place
                    it["subject_last_seen_at"] = now_iso
                    it["not_at_last_seen_place"] = False
                moved_children.append(it["item_id"])

        return _commit(
            {
                "container_id": container_id,
                "to_place_id": to_place,
                "moved_child_container_ids": affected_cids[1:],
                "moved_child_item_ids": moved_children,
                "subject_impression_updated": actually_seen,
            },
            receipt_target=container_id,
        )

    if action == "set_subject_location":
        if "place_id" not in payload and "present" not in payload:
            raise StanceContainmentValidationError("LOCATION_UPDATE_EMPTY")
        if "place_id" in payload:
            new_place = _require_place_id(payload.get("place_id"))
            cont_dom["subject_place_id"] = new_place
            cont_dom["subject_room_id"] = _room_for_place(new_place)
            if new_place == "outside":
                cont_dom["subject_present"] = False
        if "present" in payload:
            cont_dom["subject_present"] = _require_bool(
                payload.get("present"), reason_code="PRESENT_FLAG_INVALID"
            )
        obs_arrival = (
            _require_bool(
                payload.get("observe_on_arrival"),
                reason_code="OBSERVE_ON_ARRIVAL_INVALID",
            )
            if "observe_on_arrival" in payload
            else False
        )
        for it in items:
            if it["placement_kind"] == "hand":
                it["place_id"] = cont_dom["subject_place_id"]
                it["subject_last_seen_place"] = cont_dom["subject_place_id"]

        if obs_arrival and cont_dom["subject_present"]:
            _refresh_room_impressions(
                cont_dom, room_id=cont_dom["subject_room_id"], now_iso=now_iso
            )

        return _commit({
            "subject_location": {
                "place_id": cont_dom["subject_place_id"],
                "room_id": cont_dom["subject_room_id"],
                "present": cont_dom["subject_present"],
            }
        })

    if action == "observe_current_room":
        actor_mode = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        obs_cid: Optional[str] = None
        obs_place_arg: Optional[str] = None
        if "place_id" in payload:
            obs_place = _require_place_id(payload.get("place_id"))
            obs_place_arg = obs_place
            if subject_view and actor_mode == "subject":
                cont_dom["subject_place_id"] = obs_place
                cont_dom["subject_room_id"] = _room_for_place(obs_place)
                cont_dom["subject_present"] = obs_place != "outside"
        if "room_id" in payload:
            obs_room = _require_id(payload.get("room_id"), reason_code="ROOM_ID_INVALID")
            if subject_view and actor_mode == "subject":
                cont_dom["subject_room_id"] = obs_room
                cont_dom["subject_present"] = obs_room != "outside"

        if "container_id" in payload:
            obs_cid = _require_id(payload.get("container_id"), reason_code="CONTAINER_ID_INVALID")
            if obs_cid not in containers_by_id:
                raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
            if subject_view and actor_mode == "subject":
                eff_obs_c_place = owner_substrate.effective_container_place(
                    obs_cid, containers_by_id
                )
                if eff_obs_c_place != cont_dom["subject_place_id"]:
                    raise StanceContainmentConflictError("CONTAINER_NOT_REACHABLE")

        if not cont_dom["subject_present"] or cont_dom["subject_room_id"] == "outside":
            raise StanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")

        obs_scope = str(
            payload.get("scope") or ("container" if obs_cid is not None else "room")
        )
        if obs_scope not in owner_substrate.OBSERVE_SCOPES:
            raise StanceContainmentValidationError("OBSERVE_SCOPE_INVALID")

        refreshed = owner_substrate.refresh_room_impressions(
            cont_dom,
            room_id=cont_dom["subject_room_id"],
            now_iso=now_iso,
            scope=obs_scope,
            place_id=obs_place_arg or cont_dom["subject_place_id"],
            container_id=obs_cid,
        )
        return _commit(refreshed, receipt_target=obs_cid)

    if action == "set_item_impression_note":
        target_id = _require_id(payload.get("target_id"), reason_code="TARGET_ID_INVALID")
        pnote = _clean_multiline(
            payload.get("private_impression_note"),
            min_len=1,
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
        target_obj: Optional[dict[str, Any]] = next(
            (i for i in items if i["item_id"] == target_id), None
        )
        if target_obj is None:
            target_obj = containers_by_id.get(target_id)
        if target_obj is None:
            raise StanceContainmentNotFoundError("TARGET_NOT_FOUND")
        target_obj["private_impression_note"] = pnote
        return _commit({"target_id": target_id})

    if action == "remove_container":
        container_id = _require_id(
            payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
        )
        if container_id not in containers_by_id:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        if _occupied_slots_of(container_id) > 0:
            raise StanceContainmentConflictError("CONTAINER_NOT_EMPTY")
        cont_dom["containers"] = [
            c for c in containers if c["container_id"] != container_id
        ]
        return _commit({"removed_container_id": container_id})

    # =========================================================================================
    # CH. 25: COHABITATION SPATIAL NEGOTIATION (suggest_item_use, lend_item, preview_lamp_move)
    # =========================================================================================

    if action == "suggest_item_use":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        target_id = _require_id(payload.get("target_id"), reason_code="TARGET_ID_INVALID")
        if target_id not in containers_by_id and not any(
            i["item_id"] == target_id for i in items
        ):
            raise StanceContainmentNotFoundError("TARGET_NOT_FOUND")
        suggested_use = _clean_single_line(
            payload.get("suggested_use"),
            min_len=1,
            max_len=120,
            reason_code="SUGGESTED_USE_INVALID",
        )
        target_cid = (
            _require_id(
                payload.get("target_container_id"), reason_code="CONTAINER_ID_INVALID"
            )
            if "target_container_id" in payload
            else None
        )
        if target_cid is not None and target_cid not in containers_by_id:
            raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        sugg_id = _stable_id(
            "sugg", actor, target_id, suggested_use, len(cohabitation["suggestions"]), now_iso
        )
        sugg_doc = {
            "suggestion_id": sugg_id,
            "actor": actor,
            "target_id": target_id,
            "suggested_use": suggested_use,
            "target_container_id": target_cid,
            "status": "PENDING",
            "decision": None,
            "note": note,
            "created_at": now_iso,
            "responded_at": None,
        }
        cohabitation["suggestions"].append(sugg_doc)
        while len(cohabitation["suggestions"]) > 32:
            cohabitation["suggestions"].pop(0)
        return _commit({"suggestion": sugg_doc}, receipt_target=sugg_id)

    if action == "respond_item_use_suggestion":
        sugg_id = _require_id(
            payload.get("suggestion_id"), reason_code="SUGGESTION_ID_INVALID"
        )
        decision = payload.get("decision")
        if decision not in ("accept", "decline", "keep_habit"):
            raise StanceContainmentValidationError("DECISION_INVALID")
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        sugg_doc = next(
            (s for s in cohabitation["suggestions"] if s["suggestion_id"] == sugg_id),
            None,
        )
        if sugg_doc is None:
            raise StanceContainmentNotFoundError("SUGGESTION_NOT_FOUND")
        if sugg_doc["status"] != "PENDING":
            raise StanceContainmentConflictError("SUGGESTION_ALREADY_RESPONDED")
        status_map = {
            "accept": "ACCEPTED",
            "decline": "DECLINED",
            "keep_habit": "KEPT_HABIT",
        }
        sugg_doc["status"] = status_map[decision]
        sugg_doc["decision"] = decision
        sugg_doc["responded_at"] = now_iso
        if note is not None:
            sugg_doc["note"] = note
        return _commit({"suggestion": sugg_doc}, receipt_target=sugg_id)

    if action == "lend_item":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        item_id = _require_id(payload.get("item_id"), reason_code="ITEM_ID_INVALID")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise StanceContainmentNotFoundError("ITEM_NOT_FOUND")
        mode = str(payload.get("mode") or "lend")
        if mode not in ("lend", "return"):
            raise StanceContainmentValidationError("LOAN_MODE_INVALID")
        default_to = "partner" if actor == "subject" else "subject"
        to_side = str(payload.get("to_side") or default_to)
        if to_side not in PARTICIPANT_ACTORS:
            raise StanceContainmentValidationError("TO_SIDE_INVALID")
        if item_doc["owner"] == "subject" and actor == "partner" and mode == "lend":
            raise StanceContainmentPermissionError("PRIVATE_ITEM_CONSENT_REQUIRED")
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        loan_doc = {
            "item_id": item_id,
            "from_side": actor,
            "to_side": to_side,
            "status": "LENT" if mode == "lend" else "RETURNED",
            "note": note,
            "updated_at": now_iso,
        }
        cohabitation["loans"][item_id] = loan_doc
        return _commit({"loan": loan_doc}, receipt_target=item_id)

    if action == "preview_lamp_move":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        from_place = (
            _require_place_id(payload.get("from_place_id"))
            if "from_place_id" in payload
            else "desk_left"
        )
        to_place = _require_place_id(
            payload.get("to_place_id"), reason_code="TO_PLACE_ID_INVALID"
        )
        commit_flag = (
            _require_bool(payload.get("commit"), reason_code="COMMIT_FLAG_INVALID")
            if "commit" in payload
            else False
        )
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        lamp_doc = {
            "actor": actor,
            "from_place_id": from_place,
            "to_place_id": to_place,
            "committed": commit_flag,
            "status": "COMMITTED" if commit_flag else "PREVIEW",
            "note": note,
            "updated_at": now_iso,
        }
        cohabitation["lamp_preview"] = lamp_doc
        return _commit({"lamp_preview": lamp_doc})

    # =========================================================================================
    # SUBDOMAIN 3: ATTACHED DESK CO-PRESENCE & TABLE CONSUMABLES (Docs 12/13/14 + P0 #1)
    # =========================================================================================

    if action == "set_primary_activity":
        pa = dt_dom["primary_activity"]
        if "activity_id" in payload:
            pa["activity_id"] = _require_id(
                payload.get("activity_id"), reason_code="ACTIVITY_ID_INVALID"
            )
        if "label" in payload:
            pa["label"] = _clean_single_line(
                payload.get("label"),
                min_len=1,
                max_len=MAX_TITLE,
                reason_code="LABEL_INVALID",
            )
        if "status" in payload:
            st_val = payload.get("status")
            if st_val not in PRIMARY_ACTIVITY_STATUSES:
                raise StanceContainmentValidationError("PRIMARY_ACTIVITY_STATUS_INVALID")
            pa["status"] = st_val
        if "interruptibility" in payload:
            intr_val = payload.get("interruptibility")
            if intr_val not in INTERRUPTIBILITY_MODES:
                raise StanceContainmentValidationError("INTERRUPTIBILITY_INVALID")
            pa["interruptibility"] = intr_val
        if "private_note" in payload:
            pa["private_note"] = _clean_multiline(
                payload.get("private_note"),
                min_len=1,
                max_len=MAX_PRIVATE_NOTE,
                reason_code="PRIVATE_NOTE_INVALID",
            )
        pa["updated_at"] = now_iso
        if dt_dom["copresence"]["mode"] != "off":
            dt_dom["copresence"]["attached_to_activity_id"] = pa["activity_id"]
        return _commit({"primary_activity_id": pa["activity_id"]})

    if action == "propose_copresence":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        target_mode = payload.get("target_mode")
        if not isinstance(target_mode, str) or target_mode not in ACTIVE_COPRESENCE_MODES:
            raise StanceContainmentValidationError("COPRESENCE_MODE_INVALID")
        minutes = (
            _require_int_range(
                payload.get("declared_minutes"),
                minimum=1,
                maximum=MAX_COPRESENCE_MINUTES,
                reason_code="DECLARED_MINUTES_INVALID",
            )
            if "declared_minutes" in payload
            else DEFAULT_COPRESENCE_MINUTES
        )
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        prop_id = _stable_id("cpprop", actor, target_mode, now_iso)
        dt_dom["copresence"]["pending_proposal"] = {
            "proposal_id": prop_id,
            "proposer": actor,
            "target_mode": target_mode,
            "declared_minutes": minutes,
            "note": note,
            "proposed_at": now_iso,
        }
        return _commit({"proposal_id": prop_id, "proposer": actor, "target_mode": target_mode})

    if action == "respond_copresence":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        decision = payload.get("decision")
        if decision not in ("accept", "decline"):
            raise StanceContainmentValidationError("COPRESENCE_DECISION_INVALID")
        cp = dt_dom["copresence"]
        prop = cp.get("pending_proposal")
        if prop is None:
            raise StanceContainmentNotFoundError("COPRESENCE_PROPOSAL_NOT_FOUND")
        if prop["proposer"] == actor:
            raise StanceContainmentConflictError("SELF_COUNTERSIGN_FORBIDDEN")

        if decision == "decline":
            cp["pending_proposal"] = None
            cp["last_changed_by"] = actor
            return _commit({"decision": "decline", "zero_exit_debt": True})

        pa = dt_dom["primary_activity"]
        if (
            prop["target_mode"] == "break_chat"
            and pa["status"] == "ACTIVE"
            and pa["interruptibility"] == "ATOMIC"
        ):
            raise StanceContainmentConflictError("PRIMARY_ACTIVITY_ATOMIC_BOUNDARY")

        decl_min = int(prop["declared_minutes"])
        end_iso = (now_dt + timedelta(minutes=decl_min)).isoformat(timespec="seconds")
        sess_id = cp.get("session_id") or _cps_session_id(prop["proposal_id"], now_iso)
        cp["session_id"] = sess_id
        cp["mode"] = prop["target_mode"]
        cp["attached_to_activity_id"] = pa["activity_id"]
        cp["started_at"] = cp.get("started_at") or now_iso
        cp["expected_end_at"] = end_iso
        cp["declared_minutes"] = decl_min
        cp["initiated_by"] = prop["proposer"]
        cp["last_changed_by"] = actor
        cp["note"] = prop.get("note")
        cp["pending_proposal"] = None
        return _commit({"decision": "accept", "copresence_mode": cp["mode"]})

    if action == "switch_copresence_mode":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in COPRESENCE_MODES:
            raise StanceContainmentValidationError("COPRESENCE_MODE_INVALID")
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        cp = dt_dom["copresence"]
        pa = dt_dom["primary_activity"]

        if mode == "off":
            cp["mode"] = "off"
            cp["session_id"] = None
            cp["attached_to_activity_id"] = None
            cp["started_at"] = None
            cp["expected_end_at"] = None
            cp["declared_minutes"] = None
            cp["last_changed_by"] = actor
            cp["last_exit_by"] = actor
            cp["last_exit_at"] = now_iso
            cp["note"] = note
            cp["pending_proposal"] = None
            cascade_info = cascade_pause_adult_scene_permit(
                root, now_iso=now_iso, reason="COPRESENCE_SWITCHED_OFF"
            )
            return _commit({
                "copresence_mode": "off",
                "hijacks_primary_activity": False,
                "adult_permit_cascade": cascade_info,
            })

        if (
            mode == "break_chat"
            and pa["status"] == "ACTIVE"
            and pa["interruptibility"] == "ATOMIC"
        ):
            raise StanceContainmentConflictError("PRIMARY_ACTIVITY_ATOMIC_BOUNDARY")

        decl_min = (
            _require_int_range(
                payload.get("declared_minutes"),
                minimum=1,
                maximum=MAX_COPRESENCE_MINUTES,
                reason_code="DECLARED_MINUTES_INVALID",
            )
            if "declared_minutes" in payload
            else (cp.get("declared_minutes") or DEFAULT_COPRESENCE_MINUTES)
        )
        end_iso = (now_dt + timedelta(minutes=int(decl_min))).isoformat(timespec="seconds")
        cp["session_id"] = cp.get("session_id") or _cps_session_id(actor, mode, now_iso)
        cp["mode"] = mode
        # Attached bracket: strictly binds current primary_activity without interrupting/ending it!
        cp["attached_to_activity_id"] = pa["activity_id"]
        cp["started_at"] = cp.get("started_at") or now_iso
        cp["expected_end_at"] = end_iso
        cp["declared_minutes"] = int(decl_min)
        cp["initiated_by"] = cp.get("initiated_by") or actor
        cp["last_changed_by"] = actor
        cp["note"] = note
        cp["pending_proposal"] = None
        return _commit({"copresence_mode": mode, "hijacks_primary_activity": False})

    if action == "exit_copresence":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        cp = dt_dom["copresence"]
        cp["mode"] = "off"
        cp["session_id"] = None
        cp["attached_to_activity_id"] = None
        cp["started_at"] = None
        cp["expected_end_at"] = None
        cp["declared_minutes"] = None
        cp["last_changed_by"] = actor
        cp["last_exit_by"] = actor
        cp["last_exit_at"] = now_iso
        cp["note"] = note
        cp["pending_proposal"] = None
        cascade_info = cascade_pause_adult_scene_permit(
            root, now_iso=now_iso, reason="COPRESENCE_EXITED"
        )
        return _commit({
            "copresence_mode": "off",
            "exited_by": actor,
            "hijacks_primary_activity": False,
            "zero_exit_debt": True,
            "adult_permit_cascade": cascade_info,
        })

    if action in ("prepare_table_dish", "partner_bring_snack"):
        title = _clean_single_line(
            payload.get("title"),
            min_len=1,
            max_len=MAX_TITLE,
            reason_code="TITLE_INVALID",
        )
        default_kind = "tea_pot" if action == "prepare_table_dish" else "snack_plate"
        kind = payload.get("kind", default_kind)
        if not isinstance(kind, str) or kind not in DISH_KINDS:
            raise StanceContainmentValidationError("DISH_KIND_INVALID")
        portions = _require_int_range(
            payload.get("portions", 2),
            minimum=1,
            maximum=MAX_PORTIONS,
            reason_code="PORTIONS_INVALID",
        )
        place_id = (
            _require_place_id(payload.get("place_id"))
            if "place_id" in payload
            else "dining_table"
        )
        init_temp = payload.get("initial_temperature", "hot")
        if not isinstance(init_temp, str) or init_temp not in THERMAL_BANDS:
            raise StanceContainmentValidationError("INITIAL_TEMPERATURE_INVALID")

        # Ch. 15 / 31 Pen-in-mug physical consequence: drinkware occupied by stationery rejects pouring!
        explicit_cid = (
            _require_id(payload.get("container_id"), reason_code="CONTAINER_ID_INVALID")
            if "container_id" in payload
            else None
        )
        if explicit_cid is not None:
            cup_doc = containers_by_id.get(explicit_cid)
            if cup_doc is None:
                raise StanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
            if _occupied_slots_of(explicit_cid) > 0:
                raise StanceContainmentConflictError(
                    "CUP_OCCUPIED_BY_STATIONERY",
                    details={"container_id": explicit_cid},
                )
            if str(cup_doc.get("open_state") or "open_top") not in ("open_top", "open"):
                raise StanceContainmentConflictError("CONTAINER_CLOSED")
        elif kind == "warm_drink" and "ceramic_mug" in containers_by_id:
            if any(
                i["placement_kind"] == "container"
                and i["parent_container_id"] == "ceramic_mug"
                and i["item_class"] in ("writing_tool", "stationery")
                for i in items
            ):
                raise StanceContainmentConflictError(
                    "CUP_OCCUPIED_BY_STATIONERY",
                    details={"container_id": "ceramic_mug"},
                )

        dishes = dt_dom["table_dishes"]
        if len(dishes) >= MAX_TABLE_DISHES:
            raise StanceContainmentConflictError("TABLE_SURFACE_FULL")

        prepared_by = "subject" if action == "prepare_table_dish" else "partner"
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        pnote = _optional_multiline(
            payload,
            "private_note",
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
        dish_id = _stable_id("dish", prepared_by, title, len(dishes), now_iso)
        new_dish: dict[str, Any] = {
            "dish_id": dish_id,
            "title": title,
            "kind": kind,
            "place_id": place_id,
            "prepared_by": prepared_by,
            "prepared_at": now_iso,
            "initial_temperature": init_temp,
            "initial_portions": portions,
            "portions_remaining": portions,
            "consumed_by_subject": 0,
            "consumed_by_partner": 0,
            "discarded_portions": 0,
            "status": "AVAILABLE",
            "empty_at": None,
            "note": note,
            "private_note": pnote,
        }
        dishes.append(new_dish)
        return _commit(
            {
                "dish_id": dish_id,
                "portions_remaining": portions,
                "temperature_band": _derive_dish_temperature(new_dish, now_dt),
                "no_hidden_affection_score": True,
            },
            receipt_target=dish_id,
        )

    if action == "take_portion":
        dish_id = _require_id(payload.get("dish_id"), reason_code="DISH_ID_INVALID")
        consumer = _resolve_actor(payload, key="consumer", subject_view=bool(subject_view))
        take_count = _require_int_range(
            payload.get("portions", 1),
            minimum=1,
            maximum=MAX_PORTIONS,
            reason_code="PORTIONS_INVALID",
        )
        dish = next(
            (d for d in dt_dom["table_dishes"] if d["dish_id"] == dish_id), None
        )
        if dish is None:
            raise StanceContainmentNotFoundError("DISH_NOT_FOUND")
        rem = int(dish["portions_remaining"])
        if rem == 0:
            raise StanceContainmentConflictError("DISH_EMPTY_NO_PORTIONS_LEFT")
        if take_count > rem:
            raise StanceContainmentConflictError("INSUFFICIENT_PORTIONS_REMAINING")

        dish["portions_remaining"] = rem - take_count
        if consumer == "subject":
            dish["consumed_by_subject"] = int(dish["consumed_by_subject"]) + take_count
        else:
            dish["consumed_by_partner"] = int(dish["consumed_by_partner"]) + take_count

        if dish["portions_remaining"] == 0:
            dish["status"] = "EMPTY"
            dish["empty_at"] = now_iso

        return _commit(
            {
                "dish_id": dish_id,
                "consumer": consumer,
                "taken_portions": take_count,
                "portions_remaining": dish["portions_remaining"],
                "is_empty_plate": dish["portions_remaining"] == 0,
                "temperature_band": _derive_dish_temperature(dish, now_dt),
                "no_hidden_affection_score": True,
            },
            receipt_target=dish_id,
        )

    if action == "clear_table_dish":
        dish_id = _require_id(payload.get("dish_id"), reason_code="DISH_ID_INVALID")
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        discard_rem = (
            _require_bool(
                payload.get("discard_remaining"),
                reason_code="DISCARD_REMAINING_INVALID",
            )
            if "discard_remaining" in payload
            else False
        )
        dish = next(
            (d for d in dt_dom["table_dishes"] if d["dish_id"] == dish_id), None
        )
        if dish is None:
            raise StanceContainmentNotFoundError("DISH_NOT_FOUND")
        rem = int(dish["portions_remaining"])
        if rem > 0 and not discard_rem:
            raise StanceContainmentConflictError("DISH_NOT_EMPTY_REQUIRE_DISCARD_FLAG")

        discarded_now = rem
        dt_dom["table_dishes"] = [
            d for d in dt_dom["table_dishes"] if d["dish_id"] != dish_id
        ]
        return _commit({
            "cleared_dish_id": dish_id,
            "cleared_by": actor,
            "discarded_portions": discarded_now,
            "counted_as_intake": False,
        })

    raise StanceContainmentValidationError(f"ACTION_INVALID:{action}")


def cascade_pause_adult_scene_permit(
    root: Path | str,
    *,
    now_iso: str,
    reason: str = "COPRESENCE_EXITED",
) -> dict[str, Any]:
    """GAP-02: When co-presence exits or presence becomes absent, cascade ACTIVE ScenePermit
    in ``<root>/phone/adult_intimacy.json`` to ``PAUSED_PRESENCE_UNKNOWN``.

    100% zero disk writes when ``adult_intimacy.json`` is absent or has no ACTIVE permit.
    """
    intimacy_path = Path(root) / STATE_DIRNAME / "adult_intimacy.json"
    if not intimacy_path.exists() or intimacy_path.is_symlink():
        return {"cascaded": False, "paused_permit_ids": []}
    try:
        doc = json.loads(intimacy_path.read_text(encoding="utf-8"))
    except Exception:
        return {"cascaded": False, "paused_permit_ids": []}
    if not isinstance(doc, dict):
        return {"cascaded": False, "paused_permit_ids": []}

    now_dt, clean_iso = _normalize_now(now_iso)
    paused_exp_iso = (now_dt + timedelta(hours=4)).isoformat(timespec="seconds")
    paused_ids: list[str] = []
    changed = False

    permits_block = doc.get("permits")
    if isinstance(permits_block, dict):
        records = permits_block.get("records")
        if isinstance(records, dict):
            for pid, rec in records.items():
                if not isinstance(rec, dict):
                    continue
                if rec.get("state") == "ACTIVE" or rec.get("status") == "ACTIVE":
                    if "state" in rec or "status" not in rec:
                        rec["state"] = "PAUSED_PRESENCE_UNKNOWN"
                    if "status" in rec:
                        rec["status"] = "PAUSED_PRESENCE_UNKNOWN"
                    rec["pause_reason"] = reason
                    rec["paused_at"] = clean_iso
                    rec["paused_expires_at"] = paused_exp_iso
                    if "permit_rev" in rec and isinstance(rec["permit_rev"], int):
                        rec["permit_rev"] = int(rec["permit_rev"]) + 1
                    paused_ids.append(str(pid))
                    changed = True

    for alias_key in ("scene_permit", "active_permit", "permit"):
        sub = doc.get(alias_key)
        if isinstance(sub, dict) and (
            sub.get("state") == "ACTIVE" or sub.get("status") == "ACTIVE"
        ):
            if "state" in sub:
                sub["state"] = "PAUSED_PRESENCE_UNKNOWN"
            if "status" in sub:
                sub["status"] = "PAUSED_PRESENCE_UNKNOWN"
            if "state" not in sub and "status" not in sub:
                sub["state"] = "PAUSED_PRESENCE_UNKNOWN"
            sub["pause_reason"] = reason
            sub["paused_at"] = clean_iso
            sub["paused_expires_at"] = paused_exp_iso
            pid_val = str(sub.get("permit_id") or alias_key)
            if pid_val not in paused_ids:
                paused_ids.append(pid_val)
            changed = True

    if doc.get("state") == "ACTIVE" or doc.get("status") == "ACTIVE":
        if doc.get("state") == "ACTIVE":
            doc["state"] = "PAUSED_PRESENCE_UNKNOWN"
        if doc.get("status") == "ACTIVE":
            doc["status"] = "PAUSED_PRESENCE_UNKNOWN"
        doc["pause_reason"] = reason
        doc["paused_at"] = clean_iso
        changed = True

    if changed:
        if isinstance(doc.get("revision"), int) and not isinstance(doc.get("revision"), bool):
            doc["revision"] = int(doc["revision"]) + 1
        doc["updated_at"] = clean_iso
        _atomic_write_json(intimacy_path, doc)

    return {
        "cascaded": changed,
        "target_state": "PAUSED_PRESENCE_UNKNOWN" if changed else None,
        "paused_permit_ids": paused_ids,
    }


class CoPresenceAdapter:
    """GAP-06 & ERR-03 unified co-presence adapter with liveness & compatibility probes."""

    CANONICAL_SESSION_PREFIX = "cps_"

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self._root = Path(root) if root is not None else None

    def _resolve_root(self, root: Optional[Path | str] = None) -> Path:
        chosen = root if root is not None else self._root
        if chosen is None:
            raise StanceContainmentValidationError("ROOT_REQUIRED")
        return Path(chosen)

    @staticmethod
    def compatibility_probe() -> dict[str, Any]:
        return {
            "ok": True,
            "adapter": "CoPresenceAdapter",
            "canonical_session_prefix": "cps_",
            "accepts_legacy_hyphen_prefix": True,
            "cascades_adult_permit_pause": True,
        }

    def liveness_probe(
        self,
        root: Optional[Path | str] = None,
        *,
        now: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Zero-write liveness probe across desk co-presence and embodied avatar proxy."""
        day_root = self._resolve_root(root)
        now_dt, now_iso = _normalize_now(now)
        desk_proj = read_stance_containment(day_root, now=now_iso, subject_view=False)
        cp_proj = desk_proj["shared_desk_table"]["copresence"]
        desk_active = bool(cp_proj.get("active"))
        desk_session = cp_proj.get("session_id")

        embodied_active = False
        embodied_session: Optional[str] = None
        embodied_expired = False
        social_path = day_root / STATE_DIRNAME / "v03_embodied_social.json"
        if social_path.exists() and not social_path.is_symlink():
            try:
                sdoc = json.loads(social_path.read_text(encoding="utf-8"))
            except Exception:
                sdoc = None
            if isinstance(sdoc, Mapping):
                ec = sdoc.get("embodied_contact")
                if isinstance(ec, Mapping):
                    pmode = str(ec.get("partner_presence") or ec.get("presence_mode") or "absent")
                    avatar = ec.get("user_avatar_proxy")
                    exp_str = avatar.get("expires_at") if isinstance(avatar, Mapping) else None
                    exp_dt = _parse_stored_iso(exp_str)
                    if exp_dt is not None and now_dt >= exp_dt:
                        embodied_expired = True
                    elif pmode != "absent":
                        embodied_active = True
                        cps_obj = ec.get("copresence_session")
                        raw_sid = (
                            cps_obj.get("session_id")
                            if isinstance(cps_obj, Mapping)
                            else None
                        ) or ec.get("copresence_session_id")
                        embodied_session = str(raw_sid) if raw_sid else None

        active_session = desk_session or embodied_session
        return {
            "ok": True,
            "alive": bool(desk_active or embodied_active),
            "desk_copresence_active": desk_active,
            "embodied_copresence_active": embodied_active,
            "embodied_avatar_expired": embodied_expired,
            "session_id": active_session,
            "canonical_prefix": "cps_",
            "legacy_prefix_accepted": True,
            "observed_at": now_iso,
        }

    def project(
        self,
        root: Optional[Path | str] = None,
        *,
        now: str | datetime | None = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        day_root = self._resolve_root(root)
        proj = read_stance_containment(day_root, now=now, subject_view=subject_view)
        out = dict(proj)
        out["copresence_liveness"] = self.liveness_probe(day_root, now=now)
        return out

    def apply(
        self,
        payload: Mapping[str, Any],
        *,
        root: Optional[Path | str] = None,
        now: str | datetime | None = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        day_root = self._resolve_root(root)
        return apply_stance_containment_action(
            day_root, payload, now=now, subject_view=subject_view
        )


def _refresh_room_impressions(
    cont_dom: dict[str, Any], *, room_id: str, now_iso: str
) -> dict[str, Any]:
    """Refresh subject spatial impressions for objects physically in ``room_id``."""

    return owner_substrate.refresh_room_impressions(
        cont_dom, room_id=room_id, now_iso=now_iso
    )


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "ACTIVE_COPRESENCE_MODES",
    "CONTAINER_OPEN_STATES",
    "COPRESENCE_MODES",
    "CoPresenceAdapter",
    "DISH_KINDS",
    "HAND_SLOTS",
    "ITEM_CLASSES",
    "MAX_CONTAINER_NESTING_DEPTH",
    "STANCE_CONTAINMENT_ACTIONS",
    "STANCE_CONTAINMENT_FIELDS",
    "STANCE_CONTAINMENT_FILENAME",
    "STANCE_CONTAINMENT_SCHEMA",
    "STANCE_CONTAINMENT_SUBJECT_ACTIONS",
    "STANCE_FACETS",
    "STANCE_STATUSES",
    "StanceContainmentConflictError",
    "StanceContainmentError",
    "StanceContainmentNotFoundError",
    "StanceContainmentPermissionError",
    "StanceContainmentValidationError",
    "THERMAL_BANDS",
    "apply_stance_containment_action",
    "cascade_pause_adult_scene_permit",
    "default_stance_containment_state",
    "project_stance_containment",
    "read_stance_containment",
    "read_stance_containment_state",
    "stance_containment_state_path",
    "write_stance_containment_state",
]
