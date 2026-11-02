#!/usr/bin/env python3
"""S03 Perception / Belief -- a durable, sourced current-claim aggregate.

Read this before wiring
-----------------------
This is the S03 owner the v2 design asks for (03 F-033..F-039; 02 S03; original
L12169-L12391 Q6/Q22/Q23/Q25/Q28/Q29).  It is deliberately *not* a second Memory
and *not* a confidence score:

* one aggregate, four physical tables -- ``proposition_slot``,
  ``belief_current``, ``belief_evidence_edge`` and ``belief_event``;
* the event table is also the outbox: every mutation is one deterministic event,
  written in the same SQLite transaction as the edge, the current row and the
  slot.  Reads never replay history; ``rebuild()`` does, to verify the reducer;
* an observation is evidence, not a belief.  A reported statement proves that a
  source said it, never that the statement is true.  An inference binds the
  evidence it depends on and is marked ``INFERRED`` -- it is not a new source;
* statuses are explicit and carry a reason: ``UNKNOWN`` / ``SUPPORTED`` /
  ``DISPUTED`` / ``STALE`` / ``SUPERSEDED``.  Two competing answers for one slot
  are shown side by side; there is no last-write-wins;
* ``valid_time`` (when the claim is true in the world), ``observed_at`` (when the
  observation happened) and ``recorded_at`` (when this system recorded it) are
  separate.  An unknown valid time is ``NULL`` -- the fetch instant is never used
  as the effective date;
* a source correction invalidates exactly that source's evidence edges and
  re-derives the claims that depended on them.  Old claims stay in history with
  their revision chain; a retraction withholds the old payload instead of
  rewriting history;
* reads never call a model, never fetch, never write World and never write
  Memory.  A claim is neither an action permission nor World truth.

Identity
--------
``slot`` is the question: ``(subject, predicate@version, scope, context,
temporal_kind)``.  ``claim`` is one candidate answer for that question with its
own id and ``[valid_from, valid_to)``.  Claim ids are opaque; the slot tuple is
normalized and unique.  Predicates are a small, versioned registry -- this is not
a general knowledge graph.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SCHEMA_VERSION = "chiyo.s03.claims.v1"
KIND = "s03_claim_aggregate"

STATUS_UNKNOWN = "UNKNOWN"
STATUS_SUPPORTED = "SUPPORTED"
STATUS_DISPUTED = "DISPUTED"
STATUS_STALE = "STALE"
STATUS_SUPERSEDED = "SUPERSEDED"
VALID_STATUSES = frozenset(
    {STATUS_UNKNOWN, STATUS_SUPPORTED, STATUS_DISPUTED, STATUS_STALE, STATUS_SUPERSEDED}
)

SOURCE_OBSERVED = "OBSERVED"
SOURCE_REPORTED = "REPORTED"
SOURCE_INFERRED = "INFERRED"
VALID_SOURCE_KINDS = frozenset({SOURCE_OBSERVED, SOURCE_REPORTED, SOURCE_INFERRED})

RELATION_SUPPORT = "SUPPORT"
RELATION_CONTRADICT = "CONTRADICT"
RELATION_BASIS = "BASIS"

REASON_NO_QUALIFYING_EVIDENCE = "NO_QUALIFYING_EVIDENCE"
REASON_EVIDENCE_RETRACTED = "EVIDENCE_RETRACTED"
REASON_SOURCE_RETRACTED = "SOURCE_RETRACTED"
REASON_REVALIDATION_DUE = "REVALIDATION_DUE"
REASON_VALID_TIME_ENDED = "VALID_TIME_ENDED"
REASON_SUPERSEDED = "SUPERSEDED"
REASON_BASIS_NOT_CURRENT = "BASIS_NOT_CURRENT"
REASON_NO_SLOT = "NO_SLOT"
REASON_CONFLICT = "COMPETING_ANSWERS"
REASON_INFERENCE_ONLY = "INFERENCE_ONLY_PREDICATE"

#: The event kinds this aggregate writes.  Every one is a state transition that
#: the deterministic reducer can replay without calling a model.
EVENT_CLAIM_ASSERTED = "CLAIM_ASSERTED"
EVENT_CLAIM_REFRESHED = "CLAIM_REFRESHED"
EVENT_CLAIM_SUPERSEDED = "CLAIM_SUPERSEDED"
EVENT_CLAIM_STALE = "CLAIM_STALE"
EVENT_CLAIM_INVALIDATED = "CLAIM_INVALIDATED"
EVENT_EDGE_INVALIDATED = "EDGE_INVALIDATED"
EVENT_SOURCE_CORRECTED = "SOURCE_CORRECTED"
EVENT_SOURCE_RETRACTED = "SOURCE_RETRACTED"
EVENT_INFERENCE_ASSERTED = "INFERENCE_ASSERTED"

#: The design's four physical tables plus their event outbox.  No other table.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS proposition_slot (
    slot_id           TEXT PRIMARY KEY,
    subject_id        TEXT NOT NULL,
    predicate_id      TEXT NOT NULL,
    predicate_version INTEGER NOT NULL,
    scope_id          TEXT NOT NULL,
    context_json      TEXT NOT NULL DEFAULT '',
    temporal_kind     TEXT NOT NULL,
    normalized_tuple  TEXT NOT NULL UNIQUE,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS belief_current (
    claim_id          TEXT PRIMARY KEY,
    slot_id           TEXT NOT NULL,
    object_json       TEXT,
    object_digest     TEXT,
    valid_from        TEXT,
    valid_to          TEXT,
    observed_at       TEXT,
    recorded_at       TEXT NOT NULL,
    last_checked_at   TEXT NOT NULL,
    revalidate_after_seconds INTEGER NOT NULL DEFAULT 86400,
    status            TEXT NOT NULL,
    status_reason     TEXT,
    revision          INTEGER NOT NULL,
    meaning_version   TEXT NOT NULL,
    superseded_by     TEXT,
    payload_withheld  INTEGER NOT NULL DEFAULT 0,
    source_kind       TEXT NOT NULL,
    source_ref        TEXT NOT NULL,
    source_revision   TEXT,
    source_generation TEXT,
    operation_key     TEXT,
    FOREIGN KEY (slot_id) REFERENCES proposition_slot(slot_id)
);

CREATE TABLE IF NOT EXISTS belief_evidence_edge (
    edge_id               TEXT PRIMARY KEY,
    claim_id              TEXT NOT NULL,
    evidence_ref          TEXT NOT NULL,
    evidence_revision     TEXT,
    evidence_generation   TEXT,
    eligibility_generation TEXT,
    relation              TEXT NOT NULL,
    source_kind           TEXT NOT NULL,
    source_id             TEXT NOT NULL,
    receipt_ref           TEXT,
    observed_at           TEXT,
    recorded_at           TEXT NOT NULL,
    invalidated_at        TEXT,
    invalidated_reason    TEXT,
    FOREIGN KEY (claim_id) REFERENCES belief_current(claim_id),
    UNIQUE (claim_id, evidence_ref, relation)
);

CREATE TABLE IF NOT EXISTS belief_event (
    event_id          TEXT PRIMARY KEY,
    aggregate_seq     INTEGER NOT NULL,
    claim_id          TEXT,
    slot_id           TEXT,
    kind              TEXT NOT NULL,
    old_revision      INTEGER,
    new_revision      INTEGER,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    payload_json      TEXT NOT NULL DEFAULT '{}',
    recorded_at       TEXT NOT NULL,
    operation_key     TEXT,
    published_at      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS belief_event_operation_key
    ON belief_event(operation_key) WHERE operation_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS belief_current_slot_idx ON belief_current(slot_id);
CREATE INDEX IF NOT EXISTS belief_edge_claim_idx ON belief_evidence_edge(claim_id);
CREATE INDEX IF NOT EXISTS belief_event_claim_idx ON belief_event(claim_id);
"""


class S03ClaimsError(RuntimeError):
    """The call cannot be served safely.  Never a fake success."""


class S03ClaimsUnavailable(S03ClaimsError):
    """No aggregate is bound at this path (or it is unreadable)."""


