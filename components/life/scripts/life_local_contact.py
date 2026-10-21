#!/usr/bin/env python3
"""v0.4 stage A: the local visit inbox -- a bounded transport inbox and an operation ledger.

Why this module exists
---------------------
The life side had no honest place to put *an inbound contact event*.  ``life_co_presence``
encodes the co-presence protocol and ``life_cycle`` runs the day, but nothing durable could
answer the three questions a transport actually has to answer:

* "did this arrival land?"  (a bounded inbox that survives a restart),
* "was it already handled?" (an operation key plus the *result* she recorded),
* "what did she actually do with it?" (a persisted outcome, not a tick-local variable).

Without that, "he came by, I was in the middle of reading" could only be a transient in one
process, and a restart would silently re-ask the model about the same message forever.

What this module is
-------------------
* A **transport ledger**: bounded events, per-event operation keys, per-event persisted
  results, and a lookup by key.  One JSON document under the Life state root.
* A **validator** for the three inbound kinds only: an ordinary local message, an explicit
  invitation, an explicit end.  Identity (subject / home) comes from the *instance*
  constructor, never from the payload.
* A **codec bridge**: an invitation event carries a ready-made ``invitation`` block with
  exactly the fields ``life_co_presence.parse_invitation`` accepts, and an end event carries
  an ``end`` block for ``life_co_presence.parse_end``.  The strings of the two event kinds
  are identical to the protocol's own constants; this module does **not** import it (B owns
  that file).

What this module deliberately is not
------------------------------------
* **Not a relationship owner.**  No trust, no intimacy score, no relation graph, no chat
  domain.  One fixed local source, one fixed channel.
* **Not a second Activity owner.**  It stores no Activity status, no foreground, no
  pause bit and no checkpoint.  An ordinary message is ``OBSERVATION_ONLY`` here: ``submit``
  performs zero owner writes and the answer says so.  The result fields a caller may record
  are a closed whitelist, and ``ACCEPTED`` on an ordinary message is refused by name, so an
  "observation" can never be read back as engagement.
* **Not "the user received it".**  This is the instance's own local archive of a local
  arrival.  There is no external channel, no delivery confirmation and no read receipt.
* **Not an executor.**  ``GET`` (every read method) is pure: no mkdir, no lock file, no
  revision bump, no expiry write, nothing started and no model asked.  Nothing in this file
  imports a model client.

Fail closed, never "empty"
--------------------------
A state file that is missing is ``ABSENT`` (a legitimate fresh inbox).  A state file that
exists but cannot be parsed as this schema -- garbage, a half-written JSON, a wrong
schema_version -- is ``CONTACT_STATE_CORRUPT`` / ``CONTACT_STATE_EMPTY`` /
``CONTACT_STATE_SCHEMA_MISMATCH``: reads report it and write paths refuse, so a real arrival
is never silently replaced by "no events".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows has no flock
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX has no msvcrt
    msvcrt = None  # type: ignore[assignment]

SCHEMA_VERSION = "chiyo.life.local_contact.v1"
STATE_FILENAME = "life_local_contact.json"
LOCK_FILENAME = "life_local_contact.lock"

#: The only channel this inbox speaks.  Anything else is refused, not renamed.
CHANNEL_LOCAL_INBOX = "local_inbox"
#: The only source an event may carry: a local operator/person *in this instance's own
#: machine*.  It is deliberately not a remote account id.
SOURCE_LOCAL = "local_operator"

KIND_MESSAGE = "LOCAL_MESSAGE"
KIND_INVITATION = "CO_PRESENCE_INVITATION"
KIND_END = "CO_PRESENCE_END"
ALL_KINDS = (KIND_MESSAGE, KIND_INVITATION, KIND_END)

#: Aliases a caller may use for the kind.  Mapped, never guessed beyond this table.
_KIND_ALIASES = {
    "MESSAGE": KIND_MESSAGE,
    "LOCAL_MESSAGE": KIND_MESSAGE,
    "ORDINARY_MESSAGE": KIND_MESSAGE,
    "INVITATION": KIND_INVITATION,
    "CO_PRESENCE_INVITATION": KIND_INVITATION,
    "END": KIND_END,
    "CO_PRESENCE_END": KIND_END,
}

#: What the event may cause.  ``OBSERVATION_ONLY`` never pauses and never starts anything.
EFFECT_OBSERVATION_ONLY = "OBSERVATION_ONLY"
EFFECT_INVITATION = "INVITATION"
EFFECT_END = "END"
_EFFECT_OF_KIND = {
    KIND_MESSAGE: EFFECT_OBSERVATION_ONLY,
    KIND_INVITATION: EFFECT_INVITATION,
    KIND_END: EFFECT_END,
}

#: Per-event processing state (the *transport* question, not an Activity status).
STATE_PENDING = "PENDING"           # nobody has claimed it yet
STATE_CLAIMED = "CLAIMED"           # one consumer said "I am handling this"
STATE_DEFERRED = "DEFERRED"         # she deferred it to a *named* condition/opportunity
STATE_UNKNOWN = "UNKNOWN"           # the consumer does not know the owner's answer yet
STATE_PROCESSED = "PROCESSED"       # a terminal outcome is recorded
ALL_STATES = (STATE_PENDING, STATE_CLAIMED, STATE_DEFERRED, STATE_UNKNOWN, STATE_PROCESSED)

#: Outcomes the consumer (B) may record.  Closed vocabulary on purpose.
OUTCOME_OBSERVED = "OBSERVED"       # ordinary message: noticed only
OUTCOME_ACCEPTED = "ACCEPTED"       # invitation: she chose to engage
OUTCOME_DECLINED = "DECLINED"       # invitation: she chose not to
OUTCOME_DEFERRED = "DEFERRED"       # invitation: a named later condition/opportunity
OUTCOME_ENDED = "ENDED"             # end event bound to the invitation it closes
OUTCOME_UNKNOWN = "UNKNOWN"         # owner's answer unknown; reconcile by the same key
ALL_OUTCOMES = (OUTCOME_OBSERVED, OUTCOME_ACCEPTED, OUTCOME_DECLINED, OUTCOME_DEFERRED,
                OUTCOME_ENDED, OUTCOME_UNKNOWN)

#: Outcomes that end the event's life.  A terminal outcome never changes into another one.
TERMINAL_OUTCOMES = (OUTCOME_OBSERVED, OUTCOME_ACCEPTED, OUTCOME_DECLINED, OUTCOME_ENDED)

#: Which outcome each kind may record.  This is the guard that an ordinary message can never
#: be turned into engagement (and an invitation can never be closed by an end event).
_OUTCOMES_BY_KIND = {
    KIND_MESSAGE: (OUTCOME_OBSERVED, OUTCOME_UNKNOWN),
    KIND_INVITATION: (OUTCOME_ACCEPTED, OUTCOME_DECLINED, OUTCOME_DEFERRED, OUTCOME_UNKNOWN),
    KIND_END: (OUTCOME_ENDED, OUTCOME_UNKNOWN),
}

#: Bounds.  Every one of them refuses by name instead of silently truncating or dropping.
MAX_BODY_CHARS = 2000
MAX_SCOPE_CHARS = 200
MAX_DETAIL_CHARS = 400
MAX_REASON_CHARS = 64
MAX_EVENTS = 200
MAX_RESULTS_PER_EVENT = 8
MAX_ATTEMPTS_PER_EVENT = 8
MAX_REF_CHARS = 200
MAX_LIST = 50

_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{1,31}:[^\s]{1,200}$")
_EVENT_REF_PREFIX = "contact_evt"
_OP_REF_PREFIX = "contact_op"
_CLAIM_PREFIX = "contact_claim"
_INVITATION_REF_PREFIX = "localinvite"

#: The closed field whitelist of a ``submit`` payload.  Unknown keys are refused, so a
#: caller can never smuggle an Activity status / pause flag / model prompt through here.
ALLOWED_SUBMIT_FIELDS = frozenset({
    "event_kind", "kind", "text", "body", "message",
    "source_ref", "occurred_at", "expires_at", "scope", "invitation_id",
    "requires_engagement", "atomic", "duration_seconds", "explicit_end",
    "completion_evidence_ref", "reason", "conversation_ref", "message_ref",
    "subject_id", "home_ref", "channel", "operation_key", "received_at",
    "evidence_ref",
    # v0.4 shared practice: an invitation may name the shared materials it is about.  It is a
    # *request* (A reads each one through the owner at open), never a claim of sharing, and it is
    # refused on a plain message: a message must not be able to become an invitation.
    "material_refs",
})

#: The closed field whitelist of a recorded result.  No Activity field exists here at all.
ALLOWED_RESULT_FIELDS = frozenset({
    "outcome", "reason_code", "detail", "session_ref", "invitation_ref",
    "decision_ref", "defer_condition", "opportunity_ref", "owner_refs",
    "operation_key", "now",
})

#: Fields refused on a particular kind, because carrying them would *claim* an effect the
#: kind does not have (an ordinary message carrying a scope would claim an invitation).
_FIELDS_BY_KIND = {
    KIND_MESSAGE: frozenset({"scope", "invitation_id", "requires_engagement", "atomic",
                             "expires_at", "duration_seconds", "explicit_end",
                             "completion_evidence_ref", "material_refs"}),
    KIND_INVITATION: frozenset({"explicit_end", "completion_evidence_ref"}),
    KIND_END: frozenset({"scope", "requires_engagement", "atomic", "expires_at",
                         "duration_seconds", "material_refs"}),
}


class LocalContactError(RuntimeError):
    """A named refusal.  ``reason_code`` is always one of the CRYPTIC-looking CONTACT_* codes."""

    def __init__(self, reason_code: str, detail: str = "", **extra: Any) -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        self.extra = dict(extra)


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(moment: Optional[datetime]) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds")


def _instant(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _now_from(clock: Optional[Callable[[], Any]]) -> str:
    if clock is None:
        return _iso(None)
    try:
        raw = clock()
    except Exception:  # pragma: no cover - a broken clock must not fabricate a time
        return _iso(None)
    moment = _instant(raw)
    return _iso(moment) if moment else _iso(None)


def _text(value: Any, field: str, *, max_chars: int, required: bool = False) -> str:
    text = " ".join(str(value or "").split()) if field in ("scope", "detail") else str(value or "")
    if field in ("reason_code", "reason"):
        text = str(value or "").strip()
    if not text:
        if required:
            raise LocalContactError("CONTACT_%s_REQUIRED" % field.upper())
        return ""
    if len(text) > max_chars:
        raise LocalContactError("CONTACT_%s_TOO_LONG" % field.upper(),
                                "%d > %d" % (len(text), max_chars))
    return text


def _ref(value: Any, field: str, *, required: bool = False) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        if required:
            raise LocalContactError("CONTACT_%s_REQUIRED" % field.upper())
        return None
    if len(text) > MAX_REF_CHARS:
        raise LocalContactError("CONTACT_%s_TOO_LONG" % field.upper(),
                                "%d > %d" % (len(text), MAX_REF_CHARS))
    if not _REF_RE.match(text):
        raise LocalContactError("CONTACT_%s_NOT_A_STRUCTURED_REF" % field.upper(), text[:80])
    return text


def _material_refs(value: Any) -> tuple[list[str], str]:
    """``material_refs`` on an invitation: structured artifact references only.

    The shape/limit rules live in ``shared_practice_life`` (one source of truth); this returns the
    cleaned list plus a refusal detail (``""`` when fine) so the caller can refuse by name.
    """

    import shared_practice_life as spl

    return spl.bound_material_refs(value)


def _body_of(payload: Mapping[str, Any]) -> str:
    given = [(key, str(payload.get(key) or "")) for key in ("text", "body", "message")
             if str(payload.get(key) or "").strip()]
    if len(given) > 1:
        raise LocalContactError("CONTACT_BODY_AMBIGUOUS",
                                "name exactly one of text / body / message")
    if not given:
        return ""
    value = given[0][1]
    if len(value) > MAX_BODY_CHARS:
        raise LocalContactError("CONTACT_BODY_TOO_LONG", "%d > %d" % (len(value),
                                                                     MAX_BODY_CHARS))
    return value


def _refuse_unknown_fields(payload: Mapping[str, Any], allowed: Sequence[str] | frozenset,
                           *, prefix: str) -> None:
    extra = sorted(set(str(key) for key in payload) - set(allowed))
    if extra:
        raise LocalContactError("%s_FIELD_REFUSED" % prefix, ",".join(extra[:5]))


def _intent_digest(record: Mapping[str, Any], *, supplied_times: Sequence[str]) -> str:
    """The stable identity of one arrival: what it *is*, not the clock that stamped it.

    A timestamp the *caller* supplied is part of the identity (a replay of the same client
    payload is the same arrival); a timestamp this module defaulted from its own clock is
    not, so an identical retry is still the same arrival instead of a second one.  A genuine
    second message with the same words is a different ``event_ref`` (the sequence moves) and
    is kept -- the digest says "same intent", the sequence says "a second time".
    """

    payload = {key: record[key] for key in sorted(record)
               if key not in ("payload_digest", "event_ref", "seq")}
    for key in ("occurred_at", "received_at", "observed_at"):
        if key not in supplied_times:
            payload.pop(key, None)
    return "sha256:" + _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def default_operation_key(payload_digest: str) -> str:
    """The deterministic key of one arrival: the same intent is the same key."""

    return "%s:%s" % (_OP_REF_PREFIX, _sha(payload_digest)[:24])


# ---------------------------------------------------------------------------------------------
# the storage primitives
# ---------------------------------------------------------------------------------------------


def state_path(state_root: Path | str) -> Path:
    return Path(state_root) / STATE_FILENAME


def lock_path(state_root: Path | str) -> Path:
    return Path(state_root) / LOCK_FILENAME


@contextmanager
def write_lock(state_root: Path | str, *, timeout: float = 20.0) -> Iterator[None]:
    """The cross-process append lock.  Only *write* paths take it.

    A *separate* file from the state document, because the state document is replaced
    (``os.replace``) on every write and a lock on the replaced inode would silently move.

    POSIX uses ``flock``.  Windows uses ``msvcrt.locking`` on a byte of the same lock file,
    so the same read-modify-write is serialised on both platforms and a test can prove it
    with real processes on either.
    """

    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_FILENAME
    handle = path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            deadline = time.monotonic() + max(0.0, float(timeout))
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LocalContactError("CONTACT_LOCK_TIMEOUT")
                    time.sleep(0.01)
        yield
    finally:
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:  # pragma: no cover
                    pass
            elif msvcrt is not None:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:  # pragma: no cover
                    pass
        finally:
            handle.close()


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # pragma: no cover - best effort on Windows
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(fd)


def _write_atomically(path: Path, payload: Mapping[str, Any]) -> None:
    """Unique temp name + flush + fsync + ``os.replace`` + directory fsync."""

    tmp = path.with_name("%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=1,
                                    sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():  # pragma: no cover - only on a failed replace
            try:
                tmp.unlink()
            except OSError:
                pass
    _fsync_directory(path.parent)


# ---------------------------------------------------------------------------------------------
# the inbox
# ---------------------------------------------------------------------------------------------


class LocalContactInbox:
    """The one bounded local visit inbox of one instance, under one Life state root.

    ``state_root`` is the Life state root the instance already uses; ``subject_id`` and
    ``home_ref`` are the **instance's** identity (the same values the runtime hands to
    ``FiniteCoPresence``), not something a client may send.  A payload that names another
    subject or home is refused; a payload that names the right ones is still stored with the
    instance's own values, and the fact that a client identity was ignored is recorded.
    """

    def __init__(self, state_root: Path | str, *, subject_id: str, home_ref: str,
                 max_events: int = MAX_EVENTS,
                 clock: Optional[Callable[[], Any]] = None) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / STATE_FILENAME
        subject = str(subject_id or "").strip()
        if not subject:
            raise LocalContactError("CONTACT_SUBJECT_REQUIRED")
        home = str(home_ref or "").strip()
        if not home:
            raise LocalContactError("CONTACT_HOME_REQUIRED")
        if len(home) > MAX_REF_CHARS:
            raise LocalContactError("CONTACT_HOME_TOO_LONG")
        self.subject_id = subject
        self.home_ref = home
        self.max_events = int(max_events)
        if self.max_events < 1:
            raise LocalContactError("CONTACT_CAPACITY_INVALID", str(max_events))
        self._clock = clock

    # -- identity ---------------------------------------------------------------------

    def _now(self) -> str:
        return _now_from(self._clock)

    def describe(self) -> dict[str, Any]:
        """What this object is and is not.  A read; it writes nothing."""

        return {
            "owner": "LocalContactInbox",
            "schema_version": SCHEMA_VERSION,
            "state_root": str(self.state_root),
            "state_path": str(self.path),
            "subject_id": self.subject_id,
            "home_ref": self.home_ref,
            "channel": CHANNEL_LOCAL_INBOX,
            "source": SOURCE_LOCAL,
            "kinds": list(ALL_KINDS),
            "outcomes": list(ALL_OUTCOMES),
            "terminal_outcomes": list(TERMINAL_OUTCOMES),
            "bounds": {"max_events": self.max_events, "max_body_chars": MAX_BODY_CHARS,
                       "max_scope_chars": MAX_SCOPE_CHARS,
                       "max_results_per_event": MAX_RESULTS_PER_EVENT,
                       "max_attempts_per_event": MAX_ATTEMPTS_PER_EVENT,
                       "max_list": MAX_LIST},
            "owns_transport_ledger": True,
            "owns_activity": False,
            "owns_relationship": False,
            "owns_conversation": False,
            "owns_no_model": True,
            "writes_on_read": False,
            "external_channel": False,
            "delivered_to_user": False,
            "local_only": True,
        }

    # -- the document ----------------------------------------------------------------

    def _empty_doc(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "subject_id": self.subject_id,
                "home_ref": self.home_ref, "revision": 0, "seq": 0,
                "created_at": self._now(), "updated_at": None,
                "events": {}, "operations": {}, "order": []}

    def _read_doc(self) -> Optional[dict[str, Any]]:
        """``None`` when there is no state file yet; a *named* error when it is unreadable."""

        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise LocalContactError("CONTACT_STATE_UNREADABLE", str(exc)[:160]) from exc
        if not raw.strip():
            raise LocalContactError("CONTACT_STATE_EMPTY",
                                    "the state file exists but holds no JSON (half write)")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LocalContactError("CONTACT_STATE_CORRUPT", str(exc)[:160]) from exc
        if not isinstance(data, dict):
            raise LocalContactError("CONTACT_STATE_CORRUPT", "the state file is not an object")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise LocalContactError("CONTACT_STATE_SCHEMA_MISMATCH",
                                    str(data.get("schema_version"))[:80])
        if not isinstance(data.get("events"), dict) or \
                not isinstance(data.get("operations"), dict):
            raise LocalContactError("CONTACT_STATE_CORRUPT", "events/operations are missing")
        if not isinstance(data.get("order"), list):
            raise LocalContactError("CONTACT_STATE_CORRUPT", "order is missing")
        if str(data.get("subject_id") or "") != self.subject_id:
            raise LocalContactError("CONTACT_STATE_SUBJECT_MISMATCH",
                                    "%s != %s" % (data.get("subject_id"), self.subject_id))
        return data

    def _empty_result(self, reason_code: str, detail: str = "",
                      **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "status": "unreadable",
                               "reason_code": reason_code, "detail": detail,
                               "data": None, "writes_performed": 0}
        out.update(extra)
        return out

    def _mutate(self, fn: Callable[[dict[str, Any]], tuple[bool, dict[str, Any]]]
                ) -> dict[str, Any]:
        """Read-modify-write the whole document under the append lock.

        A write never starts on a ledger it cannot read: a corrupt / half-written / foreign
        state is a *named* refusal here too, so a real arrival is not appended on top of a
        document whose earlier events are already lost.
        """

        try:
            with write_lock(self.state_root):
                doc = self._read_doc() or self._empty_doc()
                changed, payload = fn(doc)
                if changed:
                    doc["revision"] = int(doc.get("revision") or 0) + 1
                    doc["updated_at"] = self._now()
                    doc["subject_id"] = self.subject_id
                    doc["home_ref"] = self.home_ref
                    _write_atomically(self.path, doc)
                return payload
        except LocalContactError as exc:
            return {"ok": False, "status": "unreadable", "reason_code": exc.reason_code,
                    "detail": exc.detail, "data": None, "writes_performed": 0,
                    "retryable": True, "reconciled": False}

    # -- submit ----------------------------------------------------------------------

    def submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Append one inbound local transport event.  Bounded, idempotent, identity-bound.

        The payload is validated against a **closed field whitelist**; the kind decides what
        the event may claim (an ordinary message may not carry a scope, an end may not carry
        a scope).  ``operation_key`` is derived from the normalised payload when the caller
        omits it, so an identical arrival is one event and a *different* payload on the same
        key is refused rather than silently overwritten.
        """

        try:
            normalised = self._normalise(payload)
        except LocalContactError as exc:
            return {"ok": False, "status": "rejected", "reason_code": exc.reason_code,
                    "detail": exc.detail, "data": None, "writes_performed": 0,
                    "event_ref": None, "operation_key": payload.get("operation_key")}
        digest = normalised["payload_digest"]
        op_key = (str(payload.get("operation_key") or "").strip()
                  or default_operation_key(digest))
        if not _REF_RE.match(op_key) or len(op_key) > MAX_REF_CHARS:
            return {"ok": False, "status": "rejected",
                    "reason_code": "CONTACT_OPERATION_KEY_INVALID", "detail": op_key[:80],
                    "data": None, "writes_performed": 0, "event_ref": None,
                    "operation_key": op_key}

        def apply(doc: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            recorded = doc["operations"].get(op_key)
            if isinstance(recorded, Mapping):
                event_ref = str(recorded.get("event_ref") or "")
                event = doc["events"].get(event_ref)
                if not isinstance(event, Mapping):
                    return False, {"ok": False, "status": "rejected",
                                   "reason_code": "CONTACT_OPERATION_DANGLING",
                                   "detail": "operation %s names a missing event" % op_key,
                                   "event_ref": event_ref, "operation_key": op_key,
                                   "writes_performed": 0, "data": None}
                if str(recorded.get("payload_digest")) != digest:
                    return False, {"ok": False, "status": "rejected",
                                   "reason_code": "CONTACT_OPERATION_KEY_CONFLICT",
                                   "detail": "key %s is already bound to another payload"
                                             % op_key,
                                   "event_ref": event_ref, "operation_key": op_key,
                                   "existing_payload_digest":
                                       recorded.get("payload_digest"),
                                   "payload_digest": digest,
                                   "writes_performed": 0, "data": None}
                return False, {"ok": True, "status": "already_applied",
                               "reason_code": "", "detail": "",
                               "event_ref": event_ref, "operation_key": op_key,
                               "replayed": True, "created": False, "reconciled": True,
                               "retryable": False, "writes_performed": 0,
                               "effect": event.get("effect"),
                               "pause_requested": False, "activity_started": False,
                               "owners_written": 0,
                               "data": {"event": self._detail(doc, event_ref)}}
            if len(doc["events"]) >= self.max_events:
                return False, {"ok": False, "status": "rejected",
                               "reason_code": "CONTACT_INBOX_CAPACITY_REACHED",
                               "detail": "%d events stored, capacity %d"
                                         % (len(doc["events"]), self.max_events),
                               "event_ref": None, "operation_key": op_key,
                               "writes_performed": 0,
                               "data": {"count": len(doc["events"]),
                                        "max_events": self.max_events}}
            seq = int(doc.get("seq") or 0) + 1
            event_ref = "%s:%s" % (_EVENT_REF_PREFIX,
                                   _sha("%s|%s|%d" % (op_key, digest, seq))[:16])
            record = dict(normalised)
            record.update({
                "event_ref": event_ref,
                "seq": seq,
                "channel": CHANNEL_LOCAL_INBOX,
                "source": SOURCE_LOCAL,
                "subject_id": self.subject_id,
                "home_ref": self.home_ref,
                "operation_key": op_key,
                "processing_state": STATE_PENDING,
                "claim": None,
                "attempts": 0,
                "results": [],
                "result": None,
                "revision": 1,
            })
            doc["events"][event_ref] = record
            doc["order"].append(event_ref)
            doc["seq"] = seq
            self._resolve_binding(doc, event_ref)
            doc["operations"][op_key] = {"kind": "submit", "event_ref": event_ref,
                                         "payload_digest": digest,
                                         "created_at": record["received_at"]}
            return True, {"ok": True, "status": "applied", "reason_code": "", "detail": "",
                          "event_ref": event_ref, "operation_key": op_key,
                          "created": True, "replayed": False, "reconciled": False,
                          "retryable": False, "writes_performed": 1,
                          "effect": record["effect"],
                          "pause_requested": False, "activity_started": False,
                          "owners_written": 0, "processing_state": STATE_PENDING,
                          "data": {"event": self._detail(doc, event_ref)}}

        return self._mutate(apply)

    def _find_invitation_event(self, doc: Mapping[str, Any], invitation_id: str) -> Optional[str]:
        """The newest invitation event in this ledger that carries this ``invitation_id``."""

        found: Optional[str] = None
        for event_ref in (doc.get("order") or []):
            event = (doc.get("events") or {}).get(event_ref)
            if not isinstance(event, Mapping):
                continue
            if event.get("event_kind") != KIND_INVITATION:
                continue
            if str(event.get("invitation_id") or "") == invitation_id:
                found = str(event_ref)
        return found

    def _resolve_binding(self, doc: dict[str, Any], event_ref: str) -> None:
        """Bind an end event to the invitation event it closes, **inside the same write**.

        An end names an ``invitation_id``; the ledger is the only place that can say which
        *event* that id belongs to.  Resolving it here (and again at record time) is what
        makes "this end closed that arrival" a real, restart-surviving fact rather than
        something a later reader has to guess.  An end whose invitation is not in the ledger
        stays ``bound=False`` with the reason recorded -- never silently paired with some
        other open invitation.
        """

        event = doc["events"].get(event_ref)
        if not isinstance(event, dict) or event.get("event_kind") != KIND_END:
            return
        binding = dict(event.get("binding") or {})
        invitation_id = str(binding.get("invitation_id") or event.get("invitation_id") or "")
        target = self._find_invitation_event(doc, invitation_id) if invitation_id else None
        binding["invitation_event_ref"] = target
        binding["resolved_at_submit"] = bool(target)
        binding["reason_code"] = "" if target else "CONTACT_INVITATION_NOT_IN_LEDGER"
        event["binding"] = binding


    def _normalise(self, payload: Any) -> dict[str, Any]:
        """Validate one payload into the stored event body.  Never invents an effect."""

        if not isinstance(payload, Mapping):
            raise LocalContactError("CONTACT_PAYLOAD_NOT_A_MAPPING")
        _refuse_unknown_fields(payload, ALLOWED_SUBMIT_FIELDS, prefix="CONTACT_PAYLOAD")
        raw_kind = str(payload.get("event_kind") or payload.get("kind") or "").strip().upper()
        if not raw_kind:
            raise LocalContactError("CONTACT_EVENT_KIND_REQUIRED")
        kind = _KIND_ALIASES.get(raw_kind)
        if kind is None:
            raise LocalContactError("CONTACT_EVENT_KIND_UNSUPPORTED", raw_kind[:40])

        identity_ignored = False
        for field, mine in (("subject_id", self.subject_id), ("home_ref", self.home_ref)):
            claimed = payload.get(field)
            if claimed is None or str(claimed).strip() == "":
                continue
            if str(claimed).strip() != mine:
                raise LocalContactError(
                    "CONTACT_%s_MISMATCH" % field.upper(),
                    "the payload names %s, this instance is %s"
                    % (str(claimed)[:60], mine[:60]))
            identity_ignored = True

        channel = payload.get("channel")
        if channel is not None and str(channel).strip() != "" and \
                str(channel).strip() != CHANNEL_LOCAL_INBOX:
            raise LocalContactError("CONTACT_CHANNEL_REFUSED",
                                    "%s (only %s)" % (str(channel)[:40], CHANNEL_LOCAL_INBOX))

        forbidden = _FIELDS_BY_KIND[kind]
        used = sorted(field for field in forbidden
                      if payload.get(field) not in (None, "", False))
        if used:
            raise LocalContactError("CONTACT_FIELD_NOT_ALLOWED_FOR_KIND", ",".join(used[:5]))

        body = _body_of(payload)
        supplied: list[str] = []
        occurred = _instant(payload.get("occurred_at"))
        if payload.get("occurred_at") is not None:
            if occurred is None:
                raise LocalContactError("CONTACT_OCCURRED_AT_INVALID",
                                        str(payload.get("occurred_at"))[:40])
            supplied.append("occurred_at")
        supplied_received = _instant(payload.get("received_at"))
        if payload.get("received_at") is not None:
            if supplied_received is None:
                raise LocalContactError("CONTACT_RECEIVED_AT_INVALID",
                                        str(payload.get("received_at"))[:40])
            supplied.append("received_at")
        received = supplied_received or _instant(self._now())
        occurred_iso = _iso(occurred or received)
        received_iso = _iso(received)
        source_ref = _ref(payload.get("source_ref"), "source_ref")

        record: dict[str, Any] = {
            "event_kind": kind,
            "effect": _EFFECT_OF_KIND[kind],
            "body": body,
            "body_chars": len(body),
            "body_stored": bool(body),
            "body_digest": ("sha256:" + _sha(body)[:32]) if body else "",
            "source_ref": source_ref,
            "occurred_at": occurred_iso,
            "received_at": received_iso,
            "observed_at": received_iso,
            "conversation_ref": _ref(payload.get("conversation_ref"), "conversation_ref"),
            "message_ref": _ref(payload.get("message_ref"), "message_ref"),
            "client_identity_ignored": identity_ignored,
            "invitation": None,
            "end": None,
            "binding": None,
        }

        if kind == KIND_INVITATION:
            scope = _text(payload.get("scope"), "scope", max_chars=MAX_SCOPE_CHARS,
                          required=True)
            expires = _instant(payload.get("expires_at"))
            if payload.get("expires_at") is not None and expires is None:
                raise LocalContactError("CONTACT_EXPIRES_AT_INVALID",
                                        str(payload.get("expires_at"))[:40])
            invitation_id = str(payload.get("invitation_id") or "").strip()
            invitation_source = "PROVIDED"
            if not invitation_id:
                invitation_source = "DERIVED_LOCAL"
                invitation_id = "%s:%s" % (
                    _INVITATION_REF_PREFIX,
                    _sha("|".join([self.subject_id, scope, occurred_iso,
                                   body]))[:16])
            if not _REF_RE.match(invitation_id):
                raise LocalContactError("CONTACT_INVITATION_ID_NOT_A_STRUCTURED_REF",
                                        invitation_id[:80])
            duration = payload.get("duration_seconds")
            if duration is not None:
                try:
                    duration = int(duration)
                except (TypeError, ValueError):
                    raise LocalContactError("CONTACT_DURATION_INVALID",
                                            str(duration)[:40]) from None
                if duration < 0 or duration > 24 * 3600:
                    raise LocalContactError("CONTACT_DURATION_INVALID", str(duration))
            material_refs, refused_refs = _material_refs(payload.get("material_refs"))
            if refused_refs:
                raise LocalContactError("CONTACT_MATERIAL_REFS_REFUSED", refused_refs)
            invitation = {
                "event_kind": KIND_INVITATION,
                "invitation_id": invitation_id,
                "source_ref": source_ref or "contact_evt:%s" % _sha(
                    invitation_id)[:16],
                "occurred_at": occurred_iso,
                "expires_at": _iso(expires) if expires else None,
                "scope": scope,
                "subject_id": self.subject_id,
                "home_ref": self.home_ref,
                "requires_engagement": bool(payload.get("requires_engagement", True)),
                "atomic": bool(payload.get("atomic", False)),
                "duration_seconds": duration,
                "evidence_ref": _ref(payload.get("evidence_ref"), "evidence_ref"),
                # v0.4 shared practice: the explicit materials this invitation is about.  A reads
                # each one through the owner before any session exists; a message cannot carry it.
                "material_refs": list(material_refs),
            }
            record["invitation"] = invitation
            record["invitation_id"] = invitation_id
            record["invitation_id_source"] = invitation_source
            record["scope"] = scope
            record["expires_at"] = invitation["expires_at"]
        elif kind == KIND_END:
            invitation_id = _ref(payload.get("invitation_id"), "invitation_id",
                                 required=True)
            explicit = bool(payload.get("explicit_end", False))
            completion_ref = _ref(payload.get("completion_evidence_ref"),
                                  "completion_evidence_ref")
            if not explicit and not completion_ref:
                raise LocalContactError(
                    "CONTACT_END_NEEDS_EXPLICIT_END_OR_COMPLETION_EVIDENCE")
            reason = str(payload.get("reason") or "").strip() or (
                "EXPLICIT_END" if explicit else "FINITE_COMPLETION")
            if len(reason) > MAX_REASON_CHARS:
                raise LocalContactError("CONTACT_REASON_TOO_LONG", str(len(reason)))
            end = {
                "event_kind": KIND_END,
                "invitation_id": invitation_id,
                "occurred_at": occurred_iso,
                "explicit_end": explicit,
                "completion_evidence_ref": completion_ref,
                "reason": reason,
                "evidence_ref": _ref(payload.get("evidence_ref"), "evidence_ref"),
            }
            record["end"] = end
            record["invitation_id"] = invitation_id
            record["end_reason"] = reason
            record["binding"] = {"invitation_id": invitation_id,
                                 "invitation_event_ref": None,
                                 "bound": False,
                                 "resolved_at_submit": False}

        record["payload_digest"] = _intent_digest(record, supplied_times=supplied)
        return record

    # -- reads (pure: no mkdir, no lock, no revision bump, no expiry write) ------------

    def _read_or_empty(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        try:
            return self._read_doc(), None
        except LocalContactError as exc:
            return None, exc.reason_code

    def _summary(self, doc: Optional[dict[str, Any]], event_ref: str,
                 *, now: Optional[datetime] = None) -> dict[str, Any]:
        """One event as a state summary.  **No body text, ever.**"""

        event = (doc or {}).get("events", {}).get(event_ref) or {}
        if not event:
            return {}
        expires = _instant(event.get("expires_at"))
        moment = now or _instant(self._now()) or datetime.now(timezone.utc)
        result = event.get("result") or {}
        return {
            "event_ref": event_ref,
            "seq": event.get("seq"),
            "event_kind": event.get("event_kind"),
            "effect": event.get("effect"),
            "processing_state": event.get("processing_state"),
            "outcome": result.get("outcome"),
            "outcome_reason_code": result.get("reason_code") or "",
            "occurred_at": event.get("occurred_at"),
            "received_at": event.get("received_at"),
            "body_chars": event.get("body_chars") or 0,
            "body_stored": bool(event.get("body_stored")),
            "body_included": False,
            "scope": event.get("scope"),
            "expires_at": event.get("expires_at"),
            "expired": bool(expires is not None and expires < moment),
            "requires_engagement": (event.get("invitation") or {}).get(
                "requires_engagement"),
            "invitation_id": event.get("invitation_id"),
            "invitation_bound": bool((event.get("binding") or {}).get("bound")),
            "defer_condition": result.get("defer_condition"),
            "opportunity_ref": result.get("opportunity_ref"),
            "session_ref": result.get("session_ref"),
            "attempts": event.get("attempts") or 0,
            "claimed_by": (event.get("claim") or {}).get("consumer"),
            "revision": event.get("revision"),
            "pause_requested": False,
            "activity_started": False,
            "external_channel_sent": False,
        }

    def _detail(self, doc: Optional[dict[str, Any]], event_ref: str,
                *, include_body: bool = True) -> dict[str, Any]:
        """One event in full detail.  The body is only here, and only when asked."""

        summary = self._summary(doc, event_ref)
        if not summary:
            return {}
        event = (doc or {}).get("events", {}).get(event_ref) or {}
        out = dict(summary)
        out["operation_key"] = event.get("operation_key")
        out["payload_digest"] = event.get("payload_digest")
        out["source"] = event.get("source")
        out["source_ref"] = event.get("source_ref")
        out["channel"] = event.get("channel")
        out["subject_id"] = event.get("subject_id")
        out["home_ref"] = event.get("home_ref")
        out["client_identity_ignored"] = bool(event.get("client_identity_ignored"))
        out["invitation"] = dict(event.get("invitation") or {}) or None
        out["end"] = dict(event.get("end") or {}) or None
        out["binding"] = dict(event.get("binding") or {}) or None
        out["claim"] = dict(event.get("claim") or {}) or None
        out["result"] = dict(event.get("result") or {}) or None
        out["results"] = [dict(row) for row in (event.get("results") or [])]
        out["body"] = event.get("body") if include_body else None
        out["body_included"] = bool(include_body and event.get("body"))
        out["private_text_included"] = bool(include_body and event.get("body"))
        return out

    def list_events(self, *, states: Optional[Sequence[str]] = None,
                    kinds: Optional[Sequence[str]] = None,
                    limit: Optional[int] = None,
                    include_body: bool = False) -> dict[str, Any]:
        """Bounded listing, newest last.  Summaries carry **no** body text."""

        doc, error = self._read_or_empty()
        if error:
            return self._empty_result(error, "the transport ledger cannot be read as %s "
                                             "state" % SCHEMA_VERSION, data=None)
        wanted = None
        if states:
            wanted = {str(item).strip().upper() for item in states}
            unknown = sorted(wanted - set(ALL_STATES))
            if unknown:
                return {"ok": False, "status": "rejected",
                        "reason_code": "CONTACT_STATE_FILTER_UNKNOWN",
                        "detail": ",".join(unknown), "data": None, "writes_performed": 0}
        kind_wanted = None
        if kinds:
            kind_wanted = {str(item).strip().upper() for item in kinds}
            unknown = sorted(kind_wanted - set(ALL_KINDS))
            if unknown:
                return {"ok": False, "status": "rejected",
                        "reason_code": "CONTACT_KIND_FILTER_UNKNOWN",
                        "detail": ",".join(unknown), "data": None, "writes_performed": 0}
        cap = MAX_LIST if limit is None else max(0, min(int(limit), MAX_LIST))
        rows: list[dict[str, Any]] = []
        if doc is not None:
            moment = _instant(self._now()) or datetime.now(timezone.utc)
            for event_ref in doc.get("order") or []:
                event = (doc.get("events") or {}).get(event_ref)
                if not isinstance(event, Mapping):
                    continue
                if wanted is not None and event.get("processing_state") not in wanted:
                    continue
                if kind_wanted is not None and event.get("event_kind") not in kind_wanted:
                    continue
                row = (self._detail(doc, event_ref, include_body=include_body)
                       if include_body else self._summary(doc, event_ref, now=moment))
                if row:
                    rows.append(row)
        total = len(rows)
        return {"ok": True, "status": "ok", "reason_code": "", "detail": "",
                "writes_performed": 0,
                "data": {"events": rows[len(rows) - cap:] if cap else [],
                         "matched": total,
                         "returned": min(total, cap),
                         "truncated": total > cap,
                         "limit": cap,
                         "body_included": bool(include_body),
                         "ledger_state": "ABSENT" if doc is None else "OK"}}

    def get_event(self, event_ref: str, *, include_body: bool = False) -> dict[str, Any]:
        """One event.  ``include_body=True`` is the only way the text leaves this module."""

        ref = str(event_ref or "").strip()
        if not ref:
            return {"ok": False, "status": "rejected", "reason_code": "CONTACT_EVENT_REF_REQUIRED",
                    "detail": "", "data": None, "writes_performed": 0}
        doc, error = self._read_or_empty()
        if error:
            return self._empty_result(error, "the transport ledger cannot be read", data=None)
        if doc is None or ref not in (doc.get("events") or {}):
            return {"ok": False, "status": "absent", "reason_code": "CONTACT_EVENT_NOT_FOUND",
                    "detail": ref[:80], "data": None, "writes_performed": 0,
                    "ledger_state": "ABSENT" if doc is None else "OK"}
        return {"ok": True, "status": "ok", "reason_code": "", "detail": "",
                "writes_performed": 0,
                "event_ref": ref,
                "processing_state": doc["events"][ref].get("processing_state"),
                "data": {"event": self._detail(doc, ref, include_body=include_body),
                         "ledger_state": "OK"}}

    def pending(self, *, limit: Optional[int] = None) -> dict[str, Any]:
        """The events **nobody has claimed yet** -- what one consumption pass may look at.

        ``DEFERRED`` is deliberately absent: a deferral names its own condition/opportunity
        and must not be re-asked every tick.  ``CLAIMED`` is absent because another consumer
        already said it is handling it (see :meth:`claimed`).
        """

        return self._select((STATE_PENDING,), limit=limit, label="pending")

    def claimed(self, *, limit: Optional[int] = None) -> dict[str, Any]:
        """Claims in flight.  A crash leaves one here; it is visible, not auto-expired."""

        return self._select((STATE_CLAIMED,), limit=limit, label="claimed")

    def deferred_events(self, *, opportunity_ref: Optional[str] = None,
                        condition: Optional[str] = None,
                        limit: Optional[int] = None) -> dict[str, Any]:
        """Deferrals, optionally narrowed to the condition/opportunity that has now occurred.

        This is the *only* path back to a deferred invitation, and it is a read: the caller
        still has to claim and record -- nothing here re-decides anything.
        """

        selected = self._select((STATE_DEFERRED,), limit=MAX_LIST, label="deferred")
        if not selected.get("ok"):
            return selected
        rows = selected["data"]["events"]
        wanted_ref = str(opportunity_ref or "").strip()
        wanted_condition = str(condition or "").strip()
        if wanted_ref:
            rows = [row for row in rows if str(row.get("opportunity_ref") or "") == wanted_ref]
        if wanted_condition:
            rows = [row for row in rows
                    if str(row.get("defer_condition") or "") == wanted_condition]
        cap = MAX_LIST if limit is None else max(0, min(int(limit), MAX_LIST))
        return {"ok": True, "status": "ok", "reason_code": "", "detail": "",
                "writes_performed": 0,
                "data": {"events": rows[:cap] if cap else [], "matched": len(rows),
                         "returned": min(len(rows), cap), "truncated": len(rows) > cap,
                         "filter": {"opportunity_ref": wanted_ref or None,
                                    "condition": wanted_condition or None},
                         "ledger_state": selected["data"]["ledger_state"]}}

    def _select(self, states: Sequence[str], *, limit: Optional[int],
                label: str) -> dict[str, Any]:
        doc, error = self._read_or_empty()
        if error:
            return self._empty_result(error, "the transport ledger cannot be read", data=None)
        cap = MAX_LIST if limit is None else max(0, min(int(limit), MAX_LIST))
        rows: list[dict[str, Any]] = []
        if doc is not None:
            moment = _instant(self._now()) or datetime.now(timezone.utc)
            for event_ref in doc.get("order") or []:
                event = (doc.get("events") or {}).get(event_ref)
                if not isinstance(event, Mapping):
                    continue
                if event.get("processing_state") not in states:
                    continue
                row = self._summary(doc, event_ref, now=moment)
                if row:
                    rows.append(row)
        total = len(rows)
        return {"ok": True, "status": "ok", "reason_code": "", "detail": "",
                "writes_performed": 0,
                "data": {label: rows[:cap] if cap else [], "events": rows[:cap] if cap else [],
                         "matched": total, "returned": min(total, cap),
                         "truncated": total > cap, "limit": cap,
                         "ledger_state": "ABSENT" if doc is None else "OK"}}

    def operation(self, operation_key: str) -> dict[str, Any]:
        """Resolve one operation key against the ledger.  The reconciliation read.

        ``status`` is one of:

        * ``"absent"``  -- nothing with this key ever landed; the same key may be used,
        * ``"applied"`` -- it landed; ``data.event`` is the event and ``data.result`` its
          recorded outcome (``None`` when nobody has recorded one yet),
        * ``"unknown"`` -- it landed and the recorded outcome is ``UNKNOWN``: reconcile with
          the owner by the same key, do not blind-retry,
        * ``"unreadable"`` -- the ledger cannot be read (named reason), so the answer is
          "unknown", never "not applied".
        """

        key = str(operation_key or "").strip()
        if not key:
            return {"ok": False, "status": "rejected",
                    "reason_code": "CONTACT_OPERATION_KEY_REQUIRED", "detail": "",
                    "data": None, "writes_performed": 0}
        doc, error = self._read_or_empty()
        if error:
            return self._empty_result(error, "the transport ledger cannot be read",
                                      data=None, operation_key=key)
        recorded = ((doc or {}).get("operations") or {}).get(key)
        if not isinstance(recorded, Mapping):
            return {"ok": True, "status": "absent", "reason_code": "CONTACT_OPERATION_ABSENT",
                    "detail": "no operation with this key is recorded", "writes_performed": 0,
                    "operation_key": key, "retryable": True, "reconciled": True,
                    "data": {"operation_key": key, "kind": None, "event_ref": None,
                             "event_state": None, "outcome": None, "result": None,
                             "ledger_state": "ABSENT" if doc is None else "OK"}}
        event_ref = str(recorded.get("event_ref") or "")
        event = ((doc or {}).get("events") or {}).get(event_ref)
        if not isinstance(event, Mapping):
            return {"ok": False, "status": "unknown",
                    "reason_code": "CONTACT_OPERATION_DANGLING",
                    "detail": "the key names a missing event", "writes_performed": 0,
                    "operation_key": key, "retryable": False, "reconciled": False,
                    "data": {"operation_key": key, "kind": recorded.get("kind"),
                             "event_ref": event_ref, "event_state": None, "outcome": None,
                             "result": None}}
        result = dict(event.get("result") or {}) or None
        outcome = (result or {}).get("outcome")
        if outcome == OUTCOME_UNKNOWN:
            status, retryable = "unknown", True
        else:
            status, retryable = "applied", False
        return {"ok": True, "status": status, "reason_code": "", "detail": "",
                "writes_performed": 0, "operation_key": key,
                "retryable": retryable, "reconciled": True,
                "data": {"operation_key": key, "kind": recorded.get("kind"),
                         "event_ref": event_ref,
                         "event_state": event.get("processing_state"),
                         "outcome": outcome, "result": result,
                         "event": self._summary(doc, event_ref),
                         "created_at": recorded.get("created_at"),
                         "ledger_state": "OK"}}

    def summary(self) -> dict[str, Any]:
        """Counts only.  **No body text and no per-event rows.**"""

        doc, error = self._read_or_empty()
        if error:
            return self._empty_result(error, "the transport ledger cannot be read",
                                      ledger_state="UNREADABLE")
        counts = {state: 0 for state in ALL_STATES}
        by_kind = {kind: 0 for kind in ALL_KINDS}
        bodies = 0
        oldest = newest = None
        if doc is not None:
            for event in (doc.get("events") or {}).values():
                if not isinstance(event, Mapping):
                    continue
                counts[str(event.get("processing_state"))] = \
                    counts.get(str(event.get("processing_state")), 0) + 1
                by_kind[str(event.get("event_kind"))] = \
                    by_kind.get(str(event.get("event_kind")), 0) + 1
                bodies += 1 if event.get("body_stored") else 0
                seq = event.get("seq")
                oldest = seq if oldest is None else min(oldest, seq)
                newest = seq if newest is None else max(newest, seq)
        total = sum(counts.values())
        return {"ok": True, "status": "ok", "reason_code": "", "detail": "",
                "writes_performed": 0,
                "data": {"ledger_state": "ABSENT" if doc is None else "OK",
                         "event_count": total,
                         "capacity": self.max_events,
                         "remaining": max(0, self.max_events - total),
                         "counts": counts, "by_kind": by_kind,
                         "operation_count": len((doc or {}).get("operations") or {}),
                         "bodies_stored": bodies,
                         "oldest_seq": oldest, "newest_seq": newest,
                         "revision": (doc or {}).get("revision"),
                         "channel": CHANNEL_LOCAL_INBOX, "source": SOURCE_LOCAL,
                         "subject_id": self.subject_id, "home_ref": self.home_ref,
                         "private_text_included": False,
                         "external_channel_sent": False,
                         "delivered_to_user": False}}

    # -- claim / record ---------------------------------------------------------------

    def claim(self, event_ref: str, *, consumer: str, now: Any = None,
              operation_key: Optional[str] = None) -> dict[str, Any]:
        """Say "this one consumption pass owns this event", once.

        The claim is persisted with a token, so two consumers -- in one process or in two --
        cannot both start work on the same arrival, and a crash leaves the claim *visible*
        (:meth:`claimed`) instead of pretending the event is still pending.  Re-claiming a
        ``DEFERRED`` event is a real re-decision at the named condition; re-claiming an
        ``UNKNOWN`` one is the reconciliation pass.  A terminal event is never claimable.
        """

        ref = str(event_ref or "").strip()
        who = str(consumer or "").strip()
        if not ref:
            return {"ok": False, "status": "rejected", "reason_code": "CONTACT_EVENT_REF_REQUIRED",
                    "detail": "", "data": None, "writes_performed": 0}
        if not who or len(who) > 80 or not re.match(r"^[A-Za-z0-9_.:\-]{1,80}$", who):
            return {"ok": False, "status": "rejected", "reason_code": "CONTACT_CONSUMER_INVALID",
                    "detail": who[:80], "data": None, "writes_performed": 0}
        moment = _iso(_instant(now) or _instant(self._now()))

        def apply(doc: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            event = doc["events"].get(ref)
            if not isinstance(event, dict):
                return False, {"ok": False, "status": "absent",
                               "reason_code": "CONTACT_EVENT_NOT_FOUND", "detail": ref[:80],
                               "event_ref": ref, "data": None, "writes_performed": 0}
            state = str(event.get("processing_state"))
            if state == STATE_PROCESSED:
                return False, {"ok": False, "status": "rejected",
                               "reason_code": "CONTACT_EVENT_ALREADY_PROCESSED",
                               "detail": "outcome %s is already recorded"
                                         % (event.get("result") or {}).get("outcome"),
                               "event_ref": ref, "data": None, "writes_performed": 0,
                               "result": dict(event.get("result") or {})}
            current = event.get("claim") or {}
            if state == STATE_CLAIMED and current:
                if str(current.get("consumer")) == who:
                    return False, {"ok": True, "status": "already_applied",
                                   "reason_code": "", "detail": "",
                                   "event_ref": ref, "replayed": True,
                                   "claim_token": current.get("claim_token"),
                                   "claim": dict(current), "writes_performed": 0,
                                   "data": {"claim": dict(current),
                                            "event": self._summary(doc, ref)}}
                return False, {"ok": False, "status": "claimed",
                               "reason_code": "CONTACT_EVENT_CLAIMED",
                               "detail": "claimed by %s at %s"
                                         % (current.get("consumer"),
                                            current.get("claimed_at")),
                               "event_ref": ref, "claim": dict(current),
                               "data": {"claim": dict(current)},
                               "writes_performed": 0}
            attempts = int(event.get("attempts") or 0)
            if attempts >= MAX_ATTEMPTS_PER_EVENT:
                return False, {"ok": False, "status": "rejected",
                               "reason_code": "CONTACT_ATTEMPT_LIMIT_REACHED",
                               "detail": "%d attempts" % attempts,
                               "event_ref": ref, "data": None, "writes_performed": 0}
            token = "%s:%s" % (_CLAIM_PREFIX, uuid.uuid4().hex[:16])
            claim = {"claim_token": token, "consumer": who, "claimed_at": moment,
                     "operation_key": (str(operation_key).strip() if operation_key else None),
                     "from_state": state}
            event["claim"] = claim
            event["processing_state"] = STATE_CLAIMED
            event["attempts"] = attempts + 1
            event["revision"] = int(event.get("revision") or 1) + 1
            return True, {"ok": True, "status": "applied", "reason_code": "", "detail": "",
                          "event_ref": ref, "replayed": False, "claim_token": token,
                          "claim": dict(claim), "writes_performed": 1,
                          "processing_state": STATE_CLAIMED,
                          "data": {"claim": dict(claim),
                                   "event": self._summary(doc, ref)}}

        return self._mutate(apply)

    def record_result(self, event_ref: str, *, outcome: str,
                      claim_token: Optional[str] = None,
                      reason_code: str = "", detail: str = "",
                      session_ref: Optional[str] = None,
                      invitation_ref: Optional[str] = None,
                      decision_ref: Optional[str] = None,
                      defer_condition: Optional[str] = None,
                      opportunity_ref: Optional[str] = None,
                      owner_refs: Optional[Sequence[Any]] = None,
                      operation_key: Optional[str] = None,
                      now: Any = None) -> dict[str, Any]:
        """Persist **one** processing result for one event, once.

        The vocabulary is closed and per-kind: an ordinary message may only be ``OBSERVED``
        (or reported ``UNKNOWN``), so no caller can turn an observation into engagement.  A
        deferral must name a condition or an opportunity, and the ``DEFERRED`` /
        ``UNKNOWN`` outcomes are explicitly *not* terminal.  A terminal outcome never
        changes: recording a different one is refused with ``CONTACT_RESULT_TERMINAL``.
        """

        ref = str(event_ref or "").strip()
        name = str(outcome or "").strip().upper()
        if not ref:
            return {"ok": False, "status": "rejected", "reason_code": "CONTACT_EVENT_REF_REQUIRED",
                    "detail": "", "data": None, "writes_performed": 0}
        if name not in ALL_OUTCOMES:
            return {"ok": False, "status": "rejected",
                    "reason_code": "CONTACT_OUTCOME_UNSUPPORTED", "detail": name[:40],
                    "data": None, "writes_performed": 0}
        try:
            code = _text(reason_code, "reason_code", max_chars=MAX_REASON_CHARS)
            if code and not _REASON_RE.match(code):
                raise LocalContactError("CONTACT_REASON_CODE_NOT_MACHINE_READABLE", code[:40])
            note = _text(detail, "detail", max_chars=MAX_DETAIL_CHARS)
            session = _ref(session_ref, "session_ref")
            invitation = _ref(invitation_ref, "invitation_ref")
            decision = _ref(decision_ref, "decision_ref")
            condition = _text(defer_condition, "defer_condition", max_chars=MAX_SCOPE_CHARS)
            opportunity = _ref(opportunity_ref, "opportunity_ref")
            refs: list[str] = []
            for item in owner_refs or ():
                cleaned = _ref(item, "owner_refs")
                if cleaned and cleaned not in refs:
                    refs.append(cleaned)
            if len(refs) > 8:
                raise LocalContactError("CONTACT_OWNER_REFS_TOO_MANY", str(len(refs)))
        except LocalContactError as exc:
            return {"ok": False, "status": "rejected", "reason_code": exc.reason_code,
                    "detail": exc.detail, "data": None, "writes_performed": 0,
                    "event_ref": ref}
        if name == OUTCOME_DEFERRED and not condition and not opportunity:
            return {"ok": False, "status": "rejected",
                    "reason_code": "CONTACT_DEFER_CONDITION_REQUIRED",
                    "detail": "name defer_condition or opportunity_ref; a deferral without a "
                              "named later condition is just an unhandled event",
                    "data": None, "writes_performed": 0, "event_ref": ref}
        if name == OUTCOME_UNKNOWN and not code:
            return {"ok": False, "status": "rejected",
                    "reason_code": "CONTACT_UNKNOWN_REASON_REQUIRED",
                    "detail": "say why the owner's answer is unknown; a bare UNKNOWN is not a "
                              "fact", "data": None, "writes_performed": 0, "event_ref": ref}
        moment = _iso(_instant(now) or _instant(self._now()))

        def apply(doc: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            event = doc["events"].get(ref)
            if not isinstance(event, dict):
                return False, {"ok": False, "status": "absent",
                               "reason_code": "CONTACT_EVENT_NOT_FOUND", "detail": ref[:80],
                               "event_ref": ref, "data": None, "writes_performed": 0}
            kind = str(event.get("event_kind"))
            if name not in _OUTCOMES_BY_KIND.get(kind, ()):
                reason = ("CONTACT_MESSAGE_CANNOT_AFFECT_ACTIVITY"
                          if kind == KIND_MESSAGE else "CONTACT_OUTCOME_NOT_ALLOWED_FOR_KIND")
                return False, {"ok": False, "status": "rejected", "reason_code": reason,
                               "detail": "%s cannot record %s; allowed: %s"
                                         % (kind, name,
                                            ",".join(_OUTCOMES_BY_KIND.get(kind, ()))),
                               "event_ref": ref, "data": None, "writes_performed": 0}
            previous = event.get("result") or {}
            state = str(event.get("processing_state"))
            if state == STATE_PROCESSED or \
                    str(previous.get("outcome")) in TERMINAL_OUTCOMES:
                same = (str(previous.get("outcome")) == name
                        and str(previous.get("reason_code") or "") == code
                        and str(previous.get("session_ref") or "") == (session or ""))
                if same:
                    return False, {"ok": True, "status": "already_applied",
                                   "reason_code": "", "detail": "",
                                   "event_ref": ref, "replayed": True,
                                   "processing_state": STATE_PROCESSED,
                                   "writes_performed": 0,
                                   "data": {"event": self._summary(doc, ref),
                                            "result": dict(previous)}}
                return False, {"ok": False, "status": "rejected",
                               "reason_code": "CONTACT_RESULT_TERMINAL",
                               "detail": "outcome %s is already recorded; a terminal result "
                                         "never changes" % previous.get("outcome"),
                               "event_ref": ref, "writes_performed": 0,
                               "result": dict(previous),
                               "data": {"event": self._summary(doc, ref),
                                        "result": dict(previous)}}
            claim = event.get("claim") or {}
            if state == STATE_CLAIMED:
                if not claim_token or str(claim.get("claim_token")) != str(claim_token):
                    return False, {"ok": False, "status": "rejected",
                                   "reason_code": "CONTACT_CLAIM_MISMATCH",
                                   "detail": "the result must be recorded by the consumer that "
                                             "holds the claim",
                                   "event_ref": ref, "writes_performed": 0,
                                   "claim": dict(claim), "data": None}
            else:
                return False, {"ok": False, "status": "rejected",
                               "reason_code": "CONTACT_EVENT_NOT_CLAIMED",
                               "detail": "claim the event before recording what happened "
                                         "with it (state=%s)" % state,
                               "event_ref": ref, "writes_performed": 0, "data": None}
            binding = dict(event.get("binding") or {})
            ending_ref = invitation
            if name == OUTCOME_ENDED:
                known = str(binding.get("invitation_event_ref") or "")
                bound_ref = str(ending_ref or known or "")
                if not bound_ref:
                    bound_ref = self._find_invitation_event(
                        doc, str(binding.get("invitation_id") or "")) or ""
                if not bound_ref:
                    return False, {"ok": False, "status": "rejected",
                                   "reason_code": "CONTACT_END_INVITATION_REQUIRED",
                                   "detail": "the invitation this end closes is not in the "
                                             "ledger; pass invitation_ref (the invitation "
                                             "event's event_ref)",
                                   "event_ref": ref, "writes_performed": 0, "data": None}
                if known and ending_ref and str(ending_ref) != known:
                    return False, {"ok": False, "status": "rejected",
                                   "reason_code": "CONTACT_END_INVITATION_MISMATCH",
                                   "detail": "this end was submitted for %s, invitation_ref is %s"
                                             % (known, ending_ref),
                                   "event_ref": ref, "writes_performed": 0, "data": None}
                binding.update({"invitation_event_ref": bound_ref,
                                "bound": True,
                                "binding_source": ("INBOX_RECORD" if known
                                                   else "CONSUMER_REPORTED"),
                                "bound_at": moment,
                                "reason_code": ""})
                event["binding"] = binding
                ending_ref = bound_ref
            history = list(event.get("results") or [])
            if len(history) >= MAX_RESULTS_PER_EVENT:
                return False, {"ok": False, "status": "rejected",
                               "reason_code": "CONTACT_RESULT_HISTORY_FULL",
                               "detail": "%d results" % len(history),
                               "event_ref": ref, "writes_performed": 0, "data": None}
            result = {
                "outcome": name,
                "reason_code": code,
                "detail": note,
                "recorded_at": moment,
                "consumer": str(claim.get("consumer") or ""),
                "session_ref": session,
                "invitation_ref": invitation,
                "invitation_ref": ending_ref,
                "defer_condition": condition or None,
                "opportunity_ref": opportunity,
                "owner_refs": refs,
                "terminal": name in TERMINAL_OUTCOMES,
                "retryable": name == OUTCOME_UNKNOWN,
                "operation_key": (str(operation_key).strip() if operation_key else None),
            }
            history.append(result)
            event["results"] = history
            event["result"] = result
            event["processing_state"] = (STATE_PROCESSED if name in TERMINAL_OUTCOMES
                                         else STATE_DEFERRED if name == OUTCOME_DEFERRED
                                         else STATE_UNKNOWN)
            event["claim"] = None
            event["revision"] = int(event.get("revision") or 1) + 1
            key = str(operation_key or "").strip()
            if key:
                existing = doc["operations"].get(key)
                digest = "sha256:" + _sha("%s|%s|%s" % (ref, name, moment))
                if isinstance(existing, Mapping) and \
                        str(existing.get("event_ref")) != ref:
                    return False, {"ok": False, "status": "rejected",
                                   "reason_code": "CONTACT_OPERATION_KEY_CONFLICT",
                                   "detail": "key %s is already bound to %s"
                                             % (key, existing.get("event_ref")),
                                   "event_ref": ref, "writes_performed": 0, "data": None}
                doc["operations"][key] = {"kind": "result", "event_ref": ref,
                                          "payload_digest": digest,
                                          "created_at": moment,
                                          "outcome": name}
            return True, {"ok": True, "status": "applied", "reason_code": "", "detail": "",
                          "event_ref": ref, "replayed": False, "writes_performed": 1,
                          "processing_state": event["processing_state"],
                          "terminal": name in TERMINAL_OUTCOMES,
                          "retryable": name == OUTCOME_UNKNOWN,
                          "data": {"event": self._summary(doc, ref),
                                   "result": dict(result),
                                   "invitation_binding": (dict(binding) or None)}}

        return self._mutate(apply)


def inbox_from_environment(environment: Mapping[str, str], *,
                           state_root: Optional[Path | str] = None,
                           subject_id: Optional[str] = None,
                           home_ref: Optional[str] = None,
                           clock: Optional[Callable[[], Any]] = None) -> LocalContactInbox:
    """Build the inbox from an instance environment.  A missing identity is a refusal.

    The two names are the ones the Life runtime already uses (``NYA02_SUBJECT`` for the
    Supply-side subject, ``NYA02_LIFE_STATE`` for the state root).  Nothing is defaulted to a
    guessed subject: an instance that cannot say who it is cannot accept a local arrival.
    """

    root: Any = state_root or environment.get("NYA02_LIFE_STATE") or ""
    subject = (subject_id or environment.get("NYA02_SUBJECT")
               or environment.get("CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT") or "")
    home = (home_ref or environment.get("NYA02_LIFE_HOME") or "")
    if not str(root or "").strip():
        raise LocalContactError("CONTACT_STATE_ROOT_REQUIRED")
    if not str(subject or "").strip():
        raise LocalContactError("CONTACT_SUBJECT_REQUIRED")
    if not str(home or "").strip():
        raise LocalContactError("CONTACT_HOME_REQUIRED",
                                "pass the same value the instance gives "
                                "FiniteCoPresence(home=...)")
    return LocalContactInbox(root, subject_id=str(subject), home_ref=str(home), clock=clock)


__all__ = [
    "ALL_KINDS",
    "ALL_OUTCOMES",
    "ALL_STATES",
    "ALLOWED_RESULT_FIELDS",
    "ALLOWED_SUBMIT_FIELDS",
    "CHANNEL_LOCAL_INBOX",
    "EFFECT_END",
    "EFFECT_INVITATION",
    "EFFECT_OBSERVATION_ONLY",
    "KIND_END",
    "KIND_INVITATION",
    "KIND_MESSAGE",
    "LOCK_FILENAME",
    "LocalContactError",
    "LocalContactInbox",
    "MAX_BODY_CHARS",
    "MAX_EVENTS",
    "MAX_LIST",
    "OUTCOME_ACCEPTED",
    "OUTCOME_DECLINED",
    "OUTCOME_DEFERRED",
    "OUTCOME_ENDED",
    "OUTCOME_OBSERVED",
    "OUTCOME_UNKNOWN",
    "SCHEMA_VERSION",
    "SOURCE_LOCAL",
    "STATE_CLAIMED",
    "STATE_DEFERRED",
    "STATE_FILENAME",
    "STATE_PENDING",
    "STATE_PROCESSED",
    "STATE_UNKNOWN",
    "TERMINAL_OUTCOMES",
    "default_operation_key",
    "inbox_from_environment",
    "lock_path",
    "state_path",
    "write_lock",
]
