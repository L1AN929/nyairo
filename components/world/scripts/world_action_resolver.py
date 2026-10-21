#!/usr/bin/env python3
"""Unified World Resolver (M4) + real concurrency semantics (M5).

Generalizes ``life_execute.ProductionAdapter.execute_world_move`` -- which was
hard-wired to a single ``MOVE_TO`` branch -- into one execution skeleton:

    ActionProposal -> normalize -> validate -> collect dependencies
    -> check observed versions -> check world/body constraints
    -> prepare mutation -> atomic commit -> finalize ledger -> ActionResult

Nothing may bypass it: LLM, Life, Body and Expression all have to submit a
proposal here.

Key properties
--------------
* **MOVE stays compatible.**  The MOVE action *delegates* to the existing
  ``world_living_skeleton.plan_move_to`` / ``apply_move_to``.  Those functions
  are untouched, so the old result shape, the old failure contract and the old
  ``execution_id`` behaviour are preserved.  A thin
  :func:`execute_move_compat` wrapper keeps the old caller working.
* **Real stale protection (M5).**  A proposal carries the *dependency values its
  caller actually observed*.  The resolver re-reads them under the write lock and
  rejects with ``STALE_STATE`` if anything moved.  This is genuine optimistic
  concurrency -- unlike the legacy ``expected_revision`` argument, which writers
  filled in with a revision they had just read themselves.
* **Dependency-scoped, not one global lock.**  Dependencies are named and
  resolved individually (``world.revision``, ``body.revision``, ``body.pose``,
  ``body.slot.<slot>``), so the check is as narrow as the current schema allows.
* **Body is a constraint, not an authority.**  ``world_body_substrate`` supplies
  placement / pose / slot state.  The resolver alone commits.
* **UNKNOWN is preserved.**  An in-flight execution reconciles to ``UNKNOWN``
  with ``reconciliation_required``; it is never rewritten to FAILURE and never
  auto-retried.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import world_execution_ledger as ledger

RESULT_KIND = "world_action_result"
SCHEMA_VERSION = "world.action.resolver.v1"

AUTHORITY_VERIFIED_MAIN_SELF = "verified_main_self_tool"
#: v0.4: the *only* other authority this resolver accepts, and only for
#: ``CONSUME``.  It is stamped by the World/Body service's explicit local-owner
#: command (``submit_local_owner_action``) after the transport peer check and
#: the server-side owner binding.  A caller cannot pass it through the gateway,
#: and a local owner operation is never recorded as a main-self one.
AUTHORITY_LOCAL_OWNER_PHONE = "local_owner_phone"
LOCAL_OWNER_SOURCE = "local_owner_phone"

# --------------------------------------------------------------------------
# Frozen error vocabulary + provenance (ticket section 10)
# --------------------------------------------------------------------------

LEGACY_CANONICAL = "LEGACY_CANONICAL"
NEW_M6_CANDIDATE = "NEW_M6_CANDIDATE"
NEW_RESOLVER_REQUIRED = "NEW_RESOLVER_REQUIRED"

CODE_OK = "OK"
CODE_NOOP = "NOOP"
CODE_FEATURE_DISABLED = "FEATURE_DISABLED"
CODE_INVALID_PROPOSAL = "INVALID_PROPOSAL"
CODE_UNKNOWN_ACTION = "UNKNOWN_ACTION"
CODE_UNKNOWN_TARGET = "UNKNOWN_TARGET"
CODE_NOT_REACHABLE = "NOT_REACHABLE"
CODE_OCCUPIED = "OCCUPIED"
CODE_STALE_STATE = "STALE_STATE"
CODE_NOT_TAKEABLE = "NOT_TAKEABLE"
#: F: CONSUME.  A consumable is a *World-owned declaration*, never a guess: an
#: undeclared, uneatable, unfree, unreachable or economy-owned instance is
#: refused with this code, and nothing is consumed.
CODE_NOT_CONSUMABLE = "NOT_CONSUMABLE"
CODE_NOT_ALLOWED = "NOT_ALLOWED"
CODE_ALREADY_APPLIED = "ALREADY_APPLIED"
CODE_POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
CODE_LEDGER_CORRUPT = "LEDGER_CORRUPT"
CODE_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
CODE_WORLD_UNAVAILABLE = "WORLD_UNAVAILABLE"
CODE_AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"

#: code -> (provenance, literal spellings found in production, note)
ERROR_PROVENANCE: dict[str, tuple[str, list[str], str]] = {
    CODE_NOT_REACHABLE: (
        LEGACY_CANONICAL,
        ["LOCATION_NOT_REACHABLE", "ROUTE_NOT_REACHABLE"],
        "the bare code NOT_REACHABLE is NEW; only the two prefixed legacy spellings "
        "existed and both map onto this",
    ),
    CODE_OCCUPIED: (
        NEW_M6_CANDIDATE,
        [],
        "the bare code OCCUPIED is NEW; production had zero hits before the M6 substrate",
    ),
    CODE_STALE_STATE: (
        NEW_RESOLVER_REQUIRED,
        ["stale_affordance", "stale_activity_target", "stale_choice_context", "STALE_PRESENCE"],
        "no bare STALE_STATE existed; several module-local spellings were unified here, "
        "legacy text preserved in legacy_reason_code",
    ),
    CODE_UNKNOWN_TARGET: (
        LEGACY_CANONICAL,
        "UNKNOWN_LOCATION (legacy) maps here; the bare code is new",
        "unknown-target rejection existed for locations",
    ),
    CODE_NOT_ALLOWED: (
        LEGACY_CANONICAL,
        "LOCATION_CLOSED / TRAVEL_NOT_ALLOWED existed; note verified_main_self_required "
        "(lowercase, life_execute) case-diverges from the UPPER convention of 8 modules",
        "permission/availability refusals existed",
    ),
    CODE_ALREADY_APPLIED: (
        LEGACY_CANONICAL,
        ["already_applied"],
        "idempotent replay result existed",
    ),
    CODE_POSTCONDITION_FAILED: (
        LEGACY_CANONICAL,
        "postcondition verification existed under three spellings",
        "postcondition verification existed",
    ),
    CODE_RECONCILIATION_REQUIRED: (
        LEGACY_CANONICAL,
        ["reconciliation_required", "uncertain"],
        "UNKNOWN/reconciliation existed",
    ),
    CODE_FEATURE_DISABLED: (
        LEGACY_CANONICAL,
        ["FEATURE_DISABLED"],
        "feature gating existed",
    ),
    CODE_WORLD_UNAVAILABLE: (
        LEGACY_CANONICAL,
        ["WORLD_NOT_INITIALIZED"],
        "missing world state existed",
    ),
    CODE_NOT_TAKEABLE: (
        NEW_RESOLVER_REQUIRED,
        "the bare code NOT_TAKEABLE is NEW; no object-level takeability concept existed",
        "no prior object-level takeability concept",
    ),
    CODE_NOT_CONSUMABLE: (
        NEW_RESOLVER_REQUIRED,
        "the bare code NOT_CONSUMABLE is NEW; no consumable-object concept existed",
        "no prior consumable/eat capability existed",
    ),
}
#: Unified mapping from the two legacy vocabularies onto canonical codes.
LEGACY_CODE_MAP: dict[str, str] = {
    "LOCATION_NOT_REACHABLE": CODE_NOT_REACHABLE,
    "ROUTE_NOT_REACHABLE": CODE_NOT_REACHABLE,
    "UNKNOWN_LOCATION": CODE_UNKNOWN_TARGET,
    # v0.4 (rc1 repair): a room target the body is not standing in.  The
    # canonical code is NOT_REACHABLE (the hand is not there); the specific
    # spelling stays what a caller reads, so it maps onto it.
    "NOT_CURRENT_AREA": CODE_NOT_REACHABLE,
    "INVALID_LOCATION_ID": CODE_INVALID_PROPOSAL,
    "LOCATION_CLOSED": CODE_NOT_ALLOWED,
    "TRAVEL_NOT_ALLOWED": CODE_NOT_ALLOWED,
    "DAILY_RHYTHM_UNAVAILABLE": CODE_WORLD_UNAVAILABLE,
    "WORLD_NOT_INITIALIZED": CODE_WORLD_UNAVAILABLE,
    "FEATURE_DISABLED": CODE_FEATURE_DISABLED,
    "ALREADY_AT_LOCATION": CODE_NOOP,
    "verified_main_self_required": CODE_AUTHORITY_REQUIRED,
    "confirmation_required": CODE_AUTHORITY_REQUIRED,
    "world_postcondition_failed": CODE_POSTCONDITION_FAILED,
    "WORLD_POSTCONDITION_FAILED": CODE_POSTCONDITION_FAILED,
    "postcondition_failed": CODE_POSTCONDITION_FAILED,
    "reconciliation_required": CODE_RECONCILIATION_REQUIRED,
    "journal_corrupt": CODE_LEDGER_CORRUPT,
    "already_applied": CODE_ALREADY_APPLIED,
    "invalid_location_id": CODE_INVALID_PROPOSAL,
    "invalid_execution_id": CODE_INVALID_PROPOSAL,
}

#: v0.4 room-living adds two verbs: ``ARRANGE`` (move **owned** furniture; never
#: a pick-up) and ``TOGGLE_FIXTURE`` (an absolute on/off of a declared fixture).
ACTIONS = (
    "MOVE",
    "PICK_UP",
    "PLACE",
    "POSE",
    "CONSUME",
    "REST",
    "ARRANGE",
    "TOGGLE_FIXTURE",
)

#: v0.4: the verbs one explicit *local owner* command may carry.  ``CONSUME``
#: keeps the phone-shopping channel; ``MOVE``/``PICK_UP``/``PLACE``/``POSE``/
#: ``REST`` are the resident-world verbs.  This tuple and the gateway's
#: ``GATEWAY_LOCAL_OWNER_VERBS`` must stay identical (both are the same
#: permission statement, and a drift would make one side wider than the other).
LOCAL_OWNER_ACTIONS = (
    "MOVE",
    "PICK_UP",
    "PLACE",
    "POSE",
    "CONSUME",
    "REST",
    "ARRANGE",
    "TOGGLE_FIXTURE",
)

#: REST is a command, not a timer.  ``body_runtime`` owns the recovery state
#: machine and the mode vocabulary; these are mirrored here so a malformed
#: command is refused before the Body is asked anything.
REST_STATES = ("start", "stop")
REST_MODES = ("rest", "sleep_like")
#: A body can only rest from a resting pose: posture is the World's measurement.
RESTING_POSES = ("seated", "lying")
REST_SLEEP_POSE = "lying"

TERMINAL_SUCCESS = frozenset({"applied", "noop"})
TERMINAL_FAILURE = frozenset({"rejected", "failed", "disabled"})
IN_FLIGHT = frozenset({"prepared", "uncertain"})

CRASH_AFTER_PREPARED = "after_prepared"
CRASH_AFTER_MUTATION = "after_mutation"
CRASH_POINTS = (CRASH_AFTER_PREPARED, CRASH_AFTER_MUTATION)

MAX_ID_LENGTH = 128


class ResolverError(ValueError):
    """The proposal violated the resolver contract."""


class ResolverCrash(RuntimeError):
    """Test-only crash injection at a named step."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, field: str, *, maximum: int = MAX_ID_LENGTH) -> str:
    if not isinstance(value, str):
        raise ResolverError(f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > maximum:
        raise ResolverError(f"{field} must be non-empty and bounded")
    return text


# --------------------------------------------------------------------------
# result construction
# --------------------------------------------------------------------------


def _result(
    proposal: Mapping[str, Any],
    *,
    status: str,
    code: str,
    reason_code: str,
    before: Any = None,
    after: Any = None,
    dependencies: Optional[Mapping[str, Any]] = None,
    stale_dependencies: Optional[list[str]] = None,
    ledger_status: Optional[str] = None,
    reconciliation_required: bool = False,
    body_signal_hint: Optional[list[str]] = None,
) -> dict[str, Any]:
    result = (
        ledger.RESULT_SUCCESS
        if status in TERMINAL_SUCCESS
        else ledger.RESULT_FAILURE
        if status in TERMINAL_FAILURE
        else ledger.RESULT_UNKNOWN
    )
    payload: dict[str, Any] = {
        "kind": RESULT_KIND,
        "schema_version": SCHEMA_VERSION,
        "execution_id": proposal.get("execution_id"),
        "action": proposal.get("action"),
        "actor_id": proposal.get("actor_id"),
        "body_id": proposal.get("body_id"),
        "status": status,
        "result": result,
        "reason_code": code,
        "legacy_reason_code": reason_code,
        "before": before,
        "after": after,
        "dependencies": dict(dependencies or {}),
        "ledger_status": ledger_status,
        "executed_at": _now_iso(),
    }
    if stale_dependencies:
        payload["stale_dependencies"] = sorted(stale_dependencies)
    if reconciliation_required:
        payload["reconciliation_required"] = True
    if body_signal_hint:
        payload["body_consequence_refs"] = sorted(body_signal_hint)
    return payload


def _reject(
    proposal: Mapping[str, Any],
    status: str,
    code: str,
    *,
    reason_code: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    return _result(
        proposal,
        status=status,
        code=code,
        reason_code=reason_code or code,
        before=before,
        after=after,
        **extra,
    )


def map_legacy_code(code: Optional[str]) -> Optional[str]:
    """Canonical code for a legacy spelling, or ``None`` if unmapped."""

    if not code:
        return None
    return LEGACY_CODE_MAP.get(code)


# --------------------------------------------------------------------------
# proposal normalization / validation
# --------------------------------------------------------------------------


_PROPOSAL_KEYS = frozenset(
    {
        "action",
        "execution_id",
        "actor_id",
        "body_id",
        "authority",
        "confirm_apply",
        "observed_dependencies",
        "params",
    }
)


def normalize_proposal(proposal: Any) -> dict[str, Any]:
    """Validate and canonicalize an ActionProposal."""

    if not isinstance(proposal, Mapping):
        raise ResolverError("proposal must be an object")

    unknown = set(proposal) - _PROPOSAL_KEYS
    if unknown:
        raise ResolverError(f"proposal has unknown fields: {sorted(unknown)}")
    missing = {"action", "execution_id"} - set(proposal)
    if missing:
        raise ResolverError(f"proposal is missing fields: {sorted(missing)}")

    action = _text(proposal["action"], "action", maximum=32)
    if action not in ACTIONS:
        raise ResolverError(f"action must be one of {list(ACTIONS)}")

    params = proposal.get("params") or {}
    if not isinstance(params, Mapping):
        raise ResolverError("params must be an object")

    observed = proposal.get("observed_dependencies") or {}
    if not isinstance(observed, Mapping):
        raise ResolverError("observed_dependencies must be an object")
    for key, value in observed.items():
        _text(key, "observed_dependencies key", maximum=64)
        if not isinstance(value, (str, int, bool)) and value is not None:
            raise ResolverError("observed dependency values must be scalar")

    return {
        "action": action,
        "execution_id": _text(proposal["execution_id"], "execution_id", maximum=256),
        "actor_id": _text(proposal.get("actor_id") or "chiyo", "actor_id"),
        "body_id": _text(proposal.get("body_id") or "chiyo_body", "body_id"),
        "authority": proposal.get("authority"),
        "confirm_apply": bool(proposal.get("confirm_apply")),
        "observed_dependencies": {k: observed[k] for k in sorted(observed)},
        "params": dict(params),
    }


def _require_params(params: Mapping[str, Any], expected: set[str]) -> None:
    unknown = set(params) - expected
    if unknown:
        raise ResolverError(f"params has unknown fields: {sorted(unknown)}")


# --------------------------------------------------------------------------
# the resolver
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# M15: object capability & destination rules (Resolver-owned)
# --------------------------------------------------------------------------


def _m15_slot_token(capability, slot: str) -> str:
    """Translate a substrate slot id into the M15F vocabulary.

    M15F names the hands ``left`` / ``right``; the substrate (which owns the
    ids) names them ``left_hand`` / ``right_hand``.  Reconciling the two is a
    boundary translation, not a second truth: an unknown token is passed through
    untouched so M15F still refuses it.
    """

    tokens = tuple(getattr(capability, "HAND_SLOTS", ()) or ())
    if slot in tokens:
        return slot
    short = slot[:-5] if slot.endswith("_hand") else slot
    return short if short in tokens else slot


def _m15_target_kind_token(capability, kind: str) -> str:
    """Substrate placement kind -> M15F target kind (``hand`` -> ``slot``).

    ``room`` has no M15F spelling: it is returned unchanged and the caller
    treats it as a kind M15F does not model.
    """

    kinds = tuple(getattr(capability, "MODELLED_TARGET_KINDS",
                          ("surface", "container", "slot")))
    if kind == "hand" and "slot" in kinds:
        return "slot"
    return kind


def _m15_capability_module():
    """Load the M15F capability vocabulary (it ships with the bridge package).

    The Resolver runs inside the World/Body service, whose sys.path does not
    include ``service/bridge``; a plain import would silently disable the
    capability gate.  Load it by file path instead, once.
    """

    global _M15_CAPABILITY_MODULE
    if _M15_CAPABILITY_MODULE is not None:
        return _M15_CAPABILITY_MODULE
    try:
        import world_object_capability as module  # already importable
        _M15_CAPABILITY_MODULE = module
        return module
    except ImportError:
        pass
    import importlib.util
    import pathlib as _pathlib
    candidate = (_pathlib.Path(__file__).resolve().parent.parent
                 / "service" / "bridge" / "world_object_capability.py")
    if not candidate.is_file():  # pragma: no cover - bridge not deployed
        return None
    spec = importlib.util.spec_from_file_location("world_object_capability", str(candidate))
    if spec is None or spec.loader is None:  # pragma: no cover
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _M15_CAPABILITY_MODULE = module
    return module


_M15_CAPABILITY_MODULE = None


def _m15_capability_block(hermes_home, object_id: str) -> dict[str, Any]:
    """An object's capability, joined by the binding layer (M15).

    The Resolver never reads the economy itself -- ``world_body_binding`` owns
    that join.  An unknown object yields ``{}``; the M15F vocabulary then reads
    UNKNOWN and PICK_UP is denied.  Nothing is assumed.
    """

    try:
        import world_body_binding as binding
    except ImportError:  # pragma: no cover
        return {}
    try:
        return binding.object_capability(hermes_home, object_id)
    except Exception:  # pragma: no cover - unreadable join denies
        return {}


def _m15_destination_verdict(
    target: Mapping[str, Any], *, world_place: Optional[str], hermes_home,
    environment: Optional[Mapping[str, str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """``(code, detail)`` -- ``(None, None)`` when the destination is legal."""

    kind = target.get("kind")
    location_id = target.get("location_id")

    try:
        import world_living_skeleton as skeleton
    except ImportError:  # pragma: no cover - no topology means no legal move
        return "UNKNOWN_DESTINATION", "world topology is unavailable"

    catalog = {}
    # W03：显式传调用方环境，避免回落到进程环境里的另一个世界；
    # 目录不可读（世界包缺失/损坏）时没有任何合法目的地 —— 拒绝而不是抛出。
    try:
        entries = skeleton.location_catalog(environment)
    except Exception:
        return "UNKNOWN_DESTINATION", "world topology is unavailable"
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("location_id"):
            catalog[str(entry["location_id"])] = dict(entry)

    if location_id not in catalog:
        return "UNKNOWN_DESTINATION", f"location {location_id} is not part of the world"
    if world_place and location_id != world_place:
        here = catalog.get(str(world_place)) or {}
        reachable = [str(r) for r in (here.get("reachable") or [])]
        if location_id not in reachable:
            return "NOT_REACHABLE", f"{location_id} is not reachable from {world_place}"

    if kind == "room":
        return None, None

    if kind == "surface":
        import world_body_substrate as substrate

        surface_id = str(target.get("surface_id"))
        if not substrate.surface_declared(surface_id):
            return ("UNKNOWN_DESTINATION",
                    f"surface {surface_id} is not declared by the world")
        owner_catalog = substrate.surface_owner_catalog(surface_id)
        owner_instance = substrate.surface_owner_instance(surface_id)
        try:
            import world_body_binding as binding

            owned = {
                str(i.get("catalog_item_id"))
                for i in (binding.load_economy(hermes_home) or {}).get("items") or []
                if isinstance(i, Mapping) and str(i.get("disposition")) == "OWNED"
            }
            owned_map = binding.owned_catalog_map(hermes_home)
        except Exception:  # pragma: no cover
            owned = set()
            owned_map = {}
        if owner_instance is not None:
            # v0.4 room-living: an instance surface (`desk_surface:<item>`) is
            # real only while *that* piece of owned furniture stands here.  Two
            # desks therefore never share one surface, and a surface whose desk
            # was moved away stops accepting placement instead of stranding it.
            if owned_map.get(owner_instance) != owner_catalog:
                return ("UNKNOWN_DESTINATION",
                        f"surface {surface_id} has no present owner object")
            try:
                store = substrate.SubstrateStore(substrate.substrate_path(hermes_home))
                live = store.load()
                placement = (
                    substrate.placement_of(live, owner_instance) if live else None
                )
            except Exception:  # pragma: no cover - unreadable substrate denies
                placement = None
            if placement is None or str(placement.get("location_id")) != str(location_id):
                return ("UNKNOWN_DESTINATION",
                        f"surface {surface_id} has no present owner object")
        elif owner_catalog and owner_catalog not in owned:
            return ("UNKNOWN_DESTINATION",
                    f"surface {surface_id} has no present owner object")
        return None, None

    if kind == "container":
        return "DESTINATION_NOT_ALLOWED", "container placement is not enabled in V1"
    if kind == "in_transit":
        return "DESTINATION_NOT_ALLOWED", "in_transit is not a PLACE destination"
    return "UNKNOWN_DESTINATION", f"unsupported destination kind {kind}"


#: M15: destination failure -> Resolver reason code
_M15_DESTINATION_CODES = {
    "UNKNOWN_DESTINATION": CODE_UNKNOWN_TARGET,
    "NOT_REACHABLE": CODE_NOT_REACHABLE,
    "DESTINATION_NOT_ALLOWED": CODE_NOT_ALLOWED,
}

#: F: consumption verdict -> Resolver reason code.  A refusal is always a code,
#: never a silent no-op, so a caller can tell "you may not" from "nothing there".
_CONSUME_VERDICT_CODES = {
    "NO_PLACEMENT": CODE_UNKNOWN_TARGET,
    "PLACEMENT_KIND_NOT_CONSUMABLE": CODE_NOT_REACHABLE,
    "NOT_HELD_OR_HERE": CODE_NOT_REACHABLE,
    "NOT_DECLARED": CODE_NOT_CONSUMABLE,
    "DECLARATION_INVALID": CODE_NOT_CONSUMABLE,
    "NOT_CONSUMABLE": CODE_NOT_CONSUMABLE,
    "NOT_FREE": CODE_NOT_CONSUMABLE,
    "ECONOMY_OBJECT": CODE_NOT_CONSUMABLE,
    # v0.4: the owned-purchase permit refuses with these when the voucher is
    # not proven.  They are consumability refusals, not transport errors.
    "PURCHASE_VOUCHER_MISSING": CODE_NOT_CONSUMABLE,
    "PURCHASE_NOT_OWNED": CODE_NOT_CONSUMABLE,
    "NOT_AN_ECONOMY_INSTANCE": CODE_NOT_CONSUMABLE,
    # An unreadable property economy cannot prove "not an economy object", so
    # this is a refusal, not a pass.
    "ECONOMY_FACTS_UNAVAILABLE": CODE_NOT_CONSUMABLE,
    "POSE_CANNOT_CONSUME": CODE_NOT_REACHABLE,
    "LOCATION_CLOSED": CODE_NOT_ALLOWED,
    "RHYTHM_UNAVAILABLE": CODE_NOT_ALLOWED,
}

class WorldResolver:
    """Single commit authority for World actions."""

    def __init__(
        self,
        hermes_home: Optional[Path | str] = None,
        *,
        ledger_path: Optional[Path | str] = None,
        environment: Optional[Mapping[str, str]] = None,
        now: Optional[Any] = None,
    ):
        home = Path(
            hermes_home
            if hermes_home is not None
            else os.environ.get("HERMES_HOME", Path.home() / ".hermes")
        )
        self.hermes_home = home
        self.environment: Mapping[str, str] = (
            environment if environment is not None else os.environ
        )
        self.ledger = ledger.ExecutionLedger(
            ledger_path if ledger_path is not None else ledger.ledger_path(home)
        )
        #: The clock for the one live-source rule (a place's opening hours, which
        #: only gate food the place itself supplies).  Injectable so a caller's own
        #: moment is used instead of an ambient wall clock; the default is
        #: unchanged behaviour.
        self._clock = now if callable(now) else (lambda: datetime.now(timezone.utc))

    # -- lazy module access (import has no runtime effect) ---------------

    def _world_module(self):
        import world_living_skeleton

        return world_living_skeleton

    def _world_state(self) -> Optional[dict[str, Any]]:
        import world_foundation

        return world_foundation.WorldStateStore(
            self.hermes_home / "data" / "world" / "world_state.json"
        ).load()

    def _binding_module(self):
        import world_body_binding

        return world_body_binding

    def _cross_authority_refs(self, object_id: str) -> list[str]:
        """Economy bookkeeping the caller must reconcile after a physical move.

        The resolver deliberately does NOT write the economy store: a physical
        placement is not authority to rewrite ownership bookkeeping.
        """

        try:
            binding = self._binding_module()
        except Exception:
            return []
        return binding.cross_authority_refs(
            self._substrate() or {}, binding.load_economy(self.hermes_home), object_id
        )

    def _substrate_module(self):
        import world_body_substrate

        return world_body_substrate

    def _substrate(self) -> Optional[dict[str, Any]]:
        module = self._substrate_module()
        return module.SubstrateStore(module.substrate_path(self.hermes_home)).load()

    # -- v0.4: local-owner provenance (derived here, never supplied) --------

    def _local_owner_context(self, normalized: Mapping[str, Any]) -> dict[str, Any]:
        """The server-side local-owner context of one submission.

        Derived from this resolver's own runtime identity (the real uid, its own
        home) and from the proposal's body/subject identity -- a caller supplies
        none of it.  It is recorded only for the local-owner authority.
        """

        return {
            "source": LOCAL_OWNER_SOURCE,
            "uid": os.getuid() if hasattr(os, "getuid") else None,
            "binding": {
                "subject": str(normalized.get("actor_id") or ""),
                "world_home": str(self.hermes_home),
            },
        }

    def _local_owner_refs(self, normalized: Mapping[str, Any]) -> list[str]:
        """Ledger-side provenance refs for a local-owner submission."""

        if normalized.get("authority") != AUTHORITY_LOCAL_OWNER_PHONE:
            return []
        context = self._local_owner_context(normalized)
        binding = context["binding"]
        return [
            "world.local_owner_phone:source=%s,uid=%s,subject=%s,home=%s"
            % (
                context["source"],
                context["uid"],
                binding["subject"],
                binding["world_home"],
            )
        ]

    # -- locking ----------------------------------------------------------

    @contextmanager
    def _commit_lock(self) -> Iterator[None]:
        if fcntl is None:
            raise ResolverError("resolver lock unavailable")
        lock_path = self.hermes_home / "data" / ".world_resolver.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as handle:
            deadline = time.monotonic() + 10.0
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ResolverError("resolver lock timeout") from exc
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # -- dependencies (M5) -----------------------------------------------

    def _dependencies(self) -> dict[str, Any]:
        """The live dependency values a caller would have observed."""

        deps: dict[str, Any] = {}
        state = self._world_state()
        if state is None:
            deps["world.revision"] = None
            deps["world.location"] = None
        else:
            deps["world.revision"] = state["revision"]
            deps["world.location"] = state["location"]["place_id"]

        substrate = self._substrate()
        if substrate is None:
            deps["body.revision"] = None
        else:
            module = self._substrate_module()
            deps["body.revision"] = substrate["revision"]
            deps["body.pose"] = substrate["pose"]
            hands = module.hand_view(substrate)
            for slot in module.HAND_SLOTS:
                deps[f"body.slot.{slot}"] = hands[slot]
        return deps

    @staticmethod
    def _stale_keys(
        observed: Mapping[str, Any], live: Mapping[str, Any]
    ) -> list[str]:
        stale: list[str] = []
        for key, value in observed.items():
            if key not in live:
                stale.append(key)
            elif live[key] != value:
                stale.append(key)
        return stale

    # -- execution --------------------------------------------------------

    def resolve(
        self,
        proposal: Mapping[str, Any],
        *,
        crash_after: Optional[str] = None,
    ) -> dict[str, Any]:
        """Resolve one proposal end to end.

        ``crash_after`` is test-only crash injection and must never be used in
        production; it exists so the crash windows of ticket section 14 can be
        tested deterministically.
        """

        if crash_after is not None and crash_after not in CRASH_POINTS:
            raise ResolverError(f"unknown crash point {crash_after!r}")

        try:
            normalized = normalize_proposal(proposal)
        except ResolverError as error:
            return _result(
                proposal if isinstance(proposal, Mapping) else {},
                status="rejected",
                code=CODE_INVALID_PROPOSAL,
                reason_code=str(error),
            )

        # authorization -- unchanged legacy contract, plus v0.4's explicit
        # local-owner phone authority.  That authority may carry exactly the
        # verbs in ``LOCAL_OWNER_ACTIONS`` and nothing else; it is stamped by the
        # service's ``submit_local_owner_action`` and can never be passed by a
        # client, so widening the list here does not widen the public surface.
        if normalized["authority"] == AUTHORITY_LOCAL_OWNER_PHONE:
            if normalized["action"] not in LOCAL_OWNER_ACTIONS:
                return _reject(
                    normalized, "rejected", CODE_AUTHORITY_REQUIRED,
                    reason_code="local_owner_authority_verb_not_allowed",
                )
        elif normalized["authority"] != AUTHORITY_VERIFIED_MAIN_SELF:
            return _reject(
                normalized, "rejected", CODE_AUTHORITY_REQUIRED,
                reason_code="verified_main_self_required",
            )
        if not normalized["confirm_apply"]:
            return _reject(
                normalized, "rejected", CODE_AUTHORITY_REQUIRED,
                reason_code="confirmation_required",
            )

        handler = {
            "MOVE": self._resolve_move,
            "PICK_UP": self._resolve_pick_up,
            "PLACE": self._resolve_place,
            "POSE": self._resolve_pose,
            "CONSUME": self._resolve_consume,
            "REST": self._resolve_rest,
            "ARRANGE": self._resolve_arrange,
            "TOGGLE_FIXTURE": self._resolve_toggle_fixture,
        }[normalized["action"]]

        try:
            with self._commit_lock():
                result = handler(normalized, crash_after=crash_after)
        except (ledger.LedgerError, ledger.LedgerCorruptError):
            return _reject(normalized, "failed", CODE_LEDGER_CORRUPT)
        except ResolverCrash:
            raise
        except ResolverError as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )
        except Exception:
            return _reject(normalized, "failed", CODE_WORLD_UNAVAILABLE)
        # v0.4: a local-owner result always says *who* submitted it, whatever the
        # verb was -- a MOVE must not read like a main-self decision just because
        # only the CONSUME path used to stamp the provenance.
        if (
            normalized.get("authority") == AUTHORITY_LOCAL_OWNER_PHONE
            and isinstance(result, dict)
        ):
            result.setdefault("authority", AUTHORITY_LOCAL_OWNER_PHONE)
            result.setdefault("local_owner", self._local_owner_context(normalized))
        return result

    # -- shared idempotency gate -----------------------------------------

    def _replay_frame(
        self, normalized: Mapping[str, Any]
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Return ``(already_finished_result, frame)`` for an execution_id."""

        frame = self.ledger.frame(normalized["execution_id"])
        if not frame["known"]:
            return None, frame

        state = frame["state"]
        if state == "committed":
            return (
                _result(
                    normalized,
                    status="already_applied",
                    code=CODE_ALREADY_APPLIED,
                    reason_code="already_applied",
                    before=frame["records"][-1]["raw"].get("before"),
                    after=frame["records"][-1]["raw"].get("after"),
                    ledger_status="committed",
                ),
                frame,
            )
        if state in IN_FLIGHT:
            # Never guess.  Flip a prepared run to uncertain and report UNKNOWN.
            if state == "prepared":
                self.ledger.append_unlocked(
                    self._entry(
                        normalized,
                        status="uncertain",
                        reason_code="reconciliation_required",
                        reconciliation_required=True,
                    )
                )
            return (
                _result(
                    normalized,
                    status="uncertain",
                    code=CODE_RECONCILIATION_REQUIRED,
                    reason_code="reconciliation_required",
                    before=frame["records"][0]["raw"].get("before"),
                    ledger_status="uncertain",
                    reconciliation_required=True,
                ),
                frame,
            )
        return (
            _reject(
                normalized,
                "failed" if state == "failed" else "rejected",
                CODE_NOT_ALLOWED,
                reason_code=frame["reason_code"] or "prior_attempt_failed",
                ledger_status=state,
            ),
            frame,
        )

    def _entry(
        self,
        normalized: Mapping[str, Any],
        *,
        status: str,
        reason_code: str,
        before: Any = None,
        after: Any = None,
        dependencies: Optional[Mapping[str, Any]] = None,
        world_mutation_refs: Optional[list[str]] = None,
        body_consequence_refs: Optional[list[str]] = None,
        cross_authority_refs: Optional[list[str]] = None,
        reconciliation_required: bool = False,
    ) -> dict[str, Any]:
        return {
            "execution_id": normalized["execution_id"],
            "action_type": normalized["action"],
            "actor_id": normalized["actor_id"],
            "status": status,
            "reason_code": reason_code,
            "timestamp": _now_iso(),
            "proposal_ref": normalized["action"],
            "observed_dependencies": dict(normalized["observed_dependencies"]),
            "expected_versions": dict(normalized["observed_dependencies"]),
            "world_mutation_refs": list(world_mutation_refs or []),
            "body_consequence_refs": list(body_consequence_refs or []),
            # v0.4: every local-owner submission carries its provenance refs on
            # every ledger entry it produces (not only on CONSUME), so a reader
            # can always tell an operator command from a main-self one.
            "cross_authority_refs": list(cross_authority_refs or [])
            + self._local_owner_refs(normalized),
            "before_state_ref": self.ledger.state_ref(before),
            "after_state_ref": self.ledger.state_ref(after),
            "before": before,
            "after": after,
            "reconciliation_required": reconciliation_required,
            "dependencies": dict(dependencies or {}),
        }

    # -- MOVE (delegates to the untouched World core) ---------------------

    def _departure_evidence(
        self, normalized: Mapping[str, Any], target_text: str, before: Any
    ) -> Optional[dict[str, Any]]:
        """This execution's own in-transit record, or ``None`` when there is none.

        Read-only.  A timed departure *is* a committed World mutation -- the owner wrote
        the travel record itself -- it is simply not an arrival, which is the only thing
        the instant-move postcondition knows how to check.
        """

        try:
            import world_travel_delivery as wtd
        except ImportError:  # pragma: no cover - flat/packaged import spelling
            return None
        try:
            if not wtd.travel_enabled(self.environment):
                return None
            state = wtd.load_state(self.hermes_home, self.environment)
        except Exception:
            return None
        if not isinstance(state, Mapping):
            return None
        record = None
        for item in state.get("travels") or []:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("execution_id")) == str(normalized.get("execution_id")):
                record = item
                break
        if record is None:
            return None
        if str(record.get("status")) == wtd.TRAVEL_STATUS_ARRIVED:
            # already arrived: the ordinary postcondition must decide, not this branch.
            return None
        if str(record.get("destination_location_id")) != str(target_text):
            return None
        origin = before.get("world_location_id") if isinstance(before, Mapping) else None
        if origin is not None and str(record.get("origin_location_id")) != str(origin):
            return None
        return {"travel_id": record.get("travel_id"), "status": record.get("status"),
                "origin_location_id": record.get("origin_location_id"),
                "destination_location_id": record.get("destination_location_id"),
                "expected_arrival_at": record.get("expected_arrival_at")}

    def _resolve_move(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        world = self._world_module()
        _require_params(normalized["params"], {"destination_location_id"})

        finished, _frame = self._replay_frame(normalized)
        if finished is not None:
            return finished

        target = normalized["params"].get("destination_location_id")
        try:
            target_text = _text(target, "destination_location_id", maximum=96)
        except ResolverError as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )
        # v0.4：室内复合节点（place/area）规范化——入口区归一化为 place，
        # 保证后置条件比较与状态写入一致。
        try:
            target_text = world.normalize_location_id(
                target_text, hermes_home=self.hermes_home, environment=self.environment
            )
        except Exception:
            pass

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE,
                reason_code="stale_dependency",
                before=None, dependencies=live, stale_dependencies=stale,
            )

        plan = world.plan_move_to(
            target_text, hermes_home=self.hermes_home, environment=self.environment
        )
        code = map_legacy_code(plan.get("reason_code"))
        if code == CODE_FEATURE_DISABLED:
            return _reject(
                normalized, "disabled", CODE_FEATURE_DISABLED,
                reason_code=plan.get("reason_code"),
            )
        if plan.get("status") == "noop":
            return _reject(
                normalized, "noop", CODE_NOOP,
                reason_code=plan.get("reason_code"), before=plan.get("before"),
                dependencies=live,
            )
        if plan.get("status") != "ready":
            return _reject(
                normalized, "rejected", code or CODE_NOT_ALLOWED,
                reason_code=plan.get("reason_code"), before=plan.get("before"),
                dependencies=live,
            )

        before = plan.get("before")
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="reachable",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        applied = world.apply_move_to(
            target_text,
            hermes_home=self.hermes_home,
            environment=self.environment,
            execution_id=normalized["execution_id"],
        )
        after = applied.get("after") or world.read_move_state(
            hermes_home=self.hermes_home, environment=self.environment
        )
        if crash_after == CRASH_AFTER_MUTATION:
            raise ResolverCrash("crash after mutation (test)")

        if applied.get("status") != "applied":
            uncertain = applied.get("status") == "uncertain"
            legacy = applied.get("reason_code")
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="uncertain" if uncertain else "failed",
                    reason_code=str(legacy or "move_failed"), before=before, after=after,
                    dependencies=live, reconciliation_required=uncertain,
                )
            )
            return _reject(
                normalized, "uncertain" if uncertain else "failed",
                map_legacy_code(legacy) or CODE_WORLD_UNAVAILABLE,
                reason_code=legacy, before=before, after=after, dependencies=live,
                ledger_status="uncertain" if uncertain else "failed",
                reconciliation_required=uncertain,
            )

        # W04 计时旅程：travel time 打开时 ``apply_move_to`` 提交的是一次**出发**，
        # 位置要到期才变。M13A 原来的后置条件（位置已等于目的地）对计时移动永远不成立，
        # 会把一次真实、已授权的出发报成 uncertain/POSTCONDITION_FAILED —— W05R 定位到
        # 的正是这里。计时出发有自己的、同样严格的后置条件：owner 自己的在途记录必须存在，
        # 且 execution_id / 起点 / 终点都与本次提交一致。位置权威仍在 owner 手里，
        # 这里只读它的证据，不写任何东西，也不提前认领到达。
        departure = self._departure_evidence(normalized, target_text, before)
        if departure is not None:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="committed", reason_code="move_departed",
                    before=before, after=after, dependencies=live,
                    world_mutation_refs=[
                        "world.travel=%s" % departure.get("travel_id")
                    ],
                )
            )
            return _result(
                normalized, status="applied", code=CODE_OK, reason_code="move_departed",
                before=before, after=after, dependencies=live, ledger_status="committed",
            )

        verified = (
            isinstance(after, Mapping)
            and after.get("world_location_id") == target_text
        )
        if not verified:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="uncertain", reason_code="world_postcondition_failed",
                    before=before, after=after, dependencies=live,
                    reconciliation_required=True,
                )
            )
            return _reject(
                normalized, "uncertain", CODE_POSTCONDITION_FAILED,
                reason_code="world_postcondition_failed", before=before, after=after,
                dependencies=live, ledger_status="uncertain", reconciliation_required=True,
            )

        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code="move_committed",
                before=before, after=after, dependencies=live,
                world_mutation_refs=[f"world.location={target_text}"],
            )
        )
        return _result(
            normalized, status="applied", code=CODE_OK, reason_code="move_committed",
            before=before, after=after, dependencies=live, ledger_status="committed",
        )

    # -- PICK_UP / PLACE / POSE (M6 substrate integration) ---------------

    def _substrate_gate(
        self, normalized: Mapping[str, Any]
    ) -> tuple[Optional[Any], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        module = self._substrate_module()
        if not module.feature_enabled(self.environment):
            return module, None, _reject(
                normalized, "disabled", CODE_FEATURE_DISABLED,
                reason_code=module.FEATURE_ENV + " is off",
            )
        state = self._substrate()
        if state is None:
            return module, None, _reject(
                normalized, "rejected", CODE_WORLD_UNAVAILABLE,
                reason_code="body_substrate_not_initialized",
            )
        # M6 completion: the body must actually be bound to this World before it
        # is allowed to act on it.  A world_id mismatch fails closed.
        binding = module.verify_binding(state, self._world_state())
        if not binding["bound"]:
            return module, None, _reject(
                normalized, "rejected", CODE_WORLD_UNAVAILABLE,
                reason_code="body_world_binding_invalid: "
                + "; ".join(binding.get("reasons") or ["unknown"]),
            )
        return module, state, None

    def _resolve_pick_up(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        module, state, blocked = self._substrate_gate(normalized)
        if blocked is not None:
            return blocked

        _require_params(normalized["params"], {"object_id", "slot_id"})
        finished, _frame = self._replay_frame(normalized)
        if finished is not None:
            return finished

        try:
            object_id = _text(normalized["params"].get("object_id"), "object_id")
            slot = _text(normalized["params"].get("slot_id"), "slot_id", maximum=32)
            if slot not in module.HAND_SLOTS:
                raise ResolverError(f"slot_id must be one of {list(module.HAND_SLOTS)}")
        except ResolverError as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE, reason_code="stale_dependency",
                dependencies=live, stale_dependencies=stale,
            )

        before = module.read_model(state)
        placement = module.placement_of(state, object_id)
        if placement is None:
            return _reject(
                normalized, "rejected", CODE_UNKNOWN_TARGET,
                reason_code=f"UNKNOWN_TARGET: {object_id} has no placement",
                before=before, dependencies=live,
            )
        if placement["kind"] == module.PLACEMENT_HAND:
            return _reject(
                normalized, "rejected", CODE_NOT_TAKEABLE,
                reason_code=f"{object_id} is already held",
                before=before, dependencies=live,
            )

        # M15: capability pre-flight (single vocabulary: world_object_capability)
        capability = _m15_capability_module()
        cap = _m15_capability_block(self.hermes_home, object_id)
        if capability is not None:
            verdict = capability.pick_up_verdict(
                obj={"capability": cap}, hand=_m15_slot_token(capability, slot)
            )
            if verdict.get("verdict") != capability.ALLOWED:
                return _reject(
                    normalized, "rejected", CODE_NOT_TAKEABLE,
                    reason_code="NOT_PORTABLE: %s" % verdict.get("reason"),
                    before=before, dependencies=live,
                )

        world = self._world_state()
        here = world["location"]["place_id"] if world else None
        if placement.get("location_id") != here:
            return _reject(
                normalized, "rejected", CODE_NOT_REACHABLE,
                reason_code="LOCATION_NOT_REACHABLE",
                before=before, dependencies=live,
            )

        occupant = module.slot_occupant(state, slot)
        if occupant is not None and occupant != object_id:
            return _reject(
                normalized, "rejected", CODE_OCCUPIED,
                reason_code=f"OCCUPIED: {slot} already holds {occupant}",
                before=before, dependencies=live,
            )
        if not module.reachability_inputs(state)["hands_usable"]:
            return _reject(
                normalized, "rejected", CODE_NOT_REACHABLE,
                reason_code=f"pose {state['pose']} cannot manipulate",
                before=before, dependencies=live,
            )

        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="pick_up_prepared",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        try:
            # Hand occupancy is derived from placement alone.  We deliberately do
            # NOT also take a resource reservation here: PICK_UP is a single
            # synchronous commit, so a reservation would be a second occupancy
            # truth and could go stale against the placement.
            updated = module.place_object(
                state, object_id, {"kind": "hand", "slot_id": slot},
                expected_revision=state["revision"],
            )
            module.SubstrateStore(module.substrate_path(self.hermes_home)).save(
                updated, expected_revision=state["revision"]
            )
        except module.BodySubstrateError as error:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="failed", reason_code="substrate_rejected",
                    before=before, dependencies=live,
                )
            )
            return _reject(
                normalized, "failed", CODE_NOT_ALLOWED,
                reason_code=str(error), before=before, dependencies=live,
                ledger_status="failed",
            )
        if crash_after == CRASH_AFTER_MUTATION:
            raise ResolverCrash("crash after mutation (test)")

        substrate = self._substrate()
        after = module.read_model(substrate)
        hints = ["hands_occupied"] if not module.free_slots(substrate) else []
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code="pick_up_committed",
                before=before, after=after, dependencies=live,
                world_mutation_refs=[f"placement.{object_id}=hand:{slot}"],
                cross_authority_refs=self._cross_authority_refs(object_id),
                body_consequence_refs=hints,
            )
        )
        return _result(
            normalized, status="applied", code=CODE_OK, reason_code="pick_up_committed",
            before=before, after=after, dependencies=live, ledger_status="committed",
            body_signal_hint=hints,
        )

    def _resolve_place(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        module, state, blocked = self._substrate_gate(normalized)
        if blocked is not None:
            return blocked

        _require_params(normalized["params"], {"object_id", "target"})
        finished, _frame = self._replay_frame(normalized)
        if finished is not None:
            return finished

        try:
            object_id = _text(normalized["params"].get("object_id"), "object_id")
            target = module.validate_placement_target(normalized["params"].get("target"))
        except (ResolverError, module.BodySubstrateError) as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE, reason_code="stale_dependency",
                dependencies=live, stale_dependencies=stale,
            )

        before = module.read_model(state)
        placement = module.placement_of(state, object_id)
        if placement is None:
            return _reject(
                normalized, "rejected", CODE_UNKNOWN_TARGET,
                reason_code=f"UNKNOWN_TARGET: {object_id} has no placement",
                before=before, dependencies=live,
            )
        if placement["kind"] != module.PLACEMENT_HAND:
            return _reject(
                normalized, "rejected", CODE_NOT_ALLOWED,
                reason_code=f"{object_id} is not held",
                before=before, dependencies=live,
            )

        # M15: capability pre-flight (the object must be placeable at all)
        capability = _m15_capability_module()
        cap = _m15_capability_block(self.hermes_home, object_id)
        modelled_kind = _m15_target_kind_token(capability, str(target.get("kind"))) \
            if capability is not None else None
        if capability is not None and cap and modelled_kind in getattr(
                capability, "MODELLED_TARGET_KINDS", ("surface", "container", "slot")):
            # a declared capability that says "not placeable" is honoured; an
            # undeclared one is not invented (the object is demonstrably held).
            # Kinds M15F does not model (room) are judged by the destination
            # rule below, which is the Resolver's own authority.
            verdict = capability.place_verdict(
                obj={"capability": cap}, held=True,
                target_kind=modelled_kind,
                target_permits=True, slot_available=True,
            )
            if verdict.get("verdict") != capability.ALLOWED:
                return _reject(
                    normalized, "rejected", CODE_NOT_ALLOWED,
                    reason_code="NOT_PLACEABLE: %s" % verdict.get("reason"),
                    before=before, dependencies=live,
                )

        # M15: destination rule (Resolver-owned, deterministic)
        world_state = self._world_state() or {}
        world_location = world_state.get("location") or {}
        world_place = world_location.get("place_id")
        world_area = world_location.get("area_id")
        dest_code, dest_detail = _m15_destination_verdict(
            target, world_place=world_place, hermes_home=self.hermes_home,
            environment=self.environment,
        )
        if dest_code is not None:
            return _reject(
                normalized, "rejected", _M15_DESTINATION_CODES.get(dest_code, CODE_UNKNOWN_TARGET),
                reason_code=f"{dest_code}: {dest_detail}",
                before=before, dependencies=live,
            )
        # The room a hand can set something down in is the room this body is
        # standing in: a room target names a *place*, it is not a teleport.  The
        # rule above only proves the room is part of this World and reachable;
        # here the resolver's own reading of the live state says the body is not
        # there, so nothing is placed and nothing is written.
        # It is only asserted when the World itself declares where the body is
        # (a state without an area cannot be compared, and then the topology
        # rule stays the only authority -- no behaviour is invented from a
        # missing fact).
        if (
            target["kind"] == module.PLACEMENT_ROOM
            and world_place
            and world_area
            and (target["location_id"], target["area_id"]) != (world_place, world_area)
        ):
            return _reject(
                normalized, "rejected", CODE_NOT_REACHABLE,
                reason_code="NOT_CURRENT_AREA: %s/%s is not the body's current "
                "area %s/%s"
                % (target["location_id"], target["area_id"], world_place, world_area),
                before=before, dependencies=live,
            )
        if target["kind"] == module.PLACEMENT_HAND and target["slot_id"] != placement["slot_id"]:
            other = module.slot_occupant(state, target["slot_id"])
            if other is not None and other != object_id:
                return _reject(
                    normalized, "rejected", CODE_OCCUPIED,
                    reason_code=f"OCCUPIED: {target['slot_id']} already holds {other}",
                    before=before, dependencies=live,
                )

        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="place_prepared",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        try:
            updated = module.place_object(
                state, object_id, target, expected_revision=state["revision"]
            )
            module.SubstrateStore(module.substrate_path(self.hermes_home)).save(
                updated, expected_revision=state["revision"]
            )
        except module.BodySubstrateError as error:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="failed", reason_code="substrate_rejected",
                    before=before, dependencies=live,
                )
            )
            return _reject(
                normalized, "failed", CODE_NOT_ALLOWED, reason_code=str(error),
                before=before, dependencies=live, ledger_status="failed",
            )
        if crash_after == CRASH_AFTER_MUTATION:
            raise ResolverCrash("crash after mutation (test)")

        after = module.read_model(self._substrate())
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code="place_committed",
                before=before, after=after, dependencies=live,
                world_mutation_refs=[f"placement.{object_id}={target['kind']}"],
            )
        )
        return _result(
            normalized, status="applied", code=CODE_OK, reason_code="place_committed",
            before=before, after=after, dependencies=live, ledger_status="committed",
        )

    def _resolve_pose(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        module, state, blocked = self._substrate_gate(normalized)
        if blocked is not None:
            return blocked

        _require_params(normalized["params"], {"pose", "orientation"})
        finished, _frame = self._replay_frame(normalized)
        if finished is not None:
            return finished

        try:
            pose = _text(normalized["params"].get("pose"), "pose", maximum=32)
            if pose not in module.POSES:
                raise ResolverError(f"pose must be one of {list(module.POSES)}")
            orientation = normalized["params"].get("orientation")
            if orientation is not None:
                orientation = _text(orientation, "orientation", maximum=32)
                if orientation not in module.ORIENTATIONS:
                    raise ResolverError(
                        f"orientation must be one of {list(module.ORIENTATIONS)}"
                    )
        except ResolverError as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE, reason_code="stale_dependency",
                dependencies=live, stale_dependencies=stale,
            )

        before = module.read_model(state)
        if state["pose"] == pose and (orientation is None or state["orientation"] == orientation):
            return _reject(
                normalized, "noop", CODE_NOOP, reason_code="already_in_pose",
                before=before, after=before, dependencies=live,
            )

        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="pose_prepared",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        try:
            updated = module.set_pose(
                state, pose, orientation=orientation, expected_revision=state["revision"]
            )
            module.SubstrateStore(module.substrate_path(self.hermes_home)).save(
                updated, expected_revision=state["revision"]
            )
        except module.BodySubstrateError as error:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="failed", reason_code="substrate_rejected",
                    before=before, dependencies=live,
                )
            )
            return _reject(
                normalized, "failed", CODE_NOT_ALLOWED, reason_code=str(error),
                before=before, dependencies=live, ledger_status="failed",
            )
        if crash_after == CRASH_AFTER_MUTATION:
            raise ResolverCrash("crash after mutation (test)")

        after = module.read_model(self._substrate())
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code="pose_committed",
                before=before, after=after, dependencies=live,
                world_mutation_refs=[f"body.pose={pose}"],
            )
        )
        return _result(
            normalized, status="applied", code=CODE_OK, reason_code="pose_committed",
            before=before, after=after, dependencies=live, ledger_status="committed",
        )

    # -- REST (v0.4: an explicit recovery command, never a timer) ---------

    def _travel_in_progress(self) -> Optional[dict[str, Any]]:
        """The owner's own in-transit record, read-only (``None`` when idle)."""

        try:
            import world_travel_delivery as wtd
        except ImportError:  # pragma: no cover - flat/packaged import spelling
            return None
        try:
            if not wtd.travel_enabled(self.environment):
                return None
            state = wtd.load_state(self.hermes_home, self.environment)
        except Exception:  # noqa: BLE001 - unreadable travel state is not "travelling"
            return None
        if not isinstance(state, Mapping):
            return None
        for item in state.get("travels") or []:
            if isinstance(item, Mapping) and item.get("status") == wtd.TRAVEL_STATUS_IN_PROGRESS:
                return dict(item)
        return None

    def _rest_surfaces_here(
        self, state: Mapping[str, Any], *, mode: str = "sleep_like"
    ) -> list[dict[str, Any]]:
        """Rest surfaces that are really here, derived from live placements.

        A bed in another room is not a bed: the owner furniture's own placement
        (which owns the fact) must be at this place.  The legacy bare
        ``bed_surface`` placement keeps working unchanged; a purchased bed or
        chair becomes usable through the instance-scoped surface of *that*
        instance, so two beds can never share one.
        """

        module = self._substrate_module()
        world = self._world_state() or {}
        here = (world.get("location") or {}).get("place_id")
        if not here:
            return []
        try:
            catalog = self._binding_module().owned_catalog_map(self.hermes_home)
            return module.rest_surfaces_here(
                state, catalog, location_id=here, mode=mode
            )
        except Exception:  # noqa: BLE001 - unreadable substrate means "no surface"
            return []

    def _rest_surface_here(self, state: Mapping[str, Any]) -> Optional[str]:
        """The first declared rest surface really placed where she is now."""

        surfaces = self._rest_surfaces_here(state, mode="sleep_like")
        return surfaces[0]["surface_id"] if surfaces else None

    def _resolve_rest(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        """Open or close one recovery session.  World facts in, a commit out.

        The Body's own thresholds (``RECOVERY_OPEN_FATIGUE``, already-active,
        no-open-session) are *not* decided here: the body runtime owns them and
        the service applies them before this resolver is contacted.  What the
        Resolver owns is the World side -- travelling, posture, and whether a
        rest surface is really present -- plus the one commit and its ledger
        evidence.
        """

        module, state, blocked = self._substrate_gate(normalized)
        if blocked is not None:
            return blocked

        _require_params(normalized["params"], {"state", "mode"})
        finished, _frame = self._replay_frame(normalized)
        if finished is not None:
            return finished

        try:
            desired = _text(
                normalized["params"].get("state"), "state", maximum=16
            ).lower()
            if desired not in REST_STATES:
                raise ResolverError(f"state must be one of {list(REST_STATES)}")
            raw_mode = normalized["params"].get("mode")
            if raw_mode is None:
                mode = "rest"
            else:
                mode = _text(raw_mode, "mode", maximum=16).lower()
                if mode not in REST_MODES:
                    raise ResolverError(f"mode must be one of {list(REST_MODES)}")
                if desired != "start":
                    raise ResolverError("mode is only accepted with state=start")
        except ResolverError as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE,
                reason_code="stale_dependency",
                dependencies=live, stale_dependencies=stale,
            )

        before = module.read_model(state)
        pose = str(before.get("pose") or "")
        surface_id: Optional[str] = None

        travelling = self._travel_in_progress()
        if travelling is not None:
            return _reject(
                normalized, "rejected", CODE_NOT_ALLOWED,
                reason_code="REST_WHILE_TRAVELLING: travel %s -> %s is in progress"
                % (travelling.get("origin_location_id"),
                   travelling.get("destination_location_id")),
                before=before, dependencies=live,
            )

        if desired == "start":
            if pose not in RESTING_POSES:
                return _reject(
                    normalized, "rejected", CODE_NOT_ALLOWED,
                    reason_code="REST_REQUIRES_RESTING_POSE: pose is %s" % (pose or "unknown"),
                    before=before, dependencies=live,
                )
            if mode == "sleep_like":
                if pose != REST_SLEEP_POSE:
                    return _reject(
                        normalized, "rejected", CODE_NOT_ALLOWED,
                        reason_code="SLEEP_LIKE_REQUIRES_LYING_POSE: pose is %s" % pose,
                        before=before, dependencies=live,
                    )
                surface_id = self._rest_surface_here(state)
                if surface_id is None:
                    return _reject(
                        normalized, "rejected", CODE_NOT_ALLOWED,
                        reason_code="SLEEP_LIKE_REQUIRES_REST_SURFACE: no declared "
                        "rest surface is placed here",
                        before=before, dependencies=live,
                    )

        mutation_ref = (
            "recovery:start:%s" % mode if desired == "start" else "recovery:stop"
        )
        committed_code = "recovery_%s_committed" % desired

        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="recovery_prepared",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        after = module.read_model(self._substrate() or state)
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code=committed_code,
                before=before, after=after, dependencies=live,
                world_mutation_refs=[mutation_ref],
                body_consequence_refs=["recovery:%s" % desired],
            )
        )
        result = _result(
            normalized, status="applied", code=CODE_OK, reason_code=committed_code,
            before=before, after=after, dependencies=live, ledger_status="committed",
        )
        # The Body input is a *fact of this commit*, not a guess by the adapter:
        # body_consequence maps these two keys onto the one legal RECOVERY input.
        result["recovery_state"] = desired
        result["recovery_mode"] = mode
        result["rest_surface_id"] = surface_id
        result["pose"] = pose
        return result
    # -- ARRANGE / TOGGLE_FIXTURE (v0.4 room-living) ----------------------

    def _room_environment(self):
        import world_room_environment

        return world_room_environment

    def _economy_module(self):
        import world_property_economy

        return world_property_economy

    def _resolve_arrange(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        """Move one piece of **owned, delivered, relocatable** furniture.

        This is deliberately not PICK_UP: nothing enters a hand, ``takeable`` is
        untouched, and a bed can be moved while still being impossible to pick
        up.  Children already resting on the furniture's own surface are carried
        in the same atomic substrate write, so a move can never strand them on a
        surface that no longer exists here.
        """

        module, state, blocked = self._substrate_gate(normalized)
        if blocked is not None:
            return blocked

        _require_params(normalized["params"], {"object_id", "target"})
        finished, _frame = self._replay_frame(normalized)
        if finished is not None:
            return finished

        try:
            object_id = _text(normalized["params"].get("object_id"), "object_id")
            target = module.validate_placement_target(
                normalized["params"].get("target")
            )
        except (ResolverError, module.BodySubstrateError) as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )
        if target["kind"] not in (module.PLACEMENT_ROOM, module.PLACEMENT_SURFACE):
            return _reject(
                normalized, "rejected", CODE_NOT_ALLOWED,
                reason_code="DESTINATION_NOT_ALLOWED: furniture is arranged into a "
                "room or installed as its own surface",
            )

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE, reason_code="stale_dependency",
                dependencies=live, stale_dependencies=stale,
            )

        before = module.read_model(state)
        placements = before["placements"]
        current = placements.get(object_id)
        if current is None:
            return _reject(
                normalized, "rejected", CODE_UNKNOWN_TARGET,
                reason_code=f"UNKNOWN_TARGET: {object_id} has no placement",
                before=before, dependencies=live,
            )
        if dict(current) == dict(target):
            return _reject(
                normalized, "noop", CODE_NOOP, reason_code="already_arranged",
                before=before, after=before, dependencies=live,
            )

        world_state = self._world_state() or {}
        world_location = world_state.get("location") or {}
        economy = self._economy_module()
        verdict = economy.arrange_verdict(
            item_instance_id=object_id,
            target=target,
            placements=placements,
            world_location=world_location,
            hermes_home=self.hermes_home,
            environment=self.environment,
        )
        if not verdict.get("allowed"):
            detail = verdict.get("reason_code") or "NOT_ALLOWED"
            if verdict.get("detail"):
                detail = "%s: %s" % (detail, verdict["detail"])
            return _reject(
                normalized, "rejected", CODE_NOT_ALLOWED, reason_code=detail,
                before=before, dependencies=live,
            )

        declared_surfaces = [
            module.surface_instance_id(kind, object_id)
            for kind in (verdict.get("declaration") or {}).get("surfaces") or ()
        ]
        if str(current.get("kind")) == module.PLACEMENT_SURFACE:
            current_surface = str(current.get("surface_id"))
            if current_surface not in declared_surfaces:
                declared_surfaces.append(current_surface)
        # Children are the objects resting on this furniture's own surface.  The
        # furniture instance itself is never its own child, even when it is
        # placed *as* that surface.
        carried = sorted(
            child
            for child, child_target in placements.items()
            if child != object_id
            and str((child_target or {}).get("kind")) == module.PLACEMENT_SURFACE
            and str((child_target or {}).get("surface_id")) in declared_surfaces
        )

        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="arrange_prepared",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        try:
            updated = module.place_object(
                state, object_id, target, expected_revision=state["revision"]
            )
            for child in carried:
                updated = module.place_object(
                    updated,
                    child,
                    {
                        "kind": module.PLACEMENT_SURFACE,
                        "location_id": target["location_id"],
                        "surface_id": placements[child]["surface_id"],
                    },
                )
            module.SubstrateStore(module.substrate_path(self.hermes_home)).save(
                updated, expected_revision=state["revision"]
            )
        except module.BodySubstrateError as error:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="failed", reason_code="substrate_rejected",
                    before=before, dependencies=live,
                )
            )
            return _reject(
                normalized, "failed", CODE_NOT_ALLOWED, reason_code=str(error),
                before=before, dependencies=live, ledger_status="failed",
            )
        if crash_after == CRASH_AFTER_MUTATION:
            raise ResolverCrash("crash after mutation (test)")

        after = module.read_model(self._substrate())
        refs = [f"placement.{object_id}={target['kind']}"]
        refs.extend(f"placement.{child}=carried_with_surface" for child in carried)
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code="arrange_committed",
                before=before, after=after, dependencies=live,
                world_mutation_refs=refs,
                cross_authority_refs=self._cross_authority_refs(object_id),
            )
        )
        result = _result(
            normalized, status="applied", code=CODE_OK,
            reason_code="arrange_committed", before=before, after=after,
            dependencies=live, ledger_status="committed",
        )
        result["arranged"] = {
            "object_id": object_id,
            "target": dict(target),
            "carried": carried,
            "surface_ids": declared_surfaces,
        }
        return result

    def _resolve_toggle_fixture(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        """Set one declared fixture's power **absolutely** (``on`` / ``off``).

        ``state`` is required and absolute: there is no blind toggle, so a
        retried command cannot flip the lamp back.  The same target state is a
        zero-write ``noop``; only a real change writes substrate and commits.
        """

        module, state, blocked = self._substrate_gate(normalized)
        if blocked is not None:
            return blocked

        _require_params(normalized["params"], {"object_id", "state"})
        finished, _frame = self._replay_frame(normalized)
        if finished is not None:
            return finished

        try:
            object_id = _text(normalized["params"].get("object_id"), "object_id")
            wanted = _text(
                normalized["params"].get("state"), "state", maximum=8
            ).lower()
        except ResolverError as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )
        if wanted not in module.FIXTURE_POWERS:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL,
                reason_code="state must be one of %s" % (list(module.FIXTURE_POWERS),),
            )

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE, reason_code="stale_dependency",
                dependencies=live, stale_dependencies=stale,
            )

        before = module.read_model(state)
        environment = self._room_environment()
        try:
            catalog = self._binding_module().owned_catalog_map(self.hermes_home)
        except Exception:  # noqa: BLE001
            catalog = {}
        declaration = environment.declaration_of_object(object_id, catalog)
        if declaration is None or not declaration.get("powerable"):
            return _reject(
                normalized, "rejected", CODE_NOT_ALLOWED,
                reason_code="NOT_A_FIXTURE: %s has no declared powerable fixture"
                % object_id,
                before=before, dependencies=live,
            )
        placement = module.placement_of(state, object_id)
        world = self._world_state() or {}
        here = (world.get("location") or {}).get("place_id")
        here_area = (world.get("location") or {}).get("area_id")
        if placement is None or here is None:
            return _reject(
                normalized, "rejected", CODE_NOT_REACHABLE,
                reason_code="FIXTURE_NOT_HERE: %s is not placed here" % object_id,
                before=before, dependencies=live,
            )
        if str(placement.get("location_id")) != str(here) or (
            placement.get("area_id") is not None
            and here_area is not None
            and str(placement.get("area_id")) != str(here_area)
        ):
            return _reject(
                normalized, "rejected", CODE_NOT_REACHABLE,
                reason_code="FIXTURE_NOT_HERE: %s is in another place/area" % object_id,
                before=before, dependencies=live,
            )

        existing = module.fixture_of(state, object_id)
        current_power = (existing or {}).get("power")
        if current_power == wanted:
            return _reject(
                normalized, "noop", CODE_NOOP,
                reason_code="fixture_already_%s" % wanted,
                before=before, after=before, dependencies=live,
            )

        since = _now_iso()
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="fixture_prepared",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        try:
            updated = module.set_fixture(
                state,
                object_id,
                wanted,
                since=since,
                declared_source=declaration.get("declared_source"),
                expected_revision=state["revision"],
            )
            module.SubstrateStore(module.substrate_path(self.hermes_home)).save(
                updated, expected_revision=state["revision"]
            )
        except module.BodySubstrateError as error:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="failed", reason_code="substrate_rejected",
                    before=before, dependencies=live,
                )
            )
            return _reject(
                normalized, "failed", CODE_NOT_ALLOWED, reason_code=str(error),
                before=before, dependencies=live, ledger_status="failed",
            )
        if crash_after == CRASH_AFTER_MUTATION:
            raise ResolverCrash("crash after mutation (test)")

        stored = module.fixture_of(updated, object_id) or {}
        after = module.read_model(self._substrate())
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code="fixture_committed",
                before=before, after=after, dependencies=live,
                world_mutation_refs=[f"world.fixture={object_id}:{wanted}"],
                cross_authority_refs=self._cross_authority_refs(object_id),
            )
        )
        result = _result(
            normalized, status="applied", code=CODE_OK,
            reason_code="fixture_committed", before=before, after=after,
            dependencies=live, ledger_status="committed",
        )
        result["fixture"] = {
            "object_id": object_id,
            "state": wanted,
            "previous_state": current_power,
            "since": stored.get("since"),
            "declared_source": stored.get("declared_source"),
            "band": declaration.get("light_band"),
        }
        return result

    # -- CONSUME (F: one instance in, one receipt out, one body consequence) --

    def _consume_contract(self):
        import world_consumable_contract

        return world_consumable_contract

    def _consume_replay(
        self, normalized: Mapping[str, Any], object_id: str
    ) -> Optional[dict[str, Any]]:
        """Replay gate for CONSUME: the receipt, not just the frame, decides.

        A committed execution replayed with the *same* object is ``already_applied``
        (nothing is consumed twice).  The same execution id replayed with a
        different object is a payload mismatch and is refused -- it must never be
        reported as an already-applied consumption.
        """

        finished, _frame = self._replay_frame(normalized)
        if finished is None:
            return None
        if finished.get("status") != "already_applied":
            return finished

        contract = self._consume_contract()
        receipt = self._consumption_receipt_for(normalized["execution_id"])
        match = contract.verify_receipt(
            receipt,
            execution_id=normalized["execution_id"],
            object_id=object_id,
            body_id=normalized["body_id"],
        )
        if match["ok"]:
            return finished
        return _reject(
            normalized, "rejected", CODE_INVALID_PROPOSAL,
            reason_code="EXECUTION_PAYLOAD_MISMATCH: %s is already recorded as a "
            "different consumption (%s)"
            % (normalized["execution_id"], ",".join(match["reasons"])),
        )

    def _resolve_consume(
        self, normalized: Mapping[str, Any], *, crash_after: Optional[str]
    ) -> dict[str, Any]:
        """Consume exactly one object instance, through the ordinary pipeline.

        Nothing here bypasses MOVE/PICK_UP/PLACE: the same substrate gate, the
        same dependency freshness check, the same execution ledger, the same
        crash windows.  What is specific is the *evidence* rule -- the live
        placement and its consumption receipt are produced by one state change
        and persisted by one atomic save, and the commit is only reached when
        that receipt matches this execution, this object and this body.
        """

        module, state, blocked = self._substrate_gate(normalized)
        if blocked is not None:
            return blocked

        _require_params(normalized["params"], {"object_id"})

        try:
            object_id = _text(normalized["params"].get("object_id"), "object_id")
        except ResolverError as error:
            return _reject(
                normalized, "rejected", CODE_INVALID_PROPOSAL, reason_code=str(error)
            )

        # Replay is judged against the recorded receipt, not just the ledger
        # frame: an execution id may not be re-used to consume a *different*
        # instance and be told "already applied".
        finished = self._consume_replay(normalized, object_id)
        if finished is not None:
            return finished

        live = self._dependencies()
        stale = self._stale_keys(normalized["observed_dependencies"], live)
        if stale:
            return _reject(
                normalized, "rejected", CODE_STALE_STATE, reason_code="stale_dependency",
                dependencies=live, stale_dependencies=stale,
            )

        before = module.read_model(state)

        # Positive evidence first: a receipt that already names this object means
        # the instance is gone because *something* consumed it.  Say so instead
        # of pretending it never existed.
        previous = module.receipts_for_object(state, object_id)
        if previous:
            return _reject(
                normalized, "rejected", CODE_NOT_ALLOWED,
                reason_code="ALREADY_CONSUMED: %s was consumed by %s at %s"
                % (object_id, previous[-1]["execution_id"], previous[-1]["consumed_at"]),
                before=before, dependencies=live,
            )

        placement = module.placement_of(state, object_id)
        if placement is None:
            return _reject(
                normalized, "rejected", CODE_UNKNOWN_TARGET,
                reason_code="UNKNOWN_TARGET: %s has no live placement "
                "(a missing object is not a consumption record)" % object_id,
                before=before, dependencies=live,
            )

        # The body must be the one this substrate describes.  A proposal naming a
        # different body cannot consume this world's food.
        if str(normalized["body_id"]) != str(state["body_id"]):
            return _reject(
                normalized, "rejected", CODE_NOT_ALLOWED,
                reason_code="BODY_MISMATCH: proposal body_id=%s, substrate body_id=%s"
                % (normalized["body_id"], state["body_id"]),
                before=before, dependencies=live,
            )

        contract = self._consume_contract()
        world = self._world_state() or {}
        here = (world.get("location") or {}).get("place_id") if world else None
        # Economy facts are a *permission* input: the tri-state read (absent /
        # present / unreadable) is owned by the contract module, and an
        # unreadable document refuses this consumption instead of being
        # flattened to "no economy objects".
        facts = contract.economy_facts(self.hermes_home)

        verdict = contract.consumption_verdict(
            object_id=object_id,
            placement=placement,
            declaration=contract.declaration_of(
                object_id, self.hermes_home, economy=facts.get("economy")
            ),
            economy=facts,
            current_place=here,
            hands_usable=bool(module.reachability_inputs(state)["hands_usable"]),
            now=self._clock(),
            environment=self.environment,
        )
        if not verdict["allowed"]:
            return _reject(
                normalized, "rejected",
                _CONSUME_VERDICT_CODES.get(verdict["reason_code"], CODE_NOT_ALLOWED),
                reason_code="%s: %s" % (verdict["reason_code"], verdict["detail"]),
                before=before, dependencies=live,
            )

        self.ledger.append_unlocked(
            self._entry(
                normalized, status="prepared", reason_code="consume_prepared",
                before=before, dependencies=live,
            )
        )
        if crash_after == CRASH_AFTER_PREPARED:
            raise ResolverCrash("crash after prepared (test)")

        # The receipt's own timestamp comes from the service clock (the same
        # moment the opening-hours rule was evaluated at), not from an ambient
        # wall clock, so the record is reproducible.
        noted_at = self._clock()
        receipt = contract.build_receipt(
            execution_id=normalized["execution_id"],
            object_id=object_id,
            body_id=state["body_id"],
            actor_id=normalized["actor_id"],
            before_revision=state["revision"],
            consumed_at=(
                noted_at.isoformat()
                if isinstance(noted_at, datetime) else str(noted_at)
            ),
            source=verdict["source"],
            placement_before=placement,
        )
        try:
            # One state change: the placement is removed and the receipt is filed
            # together, then saved once.  Either both are on disk or neither is.
            updated = module.consume_object(
                state, object_id, receipt, expected_revision=state["revision"]
            )
            module.SubstrateStore(module.substrate_path(self.hermes_home)).save(
                updated, expected_revision=state["revision"]
            )
        except module.BodySubstrateError as error:
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="failed", reason_code="substrate_rejected",
                    before=before, dependencies=live,
                )
            )
            return _reject(
                normalized, "failed", CODE_NOT_ALLOWED, reason_code=str(error),
                before=before, dependencies=live, ledger_status="failed",
            )
        if crash_after == CRASH_AFTER_MUTATION:
            raise ResolverCrash("crash after mutation (test)")

        # Postcondition: re-read the owner file and require *this execution's*
        # receipt plus the absence of the placement.  Absence alone proves
        # nothing, so it is only ever accepted next to its receipt.
        fresh = self._substrate()
        stored = (
            module.consumption_receipt(fresh, normalized["execution_id"])
            if fresh is not None else None
        )
        match = contract.verify_receipt(
            stored,
            execution_id=normalized["execution_id"],
            object_id=object_id,
            body_id=state["body_id"],
        )
        placement_gone = fresh is not None and module.placement_of(fresh, object_id) is None
        if not (match["ok"] and placement_gone):
            self.ledger.append_unlocked(
                self._entry(
                    normalized, status="uncertain",
                    reason_code="world_postcondition_failed",
                    before=before, dependencies=live, reconciliation_required=True,
                )
            )
            return _reject(
                normalized, "uncertain", CODE_POSTCONDITION_FAILED,
                reason_code="world_postcondition_failed: receipt=%s"
                % (",".join(match["reasons"]) or "unverified"),
                before=before, dependencies=live, ledger_status="uncertain",
                reconciliation_required=True,
            )

        after = module.read_model(fresh)
        self.ledger.append_unlocked(
            self._entry(
                normalized, status="committed", reason_code="consume_committed",
                before=before, after=after, dependencies=live,
                world_mutation_refs=[
                    f"placement.{object_id}=consumed",
                    f"world.consumption={stored['receipt_id']}",
                ],
                # Only a proven-present economy can produce an economy instance,
                # so cross-authority bookkeeping is only meaningful then.  The
                # local-owner provenance refs are added by ``_entry`` itself.
                cross_authority_refs=(
                    self._cross_authority_refs(object_id)
                    if contract.is_economy_instance(facts["economy"], object_id)
                    else []
                ),
            )
        )
        result = _result(
            normalized, status="applied", code=CODE_OK,
            reason_code="consume_committed", before=before, after=after,
            dependencies=live, ledger_status="committed",
        )
        # The receipt travels with the result so a caller can reconcile this
        # execution without re-deriving anything (read-only evidence).
        result["object_id"] = object_id
        result["consumption_receipt"] = stored
        if verdict.get("purchase_voucher") is not None:
            result["purchase_voucher"] = dict(verdict["purchase_voucher"])
        if normalized["authority"] == AUTHORITY_LOCAL_OWNER_PHONE:
            result["authority"] = AUTHORITY_LOCAL_OWNER_PHONE
            result["local_owner"] = self._local_owner_context(normalized)
        return result

    # -- reconciliation ---------------------------------------------------

    def reconcile(self, execution_id: str) -> dict[str, Any]:
        """Report what is known about an execution.  Never invents an outcome."""

        frame = self.ledger.frame(execution_id)
        if not frame["known"]:
            return {
                "kind": "world_action_reconciliation",
                "schema_version": SCHEMA_VERSION,
                "execution_id": execution_id,
                "known": False,
                "result": None,
                "verdict": "NOT_FOUND",
            }
        if frame["reason_code"] == "reconciliation_required" or frame["reconciliation_required"]:
            verdict = "UNKNOWN"
        elif frame["result"] == ledger.RESULT_SUCCESS:
            verdict = "SUCCESS"
        elif frame["result"] == ledger.RESULT_FAILURE:
            verdict = "FAILURE"
        else:
            verdict = "UNKNOWN"
        return {
            "kind": "world_action_reconciliation",
            "schema_version": SCHEMA_VERSION,
            "execution_id": execution_id,
            "known": True,
            "state": frame["state"],
            "result": frame["result"],
            "verdict": verdict,
            "reconciliation_required": frame["reconciliation_required"],
            "action_type": frame["action_type"],
            "attempts": frame["attempts"],
            "consumption_receipt": (
                self._consumption_receipt_for(execution_id)
                if frame.get("action_type") == "CONSUME" else None
            ),
        }

    def _consumption_receipt_for(self, execution_id: str) -> Optional[dict[str, Any]]:
        """The owner receipt for a CONSUME execution (read-only, may be ``None``).

        Reported next to the ledger verdict so a reconciler never has to decide
        between "the object is not there" and "this execution ate it".
        """

        try:
            module = self._substrate_module()
            state = self._substrate()
            if state is None:
                return None
            return module.consumption_receipt(state, execution_id)
        except Exception:  # pragma: no cover - unreadable owner is reported as absent
            return None


def execute_move_compat(
    target_location_id: Any,
    *,
    execution_id: Any,
    authority: str,
    confirm_apply: bool = False,
    hermes_home: Optional[Path | str] = None,
    environment: Optional[Mapping[str, str]] = None,
    observed_dependencies: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Compatibility wrapper: the old ``execute_world_move`` signature.

    Behaviour is preserved for callers that do not supply observed dependency
    versions -- the dependency check is simply vacuous, exactly like before.
    """

    resolver = WorldResolver(hermes_home, environment=environment)
    return resolver.resolve(
        {
            "action": "MOVE",
            "execution_id": execution_id,
            "authority": authority,
            "confirm_apply": confirm_apply,
            "observed_dependencies": dict(observed_dependencies or {}),
            "params": {"destination_location_id": target_location_id},
        }
    )
