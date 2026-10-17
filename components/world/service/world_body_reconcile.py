#!/usr/bin/env python3
"""Startup reconciliation for the standalone World/Body service (M13A).

The service may be killed at any instant.  Three windows matter, and each is
treated differently on purpose:

===========  ==========================================  =====================
window       what the ledger shows                       what startup does
===========  ==========================================  =====================
Crash A      World committed, ledger terminal SUCCESS,   apply the missing
             body consequence absent                     consequence, once
Crash B      body applied, reference not finalised       nothing (the dedupe
                                                         says already applied)
Crash C      UNKNOWN                                     report only; never
                                                         auto-retry, never
                                                         auto-grade
===========  ==========================================  =====================

Two boundaries keep this honest:

* Only **body-era** executions are considered -- records with representation
  ``v2``.  The legacy journal rows are evidence of a period before the body
  existed; healing them would invent body history that never happened, so they
  are never candidates.
* Reconciliation only ever *fills a gap*.  A restart with nothing to heal writes
  nothing at all: no record per restart, no re-application, no grading of an
  unknown.

A third boundary applies to ``CONSUME`` only: attribution comes from the World
owner's consumption receipt, never from the consumed object being gone.  The
receipt must match the execution, the payload (object id, as recorded in the
ledger's own committed refs) and the subject (body).  A ``CONSUME`` success
without such a receipt is reported as unresolved and applies nothing -- the
object is absent *by design*, so absence can never be the proof.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

SCHEMA_RECONCILE = "world.body.service.reconciliation.v1"
KIND_RECONCILE = "world_body_startup_reconciliation"

IN_FLIGHT_STATES = ("prepared", "uncertain")

#: Ledger terminal result -> the status string the consequence mapping expects.
RESULT_TO_STATUS = {
    "SUCCESS": "applied",
    "FAILURE": "rejected",
    "UNKNOWN": "uncertain",
}


class ReconciliationError(RuntimeError):
    """Reconciliation could not be completed; the service must not go ready."""


def body_era_execution_ids(ledger) -> list[str]:
    """Execution ids that have at least one v2 record, in first-seen order."""

    ids: list[str] = []
    seen: set[str] = set()
    for record in ledger.read_all():
        if record.get("representation") != "v2":
            continue
        execution_id = record.get("execution_id")
        if execution_id and execution_id not in seen:
            seen.add(execution_id)
            ids.append(execution_id)
    return ids


def body_era_frames(ledger) -> list[dict[str, Any]]:
    return [ledger.frame(execution_id) for execution_id in body_era_execution_ids(ledger)]


def consume_evidence(
    *,
    execution_id: str,
    frame: Mapping[str, Any],
    substrate: Optional[Mapping[str, Any]],
    body_id: str,
) -> dict[str, Any]:
    """Does the World owner hold a receipt that attributes this consumption?

    Four things must line up before a CONSUME execution may reach the body:

    1. the execution id,
    2. the payload (the ledger's own committed mutation refs must name the same
       object id the receipt names),
    3. the subject (``body_id``),
    4. the physical outcome (the placement really is gone).

    A missing object satisfies none of them on its own, so it is never used as
    success evidence.  Every failure is reported, never repaired.
    """

    import world_body_substrate as substrate_module
    import world_consumable_contract as contract

    if substrate is None:
        return {"ok": False, "reasons": ["substrate_unavailable"], "object_id": None}
    try:
        receipt = substrate_module.consumption_receipt(substrate, execution_id)
    except Exception:
        return {"ok": False, "reasons": ["substrate_unreadable"], "object_id": None}
    if receipt is None:
        return {"ok": False, "reasons": ["receipt_missing"], "object_id": None}

    match = contract.verify_receipt(receipt, execution_id=execution_id, body_id=body_id)
    if not match["ok"]:
        return {"ok": False, "reasons": match["reasons"], "object_id": None}

    object_id = receipt["object_id"]
    refs: list[str] = []
    for record in frame.get("records") or []:
        raw = record.get("raw") or {}
        refs.extend(str(ref) for ref in (raw.get("world_mutation_refs") or []))
    if ("placement.%s=consumed" % object_id) not in refs:
        return {"ok": False, "reasons": ["payload_mismatch"], "object_id": None}

    try:
        still_placed = substrate_module.placement_of(substrate, object_id) is not None
    except Exception:
        return {"ok": False, "reasons": ["substrate_unreadable"], "object_id": None}
    if still_placed:
        return {"ok": False, "reasons": ["placement_still_live"], "object_id": None}

    return {"ok": True, "reasons": [], "object_id": object_id}


def reconcile_startup(
    *,
    ledger,
    resolver,
    consequence_ledger,
    body_store,
    body_id: str,
    runtime_state: Mapping[str, Any],
    at: Any,
    world_facts: Optional[Mapping[str, Any]] = None,
    pose: Optional[str] = None,
    substrate: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Heal only the gaps.  Returns a report plus the caller's new body state.

    ``runtime_state`` is never mutated; the returned state must be persisted by
    the caller.
    """

    import body_consequence as bcon
    import body_runtime as br

    chain = ledger.verify()  # raises LedgerTamperError when the chain is broken

    state = br.validate_runtime(runtime_state)
    records = consequence_ledger.read_all()
    consequence_state = consequence_ledger.current_state(records)

    healed: list[dict[str, Any]] = []
    already: list[str] = []
    unresolved: list[dict[str, Any]] = []
    failures: list[str] = []
    candidates: list[dict[str, Any]] = []

    for frame in body_era_frames(ledger):
        execution_id = frame["execution_id"]
        action_type = frame.get("action_type") or ""
        result = frame.get("result")
        frame_state = frame.get("state")
        consequence_id = bcon.consequence_id(execution_id, action_type, body_id)
        existing = consequence_state.get(consequence_id)
        already_applied = existing is not None and existing.get("state") == "applied"

        candidates.append(
            {
                "execution_id": execution_id,
                "action": action_type,
                "frame_state": frame_state,
                "ledger_result": result,
                "consequence_recorded": already_applied,
            }
        )

        if frame_state in IN_FLIGHT_STATES or result == "UNKNOWN":
            # Crash C, or an outcome that is still genuinely unknown.  The
            # resolver's own view is reported so the path is provably available.
            view = resolver.reconcile(execution_id)
            unresolved.append(
                {
                    "execution_id": execution_id,
                    "resolver_verdict": view.get("verdict"),
                    "frame_state": frame_state,
                    "reconciliation_required": True,
                    "action": "none (never auto-retry, never auto-grade)",
                }
            )
            continue

        if result == "FAILURE":
            # Failure applies nothing, so there is no gap to fill.
            failures.append(execution_id)
            continue

        consume_object_id: Optional[str] = None
        if action_type == "CONSUME":
            # A consumption is only ever attributed by its owner receipt.  The
            # consumed object is *absent* by design, so absence is never used as
            # evidence: without a receipt matching this execution, this payload
            # (object) and this body, the body consequence stays unapplied.
            evidence = consume_evidence(
                execution_id=execution_id,
                frame=frame,
                substrate=substrate,
                body_id=body_id,
            )
            if not evidence["ok"]:
                unresolved.append(
                    {
                        "execution_id": execution_id,
                        "action": action_type,
                        "frame_state": frame_state,
                        "ledger_result": result,
                        "reasons": evidence["reasons"],
                        "reconciliation_required": True,
                        "action_taken": "none (no matching consumption receipt)",
                    }
                )
                continue
            consume_object_id = evidence["object_id"]

        if already_applied:
            # Crash B, or an ordinary clean restart.  Nothing to do.
            already.append(execution_id)
            continue

        # Crash A: the world and the ledger say success, the body never heard.
        outcome = bcon.apply_for_execution(
            {
                "kind": "world_action_result",
                "execution_id": execution_id,
                "action": action_type,
                "status": RESULT_TO_STATUS.get(result or "", "uncertain"),
                "reason_code": frame.get("reason_code"),
            },
            body_id=body_id,
            ledger=consequence_ledger,
            runtime_state=state,
            at=at,
            world_facts=world_facts,
            pose=pose,
            object_id=consume_object_id,
        )
        if outcome["applied"]:
            state = outcome["state"]
            body_store.save_runtime(state)
            healed.append(
                {
                    "execution_id": execution_id,
                    "consequence_id": consequence_id,
                    "transition": (outcome["transition"] or {}).get("transition_id"),
                }
            )
        else:
            already.append(execution_id)

    return {
        "kind": KIND_RECONCILE,
        "schema_version": SCHEMA_RECONCILE,
        "chain_valid": bool(chain.get("valid")),
        "chain_entries": (chain.get("legacy_entries") or 0) + (chain.get("v2_entries") or 0),
        "candidates": candidates,
        "candidate_count": len(candidates),
        "in_flight": len(unresolved),
        "healed": healed,
        "already_applied": already,
        "failures_needing_nothing": failures,
        "unresolved": unresolved,
        "wrote_anything": bool(healed),
        "state": state,
        "complete": True,
    }


def reconciliation_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """Small, log-safe view: no raw body values, no payloads."""

    return {
        "schema_version": report.get("schema_version"),
        "chain_valid": report.get("chain_valid"),
        "candidates": report.get("candidate_count"),
        "healed": len(report.get("healed") or []),
        "already_applied": len(report.get("already_applied") or []),
        "unresolved": len(report.get("unresolved") or []),
        "wrote_anything": report.get("wrote_anything"),
    }


def pending_execution_ids(ledger) -> Sequence[str]:
    return [
        frame["execution_id"]
        for frame in body_era_frames(ledger)
        if frame.get("state") in IN_FLIGHT_STATES or frame.get("result") == "UNKNOWN"
    ]
