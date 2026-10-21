#!/usr/bin/env python3
"""v0.4 stage A: the subject's own "later" items -- proposed, kept, and reconsidered when due.

Why this module exists
----------------------
Before this package the day loop could only consider what was *already there*: a foreground
Activity, a continuable artifact, a page of the current material, an observed place.  Nothing
the subject herself decided to do *later* had anywhere to live, so "回头把这段改一遍" could be
said once and was gone, and being blocked had no honest way out other than PAUSE/ABANDON.

What an item is -- and is not
-----------------------------
* It is a **deferred intention**: a bounded title, a real time she chose, the refs it came from,
  and the time it was proposed.  It is *not* an Activity, not a Commitment, not a Memory record,
  not a permission and not a task list somebody hands her: nothing here can start, resume or
  finish anything.  Turning an item into life is still a Decision plus an adoption by the
  owners that own those verbs.
* Nothing auto-executes.  ``due`` only answers "which items of hers has the clock reached";
  the round still has to consider them, and she may cancel, reschedule or leave them standing.
* There is no deadline quota and no forced schedule.  An item keeps standing until she closes,
  cancels or reschedules it, however many times it has been considered.

Storage
-------
One JSON document under the Life state root (``life_future_items.json``), written atomically
(temp file + ``os.replace`` + fsync of file and directory).  No second owner's state is opened,
no ``fcntl`` is imported (Windows can read and run this module, which is what lets the pure
cases of the stage test run there), and no text of any private document is stored -- only the
title the decision itself produced and the refs it named.

Idempotency
-----------
A proposal is keyed on ``what was intended`` (title + the due time it named + the refs it came
from), never on the attempt, so a replayed Decision cannot mint a second copy of the same
"later".  The same key with a *different* intent is a new item, which is what a real change of
mind looks like.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

SCHEMA_VERSION = "chiyo.life.future_items.v1"
STATE_FILENAME = "life_future_items.json"

ITEM_REF_PREFIX = "fitem"
ITEM_REF_NAMESPACE = f"{ITEM_REF_PREFIX}:"

STATUS_OPEN = "OPEN"
STATUS_CANCELLED = "CANCELLED"
STATUS_DONE = "DONE"
ALL_STATUSES = (STATUS_OPEN, STATUS_CANCELLED, STATUS_DONE)

ORIGIN_MODEL_PROPOSAL = "MODEL_PROPOSAL"
ORIGIN_PARK = "PARK_CURRENT_ACTIVITY"
ORIGIN_MATERIAL = "MATERIAL_QUESTION"
ALL_ORIGINS = (ORIGIN_MODEL_PROPOSAL, ORIGIN_PARK, ORIGIN_MATERIAL)

#: How far "later" may be.  A bound, not a quota: one minute up to two weeks.  Outside it the
#: proposal is refused by name instead of being silently clamped.
MIN_DELAY_MINUTES = 1
MAX_DELAY_MINUTES = 14 * 24 * 60

MAX_TITLE_CHARS = 200
MAX_REASON_CHARS = 120
MAX_OPEN_ITEMS = 24
MAX_SOURCE_REFS = 6

_STRUCTURED_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{1,31}:[^\s]{1,200}$")


class FutureItemError(RuntimeError):
    """This store refused a write.  ``reason_code`` always says which rule."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}:{detail}" if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise FutureItemError("FUTURE_ITEM_TITLE_MISSING")
    if len(title) > MAX_TITLE_CHARS:
        raise FutureItemError("FUTURE_ITEM_TITLE_TOO_LONG", f"{len(title)} > {MAX_TITLE_CHARS}")
    return title


def _clean_refs(refs: Optional[Iterable[Any]]) -> list[str]:
    out: list[str] = []
    for ref in refs or ():
        text = str(ref or "").strip()
        if not text:
            continue
        if not _STRUCTURED_REF_RE.match(text):
            raise FutureItemError("FUTURE_ITEM_SOURCE_REF_UNSTRUCTURED", text[:80])
        if text not in out:
            out.append(text)
    if len(out) > MAX_SOURCE_REFS:
        raise FutureItemError("FUTURE_ITEM_TOO_MANY_SOURCE_REFS", str(len(out)))
    return out


