"""F-153/F-154: operator forget requests, a retained barrier, and owner acknowledgment.

What this layer is -- and is not
--------------------------------
It is the *coordination* record for one explicit forget request: who asked (a local operator on
an existing instance), which opaque source refs / event ids the request covers, how far the
request got, and which owner acknowledgments are still missing.  It is **not** a second Memory
owner:

* the barrier itself is appended to the existing retained deletion authority
  (``memory_authority.MemoryDeletionAuthority``) *before* any payload work;
* each payload decision is still applied through each owner's own contract
  (``memory_control.MemoryControlLedger.apply`` for M0 evidence);
* reads stay in ``life_experience_port`` / ``life_memory_service``, which call this gate.

Barrier before payload
----------------------
``request`` appends one authority record first.  The record's ``blocked_event_ids`` carries
**only concrete event/turn ids** -- the retained authority field is event-id typed (its column
name and every existing consumer do exact event-id matching), so this layer does not smuggle a
second identifier domain into it.  A failed authority append leaves the case ``BLOCKED`` and no
owner ack is attempted.

Source-level retained blocking (a re-ingested event under the same opaque source ref, after a
deletion) is now a real typed field on the retained authority: ``blocked_sources`` holds
``(kind, namespace, ref)`` facts in their own domain, never smuggled into ``blocked_event_ids``.
Every payload owner re-proves an owner-issued ``ErasureGrant`` -- a bare integer is not a
credential -- and derived documents are found by the owner's own edges, not by a hand-written
list in the Memory event.  The explicit ``hold`` path is different: it is a *retained case*
gate (``HELD`` cases in this coordination database, outside the instance restore set) and
withholds the named scope without writing a DELETE or touching the payload.

Source tokens used by the case gate are deliberately *not* payload: they are a truncated digest
of the ref, never the ref itself, the text, or a guessable content hash.

States
------
``RECEIVED`` -> ``HELD`` (scope unclear, target not found, or an explicit hold with a scope) or
``BARRIER_APPLIED`` -> ``OWNER_PENDING`` -> ``WAITING_RETENTION`` (owner acks done, but the
append-only M0 payload is physically retained, an offline backup is retained, or an uncovered
derived surface is named) / ``COMPLETE`` (every configured owner ack done *and* no physical
residual is observable -- unreachable on the current append-only M0, never asserted from a
missing caller flag).  ``BLOCKED`` means the barrier could not be established or the authority
cannot be verified.

Completion is owner-derived, not caller-asserted: a request cannot reach ``COMPLETE`` just
because the CLI omitted ``--uncovered``/``--retention-pending`` while the evidence row still
exists.  The physical check is a boolean read only -- no text is returned or logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

_COMPONENTS = Path(__file__).resolve().parents[2]
_NATIVE = _COMPONENTS / "native"
for _path in (str(_NATIVE), str(_NATIVE / "memory_runtime_v1"), str(_NATIVE / "src")):
    if _path not in os.sys.path:
        os.sys.path.insert(0, _path)

from memory_authority import (  # noqa: E402
    NAMESPACE_BY_KIND,
    ErasureGrant,
    MemoryAuthorityUnavailable,
    MemoryDeletionAuthority,
    authority_min_seq_from_env,
    normalize_sources,
    resolve_authority_path,
    source_key,
)
from memory_control import MemoryControlLedger  # noqa: E402

#: Env names for the coordination database.  Deliberately separate from the three Memory names:
#: an instance that never configured this keeps its previous behaviour.
FORGET_CASE_ENV = ("NYA02_FORGET_CASE_DB", "CHIYO_LIFE_FORGET_CASE_DB")

SOURCE_TOKEN_PREFIX = "forgetsrc:"
MAX_REF_CHARS = 200

#: Typed source authority is a real field on the retained deletion authority: each source is
#: ``{kind, namespace, ref, generation?}`` in its own column/chain payload, matched on the full
#: triple.  Event ids stay in the event-id field; the two identifier domains are never mixed.
SOURCE_AUTHORITY_CONTRACT = "ENABLED_TYPED_SOURCE_V1"

RECEIVED = "RECEIVED"
HELD = "HELD"
BARRIER_APPLIED = "BARRIER_APPLIED"
OWNER_PENDING = "OWNER_PENDING"
WAITING_RETENTION = "WAITING_RETENTION"
COMPLETE = "COMPLETE"
BLOCKED = "BLOCKED"

REASON_SCOPE_UNCLEAR = "FORGET_SCOPE_UNCLEAR"
REASON_TARGET_NOT_FOUND = "FORGET_TARGET_NOT_FOUND"
REASON_OPERATION_KEY_REQUIRED = "FORGET_OPERATION_KEY_REQUIRED"
REASON_OPERATION_COLLISION = "FORGET_OPERATION_IDENTITY_COLLISION"
REASON_OWNER_MISMATCH = "FORGET_OWNER_MISMATCH"
REASON_AUTHORITY_REQUIRED = "FORGET_AUTHORITY_REQUIRED"
REASON_BARRIER_UNVERIFIED = "FORGET_BARRIER_UNVERIFIED"
REASON_OWNER_APPLY_FAILED = "FORGET_OWNER_APPLY_FAILED"
REASON_DERIVED_NOT_COVERED = "FORGET_DERIVED_SURFACES_NOT_COVERED"
REASON_OFFLINE_BACKUP = "FORGET_OFFLINE_BACKUP_RETAINED"
REASON_PHYSICAL_RETAINED = "FORGET_PHYSICAL_PAYLOAD_RETAINED"
REASON_SOURCE_AUTHORITY = "FORGET_SOURCE_AUTHORITY_NEEDS_OWNER_CONTRACT"
REASON_CASE_NOT_FOUND = "FORGET_CASE_NOT_FOUND"
REASON_CASE_DB_UNREADABLE = "FORGET_CASE_DB_UNREADABLE"
REASON_INSTANCE_UNSAFE = "FORGET_INSTANCE_UNSAFE"
REASON_GATE_UNHEALTHY = "FORGET_GATE_UNHEALTHY"


class ForgetRefused(RuntimeError):
    """A named refusal.  ``code`` is a stable upper-case reason."""

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(str(code))
        self.code = str(code)
        self.detail = detail


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def source_token(ref: str) -> str:
    """An opaque, non-reversible marker for one source ref (never the ref itself)."""

    return SOURCE_TOKEN_PREFIX + _sha(str(ref))[:32]


def _clean_refs(refs: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for item in refs or ():
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > MAX_REF_CHARS or "\n" in text or "\r" in text:
            raise ForgetRefused("FORGET_REF_INVALID", "a source ref must be a short opaque id")
        out.append(text)
    return sorted(set(out))


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


class ForgetCaseStore:
    """Durable coordination record for explicit forget requests on one binding."""

    def __init__(self, path: Path | str, *, evidence_db: Path | str,
                 controls_db: Path | str, authority_db: Optional[Path | str],
                 owner: str, conversation: str,
                 authority_min_seq: Optional[int] = None,
                 supply_store: Any = None) -> None:
        if not owner or not conversation:
            raise ValueError("explicit owner and conversation are required")
        self.path = Path(path)
        self.evidence_db = Path(evidence_db)
        self.controls_db = Path(controls_db)
        self.authority_db = Path(authority_db) if authority_db else None
        self.owner = str(owner)
        self.conversation = str(conversation)
        self.authority_min_seq = authority_min_seq
        #: The real derived owner (``ManagedArtifactStore``) for document bodies.  ``None`` means
        #: this binding has no derived owner; an event that points at an artifact then stays an
        #: explicit uncovered surface rather than being reported clean.
        self.supply_store = supply_store

    # -- storage -------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=3)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS forget_cases(
                  case_id TEXT PRIMARY KEY,
                  operation_key TEXT NOT NULL UNIQUE,
                  owner TEXT NOT NULL,
                  conversation TEXT NOT NULL,
                  scope_digest TEXT NOT NULL,
                  source_refs_json TEXT NOT NULL,
                  event_ids_json TEXT NOT NULL,
                  state TEXT NOT NULL,
                  reason TEXT,
                  detail TEXT,
                  barrier_seq INTEGER,
                  barrier_digest TEXT,
                  owner_acks_json TEXT NOT NULL,
                  uncovered_json TEXT NOT NULL,
                  derived_json TEXT NOT NULL DEFAULT '[]',
                  retention_pending INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS forget_case_log(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  case_id TEXT NOT NULL,
                  operation TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS forget_log_no_update
                  BEFORE UPDATE ON forget_case_log
                  BEGIN SELECT RAISE(ABORT,'forget case log is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS forget_log_no_delete
                  BEFORE DELETE ON forget_case_log
                  BEGIN SELECT RAISE(ABORT,'forget case log is append-only'); END;
                """
            )
            columns = {str(row[1]) for row in
                       connection.execute("PRAGMA table_info(forget_cases)").fetchall()}
            if "derived_json" not in columns:
                connection.execute("ALTER TABLE forget_cases ADD COLUMN derived_json "
                                   "TEXT NOT NULL DEFAULT '[]'")
            connection.commit()
        finally:
            connection.close()
        os.chmod(self.path, 0o600)

    def _row(self, case_id: str) -> Optional[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM forget_cases WHERE case_id=? AND owner=? AND conversation=?",
                (str(case_id), self.owner, self.conversation)).fetchone()
        finally:
            connection.close()

    def _by_key(self, operation_key: str) -> Optional[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM forget_cases WHERE operation_key=? AND owner=? AND conversation=?",
                (str(operation_key), self.owner, self.conversation)).fetchone()
        finally:
            connection.close()

    def _log(self, case_id: str, operation: str, payload: Mapping[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO forget_case_log(case_id,operation,payload_json,created_at) "
                "VALUES(?,?,?,?)",
                (str(case_id), str(operation), _canonical(dict(payload)), _now()))
            connection.commit()
        finally:
            connection.close()

    def _save(self, case_id: str, **fields: Any) -> None:
        allowed = {
            "state", "reason", "detail", "barrier_seq", "barrier_digest",
            "owner_acks_json", "uncovered_json", "event_ids_json", "retention_pending",
            "derived_json",
        }
        assignments = [key for key in fields if key in allowed]
        if not assignments:
            return
        sql = "UPDATE forget_cases SET %s, updated_at=? WHERE case_id=?" % (
            ", ".join("%s=?" % key for key in assignments),)
        params = [fields[key] for key in assignments] + [_now(), str(case_id)]
        connection = self._connect()
        try:
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def _insert_case(self, *, case_id: str, operation_key: str, scope_digest: str,
                     source_refs: Sequence[str], event_ids: Sequence[str],
                     state: str, reason: Optional[str], retention_pending: bool) -> None:
        stamp = _now()
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO forget_cases(case_id,operation_key,owner,conversation,"
                "scope_digest,source_refs_json,event_ids_json,state,reason,detail,barrier_seq,"
                "barrier_digest,owner_acks_json,uncovered_json,retention_pending,created_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, operation_key, self.owner, self.conversation, scope_digest,
                 _canonical(list(source_refs)), _canonical(list(event_ids)), state, reason, None,
                 None, None, _canonical([]), _canonical([]), 1 if retention_pending else 0,
                 stamp, stamp))
            connection.commit()
        finally:
            connection.close()

    # -- authority -----------------------------------------------------------------------

    def _authority(self) -> Optional[MemoryDeletionAuthority]:
        if self.authority_db is None:
            return None
        return MemoryDeletionAuthority(self.authority_db, owner=self.owner,
                                       conversation=self.conversation,
                                       minimum_seq=self.authority_min_seq)

    # -- target resolution ---------------------------------------------------------------

    def _resolve_events(self, source_refs: Sequence[str],
                        event_ids: Sequence[str]) -> list[str]:
        """Existing M0 events this owner/conversation holds for the given scope."""

        found: set[str] = set()
        try:
            connection = _readonly(self.evidence_db)
        except Exception as exc:
            raise ForgetRefused("FORGET_EVIDENCE_UNREADABLE",
                                "%s: %s" % (type(exc).__name__, exc)) from exc
        try:
            for event_id in event_ids:
                row = connection.execute(
                    "SELECT event_id FROM evidence_events WHERE event_id=? "
                    "AND conversation_id=? AND memory_owner=?",
                    (str(event_id), self.conversation, self.owner)).fetchone()
                if row is not None:
                    found.add(str(row[0]))
            if source_refs:
                # Exact match on the parsed ref id -- never a SQL substring.  A different kind
                # or namespace that happens to share an opaque string is another source, and a
                # row whose provenance cannot be parsed is not silently adopted either.
                wanted_refs = {str(ref) for ref in source_refs if str(ref)}
                rows = connection.execute(
                    "SELECT event_id,source_refs_json FROM evidence_events "
                    "WHERE conversation_id=? AND memory_owner=?",
                    (self.conversation, self.owner)).fetchall()
                for row in rows:
                    try:
                        items = json.loads(row["source_refs_json"] or "[]")
                    except ValueError:
                        continue
                    if any(isinstance(item, dict) and str(item.get("id") or "") in wanted_refs
                           for item in items):
                        found.add(str(row["event_id"]))
        except sqlite3.Error as exc:
            raise ForgetRefused("FORGET_EVIDENCE_UNREADABLE",
                                "%s: %s" % (type(exc).__name__, exc)) from exc
        finally:
            connection.close()
        return sorted(found)

    def _typed_sources(self, refs: Sequence[str],
                       event_ids: Sequence[str]) -> list[dict[str, str]]:
        """Typed ``{kind, namespace, ref}`` facts this decision bars.

        Three kinds of fact are retained, each named exactly:

        * a caller-named source ref (kind read from the carrying event, ``opaque`` otherwise);
        * every resolved event id, in the ``memory`` namespace -- this is the root a derived
          owner's own source edges point back to;
        * every ``artifact``/``artifactversion`` an event really references -- the material that
          event was built on.  These are barred permanently, so a later event citing the same
          material is refused before it is written.

        Derived documents (practice/sharing artifacts built *from* an experience) are **not**
        listed here: they are found by the owner's recorded edges, never by hand.
        """

        wanted = {str(ref) for ref in refs if str(ref)}
        kinds: dict[str, set[str]] = {}
        out: list[dict[str, str]] = []
        resolved = [str(event) for event in event_ids if str(event)]
        if resolved:
            try:
                connection = _readonly(self.evidence_db)
            except Exception as exc:
                raise ForgetRefused("FORGET_EVIDENCE_UNREADABLE",
                                    "%s: %s" % (type(exc).__name__, exc)) from exc
            try:
                placeholders = ",".join("?" for _ in resolved)
                rows = connection.execute(
                    "SELECT source_refs_json FROM evidence_events WHERE event_id IN (%s)"
                    % placeholders, tuple(resolved)).fetchall()
            except sqlite3.Error as exc:
                raise ForgetRefused("FORGET_EVIDENCE_UNREADABLE",
                                    "%s: %s" % (type(exc).__name__, exc)) from exc
            finally:
                connection.close()
            for row in rows:
                try:
                    items = json.loads(row["source_refs_json"] or "[]")
                except ValueError:
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    ident = str(item.get("id") or "")
                    kind = str(item.get("kind") or "opaque")
                    if ident in wanted:
                        kinds.setdefault(ident, set()).add(kind)
                    if kind in ("artifact", "artifactversion") and ident:
                        out.append({"kind": kind,
                                    "namespace": str(item.get("namespace")
                                                     or NAMESPACE_BY_KIND.get(kind, "")),
                                    "ref": ident})
        for ref in sorted(wanted):
            for kind in sorted(kinds.get(ref) or {"opaque"}):
                out.append({"kind": kind, "namespace": NAMESPACE_BY_KIND.get(kind, ""),
                            "ref": ref})
        for event_id in resolved:
            out.append({"kind": "memory", "namespace": "memory", "ref": event_id})
        return normalize_sources(out)

    def _derived_targets(self, event_ids: Sequence[str]) -> list[dict[str, str]]:
        """Owner artifacts/versions an event points at (the derived surface to clean).

        Read-only and source-scoped: only ``artifact`` / ``artifactversion`` refs of the named
        events.  An empty list means the scope has no such edge in this store (reported as
        NOT_PRESENT by the caller, never assumed clean).
        """

        resolved = [str(event) for event in event_ids if str(event)]
        if not resolved:
            return []
        try:
            connection = _readonly(self.evidence_db)
        except Exception as exc:
            raise ForgetRefused("FORGET_EVIDENCE_UNREADABLE",
                                "%s: %s" % (type(exc).__name__, exc)) from exc
        try:
            placeholders = ",".join("?" for _ in resolved)
            rows = connection.execute(
                "SELECT source_refs_json FROM evidence_events WHERE event_id IN (%s)"
                % placeholders, tuple(resolved)).fetchall()
        except sqlite3.Error as exc:
            raise ForgetRefused("FORGET_EVIDENCE_UNREADABLE",
                                "%s: %s" % (type(exc).__name__, exc)) from exc
        finally:
            connection.close()
        found: set[tuple[str, str]] = set()
        for row in rows:
            try:
                items = json.loads(row["source_refs_json"] or "[]")
            except ValueError:
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                kind, ident = str(item.get("kind") or ""), str(item.get("id") or "")
                if kind in ("artifact", "artifactversion") and ident:
                    found.add((kind, ident))
        return [{"kind": kind, "namespace": "supply", "ref": ident}
                for kind, ident in sorted(found)]

    def _blocked_ids(self, event_ids: Sequence[str]) -> list[str]:
        """Every event/turn sibling a retained deletion must cover."""

        blocked: set[str] = set()
        try:
            connection = _readonly(self.evidence_db)
        except Exception as exc:
            raise ForgetRefused("FORGET_EVIDENCE_UNREADABLE",
                                "%s: %s" % (type(exc).__name__, exc)) from exc
        try:
            for event_id in event_ids:
                row = connection.execute(
                    "SELECT event_id,turn_id,source_refs_json FROM evidence_events "
                    "WHERE event_id=? AND conversation_id=? AND memory_owner=?",
                    (str(event_id), self.conversation, self.owner)).fetchone()
                if row is None:
                    continue
                blocked.add(str(row["event_id"]))
                siblings = connection.execute(
                    "SELECT event_id FROM evidence_events WHERE conversation_id=? AND turn_id=?",
                    (self.conversation, row["turn_id"])).fetchall()
                blocked.update(str(item[0]) for item in siblings)
        finally:
            connection.close()
        return sorted(blocked)

    def _physical_residual(self, event_ids: Sequence[str]) -> bool:
        """Whether any named payload row is still physically present in M0.

        Boolean only: never returns content and never logs a body.  M0 is append-only, so on the
        current owner this is ``True`` whenever a target resolved -- which is exactly why a case
        whose only evidence is "the caller did not pass a flag" must not report ``COMPLETE``.
        An unreadable evidence file is treated as "still present" (cannot prove erasure).
        """

        wanted = [str(item) for item in event_ids if item]
        if not wanted:
            return False
        try:
            connection = _readonly(self.evidence_db)
        except Exception:
            return True
        try:
            placeholders = ",".join("?" for _ in wanted)
            try:
                # An erased row is kept for its event identity/audit, but it is no longer a
                # payable payload: ``payload_erased=1`` and an empty body is "gone".
                row = connection.execute(
                    "SELECT 1 FROM evidence_events WHERE event_id IN (%s) "
                    "AND (payload_erased=0 OR length(content)>0) LIMIT 1"
                    % placeholders, tuple(wanted)).fetchone()
            except sqlite3.OperationalError:
                # Pre-v3 store (no payload_erased column): a present row is unproven erasure.
                row = connection.execute(
                    "SELECT 1 FROM evidence_events WHERE event_id IN (%s) LIMIT 1"
                    % placeholders, tuple(wanted)).fetchone()
            return row is not None
        except sqlite3.Error:
            return True
        finally:
            connection.close()

    def active_holds(self) -> list[dict[str, Any]]:
        """Retained ``HELD`` cases with a named scope (the hold gate's only input).

        Read-only: a missing/uninitialized case file is a named gate failure, not a file the
        product read path silently creates.
        """

        connection = _readonly(self.path)
        try:
            rows = connection.execute(
                "SELECT case_id,source_refs_json,event_ids_json FROM forget_cases "
                "WHERE owner=? AND conversation=? AND state=?",
                (self.owner, self.conversation, HELD)).fetchall()
        finally:
            connection.close()
        out: list[dict[str, Any]] = []
        for row in rows:
            refs = [str(item) for item in json.loads(row["source_refs_json"] or "[]")]
            events = [str(item) for item in json.loads(row["event_ids_json"] or "[]")]
            if refs or events:
                out.append({"case_id": str(row["case_id"]), "source_refs": refs,
                            "event_ids": events})
        return out

    # -- public: request -----------------------------------------------------------------

    def request(self, *, operation_key: str, source_refs: Iterable[Any] = (),
                event_ids: Iterable[Any] = (), retention_pending: bool = False,
                uncovered_surfaces: Iterable[str] = (), hold_only: bool = False,
                requesting_owner: Optional[str] = None,
                requesting_uid: Optional[int] = None) -> dict[str, Any]:
        """Record one explicit forget request.  Barrier first; payload after.

        ``requesting_owner``/``requesting_uid`` are checked here so a caller cannot claim an
        identity: the operator CLI passes both from the bound instance and the OS.
        """

        key = str(operation_key or "").strip()
        if not key:
            raise ForgetRefused(REASON_OPERATION_KEY_REQUIRED,
                                "an explicit operation key is required")
        if requesting_owner is not None and str(requesting_owner) != self.owner:
            raise ForgetRefused(REASON_OWNER_MISMATCH, "the request owner is not this binding")
        getuid = getattr(os, "getuid", None)
        if requesting_uid is not None and getuid is not None:
            if int(requesting_uid) != int(getuid()):
                raise ForgetRefused(REASON_OWNER_MISMATCH,
                                    "the request uid is not the local operator")
        refs = _clean_refs(source_refs)
        events = _clean_refs(event_ids)
        uncovered = sorted({str(item) for item in (uncovered_surfaces or ()) if str(item).strip()})
        scope_digest = _sha(_canonical({"owner": self.owner, "conversation": self.conversation,
                                        "source_refs": refs, "event_ids": events}))
        existing = self._by_key(key)
        if existing is not None:
            if str(existing["scope_digest"]) != scope_digest:
                raise ForgetRefused(REASON_OPERATION_COLLISION,
                                    "this operation key already names a different scope")
            return {"ok": True, "status": "DUPLICATE", "case_id": str(existing["case_id"]),
                    "state": str(existing["state"]), "barrier_seq": existing["barrier_seq"]}
        if self.authority_db is None:
            raise ForgetRefused(REASON_AUTHORITY_REQUIRED,
                                "this binding has no retained deletion authority configured")

        if hold_only:
            return self._record_hold(operation_key=key, scope_digest=scope_digest, refs=refs,
                                     events=events, retention_pending=retention_pending)

        case_id = "forget:" + os.urandom(12).hex()
        self._insert_case(case_id=case_id, operation_key=key, scope_digest=scope_digest,
                          source_refs=refs, event_ids=events,
                          state=RECEIVED if (refs or events) else HELD,
                          reason=None if (refs or events) else REASON_SCOPE_UNCLEAR,
                          retention_pending=retention_pending)
        if not refs and not events:
            self._log(case_id, "HELD", {"reason": REASON_SCOPE_UNCLEAR})
            return self._answer(case_id, status="HELD")

        try:
            resolved = self._resolve_events(refs, events)
        except ForgetRefused as exc:
            # Cannot establish the target: hold the named scope, never report it forgotten.
            self._save(case_id, state=HELD, reason=exc.code, detail=exc.detail)
            self._log(case_id, "HELD", {"reason": exc.code})
            return self._answer(case_id, status="HELD", reason=exc.code, detail=exc.detail)

        if not resolved:
            # Nothing resolved: never turn "not found" into COMPLETE.  The case stays HELD, so the
            # named scope stays withheld by the retained case gate.
            self._save(case_id, state=HELD, reason=REASON_TARGET_NOT_FOUND)
            self._log(case_id, "HELD", {"reason": REASON_TARGET_NOT_FOUND})
            return self._answer(case_id, status="HELD", reason=REASON_TARGET_NOT_FOUND)

        # Event-id barrier only.  Source tokens are not written to this owner field (see
        # SOURCE_AUTHORITY_CONTRACT): it is event-id typed and every existing consumer
        # exact-matches event ids, so a token there would be a silent domain change.
        blocked = self._blocked_ids(resolved)

        authority = self._authority()
        try:
            authority.commit(operation_id="forget-case:" + case_id, target_event=case_id,
                             turn_id=None, blocked_event_ids=blocked,
                             fingerprint=scope_digest,
                             blocked_sources=self._typed_sources(refs, resolved))
        except MemoryAuthorityUnavailable as exc:
            self._save(case_id, state=BLOCKED, reason=exc.code, detail=exc.detail)
            self._log(case_id, "BLOCKED", {"reason": exc.code})
            return self._answer(case_id, status="BLOCKED", reason=exc.code, detail=exc.detail)
        try:
            projection = authority.projection()
        except Exception as exc:
            self._save(case_id, state=BARRIER_APPLIED, reason=REASON_BARRIER_UNVERIFIED,
                       detail="%s: %s" % (type(exc).__name__, exc))
            return self._answer(case_id, status="BARRIER_APPLIED",
                                reason=REASON_BARRIER_UNVERIFIED)
        if not projection.healthy:
            self._save(case_id, state=BARRIER_APPLIED, reason=REASON_BARRIER_UNVERIFIED,
                       detail=projection.detail)
            return self._answer(case_id, status="BARRIER_APPLIED",
                                reason=REASON_BARRIER_UNVERIFIED, detail=projection.detail)
        self._save(case_id, state=BARRIER_APPLIED, reason=None, detail=None,
                   barrier_seq=int(projection.seq), barrier_digest=projection.head_digest)
        self._log(case_id, "BARRIER_APPLIED", {"seq": int(projection.seq),
                                               "blocked": len(blocked)})
        return self._apply_owner(case_id, resolved=resolved, retention_pending=retention_pending,
                                 uncovered=uncovered)

    def _record_hold(self, *, operation_key: str, scope_digest: str, refs: Sequence[str],
                     events: Sequence[str], retention_pending: bool) -> dict[str, Any]:
        """A retained, case-level hold: withhold the named scope without touching the payload.

        It never writes a DELETE and never commits the deletion authority.  ``HELD`` cases are
        read by ``ForgetGate`` for as long as they stay held; there is no timer and no automatic
        release.  A later removal is an explicit ``confirm`` of the same case.
        """

        case_id = "forget:" + os.urandom(12).hex()
        if not refs and not events:
            self._insert_case(case_id=case_id, operation_key=operation_key,
                              scope_digest=scope_digest, source_refs=refs, event_ids=events,
                              state=HELD, reason=REASON_SCOPE_UNCLEAR,
                              retention_pending=retention_pending)
            self._log(case_id, "HELD", {"reason": REASON_SCOPE_UNCLEAR})
            return self._answer(case_id, status=HELD)
        resolved: list[str] = []
        if events:
            try:
                resolved = self._resolve_events([], events)
            except ForgetRefused:
                resolved = []
            if not resolved and not refs:
                # A named event that cannot be located is not a uniquely held target.
                self._insert_case(case_id=case_id, operation_key=operation_key,
                                  scope_digest=scope_digest, source_refs=refs,
                                  event_ids=events, state=HELD,
                                  reason=REASON_TARGET_NOT_FOUND,
                                  retention_pending=retention_pending)
                self._log(case_id, "HELD", {"reason": REASON_TARGET_NOT_FOUND})
                return self._answer(case_id, status=HELD, reason=REASON_TARGET_NOT_FOUND)
        self._insert_case(case_id=case_id, operation_key=operation_key, scope_digest=scope_digest,
                          source_refs=refs, event_ids=resolved, state=HELD, reason=None,
                          retention_pending=retention_pending)
        self._log(case_id, "HELD", {"scope_held": True, "source_refs": len(refs),
                                    "event_ids": len(resolved)})
        return self._answer(case_id, status=HELD)

    def _apply_owner(self, case_id: str, *, resolved: Sequence[str], retention_pending: bool,
                     uncovered: Sequence[str]) -> dict[str, Any]:
        """Each payload owner applies through its own contract; the barrier stays."""

        row = self._row(case_id)
        barrier_seq = int(row["barrier_seq"] or 0) if row is not None else 0
        uncovered_list = list(uncovered)
        stored_derived: list[dict[str, Any]] = []
        if row is not None:
            try:
                raw_derived = row["derived_json"] if "derived_json" in row.keys() else None
                stored_derived = list(json.loads(raw_derived or "[]"))
            except (ValueError, TypeError):
                stored_derived = []
        logical = MemoryControlLedger(self.controls_db, self.evidence_db, owner=self.owner,
                                      conversation=self.conversation, authority="")
        acks: list[str] = []
        for event_id in resolved:
            try:
                status = logical.apply(operation_id=case_id + ":owner:" + str(event_id),
                                       kind="DELETE", target=str(event_id), actor=self.owner)
            except Exception as exc:
                current = self._row(case_id)
                acks = json.loads(current["owner_acks_json"]) if current is not None else []
                self._save(case_id, state=OWNER_PENDING, reason=REASON_OWNER_APPLY_FAILED,
                           detail="%s: %s" % (type(exc).__name__, exc),
                           owner_acks_json=_canonical(acks),
                           uncovered_json=_canonical(uncovered_list),
                           retention_pending=1 if retention_pending else 0)
                self._log(case_id, "OWNER_PENDING", {"reason": REASON_OWNER_APPLY_FAILED})
                return self._answer(case_id, status=OWNER_PENDING,
                                    reason=REASON_OWNER_APPLY_FAILED)
            if status in ("APPLIED", "DUPLICATE"):
                acks.append(str(event_id))

        # The owner-issued grant is the only thing that lets a payload be cleared.  It binds this
        # application to the exact committed decision (case/seq/digest/closure), not to an
        # integer the caller supplied.
        owner_authority = self._authority()
        if owner_authority is None:
            self._save(case_id, state=OWNER_PENDING, reason=REASON_AUTHORITY_REQUIRED,
                       uncovered_json=_canonical(uncovered_list),
                       retention_pending=1 if retention_pending else 0)
            return self._answer(case_id, status=OWNER_PENDING, reason=REASON_AUTHORITY_REQUIRED)
        try:
            grant = owner_authority.authorize(case_id=case_id, event_ids=resolved,
                                              derived=stored_derived)
        except Exception as exc:
            self._save(case_id, state=OWNER_PENDING, reason=REASON_BARRIER_UNVERIFIED,
                       detail="%s: %s" % (type(exc).__name__, exc),
                       uncovered_json=_canonical(uncovered_list),
                       retention_pending=1 if retention_pending else 0)
            return self._answer(case_id, status=OWNER_PENDING, reason=REASON_BARRIER_UNVERIFIED)

        # The derived closure is the owner's own recorded lineage, never a list the Memory event
        # carries: practice/sharing artifacts built from the experience are found by following
        # their source edges back to the barred sources.  Resolved before the M0 body is cleared
        # and retained on the case so a retry after a partial owner failure cannot skip them.
        derived: list[dict[str, Any]] = list(stored_derived)
        closure: dict[str, Any] | None = None
        if self.supply_store is not None:
            closure = self.supply_store.source_closure(grant.sources)
            if closure.get("status") != "OK":
                self._save(case_id, state=OWNER_PENDING, reason=REASON_OWNER_APPLY_FAILED,
                           detail="closure: %s" % (closure.get("detail") or closure.get("status")),
                           uncovered_json=_canonical(uncovered_list),
                           retention_pending=1 if retention_pending else 0)
                return self._answer(case_id, status=OWNER_PENDING,
                                    reason=REASON_OWNER_APPLY_FAILED)
            derived = ([{"kind": "artifact", "namespace": "supply", "ref": ref}
                        for ref in closure.get("artifacts") or ()]
                       + [{"kind": "artifactversion", "namespace": "supply", "ref": ref}
                          for ref in closure.get("versions") or ()])
            self._save(case_id, derived_json=_canonical(derived))
        elif not derived:
            derived = self._derived_targets(resolved)
            if derived:
                self._save(case_id, derived_json=_canonical(derived))

        # Governed physical erasure of the M0 body, bound by the owner to the committed barrier.
        # A separate ledger carries the authority only to *read* currentness; it writes no new
        # generation, because the case barrier is the single retained decision for this request.
        physical_owner = MemoryControlLedger(
            self.controls_db, self.evidence_db, owner=self.owner,
            conversation=self.conversation,
            authority=str(self.authority_db) if self.authority_db is not None else "",
            authority_min_seq=self.authority_min_seq)
        try:
            m0 = physical_owner.erase(operation_id=case_id + ":m0-erase",
                                      event_ids=list(resolved), actor=self.owner,
                                      grant=grant)
        except Exception as exc:
            current = self._row(case_id)
            acks = json.loads(current["owner_acks_json"]) if current is not None else []
            self._save(case_id, state=OWNER_PENDING, reason=REASON_OWNER_APPLY_FAILED,
                       detail="M0: %s: %s" % (type(exc).__name__, exc),
                       owner_acks_json=_canonical(sorted(set(acks))),
                       uncovered_json=_canonical(uncovered_list),
                       retention_pending=1 if retention_pending else 0)
            self._log(case_id, "OWNER_PENDING",
                      {"reason": REASON_OWNER_APPLY_FAILED, "owner": "M0"})
            return self._answer(case_id, status=OWNER_PENDING,
                                reason=REASON_OWNER_APPLY_FAILED)

        # Real derived owner: document bodies and their projections live in the Supply artifact
        # owner.  Without a configured owner, a genuinely present edge is an explicit uncovered
        # surface; it is never assumed clean.
        derived_result: dict[str, Any] = {"present": bool(derived), "targets": derived}
        if derived:
            if self.supply_store is None:
                derived_result["status"] = "NOT_CONFIGURED"
                uncovered_list.append("supply_artifact")
            else:
                artifact_ids = [item["ref"] for item in derived if item["kind"] == "artifact"]
                version_ids = [item["ref"] for item in derived
                               if item["kind"] == "artifactversion"]
                try:
                    answer = self.supply_store.erase_source(
                        operation_key=case_id + ":supply-erase",
                        authorized_subject_id=self.owner,
                        grant=grant, authority=owner_authority,
                        artifact_ids=artifact_ids, version_ids=version_ids)
                except Exception as exc:
                    answer = {"status": "FAILED", "detail": "%s: %s"
                              % (type(exc).__name__, exc)}
                if answer.get("status") not in ("APPLIED", "DUPLICATE"):
                    current = self._row(case_id)
                    acks = json.loads(current["owner_acks_json"]) if current is not None else []
                    self._save(case_id, state=OWNER_PENDING,
                               reason=REASON_OWNER_APPLY_FAILED,
                               detail="supply: %s" % (answer.get("status"),),
                               owner_acks_json=_canonical(sorted(set(acks))),
                               uncovered_json=_canonical(uncovered_list),
                               retention_pending=1 if retention_pending else 0)
                    self._log(case_id, "OWNER_PENDING",
                              {"reason": REASON_OWNER_APPLY_FAILED, "owner": "supply"})
                    return self._answer(case_id, status=OWNER_PENDING,
                                        reason=REASON_OWNER_APPLY_FAILED)
                derived_result.update({
                    "status": answer.get("status"),
                    "erased_versions": int(answer.get("erased_versions") or 0),
                    "projection": answer.get("projection") or [],
                })
                if any(item.get("state") == "FAILED"
                       for item in derived_result.get("projection") or []):
                    uncovered_list.append("supply_projection")

        # Completion is owner-derived: the body must actually be gone (boolean read), every
        # configured owner acknowledged, and no live derived surface left uncovered.
        physical = self._physical_residual(resolved)
        state, reason = COMPLETE, None
        if uncovered_list:
            state, reason = WAITING_RETENTION, REASON_DERIVED_NOT_COVERED
        elif retention_pending:
            state, reason = WAITING_RETENTION, REASON_OFFLINE_BACKUP
        elif physical:
            state, reason = WAITING_RETENTION, REASON_PHYSICAL_RETAINED
        self._save(case_id, state=state, reason=reason, detail=None,
                   owner_acks_json=_canonical(sorted(set(acks))),
                   uncovered_json=_canonical(sorted(set(uncovered_list))),
                   retention_pending=1 if (retention_pending or physical) else 0)
        self._log(case_id, state, {"reason": reason, "acks": len(set(acks)),
                                   "uncovered": len(set(uncovered_list)),
                                   "physical": physical,
                                   "m0_erased": int(m0.get("erased") or 0),
                                   "derived": derived_result.get("status"),
                                   "derived_targets": len(derived)})
        return self._answer(case_id, status=state, reason=reason)

    # -- public: status / retry ----------------------------------------------------------

    def status(self, case_id: str) -> dict[str, Any]:
        row = self._row(case_id)
        if row is None:
            return {"ok": False, "status": BLOCKED, "case_id": str(case_id),
                    "reason": REASON_CASE_NOT_FOUND, "detail": None}
        state = str(row["state"])
        reason = row["reason"]
        authority = self._authority()
        if authority is not None:
            try:
                projection = authority.projection()
            except Exception as exc:
                return {"ok": False, "status": BLOCKED, "case_id": str(case_id),
                        "state": state, "reason": "MEMORY_AUTHORITY_UNAVAILABLE",
                        "detail": "%s: %s" % (type(exc).__name__, exc)}
            if not projection.healthy:
                return {"ok": False, "status": BLOCKED, "case_id": str(case_id),
                        "state": state, "reason": projection.reason,
                        "detail": projection.detail,
                        "barrier_seq": row["barrier_seq"]}
        return {"ok": state in (HELD, BARRIER_APPLIED, OWNER_PENDING, WAITING_RETENTION,
                                COMPLETE),
                "status": state, "case_id": str(case_id), "reason": reason,
                "barrier_seq": row["barrier_seq"],
                "barrier_digest": row["barrier_digest"],
                "source_refs": len(json.loads(row["source_refs_json"])),
                "event_ids": len(json.loads(row["event_ids_json"])),
                "owner_acks": len(json.loads(row["owner_acks_json"])),
                "uncovered": json.loads(row["uncovered_json"]),
                "retention_pending": bool(row["retention_pending"])}

    def retry(self, case_id: str) -> dict[str, Any]:
        """Re-run the missing owner acknowledgments for an existing case.

        The authority append is replayed under the same operation identity, so a successful
        first barrier is ``DUPLICATE`` and no new generation is created.
        """

        row = self._row(case_id)
        if row is None:
            raise ForgetRefused(REASON_CASE_NOT_FOUND, "no such case for this binding")
        if str(row["state"]) == HELD:
            # A hold is not a deletion; retry never upgrades it.  Confirmation is explicit.
            return self._answer(case_id, status=HELD, reason=row["reason"])
        refs = list(json.loads(row["source_refs_json"]))
        events = list(json.loads(row["event_ids_json"]))
        try:
            resolved = self._resolve_events(refs, events)
        except ForgetRefused as exc:
            self._save(case_id, state=HELD, reason=exc.code, detail=exc.detail)
            return self._answer(case_id, status=HELD, reason=exc.code, detail=exc.detail)
        if not resolved:
            self._save(case_id, state=HELD, reason=REASON_TARGET_NOT_FOUND)
            return self._answer(case_id, status=HELD, reason=REASON_TARGET_NOT_FOUND)
        authority = self._authority()
        if authority is not None:
            try:
                authority.commit(operation_id="forget-case:" + str(case_id),
                                 target_event=str(case_id), turn_id=None,
                                 blocked_event_ids=self._blocked_ids(resolved),
                                 fingerprint=str(row["scope_digest"]),
                                 blocked_sources=self._typed_sources(refs, resolved))
                projection = authority.projection()
            except MemoryAuthorityUnavailable as exc:
                self._save(case_id, state=BLOCKED, reason=exc.code, detail=exc.detail)
                return self._answer(case_id, status="BLOCKED", reason=exc.code, detail=exc.detail)
            if not projection.healthy:
                self._save(case_id, state=BARRIER_APPLIED, reason=REASON_BARRIER_UNVERIFIED)
                return self._answer(case_id, status="BARRIER_APPLIED",
                                    reason=REASON_BARRIER_UNVERIFIED)
            self._save(case_id, state=BARRIER_APPLIED, reason=None,
                       barrier_seq=int(projection.seq), barrier_digest=projection.head_digest)
        return self._apply_owner(str(case_id), resolved=resolved,
                                 retention_pending=bool(row["retention_pending"]),
                                 uncovered=list(json.loads(row["uncovered_json"])))

    def confirm(self, case_id: str) -> dict[str, Any]:
        """Explicitly turn a retained ``HELD`` case into the deletion it was holding for.

        This is the new case contract's confirmation path: it re-resolves the stored scope and
        runs the normal authority + owner flow.  A hold is never released by time or by retry.
        """

        row = self._row(case_id)
        if row is None:
            raise ForgetRefused(REASON_CASE_NOT_FOUND, "no such case for this binding")
        if str(row["state"]) != HELD:
            raise ForgetRefused("FORGET_CASE_NOT_HELD", "only a retained hold can be confirmed")
        refs = list(json.loads(row["source_refs_json"]))
        events = list(json.loads(row["event_ids_json"]))
        try:
            resolved = self._resolve_events(refs, events)
        except ForgetRefused as exc:
            self._save(case_id, state=HELD, reason=exc.code, detail=exc.detail)
            return self._answer(case_id, status=HELD, reason=exc.code, detail=exc.detail)
        if not resolved:
            self._save(case_id, state=HELD, reason=REASON_TARGET_NOT_FOUND)
            return self._answer(case_id, status=HELD, reason=REASON_TARGET_NOT_FOUND)
        authority = self._authority()
        if authority is None:
            raise ForgetRefused(REASON_AUTHORITY_REQUIRED,
                                "this binding has no retained deletion authority configured")
        try:
            authority.commit(operation_id="forget-case:" + str(case_id),
                             target_event=str(case_id), turn_id=None,
                             blocked_event_ids=self._blocked_ids(resolved),
                             fingerprint=str(row["scope_digest"]),
                             blocked_sources=self._typed_sources(refs, resolved))
            projection = authority.projection()
        except MemoryAuthorityUnavailable as exc:
            self._save(case_id, state=BLOCKED, reason=exc.code, detail=exc.detail)
            return self._answer(case_id, status="BLOCKED", reason=exc.code, detail=exc.detail)
        if not projection.healthy:
            self._save(case_id, state=BARRIER_APPLIED, reason=REASON_BARRIER_UNVERIFIED,
                       detail=projection.detail)
            return self._answer(case_id, status="BARRIER_APPLIED",
                                reason=REASON_BARRIER_UNVERIFIED, detail=projection.detail)
        self._save(case_id, state=BARRIER_APPLIED, reason=None, detail=None,
                   barrier_seq=int(projection.seq), barrier_digest=projection.head_digest)
        self._log(case_id, "CONFIRMED", {"seq": int(projection.seq)})
        return self._apply_owner(str(case_id), resolved=resolved,
                                 retention_pending=bool(row["retention_pending"]),
                                 uncovered=list(json.loads(row["uncovered_json"])))

    def list_cases(self, limit: int = 20) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT case_id,state,reason,barrier_seq,created_at FROM forget_cases "
                "WHERE owner=? AND conversation=? ORDER BY created_at DESC LIMIT ?",
                (self.owner, self.conversation, max(1, int(limit)))).fetchall()
        finally:
            connection.close()
        return [{"case_id": str(row["case_id"]), "state": str(row["state"]),
                 "reason": row["reason"], "barrier_seq": row["barrier_seq"],
                 "created_at": str(row["created_at"])} for row in rows]

    def _answer(self, case_id: str, *, status: str, reason: Optional[str] = None,
                detail: Optional[str] = None) -> dict[str, Any]:
        base = self.status(case_id)
        base.update({"status": status})
        if reason is not None:
            base["reason"] = reason
            base["ok"] = status in (HELD, BARRIER_APPLIED, OWNER_PENDING, WAITING_RETENTION,
                                    COMPLETE)
        if detail is not None:
            base["detail"] = detail
        return base


