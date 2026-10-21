"""v0.4 B: 分享生命周期与本地投递 -- 一件分享就是一件 ManagedArtifact。

What this module is
-------------------
One service the Life subject (A) calls to turn "我有一段想给她看的东西" into a real, bounded,
private draft, then -- only by an explicit action -- into a real local-inbox record:

    prepare_share   -> DRAFT
    apply_action("defer")      -> DEFERRED
    apply_action("drop")       -> DROPPED   (terminal)
    apply_action("expire")     -> EXPIRED   (terminal)
    apply_action("send_local") -> LOCAL_DELIVERED

Every one of those transitions is a **new version of one ``ManagedArtifact``** (through
``PrivateProjectService``: ``create_project`` / ``commit_project`` / ``read_project`` /
``list_projects``).  The draft's state, source binding, audience, channel and the local delivery
record live in a bounded envelope at the head of that artifact's text::

    nyairo.share.v1
    state=DRAFT
    source_artifact_id=artifact:...
    source_version_id=artifact-version:...
    source_revision=3
    ...
    text:
    <the bounded share text, verbatim>

So "the version is the state": compare-and-swap is the owner's ``expected_head``, the state history
is the owner's immutable ``artifact_versions``, and a restart loses nothing because there is nothing
here to lose.

What it is not
--------------
* **No second work library, no second memory, no store of its own.**  This module opens no sqlite
  file, writes no jsonl, keeps no in-process ledger.  ``describe()`` reports exactly that.
* **No real external send.**  The only channel is the constant ``local_inbox`` and the only
  audience the constant ``local:owner``.  ``LOCAL_DELIVERED`` means "this bounded text is now a
  version in her own local inbox on this instance"; it is **not** a channel ``SENT``, not a
  ``DELIVERED``, not ``READ``, not ``ACKNOWLEDGED`` -- the receipt says so field by field.  A
  free-form e-mail / URL / telegram name is refused, and no external kill switch is touched.
* **Not a proposal machine and not an auto-publisher.**  ``prepare_share`` needs an explicit
  ``share_text`` or an explicit ``excerpt_chars``; with neither it refuses
  (``SHARE_TEXT_REQUIRED``).  The whole private material is never pasted in by default, no tick
  turns a finished work into a pending message, and "NO_ACTION" is a perfectly normal answer.
* **No timer.**  Nothing here polls.  ``defer`` writes one version once when A says so; whether the
  deferred opportunity has arrived is A's judgement, not a per-tick re-evaluation, and
  ``DROPPED``/``EXPIRED`` never come back.

Reused, not reimplemented
-------------------------
``PrivateProjectService`` (the owner-shaped store), ``life_artifact_port.operation_key_for`` (the
owner's key derivation), and the canonical status vocabulary of the owner.  The alpha-contact
canonical contracts were read for vocabulary only (``DRAFT``/``DEFERRED``/``DROPPED``/``EXPIRED``,
grounded excerpt, "sent != delivered != read"); **no** shadow implementation of them is copied here
and none of their files is touched.

See ``docs/engineering/v04-sharing-service-result.md`` for the exact API A integrates against.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from life_artifact_port import operation_key_for  # noqa: E402
import private_project_service as _projects_owner  # noqa: E402

#: What the ``source`` field says: every fact in an answer came from this owner.
SOURCE_OWNER = "ManagedArtifact"

#: Envelope schema marker (first line of every share artifact's text).
SHARE_SCHEMA = "nyairo.share.v1"

#: The machine marker every share artifact's **title** carries, so a list can be filtered without
#: reading every artifact.  The authority is still the envelope; a row whose text is not an
#: envelope is reported as ``NOT_A_SHARE_ARTIFACT`` and never guessed into a share.
TITLE_PREFIX = "[share] "

#: The only channel and the only audience this module will ever write.
CHANNEL_LOCAL_INBOX = "local_inbox"
AUDIENCE_LOCAL_OWNER = "local:owner"
LOCAL_AUDIENCES = (AUDIENCE_LOCAL_OWNER,)
LOCAL_CHANNELS = (CHANNEL_LOCAL_INBOX,)

#: Public share states.  ``UNKNOWN`` is an *answer* status, never a stored state.
STATE_DRAFT = "DRAFT"
STATE_DEFERRED = "DEFERRED"
STATE_DROPPED = "DROPPED"
STATE_EXPIRED = "EXPIRED"
STATE_LOCAL_DELIVERED = "LOCAL_DELIVERED"
SHARE_STATES = (STATE_DRAFT, STATE_DEFERRED, STATE_DROPPED, STATE_EXPIRED,
                STATE_LOCAL_DELIVERED)
TERMINAL_STATES = (STATE_DROPPED, STATE_EXPIRED)
SENDABLE_STATES = (STATE_DRAFT, STATE_DEFERRED)

#: The three explicit actions plus the explicit expiry write.
ACTION_SEND_LOCAL = "send_local"
ACTION_DEFER = "defer"
ACTION_DROP = "drop"
ACTION_EXPIRE = "expire"
SHARE_ACTIONS = (ACTION_SEND_LOCAL, ACTION_DEFER, ACTION_DROP, ACTION_EXPIRE)

#: Answer statuses (see the result doc, section 1.5).
STATUS_OK = "ok"
STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_STALE_SOURCE = "stale_source"
STATUS_CONFLICT = "conflict"
STATUS_NOT_FOUND = "not_found"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"

#: Reason codes.
REASON_SHARE_TEXT_REQUIRED = "SHARE_TEXT_REQUIRED"
REASON_SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
REASON_SOURCE_UNREADABLE = "SOURCE_UNREADABLE"
REASON_SOURCE_ARCHIVED = "SOURCE_ARCHIVED"
REASON_SOURCE_REVISION_CHANGED = "SOURCE_REVISION_CHANGED"
REASON_SOURCE_HAS_NO_TEXT = "SOURCE_HAS_NO_TEXT"
REASON_AUDIENCE_REFUSED = "AUDIENCE_REFUSED"
REASON_CHANNEL_REFUSED = "CHANNEL_REFUSED"
REASON_SHARE_NOT_FOUND = "SHARE_NOT_FOUND"
REASON_NOT_A_SHARE = "NOT_A_SHARE_ARTIFACT"
REASON_ALREADY_DELIVERED = "SHARE_ALREADY_DELIVERED"
REASON_TERMINAL_STATE = "SHARE_TERMINAL_STATE"
REASON_EXPIRED = "SHARE_EXPIRED"
REASON_STATE_NOT_ALLOWED = "SHARE_STATE_NOT_ALLOWED"
REASON_DEFER_CONDITION_REQUIRED = "DEFER_CONDITION_REQUIRED"
REASON_CAS_CONFLICT = "CAS_CONFLICT"
REASON_OPERATION_NEVER_LANDED = "OPERATION_NEVER_LANDED"
REASON_RESOLUTION_INCONCLUSIVE = "RESOLUTION_INCONCLUSIVE"
REASON_PORT_ERROR = "SHARE_PORT_ERROR"
REASON_NO_PROJECTS = "SHARE_PROJECTS_UNAVAILABLE"
#: The source artifact's own text could not be read because the owner refused the read (Governance,
#: a tombstone, a lost grant...).  A refusal is named: it is not "moved", not "archived", not "gone".
REASON_SOURCE_GOVERNED = "SOURCE_GOVERNED"

#: What the recorded source looks like right now -- the one vocabulary the read, list and prepare
#: paths share.  Only the first three are *readable*: the bounded share text may be projected then.
SOURCE_STATE_READABLE = "READABLE"
SOURCE_STATE_REVISION_CHANGED = "REVISION_CHANGED"
SOURCE_STATE_ARCHIVED = "ARCHIVED"
SOURCE_STATE_GOVERNED = "GOVERNED"
SOURCE_STATE_NOT_FOUND = "NOT_FOUND"
SOURCE_STATE_UNAVAILABLE = "UNAVAILABLE"
SOURCE_STATES = (SOURCE_STATE_READABLE, SOURCE_STATE_REVISION_CHANGED, SOURCE_STATE_ARCHIVED,
                 SOURCE_STATE_GOVERNED, SOURCE_STATE_NOT_FOUND, SOURCE_STATE_UNAVAILABLE)
#: States in which the source read succeeded: the share's own bounded text may be shown.
READABLE_SOURCE_STATES = (SOURCE_STATE_READABLE, SOURCE_STATE_REVISION_CHANGED,
                          SOURCE_STATE_ARCHIVED)

#: Bounds.  A share is never "the whole private material".
MAX_SHARE_TEXT_CHARS = 2000
MAX_EXCERPT_CHARS = 400
MAX_TITLE_CHARS = 80
MAX_CONDITION_CHARS = 200
MAX_REASON_CHARS = 200
MAX_LIST = 100
MAX_OPERATION_KEY_CHARS = 128
MIN_EXPIRY_S = 60
MAX_EXPIRY_S = 2_592_000  # 30 days

#: The projects-owner surface this service calls.  Checked once at construction, so a wiring
#: mistake is a clear refusal instead of an ``AttributeError`` halfway through a write.
REQUIRED_PROJECT_METHODS: tuple[str, ...] = (
    "list_projects",
    "read_project",
    "create_project",
    "commit_project",
)

#: Envelope header keys, in the order they are written.  ``text:`` is always last.
_CORE_KEYS = ("state", "source_artifact_id", "source_version_id", "source_revision",
              "source_content_chars", "source_title_chars", "audience", "channel",
              "workspace_id", "subject_id", "share_revision", "created_at", "updated_at",
              "excerpt_kind", "excerpt_chars")
_OPTIONAL_KEYS = ("expires_at", "defer_condition", "opportunity_ref", "deferred_at",
                  "dropped_reason", "dropped_at", "expired_reason", "expired_at",
                  "delivery_id", "delivered_at")
_TEXT_MARKER = "text:"


class SharingError(RuntimeError):
    """Base class for this service's own refusals (a wiring/programming mistake, not an answer)."""