class S03ClaimsRefused(S03ClaimsError):
    """One named refusal before any side effect."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(("%s:%s" % (reason_code, detail))[:200])
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


# ---------------------------------------------------------------------------------------------
# predicate registry -- finite and versioned by design
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PredicateSpec:
    predicate_id: str
    version: int
    value_type: str
    cardinality: str
    requires_context: bool
    revalidate_after_seconds: int
    temporal_kind: str
    description: str
    inference_only: bool = False

    @property
    def ref(self) -> str:
        return "%s@%d" % (self.predicate_id, self.version)


#: Only predicates the current product actually uses.  Adding one is a design
#: decision, not a data-driven side effect: an unknown predicate is refused by
#: name and never merged into an existing slot.
PREDICATES: dict[str, PredicateSpec] = {
    "subject_last_observed_place": PredicateSpec(
        predicate_id="subject_last_observed_place",
        version=1,
        value_type="place_ref",
        cardinality="single",
        requires_context=False,
        revalidate_after_seconds=7 * 24 * 3600,
        temporal_kind="ongoing",
        description="the place where the subject was last actually observed",
    ),
    "object_last_observed_place": PredicateSpec(
        predicate_id="object_last_observed_place",
        version=1,
        value_type="place_ref",
        cardinality="single",
        requires_context=False,
        revalidate_after_seconds=7 * 24 * 3600,
        temporal_kind="ongoing",
        description="the place where one object was last actually observed",
    ),
    "place_closing_time": PredicateSpec(
        predicate_id="place_closing_time",
        version=1,
        value_type="time_of_day",
        cardinality="single",
        requires_context=True,
        revalidate_after_seconds=24 * 3600,
        temporal_kind="local_date",
        description="a place's closing time on one operating day, in its timezone",
    ),
    "place_likely_open": PredicateSpec(
        predicate_id="place_likely_open",
        version=1,
        value_type="bool",
        cardinality="single",
        requires_context=True,
        revalidate_after_seconds=7 * 24 * 3600,
        temporal_kind="local_date",
        description="an inference about whether a place is open, bound to a claim",
        inference_only=True,
    ),
    "place_sells_item": PredicateSpec(
        predicate_id="place_sells_item",
        version=1,
        value_type="item_ref",
        cardinality="multi",
        requires_context=False,
        revalidate_after_seconds=7 * 24 * 3600,
        temporal_kind="ongoing",
        description="one item a place is reported or observed to offer",
    ),
}

MAX_CONTEXT_CHARS = 400
MAX_VALUE_CHARS = 300
MAX_OPERATION_KEY = 200
MAX_SOURCE_REF = 220
MIN_REVALIDATE_SECONDS = 60
MAX_REVALIDATE_SECONDS = 90 * 24 * 3600


def predicate(predicate_id: str) -> PredicateSpec:
    spec = PREDICATES.get(str(predicate_id or ""))
    if spec is None:
        raise S03ClaimsRefused("S03_UNKNOWN_PREDICATE", str(predicate_id)[:80])
    return spec


# ---------------------------------------------------------------------------------------------
# small canonical helpers
# ---------------------------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _instant(value: Any, label: str) -> Optional[str]:
    """One ISO-8601 instant in UTC, or ``None`` when the caller left it unknown."""

    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise S03ClaimsRefused("S03_%s_INVALID" % label.upper(), text[:80]) from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Any) -> Optional[datetime]:
    text = _instant(value, "instant")
    return None if text is None else datetime.fromisoformat(text.replace("Z", "+00:00"))


def _structured_ref(value: Any, label: str, *, maximum: int = 180) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise S03ClaimsRefused("S03_%s_INVALID" % label.upper(), str(value)[:80])
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if not text[0].isalnum() or any(ch not in allowed for ch in text):
        raise S03ClaimsRefused("S03_%s_INVALID" % label.upper(), text[:80])
    if "/" in text or "\\" in text:
        raise S03ClaimsRefused("S03_%s_INVALID" % label.upper(), text[:80])
    return text


def _operation_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_OPERATION_KEY:
        raise S03ClaimsRefused("S03_OPERATION_KEY_INVALID", str(value)[:80])
    return text


def _context(value: Any, spec: PredicateSpec) -> str:
    if value in (None, {}, ""):
        if spec.requires_context:
            raise S03ClaimsRefused("S03_CONTEXT_REQUIRED", spec.predicate_id)
        return ""
    if not isinstance(value, Mapping):
        raise S03ClaimsRefused("S03_CONTEXT_NOT_MAPPING", type(value).__name__)
    context = {str(k): v for k, v in value.items()}
    if "timezone" in context and context.get("timezone") is not None:
        timezone_text = str(context["timezone"]).strip()
        if not timezone_text or len(timezone_text) > 64:
            raise S03ClaimsRefused("S03_CONTEXT_TIMEZONE_INVALID")
        context["timezone"] = timezone_text
    try:
        text = _canonical(context)
    except (TypeError, ValueError) as error:
        raise S03ClaimsRefused("S03_CONTEXT_NOT_JSON", str(error)[:80]) from error
    if len(text) > MAX_CONTEXT_CHARS:
        raise S03ClaimsRefused("S03_CONTEXT_TOO_LARGE", str(len(text)))
    return text


def _normalize_value(spec: PredicateSpec, value: Any) -> dict[str, Any]:
    """A typed object, or a named refusal.  Never a free-form blob."""

    if spec.value_type == "place_ref":
        if not isinstance(value, Mapping):
            raise S03ClaimsRefused("S03_VALUE_NOT_PLACE_REF")
        place_id = _structured_ref(value.get("place_id"), "place_id", maximum=120)
        out: dict[str, Any] = {"place_id": place_id}
        for key in ("place_name", "kind"):
            raw = value.get(key)
            if raw is not None:
                text = str(raw).strip()
                if not text or len(text) > 120:
                    raise S03ClaimsRefused("S03_VALUE_%s_INVALID" % key.upper())
                out[key] = text
        return out
    if spec.value_type == "time_of_day":
        if isinstance(value, Mapping):
            raw = value.get("time")
        else:
            raw = value
        text = str(raw or "").strip()
        if len(text) != 5 or text[2] != ":" or not text[:2].isdigit() or not text[3:].isdigit():
            raise S03ClaimsRefused("S03_VALUE_TIME_INVALID", text[:40])
        hour, minute = int(text[:2]), int(text[3:])
        if hour > 23 or minute > 59:
            raise S03ClaimsRefused("S03_VALUE_TIME_INVALID", text[:40])
        return {"time": "%02d:%02d" % (hour, minute)}
    if spec.value_type == "bool":
        raw = value.get("open") if isinstance(value, Mapping) else value
        if not isinstance(raw, bool):
            raise S03ClaimsRefused("S03_VALUE_BOOL_INVALID")
        return {"open": raw}
    if spec.value_type == "item_ref":
        if isinstance(value, Mapping):
            raw = value.get("item_ref")
        else:
            raw = value
        return {"item_ref": _structured_ref(raw, "item_ref", maximum=120)}
    raise S03ClaimsRefused("S03_PREDICATE_VALUE_TYPE_UNSUPPORTED", spec.value_type)


def _valid_window(valid_from: Any, valid_to: Any) -> tuple[Optional[str], Optional[str]]:
    start = _instant(valid_from, "valid_from")
    end = _instant(valid_to, "valid_to")
    if start and end and datetime.fromisoformat(end) < datetime.fromisoformat(start):
        raise S03ClaimsRefused("S03_VALID_WINDOW_INVERTED")
    return start, end


def _revalidate(spec: PredicateSpec, override: Any) -> int:
    if override is None:
        return spec.revalidate_after_seconds
    if isinstance(override, bool) or not isinstance(override, (int, float)):
        raise S03ClaimsRefused("S03_REVALIDATE_INVALID")
    seconds = int(override)
    if seconds < MIN_REVALIDATE_SECONDS or seconds > MAX_REVALIDATE_SECONDS:
        raise S03ClaimsRefused("S03_REVALIDATE_OUT_OF_RANGE")
    return seconds


def _overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    a_from = a.get("valid_from")
    a_to = a.get("valid_to")
    b_from = b.get("valid_from")
    b_to = b.get("valid_to")
    if a_to and b_from and a_to <= b_from:
        return False
    if b_to and a_from and b_to <= a_from:
        return False
    return True


# ---------------------------------------------------------------------------------------------
# the aggregate
# ---------------------------------------------------------------------------------------------


class S03ClaimService:
    """One S03 claim aggregate for one subject and one SQLite file."""

    def __init__(self, path: Path | str, *, subject: str, clock: Any = None,
                 create: bool = False) -> None:
        self.path = Path(str(path))
        self.subject = str(subject or "").strip()
        if not self.subject:
            raise S03ClaimsRefused("S03_SUBJECT_MISSING")
        self._clock = clock
        if not self.path.exists() and not create:
            raise S03ClaimsUnavailable("S03_CLAIMS_NOT_FOUND")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # ``executescript`` commits implicitly, so initialisation uses its own
            # connection rather than the BEGIN IMMEDIATE transaction helper.
            conn = sqlite3.connect(str(self.path), timeout=10.0)
            try:
                conn.executescript(SCHEMA_SQL)
                columns = {str(row[1]) for row in conn.execute(
                    "PRAGMA table_info(belief_current)").fetchall()}
                if "revalidate_after_seconds" not in columns:
                    conn.execute("ALTER TABLE belief_current ADD COLUMN "
                                 "revalidate_after_seconds INTEGER NOT NULL DEFAULT 86400")
                conn.commit()
            finally:
                conn.close()

    # -- binding ---------------------------------------------------------------------------

    @classmethod
    def initialize(cls, path: Path | str, *, subject: str) -> "S03ClaimService":
        """Explicit new-root initialisation (prepare).  Idempotent."""

        return cls(path, subject=subject, create=True)

    def _now(self, value: Any = None) -> str:
        if value is not None:
            return _instant(value, "now")  # type: ignore[return-value]
        if self._clock is not None:
            return _instant(self._clock(), "now")  # type: ignore[return-value]
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _connection(self, *, write: bool):
        service = self

        class _Ctx:
            def __enter__(self_):
                if not service.path.exists():
                    if write:
                        service.path.parent.mkdir(parents=True, exist_ok=True)
                    else:
                        raise S03ClaimsUnavailable("S03_CLAIMS_NOT_FOUND")
                conn = sqlite3.connect(str(service.path), timeout=10.0,
                                       isolation_level=None)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("PRAGMA foreign_keys=ON")
                if write:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("BEGIN IMMEDIATE")
                self_.conn = conn
                return conn

            def __exit__(self_, exc_type, exc, tb):
                try:
                    if write:
                        if exc_type is None:
                            self_.conn.execute("COMMIT")
                        else:
                            self_.conn.execute("ROLLBACK")
                finally:
                    self_.conn.close()
                return False

        return _Ctx()

    # -- idempotency -----------------------------------------------------------------------

    @staticmethod
    def _event_for_operation(conn: sqlite3.Connection, key: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM belief_event WHERE operation_key = ? ORDER BY aggregate_seq LIMIT 1",
            (key,)).fetchone()

    @staticmethod
    def _already(event: sqlite3.Row, operation_key: str) -> dict[str, Any]:
        return {"ok": True, "status": "ALREADY_APPLIED", "duplicate": True,
                "operation_key": operation_key, "event_kind": event["kind"]}

    def _next_seq(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(MAX(aggregate_seq), 0) + 1 FROM belief_event").fetchone()
        return int(row[0])

    def _append_event(self, conn: sqlite3.Connection, *, kind: str, claim_id: Optional[str],
                      slot_id: Optional[str], old_revision: Optional[int],
                      new_revision: Optional[int], evidence_refs: Sequence[str],
                      payload: Mapping[str, Any], now: str,
                      operation_key: Optional[str]) -> str:
        event_id = "evt:" + uuid.uuid4().hex[:20]
        conn.execute(
            """INSERT INTO belief_event
               (event_id, aggregate_seq, claim_id, slot_id, kind, old_revision, new_revision,
                evidence_refs_json, payload_json, recorded_at, operation_key, published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (event_id, self._next_seq(conn), claim_id, slot_id, kind, old_revision,
             new_revision, _canonical(list(evidence_refs)), _canonical(dict(payload)),
             now, operation_key))
        return event_id

    # -- slots and rows --------------------------------------------------------------------

    @staticmethod
    def _slot_tuple(subject: str, spec: PredicateSpec, scope_id: str, context_json: str) -> str:
        return "\x1f".join((subject, spec.ref, scope_id, context_json, spec.temporal_kind))

    @staticmethod
    def _slot_id(normalized: str) -> str:
        return "slot:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]

    def _ensure_slot(self, conn: sqlite3.Connection, *, spec: PredicateSpec, scope_id: str,
                     context_json: str, now: str) -> str:
        normalized = self._slot_tuple(self.subject, spec, scope_id, context_json)
        slot_id = self._slot_id(normalized)
        conn.execute(
            """INSERT OR IGNORE INTO proposition_slot
               (slot_id, subject_id, predicate_id, predicate_version, scope_id,
                context_json, temporal_kind, normalized_tuple, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slot_id, self.subject, spec.predicate_id, spec.version, scope_id,
             context_json, spec.temporal_kind, normalized, now))
        return slot_id

    @staticmethod
    def _claim_row(conn: sqlite3.Connection, claim_id: str) -> Optional[sqlite3.Row]:
        return conn.execute("SELECT * FROM belief_current WHERE claim_id = ?",
                            (claim_id,)).fetchone()

    @staticmethod
    def _edges(conn: sqlite3.Connection, claim_id: str, *,
               valid_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM belief_evidence_edge WHERE claim_id = ?"
        if valid_only:
            sql += " AND invalidated_at IS NULL"
        sql += " ORDER BY recorded_at, edge_id"
        return list(conn.execute(sql, (claim_id,)).fetchall())

    @staticmethod
    def _row_object(row: Mapping[str, Any]) -> Optional[Any]:
        if int(row["payload_withheld"] or 0):
            return None
        raw = row["object_json"]
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def _insert_claim(self, conn: sqlite3.Connection, *, slot_id: str, object_value: Mapping[str, Any],
                      valid_from: Optional[str], valid_to: Optional[str],
                      observed_at: Optional[str], now: str, status: str,
                      status_reason: Optional[str], meaning_version: str,
                      revalidate_after_seconds: int,
                      source_kind: str, source_ref: str, source_revision: Optional[str],
                      source_generation: Optional[str], operation_key: Optional[str],
                      superseded_by: Optional[str] = None,
                      payload_withheld: bool = False) -> str:
        claim_id = "claim:" + uuid.uuid4().hex[:20]
        conn.execute(
            """INSERT INTO belief_current
               (claim_id, slot_id, object_json, object_digest, valid_from, valid_to,
                observed_at, recorded_at, last_checked_at, revalidate_after_seconds,
                status, status_reason, revision, meaning_version, superseded_by,
                payload_withheld, source_kind, source_ref, source_revision,
                source_generation, operation_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (claim_id, slot_id, _canonical(dict(object_value)), _digest(dict(object_value)),
             valid_from, valid_to, observed_at, now, now, int(revalidate_after_seconds),
             status, status_reason, 1, meaning_version, superseded_by,
             1 if payload_withheld else 0, source_kind, source_ref, source_revision,
             source_generation, operation_key))
        return claim_id

    def _insert_edge(self, conn: sqlite3.Connection, *, claim_id: str, evidence_ref: str,
                     evidence_revision: Optional[str], evidence_generation: Optional[str],
                     relation: str, source_kind: str, source_id: str,
                     receipt_ref: Optional[str], observed_at: Optional[str], now: str,
                     eligibility_generation: Optional[str] = None) -> Optional[str]:
        edge_id = "edge:" + uuid.uuid4().hex[:20]
        try:
            conn.execute(
                """INSERT INTO belief_evidence_edge
                   (edge_id, claim_id, evidence_ref, evidence_revision, evidence_generation,
                    eligibility_generation, relation, source_kind, source_id, receipt_ref,
                    observed_at, recorded_at, invalidated_at, invalidated_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                (edge_id, claim_id, evidence_ref, evidence_revision, evidence_generation,
                 eligibility_generation, relation, source_kind, source_id, receipt_ref,
                 observed_at, now))
        except sqlite3.IntegrityError:
            # The same evidence supporting the same claim is one edge, not two.
            return None
        return edge_id

    # -- status projection -----------------------------------------------------------------

    def _claim_status(self, conn: sqlite3.Connection, row: sqlite3.Row, *,
                      now_dt: datetime, depth: int = 0) -> tuple[str, Optional[str]]:
        if depth > 3:
            return STATUS_UNKNOWN, REASON_BASIS_NOT_CURRENT
        if int(row["payload_withheld"] or 0):
            return STATUS_UNKNOWN, REASON_EVIDENCE_RETRACTED
        if str(row["status"]) == STATUS_SUPERSEDED:
            return STATUS_SUPERSEDED, row["status_reason"] or REASON_SUPERSEDED
        edges = self._edges(conn, str(row["claim_id"]), valid_only=True)
        if not edges:
            return STATUS_UNKNOWN, REASON_NO_QUALIFYING_EVIDENCE
        # An inference is only as current as its basis.
        for edge in edges:
            if str(edge["relation"]) != RELATION_BASIS:
                continue
            basis_id = str(edge["evidence_ref"])
            if basis_id.startswith("claim:"):
                basis_id = basis_id[len("claim:"):]
            basis = self._claim_row(conn, basis_id)
            if basis is None:
                return STATUS_UNKNOWN, REASON_BASIS_NOT_CURRENT
            basis_status, _ = self._claim_status(conn, basis, now_dt=now_dt,
                                                 depth=depth + 1)
            if basis_status in (STATUS_UNKNOWN, STATUS_SUPERSEDED):
                return STATUS_UNKNOWN, REASON_BASIS_NOT_CURRENT
            if basis_status == STATUS_STALE:
                return STATUS_STALE, REASON_BASIS_NOT_CURRENT
        valid_to = _parse(row["valid_to"])
        if valid_to is not None and now_dt >= valid_to:
            return STATUS_STALE, REASON_VALID_TIME_ENDED
        revalidate = self._claim_revalidate(row)
        last = _parse(row["last_checked_at"])
        if revalidate and last is not None and now_dt >= last + timedelta(seconds=revalidate):
            return STATUS_STALE, REASON_REVALIDATION_DUE
        return STATUS_SUPPORTED, None

    @staticmethod
    def _claim_revalidate(row: Mapping[str, Any]) -> int:
        # The deadline is stored on the claim: a source may require a tighter or looser
        # revalidation than the predicate's default, and time must not silently rewrite it.
        try:
            stored = int(row["revalidate_after_seconds"])
        except (KeyError, IndexError, TypeError, ValueError):
            stored = 0
        if stored > 0:
            return stored
        text = str(row["meaning_version"] or "")
        predicate_id = text.split("@", 1)[0]
        spec = PREDICATES.get(predicate_id)
        return 0 if spec is None else spec.revalidate_after_seconds

    def _slot_view(self, conn: sqlite3.Connection, slot: sqlite3.Row, *,
                   now_dt: datetime) -> dict[str, Any]:
        rows = list(conn.execute(
            "SELECT * FROM belief_current WHERE slot_id = ? ORDER BY recorded_at, claim_id",
            (str(slot["slot_id"]),)).fetchall())
        spec = PREDICATES.get(str(slot["predicate_id"]))
        claims: list[dict[str, Any]] = []
        statuses: list[str] = []
        for row in rows:
            status, reason = self._claim_status(conn, row, now_dt=now_dt)
            statuses.append(status)
            claims.append({
                "claim_id": str(row["claim_id"]),
                "status": status,
                "status_reason": reason,
                "revision": int(row["revision"]),
                "object": self._row_object(row),
                "valid_from": row["valid_from"],
                "valid_to": row["valid_to"],
                "observed_at": row["observed_at"],
                "recorded_at": row["recorded_at"],
                "last_checked_at": row["last_checked_at"],
                "source_kind": str(row["source_kind"]),
                "source_ref": str(row["source_ref"]),
                "source_revision": row["source_revision"],
                "source_generation": row["source_generation"],
                "superseded_by": row["superseded_by"],
                "withheld": bool(int(row["payload_withheld"] or 0)),
            })
        current = [row for row in claims if row["status"] in (STATUS_SUPPORTED, STATUS_DISPUTED)]
        distinct: dict[str, list[str]] = {}
        for row in current:
            if row["object"] is None:
                continue
            key = _canonical(row["object"])
            distinct.setdefault(key, []).append(row["claim_id"])
        conflict = False
        if spec is not None and spec.cardinality == "single" and len(current) > 1:
            # Different answers that could both hold at the same instant are a conflict.
            objects = [row for row in current if row["object"] is not None]
            for index, left in enumerate(objects):
                for right in objects[index + 1:]:
                    if (left["claim_id"] != right["claim_id"]
                            and _canonical(left["object"]) != _canonical(right["object"])
                            and _overlaps(left, right)):
                        conflict = True
        if conflict:
            for row in claims:
                if row["status"] == STATUS_SUPPORTED:
                    row["status"] = STATUS_DISPUTED
                    row["status_reason"] = REASON_CONFLICT
            status = STATUS_DISPUTED
        elif any(value == STATUS_SUPPORTED for value in statuses):
            status = STATUS_SUPPORTED
        elif any(value == STATUS_DISPUTED for value in statuses):
            status = STATUS_DISPUTED
        elif any(value == STATUS_STALE for value in statuses):
            status = STATUS_STALE
        elif any(value == STATUS_SUPERSEDED for value in statuses):
            status = STATUS_SUPERSEDED
        else:
            status = STATUS_UNKNOWN
        return {
            "slot_id": str(slot["slot_id"]),
            "predicate": str(slot["predicate_id"]),
            "predicate_version": int(slot["predicate_version"]),
            "scope_id": str(slot["scope_id"]),
            "context": json.loads(slot["context_json"]) if slot["context_json"] else {},
            "temporal_kind": str(slot["temporal_kind"]),
            "status": status,
            "status_reason": REASON_CONFLICT if conflict else (
                claims[0]["status_reason"] if len(claims) == 1 else None),
            "claim_ids": [row["claim_id"] for row in claims],
            "current_claim_ids": [row["claim_id"] for row in claims
                                  if row["status"] in (STATUS_SUPPORTED, STATUS_DISPUTED)],
            "distinct_answers": len(distinct),
            "claims": claims,
        }

    # -- assertions ------------------------------------------------------------------------

    def _assert_claim(self, conn: sqlite3.Connection, *, spec: PredicateSpec,
                      scope_id: str, context_json: str, object_value: Mapping[str, Any],
                      valid_from: Optional[str], valid_to: Optional[str],
                      observed_at: Optional[str], now: str, source_kind: str,
                      source_ref: str, source_revision: Optional[str],
                      source_generation: Optional[str], evidence_ref: str,
                      receipt_ref: Optional[str], operation_key: Optional[str],
                      revalidate_after_seconds: Optional[int] = None,
                      relation: str = RELATION_SUPPORT,
                      supersede_previous: bool = True) -> tuple[str, str]:
        """Assert one candidate answer.  Returns ``(claim_id, CREATED|UPDATED)``."""

        revalidate = (spec.revalidate_after_seconds if revalidate_after_seconds is None
                      else int(revalidate_after_seconds))

        slot_id = self._ensure_slot(conn, spec=spec, scope_id=scope_id,
                                    context_json=context_json, now=now)
        object_digest = _digest(dict(object_value))
        rows = list(conn.execute(
            "SELECT * FROM belief_current WHERE slot_id = ? ORDER BY recorded_at, claim_id",
            (slot_id,)).fetchall())
        for row in rows:
            if str(row["status"]) == STATUS_SUPERSEDED or int(row["payload_withheld"] or 0):
                continue
            if str(row["object_digest"]) != object_digest:
                continue
            if not _overlaps({"valid_from": valid_from, "valid_to": valid_to},
                             {"valid_from": row["valid_from"], "valid_to": row["valid_to"]}):
                continue
            if str(row["source_ref"]) == source_ref:
                # Same source, same answer: a refresh, not a second claim.  A newer
                # source revision is still evidence for the same answer.
                old_revision = int(row["revision"])
                cursor = conn.execute(
                    """UPDATE belief_current
                       SET last_checked_at = ?, observed_at = COALESCE(?, observed_at),
                           revision = revision + 1
                       WHERE claim_id = ? AND revision = ?""",
                    (now, observed_at, row["claim_id"], old_revision))
                if cursor.rowcount != 1:
                    raise S03ClaimsRefused("S03_CLAIM_REVISION_CONFLICT")
                edge_id = self._insert_edge(
                    conn, claim_id=str(row["claim_id"]), evidence_ref=evidence_ref,
                    evidence_revision=source_revision,
                    evidence_generation=source_generation, relation=relation,
                    source_kind=source_kind, source_id=source_ref,
                    receipt_ref=receipt_ref, observed_at=observed_at, now=now)
                self._append_event(
                    conn, kind=EVENT_CLAIM_REFRESHED, claim_id=str(row["claim_id"]),
                    slot_id=slot_id, old_revision=old_revision, new_revision=old_revision + 1,
                    evidence_refs=[evidence_ref],
                    payload={"claim_id": str(row["claim_id"]), "last_checked_at": now,
                             "observed_at": observed_at,
                             "edge": self._edge_payload(conn, edge_id)},
                    now=now, operation_key=operation_key)
                return str(row["claim_id"]), "UPDATED"

        # A single-value slot can hold one answer at a time: a different answer closes
        # the earlier interval instead of erasing it.
        to_supersede: list[sqlite3.Row] = []
        if supersede_previous and spec.cardinality == "single":
            for row in rows:
                if str(row["status"]) == STATUS_SUPERSEDED:
                    continue
                if not _overlaps({"valid_from": valid_from, "valid_to": valid_to},
                                 {"valid_from": row["valid_from"], "valid_to": row["valid_to"]}):
                    continue
                if str(row["object_digest"]) == object_digest:
                    continue
                to_supersede.append(row)

        claim_id = self._insert_claim(
            conn, slot_id=slot_id, object_value=object_value, valid_from=valid_from,
            valid_to=valid_to, observed_at=observed_at, now=now, status=STATUS_SUPPORTED,
            status_reason=None, meaning_version=spec.ref,
            revalidate_after_seconds=revalidate, source_kind=source_kind,
            source_ref=source_ref, source_revision=source_revision,
            source_generation=source_generation, operation_key=operation_key)
        edge_id = self._insert_edge(
            conn, claim_id=claim_id, evidence_ref=evidence_ref,
            evidence_revision=source_revision, evidence_generation=source_generation,
            relation=relation, source_kind=source_kind, source_id=source_ref,
            receipt_ref=receipt_ref, observed_at=observed_at, now=now)
        self._append_event(
            conn, kind=EVENT_CLAIM_ASSERTED, claim_id=claim_id, slot_id=slot_id,
            old_revision=None, new_revision=1, evidence_refs=[evidence_ref],
            payload={"claim": self._claim_payload(conn, claim_id),
                     "edge": self._edge_payload(conn, edge_id)},
            now=now, operation_key=operation_key)
        for row in to_supersede:
            self._supersede(conn, row, at=now, superseded_by=claim_id, now=now,
                            operation_key=None)
        return claim_id, "CREATED"

    @staticmethod
    def _edge_payload(conn: sqlite3.Connection, edge_id: Optional[str]) -> Optional[dict[str, Any]]:
        if edge_id is None:
            return None
        row = conn.execute("SELECT * FROM belief_evidence_edge WHERE edge_id = ?",
                           (edge_id,)).fetchone()
        return None if row is None else dict(row)

    def _claim_payload(self, conn: sqlite3.Connection, claim_id: str) -> dict[str, Any]:
        row = self._claim_row(conn, claim_id)
        if row is None:
            raise S03ClaimsRefused("S03_CLAIM_MISSING")
        return {
            "claim_id": str(row["claim_id"]), "slot_id": str(row["slot_id"]),
            "object_json": row["object_json"], "object_digest": row["object_digest"],
            "valid_from": row["valid_from"], "valid_to": row["valid_to"],
            "observed_at": row["observed_at"], "recorded_at": row["recorded_at"],
            "last_checked_at": row["last_checked_at"],
            "revalidate_after_seconds": int(row["revalidate_after_seconds"]),
            "status": str(row["status"]),
            "status_reason": row["status_reason"], "revision": int(row["revision"]),
            "meaning_version": str(row["meaning_version"]),
            "superseded_by": row["superseded_by"],
            "payload_withheld": int(row["payload_withheld"] or 0),
            "source_kind": str(row["source_kind"]), "source_ref": str(row["source_ref"]),
            "source_revision": row["source_revision"],
            "source_generation": row["source_generation"],
            "operation_key": row["operation_key"],
        }

    def _supersede(self, conn: sqlite3.Connection, row: sqlite3.Row, *, at: str,
                   superseded_by: Optional[str], now: str,
                   operation_key: Optional[str]) -> None:
        old_revision = int(row["revision"])
        new_revision = old_revision + 1
        conn.execute(
            """UPDATE belief_current
               SET status = ?, status_reason = ?, valid_to = COALESCE(?, valid_to),
                   superseded_by = ?, revision = ?
               WHERE claim_id = ? AND revision = ?""",
            (STATUS_SUPERSEDED, REASON_SUPERSEDED, at, superseded_by, new_revision,
             row["claim_id"], old_revision))
        self._append_event(
            conn, kind=EVENT_CLAIM_SUPERSEDED, claim_id=str(row["claim_id"]),
            slot_id=str(row["slot_id"]), old_revision=old_revision,
            new_revision=new_revision, evidence_refs=[],
            payload={"claim_id": str(row["claim_id"]), "superseded_by": superseded_by,
                     "valid_to": at}, now=now, operation_key=operation_key)

    # -- public writes ---------------------------------------------------------------------

    def observe_world(self, snapshot: Any, *, now: Any, operation_key: Any) -> dict[str, Any]:
        """Turn one formal World snapshot into observed evidence and claims.

        The snapshot is the facade's own envelope (``PhoneWorld.snapshot``).  This
        method accepts no free-form "I observed X": it reads the owner's blocks and
        records exactly what they contained, with the world id/revision as the
        evidence identity.  A hidden World value cannot be smuggled in here.
        """

        key = _operation_key(operation_key)
        now_iso = self._now(now)
        if not isinstance(snapshot, Mapping) or not snapshot.get("ok"):
            raise S03ClaimsRefused("S03_WORLD_OBSERVATION_UNAVAILABLE")
        data = snapshot.get("data") if isinstance(snapshot.get("data"), Mapping) else {}
        world = data.get("world") if isinstance(data.get("world"), Mapping) else {}
        location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
        if str(world.get("status")) != "available" or str(location.get("status")) != "available":
            raise S03ClaimsRefused("S03_WORLD_OBSERVATION_UNAVAILABLE")
        world_id = _structured_ref(world.get("world_id"), "world_id", maximum=120)
        revision = world.get("revision")
        if revision is None:
            raise S03ClaimsRefused("S03_WORLD_REVISION_MISSING")
        place_id = _structured_ref(location.get("place_id"), "place_id", maximum=120)
        place_name = str(location.get("name") or place_id)[:120]
        place_kind = str(location.get("kind") or "")[:80] or None
        local_date = str(world.get("local_date") or "")[:10] or None
        local_time = str(world.get("local_time") or "")[:19] or None
        receipt_ref = "worldobs:" + hashlib.sha256(
            _canonical({"world": world_id, "revision": revision, "place": place_id,
                        "area": location.get("area_id"), "local_date": local_date,
                        "local_time": local_time, "at": now_iso}).encode("utf-8")
        ).hexdigest()[:24]
        source_ref = "world:" + world_id
        source_revision = str(revision)
        place_value = {"place_id": place_id, "place_name": place_name}
        if place_kind:
            place_value["kind"] = place_kind
        claims: list[dict[str, Any]] = []
        with self._connection(write=True) as conn:
            existing = self._event_for_operation(conn, key + ":subject")
            if existing is not None:
                return self._already(existing, key + ":subject")
            spec = PREDICATES["subject_last_observed_place"]
            claim_id, outcome = self._assert_claim(
                conn, spec=spec, scope_id=self.subject, context_json="",
                object_value=place_value, valid_from=now_iso, valid_to=None,
                observed_at=now_iso, now=now_iso, source_kind=SOURCE_OBSERVED,
                source_ref=source_ref, source_revision=source_revision,
                source_generation="1",
                evidence_ref="%s@%s" % (source_ref, source_revision),
                receipt_ref=receipt_ref, operation_key=key + ":subject")
            claims.append({"predicate": spec.predicate_id, "scope_id": self.subject,
                           "claim_id": claim_id, "outcome": outcome,
                           "context": {"local_date": local_date} if local_date else {}})
            objects = data.get("objects") if isinstance(data.get("objects"), Mapping) else {}
            for row in (objects.get("here") or [])[:24]:
                if not isinstance(row, Mapping):
                    continue
                try:
                    object_id = _structured_ref(row.get("object_id"), "object_id", maximum=120)
                except S03ClaimsRefused:
                    continue
                sub_key = key + ":object:" + object_id
                if self._event_for_operation(conn, sub_key) is not None:
                    continue
                spec = PREDICATES["object_last_observed_place"]
                claim_id, outcome = self._assert_claim(
                    conn, spec=spec, scope_id=object_id, context_json="",
                    object_value={"place_id": place_id, "place_name": place_name},
                    valid_from=now_iso, valid_to=None, observed_at=now_iso,
                    now=now_iso, source_kind=SOURCE_OBSERVED, source_ref=source_ref,
                    source_revision=source_revision, source_generation="1",
                    evidence_ref="%s@%s#object:%s" % (source_ref, source_revision, object_id),
                    receipt_ref=receipt_ref, operation_key=sub_key)
                claims.append({"predicate": spec.predicate_id, "scope_id": object_id,
                               "claim_id": claim_id, "outcome": outcome, "context": {}})
        return {"ok": True, "status": "APPLIED", "operation_key": key,
                "observed_at": now_iso, "world_id": world_id,
                "world_revision": source_revision, "place_id": place_id,
                "receipt_ref": receipt_ref, "claims": claims,
                "source_kind": SOURCE_OBSERVED}

    def report(self, *, predicate_id: str, scope_id: str, value: Any,
               context: Any = None, source_ref: str, source_revision: Any = None,
               source_generation: Any = "1", valid_from: Any = None,
               valid_to: Any = None, observed_at: Any = None, now: Any,
               operation_key: Any, revalidate_after_seconds: Any = None) -> dict[str, Any]:
        """Register one *reported* answer, always labelled by its explicit source.

        A report proves that a source said this, never that it is true.  It cannot
        silently become an observation: the source must be an explicit operator /
        report channel, and the source kind stays ``REPORTED``.
        """

        key = _operation_key(operation_key)
        now_iso = self._now(now)
        source = str(source_ref or "").strip()
        if not (source.startswith("operator_report:") or source.startswith("report:")
                or source.startswith("reported:")):
            raise S03ClaimsRefused("S03_REPORT_SOURCE_NOT_EXPLICIT", source[:80])
        source = _structured_ref(source, "source_ref", maximum=MAX_SOURCE_REF)
        spec = predicate(predicate_id)
        if spec.inference_only:
            raise S03ClaimsRefused(REASON_INFERENCE_ONLY, spec.predicate_id)
        scope = _structured_ref(scope_id, "scope_id", maximum=120)
        context_json = _context(context, spec)
        object_value = _normalize_value(spec, value)
        start, end = _valid_window(valid_from, valid_to)
        observed = _instant(observed_at, "observed_at")
        revalidate = _revalidate(spec, revalidate_after_seconds)
        revision = None if source_revision is None else str(source_revision)[:80]
        generation = str(source_generation)[:80]
        with self._connection(write=True) as conn:
            existing = self._event_for_operation(conn, key)
            if existing is not None:
                return self._already(existing, key)
            slot_id = self._ensure_slot(conn, spec=spec, scope_id=scope,
                                        context_json=context_json, now=now_iso)
            # A source that changes its story must use an explicit correction; a second
            # report with a different value is not allowed to overwrite the first.
            for row in conn.execute(
                    """SELECT * FROM belief_current
                       WHERE slot_id = ? AND source_ref = ? AND status != ?""",
                    (slot_id, source, STATUS_SUPERSEDED)).fetchall():
                if str(row["object_digest"]) != _digest(object_value):
                    raise S03ClaimsRefused("S03_SOURCE_VALUE_CHANGE_REQUIRES_CORRECTION",
                                           source)
            claim_id, outcome = self._assert_claim(
                conn, spec=spec, scope_id=scope, context_json=context_json,
                object_value=object_value, valid_from=start, valid_to=end,
                observed_at=observed, now=now_iso, source_kind=SOURCE_REPORTED,
                source_ref=source, source_revision=revision,
                source_generation=generation, evidence_ref="%s#%s" % (
                    source, revision if revision is not None else generation),
                receipt_ref=None, operation_key=key,
                revalidate_after_seconds=revalidate,
                # Two sources may disagree: that is a conflict to show, not a
                # last-write-wins replacement.  Only a correction supersedes.
                supersede_previous=False)
        return {"ok": True, "status": outcome, "operation_key": key, "claim_id": claim_id,
                "slot_id": slot_id, "source_kind": SOURCE_REPORTED,
                "source_ref": source, "recorded_at": now_iso}

    def infer(self, *, predicate_id: str, scope_id: str, value: Any,
              basis_claim_ids: Sequence[str], context: Any = None,
              valid_from: Any = None, valid_to: Any = None, now: Any,
              operation_key: Any) -> dict[str, Any]:
        """Record an inference that *binds* existing claims; it is not a new source."""

        key = _operation_key(operation_key)
        now_iso = self._now(now)
        spec = predicate(predicate_id)
        if not spec.inference_only:
            raise S03ClaimsRefused("S03_NOT_INFERENCE_PREDICATE", spec.predicate_id)
        basis = [str(item) for item in (basis_claim_ids or []) if str(item).strip()]
        if not basis:
            raise S03ClaimsRefused("S03_INFERENCE_BASIS_MISSING")
        scope = _structured_ref(scope_id, "scope_id", maximum=120)
        context_json = _context(context, spec)
        object_value = _normalize_value(spec, value)
        start, end = _valid_window(valid_from, valid_to)
        with self._connection(write=True) as conn:
            existing = self._event_for_operation(conn, key)
            if existing is not None:
                return self._already(existing, key)
            now_dt = datetime.fromisoformat(now_iso)
            for claim_id in basis:
                row = self._claim_row(conn, claim_id)
                if row is None:
                    raise S03ClaimsRefused("S03_INFERENCE_BASIS_MISSING", claim_id)
                if int(row["payload_withheld"] or 0):
                    raise S03ClaimsRefused("S03_INFERENCE_BASIS_WITHHELD", claim_id)
                status, _ = self._claim_status(conn, row, now_dt=now_dt)
                if status not in (STATUS_SUPPORTED, STATUS_DISPUTED):
                    raise S03ClaimsRefused("S03_INFERENCE_BASIS_NOT_CURRENT", claim_id)
            slot_id = self._ensure_slot(conn, spec=spec, scope_id=scope,
                                        context_json=context_json, now=now_iso)
            source_ref = "inference:" + hashlib.sha256(
                _canonical({"basis": sorted(basis), "predicate": spec.ref,
                            "scope": scope}).encode("utf-8")).hexdigest()[:20]
            claim_id = self._insert_claim(
                conn, slot_id=slot_id, object_value=object_value, valid_from=start,
                valid_to=end, observed_at=now_iso, now=now_iso, status=STATUS_SUPPORTED,
                status_reason=None, meaning_version=spec.ref,
                revalidate_after_seconds=spec.revalidate_after_seconds,
                source_kind=SOURCE_INFERRED,
                source_ref=source_ref, source_revision=None, source_generation="1",
                operation_key=key)
            edges: list[dict[str, Any]] = []
            for claim_id_basis in basis:
                evidence_ref = "claim:" + claim_id_basis
                edge_id = self._insert_edge(
                    conn, claim_id=claim_id, evidence_ref=evidence_ref,
                    evidence_revision=None, evidence_generation="1",
                    relation=RELATION_BASIS, source_kind=SOURCE_INFERRED,
                    source_id=source_ref, receipt_ref=None, observed_at=now_iso, now=now_iso)
                payload = self._edge_payload(conn, edge_id)
                if payload is not None:
                    edges.append(payload)
            self._append_event(
                conn, kind=EVENT_INFERENCE_ASSERTED, claim_id=claim_id, slot_id=slot_id,
                old_revision=None, new_revision=1,
                evidence_refs=[edge["evidence_ref"] for edge in edges],
                payload={"claim": self._claim_payload(conn, claim_id), "edges": edges},
                now=now_iso, operation_key=key)
        return {"ok": True, "status": "CREATED", "operation_key": key,
                "claim_id": claim_id, "slot_id": slot_id, "source_kind": SOURCE_INFERRED,
                "basis_claim_ids": basis, "recorded_at": now_iso}

    def correct_source(self, *, source_ref: str, now: Any, operation_key: Any,
                       slot_id: Optional[str] = None,
                       replacement: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """One explicit source correction: invalidate that source, then re-derive."""

        key = _operation_key(operation_key)
        now_iso = self._now(now)
        source = _structured_ref(source_ref, "source_ref", maximum=MAX_SOURCE_REF)
        with self._connection(write=True) as conn:
            existing = self._event_for_operation(conn, key)
            if existing is not None:
                return self._already(existing, key)
            edges = list(conn.execute(
                """SELECT * FROM belief_evidence_edge
                   WHERE source_id = ? AND invalidated_at IS NULL
                   ORDER BY recorded_at, edge_id""", (source,)).fetchall())
            if not edges:
                raise S03ClaimsRefused("S03_SOURCE_NOT_FOUND", source)
            slots = sorted({str(edge_claim_slot(conn, edge)) for edge in edges})
            if slot_id is None and len(slots) > 1:
                raise S03ClaimsRefused("S03_CORRECTION_AMBIGUOUS", source)
            target_slot = slot_id or slots[0]
            affected = [edge for edge in edges
                        if str(edge_claim_slot(conn, edge)) == target_slot]
            if not affected:
                raise S03ClaimsRefused("S03_CORRECTION_SLOT_MISMATCH")
            claim_ids = sorted({str(edge["claim_id"]) for edge in affected})
            invalidated: list[str] = []
            for edge in affected:
                conn.execute(
                    """UPDATE belief_evidence_edge
                       SET invalidated_at = ?, invalidated_reason = ?
                       WHERE edge_id = ? AND invalidated_at IS NULL""",
                    (now_iso, EVENT_SOURCE_CORRECTED, edge["edge_id"]))
                invalidated.append(str(edge["edge_id"]))
                self._append_event(
                    conn, kind=EVENT_EDGE_INVALIDATED, claim_id=str(edge["claim_id"]),
                    slot_id=target_slot, old_revision=None, new_revision=None,
                    evidence_refs=[str(edge["evidence_ref"])],
                    payload={"edge_id": str(edge["edge_id"]),
                             "claim_id": str(edge["claim_id"]),
                             "reason": EVENT_SOURCE_CORRECTED},
                    now=now_iso, operation_key=None)
            replacement_claim_id: Optional[str] = None
            superseded: list[str] = []
            if replacement is not None:
                slot = conn.execute("SELECT * FROM proposition_slot WHERE slot_id = ?",
                                    (target_slot,)).fetchone()
                if slot is None:
                    raise S03ClaimsRefused("S03_SLOT_MISSING")
                spec = PREDICATES.get(str(slot["predicate_id"]))
                if spec is None:
                    raise S03ClaimsRefused("S03_UNKNOWN_PREDICATE")
                if spec.inference_only:
                    # An inferred claim is not a reported fact: correcting its "source"
                    # with a reported replacement would launder an inference into a report.
                    raise S03ClaimsRefused(REASON_INFERENCE_ONLY, spec.predicate_id)
                object_value = _normalize_value(spec, replacement.get("value"))
                start, end = _valid_window(replacement.get("valid_from"),
                                           replacement.get("valid_to"))
                observed = _instant(replacement.get("observed_at"), "observed_at")
                generation = str(int(max([int(edge["evidence_generation"] or "1")
                                          for edge in affected] or [1])) + 1)
                replacement_claim_id, _ = self._assert_claim(
                    conn, spec=spec, scope_id=str(slot["scope_id"]),
                    context_json=str(slot["context_json"]), object_value=object_value,
                    valid_from=start, valid_to=end, observed_at=observed, now=now_iso,
                    source_kind=SOURCE_REPORTED, source_ref=source,
                    source_revision=str(replacement.get("source_revision") or "") or None,
                    source_generation=generation,
                    evidence_ref="%s#correction:%s" % (source, generation),
                    receipt_ref=None, operation_key=key + ":replacement",
                    supersede_previous=False)
            for claim_id in claim_ids:
                row = self._claim_row(conn, claim_id)
                if row is None:
                    continue
                still_valid = [edge for edge in self._edges(conn, claim_id, valid_only=True)
                               if str(edge["relation"]) != RELATION_BASIS]
                if still_valid:
                    # Another independent source still supports it: do not erase it.
                    continue
                if replacement_claim_id is not None:
                    self._supersede(conn, row, at=now_iso,
                                    superseded_by=replacement_claim_id, now=now_iso,
                                    operation_key=None)
                    superseded.append(claim_id)
                else:
                    old_revision = int(row["revision"])
                    conn.execute(
                        """UPDATE belief_current
                           SET status = ?, status_reason = ?, payload_withheld = 1,
                               revision = revision + 1
                           WHERE claim_id = ? AND revision = ?""",
                        (STATUS_UNKNOWN, REASON_EVIDENCE_RETRACTED, claim_id, old_revision))
                    self._append_event(
                        conn, kind=EVENT_CLAIM_INVALIDATED, claim_id=claim_id,
                        slot_id=target_slot, old_revision=old_revision,
                        new_revision=old_revision + 1, evidence_refs=[],
                        payload={"claim_id": claim_id, "reason": REASON_EVIDENCE_RETRACTED,
                                 "withheld": True}, now=now_iso, operation_key=None)
            self._append_event(
                conn, kind=(EVENT_SOURCE_CORRECTED if replacement_claim_id is not None
                            else EVENT_SOURCE_RETRACTED),
                claim_id=None, slot_id=target_slot, old_revision=None, new_revision=None,
                evidence_refs=invalidated,
                payload={"source_ref": source, "slot_id": target_slot,
                         "invalidated_edges": invalidated,
                         "replacement_claim_id": replacement_claim_id,
                         "superseded_claims": superseded},
                now=now_iso, operation_key=key)
        return {"ok": True,
                "status": "CORRECTED" if replacement_claim_id is not None else "RETRACTED",
                "operation_key": key, "source_ref": source, "slot_id": target_slot,
                "invalidated_edges": len(invalidated),
                "superseded_claims": superseded,
                "replacement_claim_id": replacement_claim_id,
                "recorded_at": now_iso}

    def retract_source(self, *, source_ref: str, now: Any, operation_key: Any,
                       slot_id: Optional[str] = None) -> dict[str, Any]:
        return self.correct_source(source_ref=source_ref, now=now,
                                   operation_key=operation_key, slot_id=slot_id,
                                   replacement=None)

    def maintenance(self, *, now: Any) -> dict[str, Any]:
        """Explicit time advance: persist the STALE transition, never reset a deadline."""

        now_iso = self._now(now)
        now_dt = datetime.fromisoformat(now_iso)
        marked: list[str] = []
        with self._connection(write=True) as conn:
            for _pass in range(4):
                changed = False
                for row in conn.execute(
                        "SELECT * FROM belief_current WHERE status IN (?, ?)",
                        (STATUS_SUPPORTED, STATUS_DISPUTED)).fetchall():
                    status, reason = self._claim_status(conn, row, now_dt=now_dt)
                    if status != STATUS_STALE:
                        continue
                    old_revision = int(row["revision"])
                    conn.execute(
                        """UPDATE belief_current
                           SET status = ?, status_reason = ?, revision = revision + 1
                           WHERE claim_id = ? AND revision = ?""",
                        (STATUS_STALE, reason, row["claim_id"], old_revision))
                    self._append_event(
                        conn, kind=EVENT_CLAIM_STALE, claim_id=str(row["claim_id"]),
                        slot_id=str(row["slot_id"]), old_revision=old_revision,
                        new_revision=old_revision + 1, evidence_refs=[],
                        payload={"claim_id": str(row["claim_id"]), "reason": reason,
                                 "at": now_iso}, now=now_iso, operation_key=None)
                    marked.append(str(row["claim_id"]))
                    changed = True
                if not changed:
                    break
        return {"ok": True, "status": "MAINTAINED", "observed_at": now_iso,
                "marked_stale": marked, "count": len(marked)}

    # -- public reads ----------------------------------------------------------------------

    def current_view(self, *, now: Any = None, limit: int = 12,
                     slot_ids: Optional[Sequence[str]] = None) -> dict[str, Any]:
        """Bounded current claims; a pure read that never writes and never fetches."""

        now_iso = self._now(now)
        now_dt = datetime.fromisoformat(now_iso)
        with self._connection(write=False) as conn:
            if slot_ids:
                slots = [row for slot_id in slot_ids
                         for row in [conn.execute(
                             "SELECT * FROM proposition_slot WHERE slot_id = ?", (slot_id,)
                         ).fetchone()] if row is not None]
            else:
                slots = list(conn.execute(
                    "SELECT * FROM proposition_slot ORDER BY created_at, slot_id").fetchall())
            views = [self._slot_view(conn, slot, now_dt=now_dt) for slot in slots]
        rank = {STATUS_DISPUTED: 0, STATUS_STALE: 1, STATUS_SUPPORTED: 2,
                STATUS_UNKNOWN: 3, STATUS_SUPERSEDED: 4}
        views.sort(key=lambda view: (rank.get(view["status"], 9), -len(view["claims"])))
        max_claims = max(1, min(int(limit), 50))
        claims: list[dict[str, Any]] = []
        for view in views:
            for row in view["claims"]:
                if row["status"] == STATUS_SUPERSEDED:
                    continue
                claims.append({**row, "predicate": view["predicate"],
                               "predicate_version": view["predicate_version"],
                               "scope_id": view["scope_id"], "context": view["context"],
                               "slot_status": view["status"]})
        claims = claims[:max_claims]
        counts = {status.lower(): 0 for status in sorted(VALID_STATUSES)}
        source_counts = {kind.lower(): 0 for kind in sorted(VALID_SOURCE_KINDS)}
        for row in claims:
            counts[str(row["status"]).lower()] += 1
            source_counts[str(row["source_kind"]).lower()] += 1
        conflicts = [{"slot_id": view["slot_id"], "predicate": view["predicate"],
                      "scope_id": view["scope_id"], "context": view["context"],
                      "claim_ids": view["current_claim_ids"],
                      "distinct_answers": view["distinct_answers"]}
                     for view in views if view["status"] == STATUS_DISPUTED]
        stale = [{"slot_id": view["slot_id"], "predicate": view["predicate"],
                  "scope_id": view["scope_id"], "context": view["context"],
                  "claim_ids": view["claim_ids"]}
                 for view in views if view["status"] == STATUS_STALE]
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "subject_id": self.subject,
            "observed_at": now_iso,
            "claims": claims,
            "slots": [{key: view[key] for key in
                       ("slot_id", "predicate", "predicate_version", "scope_id", "context",
                        "temporal_kind", "status", "status_reason", "claim_ids",
                        "current_claim_ids", "distinct_answers")}
                      for view in views][:max_claims],
            "conflicts": conflicts,
            "stale": stale,
            "counts": {"claims": len(claims), "slots": len(views),
                       "by_status": counts, "by_source_kind": source_counts},
            "authority": "S03_CURRENT_CLAIMS_ONLY",
            "actions_authorized": 0,
            "world_updates": 0,
            "memory_writes": 0,
            "read_only": True,
            "writes_performed": 0,
        }

    def slot_status(self, *, predicate_id: str, scope_id: str, context: Any = None,
                    now: Any = None) -> dict[str, Any]:
        """One normalized slot.  No slot / no evidence is UNKNOWN, never False."""

        spec = predicate(predicate_id)
        scope = _structured_ref(scope_id, "scope_id", maximum=120)
        context_json = _context(context, spec)
        normalized = self._slot_tuple(self.subject, spec, scope, context_json)
        slot_id = self._slot_id(normalized)
        now_iso = self._now(now)
        with self._connection(write=False) as conn:
            slot = conn.execute("SELECT * FROM proposition_slot WHERE slot_id = ?",
                                (slot_id,)).fetchone()
            if slot is None:
                return {"ok": True, "status": STATUS_UNKNOWN, "reason_code": REASON_NO_SLOT,
                        "slot_id": slot_id, "predicate": spec.predicate_id,
                        "scope_id": scope, "context": json.loads(context_json) if context_json else {},
                        "claims": [], "actions_authorized": 0, "read_only": True}
            view = self._slot_view(conn, slot, now_dt=datetime.fromisoformat(now_iso))
        return {"ok": True, "status": view["status"], "reason_code": view["status_reason"],
                "observed_at": now_iso, **view,
                "actions_authorized": 0, "read_only": True}

    def slot_view(self, *, slot_id: str, now: Any = None) -> dict[str, Any]:
        """One slot by its opaque id, with claim statuses computed at ``now``."""

        slot_key = _structured_ref(slot_id, "slot_id", maximum=120)
        now_iso = self._now(now)
        with self._connection(write=False) as conn:
            slot = conn.execute("SELECT * FROM proposition_slot WHERE slot_id = ?",
                                (slot_key,)).fetchone()
            if slot is None:
                return {"ok": True, "status": STATUS_UNKNOWN, "reason_code": REASON_NO_SLOT,
                        "slot_id": slot_key, "claims": [], "actions_authorized": 0,
                        "read_only": True}
            view = self._slot_view(conn, slot, now_dt=datetime.fromisoformat(now_iso))
        return {"ok": True, "observed_at": now_iso, **view,
                "actions_authorized": 0, "read_only": True}

    def history(self, *, slot_id: str, limit: int = 20,
                now: Any = None) -> dict[str, Any]:
        """One slot's revision chain.  Retracted payloads stay withheld."""

        slot_key = _structured_ref(slot_id, "slot_id", maximum=120)
        now_iso = self._now(now)
        with self._connection(write=False) as conn:
            slot = conn.execute("SELECT * FROM proposition_slot WHERE slot_id = ?",
                                (slot_key,)).fetchone()
            if slot is None:
                raise S03ClaimsRefused("S03_SLOT_NOT_FOUND", slot_key)
            rows = list(conn.execute(
                "SELECT * FROM belief_current WHERE slot_id = ? ORDER BY recorded_at, claim_id",
                (slot_key,)).fetchall())
            claims = []
            for row in rows[:max(1, min(int(limit), 50))]:
                events = list(conn.execute(
                    """SELECT kind, old_revision, new_revision, recorded_at
                       FROM belief_event WHERE claim_id = ? ORDER BY aggregate_seq""",
                    (str(row["claim_id"]),)).fetchall())
                claims.append({
                    "claim_id": str(row["claim_id"]),
                    "object": self._row_object(row),
                    "withheld": bool(int(row["payload_withheld"] or 0)),
                    "status": str(row["status"]),
                    "status_reason": row["status_reason"],
                    "revision": int(row["revision"]),
                    "valid_from": row["valid_from"], "valid_to": row["valid_to"],
                    "observed_at": row["observed_at"], "recorded_at": row["recorded_at"],
                    "last_checked_at": row["last_checked_at"],
                    "superseded_by": row["superseded_by"],
                    "source_kind": str(row["source_kind"]),
                    "source_ref": str(row["source_ref"]),
                    "revision_history": [
                        {"kind": str(event["kind"]),
                         "old_revision": event["old_revision"],
                         "new_revision": event["new_revision"],
                         "recorded_at": str(event["recorded_at"])}
                        for event in events],
                })
        return {"ok": True, "slot_id": slot_key,
                "predicate": str(slot["predicate_id"]),
                "scope_id": str(slot["scope_id"]),
                "context": json.loads(slot["context_json"]) if slot["context_json"] else {},
                "observed_at": now_iso, "claims": claims, "read_only": True}

    # -- deterministic rebuild -------------------------------------------------------------

    def rebuild(self) -> dict[str, Any]:
        """Replay the events and verify the materialized tables agree (no repair)."""

        with self._connection(write=False) as conn:
            current = self._materialized(conn)
            replayed = self._replay(conn)
        matches = current == replayed
        return {"ok": True, "matches": matches,
                "claims": len(current["claims"]), "edges": len(current["edges"]),
                "slots": len(current["slots"]),
                "digest_current": _digest(current)[:24],
                "digest_replayed": _digest(replayed)[:24]}

    @staticmethod
    def _materialized(conn: sqlite3.Connection) -> dict[str, Any]:
        def rows(sql: str) -> list[dict[str, Any]]:
            return [dict(row) for row in conn.execute(sql).fetchall()]

        return {
            "slots": rows("SELECT * FROM proposition_slot ORDER BY slot_id"),
            "claims": rows("SELECT * FROM belief_current ORDER BY claim_id"),
            "edges": rows("SELECT * FROM belief_evidence_edge ORDER BY edge_id"),
        }

    def _replay(self, conn: sqlite3.Connection) -> dict[str, Any]:
        slots: dict[str, dict[str, Any]] = {}
        claims: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        for event in conn.execute("SELECT * FROM belief_event ORDER BY aggregate_seq").fetchall():
            kind = str(event["kind"])
            payload = json.loads(event["payload_json"] or "{}")
            if kind in (EVENT_CLAIM_ASSERTED, EVENT_INFERENCE_ASSERTED):
                claim = dict(payload["claim"])
                claims[claim["claim_id"]] = claim
                slot_id = claim["slot_id"]
                slot_row = conn.execute("SELECT * FROM proposition_slot WHERE slot_id = ?",
                                        (slot_id,)).fetchone()
                if slot_row is not None:
                    slots[slot_id] = dict(slot_row)
                if kind == EVENT_CLAIM_ASSERTED:
                    edge = payload.get("edge")
                    if edge is not None:
                        edges[str(edge["edge_id"])] = dict(edge)
                else:
                    for edge in payload.get("edges") or []:
                        edges[str(edge["edge_id"])] = dict(edge)
            elif kind == EVENT_CLAIM_REFRESHED:
                claim = claims.get(payload["claim_id"])
                if claim is not None:
                    claim["last_checked_at"] = payload["last_checked_at"]
                    if payload.get("observed_at"):
                        claim["observed_at"] = payload["observed_at"]
                    claim["revision"] = int(event["new_revision"])
                edge = payload.get("edge")
                if edge is not None:
                    edges[str(edge["edge_id"])] = dict(edge)
            elif kind == EVENT_CLAIM_SUPERSEDED:
                claim = claims.get(payload["claim_id"])
                if claim is not None:
                    claim["status"] = STATUS_SUPERSEDED
                    claim["status_reason"] = REASON_SUPERSEDED
                    if payload.get("valid_to"):
                        claim["valid_to"] = payload["valid_to"]
                    claim["superseded_by"] = payload.get("superseded_by")
                    claim["revision"] = int(event["new_revision"])
            elif kind == EVENT_CLAIM_STALE:
                claim = claims.get(payload["claim_id"])
                if claim is not None:
                    claim["status"] = STATUS_STALE
                    claim["status_reason"] = payload.get("reason")
                    claim["revision"] = int(event["new_revision"])
            elif kind == EVENT_CLAIM_INVALIDATED:
                claim = claims.get(payload["claim_id"])
                if claim is not None:
                    claim["status"] = STATUS_UNKNOWN
                    claim["status_reason"] = payload.get("reason")
                    claim["payload_withheld"] = 1 if payload.get("withheld") else 0
                    claim["revision"] = int(event["new_revision"])
            elif kind == EVENT_EDGE_INVALIDATED:
                edge = edges.get(str(payload.get("edge_id")))
                if edge is not None:
                    edge["invalidated_at"] = str(event["recorded_at"])
                    edge["invalidated_reason"] = payload.get("reason")
        return {"slots": [slots[key] for key in sorted(slots)],
                "claims": [claims[key] for key in sorted(claims)],
                "edges": [edges[key] for key in sorted(edges)]}

    # -- operator-facing convenience -------------------------------------------------------

    def prompt_block(self, *, now: Any = None, limit: int = 6) -> dict[str, Any]:
        """A bounded, non-authoritative block for the decision prompt.

        Only slot/predicate/scope/status/source-kind and a short value.  No raw
        payload, no private text, no prompt and no permission.
        """

        view = self.current_view(now=now, limit=limit)
        claims = []
        for row in view.get("claims") or []:
            obj = row.get("object")
            value: Any = None
            if isinstance(obj, Mapping):
                if "time" in obj:
                    value = obj.get("time")
                elif "place_name" in obj:
                    value = obj.get("place_name")
                elif "place_id" in obj:
                    value = str(obj.get("place_id"))[:60]
                elif "open" in obj:
                    value = bool(obj.get("open"))
                elif "item_ref" in obj:
                    value = str(obj.get("item_ref"))[:60]
            claims.append({
                "predicate": row.get("predicate"),
                "scope": str(row.get("scope_id") or "")[:60],
                "status": row.get("status"),
                "reason": row.get("status_reason"),
                "source_kind": row.get("source_kind"),
                "value": value,
                "observed_at": row.get("observed_at"),
                "recorded_at": row.get("recorded_at"),
                "valid_to": row.get("valid_to"),
                "withheld": bool(row.get("withheld")),
            })
        return {
            "available": True,
            "schema_version": "chiyo.s03.claims.prompt.v1",
            "observed_at": view["observed_at"],
            "claims": claims,
            "conflicts": [{key: row.get(key) for key in
                           ("predicate", "scope_id", "status", "distinct_answers")}
                          for row in view.get("conflicts") or ()][:3],
            "stale": [{key: row.get(key) for key in ("predicate", "scope_id")}
                      for row in view.get("stale") or ()][:3],
            "not_permission": True,
            "not_world_truth": True,
            "actions_authorized": 0,
            "memory_writes": 0,
        }


def edge_claim_slot(conn: sqlite3.Connection, edge: Mapping[str, Any]) -> str:
    row = conn.execute("SELECT slot_id FROM belief_current WHERE claim_id = ?",
                       (str(edge["claim_id"]),)).fetchone()
    return "" if row is None else str(row["slot_id"])


__all__ = [
    "KIND", "PREDICATES", "PredicateSpec", "SCHEMA_VERSION", "STATUS_DISPUTED",
    "STATUS_STALE", "STATUS_SUPERSEDED", "STATUS_SUPPORTED", "STATUS_UNKNOWN",
    "S03ClaimService", "S03ClaimsError", "S03ClaimsRefused", "S03ClaimsUnavailable",
    "SOURCE_INFERRED", "SOURCE_OBSERVED", "SOURCE_REPORTED", "predicate",
]
