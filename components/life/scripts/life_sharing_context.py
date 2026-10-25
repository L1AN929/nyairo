#!/usr/bin/env python3
"""v0.4 sharing on the life side: the bounded seam between a Decision and B's sharing owner.

What this module is
-------------------
One translation and one bound.  B owns the sharing lifecycle
(``components/life/scripts/life_sharing_service.py``: ``LifeSharingService`` over the existing
``PrivateProjectService``/``ManagedArtifact`` owner).  This module is what A's cycle is allowed to
do with it:

* read what is *open* (DRAFT / DEFERRED) as a small prompt block -- ids, states, titles, counts and
  the local inbox facts, **never** the shared text;
* turn one Decision-proposed request (already shape-checked by AG-1) into exactly one owner call;
* report the owner's own answer back, bounded, with the delivery fact kept exactly as the owner
  stated it.

What it never does
------------------
* **No send of its own.**  There is no transport here: ``send_local`` means "B writes one artifact
  version into this instance's local inbox".  ``LOCAL_DELIVERED`` is not an external send, not a
  delivery to a person, not "read", and this module never reports it as one.
* **No second store, no timer, no polling.**  Shares live in the owner's artifacts; DEFERRED is
  re-considered only when a Decision explicitly re-considers it.  Nothing here runs per tick and
  nothing here calls a model.
* **No audience/channel freedom.**  Both are B's fixed constants; a caller cannot name another.
* **No invented fields.**  Every keyword this module passes is a documented keyword of B's four
  methods; the shapes it returns are bounded projections of the owner's answer, not a second
  vocabulary.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

import life_sharing_service as lss  # noqa: E402  (B's owner seam; imported, never re-implemented)

__all__ = [
    "MAX_PROMPT_SHARES",
    "REASON_NOT_WIRED",
    "REASON_READ_FAILED",
    "SCHEMA",
    "apply_request",
    "empty_block",
    "prepare_draft",
    "prompt_block",
    "read_draft",
    "report",
    "service_from_env",
]

SCHEMA = "chiyo.life.sharing_context.v1"

#: How many open shares may enter one prompt.  A share is a *summary* here, never its text.
MAX_PROMPT_SHARES = 5
#: How much of the draft's own text may enter one prompt.  The draft itself is bounded by B
#: (2000 chars); this is only the prompt's own cut, so a decision can see *which* sentence it
#: would be sharing without turning the prompt into the document.
MAX_PROMPT_SHARE_TEXT = 160

REASON_NOT_WIRED = "SHARING_SERVICE_NOT_WIRED"
REASON_READ_FAILED = "SHARING_READ_FAILED"

#: The two states worth showing a decision: a draft not yet sent, and one whose condition was named.
OPEN_STATES = (lss.STATE_DRAFT, lss.STATE_DEFERRED)


def _text(value: Any, limit: int = 80) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def empty_block(reason_code: str = REASON_NOT_WIRED, *, detail: str = "") -> dict[str, Any]:
    """The honest absence: no shares, no inbox, no capability -- and the reason."""

    return {
        "schema": SCHEMA,
        "status": "unavailable",
        "reason_code": str(reason_code),
        "detail": _text(detail, 160),
        "channel": lss.CHANNEL_LOCAL_INBOX,
        "audience": lss.AUDIENCE_LOCAL_OWNER,
        "shares": [],
        "counts": {},
        "inbox": {"delivered_count": None, "local_only": True},
        "unknowns": [str(reason_code)],
    }


def service_from_env(environment: Optional[Mapping[str, str]] = None) -> tuple[Any, Optional[str]]:
    """B's ``sharing_from_env`` or ``(None, reason)`` -- never a half-wired service.

    The factory's own refusal (``SharingUnavailable``) is reported as a reason code; an import
    failure or any other construction error is the same honest "not wired" answer, because a
    share that cannot reach its owner must not look like one that can.
    """

    try:
        return lss.sharing_from_env(dict(environment or {})), None
    except lss.SharingUnavailable as error:
        return None, _text(error or REASON_NOT_WIRED, 96) or REASON_NOT_WIRED
    except Exception as error:  # noqa: BLE001 - reported as "not wired", never guessed
        return None, "%s: %s" % (type(error).__name__, _text(error, 96))


def _share_detail(service: Any, share_id: str) -> dict[str, Any]:
    """One open draft's own facts: bounded text, source version/currency, defer condition.

    Exactly one ``read_share`` (the owner's documented read), never the source's body.  A draft
    whose detail cannot be read stays in the list with a named reason, because "I cannot read what
    I wrote" is a fact the decision needs -- and it is the one honest reason not to act on it.
    """

    if not share_id:
        return {"detail_readable": False, "detail_reason": "SHARE_ID_MISSING"}
    try:
        answer = service.read_share(share_id, include_text=True)
    except Exception as error:  # noqa: BLE001
        return {"detail_readable": False, "detail_reason": REASON_READ_FAILED,
                "detail": "%s: %s" % (type(error).__name__, _text(error, 96))[:120]}
    if not isinstance(answer, Mapping) or not answer.get("ok"):
        return {"detail_readable": False,
                "detail_reason": _text((answer or {}).get("reason_code"), 64) or "SHARE_DETAIL_REFUSED"}
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
    share = data.get("share") if isinstance(data.get("share"), Mapping) else {}
    source = share.get("source") if isinstance(share.get("source"), Mapping) else {}
    current = data.get("source_current") if isinstance(data.get("source_current"), Mapping) else {}
    deferred = share.get("deferred") if isinstance(share.get("deferred"), Mapping) else {}
    text = str(share.get("text") or "")
    # The owner's own text-level withholding: a draft whose recorded source can no longer be
    # read keeps its metadata but its words are withheld.  That is not an empty draft and this
    # projection must not make it look like one.  Absent flags stay ``None`` (not guessed).
    withheld_raw = share.get("text_withheld")
    withheld = withheld_raw if isinstance(withheld_raw, bool) else None
    available_raw = share.get("text_available")
    available = available_raw if isinstance(available_raw, bool) else None
    return {
        "detail_readable": True,
        # Her own words, cut for the prompt.  Newlines collapse so one draft cannot add lines.
        "text_excerpt": _text(text, MAX_PROMPT_SHARE_TEXT) if available is not False else "",
        "text_withheld": withheld,
        "text_available": available,
        "withheld_reason_code": (_text(data.get("withheld_reason_code"), 64)
                                 if withheld else ""),
        "text_chars": (share.get("text_chars") if isinstance(share.get("text_chars"), int)
                       else len(text)),
        "source": {
            "artifact_id": _text(source.get("artifact_id"), 96),
            # The version the draft was prepared from: what the owner's CAS checks later.
            "version_id": _text(source.get("version_id"), 96),
            "revision": source.get("revision"),
        },
        # Whether that source is *still* the current one -- the one thing that makes a send fail.
        "source_current": {
            "readable": bool(current.get("readable")),
            "stale": bool(current.get("stale")),
            "matches_recorded": bool(current.get("matches_recorded")),
            "reason_code": _text(current.get("reason_code"), 64),
        },
        "deferred": ({
            "condition": _text(deferred.get("condition"), 200),
            "opportunity_ref": _text(deferred.get("opportunity_ref"), 200),
        } if deferred else None),
        "expires_at": _text(share.get("expires_at"), 40) or None,
        "state_derived": bool(share.get("state_derived")),
    }


def prompt_block(service: Any, *, limit: int = MAX_PROMPT_SHARES) -> dict[str, Any]:
    """What is open right now, bounded -- including the words she already drafted.

    Read-only, and exactly ``1 + N`` owner reads per decision round: one ``list_shares`` to learn
    which shares are open (plus the owner's own counts/inbox), then one ``read_share`` per open
    share to carry the *draft's own bounded text*, its source version, whether that source is still
    current, and the condition a deferred draft is waiting on.  Without those the model cannot know
    *what* it would be sending or *what* it is waiting for, and a defer it writes would be empty.

    Privacy bound stays: only the draft's own text (already bounded by B at 2000 chars) is cut to
    ``MAX_PROMPT_SHARE_TEXT`` for the prompt, the source's full body is never read here, and a
    share whose detail cannot be read is shown with a named ``reason_code`` instead of a guess.
    """

    if service is None:
        return empty_block()
    try:
        bound = max(1, min(int(limit), lss.MAX_LIST))
    except (TypeError, ValueError):
        bound = MAX_PROMPT_SHARES
    try:
        # Read *all* shares and filter locally: the owner's own counts/inbox are computed over the
        # rows it returns, so asking it for one state would silently hide the local delivery count.
        answer = service.list_shares(include_text=False)
    except Exception as error:  # noqa: BLE001 - a failed read is reported, never filled
        return empty_block(REASON_READ_FAILED, detail="%s: %s" % (type(error).__name__, error))
    if not isinstance(answer, Mapping) or not answer.get("ok"):
        return empty_block(str((answer or {}).get("reason_code") or "SHARING_LIST_REFUSED"),
                           detail=_text((answer or {}).get("detail"), 160))
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
    shares: list[dict[str, Any]] = []
    for row in list(data.get("shares") or []):
        if len(shares) >= bound:
            break
        if not isinstance(row, Mapping):
            continue
        if str(row.get("state") or "") not in OPEN_STATES:
            # EXPIRED / DROPPED / LOCAL_DELIVERED are never re-offered: a terminal state must not
            # come back as an action the model could take.
            continue
        entry = {
            "share_id": _text(row.get("share_id"), 96),
            "state": _text(row.get("state"), 24),
            "title": _text(row.get("title"), 80),
            "head_version_id": _text(row.get("head_version_id"), 96),
            "updated_at": _text(row.get("updated_at"), 40),
            "delivered": bool(row.get("delivered")),
            "source_artifact_id": _text((row.get("source") or {}).get("artifact_id"), 96),
        }
        entry.update(_share_detail(service, str(row.get("share_id") or "")))
        shares.append(entry)
    counts = data.get("counts") if isinstance(data.get("counts"), Mapping) else {}
    inbox = data.get("inbox") if isinstance(data.get("inbox"), Mapping) else {}
    return {
        "schema": SCHEMA,
        "status": "observed",
        "reason_code": "",
        "detail": "",
        # Both values are the owner's fixed constants, echoed so the model can say "my local inbox"
        # and not "telegram"/"email".
        "channel": _text(inbox.get("channel") or lss.CHANNEL_LOCAL_INBOX, 32),
        "audience": _text(inbox.get("audience") or lss.AUDIENCE_LOCAL_OWNER, 32),
        "shares": shares,
        "counts": {key: counts[key] for key in sorted(counts)
                   if isinstance(counts.get(key), int) and not isinstance(counts.get(key), bool)},
        "inbox": {
            "delivered_count": (inbox.get("delivered_count")
                                if isinstance(inbox.get("delivered_count"), int) else None),
            "local_only": True,
        },
        "truncated": bool(data.get("truncated")),
        "unknowns": [],
        # Stated once, in the block itself: a local inbox record is the only delivery this tree has.
        "note": "local_inbox only; no external channel is configured or enabled",
    }


def _receipt_facts(receipt: Any) -> Optional[dict[str, Any]]:
    if not isinstance(receipt, Mapping):
        return None
    return {
        "delivery_id": _text(receipt.get("delivery_id"), 64),
        "delivered_at": _text(receipt.get("delivered_at"), 40),
        "channel": _text(receipt.get("channel"), 32),
        "recipient": _text(receipt.get("recipient"), 32),
        "local_only": bool(receipt.get("local_only")),
        "external_channel_sent": bool(receipt.get("external_channel_sent")),
        "external_delivery_confirmed": bool(receipt.get("external_delivery_confirmed")),
        "read_by_audience": bool(receipt.get("read_by_audience")),
        "understood": bool(receipt.get("understood")),
    }


def read_draft(service: Any, share_id: str) -> dict[str, Any]:
    """One share as the owner holds it *now*, bounded -- state, head, source currency, receipt."""

    if service is None:
        return {"ok": False, "status": "unavailable", "reason_code": REASON_NOT_WIRED,
                "share_id": _text(share_id, 96)}
    try:
        answer = service.read_share(share_id, include_text=False)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "status": "unavailable", "reason_code": REASON_READ_FAILED,
                "detail": "%s: %s" % (type(error).__name__, _text(error, 120))[:160],
                "share_id": _text(share_id, 96)}
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
    share = data.get("share") if isinstance(data.get("share"), Mapping) else {}
    source = data.get("source_current") if isinstance(data.get("source_current"), Mapping) else {}
    return {
        "ok": bool(answer.get("ok")),
        "status": _text(answer.get("status"), 24),
        "reason_code": _text(answer.get("reason_code"), 64),
        "share_id": _text(share.get("share_id") or share_id, 96),
        "state": _text(share.get("state"), 24),
        "head_version_id": _text(share.get("head_version_id"), 96),
        "delivered": bool(data.get("delivered")),
        # Whether the owner's own source read allows this draft's words at all (this read does
        # not request them).  False is a withheld source, never an empty draft.
        "text_available": (share.get("text_available")
                           if isinstance(share.get("text_available"), bool) else None),
        "source_current": {
            "readable": bool(source.get("readable")),
            "stale": bool(source.get("stale")),
            "matches_recorded": bool(source.get("matches_recorded")),
            "reason_code": _text(source.get("reason_code"), 64),
        },
        "receipt": _receipt_facts(data.get("receipt")),
    }


def prepare_draft(service: Any, *, source_artifact_id: str,
                  share_text: Optional[str] = None,
                  excerpt_chars: Optional[int] = None,
                  title: Optional[str] = None,
                  expires_after_s: Optional[int] = None,
                  operation_key: Optional[str] = None) -> dict[str, Any]:
    """Ask the owner for one DRAFT.  A draft is *not* sent; nothing here delivers anything."""

    if service is None:
        return report({"ok": False, "status": "unavailable", "reason_code": REASON_NOT_WIRED})
    kwargs: dict[str, Any] = {"source_artifact_id": source_artifact_id}
    if share_text is not None:
        kwargs["share_text"] = share_text
    if excerpt_chars is not None:
        kwargs["excerpt_chars"] = int(excerpt_chars)
    if title:
        kwargs["title"] = title
    if expires_after_s is not None:
        kwargs["expires_after_s"] = int(expires_after_s)
    if operation_key:
        kwargs["operation_key"] = operation_key
    try:
        return report(service.prepare_share(**kwargs))
    except Exception as error:  # noqa: BLE001 - a refused contract is reported, never retried blind
        return report({"ok": False, "status": "refused",
                       "reason_code": "%s: %s" % (type(error).__name__, _text(error, 96))[:128]})


def apply_request(service: Any, *, share_id: str, action: str, expected_head: str,
                  defer_condition: Optional[str] = None,
                  opportunity_ref: Optional[str] = None,
                  reason: Optional[str] = None,
                  operation_key: Optional[str] = None) -> dict[str, Any]:
    """One owner action (``send_local`` / ``defer`` / ``drop``) with the draft's own CAS head."""

    if service is None:
        return report({"ok": False, "status": "unavailable", "reason_code": REASON_NOT_WIRED})
    kwargs: dict[str, Any] = {"share_id": share_id, "action": action,
                              "expected_head": expected_head}
    if defer_condition:
        kwargs["defer_condition"] = defer_condition
    if opportunity_ref:
        kwargs["opportunity_ref"] = opportunity_ref
    if reason:
        kwargs["reason"] = reason
    if operation_key:
        kwargs["operation_key"] = operation_key
    try:
        return report(service.apply_action(**kwargs))
    except Exception as error:  # noqa: BLE001
        return report({"ok": False, "status": "refused",
                       "reason_code": "%s: %s" % (type(error).__name__, _text(error, 96))[:128]})


def report(answer: Any, *, share_id: Optional[str] = None) -> dict[str, Any]:
    """The bounded, log-safe projection of one owner answer.

    ``delivered`` here means exactly ``LOCAL_DELIVERED``: one artifact version in this instance's
    local inbox.  The three "external" bits are copied from the owner's own receipt and are always
    false in this tree; if a future owner ever set them, this projection would show it instead of
    hiding it.
    """

    if not isinstance(answer, Mapping):
        return {"ok": False, "status": "unknown", "reason_code": "SHARING_ANSWER_NOT_A_MAPPING",
                "share_id": share_id}
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
    status = _text(answer.get("status"), 24)
    state = _text(data.get("state") or answer.get("state"), 24)
    receipt = _receipt_facts(data.get("receipt"))
    return {
        "ok": bool(answer.get("ok")),
        "status": status,
        "reason_code": _text(answer.get("reason_code"), 64),
        "share_id": _text(data.get("share_id") or answer.get("share_id") or share_id, 96),
        "state": state,
        "previous_state": _text(data.get("previous_state"), 24),
        "head_version_id": _text(data.get("head_version_id"), 96),
        "share_revision": data.get("share_revision"),
        # A *local* delivery only -- never "sent to a person".
        # A *local* delivery only.  ``delivered`` says "this call really left the draft in the
        # local inbox" (an ok, applied send_local), never "the share is delivered somewhere else";
        # ``state_delivered`` is the share's own state fact, which stays true after later reads.
        "delivered": bool(answer.get("ok")) and status in (lss.STATUS_APPLIED,
                                                           lss.STATUS_ALREADY_APPLIED)
                     and state == lss.STATE_LOCAL_DELIVERED,
        "state_delivered": state == lss.STATE_LOCAL_DELIVERED,
        "delivery_count": data.get("delivery_count"),
        "receipt": receipt,
        "deferred": (dict(data.get("deferred")) if isinstance(data.get("deferred"), Mapping)
                     else None),
        "dropped": (dict(data.get("dropped")) if isinstance(data.get("dropped"), Mapping)
                    else None),
        "reconciled": bool(answer.get("reconciled")),
        "retryable": bool(answer.get("retryable")),
        "source_current": (dict(data["source_current"])
                           if isinstance(data.get("source_current"), Mapping) else None),
        "truncated": bool(data.get("truncated")),
    }


def is_share_title(title: Any) -> bool:
    """B's own marker for "this artifact is a share", re-exported so A filters with the same rule."""

    return lss.is_share_title(title)


def open_share_ids(block: Mapping[str, Any]) -> Sequence[str]:
    """The share ids a block offered -- what a decision may name, and nothing else."""

    return [str(row.get("share_id")) for row in (block.get("shares") or ())
            if isinstance(row, Mapping) and row.get("share_id")]
