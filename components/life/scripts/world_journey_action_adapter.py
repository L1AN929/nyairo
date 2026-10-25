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
from typing import Any, Callable, Mapping, Optional

import action_reality_ledger as ar0
import life_body_action_targets as body_actions
#: v0.4 lived day: B owns which everyday hand verbs exist (``PICK_UP`` / ``PLACE``) and their
#: one-target encoding.  This module imports the *constant*, never a copy of the vocabulary, so a
#: verb B adds or renames cannot silently stop being delegated here.
import life_daily_action_targets as daily_actions

#: The world home this resolver reads and the journey writes.  ``CHIYO_WORLD_HOME`` is the
#: World service's own name; ``HERMES_HOME`` is the name the World *owner* functions read,
#: and ``world_body_config`` exports the former as the latter, so both name one directory.
WORLD_HOME_ENV = "CHIYO_WORLD_HOME"
HERMES_HOME_ENV = "HERMES_HOME"

#: The verbs this resolver *itself* commits: a timed journey.  Everything else is either
#: delegated to the World's formal entry or refused before submission -- nothing here
#: guesses a parameter.
SUPPORTED_VERBS = ("MOVE",)

#: Verbs that are not a journey at all but must still reach the same formal owner chain.
#: ``CONSUME`` is instant -- no travel time, no segment, no arrival -- so it is forwarded
#: verbatim to the World's own authorization entry (``WorldBridgeResolver``: Intent
#: Executor -> gateway.sock -> Resolver).  Without this the Life core could only ever
#: *move*, and the owner's consumption receipt would be unreachable from the loop.
#: W adds the two *body* verbs to the same delegation: a posture change and a rest
#: command are instant owner mutations, not journeys, and they reach the Resolver only
#: through that formal chain (the bridge decodes their encoded ``bodyact:`` target).
DELEGATED_VERBS = ("CONSUME", body_actions.POSE_VERB, body_actions.REST_VERB,
                   *daily_actions.DAILY_ACTION_VERBS)

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
        #: 业务时钟（无参 callable）。``None`` 是普通真实时间路径：旅程自己读系统墙钟。
        #: 显式压缩演示模式传入同一个业务域，让出发/到达时间与 Life 的结算同域；
        #: 安全面（一次性授权、gateway 消费）仍由 World 进程的真实墙钟判定。
        now: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.environment: Mapping[str, str] = dict(
            environment if environment is not None else os.environ
        )
        self._clock = now
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
        # caller injected a formal port; ``formal_auto`` means the engine will construct
        # the World bridge port at submit time (``_default_submit_port``).  There is no
        # direct-owner route any more: if the port cannot be constructed the segment
        # fails closed with SEGMENT_SUBMIT_PORT_UNAVAILABLE.  This reports the configured
        # route without building the bridge, so a read-only describe has no side effect.
        configured = self._explicit_submit_port
        formal = configured is not None or self._formal_submission
        return {
            "kind": "journey_world_resolver",
            "world_home_configured": bool(self.world_home),
            "supported_verbs": list(SUPPORTED_VERBS),
            "delegated_verbs": list(DELEGATED_VERBS),
            "timed": True,
            "writes_world_state": False,
            "submission_route": ("formal" if formal else "formal_auto"),
            "submission_port_kind": ("formal_owner_move_submit_port" if formal
                                     else "formal_owner_move_submit_port (auto)"),
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
            # 出发时刻是业务时间：与 ``advance_journey`` 的结算用同一个域。没有注入时钟时
            # 传 ``None``，journey 引擎保持原来的真实墙钟行为。
            outcome = self.journey.accept_journey(
                target=target, hermes_home=home, environment=self.environment,
                request_key=idempotency_key,
                now=(self._clock() if self._clock is not None else None),
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


def _formal_port_class() -> Any:
    """The World-side implementation of the formal segment submit port.

    The class lives next to the World bridge (``components/world/service/bridge``)
    because it is World authority, not Life policy: ``chiyo_bundle.world_journey``
    and the World runners must reach it without importing this Life module.  This
    name stays as a thin, lazily-resolved alias so existing Life callers and their
    injected ``bridge`` objects keep working unchanged.
    """

    errors = []
    candidates = []
    try:
        from world_action_bridge_adapter import discover_bridge_dir

        directory = discover_bridge_dir()
        if directory is not None:
            candidates.append(directory)
    except Exception as exc:  # pragma: no cover - adapter import failure
        errors.append(str(exc))
    for directory in candidates:
        text = str(directory)
        if text not in sys.path:
            sys.path.insert(0, text)
    try:
        module = importlib.import_module("journey_submit_port")
        return getattr(module, "FormalOwnerMoveSubmitPort")
    except (ImportError, AttributeError) as exc:
        errors.append(str(exc))
    raise ImportError("the World journey submit port is not reachable: %s"
                      % "; ".join(errors))


class FormalOwnerMoveSubmitPort:
    """W05R: submit one journey segment through the World's **own** authorization entry.

    The implementation is ``journey_submit_port.FormalOwnerMoveSubmitPort`` in the
    World bridge directory; this class resolves it lazily so importing this module
    still has no World-bridge side effect and no duplicated authority logic lives
    here.  Each journey segment has its own execution id, so each one mints and
    consumes its own one-shot authorization -- the first segment's ticket is never
    reused for the next.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _formal_port_class()(*args, **kwargs)

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