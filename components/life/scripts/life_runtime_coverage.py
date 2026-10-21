#!/usr/bin/env python3
"""v0.4 R: proven runtime coverage, kept apart from the wall span and from what is unknown.

Why this module exists
----------------------
``resident_summary.json`` used to report ``observed_seconds = now - first_start``: a *wall*
span that silently counts every hour the process was down as an hour of life.  Two processes,
one crash and one reboot all look the same from the wall clock, so that number could reach
86400 without the resident ever having been up for a day.  The record therefore needs four
different quantities, and none of them may be derived from another:

* ``wall_span_seconds`` -- first recorded start to now (or to the last close): the calendar
  span.  It includes downtime and says nothing about life.
* ``process_online_seconds`` / ``covered_seconds`` -- the sum of each process's *own*
  ``time.monotonic()`` measurement.  A monotonic clock is per process and per boot, so it is
  never added across a restart and never used to bridge a gap: the covered seconds are what
  some process actually measured itself alive for.  The same seconds are split into
  ``closed_process_seconds`` (measured *and* closed) and ``open_tail_seconds`` (measured but
  never closed), because only the first is an interval that was handed over.
* ``offline_gap_seconds`` -- the measured distance between one process's close and the next
  process's start, read from wall timestamps.
* ``overlap_seconds`` / ``time_ambiguity`` -- the wall base itself disagrees: the next process
  started *before* this one stopped, or the wall span is shorter than the measured coverage, or
  the span went backwards (``wall_rollback``).  A real measurement is never reduced for it, but
  an overlap is neither downtime nor continuity: two records cannot both be right about that
  interval, so ``continuous`` is false and the label stays ``RUNNING``.
* ``unknown_seconds`` -- everything the ledger cannot prove: an interval whose process never
  closed (a crash), a process that recorded no monotonic measurement, and a legacy summary that
  only ever had a wall span.  Unknown is reported as unknown; it is never folded into coverage
  and it never becomes 0 health.  Each hole is recorded once: two reasons name two *different*
  parts of it (what a row could speak for, and what came after its last heartbeat), so the
  ``unknown`` entries never overlap and never double-count.

``coverage_complete`` is true only when every interval is *accounted for*: every process closed
by a real stop, every one measured, nothing left unknown.  A measured offline gap does not make
the accounting incomplete -- it is a known hole -- so ``continuous`` says whether the covered
time is unbroken as well, and it is false whenever the wall base is ambiguous.  Nothing here
mints an acceptance label: ``soak_verified`` is always ``NOT_VERIFIED``, because "she really
lived a day" is a reviewer's judgement over the journal and the owners' results, not arithmetic
over two clocks.

The module is pure: no I/O, no environment, no clock of its own.  Every instant is handed in,
so a test can place two processes a year apart without waiting a second.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

#: What the persisted ledger and the derived view say they are.
KIND = "life_runtime_coverage"
SCHEMA_VERSION = 1

#: How a process record ended.  ``UNCLOSED`` is not an error to be repaired: it is the
#: evidence that this process never reached its own stop, and its tail stays unknown forever.
CLOSED_STOP = "STOP"
CLOSED_UNCLOSED = "UNCLOSED"
CLOSED_CRASH = "CRASH"

#: Where an interval's online seconds came from.  Only the first counts as coverage.
SOURCE_MONOTONIC = "MONOTONIC_THIS_PROCESS"
SOURCE_UNKNOWN = "UNKNOWN"

REASON_NO_MEASUREMENT = "NO_MONOTONIC_MEASUREMENT"
REASON_UNCLOSED = "UNCLOSED_INTERVAL"
REASON_LEGACY = "LEGACY_SUMMARY_WALL_SPAN"

SOAK_24H_SECONDS = 24 * 3600
SOAK_7D_SECONDS = 7 * 24 * 3600

#: This module can never verify a soak; the label is named so a reader cannot mistake it.
SOAK_VERIFIED = "NOT_VERIFIED"
SOAK_BASIS = "PROVEN_COVERAGE_SECONDS"


def _aware(value: Any) -> Optional[datetime]:
    """One instant, always timezone-aware.  A naive timestamp is read as UTC, never local."""

    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _span(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[int]:
    if later is None or earlier is None:
        return None
    return int((later - earlier).total_seconds())


def _measured(value: Any) -> Optional[int]:
    """A non-negative integer measurement, or None.  A bool is not a measurement."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value) if value >= 0 else None


