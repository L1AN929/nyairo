#!/usr/bin/env python3
"""External stop: a local operator ends one Activity without pretending it was her choice.

Design (v2, this window's approved contract)
--------------------------------------------
* The caller is the *local operator of this instance*.  The only authority inside the system
  is the existing ACTIVITY_CONSEQUENCE owner path: the composition root mints one opaque
  capability for ``STOP_INTENT`` + ``CANCEL`` and this module never widens it.
* The request is three facts: ``activity_id``, ``expected_revision``, a stable
  ``operation_key`` (plus a closed ``reason_code``).  Subject, authority and serve epoch come
  from the server side, never from the request body.
* A durable stop intent is written *inside* the Activity record the owner already has.  Once
  accepted it blocks every further dispatch of that Activity -- the check lives at the real
  submit gate, not only in the candidate projection.
* Nothing is rolled back.  A committed Action/version stays committed, an UNKNOWN stays
  unknown (only the original operation may be reconciled), and CANCELLED is not a World undo.
* When the Activity is inside an atomic region the intent stays PENDING and the answer says
  so; only the owner reaching a safe boundary turns it into CANCELLED.

This module owns no store, no journal and no timer.  The durable facts are the LR-2 Activity
record and the transitions it already writes; the optional control channel is only a mailbox
between the phone process and the live resident, and it is not a second truth.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from activity_continuity import (
    EXTERNAL_STOP_FINALIZED,
    EXTERNAL_STOP_PENDING,
    EXTERNAL_STOP_REASON_CODES,
    SCHEMA_EXTERNAL_STOP,
    validate_external_stop_payload,
)

SCHEMA_STOP_REQUEST = "chiyo.life.external_stop_request.v1"
SCHEMA_STOP_RESULT = "chiyo.life.external_stop_result.v1"
AUTHORITY_LOCAL_OPERATOR = "LOCAL_OPERATOR"

STATUS_ACCEPTED = "ACCEPTED"
STATUS_PENDING = "PENDING"
STATUS_CANCELLED = "CANCELLED"
STATUS_REPLAY = "REPLAY"
STATUS_REFUSED = "REFUSED"

#: Result codes are a closed vocabulary so the phone layer never has to interpret an
#: exception string and never echoes a token.
REFUSED_ACTIVITY_NOT_FOUND = "ACTIVITY_NOT_FOUND"
REFUSED_REVISION_STALE = "REVISION_STALE"
REFUSED_TERMINAL = "ACTIVITY_ALREADY_TERMINAL"
REFUSED_SUBJECT = "SUBJECT_MISMATCH"
REFUSED_UNKNOWN_REASON = "UNKNOWN_REASON_CODE"
REFUSED_AUTHORITY = "AUTHORITY_UNAVAILABLE"
REFUSED_OPERATION_KEY_CONFLICT = "OPERATION_KEY_CONFLICT"
REFUSED_RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
REFUSED_CONTROL_UNAVAILABLE = "CONTROL_UNAVAILABLE"
#: The mailbox is not authority.  These name *why* a queued file was not believed.
REFUSED_MAILBOX_FORGED = "MAILBOX_AUTHENTICATION_FAILED"
REFUSED_MAILBOX_ISSUER = "MAILBOX_ISSUER_UNKNOWN"
REFUSED_MAILBOX_EPOCH = "STALE_SERVE_EPOCH"
REFUSED_MAILBOX_INSTANCE = "INSTANCE_MISMATCH"
REFUSED_OPERATOR_CREDENTIAL = "OPERATOR_CREDENTIAL_UNAVAILABLE"

INTENT_PREFIX = "opstop"
_OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


class ExternalStopRefused(RuntimeError):
    """A typed refusal that never carries a credential or private text."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(str(reason_code))
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