def resolve_due_at(*, due_at: Any = None, in_minutes: Any = None, now: Any = None) -> str:
    """The one due time a proposal may name: an absolute instant, or a delay from ``now``.

    Exactly one of the two, and the delay is bounded (``MIN_DELAY_MINUTES`` ..
    ``MAX_DELAY_MINUTES``).  Anything else is a named refusal, never a guessed deadline.
    """
    base = _parse_iso(now) if now is not None else None
    base = base or datetime.now(timezone.utc)
    has_abs = due_at is not None and str(due_at).strip() != ""
    has_rel = in_minutes is not None and str(in_minutes).strip() != ""
    if has_abs == has_rel:
        raise FutureItemError("FUTURE_ITEM_DUE_TIME_AMBIGUOUS",
                              "name exactly one of due_at / in_minutes")
    if has_abs:
        moment = _parse_iso(due_at)
        if moment is None:
            raise FutureItemError("FUTURE_ITEM_DUE_AT_INVALID", str(due_at)[:40])
    else:
        try:
            minutes = int(str(in_minutes).strip())
        except (TypeError, ValueError):
            raise FutureItemError("FUTURE_ITEM_DELAY_NOT_AN_INTEGER", str(in_minutes)[:40]) from None
        if minutes < MIN_DELAY_MINUTES or minutes > MAX_DELAY_MINUTES:
            raise FutureItemError("FUTURE_ITEM_DELAY_OUT_OF_BOUNDS", str(minutes))
        moment = base + timedelta(minutes=minutes)
    return _iso(moment)


def time_wait_condition(*, due_at: str) -> dict[str, Any]:
    """The LR-4 time condition one item can be waited on (``TIME_AT_OR_AFTER``).

    It is spelled with the waiting-domain's ``time:`` kind so LR-2 accepts it as waiting evidence
    (the ref part must be a plain ``<namespace>:<id>`` token, so the instant is encoded there as
    epoch seconds), and the ISO instant travels in ``parameters.target_time`` -- the key
    ``activity_waiting_agenda.ResumeConditionEvaluator`` itself reads.  No expiry is invented.
    """
    moment = _parse_iso(due_at)
    if moment is None:
        raise FutureItemError("FUTURE_ITEM_DUE_AT_INVALID", str(due_at)[:40])
    # One instant, one spelling: the ref carries epoch seconds (a legal structured-ref id), the
    # ISO form in ``parameters.target_time`` is canonicalised the same way this store spells every
    # due time, so a condition and the item it belongs to can never disagree about the instant.
    canonical = _iso(moment)
    return {
        "waiting_on_ref": f"time:{int(moment.timestamp())}",
        "condition_kind": "TIME_AT_OR_AFTER",
        "parameters": {"target_time": canonical},
    }




def park_wait_spec(*, due_at: str, item_ref: Optional[str] = None) -> dict[str, Any]:
    """The AG-2 ``waiting_spec`` for "set this down until <that time>".

    Three things make this a legal park rather than a trick: the dependency is a real clock
    instant she chose (``TIME_AT_OR_AFTER``), the owner schedules its own agenda item for that
    instant so LR-4 can produce a resume eligibility when it arrives, and the foreground is
    released by the *owner's* own flag -- which is what makes it possible to take up something
    else meanwhile.  Nothing resumes automatically: an eligibility is only the right to ask.
    """
    condition = time_wait_condition(due_at=due_at)
    spec: dict[str, Any] = {
        "waiting_on_ref": condition["waiting_on_ref"],
        "condition_kind": condition["condition_kind"],
        "parameters": dict(condition["parameters"]),
        "release_foreground": True,
        # The agenda item carries the same canonical instant the condition does, so LR-4's due
        # scan and the condition it satisfies can never be about two different spellings.
        "schedule_agenda": {"trigger_kind": "TIME_AT",
                            "due_at": condition["parameters"]["target_time"]},
    }
    if item_ref:
        spec["future_item_ref"] = str(item_ref)
    return spec


