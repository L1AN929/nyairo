#!/usr/bin/env python3
"""B7/E09: the wake runner -- how an unattended instance decides to consider living.

What this is not
----------------
Not a scheduler platform, not a second decision engine, not a message bus.  It owns
exactly two things the cycle does not: **when** a consideration window opens, and
**whether the instance is allowed to spend anything on it**.  The consideration
itself is still ``AutonomousLifeCycle.evaluate_once``; every effect still belongs to
the owners (LR-2/LR-5, AR-0/AR-1, Supply, World, Memory).

The two wake kinds (E09.1)
--------------------------
* **event wake** -- a real event about this individual happened: a resume
  eligibility opened, the real work changed, or a legal World opportunity was
  observed.  The event decides *that it is worth asking*; the question itself is
  always asked as ``EXPLICIT_REEVALUATION``, because AG-1 permanently forbids tick
  triggers.  A repeat of the same event does not re-trigger the same decision.
* **check window** -- a low-frequency look at whether anything is worth
  considering.  The window is **not** a life event and is never a reason to do
  something: with no candidate it stops before any model call.  Missed windows are
  never back-filled.

Order (E09.1), and it is the whole point of this module::

    wake -> identity / switch / budget -> zero-model candidate precheck
         -> only with a legal candidate: AG-1 -> AG-2 -> Action

With no candidate: no LLM call, no fabricated Activity, no meaningless experience,
no outbound message.

Three states that must never be confused (E09.3)
------------------------------------------------
``IDLE_NO_CANDIDATE``  nothing worth considering; normal silence.
``NO_ACTION``/``DEFER`` there was a candidate; she chose not to act (or later).
``BLOCKED``            a technical refusal -- switch, budget, provider, World,
                       permission.  This is never reported as "she chose to rest".

Stop and restore (E09.4)
------------------------
Disabling stops new decisions and new submissions.  It does **not** stop
reconciliation: an already-submitted action whose result is unknown may still be
resolved with its owner, because leaving it unknown would be a lie about the past.
Nothing here ever deletes a work, a memory or an activity.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import autonomous_life_cycle as alc

SWITCH_FILENAME = "autonomous_switch.json"
WAKE_LOG_FILENAME = "autonomous_wakes.jsonl"
EVENT_STATE_FILENAME = "autonomous_events.json"

#: The two wake kinds.
WAKE_KIND_EVENT = "EVENT"
WAKE_KIND_CHECK_WINDOW = "CHECK_WINDOW"

#: Event kinds this revision understands (E09.1 "初版").
EVENT_RESUME_ELIGIBILITY_OPENED = "RESUME_ELIGIBILITY_OPENED"
EVENT_ARTIFACT_CHANGED = "ARTIFACT_CHANGED"
EVENT_WORLD_OPPORTUNITY = "WORLD_OPPORTUNITY"
KNOWN_EVENT_KINDS = (EVENT_RESUME_ELIGIBILITY_OPENED, EVENT_ARTIFACT_CHANGED,
                     EVENT_WORLD_OPPORTUNITY)

#: Wake statuses.  Each one is a distinct real thing.
WAKE_DISABLED = "DISABLED"
WAKE_BLOCKED = "BLOCKED"
WAKE_NOT_DUE = "NOT_DUE"
WAKE_DUPLICATE_EVENT = "DUPLICATE_EVENT"
WAKE_IDLE_NO_CANDIDATE = "IDLE_NO_CANDIDATE"
WAKE_NO_ACTION = "NO_ACTION"
WAKE_DEFERRED = "DEFER"
WAKE_PAUSED = "PAUSED"
WAKE_ABANDONED = "ABANDONED"
WAKE_ACTED = "ACTED"
WAKE_UNKNOWN = "UNKNOWN"
WAKE_UNSETTLED = "UNSETTLED"
WAKE_ERROR = "ERROR"

#: Cycle outcome -> (wake status, class).  The class is what E09.3 separates:
#: ``silence`` is normal, ``choice`` is hers, ``blocked`` is a technical refusal.
OUTCOME_CLASS = {
    alc.OUTCOME_DISABLED: (WAKE_BLOCKED, "blocked"),
    alc.OUTCOME_BUDGET_EXHAUSTED: (WAKE_BLOCKED, "blocked"),
    alc.OUTCOME_DECISION_FAILED: (WAKE_BLOCKED, "blocked"),
    alc.OUTCOME_ADOPTION_REJECTED: (WAKE_BLOCKED, "blocked"),
    alc.OUTCOME_STEP_REFUSED: (WAKE_BLOCKED, "blocked"),
    alc.OUTCOME_NO_MATERIAL: (WAKE_IDLE_NO_CANDIDATE, "silence"),
    alc.OUTCOME_NO_ACTION: (WAKE_NO_ACTION, "choice"),
    alc.OUTCOME_DEFERRED: (WAKE_DEFERRED, "choice"),
    alc.OUTCOME_PAUSED: (WAKE_PAUSED, "choice"),
    alc.OUTCOME_ABANDONED: (WAKE_ABANDONED, "choice"),
    alc.OUTCOME_ACTED: (WAKE_ACTED, "acted"),
    alc.OUTCOME_ALREADY_DONE: (WAKE_ACTED, "acted"),
    alc.OUTCOME_STEP_UNKNOWN: (WAKE_UNKNOWN, "unknown"),
    alc.OUTCOME_STEP_UNSETTLED: (WAKE_UNSETTLED, "unknown"),
}

#: A repeated identical event inside this window does not re-trigger the decision.
DEFAULT_EVENT_DEDUPE_SECONDS = 900


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


class AutonomousSwitch:
    """The persisted global on/off.  Survives a restart; never deletes anything."""

    def __init__(self, state_root: Path | str) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / SWITCH_FILENAME

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def recorded(self) -> Optional[bool]:
        """The operator's own decision, or None when they never made one."""

        data = self._load()
        return bool(data["enabled"]) if "enabled" in data else None

    def set(self, enabled: bool, *, actor: str = "operator") -> dict[str, Any]:
        payload = {"enabled": bool(enabled), "changed_at": _iso(_now()), "changed_by": actor}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        return payload

    def view(self) -> dict[str, Any]:
        data = self._load()
        return {"recorded": data.get("enabled"), "changed_at": data.get("changed_at"),
                "changed_by": data.get("changed_by")}