class SharingContractRefused(SharingError):
    """The caller asked for something this service does not accept at all."""


class SharingUnavailable(SharingError):
    """No usable projects owner was supplied (the owner was not even asked)."""


def sharing_operation_key(*parts: Any) -> str:
    """A deterministic operation key for one intended share effect.

    The owner keys idempotency on ``operation_key``, so the key is derived from *what is intended*
    (never from the attempt): replaying the same intent, or resolving an UNKNOWN and retrying with
    the same key, can never mint a second version or a second delivery.
    """
    cleaned = [str(part) for part in parts if str(part) != ""]
    if not cleaned:
        raise SharingContractRefused("sharing_operation_key requires at least one part")
    digest = operation_key_for("life_sharing", *cleaned)
    return "share:%s" % digest.split(":", 1)[-1]


def _text(value: Any, name: str, *, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SharingContractRefused("%s must be a string" % name)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise SharingContractRefused("%s must not contain NUL" % name)
    if not allow_empty and not text.strip():
        raise SharingContractRefused("%s must not be blank" % name)
    if len(text) > int(limit):
        raise SharingContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return text


def _one_line(value: Any, name: str, *, limit: int, required: bool = False) -> str:
    """A single-line value (no newlines, no NUL), bounded.  ``""`` when absent and not required."""
    if value is None:
        if required:
            raise SharingContractRefused("%s is required" % name)
        return ""
    if not isinstance(value, str):
        raise SharingContractRefused("%s must be a string" % name)
    text = " ".join(part.strip() for part in value.replace("\x00", " ").splitlines() if part.strip())
    text = text.strip()[:int(limit)]
    if required and not text:
        raise SharingContractRefused("%s must not be blank" % name)
    return text


def _artifact_id(value: Any, name: str = "share_id") -> str:
    """The owner's own artifact id, canonicalised the same way the projects owner does it.

    A caller may hold either the full id (``artifact:<hex>``) or the bare hex it saw in a display
    ref.  Both name the same artifact, so both are accepted; the id the owner is asked about is
    always the full one.
    """
    if not isinstance(value, str) or not value.strip():
        raise SharingContractRefused("%s is required" % name)
    text = value.strip()
    if len(text) > 200:
        raise SharingContractRefused("%s is too long" % name)
    if not text.startswith("artifact:") and re.match(r"^[0-9a-fA-F]{8,64}$", text):
        return "artifact:" + text
    return text


def _operation_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SharingContractRefused("operation_key must be a non-empty string")
    text = value.strip()
    if len(text) > MAX_OPERATION_KEY_CHARS:
        raise SharingContractRefused("operation_key is too long")
    return text


def is_share_title(title: Any) -> bool:
    """True when an artifact title carries this module's machine marker."""
    return isinstance(title, str) and title.startswith(TITLE_PREFIX)


def share_title(title: Optional[str], *, fallback: str = "") -> str:
    """The artifact title for one share: the marker plus the caller's own (or the source's) title."""
    base = _one_line(title, "title", limit=MAX_TITLE_CHARS) or \
        _one_line(fallback, "title", limit=MAX_TITLE_CHARS) or "未命名分享"
    return TITLE_PREFIX + base


def share_text_title(title: str) -> str:
    """Strip this module's marker from an artifact title (for display)."""
    text = str(title or "")
    return text[len(TITLE_PREFIX):] if text.startswith(TITLE_PREFIX) else text


# --------------------------------------------------------------------------------------------
# The bounded envelope
# --------------------------------------------------------------------------------------------

def encode_share_content(share: Mapping[str, Any], text: str) -> str:
    """Render one share envelope.  ``text:`` is the last header line, so the body is verbatim."""
    if not isinstance(text, str):
        raise SharingContractRefused("share text must be a string")
    lines = [SHARE_SCHEMA]
    for key in _CORE_KEYS:
        value = share.get(key)
        lines.append("%s=%s" % (key, "" if value is None else value))
    for key in _OPTIONAL_KEYS:
        value = share.get(key)
        if value is None or value == "":
            continue
        lines.append("%s=%s" % (key, value))
    lines.append(_TEXT_MARKER)
    return "\n".join(lines) + "\n" + text


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def parse_share_content(content: Any) -> Optional[dict[str, Any]]:
    """Parse one envelope.  ``None`` = this text is not a v1 share envelope (never guessed).

    The body after ``text:`` is returned verbatim, including line breaks, but is **not** returned
    for text that is not an envelope: an unreadable/foreign artifact can never leak a body through
    this parser.
    """
    if not isinstance(content, str) or not content:
        return None
    lines = content.split("\n")
    if lines[0].strip() != SHARE_SCHEMA:
        return None
    fields: dict[str, str] = {}
    body_index: Optional[int] = None
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == _TEXT_MARKER:
            body_index = index
            break
        if not line.strip():
            continue
        if "=" not in line:
            return None
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in fields:
            return None
        fields[key] = value.strip()
    if body_index is None:
        return None
    state = fields.get("state", "")
    if state not in SHARE_STATES:
        return None
    text = "\n".join(lines[body_index + 1:])
    return {
        "schema": SHARE_SCHEMA,
        "state": state,
        "text": text,
        "text_chars": len(text),
        "source_artifact_id": fields.get("source_artifact_id", ""),
        "source_version_id": fields.get("source_version_id", "") or None,
        "source_revision": _as_int(fields.get("source_revision")),
        "source_content_chars": _as_int(fields.get("source_content_chars")),
        "source_title_chars": _as_int(fields.get("source_title_chars")),
        "audience": fields.get("audience", ""),
        "channel": fields.get("channel", ""),
        "workspace_id": fields.get("workspace_id", ""),
        "subject_id": fields.get("subject_id", ""),
        "share_revision": _as_int(fields.get("share_revision")) or 0,
        "created_at": fields.get("created_at", ""),
        "updated_at": fields.get("updated_at", ""),
        "excerpt_kind": fields.get("excerpt_kind", ""),
        "excerpt_chars": _as_int(fields.get("excerpt_chars")),
        "expires_at": fields.get("expires_at", "") or None,
        "defer_condition": fields.get("defer_condition", "") or None,
        "opportunity_ref": fields.get("opportunity_ref", "") or None,
        "deferred_at": fields.get("deferred_at", "") or None,
        "dropped_reason": fields.get("dropped_reason", "") or None,
        "dropped_at": fields.get("dropped_at", "") or None,
        "expired_reason": fields.get("expired_reason", "") or None,
        "expired_at": fields.get("expired_at", "") or None,
        "delivery_id": fields.get("delivery_id", "") or None,
        "delivered_at": fields.get("delivered_at", "") or None,
        "unknown_keys": sorted(set(fields) - set(_CORE_KEYS) - set(_OPTIONAL_KEYS)),
    }


# --------------------------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------------------------

def _utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(timespec="milliseconds")


def _epoch_of(value: Any) -> Optional[float]:
    """An ISO timestamp (or epoch number) -> epoch seconds.  ``None`` when unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _normalise_clock_value(value: Any) -> float:
    if isinstance(value, bool):
        raise SharingContractRefused("clock must return epoch seconds, a datetime or an ISO string")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        parsed = _epoch_of(value)
        if parsed is None:
            raise SharingContractRefused("clock returned an unparseable timestamp")
        return parsed
    raise SharingContractRefused("clock must return epoch seconds, a datetime or an ISO string")


class LifeSharingService:
    """Prepare / read / list / act on one subject's share drafts and their local deliveries."""

    def __init__(self, projects: Any, *, clock: Optional[Any] = None) -> None:
        if projects is None:
            raise SharingUnavailable("PROJECTS_REQUIRED")
        missing = [name for name in REQUIRED_PROJECT_METHODS
                   if not callable(getattr(projects, name, None))]
        if missing:
            raise SharingUnavailable("PROJECTS_METHODS_MISSING:%s" % ",".join(missing))
        self.projects = projects
        self._clock = clock
        #: The owner's own identity facts, when it exposes them.  Never invented.
        self.subject_id = str(getattr(projects, "subject_id", "") or "")
        self.workspace_id = str(getattr(projects, "workspace_id", "") or "")

    # -- introspection -------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """What this service is wired to.  No owner call, no private text, no store of its own."""
        return {
            "kind": "life_sharing_service",
            "source": SOURCE_OWNER,
            "schema": SHARE_SCHEMA,
            "title_prefix": TITLE_PREFIX,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "audiences": list(LOCAL_AUDIENCES),
            "channels": list(LOCAL_CHANNELS),
            "states": list(SHARE_STATES),
            "actions": list(SHARE_ACTIONS),
            "owns_no_store": True,
            "external_send": False,
        }

    # -- clock ---------------------------------------------------------------------------

    def _now(self) -> float:
        if self._clock is None:
            return datetime.now(tz=timezone.utc).timestamp()
        return _normalise_clock_value(self._clock())

    # -- reads ---------------------------------------------------------------------------

    def read_share(self, share_id: Any, *, include_text: bool = True) -> dict[str, Any]:
        """One share as the owner currently holds it, plus the receipt when it was delivered.

        The bounded share text is projected **only** while the recorded source still reads back from
        the owner.  A refused/governed source read withholds the whole dependent text (metadata,
        state, source binding and receipt stay): the text was excerpted from that source, so showing
        it after the owner refused the source would leak governed content through a derived artifact.
        """
        target = _artifact_id(share_id)
        view = self._read_share_artifact(target)
        if not view["ok"]:
            return view
        share = view["data"]["share"]
        parsed = view["data"]["_parsed"]
        state, derived = self._effective_state(parsed)
        share["state"] = state
        share["state_derived"] = derived
        source_state = self.source_state_of(parsed)
        readable = self.source_is_readable(source_state)
        withheld = bool(include_text and not readable)
        if not include_text or withheld:
            share["text"] = ""
        share["text_withheld"] = withheld
        share["text_available"] = bool(readable)
        share["private_text_included"] = bool(include_text and not withheld)
        receipt = self._receipt(parsed) if state == STATE_LOCAL_DELIVERED else None
        data = {
            "share": share,
            "source_current": source_state,
            "source_readable": readable,
            "receipt": receipt,
            "delivered": state == STATE_LOCAL_DELIVERED,
            "private_text_included": bool(include_text and not withheld),
            "text_withheld": withheld,
            "withheld_reason_code": str(source_state.get("reason_code") or "") if withheld else "",
        }
        answer = self._answer(ok=True, status=STATUS_OK, data=data)
        answer["share_id"] = target
        answer["state"] = state
        return answer

    def list_shares(self, *, states: Optional[Iterable[str]] = None,
                    include_text: bool = False,
                    limit: Optional[int] = None) -> dict[str, Any]:
        """Every share the owner holds for this subject, newest revision first."""
        wanted: Optional[list[str]] = None
        if states is not None:
            wanted = []
            for entry in states:
                text = str(entry or "").strip().upper()
                if not text:
                    continue
                if text not in SHARE_STATES:
                    raise SharingContractRefused("unknown share state: %r" % (entry,))
                if text not in wanted:
                    wanted.append(text)
        cap = MAX_LIST if limit is None else max(1, min(int(limit), MAX_LIST))
        try:
            listing = self.projects.list_projects()
        except Exception as exc:  # a broken owner is not "no shares"
            return self._unavailable(REASON_PORT_ERROR, exc)
        if not isinstance(listing, Mapping):
            return self._unavailable(REASON_PORT_ERROR, TypeError("list_projects answered no dict"))
        if not listing.get("ok"):
            answer = self._answer(ok=False, status=self._status_of(listing), data={
                "shares": [], "counts": self._empty_counts(), "inbox": self._inbox(0),
                "ignored": [], "truncated": False, "private_text_included": False,
            })
            answer["reason_code"] = str(listing.get("reason_code") or REASON_NO_PROJECTS)
            answer["detail"] = str(listing.get("detail") or "")[:200]
            return answer

        shares: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []
        withheld = 0
        for row in listing.get("projects") or ():
            if not isinstance(row, Mapping):
                continue
            if not is_share_title(row.get("title")):
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            view = self._read_share_artifact(artifact_id)
            if not view["ok"]:
                if str(view.get("status")) in (STATUS_UNAVAILABLE, STATUS_UNKNOWN):
                    # An unreadable owner is not "no shares": report the failure, keep the reason.
                    answer = self._answer(ok=False, status=str(view.get("status")), data={
                        "shares": [], "counts": self._empty_counts(), "inbox": self._inbox(0),
                        "ignored": list(ignored), "truncated": False,
                        "private_text_included": False,
                        "failed_artifact_id": artifact_id,
                    }, reason_code=str(view.get("reason_code") or REASON_PORT_ERROR),
                        detail=str(view.get("detail") or ""))
                    return answer
                ignored.append({"artifact_id": artifact_id,
                                "reason_code": str(view.get("reason_code") or REASON_NOT_A_SHARE)})
                continue
            parsed = view["data"]["_parsed"]
            state, derived = self._effective_state(parsed)
            if wanted is not None and state not in wanted:
                continue
            entry = {
                "share_id": artifact_id,
                "state": state,
                "state_derived": derived,
                "title": share_text_title(row.get("title")),
                "share_revision": parsed["share_revision"],
                "head_version_id": view["data"]["share"]["head_version_id"],
                "updated_at": parsed["updated_at"],
                "lifecycle": str(row.get("lifecycle") or ""),
                "source": {"artifact_id": parsed["source_artifact_id"],
                           "version_id": parsed["source_version_id"]},
                "audience": parsed["audience"],
                "channel": parsed["channel"],
                "has_receipt": bool(parsed["delivery_id"]),
                "delivered": state == STATE_LOCAL_DELIVERED,
                "text_chars": parsed["text_chars"],
            }
            if include_text:
                # Same rule as read_share: a refused/governed source read withholds this row's text.
                source_state = self.source_state_of(parsed)
                readable = self.source_is_readable(source_state)
                entry["text_withheld"] = not readable
                entry["text_available"] = bool(readable)
                entry["source_state"] = str(source_state.get("state") or "")
                entry["source_reason_code"] = str(source_state.get("reason_code") or "")
                entry["private_text_included"] = bool(readable)
                if readable:
                    entry["text"] = parsed["text"]
                else:
                    withheld += 1
            else:
                entry["text_withheld"] = False
                entry["text_available"] = True
                entry["private_text_included"] = False
            shares.append(entry)

        shares.sort(key=lambda item: (str(item.get("updated_at") or ""), item["share_id"]),
                    reverse=True)
        counts = self._empty_counts()
        for entry in shares:
            counts["total"] += 1
            key = str(entry["state"]).lower()
            if key in counts:
                counts[key] += 1
        delivered = counts["local_delivered"]
        visible = shares[:cap]
        projected = sum(1 for entry in visible if entry.get("private_text_included"))
        data = {
            "shares": visible,
            "counts": counts,
            "truncated": len(shares) > cap,
            "ignored": ignored,
            "inbox": self._inbox(delivered),
            # The flag reports the real projection, not the request: no text escaped, it is False.
            "private_text_included": bool(include_text and projected > 0),
            "text_rows_withheld": withheld,
            "withheld_count": int(withheld),
        }
        return self._answer(ok=True, status=STATUS_OK, data=data)

    # -- prepare -------------------------------------------------------------------------

    def prepare_share(
        self,
        *,
        source_artifact_id: Any,
        share_text: Optional[str] = None,
        excerpt_chars: Optional[int] = None,
        title: Optional[str] = None,
        audience: str = AUDIENCE_LOCAL_OWNER,
        channel: str = CHANNEL_LOCAL_INBOX,
        expires_after_s: Optional[int] = None,
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Form one **private** draft bound to a real, readable source artifact and version.

        ``share_text`` is the explicit share text; ``excerpt_chars`` is the explicit bound of a
        prefix of the source's head text.  Passing neither is refused: the whole private material
        is never used as share text by default.  The source's own text never appears in the answer.
        """
        if not isinstance(audience, str) or audience not in LOCAL_AUDIENCES:
            return self._refused(REASON_AUDIENCE_REFUSED,
                                 "audience must be one of %s" % (list(LOCAL_AUDIENCES),))
        if not isinstance(channel, str) or channel not in LOCAL_CHANNELS:
            return self._refused(REASON_CHANNEL_REFUSED,
                                 "channel must be one of %s" % (list(LOCAL_CHANNELS),))
        if share_text is not None and excerpt_chars is not None:
            raise SharingContractRefused("pass either share_text or excerpt_chars, not both")
        source_id = _artifact_id(source_artifact_id, "source_artifact_id")
        explicit_key = _operation_key(operation_key)

        source = self._read_source(source_id)
        if not source["ok"]:
            return source
        project = source["data"]["project"]
        lifecycle = str(project.get("lifecycle") or "")
        if lifecycle and lifecycle != "ACTIVE":
            return self._refused(REASON_SOURCE_ARCHIVED, "the source artifact is not ACTIVE",
                                 source_artifact_id=source_id)
        head_version = project.get("head_version_id")
        if not head_version:
            return self._refused(REASON_SOURCE_UNREADABLE,
                                 "the owner reported no head version for the source",
                                 source_artifact_id=source_id)

        content = project.get("content")
        content_chars = len(content) if isinstance(content, str) else None
        if share_text is not None:
            text = _text(share_text, "share_text", limit=MAX_SHARE_TEXT_CHARS)
            kind = "EXPLICIT_TEXT"
            bound = None
        elif excerpt_chars is not None:
            if isinstance(excerpt_chars, bool) or not isinstance(excerpt_chars, int):
                raise SharingContractRefused("excerpt_chars must be an integer")
            if excerpt_chars < 1 or excerpt_chars > MAX_EXCERPT_CHARS:
                raise SharingContractRefused(
                    "excerpt_chars must be between 1 and %d" % MAX_EXCERPT_CHARS)
            if not isinstance(content, str) or not content.strip():
                return self._refused(REASON_SOURCE_HAS_NO_TEXT,
                                     "the source head has no readable text to excerpt",
                                     source_artifact_id=source_id)
            text = content[:excerpt_chars]
            if not text.strip():
                return self._refused(REASON_SOURCE_HAS_NO_TEXT,
                                     "the bounded excerpt is blank",
                                     source_artifact_id=source_id)
            kind = "SOURCE_PREFIX"
            bound = int(excerpt_chars)
        else:
            return self._refused(REASON_SHARE_TEXT_REQUIRED,
                                 "an explicit share_text or excerpt_chars is required",
                                 source_artifact_id=source_id)

        now = self._now()
        expires_at = None
        if expires_after_s is not None:
            if isinstance(expires_after_s, bool) or not isinstance(expires_after_s, int):
                raise SharingContractRefused("expires_after_s must be an integer")
            if expires_after_s < MIN_EXPIRY_S or expires_after_s > MAX_EXPIRY_S:
                raise SharingContractRefused(
                    "expires_after_s must be between %d and %d" % (MIN_EXPIRY_S, MAX_EXPIRY_S))
            expires_at = _utc_iso(now + expires_after_s)

        stamp = _utc_iso(now)
        share = {
            "state": STATE_DRAFT,
            "source_artifact_id": source_id,
            "source_version_id": str(head_version),
            "source_revision": project.get("revision"),
            "source_content_chars": content_chars,
            "source_title_chars": len(str(project.get("title") or "")),
            "audience": audience,
            "channel": channel,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "share_revision": 1,
            "created_at": stamp,
            "updated_at": stamp,
            "excerpt_kind": kind,
            "excerpt_chars": bound,
            "expires_at": expires_at,
        }
        envelope = encode_share_content(share, text)
        artifact_title = share_title(title, fallback=str(project.get("title") or ""))
        key = explicit_key or sharing_operation_key(
            "prepare", self.subject_id, self.workspace_id, source_id,
            str(head_version), sha256(text.encode("utf-8")).hexdigest()[:16], audience, channel,
            artifact_title)

        try:
            created = self.projects.create_project(title=artifact_title, content=envelope,
                                                   operation_key=key)
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc, operation_key=key)
        settled = self._settle(created, operation_key=key)
        if not settled["ok"]:
            settled["data"] = {"source": self._source_block(share)}
            settled["data"]["excerpt"] = {"kind": kind, "chars": len(text),
                                          "excerpt_chars": bound}
            return settled

        share_id = str(settled["data"].get("share_id") or created.get("artifact_id") or "")
        if not share_id:
            return self._answer(ok=False, status=STATUS_UNKNOWN, data={
                "operation_key": key,
                "source": self._source_block(share),
            }, reason_code=REASON_RESOLUTION_INCONCLUSIVE,
                detail="the owner applied the draft but reported no artifact id")
        view = self._read_share_artifact(share_id)
        if not view["ok"]:
            answer = self._answer(ok=False, status=view.get("status") or STATUS_UNAVAILABLE,
                                  data={"share_id": share_id, "operation_key": key,
                                        "source": self._source_block(share)},
                                  reason_code=str(view.get("reason_code") or REASON_PORT_ERROR),
                                  detail=str(view.get("detail") or ""))
            answer["operation_key"] = key
            return answer
        parsed = view["data"]["_parsed"]
        data = {
            "share_id": share_id,
            "state": parsed["state"],
            "head_version_id": view["data"]["share"]["head_version_id"],
            "share_revision": parsed["share_revision"],
            "artifact_revision": view["data"]["share"]["artifact_revision"],
            "title": share_text_title(artifact_title),
            "audience": parsed["audience"],
            "channel": parsed["channel"],
            "source": self._source_block(parsed),
            "text": parsed["text"],
            "text_chars": parsed["text_chars"],
            "excerpt": {"kind": parsed["excerpt_kind"], "chars": parsed["text_chars"],
                        "excerpt_chars": parsed["excerpt_chars"]},
            "expires_at": parsed["expires_at"],
            "created_at": parsed["created_at"],
            "updated_at": parsed["updated_at"],
            "private_text_included": True,
        }
        answer = self._answer(ok=bool(settled["ok"]), status=settled["status"], data=data)
        answer["share_id"] = share_id
        answer["state"] = parsed["state"]
        answer["operation_key"] = key
        answer["reconciled"] = bool(settled.get("reconciled"))
        return answer

    # -- act -----------------------------------------------------------------------------

    def apply_action(
        self,
        *,
        share_id: Any,
        action: str,
        expected_head: Any,
        operation_key: Optional[str] = None,
        defer_condition: Optional[str] = None,
        opportunity_ref: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """One explicit action on a draft: ``send_local`` / ``defer`` / ``drop`` / ``expire``.

        ``expected_head`` is the draft version the caller read (``read_share`` ->
        ``share.head_version_id``): the owner's own compare-and-swap precondition.  Nothing is
        written when the state does not allow the action, when the source revision moved, or when
        the audience/channel in the artifact is not one of the fixed local values.
        """
        if not isinstance(action, str) or action not in SHARE_ACTIONS:
            raise SharingContractRefused(
                "action must be one of %s" % (list(SHARE_ACTIONS),))
        if not isinstance(expected_head, str) or not expected_head.strip():
            raise SharingContractRefused(
                "expected_head is required: pass the head_version_id you read before deciding")
        head = expected_head.strip()
        target = _artifact_id(share_id)
        explicit_key = _operation_key(operation_key)

        view = self._read_share_artifact(target)
        if not view["ok"]:
            return view
        parsed = dict(view["data"]["_parsed"])
        head_now = view["data"]["share"]["head_version_id"]
        state, derived = self._effective_state(parsed)

        # Fixed audience/channel: an artifact whose envelope was rewritten to anything else is not
        # sendable, even if the CAS matches.
        if parsed["audience"] not in LOCAL_AUDIENCES:
            return self._refused(REASON_AUDIENCE_REFUSED,
                                 "the draft's audience is not a fixed local identity",
                                 share_id=target, state=state)
        if parsed["channel"] not in LOCAL_CHANNELS:
            return self._refused(REASON_CHANNEL_REFUSED,
                                 "the draft's channel is not the local inbox",
                                 share_id=target, state=state)

        persisted = str(parsed["state"])
        blocked = self._state_gate(action, state, persisted, parsed)
        if blocked is not None:
            blocked["share_id"] = target
            blocked["state"] = state
            return blocked

        if action == ACTION_DEFER:
            condition = _one_line(defer_condition, "defer_condition",
                                  limit=MAX_CONDITION_CHARS)
            opportunity = _one_line(opportunity_ref, "opportunity_ref",
                                    limit=MAX_CONDITION_CHARS)
            if not condition and not opportunity:
                return self._refused(REASON_DEFER_CONDITION_REQUIRED,
                                     "defer needs an explicit defer_condition or opportunity_ref",
                                     share_id=target, state=state)
        else:
            condition = ""
            opportunity = ""

        source_check: Optional[dict[str, Any]] = None
        if action == ACTION_SEND_LOCAL:
            source_check = self._source_current(parsed)
            if not source_check["readable"]:
                return self._answer(ok=False, status=STATUS_STALE_SOURCE, data={
                    "share_id": target, "state": state, "source_current": source_check,
                }, reason_code=str(source_check["reason_code"] or REASON_SOURCE_UNREADABLE),
                    detail="the source artifact cannot be read now; prepare the share again")
            if not source_check["matches_recorded"]:
                return self._answer(ok=False, status=STATUS_STALE_SOURCE, data={
                    "share_id": target, "state": state, "source_current": source_check,
                }, reason_code=str(source_check["reason_code"]
                                   or REASON_SOURCE_REVISION_CHANGED),
                    detail="the source moved since the draft was formed; prepare it again")

        # No local pre-check of ``expected_head``: the owner's own compare-and-swap is the
        # authority, and a replay of the same operation_key must be able to answer
        # ``already_applied`` instead of a manufactured conflict.  ``head_now`` is only echoed back
        # so a real conflict is actionable.

        now = self._now()
        stamp = _utc_iso(now)
        reason_text = _one_line(reason, "reason", limit=MAX_REASON_CHARS)
        updated = dict(parsed)
        updated["share_revision"] = int(parsed["share_revision"] or 0) + 1
        updated["updated_at"] = stamp
        new_state = state
        if action == ACTION_SEND_LOCAL:
            new_state = STATE_LOCAL_DELIVERED
        elif action == ACTION_DEFER:
            new_state = STATE_DEFERRED
            updated["defer_condition"] = condition or None
            updated["opportunity_ref"] = opportunity or None
            updated["deferred_at"] = stamp
        elif action == ACTION_DROP:
            new_state = STATE_DROPPED
            updated["dropped_reason"] = reason_text or None
            updated["dropped_at"] = stamp
        elif action == ACTION_EXPIRE:
            new_state = STATE_EXPIRED
            updated["expired_reason"] = reason_text or None
            updated["expired_at"] = stamp
        updated["state"] = new_state

        extra = condition or opportunity or reason_text
        key = explicit_key or sharing_operation_key(action, target, head, extra,
                                                    parsed["channel"], parsed["audience"])
        if action == ACTION_SEND_LOCAL:
            delivery_id = "localdelivery:" + sha256(
                ("%s\x1f%s" % (target, key)).encode("utf-8")).hexdigest()[:16]
            updated["delivery_id"] = delivery_id
            updated["delivered_at"] = stamp

        envelope = encode_share_content(updated, parsed["text"])
        try:
            committed = self.projects.commit_project(
                artifact_id=target, content=envelope, expected_head=head, operation_key=key)
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc, share_id=target, operation_key=key)
        settled = self._settle(committed, operation_key=key)
        if not settled["ok"]:
            failure: dict[str, Any] = {"share_id": target, "action": action,
                                       "previous_state": state}
            if str(settled.get("status")) == STATUS_CONFLICT:
                failure["current_head_version_id"] = head_now
                settled["reason_code"] = str(settled.get("reason_code") or REASON_CAS_CONFLICT)
            settled["data"] = dict(settled.get("data") or {}, **failure)
            settled["share_id"] = target
            settled["state"] = state
            return settled

        after = self._read_share_artifact(target)
        if not after["ok"]:
            return self._answer(ok=False, status=STATUS_UNKNOWN, data={
                "share_id": target, "action": action, "operation_key": key,
                "previous_state": state,
            }, reason_code=REASON_RESOLUTION_INCONCLUSIVE,
                detail="the owner answered but the new state could not be read back")
        final = dict(after["data"]["_parsed"])
        final_state, final_derived = self._effective_state(final)
        if final_state != new_state:
            answer = self._answer(ok=False, status=STATUS_UNKNOWN, data={
                "share_id": target, "action": action, "operation_key": key,
                "previous_state": state, "state": final_state,
                "head_version_id": after["data"]["share"]["head_version_id"],
            }, reason_code=REASON_RESOLUTION_INCONCLUSIVE,
                detail="the owner answered %s but the share now reads %s"
                       % (settled["status"], final_state))
            answer["share_id"] = target
            answer["state"] = final_state
            answer["operation_key"] = key
            answer["reconciled"] = bool(settled.get("reconciled"))
            return answer

        delivered_now = final_state == STATE_LOCAL_DELIVERED
        receipt = self._receipt(final) if delivered_now else None
        # The delivery block is read back from the stored envelope, never from what this call
        # *intended*: a replayed key must report the one real delivery with its real timestamp.
        delivery = (self._delivery_block(str(final["delivery_id"]), str(final["delivered_at"]),
                                         final) if delivered_now else None)
        data = {
            "share_id": target,
            "action": action,
            "state": final_state,
            "previous_state": state,
            "state_derived": final_derived,
            "head_version_id": after["data"]["share"]["head_version_id"],
            "share_revision": final["share_revision"],
            "artifact_revision": after["data"]["share"]["artifact_revision"],
            "operation_key": key,
            "reconciled": bool(settled.get("reconciled")),
            "deferred": ({"condition": final["defer_condition"],
                          "opportunity_ref": final["opportunity_ref"],
                          "deferred_at": final["deferred_at"]}
                         if final_state == STATE_DEFERRED else None),
            "dropped": ({"reason": final["dropped_reason"], "dropped_at": final["dropped_at"]}
                        if final_state == STATE_DROPPED else None),
            "expired": ({"reason": final["expired_reason"], "expired_at": final["expired_at"]}
                        if final_state == STATE_EXPIRED else None),
            "receipt": receipt,
            "delivery": delivery,
            "delivery_count": 1 if final_state == STATE_LOCAL_DELIVERED else 0,
        }
        answer = self._answer(ok=True, status=settled["status"], data=data)
        answer["share_id"] = target
        answer["state"] = final_state
        answer["operation_key"] = key
        answer["reconciled"] = bool(settled.get("reconciled"))
        return answer

    # -- state machine -------------------------------------------------------------------

    def _state_gate(self, action: str, state: str, persisted: str,
                    parsed: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """The refusal for a disallowed transition, or ``None`` when the action may proceed.

        ``state`` is the state to report (an expiry window may have made it ``EXPIRED`` without
        anything being written); ``persisted`` is the state actually stored in the head version.
        The two differ only for a derived expiry, and that is exactly the case where an explicit
        ``expire`` is still allowed: it turns the derived answer into a written fact.
        """
        if action == ACTION_SEND_LOCAL:
            if state == STATE_LOCAL_DELIVERED:
                return self._refused(REASON_ALREADY_DELIVERED,
                                     "this share was already delivered to the local inbox",
                                     receipt=self._receipt(parsed))
            if state == STATE_DROPPED:
                return self._refused(REASON_TERMINAL_STATE,
                                     "a dropped share stays dropped: there is no revival")
            if state == STATE_EXPIRED:
                if persisted == STATE_EXPIRED:
                    return self._refused(REASON_TERMINAL_STATE,
                                         "an expired share is terminal: there is no revival")
                return self._refused(REASON_EXPIRED, "this share has expired")
            if state not in SENDABLE_STATES:
                return self._refused(REASON_STATE_NOT_ALLOWED,
                                     "state %s cannot be sent" % state)
            return None
        if action == ACTION_DEFER:
            if state in TERMINAL_STATES:
                return self._refused(REASON_TERMINAL_STATE,
                                     "a %s share is terminal" % state.lower())
            if persisted != STATE_DRAFT:
                return self._refused(REASON_STATE_NOT_ALLOWED,
                                     "state %s cannot be deferred" % state)
            return None
        if action == ACTION_DROP:
            if persisted in TERMINAL_STATES:
                return self._refused(REASON_TERMINAL_STATE,
                                     "a %s share is terminal" % persisted.lower())
            if state == STATE_LOCAL_DELIVERED:
                return self._refused(REASON_STATE_NOT_ALLOWED,
                                     "an already delivered share cannot be dropped")
            return None
        if action == ACTION_EXPIRE:
            if persisted in TERMINAL_STATES:
                return self._refused(REASON_TERMINAL_STATE,
                                     "a %s share is terminal" % persisted.lower())
            if state == STATE_LOCAL_DELIVERED:
                return self._refused(REASON_STATE_NOT_ALLOWED,
                                     "an already delivered share cannot be expired")
            return None
        raise SharingContractRefused("unknown action: %r" % (action,))

    def _effective_state(self, parsed: Mapping[str, Any]) -> tuple[str, bool]:
        """The state to report, and whether it was derived from the clock rather than written."""
        state = str(parsed.get("state") or "")
        if state in SENDABLE_STATES:
            expires = _epoch_of(parsed.get("expires_at"))
            if expires is not None and self._now() >= expires:
                return STATE_EXPIRED, True
        return state, False

    # -- owner reads ---------------------------------------------------------------------

    def _read_share_artifact(self, artifact_id: str) -> dict[str, Any]:
        """Read one artifact from the owner and parse its envelope.  Never leaks a foreign body."""
        try:
            answer = self.projects.read_project(artifact_id, include_history=False)
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc, share_id=artifact_id)
        if not isinstance(answer, Mapping):
            return self._unavailable(REASON_PORT_ERROR,
                                     TypeError("read_project answered no dict"),
                                     share_id=artifact_id)
        if not answer.get("ok"):
            status = self._status_of(answer)
            out = self._answer(ok=False, status=status, data={"share_id": artifact_id},
                               reason_code=str(answer.get("reason_code") or REASON_SHARE_NOT_FOUND),
                               detail=str(answer.get("detail") or "")[:200])
            out["share_id"] = artifact_id
            return out
        project = dict(answer.get("project") or {})
        content = project.get("content")
        parsed = parse_share_content(content)
        if parsed is None:
            return self._answer(ok=False, status=STATUS_REJECTED,
                                data={"share_id": artifact_id},
                                reason_code=REASON_NOT_A_SHARE,
                                detail="this artifact is not a v1 share envelope")
        share = {
            "share_id": artifact_id,
            "state": parsed["state"],
            "state_derived": False,
            "title": share_text_title(project.get("title")),
            "audience": parsed["audience"],
            "channel": parsed["channel"],
            "head_version_id": project.get("head_version_id"),
            "version_number": project.get("version_number"),
            "share_revision": parsed["share_revision"],
            "artifact_revision": project.get("revision"),
            "lifecycle": str(project.get("lifecycle") or ""),
            "created_at": parsed["created_at"],
            "updated_at": parsed["updated_at"],
            "expires_at": parsed["expires_at"],
            "source": self._source_block(parsed),
            "text": parsed["text"],
            "text_chars": parsed["text_chars"],
            "deferred": ({"condition": parsed["defer_condition"],
                          "opportunity_ref": parsed["opportunity_ref"],
                          "deferred_at": parsed["deferred_at"]}
                         if parsed["state"] == STATE_DEFERRED else None),
            "dropped": ({"reason": parsed["dropped_reason"], "dropped_at": parsed["dropped_at"]}
                        if parsed["state"] == STATE_DROPPED else None),
            "expired": ({"reason": parsed["expired_reason"], "expired_at": parsed["expired_at"]}
                        if parsed["state"] == STATE_EXPIRED else None),
            "private_text_included": True,
        }
        return self._answer(ok=True, status=STATUS_OK,
                            data={"share": share, "_parsed": parsed})

    def _read_source(self, artifact_id: str) -> dict[str, Any]:
        """Read one source artifact through the same owner.  Only its row/metadata is returned."""
        try:
            answer = self.projects.read_project(artifact_id, include_history=False)
        except Exception as exc:
            return self._unavailable(REASON_PORT_ERROR, exc, source_artifact_id=artifact_id)
        if not isinstance(answer, Mapping):
            return self._unavailable(REASON_PORT_ERROR,
                                     TypeError("read_project answered no dict"),
                                     source_artifact_id=artifact_id)
        if not answer.get("ok"):
            # Same rule as the read/list projection: a refusal is named, not turned into "moved".
            status = self._status_of(answer)
            owner_reason = str(answer.get("reason_code") or "")
            state, reason, governed = self._source_refusal(status, owner_reason)
            return self._answer(ok=False, status=STATUS_REJECTED,
                                data={"source_artifact_id": artifact_id,
                                      "source_state": state,
                                      "governed": bool(governed),
                                      "owner_reason_code": owner_reason[:64],
                                      "owner_status": status},
                                reason_code=reason,
                                detail=(owner_reason or str(answer.get("detail") or ""))[:200])
        project = dict(answer.get("project") or {})
        if not project:
            return self._answer(ok=False, status=STATUS_REJECTED,
                                data={"source_artifact_id": artifact_id},
                                reason_code=REASON_SOURCE_NOT_FOUND,
                                detail="the owner reported no such artifact")
        return self._answer(ok=True, status=STATUS_OK, data={"project": project})

    def _source_refusal(self, status: str, owner_reason: str) -> tuple[str, str, bool]:
        """Name one refused source read: (state, reason_code, governed).

        A **definite refusal** by the owner (``rejected`` and friends) is ``GOVERNED`` and carries the
        owner's own reason code (``SOURCE_GOVERNED`` in the parent's probe) -- it is *not* a moved
        source and *not* a deleted one.  ``not_found`` stays ``NOT_FOUND`` and an unreachable owner
        stays ``UNAVAILABLE``; both keep this service's stable reason codes and put the owner's own
        code in ``owner_reason_code``.
        """
        named = str(owner_reason or "").strip().upper()[:64]
        if status == STATUS_NOT_FOUND:
            return SOURCE_STATE_NOT_FOUND, REASON_SOURCE_NOT_FOUND, False
        if status == STATUS_UNAVAILABLE:
            return SOURCE_STATE_UNAVAILABLE, REASON_SOURCE_UNREADABLE, False
        return SOURCE_STATE_GOVERNED, named or REASON_SOURCE_GOVERNED, True

    def source_state_of(self, parsed_or_share: Mapping[str, Any]) -> dict[str, Any]:
        """What the recorded source looks like right now.  Never returns the source's text.

        The same rule decides three things: whether the bounded share text may be projected
        (``source_is_readable``), whether the source merely moved / was archived, and whether the
        read was *refused* by the owner.  ``state`` is the one name for all of it.
        """
        block = {
            "state": SOURCE_STATE_UNAVAILABLE,
            "readable": False,
            "governed": False,
            "lifecycle": "",
            "head_version_id": None,
            "revision": None,
            "matches_recorded": False,
            "stale": True,
            "reason_code": REASON_SOURCE_UNREADABLE,
            "owner_status": STATUS_UNAVAILABLE,
            "owner_reason_code": "",
        }
        source_id = str(parsed_or_share.get("source_artifact_id")
                        or parsed_or_share.get("source_id") or "")
        if not source_id:
            block.update({"state": SOURCE_STATE_NOT_FOUND,
                          "reason_code": REASON_SOURCE_NOT_FOUND,
                          "owner_status": STATUS_NOT_FOUND})
            return block
        try:
            answer = self.projects.read_project(source_id, include_history=False)
        except Exception as exc:
            block.update({"owner_status": STATUS_UNAVAILABLE,
                          "owner_reason_code": type(exc).__name__[:64]})
            return block
        if not isinstance(answer, Mapping):
            block.update({"owner_status": STATUS_UNAVAILABLE,
                          "owner_reason_code": "PROJECT_READ_NO_ANSWER"})
            return block
        if not answer.get("ok"):
            status = self._status_of(answer)
            owner_reason = str(answer.get("reason_code") or "")
            state, reason, governed = self._source_refusal(status, owner_reason)
            block.update({"state": state, "reason_code": reason, "governed": governed,
                          "owner_status": status, "owner_reason_code": owner_reason[:64]})
            return block
        project = dict(answer.get("project") or {})
        lifecycle = str(project.get("lifecycle") or "")
        head = project.get("head_version_id")
        matches = (str(head or "") == str(parsed_or_share.get("source_version_id") or "")
                   and lifecycle in ("", "ACTIVE"))
        block.update({
            "state": SOURCE_STATE_READABLE,
            "readable": True,
            "governed": False,
            "lifecycle": lifecycle,
            "head_version_id": head,
            "revision": project.get("revision"),
            "matches_recorded": matches,
            "stale": False,
            "reason_code": "",
            "owner_status": STATUS_OK,
            "owner_reason_code": "",
        })
        if lifecycle and lifecycle != "ACTIVE":
            block.update({"state": SOURCE_STATE_ARCHIVED, "matches_recorded": False,
                          "stale": True, "reason_code": REASON_SOURCE_ARCHIVED})
        elif not matches:
            block.update({"state": SOURCE_STATE_REVISION_CHANGED, "stale": True,
                          "reason_code": REASON_SOURCE_REVISION_CHANGED})
        return block

    #: The older private name, kept so nothing else in this module drifts.
    _source_current = source_state_of

    @staticmethod
    def source_is_readable(source_state: Mapping[str, Any]) -> bool:
        """True only when the owner actually answered the source read."""
        return str(source_state.get("state") or "") in READABLE_SOURCE_STATES \
            and bool(source_state.get("readable"))

    # -- answer blocks -------------------------------------------------------------------

    @staticmethod
    def _source_block(parsed: Mapping[str, Any]) -> dict[str, Any]:
        """The recorded source binding: identifiers and lengths only, never its text."""
        return {
            "artifact_id": str(parsed.get("source_artifact_id") or ""),
            "version_id": parsed.get("source_version_id"),
            "revision": parsed.get("source_revision"),
            "content_chars": parsed.get("source_content_chars"),
            "title_chars": parsed.get("source_title_chars"),
        }

    def _receipt(self, parsed: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        delivery_id = parsed.get("delivery_id")
        if not delivery_id:
            return None
        return {
            "delivery_id": delivery_id,
            "delivered_at": parsed.get("delivered_at"),
            "channel": parsed.get("channel"),
            "audience": parsed.get("audience"),
            "workspace_id": parsed.get("workspace_id"),
            "subject_id": parsed.get("subject_id"),
            "recipient": parsed.get("audience"),
            "local_only": True,
            "external_channel_sent": False,
            "external_delivery_confirmed": False,
            "read_by_audience": False,
            "acknowledged": False,
            "understood": False,
            "note": "local inbox record only; not a real external send/delivery/read",
        }

    def _delivery_block(self, delivery_id: str, delivered_at: str,
                        parsed: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "delivery_id": delivery_id,
            "delivered_at": delivered_at,
            "channel": parsed.get("channel"),
            "recipient": {
                "audience": parsed.get("audience"),
                "workspace_id": parsed.get("workspace_id"),
                "subject_id": parsed.get("subject_id"),
            },
            "local_only": True,
            "external_channel_sent": False,
        }

    @staticmethod
    def _inbox(delivered: int) -> dict[str, Any]:
        return {
            "channel": CHANNEL_LOCAL_INBOX,
            "audience": AUDIENCE_LOCAL_OWNER,
            "delivered_count": int(delivered),
            "local_only": True,
            "external_channel_sent": False,
        }

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        counts = {"total": 0}
        for state in SHARE_STATES:
            counts[state.lower()] = 0
        return counts

    # -- status mapping ------------------------------------------------------------------

    @staticmethod
    def _status_of(answer: Any) -> str:
        if not isinstance(answer, Mapping):
            return STATUS_UNAVAILABLE
        status = str(answer.get("status") or "")
        if status == _projects_owner.STATUS_OK:
            return STATUS_OK
        if status in (_projects_owner.STATUS_APPLIED, _projects_owner.STATUS_ALREADY_APPLIED):
            return str(status)
        mapping = {
            _projects_owner.STATUS_NOT_FOUND: STATUS_NOT_FOUND,
            _projects_owner.STATUS_CONFLICT: STATUS_CONFLICT,
            _projects_owner.STATUS_REJECTED: STATUS_REJECTED,
            _projects_owner.STATUS_UNKNOWN: STATUS_UNKNOWN,
            _projects_owner.STATUS_UNAVAILABLE: STATUS_UNAVAILABLE,
        }
        return mapping.get(status, STATUS_UNAVAILABLE)

    def _settle(self, answer: Any, *, operation_key: str) -> dict[str, Any]:
        """Classify one owner write answer; an UNKNOWN is only reported, never retried here."""
        status = self._status_of(answer)
        data: dict[str, Any] = {
            "share_id": str((answer or {}).get("artifact_id") or "") or None,
            "operation_key": operation_key,
        }
        if status in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            out = self._answer(ok=True, status=status, data=data)
            out["operation_key"] = operation_key
            out["reconciled"] = bool((answer or {}).get("reconciled"))
            return out
        out = self._answer(ok=False, status=status, data=data,
                           reason_code=str((answer or {}).get("reason_code")
                                           or (REASON_CAS_CONFLICT if status == STATUS_CONFLICT
                                               else "")),
                           detail=str((answer or {}).get("detail") or "")[:200])
        out["operation_key"] = operation_key
        out["reconciled"] = bool((answer or {}).get("reconciled"))
        if status == STATUS_UNKNOWN:
            out["retryable"] = bool((answer or {}).get("retryable"))
            if not out["reason_code"]:
                out["reason_code"] = (REASON_OPERATION_NEVER_LANDED if out["retryable"]
                                      else REASON_RESOLUTION_INCONCLUSIVE)
        return out

    def _refused(self, reason_code: str, detail: str, **data: Any) -> dict[str, Any]:
        return self._answer(ok=False, status=STATUS_REJECTED, data=dict(data),
                            reason_code=reason_code, detail=detail[:200])

    def _unavailable(self, reason_code: str, exc: Exception, **extra: Any) -> dict[str, Any]:
        data = dict(extra)
        answer = self._answer(ok=False, status=STATUS_UNAVAILABLE, data=data,
                              reason_code=reason_code, detail=type(exc).__name__)
        for key, value in extra.items():
            if key in ("share_id", "operation_key", "source_artifact_id"):
                answer[key] = value
        return answer

    @staticmethod
    def _answer(*, ok: bool, status: str, data: Optional[dict[str, Any]] = None,
                reason_code: str = "", detail: str = "") -> dict[str, Any]:
        return {
            "ok": bool(ok),
            "status": str(status),
            "data": dict(data or {}),
            "source": SOURCE_OWNER,
            "reason_code": str(reason_code or "")[:64],
            "detail": str(detail or "")[:200],
        }


# --------------------------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------------------------

def _first_env(environment: Mapping[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = environment.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def sharing_from_env(environment: Optional[Mapping[str, str]] = None) -> LifeSharingService:
    """Build the service from the environment a Life runtime already carries.

    Same names ``private_project_service`` already speaks (``NYAIRO_*`` / ``CHIYO_*`` /
    ``LIFE_SUPPLY_*``), and the same owner: this factory calls
    ``private_project_service.life_supply_port_from_env`` and builds the real
    ``PrivateProjectService`` -- it does not re-implement either, and it keeps no config of its own.
    A half-wired environment raises ``SharingUnavailable`` instead of becoming a half-working owner.
    """
    env = dict(environment if environment is not None else {})
    workspace_id = _first_env(env, _projects_owner.ENV_WORKSPACE_NAMES)
    grant_id = _first_env(env, _projects_owner.ENV_GRANT_NAMES)
    if not workspace_id:
        raise SharingUnavailable("WORKSPACE_NOT_CONFIGURED")
    if not grant_id:
        raise SharingUnavailable("ARTIFACT_GRANT_NOT_CONFIGURED")
    try:
        port = _projects_owner.life_supply_port_from_env(env)
    except _projects_owner.ProjectUnavailable as exc:
        # The projects owner's own refusal, forwarded under this service's one exception type:
        # A only has to tell "not wired" from "wired and answered".
        raise SharingUnavailable(str(exc) or "SUPPLY_PORT_UNAVAILABLE") from exc
    excluded_raw = _first_env(env, _projects_owner.ENV_EXCLUDED_NAMES) or ""
    excluded = _projects_owner.default_excluded_artifact_ids(*excluded_raw.split(","))
    projects = _projects_owner.PrivateProjectService(port, workspace_id, grant_id,
                                                    excluded_artifact_ids=excluded)
    return LifeSharingService(projects)


#: The other names a resolver may look for.
service_from_env = sharing_from_env
from_env = sharing_from_env


__all__ = [
    "ACTION_DEFER",
    "ACTION_DROP",
    "ACTION_EXPIRE",
    "ACTION_SEND_LOCAL",
    "AUDIENCE_LOCAL_OWNER",
    "CHANNEL_LOCAL_INBOX",
    "LifeSharingService",
    "LOCAL_AUDIENCES",
    "LOCAL_CHANNELS",
    "MAX_EXCERPT_CHARS",
    "MAX_EXPIRY_S",
    "MAX_LIST",
    "MAX_SHARE_TEXT_CHARS",
    "MAX_TITLE_CHARS",
    "MIN_EXPIRY_S",
    "REASON_ALREADY_DELIVERED",
    "REASON_AUDIENCE_REFUSED",
    "REASON_CAS_CONFLICT",
    "REASON_CHANNEL_REFUSED",
    "REASON_DEFER_CONDITION_REQUIRED",
    "REASON_EXPIRED",
    "REASON_NOT_A_SHARE",
    "REASON_OPERATION_NEVER_LANDED",
    "REASON_PORT_ERROR",
    "REASON_RESOLUTION_INCONCLUSIVE",
    "REASON_SHARE_NOT_FOUND",
    "REASON_SHARE_TEXT_REQUIRED",
    "REASON_SOURCE_ARCHIVED",
    "REASON_SOURCE_HAS_NO_TEXT",
    "REASON_SOURCE_GOVERNED",
    "REASON_SOURCE_NOT_FOUND",
    "REASON_SOURCE_REVISION_CHANGED",
    "REASON_SOURCE_UNREADABLE",
    "REASON_STATE_NOT_ALLOWED",
    "REASON_TERMINAL_STATE",
    "REQUIRED_PROJECT_METHODS",
    "SHARE_ACTIONS",
    "SHARE_SCHEMA",
    "SHARE_STATES",
    "READABLE_SOURCE_STATES",
    "SOURCE_OWNER",
    "SOURCE_STATE_ARCHIVED",
    "SOURCE_STATE_GOVERNED",
    "SOURCE_STATE_NOT_FOUND",
    "SOURCE_STATE_READABLE",
    "SOURCE_STATE_REVISION_CHANGED",
    "SOURCE_STATE_UNAVAILABLE",
    "SOURCE_STATES",
    "STATUS_ALREADY_APPLIED",
    "STATUS_APPLIED",
    "STATUS_CONFLICT",
    "STATUS_NOT_FOUND",
    "STATUS_OK",
    "STATUS_REJECTED",
    "STATUS_STALE_SOURCE",
    "STATUS_UNAVAILABLE",
    "STATUS_UNKNOWN",
    "STATE_DEFERRED",
    "STATE_DRAFT",
    "STATE_DROPPED",
    "STATE_EXPIRED",
    "STATE_LOCAL_DELIVERED",
    "SharingContractRefused",
    "SharingError",
    "SharingUnavailable",
    "TITLE_PREFIX",
    "encode_share_content",
    "from_env",
    "is_share_title",
    "parse_share_content",
    "service_from_env",
    "share_text_title",
    "share_title",
    "sharing_from_env",
    "sharing_operation_key",
]
