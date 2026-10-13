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

    # -- model-facing view -------------------------------------------------------------

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
    "build_outcome_event",
    "life_conversation_id",
]
