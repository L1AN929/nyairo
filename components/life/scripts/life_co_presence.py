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
from datetime import datetime, timezone
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


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds")


def _instant(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


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
            out.update({"decision": DECISION_DEFER, "reason_code": REASON_NO_FOREGROUND,
                        "detail": "there is no private Activity to interrupt, so there is no "
                                  "save point to return to yet",
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

    def _adopt_joint_activity(self, invitation: Mapping[str, Any],
                              moment_iso: str) -> dict[str, Any]:
        """One real Decision+Adoption cycle.  The Decision decides, not this module."""
        import activity_progress_completion_lr5 as lr5
        import agency_decision_ag1 as ag1
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
        try:
            out = self.runtime.coordinator.run_decision_and_adoption_cycle(
                trigger_kind=ag1.TRIGGER_USER_REQUEST_EVENT,
                trigger_ref=reference,
                observed_at=moment_iso,
                adoption_spec=spec.to_dict(),
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
        if state != "OPEN":
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
