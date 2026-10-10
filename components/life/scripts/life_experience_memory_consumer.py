"""E07: AG-2's ExperienceConsumer, backed by the Memory boundary port.

This replaces ``RefusingExperienceConsumer`` in the production assembly -- opt-in, and off unless a
port is supplied -- so a verified outcome can actually become a memory instead of being refused.

What it keeps honest
--------------------
* The M0 ``source_ref`` is the handoff's own ``lexp:`` id, which AG-2 derives from
  ``(activity, kind, revision)``.  M0 dedupes on that identity, so the same outcome delivered
  twice is a ``duplicate``, never a second experience.
* The text is built only from the event's structured fields.  A conflict is written as a conflict;
  nothing is described as a success that the owning service did not confirm.
* A Memory failure returns ``DEFER``, which AG-2 keeps in ``pending_handoff_ids`` for a later
  retry, and which never rolls back the artifact or the settlement.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

# The Memory boundary port lives in the native tree.
_NATIVE_ROOT = Path(__file__).resolve().parents[2] / "native"
_MEMORY_RUNTIME = _NATIVE_ROOT / "memory_runtime_v1"
for _path in (str(_MEMORY_RUNTIME), str(_NATIVE_ROOT), str(_NATIVE_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from life_experience_port import LifeExperiencePort, build_outcome_event  # noqa: E402

DISPOSITION_ACCEPT = "ACCEPT"
DISPOSITION_IGNORE = "IGNORE"
DISPOSITION_DUPLICATE = "DUPLICATE"
DISPOSITION_DEFER = "DEFER"

#: What each AG-2 milestone kind means, said in plain words.  Nothing here claims more than the
#: owning service confirmed.
_KIND_PHRASES = {
    "SIGNIFICANT_PROGRESS": "完成了一次真实修改，作品所有者确认了新版本",
    "COMPLETED": "把这件事做完了",
    "ABANDONED": "放下了这件事（作品仍然留着）",
    "CONFLICT": "一次改动被拒了：读到的版本已经过期，没有覆盖别人的修改",
}


class MemoryAdmissionExperienceConsumer:
    """Delivers AG-2 experience handoffs into Memory through the one legal writer."""

    def __init__(
        self,
        port: Optional[LifeExperiencePort] = None,
        *,
        enabled: bool = False,
        max_chars: int = 600,
    ) -> None:
        self.port = port
        self.enabled = bool(enabled and port is not None)
        self.max_chars = max(0, int(max_chars))
        self.accepted = 0
        self.duplicates = 0
        self.deferred = 0
        self.ignored = 0
        self.last_error: Optional[str] = None

    # -- the protocol ------------------------------------------------------------------

    def receive_experience_handoff(self, event: Mapping[str, Any]) -> dict[str, Any]:
        if not self.enabled or self.port is None:
            self.ignored += 1
            return {"disposition": DISPOSITION_IGNORE,
                    "reason": "MEMORY_ADMISSION_DISABLED"}

        handoff_id = str(event.get("handoff_id") or "")
        if not handoff_id:
            self.deferred += 1
            return {"disposition": DISPOSITION_DEFER, "reason": "HANDOFF_ID_MISSING"}

        occurred_at = str(event.get("occurred_at") or event.get("observed_at") or "")
        kind = str(event.get("terminal_or_milestone_kind") or "")
        refs = self._refs(event, kind)
        text = self._text(event, kind, refs)

        try:
            outcome_event = build_outcome_event(
                subject=str(event.get("subject_id") or "chiyo").split(".")[0],
                source_ref=handoff_id,
                text=text,
                occurred_at=occurred_at,
                refs=refs,
            )
            result = self.port.append_outcome(outcome_event)
        except Exception as exc:  # a Memory failure must never roll back the work
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.deferred += 1
            return {"disposition": DISPOSITION_DEFER, "reason": "MEMORY_WRITE_FAILED",
                    "detail": self.last_error[:200]}

        status = str(result.get("status") or "")
        if status == "inserted":
            self.accepted += 1
            return {"disposition": DISPOSITION_ACCEPT, "event_id": result.get("event_id"),
                    "source_ref": handoff_id}
        if status == "duplicate":
            self.duplicates += 1
            return {"disposition": DISPOSITION_DUPLICATE, "event_id": result.get("event_id"),
                    "source_ref": handoff_id}
        # queued / failed: the memory owner did not take it yet.
        self.deferred += 1
        return {"disposition": DISPOSITION_DEFER, "reason": f"MEMORY_{status.upper() or 'UNKNOWN'}"}

    # -- shaping -----------------------------------------------------------------------

    @staticmethod
    def _refs(event: Mapping[str, Any], kind: str) -> list[tuple[str, str]]:
        refs: list[tuple[str, str]] = [("outcome_kind", kind or "UNKNOWN")]
        activity = event.get("activity_ref")
        if activity:
            refs.append(("activity", str(activity)))
        for action_ref in (event.get("action_refs") or [])[:1]:
            refs.append(("action", str(action_ref)))
        for artifact_ref in (event.get("artifact_refs") or [])[:1]:
            refs.append(("artifact", str(artifact_ref)))
        for settlement_ref in (event.get("settlement_refs") or [])[:1]:
            refs.append(("owner_result", str(settlement_ref)))
        return refs

    def _text(self, event: Mapping[str, Any], kind: str,
              refs: list[tuple[str, str]]) -> str:
        phrase = _KIND_PHRASES.get(kind, f"发生了一件与作品有关的事（{kind or 'UNKNOWN'}）")
        by_kind = {k: v for k, v in refs}
        parts = [phrase]
        if by_kind.get("artifact"):
            parts.append(f"作品 {by_kind['artifact']}")
        if by_kind.get("owner_result"):
            parts.append(f"所有者回执 {by_kind['owner_result']}")
        if event.get("occurred_at"):
            parts.append(f"发生在 {event['occurred_at']}")
        text = "；".join(parts) + "。"
        return text[: self.max_chars] if self.max_chars else text

    def describe(self) -> dict[str, Any]:
        return {"kind": "memory_admission_experience_consumer", "enabled": self.enabled,
                "accepted": self.accepted, "duplicates": self.duplicates,
                "deferred": self.deferred, "ignored": self.ignored,
                "last_error": self.last_error}


__all__ = [
    "DISPOSITION_ACCEPT",
    "DISPOSITION_DEFER",
    "DISPOSITION_DUPLICATE",
    "DISPOSITION_IGNORE",
    "MemoryAdmissionExperienceConsumer",
]
