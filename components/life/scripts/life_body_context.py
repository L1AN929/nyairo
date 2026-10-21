#!/usr/bin/env python3
"""v0.4 body participation: the bounded, sourced *body situation* a decision may read.

What this module is
-------------------
A pure read projection.  It takes the body column A's situation view already obtained
(``life_situation_view``'s ``body`` column, itself one ``body_capacity_source`` read over the
real ``WorldBodyReadPort.body()``) plus the World owner's own ``REST`` entry from the same
observation, and compiles it into the small qualitative block a decision model can actually
understand:

* which burden signals the Body owner really reported (type + qualitative band only),
* whether a recovery **session** is open right now,
* whether resting is possible *now* and, if not, the owner's own reason,
* what could **not** be read (named, never filled), and
* an explicit statement that this block is not a Candidate, not a permission and not a claim
  that she recovered.

What it deliberately is not
---------------------------
* **Not a second body read.**  It never touches the port: the caller passes the column it
  already has, so a round costs no extra owner call and two rounds cannot disagree.
* **No numbers.**  No fatigue value, no threshold, no seconds, no score.  The Body gateway
  exposes bands only and this module keeps it that way (``raw_values_exposed: false``).
* **No budget.**  The request budget is a different dimension (``life_situation_view``'s
  ``resource_budget`` column); nothing here reads, carries or renames it.
* **No Candidate, no grant.**  Nothing here admits an Activity, authorises a verb or picks a
  target.  It is prompt data.
* **No "healthy" inference.**  An unreadable body, or a readable body with no burden signal,
  is ``UNKNOWN`` with a named reason -- never "fine", never ``AVAILABLE``.
* **A session is not recovery.**  ``REST`` start/stop is a World session; ``recovered`` is a
  Body measurement this tree does not expose as a positive fact.  So ``recovery.claim`` is
  always ``NOT_ASSERTED`` and ``recovery.progress`` is ``NOT_READ`` unless a caller supplies a
  real progress band.  This is the rule the 2026-10-21 body/rest note settled
  (``command_success_is_not_recovery``) applied to the prompt.

Where each fact comes from
--------------------------
``capacity`` / ``burden`` / ``easing`` / ``observed_at`` / ``revision`` / ``freshness``
    the ``life_situation_view`` body column (owner: ``body_capacity_source`` ->
    ``world_body_read_port.get_body_signals``).
``session`` / ``can_start`` / ``can_stop`` / ``sleep_like`` / ``why_not`` / ``pose``
    the World owner's own ``REST`` entry (``world_body_gateway_api`` ``_rest_availability``),
    which is the only surface that publishes the session state.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

import body_capacity_source as body_capacity  # noqa: E402  (the capacity owner's vocabulary)

__all__ = [
    "CAPACITY_UNKNOWN",
    "MAX_BURDEN_ENTRIES",
    "MAX_EASING_ENTRIES",
    "RECOVERY_CLAIM_NOT_ASSERTED",
    "RECOVERY_PROGRESS_NOT_READ",
    "RECOVERY_SESSION_CLOSED",
    "RECOVERY_SESSION_OPEN",
    "RECOVERY_SESSION_UNKNOWN",
    "REASON_BODY_COLUMN_ABSENT",
    "REASON_BODY_COLUMN_INVALID",
    "REASON_BODY_VIEW_UNBOUND",
    "REASON_OK_OBSERVED",
    "REASON_OK_PARTIAL",
    "REASON_OK_UNKNOWN",
    "SCHEMA",
    "SOURCE_BODY_READ",
    "SOURCE_REST_ENTRY",
    "STATUS_OBSERVED",
    "STATUS_PARTIAL",
    "STATUS_UNKNOWN",
    "UNKNOWN_BURDEN",
    "UNKNOWN_BURDEN_IS_NOT_HEALTH",
    "UNKNOWN_PROGRESS",
    "UNKNOWN_RECOVERY_SESSION",
    "UNKNOWN_REST_CAPABILITY",
    "build",
    "empty_context",
]

SCHEMA = "chiyo.life.body_context.v1"

SOURCE_BODY_READ = "world_body_read_port.get_body_signals"
SOURCE_REST_ENTRY = "world_body_gateway_api.get_available_actions:REST"

STATUS_OBSERVED = "observed"
STATUS_PARTIAL = "partial"
STATUS_UNKNOWN = "unknown"

#: Mirrored from the capacity owner (never re-invented here).
CAPACITY_UNKNOWN = body_capacity.CAPACITY_UNKNOWN
CAPACITY_LIMITED = body_capacity.CAPACITY_LIMITED

RECOVERY_SESSION_OPEN = "OPEN"
RECOVERY_SESSION_CLOSED = "CLOSED"
RECOVERY_SESSION_UNKNOWN = "UNKNOWN"

#: ``REST`` start/stop is a session command.  It is never evidence that a body recovered.
RECOVERY_CLAIM_NOT_ASSERTED = "NOT_ASSERTED"
#: The Body gateway publishes ``recovery.progress_band``, but the capacity column does not
#: carry it; rather than guess, say so.
RECOVERY_PROGRESS_NOT_READ = "NOT_READ"

#: The body owner's own ``recovery.state`` value that means "a session is open".
_RECOVERY_STATE_ACTIVE = "active"
_RECOVERY_STATE_INACTIVE = "inactive"

REASON_OK_OBSERVED = "OK_BODY_OBSERVED"
REASON_OK_PARTIAL = "OK_BODY_PARTIAL"
REASON_OK_UNKNOWN = "OK_BODY_UNKNOWN"
#: The owner's other closed recovery states: a session that ended (``completed``) or one broken
#: by real load (``interrupted``).  Both mean "no session open"; neither means "recovered".
_RECOVERY_STATES_CLOSED = frozenset({"inactive", "completed", "interrupted"})
REASON_BODY_COLUMN_ABSENT = "BODY_SITUATION_NOT_READ"
REASON_BODY_COLUMN_INVALID = "BODY_SITUATION_COLUMN_INVALID"
REASON_BODY_VIEW_UNBOUND = "BODY_VIEW_UNBOUND"

UNKNOWN_BURDEN = "BODY_BURDEN_NOT_READ"
UNKNOWN_BURDEN_IS_NOT_HEALTH = "NO_BURDEN_SIGNAL_IS_NOT_HEALTH"
UNKNOWN_RECOVERY_SESSION = "BODY_RECOVERY_SESSION_NOT_READ"
UNKNOWN_PROGRESS = "BODY_RECOVERY_PROGRESS_NOT_READ"
UNKNOWN_REST_CAPABILITY = "BODY_REST_CAPABILITY_NOT_READ"

#: How much of one signal list may enter a prompt.  Counts only -- bands are short enums.
MAX_BURDEN_ENTRIES = 4
MAX_EASING_ENTRIES = 3


def _text(value: Any, limit: int = 48) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


#: Non-burden vocabulary split: only the three *trend* signals are "easing".  The two thermal
#: signals (`warmth_high` / `cold_tendency`) say nothing about capability or improvement, so they
#: are not carried at all rather than being renamed into something they are not.
_THERMAL_SIGNAL_TYPES = frozenset({"warmth_high", "cold_tendency"})
EASING_SIGNAL_TYPES = frozenset(
    t for t in body_capacity.NON_BURDEN_SIGNAL_TYPES if t not in _THERMAL_SIGNAL_TYPES
)


def _signal_rows(evidence: Any, wanted: Sequence[str], limit: int) -> list[dict[str, Any]]:
    """``["fatigue_noticeable:noticeable", ...]`` -> bounded ``{signal, band}`` rows."""

    rows: list[dict[str, Any]] = []
    seen: set = set()
    for raw in list(evidence or ()):
        text = _text(raw, 80)
        if not text or ":" not in text:
            continue
        signal, band = text.split(":", 1)
        if signal not in wanted or not band or (signal, band) in seen:
            continue
        seen.add((signal, band))
        rows.append({"signal": signal, "band": band})
        if len(rows) >= limit:
            break
    return rows

def _session_from_rest(
    rest: Optional[Mapping[str, Any]],
) -> tuple[str, Optional[str], list[str]]:
    """The recovery **session** verdict, from the World REST entry only.

    The Body runtime's own closed vocabulary (``body_runtime.RECOVERY_STATES``) is
    ``inactive`` / ``active`` / ``interrupted`` / ``completed``.  Only ``active`` means a session
    is open; ``completed`` is a session that *ended* and ``interrupted`` one broken by real load.
    None of the three is "recovered", so the owner's own word is echoed as ``state`` and the
    claim stays ``NOT_ASSERTED``.
    """

    if not isinstance(rest, Mapping):
        return RECOVERY_SESSION_UNKNOWN, None, [UNKNOWN_RECOVERY_SESSION]
    current = rest.get("current")
    if not isinstance(current, Mapping):
        return RECOVERY_SESSION_UNKNOWN, None, [UNKNOWN_RECOVERY_SESSION]
    return _session_from_state(current.get("recovery_state"))


def _session_from_state(value: Any) -> tuple[str, Optional[str], list[str]]:
    """One owner recovery word -> (session verdict, echoed word, named unknowns)."""

    state = _text(value, 24).lower() or None
    if state == _RECOVERY_STATE_ACTIVE:
        return RECOVERY_SESSION_OPEN, state, []
    if state in _RECOVERY_STATES_CLOSED:
        return RECOVERY_SESSION_CLOSED, state, []
    return RECOVERY_SESSION_UNKNOWN, state, [UNKNOWN_RECOVERY_SESSION]


def _rest_block(rest: Optional[Mapping[str, Any]],
                unknowns: list[str]) -> dict[str, Any]:
    """What the owner says about resting *now* -- capability facts, never an instruction."""

    if not isinstance(rest, Mapping):
        unknowns.append(UNKNOWN_REST_CAPABILITY)
        return {
            "can_start": None,
            "can_stop": None,
            "sleep_like": None,
            "why_not": None,
            "pose": None,
            "source": SOURCE_REST_ENTRY,
        }
    current = rest.get("current") if isinstance(rest.get("current"), Mapping) else {}
    why_not = None
    if rest.get("start_available") is not True:
        why_not = _text(rest.get("start_blocked_reason") or rest.get("blocked_reason"), 48) or None
    return {
        "can_start": None if rest.get("start_available") is None
                    else bool(rest.get("start_available")),
        "can_stop": None if rest.get("stop_available") is None
                   else bool(rest.get("stop_available")),
        "sleep_like": None if rest.get("sleep_like_available") is None
                      else bool(rest.get("sleep_like_available")),
        "why_not": why_not,
        "pose": _text(current.get("pose"), 24) or None,
        "rest_surface": bool(current.get("rest_surface")) if "rest_surface" in current else None,
        "travelling": None if current.get("travelling") is None
                     else bool(current.get("travelling")),
        "source": SOURCE_REST_ENTRY,
    }


def empty_context(reason_code: str, *, detail: str = "") -> dict[str, Any]:
    """The honest absence: no capacity, no session, no capability -- and the reason."""

    return {
        "schema": SCHEMA,
        "status": STATUS_UNKNOWN,
        "reason_code": str(reason_code),
        "detail": _text(detail, 160),
        "source": SOURCE_BODY_READ,
        "observed_at": None,
        "body_id": None,
        "revision": None,
        "freshness": None,
        "capacity": CAPACITY_UNKNOWN,
        "burden": [],
        "easing": [],
        "recovery": {
            "session": RECOVERY_SESSION_UNKNOWN,
            "state": None,
            "claim": RECOVERY_CLAIM_NOT_ASSERTED,
            "progress": RECOVERY_PROGRESS_NOT_READ,
        },
        "rest": {
            "can_start": None,
            "can_stop": None,
            "sleep_like": None,
            "why_not": None,
            "pose": None,
            "source": SOURCE_REST_ENTRY,
        },
        "unknowns": [str(reason_code)],
        "limits": {
            "raw_values_exposed": False,
            "numbers_exposed": False,
            "reads_performed": 0,
            "max_burden": MAX_BURDEN_ENTRIES,
            "max_easing": MAX_EASING_ENTRIES,
        },
        "not_authority": {
            "candidate": False,
            "action": False,
            "grant": False,
            "instruction": False,
        },
    }


def build(
    *,
    body_column: Optional[Mapping[str, Any]] = None,
    rest: Optional[Mapping[str, Any]] = None,
    recovery: Optional[Mapping[str, Any]] = None,
    observed_at: Optional[str] = None,
) -> dict[str, Any]:
    """Compile the body column A already read into the bounded body-situation block.

    ``body_column`` is ``life_situation_view``'s ``body`` column (or any mapping carrying the
    same owner facts); ``rest`` is the World owner's ``REST`` entry; ``recovery`` is the owner's own
    recovery block from the same body read (``state`` / ``mode`` / ``trend`` / ``progress_band``)
    when the caller could obtain it.  Missing input is a named ``UNKNOWN`` with zero reads -- never
    a healthy body and never a resting one.

    ``recovery.progress`` is the owner's own qualitative band when it was read, otherwise
    ``NOT_READ`` with that reason in ``unknowns``.  A band is progress of a *session*, not a claim
    that she recovered: ``claim`` stays ``NOT_ASSERTED`` either way.
    """

    if not isinstance(body_column, Mapping) or not body_column:
        return empty_context(REASON_BODY_COLUMN_ABSENT,
                             detail="this round read no body situation")

    #: Two real shapes reach this function and both are the *same* facts: A's situation view
    #: column spells them ``state`` / ``ref`` (owner: ``life_situation_view._column``), the raw
    #: capacity adapter spells them ``body_capacity_state`` / ``body_capacity_ref``.  Never guess
    #: a third spelling and never re-derive the state.
    capacity = body_column.get("state", body_column.get("body_capacity_state"))
    capacity_ref = body_column.get("ref", body_column.get("body_capacity_ref"))
    if capacity not in body_capacity.VALID_CAPACITY_STATES:
        return empty_context(REASON_BODY_COLUMN_INVALID,
                             detail="the body column carries no capacity state")

    unknowns: list[str] = []
    evidence = list(body_column.get("evidence") or [])
    burden = _signal_rows(evidence, body_capacity.BURDEN_SIGNAL_TYPES, MAX_BURDEN_ENTRIES)
    easing = _signal_rows(evidence, EASING_SIGNAL_TYPES, MAX_EASING_ENTRIES)
    if not burden:
        # No burden signal is an unread/quiet body, not a healthy one.
        unknowns.append(UNKNOWN_BURDEN_IS_NOT_HEALTH)

    session, recovery_state, session_unknowns = _session_from_rest(rest)
    unknowns.extend(session_unknowns)
    rest_block = _rest_block(rest, unknowns)
    # The owner's own recovery block, when the caller could read it from the same body read:
    # state/mode/trend/progress_band.  It refines the session word and the progress band; it never
    # becomes a capability number and it never becomes a recovery claim.
    owner_recovery = recovery if isinstance(recovery, Mapping) else {}
    if owner_recovery:
        recovery_state = (_text(owner_recovery.get("state"), 24).lower() or recovery_state)
        # The owner's session word wins over the REST echo: both are its own, this one is newer.
        session, recovery_state, session_unknowns = _session_from_state(recovery_state)
    progress = _text(owner_recovery.get("progress_band"), 24) or RECOVERY_PROGRESS_NOT_READ
    trend = _text(owner_recovery.get("trend"), 24) or None
    if progress == RECOVERY_PROGRESS_NOT_READ:
        unknowns.append(UNKNOWN_PROGRESS)

    identity_bound = bool(body_column.get("identity_bound"))
    revision_bound = bool(body_column.get("revision_bound"))
    time_bound = bool(body_column.get("observation_time_bound"))
    if not (identity_bound and revision_bound and time_bound):
        unknowns.append(REASON_BODY_VIEW_UNBOUND)

    capacity_known = capacity != CAPACITY_UNKNOWN
    if capacity_known and session != RECOVERY_SESSION_UNKNOWN and rest_block["can_start"] is not None:
        status, reason = STATUS_OBSERVED, REASON_OK_OBSERVED
    elif capacity_known or session != RECOVERY_SESSION_UNKNOWN:
        status, reason = STATUS_PARTIAL, REASON_OK_PARTIAL
    else:
        status, reason = STATUS_UNKNOWN, REASON_OK_UNKNOWN

    seen: set = set()
    unique_unknowns: list[str] = []
    for code in unknowns:
        if code and code not in seen:
            seen.add(code)
            unique_unknowns.append(code)

    return {
        "schema": SCHEMA,
        "status": status,
        "reason_code": reason,
        "detail": _text(body_column.get("reason_code"), 160),
        "source": body_column.get("source") or SOURCE_BODY_READ,
        "observed_at": _text(body_column.get("observed_at") or observed_at, 40) or None,
        "body_id": _text(body_column.get("observed_body_id"), 64) or None,
        "revision": _text(body_column.get("observed_revision"), 80) or None,
        "freshness": _text(body_column.get("freshness"), 24) or None,
        "capacity": capacity,
        "capacity_ref": _text(capacity_ref, 96) or None,
        # Qualitative bands only: what the owner noticed, not a number.
        "burden": burden,
        # Improvements are *not* a recovery claim; the field name says so on purpose.
        "easing": easing,
        "recovery": {
            "session": session,
            #: Echo of the owner's own word (``active`` / ``completed`` / ``interrupted`` /
            #: ``inactive``) -- a session's state, never a recovery verdict.
            "state": recovery_state,
            "claim": RECOVERY_CLAIM_NOT_ASSERTED,
            #: The owner's own qualitative band for the session in progress (``mild`` /
            #: ``noticeable`` ...), or ``NOT_READ`` with that reason in ``unknowns``.
            "progress": progress,
            "trend": trend,
        },
        "rest": rest_block,
        "unknowns": unique_unknowns,
        "limits": {
            "raw_values_exposed": False,
            "numbers_exposed": False,
            "reads_performed": 0,
            "max_burden": MAX_BURDEN_ENTRIES,
            "max_easing": MAX_EASING_ENTRIES,
        },
        "not_authority": {
            "candidate": False,
            "action": False,
            "grant": False,
            "instruction": False,
        },
    }
