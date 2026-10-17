#!/usr/bin/env python3
"""W05: the journey-backed World resolver for the Life AR-0 slot.

The gap this closes
-------------------
Before W05 the autonomous life loop moved her with the *instant* World path:
AR-0 -> ``WorldBridgeResolver`` -> Intent Executor -> Gateway -> Resolver ->
``world_living_skeleton.apply_move_to``.  That path has no notion of walking time,
so a round that "departed" was immediately recorded as *arrived*: the Life side
ingested a canonical position observation for the destination and closed the round
with ``OUTCOME_ACTED``.  W04's timed journey (``chiyo_bundle/world_journey.py``) was
never consulted, so "she set off" and "she is there" were the same event.

This module is the same AR-0 resolver duck type, but it commits a **timed
departure** through the W04 journey engine instead of teleporting:

* the durable intent is the journey's own ledger, under the same ``world_home``;
* the position authority is still the single ``world_state.json`` writer
  (``world_living_skeleton.apply_move_to``) -- this module never writes World state;
* the lock order stays ``journey -> world -> travel`` (W02/W04's global order);
* the returned evidence says ``arrived: false`` whenever the owner has not
  confirmed the destination, so the Life cycle cannot claim an arrival.

Authority chain (unchanged -- this module adds no new authority):

    Life (AR-0)                  builds the action; mints nothing
    this resolver                routes to the W04 journey engine
    world_journey.accept_journey durable intent, then ONE segment via the owner
    World Resolver / apply_move_to   the sole committer of World position
    world_travel_delivery        the in-transit record and its real arrival time

What it deliberately does NOT do
--------------------------------
* never writes ``world_state.json``, ``world_travel_delivery.json`` or
  ``world_journey.json`` itself;
* never invents a target, never re-plans a route, never retries a submission;
* never claims arrival: ``details["arrived"]`` is read back from the owner's own
  position, not from the requested target;
* never touches ``world_control.json`` (that is the World's pause, a different
  switch from the Life-side pause in ``life_pause``).
"""
from __future__ import annotations

import copy
import importlib
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

import action_reality_ledger as ar0

#: The world home this resolver reads and the journey writes.  ``CHIYO_WORLD_HOME`` is the
#: World service's own name; ``HERMES_HOME`` is the name the World *owner* functions read,
#: and ``world_body_config`` exports the former as the latter, so both name one directory.
WORLD_HOME_ENV = "CHIYO_WORLD_HOME"
HERMES_HOME_ENV = "HERMES_HOME"

#: The verbs this resolver *itself* commits: a timed journey.  POSE is refused before
#: submission rather than guessed at (same rule as the bridge adapter): one AR-0
#: ``target`` cannot express its second parameter.
SUPPORTED_VERBS = ("MOVE",)

#: Verbs that are not a journey at all but must still reach the same formal owner chain.
#: ``CONSUME`` is instant -- no travel time, no segment, no arrival -- so it is forwarded
#: verbatim to the World's own authorization entry (``WorldBridgeResolver``: Intent
#: Executor -> gateway.sock -> Resolver).  Without this the Life core could only ever
#: *move*, and the owner's consumption receipt would be unreachable from the loop.
DELEGATED_VERBS = ("CONSUME",)

DEPARTED_REF = "world:journey:%s:departed"
ARRIVED_REF = "world:journey:%s:arrived:r%s"

#: Journey projection statuses that mean "a departure really is durably committed".
COMMITTED_STATUSES = frozenset({"started", "pending", "in_transit", "completed"})
#: Statuses where the journey engine refused *before* any owner call, so a retry under the
#: same action id is safe.
DEFINITE_REFUSAL_STATUSES = frozenset(
    {"rejected", "disabled", "feature_disabled", "paused", "control_blocked", "none"}
)
#: Everything else (``blocked``, ``unknown``, ``unavailable``) may have crossed the owner
#: boundary, so it is reported uncertain and must be reconciled before any retry.
UNCERTAIN_STATUSES = frozenset({"unknown", "unavailable", "blocked"})

#: W05R: reason codes the **formal World entry** produces *before* anything is submitted.
#: They are definite refusals -- no one-shot authorization was consumed and no owner
#: mutation happened -- so AR-0 may retry under the same action id, and the caller must
#: not be told to reconcile a commit that never took place.  Treating these as "uncertain"
#: would turn a clean gate refusal into an ambiguous post-submission state.
DEFINITE_PRE_SUBMIT_REASONS = frozenset({
    "GATE_OFF",
    "SHADOW_NO_COMMIT",
    "INVALID",
    "UNSUPPORTED_VERB",
    "WORLD_HOME_NOT_CONFIGURED",
    "INTENT_BUILD_FAILED",
})