class ForgetGate:
    """Fail-closed read/admission gate for one owner's governed sources.

    Two retained inputs, never merged into one authority:

    * the retained deletion authority (event ids only -- a deletion barrier);
    * this binding's own ``HELD`` cases (the explicit-hold case gate: named event ids plus
      source tokens derived from the held refs).

    A missing/unverifiable/unreadable authority or case record blocks everything; it never
    degrades into "no forgotten sources".
    """

    def __init__(self, store: ForgetCaseStore) -> None:
        self.store = store

    def state(self) -> dict[str, Any]:
        authority = self.store._authority()
        if authority is None:
            return {"healthy": False, "reason": REASON_AUTHORITY_REQUIRED,
                    "blocked_event_ids": set(), "blocked_source_tokens": set(),
                    "blocked_source_keys": set(), "blocked_source_refs": set()}
        try:
            projection = authority.projection()
        except Exception as exc:
            return {"healthy": False, "reason": "MEMORY_AUTHORITY_UNAVAILABLE",
                    "detail": "%s: %s" % (type(exc).__name__, exc),
                    "blocked_event_ids": set(), "blocked_source_tokens": set(),
                    "blocked_source_keys": set(), "blocked_source_refs": set()}
        if not projection.healthy:
            return {"healthy": False, "reason": projection.reason, "detail": projection.detail,
                    "blocked_event_ids": set(), "blocked_source_tokens": set(),
                    "blocked_source_keys": set(), "blocked_source_refs": set()}
        events = {str(item) for item in projection.blocked_events
                  if not str(item).startswith(SOURCE_TOKEN_PREFIX)}
        tokens = {str(item) for item in projection.blocked_events
                  if str(item).startswith(SOURCE_TOKEN_PREFIX)}
        # Typed sources are exact ``(kind, namespace, ref)`` triples.  A bare ref id is not a
        # permission: the same opaque id in another kind/namespace is a different source and
        # stays visible.  They survive the case leaving HELD because they live in the retained
        # authority, not the case.
        source_keys = {tuple(key) for key in projection.blocked_sources}
        source_refs = {str(item) for item in projection.blocked_source_refs}
        try:
            holds = self.store.active_holds()
        except Exception as exc:
            return {"healthy": False, "reason": "FORGET_CASE_DB_UNREADABLE",
                    "detail": "%s: %s" % (type(exc).__name__, exc),
                    "blocked_event_ids": set(), "blocked_source_tokens": set(),
                    "blocked_source_keys": set(), "blocked_source_refs": set()}
        held_cases: list[str] = []
        for hold in holds:
            held_cases.append(str(hold["case_id"]))
            events.update(str(item) for item in hold["event_ids"])
            tokens.update(source_token(ref) for ref in hold["source_refs"])
            for ref in hold["source_refs"]:
                # A held case names a bare ref: it maps through the explicit kind table, never
                # by guessing from the string.
                source_keys.add(("opaque", "", str(ref)))
        return {"healthy": True, "reason": None, "blocked_event_ids": events,
                "blocked_source_tokens": tokens, "blocked_source_keys": source_keys,
                "blocked_source_refs": source_refs,
                "authority_seq": projection.seq, "held_cases": held_cases}

    @staticmethod
    def _event_keys(event: Any) -> set[tuple[str, str, str]]:
        keys: set[tuple[str, str, str]] = set()
        for item in (getattr(event, "source_refs", None) or ()):
            key = source_key(item)
            if key is not None:
                keys.add(key)
        return keys

    @classmethod
    def _blocks_event(cls, state: Mapping[str, Any], event: Any) -> bool:
        if str(getattr(event, "event_id", "") or "") in state["blocked_event_ids"]:
            return True
        if str(getattr(event, "turn_id", "") or "") in state["blocked_event_ids"]:
            return True
        keys = cls._event_keys(event)
        typed = state.get("blocked_source_keys") or set()
        if keys & typed:
            return True
        refs = {ref for _, _, ref in keys}
        return any(source_token(ref) in state["blocked_source_tokens"] for ref in refs)

    @staticmethod
    def _blocks_record(state: Mapping[str, Any], record: Any) -> bool:
        event_id = str(getattr(record, "event_id", "") or "")
        turn_id = str(getattr(record, "turn_id", "") or "")
        ref = str(getattr(record, "source_ref", "") or "")
        if event_id and event_id in state["blocked_event_ids"]:
            return True
        if turn_id and turn_id in state["blocked_event_ids"]:
            return True
        typed = state.get("blocked_source_keys") or set()
        # A record names only its primary source ref.  Match it only in the memory source domain:
        # a supply artifact that happens to share the string is not the same fact.
        memory_refs = {key_ref for kind, namespace, key_ref in typed
                       if namespace == "memory"
                       or kind in ("memory", "life_outcome", "reading", "opaque")}
        if ref and ref in memory_refs:
            return True
        return bool(ref) and source_token(ref) in state["blocked_source_tokens"]

    def blocks_event(self, event: Any) -> bool:
        """Admission check: any ref/turn/event identity covered by a retained barrier or hold."""

        state = self.state()
        if not state["healthy"]:
            return True
        return self._blocks_event(state, event)

    def blocks_record(self, record: Any) -> bool:
        state = self.state()
        if not state["healthy"]:
            return True
        return self._blocks_record(state, record)

    def filter_records(self, records: Iterable[Any]) -> list[Any]:
        state = self.state()
        if not state["healthy"]:
            return []
        return [record for record in records if not self._blocks_record(state, record)]


