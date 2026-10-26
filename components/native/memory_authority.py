"""Durable, locally-retained deletion authority for explicitly managed Memory.

Why a second local ledger exists
--------------------------------
M0 is append-only and the control ledger is a local projection. Restoring an older
``evidence.db`` together with an older ``controls.db`` used to revive a deleted experience:
the deletion decision lived only inside the control file that was rolled back. This module
keeps deletion decisions in a *separate* file, written **before** the control ledger, so an
older evidence/control pair cannot silently outvote a committed deletion.

What a healthy read proves -- and what it does not
--------------------------------------------------
The file carries an append-only hash chain plus a single head row. A healthy read proves the
chain is internally consistent and complete up to its head. It does **not** prove the file is
the newest copy on the machine: if the authority file itself is rolled back too, the older
deletion set is what remains. Path separation and the hash chain are integrity evidence, never
a freshness proof.

Currentness is therefore a separate, explicit handoff: the operator keeps the file outside the
instance's own backup/restore set and hands off the *trusted current base ledger* as a minimum
sequence. The normal flow keeps that floor itself: every successful ``DELETE`` commit advances a
minimal retained handoff file (``<authority>.floor.json``, same retained directory) *before* the
append lands, so an operator never edits a number per deletion and an older authority copy falls
below the floor. A chain-valid copy below that floor is an older copy and is refused; with no
handoff at all currentness is unproven and every read/new deletion is refused. Moving the file to
another directory on the same machine only removes it from the *instance* restore set -- it does
not survive a whole-machine rollback, which is exactly why the external floor is required.
``NYA02_MEMORY_AUTHORITY_MIN_SEQ`` remains available as an operator-supplied floor (CLI prints
``seq`` and ``head_digest``); the effective floor is the larger of the two.

The floor file is *derived from no data being checked*: it is written from the seq/digest this
process is about to commit, never read back out of the authority's own head to bless it. If the
floor cannot be read or updated, reads/new deletions are refused instead of trusting an old copy.

A missing, unreadable, inconsistent or unproven file fails closed: readers block and new
deletions are refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Optional

#: The fourth Memory name, alongside the three the resident already reads.  Kept here as well as
#: in the product seams so a caller can resolve it without importing the phone or Life modules.
AUTHORITY_ENV = ("NYA02_MEMORY_AUTHORITY_DB", "CHIYO_LIFE_MEMORY_AUTHORITY_DB")

#: The explicit handoff of the trusted current base ledger: at least this many committed
#: decisions.  A chain-valid copy with a smaller head is an older copy, not a current one.
AUTHORITY_MIN_SEQ_ENV = ("NYA02_MEMORY_AUTHORITY_MIN_SEQ", "CHIYO_LIFE_MEMORY_AUTHORITY_MIN_SEQ")

#: The retained floor file the normal DELETE flow advances by itself.  Optional override; the
#: default is the minimal sibling ``<authority>.floor.json`` in the same retained directory.
AUTHORITY_FLOOR_ENV = ("NYA02_MEMORY_AUTHORITY_FLOOR", "CHIYO_LIFE_MEMORY_AUTHORITY_FLOOR")

#: Genesis link of the chain.  Stable: every record hashes over the previous link.
GENESIS_DIGEST = hashlib.sha256(b"nyairo-memory-authority-v1").hexdigest()

REASON_MISSING = "MEMORY_AUTHORITY_DB_MISSING"
REASON_NOT_A_FILE = "MEMORY_AUTHORITY_DB_NOT_A_FILE"
REASON_UNREADABLE = "MEMORY_AUTHORITY_DB_UNREADABLE"
REASON_INTEGRITY = "MEMORY_AUTHORITY_INTEGRITY_FAILURE"
REASON_SCHEMA = "MEMORY_AUTHORITY_SCHEMA_MISMATCH"
REASON_CHAIN = "MEMORY_AUTHORITY_CHAIN_INVALID"
REASON_HEAD = "MEMORY_AUTHORITY_HEAD_INVALID"
REASON_UNAVAILABLE = "MEMORY_AUTHORITY_UNAVAILABLE"
#: No trusted base ledger was handed off, so a chain-valid file cannot be shown to be current.
REASON_CURRENTNESS = "MEMORY_AUTHORITY_CURRENTNESS_UNPROVEN"
#: The file is older than the handed-off base: it is a rolled-back copy, however valid its chain.
REASON_OLDER = "MEMORY_AUTHORITY_OLDER_THAN_TRUSTED"
#: The retained handoff file cannot be read, parsed or updated: currentness cannot be shown.
REASON_HANDOFF = "MEMORY_AUTHORITY_HANDOFF_INVALID"


class MemoryAuthorityUnavailable(RuntimeError):
    """The retained deletion authority cannot establish its own state.

    A configured authority that cannot be read is never treated as "no deletions": a caller
    must refuse the read (and any new deletion) rather than fall back to the older controls.
    """

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(str(code))
        self.code = str(code)
        self.detail = detail


def resolve_authority_path(controls: Path | str,
                           environment: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """Only the explicitly configured name; a file merely sitting beside the control ledger is
    never adopted.

    ``controls`` stays in the signature so callers keep one resolution point, but it is not used
    to guess a path: the retained base ledger has no default and no sibling convention.
    """

    del controls  # deliberately unused: no implicit location
    return authority_path_from_env(environment)


def authority_path_from_env(environment: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The configured authority path for this process/instance, or ``None`` when unconfigured.

    An unconfigured instance keeps the legacy path: it is never adopted automatically, no file
    is created, and no reading is blocked on its behalf.
    """

    env = os.environ if environment is None else environment
    for name in AUTHORITY_ENV:
        value = str(env.get(name) or "").strip()
        if value:
            return Path(value)
    return None


