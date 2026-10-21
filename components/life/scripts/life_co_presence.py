#!/usr/bin/env python3
"""D (life-v1): the finite co-presence protocol (COEXIST), as an adapter and nothing else.

What this module is
-------------------
It *encodes and decodes* the two events the protocol is made of -- an explicit invitation and
an explicit end (or verifiable finite completion) -- and it *wires* them to the owners that
already exist:

* LR-3 (``InterruptionCoordinator``) for the finite, safe-boundary interruption;
* LR-2 (``ActivityCommandService``, through LR-4's WAIT consequence port) for the canonical
  pause and the waiting state;
* AG-0/AG-1/AG-2 for the joint Activity: the invitation becomes a *typed user request*, the
  Decision chooses, the Adoption creates the Activity.  No second foreground is opened;
* LR-4 (``WaitingCoordinator``) for the new resume eligibility that brings the private work
  back after the joint time ends.

What it deliberately is not
---------------------------
* No relationship owner, no second Activity writer, no "co-presence state machine" of its own.
  The only thing it persists is its own session record (``co_presence.json`` in the Life state
  root), which is bookkeeping about the invitation -- not a fact about the Activity.
* An ordinary inbound message is **an observation only**: it never pauses anything and never
  starts an Activity.  Only an explicit invitation may ask for engagement.
* The operator's global pause is not a social interruption.  A paused instance refuses the
  invitation; this module never writes the pause bit.
* True concurrency beyond the current single-foreground contract is **not** implemented.  It is
  declared as deferred rather than faked: the joint Activity is a normal single foreground
  Activity, and the private work waits in the owner's own WAITING state.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE.parents[1]), str(HERE.parents[1] / "native"),
              str(HERE.parents[1] / "native" / "integration")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

SESSION_FILENAME = "co_presence.json"
SESSION_SCHEMA = "chiyo.life.co_presence.v1"

#: Event kinds this protocol accepts.  Anything else is an ordinary inbound message.
KIND_INVITATION = "CO_PRESENCE_INVITATION"
KIND_END = "CO_PRESENCE_END"

#: LR-3's own word for "this interruption may be applied now".
INTERRUPT_NOW = "INTERRUPT_NOW"

#: How long the private work waits for an invitation's explicit end before LR-4's own expiry
#: opens its resume eligibility.  Bounded on purpose: an invitation nobody answers must not be
#: able to keep her own work stopped.  A caller may narrow/widen this with ``duration_seconds``
#: or ``expires_at`` on the invitation itself.
DEFAULT_WAIT_SECONDS = 900
MIN_WAIT_SECONDS = 60
MAX_WAIT_SECONDS = 6 * 3600

EFFECT_OBSERVATION_ONLY = "OBSERVATION_ONLY"
EFFECT_INVITATION = "INVITATION"

DECISION_ACCEPT = "ACCEPT"
DECISION_DEFER = "DEFER"
DECISION_DECLINE = "DECLINE"

REASON_FOREIGN_SUBJECT = "INVITATION_SUBJECT_MISMATCH"
REASON_FOREIGN_HOME = "INVITATION_HOME_MISMATCH"
REASON_EXPIRED = "INVITATION_EXPIRED"
REASON_OPERATOR_PAUSED = "OPERATOR_PAUSED"
REASON_ATOMIC = "FOREGROUND_ATOMIC_NOT_INTERRUPTIBLE"
REASON_BUDGET = "REQUEST_BUDGET_REFUSED"
REASON_ALREADY_OPEN = "A_CO_PRESENCE_SESSION_IS_ALREADY_OPEN"
REASON_NO_ENGAGEMENT = "INVITATION_DOES_NOT_REQUIRE_ENGAGEMENT"
REASON_NO_FOREGROUND = "NO_PRIVATE_ACTIVITY_TO_INTERRUPT"
#: v0.4 B: an idle instance accepts -- there is no save point to wait for, and no private work
#: to lose.  ``REASON_NO_FOREGROUND`` stays for the callers that still report the older word.
REASON_IDLE_ACCEPT = "IDLE_WITH_NO_PRIVATE_ACTIVITY"

REQUIRED_INVITATION_FIELDS = ("invitation_id", "source_ref", "occurred_at", "scope")

#: LR-4 owns the reference vocabulary for a waiting dependency: ``activity_waiting_agenda``
#: validates the prefix of ``waiting_on_ref`` against its own list.  This protocol's reference
#: therefore lives *inside* that namespace instead of inventing a new one.
WAITING_REF_PREFIX = "wait_dep"


def session_ref(invitation_id: str) -> str:
    """The LR-4-legal waiting reference for one co-presence session."""
    return "%s:co_presence-%s" % (WAITING_REF_PREFIX, str(invitation_id))


#: LR-5 owns the reference vocabulary too: a contract target ref and an
#: ``external_result_ref`` must carry a prefix it recognises, so this protocol's own effect
#: reference lives inside the ``external_result`` namespace instead of inventing one.
EFFECT_REF_PREFIX = "external_result"


def session_effect_ref(invitation_id: str) -> str:
    """The LR-5-legal external-result reference for one co-presence session."""
    return "%s:co_presence-%s" % (EFFECT_REF_PREFIX, str(invitation_id))


class CoPresenceError(RuntimeError):
    """A refusal with a reason code.  Never a partially applied protocol step."""


#: LR-3's own word for "this interruption may be applied now".  Kept as a module constant so the
#: decision-first path can compare against it without importing the interruption owner first.
INTERRUPT_NOW = "INTERRUPT_NOW"


class _ConsentNotGiven(RuntimeError):
    """The committed Decision did not start the joint time, or the safe park was refused.

    Raised from inside AG-2's ``pre_adoption_mutation_hook`` on purpose: an exception there stops
    the adoption before any Activity is written, which is exactly the guarantee this path needs --
    no pause, no joint Activity, no half-applied protocol step.
    """

    def __init__(self, decision: str, reason_code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail))
        self.decision = decision
        self.reason_code = reason_code
        self.detail = detail


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds")


def _instant(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _bounded_text(value: Any, limit: int = 200) -> str:
    """One line, bounded -- for a report field, never for a decision."""
    return " ".join(str(value or "").split())[:limit]


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CoPresenceError(f"{field.upper()}_REQUIRED")
    return text


# ---------------------------------------------------------------------------------------------
# the codec
# ---------------------------------------------------------------------------------------------


def classify(raw: Any) -> dict[str, Any]:
    """Is this an explicit invitation, an explicit end, or an ordinary inbound message?

    Only a payload that *says* it is a co-presence event is one.  Everything else -- including a
    message that merely sounds like an invitation -- is an ordinary inbound message, which is an
    observation and nothing more.
    """
    if not isinstance(raw, Mapping):
        return {"effect": EFFECT_OBSERVATION_ONLY, "kind": "ORDINARY",
                "reason_code": "NOT_A_TYPED_EVENT"}
    kind = str(raw.get("event_kind") or raw.get("kind") or "").strip().upper()
    if kind == KIND_INVITATION:
        return {"effect": EFFECT_INVITATION, "kind": KIND_INVITATION}
    if kind == KIND_END:
        return {"effect": EFFECT_OBSERVATION_ONLY, "kind": KIND_END}
    return {"effect": EFFECT_OBSERVATION_ONLY, "kind": "ORDINARY",
            "reason_code": "NO_EXPLICIT_EVENT_KIND"}


def parse_invitation(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One explicit invitation, validated.  Missing provenance is a refusal, never a default."""
    if not isinstance(raw, Mapping):
        raise CoPresenceError("INVITATION_NOT_A_MAPPING")
    if str(raw.get("event_kind") or raw.get("kind") or "").strip().upper() != KIND_INVITATION:
        raise CoPresenceError("NOT_AN_INVITATION_EVENT")
    for field in REQUIRED_INVITATION_FIELDS:
        if not str(raw.get(field) or "").strip():
            raise CoPresenceError("INVITATION_%s_REQUIRED" % field.upper())
    occurred = _instant(raw.get("occurred_at"))
    if occurred is None:
        raise CoPresenceError("INVITATION_OCCURRED_AT_INVALID")
    expires = raw.get("expires_at")
    if expires is not None and _instant(expires) is None:
        raise CoPresenceError("INVITATION_EXPIRES_AT_INVALID")
    refs = raw.get("material_refs")
    materials: list[str] = []
    if isinstance(refs, (list, tuple)):
        materials = [str(item).strip() for item in refs if str(item or "").strip()]
    elif isinstance(refs, str) and refs.strip():
        materials = [refs.strip()]
    return {
        "invitation_id": _require_text(raw.get("invitation_id"), "invitation_id"),
        "source_ref": _require_text(raw.get("source_ref"), "source_ref"),
        "occurred_at": occurred.isoformat(timespec="seconds"),
        "expires_at": (None if expires is None
                       else _instant(expires).isoformat(timespec="seconds")),
        "scope": _require_text(raw.get("scope"), "scope"),
        "subject_id": (str(raw.get("subject_id")).strip() if raw.get("subject_id") else None),
        "home_ref": (str(raw.get("home_ref")).strip() if raw.get("home_ref") else None),
        "requires_engagement": bool(raw.get("requires_engagement", True)),
        "atomic": bool(raw.get("atomic", False)),
        "evidence_ref": (str(raw.get("evidence_ref")).strip() if raw.get("evidence_ref") else None),
        "duration_seconds": raw.get("duration_seconds"),
        # v0.4 shared practice: the explicit materials the invitation names (already validated by
        # the transport).  The protocol carries them; it never opens anything by itself.
        "material_refs": materials,
    }