#: The basis kind the cycle offers a *running* Activity when she may set it down until a time she
#: names.  It is reported as its own source so a dependency wait and a self-chosen park can never be
#: confused in a report.
PARK_BASIS_PREFIX = "parkbasis"
PARK_SOURCE = "PARK_UNTIL_CHOSEN_TIME"


def park_basis(*, activity_ref: Any, observed_at: Any,
               artifact_ref: Optional[str] = None) -> dict[str, Any]:
    """The one thing AG-1 needs in order to offer WAIT for a self-chosen park.

    AG-1 offers ``WAIT`` only when the round hands it a ``known_waiting_basis_ref``.  Until now the
    only bases were dependencies somebody else owed her, so "set this down until 10:00 and do
    something else" was not reachable.  This basis names *this* Activity and *this* round; the
    instant is not invented here -- it comes from the Decision's own ``later_item`` proposal at
    adoption time, and a WAIT without that proposal is refused by name.
    """

    activity = str(activity_ref or "")
    if not activity:
        raise FutureItemError("PARK_BASIS_ACTIVITY_MISSING")
    moment = _parse_iso(observed_at)
    if moment is None:
        raise FutureItemError("PARK_BASIS_OBSERVED_AT_INVALID", str(observed_at)[:40])
    token = _sha("|".join([activity, _iso(moment), str(artifact_ref or "")]))[:16]
    return {
        "schema_version": SCHEMA_VERSION,
        "basis_ref": f"{PARK_BASIS_PREFIX}:{token}",
        "source": PARK_SOURCE,
        "activity_ref": activity,
        "artifact_ref": (str(artifact_ref) if artifact_ref else None),
        "waiting_on_ref": None,
        "condition_kind": "TIME_AT_OR_AFTER",
        "detail": "she may set this Activity down until a time she names in this decision",
    }


def proposal_due_at(proposal: Mapping[str, Any], *, observed_at: Any) -> str:
    """The instant a *validated* proposal means, resolved against the recording round's clock."""

    if proposal.get("in_minutes") is not None:
        return resolve_due_at(in_minutes=proposal.get("in_minutes"), now=observed_at)
    due = proposal.get("due_at")
    if _parse_iso(due) is None:
        raise FutureItemError("FUTURE_ITEM_DUE_AT_INVALID", str(due)[:40])
    return str(due)


def park_wait_spec_for_decision(decision_record: Any, *, observed_at: Any,
                                activity_ref: Optional[str] = None) -> dict[str, Any]:
    """The AG-2 waiting_spec for the park this committed Decision actually asked for."""

    proposal = getattr(decision_record, "later_item_proposal", None)
    if not isinstance(proposal, Mapping) or not proposal.get("park_current"):
        raise FutureItemError(
            "PARK_TIME_REQUIRED",
            "WAIT needs a later_item proposal with park_current=true and a time")
    due = proposal_due_at(proposal, observed_at=observed_at)
    spec = park_wait_spec(due_at=due)
    if activity_ref:
        spec["activity_ref"] = str(activity_ref)
    return spec


