#!/usr/bin/env python3
"""v0.4 A: one *shared practice* -- a joint session with real material, real contributions and a
real work text -- kept on the existing ``ManagedArtifact`` owner.

What this module is
-------------------
One service, two caller roles, one artifact per session:

* a **shared material reference** that names the owner's own artifact *and the version* that was
  shared, re-read through the owner on every read (the reference is never trusted from a client);
* **user contributions** -- what the person actually typed into this instance (the phone submits
  only this, and only the person's own words);
* **subject responses / contributions** -- what she actually said or added, each one bound to a
  Life-side source (a committed decision, a contact event, or an action record) that is verified
  through a read port before anything is written;
* the **work text** -- the jointly written body, versioned by the owner, kept after the session
  ends and after a reopen.

What this module is *not*
------------------------
* **No second owner, no second chat database.**  A session *is* a ``ManagedArtifact`` row: every
  read and every write goes through ``PrivateProjectService`` (which itself only speaks
  ``LifeArtifactPort``).  There is no new table, no new JSON store, no wallet, no second Memory,
  no relationship store, and no conversation log.  Each contribution is part of the artifact's
  own immutable version history, not a parallel inbox.
* **Not the Activity / session-willingness owner.**  It decides nothing about what she is doing,
  whether a session should start, or whether it continues.  It stores the state of *this
  artifact* (``OPEN``/``PAUSED``/``ENDED``) and nothing else; pausing or resuming an Activity is
  B's business.
* **Not an agreement machine.**  Being addressed is not agreeing.  A session is ``CANDIDATE``
  until a *verified* subject response exists; only the subject side may record
  ``ACCEPTED``/``DECLINED``, and a user contribution can at most carry ``PENDING``.
* **Not an exception-thrower for owner answers.**  Programming mistakes raise
  (``SharedPracticeContractRefused``/``SharedPracticeUnavailable``); what the owner answers, and
  what a caller is not allowed to do, are dict answers with the documented ``status`` values.

The first-line marker (this module's only marker definition)
----------------------------------------------------------
::

    <!-- nyairo-shared-practice:v1 {"schema":...,"state":..., ...} -->

    <the real work text>

Only the first line is ever inspected.  A later line that looks like a marker is text.  The body
is returned verbatim and is what a human or a model reads; no answer of this module ever returns
the marker line itself (see :func:`context_view`): machine metadata must not be mistaken for
content.

Governance (fail closed, all-or-nothing)
----------------------------------------
The material reference is re-read on every read through the owner.  If the shared source is
**not readable any more** -- archived, withdrawn, refused by the owner, or gone -- then every
derived text of that session is withheld by construction (title, work text, contribution and
response texts, notes, source titles/excerpts): ``governance.blocked = true``,
``governance.redacted`` names what was withheld, and the identity/state/version facts stay
readable.  A source that merely **moved on to a newer version** is ``MOVED``: the text stays
readable, ``stale`` is true, and *new writes are refused* until the caller re-points the
reference with :meth:`SharedPracticeService.reconfirm_materials`.  Sharing a material never
publishes the private source itself: an excerpt is only ever included when the caller asks for
one explicitly *and* the source is readable.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from life_artifact_port import operation_key_for  # noqa: E402
from private_project_service import (  # noqa: E402
    ENV_EXCLUDED_NAMES,
    ENV_GRANT_NAMES,
    ENV_SOCKET_NAMES,
    ENV_SUBJECT_NAMES,
    ENV_WORKSPACE_NAMES,
    MAX_CONTENT_CHARS,
    MAX_TITLE_CHARS,
    SOURCE_OWNER,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_CONFLICT,
    STATUS_NOT_FOUND,
    STATUS_OK,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    STATUS_UNKNOWN,
    PrivateProjectService,
    ProjectContractRefused,
    ProjectUnavailable,
)

# ---------------------------------------------------------------------------------------------
# Roles: who is allowed to write which half of a session
# ---------------------------------------------------------------------------------------------

#: The Life side: the subject's own runtime.  May write the subject's half.
CALLER_LIFE = "life"
#: The person's side (the phone submits only this): may write the person's own words.
CALLER_USER = "user"
ACTORS: tuple[str, ...] = (CALLER_LIFE, CALLER_USER)

#: Who a recorded contribution says it came from.  Derived from the caller role, never from a
#: payload: a client can never claim the subject's voice.
CONTRIBUTOR_SUBJECT = "subject"
CONTRIBUTOR_USER = "user"
CONTRIBUTORS: tuple[str, ...] = (CONTRIBUTOR_SUBJECT, CONTRIBUTOR_USER)

#: Contribution kinds.  ``message`` is the person's own text, ``response`` is her final visible
#: reply, ``contribution`` is an actual piece she added to the work.
KIND_MESSAGE = "message"
KIND_RESPONSE = "response"
KIND_CONTRIBUTION = "contribution"
KINDS: tuple[str, ...] = (KIND_MESSAGE, KIND_RESPONSE, KIND_CONTRIBUTION)

#: Commitment bookkeeping.  A contribution never *becomes* an agreement by being written; the
#: person may leave a proposal ``PENDING``, and only the subject side may answer it.
COMMITMENT_NONE = "NONE"
COMMITMENT_PENDING = "PENDING"
COMMITMENT_ACCEPTED = "ACCEPTED"
COMMITMENT_DECLINED = "DECLINED"
COMMITMENTS: tuple[str, ...] = (COMMITMENT_NONE, COMMITMENT_PENDING, COMMITMENT_ACCEPTED,
                               COMMITMENT_DECLINED)
#: What the person's own contribution may carry at most.
USER_COMMITMENTS: tuple[str, ...] = (COMMITMENT_NONE, COMMITMENT_PENDING)

#: Session states (this artifact's lifecycle, nothing else).
STATE_OPEN = "OPEN"
STATE_PAUSED = "PAUSED"
STATE_ENDED = "ENDED"
STATES: tuple[str, ...] = (STATE_OPEN, STATE_PAUSED, STATE_ENDED)

#: How joint a session currently is.  ``CANDIDATE`` is a draft/candidate, never "already jointly
#: accepted": it means no *verified* subject response exists yet.
JOINT_CANDIDATE = "CANDIDATE"
JOINT_JOINT = "JOINT"
JOINT_ENDED = "ENDED"
JOINT_STATES: tuple[str, ...] = (JOINT_CANDIDATE, JOINT_JOINT, JOINT_ENDED)

#: What a recorded source reference points at, and which read method verifies it.
BASIS_DECISION = "decision"
BASIS_EVENT = "event"
BASIS_ACTION = "action"
BASIS_ADOPTION = "adoption"
BASIS_KINDS: tuple[str, ...] = (BASIS_DECISION, BASIS_EVENT, BASIS_ACTION, BASIS_ADOPTION)
#: ``basis`` key -> (kind, verifier method, identity keys accepted on the owner's answer)
BASIS_SPEC: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "decision_ref": (BASIS_DECISION, "get_decision", ("decision_id",)),
    "event_ref": (BASIS_EVENT, "get_event", ("event_ref", "event_id")),
    "action_ref": (BASIS_ACTION, "get_action", ("action_id",)),
    "adoption_ref": (BASIS_ADOPTION, "get_adoption_by_id", ("adoption_id",)),
}

#: Material states, re-read from the owner on every read.
MATERIAL_READABLE = "READABLE"
MATERIAL_MOVED = "MOVED"
MATERIAL_ARCHIVED = "ARCHIVED"
MATERIAL_GOVERNED = "GOVERNED"
MATERIAL_NOT_FOUND = "NOT_FOUND"
MATERIAL_UNAVAILABLE = "UNAVAILABLE"
MATERIAL_STATES: tuple[str, ...] = (MATERIAL_READABLE, MATERIAL_MOVED, MATERIAL_ARCHIVED,
                                    MATERIAL_GOVERNED, MATERIAL_NOT_FOUND, MATERIAL_UNAVAILABLE)
#: These states withhold every derived text of the session (see the module docstring).
BLOCKING_MATERIAL_STATES: tuple[str, ...] = (MATERIAL_ARCHIVED, MATERIAL_GOVERNED,
                                             MATERIAL_NOT_FOUND, MATERIAL_UNAVAILABLE)
#: This state keeps the text readable but refuses new writes until the reference is re-pointed.
STALE_MATERIAL_STATES: tuple[str, ...] = (MATERIAL_MOVED,)

# -- reason codes (stable; B/C branch on these, not on prose) ---------------------------------
REASON_NOT_A_SESSION = "SHARED_PRACTICE_NOT_A_SESSION"
REASON_MARKER_INVALID = "SHARED_PRACTICE_MARKER_INVALID"
REASON_ACTOR_NOT_ALLOWED = "SHARED_PRACTICE_ACTOR_NOT_ALLOWED"
REASON_STATE_NOT_ALLOWED = "SHARED_PRACTICE_STATE_NOT_ALLOWED"
REASON_ALREADY_ENDED = "SHARED_PRACTICE_ALREADY_ENDED"
REASON_NOT_ENDED = "SHARED_PRACTICE_NOT_ENDED"
REASON_NOT_PAUSED = "SHARED_PRACTICE_NOT_PAUSED"
REASON_MATERIAL_REQUIRED = "SHARED_PRACTICE_MATERIAL_REQUIRED"
REASON_MATERIAL_UNREADABLE = "SHARED_PRACTICE_MATERIAL_UNREADABLE"
REASON_MATERIAL_UNAVAILABLE = "SHARED_PRACTICE_MATERIAL_UNAVAILABLE"
REASON_MATERIAL_STALE = "SHARED_PRACTICE_MATERIAL_STALE"
REASON_SOURCE_REQUIRED = "SHARED_PRACTICE_SOURCE_REQUIRED"
REASON_SOURCE_NOT_ALLOWED = "SHARED_PRACTICE_SOURCE_NOT_ALLOWED_FOR_CALLER"
REASON_SOURCE_VERIFIER_UNBOUND = "SHARED_PRACTICE_SOURCE_VERIFIER_UNBOUND"
REASON_SOURCE_NOT_FOUND = "SHARED_PRACTICE_SOURCE_NOT_FOUND"
REASON_SOURCE_UNREADABLE = "SHARED_PRACTICE_SOURCE_UNREADABLE"
REASON_SOURCE_MISMATCH = "SHARED_PRACTICE_SOURCE_MISMATCH"
REASON_COMMITMENT_NOT_USERS = "SHARED_PRACTICE_AGREEMENT_NOT_USERS"
REASON_TEXT_REQUIRED = "SHARED_PRACTICE_TEXT_REQUIRED"
REASON_TOO_LARGE = "SHARED_PRACTICE_CONTENT_TOO_LARGE"
REASON_TOO_MANY = "SHARED_PRACTICE_TOO_MANY_ENTRIES"
REASON_GOVERNED = "SHARED_PRACTICE_SOURCE_GOVERNED"
REASON_PORT_ERROR = "SHARED_PRACTICE_PORT_ERROR"
REASON_OWNER_UNAVAILABLE = "SHARED_PRACTICE_OWNER_UNAVAILABLE"
REASON_OPERATION_NEVER_LANDED = "OPERATION_NEVER_LANDED"
REASON_OPERATION_KEY_REUSED = "SHARED_PRACTICE_OPERATION_KEY_REUSED"
REASON_OPERATION_NOT_FOUND = "SHARED_PRACTICE_OPERATION_NOT_FOUND"
REASON_OPERATION_UNKNOWN = "SHARED_PRACTICE_OPERATION_UNKNOWN"
REASON_OPERATION_UNAVAILABLE = "SHARED_PRACTICE_OPERATION_UNAVAILABLE"
REASON_OPERATION_UNSUPPORTED = "SHARED_PRACTICE_OPERATION_UNSUPPORTED"
REASON_RESOLUTION_INCONCLUSIVE = "RESOLUTION_INCONCLUSIVE"

# -- what one owner operation key resolves to (read from the owner's own receipts) -------------

#: The owner's record says this key produced its effect.
OPERATION_APPLIED = "APPLIED"
#: The owner positively knows this key never produced anything.
OPERATION_NOT_LANDED = "NOT_LANDED"
#: The owner answered, but not with a usable fact (or refused to say).
OPERATION_UNKNOWN = "UNKNOWN"
#: This owner cannot be asked (no receipt/operation surface), or the read itself failed.
OPERATION_UNAVAILABLE = "UNAVAILABLE"
OPERATION_EFFECTS: tuple[str, ...] = (OPERATION_APPLIED, OPERATION_NOT_LANDED,
                                      OPERATION_UNKNOWN, OPERATION_UNAVAILABLE)

#: The owner surfaces an operation key may be read through, in probe order (never a second ledger).
OPERATION_SURFACES: tuple[str, ...] = (
    "projects.operation",
    "projects.resolve_operation",
    "port.resolve_operation",
    "port.read_effect_receipt",
    "port.lookup_operation",
)

# -- bounds -----------------------------------------------------------------------------------
MAX_OPENING_CHARS = 1200
MAX_WORK_CHARS = 12000
MAX_CONTRIBUTION_CHARS = 1200
MAX_RESPONSE_CHARS = 2000
MAX_REASON_CHARS = 400
MAX_MATERIALS = 6
MAX_CONTRIBUTIONS = 24
MAX_RESPONSES = 12
MAX_LIST = 50
MAX_SCAN = 200
MAX_REF_CHARS = 200
MAX_SOURCE_STATUS_CHARS = 64
DEFAULT_ENTRY_LIMIT = 12
DEFAULT_EXCERPT_CHARS = 240
DEFAULT_LIST = 12
MAX_TOTAL_CONTENT_CHARS = min(MAX_CONTENT_CHARS - 1000, 19000)

#: Marker name (one place in the tree defines it).
PRACTICE_MARKER = "nyairo-shared-practice:v1"
MARKER_OPEN = "<!-- "
MARKER_CLOSE = " -->"
MARKER_VERSION = "v1"
SCHEMA = "nyairo.shared-practice.v1"

#: The port surface a projects service must offer (a programming mistake otherwise).
REQUIRED_PROJECT_METHODS: tuple[str, ...] = (
    "list_projects", "read_project", "create_project", "commit_project", "close_project",
)


class SharedPracticeError(RuntimeError):
    """Base class for this module's own refusals (wiring/programming mistakes)."""


