#!/usr/bin/env python3
"""F-140: the instance's own participant registry -- who a local arrival belongs to.

Why this module exists
----------------------
The local visit inbox (``life_local_contact``) is a **transport** ledger: it records that an
arrival happened, but it deliberately carries no sender identity and owns no relationship.  The
phone therefore could list "someone came by" and could not answer "who".  ``components/native``'s
``ParticipantResolver`` is a Telegram single-channel *configuration* file: the id is supplied by
hand, there are no aliases, no endpoints, no creation source and no link to an inbox event.  It
cannot be the instance-domain registry F-140 needs.

This module is that registry, and nothing more:

* **participants**: one owner-generated opaque ``participant:<hex>`` id, a display name, free
  aliases (same names are allowed and are never merged), the owner's ``created_at``, and the
  controlled creation source.  A name is display metadata, never an instruction and never a key.
* **endpoints**: a stable ``(provider, account_key)`` binding to one participant, created only by
  an explicit local operator action.  One endpoint can never silently point at two people; a
  second channel is a second explicit endpoint for the same participant.  An endpoint inherits
  **no** grant, capability, permission or authority -- the participant is identity, not power.
* **event links**: ``event_ref -> participant`` (optionally a named endpoint), recorded only by
  an explicit operator action after the event is verified to exist in *this* instance's real
  inbox.  An arrival nobody linked stays ``UNRESOLVED``; nothing is inferred from a display name,
  a ``source_ref`` or a weak sender label.
* **operations**: one idempotency key per intent.  The same key with the same payload replays the
  recorded answer; the same key with a different payload is refused.  Writes are serialised by
  SQLite (``BEGIN IMMEDIATE`` + busy timeout); the loser gets a named refusal, never
  "last writer wins".

What this module deliberately is not
------------------------------------
* not a message store: message bodies stay in ``life_local_contact`` and are never copied here;
* not a relationship / trust / intimacy / priority owner, and not an authority owner;
* not a lookup service for external accounts: endpoints are local operator assertions only;
* not an HTTP surface: the only write entry is the local operator CLI, and the phone reads it.

Reads are pure.  A missing database is ``ABSENT`` (a legitimate fresh instance).  A database that
exists but cannot be read as this schema is a *named* ``PARTICIPANT_STATE_*`` error, never an
empty list, so "the registry cannot be read" can never look like "nobody is known".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

SCHEMA_VERSION = "chiyo.life.participant_registry.v1"
DB_FILENAME = "participant_registry.sqlite3"

#: The only creation source this owner records.  A caller cannot claim ``system`` / ``trusted``.
SOURCE_OPERATOR_CLI = "operator:cli"
CREATED_BY_OPERATOR = "operator:cli"
ALLOWED_SOURCES = frozenset({SOURCE_OPERATOR_CLI})

PARTICIPANT_PREFIX = "participant"
ENDPOINT_PREFIX = "endpoint"

#: Bounds.  Every one of them refuses by name instead of truncating silently.
MAX_DISPLAY_NAME = 64
MAX_ALIASES = 16
MAX_ACCOUNT_KEY = 200
MAX_REF = 200
MAX_LIST = 50
MAX_LINKED_EVENT_REFS = 20
BUSY_TIMEOUT_MS = 5000

STATUS_OK = "ok"
STATUS_APPLIED = "applied"
STATUS_ALREADY = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_ABSENT = "absent"
STATUS_UNREADABLE = "unreadable"
STATUS_CONFLICT = "conflict"

_ID_RE = re.compile(r"^(participant|endpoint):[0-9a-f]{24}$")
_OPERATION_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:\-]{1,127}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{1,31}:[^\s]{1,200}$")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_.\-]{1,31}$")

#: The closed SQL schema.  ``operations`` is the idempotency ledger, not a second event source.
#: Statements are executed one by one: ``executescript`` would implicitly commit the open
#: ``BEGIN IMMEDIATE`` and turn schema creation into a second, unguarded transaction.
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS participants (
    participant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    aliases TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    source TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS endpoints (
    endpoint_id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL REFERENCES participants(participant_id),
    provider TEXT NOT NULL,
    account_key TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    UNIQUE(provider, account_key)
)""",
    """CREATE TABLE IF NOT EXISTS event_links (
    event_ref TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL REFERENCES participants(participant_id),
    endpoint_id TEXT REFERENCES endpoints(endpoint_id),
    link_source TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    linked_by TEXT NOT NULL,
    operation_key TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS operations (
    operation_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    ref TEXT NOT NULL,
    created_at TEXT NOT NULL
)""",
)


class ParticipantRegistryError(RuntimeError):
    """A named refusal.  ``reason_code`` is always a ``PARTICIPANT_*`` code."""

    def __init__(self, reason_code: str, detail: str = "", **extra: Any) -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        self.extra = dict(extra)


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds")


