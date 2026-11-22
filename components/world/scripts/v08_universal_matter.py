#!/usr/bin/env python3
"""v0.8 universal matter substrate and open multi-step act composer.

One physical vocabulary for *everything* real in the instance's space, so a new
life scene never needs a bespoke verb or a bespoke state machine:

* **Single physical ownership** -- every object has exactly one ``parent_id`` and
  containers derive ``children_ids`` live from that field.  There is no second
  count to drift.
* **Universal physical state** -- thermal / cook / moisture / cleanliness /
  freshness / portions / paper-likeness / lent-from / visibility.  Time-based
  fields settle against a real clock and age objectively even when nobody looks.
* **Role mind-space is separated from physics** -- ``subject_last_seen_parent``
  and ``impression_stale`` describe what the subject *thinks* it knows; a partner
  moving something never teleports knowledge into the subject's head.
* **Open primitives** composed by :func:`compose_open_act`:
  ``PLACE_OR_MOVE`` / ``INTRODUCE_ITEM`` / ``TRANSFORM_CREATE`` /
  ``CONSUME_PORTION`` / ``CLEAN_SURFACE_OR_VESSEL`` / ``DISCARD_ITEM`` /
  ``OBSERVE_OBJECT`` / ``SPLIT_MATERIAL`` / ``ASSEMBLE_OR_DETACH_PART`` /
  ``TRANSCRIBE_TO_PAPER`` / ``PATCH_OR_REPAIR`` / ``BORROW_NEIGHBORHOOD_ITEM`` /
  ``RETURN_BORROWED_ITEM`` / ``DELIVER_CARD_TO_PUBLIC_BOARD``.  The last three are
  the v0.4 Ch.42 cross-ring handoff (shared shelf / bookstore / public board); the
  borrowed thing is one real home object with an honest ``loan_meta``, never a
  second copy.  ``TRANSFORM_CREATE`` is
  deliberately recipe-free: any real unspoiled sources may become any
  caller-named output.  The four craft primitives add general conservation laws
  (integer ``material_units`` are never invented or destroyed by a split/fit),
  reversible assembly (a fitted part keeps its identity and detaches cleanly),
  frozen transcription (a paper snapshot never follows a later draft) and honest
  visible repair (one consumed patch unit leaves a real ``wear_state``).

Everything here is deterministic (``llm_calls == 0``), zero-network, and uses
only the standard library plus read-only calls into the shipped
``v08_open_affordance`` module (reachable-object discovery, the illness gate).
No existing module is modified.

State file: ``<root>/world/v08_matter_space.json``.  Every read helper is 100%
zero-write (no directory, no file, no mtime change); every write is one atomic
replace of that file.  The state file only appears after the first real write
operation -- reading the default space never creates it.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import importlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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

SCHEMA_VERSION = "world.v08.universal_matter.v1"
VIEW_SCHEMA = "nyairo.v08.matter_space.v1"
STATE_DIRNAME = "world"
STATE_FILENAME = "v08_matter_space.json"
MAX_RECENT_ACTS = 24
MAX_OPERATION_RECEIPTS = 200

#: Sentinel parent used in the subject's believed projection for an object whose
#: believed spot is genuinely unknown (never seen, or a reach-miss cleared it).
SUBJECT_UNKNOWN_PARENT = "__subject_unknown__"
#: Believed spot marker written after a subject reach-miss until she searches.
SUBJECT_MISSING_SPOT = "missing"

#: The appearance of an object as the subject last saw it.  A partner move keeps
#: this frozen so the subject's projection is byte-identical to the world where
#: nothing moved, until she actually observes or reaches the believed spot.
_SUBJECT_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "parent_id",
    "thermal",
    "cook_state",
    "moisture",
    "cleanliness",
    "freshness",
    "portions_left",
    "portions_total",
    "paper_like",
    "inscribed_text_cn",
    "requires_cooking",
    "lent_by",
    "loan_meta",
    "material_units",
    "assembly_role",
    "assembled_into_id",
    "fit_status",
    "transcribed_source",
    "wear_state",
    "patch_note_cn",
    "visibility",
    "proposed_use",
    "shelf_life_hours",
    "consumed",
    "discarded",
    # Carrier-bound relational posture is part of what the subject last saw:
    # a hidden partner move must not rewrite her believed drape/tuck/page mark.
    "placement_relation",
    "posture_tag",
    "anchor_page",
    "subject_association",
    "held_by",
    "open_state",
    # Ch.21 §3: a just-poured drink's cooling deadline and an honest page mark
    # are part of what the subject last saw; a hidden partner move must not
    # silently rewrite either of them.
    "scalding_until_iso",
    "scalding_wait_minutes",
    "reading_mark",
)

#: Primitives where the subject's own body must physically reach a named object.
#: ``OBSERVE_OBJECT`` is deliberately absent: looking is how she searches.
#: ``PACK_FOR_OUTING`` only guards the carrier here; every packed item is reached
#: one by one inside the primitive so a single unreachable item cannot abort an
#: otherwise honest partial pack.
_REACH_OBJECT_FIELDS: dict[str, tuple[str, ...]] = {
    "PLACE_OR_MOVE": ("object_id",),
    "MOVE_OBJECT": ("object_id",),
    "PLACE_INSIDE": ("object_id",),
    "ATTACH_NOTE": ("object_id",),
    "TAKE_OBJECT": ("object_id",),
    "ARRANGE_OBJECTS": ("member_ids",),
    "TRANSFORM_CREATE": ("source_object_ids",),
    "CONSUME_PORTION": ("object_id",),
    "CLEAN_SURFACE_OR_VESSEL": ("target_id", "helper_object_id"),
    "DISCARD_ITEM": ("object_id",),
    "SPLIT_MATERIAL": ("object_id",),
    "ASSEMBLE_OR_DETACH_PART": ("host_object_id", "part_object_id"),
    "TRANSCRIBE_TO_PAPER": ("object_id",),
    "PATCH_OR_REPAIR": ("object_id",),
    "RETURN_BORROWED_ITEM": ("object_id",),
    "DELIVER_CARD_TO_PUBLIC_BOARD": ("object_id",),
    "PACK_FOR_OUTING": ("carrier_id",),
}

OPEN_PRIMITIVES: tuple[str, ...] = (
    "PLACE_OR_MOVE",
    "MOVE_OBJECT",
    "PLACE_INSIDE",
    "ATTACH_NOTE",
    "TAKE_OBJECT",
    "ARRANGE_OBJECTS",
    "NOTE_ASSOCIATION",
    "NAME_PERSONAL_CORNER",
    "INTRODUCE_ITEM",
    "TRANSFORM_CREATE",
    "CONSUME_PORTION",
    "CLEAN_SURFACE_OR_VESSEL",
    "DISCARD_ITEM",
    "OBSERVE_OBJECT",
    "SPLIT_MATERIAL",
    "ASSEMBLE_OR_DETACH_PART",
    "TRANSCRIBE_TO_PAPER",
    "PATCH_OR_REPAIR",
    "BORROW_NEIGHBORHOOD_ITEM",
    "RETURN_BORROWED_ITEM",
    "DELIVER_CARD_TO_PUBLIC_BOARD",
    "PACK_FOR_OUTING",
)

#: Bounded relational posture for matter that sits *on* or *in* a carrier.
#: ``on`` / ``inside`` are the plain defaults; the carrier-bound relations below
#: are invalidated the moment the object is picked up or moved away.
PLACEMENT_RELATIONS: tuple[str, ...] = (
    "on",
    "inside",
    "draped_on",
    "tucked_in",
    "leaning_on",
    "attached_to",
)
#: A short human-readable posture.  ``folded`` / ``neat`` are intrinsic traces
#: that survive a move; the rest describe how the object sits on a carrier and
#: are cleared when that carrier binding breaks.
POSTURE_TAGS: tuple[str, ...] = (
    "",
    "neat",
    "casual_drape",
    "folded",
    "half_showing",
    "upright",
    "tilted",
)
CARRIER_BOUND_RELATIONS: frozenset[str] = frozenset({
    "draped_on",
    "leaning_on",
    "tucked_in",
    "attached_to",
})
CARRIER_BOUND_POSTURES: frozenset[str] = frozenset({
    "casual_drape",
    "half_showing",
    "tilted",
    "upright",
})
#: Postures that belong to the object itself and therefore survive a move.
INTRINSIC_POSTURES: frozenset[str] = frozenset({"folded", "neat"})
#: Tokens that make a carrier page-like or pocketable for ``tucked_in``.
_POCKETABLE_TOKENS: tuple[str, ...] = (
    "book", "notebook", "folder", "journal", "envelope", "pocket", "page",
    "booklet", "diary", "书", "册", "页", "信封", "口袋", "文件夹", "本子", "簿",
)
_OPEN_STATES: tuple[str, ...] = ("open", "closed")
ARRANGEMENT_PATTERNS: tuple[str, ...] = ("row", "arc", "stack", "cluster")
MAX_ARRANGEMENTS = 200
MAX_ASSOCIATION_CHARS = 80
#: A just-poured drink is honest-physics hot: it needs a short real cooldown
#: before it can be drunk.  Optional and default-off, so every legacy act stays
#: byte-identical; the deadline is a real clock instant, never a hidden timer.
MAX_SCALDING_MINUTES = 15
DEFAULT_BOIL_SCALDING_MINUTES = 5
#: A corner nickname is the subject's own private vocabulary for a real place.
MAX_NICKNAME_CHARS = 24
#: A bookmark is a bounded page position; re-reading a few pages back never
#: upgrades comprehension -- ``understood`` stays False until proven otherwise.
MAX_BOOKMARK_PAGE = 100000
MAX_REVISIT_OFFSET = 1000

#: Named places a corner nickname may attach to even before matter occupies
#: them.  Naming a corner never invents a physics zone; it is pure vocabulary.
KNOWN_CORNER_PLACES: dict[str, str] = {
    "window_side": "窗边",
    "window_sill": "窗台",
    "porch_eaves": "门廊檐下",
    "porch": "门廊",
    "entry_door": "门口",
    "open_yard": "院子",
    "yard": "小院",
    "yard_bench": "院里的长凳边",
    "balcony": "阳台",
}

#: Deterministic presets for a portable outing carrier.  A preset is plain data:
#: it is materialised on the first real ``PACK_FOR_OUTING`` reference and then
#: behaves exactly like any authored container -- no bag-specific branch exists
#: anywhere else in the engine or the phone projection.
OUTING_CARRIER_DEFAULTS: dict[str, dict[str, Any]] = {
    "canvas_bag": {
        "name_cn": "帆布挎包",
        "kind": "bag",
        "item_kind": "everyday",
        "zone_id": "entry_door",
        "container_capacity": 6,
        "portability": "portable",
        "surface_support": False,
    },
}
#: Authored carrier kinds that pack like a container even when the row only
#: declares ``kind``.  Capacity is still read honestly from the row/profile.
_PORTABLE_CARRIER_KINDS: frozenset[str] = frozenset({
    "bag", "basket", "tray", "box", "vase", "satchel", "pouch", "sack",
    "case", "pack", "container", "bottle", "jar", "cup", "bowl",
})
#: Bounded walk of the real/believed ``parent_id`` chain.
MAX_CARRIER_CHAIN_DEPTH = 6


#: How an object participates in reversible assembly.  ``host_frame`` accepts
#: fitted inserts, ``insert_part`` is meant to be fitted into a host, and
#: ``none`` is plain standalone matter.
ASSEMBLY_ROLES: tuple[str, ...] = ("none", "host_frame", "insert_part")
#: Whether an insert currently sits in a host (``fitted``), is honestly too big
#: for it (``oversized_unfitted``) or is not part of any assembly (``none``).
FIT_STATUSES: tuple[str, ...] = ("none", "fitted", "oversized_unfitted")
#: Honest visible wear: untouched, a loose edge, or a real past repair.
WEAR_STATES: tuple[str, ...] = ("intact", "loose_edge", "patched")

ITEM_KINDS: tuple[str, ...] = (
    "ingredient",
    "prepared_food",
    "drink",
    "vessel",
    "tool",
    "paper",
    "keepsake",
    "everyday",
)
THERMAL_STATES: tuple[str, ...] = ("frozen", "cold", "cool", "room", "warm", "hot")
COOK_STATES: tuple[str, ...] = ("none", "raw", "cooked")
MOISTURE_STATES: tuple[str, ...] = ("dry", "damp", "wet")
CLEANLINESS_STATES: tuple[str, ...] = ("clean", "used", "soiled")
FRESHNESS_STATES: tuple[str, ...] = ("fresh", "aging", "spoiled", "non_perishable")
AMBIENT_ZONES: tuple[str, ...] = ("room", "cold", "heating")
VISIBILITIES: tuple[str, ...] = ("shared", "private")

#: Custom-world catalogues read zero-write from the instance root.  These mirror
#: the shipped open-affordance readers; malformed or absent files are ignored.
#: Desktop/daemon instances store the active package under the HYPHEN path
#: ``<root>/world-package/`` together with an optional sibling ``scene.json``.
CUSTOM_WORLD_PACKAGE_FILES: tuple[tuple[str, ...], ...] = (
    ("world-package", "objects.json"),
    ("world", "package", "objects.json"),
    ("world_package", "objects.json"),
    ("world", "objects.json"),
)
CUSTOM_EDITOR_WORLD_FILES: tuple[tuple[str, ...], ...] = (
    ("world-package", "editor_package.json"),
    ("world-package", "world-package.v3.json"),
    ("world", "editor_package.json"),
    ("world_editor", "package.json"),
    ("world", "world-package.v3.json"),
    ("world", "nyairo-world.json"),
    ("world", "edited_world.json"),
    ("phone", "edited_world.json"),
    ("world", "scene.json"),
)

_CUSTOM_HOME_SCOPES = frozenset({
    "home", "bedroom", "living_room", "living-room", "livingroom",
    "study", "kitchen", "courtyard", "house", "apartment",
})
_CUSTOM_HOME_NAME_TOKENS = ("家", "卧室", "客厅", "书房", "厨房", "院子", "小屋", "居所")

_CUSTOM_KIND_ITEM_KINDS = {
    "book": "paper", "notebook": "paper", "paper": "paper", "document": "paper",
    "journal": "paper", "flyer": "paper", "letter": "paper",
    "keepsake": "keepsake", "memento": "keepsake", "charm": "keepsake",
    "souvenir": "keepsake",
    "ingredient": "ingredient", "fruit": "ingredient", "vegetable": "ingredient",
    "food": "ingredient", "grain": "ingredient", "meat": "ingredient",
    "drink": "drink", "beverage": "drink", "tea": "drink", "coffee": "drink",
    "dish": "prepared_food", "meal": "prepared_food", "prepared_food": "prepared_food",
    "tool": "tool", "knife": "tool", "pen": "tool", "brush": "tool",
    "utensil": "tool",
    "cup": "vessel", "bowl": "vessel", "bottle": "vessel", "jar": "vessel",
    "vessel": "vessel", "container": "vessel",
}
_CUSTOM_NAME_ITEM_KINDS: tuple[tuple[str, str], ...] = (
    ("笔记", "paper"), ("书", "paper"), ("纸", "paper"), ("册", "paper"), ("信", "paper"),
    ("茶", "drink"), ("咖啡", "drink"), ("果汁", "drink"), ("饮", "drink"),
    ("果", "ingredient"), ("菜", "ingredient"), ("米", "ingredient"),
    ("面", "ingredient"), ("肉", "ingredient"), ("蛋", "ingredient"),
    ("杯", "vessel"), ("碗", "vessel"), ("瓶", "vessel"), ("罐", "vessel"),
    ("壶", "vessel"), ("盒", "vessel"),
    ("笔", "tool"), ("刀", "tool"), ("剪", "tool"), ("勺", "tool"),
    ("挂件", "keepsake"), ("纪念", "keepsake"),
)
_CUSTOM_CONTAINER_KINDS = frozenset({
    "table", "desk", "cabinet", "shelf", "rack", "fridge", "box", "basket",
    "crate", "drawer", "wardrobe",
})
_CUSTOM_COLD_KINDS = frozenset({"fridge", "refrigerator", "cooler", "freezer", "icebox"})
_CUSTOM_HEAT_KINDS = frozenset({"stove", "oven", "heater", "cooktop", "grill", "furnace", "campfire"})
_CUSTOM_WATER_KINDS = frozenset({"sink", "tap", "faucet", "well", "fountain", "water", "shower"})
_CUSTOM_COLD_TOKENS = ("冰箱", "冷柜", "冰柜", "冷藏", "降温", "冷却")
_CUSTOM_HEAT_TOKENS = ("灶", "炉", "烤箱", "加热", "火")
_CUSTOM_WATER_TOKENS = ("水槽", "水龙头", "水池", "水源", "井", "淋浴", "喷泉")

LOCAL_TZ = timezone(timedelta(hours=8))

#: Minimum minutes a hot/warm item needs to reach room temperature in a room zone.
_ROOM_COOL_MINUTES = 30.0
#: Minimum minutes a cold item needs to warm back to room temperature.
_ROOM_WARM_MINUTES = 45.0
#: Minimum minutes a room/warm/hot item needs before a cold zone makes it cold.
_FRIDGE_CHILL_MINUTES = 20.0
#: Minimum minutes for wet/damp to dry in a room zone.
_DRY_MINUTES = 120.0
#: A cold zone slows perishable aging to this fraction of room-rate exposure.
_FRIDGE_FRESHNESS_RATE = 1.0 / 6.0
#: Aging starts at this fraction of shelf life.
_AGING_RATIO = 0.75
#: Default shelf life for a freshly cooked hot dish when the caller gave none.
_DEFAULT_COOKED_SHELF_LIFE_HOURS = 24.0
#: Eat/drink counts as one portion unless stated otherwise.
_DEFAULT_PORTIONS = 1
#: A composed dish yields this many portions unless stated otherwise.
_DEFAULT_OUTPUT_PORTIONS = 2

_ERROR_STATUS: dict[str, int] = {
    "INVALID_FIELDS": 400,
    "INVALID_TARGET": 400,
    "FORBIDDEN_ACTOR_SIDE": 403,
    "OBJECT_NOT_FOUND": 404,
    "CLIENT_IDENTITY_REFUSED": 409,
    "CAPACITY_FULL": 409,
    "INVALID_CONTAINMENT": 409,
    "SURFACE_SOILED": 409,
    "VESSEL_OCCUPIED": 409,
    "VESSEL_OCCUPIED_OR_SOILED": 409,
    "CONTAINER_NOT_EMPTY": 409,
    "ITEM_SPOILED": 409,
    "ITEM_RAW_NEEDS_HEAT": 409,
    "INSUFFICIENT_PORTIONS": 409,
    # A just-poured drink is genuinely too hot to swallow; wait, do not fake it.
    "DRINK_SCALDING_WAIT": 409,
    "ILLNESS_GATE_BLOCKED": 409,
    "OBJECT_NOT_AT_EXPECTED_SPOT": 409,
    "RETRY_WINDOW_EXPIRED": 409,
    "REVISION_CONFLICT": 409,
    "OPERATION_KEY_CONFLICT": 409,
    # v0.4 Ch.42 cross-ring loan handoff
    "NOT_A_LOAN_ITEM": 409,
    "LOAN_EXTENSION_UNSUPPORTED": 409,
    "CARD_NOT_INSCRIBED": 409,
    "NEIGHBORHOOD_UNAVAILABLE": 503,
}

_FORBIDDEN_IDENTITY_KEYS = frozenset(
    {"uid", "author_side", "by", "actor", "subject", "owner", "euid", "gid", "authority"}
)


class V08MatterError(ValueError):
    """A named refusal with the HTTP status the phone surface maps it to."""

    def __init__(self, reason_code: str, message: str = "", *, status_code: Optional[int] = None) -> None:
        self.reason_code = str(reason_code)
        self.message = str(message or self.reason_code)
        self.status_code = (
            int(status_code) if status_code is not None else _ERROR_STATUS.get(self.reason_code, 400)
        )
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=LOCAL_TZ)


def _parse_iso(value: Any) -> Optional[datetime]:
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
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).isoformat(timespec="seconds")


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    dt = _parse_iso(now_iso) if now_iso is not None else None
    if dt is None:
        dt = _now()
    return dt, _format_iso(dt)


def _minutes_since(value: Any, now_dt: datetime) -> float:
    dt = _parse_iso(value)
    if dt is None:
        return 0.0
    return max(0.0, (now_dt - dt).total_seconds() / 60.0)


def _hours_since(value: Any, now_dt: datetime) -> float:
    dt = _parse_iso(value)
    if dt is None:
        return 0.0
    return max(0.0, (now_dt - dt).total_seconds() / 3600.0)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _text(value: Any, *, min_len: int, max_len: int, field: str) -> str:
    if not isinstance(value, str):
        raise V08MatterError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if len(text) < min_len:
        raise V08MatterError("INVALID_FIELDS", f"{field} is too short")
    if len(text) > max_len:
        raise V08MatterError("INVALID_FIELDS", f"{field} exceeds {max_len} characters")
    return text


def _optional_text(value: Any, *, max_len: int, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise V08MatterError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_len:
        raise V08MatterError("INVALID_FIELDS", f"{field} exceeds {max_len} characters")
    return text


def _strict_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise V08MatterError("INVALID_FIELDS", f"{field} must be a JSON boolean")


def _choice(value: Any, choices: Sequence[str], *, field: str, default: Optional[str] = None) -> str:
    if value is None:
        if default is None:
            raise V08MatterError("INVALID_FIELDS", f"{field} is required")
        return default
    if not isinstance(value, str):
        raise V08MatterError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if text not in choices:
        raise V08MatterError("INVALID_FIELDS", f"{field} must be one of {list(choices)}")
    return text


def _int_field(
    value: Any,
    *,
    field: str,
    default: Optional[int] = None,
    minimum: int = 0,
    maximum: int = 999,
) -> int:
    if value is None:
        if default is None:
            raise V08MatterError("INVALID_FIELDS", f"{field} is required")
        return int(default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise V08MatterError("INVALID_FIELDS", f"{field} must be an integer")
    if not (minimum <= value <= maximum):
        raise V08MatterError("INVALID_FIELDS", f"{field} must be {minimum}..{maximum}")
    return int(value)


def _shelf_life(value: Any, *, field: str = "shelf_life_hours") -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise V08MatterError("INVALID_FIELDS", f"{field} must be a number or null")
    hours = float(value)
    if hours <= 0:
        return None
    if hours > 24 * 365 * 5:
        raise V08MatterError("INVALID_FIELDS", f"{field} is unreasonably long")
    return hours


def _scalding_minutes(step: Mapping[str, Any]) -> int:
    """Optional "just poured, too hot to drink" cooldown in whole minutes.

    ``scalding_minutes`` (1..15) wins when given; ``boil_scalding=True`` is the
    plain-language shorthand for the five-minute default.  No parameter means
    ``0``, so every pre-existing introduce/transform act is byte-identical.
    """

    if step.get("scalding_minutes") is not None:
        return _int_field(
            step.get("scalding_minutes"),
            field="scalding_minutes",
            minimum=1,
            maximum=MAX_SCALDING_MINUTES,
        )
    if step.get("boil_scalding") is not None:
        if _strict_bool(step.get("boil_scalding"), field="boil_scalding"):
            return DEFAULT_BOIL_SCALDING_MINUTES
    return 0


def _mark_scalding(obj: dict[str, Any], minutes: int, now_dt: datetime) -> None:
    """Stamp a real cooling deadline; ``minutes <= 0`` is a no-op."""

    if minutes <= 0:
        return
    obj["scalding_until_iso"] = _format_iso(now_dt + timedelta(minutes=minutes))
    obj["scalding_wait_minutes"] = int(minutes)


def _scalding_remaining_minutes(obj: Mapping[str, Any], now_dt: datetime) -> int:
    """Whole minutes still to wait, rounded up; ``0`` once the clock passes."""

    deadline = _parse_iso(obj.get("scalding_until_iso"))
    if deadline is None or deadline <= now_dt:
        return 0
    seconds = (deadline - now_dt).total_seconds()
    whole = int(seconds // 60)
    return max(1, whole + (1 if seconds % 60 else 0))


def _require_not_scalding(
    edible: Mapping[str, Any],
    host: Optional[Mapping[str, Any]],
    *,
    now_dt: datetime,
) -> None:
    """A just-poured drink waits for its real deadline; never faked away."""

    candidates: list[Mapping[str, Any]] = [edible]
    if isinstance(host, Mapping):
        candidates.append(host)
    for candidate in candidates:
        remaining = _scalding_remaining_minutes(candidate, now_dt)
        if remaining > 0:
            raise V08MatterError(
                "DRINK_SCALDING_WAIT",
                f"刚泡好还烫嘴，先搁手边晾几分钟（还要等 {remaining} 分钟）",
                status_code=409,
            )


def _reading_mark_from_step(
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> Optional[dict[str, Any]]:
    """An honest page mark: a position, never a claim of comprehension."""

    bookmark_page: Optional[int] = None
    if step.get("bookmark_page") is not None:
        bookmark_page = _int_field(
            step.get("bookmark_page"),
            field="bookmark_page",
            minimum=1,
            maximum=MAX_BOOKMARK_PAGE,
        )
    revisit_offset = 0
    if step.get("revisit_offset") is not None:
        revisit_offset = _int_field(
            step.get("revisit_offset"),
            field="revisit_offset",
            minimum=-MAX_REVISIT_OFFSET,
            maximum=MAX_REVISIT_OFFSET,
        )
        if bookmark_page is None:
            raise V08MatterError("INVALID_FIELDS", "revisit_offset needs bookmark_page")
    if bookmark_page is None:
        return None
    return {
        "bookmark_page": int(bookmark_page),
        "revisit_offset": int(revisit_offset),
        # Flipping two pages back and forth is not reading deeper.
        "understood": False,
        "at": now_text,
    }


def _require_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not payload:
        raise V08MatterError("INVALID_FIELDS", "payload must be a non-empty object")
    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise V08MatterError("INVALID_FIELDS", "blank or padded key forbidden")
    return dict(payload)


def _assert_no_identity_keys(payload: Mapping[str, Any]) -> None:
    found = sorted(set(payload) & _FORBIDDEN_IDENTITY_KEYS)
    if found:
        raise V08MatterError(
            "CLIENT_IDENTITY_REFUSED",
            "identity keys are never accepted from the client: " + ", ".join(found),
        )


# ---------------------------------------------------------------------------
# Default physical space
# ---------------------------------------------------------------------------


def _zone_row(
    zone_id: str,
    label_cn: str,
    *,
    ambient_zone: str,
    water_source: bool,
    capacity_slots: int,
    visibility: str = "shared",
) -> dict[str, Any]:
    return {
        "zone_id": zone_id,
        "label_cn": label_cn,
        "ambient_zone": ambient_zone,
        "water_source": water_source,
        "capacity_slots": capacity_slots,
        "visibility": visibility,
        "surface_cleanliness": "clean",
    }


def _default_zones() -> dict[str, dict[str, Any]]:
    rows = [
        _zone_row("home_desk", "书桌台面", ambient_zone="room", water_source=False, capacity_slots=4),
        _zone_row("home_staging_corner", "顺手暂放角", ambient_zone="room", water_source=False, capacity_slots=4),
        _zone_row("home_fridge", "共享冰箱", ambient_zone="cold", water_source=False, capacity_slots=6),
        _zone_row("home_fridge_door", "冰箱门磁贴墙", ambient_zone="room", water_source=False, capacity_slots=6),
        _zone_row("kitchen_counter", "备餐台", ambient_zone="room", water_source=False, capacity_slots=3),
        _zone_row("kitchen_stove", "灶台热源", ambient_zone="heating", water_source=False, capacity_slots=2),
        _zone_row("dining_table", "餐桌台面", ambient_zone="room", water_source=False, capacity_slots=3),
        _zone_row("kitchen_sink", "厨房水槽", ambient_zone="room", water_source=True, capacity_slots=4),
        _zone_row("subject_private_drawer", "私人小抽屉", ambient_zone="room", water_source=False, capacity_slots=3, visibility="private"),
    ]
    return {row["zone_id"]: row for row in rows}


def _object_row(
    object_id: str,
    label_cn: str,
    *,
    item_kind: str,
    parent_id: str,
    capacity_slots: int = 0,
    paper_like: bool = False,
    visibility: str = "shared",
    material_units: int = 1,
    assembly_role: str = "none",
) -> dict[str, Any]:
    role = str(assembly_role or "none").strip().lower()
    units = int(material_units)
    return {
        "object_id": object_id,
        "label_cn": label_cn,
        "item_kind": item_kind,
        "parent_id": parent_id,
        "capacity_slots": int(capacity_slots),
        "material_units": max(1, min(24, units)),
        "assembly_role": role if role in ASSEMBLY_ROLES else "none",
        "assembled_into_id": None,
        "fit_status": "none",
        "transcribed_source": None,
        "wear_state": "intact",
        "patch_note_cn": None,
        "thermal": "room",
        "cook_state": "none",
        "moisture": "dry",
        "cleanliness": "clean",
        "freshness": "non_perishable",
        "portions_left": None,
        "portions_total": None,
        "paper_like": bool(paper_like),
        "inscribed_text_cn": None,
        "requires_cooking": False,
        "lent_by": None,
        "visibility": visibility,
        "subject_last_seen_parent": parent_id,
        "subject_last_seen_snapshot": None,
        "impression_stale": False,
        "not_at_believed_spot": False,
        "last_miss_parent": None,
        # A sealed carrier keeps its hidden body private from the subject until she
        # legitimately opens/unseals it; the partner God-view still sees it.
        "sealed": False,
        "unsealed_for_subject": False,
        "last_observed_at": None,
        "state_clock_iso": None,
        "last_thermal_at": None,
        "last_moisture_at": None,
        "freshness_anchor_at": None,
        "freshness_settled_at": None,
        "effective_exposure_hours": 0.0,
        "shelf_life_hours": None,
        "proposed_use": None,
        # Ch.21 §3: "just poured, too hot to drink" and an honest page mark.
        # Both default off; a legacy row reads them as absent.
        "scalding_until_iso": None,
        "scalding_wait_minutes": 0,
        "reading_mark": None,
        # Carrier-bound relational posture (Layer 2).  ``on`` / ``inside`` are
        # plain placement; ``posture_tag`` is a short observable description.
        "placement_relation": "on",
        "posture_tag": "",
        "anchor_page": None,
        # Layer 3: an attributed, strictly non-escalating personal association.
        # It never rewrites physics, capabilities or completion ledgers.
        "subject_association": None,
        "held_by": None,
        "open_state": None,
        "from_open_affordance": False,
        "consumed": False,
        "discarded": False,
        # Ch.16: a familiar spot is only ever earned by real, repeated subject
        # contact in the same zone -- never pre-scripted and never invented.
        "familiar_spot_zone_id": None,
        "familiar_spot_hits": 0,
        "familiar_spot_anchor_zone": None,
        "consecutive_returns": 0,
        "preferred_home_slot": None,
        "is_habitual_slot": False,
    }


def _default_objects() -> dict[str, dict[str, Any]]:
    rows = [
        _object_row("ceramic_cup_01", "白瓷杯", item_kind="vessel", parent_id="home_desk", capacity_slots=2),
        _object_row("soup_bowl_01", "深口瓷碗", item_kind="vessel", parent_id="kitchen_counter", capacity_slots=2),
        _object_row("pen_01", "常用勾线笔", item_kind="tool", parent_id="home_desk"),
        _object_row("pen_02", "水彩画笔", item_kind="tool", parent_id="home_desk"),
        _object_row("sketch_sheet_01", "未收起的参考画纸", item_kind="paper", parent_id="home_desk", paper_like=True),
    ]
    return {row["object_id"]: row for row in rows}


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        # A subject-only cursor: hidden partner changes never bump it, so they can
        # never leak through a global version/updated_at difference.
        "subject_view_revision": 0,
        "next_op_seq": 0,
        "committed_op_ring": [],
        "evicted_up_to_revision": 0,
        "created_at": None,
        "updated_at": None,
        "zones": _default_zones(),
        "matter_objects": _default_objects(),
        # Live-projected group arrangements; never a stored "still intact" lie.
        "arrangements": {},
        # The subject's own corner vocabulary.  An old archive without this key
        # reads as an empty mapping and never gains it on a read.
        "zone_nicknames": {},
        "recent_acts": [],
        "operations": {},
        "discards": [],
    }


# ---------------------------------------------------------------------------
# State persistence (reads are 100% zero-write)
# ---------------------------------------------------------------------------


def state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / STATE_FILENAME


def _write_locked(func):
    """Serialize a whole mutating entry point under the shared state lock."""

    @functools.wraps(func)
    def wrapper(root: Path | str, *args, **kwargs):
        with state_file_lock(state_path(root)):
            return func(root, *args, **kwargs)

    return wrapper


def _read_state_zero_write(root: Path | str) -> dict[str, Any]:
    """Parse the state file without creating anything.  Missing/corrupt reads as defaults."""

    path = state_path(root)
    state = _default_state()
    try:
        if not path.is_file() or path.is_symlink():
            return state
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return state
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return state
    if not isinstance(parsed, Mapping):
        return state

    for key in (
        "schema_version",
        "revision",
        "subject_view_revision",
        "next_op_seq",
        "evicted_up_to_revision",
        "created_at",
        "updated_at",
    ):
        if key in parsed:
            state[key] = copy.deepcopy(parsed[key])
    if isinstance(parsed.get("committed_op_ring"), list):
        state["committed_op_ring"] = copy.deepcopy(parsed["committed_op_ring"])

    if isinstance(parsed.get("zones"), Mapping):
        state["zones"] = {}
        for zid, zrow in parsed["zones"].items():
            if isinstance(zrow, Mapping) and str(zid).strip():
                row = copy.deepcopy(dict(zrow))
                row.setdefault("zone_id", str(zid))
                state["zones"][str(zid)] = row
    # Zones are structural anchors that are never deleted; re-seed any missing.
    for zid, zrow in _default_zones().items():
        state["zones"].setdefault(zid, zrow)

    raw_objects = parsed.get("matter_objects")
    if not isinstance(raw_objects, Mapping):
        # Tolerant alias: a hand-authored or older fixture may key the same rows
        # as ``objects`` (a mapping keyed by id or a list of rows).  This keeps
        # zero-code container injection working without a second state shape.
        raw_objects = parsed.get("objects") if isinstance(parsed.get("objects"), (Mapping, list)) else None
    if isinstance(raw_objects, Mapping):
        state["matter_objects"] = {}
        for oid, orow in raw_objects.items():
            if isinstance(orow, Mapping) and str(oid).strip():
                row = copy.deepcopy(dict(orow))
                row.setdefault("object_id", str(oid))
                state["matter_objects"][str(oid)] = row
    elif isinstance(raw_objects, list):
        state["matter_objects"] = {}
        for orow in raw_objects:
            if not isinstance(orow, Mapping):
                continue
            oid = str(orow.get("object_id") or "").strip()
            if oid:
                state["matter_objects"][oid] = copy.deepcopy(dict(orow))

    objects_map = state.get("matter_objects")
    if isinstance(objects_map, dict):
        for oid, orow in objects_map.items():
            if not isinstance(orow, dict):
                continue
            # A row authored by a fixture has no separate belief history yet; it
            # is where it is.  This is the same assumption partner_bring_item
            # already makes for a genuinely visible addition.
            orow.setdefault("subject_last_seen_parent", orow.get("parent_id"))
            # ``placement_relation`` is deliberately left absent when a fixture
            # omits it: the projection then derives the honest default from the
            # real carrier (inside a container, on a surface/zone).
            orow.setdefault("posture_tag", "")
            orow.setdefault("anchor_page", None)
            orow.setdefault("subject_association", None)
            orow.setdefault("held_by", None)
            orow.setdefault("open_state", None)
            # Ch.21 fields are absent from older archives; absent means off.
            orow.setdefault("scalding_until_iso", None)
            orow.setdefault("scalding_wait_minutes", 0)
            orow.setdefault("reading_mark", None)
            orow.setdefault("consecutive_returns", 0)
            orow.setdefault("preferred_home_slot", None)
            orow.setdefault("is_habitual_slot", False)
            orow.setdefault("object_id", str(oid))

    raw_arrangements = parsed.get("arrangements")
    if isinstance(raw_arrangements, Mapping):
        state["arrangements"] = {}
        for aid, arow in raw_arrangements.items():
            if isinstance(arow, Mapping) and str(aid).strip():
                row = copy.deepcopy(dict(arow))
                row.setdefault("arrangement_id", str(aid))
                state["arrangements"][str(aid)] = row

    # Corner nicknames are pure vocabulary: a missing key on an old archive reads
    # as ``{}`` and is never written back just because someone looked.
    raw_nicknames = parsed.get("zone_nicknames")
    state["zone_nicknames"] = {}
    if isinstance(raw_nicknames, Mapping):
        for zid, nrow in raw_nicknames.items():
            if not isinstance(nrow, Mapping) or not str(zid).strip():
                continue
            text = str(nrow.get("nickname_cn") or "").strip()
            if not text:
                continue
            row = copy.deepcopy(dict(nrow))
            row["zone_id"] = str(zid)
            row["nickname_cn"] = text[:MAX_NICKNAME_CHARS]
            row["shared_with_partner"] = bool(nrow.get("shared_with_partner"))
            state["zone_nicknames"][str(zid)] = row

    if isinstance(parsed.get("recent_acts"), list):
        state["recent_acts"] = copy.deepcopy(parsed["recent_acts"])
    if isinstance(parsed.get("operations"), Mapping):
        state["operations"] = copy.deepcopy(dict(parsed["operations"]))
    if isinstance(parsed.get("discards"), list):
        state["discards"] = copy.deepcopy(parsed["discards"])
    if isinstance(parsed.get("corkboard_wall"), Mapping):
        state["corkboard_wall"] = copy.deepcopy(dict(parsed["corkboard_wall"]))
    if isinstance(parsed.get("dietary_preferences"), Mapping):
        state["dietary_preferences"] = copy.deepcopy(dict(parsed["dietary_preferences"]))
    return state


def _atomic_write_state(root: Path | str, state: Mapping[str, Any]) -> None:
    path = state_path(root)
    if path.exists() and path.is_symlink():
        raise V08MatterError("INVALID_TARGET", "state file is a symlink")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise V08MatterError("INVALID_TARGET", "state directory is a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(state))
    _trim_collections(doc)
    encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".v08_matter_", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, str(path))
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _trim_collections(state: dict[str, Any]) -> None:
    acts = state.get("recent_acts")
    if isinstance(acts, list) and len(acts) > MAX_RECENT_ACTS:
        state["recent_acts"] = acts[-MAX_RECENT_ACTS:]
    # Eviction is strictly by submission order (``op_seq``), never by the
    # alphabetical key order that ``sort_keys=True`` would produce.  Evicted rows
    # stay replayable through ``committed_op_ring`` / the operations journal.
    if isinstance(state.get("operations"), dict):
        trim_receipts_by_op_seq(
            state, dict_key="operations", max_keep=MAX_OPERATION_RECEIPTS
        )
    discards = state.get("discards")
    if isinstance(discards, list) and len(discards) > MAX_OPERATION_RECEIPTS:
        state["discards"] = discards[-MAX_OPERATION_RECEIPTS:]
    arrangements = state.get("arrangements")
    if isinstance(arrangements, dict) and len(arrangements) > MAX_ARRANGEMENTS:
        # Insertion order is the honest creation order; drop oldest surplus only.
        for aid in list(arrangements)[: len(arrangements) - MAX_ARRANGEMENTS]:
            arrangements.pop(aid, None)


def _bump(state: dict[str, Any], now_text: str) -> None:
    state["schema_version"] = SCHEMA_VERSION
    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_text
    if not state.get("created_at"):
        state["created_at"] = now_text


def _bump_subject_view(state: dict[str, Any]) -> None:
    """Advance the subject's own cursor -- hidden partner changes never do."""

    state["subject_view_revision"] = int(state.get("subject_view_revision") or 0) + 1