@dataclass
class WakeRecord:
    """One consideration window, as it actually happened."""

    kind: str
    reason: str
    status: str
    status_class: str
    at: str
    trigger_ref: str = ""
    trigger_kind: str = ""
    llm_calls: int = 0
    detail: str = ""
    outcome: Optional[str] = None
    decision: Optional[str] = None
    activity_ref: Optional[str] = None
    action_id: Optional[str] = None
    action_status: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class AutonomousLifeRunner:
    """Wakes the cycle when there is a real reason to, and refuses when there is not."""

    def __init__(
        self,
        cycle: alc.AutonomousLifeCycle,
        *,
        state_root: Optional[Path | str] = None,
        env: Optional[Mapping[str, str]] = None,
        clock: Optional[Callable[[], datetime]] = None,
        interval_minutes: Optional[int] = None,
        event_dedupe_seconds: int = DEFAULT_EVENT_DEDUPE_SECONDS,
        enabled: Optional[bool] = None,
    ) -> None:
        self.cycle = cycle
        source = dict(os.environ if env is None else env)
        self.env = source
        self.state_root = Path(state_root or cycle.state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _now
        self.interval_minutes = int(
            interval_minutes if interval_minutes is not None
            else alc._int_setting(source, alc.ENV_CHECK_INTERVAL_MINUTES,
                                  alc.DEFAULT_CHECK_INTERVAL_MINUTES))
        self.event_dedupe_seconds = max(0, int(event_dedupe_seconds))
        self.switch = AutonomousSwitch(self.state_root)
        self.wake_log = self.state_root / WAKE_LOG_FILENAME
        self._event_state_path = self.state_root / EVENT_STATE_FILENAME
        self._forced_enabled = enabled
        self.last_wake: Optional[WakeRecord] = None
        self.last_error: Optional[str] = None
        self.wakes_run = 0

    # -- identity / switch -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Operator switch first, then the cycle's own master switch.

        The persisted switch wins when the operator made one, because "disable" has
        to mean disable across a restart.  Otherwise the environment decides.
        """

        recorded = self.switch.recorded()
        if self._forced_enabled is not None and recorded is None:
            # An injected flag cannot outvote the cycle's own master switch: the runner
            # may only be enabled where the cycle would actually consider anything.
            return bool(self._forced_enabled) and bool(self.cycle.enabled)
        if recorded is not None:
            return bool(recorded) and bool(self.cycle.enabled)
        return bool(self.cycle.enabled)

    def enable(self, *, actor: str = "operator") -> dict[str, Any]:
        self.switch.set(True, actor=actor)
        return self.status()

    def disable(self, *, actor: str = "operator") -> dict[str, Any]:
        """Stop new work.  Deliberately does NOT touch works, memories or activities."""

        self.switch.set(False, actor=actor)
        return self.status()

    # -- the low-frequency window ---------------------------------------------

    def _event_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._event_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_event_state(self, payload: dict[str, Any]) -> None:
        self._event_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._event_state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._event_state_path)

    def last_check_at(self) -> Optional[str]:
        return self._event_state().get("last_check_at")

    def due(self, *, now: Optional[datetime] = None) -> tuple[bool, str]:
        """Is a check window open?  Missed windows are never back-filled."""

        moment = now or self._clock()
        last = _parse(self.last_check_at())
        if last is None:
            return True, "NEVER_CHECKED"
        elapsed = (moment - last).total_seconds()
        if elapsed >= self.interval_minutes * 60:
            return True, "INTERVAL_ELAPSED"
        return False, "NOT_DUE"

    def _mark_checked(self, moment: datetime) -> None:
        state = self._event_state()
        state["last_check_at"] = _iso(moment)
        self._write_event_state(state)

    # -- event wake ------------------------------------------------------------

    def note_event(self, kind: str, ref: str, *, occurred_at: Optional[str] = None,
                   detail: str = "") -> dict[str, Any]:
        """Record that a real event happened, and say whether it is worth waking for.

        A repeat of the same ``(kind, ref)`` inside the dedupe window is recorded as
        seen but marked as not worth another decision: "重复事件不得无意义地连续触发
        同一决策".
        """

        moment = _parse(occurred_at) or self._clock()
        state = self._event_state()
        seen = dict(state.get("seen") or {})
        key = "%s|%s" % (str(kind), str(ref))
        previous = _parse(seen.get(key))
        fresh = (previous is None
                 or (moment - previous).total_seconds() >= self.event_dedupe_seconds)
        if fresh:
            seen[key] = _iso(moment)
        # Keep the dedupe table bounded without a background sweeper.
        if len(seen) > 200:
            for stale in sorted(seen, key=lambda k: seen[k])[: len(seen) - 100]:
                seen.pop(stale, None)
        state["seen"] = seen
        state["last_event_at"] = _iso(moment)
        state["last_event_kind"] = str(kind)
        state["last_event_ref"] = str(ref)
        self._write_event_state(state)
        return {"kind": str(kind), "ref": str(ref), "fresh": bool(fresh),
                "dedupe_seconds": self.event_dedupe_seconds, "detail": detail,
                "known_kind": str(kind) in KNOWN_EVENT_KINDS}

    # -- the wake itself -------------------------------------------------------

    def wake(self, *, kind: str, reason: str, trigger_ref: str,
             trigger_kind: str = alc.TRIGGER_IDLE_WINDOW, act: bool = True,
             dedupe: bool = False, now: Optional[datetime] = None) -> WakeRecord:
        """One consideration window, in the mandated order."""

        moment = now or self._clock()
        # 1. identity / switch
        if not self.enabled:
            return self._record(kind, reason, WAKE_DISABLED, "blocked", moment,
                                trigger_ref=trigger_ref, trigger_kind=trigger_kind,
                                detail="autonomous life is off; nothing was considered")
        # 2. budget
        allowed, why = self.cycle.budget.may_start_wake()
        if not allowed:
            return self._record(kind, reason, WAKE_BLOCKED, "blocked", moment,
                                trigger_ref=trigger_ref, trigger_kind=trigger_kind,
                                detail="budget refused the wake: %s" % why,
                                extra={"budget": self.cycle.budget.snapshot()})
        # 3. the same real event twice is not two decisions
        if dedupe and not self._event_is_fresh(reason, trigger_ref, moment):
            return self._record(kind, reason, WAKE_DUPLICATE_EVENT, "silence", moment,
                                trigger_ref=trigger_ref, trigger_kind=trigger_kind,
                                detail="this event was already considered inside the "
                                       "dedupe window")
        # 4. the cycle: zero-model precheck, then AG-1 -> AG-2 -> Action only if there
        #    is something legal to consider.
        self._mark_checked(moment)
        try:
            out = self.cycle.evaluate_once(trigger_ref=trigger_ref, trigger_kind=trigger_kind,
                                           act=act, observed_at=_iso(moment))
        except Exception as exc:  # a real failure is reported as one, never as silence
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return self._record(kind, reason, WAKE_ERROR, "blocked", moment,
                                trigger_ref=trigger_ref, trigger_kind=trigger_kind,
                                detail=self.last_error)
        status, status_class = self._classify(out)
        return self._record(
            kind, reason, status, status_class, moment,
            trigger_ref=trigger_ref, trigger_kind=trigger_kind,
            llm_calls=int(out.get("llm_calls") or 0),
            detail=str(out.get("detail") or ""),
            outcome=out.get("outcome"),
            decision=out.get("decision"),
            activity_ref=out.get("activity_ref"),
            action_id=out.get("action_id"),
            action_status=out.get("action_status"),
            extra={"budget": out.get("budget") or self.cycle.budget.snapshot()},
        )

    def run_due(self, *, act: bool = True, now: Optional[datetime] = None) -> WakeRecord:
        """The low-frequency window.  Not due means nothing at all happens."""

        moment = now or self._clock()
        is_due, why = self.due(now=moment)
        if not is_due:
            return self._record(WAKE_KIND_CHECK_WINDOW, "check-window", WAKE_NOT_DUE,
                                "silence", moment,
                                trigger_ref="evt:autonomous:check",
                                trigger_kind=alc.TRIGGER_IDLE_WINDOW,
                                detail="the check window is not open yet: %s" % why)
        return self.wake(kind=WAKE_KIND_CHECK_WINDOW, reason="check-window",
                         trigger_ref="evt:autonomous:check", act=act, now=moment)

    def wake_on_event(self, event: Mapping[str, Any], *, act: bool = True,
                      now: Optional[datetime] = None) -> WakeRecord:
        """An event wake: only a real, fresh event about this individual gets here."""

        moment = now or self._clock()
        kind = str(event.get("kind") or "")
        ref = str(event.get("ref") or "")
        noted = self.note_event(kind, ref, occurred_at=event.get("occurred_at"),
                                detail=str(event.get("detail") or ""))
        if not noted["fresh"]:
            return self._record(WAKE_KIND_EVENT, kind, WAKE_DUPLICATE_EVENT, "silence",
                                moment, trigger_ref=ref,
                                trigger_kind=alc.TRIGGER_IDLE_WINDOW,
                                detail="the same event was already considered")
        trigger_kind = self._trigger_kind_for_event(kind)
        return self.wake(kind=WAKE_KIND_EVENT, reason=kind, trigger_ref=ref,
                         trigger_kind=trigger_kind, act=act, now=moment)

    @staticmethod
    def _trigger_kind_for_event(kind: str) -> str:
        """The legal trigger an event maps to.  Never a tick."""

        if kind == EVENT_RESUME_ELIGIBILITY_OPENED:
            return alc.TRIGGER_RESUME_OPENED
        return alc.TRIGGER_IDLE_WINDOW

    def _event_is_fresh(self, reason: str, ref: str, moment: datetime) -> bool:
        seen = _parse((self._event_state().get("seen") or {}).get("%s|%s" % (reason, ref)))
        return (seen is None
                or (moment - seen).total_seconds() < self.event_dedupe_seconds)

    @staticmethod
    def _classify(out: Mapping[str, Any]) -> tuple[str, str]:
        outcome = str(out.get("outcome") or "")
        mapped = OUTCOME_CLASS.get(outcome)
        if mapped is not None:
            return mapped
        return WAKE_ERROR, "blocked"

    # -- reconciliation survives a disable (E09.4) -----------------------------

    def pending_unknown_actions(self) -> list[dict[str, Any]]:
        try:
            return list(self.cycle.runtime.action_read.list_actions(status="UNKNOWN"))
        except Exception as exc:  # a read failure is reported, never hidden
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return []

    def reconcile_pending(self) -> list[dict[str, Any]]:
        """Resolve already-submitted unknown results.  Allowed even when disabled.

        A disable stops *new* work.  It must not stop telling the truth about work
        that already crossed a submission boundary -- leaving those unknown would
        make the instance's own history a guess.
        """

        results: list[dict[str, Any]] = []
        for action in self.pending_unknown_actions():
            action_id = str(action.get("action_id") or "")
            try:
                resolved = self.cycle.runtime.action_command.reconcile_action(action_id=action_id)
            except Exception as exc:
                results.append({"action_id": action_id, "resolved": False,
                                "error": "%s: %s" % (type(exc).__name__, exc)})
                continue
            results.append({"action_id": action_id, "resolved": True,
                            "status": (resolved.get("action") or {}).get("status")})
        return results

    # -- reporting -------------------------------------------------------------

    def _record(self, kind: str, reason: str, status: str, status_class: str,
                moment: datetime, *, trigger_ref: str = "", trigger_kind: str = "",
                llm_calls: int = 0, detail: str = "", outcome: Optional[str] = None,
                decision: Optional[str] = None, activity_ref: Optional[str] = None,
                action_id: Optional[str] = None, action_status: Optional[str] = None,
                extra: Optional[dict[str, Any]] = None) -> WakeRecord:
        record = WakeRecord(
            kind=kind, reason=reason, status=status, status_class=status_class,
            at=_iso(moment), trigger_ref=trigger_ref, trigger_kind=trigger_kind,
            llm_calls=llm_calls, detail=detail, outcome=outcome, decision=decision,
            activity_ref=activity_ref, action_id=action_id, action_status=action_status,
            extra=dict(extra or {}))
        self.last_wake = record
        if status != WAKE_NOT_DUE:
            self.wakes_run += 1
        self._journal(record)
        return record

    def _journal(self, record: WakeRecord) -> None:
        try:
            self.wake_log.parent.mkdir(parents=True, exist_ok=True)
            with self.wake_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)
                             + "\n")
        except OSError as exc:  # journalling must never break a wake
            self.last_error = "wake journal unavailable: %s" % exc

    def recent_wakes(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0 or not self.wake_log.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.wake_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows[-int(limit):]

    def recent_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        """The real AR-0 actions, newest first -- including their UNKNOWN ones."""

        try:
            rows = list(self.cycle.runtime.action_read.list_actions())
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return []
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows[: max(0, int(limit))]

    def status(self) -> dict[str, Any]:
        cycle_status = self.cycle.status()
        return {
            "autonomous_enabled": self.enabled,
            "switch": self.switch.view(),
            "interval_minutes": self.interval_minutes,
            "check_window": {"due": self.due()[0], "reason": self.due()[1],
                             "last_check_at": self.last_check_at()},
            "budget": cycle_status.get("budget"),
            "wakes_run": self.wakes_run,
            "last_wake": self.last_wake.to_dict() if self.last_wake else None,
            "recent_wakes": self.recent_wakes(limit=5),
            "pending_unknown_actions": [a.get("action_id")
                                        for a in self.pending_unknown_actions()],
            "cycle": cycle_status,
            "last_error": self.last_error or cycle_status.get("last_error"),
        }


__all__ = [
    "AutonomousLifeRunner",
    "AutonomousSwitch",
    "DEFAULT_EVENT_DEDUPE_SECONDS",
    "EVENT_ARTIFACT_CHANGED",
    "EVENT_RESUME_ELIGIBILITY_OPENED",
    "EVENT_WORLD_OPPORTUNITY",
    "KNOWN_EVENT_KINDS",
    "OUTCOME_CLASS",
    "SWITCH_FILENAME",
    "WAKE_ACTED",
    "WAKE_BLOCKED",
    "WAKE_DEFERRED",
    "WAKE_DISABLED",
    "WAKE_DUPLICATE_EVENT",
    "WAKE_IDLE_NO_CANDIDATE",
    "WAKE_KIND_CHECK_WINDOW",
    "WAKE_KIND_EVENT",
    "WAKE_NOT_DUE",
    "WAKE_NO_ACTION",
    "WAKE_UNKNOWN",
    "WakeRecord",
]
