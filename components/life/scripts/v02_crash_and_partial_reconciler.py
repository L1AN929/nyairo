#!/usr/bin/env python3
"""v0.2 Crash-Safe SQLite WAL Store, CP01-CP12 Recovery & Compound Partial Reconciler.

Implements the deterministic (llm_calls == 0) Stage 1 engineering contracts:
1. Real SQLite WAL store (journal_mode=WAL, synchronous=FULL, foreign_keys=ON,
   ``state_tx_guard.state_file_lock`` for companion JSON ledgers/control authority.
2. All 9 transaction stages: T0 (S04), T1 (S09+S10), TD (dispatch prepare),
   TA (worker start), T2 (S01 artifact commit), T3 (S10 result admission),
   TS (settlement creation), T4 (target owner effect/NO_CHANGE), TC (closure).
3. All 12 Crash Points CP01-CP12 with real OS subprocess crash hooks (sentinel
   file + pause for proc.kill()), deterministic restart reconciliation, and
   explicit broken-recovery/naive-resend modes for negative-control verification.
4. Compound Action Child DAG with PARTIAL result vector: never duplicates or
   resends already SUCCEEDED children; idempotency keys strictly exclude
   run_id, writer_epoch, retry_count, and wall-clock time.
5. Bounded query reconciliation ending in QUIESCED_UNRESOLVED while keeping
   outcome="UNKNOWN", blocking only dependent steps while unrelated activities
   remain unblocked; strict separation of HUMAN_ATTESTED_EXPERIENCE from
   PROVIDER_RECEIPT; writer fence + store integrity proof required before
   returning NOT_EXECUTED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import state_tx_guard as _tx  # type: ignore[no-redef]


SCHEMA_VERSION = "nyairo.v02.crash_partial_reconciler.v1"
IDEMPOTENCY_FORMAT_VERSION = "ik_v1"

TX_STAGES: tuple[str, ...] = ("T0", "T1", "TD", "TA", "T2", "T3", "TS", "T4", "TC")
CRASH_POINTS: tuple[str, ...] = tuple(f"CP{i:02d}" for i in range(1, 13))

ACTION_PHASES: tuple[str, ...] = (
    "REQUESTED",
    "DISPATCHING",
    "WAITING_RESULT",
    "RECONCILING",
    "QUIESCED_UNRESOLVED",
    "CLOSED",
)
ACTION_OUTCOMES: tuple[str, ...] = (
    "PENDING",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    "NOT_EXECUTED",
)
ACTIVITY_STATUSES: tuple[str, ...] = (
    "READY",
    "ACTIVE",
    "WAITING_EXTERNAL",
    "BLOCKED",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "ABANDONED",
)
RECEIPT_KINDS: tuple[str, ...] = (
    "OWNER_OPERATION_RECEIPT",
    "PROVIDER_RECEIPT",
    "HUMAN_ATTESTED_EXPERIENCE",
)
PROJECTION_HEALTH_STATES: tuple[str, ...] = (
    "CURRENT",
    "MISSING",
    "DIVERGED",
    "UNBOUND",
)

DEFAULT_RECONCILE_DELAYS_MS: tuple[int, ...] = (1000, 5000, 30000, 120000, 600000)
DEFAULT_MAX_RECONCILE_QUERIES = 5
DEFAULT_TZ = timezone(timedelta(hours=8))

REQUIRED_SQLITE_TABLES: tuple[str, ...] = (
    "runtime_control",
    "decisions",
    "adoptions",
    "activities",
    "actions",
    "action_attempts",
    "action_receipts",
    "artifacts",
    "artifact_versions",
    "operation_mappings",
    "artifact_projections",
    "consequence_envelopes",
    "consequence_obligations",
    "owner_receipts",
    "memory_sources",
    "memory_records",
    "event_journal",
    "outbox",
    "inbox",
)


class ReconcilerContractError(RuntimeError):
    """Raised when an invariant, CAS check, idempotency binding, or fence fails."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _utc_now_iso() -> str:
    return datetime.now(DEFAULT_TZ).isoformat(timespec="seconds")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_idempotency_key(
    *,
    effect_namespace: str,
    provider_account: str,
    subject_id: str,
    origin_branch_id: str,
    logical_action_id: str,
    format_version: str = IDEMPOTENCY_FORMAT_VERSION,
) -> str:
    """Compute canonical idempotency key excluding run_id, writer_epoch, retry, and wall-time."""
    tuple_payload = [
        str(format_version),
        str(effect_namespace),
        str(provider_account),
        str(subject_id),
        str(origin_branch_id),
        str(logical_action_id),
    ]
    encoded = json.dumps(tuple_payload, ensure_ascii=False, separators=(",", ":"))
    return f"{format_version}:{_sha256_hex(encoded)[:32]}"


def compute_payload_hash(payload: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 fingerprint over canonical JSON payload."""
    return _tx.payload_fingerprint(payload)


def atomic_write_text(path: Path, content: str) -> None:
    """Atomic text file write in the same directory using temp -> flush -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class ReconciliationPolicy:
    """Bounded query policy ending in QUIESCED_UNRESOLVED."""

    policy_id: str = "default_local_v1"
    delays_ms: tuple[int, ...] = DEFAULT_RECONCILE_DELAYS_MS
    max_queries: int = DEFAULT_MAX_RECONCILE_QUERIES
    query_horizon_ms: int = 900_000
    on_exhaustion: str = "QUIESCED_UNRESOLVED"


class CrashHookController:
    """Deterministic subprocess crash-hook signaling and pause loop for CP01-CP12."""

    def __init__(
        self,
        *,
        target_cp: str = "",
        sentinel_path: Optional[Path] = None,
        max_wait_seconds: float = 30.0,
    ) -> None:
        self.target_cp = str(target_cp or "").strip()
        self.sentinel_path = Path(sentinel_path) if sentinel_path is not None else None
        self.max_wait_seconds = float(max_wait_seconds)
        self.triggered_hooks: list[str] = []

    def hit(self, cp_name: str, metadata: Optional[Mapping[str, Any]] = None) -> None:
        """Record hook hit; if matching target_cp, write sentinel and block for proc.kill()."""
        self.triggered_hooks.append(cp_name)
        if not self.target_cp or cp_name != self.target_cp:
            return
        payload = {
            "crash_point": cp_name,
            "pid": os.getpid(),
            "hit_at": _utc_now_iso(),
            "metadata": dict(metadata or {}),
        }
        if self.sentinel_path is not None:
            atomic_write_text(
                self.sentinel_path,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        deadline = time.monotonic() + self.max_wait_seconds
        while time.monotonic() < deadline:
            time.sleep(0.02)
        raise SystemExit(99)


class ControlAuthorityStore:
    """External control authority outside business DB rollback domain (writer fence + integrity)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "control_authority.json"

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema": "nyairo.v02.control_authority.v1",
            "active_writer_epoch": 1,
            "fenced_epochs": [],
            "deleted_sources": {},
            "store_integrity_ok": True,
            "updated_at": _utc_now_iso(),
        }

    def read(self) -> dict[str, Any]:
        """Zero-write read of control authority."""
        if not self.path.exists():
            return self._default_state()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def ensure_initialized(self) -> dict[str, Any]:
        with _tx.state_file_lock(self.path):
            if not self.path.exists():
                state = self._default_state()
                _tx.atomic_write_json(self.path, state)
                return state
            return json.loads(self.path.read_text(encoding="utf-8"))

    def allocate_new_epoch(self, *, fence_previous: bool = True) -> dict[str, Any]:
        with _tx.state_file_lock(self.path):
            state = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else self._default_state()
            )
            old_epoch = int(state.get("active_writer_epoch", 1))
            new_epoch = old_epoch + 1
            fenced = set(int(e) for e in state.get("fenced_epochs", []))
            if fence_previous:
                fenced.add(old_epoch)
            state["active_writer_epoch"] = new_epoch
            state["fenced_epochs"] = sorted(fenced)
            state["updated_at"] = _utc_now_iso()
            _tx.atomic_write_json(self.path, state)
            return state

    def is_epoch_fenced(self, epoch: int) -> bool:
        state = self.read()
        fenced = set(int(e) for e in state.get("fenced_epochs", []))
        active = int(state.get("active_writer_epoch", 1))
        return int(epoch) in fenced and int(epoch) < active

    def is_epoch_active(self, epoch: int) -> bool:
        state = self.read()
        fenced = set(int(e) for e in state.get("fenced_epochs", []))
        active = int(state.get("active_writer_epoch", 1))
        return int(epoch) == active and int(epoch) not in fenced

    def set_store_integrity_flag(self, integrity_ok: bool) -> dict[str, Any]:
        with _tx.state_file_lock(self.path):
            state = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else self._default_state()
            )
            state["store_integrity_ok"] = bool(integrity_ok)
            state["updated_at"] = _utc_now_iso()
            _tx.atomic_write_json(self.path, state)
            return state

    def mark_source_deleted(self, source_id: str, generation: int) -> dict[str, Any]:
        with _tx.state_file_lock(self.path):
            state = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else self._default_state()
            )
            deleted = dict(state.get("deleted_sources", {}))
            deleted[str(source_id)] = int(generation)
            state["deleted_sources"] = deleted
            state["updated_at"] = _utc_now_iso()
            _tx.atomic_write_json(self.path, state)
            return state


class ExternalProviderLedger:
    """Isolated persistent external receiver ledger for side-effect & lookup verification."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "external_provider_ledger.json"

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema": "nyairo.v02.external_provider_ledger.v1",
            "next_seq": 1,
            "invocations": [],
            "lookup_enabled": True,
            "conclusive_not_found": True,
        }

    def read(self) -> dict[str, Any]:
        """Zero-write read of external provider ledger."""
        if not self.path.exists():
            return self._default_state()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def configure_lookup(self, *, lookup_enabled: bool, conclusive_not_found: bool) -> None:
        with _tx.state_file_lock(self.path):
            state = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else self._default_state()
            )
            state["lookup_enabled"] = bool(lookup_enabled)
            state["conclusive_not_found"] = bool(conclusive_not_found)
            _tx.atomic_write_json(self.path, state)

    def invoke(
        self,
        *,
        logical_action_id: str,
        idempotency_key: str,
        attempt_id: str,
        payload_hash: str,
        supports_idempotency: bool = False,
        outcome: str = "SUCCEEDED",
        drop_ack: bool = False,
    ) -> dict[str, Any]:
        """Simulate external side-effect delivery; persists invocation before optionally dropping ack."""
        with _tx.state_file_lock(self.path):
            state = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else self._default_state()
            )
            invocations = list(state.get("invocations", []))
            if supports_idempotency:
                for existing in invocations:
                    if existing.get("idempotency_key") == idempotency_key:
                        if existing.get("payload_hash") != payload_hash:
                            raise ReconcilerContractError(
                                "PROVIDER_IDEMPOTENCY_CONFLICT",
                                f"key={idempotency_key}",
                            )
                        if drop_ack:
                            raise TimeoutError("provider_ack_lost_after_receive")
                        return dict(existing)

            seq = int(state.get("next_seq", 1))
            state["next_seq"] = seq + 1
            receipt_id = f"prov_rcpt_{seq:06d}"
            entry = {
                "invocation_seq": seq,
                "logical_action_id": logical_action_id,
                "idempotency_key": idempotency_key,
                "attempt_id": attempt_id,
                "payload_hash": payload_hash,
                "outcome": outcome,
                "provider_receipt_id": receipt_id if outcome == "SUCCEEDED" else None,
                "recorded_at": _utc_now_iso(),
            }
            invocations.append(entry)
            state["invocations"] = invocations
            _tx.atomic_write_json(self.path, state)

        if drop_ack:
            raise TimeoutError("provider_ack_lost_after_receive")
        return entry

    def query(self, *, idempotency_key: str) -> dict[str, Any]:
        """Query external provider ledger without mutating invocation counts."""
        state = self.read()
        if not state.get("lookup_enabled", True):
            return {
                "found": False,
                "conclusive_not_found": False,
                "reason": "PROVIDER_LOOKUP_UNAVAILABLE",
            }
        matches = [
            row
            for row in state.get("invocations", [])
            if row.get("idempotency_key") == idempotency_key
        ]
        if matches:
            latest = dict(matches[-1])
            return {
                "found": True,
                "conclusive_not_found": True,
                "invocation": latest,
                "provider_receipt_id": latest.get("provider_receipt_id"),
                "outcome": latest.get("outcome"),
            }
        return {
            "found": False,
            "conclusive_not_found": bool(state.get("conclusive_not_found", True)),
            "reason": "NOT_FOUND_AT_PROVIDER",
        }

    def count_for_key(self, idempotency_key: str) -> int:
        state = self.read()
        return sum(
            1
            for row in state.get("invocations", [])
            if row.get("idempotency_key") == idempotency_key
        )

    def count_for_logical_action(self, logical_action_id: str) -> int:
        state = self.read()
        return sum(
            1
            for row in state.get("invocations", [])
            if row.get("logical_action_id") == logical_action_id
        )

    def total_invocations(self) -> int:
        return len(self.read().get("invocations", []))