class SharedPracticeContractRefused(SharedPracticeError):
    """The caller asked for something this service does not accept at all."""


class SharedPracticeUnavailable(SharedPracticeError):
    """No usable projects service was supplied (the owner was never asked)."""


def shared_practice_operation_key(*parts: Any) -> str:
    """A deterministic operation key for one intended effect.

    Derived from *what is intended*, never from the attempt, so resolving an UNKNOWN and retrying
    with the same key cannot mint a second version of one contribution.
    """
    cleaned = [str(part) for part in parts if str(part) != ""]
    if not cleaned:
        raise SharedPracticeContractRefused(
            "shared_practice_operation_key requires at least one part")
    digest = operation_key_for("shared_practice", *cleaned)
    return "shared-practice:%s" % digest.split(":", 1)[-1]


# ---------------------------------------------------------------------------------------------
# Pure helpers: the marker
# ---------------------------------------------------------------------------------------------


def _json_line(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _marker_line(payload: Mapping[str, Any]) -> str:
    line = MARKER_OPEN + PRACTICE_MARKER + " " + _json_line(payload) + MARKER_CLOSE
    if "\n" in line or "\r" in line:  # json.dumps escapes newlines; this is only an invariant
        raise SharedPracticeContractRefused("marker line must be one physical line")
    return line


def _split_marker(content: Any) -> tuple[Optional[str], str]:
    """Split content into ``(JSON text | None, body)``.  **Only the first line counts.**"""
    if not isinstance(content, str):
        return None, ""
    first, separator, rest = content.partition("\n")
    line = first.strip()
    if not (line.startswith(MARKER_OPEN) and line.endswith(MARKER_CLOSE)):
        return None, content
    inner = line[len(MARKER_OPEN):-len(MARKER_CLOSE)]
    name, space, blob = inner.partition(" ")
    if not space or name.strip() != PRACTICE_MARKER or not blob.strip():
        return None, content
    body = rest if separator else ""
    # One blank line separates the marker from the body; only one is stripped.
    if body.startswith("\n"):
        body = body[1:]
    return blob.strip(), body


def _int_out(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _bounded_str(value: Any, *, limit: int = 400) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _basis_out(value: Any) -> dict[str, Any]:
    """A recorded source reference, bounded to identity/status facts only (never owner text)."""
    if not isinstance(value, Mapping):
        return {}
    block: dict[str, Any] = {
        "kind": _bounded_str(value.get("kind"), limit=32),
        "ref": _bounded_str(value.get("ref"), limit=MAX_REF_CHARS),
        "verified": value.get("verified") is True,
        "verified_id": _bounded_str(value.get("verified_id"), limit=MAX_REF_CHARS),
        "owner_status": _bounded_str(value.get("owner_status"), limit=MAX_SOURCE_STATUS_CHARS),
        "verified_at": _bounded_str(value.get("verified_at"), limit=64),
    }
    if block["kind"] not in BASIS_KINDS:
        block["kind"] = ""
    return block


def _entry_out(value: Any, *, seq: int) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    contributor = _bounded_str(value.get("contributor"), limit=16)
    kind = _bounded_str(value.get("kind"), limit=16)
    if contributor not in CONTRIBUTORS or kind not in KINDS:
        return None
    commitment = _bounded_str(value.get("commitment_state"), limit=16)
    if commitment not in COMMITMENTS:
        commitment = COMMITMENT_NONE
    return {
        "seq": _int_out(value.get("seq")) or seq,
        "contributor": contributor,
        "kind": kind,
        "text": value.get("text") if isinstance(value.get("text"), str) else "",
        "commitment_state": commitment,
        "basis": _basis_out(value.get("basis")),
        "recorded_at": _bounded_str(value.get("recorded_at"), limit=64),
        "against_version_id": _bounded_str(value.get("against_version_id"),
                                           limit=MAX_REF_CHARS),
        #: The owner operation key this entry was written with: a replay of the same key finds it
        #: here instead of writing a second identical entry (older documents simply answer "").
        "operation_key": _bounded_str(value.get("operation_key"), limit=128),
    }


def _materials_out(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(value, (list, tuple)):
        return out
    for item in value:
        if not isinstance(item, Mapping):
            continue
        artifact_id = _bounded_str(item.get("artifact_id"), limit=MAX_REF_CHARS)
        if not artifact_id:
            continue
        out.append({
            "artifact_id": artifact_id,
            "version_id": _bounded_str(item.get("version_id"), limit=MAX_REF_CHARS) or "",
            "revision": _int_out(item.get("revision")),
            "recorded_at": _bounded_str(item.get("recorded_at"), limit=64),
        })
        if len(out) >= MAX_MATERIALS:
            break
    return out


def parse_shared_practice_content(content: Any) -> dict[str, Any]:
    """Read one artifact's content: the **first line** marker plus the body verbatim.

    Never raises and never guesses: a document whose first line is not this marker has
    ``marker_present=False``, and a broken marker is named in ``parse_error``.  Malformed stored
    rows are skipped and counted in ``skipped_entries`` instead of being invented.
    """
    blob, body = _split_marker(content)
    parsed: dict[str, Any] = {
        "marker_present": False,
        "marker_version": "",
        "parse_error": "",
        "schema": "",
        "title": "",
        "state": "",
        "practice_revision": 0,
        "created_at": "",
        "updated_at": "",
        "materials": [],
        "contributions": [],
        "responses": [],
        "work": {},
        "paused": None,
        "ended": None,
        "reopen_count": 0,
        "last_operation_key": "",
        "last_operation_intent": "",
        "open_operation": None,
        "body": body if isinstance(body, str) else "",
        "skipped_entries": 0,
        "unknown_keys": [],
    }
    if blob is None:
        return parsed
    try:
        payload = json.loads(blob)
    except ValueError:
        parsed["marker_present"] = True
        parsed["marker_version"] = MARKER_VERSION
        parsed["parse_error"] = "MARKER_JSON_INVALID"
        return parsed
    if not isinstance(payload, Mapping):
        parsed["marker_present"] = True
        parsed["marker_version"] = MARKER_VERSION
        parsed["parse_error"] = "MARKER_NOT_AN_OBJECT"
        return parsed
    parsed["marker_present"] = True
    parsed["marker_version"] = MARKER_VERSION
    schema = _bounded_str(payload.get("schema"), limit=64)
    if schema != SCHEMA:
        parsed["parse_error"] = "MARKER_SCHEMA_UNKNOWN:%s" % (schema or "missing")
        return parsed
    parsed["schema"] = schema
    parsed["title"] = _bounded_str(payload.get("title"), limit=MAX_TITLE_CHARS)
    state = _bounded_str(payload.get("state"), limit=16)
    parsed["state"] = state if state in STATES else ""
    parsed["practice_revision"] = _int_out(payload.get("practice_revision")) or 0
    parsed["created_at"] = _bounded_str(payload.get("created_at"), limit=64)
    parsed["updated_at"] = _bounded_str(payload.get("updated_at"), limit=64)
    parsed["materials"] = _materials_out(payload.get("materials"))
    for index, row in enumerate(payload.get("contributions") or []):
        entry = _entry_out(row, seq=index + 1)
        if entry is None or entry["kind"] == KIND_RESPONSE:
            parsed["skipped_entries"] += 1
            continue
        parsed["contributions"].append(entry)
    for index, row in enumerate(payload.get("responses") or []):
        entry = _entry_out(row, seq=index + 1)
        if entry is None or entry["kind"] != KIND_RESPONSE:
            parsed["skipped_entries"] += 1
            continue
        parsed["responses"].append(entry)
    work = payload.get("work")
    if isinstance(work, Mapping):
        parsed["work"] = {
            "chars": _int_out(work.get("chars")),
            "updated_at": _bounded_str(work.get("updated_at"), limit=64),
            "updated_by": _bounded_str(work.get("updated_by"), limit=16),
            "basis": _basis_out(work.get("basis")),
        }
    for name in ("paused", "ended"):
        block = payload.get(name)
        if isinstance(block, Mapping):
            parsed[name] = {
                "at": _bounded_str(block.get("at"), limit=64),
                "by": _bounded_str(block.get("by"), limit=16),
                "text": _bounded_str(block.get("text"), limit=MAX_REASON_CHARS),
                "basis": _basis_out(block.get("basis")),
            }
    parsed["reopen_count"] = _int_out(payload.get("reopen_count")) or 0
    parsed["last_operation_key"] = _bounded_str(payload.get("last_operation_key"), limit=128)
    parsed["last_operation_intent"] = _bounded_str(payload.get("last_operation_intent"),
                                                   limit=64)
    opened = payload.get("open_operation")
    if isinstance(opened, Mapping) and opened.get("key"):
        parsed["open_operation"] = {
            "key": _bounded_str(opened.get("key"), limit=128),
            "intent": _bounded_str(opened.get("intent"), limit=64),
        }
    parsed["unknown_keys"] = sorted(set(str(key) for key in payload)
                                    - {"schema", "title", "state", "practice_revision",
                                       "created_at", "updated_at", "materials",
                                       "contributions", "responses", "work", "paused", "ended",
                                       "reopen_count", "last_operation_key",
                                       "last_operation_intent", "open_operation"})
    return parsed


def is_shared_practice_content(content: Any) -> bool:
    """Is this artifact a shared-practice session (valid first-line marker, known state)?"""
    parsed = parse_shared_practice_content(content)
    return bool(parsed["marker_present"]) and not parsed["parse_error"] and bool(parsed["state"])


def shared_practice_state(content: Any) -> str:
    """``OPEN`` / ``PAUSED`` / ``ENDED`` / ``""`` -- from the first-line marker only."""
    parsed = parse_shared_practice_content(content)
    return parsed["state"] if not parsed["parse_error"] else ""


def build_shared_practice_content(metadata: Mapping[str, Any], body: str = "") -> str:
    """Compose one session document: marker line, one blank line, then the body verbatim."""
    if not isinstance(metadata, Mapping):
        raise SharedPracticeContractRefused("metadata must be a mapping")
    state = _bounded_str(metadata.get("state"), limit=16)
    if state not in STATES:
        raise SharedPracticeContractRefused("state must be one of %s" % (", ".join(STATES)))
    text = body if isinstance(body, str) else ""
    if len(text) > MAX_WORK_CHARS:
        raise SharedPracticeContractRefused("work text exceeds %d characters" % MAX_WORK_CHARS)
    payload = {key: value for key, value in dict(metadata).items() if value is not None}
    line = _marker_line(payload)
    content = line + "\n\n" + text if text else line + "\n"
    if len(content) > MAX_TOTAL_CONTENT_CHARS:
        raise SharedPracticeContractRefused(
            "this session would exceed the owner's content bound (%d characters)"
            % MAX_TOTAL_CONTENT_CHARS)
    return content


# ---------------------------------------------------------------------------------------------
# Pure helpers: input validation
# ---------------------------------------------------------------------------------------------


def _text(value: Any, name: str, *, limit: int, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise SharedPracticeContractRefused("%s must be a string" % name)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise SharedPracticeContractRefused("%s must not contain NUL" % name)
    if not allow_empty and not text.strip():
        raise SharedPracticeContractRefused("%s must not be blank" % name)
    if len(text) > int(limit):
        raise SharedPracticeContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return text


def _one_line(value: Any, name: str, *, limit: int, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise SharedPracticeContractRefused("%s must be a string" % name)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise SharedPracticeContractRefused("%s must not contain NUL" % name)
    line = " ".join(part for part in text.splitlines() if part.strip()).strip()
    if not allow_empty and not line:
        raise SharedPracticeContractRefused("%s must not be blank" % name)
    if len(line) > int(limit):
        raise SharedPracticeContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return line


def _artifact_id(value: Any, name: str = "session_id") -> str:
    """The owner's own artifact id, canonicalised (bare hex from a display ref is accepted)."""
    if not isinstance(value, str) or not value.strip():
        raise SharedPracticeContractRefused("%s is required" % name)
    text = value.strip()
    if len(text) > MAX_REF_CHARS:
        raise SharedPracticeContractRefused("%s is too long" % name)
    if not text.startswith("artifact:") and len(text) >= 8 \
            and all(char in "0123456789abcdefABCDEF" for char in text):
        return "artifact:" + text
    return text


def _head(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SharedPracticeContractRefused(
            "expected_head is required: pass the head_version_id you read before writing")
    text = value.strip()
    if len(text) > MAX_REF_CHARS:
        raise SharedPracticeContractRefused("expected_head is too long")
    return text


def _operation_effect_of(value: Any) -> dict[str, Any]:
    """Read one owner answer to an operation key into ``{effect, artifact_id, ...}``.

    Accepts the three shapes an owner actually answers with -- a dict answer, a receipt/operation
    row, or an ``ArtifactOutcome``-like object -- and never invents an effect it was not told.  The
    one judgement made here is named: an owner answer that *claims* ``APPLIED`` while naming no
    artifact/version names no landed effect, so there is nothing to reuse (``NOT_LANDED``); A will
    not guess which session a key meant.
    """
    if value is None:
        # "the owner has no record of this key" is a positive fact, not an unknown.
        return {"effect": OPERATION_NOT_LANDED, "artifact_id": "", "version_id": "",
                "version_number": None, "owner_reason": "NO_OPERATION_RECORD"}
    effect = ""
    reason = ""
    artifact_id = ""
    version_id = ""
    version_number: Optional[int] = None
    if isinstance(value, Mapping):
        effect = str(value.get("effect_state") or "").strip().upper()
        artifact_id = str(value.get("artifact_id") or "")
        version_id = str(value.get("version_id") or "")
        version_number = _int_out(value.get("version_number"))
        reason = str(value.get("reason") or value.get("detail") or "")[:200]
        if not effect:
            status = str(value.get("status") or "").strip().lower()
            if status in ("ok", "applied"):
                effect = "APPLIED"
            elif status in ("no_change", "not_found", "not_landed"):
                effect = "NOT_LANDED"
            elif status in ("unknown", "conflict", "refused"):
                effect = "UNKNOWN"
            elif artifact_id or version_id:
                # A receipt / operation row names the effect it produced.
                effect = "APPLIED"
    else:
        effect = str(getattr(value, "effect_state", "") or "").strip().upper()
        artifact_id = str(getattr(value, "artifact_id", "") or "")
        version_id = str(getattr(value, "version_id", "") or "")
        version_number = _int_out(getattr(value, "version_number", None))
        reason = str(getattr(value, "reason", "") or getattr(value, "detail", "") or "")[:200]
    if effect == "APPLIED" and not artifact_id and not version_id:
        return {"effect": OPERATION_NOT_LANDED, "artifact_id": "", "version_id": "",
                "version_number": None, "owner_reason": "APPLIED_WITHOUT_IDENTITY"}
    if effect == "APPLIED":
        return {"effect": OPERATION_APPLIED, "artifact_id": artifact_id[:MAX_REF_CHARS],
                "version_id": version_id[:MAX_REF_CHARS], "version_number": version_number,
                "owner_reason": reason}
    if effect in ("NO_CHANGE", "NOT_FOUND", "NOT_LANDED"):
        return {"effect": OPERATION_NOT_LANDED, "artifact_id": artifact_id[:MAX_REF_CHARS],
                "version_id": version_id[:MAX_REF_CHARS], "version_number": version_number,
                "owner_reason": reason}
    return {"effect": OPERATION_UNKNOWN, "artifact_id": artifact_id[:MAX_REF_CHARS],
            "version_id": version_id[:MAX_REF_CHARS], "version_number": version_number,
            "owner_reason": reason or effect or "NO_OPERATION_FACT"}


def _operation_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SharedPracticeContractRefused("operation_key must be a non-empty string")
    text = value.strip()
    if len(text) > 128:
        raise SharedPracticeContractRefused("operation_key is too long")
    return text


def _intent_digest(action: str, *parts: Any) -> str:
    """A short, non-reversible fingerprint of *what one write intends*.

    Recorded next to the operation key in the version being written, so a replay can be told apart
    from a key that was reused for a different payload without ever storing the payload twice.  It
    is a digest only: no text, no private source, nothing that could be read back.
    """
    blob = "\x1f".join([str(action)] + [str(part) for part in parts])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _material_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: Sequence[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise SharedPracticeContractRefused("material_refs must be a list of artifact ids")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise SharedPracticeContractRefused("every material ref must be a string")
        if not item.strip():
            continue
        target = _artifact_id(item, "material_refs")
        if target not in out:
            out.append(target)
    if len(out) > MAX_MATERIALS:
        raise SharedPracticeContractRefused("at most %d materials per session" % MAX_MATERIALS)
    return out


def _commitment(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return COMMITMENT_NONE
    if text not in COMMITMENTS:
        raise SharedPracticeContractRefused("commitment_state must be one of %s"
                                            % (", ".join(COMMITMENTS)))
    return text


def _limit(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return min(DEFAULT_ENTRY_LIMIT, maximum)
    return max(1, min(int(value), int(maximum)))


def _now_from(clock: Optional[Callable[[], Any]]) -> str:
    """One ISO instant from the caller's clock (deterministic in tests)."""
    if clock is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    raw = clock()
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc).isoformat(timespec="seconds")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(float(raw), timezone.utc).isoformat(timespec="seconds")
    text = str(raw or "").strip()
    if not text:
        raise SharedPracticeContractRefused("clock must return an instant")
    return text[:64]


# ---------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------


class SharedPracticeService:
    """Shared material, contributions, a work text and a session state -- on one artifact.

    One instance per caller role: a phone builds ``actor=CALLER_USER`` and therefore can never
    write the subject's half; a Life runtime builds ``actor=CALLER_LIFE`` and must bind every
    subject-side write to a source reference its read port can verify.
    """

    def __init__(
        self,
        projects: Any,
        *,
        actor: str,
        source_verifier: Any = None,
        subject_id: Optional[str] = None,
        clock: Optional[Callable[[], Any]] = None,
        max_list: int = MAX_LIST,
        max_scan: int = MAX_SCAN,
    ) -> None:
        if projects is None:
            raise SharedPracticeUnavailable("PROJECT_SERVICE_REQUIRED")
        missing = [name for name in REQUIRED_PROJECT_METHODS
                   if not callable(getattr(projects, name, None))]
        if missing:
            raise SharedPracticeUnavailable("PROJECT_METHODS_MISSING:%s" % ",".join(missing))
        actor_text = str(actor or "").strip().lower()
        if actor_text not in ACTORS:
            raise SharedPracticeContractRefused("actor must be one of %s" % (", ".join(ACTORS)))
        resolved_subject = subject_id or getattr(projects, "subject_id", None)
        self.projects = projects
        self.actor = actor_text
        self.subject_id = str(resolved_subject or "")
        self.workspace_id = str(getattr(projects, "workspace_id", "") or "")
        self.grant_id = str(getattr(projects, "grant_id", "") or "")
        #: A read port with ``get_decision`` / ``get_event`` / ``get_action`` /
        #: ``get_adoption_by_id``.  ``None`` means "this caller may not bind sources", so every
        #: subject-side write is refused instead of being written without a source.
        self.source_verifier = source_verifier
        self._clock = clock
        self.max_list = max(1, int(max_list))
        self.max_scan = max(1, int(max_scan))
        #: A per-instance counter for the *derived* open key: no caller key means "no idempotency
        #: contract", so two intentional identical opens stay two sessions (see `open_practice`).
        self._open_serial = 0

    # -- introspection -------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """What this service is wired to.  No owner call, no private text."""
        return {
            "kind": "shared_practice_service",
            "source": SOURCE_OWNER,
            "actor": self.actor,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "grant_present": bool(self.grant_id),
            "marker": PRACTICE_MARKER,
            "schema": SCHEMA,
            "states": list(STATES),
            "joint_states": list(JOINT_STATES),
            "kinds": list(KINDS),
            "commitments": list(COMMITMENTS),
            "basis_kinds": list(BASIS_KINDS),
            #: Which owner surface answers one operation key ("" = this owner cannot be asked).
            "operation_lookup": self._operation_surface()[0],
            "operation_effects": list(OPERATION_EFFECTS),
            "source_verifier_bound": self.source_verifier is not None,
            "material_states": list(MATERIAL_STATES),
            "limits": {
                "materials": MAX_MATERIALS,
                "contributions": MAX_CONTRIBUTIONS,
                "responses": MAX_RESPONSES,
                "work_chars": MAX_WORK_CHARS,
                "contribution_chars": MAX_CONTRIBUTION_CHARS,
                "response_chars": MAX_RESPONSE_CHARS,
                "list": self.max_list,
                "scan": self.max_scan,
                "content_chars": MAX_TOTAL_CONTENT_CHARS,
            },
            "owns_activity": False,
            "owns_relationship": False,
            "owns_conversation": False,
            "owns_model": False,
            "writes_on_read": False,
        }

    def now(self) -> str:
        return _now_from(self._clock)

    # -- reads ---------------------------------------------------------------------------

    def list_practices(self, *, include_ended: bool = True,
                       limit: Optional[int] = None) -> dict[str, Any]:
        """Every shared-practice session this subject holds, newest revision first.  Pure read.

        A row carries identity, state, counts and the governance block -- never a body and never
        a contribution text.
        """
        cap = self.max_list if limit is None else max(1, min(int(limit), self.max_list))
        try:
            listing = self.projects.list_projects()
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc)
        if not isinstance(listing, Mapping) or not listing.get("ok"):
            return self._unavailable(
                REASON_OWNER_UNAVAILABLE,
                RuntimeError(str((listing or {}).get("reason_code") or "LIST_REFUSED")))
        rows: list[dict[str, Any]] = []
        skipped = {"other": 0, "ended": 0, "unreadable": 0}
        scanned = 0
        for project in list(listing.get("projects") or [])[:self.max_scan]:
            artifact_id = str((project or {}).get("artifact_id") or "")
            if not artifact_id:
                continue
            scanned += 1
            loaded = self._load(artifact_id, include_history=False)
            if not loaded.get("ok"):
                reason = str(loaded["answer"].get("reason_code") or "")
                skipped["other" if reason in (REASON_NOT_A_SESSION, REASON_MARKER_INVALID)
                        else "unreadable"] += 1
                continue
            parsed = loaded["parsed"]
            if parsed["state"] == STATE_ENDED and not include_ended:
                skipped["ended"] += 1
                continue
            states = self._material_states(parsed)
            row = self._summary(loaded, states)
            if not row:
                skipped["other"] += 1
                continue
            rows.append(row)

        def order(row: Mapping[str, Any]) -> tuple:
            updated = row.get("updated_at")
            return (-(float(updated) if isinstance(updated, (int, float))
                      and not isinstance(updated, bool) else -1.0),
                    str(row.get("session_id") or ""))

        rows.sort(key=order)
        return self._answer(True, STATUS_OK, {
            "sessions": rows[:cap],
            "count": len(rows[:cap]),
            "matched": len(rows),
            "truncated": len(rows) > cap,
            "scanned": scanned,
            "skipped": skipped,
            "private_text_included": False,
            "note": "列表只有身份、状态与计数：正文与贡献只在 read_practice / context_view 里。",
        })

    def read_practice(self, session_id: Any, *, include_history: bool = False,
                      include_material_excerpt: bool = False,
                      entry_limit: int = DEFAULT_ENTRY_LIMIT,
                      excerpt_chars: int = DEFAULT_EXCERPT_CHARS) -> dict[str, Any]:
        """One session: the owner's current text, the contributions, the material states.

        Governance is all-or-nothing: when the shared source is not readable any more, every
        derived text is withheld (see the module docstring) and the identity/state/version facts
        stay readable.
        """
        loaded = self._load(_artifact_id(session_id), include_history=include_history)
        if not loaded.get("ok"):
            return loaded["answer"]
        states = self._material_states(
            loaded["parsed"], include_excerpt=include_material_excerpt,
            excerpt_chars=_limit(excerpt_chars, 4000))
        data = self._view(loaded, states, entry_limit=_limit(entry_limit, MAX_CONTRIBUTIONS))
        return self._answer(True, STATUS_OK, data)

    def context_view(self, session_id: Any, *, include_material_excerpt: bool = False,
                     entry_limit: int = DEFAULT_ENTRY_LIMIT,
                     excerpt_chars: int = DEFAULT_EXCERPT_CHARS) -> dict[str, Any]:
        """The bounded, *text-first* view a decision or a screen reads.

        It carries the real work text and the real contributions (never the marker line, never a
        machine placeholder), the current material states, and what this service explicitly does
        *not* own.  Zero writes.
        """
        loaded = self._load(_artifact_id(session_id), include_history=False)
        if not loaded.get("ok"):
            return loaded["answer"]
        states = self._material_states(
            loaded["parsed"], include_excerpt=include_material_excerpt,
            excerpt_chars=_limit(excerpt_chars, 4000))
        entry_cap = _limit(entry_limit, MAX_CONTRIBUTIONS)
        view = self._view(loaded, states, entry_limit=entry_cap)
        blocked = view["governance"]["blocked"]
        responses = list(view["responses"])
        data: dict[str, Any] = {
            "session_id": view["session_id"],
            "title": view["title"],
            "state": view["state"],
            "joint_state": view["joint_state"],
            "practice_revision": view["practice_revision"],
            "text": view["work"]["text"],
            "text_chars": view["work"]["chars"],
            "text_available": view["work"]["available"],
            "head_version_id": view["project"].get("head_version_id"),
            "materials": view["materials"],
            "contributions": view["contributions"],
            "contribution_count": view["contribution_count"],
            "contributions_truncated": view["contributions_truncated"],
            "responses": responses[-3:],
            "last_response": responses[-1] if responses else None,
            "pending_commitments": view["pending_commitments"],
            "paused": view["paused"],
            "ended": view["ended"],
            "last_ended_at": view.get("last_ended_at", ""),
            "reopen_count": view["reopen_count"],
            "governance": view["governance"],
            "limits": {"entry_limit": entry_cap,
                       "contributions": MAX_CONTRIBUTIONS,
                       "text_chars": MAX_WORK_CHARS},
            "not_authority": {
                "activity": False,
                "session_willingness": False,
                "relationship": False,
                "conversation": False,
                "note": "会话状态只是这件作品的；要不要开始/继续相处由 Life 的候选与决定说。",
            },
            "private_text_included": not blocked,
        }
        return self._answer(True, STATUS_OK, data)

    # -- reads: one operation key (the owner's own receipts, never a second ledger) ------

    def operation(self, operation_key: Any) -> dict[str, Any]:
        """What the owner's own record says this key did.  **Pure read: never resends a write.**

        There is no ledger of this module's own: the answer is the owner's receipt / operation
        row, read through whichever surface the projects owner actually exposes (named in
        ``describe()["operation_lookup"]``).  An owner that cannot be asked is *named*, never
        guessed, and the answer always carries ``read_only=True`` and ``resend=False``.
        """
        key = _operation_key(operation_key)
        if key is None:
            raise SharedPracticeContractRefused("operation_key is required")
        surface, reader = self._operation_surface()
        data: dict[str, Any] = {
            "operation_key": key,
            "effect": OPERATION_UNAVAILABLE,
            "applied": False,
            "recorded": False,
            "owner_reason": "",
            "artifact_id": None,
            "version_id": None,
            "version_number": None,
            "surface": surface,
            "read_only": True,
            "resend": False,
            "shared_practice": False,
        }
        if reader is None:
            data["note"] = ("这个 owner 没有按原键只读对账的面：A 不自建第二份账本，"
                            "也不拿这个键重发写。")
            return self._answer(False, STATUS_UNAVAILABLE, data,
                                reason_code=REASON_OPERATION_UNSUPPORTED)
        try:
            raw = reader(key)
        except Exception as exc:
            data["owner_reason"] = type(exc).__name__[:MAX_SOURCE_STATUS_CHARS]
            return self._answer(False, STATUS_UNAVAILABLE, data,
                                reason_code=REASON_OPERATION_UNAVAILABLE,
                                detail=type(exc).__name__)
        resolved = _operation_effect_of(raw)
        effect = resolved["effect"]
        data.update({
            "effect": effect,
            "applied": effect == OPERATION_APPLIED,
            "recorded": effect == OPERATION_APPLIED,
            "owner_reason": resolved["owner_reason"],
            "artifact_id": resolved["artifact_id"] or None,
            "version_id": resolved["version_id"] or None,
            "version_number": resolved["version_number"],
        })
        if effect == OPERATION_APPLIED and data["artifact_id"]:
            data.update(self._session_fact(str(data["artifact_id"])))
        if effect == OPERATION_APPLIED:
            return self._answer(True, STATUS_APPLIED, data)
        if effect == OPERATION_NOT_LANDED:
            return self._answer(False, STATUS_NOT_FOUND, data,
                                reason_code=REASON_OPERATION_NOT_FOUND)
        data["retryable"] = False
        return self._answer(False, STATUS_UNKNOWN, data, reason_code=REASON_OPERATION_UNKNOWN)

    def _operation_surface(self) -> tuple[str, Any]:
        """Which owner surface can answer one operation key (first hit wins; never a local ledger)."""
        for name in ("operation", "resolve_operation"):
            reader = getattr(self.projects, name, None)
            if callable(reader):
                return "projects.%s" % name, reader
        port = getattr(self.projects, "port", None)
        if port is not None:
            for name in ("read_effect_receipt", "lookup_operation", "resolve_operation"):
                reader = getattr(port, name, None)
                if callable(reader):
                    return "port.%s" % name, reader
        return "", None

    def _session_fact(self, artifact_id: str) -> dict[str, Any]:
        """Identity/state facts of one landed artifact -- only when it really is one of ours."""
        loaded = self._load(artifact_id, include_history=False)
        if not loaded.get("ok"):
            return {"shared_practice": False}
        parsed = loaded["parsed"]
        return {
            "shared_practice": True,
            "session_id": loaded["session_id"],
            "state": parsed["state"],
            "joint_state": self._joint_state(parsed),
            "practice_revision": parsed["practice_revision"],
        }

    # -- writes: open --------------------------------------------------------------------

    def open_practice(
        self,
        *,
        title: Any,
        opening_text: Any,
        material_refs: Any,
        operation_key: Any = None,
        basis: Any = None,
    ) -> dict[str, Any]:
        """Open one session around explicit shared materials and an explicit first text.

        Both callers may open (the person picks the material, the Life runtime opens from its own
        step); opening is **not** acceptance -- the session stays ``CANDIDATE`` until a verified
        subject response exists.  Every material is read through the owner first, so a reference
        that is not readable never becomes a session.
        """
        guard = self._guard("open", basis=basis)
        if guard is not None:
            return guard
        clean_title = _one_line(title, "title", limit=MAX_TITLE_CHARS)
        clean_opening = _text(opening_text, "opening_text", limit=MAX_OPENING_CHARS)
        refs = _material_refs(material_refs)
        if not refs:
            return self._rejected(REASON_MATERIAL_REQUIRED, {
                "material_refs": [],
                "detail": "一段共同实践必须点名至少一件可读的共享材料。",
            })
        states = self._read_materials(refs)
        bad = [row for row in states if row["blocking"] or row["stale"]]
        if bad:
            return self._rejected(REASON_MATERIAL_UNREADABLE, {
                "materials": states,
                "detail": "有材料现在读不出来（或已经换版）：不能拿它当共享材料。",
            })
        intent = _intent_digest("open", clean_title, clean_opening, ",".join(refs))
        explicit = _operation_key(operation_key)
        operation_check = "SKIPPED"
        idempotency_unverified = False
        if explicit:
            # One intended open is one session, even across a rebuilt service: ask the owner
            # first (its own receipts), and only write when it positively knows nothing landed.
            check = self.operation(explicit)
            effect = str((check.get("data") or {}).get("effect") or "")
            operation_check = effect or OPERATION_UNAVAILABLE
            if effect == OPERATION_APPLIED:
                return self._reuse_open(check.get("data") or {}, key=explicit, intent=intent)
            if effect == OPERATION_UNKNOWN:
                check["data"]["operation_check"] = OPERATION_UNKNOWN
                check["data"]["note"] = ("这个键在 owner 那边是不是落了还不确定：A 不重发写；"
                                         "先确认，再决定要不要换键。")
                return check
            if effect == OPERATION_UNAVAILABLE:
                idempotency_unverified = True
        now = self.now()
        metadata = {
            "schema": SCHEMA,
            "title": clean_title,
            "state": STATE_OPEN,
            "practice_revision": 1,
            "created_at": now,
            "updated_at": now,
            "materials": [{"artifact_id": row["artifact_id"],
                           "version_id": row["version_id"],
                           "revision": row["revision"],
                           "recorded_at": now} for row in states],
            "contributions": [],
            "responses": [],
            "work": {"chars": len(clean_opening), "updated_at": now,
                     "updated_by": self.actor, "basis": None},
            "paused": None,
            "ended": None,
            "reopen_count": 0,
        }
        if isinstance(basis, Mapping):
            verified = self._verify_basis(basis)
            if not verified["ok"]:
                return verified["answer"]
            metadata["work"]["basis"] = verified["block"]
        self._open_serial += 1
        key = explicit or shared_practice_operation_key(
            "open", self.subject_id, self.workspace_id, clean_title, clean_opening,
            ",".join(refs), self.now(), str(id(self)), str(self._open_serial))
        metadata["last_operation_key"] = key
        metadata["last_operation_intent"] = intent
        #: Written once, never overwritten by later writes: "this session was opened by this
        #: key with this intent", so a replay months later still compares against the open.
        metadata["open_operation"] = {"key": key, "intent": intent}
        try:
            answer = self.projects.create_project(
                title=clean_title,
                content=build_shared_practice_content(metadata, clean_opening),
                operation_key=key,
            )
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc)
        data = {
            "session_id": answer.get("artifact_id"),
            "title": clean_title,
            "state": STATE_OPEN,
            "joint_state": JOINT_CANDIDATE,
            "practice_revision": 1,
            "materials": states,
            "text_chars": len(clean_opening),
            "accepted": False,
            "operation_key": key if explicit else "",
            "operation_check": operation_check,
            "idempotency_unverified": idempotency_unverified,
            "replayed": False,
            "new_version": True,
            "note": ("打开只是候选：没有经核对的她的回应，就还不是共同接纳的一段。"
                     if not idempotency_unverified else
                     "打开只是候选；这个 owner 没有按原键对账面，A 无法证明这个键没落过，"
                     "重复调用不保证去重。"),
        }
        return self._settle(answer, data, default_reason=REASON_OWNER_UNAVAILABLE)

    def _reuse_open(self, fact: Mapping[str, Any], *, key: str,
                    intent: str) -> dict[str, Any]:
        """The same key + the same intent already opened a session: hand it back, write nothing.

        ``intent`` is title + opening text + material refs only -- **no instant** -- so a retry
        after a restart compares equal.  A key that landed somewhere else, or with another
        intent, is refused by name instead of being merged into a session it never meant.
        """
        artifact_id = str(fact.get("artifact_id") or "")
        if not artifact_id:
            return self._answer(False, STATUS_UNKNOWN, {
                "operation_key": key, "effect": OPERATION_APPLIED, "resend": False,
                "read_only": True, "operation_check": OPERATION_APPLIED,
                "note": "owner 说这个键落了，却没点名是哪件工件：不猜，也不重发写。",
            }, reason_code=REASON_RESOLUTION_INCONCLUSIVE)
        loaded = self._load(artifact_id, include_history=False)
        if not loaded.get("ok"):
            answer = loaded["answer"]
            if str(answer.get("reason_code") or "") in (REASON_NOT_A_SESSION,
                                                        REASON_MARKER_INVALID):
                return self._rejected(REASON_OPERATION_KEY_REUSED, {
                    "operation_key": key, "artifact_id": artifact_id,
                    "detail": "这个 key 已经落在另一件不是共同实践的工件上：不是重放。",
                })
            return answer
        parsed = loaded["parsed"]
        opened = parsed.get("open_operation") or {}
        if str(opened.get("key") or "") != key or str(opened.get("intent") or "") != intent:
            return self._rejected(REASON_OPERATION_KEY_REUSED, {
                "operation_key": key, "artifact_id": artifact_id, "session_id": artifact_id,
                "detail": "这个 key 已经开过另一段（标题/开场/材料不同）：不是重放。",
            })
        states = self._material_states(parsed)
        blocked = any(row["blocking"] for row in states)
        joint = self._joint_state(parsed)
        head = loaded["project"].get("head_version_id")
        return self._answer(True, STATUS_ALREADY_APPLIED, {
            "session_id": artifact_id,
            "title": "" if blocked else parsed["title"],
            "state": parsed["state"],
            "joint_state": joint,
            "practice_revision": parsed["practice_revision"],
            "materials": states,
            "text_chars": 0 if blocked else len(parsed["body"]),
            "accepted": joint == JOINT_JOINT,
            "contribution_count": len(parsed["contributions"]),
            "response_count": len(parsed["responses"]),
            "head_version_id": head,
            "version_id": head,
            "version_number": loaded["project"].get("version_number"),
            "operation_key": key,
            "operation_check": OPERATION_APPLIED,
            "replayed": True,
            "new_version": False,
            "note": "同一个 operation_key、同一份意图：原样交回原来那一段，没有新建第二件作品。",
        })

    # -- writes: contributions -----------------------------------------------------------

    def submit_user_contribution(
        self,
        session_id: Any,
        *,
        text: Any,
        expected_head: Any,
        commitment_state: Any = COMMITMENT_NONE,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """Record the person's own words in an open session.

        Only the person's side.  It can never carry the subject's voice, and it can at most leave
        a commitment ``PENDING`` -- ``ACCEPTED``/``DECLINED`` belong to the subject's side.
        """
        guard = self._guard("user_contribution")
        if guard is not None:
            return guard
        commitment = _commitment(commitment_state)
        if commitment not in USER_COMMITMENTS:
            return self._rejected(REASON_COMMITMENT_NOT_USERS, {
                "commitment_state": commitment,
                "detail": "用户侧最多只能把承诺挂成 PENDING：答应与否是她的那一半。",
            })
        return self._append(
            session_id, text=text, kind=KIND_MESSAGE, contributor=CONTRIBUTOR_USER,
            text_limit=MAX_CONTRIBUTION_CHARS, expected_head=expected_head,
            commitment=commitment, basis=None, operation_key=operation_key, action="user")

    def submit_subject_response(
        self,
        session_id: Any,
        *,
        text: Any,
        basis: Any = None,
        expected_head: Any,
        commitment_state: Any = COMMITMENT_NONE,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """Record her **final visible reply**, bound to a verified Life-side source."""
        guard = self._guard("subject_response", basis=basis)
        if guard is not None:
            return guard
        verified = self._verify_basis(basis)
        if not verified["ok"]:
            return verified["answer"]
        return self._append(
            session_id, text=text, kind=KIND_RESPONSE, contributor=CONTRIBUTOR_SUBJECT,
            text_limit=MAX_RESPONSE_CHARS, expected_head=expected_head,
            commitment=_commitment(commitment_state), basis=verified["block"],
            operation_key=operation_key, action="response")

    def submit_subject_contribution(
        self,
        session_id: Any,
        *,
        text: Any,
        basis: Any = None,
        expected_head: Any,
        commitment_state: Any = COMMITMENT_NONE,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """Record an actual piece she added (a real contribution, not just a reply)."""
        guard = self._guard("subject_contribution", basis=basis)
        if guard is not None:
            return guard
        verified = self._verify_basis(basis)
        if not verified["ok"]:
            return verified["answer"]
        return self._append(
            session_id, text=text, kind=KIND_CONTRIBUTION, contributor=CONTRIBUTOR_SUBJECT,
            text_limit=MAX_CONTRIBUTION_CHARS, expected_head=expected_head,
            commitment=_commitment(commitment_state), basis=verified["block"],
            operation_key=operation_key, action="contribution")

    def commit_work_text(
        self,
        session_id: Any,
        *,
        text: Any,
        basis: Any = None,
        expected_head: Any,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """Replace the joint work text as one new owner version (CAS).

        ``text`` is the **full** new body (the caller composes it from what it read); the previous
        version stays in the owner's immutable history, so a work change is never a silent
        overwrite.
        """
        guard = self._guard("work_text", basis=basis)
        if guard is not None:
            return guard
        verified = self._verify_basis(basis)
        if not verified["ok"]:
            return verified["answer"]
        target = _artifact_id(session_id)
        head = _head(expected_head)
        clean = _text(text, "text", limit=MAX_WORK_CHARS)
        loaded = self._load(target, include_history=False)
        if not loaded.get("ok"):
            return loaded["answer"]
        refusal = self._write_gate(loaded)
        if refusal is not None:
            return refusal
        parsed = loaded["parsed"]
        explicit = _operation_key(operation_key)
        intent = _intent_digest("work", clean)
        if explicit and explicit == str(parsed.get("last_operation_key") or ""):
            if str(parsed.get("last_operation_intent") or "") != intent:
                return self._rejected(REASON_OPERATION_KEY_REUSED, {
                    "session_id": target, "operation_key": explicit,
                    "detail": "这个 operation_key 已经用于另一版正文：不是重放。",
                })
            return self._answer(True, STATUS_ALREADY_APPLIED, {
                "session_id": target, "state": parsed["state"],
                "practice_revision": parsed["practice_revision"],
                "text_chars": len(parsed["body"]),
                "head_version_id": head,
                "operation_key": explicit, "replayed": True, "new_version": False,
                "note": "同一个 operation_key 已经写过了：没有写第二个版本。",
            })
        now = self.now()
        metadata = self._metadata_of(parsed)
        metadata["practice_revision"] = int(parsed["practice_revision"]) + 1
        metadata["updated_at"] = now
        metadata["work"] = {"chars": len(clean), "updated_at": now,
                           "updated_by": self.actor, "basis": verified["block"]}
        return self._commit(target, metadata, clean, head, operation_key, "work", {
            "session_id": target,
            "state": parsed["state"],
            "text_chars": len(clean),
            "basis": verified["block"],
        }, intent=intent)

    # -- writes: pause / resume / end / reopen -------------------------------------------

    def pause_practice(self, session_id: Any, *, note: Any = "", expected_head: Any,
                       basis: Any = None, operation_key: Any = None) -> dict[str, Any]:
        """Set this session to ``PAUSED`` (the work and every contribution stay untouched)."""
        guard = self._guard("pause", basis=basis)
        if guard is not None:
            return guard
        return self._set_state(session_id, target_state=STATE_PAUSED, note=note, basis=basis,
                               expected_head=expected_head, operation_key=operation_key,
                               action="pause")

    def resume_practice(self, session_id: Any, *, note: Any = "", expected_head: Any,
                        basis: Any = None, operation_key: Any = None) -> dict[str, Any]:
        """``PAUSED`` -> ``OPEN``.  Her side only, and only with a verified source."""
        guard = self._guard("resume", basis=basis)
        if guard is not None:
            return guard
        verified = self._verify_basis(basis)
        if not verified["ok"]:
            return verified["answer"]
        return self._set_state(session_id, target_state=STATE_OPEN, note=note,
                               basis=verified["block"], expected_head=expected_head,
                               operation_key=operation_key, action="resume")

    def end_practice(self, session_id: Any, *, reason: Any = "", expected_head: Any,
                     basis: Any = None, operation_key: Any = None) -> dict[str, Any]:
        """Explicitly end the session.  Nothing is deleted: the work text, every contribution and
        every version stay readable afterwards."""
        guard = self._guard("end", basis=basis)
        if guard is not None:
            return guard
        return self._set_state(session_id, target_state=STATE_ENDED, note=reason, basis=basis,
                               expected_head=expected_head, operation_key=operation_key,
                               action="end")

    def reopen_practice(self, session_id: Any, *, reason: Any = "", expected_head: Any,
                        basis: Any = None, operation_key: Any = None) -> dict[str, Any]:
        """``ENDED`` -> ``OPEN``: the real work and contributions are picked up again, not
        reconstructed.  Her side only, and only with a verified source."""
        guard = self._guard("reopen", basis=basis)
        if guard is not None:
            return guard
        verified = self._verify_basis(basis)
        if not verified["ok"]:
            return verified["answer"]
        return self._set_state(session_id, target_state=STATE_OPEN, note=reason,
                               basis=verified["block"], expected_head=expected_head,
                               operation_key=operation_key, action="reopen")

    def reconfirm_materials(self, session_id: Any, *, expected_head: Any,
                            material_refs: Any = (), basis: Any = None,
                            operation_key: Any = None) -> dict[str, Any]:
        """Re-point the shared material reference at the owner's *current* versions.

        The only honest way out of ``MOVED``: it records the version that is being shared now,
        still after reading it through the owner.
        """
        guard = self._guard("reconfirm", basis=basis)
        if guard is not None:
            return guard
        target = _artifact_id(session_id)
        head = _head(expected_head)
        refs = _material_refs(material_refs)
        loaded = self._load(target, include_history=False)
        if not loaded.get("ok"):
            return loaded["answer"]
        parsed = loaded["parsed"]
        if parsed["state"] == STATE_ENDED:
            return self._rejected(REASON_ALREADY_ENDED, {
                "session_id": target,
                "detail": "已经结束的一段不再改材料引用：先由她的那一半重新打开。",
            })
        if not refs:
            refs = [row["artifact_id"] for row in parsed["materials"]]
        if not refs:
            return self._rejected(REASON_MATERIAL_REQUIRED, {"session_id": target,
                                                             "material_refs": []})
        states = self._read_materials(refs)
        bad = [row for row in states if row["blocking"]]
        if bad:
            return self._rejected(REASON_MATERIAL_UNAVAILABLE, {
                "session_id": target, "materials": states,
                "detail": "有材料现在读不出来：不能把引用指过去。",
            })
        now = self.now()
        metadata = self._metadata_of(parsed)
        metadata["practice_revision"] = int(parsed["practice_revision"]) + 1
        metadata["updated_at"] = now
        metadata["materials"] = [{"artifact_id": row["artifact_id"],
                                  "version_id": row["version_id"],
                                  "revision": row["revision"],
                                  "recorded_at": now} for row in states]
        extra: dict[str, Any] = {"session_id": target, "state": parsed["state"],
                                 "materials": states}
        if isinstance(basis, Mapping):
            verified = self._verify_basis(basis)
            if not verified["ok"]:
                return verified["answer"]
            extra["basis"] = verified["block"]
        return self._commit(target, metadata, parsed["body"], head, operation_key,
                            "materials", extra)

    # -- internals: guards ---------------------------------------------------------------

    def _guard(self, action: str, *, basis: Any = None) -> Optional[dict[str, Any]]:
        """Is this caller allowed to attempt this write at all?  A named refusal, not a raise."""
        allowed = ACTOR_WRITES.get(action, ())
        if self.actor not in allowed:
            return self._rejected(REASON_ACTOR_NOT_ALLOWED, {
                "action": action,
                "actor": self.actor,
                "allowed_actors": list(allowed),
                "detail": "这个调用方不能写这一半：只有她的那一半能写主体的回应与作品。",
            })
        needs_basis = action in ACTOR_BASIS_REQUIRED
        if needs_basis and not isinstance(basis, Mapping):
            return self._rejected(REASON_SOURCE_REQUIRED, {
                "action": action,
                "detail": "主体的写入必须绑定一个经核对的来源（decision / event / action）。",
            })
        if self.actor == CALLER_USER and isinstance(basis, Mapping):
            return self._rejected(REASON_SOURCE_NOT_ALLOWED, {
                "action": action,
                "detail": "用户侧不提供来源：来源是 Life 的读面核对出来的。",
            })
        return None

    def _write_gate(self, loaded: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """State + governance gate shared by every write on an existing session."""
        parsed = loaded["parsed"]
        if parsed["state"] == STATE_ENDED:
            return self._rejected(REASON_ALREADY_ENDED, {
                "session_id": loaded["session_id"], "state": parsed["state"],
                "detail": "这一段已经结束：正文与贡献都还在，但不再追加。",
            })
        if parsed["state"] == STATE_PAUSED:
            return self._rejected(REASON_STATE_NOT_ALLOWED, {
                "session_id": loaded["session_id"], "state": parsed["state"],
                "detail": "这一段暂停着：先恢复（或结束）再说。",
            })
        return self._material_gate(parsed, session_id=loaded["session_id"])

    def _material_gate(self, parsed: Mapping[str, Any], *,
                       session_id: str) -> Optional[dict[str, Any]]:
        states = self._material_states(parsed)
        blocked = [row for row in states if row["blocking"]]
        if blocked:
            return self._rejected(REASON_MATERIAL_UNAVAILABLE, {
                "session_id": session_id, "materials": states,
                "detail": "共享材料已经读不出来（撤回/归档/读不到）：不再往这一段里写。",
            })
        stale = [row for row in states if row["stale"]]
        if stale:
            return self._rejected(REASON_MATERIAL_STALE, {
                "session_id": session_id, "materials": states,
                "detail": "共享材料换版了：先用 reconfirm_materials 把引用指到当前版本。",
            })
        return None

    # -- internals: owner reads ----------------------------------------------------------

    def _load(self, session_id: str, *, include_history: bool) -> dict[str, Any]:
        """Read one artifact through the projects owner and parse the session marker."""
        target = session_id
        try:
            answer = self.projects.read_project(target, include_history=include_history)
        except Exception as exc:
            return {"ok": False, "answer": self._unavailable(REASON_PORT_ERROR, exc,
                                                             session_id=target)}
        if not isinstance(answer, Mapping):
            return {"ok": False,
                    "answer": self._unavailable(REASON_PORT_ERROR,
                                                TypeError("read_project answered no dict"),
                                                session_id=target)}
        if not answer.get("ok"):
            status = str(answer.get("status") or STATUS_UNAVAILABLE)
            out = self._answer(False, status, {"session_id": target},
                               reason_code=str(answer.get("reason_code")
                                              or REASON_OWNER_UNAVAILABLE)[:64],
                               detail=str(answer.get("detail") or "")[:200])
            return {"ok": False, "answer": out}
        project = dict(answer.get("project") or {})
        content = project.get("content") if isinstance(project.get("content"), str) else ""
        parsed = parse_shared_practice_content(content)
        if not parsed["marker_present"]:
            return {"ok": False,
                    "answer": self._rejected(REASON_NOT_A_SESSION,
                                             {"session_id": target,
                                              "detail": "这件工件不是共同实践的一段。"})}
        if parsed["parse_error"] or not parsed["state"]:
            # Fail closed: an unreadable marker is never read as "a session with defaults".
            return {"ok": False,
                    "answer": self._rejected(REASON_MARKER_INVALID,
                                             {"session_id": target,
                                              "parse_error": str(parsed["parse_error"])[:80],
                                              "detail": "标记读不出来：按失败处理，不猜。"})}
        return {"ok": True, "session_id": target, "project": project, "parsed": parsed,
                "history": answer.get("history")}

    def _read_one_material(self, artifact_id: str) -> dict[str, Any]:
        try:
            answer = self.projects.read_project(artifact_id, include_history=False)
        except Exception as exc:
            return {"state": MATERIAL_UNAVAILABLE, "reason_code": REASON_PORT_ERROR,
                    "detail": type(exc).__name__, "project": {}, "owner_status": STATUS_UNAVAILABLE}
        if not isinstance(answer, Mapping):
            return {"state": MATERIAL_UNAVAILABLE, "reason_code": REASON_PORT_ERROR,
                    "detail": "READ_NO_ANSWER", "project": {},
                    "owner_status": STATUS_UNAVAILABLE}
        if not answer.get("ok"):
            status = str(answer.get("status") or STATUS_UNAVAILABLE)
            owner_reason = str(answer.get("reason_code") or "")[:64]
            if status == STATUS_NOT_FOUND:
                state = MATERIAL_NOT_FOUND
            elif status == STATUS_UNAVAILABLE:
                state = MATERIAL_UNAVAILABLE
            else:
                state = MATERIAL_GOVERNED
            return {"state": state, "reason_code": owner_reason or REASON_GOVERNED,
                    "detail": str(answer.get("detail") or "")[:200], "project": {},
                    "owner_status": status}
        return {"state": MATERIAL_READABLE, "reason_code": "", "detail": "",
                "project": dict(answer.get("project") or {}), "owner_status": STATUS_OK}

    def _material_states(self, parsed: Mapping[str, Any], *,
                         include_excerpt: bool = False,
                         excerpt_chars: int = DEFAULT_EXCERPT_CHARS) -> list[dict[str, Any]]:
        """Every recorded material as the owner reports it **now** (one owner read each)."""
        out: list[dict[str, Any]] = []
        for row in parsed.get("materials") or []:
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            read = self._read_one_material(artifact_id)
            project = read["project"]
            lifecycle = str(project.get("lifecycle") or "")
            current_version = str(project.get("head_version_id") or "") or ""
            recorded = str(row.get("version_id") or "")
            state = read["state"]
            if state == MATERIAL_READABLE:
                if lifecycle and lifecycle != "ACTIVE":
                    state = MATERIAL_ARCHIVED
                elif recorded and current_version and current_version != recorded:
                    state = MATERIAL_MOVED
                elif not recorded and not current_version:
                    state = MATERIAL_UNAVAILABLE
            block: dict[str, Any] = {
                "artifact_id": artifact_id,
                "version_id": recorded,
                "recorded_revision": row.get("revision"),
                "recorded_at": str(row.get("recorded_at") or ""),
                "state": state,
                "readable": state in (MATERIAL_READABLE, MATERIAL_MOVED),
                "blocking": state in BLOCKING_MATERIAL_STATES,
                "stale": state in STALE_MATERIAL_STATES,
                "reason_code": read["reason_code"],
                "owner_status": read["owner_status"],
                "lifecycle": lifecycle[:24],
                "current_version_id": current_version[:MAX_REF_CHARS],
                "current_revision": project.get("revision"),
                "title": str(project.get("title") or "")[:MAX_TITLE_CHARS]
                if state in (MATERIAL_READABLE, MATERIAL_MOVED) else "",
                "content_chars": project.get("content_chars")
                if state in (MATERIAL_READABLE, MATERIAL_MOVED) else None,
            }
            if include_excerpt and state == MATERIAL_READABLE:
                content = project.get("content")
                block["excerpt"] = (" ".join(content.split())[:int(excerpt_chars)]
                                    if isinstance(content, str) else "")
            out.append(block)
        return out

    def _read_materials(self, refs: Iterable[str]) -> list[dict[str, Any]]:
        """Read named materials to *open* or *re-point* a reference (version snapshot)."""
        out: list[dict[str, Any]] = []
        for artifact_id in refs:
            read = self._read_one_material(artifact_id)
            project = read["project"]
            lifecycle = str(project.get("lifecycle") or "")
            state = read["state"]
            if state == MATERIAL_READABLE and lifecycle and lifecycle != "ACTIVE":
                state = MATERIAL_ARCHIVED
            out.append({
                "artifact_id": artifact_id,
                "version_id": str(project.get("head_version_id") or ""),
                "revision": project.get("revision"),
                "recorded_at": "",
                "state": state,
                "readable": state == MATERIAL_READABLE,
                "blocking": state in BLOCKING_MATERIAL_STATES,
                "stale": False,
                "reason_code": read["reason_code"],
                "owner_status": read["owner_status"],
                "lifecycle": lifecycle[:24],
                "title": str(project.get("title") or "")[:MAX_TITLE_CHARS]
                if state == MATERIAL_READABLE else "",
                "content_chars": project.get("content_chars")
                if state == MATERIAL_READABLE else None,
            })
        return out
    # -- internals: sources --------------------------------------------------------------

    def _verify_basis(self, basis: Any) -> dict[str, Any]:
        """Verify one Life-side source reference through its own read port.

        The caller names a reference; the *facts* come from the owner's answer only.  Nothing the
        caller claims about the source is trusted, and nothing the owner said beyond identity and
        status is stored.
        """
        if not isinstance(basis, Mapping):
            raise SharedPracticeContractRefused("basis must be a mapping")
        present = {key: str(basis.get(key) or "").strip()
                   for key in BASIS_SPEC if str(basis.get(key) or "").strip()}
        if len(present) != 1:
            raise SharedPracticeContractRefused(
                "basis must name exactly one of %s" % (", ".join(sorted(BASIS_SPEC))))
        unknown = sorted(set(str(key) for key in basis) - set(BASIS_SPEC))
        if unknown:
            raise SharedPracticeContractRefused(
                "basis carries unknown keys: %s" % (", ".join(unknown)))
        ref_key, ref = next(iter(present.items()))
        if len(ref) > MAX_REF_CHARS:
            raise SharedPracticeContractRefused("basis ref is too long")
        kind, method_name, id_keys = BASIS_SPEC[ref_key]
        blocked = {
            "kind": kind, "ref": ref, "verified": False, "verified_id": "",
            "owner_status": "", "verified_at": self.now(),
        }
        if self.source_verifier is None:
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_VERIFIER_UNBOUND, {
                "basis": blocked,
                "detail": "这个服务没有接 Life 的读面：主体的写入不能凭空绑定来源。",
            })}
        reader = getattr(self.source_verifier, method_name, None)
        if not callable(reader):
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_VERIFIER_UNBOUND, {
                "basis": blocked, "reader": method_name,
                "detail": "读面缺少 %s。" % method_name,
            })}
        try:
            answer = reader(ref)
        except Exception as exc:
            blocked["owner_status"] = type(exc).__name__[:MAX_SOURCE_STATUS_CHARS]
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_UNREADABLE, {
                "basis": blocked, "detail": "读面拒绝或读不到这个来源。",
            })}
        if answer is None:
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_NOT_FOUND, {
                "basis": blocked, "detail": "读面没有这个来源。",
            })}
        if not isinstance(answer, Mapping):
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_UNREADABLE, {
                "basis": blocked, "detail": "读面回了一个不是 dict 的答复。",
            })}
        status = str(answer.get("status") or answer.get("reason_code") or "")[:MAX_SOURCE_STATUS_CHARS]
        if answer.get("ok") is False:
            blocked["owner_status"] = status
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_UNREADABLE, {
                "basis": blocked, "owner_status": status,
                "detail": "读面明确说这个来源读不出来。",
            })}
        found = self._identity_in(answer, id_keys)
        if not found:
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_MISMATCH, {
                "basis": blocked, "detail": "读面的答复里没有这个来源的身份。",
            })}
        if found != ref:
            return {"ok": False, "answer": self._rejected(REASON_SOURCE_MISMATCH, {
                "basis": blocked, "verified_id": found[:MAX_REF_CHARS],
                "detail": "读面回的是另一个来源。",
            })}
        blocked.update({"verified": True, "verified_id": found[:MAX_REF_CHARS],
                        "owner_status": status or "OK"})
        return {"ok": True, "block": blocked}

    @staticmethod
    def _identity_in(answer: Mapping[str, Any], id_keys: Sequence[str]) -> str:
        """Where the owner's own identity for that source sits (top level, then ``data``)."""
        for container in (answer, answer.get("data")):
            if not isinstance(container, Mapping):
                continue
            for key in id_keys:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    # -- internals: views ----------------------------------------------------------------

    def _summary(self, loaded: Mapping[str, Any], states: Sequence[Mapping[str, Any]]
                 ) -> dict[str, Any]:
        parsed = loaded["parsed"]
        project = loaded["project"]
        blocked = any(row["blocking"] for row in states)
        stale = any(row["stale"] for row in states)
        return {
            "session_id": loaded["session_id"],
            "title": "" if blocked else parsed["title"],
            "state": parsed["state"],
            "joint_state": self._joint_state(parsed),
            "practice_revision": parsed["practice_revision"],
            "created_at": parsed["created_at"],
            "updated_at": parsed["updated_at"],
            "head_version_id": project.get("head_version_id"),
            "version_number": project.get("version_number"),
            "revision": project.get("revision"),
            "lifecycle": str(project.get("lifecycle") or ""),
            "materials": [{"artifact_id": row["artifact_id"], "state": row["state"],
                           "readable": row["readable"], "stale": row["stale"],
                           "blocking": row["blocking"]} for row in states],
            "material_count": len(states),
            "contribution_count": len(parsed["contributions"]),
            "response_count": len(parsed["responses"]),
            "pending_commitments": sum(1 for entry in parsed["contributions"]
                                       if entry["commitment_state"] == COMMITMENT_PENDING),
            "governance_blocked": blocked,
            "governance_reason": (REASON_GOVERNED if blocked
                                  else REASON_MATERIAL_STALE if stale else ""),
            "title_withheld": blocked,
        }

    def _view(self, loaded: Mapping[str, Any], states: Sequence[Mapping[str, Any]], *,
              entry_limit: int) -> dict[str, Any]:
        """The full read payload.  Text is withheld *by construction* when governed."""
        parsed = loaded["parsed"]
        project = loaded["project"]
        blocked = any(row["blocking"] for row in states)
        stale = any(row["stale"] for row in states)
        contributions = list(parsed["contributions"])
        responses = list(parsed["responses"])
        withheld: list[str] = []
        body = parsed["body"]
        if blocked:
            body = ""
            withheld.extend(["title", "work.text", "contributions[].text",
                             "responses[].text", "paused.text", "ended.text",
                             "materials[].title", "materials[].excerpt", "project.title"])
        work = {
            "text": body,
            "chars": len(body),
            "available": bool(body) and not blocked,
            "updated_at": "" if blocked else str(parsed["work"].get("updated_at") or ""),
            "updated_by": "" if blocked else str(parsed["work"].get("updated_by") or ""),
            "basis": {} if blocked else _basis_out(parsed["work"].get("basis")),
        }
        shown = [self._entry_public(entry, blocked=blocked) for entry in contributions]
        shown_responses = [self._entry_public(entry, blocked=blocked) for entry in responses]
        truncated = len(shown) > entry_limit
        public_materials: list[dict[str, Any]] = []
        for row in states:
            item = dict(row)
            if row["blocking"]:
                item["title"] = ""
                item.pop("excerpt", None)
            public_materials.append(item)
        data: dict[str, Any] = {
            "session_id": loaded["session_id"],
            "title": "" if blocked else parsed["title"],
            "state": parsed["state"],
            "joint_state": self._joint_state(parsed),
            "accepted": self._joint_state(parsed) == JOINT_JOINT,
            "practice_revision": parsed["practice_revision"],
            "created_at": parsed["created_at"],
            "updated_at": parsed["updated_at"],
            "project": self._project_public(project, blocked=blocked),
            "materials": public_materials,
            "material_count": len(public_materials),
            "work": work,
            "contributions": shown[:entry_limit],
            "contribution_count": len(shown),
            "contributions_truncated": truncated,
            "responses": shown_responses,
            "response_count": len(shown_responses),
            "pending_commitments": [entry for entry in shown
                                    if entry["commitment_state"] == COMMITMENT_PENDING],
            "paused": None if blocked or not parsed["paused"] else {
                "at": parsed["paused"]["at"], "by": parsed["paused"]["by"],
                "text": parsed["paused"]["text"], "basis": parsed["paused"]["basis"]},
            # ``ended`` describes the state this session is *in now*: a reopened session is OPEN
            # again, so the end block is no longer current (the version it was written in stays in
            # the owner's history).  ``last_ended_at`` keeps the instant without its prose.
            "ended": None if blocked or parsed["state"] != STATE_ENDED
            or not parsed["ended"] else {
                "at": parsed["ended"]["at"], "by": parsed["ended"]["by"],
                "text": parsed["ended"]["text"], "basis": parsed["ended"]["basis"]},
            "last_ended_at": ("" if blocked or not parsed["ended"]
                              else str(parsed["ended"].get("at") or "")[:64]),
            "reopen_count": parsed["reopen_count"],
            "governance": {
                "blocked": blocked,
                "stale": stale,
                "reason_code": (REASON_GOVERNED if blocked
                                else REASON_MATERIAL_STALE if stale else ""),
                "redacted": withheld,
                "writes_allowed": not blocked and not stale and parsed["state"] == STATE_OPEN,
                "material_count": len(public_materials),
            },
            "private_text_included": not blocked,
            "skipped_entries": parsed["skipped_entries"],
        }
        if loaded.get("history") is not None:
            data["history"] = loaded["history"]
        return data

    @staticmethod
    def _entry_public(entry: Mapping[str, Any], *, blocked: bool) -> dict[str, Any]:
        out = {
            "seq": entry["seq"],
            "contributor": entry["contributor"],
            "kind": entry["kind"],
            "text": "" if blocked else entry["text"],
            "chars": 0 if blocked else len(entry["text"]),
            "commitment_state": entry["commitment_state"],
            "basis": entry["basis"],
            "recorded_at": entry["recorded_at"],
            "against_version_id": entry["against_version_id"],
            "operation_key": str(entry.get("operation_key") or ""),
        }
        return out

    @staticmethod
    def _project_public(project: Mapping[str, Any], *, blocked: bool) -> dict[str, Any]:
        """The owner's row, with the raw content (marker + body) never included."""
        lifecycle = str(project.get("lifecycle") or "")
        return {
            "artifact_id": str(project.get("artifact_id") or ""),
            "title": "" if blocked else str(project.get("title") or ""),
            "lifecycle": lifecycle,
            "status": "archived" if lifecycle == "ARCHIVED" else "active",
            "revision": project.get("revision"),
            "head_version_id": project.get("head_version_id"),
            "version_number": project.get("version_number"),
            "created_at": project.get("created_at"),
            "updated_at": project.get("updated_at"),
            "workspace_id": str(project.get("workspace_id") or ""),
            "source": SOURCE_OWNER,
        }

    @staticmethod
    def _joint_state(parsed: Mapping[str, Any]) -> str:
        if parsed["state"] == STATE_ENDED:
            return JOINT_ENDED
        for entry in parsed["responses"]:
            if entry["basis"].get("verified"):
                return JOINT_JOINT
        return JOINT_CANDIDATE

    @staticmethod
    def _metadata_of(parsed: Mapping[str, Any]) -> dict[str, Any]:
        """Rebuild the marker payload from what is already stored (writer cannot forge it)."""
        return {
            "schema": SCHEMA,
            "title": parsed["title"],
            "state": parsed["state"],
            "practice_revision": parsed["practice_revision"],
            "created_at": parsed["created_at"],
            "updated_at": parsed["updated_at"],
            "materials": [dict(row) for row in parsed["materials"]],
            "contributions": [dict(row) for row in parsed["contributions"]],
            "responses": [dict(row) for row in parsed["responses"]],
            "work": dict(parsed["work"]),
            "paused": dict(parsed["paused"]) if parsed["paused"] else None,
            "ended": dict(parsed["ended"]) if parsed["ended"] else None,
            "reopen_count": parsed["reopen_count"],
            "last_operation_key": parsed["last_operation_key"],
            "last_operation_intent": parsed["last_operation_intent"],
            "open_operation": (dict(parsed["open_operation"])
                               if parsed["open_operation"] else None),
        }

    # -- internals: writes ---------------------------------------------------------------

    def _append(self, session_id: Any, *, text: Any, kind: str, contributor: str,
                text_limit: int, expected_head: Any, commitment: str, basis: Any,
                operation_key: Any, action: str) -> dict[str, Any]:
        target = _artifact_id(session_id)
        head = _head(expected_head)
        clean = _text(text, "text", limit=text_limit)
        loaded = self._load(target, include_history=False)
        if not loaded.get("ok"):
            return loaded["answer"]
        refusal = self._write_gate(loaded)
        if refusal is not None:
            return refusal
        parsed = loaded["parsed"]
        entries_key = "responses" if kind == KIND_RESPONSE else "contributions"
        existing = list(parsed[entries_key])
        cap = MAX_RESPONSES if kind == KIND_RESPONSE else MAX_CONTRIBUTIONS
        if len(existing) >= cap:
            return self._rejected(REASON_TOO_MANY, {
                "session_id": target, "kind": kind, "limit": cap,
                "detail": "这一段已经写满了：再写就不是这一段了。",
            })
        explicit = _operation_key(operation_key)
        if explicit:
            # A replay of one intended effect: the same key is already an entry of this session,
            # so there is nothing to write.  Checking here (not only at the owner) is what makes
            # "same key, same payload" one contribution even though the composed document differs.
            intent = _intent_digest(action, contributor, kind, clean, commitment,
                                    (basis or {}).get("ref", "") if isinstance(basis, Mapping)
                                    else "")
            for row in list(parsed["contributions"]) + list(parsed["responses"]):
                if str(row.get("operation_key") or "") != explicit:
                    continue
                if _intent_digest(action, row.get("contributor"), row.get("kind"),
                                  row.get("text"), row.get("commitment_state"),
                                  (row.get("basis") or {}).get("ref", "")
                                  if isinstance(row.get("basis"), Mapping) else "") != intent:
                    # The same key with a *different* payload is not a replay: refuse it here
                    # instead of writing something the key never meant (the owner's own digest
                    # guard stays the second line of defence).
                    return self._rejected(REASON_OPERATION_KEY_REUSED, {
                        "session_id": target, "operation_key": explicit,
                        "existing_seq": row.get("seq"),
                        "detail": "这个 operation_key 已经用于另一条不同的内容：不是重放。",
                    })
                joint = self._joint_state(parsed)
                return self._answer(True, STATUS_ALREADY_APPLIED, {
                    "session_id": target,
                    "state": parsed["state"],
                    "joint_state": joint,
                    "entry": self._entry_public(row, blocked=False),
                    "contribution_count": len(parsed["contributions"]),
                    "response_count": len(parsed["responses"]),
                    "practice_revision": parsed["practice_revision"],
                    "accepted": joint == JOINT_JOINT,
                    "operation_key": explicit,
                    "head_version_id": head,
                    "replayed": True,
                    "new_version": False,
                    "note": "同一个 operation_key 已经在这一段里了：没有写第二个版本。",
                })
        now = self.now()
        seqs = [int(entry["seq"]) for entry in parsed["contributions"] + parsed["responses"]]
        seq = (max(seqs) + 1) if seqs else 1
        key = explicit or shared_practice_operation_key(
            "practice", action, target, head, str(seq), clean)
        entry = {
            "seq": seq,
            "contributor": contributor,
            "kind": kind,
            "text": clean,
            "commitment_state": commitment,
            "basis": dict(basis) if isinstance(basis, Mapping) else {},
            "recorded_at": now,
            "against_version_id": head,
            "operation_key": key,
        }
        existing.append(entry)
        metadata = self._metadata_of(parsed)
        metadata[entries_key] = existing
        metadata["practice_revision"] = int(parsed["practice_revision"]) + 1
        metadata["updated_at"] = now
        metadata["last_operation_key"] = key
        # A verified subject reply is what turns a candidate into a joint session; the counts and
        # the joint state below are therefore derived from the *metadata being written*, not from
        # the document read before this entry existed.
        try:
            content = build_shared_practice_content(metadata, parsed["body"])
        except SharedPracticeContractRefused as exc:
            return self._rejected(REASON_TOO_LARGE, {"session_id": target,
                                                     "detail": str(exc)[:200]})
        try:
            answer = self.projects.commit_project(artifact_id=target, content=content,
                                                  expected_head=head, operation_key=key)
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc, session_id=target)
        after = dict(parsed)
        after["contributions"] = list(metadata["contributions"])
        after["responses"] = list(metadata["responses"])
        joint = self._joint_state(after)
        data = {
            "session_id": target,
            "state": parsed["state"],
            "joint_state": joint,
            "entry": self._entry_public(entry, blocked=False),
            "contribution_count": len(metadata["contributions"]),
            "response_count": len(metadata["responses"]),
            "practice_revision": metadata["practice_revision"],
            "accepted": joint == JOINT_JOINT,
        }
        return self._settle(answer, data, default_reason=REASON_OWNER_UNAVAILABLE,
                            session_id=target, expected_head=head)

    def _set_state(self, session_id: Any, *, target_state: str, note: Any, basis: Any,
                   expected_head: Any, operation_key: Any, action: str) -> dict[str, Any]:
        target = _artifact_id(session_id)
        head = _head(expected_head)
        clean_note = _one_line(note, "note", limit=MAX_REASON_CHARS, allow_empty=True)
        loaded = self._load(target, include_history=False)
        if not loaded.get("ok"):
            return loaded["answer"]
        parsed = loaded["parsed"]
        explicit = _operation_key(operation_key)
        intent = _intent_digest(action, target_state, clean_note)
        if explicit and explicit == str(parsed.get("last_operation_key") or ""):
            if str(parsed.get("last_operation_intent") or "") != intent:
                return self._rejected(REASON_OPERATION_KEY_REUSED, {
                    "session_id": target, "operation_key": explicit,
                    "detail": "这个 operation_key 已经用于另一件状态变更：不是重放。",
                })
            return self._answer(True, STATUS_ALREADY_APPLIED, {
                "session_id": target, "state": parsed["state"],
                "practice_revision": parsed["practice_revision"],
                "head_version_id": head,
                "operation_key": explicit, "replayed": True, "new_version": False,
                "note": "同一个 operation_key 已经写过了：没有写第二个版本。",
            })
        current = parsed["state"]
        if target_state == STATE_PAUSED and current == STATE_ENDED:
            return self._rejected(REASON_ALREADY_ENDED, {"session_id": target,
                                                         "state": current})
        if target_state == STATE_OPEN and action == "resume" and current != STATE_PAUSED:
            return self._rejected(REASON_NOT_PAUSED, {"session_id": target, "state": current,
                                                      "detail": "这一段没有暂停着。"})
        if target_state == STATE_OPEN and action == "reopen" and current != STATE_ENDED:
            return self._rejected(REASON_NOT_ENDED, {"session_id": target, "state": current,
                                                     "detail": "这一段还没结束。"})
        if target_state == STATE_ENDED and current == STATE_ENDED:
            return self._answer(True, STATUS_ALREADY_APPLIED, {
                "session_id": target, "state": STATE_ENDED, "already_ended": True,
                "note": "这一段已经结束：不写第二个版本。",
            })
        if action != "open":
            refusal = self._material_gate(parsed, session_id=target)
            if refusal is not None and target_state != STATE_ENDED:
                return refusal
        now = self.now()
        metadata = self._metadata_of(parsed)
        metadata["state"] = target_state
        metadata["practice_revision"] = int(parsed["practice_revision"]) + 1
        metadata["updated_at"] = now
        block = {"at": now, "by": self.actor, "text": clean_note,
                 "basis": dict(basis) if isinstance(basis, Mapping) else {}}
        extra: dict[str, Any] = {"session_id": target, "state": target_state,
                                 "previous_state": current}
        if target_state == STATE_PAUSED:
            metadata["paused"] = block
        elif target_state == STATE_ENDED:
            metadata["ended"] = block
        elif action == "resume":
            metadata["paused"] = None
        elif action == "reopen":
            metadata["reopen_count"] = int(parsed["reopen_count"]) + 1
            metadata["paused"] = None
            extra["reopen_count"] = metadata["reopen_count"]
            extra["text_chars"] = len(parsed["body"])
            extra["contribution_count"] = len(parsed["contributions"])
            extra["note"] = "重开的是同一段：正文与每条贡献都还在，没有重建。"
        if isinstance(basis, Mapping):
            extra["basis"] = dict(basis)
        return self._commit(target, metadata, parsed["body"], head, operation_key,
                            action, extra, intent=intent)

    def _commit(self, session_id: str, metadata: Mapping[str, Any], body: str, head: str,
                operation_key: Any, action: str, extra: Mapping[str, Any],
                *, intent: str = "") -> dict[str, Any]:
        meta = dict(metadata)
        explicit = _operation_key(operation_key)
        key = explicit or shared_practice_operation_key(
            "practice", action, session_id, head, str(meta.get("practice_revision")),
            str(meta.get("state")), body)
        # Recorded in the version being written: a replay of this key can be recognised from the
        # document itself, without asking the owner to re-run the effect.  The intent digest is
        # what separates "the same effect again" from "this key reused for something else".
        meta["last_operation_key"] = key
        meta["last_operation_intent"] = intent or _intent_digest(action, body)
        try:
            content = build_shared_practice_content(meta, body)
        except SharedPracticeContractRefused as exc:
            return self._rejected(REASON_TOO_LARGE, {"session_id": session_id,
                                                     "detail": str(exc)[:200]})
        try:
            answer = self.projects.commit_project(artifact_id=session_id, content=content,
                                                  expected_head=head, operation_key=key)
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc, session_id=session_id)
        data = dict(extra)
        data.setdefault("session_id", session_id)
        data["practice_revision"] = meta.get("practice_revision")
        return self._settle(answer, data, default_reason=REASON_OWNER_UNAVAILABLE,
                            session_id=session_id, expected_head=head)

    # -- answers -------------------------------------------------------------------------

    @staticmethod
    def _answer(ok: bool, status: str, data: Optional[Mapping[str, Any]] = None,
                *, reason_code: str = "", detail: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": bool(ok),
            "status": str(status),
            "source": SOURCE_OWNER,
            "data": dict(data or {}),
        }
        if reason_code:
            payload["reason_code"] = str(reason_code)[:64]
        if detail:
            payload["detail"] = str(detail)[:200]
        return payload

    def _rejected(self, reason_code: str, data: Optional[Mapping[str, Any]] = None
                  ) -> dict[str, Any]:
        return self._answer(False, STATUS_REJECTED, data, reason_code=reason_code)

    def _unavailable(self, reason_code: str, exc: Exception, **extra: Any) -> dict[str, Any]:
        data = dict(extra)
        data["detail"] = type(exc).__name__
        return self._answer(False, STATUS_UNAVAILABLE, data, reason_code=reason_code)

    def _settle(self, answer: Any, data: Mapping[str, Any], *, default_reason: str,
                session_id: str = "", expected_head: str = "") -> dict[str, Any]:
        """Classify one projects answer; an UNKNOWN is reported, never retried here.

        ``expected_head`` lets the answer say one derived fact the owner's status does not: whether
        the version the owner named is the head the caller already had.  That is the honest reading
        of a replayed operation key (same key, same payload: the owner returns its original receipt,
        nothing new was written) and it never rewrites the owner's own status.
        """
        if not isinstance(answer, Mapping):
            return self._answer(False, STATUS_UNAVAILABLE, dict(data),
                                reason_code=REASON_PORT_ERROR,
                                detail="the projects owner answered no dict")
        status = str(answer.get("status") or STATUS_UNAVAILABLE)
        payload_data = dict(data)
        for key in ("artifact_id", "version_id", "version_number", "head_version_id",
                    "parent_version_id", "revision", "lifecycle", "operation_key",
                    "effect_state", "reconciled", "retryable", "owner_status"):
            if answer.get(key) is not None:
                payload_data[key] = answer[key]
        if expected_head:
            produced = payload_data.get("version_id")
            replayed = bool(produced) and str(produced) == str(expected_head)
            payload_data["replayed"] = replayed
            payload_data["new_version"] = bool(produced) and not replayed
        if session_id and not payload_data.get("session_id"):
            payload_data["session_id"] = session_id
        if answer.get("ok") and status in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            return self._answer(True, status, payload_data)
        out = self._answer(False, status, payload_data,
                           reason_code=str(answer.get("reason_code") or default_reason)[:64],
                           detail=str(answer.get("detail") or "")[:200])
        if status == STATUS_UNKNOWN:
            out["data"]["retryable"] = bool(answer.get("retryable"))
        return out