# ---------------------------------------------------------------------------
# Operation-key idempotency
# ---------------------------------------------------------------------------


def _core_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in ("operation_key", "fingerprint")
    }


def _fingerprint(action: str, actor_side: str, core: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"action": action, "actor_side": actor_side, "core": core},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_replay(
    state: Mapping[str, Any],
    *,
    operation_key: Optional[str],
    fingerprint: str,
    root: Optional[Path | str] = None,
    expected_revision: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Classify a retry across the live receipts, the evicted ring and the journal."""

    if not operation_key:
        return None
    outcome, row = reconcile_evicted_or_active_op(
        root,
        state,
        operation_key,
        dict_key="operations",
        expected_revision=expected_revision,
        payload_hash=fingerprint,
    )
    if outcome == "conflict":
        raise V08MatterError(
            "OPERATION_KEY_CONFLICT",
            "the same operation_key was used for a different action",
        )
    if outcome == "expired":
        raise V08MatterError(
            "RETRY_WINDOW_EXPIRED",
            "this retry is older than the committed retention window",
        )
    if outcome not in ("hit_full", "hit_evicted") or not isinstance(row, Mapping):
        return None

    # Legacy receipts written before the guard carry a raw ``fingerprint`` field.
    legacy_fingerprint = str(row.get("fingerprint") or "")
    if legacy_fingerprint and legacy_fingerprint != fingerprint:
        raise V08MatterError(
            "OPERATION_KEY_CONFLICT",
            "the same operation_key was used for a different action",
        )

    receipt = row.get("receipt")
    if isinstance(receipt, Mapping):
        out = copy.deepcopy(dict(receipt))
        out["already_applied"] = True
        return out
    out = {
        "status": str(row.get("status") or "applied"),
        "already_applied": True,
        "operation_key": operation_key,
        "revision": row.get("revision"),
        "reconciled_from": str(row.get("reconciled_from") or "committed_op_ring"),
    }
    return out


def _store_operation(
    state: dict[str, Any],
    *,
    operation_key: Optional[str],
    fingerprint: str,
    receipt: Mapping[str, Any],
    committed_at: str = "",
) -> None:
    if not operation_key:
        return
    row = {
        "fingerprint": fingerprint,
        "receipt": copy.deepcopy(dict(receipt)),
        "status": str(receipt.get("status") or "applied"),
        "ok": str(receipt.get("status") or "applied") in ("applied", "already_applied"),
    }
    # ``stamp_receipt`` owns the monotonic ``op_seq`` so eviction is submission
    # ordered and a replay can be reconciled long after the row is trimmed.
    stamp_receipt(
        state,
        str(operation_key),
        row,
        revision=int(state.get("revision") or 0),
        dict_key="operations",
        payload_hash=fingerprint,
        committed_at=committed_at,
    )


# ---------------------------------------------------------------------------
# Subject mind-space: believed snapshot, pre-miss projection, sealing
# ---------------------------------------------------------------------------


def _capture_subject_snapshot(obj: Mapping[str, Any]) -> dict[str, Any]:
    """A lightweight immutable record of the object as the subject last saw it."""

    snapshot: dict[str, Any] = {}
    for key in _SUBJECT_SNAPSHOT_FIELDS:
        if key in obj:
            snapshot[key] = copy.deepcopy(obj.get(key))
    return snapshot


def _apply_subject_snapshot(row: dict[str, Any], snapshot: Mapping[str, Any]) -> None:
    for key in _SUBJECT_SNAPSHOT_FIELDS:
        if key in snapshot:
            row[key] = copy.deepcopy(snapshot[key])


def _refresh_subject_belief(live: dict[str, Any]) -> None:
    """The subject has just legitimately seen this object where it really is."""

    live["subject_last_seen_parent"] = live.get("parent_id")
    live["subject_last_seen_snapshot"] = _capture_subject_snapshot(live)
    live["impression_stale"] = False
    live["not_at_believed_spot"] = False
    live["last_miss_parent"] = None


def _record_subject_reach_miss(
    state: dict[str, Any], object_id: str, believed_parent: Any
) -> tuple[str, str]:
    """Freeze a safe miss: believed spot cleared, receipts clean, no real parent."""

    live = state.get("matter_objects", {}).get(object_id)
    if not isinstance(live, dict):
        return "原来的地方", "东西"
    believed_zone = _zone(state, believed_parent)
    if believed_zone is not None:
        spot = str(believed_zone.get("label_cn") or "原来的地方")
    else:
        container = _object(state, believed_parent)
        spot = str((container or {}).get("label_cn") or "原来的地方")
    label = _public_label(live)
    live["impression_stale"] = False
    live["not_at_believed_spot"] = True
    live["last_miss_parent"] = believed_parent
    # Clear the believed spot: she no longer asserts it is anywhere in particular.
    # (Legacy state may still carry ``SUBJECT_MISSING_SPOT``; the guard accepts it.)
    live["subject_last_seen_parent"] = ""
    live["subject_last_seen_snapshot"] = None
    return spot, label


def _is_sealed_from_subject(obj: Mapping[str, Any]) -> bool:
    """A sealed carrier hides its body/contents until the subject opens it."""

    if not bool(obj.get("sealed")):
        return False
    return not bool(obj.get("unsealed_for_subject") or obj.get("shared_with_subject"))


def _object_is_hidden_from_subject(
    objects: Mapping[str, Any], object_id: str, *, _depth: int = 0
) -> bool:
    """True when any real ancestor container is sealed/unopened to the subject."""

    if _depth > 8:
        return False
    obj = objects.get(object_id)
    if not isinstance(obj, Mapping):
        return False
    if _is_sealed_from_subject(obj):
        return True
    parent_id = str(obj.get("parent_id") or "")
    if not parent_id or parent_id not in objects:
        return False
    return _object_is_hidden_from_subject(objects, parent_id, _depth=_depth + 1)


def _subject_believed_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the space as the subject believes it, with zero pre-miss truth.

    Every stale object is projected back to ``subject_last_seen_parent`` using its
    frozen snapshot; an unseen or miss-cleared object is parked at a sentinel
    parent so it is neither listed nor counted.  Sealed bodies and sealed
    containers' hidden contents are withheld.  The returned state is a deep
    copy -- callers may settle/project it freely.
    """

    believed = copy.deepcopy(dict(state))
    objects = believed.get("matter_objects")
    if not isinstance(objects, dict):
        return believed

    for object_id, obj in list(objects.items()):
        if not isinstance(obj, dict) or not _is_active(obj):
            continue
        if _object_is_hidden_from_subject(objects, str(object_id)) and not _is_sealed_from_subject(obj):
            # A hidden child of a sealed container: not part of the subject's world.
            obj["parent_id"] = SUBJECT_UNKNOWN_PARENT
            obj["_subject_hidden"] = True
            continue

        believed_parent = obj.get("subject_last_seen_parent")
        if bool(obj.get("impression_stale")):
            if believed_parent and str(believed_parent) != SUBJECT_MISSING_SPOT:
                obj["parent_id"] = believed_parent
                snapshot = obj.get("subject_last_seen_snapshot")
                if isinstance(snapshot, Mapping):
                    _apply_subject_snapshot(obj, snapshot)
                obj["impression_stale"] = False
            else:
                # Never legitimately seen, or the believed spot already missed.
                obj["parent_id"] = SUBJECT_UNKNOWN_PARENT
                obj["_subject_hidden"] = True
        elif bool(obj.get("not_at_believed_spot")):
            obj["parent_id"] = SUBJECT_UNKNOWN_PARENT
            obj["_subject_hidden"] = True
        elif not believed_parent and not (
            obj.get("returned_out") or obj.get("delivered_to_board")
        ):
            # New partner matter the subject has not encountered yet.
            obj["parent_id"] = SUBJECT_UNKNOWN_PARENT
            obj["_subject_hidden"] = True

        if _is_sealed_from_subject(obj):
            obj["inscribed_text_cn"] = None

    return believed


# ---------------------------------------------------------------------------
# Containment, capacity and physical helpers
# ---------------------------------------------------------------------------


def _is_active(obj: Mapping[str, Any]) -> bool:
    # A consumed, discarded or returned-out object is no longer physically here.
    return not obj.get("consumed") and not obj.get("discarded") and not obj.get("returned_out")


def _active_objects(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    objects = state.get("matter_objects")
    if not isinstance(objects, Mapping):
        return {}
    return {str(oid): dict(obj) for oid, obj in objects.items() if isinstance(obj, Mapping) and _is_active(obj)}


def _children_of(state: Mapping[str, Any], parent_id: Any) -> list[str]:
    pid = str(parent_id or "")
    objects = state.get("matter_objects")
    if not isinstance(objects, Mapping):
        return []
    out = []
    for oid, obj in objects.items():
        if not isinstance(obj, Mapping) or not _is_active(obj):
            continue
        if str(obj.get("parent_id") or "") == pid:
            out.append(str(oid))
    return sorted(out)


def _assembled_parts_of(state: Mapping[str, Any], host_id: Any) -> list[str]:
    """Active insert parts whose ``assembled_into_id`` points at this host."""

    hid = str(host_id or "")
    if not hid:
        return []
    objects = state.get("matter_objects")
    if not isinstance(objects, Mapping):
        return []
    out = []
    for oid, obj in objects.items():
        if not isinstance(obj, Mapping) or not _is_active(obj):
            continue
        if str(obj.get("assembled_into_id") or "") == hid:
            out.append(str(oid))
    return sorted(out)


def _object(state: Mapping[str, Any], object_id: Any) -> Optional[dict[str, Any]]:
    objects = state.get("matter_objects")
    if not isinstance(objects, Mapping):
        return None
    obj = objects.get(str(object_id or ""))
    return dict(obj) if isinstance(obj, Mapping) else None


def _zone(state: Mapping[str, Any], zone_id: Any) -> Optional[dict[str, Any]]:
    zones = state.get("zones")
    if not isinstance(zones, Mapping):
        return None
    row = zones.get(str(zone_id or ""))
    return dict(row) if isinstance(row, Mapping) else None


def _ambient_for(state: Mapping[str, Any], obj: Mapping[str, Any]) -> str:
    parent_id = obj.get("parent_id")
    for _ in range(8):
        if not parent_id:
            break
        zone = _zone(state, parent_id)
        if zone is not None:
            return str(zone.get("ambient_zone") or "room")
        parent = _object(state, parent_id)
        if parent is None or not _is_active(parent):
            break
        parent_id = parent.get("parent_id")
    return "room"


def _surface_cleanliness(state: Mapping[str, Any], parent_id: Any) -> Optional[str]:
    zone = _zone(state, parent_id)
    if zone is not None:
        return str(zone.get("surface_cleanliness") or "clean")
    return None


def _zone_has_water(state: Mapping[str, Any], zone_id: Any) -> bool:
    zone = _zone(state, zone_id)
    return zone is not None and bool(zone.get("water_source"))


def _target_capacity(state: Mapping[str, Any], target_id: Any) -> Optional[int]:
    zone = _zone(state, target_id)
    if zone is not None:
        return int(zone.get("capacity_slots") or 0)
    obj = _object(state, target_id)
    if obj is not None:
        return int(obj.get("capacity_slots") or 0)
    return None


def _validate_target_exists(
    state: Mapping[str, Any],
    target_id: Any,
    *,
    root: Optional[Path | str] = None,
) -> str:
    tid = str(target_id or "").strip()
    if not tid:
        raise V08MatterError("INVALID_FIELDS", "target_parent_id is required")
    if _zone(state, tid) is None and _object(state, tid) is None and root is not None:
        # A shared custom zone is real before the first write; materialise it
        # now so the step lands in the authored place rather than a 404.
        _materialize_custom_zone(state, root, tid)
    if _zone(state, tid) is None and _object(state, tid) is None:
        raise V08MatterError("INVALID_TARGET", f"unknown target: {tid}")
    return tid


def _validate_capacity(state: Mapping[str, Any], target_id: str, *, moving_id: Optional[str] = None) -> None:
    capacity = _target_capacity(state, target_id)
    if capacity is None:
        raise V08MatterError("INVALID_TARGET", f"unknown target: {target_id}")
    if _zone(state, target_id) is None and capacity <= 0:
        raise V08MatterError("INVALID_CONTAINMENT", "that object cannot hold another object")
    children = [cid for cid in _children_of(state, target_id) if cid != moving_id]
    if len(children) >= capacity:
        raise V08MatterError("CAPACITY_FULL", f"target has no free slot: {target_id}")


def _validate_placement(
    state: Mapping[str, Any],
    obj: Mapping[str, Any],
    target_id: str,
    *,
    carrier_relation: Optional[str] = None,
) -> None:
    moving_id = str(obj.get("object_id") or "")
    if moving_id and moving_id == target_id:
        raise V08MatterError("INVALID_CONTAINMENT", "an object cannot contain itself")
    target_zone = _zone(state, target_id)
    target_object = _object(state, target_id)
    if target_zone is None and target_object is None:
        raise V08MatterError("INVALID_TARGET", f"unknown target: {target_id}")
    if target_object is not None:
        exterior_relations = ("draped_on", "leaning_on", "attached_to")
        moving_capacity = int(obj.get("capacity_slots") or 0)
        target_capacity_slots = int(target_object.get("capacity_slots") or 0)
        if (
            moving_capacity > 0
            and target_capacity_slots > 0
            and carrier_relation not in exterior_relations
        ):
            # Only real container *nesting* is forbidden.  A container may be
            # leant/draped/attached on the outside of another carrier without
            # consuming an interior slot.
            raise V08MatterError("INVALID_CONTAINMENT", "containers may not nest inside containers")
        if target_capacity_slots <= 0:
            # A capacity-0 carrier is furniture/surface: something may sit
            # plainly on it or be leant/draped/attached on its *outside* --
            # that uses no interior slot and is not container nesting.
            # Inside/tucked placement still needs a real container/pocket.
            if carrier_relation in exterior_relations or carrier_relation == "on":
                return
            raise V08MatterError("INVALID_CONTAINMENT", "target cannot contain another object")
    if target_zone is not None and target_zone.get("surface_cleanliness") == "soiled" and obj.get("paper_like"):
        raise V08MatterError("SURFACE_SOILED", "the surface is soiled; paper would be ruined")
    if target_object is not None and carrier_relation in ("draped_on", "leaning_on", "attached_to"):
        # Resting on the outside never consumes the carrier's interior slot.
        return
    _validate_capacity(state, target_id, moving_id=moving_id or None)


def _object_open_state(obj: Mapping[str, Any]) -> str:
    """The carrier's real open/closed state; ``sealed`` counts as closed."""

    profile = obj.get("matter_profile") if isinstance(obj.get("matter_profile"), Mapping) else {}
    raw = obj.get("open_state")
    if raw is None and isinstance(profile, Mapping):
        raw = profile.get("open_state")
    state = str(raw or "open").strip().lower()
    if state not in _OPEN_STATES:
        state = "open"
    if bool(obj.get("sealed")) and not bool(obj.get("unsealed_for_subject")):
        return "closed"
    return state


def _carrier_capabilities(obj: Mapping[str, Any]) -> dict[str, bool]:
    """Bounded derived capabilities from plain fields + an authored profile."""

    profile = obj.get("matter_profile") if isinstance(obj.get("matter_profile"), Mapping) else {}
    capacity = int(obj.get("capacity_slots") or profile.get("capacity_slots") or 0)
    caps = {
        "container": capacity > 0 or str(obj.get("item_kind") or "") == "vessel",
        "pocketable": False,
        "readable": False,
        "surface": True,
    }
    for source in (obj, profile):
        if not isinstance(source, Mapping):
            continue
        if source.get("container") is True or source.get("is_container") is True:
            caps["container"] = True
        for key in ("readable", "book", "notebook", "pages", "page_count"):
            value = source.get(key)
            if value is True or (isinstance(value, int) and not isinstance(value, bool) and value > 0):
                caps["readable"] = True
        for key in ("pocket", "pocketable", "folder", "envelope"):
            value = source.get(key)
            if value is True or (isinstance(value, int) and not isinstance(value, bool) and value > 0):
                caps["pocketable"] = True
        raw_tags = source.get("tags") or source.get("traits") or source.get("capabilities")
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        if isinstance(raw_tags, (list, tuple)):
            tokens = {str(tag).strip().lower() for tag in raw_tags}
            if tokens & {"container", "vessel"}:
                caps["container"] = True
            if tokens & {"pocket", "pocketable", "folder", "envelope", "page"}:
                caps["pocketable"] = True
            if tokens & {"book", "readable", "notebook", "pages"}:
                caps["readable"] = True
    haystack = " ".join(
        str(value)
        for value in (
            obj.get("object_id"),
            obj.get("label_cn"),
            obj.get("kind"),
            obj.get("item_kind"),
            profile.get("kind") if isinstance(profile, Mapping) else None,
            " ".join(str(v) for v in profile.values()) if isinstance(profile, Mapping) else None,
        )
        if value is not None
    ).lower()
    if any(token in haystack for token in _POCKETABLE_TOKENS):
        caps["pocketable"] = True
    return caps


def _object_portability(obj: Mapping[str, Any]) -> str:
    """``fixed`` only when the object (or its profile) declares it; else portable."""

    profile = obj.get("matter_profile") if isinstance(obj.get("matter_profile"), Mapping) else {}
    raw = obj.get("portability")
    if raw is None and isinstance(profile, Mapping):
        raw = profile.get("portability")
    return str(raw or "portable").strip().lower() or "portable"


def _object_container_capacity(obj: Mapping[str, Any]) -> int:
    """Honest interior capacity declared on the row or its matter profile."""

    profile = obj.get("matter_profile") if isinstance(obj.get("matter_profile"), Mapping) else {}
    for source in (obj, profile):
        if not isinstance(source, Mapping):
            continue
        for key in ("container_capacity", "capacity_slots"):
            value = source.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return int(value)
    return 0


def _object_is_portable_carrier(obj: Mapping[str, Any]) -> bool:
    """Can this real object be carried out with things inside it?

    Any authored container qualifies -- capacity on the row/profile, a real
    container capability, or a plain carrier ``kind``.  Only an explicitly
    ``fixed`` fixture (a built-in shelf, a cabinet) is refused.
    """

    if _object_portability(obj) == "fixed":
        return False
    if _object_container_capacity(obj) > 0:
        return True
    if _carrier_capabilities(obj)["container"]:
        return True
    profile = obj.get("matter_profile") if isinstance(obj.get("matter_profile"), Mapping) else {}
    kind = str(obj.get("kind") or profile.get("kind") or "").strip().lower()
    return kind in _PORTABLE_CARRIER_KINDS


def _carrier_object_row(
    carrier_id: str,
    preset: Mapping[str, Any],
    *,
    parent_id: str,
) -> dict[str, Any]:
    """Materialise one preset carrier as ordinary matter data."""

    capacity = int(preset.get("container_capacity") or 0)
    row = _object_row(
        str(carrier_id),
        str(preset.get("name_cn") or carrier_id),
        item_kind=str(preset.get("item_kind") or "everyday"),
        parent_id=str(parent_id),
        capacity_slots=capacity,
        visibility="shared",
    )
    kind = str(preset.get("kind") or "")
    portability = str(preset.get("portability") or "portable")
    surface_support = bool(preset.get("surface_support", False))
    if kind:
        row["kind"] = kind
    row["portability"] = portability
    row["surface_support"] = surface_support
    row["matter_profile"] = {
        "kind": kind or "bag",
        "container": capacity > 0,
        "capacity_slots": capacity,
        "container_capacity": capacity,
        "open_state": "open",
        "portability": portability,
        "surface_support": surface_support,
    }
    return row


def _ensure_carrier_zone(state: dict[str, Any], zone_id: Any) -> str:
    """The place a newly materialised carrier lives; a missing zone is authored."""

    zid = str(zone_id or "").strip() or "entry_door"
    if _zone(state, zid) is not None or _object(state, zid) is not None:
        return zid
    label = "门口" if zid == "entry_door" else zid
    state.setdefault("zones", {})[zid] = _zone_row(
        zid, label, ambient_zone="room", water_source=False, capacity_slots=2
    )
    return zid


def _read_neighborhood_journey_zero_write(root: Path | str) -> Optional[dict[str, Any]]:
    """The real journey record, read strictly zero-write (never created)."""

    path = Path(root) / "world" / "v04_neighborhood_state.json"
    doc = _read_json_zero_write(path)
    if not isinstance(doc, Mapping):
        return None
    journey = doc.get("journey")
    return dict(journey) if isinstance(journey, Mapping) else None


def _believed_parent_of(obj: Mapping[str, Any]) -> str:
    """The carrier the subject may act on -- never the God-view real parent."""

    if bool(obj.get("not_at_believed_spot")):
        return ""
    believed = obj.get("subject_last_seen_parent")
    if bool(obj.get("impression_stale")):
        if believed in (None, "", SUBJECT_MISSING_SPOT, SUBJECT_UNKNOWN_PARENT):
            return ""
        return str(believed)
    if believed not in (None, "", SUBJECT_MISSING_SPOT, SUBJECT_UNKNOWN_PARENT):
        return str(believed)
    return str(obj.get("parent_id") or "")


def _ancestor_object_ids(
    objects_map: Mapping[str, Any],
    start_id: Any,
    *,
    max_depth: int = MAX_CARRIER_CHAIN_DEPTH,
) -> list[str]:
    """Nearest-first real object ancestors, bounded and cycle-safe."""

    out: list[str] = []
    cur = str(start_id or "")
    seen = {cur}
    for _ in range(max_depth):
        obj = objects_map.get(cur)
        if not isinstance(obj, Mapping):
            break
        parent = str(obj.get("parent_id") or "")
        if not parent or parent not in objects_map or parent in seen:
            break
        out.append(parent)
        seen.add(parent)
        cur = parent
    return out


def _derive_effective_location_readonly(
    obj_id: str,
    objects_map: Mapping[str, Any],
    *,
    zones_by_id: Mapping[str, Any],
    perspective: str,
    journey_doc: Optional[Mapping[str, Any]] = None,
    max_depth: int = MAX_CARRIER_CHAIN_DEPTH,
) -> dict[str, Any]:
    """Derive a live effective zone/place along the carrier chain; never writes.

    The child rows are never rewritten when a parent carrier moves: this walks
    the (real, or for ``perspective == "subject"`` the believed) ``parent_id``
    chain on every projection instead.  A stale or miss-cleared belief stops the
    walk, so a hidden partner move can never be inferred from a derived zone.
    """

    objects = objects_map if isinstance(objects_map, Mapping) else {}
    zones = zones_by_id if isinstance(zones_by_id, Mapping) else {}
    oid = str(obj_id or "")
    believed = str(perspective or "") == "subject"

    chain: list[str] = []
    effective_zone_id: Optional[str] = None
    cur = oid
    seen = {oid}
    for _ in range(max(1, int(max_depth))):
        obj = objects.get(cur)
        if not isinstance(obj, Mapping):
            break
        parent = _believed_parent_of(obj) if believed else str(obj.get("parent_id") or "")
        if not parent:
            break
        if parent in zones:
            effective_zone_id = parent
            break
        if parent in objects:
            if parent in seen:
                break
            chain.append(parent)
            seen.add(parent)
            cur = parent
            continue
        # An authored anchor the caller did not hand in is still the real root.
        effective_zone_id = parent
        break

    carried_ids: list[str] = []
    journey = journey_doc if isinstance(journey_doc, Mapping) else None
    if journey is not None:
        raw_carried = journey.get("carried_item_ids")
        if isinstance(raw_carried, (list, tuple)):
            carried_ids = [str(item) for item in raw_carried if str(item or "").strip()]
    carried_on_journey = bool(carried_ids) and any(
        candidate in carried_ids for candidate in ([oid] + chain)
    )
    if carried_on_journey and journey is not None:
        journey_state = str(journey.get("state") or "")
        if journey_state == "in_transit":
            effective_place_id = "in_transit"
        else:
            effective_place_id = str(journey.get("current_place_id") or "home") or "home"
    else:
        effective_place_id = "home"

    return {
        "object_id": oid,
        "effective_zone_id": effective_zone_id,
        "effective_place_id": effective_place_id,
        "carrier_chain": chain,
        "carried_on_journey": bool(carried_on_journey),
        "root_carrier_id": chain[-1] if chain else oid,
    }


def _default_placement_relation(state: Mapping[str, Any], target_id: str) -> str:
    target_object = _object(state, target_id)
    if target_object is not None and int(target_object.get("capacity_slots") or 0) > 0:
        return "inside"
    return "on"


def _validate_carrier_relation(
    state: Mapping[str, Any],
    target_id: str,
    relation: str,
    anchor_page: Optional[int],
) -> None:
    """Carrier laws for one real relation; never invents a capability."""

    if anchor_page is not None and relation != "tucked_in":
        raise V08MatterError("INVALID_FIELDS", "anchor_page is only meaningful for tucked_in")
    target_object = _object(state, target_id)
    if target_object is None:
        # A zone has a real surface/slot capacity; every relation is legal there.
        return
    caps = _carrier_capabilities(target_object)
    if relation == "inside":
        if not caps["container"]:
            raise V08MatterError("INVALID_CONTAINMENT", "that carrier has no container capacity")
        if _object_open_state(target_object) == "closed":
            raise V08MatterError("INVALID_CONTAINMENT", "open that carrier before placing something inside")
    elif relation == "tucked_in":
        if not (caps["container"] or caps["pocketable"] or caps["readable"]):
            raise V08MatterError("INVALID_CONTAINMENT", "that carrier has no pages or pocket to tuck into")
        if _object_open_state(target_object) == "closed":
            raise V08MatterError("INVALID_CONTAINMENT", "open that carrier before tucking something in")
    elif relation in ("draped_on", "leaning_on", "attached_to"):
        if _object_open_state(target_object) == "closed" and relation == "leaning_on":
            raise V08MatterError("INVALID_CONTAINMENT", "cannot lean on a closed carrier")
        if bool(target_object.get("sealed")) and not bool(target_object.get("unsealed_for_subject")):
            raise V08MatterError("INVALID_CONTAINMENT", "that carrier is sealed")


def _intrinsic_posture(posture: Any) -> str:
    value = str(posture or "")
    return value if value in INTRINSIC_POSTURES else ""


def _clear_carrier_binding(obj: dict[str, Any], *, relation: str = "") -> None:
    """Break a carrier-bound relation while preserving intrinsic traces."""

    obj["placement_relation"] = relation
    obj["anchor_page"] = None
    if str(obj.get("posture_tag") or "") in CARRIER_BOUND_POSTURES:
        obj["posture_tag"] = ""


def _break_arrangements_for_member(
    state: dict[str, Any],
    member_id: str,
    *,
    now_text: str,
    reason: str,
    actor_side: str,
) -> list[str]:
    """Stamp an honest break on every arrangement this member belonged to."""

    broken: list[str] = []
    arrangements = state.get("arrangements")
    if not isinstance(arrangements, dict):
        return broken
    for aid, record in arrangements.items():
        if not isinstance(record, dict):
            continue
        members = record.get("member_ids")
        if not isinstance(members, list) or member_id not in [str(m) for m in members]:
            continue
        if record.get("broken_at"):
            continue
        record["broken_at"] = now_text
        record["invalidated_reason"] = reason
        record["invalidated_by"] = actor_side
        broken.append(str(aid))
    return broken


def _new_object_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        ("|".join(str(part) for part in parts)).encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{digest}"


# ---------------------------------------------------------------------------
# Time-based physics (settle in place; projection settles a deep copy)
# ---------------------------------------------------------------------------


def _anchor_object_clocks_if_needed(
    state: Mapping[str, Any],
    obj: dict[str, Any],
    *,
    now_text: str,
    old_ambient: Optional[str] = None,
) -> None:
    """Anchor thermal/freshness clocks when matter is first seen or moved.

    A freshly materialised custom object carries no clock anchors.  With
    ``last_thermal_at`` left ``None``, ``_settle_object`` computes zero elapsed
    minutes forever and the item never chills; a perishable with no freshness
    anchor never starts its exposure clock at all.  Re-anchoring on an ambient
    change also stops time spent in the old zone being credited to the new one.
    """

    new_ambient = _ambient_for(state, obj)
    if obj.get("last_thermal_at") is None or (
        old_ambient is not None and old_ambient != new_ambient
    ):
        obj["last_thermal_at"] = now_text
        obj["state_clock_iso"] = now_text

    shelf_life = obj.get("shelf_life_hours")
    if (
        isinstance(shelf_life, (int, float))
        and not isinstance(shelf_life, bool)
        and float(shelf_life) > 0
    ):
        if not obj.get("freshness_anchor_at"):
            obj["freshness_anchor_at"] = now_text
        if not obj.get("freshness_settled_at"):
            obj["freshness_settled_at"] = now_text
        if str(obj.get("freshness") or "non_perishable") == "non_perishable":
            obj["freshness"] = "fresh"


def _read_microzone_env_zero_write(
    root: Path | str,
    now_text: str,
) -> Optional[dict[str, Any]]:
    """Lazy, failure-tolerant read of the real micro-zone conditions.

    Returns ``None`` when the optional ecology module is unavailable or errors,
    so every legacy behaviour stays byte-identical when it is unused.
    """

    try:
        from components.world.scripts import v08_microzone_ecology as microzone
    except Exception:  # noqa: BLE001 - flat path or a partially loaded package
        try:
            import v08_microzone_ecology as microzone  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001 - absent module means no coupling
            return None
    try:
        env = microzone.resolve_local_conditions_zero_write(root, now_iso=now_text)
    except Exception:  # noqa: BLE001 - a broken optional state is simply absent
        return None
    return dict(env) if isinstance(env, Mapping) else None


def _zone_id_for(state: Mapping[str, Any], obj: Mapping[str, Any]) -> Optional[str]:
    """The zone a (possibly nested) object physically sits in."""

    parent_id = obj.get("parent_id")
    for _ in range(8):
        if not parent_id:
            return None
        pid = str(parent_id)
        if _zone(state, pid) is not None:
            return pid
        parent = _object(state, pid)
        if parent is None or not _is_active(parent):
            return None
        parent_id = parent.get("parent_id")
    return None


def _note_familiar_contact(
    state: Mapping[str, Any],
    obj: Any,
    *,
    zone_id: Optional[str] = None,
) -> None:
    """Track organic subject use at one zone; two real contacts anchor the spot.

    This is deliberately act-driven only.  A partner silently moving an object
    never calls into here, so an unfamiliar item cannot conjure an expectation.
    """

    if not isinstance(obj, dict):
        return
    zone = str(zone_id or _zone_id_for(state, obj) or "").strip()
    if not zone:
        return
    raw_hits = obj.get("familiar_spot_hits")
    hits = raw_hits if isinstance(raw_hits, int) and not isinstance(raw_hits, bool) and raw_hits >= 0 else 0
    if str(obj.get("familiar_spot_zone_id") or "") == zone:
        hits += 1
    else:
        obj["familiar_spot_zone_id"] = zone
        hits = 1
    obj["familiar_spot_hits"] = hits
    if hits >= 2:
        obj["familiar_spot_anchor_zone"] = zone

    # v0.5 Step 3: habitual slot forms after >= 3 consecutive returns without
    # any artificial affection points.
    raw_consec = obj.get("consecutive_returns")
    consec = raw_consec if isinstance(raw_consec, int) and not isinstance(raw_consec, bool) and raw_consec >= 0 else 0
    if str(obj.get("preferred_home_slot") or "") == zone:
        consec += 1
    else:
        obj["preferred_home_slot"] = zone
        consec = 1
    obj["consecutive_returns"] = consec
    obj["is_habitual_slot"] = consec >= 3


def _settle_object(
    state: Mapping[str, Any],
    obj: dict[str, Any],
    now_dt: datetime,
    now_text: str,
    *,
    env_context: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Advance one object's physical clocks.  Returns change flags."""

    flags = {
        "dirty": False,
        "cooled": False,
        "chilled": False,
        "spoiled": False,
        "dried": False,
        "rain_dampened": False,
    }
    ambient = _ambient_for(state, obj)
    zone_id = _zone_id_for(state, obj)
    env = env_context if isinstance(env_context, Mapping) else None
    weather = str((env or {}).get("weather") or "")
    window_state = str((env or {}).get("window_state") or "")

    # Rain only wets genuinely exposed matter; porch eaves stay rain-sheltered.
    outdoor_rain = zone_id in ("open_yard", "yard", "yard_line", "yard_bench") and weather in (
        "drizzle",
        "rain",
    )
    sill_rain = (
        zone_id in ("window_side", "window_sill", "home_desk")
        and weather == "rain"
        and window_state == "open"
        and bool(obj.get("paper_like"))
    )
    rain_exposed = outdoor_rain or sill_rain
    sheltered_slow_drying = zone_id in ("porch_eaves", "porch") and weather in ("drizzle", "rain")

    # -- temperature ------------------------------------------------------
    thermal = str(obj.get("thermal") or "room")
    elapsed = _minutes_since(obj.get("last_thermal_at"), now_dt)
    # An open window pulls room warmth away faster; an ajar sill does so at the sill.
    cool_minutes = _ROOM_COOL_MINUTES
    if env is not None:
        if window_state == "open":
            cool_minutes = _ROOM_COOL_MINUTES * 0.6
        elif window_state == "ajar" and zone_id in ("window_side", "window_sill"):
            cool_minutes = _ROOM_COOL_MINUTES * 0.6
    new_thermal = thermal
    if ambient == "cold":
        if thermal in ("room", "warm", "hot", "cool") and elapsed >= _FRIDGE_CHILL_MINUTES:
            new_thermal = "cold"
            flags["chilled"] = True
    elif ambient == "room":
        if thermal in ("hot", "warm") and elapsed >= cool_minutes:
            new_thermal = "room"
            flags["cooled"] = True
        elif thermal == "cold" and elapsed >= _ROOM_WARM_MINUTES:
            new_thermal = "room"
            flags["cooled"] = True
    if new_thermal != thermal:
        obj["thermal"] = new_thermal
        obj["last_thermal_at"] = now_text
        obj["state_clock_iso"] = now_text
        flags["dirty"] = True

    # -- moisture ---------------------------------------------------------
    moisture = str(obj.get("moisture") or "dry")
    if ambient == "room" and moisture in ("wet", "damp") and not rain_exposed:
        dry_minutes = _DRY_MINUTES * (2.0 if sheltered_slow_drying else 1.0)
        if _minutes_since(obj.get("last_moisture_at"), now_dt) >= dry_minutes:
            obj["moisture"] = "dry"
            obj["last_moisture_at"] = now_text
            obj["state_clock_iso"] = now_text
            flags["dried"] = True
            flags["dirty"] = True

    # -- rain wetting (real weather, not a narrative effect) --------------
    if outdoor_rain:
        current = str(obj.get("moisture") or "dry")
        if weather == "rain" and current != "wet":
            obj["moisture"] = "wet"
            obj["last_moisture_at"] = now_text
            obj["state_clock_iso"] = now_text
            flags["rain_dampened"] = True
            flags["dirty"] = True
        elif weather == "drizzle" and current == "dry":
            obj["moisture"] = "damp"
            obj["last_moisture_at"] = now_text
            obj["state_clock_iso"] = now_text
            flags["rain_dampened"] = True
            flags["dirty"] = True
    elif sill_rain and str(obj.get("moisture") or "dry") == "dry":
        obj["moisture"] = "damp"
        obj["last_moisture_at"] = now_text
        obj["state_clock_iso"] = now_text
        flags["rain_dampened"] = True
        flags["dirty"] = True

    # -- freshness --------------------------------------------------------
    shelf_life = obj.get("shelf_life_hours")
    if not isinstance(shelf_life, (int, float)) or float(shelf_life) <= 0:
        if obj.get("freshness") != "non_perishable":
            obj["freshness"] = "non_perishable"
            flags["dirty"] = True
        return flags

    rate = _FRIDGE_FRESHNESS_RATE if ambient == "cold" else 1.0
    settled_at = obj.get("freshness_settled_at") or obj.get("freshness_anchor_at")
    delta_hours = _hours_since(settled_at, now_dt) if settled_at else 0.0
    try:
        exposure = float(obj.get("effective_exposure_hours") or 0.0)
    except (TypeError, ValueError):
        exposure = 0.0
    if delta_hours > 0.0:
        exposure += delta_hours * rate
        obj["effective_exposure_hours"] = round(exposure, 6)
        obj["freshness_settled_at"] = now_text
        flags["dirty"] = True
    ratio = exposure / float(shelf_life)
    new_freshness = "fresh"
    if ratio >= 1.0:
        new_freshness = "spoiled"
    elif ratio >= _AGING_RATIO:
        new_freshness = "aging"
    old_freshness = str(obj.get("freshness") or "fresh")
    if new_freshness != old_freshness:
        obj["freshness"] = new_freshness
        obj["state_clock_iso"] = now_text
        if new_freshness == "spoiled":
            flags["spoiled"] = True
        flags["dirty"] = True
    return flags


def _settle_state(
    state: dict[str, Any],
    now_dt: datetime,
    now_text: str,
    *,
    env_context: Optional[Mapping[str, Any]] = None,
) -> dict[str, int]:
    counts = {
        "settled_items": 0,
        "cooled": 0,
        "chilled": 0,
        "spoiled": 0,
        "dried": 0,
        "rain_dampened": 0,
    }
    objects = state.get("matter_objects")
    if not isinstance(objects, Mapping):
        return counts
    for obj in objects.values():
        if not isinstance(obj, dict) or not _is_active(obj):
            continue
        flags = _settle_object(state, obj, now_dt, now_text, env_context=env_context)
        if flags["dirty"]:
            counts["settled_items"] += 1
        if flags["cooled"]:
            counts["cooled"] += 1
        if flags["chilled"]:
            counts["chilled"] += 1
        if flags["spoiled"]:
            counts["spoiled"] += 1
        if flags["dried"]:
            counts["dried"] += 1
        if flags["rain_dampened"]:
            counts["rain_dampened"] += 1
    return counts


def _project_state(
    state: Mapping[str, Any],
    now_dt: datetime,
    now_text: str,
    *,
    root: Optional[Path | str] = None,
    env_context: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """A deep copy with physics projected forward in memory (never written)."""

    if env_context is None and root is not None:
        env_context = _read_microzone_env_zero_write(root, now_text)
    projected = copy.deepcopy(dict(state))
    _settle_state(projected, now_dt, now_text, env_context=env_context)
    return projected


# ---------------------------------------------------------------------------
# Derived role (never a rename command; always a live description)
# ---------------------------------------------------------------------------


def _public_label(obj: Mapping[str, Any]) -> str:
    return str(obj.get("label_cn") or obj.get("object_id") or "物件")


def _proposed_suffix(obj: Mapping[str, Any]) -> str:
    proposal = obj.get("proposed_use")
    if not isinstance(proposal, Mapping):
        return ""
    text = str(proposal.get("proposed_use_cn") or "").strip()
    if not text:
        return ""
    status = str(proposal.get("status") or "proposed")
    if status == "adopted":
        return f"；已采纳用法：{text}"
    if status == "declined":
        return f"；已婉拒用法：{text}"
    return f"；伴侣建议：{text}（待决定）"


def _portion_suffix(obj: Mapping[str, Any]) -> str:
    portions = obj.get("portions_left")
    if isinstance(portions, int) and portions > 0:
        return f"（剩 {portions} 份）"
    return ""


def _inscription_text(obj: Mapping[str, Any]) -> Optional[str]:
    """The live inscription on a paper-like item, if any (never a rename)."""

    value = obj.get("inscribed_text_cn")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def note_legibility(obj: Mapping[str, Any]) -> Optional[str]:
    """Derive objective legibility from live moisture physics.

    An inscription on a paper-like item that is currently damp or wet reads as
    ``"smudged"``; once the room air dries it back to ``"dry"`` the same
    inscription reads as ``"clear"`` again.  No inscription means ``None``.
    """

    if not _inscription_text(obj):
        return None
    if bool(obj.get("paper_like")) and str(obj.get("moisture") or "dry") in ("wet", "damp"):
        return "smudged"
    return "clear"


def _inscription_suffix(obj: Mapping[str, Any]) -> str:
    """A short, bounded hint that the item carries a written note."""

    text = _inscription_text(obj)
    if not text:
        return ""
    snippet = text if len(text) <= 24 else text[:23] + "…"
    if note_legibility(obj) == "smudged":
        return f"（上面隐约写着：{snippet}）"
    return f"（写着：{snippet}）"


def _craft_role_decoration(state: Mapping[str, Any], obj: Mapping[str, Any]) -> tuple[str, str]:
    """A natural prefix/suffix describing craft/assembly/wear state (or nothing)."""

    prefix = ""
    suffix = ""
    wear = str(obj.get("wear_state") or "intact")
    if wear == "patched":
        prefix = "补过的"
        note = obj.get("patch_note_cn")
        if isinstance(note, str) and note.strip():
            suffix += f"（{note.strip()}）"
    elif wear == "loose_edge":
        prefix = "边缘有点翘的"

    fit_status = str(obj.get("fit_status") or "none")
    if fit_status == "oversized_unfitted":
        suffix += "（偏大，还装不进去）"

    parts = _assembled_parts_of(state, obj.get("object_id"))
    if parts:
        names: list[str] = []
        for pid in parts[:3]:
            part = _object(state, pid)
            if part is not None:
                names.append(_public_label(part))
        if names:
            suffix += f"（装着{'、'.join(names)}等配件）"
    return prefix, suffix


def derived_role_cn(state: Mapping[str, Any], obj: Mapping[str, Any]) -> str:
    """Derive a human-visible role from live state alone."""

    base = _base_role_cn(state, obj)
    prefix, suffix = _craft_role_decoration(state, obj)
    return prefix + base + suffix


def _base_role_cn(state: Mapping[str, Any], obj: Mapping[str, Any]) -> str:

    kind = str(obj.get("item_kind") or "everyday")
    suffix = _proposed_suffix(obj)

    # v0.4 Ch.42: a borrowed thing is described by what it really is -- not mine,
    # return it when done -- never a debt or a hidden score.
    loan = obj.get("loan_meta")
    if isinstance(loan, Mapping) and str(loan.get("loan_status") or "") == "borrowed":
        return "借来的物件 · 用完需归还" + suffix

    if kind == "vessel":
        children = [
            _object(state, cid) for cid in _children_of(state, obj.get("object_id"))
        ]
        children = [child for child in children if child is not None]
        non_food = [
            child for child in children
            if str(child.get("item_kind")) not in ("prepared_food", "drink", "ingredient")
        ]
        food = [
            child for child in children
            if str(child.get("item_kind")) in ("prepared_food", "drink")
        ]
        if non_food:
            names = "、".join(_public_label(child) for child in non_food[:3])
            return f"临时装着{names}（盛饮前需先腾空）{suffix}"
        if food:
            names = "、".join(_public_label(child) for child in food[:3])
            return f"正盛着{names}{suffix}"
        if str(obj.get("cleanliness") or "clean") != "clean":
            return f"用过待洗的容器{suffix}"
        return f"空置可用的容器{suffix}"

    if kind in ("ingredient", "prepared_food", "drink"):
        if str(obj.get("freshness") or "") == "spoiled":
            return "已变质（不可食用）" + suffix
        thermal = str(obj.get("thermal") or "room")
        cook_state = str(obj.get("cook_state") or "none")
        portions = _portion_suffix(obj)
        aging = str(obj.get("freshness") or "") == "aging"
        if kind == "ingredient":
            if cook_state == "raw":
                base = "等待下锅的生鲜食材"
            else:
                base = "处理过的食材"
        elif kind == "drink":
            if thermal == "hot":
                base = "热饮"
            elif thermal == "cold":
                base = "冰饮"
            else:
                base = "饮品"
        else:
            if cook_state == "raw":
                base = "还需加热的生食"
            elif thermal == "hot":
                base = "热乎刚做好的成品"
            elif thermal in ("cold", "cool"):
                base = "放凉了的成品"
            else:
                base = "做好的成品"
        if aging:
            base = "不太新鲜的" + base
        return base + portions + suffix

    if kind == "paper":
        inscription = _inscription_suffix(obj)
        if inscription:
            if note_legibility(obj) == "smudged":
                return "沾水晕开的纸片" + inscription + suffix
            return "写着字的纸页" + inscription + suffix
        if str(obj.get("moisture") or "") in ("wet", "damp"):
            return "受潮的纸页" + suffix
        return "可铺开的纸页" + suffix
    if kind == "tool":
        return "可用的工具" + suffix
    if kind == "keepsake":
        return "留下的纪念小物" + _inscription_suffix(obj) + suffix
    return "日常小物" + _inscription_suffix(obj) + suffix


# ---------------------------------------------------------------------------
# Read-only bridge to shipped affordances
# ---------------------------------------------------------------------------


_AFFORDANCE_MODULE: Any = None


def _affordance() -> Any:
    global _AFFORDANCE_MODULE
    if _AFFORDANCE_MODULE is not None:
        return _AFFORDANCE_MODULE
    for name in ("components.world.scripts.v08_open_affordance", "v08_open_affordance"):
        try:
            _AFFORDANCE_MODULE = importlib.import_module(name)
            return _AFFORDANCE_MODULE
        except Exception:  # noqa: BLE001 - shipping module may not be importable here
            continue
    _AFFORDANCE_MODULE = False
    return False


def _blocks_strenuous_outing(root: Path | str) -> bool:
    module = _affordance()
    if module:
        try:
            return bool(module._blocks_strenuous_outing(root))
        except Exception:  # noqa: BLE001 - fall through to the local zero-write read
            pass
    try:
        path = Path(root) / "phone" / "v03_ecology.json"
        if not path.is_file() or path.is_symlink():
            return False
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(doc, Mapping):
        return False
    for key in ("illness", "capability_envelope"):
        block = doc.get(key)
        if isinstance(block, Mapping) and isinstance(block.get("blocks_strenuous_outing"), bool):
            return bool(block["blocks_strenuous_outing"])
    if isinstance(doc.get("blocks_strenuous_outing"), bool):
        return bool(doc["blocks_strenuous_outing"])
    illness = doc.get("illness") if isinstance(doc.get("illness"), Mapping) else {}
    phase = str(illness.get("phase") or doc.get("phase") or "").strip().lower()
    return phase in ("acute", "acute_illness")


def _discover_open_objects(root: Path | str) -> list[dict[str, Any]]:
    module = _affordance()
    if not module:
        return []
    try:
        rows = module.discover_reachable_objects(root)
    except Exception:  # noqa: BLE001 - a broken catalogue is simply absent
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


_NEIGHBORHOOD_MODULE: Any = None


def _neighborhood() -> Any:
    """Lazily import the shipped v0.4 neighborhood engine (shared shelf/bookstore).

    The import is deferred so this module stays importable on its own and a host
    that has not shipped the neighborhood layer simply gets a named refusal.
    """

    global _NEIGHBORHOOD_MODULE
    if _NEIGHBORHOOD_MODULE is not None:
        return _NEIGHBORHOOD_MODULE
    for name in ("components.world.scripts.v04_neighborhood_living", "v04_neighborhood_living"):
        try:
            _NEIGHBORHOOD_MODULE = importlib.import_module(name)
            return _NEIGHBORHOOD_MODULE
        except Exception:  # noqa: BLE001 - neighborhood layer may be absent here
            continue
    _NEIGHBORHOOD_MODULE = False
    return False


def _apply_neighborhood_command(root: Path | str, payload: dict[str, Any], *, now_text: str) -> dict[str, Any]:
    """Delegate one cross-ring command to v0.4, mapping its refusal honestly.

    The neighborhood engine owns the shelf / bookstore ledger; this layer only
    materialises the single home object that mirrors the real loan.  A domain
    refusal keeps its exact reason code and HTTP status.
    """

    module = _neighborhood()
    if not module:
        raise V08MatterError("NEIGHBORHOOD_UNAVAILABLE", "the neighborhood ledger is unavailable")
    try:
        _status, result, projected = module.apply_v04_neighborhood_command(
            root, payload, actor_side="subject", now_iso=now_text
        )
    except Exception as exc:  # noqa: BLE001 - only v0.4 domain errors are mapped
        reason = getattr(exc, "reason_code", None)
        http_status = getattr(exc, "http_status", None)
        if reason is not None and http_status is not None:
            raise V08MatterError(
                str(reason), str(getattr(exc, "detail", "") or reason), status_code=int(http_status)
            ) from exc
        raise
    return {"result": result, "projected": projected}


# ---------------------------------------------------------------------------
# Custom WorldGen / World-Editor matter (read-only discovery)
# ---------------------------------------------------------------------------


def _read_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    """Parse an existing JSON file without creating or touching anything."""

    try:
        if not path.is_file() or path.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _catalogue_rows(doc: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    rows = doc.get(key)
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _custom_location_scope(value: Any) -> str:
    return str(value or "").strip().lower().replace("\\", "/").split("/")[0]


def _custom_is_home_location(value: Any) -> bool:
    scope = _custom_location_scope(value)
    return bool(scope) and scope in _CUSTOM_HOME_SCOPES


def _custom_looks_like_home_name(value: Any) -> bool:
    text = str(value or "")
    return any(token in text for token in _CUSTOM_HOME_NAME_TOKENS)


def _custom_locations_match(candidate: Any, current: Any) -> bool:
    left = str(candidate or "").strip().lower().replace("\\", "/").rstrip("/")
    right = str(current or "").strip().lower().replace("\\", "/").rstrip("/")
    if not left or not right:
        return False
    if left == right:
        return True
    if left.startswith(right + "/") or right.startswith(left + "/"):
        return True
    return _custom_is_home_location(left) and _custom_is_home_location(right)


def _custom_world_object_matches_location(object_location: Any, current: Any) -> bool:
    location = str(object_location or "").strip()
    if not location:
        return _custom_is_home_location(current)
    if _custom_locations_match(location, current):
        return True
    return _custom_is_home_location(current) and _custom_is_home_location(location)


def _custom_room_map(doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rooms: dict[str, Mapping[str, Any]] = {}
    for room in _catalogue_rows(doc, "rooms"):
        room_id = str(room.get("id") or room.get("room_id") or "").strip()
        if room_id:
            rooms[room_id] = room
    return rooms


def _custom_editor_room_matches(
    room_id: Any,
    rooms: Mapping[str, Mapping[str, Any]],
    current: Any,
) -> bool:
    rid = str(room_id or "").strip()
    if not rooms:
        # An objects-only editor save is one single space.
        if not rid:
            return _custom_is_home_location(current)
        return _custom_locations_match(rid, current)
    if _custom_locations_match(rid, current):
        return True
    if not _custom_is_home_location(current):
        return False
    home_rooms = [
        known_id
        for known_id, room in rooms.items()
        if _custom_is_home_location(known_id) or _custom_looks_like_home_name(room.get("name"))
    ]
    if home_rooms:
        return rid in home_rooms
    return len(rooms) == 1


def _custom_current_location(root: Path | str) -> str:
    module = _affordance()
    if module:
        try:
            found = str(module.current_location_id(root) or "").strip()
            if found:
                return found
        except Exception:  # noqa: BLE001 - fall back to home
            pass
    return "home"


def _custom_object_traits(obj: Mapping[str, Any]) -> list[str]:
    explicit = obj.get("affordance_traits")
    if not isinstance(explicit, Sequence) or isinstance(explicit, (str, bytes, bytearray)):
        explicit = obj.get("traits")
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes, bytearray)):
        traits = [str(trait).strip() for trait in explicit if isinstance(trait, str) and trait.strip()]
        if traits:
            return traits
    kind = str(obj.get("kind") or obj.get("type") or "").strip().lower()
    name = str(obj.get("name") or obj.get("label") or "")
    traits = []
    if kind in _CUSTOM_CONTAINER_KINDS or any(token in name for token in ("桌", "架", "柜", "篮", "箱")):
        traits.append("container")
    if kind in _CUSTOM_KIND_ITEM_KINDS and _CUSTOM_KIND_ITEM_KINDS[kind] == "paper":
        traits.append("inscribable")
    if obj.get("portable") is True:
        traits.append("movable")
    return traits or ["usable"]


def _custom_infer_item_kind(kind: Any, name: Any, traits: Sequence[str]) -> str:
    kind_text = str(kind or "").strip().lower()
    if kind_text in _CUSTOM_KIND_ITEM_KINDS:
        return _CUSTOM_KIND_ITEM_KINDS[kind_text]
    name_text = str(name or "")
    for token, item_kind in _CUSTOM_NAME_ITEM_KINDS:
        if token in name_text:
            return item_kind
    if "container" in traits:
        return "vessel"
    return "everyday"


def _custom_infer_ambient(kind: Any, name: Any) -> str:
    kind_text = str(kind or "").strip().lower()
    name_text = str(name or "")
    if kind_text in _CUSTOM_COLD_KINDS or any(token in name_text for token in _CUSTOM_COLD_TOKENS):
        return "cold"
    if kind_text in _CUSTOM_HEAT_KINDS or any(token in name_text for token in _CUSTOM_HEAT_TOKENS):
        return "heating"
    return "room"


def _custom_infer_water(kind: Any, name: Any) -> bool:
    kind_text = str(kind or "").strip().lower()
    name_text = str(name or "")
    return kind_text in _CUSTOM_WATER_KINDS or any(token in name_text for token in _CUSTOM_WATER_TOKENS)


def _normalize_custom_matter_profile(value: Any) -> dict[str, Any]:
    """Defensively keep only the fields the matter substrate understands."""

    if not isinstance(value, Mapping):
        return {}
    out: dict[str, Any] = {}
    item_kind = str(value.get("item_kind") or "").strip().lower()
    if item_kind in ITEM_KINDS:
        out["item_kind"] = item_kind
    capacity = value.get("capacity_slots")
    if isinstance(capacity, int) and not isinstance(capacity, bool) and 0 <= capacity <= 24:
        out["capacity_slots"] = int(capacity)
    for key in ("paper_like", "water_source", "portable"):
        if isinstance(value.get(key), bool):
            out[key] = value[key]
    shelf_life = value.get("shelf_life_hours")
    if isinstance(shelf_life, (int, float)) and not isinstance(shelf_life, bool) and float(shelf_life) > 0:
        out["shelf_life_hours"] = float(shelf_life)
    portions = value.get("portions")
    if isinstance(portions, int) and not isinstance(portions, bool) and 1 <= portions <= 99:
        out["portions"] = int(portions)
    ambient = str(value.get("ambient_zone") or "").strip().lower()
    if ambient in AMBIENT_ZONES:
        out["ambient_zone"] = ambient
    units = value.get("material_units")
    if isinstance(units, int) and not isinstance(units, bool) and 1 <= units <= 24:
        out["material_units"] = int(units)
    role = str(value.get("assembly_role") or "").strip().lower()
    if role in ASSEMBLY_ROLES:
        out["assembly_role"] = role
    return out


def _custom_world_row(
    object_id: str,
    obj: Mapping[str, Any],
    *,
    source: str,
    location_id: str,
    placement: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    name = str(obj.get("name") or obj.get("label") or object_id).strip()[:40] or object_id[:40]
    kind = str(obj.get("kind") or obj.get("type") or "").strip().lower()
    traits = _custom_object_traits(obj)
    raw_profile = obj.get("matter_profile")
    has_matter_profile = isinstance(raw_profile, Mapping) and bool(raw_profile)
    profile = _normalize_custom_matter_profile(raw_profile)
    if not profile.get("item_kind"):
        profile["item_kind"] = _custom_infer_item_kind(kind, name, traits)
    ambient = str(profile.get("ambient_zone") or _custom_infer_ambient(kind, name)).strip().lower()
    profile["ambient_zone"] = ambient if ambient in AMBIENT_ZONES else "room"
    if "water_source" not in profile:
        profile["water_source"] = _custom_infer_water(kind, name)
    if "capacity_slots" not in profile:
        raw_capacity = obj.get("capacity_slots", obj.get("capacity"))
        if isinstance(raw_capacity, int) and not isinstance(raw_capacity, bool) and raw_capacity >= 0:
            profile["capacity_slots"] = min(24, int(raw_capacity))
        elif placement is not None:
            # The WorldGen scene.json placement is a fallback only: an explicit
            # objects.json capacity (or matter_profile) already won above.
            raw_capacity = placement.get("capacity")
            if isinstance(raw_capacity, int) and not isinstance(raw_capacity, bool) and raw_capacity >= 0:
                profile["capacity_slots"] = min(24, int(raw_capacity))
    explicit_portable: Optional[bool] = profile.get("portable")
    if explicit_portable is None and isinstance(obj.get("portable"), bool):
        explicit_portable = bool(obj.get("portable"))
    if explicit_portable is None and placement is not None and isinstance(placement.get("portable"), bool):
        explicit_portable = bool(placement.get("portable"))
    item_kind = str(profile.get("item_kind") or "everyday")
    capacity = int(profile.get("capacity_slots") or 0)
    handheld_vessel = (
        item_kind == "vessel" and capacity <= 4 and kind not in _CUSTOM_CONTAINER_KINDS
    )
    is_zone = bool(
        explicit_portable is False
        or profile["ambient_zone"] in ("cold", "heating")
        or profile.get("water_source") is True
        or (capacity > 0 and not handheld_vessel)
    )
    return {
        "object_id": object_id,
        "label_cn": name,
        "kind": kind,
        "traits": traits,
        "source": source,
        "location_id": location_id,
        "matter_profile": profile,
        "has_matter_profile": has_matter_profile,
        "portable": explicit_portable is not False,
        "is_zone": is_zone,
    }


def _custom_world_rows(root: Path | str) -> list[dict[str, Any]]:
    """Every custom object authored for the current place; 100% zero-write."""

    base = Path(root)
    current = _custom_current_location(base)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for rel in CUSTOM_WORLD_PACKAGE_FILES:
        pkg_obj_path = base.joinpath(*rel)
        doc = _read_json_zero_write(pkg_obj_path)
        if not doc:
            continue
        # WorldGen writes an optional sibling scene.json next to objects.json;
        # its per-object placement carries portable/capacity when the catalogue
        # row itself does not.  Zero-write: a missing sibling is simply absent.
        scene_doc = _read_json_zero_write(pkg_obj_path.parent / "scene.json")
        placements = (
            scene_doc.get("placements")
            if isinstance(scene_doc, Mapping) and isinstance(scene_doc.get("placements"), Mapping)
            else None
        )
        for obj in _catalogue_rows(doc, "objects"):
            object_id = str(obj.get("object_id") or obj.get("id") or "").strip()
            if not object_id or object_id in seen:
                continue
            location = str(obj.get("location_id") or obj.get("room_id") or "").strip()
            if not _custom_world_object_matches_location(location, current):
                continue
            seen.add(object_id)
            placement = placements.get(object_id) if placements is not None else None
            rows.append(_custom_world_row(
                object_id,
                obj,
                source="world_package",
                location_id=location,
                placement=placement if isinstance(placement, Mapping) else None,
            ))

    for rel in CUSTOM_EDITOR_WORLD_FILES:
        doc = _read_json_zero_write(base.joinpath(*rel))
        if not doc or not isinstance(doc.get("objects"), (list, Mapping)):
            continue
        rooms = _custom_room_map(doc)
        for obj in _catalogue_rows(doc, "objects"):
            object_id = str(obj.get("id") or obj.get("object_id") or "").strip()
            if not object_id or object_id in seen:
                continue
            room_id = str(obj.get("room_id") or obj.get("location_id") or "").strip()
            if not _custom_editor_room_matches(room_id, rooms, current):
                continue
            seen.add(object_id)
            rows.append(_custom_world_row(object_id, obj, source="world_editor", location_id=room_id))
    return rows


def _custom_zone_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    profile = row.get("matter_profile") if isinstance(row.get("matter_profile"), Mapping) else {}
    zone_id = str(row.get("object_id") or "")
    ambient = str(profile.get("ambient_zone") or "room").strip().lower()
    if ambient not in AMBIENT_ZONES:
        ambient = "room"
    capacity = profile.get("capacity_slots")
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
        capacity = 4
    return {
        "zone_id": zone_id,
        "label_cn": str(row.get("label_cn") or zone_id)[:40],
        "ambient_zone": ambient,
        "water_source": bool(profile.get("water_source")),
        "capacity_slots": max(1, min(24, int(capacity))),
        "visibility": "shared",
        "surface_cleanliness": "clean",
        "from_custom_world": True,
    }


def _object_from_custom_world(
    row: Mapping[str, Any],
    *,
    parent_id: str = "home_staging_corner",
) -> dict[str, Any]:
    profile = row.get("matter_profile") if isinstance(row.get("matter_profile"), Mapping) else {}
    object_id = str(row.get("object_id") or "")
    item_kind = str(profile.get("item_kind") or "everyday")
    if item_kind not in ITEM_KINDS:
        item_kind = "everyday"
    capacity = profile.get("capacity_slots")
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
        capacity = 0
    paper_like = bool(profile.get("paper_like")) or item_kind == "paper"
    obj = _object_row(
        object_id,
        str(row.get("label_cn") or object_id)[:60],
        item_kind=item_kind,
        parent_id=parent_id,
        capacity_slots=min(24, int(capacity)),
        paper_like=paper_like,
        material_units=int(profile.get("material_units") or 1),
        assembly_role=str(profile.get("assembly_role") or "none"),
    )
    obj["from_custom_world"] = True
    shelf_life = profile.get("shelf_life_hours")
    if isinstance(shelf_life, (int, float)) and not isinstance(shelf_life, bool) and float(shelf_life) > 0:
        obj["shelf_life_hours"] = float(shelf_life)
        obj["freshness"] = "fresh"
    elif item_kind in ("ingredient", "prepared_food", "drink"):
        obj["freshness"] = "fresh" if item_kind != "drink" else "non_perishable"
    portions = profile.get("portions")
    if isinstance(portions, int) and not isinstance(portions, bool) and portions >= 1:
        obj["portions_total"] = min(99, int(portions))
        obj["portions_left"] = min(99, int(portions))
    return obj


def _discover_custom_world(root: Path | str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (custom zones, custom portable object rows) for the current place."""

    zones: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    try:
        rows = _custom_world_rows(root)
    except Exception:  # noqa: BLE001 - a broken catalogue is simply absent
        return zones, objects
    for row in rows:
        if row.get("is_zone"):
            zones.append(_custom_zone_from_row(row))
        else:
            objects.append(row)
    return zones, objects


def _materialize_custom_zone(
    state: dict[str, Any],
    root: Path | str,
    zone_id: Any,
) -> Optional[dict[str, Any]]:
    """Bring one discovered shared custom zone into the working state on write."""

    tid = str(zone_id or "").strip()
    if not tid:
        return None
    existing = _zone(state, tid)
    if existing is not None:
        return existing
    zones, _ = _discover_custom_world(root)
    for zone in zones:
        if str(zone.get("zone_id") or "") != tid:
            continue
        target = state.setdefault("zones", {})
        target[tid] = dict(zone)
        return dict(zone)
    return None


def _object_from_affordance(row: Mapping[str, Any], *, parent_id: str = "home_staging_corner") -> dict[str, Any]:
    traits = [str(trait) for trait in (row.get("traits") or [])]
    if "container" in traits:
        item_kind = "vessel"
        capacity = 2
    elif "usable" in traits:
        item_kind = "tool"
        capacity = 0
    else:
        item_kind = "everyday"
        capacity = 0
    object_id = str(row.get("object_id") or "").strip()
    obj = _object_row(
        object_id,
        str(row.get("label_cn") or object_id or "物件")[:60],
        item_kind=item_kind,
        parent_id=parent_id,
        capacity_slots=capacity,
    )
    obj["from_open_affordance"] = True
    return obj


def _extras_for_view(state: Mapping[str, Any], root: Path | str) -> list[dict[str, Any]]:
    known_zones = set(state.get("zones") or {})
    known_objects = set(state.get("matter_objects") or {})
    extras: list[dict[str, Any]] = []
    seen: set[str] = set()
    _, custom_objects = _discover_custom_world(root)
    # An authored matter_profile is richer than the generic affordance read of
    # the same catalogue row, so it wins exactly as it does in _ensure_object.
    profiled_ids = {
        str(row.get("object_id") or "").strip()
        for row in custom_objects
        if row.get("has_matter_profile")
    }
    for row in _discover_open_objects(root):
        oid = str(row.get("object_id") or "").strip()
        if not oid or oid in known_zones or oid in known_objects or oid in seen:
            continue
        if oid in profiled_ids:
            continue
        seen.add(oid)
        extras.append(_object_from_affordance(row))
    for row in custom_objects:
        oid = str(row.get("object_id") or "").strip()
        if not oid or oid in known_zones or oid in known_objects or oid in seen:
            continue
        seen.add(oid)
        extras.append(_object_from_custom_world(row))
    return extras


def _ensure_object(
    state: dict[str, Any],
    root: Path | str,
    object_id: Any,
    *,
    actor_side: str,
    now_text: Optional[str] = None,
) -> dict[str, Any]:
    """Return a real object; materialise an external one on first real reference."""

    oid = str(object_id or "").strip()
    if not oid:
        raise V08MatterError("INVALID_FIELDS", "object_id is required")
    existing = _object(state, oid)
    if existing is not None:
        return existing
    if actor_side != "subject":
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {oid}")

    # A custom matter_profile is richer than the generic affordance traits, so
    # it wins when one is authored.  Otherwise the shipped catalogue keeps its
    # existing materialisation (and its legacy ``from_open_affordance`` flag).
    custom_row: Optional[dict[str, Any]] = None
    for row in _discover_custom_world(root)[1]:
        if str(row.get("object_id") or "").strip() == oid:
            custom_row = row
            break
    if custom_row is not None and custom_row.get("has_matter_profile"):
        obj = _object_from_custom_world(custom_row)
        state.setdefault("matter_objects", {})[oid] = obj
        if now_text is not None:
            _anchor_object_clocks_if_needed(state, obj, now_text=now_text)
        return dict(obj)

    for row in _discover_open_objects(root):
        if str(row.get("object_id") or "").strip() != oid:
            continue
        obj = _object_from_affordance(row)
        objects = state.setdefault("matter_objects", {})
        objects[oid] = obj
        if now_text is not None:
            _anchor_object_clocks_if_needed(state, obj, now_text=now_text)
        return dict(obj)
    if custom_row is not None:
        obj = _object_from_custom_world(custom_row)
        state.setdefault("matter_objects", {})[oid] = obj
        if now_text is not None:
            _anchor_object_clocks_if_needed(state, obj, now_text=now_text)
        return dict(obj)
    raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {oid}")


# ---------------------------------------------------------------------------
# Living-needs relief hints (read-only; never creates a profile)
# ---------------------------------------------------------------------------


def _lived_need_relief_hints(root: Path | str, item_kind: str) -> dict[str, Any]:
    """Return a friendly hint only when the instance already opted into needs.

    No profile file is ever created.  A profile that is absent, disabled or
    malformed simply yields no hint and no side effect.
    """

    candidates = (
        Path(root) / "state" / "lived_needs_profile.json",
        Path(root) / "body" / "lived_needs_profile.json",
        Path(root) / "lived_needs_profile.json",
    )
    profile: Optional[dict[str, Any]] = None
    for path in candidates:
        try:
            if not path.is_file() or path.is_symlink():
                continue
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(parsed, Mapping):
            profile = dict(parsed)
            break
    if not profile or profile.get("enabled") is not True:
        return {}

    need = "thirst" if item_kind == "drink" else "hunger" if item_kind in ("prepared_food", "ingredient") else None
    if need is None:
        return {}
    # A gentle, bounded hint; the Body owner remains the only writer of levels.
    return {need: 0.15}


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _projected_placement_relation(state: Mapping[str, Any], obj: Mapping[str, Any]) -> str:
    relation = str(obj.get("placement_relation") or "")
    if relation in PLACEMENT_RELATIONS:
        return relation
    if obj.get("held_by"):
        return ""
    return _default_placement_relation(state, str(obj.get("parent_id") or ""))


def _projected_anchor_page(obj: Mapping[str, Any]) -> Optional[int]:
    """A page mark is a bounded position, never a grant to the page's text."""

    if str(obj.get("placement_relation") or "") != "tucked_in":
        return None
    value = obj.get("anchor_page")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not (1 <= value <= 2000):
        return None
    return value


def _visible_children_ids(
    state: Mapping[str, Any],
    parent_id: str,
    *,
    actor_side: str = "subject",
) -> list[str]:
    children = _children_of(state, parent_id)
    if actor_side != "partner":
        return children
    objs = state.get("matter_objects") or {}
    out: list[str] = []
    for cid in children:
        cobj = objs.get(cid)
        if not isinstance(cobj, Mapping):
            continue
        if cobj.get("_subject_hidden") or _is_private_object(state, cobj):
            continue
        out.append(cid)
    return out


def _visible_assembled_parts_of(
    state: Mapping[str, Any],
    host_id: str,
    *,
    actor_side: str = "subject",
) -> list[str]:
    parts = _assembled_parts_of(state, host_id)
    if actor_side != "partner":
        return parts
    objs = state.get("matter_objects") or {}
    out: list[str] = []
    for pid in parts:
        pobj = objs.get(pid)
        if not isinstance(pobj, Mapping):
            continue
        if pobj.get("_subject_hidden") or _is_private_object(state, pobj):
            continue
        out.append(pid)
    return out


def _object_projection(
    state: Mapping[str, Any],
    obj: Mapping[str, Any],
    *,
    subject_view: bool = False,
    journey_doc: Optional[Mapping[str, Any]] = None,
    now_dt: Optional[datetime] = None,
) -> dict[str, Any]:
    object_id = str(obj.get("object_id") or "")
    actor_side = "subject" if subject_view else "partner"
    children = _visible_children_ids(state, object_id, actor_side=actor_side)
    association = obj.get("subject_association")
    reading_mark = obj.get("reading_mark")
    scalding_remaining = (
        _scalding_remaining_minutes(obj, now_dt) if now_dt is not None else 0
    )
    objects_map = state.get("matter_objects")
    if not isinstance(objects_map, Mapping):
        objects_map = {}
    if object_id not in objects_map:
        objects_map = {**objects_map, object_id: obj}
    location = _derive_effective_location_readonly(
        object_id,
        objects_map,
        zones_by_id=state.get("zones") if isinstance(state.get("zones"), Mapping) else {},
        perspective="subject" if subject_view else "partner",
        journey_doc=journey_doc,
    )
    row = {
        "object_id": object_id,
        "label_cn": obj.get("label_cn"),
        "item_kind": obj.get("item_kind"),
        "parent_id": obj.get("parent_id"),
        "capacity_slots": int(obj.get("capacity_slots") or 0),
        "occupancy": len(children),
        "children_ids": children,
        "thermal": obj.get("thermal"),
        "cook_state": obj.get("cook_state"),
        "moisture": obj.get("moisture"),
        "cleanliness": obj.get("cleanliness"),
        "freshness": obj.get("freshness"),
        "portions_left": obj.get("portions_left"),
        "portions_total": obj.get("portions_total"),
        "paper_like": bool(obj.get("paper_like")),
        "inscribed_text_cn": _inscription_text(obj),
        "note_legibility": note_legibility(obj),
        "requires_cooking": bool(obj.get("requires_cooking")),
        "lent_by": obj.get("lent_by"),
        # v0.4 Ch.42: a borrowed neighborhood item keeps its honest loan record on
        # the very same home object; there is never a second copy.
        "loan_meta": copy.deepcopy(obj.get("loan_meta")),
        "delivered_to_board": obj.get("delivered_to_board"),
        "material_units": max(1, int(obj.get("material_units") or 1)),
        "assembly_role": obj.get("assembly_role") or "none",
        "assembled_into_id": obj.get("assembled_into_id"),
        "assembled_parts_ids": _visible_assembled_parts_of(state, object_id, actor_side=actor_side),
        "fit_status": obj.get("fit_status") or "none",
        "transcribed_source": copy.deepcopy(obj.get("transcribed_source")),
        "wear_state": obj.get("wear_state") or "intact",
        "patch_note_cn": obj.get("patch_note_cn"),
        "visibility": obj.get("visibility") or "shared",
        "derived_role_cn": derived_role_cn(state, obj),
        "subject_last_seen_parent": obj.get("subject_last_seen_parent"),
        "impression_stale": bool(obj.get("impression_stale")),
        "last_observed_at": obj.get("last_observed_at"),
        "shelf_life_hours": obj.get("shelf_life_hours"),
        "proposed_use": copy.deepcopy(obj.get("proposed_use")),
        # Layer 2: how it sits on/in a carrier, and whether it is in hand.
        "placement_relation": _projected_placement_relation(state, obj),
        "posture_tag": str(obj.get("posture_tag") or ""),
        "anchor_page": _projected_anchor_page(obj),
        "held_by": obj.get("held_by"),
        "open_state": _object_open_state(obj),
        # Ch.39 §二/§六: carrier-chain location is derived live on every read.
        # Child rows keep one real ``parent_id``; moving the carrier never
        # rewrites them, and a subject projection only walks what she believes.
        "effective_zone_id": location["effective_zone_id"],
        "effective_place_id": location["effective_place_id"],
        "carrier_chain": list(location["carrier_chain"]),
        "carried_on_journey": bool(location["carried_on_journey"]),
        # Layer 3: an attributed personal association, never objective fact.
        "subject_association": (
            copy.deepcopy(dict(association)) if isinstance(association, Mapping) else None
        ),
        # Ch.21 §3: "just poured, still too hot" is objective physics with a
        # real deadline; the page mark is a position, never a claim of mastery.
        "scalding_until_iso": obj.get("scalding_until_iso"),
        "scalding_wait_minutes": int(obj.get("scalding_wait_minutes") or 0),
        "scalding": scalding_remaining > 0,
        "scalding_remaining_minutes": scalding_remaining,
        "reading_mark": (
            copy.deepcopy(dict(reading_mark)) if isinstance(reading_mark, Mapping) else None
        ),
        "consecutive_returns": int(obj.get("consecutive_returns") or 0),
        "preferred_home_slot": obj.get("preferred_home_slot"),
        "is_habitual_slot": bool(
            obj.get("is_habitual_slot") or int(obj.get("consecutive_returns") or 0) >= 3
        ),
        "active": _is_active(obj),
    }
    for opt_key in (
        "provenance_tag",
        "provenance_cn",
        "tucked_note_cn",
        "empty_box_occupied_by_cat",
        "unpacked",
    ):
        if opt_key in obj:
            row[opt_key] = copy.deepcopy(obj[opt_key])
    if subject_view:
        # The subject never learns that a partner moved something until she
        # actually reaches/inspects it.  The stale flag is a God-view fact.
        row.pop("impression_stale", None)
        row["not_at_believed_spot"] = bool(obj.get("not_at_believed_spot"))
    if obj.get("from_open_affordance"):
        row["from_open_affordance"] = True
    return row


def _zone_projection(
    state: Mapping[str, Any],
    zone: Mapping[str, Any],
    *,
    actor_side: str = "subject",
) -> dict[str, Any]:
    zone_id = str(zone.get("zone_id") or "")
    children = _visible_children_ids(state, zone_id, actor_side=actor_side)
    return {
        "zone_id": zone_id,
        "label_cn": zone.get("label_cn"),
        "ambient_zone": zone.get("ambient_zone"),
        "water_source": bool(zone.get("water_source")),
        "capacity_slots": int(zone.get("capacity_slots") or 0),
        "occupancy": len(children),
        "children_ids": children,
        "visibility": zone.get("visibility") or "shared",
        "surface_cleanliness": zone.get("surface_cleanliness") or "clean",
    }


def _is_private_object(state: Mapping[str, Any], obj: Mapping[str, Any]) -> bool:
    if obj.get("private") is True or obj.get("unshared") is True:
        return True
    if str(obj.get("visibility") or "shared").strip().lower() == "private":
        return True
    objs = state.get("matter_objects") if isinstance(state.get("matter_objects"), Mapping) else {}
    cur = obj.get("parent_id") or obj.get("zone_id") or obj.get("location")
    seen: set[str] = set()
    oid = str(obj.get("object_id") or "")
    if oid:
        seen.add(oid)
    for _ in range(24):
        if not cur:
            break
        cur_id = str(cur)
        if cur_id in seen:
            break
        seen.add(cur_id)
        zone = _zone(state, cur_id)
        if zone is not None:
            return _is_private_zone(zone)
        pobj = objs.get(cur_id)
        if not isinstance(pobj, Mapping):
            break
        if pobj.get("private") is True or pobj.get("unshared") is True:
            return True
        if str(pobj.get("visibility") or "shared").strip().lower() == "private":
            return True
        cur = pobj.get("parent_id") or pobj.get("zone_id") or pobj.get("location")
    return False


def _is_private_zone(zone: Mapping[str, Any]) -> bool:
    return str(zone.get("visibility") or "shared") == "private"


# ---------------------------------------------------------------------------
# Public read view (100% zero-write)
# ---------------------------------------------------------------------------


#: Item kinds / markers that count as real craft or writing matter at hand.
def _is_craft_ready(obj: Mapping[str, Any]) -> bool:
    kind = str(obj.get("item_kind") or "")
    if kind in ("tool", "paper"):
        return True
    if bool(obj.get("paper_like")):
        return True
    if str(obj.get("assembly_role") or "none") != "none":
        return True
    if str(obj.get("fit_status") or "none") != "none":
        return True
    if kind == "material" and int(obj.get("material_units") or 0) >= 1:
        return True
    return False


_STOW_ZONE_HINTS: tuple[str, ...] = ("抽屉", "柜", "收纳", "箱", "staging", "drawer", "cabinet")


def _is_stow_zone(zone_id: str, zone: Optional[Mapping[str, Any]]) -> bool:
    if str(zone_id or "") in ("home_staging_corner", "subject_private_drawer"):
        return True
    haystack = f"{zone_id} {str((zone or {}).get('label_cn') or '')}".lower()
    return any(hint in haystack for hint in _STOW_ZONE_HINTS)


def _absence_epistemics_rows_zero_write(
    root: Path | str,
    *,
    observed_zone_ids: Optional[Sequence[str]] = None,
    occluded_zone_ids: Optional[Sequence[str]] = None,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
    _state_override: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Every anchored-but-absent familiar object plus what the subject can know.

    A spot only becomes an expectation after the object has genuinely lived in
    that same zone at least twice (``familiar_spot_anchor_zone``).  An occluded
    or simply non-observed expected zone yields ``unknown_unobserved`` -- never a
    fabricated "it is gone" story.
    """

    if actor_side not in ("subject", "partner"):
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")

    now_dt, now_text = _resolve_now(now_iso)
    if _state_override is not None:
        state = dict(_state_override)
    else:
        state = _project_state(_read_state_zero_write(root), now_dt, now_text, root=root)
    observed = set(str(z) for z in observed_zone_ids) if observed_zone_ids is not None else None
    occluded = set(str(z) for z in occluded_zone_ids) if occluded_zone_ids is not None else set()

    rows: list[dict[str, Any]] = []
    for oid in sorted(state.get("matter_objects") or {}):
        obj = state["matter_objects"][oid]
        if not isinstance(obj, Mapping) or not _is_active(obj):
            continue
        if obj.get("_subject_hidden"):
            continue
        if actor_side == "partner" and _is_private_object(state, obj):
            continue
        anchor = str(obj.get("familiar_spot_anchor_zone") or "").strip()
        if not anchor:
            continue  # never lived there twice: no expectation to notice
        current_zone = _zone_id_for(state, obj)
        if current_zone is not None and str(current_zone) == anchor:
            continue  # right where it is normally kept: nothing to notice

        zone = _zone(state, anchor)
        zone_label = str((zone or {}).get("label_cn") or "原来的地方")
        label = _public_label(obj)
        if anchor in occluded or (observed is not None and anchor not in observed):
            epistemic = "unknown_unobserved"
            cue_cn: Optional[str] = None
        else:
            epistemic = "witnessed_empty"
            cue_cn = f"平时搁在{zone_label}的「{label}」这会儿空着"
        rows.append({
            "object_id": oid,
            "label_cn": label,
            "expected_zone_id": anchor,
            "zone_label_cn": zone_label,
            "epistemic_state": epistemic,
            "cue_cn": cue_cn,
            "zero_blame": True,
        })
    return rows


def inspect_absence_epistemics_zero_write(
    root: Path | str,
    *,
    observed_zone_ids: Optional[Sequence[str]] = None,
    occluded_zone_ids: Optional[Sequence[str]] = None,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Pure helper: the epistemic state of every familiar absence (zero-write)."""

    return _absence_epistemics_rows_zero_write(
        root,
        observed_zone_ids=observed_zone_ids,
        occluded_zone_ids=occluded_zone_ids,
        actor_side=actor_side,
        now_iso=now_iso,
    )


def project_familiar_absence_cues_zero_write(
    root: Path | str,
    *,
    observed_zone_ids: Optional[Sequence[str]] = None,
    occluded_zone_ids: Optional[Sequence[str]] = None,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
    _state_override: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Calm, blame-free only-witnessed-empty cues; occluded spots stay unknown.

    Reads are 100% zero-write.  Partner projections never see private matter.
    ``_state_override`` lets a caller reuse an already-projected (and, for the
    subject, already-believed) state so an absence cue can never be derived from
    the hidden ground truth a subject has not witnessed yet.
    """

    rows = _absence_epistemics_rows_zero_write(
        root,
        observed_zone_ids=observed_zone_ids,
        occluded_zone_ids=occluded_zone_ids,
        actor_side=actor_side,
        now_iso=now_iso,
        _state_override=_state_override,
    )
    return [row for row in rows if row.get("epistemic_state") == "witnessed_empty"]


def project_workspace_scaffolds_zero_write(
    root: Path | str,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
    _state_override: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Purely derived "how easy it is to pick the thread back up" cues.

    No hidden phase score, no streak counter, no fake progress.  Every row is a
    live reading of what is actually sitting on an open surface; stowing things
    away lowers the ease and laying them back out raises it again.
    """

    if actor_side not in ("subject", "partner"):
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")

    now_dt, now_text = _resolve_now(now_iso)
    if _state_override is not None:
        state = dict(_state_override)
    else:
        state = _project_state(_read_state_zero_write(root), now_dt, now_text, root=root)

    def visible(obj: Mapping[str, Any]) -> bool:
        if obj.get("_subject_hidden"):
            return False
        return not (actor_side == "partner" and _is_private_object(state, obj))

    def zone_for(zone_id: str) -> Optional[Mapping[str, Any]]:
        zone = _zone(state, zone_id)
        if zone is None:
            return None
        if actor_side == "partner" and _is_private_zone(zone):
            return None
        return zone

    def children(zone_id: str) -> list[Mapping[str, Any]]:
        out: list[Mapping[str, Any]] = []
        for oid in _children_of(state, zone_id):
            obj = state["matter_objects"].get(oid)
            if isinstance(obj, Mapping) and visible(obj):
                out.append(obj)
        return out

    def any_stowed(predicate) -> bool:
        """Is a matching item tucked into a staging corner / drawer / cabinet?"""

        for zid, zone in (state.get("zones") or {}).items():
            if str(zid) == "home_desk" or str(zid) == "kitchen_counter":
                continue
            if actor_side == "partner" and _is_private_zone(zone):
                continue
            if not _is_stow_zone(str(zid), zone):
                continue
            for oid in _children_of(state, str(zid)):
                obj = state["matter_objects"].get(oid)
                if isinstance(obj, Mapping) and visible(obj) and predicate(obj):
                    return True
        return False

    def row(zone_id: str, zone: Mapping[str, Any], domain: str, ease: str, summary: str, ids: list[str]) -> dict[str, Any]:
        return {
            "zone_id": zone_id,
            "zone_label_cn": zone.get("label_cn"),
            "ready_domain": domain,
            "startup_ease": ease,
            "summary_cn": summary,
            "derived_from_objects": ids,
            "zero_write": True,
        }

    scaffolds: list[dict[str, Any]] = []

    # 1) The writing / craft desk.
    desk = zone_for("home_desk")
    if desk is not None:
        desk_children = children("home_desk")
        desk_ready = [o for o in desk_children if _is_craft_ready(o)]
        cap = int(desk.get("capacity_slots") or 0)
        free = max(0, cap - len(desk_children if actor_side == "partner" else _children_of(state, "home_desk")))
        if desk_ready:
            scaffolds.append(row(
                "home_desk", desk, "craft_or_writing", "ready_at_hand",
                "书桌上有摆开的工具/纸张，接着动手或写画很顺手",
                [str(o.get("object_id")) for o in desk_ready],
            ))
        elif any_stowed(_is_craft_ready):
            scaffolds.append(row(
                "home_desk", desk, "craft_or_writing", "stowed_away",
                "桌边家伙什儿已经收进去了，重新摊开要多费一道手",
                [],
            ))
        elif cap > 0 and free == 0:
            scaffolds.append(row(
                "home_desk", desk, "craft_or_writing", "crowded_surface",
                "书桌上东西堆满了，腾不出地方接着动手",
                [],
            ))

    # 2) The kitchen counter: a clean cup, ready ingredients, or a dirty bowl.
    counter = zone_for("kitchen_counter")
    if counter is not None:
        kids = children("kitchen_counter")
        clean_vessels = [
            o for o in kids
            if str(o.get("item_kind")) == "vessel" and str(o.get("cleanliness") or "clean") == "clean"
        ]
        ready_food = [
            o for o in kids
            if str(o.get("item_kind")) in ("ingredient", "drink")
            and str(o.get("freshness") or "") != "spoiled"
        ]
        soiled_vessels = [
            o for o in kids
            if str(o.get("item_kind")) == "vessel"
            and str(o.get("cleanliness") or "clean") in ("used", "soiled")
        ]
        ready_ids = [str(o.get("object_id")) for o in clean_vessels + ready_food]
        if ready_ids:
            scaffolds.append(row(
                "kitchen_counter", counter, "warm_tea_or_cooking", "ready_at_hand",
                "备餐台上有干净的杯碗和食材，烧水或做点热的很顺手",
                ready_ids,
            ))
        elif soiled_vessels:
            scaffolds.append(row(
                "kitchen_counter", counter, "warm_tea_or_cooking", "needs_cleanup",
                "备餐台上的杯碗还没洗，得先刷干净才好用",
                [str(o.get("object_id")) for o in soiled_vessels],
            ))
        elif any_stowed(
            lambda o: str(o.get("item_kind")) in ("vessel", "ingredient", "drink")
        ):
            scaffolds.append(row(
                "kitchen_counter", counter, "warm_tea_or_cooking", "stowed_away",
                "备餐的家伙什儿都收起来了，要用得先去拿",
                [],
            ))

    return scaffolds


def _project_active_arrangements(
    state: Mapping[str, Any],
    *,
    actor_side: str,
    subject_view: bool,
) -> list[dict[str, Any]]:
    """Live projection: an arrangement is real only while every member is real.

    Nothing here trusts a stored "still intact" boolean.  A subject projection
    evaluates members against the state she is authorised to believe (no hidden
    partner move leaks pre-miss); a break she herself caused is honoured
    immediately so putting the clip back cannot silently resurrect the line.
    """

    arrangements = state.get("arrangements")
    if not isinstance(arrangements, Mapping):
        return []
    objects = state.get("matter_objects") or {}
    out: list[dict[str, Any]] = []
    for aid in sorted(arrangements):
        record = arrangements[aid]
        if not isinstance(record, Mapping):
            continue
        if record.get("broken_at"):
            invalidated_by = str(record.get("invalidated_by") or "")
            if not subject_view or invalidated_by == "subject":
                continue
        zone_id = str(record.get("zone_or_carrier_id") or "")
        members = [str(item) for item in (record.get("member_ids") or []) if str(item)]
        if len(members) < 2:
            continue
        zone = _zone(state, zone_id)
        carrier = _object(state, zone_id)
        if actor_side == "partner":
            if zone is not None and _is_private_zone(zone):
                continue
            if carrier is not None and _is_private_object(state, carrier):
                continue
        intact = True
        for member_id in members:
            member = objects.get(member_id)
            if not isinstance(member, Mapping) or not _is_active(member):
                intact = False
                break
            if member.get("_subject_hidden"):
                intact = False
                break
            if member.get("held_by"):
                intact = False
                break
            if actor_side == "partner" and _is_private_object(state, member):
                intact = False
                break
            if str(member.get("parent_id") or "") != zone_id:
                intact = False
                break
        if not intact:
            continue
        association = record.get("subject_association")
        out.append({
            "arrangement_id": str(record.get("arrangement_id") or aid),
            "zone_or_carrier_id": zone_id,
            "member_ids": members,
            "pattern": str(record.get("pattern") or ""),
            "created_by": record.get("created_by"),
            "created_at": record.get("created_at"),
            "subject_association": (
                copy.deepcopy(dict(association)) if isinstance(association, Mapping) else None
            ),
        })
    return out


def _project_zone_nicknames(
    state: Mapping[str, Any], *, actor_side: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Live nickname projection: an unshared name is wholly absent for a partner.

    There is no ``hidden_count`` and no placeholder row -- the partner view is
    byte-identical to a world where the private name was never coined, so no
    shared understanding can be assumed before it is explicitly shared.
    """

    raw = state.get("zone_nicknames")
    rows: list[dict[str, Any]] = []
    by_zone: dict[str, str] = {}
    if not isinstance(raw, Mapping):
        return rows, by_zone
    for zid in sorted(raw):
        entry = raw[zid]
        if not isinstance(entry, Mapping):
            continue
        text = str(entry.get("nickname_cn") or "").strip()
        if not text:
            continue
        shared = bool(entry.get("shared_with_partner"))
        if actor_side != "subject" and not shared:
            continue
        name = text[:MAX_NICKNAME_CHARS]
        rows.append({
            "zone_id": str(zid),
            "nickname_cn": name,
            "shared_with_partner": shared,
        })
        by_zone[str(zid)] = name
    return rows, by_zone


_STAGING_CORNER_ID = "home_staging_corner"


def _project_staging_corner(state: Mapping[str, Any], *, actor_side: str) -> dict[str, Any]:
    """Ch.25: the "顺手暂放角" as a calm, zero-write projection.

    Never a guilt score and never a debt: landing something here is simply
    "put it down for now".  Private matter is invisible to the partner, so the
    partner's slot count and labels must not include it (no side channel).
    """

    zone = _zone(state, _STAGING_CORNER_ID) or {}
    capacity_slots = int(zone.get("capacity_slots") or 4)
    item_ids: list[str] = []
    item_labels_cn: list[str] = []
    for oid in sorted(state.get("matter_objects") or {}):
        obj = state["matter_objects"][oid]
        if not isinstance(obj, Mapping) or not _is_active(obj):
            continue
        if obj.get("_subject_hidden"):
            continue
        if str(obj.get("parent_id") or "") != _STAGING_CORNER_ID:
            continue
        if actor_side == "partner" and _is_private_object(state, obj):
            continue
        item_ids.append(oid)
        item_labels_cn.append(_public_label(obj))
    occupied_slots = len(item_ids)
    is_full = occupied_slots >= capacity_slots
    if is_full:
        note_cn = "暂放角已经放满，先挪开一件再放新东西"
    elif occupied_slots > 0:
        note_cn = "暂时放这儿，先不用急着归位"
    else:
        note_cn = ""
    return {
        "zone_id": _STAGING_CORNER_ID,
        "label_cn": str(zone.get("label_cn") or "顺手暂放角"),
        "capacity_slots": capacity_slots,
        "occupied_slots": occupied_slots,
        "free_slots": max(0, capacity_slots - occupied_slots),
        "item_ids": item_ids,
        "item_labels_cn": item_labels_cn,
        "is_full": is_full,
        "obligation_debt": 0,
        "messy_guilt": False,
        "note_cn": note_cn,
    }


def read_matter_space_view(
    root: Path | str,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Describe the real space.  Subject sees all; partner never sees private matter.

    The read is 100% zero-write: no directory, no file and no mtime is touched.
    """

    if actor_side not in ("subject", "partner"):
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")

    now_dt, now_text = _resolve_now(now_iso)
    live_state = _project_state(_read_state_zero_write(root), now_dt, now_text, root=root)
    subject_view = actor_side == "subject"
    state = _subject_believed_state(live_state) if subject_view else live_state
    # Ch.39 §六: the real journey record is read strictly zero-write and only
    # used to derive where a carried carrier currently is.
    journey_doc = _read_neighborhood_journey_zero_write(root)

    nickname_rows, nickname_by_zone = _project_zone_nicknames(state, actor_side=actor_side)

    zones_out: list[dict[str, Any]] = []
    for zid in sorted(state.get("zones") or {}):
        zone = state["zones"][zid]
        if actor_side == "partner" and _is_private_zone(zone):
            continue
        zone_row = _zone_projection(state, zone, actor_side=actor_side)
        nickname = nickname_by_zone.get(str(zid))
        if nickname:
            zone_row["nickname_cn"] = nickname
        zones_out.append(zone_row)

    # Custom WorldGen / World-Editor zones exist in the real space before their
    # first write; overlay them in memory only (never materialise on a read).
    known_zone_ids = set(state.get("zones") or {})
    custom_zones, _ = _discover_custom_world(root)
    for zone in sorted(custom_zones, key=lambda row: str(row.get("zone_id") or "")):
        zid = str(zone.get("zone_id") or "")
        if not zid or zid in known_zone_ids:
            continue
        if actor_side == "partner" and _is_private_zone(zone):
            continue
        zone_row = _zone_projection(state, zone, actor_side=actor_side)
        nickname = nickname_by_zone.get(str(zid))
        if nickname:
            zone_row["nickname_cn"] = nickname
        zones_out.append(zone_row)

    objects_out: list[dict[str, Any]] = []
    for oid in sorted(state.get("matter_objects") or {}):
        obj = state["matter_objects"][oid]
        if not _is_active(obj):
            continue
        if obj.get("_subject_hidden"):
            continue
        if actor_side == "partner" and _is_private_object(state, obj):
            continue
        objects_out.append(
            _object_projection(
                state, obj, subject_view=subject_view, journey_doc=journey_doc, now_dt=now_dt
            )
        )
    for extra in _extras_for_view(state, root):
        if extra.get("_subject_hidden"):
            continue
        if actor_side == "partner" and _is_private_object(state, extra):
            continue
        objects_out.append(
            _object_projection(
                state, extra, subject_view=subject_view, journey_doc=journey_doc, now_dt=now_dt
            )
        )

    recent_acts = copy.deepcopy(state.get("recent_acts") or [])
    if actor_side == "partner":
        recent_acts = []
    elif subject_view:
        # A partner-only act must not hint through the subject's recent list.
        recent_acts = [row for row in recent_acts if row.get("actor_side") != "partner"]

    return {
        "schema": VIEW_SCHEMA,
        "actor_side": actor_side,
        "generated_at": now_text,
        "subject_view_revision": int(live_state.get("subject_view_revision") or 0),
        "zones": zones_out,
        "objects": objects_out,
        # Ch.16 §3 #13: the subject's own corner vocabulary, already filtered.
        "zone_nicknames": nickname_rows,
        # Live-projected: a single picked-up/moved member drops the whole line.
        "active_arrangements": _project_active_arrangements(
            state, actor_side=actor_side, subject_view=subject_view
        ),
        "recent_acts": recent_acts,
        # Ch.16: an absent familiar thing only becomes a calm "空着" cue when
        # that spot is genuinely witnessed empty; occluded spots stay unknown.
        "familiar_absence_cues": project_familiar_absence_cues_zero_write(
            root, actor_side=actor_side, now_iso=now_text, _state_override=state
        ),
        # Ch.16: live, physically derived startup ease -- never a hidden score.
        "workspace_scaffolds": project_workspace_scaffolds_zero_write(
            root, actor_side=actor_side, now_iso=now_text, _state_override=state
        ),
        # Ch.25: the light landing corner -- slots only, never guilt or debt.
        "staging_corner": _project_staging_corner(state, actor_side=actor_side),
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Primitive implementations (mutate the working state; return a step receipt)
# ---------------------------------------------------------------------------


def _carry_assembled_parts(
    state: dict[str, Any],
    host_id: str,
    target_id: str,
    *,
    now_text: str,
    mark_stale: bool,
) -> list[str]:
    """Move every fitted insert with its host, honestly occupying the new place."""

    carried: list[str] = []
    for part_id in _assembled_parts_of(state, host_id):
        live = state["matter_objects"].get(part_id)
        if not isinstance(live, dict):
            continue
        old_ambient = _ambient_for(state, live)
        if mark_stale:
            # A partner moved the host: freeze what the subject last saw, then
            # let the real object slip away underneath that belief.
            if live.get("subject_last_seen_parent") and not live.get("impression_stale"):
                live["subject_last_seen_snapshot"] = _capture_subject_snapshot(live)
            live["parent_id"] = target_id
            live["impression_stale"] = True
        else:
            live["parent_id"] = target_id
            _refresh_subject_belief(live)
        _anchor_object_clocks_if_needed(state, live, now_text=now_text, old_ambient=old_ambient)
        carried.append(part_id)
    return carried


def _apply_place_or_move(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    default_relation: Optional[str] = None,
    op: str = "PLACE_OR_MOVE",
) -> dict[str, Any]:
    obj = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    target_id = _validate_target_exists(state, step.get("target_parent_id"), root=root)
    object_id = str(obj["object_id"])
    live = state["matter_objects"][object_id]
    old_parent = str(live.get("parent_id") or "")
    moved = old_parent != target_id

    explicit_relation = step.get("placement_relation")
    if explicit_relation is not None:
        relation = _choice(explicit_relation, PLACEMENT_RELATIONS, field="placement_relation")
    elif moved:
        relation = default_relation or _default_placement_relation(state, target_id)
    else:
        # Re-settling something in the same spot never reinterprets its posture.
        relation = str(live.get("placement_relation") or "") or _default_placement_relation(state, target_id)

    posture_given = step.get("posture_tag") is not None
    if posture_given:
        posture = _choice(step.get("posture_tag"), POSTURE_TAGS, field="posture_tag")
    else:
        posture = str(live.get("posture_tag") or "")
        if relation not in CARRIER_BOUND_RELATIONS:
            posture = _intrinsic_posture(posture)
        elif moved and str(live.get("placement_relation") or "") != relation:
            posture = _intrinsic_posture(posture)

    anchor_page: Optional[int] = None
    if relation == "tucked_in":
        if step.get("anchor_page") is not None:
            anchor_page = _int_field(
                step["anchor_page"], field="anchor_page", minimum=1, maximum=2000
            )
        elif not moved and str(live.get("placement_relation") or "") == "tucked_in":
            raw_anchor = live.get("anchor_page")
            if isinstance(raw_anchor, int) and not isinstance(raw_anchor, bool):
                anchor_page = raw_anchor
    elif step.get("anchor_page") is not None:
        raise V08MatterError("INVALID_FIELDS", "anchor_page is only meaningful for tucked_in")

    if relation not in CARRIER_BOUND_RELATIONS and not posture_given:
        posture = _intrinsic_posture(posture)

    _validate_placement(state, obj, target_id, carrier_relation=relation)
    _validate_carrier_relation(state, target_id, relation, anchor_page)
    old_ambient = _ambient_for(state, obj)

    live["parent_id"] = target_id
    live["held_by"] = None
    live["placement_relation"] = relation
    live["posture_tag"] = posture
    live["anchor_page"] = anchor_page
    if step.get("open_state") is not None:
        live["open_state"] = _choice(step.get("open_state"), _OPEN_STATES, field="open_state")
    _refresh_subject_belief(live)
    _note_familiar_contact(state, live, zone_id=_zone_id_for(state, live))
    _anchor_object_clocks_if_needed(state, live, now_text=now_text, old_ambient=old_ambient)
    broken = _break_arrangements_for_member(
        state, object_id, now_text=now_text, reason="MEMBER_MOVED", actor_side="subject"
    ) if moved else []
    carried = _carry_assembled_parts(state, object_id, target_id, now_text=now_text, mark_stale=False)
    return {
        "op": op,
        "status": "applied",
        "object_id": obj["object_id"],
        "target_parent_id": target_id,
        "placement_relation": relation,
        "posture_tag": posture,
        "anchor_page": anchor_page,
        "broken_arrangement_ids": broken,
        "carried_part_ids": carried,
    }


def _apply_take_object(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Pick an object up: the carrier binding breaks, intrinsic traces stay."""

    obj = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    object_id = str(obj["object_id"])
    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict) or not _is_active(live):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    holder = str(live.get("held_by") or "")
    if holder and holder != "subject":
        raise V08MatterError("INVALID_FIELDS", "that object is held by someone else")
    previous_relation = str(live.get("placement_relation") or "")
    live["held_by"] = "subject"
    _clear_carrier_binding(live, relation="")
    _refresh_subject_belief(live)
    broken = _break_arrangements_for_member(
        state, object_id, now_text=now_text, reason="MEMBER_TAKEN", actor_side="subject"
    )
    return {
        "op": "TAKE_OBJECT",
        "status": "applied",
        "object_id": object_id,
        "held_by": "subject",
        "previous_placement_relation": previous_relation,
        "placement_relation": "",
        "posture_tag": str(live.get("posture_tag") or ""),
        "anchor_page": None,
        "broken_arrangement_ids": broken,
    }


def _apply_arrange_objects(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    actor_side: str = "subject",
) -> dict[str, Any]:
    """Record one intentional group arrangement; validity is projected live."""

    target_id = _validate_target_exists(state, step.get("zone_or_carrier_id"), root=root)
    raw_members = step.get("member_ids")
    if not isinstance(raw_members, (list, tuple)):
        raise V08MatterError("INVALID_FIELDS", "member_ids must be a list of object ids")
    member_ids: list[str] = []
    for raw in raw_members:
        member = str(raw or "").strip()
        if not member:
            raise V08MatterError("INVALID_FIELDS", "member_ids must not contain blanks")
        if member in member_ids:
            raise V08MatterError("INVALID_FIELDS", "member_ids must be unique")
        member_ids.append(member)
    if not (2 <= len(member_ids) <= 8):
        raise V08MatterError("INVALID_FIELDS", "member_ids must contain 2..8 objects")
    pattern = _choice(step.get("pattern"), ARRANGEMENT_PATTERNS, field="pattern")
    for member_id in member_ids:
        live = state["matter_objects"].get(member_id)
        if not isinstance(live, dict) or not _is_active(live):
            raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {member_id}")
        if live.get("held_by"):
            raise V08MatterError("INVALID_FIELDS", f"object is held, not placed: {member_id}")
        if str(live.get("parent_id") or "") != target_id:
            raise V08MatterError(
                "INVALID_TARGET",
                "every member must already sit directly at the arrangement spot",
            )
    arrangement_id = _optional_text(step.get("arrangement_id"), max_len=64, field="arrangement_id")
    if arrangement_id is None:
        arrangement_id = _new_object_id("arr", target_id, pattern, *member_ids, now_text)
    initial_association: Optional[dict[str, Any]] = None
    raw_assoc = step.get("subject_association")
    raw_assoc_text = None
    if isinstance(raw_assoc, Mapping):
        raw_assoc_text = raw_assoc.get("text")
    elif raw_assoc is None:
        raw_assoc_text = step.get("association_text")
    if raw_assoc_text is not None:
        initial_association = {
            "by": actor_side,
            "text": _text(raw_assoc_text, min_len=1, max_len=MAX_ASSOCIATION_CHARS, field="text"),
            "at": now_text,
        }
    record = {
        "arrangement_id": arrangement_id,
        "zone_or_carrier_id": target_id,
        "member_ids": member_ids,
        "pattern": pattern,
        "created_by": actor_side,
        "created_at": now_text,
        "subject_association": initial_association,
        "broken_at": None,
        "invalidated_reason": None,
        "invalidated_by": None,
    }
    existing = state.setdefault("arrangements", {}).get(arrangement_id)
    if initial_association is None and isinstance(existing, Mapping) and isinstance(existing.get("subject_association"), Mapping):
        record["subject_association"] = copy.deepcopy(dict(existing["subject_association"]))
    state["arrangements"][arrangement_id] = record
    return {
        "op": "ARRANGE_OBJECTS",
        "status": "applied",
        "arrangement_id": arrangement_id,
        "zone_or_carrier_id": target_id,
        "member_ids": member_ids,
        "pattern": pattern,
    }


def _apply_note_association(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    actor_side: str = "subject",
) -> dict[str, Any]:
    """Store one attributed personal association.  Physics is never touched."""

    target_id = _optional_text(step.get("target_id"), max_len=64, field="target_id")
    if target_id is None:
        raise V08MatterError("INVALID_FIELDS", "target_id is required")
    raw_text = step.get("text")
    if raw_text is None:
        raw_text = step.get("association_text")
    text = _text(raw_text, min_len=1, max_len=MAX_ASSOCIATION_CHARS, field="text")
    # Ch.16 §3 #14: a bookmark is a position, not a claim of understanding.
    reading_mark = _reading_mark_from_step(step, now_text=now_text)
    association = {"by": actor_side, "text": text, "at": now_text}

    arrangements = state.get("arrangements")
    if isinstance(arrangements, dict) and target_id in arrangements and _object(state, target_id) is None:
        record = arrangements[target_id]
        if not isinstance(record, dict):
            raise V08MatterError("OBJECT_NOT_FOUND", f"unknown target: {target_id}")
        record["subject_association"] = association
        if reading_mark is not None:
            record["reading_mark"] = reading_mark
        return {
            "op": "NOTE_ASSOCIATION",
            "status": "applied",
            "target_id": target_id,
            "target_type": "arrangement",
            "subject_association": copy.deepcopy(association),
            "reading_mark": copy.deepcopy(reading_mark),
        }

    obj = _ensure_object(state, root, target_id, actor_side="subject", now_text=now_text)
    object_id = str(obj["object_id"])
    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict) or not _is_active(live):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown target: {target_id}")
    live["subject_association"] = association
    if reading_mark is not None:
        # ``understood`` is firmly False: flipping back two pages is not proof
        # that the chapter landed, and nothing here may upgrade comprehension.
        live["reading_mark"] = reading_mark
    return {
        "op": "NOTE_ASSOCIATION",
        "status": "applied",
        "target_id": object_id,
        "target_type": "object",
        "subject_association": copy.deepcopy(association),
        "reading_mark": copy.deepcopy(live.get("reading_mark")),
    }


def _apply_introduce_item(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    now_dt: datetime,
    default_visibility: Optional[str],
) -> dict[str, Any]:
    label = _text(step.get("label_cn"), min_len=1, max_len=60, field="label_cn")
    item_kind = _choice(step.get("item_kind"), ITEM_KINDS, field="item_kind")
    target_id = _validate_target_exists(state, step.get("target_parent_id"), root=root)
    target_zone = _zone(state, target_id)

    visibility = default_visibility or "shared"
    if step.get("visibility") is not None:
        visibility = _choice(step.get("visibility"), VISIBILITIES, field="visibility")
    if target_zone is not None and _is_private_zone(target_zone):
        visibility = "private"
    elif _is_private_object(state, _object(state, target_id) or {}):
        visibility = "private"

    capacity = _int_field(step.get("capacity_slots"), field="capacity_slots", default=0, minimum=0, maximum=12)
    paper_like = _strict_bool(step.get("paper_like"), field="paper_like") if step.get("paper_like") is not None else False
    raw_inscription = step.get("inscribed_text_cn")
    inscription = _optional_text(raw_inscription, max_len=120, field="inscribed_text_cn")
    if raw_inscription is not None and inscription is None:
        raise V08MatterError("INVALID_FIELDS", "inscribed_text_cn must not be blank")
    if inscription is not None and not (paper_like or item_kind in ("paper", "keepsake")):
        raise V08MatterError(
            "INVALID_FIELDS",
            "inscribed_text_cn is only allowed on paper or keepsake items",
        )
    shelf_life = _shelf_life(step.get("shelf_life_hours"))
    portions = step.get("portions")
    portions_total = None
    portions_left = None
    if portions is not None:
        portions_total = _int_field(portions, field="portions", default=_DEFAULT_PORTIONS, minimum=1, maximum=99)
        portions_left = portions_total
    material_units = _int_field(
        step.get("material_units"), field="material_units", default=1, minimum=1, maximum=24
    )
    assembly_role = _choice(
        step.get("assembly_role"), ASSEMBLY_ROLES, field="assembly_role", default="none"
    )

    object_id = _optional_text(step.get("object_id"), max_len=64, field="object_id")
    if object_id is None:
        object_id = _new_object_id("intro", label, item_kind, now_text, state.get("revision"), len(state.get("matter_objects") or {}))
    if _object(state, object_id) is not None:
        raise V08MatterError("INVALID_FIELDS", f"object already exists: {object_id}")

    obj = _object_row(
        object_id,
        label,
        item_kind=item_kind,
        parent_id=target_id,
        capacity_slots=capacity,
        paper_like=paper_like,
        visibility=visibility,
        material_units=material_units,
        assembly_role=assembly_role,
    )
    obj["inscribed_text_cn"] = inscription
    obj["thermal"] = _choice(step.get("thermal"), THERMAL_STATES, field="thermal", default="room")
    obj["cook_state"] = _choice(step.get("cook_state"), COOK_STATES, field="cook_state", default="none")
    if step.get("requires_cooking") is not None:
        obj["requires_cooking"] = _strict_bool(step.get("requires_cooking"), field="requires_cooking")
    obj["portions_total"] = portions_total
    obj["portions_left"] = portions_left
    obj["last_thermal_at"] = now_text
    obj["last_moisture_at"] = now_text
    obj["state_clock_iso"] = now_text
    if shelf_life is not None:
        obj["shelf_life_hours"] = shelf_life
        obj["freshness"] = "fresh"
        obj["freshness_anchor_at"] = now_text
        obj["freshness_settled_at"] = now_text
        obj["effective_exposure_hours"] = 0.0
    elif item_kind in ("ingredient", "prepared_food", "drink"):
        obj["freshness"] = "fresh" if item_kind != "drink" else "non_perishable"
    else:
        obj["freshness"] = "non_perishable"

    if step.get("placement_relation") is not None:
        relation = _choice(step.get("placement_relation"), PLACEMENT_RELATIONS, field="placement_relation")
    else:
        relation = _default_placement_relation(state, target_id)
    anchor_page: Optional[int] = None
    if step.get("anchor_page") is not None:
        if relation != "tucked_in":
            raise V08MatterError("INVALID_FIELDS", "anchor_page is only meaningful for tucked_in")
        anchor_page = _int_field(step["anchor_page"], field="anchor_page", minimum=1, maximum=2000)
    posture = (
        _choice(step.get("posture_tag"), POSTURE_TAGS, field="posture_tag")
        if step.get("posture_tag") is not None
        else ""
    )
    _validate_placement(state, obj, target_id, carrier_relation=relation)
    _validate_carrier_relation(state, target_id, relation, anchor_page)
    obj["placement_relation"] = relation
    obj["posture_tag"] = posture
    obj["anchor_page"] = anchor_page
    scalding = _scalding_minutes(step)
    if scalding > 0:
        if item_kind not in ("drink", "prepared_food", "ingredient"):
            raise V08MatterError(
                "INVALID_FIELDS", "scalding_minutes only applies to food or drink"
            )
        _mark_scalding(obj, scalding, now_dt)
    state["matter_objects"][object_id] = obj
    return {
        "op": "INTRODUCE_ITEM",
        "status": "applied",
        "object_id": object_id,
        "label_cn": label,
        "target_parent_id": target_id,
        "scalding_until_iso": obj.get("scalding_until_iso"),
        "scalding_wait_minutes": int(obj.get("scalding_wait_minutes") or 0),
    }


def _corner_place_label(
    state: Mapping[str, Any], root: Path | str, zone_id: str
) -> Optional[str]:
    """The human name of a real place a nickname may attach to, or ``None``.

    A nickname is vocabulary, not physics: this never materialises a zone.  It
    accepts a live matter zone, a discovered custom-world zone, or one of the
    bounded shipped ambient places (a windowsill, the porch eaves).
    """

    zone = _zone(state, zone_id)
    if zone is not None:
        return str(zone.get("label_cn") or zone_id)
    zones, _ = _discover_custom_world(root)
    for zone_row in zones:
        if str(zone_row.get("zone_id") or "") == zone_id:
            return str(zone_row.get("label_cn") or zone_id)
    return KNOWN_CORNER_PLACES.get(zone_id)


def _apply_name_personal_corner(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Name (or clear) the subject's own corner vocabulary for one real place."""

    zone_id = _optional_text(step.get("zone_id"), max_len=64, field="zone_id")
    if zone_id is None:
        raise V08MatterError("INVALID_FIELDS", "zone_id is required")
    place_label = _corner_place_label(state, root, zone_id)
    if place_label is None:
        raise V08MatterError("INVALID_TARGET", f"unknown corner: {zone_id}")

    nicknames = state.get("zone_nicknames")
    if not isinstance(nicknames, dict):
        nicknames = {}
        state["zone_nicknames"] = nicknames

    clear = (
        _strict_bool(step.get("clear"), field="clear")
        if step.get("clear") is not None
        else False
    )
    if clear:
        existed = nicknames.pop(zone_id, None) is not None
        return {
            "op": "NAME_PERSONAL_CORNER",
            "status": "applied",
            "zone_id": zone_id,
            "zone_label_cn": place_label,
            "cleared": True,
            "had_nickname": existed,
        }

    nickname = _text(
        step.get("nickname_cn"), min_len=1, max_len=MAX_NICKNAME_CHARS, field="nickname_cn"
    )
    shared = (
        _strict_bool(step.get("shared_with_partner"), field="shared_with_partner")
        if step.get("shared_with_partner") is not None
        else False
    )
    previous = nicknames.get(zone_id)
    nicknames[zone_id] = {
        "zone_id": zone_id,
        "nickname_cn": nickname,
        "shared_with_partner": shared,
        "named_by": "subject",
        "created_at": (
            str(previous.get("created_at"))
            if isinstance(previous, Mapping) and previous.get("created_at")
            else now_text
        ),
        "updated_at": now_text,
    }
    return {
        "op": "NAME_PERSONAL_CORNER",
        "status": "applied",
        "zone_id": zone_id,
        "zone_label_cn": place_label,
        "nickname_cn": nickname,
        "shared_with_partner": shared,
        "cleared": False,
    }


def _apply_pack_for_outing(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    actor_side: str = "subject",
) -> dict[str, Any]:
    """Pack reachable items into a portable carrier; partial reality is kept.

    One unreachable, missing or over-capacity item never rolls back the items
    that already went in.  A subject reach-miss is recorded in her belief only;
    the real spot a partner hid something is never named in the receipt.
    """

    carrier_id = _optional_text(step.get("carrier_id"), max_len=64, field="carrier_id") or "canvas_bag"
    raw_zone = _optional_text(step.get("zone_id"), max_len=64, field="zone_id")

    raw_items = step.get("item_ids")
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        raise V08MatterError("INVALID_FIELDS", "item_ids must be a non-empty list of object ids")
    item_ids: list[str] = []
    for raw in raw_items:
        item_id = str(raw or "").strip()
        if not item_id:
            raise V08MatterError("INVALID_FIELDS", "item_ids must not contain blanks")
        if item_id not in item_ids:
            item_ids.append(item_id)
    if carrier_id in item_ids:
        raise V08MatterError("INVALID_CONTAINMENT", "a carrier cannot be packed into itself")

    auto_create = True
    if step.get("auto_create_carrier") is not None:
        auto_create = _strict_bool(step.get("auto_create_carrier"), field="auto_create_carrier")

    expected_zones: dict[str, str] = {}
    raw_expected = step.get("expected_item_zones")
    if raw_expected is not None:
        if not isinstance(raw_expected, Mapping):
            raise V08MatterError("INVALID_FIELDS", "expected_item_zones must be an object")
        for key, value in raw_expected.items():
            if not isinstance(key, str) or not key.strip():
                raise V08MatterError("INVALID_FIELDS", "expected_item_zones keys must be object ids")
            expected_zones[key.strip()] = str(value or "").strip()

    carrier = _object(state, carrier_id)
    carrier_created = False
    if carrier is None:
        if not auto_create:
            raise V08MatterError("OBJECT_NOT_FOUND", f"unknown carrier: {carrier_id}")
        preset = OUTING_CARRIER_DEFAULTS.get(carrier_id)
        if preset is None or actor_side != "subject":
            raise V08MatterError("OBJECT_NOT_FOUND", f"unknown carrier: {carrier_id}")
        zone_id = _ensure_carrier_zone(state, raw_zone or preset.get("zone_id") or "entry_door")
        if zone_id == carrier_id:
            zone_id = _ensure_carrier_zone(state, "entry_door")
        row = _carrier_object_row(carrier_id, preset, parent_id=zone_id)
        state.setdefault("matter_objects", {})[carrier_id] = row
        _anchor_object_clocks_if_needed(state, row, now_text=now_text)
        carrier = dict(row)
        carrier_created = True
    else:
        if actor_side == "partner" and _is_private_object(state, carrier):
            raise V08MatterError("OBJECT_NOT_FOUND", "no such carrier is visible")
        if bool(carrier.get("sealed")) and not bool(carrier.get("unsealed_for_subject")):
            raise V08MatterError("INVALID_CONTAINMENT", "先把这只容器打开再往里装")
        if not _object_is_portable_carrier(carrier):
            raise V08MatterError(
                "INVALID_CONTAINMENT",
                "这件东西不能随身带走，换一个能拎的容器",
            )

    capacity = _target_capacity(state, carrier_id)
    if capacity is None:
        capacity = 0

    objects_map = state.get("matter_objects") or {}
    unpacked: list[dict[str, Any]] = []
    packed: list[str] = []

    for item_id in item_ids:
        live = state.get("matter_objects", {}).get(item_id)
        if not isinstance(live, dict) or not _is_active(live):
            unpacked.append({
                "object_id": item_id,
                "reason_code": "OBJECT_NOT_FOUND",
                "reason_cn": "这件东西现在拿不到，没装进去",
            })
            continue
        if item_id in _ancestor_object_ids(objects_map, carrier_id):
            unpacked.append({
                "object_id": item_id,
                "reason_code": "INVALID_CONTAINMENT",
                "reason_cn": "装不进去，会绕成一层套一层",
            })
            continue

        believed = live.get("subject_last_seen_parent")
        if actor_side == "subject":
            if bool(live.get("not_at_believed_spot")) or str(believed) == SUBJECT_MISSING_SPOT:
                unpacked.append({
                    "object_id": item_id,
                    "reason_code": "OBJECT_NOT_AT_EXPECTED_SPOT",
                    "reason_cn": "不在原来以为的位置，伸手没摸到",
                })
                continue
            if bool(live.get("impression_stale")) and believed:
                _record_subject_reach_miss(state, item_id, believed)
                unpacked.append({
                    "object_id": item_id,
                    "reason_code": "OBJECT_NOT_AT_EXPECTED_SPOT",
                    "reason_cn": "不在原来以为的位置，伸手没摸到",
                })
                continue
            if _object_is_hidden_from_subject(objects_map, item_id):
                if believed:
                    _record_subject_reach_miss(state, item_id, believed)
                unpacked.append({
                    "object_id": item_id,
                    "reason_code": "OBJECT_NOT_AT_EXPECTED_SPOT",
                    "reason_cn": "隔着一层还没打开，摸不到",
                })
                continue
            expected_zone = expected_zones.get(item_id)
            if expected_zone:
                probe = dict(live)
                probe["parent_id"] = believed or live.get("parent_id")
                believed_zone = _zone_id_for(state, probe)
                if believed_zone and believed_zone != expected_zone:
                    _record_subject_reach_miss(state, item_id, believed)
                    unpacked.append({
                        "object_id": item_id,
                        "reason_code": "OBJECT_NOT_AT_EXPECTED_SPOT",
                        "reason_cn": "不在说好的位置，伸手没摸到",
                    })
                    continue
        else:
            expected_zone = expected_zones.get(item_id)
            if expected_zone and _zone_id_for(state, live) != expected_zone:
                unpacked.append({
                    "object_id": item_id,
                    "reason_code": "INVALID_TARGET",
                    "reason_cn": "不在约定的位置，没有装进去",
                })
                continue

        if str(live.get("parent_id") or "") != carrier_id:
            try:
                _validate_capacity(state, carrier_id, moving_id=item_id)
            except V08MatterError as error:
                unpacked.append({
                    "object_id": item_id,
                    "reason_code": error.reason_code,
                    "reason_cn": "包里已经装满了，这件先留在外面",
                })
                continue

        old_parent = str(live.get("parent_id") or "")
        old_ambient = _ambient_for(state, live)
        live["parent_id"] = carrier_id
        live["held_by"] = None
        # Extrinsic carrier-bound postures (drape/tuck/lean) do not survive the
        # pack; intrinsic traces (folded / neat) are kept.
        _clear_carrier_binding(live, relation="inside")
        if actor_side == "subject":
            _refresh_subject_belief(live)
        else:
            if live.get("subject_last_seen_parent") and not live.get("impression_stale"):
                live["subject_last_seen_snapshot"] = _capture_subject_snapshot(live)
            live["impression_stale"] = True
        if old_parent and old_parent != carrier_id:
            _break_arrangements_for_member(
                state, item_id, now_text=now_text, reason="MEMBER_MOVED", actor_side=actor_side
            )
        _anchor_object_clocks_if_needed(state, live, now_text=now_text, old_ambient=old_ambient)
        packed.append(item_id)

    if packed:
        carrier_live = state.get("matter_objects", {}).get(carrier_id)
        if isinstance(carrier_live, dict):
            _note_familiar_contact(state, carrier_live, zone_id=_zone_id_for(state, carrier_live))

    if not unpacked:
        status = "packed"
    elif packed:
        status = "partially_packed"
    else:
        status = "nothing_packed"

    location = _derive_effective_location_readonly(
        carrier_id,
        state.get("matter_objects") or {},
        zones_by_id=state.get("zones") or {},
        perspective=actor_side,
        journey_doc=_read_neighborhood_journey_zero_write(root),
    )
    return {
        "op": "PACK_FOR_OUTING",
        "status": status,
        "carrier_id": carrier_id,
        "carrier_created": carrier_created,
        "packed_item_ids": packed,
        "unpacked_items": unpacked,
        "carrier_effective_zone_id": location["effective_zone_id"],
        "carrier_effective_place_id": location["effective_place_id"],
        "carrier_chain": location["carrier_chain"],
        "carried_on_journey": location["carried_on_journey"],
    }


def _transform_is_heated(state: Mapping[str, Any], medium_id: Optional[str], target_vessel_id: Optional[str]) -> bool:
    if medium_id:
        medium_zone = _zone(state, medium_id)
        if medium_zone is not None and medium_zone.get("ambient_zone") == "heating":
            return True
        medium_obj = _object(state, medium_id)
        if medium_obj is not None and _ambient_for(state, medium_obj) == "heating":
            return True
    if target_vessel_id:
        vessel = _object(state, target_vessel_id)
        if vessel is not None and _ambient_for(state, vessel) == "heating":
            return True
    return False


def _apply_transform_create(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    now_dt: datetime,
) -> dict[str, Any]:
    raw_sources = step.get("source_object_ids")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        raise V08MatterError("INVALID_FIELDS", "source_object_ids must be a list")
    if not (1 <= len(raw_sources) <= 4):
        raise V08MatterError("INVALID_FIELDS", "source_object_ids must have 1..4 entries")

    output_label = _text(step.get("output_label_cn"), min_len=1, max_len=60, field="output_label_cn")
    output_kind = _choice(
        step.get("output_kind"), ("prepared_food", "drink", "everyday"),
        field="output_kind", default="prepared_food",
    )
    output_portions = _int_field(
        step.get("output_portions"), field="output_portions",
        default=_DEFAULT_OUTPUT_PORTIONS, minimum=1, maximum=12,
    )
    # Validate the optional scalding cooldown before consuming any source so a
    # malformed request can never leave half-mutated state behind.
    scalding = _scalding_minutes(step)
    if scalding > 0 and output_kind not in ("drink", "prepared_food"):
        raise V08MatterError(
            "INVALID_FIELDS", "scalding_minutes only applies to food or drink"
        )
    target_vessel_id = _optional_text(step.get("target_vessel_id"), max_len=64, field="target_vessel_id")
    medium_id = _optional_text(step.get("medium_object_id"), max_len=64, field="medium_object_id")
    if medium_id:
        # A custom stove is a discovered zone until this first real use.
        _materialize_custom_zone(state, root, medium_id)

    # Resolve every source first: a missing ingredient is a hard refusal, never
    # an invented object.
    sources: list[dict[str, Any]] = []
    for raw in raw_sources:
        sources.append(_ensure_object(state, root, raw, actor_side="subject", now_text=now_text))
    for source in sources:
        if str(source.get("freshness") or "") == "spoiled":
            raise V08MatterError("ITEM_SPOILED", f"a source has spoiled: {_public_label(source)}")

    # A court vessel must be clean and empty.
    if target_vessel_id:
        vessel = _ensure_object(state, root, target_vessel_id, actor_side="subject", now_text=now_text)
        if str(vessel.get("item_kind")) != "vessel" and int(vessel.get("capacity_slots") or 0) <= 0:
            raise V08MatterError("INVALID_TARGET", "target_vessel_id is not a vessel")
        if str(vessel.get("cleanliness") or "clean") != "clean":
            raise V08MatterError("VESSEL_OCCUPIED_OR_SOILED", "the vessel is not clean")
        if _children_of(state, vessel.get("object_id")):
            raise V08MatterError("VESSEL_OCCUPIED_OR_SOILED", "the vessel already holds something")

    heated = _transform_is_heated(state, medium_id, target_vessel_id)
    shelf_life = _shelf_life(step.get("shelf_life_hours"))
    if heated and shelf_life is None and output_kind != "everyday":
        shelf_life = _DEFAULT_COOKED_SHELF_LIFE_HOURS

    # Consume one portion of every source.
    consumed_sources: list[str] = []
    for source in sources:
        source_id = str(source.get("object_id"))
        live = state["matter_objects"].get(source_id)
        if not isinstance(live, dict):
            raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {source_id}")
        _note_familiar_contact(state, live, zone_id=_zone_id_for(state, live))
        portions_left = live.get("portions_left")
        if not isinstance(portions_left, int):
            portions_left = 1
        portions_left -= 1
        if portions_left <= 0:
            live["portions_left"] = 0
            live["consumed"] = True
            live["parent_id"] = None
            consumed_sources.append(source_id)
        else:
            live["portions_left"] = portions_left

    # Where the product lands.
    output_parent_id = target_vessel_id
    if output_parent_id is None:
        for source in sources:
            parent_id = source.get("parent_id")
            if _zone(state, parent_id) is not None:
                output_parent_id = str(parent_id)
                break
    if output_parent_id is None and medium_id:
        medium_zone = _zone(state, medium_id)
        if medium_zone is not None:
            output_parent_id = medium_id
        else:
            medium_obj = _object(state, medium_id)
            if medium_obj is not None:
                parent_id = medium_obj.get("parent_id")
                if _zone(state, parent_id) is not None:
                    output_parent_id = str(parent_id)
    if output_parent_id is None:
        output_parent_id = "kitchen_counter"

    output_id = _new_object_id("made", output_label, now_text, state.get("revision"), output_parent_id)
    output = _object_row(
        output_id,
        output_label,
        item_kind=output_kind if output_kind in ITEM_KINDS else "prepared_food",
        parent_id=output_parent_id,
        capacity_slots=0,
    )
    output["portions_total"] = output_portions
    output["portions_left"] = output_portions
    output["last_thermal_at"] = now_text
    output["last_moisture_at"] = now_text
    output["state_clock_iso"] = now_text
    if heated and output_kind != "everyday":
        output["cook_state"] = "cooked"
        output["thermal"] = "hot"
        output["freshness"] = "fresh"
    else:
        raw_source = any(str(source.get("cook_state")) == "raw" for source in sources)
        output["cook_state"] = "raw" if raw_source and output_kind != "everyday" else "none"
        output["thermal"] = "room"
        output["freshness"] = "fresh" if output_kind != "everyday" else "non_perishable"
    if shelf_life is not None:
        output["shelf_life_hours"] = shelf_life
        output["freshness_anchor_at"] = now_text
        output["freshness_settled_at"] = now_text
        output["effective_exposure_hours"] = 0.0
    if _zone(state, output_parent_id) is not None and _is_private_zone(_zone(state, output_parent_id) or {}):
        output["visibility"] = "private"
    _mark_scalding(output, scalding, now_dt)

    _validate_capacity(state, output_parent_id)
    output["placement_relation"] = _default_placement_relation(state, output_parent_id)
    state["matter_objects"][output_id] = output
    _note_familiar_contact(state, output, zone_id=_zone_id_for(state, output))
    return {
        "op": "TRANSFORM_CREATE",
        "status": "applied",
        "source_object_ids": [str(source.get("object_id")) for source in sources],
        "consumed_source_ids": consumed_sources,
        "output_object_id": output_id,
        "output_label_cn": output_label,
        "output_kind": output["item_kind"],
        "cook_state": output["cook_state"],
        "thermal": output["thermal"],
        "freshness": output["freshness"],
        "portions_left": output["portions_left"],
        "parent_id": output_parent_id,
        "heated": heated,
        "scalding_until_iso": output.get("scalding_until_iso"),
        "scalding_wait_minutes": int(output.get("scalding_wait_minutes") or 0),
    }


def _resolve_edible(state: Mapping[str, Any], obj: Mapping[str, Any]) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Return (edible object, host vessel or None).

    A vessel is never itself eaten: it delegates to an edible child.  An empty
    vessel, or one holding tools/paper, is a refusal.
    """

    if str(obj.get("item_kind")) == "vessel":
        children = [_object(state, cid) for cid in _children_of(state, obj.get("object_id"))]
        edible = [
            child for child in children
            if child is not None and str(child.get("item_kind")) in ("prepared_food", "drink", "ingredient")
        ]
        if not edible:
            raise V08MatterError(
                "VESSEL_OCCUPIED_OR_SOILED",
                "the vessel holds nothing edible (or holds something else)",
            )
        child = edible[0]
        return child, obj
    return obj, None


def _apply_consume_portion(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    now_dt: datetime,
) -> dict[str, Any]:
    obj = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    edible, host = _resolve_edible(state, obj)
    if str(edible.get("item_kind")) not in ("prepared_food", "drink", "ingredient"):
        raise V08MatterError("INVALID_TARGET", "that object is not edible")
    if str(edible.get("freshness") or "") == "spoiled":
        raise V08MatterError("ITEM_SPOILED", "the food has spoiled and must not be eaten")
    if str(edible.get("cook_state") or "none") == "raw" and (
        str(edible.get("item_kind")) == "prepared_food" or bool(edible.get("requires_cooking"))
    ):
        raise V08MatterError("ITEM_RAW_NEEDS_HEAT", "the food is raw and still needs heat")
    # A just-poured drink is really too hot right now; the deadline is part of
    # the object's physics, so a clock advance releases it with no extra state.
    _require_not_scalding(edible, host, now_dt=now_dt)

    portions = _int_field(step.get("portions"), field="portions", default=_DEFAULT_PORTIONS, minimum=1, maximum=12)
    edible_id = str(edible.get("object_id"))
    live = state["matter_objects"].get(edible_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {edible_id}")
    _note_familiar_contact(state, live, zone_id=_zone_id_for(state, live))
    portions_left = live.get("portions_left")
    if not isinstance(portions_left, int):
        portions_left = 1
    if portions_left < portions:
        raise V08MatterError("INSUFFICIENT_PORTIONS", "not enough portions left")
    portions_left -= portions
    receipt = {
        "op": "CONSUME_PORTION",
        "status": "applied",
        "object_id": edible_id,
        "label_cn": edible.get("label_cn"),
        "portions_consumed": portions,
        "portions_left": max(0, portions_left),
        "counted_as_intake": True,
    }
    if portions_left <= 0:
        live["portions_left"] = 0
        live["consumed"] = True
        live["parent_id"] = None
        if host is not None:
            host_live = state["matter_objects"].get(str(host.get("object_id")))
            if isinstance(host_live, dict):
                host_live["cleanliness"] = "used"
                host_live["state_clock_iso"] = now_text
        receipt["consumed_fully"] = True
        receipt["host_vessel_marked_used"] = bool(host is not None)
    else:
        live["portions_left"] = portions_left
        receipt["consumed_fully"] = False

    hints = _lived_need_relief_hints(root, str(edible.get("item_kind")))
    if hints:
        receipt["lived_need_relief_hints"] = hints
    return receipt


def _apply_clean(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    target_id = str(step.get("target_id") or "").strip()
    if not target_id:
        raise V08MatterError("INVALID_FIELDS", "target_id is required")
    helper_id = _optional_text(step.get("helper_object_id"), max_len=64, field="helper_object_id")
    if helper_id:
        _materialize_custom_zone(state, root, helper_id)

    zone = _zone(state, target_id)
    if zone is None and _object(state, target_id) is None:
        zone = _materialize_custom_zone(state, root, target_id)
    if zone is not None:
        live_zone = state["zones"].get(target_id)
        if not isinstance(live_zone, dict):
            raise V08MatterError("INVALID_TARGET", f"unknown target: {target_id}")
        live_zone["surface_cleanliness"] = "clean"
        return {
            "op": "CLEAN_SURFACE_OR_VESSEL",
            "status": "applied",
            "target_id": target_id,
            "target_kind": "zone",
            "surface_cleanliness": "clean",
        }

    obj = _object(state, target_id)
    if obj is None:
        raise V08MatterError("INVALID_TARGET", f"unknown target: {target_id}")
    if _children_of(state, target_id):
        raise V08MatterError("CONTAINER_NOT_EMPTY", "empty the container before washing it")

    is_vessel = str(obj.get("item_kind")) == "vessel" or int(obj.get("capacity_slots") or 0) > 0
    if is_vessel:
        at_sink = (
            str(obj.get("parent_id") or "") == "kitchen_sink"
            or helper_id == "kitchen_sink"
            or _zone_has_water(state, obj.get("parent_id"))
            or _zone_has_water(state, helper_id)
        )
        if not at_sink:
            raise V08MatterError(
                "INVALID_TARGET",
                "a vessel must be taken to the kitchen_sink (or washed with helper_object_id=kitchen_sink)",
            )
    live = state["matter_objects"].get(target_id)
    if not isinstance(live, dict):
        raise V08MatterError("INVALID_TARGET", f"unknown target: {target_id}")
    live["cleanliness"] = "clean"
    live["moisture"] = "wet"
    live["last_moisture_at"] = now_text
    live["state_clock_iso"] = now_text
    return {
        "op": "CLEAN_SURFACE_OR_VESSEL",
        "status": "applied",
        "target_id": target_id,
        "target_kind": "object",
        "cleanliness": "clean",
        "moisture": "wet",
    }


def _apply_discard(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    obj = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    object_id = str(obj.get("object_id"))
    if _children_of(state, object_id):
        raise V08MatterError("CONTAINER_NOT_EMPTY", "empty the container before discarding it")
    if _assembled_parts_of(state, object_id):
        raise V08MatterError("CONTAINER_NOT_EMPTY", "detach the fitted parts before discarding the host")
    reason = _optional_text(step.get("reason_cn"), max_len=120, field="reason_cn") or "不再需要"
    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    live["discarded"] = True
    live["consumed"] = False
    live["parent_id"] = None
    record = {
        "object_id": object_id,
        "label_cn": obj.get("label_cn"),
        "reason_cn": reason,
        "counted_as_intake": False,
        "at": now_text,
    }
    discards = state.get("discards")
    if not isinstance(discards, list):
        discards = []
    discards.append(record)
    state["discards"] = discards
    return {
        "op": "DISCARD_ITEM",
        "status": "applied",
        "object_id": object_id,
        "counted_as_intake": False,
        "reason_cn": reason,
    }


def _apply_observe(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    obj = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    object_id = str(obj.get("object_id"))
    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    previous_seen = live.get("subject_last_seen_parent")
    observed_parent = live.get("parent_id")
    moved = str(previous_seen) != str(observed_parent)
    _refresh_subject_belief(live)
    live["last_observed_at"] = now_text
    _note_familiar_contact(state, live, zone_id=_zone_id_for(state, live))
    return {
        "op": "OBSERVE_OBJECT",
        "status": "applied",
        "object_id": object_id,
        "moved_since_last_seen": moved,
        "previous_seen_parent": previous_seen,
        "observed_parent": observed_parent,
    }


def _apply_split_material(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Split integer material units off a source without inventing any unit."""

    source = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    source_id = str(source.get("object_id"))
    if not _is_active(source):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {source_id}")
    live = state["matter_objects"].get(source_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {source_id}")

    source_units = int(live.get("material_units") or 1)
    take_units = _int_field(step.get("take_units"), field="take_units", minimum=1, maximum=24)
    if source_units < 2:
        raise V08MatterError("INVALID_TARGET", "the source has no spare material to split")
    if take_units >= source_units:
        raise V08MatterError(
            "INVALID_FIELDS",
            "take_units must be smaller than the source's remaining material",
        )

    output_label = _text(step.get("output_label_cn"), min_len=1, max_len=60, field="output_label_cn")
    raw_target = step.get("target_parent_id")
    target_id = _validate_target_exists(
        state,
        raw_target if raw_target is not None else live.get("parent_id"),
        root=root,
    )
    if step.get("item_kind") is not None:
        item_kind = _choice(step.get("item_kind"), ITEM_KINDS, field="item_kind")
    else:
        item_kind = str(live.get("item_kind") or "everyday")
        if item_kind not in ITEM_KINDS:
            item_kind = "everyday"
    if step.get("paper_like") is not None:
        paper_like = _strict_bool(step.get("paper_like"), field="paper_like")
    else:
        paper_like = bool(live.get("paper_like")) or item_kind == "paper"
    assembly_role = _choice(
        step.get("assembly_role"), ASSEMBLY_ROLES, field="assembly_role", default="none"
    )

    output_id = _optional_text(step.get("output_object_id"), max_len=64, field="output_object_id")
    if output_id is None:
        output_id = _new_object_id(
            "split", source_id, now_text, state.get("revision"), take_units, output_label
        )
    if _object(state, output_id) is not None:
        raise V08MatterError("INVALID_FIELDS", f"object already exists: {output_id}")

    output = _object_row(
        output_id,
        output_label,
        item_kind=item_kind,
        parent_id=target_id,
        paper_like=paper_like,
        material_units=take_units,
        assembly_role=assembly_role,
    )
    if _is_private_object(state, live) or _is_private_zone(_zone(state, target_id) or {}):
        output["visibility"] = "private"

    _validate_placement(state, output, target_id)
    output["placement_relation"] = _default_placement_relation(state, target_id)
    _note_familiar_contact(state, live, zone_id=_zone_id_for(state, live))
    live["material_units"] = source_units - take_units
    state["matter_objects"][output_id] = output
    _note_familiar_contact(state, output, zone_id=_zone_id_for(state, output))
    return {
        "op": "SPLIT_MATERIAL",
        "status": "applied",
        "source_object_id": source_id,
        "source_units_before": source_units,
        "source_units_after": source_units - take_units,
        "output_object_id": output_id,
        "output_material_units": take_units,
        "parent_id": target_id,
        "label_cn": output_label,
        "conserved_total_units": source_units,
    }


def _apply_assemble_or_detach_part(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Reversibly fit a part into a host, or honestly record that it did not fit."""

    mode = _choice(step.get("mode"), ("assemble", "detach"), field="mode")
    host = _ensure_object(state, root, step.get("host_object_id"), actor_side="subject", now_text=now_text)
    part = _ensure_object(state, root, step.get("part_object_id"), actor_side="subject", now_text=now_text)
    host_id = str(host.get("object_id"))
    part_id = str(part.get("object_id"))
    if host_id == part_id:
        raise V08MatterError("INVALID_TARGET", "an object cannot assemble into itself")
    if not _is_active(host) or not _is_active(part):
        raise V08MatterError("OBJECT_NOT_FOUND", "both objects must be active to assemble")
    if _is_private_object(state, host) != _is_private_object(state, part):
        # A private part inside a shared host (or vice versa) would leak across
        # the privacy boundary on the partner door.
        raise V08MatterError("INVALID_TARGET", "private and shared matter do not assemble together")

    host_live = state["matter_objects"].get(host_id)
    part_live = state["matter_objects"].get(part_id)
    if not isinstance(host_live, dict) or not isinstance(part_live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", "unknown object")

    def _move_part_beside_host() -> None:
        target_parent = host_live.get("parent_id")
        if part_live.get("parent_id") != target_parent and (
            _zone(state, target_parent) is not None or _object(state, target_parent) is not None
        ):
            _validate_capacity(state, str(target_parent), moving_id=part_id)
        part_live["parent_id"] = target_parent

    if mode == "assemble":
        if step.get("part_fits") is not None:
            part_fits = _strict_bool(step.get("part_fits"), field="part_fits")
        else:
            part_fits = True
        current_host = str(part_live.get("assembled_into_id") or "")
        if current_host and current_host != host_id and str(part_live.get("fit_status") or "") == "fitted":
            raise V08MatterError(
                "INVALID_TARGET", "the part is already fitted into another host; detach it first"
            )

        _move_part_beside_host()
        if not part_fits:
            part_live["assembled_into_id"] = None
            part_live["fit_status"] = "oversized_unfitted"
            part_live["wear_state"] = part_live.get("wear_state") or "intact"
            part_live["subject_last_seen_parent"] = part_live.get("parent_id")
            return {
                "op": "ASSEMBLE_OR_DETACH_PART",
                "status": "applied",
                "mode": "assemble",
                "host_object_id": host_id,
                "part_object_id": part_id,
                "fitted": False,
                "fit_status": "oversized_unfitted",
                "parent_id": part_live.get("parent_id"),
            }
        part_live["assembled_into_id"] = host_id
        part_live["fit_status"] = "fitted"
        part_live["subject_last_seen_parent"] = part_live.get("parent_id")
        return {
            "op": "ASSEMBLE_OR_DETACH_PART",
            "status": "applied",
            "mode": "assemble",
            "host_object_id": host_id,
            "part_object_id": part_id,
            "fitted": True,
            "fit_status": "fitted",
            "parent_id": part_live.get("parent_id"),
        }

    if str(part_live.get("assembled_into_id") or "") != host_id:
        raise V08MatterError("INVALID_TARGET", "that part is not fitted into this host")
    part_live["assembled_into_id"] = None
    part_live["fit_status"] = "none"
    return {
        "op": "ASSEMBLE_OR_DETACH_PART",
        "status": "applied",
        "mode": "detach",
        "host_object_id": host_id,
        "part_object_id": part_id,
        "fitted": False,
        "fit_status": "none",
        "parent_id": part_live.get("parent_id"),
    }


def _apply_transcribe_to_paper(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Freeze one artifact snapshot onto real paper; the source may keep moving."""

    obj = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    object_id = str(obj.get("object_id"))
    if not _is_active(obj):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    if not (bool(obj.get("paper_like")) or str(obj.get("item_kind")) in ("paper", "keepsake")):
        raise V08MatterError("INVALID_TARGET", "only paper or keepsake can hold a transcription")

    artifact_id = _text(step.get("artifact_id"), min_len=1, max_len=80, field="artifact_id")
    version_id = _text(step.get("version_id"), min_len=1, max_len=64, field="version_id")
    excerpt = _text(step.get("snapshot_excerpt_cn"), min_len=1, max_len=120, field="snapshot_excerpt_cn")

    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    live["inscribed_text_cn"] = excerpt
    live["transcribed_source"] = {
        "artifact_id": artifact_id,
        "version_id": version_id,
        "snapshot_excerpt_cn": excerpt,
        "transcribed_at": now_text,
    }
    _note_familiar_contact(state, live, zone_id=_zone_id_for(state, live))
    return {
        "op": "TRANSCRIBE_TO_PAPER",
        "status": "applied",
        "object_id": object_id,
        "artifact_id": artifact_id,
        "version_id": version_id,
        "snapshot_excerpt_cn": excerpt,
        "transcribed_at": now_text,
    }


def _apply_patch_or_repair(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Repair or label matter, honestly consuming one unit of real patch material."""

    obj = _ensure_object(state, root, step.get("object_id"), actor_side="subject", now_text=now_text)
    object_id = str(obj.get("object_id"))
    if not _is_active(obj):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    note = _text(step.get("patch_note_cn"), min_len=1, max_len=100, field="patch_note_cn")
    if step.get("fixes_fit") is not None:
        fixes_fit = _strict_bool(step.get("fixes_fit"), field="fixes_fit")
    else:
        fixes_fit = False

    patch_material_id = _optional_text(
        step.get("patch_material_id"), max_len=64, field="patch_material_id"
    )
    patch_units_before: Optional[int] = None
    patch_units_after: Optional[int] = None
    patch_consumed = False
    if patch_material_id is not None:
        if patch_material_id == object_id:
            raise V08MatterError("INVALID_TARGET", "an object cannot patch itself")
        _ensure_object(state, root, patch_material_id, actor_side="subject", now_text=now_text)
        mat_live = state["matter_objects"].get(patch_material_id)
        if not isinstance(mat_live, dict) or not _is_active(mat_live):
            raise V08MatterError("OBJECT_NOT_FOUND", "the patch material is not available")
        patch_units_before = int(mat_live.get("material_units") or 1)
        if patch_units_before < 1:
            raise V08MatterError("INVALID_TARGET", "the patch material has no units left")
        if patch_units_before > 1:
            mat_live["material_units"] = patch_units_before - 1
            patch_units_after = patch_units_before - 1
        else:
            mat_live["material_units"] = 0
            mat_live["consumed"] = True
            mat_live["parent_id"] = None
            patch_units_after = 0
            patch_consumed = True

    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    live["wear_state"] = "patched"
    live["patch_note_cn"] = note
    _note_familiar_contact(state, live, zone_id=_zone_id_for(state, live))
    reset_fit = False
    if fixes_fit and str(live.get("fit_status") or "none") == "oversized_unfitted":
        live["fit_status"] = "none"
        reset_fit = True
    return {
        "op": "PATCH_OR_REPAIR",
        "status": "applied",
        "object_id": object_id,
        "patch_material_id": patch_material_id,
        "patch_units_before": patch_units_before,
        "patch_units_after": patch_units_after,
        "patch_material_consumed": patch_consumed,
        "wear_state": "patched",
        "patch_note_cn": note,
        "fit_status": live.get("fit_status") or "none",
        "fit_reset": reset_fit,
    }


# ---------------------------------------------------------------------------
# v0.4 Ch.42 cross-ring handoff: shared shelf / bookstore / public board
# ---------------------------------------------------------------------------

_LOAN_SOURCE_RINGS: tuple[str, ...] = ("shared_shelf", "bookstore")
_PUBLIC_BOARDS: tuple[str, ...] = ("public_notice_board", "bookstore_display_board")


def _neighborhood_op_key(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        ("|".join(str(part) for part in parts)).encode("utf-8")
    ).hexdigest()[:16]
    return f"v08_{prefix}_{digest}"


def _v04_public_life(projected: Any) -> Mapping[str, Any]:
    """Read ``public_life`` from either a flattened core or a full v0.4 projection."""

    if not isinstance(projected, Mapping):
        return {}
    direct = projected.get("public_life")
    if isinstance(direct, Mapping):
        return direct
    data = projected.get("data")
    if isinstance(data, Mapping):
        nested = data.get("public_life")
        if isinstance(nested, Mapping):
            return nested
    return {}


def _neighborhood_item_label(projected: Any, source_ring: str, item_id: str) -> str:
    """The item's honest real name, read only from the v0.4 projection."""

    public_life = _v04_public_life(projected)
    if source_ring == "shared_shelf":
        shelf = public_life.get("shared_shelf")
        shelf = shelf if isinstance(shelf, Mapping) else {}
        by_id = shelf.get("items_by_id")
        by_id = by_id if isinstance(by_id, Mapping) else {}
        row = by_id.get(item_id)
        row = row if isinstance(row, Mapping) else {}
        label = row.get("name") or row.get("label")
        return str(label).strip() if isinstance(label, str) and label.strip() else item_id
    bookstore = public_life.get("bookstore")
    bookstore = bookstore if isinstance(bookstore, Mapping) else {}
    by_id = bookstore.get("books_by_id")
    by_id = by_id if isinstance(by_id, Mapping) else {}
    row = by_id.get(item_id)
    row = row if isinstance(row, Mapping) else {}
    title = row.get("title")
    return str(title).strip() if isinstance(title, str) and title.strip() else item_id


def _lender_meta(source_ring: str) -> tuple[str, str, str]:
    if source_ring == "shared_shelf":
        return ("街角共享架", "shared_shelf", "community_shared_shelf")
    return ("街角旧书店", "bookstore_return_box", "bookstore")


def _apply_borrow_neighborhood_item(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Borrow one real item; exactly one home object mirrors the loan (no copy)."""

    source_ring = _choice(step.get("source_ring"), _LOAN_SOURCE_RINGS, field="source_ring")
    item_id = _text(
        step.get("neighborhood_item_id"), min_len=1, max_len=64, field="neighborhood_item_id"
    )
    loan_hours = _int_field(
        step.get("loan_hours"), field="loan_hours", default=48, minimum=1, maximum=336
    )
    loan_days = _int_field(
        step.get("loan_days"), field="loan_days", default=7, minimum=1, maximum=30
    )
    target_id = _validate_target_exists(
        state, step.get("target_parent_id") or "home_desk", root=root
    )
    object_id = _optional_text(step.get("object_id"), max_len=64, field="object_id")
    if object_id is None:
        object_id = f"nb_{source_ring}_{item_id}"

    # Strict zero duplication: the same lender item may be in home custody once.
    for existing_id, existing_obj in (state.get("matter_objects") or {}).items():
        if not isinstance(existing_obj, Mapping) or not _is_active(existing_obj):
            continue
        meta = existing_obj.get("loan_meta")
        if not isinstance(meta, Mapping):
            continue
        if (
            str(meta.get("source_ring") or "") == source_ring
            and str(meta.get("neighborhood_item_id") or "") == item_id
            and str(meta.get("loan_status") or "") == "borrowed"
        ):
            raise V08MatterError(
                "INVALID_TARGET", "this item is already in home custody", status_code=409
            )

    prior = state.get("matter_objects", {}).get(object_id)
    if isinstance(prior, Mapping) and _is_active(prior):
        raise V08MatterError("INVALID_FIELDS", f"object already exists: {object_id}")

    op_key = _neighborhood_op_key("borrow", source_ring, item_id, target_id, now_text)
    if source_ring == "shared_shelf":
        command: dict[str, Any] = {
            "action": "shared_shelf_loan",
            "item_id": item_id,
            "operation": "borrow",
            "loan_hours": loan_hours,
            "operation_key": op_key,
        }
    else:
        command = {
            "action": "bookstore_service",
            "service": "borrow_book",
            "book_id": item_id,
            "loan_days": loan_days,
            "operation_key": op_key,
        }
    outcome = _apply_neighborhood_command(root, command, now_text=now_text)
    result = outcome["result"]
    label = _neighborhood_item_label(outcome["projected"], source_ring, item_id)
    due_at = result.get("due_at")
    lender_label, return_target, lent_by = _lender_meta(source_ring)

    target_zone = _zone(state, target_id)
    target_obj = _object(state, target_id)
    visibility = "shared"
    if target_zone is not None and _is_private_zone(target_zone):
        visibility = "private"
    elif _is_private_object(state, target_obj or {}):
        visibility = "private"

    is_book = source_ring == "bookstore"
    loan_meta = {
        "source_ring": source_ring,
        "neighborhood_item_id": item_id,
        "lender_label_cn": lender_label,
        "return_target_id": return_target,
        "due_at": due_at,
        "loan_status": "borrowed",
        "ownership_preserved": True,
        "shopkeeper_received": False,
        "affection_score_coupled": False,
    }

    objects = state.setdefault("matter_objects", {})
    if isinstance(prior, dict):
        live = prior
        live.update({
            "label_cn": label,
            "item_kind": "paper" if is_book else "tool",
            "paper_like": is_book,
            "capacity_slots": 0,
            "material_units": 1,
            "parent_id": target_id,
            "visibility": visibility,
            "lent_by": lent_by,
            "returned_out": False,
            "consumed": False,
            "discarded": False,
            "delivered_to_board": None,
            "loan_meta": copy.deepcopy(loan_meta),
            "subject_last_seen_parent": target_id,
            "impression_stale": False,
        })
    else:
        live = _object_row(
            object_id,
            label,
            item_kind="paper" if is_book else "tool",
            parent_id=target_id,
            paper_like=is_book,
            visibility=visibility,
            material_units=1,
        )
        live["lent_by"] = lent_by
        live["loan_meta"] = copy.deepcopy(loan_meta)
        live["returned_out"] = False
        objects[object_id] = live
    _anchor_object_clocks_if_needed(state, live, now_text=now_text)
    return {
        "op": "BORROW_NEIGHBORHOOD_ITEM",
        "status": "applied",
        "object_id": object_id,
        "label_cn": label,
        "item_kind": live.get("item_kind"),
        "paper_like": bool(live.get("paper_like")),
        "target_parent_id": target_id,
        "source_ring": source_ring,
        "neighborhood_item_id": item_id,
        "due_at": due_at,
        "loan_meta": copy.deepcopy(loan_meta),
        "affection_score_coupled": False,
    }


def _apply_return_borrowed_item(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Return one borrowed real item; the home object leaves physical custody."""

    object_id = _text(step.get("object_id"), min_len=1, max_len=64, field="object_id")
    obj = _object(state, object_id)
    if obj is None or not _is_active(obj):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    meta = obj.get("loan_meta")
    if not isinstance(meta, Mapping) or str(meta.get("loan_status") or "") != "borrowed":
        raise V08MatterError("NOT_A_LOAN_ITEM", "this object is not a borrowed item", status_code=409)
    source_ring = str(meta.get("source_ring") or "")
    if source_ring not in _LOAN_SOURCE_RINGS:
        raise V08MatterError("NOT_A_LOAN_ITEM", "unknown lender for this borrowed item", status_code=409)
    if _children_of(state, object_id) or _assembled_parts_of(state, object_id):
        raise V08MatterError("CONTAINER_NOT_EMPTY", "empty the borrowed thing before returning it")

    via_return_box = (
        _strict_bool(step.get("via_return_box"), field="via_return_box")
        if step.get("via_return_box") is not None
        else False
    )
    return_location = (
        _choice(step.get("return_location"), ("porch", "bookstore"), field="return_location")
        if step.get("return_location") is not None
        else None
    )
    nb_id = str(meta.get("neighborhood_item_id") or "")

    op_key = _neighborhood_op_key(
        "return", source_ring, nb_id, object_id, via_return_box, return_location, now_text
    )
    if source_ring == "shared_shelf":
        command: dict[str, Any] = {
            "action": "shared_shelf_loan",
            "item_id": nb_id,
            "operation": "return",
            "operation_key": op_key,
        }
    else:
        command = {
            "action": "bookstore_service",
            "service": "return_book",
            "book_id": nb_id,
            "via_return_box": via_return_box,
            "return_location": return_location or "bookstore",
            "operation_key": op_key,
        }
    outcome = _apply_neighborhood_command(root, command, now_text=now_text)
    result = outcome["result"]

    receipt_kind = result.get("return_receipt_kind")
    pending = str(receipt_kind or "") == "return_box_pending"
    shopkeeper_received = bool(result.get("shopkeeper_received", False))
    # Ch.42 §三: a return box or a closed store NEVER fakes the shopkeeper taking it.
    if pending or via_return_box or return_location == "porch":
        shopkeeper_received = False
    loan_status = "return_box_pending" if pending else "returned_received"

    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    updated_meta = copy.deepcopy(dict(meta))
    updated_meta.update({
        "loan_status": loan_status,
        "shopkeeper_received": shopkeeper_received,
        "return_receipt_kind": receipt_kind,
        "returned_at": now_text,
        "returned_via_return_box": bool(via_return_box),
        "return_location": return_location or ("bookstore" if source_ring == "bookstore" else None),
    })
    live["loan_meta"] = updated_meta
    live["returned_out"] = True
    live["parent_id"] = None
    live["subject_last_seen_parent"] = None
    live["impression_stale"] = False
    return {
        "op": "RETURN_BORROWED_ITEM",
        "status": "applied",
        "object_id": object_id,
        "source_ring": source_ring,
        "neighborhood_item_id": nb_id,
        "loan_status": loan_status,
        "return_receipt_kind": receipt_kind,
        "shopkeeper_received": shopkeeper_received,
        "via_return_box": bool(via_return_box),
        "returned_out": True,
        "affection_score_coupled": False,
    }


def _apply_deliver_card_to_public_board(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
) -> dict[str, Any]:
    """Deliver one real physical card to a public board; no private draft is read."""

    object_id = _text(step.get("object_id"), min_len=1, max_len=64, field="object_id")
    target_board = _choice(
        step.get("target_board"), _PUBLIC_BOARDS, field="target_board", default="public_notice_board"
    )
    obj = _object(state, object_id)
    if obj is None or not _is_active(obj):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    if not (bool(obj.get("paper_like")) or str(obj.get("item_kind")) in ("paper", "keepsake")):
        raise V08MatterError("INVALID_TARGET", "only a real paper card can go on a public board")

    # Read ONLY the physical card text: a frozen transcription snapshot if present,
    # else the inscription actually written on the card.  Never a private draft file.
    source = obj.get("transcribed_source")
    source = source if isinstance(source, Mapping) else {}
    excerpt = source.get("snapshot_excerpt_cn")
    if not isinstance(excerpt, str) or not excerpt.strip():
        excerpt = obj.get("inscribed_text_cn")
    if not isinstance(excerpt, str) or not excerpt.strip():
        raise V08MatterError(
            "CARD_NOT_INSCRIBED", "the card carries no written text to display", status_code=409
        )
    excerpt = excerpt.strip()
    version_id = source.get("version_id")
    label = _public_label(obj)
    body = excerpt
    if isinstance(version_id, str) and version_id.strip():
        body = f"{excerpt}\n\n（草稿版本 {version_id.strip()}）"

    op_key = _neighborhood_op_key("deliver_card", object_id, target_board, now_text)
    command = {
        "action": "post_public_notice",
        "title": label[:100],
        "body": body[:600],
        "operation_key": op_key,
    }
    outcome = _apply_neighborhood_command(root, command, now_text=now_text)
    result = outcome["result"]

    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    live["returned_out"] = True
    live["parent_id"] = None
    live["subject_last_seen_parent"] = None
    live["impression_stale"] = False
    live["delivered_to_board"] = target_board
    return {
        "op": "DELIVER_CARD_TO_PUBLIC_BOARD",
        "status": "applied",
        "object_id": object_id,
        "target_board": target_board,
        "notice_id": result.get("notice_id"),
        "snapshot_excerpt_cn": excerpt,
        "version_id": version_id if isinstance(version_id, str) else None,
        "private_draft_exposed": False,
        "returned_out": True,
        "affection_score_coupled": False,
    }


@_write_locked
def request_loan_extension(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Extend a shared-shelf loan; a plain date move with no affection coupling."""

    if actor_side != "subject":
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", "loan extension is the subject's own act")
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    object_id = _text(body.get("object_id"), min_len=1, max_len=64, field="object_id")
    extra_hours = _int_field(
        body.get("extra_hours"), field="extra_hours", default=24, minimum=1, maximum=168
    )
    note_cn = _optional_text(body.get("note_cn"), max_len=200, field="note_cn")

    now_dt, now_text = _resolve_now(now_iso)
    del now_dt
    state = _load_for_write(root)
    obj = _object(state, object_id)
    if obj is None or not _is_active(obj):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    meta = obj.get("loan_meta")
    if not isinstance(meta, Mapping) or str(meta.get("loan_status") or "") != "borrowed":
        raise V08MatterError("NOT_A_LOAN_ITEM", "this object is not a borrowed item", status_code=409)
    source_ring = str(meta.get("source_ring") or "")
    if source_ring != "shared_shelf":
        raise V08MatterError(
            "LOAN_EXTENSION_UNSUPPORTED",
            "only a shared-shelf loan can be extended here",
            status_code=409,
        )
    nb_id = str(meta.get("neighborhood_item_id") or "")
    op_key = _neighborhood_op_key("extend", nb_id, object_id, extra_hours, now_text)
    command = {
        "action": "shared_shelf_loan",
        "item_id": nb_id,
        "operation": "request_extension",
        "extra_hours": extra_hours,
        "operation_key": op_key,
    }
    if note_cn:
        command["reason"] = note_cn
    outcome = _apply_neighborhood_command(root, command, now_text=now_text)
    result = outcome["result"]

    live = state["matter_objects"].get(object_id)
    if not isinstance(live, dict):
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    updated_meta = copy.deepcopy(dict(meta))
    new_due = result.get("new_due_at") or updated_meta.get("due_at")
    updated_meta["due_at"] = new_due
    updated_meta["extension_status"] = "extended"
    updated_meta["extension_count"] = int(
        result.get("extension_count") or (updated_meta.get("extension_count") or 0) + 1
    )
    updated_meta["affection_score_coupled"] = False
    live["loan_meta"] = updated_meta
    _commit(root, state, now_text)
    return {
        "status": "applied",
        "action": "request_loan_extension",
        "object_id": object_id,
        "source_ring": source_ring,
        "due_at": new_due,
        "extension_count": updated_meta["extension_count"],
        "extension_status": "extended",
        "affection_score_coupled": False,
        "llm_calls": 0,
    }


def _reach_object_ids(step: Mapping[str, Any]) -> list[str]:
    """Object ids the subject's step physically needs to touch.

    Looking (``OBSERVE_OBJECT``) is intentionally absent: searching a place is how
    she discovers where something actually went.
    """

    op = str(step.get("op") or "").strip().upper()
    fields = _REACH_OBJECT_FIELDS.get(op)
    if not fields:
        return []
    ids: list[str] = []
    for field_name in fields:
        value = step.get(field_name)
        if isinstance(value, (list, tuple)):
            ids.extend(str(item) for item in value if item)
        elif value:
            ids.append(str(value))
    return ids


def _subject_reach_guard(state: dict[str, Any], step: Mapping[str, Any]) -> None:
    """A reach only finds matter that is still where the subject believes it is.

    Raises a 409 ``OBJECT_NOT_AT_EXPECTED_SPOT`` that names *only* the believed
    spot.  The real ``parent_id`` never enters the reason, message or receipt.
    """

    for object_id in _reach_object_ids(step):
        live = state.get("matter_objects", {}).get(object_id)
        if not isinstance(live, dict) or not _is_active(live):
            continue
        believed = live.get("subject_last_seen_parent")
        if bool(live.get("not_at_believed_spot")) or str(believed) == SUBJECT_MISSING_SPOT:
            label = _public_label(live)
            raise V08MatterError(
                "OBJECT_NOT_AT_EXPECTED_SPOT",
                f"你伸手去拿「{label}」，可那里没有它——不在你以为的位置",
            )
        if bool(live.get("impression_stale")) and believed:
            spot, label = _record_subject_reach_miss(state, object_id, believed)
            raise V08MatterError(
                "OBJECT_NOT_AT_EXPECTED_SPOT",
                f"你伸手去拿「{label}」，{spot}那里没有它——不在你以为的位置",
            )
        if _object_is_hidden_from_subject(state.get("matter_objects") or {}, object_id):
            label = _public_label(live)
            raise V08MatterError(
                "OBJECT_NOT_AT_EXPECTED_SPOT",
                f"你伸手去拿「{label}」，可那里没有它——不在你以为的位置",
            )


def _apply_primitive(
    state: dict[str, Any],
    root: Path | str,
    step: Mapping[str, Any],
    *,
    now_text: str,
    now_dt: datetime,
    default_visibility: Optional[str],
) -> dict[str, Any]:
    if not isinstance(step, Mapping):
        raise V08MatterError("INVALID_FIELDS", "each step must be an object")
    _assert_no_identity_keys(step)
    op = str(step.get("op") or "").strip().upper()
    if op not in OPEN_PRIMITIVES:
        raise V08MatterError("INVALID_FIELDS", "op must be one of the open primitives")
    if op == "PLACE_OR_MOVE":
        return _apply_place_or_move(state, root, step, now_text=now_text)
    if op == "MOVE_OBJECT":
        return _apply_place_or_move(state, root, step, now_text=now_text, op="MOVE_OBJECT")
    if op == "PLACE_INSIDE":
        return _apply_place_or_move(
            state, root, step, now_text=now_text, default_relation="inside", op="PLACE_INSIDE"
        )
    if op == "ATTACH_NOTE":
        return _apply_place_or_move(
            state, root, step, now_text=now_text, default_relation="attached_to", op="ATTACH_NOTE"
        )
    if op == "TAKE_OBJECT":
        return _apply_take_object(state, root, step, now_text=now_text)
    if op == "ARRANGE_OBJECTS":
        return _apply_arrange_objects(state, root, step, now_text=now_text)
    if op == "NOTE_ASSOCIATION":
        return _apply_note_association(state, root, step, now_text=now_text)
    if op == "NAME_PERSONAL_CORNER":
        return _apply_name_personal_corner(state, root, step, now_text=now_text)
    if op == "INTRODUCE_ITEM":
        return _apply_introduce_item(
            state, root, step, now_text=now_text, now_dt=now_dt,
            default_visibility=default_visibility,
        )
    if op == "TRANSFORM_CREATE":
        return _apply_transform_create(state, root, step, now_text=now_text, now_dt=now_dt)
    if op == "CONSUME_PORTION":
        return _apply_consume_portion(state, root, step, now_text=now_text, now_dt=now_dt)
    if op == "CLEAN_SURFACE_OR_VESSEL":
        return _apply_clean(state, root, step, now_text=now_text)
    if op == "DISCARD_ITEM":
        return _apply_discard(state, root, step, now_text=now_text)
    if op == "SPLIT_MATERIAL":
        return _apply_split_material(state, root, step, now_text=now_text)
    if op == "ASSEMBLE_OR_DETACH_PART":
        return _apply_assemble_or_detach_part(state, root, step, now_text=now_text)
    if op == "TRANSCRIBE_TO_PAPER":
        return _apply_transcribe_to_paper(state, root, step, now_text=now_text)
    if op == "PATCH_OR_REPAIR":
        return _apply_patch_or_repair(state, root, step, now_text=now_text)
    if op == "BORROW_NEIGHBORHOOD_ITEM":
        return _apply_borrow_neighborhood_item(state, root, step, now_text=now_text)
    if op == "RETURN_BORROWED_ITEM":
        return _apply_return_borrowed_item(state, root, step, now_text=now_text)
    if op == "DELIVER_CARD_TO_PUBLIC_BOARD":
        return _apply_deliver_card_to_public_board(state, root, step, now_text=now_text)
    if op == "PACK_FOR_OUTING":
        return _apply_pack_for_outing(state, root, step, now_text=now_text)
    return _apply_observe(state, root, step, now_text=now_text)


# ---------------------------------------------------------------------------
# Open multi-step composer
# ---------------------------------------------------------------------------


def compose_open_act(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Compose 1..6 open primitives into one honest multi-step act.

    The whole read->validate->mutate->bump->receipt->write transaction runs under
    one exclusive state lock, so two writers can never interleave a stale read
    with a fresh commit.
    """

    with state_file_lock(state_path(root)):
        return _compose_open_act_locked(
            root, payload, actor_side=actor_side, now_iso=now_iso
        )


def _compose_open_act_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Compose 1..6 open primitives into one honest multi-step act.

    On a later blocked step the already-landed prefix is kept and the receipt is
    ``partially_applied`` -- the full set is never faked.
    """

    if actor_side != "subject":
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", "open acts are the subject's own actions")
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)

    intent = _text(body.get("intent_cn"), min_len=1, max_len=120, field="intent_cn")
    raw_steps = body.get("steps")
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, (str, bytes)):
        raise V08MatterError("INVALID_FIELDS", "steps must be a list")
    steps = list(raw_steps)
    if not (1 <= len(steps) <= 6):
        raise V08MatterError("INVALID_FIELDS", "steps must have 1..6 entries")

    # Optional surface fields may be given once on the act and inherited by the
    # steps that understand them, so a phone-shaped flat payload and a nested
    # step payload express the same act.
    placement_defaults: dict[str, Any] = {
        key: body.get(key)
        for key in ("placement_relation", "posture_tag", "anchor_page")
        if body.get(key) is not None
    }
    if "anchor_page" in placement_defaults and "placement_relation" not in placement_defaults:
        placement_defaults["placement_relation"] = "tucked_in"
    arrangement_defaults: dict[str, Any] = {
        key: body.get(key)
        for key in ("arrangement_id", "zone_or_carrier_id", "member_ids", "pattern")
        if body.get(key) is not None
    }
    association_defaults: dict[str, Any] = {
        key: body.get(key)
        for key in ("target_id", "text", "association_text")
        if body.get(key) is not None
    }
    if placement_defaults or arrangement_defaults or association_defaults:
        decorated: list[Any] = []
        for entry in steps:
            if not isinstance(entry, Mapping):
                decorated.append(entry)
                continue
            op = str(entry.get("op") or "").strip().upper()
            if op in (
                "PLACE_OR_MOVE", "MOVE_OBJECT", "PLACE_INSIDE", "ATTACH_NOTE",
                "INTRODUCE_ITEM",
            ):
                inherited = placement_defaults
            elif op == "ARRANGE_OBJECTS":
                inherited = arrangement_defaults
            elif op == "NOTE_ASSOCIATION":
                inherited = association_defaults
            else:
                inherited = {}
            if not inherited:
                decorated.append(entry)
                continue
            merged = dict(entry)
            for key, value in inherited.items():
                merged.setdefault(key, value)
            decorated.append(merged)
        steps = decorated

    stop_after: Optional[int] = None
    if body.get("stop_after_step") is not None:
        stop_after = _int_field(
            body.get("stop_after_step"), field="stop_after_step",
            minimum=1, maximum=len(steps),
        )
    if body.get("allow_partial") is not None:
        allow_partial = _strict_bool(body.get("allow_partial"), field="allow_partial")
    else:
        allow_partial = len(steps) >= 2

    default_visibility: Optional[str] = None
    if body.get("visibility") is not None:
        default_visibility = _choice(body.get("visibility"), VISIBILITIES, field="visibility")

    soils_surface_id = _optional_text(body.get("soils_surface_id"), max_len=64, field="soils_surface_id")
    strenuous = _strict_bool(body.get("strenuous_outing"), field="strenuous_outing") if body.get("strenuous_outing") is not None else False
    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    expected_revision: Optional[int] = None
    if body.get("expected_revision") is not None:
        expected_revision = _int_field(
            body.get("expected_revision"), field="expected_revision", minimum=0
        )

    now_dt, now_text = _resolve_now(now_iso)
    fingerprint = _fingerprint("compose_open_act", actor_side, _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state,
        operation_key=operation_key,
        fingerprint=fingerprint,
        root=root,
        expected_revision=expected_revision,
    )
    if replay is not None:
        return replay

    if strenuous and _blocks_strenuous_outing(root):
        raise V08MatterError(
            "ILLNESS_GATE_BLOCKED",
            "the body is unwell; a strenuous outing act is not allowed right now",
        )

    if soils_surface_id is not None and _zone(state, soils_surface_id) is None:
        if _materialize_custom_zone(state, root, soils_surface_id) is None:
            raise V08MatterError("INVALID_TARGET", f"unknown surface: {soils_surface_id}")

    # Bring the whole space forward before composing new facts on top.
    _settle_state(state, now_dt, now_text, env_context=_read_microzone_env_zero_write(root, now_text))

    limit = stop_after if stop_after is not None else len(steps)
    step_results: list[dict[str, Any]] = []
    failed: Optional[dict[str, Any]] = None
    applied = 0
    for index in range(limit):
        step = steps[index]
        try:
            _subject_reach_guard(state, step)
            result = _apply_primitive(
                state, root, step,
                now_text=now_text, now_dt=now_dt, default_visibility=default_visibility,
            )
        except V08MatterError as error:
            if error.reason_code == "OBJECT_NOT_AT_EXPECTED_SPOT":
                # The miss itself is a real, subject-witnessed fact: persist it
                # so the next read stops presenting the object at the old spot.
                # No receipt/recent_act is written for a blind grab.
                _bump(state, now_text)
                _atomic_write_state(root, state)
            if not allow_partial or applied == 0:
                raise
            failed = {
                "step_index": index,
                "op": str(step.get("op") if isinstance(step, Mapping) else ""),
                "reason_code": error.reason_code,
                "message": error.message,
                "status_code": error.status_code,
            }
            break
        step_results.append(result)
        applied += 1

    status = "applied"
    stop_reason: Optional[str] = None
    if failed is not None:
        status = "partially_applied"
        stop_reason = failed["reason_code"]
    elif stop_after is not None and stop_after < len(steps):
        status = "partially_applied"
        stop_reason = "STOP_AFTER_STEP"

    soiled = False
    if applied > 0 and soils_surface_id is not None:
        live_zone = state["zones"].get(soils_surface_id)
        if isinstance(live_zone, dict):
            live_zone["surface_cleanliness"] = "soiled"
            soiled = True

    receipt: dict[str, Any] = {
        "status": status,
        "action": "compose_open_act",
        "intent_cn": intent,
        "steps_total": len(steps),
        "steps_applied": applied,
        "step_results": step_results,
        "failed_step": failed,
        "stop_reason": stop_reason,
        "stop_after_step": stop_after,
        "soiled_surface_id": soils_surface_id if soiled else None,
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }

    history = state.get("recent_acts")
    if not isinstance(history, list):
        history = []
    history.append({
        "intent_cn": intent,
        "status": status,
        "steps_applied": applied,
        "steps_total": len(steps),
        "actor_side": actor_side,
        "visibility": default_visibility or "shared",
        "at": now_text,
    })
    state["recent_acts"] = history[-MAX_RECENT_ACTS:]

    # If nothing actually landed, do not create a state file on disk.
    if applied > 0 or _state_file_exists(root):
        _bump(state, now_text)
        if applied > 0:
            _bump_subject_view(state)
        _store_operation(
            state,
            operation_key=operation_key,
            fingerprint=fingerprint,
            receipt=receipt,
            committed_at=now_text,
        )
        _atomic_write_state(root, state)
    return receipt


def _state_file_exists(root: Path | str) -> bool:
    path = state_path(root)
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Partner-facing acts (privacy is enforced before anything is read)
# ---------------------------------------------------------------------------


def _partner_private_or_missing(
    state: Mapping[str, Any],
    object_id: Any,
    *,
    target_id: Any = None,
    root: Optional[Path | str] = None,
) -> None:
    oid = str(object_id or "").strip()
    obj = _object(state, oid)
    if obj is None:
        raise V08MatterError("OBJECT_NOT_FOUND", "no such object is visible")
    if _is_private_object(state, obj):
        raise V08MatterError("OBJECT_NOT_FOUND", "no such object is visible")
    if target_id is not None:
        tid = str(target_id or "").strip()
        target_zone = _zone(state, tid)
        if target_zone is None and root is not None:
            # Shared custom zones are legal destinations for either side.
            target_zone = _materialize_custom_zone(state, root, tid)
        if target_zone is not None:
            if _is_private_zone(target_zone):
                raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")
            return
        target_obj = _object(state, tid)
        if target_obj is None or _is_private_object(state, target_obj):
            raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")


def _load_for_write(root: Path | str) -> dict[str, Any]:
    return _read_state_zero_write(root)


def _commit(root: Path | str, state: dict[str, Any], now_text: str) -> None:
    _bump(state, now_text)
    _atomic_write_state(root, state)


@_write_locked
def partner_bring_item(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """A partner may add a real item to a shared place; private matter is invisible."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    now_dt, now_text = _resolve_now(now_iso)
    del now_dt

    label = _text(body.get("label_cn"), min_len=1, max_len=60, field="label_cn")
    item_kind = _choice(body.get("item_kind"), ITEM_KINDS, field="item_kind")
    target_id = str(body.get("target_parent_id") or "").strip()

    state = _load_for_write(root)
    if _zone(state, target_id) is None:
        _materialize_custom_zone(state, root, target_id)
    if _zone(state, target_id) is None:
        raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")
    if _is_private_zone(_zone(state, target_id) or {}):
        raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")

    visibility = "private" if body.get("visibility") is not None and _choice(body.get("visibility"), VISIBILITIES, field="visibility") == "private" else "shared"
    if visibility == "private":
        # A partner cannot mint private matter into a shared place.
        raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")

    paper_like = _strict_bool(body.get("paper_like"), field="paper_like") if body.get("paper_like") is not None else False
    raw_inscription = body.get("inscribed_text_cn")
    inscription = _optional_text(raw_inscription, max_len=120, field="inscribed_text_cn")
    if raw_inscription is not None and inscription is None:
        raise V08MatterError("INVALID_FIELDS", "inscribed_text_cn must not be blank")
    if inscription is not None and not (paper_like or item_kind in ("paper", "keepsake")):
        raise V08MatterError(
            "INVALID_FIELDS",
            "inscribed_text_cn is only allowed on paper or keepsake items",
        )
    shelf_life = _shelf_life(body.get("shelf_life_hours"))
    sections = body.get("portions")
    portions_total = None
    portions_left = None
    if sections is not None:
        portions_total = _int_field(sections, field="portions", default=_DEFAULT_PORTIONS, minimum=1, maximum=99)
        portions_left = portions_total
    material_units = _int_field(
        body.get("material_units"), field="material_units", default=1, minimum=1, maximum=24
    )
    assembly_role = _choice(
        body.get("assembly_role"), ASSEMBLY_ROLES, field="assembly_role", default="none"
    )

    object_id = _optional_text(body.get("object_id"), max_len=64, field="object_id")
    if object_id is None:
        object_id = _new_object_id("lent", label, item_kind, now_text, len(state.get("matter_objects") or {}))
    if _object(state, object_id) is not None:
        raise V08MatterError("INVALID_FIELDS", f"object already exists: {object_id}")

    obj = _object_row(
        object_id, label, item_kind=item_kind, parent_id=target_id, paper_like=paper_like,
        material_units=material_units, assembly_role=assembly_role,
    )
    obj["inscribed_text_cn"] = inscription
    obj["thermal"] = _choice(body.get("thermal"), THERMAL_STATES, field="thermal", default="room")
    obj["cook_state"] = _choice(body.get("cook_state"), COOK_STATES, field="cook_state", default="none")
    obj["portions_total"] = portions_total
    obj["portions_left"] = portions_left
    obj["last_thermal_at"] = now_text
    obj["last_moisture_at"] = now_text
    obj["state_clock_iso"] = now_text
    if shelf_life is not None:
        obj["shelf_life_hours"] = shelf_life
        obj["freshness"] = "fresh"
        obj["freshness_anchor_at"] = now_text
        obj["freshness_settled_at"] = now_text
        obj["effective_exposure_hours"] = 0.0
    elif item_kind in ("ingredient", "prepared_food"):
        obj["freshness"] = "fresh"
    else:
        obj["freshness"] = "non_perishable"
    if body.get("requires_cooking") is not None:
        obj["requires_cooking"] = _strict_bool(body.get("requires_cooking"), field="requires_cooking")
    if body.get("lent_to_subject") is not None and _strict_bool(body.get("lent_to_subject"), field="lent_to_subject"):
        obj["lent_by"] = "partner"
    # Bringing something into a shared place is a real, visible addition: the
    # subject sees it where it was put.  Only a *move* of matter she already had
    # an impression of (``partner_move_shared_object``) creates a stale belief.
    obj["placement_relation"] = _default_placement_relation(state, target_id)
    obj["subject_last_seen_parent"] = target_id
    obj["subject_last_seen_snapshot"] = _capture_subject_snapshot(obj)
    obj["impression_stale"] = False

    _validate_placement(state, obj, target_id)
    state["matter_objects"][object_id] = obj
    _commit(root, state, now_text)
    return {
        "status": "applied",
        "action": "partner_bring_item",
        "object_id": object_id,
        "label_cn": label,
        "target_parent_id": target_id,
        "at": now_text,
        "llm_calls": 0,
    }


@_write_locked
def partner_move_shared_object(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """A partner may move shared matter; the subject's knowledge does not follow."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    _, now_text = _resolve_now(now_iso)

    object_id = str(body.get("object_id") or "").strip()
    target_id = str(body.get("target_parent_id") or "").strip()

    state = _load_for_write(root)
    _partner_private_or_missing(state, object_id, target_id=target_id, root=root)
    obj = _object(state, object_id)
    if obj is None:  # defensive; _partner_private_or_missing already refused
        raise V08MatterError("OBJECT_NOT_FOUND", "no such object is visible")

    _validate_target_exists(state, target_id, root=root)
    _validate_placement(state, obj, target_id)
    using_at = _optional_text(
        body.get("subject_using_at") if body.get("subject_using_at") is not None else obj.get("currently_in_use_at"),
        max_len=80,
        field="subject_using_at",
    )
    if using_at and target_id != using_at and not bool(body.get("force_apply")):
        compromise = negotiate_shared_lamp_move(
            requested_zone=target_id,
            subject_using_lamp_at=using_at,
        )
        return {
            "status": "counter_compromise",
            "action": "partner_move_shared_object",
            "object_id": object_id,
            "requested_zone": target_id,
            "outcome": compromise["outcome"],
            "suggested_zone": compromise["suggested_zone"],
            "reply_cn": compromise["reply_cn"],
            "at": now_text,
            "llm_calls": 0,
        }
    old_ambient = _ambient_for(state, obj)
    live = state["matter_objects"][object_id]
    moved = str(live.get("parent_id") or "") != target_id
    if live.get("subject_last_seen_parent") and not live.get("impression_stale"):
        # Freeze the subject's last genuine sighting before reality moves away.
        live["subject_last_seen_snapshot"] = _capture_subject_snapshot(live)
    live["parent_id"] = target_id
    # Deliberately keep subject_last_seen_parent unchanged and mark it stale.
    live["impression_stale"] = True
    if moved:
        # A carrier-bound drape/tuck/lean does not survive leaving its carrier;
        # intrinsic traces (folded/neat) do.  The subject's frozen snapshot keeps
        # her believing the old drape until she really looks.
        _clear_carrier_binding(live, relation="")
        live["placement_relation"] = _default_placement_relation(state, target_id)
        _break_arrangements_for_member(
            state, object_id, now_text=now_text, reason="MEMBER_MOVED", actor_side="partner"
        )
    _anchor_object_clocks_if_needed(state, live, now_text=now_text, old_ambient=old_ambient)
    _carry_assembled_parts(state, object_id, target_id, now_text=now_text, mark_stale=True)

    _commit(root, state, now_text)
    return {
        "status": "applied",
        "action": "partner_move_shared_object",
        "object_id": object_id,
        "target_parent_id": target_id,
        "impression_stale": True,
        "at": now_text,
        "llm_calls": 0,
    }


@_write_locked
def partner_propose_object_use(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Propose a new use for shared matter; the subject decides later."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    _, now_text = _resolve_now(now_iso)

    object_id = str(body.get("object_id") or "").strip()
    proposed_use = _text(body.get("proposed_use_cn"), min_len=1, max_len=80, field="proposed_use_cn")

    state = _load_for_write(root)
    _partner_private_or_missing(state, object_id)
    obj = _object(state, object_id)
    if obj is None:
        raise V08MatterError("OBJECT_NOT_FOUND", "no such object is visible")

    live = state["matter_objects"][object_id]
    live["proposed_use"] = {
        "proposed_use_cn": proposed_use,
        "status": "proposed",
        "proposed_by": "partner",
        "at": now_text,
    }
    _commit(root, state, now_text)
    return {
        "status": "applied",
        "action": "partner_propose_object_use",
        "object_id": object_id,
        "proposed_use_cn": proposed_use,
        "proposal_status": "proposed",
        "at": now_text,
        "llm_calls": 0,
    }


@_write_locked
def respond_proposed_use(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Subject adopts or declines a proposed use."""

    if actor_side != "subject":
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", "only the subject decides on a proposal")
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    _, now_text = _resolve_now(now_iso)

    object_id = str(body.get("object_id") or "").strip()
    decision = _choice(body.get("decision"), ("adopt", "decline"), field="decision")

    state = _load_for_write(root)
    obj = _object(state, object_id)
    if obj is None:
        raise V08MatterError("OBJECT_NOT_FOUND", f"unknown object: {object_id}")
    proposal = obj.get("proposed_use")
    if not isinstance(proposal, Mapping) or str(proposal.get("status")) != "proposed":
        raise V08MatterError("INVALID_TARGET", "there is no pending proposal on that object")

    status = "adopted" if decision == "adopt" else "declined"
    live = state["matter_objects"][object_id]
    updated = dict(proposal)
    updated["status"] = status
    updated["decided_at"] = now_text
    updated["decided_by"] = "subject"
    live["proposed_use"] = updated
    _commit(root, state, now_text)
    return {
        "status": "applied",
        "action": "respond_proposed_use",
        "object_id": object_id,
        "decision": decision,
        "proposal_status": status,
        "at": now_text,
        "llm_calls": 0,
    }


@_write_locked
def name_personal_corner(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """The subject names a real corner in her own words (Ch.16 §3 #13).

    This is her private vocabulary, so a partner door is a named 403 rather than
    a silent no-op.  ``shared_with_partner`` is an explicit, separate decision.
    """

    if actor_side != "subject":
        raise V08MatterError(
            "FORBIDDEN_ACTOR_SIDE", "a personal corner name is the subject's own act"
        )
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    _, now_text = _resolve_now(now_iso)

    state = _load_for_write(root)
    result = _apply_name_personal_corner(state, root, body, now_text=now_text)
    result = dict(result)
    result["action"] = "name_personal_corner"
    result["at"] = now_text
    result["llm_calls"] = 0
    _commit(root, state, now_text)
    return result


# ---------------------------------------------------------------------------
# Clock hook: settle physical state
# ---------------------------------------------------------------------------


def settle_matter_physics(root: Path | str, now_iso: Optional[str] = None) -> dict[str, int]:
    """Advance real physical clocks.  A missing state file is 100% zero-write.

    With no matter on disk there is nothing to rain-dampen, so the legacy
    five-key zero shape is returned unchanged; once a real state exists every
    settle receipt also carries ``rain_dampened`` (0 unless real rain fell).
    """

    zero = {"settled_items": 0, "cooled": 0, "chilled": 0, "spoiled": 0, "dried": 0}
    # Zero-write fast path: never take the lock (and never create ``.lock``) when
    # there is no state file at all.
    if not _state_file_exists(root):
        return zero

    with state_file_lock(state_path(root)):
        if not _state_file_exists(root):
            return zero
        now_dt, now_text = _resolve_now(now_iso)
        state = _read_state_zero_write(root)
        counts = _settle_state(
            state, now_dt, now_text, env_context=_read_microzone_env_zero_write(root, now_text)
        )
        if counts["settled_items"] > 0:
            _bump(state, now_text)
            _atomic_write_state(root, state)
        return counts


# ---------------------------------------------------------------------------
# Zero-write opportunity scan for the daily loop bridge
# ---------------------------------------------------------------------------


def _perishable(obj: Mapping[str, Any]) -> bool:
    shelf = obj.get("shelf_life_hours")
    return isinstance(shelf, (int, float)) and float(shelf) > 0 and str(obj.get("freshness") or "") in ("fresh", "aging")


def _zone_has_space(state: Mapping[str, Any], zone_id: str) -> bool:
    zone = _zone(state, zone_id)
    if zone is None:
        return False
    return len(_children_of(state, zone_id)) < int(zone.get("capacity_slots") or 0)


def scan_matter_opportunities_zero_write(
    root: Path | str,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """At most two objective, physics-derived candidates.  Never writes."""

    if actor_side not in ("subject", "partner"):
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")
    if not _state_file_exists(root):
        return []
    now_dt, now_text = _resolve_now(now_iso)
    live_state = _project_state(_read_state_zero_write(root), now_dt, now_text, root=root)
    state = live_state
    if actor_side == "subject":
        # Never hand the subject an oracle: candidates come from the space as
        # she believes it, so a partner's hidden move cannot surface here.
        state = _subject_believed_state(live_state)
    objects = {
        oid: obj for oid, obj in _active_objects(state).items()
        if not obj.get("_subject_hidden")
        and not (actor_side == "partner" and _is_private_object(state, obj))
    }

    candidates: list[dict[str, Any]] = []

    # 0) A borrowed thing nearing its due time (or overdue): one calm, physical
    #    "take it back" candidate -- never a debt or guilt signal.
    for oid in sorted(objects):
        obj = objects[oid]
        loan = obj.get("loan_meta")
        if not isinstance(loan, Mapping) or str(loan.get("loan_status") or "") != "borrowed":
            continue
        due_dt = _parse_iso(loan.get("due_at"))
        if due_dt is None:
            continue
        if (due_dt - now_dt).total_seconds() > 12 * 3600:
            continue
        label = _public_label(obj)
        candidates.append({
            "target_ref": f"v08_matter:return_loan:{oid}",
            "title_hint": f"顺手把借来的「{label}」拿去还",
            "availability_basis": "MATTER_LOAN_DUE_SOON",
            "open_act_payload": {
                "intent_cn": "把借到期的物件还回原处",
                "steps": [{"op": "RETURN_BORROWED_ITEM", "object_id": oid}],
            },
        })
        break

    # 1) Used/cluttered/soiled shared surfaces.
    tidy_zones = ("dining_table", "home_desk", "kitchen_counter", "home_staging_corner")
    used_vessel_ids: list[str] = []
    cluttered_zone: Optional[str] = None
    soiled_zone: Optional[str] = None
    for zone_id in tidy_zones:
        zone = _zone(state, zone_id)
        if zone is None:
            continue
        if zone.get("surface_cleanliness") == "soiled":
            soiled_zone = soiled_zone or zone_id
        if not _zone_has_space(state, zone_id):
            cluttered_zone = cluttered_zone or zone_id
        for child_id in _children_of(state, zone_id):
            child = objects.get(child_id)
            if child is not None and str(child.get("item_kind")) == "vessel" and str(child.get("cleanliness")) == "used":
                used_vessel_ids.append(child_id)
    if soiled_zone or cluttered_zone or used_vessel_ids:
        steps: list[dict[str, Any]] = []
        if used_vessel_ids:
            steps.append({"op": "PLACE_OR_MOVE", "object_id": used_vessel_ids[0], "target_parent_id": "kitchen_sink"})
            steps.append({"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": used_vessel_ids[0]})
        if soiled_zone:
            steps.append({"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": soiled_zone})
        candidates.append({
            "target_ref": "v08_matter:tidy_clean",
            "title_hint": "先收拾或洗一下手边用过的桌面/器皿",
            "availability_basis": "MATTER_USED_OR_CLUTTERED",
            "open_act_payload": {
                "intent_cn": "随手收拾一下用过的桌面和器皿",
                "steps": steps or [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": soiled_zone or cluttered_zone or "home_desk"}],
            },
        })

    # 2) Food trajectory: fridge, cooking, or eating what is already made.
    if len(candidates) < 2:
        room_perishables = [
            oid for oid, obj in objects.items()
            if _perishable(obj) and _ambient_for(state, obj) == "room" and str(obj.get("item_kind")) in ("ingredient", "prepared_food", "drink")
        ]
        cooked_ready = [
            oid for oid, obj in objects.items()
            if str(obj.get("item_kind")) == "prepared_food"
            and isinstance(obj.get("portions_left"), int) and obj.get("portions_left") > 0
            and str(obj.get("freshness")) != "spoiled"
        ]
        raw_ingredients = [
            oid for oid, obj in objects.items()
            if str(obj.get("item_kind")) == "ingredient" and str(obj.get("cook_state")) == "raw"
        ]
        stove_free = _zone_has_space(state, "kitchen_stove")
        if room_perishables and _zone_has_space(state, "home_fridge"):
            target = room_perishables[0]
            candidates.append({
                "target_ref": f"v08_matter:chill:{target}",
                "title_hint": "把室温下的易腐食材先收进冰箱",
                "availability_basis": "MATTER_PERISHABLE_AT_ROOM",
                "open_act_payload": {
                    "intent_cn": "把易坏的食材收进冰箱",
                    "steps": [{"op": "PLACE_OR_MOVE", "object_id": target, "target_parent_id": "home_fridge"}],
                },
            })
        elif raw_ingredients and stove_free:
            source = raw_ingredients[0]
            candidates.append({
                "target_ref": "v08_matter:cook",
                "title_hint": "手边有生鲜原料、灶台也空着，可以搭一顿热餐",
                "availability_basis": "MATTER_RAW_AND_STOVE_FREE",
                "open_act_payload": {
                    "intent_cn": "用现有原料做一份热食",
                    "steps": [{
                        "op": "TRANSFORM_CREATE",
                        "source_object_ids": [source],
                        "output_label_cn": "现做的一份热食",
                        "medium_object_id": "kitchen_stove",
                    }],
                },
            })
        elif cooked_ready:
            target = cooked_ready[0]
            candidates.append({
                "target_ref": f"v08_matter:eat:{target}",
                "title_hint": "有做好的成品，可以吃一份",
                "availability_basis": "MATTER_COOKED_FOOD_READY",
                "open_act_payload": {
                    "intent_cn": "趁热吃一份做好的食物",
                    "steps": [{"op": "CONSUME_PORTION", "object_id": target}],
                },
            })

    # 3) Lost track of something.  For the subject this is only ever offered
    #    *after* a real reach-miss already told her an item is no longer at the
    #    spot she believed -- never off a partner's hidden move she has not
    #    witnessed (``_subject_believed_state`` keeps those objects hidden and
    #    withholds ``impression_stale``).  The partner God-view may surface real
    #    stale movement directly.
    if len(candidates) < 2:
        if actor_side == "subject":
            live_objects = live_state.get("matter_objects") or {}
            lost_candidates = sorted(
                oid for oid, obj in live_objects.items()
                if isinstance(obj, Mapping)
                and _is_active(obj)
                and bool(obj.get("not_at_believed_spot"))
                # Never hint at an item sealed inside a carrier she has not opened.
                and not _object_is_hidden_from_subject(live_objects, oid)
            )
        else:
            lost_candidates = sorted(
                oid for oid, obj in objects.items() if bool(obj.get("impression_stale"))
            )
        if lost_candidates:
            target = lost_candidates[0]
            candidates.append({
                "target_ref": f"v08_matter:observe:{target}",
                "title_hint": "有东西被挪动过，看一眼现在放在哪",
                "availability_basis": "MATTER_UNSEEN_MOVE",
                "open_act_payload": {
                    "intent_cn": "看一眼被挪动过的小物",
                    "steps": [{"op": "OBSERVE_OBJECT", "object_id": target}],
                },
            })

    # 4) Craft in progress: an oversized insert waiting on the desk, or stock
    #    that can be honestly split.  At most one craft hint, only when there is
    #    still room, so the objective tidy/food/observe order is unchanged.
    if len(candidates) < 2:
        oversized = sorted(
            oid for oid, obj in objects.items()
            if str(obj.get("fit_status") or "") == "oversized_unfitted"
            and str(obj.get("parent_id") or "") == "home_desk"
        )
        if oversized:
            part_id = oversized[0]
            candidates.append({
                "target_ref": f"v08_matter:craft_refit:{part_id}",
                "title_hint": "桌上有个偏大装不进去的配件，可以修整一下再试装",
                "availability_basis": "MATTER_PART_OVERSIZED_UNFITTED",
                "open_act_payload": {
                    "intent_cn": "把偏大的配件修整一下，准备重新装上",
                    "steps": [{
                        "op": "PATCH_OR_REPAIR",
                        "object_id": part_id,
                        "patch_note_cn": "修整一下边缘，重新试着装进去",
                        "fixes_fit": True,
                    }],
                },
            })
        else:
            splittable = sorted(
                oid for oid, obj in objects.items()
                if str(obj.get("parent_id") or "") == "home_desk"
                and (bool(obj.get("paper_like")) or str(obj.get("item_kind")) == "paper")
                and int(obj.get("material_units") or 1) >= 2
            )
            if splittable:
                stock_id = splittable[0]
                candidates.append({
                    "target_ref": f"v08_matter:craft_split:{stock_id}",
                    "title_hint": "桌上有整块的纸料，可以裁下一小块备用",
                    "availability_basis": "MATTER_STOCK_SPLITTABLE",
                    "open_act_payload": {
                        "intent_cn": "从整块纸料上裁下一小块",
                        "steps": [{
                            "op": "SPLIT_MATERIAL",
                            "object_id": stock_id,
                            "take_units": 1,
                            "output_label_cn": "裁下的一小块纸料",
                        }],
                    },
                })

    return candidates[:2]


# ---------------------------------------------------------------------------
# Zero-write co-placement preview (v3.0 Ch.27 §四.1 -- 共同试摆：先看占用格位与物理影响)
# ---------------------------------------------------------------------------


def _preview_resolve_custom_zone(root: Path | str, zone_id: str) -> Optional[dict[str, Any]]:
    """Look up one custom WorldGen/World-Editor zone in memory only."""

    try:
        zones, _ = _discover_custom_world(root)
    except Exception:  # noqa: BLE001 - a broken catalogue is simply absent
        return None
    for zone in zones:
        if str(zone.get("zone_id") or "") == zone_id:
            return dict(zone)
    return None


def preview_placement_zero_write(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Preview a co-placement: occupied slots and real physical effects.

    100% zero-write: it never creates ``<root>/world`` or the state file, never
    mutates an existing file and never touches a mtime.  It answers *where the
    item would go, whether it fits, and what the live surface would do to it*
    so both sides can look before anyone commits a real write.
    """

    if actor_side not in ("subject", "partner"):
        raise V08MatterError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)

    now_dt, now_text = _resolve_now(now_iso)
    # A projected deep copy settles physics in memory; nothing is written back.
    state = _project_state(_read_state_zero_write(root), now_dt, now_text, root=root)
    if actor_side == "subject":
        # Preview strictly against the subject's believed space.  A hidden
        # partner move or a sealed carrier must never answer as an oracle.
        state = _subject_believed_state(state)

    target_id = str(body.get("target_parent_id") or "").strip()
    if not target_id:
        raise V08MatterError("INVALID_FIELDS", "target_parent_id is required")

    target_zone = _zone(state, target_id)
    target_obj = _object(state, target_id)
    if target_zone is None and target_obj is None:
        # Custom zones are real before their first write; resolve in memory only.
        custom_zone = _preview_resolve_custom_zone(root, target_id)
        if custom_zone is not None:
            target_zone = custom_zone

    # Strict partner privacy: private/unseen places are a plain 404.
    if actor_side == "partner":
        if target_zone is not None and (
            _is_private_zone(target_zone) or str(target_id) == "subject_private_drawer"
        ):
            raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")
        if target_obj is not None and _is_private_object(state, target_obj):
            raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")
    elif target_obj is not None and target_obj.get("_subject_hidden"):
        raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")

    if target_zone is None and target_obj is None:
        if actor_side == "partner":
            raise V08MatterError("OBJECT_NOT_FOUND", "no such place is visible")
        raise V08MatterError("INVALID_TARGET", f"unknown target: {target_id}")

    # Resolve the incoming item: an existing object, or a candidate new one.
    object_id = _optional_text(body.get("object_id"), max_len=64, field="object_id")
    incoming: Optional[dict[str, Any]] = None
    is_existing = False
    if object_id is not None:
        if actor_side == "partner":
            existing = _object(state, object_id)
            if (
                existing is None
                or not _is_active(existing)
                or _is_private_object(state, existing)
            ):
                raise V08MatterError("OBJECT_NOT_FOUND", "no such object is visible")
            incoming = dict(existing)
        else:
            # Materialises a custom/open object into the in-memory copy only.
            incoming = _ensure_object(
                state, root, object_id, actor_side="subject", now_text=now_text
            )
            if incoming.get("_subject_hidden"):
                raise V08MatterError("OBJECT_NOT_FOUND", "no such object is visible")
        is_existing = True

    if incoming is not None:
        item_kind = str(incoming.get("item_kind") or "everyday")
        paper_like = bool(incoming.get("paper_like")) or item_kind == "paper"
    else:
        item_kind = _choice(body.get("item_kind"), ITEM_KINDS, field="item_kind", default="everyday")
        if body.get("paper_like") is not None:
            paper_like = _strict_bool(body.get("paper_like"), field="paper_like")
        else:
            paper_like = item_kind == "paper"
        label = _optional_text(body.get("label_cn"), max_len=60, field="label_cn")
        shelf_life = _shelf_life(body.get("shelf_life_hours"))
        moisture: Optional[str] = None
        if body.get("moisture") is not None:
            moisture = _choice(body.get("moisture"), MOISTURE_STATES, field="moisture")
        incoming = _object_row(
            "__preview_candidate__",
            label or "预览物件",
            item_kind=item_kind,
            parent_id=None,
            paper_like=paper_like,
        )
        if shelf_life is not None:
            incoming["shelf_life_hours"] = shelf_life
        if moisture is not None:
            incoming["moisture"] = moisture

    # Capacity and occupancy of the real target.
    if target_zone is not None:
        capacity_slots = int(target_zone.get("capacity_slots") or 0)
        target_label_cn = str(target_zone.get("label_cn") or target_id)
        target_ambient = str(target_zone.get("ambient_zone") or "room")
    else:
        live_target = target_obj or {}
        capacity_slots = int(live_target.get("capacity_slots") or 0)
        target_label_cn = str(live_target.get("label_cn") or target_id)
        target_ambient = _ambient_for(state, live_target)
    target_is_container = capacity_slots > 0

    slots_used_before = len(_children_of(state, target_id))
    slots_free_before = max(0, capacity_slots - slots_used_before)

    # Already inside the target?  Then it keeps its current slot rather than
    # consuming a new one (and is never refused for "fullness").
    already_inside = bool(
        is_existing
        and object_id is not None
        and str((incoming or {}).get("parent_id") or "") == target_id
    )

    can_place = True
    blocking_reason_cn = ""
    if is_existing and object_id is not None and object_id == target_id:
        can_place = False
        blocking_reason_cn = "一个物件没法放进它自己里面"
    elif not target_is_container:
        can_place = False
        blocking_reason_cn = "这个位置不是能放东西的容器"
    elif not already_inside and slots_used_before >= capacity_slots:
        can_place = False
        need = slots_used_before - capacity_slots + 1
        blocking_reason_cn = (
            f"这里只能放 {capacity_slots} 样，现在已经满了，得先腾出 {need} 个空位"
        )

    if already_inside:
        slots_after = slots_used_before
    else:
        slots_after = slots_used_before + (1 if can_place else 0)

    # Deterministic physical-effect predictions from live target & item state.
    effects: list[str] = []

    def _add_effect(text: str) -> None:
        if text not in effects:
            effects.append(text)

    if target_ambient == "cold":
        _add_effect("放进冷藏区：大约 20 分钟会变凉，易坏的东西保鲜时长拉长到常温的 6 倍")
    elif target_ambient == "heating":
        _add_effect("放在热源上：可以用于加热或烹煮熟食，小心别把怕烫或纸质的东西久留这里")

    water_source = False
    target_wet = False
    target_cleanliness = "clean"
    if target_zone is not None:
        water_source = bool(target_zone.get("water_source"))
        target_cleanliness = str(target_zone.get("surface_cleanliness") or "clean")
    elif target_obj is not None:
        water_source = _zone_has_water(state, target_obj.get("parent_id"))
        target_wet = str(target_obj.get("moisture") or "dry") in ("damp", "wet")
        target_cleanliness = str(target_obj.get("cleanliness") or "clean")

    if (water_source or target_wet) and (paper_like or item_kind == "paper"):
        _add_effect("靠近水源与水汽：纸质表面或字迹容易被水汽洇开")

    if (
        target_obj is not None
        and str(target_obj.get("item_kind") or "") == "vessel"
        and item_kind in ("tool", "everyday", "paper", "keepsake")
    ):
        _add_effect("装进容器之后：下次要用这只容器喝水前得先把东西挪出来")

    if target_cleanliness == "soiled" and item_kind in ("ingredient", "prepared_food", "drink"):
        _add_effect("台面或容器现在还脏着，直接放上去不太顺手，建议先清理一下")

    return {
        "ok": True,
        "schema": VIEW_SCHEMA,
        "mutated_state": False,
        "can_place": can_place,
        "target_parent_id": target_id,
        "target_label_cn": target_label_cn,
        "target_ambient_zone": target_ambient,
        "capacity_slots": capacity_slots,
        "slots_used_before": slots_used_before,
        "slots_after": slots_after,
        "blocking_reason_cn": blocking_reason_cn,
        "predicted_effects_cn": effects,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# v0.5 Step 3: Tide curios, habitual slot, entrance cartons, diet, scent,
# reversible corkboard wall & shared-object gentle compromise
# ---------------------------------------------------------------------------

_CURIO_REEF_COLORS: tuple[str, ...] = (
    "浅青色磨砂海玻璃",
    "琥珀棕圆润海玻璃",
    "雾白色半透海玻璃",
)
_CURIO_BOOK_NOTES: tuple[str, ...] = (
    "扉页夹着半张褪色的海港电车旧票根",
    "第 19 页页边留着前人淡淡的铅笔字：‘雨后暗部留白更好看’",
    "封底贴着一张手绘的镇西林间抄近路小便签",
)
_MEAT_DIET_KEYWORDS: tuple[str, ...] = (
    "肉",
    "猪",
    "牛",
    "鸡",
    "鸭",
    "羊",
    "鱼",
    "虾",
    "蟹",
    "贝",
    "火腿",
    "培根",
    "香肠",
    "排骨",
    "炸猪排",
    "肉松",
    "海鲜",
)


def _curio_hash_mod(seed_text: str, mod: int) -> int:
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % mod


def is_coastal_low_tide_open(world_theme: str, day_index: int, hour: int) -> bool:
    """Two-hour rotating low-tide reef access window on coastal towns only."""

    if str(world_theme or "").strip() != "coastal_town":
        return False
    peak_hour = 16 + (int(day_index) % 3)
    return peak_hour <= int(hour) < (peak_hour + 2)


def preview_secondhand_or_reef_curio(
    world_seed: str,
    day_index: int,
    source_kind: str,
) -> dict[str, Any]:
    """Deterministically derive a unique provenance-tagged curio (0 disk writes)."""

    kind = str(source_kind or "").strip().lower()
    if kind not in ("reef", "bookstore", "secondhand"):
        raise V08MatterError(
            "INVALID_FIELDS",
            "source_kind must be one of ('reef', 'bookstore', 'secondhand')",
        )
    seed = str(world_seed or "shiomi").strip() or "shiomi"
    day = int(day_index)
    idx = _curio_hash_mod(f"{seed}:{kind}:{day}", 3)
    seed_tag = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]
    if kind == "reef":
        return {
            "object_id": f"curio_reef_{day}_{idx}_{seed_tag}",
            "label_cn": _CURIO_REEF_COLORS[idx],
            "item_kind": "keepsake",
            "paper_like": False,
            "provenance_tag": "退潮防波堤旧石阶海玻璃",
            "provenance_cn": f"第 {day} 天傍晚退潮时在防波堤旧石阶下捡到的海玻璃",
            "tucked_note_cn": "",
            "source_kind": "reef",
            "day_index": day,
            "paperweight_capable": True,
            "llm_calls": 0,
        }
    return {
        "object_id": f"curio_book_{day}_{idx}_{seed_tag}",
        "label_cn": "旧书店木箱淘来的二手画集",
        "item_kind": "paper",
        "paper_like": True,
        "provenance_tag": "旧书店木箱夹旧票根画集",
        "provenance_cn": f"第 {day} 天淘自街角旧书店角落木箱，夹着旧票根的二手画集",
        "tucked_note_cn": _CURIO_BOOK_NOTES[idx],
        "source_kind": "bookstore",
        "day_index": day,
        "paperweight_capable": False,
        "llm_calls": 0,
    }


@_write_locked
def collect_secondhand_or_reef_curio(
    day_root: Path | str,
    *,
    world_seed: str,
    day_index: int,
    source_kind: str,
    operation_key: str,
    target_parent_id: str = "home_staging_corner",
    world_theme: Optional[str] = None,
    hour: Optional[int] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Durably collect one unique provenance-tagged reef glass or bookstore curio."""

    op_key = _text(operation_key, min_len=1, max_len=120, field="operation_key")
    spec = preview_secondhand_or_reef_curio(world_seed, day_index, source_kind)
    if spec["source_kind"] == "reef" and world_theme is not None and hour is not None:
        if not is_coastal_low_tide_open(world_theme, day_index, hour):
            raise V08MatterError(
                "LOW_TIDE_WINDOW_CLOSED",
                "coastal low-tide reef steps are currently submerged",
                status_code=409,
            )

    now_dt, now_text = _resolve_now(now_iso)
    fingerprint = _fingerprint(
        "collect_secondhand_or_reef_curio",
        "subject",
        {
            "world_seed": str(world_seed),
            "day_index": int(day_index),
            "source_kind": spec["source_kind"],
            "target_parent_id": str(target_parent_id),
        },
    )
    state = _read_state_zero_write(day_root)
    replay = _check_replay(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        root=day_root,
        expected_revision=None,
    )
    if replay is not None:
        return replay

    oid = spec["object_id"]
    if oid in (state.get("matter_objects") or {}):
        raise V08MatterError(
            "CURIO_ALREADY_COLLECTED",
            f"unique curio {oid!r} has already been collected",
            status_code=409,
        )

    target_id = _validate_target_exists(state, target_parent_id, root=day_root)
    _validate_capacity(state, target_id)

    row = _object_row(
        oid,
        spec["label_cn"],
        item_kind=spec["item_kind"],
        parent_id=target_id,
        paper_like=bool(spec["paper_like"]),
    )
    row["provenance_tag"] = spec["provenance_tag"]
    row["provenance_cn"] = spec["provenance_cn"]
    row["tucked_note_cn"] = spec["tucked_note_cn"]
    row["curio_source_kind"] = spec["source_kind"]
    row["curio_day_index"] = int(day_index)
    row["paperweight_capable"] = bool(spec["paperweight_capable"])
    _refresh_subject_belief(row)
    _anchor_object_clocks_if_needed(state, row, now_text=now_text)
    state["matter_objects"][oid] = row

    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "collect_secondhand_or_reef_curio",
        "object_id": oid,
        "label_cn": spec["label_cn"],
        "provenance_tag": spec["provenance_tag"],
        "provenance_cn": spec["provenance_cn"],
        "tucked_note_cn": spec["tucked_note_cn"],
        "target_parent_id": target_id,
        "item": _object_projection(state, row, subject_view=True, now_dt=now_dt),
        "already_applied": False,
        "at": now_text,
        "llm_calls": 0,
    }
    _bump(state, now_text)
    _bump_subject_view(state)
    _store_operation(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        receipt=receipt,
        committed_at=now_text,
    )
    _atomic_write_state(day_root, state)
    return receipt


def is_habitual_slot_zero_write(root: Path | str, object_id: str) -> bool:
    """Pure zero-write check for whether ``object_id`` has earned a habitual slot."""

    state = _read_state_zero_write(root)
    obj = _object(state, object_id)
    if obj is None:
        return False
    return bool(obj.get("is_habitual_slot") or int(obj.get("consecutive_returns") or 0) >= 3)


def _ensure_home_entrance_zone(state: dict[str, Any]) -> str:
    zones = state.setdefault("zones", {})
    if "home_entrance" not in zones:
        zones["home_entrance"] = _zone_row(
            "home_entrance",
            "玄关",
            ambient_zone="room",
            water_source=False,
            capacity_slots=6,
        )
    return "home_entrance"


@_write_locked
def unpack_parcel_at_entrance(
    day_root: Path | str,
    parcel_id: Optional[str] = None,
    *,
    order_id: Optional[str] = None,
    item_label_cn: str = "网购包裹物件",
    item_kind: str = "everyday",
    target_parent_id: str = "home_entrance",
    cat_present: bool = True,
    delivered_to_entrance: bool = True,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Unpack an arrived entryway parcel; optionally let a cat claim the empty carton."""

    resolved_id = _text(parcel_id or order_id, min_len=1, max_len=80, field="parcel_id")
    if not delivered_to_entrance:
        raise V08MatterError(
            "PARCEL_STILL_IN_TRANSIT",
            f"parcel {resolved_id!r} has not arrived at entrance yet",
            status_code=409,
        )
    clean_label = _text(item_label_cn, min_len=1, max_len=80, field="item_label_cn")
    clean_kind = _choice(item_kind, ITEM_KINDS, field="item_kind", default="everyday")
    op_key = _optional_text(operation_key, max_len=120, field="operation_key")
    now_dt, now_text = _resolve_now(now_iso)

    state = _read_state_zero_write(day_root)
    fingerprint = _fingerprint(
        "unpack_parcel_at_entrance",
        "subject",
        {
            "parcel_id": resolved_id,
            "item_label_cn": clean_label,
            "item_kind": clean_kind,
            "target_parent_id": str(target_parent_id),
            "cat_present": bool(cat_present),
        },
    )
    replay = _check_replay(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        root=day_root,
        expected_revision=None,
    )
    if replay is not None:
        return replay

    entrance_zone = _ensure_home_entrance_zone(state)
    resolved_target = str(target_parent_id or entrance_zone).strip() or entrance_zone
    if resolved_target != entrance_zone:
        resolved_target = _validate_target_exists(state, resolved_target, root=day_root)

    carton_id = f"parcel_box_{resolved_id}"
    inner_id = f"parcel_item_{resolved_id}"

    existing_carton = state["matter_objects"].get(carton_id)
    if isinstance(existing_carton, Mapping) and existing_carton.get("unpacked"):
        raise V08MatterError(
            "PARCEL_ALREADY_UNPACKED",
            f"parcel {resolved_id!r} was already unpacked",
            status_code=409,
        )

    carton_row = _object_row(
        carton_id,
        f"{clean_label}空纸箱",
        item_kind="vessel",
        parent_id=entrance_zone,
        capacity_slots=2,
    )
    carton_row["unpacked"] = True
    carton_row["empty_box_occupied_by_cat"] = bool(cat_present)
    carton_row["provenance_tag"] = "玄关拆箱空纸箱"
    carton_row["provenance_cn"] = f"拆开 {clean_label} 包裹留下的空纸箱"
    _refresh_subject_belief(carton_row)
    _anchor_object_clocks_if_needed(state, carton_row, now_text=now_text)
    state["matter_objects"][carton_id] = carton_row

    inner_row = _object_row(
        inner_id,
        clean_label,
        item_kind=clean_kind,
        parent_id=resolved_target,
    )
    inner_row["unpacked_from_parcel_id"] = resolved_id
    inner_row["provenance_tag"] = "玄关隔日包裹"
    inner_row["provenance_cn"] = f"从玄关包裹 {resolved_id} 拆出的物件"
    _refresh_subject_belief(inner_row)
    _anchor_object_clocks_if_needed(state, inner_row, now_text=now_text)
    state["matter_objects"][inner_id] = inner_row

    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "unpack_parcel_at_entrance",
        "parcel_id": resolved_id,
        "carton_object_id": carton_id,
        "unpacked_object_id": inner_id,
        "unpacked": True,
        "cat_present": bool(cat_present),
        "empty_box_occupied_by_cat": bool(cat_present),
        "carton": _object_projection(state, carton_row, subject_view=True, now_dt=now_dt),
        "item": _object_projection(state, inner_row, subject_view=True, now_dt=now_dt),
        "already_applied": False,
        "at": now_text,
        "llm_calls": 0,
    }
    _bump(state, now_text)
    _bump_subject_view(state)
    _store_operation(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        receipt=receipt,
        committed_at=now_text,
    )
    _atomic_write_state(day_root, state)
    return receipt


def filter_meal_candidates_by_dietary_boundary(
    candidates: Sequence[Mapping[str, Any]],
    *,
    dietary_preferences: Optional[Mapping[str, Any]] = None,
    no_meat_eggs_only: bool = False,
    avoid_ingredients: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Filter meal candidates silently by dietary preferences without preachy text."""

    prefs = dietary_preferences if isinstance(dietary_preferences, Mapping) else {}
    restrict_meat = bool(
        no_meat_eggs_only
        or prefs.get("no_meat_eggs_only")
        or prefs.get("no_meat")
        or prefs.get("avoid_meat")
    )
    avoid_terms: list[str] = []
    for raw_source in (avoid_ingredients, prefs.get("avoid_ingredients")):
        if isinstance(raw_source, Sequence) and not isinstance(raw_source, (str, bytes)):
            for entry in raw_source:
                text = str(entry or "").strip().lower()
                if text and text not in avoid_terms:
                    avoid_terms.append(text)

    kept: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name_cn") or item.get("label_cn") or item.get("name") or "")
        ingredients_raw = item.get("ingredients")
        tags_raw = item.get("tags")
        corpus_parts = [name]
        for seq in (ingredients_raw, tags_raw):
            if isinstance(seq, Sequence) and not isinstance(seq, (str, bytes)):
                corpus_parts.extend(str(x or "") for x in seq)
        corpus = " ".join(corpus_parts).lower()

        explicit_meat = item.get("contains_meat")
        if restrict_meat:
            if explicit_meat is True:
                continue
            if explicit_meat is None and any(tok in corpus for tok in _MEAT_DIET_KEYWORDS):
                continue
        if avoid_terms and any(term in corpus for term in avoid_terms):
            continue
        kept.append(copy.deepcopy(dict(item)))
    return kept


def choose_evening_food(
    candidates: Sequence[Mapping[str, Any]],
    *,
    no_meat_eggs_only: bool = False,
    dietary_preferences: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Return surviving meal names after silent dietary boundary filtering."""

    filtered = filter_meal_candidates_by_dietary_boundary(
        candidates,
        dietary_preferences=dietary_preferences,
        no_meat_eggs_only=no_meat_eggs_only,
    )
    return [
        str(row.get("name_cn") or row.get("label_cn") or row.get("name") or "")
        for row in filtered
    ]


def get_dietary_preferences_zero_write(root: Path | str) -> dict[str, Any]:
    """Pure zero-write read of persisted dietary boundaries."""

    state = _read_state_zero_write(root)
    raw = state.get("dietary_preferences")
    return copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}


@_write_locked
def set_dietary_preferences(
    day_root: Path | str,
    preferences: Mapping[str, Any],
    *,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Persist dietary boundary preferences safely without moralizing."""

    prefs = _require_mapping(preferences)
    _assert_no_identity_keys(prefs)
    cleaned: dict[str, Any] = {
        "no_meat_eggs_only": bool(prefs.get("no_meat_eggs_only")),
        "avoid_ingredients": [
            str(x).strip()[:40]
            for x in (prefs.get("avoid_ingredients") or [])
            if isinstance(x, str) and x.strip()
        ][:32],
    }
    op_key = _optional_text(operation_key, max_len=120, field="operation_key")
    _, now_text = _resolve_now(now_iso)
    state = _read_state_zero_write(day_root)
    fingerprint = _fingerprint("set_dietary_preferences", "subject", cleaned)
    replay = _check_replay(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        root=day_root,
        expected_revision=None,
    )
    if replay is not None:
        return replay

    state["dietary_preferences"] = cleaned
    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "set_dietary_preferences",
        "dietary_preferences": copy.deepcopy(cleaned),
        "already_applied": False,
        "at": now_text,
        "llm_calls": 0,
    }
    _bump(state, now_text)
    _bump_subject_view(state)
    _store_operation(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        receipt=receipt,
        committed_at=now_text,
    )
    _atomic_write_state(day_root, state)
    return receipt


def perceive_neighbor_signal(
    *,
    raw_actor_name: str = "",
    sound_desc_cn: str = "",
    scent_desc_cn: str = "",
    insulation_level: int = 1,
    just_entered_place: bool = True,
    hop_distance: int = 1,
    strip_leaked_name: bool = False,
) -> dict[str, Any]:
    """1-hop sound/scent wall leakage guard (no omniscient actor name leaks)."""

    if not just_entered_place or int(hop_distance) > 1:
        return {"sound_cn": "", "scent_cn": "", "leaked_actor": "", "llm_calls": 0}

    level = int(insulation_level)
    sound_raw = str(sound_desc_cn or "")
    scent_raw = str(scent_desc_cn or "")

    sound_out = ""
    if level == 0:
        sound_out = sound_raw
    elif level in (1, 2):
        sound_out = f"隔着门窗隐约传来：{sound_raw}" if sound_raw else ""

    scent_out = scent_raw if level <= 1 else ""
    actor_name = str(raw_actor_name or "").strip()
    if actor_name and (actor_name in sound_out or actor_name in scent_out):
        if strip_leaked_name:
            sound_out = sound_out.replace(actor_name, "门外有人")
            scent_out = scent_out.replace(actor_name, "门外")
        else:
            raise V08MatterError(
                "ACTOR_IDENTITY_LEAK_THROUGH_WALL",
                "unseen actor name cannot leak across closed/cracked walls",
                status_code=400,
            )
    return {"sound_cn": sound_out, "scent_cn": scent_out, "leaked_actor": "", "llm_calls": 0}


@dataclass
class CorkboardWall:
    """Reversible corkboard wall capped at 6 pins, returning evicted cards to album."""

    max_pins: int = 6
    pinned_cards: list[str] = field(default_factory=list)
    album_returned: list[str] = field(default_factory=list)

    def pin_card(self, card_id: str) -> dict[str, Any]:
        cid = str(card_id or "").strip()
        if not cid:
            raise V08MatterError("INVALID_FIELDS", "card_id must be non-empty")
        if cid in self.pinned_cards:
            return {
                "pinned": list(self.pinned_cards),
                "returned_to_album": list(self.album_returned),
                "llm_calls": 0,
            }
        while len(self.pinned_cards) >= max(1, int(self.max_pins)):
            evicted = self.pinned_cards.pop(0)
            if evicted not in self.album_returned:
                self.album_returned.append(evicted)
        self.pinned_cards.append(cid)
        return {
            "pinned": list(self.pinned_cards),
            "returned_to_album": list(self.album_returned),
            "llm_calls": 0,
        }

    def unpin_card(self, card_id: str) -> dict[str, Any]:
        cid = str(card_id or "").strip()
        if cid in self.pinned_cards:
            self.pinned_cards.remove(cid)
            if cid not in self.album_returned:
                self.album_returned.append(cid)
        return {
            "pinned": list(self.pinned_cards),
            "returned_to_album": list(self.album_returned),
            "llm_calls": 0,
        }


def read_corkboard_wall_zero_write(root: Path | str) -> dict[str, Any]:
    """Pure zero-write snapshot of the room's reversible corkboard wall."""

    state = _read_state_zero_write(root)
    raw = state.get("corkboard_wall")
    if not isinstance(raw, Mapping):
        return {
            "max_pins": 6,
            "pinned_cards": [],
            "album_returned": [],
            "llm_calls": 0,
        }
    return {
        "max_pins": int(raw.get("max_pins") or 6),
        "pinned_cards": [str(x) for x in (raw.get("pinned_cards") or [])],
        "album_returned": [str(x) for x in (raw.get("album_returned") or [])],
        "llm_calls": 0,
    }


@_write_locked
def pin_corkboard_card(
    day_root: Path | str,
    card_id: str,
    *,
    max_pins: int = 6,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Pin one card onto the corkboard wall; oldest pin returns harmlessly to album."""

    cid = _text(card_id, min_len=1, max_len=120, field="card_id")
    cap = _int_field(max_pins, field="max_pins", minimum=1, maximum=12)
    op_key = _optional_text(operation_key, max_len=120, field="operation_key")
    _, now_text = _resolve_now(now_iso)

    state = _read_state_zero_write(day_root)
    fingerprint = _fingerprint("pin_corkboard_card", "subject", {"card_id": cid, "max_pins": cap})
    replay = _check_replay(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        root=day_root,
        expected_revision=None,
    )
    if replay is not None:
        return replay

    raw_board = state.get("corkboard_wall") if isinstance(state.get("corkboard_wall"), Mapping) else {}
    board = CorkboardWall(
        max_pins=cap,
        pinned_cards=[str(x) for x in (raw_board.get("pinned_cards") or [])],
        album_returned=[str(x) for x in (raw_board.get("album_returned") or [])],
    )
    res = board.pin_card(cid)
    state["corkboard_wall"] = {
        "max_pins": board.max_pins,
        "pinned_cards": list(board.pinned_cards),
        "album_returned": list(board.album_returned),
    }
    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "pin_corkboard_card",
        "card_id": cid,
        "max_pins": board.max_pins,
        "pinned": res["pinned"],
        "returned_to_album": res["returned_to_album"],
        "already_applied": False,
        "at": now_text,
        "llm_calls": 0,
    }
    _bump(state, now_text)
    _bump_subject_view(state)
    _store_operation(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        receipt=receipt,
        committed_at=now_text,
    )
    _atomic_write_state(day_root, state)
    return receipt


@_write_locked
def unpin_corkboard_card(
    day_root: Path | str,
    card_id: str,
    *,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Unpin one card from the corkboard wall and return it to album cleanly."""

    cid = _text(card_id, min_len=1, max_len=120, field="card_id")
    op_key = _optional_text(operation_key, max_len=120, field="operation_key")
    _, now_text = _resolve_now(now_iso)

    state = _read_state_zero_write(day_root)
    fingerprint = _fingerprint("unpin_corkboard_card", "subject", {"card_id": cid})
    replay = _check_replay(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        root=day_root,
        expected_revision=None,
    )
    if replay is not None:
        return replay

    raw_board = state.get("corkboard_wall") if isinstance(state.get("corkboard_wall"), Mapping) else {}
    board = CorkboardWall(
        max_pins=int(raw_board.get("max_pins") or 6),
        pinned_cards=[str(x) for x in (raw_board.get("pinned_cards") or [])],
        album_returned=[str(x) for x in (raw_board.get("album_returned") or [])],
    )
    res = board.unpin_card(cid)
    state["corkboard_wall"] = {
        "max_pins": board.max_pins,
        "pinned_cards": list(board.pinned_cards),
        "album_returned": list(board.album_returned),
    }
    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "unpin_corkboard_card",
        "card_id": cid,
        "pinned": res["pinned"],
        "returned_to_album": res["returned_to_album"],
        "already_applied": False,
        "at": now_text,
        "llm_calls": 0,
    }
    _bump(state, now_text)
    _bump_subject_view(state)
    _store_operation(
        state,
        operation_key=op_key,
        fingerprint=fingerprint,
        receipt=receipt,
        committed_at=now_text,
    )
    _atomic_write_state(day_root, state)
    return receipt


def negotiate_shared_lamp_move(
    *,
    requested_zone: str,
    subject_using_lamp_at: Optional[str] = None,
) -> dict[str, Any]:
    """Offer a middle-ground spot instead of a blunt 409 when lamp is currently in use."""

    req = str(requested_zone or "").strip()
    using = str(subject_using_lamp_at or "").strip() if subject_using_lamp_at else ""
    if using and req != using:
        return {
            "outcome": "COUNTER_COMPROMISE",
            "suggested_zone": f"between_{using}_and_{req}",
            "reply_cn": "这会儿书桌这边还借着它的光呢，要不先摆在两处中间？两边都能照着。",
            "llm_calls": 0,
        }
    return {
        "outcome": "APPLIED",
        "suggested_zone": req,
        "reply_cn": "好呀，挪过去摆稳了。",
        "llm_calls": 0,
    }


__all__ = [
    "AMBIENT_ZONES",
    "ASSEMBLY_ROLES",
    "CLEANLINESS_STATES",
    "CorkboardWall",
    "FIT_STATUSES",
    "WEAR_STATES",
    "COOK_STATES",
    "FRESHNESS_STATES",
    "ITEM_KINDS",
    "MAX_OPERATION_RECEIPTS",
    "MAX_RECENT_ACTS",
    "MAX_CARRIER_CHAIN_DEPTH",
    "MAX_SCALDING_MINUTES",
    "MAX_NICKNAME_CHARS",
    "MOISTURE_STATES",
    "OPEN_PRIMITIVES",
    "OUTING_CARRIER_DEFAULTS",
    "SCHEMA_VERSION",
    "STATE_DIRNAME",
    "STATE_FILENAME",
    "THERMAL_STATES",
    "V08MatterError",
    "VIEW_SCHEMA",
    "VISIBILITIES",
    "choose_evening_food",
    "collect_secondhand_or_reef_curio",
    "compose_open_act",
    "derived_role_cn",
    "filter_meal_candidates_by_dietary_boundary",
    "get_dietary_preferences_zero_write",
    "inspect_absence_epistemics_zero_write",
    "is_coastal_low_tide_open",
    "is_habitual_slot_zero_write",
    "name_personal_corner",
    "negotiate_shared_lamp_move",
    "note_legibility",
    "partner_bring_item",
    "partner_move_shared_object",
    "partner_propose_object_use",
    "perceive_neighbor_signal",
    "pin_corkboard_card",
    "preview_placement_zero_write",
    "preview_secondhand_or_reef_curio",
    "project_familiar_absence_cues_zero_write",
    "project_workspace_scaffolds_zero_write",
    "read_corkboard_wall_zero_write",
    "read_matter_space_view",
    "request_loan_extension",
    "respond_proposed_use",
    "scan_matter_opportunities_zero_write",
    "set_dietary_preferences",
    "settle_matter_physics",
    "state_path",
    "unpack_parcel_at_entrance",
    "unpin_corkboard_card",
]