def _journey_module() -> Any:
    """Import ``chiyo_bundle.world_journey`` from this tree, by either import spelling."""

    errors = []
    for name in ("chiyo_bundle.world_journey", "world_journey"):
        try:
            return importlib.import_module(name)
        except ImportError as exc:  # pragma: no cover - spelling fallback
            errors.append("%s: %s" % (name, exc))
    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("chiyo_bundle.world_journey")
    except ImportError as exc:
        raise ImportError("world_journey is not importable: %s" % errors) from exc


class JourneyWorldResolver:
    """A real World Resolver in the shape AR-0's adapter already speaks (timed)."""

    def __init__(
        self,
        *,
        environment: Optional[Mapping[str, str]] = None,
        world_home: Optional[Path | str] = None,
        journey: Any = None,
        submit_port: Any = None,
        formal_submission: bool = True,
    ) -> None:
        self.environment: Mapping[str, str] = dict(
            environment if environment is not None else os.environ
        )
        self._world_home = str(world_home).strip() if world_home else None
        self._journey = journey
        #: W05R: the World's formal authorization entry.  Built lazily on first real
        #: submission so importing this module stays free of World-bridge side effects.
        self._explicit_submit_port = submit_port
        self._formal_submission = bool(formal_submission)
        self._submit_port: Any = None
        #: AR-0 reads this for its own reconcile fallback; same shape the sandbox uses.
        self.by_idem_key: dict[str, dict[str, Any]] = {}
        self.last_outcome: Optional[dict[str, Any]] = None
        self.last_result: Optional[dict[str, Any]] = None
        #: The formal entry used for the delegated (non-journey) verbs.  Built lazily so
        #: importing this module still has no World-bridge side effect.
        self._delegate: Any = None

    @property
    def submit_port(self) -> Any:
        """The formal owner submission port, or ``None`` for the W05 direct path.

        ``formal_submission=False`` is an explicit opt-out for callers that already
        hold an equivalent authorization route; the default is the formal one, because
        a timed journey must not be the one World mutation that skips the gate.
        """

        if self._explicit_submit_port is not None:
            return self._explicit_submit_port
        if not self._formal_submission:
            return None
        if self._submit_port is None:
            self._submit_port = FormalOwnerMoveSubmitPort(environment=self.environment)
        return self._submit_port

    @property
    def delegate(self) -> Any:
        """The World's formal entry for the verbs this resolver does not own itself.

        Same chain, same authority and same transport as the journey segments: only the
        *shape of the action* differs (an instant owner mutation instead of a timed
        segment).  It mints nothing and caches no action space of its own beyond the
        execution ids it is asked about.
        """

        if self._delegate is None:
            from world_action_bridge_adapter import WorldBridgeResolver

            self._delegate = WorldBridgeResolver(environment=self.environment)
        return self._delegate

    # -- identity ----------------------------------------------------------

    @property
    def journey(self) -> Any:
        if self._journey is None:
            self._journey = _journey_module()
        return self._journey

    @property
    def world_home(self) -> Optional[str]:
        """The explicit home, else the request environment's own names.  Never guessed."""

        if self._world_home:
            return self._world_home
        for name in (WORLD_HOME_ENV, HERMES_HOME_ENV):
            value = str(self.environment.get(name) or "").strip()
            if value:
                return value
        return None

    def describe(self) -> dict[str, Any]:
        """No secrets: names and shapes only."""

        # W05R: which authorization route the timed segment takes.  ``formal`` means the
        # Intent Executor mints the one-shot authorization and the gateway consumes it;
        # ``direct_owner`` is the W05 behaviour and is only reachable when a caller
        # explicitly opted out.  This reports the *configured* route without building the
        # bridge, so a read-only describe can never have a World side effect.
        configured = self._explicit_submit_port
        formal = configured is not None or self._formal_submission
        return {
            "kind": "journey_world_resolver",
            "world_home_configured": bool(self.world_home),
            "supported_verbs": list(SUPPORTED_VERBS),
            "delegated_verbs": list(DELEGATED_VERBS),
            "timed": True,
            "writes_world_state": False,
            "submission_route": ("formal" if formal else "direct_owner"),
            "submission_port_kind": ("formal_owner_move_submit_port" if formal else None),
            "mints_authority": False,
        }

    @staticmethod
    def execution_id_for(idempotency_key: str, verb: str, target: str) -> str:
        """Deterministic execution id, identical in shape to the bridge adapter's.

        An UNKNOWN outcome must be reconcilable under the *same* id, and a retry must never
        mint a new one, so it is derived from the AR-0 idempotency key.
        """

        return "world-exec:" + ar0.sha256_hex(
            "ar0:%s:%s:%s" % (idempotency_key, verb, target)
        )[:32]

    # -- the AR-0 resolver contract ---------------------------------------

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

        execution_id = self.execution_id_for(idempotency_key, verb, target)
        if verb in DELEGATED_VERBS:
            # Not a journey: handed to the World's own formal entry unchanged, under this
            # resolver's deterministic execution id so an UNKNOWN is still reconcilable
            # under the same identity.
            return self._record(idempotency_key,
                                dict(self.delegate.resolve_action(
                                    idempotency_key=idempotency_key, verb=verb, target=target,
                                    expected_dependencies=expected_dependencies,
                                    fault_hook=fault_hook)))
        if verb not in SUPPORTED_VERBS:
            return self._record(idempotency_key, {
                "execution_id": execution_id,
                "status": "rejected",
                "code": "UNSUPPORTED_VERB_%s" % verb,
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                "detail": "verb %s cannot be expressed by a single AR-0 target" % verb,
                "details": {"arrived": False, "reason_code": "UNSUPPORTED_VERB"},
            })

        home = self.world_home
        if not home:
            return self._record(idempotency_key, {
                "execution_id": execution_id,
                "status": "rejected",
                "code": "WORLD_HOME_NOT_CONFIGURED",
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                "detail": "no %s / %s in this request environment" % (WORLD_HOME_ENV,
                                                                     HERMES_HOME_ENV),
                "details": {"arrived": False, "reason_code": "WORLD_HOME_NOT_CONFIGURED"},
            })

        try:
            # The AR-0 idempotency key *is* the journey request key: it already encodes where she
            # was, the world revision the observation came from and where she is going.  Storing it
            # on the journey is what makes reconciliation exact instead of a guess -- a retry, or a
            # crash, can find the same request again by its own key.
            outcome = self.journey.accept_journey(
                target=target, hermes_home=home, environment=self.environment,
                request_key=idempotency_key,
                # W05R: the segment is submitted through the World's own authorization
                # entry, never by reaching the Resolver's commit function directly.
                submit_port=self.submit_port,
            )
        except Exception as exc:  # the journey engine could not even be reached
            return self._record(idempotency_key, {
                "execution_id": execution_id,
                "status": "uncertain",
                "code": "JOURNEY_UNAVAILABLE",
                "transport_result": ar0.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                "detail": "%s: %s" % (type(exc).__name__, exc),
                "details": {"arrived": False, "reason_code": "JOURNEY_UNAVAILABLE"},
            })
        self.last_outcome = copy.deepcopy(dict(outcome))
        mapped = self._map_journey(execution_id, target, dict(outcome))
        self.last_result = copy.deepcopy(mapped)
        return self._record(idempotency_key, mapped)

    # -- mapping -----------------------------------------------------------

    def _map_journey(self, execution_id: str, target: str,
                     outcome: Mapping[str, Any]) -> dict[str, Any]:
        status = str(outcome.get("status") or "")
        reason = str(outcome.get("reason_code") or "") or None
        location = outcome.get("location_id")
        journey_id = outcome.get("journey_id")
        arrived = bool(location is not None and str(location) == str(target))
        if status == "already_there":
            arrived = True
        details: dict[str, Any] = {
            "arrived": arrived,
            "journey_id": journey_id,
            "journey_status": status,
            "reason_code": reason,
            "origin_location_id": outcome.get("origin_location_id"),
            "destination_location_id": outcome.get("destination_location_id") or target,
            "location_id": location,
            "current_segment": outcome.get("current_segment"),
            "segment_status": outcome.get("segment_status"),
            "expected_arrival_at": outcome.get("expected_arrival_at"),
            "remaining_seconds": outcome.get("remaining_seconds"),
            "segments_confirmed": outcome.get("segments_confirmed"),
            "route": list(outcome.get("route") or []),
            "graph_matches": outcome.get("graph_matches"),
        }

        if status in COMMITTED_STATUSES or status == "already_there":
            mutation_ref = (
                ARRIVED_REF % (journey_id, outcome.get("revision"))
                if arrived else DEPARTED_REF % (journey_id,)
            )
            return {
                "execution_id": execution_id,
                "status": "committed",
                "code": "JOURNEY_ARRIVED" if arrived else "JOURNEY_DEPARTED",
                "world_revision": None,
                "world_mutation_ref": mutation_ref,
                "transport_result": ar0.TRANSPORT_OK_SUCCEEDED,
                "detail": "journey %s %s at %s" % (journey_id, status, location),
                "details": details,
            }

        if reason in DEFINITE_PRE_SUBMIT_REASONS:
            # The journey records the refusal as ``blocked`` because its own durable intent
            # was already written before the owner call.  That is not a submission: the
            # formal entry refused before minting anything, so this is definite.
            return {
                "execution_id": execution_id,
                "status": "rejected",
                "code": reason,
                "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                "detail": "the formal World entry refused before submission: %s" % reason,
                "details": details,
            }

        if status in UNCERTAIN_STATUSES:
            return {
                "execution_id": execution_id,
                "status": "uncertain",
                "code": reason or "JOURNEY_UNKNOWN",
                "transport_result": ar0.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                "detail": "journey state %r may have crossed the owner boundary" % status,
                "details": details,
            }

        definite = status in DEFINITE_REFUSAL_STATUSES
        return {
            "execution_id": execution_id,
            "status": "rejected",
            "code": reason or ("JOURNEY_REFUSED" if not status else status.upper()),
            "transport_result": (ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT if definite
                                 else ar0.TRANSPORT_ERR_DEFINITE_REJECT),
            "detail": "journey engine refused: %s %s" % (status, reason or ""),
            "details": details,
        }

    def _record(self, idempotency_key: str, entry: dict[str, Any]) -> dict[str, Any]:
        self.by_idem_key[idempotency_key] = copy.deepcopy(entry)
        return entry

    # -- reconciliation ----------------------------------------------------

    def reconcile_execution(self, execution_id: str) -> dict[str, Any]:
        """Ask the journey ledger what it knows about one execution.  Never invents."""

        home = self.world_home
        if not home:
            return {"outcome": "UNKNOWN", "world_query_ref": execution_id,
                    "evidence_refs": [execution_id],
                    "detail": "no world home in this request environment"}
        try:
            state = self.journey.load_state(home)
        except Exception as error:  # a corrupt ledger is not a verdict
            return {"outcome": "UNKNOWN", "world_query_ref": execution_id,
                    "evidence_refs": [execution_id],
                    "detail": "journey ledger unreadable: %s" % error}
        if state is None:
            return {"outcome": "NOT_FOUND", "world_query_ref": execution_id,
                    "evidence_refs": [execution_id],
                    "detail": "no journey ledger at all"}
        candidates = [state.get("active")] + list(state.get("history") or [])
        for journey in candidates:
            if not isinstance(journey, Mapping):
                continue
            for segment in journey.get("segments") or []:
                if not isinstance(segment, Mapping):
                    continue
                if str(segment.get("execution_id")) != str(execution_id):
                    continue
                status = str(segment.get("status") or "")
                if status in {"started", "confirmed"}:
                    return {
                        "outcome": "SUCCEEDED", "verdict": "SUCCESS", "state": status,
                        "result": {"journey_id": journey.get("journey_id"),
                                   "destination_location_id":
                                       segment.get("destination_location_id"),
                                   "arrived": bool(journey.get("status") == "completed"
                                                   and str(journey.get("target_location_id"))
                                                   == str(segment.get("destination_location_id")))},
                        "action_type": "MOVE",
                        "world_query_ref": execution_id,
                        "evidence_refs": [execution_id,
                                          "world:journey:%s:segment:%s"
                                          % (journey.get("journey_id"), segment.get("index"))],
                    }
                return {"outcome": "UNKNOWN", "world_query_ref": execution_id,
                        "evidence_refs": [execution_id],
                        "detail": "segment status %r is not a committed departure" % status}
        return {"outcome": "NOT_FOUND", "world_query_ref": execution_id,
                "evidence_refs": [execution_id], "detail": "no such segment execution id"}


    def reconcile_request(self, *, idempotency_key: str, target: str) -> dict[str, Any]:
        """Did *this request* already commit a departure?  Read the journey ledger only.

        The journey engine owns its own segment execution ids, so an AR-0 execution id cannot be
        used to look a segment up.  The request key can: ``accept_journey`` stores it, and this
        reads it back.  A request with no journey at all is NOT_FOUND, which is the only honest
        answer that lets AR-0 retry under the same action id.
        """

        home = self.world_home
        if not home:
            return {"outcome": "UNKNOWN", "world_query_ref": idempotency_key,
                    "evidence_refs": [idempotency_key],
                    "detail": "no world home in this request environment"}
        try:
            state = self.journey.load_state(home)
        except Exception as error:
            return {"outcome": "UNKNOWN", "world_query_ref": idempotency_key,
                    "evidence_refs": [idempotency_key],
                    "detail": "journey ledger unreadable: %s" % error}
        if state is None:
            return {"outcome": "NOT_FOUND", "world_query_ref": idempotency_key,
                    "evidence_refs": [idempotency_key], "detail": "no journey ledger at all"}
        for journey in [state.get("active")] + list(state.get("history") or []):
            if not isinstance(journey, Mapping):
                continue
            if str(journey.get("request_key") or "") != str(idempotency_key):
                continue
            status = str(journey.get("status") or "")
            arrived = (status == "completed"
                       and str(journey.get("target_location_id")) == str(target))
            if status in {"pending", "in_transit", "completed"}:
                return {
                    "outcome": "SUCCEEDED", "verdict": "SUCCESS", "state": status,
                    "result": {"journey_id": journey.get("journey_id"), "arrived": arrived,
                               "target_location_id": journey.get("target_location_id"),
                               "segments_confirmed": sum(
                                   1 for item in journey.get("segments") or []
                                   if str(item.get("status")) == "confirmed")},
                    "action_type": "MOVE",
                    "world_query_ref": idempotency_key,
                    "evidence_refs": [idempotency_key,
                                      "world:journey:%s" % journey.get("journey_id")],
                }
            if status in {"blocked", "unknown"}:
                return {"outcome": "FAILED", "verdict": "FAILURE", "state": status,
                        "result": journey.get("error"), "action_type": "MOVE",
                        "world_query_ref": idempotency_key,
                        "evidence_refs": [idempotency_key]}
            return {"outcome": "UNKNOWN", "world_query_ref": idempotency_key,
                    "evidence_refs": [idempotency_key],
                    "detail": "journey status %r is not a committed departure" % status}
        return {"outcome": "NOT_FOUND", "world_query_ref": idempotency_key,
                "evidence_refs": [idempotency_key],
                "detail": "no journey carries this request key"}


