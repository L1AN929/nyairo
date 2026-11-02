#!/usr/bin/env python3
"""S10 F113-F115 demo: accepted-then-lost stays UNKNOWN, layered, and sourced.

What is real here (local only, 0 product model calls, 0 network)
---------------------------------------------------------------
* the real durable receipt owner `DurableContactReceiptStore` (SQLite WAL journal),
* the real reconciliation authority and the real read-only result projector,
* a controlled local transport double that only records observations.  It never
  opens a socket, never touches Telegram, and is named as a double in the output.

The chain
---------
1. The platform accepts the message (`PROVIDER_ACCEPTED` -> ACKNOWLEDGED).
2. The adapter then crashes after acceptance.  The lost answer is recorded as
   `STATUS_UNKNOWN`; the action becomes **UNKNOWN**, not FAILED, and the
   reconciliation reason names the stage: `UNKNOWN_AFTER_ACCEPT`.
3. The projection keeps the layers apart: submitted=True, acknowledged=True,
   delivered=None (not True), failure=None, unknown=True.
4. The original operation is queried through the same key; the transport send
   counter stays at 1 - a lost answer never resends.
5. A fresh store on the same file re-reads the same UNKNOWN/UNKNOWN_AFTER_ACCEPT;
   nothing is replayed.
6. Boundaries, printed explicitly: a lone uncertain observation with no
   submit/accept fact keeps `UNKNOWN_PROVIDER_STATE` (the kind set alone cannot
   prove submission); `FAILED_BEFORE_ACCEPT` is the only FAILURE; a positive
   delivery plus a delivery failure is CONFLICT.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s10_delivery_seams_demo.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALPHA = ROOT / "components" / "alpha"
CONTACT = ALPHA / "chiyo" / "contact"
for entry in (str(CONTACT), str(ALPHA), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from contact_receipt_model import ContactTransportReceiptEvidence  # noqa: E402
from contact_reconciliation import ContactReconciliationAuthority  # noqa: E402
from durable.contact_durable_store import DurableContactReceiptStore  # noqa: E402

NOW = "2026-10-27T12:00:00Z"
PAYLOAD_HASH = hashlib.sha256(b"s10-delivery-seams-payload").hexdigest()
CONTENT_HASH = hashlib.sha256(b"s10-delivery-seams-content").hexdigest()


def _action(name: str) -> str:
    return "actn:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:40]


def _key(name: str) -> str:
    return "ar0_act:" + hashlib.sha256(("key:" + name).encode("utf-8")).hexdigest()[:40]


def _evidence(action_ref: str, submission_key: str, *, kind: str, status: str,
              effect: bool, count: int, source: str) -> ContactTransportReceiptEvidence:
    return ContactTransportReceiptEvidence.create(
        action_ref=action_ref,
        submission_key=submission_key,
        namespace="sandbox.telegram",
        transport_record_ref="tlog:" + hashlib.sha256((action_ref + kind).encode()).hexdigest(),
        observation_kind=kind,
        provider_status=status,
        recipient_ref="recipient:local-demo",
        channel="channel:local_inbox",
        payload_hash=PAYLOAD_HASH,
        content_hash=CONTENT_HASH,
        provider_message_ref=None,
        provider_time=None,
        observed_at=NOW,
        recorded_at=NOW,
        provenance_refs=("source:" + source, "request:mreq-demo", "payload:payload-demo"),
        effect_applied=effect,
        effect_count_for_key=count,
    )


class RecordingLocalTransport:
    """Controlled local double: counts sends, answers queries, never sends anything."""

    def __init__(self) -> None:
        self.send_count = 0
        self._by_key: dict[str, dict[str, Any]] = {}

    def send(self, submission_key: str, *, accepted: bool) -> dict[str, Any]:
        self.send_count += 1
        observation = {"submission_key": submission_key,
                       "status": "ACKNOWLEDGED" if accepted else "UNKNOWN",
                       "acknowledged": accepted, "delivered": False}
        self._by_key[submission_key] = observation
        return observation

    def record_lost(self, submission_key: str) -> dict[str, Any]:
        observation = {"submission_key": submission_key, "status": "UNKNOWN",
                       "acknowledged": False, "delivered": False}
        self._by_key[submission_key] = observation
        return observation

    def query(self, submission_key: str) -> dict[str, Any] | None:
        return self._by_key.get(submission_key)


def _project(store: DurableContactReceiptStore, action_ref: str, label: str,
             checks: list[dict[str, Any]]) -> Any:
    projection = store.project(action_ref=action_ref)
    facts = {"label": label, "state": projection.current_state,
             "submitted": projection.submitted, "acknowledged": projection.acknowledged,
             "delivered": projection.delivered, "read_status": projection.read_status,
             "failure": projection.failure, "unknown": projection.unknown}
    checks.append(facts)
    return projection


def _stage_reason(store: DurableContactReceiptStore, action_ref: str) -> str:
    """Read the reason from the existing reconciliation authority, not the projector."""
    receipts, _records = store.recover()
    record = ContactReconciliationAuthority(store=receipts).build(
        action_ref=action_ref, recorded_at=NOW)
    return record.reason


def _expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="s10-delivery-seams-"))
    db_path = root / "receipts.sqlite"
    transport = RecordingLocalTransport()
    failures: list[str] = []
    checks: list[dict[str, Any]] = []

    # -- 1/2. accepted, then the adapter crashes ---------------------------
    lost_action = _action("accepted-then-lost")
    lost_key = _key("accepted-then-lost")
    accepted = _evidence(lost_action, lost_key, kind="PROVIDER_ACCEPTED",
                         status="ACKNOWLEDGED", effect=True, count=1,
                         source="src-accepted")
    transport.send(lost_key, accepted=True)

    store = DurableContactReceiptStore(db_path)
    _expect(store.accept_evidence(accepted) is True, "accepted evidence was not journaled", failures)
    after_accept = _project(store, lost_action, "after-platform-accept", checks)
    _expect(after_accept.current_state == "ACKNOWLEDGED",
            f"expected ACKNOWLEDGED after accept, got {after_accept.current_state}", failures)
    _expect(after_accept.acknowledged is True and after_accept.delivered is None,
            "accepted must be ACKNOWLEDGED with delivered unknown", failures)

    # The adapter raises after the platform accepted (controlled local double).
    transport.record_lost(lost_key)
    lost = _evidence(lost_action, lost_key, kind="STATUS_UNKNOWN",
                     status="UNKNOWN", effect=True, count=1, source="src-lost")
    _expect(store.accept_evidence(lost) is True, "lost-answer evidence was not journaled", failures)
    after_loss = _project(store, lost_action, "accepted-then-lost", checks)

    _expect(after_loss.current_state == "UNKNOWN",
            f"lost answer must stay UNKNOWN, got {after_loss.current_state}", failures)
    _expect(after_loss.current_state != "FAILURE",
            "an accepted-then-lost answer was reported as FAILURE", failures)
    _expect(after_loss.failure is None, "UNKNOWN must not claim failure", failures)
    _expect(after_loss.submitted is True, "submitted fact was lost", failures)
    _expect(after_loss.acknowledged is True, "acknowledged fact was lost", failures)
    _expect(after_loss.delivered is None, "UNKNOWN must not claim delivery", failures)
    lost_reason = _stage_reason(store, lost_action)
    _expect(lost_reason == "UNKNOWN_AFTER_ACCEPT",
            f"reason does not name the acceptance stage: {lost_reason!r}", failures)

    # -- 3. the original operation, not a second send ----------------------
    queried = transport.query(lost_key)
    _expect(queried is not None and queried["status"] == "UNKNOWN",
            "original operation query did not return the lost observation", failures)
    _expect(transport.send_count == 1, "a lost answer triggered a resend", failures)

    # Execution source: the result points at this receipt evidence, not a guess.
    receipts = store.recover()[0].by_action(lost_action)
    source_refs = tuple(ref for item in receipts for ref in item.provenance_refs)
    _expect("source:src-lost" in source_refs and "source:src-accepted" in source_refs,
            "receipt provenance does not name the execution sources", failures)

    # -- 4. a fresh store on the same file --------------------------------
    reopened = DurableContactReceiptStore(db_path)
    restarted = _project(reopened, lost_action, "after-reopen", checks)
    _expect(restarted.current_state == "UNKNOWN",
            "UNKNOWN did not survive reopening the store", failures)
    restarted_reason = _stage_reason(reopened, lost_action)
    _expect(restarted_reason == "UNKNOWN_AFTER_ACCEPT",
            "stage reason did not survive reopening the store", failures)
    _expect(len(reopened.recover()[0].by_action(lost_action)) == len(receipts),
            "reopening replayed or dropped receipt evidence", failures)
    _expect(transport.send_count == 1, "reopening triggered a resend", failures)

    # -- 5. boundaries ----------------------------------------------------
    lone_action = _action("lone-unknown")
    lone_key = _key("lone-unknown")
    lone = _evidence(lone_action, lone_key, kind="STATUS_UNKNOWN",
                     status="UNKNOWN", effect=False, count=0, source="src-lone")
    reopened.accept_evidence(lone)
    lone_projection = _project(reopened, lone_action, "lone-unknown", checks)
    _expect(lone_projection.current_state == "UNKNOWN", "lone unknown is not UNKNOWN", failures)
    lone_reason = _stage_reason(reopened, lone_action)
    _expect(lone_reason == "UNKNOWN_PROVIDER_STATE",
            "a lone observation without a submit/accept fact must stay stage-less", failures)

    failed_action = _action("failed-before-accept")
    failed_key = _key("failed-before-accept")
    failed = _evidence(failed_action, failed_key, kind="FAILED_BEFORE_ACCEPT",
                       status="FAILED_BEFORE_ACCEPT", effect=False, count=0,
                       source="src-refused")
    reopened.accept_evidence(failed)
    failed_projection = _project(reopened, failed_action, "failed-before-accept", checks)
    _expect(failed_projection.current_state == "FAILURE",
            "pre-accept failure is not FAILURE", failures)
    _expect(failed_projection.failure is True and failed_projection.submitted is False,
            "pre-accept failure layers are wrong", failures)

    conflict_action = _action("conflict")
    conflict_key = _key("conflict")
    reopened.accept_evidence(_evidence(
        conflict_action, conflict_key, kind="DELIVERY_CONFIRMED", status="DELIVERED",
        effect=True, count=1, source="src-delivered"))
    reopened.accept_evidence(_evidence(
        conflict_action, conflict_key, kind="DELIVERY_FAILED", status="UNKNOWN",
        effect=True, count=1, source="src-failed"))
    conflict_projection = _project(reopened, conflict_action, "conflict", checks)
    _expect(conflict_projection.current_state == "CONFLICT",
            "positive delivery plus failure is not CONFLICT", failures)

    payload = {
        "demo": "s10_delivery_seams",
        "transport": "controlled local double (no network)",
        "store": "DurableContactReceiptStore (sqlite wal)",
        "root": str(root),
        "checks": checks,
        "stage_reasons": {
            "accepted-then-lost": lost_reason,
            "after-reopen": restarted_reason,
            "lone-unknown": lone_reason,
        },
        "failures": failures,
        "ok": not failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
