"""S10: an honest, read-only projection of AR-0 Action / Attempt / Receipt / reconcile facts.

Why this module exists
----------------------
AR-0 already stores every fact S10 needs, but it stores them as separate records with their
own vocabularies: a logical ``ActionRecord``, one or more ``ActionAttempt`` rows, owner
``ActionReceipt`` claims, observed ``ResultEvidence`` and deterministic
``ReconciliationRecord`` rows.  A user asking "did it happen?" cannot read one field and be
told the truth.  This module projects those rows into one view **without inventing anything**:

* Request is not an attempt.  ``PREPARED`` means the canonical request was frozen; it does
  not mean the request was dispatched, and it never means it was sent.
* An attempt's ``started_at`` proves the *local* attempt began.  It does not prove a provider
  received a request.
* A provider ``ACCEPTED`` receipt is not delivery and is not the user having received
  anything.  A local inbox row is not a user receipt either, so ``user_received`` is always
  ``NOT_RECORDED`` here unless an owner actually recorded a ``READ`` claim.
* A lost answer is ``UNKNOWN``.  It is never projected as ``FAILED``, and a timeout does not
  become a failure.
* A missing field is reported as ``NOT_RECORDED``; it is never filled with a plausible guess.

The projection is read-only by construction: it takes an ``ActionReadService`` (or builds one
over a state root) and only calls its ``list_*``/``get_*`` methods.  It never proposes,
prepares, submits, reconciles, records evidence or opens a WAL.

Deliberate gaps (named, not implemented here)
---------------------------------------------
AR-0 has no separate ``DispatchAdmitted`` record, no child/DAG/PARTIAL outcome vector, and no
bounded reconciliation scheduler (``delaysMs`` / ``maxQueries`` / ``QUIESCED_UNRESOLVED``).
Those are protocol decisions; this module reports them as ``NOT_RECORDED`` /
``NOT_IMPLEMENTED`` instead of building a parallel store or a private retry timer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

#: Same value as ``life_runtime_production.NAMESPACE_AGENCY`` (which re-exports
#: ``candidate_sources_ag0.NAMESPACE_PRODUCTION``).  ``tests/test_s10_action_outcomes.py``
#: pins the equality so this copy cannot drift silently.
NAMESPACE_AGENCY = "production"

# ---------------------------------------------------------------------------------------------
# projection vocabulary
# ---------------------------------------------------------------------------------------------

NOT_RECORDED = "NOT_RECORDED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

PHASE_REQUESTED_UNFROZEN = "REQUESTED_UNFROZEN"
PHASE_REQUESTED_FROZEN = "REQUESTED_FROZEN"
PHASE_SCHEDULED = "SCHEDULED"
PHASE_WAITING_RESULT = "WAITING_RESULT"
PHASE_RECONCILING = "RECONCILING"
PHASE_UNRESOLVED = "UNRESOLVED"
PHASE_CLOSED = "CLOSED"

OUTCOME_PENDING = "PENDING"
OUTCOME_SUCCEEDED = "SUCCEEDED"
OUTCOME_FAILED = "FAILED"
OUTCOME_UNKNOWN = "UNKNOWN"
OUTCOME_NOT_EXECUTED = "NOT_EXECUTED"
OUTCOME_CANCELLED_AFTER_SUBMIT = "CANCELLED_AFTER_SUBMIT"

ATTEMPT_STARTED_LOCAL = "LOCAL_ATTEMPT_RECORDED"
ATTEMPT_NOT_STARTED = "NO_ATTEMPT_RECORDED"

ACCEPTANCE_RECEIPT = "RECEIPT_ACCEPTED"
DELIVERY_RECEIPT = "RECEIPT_DELIVERED"
READ_RECEIPT = "RECEIPT_READ"

_PENDING_STATUSES = frozenset({"PROPOSED", "PREPARED", "SCHEDULED", "SUBMITTED", "ACKNOWLEDGED"})

#: Explicit boundary statements printed with every envelope.  They are part of the surface:
#: a caller that reads only the "facts" block must not promote any of these.
BOUNDARIES: dict[str, str] = {
    "request_vs_attempt": (
        "PREPARED/REQUESTED_FROZEN records a frozen request only; it is not dispatch, "
        "not send, and not a provider fact."
    ),
    "attempt_vs_provider": (
        "An attempt's started_at proves the local attempt began. It does not prove the "
        "provider received a request; only an owner receipt/reconciliation does."
    ),
    "accepted_vs_delivered": (
        "A provider ACCEPTED receipt is not DELIVERED and is not READ. Delivery state is "
        "only what the owner recorded, never inferred."
    ),
    "local_inbox_vs_user": (
        "A local inbox/draft row is not the user having received anything; "
        "user_received stays NOT_RECORDED without an owner READ claim."
    ),
    "timeout_vs_failure": (
        "UNKNOWN is preserved. A timeout/lost answer is never rewritten as FAILED and is "
        "never resent without the original operation being resolved first."
    ),
    "hash_vs_causation": (
        "Identical content/hash is only compatibility evidence. It never proves this "
        "action created the change; only the owner's operation mapping does."
    ),
    "partial": (
        "AR-0 has no child Action DAG or PARTIAL outcome vector. AG-2's "
        "INTEGRATION_STATUS_PARTIAL is an integration-transaction status, not a projected "
        "outcome vector, so it is NOT_IMPLEMENTED at this surface."
    ),
    "bounded_query": (
        "AR-0 exposes one deterministic reconcile call per invocation. There is no "
        "delaysMs/maxQueries scheduler and no QUIESCED_UNRESOLVED outcome; those remain "
        "NOT_IMPLEMENTED and are not replaced with a private timer or retry counter."
    ),
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _str_or_none(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


# ---------------------------------------------------------------------------------------------
# per-row projections (safe subset; never payload/content/正文/凭据)
# ---------------------------------------------------------------------------------------------


def _attempt_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": row.get("attempt_id"),
        "attempt_no": row.get("attempt_no"),
        "started_at": row.get("started_at"),
        "submitted_at": row.get("submitted_at"),
        "status": row.get("status"),
        "transport_result": row.get("transport_result"),
        "error_class": row.get("error_class"),
        "error_ref": row.get("error_ref"),
        "provider_ref": row.get("provider_ref"),
        "receipt_count": len(row.get("receipt_refs") or ()),
    }


def _receipt_view(row: Mapping[str, Any]) -> dict[str, Any]:
    # ``details``/``raw_ref`` deliberately omitted: they can carry provider-specific blobs.
    return {
        "receipt_id": row.get("receipt_id"),
        "receipt_kind": row.get("receipt_kind"),
        "provider": row.get("provider"),
        "provider_operation_ref": row.get("provider_operation_ref"),
        "status_claim": row.get("status_claim"),
        "evidence_kind": row.get("evidence_kind"),
        "authority_domain": row.get("authority_domain"),
        "occurred_at": row.get("occurred_at"),
        "observed_at": row.get("observed_at"),
        "recorded_at": row.get("recorded_at"),
    }


def _evidence_view(row: Mapping[str, Any]) -> dict[str, Any]:
    # Hashes and details are omitted: this is a status surface, not a byte auditor.  The
    # observable facts are the observer, the artifact and whether it verified.
    return {
        "evidence_id": row.get("evidence_id"),
        "observer_kind": row.get("observer_kind"),
        "artifact_ref": row.get("artifact_ref"),
        "verified": bool(row.get("verified")),
        "occurred_at": row.get("occurred_at"),
        "observed_at": row.get("observed_at"),
        "recorded_at": row.get("recorded_at"),
        "source_ref_count": len(row.get("source_refs") or ()),
    }


def _reconciliation_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reconciliation_id": row.get("reconciliation_id"),
        "attempt_id": row.get("attempt_id"),
        "strategy": row.get("strategy"),
        "result": row.get("result"),
        "previous_status": row.get("previous_status"),
        "resolved_status": row.get("resolved_status"),
        "occurred_at": row.get("occurred_at"),
        "recorded_at": row.get("recorded_at"),
        "evidence_ref_count": len(row.get("evidence_refs") or ()),
        "provider_query_ref": row.get("provider_query_ref"),
        "world_query_ref": row.get("world_query_ref"),
        "artifact_query_ref": row.get("artifact_query_ref"),
    }


def _phase(act: Mapping[str, Any], reconciliation_count: int) -> str:
    status = str(act.get("status") or "")
    if status == "PROPOSED":
        return PHASE_REQUESTED_UNFROZEN
    if status == "PREPARED":
        return PHASE_REQUESTED_FROZEN
    if status == "SCHEDULED":
        return PHASE_SCHEDULED
    if status in {"SUBMITTED", "ACKNOWLEDGED"}:
        return PHASE_WAITING_RESULT
    if status == "UNKNOWN":
        return PHASE_RECONCILING if reconciliation_count else PHASE_UNRESOLVED
    return PHASE_CLOSED


def _outcome(act: Mapping[str, Any], attempts: list[Mapping[str, Any]]) -> str:
    status = str(act.get("status") or "")
    if status in _PENDING_STATUSES:
        return OUTCOME_PENDING
    if status == "SUCCEEDED":
        return OUTCOME_SUCCEEDED
    if status == "FAILED":
        return OUTCOME_FAILED
    if status == "UNKNOWN":
        return OUTCOME_UNKNOWN
    if status == "CANCELLED":
        submitted = bool(act.get("submitted_at")) or any(
            row.get("submitted_at") for row in attempts)
        return OUTCOME_CANCELLED_AFTER_SUBMIT if submitted else OUTCOME_NOT_EXECUTED
    return NOT_RECORDED


def _not_sent(attempts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Only a definite pre-submission transport result can support "not sent".

    A plain PREPARED action with zero attempts does **not** prove it was never sent: the old
    worker must be fenced and the attempt record must exist before that claim is allowed
    (design Q11).  So the default is ``NOT_RECORDED``.
    """
    for row in attempts:
        if str(row.get("transport_result") or "") == "DEFINITE_PRE_SUBMISSION_FAILURE":
            return {
                "value": "DEFINITE_PRE_SUBMIT_FAILURE",
                "not_sent": True,
                "attempt_id": row.get("attempt_id"),
                "error_class": row.get("error_class"),
            }
    return {"value": NOT_RECORDED, "not_sent": None, "attempt_id": None, "error_class": None}


