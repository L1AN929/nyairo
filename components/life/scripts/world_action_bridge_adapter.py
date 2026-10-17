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

#: Where the World bridge modules live, when the tree layout is not discoverable.
WORLD_BRIDGE_DIR_ENV = "NYAIRO_WORLD_BRIDGE_DIR"

#: The verbs this adapter maps faithfully, i.e. the ones a single AR-0 ``target``
#: string can express completely.  ``MOVE``'s target is the destination place and
#: ``CONSUME``'s target is the World object instance id -- the Resolver's own
#: single-parameter shapes (``destination_location_id`` / ``object_id``).  POSE is
#: still refused as a pre-submission failure rather than guessed at: the Resolver
#: requires ``pose`` *and* ``orientation``, and one target cannot supply both
#: without inventing one of them.
SUPPORTED_VERBS = ("MOVE", "CONSUME")

#: Verb -> the Resolver's own parameter name (world_action_resolver._require_params).
#: Both entries are total: nothing here invents a second parameter, and the AR-0
#: single target *is* the parameter value.
VERB_PARAMETER = {"MOVE": "destination_location_id", "CONSUME": "object_id"}


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
            "params": {VERB_PARAMETER[verb]: target},
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
        try:
            _, _, wbc = self._bridge()
            probe = self.client.get_snapshot()
            if probe.outcome == wbc.OK and isinstance(probe.data, dict):
                revision = probe.data.get("world_revision")
                place = (probe.data.get("location") or {}).get("place_id")
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

    ``submit``/``validate_prepare``/capabilities are inherited unchanged: the
    AR-0 contract is already correct, only the resolver behind it changes.  The
    one override is reconciliation, because AR-0's sandbox version reads an
    in-memory dict that a restart would lose, while the real execution id is
    recomputable from the action's own frozen payload.
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
    "RealWorldActionAdapter",
    "SUPPORTED_VERBS",
    "VERB_PARAMETER",
    "WORLD_BRIDGE_DIR_ENV",
    "WorldBridgeResolver",
    "WorldBridgeUnavailable",
    "discover_bridge_dir",
]