def authority_min_seq_from_env(environment: Optional[Mapping[str, str]] = None) -> Optional[int]:
    """The handed-off trusted floor, or ``None`` when currentness cannot be established.

    A malformed value is also ``None``: "cannot confirm" must never degrade into "trust an
    arbitrary copy", so the caller blocks instead of guessing zero.
    """

    env = os.environ if environment is None else environment
    for name in AUTHORITY_MIN_SEQ_ENV:
        value = str(env.get(name) or "").strip()
        if not value:
            continue
        try:
            return max(0, int(value))
        except ValueError:
            return None
    return None


def resolve_floor_path(authority_path: Path | str,
                       environment: Optional[Mapping[str, str]] = None) -> Path:
    """Where the normal flow retains its floor: the configured override, else a sibling file.

    The sibling lives in the same retained directory as the authority (outside the instance's
    backup/restore set).  It is a separate file so rolling the authority database back — without
    also rolling back the retained directory — leaves the newer floor in place and refuses the
    copy.  It is never derived from the authority's contents.
    """

    env = os.environ if environment is None else environment
    for name in AUTHORITY_FLOOR_ENV:
        value = str(env.get(name) or "").strip()
        if value:
            return Path(value)
    return Path(str(authority_path) + ".floor.json")


def read_floor_file(path: Path) -> Optional[FloorState]:
    """The retained floor, ``None`` when the file is absent, or a named refusal if malformed."""

    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MemoryAuthorityUnavailable(
            REASON_HANDOFF, "cannot read the retained handoff: %s: %s"
            % (type(exc).__name__, exc))
    try:
        payload = json.loads(raw)
        seq = int(payload["seq"])
        if seq < 0:
            raise ValueError("negative seq")
        digest = payload.get("head_digest")
        digest = str(digest) if digest else None
        updated_at = payload.get("updated_at")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise MemoryAuthorityUnavailable(
            REASON_HANDOFF, "the retained handoff is malformed: %s: %s"
            % (type(exc).__name__, exc))
    return FloorState(seq=seq, head_digest=digest,
                      updated_at=str(updated_at) if updated_at else None)


@dataclass
class FloorState:
    """The trusted floor this process could establish, and where it came from."""

    seq: int
    head_digest: Optional[str] = None
    updated_at: Optional[str] = None
    source: str = "retained_floor"


@dataclass
class AuthorityProjection:
    healthy: bool = False
    reason: Optional[str] = None
    detail: Optional[str] = None
    blocked_events: set[str] = field(default_factory=set)
    blocked_turns: set[str] = field(default_factory=set)
    operations: int = 0
    seq: int = 0
    head_digest: Optional[str] = None
    latest_created_at: Optional[str] = None
    #: The floor actually applied (``None`` when currentness is unproven) and its origin.
    trusted_floor: Optional[int] = None
    floor_source: Optional[str] = None

    def update_reason(self, reason: str, detail: Optional[str]) -> None:
        self.healthy = False
        self.reason = reason
        self.detail = detail


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _record_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    blocked = row["blocked_event_ids"]
    if isinstance(blocked, str):
        blocked = json.loads(blocked)
    return {
        "seq": int(row["seq"]),
        "operation_id": str(row["operation_id"]),
        "owner": str(row["owner"]),
        "conversation": str(row["conversation"]),
        "target_event": str(row["target_event"]),
        "turn_id": str(row["turn_id"]) if row["turn_id"] is not None else None,
        "blocked_event_ids": sorted(str(item) for item in (blocked or [])),
        "fingerprint": str(row["fingerprint"]),
        "created_at": str(row["created_at"]),
    }


