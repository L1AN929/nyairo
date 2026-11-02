"""ManagedArtifact C5 v1 store reconstructed from the user's explicit handoff contract.

This is NOT recovered B3 source. Contract definition lives in contract.py. Writes must
be invoked through a service that reserves COMMIT_MANAGED_ARTIFACT in Governance first.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from lifesupply.sqlite_safety import configure_journal
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

from lifesupply.artifact import album as album_media
from lifesupply.fencing import (
    WriterFence,
    activate_writer_epoch,
    install_write_guards,
    register_writer_connection,
    require_schema_columns,
    reject_incompatible_existing_schema,
)

#: The owner-parameterised writer functions a *single-database* file's guards call.  An
#: operator-side store opening that file must register them too, or every guarded write fails.
_OWNER_FENCE_EPOCH_FN = "c6_writer_epoch_for"
_OWNER_FENCE_INSTANCE_FN = "c6_writer_instance_for"
_OWNER_FENCE_TOKEN_FN = "c6_writer_token_for"

OUTCOMES = frozenset({"OK", "NO_CHANGE", "DENY", "CONFLICT", "BLOCKED", "NOT_FOUND", "UNKNOWN"})

#: One Markdown projection is a representation, not a second copy of the work.  The bound is the
#: owner's own read limit: a file larger than this is refused as an unusable representation rather
#: than pulled into memory.  256 KiB is far above any ordinary note yet bounded and explicit.
MAX_PROJECTION_BYTES = 256 * 1024

#: Test/demo-only sentinel: ``None`` is a meaningful override ("pretend there is no head"), so a
#: separate object distinguishes "no injection" from "inject no head".
_UNSET = object()


def _projection_rel_is_safe(rel: Any) -> bool:
    """A binding path must stay a *relative path inside* the projection root.

    Absolute paths, ``..`` segments and NUL bytes are refused before any file-system call, so a
    binding can never name a file outside the managed workspace.
    """
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        return False
    if rel.startswith("/") or rel.startswith("\\"):
        return False
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if not parts:
        return False
    return all(part not in {"", ".", ".."} and not part.startswith("/") for part in parts)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> float:
    return time.time()


class ArtifactStoreClosed(RuntimeError):
    """A recovered copy is not activated, so no body may be read or written.

    It carries the gate's own named reason code, so the socket layer can answer a definite
    ``BLOCKED`` instead of mislabelling a closed copy as an unknown effect.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(str(code))
        self.code = str(code)
        self.detail = str(detail)