def process_identity(*, pid: Any, started_at: Any, host: Optional[str] = None) -> str:
    """One process, identified by (host, pid, its own start instant).

    Two processes over one state root are two different lives, and a restarted process can be
    handed the same pid, so the start instant is part of the identity.  The wall start is used
    because it is also what the ledger persists; a monotonic value would not survive the
    process that produced it.
    """

    return ":".join([str(host or "local"),
                     str(pid if pid is not None else "pid-unknown"),
                     str(started_at or "start-unknown")])


def new_process_record(*, process_id: str, pid: Any, started_at: Any,
                       monotonic_started: Optional[float] = None) -> dict[str, Any]:
    """A process record as it is written the moment the process starts.

    ``online_seconds`` is None on purpose: a process that has not measured itself yet has no
    coverage to claim, and 0 would read as "it was up for no time" instead of "not known yet".
    """

    moment = str(started_at or "")
    return {
        "process_id": str(process_id),
        "pid": pid,
        "started_at": moment,
        "stopped_at": None,
        "last_heartbeat_at": moment,
        "online_seconds": None,
        "online_seconds_source": SOURCE_UNKNOWN,
        "monotonic_started": (None if monotonic_started is None else float(monotonic_started)),
        "closed": CLOSED_UNCLOSED,
    }


def heartbeat(record: Mapping[str, Any], *, at: Any,
              online_seconds: Optional[int]) -> dict[str, Any]:
    """The same record, with this process's own measurement refreshed.  Never closes it."""

    row = dict(record)
    if at is not None:
        row["last_heartbeat_at"] = str(at)
    measured = _measured(online_seconds)
    if measured is not None:
        row["online_seconds"] = measured
        row["online_seconds_source"] = SOURCE_MONOTONIC
    return row


def close_process_record(record: Mapping[str, Any], *, closed_at: Any,
                         online_seconds: Optional[int],
                         closed: str = CLOSED_STOP) -> dict[str, Any]:
    """The same record, closed by a real stop.  ``STOP`` is the only closure that proves life."""

    row = dict(record)
    moment = str(closed_at or "")
    row["stopped_at"] = moment or row.get("stopped_at")
    row["closed"] = str(closed)
    if moment:
        row["last_heartbeat_at"] = moment
    measured = _measured(online_seconds)
    if measured is not None:
        row["online_seconds"] = measured
        row["online_seconds_source"] = SOURCE_MONOTONIC
    return row


def legacy_summary_seconds(summary: Optional[Mapping[str, Any]]) -> int:
    """An old summary's wall span: *not* coverage, and reported as unknown.

    A summary written before the process ledger existed has only ``observed_seconds`` (a wall
    span) and no per-process rows.  Reading it back as coverage would be exactly the lie this
    module removes, so the number is returned here and the caller records it under
    ``unknown_seconds``.  A summary that already carries a coverage view returns 0: it is not
    a legacy record and its span is already accounted for by its own processes.
    """

    if not isinstance(summary, Mapping):
        return 0
    if isinstance(summary.get("coverage"), Mapping):
        return 0
    try:
        return max(0, int(summary.get("observed_seconds") or 0))
    except (TypeError, ValueError):
        return 0


def _record_unknown(entries: list[dict[str, Any]], process_id: Any,
                    seconds: Optional[int], reason: str) -> int:
    """One unknown interval, recorded once, and the seconds the caller must add.

    Entry and total come from the same call, so an interval cannot be counted twice by two
    reasons: each reason names a *different* part of the hole (the part a row could speak for
    and the part after its last heartbeat), and a zero-second part is not information.
    """

    if seconds is None:
        entries.append({"process_id": process_id, "seconds": None, "reason": reason})
        return 0
    if seconds > 0:
        entries.append({"process_id": process_id, "seconds": int(seconds), "reason": reason})
        return int(seconds)
    return 0


