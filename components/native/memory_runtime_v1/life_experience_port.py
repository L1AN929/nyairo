"""E07: the only door Life has into Memory, and the only door back out of it.

Two responsibilities, both narrow:

* **In** -- build one M0 ``EvidenceEvent`` for a verified execution outcome and append it through
  the single existing writer (``EvidenceStore.append``).  No second store, no parallel table.
* **Out** -- hand Life a bounded, newest-first, origin-filtered view of those outcomes, after the
  existing governance (correction / deletion / stop-recall) has had its say.  Life never opens a
  Memory database itself; it asks this port.

Why the view is not ``list_events``
-----------------------------------
``EvidenceStore.list_events`` returns every origin mixed together in ascending time order, so it
cannot answer "her most recent verified outcomes".  The store gained
``recent_verified_outcomes`` for exactly that question.

Fail closed
-----------
If the control ledger cannot be read or does not pass its own integrity check, the projection is
unhealthy and ``recalls()`` returns nothing.  An experience whose governance state cannot be
established therefore never reaches a model context -- which is what E07 requires, and is also how
a disabled old experience is prevented from creeping back in through a summary.
"""
from __future__ import annotations

import sys
import json
import sqlite3
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

_NATIVE = str(Path(__file__).resolve().parents[1])
_SRC = str(Path(_NATIVE) / "src")
for _path in (_SRC, _NATIVE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.evidence import (  # noqa: E402
    LIFE_ORIGIN,
    LIFE_ROLE,
    VERIFIED,
    EvidenceEvent,
    EvidenceStore,
    new_event_id,
    utc_now,
)
from memory_control import MemoryControlLedger  # noqa: E402

#: Life events live in their own stream, never in a chat conversation.  The M0 schema requires a
#: conversation_id, so it is named honestly rather than borrowed from a real conversation.
LIFE_CONVERSATION_PREFIX = "life:"
#: Default bound on how much experience text may reach a model.
DEFAULT_MAX_CHARS = 1200
DEFAULT_LIMIT = 5
#: Readiness vocabulary shared with ``components/phone/life.py``'s ``check_memory``: "the path is
#: configured" is never reported as "this can recall".
READINESS_READY = "READY"
READINESS_BLOCKED = "BLOCKED"
READINESS_UNAVAILABLE = "UNAVAILABLE"
#: How many terms of a query are ever honoured (a bounded query, not a search language).
MAX_QUERY_TERMS = 8


class MemoryReadUnavailable(RuntimeError):
    """A named reason the read side cannot answer.  It is never turned into an empty library."""

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(str(code))
        self.code = str(code)
        self.detail = detail


def _query_terms(query: str) -> list[str]:
    """Deterministic, bounded terms: whitespace-split, case-folded, no search language."""
    return [term for term in str(query or "").lower().split()][:MAX_QUERY_TERMS]


def _like_literal(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def life_conversation_id(subject: str) -> str:
    return f"{LIFE_CONVERSATION_PREFIX}{subject}"


@dataclass(frozen=True)
class LifeExperienceRecord:
    """One verified outcome, as Life is allowed to see it."""

    experience_id: str
    event_id: str
    occurred_at: str
    subject: str
    source_ref: str
    text: str
    source_event_ids: list[str] = field(default_factory=list)
    activity_ref: Optional[str] = None
    action_ref: Optional[str] = None
    artifact_ref: Optional[str] = None
    version_ref: Optional[str] = None
    owner_result: Optional[str] = None
    outcome_kind: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "subject": self.subject,
            "source_ref": self.source_ref,
            "text": self.text,
            "activity_ref": self.activity_ref,
            "action_ref": self.action_ref,
            "artifact_ref": self.artifact_ref,
            "version_ref": self.version_ref,
            "owner_result": self.owner_result,
            "outcome_kind": self.outcome_kind,
        }


def build_outcome_event(
    *,
    subject: str,
    source_ref: str,
    text: str,
    occurred_at: str,
    refs: Iterable[tuple[str, str]] = (),
    extra_refs: Iterable[tuple[str, str]] = (),
) -> EvidenceEvent:
    """One M0 event for a verified execution outcome.

    ``source_ref`` must be stable for the thing that happened: M0 dedupes on
    ``(source_origin, primary_source_ref_id)``, so a repeated delivery of the same outcome is a
    ``duplicate`` rather than a second experience.
    """
    source_refs: list[dict[str, str]] = [{"kind": "life_outcome", "id": str(source_ref)}]
    for kind, ident in list(refs) + list(extra_refs):
        if kind and ident:
            source_refs.append({"kind": str(kind), "id": str(ident)})
    return EvidenceEvent(
        event_id=new_event_id(),
        occurred_at=occurred_at,
        memory_owner=subject,
        source_origin=LIFE_ORIGIN,
        delivery_status=VERIFIED,
        epistemic_role=LIFE_ROLE,
        speaker="chiyo",
        content=str(text),
        conversation_id=life_conversation_id(subject),
        turn_id=str(source_ref),
        source_refs=source_refs,
        created_at=utc_now(),
    )


class LifeExperiencePort:
    """Read/write boundary between Life and Memory.  Life holds this, not a database handle."""

    def __init__(
        self,
        evidence_db: str | Path,
        *,
        controls_db: Optional[str | Path] = None,
        subject: str = "chiyo",
        limit: int = DEFAULT_LIMIT,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self.evidence_db = Path(evidence_db)
        self.controls_db = Path(controls_db) if controls_db else self.evidence_db
        self.subject = str(subject)
        self.limit = max(0, int(limit))
        self.max_chars = max(0, int(max_chars))

    # -- in ----------------------------------------------------------------------------

    def append_outcome(self, event: EvidenceEvent) -> dict[str, Any]:
        """Append through the single M0 writer.  Returns the writer's own status."""
        store = EvidenceStore(self.evidence_db)
        result = store.append(event)
        return {"status": result.status, "event_id": result.event_id}

    # -- out ---------------------------------------------------------------------------

    def _projection(self):
        ledger = MemoryControlLedger(self.controls_db, self.evidence_db,
                                     owner=self.subject,
                                     conversation=life_conversation_id(self.subject))
        return ledger.projection()

    def recent(self, *, limit: Optional[int] = None, since: Optional[str] = None,
               governance=None) -> list[LifeExperienceRecord]:
        """Newest verified outcomes, governance applied, bounded.  Empty when governance is unsure."""
        want = self.limit if limit is None else max(0, int(limit))
        if want == 0:
            return []
        store = EvidenceStore(self.evidence_db)
        rows = store.recent_verified_outcomes(memory_owner=self.subject, limit=want, since=since)
        records = [self._to_record(row) for row in rows]
        if not records:
            return []
        projection = governance if governance is not None else self._projection()
        if not getattr(projection, "healthy", False):
            # Governance state is not established: nothing may enter a model context.
            return []
        kept, _corrections = projection.recalls(records)
        return [self._trim(record) for record in kept]

    @staticmethod
    def _to_record(row: dict[str, Any]) -> LifeExperienceRecord:
        refs = {str(item.get("kind")): str(item.get("id"))
                for item in (row.get("source_refs") or []) if isinstance(item, dict)}
        return LifeExperienceRecord(
            experience_id=str(row.get("event_id")),
            event_id=str(row.get("event_id")),
            occurred_at=str(row.get("occurred_at")),
            subject=str(row.get("memory_owner")),
            source_ref=refs.get("life_outcome", str(row.get("primary_source_ref_id"))),
            text=str(row.get("content") or ""),
            source_event_ids=[str(row.get("event_id"))],
            activity_ref=refs.get("activity"),
            action_ref=refs.get("action"),
            artifact_ref=refs.get("artifact"),
            version_ref=refs.get("artifactversion"),
            owner_result=refs.get("owner_result"),
            outcome_kind=refs.get("outcome_kind"),
        )

    def _trim(self, record: LifeExperienceRecord) -> LifeExperienceRecord:
        if self.max_chars and len(record.text) > self.max_chars:
            return LifeExperienceRecord(**{**record.__dict__,
                                           "text": record.text[: self.max_chars].rstrip() + "…"})
        return record

    # -- read-only recall (never a store constructor) ------------------------------------

    @staticmethod
    def _readonly(path: Path | str) -> sqlite3.Connection:
        """An already existing SQLite file, opened ``mode=ro``: it can neither create nor migrate."""
        connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                                    timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _as_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        refs = data.pop("source_refs_json", None)
        data["source_refs"] = json.loads(refs) if refs else []
        return data

    @staticmethod
    def _not_ready(out: dict[str, Any], reason: str, detail: Optional[str],
                   status: str = READINESS_BLOCKED) -> dict[str, Any]:
        out.update({"binding_status": status, "recall_ready": False,
                    "reason": reason, "detail": detail})
        return out

    def readiness(self) -> dict[str, Any]:
        """Can this binding actually recall anything?  Pure read, zero write.

        The M0 file and the control ledger are only ever opened ``mode=ro``.  No
        ``EvidenceStore`` is constructed -- its ``__init__`` would ``mkdir`` the parent, run
        migrations and ``chmod`` the file -- and no ledger is initialized.  A read path that can
        create a database is not a read path.
        """
        evidence, controls = Path(self.evidence_db), Path(self.controls_db)
        out: dict[str, Any] = {
            "binding_status": READINESS_BLOCKED,
            "recall_ready": False,
            "reason": None,
            "detail": None,
            "owner": self.subject,
            "database": str(evidence),
            "controls_database": str(controls),
            "controls_defaulted": controls == evidence,
            "database_exists": False,
            "database_is_file": False,
            "controls_exists": False,
            "controls_is_file": False,
            "integrity": None,
            "evidence_table": False,
            "governance_healthy": None,
            "controls": None,
        }
        for key, path in (("database", evidence), ("controls", controls)):
            try:
                info = path.lstat()
            except OSError:
                continue
            out[key + "_exists"] = True
            out[key + "_is_file"] = stat.S_ISREG(info.st_mode)
        if not out["database_exists"]:
            return self._not_ready(
                out, "MEMORY_DB_MISSING",
                "the configured Memory database does not exist: %s" % (evidence,))
        if not out["database_is_file"]:
            return self._not_ready(
                out, "MEMORY_DB_NOT_A_FILE",
                "the configured Memory database is not a regular file: %s" % (evidence,))
        if not out["controls_exists"]:
            return self._not_ready(
                out, "MEMORY_CONTROLS_DB_MISSING",
                "the configured control ledger does not exist: %s" % (controls,))
        if not out["controls_is_file"]:
            return self._not_ready(
                out, "MEMORY_CONTROLS_DB_NOT_A_FILE",
                "the configured control ledger is not a regular file: %s" % (controls,))
        try:
            connection = self._readonly(evidence)
        except Exception as exc:
            return self._not_ready(out, "MEMORY_DB_UNREADABLE",
                                   "%s: %s" % (type(exc).__name__, exc))
        try:
            out["integrity"] = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            out["evidence_table"] = bool(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_events'"
            ).fetchone())
        except Exception as exc:
            return self._not_ready(out, "MEMORY_DB_UNREADABLE",
                                   "%s: %s" % (type(exc).__name__, exc))
        finally:
            connection.close()
        if str(out["integrity"] or "").lower() != "ok":
            return self._not_ready(
                out, "MEMORY_DB_INTEGRITY_FAILURE",
                "PRAGMA quick_check on the Memory database did not return ok (%r)"
                % (out["integrity"],))
        if not out["evidence_table"]:
            return self._not_ready(
                out, "MEMORY_DB_SCHEMA_MISMATCH",
                "the configured database has no evidence_events table, so it is not M0")
        try:
            projection = self._projection()
        except Exception as exc:
            out.update({"binding_status": READINESS_UNAVAILABLE,
                        "reason": "MEMORY_GOVERNANCE_PROBE_FAILED",
                        "detail": "%s: %s" % (type(exc).__name__, exc)})
            return out
        out["governance_healthy"] = bool(getattr(projection, "healthy", False))
        out["controls"] = int(getattr(projection, "controls", 0) or 0)
        if not out["governance_healthy"]:
            detail = ("the control ledger projection is unhealthy, so nothing at all would be "
                      "recalled from this Memory")
            if out["controls_defaulted"]:
                detail += (" — no separate control ledger is configured, so the M0 database "
                           "itself is used as the ledger and has no memory_controls table")
            return self._not_ready(out, "MEMORY_CONTROLS_UNHEALTHY", detail)
        out.update({"binding_status": READINESS_READY, "reason": None, "detail": None,
                    "recall_ready": True})
        return out

    def search(self, *, query: str = "", limit: Optional[int] = None,
               since: Optional[str] = None) -> list[LifeExperienceRecord]:
        """Relevant experiences for this owner, governance applied, newest first.

        Unlike :meth:`recent`, an empty query is not the only question: every whitespace-separated
        term must appear in the experience's text or in its own refs, so a genuinely *older*
        relevant experience is found even when the newest five are unrelated.  Reading is done
        ``mode=ro``; the failure of a binding is raised as a named :class:`MemoryReadUnavailable`,
        never returned as an empty list.
        """
        want = self.limit if limit is None else max(0, int(limit))
        if want == 0:
            return []
        readiness = self.readiness()
        if not readiness.get("recall_ready"):
            raise MemoryReadUnavailable(str(readiness.get("reason") or "MEMORY_BINDING_UNUSABLE"),
                                        readiness.get("detail"))
        rows = self._select_rows(_query_terms(query), want=want, since=since)
        records = [self._to_record(row) for row in rows]
        if not records:
            return []
        projection = self._projection()
        if not getattr(projection, "healthy", False):
            raise MemoryReadUnavailable("MEMORY_CONTROLS_UNHEALTHY")
        kept, _corrections = projection.recalls(records)
        return [self._trim(record) for record in kept]

    def read(self, experience_id: str) -> Optional[LifeExperienceRecord]:
        """One experience by id, same owner and same governance as :meth:`search`.

        ``None`` means *not recallable* — absent, another owner's, corrected away or deleted.
        The three are deliberately indistinguishable so that a governance decision cannot be
        probed, and a stale body is never returned for any of them.
        """
        key = str(experience_id or "").strip()
        if not key:
            raise ValueError("experience_id is required")
        readiness = self.readiness()
        if not readiness.get("recall_ready"):
            raise MemoryReadUnavailable(str(readiness.get("reason") or "MEMORY_BINDING_UNUSABLE"),
                                        readiness.get("detail"))
        connection = self._readonly(self.evidence_db)
        try:
            row = connection.execute(
                "SELECT * FROM evidence_events WHERE event_id=? AND memory_owner=? "
                "AND source_origin=?", (key, self.subject, LIFE_ORIGIN)).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        record = self._to_record(self._as_dict(row))
        projection = self._projection()
        if not getattr(projection, "healthy", False):
            raise MemoryReadUnavailable("MEMORY_CONTROLS_UNHEALTHY")
        kept, _corrections = projection.recalls([record])
        return self._trim(kept[0]) if kept else None

    def _select_rows(self, terms: list[str], *, want: int,
                     since: Optional[str] = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM evidence_events WHERE memory_owner=? AND source_origin=?"
        params: list[Any] = [self.subject, LIFE_ORIGIN]
        if since:
            sql += " AND occurred_at > ?"
            params.append(str(since))
        for term in terms:
            sql += (" AND (lower(content) LIKE ? ESCAPE '\\' "
                    "OR lower(source_refs_json) LIKE ? ESCAPE '\\')")
            pattern = "%" + _like_literal(term) + "%"
            params.extend([pattern, pattern])
        sql += " ORDER BY occurred_at DESC, created_at DESC, event_id DESC LIMIT ?"
        params.append(int(want))
        connection = self._readonly(self.evidence_db)
        try:
            rows = connection.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise MemoryReadUnavailable("MEMORY_DB_UNREADABLE",
                                        "%s: %s" % (type(exc).__name__, exc)) from exc
        finally:
            connection.close()
        return [self._as_dict(row) for row in rows]

    # -- model-facing view ---------------------------------------------------------------

    def prompt_view(self, records: Optional[list[LifeExperienceRecord]] = None) -> list[dict[str, Any]]:
        """The bounded, attributable view handed to a model.  Never the whole memory."""
        items = self.recent() if records is None else records
        return [
            {
                "experience_id": record.experience_id,
                "occurred_at": record.occurred_at,
                "text": record.text,
                "activity_ref": record.activity_ref,
                "artifact_ref": record.artifact_ref,
                "version_ref": record.version_ref,
                "owner_result": record.owner_result,
            }
            for record in items
        ]


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_CHARS",
    "LIFE_CONVERSATION_PREFIX",
    "LifeExperiencePort",
    "LifeExperienceRecord",
    "MAX_QUERY_TERMS",
    "MemoryReadUnavailable",
    "READINESS_BLOCKED",
    "READINESS_READY",
    "READINESS_UNAVAILABLE",
    "build_outcome_event",
    "life_conversation_id",
]