class ManagedArtifactStore:
    """SQLite authority for identities, immutable versions, operations, receipts and bindings."""

    def __init__(self, db_path: str | Path | None = None, *, projection_root: str | Path | None = None,
                 media_root: str | Path | None = None,
                 workspace_authority: Any = None, clock=_now, writer_fence: Any = None,
                 read_only: bool = False, db: Any = None, recovery_gate: Any = None,
                 fences_root: str | Path | None = None,
                 writer_instance: str | None = None):
        if db is None and db_path is None:
            raise ValueError("ManagedArtifactStore requires db_path or db")
        self.db = db
        self.db_path = str(db_path) if db_path is not None else str(db.path)
        self.projection_root = Path(projection_root) if projection_root else None
        #: Album picture bytes live beside the owner's own database, never in a repo data path.
        #: The path is derived by the caller from the service root; ``None`` keeps every old
        #: construction valid and simply means "this owner has no media directory".
        self.media_root = Path(media_root) if media_root else None
        self.workspace_authority = workspace_authority
        self.clock = clock
        #: An operator-side writer (the forget CLI) opens the *same* canonical file the service
        #: owns.  Passing ``fences_root`` makes it take the owner's real writer fence -- the same
        #: flock the service holds -- so an offline erase either wins the fence or is refused by
        #: name, and the single-database guards see a matching epoch/token.
        self._owns_fence = False
        if writer_fence is None and fences_root and not read_only:
            writer_fence = WriterFence("ManagedArtifact", Path(fences_root),
                                       instance_id=writer_instance or None)
            self._owns_fence = True
        self.writer_fence = writer_fence
        self.read_only = read_only
        self._merged_layout_cache: bool | None = None
        #: An activation gate for a *recovered* copy.  ``None`` keeps every ordinary store as it
        #: was; a recovered store refuses bodies until the retained authority's sweep has
        #: actually reopened it (see ``forget_request.RecoveryGate``).
        self.recovery_gate = recovery_gate
        if db is not None:
            # C7 single database: the DB owns the schema, the epochs and the guards.
            if not read_only and self.projection_root:
                self.projection_root.mkdir(parents=True, exist_ok=True)
            if not read_only and self.media_root:
                self.media_root.mkdir(parents=True, exist_ok=True)
            self._verify_schema()
        elif not read_only:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            reject_incompatible_existing_schema(self.db_path, "ManagedArtifact", "managed_artifacts", {
                "managed_artifacts": {"writer_owner", "writer_instance", "writer_epoch"},
                "artifact_versions": {"version_id", "operation_key", "content_sha256"},
                "artifact_operations": {"operation_key", "request_digest", "result_json"},
                "artifact_projection_outbox": {"artifact_id", "expected_sha256", "state"},
                "c6_writer_epoch": {"active_epoch", "instance_id", "writer_token"}})
            if self.projection_root:
                self.projection_root.mkdir(parents=True, exist_ok=True)
            if self.media_root:
                self.media_root.mkdir(parents=True, exist_ok=True)
            self._init_db()
        else:
            self._verify_schema()

    def _verify_schema(self) -> None:
        with self._connection() as con:
            require_schema_columns(con, "ManagedArtifact", {
                "managed_artifacts": {"writer_owner", "writer_instance", "writer_epoch"},
                "artifact_versions": {"version_id", "operation_key", "content_sha256"},
                "artifact_operations": {"operation_key", "request_digest", "result_json"},
                "artifact_projection_outbox": {"artifact_id", "expected_sha256", "state"},
                "c6_writer_epoch": {"active_epoch", "instance_id", "writer_token"},
            })

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self.db is not None:
            active = self.db.active_unit_of_work
            if active is not None:
                # C7: join the coordinated transaction instead of opening our own.
                yield active.join()
                return
            con = self.db._open()
            try:
                yield con
            finally:
                con.close()
            return
        uri = f"file:{self.db_path}?mode=ro" if self.read_only else self.db_path
        con = sqlite3.connect(uri, uri=self.read_only, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        register_writer_connection(con, self.writer_fence)
        if self._is_merged_layout(con):
            # The single-database file's guards call the owner-parameterised functions.  With no
            # fence the store is a *reader*; epoch -1 makes any guarded write abort by name
            # instead of silently succeeding outside the writer's lock.
            epoch = int(self.writer_fence.assert_current()) if self.writer_fence else -1
            instance_id = (self.writer_fence.instance_id if self.writer_fence
                           else "unfenced-operator")
            token = (self.writer_fence.writer_token if self.writer_fence else "unfenced-operator")
            con.create_function(_OWNER_FENCE_EPOCH_FN, 1, lambda owner: epoch,
                                deterministic=False)
            con.create_function(_OWNER_FENCE_INSTANCE_FN, 1, lambda owner: instance_id,
                                deterministic=False)
            con.create_function(_OWNER_FENCE_TOKEN_FN, 1, lambda owner: token,
                                deterministic=False)
        try:
            yield con
        finally:
            con.close()

    def _is_merged_layout(self, con: Any) -> bool:
        if self._merged_layout_cache is None:
            self._merged_layout_cache = bool(con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'c7_fence_%'").fetchone()[0])
        return self._merged_layout_cache

    def close(self) -> None:
        """Release a fence this store itself took (an operator-side open)."""

        if self._owns_fence and self.writer_fence is not None:
            self.writer_fence.close()
            self.writer_fence = None
            self._owns_fence = False

    def stamp_recovery_incarnation(self, *, owner: str, conversation: str,
                                   authority_seq: int, authority_digest: str,
                                   generation: str, stamped_at: str | None = None) -> str:
        """Stamp this Supply database as the incarnation a recovery sweep opened.

        Version bodies are the only copy of Practice/Sharing text, so a backup restored onto the
        same path would serve forgotten text again while every path still matches.  The generation
        is kept in the owner's own database and is not touched by ordinary commits or erasures.
        """

        if not str(generation or ""):
            raise ValueError("an incarnation needs a generation")
        with self._connection() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS recovery_incarnation ("
                "kind TEXT PRIMARY KEY, generation TEXT NOT NULL, owner TEXT NOT NULL, "
                "conversation TEXT NOT NULL, authority_seq INTEGER NOT NULL, "
                "authority_digest TEXT NOT NULL, stamped_at TEXT NOT NULL)")
            con.execute(
                "INSERT INTO recovery_incarnation"
                "(kind,generation,owner,conversation,authority_seq,authority_digest,stamped_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(kind) DO UPDATE SET generation=excluded.generation,"
                " owner=excluded.owner, conversation=excluded.conversation,"
                " authority_seq=excluded.authority_seq,"
                " authority_digest=excluded.authority_digest, stamped_at=excluded.stamped_at",
                ("supply", str(generation), str(owner), str(conversation),
                 int(authority_seq), str(authority_digest or ""),
                 str(stamped_at or _now())))
        return str(generation)

    def _assert_writer(self) -> None:
        if self.writer_fence is not None:
            self.writer_fence.assert_current()

    def _require_open(self, operation: str) -> None:
        """Refuse bodies on a recovered copy until its retained sweep has activated it."""

        gate = self.recovery_gate
        if gate is None:
            return
        try:
            # A marker only proves *some* copy was swept.  This store serves this database, so
            # it refuses to ride on an activation that names a different one.
            verify = getattr(gate, "verify_copy", None)
            if verify is not None:
                verify(supply=self.db_path, projection=self.projection_root)
            gate.ensure(operation)
        except ArtifactStoreClosed:
            raise
        except Exception as exc:
            # The gate is duck-typed on purpose (the owner keeps no dependency on the Life
            # policy module), but its named refusal must stay a definite BLOCKED at the wire.
            code = str(getattr(exc, "code", "") or "RECOVERY_NOT_ACTIVATED")
            detail = str(getattr(exc, "detail", "") or exc)
            raise ArtifactStoreClosed(code, detail) from exc

    def _init_db(self) -> None:
        if self.db is not None:
            raise RuntimeError(
                "schema DDL belongs to LifeSupplyDB on the single-database layout")
        with self._connection() as con:
            configure_journal(con)
            con.execute("PRAGMA synchronous=FULL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO schema_meta VALUES('version','1');
                CREATE TABLE IF NOT EXISTS c6_writer_epoch(owner TEXT PRIMARY KEY, active_epoch INTEGER NOT NULL, instance_id TEXT NOT NULL, writer_token TEXT NOT NULL DEFAULT 'unfenced-test');
                INSERT OR IGNORE INTO c6_writer_epoch(owner,active_epoch,instance_id,writer_token) VALUES('ManagedArtifact',0,'unfenced-test','unfenced-test');
                CREATE TABLE IF NOT EXISTS managed_artifacts(
                    artifact_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    lifecycle TEXT NOT NULL CHECK(lifecycle IN ('ACTIVE','ARCHIVED','TOMBSTONED')),
                    head_version_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    meaning_version TEXT NOT NULL,
                    caller_principal TEXT NOT NULL DEFAULT 'service:chiyo-life-supply',
                    writer_owner TEXT NOT NULL DEFAULT 'ManagedArtifact',
                    writer_instance TEXT NOT NULL DEFAULT 'legacy',
                    writer_epoch INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS artifacts_subject ON managed_artifacts(subject_id,lifecycle);
                CREATE TABLE IF NOT EXISTS artifact_versions(
                    version_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES managed_artifacts(artifact_id),
                    version_number INTEGER NOT NULL,
                    parent_version_id TEXT,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    operation_key TEXT NOT NULL UNIQUE,
                    content_erased INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(artifact_id,version_number)
                );
                CREATE TRIGGER IF NOT EXISTS artifact_versions_no_update
                    BEFORE UPDATE ON artifact_versions BEGIN SELECT RAISE(ABORT,'artifact versions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS artifact_versions_no_delete
                    BEFORE DELETE ON artifact_versions BEGIN SELECT RAISE(ABORT,'artifact versions are immutable'); END;
                CREATE TABLE IF NOT EXISTS artifact_operations(
                    operation_key TEXT PRIMARY KEY,
                    operation_kind TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_effect_receipts(
                    receipt_id TEXT PRIMARY KEY,
                    operation_key TEXT NOT NULL UNIQUE REFERENCES artifact_operations(operation_key),
                    artifact_id TEXT NOT NULL,
                    version_id TEXT,
                    effect_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_projection_bindings(
                    artifact_id TEXT PRIMARY KEY REFERENCES managed_artifacts(artifact_id),
                    relative_path TEXT NOT NULL UNIQUE,
                    bound_at REAL NOT NULL,
                    projection_sha256 TEXT,
                    projection_state TEXT NOT NULL DEFAULT 'CLEAN'
                );
                CREATE TABLE IF NOT EXISTS artifact_projection_outbox(
                    artifact_id TEXT PRIMARY KEY REFERENCES managed_artifacts(artifact_id),
                    expected_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('DIRTY','CLEAN')),
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_source_erasures(
                    operation_key TEXT PRIMARY KEY,
                    artifact_ids_json TEXT NOT NULL,
                    version_ids_json TEXT NOT NULL,
                    barrier_seq INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_source_edges(
                    child_kind TEXT NOT NULL,
                    child_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_namespace TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    generation TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    PRIMARY KEY(child_kind, child_id, source_kind, source_namespace, source_ref)
                );
                CREATE INDEX IF NOT EXISTS artifact_source_edges_src
                    ON artifact_source_edges(source_kind, source_namespace, source_ref);
            """)
            # One-way widening: an existing owner file gains the erasure marker.  Version rows keep
            # their identity and number; only the body column is cleared by the governed method.
            artifact_columns = {str(row[1]) for row in
                                con.execute("PRAGMA table_info(artifact_versions)").fetchall()}
            if "content_erased" not in artifact_columns:
                con.execute("ALTER TABLE artifact_versions ADD COLUMN content_erased "
                            "INTEGER NOT NULL DEFAULT 0")
            require_schema_columns(con, "ManagedArtifact", {
                "managed_artifacts": {"writer_owner", "writer_instance", "writer_epoch"},
                "artifact_versions": {"version_id", "operation_key", "content_sha256"},
                "artifact_operations": {"operation_key", "request_digest", "result_json"},
                "artifact_projection_outbox": {"artifact_id", "expected_sha256", "state"},
            })
            tables = ("managed_artifacts", "artifact_versions", "artifact_operations",
                      "artifact_effect_receipts", "artifact_projection_bindings",
                      "artifact_projection_outbox")
            # A *single-database* owner file (the product layout) already guards every table with
            # its own owner-parameterised triggers installed by ``LifeSupplyDB``.  Installing the
            # legacy 0-arg guards here would (a) make the running service's next write fail on a
            # SQL function its connection never registers and (b) reset the epoch row underneath a
            # live service.  So an operator-side open only re-asserts the standalone legacy
            # guards, and it removes any legacy trigger an earlier open left behind.
            merged_layout = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'c7_fence_%'").fetchone()[0]
            if merged_layout:
                for table in tables:
                    for action in ("insert", "update", "delete"):
                        con.execute("DROP TRIGGER IF EXISTS c6_fence_%s_%s" % (table, action))
                if self.writer_fence is not None:
                    # Take (or continue) this owner's writer epoch in the shared row, exactly as
                    # the service does at startup; otherwise the guards would see a stale token.
                    from lifesupply.db import activate_owner_epoch

                    activate_owner_epoch(con, "ManagedArtifact", self.writer_fence)
            else:
                activate_writer_epoch(con, "ManagedArtifact", self.writer_fence)
                install_write_guards(con, "ManagedArtifact", tables)

    @staticmethod
    def _write_edges(con: Any, *, child_kind: str, child_id: str, sources: Any,
                     now: float) -> int:
        written = 0
        for item in sources or ():
            edge = ManagedArtifactStore._edge_source(item)
            if edge is None:
                continue
            source_kind, source_namespace, source_ref, generation = edge
            con.execute(
                "INSERT OR IGNORE INTO artifact_source_edges(child_kind,child_id,"
                "source_kind,source_namespace,source_ref,generation,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (child_kind, child_id, source_kind, source_namespace, source_ref, generation, now))
            written += 1
        return written

    def create_artifact(self, *, subject_id: str, workspace_id: str, title: str,
                        operation_key: str, grant_reservation_id: str,
                        operation_payload: Any = None,
                        source_refs: Any = ()) -> dict[str, Any]:
        self._assert_writer()
        self._require_open("create_artifact")
        if not grant_reservation_id or self.workspace_authority is None:
            return {"status": "DENY", "detail": "COMMIT_MANAGED_ARTIFACT reservation and Workspace authority required"}
        workspace = self.workspace_authority.get_workspace_by_id(workspace_id)
        if not workspace or workspace["subject_id"] != subject_id or workspace["lifecycle"] != "ACTIVE":
            return {"status": "DENY", "detail": "workspace owner/lifecycle mismatch"}
        expected_payload = {"operation": "create_artifact", "subject_id": subject_id,
            "workspace_id": workspace_id, "title": title.strip()}
        if operation_payload is not None and operation_payload != expected_payload:
            return {"status": "DENY", "detail": "artifact operation payload mismatch"}
        if not all((subject_id, workspace_id, title.strip(), operation_key)):
            return {"status": "DENY", "detail": "required field missing"}
        req = {"subject": subject_id, "workspace": workspace_id, "title": title.strip(),
               "operation_payload": operation_payload}
        digest, now = _digest(req), float(self.clock())
        try:
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                replay = self._lookup(con, operation_key, digest, "CREATE")
                if replay is not None:
                    con.commit(); return replay
                aid = "artifact:" + uuid.uuid4().hex
                con.execute("""INSERT INTO managed_artifacts(
                    artifact_id,subject_id,workspace_id,title,lifecycle,head_version_id,
                    created_at,updated_at,revision,meaning_version,caller_principal,
                    writer_owner,writer_instance,writer_epoch
                ) VALUES(?,?,?,?,'ACTIVE',NULL,?,?,1,'managed_artifact.v1',?,'ManagedArtifact',?,?)""",
                    (aid, subject_id, workspace_id, title.strip(), now, now,
                     "service:chiyo-life-supply", self.writer_fence.instance_id if self.writer_fence else "legacy",
                     int(self.writer_fence.epoch) if self.writer_fence else 0))
                result = {"status": "OK", "artifact_id": aid, "revision": 1}
                if source_refs:
                    result["source_edges"] = self._write_edges(
                        con, child_kind="artifact", child_id=aid,
                        sources=source_refs, now=now)
                con.execute("INSERT INTO artifact_operations VALUES(?,?,?,?,?,?,?)",
                            (operation_key, "CREATE", aid, digest, result["status"], _json(result), now))
                con.execute("INSERT INTO artifact_effect_receipts VALUES(?,?,?,?,?,?,?)",
                            ("receipt:" + uuid.uuid4().hex, operation_key, aid, None, "CREATE", "APPLIED", now))
                con.commit(); return result
        except sqlite3.IntegrityError:
            return {"status": "CONFLICT", "detail": "artifact operation conflict"}
        except sqlite3.Error:
            return {"status": "UNKNOWN", "detail": "artifact store unavailable"}

    @staticmethod
    def _lookup(con: sqlite3.Connection, operation_key: str, digest: str,
                operation_kind: str | None = None) -> dict[str, Any] | None:
        row = con.execute("SELECT * FROM artifact_operations WHERE operation_key=?", (operation_key,)).fetchone()
        if not row:
            return None
        if row["request_digest"] != digest or (operation_kind and row["operation_kind"] != operation_kind):
            return {"status": "CONFLICT", "detail": "operation key reused with different request"}
        return json.loads(row["result_json"])

    def read_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        self._require_open("read_artifact")
        try:
            with self._connection() as con:
                row = con.execute("SELECT * FROM managed_artifacts WHERE artifact_id=? AND lifecycle!='TOMBSTONED'", (artifact_id,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def get_artifact_for_subject(self, artifact_id: str, subject_id: str) -> dict[str, Any] | None:
        try:
            with self._connection() as con:
                row = con.execute("SELECT * FROM managed_artifacts WHERE artifact_id=? AND subject_id=?",
                                  (artifact_id, subject_id)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def read_head(self, artifact_id: str) -> dict[str, Any] | None:
        self._require_open("read_head")
        try:
            with self._connection() as con:
                row = con.execute("""SELECT v.* FROM managed_artifacts a
                    JOIN artifact_versions v ON v.version_id=a.head_version_id
                    WHERE a.artifact_id=? AND a.lifecycle!='TOMBSTONED'""", (artifact_id,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def read_version(self, version_id: str) -> dict[str, Any] | None:
        self._require_open("read_version")
        try:
            with self._connection() as con:
                row = con.execute("SELECT v.* FROM artifact_versions v JOIN managed_artifacts a ON a.artifact_id=v.artifact_id WHERE v.version_id=? AND a.lifecycle!='TOMBSTONED'", (version_id,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None
    # -- album media (EP-16): owner-named files beside the canonical database ---------------

    def put_media(self, data: bytes, content_type: str) -> dict[str, Any]:
        """Write one album picture through the owner's own media directory.

        The caller supplies bytes and a declared type only; the file name, digest and size are
        produced here.  The write is atomic and idempotent for identical bytes.  A failed write
        leaves no reference behind -- the caller's database transaction has not committed yet.
        """
        if self.media_root is None:
            return {"status": "BLOCKED", "reason": "MEDIA_ROOT_NOT_CONFIGURED"}
        self._assert_writer()
        try:
            facts = album_media.write_media(self.media_root, data, content_type)
        except album_media.AlbumImageRefused as exc:
            return {"status": "DENY", "reason": exc.reason, "detail": exc.detail}
        except OSError:
            return {"status": "UNKNOWN", "reason": "MEDIA_WRITE_FAILED"}
        return {"status": "OK", **facts}

    def get_media(self, name: Any, sha256: Any, size: Any) -> bytes | None:
        """Read one owner-named media file, verified against the manifest's own facts."""
        return album_media.read_media(self.media_root, name, sha256, size)

    #: Bounds on one page of version *metadata*.  A caller can page with the returned cursor; the
    #: owner never returns an unbounded list and never returns version bodies here.
    DEFAULT_VERSION_PAGE = 20
    MAX_VERSION_PAGE = 50

    def list_versions(self, artifact_id: str, *, limit: int = DEFAULT_VERSION_PAGE,
                      before_version_number: int | None = None) -> dict[str, Any] | None:
        """One page of this artifact's version metadata, newest first (no bodies).

        ``None`` means the owner could not read (unavailable), never "no versions".  A known
        artifact answers with an explicit ``total_versions`` plus a ``next_before_version_number``
        cursor, so a caller can tell a short page from a whole history instead of guessing.  The
        versions themselves are immutable rows (triggers below refuse UPDATE/DELETE), so a paged
        read can never observe a rewritten history.
        """
        cap = max(1, min(int(limit), self.MAX_VERSION_PAGE))
        try:
            with self._connection() as con:
                artifact = con.execute(
                    "SELECT artifact_id,head_version_id,lifecycle FROM managed_artifacts"
                    " WHERE artifact_id=?", (artifact_id,)).fetchone()
                if artifact is None:
                    return None
                sql = ("SELECT version_id,artifact_id,version_number,parent_version_id,"
                       "content_sha256,length(content) AS content_chars,created_at,operation_key,"
                       "content_erased FROM artifact_versions WHERE artifact_id=?")
                params: list[Any] = [artifact_id]
                if before_version_number is not None:
                    sql += " AND version_number<?"
                    params.append(int(before_version_number))
                sql += " ORDER BY version_number DESC LIMIT ?"
                params.append(cap)
                rows = [dict(row) for row in con.execute(sql, tuple(params)).fetchall()]
                total = int(con.execute(
                    "SELECT count(*) FROM artifact_versions WHERE artifact_id=?",
                    (artifact_id,)).fetchone()[0])
                oldest = (int(rows[-1]["version_number"]) if rows else None)
                has_older = oldest is not None and oldest > 1
                return {
                    "versions": rows,
                    "total_versions": total,
                    "returned": len(rows),
                    "head_version_id": artifact["head_version_id"],
                    "lifecycle": artifact["lifecycle"],
                    "next_before_version_number": oldest if has_older else None,
                }
        except sqlite3.Error:
            return None

    def read_effect_receipt(self, operation_key: str) -> dict[str, Any] | None:
        try:
            with self._connection() as con:
                row = con.execute("SELECT * FROM artifact_effect_receipts WHERE operation_key=?", (operation_key,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def lookup_operation(self, operation_key: str) -> dict[str, Any] | None:
        try:
            with self._connection() as con:
                row = con.execute("SELECT * FROM artifact_operations WHERE operation_key=?", (operation_key,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def read_create_operation(self, artifact_id: str) -> dict[str, Any] | None:
        """The CREATE operation row that brought one artifact identity into being.

        Album reads use it only to find the operation key under which the artifact was created,
        so the Governance reservation recorded for that exact action can be compared.  It is the
        owner's own durable log, never a caller-supplied claim.
        """
        try:
            with self._connection() as con:
                row = con.execute(
                    "SELECT operation_key,artifact_id,outcome,result_json FROM artifact_operations"
                    " WHERE artifact_id=? AND operation_kind='CREATE'",
                    (artifact_id,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def read_reservations_for_action(self, action_key: str) -> list[dict[str, Any]]:
        """The Governance reservations recorded for one action key (shared single DB, read only).

        This mirrors the narrow read the Workspace owner already makes of the reservation table:
        the caller compares the row's own ``action_digest`` and ``state``; this method never
        inserts, updates or interprets Governance state, and an unavailable read answers ``[]``
        (never "allowed").
        """
        try:
            with self._connection() as con:
                rows = con.execute(
                    "SELECT reservation_id,grant_id,action_key,action_digest,state"
                    " FROM permission_reservations WHERE action_key=?", (action_key,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error:
            return []

    def commit_markdown(self, *, artifact_id: str, authorized_subject_id: str,
                        expected_head: str | None, content: str, operation_key: str,
                        grant_reservation_id: str, operation_payload: Any = None,
                        operation_kind: str = "COMMIT_MARKDOWN",
                        provenance: Mapping[str, Any] | None = None,
                        expected_projection_sha256: str | None = None,
                        source_refs: Any = ()) -> dict[str, Any]:
        self._assert_writer()
        self._require_open("commit_markdown")
        if not grant_reservation_id:
            return {"status": "DENY", "detail": "COMMIT_MANAGED_ARTIFACT grant reservation required"}
        if not operation_key or not isinstance(content, str) or operation_kind != "COMMIT_MARKDOWN":
            return {"status": "DENY", "detail": "operation key/content/kind invalid"}
        expected_payload = {"operation": "commit_markdown", "subject_id": authorized_subject_id,
            "artifact_id": artifact_id, "expected_head": expected_head, "content": content}
        if operation_payload is not None and operation_payload != expected_payload:
            return {"status": "DENY", "detail": "route/action payload mismatch"}
        request = {"artifact_id": artifact_id, "expected_head": expected_head,
                   "content": content, "operation_kind": operation_kind,
                   "operation_payload": operation_payload}
        digest, now = _digest(request), float(self.clock())
        try:
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                # Frozen rule: operation replay/conflict is decided before any CAS check.
                replay = self._lookup(con, operation_key, digest, "COMMIT_MARKDOWN")
                if replay is not None:
                    # A historical operation replay returns its original receipt, but its old
                    # version must never be projected over a newer canonical head -- and an
                    # external edit is still evidence, so this refresh goes through the guard.
                    con.commit()
                    live = con.execute(
                        "SELECT v.content FROM managed_artifacts a JOIN artifact_versions v"
                        " ON v.version_id=a.head_version_id WHERE a.artifact_id=?",
                        (artifact_id,)).fetchone()
                    if live is not None:
                        self._update_projection(artifact_id, live["content"],
                                                float(self.clock()))
                    return replay
                artifact = con.execute("SELECT * FROM managed_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
                if artifact is None or artifact["subject_id"] != authorized_subject_id:
                    con.rollback(); return {"status": "NOT_FOUND", "detail": "artifact subject mismatch"}
                if artifact["lifecycle"] != "ACTIVE":
                    con.rollback(); return {"status": "BLOCKED", "detail": "artifact is not active"}
                current_head = artifact["head_version_id"]
                if current_head != expected_head:
                    con.rollback(); return {"status": "CONFLICT", "detail": "stale expected_head", "current_head": current_head}
                if current_head is not None:
                    old = con.execute("SELECT content FROM artifact_versions WHERE version_id=?", (current_head,)).fetchone()
                    if old and old["content"] == content:
                        result = {"status": "NO_CHANGE", "artifact_id": artifact_id,
                                  "head_version_id": current_head, "version_created": False}
                        if provenance is not None:
                            result["provenance"] = dict(provenance)
                        if source_refs and current_head:
                            result["source_edges"] = self._write_edges(
                                con, child_kind="artifactversion", child_id=current_head,
                                sources=source_refs, now=now)
                        con.execute("INSERT INTO artifact_operations VALUES(?,?,?,?,?,?,?)",
                                    (operation_key, "COMMIT_MARKDOWN", artifact_id, digest,
                                     result["status"], _json(result), now))
                        con.commit(); return result
                number = int(con.execute("SELECT count(*) FROM artifact_versions WHERE artifact_id=?", (artifact_id,)).fetchone()[0]) + 1
                version_id = "artifact-version:" + uuid.uuid4().hex
                con.execute(
                    "INSERT INTO artifact_versions(version_id,artifact_id,version_number,"
                    "parent_version_id,content,content_sha256,created_at,operation_key,"
                    "content_erased) VALUES(?,?,?,?,?,?,?,?,0)",
                    (version_id, artifact_id, number, current_head, content,
                     hashlib.sha256(content.encode()).hexdigest(), now, operation_key))
                con.execute("UPDATE managed_artifacts SET head_version_id=?,updated_at=?,revision=revision+1 WHERE artifact_id=? AND head_version_id IS ?",
                            (version_id, now, artifact_id, current_head))
                result = {"status": "OK", "artifact_id": artifact_id,
                          "version_id": version_id, "version_number": number,
                          "parent_version_id": current_head, "version_created": True}
                if provenance is not None:
                    # An import is recorded as an ordinary immutable version whose *source* is
                    # explicit and external; the creator chain is not forged.
                    result["provenance"] = dict(provenance)
                if source_refs:
                    # Lineage is recorded by the same owner call that commits the version, inside
                    # this transaction: a derived body cannot exist without its source facts.
                    result["source_edges"] = self._write_edges(
                        con, child_kind="artifactversion", child_id=version_id,
                        sources=source_refs, now=now)
                con.execute("INSERT INTO artifact_operations VALUES(?,?,?,?,?,?,?)",
                            (operation_key, "COMMIT_MARKDOWN", artifact_id, digest,
                             result["status"], _json(result), now))
                con.execute("INSERT INTO artifact_effect_receipts VALUES(?,?,?,?,?,?,?)",
                            ("receipt:" + uuid.uuid4().hex, operation_key, artifact_id,
                             version_id, "COMMIT_MARKDOWN", "APPLIED", now))
                con.execute("INSERT INTO artifact_projection_outbox VALUES(?,?, 'DIRTY', ?) ON CONFLICT(artifact_id) DO UPDATE SET expected_sha256=excluded.expected_sha256,state='DIRTY',updated_at=excluded.updated_at",
                            (artifact_id, hashlib.sha256(content.encode()).hexdigest(), now))
                con.commit()
                self._update_projection(artifact_id, content, now,
                                        overwrite_if_sha=expected_projection_sha256)
                return result
        except sqlite3.IntegrityError:
            return {"status": "CONFLICT", "detail": "artifact operation or version conflict"}
        except (sqlite3.Error, OSError):
            return {"status": "UNKNOWN", "detail": "artifact store unavailable"}

    def _projection_has_external_edit(self, artifact_id: str, path: Path) -> bool:
        """True when the file on disk is not the bytes this owner last wrote.

        The binding's ``projection_sha256`` is the owner's own record of its last projection
        write.  A mismatch means an external edit exists, so an automatic re-projection must
        preserve it instead of silently overwriting the evidence.
        """
        try:
            with self._connection() as con:
                row = con.execute(
                    "SELECT projection_sha256 FROM artifact_projection_bindings WHERE artifact_id=?",
                    (artifact_id,)).fetchone()
        except sqlite3.Error:
            return False
        if row is None or not row["projection_sha256"]:
            return False
        try:
            if not path.is_file() or path.is_symlink():
                return False
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False
        return actual != str(row["projection_sha256"])

    def _update_projection(self, artifact_id: str, content: str, now: float, *,
                           overwrite_if_sha: str | None = None,
                           force_external: bool = False) -> str | None:
        self._assert_writer()
        if not self.projection_root:
            return None
        digest = hashlib.sha256(artifact_id.encode()).hexdigest()
        rel = f"{digest[:2]}/{digest}.md"
        path = self.projection_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # A planted symlinked shard directory must never redirect the write outside the root.
            parent_is_symlink = path.parent.is_symlink()
            parent_escapes = path.parent.resolve().parent != self.projection_root.resolve()
        except OSError:
            parent_is_symlink, parent_escapes = True, True
        if parent_is_symlink or parent_escapes:
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE artifact_projection_bindings SET projection_state='DIRTY' WHERE artifact_id=?", (artifact_id,))
                con.execute("UPDATE artifact_projection_outbox SET state='DIRTY',updated_at=? WHERE artifact_id=?", (now, artifact_id))
                con.commit()
            return "DIRTY"
        preserve = False
        if overwrite_if_sha is not None:
            # An explicit import may overwrite the projection -- but only when the file still
            # holds the exact bytes the user confirmed.  A later unconfirmed edit is preserved.
            try:
                preserve = (not path.is_file() or path.is_symlink()
                            or hashlib.sha256(path.read_bytes()).hexdigest() != overwrite_if_sha)
            except OSError:
                preserve = True
        if preserve:
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE artifact_projection_bindings SET projection_state='DIRTY' WHERE artifact_id=?", (artifact_id,))
                con.execute("UPDATE artifact_projection_outbox SET state='DIRTY',updated_at=? WHERE artifact_id=?", (now, artifact_id))
                con.commit()
            return "DIRTY"
        if (overwrite_if_sha is None and not force_external
                and self._projection_has_external_edit(artifact_id, path)):
            # An external edit is evidence.  The canonical commit still stands, but the
            # projection is left untouched and marked DIRTY; only an explicit import (or an
            # explicit rebuild when the file is missing) may move it.
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE artifact_projection_bindings SET projection_state='DIRTY' WHERE artifact_id=?", (artifact_id,))
                con.execute("UPDATE artifact_projection_outbox SET state='DIRTY',updated_at=? WHERE artifact_id=?", (now, artifact_id))
                con.commit()
            return "DIRTY"
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
        # Determine head/version before marking the projection clean. If canonical state moved
        # during I/O, preserve DIRTY so recovery rebuilds the latest head.
        digest = hashlib.sha256(content.encode()).hexdigest()
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            head = con.execute("SELECT v.content,v.content_sha256 FROM managed_artifacts a JOIN artifact_versions v ON v.version_id=a.head_version_id WHERE a.artifact_id=?", (artifact_id,)).fetchone()
            if head is None or head["content_sha256"] != digest or head["content"] != content:
                con.execute("UPDATE artifact_projection_bindings SET projection_state='DIRTY' WHERE artifact_id=?", (artifact_id,))
                con.execute("UPDATE artifact_projection_outbox SET state='DIRTY',updated_at=? WHERE artifact_id=?", (now, artifact_id))
                con.commit()
                return "DIRTY"
            con.execute("INSERT INTO artifact_projection_bindings(artifact_id,relative_path,bound_at,projection_sha256,projection_state) VALUES(?,?,?,?, 'CLEAN') ON CONFLICT(artifact_id) DO UPDATE SET relative_path=excluded.relative_path,bound_at=excluded.bound_at,projection_sha256=excluded.projection_sha256,projection_state='CLEAN'",
                        (artifact_id, rel, now, digest))
            con.execute("INSERT INTO artifact_projection_outbox VALUES(?,?, 'CLEAN', ?) ON CONFLICT(artifact_id) DO UPDATE SET expected_sha256=excluded.expected_sha256,state='CLEAN',updated_at=excluded.updated_at",
                        (artifact_id, digest, now))
            con.commit()
        return "CLEAN"

    def check_projection(self, artifact_id: str) -> dict[str, Any]:
        if not self.projection_root:
            return {"status": "UNKNOWN", "detail": "projection root not configured"}
        try:
            with self._connection() as con:
                binding = con.execute("SELECT * FROM artifact_projection_bindings WHERE artifact_id=?", (artifact_id,)).fetchone()
                head = con.execute("SELECT v.* FROM managed_artifacts a JOIN artifact_versions v ON v.version_id=a.head_version_id WHERE a.artifact_id=?", (artifact_id,)).fetchone()
            if not binding or not head:
                return {"status": "NOT_FOUND"}
            path = self.projection_root / binding["relative_path"]
            if not path.is_file():
                return {"status": "CONFLICT", "detail": "projection missing"}
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = head["content_sha256"]
            return {"status": "OK" if actual == expected else "CONFLICT",
                    "expected_sha256": expected, "actual_sha256": actual}
        except (OSError, sqlite3.Error):
            return {"status": "UNKNOWN", "detail": "projection check unavailable"}

    # -- F-011/F-012/F-014: representation observation, explicit import, health ----------------

    def read_projection_binding(self, artifact_id: str) -> dict[str, Any] | None:
        """The owner's own mutable representation binding for one artifact, or ``None``."""
        try:
            with self._connection() as con:
                row = con.execute(
                    "SELECT * FROM artifact_projection_bindings WHERE artifact_id=?",
                    (artifact_id,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            return None

    def _safe_projection_path(self, rel: Any) -> tuple[Path | None, str | None]:
        """Resolve one binding path *inside* the projection root, refusing every escape.

        Every path component is checked with ``lstat`` so a symlink is refused instead of
        followed; the resolved path must still sit under the root.  The answer is
        ``(path, reason)`` where a non-``None`` reason is the named refusal.
        """
        if not self.projection_root:
            return None, "PROJECTION_ROOT_NOT_CONFIGURED"
        if not _projection_rel_is_safe(rel):
            return None, "UNSAFE_BINDING_PATH"
        root = self.projection_root.resolve()
        path = root
        for part in PurePosixPath(str(rel).replace("\\", "/")).parts:
            path = path / part
            try:
                st = path.lstat()
            except FileNotFoundError:
                return None, "MISSING"
            except OSError:
                return None, "FILE_UNREADABLE"
            if stat.S_ISLNK(st.st_mode):
                return None, "SYMLINK_REFUSED"
        try:
            path.resolve(strict=False).relative_to(root)
        except (OSError, ValueError):
            return None, "PATH_ESCAPES_ROOT"
        try:
            st = path.lstat()
        except OSError:
            return None, "FILE_UNREADABLE"
        if not stat.S_ISREG(st.st_mode):
            return None, "NOT_A_REGULAR_FILE"
        return path, None

    @staticmethod
    def _read_projection_text(path: Path) -> tuple[str | None, str | None]:
        """Read one projection with the owner's size bound, never following a final symlink."""
        try:
            st = path.lstat()
            if st.st_size > MAX_PROJECTION_BYTES:
                return None, "PROJECTION_TOO_LARGE"
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(path), flags)
            try:
                data = os.read(fd, MAX_PROJECTION_BYTES + 1)
            finally:
                os.close(fd)
        except OSError:
            return None, "FILE_UNREADABLE"
        if len(data) > MAX_PROJECTION_BYTES:
            return None, "PROJECTION_TOO_LARGE"
        try:
            return data.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "PROJECTION_NOT_UTF8"

    @staticmethod
    def _projection_diff(canonical: str, projected: str) -> dict[str, Any]:
        left, right = canonical.splitlines(), projected.splitlines()
        added = removed = 0
        matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in {"replace", "delete"}:
                removed += i2 - i1
            if tag in {"replace", "insert"}:
                added += j2 - j1
        return {"note_lines_added": added, "note_lines_removed": removed,
                "canonical_lines": len(left), "projection_lines": len(right)}

    def observe_projection(self, artifact_id: str, *, subject_id: str | None = None,
                           include_content: bool = False,
                           _head_override: Any = _UNSET) -> dict[str, Any]:
        """Actually read the projection and report payload and representation health apart.

        This is a pure read: no row is written, no Observation event is stored, and the
        projection is never repaired here.  ``content_health`` describes the canonical payload
        (``AVAILABLE``/``UNAVAILABLE``/``CORRUPT``); ``representation_health`` describes the
        managed file (``CURRENT``/``MISSING``/``DIVERGED``/``UNBOUND``).  A readable canonical
        payload is never called a successful projection merely because a file exists.

        ``include_content`` is for the explicit import command only; the returned text is placed
        under ``_candidate_content`` and the observe operation never forwards it.  ``_head_override``
        exists solely so a test/demo can inject a controlled corrupt/unavailable payload; it is
        never passed by the service.
        """
        try:
            with self._connection() as con:
                artifact = con.execute(
                    "SELECT * FROM managed_artifacts WHERE artifact_id=? AND lifecycle!='TOMBSTONED'",
                    (artifact_id,)).fetchone()
                head = con.execute(
                    "SELECT v.* FROM managed_artifacts a JOIN artifact_versions v"
                    " ON v.version_id=a.head_version_id"
                    " WHERE a.artifact_id=? AND a.lifecycle!='TOMBSTONED'",
                    (artifact_id,)).fetchone()
                binding = con.execute(
                    "SELECT * FROM artifact_projection_bindings WHERE artifact_id=?",
                    (artifact_id,)).fetchone()
        except sqlite3.Error:
            return {"status": "UNKNOWN", "reason": "OWNER_READ_UNAVAILABLE"}
        if artifact is None or (subject_id is not None and artifact["subject_id"] != subject_id):
            return {"status": "NOT_FOUND", "reason": "ARTIFACT_NOT_FOUND"}
        artifact = dict(artifact)
        head = dict(head) if head is not None else None
        binding = dict(binding) if binding is not None else None
        if _head_override is not _UNSET:
            head = dict(_head_override) if isinstance(_head_override, dict) else None

        content: str | None = None
        head_view: dict[str, Any] | None = None
        if head is None:
            content_health = {"state": "UNAVAILABLE", "reason_code": "NO_CANONICAL_HEAD"}
        else:
            content = head.get("content") if isinstance(head.get("content"), str) else None
            head_view = {key: head.get(key) for key in (
                "version_id", "version_number", "parent_version_id", "content_sha256",
                "created_at", "operation_key")}
            head_view["content_chars"] = len(content) if content is not None else None
            stored_sha = str(head.get("content_sha256") or "")
            actual_sha = hashlib.sha256(content.encode()).hexdigest() if content is not None else ""
            if content is None or not stored_sha or actual_sha != stored_sha:
                # A payload that cannot be verified is not served as if it were fine.
                content_health = {"state": "CORRUPT", "reason_code": "STORED_HASH_MISMATCH"}
                content = None
            else:
                content_health = {"state": "AVAILABLE", "reason_code": None}

        binding_view: dict[str, Any] | None = None
        rep_state, rep_reason = "UNBOUND", "NO_BINDING"
        file_view: dict[str, Any] | None = None
        projected_text: str | None = None
        projected_sha: str | None = None
        if binding is not None:
            binding_view = {key: binding.get(key) for key in (
                "relative_path", "bound_at", "projection_sha256", "projection_state")}
            rel = binding.get("relative_path")
            path, reason = self._safe_projection_path(rel)
            if reason == "MISSING":
                rep_state, rep_reason = "MISSING", "PROJECTION_FILE_MISSING"
            elif reason == "NOT_A_REGULAR_FILE":
                rep_state, rep_reason = "MISSING", reason
            elif reason in {"UNSAFE_BINDING_PATH", "PATH_ESCAPES_ROOT", "SYMLINK_REFUSED",
                            "PROJECTION_ROOT_NOT_CONFIGURED", "FILE_UNREADABLE"}:
                # A binding that cannot safely name a managed file is not a usable representation.
                rep_state, rep_reason = "UNBOUND", reason
            elif reason is None:
                try:
                    st = path.lstat()
                    file_view = {"relative_path": rel, "size_bytes": int(st.st_size),
                                 "mtime": float(st.st_mtime)}
                except OSError:
                    file_view = {"relative_path": rel}
                projected_text, read_reason = self._read_projection_text(path)
                if read_reason is not None:
                    rep_state, rep_reason = "DIVERGED", read_reason
                else:
                    projected_sha = hashlib.sha256(projected_text.encode()).hexdigest()
                    file_view["sha256"] = projected_sha
                    if content_health["state"] != "AVAILABLE":
                        rep_state, rep_reason = "DIVERGED", "CANONICAL_PAYLOAD_UNUSABLE"
                    elif projected_sha == str(head.get("content_sha256") or ""):
                        rep_state, rep_reason = "CURRENT", None
                    else:
                        rep_state, rep_reason = "DIVERGED", "CONTENT_DIFFERS_FROM_HEAD"

        candidate: dict[str, Any] | None = None
        candidate_content: str | None = None
        if (rep_state == "DIVERGED" and content_health["state"] == "AVAILABLE"
                and content is not None and projected_text is not None and projected_sha):
            # Which version was the file last projected *from*?  If a canonical commit happened
            # while the external edit was pending, the head has moved but the file is still the
            # old projection plus an external edit.  The owner's own binding hash names that old
            # version; importing against it then answers HEAD_MOVED instead of silently rebasing.
            head_sha = str(head.get("content_sha256") or "")
            binding_sha = str((binding_view or {}).get("projection_sha256") or "")
            base_version: str | None = str(head.get("version_id") or "") or None
            base_version_number = head.get("version_number")
            base_basis = "current_head"
            if binding_sha and binding_sha != head_sha:
                base_version, base_version_number, base_basis = None, None, "NO_MATCHING_VERSION"
                try:
                    with self._connection() as con:
                        rows = con.execute(
                            "SELECT version_id,version_number FROM artifact_versions"
                            " WHERE artifact_id=? AND content_sha256=?"
                            " ORDER BY version_number DESC",
                            (artifact_id, binding_sha)).fetchall()
                except sqlite3.Error:
                    rows = []
                if len(rows) == 1:
                    base_version = str(rows[0]["version_id"])
                    base_version_number = rows[0]["version_number"]
                    base_basis = "binding_projection_sha256"
                elif rows:
                    base_basis = "AMBIGUOUS_BASE_VERSION"
            seed = _json({"artifact_id": artifact_id,
                          "relative_path": binding_view.get("relative_path") if binding_view else None,
                          "base_version_id": base_version,
                          "observed_sha256": projected_sha})
            candidate = {
                "candidate_id": "projection-candidate:"
                                + hashlib.sha256(seed.encode("utf-8")).hexdigest(),
                "artifact_id": artifact_id,
                "base_projection_version": base_version,
                "base_version_number": base_version_number,
                "base_basis": base_basis,
                "base_canonical_sha256": binding_sha or None,
                "observed_sha256": projected_sha,
                "size_bytes": (file_view or {}).get("size_bytes"),
                "authority": "EXTERNAL_UNKNOWN",
                "author_basis": ("file content only; authorship is not inferred from path, "
                                 "directory, user name or mtime"),
                "source": "EXTERNAL_FILESYSTEM",
                "diff": self._projection_diff(content, projected_text),
            }
            if include_content:
                candidate_content = projected_text

        answer: dict[str, Any] = {
            "status": "OK",
            "artifact": {key: artifact.get(key) for key in (
                "artifact_id", "subject_id", "workspace_id", "title", "lifecycle",
                "head_version_id", "revision", "meaning_version", "created_at", "updated_at")},
            "head": head_view,
            "content_health": content_health,
            "representation_health": {
                "state": rep_state,
                "reason_code": rep_reason,
                "binding": binding_view,
                "file": file_view,
            },
            "candidate": candidate,
            "source": "ManagedArtifact",
        }
        if include_content:
            answer["_candidate_content"] = candidate_content
        return answer

    def rebuild_projection_safe(self, artifact_id: str, *,
                                subject_id: str | None = None) -> dict[str, Any]:
        """Rebuild a MISSING/UNBOUND projection, never overwrite a DIVERGED one."""
        seen = self.observe_projection(artifact_id, subject_id=subject_id)
        if seen.get("status") != "OK":
            return seen
        state = seen["representation_health"]["state"]
        if state == "CURRENT":
            return {"status": "NO_CHANGE", "artifact_id": artifact_id,
                    "content_health": seen["content_health"],
                    "representation_health": seen["representation_health"],
                    "detail": "projection already current; nothing written"}
        if state == "DIVERGED":
            return {"status": "BLOCKED", "reason": "PROJECTION_DIVERGED",
                    "detail": ("a diverged projection is external evidence; it is never "
                               "overwritten by a rebuild"),
                    "content_health": seen["content_health"],
                    "representation_health": seen["representation_health"]}
        if state == "UNBOUND" and seen["representation_health"]["reason_code"] in {
                "SYMLINK_REFUSED", "UNSAFE_BINDING_PATH", "PATH_ESCAPES_ROOT",
                "FILE_UNREADABLE"}:
            # Only a genuinely absent binding may be rebuilt.  A binding that names a symlink or
            # escapes the root is a structural warning, not something to overwrite.
            return {"status": "BLOCKED", "reason": "PROJECTION_BINDING_UNSAFE",
                    "detail": "the binding does not name a safely writable managed file",
                    "content_health": seen["content_health"],
                    "representation_health": seen["representation_health"]}
        if seen["content_health"]["state"] != "AVAILABLE":
            return {"status": "BLOCKED", "reason": "CANONICAL_PAYLOAD_UNUSABLE",
                    "detail": "no verified canonical payload to project",
                    "content_health": seen["content_health"],
                    "representation_health": seen["representation_health"]}
        written = self.rebuild_projection(artifact_id)
        if written.get("status") not in {"OK", "NO_CHANGE"}:
            return {"status": "UNKNOWN", "reason": "PROJECTION_WRITE_UNCERTAIN",
                    "detail": str(written.get("detail") or ""),
                    "content_health": seen["content_health"],
                    "representation_health": seen["representation_health"]}
        after = self.observe_projection(artifact_id, subject_id=subject_id)
        ok = after.get("status") == "OK" and after["representation_health"]["state"] == "CURRENT"
        return {"status": "OK" if ok else "UNKNOWN", "artifact_id": artifact_id,
                "reason": None if ok else "PROJECTION_VERIFY_AFTER_WRITE_FAILED",
                "content_health": after.get("content_health"),
                "representation_health": after.get("representation_health")}

    def read_version_provenance(self, artifact_id: str,
                                version_id: str) -> dict[str, Any] | None:
        """The owner-written provenance of one version, or ``None`` for an ordinary commit."""
        try:
            with self._connection() as con:
                row = con.execute(
                    "SELECT operation_key FROM artifact_versions WHERE artifact_id=? AND version_id=?",
                    (artifact_id, version_id)).fetchone()
                if row is None:
                    return None
                operation = con.execute(
                    "SELECT result_json FROM artifact_operations WHERE operation_key=?",
                    (row["operation_key"],)).fetchone()
        except sqlite3.Error:
            return None
        if operation is None:
            return None
        try:
            result = json.loads(operation["result_json"])
        except (TypeError, ValueError):
            return None
        provenance = result.get("provenance") if isinstance(result, dict) else None
        return dict(provenance) if isinstance(provenance, dict) else None

    def recover_dirty_projections(self) -> dict[str, Any]:
        try:
            with self._connection() as con:
                rows = con.execute("SELECT o.artifact_id,v.content FROM artifact_projection_outbox o JOIN managed_artifacts a ON a.artifact_id=o.artifact_id JOIN artifact_versions v ON v.version_id=a.head_version_id WHERE o.state='DIRTY' AND a.lifecycle!='TOMBSTONED'").fetchall()
            recovered: list[str] = []
            preserved: list[str] = []
            for row in rows:
                state = self._update_projection(row["artifact_id"], row["content"],
                                                float(self.clock()))
                if state == "DIRTY":
                    preserved.append(row["artifact_id"])
                else:
                    recovered.append(row["artifact_id"])
            return {"status": "OK", "recovered": recovered,
                    "preserved_diverged": preserved}
        except (sqlite3.Error, OSError):
            return {"status": "UNKNOWN", "detail": "projection recovery incomplete"}

    def rebuild_projection(self, artifact_id: str) -> dict[str, Any]:
        if not self.projection_root:
            return {"status": "UNKNOWN", "detail": "projection root not configured"}
        try:
            with self._connection() as con:
                row = con.execute("SELECT v.content FROM managed_artifacts a JOIN artifact_versions v ON v.version_id=a.head_version_id WHERE a.artifact_id=? AND a.lifecycle!='TOMBSTONED'", (artifact_id,)).fetchone()
            if row is None:
                return {"status": "NOT_FOUND"}
            # Explicit low-level repair: the caller has already decided to discard the file.
            self._update_projection(artifact_id, row["content"], float(self.clock()),
                                    force_external=True)
            return self.check_projection(artifact_id)
        except sqlite3.Error:
            return {"status": "UNKNOWN", "detail": "artifact store unavailable"}

    def head_was_erased(self, artifact_id: str) -> bool | None:
        """Whether the canonical head body was cleared by a governed erasure.

        A projection file is a representation, never the authority.  A file restored onto the
        projection path after a forget decision must not turn the empty head back into text
        through the explicit import command, so that consumption entry asks this *before* it
        re-reads the file.  ``None`` means the owner could not read its own canonical head; the
        caller fails closed instead of guessing.  An artifact that simply has no head yet is
        ``False`` (the import's own base-version check still refuses it).
        """

        try:
            with self._connection() as con:
                artifact = con.execute(
                    "SELECT head_version_id FROM managed_artifacts WHERE artifact_id=?",
                    (str(artifact_id),)).fetchone()
                if artifact is None:
                    return None
                if artifact["head_version_id"] is None:
                    return False
                head = con.execute(
                    "SELECT content_erased FROM artifact_versions WHERE version_id=?",
                    (str(artifact["head_version_id"]),)).fetchone()
        except sqlite3.Error:
            return None
        if head is None:
            return None
        return bool(int(head["content_erased"] or 0))

    def reconcile_registered_projections(self) -> dict[str, Any]:
        """Re-derive every owner-registered projection from its canonical head, in place.

        A projection file carries no incarnation of its own: a pre-deletion file copied back onto
        the same path is indistinguishable *by path* from the swept representation.  Recovery
        therefore re-derives the registered files from canonical state (whose erased bodies are
        already empty) instead of trusting the bytes on disk.  Only bindings this owner recorded
        are considered -- an unregistered file in the directory is never guessed at, deleted or
        rewritten -- and a binding that cannot name a safely writable managed file is a named
        failure, not a silent skip.  Ordinary operation is unaffected: a file whose bytes already
        match the head is left untouched.
        """

        self._assert_writer()
        if not self.projection_root:
            # Without a root this owner never wrote a projection, so there is nothing to rebuild.
            return {"status": "NOT_CONFIGURED", "reason": "PROJECTION_ROOT_NOT_CONFIGURED",
                    "reconciled": [], "unchanged": [], "blocked": []}
        try:
            with self._connection() as con:
                bindings = [dict(row) for row in con.execute(
                    "SELECT b.artifact_id,b.relative_path FROM artifact_projection_bindings b"
                    " JOIN managed_artifacts a ON a.artifact_id=b.artifact_id"
                    " ORDER BY b.artifact_id").fetchall()]
                heads: dict[str, Any] = {}
                for binding in bindings:
                    aid = str(binding["artifact_id"])
                    if aid in heads:
                        continue
                    heads[aid] = con.execute(
                        "SELECT v.content,v.content_sha256,a.head_version_id"
                        " FROM managed_artifacts a LEFT JOIN artifact_versions v"
                        " ON v.version_id=a.head_version_id WHERE a.artifact_id=?",
                        (aid,)).fetchone()
        except sqlite3.Error:
            return {"status": "UNKNOWN", "reason": "OWNER_READ_UNAVAILABLE",
                    "reconciled": [], "unchanged": [], "blocked": []}
        reconciled: list[str] = []
        unchanged: list[str] = []
        blocked: list[dict[str, str]] = []
        for binding in bindings:
            aid = str(binding["artifact_id"])
            head = heads.get(aid)
            if head is None or head["head_version_id"] is None:
                blocked.append({"artifact_id": aid, "reason": "NO_CANONICAL_HEAD"})
                continue
            content = head["content"] if isinstance(head["content"], str) else None
            if content is None:
                blocked.append({"artifact_id": aid, "reason": "CANONICAL_PAYLOAD_UNREADABLE"})
                continue
            path, reason = self._safe_projection_path(binding["relative_path"])
            if reason not in (None, "MISSING"):
                # A symlink, an escaping path or a non-regular file is not a representation this
                # owner may rewrite; leave it untouched and report it by name.
                blocked.append({"artifact_id": aid, "reason": str(reason)})
                continue
            if path is not None:
                actual, read_reason = self._read_projection_text(path)
                if read_reason is None and hashlib.sha256(actual.encode()).hexdigest() \
                        == str(head["content_sha256"] or ""):
                    unchanged.append(aid)
                    continue
            state = self._update_projection(aid, content, float(self.clock()),
                                            force_external=True)
            if state == "CLEAN":
                reconciled.append(aid)
            else:
                blocked.append({"artifact_id": aid, "reason": "PROJECTION_WRITE_UNCERTAIN"})
        return {"status": "OK" if not blocked else "BLOCKED",
                "reason": None if not blocked else "PROJECTION_RECONCILE_INCOMPLETE",
                "reconciled": reconciled, "unchanged": unchanged, "blocked": blocked}

    # -- source edges (real lineage) --------------------------------------------------------

    @staticmethod
    def _edge_source(item: Any) -> tuple[str, str, str, str] | None:
        if isinstance(item, Mapping):
            kind = str(item.get("kind") or "").strip()
            ref = str(item.get("ref") or item.get("id") or "").strip()
            namespace = str(item.get("namespace") or "").strip()
            generation = str(item.get("generation") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 3:
            kind, namespace, ref = (str(item[0]), str(item[1]), str(item[2]))
            generation = str(item[3]) if len(item) > 3 else ""
        else:
            return None
        if not kind or not ref:
            return None
        return (kind, namespace, ref, generation)

    def register_source_edges(self, *, child_artifact_id: str, sources: Any,
                              version_id: str | None = None,
                              operation_key: str | None = None) -> dict[str, Any]:
        """Record what one derived artifact/version was built from, inside the owner.

        The edges are the owner's own lineage facts.  They are written by the same owner call
        that commits the derived version, so a derived document cannot exist without the facts a
        reverse closure needs.  Idempotent: the same edge is stored once.
        """

        child = str(child_artifact_id or "").strip()
        if not child:
            return {"status": "DENY", "detail": "child_artifact_id is required"}
        edges = [self._edge_source(item) for item in (sources or ())]
        edges = [edge for edge in edges if edge is not None]
        if not edges:
            return {"status": "NO_CHANGE", "artifact_id": child, "edges": 0}
        kind = "artifactversion" if version_id else "artifact"
        ident = str(version_id or child)
        now = float(self.clock())
        try:
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("SELECT 1 FROM managed_artifacts WHERE artifact_id=?", (child,)).fetchone()
                for source_kind, source_namespace, source_ref, generation in edges:
                    con.execute(
                        "INSERT OR IGNORE INTO artifact_source_edges(child_kind,child_id,"
                        "source_kind,source_namespace,source_ref,generation,created_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (kind, ident, source_kind, source_namespace, source_ref, generation, now))
                con.commit()
        except sqlite3.Error:
            return {"status": "UNKNOWN", "detail": "artifact store unavailable during edge write"}
        return {"status": "OK", "artifact_id": child, "version_id": version_id,
                "edges": len(edges), "operation_key": operation_key}

    def source_closure(self, sources: Any, *, max_depth: int = 8) -> dict[str, Any]:
        """Every artifact/version reachable *from* these sources by the owner's own edges.

        The traversal is bounded (depth and a visited set); an edge that loops or a chain deeper
        than ``max_depth`` sets ``truncated`` instead of hanging.  A source that is itself an
        artifact/version is part of the closure: erasing it must clear its own body too.
        """

        frontier: set[tuple[str, str, str]] = set()
        for item in sources or ():
            edge = self._edge_source(item)
            if edge is not None:
                frontier.add((edge[0], edge[1], edge[2]))
        artifacts: set[str] = set()
        versions: set[str] = set()
        seen: set[tuple[str, str, str]] = set(frontier)
        visited_edges: list[dict[str, str]] = []
        truncated = False
        for kind, namespace, ref in frontier:
            if kind == "artifact":
                artifacts.add(ref)
            elif kind == "artifactversion":
                versions.add(ref)
        try:
            with self._connection() as con:
                for depth in range(max_depth + 1):
                    if not frontier:
                        break
                    rows: list[Any] = []
                    for kind, namespace, ref in frontier:
                        rows.extend(con.execute(
                            "SELECT * FROM artifact_source_edges WHERE source_kind=? "
                            "AND source_namespace=? AND source_ref=?",
                            (kind, namespace, ref)).fetchall())
                    next_frontier: set[tuple[str, str, str]] = set()
                    for row in rows:
                        child_kind, child_id = str(row["child_kind"]), str(row["child_id"])
                        visited_edges.append({
                            "child_kind": child_kind, "child_id": child_id,
                            "source_kind": str(row["source_kind"]),
                            "source_namespace": str(row["source_namespace"]),
                            "source_ref": str(row["source_ref"])})
                        if child_kind == "artifactversion":
                            versions.add(child_id)
                            owner_row = con.execute(
                                "SELECT artifact_id FROM artifact_versions WHERE version_id=?",
                                (child_id,)).fetchone()
                            if owner_row is not None:
                                artifacts.add(str(owner_row["artifact_id"]))
                        elif child_kind == "artifact":
                            artifacts.add(child_id)
                        # A child of this owner lives in the ``supply`` namespace; carrying the
                        # namespace is what lets the next layer's edges match exactly.
                        key = (child_kind, "supply", child_id)
                        if key not in seen:
                            seen.add(key)
                            next_frontier.add(key)
                    frontier = next_frontier
                    if frontier and depth == max_depth:
                        truncated = True
        except sqlite3.Error:
            return {"status": "UNKNOWN", "detail": "artifact store unavailable during closure",
                    "artifacts": [], "versions": [], "edges": [], "truncated": True}
        return {"status": "OK", "artifacts": sorted(artifacts), "versions": sorted(versions),
                "edges": visited_edges, "truncated": truncated}

    def erase_source(self, *, operation_key: str, authorized_subject_id: str,
                     grant: Any = None, authority: Any = None,
                     artifact_ids: Any = (), version_ids: Any = (),
                     barrier_seq: int | None = None) -> dict[str, Any]:
        """Governed source erasure for this owner's document bodies.

        There is no integer shortcut: the caller must hand in the owner-issued
        :class:`~memory_authority.ErasureGrant` *and* the trusted retained authority itself.
        This owner re-proves the grant (owner, conversation, exact seq + digest, currentness),
        computes the *real* reverse closure from its own recorded source edges, and refuses any
        target outside that closure -- an artifact of the same subject that is not derived from
        the barred sources is never erased by a sibling's decision.

        Every version body of the resulting artifacts is cleared in place: version identity,
        number, operation key and audit rows stay, ``content_erased=1`` records that the body is
        gone, the title becomes an opaque tombstone, and every bound projection file is
        overwritten with an empty representation.  The operation is idempotent under its own key.
        """

        self._assert_writer()
        aids = sorted({str(item) for item in (artifact_ids or ()) if str(item)})
        vids = sorted({str(item) for item in (version_ids or ()) if str(item)})
        if not operation_key or not authorized_subject_id:
            return {"status": "DENY", "detail": "operation key and authorized subject required"}
        if grant is None or authority is None:
            return {"status": "DENY",
                    "detail": "a trusted retained authority and its erasure grant are required"}
        try:
            authority.verify_grant(grant)
        except Exception as exc:
            return {"status": "DENY", "detail": "%s: %s" % (type(exc).__name__, exc)}
        if str(getattr(grant, "owner", "")) != authorized_subject_id:
            return {"status": "DENY", "detail": "grant owner mismatch"}
        closure = self.source_closure(getattr(grant, "sources", ()) or ())
        if closure.get("status") != "OK":
            return {"status": "UNKNOWN", "detail": str(closure.get("detail") or "closure failed")}
        allowed_artifacts = set(closure.get("artifacts") or ())
        allowed_versions = set(closure.get("versions") or ())
        if not aids and not vids:
            aids = sorted(allowed_artifacts)
            vids = sorted(allowed_versions)
        if not aids and not vids:
            return {"status": "DENY", "detail": "no source scope"}
        if set(aids) - allowed_artifacts:
            return {"status": "DENY",
                    "detail": "the requested targets are outside the authorized source closure",
                    "allowed_artifacts": sorted(allowed_artifacts),
                    "allowed_versions": sorted(allowed_versions)}
        fingerprint = _digest({"owner": "ManagedArtifact", "subject": authorized_subject_id,
                               "artifacts": aids, "versions": vids,
                               "barrier": int(grant.barrier_seq),
                               "digest": str(grant.barrier_digest),
                               "case": str(grant.case_id)})
        now = float(self.clock())
        try:
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                replay = con.execute(
                    "SELECT fingerprint,result_json FROM artifact_source_erasures "
                    "WHERE operation_key=?", (operation_key,)).fetchone()
                if replay is not None:
                    if str(replay["fingerprint"]) != fingerprint:
                        con.rollback()
                        return {"status": "CONFLICT", "detail": "operation identity collision"}
                    con.commit()
                    return json.loads(replay["result_json"])
                targets: dict[tuple[str, str], None] = {}
                for aid in aids:
                    row = con.execute(
                        "SELECT artifact_id,subject_id FROM managed_artifacts WHERE artifact_id=?",
                        (aid,)).fetchone()
                    if row is None or str(row["subject_id"]) != authorized_subject_id:
                        con.rollback()
                        return {"status": "DENY", "detail": "artifact subject mismatch"}
                    for version in con.execute(
                            "SELECT version_id FROM artifact_versions WHERE artifact_id=?",
                            (aid,)).fetchall():
                        targets[(aid, str(version["version_id"]))] = None
                for vid in vids:
                    row = con.execute(
                        "SELECT v.version_id,v.artifact_id,a.subject_id FROM artifact_versions v "
                        "JOIN managed_artifacts a ON a.artifact_id=v.artifact_id "
                        "WHERE v.version_id=?", (vid,)).fetchone()
                    if row is None or str(row["subject_id"]) != authorized_subject_id:
                        con.rollback()
                        return {"status": "DENY", "detail": "version subject mismatch"}
                    # A version is covered either by its own closure entry or by its artifact's
                    # (erasing an artifact clears every version it holds).
                    if vid not in allowed_versions and str(row["artifact_id"]) not in allowed_artifacts:
                        con.rollback()
                        return {"status": "DENY",
                                "detail": "the requested version is outside the authorized "
                                          "source closure"}
                    targets[(str(row["artifact_id"]), vid)] = None
                # A governed erasure clears the body column; the immutability trigger is dropped
                # for exactly this statement pair and restored inside the same transaction.
                con.execute("DROP TRIGGER IF EXISTS artifact_versions_no_update")
                erased = 0
                empty_sha = hashlib.sha256(b"").hexdigest()
                tombstone_title = "[erased]"
                for aid, vid in targets:
                    # The body hash moves with the body: leaving the old digest would keep a
                    # guessable fingerprint of the forgotten text in the same row.
                    erased += int(con.execute(
                        "UPDATE artifact_versions SET content='',content_sha256=?,"
                        "content_erased=1 "
                        "WHERE version_id=? AND artifact_id=? AND content_erased=0",
                        (empty_sha, vid, aid)).rowcount)
                    # A title is content too.  Leave only an opaque marker on the identity row.
                    con.execute("UPDATE managed_artifacts SET title=?,updated_at=? "
                                "WHERE artifact_id=? AND subject_id=?",
                                (tombstone_title, now, aid, authorized_subject_id))
                    # The request digest is a hash over the old title/content: an honest body
                    # hash would let a guessed body be confirmed.  Replace it inside the same
                    # transaction, keeping the operation key's replay identity.
                    for operation in con.execute(
                            "SELECT operation_key FROM artifact_operations WHERE artifact_id=?",
                            (aid,)).fetchall():
                        con.execute(
                            "UPDATE artifact_operations SET request_digest=? "
                            "WHERE operation_key=?",
                            (hashlib.sha256(("erased:" + str(operation["operation_key"]))
                                            .encode("utf-8")).hexdigest(),
                             str(operation["operation_key"])))
                    con.execute("UPDATE artifact_projection_bindings SET projection_sha256=? "
                                "WHERE artifact_id=?", (empty_sha, aid))
                    con.execute("UPDATE artifact_projection_outbox SET expected_sha256=?,"
                                "state='DIRTY',updated_at=? WHERE artifact_id=?",
                                (empty_sha, now, aid))
                con.execute(
                    "CREATE TRIGGER IF NOT EXISTS artifact_versions_no_update "
                    "BEFORE UPDATE ON artifact_versions BEGIN SELECT RAISE(ABORT,"
                    "'artifact versions are immutable'); END")
                result = {
                    "status": "APPLIED",
                    "artifact_ids": sorted({aid for aid, _ in targets}),
                    "version_ids": sorted({vid for _, vid in targets}),
                    "erased_versions": erased,
                    "barrier_seq": int(grant.barrier_seq),
                    "case_id": str(grant.case_id),
                    "closure": {"artifacts": sorted(allowed_artifacts),
                                "versions": sorted(allowed_versions),
                                "truncated": bool(closure.get("truncated"))},
                }
                con.execute(
                    "INSERT INTO artifact_source_erasures(operation_key,artifact_ids_json,"
                    "version_ids_json,barrier_seq,fingerprint,result_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (operation_key, _json(result["artifact_ids"]),
                     _json(result["version_ids"]), int(grant.barrier_seq), fingerprint,
                     _json(result), now))
                con.commit()
        except sqlite3.Error:
            return {"status": "UNKNOWN", "detail": "artifact store unavailable during erase"}

        compaction: dict[str, Any] = {}
        if self.db is None:
            try:
                with self._connection() as con:
                    mode = str(con.execute("PRAGMA journal_mode").fetchone()[0]).upper()
                    con.execute("PRAGMA secure_delete=ON")
                    if mode == "WAL":
                        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    con.execute("VACUUM")
                    compaction = {"journal_mode": mode, "vacuum": "OK"}
            except sqlite3.Error as exc:
                compaction = {"journal_mode": None, "vacuum": "FAILED",
                              "detail": "%s: %s" % (type(exc).__name__, exc)}
        result["compaction"] = compaction

        projection: list[dict[str, Any]] = []
        for aid in result["artifact_ids"]:
            try:
                # A representation is not the authority: overwrite the bound file with an empty
                # body so the forgotten text is not readable through the projection either.
                state = self._update_projection(aid, "", float(self.clock()),
                                                force_external=True)
                if state is not None:
                    projection.append({"artifact_id": aid, "state": state})
            except Exception:
                projection.append({"artifact_id": aid, "state": "FAILED"})
        result["projection"] = projection
        return result

    def archive(self, artifact_id: str, *, authorized_subject_id: str, expected_revision: int,
                operation_key: str, grant_reservation_id: str, operation_payload: Any = None) -> dict[str, Any]:
        return self._lifecycle(artifact_id, "ARCHIVED", authorized_subject_id, expected_revision, operation_key, grant_reservation_id, operation_payload)

    def tombstone(self, artifact_id: str, *, authorized_subject_id: str, expected_revision: int,
                  operation_key: str, grant_reservation_id: str, operation_payload: Any = None) -> dict[str, Any]:
        return self._lifecycle(artifact_id, "TOMBSTONED", authorized_subject_id, expected_revision, operation_key, grant_reservation_id, operation_payload)

    def _lifecycle(self, artifact_id: str, lifecycle: str, authorized_subject_id: str, expected_revision: int,
                   operation_key: str, grant_reservation_id: str,
                   operation_payload: Any = None) -> dict[str, Any]:
        self._assert_writer()
        if not grant_reservation_id:
            return {"status": "DENY", "detail": "COMMIT_MANAGED_ARTIFACT grant reservation required"}
        expected_operation = "archive_artifact" if lifecycle == "ARCHIVED" else "tombstone_artifact"
        expected_payload = {"operation": expected_operation, "subject_id": authorized_subject_id,
            "artifact_id": artifact_id, "expected_revision": expected_revision}
        if operation_payload is not None and operation_payload != expected_payload:
            return {"status": "DENY", "detail": "route/action payload mismatch"}
        body = {"artifact_id": artifact_id, "lifecycle": lifecycle,
                "expected_revision": expected_revision, "operation_payload": operation_payload,
                "authorized_subject_id": authorized_subject_id}
        digest, now = _digest(body), float(self.clock())
        try:
            with self._connection() as con:
                con.execute("BEGIN IMMEDIATE")
                replay = self._lookup(con, operation_key, digest, lifecycle)
                if replay is not None:
                    con.commit(); return replay
                row = con.execute("SELECT * FROM managed_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
                if not row or row["subject_id"] != authorized_subject_id:
                    con.rollback(); return {"status": "NOT_FOUND", "detail": "artifact subject mismatch"}
                if row["revision"] != expected_revision:
                    con.rollback(); return {"status": "CONFLICT", "revision": row["revision"]}
                if row["lifecycle"] == lifecycle:
                    result = {"status": "NO_CHANGE", "artifact_id": artifact_id,
                              "lifecycle": lifecycle, "revision": expected_revision}
                    con.execute("INSERT INTO artifact_operations VALUES(?,?,?,?,?,?,?)",
                                (operation_key, lifecycle, artifact_id, digest, result["status"], _json(result), now))
                    con.commit(); return result
                if row["lifecycle"] == "TOMBSTONED":
                    con.rollback(); return {"status": "BLOCKED", "detail": "terminal tombstone"}
                con.execute("UPDATE managed_artifacts SET lifecycle=?,revision=revision+1,updated_at=? WHERE artifact_id=? AND revision=?",
                            (lifecycle, now, artifact_id, expected_revision))
                result = {"status": "OK", "artifact_id": artifact_id, "lifecycle": lifecycle,
                          "revision": expected_revision + 1}
                con.execute("INSERT INTO artifact_operations VALUES(?,?,?,?,?,?,?)",
                            (operation_key, lifecycle, artifact_id, digest, result["status"], _json(result), now))
                con.execute("INSERT INTO artifact_effect_receipts VALUES(?,?,?,?,?,?,?)",
                            ("receipt:" + uuid.uuid4().hex, operation_key, artifact_id,
                             row["head_version_id"], lifecycle, "APPLIED", now))
                con.commit(); return result
        except sqlite3.IntegrityError:
            return {"status": "CONFLICT", "detail": "operation key conflict"}
        except sqlite3.Error:
            return {"status": "UNKNOWN", "detail": "artifact store unavailable"}

    def list_artifacts(self, subject_id: str) -> list[dict[str, Any]] | None:
        try:
            with self._connection() as con:
                rows = con.execute("SELECT * FROM managed_artifacts WHERE subject_id=? AND lifecycle!='TOMBSTONED' ORDER BY created_at", (subject_id,)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error:
            return None

    def content_erased_count(self) -> int:
        """How many version bodies this owner has actually cleared (a count, never a body)."""

        try:
            with self._connection() as con:
                return int(con.execute(
                    "SELECT count(*) FROM artifact_versions WHERE content_erased=1").fetchone()[0])
        except sqlite3.Error:
            return -1

    def artifact_count(self) -> int | None:
        try:
            with self._connection() as con:
                return int(con.execute("SELECT count(*) FROM managed_artifacts WHERE lifecycle!='TOMBSTONED'").fetchone()[0])
        except sqlite3.Error:
            return None
