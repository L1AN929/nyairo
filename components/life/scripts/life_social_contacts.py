#!/usr/bin/env python3
"""v0.4 B: the resident's local-contact pass -- A's inbox, the protocol, and one honest step.

What this module is
-------------------
It is the *seam* between the transport ledger A owns (``life_local_contact``) and the finite
co-presence protocol (``life_co_presence``), driven by the normal resident step
(``autonomous_life_assembly.runner_tick``), not by an operator helper:

* an ordinary inbound message is **observed** and nothing else: no pause, no Activity, no model
  call, and the transport result is the terminal ``OBSERVED``;
* an explicit invitation goes through **one** fresh AG-1 Decision (the protocol ingests the
  invitation as the typed user request, AG-1 commits, and the adoption follows that committed
  Decision -- there is no second model call anywhere in this path);
* an explicit end runs the protocol's own end path (LR-4 releases the private wait, LR-5
  completes the joint Activity) and the transport result is the terminal ``ENDED``.

What it deliberately is not
---------------------------
* no second Activity owner, no relationship store, no chat domain, no "social state machine":
  everything that persists here is either A's transport ledger or the protocol's own session
  record;
* no re-decision loop: a ``DEFERRED`` event names its condition and is never in ``pending()``,
  so the next tick does not ask the model again;
* no blind retry: a failure that leaves the outcome unknown is recorded as ``UNKNOWN`` with a
  named reason and stays out of ``pending()``; reconciling it means :meth:`reconcile`, which
  reads A's accounting for the same operation key;
* no outbound delivery and no private text in views: :meth:`view` and :meth:`cognition_block`
  carry counts, states and the invitation's *scope* label only -- a message body never leaves
  :meth:`life_local_contact.LocalContactInbox.get_event`.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE.parents[1]), str(HERE.parents[1] / "native"),
              str(HERE.parents[1] / "native" / "integration")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: v0.4 shared practice: A's service seam.  Imported softly so a tree without it still runs the
#: contact pass -- and says so by name instead of pretending the content half exists.
try:
    import shared_practice_life as spl  # noqa: E402
except Exception:  # pragma: no cover - the seam is optional, its absence is reported
    spl = None  # type: ignore[assignment]


#: Who owns a claim in A's ledger.  One consumer name, so a claim is recognisably this pass.
CONSUMER = "life_cycle"

#: How many pending events one resident tick may consume.  Bounded on purpose: a tick is a step,
#: not a drain, and an invitation inside it spends a real Decision.
DEFAULT_MAX_PER_TICK = 2
MAX_PER_TICK = 4

KIND_MESSAGE = "LOCAL_MESSAGE"
KIND_INVITATION = "CO_PRESENCE_INVITATION"
KIND_END = "CO_PRESENCE_END"

OUTCOME_OBSERVED = "OBSERVED"
OUTCOME_ACCEPTED = "ACCEPTED"
OUTCOME_DEFERRED = "DEFERRED"
OUTCOME_DECLINED = "DECLINED"
OUTCOME_ENDED = "ENDED"
OUTCOME_UNKNOWN = "UNKNOWN"

REASON_PORT_UNAVAILABLE = "CONTACT_PORT_UNAVAILABLE"
REASON_PENDING_UNREADABLE = "CONTACT_PENDING_UNREADABLE"
REASON_EVENT_UNREADABLE = "CONTACT_EVENT_UNREADABLE"
REASON_HANDLER_FAILED = "CONTACT_HANDLER_FAILED"
REASON_KIND_UNHANDLED = "CONTACT_EVENT_KIND_UNHANDLED"
REASON_END_NOT_BOUND = "CO_PRESENCE_END_NOT_BOUND"

#: The only text that may appear in a bounded view: the invitation's own scope label.
MAX_SCOPE_CHARS = 120


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds")


def _code(value: Any, fallback: str) -> str:
    """A reason code A accepts: ``^[A-Z][A-Z0-9_]{1,63}$``."""

    text = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(value or "")).upper()
    text = text.lstrip("_")[:64]
    if len(text) < 2 or not text[0].isalpha():
        return fallback
    return text


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


class SocialContactPort:
    """One bounded, resident-driven pass over the local-contact ledger."""

    def __init__(self, runtime: Any, *, state_root: Path | str, subject_id: str, home: str,
                 inbox: Any = None, protocol: Any = None, cycle: Any = None,
                 practice: Any = None, consumer: str = CONSUMER,
                 max_per_tick: int = DEFAULT_MAX_PER_TICK) -> None:
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.subject_id = str(subject_id or "")
        self.home = str(home or "")
        self.inbox = inbox
        self.protocol = protocol
        self.cycle = cycle
        #: v0.4 shared practice: A's service seam over this instance's own owner port.  ``None``
        #: means the content half is not wired, and every practice answer says so by name.
        self.practice = practice
        self.consumer = consumer
        self.max_per_tick = max(1, min(int(max_per_tick), MAX_PER_TICK))
        self.last_tick: Optional[dict[str, Any]] = None
        self.last_results: list[dict[str, Any]] = []

    # -- what this thing is ----------------------------------------------------------------

    def available(self) -> bool:
        return self.inbox is not None and self.protocol is not None

    def describe(self) -> dict[str, Any]:
        """Read-only self-report: which seams exist, and what this object refuses to own."""

        missing = []
        if self.inbox is None:
            missing.append("inbox")
        if self.protocol is None:
            missing.append("protocol")
        practice = self._practice_describe()
        return {
            "owner": "SocialContactPort",
            "available": self.available(),
            "missing": missing,
            "state_root": str(self.state_root),
            "subject_id": self.subject_id,
            "home_ref": self.home,
            "consumer": self.consumer,
            "max_per_tick": self.max_per_tick,
            "owns_activity": False,
            "owns_relationship": False,
            "owns_conversation": False,
            "pauses_anything_itself": False,
            "outbound_delivery": False,
            "external_channel_sent": False,
            "delivered_to_user": False,
            "shared_practice": practice,
            "rules": {
                "ordinary_message": ("observed only: no pause, no Activity, and a model call only "
                                     "inside an already-open shared practice"),
                "invitation": "one fresh AG-1 Decision; accept/defer/decline are separate",
                "end": "the protocol's own end path; silence is not an end",
            },
        }

    def _practice_describe(self) -> dict[str, Any]:
        """What the content half is wired to -- or the name of what is missing."""

        if self.practice is not None:
            describe = getattr(self.practice, "describe", None)
            block = dict(describe() if callable(describe) else {})
            block["available"] = True
            return block
        if spl is None:
            return {"available": False, "reason_code": "SHARED_PRACTICE_SEAM_NOT_IMPORTABLE"}
        return {"available": False, "reason_code": spl.REASON_SEAM_UNAVAILABLE,
                "detail": "no shared-practice service was wired for this instance"}

    # -- bounded reads (never a body) -------------------------------------------------------

    def view(self) -> dict[str, Any]:
        """Counts, states and the invitation's scope label -- never a message body."""

        out: dict[str, Any] = {"available": self.available(), "inbox": None, "session": None,
                               "private_text_included": False, "delivered_to_user": False}
        if self.inbox is None:
            out["inbox"] = {"ledger_state": "ABSENT", "reason_code": REASON_PORT_UNAVAILABLE}
            return out
        summary = self.inbox.summary()
        data = dict(summary.get("data") or {}) if isinstance(summary.get("data"), Mapping) else {}
        out["inbox"] = {
            "ok": bool(summary.get("ok")),
            "reason_code": str(summary.get("reason_code") or "") or None,
            "ledger_state": str(data.get("ledger_state") or ("UNREADABLE" if not summary.get("ok")
                                                            else "OK")),
            "counts": dict(data.get("counts") or {}),
            "by_kind": dict(data.get("by_kind") or {}),
            "event_count": data.get("event_count"),
            "bodies_stored": data.get("bodies_stored"),
            "private_text_included": False,
        }
        if self.protocol is not None:
            try:
                session = dict(self.protocol.read_session() or {})
            except Exception as exc:  # a read failure is reported, never guessed
                session = {"state": "UNREADABLE",
                           "detail": "%s: %s" % (type(exc).__name__, exc)}
            out["session"] = {
                "state": str(session.get("state") or "NONE"),
                "invitation_id": session.get("invitation_id"),
                "scope": _text(session.get("scope"), MAX_SCOPE_CHARS) or None,
                "opened_at": session.get("opened_at"),
                "closed_at": session.get("closed_at"),
                "end_reason": session.get("end_reason"),
                "private_activity_ref": session.get("private_activity_ref"),
                "joint_activity_ref": session.get("joint_activity_ref"),
                "single_foreground": session.get("single_foreground"),
                "resume_requires_decision": True,
            }
        out["shared_practice"] = self._practice_view()
        if self.last_results:
            out["last_tick"] = [{"kind": item.get("kind"), "outcome": item.get("outcome"),
                                 "reason_code": item.get("reason_code"),
                                 "event_ref": item.get("event_ref"),
                                 "llm_calls": item.get("llm_calls")}
                                for item in self.last_results[-MAX_PER_TICK:]]
        return out

    def cognition_block(self) -> dict[str, Any]:
        """A bounded block for the Decision prompt: who is at the door, and what is *open*.

        An already-open shared practice is described with its real content (the work text, the
        recent contributions, the materials) -- not with a bare ``OPEN`` label: a Decision that
        cannot see what the work says has nothing to decide about.
        """

        view = self.view()
        inbox = dict(view.get("inbox") or {})
        session = dict(view.get("session") or {})
        counts = dict(inbox.get("counts") or {})
        practice = dict(view.get("shared_practice") or {})
        return {
            "available": bool(view.get("available")),
            "pending": int(counts.get("PENDING") or 0),
            "claimed": int(counts.get("CLAIMED") or 0),
            "deferred": int(counts.get("DEFERRED") or 0),
            "unknown": int(counts.get("UNKNOWN") or 0),
            "invitation_scope": session.get("scope"),
            "co_presence_state": str(session.get("state") or "NONE"),
            # v0.4 shared practice: real content, bounded -- and never a claim of agreement.
            "shared_practice": dict(practice.get("prompt") or {}),
            "shared_practice_state": practice.get("state"),
            "shared_practice_joint_state": practice.get("joint_state"),
            "delivered_to_user": False,
            "private_text_included": False,
        }

    def _practice_view(self) -> dict[str, Any]:
        """The content half as this pass sees it: state, counts, and a bounded prompt block."""

        available = self.practice is not None
        out: dict[str, Any] = {"available": available, "state": None, "joint_state": None,
                               "session_id": None, "counts": {}, "prompt": None,
                               "model_ready": bool(getattr(self.practice, "model_ready",
                                                           lambda: False)()),
                               "private_text_included": False}
        if not available:
            out["reason_code"] = str(self._practice_describe().get("reason_code") or "")
            return out
        try:
            session = self.practice.find_open()
            if session is not None:
                out["session_id"] = str(session.get("session_id") or "") or None
                out["state"] = str(session.get("state") or "") or None
                out["joint_state"] = str(session.get("joint_state") or "") or None
                out["counts"] = {"contributions": session.get("contribution_count"),
                                 "responses": session.get("response_count")}
                out["prompt"] = dict(self.practice.prompt_block() or {})
        except Exception as exc:  # a read failure is reported, never guessed
            out["reason_code"] = _code(type(exc).__name__, "SHARED_PRACTICE_VIEW_FAILED")
        return out

    def reconcile(self, operation_key: str) -> dict[str, Any]:
        """A's accounting for one operation key -- the UNKNOWN read, not a redo."""

        if self.inbox is None:
            return {"ok": False, "status": "unavailable", "reason_code": REASON_PORT_UNAVAILABLE,
                    "operation_key": str(operation_key or "")[:120]}
        out = dict(self.inbox.operation(operation_key))
        out["operation_key"] = str(operation_key or "")[:120]
        out["redecided"] = False
        return out

    # -- the resident step -----------------------------------------------------------------

    def tick(self, *, limit: Optional[int] = None, now: Optional[str] = None) -> dict[str, Any]:
        """Consume at most ``limit`` pending events; one event, one transport result."""

        if not self.available():
            out = {"ok": False, "reason_code": REASON_PORT_UNAVAILABLE, "processed": [],
                   "llm_calls": 0, "max_per_tick": self.max_per_tick,
                   "missing": self.describe()["missing"]}
            self.last_tick = out
            return out
        cap = self.max_per_tick if limit is None else max(1, min(int(limit), MAX_PER_TICK))
        listing = self.inbox.pending(limit=cap)
        if not listing.get("ok"):
            data = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
            out = {"ok": False,
                   "reason_code": _code(listing.get("reason_code"), REASON_PENDING_UNREADABLE),
                   "detail": _text(listing.get("detail"), 400),
                   "processed": [], "llm_calls": 0, "max_per_tick": cap,
                   "ledger_state": (data or {}).get("ledger_state")}
            self.last_tick = out
            return out
        rows = list((listing.get("data") or {}).get("events") or [])
        processed = [self._process(str(row.get("event_ref") or ""), now=now)
                     for row in rows[:cap] if str(row.get("event_ref") or "")]
        after = self.inbox.pending(limit=1)
        after_data = after.get("data") if isinstance(after.get("data"), Mapping) else {}
        out = {
            "ok": True,
            "reason_code": "",
            "processed": processed,
            "processed_count": len(processed),
            "matched": int((listing.get("data") or {}).get("matched") or 0),
            "llm_calls": sum(int(item.get("llm_calls") or 0) for item in processed),
            "max_per_tick": cap,
            "still_pending": int((after_data or {}).get("matched") or 0),
            "view": self.view(),
        }
        self.last_tick = out
        self.last_results = processed
        return out

    # -- one event -------------------------------------------------------------------------

    def _process(self, event_ref: str, *, now: Optional[str] = None) -> dict[str, Any]:
        if not event_ref:
            return {"event_ref": None, "consumed": False, "reason_code": REASON_EVENT_UNREADABLE,
                    "llm_calls": 0}
        claim = self.inbox.claim(event_ref, consumer=self.consumer, now=now)
        if not claim.get("ok"):
            # Another consumer owns it (or it is already terminal): nothing was done here.
            return {"event_ref": event_ref, "consumed": False,
                    "status": claim.get("status"),
                    "reason_code": claim.get("reason_code"),
                    "llm_calls": 0}
        token = claim.get("claim_token")
        got = self.inbox.get_event(event_ref, include_body=True)
        if not got.get("ok"):
            return self._record_unknown(event_ref, token, REASON_EVENT_UNREADABLE,
                                        "the claimed event could not be read back")
        detail = dict(((got.get("data") or {}).get("event")) or {})
        kind = str(detail.get("event_kind") or "")
        try:
            if kind == KIND_MESSAGE:
                return self._ordinary_message(event_ref, token, detail)
            if kind == KIND_INVITATION:
                return self._invitation(event_ref, token, detail, now=now)
            if kind == KIND_END:
                return self._end(event_ref, token, detail, now=now)
        except Exception as exc:  # a handler failure is an honest UNKNOWN, never a crash
            return self._record_unknown(event_ref, token, _code(type(exc).__name__, REASON_HANDLER_FAILED),
                                        "%s: %s" % (type(exc).__name__, exc))
        return self._record_unknown(event_ref, token, REASON_KIND_UNHANDLED, kind)

    def _record_unknown(self, event_ref: str, token: Any, reason_code: str,
                        detail: str, *, llm_calls: int = 0,
                        practice: Any = None) -> dict[str, Any]:
        """Record ``UNKNOWN`` (A requires a reason) and keep the event out of ``pending()``."""

        code = _code(reason_code, REASON_HANDLER_FAILED)
        result = self.inbox.record_result(event_ref, outcome=OUTCOME_UNKNOWN, claim_token=token,
                                          reason_code=code,
                                          detail=_text(detail or "the pass could not settle it", 400))
        out = {"event_ref": event_ref, "consumed": True, "outcome": OUTCOME_UNKNOWN,
               "reason_code": code, "llm_calls": int(llm_calls),
               "detail": _text(detail, 300),
               "recorded": bool(result.get("ok")),
               "record_reason_code": result.get("reason_code") or None,
               "reconcile_with": "inbox.operation(<operation_key>) -- never a blind redo"}
        if practice is not None:
            # The content half says exactly how far it got (stage + owner reason), so an UNKNOWN
            # is diagnosable without re-sending the message.
            out["practice"] = practice
        return out

    def _record_view(self, result: Mapping[str, Any]) -> dict[str, Any]:
        return {"ok": bool(result.get("ok")), "status": result.get("status"),
                "reason_code": result.get("reason_code") or None,
                "processing_state": result.get("processing_state"),
                "writes_performed": result.get("writes_performed")}

    def _ordinary_message(self, event_ref: str, token: Any,
                          detail: Mapping[str, Any]) -> dict[str, Any]:
        """An ordinary message never pauses, starts or accepts anything.

        Inside an already-open shared practice it is one real content step (the person's own
        words, one content request, her reply/contribution/work through A).  Outside one it is
        observed and nothing else: a message can never open a session or become an invitation.
        """

        observed = dict(self.protocol.observe(detail))
        gate = self._practice_gate() if self.practice is not None else None
        practice = self._practice_step(event_ref, detail, gate=gate)
        if practice is not None and not practice.get("ok"):
            return self._record_unknown(
                event_ref, token,
                _code(practice.get("reason_code"), "SHARED_PRACTICE_STEP_FAILED"),
                _text(practice.get("detail"), 400) or "the shared-practice step did not land",
                llm_calls=int(practice.get("llm_calls") or 0), practice=practice)
        recorded = bool(practice and practice.get("ok"))
        if recorded:
            detail_text = ("an ordinary message inside an open shared practice: the words, one "
                           "content request and her reply were recorded by the owners")
        elif gate is not None and not gate.get("allowed"):
            detail_text = ("an ordinary message is observed only: no content step (%s)"
                           % str(gate.get("reason_code") or "SHARED_PRACTICE_NOT_CONTINUED"))
        else:
            detail_text = ("an ordinary inbound message is observed only: no pause, no Activity")
        result = self.inbox.record_result(
            event_ref, outcome=OUTCOME_OBSERVED, claim_token=token,
            reason_code=("SHARED_PRACTICE_CONTENT_RECORDED" if recorded else "OBSERVED_ONLY"),
            detail=detail_text)
        out = {"event_ref": event_ref, "consumed": True, "kind": KIND_MESSAGE,
               "outcome": OUTCOME_OBSERVED,
               "llm_calls": int((practice or {}).get("llm_calls") or 0),
               "effect": observed.get("effect"),
               "pause_requested": bool(observed.get("pause_requested")),
               "activity_started": bool(observed.get("activity_started")),
               "practice": practice,
               "recorded": bool(result.get("ok")),
               "record_reason_code": result.get("reason_code") or None}
        if gate is not None:
            # Named, bounded: *why* a message did or did not continue the work, without a body.
            out["practice_gate"] = gate
        return out

    def _practice_step(self, event_ref: str, detail: Mapping[str, Any], *,
                       gate: Any = None) -> Optional[dict[str, Any]]:
        """The content step -- only inside the joint time this instance really has open.

        v0.4 shared practice: an ordinary message may continue a work, but only the work of the
        joint time that is open *right now* and was bound by its own accepted invitation.  An
        open practice artifact somewhere is not enough (a phone-side candidate is not consent),
        and a paused or ended practice, an ended joint Activity or an unreadable binding means
        the message is observed and nothing else.
        """

        if self.practice is None:
            return None
        body = str(detail.get("body") or "")
        if not body.strip():
            return None
        verdict = self._practice_gate() if gate is None else dict(gate)
        if not verdict.get("allowed"):
            return None
        try:
            return dict(self.practice.step_message(text=body, event_ref=event_ref) or {})
        except Exception as exc:  # a seam bug is a named UNKNOWN, not a lost message
            return {"ok": False, "reason_code": "SHARED_PRACTICE_STEP_FAILED", "llm_calls": 0,
                    "detail": "%s: %s" % (type(exc).__name__, exc)}

    def _activity_status(self, activity_ref: str) -> str:
        """The joint Activity's own status, read from the owner (never guessed)."""

        reader = getattr(self.runtime, "activity_read", None) if self.runtime is not None else None
        if reader is None:
            return "UNVERIFIABLE"
        try:
            row = dict(reader.get_activity(str(activity_ref)) or {})
        except Exception as exc:
            return "UNREADABLE:%s" % type(exc).__name__
        return str(row.get("status") or "ABSENT")

    def _practice_gate(self) -> dict[str, Any]:
        """May a message continue the shared practice?  Only a real, current joint time.

        The question is not "is some practice artifact open?" -- it is whether *this* instance
        has an OPEN co-presence session whose joint Activity is really running and whose own
        acceptance bound this very practice session.  Every other case (a candidate session the
        phone opened, a paused or ended practice, an ended joint Activity, a binding written for
        another invitation, or a binding this restart cannot read) answers ``allowed=False`` with
        a name, and the message stays an observation.
        """

        if self.practice is None:
            return {"allowed": False, "reason_code": "SHARED_PRACTICE_SEAM_UNAVAILABLE"}
        try:
            session = dict(self.protocol.read_session() or {})
        except Exception as exc:
            return {"allowed": False, "reason_code": "CO_PRESENCE_SESSION_UNREADABLE",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        state = str(session.get("state") or "NONE")
        if state != "OPEN":
            return {"allowed": False, "reason_code": "CO_PRESENCE_NOT_OPEN",
                    "co_presence_state": state}
        joint_ref = str(session.get("joint_activity_ref") or "")
        if not joint_ref:
            return {"allowed": False, "reason_code": "CO_PRESENCE_NO_JOINT_ACTIVITY"}
        joint_status = self._activity_status(joint_ref)
        if joint_status != "ACTIVE":
            return {"allowed": False, "reason_code": "JOINT_ACTIVITY_NOT_ACTIVE",
                    "joint_activity_ref": joint_ref, "joint_activity_status": joint_status}
        try:
            open_row = self.practice.find_open()
        except Exception as exc:  # a read failure is named, never treated as "no session"
            return {"allowed": False, "reason_code": "SHARED_PRACTICE_VIEW_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        if open_row is None:
            return {"allowed": False, "reason_code": "SHARED_PRACTICE_NO_OPEN_SESSION",
                    "joint_activity_ref": joint_ref}
        open_id = str(open_row.get("session_id") or "")
        bound = str(session.get("practice_session_ref") or "")
        if not bound:
            return {"allowed": False, "reason_code": "SHARED_PRACTICE_NOT_BOUND",
                    "open_session_id": open_id,
                    "detail": "the joint time never bound a practice, so a message cannot "
                              "continue one"}
        if bound != open_id:
            return {"allowed": False, "reason_code": "SHARED_PRACTICE_BOUND_TO_ANOTHER_SESSION",
                    "bound_session_id": bound, "open_session_id": open_id}
        practice_state = str(open_row.get("state") or "")
        if practice_state != "OPEN":
            return {"allowed": False, "reason_code": "SHARED_PRACTICE_NOT_OPEN",
                    "practice_state": practice_state, "open_session_id": open_id}
        return {"allowed": True, "reason_code": "", "session_id": bound,
                "joint_activity_ref": joint_ref, "joint_activity_status": joint_status,
                "co_presence_state": state, "practice_state": practice_state}

    def _practice_bind(self, session_id: Any, *, invitation: Mapping[str, Any],
                       event_ref: str = "") -> dict[str, Any]:
        """Record, on the accepted joint time, *which* practice it opened (durably)."""

        bind = getattr(self.protocol, "bind_practice", None)
        if not callable(bind):
            return {"ok": False, "reason_code": "SHARED_PRACTICE_BIND_UNAVAILABLE",
                    "detail": "this protocol cannot record which practice belongs to the joint "
                              "time, so no message will continue it"}
        try:
            return dict(bind(invitation_id=invitation.get("invitation_id"),
                             practice_session_ref=str(session_id or ""),
                             event_ref=event_ref) or {})
        except Exception as exc:
            return {"ok": False, "reason_code": "SHARED_PRACTICE_BIND_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}

    def _practice_open(self, detail: Mapping[str, Any], invitation: Mapping[str, Any],
                       event_ref: str = "") -> Optional[dict[str, Any]]:
        """Open the content half for this joint time -- or say by name why it did not open."""

        if self.practice is None:
            return None
        refs = list(invitation.get("material_refs") or [])
        title = _text(invitation.get("scope"), 120) or "一起做点东西"
        opening = _text(detail.get("body"), 1200) or title
        if not refs:
            return {"ok": False, "opened": False,
                    "reason_code": "SHARED_PRACTICE_MATERIAL_REFUSED", "llm_calls": 0,
                    "detail": "the invitation named no shared material, so no session was opened"}
        # The work is bound to *this* invitation event (A verifies the reference against the
        # instance's own read surface), and the operation key is the event's own, so a retried
        # acceptance replays instead of forking a second work.
        basis = {"event_ref": str(event_ref)} if event_ref else None
        try:
            opened = dict(self.practice.open_session(
                material_refs=refs, title=title, opening_text=opening, basis=basis,
                operation_key=("practice_open:%s" % str(event_ref)[:120]) if event_ref else None
            ) or {})
        except Exception as exc:
            return {"ok": False, "opened": False, "reason_code": "SHARED_PRACTICE_OPEN_FAILED",
                    "llm_calls": 0, "detail": "%s: %s" % (type(exc).__name__, exc)}
        if opened.get("ok") and opened.get("session_id"):
            opened["bound"] = self._practice_bind(opened.get("session_id"),
                                                  invitation=invitation, event_ref=event_ref)
        return opened

    def _practice_end(self, end_event: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """End the content half after the explicit end (nothing is deleted)."""

        if self.practice is None:
            return None
        try:
            if self.practice.find_open() is None:
                return None
            return dict(self.practice.end_session(reason=str(end_event.get("reason") or "")) or {})
        except Exception as exc:
            return {"ok": False, "reason_code": "SHARED_PRACTICE_END_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}

    def _invitation(self, event_ref: str, token: Any, detail: Mapping[str, Any], *,
                    now: Optional[str] = None) -> dict[str, Any]:
        """One invitation, one Decision: accept, defer or decline -- recorded as it happened."""

        import life_co_presence as cop

        raw = detail.get("invitation")
        if not isinstance(raw, Mapping):
            return self._record_unknown(event_ref, token, "CO_PRESENCE_INVITATION_MISSING",
                                        "the ledger event carries no invitation block")
        invitation = dict(cop.parse_invitation(raw))
        started = dict(self.protocol.begin_from_decision(invitation, now=now))
        decision = str(started.get("decision") or "")
        decision_ref = str(started.get("decision_record_ref") or "") or None
        llm_calls = int(started.get("llm_calls") or 0)
        base = {"event_ref": event_ref, "consumed": True, "kind": KIND_INVITATION,
                "invitation_id": invitation["invitation_id"], "decision": decision,
                "decision_record_ref": decision_ref,
                "selected_candidate_ref": started.get("selected_candidate_ref"),
                "pause_applied": False, "llm_calls": llm_calls,
                "activity_started": bool(started.get("activity_started")),
                "park_applied": bool(started.get("park_applied")),
                "handoff": started.get("handoff"),
                "joint_activity_ref": started.get("joint_activity_ref"),
                "private_activity_ref": started.get("private_activity_ref"),
                "reason_code": _code(started.get("reason_code"), "CO_PRESENCE_UNSETTLED"),
                "detail": _text(started.get("detail"), 300)}
        if started.get("ok"):
            result = self.inbox.record_result(
                event_ref, outcome=OUTCOME_ACCEPTED, claim_token=token,
                session_ref=str(started.get("session_ref") or "") or None,
                decision_ref=decision_ref, reason_code="CO_PRESENCE_ACCEPTED",
                detail="the Decision accepted the invitation; the joint time is a normal "
                       "single-foreground Activity and the private work waits with its checkpoint")
            base.update({"outcome": OUTCOME_ACCEPTED, "recorded": bool(result.get("ok")),
                         "record_reason_code": result.get("reason_code") or None,
                         "stage": started.get("stage")})
            # v0.4 shared practice: the joint time now has explicit materials -- open the content
            # half over A's service.  Opening is NOT acceptance (it stays CANDIDATE until a
            # verified reply exists), and a refusal here is reported, never papered over.
            practice = self._practice_open(detail, invitation, event_ref=event_ref)
            if practice is not None:
                base["shared_practice"] = practice
                base["practice_session_ref"] = practice.get("session_id")
                base["practice_state"] = practice.get("state")
                base["practice_joint_state"] = practice.get("joint_state")
                base["practice_bound"] = bool((practice.get("bound") or {}).get("ok"))
            return base
        if decision == cop.DECISION_DEFER:
            condition = str(started.get("defer_condition") or
                            "co_presence_invitation:%s:reconsidered" % invitation["invitation_id"])
            result = self.inbox.record_result(
                event_ref, outcome=OUTCOME_DEFERRED, claim_token=token,
                decision_ref=decision_ref, defer_condition=condition,
                reason_code=_code(started.get("reason_code"), "CO_PRESENCE_DEFERRED"),
                detail=_text(started.get("detail"), 400))
            base.update({"outcome": OUTCOME_DEFERRED, "defer_condition": condition,
                         "recorded": bool(result.get("ok")),
                         "record_reason_code": result.get("reason_code") or None})
            return base
        if decision == cop.DECISION_DECLINE:
            result = self.inbox.record_result(
                event_ref, outcome=OUTCOME_DECLINED, claim_token=token,
                decision_ref=decision_ref,
                reason_code=_code(started.get("reason_code"), "CO_PRESENCE_DECLINED"),
                detail=_text(started.get("detail"), 400))
            base.update({"outcome": OUTCOME_DECLINED, "recorded": bool(result.get("ok")),
                         "record_reason_code": result.get("reason_code") or None})
            return base
        # No committed Decision at all (a failed or unreadable model answer): unknown, and the
        # same operation key is the way back -- this path never re-asks by itself.
        return self._record_unknown(
            event_ref, token, _code(started.get("reason_code"), "CO_PRESENCE_DECISION_UNKNOWN"),
            _text(started.get("detail"), 400) or "the invitation produced no committed Decision",
            llm_calls=llm_calls)

    def _end(self, event_ref: str, token: Any, detail: Mapping[str, Any], *,
             now: Optional[str] = None) -> dict[str, Any]:
        """The explicit end: LR-4 releases the private wait, LR-5 completes the joint Activity."""

        import life_co_presence as cop

        raw = detail.get("end")
        if not isinstance(raw, Mapping):
            return self._record_unknown(event_ref, token, "CO_PRESENCE_END_MISSING",
                                        "the ledger event carries no end block")
        end_event = dict(cop.parse_end(raw))
        binding = dict(raw.get("binding") or {}) or dict(detail.get("binding") or {})
        invitation_ref = str(binding.get("invitation_event_ref") or "") or None
        ended: dict[str, Any] = {}
        try:
            ended = dict(self.protocol.end(end_event, now=now))
        except cop.CoPresenceError as exc:
            # No open session for this invitation: the end cannot be bound to anything.
            return self._record_unknown(event_ref, token, _code(str(exc).split(":")[0],
                                                                 REASON_END_NOT_BOUND),
                                        str(exc))
        joint = dict(ended.get("joint_completion") or {})
        terminal = str(joint.get("reason_code") or "") == "JOINT_ACTIVITY_COMPLETED"
        result = self.inbox.record_result(
            event_ref, outcome=OUTCOME_ENDED, claim_token=token, invitation_ref=invitation_ref,
            reason_code=("CO_PRESENCE_ENDED" if terminal else "CO_PRESENCE_END_JOINT_NOT_TERMINAL"),
            detail=("the explicit end completed the joint Activity and LR-4 opened the private "
                    "work's resume eligibility" if terminal else
                    _text(joint.get("detail"), 300) or "the joint Activity did not reach a "
                                                       "terminal state; the private save point "
                                                       "is preserved"))
        out = {"event_ref": event_ref, "consumed": True, "kind": KIND_END,
               "invitation_id": end_event["invitation_id"],
               "invitation_ref": invitation_ref, "outcome": OUTCOME_ENDED, "llm_calls": 0,
               "joint_activity_status": joint.get("activity_status"),
               "joint_completed": terminal,
               "new_eligibility_refs": list(ended.get("new_eligibility_refs") or []),
               "private_activity": dict(ended.get("private_activity") or {}),
               "resume_requires_decision": True,
               "recorded": bool(result.get("ok")),
               "record_reason_code": result.get("reason_code") or None}
        # v0.4 shared practice: the explicit end also ends the content half -- and the work,
        # every contribution and the version history stay readable.
        practice = self._practice_end(end_event)
        if practice is not None:
            out["shared_practice"] = practice
            out["practice_state"] = practice.get("state")
            out["practice_ended"] = bool(practice.get("ok"))
        if not result.get("ok") and not invitation_ref:
            return self._record_unknown(event_ref, token, REASON_END_NOT_BOUND,
                                        "the end event could not be bound to this ledger's "
                                        "invitation")
        return out


__all__ = [
    "CONSUMER",
    "DEFAULT_MAX_PER_TICK",
    "KIND_END",
    "KIND_INVITATION",
    "KIND_MESSAGE",
    "OUTCOME_ACCEPTED",
    "OUTCOME_DECLINED",
    "OUTCOME_DEFERRED",
    "OUTCOME_ENDED",
    "OUTCOME_OBSERVED",
    "OUTCOME_UNKNOWN",
    "REASON_PORT_UNAVAILABLE",
    "SocialContactPort",
]