def coverage(processes: Iterable[Mapping[str, Any]], *, now: Any = None,
             legacy_seconds: int = 0,
             current_process_id: Optional[str] = None) -> dict[str, Any]:
    """The four quantities, from the recorded processes only.

    ``processes`` is the ledger's append order, which *is* the real start order; the rows are
    never sorted, because a wall rollback would reorder them and invent a sequence that never
    happened.  ``now`` is the current instant and is only needed for a running process (whose
    open tail is covered by its own monotonic measurement) or for the wall span.
    """

    rows = [dict(row) for row in (processes or []) if isinstance(row, Mapping)]
    moment = _aware(now)
    covered = 0
    #: The same covered seconds, split by whether the interval they belong to ever *closed*.
    #: ``closed_process_seconds`` is time a process measured and then stopped for; an open
    #: tail is a measurement without a closure, so it proves the process was up for that long
    #: but not that the interval was handed over.  A reader that only needs "proven online
    #: seconds" reads ``covered``; a reader deciding what a *day* is reads both.
    closed_seconds = 0
    open_tail_seconds = 0
    offline = 0
    overlap = 0
    unknown = 0
    gaps: list[dict[str, Any]] = []
    unknown_entries: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        process_id = row.get("process_id")
        started = _aware(row.get("started_at"))
        stopped = _aware(row.get("stopped_at"))
        closed = str(row.get("closed") or CLOSED_UNCLOSED)
        unclosed = closed != CLOSED_STOP
        following = _aware(rows[index + 1].get("started_at")) if index + 1 < len(rows) else None
        is_current = bool(current_process_id) and process_id == current_process_id
        heartbeat_at = _aware(row.get("last_heartbeat_at")) or started

        measured = _measured(row.get("online_seconds"))
        if measured is not None:
            # A real measurement is coverage, whatever else is wrong with the record: it is not
            # reduced because a later row disagrees with it about wall time.
            covered += measured
            if closed == CLOSED_STOP:
                closed_seconds += measured
            else:
                open_tail_seconds += measured
        else:
            # No measurement: the wall span of the part this row can even speak for is the most
            # that can be said, and it is unknown -- never 0 health.  For a process that never
            # closed that part ends at its last heartbeat; the rest is the unclosed hole below,
            # so the same seconds are never counted twice.
            span = _span(moment if is_current else heartbeat_at, started) if unclosed \
                else _span(stopped, started)
            unknown += _record_unknown(unknown_entries, process_id, span,
                                       REASON_NO_MEASUREMENT)

        if unclosed and not is_current:
            # An unclosed process did not hand over to the next one: the distance from its last
            # heartbeat to whatever came next (or to now) is a hole, not life.
            edge = following or moment
            unknown += _record_unknown(unknown_entries, process_id,
                                       _span(edge, heartbeat_at), REASON_UNCLOSED)

        if following is not None and not unclosed:
            gap = _span(following, stopped)
            if gap is not None and gap > 0:
                offline += gap
                gaps.append({"after_process": process_id,
                             "before_process": rows[index + 1].get("process_id"),
                             "seconds": gap, "kind": "OFFLINE", "ambiguous": False})
            elif gap is not None and gap < 0:
                # The next process started *before* this one stopped.  Two records cannot both
                # be right about that wall interval, so this is named as time ambiguity: it is
                # not downtime, and it is not continuity either.  The measured seconds stand --
                # a real monotonic measurement is not made smaller by a clock disagreement.
                overlap += -gap
                gaps.append({"after_process": process_id,
                             "before_process": rows[index + 1].get("process_id"),
                             "seconds": -gap, "kind": "OVERLAP", "ambiguous": True})
        # When this row is unclosed the hole is already counted above, never twice here.

    legacy = max(0, int(legacy_seconds or 0))
    if legacy:
        unknown += legacy
        unknown_entries.append({"process_id": None, "seconds": legacy, "reason": REASON_LEGACY})

    wall_span = 0
    wall_rollback = False
    if rows:
        first = _aware(rows[0].get("started_at"))
        tail_row = rows[-1]
        tail_closed = str(tail_row.get("closed") or CLOSED_UNCLOSED)
        if tail_closed == CLOSED_STOP:
            last_edge = (_aware(tail_row.get("stopped_at"))
                         or _aware(tail_row.get("last_heartbeat_at")))
        else:
            last_edge = moment
        raw = _span(last_edge, first)
        wall_rollback = raw is not None and raw < 0
        wall_span = max(0, raw or 0)

    offline_gaps = [gap for gap in gaps if gap.get("kind") == "OFFLINE"]
    complete = bool(rows) and not unknown_entries and unknown == 0 and all(
        str(row.get("closed") or CLOSED_UNCLOSED) == CLOSED_STOP for row in rows)
    # Coverage cannot exceed the wall span it happened in.  When it does -- an overlap, a clock
    # that moved, or two records that disagree -- the wall ordering is not trustworthy, and that
    # is a separate fact from "a process was down".  A real measurement is never reduced for it.
    covered_exceeds_wall = bool(rows) and covered > wall_span
    time_ambiguity = bool(wall_rollback or overlap > 0 or covered_exceeds_wall)
    # A measured gap between two closed processes is a known hole, so the accounting is still
    # complete; ``continuous`` is what says the covered time had no break in it -- and an
    # ambiguous time base is not a continuous day either.
    continuous = complete and not offline_gaps and not time_ambiguity

    return {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "wall_span_seconds": wall_span,
        "process_online_seconds": covered,
        "covered_seconds": covered,
        "closed_process_seconds": closed_seconds,
        "open_tail_seconds": open_tail_seconds,
        "offline_gap_seconds": offline,
        "overlap_seconds": overlap,
        "unknown_seconds": unknown,
        "coverage_complete": complete,
        "continuous": continuous,
        "wall_rollback": wall_rollback,
        "covered_exceeds_wall_span": covered_exceeds_wall,
        "time_ambiguity": time_ambiguity,
        "process_count": len(rows),
        "processes": [{"process_id": row.get("process_id"), "pid": row.get("pid"),
                       "started_at": row.get("started_at"),
                       "last_heartbeat_at": row.get("last_heartbeat_at"),
                       "stopped_at": row.get("stopped_at"),
                       "closed": str(row.get("closed") or CLOSED_UNCLOSED),
                       "online_seconds": _measured(row.get("online_seconds")),
                       "online_seconds_source": row.get("online_seconds_source")
                       or SOURCE_UNKNOWN}
                      for row in rows],
        "gaps": gaps,
        "unknown": unknown_entries,
        "sources": {
            "clock": "MONOTONIC_PER_PROCESS",
            "gap_clock": "WALL_TIMESTAMPS",
            "ledger": "PRESENT" if rows else "ABSENT",
            "legacy_summary": ("PRESENT_UNPROVEN" if legacy else "ABSENT"),
        },
        "soak_verified": SOAK_VERIFIED,
        "soak_basis": SOAK_BASIS,
    }


