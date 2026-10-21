#!/usr/bin/env python3
"""Gateway-facing World/Body surface (M13B).

Ticket sections 8-14, 18-22, 54-55.  This is deliberately a **separate, narrow**
interface from the M13A admin control surface:

============================  ==========================================
admin socket (``control.sock``)  gateway socket (``gateway.sock``)
============================  ==========================================
``act``  (raw ActionProposal)    ``submit_action`` (proposal minus
                                  authority/confirm -- service stamps
                                  authorisation itself)
``apply_result`` (**admin**;     absent -- the gateway must never be able
 bypasses action authority)      to fabricate an execution result
``reconcile``                    absent
``bootstrap`` / migration        absent
============================  ==========================================

Everything on this surface is bounded and read-only except ``submit_action``.
Observations are **qualitative and finite**: no raw somatic floats, no full
World JSON, no test fixture.

Object portability: the current World substrate contract has no canonical
"takeable" capability, so ``PICK_UP`` exposure for ordinary objects is **off**
and fail-closed here rather than fixed by inventing a rule (ticket 19-21).

Consumption (F): ``CONSUME {object_id}`` is a first-class Resolver verb, so the
gateway exposes it in ``get_available_actions`` and submits it through the same
one-shot authorization as every other verb.  Candidates come from the live
placement plus the world-owned ``consumables`` declaration block plus
``world_daily_rhythm``'s opening state; building that list writes nothing, mints
nothing and never touches Body.  One object instance = one consumption; the
result carries the owner receipt, and ``get_action_result`` reports it beside
the ledger verdict.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import stat
from typing import Any, Mapping, Optional, Sequence

SCHEMA_GATEWAY_API = "world.body.gateway.api.v1"
KIND_GATEWAY_API = "world_body_gateway"

GATEWAY_SOCKET_NAME = "gateway.sock"

#: Read-only surface.
GATEWAY_READ_COMMANDS = (
    "get_snapshot",
    "get_observation",
    "get_body_signals",
    "get_available_actions",
    "get_action_result",
    "get_status",
    "get_environment",
    "ping",
)
#: The one mutating command (ticket section 10).
# The one mutating command (ticket section 10).
GATEWAY_MUTATING_COMMANDS = ("submit_action",)
# v0.4: the explicit local-owner command.  Separate from submit_action so the
# authority it stamps is its own (local_owner_phone), never the main-self one,
# and so it can be switched off at the service on its own.
GATEWAY_LOCAL_OWNER_COMMANDS = ("submit_local_owner_action",)
GATEWAY_COMMANDS = (
    GATEWAY_READ_COMMANDS + GATEWAY_MUTATING_COMMANDS + GATEWAY_LOCAL_OWNER_COMMANDS
)
# Verbs the local-owner command may carry.  One tuple, one permission
# statement: this must stay identical to the Resolver's ``LOCAL_OWNER_ACTIONS``
# (a drift would make one side wider than the other).  CONSUME keeps the phone
# shopping channel; MOVE/PICK_UP/PLACE/POSE/REST are the resident-world verbs the
# phone now owns -- each still goes through the same one-shot authorization,
# the same Resolver lock/revision/postcondition chain and the same ledger.
GATEWAY_LOCAL_OWNER_VERBS = (
    "MOVE",
    "PICK_UP",
    "PLACE",
    "POSE",
    "CONSUME",
    "REST",
    "ARRANGE",
    "TOGGLE_FIXTURE",
)
GATEWAY_LOCAL_OWNER_SOURCE = "local_owner_phone"
GATEWAY_LOCAL_OWNER_AUTHORITY = "local_owner_phone"
# Explicit, server-side opt-in: its value is the bound subject. Unset means
# the local-owner command is not bound at all (fail closed).
GATEWAY_LOCAL_OWNER_BINDING_ENV = "CHIYO_WORLD_LOCAL_OWNER_PHONE"
_LOCAL_OWNER_REQUEST_KEYS = frozenset(
    {
        "action",
        "verb",
        "execution_id",
        "params",
        "observed_dependencies",
        "authorization_id",
        "decision_ref",
    }
)
# Anything that looks like a permission claim is refused: the service stamps
# these, a client may not supply them.
_LOCAL_OWNER_FORBIDDEN_KEYS = frozenset(
    {
        "authority",
        "confirm_apply",
        "main_self_verified",
        "verified_main_self",
        "uid",
        "gid",
        "binding",
        "source",
        "permission",
        "home",
    }
)

#: Explicitly refused, so a probing caller gets a clear answer rather than a
#: generic "unknown command" (ticket section 9).
GATEWAY_FORBIDDEN_COMMANDS = (
    "apply_result",
    "set_body_state",
    "set_fatigue",
    "force_placement",
    "edit_world",
    "bootstrap",
    "migration",
    "reconcile",
    "act",
)

#: Authorisation the *service* stamps onto a gateway submission.  A caller may
#: not supply this (ticket section 55) -- it is derived from the request
#: envelope, which the socket permissions are what actually protect.
GATEWAY_AUTHORITY = "verified_main_self_tool"
GATEWAY_ACTOR = "chiyo"
GATEWAY_CALLER = "hermes_gateway"

# --- M14A: action socket authority firewall -------------------------------
try:
    import world_action_authorization as waa
except ImportError:  # pragma: no cover - direct-load contexts
    import pathlib as _pl
    import sys as _sys
    _HERE = str(_pl.Path(__file__).resolve().parent)
    if _HERE not in _sys.path:
        _sys.path.insert(0, _HERE)
    import world_action_authorization as waa

#: Unix credential of the only process allowed to call the gateway surface.
#: Captured via SO_PEERCRED by the service loop (transport hardening layer).
GATEWAY_ALLOWED_PEER_UID = 0  # root; the whole deployment runs as root

#: M15: poses that can manipulate (mirrors world_body_substrate._HANDS_USABLE_POSES;
#: the substrate remains the owner of the rule).
_HANDS_USABLE_POSES = ("standing", "seated")

#: World objects that are the M13A.1 acceptance fixture, not Chiyo's property.
TEST_OBJECT_PREFIXES = ("M13A1_TEST_OBJECT", "M13A1_")
TEST_OBJECT_MARKER = "M13A1 acceptance fixture"

#: Poses/orientations, mirrored for the action-proposal vocabulary.
POSES = ("standing", "seated", "lying", "transitioning")
ORIENTATIONS = ("north", "south", "east", "west", "up", "down")
HAND_SLOTS = ("left_hand", "right_hand")

MAX_TEXT = 240

#: v0.4: a body can only rest from a resting pose (World owns posture; the
#: Resolver enforces the same tuple).  Mirrored, never re-decided here.
RESTING_POSES = ("seated", "lying")

class GatewayAPIError(ValueError):
    """The gateway asked for something the narrow interface does not offer."""


def _text(value: Any, label: str, *, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GatewayAPIError("%s must be a non-empty string" % label)
    text = value.strip()
    if len(text) > maximum:
        raise GatewayAPIError("%s is too long" % label)
    return text


def is_test_object(object_id: Any) -> bool:
    """True for the acceptance fixture, which production observation hides."""

    if not isinstance(object_id, str):
        return False
    return object_id.startswith(TEST_OBJECT_PREFIXES) or TEST_OBJECT_MARKER in object_id


def _normalized_local_owner_binding(value: Any) -> Optional[dict[str, Any]]:
    """Normalize the *server-side* local-owner binding, or ``None``.

    ``None`` means "this service is not allowed to accept local-owner
    commands".  A malformed binding is ``None`` too: a half-configured binding
    must never widen the surface.
    """

    if not isinstance(value, Mapping):
        return None
    source = value.get("source")
    uid = value.get("uid")
    subject = value.get("subject")
    home = value.get("world_home")
    if source != GATEWAY_LOCAL_OWNER_SOURCE:
        return None
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        return None
    if not isinstance(subject, str) or not subject.strip():
        return None
    if not isinstance(home, str) or not home.strip():
        return None
    return {
        "source": GATEWAY_LOCAL_OWNER_SOURCE,
        "uid": uid,
        "subject": subject.strip(),
        "world_home": home.strip(),
    }


def socket_permissions(path: pathlib.Path) -> dict[str, Any]:
    try:
        info = path.stat()
    except OSError as error:
        return {"exists": False, "error": str(error)}
    return {
        "exists": True,
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


# --------------------------------------------------------------------------
# the surface
# --------------------------------------------------------------------------


class GatewaySurface:
    """Read + submit_action over a bounded view of the running service."""

    def __init__(
        self,
        service: Any,
        *,
        include_test_objects: bool = False,
        local_owner_binding: Optional[Mapping[str, Any]] = None,
    ):
        self.service = service
        self.include_test_objects = bool(include_test_objects)

        self.include_test_objects = bool(include_test_objects)
        # M14A: registry of one-shot action authorizations (issued by the
        # Intent Executor side, consumed here; the surface never issues).
        # Defensive: a bare/stub service (admin-surface tests, direct use)
        # may have no config; the firewall then rejects every submission
        # instead of making the surface unconstructible.
        _run_dir = None
        try:
            _run_dir = getattr(service.config, "run_dir")
        except Exception:  # pragma: no cover - stub services
            _run_dir = None
        self.authorizations = waa.AuthorizationRegistry(_run_dir) if _run_dir else None
        # v0.4: the local-owner (phone) command exists only when the *service*
        # was explicitly bound to a subject.  A binding that is not
        # well-formed is treated as "not bound" (fail closed).
        self.local_owner_binding = _normalized_local_owner_binding(
            local_owner_binding
            if local_owner_binding is not None
            else getattr(service, "local_owner_phone_binding", None)
        )

    # ---- helpers ---------------------------------------------------------
    def _substrate(self) -> Optional[dict[str, Any]]:
        service = self.service
        try:
            store = service.wbs.SubstrateStore(service.wbs.substrate_path(service.config.world_home))
            return store.load_with_migration()[0]
        except Exception:
            return None
    def _world_live(self) -> dict[str, Any]:
        """The World state as it is on disk *now*.

        M13A.1 created the substrate, but before that nothing could move the
        World, so the service cached `world_state` at startup.  Now that MOVE
        works through the Resolver the cache goes stale on the first successful
        move -- and a gateway that reports a stale location is a gateway that
        lies to the model.  Always read the live file; fall back to the cache
        only if the file is momentarily unreadable.
        """

        service = self.service
        try:
            import json as _json

            return _json.loads(service.config.world_state_path.read_text(encoding="utf-8"))
        except Exception:
            return dict(service.world_state or {})

    def _visible_object_ids(self) -> list[str]:
        substrate = self._substrate() or {}
        ids = sorted((substrate.get("placements") or {}).keys())
        if self.include_test_objects:
            return ids
        return [i for i in ids if not is_test_object(i)]

    def _takeability(self, object_id: str) -> dict[str, Any]:
        """M15: object capability + live placement verdict (per object).

        Capability comes from the object's canonical identity (economy catalog
        entry, or a declared smoke/fixture object) via the binding join.  An
        object with no declared capability reads UNKNOWN and is NOT takeable --
        never assumed.
        """

        substrate = self._substrate() or {}
        placement = (substrate.get("placements") or {}).get(object_id)
        capability = self._object_capability(object_id)
        takeable = str(capability.get("takeable") or "UNKNOWN").lower() == "true"

        verdict = {
            "object_id": object_id,
            "known": placement is not None,
            "capability": capability,
            "takeable": takeable,
            "placement_kind": (placement or {}).get("kind"),
            "reason": None,
        }
        if placement is None:
            verdict["reason"] = "OBJECT_NOT_FOUND"
        elif placement.get("kind") == "hand":
            verdict["takeable"] = False
            verdict["reason"] = "ALREADY_HELD"
        elif not takeable:
            verdict["reason"] = "NOT_PORTABLE"
        return verdict

    # ---- M15: real availability truth -------------------------------------
    def _object_capability(self, object_id: str) -> dict[str, Any]:
        """The object's canonical capability block (binding join, never invented)."""

        try:
            import world_body_binding as binding
            return binding.object_capability(self.service.config.world_home, object_id)
        except Exception:
            return {}

    def _pick_up_availability(self, *, held: list[str]) -> dict[str, Any]:
        """M15 §17: the physical truth about PICK_UP right now.

        The intent mutation gate is a *gateway* concept; this service owns
        placements and capabilities, so it answers with what it can see:
        capability / hands / reachability.  The bridge labels the model-facing
        space RUNTIME_DISABLED when its own gate is off.
        """

        substrate = self._substrate() or {}
        placements = substrate.get("placements") or {}
        hands = {slot: None for slot in HAND_SLOTS}
        for object_id, target in placements.items():
            if str(target.get("kind")) == "hand" and target.get("slot_id") in hands:
                hands[str(target["slot_id"])] = object_id
        free = [slot for slot, occupant in hands.items() if occupant is None]
        if not free:
            return {"available": False, "blocked_reason": "HAND_OCCUPIED",
                    "detail": "every hand slot is occupied", "candidates": []}

        # M15: the pose rule -- the same one the Resolver enforces.  A body that
        # cannot manipulate must never be told PICK_UP is available.
        pose = (substrate.get("pose") or "").strip()
        if pose not in _HANDS_USABLE_POSES:
            return {"available": False, "blocked_reason": "NOT_REACHABLE",
                    "detail": "pose %s cannot manipulate" % pose, "candidates": []}

        here = (self._world_live() or {}).get("location", {}).get("place_id") \
            if isinstance(self._world_live(), Mapping) else None
        takeable_ids, elsewhere_ids = [], []
        for object_id, target in sorted(placements.items()):
            if str(target.get("kind")) == "hand":
                continue
            if is_test_object(object_id) and not self.include_test_objects:
                continue
            if not self._takeability(object_id).get("takeable"):
                continue
            if target.get("location_id") == here:
                takeable_ids.append(object_id)
            else:
                elsewhere_ids.append(object_id)

        if not takeable_ids:
            reason = "NOT_REACHABLE" if elsewhere_ids else "NO_TAKEABLE_OBJECT"
            return {"available": False, "blocked_reason": reason,
                    "detail": ("takeable objects exist but not in %s" % here)
                    if elsewhere_ids else "no object in this world declares takeable=true",
                    "candidates": []}

        return {"available": True, "blocked_reason": None, "detail": None,
                "candidates": [{"object_id": o, "slots": free} for o in takeable_ids]}

    # ---- v0.4 REST: is a recovery command legal *right now*? -------------
    def _travel_in_progress(self) -> Optional[dict[str, Any]]:
        """The owner's in-transit record (read-only), or ``None``."""

        try:
            import world_travel_delivery as wtd
        except Exception:  # noqa: BLE001 - no travel module means not travelling
            return None
        try:
            if not wtd.travel_enabled(getattr(self.service.config, "environment", None)):
                return None
            state = wtd.load_state(
                self.service.config.world_home,
                getattr(self.service.config, "environment", None),
            )
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(state, Mapping):
            return None
        for item in state.get("travels") or []:
            if isinstance(item, Mapping) and item.get("status") == wtd.TRAVEL_STATUS_IN_PROGRESS:
                return dict(item)
        return None

    def _rest_surface_here(self, substrate: Mapping[str, Any]) -> Optional[str]:
        """The declared rest surface really placed where she is (or ``None``).

        Derived from live placements: the legacy bare ``bed_surface`` placement
        and the instance-scoped surface of an owned bed both count, and a bed in
        another area does not.
        """

        here = (self._world_live().get("location") or {}).get("place_id")
        if not here:
            return None
        try:
            import world_body_binding as binding

            catalog = binding.owned_catalog_map(self.service.config.world_home)
        except Exception:  # noqa: BLE001 - no join means no rest surface
            return None
        try:
            surfaces = self.service.wbs.rest_surfaces_here(
                substrate, catalog, location_id=here, mode="sleep_like"
            )
        except Exception:  # noqa: BLE001
            return None
        return surfaces[0]["surface_id"] if surfaces else None

    def _rest_availability(self) -> dict[str, Any]:
        """The physical + Body truth behind REST (bands only, zero raw values).

        Everything here is a *capability fact*: travelling, posture, whether a
        bed is really here, whether a session is already open, and whether the
        Body says rest is needed at all.  The fatigue **value** never leaves this
        method -- only the verdict.
        """

        service = self.service
        substrate = self._substrate() or {}
        pose = str(substrate.get("pose") or "")
        travelling = self._travel_in_progress() is not None
        surface_id = self._rest_surface_here(substrate)
        body = service.body_state or {}
        recovery = body.get("recovery") or {}
        recovery_state = str(recovery.get("state") or "inactive")
        active = recovery_state == "active"
        try:
            import body_runtime as br

            threshold = float(br.RECOVERY_OPEN_FATIGUE)
        except Exception:  # noqa: BLE001 - no runtime means no verdict
            threshold = None
        fatigue: Optional[float] = None
        try:
            fatigue = float((body.get("somatic") or {}).get("fatigue"))
        except (TypeError, ValueError):
            fatigue = None
        needs = fatigue is not None and threshold is not None and fatigue >= threshold
        resting_pose = pose in RESTING_POSES
        start_available = bool(not active and not travelling and resting_pose and needs)
        sleep_like_available = bool(start_available and pose == "lying" and surface_id)

        blocked: Optional[str] = None
        detail: Optional[str] = None
        if active:
            blocked = "RECOVERY_ALREADY_ACTIVE"
            detail = "a recovery session is already open; stop it instead"
        elif travelling:
            blocked = "TRAVELLING"
            detail = "a walk is in progress; rest is not available while moving"
        elif not resting_pose:
            blocked = "REST_REQUIRES_RESTING_POSE"
            detail = "pose %s cannot rest; sit down or lie down first" % (pose or "unknown")
        elif fatigue is None or threshold is None:
            blocked = "BODY_STATE_UNAVAILABLE"
            detail = "the Body runtime cannot answer whether rest is needed"
        elif not needs:
            blocked = "RECOVERY_NOT_NEEDED"
            detail = "fatigue is below the recovery threshold; rest is not needed right now"

        return {
            "available": bool(start_available or active),
            "blocked_reason": None if (start_available or active) else blocked,
            "detail": None if (start_available or active) else detail,
            "start_available": start_available,
            "stop_available": active,
            # Per-direction reasons, so "stop what is not open" and "start when
            # it is not needed" are never reported as the same thing.
            "start_blocked_reason": None if start_available else blocked,
            "start_detail": None if start_available else detail,
            "stop_blocked_reason": None if active else "NO_OPEN_RECOVERY",
            "sleep_like_available": sleep_like_available,
            "candidates": [],
            "current": {
                "pose": pose or None,
                "recovery_state": recovery_state,
                "travelling": travelling,
                "rest_surface": surface_id,
            },
        }
    # ---- F: consumable projection (read-only, no ticket, no body) ---------
    def _economy_facts(self) -> dict[str, Any]:
        """Property-economy facts for this home: absent / present / unreadable.

        The tri-state read lives in ``world_consumable_contract`` (one owner for
        this rule).  The gateway must not decide it locally, and must never turn
        an unreadable document into an empty one: "not an economy object" is a
        permission claim that needs readable facts.
        """

        try:
            import world_consumable_contract as contract

            return contract.economy_facts(self.service.config.world_home)
        except Exception as error:  # pragma: no cover - contract not deployed
            return {
                "status": "unreadable",
                "economy": None,
                "reason_code": "ECONOMY_FACTS_UNAVAILABLE",
                "detail": "consumable contract unavailable (%s)" % type(error).__name__,
            }

    def _consume_availability(self) -> dict[str, Any]:
        """What could be consumed right now, from the owners that decide it.

        Read-only by construction: the live placement, the world-owned
        ``consumables`` declaration block and ``world_daily_rhythm``'s opening
        state.  Nothing is written here -- no placement change, no authorization
        ticket, no Body consequence.
        """

        empty = {"available": False, "blocked_reason": None, "detail": None,
                 "candidates": [], "declarations_present": False}
        try:
            import world_consumable_contract as contract
        except Exception:  # pragma: no cover - contract not deployed
            return dict(empty, blocked_reason="CONSUMABLE_CONTRACT_UNAVAILABLE")

        substrate = self._substrate() or {}
        placements = substrate.get("placements") or {}
        world = self._world_live() or {}
        here = (world.get("location") or {}).get("place_id")
        declarations = contract.load_consumable_declarations(
            self.service.config.world_home
        )
        economy = self._economy_facts()
        if economy.get("status") == contract.ECONOMY_UNREADABLE:
            # Nothing can be projected: an unreadable economy might own this very
            # instance, so no candidate may be offered.
            return {
                "available": False,
                "blocked_reason": contract.ECONOMY_FACTS_UNAVAILABLE,
                "detail": economy.get("detail"),
                "candidates": [],
                "declarations_present": bool(declarations),
            }
        labels = self._labels()
        hands_usable = str(substrate.get("pose") or "").strip() in _HANDS_USABLE_POSES

        candidates: list[dict[str, Any]] = []
        reasons: list[str] = []
        for object_id in sorted(placements):
            if is_test_object(object_id) and not self.include_test_objects:
                continue
            verdict = contract.consumption_verdict(
                object_id=object_id,
                placement=placements[object_id],
                declaration=(
                    declarations.get(object_id)
                    or contract.declaration_of(
                        object_id,
                        self.service.config.world_home,
                        economy=economy.get("economy"),
                    )
                ),
                economy=economy,
                current_place=here,
                hands_usable=hands_usable,
                now=self.service._moment(),
                environment=getattr(self.service.config, "environment", None),
            )
            if verdict["allowed"]:
                placement = placements[object_id] or {}
                candidates.append(
                    {
                        "object_id": object_id,
                        "label": labels.get(object_id) or object_id,
                        "source": verdict["source"],
                        "placement_kind": placement.get("kind"),
                    }
                )
            elif verdict["reason_code"] != contract.NOT_DECLARED:
                reasons.append(verdict["reason_code"])

        if candidates:
            return {
                "available": True,
                "blocked_reason": None,
                "detail": None,
                "candidates": candidates,
                "declarations_present": bool(declarations),
            }

        priority = (
            # A broken economy outranks every other reason: it is the reason no
            # other judgement can be trusted.
            "ECONOMY_FACTS_UNAVAILABLE",
            "LOCATION_CLOSED",
            "NOT_HELD_OR_HERE",
            "POSE_CANNOT_CONSUME",
            "NOT_FREE",
            "NOT_CONSUMABLE",
            "ECONOMY_OBJECT",
            "DECLARATION_INVALID",
            "PLACEMENT_KIND_NOT_CONSUMABLE",
            "RHYTHM_UNAVAILABLE",
        )
        blocked = next((code for code in priority if code in reasons), None)
        detail = None
        if blocked is None:
            blocked = "NO_CONSUMABLE_OBJECT"
            detail = ("no object in this world declares consumable=true and "
                      "free_to_consume=true")
        return {
            "available": False,
            "blocked_reason": blocked,
            "detail": detail,
            "candidates": [],
            "declarations_present": bool(declarations),
        }

    # ---- read ------------------------------------------------------------
    def get_status(self) -> dict[str, Any]:
        service = self.service
        readiness = service.readiness()
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "ready": readiness["status"] == "READY",
            "readiness": readiness["status"],
            "readiness_checks": readiness["checks"],
            "world_id": self._world_live().get("world_id"),
            "world_revision": self._world_live().get("revision"),
            "body_id": (service.body_state or {}).get("body_id"),
            "socket_permissions": socket_permissions(service.gateway_socket_path)
            if getattr(service, "gateway_socket_path", None) else None,
        }

    def get_snapshot(self) -> dict[str, Any]:
        """Finite, grounded world+body snapshot (ticket sections 37-38)."""

        service = self.service
        world = self._world_live()
        substrate = self._substrate() or {}
        location = world.get("location") or {}

        placements = substrate.get("placements") or {}
        hands: dict[str, Optional[str]] = {slot: None for slot in HAND_SLOTS}
        for object_id, placement in placements.items():
            if placement.get("kind") == "hand" and placement.get("slot_id") in hands:
                hands[placement["slot_id"]] = object_id
        if not self.include_test_objects:
            hands = {slot: (None if is_test_object(held) else held)
                     for slot, held in hands.items()}
        # A readable label instead of a 70-char instance id: the chat model
        # should see "basic_bed", not `item-8ad09561...`.  The join is with the
        # property economy in this same home, so it is bounded and local.
        labels: dict[str, str] = {}
        try:
            economy = json.loads(
                (service.config.world_home / "state" / "world_property_economy.json")
                .read_text(encoding="utf-8")
            )
            for item in economy.get("items") or []:
                if isinstance(item, dict) and item.get("item_instance_id"):
                    labels[item["item_instance_id"]] = str(
                        item.get("catalog_item_id") or "item"
                    )
        except Exception:
            labels = {}

        visible: list[dict[str, Any]] = []
        for object_id in self._visible_object_ids():
            placement = placements.get(object_id) or {}
            visible.append({
                "object_id": object_id,
                "label": labels.get(object_id) or object_id,
                "kind": placement.get("kind"),
                "surface_id": placement.get("surface_id"),
                "container_id": placement.get("container_id"),
                "slot_id": placement.get("slot_id"),
                "in_hand": placement.get("kind") == "hand",
            })
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "world_id": world.get("world_id"),
            "world_revision": world.get("revision"),
            "location": {"place_id": location.get("place_id"), "area_id": location.get("area_id")},
            "scene_id": (world.get("scene") or {}).get("scene_id"),
            "pose": substrate.get("pose"),
            "orientation": substrate.get("orientation"),
            "hands": hands,
            "visible_objects": visible,
            "visible_object_count": len(visible),
            "observation_scope": "finite",
            "includes_test_objects": self.include_test_objects,
            "full_world_json_exposed": False,
        }

    def get_observation(self) -> dict[str, Any]:
        """The bounded observation the chat may be grounded on (section 13)."""

        snapshot = self.get_snapshot()
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "world_id": snapshot["world_id"],
            "location": snapshot["location"],
            "pose": snapshot["pose"],
            "hands_occupied": sorted(
                slot for slot, held in (snapshot.get("hands") or {}).items() if held
            ),
            "visible_object_count": snapshot["visible_object_count"],
            "note": "bounded observation only; no raw body values, no full world JSON",
        }

    def get_body_signals(self) -> dict[str, Any]:
        """Bounded BodySignal -- never raw floats (ticket section 14)."""

        service = self.service
        state = service.body_state
        if not state:
            return {
                "kind": KIND_GATEWAY_API,
                "schema_version": SCHEMA_GATEWAY_API,
                "available": False,
                "signals": [],
                "raw_values_exposed": False,
            }
        import body_signal as bsi

        produced = bsi.produce_signals(state, occurred_at=service._moment())
        # body_summary() already proves the qualitative shape; re-derive here
        # through the service so the gateway never touches body_runtime.
        summary = service.body_summary()
        recovery = dict(summary.get("recovery") or {})
        # v0.4: the progress of an open session is reported as the existing
        # three-band vocabulary, never as a percentage or a float.
        progress = (state.get("recovery") or {}).get("progress")
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            recovery["progress_band"] = (
                bsi.BAND_MILD
                if progress < 0.34
                else bsi.BAND_NOTICEABLE
                if progress < 0.67
                else bsi.BAND_STRONG
            )
        else:
            recovery["progress_band"] = None
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "available": True,
            "recovery": recovery,
            "latch": produced.get("latch"),
            "signals": produced.get("signals"),
            "bands_only": True,
            "raw_values_exposed": False,
        }
    def get_environment(self) -> dict[str, Any]:
        """The derived room environment of the current place/area (read-only).

        ``light`` is a *declared* fact (declared fixture + recorded power).  When
        it cannot be known it says ``unknown`` with a reason code -- it is never
        reported as ``0`` and never as "dark".  Surfaces are instance-scoped.
        """

        service = self.service
        world_home = service.config.world_home
        environment = getattr(service.config, "environment", None)
        live = self._world_live()
        location = live.get("location") or {}
        substrate = self._substrate()
        payload: dict[str, Any] = {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "source": "world_room_environment",
            "location": location,
            "read_is_pure": True,
        }
        try:
            import sys

            scripts_dir = str(getattr(service.config, "scripts_dir", ""))
            if scripts_dir and scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            import world_body_binding as binding
            import world_room_environment as room

            light = room.light_for_home(
                world_home, now=service._moment(), location=location
            )
            catalog = binding.owned_catalog_map(world_home)
            surfaces = (
                service.wbs.surfaces_here(
                    substrate,
                    catalog,
                    location_id=location.get("place_id"),
                    area_id=location.get("area_id"),
                )
                if substrate
                else []
            )
            feature_on = room.feature_enabled(environment)
        except Exception:  # noqa: BLE001 - unknown, never invented
            light = {
                "status": "unknown",
                "reason_code": "ROOM_ENVIRONMENT_UNAVAILABLE",
                "power": None,
                "band": None,
                "source": "world_room_environment",
            }
            surfaces = []
            feature_on = False
        payload["light"] = light
        payload["surfaces"] = surfaces
        payload["fixture_block_present"] = bool(
            (substrate or {}).get("fixtures")
        )
        payload["environment_channel_enabled"] = bool(feature_on)
        payload["body_input_model"] = "environment.light.v1"
        return payload

    def _labels(self) -> dict[str, str]:
        """Economy-joined readable names, so the model never sees a raw 70-char id."""

        labels: dict[str, str] = {}
        try:
            economy = json.loads(
                (self.service.config.world_home / "state" / "world_property_economy.json")
                .read_text(encoding="utf-8")
            )
            for item in economy.get("items") or []:
                if isinstance(item, dict) and item.get("item_instance_id"):
                    labels[item["item_instance_id"]] = str(
                        item.get("catalog_item_id") or "item"
                    )
        except Exception:
            labels = {}
        return labels

    def _room_living_availability(self) -> dict[str, Any]:
        """Candidates for ARRANGE / TOGGLE_FIXTURE, derived from the owners.

        An ARRANGE candidate is owned + delivered + relocatable **and** already
        placed at home; a fixture candidate is a declared powerable fixture
        placed in this area.  Nothing is invented when a document is unreadable:
        the list is simply empty with a reason.
        """

        substrate = self._substrate() or {}
        world_home = self.service.config.world_home
        environment = getattr(self.service.config, "environment", None)
        location = self._world_live().get("location") or {}
        place = location.get("place_id")
        area = location.get("area_id")
        labels = self._labels()
        arrange: list[dict[str, Any]] = []
        fixtures: list[dict[str, Any]] = []
        rooms: list[dict[str, Any]] = []
        reason: Optional[str] = None
        try:
            import world_body_binding as binding
            import world_property_economy as economy
            import world_room_environment as room

            catalog = binding.owned_catalog_map(world_home)
            state = binding.load_economy(world_home) or {}
            placements = substrate.get("placements") or {}
            fixture_records = substrate.get("fixtures") or {}
            home_place = economy.HOME_ITEM_LOCATION.split("/", 1)[0]
            for item in state.get("items") or []:
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("disposition")) != economy.DISPOSITION_OWNED:
                    continue
                object_id = str(item.get("item_instance_id"))
                placement = placements.get(object_id)
                declaration = economy.catalog_declaration(item.get("catalog_item_id"))
                label = labels.get(object_id) or object_id
                if (
                    declaration.get("relocatable")
                    and item.get("current_location") == economy.HOME_ITEM_LOCATION
                    and placement
                    and str(placement.get("location_id")) == home_place
                ):
                    arrange.append(
                        {
                            "object_id": object_id,
                            "label": label,
                            "catalog_item_id": item.get("catalog_item_id"),
                            "placement": dict(placement),
                            "surfaces": [
                                self.service.wbs.surface_instance_id(kind, object_id)
                                for kind in declaration.get("surfaces") or ()
                            ],
                        }
                    )
                fixture_declaration = room.declaration_of_object(object_id, catalog)
                if (
                    fixture_declaration
                    and fixture_declaration.get("powerable")
                    and placement
                    and str(placement.get("location_id")) == str(place)
                    and (
                        placement.get("area_id") is None
                        or area is None
                        or str(placement.get("area_id")) == str(area)
                    )
                ):
                    record = fixture_records.get(object_id) or {}
                    fixtures.append(
                        {
                            "object_id": object_id,
                            "label": label,
                            "power": record.get("power"),
                            "since": record.get("since"),
                            "band": fixture_declaration.get("light_band"),
                            "declared_source": fixture_declaration.get("declared_source"),
                            "place_id": place,
                            "area_id": area,
                        }
                    )
            rooms = [
                {"location_id": home_place, "area_id": found}
                for found in sorted(economy.declared_home_areas(environment))
            ]
        except Exception:  # noqa: BLE001 - no join, no candidate
            reason = "ROOM_LIVING_UNAVAILABLE"
        return {
            "arrange": arrange,
            "fixtures": fixtures,
            "rooms": rooms,
            "reason_code": reason,
        }

    def _reachable_move_candidates(self) -> list[dict[str, Any]]:
        """B6-3: reachable destinations from the World's own location catalog.

        The list is derived from the canonical reachability relation at the
        *current* place, so it changes with the real World state and is never
        invented by the gateway or by the model.  A surface that cannot read
        the catalog offers no MOVE candidate at all: an empty list is honest,
        a guessed destination is not.
        """

        place_id = (self._world_live().get("location") or {}).get("place_id")
        if not place_id:
            return []
        try:
            import world_living_skeleton as wls
        except Exception:  # noqa: BLE001 - no catalog means no candidate
            return []
        try:
            # W03：显式传本次服务的环境，避免回落到进程环境里的另一个世界。
            catalog = {
                item["location_id"]: item
                for item in wls.location_catalog(self.service.config.environment)
            }
            current = catalog[str(place_id)]
        except Exception:  # noqa: BLE001
            return []
        candidates: list[dict[str, Any]] = []
        for target in current.get("reachable") or []:
            item = catalog.get(str(target))
            if item is None:
                continue
            candidates.append({
                "location_id": item["location_id"],
                "label": item["name"],
                "kind": item["kind"],
            })
        return candidates

    def get_available_actions(self) -> dict[str, Any]:
        """What the gateway is allowed to ask for (ticket sections 18-20)."""

        substrate = self._substrate() or {}
        labels = self._labels()
        held = sorted(
            object_id for object_id, placement in (substrate.get("placements") or {}).items()
            if placement.get("kind") == "hand"
        )
        held = [] if self.include_test_objects else [h for h in held if not is_test_object(h)]
        visible = [
            {"object_id": object_id, "label": labels.get(object_id) or object_id}
            for object_id in self._visible_object_ids()
        ]

        pick_up = self._pick_up_availability(held=held)
        consume = self._consume_availability()
        rest = self._rest_availability()
        room_living = self._room_living_availability()

        actions: list[dict[str, Any]] = [
            {
                "verb": "MOVE",
                "available": True,
                "params": {"destination_location_id": "string"},
                "requires": ["world.revision", "world.location"],
                # B6-3: reachable destinations, taken from the World's own
                # catalog at the current place -- never invented here.
                "candidates": self._reachable_move_candidates(),
            },
            {
                "verb": "POSE",
                "available": True,
                "params": {"pose": list(POSES), "orientation": list(ORIENTATIONS)},
                "requires": ["body.revision", "body.pose"],
            },
            {
                "verb": "PLACE",
                "available": bool(held),
                "params": {"object_id": "string", "target": "placement_target"},
                "requires": ["body.revision", "body.pose"],
                "blocked_reason": None if held else "nothing_held",
            },
            {
                "verb": "PICK_UP",
                "available": bool(pick_up["available"]),
                "params": {"object_id": "string", "slot_id": list(HAND_SLOTS)},
                "requires": ["body.revision", "body.pose", "capability.takeable"],
                "blocked_reason": pick_up["blocked_reason"],
                "detail": pick_up["detail"],
                "candidates": pick_up["candidates"],
            },
            {
                # F: one object instance, consumed once.  A candidate here is
                # held or sits free in the current place; the Resolver re-reads
                # both facts (and the place's opening state) at commit time.
                "verb": "CONSUME",
                "available": bool(consume["available"]),
                "params": {"object_id": "string"},
                "requires": ["world.location", "body.revision", "body.pose"],
                "blocked_reason": consume["blocked_reason"],
                "detail": consume["detail"],
                "candidates": consume["candidates"],
                "contract": {
                    "one_instance_per_consumption": True,
                    "declaration": "state/world_object_capabilities.json#consumables "
                                   "(world-owned; consumable + free_to_consume)",
                    "placement_is_live_source": True,
                    "held_food_ignores_opening_hours": True,
                    "body_effect": "consume_operation_load_model:2.0s manipulation "
                                   "only; no nutrition, satiety, hunger or health",
                },
            },
            {
                # v0.4: REST is a command, not a timer.  One session at a time;
                # posture and a real bed are World facts, "does it need rest at
                # all" is the Body's own threshold -- both are facts here, never
                # raw values.
                "verb": "REST",
                "available": bool(rest["available"]),
                "params": {"state": ["start", "stop"], "mode": ["rest", "sleep_like"]},
                "requires": ["body.pose", "body.recovery"],
                "blocked_reason": rest["blocked_reason"],
                "detail": rest["detail"],
                "start_available": rest["start_available"],
                "stop_available": rest["stop_available"],
                "sleep_like_available": rest["sleep_like_available"],
                "start_blocked_reason": rest["start_blocked_reason"],
                "stop_blocked_reason": rest["stop_blocked_reason"],
                "current": rest["current"],
                "contract": {
                    "starts_a_session_not_a_timer": True,
                    "command_success_is_not_recovery": True,
                    "sleep_like_is_a_mode_not_a_daily_task": True,
                    "body_effect": "recovery session state only; no hunger, "
                                   "nutrition, healing or scheduled sleep",
                },
            },
            {
                # v0.4 room-living: ARRANGE moves **owned furniture**.  It is not
                # a pick-up: `takeable` is untouched, the bed stays impossible to
                # hold, and children already on the furniture's own surface are
                # carried in the same atomic write.
                "verb": "ARRANGE",
                "available": bool(room_living["arrange"]),
                "params": {"object_id": "string", "target": "placement_target(room|surface)"},
                "requires": ["body.revision", "body.pose"],
                "blocked_reason": (
                    None if room_living["arrange"]
                    else room_living["reason_code"] or "NO_ARRANGEABLE_FURNITURE"
                ),
                "candidates": room_living["arrange"],
                "rooms": room_living["rooms"],
                "contract": {
                    "moves_owned_furniture_only": True,
                    "not_a_pick_up": True,
                    "takeable_unchanged": True,
                    "children_carried_with_the_surface": True,
                    "home_only": True,
                },
            },
            {
                # v0.4 room-living: an absolute fixture state.  There is no blind
                # toggle, and an unknown power is never reported as "off".
                "verb": "TOGGLE_FIXTURE",
                "available": bool(room_living["fixtures"]),
                "params": {"object_id": "string", "state": ["on", "off"]},
                "requires": ["body.revision", "body.pose"],
                "blocked_reason": (
                    None if room_living["fixtures"]
                    else room_living["reason_code"] or "NO_DECLARED_FIXTURE_HERE"
                ),
                "candidates": room_living["fixtures"],
                "contract": {
                    "absolute_state": True,
                    "no_blind_toggle": True,
                    "unknown_is_not_off": True,
                    "declared_fixtures_only": True,
                },
            },
        ]
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "actions": actions,
            "visible_objects": visible,
            "portability": {
                "canonical_takeability_rule": True,
                "rule": "capability.takeable from the object's canonical identity "
                        "(economy catalog entry, or a declared fixture object)",
                "pick_up_blocked_reason": pick_up["blocked_reason"],
                "takeable_candidates": pick_up["candidates"],
                "fail_closed": True,
            },
        }

    def get_action_result(self, execution_id: Any) -> dict[str, Any]:
        """Canonical truth for one execution.  Never invents an outcome."""

        execution_id = _text(execution_id, "execution_id", maximum=256)
        view = self.service.resolver.reconcile(execution_id)
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "execution_id": execution_id,
            "known": view.get("known"),
            "verdict": view.get("verdict"),
            "state": view.get("state"),
            "result": view.get("result"),
            "action_type": view.get("action_type"),
            # F: for a CONSUME execution the owner receipt is reported next to
            # the ledger verdict, so "unknown" and "the object is gone" can be
            # told apart without inferring anything from absence.
            "consumption_receipt": view.get("consumption_receipt"),
            "receipt_present": view.get("consumption_receipt") is not None,
            "authoritative": True,
            "source": "resolver_ledger",
        }

    # ---- the one mutation ------------------------------------------------
    def submit_action(self, request: Any) -> dict[str, Any]:
        """Gateway -> service -> Resolver.  The gateway never commits.

        M14A: the request MUST carry a one-shot ``authorization_id`` issued by
        the Intent Executor for this exact execution (id + verb + args).  The
        surface consumes it atomically; anything else is rejected before the
        Resolver is contacted (fail-closed, zero mutation).
        """

        if not isinstance(request, Mapping):
            raise GatewayAPIError("submit_action requires an object")

        action = _text(request.get("action") or request.get("verb"), "action", maximum=32)
        execution_id = _text(request.get("execution_id"), "execution_id", maximum=256)
        params = request.get("params") or {}
        if not isinstance(params, Mapping):
            raise GatewayAPIError("params must be an object")
        observed = request.get("observed_dependencies") or {}
        if not isinstance(observed, Mapping):
            raise GatewayAPIError("observed_dependencies must be an object")

        # ticket 55: the caller cannot supply authority.  The service stamps it.
        if "authority" in request or "confirm_apply" in request:
            raise GatewayAPIError(
                "authority/confirm_apply are stamped by the service, not supplied by the gateway"
            )

        # M14A: consume the one-shot authorization BEFORE any resolver contact.
        if self.authorizations is None:
            return self._reject_authorization(
                execution_id, action, "AUTHORIZATION_REGISTRY_UNAVAILABLE",
                "no authorization registry is wired to this surface",
            )
        authorization_id = request.get("authorization_id")
        if not isinstance(authorization_id, str) or not authorization_id.strip():
            return self._reject_authorization(
                execution_id, action, "AUTHORIZATION_REQUIRED",
                "submit_action requires an Intent-Executor-issued authorization_id",
            )
        try:
            record = self.authorizations.verify_and_consume(
                authorization_id=authorization_id,
                execution_id=execution_id,
                verb=action,
                params=dict(params),
                actor=GATEWAY_ACTOR,
                # M15G: cross-decision replay guard.  The submission names the
                # Decision it belongs to; a mismatched capability is refused.
                decision_id=request.get("decision_ref"),
            )
        except waa.AuthorizationError as error:
            return self._reject_authorization(
                execution_id, action, str(error),
                "authorization verify/consume failed (fail-closed)",
            )

        proposal = {
            "action": action,
            "execution_id": execution_id,
            "actor_id": GATEWAY_ACTOR,
            "body_id": (self.service.body_state or {}).get("body_id"),
            "authority": GATEWAY_AUTHORITY,
            "confirm_apply": True,
            "observed_dependencies": {k: observed[k] for k in sorted(observed)},
            "params": dict(params),
        }

        # Exposure policy: refuse anything the surface does not expose, so a
        # caller cannot route around get_available_actions (ticket 18-20).
        exposed = {a["verb"] for a in self.get_available_actions()["actions"] if a["available"]}
        if action not in exposed:
            return {
                "kind": KIND_GATEWAY_API,
                "schema_version": SCHEMA_GATEWAY_API,
                "execution_id": execution_id,
                "action": action,
                "status": "rejected",
                "reason_code": "NOT_EXPOSED",
                "detail": "verb %s is not exposed to the gateway right now" % action,
                "world_unchanged": True,
                "body_unchanged": True,
            }

        result = self.service.act(proposal)
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "execution_id": result.get("execution_id"),
            "action": result.get("action"),
            "status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "applied_to_body": result.get("applied_to_body"),
            "authoritative": True,
            "source": "resolver",
            "authorization": {
                "caller": GATEWAY_CALLER,
                "authority": GATEWAY_AUTHORITY,
                "authorization_id": record.get("authorization_id"),
                "decision_ref": record.get("decision_id"),
                "intent_id": record.get("intent_id"),
                "state": "CONSUMED",
            },
        }

    def _reject_authorization(
        self, execution_id: str, action: str, reason_code: str, detail: str
    ) -> dict[str, Any]:
        """Fail-closed rejection: zero mutation, zero ledger SUCCESS."""
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "execution_id": execution_id,
            "action": action,
            "status": "rejected",
            "reason_code": reason_code,
            "detail": detail,
            "world_unchanged": True,
            "body_unchanged": True,
            "authoritative": True,
            "source": "authorization_firewall",
        }

    def submit_local_owner_action(self, request: Any) -> dict[str, Any]:
        """The explicit *local owner* (phone) command.

        Same firewall as ``submit_action`` -- the one-shot authorization is
        still verified and consumed before the Resolver is contacted -- but two
        deliberate differences:

        * the binding is server-side only (``CHIYO_WORLD_LOCAL_OWNER_PHONE``);
          an unbound service refuses, and a request may not carry any
          permission-shaped field;
        * the stamp is ``local_owner_phone``, never ``verified_main_self_tool``,
          so the ledger and receipt provably say "the local owner did this from
          the phone" instead of claiming a main-self decision.

        The verb allowlist is ``CONSUME`` only.
        """

        if not isinstance(request, Mapping):
            raise GatewayAPIError("submit_local_owner_action requires an object")
        binding = self.local_owner_binding
        if binding is None:
            return {
                "kind": KIND_GATEWAY_API,
                "schema_version": SCHEMA_GATEWAY_API,
                "execution_id": str(request.get("execution_id") or ""),
                "action": str(request.get("action") or request.get("verb") or ""),
                "status": "rejected",
                "reason_code": "LOCAL_OWNER_PHONE_NOT_BOUND",
                "detail": "this service is not bound to a local owner phone surface",
                "world_unchanged": True,
                "body_unchanged": True,
                "authoritative": True,
                "source": "local_owner_firewall",
            }
        forbidden = set(request) & _LOCAL_OWNER_FORBIDDEN_KEYS
        if forbidden:
            raise GatewayAPIError(
                "permissions are stamped by the service, not supplied by the "
                "caller: %s" % sorted(forbidden)
            )
        unknown = set(request) - _LOCAL_OWNER_REQUEST_KEYS
        if unknown:
            raise GatewayAPIError("unknown request fields: %s" % sorted(unknown))

        action = _text(request.get("action") or request.get("verb"), "action", maximum=32)
        execution_id = _text(request.get("execution_id"), "execution_id", maximum=256)
        params = request.get("params") or {}
        if not isinstance(params, Mapping):
            raise GatewayAPIError("params must be an object")
        observed = request.get("observed_dependencies") or {}
        if not isinstance(observed, Mapping):
            raise GatewayAPIError("observed_dependencies must be an object")
        if action not in GATEWAY_LOCAL_OWNER_VERBS:
            return {
                "kind": KIND_GATEWAY_API,
                "schema_version": SCHEMA_GATEWAY_API,
                "execution_id": execution_id,
                "action": action,
                "status": "rejected",
                "reason_code": "LOCAL_OWNER_VERB_NOT_ALLOWED",
                "detail": "the local-owner command accepts %s only"
                % (list(GATEWAY_LOCAL_OWNER_VERBS),),
                "world_unchanged": True,
                "body_unchanged": True,
                "authoritative": True,
                "source": "local_owner_firewall",
            }

        # The registry firewall is unchanged: verify + consume before the
        # Resolver sees anything (fail-closed).
        if self.authorizations is None:
            return self._reject_authorization(
                execution_id, action, "AUTHORIZATION_REGISTRY_UNAVAILABLE",
                "no authorization registry is wired to this surface",
            )
        authorization_id = request.get("authorization_id")
        if not isinstance(authorization_id, str) or not authorization_id.strip():
            return self._reject_authorization(
                execution_id, action, "AUTHORIZATION_REQUIRED",
                "submit_local_owner_action requires an issued authorization_id",
            )
        try:
            record = self.authorizations.verify_and_consume(
                authorization_id=authorization_id,
                execution_id=execution_id,
                verb=action,
                params=dict(params),
                actor=GATEWAY_ACTOR,
                decision_id=request.get("decision_ref"),
            )
        except waa.AuthorizationError as error:
            return self._reject_authorization(
                execution_id, action, str(error),
                "authorization verify/consume failed (fail-closed)",
            )

        proposal = {
            "action": action,
            "execution_id": execution_id,
            "actor_id": binding["subject"] or GATEWAY_ACTOR,
            "body_id": (self.service.body_state or {}).get("body_id"),
            "authority": GATEWAY_LOCAL_OWNER_AUTHORITY,
            "confirm_apply": True,
            "observed_dependencies": {k: observed[k] for k in sorted(observed)},
            "params": dict(params),
        }
        # Exposure policy.  The verb must be exposed right now; when it is
        # blocked we answer with the *specific* capability fact (travelling /
        # posture / nothing held / recovery not needed ...) instead of a generic
        # "not exposed", so the caller never has to guess why.  Nothing is
        # submitted in either case.
        exposure = next(
            (
                entry
                for entry in self.get_available_actions()["actions"]
                if entry.get("verb") == action
            ),
            None,
        )
        blocked: Optional[str] = None
        detail: Optional[str] = None
        if exposure is None:
            blocked = "NOT_EXPOSED"
            detail = "verb %s is not part of this surface" % action
        elif action == "REST":
            # One session, two directions: ask *which* direction was requested so
            # "stop what is not open" is never reported as "rest is not needed".
            desired = str((params or {}).get("state") or "").strip().lower()
            if desired == "stop":
                if not exposure.get("stop_available"):
                    blocked = exposure.get("stop_blocked_reason") or "NO_OPEN_RECOVERY"
                    detail = "no recovery session is open, so there is nothing to stop"
            elif not exposure.get("start_available"):
                blocked = exposure.get("start_blocked_reason") or "NOT_EXPOSED"
                detail = exposure.get("detail")
        elif not exposure.get("available"):
            blocked = exposure.get("blocked_reason") or "NOT_EXPOSED"
            detail = exposure.get("detail")
        if blocked is not None:
            return {
                "kind": KIND_GATEWAY_API,
                "schema_version": SCHEMA_GATEWAY_API,
                "execution_id": execution_id,
                "action": action,
                "status": "rejected",
                "reason_code": blocked,
                "detail": detail or "verb %s is not exposed to the gateway right now" % action,
                "blocked_reason": blocked,
                "world_unchanged": True,
                "body_unchanged": True,
                "authoritative": True,
                "source": "local_owner_exposure",
            }
        result = self.service.act(proposal)
        return {
            "kind": KIND_GATEWAY_API,
            "schema_version": SCHEMA_GATEWAY_API,
            "execution_id": result.get("execution_id"),
            "action": result.get("action"),
            "status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "applied_to_body": result.get("applied_to_body"),
            "consequence_reason": result.get("consequence_reason"),
            "consequence_id": result.get("consequence_id"),
            "movement_settlement": result.get("movement_settlement"),
            "recovery_state": result.get("recovery_state"),
            "legacy_reason_code": result.get("legacy_reason_code"),
            "detail": result.get("detail"),
            "consumption_receipt": result.get("consumption_receipt"),
            "purchase_voucher": result.get("purchase_voucher"),
            "fixture": result.get("fixture"),
            "arranged": result.get("arranged"),
            "rest_surface_id": result.get("rest_surface_id"),
            "source": "resolver",
            "local_owner": {
                "source": GATEWAY_LOCAL_OWNER_SOURCE,
                "uid": binding["uid"],
                "binding": {
                    "subject": binding["subject"],
                    "world_home": binding["world_home"],
                },
                "caller": "local_owner_phone_surface",
                "authorization": {
                    "authorization_id": record.get("authorization_id"),
                    "decision_ref": record.get("decision_id"),
                    "intent_id": record.get("intent_id"),
                    "state": "CONSUMED",
                },
            },
        }

    # ---- dispatch --------------------------------------------------------
    def handle(self, command: Any, argument: Any = None) -> dict[str, Any]:
        name = str(command or "").strip().lower()
        if name in GATEWAY_FORBIDDEN_COMMANDS:
            return {
                "ok": False,
                "error": "forbidden_on_gateway_surface",
                "command": name,
                "detail": "this command belongs to the admin control surface",
            }
        if name not in GATEWAY_COMMANDS:
            return {
                "ok": False,
                "error": "unknown_command",
                "command": name,
                "allowed": list(GATEWAY_COMMANDS),
            }
        try:
            handler = {
                "ping": lambda: {"pong": True},
                "get_status": self.get_status,
                "get_environment": self.get_environment,
                "get_snapshot": self.get_snapshot,
                "get_observation": self.get_observation,
                "get_body_signals": self.get_body_signals,
                "get_available_actions": self.get_available_actions,
                "get_action_result": lambda: self.get_action_result(
                    (argument or {}).get("execution_id") if isinstance(argument, Mapping) else argument
                ),
                "submit_action": lambda: self.submit_action(argument),
                "submit_local_owner_action": lambda: (
                    self.submit_local_owner_action(argument)
                ),
            }[name]
            payload = handler()
        except GatewayAPIError as error:
            return {"ok": False, "error": "invalid_request", "command": name, "detail": str(error)}
        except Exception as error:  # never take the service down for a gateway call
            return {"ok": False, "error": type(error).__name__, "command": name,
                    "detail": str(error)[:400]}
        return {"ok": True, "command": name, "result": payload}


def peer_credentials(connection: socket.socket) -> Optional[dict[str, int]]:
    """Return {pid, uid, gid} for the peer, or None when unavailable (Linux)."""

    import struct
    cr = getattr(socket, "SO_PEERCRED", None)
    if cr is None:
        return None
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, cr, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", raw)
        return {"pid": pid, "uid": uid, "gid": gid}
    except OSError:
        return None