@dataclass(frozen=True)
class ExternalStopRequest:
    activity_id: str
    expected_revision: int
    operation_key: str
    reason_code: str
    subject_id: str
    epoch: str
    requested_at: str
    intent_id: str = ""
    boundary_mode: str = "IMMEDIATE"
    unresolved_action_refs: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_EXTERNAL_STOP,
            "intent_id": self.intent_id,
            "operation_key": self.operation_key,
            "activity_id": self.activity_id,
            "activity_revision": int(self.expected_revision),
            "reason_code": self.reason_code,
            "requested_at": self.requested_at,
            "authority": AUTHORITY_LOCAL_OPERATOR,
            "subject_id": self.subject_id,
            "epoch": self.epoch,
            "boundary_mode": self.boundary_mode,
            "unresolved_action_refs": list(self.unresolved_action_refs),
            "state": EXTERNAL_STOP_PENDING,
        }


def build_request(
    *,
    activity_id: Any,
    expected_revision: Any,
    operation_key: Any,
    reason_code: Any,
    subject_id: Any,
    epoch: Any,
    requested_at: Optional[str] = None,
    intent_id: Optional[str] = None,
    boundary_mode: str = "IMMEDIATE",
    unresolved_action_refs: Sequence[str] = (),
) -> ExternalStopRequest:
    """Server-side construction.  The body cannot supply subject/epoch/authority."""

    act_id = str(activity_id or "").strip()
    if not act_id:
        raise ExternalStopRefused("ACTIVITY_ID_REQUIRED")
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        raise ExternalStopRefused("EXPECTED_REVISION_REQUIRED") from None
    if revision < 0:
        raise ExternalStopRefused("EXPECTED_REVISION_REQUIRED")
    key = str(operation_key or "").strip()
    if not _OPERATION_KEY_RE.match(key):
        raise ExternalStopRefused("OPERATION_KEY_INVALID")
    reason = str(reason_code or "").strip().upper()
    if reason not in EXTERNAL_STOP_REASON_CODES:
        raise ExternalStopRefused(REFUSED_UNKNOWN_REASON)
    subject = str(subject_id or "").strip()
    if not subject:
        raise ExternalStopRefused(REFUSED_SUBJECT)
    epoch_text = str(epoch or "").strip()
    if not epoch_text:
        raise ExternalStopRefused(REFUSED_AUTHORITY)
    # The intent id is derived from the stable operation key, not a fresh random value: a
    # retry of the same operation must land on the same owner intent, and a different payload
    # under the same key is refused by the owner's payload fingerprint rather than silently
    # becoming a second stop.
    intent = str(intent_id or "").strip()
    if not intent:
        intent = "opstop:%s" % hashlib.sha256(
            ("external-stop:" + key).encode("utf-8")).hexdigest()[:16]
    if not intent.startswith(INTENT_PREFIX + ":"):
        intent = "opstop:%s" % intent
    return ExternalStopRequest(
        activity_id=act_id,
        expected_revision=revision,
        operation_key=key,
        reason_code=reason,
        subject_id=subject,
        epoch=epoch_text,
        requested_at=str(requested_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        intent_id=intent,
        boundary_mode=str(boundary_mode or "IMMEDIATE").strip().upper(),
        unresolved_action_refs=tuple(str(ref) for ref in unresolved_action_refs if str(ref)),
    )


# ---------------------------------------------------------------------------------------------
# boundary resolution (existing interruption rules, no new rule)
# ---------------------------------------------------------------------------------------------


def _owner_confirmed_boundary(interruption: Any, activity_id: str,
                              activity_revision: Any = None) -> bool:
    """Did the LR-3 owner already apply a real safe boundary for this Activity?

    The only evidence accepted is a record the boundary handler / the narrow stop entry itself
    wrote under ``applied_boundaries``.  Nothing here fabricates one and nothing reads the
    caller's word.  When the record and the live Activity both name a revision they must
    agree: a boundary recorded at an earlier revision cannot authorize a later one.
    """

    if not activity_id:
        return False
    try:
        state = interruption.interruption_store.load_state()
    except Exception:
        return False
    applied = state.get("applied_boundaries") or {}
    if not isinstance(applied, Mapping):
        return False
    for record in applied.values():
        if not isinstance(record, Mapping):
            continue
        if str(record.get("activity_id") or "") != activity_id:
            continue
        recorded = record.get("activity_revision")
        if recorded is not None and activity_revision is not None:
            try:
                if int(recorded) != int(activity_revision):
                    continue
            except (TypeError, ValueError):
                continue
        return True
    return False


def _safe_to_finalize(activity: Mapping[str, Any], interruption: Any) -> tuple[bool, str]:
    """Can this Activity be ended now without tearing an atomic region apart?

    Only the existing LR-3 attention/interruptibility values are consulted.  An active atomic
    region keeps the stop pending until the owner closes it through
    ``exit_atomic_region``; a SAFE_BOUNDARY interruptibility needs a boundary record the owner
    itself applied.  When neither exists the answer is an explicit PENDING basis -- never a
    silent "safe" and never a fabricated boundary.
    """

    if interruption is None:
        return True, "NO_INTERRUPTION_AUTHORITY"
    try:
        _occ, interruptibility, atomic = interruption.get_effective_attention(activity)
    except Exception as exc:  # an unreadable interruption state must not become "safe"
        return False, "INTERRUPTION_READ_FAILED:%s" % type(exc).__name__
    if atomic is not None:
        return False, "ATOMIC_REGION_ACTIVE"
    activity_id = str(activity.get("activity_id") or "")
    if _owner_confirmed_boundary(interruption, activity_id, activity.get("revision")):
        # A record the owner itself wrote under ``applied_boundaries`` is evidence even when
        # the Activity has since left ACTIVE (e.g. the boundary handler paused it).
        return True, "SAFE_BOUNDARY_CONFIRMED_BY_OWNER"
    if str(interruptibility) == "SAFE_BOUNDARY":
        return False, "SAFE_BOUNDARY_NOT_CONFIRMED_BY_OWNER"
    return True, "NONE"


# ---------------------------------------------------------------------------------------------
# the owner-side service
# ---------------------------------------------------------------------------------------------


class ExternalStopService:
    """Thin wrapper over the existing LR-2 command; no state of its own."""

    def __init__(
        self,
        *,
        activity_command: Any,
        activity_read: Any,
        activity_store: Any,
        stop_capability: Any,
        now_iso: Callable[[], str],
        subject_id: Optional[str] = None,
        interruption: Any = None,
        cancel_handoff: Optional[Callable[[str], Mapping[str, Any]]] = None,
        action_read: Any = None,
    ) -> None:
        if not callable(now_iso):
            raise TypeError("now_iso must be callable")
        self.activity_command = activity_command
        self.activity_read = activity_read
        self.activity_store = activity_store
        self.stop_capability = stop_capability
        self.subject_id = str(subject_id or "")
        self.interruption = interruption
        self.cancel_handoff = cancel_handoff
        #: AR-0 read face.  It is the only source for "is an Action of this Activity still
        #: possibly in effect?" -- the safe-boundary confirmation refuses without it.
        self.action_read = action_read
        self._now_iso = now_iso
        self.last_handoff: Optional[dict[str, Any]] = None

    # -- reads ---------------------------------------------------------------------------
    def _store_revision(self) -> int:
        state, _ = self.activity_store.load_verified_state_and_journal(_include_journal=False)
        return int(state["revision"])

    def _activity(self, activity_id: str) -> Mapping[str, Any]:
        act = self.activity_read.get_activity(activity_id)
        if act is None:
            raise ExternalStopRefused(REFUSED_ACTIVITY_NOT_FOUND)
        return act

    def read_state(self, activity_id: str) -> dict[str, Any]:
        act = self.activity_read.get_activity(activity_id)
        if act is None:
            return {"status": STATUS_REFUSED, "reason_code": REFUSED_ACTIVITY_NOT_FOUND,
                    "activity_id": activity_id}
        stop = dict(act.get("external_stop") or {})
        return {
            "schema_version": "chiyo.life.external_stop_state.v1",
            "activity_id": act.get("activity_id"),
            "activity_status": act.get("status"),
            "activity_revision": act.get("revision"),
            "stop_state": stop.get("state"),
            "intent_id": stop.get("intent_id"),
            "reason_code": stop.get("reason_code"),
            "requested_at": stop.get("requested_at"),
            "accepted_at": stop.get("accepted_at"),
            "finalized_at": stop.get("finalized_at"),
            "boundary_mode": stop.get("boundary_mode"),
            "unresolved_action_refs": list(stop.get("unresolved_action_refs") or []),
            "terminal_reason_code": act.get("terminal_reason_code"),
            "checkpoint_ref": act.get("checkpoint_ref"),
            # Never a body, a token or a credential: the reader can state *that* a stop
            # exists without reading anything private about her life.
            "last_action_ref": act.get("last_action_ref"),
        }

    # -- writes --------------------------------------------------------------------------
    def accept(self, request: ExternalStopRequest) -> dict[str, Any]:
        """Durably accept the intent.  Idempotent on the owner's own operation key."""

        act = self._activity(request.activity_id)
        if self.subject_id and str(act.get("subject_id") or "") != self.subject_id:
            raise ExternalStopRefused(REFUSED_SUBJECT)
        # A replay is recognised before the revision and terminal checks: the owner already
        # accepted this exact operation, so the caller's idea of the revision (it may have
        # moved since) is not evidence of a stale request.  Identity is the stable operation
        # key + intent + reason; a different payload under the same key is still a conflict.
        existing = dict(act.get("external_stop") or {})
        if (existing.get("intent_id") == request.intent_id
                and existing.get("operation_key") == request.operation_key
                and existing.get("reason_code") == request.reason_code):
            return self._result(request, STATUS_REPLAY, existing)
        if str(act.get("status") or "") in {
            "COMPLETED", "FAILED", "ABANDONED", "CANCELLED", "EXPIRED",
        }:
            raise ExternalStopRefused(REFUSED_TERMINAL)
        if int(act.get("revision", -1)) != int(request.expected_revision):
            raise ExternalStopRefused(REFUSED_REVISION_STALE)
        payload = request.payload()
        validate_external_stop_payload(payload, activity=act, subject_id=self.subject_id or None)
        revision = self._store_revision()
        try:
            result = self.activity_command.record_external_stop(
                activity_id=request.activity_id,
                expected_revision=revision,
                idempotency_key="opstop_intent:%s" % request.operation_key,
                source_refs=[request.intent_id],
                external_stop=payload,
                consequence_capability=self.stop_capability,
                proposal_ref=request.intent_id,
                occurred_at=request.requested_at,
                observed_at=self._now_iso(),
            )
        except Exception as exc:
            raise ExternalStopRefused(
                _classify(exc), "%s: %s" % (type(exc).__name__, exc)) from exc
        stop = dict((getattr(result, "activity", None) or {}).get("external_stop") or payload)
        replay = bool(getattr(result, "idempotent_replay", False))
        return self._result(request, STATUS_REPLAY if replay else STATUS_ACCEPTED, stop)

    def finalize(self, request: ExternalStopRequest, *,
                 allow_boundary_confirmation: bool = False) -> dict[str, Any]:
        """Turn the pending intent into CANCELLED when the owner is at a safe boundary.

        ``allow_boundary_confirmation`` is set only when the *same* accepted operation is sent
        again.  A first request never confirms anything by itself: an Activity marked
        ``SAFE_BOUNDARY`` stays PENDING until its owner can prove the boundary (no Action of
        this Activity is in flight, no atomic region is active).  The caller's word is never
        the evidence -- the LR-3 owner writes its own record and the stop service then reads
        the live facts back.
        """

        act = self._activity(request.activity_id)
        status = str(act.get("status") or "")
        stop = dict(act.get("external_stop") or {})
        if status == "CANCELLED":
            if stop.get("intent_id") == request.intent_id:
                out = self._result(request, STATUS_REPLAY, stop)
                # The terminal is already committed.  A replay repairs a handoff that was
                # never emitted (e.g. the process died between CANCEL and the emit): AG-2's
                # handoff id is derived from (activity, kind), so this is idempotent and can
                # never become a second handoff.
                self._emit_cancel_handoff(request, out)
                return out
            raise ExternalStopRefused(REFUSED_TERMINAL)
        if status in {"COMPLETED", "FAILED", "ABANDONED", "EXPIRED"}:
            raise ExternalStopRefused(REFUSED_TERMINAL)
        if stop.get("intent_id") != request.intent_id:
            raise ExternalStopRefused("STOP_INTENT_NOT_ACCEPTED")
        if stop.get("state") == EXTERNAL_STOP_FINALIZED:
            out = self._result(request, STATUS_REPLAY, stop)
            self._emit_cancel_handoff(request, out)
            return out
        safe, basis = _safe_to_finalize(act, self.interruption)
        if not safe and allow_boundary_confirmation and basis == (
                "SAFE_BOUNDARY_NOT_CONFIRMED_BY_OWNER"):
            confirmed, confirm_basis = self._confirm_owner_boundary(request)
            if confirmed:
                safe, basis = _safe_to_finalize(
                    self._activity(request.activity_id), self.interruption)
            else:
                basis = confirm_basis
        if not safe:
            out = self._result(request, STATUS_PENDING, stop)
            out["boundary"] = {"safe": False, "basis": basis}
            out["detail"] = ("the stop intent is durable and blocks further dispatch; the "
                             "Activity is not at a safe boundary yet")
            return out
        revision = self._store_revision()
        try:
            result = self.activity_command.cancel_activity(
                activity_id=request.activity_id,
                expected_revision=revision,
                idempotency_key="opstop_cancel:%s" % request.operation_key,
                source_refs=[request.intent_id],
                causal_parent_refs=[request.intent_id],
                reason_code="CANCELLED_BY_EXTERNAL_OPERATOR",
                consequence_capability=self.stop_capability,
                proposal_ref=request.intent_id,
                occurred_at=self._now_iso(),
                observed_at=self._now_iso(),
            )
        except Exception as exc:
            raise ExternalStopRefused(
                _classify(exc), "%s: %s" % (type(exc).__name__, exc)) from exc
        committed = dict((getattr(result, "activity", None) or {}).get("external_stop") or stop)
        replay = bool(getattr(result, "idempotent_replay", False))
        out = self._result(request, STATUS_REPLAY if replay else STATUS_CANCELLED, committed)
        out["boundary"] = {"safe": True, "basis": basis}
        if not replay:
            self._emit_cancel_handoff(request, out)
        return out

    def handle(self, request: ExternalStopRequest) -> dict[str, Any]:
        accepted = self.accept(request)
        if accepted["status"] not in (STATUS_ACCEPTED, STATUS_REPLAY):
            return accepted
        # A repeat of the same accepted operation is the operator's timing signal ("try to
        # finish now"); the owner still decides whether a real boundary exists.
        return self.finalize(
            request, allow_boundary_confirmation=(accepted["status"] == STATUS_REPLAY))

    # -- safe boundary confirmation (only for one accepted external stop) ----------------
    def _in_flight_actions(self, activity_id: str) -> list[str]:
        """AR-0 Action ids of this Activity that may still be in effect."""

        from action_reality_ledger import IN_FLIGHT_ACTION_STATUSES

        if self.action_read is None:
            raise RuntimeError("ACTION_LEDGER_UNAVAILABLE")
        rows = self.action_read.list_actions()
        return [str(row.get("action_id") or "") for row in rows
                if str(row.get("activity_ref") or "") == str(activity_id)
                and str(row.get("status") or "") in IN_FLIGHT_ACTION_STATUSES]

    def _confirm_owner_boundary(self, request: ExternalStopRequest) -> tuple[bool, str]:
        """Ask LR-3 to record its own safe-boundary fact; never accept a caller's claim."""

        if self.interruption is None or not hasattr(
                self.interruption, "confirm_external_stop_boundary"):
            return False, "OWNER_BOUNDARY_API_UNAVAILABLE"
        if self.action_read is None:
            return False, "ACTION_LEDGER_UNAVAILABLE"
        try:
            in_flight = self._in_flight_actions(request.activity_id)
        except Exception as exc:
            return False, "ACTION_LEDGER_READ_FAILED:%s" % type(exc).__name__
        if in_flight:
            # An Action already past dispatch is not recalled, and its effect is not
            # rewritten.  The stop stays PENDING until the owner's own reconciliation
            # settles it; retrying the same request then re-checks.
            return False, "IN_FLIGHT_ACTION_UNRESOLVED"
        act = self._activity(request.activity_id)
        boundary_ref = "safe_boundary:external_stop:%s" % request.intent_id
        try:
            self.interruption.confirm_external_stop_boundary(
                activity_id=request.activity_id,
                expected_activity_revision=int(act.get("revision", -1)),
                boundary_ref=boundary_ref,
                observed_at=self._now_iso(),
            )
        except Exception as exc:
            return False, "BOUNDARY_CONFIRMATION_REFUSED:%s" % type(exc).__name__
        return True, "SAFE_BOUNDARY_CONFIRMED_BY_OWNER"

    def _emit_cancel_handoff(self, request: ExternalStopRequest,
                             out: dict[str, Any]) -> None:
        """Emit/repair the one CANCELLED handoff; a failure never rolls the terminal back."""

        if self.cancel_handoff is None:
            return
        try:
            self.last_handoff = dict(self.cancel_handoff(request.activity_id) or {})
            out["handoff"] = self.last_handoff
        except Exception as exc:  # the terminal stays committed; AG-2 reconcile retries
            self.last_handoff = {"status": "HANDOFF_FAILED",
                                 "error": "%s: %s" % (type(exc).__name__, exc)}
            out["handoff"] = self.last_handoff

    def _result(self, request: ExternalStopRequest, status: str,
                stop: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_STOP_RESULT,
            "status": status,
            "activity_id": request.activity_id,
            "intent_id": request.intent_id,
            "operation_key": request.operation_key,
            "reason_code": request.reason_code,
            "stop_state": stop.get("state"),
            "activity_revision": stop.get("finalized_revision") or stop.get("accepted_revision"),
            "accepted_at": stop.get("accepted_at"),
            "finalized_at": stop.get("finalized_at"),
            # Honest distinction for every read face: only these statuses mean the *owner*
            # holds a durable stop intent.  A queued mailbox file is answered separately.
            "accepted_as_stop": status in {STATUS_ACCEPTED, STATUS_PENDING, STATUS_CANCELLED,
                                          STATUS_REPLAY},
            # No token, no hash, no private text: only whether the credential matched.
            "credential_checked": True,
        }


def _classify(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    if "Idempotency" in name or "idempotency" in text:
        return REFUSED_OPERATION_KEY_CONFLICT
    if "Revision" in name or "revision_conflict" in text:
        return REFUSED_REVISION_STALE
    if "Terminal" in name or "terminal" in text:
        return REFUSED_TERMINAL
    if "Foreground" in name:
        return "FOREGROUND_CONFLICT"
    return "OWNER_REFUSED:%s" % name


# ---------------------------------------------------------------------------------------------
# the optional cross-process mailbox (phone -> live resident)
# ---------------------------------------------------------------------------------------------

#: A mailbox file is written by the phone process, but *authority* stays with the serve
#: credential.  The phone signs the request with a key derived from this serve's operator
#: token; the resident verifies it against the credential file it can read, and also requires
#: the file to name the same instance, Life subject and serve epoch.  A file dropped into the
#: directory by anyone else is refused by name -- it is never treated as ``operator = true``
#: because it says so.  Someone who can read the credential (same uid) is still outside this
#: boundary; that limit is stated, not papered over.
MAILBOX_ISSUER = "LOCAL_OPERATOR"
MAILBOX_KEY_LABEL = b"chiyo.life.external-stop.mailbox.v1"
MAILBOX_SCHEMA = SCHEMA_STOP_REQUEST


def mailbox_key(token: str) -> bytes:
    return hmac.new(str(token).encode("utf-8"), MAILBOX_KEY_LABEL, hashlib.sha256).digest()


def _canonical_body(payload: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in payload.items()
            if key != "mac" and not str(key).startswith("_")}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def mailbox_mac(payload: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, _canonical_body(payload), hashlib.sha256).hexdigest()


def _control_dir(state_root: Path | str) -> Path:
    return Path(state_root) / "external-stop"


def instance_root_for_state_root(state_root: Path | str) -> Path | None:
    """``<instance>/life/state`` -> ``<instance>``; anything else is not guessed."""

    root = Path(state_root).expanduser().resolve()
    if root.name == "state" and root.parent.name == "life":
        return root.parent.parent
    return None


def load_operator_credential(instance_root: Path | str) -> Any:
    """Read the live serve credential from the instance root; fail closed when absent.

    The credential module lives in ``components/phone`` and has no package-relative imports,
    so it is loaded by path rather than by adding a Life->phone package dependency.
    """

    phone_dir = Path(__file__).resolve().parents[2] / "phone"
    if str(phone_dir) not in sys.path:
        sys.path.insert(0, str(phone_dir))
    import operator_stop as _operator_stop  # noqa: WPS433 - lazy on purpose

    return _operator_stop.read_credential(_operator_stop.credential_path(instance_root))


def verify_mailbox_request(payload: Mapping[str, Any], *, credential: Any) -> None:
    """Refuse anything the current serve did not sign.  Raises a typed refusal."""

    if str(payload.get("issuer") or "") != MAILBOX_ISSUER:
        raise ExternalStopRefused(REFUSED_MAILBOX_ISSUER)
    instance = payload.get("instance")
    claimed = ""
    if isinstance(instance, str) and instance.strip():
        claimed = str(Path(instance).expanduser().resolve())
    if not claimed or not hmac.compare_digest(claimed, str(getattr(credential, "instance", ""))):
        raise ExternalStopRefused(REFUSED_MAILBOX_INSTANCE)
    epoch = str(payload.get("epoch") or "")
    if not epoch or not hmac.compare_digest(epoch, str(getattr(credential, "epoch", "") or "")):
        # A request queued under an earlier serve is *not* a new authorization.  If the owner
        # already accepted that intent it lives in the Activity record and can still be
        # finalised through a freshly signed request; the old file alone never re-grants it.
        raise ExternalStopRefused(REFUSED_MAILBOX_EPOCH)
    if str(payload.get("subject_id") or "") != str(getattr(credential, "life_subject", "") or ""):
        raise ExternalStopRefused(REFUSED_SUBJECT)
    mac = str(payload.get("mac") or "")
    expected = mailbox_mac(payload, mailbox_key(getattr(credential, "token", "")))
    if not mac or not hmac.compare_digest(mac, expected):
        raise ExternalStopRefused(REFUSED_MAILBOX_FORGED)



class StopControlChannel:
    """One request file per operation key.

    The phone process can write a request but it cannot write the Activity owner: it does not
    hold the single-writer lease.  The live resident drains this mailbox at its own round
    boundary and answers through the same owner path.  Every file is 0600 and the directory is
    never inside the repository.  Nothing here is a second Activity truth.

    ``state_root`` is the *Life* state root (``<instance>/life/state``), the same path the
    resident already uses, so both processes compute it from the instance without a new
    configuration knob.
    """

    def __init__(self, state_root: Path | str) -> None:
        self.root = Path(state_root).expanduser().resolve()
        self.directory = _control_dir(self.root)

    def _ensure(self) -> None:
        if self.directory.is_symlink():
            raise ExternalStopRefused(REFUSED_CONTROL_UNAVAILABLE, "directory is a symlink")
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def submit(self, request: ExternalStopRequest, *, key: bytes,
               instance: Path | str) -> Path:
        """Queue one request signed with this serve's credential key.

        ``key`` is the derived mailbox key -- never the token itself, and never written down.
        Without it a request cannot be created, so ``submit`` can never mint authority.
        """

        if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
            raise ExternalStopRefused(REFUSED_OPERATOR_CREDENTIAL, "mailbox key required")
        self._ensure()
        name = "%s.json" % re.sub(r"[^A-Za-z0-9_.-]", "_", request.operation_key)[:120]
        target = self.directory / name
        if target.is_symlink():
            raise ExternalStopRefused(REFUSED_CONTROL_UNAVAILABLE, "request path is a symlink")
        payload = {
            "schema_version": SCHEMA_STOP_REQUEST,
            "issuer": MAILBOX_ISSUER,
            "instance": str(Path(instance).expanduser().resolve()),
            "activity_id": request.activity_id,
            "expected_revision": request.expected_revision,
            "operation_key": request.operation_key,
            "reason_code": request.reason_code,
            "subject_id": request.subject_id,
            "epoch": request.epoch,
            "requested_at": request.requested_at,
            "intent_id": request.intent_id,
            "boundary_mode": request.boundary_mode,
            "unresolved_action_refs": list(request.unresolved_action_refs),
        }
        payload["mac"] = mailbox_mac(payload, bytes(key))
        tmp = self.directory / (".%s.%s.tmp" % (name, secrets.token_hex(6)))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(tmp, flags, 0o600)
        except OSError as exc:
            raise ExternalStopRefused(REFUSED_CONTROL_UNAVAILABLE, str(exc)) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise ExternalStopRefused(REFUSED_CONTROL_UNAVAILABLE, str(exc)) from exc
        return target

    def list_requests(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            if path.is_symlink():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("schema_version") != SCHEMA_STOP_REQUEST:
                continue
            payload["_path"] = str(path)
            out.append(payload)
        return out

    def write_result(self, request_path: Path | str, result: Mapping[str, Any]) -> Path:
        path = Path(request_path)
        target = path.with_suffix(".result.json")
        payload = dict(result)
        payload["schema_version"] = SCHEMA_STOP_RESULT
        tmp = target.with_name(".%s.%s.tmp" % (target.name, secrets.token_hex(6)))
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return target

    def read_result(self, operation_key: str) -> Optional[dict[str, Any]]:
        name = "%s.result.json" % re.sub(
            r"[^A-Za-z0-9_.-]", "_", str(operation_key))[:120]
        target = self.directory / name
        if target.is_symlink() or not target.is_file():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def clear(self, request_path: Path | str) -> None:
        """Remove the request we just handled.  Never touches an unrelated request."""

        path = Path(request_path)
        try:
            path.unlink()
        except OSError:
            pass


def request_from_mailbox(payload: Mapping[str, Any]) -> ExternalStopRequest:
    return build_request(
        activity_id=payload.get("activity_id"),
        expected_revision=payload.get("expected_revision"),
        operation_key=payload.get("operation_key"),
        reason_code=payload.get("reason_code"),
        subject_id=payload.get("subject_id"),
        epoch=payload.get("epoch"),
        requested_at=payload.get("requested_at"),
        intent_id=payload.get("intent_id"),
        boundary_mode=str(payload.get("boundary_mode") or "IMMEDIATE"),
        unresolved_action_refs=payload.get("unresolved_action_refs") or (),
    )


def stop_pending_for(activity: Mapping[str, Any]) -> bool:
    """The one predicate the submit gate uses: a pending stop blocks new dispatch."""

    stop = activity.get("external_stop") or {}
    return isinstance(stop, Mapping) and stop.get("state") == EXTERNAL_STOP_PENDING


__all__ = [
    "AUTHORITY_LOCAL_OPERATOR",
    "ExternalStopRefused",
    "ExternalStopRequest",
    "ExternalStopService",
    "INTENT_PREFIX",
    "REFUSED_ACTIVITY_NOT_FOUND",
    "REFUSED_AUTHORITY",
    "REFUSED_CONTROL_UNAVAILABLE",
    "REFUSED_OPERATION_KEY_CONFLICT",
    "REFUSED_REVISION_STALE",
    "REFUSED_RUNTIME_UNAVAILABLE",
    "REFUSED_SUBJECT",
    "REFUSED_TERMINAL",
    "REFUSED_UNKNOWN_REASON",
    "SCHEMA_STOP_REQUEST",
    "SCHEMA_STOP_RESULT",
    "STATUS_ACCEPTED",
    "STATUS_CANCELLED",
    "STATUS_PENDING",
    "STATUS_REFUSED",
    "STATUS_REPLAY",
    "StopControlChannel",
    "MAILBOX_ISSUER",
    "build_request",
    "instance_root_for_state_root",
    "load_operator_credential",
    "mailbox_key",
    "mailbox_mac",
    "request_from_mailbox",
    "stop_pending_for",
    "verify_mailbox_request",
]
