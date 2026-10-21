"""E07: AG-2's ExperienceConsumer, backed by the Memory boundary port.

This replaces ``RefusingExperienceConsumer`` in the production assembly -- opt-in, and off unless a
port is supplied -- so a verified outcome can actually become a memory instead of being refused.

What it keeps honest
--------------------
* The M0 ``source_ref`` is the handoff's own ``lexp:`` id, which AG-2 derives from
  ``(activity, kind, revision)``.  M0 dedupes on that identity, so the same outcome delivered
  twice is a ``duplicate``, never a second experience.
* The **owner is the bound port's own subject**.  It is never taken from ``subject_id``: a handoff
  spelling such as ``chiyo.global`` (or any other instance's id) must not decide whose memory this
  row belongs to, or one instance's outcome lands in another owner's stream.
* The text is built only from the event's structured fields.  A conflict is written as a conflict;
  nothing is described as a success that the owning service did not confirm.
* A handoff that carries World facts and **no** artifact ref is a World consequence, not a work
  modification: it is written with a neutral sentence and the owner's own ``world`` / ``action``
  / ``owner_result`` refs.  It never claims a modified work or a new version, and an event that
  arrives carrying an unresolved outcome is never upgraded to a success.
* C may attach an optional ``owner_confirmed_context`` mapping
  ``{artifact_id, version_ref, title, result_description, outcome_confirmed: true}``.  Only those
  five keys are ever read, only when every type is right and every reference is a real one carried
  by the same event; anything else is dropped and the generic sentence is used instead.  A model's
  own note is not a fact and is never promoted to one.  C owns the owner check for that mapping --
  this consumer only checks structure and references.
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

#: World consequences are **not** work modifications.  A handoff that carries World facts and
#: no artifact ref is written with these neutral sentences: they say only that something was
#: confirmed (the cycle emits such a handoff only from an owner receipt/observation) and let
#: the owner's own refs carry *what* it was -- no verb, no body effect, no nutrition value and
#: no "new version" is invented from free text.
_WORLD_CONFIRMED = "在真实世界里发生了一件已确证的事"
_WORLD_UNSETTLED = "在真实世界里做过一件事，结果还没有确定"
#: Outcomes that must never be written as a success.  Today's ``UNKNOWN`` path does not emit a
#: World consequence at all (``reconcile_unknown_step`` claims nothing), so this is the guard
#: for a handoff that *does* arrive carrying its own unsettled verdict.
_UNSETTLED_OUTCOMES = frozenset({"UNKNOWN_EFFECT", "UNKNOWN", "PENDING", "OPEN"})

#: The complete set of keys C's optional confirmed-result mapping may carry.  Anything else -- a
#: model's ``self_note``, a reflection paragraph, a free-form summary -- is not read at all.
OWNER_CONFIRMED_KEYS = ("artifact_id", "version_ref", "title", "result_description",
                        "outcome_confirmed")
#: Any event field that can carry a real reference; a version ref must appear in one of these.
_REF_FIELDS = ("artifact_refs", "fact_refs", "settlement_refs", "progress_refs", "action_refs",
               "world_refs", "decision_refs", "adoption_refs", "causal_parent_refs")


class _OwnerUnresolved(RuntimeError):
    """The bound port cannot name a Memory owner, so nothing may be written."""


def _real_refs(event: Mapping[str, Any]) -> set[str]:
    """Every reference the event itself really carries (no invention, no inference)."""

    refs: set[str] = set()
    for field in _REF_FIELDS:
        for value in (event.get(field) or []):
            if value:
                refs.add(str(value))
    completion = event.get("completion_evidence_ref")
    if completion:
        refs.add(str(completion))
    activity = event.get("activity_ref")
    if activity:
        refs.add(str(activity))
    return refs


class MemoryAdmissionExperienceConsumer:
    """Delivers AG-2 experience handoffs into Memory through the one legal writer."""

    #: How much of a confirmed description may reach a memory row (a bounded sentence, not a
    #: document).  Long text is truncated here, not quoted whole.
    MAX_DESCRIPTION_CHARS = 320

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
        #: How the optional confirmed-result mapping was treated: counted, never quoted.
        self.owner_context: dict[str, int] = {}

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
        try:
            owner = self._owner(event)
        except _OwnerUnresolved as exc:
            self.deferred += 1
            self.last_error = f"_OwnerUnresolved: {exc}"
            return {"disposition": DISPOSITION_DEFER, "reason": "MEMORY_OWNER_UNRESOLVED",
                    "detail": str(exc)[:200]}
        confirmed, confirmed_note = self.confirmed_context(event)
        self.owner_context[confirmed_note] = self.owner_context.get(confirmed_note, 0) + 1
        refs = self._refs(event, kind, confirmed)
        text = self._text(event, kind, refs, confirmed)

        receipt = {"owner_confirmed_context": confirmed_note}
        try:
            outcome_event = build_outcome_event(
                subject=owner,
                source_ref=handoff_id,
                text=text,
                occurred_at=occurred_at,
                refs=refs,
            )
            result = self.port.append_outcome(outcome_event)
        except Exception as exc:  # a Memory failure must never roll back the work
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.deferred += 1
            return dict(receipt, disposition=DISPOSITION_DEFER, reason="MEMORY_WRITE_FAILED",
                        detail=self.last_error[:200])

        status = str(result.get("status") or "")
        if status == "inserted":
            self.accepted += 1
            return dict(receipt, disposition=DISPOSITION_ACCEPT, event_id=result.get("event_id"),
                        source_ref=handoff_id, owner=owner)
        if status == "duplicate":
            self.duplicates += 1
            return dict(receipt, disposition=DISPOSITION_DUPLICATE,
                        event_id=result.get("event_id"), source_ref=handoff_id, owner=owner)
        # queued / failed: the memory owner did not take it yet.
        self.deferred += 1
        return dict(receipt, disposition=DISPOSITION_DEFER,
                    reason=f"MEMORY_{status.upper() or 'UNKNOWN'}")

    # -- owner -------------------------------------------------------------------------

    def _owner(self, event: Mapping[str, Any]) -> str:
        """The Memory owner is the *bound port's own subject*, never a handoff's spelling.

        The old code took ``subject_id.split(".")[0]``, which quietly accepted any domain that
        arrived in the payload and could file this instance's outcome under another owner (the
        Supply/host subject, a ``.global`` variant, or a foreign instance's id).  A Memory row's
        owner is a binding fact: it comes from the port that was built for this instance.
        """

        owner = str(getattr(self.port, "subject", "") or "").strip()
        if not owner:
            raise _OwnerUnresolved("the bound Memory port exposes no owner")
        return owner

    # -- the optional confirmed-result mapping (C's handoff boundary) --------------------

    def confirmed_context(self, event: Mapping[str, Any]) -> tuple[Optional[dict[str, Any]],
                                                                  str]:
        """Accept C's ``owner_confirmed_context`` only when it is type-correct and anchored.

        Returns ``(mapping, note)``.  ``note`` is a stable, content-free code for the receipt:
        ``USED``, ``USED_WITH_IGNORED_KEYS``, or one of ``ABSENT`` / ``NOT_A_MAPPING`` /
        ``NOT_CONFIRMED`` / ``DESCRIPTION_MISSING`` / ``ARTIFACT_MISSING`` /
        ``ARTIFACT_NOT_REFERENCED`` / ``VERSION_MISSING`` / ``VERSION_NOT_REFERENCED`` /
        ``TITLE_NOT_TEXT`` / ``UNSETTLED``.  Anything rejected falls back to the generic
        sentence: a rejected description is never written as fact.
        """

        raw = event.get("owner_confirmed_context")
        if raw is None:
            return None, "ABSENT"
        if not isinstance(raw, Mapping):
            return None, "NOT_A_MAPPING"
        if raw.get("outcome_confirmed") is not True:
            return None, "NOT_CONFIRMED"
        description = raw.get("result_description")
        artifact_id = raw.get("artifact_id")
        version_ref = raw.get("version_ref")
        title = raw.get("title")
        if not isinstance(description, str) or not description.strip():
            return None, "DESCRIPTION_MISSING"
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            return None, "ARTIFACT_MISSING"
        if not isinstance(version_ref, str) or not version_ref.strip():
            return None, "VERSION_MISSING"
        if title is not None and not isinstance(title, str):
            return None, "TITLE_NOT_TEXT"
        artifacts = {str(item) for item in (event.get("artifact_refs") or []) if item}
        if artifact_id.strip() not in artifacts:
            return None, "ARTIFACT_NOT_REFERENCED"
        if version_ref.strip() not in _real_refs(event):
            return None, "VERSION_NOT_REFERENCED"
        worlds = [str(item) for item in (event.get("world_refs") or []) if item]
        if self._unsettled(event, worlds):
            # A confirmed description may not overturn an explicitly unresolved outcome.
            return None, "UNSETTLED"
        ignored = [str(key) for key in raw if str(key) not in OWNER_CONFIRMED_KEYS]
        return ({"artifact_id": artifact_id.strip(),
                 "version_ref": version_ref.strip(),
                 "title": str(title).strip() if isinstance(title, str) else None,
                 "result_description": description.strip()[: self.MAX_DESCRIPTION_CHARS]},
                "USED_WITH_IGNORED_KEYS" if ignored else "USED")

    # -- shaping -----------------------------------------------------------------------

    @staticmethod
    def _refs(event: Mapping[str, Any], kind: str,
              confirmed: Optional[Mapping[str, Any]] = None) -> list[tuple[str, str]]:
        refs: list[tuple[str, str]] = [("outcome_kind", kind or "UNKNOWN")]
        activity = event.get("activity_ref")
        if activity:
            refs.append(("activity", str(activity)))
        for action_ref in (event.get("action_refs") or [])[:1]:
            refs.append(("action", str(action_ref)))
        for artifact_ref in (event.get("artifact_refs") or [])[:1]:
            refs.append(("artifact", str(artifact_ref)))
        # The World owner's own facts travel with the row: a World consequence keeps the
        # provenance it was admitted for, and no artifact is invented to fill the gap.
        for world_ref in (event.get("world_refs") or [])[:2]:
            refs.append(("world", str(world_ref)))
        for settlement_ref in (event.get("settlement_refs") or [])[:1]:
            refs.append(("owner_result", str(settlement_ref)))
        if confirmed:
            # Last one wins in the row's ref map: the *confirmed* artifact/version is the one the
            # sentence is about, and it was already proven to be a real reference of this event.
            refs.append(("artifact", str(confirmed["artifact_id"])))
            refs.append(("artifactversion", str(confirmed["version_ref"])))
        return refs

    def _text(self, event: Mapping[str, Any], kind: str, refs: list[tuple[str, str]],
              confirmed: Optional[Mapping[str, Any]] = None) -> str:
        by_kind = {k: v for k, v in refs}
        worlds = [str(ref) for ref in (event.get("world_refs") or []) if ref]
        if worlds and not by_kind.get("artifact"):
            # A World-only consequence: one neutral sentence plus exactly the refs the owner
            # confirmed.  Nothing here claims a work was modified or that a new version
            # exists, because no artifact ref says so -- and nothing describes *what* she did
            # beyond the owner's own fact tokens.
            return self._trim(self._world_text(event, by_kind, worlds))
        if confirmed:
            return self._trim(self._confirmed_text(event, by_kind, confirmed))
        phrase = _KIND_PHRASES.get(kind, f"发生了一件与作品有关的事（{kind or 'UNKNOWN'}）")
        parts = [phrase]
        if by_kind.get("artifact"):
            parts.append(f"作品 {by_kind['artifact']}")
        if by_kind.get("owner_result"):
            parts.append(f"所有者回执 {by_kind['owner_result']}")
        if event.get("occurred_at"):
            parts.append(f"发生在 {event['occurred_at']}")
        return self._trim("；".join(parts) + "。")

    @staticmethod
    def _confirmed_text(event: Mapping[str, Any], by_kind: dict[str, str],
                        confirmed: Mapping[str, Any]) -> str:
        """A bounded sentence about what the owner actually confirmed -- nothing more."""

        parts: list[str] = []
        if confirmed.get("title"):
            parts.append(str(confirmed["title"]))
        parts.append(str(confirmed["result_description"]))
        parts.append(f"作品 {confirmed['artifact_id']}")
        parts.append(f"版本 {confirmed['version_ref']}")
        if by_kind.get("owner_result"):
            parts.append(f"所有者回执 {by_kind['owner_result']}")
        if event.get("occurred_at"):
            parts.append(f"发生在 {event['occurred_at']}")
        return "；".join(parts) + "。"

    def _world_text(self, event: Mapping[str, Any], by_kind: dict[str, str],
                    worlds: list[str]) -> str:
        parts = [_WORLD_UNSETTLED if self._unsettled(event, worlds) else _WORLD_CONFIRMED]
        parts.append("世界事实 " + "、".join(worlds[:2]))
        if by_kind.get("action"):
            parts.append(f"动作 {by_kind['action']}")
        if by_kind.get("owner_result"):
            parts.append(f"所有者回执 {by_kind['owner_result']}")
        if event.get("occurred_at"):
            parts.append(f"发生在 {event['occurred_at']}")
        return "；".join(parts) + "。"

    @staticmethod
    def _unsettled(event: Mapping[str, Any], worlds: list[str]) -> bool:
        """Never upgrade an unresolved outcome to a success."""

        declared = {str(event.get("outcome_kind") or "").upper(),
                    str(event.get("settlement_outcome") or "").upper()}
        if declared & _UNSETTLED_OUTCOMES:
            return True
        return any(str(ref).upper().rsplit(":", 1)[-1] in _UNSETTLED_OUTCOMES
                   for ref in worlds)

    def _trim(self, text: str) -> str:
        return text[: self.max_chars] if self.max_chars else text

    def describe(self) -> dict[str, Any]:
        return {"kind": "memory_admission_experience_consumer", "enabled": self.enabled,
                "accepted": self.accepted, "duplicates": self.duplicates,
                "deferred": self.deferred, "ignored": self.ignored,
                "last_error": self.last_error,
                "owner_confirmed_context": dict(self.owner_context)}


__all__ = [
    "DISPOSITION_ACCEPT",
    "DISPOSITION_DEFER",
    "DISPOSITION_DUPLICATE",
    "DISPOSITION_IGNORE",
    "MemoryAdmissionExperienceConsumer",
    "OWNER_CONFIRMED_KEYS",
]
