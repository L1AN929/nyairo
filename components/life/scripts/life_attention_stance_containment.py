#!/usr/bin/env python3
"""v3.0 Owner-Level Stance Revision Lineage & World Nested Container Substrate (ADR-047 / ADR-048).

Persists ``life.v03.attention_stance_containment.v1`` at
``<day_root>/life/attention_stance_containment.json`` with 100% zero disk writes on pure
reads, zero LLM calls, zero RNG, and open-source character neutrality (default ``"TA"``).

Core Responsibilities
---------------------
1. ADR-047 Reading Material Anchor & Stance Revision Lineage:
   * Zero-write probe of mounted Supply / reading workbench revisions under ``<day_root>``
     (falling back cleanly to explicit ``(scope_ref, source_revision, page_or_anchor, facet)``
     when no external reading library is mounted).
   * Closed ``facet`` enum ``("appearance", "content", "method_fit")``.
   * Supports ``propose_stance``, ``fix_stance_typo`` (CAS ``expected_version`` in-place typo
     correction without altering lineage), ``revise_stance`` (new ``stance_id``, bidirectional
     ``replaces_stance_id`` / ``superseded_by``, old stance marked ``SUPERSEDED`` with original
     text preserved), ``mark_source_updated`` (``SOURCE_REVISION_STALE``), and
     ``revoke_stance_source`` (``SOURCE_WITHDRAWN``, purges cached text/excerpts/notes, writes
     irreversible ``withdrawn_sources`` tombstones so replay or index rebuild never resurrects).
2. ADR-048 World Nested Container Reachability & Two-Hand Partial Transfer State Machine:
   * Supports ``room -> surface/place -> container (optional parent_container_id) -> item``
     plus two hand slots ``left_hand`` / ``right_hand``.
   * Supports container ``open_state`` in ``("open_top", "open", "closed", "locked")`` (default
     ``"open_top"``) and ``set_container_open_state``; closed/locked containers (or ancestors)
     reject direct item insertion/extraction with ``CONTAINER_CLOSED`` / ``CONTAINER_LOCKED``.
   * ``pick_and_put_in_container`` implements strict non-atomic partial completion: if step 1
     picks the item into ``hand_slot`` and step 2 fails with ``CONTAINER_FULL``,
     ``CONTAINER_NOT_REACHABLE``, ``CONTAINER_CLASS_REJECTED``, ``CONTAINER_CLOSED``, or
     ``CONTAINER_LOCKED``, the item remains in ``hand_slot`` on disk
     (``partial_completion=True``, ``rolled_back=False``).
   * Physical placement is strictly separated from subject spatial impression:
     ``partner_move_container`` updates physical positions of the container, nested child
     containers, and contained items while leaving ``subject_last_seen_place`` stale
     (``impression_stale=True``) when unwitnessed until ``observe_current_room`` runs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = Path(__file__).resolve().parent
_COMPONENTS_ROOT = _LIFE_SCRIPTS.parents[1]
for _entry in (str(_LIFE_SCRIPTS), str(_COMPONENTS_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import attention_view_projector as attn_proj  # noqa: E402

ATTENTION_STANCE_CONTAINMENT_SCHEMA = "life.v03.attention_stance_containment.v1"
STATE_DIRNAME = "life"
STATE_FILENAME = "attention_stance_containment.json"
STATE_SCHEMA_VERSION = 1
CONTACT_MAX_NATURAL_SECONDS = 1800

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
PARTICIPANT_ACTORS: tuple[str, ...] = ("subject", "partner")
OBSERVE_SCOPES: tuple[str, ...] = ("room", "micro_zone", "container")

MAX_STANCES = 64
MAX_STANCE_TEXT = 400
MAX_EXCERPT_TEXT = 400
MAX_PRIVATE_NOTE = 200
MAX_PUBLIC_NOTE = 160
MAX_TITLE = 60
MAX_OPERATION_KEY = 120
MAX_OPERATION_RECEIPTS = 64
MAX_CONTAINERS = 24
MAX_ITEMS = 48
MAX_CONTAINER_CAPACITY = 12
MAX_CONTAINER_NESTING_DEPTH = 3

LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,120}$")

PLACE_ROOM_MAP: dict[str, str] = {
    "study_desk": "study_room",
    "desk_left": "study_room",
    "desk_right": "study_room",
    "bookshelf": "study_room",
    "window_sill": "study_room",
    "dining_table": "kitchen_dining",
    "kitchen_counter": "kitchen_dining",
    "entry_stand": "entryway",
    "entryway_tray": "entryway",
    "entryway": "entryway",
    "doorway_shoe_zone": "entryway",
    "sofa_side": "living_room",
    "bed_side": "bedroom",
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

OWNER_ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    # ADR-047: Stance & revision lineage
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
    # ADR-048: Nested world containers, open/lock gate & hand transfer state machine
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
    # Ch. 25: Cohabitation spatial negotiation (suggest_item_use, lend_item, preview_lamp_move)
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
}

OWNER_ACTIONS: frozenset[str] = frozenset(OWNER_ACTION_ALLOWED_FIELDS.keys())

OWNER_SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
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
})


# ---------------------------------------------------------------------------------------------
# Structured Exceptions
# ---------------------------------------------------------------------------------------------


class LifeStanceContainmentError(ValueError):
    """Base structured error for ADR-047 / ADR-048 owner substrate."""

    http_status: int = 400

    def __init__(self, reason_code: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.details: dict[str, Any] = dict(details or {})


class LifeStanceContainmentValidationError(LifeStanceContainmentError):
    http_status = 400


class LifeStanceContainmentPermissionError(LifeStanceContainmentError):
    http_status = 403


class LifeStanceContainmentNotFoundError(LifeStanceContainmentError):
    http_status = 404


class LifeStanceContainmentConflictError(LifeStanceContainmentError):
    http_status = 409


# ---------------------------------------------------------------------------------------------
# Deterministic Helpers
# ---------------------------------------------------------------------------------------------


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


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
            raise LifeStanceContainmentValidationError("NOW_TIMESTAMP_INVALID")
        normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise LifeStanceContainmentValidationError("NOW_TIMESTAMP_INVALID") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds")
    raise LifeStanceContainmentValidationError("NOW_TIMESTAMP_INVALID")


def _iso_or_none(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:48]


def _require_id(value: Any, *, reason_code: str) -> str:
    if not isinstance(value, str):
        raise LifeStanceContainmentValidationError(reason_code)
    cleaned = value.strip()
    if not LOCAL_ID_RE.match(cleaned):
        raise LifeStanceContainmentValidationError(reason_code)
    return cleaned


def _require_place_id(value: Any, *, reason_code: str = "PLACE_ID_INVALID") -> str:
    pid = _require_id(value, reason_code=reason_code)
    if pid in HAND_SLOTS:
        raise LifeStanceContainmentValidationError(reason_code)
    return pid


def _require_anchor(value: Any, *, reason_code: str = "PAGE_OR_ANCHOR_INVALID") -> str:
    if isinstance(value, bool):
        raise LifeStanceContainmentValidationError(reason_code)
    if isinstance(value, int):
        if value < 1 or value > 100000:
            raise LifeStanceContainmentValidationError(reason_code)
        return str(value)
    if isinstance(value, str):
        if "\x00" in value or "\r" in value or "\n" in value:
            raise LifeStanceContainmentValidationError(reason_code)
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 80:
            raise LifeStanceContainmentValidationError(reason_code)
        return cleaned
    raise LifeStanceContainmentValidationError(reason_code)


def _clean_single_line(
    value: Any, *, min_len: int, max_len: int, reason_code: str
) -> str:
    if not isinstance(value, str) or "\x00" in value or "\r" in value or "\n" in value:
        raise LifeStanceContainmentValidationError(reason_code)
    cleaned = " ".join(value.split())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise LifeStanceContainmentValidationError(reason_code)
    return cleaned


def _clean_multiline(
    value: Any, *, min_len: int, max_len: int, reason_code: str
) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise LifeStanceContainmentValidationError(reason_code)
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise LifeStanceContainmentValidationError(reason_code)
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
        raise LifeStanceContainmentValidationError(reason_code)
    return value


def _require_int_range(
    value: Any, *, minimum: int, maximum: int, reason_code: str
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LifeStanceContainmentValidationError(reason_code)
    if value < minimum or value > maximum:
        raise LifeStanceContainmentValidationError(reason_code)
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


def _read_character_identity(day_root: Path | str) -> dict[str, str]:
    """Read character identity dynamically with open-source neutral ``TA`` fallback."""
    try:
        from components.phone import sensory_character_view as char_view

        reader = getattr(char_view, "read_character_pack", None)
        pack_info: Mapping[str, Any] = {}
        if callable(reader):
            res = reader(Path(day_root), subject_view=True)
            if isinstance(res, Mapping):
                if isinstance(res.get("active_pack"), Mapping):
                    pack_info = res["active_pack"]
                elif isinstance(res.get("pack_doc"), Mapping):
                    pack_info = res["pack_doc"]
                else:
                    pack_info = res
        else:
            c_state, c_reason = char_view.read_character_pack_state(day_root)
            projected = char_view.project_character_pack(
                c_state, state_reason=c_reason, subject_view=True
            )
            if isinstance(projected.get("active_pack"), Mapping):
                pack_info = projected["active_pack"]
        pack_id = str(pack_info.get("pack_id") or "neutral_resident").strip() or "neutral_resident"
        display_name = str(pack_info.get("display_name") or "TA").strip() or "TA"
        pronouns = str(pack_info.get("pronouns") or "TA").strip() or "TA"
        return {
            "pack_id": pack_id,
            "display_name": display_name,
            "pronouns": pronouns,
        }
    except Exception:
        return {
            "pack_id": "neutral_resident",
            "display_name": "TA",
            "pronouns": "TA",
        }


# ---------------------------------------------------------------------------------------------
# Zero-Write Reading Material / Supply Revision Probing (ADR-047)
# ---------------------------------------------------------------------------------------------


def _safe_read_json_dict(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.exists() or not path.is_file():
            return None
        if path.parent.exists() and path.parent.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError):
        return None


def _extract_material_entry(
    raw: Mapping[str, Any],
    *,
    fallback_scope: Optional[str] = None,
    source_file: str,
) -> Optional[dict[str, Any]]:
    scope = (
        raw.get("scope_ref")
        or raw.get("material_id")
        or raw.get("artifact_id")
        or raw.get("book_id")
        or fallback_scope
    )
    if not isinstance(scope, str) or not LOCAL_ID_RE.match(scope.strip()):
        return None
    clean_scope = scope.strip()

    rev_val = (
        raw.get("source_revision")
        or raw.get("current_source_revision")
        or raw.get("head_revision")
        or raw.get("head_version")
        or raw.get("revision")
        or raw.get("version")
    )
    if isinstance(rev_val, int) and not isinstance(rev_val, bool):
        clean_rev = f"v{rev_val}"
    elif isinstance(rev_val, str) and LOCAL_ID_RE.match(rev_val.strip()):
        clean_rev = rev_val.strip()
    else:
        return None

    withdrawn = bool(
        raw.get("withdrawn", False)
        or raw.get("revoked", False)
        or str(raw.get("status") or "").upper() in ("WITHDRAWN", "REVOKED", "SOURCE_WITHDRAWN")
    )
    title = str(raw.get("title") or clean_scope).strip()[:MAX_TITLE]
    return {
        "scope_ref": clean_scope,
        "source_revision": clean_rev,
        "withdrawn": withdrawn,
        "title": title,
        "source_file": source_file,
    }


def discover_reading_materials(day_root: Path | str) -> dict[str, dict[str, Any]]:
    """Pure zero-write scan of ``<day_root>`` for mounted Supply / reading workbench materials."""
    root = Path(day_root)
    discovered: dict[str, dict[str, Any]] = {}

    def _ingest_doc(doc: Mapping[str, Any], rel_label: str) -> None:
        direct = _extract_material_entry(doc, source_file=rel_label)
        if direct is not None:
            discovered[direct["scope_ref"]] = direct
        for container_key in ("materials", "items", "books", "sources", "entries"):
            sub = doc.get(container_key)
            if isinstance(sub, Mapping):
                for k, v in sub.items():
                    if isinstance(v, Mapping):
                        entry = _extract_material_entry(
                            v, fallback_scope=str(k), source_file=rel_label
                        )
                        if entry is not None:
                            discovered[entry["scope_ref"]] = entry
                    elif isinstance(v, str) and LOCAL_ID_RE.match(str(k).strip()) and LOCAL_ID_RE.match(v.strip()):
                        discovered[str(k).strip()] = {
                            "scope_ref": str(k).strip(),
                            "source_revision": v.strip(),
                            "withdrawn": False,
                            "title": str(k).strip()[:MAX_TITLE],
                            "source_file": rel_label,
                        }
            elif isinstance(sub, list):
                for item in sub[:64]:
                    if isinstance(item, Mapping):
                        entry = _extract_material_entry(item, source_file=rel_label)
                        if entry is not None:
                            discovered[entry["scope_ref"]] = entry

    candidates = [
        root / "phone" / "reading_workbench.json",
        root / "life" / "reading_workbench.json",
        root / "supply" / "materials.json",
        root / "supply" / "reading_materials.json",
        root / "supply" / "catalog.json",
        root / "supply" / "manifest.json",
    ]
    for cand in candidates:
        doc = _safe_read_json_dict(cand)
        if doc is not None:
            _ingest_doc(doc, str(cand.relative_to(root)).replace("\\", "/"))

    supply_dir = root / "supply"
    if supply_dir.exists() and supply_dir.is_dir() and not supply_dir.is_symlink():
        try:
            json_files = sorted(supply_dir.glob("*.json"))[:32]
        except OSError:
            json_files = []
        for jf in json_files:
            if jf in candidates:
                continue
            doc = _safe_read_json_dict(jf)
            if doc is not None:
                _ingest_doc(
                    doc,
                    str(jf.relative_to(root)).replace("\\", "/"),
                )

    return discovered


def probe_reading_material_revision(
    day_root: Path | str, scope_ref: str
) -> Optional[dict[str, Any]]:
    """Probe ``<day_root>`` for a real mounted material matching ``scope_ref`` (zero-write)."""
    clean_scope = _require_id(scope_ref, reason_code="SCOPE_REF_INVALID")
    materials = discover_reading_materials(day_root)
    return materials.get(clean_scope)


# ---------------------------------------------------------------------------------------------
# Default State & Atomic Persistence (<day_root>/life/attention_stance_containment.json)
# ---------------------------------------------------------------------------------------------


def attention_stance_containment_state_path(day_root: Path | str) -> Path:
    return Path(day_root) / STATE_DIRNAME / STATE_FILENAME


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


def _default_cohabitation() -> dict[str, Any]:
    return {
        "suggestions": [],
        "loans": {},
        "lamp_preview": None,
    }


def _validate_obtained_pages_map(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_scope, info in raw.items():
        if not isinstance(raw_scope, str) or not LOCAL_ID_RE.match(raw_scope.strip()):
            continue
        sref = raw_scope.strip()
        if isinstance(info, Mapping):
            pages_raw = info.get("obtained_pages")
            pages = sorted({
                int(p)
                for p in (pages_raw if isinstance(pages_raw, list) else [])
                if not isinstance(p, bool) and isinstance(p, int) and p > 0
            })
            max_p = info.get("max_obtained_page")
            if not isinstance(max_p, bool) and isinstance(max_p, int) and max_p >= 0:
                for p in range(1, max_p + 1):
                    if p not in pages:
                        pages.append(p)
                pages.sort()
            total_p = info.get("total_pages")
            out[sref] = {
                "scope_ref": sref,
                "obtained_pages": pages,
                "total_pages": int(total_p)
                if not isinstance(total_p, bool) and isinstance(total_p, int) and total_p > 0
                else None,
                "enforce_page_gate": True,
                "updated_at": _iso_or_none(info.get("updated_at")),
            }
        elif isinstance(info, list):
            pages = sorted({
                int(p)
                for p in info
                if not isinstance(p, bool) and isinstance(p, int) and p > 0
            })
            out[sref] = {
                "scope_ref": sref,
                "obtained_pages": pages,
                "total_pages": None,
                "enforce_page_gate": True,
                "updated_at": None,
            }
    return out


def _validate_cohabitation(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return _default_cohabitation()
    suggestions_out: list[dict[str, Any]] = []
    raw_suggs = raw.get("suggestions")
    if isinstance(raw_suggs, list):
        for s in raw_suggs[-32:]:
            if not isinstance(s, Mapping) or not isinstance(s.get("suggestion_id"), str):
                continue
            suggestions_out.append({
                "suggestion_id": str(s["suggestion_id"])[:120],
                "actor": str(s.get("actor") or "partner")[:24],
                "target_id": str(s.get("target_id") or "")[:120],
                "suggested_use": str(s.get("suggested_use") or "")[:120],
                "target_container_id": (
                    None
                    if s.get("target_container_id") is None
                    else str(s.get("target_container_id"))[:120]
                ),
                "status": str(s.get("status") or "PENDING")[:32],
                "decision": None if s.get("decision") is None else str(s.get("decision"))[:32],
                "note": None if s.get("note") is None else str(s.get("note"))[:MAX_PUBLIC_NOTE],
                "created_at": _iso_or_none(s.get("created_at")),
                "responded_at": _iso_or_none(s.get("responded_at")),
            })
    loans_out: dict[str, dict[str, Any]] = {}
    raw_loans = raw.get("loans")
    if isinstance(raw_loans, Mapping):
        for k, v in raw_loans.items():
            if not isinstance(k, str) or not isinstance(v, Mapping):
                continue
            loans_out[str(k)[:120]] = {
                "item_id": str(v.get("item_id") or k)[:120],
                "from_side": str(v.get("from_side") or "subject")[:24],
                "to_side": str(v.get("to_side") or "partner")[:24],
                "status": str(v.get("status") or "LENT")[:24],
                "note": None if v.get("note") is None else str(v.get("note"))[:MAX_PUBLIC_NOTE],
                "updated_at": _iso_or_none(v.get("updated_at")),
            }
    lamp_prev = raw.get("lamp_preview")
    validated_lamp: Optional[dict[str, Any]] = None
    if isinstance(lamp_prev, Mapping):
        validated_lamp = {
            "actor": str(lamp_prev.get("actor") or "partner")[:24],
            "from_place_id": str(lamp_prev.get("from_place_id") or "desk_left")[:64],
            "to_place_id": str(lamp_prev.get("to_place_id") or "desk_right")[:64],
            "committed": bool(lamp_prev.get("committed", False)),
            "status": str(lamp_prev.get("status") or "PREVIEW")[:32],
            "note": None
            if lamp_prev.get("note") is None
            else str(lamp_prev.get("note"))[:MAX_PUBLIC_NOTE],
            "updated_at": _iso_or_none(lamp_prev.get("updated_at")),
        }
    return {
        "suggestions": suggestions_out,
        "loans": loans_out,
        "lamp_preview": validated_lamp,
    }


def read_cross_module_hand_locks(
    day_root: Path | str,
    *,
    now_iso: str,
    local_hand_slots: Optional[Mapping[str, Any]] = None,
) -> dict[str, str]:
    """Zero-write probe of ``phone/v03_embodied_social.json`` for unified two-hand interlock."""
    social_path = Path(day_root) / "phone" / "v03_embodied_social.json"
    doc = _safe_read_json_dict(social_path)
    if not isinstance(doc, Mapping):
        return {}
    ec = doc.get("embodied_contact")
    if not isinstance(ec, Mapping):
        return {}

    now_dt, _ = _normalize_now(now_iso)
    contact_expired = False
    active_c = ec.get("active_contact")
    if isinstance(active_c, Mapping) and active_c.get("started_at"):
        try:
            started_dt, _ = _normalize_now(str(active_c["started_at"]))
            if (now_dt - started_dt).total_seconds() >= CONTACT_MAX_NATURAL_SECONDS:
                contact_expired = True
        except Exception:
            contact_expired = False

    raw_slots = ec.get("hand_slots")
    if not isinstance(raw_slots, Mapping):
        return {}
    local_map = local_hand_slots or {}
    locks: dict[str, str] = {}
    for slot in HAND_SLOTS:
        sdoc = raw_slots.get(slot)
        if not isinstance(sdoc, Mapping):
            continue
        st = str(sdoc.get("state") or "free")
        if st == "contact_reserved" and not contact_expired:
            cid = str(sdoc.get("contact_id") or "active_contact")
            locks[slot] = f"contact:{cid}"
        elif st == "holding_object":
            held_id = sdoc.get("object_id") or sdoc.get("held_object_id")
            if isinstance(held_id, str) and held_id.strip():
                clean_held = held_id.strip()
                if local_map.get(slot) != clean_held:
                    locks[slot] = clean_held
    return locks


def sync_embodied_social_hand_slots_if_present(
    day_root: Path | str,
    *,
    hand_slots: Mapping[str, Optional[str]],
    items: list[dict[str, Any]],
    now_iso: str,
) -> None:
    """When ``phone/v03_embodied_social.json`` exists, keep its ``hand_slots`` aligned on writes."""
    social_path = Path(day_root) / "phone" / "v03_embodied_social.json"
    if not social_path.exists() or social_path.is_symlink():
        return
    doc = _safe_read_json_dict(social_path)
    if not isinstance(doc, Mapping):
        return
    ec = doc.get("embodied_contact")
    if not isinstance(ec, dict):
        return
    ec_slots = ec.get("hand_slots")
    if not isinstance(ec_slots, dict):
        return

    item_labels = {str(it["item_id"]): str(it["label"]) for it in items}
    changed = False
    for slot in HAND_SLOTS:
        cur = ec_slots.get(slot)
        if not isinstance(cur, dict):
            continue
        if cur.get("state") == "contact_reserved":
            continue
        local_iid = hand_slots.get(slot)
        cur_held = cur.get("object_id") or cur.get("held_object_id")
        if local_iid is not None:
            new_slot = {
                "state": "holding_object",
                "object_id": local_iid,
                "held_object_id": local_iid,
                "label": item_labels.get(local_iid, local_iid),
                "held_object_label": item_labels.get(local_iid, local_iid),
                "two_handed": False,
                "contact_id": None,
                "contact_kind": None,
                "updated_at": now_iso,
            }
            if cur != new_slot:
                ec_slots[slot] = new_slot
                changed = True
        elif cur.get("state") == "holding_object" and cur_held in item_labels:
            ec_slots[slot] = {
                "state": "free",
                "object_id": None,
                "held_object_id": None,
                "label": None,
                "held_object_label": None,
                "two_handed": False,
                "contact_id": None,
                "contact_kind": None,
                "updated_at": now_iso,
            }
            changed = True

    if changed:
        doc["updated_at"] = now_iso
        try:
            _atomic_write_json(social_path, doc)
        except Exception:
            return


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


def default_attention_stance_containment_state() -> dict[str, Any]:
    return {
        "schema": ATTENTION_STANCE_CONTAINMENT_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "origin_writer": "owner",
        "stance_domain": {
            "stances": [],
            "source_registry": {},
            "withdrawn_sources": {},
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
            "cohabitation": _default_cohabitation(),
        },
        "operation_receipts": [],
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists() and path.is_symlink():
        raise LifeStanceContainmentConflictError("ATTENTION_STANCE_STATE_SYMLINK")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise LifeStanceContainmentConflictError("ATTENTION_STANCE_DIR_SYMLINK")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


# ---------------------------------------------------------------------------------------------
# Topology & Lineage Helpers
# ---------------------------------------------------------------------------------------------


def compute_active_stance_ids(
    stances: list[dict[str, Any]],
    source_registry: Mapping[str, Mapping[str, Any]],
    withdrawn_sources: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Deterministically rebuild active stance list; withdrawn/superseded/stale NEVER resurrect."""
    withdrawn_map = withdrawn_sources or {}
    active_ids: list[str] = []
    for st in stances:
        if st.get("status") != "ACTIVE":
            continue
        if st.get("superseded_by") is not None:
            continue
        scope_ref = str(st.get("scope_ref") or "")
        if scope_ref in withdrawn_map:
            continue
        reg = source_registry.get(scope_ref)
        if isinstance(reg, Mapping):
            if reg.get("withdrawn"):
                continue
            if reg.get("current_source_revision") != st.get("source_revision"):
                continue
        active_ids.append(str(st["stance_id"]))
    return active_ids


