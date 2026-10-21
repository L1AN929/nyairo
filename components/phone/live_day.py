#!/usr/bin/env python3
"""v0.4 C: one operator command that lets one phone instance live one continuous segment.

Why this layer exists
---------------------
Every piece of her day already has an owner: ``AutonomousLifeResident`` owns the loop and the
writer lease, ``phone.demo.serve_instance`` owns the Supply/World children and the loopback
phone surface, ``phone.life.life_run`` maps *this phone instance's* live binding onto the
existing Life runtime and refuses honestly.  What was missing is the operator-shaped sentence
in between: "start this instance's life for at most N seconds / N ticks, then stop, and tell me
where it ended up".

So this module adds exactly that, and nothing else:

* :func:`run_segment` -- start (only when they are not already up) the parents' own owner
  children through :func:`components.phone.demo.serve_instance`, run the *existing* resident
  through :func:`components.phone.life.life_run` with a **finite** bound and an optional
  *lower-only* per-segment request cap, then always release: the resident's own ``stop()``
  releases the writer lease, and the children this process started are stopped in ``finally``
  -- on cancellation too.
* :func:`status` -- pure read.  No service is started, no database or table is created, no
  socket is bound: resident summary + wake journal + coverage ledger + the owner's own
  persisted state + the read-only World journey projection.  What cannot be read live is
  reported ``UNKNOWN`` with the reason, never guessed.
* :func:`stop` -- signal the resident this instance's own summary names (POSIX only; a hard
  ``TerminateProcess`` on Windows would not run its ``finally``, so it is refused there).
* :func:`segment_summary` -- the bounded operator summary: where she is now, what is in her
  hands, what she is doing, where the segment stopped, when the next consideration could
  happen, and what this segment actually changed.  Rebuilt from the live owners and the
  records the resident/world already keep; it is not a second LifeManager store, it never
  carries private text and it never writes.

What this module deliberately does not do
-----------------------------------------
* No ``prepare`` / reset / re-seed: the instance, its world, its run id and its request ledger
  are only read.  It never writes ``instance.json`` itself (``serve_instance`` still does what
  it always does for a new generation), never opens ``CHIYO_AUTONOMOUS_NEW_RUN_WINDOW``, never
  clears the quiet state, never forces a wake and never re-opens a request window.
* No unbounded default: ``duration_seconds`` defaults to 900 and ``max_ticks`` may only make it
  tighter.  An explicit long segment is still finite and capped by ``MAX_DURATION_SECONDS``.
* No diary, no message, no "mental livestream": the summary is facts rebuilt from records.

Contracts: ``docs/engineering/v04-lived-day-runtime-result.md``.
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import life as phone_life
from . import service as phone_service

SCHEMA_RUN = "phone.live_day.run.v1"
SCHEMA_STATUS = "phone.live_day.status.v1"
SCHEMA_SUMMARY = "phone.live_day.summary.v1"
SCHEMA_STOP = "phone.live_day.stop.v1"

#: Finite by default.  An operator who wants a longer stretch asks for one explicitly, and even
#: then it is still a bounded segment -- never an unbounded daemon.
DEFAULT_DURATION_SECONDS = 900
#: One day is the hard ceiling: anything above it is a daemon wearing a duration argument.
MAX_DURATION_SECONDS = 86400

SERVICE_AUTO = "auto"
SERVICE_ALWAYS = "always"
SERVICE_NEVER = "never"
SERVICE_MODES = (SERVICE_AUTO, SERVICE_ALWAYS, SERVICE_NEVER)

READY = "READY"
UNKNOWN = "UNKNOWN"
UNAVAILABLE = "UNAVAILABLE"

#: A live owner read from a report is time-bounded: an unresponsive owner delays the answer by
#: this many seconds and is then reported ``UNKNOWN``/``TIMEOUT`` instead of blocking the release.
LIVE_READ_TIMEOUT_SECONDS = 15.0

#: How many wake records / journal rows the bounded summary may carry.
DEFAULT_SUMMARY_LIMIT = 12
#: A tail read of a record file is bounded by bytes as well as by rows: the journal grows.
TAIL_BYTES = 262144

#: The events that can legitimately bring the next consideration *earlier* than the interval.
EARLIER_IF = ("WORLD_OPPORTUNITY_CHANGED", "RESUME_ELIGIBILITY_OPENED", "ARTIFACT_CHANGED")

#: Wake statuses, grouped into the categories that must never be confused with each other
#: (16 §2.3: idle / waiting / rest / unknown are different things; a fault is not rest).
WAKE_BUCKETS = {
    "idle": ("IDLE_NO_CANDIDATE",),
    "quiet": ("NOT_DUE", "QUIET", "DUPLICATE_EVENT"),
    "chose": ("NO_ACTION", "DEFER", "PAUSED", "ABANDONED"),
    "acted": ("ACTED", "IN_TRANSIT"),
    "unknown": ("UNKNOWN", "UNSETTLED", "ERROR"),
    "blocked": ("BLOCKED", "NO_STEP_AVAILABLE", "DISABLED"),
    "held": ("PAUSED_BY_OPERATOR",),
}


class LiveDayError(RuntimeError):
    """A named refusal.  Nothing was started and nothing was written."""


# ---------------------------------------------------------------------------------------------
# small read-only helpers
# ---------------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _read_jsonl_tail(path: Path, limit: int, *, max_bytes: int = TAIL_BYTES) -> list[dict[str, Any]]:
    """The last ``limit`` JSON objects of a newline-delimited record file.

    Bounded on purpose: the resident journal and the wake log grow for as long as the instance
    lives, so a report reads a byte-bounded tail and never the whole file.  A half-written first
    line of the tail window is dropped rather than parsed as a record.
    """

    if limit <= 0:
        return []
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return []
    start = max(0, size - max(1, int(max_bytes)))
    try:
        with path.open("rb") as handle:
            if start:
                handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start and lines:
        lines = lines[1:]
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, Mapping):
            rows.append(dict(row))
    return rows[-int(limit):]


def _pid_alive(pid: Any) -> Optional[bool]:
    """Whether that pid is alive, or ``None`` when this platform cannot say safely.

    On Windows ``os.kill(pid, 0)`` would terminate the process, so it is never called there.
    """

    try:
        number = int(pid)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if os.name != "posix":
        return None
    try:
        os.kill(number, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _recorded_ticks(state_root: Path | str) -> int:
    """How many ticks this state root has already recorded (summary, then journal).

    The resident's own ``run(max_ticks=...)`` compares that argument against its *cumulative*
    counter (replayed from the journal at start), so an operator's "this segment gets N ticks"
    has to be translated into an absolute target here instead of being passed through.
    """

    summary = _read_json(Path(state_root) / "resident_summary.json")
    recorded = 0
    try:
        recorded = max(0, int(summary.get("ticks") or 0))
    except (TypeError, ValueError):
        recorded = 0
    for row in _read_jsonl_tail(Path(state_root) / "resident_journal.jsonl", 5):
        try:
            recorded = max(recorded, int(row.get("tick") or 0))
        except (TypeError, ValueError):
            continue
    return recorded


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


# ---------------------------------------------------------------------------------------------
# live owner reads (read-only; never starts or binds anything)
# ---------------------------------------------------------------------------------------------


def _life_modules() -> dict[str, Any]:
    return phone_life._life_modules()


def _pause_view(state_root: Path | str) -> dict[str, Any]:
    try:
        _life_modules()
        import life_pause
    except Exception as exc:  # the pause bit is unreadable -> UNKNOWN, never "not paused"
        return {"state": UNKNOWN, "paused": None,
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    try:
        return dict(life_pause.read(state_root))
    except Exception as exc:
        return {"state": UNKNOWN, "paused": None,
                "detail": "%s: %s" % (type(exc).__name__, exc)}


def journey_projection(environment: Mapping[str, str]) -> dict[str, Any]:
    """The World journey's own read-only projection (no realize, no advance, no file written)."""

    home = str(environment.get("CHIYO_WORLD_HOME") or environment.get("HERMES_HOME") or "")
    if not home:
        return {"status": UNAVAILABLE, "reason": "WORLD_HOME_NOT_CONFIGURED"}
    try:
        cycle = _life_modules()["odl"]  # noqa: F841 - importing the chain puts modules on path
        import autonomous_life_cycle as alc

        journey = alc.journey_module()
        projection = journey.observe_journey(hermes_home=home, environment=dict(environment))
    except Exception as exc:
        return {"status": UNAVAILABLE, "reason": "JOURNEY_PROJECTION_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    out = dict(projection or {})
    out.setdefault("source", "world_journey_projection")
    return out
def _daily_life_context(provider: Any, reader: Any, *, limit: int) -> Optional[dict[str, Any]]:
    """B's own ``daily_life_context`` seam, read after **one** real observation if needed.

    That seam describes the observation the provider already took in this round; a report that
    has just started has taken none, so it answers ``unavailable/NO_OBSERVATION``.  In that case
    exactly one read-only observation is taken through the same provider (the same transport the
    action path submits through) and the seam is asked again -- the context a report shows is
    therefore built from a real observation, never from a remembered one.
    """

    def call() -> Optional[dict[str, Any]]:
        try:
            return dict(reader(limit=int(limit)) or {})
        except TypeError:
            try:
                return dict(reader() or {})
            except Exception:
                return None
        except Exception:
            return None

    context = call()
    if context is None:
        return None
    if str(context.get("status") or "").strip().lower() in {"available", "ready"}:
        return context
    try:
        provider.observe()
    except Exception:
        return context
    return call() or context




def _catalog_names(catalog: Mapping[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for item in (catalog or {}).get("opportunities") or []:
        if not isinstance(item, Mapping):
            continue
        place = str(item.get("location_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if place and name:
            names[place] = name
    return names


def live_owner_context(environment: Mapping[str, str], *, limit: int = 8) -> dict[str, Any]:
    """One bounded, read-only look at where she is / what the World offers.

    If the World provider of this tree publishes its own ``daily_life_context`` seam, that seam
    is used as-is (it is B's contract and this layer must not fork it).  Otherwise the provider's
    own observation read is used -- the same transport the action path submits through, so the
    facts are the World owner's, not a second database.  Nothing here writes, and an unreachable
    or unconfigured World is reported as ``UNAVAILABLE`` instead of being guessed.
    """

    prepared: dict[str, Any] = {"status": UNAVAILABLE}
    try:
        import autonomous_life_assembly as assembly

        resolved = assembly.resolve_world_provider(dict(environment))
    except Exception as exc:
        return {**prepared, "reason": "WORLD_RESOLVE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    provider = resolved.get("provider")
    if provider is None:
        return {**prepared, "reason": str(resolved.get("reason") or "WORLD_UNAVAILABLE"),
                "detail": resolved.get("detail")}

    reader = getattr(provider, "daily_life_context", None)
    if callable(reader):
        context = _daily_life_context(provider, reader, limit=limit)
        if context is None:
            return {**prepared, "reason": "DAILY_LIFE_CONTEXT_FAILED"}
        context.setdefault("source", "provider_daily_life_context")
        return context

    try:
        provider.observe()
    except Exception as exc:
        return {**prepared, "reason": "WORLD_OBSERVE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    observation = dict(getattr(provider, "last_observation", None) or {})
    if not observation:
        return {**prepared, "reason": str(getattr(provider, "last_reason", "") or
                                          "WORLD_OBSERVATION_EMPTY")}
    catalog = dict(getattr(provider, "last_catalog", None) or {})
    names = _catalog_names(catalog)
    labels: dict[str, str] = {}
    for item in observation.get("visible_objects") or []:
        if isinstance(item, Mapping) and item.get("object_id"):
            labels[str(item["object_id"])] = str(item.get("label") or item.get("object_id"))
    location = dict(observation.get("location") or {})
    place_id = str(location.get("place_id") or "").strip() or None
    hands = {str(slot): (None if held is None else labels.get(str(held), str(held)))
             for slot, held in (observation.get("hands") or {}).items()}
    nearby: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    for item in (catalog.get("opportunities") or []):
        if not isinstance(item, Mapping):
            continue
        place = str(item.get("location_id") or "")
        if not place or place == place_id:
            continue
        nearby.append({"location_id": place, "name": names.get(place),
                       "walk_seconds": (item.get("reachability") or {}).get("walk_seconds"),
                       "availability": (item.get("availability") or {}).get("status"),
                       "observed": (item.get("observed") or {}).get("status")})
    for item in list(getattr(provider, "last_opportunity_view", []) or [])[: max(1, int(limit))]:
        if not isinstance(item, Mapping):
            continue
        opportunities.append({
            "verb": item.get("verb"), "target_ref": item.get("target_ref"),
            "location_id": item.get("location_id"), "availability": item.get("availability"),
            "walk_seconds": item.get("walk_seconds"),
            "stay_seconds": item.get("stay_seconds"),
            "owner_blocked_reason": item.get("owner_blocked_reason")})
    unknowns: list[str] = []
    if not names.get(place_id or ""):
        unknowns.append("place_name")
    if (catalog.get("status") or "unavailable") != "ready":
        unknowns.append("catalog_status:%s" % (catalog.get("status") or "unavailable"))
    return {
        "status": READY,
        "source": "provider_observe",
        "observed_at": None,
        "world_revision": observation.get("world_revision"),
        "location": {"place_id": place_id, "name": names.get(place_id or ""),
                     "area_id": location.get("area_id")},
        "body": {"pose": observation.get("pose"), "orientation": observation.get("orientation"),
                 "hands": hands},
        "nearby": nearby[: max(1, int(limit))],
        "opportunities": opportunities,
        "unknowns": unknowns,
    }


def _bounded_live_context(environment: Mapping[str, str], *, limit: int,
                          timeout_seconds: float = LIVE_READ_TIMEOUT_SECONDS) -> dict[str, Any]:
    """``live_owner_context`` with a hard time bound; a late owner is reported, not awaited.

    The socket read itself has no cancellation point, so it runs on a daemon thread: a read that
    does not answer in time yields ``status=UNAVAILABLE, reason=OWNER_READ_TIMEOUT`` and the
    caller proceeds to release the lease/children.  A late result is simply never read.
    """

    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["context"] = live_owner_context(environment, limit=limit)
        except BaseException as exc:  # reported as an unavailable read
            box["error"] = "%s: %s" % (type(exc).__name__, exc)

    thread = threading.Thread(target=target, name="live-day-live-read", daemon=True)
    thread.start()
    thread.join(float(timeout_seconds))
    if thread.is_alive():
        return {"status": UNAVAILABLE, "reason": "OWNER_READ_TIMEOUT",
                "detail": "the World owner did not answer within %.0fs" % float(timeout_seconds)}
    if box.get("error"):
        return {"status": UNAVAILABLE, "reason": "OWNER_READ_FAILED",
                "detail": box["error"]}
    return dict(box.get("context") or {"status": UNAVAILABLE, "reason": "OWNER_READ_EMPTY"})


# ---------------------------------------------------------------------------------------------
# the operator summary (bounded, rebuilt from records; no new store)
# ---------------------------------------------------------------------------------------------


def _wake_counts(wakes: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    blocked: dict[str, int] = {}
    buckets = {name: 0 for name in WAKE_BUCKETS}
    for wake in wakes:
        status = str(wake.get("status") or "")
        status_class = str(wake.get("status_class") or "")
        if status:
            by_status[status] = by_status.get(status, 0) + 1
        if status_class:
            by_class[status_class] = by_class.get(status_class, 0) + 1
        for name, statuses in WAKE_BUCKETS.items():
            if status in statuses:
                buckets[name] += 1
        if status == "BLOCKED" or status_class == "blocked":
            reason = str(wake.get("detail") or "BLOCKED")[:120]
            blocked[reason] = blocked.get(reason, 0) + 1
    return {"by_status": by_status, "by_status_class": by_class, "buckets": buckets,
            "blocked_reasons": blocked}


def _bounded_wake(wake: Mapping[str, Any]) -> dict[str, Any]:
    """One wake record, reduced to the facts an operator may read."""

    out: dict[str, Any] = {
        "at": wake.get("at"), "kind": wake.get("kind"), "status": wake.get("status"),
        "status_class": wake.get("status_class"), "outcome": wake.get("outcome"),
        "decision": wake.get("decision"), "activity_ref": wake.get("activity_ref"),
        "action_id": wake.get("action_id"), "action_status": wake.get("action_status"),
        "llm_calls": wake.get("llm_calls"),
        "detail": (str(wake.get("detail"))[:200] if wake.get("detail") else None),
    }
    extra = wake.get("extra")
    if isinstance(extra, Mapping):
        waiting = extra.get("waiting")
        if isinstance(waiting, Mapping):
            out["maintenance"] = {key: waiting.get(key) for key in
                                  ("state", "checked", "changed", "satisfied",
                                   "emitted_eligibilities", "errors")}
        if extra.get("reconsideration") is not None:
            out["reconsideration"] = extra.get("reconsideration")
        if extra.get("owner_version_id"):
            out["owner_version_id"] = extra.get("owner_version_id")
    return out


def segment_summary(root: Path | str, *, phone: Optional[Mapping[str, Any]] = None,
                    nested: Optional[Mapping[str, Any]] = None,
                    state_root: Optional[Path | str] = None,
                    environment: Optional[Mapping[str, str]] = None,
                    segment_started_at: Optional[str] = None,
                    segment_ended_at: Optional[str] = None,
                    live_context: Optional[Mapping[str, Any]] = None,
                    limit: int = DEFAULT_SUMMARY_LIMIT) -> dict[str, Any]:
    """The bounded "where is she now" report, rebuilt from what the owners already recorded.

    Every section says where its fact came from and what it could not read.  A segment that
    could not read a live owner therefore reports ``UNKNOWN`` with a reason instead of inventing
    a place, and silence (``NOT_DUE``/``QUIET``/``IDLE_NO_CANDIDATE``) is counted apart from a
    fault (``BLOCKED``/``ERROR``) and apart from her own choice (``NO_ACTION``/``DEFER``).
    """

    root_path = phone_life.phone_root(root)
    if phone is None:
        try:
            phone = phone_life.load_phone_instance(root_path)
        except Exception as exc:
            phone = None
            phone_error = "%s: %s" % (type(exc).__name__, exc)
        else:
            phone_error = None
    else:
        phone_error = None
    if nested is None:
        try:
            nested = phone_life.read_nested_life(root_path)
        except Exception:
            nested = None
    if environment is None:
        try:
            environment = phone_life.map_life_environment(phone or {}, nested)
        except Exception:
            environment = {}
    resolved_state_root = (Path(state_root) if state_root is not None
                           else phone_life.life_state_root(root_path, nested))
    environment = dict(environment or {})

    summary = _read_json(resolved_state_root / "resident_summary.json")
    coverage = _read_json(resolved_state_root / "resident_coverage.json")
    events = _read_json(resolved_state_root / "autonomous_events.json")
    switch = _read_json(resolved_state_root / "autonomous_switch.json")
    pause = _pause_view(resolved_state_root)
    journey = journey_projection(environment)
    live = dict(live_context or {})

    start = _parse(segment_started_at)
    all_wakes = _read_jsonl_tail(resolved_state_root / "autonomous_wakes.jsonl", max(limit * 4, 40))
    segment_wakes = [wake for wake in all_wakes
                     if start is None or (_parse(wake.get("at")) or start) >= start]
    wakes = segment_wakes[-max(1, int(limit)):]
    ticks = [row for row in _read_jsonl_tail(resolved_state_root / "resident_journal.jsonl", 20)
             if start is None or (_parse(row.get("at")) or start) >= start][-5:]
    counters = dict(summary.get("counters") or {})
    baseline = dict(summary.get("baseline") or {})
    model = dict(summary.get("model_calls_detail") or {})
    budget = dict(summary.get("budget") or {})
    cap = dict(summary.get("segment_request_cap") or {})
    #: The persisted budget snapshot keeps the hash a *cycle* reports: the request ledger view
    #: lives under ``budget.requests``.  Reading the top level only would silently report nulls.
    budget_requests = dict(budget.get("requests") or {})

    # -- where she is now ------------------------------------------------------------------
    #  B's ``daily_life_context`` spells its verdict ``available``/``unavailable`` and names each
    #  gap as ``{"code", "detail"}``; an older provider spells it ``READY``/``UNAVAILABLE`` and may
    #  name gaps as plain strings.  Both are read here, and neither is upgraded into certainty.
    live_state = str(live.get("status") or "").strip().lower()
    live_ready = live_state in {"available", "ready"}
    location = dict(live.get("location") or {})
    journey_place = str(journey.get("location_id") or "").strip() or None
    place_id = _first_text(location.get("place_id"), journey_place)
    place_name = location.get("name")
    where_unknown: list[str] = []
    if live_ready:
        where_source = str(live.get("source") or "live_owner")
        where_status = READY
        for item in live.get("unknowns") or []:
            where_unknown.append(str(item.get("code") if isinstance(item, Mapping) else item))
    elif place_id:
        where_source = "world_journey_projection"
        where_status = READY if location.get("place_id") else UNKNOWN
        where_unknown.append("live_owner_unread:%s" % (
            live.get("reason_code") or live.get("reason") or "NOT_ATTEMPTED"))
    else:
        where_source = "UNKNOWN"
        where_status = UNKNOWN
        where_unknown.append(str(live.get("reason_code") or live.get("reason")
                                 or "NO_LIVE_OWNER_READ"))
    if not place_name and where_status == READY:
        where_unknown.append("place_name")
    where = {
        "status": where_status,
        "place_id": place_id,
        "place_name": place_name,
        "area_id": location.get("area_id"),
        "area_name": location.get("area_name"),
        "kind": location.get("kind"),
        "here": location.get("here"),
        "source": where_source,
        "observed_at": live.get("observed_at"),
        "world_revision": live.get("world_revision") or journey.get("revision"),
        "unknowns": sorted({item for item in where_unknown if item}),
    }
    in_transit = str(journey.get("status") or "") in {"travelling", "in_transit"} or bool(
        journey.get("remaining_seconds"))
    journey_block = {
        "status": journey.get("status"),
        "reason_code": journey.get("reason_code"),
        "target_location_id": journey.get("target_location_id"),
        "expected_arrival_at": journey.get("expected_arrival_at"),
        "remaining_seconds": journey.get("remaining_seconds"),
        "segment_status": journey.get("segment_status"),
        "segments_confirmed": journey.get("segments_confirmed"),
        "source": journey.get("source") or "world_journey_projection",
    }
    if journey.get("status") == UNAVAILABLE:
        journey_block["detail"] = journey.get("detail") or journey.get("reason")

    # -- what is in her hands / what she is doing -------------------------------------------
    body = dict(live.get("body") or {})
    labels: dict[str, str] = {}
    for item in live.get("nearby") or []:
        if isinstance(item, Mapping) and item.get("object_id"):
            labels[str(item["object_id"])] = str(item.get("label") or item.get("object_id"))
    raw_hands = dict(body.get("hands") or {})
    held_items: dict[str, Any] = {}
    hands_known = bool(body)  # the owner reported her body at all
    for slot, value in raw_hands.items():
        object_id = (str((value or {}).get("object_id") or "") if isinstance(value, Mapping)
                     else str(value or ""))
        held_items[str(slot)] = (labels.get(object_id, object_id) if object_id else None)
    hands_block = {
        "status": READY if hands_known else UNKNOWN,
        "items": held_items,
        "holding": [item for item in held_items.values() if item],
        "free_slots": list(body.get("free_slots") or []),
        "pose": body.get("pose"),
        "orientation": body.get("orientation"),
        "hands_usable": body.get("hands_usable"),
        "source": (str(live.get("source") or "live_owner") if hands_known
                   else "UNKNOWN:%s" % (live.get("reason_code") or live.get("reason")
                                        or "NO_LIVE_OWNER_READ")),
    }
    #: "What is she doing" is the last wake that really considered something.  A ``NOT_DUE`` or
    #: ``QUIET`` record is silence -- the window was closed or the state was already considered --
    #: so it can never be reported as her current activity.
    last_wake = wakes[-1] if wakes else {}
    active_wake: dict[str, Any] = {}
    for wake in reversed(wakes):
        if str(wake.get("status") or "") not in {"NOT_DUE", "QUIET", "DUPLICATE_EVENT"}:
            active_wake = dict(wake)
            break
    doing = {
        "status": READY if active_wake else UNKNOWN,
        "activity_ref": active_wake.get("activity_ref"),
        "decision": active_wake.get("decision"),
        "outcome": active_wake.get("outcome"),
        "status_word": active_wake.get("status"),
        "since": active_wake.get("at"),
        "source": "resident_wake_journal",
        "last_silence": (None if not last_wake
                        else {"status": last_wake.get("status"), "at": last_wake.get("at")}),
        "pause": {"state": pause.get("state"), "paused": pause.get("paused"),
                  "paused_at": pause.get("paused_at"), "reason": pause.get("reason")},
        "note": ("活动引用只来自这一段真的被考虑过的 wake 记录；NOT_DUE/QUIET 是安静，"
                 "不是她正在做的活动"),
    }
    if not active_wake:
        doing["unknown_reason"] = ("NO_CONSIDERED_WAKE_IN_THIS_SEGMENT" if wakes
                                   else "NO_WAKE_IN_THIS_SEGMENT")

    # -- where the segment stopped ----------------------------------------------------------
    work = dict(baseline.get("work") or {})
    selection = dict(baseline.get("work_selection") or summary.get("work_selection") or {})
    stop_point = {
        "status": READY if (work or selection) else UNKNOWN,
        "artifact_id": work.get("artifact_id"),
        "title": work.get("title"),
        "head_version_id": work.get("head_version_id"),
        "version_number": work.get("version_number"),
        "content_chars": work.get("content_chars"),
        "resumable": work.get("resumable"),
        "selected_by": selection.get("selected_by"),
        "foreground_activity_ref": baseline.get("foreground_activity_ref"),
        "stopped_at": summary.get("stopped_at"),
        "stop_reason": summary.get("stop_reason"),
        "source": "resident_baseline" if work else "NO_BASELINE",
        "note": "启停点来自 owner 的作品 head 与 runtime 前台指针；不是重放许可",
    }
    if not work:
        stop_point["unknown_reason"] = "BASELINE_ABSENT"

    # -- when the next consideration could happen -------------------------------------------
    interval = summary.get("interval_minutes")
    last_check = events.get("last_check_at")
    next_check = None
    next_status = UNKNOWN
    parsed_check = _parse(last_check)
    if isinstance(interval, int) and parsed_check is not None:
        next_check = _iso(parsed_check + timedelta(minutes=int(interval)))
        next_status = READY
    elif not events:
        next_status = UNKNOWN
        reason = "OWNER_EVENT_STATE_ABSENT"
    else:
        reason = "INTERVAL_OR_LAST_CHECK_UNKNOWN"
    if next_status == READY:
        reason = None
    next_block = {
        "status": next_status,
        "check_window_at": next_check,
        "interval_minutes": interval,
        "last_check_at": last_check,
        "journey_arrival_at": journey.get("expected_arrival_at"),
        "earlier_if": list(EARLIER_IF),
        "source": "runner_event_state + resident_summary",
        "note": "安静不是故障：窗口没到就没有请求，也不重新打开预算",
        "unknown_reason": reason,
    }

    # -- what this segment actually changed --------------------------------------------------
    changed = {
        "wakes": _wake_counts(segment_wakes),
        "llm_calls": sum(int(wake.get("llm_calls") or 0) for wake in segment_wakes),
        "ticks": [{"at": row.get("at"), "tick": row.get("tick"),
                   "events": len(row.get("events") or []),
                   "due_status": (row.get("due") or {}).get("status"),
                   "journey": {key: (row.get("journey") or {}).get(key)
                               for key in ("journey_status", "location_id", "arrived",
                                           "arrival_refused")}}
                  for row in ticks],
        "owner_versions": int(counters.get("owner_confirmed_versions") or 0),
        "owner_version_refs": sorted((counters.get("owner_version_refs") or {}).keys())[:20],
        "owner_version_unknown": int(counters.get("owner_version_unknown") or 0),
        "model_requests": model.get("total_requests"),
        "model_requests_source": model.get("total_source"),
        "cognition_calls": model.get("cognition_calls"),
        "content_generation_calls": model.get("content_generation_calls"),
        "coverage": {"covered_seconds": summary.get("covered_seconds"),
                     "wall_span_seconds": summary.get("wall_span_seconds"),
                     "unknown_seconds": summary.get("unknown_seconds"),
                     "processes": len(coverage.get("processes") or [])},
        "budget": {
            "run_id": budget_requests.get("run_id") or budget.get("run_id"),
            "utc_day": budget_requests.get("utc_day"),
            "utc_day_calls": budget_requests.get("utc_day_calls"),
            "utc_day_max": budget_requests.get("utc_day_max"),
            "run_window_calls": budget_requests.get("run_window_calls"),
            "run_window_max": budget_requests.get("run_window_max"),
            "windows": budget_requests.get("windows"),
            "ledger_state": budget_requests.get("state"),
            "cognition_used_today": budget.get("cognition_used_today"),
            "source": "resident_summary.budget.requests (as the cycle reported it)",
            "note": "同一个 run 窗口被续用：这里只读已记录的消耗，不重开窗口、不重置账本",
        },
        "segment_request_cap": cap or None,
        "actions": [{"action_id": wake.get("action_id"), "status": wake.get("action_status"),
                     "outcome": wake.get("outcome"), "at": wake.get("at")}
                    for wake in wakes if wake.get("action_id")][-max(1, int(limit)):],
        "last_wake": _bounded_wake(last_wake) if last_wake else None,
    }

    return {
        "schema": SCHEMA_SUMMARY,
        "instance": str(root_path),
        "state_root": str(resolved_state_root),
        "segment": {
            "started_at": segment_started_at,
            "ended_at": segment_ended_at or _iso(_now()),
            "starts": summary.get("starts"),
            "scope": ("EXPLICIT_SEGMENT" if segment_started_at else "RECORDED_HISTORY"),
            "this_process_started_at": summary.get("this_process_started_at"),
            "ticks_recorded": summary.get("ticks"),
            "running": summary.get("running"),
            "stop_reason": summary.get("stop_reason"),
            "stopped_at": summary.get("stopped_at"),
            "switch": switch or None,
        },
        "where": where,
        "journey": journey_block,
        "in_transit": in_transit,
        "hands": hands_block,
        "doing": doing,
        "nearby": list(live.get("nearby") or [])[: max(1, int(limit))],
        "opportunities": list(live.get("opportunities") or [])[: max(1, int(limit))],
        "stop_point": stop_point,
        "next": next_block,
        "changed": changed,
        "categories": {
            "silence": changed["wakes"]["buckets"].get("quiet", 0),
            "idle_no_candidate": changed["wakes"]["buckets"].get("idle", 0),
            "her_choice": changed["wakes"]["buckets"].get("chose", 0),
            "acted": changed["wakes"]["buckets"].get("acted", 0),
            "held_by_operator": changed["wakes"]["buckets"].get("held", 0),
            "blocked": changed["wakes"]["buckets"].get("blocked", 0),
            "unknown": changed["wakes"]["buckets"].get("unknown", 0),
            "fault": changed["wakes"]["buckets"].get("blocked", 0)
                     + changed["wakes"]["buckets"].get("unknown", 0),
            "note": ("安静/没合格事项/她的选择/被挡住/未知彼此不可替代；"
                     "预算耗尽或被挡不是休息"),
        },
        "limits": {"wakes": int(limit), "tail_bytes": TAIL_BYTES,
                   "private_text_included": False},
        "phone_error": phone_error,
        "read_only": True,
        "writes": False,
    }


# ---------------------------------------------------------------------------------------------
# services (the parent's own owner startup, only when they are not already up)
# ---------------------------------------------------------------------------------------------


class _ServeHandle:
    """One in-process ``serve_instance`` generation and the children *it* started."""

    def __init__(self, *, thread: threading.Thread, state: dict[str, Any],
                 stop_event: threading.Event) -> None:
        self.thread = thread
        self.state = state
        self.stop_event = stop_event

    @property
    def announcement(self) -> dict[str, Any]:
        return {key: value for key, value in self.state.items() if not str(key).startswith("_")}

    def error(self) -> Optional[str]:
        return self.state.get("error")

    def stop(self, *, timeout: float = 45.0) -> dict[str, Any]:
        self.stop_event.set()
        self.thread.join(timeout=float(timeout))
        return {"stopped": not self.thread.is_alive(),
                "children": dict(self.state.get("children") or {}),
                "pid": self.state.get("pid")}


def _default_service_runner(root: Path | str, *, stop_event: threading.Event,
                            ready_timeout: float) -> _ServeHandle:
    """Start the phone's own owners in-process through ``demo.serve_instance``.

    Reusing that function (instead of spawning a second CLI generation) keeps the live binding,
    the child logs and the socket retirement rules exactly as the operator's own ``serve``
    does.  The children are that function's children: its ``finally`` stops them on the way out,
    including when this segment is cancelled.
    """

    from . import demo

    ready = threading.Event()
    state: dict[str, Any] = {}

    def on_ready(announcement: Mapping[str, Any]) -> None:
        state.update(dict(announcement))
        ready.set()

    def target() -> None:
        try:
            demo.serve_instance(Path(root), port=0, ready_timeout=float(ready_timeout),
                                stop_event=stop_event, announce=False, on_ready=on_ready)
        except BaseException as exc:  # a real startup failure is reported, not swallowed
            state["error"] = "%s: %s" % (type(exc).__name__, exc)
        finally:
            ready.set()

    thread = threading.Thread(target=target, name="live-day-serve", daemon=True)
    thread.start()
    ready.wait(float(ready_timeout) + 5.0)
    return _ServeHandle(thread=thread, state=state, stop_event=stop_event)


def _ensure_services(root: Path | str, phone: Mapping[str, Any], *,
                     mode: str, stop_event: threading.Event,
                     ready_timeout: float,
                     service_runner: Optional[Callable[..., _ServeHandle]] = None
                     ) -> tuple[dict[str, Any], Optional[_ServeHandle]]:
    supply = str(phone.get("supply_socket") or "").strip()
    live = False
    if supply:
        try:
            live = bool(phone_service.supply_ready(supply))
        except Exception:
            live = False
    report: dict[str, Any] = {"mode": mode, "existing_live": live, "started": False,
                              "supply_socket": supply or None, "ready_timeout": ready_timeout}
    if live and mode != SERVICE_ALWAYS:
        report["status"] = READY
        report["detail"] = "the phone instance's own owners already answer; nothing was started"
        return report, None
    if mode == SERVICE_NEVER:
        report["status"] = UNAVAILABLE
        report["reason"] = "OWNER_SERVICES_NOT_RUNNING"
        return report, None
    runner = service_runner or _default_service_runner
    handle = runner(Path(root), stop_event=stop_event, ready_timeout=float(ready_timeout))
    if handle.error() or not handle.announcement.get("port"):
        detail = handle.error() or "CHILD_SERVICE_TIMEOUT"
        handle.stop()
        report["status"] = UNAVAILABLE
        report["reason"] = "OWNER_SERVICES_NOT_READY"
        report["detail"] = detail
        return report, None
    announcement = handle.announcement
    report.update({
        "status": READY,
        "started": True,
        "pid": announcement.get("pid"),
        "children": dict(announcement.get("children") or {}),
        "url": announcement.get("url"),
        "supply_generation": announcement.get("supply_generation"),
        "detail": "started this segment's own owner children through demo.serve_instance",
    })
    return report, handle


# ---------------------------------------------------------------------------------------------
# one continuous segment
# ---------------------------------------------------------------------------------------------


class _SegmentControl:
    """Cancellation for one segment: the resident's own ``request_stop`` plus the serve event."""

    def __init__(self, *, stop_event: threading.Event) -> None:
        self.stop_event = stop_event
        self.resident: Any = None
        self.cancel_reason: Optional[str] = None

    def attach(self, resident: Any) -> None:
        self.resident = resident
        if self.cancel_reason is not None:
            try:
                resident.request_stop(reason=self.cancel_reason)
            except Exception:
                pass

    def cancel(self, reason: str) -> None:
        if self.cancel_reason is None:
            self.cancel_reason = reason
        self.stop_event.set()
        if self.resident is not None:
            try:
                self.resident.request_stop(reason=reason)
            except Exception:
                pass


class _signal_cancel:
    """Install graceful cancel handlers for the duration of a segment (main thread only)."""

    def __init__(self, control: _SegmentControl) -> None:
        self.control = control
        self.previous: dict[int, Any] = {}

    def __enter__(self) -> "_signal_cancel":
        def handler(signum, _frame):
            self.control.cancel("signal:%s" % signum)

        for name in ("SIGTERM", "SIGINT"):
            number = getattr(signal, name, None)
            if number is None:
                continue
            try:
                self.previous[number] = signal.signal(number, handler)
            except (ValueError, OSError):
                continue
        return self

    def __exit__(self, *_exc: Any) -> bool:
        for number, handler in self.previous.items():
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):
                pass
        return False


def run_segment(root: Path | str, *, duration_seconds: Optional[int] = DEFAULT_DURATION_SECONDS,
                max_ticks: Optional[int] = None, poll_seconds: Optional[int] = None,
                max_new_requests: Optional[int] = None, act: bool = True,
                start_services: str = SERVICE_AUTO,
                environment: Optional[Mapping[str, str]] = None,
                service_runner: Optional[Callable[..., _ServeHandle]] = None,
                resident_factory: Optional[Callable[..., Any]] = None,
                ready_timeout: Optional[float] = None,
                stop_event: Optional[threading.Event] = None,
                limit: int = DEFAULT_SUMMARY_LIMIT) -> dict[str, Any]:
    """Run one finite segment of this instance's life, then always release everything.

    ``service_runner`` / ``resident_factory`` exist only so a test can prove *what* was started
    without a real socket/owner; both default to the shipped functions.  The resident factory
    is wrapped (never replaced) so the genuine class is still the one that runs.
    """

    from . import demo

    root_path = phone_life.phone_root(root)
    event = stop_event if stop_event is not None else threading.Event()
    control = _SegmentControl(stop_event=event)
    ready_seconds = float(demo.CHILD_READY_TIMEOUT if ready_timeout is None else ready_timeout)
    started_at = _iso(_now())
    report: dict[str, Any] = {
        "schema": SCHEMA_RUN,
        "instance": str(root_path),
        "ok": False,
        "status": UNKNOWN,
        "exit_code": phone_life.EXIT_BLOCKED,
        "bounds": {
            "duration_seconds": duration_seconds,
            "max_ticks": max_ticks,
            "poll_seconds": poll_seconds,
            "max_new_requests": max_new_requests,
            "unbounded": False,
            "default_finite": True,
        },
        "act": bool(act),
        "start_services": start_services,
        "services": {"mode": start_services, "started": False, "status": UNAVAILABLE},
        "segment_started_at": started_at,
        "model_called": False,
        "processes_started": 0,
        "instance_files_written_by_this_layer": [],
        "note": ("一个连续生活段：owner startup 复用 phone.demo.serve_instance，"
                 "循环复用 phone.life.life_run 里的现有 resident；不 prepare、不reset、"
                 "不新开 run 窗口、不提高任何既有请求上限"),
    }

    def refused(reason: str, detail: str = "", *, status: str = phone_life.BLOCKED,
                exit_code: int = phone_life.EXIT_REFUSED) -> dict[str, Any]:
        report.update({"reason": reason, "detail": detail, "status": status,
                       "ok": False, "exit_code": exit_code,
                       "segment_ended_at": _iso(_now())})
        return report

    # -- bounds ---------------------------------------------------------------------------
    if start_services not in SERVICE_MODES:
        return refused("SERVICE_MODE_UNKNOWN", "start_services must be one of %s"
                       % (", ".join(SERVICE_MODES),), status=phone_life.BLOCKED,
                       exit_code=phone_life.EXIT_USAGE)
    if duration_seconds is None and max_ticks is None:
        return refused("FINITE_BOUND_REQUIRED",
                       "this entry never runs unbounded: pass --duration-seconds and/or "
                       "--max-ticks", exit_code=phone_life.EXIT_USAGE)
    if duration_seconds is not None:
        try:
            duration_seconds = int(duration_seconds)
        except (TypeError, ValueError):
            return refused("DURATION_INVALID", "duration_seconds is not an integer",
                           exit_code=phone_life.EXIT_USAGE)
        if duration_seconds <= 0 or duration_seconds > MAX_DURATION_SECONDS:
            return refused("DURATION_OUT_OF_RANGE",
                           "duration_seconds must be 1..%d" % MAX_DURATION_SECONDS,
                           exit_code=phone_life.EXIT_USAGE)
    if max_ticks is not None:
        try:
            max_ticks = int(max_ticks)
        except (TypeError, ValueError):
            return refused("MAX_TICKS_INVALID", "max_ticks is not an integer",
                           exit_code=phone_life.EXIT_USAGE)
        if max_ticks <= 0:
            return refused("MAX_TICKS_INVALID", "max_ticks must be >= 1",
                           exit_code=phone_life.EXIT_USAGE)
    if poll_seconds is not None:
        try:
            poll_seconds = int(poll_seconds)
        except (TypeError, ValueError):
            return refused("POLL_INVALID", "poll_seconds is not an integer",
                           exit_code=phone_life.EXIT_USAGE)
        if poll_seconds <= 0:
            return refused("POLL_INVALID", "poll_seconds must be >= 1",
                           exit_code=phone_life.EXIT_USAGE)
    if max_new_requests is not None:
        try:
            max_new_requests = int(max_new_requests)
        except (TypeError, ValueError):
            return refused("REQUEST_CAP_INVALID", "max_new_requests is not an integer",
                           exit_code=phone_life.EXIT_USAGE)
        if max_new_requests <= 0:
            return refused("REQUEST_CAP_INVALID", "max_new_requests must be >= 1",
                           exit_code=phone_life.EXIT_USAGE)

    # -- cheap read-only prechecks (so services are not started for an instance that refuses)
    try:
        phone = phone_life.load_phone_instance(root_path)
    except phone_life.PhoneLifeError as exc:
        return refused(str(exc), "the phone instance could not be read")
    try:
        nested = phone_life.read_nested_life(root_path)
    except phone_life.PhoneLifeError as exc:
        return refused(str(exc), "the nested Life instance could not be read")
    if nested is None:
        return refused("NESTED_LIFE_INSTANCE_MISSING",
                       "this phone root has no %s/instance.json; this entry never prepares one"
                       % phone_life.ONE_DAY_DIRNAME)
    binding_env = phone_life.map_life_environment(
        phone, nested, base_env=environment if environment is None else dict(environment))
    state_root = phone_life.life_state_root(root_path, nested)
    report["state_root"] = str(state_root)
    report["run_id"] = binding_env.get("CHIYO_AUTONOMOUS_RUN_ID")
    permissions = phone_life.check_permissions(root_path, phone)
    report["permissions"] = permissions
    if permissions.get("uid_matches") is False:
        return refused("LOCAL_OWNER_UID_MISMATCH",
                       "this process is not the instance's recorded local owner")
    switch = phone_life.check_switch(root_path, nested)
    report["switch"] = switch
    if switch.get("recorded") is not True:
        return refused("AUTONOMOUS_SWITCH_OFF",
                       "the operator's own persisted switch is not on; this command never "
                       "turns autonomy on by itself")
    model = phone_life.check_model(binding_env)
    report["model"] = model
    if not model.get("configured"):
        return refused("NO_MODEL_PROVIDER",
                       "this instance has no usable provider configuration; nothing was started")
    if phone_life._int_or_none(binding_env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")) is None \
            and phone_life._int_or_none(
                binding_env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")) is None:
        return refused("REQUEST_CEILING_UNSET",
                       "no explicit request ceiling is configured for this instance; this entry "
                       "never invents one")

    # -- the owner children (only when they are not already up) ---------------------------
    services, serve = _ensure_services(root_path, phone, mode=start_services, stop_event=event,
                                       ready_timeout=ready_seconds,
                                       service_runner=service_runner)
    report["services"] = services
    if serve is not None:
        report["processes_started"] = len(services.get("children") or {})
    # ``--max-ticks`` is *this segment's* bound.  The resident compares its argument against its
    # own cumulative counter (replayed from the journal), so the absolute target is translated
    # here -- otherwise a second segment would stop after a single tick.
    max_ticks_absolute = max_ticks
    if max_ticks is not None:
        already = _recorded_ticks(state_root)
        max_ticks_absolute = already + int(max_ticks)
        report["bounds"]["ticks_already_recorded"] = already
        report["bounds"]["max_ticks_absolute"] = max_ticks_absolute
    capture: dict[str, Any] = {}
    lease: dict[str, Any] = {}
    life_report: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    try:
        if str(services.get("status")) != READY:
            return refused(str(services.get("reason") or "OWNER_SERVICES_NOT_READY"),
                           str(services.get("detail") or ""))
        # The genuine resident, with this segment's lower-only request cap attached.
        genuine = None
        try:
            genuine = _life_modules()["resident"].AutonomousLifeResident
        except Exception as exc:
            return refused("RESIDENT_UNAVAILABLE", "%s: %s" % (type(exc).__name__, exc))
        base_factory = resident_factory or genuine

        def factory(**kwargs: Any) -> Any:
            resident = base_factory(**kwargs, segment_request_cap=max_new_requests)
            capture["resident"] = resident
            control.attach(resident)
            return resident

        with _signal_cancel(control):
            try:
                life_report = phone_life.life_run(
                    root_path, once=False, max_ticks=max_ticks_absolute,
                    duration_seconds=(duration_seconds if duration_seconds is not None
                                      else DEFAULT_DURATION_SECONDS),
                    poll_seconds=poll_seconds, act=bool(act),
                    environment=environment, resident_factory=factory)
            except BaseException as exc:  # a cancel is not a crash: report it as one
                control.cancel("%s: %s" % (type(exc).__name__, exc))
                report["cancelled"] = True
                report["cancel_error"] = "%s: %s" % (type(exc).__name__, exc)
                life_report = {"ok": False, "reason": "CANCELLED",
                               "exit_code": phone_life.EXIT_BLOCKED}
    finally:
        # "Where is she now" is read from the live owner *before* the children of this segment
        # are stopped, so the last frame of the segment is the owner's own, not a guess.  The
        # read is time-bounded: a wedged owner can delay the report by a few seconds, never the
        # release of the lease or the children.
        live_context: Mapping[str, Any] = {
            "status": UNAVAILABLE,
            "reason": ("LIVE_READ_NOT_ATTEMPTED" if serve is None and not services.get("existing_live")
                       else "NOT_READ"),
        }
        if services.get("status") == READY:
            live_context = _bounded_live_context(binding_env, limit=limit,
                                                 timeout_seconds=LIVE_READ_TIMEOUT_SECONDS)
        if serve is not None:
            report["services_stopped"] = serve.stop()
        lease = phone_life.check_single_lease(state_root)
        report["lease"] = lease
        report["segment_ended_at"] = _iso(_now())
        report["segment_ownership"] = {
            "resident_factory": ("genuine_class_with_segment_cap" if resident_factory is None
                                 else "injected"),
            "resident_captured": "resident" in capture,
            "new_run_window_opened": False,
            "ledger_reset": False,
        }
        try:
            summary = segment_summary(
                root_path, phone=phone, nested=nested, state_root=state_root,
                environment=binding_env, segment_started_at=started_at,
                segment_ended_at=report.get("segment_ended_at"),
                live_context=live_context, limit=limit)
        except Exception as exc:  # the summary must never hide the segment's own result
            summary = {"schema": SCHEMA_SUMMARY, "status": UNKNOWN,
                       "error": "%s: %s" % (type(exc).__name__, exc)}
        # Keep the bounded facts the operator asked for even if the process is about to be
        # cancelled: this is the report, not a new store.
        report["life"] = {key: life_report.get(key) for key in
                          ("ok", "reason", "detail", "ran", "stopped", "counters",
                           "model_calls", "model_called", "exit_code", "lease", "switch",
                           "memory", "binding", "state_root", "run_id")}
        report["summary"] = summary
        # This segment's own spend, from this segment's wake records; the resident's counter is
        # cumulative (it replays the whole journal), so it is reported beside it.
        segment_calls = int((summary.get("changed") or {}).get("llm_calls") or 0)
        report["model_calls"] = segment_calls
        report["model_called"] = bool(segment_calls)
        report["model_calls_cumulative"] = int(life_report.get("model_calls") or 0)
    cancelled = bool(report.get("cancelled"))
    lease_status = str((report.get("lease") or {}).get("status") or "")
    if cancelled:
        report.update({"status": "CANCELLED", "reason": "CANCELLED",
                       "ok": False, "exit_code": 130,
                       "detail": "the segment was cancelled; the resident was stopped by its own "
                                 "stop() and the children this command started were stopped"})
    elif str(life_report.get("reason") or ""):
        blocked_reason = str(life_report.get("reason"))
        report.update({"status": phone_life.BLOCKED, "reason": blocked_reason,
                       "detail": str(life_report.get("detail") or ""),
                       "ok": False,
                       "exit_code": int(life_report.get("exit_code")
                                        or phone_life.EXIT_BLOCKED)})
    elif lease_status == "HELD":
        report.update({"status": phone_life.BLOCKED, "reason": "LEASE_STILL_HELD",
                       "ok": False, "exit_code": phone_life.EXIT_LEASE_HELD,
                       "detail": "the writer lease is still held after the segment"})
    else:
        report.update({"status": READY, "ok": True, "exit_code": phone_life.EXIT_OK})
    return report


# ---------------------------------------------------------------------------------------------
# read-only status / stop
# ---------------------------------------------------------------------------------------------


def status(root: Path | str, *, environment: Optional[Mapping[str, str]] = None,
           live: bool = False, limit: int = DEFAULT_SUMMARY_LIMIT) -> dict[str, Any]:
    """Pure read: where the instance is, without starting a service or creating anything.

    No socket is bound, no database/table is created and no lock file is touched.  The
    ``--live`` switch additionally asks the *already running* World owner for one bounded
    observation; when that is impossible the field is reported ``UNKNOWN`` with the reason.
    """

    root_path = phone_life.phone_root(root)
    report: dict[str, Any] = {
        "schema": SCHEMA_STATUS,
        "instance": str(root_path),
        "read_only": True,
        "services_started": 0,
        "model_called": False,
        "writes": False,
        "ok": False,
        "status": UNKNOWN,
        "exit_code": phone_life.EXIT_REFUSED,
        "limits": {"wakes": int(limit), "private_text_included": False},
    }
    try:
        phone = phone_life.load_phone_instance(root_path)
    except phone_life.PhoneLifeError as exc:
        report.update({"reason": str(exc), "detail": "the phone instance could not be read"})
        return report
    try:
        nested = phone_life.read_nested_life(root_path)
    except phone_life.PhoneLifeError as exc:
        nested = None
        report["nested_error"] = str(exc)
    if nested is None and "nested_error" not in report:
        report.update({"reason": "NESTED_LIFE_INSTANCE_MISSING",
                       "detail": "this phone root has no nested Life instance"})
        return report
    bound = phone_life.map_life_environment(
        phone, nested, base_env=environment if environment is None else dict(environment))
    state_root = phone_life.life_state_root(root_path, nested)
    summary = _read_json(state_root / "resident_summary.json")
    running_flag = summary.get("running")
    liveness = _pid_alive(summary.get("pid")) if running_flag else None
    live_context: Mapping[str, Any] = {
        "status": UNAVAILABLE, "reason": "NOT_REQUESTED"}
    if live:
        gateway = str(phone.get("world_body_socket") or "").strip()
        if not gateway:
            live_context = {"status": UNAVAILABLE, "reason": "WORLD_BODY_SOCKET_NOT_BOUND"}
        else:
            live_context = live_owner_context(bound, limit=limit)
    try:
        report["summary"] = segment_summary(
            root_path, phone=phone, nested=nested, state_root=state_root,
            environment=bound, segment_started_at=None,
            segment_ended_at=_iso(_now()), live_context=live_context, limit=limit)
    except Exception as exc:
        report["summary"] = {"schema": SCHEMA_SUMMARY, "status": UNKNOWN,
                             "error": "%s: %s" % (type(exc).__name__, exc)}
    report.update({
        "state_root": str(state_root),
        "server_summary_present": bool(summary),
        "running_recorded": running_flag,
        "pid": summary.get("pid"),
        "pid_alive": liveness,
        "lease": phone_life.check_single_lease(state_root),
        "live_owner_read": {"requested": bool(live), "status": live_context.get("status"),
                            "reason": live_context.get("reason")},
        "coverage": {key: summary.get(key) for key in
                     ("covered_seconds", "wall_span_seconds", "unknown_seconds",
                      "coverage_complete", "continuous")},
        "model_calls_detail": summary.get("model_calls_detail"),
        "status": READY if summary else UNKNOWN,
        "ok": True,
        "exit_code": phone_life.EXIT_OK,
    })
    if running_flag and liveness is False:
        report["note"] = ("the persisted record still says running but its pid is gone: this is "
                          "an unclosed segment, not a live one")
    return report


def stop(root: Path | str) -> dict[str, Any]:
    """Ask the resident this instance's own record names to stop (POSIX only, graceful).

    The resident's SIGTERM handler calls its own ``request_stop``, so the loop exits between
    ticks and its ``stop()`` releases the writer lease.  Windows has no deliverable SIGTERM
    (``os.kill`` would terminate the process without running ``finally``), so there this command
    refuses and names the honest alternative: Ctrl-C in the segment's own terminal.
    """

    root_path = phone_life.phone_root(root)
    report: dict[str, Any] = {"schema": SCHEMA_STOP, "instance": str(root_path), "ok": False,
                              "status": UNKNOWN, "exit_code": phone_life.EXIT_REFUSED}
    try:
        phone = phone_life.load_phone_instance(root_path)
        nested = phone_life.read_nested_life(root_path)
    except phone_life.PhoneLifeError as exc:
        report.update({"reason": str(exc)})
        return report
    if nested is None:
        report.update({"reason": "NESTED_LIFE_INSTANCE_MISSING"})
        return report
    state_root = phone_life.life_state_root(root_path, nested)
    summary = _read_json(state_root / "resident_summary.json")
    report["state_root"] = str(state_root)
    report["pid"] = summary.get("pid")
    if not summary:
        report.update({"status": READY, "ok": True, "exit_code": phone_life.EXIT_OK,
                       "reason": "NO_SEGMENT_RECORDED",
                       "detail": "nothing ever ran on this state root; nothing to stop"})
        return report
    if str(summary.get("state_root") or "") not in ("", str(state_root)):
        report.update({"reason": "SUMMARY_STATE_ROOT_MISMATCH",
                       "detail": "the recorded segment belongs to another state root"})
        return report
    if summary.get("running") is not True:
        report.update({"status": READY, "ok": True, "exit_code": phone_life.EXIT_OK,
                       "reason": "ALREADY_STOPPED", "stopped_at": summary.get("stopped_at")})
        return report
    liveness = _pid_alive(summary.get("pid"))
    if liveness is False:
        report.update({"status": READY, "ok": True, "exit_code": phone_life.EXIT_OK,
                       "reason": "STALE_RECORD",
                       "detail": "the record says running but its pid is gone; the lease is free",
                       "lease": phone_life.check_single_lease(state_root)})
        return report
    if os.name != "posix":
        report.update({"reason": "SIGNAL_STOP_UNSUPPORTED_ON_PLATFORM",
                       "detail": ("on this platform os.kill would terminate the process without "
                                  "its own stop(); use Ctrl-C in the segment's terminal")})
        return report
    if liveness is None:
        report.update({"reason": "PID_LIVENESS_UNKNOWN"})
        return report
    try:
        os.kill(int(summary["pid"]), signal.SIGTERM)
    except OSError as exc:
        report.update({"reason": "SIGNAL_FAILED", "detail": "%s: %s"
                       % (type(exc).__name__, exc)})
        return report
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        latest = _read_json(state_root / "resident_summary.json")
        if latest.get("running") is False:
            break
        time.sleep(0.2)
    latest = _read_json(state_root / "resident_summary.json")
    report.update({"status": READY, "ok": True, "exit_code": phone_life.EXIT_OK,
                   "running_after": latest.get("running"),
                   "stopped_at": latest.get("stopped_at"),
                   "lease": phone_life.check_single_lease(state_root),
                   "detail": "SIGTERM was delivered to the resident's own stop handler"})
    return report


def summary_text(summary: Mapping[str, Any]) -> str:
    """The five operator questions as a few bounded lines.  Facts only, no narration."""

    def brief(value: Any) -> str:
        text = str(value if value is not None else "unknown")
        return text if len(text) <= 120 else text[:117] + "..."

    where = dict(summary.get("where") or {})
    hands = dict(summary.get("hands") or {})
    doing = dict(summary.get("doing") or {})
    stop_point = dict(summary.get("stop_point") or {})
    next_block = dict(summary.get("next") or {})
    categories = dict(summary.get("categories") or {})
    changed = dict(summary.get("changed") or {})
    held = {slot: item for slot, item in (hands.get("items") or {}).items() if item}
    free_slots = list(hands.get("free_slots") or [])
    hands_known = str(hands.get("status") or "") == READY
    scope_label = ("本段改变" if str((summary.get("segment") or {}).get("scope")) == "EXPLICIT_SEGMENT"
                   else "已记录改变")
    lines = [
        "现在在哪: %s (%s) [%s]" % (
            brief(where.get("place_name") or where.get("place_id")),
            brief(where.get("place_id")), brief(where.get("source"))),
        "手上什么: %s [%s]" % (
            (", ".join("%s=%s" % (slot, item) for slot, item in held.items()) if held
             else ("空（双手空闲：%s）" % (", ".join(free_slots) or "未报") if hands_known
                   else "未读到")),
            brief(hands.get("source"))),
        "正在做什么: %s %s [%s]" % (brief(doing.get("activity_ref")),
                                  brief(doing.get("status_word")),
                                  brief(doing.get("source"))),
        "停在哪: %s v%s (%s) 停在 %s" % (
            brief(stop_point.get("artifact_id") or stop_point.get("foreground_activity_ref")),
            brief(stop_point.get("version_number")), brief(stop_point.get("selected_by")),
            brief(stop_point.get("stopped_at"))),
        "下一次可考虑: 窗口 %s（每 %s 分钟；旅程到达 %s，或有真实变化时更早）" % (
            brief(next_block.get("check_window_at")), brief(next_block.get("interval_minutes")),
            brief(next_block.get("journey_arrival_at"))),
        scope_label + ": acted=%s 选择不动=%s 安静=%s 无合格事项=%s 被挡=%s 未知=%s 请求=%s" % (
            brief(categories.get("acted")), brief(categories.get("her_choice")),
            brief(categories.get("silence")), brief(categories.get("idle_no_candidate")),
            brief(categories.get("blocked")), brief(categories.get("unknown")),
            brief(changed.get("model_requests"))),
    ]
    return "\n".join(lines)


__all__ = [
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_SUMMARY_LIMIT",
    "EARLIER_IF",
    "LiveDayError",
    "MAX_DURATION_SECONDS",
    "SCHEMA_RUN",
    "SCHEMA_STATUS",
    "SCHEMA_STOP",
    "SCHEMA_SUMMARY",
    "SERVICE_ALWAYS",
    "SERVICE_AUTO",
    "SERVICE_MODES",
    "SERVICE_NEVER",
    "journey_projection",
    "live_owner_context",
    "run_segment",
    "segment_summary",
    "status",
    "stop",
    "summary_text",
]
