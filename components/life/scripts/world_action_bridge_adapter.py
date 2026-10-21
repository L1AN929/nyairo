#!/usr/bin/env python3
"""B6-2: the narrow, real World action adapter for the Life AR-0 slot.

Authority chain (unchanged -- this module adds no new authority):

    Life (this module)            builds an *intent*; it mints nothing
    World Intent Executor         the ONLY place a one-shot authorization is issued
    World Gateway (gateway.sock)  stamps authority/actor and consumes it
    World Resolver                the sole committer of World state

Why this exists
---------------
AR-0 (``action_reality_ledger``) already owns the Life-side action ledger, its
receipts, its reconciliation and its submission fence.  Its only dependency on
the World is a small duck-typed resolver:

    resolve_action(*, idempotency_key, verb, target, expected_dependencies,
                   fault_hook) -> {"execution_id", "status", "code",
                                   "transport_result", ...}

``FakeWorldResolver`` implements that against an in-memory sandbox world; the
V0.1 production root wired ``RefusingWorldResolver``, which refuses outright.
This module supplies the third implementation: one that reaches the real World
through the existing Intent Executor + ``WorldBodyClient`` + gateway socket.

What it deliberately does NOT do
--------------------------------
* never mints a World authority -- the gateway refuses a caller-supplied one;
* never writes ``world_state.json`` -- the Resolver is the sole writer;
* never opens the gateway socket itself -- ``WorldBodyClient`` owns transport,
  and only the existing *writable* ``gateway.sock`` is used; the read-only
  ``read.sock`` port stays read-only;
* does not require ``bridge_init.register()`` -- that wires the chat plugin's
  LLM tools, which is not a Life prerequisite;
* adds no second World action ledger: AR-0's store remains the only Life-side
  ledger, and the World's own resolver ledger remains the only World truth.

Concurrency evidence
--------------------
AR-0 hands over ``expected_dependencies`` from the frozen payload.  The bridge's
frozen rule (``action_intent`` section 5) is that dependency evidence is produced
by the *authority side* at submission time, so the Intent Executor re-reads a
fresh snapshot and this module never forwards a caller-supplied revision into the
Resolver proposal.  ``expected_dependencies`` therefore stays AR-0-side
bookkeeping, exactly as before.
"""
from __future__ import annotations

import copy
import os
import pathlib
import sys
from typing import Any, Mapping, Optional

import action_reality_ledger as ar0
import life_body_action_targets as body_actions
import life_daily_action_targets as daily_actions

#: Where the World bridge modules live, when the tree layout is not discoverable.
WORLD_BRIDGE_DIR_ENV = "NYAIRO_WORLD_BRIDGE_DIR"

#: The verbs this adapter maps faithfully.  ``MOVE``/``CONSUME`` carry one parameter
#: each, so the AR-0 ``target`` *is* the parameter value.  ``POSE``/``REST`` need two
#: parameters each and travel as one encoded ``bodyact:`` target
#: (``life_body_action_targets``); ``PICK_UP``/``PLACE`` need two or three and travel as
#: one encoded ``dailyact:`` target (``life_daily_action_targets``).  This adapter decodes
#: both at submission time -- the AR-0 payload keeps its single-target shape, and no verb
#: ever gains a free-form ``params`` mapping that the caller could fill in.
SUPPORTED_VERBS = ("MOVE", "CONSUME", "POSE", "REST", "PICK_UP", "PLACE")

#: Verbs whose whole parameter set lives inside the encoded target.
BODY_ACTION_VERBS = body_actions.BODY_ACTION_VERBS
DAILY_ACTION_VERBS = daily_actions.DAILY_ACTION_VERBS

#: Verb -> the Resolver's own single parameter name (world_action_resolver._require_params).
#: Both entries are total: nothing here invents a second parameter, and the AR-0
#: single target *is* the parameter value.
VERB_PARAMETER = {"MOVE": "destination_location_id", "CONSUME": "object_id"}