#: A recovered copy is opened by an explicit activation marker in its own directory, never by
#: simply constructing the ordinary owner objects around it.
RECOVERY_ENV = ("NYA02_F154_RECOVERY_DIR", "CHIYO_F154_RECOVERY_DIR")
RECOVERY_STATE_FILE = "recovery-state.json"

#: Every owner that took part in a sweep stamps its own database with an *incarnation*: a random
#: generation chosen during this activation.  The marker binds those generations, and the owners
#: re-read them before serving any body.  A path comparison alone is not enough -- restoring an
#: older backup *onto the same path* keeps every path equal while putting the forgotten body back.
INCIDENT_TABLE = "recovery_incarnation"


class RecoveryBlocked(RuntimeError):
    """The recovered copy is not (or no longer) activated; no body may be served."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(str(code))
        self.code = str(code)
        self.detail = str(detail)


#: The columns a recovered control ledger must carry before it may be trusted.  A file that
#: merely ends in ``.db`` is not a control ledger; the sweep will not open a copy on one.
CONTROL_COLUMNS = frozenset({"operation_id", "owner", "conversation", "kind", "target_event",
                             "replacement_event", "fingerprint", "created_at"})
ERASURE_COLUMNS = frozenset({"operation_id", "owner", "conversation", "event_ids_json",
                             "barrier_seq", "fingerprint", "result_json", "created_at"})


def recovered_controls_identity(path: Path | str, *, owner: str, conversation: str,
                                recovery_dir: Path | str | None,
                                recovered_evidence: Path | str | None,
                                forbidden_paths: Sequence[Path | str] = ()) -> dict[str, Any]:
    """Read a recovered control ledger *read-only* and prove it belongs to this copy.

    ``--recovered-controls`` was accepted and never opened.  A controls copy is the half of the
    barrier that says *which* M0 bodies are forgotten, so accepting an unread one is the same as
    trusting a file by its name: a missing ledger, another owner's ledger or the live instance's
    own file would all be swept as if they carried the retained decisions.

    The file is never created, migrated or written here.  Every refusal happens before the
    caller touches an evidence body, a fence or the activation marker.
    """

    raw = Path(path)
    try:
        info = os.lstat(raw)
    except FileNotFoundError:
        raise RecoveryBlocked("RECOVERED_CONTROLS_MISSING",
                              "the named controls copy does not exist") from None
    except OSError as exc:
        raise RecoveryBlocked("RECOVERED_CONTROLS_UNREADABLE",
                              "%s: %s" % (type(exc).__name__, exc)) from exc
    if stat.S_ISLNK(info.st_mode):
        raise RecoveryBlocked("RECOVERED_CONTROLS_SYMLINK",
                              "the controls copy must be a real file, not a link")
    if not stat.S_ISREG(info.st_mode):
        raise RecoveryBlocked("RECOVERED_CONTROLS_NOT_A_FILE",
                              "the controls copy is not a regular file")
    resolved = raw.resolve()
    if recovery_dir is not None:
        try:
            resolved.relative_to(Path(recovery_dir).resolve())
        except ValueError:
            raise RecoveryBlocked(
                "RECOVERED_CONTROLS_OUTSIDE_COPY",
                "the controls copy is not inside the recovery directory being activated") from None
    for forbidden in forbidden_paths:
        if forbidden is None or str(forbidden) == "":
            continue
        try:
            live = Path(forbidden).resolve()
        except OSError:
            continue
        if resolved == live:
            raise RecoveryBlocked(
                "RECOVERED_CONTROLS_LIVE_PATH",
                "the controls copy is the live instance's own ledger")

    rows = _read_control_rows(resolved, owner=owner, conversation=conversation)
    targets = [str(row[0]) for row in rows]
    if targets:
        # A recovered control row must name an event the *recovered evidence* actually holds for
        # this owner.  That is what binds the ledger to this copy instead of to a sibling's.
        if not recovered_evidence or not Path(recovered_evidence).is_file():
            raise RecoveryBlocked("RECOVERED_CONTROLS_EVIDENCE_UNREADABLE",
                                  "controls claim rows but the recovered evidence is not readable")
        try:
            evidence = sqlite3.connect(Path(recovered_evidence).resolve().as_uri() + "?mode=ro",
                                       uri=True, timeout=2)
            try:
                for event_id in targets:
                    row = evidence.execute(
                        "SELECT 1 FROM evidence_events WHERE event_id=? AND conversation_id=? "
                        "AND memory_owner=?", (event_id, conversation, owner)).fetchone()
                    if row is None:
                        raise RecoveryBlocked(
                            "RECOVERED_CONTROLS_DANGLING",
                            "a control row names an event this recovered copy does not hold")
            finally:
                evidence.close()
        except RecoveryBlocked:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise RecoveryBlocked("RECOVERED_CONTROLS_EVIDENCE_UNREADABLE",
                                  "%s: %s" % (type(exc).__name__, exc)) from exc
    digest = _file_digest(resolved)
    return {"path": str(resolved), "digest": digest, "rows": len(rows),
            "identity": "VERIFIED" if rows else "EMPTY"}


def _read_control_rows(path: Path, *, owner: str, conversation: str) -> list[tuple[str]]:
    """Schema + identity of one controls file, opened ``mode=ro`` (never created)."""

    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RecoveryBlocked("RECOVERED_CONTROLS_CORRUPT", "integrity check failed")
            tables = {str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "memory_controls" not in tables:
                raise RecoveryBlocked("RECOVERED_CONTROLS_BAD_SCHEMA",
                                      "the file carries no memory_controls table")
            columns = {str(row[1]) for row in connection.execute(
                "PRAGMA table_info(memory_controls)")}
            if not CONTROL_COLUMNS.issubset(columns):
                raise RecoveryBlocked("RECOVERED_CONTROLS_BAD_SCHEMA",
                                      "memory_controls is missing required columns")
            rows: list[tuple[str]] = []
            for row in connection.execute(
                    "SELECT target_event, owner, conversation FROM memory_controls"):
                if str(row[1]) != str(owner) or str(row[2]) != str(conversation):
                    raise RecoveryBlocked(
                        "RECOVERED_CONTROLS_OWNER_MISMATCH",
                        "a control row belongs to another owner/conversation")
                rows.append((str(row[0]),))
            if "memory_erasures" in tables:
                columns = {str(row[1]) for row in connection.execute(
                    "PRAGMA table_info(memory_erasures)")}
                if not ERASURE_COLUMNS.issubset(columns):
                    raise RecoveryBlocked("RECOVERED_CONTROLS_BAD_SCHEMA",
                                          "memory_erasures is missing required columns")
                for row in connection.execute(
                        "SELECT owner, conversation FROM memory_erasures"):
                    if str(row[0]) != str(owner) or str(row[1]) != str(conversation):
                        raise RecoveryBlocked(
                            "RECOVERED_CONTROLS_OWNER_MISMATCH",
                            "an erasure row belongs to another owner/conversation")
            return rows
        finally:
            connection.close()
    except RecoveryBlocked:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise RecoveryBlocked("RECOVERED_CONTROLS_UNREADABLE",
                              "%s: %s" % (type(exc).__name__, exc)) from exc


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_incarnation(path: Path | str, kind: str, *, owner: str,
                     conversation: str) -> dict[str, Any]:
    """Read one owner's own incarnation stamp out of *this* file, read-only.

    The stamp is written by the owner of that database (``EvidenceStore`` /
    ``MemoryControlLedger`` / ``ManagedArtifactStore``).  This reader only proves, at open time,
    that the file being opened is still the one the activation swept -- a database restored from
    before the stamp carries no row (or an older generation) and is refused by name.
    """

    resolved = Path(path).resolve()
    try:
        connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=2)
    except (OSError, sqlite3.Error) as exc:
        raise RecoveryBlocked("RECOVERY_INCARNATION_MISSING",
                              "%s: %s: %s" % (kind, type(exc).__name__, exc)) from exc
    try:
        try:
            tables = {str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.Error as exc:
            raise RecoveryBlocked("RECOVERY_INCARNATION_MISSING",
                                  "%s: %s: %s" % (kind, type(exc).__name__, exc)) from exc
        if INCIDENT_TABLE not in tables:
            raise RecoveryBlocked("RECOVERY_INCARNATION_MISSING",
                                  "%s carries no recovery incarnation" % kind)
        row = connection.execute(
            "SELECT generation,owner,conversation FROM %s WHERE kind=?" % INCIDENT_TABLE,
            (str(kind),)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RecoveryBlocked("RECOVERY_INCARNATION_MISSING",
                              "%s carries no incarnation for this owner" % kind)
    if str(row[1]) != str(owner) or str(row[2]) != str(conversation):
        raise RecoveryBlocked("RECOVERY_INCARNATION_OWNER_MISMATCH",
                              "the %s incarnation belongs to another owner" % kind)
    return {"kind": str(kind), "generation": str(row[0])}


class RecoveryGate:
    """Mandatory activation gate for a recovered Memory/derived copy.

    The marker is written only by :func:`recovery_apply` after the retained authority's sweep
    has actually run over *that* copy.  Every read/new write on the recovered owner re-checks:

    * the marker exists and is ``OPEN`` for this owner/conversation;
    * the retained authority is still healthy and names the *same* head the activation swept
      (a later deletion advances the head and closes the copy again).

    A missing marker, an unreadable file, a mismatched owner or an authority that cannot be
    obtained is a named block -- never "no deletions".
    """

    def __init__(self, marker: Path | str, *, owner: str, conversation: str,
                 authority_db: Path | str | None = None,
                 minimum_seq: Optional[int] = None) -> None:
        self.marker = Path(marker)
        self.owner = str(owner)
        self.conversation = str(conversation)
        self.authority_db = Path(authority_db) if authority_db is not None else None
        self.minimum_seq = minimum_seq

    @classmethod
    def from_env(cls, environment: Optional[Mapping[str, str]] = None, *, owner: str,
                 conversation: str, authority_db: Path | str | None = None,
                 minimum_seq: Optional[int] = None) -> Optional["RecoveryGate"]:
        env = dict(environment or {})
        for name in RECOVERY_ENV:
            value = str(env.get(name) or "").strip()
            if value:
                return cls(Path(value) / RECOVERY_STATE_FILE, owner=owner,
                           conversation=conversation, authority_db=authority_db,
                           minimum_seq=minimum_seq)
        return None

    def state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.marker.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"status": "PENDING", "reason": "RECOVERY_NOT_RUN"}
        except (OSError, ValueError) as exc:
            return {"status": "BLOCKED", "reason": "RECOVERY_STATE_UNREADABLE",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        if not isinstance(data, dict) or data.get("status") != "OPEN":
            return {"status": "BLOCKED", "reason": "RECOVERY_STATE_NOT_OPEN"}
        if str(data.get("owner")) != self.owner or str(data.get("conversation")) != self.conversation:
            return {"status": "BLOCKED", "reason": "RECOVERY_STATE_OWNER_MISMATCH"}
        return {"status": "OPEN", **data}

    def ensure(self, operation: str) -> None:
        state = self.state()
        if state.get("status") != "OPEN":
            raise RecoveryBlocked(state.get("reason") or "RECOVERY_NOT_ACTIVATED",
                                  "operation=%s" % operation)
        if self.authority_db is None:
            raise RecoveryBlocked("RECOVERY_AUTHORITY_NOT_CONFIGURED",
                                  "operation=%s" % operation)
        authority = MemoryDeletionAuthority(self.authority_db, owner=self.owner,
                                            conversation=self.conversation,
                                            minimum_seq=self.minimum_seq)
        try:
            projection = authority.projection()
        except Exception as exc:
            raise RecoveryBlocked("RECOVERY_AUTHORITY_UNAVAILABLE",
                                  "%s: %s" % (type(exc).__name__, exc)) from exc
        if not projection.healthy:
            raise RecoveryBlocked(projection.reason or "RECOVERY_AUTHORITY_UNAVAILABLE",
                                  projection.detail or "")
        if int(projection.seq) != int(state.get("authority_seq", -1)) \
                or str(projection.head_digest or "") != str(state.get("authority_digest", "")):
            raise RecoveryBlocked(
                "RECOVERY_AUTHORITY_ADVANCED",
                "the retained decision set changed after this copy was activated")

    def verify_copy(self, *, evidence: Path | str | None = None,
                    controls: Path | str | None = None,
                    supply: Path | str | None = None,
                    projection: Path | str | None = None) -> None:
        """Refuse a file that is not the one *this* marker activated.

        The gate alone only proves "some copy was swept".  A service that is handed a marker
        but opens a different evidence/controls/supply file would serve an unswept body under a
        valid activation, so each half checks its own path against the marker before use.
        Fields the marker does not name (an older marker, a half without a recovered store) are
        left alone; a named field that disagrees is a definite block.
        """

        state = self.state()
        if state.get("status") != "OPEN":
            raise RecoveryBlocked(state.get("reason") or "RECOVERY_NOT_ACTIVATED",
                                  "operation=verify_copy")
        incarnations = state.get("incarnations")
        given_halves = [("evidence", evidence), ("controls", controls), ("supply", supply)]
        if any(given is not None and str(given) != "" for _kind, given in given_halves):
            # A marker written before incarnations existed proves only "some copy was swept".
            # It cannot tell the swept copy from an older backup restored onto the same path, so
            # it is refused rather than trusted for the half the caller is about to open.
            if not isinstance(incarnations, dict) or not incarnations:
                raise RecoveryBlocked(
                    "RECOVERY_INCARNATION_UNBOUND",
                    "this activation names no incarnations; re-run the formal recover")
        for key, given in (("recovered_evidence", evidence), ("recovered_controls", controls),
                           ("recovered_supply", supply),
                           ("recovered_projection", projection)):
            if given is None or str(given) == "":
                continue
            bound = str(state.get(key) or "").strip()
            if not bound:
                continue
            try:
                same = Path(given).resolve() == Path(bound).resolve()
            except OSError:
                same = False
            if not same:
                raise RecoveryBlocked(
                    "RECOVERY_COPY_MISMATCH",
                    "%s is not the copy this activation swept" % key)
        for kind, given in given_halves:
            if given is None or str(given) == "":
                continue
            bound_generation = str((incarnations or {}).get(kind) or "").strip()
            if not bound_generation:
                # The marker recorded no stamp for this half, but the caller is opening one:
                # an unbound half could be any file bearing the same name.
                raise RecoveryBlocked(
                    "RECOVERY_INCARNATION_UNBOUND",
                    "this activation carries no %s incarnation" % kind)
            seen = read_incarnation(given, kind, owner=self.owner,
                                    conversation=self.conversation)
            if seen["generation"] != bound_generation:
                raise RecoveryBlocked(
                    "RECOVERY_INCARNATION_MISMATCH",
                    "the %s database is not the one this activation swept" % kind)

    def activate(self, *, owner: str, conversation: str, projection: Any,
                 recovered_evidence: Path | str, erased_events: int = 0,
                 derived_artifacts: Sequence[str] = (),
                 recovered_controls: Optional[Mapping[str, Any]] = None,
                 recovered_supply: Path | str | None = None,
                 recovered_projection: Path | str | None = None,
                 incarnations: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
        if str(owner) != self.owner or str(conversation) != self.conversation:
            raise RecoveryBlocked("RECOVERY_STATE_OWNER_MISMATCH")
        if not recovered_controls:
            # The marker's contract is "this exact copy, swept under this exact barrier".  A
            # marker without the controls half would let the runtime accept the copy on evidence
            # alone, so the write itself is refused (not only the CLI that called it).
            raise RecoveryBlocked("RECOVERED_CONTROLS_REQUIRED",
                                  "an activation cannot be written without a bound ledger")
        stamps = {str(kind): str(value) for kind, value in (incarnations or {}).items()
                  if str(value or "")}
        required = ["evidence", "controls"] + (["supply"] if recovered_supply is not None else [])
        missing = [kind for kind in required if not stamps.get(kind)]
        if missing:
            # The marker must name the generations the sweep actually stamped.  A marker without
            # them would be exactly the old path-only activation that cannot tell a backup
            # restored onto the same path from the copy that was swept.
            raise RecoveryBlocked("RECOVERY_INCARNATION_REQUIRED",
                                  "no incarnation bound for: %s" % ",".join(sorted(missing)))
        payload = {
            "status": "OPEN",
            "owner": self.owner,
            "conversation": self.conversation,
            "authority_seq": int(getattr(projection, "seq", 0) or 0),
            "authority_digest": str(getattr(projection, "head_digest", "") or ""),
            "recovered_evidence": str(recovered_evidence),
            "erased_events": int(erased_events),
            "derived_artifacts": [str(item) for item in derived_artifacts],
            "incarnations": dict(sorted(stamps.items())),
            "activated_at": _now(),
        }
        if recovered_controls:
            # The marker names the exact ledger the sweep actually read, so a later open of a
            # *different* controls file cannot ride on this activation.
            payload["recovered_controls"] = str(recovered_controls.get("path") or "")
            payload["recovered_controls_digest"] = str(recovered_controls.get("digest") or "")
            payload["recovered_controls_identity"] = str(
                recovered_controls.get("identity") or "")
        if recovered_supply is not None:
            payload["recovered_supply"] = str(recovered_supply)
        if recovered_projection is not None:
            payload["recovered_projection"] = str(recovered_projection)
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.marker.with_name(".%s.tmp-%d" % (self.marker.name, os.getpid()))
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.marker)
            os.chmod(self.marker, 0o600)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise RecoveryBlocked("RECOVERY_ACTIVATION_WRITE_FAILED",
                                  "%s: %s" % (type(exc).__name__, exc)) from exc
        return payload


def recovery_apply(*, authority_db: Path | str, owner: str, conversation: str,
                   recovered_evidence: Path | str, authority_min_seq: Optional[int] = None,
                   supply_store: Any = None,
                   recovery_gate: Optional[RecoveryGate] = None,
                   recovered_controls: Path | str | None = None,
                   recovery_dir: Path | str | None = None,
                   forbidden_controls_paths: Sequence[Path | str] = ()) -> dict[str, Any]:
    """RECOVERY_ONLY sweep for a recovered Memory (and derived) copy, then activate it.

    A restore must not open product reads on a recovered body before the *retained* authority has
    been re-applied.  For every committed decision this function re-proves an owner grant, clears
    the recovered events inside that decision, and follows the derived owner's **own recorded
    source edges** to clear every artifact/version that was built from the barred sources.  It
    never initializes a fresh floor and never answers ``READY`` on an unproven authority.

    When a :class:`RecoveryGate` is supplied, the copy is only opened after every owner has
    acknowledged; the marker names the exact authority head that was swept, so a later deletion
    closes it again.  A sweep that fails half-way leaves the copy closed.

    A backup of the body still existing elsewhere is a retention decision the caller reports;
    this sweep only guarantees the *recovered live copy* cannot serve a forgotten body.
    """

    # Activation is a promise about a *barrier*: which M0 bodies were forgotten is the control
    # ledger's half.  A marker written without binding one would open the copy on evidence alone,
    # and every later service would accept it -- so an activation without a controls binding is
    # refused by name, not silently downgraded to the old "evidence-only" marker.
    if recovery_gate is not None and (recovered_controls is None
                                      or str(recovered_controls) == ""):
        return {"status": BLOCKED, "reason": "RECOVERED_CONTROLS_REQUIRED",
                "detail": "an activation must bind the control ledger it read"}
    # The controls half is read first: a copy sweep that cannot *prove* which bodies were
    # forgotten must not start erasing or writing a marker, so the ledger is opened read-only
    # and bound before the authority is consulted.
    controls_binding: Optional[dict[str, Any]] = None
    if recovered_controls is not None and str(recovered_controls) != "":
        try:
            controls_binding = recovered_controls_identity(
                recovered_controls, owner=owner, conversation=conversation,
                recovery_dir=recovery_dir if recovery_dir is not None
                else (Path(recovery_gate.marker).parent if recovery_gate is not None else None),
                recovered_evidence=recovered_evidence,
                forbidden_paths=forbidden_controls_paths)
        except RecoveryBlocked as exc:
            return {"status": BLOCKED, "reason": exc.code, "detail": exc.detail}

    authority = MemoryDeletionAuthority(authority_db, owner=owner, conversation=conversation,
                                        minimum_seq=authority_min_seq)
    try:
        projection = authority.projection()
    except Exception as exc:
        return {"status": BLOCKED, "reason": "MEMORY_AUTHORITY_UNAVAILABLE",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    if not projection.healthy:
        return {"status": BLOCKED, "reason": projection.reason, "detail": projection.detail}
    recovered = Path(recovered_evidence)
    from app.evidence import EvidenceStore
    erased_total = 0
    cases: list[dict[str, Any]] = []
    derived_artifacts: set[str] = set()
    derived_versions: set[str] = set()
    for record in projection.records:
        try:
            grant = authority.authorize(case_id=str(record.get("operation_id") or ""),
                                        event_ids=record.get("blocked_event_ids") or ())
        except Exception as exc:
            return {"status": BLOCKED, "reason": "RECOVERY_GRANT_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        artifacts: list[str] = []
        versions: list[str] = []
        if supply_store is not None:
            closure = supply_store.source_closure(getattr(grant, "sources", ()) or ())
            if closure.get("status") != "OK":
                return {"status": BLOCKED, "reason": "RECOVERY_CLOSURE_FAILED",
                        "detail": str(closure.get("detail") or closure.get("status"))}
            artifacts = list(closure.get("artifacts") or ())
            versions = list(closure.get("versions") or ())
            derived_artifacts.update(artifacts)
            derived_versions.update(versions)
        target_events = sorted(set(grant.event_ids))
        if target_events:
            try:
                sweep = EvidenceStore(recovered).erase_payload(
                    target_events, grant=grant,
                    operation_key="recovery:%s:%s"
                    % (record.get("seq"), str(record.get("digest"))[:16]))
            except Exception as exc:
                return {"status": BLOCKED, "reason": "RECOVERY_ERASE_FAILED",
                        "detail": "%s: %s" % (type(exc).__name__, exc)}
            if sweep.get("status") not in ("APPLIED", "NO_CHANGE"):
                return {"status": BLOCKED, "reason": "RECOVERY_ERASE_FAILED",
                        "detail": str(sweep.get("detail") or sweep.get("status"))}
            erased_total += int(sweep.get("erased") or 0)
        if supply_store is not None and (artifacts or versions):
            try:
                answer = supply_store.erase_source(
                    operation_key="recovery-supply:%s:%s"
                    % (record.get("seq"), str(record.get("digest"))[:16]),
                    authorized_subject_id=owner, grant=grant, authority=authority,
                    artifact_ids=artifacts, version_ids=versions)
            except Exception as exc:
                answer = {"status": "FAILED",
                          "detail": "%s: %s" % (type(exc).__name__, exc)}
            if answer.get("status") not in ("APPLIED", "DUPLICATE"):
                return {"status": BLOCKED, "reason": "RECOVERY_DERIVED_ERASE_FAILED",
                        "detail": str(answer.get("detail") or answer.get("status"))}
        cases.append({"seq": int(record.get("seq") or 0),
                      "events": len(target_events),
                      "artifacts": len(artifacts), "versions": len(versions)})
    # A projection is a representation, not a second authority.  Before the copy is reopened,
    # every *registered* projection is re-derived from the swept canonical state, so a
    # pre-deletion file put back onto the same path cannot linger as private text.  Unregistered
    # files are never guessed at, and a registered binding that cannot be rewritten is a named
    # block instead of a silent skip.
    projection_reconcile: Optional[dict[str, Any]] = None
    if supply_store is not None:
        reconcile = getattr(supply_store, "reconcile_registered_projections", None)
        if callable(reconcile):
            try:
                projection_reconcile = reconcile()
            except Exception as exc:
                return {"status": BLOCKED, "reason": "RECOVERY_PROJECTION_RECONCILE_FAILED",
                        "detail": "%s: %s" % (type(exc).__name__, exc)}
            if str(projection_reconcile.get("status") or "") not in ("OK", "NOT_CONFIGURED"):
                return {"status": BLOCKED,
                        "reason": str(projection_reconcile.get("reason")
                                      or "RECOVERY_PROJECTION_RECONCILE_FAILED"),
                        "detail": "registered projections were not rebuilt from canonical state"}
    # Each owner stamps its own database with a generation chosen for *this* activation.  The
    # marker then names those generations, so a backup restored onto the same path later is a
    # different incarnation even though every path still matches.
    incarnations: dict[str, str] = {}
    if recovery_gate is not None:
        generation = lambda: hashlib.sha256(os.urandom(32)).hexdigest()[:32]  # noqa: E731
        try:
            incarnations["evidence"] = EvidenceStore(recovered).stamp_recovery_incarnation(
                owner=owner, conversation=conversation, authority_seq=int(projection.seq),
                authority_digest=str(projection.head_digest or ""), generation=generation())
            controls_path = recovered_controls
            from memory_control import MemoryControlLedger
            incarnations["controls"] = MemoryControlLedger(
                controls_path, recovered, owner=owner,
                conversation=conversation).stamp_recovery_incarnation(
                    authority_seq=int(projection.seq),
                    authority_digest=str(projection.head_digest or ""), generation=generation())
            if supply_store is not None:
                incarnations["supply"] = supply_store.stamp_recovery_incarnation(
                    owner=owner, conversation=conversation,
                    authority_seq=int(projection.seq),
                    authority_digest=str(projection.head_digest or ""), generation=generation())
        except Exception as exc:
            # A half-stamped copy is still closed: the marker is written only after every half
            # has its own incarnation, so no partial cleanup can be activated.
            return {"status": BLOCKED, "reason": "RECOVERY_INCARNATION_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
    activation = None
    if recovery_gate is not None:
        try:
            activation = recovery_gate.activate(
                owner=owner, conversation=conversation, projection=projection,
                recovered_evidence=recovered, erased_events=erased_total,
                derived_artifacts=sorted(derived_artifacts),
                incarnations=incarnations,
                recovered_controls=controls_binding,
                recovered_supply=(getattr(supply_store, "db_path", None)
                                  if supply_store is not None else None),
                recovered_projection=(getattr(supply_store, "projection_root", None)
                                      if supply_store is not None else None))
        except RecoveryBlocked as exc:
            return {"status": BLOCKED, "reason": exc.code, "detail": exc.detail}
        except Exception as exc:
            return {"status": BLOCKED, "reason": "RECOVERY_ACTIVATION_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"status": "RECOVERED_OPEN" if activation else "RECOVERY_ONLY_APPLIED",
            "barrier_seq": int(projection.seq),
            "erased_events": erased_total,
            "recovered_controls": controls_binding,
            "blocked_sources": sorted([list(key) for key in projection.blocked_sources]),
            "incarnations": dict(sorted(incarnations.items())),
            "projection_reconcile": projection_reconcile,
            "cases": cases,
            "derived": {"artifacts": sorted(derived_artifacts),
                        "versions": sorted(derived_versions)},
            "activation": activation}


def forget_case_path_from_env(environment: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    env = os.environ if environment is None else environment
    for name in FORGET_CASE_ENV:
        value = str(env.get(name) or "").strip()
        if value:
            return Path(value)
    return None


def gate_from_env(environment: Optional[Mapping[str, str]] = None) -> Optional[ForgetGate]:
    """The gate for an instance that explicitly configured a forget coordination database.

    Unconfigured instances keep their previous behaviour.  A configured but unusable binding
    returns a gate whose ``state()`` is unhealthy, so callers fail closed.
    """

    env = os.environ if environment is None else environment
    case_path = forget_case_path_from_env(env)
    if case_path is None:
        return None
    database = _first(env, ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB"))
    controls = _first(env, ("NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB"))
    authority = _first(env, ("NYA02_MEMORY_AUTHORITY_DB", "CHIYO_LIFE_MEMORY_AUTHORITY_DB"))
    owner = _first(env, ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT")) or "chiyo"
    conversation = "life:" + owner
    minimum: Optional[int] = None
    raw = _first(env, ("NYA02_MEMORY_AUTHORITY_MIN_SEQ", "CHIYO_LIFE_MEMORY_AUTHORITY_MIN_SEQ"))
    if raw:
        try:
            minimum = int(raw)
        except ValueError:
            minimum = None
    return ForgetGate(ForgetCaseStore(case_path, evidence_db=database or "",
                                      controls_db=controls or database or "",
                                      authority_db=authority or None, owner=owner,
                                      conversation=conversation,
                                      authority_min_seq=minimum))


def _first(environment: Mapping[str, str], names: Sequence[str]) -> str:
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return value
    return ""


__all__ = [
    "BARRIER_APPLIED", "BLOCKED", "COMPLETE", "ForgetCaseStore", "ForgetGate",
    "ForgetRefused", "HELD", "OWNER_PENDING", "RECEIVED", "SOURCE_AUTHORITY_CONTRACT",
    "SOURCE_TOKEN_PREFIX", "WAITING_RETENTION", "forget_case_path_from_env", "gate_from_env",
    "source_token",
]