@dataclass
class FutureItem:
    item_ref: str
    title: str
    due_at: str
    status: str
    origin: str
    created_at: str
    updated_at: str
    subject_id: Optional[str] = None
    activity_ref: Optional[str] = None
    workspace_id: Optional[str] = None
    source_refs: list[str] = field(default_factory=list)
    revision: int = 1
    times_considered: int = 0
    last_considered_at: Optional[str] = None
    resolution_reason_code: Optional[str] = None
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_ref": self.item_ref,
            "title": self.title,
            "due_at": self.due_at,
            "status": self.status,
            "origin": self.origin,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "subject_id": self.subject_id,
            "activity_ref": self.activity_ref,
            "workspace_id": self.workspace_id,
            "source_refs": list(self.source_refs),
            "revision": int(self.revision),
            "times_considered": int(self.times_considered),
            "last_considered_at": self.last_considered_at,
            "resolution_reason_code": self.resolution_reason_code,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FutureItem":
        return cls(
            item_ref=str(data["item_ref"]),
            title=str(data["title"]),
            due_at=str(data["due_at"]),
            status=str(data.get("status") or STATUS_OPEN),
            origin=str(data.get("origin") or ORIGIN_MODEL_PROPOSAL),
            created_at=str(data["created_at"]),
            updated_at=str(data.get("updated_at") or data["created_at"]),
            subject_id=data.get("subject_id"),
            activity_ref=data.get("activity_ref"),
            workspace_id=data.get("workspace_id"),
            source_refs=[str(r) for r in (data.get("source_refs") or [])],
            revision=int(data.get("revision") or 1),
            times_considered=int(data.get("times_considered") or 0),
            last_considered_at=data.get("last_considered_at"),
            resolution_reason_code=data.get("resolution_reason_code"),
            idempotency_key=str(data.get("idempotency_key") or ""),
        )

    def is_due(self, now_at: Any) -> bool:
        if self.status != STATUS_OPEN:
            return False
        due = _parse_iso(self.due_at)
        now = _parse_iso(now_at) or datetime.now(timezone.utc)
        return due is not None and due <= now