def soak_labels(view: Mapping[str, Any]) -> dict[str, str]:
    """The two threshold labels, from *covered* seconds, and only when it is continuous.

    The vocabulary is the one the record already used, so an old reader keeps working; what
    changed is the input.  A reached threshold with a hole in the coverage stays ``RUNNING``:
    a wall span that crossed 24h is not a day of life, and neither is 24 measured hours broken
    by a stop.
    """

    covered = _measured((view or {}).get("covered_seconds")) or 0
    continuous = bool((view or {}).get("continuous"))
    reached_24h = covered >= SOAK_24H_SECONDS and continuous
    reached_7d = covered >= SOAK_7D_SECONDS and continuous
    return {
        "soak_24h": ("SOAK_THRESHOLD_REACHED_24H" if reached_24h else "SOAK_RUNNING_24H"),
        "soak_7d": ("SOAK_THRESHOLD_REACHED_7D" if reached_7d
                    else "SOAK_RUNNING_7D" if covered >= SOAK_24H_SECONDS
                    else "SOAK_PENDING_7D"),
        "soak_basis": SOAK_BASIS,
        "soak_verified": SOAK_VERIFIED,
    }


__all__ = [
    "CLOSED_CRASH",
    "CLOSED_STOP",
    "CLOSED_UNCLOSED",
    "KIND",
    "REASON_LEGACY",
    "REASON_NO_MEASUREMENT",
    "REASON_UNCLOSED",
    "SCHEMA_VERSION",
    "SOAK_24H_SECONDS",
    "SOAK_7D_SECONDS",
    "SOAK_BASIS",
    "SOAK_VERIFIED",
    "SOURCE_MONOTONIC",
    "SOURCE_UNKNOWN",
    "close_process_record",
    "coverage",
    "heartbeat",
    "legacy_summary_seconds",
    "new_process_record",
    "process_identity",
    "soak_labels",
]
