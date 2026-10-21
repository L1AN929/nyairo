#!/usr/bin/env python3
"""v0.4 phone world -- the World-side facade the phone (P/U) calls.

One resident world, one body, one operator: the phone can see where she is and
what is around her, walk somewhere (and *be on the way*, not teleport), pick up /
put down / sit / lie down, and ask to start or stop resting.  Nothing here is a
second store or a second authority:

==================================  =========================================
world position / topology           ``world_living_skeleton`` + ``world_runtime_data``
reachability + opening hours        ``world_daily_opportunities`` (read-only)
in-transit walk                     ``world_travel_delivery`` (arrival = owner tick)
placements / pose / hands           ``world_body_substrate``
consumability / property economy    ``world_consumable_contract`` / ``world_property_economy``
commit / idempotency                ``world_action_resolver`` + execution ledger
one-shot submission ticket          ``world_action_authorization`` registry
six somatic variables + recovery    ``body_runtime`` (via the gateway only)
==================================  =========================================

Reads are **zero-write**: ``snapshot()`` and ``operation()`` never initialize,
never materialize, never settle an arrival, never migrate on disk.  The single
write path is ``act()`` -> gateway ``submit_local_owner_action`` -> Resolver ->
ledger -> ``body_consequence``; arrival and recovery only advance in the owner's
own tick.

Usage (P's server process, same uid as the World/Body service)::

    phone = PhoneWorld(
        os.environ["CHIYO_WORLD_HOME"],
        os.environ,
        local_owner_uid=os.getuid(),
        subject="chiyo",
        gateway_socket=Path(home) / "run" / "gateway.sock",
    )
    phone.snapshot()                                    # GET /api/phone/world
    phone.act("MOVE", {"destination_location_id": "convenience_store"}, key)
    phone.operation(key)                                # reconcile one key
"""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_SERVICE_DIR = _HERE.parent / "service"
for _path in (str(_HERE), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import world_body_binding as binding  # noqa: E402
import world_body_substrate as substrate  # noqa: E402
import world_daily_opportunities as opportunities  # noqa: E402
import world_execution_ledger as ledger  # noqa: E402
import world_foundation as wf  # noqa: E402
import world_living_skeleton as skeleton  # noqa: E402
import world_property_economy as economy  # noqa: E402
import world_runtime_data as wrd  # noqa: E402
import world_travel_delivery as travel  # noqa: E402
import world_room_environment as room_environment  # noqa: E402
#: One key names one operation *across the whole phone*: the shopping facade
#: derives the same execution id from the same key, so a key can never mean two
#: different things (a purchase and a walk, say) and a replay is a replay.
from world_phone_shopping import execution_id_for  # noqa: E402,F401

SCHEMA_PHONE_WORLD = "world.phone.world.v1"
KIND_PHONE_WORLD = "world_phone_world"

#: Envelope statuses (mirrors ``PhoneShopping``; P's HTTP table is shared).
STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"
STATUS_DISABLED = "disabled"
STATUS_NOT_FOUND = "not_found"

#: The verbs the *local owner* (this facade) may submit.  Identical to the
#: gateway's ``GATEWAY_LOCAL_OWNER_VERBS`` and the Resolver's
#: ``LOCAL_OWNER_ACTIONS``; ``CONSUME`` is deliberately not offered here because
#: it already has a channel (``PhoneShopping.use``).  ``ARRANGE`` and
#: ``TOGGLE_FIXTURE`` are the v0.4 room-living verbs.
VERBS = (
    "MOVE",
    "PICK_UP",
    "PLACE",
    "POSE",
    "REST",
    "ARRANGE",
    "TOGGLE_FIXTURE",
)

#: Per-verb exact parameter names.  Anything else is refused, so a client cannot
#: smuggle an elapsed time or a body value into the body.  ``ARRANGE`` never
#: accepts a hand slot (furniture is not carried) and ``TOGGLE_FIXTURE`` never
#: accepts a bare toggle: the requested *state* is absolute and required.
PARAM_SPEC: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "MOVE": (("destination_location_id",), ()),
    "PICK_UP": (("object_id", "slot_id"), ()),
    "PLACE": (("object_id", "target"), ()),
    "POSE": (("pose",), ("orientation",)),
    "REST": (("state",), ("mode",)),
    "ARRANGE": (("object_id", "target"), ()),
    "TOGGLE_FIXTURE": (("object_id", "state"), ()),
}

#: Anything that looks like identity, permission or a body value.
FORBIDDEN_PARAM_KEYS = frozenset(
    {
        "uid",
        "gid",
        "authority",
        "confirm_apply",
        "home",
        "subject",
        "world_home",
        "binding",
        "source",
        "permission",
        "elapsed_seconds",
        "movement_duration_seconds",
        "posture_seconds",
        "fatigue",
        "activation",
        "comfort",
        "sensory_load",
        "tension",
        "warmth",
        "somatic",
        "recovery",
        "runtime_state",
    }
)

LOCAL_OWNER_SOURCE = "local_owner_phone"
LOCAL_OWNER_COMMAND = "submit_local_owner_action"

DEFAULT_READ_TIMEOUT = 3.0
#: A mutation is timed above the Resolver's own 10s commit budget.
DEFAULT_MUTATION_TIMEOUT = 15.0

MAX_OPERATION_KEY = 128
MAX_ID = 128
MAX_MOVE_TARGET = 96
MAX_SIGNALS = 8
#: Bounded destination list; mirrors ``world_daily_opportunities.MAX_WALK_SECONDS``.
MAX_WALK_SECONDS = opportunities.MAX_WALK_SECONDS
MAX_NODES = 40
RECENT_LIMIT_DEFAULT = 5
RECENT_LIMIT_MAX = 20


# ---------------------------------------------------------------------------
# envelope helpers
# ---------------------------------------------------------------------------


def _envelope(
    ok: bool,
    status: str,
    data: Optional[Mapping[str, Any]] = None,
    reason_code: Optional[str] = None,
) -> dict[str, Any]:
    """The one envelope shape P's HTTP layer copies verbatim."""

    result: dict[str, Any] = {
        "kind": KIND_PHONE_WORLD,
        "schema_version": SCHEMA_PHONE_WORLD,
        "ok": bool(ok),
        "status": status,
        "data": dict(data or {}),
    }
    if reason_code is not None:
        result["reason_code"] = reason_code
    return result