def container_ancestor_chain(
    cid: str, containers_by_id: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Return ``[root_cid, ..., cid]`` while rejecting cycles or excessive nesting depth."""
    chain: list[str] = []
    visited: set[str] = set()
    cur: Optional[str] = cid
    while cur is not None:
        if cur in visited:
            raise ValueError(f"container cycle detected at {cur}")
        if cur not in containers_by_id:
            raise ValueError(f"unknown container {cur}")
        visited.add(cur)
        chain.append(cur)
        if len(chain) > MAX_CONTAINER_NESTING_DEPTH:
            raise ValueError("container nesting depth exceeded")
        parent = containers_by_id[cur].get("parent_container_id")
        cur = str(parent) if parent is not None else None
    chain.reverse()
    return chain


def effective_container_place(
    cid: str, containers_by_id: Mapping[str, Mapping[str, Any]]
) -> str:
    chain = container_ancestor_chain(cid, containers_by_id)
    root_c = containers_by_id[chain[0]]
    return str(root_c["place_id"])


def check_container_access_gate(
    cid: str,
    containers_by_id: Mapping[str, Mapping[str, Any]],
    *,
    include_self: bool = True,
) -> Optional[str]:
    """Return ``CONTAINER_LOCKED`` or ``CONTAINER_CLOSED`` if any gate along the chain blocks access."""
    chain = container_ancestor_chain(cid, containers_by_id)
    check_ids = chain if include_self else chain[:-1]
    for check_id in check_ids:
        open_st = str(containers_by_id[check_id].get("open_state") or "open_top")
        if open_st == "locked":
            return "CONTAINER_LOCKED"
        if open_st == "closed":
            return "CONTAINER_CLOSED"
    return None


def descendant_container_ids(
    root_cid: str, containers: list[dict[str, Any]]
) -> list[str]:
    """Return all nested child/descendant container IDs beneath ``root_cid``."""
    by_id = {c["container_id"]: c for c in containers}
    descendants: list[str] = []
    for c in containers:
        cid = c["container_id"]
        if cid == root_cid:
            continue
        chain = container_ancestor_chain(cid, by_id)
        if root_cid in chain:
            descendants.append(cid)
    return descendants


# ---------------------------------------------------------------------------------------------
# State Validation
# ---------------------------------------------------------------------------------------------


def _validate_owner_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("owner state not an object")
    if raw.get("schema") != ATTENTION_STANCE_CONTAINMENT_SCHEMA:
        raise ValueError("owner schema mismatch")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("owner schema_version mismatch")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    origin_writer = str(raw.get("origin_writer") or "owner")
    if origin_writer not in ("owner", "phone_sync"):
        origin_writer = "owner"

    stance_dom = raw.get("stance_domain")
    if not isinstance(stance_dom, Mapping):
        raise ValueError("invalid stance_domain")
    stances_raw = stance_dom.get("stances")
    src_reg_raw = stance_dom.get("source_registry")
    withdrawn_raw = stance_dom.get("withdrawn_sources") or {}
    if not isinstance(stances_raw, list) or len(stances_raw) > MAX_STANCES:
        raise ValueError("invalid stances list")
    if not isinstance(src_reg_raw, Mapping) or not isinstance(withdrawn_raw, Mapping):
        raise ValueError("invalid source_registry / withdrawn_sources")

    validated_withdrawn: dict[str, dict[str, Any]] = {}
    for raw_scope, w_info in withdrawn_raw.items():
        scope = _require_id(raw_scope, reason_code="SCOPE_REF_INVALID")
        if isinstance(w_info, Mapping):
            validated_withdrawn[scope] = {
                "scope_ref": scope,
                "withdrawn_at": _iso_or_none(w_info.get("withdrawn_at")),
                "reason": (
                    None
                    if w_info.get("reason") is None
                    else str(w_info.get("reason"))[:MAX_PUBLIC_NOTE]
                ),
                "last_known_revision": (
                    None
                    if w_info.get("last_known_revision") is None
                    else str(w_info.get("last_known_revision"))[:120]
                ),
            }
        else:
            validated_withdrawn[scope] = {
                "scope_ref": scope,
                "withdrawn_at": None,
                "reason": None,
                "last_known_revision": None,
            }

    validated_registry: dict[str, dict[str, Any]] = {}
    for raw_scope, info in src_reg_raw.items():
        scope = _require_id(raw_scope, reason_code="SCOPE_REF_INVALID")
        if not isinstance(info, Mapping):
            raise ValueError("invalid source_registry entry")
        cur_rev = _require_id(
            info.get("current_source_revision"), reason_code="SOURCE_REVISION_INVALID"
        )
        withdrawn = bool(info.get("withdrawn", False)) or (scope in validated_withdrawn)
        validated_registry[scope] = {
            "scope_ref": scope,
            "current_source_revision": cur_rev,
            "withdrawn": withdrawn,
            "updated_at": _iso_or_none(info.get("updated_at")),
            "withdrawn_at": _iso_or_none(info.get("withdrawn_at")),
            "note": (
                None
                if info.get("note") is None
                else str(info.get("note"))[:MAX_PUBLIC_NOTE]
            ),
            "withdraw_reason": (
                None
                if info.get("withdraw_reason") is None
                else str(info.get("withdraw_reason"))[:MAX_PUBLIC_NOTE]
            ),
        }
        if withdrawn and scope not in validated_withdrawn:
            validated_withdrawn[scope] = {
                "scope_ref": scope,
                "withdrawn_at": _iso_or_none(info.get("withdrawn_at")),
                "reason": validated_registry[scope]["withdraw_reason"],
                "last_known_revision": cur_rev,
            }

    for scope, w_doc in validated_withdrawn.items():
        if scope not in validated_registry:
            validated_registry[scope] = {
                "scope_ref": scope,
                "current_source_revision": str(w_doc.get("last_known_revision") or "revoked"),
                "withdrawn": True,
                "updated_at": w_doc.get("withdrawn_at"),
                "withdrawn_at": w_doc.get("withdrawn_at"),
                "note": None,
                "withdraw_reason": w_doc.get("reason"),
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
        if (
            scope_ref in validated_withdrawn
            or validated_registry.get(scope_ref, {}).get("withdrawn")
        ):
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
            excerpt_val: Optional[str] = None
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
            excerpt_val = (
                None
                if item.get("cached_excerpt") is None
                else _clean_multiline(
                    item.get("cached_excerpt"),
                    min_len=1,
                    max_len=MAX_EXCERPT_TEXT,
                    reason_code="CACHED_EXCERPT_INVALID",
                )
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
            "cached_excerpt": excerpt_val,
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

    rebuilt_active_ids = compute_active_stance_ids(
        validated_stances, validated_registry, validated_withdrawn
    )

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
            raise ValueError(f"invalid open_state {open_st}")
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

    child_counts: dict[str, int] = {cid: 0 for cid in container_map}
    for c_doc in validated_containers:
        eff_p = effective_container_place(c_doc["container_id"], container_map)
        c_doc["place_id"] = eff_p
        c_doc["room_id"] = _room_for_place(eff_p)
        pcid = c_doc["parent_container_id"]
        if pcid is not None:
            child_counts[pcid] += 1
            if child_counts[pcid] > container_map[pcid]["capacity"]:
                raise ValueError("nested container exceeds parent capacity")

    validated_items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
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
            eff_place = effective_container_place(pcid, container_map)
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
        if raw_hands.get(slot) != derived_hands[slot]:
            raise ValueError("hand_slots out of sync with items")

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
        validated_receipts.append({
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
        })

    validated_obtained_pages = _validate_obtained_pages_map(
        stance_dom.get("obtained_pages_by_scope")
    )
    validated_cohabitation = _validate_cohabitation(cont_dom.get("cohabitation"))

    return {
        "schema": ATTENTION_STANCE_CONTAINMENT_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_or_none(raw.get("updated_at")),
        "origin_writer": origin_writer,
        "stance_domain": {
            "stances": validated_stances,
            "source_registry": validated_registry,
            "withdrawn_sources": validated_withdrawn,
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
        "operation_receipts": validated_receipts,
    }


def read_attention_stance_containment_state(
    day_root: Path | str,
) -> tuple[dict[str, Any], Optional[str], bool]:
    """Pure zero-write state load. Returns ``(state, error_reason, exists_on_disk)``."""
    path = attention_stance_containment_state_path(day_root)
    if path.exists() and path.is_symlink():
        return default_attention_stance_containment_state(), "ATTENTION_STANCE_STATE_SYMLINK", True
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        return default_attention_stance_containment_state(), "ATTENTION_STANCE_DIR_SYMLINK", True
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_attention_stance_containment_state(), None, False
    except OSError:
        return (
            default_attention_stance_containment_state(),
            "ATTENTION_STANCE_STATE_UNREADABLE",
            True,
        )
    try:
        parsed = json.loads(raw_text)
        return _validate_owner_state(parsed), None, True
    except Exception:
        return (
            default_attention_stance_containment_state(),
            "ATTENTION_STANCE_STATE_CORRUPT",
            True,
        )


def write_attention_stance_containment_state(
    day_root: Path | str, state: Mapping[str, Any]
) -> dict[str, Any]:
    doc = dict(state)
    doc["schema"] = ATTENTION_STANCE_CONTAINMENT_SCHEMA
    doc["schema_version"] = STATE_SCHEMA_VERSION
    validated = _validate_owner_state(doc)
    return _atomic_write_json(attention_stance_containment_state_path(day_root), validated)


# ---------------------------------------------------------------------------------------------
# Pure Read Projection
# ---------------------------------------------------------------------------------------------


def _project_owner_stance_row(
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
        row["cached_excerpt"] = st.get("cached_excerpt")
    return row


def project_attention_stance_containment(
    day_root: Path | str,
    state: Mapping[str, Any],
    *,
    now_iso: str,
    subject_view: bool,
    state_reason: Optional[str] = None,
) -> dict[str, Any]:
    char_info = _read_character_identity(day_root)
    name = char_info["display_name"]
    discovered_materials = discover_reading_materials(day_root)

    # 1. Stance Revisions Projection
    stance_dom = state["stance_domain"]
    raw_stances: list[dict[str, Any]] = list(stance_dom["stances"])
    source_reg: dict[str, dict[str, Any]] = dict(stance_dom["source_registry"])
    withdrawn_sources: dict[str, dict[str, Any]] = dict(
        stance_dom.get("withdrawn_sources") or {}
    )
    obtained_pages_map = _validate_obtained_pages_map(
        stance_dom.get("obtained_pages_by_scope")
    )
    active_ids_set = set(
        compute_active_stance_ids(raw_stances, source_reg, withdrawn_sources)
    )
    by_id = {st["stance_id"]: st for st in raw_stances}

    withdrawn_scopes = sorted(
        set(withdrawn_sources.keys())
        | {scope for scope, info in source_reg.items() if info.get("withdrawn")}
    )

    current_stances: list[dict[str, Any]] = []
    lineage_history: list[dict[str, Any]] = []
    stale_stances: list[dict[str, Any]] = []

    for st in raw_stances:
        status = st["status"]
        if status == "SOURCE_WITHDRAWN" or st["scope_ref"] in withdrawn_scopes:
            continue
        if not subject_view and not st.get("shared_with_partner"):
            continue
        projected_row = _project_owner_stance_row(
            st, subject_view=subject_view, all_by_id=by_id
        )
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
        "withdrawn_sources": copy.deepcopy(withdrawn_sources) if subject_view else {
            k: {"scope_ref": k, "withdrawn_at": v.get("withdrawn_at")}
            for k, v in withdrawn_sources.items()
        },
        "mounted_reading_materials": sorted(discovered_materials.keys()),
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

    # 2. World Nested Containment & Spatial Impressions Projection
    cont_dom = state["containment_domain"]
    subj_place = cont_dom["subject_place_id"]
    subj_room = cont_dom["subject_room_id"]
    subj_present = bool(cont_dom["subject_present"])
    local_hand_slots = dict(cont_dom["hand_slots"])
    ext_hand_locks = read_cross_module_hand_locks(
        day_root, now_iso=now_iso, local_hand_slots=local_hand_slots
    )
    hand_slots = dict(local_hand_slots)
    for slot, lock_val in ext_hand_locks.items():
        if hand_slots.get(slot) is None:
            hand_slots[slot] = lock_val

    containers_by_id = {c["container_id"]: c for c in cont_dom["containers"]}
    child_items_by_cid: dict[str, list[str]] = {cid: [] for cid in containers_by_id}
    child_containers_by_cid: dict[str, list[str]] = {cid: [] for cid in containers_by_id}
    for c in cont_dom["containers"]:
        pcid = c.get("parent_container_id")
        if pcid in child_containers_by_cid:
            child_containers_by_cid[pcid].append(c["container_id"])
    for it in cont_dom["items"]:
        if it["placement_kind"] == "container" and it["parent_container_id"] in child_items_by_cid:
            child_items_by_cid[it["parent_container_id"]].append(it["item_id"])

    projected_containers: list[dict[str, Any]] = []
    max_observed_depth = 1
    for c in cont_dom["containers"]:
        cid = c["container_id"]
        chain = container_ancestor_chain(cid, containers_by_id)
        if len(chain) > max_observed_depth:
            max_observed_depth = len(chain)
        phys_place = effective_container_place(cid, containers_by_id)
        c_children = list(child_items_by_cid[cid])
        nested_cids = list(child_containers_by_cid[cid])
        occupied = len(c_children) + len(nested_cids)
        cap = int(c["capacity"])
        last_seen = c["subject_last_seen_place"]
        is_stale = phys_place != last_seen
        open_st = str(c.get("open_state") or "open_top")
        gate_block = check_container_access_gate(cid, containers_by_id, include_self=True)
        reachable = bool(
            subj_present and phys_place == subj_place and gate_block is None
        )

        if c.get("drinkware"):
            derived_use = "empty_drinkable" if occupied == 0 and open_st in ("open_top", "open") else "holding_items"
            can_pour_drink = occupied == 0 and open_st in ("open_top", "open")
        else:
            derived_use = "empty_container" if occupied == 0 else "holding_items"
            can_pour_drink = False

        c_row: dict[str, Any] = {
            "container_id": cid,
            "label": c["label"],
            "place_id": phys_place,
            "effective_place_id": phys_place,
            "room_id": _room_for_place(phys_place),
            "parent_container_id": c.get("parent_container_id"),
            "nesting_depth": len(chain),
            "open_state": open_st,
            "capacity": cap,
            "occupied_slots": occupied,
            "remaining_capacity": cap - occupied,
            "is_full": occupied >= cap,
            "accepted_classes": list(c["accepted_classes"]),
            "child_item_ids": c_children,
            "child_container_ids": nested_cids,
            "derived_use": derived_use,
            "can_pour_drink_now": can_pour_drink,
            "reachable_now": reachable,
            "access_blocked_reason": gate_block,
            "subject_last_seen_place": last_seen,
            "subject_last_seen_at": c.get("subject_last_seen_at"),
            "impression_stale": is_stale,
            "not_at_last_seen_place": bool(c.get("not_at_last_seen_place", False)),
        }
        if subject_view:
            c_row["private_impression_note"] = c.get("private_impression_note")
        projected_containers.append(c_row)

    projected_items: list[dict[str, Any]] = []
    subject_cognitive_cues: list[str] = []
    for it in cont_dom["items"]:
        pkind = it["placement_kind"]
        gate_block_it: Optional[str] = None
        if pkind == "container" and it["parent_container_id"] in containers_by_id:
            phys_place = effective_container_place(it["parent_container_id"], containers_by_id)
            gate_block_it = check_container_access_gate(
                it["parent_container_id"], containers_by_id, include_self=True
            )
        elif pkind == "hand":
            phys_place = subj_place
        else:
            phys_place = it["place_id"]

        last_seen = it["subject_last_seen_place"]
        is_stale = phys_place != last_seen
        reachable = bool(
            pkind == "hand"
            or (subj_present and phys_place == subj_place and gate_block_it is None)
        )

        i_row: dict[str, Any] = {
            "item_id": it["item_id"],
            "label": it["label"],
            "item_class": it["item_class"],
            "owner": it["owner"],
            "placement_kind": pkind,
            "place_id": phys_place,
            "effective_place_id": phys_place,
            "room_id": _room_for_place(phys_place),
            "parent_container_id": it["parent_container_id"],
            "held_in_hand": it["held_in_hand"],
            "reachable_now": reachable,
            "access_blocked_reason": gate_block_it,
            "subject_last_seen_place": last_seen,
            "subject_last_seen_at": it.get("subject_last_seen_at"),
            "impression_stale": is_stale,
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
        projected_items.append(i_row)

    cohabitation_proj = copy.deepcopy(
        cont_dom.get("cohabitation") or _default_cohabitation()
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
        "stale_impression_count": sum(
            1 for row in (projected_containers + projected_items) if row["impression_stale"]
        ),
        "no_fake_rollback": True,
        "no_background_omniscience": True,
        "nested_containment_supported": True,
        "max_containment_depth": max(max_observed_depth, 1),
    }
    if subject_view:
        containment_block["subject_cognitive_cues"] = subject_cognitive_cues

    obtained_idx = attn_proj.discover_obtained_pages_index(
        day_root, extra_obtained_by_scope=obtained_pages_map
    )
    merged_materials_for_attn: dict[str, dict[str, Any]] = {}
    for sref, minfo in discovered_materials.items():
        merged_materials_for_attn[sref] = {
            **minfo,
            "obtained_pages": obtained_idx.get(sref, {}).get("obtained_pages", []),
        }
    for sref, oinfo in obtained_idx.items():
        if sref not in merged_materials_for_attn:
            merged_materials_for_attn[sref] = dict(oinfo)

    out_doc: dict[str, Any] = {
        "schema": ATTENTION_STANCE_CONTAINMENT_SCHEMA,
        "read_only": True,
        "view": "subject" if subject_view else "partner_default",
        "subject_view": bool(subject_view),
        "observed_at": now_iso,
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "state_status": "available" if state_reason is None else "degraded",
        "state_reason_code": state_reason,
        "llm_calls": 0,
        "character": char_info,
        "stance_revisions": stance_block,
        "object_containment": containment_block,
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
        },
    }
    if subject_view:
        out_doc["attention_view"] = attn_proj.build_attention_view(
            {
                "subject_ref": f"character:{char_info.get('pack_id') or 'ta'}",
                "stance_revisions": stance_block,
                "object_containment": containment_block,
                "reading_materials": merged_materials_for_attn,
            },
            now=now_iso,
        )
    return out_doc


def read_attention_stance_containment(
    day_root: Path | str,
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Pure zero-write read of ``life.v03.attention_stance_containment.v1``."""
    _now_dt, now_iso = _normalize_now(now)
    state, state_reason, _exists = read_attention_stance_containment_state(day_root)
    return project_attention_stance_containment(
        day_root,
        state,
        now_iso=now_iso,
        subject_view=bool(subject_view),
        state_reason=state_reason,
    )


# ---------------------------------------------------------------------------------------------
# Synchronization Helpers Between Owner Ledger (life/) and Phone View (phone/)
# ---------------------------------------------------------------------------------------------


def sync_owner_domains_from_phone_state(
    day_root: Path | str,
    phone_state: Mapping[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    """Persist stance & containment slices from ``phone/stance_containment.json`` into owner ledger."""
    existing, reason, exists_on_disk = read_attention_stance_containment_state(day_root)
    if reason is not None:
        raise LifeStanceContainmentConflictError(reason)

    phone_stance = phone_state.get("stance_domain") or {}
    phone_cont = phone_state.get("containment_domain") or {}
    existing_withdrawn = copy.deepcopy(
        existing["stance_domain"].get("withdrawn_sources") or {}
    )
    existing_excerpts = {
        s["stance_id"]: s.get("cached_excerpt")
        for s in existing["stance_domain"].get("stances", [])
    }

    merged_withdrawn = dict(existing_withdrawn)
    for scope, reg in (phone_stance.get("source_registry") or {}).items():
        if isinstance(reg, Mapping) and reg.get("withdrawn"):
            merged_withdrawn[str(scope)] = {
                "scope_ref": str(scope),
                "withdrawn_at": reg.get("withdrawn_at") or now_iso,
                "reason": reg.get("withdraw_reason"),
                "last_known_revision": reg.get("current_source_revision"),
            }

    merged_stances: list[dict[str, Any]] = []
    for s in phone_stance.get("stances") or []:
        s_copy = copy.deepcopy(dict(s))
        if s_copy.get("status") == "SOURCE_WITHDRAWN" or s_copy.get("scope_ref") in merged_withdrawn:
            s_copy["cached_excerpt"] = None
        elif "cached_excerpt" not in s_copy:
            s_copy["cached_excerpt"] = existing_excerpts.get(s_copy.get("stance_id"))
        merged_stances.append(s_copy)

    merged_containers: list[dict[str, Any]] = []
    existing_containers_by_id = {
        c["container_id"]: c for c in existing["containment_domain"].get("containers", [])
    }
    for c in phone_cont.get("containers") or []:
        c_copy = copy.deepcopy(dict(c))
        prev_c = existing_containers_by_id.get(c_copy.get("container_id"), {})
        if "open_state" not in c_copy or not c_copy["open_state"]:
            c_copy["open_state"] = prev_c.get("open_state") or c_copy.get("contents_visibility") or "open_top"
        if "parent_container_id" not in c_copy:
            c_copy["parent_container_id"] = prev_c.get("parent_container_id")
        merged_containers.append(c_copy)

    merged_obtained_pages = copy.deepcopy(
        existing["stance_domain"].get("obtained_pages_by_scope") or {}
    )
    if isinstance(phone_stance.get("obtained_pages_by_scope"), Mapping):
        merged_obtained_pages.update(
            copy.deepcopy(dict(phone_stance["obtained_pages_by_scope"]))
        )

    merged_cohabitation = _validate_cohabitation(
        phone_cont.get("cohabitation")
        if "cohabitation" in phone_cont
        else existing["containment_domain"].get("cohabitation")
    )

    next_rev = max(
        int(phone_state.get("revision") or 0),
        (int(existing.get("revision") or 0) + 1) if exists_on_disk else 1,
    )
    owner_doc = {
        "schema": ATTENTION_STANCE_CONTAINMENT_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "origin_writer": "phone_sync",
        "stance_domain": {
            "stances": merged_stances,
            "source_registry": copy.deepcopy(dict(phone_stance.get("source_registry") or {})),
            "withdrawn_sources": merged_withdrawn,
            "active_stance_ids": list(phone_stance.get("active_stance_ids") or []),
            "obtained_pages_by_scope": merged_obtained_pages,
        },
        "containment_domain": {
            "subject_place_id": phone_cont.get("subject_place_id", "study_desk"),
            "subject_room_id": phone_cont.get("subject_room_id", "study_room"),
            "subject_present": bool(phone_cont.get("subject_present", True)),
            "hand_slots": copy.deepcopy(dict(phone_cont.get("hand_slots") or {"left_hand": None, "right_hand": None})),
            "containers": merged_containers,
            "items": copy.deepcopy(list(phone_cont.get("items") or [])),
            "cohabitation": merged_cohabitation,
        },
        "operation_receipts": copy.deepcopy(
            list(phone_state.get("operation_receipts") or existing.get("operation_receipts") or [])
        )[-MAX_OPERATION_RECEIPTS:],
    }
    return write_attention_stance_containment_state(day_root, owner_doc)


def _sync_phone_state_if_present(
    day_root: Path | str, owner_state: Mapping[str, Any], *, now_iso: str
) -> None:
    """When owner mutates directly and ``phone/stance_containment.json`` exists, keep it aligned."""
    phone_path = Path(day_root) / "phone" / "stance_containment.json"
    if not phone_path.exists() or phone_path.is_symlink():
        return
    try:
        from components.phone import stance_containment_view as scv

        p_state, p_reason = scv.read_stance_containment_state(day_root)
        if p_reason is not None:
            return
        p_state["stance_domain"] = {
            "stances": [
                {k: v for k, v in copy.deepcopy(s).items() if k != "cached_excerpt"}
                for s in owner_state["stance_domain"]["stances"]
            ],
            "source_registry": copy.deepcopy(owner_state["stance_domain"]["source_registry"]),
            "active_stance_ids": list(owner_state["stance_domain"]["active_stance_ids"]),
            "obtained_pages_by_scope": copy.deepcopy(
                owner_state["stance_domain"].get("obtained_pages_by_scope") or {}
            ),
        }
        p_state["containment_domain"] = copy.deepcopy(owner_state["containment_domain"])
        p_state["revision"] = max(int(p_state.get("revision") or 0) + 1, int(owner_state["revision"]))
        p_state["updated_at"] = now_iso
        scv.write_stance_containment_state(day_root, p_state)
    except Exception:
        return


# ---------------------------------------------------------------------------------------------
# Public Mutation Entrypoint
# ---------------------------------------------------------------------------------------------


def _lookup_receipt(
    receipts: list[dict[str, Any]],
    *,
    operation_key: str,
    action: str,
    fingerprint: str,
) -> Optional[dict[str, Any]]:
    for rec in receipts:
        if rec["operation_key"] == operation_key:
            if rec["action"] != action or rec["fingerprint"] != fingerprint:
                raise LifeStanceContainmentConflictError("OPERATION_KEY_CONFLICT")
            return rec
    return None


def _record_receipt(
    receipts: list[dict[str, Any]],
    *,
    operation_key: Optional[str],
    action: str,
    fingerprint: str,
    now_iso: str,
    scope_ref: Optional[str] = None,
    target_id: Optional[str] = None,
) -> None:
    if operation_key is None:
        return
    receipts.append({
        "operation_key": operation_key,
        "action": action,
        "fingerprint": fingerprint,
        "scope_ref": scope_ref,
        "target_id": target_id,
        "recorded_at": now_iso,
    })
    while len(receipts) > MAX_OPERATION_RECEIPTS:
        receipts.pop(0)


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
        raise LifeStanceContainmentValidationError(f"{key.upper()}_INVALID")
    if actor == "subject" and not subject_view:
        raise LifeStanceContainmentPermissionError("SUBJECT_CAPABILITY_REQUIRED")
    return actor


def refresh_room_impressions(
    cont_dom: dict[str, Any],
    *,
    room_id: str,
    now_iso: str,
    scope: str = "room",
    place_id: Optional[str] = None,
    container_id: Optional[str] = None,
) -> dict[str, Any]:
    """Refresh subject spatial impressions with bounded scope (room / micro_zone / container)."""
    if scope not in OBSERVE_SCOPES:
        raise LifeStanceContainmentValidationError("OBSERVE_SCOPE_INVALID")

    refreshed_containers: list[str] = []
    refreshed_items: list[str] = []
    missing_from_old_spot: list[str] = []

    containers_by_id = {c["container_id"]: c for c in cont_dom["containers"]}

    if scope == "container":
        if not container_id or container_id not in containers_by_id:
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        gate_err = check_container_access_gate(
            container_id, containers_by_id, include_self=True
        )
        if gate_err is not None:
            raise LifeStanceContainmentConflictError(gate_err)
        target_cids = {container_id} | set(
            descendant_container_ids(container_id, cont_dom["containers"])
        )
        for cid in sorted(target_cids):
            c = containers_by_id[cid]
            phys_place = effective_container_place(cid, containers_by_id)
            c["place_id"] = phys_place
            c["room_id"] = _room_for_place(phys_place)
            anc_blocked = check_container_access_gate(
                cid, containers_by_id, include_self=False
            )
            if anc_blocked is None:
                c["subject_last_seen_place"] = phys_place
                c["subject_last_seen_at"] = now_iso
                c["not_at_last_seen_place"] = False
                refreshed_containers.append(cid)
        for it in cont_dom["items"]:
            if it["placement_kind"] == "container" and it["parent_container_id"] in target_cids:
                pcid = it["parent_container_id"]
                phys_place = effective_container_place(pcid, containers_by_id)
                it["place_id"] = phys_place
                if check_container_access_gate(pcid, containers_by_id, include_self=True) is None:
                    it["subject_last_seen_place"] = phys_place
                    it["subject_last_seen_at"] = now_iso
                    it["not_at_last_seen_place"] = False
                    refreshed_items.append(it["item_id"])
        return {
            "observed_scope": "container",
            "observed_room_id": room_id,
            "observed_container_id": container_id,
            "refreshed_container_ids": refreshed_containers,
            "refreshed_item_ids": refreshed_items,
            "missing_from_last_seen_ids": missing_from_old_spot,
        }

    target_micro_place = place_id or cont_dom["subject_place_id"]

    for c in cont_dom["containers"]:
        cid = c["container_id"]
        phys_place = effective_container_place(cid, containers_by_id)
        c["place_id"] = phys_place
        c["room_id"] = _room_for_place(phys_place)
        c_room = c["room_id"]
        last_seen_room = _room_for_place(c["subject_last_seen_place"])
        ancestor_blocked = check_container_access_gate(
            cid, containers_by_id, include_self=False
        )
        if scope == "micro_zone":
            if phys_place == target_micro_place and ancestor_blocked is None:
                c["subject_last_seen_place"] = phys_place
                c["subject_last_seen_at"] = now_iso
                c["not_at_last_seen_place"] = False
                refreshed_containers.append(cid)
            elif c["subject_last_seen_place"] == target_micro_place and phys_place != target_micro_place:
                c["not_at_last_seen_place"] = True
                missing_from_old_spot.append(cid)
        else:
            if c_room == room_id and ancestor_blocked is None:
                c["subject_last_seen_place"] = phys_place
                c["subject_last_seen_at"] = now_iso
                c["not_at_last_seen_place"] = False
                refreshed_containers.append(cid)
            elif last_seen_room == room_id and c_room != room_id:
                c["not_at_last_seen_place"] = True
                missing_from_old_spot.append(cid)

    for it in cont_dom["items"]:
        if it["placement_kind"] == "hand":
            it["place_id"] = cont_dom["subject_place_id"]
            it["subject_last_seen_place"] = cont_dom["subject_place_id"]
            it["subject_last_seen_at"] = now_iso
            it["not_at_last_seen_place"] = False
            continue

        if it["placement_kind"] == "container" and it["parent_container_id"] in containers_by_id:
            pcid = it["parent_container_id"]
            phys_place = effective_container_place(pcid, containers_by_id)
            it["place_id"] = phys_place
            gate_blocked = check_container_access_gate(
                pcid, containers_by_id, include_self=True
            )
            visible_in_parent = gate_blocked is None
        else:
            phys_place = it["place_id"]
            visible_in_parent = True

        it_room = _room_for_place(phys_place)
        last_seen_room = _room_for_place(it["subject_last_seen_place"])
        if scope == "micro_zone":
            if phys_place == target_micro_place and visible_in_parent:
                it["subject_last_seen_place"] = phys_place
                it["subject_last_seen_at"] = now_iso
                it["not_at_last_seen_place"] = False
                refreshed_items.append(it["item_id"])
            elif it["subject_last_seen_place"] == target_micro_place and (
                phys_place != target_micro_place or not visible_in_parent
            ):
                it["not_at_last_seen_place"] = True
                missing_from_old_spot.append(it["item_id"])
        else:
            if it_room == room_id and visible_in_parent:
                it["subject_last_seen_place"] = phys_place
                it["subject_last_seen_at"] = now_iso
                it["not_at_last_seen_place"] = False
                refreshed_items.append(it["item_id"])
            elif last_seen_room == room_id and it_room != room_id:
                it["not_at_last_seen_place"] = True
                missing_from_old_spot.append(it["item_id"])

    return {
        "observed_scope": scope,
        "observed_room_id": room_id,
        "observed_place_id": target_micro_place if scope == "micro_zone" else None,
        "refreshed_container_ids": refreshed_containers,
        "refreshed_item_ids": refreshed_items,
        "missing_from_last_seen_ids": missing_from_old_spot,
    }


def apply_attention_stance_containment_action(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Validate and apply one explicit action on ``life/attention_stance_containment.json``."""
    if not isinstance(payload, Mapping):
        raise LifeStanceContainmentValidationError("PAYLOAD_NOT_OBJECT")

    _now_dt, now_iso = _normalize_now(now)

    for raw_k in payload.keys():
        if not isinstance(raw_k, str) or not raw_k.strip():
            raise LifeStanceContainmentValidationError("EMPTY_FIELD_NAME")
        if raw_k in FORBIDDEN_FOREIGN_KEYS:
            raise LifeStanceContainmentValidationError(f"UNKNOWN_FIELD:{raw_k}")

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        raise LifeStanceContainmentValidationError("ACTION_REQUIRED")
    if raw_action != raw_action.strip() or "\x00" in raw_action:
        raise LifeStanceContainmentValidationError("ACTION_INVALID")
    action = raw_action.strip()
    if action not in OWNER_ACTION_ALLOWED_FIELDS:
        raise LifeStanceContainmentValidationError(f"ACTION_INVALID:{action}")

    allowed_fields = OWNER_ACTION_ALLOWED_FIELDS[action]
    extra_fields = sorted(set(payload.keys()) - set(allowed_fields))
    if extra_fields:
        raise LifeStanceContainmentValidationError(f"UNKNOWN_FIELD:{extra_fields[0]}")

    for k, v in payload.items():
        if k == "action":
            continue
        if k == "parent_container_id" and v is None and action in ("register_container", "nest_container"):
            continue
        if v is None:
            raise LifeStanceContainmentValidationError(f"NULL_FIELD_NOT_ALLOWED:{k}")
        if isinstance(v, str) and not v.strip():
            raise LifeStanceContainmentValidationError(f"EMPTY_STRING_FIELD:{k}")

    if action in OWNER_SUBJECT_ONLY_ACTIONS and not subject_view:
        raise LifeStanceContainmentPermissionError("SUBJECT_CAPABILITY_REQUIRED")

    current_state, state_reason, _exists = read_attention_stance_containment_state(day_root)
    if state_reason is not None:
        raise LifeStanceContainmentConflictError(state_reason)

    state = copy.deepcopy(current_state)
    state["origin_writer"] = "owner"

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
        if existing_rec is not None:
            rec_scope = existing_rec.get("scope_ref")
            if (
                action != "revoke_stance_source"
                and rec_scope
                and (
                    rec_scope in state["stance_domain"]["withdrawn_sources"]
                    or state["stance_domain"]["source_registry"]
                    .get(rec_scope, {})
                    .get("withdrawn")
                )
            ):
                raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
            proj = project_attention_stance_containment(
                day_root,
                state,
                now_iso=now_iso,
                subject_view=bool(subject_view),
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

    if expected_rev is not None and int(state["revision"]) != expected_rev:
        raise LifeStanceContainmentConflictError("REVISION_CONFLICT")

    def _commit(
        extra: Mapping[str, Any],
        *,
        status_label: str = "applied",
        ok_flag: bool = True,
        reason_code: Optional[str] = None,
        receipt_scope: Optional[str] = None,
        receipt_target: Optional[str] = None,
    ) -> dict[str, Any]:
        state["stance_domain"]["active_stance_ids"] = compute_active_stance_ids(
            state["stance_domain"]["stances"],
            state["stance_domain"]["source_registry"],
            state["stance_domain"]["withdrawn_sources"],
        )
        state["revision"] = int(state["revision"]) + 1
        state["updated_at"] = now_iso
        _record_receipt(
            state["operation_receipts"],
            operation_key=op_key,
            action=action,
            fingerprint=fp,
            now_iso=now_iso,
            scope_ref=receipt_scope,
            target_id=receipt_target,
        )
        written = write_attention_stance_containment_state(day_root, state)
        _sync_phone_state_if_present(day_root, written, now_iso=now_iso)
        sync_embodied_social_hand_slots_if_present(
            day_root,
            hand_slots=written["containment_domain"]["hand_slots"],
            items=written["containment_domain"]["items"],
            now_iso=now_iso,
        )
        projected = project_attention_stance_containment(
            day_root,
            written,
            now_iso=now_iso,
            subject_view=bool(subject_view),
        )
        result = dict(projected)
        result["ok"] = ok_flag
        result["status"] = status_label
        result["action"] = action
        result["changed"] = True
        result["replayed"] = False
        result["reason_code"] = reason_code
        result.update(dict(extra))
        return result

    stance_dom = state["stance_domain"]
    stances: list[dict[str, Any]] = stance_dom["stances"]
    src_reg: dict[str, dict[str, Any]] = stance_dom["source_registry"]
    withdrawn_sources: dict[str, dict[str, Any]] = stance_dom["withdrawn_sources"]
    obtained_pages_by_scope: dict[str, dict[str, Any]] = stance_dom.setdefault(
        "obtained_pages_by_scope", {}
    )

    cont_dom = state["containment_domain"]
    containers: list[dict[str, Any]] = cont_dom["containers"]
    items: list[dict[str, Any]] = cont_dom["items"]
    hand_slots: dict[str, Optional[str]] = cont_dom["hand_slots"]
    cohabitation: dict[str, Any] = cont_dom.setdefault(
        "cohabitation", _default_cohabitation()
    )

    # =========================================================================================
    # ADR-047: STANCE REVISIONS & READING MATERIAL ANCHOR
    # =========================================================================================

    if action == "record_obtained_pages":
        scope_ref = _require_id(payload.get("scope_ref"), reason_code="SCOPE_REF_INVALID")
        if scope_ref in withdrawn_sources or src_reg.get(scope_ref, {}).get("withdrawn"):
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if "obtained_pages" not in payload and "max_obtained_page" not in payload:
            raise LifeStanceContainmentValidationError("OBTAINED_PAGES_REQUIRED")
        prev_info = obtained_pages_by_scope.get(scope_ref, {})
        pages_set: set[int] = set(prev_info.get("obtained_pages") or [])
        if "obtained_pages" in payload:
            raw_list = payload.get("obtained_pages")
            if not isinstance(raw_list, list):
                raise LifeStanceContainmentValidationError("OBTAINED_PAGES_INVALID")
            for p in raw_list:
                if isinstance(p, bool) or not isinstance(p, int) or p < 1 or p > 100000:
                    raise LifeStanceContainmentValidationError("OBTAINED_PAGES_INVALID")
                pages_set.add(p)
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
        probed = probe_reading_material_revision(day_root, scope_ref)
        if probed is not None and probed.get("withdrawn"):
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")

        if "source_revision" in payload:
            source_revision = _require_id(
                payload.get("source_revision"), reason_code="SOURCE_REVISION_INVALID"
            )
        elif probed is not None:
            source_revision = str(probed["source_revision"])
        else:
            raise LifeStanceContainmentValidationError("SOURCE_REVISION_INVALID")

        page_or_anchor = _require_anchor(payload.get("page_or_anchor"))
        facet = payload.get("facet")
        if not isinstance(facet, str) or facet not in STANCE_FACETS:
            raise LifeStanceContainmentValidationError("STANCE_FACET_INVALID")
        text = _clean_multiline(
            payload.get("text"),
            min_len=1,
            max_len=MAX_STANCE_TEXT,
            reason_code="STANCE_TEXT_INVALID",
        )
        cached_excerpt = _optional_multiline(
            payload,
            "cached_excerpt",
            max_len=MAX_EXCERPT_TEXT,
            reason_code="CACHED_EXCERPT_INVALID",
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

        if scope_ref in withdrawn_sources:
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        reg_entry = src_reg.get(scope_ref)
        if reg_entry is not None:
            if reg_entry.get("withdrawn"):
                raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
            if reg_entry["current_source_revision"] != source_revision:
                raise LifeStanceContainmentConflictError("STANCE_SOURCE_REVISION_MISMATCH")
        elif probed is not None and probed["source_revision"] != source_revision:
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_REVISION_MISMATCH")

        page_ok, page_reason, page_detail = attn_proj.verify_page_obtained(
            day_root,
            scope_ref=scope_ref,
            page_or_anchor=page_or_anchor,
            extra_obtained_by_scope=obtained_pages_by_scope,
        )
        if not page_ok:
            raise LifeStanceContainmentConflictError(
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
                raise LifeStanceContainmentConflictError("ACTIVE_STANCE_EXISTS_USE_REVISE")

        if len(stances) >= MAX_STANCES:
            raise LifeStanceContainmentConflictError("STANCE_LIMIT_REACHED")

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
            "cached_excerpt": cached_excerpt,
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
                "stance": _project_owner_stance_row(
                    new_stance, subject_view=bool(subject_view), all_by_id=by_id
                ),
                "probed_reading_material": probed,
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
            raise LifeStanceContainmentNotFoundError("STANCE_NOT_FOUND")
        if (
            target_st["status"] == "SOURCE_WITHDRAWN"
            or target_st["scope_ref"] in withdrawn_sources
            or src_reg.get(target_st["scope_ref"], {}).get("withdrawn")
        ):
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if target_st["status"] == "SUPERSEDED":
            raise LifeStanceContainmentConflictError("STANCE_ALREADY_SUPERSEDED")
        if target_st["status"] == "SOURCE_REVISION_STALE":
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_STALE")
        if int(target_st["text_version"]) != expected_version:
            raise LifeStanceContainmentConflictError("STANCE_VERSION_CONFLICT")

        target_st["text"] = text
        target_st["text_version"] = int(target_st["text_version"]) + 1
        target_st["updated_at"] = now_iso
        target_st["last_edit_kind"] = "typo_fix"
        if private_note is not None:
            target_st["private_note"] = private_note

        by_id = {s["stance_id"]: s for s in stances}
        return _commit(
            {
                "stance": _project_owner_stance_row(
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
        cached_excerpt = _optional_multiline(
            payload,
            "cached_excerpt",
            max_len=MAX_EXCERPT_TEXT,
            reason_code="CACHED_EXCERPT_INVALID",
        )
        old_st = next((s for s in stances if s["stance_id"] == old_id), None)
        if old_st is None:
            raise LifeStanceContainmentNotFoundError("STANCE_NOT_FOUND")

        scope_ref = old_st["scope_ref"]
        probed = probe_reading_material_revision(day_root, scope_ref)
        if (
            old_st["status"] == "SOURCE_WITHDRAWN"
            or scope_ref in withdrawn_sources
            or src_reg.get(scope_ref, {}).get("withdrawn")
            or (probed is not None and probed.get("withdrawn"))
        ):
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if old_st["status"] == "SUPERSEDED" or old_st["superseded_by"] is not None:
            raise LifeStanceContainmentConflictError("STANCE_ALREADY_SUPERSEDED")

        if "scope_ref" in payload:
            req_scope = _require_id(
                payload.get("scope_ref"), reason_code="SCOPE_REF_INVALID"
            )
            if req_scope != scope_ref:
                raise LifeStanceContainmentConflictError("STANCE_SCOPE_MISMATCH")

        if "facet" in payload:
            req_facet = payload.get("facet")
            if not isinstance(req_facet, str) or req_facet not in STANCE_FACETS:
                raise LifeStanceContainmentValidationError("STANCE_FACET_INVALID")
            if req_facet != old_st["facet"]:
                raise LifeStanceContainmentConflictError("STANCE_FACET_MISMATCH")

        if "expected_version" in payload:
            exp_ver = _require_int_range(
                payload.get("expected_version"),
                minimum=1,
                maximum=100000,
                reason_code="EXPECTED_VERSION_INVALID",
            )
            if int(old_st["text_version"]) != exp_ver:
                raise LifeStanceContainmentConflictError("STANCE_VERSION_CONFLICT")

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
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_REVISION_MISMATCH")

        if scope_ref in src_reg:
            src_reg[scope_ref]["current_source_revision"] = current_src_rev

        new_anchor = (
            _require_anchor(payload.get("page_or_anchor"))
            if "page_or_anchor" in payload
            else old_st["page_or_anchor"]
        )
        page_ok, page_reason, page_detail = attn_proj.verify_page_obtained(
            day_root,
            scope_ref=scope_ref,
            page_or_anchor=new_anchor,
            extra_obtained_by_scope=obtained_pages_by_scope,
        )
        if not page_ok:
            raise LifeStanceContainmentConflictError(
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
            raise LifeStanceContainmentConflictError("STANCE_LIMIT_REACHED")

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
            "cached_excerpt": cached_excerpt,
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
                "stance": _project_owner_stance_row(
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
            raise LifeStanceContainmentNotFoundError("STANCE_NOT_FOUND")
        if (
            target_st["status"] == "SOURCE_WITHDRAWN"
            or target_st["scope_ref"] in withdrawn_sources
            or src_reg.get(target_st["scope_ref"], {}).get("withdrawn")
        ):
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        target_st["shared_with_partner"] = shared
        target_st["updated_at"] = now_iso
        by_id = {s["stance_id"]: s for s in stances}
        return _commit(
            {
                "stance": _project_owner_stance_row(
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
        if scope_ref in withdrawn_sources or (reg_entry is not None and reg_entry.get("withdrawn")):
            raise LifeStanceContainmentConflictError("STANCE_SOURCE_WITHDRAWN")
        if reg_entry is not None and reg_entry["current_source_revision"] == new_src_rev:
            raise LifeStanceContainmentConflictError("SOURCE_REVISION_UNCHANGED")

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
        withdrawn_sources[scope_ref] = {
            "scope_ref": scope_ref,
            "withdrawn_at": now_iso,
            "reason": reason,
            "last_known_revision": prev_rev,
        }
        withdrawn_ids: list[str] = []
        for st in stances:
            if st["scope_ref"] == scope_ref:
                st["status"] = "SOURCE_WITHDRAWN"
                st["withdrawn_at"] = now_iso
                st["shared_with_partner"] = False
                st["text"] = None
                st["cached_excerpt"] = None
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
        rebuilt = compute_active_stance_ids(stances, src_reg, withdrawn_sources)
        stance_dom["active_stance_ids"] = rebuilt
        return _commit({"rebuilt_active_stance_ids": rebuilt})

    # =========================================================================================
    # ADR-048: WORLD NESTED CONTAINMENT, OPEN/LOCK GATE & TWO-HAND PARTIAL TRANSFER
    # =========================================================================================

    containers_by_id = {c["container_id"]: c for c in containers}

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
                return effective_container_place(pcid, containers_by_id)
            return str(it_doc["place_id"])
        if it_doc["placement_kind"] == "hand":
            return str(cont_dom["subject_place_id"])
        return str(it_doc["place_id"])

    ext_hand_locks = read_cross_module_hand_locks(
        day_root, now_iso=now_iso, local_hand_slots=hand_slots
    )

    def _select_free_hand(requested_slot: Optional[str]) -> str:
        if requested_slot is not None:
            if requested_slot not in HAND_SLOTS:
                raise LifeStanceContainmentValidationError("HAND_SLOT_INVALID")
            if hand_slots[requested_slot] is not None or requested_slot in ext_hand_locks:
                raise LifeStanceContainmentConflictError("HAND_SLOT_OCCUPIED")
            return requested_slot
        if hand_slots["right_hand"] is None and "right_hand" not in ext_hand_locks:
            return "right_hand"
        if hand_slots["left_hand"] is None and "left_hand" not in ext_hand_locks:
            return "left_hand"
        raise LifeStanceContainmentConflictError("HAND_SLOT_OCCUPIED")

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
            written = write_attention_stance_containment_state(day_root, state)
            _sync_phone_state_if_present(day_root, written, now_iso=now_iso)
            raise LifeStanceContainmentConflictError(
                "ITEM_NOT_AT_EXPECTED_SPOT",
                details={
                    "item_id": item_doc["item_id"],
                    "expected_place_id": expected_place,
                },
            )
        raise LifeStanceContainmentConflictError(fallback_reason)

    if action == "register_container":
        actor = _resolve_actor(payload, key="actor", subject_view=bool(subject_view))
        cid = _require_id(payload.get("container_id"), reason_code="CONTAINER_ID_INVALID")
        if cid in containers_by_id or any(i["item_id"] == cid for i in items):
            raise LifeStanceContainmentConflictError("CONTAINER_ID_ALREADY_EXISTS")
        if len(containers) >= MAX_CONTAINERS:
            raise LifeStanceContainmentConflictError("CONTAINER_LIMIT_REACHED")
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
            raise LifeStanceContainmentValidationError("ACCEPTED_CLASSES_INVALID")
        acc = [str(cls) for cls in acc_raw if str(cls) in ITEM_CLASSES]
        if len(acc) != len(acc_raw):
            raise LifeStanceContainmentValidationError("ACCEPTED_CLASSES_INVALID")

        open_st = str(payload.get("open_state") or "open_top")
        if open_st not in CONTAINER_OPEN_STATES:
            raise LifeStanceContainmentValidationError("CONTAINER_OPEN_STATE_INVALID")
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
                raise LifeStanceContainmentNotFoundError("PARENT_CONTAINER_NOT_FOUND")
            parent_chain = container_ancestor_chain(parent_cid, containers_by_id)
            if len(parent_chain) + 1 > MAX_CONTAINER_NESTING_DEPTH:
                raise LifeStanceContainmentConflictError("CONTAINER_NESTING_DEPTH_EXCEEDED")
            gate_err = check_container_access_gate(
                parent_cid, containers_by_id, include_self=True
            )
            if gate_err is not None:
                raise LifeStanceContainmentConflictError(gate_err)
            if _occupied_slots_of(parent_cid) >= int(containers_by_id[parent_cid]["capacity"]):
                raise LifeStanceContainmentConflictError("CONTAINER_FULL")
            c_place = effective_container_place(parent_cid, containers_by_id)
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
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        if not cont_doc["portable"]:
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_PORTABLE")

        old_parent = cont_doc.get("parent_container_id")
        if old_parent is not None:
            old_gate = check_container_access_gate(
                old_parent, containers_by_id, include_self=True
            )
            if old_gate is not None:
                raise LifeStanceContainmentConflictError(old_gate)

        cur_place = effective_container_place(cid, containers_by_id)
        if actor == "subject" and (
            not cont_dom["subject_present"] or cur_place != cont_dom["subject_place_id"]
        ):
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_REACHABLE")

        parent_cid_raw = payload.get("parent_container_id")
        if parent_cid_raw is not None:
            parent_cid = _require_id(parent_cid_raw, reason_code="CONTAINER_ID_INVALID")
            if parent_cid == cid:
                raise LifeStanceContainmentConflictError("SELF_NESTING_FORBIDDEN")
            if parent_cid not in containers_by_id:
                raise LifeStanceContainmentNotFoundError("PARENT_CONTAINER_NOT_FOUND")
            desc_ids = set(descendant_container_ids(cid, containers))
            if parent_cid in desc_ids:
                raise LifeStanceContainmentConflictError("CONTAINER_NESTING_CYCLE")
            parent_chain = container_ancestor_chain(parent_cid, containers_by_id)
            subtree_extra = 1 if desc_ids else 0
            if len(parent_chain) + 1 + subtree_extra > MAX_CONTAINER_NESTING_DEPTH:
                raise LifeStanceContainmentConflictError("CONTAINER_NESTING_DEPTH_EXCEEDED")
            dest_place = effective_container_place(parent_cid, containers_by_id)
            if actor == "subject" and dest_place != cont_dom["subject_place_id"]:
                raise LifeStanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
            gate_err = check_container_access_gate(
                parent_cid, containers_by_id, include_self=True
            )
            if gate_err is not None:
                raise LifeStanceContainmentConflictError(gate_err)
            if parent_cid != old_parent and _occupied_slots_of(parent_cid) >= int(
                containers_by_id[parent_cid]["capacity"]
            ):
                raise LifeStanceContainmentConflictError("CONTAINER_FULL")
            cont_doc["parent_container_id"] = parent_cid
        else:
            dest_place = _require_place_id(
                payload.get("to_place_id") or cur_place, reason_code="TO_PLACE_ID_INVALID"
            )
            if actor == "subject" and dest_place != cont_dom["subject_place_id"]:
                raise LifeStanceContainmentConflictError("SURFACE_NOT_REACHABLE")
            cont_doc["parent_container_id"] = None

        affected_cids = [cid] + descendant_container_ids(cid, containers)
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
            raise LifeStanceContainmentValidationError("CONTAINER_OPEN_STATE_INVALID")
        cont_doc = containers_by_id.get(cid)
        if cont_doc is None:
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")

        eff_place = effective_container_place(cid, containers_by_id)
        if actor == "subject" and (
            not cont_dom["subject_present"] or eff_place != cont_dom["subject_place_id"]
        ):
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_REACHABLE")

        ancestor_gate = check_container_access_gate(
            cid, containers_by_id, include_self=False
        )
        if ancestor_gate is not None:
            raise LifeStanceContainmentConflictError(ancestor_gate)

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
            raise LifeStanceContainmentConflictError("NESTED_CONTAINER_FORBIDDEN")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise LifeStanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if not cont_dom["subject_present"]:
            raise LifeStanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")
        if item_doc["placement_kind"] == "hand":
            raise LifeStanceContainmentConflictError("ITEM_ALREADY_IN_HAND")
        if _effective_item_place(item_doc) != cont_dom["subject_place_id"]:
            _check_first_reach_miss_and_raise(
                item_doc,
                expected_place=cont_dom["subject_place_id"],
                fallback_reason="ITEM_NOT_REACHABLE",
            )
        if item_doc["placement_kind"] == "container" and item_doc["parent_container_id"] in containers_by_id:
            src_gate = check_container_access_gate(
                item_doc["parent_container_id"], containers_by_id, include_self=True
            )
            if src_gate is not None:
                raise LifeStanceContainmentConflictError(src_gate)

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
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise LifeStanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if not cont_dom["subject_present"]:
            raise LifeStanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")
        eff_c_place = effective_container_place(container_id, containers_by_id)
        if eff_c_place != cont_dom["subject_place_id"]:
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
        gate_err = check_container_access_gate(
            container_id, containers_by_id, include_self=True
        )
        if gate_err is not None:
            raise LifeStanceContainmentConflictError(gate_err)
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
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise LifeStanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if item_doc["placement_kind"] != "hand" or item_doc["held_in_hand"] not in HAND_SLOTS:
            raise LifeStanceContainmentConflictError("ITEM_NOT_IN_HAND")
        eff_c_place = effective_container_place(container_id, containers_by_id)
        if not cont_dom["subject_present"] or eff_c_place != cont_dom["subject_place_id"]:
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
        gate_err = check_container_access_gate(
            container_id, containers_by_id, include_self=True
        )
        if gate_err is not None:
            raise LifeStanceContainmentConflictError(gate_err)
        if item_doc["item_class"] not in cont_doc["accepted_classes"]:
            raise LifeStanceContainmentConflictError("CONTAINER_CLASS_REJECTED")
        if _occupied_slots_of(container_id) >= int(cont_doc["capacity"]):
            raise LifeStanceContainmentConflictError("CONTAINER_FULL")

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
            raise LifeStanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if item_doc["placement_kind"] != "hand" or item_doc["held_in_hand"] not in HAND_SLOTS:
            raise LifeStanceContainmentConflictError("ITEM_NOT_IN_HAND")
        if not cont_dom["subject_present"] or target_place != cont_dom["subject_place_id"]:
            raise LifeStanceContainmentConflictError("SURFACE_NOT_REACHABLE")

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
            raise LifeStanceContainmentConflictError("NESTED_CONTAINER_FORBIDDEN")

        cont_doc = containers_by_id.get(container_id)
        if cont_doc is None:
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        item_doc = next((i for i in items if i["item_id"] == item_id), None)
        if item_doc is None:
            raise LifeStanceContainmentNotFoundError("ITEM_NOT_FOUND")
        if not cont_dom["subject_present"]:
            raise LifeStanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")

        # Step 1: Pick up into hand_slot
        if item_doc["placement_kind"] == "hand":
            active_slot = str(item_doc["held_in_hand"])
            if req_slot is not None and req_slot != active_slot:
                raise LifeStanceContainmentConflictError("ITEM_ALREADY_IN_OTHER_HAND")
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
                src_gate = check_container_access_gate(
                    item_doc["parent_container_id"], containers_by_id, include_self=True
                )
                if src_gate is not None:
                    raise LifeStanceContainmentConflictError(src_gate)
            active_slot = _select_free_hand(req_slot)
            item_doc["placement_kind"] = "hand"
            item_doc["parent_container_id"] = None
            item_doc["held_in_hand"] = active_slot
            item_doc["place_id"] = cont_dom["subject_place_id"]
            item_doc["subject_last_seen_place"] = cont_dom["subject_place_id"]
            item_doc["subject_last_seen_at"] = now_iso
            item_doc["not_at_last_seen_place"] = False
            hand_slots[active_slot] = item_id

        # Step 2: Attempt placement into target container; on failure, persist item in hand!
        eff_c_place = effective_container_place(container_id, containers_by_id)
        partial_reason: Optional[str] = None
        if eff_c_place != cont_dom["subject_place_id"]:
            partial_reason = "CONTAINER_NOT_REACHABLE"
        else:
            gate_err = check_container_access_gate(
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
        proj_hands_after = dict(hand_slots)
        for s_k, l_v in ext_hand_locks.items():
            if proj_hands_after.get(s_k) is None:
                proj_hands_after[s_k] = l_v
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
                "hand_slots": proj_hands_after,
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
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        cur_c_place = effective_container_place(container_id, containers_by_id)
        if not cont_dom["subject_present"] or cur_c_place != cont_dom["subject_place_id"]:
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_REACHABLE")
        if not cont_doc["portable"]:
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_PORTABLE")
        ancestor_gate = check_container_access_gate(
            container_id, containers_by_id, include_self=False
        )
        if ancestor_gate is not None:
            raise LifeStanceContainmentConflictError(ancestor_gate)

        # Moving a container onto a target place puts it directly on that surface
        cont_doc["parent_container_id"] = None
        affected_cids = [container_id] + descendant_container_ids(container_id, containers)
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
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        if not cont_doc["portable"]:
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_PORTABLE")

        cont_doc["parent_container_id"] = None
        dest_room = _room_for_place(to_place)
        actually_seen = bool(
            witnessed
            and cont_dom["subject_present"]
            and cont_dom["subject_room_id"] == dest_room
        )

        affected_cids = [container_id] + descendant_container_ids(container_id, containers)
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
            raise LifeStanceContainmentValidationError("LOCATION_UPDATE_EMPTY")
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
            refresh_room_impressions(
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
        obs_place_arg: Optional[str] = None
        if "place_id" in payload:
            obs_place = _require_place_id(payload.get("place_id"))
            obs_place_arg = obs_place
            cont_dom["subject_place_id"] = obs_place
            cont_dom["subject_room_id"] = _room_for_place(obs_place)
            cont_dom["subject_present"] = obs_place != "outside"
        if "room_id" in payload:
            obs_room = _require_id(payload.get("room_id"), reason_code="ROOM_ID_INVALID")
            cont_dom["subject_room_id"] = obs_room
            cont_dom["subject_present"] = obs_room != "outside"

        if not cont_dom["subject_present"] or cont_dom["subject_room_id"] == "outside":
            raise LifeStanceContainmentConflictError("SUBJECT_NOT_IN_ROOM")

        obs_cid: Optional[str] = None
        if "container_id" in payload:
            obs_cid = _require_id(
                payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
            )
            if obs_cid not in containers_by_id:
                raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
            eff_obs_c_place = effective_container_place(obs_cid, containers_by_id)
            if eff_obs_c_place != cont_dom["subject_place_id"]:
                raise LifeStanceContainmentConflictError("CONTAINER_NOT_REACHABLE")

        obs_scope = str(
            payload.get("scope") or ("container" if obs_cid is not None else "room")
        )
        if obs_scope not in OBSERVE_SCOPES:
            raise LifeStanceContainmentValidationError("OBSERVE_SCOPE_INVALID")

        refreshed = refresh_room_impressions(
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
            raise LifeStanceContainmentNotFoundError("TARGET_NOT_FOUND")
        target_obj["private_impression_note"] = pnote
        return _commit({"target_id": target_id})

    if action == "remove_container":
        container_id = _require_id(
            payload.get("container_id"), reason_code="CONTAINER_ID_INVALID"
        )
        if container_id not in containers_by_id:
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
        if _occupied_slots_of(container_id) > 0:
            raise LifeStanceContainmentConflictError("CONTAINER_NOT_EMPTY")
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
            raise LifeStanceContainmentNotFoundError("TARGET_NOT_FOUND")
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
            raise LifeStanceContainmentNotFoundError("CONTAINER_NOT_FOUND")
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
            raise LifeStanceContainmentValidationError("DECISION_INVALID")
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        sugg_doc = next(
            (s for s in cohabitation["suggestions"] if s["suggestion_id"] == sugg_id),
            None,
        )
        if sugg_doc is None:
            raise LifeStanceContainmentNotFoundError("SUGGESTION_NOT_FOUND")
        if sugg_doc["status"] != "PENDING":
            raise LifeStanceContainmentConflictError("SUGGESTION_ALREADY_RESPONDED")
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
            raise LifeStanceContainmentNotFoundError("ITEM_NOT_FOUND")
        mode = str(payload.get("mode") or "lend")
        if mode not in ("lend", "return"):
            raise LifeStanceContainmentValidationError("LOAN_MODE_INVALID")
        default_to = "partner" if actor == "subject" else "subject"
        to_side = str(payload.get("to_side") or default_to)
        if to_side not in PARTICIPANT_ACTORS:
            raise LifeStanceContainmentValidationError("TO_SIDE_INVALID")
        if item_doc["owner"] == "subject" and actor == "partner" and mode == "lend":
            raise LifeStanceContainmentPermissionError("PRIVATE_ITEM_CONSENT_REQUIRED")
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

    raise LifeStanceContainmentValidationError(f"ACTION_INVALID:{action}")


__all__ = [
    "ATTENTION_STANCE_CONTAINMENT_SCHEMA",
    "CONTACT_MAX_NATURAL_SECONDS",
    "CONTAINER_OPEN_STATES",
    "HAND_SLOTS",
    "ITEM_CLASSES",
    "LifeStanceContainmentConflictError",
    "LifeStanceContainmentError",
    "LifeStanceContainmentNotFoundError",
    "LifeStanceContainmentPermissionError",
    "LifeStanceContainmentValidationError",
    "MAX_CONTAINER_NESTING_DEPTH",
    "OBSERVE_SCOPES",
    "OWNER_ACTIONS",
    "OWNER_ACTION_ALLOWED_FIELDS",
    "OWNER_SUBJECT_ONLY_ACTIONS",
    "STANCE_FACETS",
    "STANCE_STATUSES",
    "apply_attention_stance_containment_action",
    "attention_stance_containment_state_path",
    "check_container_access_gate",
    "compute_active_stance_ids",
    "container_ancestor_chain",
    "default_attention_stance_containment_state",
    "descendant_container_ids",
    "discover_reading_materials",
    "effective_container_place",
    "probe_reading_material_revision",
    "project_attention_stance_containment",
    "read_attention_stance_containment",
    "read_attention_stance_containment_state",
    "read_cross_module_hand_locks",
    "refresh_room_impressions",
    "sync_embodied_social_hand_slots_if_present",
    "sync_owner_domains_from_phone_state",
    "write_attention_stance_containment_state",
]