class FormalOwnerMoveSubmitPort:
    """W05R: submit one journey segment through the World's **own** authorization entry.

    The World's formal chain is:

        Intent Executor   the ONLY place a one-shot authorization is minted
        gateway.sock      consumes it; a caller-supplied authority is refused
        Resolver          the sole committer of World position

    ``world_action_bridge_adapter.WorldBridgeResolver`` already speaks exactly that
    chain, so this port reimplements none of it: it builds the intent for *this
    segment's own* execution id, submits it, and translates the answer back into the
    owner-status shape ``world_living_skeleton.apply_move_to`` returns -- which is what
    the journey ledger already reads back from the travel record.

    What changes compared with reaching the Resolver's commit function directly:

    * the mutation gate (``WORLD_BODY_INTENT_MUTATION_GATE``) is consulted **at submit
      time**, by the executor, before anything is sent anywhere;
    * the fresh snapshot read and the dependency evidence are produced by the authority
      side, and the one-shot authorization is bound to them;
    * the gateway consumes that authorization, so a stale or withdrawn one is refused
      with no side effect on position, travel or journey.

    It mints nothing, caches no action space, never writes World state and never claims
    arrival.  ``expected_dependencies`` stays AR-0-side bookkeeping, exactly as the
    bridge's frozen rule says: the authority side re-reads its own evidence.
    """

    def __init__(self, *, environment: Optional[Mapping[str, str]] = None,
                 bridge_dir: Optional[Path | str] = None, bridge: Any = None) -> None:
        self.environment: Mapping[str, str] = dict(
            environment if environment is not None else os.environ)
        self._bridge_dir = bridge_dir
        self._bridge = bridge
        self.last_intent: Optional[dict[str, Any]] = None
        self.last_result: Optional[dict[str, Any]] = None
        #: Real submissions made.  Never incremented for a refusal the executor made
        #: before contacting the gateway -- that distinction is what the W05R
        #: reconciliation-by-count evidence reads.
        self.submissions = 0

    def _resolver(self) -> Any:
        if self._bridge is None:
            from world_action_bridge_adapter import WorldBridgeResolver

            self._bridge = WorldBridgeResolver(environment=self.environment,
                                               bridge_dir=self._bridge_dir)
        return self._bridge

    def describe(self) -> dict[str, Any]:
        info = dict(self._resolver().describe())
        info.update({"kind": "formal_owner_move_submit_port",
                     "mints_authority": False,
                     "writes_world_state": False,
                     "uses_expected_dependencies": False,
                     "dependency_evidence_source": "authority_side_fresh_read"})
        return info

    def submit_move(self, *, destination: str, origin: str, execution_id: str,
                    hermes_home: Any, environment: Mapping[str, str],
                    now: Any = None) -> dict[str, Any]:
        """One segment, through the formal chain.  Returns an owner-status mapping."""

        bridge = self._resolver()
        ai, executor, _wbc = bridge._bridge()
        try:
            intent = bridge.build_intent(execution_id=str(execution_id), verb="MOVE",
                                         target=str(destination))
        except Exception as exc:  # the formal shape could not even be built
            return {"kind": "MOVE_TO", "status": "unavailable", "formal": True,
                    "reason_code": "INTENT_BUILD_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        self.last_intent = copy.deepcopy(intent)
        try:
            result = dict(executor.execute_intent(intent, client=bridge.client) or {})
        except Exception as exc:  # the executor could not be reached at all
            return {"kind": "MOVE_TO", "status": "uncertain", "formal": True,
                    "reason_code": "INTENT_SUBMIT_UNAVAILABLE",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        self.last_result = copy.deepcopy(result)
        mapped = self._as_owner_status(ai, result)
        if str(mapped.get("reason_code") or "") not in {"GATE_OFF", "SHADOW_NO_COMMIT",
                                                        "INVALID"}:
            self.submissions += 1
        return mapped

    @staticmethod
    def _as_owner_status(ai: Any, result: Mapping[str, Any]) -> dict[str, Any]:
        status = str(result.get("intent_status") or "")
        outcome = str(result.get("outcome") or "")
        detail = str(result.get("detail") or "")[:200]
        if status == ai.STATUS_APPLIED:
            return {"kind": "MOVE_TO", "status": "applied", "formal": True,
                    "reason_code": outcome or None, "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        if status == ai.STATUS_STALE:
            return {"kind": "MOVE_TO", "status": "rejected", "formal": True,
                    "reason_code": "STALE_STATE", "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        if status == ai.STATUS_REJECTED:
            return {"kind": "MOVE_TO", "status": "rejected", "formal": True,
                    "reason_code": outcome or "INTENT_REJECTED", "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        if status == ai.STATUS_PROPOSED:
            # shadow: everything up to Resolver-shape validation ran, nothing committed.
            return {"kind": "MOVE_TO", "status": "rejected", "formal": True,
                    "reason_code": "SHADOW_NO_COMMIT", "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        return {"kind": "MOVE_TO", "status": "uncertain", "formal": True,
                "reason_code": outcome or "INTENT_UNKNOWN", "detail": detail,
                "intent_status": status, "execution_id": result.get("execution_id")}

class RealJourneyWorldActionAdapter(ar0.WorldActionAdapter):
    """AR-0's World adapter with a timed resolver and journey-aware reconciliation.

    ``submit``/``validate_prepare``/capabilities are inherited unchanged: the AR-0 contract
    is already correct, only the resolver behind it changes.  The one override is
    reconciliation, because the execution id is recomputable from the action's own frozen
    payload while the ledger lookup has to read the journey store.
    """

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
        if verb in DELEGATED_VERBS:
            # The journey ledger knows nothing about a delegated verb; the owner's own
            # execution result is the only place that can answer.
            view = resolver.delegate.reconcile_execution(execution_id)
        else:
            view = resolver.reconcile_request(idempotency_key=idempotency_key, target=target)
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
    "ARRIVED_REF",
    "COMMITTED_STATUSES",
    "DELEGATED_VERBS",
    "DEFINITE_REFUSAL_STATUSES",
    "DEPARTED_REF",
    "FormalOwnerMoveSubmitPort",
    "HERMES_HOME_ENV",
    "JourneyWorldResolver",
    "RealJourneyWorldActionAdapter",
    "SUPPORTED_VERBS",
    "UNCERTAIN_STATUSES",
    "WORLD_HOME_ENV",
]