#: Which caller may attempt which write.  A caller outside its half gets a named refusal.
ACTOR_WRITES: dict[str, tuple[str, ...]] = {
    "open": (CALLER_LIFE, CALLER_USER),
    "user_contribution": (CALLER_USER,),
    "subject_response": (CALLER_LIFE,),
    "subject_contribution": (CALLER_LIFE,),
    "work_text": (CALLER_LIFE,),
    "pause": (CALLER_LIFE, CALLER_USER),
    "resume": (CALLER_LIFE,),
    "end": (CALLER_LIFE, CALLER_USER),
    "reopen": (CALLER_LIFE,),
    "reconfirm": (CALLER_LIFE, CALLER_USER),
}
#: Writes that are a *willingness* act of hers and therefore need a verified source.
ACTOR_BASIS_REQUIRED: tuple[str, ...] = ("subject_response", "subject_contribution",
                                         "work_text", "resume", "reopen")


# ---------------------------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------------------------


def _first_env(environment: Mapping[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = environment.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def shared_practice_service_from_env(
    port: Any = None,
    env: Optional[Mapping[str, str]] = None,
    *,
    actor: str,
    source_verifier: Any = None,
) -> Optional[SharedPracticeService]:
    """Build a service from the same env bindings ``private_project_service`` already speaks.

    Returns ``None`` when the workspace / grant / subject are not configured: a half-wired owner
    is never turned into a half-working one.  The caller's ``actor`` is mandatory -- there is no
    default role, so nobody gets the subject's half by accident.
    """
    environment: dict[str, str] = dict(env or {})
    workspace_id = _first_env(environment, ENV_WORKSPACE_NAMES)
    grant_id = _first_env(environment, ENV_GRANT_NAMES)
    subject_id = _first_env(environment, ENV_SUBJECT_NAMES)
    if not workspace_id or not grant_id or not subject_id:
        return None
    try:
        resolved = port
        if resolved is None or isinstance(resolved, str):
            if isinstance(resolved, str) and resolved.strip():
                from life_artifact_port import LifeArtifactPort

                resolved = LifeArtifactPort(resolved.strip(), subject_id)
            else:
                socket_path = _first_env(environment, ENV_SOCKET_NAMES)
                if not socket_path:
                    return None
                from private_project_service import life_supply_port_from_env

                resolved = life_supply_port_from_env(environment)
        excluded_raw = _first_env(environment, ENV_EXCLUDED_NAMES) or ""
        excluded = tuple(sorted(part.strip() for part in excluded_raw.split(",") if part.strip()))
        projects = PrivateProjectService(resolved, workspace_id, grant_id,
                                        subject_id=subject_id,
                                        excluded_artifact_ids=excluded)
        return SharedPracticeService(projects, actor=actor, source_verifier=source_verifier)
    except (ProjectUnavailable, ProjectContractRefused, SharedPracticeContractRefused,
            SharedPracticeUnavailable):
        return None
    except Exception:
        return None


#: The other names a resolver may look for.
from_env = shared_practice_service_from_env
service_from_env = shared_practice_service_from_env
shared_practice_from_env = shared_practice_service_from_env


__all__ = [
    "ACTORS",
    "ACTOR_BASIS_REQUIRED",
    "ACTOR_WRITES",
    "BASIS_ACTION",
    "BASIS_ADOPTION",
    "BASIS_DECISION",
    "BASIS_EVENT",
    "BASIS_KINDS",
    "BLOCKING_MATERIAL_STATES",
    "CALLER_LIFE",
    "CALLER_USER",
    "COMMITMENTS",
    "COMMITMENT_ACCEPTED",
    "COMMITMENT_DECLINED",
    "COMMITMENT_NONE",
    "COMMITMENT_PENDING",
    "CONTRIBUTORS",
    "CONTRIBUTOR_SUBJECT",
    "CONTRIBUTOR_USER",
    "DEFAULT_ENTRY_LIMIT",
    "DEFAULT_EXCERPT_CHARS",
    "DEFAULT_LIST",
    "JOINT_CANDIDATE",
    "JOINT_ENDED",
    "JOINT_JOINT",
    "JOINT_STATES",
    "KINDS",
    "KIND_CONTRIBUTION",
    "KIND_MESSAGE",
    "KIND_RESPONSE",
    "MARKER_CLOSE",
    "MARKER_OPEN",
    "MARKER_VERSION",
    "MATERIAL_ARCHIVED",
    "MATERIAL_GOVERNED",
    "MATERIAL_MOVED",
    "MATERIAL_NOT_FOUND",
    "MATERIAL_READABLE",
    "MATERIAL_STATES",
    "MATERIAL_UNAVAILABLE",
    "MAX_CONTRIBUTIONS",
    "MAX_CONTRIBUTION_CHARS",
    "MAX_LIST",
    "MAX_MATERIALS",
    "MAX_OPENING_CHARS",
    "MAX_RESPONSES",
    "MAX_RESPONSE_CHARS",
    "MAX_SCAN",
    "MAX_TOTAL_CONTENT_CHARS",
    "MAX_WORK_CHARS",
    "OPERATION_APPLIED",
    "OPERATION_EFFECTS",
    "OPERATION_NOT_LANDED",
    "OPERATION_SURFACES",
    "OPERATION_UNAVAILABLE",
    "OPERATION_UNKNOWN",
    "PRACTICE_MARKER",
    "REASON_ACTOR_NOT_ALLOWED",
    "REASON_ALREADY_ENDED",
    "REASON_COMMITMENT_NOT_USERS",
    "REASON_GOVERNED",
    "REASON_MARKER_INVALID",
    "REASON_MATERIAL_REQUIRED",
    "REASON_MATERIAL_STALE",
    "REASON_MATERIAL_UNAVAILABLE",
    "REASON_MATERIAL_UNREADABLE",
    "REASON_NOT_A_SESSION",
    "REASON_NOT_ENDED",
    "REASON_NOT_PAUSED",
    "REASON_OPERATION_KEY_REUSED",
    "REASON_OPERATION_NOT_FOUND",
    "REASON_OPERATION_UNAVAILABLE",
    "REASON_OPERATION_UNKNOWN",
    "REASON_OPERATION_UNSUPPORTED",
    "REASON_OWNER_UNAVAILABLE",
    "REASON_PORT_ERROR",
    "REASON_SOURCE_MISMATCH",
    "REASON_SOURCE_NOT_ALLOWED",
    "REASON_SOURCE_NOT_FOUND",
    "REASON_SOURCE_REQUIRED",
    "REASON_SOURCE_UNREADABLE",
    "REASON_SOURCE_VERIFIER_UNBOUND",
    "REASON_STATE_NOT_ALLOWED",
    "REASON_TEXT_REQUIRED",
    "REASON_TOO_LARGE",
    "REASON_TOO_MANY",
    "SCHEMA",
    "STATES",
    "STATE_ENDED",
    "STATE_OPEN",
    "STATE_PAUSED",
    "STALE_MATERIAL_STATES",
    "SharedPracticeContractRefused",
    "SharedPracticeError",
    "SharedPracticeService",
    "SharedPracticeUnavailable",
    "build_shared_practice_content",
    "from_env",
    "is_shared_practice_content",
    "parse_shared_practice_content",
    "service_from_env",
    "shared_practice_from_env",
    "shared_practice_operation_key",
    "shared_practice_service_from_env",
    "shared_practice_state",
]