def resolver_params(verb: str, target: str) -> dict[str, Any]:
    """The owner's own parameter mapping for one AR-0 target.

    Single-parameter verbs map the target onto the Resolver's parameter name, exactly as
    before.  A body verb is decoded from its encoded target, and a target that cannot be
    decoded completely raises :class:`life_body_action_targets.BodyActionTargetError` -- a
    half-specified posture is never guessed at, and it never reaches the owner.  A daily
    verb is decoded the same way from its encoded ``dailyact:`` target
    (:class:`life_daily_action_targets.DailyActionTargetError`): the object, the hand slot
    and the placement destination are all read back from the ref, never re-derived here.
    """

    if verb in BODY_ACTION_VERBS:
        parsed = body_actions.parse_body_action_target(target)
        if parsed is None:
            raise body_actions.BodyActionTargetError(
                "verb %s needs a %s target, got %r"
                % (verb, body_actions.BODY_ACTION_REF_PREFIX, target))
        return dict(parsed["params"])
    if verb in DAILY_ACTION_VERBS:
        parsed = daily_actions.parse_daily_action_target(target)
        if parsed is None:
            raise daily_actions.DailyActionTargetError(
                "verb %s needs a %s target, got %r"
                % (verb, daily_actions.DAILY_ACTION_REF_PREFIX, target))
        return dict(parsed["params"])
    return {VERB_PARAMETER[verb]: target}


class WorldBridgeUnavailable(RuntimeError):
    """The World bridge modules are not reachable from this tree."""