def parse_end(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One explicit end event, or verifiable finite completion evidence."""
    if not isinstance(raw, Mapping):
        raise CoPresenceError("END_NOT_A_MAPPING")
    if str(raw.get("event_kind") or raw.get("kind") or "").strip().upper() != KIND_END:
        raise CoPresenceError("NOT_AN_END_EVENT")
    invitation_id = _require_text(raw.get("invitation_id"), "invitation_id")
    occurred = _instant(raw.get("occurred_at"))
    if occurred is None:
        raise CoPresenceError("END_OCCURRED_AT_INVALID")
    completion_ref = str(raw.get("completion_evidence_ref") or "").strip()
    explicit = bool(raw.get("explicit_end", False))
    if not completion_ref and not explicit:
        raise CoPresenceError("END_NEEDS_EXPLICIT_END_OR_COMPLETION_EVIDENCE")
    return {
        "invitation_id": invitation_id,
        "occurred_at": occurred.isoformat(timespec="seconds"),
        "explicit_end": explicit,
        "completion_evidence_ref": completion_ref or None,
        "reason": str(raw.get("reason") or ("EXPLICIT_END" if explicit
                                            else "FINITE_COMPLETION")).strip(),
        "evidence_ref": (str(raw.get("evidence_ref")).strip() if raw.get("evidence_ref") else None),
    }


# ---------------------------------------------------------------------------------------------
# the protocol
# ---------------------------------------------------------------------------------------------


class FiniteCoPresence:
    """The finite co-presence protocol over one existing Life runtime.

    The runtime must be the one holding the canonical writer lease: every owner call below goes
    through it, and this class never opens a store of its own.
    """

    def __init__(self, runtime: Any, *, state_root: Path | str, subject_id: str,
                 home: str, cycle: Any = None,
                 canonical_subject: Optional[str] = None) -> None:
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.subject_id = _require_text(subject_id, "subject_id")
        self.home = _require_text(home, "home")
        self.cycle = cycle
        #: The *canonical* Life subject the owners speak (``chiyo.global``).  The instance's own
        #: subject (the Supply workspace subject) is a different concept: it is what an
        #: invitation must match, and it is deliberately not what LR-3/LR-4 are given.
        if canonical_subject:
            self.canonical_subject = _require_text(canonical_subject, "canonical_subject")
        else:
            try:
                import activity_continuity as ac

                self.canonical_subject = ac.GLOBAL_SUBJECT_ID
            except Exception:  # pragma: no cover - the owner is always importable here
                self.canonical_subject = "chiyo.global"

    # -- the session record (this protocol's own bookkeeping) ------------------------------

    @property
    def session_path(self) -> Path:
        return self.state_root / SESSION_FILENAME

    def read_session(self) -> dict[str, Any]:
        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": SESSION_SCHEMA, "state": "NONE"}
        except (OSError, ValueError) as exc:
            return {"schema_version": SESSION_SCHEMA, "state": "UNREADABLE",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        if not isinstance(data, Mapping):
            return {"schema_version": SESSION_SCHEMA, "state": "INVALID"}
        return dict(data)

    def _write_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.state_root.mkdir(parents=True, exist_ok=True)
        path = self.session_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)
        return dict(payload)

    def bind_practice(self, *, invitation_id: Any, practice_session_ref: Any,
                      event_ref: Any = None, now: Optional[str] = None) -> dict[str, Any]:
        """Record *which* shared-practice session this open joint time really opened.

        v0.4 shared practice: the content half is only the continuation of a joint time that
        was really accepted, so the binding is written here -- on the co-presence session the
        acceptance created -- and read back from it.  A session that is not open (``CLOSED``
        after an explicit end, ``PARKED_NO_JOINT`` when the joint Activity never came up), an
        invitation that names another session, or an empty reference is refused by name: an
        open practice artifact by itself is not consent to keep talking.
        """

        session = self.read_session()
        state = str(session.get("state") or "")
        if state != "OPEN":
            return {"ok": False, "reason_code": "PRACTICE_BIND_REQUIRES_OPEN_SESSION",
                    "state": state,
                    "detail": "only an open joint time can carry a shared practice"}
        if str(invitation_id or "") != str(session.get("invitation_id") or ""):
            return {"ok": False, "reason_code": "PRACTICE_BIND_FOR_ANOTHER_SESSION",
                    "invitation_id": session.get("invitation_id"),
                    "detail": "the practice must belong to this session's own invitation"}
        ref = _bounded_text(practice_session_ref, 200)
        if not ref:
            return {"ok": False, "reason_code": "PRACTICE_BIND_REQUIRES_SESSION_REF",
                    "detail": "no shared-practice session was named"}
        moment_iso = _iso(_instant(now) or datetime.now(timezone.utc))
        bound = dict(session)
        bound.update({"practice_session_ref": ref, "practice_bound_at": moment_iso,
                      "practice_invitation_event_ref": (_bounded_text(event_ref, 200) or None)})
        self._write_session(bound)
        return {"ok": True, "reason_code": "", "state": "OPEN",
                "session_ref": session_ref(str(session.get("invitation_id") or "")),
                "practice_session_ref": ref, "bound_at": moment_iso}

    # -- ordinary inbound messages ---------------------------------------------------------

    def observe(self, raw: Any) -> dict[str, Any]:
        """An ordinary inbound message: an observation, and nothing else.

        It is not stored on any owner, it does not pause anything and it does not start an
        Activity.  The answer says exactly that, so no caller can read it as engagement.
        """
        classified = classify(raw)
        if classified["kind"] == KIND_INVITATION:
            return {"effect": EFFECT_INVITATION, "kind": KIND_INVITATION,
                    "pause_requested": False, "activity_started": False,
                    "detail": "an explicit invitation is not an observation; consider() it"}
        return {"effect": EFFECT_OBSERVATION_ONLY, "kind": classified["kind"],
                "reason_code": classified.get("reason_code"),
                "pause_requested": False, "activity_started": False,
                "owners_written": 0,
                "detail": "an inbound message is observed only; the reply, if any, is a "
                          "read-only expression and needs no pause and no new Activity"}

    # -- the decision ----------------------------------------------------------------------

    def _life_view(self) -> dict[str, Any]:
        try:
            return dict(self.runtime.interruption_coordinator.get_current_life_view() or {})
        except Exception as exc:
            return {"read_error": "%s: %s" % (type(exc).__name__, exc)}

    def _foreground(self) -> Optional[dict[str, Any]]:
        for activity in self.runtime.activity_read.list_open_activities():
            if str(activity.get("status")) == "ACTIVE":
                return dict(activity)
        return None

    def _private_activity_after_pause(self, activity_id: str = "") -> Optional[dict[str, Any]]:
        """The interrupted private Activity as the owner reports it *after* the interruption.

        The pause is a canonical transition, so it bumps the Activity revision; parking the
        work has to use the revision the owner now holds, not the one read before the pause.
        ``activity_id`` is the Activity LR-3 actually assessed: with more than one open
        Activity (a WAITING one plus the interrupted one, say) the owner's list order is not a
        statement about which one was interrupted, and parking the wrong one fails on a stale
        revision and loses the joint time entirely.
        """
        if activity_id:
            try:
                row = self.runtime.activity_read.get_activity(str(activity_id))
            except Exception:
                row = None
            if isinstance(row, Mapping) and str(row.get("status")) in {"PAUSED", "ACTIVE",
                                                                       "WAITING"}:
                return dict(row)
            return None
        for activity in self.runtime.activity_read.list_open_activities():
            if str(activity.get("status")) in {"PAUSED", "ACTIVE", "WAITING"}:
                return dict(activity)
        return None

    def consider(self, invitation: Mapping[str, Any], *, now: Optional[str] = None) -> dict[str, Any]:
        """Accept, defer or decline one explicit invitation -- from the owners' own answers.

        Nothing is written here.  The refusals are the real reasons: a foreign subject or home,
        an expired invitation, the operator's pause, an atomic foreground Activity, a refused
        request budget, or an already open session.
        """
        moment = _instant(now) or datetime.now(timezone.utc)
        moment_iso = moment.isoformat(timespec="seconds")
        out: dict[str, Any] = {"invitation_id": invitation["invitation_id"],
                               "observed_at": moment_iso,
                               "requires_engagement": bool(invitation["requires_engagement"]),
                               "writes_performed": 0}
        if invitation.get("subject_id") and str(invitation["subject_id"]) != self.subject_id:
            out.update({"decision": DECISION_DECLINE, "reason_code": REASON_FOREIGN_SUBJECT,
                        "detail": "the invitation names subject %s, this instance is %s"
                                  % (invitation["subject_id"], self.subject_id)})
            return out
        if invitation.get("home_ref") and str(invitation["home_ref"]) != self.home:
            out.update({"decision": DECISION_DECLINE, "reason_code": REASON_FOREIGN_HOME,
                        "detail": "the invitation names another home"})
            return out
        expires = invitation.get("expires_at")
        if expires is not None and _instant(expires) < moment:
            out.update({"decision": DECISION_DECLINE, "reason_code": REASON_EXPIRED,
                        "detail": "the invitation expired at %s" % expires})
            return out
        if not invitation["requires_engagement"]:
            out.update({"decision": DECISION_DECLINE, "reason_code": REASON_NO_ENGAGEMENT,
                        "detail": "an invitation that needs no engagement is answered by an "
                                  "observation, not by a new Activity"})
            return out

        session = self.read_session()
        if str(session.get("state")) == "OPEN":
            out.update({"decision": DECISION_DEFER, "reason_code": REASON_ALREADY_OPEN,
                        "detail": "session %s is still open" % session.get("invitation_id")})
            return out

        import life_pause

        pause_view = life_pause.read(self.state_root)
        if pause_view.get("paused"):
            out.update({"decision": DECISION_DECLINE, "reason_code": REASON_OPERATOR_PAUSED,
                        "detail": "the operator's pause is set (%s); a global pause is not a "
                                  "social interruption" % pause_view.get("reason_code")})
            return out

        if bool(invitation.get("atomic")):
            out.update({"decision": DECISION_DECLINE, "reason_code": REASON_ATOMIC,
                        "detail": "the invitation declares itself atomic; it is not an "
                                  "interruption of anything"})
            return out

        life = self._life_view()
        interruptibility = str(life.get("interruptibility") or "")
        foreground = self._foreground()
        if life.get("read_error"):
            out.update({"decision": DECISION_DEFER, "reason_code": "LR3_LIFE_VIEW_UNREADABLE",
                        "detail": life["read_error"]})
            return out
        if foreground is not None and interruptibility == "ATOMIC":
            out.update({"decision": DECISION_DECLINE, "reason_code": REASON_ATOMIC,
                        "detail": "the foreground Activity is atomic; it is never force-switched",
                        "foreground_activity_ref": foreground.get("activity_id")})
            return out
        if foreground is None:
            # v0.4 B: an idle instance may accept.  With no private Activity there is nothing to
            # interrupt and nothing to park, so the joint time is simply the next Activity -- this
            # is *not* a global refusal, and it is not a deferral either: nothing is waiting on a
            # save point that does not exist.  An instance with no private checkpoint is exactly
            # this case.
            out.update({"decision": DECISION_ACCEPT,
                        "reason_code": REASON_IDLE_ACCEPT,
                        "detail": "there is no private Activity to interrupt, so the joint time "
                                  "starts as an ordinary Activity with no save point to keep",
                        "idle": True,
                        "interruptibility": interruptibility or None})
            return out

        requests = getattr(getattr(self.cycle, "budget", None), "requests", None)
        if requests is not None:
            verdict = dict(requests.check())
            if verdict.get("allowed") is not True:
                out.update({"decision": DECISION_DEFER, "reason_code": REASON_BUDGET,
                            "detail": "the request ledger refuses: %s"
                                      % verdict.get("reason_code")})
                return out
        out.update({"decision": DECISION_ACCEPT,
                    "reason_code": "INTERRUPTIBLE_PRIVATE_ACTIVITY",
                    "detail": "the private Activity is interruptible; the joint time will be a "
                              "normal single foreground Activity after a canonical pause",
                    "foreground_activity_ref": foreground.get("activity_id"),
                    "foreground_revision": foreground.get("revision"),
                    "interruptibility": interruptibility or None,
                    "private_checkpoint_ref": foreground.get("checkpoint_ref")})
        return out

    # -- beginning the joint time ----------------------------------------------------------

    def begin(self, invitation: Mapping[str, Any], *, now: Optional[str] = None,
              allow_open_without_pause: bool = False) -> dict[str, Any]:
        """Interrupt the private work legally, then let the Decision create the joint Activity.

        Order, all of it through the owners:

        1. LR-3 assesses an explicit hold request and applies the canonical pause at a safe
           boundary (``apply_immediate_pause=True``) -- or refuses, and then nothing else runs;
        2. the private Activity enters the owner's own WAITING state with an ``EVENT_MATCH``
           resume condition on this session's end event, releasing the foreground;
        3. the invitation is ingested as a *typed user request*, so AG-0 can offer it;
        4. one Decision+Adoption cycle runs; the joint Activity exists only if the committed
           Decision selected that candidate.  No second foreground is opened by force.
        """
        decision = self.consider(invitation, now=now)
        if decision["decision"] != DECISION_ACCEPT:
            return {"ok": False, "stage": "CONSIDER", "decision": decision,
                    "reason_code": decision["reason_code"], "pause_applied": False,
                    "activity_started": False}
        moment_iso = _iso(_instant(now))
        import activity_interruption as ai

        event = ai.CommunicationEventAdapter.adapt({
            "event_id": "comm_evt:co_presence:%s" % invitation["invitation_id"],
            "subject_id": self.canonical_subject,
            "channel_ref": "channel:co_presence",
            "conversation_ref": "conv:co_presence:%s" % invitation["invitation_id"],
            "message_ref": "msg:co_presence:%s" % invitation["invitation_id"],
            "message_class": ai.MESSAGE_CLASS_EXPLICIT_HOLD_REQUEST,
            "request_boundary_pause": False,
            "occurred_at": invitation["occurred_at"],
            "observed_at": moment_iso,
            "recorded_at": moment_iso,
            "enqueued_at": moment_iso,
            "source_refs": ["channel:co_presence", invitation["source_ref"]],
        })
        assessments = self.runtime.interruption_coordinator.assess_events(
            [event], apply_immediate_pause=True)
        assessment = dict(assessments[-1]) if assessments else {}
        effect = str(assessment.get("activity_effect") or "")
        out: dict[str, Any] = {"ok": True, "stage": "INTERRUPTED",
                               "assessment": assessment, "activity_effect": effect,
                               "pause_applied": effect == ai.EFFECT_INTERRUPT_NOW,
                               "applied_transition_ref": assessment.get("applied_transition_ref"),
                               "decision": decision}
        if effect != ai.EFFECT_INTERRUPT_NOW:
            # The owner decided this interruption is not to be applied now (for example a
            # boundary that must be reached first).  Nothing else is started: the joint time
            # would otherwise open on top of an active private Activity.
            out.update({"ok": False, "stage": "NOT_INTERRUPTED",
                        "reason_code": "INTERRUPTION_NOT_APPLIED_NOW",
                        "activity_started": False,
                        "detail": "the owner answered %s; the invitation must be re-considered "
                                  "after the boundary" % effect})
            return out

        waiting = self._park_private_activity(
            invitation, moment_iso,
            activity_id=str(assessment.get("activity_id")
                            or decision.get("foreground_activity_ref") or ""))
        out["private_waiting"] = waiting
        if not waiting.get("ok"):
            # The private work could not be parked, so the single foreground was never released:
            # no joint Activity may be started, and this is *not* a success.  The pause already
            # applied by LR-3 is reported as the recoverable state it is.
            out.update({"ok": False, "stage": "PRIVATE_PARK_REFUSED",
                        "reason_code": waiting.get("reason_code") or "PRIVATE_WAIT_REFUSED",
                        "activity_started": False,
                        "private_recovery": self._private_recovery(decision),
                        "detail": "the private Activity could not enter the owner's WAITING "
                                  "state, so the joint time was not started; the private work "
                                  "is still the owner's and can be resumed normally"})
            return out
        out["request"] = self._ingest_invitation(invitation, moment_iso)
        adoption = self._adopt_joint_activity(invitation, moment_iso)
        out["adoption"] = adoption
        joint_ref = adoption.get("activity_ref")
        applied = (bool(adoption.get("ok"))
                   and str(adoption.get("adoption_status") or "") == "APPLIED"
                   and bool(joint_ref))
        if not applied:
            # The Decision did not commit, or AG-2 did not adopt it.  There is no joint Activity,
            # so this is not a started joint time -- and the private save point must stay
            # recoverable.
            out.update({"ok": False, "stage": "ADOPTION_NOT_APPLIED",
                        "reason_code": (str(adoption.get("reason_code"))
                                        if not adoption.get("ok")
                                        else "ADOPTION_STATUS_NOT_APPLIED"),
                        "activity_started": False,
                        "private_recovery": self._private_recovery(decision, waiting),
                        "detail": "the joint Activity was not adopted (decision=%s "
                                  "adoption_status=%s); the private work stays in the owner's "
                                  "WAITING state with its resume condition, so the end event (or "
                                  "a resume eligibility) brings it back"
                                  % (adoption.get("decision"), adoption.get("adoption_status"))})
            return out
        session = {
            "schema_version": SESSION_SCHEMA,
            "state": "OPEN",
            "invitation_id": invitation["invitation_id"],
            "source_ref": invitation["source_ref"],
            "scope": invitation["scope"],
            "subject_id": self.subject_id,
            "home": self.home,
            "opened_at": moment_iso,
            "expires_at": invitation.get("expires_at"),
            "private_activity_ref": ((waiting or {}).get("activity_id")
                                     or decision.get("foreground_activity_ref")),
            "private_checkpoint_ref": decision.get("private_checkpoint_ref"),
            "pause_transition_ref": assessment.get("applied_transition_ref"),
            "resume_condition_ref": (waiting or {}).get("condition_id"),
            "joint_activity_ref": joint_ref,
            "joint_activity_status": "ACTIVE",
            "single_foreground": True,
            "true_concurrency": "DEFERRED_BEYOND_CURRENT_CONTRACT",
        }
        self._write_session(session)
        out["session"] = session
        return out

    def _private_recovery(self, decision: Mapping[str, Any],
                          waiting: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """What the private work looks like after a failed ``begin`` -- and how it comes back."""
        activity_ref = ((waiting or {}).get("activity_id")
                        or decision.get("foreground_activity_ref"))
        return {
            "activity_ref": activity_ref,
            "checkpoint_ref": decision.get("private_checkpoint_ref"),
            "resume_condition_ref": (waiting or {}).get("condition_id"),
            "activity": self._activity_view(activity_ref),
            "recoverable": bool(activity_ref),
            "how": "the private Activity stays in the owner's own state; a new resume "
                   "eligibility (or the session's end event) is what brings it back, through "
                   "AG-0 -> AG-1 Decision -> AG-2 Adoption",
        }


    def _park_private_activity(self, invitation: Mapping[str, Any],
                               moment_iso: str, *, activity_id: str = "") -> dict[str, Any]:
        """Put the private work into the owner's own WAITING state, waiting for the end event.

        This is the save point the joint time must return to: the Activity stays the owner's,
        with a real resume condition, and the foreground is released so the joint Activity can
        legally become the single foreground.  A canonical pause alone does **not** release the
        single foreground -- LR-2 still refuses a second START -- so this step is what makes the
        joint time possible at all.
        """
        activity = self._private_activity_after_pause(activity_id)
        activity_id = str((activity or {}).get("activity_id") or "")
        revision = (activity or {}).get("revision")
        if not activity_id or revision is None:
            return {"ok": False, "reason_code": "NO_PRIVATE_ACTIVITY_TO_PARK"}
        import activity_waiting_agenda as awa

        try:
            result = self.runtime.waiting_coordinator.enter_waiting_with_condition(
                activity_id=activity_id, expected_revision=int(revision),
                waiting_on_ref=session_ref(invitation["invitation_id"]),
                condition_kind=awa.COND_KIND_EVENT_MATCH,
                parameters={"expected_event_kind": KIND_END,
                            "expected_ref": session_ref(invitation["invitation_id"])},
                source_refs=[session_ref(invitation["invitation_id"]),
                             invitation["source_ref"]],
                idempotency_key="co_presence:wait:%s" % invitation["invitation_id"],
                release_foreground=True,
                occurred_at=moment_iso, observed_at=moment_iso)
        except Exception as exc:
            return {"ok": False, "reason_code": "PRIVATE_WAIT_REFUSED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        activity = getattr(result, "activity", None) or {}
        return {"ok": True, "activity_id": activity_id,
                "status": activity.get("status"),
                "revision": activity.get("revision"),
                "condition_id": getattr(result, "resume_condition_ref", None)
                                or (result.get("resume_condition_ref")
                                    if isinstance(result, Mapping) else None),
                "raw": (result.to_dict() if hasattr(result, "to_dict") else None)}

    def _ingest_invitation(self, invitation: Mapping[str, Any],
                           moment_iso: str) -> dict[str, Any]:
        """The invitation becomes a typed user request -- the only way AG-0 may offer it."""
        source = getattr(self.runtime, "user_request_source", None)
        if source is None:
            return {"ok": False, "reason_code": "NO_USER_REQUEST_SOURCE"}
        event = {
            "event_id": "ureq:co_presence:%s" % invitation["invitation_id"],
            "channel": "channel:co_presence",
            "event_kind": "EXPLICIT_USER_REQUEST",
            "request_status": "OPEN",
            "target_refs": ["co_presence:%s" % invitation["invitation_id"]],
            "occurred_at": invitation["occurred_at"],
            "observed_at": moment_iso,
            "revision": 1,
            "valid_from": invitation["occurred_at"],
            "valid_until": invitation.get("expires_at"),
        }
        try:
            record = source.ingest_typed_event(event)
        except Exception as exc:
            return {"ok": False, "reason_code": "TYPED_REQUEST_REFUSED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        return {"ok": True, "event_id": getattr(record, "event_id", event["event_id"]),
                "request_status": getattr(record, "request_status", "OPEN")}

    def _joint_adoption_spec(self, invitation: Mapping[str, Any]) -> dict[str, Any]:
        """The adoption spec one joint time is created with -- built once, used by both paths."""
        import activity_progress_completion_lr5 as lr5
        import life_integration_ag2 as ag2

        reference = session_effect_ref(invitation["invitation_id"])
        spec = ag2.ActivityAdoptionSpec(
            activity_kind="FINITE_CO_PRESENCE",
            title="有限共同时间：%s" % invitation["scope"],
            target_refs=[reference],
            origin_ref=reference,
            # The joint time is bounded by the owner's own external result: the explicit end
            # event is what completes it, and no single write may declare it over.
            progress_contract_spec={
                "progress_kind": lr5.PROGRESS_KIND_WAIT_FOR_EXTERNAL_RESULT,
                "checkpoint_semantics": lr5.CHECKPOINT_SEMANTICS_TARGET_STATE,
                "target_refs": [reference],
                "policy_version": lr5.POLICY_EXTERNAL_RESULT_V1,
            },
            completion_contract_spec={
                "criteria_kind": lr5.CRITERIA_EXTERNAL_RESULT_MATCH,
                "target_refs": [reference],
                # LR-5 requires one of required_predicate / required_effect_ref /
                # expected_artifact_ref.  The criterion is the *external result* that names this
                # session: the explicit inbound end event.  Its authority is
                # ``external_result_authority`` -- an external result, never AR-1's
                # ``result_settlement_authority`` (this protocol does not own that).
                "criteria": {"required_effect_ref": reference},
                "required_evidence_kinds": [lr5.SOURCE_KIND_EXTERNAL_RESULT],
                "required_authority_domains": ["external_result_authority"],
                "policy_version": lr5.POLICY_EXTERNAL_RESULT_V1,
            },
            result_binding_spec={
                "target_refs": [reference],
                "allowed_evidence_kinds": [lr5.SOURCE_KIND_EXTERNAL_RESULT],
                "authority_domains": ["external_result_authority"],
                "policy_version": lr5.POLICY_EXTERNAL_RESULT_V1,
            },
        )
        return spec.to_dict()

    def _adopt_joint_activity(self, invitation: Mapping[str, Any],
                              moment_iso: str) -> dict[str, Any]:
        """One real Decision+Adoption cycle.  The Decision decides, not this module."""
        import agency_decision_ag1 as ag1

        reference = session_effect_ref(invitation["invitation_id"])
        try:
            out = self.runtime.coordinator.run_decision_and_adoption_cycle(
                trigger_kind=ag1.TRIGGER_USER_REQUEST_EVENT,
                trigger_ref=reference,
                observed_at=moment_iso,
                adoption_spec=self._joint_adoption_spec(invitation),
                execute_initial_step=False)
        except Exception as exc:
            return {"ok": False, "reason_code": "DECISION_CYCLE_REFUSED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        record = (out.get("decision_result") or {}).get("decision_record")
        adoption = (out.get("adoption_result") or {}).get("adoption_record")
        integration = out.get("integration_record")
        return {
            "ok": True,
            "correlation_id": out.get("correlation_id"),
            "decision": getattr(record, "decision", None),
            "selected_candidate_ref": getattr(record, "selected_candidate_ref", None),
            "adoption_status": getattr(adoption, "adoption_status", None),
            "activity_ref": getattr(integration, "activity_ref", None),
            "single_foreground": True,
        }

    # -- the decision-first path (v0.4 B: the resident's own route) ------------------------

    def begin_from_decision(self, invitation: Mapping[str, Any], *,
                            now: Optional[str] = None) -> dict[str, Any]:
        """Accept, defer or decline one invitation -- **the Decision decides first**, once.

        Order, and why it is this order:

        1. ``consider``: the owners' own legality gate (foreign subject or home, expiry, the
           operator's pause, an ATOMIC foreground, the request ledger).  An idle instance accepts
           here -- no private save point is not a refusal;
        2. **one** AG-1 Decision, reserved against the instance's own request ledger *before it is
           sent* (``BudgetedCognitionAdapter``), with the bounded ``contact`` situation.  Nothing
           has touched an Activity at this point: NO_ACTION, DEFER, WAIT, a decline, an exhausted
           budget or a failed/exception-raising cognition all leave the running work exactly as
           its owner had it -- same status, same revision, same checkpoint, same foreground, and
           no session record anywhere;
        3. only when that committed Decision says START does AG-2 adopt it, and the *adoption*
           hands the foreground over: LR-4's own WAITING transition with a resume condition on
           this session's end event (bounded, so a silent invitation cannot keep the book shut),
           then the joint Activity.  The handoff is CAS-checked inside the owner and a refusal
           raises **before** anything is written;
        4. if the start fails *after* the handoff, the condition is released through LR-4's own
           event path (a resume eligibility), the outcome is recorded as UNKNOWN with a named
           reason, and a ``PARKED_NO_JOINT`` session is kept so an explicit end still reconciles.

        One invitation costs **one** decision request (one reservation), and a deferral names its
        condition so the event is never re-asked on the next tick.
        """
        import agency_decision_ag1 as ag1
        import life_integration_ag2 as ag2
        import life_request_budget as lrb

        moment = _instant(now) or datetime.now(timezone.utc)
        moment_iso = moment.isoformat(timespec="seconds")
        decision = self.consider(invitation, now=moment_iso)
        out: dict[str, Any] = {
            "ok": False, "stage": "CONSIDER", "decision": decision["decision"],
            "reason_code": decision["reason_code"], "detail": decision.get("detail", ""),
            "llm_calls": 0, "session": None, "session_ref": None,
            "joint_activity_ref": None, "private_activity_ref": None,
            "decision_record_ref": None, "selected_candidate_ref": None,
            "pause_applied": False, "activity_started": False, "park_applied": False,
            "handoff": None, "idle": bool(decision.get("idle")),
        }
        if decision["decision"] != DECISION_ACCEPT:
            return out

        reference = session_effect_ref(invitation["invitation_id"])
        request = self._ingest_invitation(invitation, moment_iso)

        # -- the one decision request, reserved before it is sent ---------------------------
        service = getattr(self.runtime, "decision_service", None)
        original = getattr(service, "cognition_adapter", None)
        ledger = getattr(getattr(self.cycle, "budget", None), "requests", None)
        budget = getattr(ledger, "reserve", None)
        # The instance's own ceiling, read before anything is sent: a spent ledger means this
        # invitation costs **no** request at all (and therefore cannot touch an Activity either).
        if ledger is not None and hasattr(ledger, "check"):
            verdict = dict(ledger.check() or {})
            if verdict.get("allowed") is not True:
                out.update({"stage": "BUDGET_REFUSED", "decision": DECISION_DEFER,
                            "reason_code": str(verdict.get("reason_code")
                                               or "REQUEST_BUDGET_REFUSED"),
                            "detail": "the request ledger refuses this invitation: %s"
                                      % verdict.get("reason_code"),
                            "llm_calls": 0, "request": request})
                return out
        wrapped = None
        assigned_wrapper = False
        if isinstance(original, lrb.BudgetedCognitionAdapter) and original.budget is ledger:
            wrapped = original
        elif budget is not None and original is not None:
            admission = (self._admission()
                         if getattr(original, "send_split_capable", False) else None)
            wrapped = lrb.BudgetedCognitionAdapter(original, ledger, admission=admission)
            service.cognition_adapter = wrapped
            assigned_wrapper = True
        attempt_start = len(getattr(wrapped, "attempts", []) or [])
        attempts = lambda: len(getattr(wrapped, "attempts", []) or []) - attempt_start
        try:
            cycle_out = self.runtime.coordinator.run_decision_and_adoption_cycle(
                trigger_kind=ag1.TRIGGER_USER_REQUEST_EVENT,
                trigger_ref=reference,
                observed_at=moment_iso,
                adoption_spec=self._joint_adoption_spec(invitation),
                # The handoff is built from the *owner's own* current foreground, and the owner
                # CAS-checks it again when it runs; nothing is cleared here.
                handoff_spec=self._handoff_spec_for(invitation, moment_iso),
                execute_initial_step=False,
                # The Decision's bounded view of what is being asked: the invitation's scope
                # label and the session state.  No body, no private material, no authority.
                situation_context={"contact": self.contact_context(invitation)})
        except ag2.ForegroundHandoffStranded as stranded:
            release = self._release_handoff(stranded.handoff, moment_iso)
            session = self._parked_session(invitation, moment_iso, decision,
                                           stranded.handoff, "HANDOFF_STRANDED")
            self._write_session(session)
            out.update({"stage": "HANDOFF_STRANDED", "decision": "",
                        "reason_code": "JOINT_START_FAILED_AFTER_HANDOFF",
                        "detail": _bounded_text(stranded.cause, 300),
                        "llm_calls": max(1, attempts()), "request": request,
                        "handoff": dict(stranded.handoff), "release": release,
                        "park_applied": True, "session": session,
                        "private_activity_ref": stranded.handoff.get("activity_ref"),
                        "private_recovery": self._recovery_facts(decision, stranded.handoff,
                                                                 release)})
            return out
        except lrb.RequestBudgetError as exc:
            out.update({"stage": "BUDGET_REFUSED", "decision": DECISION_DEFER,
                        "reason_code": str(getattr(exc, "reason_code", "") or "REQUEST_BUDGET_REFUSED"),
                        "detail": _bounded_text(getattr(exc, "detail", "") or str(exc), 300),
                        "llm_calls": 0, "request": request})
            return out
        except Exception as exc:
            out.update({"stage": "DECISION_CYCLE_REFUSED", "decision": "",
                        "reason_code": "DECISION_CYCLE_REFUSED",
                        "detail": _bounded_text("%s: %s" % (type(exc).__name__, exc), 300),
                        "llm_calls": attempts(), "request": request})
            return out
        finally:
            if assigned_wrapper and original is not None:
                try:
                    service.cognition_adapter = original
                except Exception:
                    pass

        record = (cycle_out.get("decision_result") or {}).get("decision_record")
        adoption = (cycle_out.get("adoption_result") or {}).get("adoption_record")
        integration = cycle_out.get("integration_record")
        handoff = ((cycle_out.get("adoption_result") or {}).get("handoff_result") or None)
        chosen = str(getattr(record, "decision", "") or "")
        adoption_status = str(getattr(adoption, "adoption_status", "") or "")
        joint_ref = getattr(integration, "activity_ref", None)
        out.update({"llm_calls": max(1, attempts()), "request": request, "decided": chosen,
                    "decision_record_ref": getattr(record, "decision_id", None),
                    "selected_candidate_ref": getattr(record, "selected_candidate_ref", None),
                    "adoption_status": adoption_status,
                    "joint_activity_ref": joint_ref,
                    "handoff": dict(handoff) if isinstance(handoff, Mapping) else None,
                    "park_applied": bool(handoff),
                    "private_activity_ref": (handoff or {}).get("activity_ref")})
        if record is None or not chosen:
            failed = dict(cycle_out.get("decision_result") or {})
            out.update({"decision": "", "stage": "DECISION_NOT_COMMITTED",
                        "reason_code": "DECISION_NOT_COMMITTED",
                        "failure_class": str(failed.get("failure_class")
                                             or failed.get("stale_reason") or "DECISION_NOT_COMMITTED"),
                        "detail": "no committed Decision exists; this is UNKNOWN, not a refusal"})
            return out
        accepted = adoption_status == "APPLIED" and bool(joint_ref)
        if accepted:
            session = {
                "schema_version": SESSION_SCHEMA,
                "state": "OPEN",
                "invitation_id": invitation["invitation_id"],
                "source_ref": invitation["source_ref"],
                "scope": invitation["scope"],
                "subject_id": self.subject_id,
                "home": self.home,
                "opened_at": moment_iso,
                "expires_at": invitation.get("expires_at"),
                "private_activity_ref": (handoff or {}).get("activity_ref"),
                "private_checkpoint_ref": decision.get("private_checkpoint_ref"),
                "pause_transition_ref": None,
                "resume_condition_ref": (handoff or {}).get("resume_condition_ref"),
                "resume_condition_expires_at": (handoff or {}).get("condition_expires_at"),
                "joint_activity_ref": joint_ref,
                "joint_activity_status": "ACTIVE",
                "single_foreground": True,
                "true_concurrency": "DEFERRED_BEYOND_CURRENT_CONTRACT",
                "decision_ref": out.get("decision_record_ref"),
            }
            self._write_session(session)
            out.update({"ok": True, "stage": "JOINT_OPEN", "decision": DECISION_ACCEPT,
                        "reason_code": "CO_PRESENCE_STARTED",
                        "detail": "the Decision accepted; the adoption handed the foreground "
                                  "over and started the joint Activity",
                        "session": session,
                        "session_ref": "co_presence:%s" % invitation["invitation_id"],
                        "activity_started": True,
                        "private_recovery": self._recovery_facts(decision, handoff, None)})
            return out

        # -- not accepted: nothing was handed over, and nothing is left hanging --------------
        if chosen in {ag1.DECISION_WAIT, ag1.DECISION_DEFER}:
            out.update({"decision": DECISION_DEFER, "stage": "DEFERRED_BY_DECISION",
                        "reason_code": ("DECISION_WAIT" if chosen == ag1.DECISION_WAIT
                                        else "DECISION_DEFER"),
                        "detail": "the Decision chose %s; the running work is untouched and the "
                                  "invitation is answered later" % chosen})
        elif chosen == ag1.DECISION_START:
            out.update({"decision": "", "stage": "ADOPTION_NOT_APPLIED",
                        "reason_code": "ADOPTION_NOT_APPLIED",
                        "detail": "the Decision chose START but the adoption did not apply "
                                  "(status=%s)" % (adoption_status or "NONE")})
        else:
            out.update({"decision": DECISION_DECLINE, "stage": "DECLINED_BY_DECISION",
                        "reason_code": "DECISION_%s" % (chosen or "UNKNOWN"),
                        "detail": "the Decision chose %s, not the joint time"
                                  % (chosen or "nothing")})
        out["decision_record_ref"] = out.get("decision_record_ref")
        return out

    def _admission(self):
        """The instance's own request-start handshake (the operator's pause bit)."""
        try:
            import life_pause
        except Exception:  # pragma: no cover - the owner is always importable here
            return None

        root = self.state_root

        def _admit(kind: str, schema_retry_index: int = 0) -> None:
            life_pause.admit_request(root, stage="before_%s_request" % str(kind or "request"))

        return _admit

    def _handoff_spec_for(self, invitation: Mapping[str, Any],
                          moment_iso: str) -> Optional[dict[str, Any]]:
        """The bounded handoff this adoption would perform, or ``None`` when nothing is running."""
        foreground = self._foreground()
        if not foreground:
            return None
        activity_id = str(foreground.get("activity_id") or "")
        revision = foreground.get("revision")
        if not activity_id or revision is None:
            return None
        import activity_waiting_agenda as awa

        return {
            "activity_ref": activity_id,
            "expected_revision": int(revision),
            "waiting_on_ref": session_ref(invitation["invitation_id"]),
            "condition_kind": awa.COND_KIND_EVENT_MATCH,
            "parameters": {"expected_event_kind": KIND_END,
                           "expected_ref": session_ref(invitation["invitation_id"])},
            "source_refs": [session_ref(invitation["invitation_id"]),
                            invitation["source_ref"]],
            "idempotency_key": "co_presence:wait:%s" % invitation["invitation_id"],
            "condition_expires_at": self._wait_until(invitation, moment_iso),
        }

    def _wait_until(self, invitation: Mapping[str, Any], moment_iso: str) -> str:
        """The bounded window this wait may last -- never an open-ended stop of her own work."""
        window = invitation.get("duration_seconds")
        try:
            window_s = int(window) if window is not None else DEFAULT_WAIT_SECONDS
        except (TypeError, ValueError):
            window_s = DEFAULT_WAIT_SECONDS
        window_s = max(MIN_WAIT_SECONDS, min(window_s, MAX_WAIT_SECONDS))
        return invitation.get("expires_at") or _iso(
            _instant(moment_iso) + timedelta(seconds=window_s))

    def _release_handoff(self, handoff: Mapping[str, Any],
                         moment_iso: str) -> dict[str, Any]:
        """Let LR-4 release a handoff that never became a joint time (its own event path)."""
        event = {
            "event_id": "cevt:co_presence_handoff_release:%s" % handoff.get("activity_ref"),
            "event_category": "EXTERNAL",
            "event_kind": KIND_END,
            "occurred_at": moment_iso,
            "observed_at": moment_iso,
            "recorded_at": moment_iso,
            "enqueued_at": moment_iso,
            "source_refs": [str(handoff.get("waiting_on_ref") or "")],
            "waiting_on_ref": str(handoff.get("waiting_on_ref") or ""),
        }
        try:
            evaluation = self.runtime.waiting_coordinator.evaluate_event(event)
        except Exception as exc:
            return {"ok": False, "reason_code": "HANDOFF_RELEASE_REFUSED",
                    "detail": _bounded_text("%s: %s" % (type(exc).__name__, exc), 200)}
        return {"ok": True,
                "new_eligibility_refs": list(evaluation.get("emitted_eligibilities") or []),
                "resume_requires_decision": True}

    def _parked_session(self, invitation: Mapping[str, Any], moment_iso: str,
                        decision: Mapping[str, Any], handoff: Mapping[str, Any],
                        reason_code: str) -> dict[str, Any]:
        """The record an explicit end can still be matched to after a stranded handoff."""
        return {
            "schema_version": SESSION_SCHEMA, "state": "PARKED_NO_JOINT",
            "invitation_id": invitation["invitation_id"],
            "source_ref": invitation["source_ref"], "scope": invitation["scope"],
            "subject_id": self.subject_id, "home": self.home,
            "opened_at": moment_iso, "expires_at": invitation.get("expires_at"),
            "private_activity_ref": handoff.get("activity_ref"),
            "private_checkpoint_ref": decision.get("private_checkpoint_ref"),
            "resume_condition_ref": handoff.get("resume_condition_ref"),
            "resume_condition_expires_at": handoff.get("condition_expires_at"),
            "joint_activity_ref": None, "joint_activity_status": None,
            "single_foreground": True,
            "true_concurrency": "DEFERRED_BEYOND_CURRENT_CONTRACT",
            "reason_code": reason_code,
        }

    def _assess_hold_request(self, invitation: Mapping[str, Any],
                             moment_iso: str) -> dict[str, Any]:
        """LR-3's own verdict on this explicit hold request -- an assessment, not a pause."""
        import activity_interruption as ai

        try:
            event = ai.CommunicationEventAdapter.adapt({
                "event_id": "comm_evt:co_presence:%s" % invitation["invitation_id"],
                "subject_id": self.canonical_subject,
                "channel_ref": "channel:co_presence",
                "conversation_ref": "conv:co_presence:%s" % invitation["invitation_id"],
                "message_ref": "msg:co_presence:%s" % invitation["invitation_id"],
                "message_class": ai.MESSAGE_CLASS_EXPLICIT_HOLD_REQUEST,
                "request_boundary_pause": False,
                "occurred_at": invitation["occurred_at"],
                "observed_at": moment_iso,
                "recorded_at": moment_iso,
                "enqueued_at": moment_iso,
                "source_refs": ["channel:co_presence", invitation["source_ref"]],
            })
            assessments = self.runtime.interruption_coordinator.assess_events(
                [event], apply_immediate_pause=False)
        except Exception as exc:
            return {"read_error": "%s: %s" % (type(exc).__name__, exc)}
        return dict(assessments[-1]) if assessments else {}

    def _recovery_facts(self, decision: Mapping[str, Any], handoff: Any,
                        release: Any) -> dict[str, Any]:
        """What the private work looks like now, and how it comes back -- stated, not implied."""
        held = dict(handoff or {}) if isinstance(handoff, Mapping) else {}
        activity_ref = held.get("activity_ref") or decision.get("foreground_activity_ref")
        if held:
            how = ("the private Activity is WAITING under LR-4 with the session's end condition "
                   "(and its own expiry); the end event or that expiry opens a resume "
                   "eligibility, and a new Decision is what resumes it")
            if isinstance(release, Mapping) and release.get("new_eligibility_refs"):
                how = ("the wait was already released through LR-4 and a resume eligibility is "
                       "open; a new Decision is what resumes it")
        else:
            how = "nothing was stopped: the private Activity is exactly as its owner left it"
        return {
            "activity_ref": activity_ref,
            "activity": self._activity_view(activity_ref),
            "checkpoint_ref": decision.get("private_checkpoint_ref"),
            "resume_condition_ref": held.get("resume_condition_ref"),
            "recoverable": True,
            "how": how,
        }

    # -- ending the joint time -------------------------------------------------------------

    def end(self, end_event: Mapping[str, Any], *, now: Optional[str] = None) -> dict[str, Any]:
        """End the joint time: the explicit end event completes the joint Activity for real.

        Order, all of it through the owners:

        1. LR-4 ``evaluate_event``: the explicit end event satisfies the *private* work's
           ``EVENT_MATCH`` resume condition and the owner emits a **new resume eligibility**;
        2. LR-5 ``ingest_progress_evidence``: the same explicit inbound end event is the
           ``EXTERNAL_CANONICAL_RESULT`` that satisfies the joint Activity's registered
           completion contract, so LR-5 creates the completion eligibility and (with the
           coordinator's own ``auto_apply_natural_completion``) completes the Activity through
           LR-2 -- the joint Activity is read back from the owner to confirm it is terminal;
        3. both Activities are read back from the owner and reported as they are.

        The evidence authority is ``external_result_authority``: the co-presence end event is
        an external result, **not** an AR-1 settled action result, and this module never claims
        ``result_settlement_authority``.  The event is only accepted with explicit provenance
        (``parse_end`` requires an explicit end flag or completion evidence, plus source and
        time), and the same session can be ended only once: a second call is an idempotent
        replay, never a second completion.
        """
        session = self.read_session()
        state = str(session.get("state") or "")
        if state == "CLOSED":
            if str(end_event["invitation_id"]) != str(session.get("invitation_id")):
                raise CoPresenceError("END_EVENT_IS_FOR_ANOTHER_SESSION")
            return {"ok": True, "already_closed": True, "session": session,
                    "joint_activity": session.get("joint_activity") or {},
                    "private_activity": session.get("private_activity") or {},
                    "new_eligibility_refs": list(session.get("resume_eligibilities") or []),
                    "resume_requires_decision": True,
                    "replayed": True,
                    "true_concurrency": "DEFERRED_BEYOND_CURRENT_CONTRACT"}
        if state not in {"OPEN", "PARKED_NO_JOINT"}:
            raise CoPresenceError("NO_OPEN_SESSION")
        if str(end_event["invitation_id"]) != str(session.get("invitation_id")):
            raise CoPresenceError("END_EVENT_IS_FOR_ANOTHER_SESSION")
        moment_iso = _iso(_instant(now) or _instant(end_event["occurred_at"]))
        event = {
            "event_id": "cevt:co_presence_end:%s" % end_event["invitation_id"],
            "event_category": "EXTERNAL",
            "event_kind": KIND_END,
            "occurred_at": end_event["occurred_at"],
            "observed_at": moment_iso,
            "recorded_at": moment_iso,
            "enqueued_at": moment_iso,
            "source_refs": [session_ref(end_event["invitation_id"])],
            "waiting_on_ref": session_ref(end_event["invitation_id"]),
        }
        try:
            evaluation = self.runtime.waiting_coordinator.evaluate_event(event)
        except Exception as exc:
            raise CoPresenceError("END_EVENT_REFUSED: %s: %s" % (type(exc).__name__, exc))
        completion = self._complete_joint_activity(session, end_event, moment_iso)
        request = self._close_request(session, end_event, moment_iso)
        joint = self._activity_view(session.get("joint_activity_ref"))
        private = self._activity_view(session.get("private_activity_ref"))
        closed = dict(session)
        closed.update({"state": "CLOSED", "closed_at": moment_iso,
                       "end_reason": end_event["reason"],
                       "end_evidence_ref": end_event.get("completion_evidence_ref")
                                           or end_event.get("evidence_ref"),
                       "explicit_end": bool(end_event["explicit_end"]),
                       "resume_eligibilities": list(evaluation.get("emitted_eligibilities") or []),
                       "joint_activity": joint,
                       "private_activity": private,
                       "completion": completion,
                       "request": request})
        self._write_session(closed)
        return {
            "ok": bool(completion.get("ok")),
            "already_closed": False,
            "session": closed,
            "evaluation": dict(evaluation),
            "new_eligibility_refs": list(evaluation.get("emitted_eligibilities") or []),
            "joint_activity": joint,
            "joint_completion": completion,
            "private_activity": private,
            "private_save_point": {"activity_ref": session.get("private_activity_ref"),
                                   "checkpoint_ref": session.get("private_checkpoint_ref")},
            "resume_requires_decision": True,
            "resume_path": "the new eligibility goes through AG-0 -> AG-1 Decision -> AG-2 "
                           "Adoption (LR-4 explicit resume); this module does not resume it",
            "true_concurrency": "DEFERRED_BEYOND_CURRENT_CONTRACT",
        }

    def contact_context(self, invitation: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """The bounded local-contact block one Decision may see -- no body, no private material.

        It says what an invitation *is* and what accepting it would mean for the running
        Activity.  It never says what she should choose, and it cannot act.
        """
        session = self.read_session()
        out: dict[str, Any] = {
            "available": True,
            "co_presence_state": str(session.get("state") or "NONE"),
            "invitation_scope": (invitation or {}).get("scope"),
            "delivered_to_user": False,
            "external_channel_sent": False,
            "private_text_included": False,
        }
        if invitation:
            out["invitation_id"] = invitation.get("invitation_id")
            out["invitation_kind"] = KIND_INVITATION
            out["what_accepting_means"] = (
                "the running Activity is set down at its own checkpoint and one finite joint "
                "Activity starts; it ends only at an explicit end, and coming back to the old "
                "work is a new Decision")
        return out

    def _activity_view(self, activity_ref: Any) -> dict[str, Any]:
        """One Activity as the owner reports it right now."""
        if not activity_ref:
            return {"activity_id": None, "status": None}
        try:
            activity = self.runtime.activity_read.get_activity(str(activity_ref)) or {}
        except Exception as exc:
            return {"activity_id": str(activity_ref), "read_error":
                    "%s: %s" % (type(exc).__name__, exc)}
        return {"activity_id": activity.get("activity_id"), "status": activity.get("status"),
                "revision": activity.get("revision"),
                "checkpoint_ref": activity.get("checkpoint_ref"),
                "last_action_ref": activity.get("last_action_ref"),
                "resume_condition_ref": activity.get("resume_condition_ref"),
                "waiting_on_ref": activity.get("waiting_on_ref")}

    def _complete_joint_activity(self, session: Mapping[str, Any], end_event: Mapping[str, Any],
                                 moment_iso: str) -> dict[str, Any]:
        """The explicit end event, as the external result that completes the joint Activity.

        LR-5 is the only writer of progress/completion state; this method hands it the evidence
        and reports exactly what the owner did (including an idempotent replay, or a refusal).
        """
        import activity_progress_completion_lr5 as lr5

        joint_ref = session.get("joint_activity_ref")
        invitation_id = str(session.get("invitation_id"))
        reference = session_effect_ref(invitation_id)
        if not joint_ref:
            return {"ok": False, "reason_code": "NO_JOINT_ACTIVITY",
                    "detail": "this session never got a joint Activity, so there is nothing to "
                              "complete; the private save point is untouched"}
        evidence = {
            "progress_evidence_id": "pevid:co_presence:%s" % invitation_id,
            "subject_id": self.canonical_subject,
            "activity_id": str(joint_ref),
            "source_kind": lr5.SOURCE_KIND_EXTERNAL_RESULT,
            "source_ref": "co_presence_end:%s" % invitation_id,
            "external_result_ref": reference,
            "target_ref": reference,
            # An external result, not an AR-1 settlement: this module never claims
            # ``result_settlement_authority``.
            "authority_domain": "external_result_authority",
            "epistemic_kind": lr5.EPISTEMIC_CANONICAL_OBSERVATION,
            "observed_facts": {
                "co_presence_ref": reference,
                "explicit_end": bool(end_event["explicit_end"]),
                "end_evidence_ref": (end_event.get("completion_evidence_ref")
                                     or end_event.get("evidence_ref") or reference),
                "declared_by": "explicit_inbound_co_presence_protocol",
            },
            "occurred_at": end_event["occurred_at"],
            "observed_at": moment_iso,
            "recorded_at": moment_iso,
            "idempotency_key": "idem:co_presence_end:%s" % invitation_id,
        }
        try:
            out = dict(self.runtime.progress_coordinator.ingest_progress_evidence(
                evidence, apply_natural_completion=True))
        except Exception as exc:
            return {"ok": False, "reason_code": "COMPLETION_INGEST_REFUSED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        eligibility = out.get("completion_eligibility") or {}
        terminal = str(out.get("activity_status") or "") in {"COMPLETED", "ABANDONED"}
        return {
            "ok": bool(terminal),
            "activity_id": out.get("activity_id"),
            "activity_status": out.get("activity_status"),
            "completed_transition": bool(out.get("completed_transition")),
            "completion_status": out.get("completion_status"),
            "idempotent_replay": bool(out.get("idempotent_replay")),
            "completion_eligibility_ref": eligibility.get("eligibility_id"),
            "reason_code": ("JOINT_ACTIVITY_COMPLETED" if terminal
                            else "JOINT_ACTIVITY_NOT_TERMINAL"),
            "detail": ("the owner completed the joint Activity from the explicit end event"
                       if terminal else
                       "the owner did not reach a terminal status for the joint Activity; "
                       "the private save point is preserved and resumable"),
        }


    def _close_request(self, session: Mapping[str, Any], end_event: Mapping[str, Any],
                       moment_iso: str) -> dict[str, Any]:
        """Close the typed request through the source's own vocabulary."""
        source = getattr(self.runtime, "user_request_source", None)
        if source is None:
            return {"ok": False, "reason_code": "NO_USER_REQUEST_SOURCE"}
        event_id = "ureq:co_presence:%s" % session.get("invitation_id")
        try:
            if end_event["explicit_end"]:
                source.advance_request_revision(event_id, new_revision=2, observed_at=moment_iso)
                return {"ok": True, "request_status": "OPEN_ADVANCED"}
            source.cancel_request(event_id, cancelled_at=moment_iso,
                                  cancel_ref="cevt:co_presence_end:%s" % session.get("invitation_id"))
            return {"ok": True, "request_status": "CANCELLED"}
        except Exception as exc:
            return {"ok": False, "reason_code": "REQUEST_CLOSE_REFUSED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}


def operator_action(root: Path | str, *, raw: Optional[str] = None) -> dict[str, Any]:
    """The ``invite`` verb's answer: the codec, and the session as it stands.

    Starting or ending a session needs the runtime that holds the canonical lease, so this
    entry refuses with ``RUNTIME_REQUIRED`` instead of opening a second writer: the resident (or
    the integration test) drives ``begin``/``end`` with its own runtime.
    """
    payload: Any = None
    if raw:
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise CoPresenceError("INVITATION_JSON_INVALID: %s" % exc)
    session = FiniteCoPresence.__new__(FiniteCoPresence)
    session.state_root = Path(root) / "life" / "state"
    out: dict[str, Any] = {
        "instance": str(root),
        "read_only": True,
        "session": FiniteCoPresence.read_session(session),
        "rules": {
            "ordinary_message": "an inbound message is an observation: no pause, no new Activity",
            "explicit_invitation": "needs event_kind=%s with source, time, scope, subject, home"
                                   % KIND_INVITATION,
            "explicit_end": "needs event_kind=%s with explicit_end or completion evidence"
                            % KIND_END,
            "atomic_foreground": "never force-switched",
            "operator_pause": "not a social interruption; a paused instance declines",
        },
    }
    if payload is not None:
        classified = classify(payload)
        out["classified_as"] = classified
        if classified["kind"] == KIND_INVITATION:
            out["invitation"] = parse_invitation(payload)
        elif classified["kind"] == KIND_END:
            out["end_event"] = parse_end(payload)
    if payload is not None and classify(payload)["effect"] == EFFECT_INVITATION:
        out["reason_code"] = "RUNTIME_REQUIRED"
        out["detail"] = ("begin/end need the runtime that holds the canonical writer lease; "
                         "run them from the resident or the integration harness")
        out["ok"] = False
        return out
    out["ok"] = True
    return out


__all__ = [
    "CoPresenceError",
    "INTERRUPT_NOW",
    "REASON_IDLE_ACCEPT",
    "DECISION_ACCEPT",
    "DECISION_DECLINE",
    "DECISION_DEFER",
    "EFFECT_INVITATION",
    "EFFECT_OBSERVATION_ONLY",
    "FiniteCoPresence",
    "KIND_END",
    "KIND_INVITATION",
    "SESSION_FILENAME",
    "classify",
    "operator_action",
    "parse_end",
    "parse_invitation",
]