def _provider_facts(receipts: list[Mapping[str, Any]]) -> dict[str, Any]:
    claims = {str(row.get("status_claim") or "") for row in receipts}
    return {
        "provider_accepted": ACCEPTANCE_RECEIPT if claims & {"ACCEPTED"} else NOT_RECORDED,
        "provider_delivered": DELIVERY_RECEIPT if "DELIVERED" in claims else NOT_RECORDED,
        "provider_read": READ_RECEIPT if "READ" in claims else NOT_RECORDED,
        # Never inferable from a local draft/inbox row.
        "user_received": READ_RECEIPT if "READ" in claims else NOT_RECORDED,
    }


def project_action(
    act: Mapping[str, Any],
    *,
    attempts: Iterable[Mapping[str, Any]] = (),
    receipts: Iterable[Mapping[str, Any]] = (),
    evidences: Iterable[Mapping[str, Any]] = (),
    reconciliations: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """One logical Action, with request/attempt/receipt/evidence/reconcile kept separate."""

    attempt_rows = list(attempts)
    receipt_rows = list(receipts)
    evidence_rows = list(evidences)
    reconciliation_rows = list(reconciliations)
    not_sent = _not_sent(attempt_rows)
    facts = _provider_facts(receipt_rows)
    payload_frozen = isinstance(act.get("frozen_payload"), Mapping) and bool(
        act.get("payload_hash"))
    facts.update({
        "request_recorded": bool(act.get("created_at")),
        "payload_frozen": payload_frozen,
        # AR-0 has no distinct DispatchAdmitted row; a frozen request is not a dispatch fact.
        "dispatch_admitted": NOT_RECORDED,
        "attempt_started": ATTEMPT_STARTED_LOCAL if attempt_rows else ATTEMPT_NOT_STARTED,
        "outcome_observed": _outcome(act, attempt_rows),
        "reconciliation_recorded": bool(reconciliation_rows),
    })
    return {
        "action_id": act.get("action_id"),
        "idempotency_key": act.get("idempotency_key"),
        "action_kind": act.get("action_kind"),
        "adapter_kind": act.get("adapter_kind"),
        "revision": act.get("revision"),
        "status": act.get("status"),
        "phase": _phase(act, len(reconciliation_rows)),
        "outcome": _outcome(act, attempt_rows),
        "target_ref": act.get("target_ref"),
        "decision_ref": act.get("decision_ref"),
        "activity_ref": act.get("activity_ref"),
        "payload": {
            "frozen": payload_frozen,
            "hash_recorded": bool(act.get("payload_hash")),
            "body_included": False,
        },
        "timestamps": {
            name: act.get(name)
            for name in ("created_at", "prepared_at", "scheduled_at", "submitted_at",
                         "acknowledged_at", "succeeded_at", "failed_at", "cancelled_at",
                         "unknown_since", "updated_at")
        },
        "attempt_count_recorded": int(act.get("attempt_count") or 0),
        "attempts": [_attempt_view(row) for row in attempt_rows],
        "receipts": [_receipt_view(row) for row in receipt_rows],
        "result_evidence": [_evidence_view(row) for row in evidence_rows],
        "reconciliation": [_reconciliation_view(row) for row in reconciliation_rows],
        "not_sent": not_sent,
        "delivery_state": act.get("delivery_state", NOT_RECORDED),
        "provider_operation_ref_recorded": bool(act.get("provider_operation_ref")),
        "facts": facts,
    }


# ---------------------------------------------------------------------------------------------
# state-root envelope
# ---------------------------------------------------------------------------------------------


def _read_service(state_root: Path | str):
    import action_reality_ledger as ar0

    store = ar0.ActionStore(Path(state_root), namespace=NAMESPACE_AGENCY)
    return ar0.ActionStore, store, ar0.ActionReadService(store)


def named_gaps() -> dict[str, str]:
    return {
        "child_dag_partial": NOT_IMPLEMENTED,
        "bounded_query_scheduler": NOT_IMPLEMENTED,
        "quiesced_unresolved": NOT_IMPLEMENTED,
        "dispatch_admitted": NOT_RECORDED,
        "dispatch_attempts_as_child_events": NOT_IMPLEMENTED,
    }


def build_action_review(
    state_root: Path | str,
    *,
    action_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Read one action or the recent list.  Never writes, never reconciles, never recovers WAL."""

    root = Path(state_root)
    store_cls, _store, reader = _read_service(root)
    # A pending WAL means a previous writer crashed mid-commit.  Replaying it is a *write*;
    # a pure read surface must refuse and say so instead of silently recovering.
    wal_path = root / store_cls.WAL_FILENAME
    if wal_path.exists():
        return {
            "ok": False,
            "state_root": str(root),
            "reason": "WAL_PENDING_RECOVERY",
            "detail": "a commit WAL exists; open the instance with its own runtime before reading",
            "read_at": _iso_now(),
        }
    try:
        if action_id:
            act = reader.get_action(str(action_id))
            if act is None:
                return {
                    "ok": False,
                    "state_root": str(root),
                    "reason": "ACTION_NOT_FOUND",
                    "action_id": str(action_id),
                    "read_at": _iso_now(),
                }
            actions = [act]
        else:
            actions = list(reader.list_actions())
            actions.sort(key=lambda row: (str(row.get("created_at") or ""),
                                          str(row.get("action_id") or "")), reverse=True)
            if limit is not None:
                actions = actions[: max(0, int(limit))]
        projected = []
        for act in actions:
            aid = str(act.get("action_id") or "")
            projected.append(project_action(
                act,
                attempts=reader.list_attempts(aid),
                receipts=reader.list_receipts(aid),
                evidences=reader.list_result_evidences(aid),
                reconciliations=reader.list_reconciliations(aid),
            ))
    except Exception as exc:  # named refusal, never a silent empty list
        return {
            "ok": False,
            "state_root": str(root),
            "reason": "ACTION_STATE_UNREADABLE",
            "detail": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
            "read_at": _iso_now(),
        }
    return {
        "ok": True,
        "state_root": str(root),
        "read_at": _iso_now(),
        "count": len(projected),
        "actions": projected,
        "boundaries": dict(BOUNDARIES),
        "named_gaps": named_gaps(),
    }


def render_text(envelope: Mapping[str, Any]) -> str:
    """A compact operator line per action; no payload/content is ever printed."""

    if not envelope.get("ok"):
        return "ERROR %s: %s" % (envelope.get("reason"), envelope.get("detail") or "")
    lines = ["ACTION REVIEW  state_root=%s  count=%s"
             % (envelope.get("state_root"), envelope.get("count"))]
    for row in envelope.get("actions") or ():
        lines.append(
            "%-22s %-11s %-20s %-10s idem=%s attempts=%d receipts=%d evidence=%d recon=%d "
            "not_sent=%s" % (
                row.get("action_id"), row.get("status"), row.get("phase"),
                row.get("outcome"), row.get("idempotency_key"),
                len(row.get("attempts") or ()), len(row.get("receipts") or ()),
                len(row.get("result_evidence") or ()), len(row.get("reconciliation") or ()),
                (row.get("not_sent") or {}).get("value")))
    return "\n".join(lines)


__all__ = [
    "BOUNDARIES",
    "NAMESPACE_AGENCY",
    "NOT_IMPLEMENTED",
    "NOT_RECORDED",
    "build_action_review",
    "named_gaps",
    "project_action",
    "render_text",
]