class FutureItems:
    """The one store of her own "later" items, under one Life state root."""

    def __init__(self, state_root: Path | str, *, subject_id: Optional[str] = None,
                 max_open_items: int = MAX_OPEN_ITEMS) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / STATE_FILENAME
        self.subject_id = str(subject_id) if subject_id else None
        self.max_open_items = int(max_open_items)
        if self.max_open_items < 1:
            raise FutureItemError("FUTURE_ITEM_CAPACITY_INVALID", str(max_open_items))

    # -- storage ----------------------------------------------------------------------

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "subject_id": self.subject_id,
                "revision": 0, "items": {}}

    def read_state(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._empty()
        except OSError as exc:  # pragma: no cover - an unreadable state is reported
            raise FutureItemError("FUTURE_ITEM_STATE_UNREADABLE", str(exc)[:120]) from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise FutureItemError("FUTURE_ITEM_STATE_CORRUPT", str(exc)[:120]) from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise FutureItemError("FUTURE_ITEM_STATE_SCHEMA_MISMATCH", str(data)[:80])
        items = data.get("items")
        data["items"] = dict(items) if isinstance(items, dict) else {}
        data.setdefault("revision", 0)
        return data

    def _write_state(self, state: Mapping[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "subject_id": state.get("subject_id"),
            "revision": int(state.get("revision") or 0),
            "items": dict(state.get("items") or {}),
        }
        tmp = self.path.with_suffix(self.path.suffix + f".tmp-{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        try:
            dir_fd = os.open(str(self.state_root), os.O_RDONLY)
        except OSError:  # pragma: no cover - directory fsync is best effort
            return
        try:
            os.fsync(dir_fd)
        except OSError:  # pragma: no cover
            pass
        finally:
            os.close(dir_fd)

    # -- reads ------------------------------------------------------------------------

    def items(self, *, status: Optional[str] = None) -> list[FutureItem]:
        state = self.read_state()
        rows = [FutureItem.from_dict(row) for row in (state.get("items") or {}).values()]
        if status is not None:
            rows = [row for row in rows if row.status == status]
        rows.sort(key=lambda row: (row.due_at, row.item_ref))
        return rows

    def open_items(self) -> list[FutureItem]:
        return self.items(status=STATUS_OPEN)

    def get(self, item_ref: Any) -> Optional[FutureItem]:
        ref = str(item_ref or "")
        if ref and not ref.startswith(ITEM_REF_NAMESPACE):
            ref = ITEM_REF_NAMESPACE + ref
        row = (self.read_state().get("items") or {}).get(ref)
        return FutureItem.from_dict(row) if isinstance(row, Mapping) else None

    def due(self, now_at: Any) -> list[FutureItem]:
        """The OPEN items the clock has reached.  Reads only; nothing is mutated."""
        now = _parse_iso(now_at) or datetime.now(timezone.utc)
        return [row for row in self.open_items() if row.is_due(_iso(now))]

    # -- writes -----------------------------------------------------------------------

    def propose(self, *, title: Any, due_at: Any = None, in_minutes: Any = None,
                origin: str = ORIGIN_MODEL_PROPOSAL, source_refs: Optional[Sequence[Any]] = None,
                activity_ref: Optional[str] = None, workspace_id: Optional[str] = None,
                now: Any = None) -> dict[str, Any]:
        """Record one deferred intention.  Returns ``{"item": ..., "created": bool}``.

        The clock used for a relative delay is the caller's ``now`` (the round's own observed
        instant), so a replayed round proposes the same instant instead of drifting.
        """
        moment = _parse_iso(now) if now is not None else None
        observed = _iso(moment) if moment else _utc_now_iso()
        clean_title = _clean_title(title)
        origin_text = str(origin or ORIGIN_MODEL_PROPOSAL)
        if origin_text not in ALL_ORIGINS:
            raise FutureItemError("FUTURE_ITEM_ORIGIN_UNSUPPORTED", origin_text)
        resolved_due = resolve_due_at(due_at=due_at, in_minutes=in_minutes, now=observed)
        refs = _clean_refs(source_refs)
        intent = "|".join([clean_title, resolved_due, ",".join(sorted(refs)), origin_text])
        idem = f"fitem_intent:{_sha(intent)[:24]}"

        state = self.read_state()
        items: dict[str, Any] = state["items"]
        for existing in items.values():
            if str(existing.get("idempotency_key")) == idem and \
                    str(existing.get("status")) == STATUS_OPEN:
                return {"item": FutureItem.from_dict(existing).to_dict(), "created": False}

        open_count = sum(1 for row in items.values()
                         if str(row.get("status")) == STATUS_OPEN)
        if open_count >= self.max_open_items:
            raise FutureItemError("FUTURE_ITEM_CAPACITY_FULL", f"{open_count} open")

        item_ref = f"{ITEM_REF_NAMESPACE}{_sha(idem + observed)[:16]}"
        record = FutureItem(
            item_ref=item_ref, title=clean_title, due_at=resolved_due, status=STATUS_OPEN,
            origin=origin_text, created_at=observed, updated_at=observed,
            subject_id=self.subject_id, activity_ref=(str(activity_ref) if activity_ref else None),
            workspace_id=(str(workspace_id) if workspace_id else None),
            source_refs=refs, revision=1, idempotency_key=idem,
        ).to_dict()
        items[item_ref] = record
        state["revision"] = int(state.get("revision") or 0) + 1
        self._write_state(state)
        return {"item": record, "created": True}

    def _mutate(self, item_ref: Any, *, change: dict[str, Any], reason: str,
                now: Any = None) -> dict[str, Any]:
        moment = _parse_iso(now)
        observed = _iso(moment) if moment else _utc_now_iso()
        state = self.read_state()
        items: dict[str, Any] = state["items"]
        ref = str(item_ref or "")
        if ref and not ref.startswith(ITEM_REF_NAMESPACE):
            ref = ITEM_REF_NAMESPACE + ref
        raw = items.get(ref)
        if not isinstance(raw, Mapping):
            raise FutureItemError("FUTURE_ITEM_NOT_FOUND", ref[:80])
        if str(raw.get("status")) != STATUS_OPEN:
            raise FutureItemError("FUTURE_ITEM_NOT_OPEN", str(raw.get("status")))
        updated = dict(raw)
        updated.update(change)
        updated["updated_at"] = observed
        updated["revision"] = int(raw.get("revision") or 1) + 1
        updated["resolution_reason_code"] = str(reason)[:MAX_REASON_CHARS]
        items[ref] = updated
        state["revision"] = int(state.get("revision") or 0) + 1
        self._write_state(state)
        return FutureItem.from_dict(updated).to_dict()

    def reschedule(self, item_ref: Any, *, due_at: Any = None, in_minutes: Any = None,
                   reason: str = "RESCHEDULED", now: Any = None) -> dict[str, Any]:
        moment = _parse_iso(now)
        observed = _iso(moment) if moment else _utc_now_iso()
        resolved = resolve_due_at(due_at=due_at, in_minutes=in_minutes, now=observed)
        return self._mutate(item_ref, change={"due_at": resolved}, reason=reason, now=observed)

    def cancel(self, item_ref: Any, *, reason: str = "CANCELLED_BY_SUBJECT",
               now: Any = None) -> dict[str, Any]:
        return self._mutate(item_ref, change={"status": STATUS_CANCELLED}, reason=reason, now=now)

    def close(self, item_ref: Any, *, reason: str = "DONE", now: Any = None) -> dict[str, Any]:
        return self._mutate(item_ref, change={"status": STATUS_DONE}, reason=reason, now=now)

    def note_considered(self, item_ref: Any, *, at: Any = None) -> dict[str, Any]:
        """Record that one round *looked at* this item (a report, never a completion)."""
        state = self.read_state()
        items: dict[str, Any] = state["items"]
        ref = str(item_ref or "")
        if ref and not ref.startswith(ITEM_REF_NAMESPACE):
            ref = ITEM_REF_NAMESPACE + ref
        raw = items.get(ref)
        if not isinstance(raw, Mapping) or str(raw.get("status")) != STATUS_OPEN:
            return {}
        updated = dict(raw)
        updated["times_considered"] = int(raw.get("times_considered") or 0) + 1
        updated["last_considered_at"] = _iso(_parse_iso(at) or datetime.now(timezone.utc))
        items[ref] = updated
        state["revision"] = int(state.get("revision") or 0) + 1
        self._write_state(state)
        return FutureItem.from_dict(updated).to_dict()

    # -- report -----------------------------------------------------------------------

    def view(self, *, now: Any = None, limit: int = 8) -> dict[str, Any]:
        now_iso = _iso(_parse_iso(now) or datetime.now(timezone.utc))
        try:
            rows = self.items()
        except FutureItemError as exc:
            return {"status": "READ_FAILED", "reason_code": exc.reason_code,
                    "open_count": 0, "due_refs": [], "open": []}
        open_rows = [row for row in rows if row.status == STATUS_OPEN]
        due_rows = [row for row in open_rows if row.is_due(now_iso)]
        return {
            "status": "OK",
            "now": now_iso,
            "open_count": len(open_rows),
            "due_refs": [row.item_ref for row in due_rows],
            "due": [{key: row.to_dict().get(key) for key in
                     ("item_ref", "title", "due_at", "origin", "times_considered")}
                    for row in due_rows[:limit]],
            "open": [{key: row.to_dict().get(key) for key in
                      ("item_ref", "title", "due_at", "origin", "times_considered")}
                     for row in open_rows[:limit]],
        }


def candidate_items(future_items: Optional[FutureItems], *, now: Any) -> list[dict[str, Any]]:
    """The bounded, text-free candidate payloads for the due items of one round (0 LLM).

    Each entry is what the PERSONAL_OPPORTUNITY source needs to build one candidate: the item's
    own ref, the refs it came from, and (when it named one) the artifact it may be carried out
    on.  No item text is copied into a permission, and no item is turned into a Decision.
    """
    if future_items is None:
        return []
    try:
        due = future_items.due(now)
    except FutureItemError:
        return []
    out: list[dict[str, Any]] = []
    for row in due:
        payload = row.to_dict()
        artifact_ref = next((ref for ref in row.source_refs
                             if str(ref).startswith("artifact:")), None)
        payload["artifact_ref"] = artifact_ref
        out.append(payload)
    return out


__all__ = [
    "ALL_ORIGINS",
    "ALL_STATUSES",
    "FutureItem",
    "FutureItemError",
    "FutureItems",
    "ITEM_REF_NAMESPACE",
    "ITEM_REF_PREFIX",
    "MAX_DELAY_MINUTES",
    "MAX_OPEN_ITEMS",
    "MAX_TITLE_CHARS",
    "MIN_DELAY_MINUTES",
    "ORIGIN_MATERIAL",
    "ORIGIN_MODEL_PROPOSAL",
    "ORIGIN_PARK",
    "SCHEMA_VERSION",
    "STATE_FILENAME",
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_OPEN",
    "candidate_items",
    "park_wait_spec",
    "resolve_due_at",
    "time_wait_condition",
]


ACTION_CANCEL = "cancel"
ACTION_RESCHEDULE = "reschedule"
ALL_ACTIONS = (ACTION_CANCEL, ACTION_RESCHEDULE)
MAX_ACTIONS_PER_DECISION = 3


def validate_actions(raw: Any) -> list[dict[str, Any]]:
    """The bounded shape of "what to do with an item I already have" (cancel / reschedule).

    At most ``MAX_ACTIONS_PER_DECISION``, each naming an item by its own ref.  An ``fitem:``
    shaped ref is required: this cannot reach any other owner's object.  Nothing is written here.
    """
    if isinstance(raw, (str, bytes)) or isinstance(raw, Mapping) or not isinstance(raw, Iterable):
        raise FutureItemError("FUTURE_ITEM_ACTIONS_NOT_A_LIST")
    rows = list(raw)
    if len(rows) > MAX_ACTIONS_PER_DECISION:
        raise FutureItemError("FUTURE_ITEM_ACTIONS_TOO_MANY", str(len(rows)))
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, (str, bytes)) or not isinstance(row, Mapping):
            raise FutureItemError("FUTURE_ITEM_ACTION_NOT_A_MAPPING")
        allowed = {"item_ref", "op", "due_at", "in_minutes", "reason"}
        unknown = set(row.keys()) - allowed
        if unknown:
            raise FutureItemError("FUTURE_ITEM_ACTION_UNKNOWN_FIELD",
                                  ",".join(sorted(map(str, unknown))))
        ref = str(row.get("item_ref") or "").strip()
        if not ref:
            raise FutureItemError("FUTURE_ITEM_ACTION_REF_MISSING")
        if not ref.startswith(ITEM_REF_NAMESPACE):
            ref = ITEM_REF_NAMESPACE + ref
        op = str(row.get("op") or "").strip().lower()
        if op not in ALL_ACTIONS:
            raise FutureItemError("FUTURE_ITEM_ACTION_UNSUPPORTED", op[:40])
        entry: dict[str, Any] = {"item_ref": ref, "op": op}
        if op == ACTION_RESCHEDULE:
            entry["due_at"] = resolve_due_at(due_at=row.get("due_at"),
                                             in_minutes=row.get("in_minutes"))
        elif row.get("due_at") is not None or row.get("in_minutes") is not None:
            raise FutureItemError("FUTURE_ITEM_ACTION_DUE_TIME_NOT_APPLICABLE", op)
        reason = " ".join(str(row.get("reason") or "").split())[:MAX_REASON_CHARS]
        if reason:
            entry["reason"] = reason
        out.append(entry)
    return out


def validate_proposal(raw: Any) -> dict[str, Any]:
    """The bounded shape of one *proposal* a Decision output may carry.

    Kept here rather than in the decision validator so there is exactly one definition of what a
    "later item" is.  Nothing is written: a proposal is validated, then (and only then) the round
    records it in the store.  The delay she named is echoed as ``in_minutes`` so the recording
    round can resolve it against its own observed instant.
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Mapping):
        raise FutureItemError("FUTURE_ITEM_PROPOSAL_NOT_A_MAPPING")
    allowed = {"title", "due_at", "in_minutes", "source_refs", "note", "park_current"}
    unknown = set(raw.keys()) - allowed
    if unknown:
        raise FutureItemError("FUTURE_ITEM_PROPOSAL_UNKNOWN_FIELD",
                              ",".join(sorted(map(str, unknown))))
    title = _clean_title(raw.get("title"))
    # Re-validating our own output must be a no-op: the validator echoes the delay it was given
    # *and* the instant that delay resolved to, so both being present is the normal shape of a
    # proposal already carried on a DecisionRecord (the record re-checks itself when rebuilt).
    has_due = raw.get("due_at") not in (None, "")
    has_delay = raw.get("in_minutes") not in (None, "")
    if has_due and has_delay:
        due = str(raw.get("due_at"))
        if _parse_iso(due) is None:
            raise FutureItemError("FUTURE_ITEM_DUE_AT_INVALID", str(due)[:40])
    else:
        due = resolve_due_at(due_at=raw.get("due_at"), in_minutes=raw.get("in_minutes"))
    refs = _clean_refs(raw.get("source_refs"))
    note = " ".join(str(raw.get("note") or "").split())[:MAX_REASON_CHARS] or None
    # v0.4 stage A (park): ``park_current`` is the model saying "set the running Activity down
    # until then".  It changes the *meaning* of the proposal (the round then has to WAIT with a
    # time condition), so it must be an explicit boolean -- never inferred from the title.
    park = raw.get("park_current")
    if park is not None and not isinstance(park, bool):
        raise FutureItemError("FUTURE_ITEM_PARK_FLAG_NOT_A_BOOLEAN", str(park)[:32])
    out: dict[str, Any] = {"title": title, "due_at": due, "source_refs": refs, "note": note,
                           "park_current": bool(park)}
    if has_delay:
        # Keep the delay she named: the recording round re-resolves it against its own clock, and a
        # rebuilt DecisionRecord re-validates this exact mapping (so it must stay idempotent).
        out["in_minutes"] = int(str(raw.get("in_minutes")).strip())
    return out


def apply_actions(store: "FutureItems", actions: Sequence[Mapping[str, Any]], *,
                  now: Any = None) -> list[dict[str, Any]]:
    """Apply validated actions to the store, one owner write each; refusals are reported.

    A refused action is *not* silently dropped and never retried as something else: the caller
    gets one row per action saying what really happened (``APPLIED`` / the store's own reason).
    """
    results: list[dict[str, Any]] = []
    for action in actions or ():
        ref = str(action.get("item_ref"))
        op = str(action.get("op"))
        try:
            if op == ACTION_RESCHEDULE:
                row = store.reschedule(
                    ref, due_at=action.get("due_at"),
                    reason=str(action.get("reason") or "RESCHEDULED_BY_SUBJECT"), now=now)
            else:
                row = store.cancel(
                    ref, reason=str(action.get("reason") or "CANCELLED_BY_SUBJECT"), now=now)
        except FutureItemError as exc:
            results.append({"item_ref": ref, "op": op, "status": "REFUSED",
                            "reason_code": exc.reason_code})
            continue
        results.append({"item_ref": ref, "op": op, "status": "APPLIED",
                        "due_at": row.get("due_at"), "item_status": row.get("status")})
    return results


__all__ += [
    "ACTION_CANCEL",
    "ACTION_RESCHEDULE",
    "ALL_ACTIONS",
    "MAX_ACTIONS_PER_DECISION",
    "apply_actions",
    "validate_actions",
    "validate_proposal",
]

__all__ += [
    "PARK_BASIS_PREFIX",
    "PARK_SOURCE",
    "park_basis",
    "park_wait_spec_for_decision",
    "proposal_due_at",
]