class CrashAndPartialReconcilerStore:
    """SQLite WAL canonical store implementing T0-TC, CP01-CP12, Compound DAG, and Reconcile."""

    def __init__(
        self,
        root: Path | str,
        *,
        crash_hook: Optional[CrashHookController] = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "canonical_life.sqlite3"
        self.control = ControlAuthorityStore(self.root)
        self.provider = ExternalProviderLedger(self.root)
        self.crash_hook = crash_hook or CrashHookController()
        self.llm_calls = 0

    def _connect_write(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _connect_readonly(self) -> Optional[sqlite3.Connection]:
        if not self.db_path.is_file():
            return None
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        """Create schema with WAL, synchronous=FULL, foreign_keys=ON, and structured constraints."""
        self.control.ensure_initialized()
        conn = self._connect_write()
        try:
            # Need to parameterize separately or format the string
            sql = """
                CREATE TABLE IF NOT EXISTS runtime_control (
                    control_key TEXT PRIMARY KEY,
                    active_writer_epoch INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    origin_branch_id TEXT NOT NULL,
                    input_manifest_json TEXT NOT NULL,
                    selected_kind TEXT NOT NULL CHECK(selected_kind IN ('ADOPT','NO_ACTION')),
                    permission_ok INTEGER NOT NULL DEFAULT 1 CHECK(permission_ok IN (0,1)),
                    expires_at TEXT NOT NULL,
                    writer_epoch INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS adoptions (
                    adoption_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                    owner_domain TEXT NOT NULL DEFAULT 'S09',
                    status TEXT NOT NULL CHECK(status IN ('EFFECTIVE','REVOKED','SUPERSEDED','CLOSED')),
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS activities (
                    activity_id TEXT PRIMARY KEY,
                    adoption_id TEXT NOT NULL UNIQUE REFERENCES adoptions(adoption_id),
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'READY','ACTIVE','WAITING_EXTERNAL','BLOCKED','PAUSED',
                        'COMPLETED','FAILED','CANCELLED','ABANDONED'
                    )),
                    revision INTEGER NOT NULL DEFAULT 1,
                    blocked_on_action_id TEXT,
                    unresolved_action_ref TEXT,
                    checkpoint_json TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    logical_action_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    origin_branch_id TEXT NOT NULL,
                    provider_account TEXT NOT NULL,
                    effect_namespace TEXT NOT NULL,
                    parent_action_id TEXT REFERENCES actions(action_id),
                    child_index INTEGER,
                    is_compound INTEGER NOT NULL DEFAULT 0 CHECK(is_compound IN (0,1)),
                    adoption_id TEXT REFERENCES adoptions(adoption_id),
                    activity_id TEXT REFERENCES activities(activity_id),
                    phase TEXT NOT NULL CHECK(phase IN (
                        'REQUESTED','DISPATCHING','WAITING_RESULT',
                        'RECONCILING','QUIESCED_UNRESOLVED','CLOSED'
                    )),
                    outcome TEXT NOT NULL CHECK(outcome IN (
                        'PENDING','SUCCEEDED','FAILED','UNKNOWN','NOT_EXECUTED'
                    )),
                    reconcile_query_count INTEGER NOT NULL DEFAULT 0,
                    max_reconcile_queries INTEGER NOT NULL DEFAULT 5,
                    reconcile_policy_id TEXT NOT NULL DEFAULT 'default_local_v1',
                    operation_id TEXT NOT NULL UNIQUE,
                    provider_supports_idempotency INTEGER NOT NULL DEFAULT 0 CHECK(provider_supports_idempotency IN (0,1)),
                    conclusive_not_found INTEGER NOT NULL DEFAULT 1 CHECK(conclusive_not_found IN (0,1)),
                    writer_epoch INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS action_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES actions(action_id),
                    attempt_seq INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'PREPARED','STARTED','PROVIDER_ACCEPTED',
                        'COMPLETED','FAILED','UNKNOWN','NOT_EXECUTED'
                    )),
                    writer_epoch INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    provider_request_id TEXT,
                    result_outcome TEXT,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(action_id, attempt_seq)
                );

                CREATE TABLE IF NOT EXISTS action_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES actions(action_id),
                    attempt_id TEXT REFERENCES action_attempts(attempt_id),
                    receipt_kind TEXT NOT NULL CHECK(receipt_kind IN (
                        'OWNER_OPERATION_RECEIPT','PROVIDER_RECEIPT','HUMAN_ATTESTED_EXPERIENCE'
                    )),
                    claimed_outcome TEXT NOT NULL,
                    authoritative_for_machine_success INTEGER NOT NULL CHECK(authoritative_for_machine_success IN (0,1)),
                    allows_stop_investigation INTEGER NOT NULL DEFAULT 0 CHECK(allows_stop_investigation IN (0,1)),
                    actor_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    controller TEXT NOT NULL,
                    head_version_id TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    lifecycle TEXT NOT NULL CHECK(lifecycle IN ('ACTIVE','ARCHIVED','TOMBSTONED')),
                    deleted_generation INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS artifact_versions (
                    version_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    parent_version_id TEXT,
                    content_body TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    utf8_byte_length INTEGER NOT NULL,
                    origin_kind TEXT NOT NULL CHECK(origin_kind IN ('ACTION_COMMIT','EXPLICIT_IMPORT')),
                    action_id TEXT,
                    creator_receipt_id TEXT NOT NULL UNIQUE,
                    writer_epoch INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operation_mappings (
                    operation_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    version_id TEXT NOT NULL REFERENCES artifact_versions(version_id),
                    creator_receipt_id TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    committed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifact_projections (
                    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(artifact_id),
                    file_path TEXT NOT NULL,
                    known_projection_hash TEXT,
                    generated_version_id TEXT,
                    health TEXT NOT NULL CHECK(health IN ('CURRENT','MISSING','DIVERGED','UNBOUND')),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS consequence_envelopes (
                    envelope_id TEXT PRIMARY KEY,
                    source_fact_id TEXT NOT NULL UNIQUE,
                    action_id TEXT NOT NULL REFERENCES actions(action_id),
                    contract_version TEXT NOT NULL,
                    phase TEXT NOT NULL CHECK(phase IN ('OPEN','BLOCKED','CLOSED')),
                    created_at TEXT NOT NULL,
                    closed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS consequence_obligations (
                    obligation_id TEXT PRIMARY KEY,
                    envelope_id TEXT NOT NULL REFERENCES consequence_envelopes(envelope_id),
                    target_owner TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0,1)),
                    status TEXT NOT NULL CHECK(status IN ('PENDING','APPLIED','NO_CHANGE','BLOCKED')),
                    owner_receipt_id TEXT,
                    reason_code TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(envelope_id, target_owner, kind, target_ref)
                );

                CREATE TABLE IF NOT EXISTS owner_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    obligation_id TEXT NOT NULL UNIQUE REFERENCES consequence_obligations(obligation_id),
                    owner_domain TEXT NOT NULL,
                    disposition TEXT NOT NULL CHECK(disposition IN ('APPLIED','NO_CHANGE')),
                    reason_code TEXT NOT NULL,
                    resulting_ref TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_sources (
                    source_id TEXT PRIMARY KEY,
                    deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
                    deletion_generation INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES memory_sources(source_id),
                    source_fact_id TEXT NOT NULL,
                    action_id TEXT,
                    deletion_generation INTEGER NOT NULL,
                    admission_status TEXT NOT NULL CHECK(admission_status IN (
                        'COMMITTED_RECORD','COMMITTED_NO_CHANGE','BLOCKED','REJECTED'
                    )),
                    content TEXT NOT NULL,
                    receipt_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_journal (
                    event_id TEXT PRIMARY KEY,
                    producer TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    aggregate_seq INTEGER NOT NULL,
                    causation_refs_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    writer_epoch INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(producer, aggregate_id, aggregate_seq)
                );

                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY REFERENCES event_journal(event_id),
                    destination TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('PENDING','DISPATCHED','ACKED')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inbox (
                    consumer TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PROCESSED','IGNORED')),
                    result_ref TEXT,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY(consumer, event_id)
                );
            """
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO runtime_control(control_key, active_writer_epoch, updated_at) VALUES (?, ?, ?)",
                ('primary', 1, _utc_now_iso()),
            )

        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def pragma_snapshot(self) -> dict[str, Any]:
        """Inspect active SQLite PRAGMA settings for WAL, synchronous, and foreign_keys."""
        conn = self._connect_write()
        try:
            jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            return {
                "journal_mode": str(jm).lower(),
                "synchronous": int(sync),
                "foreign_keys": int(fk),
            }
        finally:
            conn.close()

    def _next_agg_seq(self, conn: sqlite3.Connection, producer: str, aggregate_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(aggregate_seq), 0) AS m FROM event_journal WHERE producer=? AND aggregate_id=?",
            (producer, aggregate_id),
        ).fetchone()
        return int(row["m"]) + 1

    def _append_event(
        self,
        conn: sqlite3.Connection,
        *,
        event_id: str,
        producer: str,
        event_type: str,
        aggregate_id: str,
        causation_refs: Sequence[str],
        payload: Mapping[str, Any],
        writer_epoch: int,
        outbox_dest: Optional[str] = None,
    ) -> None:
        seq = self._next_agg_seq(conn, producer, aggregate_id)
        now_iso = _utc_now_iso()
        conn.execute(
            """
            INSERT INTO event_journal(
                event_id, producer, event_type, aggregate_id, aggregate_seq,
                causation_refs_json, payload_json, writer_epoch, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                producer,
                event_type,
                aggregate_id,
                seq,
                json.dumps(list(causation_refs), ensure_ascii=False),
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                int(writer_epoch),
                now_iso,
            ),
        )
        if outbox_dest is not None:
            conn.execute(
                """
                INSERT INTO outbox(event_id, destination, state, created_at, updated_at)
                VALUES (?, ?, 'PENDING', ?, ?)
                """,
                (event_id, outbox_dest, now_iso, now_iso),
            )

    def create_decision(
        self,
        *,
        decision_id: str,
        subject_id: str = "sub1",
        origin_branch_id: str = "br1",
        input_manifest_json: str = "{}",
        selected_kind: str = "ADOPT",
        expires_at: str = "2099-01-01T00:00:00",
        writer_epoch: int = 1,
    ) -> None:
        conn = self._connect_write()
        try:
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, subject_id, origin_branch_id, input_manifest_json, 
                    selected_kind, expires_at, writer_epoch, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id, subject_id, origin_branch_id, input_manifest_json,
                    selected_kind, expires_at, writer_epoch, _utc_now_iso(),
                ),
            )
        finally:
            conn.close()

    def create_action_attempt(
        self,
        *,
        attempt_id: str,
        action_id: str,
        attempt_seq: int = 1,
        writer_epoch: int = 1,
        run_id: str = "run1",
    ) -> None:
        conn = self._connect_write()
        try:
            conn.execute(
                """
                INSERT INTO action_attempts (
                    attempt_id, action_id, attempt_seq, state, writer_epoch, 
                    run_id, evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'COMPLETED', ?, ?, '{}', ?, ?)
                """,
                (
                    attempt_id, action_id, attempt_seq, writer_epoch, run_id, 
                    _utc_now_iso(), _utc_now_iso(),
                ),
            )
        finally:
            conn.close()

    def _check_active_writer(self, writer_epoch: int) -> None:
        if not self.control.is_epoch_active(writer_epoch):
            raise ReconcilerContractError(
                "WRITER_EPOCH_FENCED",
                f"epoch={writer_epoch} active={self.control.read().get('active_writer_epoch')}",
            )

    # ------------------------------------------------------------------
    # Transaction Stage T0: S04 Decision + AdoptionRequested Outbox
    # ------------------------------------------------------------------

    def tx_t0_commit_decision(
        self,
        *,
        decision_id: str,
        request_id: str,
        subject_id: str = "subj_primary",
        origin_branch_id: str = "branch_main",
        selected_kind: str = "ADOPT",
        permission_ok: bool = True,
        expires_at: str = "2099-01-01T00:00:00+08:00",
        input_manifest: Optional[Mapping[str, Any]] = None,
        writer_epoch: int = 1,
    ) -> dict[str, Any]:
        """T0 (S04): Atomically persist accepted Decision fact, input manifest, and AdoptionRequested outbox."""
        self._check_active_writer(writer_epoch)
        now_iso = _utc_now_iso()
        event_id = f"evt_t0_{decision_id}"
        manifest = dict(input_manifest or {"sources": ["obs_1"]})
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO decisions(
                    decision_id, subject_id, origin_branch_id, input_manifest_json,
                    selected_kind, permission_ok, expires_at, writer_epoch, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    subject_id,
                    origin_branch_id,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    selected_kind,
                    1 if permission_ok else 0,
                    expires_at,
                    int(writer_epoch),
                    now_iso,
                ),
            )
            if selected_kind == "ADOPT":
                self._append_event(
                    conn,
                    event_id=event_id,
                    producer="S04",
                    event_type="AdoptionRequested",
                    aggregate_id=decision_id,
                    causation_refs=[decision_id],
                    payload={
                        "decision_id": decision_id,
                        "request_id": request_id,
                        "subject_id": subject_id,
                        "origin_branch_id": origin_branch_id,
                    },
                    writer_epoch=writer_epoch,
                    outbox_dest="S09",
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        self.crash_hook.hit("CP01", {"decision_id": decision_id, "request_id": request_id})
        return {"decision_id": decision_id, "request_id": request_id, "event_id": event_id}

    def update_decision_gate(
        self,
        *,
        decision_id: str,
        permission_ok: Optional[bool] = None,
        expires_at: Optional[str] = None,
    ) -> None:
        """Update decision permission/expiry (simulates revocation or expiry before S09 intake)."""
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if permission_ok is not None:
                conn.execute(
                    "UPDATE decisions SET permission_ok=? WHERE decision_id=?",
                    (1 if permission_ok else 0, decision_id),
                )
            if expires_at is not None:
                conn.execute(
                    "UPDATE decisions SET expires_at=? WHERE decision_id=?",
                    (expires_at, decision_id),
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Transaction Stage T1: Owner-coordinated Adoption + Activity + Action + Outbox
    # ------------------------------------------------------------------

    def tx_t1_commit_adoption_activity_action(
        self,
        *,
        decision_id: str,
        request_id: str,
        logical_action_id: str,
        effect_namespace: str = "artifact.commit",
        provider_account: str = "local_s01",
        payload: Optional[Mapping[str, Any]] = None,
        activity_title: str = "Managed draft write",
        provider_supports_idempotency: bool = False,
        conclusive_not_found: bool = True,
        max_reconcile_queries: int = DEFAULT_MAX_RECONCILE_QUERIES,
        writer_epoch: int = 1,
        now_iso: Optional[str] = None,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """T1: Validate Decision expiry/permission and commit Adoption + Activity + Action + Outbox."""
        self._check_active_writer(writer_epoch)
        self.crash_hook.hit("CP02", {"decision_id": decision_id, "request_id": request_id})

        check_now = now_iso or _utc_now_iso()
        action_payload = dict(payload or {"artifact_id": "art_1", "content": "hello"})
        p_hash = compute_payload_hash(action_payload)

        adoption_id = f"adopt_{request_id}"
        activity_id = f"actv_{request_id}"
        action_id = f"act_{logical_action_id}"
        operation_id = f"op_{logical_action_id}"
        event_id = f"evt_t1_{request_id}"

        conn = self._connect_write()
        try:
            if broken_mode != "autocommit_activity_before_outbox":
                conn.execute("BEGIN IMMEDIATE")

            dec = conn.execute(
                "SELECT * FROM decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            if dec is None:
                raise ReconcilerContractError("DECISION_NOT_FOUND", decision_id)

            if broken_mode != "skip_decision_validation":
                if int(dec["permission_ok"]) != 1:
                    raise ReconcilerContractError("DECISION_PERMISSION_DENIED", decision_id)
                if str(dec["expires_at"]) <= check_now:
                    raise ReconcilerContractError("DECISION_EXPIRED", decision_id)

            existing = conn.execute(
                "SELECT * FROM adoptions WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None and broken_mode != "duplicate_adoption_new_id":
                actv = conn.execute(
                    "SELECT * FROM activities WHERE adoption_id=?", (existing["adoption_id"],)
                ).fetchone()
                act = conn.execute(
                    "SELECT * FROM actions WHERE adoption_id=?", (existing["adoption_id"],)
                ).fetchone()
                if broken_mode != "autocommit_activity_before_outbox":
                    conn.execute("COMMIT")
                return {
                    "status": "ALREADY_ADOPTED",
                    "adoption_id": existing["adoption_id"],
                    "activity_id": actv["activity_id"] if actv else None,
                    "action_id": act["action_id"] if act else None,
                    "idempotency_key": act["idempotency_key"] if act else None,
                }

            if broken_mode == "duplicate_adoption_new_id":
                suffix = _sha256_hex(f"{request_id}:{time.monotonic_ns()}")[:8]
                adoption_id = f"adopt_{request_id}_{suffix}"
                activity_id = f"actv_{request_id}_{suffix}"
                action_id = f"act_{logical_action_id}_{suffix}"
                operation_id = f"op_{logical_action_id}_{suffix}"
                event_id = f"evt_t1_{request_id}_{suffix}"
                effective_req_id = f"{request_id}_{suffix}"
                idem_key = compute_idempotency_key(
                    effect_namespace=effect_namespace,
                    provider_account=provider_account,
                    subject_id=str(dec["subject_id"]),
                    origin_branch_id=str(dec["origin_branch_id"]),
                    logical_action_id=f"{logical_action_id}_{suffix}",
                )
            else:
                effective_req_id = request_id
                idem_key = compute_idempotency_key(
                    effect_namespace=effect_namespace,
                    provider_account=provider_account,
                    subject_id=str(dec["subject_id"]),
                    origin_branch_id=str(dec["origin_branch_id"]),
                    logical_action_id=logical_action_id,
                )

            ts = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO adoptions(adoption_id, request_id, decision_id, owner_domain, status, revision, created_at)
                VALUES (?, ?, ?, 'S09', 'EFFECTIVE', 1, ?)
                """,
                (adoption_id, effective_req_id, decision_id, ts),
            )
            conn.execute(
                """
                INSERT INTO activities(activity_id, adoption_id, title, status, revision, updated_at)
                VALUES (?, ?, ?, 'ACTIVE', 1, ?)
                """,
                (activity_id, adoption_id, activity_title, ts),
            )

            # CP04: inside T1 after Activity SQL write, before outbox SQL write
            self.crash_hook.hit(
                "CP04",
                {"adoption_id": adoption_id, "activity_id": activity_id, "request_id": request_id},
            )

            conn.execute(
                """
                INSERT INTO actions(
                    action_id, idempotency_key, payload_hash, logical_action_id,
                    subject_id, origin_branch_id, provider_account, effect_namespace,
                    adoption_id, activity_id, phase, outcome,
                    max_reconcile_queries, operation_id,
                    provider_supports_idempotency, conclusive_not_found,
                    writer_epoch, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REQUESTED', 'PENDING', ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    action_id,
                    idem_key,
                    p_hash,
                    logical_action_id,
                    str(dec["subject_id"]),
                    str(dec["origin_branch_id"]),
                    provider_account,
                    effect_namespace,
                    adoption_id,
                    activity_id,
                    int(max_reconcile_queries),
                    operation_id,
                    1 if provider_supports_idempotency else 0,
                    1 if conclusive_not_found else 0,
                    int(writer_epoch),
                    ts,
                    ts,
                ),
            )
            self._append_event(
                conn,
                event_id=event_id,
                producer="S09",
                event_type="ActionDispatchRequested",
                aggregate_id=action_id,
                causation_refs=[decision_id, adoption_id, activity_id],
                payload={
                    "action_id": action_id,
                    "activity_id": activity_id,
                    "adoption_id": adoption_id,
                    "idempotency_key": idem_key,
                },
                writer_epoch=writer_epoch,
                outbox_dest="S10",
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO inbox(consumer, event_id, status, result_ref, processed_at)
                VALUES ('S09_adoption', ?, 'PROCESSED', ?, ?)
                """,
                (f"evt_t0_{decision_id}", adoption_id, ts),
            )
            if broken_mode != "autocommit_activity_before_outbox":
                conn.execute("COMMIT")
        except BaseException:
            if broken_mode != "autocommit_activity_before_outbox":
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            conn.close()

        # CP03: immediately after T1 committed (Adoption + Activity + Action + Outbox all durable)
        self.crash_hook.hit(
            "CP03",
            {"adoption_id": adoption_id, "activity_id": activity_id, "action_id": action_id},
        )
        return {
            "status": "ADOPTED",
            "adoption_id": adoption_id,
            "activity_id": activity_id,
            "action_id": action_id,
            "idempotency_key": idem_key,
            "operation_id": operation_id,
            "event_id": event_id,
        }

    # ------------------------------------------------------------------
    # Transaction Stages TD & TA: Dispatch Prepare + Worker Start
    # ------------------------------------------------------------------

    def consume_outbox_to_inbox(
        self,
        *,
        consumer: str,
        event_id: str,
        result_ref: str,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """At-least-once consumer inbox deduplication for outbox events (CP05)."""
        effective_event_id = (
            f"{event_id}_mutated_{time.monotonic_ns()}"
            if broken_mode == "rotate_event_id_bypass_inbox"
            else event_id
        )
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM inbox WHERE consumer=? AND event_id=?",
                (consumer, effective_event_id),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return {
                    "deduplicated": True,
                    "consumer": consumer,
                    "event_id": effective_event_id,
                    "result_ref": existing["result_ref"],
                }
            ts = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO inbox(consumer, event_id, status, result_ref, processed_at)
                VALUES (?, ?, 'PROCESSED', ?, ?)
                """,
                (consumer, effective_event_id, result_ref, ts),
            )
            conn.execute(
                "UPDATE outbox SET state='ACKED', updated_at=? WHERE event_id=?",
                (ts, event_id),
            )
            conn.execute("COMMIT")
            return {
                "deduplicated": False,
                "consumer": consumer,
                "event_id": effective_event_id,
                "result_ref": result_ref,
            }
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def tx_td_prepare_dispatch(
        self,
        *,
        action_id: str,
        run_id: str = "run_1",
        writer_epoch: int = 1,
        trigger_cp05: bool = False,
        trigger_cp_after_td: bool = False,
    ) -> dict[str, Any]:
        """TD (S10): Verify fence/adoption, reserve PREPARED attempt + DispatchAdmitted outbox."""
        self._check_active_writer(writer_epoch)
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            act = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if act is None:
                raise ReconcilerContractError("ACTION_NOT_FOUND", action_id)
            if act["adoption_id"]:
                adopt = conn.execute(
                    "SELECT * FROM adoptions WHERE adoption_id=?", (act["adoption_id"],)
                ).fetchone()
                if adopt is None or adopt["status"] != "EFFECTIVE":
                    raise ReconcilerContractError("ADOPTION_NOT_EFFECTIVE", str(act["adoption_id"]))

            max_seq_row = conn.execute(
                "SELECT COALESCE(MAX(attempt_seq), 0) AS m FROM action_attempts WHERE action_id=?",
                (action_id,),
            ).fetchone()
            next_seq = int(max_seq_row["m"]) + 1
            attempt_id = f"att_{action_id}_{next_seq:03d}"
            ts = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO action_attempts(
                    attempt_id, action_id, attempt_seq, state, writer_epoch,
                    run_id, evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'PREPARED', ?, ?, '{}', ?, ?)
                """,
                (attempt_id, action_id, next_seq, int(writer_epoch), run_id, ts, ts),
            )
            conn.execute(
                """
                UPDATE actions SET phase='DISPATCHING', revision=revision+1, updated_at=?
                WHERE action_id=?
                """,
                (ts, action_id),
            )
            self._append_event(
                conn,
                event_id=f"evt_td_{attempt_id}",
                producer="S10",
                event_type="DispatchAdmitted",
                aggregate_id=action_id,
                causation_refs=[action_id, attempt_id],
                payload={"action_id": action_id, "attempt_id": attempt_id, "attempt_seq": next_seq},
                writer_epoch=writer_epoch,
                outbox_dest="worker",
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        if trigger_cp05:
            self.crash_hook.hit("CP05", {"action_id": action_id, "attempt_id": attempt_id})
        if trigger_cp_after_td:
            self.crash_hook.hit("CP_AFTER_TD", {"action_id": action_id, "attempt_id": attempt_id})
        return {"action_id": action_id, "attempt_id": attempt_id, "attempt_seq": next_seq}

    def tx_ta_start_attempt(
        self,
        *,
        attempt_id: str,
        writer_epoch: int = 1,
        trigger_cp_after_ta: bool = False,
    ) -> dict[str, Any]:
        """TA (worker): CAS PREPARED -> STARTED, re-verify fence, emit ActionAttempted."""
        self._check_active_writer(writer_epoch)
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ts = _utc_now_iso()
            cur = conn.execute(
                """
                UPDATE action_attempts
                SET state='STARTED', writer_epoch=?, updated_at=?
                WHERE attempt_id=? AND state='PREPARED'
                """,
                (int(writer_epoch), ts, attempt_id),
            )
            if cur.rowcount != 1:
                raise ReconcilerContractError("TA_CAS_CONFLICT", attempt_id)
            att = conn.execute(
                "SELECT * FROM action_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            action_id = str(att["action_id"])
            conn.execute(
                """
                UPDATE actions SET phase='WAITING_RESULT', revision=revision+1, updated_at=?
                WHERE action_id=?
                """,
                (ts, action_id),
            )
            self._append_event(
                conn,
                event_id=f"evt_ta_{attempt_id}",
                producer="S10",
                event_type="ActionAttempted",
                aggregate_id=action_id,
                causation_refs=[action_id, attempt_id],
                payload={"action_id": action_id, "attempt_id": attempt_id},
                writer_epoch=writer_epoch,
                outbox_dest="S09",
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        if trigger_cp_after_ta:
            self.crash_hook.hit("CP_AFTER_TA", {"attempt_id": attempt_id})
        return {"attempt_id": attempt_id, "state": "STARTED"}

    def execute_external_attempt(
        self,
        *,
        action_id: str,
        attempt_id: str,
        drop_ack: bool = False,
        trigger_cp06: bool = False,
    ) -> dict[str, Any]:
        """Execute external provider I/O after TA commit; handles CP06 after submit."""
        conn = self._connect_readonly()
        assert conn is not None
        try:
            act = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if act is None:
                raise ReconcilerContractError("ACTION_NOT_FOUND", action_id)
            act_dict = dict(act)
        finally:
            conn.close()

        ack_lost = False
        provider_entry: Optional[dict[str, Any]] = None
        try:
            provider_entry = self.provider.invoke(
                logical_action_id=str(act_dict["logical_action_id"]),
                idempotency_key=str(act_dict["idempotency_key"]),
                attempt_id=attempt_id,
                payload_hash=str(act_dict["payload_hash"]),
                supports_idempotency=bool(act_dict["provider_supports_idempotency"]),
                outcome="SUCCEEDED",
                drop_ack=drop_ack,
            )
        except TimeoutError:
            ack_lost = True

        if trigger_cp06:
            self.crash_hook.hit(
                "CP06",
                {"action_id": action_id, "attempt_id": attempt_id, "ack_lost": ack_lost},
            )
        return {
            "action_id": action_id,
            "attempt_id": attempt_id,
            "ack_lost": ack_lost,
            "provider_entry": provider_entry,
        }

    # ------------------------------------------------------------------
    # Transaction Stage T2: S01 Artifact Commit + Projection
    # ------------------------------------------------------------------

    def tx_t2_commit_artifact_version(
        self,
        *,
        artifact_id: str,
        action_id: str,
        content_body: str,
        expected_head_version_id: Optional[str] = None,
        projection_path: Optional[Path] = None,
        writer_epoch: int = 1,
        trigger_cp07: bool = False,
        trigger_cp09: bool = False,
        trigger_cp10: bool = False,
    ) -> dict[str, Any]:
        """T2 (S01): Atomically commit ArtifactVersion, head CAS, operation mapping, creator receipt, outbox."""
        self._check_active_writer(writer_epoch)
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            act = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if act is None:
                raise ReconcilerContractError("ACTION_NOT_FOUND", action_id)
            operation_id = str(act["operation_id"])

            existing_op = conn.execute(
                "SELECT * FROM operation_mappings WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if existing_op is not None:
                conn.execute("COMMIT")
                return {
                    "status": "IDEMPOTENT_REPLAY",
                    "artifact_id": existing_op["artifact_id"],
                    "version_id": existing_op["version_id"],
                    "creator_receipt_id": existing_op["creator_receipt_id"],
                    "sha256": existing_op["sha256"],
                }

            art = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if art is None:
                if expected_head_version_id is not None:
                    raise ReconcilerContractError("ARTIFACT_CAS_HEAD_MISMATCH", artifact_id)
                conn.execute(
                    """
                    INSERT INTO artifacts(artifact_id, controller, head_version_id, revision, lifecycle, deleted_generation)
                    VALUES (?, 'S01', NULL, 0, 'ACTIVE', 0)
                    """,
                    (artifact_id,),
                )
                current_head = None
            else:
                if art["lifecycle"] == "TOMBSTONED":
                    raise ReconcilerContractError("ARTIFACT_TOMBSTONED", artifact_id)
                current_head = art["head_version_id"]
                if current_head != expected_head_version_id:
                    raise ReconcilerContractError(
                        "ARTIFACT_CAS_HEAD_MISMATCH",
                        f"expected={expected_head_version_id} actual={current_head}",
                    )

            ts = _utc_now_iso()
            body_bytes = content_body.encode("utf-8")
            sha = hashlib.sha256(body_bytes).hexdigest()
            version_id = f"ver_{operation_id}"
            creator_receipt_id = f"rcpt_s01_{operation_id}"

            conn.execute(
                """
                INSERT INTO artifact_versions(
                    version_id, artifact_id, parent_version_id, content_body, sha256,
                    utf8_byte_length, origin_kind, action_id, creator_receipt_id,
                    writer_epoch, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTION_COMMIT', ?, ?, ?, ?)
                """,
                (
                    version_id,
                    artifact_id,
                    current_head,
                    content_body,
                    sha,
                    len(body_bytes),
                    action_id,
                    creator_receipt_id,
                    int(writer_epoch),
                    ts,
                ),
            )
            conn.execute(
                """
                UPDATE artifacts
                SET head_version_id=?, revision=revision+1
                WHERE artifact_id=?
                """,
                (version_id, artifact_id),
            )
            conn.execute(
                """
                INSERT INTO operation_mappings(
                    operation_id, action_id, artifact_id, version_id,
                    creator_receipt_id, sha256, committed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (operation_id, action_id, artifact_id, version_id, creator_receipt_id, sha, ts),
            )
            if projection_path is not None:
                conn.execute(
                    """
                    INSERT INTO artifact_projections(
                        artifact_id, file_path, known_projection_hash,
                        generated_version_id, health, updated_at
                    ) VALUES (?, ?, NULL, NULL, 'MISSING', ?)
                    ON CONFLICT(artifact_id) DO UPDATE SET
                        file_path=excluded.file_path,
                        health=CASE
                            WHEN artifact_projections.health='DIVERGED' THEN 'DIVERGED'
                            ELSE 'MISSING'
                        END,
                        updated_at=excluded.updated_at
                    """,
                    (artifact_id, str(projection_path), ts),
                )
            self._append_event(
                conn,
                event_id=f"evt_t2_{operation_id}",
                producer="S01",
                event_type="ArtifactVersionCommitted",
                aggregate_id=artifact_id,
                causation_refs=[action_id, operation_id],
                payload={
                    "artifact_id": artifact_id,
                    "version_id": version_id,
                    "operation_id": operation_id,
                    "creator_receipt_id": creator_receipt_id,
                    "sha256": sha,
                },
                writer_epoch=writer_epoch,
                outbox_dest="S10",
            )

            if trigger_cp09:
                self.crash_hook.hit(
                    "CP09",
                    {"artifact_id": artifact_id, "operation_id": operation_id, "committed": False},
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        if trigger_cp10:
            self.crash_hook.hit(
                "CP10",
                {"artifact_id": artifact_id, "version_id": version_id, "operation_id": operation_id},
            )
        if trigger_cp07:
            self.crash_hook.hit(
                "CP07",
                {"action_id": action_id, "operation_id": operation_id, "version_id": version_id},
            )

        return {
            "status": "COMMITTED",
            "artifact_id": artifact_id,
            "version_id": version_id,
            "creator_receipt_id": creator_receipt_id,
            "operation_id": operation_id,
            "sha256": sha,
        }

    def materialize_artifact_projection(
        self,
        *,
        artifact_id: str,
        file_path: Optional[Path] = None,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """Write .md projection outside T2; stops on DIVERGED unless broken_mode overwrites."""
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            art = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if art is None or not art["head_version_id"]:
                raise ReconcilerContractError("ARTIFACT_HEAD_MISSING", artifact_id)
            ver = conn.execute(
                "SELECT * FROM artifact_versions WHERE version_id=?", (art["head_version_id"],)
            ).fetchone()
            proj = conn.execute(
                "SELECT * FROM artifact_projections WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            target_path = Path(file_path or (proj["file_path"] if proj else self.root / f"{artifact_id}.md"))

            if target_path.exists() and proj is not None and proj["known_projection_hash"]:
                disk_sha = hashlib.sha256(target_path.read_bytes()).hexdigest()
                if disk_sha != proj["known_projection_hash"] and broken_mode != "overwrite_diverged":
                    ts = _utc_now_iso()
                    conn.execute(
                        """
                        UPDATE artifact_projections SET health='DIVERGED', updated_at=?
                        WHERE artifact_id=?
                        """,
                        (ts, artifact_id),
                    )
                    conn.execute("COMMIT")
                    return {
                        "status": "BLOCKED_DIVERGED",
                        "health": "DIVERGED",
                        "file_path": str(target_path),
                        "version_id": ver["version_id"],
                    }

            atomic_write_text(target_path, str(ver["content_body"]))
            ts = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO artifact_projections(
                    artifact_id, file_path, known_projection_hash,
                    generated_version_id, health, updated_at
                ) VALUES (?, ?, ?, ?, 'CURRENT', ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    file_path=excluded.file_path,
                    known_projection_hash=excluded.known_projection_hash,
                    generated_version_id=excluded.generated_version_id,
                    health='CURRENT',
                    updated_at=excluded.updated_at
                """,
                (artifact_id, str(target_path), ver["sha256"], ver["version_id"], ts),
            )
            conn.execute("COMMIT")
            return {
                "status": "MATERIALIZED",
                "health": "CURRENT",
                "file_path": str(target_path),
                "version_id": ver["version_id"],
            }
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Transaction Stage T3: S10 Result Admission
    # ------------------------------------------------------------------

    def tx_t3_admit_action_result(
        self,
        *,
        action_id: str,
        attempt_id: str,
        outcome: str,
        receipt_kind: str,
        receipt_id: str,
        actor_id: str = "S10",
        evidence: Optional[Mapping[str, Any]] = None,
        writer_epoch: int = 1,
        trigger_cp08: bool = False,
    ) -> dict[str, Any]:
        """T3 (S10): Admit authoritative outcome + receipt + ResultObserved outbox."""
        self._check_active_writer(writer_epoch)
        if receipt_kind not in RECEIPT_KINDS:
            raise ReconcilerContractError("INVALID_RECEIPT_KIND", receipt_kind)
        if receipt_kind == "HUMAN_ATTESTED_EXPERIENCE":
            raise ReconcilerContractError(
                "HUMAN_ATTESTATION_CANNOT_SETTLE_MACHINE_OUTCOME",
                "Use record_human_attestation() instead",
            )
        if outcome not in ACTION_OUTCOMES:
            raise ReconcilerContractError("INVALID_OUTCOME", outcome)

        phase = "CLOSED" if outcome in ("SUCCEEDED", "FAILED", "NOT_EXECUTED") else "RECONCILING"
        source_fact_id = f"sf_{action_id}_{receipt_id}"
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_rcpt = conn.execute(
                "SELECT * FROM action_receipts WHERE receipt_id=? AND action_id=?",
                (receipt_id, action_id),
            ).fetchone()
            if existing_rcpt is not None and str(existing_rcpt["claimed_outcome"]) == outcome:
                conn.execute("COMMIT")
                return {
                    "action_id": action_id,
                    "phase": phase,
                    "outcome": outcome,
                    "source_fact_id": source_fact_id,
                    "receipt_id": receipt_id,
                    "idempotent_replay": True,
                }
            ts = _utc_now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO action_receipts(
                    receipt_id, action_id, attempt_id, receipt_kind,
                    claimed_outcome, authoritative_for_machine_success,
                    allows_stop_investigation, actor_id, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
                """,
                (
                    receipt_id,
                    action_id,
                    attempt_id,
                    receipt_kind,
                    outcome,
                    actor_id,
                    json.dumps(dict(evidence or {}), ensure_ascii=False, sort_keys=True),
                    ts,
                ),
            )
            att_state = "COMPLETED" if outcome == "SUCCEEDED" else (
                "NOT_EXECUTED" if outcome == "NOT_EXECUTED" else (
                    "FAILED" if outcome == "FAILED" else "UNKNOWN"
                )
            )
            conn.execute(
                """
                UPDATE action_attempts
                SET state=?, result_outcome=?, updated_at=?
                WHERE attempt_id=?
                """,
                (att_state, outcome, ts, attempt_id),
            )
            conn.execute(
                """
                UPDATE actions
                SET phase=?, outcome=?, revision=revision+1, updated_at=?
                WHERE action_id=?
                """,
                (phase, outcome, ts, action_id),
            )
            base_evt_id = f"evt_t3_{action_id}"
            evt_exists = conn.execute(
                "SELECT 1 FROM event_journal WHERE event_id=?", (base_evt_id,)
            ).fetchone()
            t3_event_id = f"{base_evt_id}_{receipt_id}" if evt_exists is not None else base_evt_id
            self._append_event(
                conn,
                event_id=t3_event_id,
                producer="S10",
                event_type="ResultObserved",
                aggregate_id=action_id,
                causation_refs=[action_id, attempt_id, receipt_id],
                payload={
                    "action_id": action_id,
                    "attempt_id": attempt_id,
                    "source_fact_id": source_fact_id,
                    "outcome": outcome,
                    "receipt_id": receipt_id,
                },
                writer_epoch=writer_epoch,
                outbox_dest="TS",
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        if trigger_cp08:
            self.crash_hook.hit(
                "CP08",
                {"action_id": action_id, "source_fact_id": source_fact_id, "outcome": outcome},
            )
        return {
            "action_id": action_id,
            "phase": phase,
            "outcome": outcome,
            "source_fact_id": source_fact_id,
            "receipt_id": receipt_id,
        }

    # ------------------------------------------------------------------
    # Transaction Stages TS, T4, TC: Consequence Settlement
    # ------------------------------------------------------------------

    def tx_ts_create_settlement(
        self,
        *,
        action_id: str,
        source_fact_id: str,
        obligations: Optional[Sequence[tuple[str, str, str, int]]] = None,
        contract_version: str = "save_artifact.v1",
        writer_epoch: int = 1,
    ) -> dict[str, Any]:
        """TS: Freeze deterministic obligations & ConsequenceEnvelope by source_fact_id."""
        self._check_active_writer(writer_epoch)
        spec = list(
            obligations
            or [
                ("S01", "ARTIFACT_OUTCOME", f"art_{action_id}", 1),
                ("S09", "ACTIVITY_OUTCOME", f"actv_{action_id}", 1),
                ("S05", "MEMORY_ADMISSION", f"mem_{action_id}", 1),
            ]
        )
        envelope_id = f"env_{_sha256_hex(f'{source_fact_id}:{contract_version}')[:16]}"
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM consequence_envelopes WHERE source_fact_id=?", (source_fact_id,)
            ).fetchone()
            if existing is not None:
                ob_rows = conn.execute(
                    "SELECT * FROM consequence_obligations WHERE envelope_id=? ORDER BY obligation_id",
                    (existing["envelope_id"],),
                ).fetchall()
                conn.execute("COMMIT")
                return {
                    "status": "EXISTING_ENVELOPE",
                    "envelope_id": existing["envelope_id"],
                    "obligations": [dict(r) for r in ob_rows],
                }

            ts = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO consequence_envelopes(envelope_id, source_fact_id, action_id, contract_version, phase, created_at)
                VALUES (?, ?, ?, ?, 'OPEN', ?)
                """,
                (envelope_id, source_fact_id, action_id, contract_version, ts),
            )
            created_obs: list[dict[str, Any]] = []
            for owner, kind, target_ref, required in spec:
                ob_id = f"ob_{_sha256_hex(f'{envelope_id}:{owner}:{kind}:{target_ref}')[:16]}"
                conn.execute(
                    """
                    INSERT INTO consequence_obligations(
                        obligation_id, envelope_id, target_owner, kind, target_ref,
                        required, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
                    """,
                    (ob_id, envelope_id, owner, kind, target_ref, int(required), ts),
                )
                created_obs.append(
                    {
                        "obligation_id": ob_id,
                        "target_owner": owner,
                        "kind": kind,
                        "target_ref": target_ref,
                        "required": int(required),
                        "status": "PENDING",
                    }
                )
            self._append_event(
                conn,
                event_id=f"evt_ts_{envelope_id}",
                producer="TS",
                event_type="SettlementFrozen",
                aggregate_id=envelope_id,
                causation_refs=[source_fact_id, action_id],
                payload={"envelope_id": envelope_id, "obligations": created_obs},
                writer_epoch=writer_epoch,
                outbox_dest="owners",
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO inbox(consumer, event_id, status, result_ref, processed_at)
                VALUES ('TS_settlement', ?, 'PROCESSED', ?, ?)
                """,
                (f"evt_t3_{action_id}", envelope_id, ts),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        return {
            "status": "CREATED",
            "envelope_id": envelope_id,
            "obligations": created_obs,
        }

    def register_memory_source(self, *, source_id: str, generation: int = 1) -> None:
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO memory_sources(source_id, deleted, deletion_generation, updated_at)
                VALUES (?, 0, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    deletion_generation=excluded.deletion_generation,
                    updated_at=excluded.updated_at
                """,
                (source_id, int(generation), _utc_now_iso()),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def delete_memory_source(self, *, source_id: str) -> int:
        conn = self._connect_write()
        new_gen = 1
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT deletion_generation FROM memory_sources WHERE source_id=?", (source_id,)
            ).fetchone()
            new_gen = (int(row["deletion_generation"]) + 1) if row else 1
            conn.execute(
                """
                INSERT INTO memory_sources(source_id, deleted, deletion_generation, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    deleted=1,
                    deletion_generation=excluded.deletion_generation,
                    updated_at=excluded.updated_at
                """,
                (source_id, new_gen, _utc_now_iso()),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()
        self.control.mark_source_deleted(source_id, new_gen)
        return new_gen

    def tx_t4_commit_owner_effect(
        self,
        *,
        obligation_id: str,
        disposition: str = "APPLIED",
        reason_code: str = "OWNER_VERIFIED",
        memory_source_id: Optional[str] = None,
        expected_source_generation: Optional[int] = None,
        memory_content: str = "",
        writer_epoch: int = 1,
        trigger_cp12_before_commit: bool = False,
        trigger_cp12_after_commit: bool = False,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """T4: Single target owner local transaction (effect/NO_CHANGE + receipt + inbox + outbox)."""
        self._check_active_writer(writer_epoch)
        if disposition not in ("APPLIED", "NO_CHANGE"):
            raise ReconcilerContractError("INVALID_DISPOSITION", disposition)

        conn = self._connect_write()
        receipt_id = f"own_rcpt_{obligation_id}"
        try:
            conn.execute("BEGIN IMMEDIATE")
            ob = conn.execute(
                "SELECT * FROM consequence_obligations WHERE obligation_id=?", (obligation_id,)
            ).fetchone()
            if ob is None:
                raise ReconcilerContractError("OBLIGATION_NOT_FOUND", obligation_id)

            existing_rcpt = conn.execute(
                "SELECT * FROM owner_receipts WHERE obligation_id=?", (obligation_id,)
            ).fetchone()
            if existing_rcpt is not None and broken_mode != "duplicate_owner_handler":
                conn.execute("COMMIT")
                return {
                    "status": "IDEMPOTENT_OWNER_RECEIPT",
                    "obligation_id": obligation_id,
                    "receipt_id": existing_rcpt["receipt_id"],
                    "disposition": existing_rcpt["disposition"],
                }

            owner = str(ob["target_owner"])
            ts = _utc_now_iso()
            resulting_ref = str(ob["target_ref"])

            if owner == "S05" and memory_source_id is not None:
                src = conn.execute(
                    "SELECT * FROM memory_sources WHERE source_id=?", (memory_source_id,)
                ).fetchone()
                if src is None:
                    raise ReconcilerContractError("MEMORY_SOURCE_NOT_FOUND", memory_source_id)
                if broken_mode != "resurrect_deleted_source":
                    if int(src["deleted"]) == 1:
                        raise ReconcilerContractError("REJECTED_DELETED_SOURCE", memory_source_id)
                    if (
                        expected_source_generation is not None
                        and int(src["deletion_generation"]) != int(expected_source_generation)
                    ):
                        raise ReconcilerContractError(
                            "REJECTED_STALE_GENERATION",
                            f"expected={expected_source_generation} actual={src['deletion_generation']}",
                        )
                env = conn.execute(
                    "SELECT * FROM consequence_envelopes WHERE envelope_id=?", (ob["envelope_id"],)
                ).fetchone()
                mem_id = f"mem_{obligation_id}"
                adm_status = "COMMITTED_NO_CHANGE" if disposition == "NO_CHANGE" else "COMMITTED_RECORD"
                if broken_mode != "receipt_without_memory_record":
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO memory_records(
                            memory_id, source_id, source_fact_id, action_id,
                            deletion_generation, admission_status, content, receipt_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mem_id,
                            memory_source_id,
                            str(env["source_fact_id"]),
                            str(env["action_id"]),
                            int(src["deletion_generation"]),
                            adm_status,
                            memory_content,
                            receipt_id,
                            ts,
                        ),
                    )
                resulting_ref = mem_id

            if broken_mode == "duplicate_owner_handler":
                receipt_id = f"own_rcpt_{obligation_id}_{time.monotonic_ns()}"
            else:
                conn.execute(
                    """
                    INSERT INTO owner_receipts(
                        receipt_id, obligation_id, owner_domain, disposition,
                        reason_code, resulting_ref, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (receipt_id, obligation_id, owner, disposition, reason_code, resulting_ref, ts),
                )
            conn.execute(
                """
                UPDATE consequence_obligations
                SET status=?, owner_receipt_id=?, reason_code=?, updated_at=?
                WHERE obligation_id=?
                """,
                (disposition, receipt_id, reason_code, ts, obligation_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO inbox(consumer, event_id, status, result_ref, processed_at)
                VALUES (?, ?, 'PROCESSED', ?, ?)
                """,
                (f"owner_{owner}_{obligation_id}", f"evt_ts_{ob['envelope_id']}", receipt_id, ts),
            )
            self._append_event(
                conn,
                event_id=f"evt_t4_{receipt_id}",
                producer=owner,
                event_type="OwnerSettlementCommitted",
                aggregate_id=str(ob["envelope_id"]),
                causation_refs=[str(ob["envelope_id"]), obligation_id],
                payload={
                    "obligation_id": obligation_id,
                    "receipt_id": receipt_id,
                    "disposition": disposition,
                    "resulting_ref": resulting_ref,
                },
                writer_epoch=writer_epoch,
                outbox_dest="TC",
            )

            if trigger_cp12_before_commit:
                self.crash_hook.hit(
                    "CP12",
                    {"obligation_id": obligation_id, "phase": "before_commit"},
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        if trigger_cp12_after_commit:
            self.crash_hook.hit(
                "CP12",
                {"obligation_id": obligation_id, "phase": "after_commit", "receipt_id": receipt_id},
            )
        return {
            "status": "COMMITTED",
            "obligation_id": obligation_id,
            "receipt_id": receipt_id,
            "disposition": disposition,
            "resulting_ref": resulting_ref,
        }

    def tx_tc_close_settlement(
        self,
        *,
        envelope_id: str,
        writer_epoch: int = 1,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """TC: Verify owner_receipts for all required obligations and close envelope only when complete."""
        self._check_active_writer(writer_epoch)
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            env = conn.execute(
                "SELECT * FROM consequence_envelopes WHERE envelope_id=?", (envelope_id,)
            ).fetchone()
            if env is None:
                raise ReconcilerContractError("ENVELOPE_NOT_FOUND", envelope_id)
            if env["phase"] == "CLOSED":
                conn.execute("COMMIT")
                return {
                    "status": "CLOSED",
                    "envelope_id": envelope_id,
                    "unfinished_obligations": [],
                    "already_closed": True,
                }
            obs = conn.execute(
                "SELECT * FROM consequence_obligations WHERE envelope_id=?", (envelope_id,)
            ).fetchall()

            unfinished: list[str] = []
            for ob in obs:
                if int(ob["required"]) == 1:
                    if ob["status"] not in ("APPLIED", "NO_CHANGE") or not ob["owner_receipt_id"]:
                        unfinished.append(str(ob["obligation_id"]))
                    else:
                        rcpt = conn.execute(
                            "SELECT * FROM owner_receipts WHERE receipt_id=? AND obligation_id=?",
                            (ob["owner_receipt_id"], ob["obligation_id"]),
                        ).fetchone()
                        if rcpt is None:
                            unfinished.append(str(ob["obligation_id"]))

            if unfinished and broken_mode != "fake_close_envelope":
                conn.execute("COMMIT")
                return {
                    "status": "OPEN",
                    "envelope_id": envelope_id,
                    "unfinished_obligations": unfinished,
                }

            ts = _utc_now_iso()
            conn.execute(
                "UPDATE consequence_envelopes SET phase='CLOSED', closed_at=? WHERE envelope_id=?",
                (ts, envelope_id),
            )
            self._append_event(
                conn,
                event_id=f"evt_tc_{envelope_id}",
                producer="TC",
                event_type="ConsequenceEnvelopeClosed",
                aggregate_id=envelope_id,
                causation_refs=[envelope_id],
                payload={"envelope_id": envelope_id, "closed_at": ts},
                writer_epoch=writer_epoch,
            )
            conn.execute("COMMIT")
            return {
                "status": "CLOSED",
                "envelope_id": envelope_id,
                "unfinished_obligations": unfinished,
            }
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Compound Action Child DAG (PARTIAL result vector, never resend SUCCEEDED)
    # ------------------------------------------------------------------

    def create_compound_action(
        self,
        *,
        parent_logical_action_id: str,
        child_specs: Sequence[Mapping[str, Any]],
        subject_id: str = "subj_primary",
        origin_branch_id: str = "branch_main",
        effect_namespace: str = "compound.batch",
        provider_account: str = "local_batch",
        writer_epoch: int = 1,
    ) -> dict[str, Any]:
        """Create parent compound Action and child Action DAG nodes with independent idempotency keys."""
        self._check_active_writer(writer_epoch)
        parent_id = f"act_{parent_logical_action_id}"
        parent_key = compute_idempotency_key(
            effect_namespace=effect_namespace,
            provider_account=provider_account,
            subject_id=subject_id,
            origin_branch_id=origin_branch_id,
            logical_action_id=parent_logical_action_id,
        )
        parent_hash = compute_payload_hash({"children": [dict(c) for c in child_specs]})
        ts = _utc_now_iso()
        conn = self._connect_write()
        children_out: list[dict[str, Any]] = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO actions(
                    action_id, idempotency_key, payload_hash, logical_action_id,
                    subject_id, origin_branch_id, provider_account, effect_namespace,
                    is_compound, phase, outcome, operation_id,
                    writer_epoch, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'REQUESTED', 'PENDING', ?, ?, 1, ?, ?)
                """,
                (
                    parent_id,
                    parent_key,
                    parent_hash,
                    parent_logical_action_id,
                    subject_id,
                    origin_branch_id,
                    provider_account,
                    effect_namespace,
                    f"op_{parent_logical_action_id}",
                    int(writer_epoch),
                    ts,
                    ts,
                ),
            )
            for idx, spec in enumerate(child_specs):
                c_logical = str(spec["logical_action_id"])
                c_id = f"act_{c_logical}"
                c_key = compute_idempotency_key(
                    effect_namespace=str(spec.get("effect_namespace", effect_namespace)),
                    provider_account=str(spec.get("provider_account", provider_account)),
                    subject_id=subject_id,
                    origin_branch_id=origin_branch_id,
                    logical_action_id=c_logical,
                )
                c_payload = dict(spec.get("payload") or {"child_index": idx, "logical": c_logical})
                c_hash = compute_payload_hash(c_payload)
                conn.execute(
                    """
                    INSERT INTO actions(
                        action_id, idempotency_key, payload_hash, logical_action_id,
                        subject_id, origin_branch_id, provider_account, effect_namespace,
                        parent_action_id, child_index, is_compound,
                        phase, outcome, operation_id,
                        provider_supports_idempotency, conclusive_not_found,
                        writer_epoch, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'REQUESTED', 'PENDING', ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        c_id,
                        c_key,
                        c_hash,
                        c_logical,
                        subject_id,
                        origin_branch_id,
                        str(spec.get("provider_account", provider_account)),
                        str(spec.get("effect_namespace", effect_namespace)),
                        parent_id,
                        idx,
                        f"op_{c_logical}",
                        1 if spec.get("provider_supports_idempotency", False) else 0,
                        1 if spec.get("conclusive_not_found", True) else 0,
                        int(writer_epoch),
                        ts,
                        ts,
                    ),
                )
                children_out.append(
                    {
                        "action_id": c_id,
                        "logical_action_id": c_logical,
                        "idempotency_key": c_key,
                        "payload_hash": c_hash,
                        "child_index": idx,
                    }
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        return {
            "parent_action_id": parent_id,
            "parent_idempotency_key": parent_key,
            "children": children_out,
        }

    def check_idempotency_payload_binding(
        self,
        *,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> bool:
        """Reject same idempotency_key with mutated payload (REJECT_IDEMPOTENCY_CONFLICT)."""
        p_hash = compute_payload_hash(payload)
        conn = self._connect_readonly()
        if conn is None:
            return True
        try:
            row = conn.execute(
                "SELECT payload_hash FROM actions WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is not None and str(row["payload_hash"]) != p_hash:
                raise ReconcilerContractError(
                    "REJECT_IDEMPOTENCY_CONFLICT",
                    f"key={idempotency_key} payload mismatch",
                )
            return True
        finally:
            conn.close()

    def get_compound_action_view(self, parent_action_id: str) -> dict[str, Any]:
        """Project child result vector and PARTIAL status without erasing UNKNOWN."""
        conn = self._connect_readonly()
        assert conn is not None
        try:
            parent = conn.execute(
                "SELECT * FROM actions WHERE action_id=?", (parent_action_id,)
            ).fetchone()
            if parent is None:
                raise ReconcilerContractError("ACTION_NOT_FOUND", parent_action_id)
            children = conn.execute(
                "SELECT * FROM actions WHERE parent_action_id=? ORDER BY child_index",
                (parent_action_id,),
            ).fetchall()
        finally:
            conn.close()

        vector = [
            {
                "action_id": str(c["action_id"]),
                "child_index": int(c["child_index"]),
                "phase": str(c["phase"]),
                "outcome": str(c["outcome"]),
            }
            for c in children
        ]
        outcomes = [v["outcome"] for v in vector]
        if outcomes and all(o == "SUCCEEDED" for o in outcomes):
            compound_status = "ALL_SUCCEEDED"
        elif any(o == "SUCCEEDED" for o in outcomes) and any(o != "SUCCEEDED" for o in outcomes):
            compound_status = "PARTIAL"
        elif any(o == "UNKNOWN" for o in outcomes):
            compound_status = "UNKNOWN"
        else:
            compound_status = str(parent["outcome"])

        return {
            "parent_action_id": parent_action_id,
            "parent_outcome": str(parent["outcome"]),
            "compound_status": compound_status,
            "child_vector": vector,
        }

    def retry_or_reconcile_compound_action(
        self,
        *,
        parent_action_id: str,
        writer_epoch: int = 1,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """Reconcile compound children: NEVER resend already SUCCEEDED children."""
        self._check_active_writer(writer_epoch)
        view = self.get_compound_action_view(parent_action_id)
        skipped_succeeded: list[str] = []
        reconciled_children: list[dict[str, Any]] = []

        for child in view["child_vector"]:
            cid = child["action_id"]
            if child["outcome"] == "SUCCEEDED" and broken_mode != "naive_resend_all_children":
                skipped_succeeded.append(cid)
                continue
            if broken_mode == "naive_resend_all_children" and child["outcome"] == "SUCCEEDED":
                conn = self._connect_readonly()
                assert conn is not None
                row = conn.execute("SELECT * FROM actions WHERE action_id=?", (cid,)).fetchone()
                conn.close()
                self.provider.invoke(
                    logical_action_id=str(row["logical_action_id"]),
                    idempotency_key=str(row["idempotency_key"]),
                    attempt_id=f"broken_resend_{cid}",
                    payload_hash=str(row["payload_hash"]),
                    supports_idempotency=False,
                )
                continue
            res = self.reconcile_action(action_id=cid, writer_epoch=writer_epoch)
            reconciled_children.append(res)

        return {
            "parent_action_id": parent_action_id,
            "skipped_succeeded_children": skipped_succeeded,
            "reconciled_children": reconciled_children,
            "after_view": self.get_compound_action_view(parent_action_id),
        }

    # ------------------------------------------------------------------
    # Store Integrity + Fence Proof, Bounded Query Reconcile, Attestation Separation
    # ------------------------------------------------------------------

    def verify_store_integrity(self) -> tuple[bool, str]:
        """Verify SQLite store integrity and external control authority integrity flag."""
        ctrl = self.control.read()
        if not bool(ctrl.get("store_integrity_ok", True)):
            return False, "CONTROL_STORE_INTEGRITY_FAILED"
        if not self.db_path.is_file():
            return False, "STORE_MISSING"
        conn = None
        try:
            conn = self._connect_readonly()
            assert conn is not None
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if row is None or str(row[0]).lower() != "ok":
                return False, f"SQLITE_INTEGRITY_FAILED:{row[0] if row else 'none'}"
            tables = {
                str(r[0])
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            missing = [t for t in REQUIRED_SQLITE_TABLES if t not in tables]
            if missing:
                return False, f"MISSING_TABLES:{','.join(missing)}"
            return True, "OK"
        except sqlite3.DatabaseError as exc:
            return False, f"SQLITE_ERROR:{exc}"
        finally:
            if conn is not None:
                conn.close()

    def record_human_attestation(
        self,
        *,
        action_id: str,
        actor_id: str,
        statement: str,
        stop_investigation: bool = True,
    ) -> dict[str, Any]:
        """Record HUMAN_ATTESTED_EXPERIENCE; NEVER converts UNKNOWN to SUCCEEDED or PROVIDER_RECEIPT."""
        conn = self._connect_write()
        receipt_id = f"hatt_{action_id}_{_sha256_hex(f'{actor_id}:{statement}')[:12]}"
        try:
            conn.execute("BEGIN IMMEDIATE")
            act = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if act is None:
                raise ReconcilerContractError("ACTION_NOT_FOUND", action_id)
            ts = _utc_now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO action_receipts(
                    receipt_id, action_id, attempt_id, receipt_kind,
                    claimed_outcome, authoritative_for_machine_success,
                    allows_stop_investigation, actor_id, evidence_json, created_at
                ) VALUES (?, ?, NULL, 'HUMAN_ATTESTED_EXPERIENCE', 'ATTESTED_BY_HUMAN', 0, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    action_id,
                    1 if stop_investigation else 0,
                    actor_id,
                    json.dumps({"statement": statement}, ensure_ascii=False),
                    ts,
                ),
            )
            if stop_investigation and act["outcome"] == "UNKNOWN":
                conn.execute(
                    """
                    UPDATE actions
                    SET phase='QUIESCED_UNRESOLVED', revision=revision+1, updated_at=?
                    WHERE action_id=? AND outcome='UNKNOWN'
                    """,
                    (ts, action_id),
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

        return {
            "receipt_id": receipt_id,
            "receipt_kind": "HUMAN_ATTESTED_EXPERIENCE",
            "authoritative_for_machine_success": False,
            "action_id": action_id,
            "outcome_remains": "UNKNOWN",
        }

    def record_provider_receipt(
        self,
        *,
        action_id: str,
        attempt_id: str,
        provider_receipt_id: str,
        claimed_outcome: str = "SUCCEEDED",
        writer_epoch: int = 1,
    ) -> dict[str, Any]:
        """Record authoritative PROVIDER_RECEIPT and settle Action via T3."""
        return self.tx_t3_admit_action_result(
            action_id=action_id,
            attempt_id=attempt_id,
            outcome=claimed_outcome,
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id=provider_receipt_id,
            actor_id="external_provider",
            evidence={"provider_receipt_id": provider_receipt_id},
            writer_epoch=writer_epoch,
        )

    def block_activity_on_unknown_action(
        self,
        *,
        activity_id: str,
        action_id: str,
    ) -> None:
        """Block ONLY the activity/step depending on an UNKNOWN action."""
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE activities
                SET status='BLOCKED', blocked_on_action_id=?, unresolved_action_ref=?,
                    revision=revision+1, updated_at=?
                WHERE activity_id=?
                """,
                (action_id, action_id, _utc_now_iso(), activity_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def create_unrelated_activity(
        self,
        *,
        decision_id: str,
        request_id: str,
        title: str = "Unrelated independent activity",
        writer_epoch: int = 1,
    ) -> dict[str, Any]:
        """Create and start an unrelated activity to verify non-interference when another action is UNKNOWN."""
        self.tx_t0_commit_decision(
            decision_id=decision_id,
            request_id=request_id,
            writer_epoch=writer_epoch,
        )
        return self.tx_t1_commit_adoption_activity_action(
            decision_id=decision_id,
            request_id=request_id,
            logical_action_id=f"unrelated_{request_id}",
            activity_title=title,
            writer_epoch=writer_epoch,
        )

    def advance_unrelated_activity(self, *, activity_id: str) -> dict[str, Any]:
        """Verify an unrelated unblocked activity can progress to COMPLETED."""
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM activities WHERE activity_id=?", (activity_id,)
            ).fetchone()
            if row is None:
                raise ReconcilerContractError("ACTIVITY_NOT_FOUND", activity_id)
            if row["status"] == "BLOCKED":
                raise ReconcilerContractError("ACTIVITY_IS_BLOCKED", str(row["blocked_on_action_id"]))
            conn.execute(
                "UPDATE activities SET status='COMPLETED', revision=revision+1, updated_at=? WHERE activity_id=?",
                (_utc_now_iso(), activity_id),
            )
            conn.execute("COMMIT")
            return {"activity_id": activity_id, "status": "COMPLETED"}
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def reconcile_action(
        self,
        *,
        action_id: str,
        writer_epoch: int = 1,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """Bounded query reconciliation with strict fence + integrity gate for NOT_EXECUTED.

        Never coerces UNKNOWN to FAILED or SUCCEEDED; transitions to QUIESCED_UNRESOLVED
        when max_reconcile_queries is exhausted.
        """
        self._check_active_writer(writer_epoch)
        conn = self._connect_write()
        try:
            conn.execute("BEGIN IMMEDIATE")
            act = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if act is None:
                raise ReconcilerContractError("ACTION_NOT_FOUND", action_id)

            if act["outcome"] == "SUCCEEDED" and act["phase"] == "CLOSED":
                conn.execute("COMMIT")
                return {
                    "action_id": action_id,
                    "phase": "CLOSED",
                    "outcome": "SUCCEEDED",
                    "reconcile_query_count": int(act["reconcile_query_count"]),
                }

            # 1. Check S01 operation_mappings first (authoritative local owner mapping)
            op_map = conn.execute(
                "SELECT * FROM operation_mappings WHERE operation_id=?", (act["operation_id"],)
            ).fetchone()
            last_att = conn.execute(
                "SELECT * FROM action_attempts WHERE action_id=? ORDER BY attempt_seq DESC LIMIT 1",
                (action_id,),
            ).fetchone()

            if op_map is not None:
                conn.execute("COMMIT")
                conn = None
                return self.tx_t3_admit_action_result(
                    action_id=action_id,
                    attempt_id=str(last_att["attempt_id"]) if last_att else f"att_{action_id}_001",
                    outcome="SUCCEEDED",
                    receipt_kind="OWNER_OPERATION_RECEIPT",
                    receipt_id=str(op_map["creator_receipt_id"]),
                    actor_id="S01",
                    evidence={
                        "operation_id": str(op_map["operation_id"]),
                        "version_id": str(op_map["version_id"]),
                    },
                    writer_epoch=writer_epoch,
                )

            # Check bounded query budget before querying provider
            q_count = int(act["reconcile_query_count"])
            max_q = int(act["max_reconcile_queries"])
            if act["phase"] == "QUIESCED_UNRESOLVED" or q_count >= max_q:
                ts = _utc_now_iso()
                conn.execute(
                    """
                    UPDATE actions
                    SET phase='QUIESCED_UNRESOLVED', outcome='UNKNOWN', revision=revision+1, updated_at=?
                    WHERE action_id=?
                    """,
                    (ts, action_id),
                )
                conn.execute("COMMIT")
                return {
                    "action_id": action_id,
                    "phase": "QUIESCED_UNRESOLVED",
                    "outcome": "UNKNOWN",
                    "reconcile_query_count": q_count,
                    "auto_polling_stopped": True,
                }

            # Query external provider (read-only lookup, increments reconcile_query_count)
            q_count += 1
            prov_res = self.provider.query(idempotency_key=str(act["idempotency_key"]))
            if prov_res.get("found") and prov_res.get("provider_receipt_id"):
                conn.execute(
                    "UPDATE actions SET reconcile_query_count=?, updated_at=? WHERE action_id=?",
                    (q_count, _utc_now_iso(), action_id),
                )
                conn.execute("COMMIT")
                conn = None
                return self.tx_t3_admit_action_result(
                    action_id=action_id,
                    attempt_id=str(last_att["attempt_id"]) if last_att else f"att_{action_id}_001",
                    outcome="SUCCEEDED",
                    receipt_kind="PROVIDER_RECEIPT",
                    receipt_id=str(prov_res["provider_receipt_id"]),
                    actor_id="external_provider",
                    evidence={"invocation": prov_res.get("invocation")},
                    writer_epoch=writer_epoch,
                )

            # Not found: check if NOT_EXECUTED is provable.
            # Requires: (a) old writer fenced, (b) store integrity proof, AND
            # (c) either attempt is still PREPARED (never entered TA/IO) or conclusive_not_found.
            old_epoch = int(last_att["writer_epoch"]) if last_att else int(act["writer_epoch"])
            old_fenced = self.control.is_epoch_fenced(old_epoch)
            integrity_ok, integrity_reason = self.verify_store_integrity()
            prepared_only = last_att is not None and last_att["state"] == "PREPARED"
            conclusive_nf = bool(act["conclusive_not_found"]) and bool(
                prov_res.get("conclusive_not_found", False)
            )

            can_prove_not_executed = (
                old_fenced
                and integrity_ok
                and (prepared_only or (last_att is None) or conclusive_nf)
            )
            if broken_mode == "notfound_marks_failed":
                ts = _utc_now_iso()
                conn.execute(
                    "UPDATE actions SET phase='CLOSED', outcome='FAILED', reconcile_query_count=?, updated_at=? WHERE action_id=?",
                    (q_count, ts, action_id),
                )
                conn.execute("COMMIT")
                return {
                    "action_id": action_id,
                    "phase": "CLOSED",
                    "outcome": "FAILED",
                    "reconcile_query_count": q_count,
                }

            if can_prove_not_executed and broken_mode != "forbid_not_executed":
                conn.execute(
                    "UPDATE actions SET reconcile_query_count=?, updated_at=? WHERE action_id=?",
                    (q_count, _utc_now_iso(), action_id),
                )
                conn.execute("COMMIT")
                conn = None
                return self.tx_t3_admit_action_result(
                    action_id=action_id,
                    attempt_id=str(last_att["attempt_id"]) if last_att else f"att_{action_id}_001",
                    outcome="NOT_EXECUTED",
                    receipt_kind="OWNER_OPERATION_RECEIPT",
                    receipt_id=f"rcpt_notexec_{action_id}_{q_count}",
                    actor_id="S10",
                    evidence={
                        "old_epoch_fenced": old_fenced,
                        "store_integrity": integrity_reason,
                        "prepared_only": prepared_only,
                    },
                    writer_epoch=writer_epoch,
                )

            # Cannot prove NOT_EXECUTED -> keep UNKNOWN; quiesce if budget exhausted
            new_phase = "QUIESCED_UNRESOLVED" if q_count >= max_q else "RECONCILING"
            ts = _utc_now_iso()
            conn.execute(
                """
                UPDATE actions
                SET phase=?, outcome='UNKNOWN', reconcile_query_count=?,
                    revision=revision+1, updated_at=?
                WHERE action_id=?
                """,
                (new_phase, q_count, ts, action_id),
            )
            if last_att is not None and last_att["state"] == "STARTED":
                conn.execute(
                    "UPDATE action_attempts SET state='UNKNOWN', updated_at=? WHERE attempt_id=?",
                    (ts, last_att["attempt_id"]),
                )
            conn.execute("COMMIT")
            return {
                "action_id": action_id,
                "phase": new_phase,
                "outcome": "UNKNOWN",
                "reconcile_query_count": q_count,
                "old_epoch_fenced": old_fenced,
                "store_integrity": integrity_reason,
            }
        except BaseException:
            if conn is not None:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            if conn is not None:
                conn.close()

    # ------------------------------------------------------------------
    # Read-only Inspection & Recovery Helpers for CP01-CP12
    # ------------------------------------------------------------------

    def snapshot_db(self) -> dict[str, Any]:
        """Zero-write snapshot of all canonical tables for assertions."""
        conn = self._connect_readonly()
        if conn is None:
            return {}
        try:
            snap: dict[str, Any] = {}
            for tbl in REQUIRED_SQLITE_TABLES:
                rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
                snap[tbl] = [dict(r) for r in rows]
            return snap
        finally:
            conn.close()

    def recover_after_crash(
        self,
        *,
        crash_point: str,
        decision_id: str = "dec_1",
        request_id: str = "req_1",
        logical_action_id: str = "la_1",
        artifact_id: str = "art_1",
        content_body: str = "# Recovered Draft\n",
        projection_path: Optional[Path] = None,
        memory_source_id: str = "src_1",
        expected_source_generation: int = 1,
        broken_mode: str = "",
    ) -> dict[str, Any]:
        """Deterministic post-kill restart reconciliation for CP01-CP12."""
        if crash_point not in CRASH_POINTS:
            raise ReconcilerContractError("UNKNOWN_CRASH_POINT", crash_point)

        # Step 1: Allocate a new writer epoch and fence the crashed writer
        ctrl = self.control.allocate_new_epoch(fence_previous=True)
        epoch = int(ctrl["active_writer_epoch"])

        if crash_point == "CP01":
            # T0 Decision is in DB; S09 validates expiry & permission and runs T1 once
            return self.tx_t1_commit_adoption_activity_action(
                decision_id=decision_id,
                request_id=request_id,
                logical_action_id=logical_action_id,
                writer_epoch=epoch,
                broken_mode=broken_mode,
            )

        if crash_point == "CP02":
            # Request outbox present, T1 did not commit -> replay same request_id
            return self.tx_t1_commit_adoption_activity_action(
                decision_id=decision_id,
                request_id=request_id,
                logical_action_id=logical_action_id,
                writer_epoch=epoch,
                broken_mode=broken_mode,
            )

        if crash_point in ("CP03", "CP04"):
            # CP03: T1 full commit exists -> recover outbox dispatch without recreating Activity.
            # CP04: T1 rolled back -> replay T1 cleanly with original request_id.
            snap = self.snapshot_db()
            existing_acts = [a for a in snap.get("activities", [])]
            if existing_acts and broken_mode == "recreate_activity_on_missing_ack":
                return self.tx_t1_commit_adoption_activity_action(
                    decision_id=decision_id,
                    request_id=request_id,
                    logical_action_id=logical_action_id,
                    writer_epoch=epoch,
                    broken_mode="duplicate_adoption_new_id",
                )
            t1_res = self.tx_t1_commit_adoption_activity_action(
                decision_id=decision_id,
                request_id=request_id,
                logical_action_id=logical_action_id,
                writer_epoch=epoch,
            )
            action_id = str(t1_res["action_id"])
            existing_attempts = [
                a for a in snap.get("action_attempts", []) if a["action_id"] == action_id
            ]
            if existing_attempts:
                latest = existing_attempts[-1]
                td_res = {
                    "action_id": action_id,
                    "attempt_id": latest["attempt_id"],
                    "attempt_seq": latest["attempt_seq"],
                    "idempotent_replay": True,
                }
            else:
                td_res = self.tx_td_prepare_dispatch(
                    action_id=action_id,
                    run_id=f"run_{epoch}",
                    writer_epoch=epoch,
                )
            return {"t1": t1_res, "td": td_res, "writer_epoch": epoch}

        if crash_point == "CP05":
            # Outbox committed, consumer ack lost -> redeliver SAME event_id, inbox deduplicates
            snap = self.snapshot_db()
            pending_out = [o for o in snap.get("outbox", []) if o["state"] != "ACKED"]
            results = []
            for row in pending_out:
                results.append(
                    self.consume_outbox_to_inbox(
                        consumer=f"consumer_{row['destination']}",
                        event_id=str(row["event_id"]),
                        result_ref=str(row["event_id"]),
                        broken_mode=broken_mode,
                    )
                )
            return {"recovered_outbox": results, "writer_epoch": epoch}

        if crash_point == "CP06":
            action_id = f"act_{logical_action_id}"
            if broken_mode == "blind_resend_without_idempotency":
                snap = self.snapshot_db()
                act = [a for a in snap["actions"] if a["action_id"] == action_id][0]
                self.provider.invoke(
                    logical_action_id=str(act["logical_action_id"]),
                    idempotency_key=str(act["idempotency_key"]),
                    attempt_id=f"att_{action_id}_blind",
                    payload_hash=str(act["payload_hash"]),
                    supports_idempotency=False,
                )
                return {"action_id": action_id, "blind_resent": True}
            return self.reconcile_action(action_id=action_id, writer_epoch=epoch)

        if crash_point == "CP07":
            action_id = f"act_{logical_action_id}"
            if broken_mode == "hash_or_notfound_inference":
                # Broken: ignores operation_mappings and blindly writes FAILED on assumed not-found
                return self.reconcile_action(
                    action_id=action_id, writer_epoch=epoch, broken_mode="notfound_marks_failed"
                ) if not self.snapshot_db().get("operation_mappings") else {
                    "broken_hash_claim": True,
                    "claimed_via_hash_only": True,
                }
            return self.reconcile_action(action_id=action_id, writer_epoch=epoch)

        if crash_point == "CP08":
            action_id = f"act_{logical_action_id}"
            if broken_mode == "rerun_tool_on_missing_settlement":
                snap = self.snapshot_db()
                act = [a for a in snap["actions"] if a["action_id"] == action_id][0]
                self.provider.invoke(
                    logical_action_id=str(act["logical_action_id"]),
                    idempotency_key=str(act["idempotency_key"]),
                    attempt_id=f"att_{action_id}_rerun",
                    payload_hash=str(act["payload_hash"]),
                    supports_idempotency=False,
                )
                return {"broken_rerun": True}
            source_fact_id = f"sf_{action_id}_rcpt_s01_op_{logical_action_id}"
            snap = self.snapshot_db()
            rcpts = [r for r in snap["action_receipts"] if r["action_id"] == action_id]
            if rcpts:
                source_fact_id = f"sf_{action_id}_{rcpts[-1]['receipt_id']}"
            return self.tx_ts_create_settlement(
                action_id=action_id,
                source_fact_id=source_fact_id,
                writer_epoch=epoch,
            )

        if crash_point == "CP09":
            action_id = f"act_{logical_action_id}"
            if broken_mode == "file_exists_claims_version":
                if projection_path is not None and projection_path.exists():
                    return {"claimed_by_file_exists": True, "version_committed": False}
            return self.tx_t2_commit_artifact_version(
                artifact_id=artifact_id,
                action_id=action_id,
                content_body=content_body,
                projection_path=projection_path,
                writer_epoch=epoch,
            )

        if crash_point == "CP10":
            if broken_mode == "new_version_for_projection":
                # Broken: inserts a brand new version instead of projecting existing head
                snap = self.snapshot_db()
                art = [a for a in snap["artifacts"] if a["artifact_id"] == artifact_id][0]
                return self.tx_t2_commit_artifact_version(
                    artifact_id=artifact_id,
                    action_id=f"act_{logical_action_id}_dup",
                    content_body=content_body,
                    expected_head_version_id=art["head_version_id"],
                    projection_path=projection_path,
                    writer_epoch=epoch,
                )
            return self.materialize_artifact_projection(
                artifact_id=artifact_id,
                file_path=projection_path,
                broken_mode="overwrite_diverged" if broken_mode == "overwrite_diverged" else "",
            )

        if crash_point == "CP11":
            snap = self.snapshot_db()
            envs = snap.get("consequence_envelopes", [])
            if not envs:
                raise ReconcilerContractError("ENVELOPE_NOT_FOUND", "CP11")
            env_id = str(envs[0]["envelope_id"])
            obs = [o for o in snap["consequence_obligations"] if o["envelope_id"] == env_id]
            for ob in obs:
                if broken_mode == "fake_close_envelope":
                    break
                if broken_mode == "rerun_all_handlers":
                    self.tx_t4_commit_owner_effect(
                        obligation_id=str(ob["obligation_id"]),
                        writer_epoch=epoch,
                        broken_mode="duplicate_owner_handler",
                    )
                elif ob["status"] == "PENDING":
                    if ob["target_owner"] == "S05":
                        self.tx_t4_commit_owner_effect(
                            obligation_id=str(ob["obligation_id"]),
                            memory_source_id=memory_source_id,
                            expected_source_generation=expected_source_generation,
                            memory_content="verified settlement",
                            writer_epoch=epoch,
                        )
                    else:
                        self.tx_t4_commit_owner_effect(
                            obligation_id=str(ob["obligation_id"]),
                            writer_epoch=epoch,
                        )
            return self.tx_tc_close_settlement(
                envelope_id=env_id,
                writer_epoch=epoch,
                broken_mode=broken_mode,
            )

        # CP12
        snap = self.snapshot_db()
        s05_obs = [o for o in snap["consequence_obligations"] if o["target_owner"] == "S05"]
        if not s05_obs:
            raise ReconcilerContractError("S05_OBLIGATION_NOT_FOUND", "CP12")
        ob = s05_obs[0]
        if ob["status"] == "PENDING":
            self.tx_t4_commit_owner_effect(
                obligation_id=str(ob["obligation_id"]),
                memory_source_id=memory_source_id,
                expected_source_generation=expected_source_generation,
                memory_content="recovered memory admission",
                writer_epoch=epoch,
                broken_mode=broken_mode,
            )
        return self.tx_tc_close_settlement(
            envelope_id=str(ob["envelope_id"]),
            writer_epoch=epoch,
        )


# ----------------------------------------------------------------------
# Live Production Canonical Ledger Mirror & Zero-Write Summary
# ----------------------------------------------------------------------


def canonical_ledger_path(day_root: Path | str) -> Path:
    """Return ``<day_root>/v02/canonical_life.sqlite3`` purely without touching disk."""
    return Path(day_root) / "v02" / "canonical_life.sqlite3"


def _is_safe_ledger_chain(day_root: Path, db_path: Path) -> bool:
    """True when neither ``day_root``, ``v02``, nor the ledger file itself is a symlink."""
    try:
        if day_root.is_symlink():
            return False
        v02_dir = db_path.parent
        if v02_dir.is_symlink():
            return False
        if db_path.is_symlink():
            return False
        return True
    except OSError:
        return False


def _canonical_sqlite_ro_immutable_uri(db_path: Path) -> str:
    """Build cross-platform SQLite ``mode=ro&immutable=1`` URI (guarantees zero WAL/SHM creation)."""
    return f"{db_path.resolve().as_uri()}?mode=ro&immutable=1"


def read_canonical_ledger_summary_zero_write(day_root: Path | str) -> dict[str, Any]:
    """Strictly 100% zero-write summary of ``<day_root>/v02/canonical_life.sqlite3``.

    Never creates directories, files, or SQLite ``-wal``/``-shm`` aux files.
    """
    root = Path(day_root)
    db_path = canonical_ledger_path(root)
    fallback: dict[str, Any] = {
        "present": False,
        "ledger_mode": "file_fallback_pending_first_write",
        "journal_events": 0,
        "decisions": 0,
        "unresolved_attempts": 0,
        "last_settled_at": None,
        "llm_calls": 0,
    }
    if not _is_safe_ledger_chain(root, db_path):
        return fallback
    try:
        if not db_path.is_file():
            return fallback
    except OSError:
        return fallback

    conn: Optional[sqlite3.Connection] = None
    try:
        uri = _canonical_sqlite_ro_immutable_uri(db_path)
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row

        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        journal_events = 0
        if "event_journal" in tables:
            journal_events = int(
                conn.execute("SELECT COUNT(*) FROM event_journal").fetchone()[0]
            )

        decisions = 0
        if "decisions" in tables:
            decisions = int(
                conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            )

        unresolved_attempts = 0
        if "action_attempts" in tables:
            unresolved_attempts += int(
                conn.execute(
                    "SELECT COUNT(*) FROM action_attempts "
                    "WHERE state IN ('PREPARED','STARTED','PROVIDER_ACCEPTED','UNKNOWN')"
                ).fetchone()[0]
            )

        live_settlements = 0
        if "live_cycle_settlements" in tables:
            live_settlements = int(
                conn.execute("SELECT COUNT(*) FROM live_cycle_settlements").fetchone()[0]
            )
            unresolved_attempts += int(
                conn.execute(
                    "SELECT COUNT(*) FROM live_cycle_settlements "
                    "WHERE UPPER(outcome_status) IN ('UNKNOWN','PENDING','UNRESOLVED')"
                ).fetchone()[0]
            )

        ts_candidates: list[str] = []
        if "live_cycle_settlements" in tables:
            row = conn.execute(
                "SELECT MAX(settled_at) FROM live_cycle_settlements"
            ).fetchone()
            if row and row[0]:
                ts_candidates.append(str(row[0]))
        if "event_journal" in tables:
            row = conn.execute(
                "SELECT MAX(created_at) FROM event_journal"
            ).fetchone()
            if row and row[0]:
                ts_candidates.append(str(row[0]))
        if "consequence_envelopes" in tables:
            row = conn.execute(
                "SELECT MAX(closed_at) FROM consequence_envelopes WHERE closed_at IS NOT NULL"
            ).fetchone()
            if row and row[0]:
                ts_candidates.append(str(row[0]))

        last_settled_at = max(ts_candidates) if ts_candidates else None
        return {
            "present": True,
            "ledger_mode": "sqlite_canonical_live",
            "journal_events": journal_events,
            "decisions": decisions,
            "unresolved_attempts": unresolved_attempts,
            "live_settlements": live_settlements,
            "last_settled_at": last_settled_at,
            "llm_calls": 0,
        }
    except (sqlite3.Error, OSError):
        return {
            "present": True,
            "ledger_mode": "sqlite_unreadable",
            "corrupt": True,
            "journal_events": 0,
            "decisions": 0,
            "unresolved_attempts": 0,
            "last_settled_at": None,
            "llm_calls": 0,
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def record_live_cycle_settlement(
    day_root: Path | str,
    *,
    cycle_id: str,
    action_kind: str,
    outcome_status: str,
    target_ref: str = "",
    summary: str = "",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Idempotently mirror a verified live cycle settlement into ``canonical_life.sqlite3``.

    Schema-compatible with :class:`CrashAndPartialReconcilerStore` (reuses the exact
    ``event_journal`` DDL alongside ``live_cycle_settlements``) and always truncates WAL
    and switches ``journal_mode=DELETE`` before close so zero ``-wal``/``-shm`` files leak.
    """
    clean_cycle_id = str(cycle_id or "").strip()
    if not clean_cycle_id:
        raise ReconcilerContractError("CYCLE_ID_REQUIRED", "cycle_id must be non-empty")
    clean_action = str(action_kind or "").strip() or "CYCLE_SETTLEMENT"
    clean_outcome = str(outcome_status or "").strip().upper() or "APPLIED"
    clean_target = str(target_ref or "").strip()[:240]
    clean_summary = str(summary or "").strip()[:400]
    ts = str(now_iso).strip() if isinstance(now_iso, str) and now_iso.strip() else _utc_now_iso()

    root = Path(day_root)
    db_path = canonical_ledger_path(root)
    if not _is_safe_ledger_chain(root, db_path):
        raise ReconcilerContractError(
            "SYMLINK_PATH_FORBIDDEN",
            f"Refusing symlinked canonical ledger path: {db_path}",
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not _is_safe_ledger_chain(root, db_path):
        raise ReconcilerContractError(
            "SYMLINK_PATH_FORBIDDEN",
            f"Refusing symlinked canonical ledger path: {db_path}",
        )

    event_id = f"ev_live_cycle_{_sha256_hex(clean_cycle_id)[:24]}"
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA journal_mode=DELETE")

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_cycle_settlements (
                    cycle_id TEXT PRIMARY KEY,
                    action_kind TEXT NOT NULL,
                    outcome_status TEXT NOT NULL,
                    target_ref TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL UNIQUE,
                    settled_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_journal (
                    event_id TEXT PRIMARY KEY,
                    producer TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    aggregate_seq INTEGER NOT NULL,
                    causation_refs_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    writer_epoch INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(producer, aggregate_id, aggregate_seq)
                )
                """
            )

            existing = conn.execute(
                """
                SELECT cycle_id, action_kind, outcome_status, target_ref, summary, event_id, settled_at
                FROM live_cycle_settlements
                WHERE cycle_id = ?
                """,
                (clean_cycle_id,),
            ).fetchone()

            if existing is not None:
                conn.execute("COMMIT")
                result = {
                    "ok": True,
                    "recorded": False,
                    "idempotent_replay": True,
                    "cycle_id": str(existing["cycle_id"]),
                    "action_kind": str(existing["action_kind"]),
                    "outcome_status": str(existing["outcome_status"]),
                    "target_ref": str(existing["target_ref"]),
                    "event_id": str(existing["event_id"]),
                    "settled_at": str(existing["settled_at"]),
                    "ledger_path": str(db_path),
                    "llm_calls": 0,
                }
            else:
                conn.execute(
                    """
                    INSERT INTO live_cycle_settlements(
                        cycle_id, action_kind, outcome_status, target_ref, summary, event_id, settled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_cycle_id,
                        clean_action,
                        clean_outcome,
                        clean_target,
                        clean_summary,
                        event_id,
                        ts,
                    ),
                )
                producer = "v02_daily_loop_bridge"
                seq_row = conn.execute(
                    "SELECT COALESCE(MAX(aggregate_seq), 0) AS m FROM event_journal "
                    "WHERE producer = ? AND aggregate_id = ?",
                    (producer, clean_cycle_id),
                ).fetchone()
                next_seq = int(seq_row["m"]) + 1 if seq_row is not None else 1
                causation_refs = [clean_target] if clean_target else []
                payload_obj = {
                    "cycle_id": clean_cycle_id,
                    "action_kind": clean_action,
                    "outcome_status": clean_outcome,
                    "target_ref": clean_target,
                    "summary": clean_summary,
                }
                conn.execute(
                    """
                    INSERT OR IGNORE INTO event_journal(
                        event_id, producer, event_type, aggregate_id, aggregate_seq,
                        causation_refs_json, payload_json, writer_epoch, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        producer,
                        "LIVE_CYCLE_SETTLED",
                        clean_cycle_id,
                        next_seq,
                        json.dumps(causation_refs, ensure_ascii=False),
                        json.dumps(payload_obj, ensure_ascii=False, sort_keys=True),
                        1,
                        ts,
                    ),
                )
                conn.execute("COMMIT")
                result = {
                    "ok": True,
                    "recorded": True,
                    "idempotent_replay": False,
                    "cycle_id": clean_cycle_id,
                    "action_kind": clean_action,
                    "outcome_status": clean_outcome,
                    "target_ref": clean_target,
                    "event_id": event_id,
                    "settled_at": ts,
                    "ledger_path": str(db_path),
                    "llm_calls": 0,
                }
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        # Guarantee zero -wal/-shm aux files remain on disk after any live write.
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        conn.execute("PRAGMA journal_mode=DELETE")
        return result
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


# ----------------------------------------------------------------------
# Subprocess Worker Entrypoint for Real OS proc.kill() Testing
# ----------------------------------------------------------------------


def run_worker_for_crash_point(
    *,
    root: Path,
    crash_point: str,
    sentinel_path: Path,
    decision_id: str = "dec_1",
    request_id: str = "req_1",
    logical_action_id: str = "la_1",
    artifact_id: str = "art_1",
    projection_path: Optional[Path] = None,
    drop_ack: bool = False,
    supports_idempotency: bool = False,
    conclusive_not_found: bool = True,
    memory_source_id: str = "src_1",
    broken_mode: str = "",
) -> None:
    """Execute real pipeline up to the requested Crash Point CP01-CP12 and pause for proc.kill()."""
    hook = CrashHookController(target_cp=crash_point, sentinel_path=sentinel_path)
    store = CrashAndPartialReconcilerStore(root, crash_hook=hook)
    store.initialize()

    if crash_point == "CP01":
        store.tx_t0_commit_decision(
            decision_id=decision_id,
            request_id=request_id,
            permission_ok=True,
            expires_at="2099-01-01T00:00:00+08:00",
        )
        return

    store.tx_t0_commit_decision(decision_id=decision_id, request_id=request_id)

    if crash_point in ("CP02", "CP03", "CP04"):
        store.tx_t1_commit_adoption_activity_action(
            decision_id=decision_id,
            request_id=request_id,
            logical_action_id=logical_action_id,
            broken_mode=broken_mode,
        )
        return

    t1 = store.tx_t1_commit_adoption_activity_action(
        decision_id=decision_id,
        request_id=request_id,
        logical_action_id=logical_action_id,
        provider_supports_idempotency=supports_idempotency,
        conclusive_not_found=conclusive_not_found,
    )
    action_id = str(t1["action_id"])

    if crash_point == "CP05":
        # Commit TD + outbox event WITHOUT consuming/acking inbox beforehand so SIGKILL hits before ack.
        store.tx_td_prepare_dispatch(action_id=action_id, trigger_cp05=True)
        return

    td = store.tx_td_prepare_dispatch(
        action_id=action_id,
        trigger_cp_after_td=(crash_point == "CP_AFTER_TD"),
    )
    if crash_point == "CP_AFTER_TD":
        return
    attempt_id = str(td["attempt_id"])
    store.tx_ta_start_attempt(
        attempt_id=attempt_id,
        trigger_cp_after_ta=(crash_point == "CP_AFTER_TA"),
    )
    if crash_point == "CP_AFTER_TA":
        return

    if crash_point == "CP06":
        store.execute_external_attempt(
            action_id=action_id,
            attempt_id=attempt_id,
            drop_ack=drop_ack,
            trigger_cp06=True,
        )
        return

    if crash_point in ("CP07", "CP09", "CP10"):
        store.tx_t2_commit_artifact_version(
            artifact_id=artifact_id,
            action_id=action_id,
            content_body="# Canonical Markdown\nBody text.\n",
            projection_path=projection_path or (root / f"{artifact_id}.md"),
            trigger_cp07=(crash_point == "CP07"),
            trigger_cp09=(crash_point == "CP09"),
            trigger_cp10=(crash_point == "CP10"),
        )
        return

    t2 = store.tx_t2_commit_artifact_version(
        artifact_id=artifact_id,
        action_id=action_id,
        content_body="# Canonical Markdown\nBody text.\n",
        projection_path=projection_path or (root / f"{artifact_id}.md"),
    )
    t3 = store.tx_t3_admit_action_result(
        action_id=action_id,
        attempt_id=attempt_id,
        outcome="SUCCEEDED",
        receipt_kind="OWNER_OPERATION_RECEIPT",
        receipt_id=str(t2["creator_receipt_id"]),
        actor_id="S01",
        evidence={"version_id": t2["version_id"]},
        trigger_cp08=(crash_point == "CP08"),
    )
    if crash_point == "CP08":
        return

    ts = store.tx_ts_create_settlement(
        action_id=action_id,
        source_fact_id=str(t3["source_fact_id"]),
    )
    store.register_memory_source(source_id=memory_source_id, generation=1)
    obs = ts["obligations"]
    # Complete S01 and S09 first
    for ob in obs:
        if ob["target_owner"] in ("S01", "S09"):
            store.tx_t4_commit_owner_effect(obligation_id=str(ob["obligation_id"]))

    if crash_point == "CP11":
        hook.hit("CP11", {"envelope_id": ts["envelope_id"]})
        return

    s05_ob = [o for o in obs if o["target_owner"] == "S05"][0]
    if crash_point == "CP12":
        mode_flag = os.environ.get("NYAIRO_CP12_PHASE", "before_commit")
        store.tx_t4_commit_owner_effect(
            obligation_id=str(s05_ob["obligation_id"]),
            memory_source_id=memory_source_id,
            expected_source_generation=1,
            memory_content="saved draft memory",
            trigger_cp12_before_commit=(mode_flag == "before_commit"),
            trigger_cp12_after_commit=(mode_flag == "after_commit"),
        )
        return


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="v02 Crash & Partial Reconciler Worker")
    parser.add_argument(
        "--worker-cp",
        required=True,
        choices=CRASH_POINTS + ("CP_AFTER_TD", "CP_AFTER_TA"),
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--sentinel", required=True)
    parser.add_argument("--decision-id", default="dec_1")
    parser.add_argument("--request-id", default="req_1")
    parser.add_argument("--logical-action-id", default="la_1")
    parser.add_argument("--artifact-id", default="art_1")
    parser.add_argument("--projection-path", default="")
    parser.add_argument("--drop-ack", action="store_true")
    parser.add_argument("--supports-idempotency", action="store_true")
    parser.add_argument("--non-conclusive", action="store_true")
    parser.add_argument("--memory-source-id", default="src_1")
    parser.add_argument("--broken-mode", default="")
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    run_worker_for_crash_point(
        root=Path(args.root),
        crash_point=args.worker_cp,
        sentinel_path=Path(args.sentinel),
        decision_id=args.decision_id,
        request_id=args.request_id,
        logical_action_id=args.logical_action_id,
        artifact_id=args.artifact_id,
        projection_path=Path(args.projection_path) if args.projection_path else None,
        drop_ack=bool(args.drop_ack),
        supports_idempotency=bool(args.supports_idempotency),
        conclusive_not_found=not bool(args.non_conclusive),
        memory_source_id=args.memory_source_id,
        broken_mode=args.broken_mode,
    )
