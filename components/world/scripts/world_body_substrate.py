#!/usr/bin/env python3
"""Bounded World-side Embodiment Substrate (Body / Embodiment V1 prerequisite).

This module is the **World authority** for physical placement plus the minimum
body substrate that ``Body / Embodiment V1`` needs to exist inside the world:

* exactly one physical placement per object instance (placement authority);
* body entity ``pose`` / ``orientation``;
* body physical resource slots (hands) with **derived** occupancy;
* exclusive body-resource reservations for in-flight actions;
* reachability inputs derived from pose + free slots + placement location.
* consumption receipts: when one instance is consumed, its live placement and a
  receipt (execution / object / body / proposal fingerprint / before_revision /
  consumed_at) are written by **one** state change and therefore one atomic
  save.  The receipt is the attribution; a missing placement never is.

Design constraints (frozen by ``B0B_CONTRACT_FREEZE.md`` / this ticket):

* **Additive only.**  It does not modify ``world_state.json``,
  ``world_property_economy.json``, ``world_timeline.json``, location
  reachability, travel, ownership, the Life executor, or the legacy HeartMind
  state.  Its own state lives in ``data/world/body_substrate.json``.
* **Importing has no runtime effect.**  A caller must inject ``hermes_home``
  and explicitly call a function.  There is no scheduler, no tick, no daemon,
  no gateway hook and no LLM call.
* **No second truth.**  There is no ``inventory`` list and no stored
  ``left_hand``/``right_hand`` value.  Hand occupancy is *always* derived by
  scanning the placement map, so a hand query can never diverge from the
  placement it came from.
* **Deterministic.**  No randomness, no wall-clock dependency in any
  computation; ``at`` timestamps are injected by the caller.

Ownership split
---------------
===========================  ==========================================
World owns                   placement, pose, orientation, slots,
                             reservations  (this module)
Embodiment owns              BodyProfile, somatic runtime, BodySignal,
                             recovery      (not this module)
===========================  ==========================================
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = "world.body.substrate.v1.3"
#: v1 had no body entity binding; v1.1 had no consumption receipts; v1.2 had no
#: fixture (light/fixture power) block.  All are readable and upgraded in
#: :func:`migrate_substrate` -- the placement structure itself is never rewritten
#: by any step.  v1.3 is the smallest compatible extension: one optional
#: ``fixtures`` map, empty meaning "power UNKNOWN" (never "off").
LEGACY_SCHEMA_VERSIONS = (
    "world.body.substrate.v1",
    "world.body.substrate.v1.1",
    "world.body.substrate.v1.2",
)
KIND = "world_body_substrate"
WORLD_ENTITY_KIND = "body"
FEATURE_ENV = "CHIYO_WORLD_BODY_SUBSTRATE_ENABLED"
DEFAULT_WORLD_ID = "shiomi_city"
DEFAULT_BODY_ID = "chiyo_body"
STATE_RELATIVE_PATH = ("data", "world", "body_substrate.json")

MAX_ID_LENGTH = 128
MAX_PLACEMENTS = 512
MAX_RESERVATIONS = 32
#: Bounded retention for consumption receipts (the object lifecycle record).
#: Reaching the bound fails a *new* consumption closed; an existing receipt is
#: never pruned to make room, because that would silently drop replay
#: protection for an execution that already happened.
MAX_CONSUMPTIONS = 256

#: M15: canonical surface declarations.  A surface exists only when the world
#: says so AND the object that owns it is present; the list is seeded with the
#: single surface the M13A.1 bootstrap already declares.  No surface is invented
#: here, and `surface_verdict` never assumes one.
#: M15: capabilities declared for objects that are NOT economy items (the
#: purpose-built smoke/fixture objects).  Production objects get their capability
#: from their catalog entry; this registry exists so a fixture never needs the
#: economy to be forged, and it is empty in production until a smoke object is
#: declared here.  It grants nothing by itself.
DECLARED_CAPABILITIES: dict[str, dict[str, Any]] = {}

#: v0.4 room-living: **surface kinds** (not one global surface).  A surface kind
#: names the catalog item that must really exist for the surface to be real, and
#: what the surface can physically support.  A surface *instance* is addressed as
#: ``<kind>:<owner item_instance_id>`` and is only real while that instance has a
#: live placement in the same place -- two desks therefore never share one
#: ``desk_surface`` and things put on one desk can never appear on the other.
#:
#: ``bed_surface`` additionally keeps its legacy bare spelling: the M13A.1
#: bootstrap declared that id before instances existed, and existing live worlds
#: must keep working without a reset.  ``desk_surface`` / ``chair_surface`` are
#: deliberately **instance-only** (a bare id is not declared), which is what
#: removes the global-surface collision at the source.
SURFACE_KINDS: dict[str, dict[str, Any]] = {
    "bed_surface": {
        "surface_id": "bed_surface",
        "owner_catalog_item_id": "basic_bed",
        "label": "bed",
        "pose_condition": "lying",
        "rest_modes": ("rest", "sleep_like"),
        "legacy_bare": True,
    },
    "desk_surface": {
        "surface_id": "desk_surface",
        "owner_catalog_item_id": "simple_desk",
        "label": "desk",
        "pose_condition": None,
        "rest_modes": (),
        "legacy_bare": False,
    },
    "chair_surface": {
        "surface_id": "chair_surface",
        "owner_catalog_item_id": "simple_chair",
        "label": "chair",
        "pose_condition": "seated",
        "rest_modes": ("rest",),
        "legacy_bare": False,
    },
}

#: Backwards-compatible view (older code iterates the keys / reads
#: ``owner_catalog_item_id``).  It is the same declaration, not a second copy.
SURFACES: dict[str, dict[str, Any]] = SURFACE_KINDS

#: Fixture power records (v1.3).  Bounded like every other block; an absent entry
#: means the power is **UNKNOWN**, never "off".
MAX_FIXTURES = 64
FIXTURE_POWERS = ("on", "off")
FIXTURE_POWER_ON = "on"
FIXTURE_POWER_OFF = "off"

_FIXTURE_KEYS = frozenset({"power", "since", "declared_source", "revision"})
HAND_SLOTS = ("left_hand", "right_hand")
POSES = ("standing", "seated", "lying", "transitioning")
ORIENTATIONS = ("north", "south", "east", "west", "up", "down")

PLACEMENT_ROOM = "room"
PLACEMENT_SURFACE = "surface"
PLACEMENT_CONTAINER = "container"
PLACEMENT_HAND = "hand"
PLACEMENT_IN_TRANSIT = "in_transit"
PLACEMENT_KINDS = (
    PLACEMENT_ROOM,
    PLACEMENT_SURFACE,
    PLACEMENT_CONTAINER,
    PLACEMENT_HAND,
    PLACEMENT_IN_TRANSIT,
)

_STATE_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "revision",
        "world_id",
        "body_id",
        "world_entity_id",
        "pose",
        "orientation",
        "placements",
        "reservations",
        "consumptions",
        "fixtures",
    }
)

# Reachability is a property of the pose, not of the object.
_HANDS_USABLE_POSES = frozenset({"standing", "seated"})


class BodySubstrateError(ValueError):
    """The substrate contract was violated by the caller."""


# --------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------


def _require_text(value: Any, field: str, *, maximum: int = MAX_ID_LENGTH) -> str:
    if not isinstance(value, str):
        raise BodySubstrateError(f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > maximum:
        raise BodySubstrateError(f"{field} must be non-empty and bounded")
    return text


def _require_revision(value: Any, field: str = "revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BodySubstrateError(f"{field} must be a non-negative integer")
    return value


def _require_choice(value: Any, field: str, allowed: tuple[str, ...]) -> str:
    text = _require_text(value, field, maximum=32)
    if text not in allowed:
        raise BodySubstrateError(f"{field} must be one of {sorted(allowed)}")
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_copy(value: Any) -> Any:
    """Return a detached, canonical, bounded JSON copy."""

    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise BodySubstrateError("value is not bounded JSON") from exc


def validate_placement_target(target: Any) -> dict[str, Any]:
    """Validate and normalize a single exclusive placement target."""

    if not isinstance(target, Mapping):
        raise BodySubstrateError("placement target must be an object")
    kind = _require_choice(target.get("kind"), "placement.kind", PLACEMENT_KINDS)

    if kind == PLACEMENT_HAND:
        if set(target) != {"kind", "slot_id"}:
            raise BodySubstrateError("hand placement must have exactly kind/slot_id")
        return {
            "kind": kind,
            "slot_id": _require_choice(target["slot_id"], "placement.slot_id", HAND_SLOTS),
        }

    if kind == PLACEMENT_IN_TRANSIT:
        if set(target) != {"kind", "from_location_id", "to_location_id"}:
            raise BodySubstrateError(
                "in_transit placement must have exactly kind/from/to"
            )
        return {
            "kind": kind,
            "from_location_id": _require_text(
                target["from_location_id"], "placement.from_location_id"
            ),
            "to_location_id": _require_text(
                target["to_location_id"], "placement.to_location_id"
            ),
        }

    expected = {"kind", "location_id", "area_id"} if kind == PLACEMENT_ROOM else {
        "kind",
        "location_id",
        f"{kind}_id",
    }
    if set(target) != expected:
        raise BodySubstrateError(f"{kind} placement must have exactly {sorted(expected)}")
    location_id = _require_text(target["location_id"], "placement.location_id")
    if kind == PLACEMENT_ROOM:
        return {
            "kind": kind,
            "location_id": location_id,
            "area_id": _require_text(target["area_id"], "placement.area_id"),
        }
    detail_field = f"{kind}_id"
    return {
        "kind": kind,
        "location_id": location_id,
        detail_field: _require_text(target[detail_field], f"placement.{detail_field}"),
    }


def validate_consumption_receipt(record: Any) -> dict[str, Any]:
    """Validate one World-owned consumption receipt (object lifecycle record).

    The receipt is the *only* thing that proves an execution consumed an
    instance: the placement it removed is gone by design, so absence can never
    be attribution.  Field vocabulary lives in ``world_consumable_contract``
    (one definition, no second copy); this function only enforces it.
    """

    import world_consumable_contract as contract

    if not isinstance(record, Mapping) or set(record) != contract.RECEIPT_KEYS:
        raise BodySubstrateError(
            "consumption receipt must have exactly the receipt fields"
        )
    if record.get("kind") != contract.KIND_RECEIPT:
        raise BodySubstrateError("consumption receipt kind is not supported")
    if record.get("schema_version") != contract.SCHEMA_RECEIPT:
        raise BodySubstrateError("consumption receipt schema is not supported")
    placement_before = record.get("placement_before")
    if placement_before is not None:
        placement_before = validate_placement_target(placement_before)
    return {
        "kind": contract.KIND_RECEIPT,
        "schema_version": contract.SCHEMA_RECEIPT,
        "receipt_id": _require_text(record["receipt_id"], "receipt.receipt_id"),
        "execution_id": _require_text(
            record["execution_id"], "receipt.execution_id", maximum=256
        ),
        "object_id": _require_text(record["object_id"], "receipt.object_id"),
        "body_id": _require_text(record["body_id"], "receipt.body_id"),
        "actor_id": _require_text(record["actor_id"], "receipt.actor_id"),
        "proposal_fingerprint": _require_text(
            record["proposal_fingerprint"], "receipt.proposal_fingerprint"
        ),
        "before_revision": _require_revision(
            record["before_revision"], "receipt.before_revision"
        ),
        "consumed_at": _require_text(record["consumed_at"], "receipt.consumed_at"),
        "source": _require_choice(record["source"], "receipt.source", contract.SOURCES),
        "placement_before": placement_before,
    }


def validate_substrate(state: Any) -> dict[str, Any]:
    """Validate and return a detached, normalized substrate state."""

    if not isinstance(state, Mapping) or set(state) != _STATE_KEYS:
        raise BodySubstrateError("substrate state must have exactly the V1.2 fields")

    if state["schema_version"] != SCHEMA_VERSION:
        raise BodySubstrateError("substrate schema_version is not supported")
    if state["kind"] != KIND:
        raise BodySubstrateError("substrate kind is not supported")

    placements_raw = state["placements"]
    if not isinstance(placements_raw, Mapping) or len(placements_raw) > MAX_PLACEMENTS:
        raise BodySubstrateError("placements must be a bounded object")

    placements: dict[str, Any] = {}
    slot_owner: dict[str, str] = {}
    for object_id, target in placements_raw.items():
        name = _require_text(object_id, "placements key")
        normalized = validate_placement_target(target)
        if normalized["kind"] == PLACEMENT_HAND:
            slot_id = normalized["slot_id"]
            if slot_id in slot_owner:
                raise BodySubstrateError(
                    f"exclusive slot {slot_id} holds two objects: "
                    f"{slot_owner[slot_id]} and {name}"
                )
            slot_owner[slot_id] = name
        placements[name] = normalized

    reservations_raw = state["reservations"]
    if not isinstance(reservations_raw, Mapping) or len(reservations_raw) > MAX_RESERVATIONS:
        raise BodySubstrateError("reservations must be a bounded object")

    reservations: dict[str, Any] = {}
    for slot_id, record in reservations_raw.items():
        slot = _require_choice(slot_id, "reservations key", HAND_SLOTS)
        if not isinstance(record, Mapping) or set(record) != {"execution_id", "reserved_at"}:
            raise BodySubstrateError(
                "reservation must have exactly execution_id/reserved_at"
            )
        reservations[slot] = {
            "execution_id": _require_text(record["execution_id"], "reservation.execution_id"),
            "reserved_at": _require_text(record["reserved_at"], "reservation.reserved_at"),
        }

    consumptions_raw = state["consumptions"]
    if not isinstance(consumptions_raw, Mapping) or len(consumptions_raw) > MAX_CONSUMPTIONS:
        raise BodySubstrateError("consumptions must be a bounded object")

    consumptions: dict[str, Any] = {}
    for execution_id, receipt in consumptions_raw.items():
        key = _require_text(execution_id, "consumptions key", maximum=256)
        normalized_receipt = validate_consumption_receipt(receipt)
        if normalized_receipt["execution_id"] != key:
            raise BodySubstrateError(
                "consumption receipt is filed under a different execution_id"
            )
        consumptions[key] = normalized_receipt
    if len({r["receipt_id"] for r in consumptions.values()}) != len(consumptions):
        raise BodySubstrateError("consumption receipts must have distinct receipt ids")

    fixtures_raw = state["fixtures"]
    if not isinstance(fixtures_raw, Mapping) or len(fixtures_raw) > MAX_FIXTURES:
        raise BodySubstrateError("fixtures must be a bounded object")

    fixtures: dict[str, Any] = {}
    for object_id, record in fixtures_raw.items():
        name = _require_text(object_id, "fixtures key")
        if not isinstance(record, Mapping) or set(record) != _FIXTURE_KEYS:
            raise BodySubstrateError(
                "fixture record must have exactly power/since/declared_source/revision"
            )
        fixtures[name] = {
            "power": _require_choice(record["power"], "fixture.power", FIXTURE_POWERS),
            "since": _require_text(record["since"], "fixture.since", maximum=64),
            "declared_source": _require_text(
                record["declared_source"], "fixture.declared_source", maximum=128
            ),
            "revision": _require_revision(record["revision"], "fixture.revision"),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "revision": _require_revision(state["revision"]),
        "world_id": _require_text(state["world_id"], "world_id"),
        "body_id": _require_text(state["body_id"], "body_id"),
        "world_entity_id": _require_text(state["world_entity_id"], "world_entity_id"),
        "pose": _require_choice(state["pose"], "pose", POSES),
        "orientation": _require_choice(state["orientation"], "orientation", ORIENTATIONS),
        "placements": placements,
        "reservations": reservations,
        "consumptions": consumptions,
        "fixtures": fixtures,
    }


# --------------------------------------------------------------------------
# persistence (same discipline as the existing World stores)
# --------------------------------------------------------------------------


def substrate_path(hermes_home: Optional[Path | str] = None) -> Path:
    home = (
        Path(hermes_home).expanduser()
        if hermes_home is not None
        else Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    )
    return home.joinpath(*STATE_RELATIVE_PATH)


def feature_enabled(environment: Optional[Mapping[str, str]] = None) -> bool:
    raw = (environment or os.environ).get(FEATURE_ENV)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"} if raw else False


def migrate_substrate(raw: Any) -> tuple[dict[str, Any], bool]:
    """Upgrade an older substrate representation to the current schema.

    Returns ``(state, migrated)``.  This is the *only* sanctioned upgrade path:
    a future ``v1.3 -> v2`` step is added here rather than overwriting JSON, so
    ``body_id``, the world binding, placements and reservations all survive.

    v1 -> v1.2: the body entity binding is introduced (derived from ``body_id``;
    nothing recomputed) and the consumption receipt block defaults to empty.
    v1.1 -> v1.2: only the receipt block is added, again defaulting to empty, so
    an existing placement map is carried over byte-for-byte in meaning.
    v1.2 -> v1.3: only the ``fixtures`` block is added, empty.  An empty block
    means every fixture's power is **UNKNOWN** (not "off"), so an upgraded home
    reports "no light state known" until a real toggle command writes one.
    Unknown schemas fail closed.
    """

    if not isinstance(raw, Mapping):
        raise BodySubstrateError("substrate state must be an object")
    version = raw.get("schema_version")
    if version == SCHEMA_VERSION:
        return validate_substrate(raw), False
    if version not in LEGACY_SCHEMA_VERSIONS:
        raise BodySubstrateError(f"substrate schema_version {version!r} is not supported")

    upgraded = {
        "schema_version": SCHEMA_VERSION,
        "kind": raw.get("kind"),
        "revision": raw.get("revision"),
        "world_id": raw.get("world_id"),
        "body_id": raw.get("body_id"),
        "world_entity_id": raw.get("world_entity_id") or raw.get("body_id"),
        "pose": raw.get("pose"),
        "orientation": raw.get("orientation"),
        "placements": raw.get("placements") or {},
        "reservations": raw.get("reservations") or {},
        #: v1.2 addition.  An older home simply has no receipts yet; nothing is
        #: invented for it and no placement is touched.
        "consumptions": raw.get("consumptions") or {},
        #: v1.3 addition: no fixture power is invented on upgrade.
        "fixtures": raw.get("fixtures") or {},
    }
    return validate_substrate(upgraded), True


def verify_binding(
    state: Mapping[str, Any], world_state: Optional[Mapping[str, Any]]
) -> dict[str, Any]:
    """Prove the body entity is bound to the World it claims.  Fails closed."""

    normalized = validate_substrate(state)
    reasons: list[str] = []

    if not isinstance(world_state, Mapping):
        return {
            "bound": False,
            "body_id": normalized["body_id"],
            "world_entity_id": normalized["world_entity_id"],
            "world_id": normalized["world_id"],
            "reasons": ["world_state_unavailable"],
        }

    world_id = world_state.get("world_id")
    if world_id != normalized["world_id"]:
        reasons.append(
            f"world_id mismatch: substrate={normalized['world_id']!r} world={world_id!r}"
        )
    if normalized["world_entity_id"] != normalized["body_id"]:
        reasons.append("world_entity_id does not match body_id")
    if world_state.get("schema_version") is None:
        reasons.append("world_state has no schema_version")

    return {
        "bound": not reasons,
        "body_id": normalized["body_id"],
        "world_entity_id": normalized["world_entity_id"],
        "world_id": normalized["world_id"],
        "world_revision": world_state.get("revision"),
        "body_revision": normalized["revision"],
        "reasons": reasons,
    }


def initial_substrate(
    *, world_id: str = DEFAULT_WORLD_ID, body_id: str = DEFAULT_BODY_ID
) -> dict[str, Any]:
    """Deterministic empty substrate.  No randomness, no time input."""

    resolved_body = _require_text(body_id, "body_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "revision": 0,
        "world_id": _require_text(world_id, "world_id"),
        "body_id": resolved_body,
        "world_entity_id": resolved_body,
        "pose": "standing",
        "orientation": "south",
        "placements": {},
        "reservations": {},
        "consumptions": {},
        "fixtures": {},
    }


class SubstrateStore:
    """Atomic, lock-protected store for the embodiment substrate."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> Optional[dict[str, Any]]:
        state, _migrated = self.load_with_migration()
        return state

    def load_with_migration(self) -> tuple[Optional[dict[str, Any]], bool]:
        """Load and transparently upgrade an older representation.

        Read-only: the file on disk is never rewritten by a load.
        """

        if not self.path.exists():
            return None, False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BodySubstrateError("substrate state is not readable JSON") from exc
        return migrate_substrate(raw)

    def save(
        self, state: Mapping[str, Any], *, expected_revision: Optional[int] = None
    ) -> dict[str, Any]:
        """Atomically persist ``state``.

        ``expected_revision`` is a **real** optimistic-concurrency check: a
        caller that observed revision N must pass ``N`` and the write fails
        closed if the live file has moved on.  Unlike the legacy World store it
        is not silently skipped -- it is the documented write path.
        """
        # The lock file lives next to the state file, so the directory must
        # exist before the lock is taken (otherwise the very first save fails).
        self.path.parent.mkdir(parents=True, exist_ok=True)
        normalized = validate_substrate(state)
        with _locked(self.path):
            if expected_revision is not None:
                expected = _require_revision(expected_revision, "expected_revision")
                current = self.load()
                live = current["revision"] if current is not None else None
                if live != expected:
                    raise BodySubstrateError(
                        f"substrate revision mismatch: expected {expected}, live {live}"
                    )
            payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            descriptor, temporary = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except BaseException:
                if os.path.exists(temporary):
                    os.unlink(temporary)
                raise
        return normalized


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    handle = open(str(path) + ".lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class StaleSubstrateError(BodySubstrateError):
    """The caller acted on a substrate revision that is no longer current."""


# --------------------------------------------------------------------------
# pure state transitions (no I/O, no time unless injected)
# --------------------------------------------------------------------------


def _bump(state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_substrate(state)
    normalized["revision"] = normalized["revision"] + 1
    return normalized


def declare_capability(object_id: Any, capability: Mapping[str, Any]) -> None:
    """Declare the capability of a non-economy object (fixtures, smoke object)."""

    name = _require_text(object_id, "object_id")
    block = dict(capability or {})
    DECLARED_CAPABILITIES[name] = block


#: M15: the world-owned capability declaration file (non-economy objects).
CAPABILITIES_RELATIVE = ("state", "world_object_capabilities.json")
CAPABILITIES_SCHEMA = "world.object.capabilities.v1"


def capabilities_path(hermes_home: Optional[Path | str] = None) -> Path:
    home = (
        Path(hermes_home).expanduser()
        if hermes_home is not None
        else Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    )
    return home.joinpath(*CAPABILITIES_RELATIVE)


def load_declared_capabilities(hermes_home: Optional[Path | str] = None) -> dict[str, dict[str, Any]]:
    """Read the world's capability declarations.  Fail-closed on any problem."""

    path = capabilities_path(hermes_home)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, Mapping) or raw.get("schema_version") != CAPABILITIES_SCHEMA:
        return {}
    objects = raw.get("objects")
    if not isinstance(objects, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for object_id, capability in objects.items():
        if isinstance(capability, Mapping):
            out[str(object_id)] = dict(capability)
    return out


def declared_capability(object_id: Any, hermes_home: Optional[Path | str] = None) -> dict[str, Any]:
    """The declared capability of a non-economy object ({} when none).

    Order: the world-owned declaration file first, then the in-process registry
    (tests/fixtures).  Both are declarations, never assumptions.
    """

    name = str(object_id)
    persisted = load_declared_capabilities(hermes_home).get(name)
    if isinstance(persisted, Mapping):
        return dict(persisted)
    entry = DECLARED_CAPABILITIES.get(name)
    return dict(entry) if isinstance(entry, Mapping) else {}


def surface_kind_of(surface_id: Any) -> Optional[str]:
    """The declared *kind* of a surface id, or ``None``.

    ``"desk_surface:item-abc"`` -> ``"desk_surface"``; ``"bed_surface"`` ->
    ``"bed_surface"``; anything else -> ``None``.  The kind is the only thing the
    world declares; *which* piece of furniture it belongs to is the instance
    suffix, and that is what stops two desks from sharing one surface.
    """

    text = str(surface_id or "")
    kind = text.split(":", 1)[0]
    return kind if kind in SURFACE_KINDS else None


def surface_owner_instance(surface_id: Any) -> Optional[str]:
    """The owner item instance a surface id is bound to (``None`` when bare)."""

    text = str(surface_id or "")
    if ":" not in text:
        return None
    owner = text.split(":", 1)[1].strip()
    return owner or None


def surface_instance_id(surface_kind: Any, object_id: Any) -> str:
    """The instance-scoped surface id of one piece of furniture."""

    kind = _require_text(surface_kind, "surface_kind", maximum=32)
    if kind not in SURFACE_KINDS:
        raise BodySubstrateError(f"surface kind {kind!r} is not declared")
    return "%s:%s" % (kind, _require_text(object_id, "object_id"))


def surface_declared(surface_id: Any) -> bool:
    """Is this surface id a declared surface of the world?

    A bare id is only legal for a kind that predates instances (``bed_surface``);
    ``desk_surface`` / ``chair_surface`` exist **only** as instance-scoped ids,
    so a global id can never bind every desk in the world to one surface.
    """

    kind = surface_kind_of(surface_id)
    if kind is None:
        return False
    if surface_owner_instance(surface_id) is None:
        return bool(SURFACE_KINDS[kind].get("legacy_bare"))
    return True


def surface_owner_catalog(surface_id: Any) -> Optional[str]:
    """The catalog item that must exist for the surface to be real."""

    kind = surface_kind_of(surface_id)
    return SURFACE_KINDS[kind]["owner_catalog_item_id"] if kind else None


def surface_declaration(surface_id: Any) -> dict[str, Any]:
    """The full declaration of a surface kind (``{}`` when undeclared)."""

    kind = surface_kind_of(surface_id)
    return dict(SURFACE_KINDS[kind]) if kind else {}


def derived_surfaces(
    placements: Mapping[str, Any],
    catalog_by_instance: Mapping[str, str],
    *,
    location_id: Optional[str] = None,
    area_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """The surfaces that are *really* here, derived from live placements.

    A surface instance exists only when its owner furniture instance
    (1) is a real economy instance of the declared catalog item and
    (2) has a live placement of its own in this place/area.  Nothing is stored:
    move the desk and its surface moves with it; there is no second registry to
    drift, and no global surface id to collide.

    Children (placements whose ``surface_id`` is this surface) are listed on the
    surface, which is what lets a furniture move carry them explicitly.
    """

    if location_id is None:
        return []
    surfaces: list[dict[str, Any]] = []
    for object_id in sorted(placements):
        target = placements[object_id] or {}
        catalog_item = catalog_by_instance.get(str(object_id))
        if not catalog_item:
            continue
        for kind, declaration in sorted(SURFACE_KINDS.items()):
            if declaration["owner_catalog_item_id"] != catalog_item:
                continue
            if str(target.get("location_id")) != str(location_id):
                continue
            if area_id is not None and target.get("kind") == PLACEMENT_ROOM:
                if str(target.get("area_id") or "") != str(area_id):
                    continue
            surface_id = surface_instance_id(kind, object_id)
            surfaces.append(
                {
                    "surface_id": surface_id,
                    "surface_kind": kind,
                    "label": declaration.get("label"),
                    "owner_object_id": str(object_id),
                    "owner_catalog_item_id": catalog_item,
                    "location_id": target.get("location_id"),
                    "area_id": target.get("area_id"),
                    "pose_condition": declaration.get("pose_condition"),
                    "rest_modes": list(declaration.get("rest_modes") or ()),
                    "items": sorted(
                        child
                        for child, child_target in placements.items()
                        if str((child_target or {}).get("kind")) == PLACEMENT_SURFACE
                        and str((child_target or {}).get("surface_id")) == surface_id
                    ),
                }
            )
    return surfaces


def surfaces_here(
    state: Mapping[str, Any],
    catalog_by_instance: Mapping[str, str],
    *,
    location_id: Optional[str],
    area_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Derived surfaces for one place/area, plus the legacy bare-surface view.

    The legacy bare ``bed_surface`` placement the M13A.1 bootstrap wrote is kept
    as its own entry (never merged into an instance surface twice).
    """

    normalized = validate_substrate(state)
    placements = normalized["placements"]
    derived = derived_surfaces(
        placements,
        catalog_by_instance,
        location_id=location_id,
        area_id=area_id,
    )
    seen_kinds = {item["surface_kind"] for item in derived}
    for object_id in sorted(placements):
        target = placements[object_id] or {}
        if str(target.get("kind")) != PLACEMENT_SURFACE:
            continue
        surface_id = str(target.get("surface_id"))
        kind = surface_kind_of(surface_id)
        if kind is None or surface_owner_instance(surface_id) is not None:
            continue
        if location_id is not None and str(target.get("location_id")) != str(location_id):
            continue
        if kind in seen_kinds:
            continue
        declaration = SURFACE_KINDS[kind]
        derived.append(
            {
                "surface_id": surface_id,
                "surface_kind": kind,
                "label": declaration.get("label"),
                "owner_object_id": None,
                "owner_catalog_item_id": declaration["owner_catalog_item_id"],
                "location_id": target.get("location_id"),
                "area_id": target.get("area_id"),
                "pose_condition": declaration.get("pose_condition"),
                "rest_modes": list(declaration.get("rest_modes") or ()),
                "items": sorted(
                    child
                    for child, child_target in placements.items()
                    if str((child_target or {}).get("kind")) == PLACEMENT_SURFACE
                    and str((child_target or {}).get("surface_id")) == surface_id
                ),
            }
        )
    return derived


def rest_surfaces_here(
    state: Mapping[str, Any],
    catalog_by_instance: Mapping[str, str],
    *,
    location_id: Optional[str],
    mode: str = "sleep_like",
) -> list[dict[str, Any]]:
    """Surfaces that really support this rest mode at this place."""

    return [
        item
        for item in surfaces_here(
            state, catalog_by_instance, location_id=location_id
        )
        if mode in (item.get("rest_modes") or [])
    ]


def fixture_of(state: Mapping[str, Any], object_id: Any) -> Optional[dict[str, Any]]:
    """The recorded power of one fixture, or ``None`` (UNKNOWN -- never "off")."""

    normalized = validate_substrate(state)
    found = normalized["fixtures"].get(str(object_id))
    return canonical_copy(found) if found is not None else None


def set_fixture(
    state: Mapping[str, Any],
    object_id: Any,
    power: Any,
    *,
    since: Any = None,
    declared_source: Any = None,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    """Write one fixture's absolute power state (the only light truth).

    Absolutely idempotent by state: writing the same power again is refused with
    ``ALREADY_APPLIED`` so a replay cannot produce a second effect, and the
    caller never has to guess whether "on" happened twice.
    """

    normalized = validate_substrate(state)
    if expected_revision is not None:
        expected = _require_revision(expected_revision, "expected_revision")
        if normalized["revision"] != expected:
            raise StaleSubstrateError(
                f"stale substrate: expected {expected}, live {normalized['revision']}"
            )
    name = _require_text(object_id, "object_id")
    wanted = _require_choice(power, "fixture.power", FIXTURE_POWERS)
    existing = normalized["fixtures"].get(name)
    if existing is not None and existing["power"] == wanted:
        raise BodySubstrateError(
            f"ALREADY_APPLIED: {name} is already {wanted}"
        )
    if existing is None and len(normalized["fixtures"]) >= MAX_FIXTURES:
        raise BodySubstrateError("LIMIT: the fixture block is full")
    updated = canonical_copy(normalized)
    updated["fixtures"][name] = {
        "power": wanted,
        "since": _require_text(since, "fixture.since", maximum=64)
        if since is not None
        else _now_iso(),
        "declared_source": _require_text(
            declared_source if declared_source is not None else "catalog_declaration",
            "fixture.declared_source",
            maximum=128,
        ),
        "revision": normalized["revision"] + 1,
    }
    return _bump(updated)

def placement_of(state: Mapping[str, Any], object_id: str) -> Optional[dict[str, Any]]:
    """The single authoritative placement of ``object_id`` (or ``None``)."""

    normalized = validate_substrate(state)
    name = _require_text(object_id, "object_id")
    found = normalized["placements"].get(name)
    return canonical_copy(found) if found is not None else None


def slot_occupant(state: Mapping[str, Any], slot_id: str) -> Optional[str]:
    """DERIVED hand occupancy.  Never stored, so it cannot drift."""

    normalized = validate_substrate(state)
    slot = _require_choice(slot_id, "slot_id", HAND_SLOTS)
    for object_id, target in sorted(normalized["placements"].items()):
        if target["kind"] == PLACEMENT_HAND and target["slot_id"] == slot:
            return object_id
    return None


def hand_view(state: Mapping[str, Any]) -> dict[str, Optional[str]]:
    """Derived view: ``{"left_hand": ..., "right_hand": ...}``."""

    normalized = validate_substrate(state)
    return {slot: slot_occupant(normalized, slot) for slot in HAND_SLOTS}


def free_slots(state: Mapping[str, Any]) -> list[str]:
    normalized = validate_substrate(state)
    return [slot for slot in HAND_SLOTS if slot_occupant(normalized, slot) is None]


def place_object(
    state: Mapping[str, Any],
    object_id: str,
    target: Any,
    *,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    """Set the exclusive placement of ``object_id``.

    Raises :class:`StaleSubstrateError` if ``expected_revision`` is given and
    stale, and :class:`BodySubstrateError` if an exclusive hand slot already
    holds another object (``OCCUPIED``) or the object is unknown.
    """

    normalized = validate_substrate(state)
    if expected_revision is not None:
        expected = _require_revision(expected_revision, "expected_revision")
        if normalized["revision"] != expected:
            raise StaleSubstrateError(
                f"stale substrate: expected {expected}, live {normalized['revision']}"
            )

    name = _require_text(object_id, "object_id")
    normalized_target = validate_placement_target(target)

    if normalized_target["kind"] == PLACEMENT_HAND:
        slot = normalized_target["slot_id"]
        occupant = slot_occupant(normalized, slot)
        if occupant is not None and occupant != name:
            raise BodySubstrateError(f"OCCUPIED: {slot} already holds {occupant}")

    updated = canonical_copy(normalized)
    updated["placements"][name] = normalized_target
    return _bump(updated)


def remove_object(
    state: Mapping[str, Any], object_id: str, *, expected_revision: Optional[int] = None
) -> dict[str, Any]:
    normalized = validate_substrate(state)
    if expected_revision is not None:
        expected = _require_revision(expected_revision, "expected_revision")
        if normalized["revision"] != expected:
            raise StaleSubstrateError(
                f"stale substrate: expected {expected}, live {normalized['revision']}"
            )
    name = _require_text(object_id, "object_id")
    if name not in normalized["placements"]:
        raise BodySubstrateError(f"UNKNOWN: {name} has no placement")
    updated = canonical_copy(normalized)
    del updated["placements"][name]
    return _bump(updated)


def consumption_receipt(
    state: Mapping[str, Any], execution_id: str
) -> Optional[dict[str, Any]]:
    """The World-owned receipt for one execution (``None`` when absent).

    This is the *only* accepted proof that an execution consumed an instance.
    A missing placement is never upgraded into that proof.
    """

    normalized = validate_substrate(state)
    key = _require_text(execution_id, "execution_id", maximum=256)
    found = normalized["consumptions"].get(key)
    return canonical_copy(found) if found is not None else None


def receipts_for_object(state: Mapping[str, Any], object_id: Any) -> list[dict[str, Any]]:
    """Every receipt that consumed this object id (normally zero or one)."""

    normalized = validate_substrate(state)
    name = _require_text(object_id, "object_id")
    return [
        canonical_copy(receipt)
        for _key, receipt in sorted(normalized["consumptions"].items())
        if receipt["object_id"] == name
    ]


def consume_object(
    state: Mapping[str, Any],
    object_id: str,
    receipt: Mapping[str, Any],
    *,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    """One atomic state change: drop the live placement **and** file the receipt.

    Both halves are returned together so the caller can persist them with a
    single ``SubstrateStore.save()`` -- an owner change plus its evidence in one
    atomic write.  There is deliberately no "remove first, record later" mode:
    that is exactly the crash window where a missing object could be mistaken
    for a committed consumption.
    """

    normalized = validate_substrate(state)
    if expected_revision is not None:
        expected = _require_revision(expected_revision, "expected_revision")
        if normalized["revision"] != expected:
            raise StaleSubstrateError(
                f"stale substrate: expected {expected}, live {normalized['revision']}"
            )
    name = _require_text(object_id, "object_id")
    if name not in normalized["placements"]:
        raise BodySubstrateError(f"UNKNOWN: {name} has no live placement")

    validated = validate_consumption_receipt(receipt)
    if validated["object_id"] != name:
        raise BodySubstrateError("consumption receipt names a different object")
    if validated["execution_id"] in normalized["consumptions"]:
        raise BodySubstrateError(
            "ALREADY_APPLIED: this execution already filed a consumption receipt"
        )
    if len(normalized["consumptions"]) >= MAX_CONSUMPTIONS:
        raise BodySubstrateError(
            "LIMIT: the consumption receipt block is full; an existing receipt is "
            "never pruned to make room for a new one"
        )

    updated = canonical_copy(normalized)
    del updated["placements"][name]
    updated["consumptions"][validated["execution_id"]] = validated
    return _bump(updated)


def set_pose(
    state: Mapping[str, Any],
    pose: str,
    *,
    orientation: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    normalized = validate_substrate(state)
    if expected_revision is not None:
        expected = _require_revision(expected_revision, "expected_revision")
        if normalized["revision"] != expected:
            raise StaleSubstrateError(
                f"stale substrate: expected {expected}, live {normalized['revision']}"
            )
    updated = canonical_copy(normalized)
    updated["pose"] = _require_choice(pose, "pose", POSES)
    if orientation is not None:
        updated["orientation"] = _require_choice(orientation, "orientation", ORIENTATIONS)
    return _bump(updated)


def reserve_slot(
    state: Mapping[str, Any],
    slot_id: str,
    execution_id: str,
    *,
    at: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    """Exclusive body-resource reservation for one in-flight action."""

    normalized = validate_substrate(state)
    if expected_revision is not None:
        expected = _require_revision(expected_revision, "expected_revision")
        if normalized["revision"] != expected:
            raise StaleSubstrateError(
                f"stale substrate: expected {expected}, live {normalized['revision']}"
            )
    slot = _require_choice(slot_id, "slot_id", HAND_SLOTS)
    holder = _require_text(execution_id, "execution_id")
    existing = normalized["reservations"].get(slot)
    if existing is not None and existing["execution_id"] != holder:
        raise BodySubstrateError(
            f"OCCUPIED: {slot} is reserved by {existing['execution_id']}"
        )
    updated = canonical_copy(normalized)
    updated["reservations"][slot] = {
        "execution_id": holder,
        "reserved_at": _require_text(at, "at") if at is not None else _now_iso(),
    }
    return _bump(updated)


def release_slot(
    state: Mapping[str, Any],
    slot_id: str,
    execution_id: str,
    *,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    normalized = validate_substrate(state)
    if expected_revision is not None:
        expected = _require_revision(expected_revision, "expected_revision")
        if normalized["revision"] != expected:
            raise StaleSubstrateError(
                f"stale substrate: expected {expected}, live {normalized['revision']}"
            )
    slot = _require_choice(slot_id, "slot_id", HAND_SLOTS)
    holder = _require_text(execution_id, "execution_id")
    existing = normalized["reservations"].get(slot)
    if existing is None or existing["execution_id"] != holder:
        raise BodySubstrateError(f"UNKNOWN: {slot} is not reserved by {holder}")
    updated = canonical_copy(normalized)
    del updated["reservations"][slot]
    return _bump(updated)


# --------------------------------------------------------------------------
# derived capability view -- never persisted, never authoritative
# --------------------------------------------------------------------------


def reachability_inputs(state: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic body-side inputs for the World resolver.

    This is a *derived view*.  It has no authority: the resolver still decides.
    """

    normalized = validate_substrate(state)
    pose = normalized["pose"]
    usable = pose in _HANDS_USABLE_POSES
    hands = hand_view(normalized)
    return {
        "body_id": normalized["body_id"],
        "pose": pose,
        "orientation": normalized["orientation"],
        "body_revision": normalized["revision"],
        "hands_usable": usable,
        "left_hand": hands["left_hand"],
        "right_hand": hands["right_hand"],
        "free_slots": free_slots(normalized) if usable else [],
        "reserved_slots": sorted(normalized["reservations"]),
        "can_manipulate": bool(usable and free_slots(normalized)),
    }


def read_model(state: Mapping[str, Any]) -> dict[str, Any]:
    """Composite read model.  Combines World-owned facts only."""

    normalized = validate_substrate(state)
    return {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "world_id": normalized["world_id"],
        "body_id": normalized["body_id"],
        "world_entity_id": normalized["world_entity_id"],
        "revision": normalized["revision"],
        "pose": normalized["pose"],
        "orientation": normalized["orientation"],
        "hands": hand_view(normalized),
        "placements": canonical_copy(normalized["placements"]),
        "fixtures": canonical_copy(normalized["fixtures"]),
        "reservations": canonical_copy(normalized["reservations"]),
        #: Bounded lifecycle evidence: how many consumptions this substrate has
        #: recorded.  The receipts themselves are owner records, not a read model.
        "consumption_count": len(normalized["consumptions"]),
        "derived": reachability_inputs(normalized),
    }