def _ok(status: str, data: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    return _envelope(True, status, data)


def _fail(
    status: str, reason_code: str, data: Optional[Mapping[str, Any]] = None
) -> dict[str, Any]:
    return _envelope(False, status, data, reason_code)


def _bounded(value: Any, *, maximum: int = MAX_ID) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > maximum:
        return None
    return text


def _block(
    status: str = "available",
    reason_code: Optional[str] = None,
    source: Optional[str] = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {"status": status}
    if reason_code is not None:
        block["reason_code"] = reason_code
    if source is not None:
        block["source"] = source
    return block


def _instant(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


# ---------------------------------------------------------------------------
# the facade
# ---------------------------------------------------------------------------


class PhoneWorld:
    """The phone's world surface for one bound local owner."""

    def __init__(
        self,
        hermes_home: Any,
        environment: Optional[Mapping[str, str]],
        *,
        local_owner_uid: Any,
        subject: str = "chiyo",
        gateway_socket: Any = None,
    ):
        self.environment: dict[str, str] = dict(environment or {})
        self._auth_error: Optional[str] = None
        try:
            raw_home = Path(str(hermes_home)).expanduser()
        except (TypeError, ValueError):
            raw_home = None
        if raw_home is None or not raw_home.is_absolute():
            self.hermes_home: Optional[Path] = None
            self._auth_error = "LOCAL_OWNER_HOME_MISMATCH"
        else:
            self.hermes_home = raw_home
            if not raw_home.is_dir():
                self._auth_error = "LOCAL_OWNER_HOME_MISMATCH"
        real_uid = os.getuid() if hasattr(os, "getuid") else None
        if self._auth_error is None:
            if (
                isinstance(local_owner_uid, bool)
                or not isinstance(local_owner_uid, int)
                or real_uid is None
                or local_owner_uid != real_uid
            ):
                self._auth_error = "LOCAL_OWNER_UID_MISMATCH"
        self.local_owner_uid = local_owner_uid
        if self._auth_error is None and subject != economy.OWNER:
            self._auth_error = "LOCAL_OWNER_SUBJECT_MISMATCH"
        self.subject = subject
        if gateway_socket is not None:
            self.gateway_socket = Path(str(gateway_socket)).expanduser()
        elif self.hermes_home is not None:
            self.gateway_socket = self.hermes_home / "run" / "gateway.sock"
        else:
            self.gateway_socket = None

    # -- identity ---------------------------------------------------------

    @property
    def bound(self) -> bool:
        return self._auth_error is None

    def _guard(self) -> Optional[dict[str, Any]]:
        """Reject before any read/write when the binding does not hold."""

        if self._auth_error is None:
            return None
        return _fail(STATUS_REJECTED, self._auth_error)

    def _local_owner_context(self) -> Optional[dict[str, Any]]:
        if self._auth_error is not None or self.hermes_home is None:
            return None
        return {
            "source": LOCAL_OWNER_SOURCE,
            "uid": self.local_owner_uid,
            "subject": self.subject,
            "home": str(self.hermes_home),
        }

    # -- owner reads (all zero-write) -------------------------------------

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _world(self):
        try:
            return wrd.active_world(self.environment)
        except Exception:  # noqa: BLE001 - an unreadable package means no topology
            return None

    def _raw_world_state(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """``(state, reason)`` from the live file, without any write."""

        if self.hermes_home is None:
            return None, "WORLD_NOT_INITIALIZED"
        path = self.hermes_home / "data" / "world" / "world_state.json"
        if not path.exists():
            return None, "WORLD_NOT_INITIALIZED"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "WORLD_STATE_UNREADABLE"
        return (payload if isinstance(payload, dict) else None), None

    def _world_state(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """``(state, reason)`` judged by the World's own read-only rule."""

        raw, reason = self._raw_world_state()
        if raw is None:
            return None, reason or "WORLD_STATE_INVALID"
        try:
            state = skeleton.read_world_state(
                hermes_home=self.hermes_home, environment=self.environment
            )
        except Exception:  # noqa: BLE001
            return None, "WORLD_STATE_INVALID"
        if state is None:
            return None, "WORLD_STATE_INVALID"
        return state, None

    def _substrate_state(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        try:
            store = substrate.SubstrateStore(substrate.substrate_path(self.hermes_home))
            live = store.load_with_migration()[0]
        except substrate.BodySubstrateError:
            return None, "SUBSTRATE_STATE_ERROR"
        except OSError:
            return None, "SUBSTRATE_STATE_ERROR"
        if live is None:
            return None, "SUBSTRATE_NOT_INITIALIZED"
        return live, None

    def _travel_state(self) -> Optional[dict[str, Any]]:
        try:
            if not travel.travel_enabled(self.environment):
                return None
            state = travel.load_state(self.hermes_home, self.environment)
        except Exception:  # noqa: BLE001
            return None
        return state if isinstance(state, Mapping) else None

    def _travel_record(self, execution_id: Any) -> Optional[dict[str, Any]]:
        state = self._travel_state()
        if state is None:
            return None
        for item in state.get("travels") or []:
            if isinstance(item, Mapping) and str(item.get("execution_id")) == str(
                execution_id
            ):
                return dict(item)
        return None

    def _labels(self) -> dict[str, str]:
        """Economy-joined readable names (bounded, local, read-only)."""

        labels: dict[str, str] = {}
        if self.hermes_home is None:
            return labels
        try:
            document = json.loads(
                (self.hermes_home / "state" / "world_property_economy.json").read_text(
                    encoding="utf-8"
                )
            )
            for item in document.get("items") or []:
                if isinstance(item, Mapping) and item.get("item_instance_id"):
                    labels[str(item["item_instance_id"])] = str(
                        item.get("catalog_item_id") or "item"
                    )
        except Exception:  # noqa: BLE001 - labels are cosmetic, never permission
            return {}
        return labels

    def _capability(self, object_id: str) -> dict[str, Any]:
        try:
            return binding.object_capability(self.hermes_home, object_id) or {}
        except Exception:  # noqa: BLE001 - unknown capability is UNKNOWN, not "yes"
            return {}

    # -- gateway ----------------------------------------------------------

    def _gateway_call(
        self,
        command: str,
        argument: Any = None,
        *,
        timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> dict[str, Any]:
        """One request on the trusted local socket.  Never raises for transport."""

        request = json.dumps({"command": command, "argument": argument}) + "\n"
        sock = str(self.gateway_socket) if self.gateway_socket is not None else ""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(float(timeout))
                client.connect(sock)
                client.sendall(request.encode())
                buffer = b""
                while b"\n" not in buffer:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
        except socket.timeout:
            return {"outcome": "TIMEOUT", "reason": "socket_timeout", "result": None}
        except FileNotFoundError:
            return {"outcome": "UNAVAILABLE", "reason": "socket_missing", "result": None}
        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, OSError) as error:
            return {
                "outcome": "UNAVAILABLE",
                "reason": "socket_error:%s" % type(error).__name__,
                "result": None,
            }
        if not buffer:
            return {"outcome": "UNAVAILABLE", "reason": "empty_response", "result": None}
        try:
            payload = json.loads(buffer.decode("utf-8").strip().splitlines()[0])
        except (ValueError, IndexError):
            return {"outcome": "UNAVAILABLE", "reason": "malformed_response", "result": None}
        if not payload.get("ok"):
            return {
                "outcome": "FAILURE",
                "reason": str(payload.get("error")),
                "result": payload,
            }
        return {"outcome": "OK", "reason": None, "result": payload.get("result")}

    # =====================================================================
    # snapshot() -- nine read-only blocks
    # =====================================================================

    def snapshot(self) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked

        world_state, world_reason = self._world_state()
        world = self._world()
        catalog: Optional[dict[str, Any]] = None
        if world_state is not None:
            try:
                catalog = opportunities.location_opportunity_catalog(
                    self.environment,
                    now=self._now(),
                    hermes_home=self.hermes_home,
                )
            except Exception:  # noqa: BLE001 - no catalog, no destinations
                catalog = None
        location = self._location_block(world_state, world, world_reason)
        data = {
            "world": self._world_block(world_state, world, world_reason),
            "location": location,
            "local_environment": self._local_environment_block(
                world, catalog, location
            ),
            # v0.4 room-living: owned furniture, the surfaces really here and
            # the delivery state are additive fields of the *objects* block, so
            # the previously frozen nine top-level blocks keep their shape.
            "destinations": self._destinations_block(world, catalog, location),
            "travel": self._travel_block(),
            "objects": self._objects_block(world_state, location),
            "body": self._body_block(location),
            "actions": self._actions_block(),
            "recent_executions": self._recent_block(RECENT_LIMIT_DEFAULT),
        }
        return _ok(STATUS_APPLIED, data)

    def _world_block(
        self,
        state: Optional[Mapping[str, Any]],
        world: Any,
        reason: Optional[str],
    ) -> dict[str, Any]:
        if state is None:
            block = _block("unavailable", reason or "WORLD_STATE_INVALID", "world_state")
            block.update(
                {
                    "world_id": None,
                    "revision": None,
                    "schema_version": None,
                    "timezone": None,
                    "scene_id": None,
                    "local_time": None,
                    "local_date": None,
                    "weekday": None,
                }
            )
            return block
        local = None
        try:
            local = wf.world_local_time(dict(state), self._now())
        except Exception:  # noqa: BLE001 - no clock projection, no local fields
            local = None
        block = _block("available", source="world_state")
        block.update(
            {
                "world_id": state.get("world_id"),
                "revision": state.get("revision"),
                "schema_version": state.get("schema_version"),
                "timezone": state.get("timezone")
                or getattr(world, "timezone_name", None),
                "scene_id": (state.get("scene") or {}).get("scene_id"),
                "local_time": local.isoformat(timespec="seconds") if local else None,
                "local_date": local.date().isoformat() if local else None,
                "weekday": local.isoweekday() if local else None,
            }
        )
        return block

    def _current_node(
        self, state: Optional[Mapping[str, Any]], world: Any
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """``(node, place_id, area_id)`` from the live state (may be ``None``)."""

        if state is None:
            return None, None, None
        location = state.get("location") or {}
        place_id = location.get("place_id")
        area_id = location.get("area_id")
        node = None
        if world is not None and place_id:
            try:
                node = world.state_node(str(place_id), area_id)
            except Exception:  # noqa: BLE001
                node = None
        return node, place_id, area_id

    def _location_block(
        self,
        state: Optional[Mapping[str, Any]],
        world: Any,
        reason: Optional[str],
    ) -> dict[str, Any]:
        node, place_id, area_id = self._current_node(state, world)
        if node is None or place_id is None:
            return _block(
                "unavailable",
                "WORLD_NOT_INITIALIZED"
                if state is None and reason == "WORLD_NOT_INITIALIZED"
                else "WORLD_LOCATION_INVALID",
                "world_state+world_topology",
            )
        projection = {}
        if world is not None:
            try:
                projection = world.location(node) or {}
            except Exception:  # noqa: BLE001
                projection = {}
        block = _block("available", source="world_state+world_topology")
        block.update(
            {
                "node": node,
                "place_id": place_id,
                "area_id": area_id,
                "name": projection.get("name") or place_id,
                "kind": projection.get("kind")
                or (
                    (world.location(place_id) or {}).get("kind")
                    if world is not None
                    else None
                ),
                "home": bool(
                    world is not None
                    and str(place_id) == str(getattr(world, "home_location_id", ""))
                ),
            }
        )
        return block

    def _local_environment_block(
        self,
        world: Any,
        catalog: Optional[Mapping[str, Any]],
        location: Mapping[str, Any],
    ) -> dict[str, Any]:
        if location.get("status") != "available":
            block = _block(
                "unavailable",
                location.get("reason_code"),
                "world_package_scene",
            )
            block["light"] = self._light_block(location)
            return block
        place_id = str(location["place_id"])
        nav = None
        if world is not None:
            try:
                nav = world.navigation_of(place_id)
            except Exception:  # noqa: BLE001
                nav = None
        if not nav:
            block = _block(
                "unavailable", "WORLD_HAS_NO_INDOOR_DECLARATION", "world_package_scene"
            )
            block.update({"building": None, "area": None, "furniture": [],
                          "furniture_total": 0, "adjacent_areas": []})
            block["light"] = self._light_block(location)
            return block

        area_id = str(location.get("area_id") or "")
        area = next(
            (item for item in nav.get("areas") or [] if str(item.get("area_id")) == area_id),
            None,
        )
        building_name = None
        if world is not None:
            try:
                building_name = (world.location(place_id) or {}).get("name")
            except Exception:  # noqa: BLE001
                building_name = None

        furniture: list[dict[str, Any]] = []
        observed_reason: Optional[str] = None
        entry = None
        if isinstance(catalog, Mapping) and catalog.get("status") == "available":
            entry = next(
                (
                    item
                    for item in catalog.get("opportunities") or []
                    if item.get("here")
                ),
                None,
            )
        if entry is not None:
            observed = entry.get("observed") or {}
            if observed.get("status") == "visible_here":
                furniture = [
                    {
                        "object_id": item.get("object_id"),
                        "name": item.get("name"),
                        "kind": item.get("kind"),
                        "note": item.get("note") or "",
                    }
                    for item in observed.get("furniture") or []
                ]
            else:
                observed_reason = observed.get("reason_code") or "NOT_OBSERVED"
        else:
            observed_reason = "WORLD_OPPORTUNITIES_UNAVAILABLE"

        adjacent: list[dict[str, Any]] = []
        if world is not None and location.get("node"):
            try:
                for node in world.reachable_of(str(location["node"])):
                    projection = world.location(str(node)) or {}
                    adjacent.append(
                        {
                            "node": str(node),
                            "name": projection.get("name") or str(node),
                            "kind": projection.get("kind"),
                        }
                    )
            except Exception:  # noqa: BLE001
                adjacent = []

        block = _block("available", source="world_package_scene")
        block.update(
            {
                "building": {"place_id": place_id, "name": building_name or place_id},
                "area": (
                    {
                        "area_id": area_id,
                        "name": (area or {}).get("name") or area_id,
                        "kind": (area or {}).get("kind") or "area",
                    }
                    if area_id
                    else None
                ),
                "furniture": furniture,
                "furniture_total": len(furniture),
                "adjacent_areas": adjacent,
                "note": "furniture 只含当前区域、世界包声明过的摆位；上限 8 件",
            }
        )
        if observed_reason is not None:
            block["furniture_status"] = "unavailable"
            block["furniture_reason_code"] = observed_reason
        block["light"] = self._light_block(location)
        return block

    # -- v0.4 room-living read blocks (all read-only) ----------------------

    def _light_block(self, location: Mapping[str, Any]) -> dict[str, Any]:
        """The room light of this place/area: declared + recorded power only."""

        try:
            return room_environment.light_for_home(
                self.hermes_home, now=self._now(), location=location
            )
        except Exception:  # noqa: BLE001 - unknown, never "dark"
            return {
                "status": "unknown",
                "reason_code": "ROOM_ENVIRONMENT_UNAVAILABLE",
                "power": None,
                "band": None,
                "source": "world_room_environment",
                "declared_source": None,
                "since": None,
                "on_seconds": None,
                "fixture_ids": [],
                "place_id": location.get("place_id"),
                "area_id": location.get("area_id"),
            }

    def _owned_catalog(self) -> dict[str, str]:
        try:
            return binding.owned_catalog_map(self.hermes_home)
        except Exception:  # noqa: BLE001
            return {}

    def _surfaces_block(self, location: Mapping[str, Any]) -> dict[str, Any]:
        """Surfaces that are *really* here, instance-scoped.

        A global ``desk_surface`` is never reported: each desk's surface is
        ``desk_surface:<item_instance_id>`` and exists only while that desk is
        placed here.  Children already on a surface are listed with it.
        """

        live, reason = self._substrate_state()
        block = _block("available", source="world_body_substrate")
        block.update(
            {
                "here": [],
                "count": 0,
                "rest_here": [],
                "declared_kinds": sorted(substrate.SURFACE_KINDS),
                "instance_scoped": True,
                "read_is_pure": True,
            }
        )
        if live is None:
            block["status"] = "unavailable"
            block["reason_code"] = reason
            return block
        if location.get("status") != "available":
            block["status"] = "unavailable"
            block["reason_code"] = location.get("reason_code") or "WORLD_LOCATION_INVALID"
            return block
        try:
            surfaces = substrate.surfaces_here(
                live,
                self._owned_catalog(),
                location_id=location.get("place_id"),
                area_id=location.get("area_id"),
            )
        except Exception:  # noqa: BLE001
            block["status"] = "unavailable"
            block["reason_code"] = "SUBSTRATE_STATE_ERROR"
            return block
        block["here"] = surfaces
        block["count"] = len(surfaces)
        block["rest_here"] = [
            item for item in surfaces if "sleep_like" in (item.get("rest_modes") or [])
        ]
        return block

    def _owned_furniture_block(self) -> dict[str, Any]:
        """Owned instances with their real delivery + placement state.

        ``delivered`` is the economy's own coarse fact; ``placement`` is the
        substrate's authoritative one.  A delivered item with ``placement=None``
        is exactly the "bought, arrived, not yet arranged" state.
        """

        block = _block("available", source="world_property_economy")
        block.update(
            {"items": [], "count": 0, "arrange_available": False, "read_is_pure": True}
        )
        try:
            state = economy.load_state(self.hermes_home)
        except economy.PropertyEconomyError:
            block["status"] = "unavailable"
            block["reason_code"] = "ECONOMY_STATE_ERROR"
            return block
        if state is None:
            block["status"] = "unavailable"
            block["reason_code"] = "GENESIS_NOT_INITIALIZED"
            return block
        live, _reason = self._substrate_state()
        placements = dict((live or {}).get("placements") or {})
        labels = self._labels()
        catalog_names = {
            entry["catalog_item_id"]: entry["name"] for entry in economy.CATALOG
        }
        items: list[dict[str, Any]] = []
        for item in state["items"]:
            if item["disposition"] != economy.DISPOSITION_OWNED:
                continue
            item_id = item["item_instance_id"]
            declaration = economy.catalog_declaration(item["catalog_item_id"])
            placement = placements.get(item_id)
            delivered = item["current_location"] == economy.HOME_ITEM_LOCATION
            entry: dict[str, Any] = {
                "item_instance_id": item_id,
                "label": labels.get(item_id) or item_id,
                "catalog_item_id": item["catalog_item_id"],
                "name": catalog_names.get(item["catalog_item_id"], item["catalog_item_id"]),
                "usable_kind": declaration.get("usable_kind"),
                "relocatable": bool(declaration.get("relocatable")),
                "surfaces": [
                    substrate.surface_instance_id(kind, item_id)
                    for kind in declaration.get("surfaces") or ()
                ],
                "current_location": item["current_location"],
                "delivered": delivered,
                "delivery_status": (
                    "DELIVERED" if delivered else
                    "IN_TRANSIT" if str(item["current_location"]).startswith("in_transit/")
                    else "AT_HOME_UNPLACED"
                ),
                "placement": dict(placement) if placement else None,
                "arrange_available": bool(
                    delivered and declaration.get("relocatable")
                ),
            }
            items.append(entry)
        block["items"] = items
        block["count"] = len(items)
        block["arrange_available"] = any(item["arrange_available"] for item in items)
        block["unplaced_delivered"] = [
            item["item_instance_id"]
            for item in items
            if item["delivered"] and item["placement"] is None
        ]
        return block

    def _deliveries_block(self) -> dict[str, Any]:
        """The real delivery state.  A read never realizes an arrival."""

        try:
            return economy.deliveries_projection(
                hermes_home=self.hermes_home, environment=self.environment
            )
        except economy.PropertyEconomyError:
            return {
                "status": "unavailable",
                "reason_code": "ECONOMY_STATE_ERROR",
                "items": [],
            }

        block["light"] = self._light_block(location)
        return block

    def _node_walk(
        self, world: Any, origin: str, node: str
    ) -> tuple[Optional[int], Optional[list[str]]]:
        """``(walk_seconds, route)`` along the World's own graph, or ``(None, None)``."""

        try:
            route = world.route_to(origin, node)
        except Exception:  # noqa: BLE001
            return None, None
        if route is None:
            return None, None
        total = 0
        previous = origin
        for step in route:
            try:
                seconds = world.route_duration_seconds(previous, step)
            except Exception:  # noqa: BLE001
                return None, None
            if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
                return None, None
            total += seconds
            previous = step
        return total, list(route)

    def _destinations_block(
        self,
        world: Any,
        catalog: Optional[Mapping[str, Any]],
        location: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(catalog, Mapping) or catalog.get("status") != "available":
            block = _block(
                "unavailable",
                (catalog or {}).get("reason_code") or "WORLD_DATA_UNAVAILABLE",
                "world_daily_opportunities",
            )
            block.update({"scope": "NOW_ONLY", "places": [], "nodes": []})
            return block

        places: list[dict[str, Any]] = []
        for item in catalog.get("opportunities") or []:
            reachability = item.get("reachability") or {}
            availability = item.get("availability") or {}
            observed = item.get("observed") or {}
            places.append(
                {
                    "location_id": item.get("location_id"),
                    "name": item.get("name"),
                    "kind": item.get("kind"),
                    "here": bool(item.get("here")),
                    "walk_seconds": reachability.get("walk_seconds"),
                    "reach": reachability.get("status"),
                    "route": list(reachability.get("route") or []),
                    "availability": {
                        "status": availability.get("status"),
                        "reason_code": availability.get("reason_code"),
                        "source": availability.get("source"),
                    },
                    "affordances": [
                        {
                            "affordance_id": entry.get("affordance_id"),
                            "description": entry.get("description"),
                        }
                        for entry in item.get("affordances") or []
                    ],
                    "indoor_areas": list(item.get("indoor_areas") or []),
                    "observed": {
                        "status": observed.get("status"),
                        "reason_code": observed.get("reason_code"),
                    },
                }
            )

        nodes: list[dict[str, Any]] = []
        origin = catalog.get("current_location_id")
        if world is not None and origin:
            try:
                direct = set(world.reachable_of(str(origin)))
            except Exception:  # noqa: BLE001
                direct = set()
            try:
                candidates = world.all_nodes()
            except Exception:  # noqa: BLE001
                candidates = []
            for node in candidates:
                if str(node) == str(origin):
                    continue
                seconds, route = self._node_walk(world, str(origin), str(node))
                if seconds is None or seconds > MAX_WALK_SECONDS:
                    continue
                projection = {}
                try:
                    projection = world.location(str(node)) or {}
                except Exception:  # noqa: BLE001
                    projection = {}
                place_id, area_id = world.split_node(str(node))
                nodes.append(
                    {
                        "node": str(node),
                        "place_id": place_id,
                        "area_id": area_id,
                        "label": projection.get("name") or str(node),
                        "kind": "area" if "/" in str(node) else "place",
                        "walk_seconds": seconds,
                        "reach": "reachable",
                        "direct_move_available": str(node) in direct,
                        "route": route or [],
                    }
                )
            nodes.sort(key=lambda item: (item["walk_seconds"], item["node"]))
            nodes = nodes[:MAX_NODES]

        block = _block("available", source="world_daily_opportunities")
        block.update(
            {
                "scope": "NOW_ONLY",
                "current_node": catalog.get("current_location_id"),
                "walk_seconds_max": MAX_WALK_SECONDS,
                "places": places,
                "nodes": nodes,
                "action_grant": False,
            }
        )
        return block

    def _travel_projection(
        self, record: Mapping[str, Any], *, now: Optional[datetime] = None
    ) -> dict[str, Any]:
        """Read-only ``§2.5`` projection of one travel record."""

        moment = now or self._now()
        started = _instant(record.get("started_at"))
        expected = _instant(record.get("expected_arrival_at"))
        duration = record.get("route_duration_seconds")
        duration = duration if isinstance(duration, int) and not isinstance(duration, bool) else None
        elapsed = 0
        remaining: Optional[int] = None
        band = None
        if started is not None:
            elapsed = max(0, int((moment - started).total_seconds()))
            if duration:
                elapsed = min(elapsed, duration)
                remaining = max(0, duration - elapsed)
                if remaining <= 20:
                    band = "almost_there"
                elif duration and (elapsed / float(duration)) < 0.34:
                    band = "just_started"
                else:
                    band = "underway"
        status = (
            "arrived"
            if str(record.get("status")) == travel.TRAVEL_STATUS_ARRIVED
            else "in_progress"
        )
        block: dict[str, Any] = {
            "status": status,
            "source": "world_travel_delivery.observe_travel",
            "travel_id": record.get("travel_id"),
            "execution_id": record.get("execution_id"),
            "origin_location_id": record.get("origin_location_id"),
            "destination_location_id": record.get("destination_location_id"),
            "route_id": record.get("route_id"),
            "route_revision": record.get("route_revision"),
            "route_duration_seconds": duration,
            "started_at": record.get("started_at"),
            "expected_arrival_at": record.get("expected_arrival_at"),
            "arrived_at": record.get("arrived_at"),
            "elapsed_seconds": elapsed,
            "remaining_seconds": remaining,
            "progress_band": band,
            # Reading this projection never moves the World: arrival belongs to
            # the owner tick, so a GET must say "unchanged" out loud.
            "location_unchanged": status != "arrived",
        }
        block["body_load"], block["body_load_source"] = self._body_load_state(
            record.get("execution_id")
        )
        return block

    def _body_load_state(self, execution_id: Any) -> tuple[str, str]:
        records = self._consequence_records()
        if records is None:
            return "unknown", "body_consequence_ledger_unreadable"
        latest = None
        for record in records:
            if str(record.get("execution_id")) == str(execution_id):
                latest = record
        if latest is None:
            return "unknown", "body_consequence_ledger"
        return (
            "settled" if latest.get("state") == "applied" else "pending",
            "body_consequence_ledger",
        )

    def _consequence_records(self) -> Optional[list[dict[str, Any]]]:
        try:
            import body_consequence as bcon

            return bcon.ConsequenceLedger(
                self.hermes_home / "data" / "body" / "consequences.jsonl"
            ).read_all()
        except Exception:  # noqa: BLE001 - an unreadable journal is not "nothing owed"
            return None

    def _travel_block(self) -> dict[str, Any]:
        try:
            enabled = travel.travel_enabled(self.environment)
        except Exception:  # noqa: BLE001
            enabled = False
        if not enabled:
            block = _block("disabled", "FEATURE_DISABLED", "world_travel_delivery")
            block["location_unchanged"] = True
            return block
        state = self._travel_state()
        if state is None:
            block = _block("none", None, "world_travel_delivery")
            block["location_unchanged"] = True
            return block
        active = next(
            (
                item
                for item in state.get("travels") or []
                if isinstance(item, Mapping)
                and item.get("status") == travel.TRAVEL_STATUS_IN_PROGRESS
            ),
            None,
        )
        if active is None:
            block = _block("none", None, "world_travel_delivery")
            block["location_unchanged"] = True
            return block
        projection = self._travel_projection(active)
        block = _block("in_progress", None, "world_travel_delivery")
        block.update({k: v for k, v in projection.items() if k not in ("status", "source")})
        return block

    def _objects_block(
        self, world_state: Optional[Mapping[str, Any]], location: Mapping[str, Any]
    ) -> dict[str, Any]:
        live, reason = self._substrate_state()
        if live is None:
            block = _block("unavailable", reason, "world_body_substrate")
            block.update(
                {"here": [], "hands": {}, "held": [], "counts": {"here": 0, "hands": 0},
                 "substrate_revision": None}
            )
            block["surfaces"] = self._surfaces_block(location)
            block["owned_furniture"] = self._owned_furniture_block()
            block["deliveries"] = self._deliveries_block()
            return block
        labels = self._labels()
        placements = dict(live.get("placements") or {})
        here_place = location.get("place_id")
        here: list[dict[str, Any]] = []
        for object_id in sorted(placements):
            placement = placements[object_id] or {}
            if here_place is None or str(placement.get("location_id")) != str(here_place):
                continue
            capability = self._capability(object_id)
            takeable = capability.get("takeable")
            portable: Optional[bool]
            if isinstance(takeable, bool):
                portable = takeable
            elif isinstance(takeable, str) and takeable.strip().lower() in ("true", "false"):
                portable = takeable.strip().lower() == "true"
            else:
                # No declared capability is UNKNOWN -- never "you may take it".
                portable = None
            here.append(
                {
                    "object_id": object_id,
                    "label": labels.get(object_id) or object_id,
                    "placement_kind": placement.get("kind"),
                    "placement": dict(placement),
                    "portable": portable,
                    "portable_source": "world_body_binding.object_capability",
                }
            )
        hands = substrate.hand_view(live)
        held = sorted(value for value in hands.values() if value)
        block = _block("available", source="world_body_substrate")
        block.update(
            {
                "substrate_revision": live.get("revision"),
                "body_id": live.get("body_id"),
                "here": here,
                "hands": hands,
                "held": held,
                "counts": {"here": len(here), "hands": len(held)},
            }
        )
        # v0.4 room-living additive fields (same block, read-only):
        #   surfaces        -- the instance-scoped surfaces really here
        #   owned_furniture -- owned instances + delivery/placement state
        #   deliveries      -- the real pending/delivered records
        block["surfaces"] = self._surfaces_block(location)
        block["owned_furniture"] = self._owned_furniture_block()
        block["deliveries"] = self._deliveries_block()
        return block

    def _rest_evidence(self, live: Optional[Mapping[str, Any]], location: Mapping[str, Any]) -> Optional[str]:
        """``pose:<pose>@<surface>`` only when a declared surface is really here."""

        if live is None or location.get("status") != "available":
            return None
        pose = str(live.get("pose") or "")
        if pose not in ("seated", "lying"):
            return None
        try:
            surfaces = substrate.surfaces_here(
                live,
                self._owned_catalog(),
                location_id=location.get("place_id"),
                area_id=location.get("area_id"),
            )
        except Exception:  # noqa: BLE001
            return None
        for surface in surfaces:
            condition = surface.get("pose_condition")
            if condition and condition == pose:
                return "pose:%s@%s" % (pose, surface.get("surface_id"))
        return None

    def _body_block(self, location: Mapping[str, Any]) -> dict[str, Any]:
        live, reason = self._substrate_state()
        signals = self._gateway_call("get_body_signals")
        available_signals = (
            signals["outcome"] == "OK"
            and isinstance(signals.get("result"), Mapping)
            and bool(signals["result"].get("available"))
        )

        if live is None:
            block = _block("unavailable", reason, "world_body_substrate")
            block.update(
                {
                    "body_id": None,
                    "pose": None,
                    "orientation": None,
                    "pose_source": "world_body_substrate",
                    "recovery": None,
                    "signals": [],
                    "signals_status": "available" if available_signals else "unavailable",
                    "signals_source": "world_body_gateway_api.get_body_signals",
                    "bands_only": True,
                    "raw_values_exposed": False,
                    "rest_evidence": None,
                }
            )
            return block

        recovery = None
        exposed: list[dict[str, Any]] = []
        if available_signals:
            payload = signals["result"]
            recovery = payload.get("recovery")
            for signal in (payload.get("signals") or [])[:MAX_SIGNALS]:
                if not isinstance(signal, Mapping):
                    continue
                exposed.append(
                    {
                        "signal_type": signal.get("signal_type"),
                        "qualitative_band": signal.get("qualitative_band"),
                    }
                )
            status = "available"
            reason_code = None
        else:
            status = "partial"
            reason_code = "WORLD_BODY_SERVICE_UNAVAILABLE"

        block = _block(status, reason_code, "world_body_substrate")
        block.update(
            {
                "body_id": live.get("body_id"),
                "pose": live.get("pose"),
                "orientation": live.get("orientation"),
                "pose_source": "world_body_substrate",
                "recovery": recovery,
                "signals": exposed,
                "signals_status": "available" if available_signals else "unavailable",
                "signals_source": "world_body_gateway_api.get_body_signals",
                "bands_only": True,
                "raw_values_exposed": False,
                "rest_evidence": self._rest_evidence(live, location),
                "note": "pose 是 World 的测量；recovery/bands 是 Body 的投影；这里没有裸浮点",
            }
        )
        return block

    def _world_aftermath(self) -> dict[str, Any]:
        """Where the World stands right now (read-only, post-commit truthful)."""

        state, reason = self._world_state()
        world = self._world()
        node, place_id, area_id = self._current_node(state, world)
        return {
            "status": "available" if state is not None else "unavailable",
            "reason_code": reason,
            "revision": (state or {}).get("revision"),
            "location": {"node": node, "place_id": place_id, "area_id": area_id},
            "location_unchanged": True,
        }

    def _actions_block(self) -> dict[str, Any]:
        response = self._gateway_call("get_available_actions")
        block = _block("available", source="world_body_gateway_api.get_available_actions")
        block["local_owner_verbs"] = list(VERBS)
        if response["outcome"] != "OK" or not isinstance(response.get("result"), Mapping):
            block["status"] = "unavailable"
            block["reason_code"] = "WORLD_BODY_SERVICE_UNAVAILABLE"
            block["verbs"] = []
            return block
        verbs: list[dict[str, Any]] = []
        for entry in response["result"].get("actions") or []:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("verb")) not in VERBS:
                continue
            verbs.append(
                {
                    "verb": entry.get("verb"),
                    "available": bool(entry.get("available")),
                    "blocked_reason": entry.get("blocked_reason"),
                    "detail": entry.get("detail"),
                    "params": entry.get("params"),
                    "candidates": list(entry.get("candidates") or []),
                    "requires": list(entry.get("requires") or []),
                    "start_available": entry.get("start_available"),
                    "stop_available": entry.get("stop_available"),
                    "sleep_like_available": entry.get("sleep_like_available"),
                    "current": entry.get("current"),
                }
            )
        block["verbs"] = verbs
        missing = [verb for verb in VERBS if verb not in {v["verb"] for v in verbs}]
        if missing:
            block["missing_verbs"] = missing
        return block

    def _recent_block(self, limit: int) -> dict[str, Any]:
        bound = max(1, min(int(limit), RECENT_LIMIT_MAX))
        try:
            records = ledger.ExecutionLedger(
                ledger.ledger_path(self.hermes_home)
            ).read_all()
        except Exception:  # noqa: BLE001 - a corrupt ledger is reported as corrupt
            block = _block("unavailable", "LEDGER_CORRUPT", "world_execution_ledger")
            block.update({"limit": bound, "items": []})
            return block
        latest: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for record in records:
            execution_id = str(record.get("execution_id") or "")
            if not execution_id:
                continue
            if execution_id not in latest:
                order.append(execution_id)
            latest[execution_id] = record
        items: list[dict[str, Any]] = []
        for execution_id in list(reversed(order))[:bound]:
            record = latest[execution_id]
            raw = record.get("raw") or {}
            refs = [str(item) for item in raw.get("cross_authority_refs") or []]
            items.append(
                {
                    "execution_id": execution_id,
                    "action_type": record.get("action_type"),
                    "state": raw.get("status"),
                    "result": record.get("result"),
                    "reason_code": record.get("reason_code"),
                    "occurred_at": record.get("timestamp"),
                    "world_mutation_refs": list(raw.get("world_mutation_refs") or []),
                    "local_owner": any(
                        ref.startswith("world.local_owner_phone:") for ref in refs
                    ),
                }
            )
        block = _block("available", source="world_execution_ledger")
        block.update({"limit": bound, "items": items})
        return block

    # =====================================================================
    # act() -- the one write path
    # =====================================================================

    def _normalize_params(
        self, action: str, params: Any
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """``(normalized_params, reason_code)``; exactly one of them is ``None``."""

        required, optional = PARAM_SPEC[action]
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            return None, "INVALID_PARAMS"
        keys = set(params)
        forbidden = keys & FORBIDDEN_PARAM_KEYS
        if forbidden:
            return None, "INVALID_PARAMS"
        allowed = set(required) | set(optional)
        if keys - allowed:
            return None, "INVALID_PARAMS"
        if set(required) - keys:
            return None, "INVALID_PARAMS"

        normalized: dict[str, Any] = {}
        if action == "MOVE":
            target = _bounded(params.get("destination_location_id"), maximum=MAX_MOVE_TARGET)
            if target is None:
                return None, "INVALID_PARAMS"
            try:
                target = skeleton.normalize_location_id(
                    target, hermes_home=self.hermes_home, environment=self.environment
                )
            except Exception:  # noqa: BLE001 - keep the literal id, the owner judges
                pass
            normalized["destination_location_id"] = target
        elif action == "PICK_UP":
            object_id = _bounded(params.get("object_id"))
            slot_id = _bounded(params.get("slot_id"), maximum=32)
            if object_id is None or slot_id not in substrate.HAND_SLOTS:
                return None, "INVALID_PARAMS"
            normalized["object_id"] = object_id
            normalized["slot_id"] = slot_id
        elif action == "PLACE":
            object_id = _bounded(params.get("object_id"))
            if object_id is None:
                return None, "INVALID_PARAMS"
            try:
                target = substrate.validate_placement_target(params.get("target"))
            except substrate.BodySubstrateError:
                return None, "INVALID_PARAMS"
            normalized["object_id"] = object_id
            normalized["target"] = target
        elif action == "POSE":
            pose = _bounded(params.get("pose"), maximum=32)
            if pose is None:
                return None, "INVALID_PARAMS"
            pose = pose.lower()
            if pose not in substrate.POSES:
                return None, "INVALID_PARAMS"
            normalized["pose"] = pose
            orientation = params.get("orientation")
            if orientation is not None:
                orientation = _bounded(orientation, maximum=32)
                if orientation is None or orientation.lower() not in substrate.ORIENTATIONS:
                    return None, "INVALID_PARAMS"
                normalized["orientation"] = orientation.lower()
        elif action == "REST":
            state = _bounded(params.get("state"), maximum=16)
            if state is None or state.lower() not in ("start", "stop"):
                return None, "INVALID_PARAMS"
            state = state.lower()
            normalized["state"] = state
            mode = params.get("mode")
            if mode is not None:
                mode = _bounded(mode, maximum=16)
                if mode is None or mode.lower() not in ("rest", "sleep_like"):
                    return None, "INVALID_PARAMS"
                if state != "start":
                    return None, "INVALID_PARAMS"
                normalized["mode"] = mode.lower()
            elif state == "start":
                normalized["mode"] = "rest"
        elif action == "ARRANGE":
            object_id = _bounded(params.get("object_id"))
            if object_id is None:
                return None, "INVALID_PARAMS"
            try:
                target = substrate.validate_placement_target(params.get("target"))
            except substrate.BodySubstrateError:
                return None, "INVALID_PARAMS"
            if target.get("kind") not in (
                substrate.PLACEMENT_ROOM,
                substrate.PLACEMENT_SURFACE,
            ):
                return None, "INVALID_PARAMS"
            normalized["object_id"] = object_id
            normalized["target"] = target
        elif action == "TOGGLE_FIXTURE":
            object_id = _bounded(params.get("object_id"))
            state = _bounded(params.get("state"), maximum=8)
            if object_id is None or state is None or state.lower() not in (
                "on",
                "off",
            ):
                return None, "INVALID_PARAMS"
            normalized["object_id"] = object_id
            normalized["state"] = state.lower()
        return normalized, None

    def _frame(self, execution_id: str) -> dict[str, Any]:
        try:
            return ledger.ExecutionLedger(ledger.ledger_path(self.hermes_home)).frame(
                execution_id
            )
        except Exception:  # noqa: BLE001
            return {"known": False, "state": "corrupt", "records": []}

    def _replay_evidence(
        self, frame: Mapping[str, Any], action: str, params: Mapping[str, Any]
    ) -> str:
        """``matched`` / ``unmatched`` / ``unverified`` for a committed replay.

        Judged only from what the ledger already recorded (the entry's
        ``before``/``after``/``world_mutation_refs`` plus the owner's travel and
        placement facts) -- no second parameter store exists or is created.
        """

        records = frame.get("records") or []
        raw = (records[-1] or {}).get("raw") if records else None
        if not isinstance(raw, Mapping):
            return "unverified"
        refs = [str(item) for item in raw.get("world_mutation_refs") or []]
        after = raw.get("after") if isinstance(raw.get("after"), Mapping) else {}
        execution_id = frame.get("execution_id")

        if action == "MOVE":
            target = params.get("destination_location_id")
            if any(ref.startswith("world.travel=") for ref in refs):
                record = self._travel_record(execution_id)
                if record is None:
                    return "unverified"
                return (
                    "matched"
                    if str(record.get("destination_location_id")) == str(target)
                    else "unmatched"
                )
            for ref in refs:
                if ref.startswith("world.location="):
                    return "matched" if ref.split("=", 1)[1] == str(target) else "unmatched"
            return "unverified"
        if action == "POSE":
            if not after.get("pose"):
                return "unverified"
            if str(after.get("pose")) != str(params.get("pose")):
                return "unmatched"
            orientation = params.get("orientation")
            if orientation is not None and str(after.get("orientation")) != str(orientation):
                return "unmatched"
            return "matched"
        if action == "PICK_UP":
            hands = after.get("hands")
            if not isinstance(hands, Mapping) or params.get("slot_id") not in hands:
                return "unverified"
            return (
                "matched"
                if str(hands.get(params.get("slot_id"))) == str(params.get("object_id"))
                else "unmatched"
            )
        if action == "PLACE":
            placements = after.get("placements")
            if not isinstance(placements, Mapping):
                return "unverified"
            recorded = placements.get(str(params.get("object_id")))
            if not isinstance(recorded, Mapping):
                return "unverified"
            return "matched" if dict(recorded) == dict(params.get("target") or {}) else "unmatched"
        if action == "REST":
            if params.get("state") == "start":
                wanted = "recovery:start:%s" % params.get("mode", "rest")
                if any(ref.startswith("recovery:start:") for ref in refs):
                    return "matched" if wanted in refs else "unmatched"
                return "unverified"
            if any(ref.startswith("recovery:stop") for ref in refs):
                return "matched"
            return "unverified"
        if action == "ARRANGE":
            placements = after.get("placements")
            if not isinstance(placements, Mapping):
                return "unverified"
            recorded = placements.get(str(params.get("object_id")))
            if not isinstance(recorded, Mapping):
                return "unverified"
            return (
                "matched"
                if dict(recorded) == dict(params.get("target") or {})
                else "unmatched"
            )
        if action == "TOGGLE_FIXTURE":
            fixtures = after.get("fixtures")
            if not isinstance(fixtures, Mapping):
                return "unverified"
            recorded = fixtures.get(str(params.get("object_id")))
            if not isinstance(recorded, Mapping):
                return "unverified"
            return (
                "matched"
                if str(recorded.get("power")) == str(params.get("state"))
                else "unmatched"
            )
        return "unverified"

    def _observed_dependencies(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        response = self._gateway_call("get_snapshot")
        if response["outcome"] != "OK" or not isinstance(response.get("result"), Mapping):
            return None, "WORLD_BODY_SERVICE_UNAVAILABLE"
        view = response["result"]
        location = view.get("location") or {}
        dependencies = {
            "world.revision": view.get("world_revision"),
            "world.location": location.get("place_id"),
            "body.pose": view.get("pose"),
        }
        if any(value is None for value in dependencies.values()):
            return None, "WORLD_BODY_SERVICE_UNAVAILABLE"
        return dependencies, None

    def _issue_authorization(
        self, *, execution_id: str, key: str, action: str, params: Mapping[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Issue the one-shot ticket for this exact execution/verb/args.

        The registry is the service's own file in its run directory -- the same
        store the Intent Executor uses.  This side only *requests* the
        capability; the service still verifies and consumes it.
        """

        try:
            import world_action_authorization as waa
        except ImportError:  # pragma: no cover - flat/packaged import spelling
            return None
        if self.gateway_socket is None:
            return None
        import hashlib

        try:
            registry = waa.AuthorizationRegistry(self.gateway_socket.parent)
            return registry.issue(
                decision_id=None,
                intent_id="local-owner-phone:%s" % key,
                intent_fingerprint="sha256:%s"
                % hashlib.sha256(
                    ("%s|%s" % (action, key)).encode("utf-8")
                ).hexdigest()[:32],
                execution_id=execution_id,
                actor=economy.OWNER,
                verb=action,
                params=dict(params),
                authority_generation=None,
            )
        except (OSError, ValueError):
            return None

    def act(
        self,
        action: Any,
        params: Any = None,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """Submit one local-owner command, bound to exactly one operation key."""

        blocked = self._guard()
        if blocked is not None:
            return blocked

        key = _bounded(operation_key, maximum=MAX_OPERATION_KEY)
        if key is None:
            return _fail(STATUS_REJECTED, "INVALID_OPERATION_KEY")
        name = str(action or "").strip().upper()
        if name not in VERBS:
            return _fail(
                STATUS_REJECTED,
                "UNKNOWN_WORLD_ACTION",
                {"local_owner_verbs": list(VERBS)},
            )
        normalized, reason = self._normalize_params(name, params)
        if normalized is None:
            return _fail(STATUS_REJECTED, reason or "INVALID_PARAMS", {"action": name})

        execution_id = execution_id_for(key)

        # 1. one key names one operation: a key already spent on a *purchase* may
        #    not become a walk (the economy keeps its own intent ids).
        try:
            purchase = economy.find_purchase_intent(key, hermes_home=self.hermes_home)
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if purchase is not None:
            return _fail(
                STATUS_REJECTED,
                "OPERATION_KEY_CONFLICT",
                {
                    "operation": "purchase",
                    "transaction_id": purchase.get("transaction_id"),
                    "item_instance_id": purchase.get("item_instance_id"),
                },
            )

        # 2. and a key already spent on *this* ledger may not mean something else.
        frame = self._frame(execution_id)
        if frame.get("state") == "corrupt":
            return _fail(STATUS_UNAVAILABLE, "LEDGER_CORRUPT")
        if frame.get("known"):
            recorded_action = str(frame.get("action_type") or "")
            if recorded_action != name:
                return _fail(
                    STATUS_REJECTED,
                    "OPERATION_KEY_CONFLICT",
                    {
                        "operation": "use" if recorded_action == "CONSUME" else "world",
                        "execution_id": execution_id,
                        "action_type": recorded_action or None,
                        "requested_action": name,
                    },
                )
            ledger_state = str(frame.get("state") or "")
            if ledger_state == "committed":
                evidence = self._replay_evidence(frame, name, normalized)
                if evidence == "unmatched":
                    records = frame.get("records") or []
                    raw = (records[-1] or {}).get("raw") if records else {}
                    return _fail(
                        STATUS_REJECTED,
                        "OPERATION_TARGET_CONFLICT",
                        {
                            "execution_id": execution_id,
                            "action_type": recorded_action,
                            "recorded": {
                                "before": (raw or {}).get("before"),
                                "after": (raw or {}).get("after"),
                                "world_mutation_refs": list(
                                    (raw or {}).get("world_mutation_refs") or []
                                ),
                            },
                            "requested_params": dict(normalized),
                        },
                    )
                return _ok(
                    STATUS_ALREADY_APPLIED,
                    {
                        "execution_id": execution_id,
                        "action": name,
                        "operation_key": key,
                        "replay_evidence": evidence,
                        "travel": self._travel_aftermath(execution_id),
                        "body": self._body_aftermath(),
                    },
                )
            if ledger_state in ("prepared", "uncertain"):
                # Never guessed, never re-submitted with a new key.
                return _fail(
                    STATUS_UNKNOWN,
                    "RECONCILIATION_REQUIRED",
                    {
                        "execution_id": execution_id,
                        "action": name,
                        "operation_key": key,
                        "ledger_state": ledger_state,
                        "reconciliation_required": True,
                    },
                )
            return _fail(
                STATUS_REJECTED,
                str(frame.get("reason_code") or "OPERATION_FAILED"),
                {
                    "execution_id": execution_id,
                    "action": name,
                    "operation_key": key,
                    "ledger_state": ledger_state,
                },
            )

        # 3. the trusted local port.  No socket -> unavailable, never a start.
        if self.gateway_socket is None or not self.gateway_socket.exists():
            return _fail(
                STATUS_UNAVAILABLE, "WORLD_BODY_SERVICE_UNAVAILABLE", {"detail": "socket_missing"}
            )
        dependencies, reason = self._observed_dependencies()
        if dependencies is None:
            return _fail(STATUS_UNAVAILABLE, reason or "WORLD_BODY_SERVICE_UNAVAILABLE")

        # 4. one-shot ticket for exactly this execution/verb/args.
        issuance = self._issue_authorization(
            execution_id=execution_id, key=key, action=name, params=normalized
        )
        if issuance is None:
            return _fail(STATUS_UNAVAILABLE, "AUTHORIZATION_REGISTRY_UNAVAILABLE")

        response = self._gateway_call(
            LOCAL_OWNER_COMMAND,
            {
                "action": name,
                "execution_id": execution_id,
                "params": normalized,
                "observed_dependencies": dependencies,
                "authorization_id": issuance["authorization_id"],
            },
            timeout=DEFAULT_MUTATION_TIMEOUT,
        )
        if response["outcome"] == "UNAVAILABLE":
            return _fail(
                STATUS_UNAVAILABLE,
                "WORLD_BODY_SERVICE_UNAVAILABLE",
                {"detail": response.get("reason")},
            )
        if response["outcome"] == "TIMEOUT":
            # The commit may or may not have happened.  Never retried here with a
            # new key: the caller reconciles with the same operation key.
            return _fail(
                STATUS_UNKNOWN, "SERVICE_TIMEOUT", {"execution_id": execution_id}
            )
        payload = response.get("result")
        if not isinstance(payload, Mapping):
            return _fail(STATUS_UNAVAILABLE, "WORLD_BODY_SERVICE_UNAVAILABLE")

        status = str(payload.get("status") or "")
        data: dict[str, Any] = {
            "execution_id": payload.get("execution_id") or execution_id,
            "action": name,
            "operation_key": key,
            "reason_code": _reason_of(payload),
            "detail": payload.get("detail"),
            "world": self._world_aftermath(),
            "travel": self._travel_aftermath(payload.get("execution_id") or execution_id),
            "local_owner": payload.get("local_owner"),
            "body": self._body_aftermath(payload),
            "authoritative": payload.get("authoritative"),
            "source": payload.get("source"),
            "replay_evidence": "fresh",
        }
        if name == "MOVE":
            # A departure leaves the World position where it was; only the owner
            # tick's own arrival may change it.
            data["world"]["location_unchanged"] = (
                (data.get("travel") or {}).get("status") != "arrived"
            )
        if payload.get("consumption_receipt") is not None:
            data["receipt"] = payload["consumption_receipt"]
        if status == "applied":
            return _ok(STATUS_APPLIED, data)
        if status == "already_applied":
            data["replay_evidence"] = "matched"
            return _ok(STATUS_ALREADY_APPLIED, data)
        if status in ("uncertain",):
            return _fail(STATUS_UNKNOWN, "RECONCILIATION_REQUIRED", data)
        if status == "disabled":
            return _fail(STATUS_DISABLED, _reason_of(payload) or "FEATURE_DISABLED", data)
        return _fail(STATUS_REJECTED, _reason_of(payload) or "ACTION_REJECTED", data)

    def _travel_aftermath(self, execution_id: Any) -> Optional[dict[str, Any]]:
        record = self._travel_record(execution_id)
        if record is None:
            return None
        return self._travel_projection(record)

    def _body_aftermath(self, payload: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        payload = payload or {}
        body: dict[str, Any] = {
            "applied_to_body": payload.get("applied_to_body"),
            "consequence_reason": payload.get("consequence_reason"),
            "consequence_id": payload.get("consequence_id"),
            "movement_settlement": payload.get("movement_settlement"),
        }
        response = self._gateway_call("get_body_signals")
        if response["outcome"] == "OK" and isinstance(response.get("result"), Mapping):
            result = response["result"]
            body["recovery"] = result.get("recovery")
            body["recovery_status"] = "available" if result.get("available") else "unavailable"
        else:
            body["recovery"] = None
            body["recovery_status"] = "unavailable"
        return body

    # =====================================================================
    # operation() -- read-only reconciliation of one key
    # =====================================================================

    def operation(self, operation_key: Any) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        key = _bounded(operation_key, maximum=MAX_OPERATION_KEY)
        if key is None:
            return _fail(STATUS_REJECTED, "INVALID_OPERATION_KEY")

        try:
            purchase = economy.find_purchase_intent(key, hermes_home=self.hermes_home)
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if purchase is not None:
            data = {
                "operation": "purchase",
                "transaction_id": purchase.get("transaction_id"),
                "item_instance_id": purchase.get("item_instance_id"),
                "catalog_item_id": purchase.get("catalog_item_id"),
                "current_balance": purchase.get("current_balance"),
            }
            if purchase.get("status") == "unknown":
                return _fail(
                    STATUS_UNKNOWN,
                    purchase.get("reason_code") or "PURCHASE_LEDGER_INCONSISTENT",
                    data,
                )
            return _ok(STATUS_APPLIED, data)

        execution_id = execution_id_for(key)
        frame = self._frame(execution_id)
        if frame.get("state") == "corrupt":
            return _fail(STATUS_UNAVAILABLE, "LEDGER_CORRUPT")
        if not frame.get("known"):
            return _fail(STATUS_NOT_FOUND, "OPERATION_NOT_FOUND")

        records = frame.get("records") or []
        raw = (records[-1] or {}).get("raw") if records else {}
        action_type = str(frame.get("action_type") or "")
        data: dict[str, Any] = {
            "operation": "use" if action_type == "CONSUME" else "world",
            "execution_id": execution_id,
            "action_type": action_type or None,
            "ledger_state": frame.get("state"),
            "ledger_result": frame.get("result"),
            "reason_code": frame.get("reason_code"),
            "world_mutation_refs": list((raw or {}).get("world_mutation_refs") or []),
            "reconciliation_required": bool(frame.get("reconciliation_required")),
        }
        travel_record = self._travel_record(execution_id)
        if travel_record is not None:
            data["travel"] = self._travel_projection(travel_record)
        if frame.get("result") == ledger.RESULT_SUCCESS and frame.get("state") == "committed":
            return _ok(STATUS_APPLIED, data)
        if frame.get("result") == ledger.RESULT_FAILURE:
            return _fail(
                STATUS_REJECTED, str(frame.get("reason_code") or "OPERATION_FAILED"), data
            )
        return _fail(STATUS_UNKNOWN, "RECONCILIATION_REQUIRED", data)


def _reason_of(payload: Mapping[str, Any]) -> Optional[str]:
    """The narrow code a caller can act on, from a Resolver/service payload.

    The Resolver reports a canonical code plus a legacy detail
    (``NOT_ALLOWED`` + ``"REST_WHILE_TRAVELLING: ..."``); the phone contract
    promises the *specific* spelling, so the legacy prefix wins and its detail is
    kept separately.

    A legacy prefix of exactly ``UNKNOWN`` is **not** a code -- it is the old
    placeholder text (``"UNKNOWN: <id> has no placement"``) and reporting it as
    a reason throws away the only thing the caller could act on.  When the
    legacy side carries no real spelling, the canonical code is what is said
    (``UNKNOWN_TARGET`` is a fact; ``UNKNOWN`` is a shrug).
    """

    legacy = payload.get("legacy_reason_code")
    if isinstance(legacy, str) and legacy.strip():
        spelling = legacy.split(":", 1)[0].strip()
        if spelling and spelling != "UNKNOWN":
            return spelling
    reason = payload.get("reason_code")
    if isinstance(reason, str) and reason.strip():
        return reason.split(":", 1)[0].strip()
    if isinstance(legacy, str) and legacy.strip():
        return legacy.split(":", 1)[0].strip()
    return None


__all__ = [
    "PhoneWorld",
    "VERBS",
    "PARAM_SPEC",
    "SCHEMA_PHONE_WORLD",
    "KIND_PHONE_WORLD",
    "execution_id_for",
]