def _now_from(clock: Optional[Callable[[], Any]]) -> str:
    if clock is None:
        return _iso()
    try:
        raw = clock()
    except Exception:  # pragma: no cover - a broken clock must not fabricate a time
        return _iso()
    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return _iso()
    else:
        return _iso()
    return _iso(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(kind: str, payload: Mapping[str, Any]) -> str:
    return "sha256:" + _sha(json.dumps({"kind": kind, "payload": dict(payload)},
                                       ensure_ascii=False, sort_keys=True))


def _clean_name(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ParticipantRegistryError("PARTICIPANT_%s_REQUIRED" % field.upper())
    text = " ".join(value.split())
    if not text:
        raise ParticipantRegistryError("PARTICIPANT_%s_REQUIRED" % field.upper())
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ParticipantRegistryError("PARTICIPANT_%s_INVALID" % field.upper())
    if len(text) > MAX_DISPLAY_NAME:
        raise ParticipantRegistryError("PARTICIPANT_%s_TOO_LONG" % field.upper(),
                                       "%d > %d" % (len(text), MAX_DISPLAY_NAME))
    return text


def _aliases(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ParticipantRegistryError("PARTICIPANT_ALIASES_INVALID")
    cleaned: list[str] = []
    for item in value:
        text = _clean_name(item, "alias")
        if text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > MAX_ALIASES:
        raise ParticipantRegistryError("PARTICIPANT_ALIASES_TOO_MANY", str(len(cleaned)))
    return tuple(cleaned)


def _operation_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ParticipantRegistryError("PARTICIPANT_OPERATION_KEY_REQUIRED")
    if not _OPERATION_KEY_RE.match(text):
        raise ParticipantRegistryError("PARTICIPANT_OPERATION_KEY_INVALID", text[:80])
    return text


def _ref(value: Any, field: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ParticipantRegistryError("PARTICIPANT_%s_REQUIRED" % field.upper())
        return ""
    if len(text) > MAX_REF:
        raise ParticipantRegistryError("PARTICIPANT_%s_TOO_LONG" % field.upper())
    if not _REF_RE.match(text):
        raise ParticipantRegistryError("PARTICIPANT_%s_NOT_A_STRUCTURED_REF" % field.upper(),
                                       text[:80])
    return text


def _participant_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(PARTICIPANT_PREFIX + ":"):
        raise ParticipantRegistryError("PARTICIPANT_ID_REQUIRED")
    if not _ID_RE.match(text):
        raise ParticipantRegistryError("PARTICIPANT_ID_INVALID", text[:80])
    return text


def _endpoint_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _ID_RE.match(text) or not text.startswith(ENDPOINT_PREFIX + ":"):
        raise ParticipantRegistryError("PARTICIPANT_ENDPOINT_ID_INVALID", text[:80])
    return text


def _provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        raise ParticipantRegistryError("PARTICIPANT_PROVIDER_REQUIRED")
    if not _PROVIDER_RE.match(text):
        raise ParticipantRegistryError("PARTICIPANT_PROVIDER_INVALID", text[:40])
    return text


def _account_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ParticipantRegistryError("PARTICIPANT_ACCOUNT_KEY_REQUIRED")
    if len(text) > MAX_ACCOUNT_KEY or any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise ParticipantRegistryError("PARTICIPANT_ACCOUNT_KEY_INVALID", text[:80])
    return text


def _source(value: Any) -> str:
    text = str(value or "").strip()
    if text not in ALLOWED_SOURCES:
        raise ParticipantRegistryError("PARTICIPANT_SOURCE_REFUSED", text[:80])
    return text


def _created_by(value: Any) -> str:
    text = str(value or "").strip()
    if text != CREATED_BY_OPERATOR:
        raise ParticipantRegistryError("PARTICIPANT_CREATED_BY_REFUSED", text[:80])
    return text


def _new_id(prefix: str) -> str:
    return "%s:%s" % (prefix, secrets.token_hex(12))


def _empty(reason_code: str, detail: str = "", *, status: str = STATUS_UNREADABLE,
           **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "status": status, "reason_code": reason_code,
                           "detail": detail, "data": None, "writes_performed": 0}
    out.update(extra)
    return out


# ---------------------------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------------------------


class ParticipantRegistry:
    """One instance's participant registry.  Identity only; no message body, no authority."""

    def __init__(self, state_root: Path | str, *, subject_id: str,
                 clock: Optional[Callable[[], Any]] = None,
                 busy_timeout_ms: int = BUSY_TIMEOUT_MS,
                 inbox: Optional[Any] = None) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / DB_FILENAME
        subject = str(subject_id or "").strip()
        if not subject:
            raise ParticipantRegistryError("PARTICIPANT_SUBJECT_REQUIRED")
        self.subject_id = subject
        self._clock = clock
        self._busy_timeout_ms = max(1, int(busy_timeout_ms))
        #: An explicit, already-bound local inbox.  ``None`` means "this owner cannot verify an
        #: arrival here": linking then refuses by name instead of trusting a caller's word.
        self._inbox = inbox

    # -- identity ---------------------------------------------------------------------

    def _now(self) -> str:
        return _now_from(self._clock)

    def describe(self) -> dict[str, Any]:
        return {
            "owner": "ParticipantRegistry",
            "schema_version": SCHEMA_VERSION,
            "state_root": str(self.state_root),
            "path": str(self.path),
            "subject_id": self.subject_id,
            "owns_participant_identity": True,
            "owns_endpoint_binding": True,
            "owns_event_link": True,
            "owns_message_body": False,
            "owns_relationship": False,
            "owns_authority": False,
            "inherits_grants": False,
            "reads_on_get": True,
            "writes_on_read": False,
        }

    # -- the OS boundary --------------------------------------------------------------

    def _state_root_stat(self) -> Optional[os.stat_result]:
        try:
            return os.lstat(self.state_root)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ParticipantRegistryError("PARTICIPANT_STATE_ROOT_UNREADABLE",
                                           str(exc)[:120]) from exc

    def _ledger_stat(self) -> Optional[os.stat_result]:
        try:
            return os.lstat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ParticipantRegistryError("PARTICIPANT_STATE_UNREADABLE",
                                           str(exc)[:120]) from exc

    def _verify_state_root(self) -> Optional[os.stat_result]:
        """Never follow a symlink: the root must be a real directory, or not exist yet."""

        info = self._state_root_stat()
        if info is None:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise ParticipantRegistryError(
                "PARTICIPANT_STATE_ROOT_SYMLINK",
                "%s is a symlink; this owner never follows one" % str(self.state_root)[:120])
        if not stat.S_ISDIR(info.st_mode):
            raise ParticipantRegistryError("PARTICIPANT_STATE_ROOT_INVALID",
                                           str(self.state_root)[:120])
        return info

    def _verify_operator_write(self) -> None:
        """POSIX owner/mode checks for the only write entry; fail closed elsewhere.

        ``created_by = operator:cli`` is a *source label*, not authentication of a human.
        What this check can honestly say is: the process has this uid, the state root and the
        ledger file are owned by that uid and are not group/other writable, and neither is a
        symlink.  A different process with the same uid (or root) is outside this boundary.
        """

        if os.name != "posix" or not hasattr(os, "geteuid"):
            raise ParticipantRegistryError(
                "PARTICIPANT_OPERATOR_OS_UNSUPPORTED",
                "writes need a POSIX uid/mode check; this host has no verifiable local owner")
        euid = int(os.geteuid())
        info = self._verify_state_root()
        if info is not None:
            if int(info.st_uid) != euid:
                raise ParticipantRegistryError(
                    "PARTICIPANT_STATE_ROOT_NOT_OWNED",
                    "%s is owned by uid %s, this operator is %s"
                    % (str(self.state_root)[:100], info.st_uid, euid))
            if int(info.st_mode) & 0o022:
                raise ParticipantRegistryError(
                    "PARTICIPANT_STATE_ROOT_UNSAFE_MODE",
                    "%s is group/other writable (mode %o)"
                    % (str(self.state_root)[:100], stat.S_IMODE(info.st_mode)))
        ledger = self._ledger_stat()
        if ledger is not None:
            if stat.S_ISLNK(ledger.st_mode):
                raise ParticipantRegistryError(
                    "PARTICIPANT_STATE_FILE_SYMLINK",
                    "%s is a symlink; this owner never follows one" % str(self.path)[:120])
            if not stat.S_ISREG(ledger.st_mode):
                raise ParticipantRegistryError("PARTICIPANT_STATE_FILE_INVALID",
                                               str(self.path)[:120])
            if int(ledger.st_uid) != euid:
                raise ParticipantRegistryError(
                    "PARTICIPANT_STATE_FILE_NOT_OWNED",
                    "%s is owned by uid %s, this operator is %s"
                    % (str(self.path)[:100], ledger.st_uid, euid))
            if int(ledger.st_mode) & 0o022:
                raise ParticipantRegistryError(
                    "PARTICIPANT_STATE_FILE_UNSAFE_MODE",
                    "%s is group/other writable (mode %o)"
                    % (str(self.path)[:100], stat.S_IMODE(ledger.st_mode)))

    # -- connections ------------------------------------------------------------------

    def _read_connection(self) -> Optional[sqlite3.Connection]:
        """A read-only connection, or ``None`` when no database exists yet."""

        self._verify_state_root()
        ledger = self._ledger_stat()
        if ledger is not None and stat.S_ISLNK(ledger.st_mode):
            raise ParticipantRegistryError(
                "PARTICIPANT_STATE_FILE_SYMLINK",
                "%s is a symlink; this owner never follows one" % str(self.path)[:120])
        if not self.path.is_file():
            return None
        try:
            uri = self.path.resolve().as_uri() + "?mode=ro"
        except ValueError as exc:  # pragma: no cover - a path sqlite could never serve
            raise ParticipantRegistryError("PARTICIPANT_STATE_UNREADABLE", str(exc)[:120]) from exc
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=self._busy_timeout_ms / 1000.0)
        except sqlite3.OperationalError as exc:
            if "unable to open" in str(exc).lower():
                return None
            raise ParticipantRegistryError("PARTICIPANT_STATE_UNREADABLE", str(exc)[:120]) from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _write_connection(self) -> sqlite3.Connection:
        self._verify_operator_write()
        try:
            self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Re-check after a possible create: mkdir's mode is masked by umask, so the leaf
            # could still be group/other writable on an unusual umask.
            self._verify_operator_write()
            conn = sqlite3.connect(str(self.path), timeout=self._busy_timeout_ms / 1000.0,
                                   isolation_level=None)
        except ParticipantRegistryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise ParticipantRegistryError("PARTICIPANT_STATE_UNWRITABLE", str(exc)[:120]) from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=%d" % self._busy_timeout_ms)
            conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error:
            conn.close()
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - best effort on platforms without chmod
            pass
        try:
            self._verify_operator_write()
        except ParticipantRegistryError:
            conn.close()
            raise
        return conn

    def _verify_meta(self, conn: sqlite3.Connection) -> None:
        """Bind the file to this instance.  A foreign or half-written file is refused by name."""

        try:
            rows = {str(row["key"]): str(row["value"])
                    for row in conn.execute("SELECT key, value FROM meta")}
        except sqlite3.DatabaseError as exc:
            raise ParticipantRegistryError("PARTICIPANT_STATE_CORRUPT",
                                           str(exc)[:120]) from exc
        if not rows:
            raise ParticipantRegistryError("PARTICIPANT_STATE_UNINITIALIZED")
        if rows.get("schema_version") != SCHEMA_VERSION:
            raise ParticipantRegistryError("PARTICIPANT_SCHEMA_MISMATCH",
                                           str(rows.get("schema_version"))[:80])
        if rows.get("subject_id") != self.subject_id:
            raise ParticipantRegistryError(
                "PARTICIPANT_INSTANCE_MISMATCH",
                "the registry belongs to %s, this instance is %s"
                % (str(rows.get("subject_id"))[:60], self.subject_id[:60]))

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        try:
            for statement in _SCHEMA:
                conn.execute(statement)
            row = conn.execute("SELECT COUNT(*) AS n FROM meta").fetchone()
            if row is not None and int(row["n"]) == 0:
                now = self._now()
                conn.executemany(
                    "INSERT INTO meta(key, value) VALUES(?, ?)",
                    [("schema_version", SCHEMA_VERSION), ("subject_id", self.subject_id),
                     ("created_at", now), ("created_by", CREATED_BY_OPERATOR)])
            self._verify_meta(conn)
        except ParticipantRegistryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise ParticipantRegistryError("PARTICIPANT_STATE_CORRUPT", str(exc)[:120]) from exc

    def _mutate(self, fn: Callable[[sqlite3.Connection], tuple[bool, dict[str, Any]]]
                ) -> dict[str, Any]:
        try:
            conn = self._write_connection()
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail)
        except sqlite3.DatabaseError as exc:
            return _empty("PARTICIPANT_STATE_CORRUPT", str(exc)[:120])
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_schema(conn)
            changed, payload = fn(conn)
            if changed:
                conn.execute("COMMIT")
            else:
                conn.execute("ROLLBACK")
            return payload
        except ParticipantRegistryError as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return _empty(exc.reason_code, exc.detail, status=STATUS_REJECTED)
        except sqlite3.IntegrityError as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return _empty("PARTICIPANT_UNIQUE_CONFLICT", str(exc)[:120],
                          status=STATUS_CONFLICT)
        except sqlite3.OperationalError as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            text = str(exc).lower()
            code = "PARTICIPANT_DB_BUSY" if ("locked" in text or "busy" in text) \
                else "PARTICIPANT_STATE_UNREADABLE"
            return _empty(code, str(exc)[:120])
        except sqlite3.DatabaseError as exc:
            return _empty("PARTICIPANT_STATE_CORRUPT", str(exc)[:120])
        finally:
            conn.close()

    def _replay(self, conn: sqlite3.Connection, key: str, digest: str,
                kind: str) -> Optional[dict[str, Any]]:
        row = conn.execute("SELECT * FROM operations WHERE operation_key = ?",
                           (key,)).fetchone()
        if row is None:
            return None
        if str(row["payload_digest"]) != digest:
            return _empty("PARTICIPANT_OPERATION_KEY_CONFLICT",
                          "key %s is already bound to another payload" % key,
                          status=STATUS_REJECTED, operation_key=key,
                          existing_payload_digest=str(row["payload_digest"]),
                          payload_digest=digest)
        return {"ok": True, "status": STATUS_ALREADY, "reason_code": "", "detail": "",
                "operation_key": key, "replayed": True, "created": False,
                "writes_performed": 0, "kind": kind,
                "data": self._operation_data(conn, kind, str(row["ref"]))}

    def _record_operation(self, conn: sqlite3.Connection, key: str, kind: str,
                          digest: str, ref: str) -> None:
        conn.execute("INSERT INTO operations(operation_key, kind, payload_digest, ref, "
                     "created_at) VALUES(?, ?, ?, ?, ?)",
                     (key, kind, digest, ref, self._now()))

    def _operation_data(self, conn: sqlite3.Connection, kind: str, ref: str
                        ) -> dict[str, Any]:
        if kind == "participant":
            row = self._participant_row(conn, ref)
            return {"participant": row} if row else {}
        if kind == "endpoint":
            row = self._endpoint_row(conn, ref)
            return {"endpoint": row} if row else {}
        return {"event_ref": ref, "link": self._link_row(conn, ref)}

    # -- rows -------------------------------------------------------------------------

    @staticmethod
    def _participant_row(conn: sqlite3.Connection, participant_id: str
                         ) -> Optional[dict[str, Any]]:
        row = conn.execute("SELECT * FROM participants WHERE participant_id = ?",
                           (participant_id,)).fetchone()
        if row is None:
            return None
        try:
            aliases = json.loads(row["aliases"])
        except ValueError:
            aliases = []
        return {
            "participant_id": str(row["participant_id"]),
            "display_name": str(row["display_name"]),
            "aliases": [str(item) for item in aliases if isinstance(item, str)],
            "created_at": str(row["created_at"]),
            "created_by": str(row["created_by"]),
            "source": str(row["source"]),
            "is_authority": False,
            "inherits_grants": False,
        }

    @staticmethod
    def _endpoint_row(conn: sqlite3.Connection, endpoint_id: str
                      ) -> Optional[dict[str, Any]]:
        row = conn.execute("SELECT * FROM endpoints WHERE endpoint_id = ?",
                           (endpoint_id,)).fetchone()
        if row is None:
            return None
        return {
            "endpoint_id": str(row["endpoint_id"]),
            "participant_id": str(row["participant_id"]),
            "provider": str(row["provider"]),
            "account_key": str(row["account_key"]),
            "source": str(row["source"]),
            "created_at": str(row["created_at"]),
            "created_by": str(row["created_by"]),
        }

    @staticmethod
    def _link_row(conn: sqlite3.Connection, event_ref: str) -> Optional[dict[str, Any]]:
        row = conn.execute("SELECT * FROM event_links WHERE event_ref = ?",
                           (event_ref,)).fetchone()
        if row is None:
            return None
        return {
            "event_ref": str(row["event_ref"]),
            "participant_id": str(row["participant_id"]),
            "endpoint_id": str(row["endpoint_id"]) if row["endpoint_id"] else None,
            "link_source": str(row["link_source"]),
            "linked_at": str(row["linked_at"]),
            "linked_by": str(row["linked_by"]),
            "resolution": "LINKED",
        }

    def _counts(self, conn: sqlite3.Connection, participant_id: str) -> tuple[int, int]:
        endpoints = conn.execute("SELECT COUNT(*) AS n FROM endpoints WHERE participant_id = ?",
                                 (participant_id,)).fetchone()
        events = conn.execute("SELECT COUNT(*) AS n FROM event_links WHERE participant_id = ?",
                              (participant_id,)).fetchone()
        return (int(endpoints["n"]) if endpoints else 0,
                int(events["n"]) if events else 0)

    # -- writes -----------------------------------------------------------------------

    def create(self, *, display_name: Any, aliases: Any = None, operation_key: Any,
               source: Any = SOURCE_OPERATOR_CLI,
               created_by: Any = CREATED_BY_OPERATOR) -> dict[str, Any]:
        """Register one participant.  Same display names are separate people; never merged."""

        try:
            name = _clean_name(display_name, "display_name")
            alias_list = _aliases(aliases)
            key = _operation_key(operation_key)
            src = _source(source)
            who = _created_by(created_by)
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail, status=STATUS_REJECTED)
        payload = {"display_name": name, "aliases": list(alias_list),
                   "source": src, "created_by": who}
        digest = _digest("participant", payload)

        def apply(conn: sqlite3.Connection) -> tuple[bool, dict[str, Any]]:
            replay = self._replay(conn, key, digest, "participant")
            if replay is not None:
                return False, replay
            participant_id = _new_id(PARTICIPANT_PREFIX)
            conn.execute("INSERT INTO participants(participant_id, display_name, aliases, "
                         "created_at, created_by, source) VALUES(?, ?, ?, ?, ?, ?)",
                         (participant_id, name, json.dumps(list(alias_list),
                                                            ensure_ascii=False),
                          self._now(), who, src))
            self._record_operation(conn, key, "participant", digest, participant_id)
            return True, {
                "ok": True, "status": STATUS_APPLIED, "reason_code": "", "detail": "",
                "operation_key": key, "replayed": False, "created": True,
                "writes_performed": 1,
                "data": {"participant": self._participant_row(conn, participant_id)},
            }

        return self._mutate(apply)

    def bind_endpoint(self, *, participant_id: Any, provider: Any, account_key: Any,
                      operation_key: Any, source: Any = SOURCE_OPERATOR_CLI,
                      created_by: Any = CREATED_BY_OPERATOR) -> dict[str, Any]:
        """Bind one explicit ``(provider, account_key)`` endpoint to one participant."""

        try:
            pid = _participant_id(participant_id)
            prov = _provider(provider)
            account = _account_key(account_key)
            key = _operation_key(operation_key)
            src = _source(source)
            who = _created_by(created_by)
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail, status=STATUS_REJECTED)
        payload = {"participant_id": pid, "provider": prov, "account_key": account,
                   "source": src, "created_by": who}
        digest = _digest("endpoint", payload)

        try:
            conn = self._read_connection()
            if conn is None:
                return _empty("PARTICIPANT_NOT_FOUND", pid, status=STATUS_ABSENT)
            try:
                self._verify_meta(conn)
                if self._participant_row(conn, pid) is None:
                    return _empty("PARTICIPANT_NOT_FOUND", pid, status=STATUS_ABSENT)
            finally:
                conn.close()
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail)
        except sqlite3.DatabaseError as exc:
            return _empty("PARTICIPANT_STATE_CORRUPT", str(exc)[:120])

        def apply(conn: sqlite3.Connection) -> tuple[bool, dict[str, Any]]:
            replay = self._replay(conn, key, digest, "endpoint")
            if replay is not None:
                return False, replay
            if self._participant_row(conn, pid) is None:
                return False, _empty("PARTICIPANT_NOT_FOUND", pid, status=STATUS_ABSENT)
            existing = conn.execute(
                "SELECT * FROM endpoints WHERE provider = ? AND account_key = ?",
                (prov, account)).fetchone()
            if existing is not None:
                if str(existing["participant_id"]) == pid:
                    # The exact same binding is already recorded; report it, do not duplicate.
                    return False, {
                        "ok": True, "status": STATUS_ALREADY, "reason_code": "",
                        "detail": "", "operation_key": key, "replayed": True,
                        "created": False, "writes_performed": 0,
                        "data": {"endpoint": self._endpoint_row(conn,
                                                                str(existing["endpoint_id"]))},
                    }
                return False, _empty(
                    "PARTICIPANT_ENDPOINT_ALREADY_BOUND",
                    "%s/%s is already bound to another participant" % (prov, account),
                    status=STATUS_CONFLICT, endpoint_id=str(existing["endpoint_id"]))
            endpoint_id = _new_id(ENDPOINT_PREFIX)
            conn.execute("INSERT INTO endpoints(endpoint_id, participant_id, provider, "
                         "account_key, source, created_at, created_by) "
                         "VALUES(?, ?, ?, ?, ?, ?, ?)",
                         (endpoint_id, pid, prov, account, src, self._now(), who))
            self._record_operation(conn, key, "endpoint", digest, endpoint_id)
            return True, {
                "ok": True, "status": STATUS_APPLIED, "reason_code": "", "detail": "",
                "operation_key": key, "replayed": False, "created": True,
                "writes_performed": 1,
                "data": {"endpoint": self._endpoint_row(conn, endpoint_id)},
            }

        return self._mutate(apply)

    def link_event(self, *, event_ref: Any, participant_id: Any,
                   endpoint_id: Any = None, operation_key: Any,
                   source: Any = SOURCE_OPERATOR_CLI,
                   created_by: Any = CREATED_BY_OPERATOR) -> dict[str, Any]:
        """Explicitly say which participant one **real** inbox event belongs to.

        The event is verified against this instance's own inbox before anything is written.
        An unlinked arrival stays ``UNRESOLVED``; this method never guesses from a name or a
        weak ``source_ref``.
        """

        try:
            ref = _ref(event_ref, "event_ref", required=True)
            pid = _participant_id(participant_id)
            eid = _endpoint_id(endpoint_id)
            key = _operation_key(operation_key)
            src = _source(source)
            who = _created_by(created_by)
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail, status=STATUS_REJECTED)
        if self._inbox is None:
            return _empty("PARTICIPANT_EVENT_CHECK_UNAVAILABLE",
                          "no local inbox is bound; an arrival cannot be verified")
        try:
            answer = self._inbox.get_event(ref)
        except Exception as exc:  # an owner refusal is "cannot verify", never "no event"
            return _empty("PARTICIPANT_EVENT_CHECK_UNAVAILABLE", type(exc).__name__)
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            status = str((answer or {}).get("status")) if isinstance(answer, Mapping) else ""
            if status == "absent":
                return _empty("PARTICIPANT_EVENT_NOT_FOUND", ref, status=STATUS_ABSENT)
            code = str((answer or {}).get("reason_code") or "PARTICIPANT_EVENT_UNREADABLE") \
                if isinstance(answer, Mapping) else "PARTICIPANT_EVENT_UNREADABLE"
            return _empty(code, ref)
        payload = {"event_ref": ref, "participant_id": pid, "endpoint_id": eid or None,
                   "source": src, "created_by": who}
        digest = _digest("event_link", payload)

        try:
            conn = self._read_connection()
            if conn is None:
                return _empty("PARTICIPANT_NOT_FOUND", pid, status=STATUS_ABSENT)
            try:
                self._verify_meta(conn)
                if self._participant_row(conn, pid) is None:
                    return _empty("PARTICIPANT_NOT_FOUND", pid, status=STATUS_ABSENT)
                if eid and self._endpoint_row(conn, eid) is None:
                    return _empty("PARTICIPANT_ENDPOINT_NOT_FOUND", eid, status=STATUS_ABSENT)
            finally:
                conn.close()
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail)
        except sqlite3.DatabaseError as exc:
            return _empty("PARTICIPANT_STATE_CORRUPT", str(exc)[:120])

        def apply(conn: sqlite3.Connection) -> tuple[bool, dict[str, Any]]:
            replay = self._replay(conn, key, digest, "event_link")
            if replay is not None:
                return False, replay
            if self._participant_row(conn, pid) is None:
                return False, _empty("PARTICIPANT_NOT_FOUND", pid, status=STATUS_ABSENT)
            if eid:
                endpoint = self._endpoint_row(conn, eid)
                if endpoint is None:
                    return False, _empty("PARTICIPANT_ENDPOINT_NOT_FOUND", eid,
                                         status=STATUS_ABSENT)
                if endpoint["participant_id"] != pid:
                    return False, _empty(
                        "PARTICIPANT_ENDPOINT_MISMATCH",
                        "%s belongs to another participant" % eid, status=STATUS_CONFLICT)
            existing = self._link_row(conn, ref)
            if existing is not None:
                if existing["participant_id"] == pid and \
                        (existing["endpoint_id"] or None) == (eid or None):
                    return False, {
                        "ok": True, "status": STATUS_ALREADY, "reason_code": "", "detail": "",
                        "operation_key": key, "replayed": True, "created": False,
                        "writes_performed": 0, "data": {"link": existing},
                    }
                return False, _empty("PARTICIPANT_EVENT_LINK_CONFLICT",
                                     "%s is already linked to %s"
                                     % (ref, existing["participant_id"]),
                                     status=STATUS_CONFLICT, link=existing)
            conn.execute("INSERT INTO event_links(event_ref, participant_id, endpoint_id, "
                         "link_source, linked_at, linked_by, operation_key) "
                         "VALUES(?, ?, ?, ?, ?, ?, ?)",
                         (ref, pid, eid or None, src, self._now(), who, key))
            self._record_operation(conn, key, "event_link", digest, ref)
            return True, {
                "ok": True, "status": STATUS_APPLIED, "reason_code": "", "detail": "",
                "operation_key": key, "replayed": False, "created": True,
                "writes_performed": 1,
                "data": {"link": self._link_row(conn, ref)},
            }

        return self._mutate(apply)

    # -- reads (pure; a missing ledger never becomes an empty one by accident) ---------

    def _read(self, fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> dict[str, Any]:
        try:
            conn = self._read_connection()
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail, ledger_state="UNREADABLE")
        if conn is None:
            return _empty("PARTICIPANT_LEDGER_ABSENT", "", status=STATUS_ABSENT,
                          ledger_state="ABSENT")
        try:
            self._verify_meta(conn)
            return fn(conn)
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail, ledger_state="UNREADABLE")
        except sqlite3.DatabaseError as exc:
            return _empty("PARTICIPANT_STATE_CORRUPT", str(exc)[:120],
                          ledger_state="UNREADABLE")
        finally:
            conn.close()

    def list_participants(self, *, limit: Optional[int] = None) -> dict[str, Any]:
        cap = MAX_LIST if limit is None else max(0, min(int(limit), MAX_LIST))

        def read(conn: sqlite3.Connection) -> dict[str, Any]:
            rows = conn.execute(
                "SELECT * FROM participants ORDER BY rowid").fetchall()
            total = len(rows)
            selected = rows[len(rows) - cap:] if cap else []
            out: list[dict[str, Any]] = []
            for row in selected:
                item = self._participant_row(conn, str(row["participant_id"])) or {}
                endpoints, events = self._counts(conn, str(row["participant_id"]))
                item["endpoint_count"] = endpoints
                item["linked_event_count"] = events
                out.append(item)
            return {"ok": True, "status": STATUS_OK, "reason_code": "", "detail": "",
                    "writes_performed": 0,
                    "data": {"participants": out, "count": total,
                             "returned": len(out), "truncated": total > len(out),
                             "limit": cap, "ledger_state": "OK",
                             "private_text_included": False}}

        answer = self._read(read)
        if answer.get("status") == STATUS_ABSENT:
            return {"ok": True, "status": STATUS_OK, "reason_code": "", "detail": "",
                    "writes_performed": 0,
                    "data": {"participants": [], "count": 0, "returned": 0,
                             "truncated": False, "limit": cap, "ledger_state": "ABSENT",
                             "private_text_included": False}}
        return answer

    def get_participant(self, participant_id: Any) -> dict[str, Any]:
        try:
            pid = _participant_id(participant_id)
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail, status=STATUS_REJECTED)

        def read(conn: sqlite3.Connection) -> dict[str, Any]:
            row = self._participant_row(conn, pid)
            if row is None:
                return _empty("PARTICIPANT_NOT_FOUND", pid, status=STATUS_ABSENT,
                              ledger_state="OK", participant_id=pid)
            endpoints = [self._endpoint_row(conn, str(item["endpoint_id"]))
                         for item in conn.execute(
                             "SELECT endpoint_id FROM endpoints WHERE participant_id = ? "
                             "ORDER BY rowid", (pid,)).fetchall()]
            links = [self._link_row(conn, str(item["event_ref"]))
                     for item in conn.execute(
                         "SELECT event_ref FROM event_links WHERE participant_id = ? "
                         "ORDER BY rowid DESC LIMIT ?",
                         (pid, MAX_LINKED_EVENT_REFS)).fetchall()]
            total_links = conn.execute(
                "SELECT COUNT(*) AS n FROM event_links WHERE participant_id = ?",
                (pid,)).fetchone()
            row["endpoints"] = [item for item in endpoints if item]
            row["linked_events"] = [item for item in links if item]
            row["linked_event_refs"] = [item["event_ref"] for item in row["linked_events"]]
            row["endpoint_count"] = len(row["endpoints"])
            row["linked_event_count"] = int(total_links["n"]) if total_links else 0
            row["linked_events_truncated"] = row["linked_event_count"] > len(row["linked_events"])
            return {"ok": True, "status": STATUS_OK, "reason_code": "", "detail": "",
                    "writes_performed": 0,
                    "data": {"participant": row, "ledger_state": "OK",
                             "private_text_included": False,
                             "message_bodies_included": False}}

        return self._read(read)

    def resolve_event(self, event_ref: Any) -> dict[str, Any]:
        try:
            ref = _ref(event_ref, "event_ref", required=True)
        except ParticipantRegistryError as exc:
            return _empty(exc.reason_code, exc.detail, status=STATUS_REJECTED)

        def read(conn: sqlite3.Connection) -> dict[str, Any]:
            link = self._link_row(conn, ref)
            if link is None:
                return {"ok": True, "status": "UNRESOLVED", "reason_code": "", "detail": "",
                        "writes_performed": 0,
                        "data": {"event_ref": ref, "resolution": "UNRESOLVED",
                                 "participant": None, "endpoint": None,
                                 "ledger_state": "OK",
                                 "note": "no explicit operator link; the arrival is not "
                                         "attributed to anyone"}}
            participant = self._participant_row(conn, link["participant_id"])
            endpoint = (self._endpoint_row(conn, link["endpoint_id"])
                        if link.get("endpoint_id") else None)
            return {"ok": True, "status": "LINKED", "reason_code": "", "detail": "",
                    "writes_performed": 0,
                    "data": {"event_ref": ref, "resolution": "LINKED",
                             "participant": participant, "endpoint": endpoint,
                             "link": link, "ledger_state": "OK",
                             "private_text_included": False}}

        answer = self._read(read)
        if answer.get("status") == STATUS_ABSENT:
            return {"ok": True, "status": "UNRESOLVED", "reason_code": "", "detail": "",
                    "writes_performed": 0,
                    "data": {"event_ref": ref, "resolution": "UNRESOLVED",
                             "participant": None, "endpoint": None,
                             "ledger_state": "ABSENT",
                             "note": "this instance has no participant ledger yet"}}
        return answer

    def summary(self) -> dict[str, Any]:
        def read(conn: sqlite3.Connection) -> dict[str, Any]:
            participants = conn.execute("SELECT COUNT(*) AS n FROM participants").fetchone()
            endpoints = conn.execute("SELECT COUNT(*) AS n FROM endpoints").fetchone()
            links = conn.execute("SELECT COUNT(*) AS n FROM event_links").fetchone()
            ops = conn.execute("SELECT COUNT(*) AS n FROM operations").fetchone()
            return {"ok": True, "status": STATUS_OK, "reason_code": "", "detail": "",
                    "writes_performed": 0,
                    "data": {"ledger_state": "OK",
                             "participant_count": int(participants["n"]) if participants else 0,
                             "endpoint_count": int(endpoints["n"]) if endpoints else 0,
                             "linked_event_count": int(links["n"]) if links else 0,
                             "operation_count": int(ops["n"]) if ops else 0,
                             "private_text_included": False,
                             "owns_authority": False}}

        answer = self._read(read)
        if answer.get("status") == STATUS_ABSENT:
            return {"ok": True, "status": STATUS_OK, "reason_code": "", "detail": "",
                    "writes_performed": 0,
                    "data": {"ledger_state": "ABSENT", "participant_count": 0,
                             "endpoint_count": 0, "linked_event_count": 0,
                             "operation_count": 0, "private_text_included": False,
                             "owns_authority": False}}
        return answer


__all__ = [
    "ALLOWED_SOURCES",
    "CREATED_BY_OPERATOR",
    "DB_FILENAME",
    "MAX_ALIASES",
    "MAX_DISPLAY_NAME",
    "MAX_LIST",
    "ParticipantRegistry",
    "ParticipantRegistryError",
    "SCHEMA_VERSION",
    "SOURCE_OPERATOR_CLI",
]