def discover_bridge_dir(start: Optional[pathlib.Path] = None) -> Optional[pathlib.Path]:
    """Locate ``<repo>/components/world/service/bridge`` without importing it."""

    explicit = os.environ.get(WORLD_BRIDGE_DIR_ENV)
    if explicit:
        candidate = pathlib.Path(explicit)
        return candidate if candidate.is_dir() else None
    here = pathlib.Path(start or __file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "world" / "service" / "bridge"
        if (candidate / "intent_executor.py").is_file():
            return candidate
    return None


class WorldBridgeResolver:
    """A real World Resolver, in the shape AR-0's adapter already speaks.

    The name is deliberate: AR-0 calls this object a "World Resolver" and treats
    it as the sole authority over World facts.  This class does not resolve
    anything itself -- it routes to the real Intent Executor, which routes to the
    real gateway, which routes to the real Resolver.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        environment: Optional[Mapping[str, str]] = None,
        bridge_dir: Optional[pathlib.Path | str] = None,
    ) -> None:
        self.environment = dict(environment if environment is not None else os.environ)
        self._client = client
        self._bridge_dir = pathlib.Path(bridge_dir) if bridge_dir else None
        self._modules: Optional[tuple[Any, Any, Any]] = None
        #: AR-0 reads this for its own reconcile fallback; kept in the same shape
        #: the sandbox resolver uses so nothing downstream needs to special-case.
        self.by_idem_key: dict[str, dict[str, Any]] = {}
        self.last_intent: Optional[dict[str, Any]] = None
        self.last_result: Optional[dict[str, Any]] = None

    # ---- the World bridge, loaded on first real use ----------------------
    def _bridge(self) -> tuple[Any, Any, Any]:
        if self._modules is not None:
            return self._modules
        directory = self._bridge_dir or discover_bridge_dir()
        if directory is None:
            raise WorldBridgeUnavailable(
                "the World bridge modules are not reachable from this tree; set "
                "%s to <repo>/components/world/service/bridge" % WORLD_BRIDGE_DIR_ENV
            )
        text = str(directory)
        if text not in sys.path:
            sys.path.insert(0, text)
        import action_intent as world_action_intent  # noqa: E402
        import intent_executor as world_intent_executor  # noqa: E402
        import world_body_client as world_body_client  # noqa: E402

        self._modules = (world_action_intent, world_intent_executor, world_body_client)
        return self._modules

    @property
    def client(self) -> Any:
        if self._client is None:
            _, _, wbc = self._bridge()
            self._client = wbc.WorldBodyClient(environment=self.environment)
        return self._client

    def describe(self) -> dict[str, Any]:
        directory = self._bridge_dir or discover_bridge_dir()
        return {
            "bridge_dir": str(directory) if directory else None,
            "integration_mode": self.environment.get("WORLD_BODY_INTEGRATION_MODE"),
            "socket": self.environment.get("WORLD_BODY_SOCKET"),
            "intent_gate": self.environment.get("WORLD_BODY_INTENT_MUTATION_GATE"),
            "auth_registry_dir": self.environment.get("WORLD_ACTION_AUTH_REGISTRY_DIR"),
            "supported_verbs": list(SUPPORTED_VERBS),
        }

    # ---- identity --------------------------------------------------------
    @staticmethod
    def execution_id_for(idempotency_key: str, verb: str, target: str) -> str:
        """Deterministic execution id.

        Requirement: an UNKNOWN outcome must be reconcilable with the *same*
        execution id, and a retry must never mint a new one.  Deriving it from
        the AR-0 idempotency key (which itself already contains what the action
        intends to write) makes both properties structural rather than a
        convention.
        """

        return "world-exec:" + ar0.sha256_hex(
            "ar0:%s:%s:%s" % (idempotency_key, verb, target)
        )[:32]

    def build_intent(
        self,
        *,
        execution_id: str,
        verb: str,
        target: str,
        decision_ref: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Shape one World Action Intent for the existing Intent Executor.

        This is a transport structure, not a new decision contract: the decision
        already happened (AG-1 Decision + AG-2 Adoption + AR-0's own frozen
        proposal).  The Intent Executor validates it, reads a fresh snapshot
        itself and mints the one-shot authorization.
        """

        ai, _, _ = self._bridge()
        moment = created_at or ar0._iso(ar0.now())
        intent: dict[str, Any] = {
            "kind": ai.KIND_INTENT,
            "schema_version": ai.SCHEMA_INTENT,
            "intent_id": "",
            "decision_ref": decision_ref,
            "actor_id": "chiyo",
            "action": "SELECT",
            "verb": verb,
            "params": resolver_params(verb, target),
            "target_summary": "%s -> %s" % (verb, target),
            "created_at": moment,
            "source_provenance": "ar0_world_bridge_adapter",
            "authorization_ref": "pending",
            "execution_id": execution_id,
            "intent_status": ai.STATUS_AUTHORIZED,
            # The authority side fills real dependency evidence at submission
            # time (frozen rule); this fingerprint only names the intent.
            "snapshot_fingerprint": ar0.sha256_hex("ar0:%s" % execution_id)[:32],
            "origin": "life_ar0",
            "intent_summary": "AR-0 %s to %s" % (verb, target),
        }
        intent["intent_id"] = "world-intent:" + ar0.sha256_hex(
            "%s:%s:%s" % (execution_id, verb, target)
        )[:32]
        return intent

    # ---- the AR-0 resolver contract --------------------------------------
    def resolve_action(
        self,
        *,
        idempotency_key: str,
        verb: str,
        target: str,
        expected_dependencies: Mapping[str, Any],
        fault_hook: Optional[Any] = None,
    ) -> dict[str, Any]:
        cached = self.by_idem_key.get(idempotency_key)
        if cached is not None:
            return copy.deepcopy(cached)

        ai, executor, wbc = self._bridge()
        execution_id = self.execution_id_for(idempotency_key, verb, target)

        if verb not in SUPPORTED_VERBS:
            return self._record(idempotency_key, {
                "execution_id": execution_id,
                "status": "rejected",
                "code": "UNSUPPORTED_VERB_%s" % verb,
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                "detail": "verb %s cannot be expressed by a single AR-0 target" % verb,
            })

        if verb in BODY_ACTION_VERBS:
            # A body verb's parameters live inside the target.  An undecodable target is a
            # definite pre-submission refusal: nothing was minted, nothing reached the
            # owner, and AR-0 may retry under the same action id once the target is whole.
            try:
                resolver_params(verb, target)
            except body_actions.BodyActionTargetError as error:
                return self._record(idempotency_key, {
                    "execution_id": execution_id,
                    "status": "rejected",
                    "code": "BODY_ACTION_TARGET_INVALID",
                    "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                    "detail": str(error) or "body action target is incomplete",
                })

        if verb in DAILY_ACTION_VERBS:
            # Same rule for the hand verbs: the object, the slot and the destination are all
            # inside the target.  An undecodable one is a definite pre-submission refusal --
            # nothing was minted, nothing reached the owner, and the same action id may be
            # retried once the target is whole.
            try:
                resolver_params(verb, target)
            except daily_actions.DailyActionTargetError as error:
                return self._record(idempotency_key, {
                    "execution_id": execution_id,
                    "status": "rejected",
                    "code": "DAILY_ACTION_TARGET_INVALID",
                    "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                    "detail": str(error) or "daily action target is incomplete",
                })

        intent = self.build_intent(execution_id=execution_id, verb=verb, target=target)
        self.last_intent = copy.deepcopy(intent)
        result = executor.execute_intent(intent, client=self.client)
        self.last_result = copy.deepcopy(result)
        return self._record(
            idempotency_key,
            self._map_result(ai, result, execution_id=execution_id, verb=verb, target=target),
        )

    # ---- mapping ---------------------------------------------------------
    def _map_result(
        self, ai: Any, result: Mapping[str, Any], *, execution_id: str, verb: str, target: str
    ) -> dict[str, Any]:
        intent_status = str(result.get("intent_status") or "")
        outcome = str(result.get("outcome") or "")

        if intent_status == ai.STATUS_APPLIED:
            return self._success(execution_id=execution_id, verb=verb, target=target,
                                 detail=outcome)

        if intent_status == ai.STATUS_REJECTED:
            # GATE_OFF / SHADOW_NO_COMMIT / INVALID never crossed the submission
            # boundary, so AR-0 may retry under the same action id.
            if outcome in {"GATE_OFF", "SHADOW_NO_COMMIT", "INVALID"}:
                return {
                    "execution_id": execution_id,
                    "status": "rejected",
                    "code": outcome,
                    "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                    "detail": str(result.get("detail") or ""),
                }
            return {
                "execution_id": execution_id,
                "status": "rejected",
                "code": outcome or "REJECTED",
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_REJECT,
                "detail": str(result.get("detail") or ""),
            }

        if intent_status == ai.STATUS_STALE:
            return {
                "execution_id": execution_id,
                "status": "rejected",
                "code": "STALE_STATE",
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_REJECT,
                "detail": str(result.get("detail") or ""),
            }

        # UNKNOWN: a purely local refusal (integration off, runtime not ready, no
        # provenance) never reached the Resolver, so it is a pre-submission
        # failure.  Anything else may have committed, so it is reconciled with
        # the SAME execution id before being called ambiguous.
        if outcome in {"UNAVAILABLE", "NOT_READY", "NO_PROVENANCE"}:
            return {
                "execution_id": execution_id,
                "status": "rejected",
                "code": outcome,
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                "detail": str(result.get("detail") or ""),
            }

        reconciled = self.reconcile_execution(execution_id)
        if reconciled["outcome"] == "SUCCEEDED":
            return self._success(execution_id=execution_id, verb=verb, target=target,
                                 detail="reconciled:%s" % outcome)
        if reconciled["outcome"] == "FAILED":
            return {
                "execution_id": execution_id,
                "status": "rejected",
                "code": "RECONCILED_FAILURE",
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_REJECT,
                "detail": str(reconciled.get("result")),
            }
        return {
            "execution_id": execution_id,
            "status": "uncertain",
            "code": "RECONCILIATION_REQUIRED",
            "transport_result": ar0.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
            "detail": str(result.get("detail") or outcome),
            "reconciliation": reconciled,
        }

    def _success(self, *, execution_id: str, verb: str, target: str, detail: str) -> dict[str, Any]:
        """Read the World back and describe the mutation with real evidence."""

        revision = None
        place = None
        snapshot: Optional[dict[str, Any]] = None
        try:
            _, _, wbc = self._bridge()
            probe = self.client.get_snapshot()
            if probe.outcome == wbc.OK and isinstance(probe.data, dict):
                snapshot = dict(probe.data)
                revision = snapshot.get("world_revision")
                place = (snapshot.get("location") or {}).get("place_id")
        except Exception:  # noqa: BLE001 - the commit already happened; the ref is extra
            revision = None
            place = None
        mutation_ref = (
            "world:mutation:%s:r%s" % (place, revision)
            if place is not None and revision is not None
            else "world:execution:%s" % execution_id
        )
        entry: dict[str, Any] = {
            "execution_id": execution_id,
            "status": "committed",
            "code": "OK",
            "world_revision": revision,
            "world_mutation_ref": mutation_ref,
            "transport_result": ar0.TRANSPORT_OK_SUCCEEDED,
            "detail": detail,
        }
        if verb == "CONSUME":
            # The one owner fact that names *which* instance was consumed and that the
            # deletion and its receipt were saved together.  It is read back under the
            # original execution id; a missing receipt is reported as missing rather
            # than inferred from "the object is gone" (the F contract).
            entry["details"] = self._consumption_details(execution_id)
        if verb in BODY_ACTION_VERBS:
            # What the World's own snapshot says *after* the commit -- the only body fact a
            # committed command can carry.  A rest command's success is the session
            # command's outcome, never a claim that the body recovered, so it is labelled
            # as exactly that instead of being reported as a recovery.
            details: dict[str, Any] = {
                "body_observed": {
                    "pose": (snapshot or {}).get("pose"),
                    "orientation": (snapshot or {}).get("orientation"),
                },
                "observation_source": "world_gateway.get_snapshot",
            }
            if verb == body_actions.REST_VERB:
                details["command_success_is_not_recovery"] = True
            entry["details"] = details
        if verb in DAILY_ACTION_VERBS:
            # W-v04B: what the World's own snapshot says *after* a pick-up or a placement.
            # It is a real, bounded read of the hands and the object's placement, and it is
            # the material a completion criterion is checked against -- but it is *post*
            # state: it can never prove that *this* execution produced it.  Attribution
            # stays with AR-0's receipt and the World's execution record, and the flag below
            # says so out loud so nothing downstream can quietly upgrade it.
            entry["details"] = self._daily_placement_details(target)
        return entry

    def _consumption_details(self, execution_id: str) -> dict[str, Any]:
        """The World owner's own consumption receipt, or an honest absence."""

        try:
            _, _, wbc = self._bridge()
            probe = self.client.get_action_result(execution_id)
        except Exception as error:  # noqa: BLE001 - unreachable World is not a receipt
            return {"receipt_present": False,
                    "receipt_query_error": "%s: %s" % (type(error).__name__, error)}
        if probe.outcome != wbc.OK or not isinstance(probe.data, dict):
            return {"receipt_present": False,
                    "receipt_query_error": "outcome=%s" % probe.outcome}
        data = probe.data
        return {
            "receipt_present": bool(data.get("receipt_present")),
            "consumption_receipt": data.get("consumption_receipt"),
            "world_verdict": data.get("verdict"),
        }

    # ---- the hand verbs' post-state read ---------------------------------
    def read_daily_placement(self, object_id: Any) -> dict[str, Any]:
        """One fresh, read-only owner read of where an object is right now.

        This is the query a completion check needs after a committed pick-up or
        placement: it returns the World's own ``hands`` mapping and the object's own
        placement entry from ``get_snapshot``.  It writes nothing, mints nothing and reads
        no database of its own; an unreadable World answers ``observed: False`` with a
        reason instead of a guessed placement.
        """

        name = str(object_id or "").strip()
        if not name:
            raise ValueError("read_daily_placement needs an object_id")
        snapshot, reason = self._fresh_snapshot()
        if snapshot is None:
            return {"observed": False, "object_id": name, "reason": reason,
                    "hands": None, "placement": None, "world_revision": None,
                    "location": None}
        return self._placement_view(snapshot, name)

    def _fresh_snapshot(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """One ``get_snapshot`` read, or ``(None, reason)`` -- never a raise."""

        try:
            _, _, wbc = self._bridge()
            probe = self.client.get_snapshot()
        except Exception as error:  # noqa: BLE001 - unreachable World is not an observation
            return None, "%s: %s" % (type(error).__name__, error)
        if probe.outcome != wbc.OK or not isinstance(probe.data, dict):
            return None, "outcome=%s" % probe.outcome
        return dict(probe.data), None

    @staticmethod
    def _placement_view(snapshot: Mapping[str, Any], object_id: str) -> dict[str, Any]:
        """The bounded placement view of one object, straight from one snapshot."""

        entry: Optional[dict[str, Any]] = None
        for item in snapshot.get("visible_objects") or []:
            if isinstance(item, Mapping) and str(item.get("object_id")) == object_id:
                entry = {
                    "kind": item.get("kind"),
                    "surface_id": item.get("surface_id"),
                    "container_id": item.get("container_id"),
                    "slot_id": item.get("slot_id"),
                    "in_hand": bool(item.get("in_hand")),
                }
                break
        return {
            "observed": True,
            "object_id": object_id,
            "reason": None,
            "hands": dict(snapshot.get("hands") or {}),
            "placement": entry,
            "world_revision": snapshot.get("world_revision"),
            "location": dict(snapshot.get("location") or {}),
        }

    def _daily_placement_details(self, target: str) -> dict[str, Any]:
        """The post-state block one committed hand action carries.

        ``placement_fact`` is the same ``{fact_key, expected_value, value, satisfied}``
        shape a completion criterion reads, computed from one read -- so a caller can see
        the criterion's verdict next to the execution that answered it, and can also see
        that the verdict is a *state* verdict (``post_state_only``) and not a proof of
        attribution.
        """

        details: dict[str, Any] = {
            "observation_source": daily_actions.SOURCE_SNAPSHOT,
            "post_state_only": True,
            "post_state_proves_this_execution": False,
        }
        parsed: Optional[dict[str, Any]] = None
        try:
            parsed = daily_actions.parse_daily_action_target(target)
        except daily_actions.DailyActionTargetError as error:
            details["target_decode_error"] = str(error)
        if parsed is None:
            details["daily_placement"] = {"observed": False,
                                          "reason": "TARGET_NOT_A_DAILY_ACTION"}
            return details
        object_id = str(parsed["object_id"])
        snapshot, reason = self._fresh_snapshot()
        if snapshot is None:
            details["daily_placement"] = {"observed": False, "object_id": object_id,
                                          "reason": reason, "hands": None,
                                          "placement": None}
        else:
            details["daily_placement"] = self._placement_view(snapshot, object_id)
        details["placement_fact"] = daily_actions.daily_action_observed_fact(parsed, snapshot)
        return details

    def _record(self, idempotency_key: str, entry: dict[str, Any]) -> dict[str, Any]:
        self.by_idem_key[idempotency_key] = copy.deepcopy(entry)
        return entry

    # ---- reconciliation --------------------------------------------------
    def reconcile_execution(self, execution_id: str) -> dict[str, Any]:
        """Ask the World what it knows about one execution.  Never invents."""

        try:
            _, _, wbc = self._bridge()
            probe = self.client.get_action_result(execution_id)
        except Exception as error:  # noqa: BLE001 - unreachable World is not a verdict
            return {
                "outcome": "UNKNOWN",
                "world_query_ref": execution_id,
                "evidence_refs": [execution_id],
                "detail": "world query failed: %s" % error,
            }
        if probe.outcome != wbc.OK or not isinstance(probe.data, dict):
            return {
                "outcome": "UNKNOWN",
                "world_query_ref": execution_id,
                "evidence_refs": [execution_id],
                "detail": "world query outcome=%s" % probe.outcome,
            }
        data = probe.data
        if not data.get("known"):
            return {
                "outcome": "NOT_FOUND",
                "world_query_ref": execution_id,
                "evidence_refs": [execution_id],
                "verdict": data.get("verdict"),
            }
        verdict = str(data.get("verdict") or "UNKNOWN")
        outcome = {"SUCCESS": "SUCCEEDED", "FAILURE": "FAILED"}.get(verdict, "UNKNOWN")
        return {
            "outcome": outcome,
            "verdict": verdict,
            "state": data.get("state"),
            "result": data.get("result"),
            "action_type": data.get("action_type"),
            "world_query_ref": execution_id,
            "evidence_refs": [execution_id],
        }


class RealWorldActionAdapter(ar0.WorldActionAdapter):
    """AR-0's World adapter, with reconciliation answered by the real World.

    ``submit``/capabilities are inherited unchanged: the AR-0 contract is already correct,
    only the resolver behind it changes.  ``validate_prepare`` is widened by exactly the two
    hand verbs (below) and reconciliation is overridden, because AR-0's sandbox version
    reads an in-memory dict that a restart would lose, while the real execution id is
    recomputable from the action's own frozen payload.
    """

    #: The two verbs AR-0's own frozen set does not yet name, and which this adapter can
    #: still carry faithfully: their whole parameter set is inside the single encoded
    #: ``dailyact:`` target, so the payload shape AR-0 validates is unchanged.
    EXTRA_VERBS = daily_actions.DAILY_ACTION_VERBS

    def validate_prepare(
        self, action: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """AR-0's prepare contract, plus the two hand verbs.

        Only admission changes: the three payload keys are the same three, the encoded
        target is still the only place the parameters live, and the World's own
        allowlist, mutation gate and Resolver still decide whether anything commits.
        """

        verb = payload.get("verb")
        if verb in self.EXTRA_VERBS:
            target = payload.get("target")
            if not isinstance(target, str) or not target.strip():
                raise ar0.ContractViolationError(
                    "WORLD_ACTION requires non-empty 'target'")
            parsed = daily_actions.parse_daily_action_target(target)
            if parsed is None or str(parsed["verb"]) != verb:
                raise ar0.ContractViolationError(
                    "WORLD_ACTION verb=%r needs a %s target, got %r"
                    % (verb, daily_actions.DAILY_ACTION_REF_PREFIX, target))
            return {
                "verb": verb,
                "target": target.strip(),
                "expected_dependencies": dict(payload.get("expected_dependencies", {})),
            }
        return super().validate_prepare(action, payload)

    def query_status_for_reconcile(
        self,
        *,
        action: Mapping[str, Any],
        attempt: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        resolver = self.resolver
        payload = dict(action.get("frozen_payload") or {})
        verb = str(payload.get("verb") or "")
        target = str(payload.get("target") or "")
        idempotency_key = str(action.get("idempotency_key") or "")
        if not verb or not target or not idempotency_key:
            return {
                "strategy": ar0.RECON_STRATEGY_WORLD_LEDGER_LOOKUP,
                "result": ar0.RECON_RESULT_NOT_SUBMITTED,
                "world_query_ref": "wquery:%s:no-payload" % idempotency_key,
                "evidence_refs": ["wquery:%s:no-payload" % idempotency_key],
            }
        execution_id = resolver.execution_id_for(idempotency_key, verb, target)
        view = resolver.reconcile_execution(execution_id)
        outcome = view["outcome"]
        if outcome == "SUCCEEDED":
            result = ar0.RECON_RESULT_CONFIRMED_SUCCEEDED
        elif outcome == "FAILED":
            result = ar0.RECON_RESULT_CONFIRMED_FAILED
        elif outcome == "NOT_FOUND":
            result = ar0.RECON_RESULT_NOT_SUBMITTED
        else:
            result = ar0.RECON_RESULT_STILL_UNKNOWN
        return {
            "strategy": ar0.RECON_STRATEGY_WORLD_LEDGER_LOOKUP,
            "result": result,
            "world_query_ref": execution_id,
            "evidence_refs": list(view.get("evidence_refs") or [execution_id]),
            "world_view": view,
        }


__all__ = [
    "BODY_ACTION_VERBS",
    "DAILY_ACTION_VERBS",
    "RealWorldActionAdapter",
    "SUPPORTED_VERBS",
    "VERB_PARAMETER",
    "WORLD_BRIDGE_DIR_ENV",
    "WorldBridgeResolver",
    "WorldBridgeUnavailable",
    "discover_bridge_dir",
    "resolver_params",
]