class MemoryDeletionAuthority:
    """Append-only local deletion authority with an explicit, verifiable head."""

    def __init__(self, path: Path | str, *, owner: str, conversation: str,
                 minimum_seq: Optional[int] = None,
                 floor_path: Optional[Path | str] = None) -> None:
        if not owner or not conversation:
            raise ValueError("explicit owner and conversation are required")
        self.path = Path(path)
        self.owner = str(owner)
        self.conversation = str(conversation)
        #: Handed-off trusted floor.  ``None`` means currentness is unproven, not "zero".
        self.minimum_seq = None if minimum_seq is None else max(0, int(minimum_seq))
        #: The automatically advanced retained floor, re-read on every projection so a long-lived
        #: port sees the floor a DELETE advanced after it was constructed.
        self.floor_path = (Path(floor_path) if floor_path is not None
                           else resolve_floor_path(self.path))

    # -- write side ----------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the file, its two tables and the genesis head.  Explicit opt-in only.

        A *genuinely new, empty* base also gets its retained floor at seq 0, so the first DELETE
        needs no hand-edited number.  An already existing file without a floor is left without
        one: initializing zero there would bless a history nobody can vouch for.
        """

        existed = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript('''
              CREATE TABLE IF NOT EXISTS authority_records(
                seq INTEGER PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,
                conversation TEXT NOT NULL,
                target_event TEXT NOT NULL,
                turn_id TEXT,
                blocked_event_ids TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                digest TEXT NOT NULL);
              CREATE TRIGGER IF NOT EXISTS authority_no_update BEFORE UPDATE ON authority_records
              BEGIN SELECT RAISE(ABORT,'memory deletion authority is append-only'); END;
              CREATE TRIGGER IF NOT EXISTS authority_no_delete BEFORE DELETE ON authority_records
              BEGIN SELECT RAISE(ABORT,'memory deletion authority is append-only'); END;
              CREATE TABLE IF NOT EXISTS authority_head(
                id INTEGER PRIMARY KEY CHECK(id=1),
                seq INTEGER NOT NULL,
                digest TEXT NOT NULL);
            ''')
            row = connection.execute("SELECT seq FROM authority_head WHERE id=1").fetchone()
            if row is None:
                connection.execute("INSERT INTO authority_head(id,seq,digest) VALUES(1,0,?)",
                                   (GENESIS_DIGEST,))
                row = (0,)
            head_seq = int(row[0])
            records = connection.execute(
                "SELECT count(*) FROM authority_records").fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        os.chmod(self.path, 0o600)
        # Zero baseline only for a brand-new empty base.  Never overwrite an existing floor.
        if not existed and head_seq == 0 and int(records) == 0 and not self.floor_path.exists():
            self._write_floor(0, GENESIS_DIGEST)

    def commit(self, *, operation_id: str, target_event: str, turn_id: Optional[str],
               blocked_event_ids: list[str], fingerprint: str) -> str:
        """Append one deletion decision.  Returns ``APPLIED`` or ``DUPLICATE``.

        The whole existing chain is verified first.  A configured authority that is missing,
        corrupt or unverifiable refuses the write -- a deletion that cannot be durably recorded
        must not be reported as successful.
        """

        if not operation_id or not target_event or not fingerprint:
            raise ValueError("invalid memory deletion authority record")
        state = self.projection()
        if not state.healthy:
            raise MemoryAuthorityUnavailable(state.reason or REASON_UNAVAILABLE, state.detail)
        connection = sqlite3.connect(self.path, timeout=3)
        try:
            connection.execute("BEGIN IMMEDIATE")
            head = connection.execute(
                "SELECT seq,digest FROM authority_head WHERE id=1").fetchone()
            if head is None:
                raise MemoryAuthorityUnavailable(REASON_HEAD, "the head row is absent")
            existing = connection.execute(
                "SELECT fingerprint FROM authority_records WHERE operation_id=?",
                (operation_id,)).fetchone()
            if existing is not None:
                if str(existing[0]) != str(fingerprint):
                    raise ValueError("operation identity collision")
                connection.rollback()
                return "DUPLICATE"
            seq = int(head[0]) + 1
            created_at = datetime.now(timezone.utc).isoformat()
            record = {
                "seq": seq,
                "operation_id": str(operation_id),
                "owner": self.owner,
                "conversation": self.conversation,
                "target_event": str(target_event),
                "turn_id": str(turn_id) if turn_id is not None else None,
                "blocked_event_ids": sorted({str(item) for item in blocked_event_ids if item}),
                "fingerprint": str(fingerprint),
                "created_at": created_at,
            }
            digest = hashlib.sha256(
                str(head[1]).encode("utf-8") + b"\n" + _canonical(record)).hexdigest()
            # Retain the new floor *before* the append lands: if either side fails, currentness
            # is unproven (reads block) rather than an older copy being trusted.  The value is
            # this commit's own seq/digest, never taken from the authority's existing head.
            self._write_floor(seq, digest, created_at=created_at)
            connection.execute(
                "INSERT INTO authority_records(seq,operation_id,owner,conversation,target_event,"
                "turn_id,blocked_event_ids,fingerprint,created_at,digest) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (seq, record["operation_id"], record["owner"], record["conversation"],
                 record["target_event"], record["turn_id"],
                 json.dumps(record["blocked_event_ids"], separators=(",", ":")),
                 record["fingerprint"], created_at, digest))
            connection.execute("UPDATE authority_head SET seq=?,digest=? WHERE id=1",
                               (seq, digest))
            connection.commit()
            return "APPLIED"
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _write_floor(self, seq: int, head_digest: str,
                     *, created_at: Optional[str] = None) -> None:
        """Atomically retain the new trusted floor (0600, fsync, replace)."""

        path = self.floor_path
        payload = {"seq": int(seq), "head_digest": str(head_digest),
                   "updated_at": created_at or datetime.now(timezone.utc).isoformat()}
        tmp = path.with_name(".%s.tmp-%d" % (path.name, os.getpid()))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise MemoryAuthorityUnavailable(
                REASON_HANDOFF, "cannot retain the trusted floor: %s: %s"
                % (type(exc).__name__, exc))

    # -- read side -----------------------------------------------------------------------

    @staticmethod
    def _readonly(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def projection(self) -> AuthorityProjection:
        """The deletion set this authority can prove, or a named unhealthy state."""

        out = AuthorityProjection()
        try:
            info = self.path.lstat()
        except OSError:
            out.update_reason(REASON_MISSING,
                              "the configured deletion authority does not exist: %s" % (self.path,))
            return out
        import stat as _stat

        if not _stat.S_ISREG(info.st_mode):
            out.update_reason(REASON_NOT_A_FILE,
                              "the configured deletion authority is not a regular file: %s"
                              % (self.path,))
            return out
        try:
            connection = self._readonly(self.path)
        except Exception as exc:
            out.update_reason(REASON_UNREADABLE, "%s: %s" % (type(exc).__name__, exc))
            return out
        try:
            # One read snapshot: a concurrent commit must not make the head and the rows
            # disagree and turn a healthy chain into a false "invalid".
            connection.execute("BEGIN")
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if integrity.lower() != "ok":
                out.update_reason(REASON_INTEGRITY,
                                  "PRAGMA quick_check did not return ok (%r)" % (integrity,))
                return out
            tables = {str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if not {"authority_records", "authority_head"} <= tables:
                out.update_reason(REASON_SCHEMA,
                                  "the configured file has no authority_records/authority_head")
                return out
            head = connection.execute(
                "SELECT seq,digest FROM authority_head WHERE id=1").fetchone()
            if head is None:
                out.update_reason(REASON_HEAD, "the head row is absent")
                return out
            head_seq, head_digest = int(head[0]), str(head[1])
            rows = connection.execute(
                "SELECT * FROM authority_records ORDER BY seq").fetchall()
        except (sqlite3.Error, ValueError) as exc:
            out.update_reason(REASON_UNREADABLE, "%s: %s" % (type(exc).__name__, exc))
            return out
        finally:
            connection.close()

        if len(rows) != head_seq:
            out.update_reason(REASON_CHAIN,
                              "the chain has %d records but its head claims %d"
                              % (len(rows), head_seq))
            return out
        previous = GENESIS_DIGEST
        for index, row in enumerate(rows, start=1):
            if int(row["seq"]) != index:
                out.update_reason(REASON_CHAIN, "the chain is not contiguous at seq %d" % (index,))
                return out
            try:
                payload = _record_payload(row)
            except (ValueError, TypeError, KeyError) as exc:
                out.update_reason(REASON_CHAIN, "record %d is malformed: %s" % (index, exc))
                return out
            expected = hashlib.sha256(
                previous.encode("utf-8") + b"\n" + _canonical(payload)).hexdigest()
            if expected != str(row["digest"]):
                out.update_reason(REASON_CHAIN, "the chain digest does not verify at seq %d"
                                  % (index,))
                return out
            previous = expected
            if payload["owner"] != self.owner or payload["conversation"] != self.conversation:
                continue
            out.operations += 1
            out.blocked_events.update(payload["blocked_event_ids"])
            if payload["turn_id"]:
                out.blocked_turns.add(payload["turn_id"])
            if out.latest_created_at is None or payload["created_at"] > out.latest_created_at:
                out.latest_created_at = payload["created_at"]
        if previous != head_digest:
            out.update_reason(REASON_CHAIN, "the head digest does not match the chain tail")
            return out
        # A valid chain is integrity only.  Currentness comes from the retained floor the normal
        # flow advanced (plus any operator-supplied floor); without one (or when this file is
        # older than it) a rolled-back copy must not be allowed to outvote the deletion it lost.
        floors: list[FloorState] = []
        if self.minimum_seq is not None:
            floors.append(FloorState(seq=self.minimum_seq, source="operator_min_seq"))
        try:
            retained = read_floor_file(self.floor_path)
        except MemoryAuthorityUnavailable as exc:
            out.update_reason(exc.code, exc.detail)
            return out
        if retained is not None:
            if retained.head_digest and 0 < retained.seq <= len(rows):
                if str(rows[retained.seq - 1]["digest"]) != retained.head_digest:
                    out.update_reason(
                        REASON_HANDOFF,
                        "the retained floor at seq %d names a different chain than this file"
                        % (retained.seq,))
                    return out
            floors.append(retained)
        if not floors:
            out.update_reason(REASON_CURRENTNESS,
                              "no trusted current base ledger was handed off for this authority; "
                              "a chain-valid file is not proof that it is the newest copy")
            return out
        floor = max(floors, key=lambda item: item.seq)
        if head_seq < floor.seq:
            out.update_reason(REASON_OLDER,
                              "this authority holds %d decisions but the retained trusted floor "
                              "holds at least %d: it is an older copy"
                              % (head_seq, floor.seq))
            return out
        out.healthy = True
        out.trusted_floor = floor.seq
        out.floor_source = floor.source
        out.seq = head_seq
        out.head_digest = head_digest
        return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a Memory deletion authority.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--conversation", required=True)
    parser.add_argument("--commit", nargs=4, metavar=("OPERATION_ID", "TARGET_EVENT", "TURN_ID",
                                                      "FINGERPRINT"),
                        help="append one deletion decision (hashes the target group)")
    parser.add_argument("--blocked-event", action="append", default=[],
                        help="an event id the deletion covers (repeatable)")
    parser.add_argument("--min-seq", type=int, default=None,
                        help="the handed-off trusted floor; without it currentness is unproven")
    args = parser.parse_args()
    authority = MemoryDeletionAuthority(args.db, owner=args.owner,
                                        conversation=args.conversation,
                                        minimum_seq=args.min_seq)
    if args.commit:
        operation_id, target, turn_id, fingerprint = args.commit
        blocked = list(args.blocked_event) or [target]
        print(json.dumps({"status": authority.commit(
            operation_id=operation_id, target_event=target,
            turn_id=None if turn_id in ("", "-") else turn_id,
            blocked_event_ids=blocked, fingerprint=fingerprint)}))
        return
    projection = authority.projection()
    print(json.dumps({
        "healthy": projection.healthy,
        "reason": projection.reason,
        "detail": projection.detail,
        "operations": projection.operations,
        "seq": projection.seq,
        "minimum_seq": authority.minimum_seq,
        "trusted_floor": projection.trusted_floor,
        "floor_source": projection.floor_source,
        "head_digest": projection.head_digest,
        "blocked_events": sorted(projection.blocked_events),
        "blocked_turns": sorted(projection.blocked_turns),
        "latest_created_at": projection.latest_created_at,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
