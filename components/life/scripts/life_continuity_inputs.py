"""C package (MEM-GATE / CHOICE / FUTURE; CT09/12/19): experience -> the next choice.

What this module is
-------------------
One *read* seam for the single life entry (D): "what did she actually get through, what is
still unfinished, and what may continue right now".  It answers those three questions from
real owners only:

* **past** -- admitted M0 outcomes, read through the Memory boundary port
  (``life_experience_port.LifeExperiencePort``); never from a prompt, a log or a summary;
* **unfinished** -- open canonical Activities (LR-2) and open AgendaItems (LR-4);
* **waiting** -- a WAIT may resume only when a *new* ``ResumeEligibility`` exists, i.e. one
  the caller has not already seen (``known_eligibility_refs`` watermark) and/or one opened
  after ``since``.

What it deliberately is not
---------------------------
* It signs **nothing**: no authority, no grant, no location/position permission, no
  Candidate, no Decision, no Adoption, no Activity write.  ``next_step`` is a *suggestion
  with a reason and its evidence*, never a permission; the one foreground / Decision+Adoption
  pairing / owner admission rules stay exactly where they were (AG-0/AG-1/AG-2/LR-2).
  ``recall`` cannot mint current-location authority or a Candidate: the refusal methods
  (``candidate_from_recall`` / ``location_authority_from_recall`` / ``grant_from_recall``)
  raise instead of returning one.
* It never fabricates offline experience.  A gap in time produces an empty past, not a
  narrated one; ``past_state`` distinguishes "nothing was admitted" from "the read was
  refused", so an unreadable control ledger can never be mistaken for a quiet day.
* It never polls a model.  There is no model dependency at all: ``llm_calls`` is 0 by
  construction and re-checking a WAIT is a *clock* question (``next_recheck_at``).

How history changes the next step
---------------------------------
With the same open items and the same ``now``, an admitted owner-confirmed consequence for
an open Activity moves the continuation point from that item's own checkpoint to the
**object the owner confirmed** (the version/result the experience names), and that item's
choice becomes the preferred one.  Without such a consequence the item is continued from its
checkpoint.  A weak cue (free text, an unknown activity, or a consequence with no confirmed
object) changes nothing -- see ``tests/test_life_continuity_inputs.py``.

Fail closed
-----------
``governance_probe`` (optional) is the caller's way to ask the Memory control ledger whether
governance is established at all.  When it answers unhealthy, or when the past source is
bound to a different subject than the binding, or when the source raises, the past view is
empty **and** ``past_state``/``refusals`` say why.  A past source that is not wired at all is
reported as ``PAST_SOURCE_NOT_WIRED``, never as "no history".

Dedup / no double evidence
--------------------------
M0 dedupes a repeated delivery on ``(source_origin, primary_source_ref_id)``; this seam
dedupes again on the same ``source_ref`` and counts distinct *roots*
``(activity_ref, version_ref|owner_result, outcome_kind)``, so re-delivering the same outcome
never inflates ``evidence_roots``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

CONTINUITY_INPUTS_SCHEMA = "chiyo.life.continuity_inputs.v1"

OWNER_MEMORY = "memory"
OWNER_ACTIVITY = "activity"
OWNER_AGENDA = "agenda"

#: ``past_state`` values.  "empty" and "refused" are different facts and are never merged.
PAST_STATE_OK = "OK"
PAST_STATE_NO_HISTORY = "NO_ADMITTED_HISTORY"
PAST_STATE_NOT_WIRED = "PAST_SOURCE_NOT_WIRED"
PAST_STATE_UNREADABLE = "CONTROL_LEDGER_UNREADABLE"
PAST_STATE_WRONG_SUBJECT = "WRONG_SUBJECT"

#: Read-side mirrors of the owning domains' open statuses (LR-2 / LR-4).  This module never
#: writes them; it only asks the owners for what they already consider open.
UNFINISHED_ACTIVITY_STATUSES = frozenset({"ACTIVE", "PAUSED", "WAITING"})
OPEN_AGENDA_STATUSES = frozenset({"SCHEDULED", "DUE"})

SOURCE_OK = "OK"
SOURCE_NOT_WIRED = "NOT_WIRED"
SOURCE_UNREADABLE = "UNREADABLE"

NEXT_STEP_RESUME_WAIT = "RESUME_WAITING_NEW_ELIGIBILITY"
NEXT_STEP_HISTORY = "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT"
NEXT_STEP_CHECKPOINT = "CONTINUE_FROM_CHECKPOINT"
NEXT_STEP_NO_ACTION = "NO_ACTION_NOTHING_OPEN"

CHOICE_CONTINUE = "CONTINUE_UNFINISHED"
CHOICE_RESUME = "RESUME_WAITING"
CHOICE_NO_ACTION = "NO_ACTION"

#: Refusal reasons a caller can see in ``refusals``.
REFUSAL_WRONG_SUBJECT = "PAST_SOURCE_SUBJECT_MISMATCH"
REFUSAL_FOREIGN_ROW = "PAST_ROW_SUBJECT_MISMATCH"
REFUSAL_GOVERNANCE_UNHEALTHY = "MEMORY_GOVERNANCE_NOT_ESTABLISHED"
REFUSAL_SOURCE_RAISED = "SOURCE_READ_FAILED"
REFUSAL_NOT_WIRED = "SOURCE_NOT_WIRED"


class ContinuityInputsError(Exception):
    """The continuity inputs could not be built."""


class ContinuityRefused(ContinuityInputsError):
    """A read view was asked for something only an owner may sign (fail closed)."""


def _refuse(what: str) -> "ContinuityRefused":
    return ContinuityRefused(
        f"the continuity read cannot sign {what}: a recalled past is evidence about what "
        "happened, never a permission, a position, a grant or a Candidate; those stay with "
        "their owners (World/Supply/AG-0/AG-1/AG-2) under the existing admission rules"
    )


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _require_iso(value: Any, name: str) -> str:
    if _parse_iso(value) is None:
        raise ContinuityInputsError(f"{name} must be an ISO-8601 timestamp; got {value!r}")
    return str(value)


def _as_mapping(value: Any, what: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return dict(data)
    raise ContinuityInputsError(f"{what} is not a mapping and has no to_dict(): {value!r}")


def _newer(first: Mapping[str, Any], second: Mapping[str, Any], key: str) -> bool:
    """True when ``first``'s ``key`` is newer than ``second``'s (ISO-aware, string fallback)."""
    a, b = first.get(key), second.get(key)
    pa, pb = _parse_iso(a), _parse_iso(b)
    if pa is not None and pb is not None:
        return pa > pb
    return str(a or "") > str(b or "")


@dataclass(frozen=True)
class ContinuityBinding:
    """The instance this read belongs to: whose past, which home, at what time, for whom."""

    subject: str
    home: str
    now: str
    owner_refs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.subject or "").strip():
            raise ContinuityInputsError("binding.subject is required")
        if not str(self.home or "").strip():
            raise ContinuityInputsError("binding.home is required")
        _require_iso(self.now, "binding.now")
        for key, value in dict(self.owner_refs).items():
            if not str(key or "").strip() or not str(value or "").strip():
                raise ContinuityInputsError(
                    f"binding.owner_refs entries must be non-empty; got {key!r}={value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "home": self.home, "now": self.now,
                "owner_refs": dict(self.owner_refs)}


@dataclass(frozen=True)
class ContinuityInputs:
    """The structured read D consumes.  Read-only, bounded, attributable, authority-free."""

    schema_version: str
    binding: ContinuityBinding
    past_state: str
    past: list[dict[str, Any]] = field(default_factory=list)
    evidence_roots: int = 0
    unfinished: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    choices: list[dict[str, Any]] = field(default_factory=list)
    next_step: Optional[dict[str, Any]] = None
    observed_eligibility_refs: list[str] = field(default_factory=list)
    next_recheck_at: Optional[str] = None
    source_states: dict[str, str] = field(default_factory=dict)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    # -- what this view can never carry ------------------------------------------------
    llm_calls: int = 0
    requires_model_call: bool = False
    authority: Optional[Any] = None
    location_authority: Optional[Any] = None
    grants: tuple[Any, ...] = ()
    candidate: Optional[Any] = None
    decision: Optional[Any] = None
    adoption: Optional[Any] = None
    may_start_activity: bool = False

    # -- refusals (a caller that tries to use recall as authority gets an error) --------
    def candidate_from_recall(self, *_: Any, **__: Any) -> Any:
        raise _refuse("a Candidate")

    def location_authority_from_recall(self, *_: Any, **__: Any) -> Any:
        raise _refuse("a current position or a movement permission")

    def grant_from_recall(self, *_: Any, **__: Any) -> Any:
        raise _refuse("a grant or a capability")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "binding": self.binding.to_dict(),
            "past_state": self.past_state,
            "past": [dict(item) for item in self.past],
            "evidence_roots": self.evidence_roots,
            "unfinished": [dict(item) for item in self.unfinished],
            "waiting": [dict(item) for item in self.waiting],
            "choices": [dict(item) for item in self.choices],
            "next_step": dict(self.next_step) if self.next_step else None,
            "observed_eligibility_refs": list(self.observed_eligibility_refs),
            "next_recheck_at": self.next_recheck_at,
            "source_states": dict(self.source_states),
            "refusals": [dict(item) for item in self.refusals],
            "llm_calls": self.llm_calls,
            "requires_model_call": self.requires_model_call,
            "authority": self.authority,
            "location_authority": self.location_authority,
            "grants": list(self.grants),
            "candidate": self.candidate,
            "decision": self.decision,
            "adoption": self.adoption,
            "may_start_activity": self.may_start_activity,
        }


class LifeContinuityInputs:
    """Builds :class:`ContinuityInputs` from injected owner reads.  Holds no database.

    ``past_source``      -- Memory boundary read port: ``recent(limit=...)`` (and, when it
                            exposes one, ``subject``); ``LifeExperiencePort`` satisfies it.
    ``activity_source``  -- LR-2 read: ``list_open_activities()`` / ``get_activity(id)``;
                            ``activity_continuity.ActivityReadService`` satisfies it.
    ``agenda_source``    -- LR-4 read: ``list_agenda_items(only_open=True)`` /
                            ``list_eligibilities(only_open=True)`` / ``get_eligibility(id)``;
                            ``activity_waiting_agenda.WaitingCoordinator`` satisfies it.
    ``governance_probe`` -- optional callable returning ``{"healthy": bool, ...}`` for the
                            Memory control ledger; ``{"healthy": False}`` fails the past view
                            closed instead of reporting a quiet day.
    """

    def __init__(
        self,
        *,
        binding: ContinuityBinding,
        past_source: Optional[Any] = None,
        activity_source: Optional[Any] = None,
        agenda_source: Optional[Any] = None,
        governance_probe: Optional[Callable[[], Mapping[str, Any]]] = None,
        max_past: int = 5,
        max_text_chars: int = 400,
    ) -> None:
        if not isinstance(binding, ContinuityBinding):
            raise ContinuityInputsError("binding must be a ContinuityBinding")
        self.binding = binding
        self.past_source = past_source
        self.activity_source = activity_source
        self.agenda_source = agenda_source
        self.governance_probe = governance_probe
        self.max_past = max(0, int(max_past))
        self.max_text_chars = max(0, int(max_text_chars))

    # -- refusals (mirrored on the reader so a caller cannot use it as authority either) --
    def candidate_from_recall(self, *_: Any, **__: Any) -> Any:
        raise _refuse("a Candidate")

    def location_authority_from_recall(self, *_: Any, **__: Any) -> Any:
        raise _refuse("a current position or a movement permission")

    def grant_from_recall(self, *_: Any, **__: Any) -> Any:
        raise _refuse("a grant or a capability")

    # -- past --------------------------------------------------------------------------

    def past(self) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
        """``(rows, past_state, refusals)``.  Never raises for a Memory-side failure."""
        refusals: list[dict[str, Any]] = []
        if self.past_source is None:
            refusals.append({"reason_code": REFUSAL_NOT_WIRED, "owner": OWNER_MEMORY})
            return [], PAST_STATE_NOT_WIRED, refusals

        declared = getattr(self.past_source, "subject", None)
        if declared is not None and str(declared) != self.binding.subject:
            refusals.append({"reason_code": REFUSAL_WRONG_SUBJECT, "owner": OWNER_MEMORY,
                             "detail": {"source_subject": str(declared),
                                        "binding_subject": self.binding.subject}})
            return [], PAST_STATE_WRONG_SUBJECT, refusals

        if self.governance_probe is not None:
            try:
                state = dict(self.governance_probe() or {})
            except Exception as exc:  # a probe that cannot answer is not a healthy answer
                refusals.append({"reason_code": REFUSAL_GOVERNANCE_UNHEALTHY,
                                 "owner": OWNER_MEMORY,
                                 "detail": f"{type(exc).__name__}: {exc}"[:200]})
                return [], PAST_STATE_UNREADABLE, refusals
            if not bool(state.get("healthy")):
                refusals.append({"reason_code": REFUSAL_GOVERNANCE_UNHEALTHY,
                                 "owner": OWNER_MEMORY,
                                 "detail": str(state.get("reason") or "unhealthy")[:200]})
                return [], PAST_STATE_UNREADABLE, refusals

        try:
            raw = self.past_source.recent(limit=self.max_past)
        except Exception as exc:
            refusals.append({"reason_code": REFUSAL_SOURCE_RAISED, "owner": OWNER_MEMORY,
                             "detail": f"{type(exc).__name__}: {exc}"[:200]})
            return [], PAST_STATE_UNREADABLE, refusals

        rows: list[dict[str, Any]] = []
        for item in list(raw or []):
            row = self._past_row(_as_mapping(item, "past record"))
            if row is None:
                refusals.append({"reason_code": REFUSAL_FOREIGN_ROW, "owner": OWNER_MEMORY,
                                 "detail": {"binding_subject": self.binding.subject}})
                continue
            rows.append(row)
        rows = self._dedupe_past(rows)
        return rows, (PAST_STATE_OK if rows else PAST_STATE_NO_HISTORY), refusals

    def _past_row(self, data: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        subject = str(data.get("subject") or "")
        if subject and subject != self.binding.subject:
            return None
        text = str(data.get("text") or "")
        if self.max_text_chars and len(text) > self.max_text_chars:
            text = text[: self.max_text_chars].rstrip() + "…"
        return {
            "owner": OWNER_MEMORY,
            "experience_id": str(data.get("experience_id") or data.get("event_id") or ""),
            "source_ref": str(data.get("source_ref") or ""),
            "occurred_at": str(data.get("occurred_at") or ""),
            "subject": subject or self.binding.subject,
            "home": self.binding.home,
            "bound_at": self.binding.now,
            "activity_ref": data.get("activity_ref"),
            "action_ref": data.get("action_ref"),
            "artifact_ref": data.get("artifact_ref"),
            "version_ref": data.get("version_ref"),
            "owner_result": data.get("owner_result"),
            "outcome_kind": data.get("outcome_kind"),
            "text": text,
        }

    @staticmethod
    def _root_of(row: Mapping[str, Any]) -> tuple[str, str, str]:
        return (str(row.get("activity_ref") or ""),
                str(row.get("version_ref") or row.get("owner_result") or ""),
                str(row.get("outcome_kind") or ""))

    def _dedupe_past(self, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """One row per source_ref, then one per root: a repeat never adds evidence."""
        by_source: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("source_ref") or row.get("experience_id"))
            kept = by_source.get(key)
            if kept is None or _newer(row, kept, "occurred_at"):
                by_source[key] = row
        by_root: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in by_source.values():
            key = self._root_of(row)
            if key == ("", "", ""):
                # A row naming no root is not a duplicate of another such row; key it by its
                # own source so it can never be silently dropped.
                key = ("", "", "source:" + str(row.get("source_ref")
                                               or row.get("experience_id")))
            kept = by_root.get(key)
            if kept is None or _newer(row, kept, "occurred_at"):
                by_root[key] = row
        ordered = sorted(by_root.values(), key=lambda r: str(r.get("occurred_at") or ""),
                         reverse=True)
        return ordered[: self.max_past] if self.max_past else []

    # -- unfinished (Activity + Agenda) -------------------------------------------------

    def _read_open_activities(self, refusals: list[dict[str, Any]]) -> tuple[list[dict], str]:
        if self.activity_source is None:
            refusals.append({"reason_code": REFUSAL_NOT_WIRED, "owner": OWNER_ACTIVITY})
            return [], SOURCE_NOT_WIRED
        try:
            raw = self.activity_source.list_open_activities()
        except Exception as exc:
            refusals.append({"reason_code": REFUSAL_SOURCE_RAISED, "owner": OWNER_ACTIVITY,
                             "detail": f"{type(exc).__name__}: {exc}"[:200]})
            return [], SOURCE_UNREADABLE
        out: list[dict[str, Any]] = []
        for item in list(raw or []):
            act = _as_mapping(item, "activity")
            if str(act.get("status") or "") not in UNFINISHED_ACTIVITY_STATUSES:
                continue
            out.append(act)
        return out, SOURCE_OK

    def _read_open_agenda_items(self, refusals: list[dict[str, Any]]) -> tuple[list[dict], str]:
        if self.agenda_source is None:
            refusals.append({"reason_code": REFUSAL_NOT_WIRED, "owner": OWNER_AGENDA})
            return [], SOURCE_NOT_WIRED
        try:
            raw = self.agenda_source.list_agenda_items(only_open=True)
        except Exception as exc:
            refusals.append({"reason_code": REFUSAL_SOURCE_RAISED, "owner": OWNER_AGENDA,
                             "detail": f"{type(exc).__name__}: {exc}"[:200]})
            return [], SOURCE_UNREADABLE
        out: list[dict[str, Any]] = []
        for item in list(raw or []):
            ag = _as_mapping(item, "agenda item")
            if str(ag.get("status") or "") in OPEN_AGENDA_STATUSES:
                out.append(ag)
        return out, SOURCE_OK

    def _read_open_eligibilities(self, refusals: list[dict[str, Any]]) -> tuple[list[dict], str]:
        if self.agenda_source is None:
            return [], SOURCE_NOT_WIRED
        try:
            raw = self.agenda_source.list_eligibilities(only_open=True)
        except Exception as exc:
            refusals.append({"reason_code": REFUSAL_SOURCE_RAISED, "owner": OWNER_AGENDA,
                             "detail": f"{type(exc).__name__}: {exc}"[:200]})
            return [], SOURCE_UNREADABLE
        return [_as_mapping(item, "resume eligibility") for item in list(raw or [])], SOURCE_OK

    def _waiting(
        self,
        activities: Sequence[Mapping[str, Any]],
        eligibilities: Sequence[Mapping[str, Any]],
        known_eligibility_refs: Iterable[str],
        since: Optional[str],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
        """A WAIT offers a resume *only* for an eligibility the caller has not seen yet."""
        known = {str(ref) for ref in known_eligibility_refs}
        since_dt = _parse_iso(since) if since else None
        open_refs: list[str] = []
        fresh_by_activity: dict[str, dict[str, Any]] = {}
        for elig in eligibilities:
            ref = str(elig.get("eligibility_id") or "")
            if not ref:
                continue
            open_refs.append(ref)
            if ref in known:
                continue
            eligible_at = elig.get("eligible_at")
            if since_dt is not None:
                eligible_dt = _parse_iso(eligible_at)
                if eligible_dt is None or eligible_dt <= since_dt:
                    continue
            activity_ref = str(elig.get("activity_id") or "")
            kept = fresh_by_activity.get(activity_ref)
            if kept is None or _newer(elig, kept, "eligible_at"):
                fresh_by_activity[activity_ref] = dict(elig)

        waiting: list[dict[str, Any]] = []
        for act in activities:
            if str(act.get("status") or "") != "WAITING":
                continue
            activity_ref = str(act.get("activity_id") or "")
            fresh = fresh_by_activity.get(activity_ref)
            waiting.append({
                "owner": OWNER_ACTIVITY,
                "activity_ref": activity_ref,
                "subject": str(act.get("subject_id") or self.binding.subject),
                "home": self.binding.home,
                "bound_at": self.binding.now,
                "status": "WAITING",
                "waiting_since": act.get("waiting_since"),
                # Echoed as the owner wrote them.  This read has no waiting-ref vocabulary of
                # its own and never re-classifies a reference (LR-4 owns that schema).
                "waiting_on_ref": act.get("waiting_on_ref"),
                "resume_condition_ref": act.get("resume_condition_ref"),
                "resume_eligibility": None if fresh is None else {
                    "owner": OWNER_AGENDA,
                    "eligibility_ref": str(fresh.get("eligibility_id") or ""),
                    "eligible_at": fresh.get("eligible_at"),
                    "activity_ref": activity_ref,
                    "activity_revision": fresh.get("activity_revision"),
                    "resume_condition_ref": fresh.get("resume_condition_ref"),
                    "waiting_episode_id": fresh.get("waiting_episode_id"),
                    "source_event_refs": list(fresh.get("source_event_refs") or []),
                },
                "resume_refused_reason": None if fresh is not None else "NO_NEW_ELIGIBILITY",
            })
        return waiting, fresh_by_activity, open_refs

    def _unfinished(
        self,
        activities: Sequence[Mapping[str, Any]],
        agenda_items: Sequence[Mapping[str, Any]],
        fresh_by_activity: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for act in activities:
            activity_ref = str(act.get("activity_id") or "")
            fresh = fresh_by_activity.get(activity_ref)
            items.append({
                "kind": "ACTIVITY",
                "owner": OWNER_ACTIVITY,
                "item_ref": activity_ref,
                "subject": str(act.get("subject_id") or self.binding.subject),
                "home": self.binding.home,
                "bound_at": self.binding.now,
                "status": str(act.get("status") or ""),
                "title": act.get("title"),
                "activity_kind": act.get("activity_kind"),
                "revision": act.get("revision"),
                "updated_at": act.get("updated_at"),
                "checkpoint_ref": act.get("checkpoint_ref"),
                "waiting_on_ref": act.get("waiting_on_ref"),
                "resume_condition_ref": act.get("resume_condition_ref"),
                "resumable": bool(str(act.get("status") or "") == "WAITING" and fresh),
                "resume_eligibility_ref": (str(fresh.get("eligibility_id") or "")
                                           if fresh else None),
                "continue_from_ref": None,
                "continue_from_reason": None,
            })
        for ag in agenda_items:
            items.append({
                "kind": "AGENDA_ITEM",
                "owner": OWNER_AGENDA,
                "item_ref": str(ag.get("agenda_id") or ""),
                "subject": str(ag.get("subject_id") or self.binding.subject),
                "home": self.binding.home,
                "bound_at": self.binding.now,
                "status": str(ag.get("status") or ""),
                "activity_ref": ag.get("activity_id"),
                "trigger_kind": ag.get("trigger_kind"),
                "due_at": ag.get("due_at"),
                "resume_condition_ref": ag.get("resume_condition_ref"),
                "waiting_episode_id": ag.get("waiting_episode_id"),
                "source_refs": list(ag.get("source_refs") or []),
                "resumable": False,
                "continue_from_ref": None,
                "continue_from_reason": None,
            })
        return items

    # -- next step / choices ------------------------------------------------------------

    @staticmethod
    def _newest_owner_confirmed(
        past: Sequence[Mapping[str, Any]],
        unfinished: Sequence[Mapping[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """The newest admitted consequence that names an object an open item can continue.

        A weak cue is ignored: free text, an unknown activity, or a consequence whose owner
        confirmed no object (a refusal/CONFLICT, say) is not a continuation point.
        """
        open_activity_refs = {str(item["item_ref"]) for item in unfinished
                              if item.get("kind") == "ACTIVITY"}
        best: Optional[dict[str, Any]] = None
        for row in past:
            if str(row.get("activity_ref") or "") not in open_activity_refs:
                continue
            if not (row.get("version_ref") or row.get("owner_result")):
                continue
            if best is None or _newer(row, best, "occurred_at"):
                best = dict(row)
        return best

    def _choices(
        self,
        unfinished: Sequence[dict[str, Any]],
        preferred_ref: Optional[str],
        history: Optional[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        choices: list[dict[str, Any]] = []
        for item in unfinished:
            resumable = bool(item.get("resumable"))
            evidence = [item["item_ref"]]
            reason = "OPEN_ITEM_CAN_CONTINUE"
            if resumable:
                reason = "WAIT_HAS_NEW_ELIGIBILITY"
                evidence.append(str(item.get("resume_eligibility_ref") or ""))
            if history is not None and str(item["item_ref"]) == str(
                    history.get("activity_ref") or ""):
                reason = "ADMITTED_CONSEQUENCE_FOR_THIS_ITEM"
                evidence.append(str(history.get("source_ref") or ""))
            choices.append({
                "choice_id": f"cont:{item['item_ref']}",
                "kind": CHOICE_RESUME if resumable else CHOICE_CONTINUE,
                "owner": item.get("owner"),
                "subject_ref": item["item_ref"],
                "activity_ref": item.get("activity_ref") or (
                    item["item_ref"] if item.get("kind") == "ACTIVITY" else None),
                "reason_code": reason,
                "evidence_refs": [ref for ref in evidence if ref],
                "preferred": str(item["item_ref"]) == str(preferred_ref or ""),
                "authority": None,
            })
        choices.append({
            "choice_id": "cont:no_action",
            "kind": CHOICE_NO_ACTION,
            "owner": None,
            "subject_ref": None,
            "activity_ref": None,
            "reason_code": "NO_ACTION_IS_ALWAYS_LEGAL",
            "evidence_refs": [],
            "preferred": preferred_ref is None,
            "authority": None,
        })
        return choices

    def _next_step(
        self,
        unfinished: list[dict[str, Any]],
        waiting: Sequence[Mapping[str, Any]],
        past: Sequence[Mapping[str, Any]],
    ) -> Optional[dict[str, Any]]:
        # 1. a WAIT whose new eligibility has arrived is over; that is the one thing that
        #    outranks ordinary open work.
        resumable = [w for w in waiting if w.get("resume_eligibility")]
        if resumable:
            chosen = sorted(resumable,
                            key=lambda w: str((w.get("resume_eligibility") or {})
                                              .get("eligible_at") or ""))[-1]
            elig = dict(chosen.get("resume_eligibility") or {})
            return {
                "kind": NEXT_STEP_RESUME_WAIT,
                "reason_code": NEXT_STEP_RESUME_WAIT,
                "item_ref": chosen.get("activity_ref"),
                "activity_ref": chosen.get("activity_ref"),
                "object_ref": chosen.get("resume_condition_ref"),
                "continue_from_ref": chosen.get("resume_condition_ref"),
                "evidence_refs": [elig.get("eligibility_ref")] + list(
                    elig.get("source_event_refs") or []),
                "subject": self.binding.subject,
                "home": self.binding.home,
                "bound_at": self.binding.now,
                "authority": None,
                "requires_model_call": False,
            }

        # 2. history: continue the object an owner actually confirmed for an open item.
        history = self._newest_owner_confirmed(past, unfinished)
        if history is not None:
            activity_ref = str(history.get("activity_ref"))
            object_ref = history.get("version_ref") or history.get("owner_result")
            for item in unfinished:
                if str(item["item_ref"]) == activity_ref:
                    item["continue_from_ref"] = object_ref
                    item["continue_from_reason"] = NEXT_STEP_HISTORY
            return {
                "kind": NEXT_STEP_HISTORY,
                "reason_code": NEXT_STEP_HISTORY,
                "item_ref": activity_ref,
                "activity_ref": activity_ref,
                "object_ref": object_ref,
                "artifact_ref": history.get("artifact_ref"),
                "continue_from_ref": object_ref,
                "evidence_refs": [ref for ref in
                                  (history.get("source_ref"), history.get("experience_id")) if ref],
                "outcome_kind": history.get("outcome_kind"),
                "subject": self.binding.subject,
                "home": self.binding.home,
                "bound_at": self.binding.now,
                "authority": None,
                "requires_model_call": False,
            }

        # 3. no admitted consequence for an open item: fall back to its own checkpoint.
        activities = [item for item in unfinished if item.get("kind") == "ACTIVITY"]
        if activities:
            chosen = sorted(activities, key=lambda i: str(i.get("updated_at") or ""))[-1]
            chosen["continue_from_ref"] = chosen.get("checkpoint_ref")
            chosen["continue_from_reason"] = NEXT_STEP_CHECKPOINT
            return {
                "kind": NEXT_STEP_CHECKPOINT,
                "reason_code": NEXT_STEP_CHECKPOINT,
                "item_ref": chosen["item_ref"],
                "activity_ref": chosen["item_ref"],
                "object_ref": chosen.get("checkpoint_ref"),
                "continue_from_ref": chosen.get("checkpoint_ref"),
                "evidence_refs": [chosen["item_ref"]],
                "subject": self.binding.subject,
                "home": self.binding.home,
                "bound_at": self.binding.now,
                "authority": None,
                "requires_model_call": False,
            }

        # 4. nothing open: quiet is a legal answer, not a failure.
        return {
            "kind": NEXT_STEP_NO_ACTION,
            "reason_code": NEXT_STEP_NO_ACTION,
            "item_ref": None,
            "activity_ref": None,
            "object_ref": None,
            "continue_from_ref": None,
            "evidence_refs": [],
            "subject": self.binding.subject,
            "home": self.binding.home,
            "bound_at": self.binding.now,
            "authority": None,
            "requires_model_call": False,
        }

    # -- the read -----------------------------------------------------------------------

    def read(
        self,
        *,
        known_eligibility_refs: Iterable[str] = (),
        since: Optional[str] = None,
    ) -> ContinuityInputs:
        refusals: list[dict[str, Any]] = []
        past, past_state, past_refusals = self.past()
        refusals.extend(past_refusals)
        activities, activity_state = self._read_open_activities(refusals)
        agenda_items, agenda_state = self._read_open_agenda_items(refusals)
        eligibilities, _elig_state = self._read_open_eligibilities(refusals)
        waiting, fresh_by_activity, open_eligibility_refs = self._waiting(
            activities, eligibilities, known_eligibility_refs, since)
        unfinished = self._unfinished(activities, agenda_items, fresh_by_activity)
        next_step = self._next_step(unfinished, waiting, past)
        choices = self._choices(
            unfinished, (next_step or {}).get("item_ref"), self._newest_owner_confirmed(past, unfinished))
        rechecks = sorted(str(ag.get("due_at")) for ag in agenda_items if ag.get("due_at"))
        return ContinuityInputs(
            schema_version=CONTINUITY_INPUTS_SCHEMA,
            binding=self.binding,
            past_state=past_state,
            past=past,
            evidence_roots=len(past),
            unfinished=unfinished,
            waiting=waiting,
            choices=choices,
            next_step=next_step,
            observed_eligibility_refs=open_eligibility_refs,
            next_recheck_at=rechecks[0] if rechecks else None,
            source_states={OWNER_MEMORY: past_state, OWNER_ACTIVITY: activity_state,
                           OWNER_AGENDA: agenda_state},
            refusals=refusals,
        )


__all__ = [
    "CHOICE_CONTINUE",
    "CHOICE_NO_ACTION",
    "CHOICE_RESUME",
    "CONTINUITY_INPUTS_SCHEMA",
    "ContinuityBinding",
    "ContinuityInputs",
    "ContinuityInputsError",
    "ContinuityRefused",
    "LifeContinuityInputs",
    "NEXT_STEP_CHECKPOINT",
    "NEXT_STEP_HISTORY",
    "NEXT_STEP_NO_ACTION",
    "NEXT_STEP_RESUME_WAIT",
    "PAST_STATE_NOT_WIRED",
    "PAST_STATE_NO_HISTORY",
    "PAST_STATE_OK",
    "PAST_STATE_UNREADABLE",
    "PAST_STATE_WRONG_SUBJECT",
]
