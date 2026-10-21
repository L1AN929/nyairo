"""rc1 C: the subject's *own* bound materials, read as a version-sensitive situation.

What this module is
-------------------
One **pure read**.  It answers the question a decision needs in order to be a real one:
"where does my own material actually stand right now?" -- the owner's current revision of
the bound reading material, whether the stop point in my private-items document still
belongs to *that* revision and was really acquired, whether there is a next page, whether
the material is finished, and how many private notes/questions I left beside it.

What it is not
--------------
* It owns nothing.  No store, no cache, no page text, no comprehension score: ``understanding``
  is ``UNKNOWN`` in every answer, exactly like the rest of the reading chain
  (``private_daily_items.UNDERSTANDING_UNKNOWN``).
* It never guesses which artifact is which.  Both the reading material and the private-items
  document are named by the caller; an unbound or mismatched binding is reported
  (``READING_NOT_BOUND`` / ``PRIVATE_ITEMS_UNAVAILABLE``), never filled in from recency.
* It writes nothing and calls no model.  Every fact comes from a read of the ManagedArtifact
  owner through ``LifeArtifactPort``; a page number is where she *is*, never what she
  understood.

Why the numbers are spelled into fact keys
------------------------------------------
The production prompt keeps only ASCII ``[A-Za-z0-9_]`` **keys** of ``observed_facts``
(``agency_cognition_adapter.py:203``), bounded to 24 of them and 48 characters each.  A page
number that only lives in a value or inside a ref (``artifactversion:…:3``) is therefore
invisible to the model, so :func:`reading_fact_keys` puts the *number itself* in the key
(``reading_page_2``) and leaves the exact refs in the reported situation.  No key ever
carries material text, a note body or a version ref that the sanitiser would swallow.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

import private_daily_items as pdi  # noqa: E402
from life_artifact_port import (  # noqa: E402
    ArtifactOwnerRefused,
    LifeArtifactUnavailable,
)

#: How the reading material marks its pages (same marker the cycle and the phone use).
PAGE_MARKER = "%%PAGE%%"

#: The only value this module ever reports for comprehension.
UNDERSTANDING_UNKNOWN = pdi.UNDERSTANDING_UNKNOWN

# -- states: each one is a different real situation, never merged --------------------------
#: A next page exists and may be read.
STATE_AVAILABLE = "AVAILABLE"
#: The current revision is fully read: there is no next page (a normal result, not an error).
STATE_EXHAUSTED = "EXHAUSTED"
#: This instance named no reading material / private-items document at all.
STATE_READING_NOT_BOUND = "READING_NOT_BOUND"
#: The owner holds the artifact but it has no committed version yet.
STATE_MATERIAL_UNCOMMITTED = "MATERIAL_UNCOMMITTED"
#: The artifact is not ACTIVE (archived / withdrawn at the artifact level).
STATE_MATERIAL_NOT_ACTIVE = "MATERIAL_NOT_ACTIVE"
#: The artifact's content is empty (or only whitespace): there is no page to read.
STATE_MATERIAL_EMPTY = "MATERIAL_EMPTY"
#: The record of the last stop is not a page number (a locator string): not plannable.
STATE_POSITION_NOT_A_PAGE = "POSITION_NOT_A_PAGE"
#: The recorded stop page does not exist in the current revision.
STATE_POSITION_OUT_OF_RANGE = "POSITION_OUT_OF_RANGE"
#: Every acquisition of the current revision has been marked unavailable (the source was
#: revoked): a stop on it is not a valid stop.
STATE_SOURCE_REVOKED = "SOURCE_REVOKED"
#: The private-items document could not be read or decoded.
STATE_PRIVATE_ITEMS_UNAVAILABLE = "PRIVATE_ITEMS_UNAVAILABLE"
#: The owner could not be reached at all.
STATE_OWNER_UNAVAILABLE = "OWNER_UNAVAILABLE"
#: The owner answered, but refused the read.
STATE_OWNER_REFUSED = "OWNER_REFUSED"

#: Bound on ``observed_facts`` keys that reach the production prompt (see module docstring).
MAX_FACT_KEYS = 24
#: Bound on one fact key (prompt side truncates at 48; we never emit more to begin with).
MAX_FACT_KEY_CHARS = 48
#: How many of those keys this situation may occupy by itself.
MAX_READING_FACT_KEYS = 8

_EMPTY_MATERIAL: dict[str, Any] = {
    "available": False, "lifecycle": None, "version_id": None, "version_number": None,
    "pages": None, "content_chars": None, "title": None, "error": "",
}
_EMPTY_POSITION: dict[str, Any] = {
    "page": None, "next_page": None, "position_kind": None, "set_at": None,
    "position_id": None, "acquisition_id": None, "on_current_revision": False,
    "needs_reread": False, "needs_reread_reason": "", "prior_revision_pages": [],
}
_EMPTY_PRIVATE_ITEMS: dict[str, Any] = {
    "ok": False, "owner_version_number": None, "doc_revision": None, "error": "",
}


def _token(value: Any, *, limit: int = 24) -> str:
    """One ASCII spelling that survives the prompt's key projection (never text)."""
    text = "".join(ch if (ch.isascii() and (ch.isalnum() or ch == "_")) else "_"
                   for ch in str(value or ""))
    return text.strip("_")[:limit] or "unknown"


def _source_id(reading_artifact_id: str) -> str:
    """The private-items ``source_id`` for the reading material (one ``artifact:`` layer)."""
    text = str(reading_artifact_id or "").strip()
    if not text:
        return ""
    return text if text.startswith("artifact:") else "artifact:%s" % text


def _split_pages(content: Optional[str], marker: str = PAGE_MARKER) -> list[str]:
    """The material's real pages.  No marker means one page, never zero."""
    if not isinstance(content, str):
        return []
    if marker not in content:
        return [content]
    return content.split(marker)


def _is_page(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_reading_situation(
    port: Any,
    *,
    reading_artifact_id: Optional[str] = None,
    private_items_artifact_id: Optional[str] = None,
    page_marker: str = PAGE_MARKER,
) -> dict[str, Any]:
    """One read-only situation of the bound materials, from the owner's own records.

    The answer always has the same shape (see ``_EMPTY_*`` above); ``state`` says what the
    situation really is.  Nothing here repairs a missing fact: an unreadable owner, a revoked
    source, an empty material and a finished material are four different answers.
    """
    situation: dict[str, Any] = {
        "reading_artifact_id": (str(reading_artifact_id) if reading_artifact_id else None),
        "private_items_artifact_id": (str(private_items_artifact_id)
                                      if private_items_artifact_id else None),
        "source_id": _source_id(reading_artifact_id or ""),
        "state": STATE_READING_NOT_BOUND,
        "reason_code": STATE_READING_NOT_BOUND,
        "detail": "",
        "available": False,
        "exhausted": False,
        "understanding": UNDERSTANDING_UNKNOWN,
        "material": dict(_EMPTY_MATERIAL),
        "position": dict(_EMPTY_POSITION),
        "private_items": dict(_EMPTY_PRIVATE_ITEMS),
        "annotations": {"note": 0, "unclear": 0, "stale_note": 0, "stale_unclear": 0},
    }
    if not reading_artifact_id or not private_items_artifact_id or port is None:
        return situation

    reading_id = str(reading_artifact_id)
    doc_id = str(private_items_artifact_id)
    source_id = situation["source_id"]

    # -- the material itself: the owner's current head ------------------------------------
    try:
        view = dict(port.read_artifact(reading_id) or {})
    except LifeArtifactUnavailable as exc:
        situation.update({"state": STATE_OWNER_UNAVAILABLE,
                          "reason_code": STATE_OWNER_UNAVAILABLE,
                          "detail": str(exc)[:200],
                          "material": {**_EMPTY_MATERIAL, "error": str(exc)[:200]}})
        return situation
    except ArtifactOwnerRefused as exc:
        state = (STATE_OWNER_REFUSED if str(exc.status) != "NOT_FOUND"
                 else STATE_MATERIAL_UNCOMMITTED)
        situation.update({"state": state,
                          "reason_code": "%s:%s" % (state, exc.status),
                          "detail": f"{exc.reason}:{exc.detail}"[:200],
                          "material": {**_EMPTY_MATERIAL,
                                       "error": f"{exc.reason}:{exc.detail}"[:200]}})
        return situation

    artifact = dict(view.get("artifact") or {})
    head = dict(view.get("head") or {})
    content = view.get("content")
    lifecycle = str(artifact.get("lifecycle") or "") or None
    version_id = head.get("version_id")
    situation["material"] = {
        "available": True,
        "lifecycle": lifecycle,
        "version_id": None if version_id is None else str(version_id),
        "version_number": head.get("version_number"),
        "pages": None,
        "content_chars": (len(content) if isinstance(content, str) else None),
        "title": str(artifact.get("title") or "")[:200] or None,
        "error": "",
    }
    if lifecycle is not None and lifecycle != "ACTIVE":
        situation.update({"state": STATE_MATERIAL_NOT_ACTIVE,
                          "reason_code": STATE_MATERIAL_NOT_ACTIVE,
                          "detail": "MATERIAL_LIFECYCLE:%s" % lifecycle})
        return situation
    if not version_id:
        situation.update({"state": STATE_MATERIAL_UNCOMMITTED,
                          "reason_code": STATE_MATERIAL_UNCOMMITTED,
                          "detail": "MATERIAL_HAS_NO_COMMITTED_VERSION"})
        return situation
    pages = _split_pages(content, page_marker)
    if not pages or not str(content or "").strip():
        situation.update({"state": STATE_MATERIAL_EMPTY,
                          "reason_code": STATE_MATERIAL_EMPTY,
                          "detail": "MATERIAL_HAS_NO_TEXT",
                          "material": {**situation["material"], "pages": 0}})
        return situation
    revision = str(version_id)
    situation["material"]["pages"] = len(pages)

    # -- the private-items document: the only place a stop point may live ----------------
    try:
        items = pdi.PrivateDailyItems(port, doc_id)
        document = items.load()
        rows = items.items()
    except LifeArtifactUnavailable as exc:
        situation.update({"state": STATE_OWNER_UNAVAILABLE,
                          "reason_code": STATE_OWNER_UNAVAILABLE,
                          "detail": str(exc)[:200],
                          "private_items": {**_EMPTY_PRIVATE_ITEMS, "error": str(exc)[:200]}})
        return situation
    except Exception as exc:  # contract refusal, version mismatch, decode failure, refusal
        detail = f"{type(exc).__name__}: {exc}"[:200]
        situation.update({"state": STATE_PRIVATE_ITEMS_UNAVAILABLE,
                          "reason_code": STATE_PRIVATE_ITEMS_UNAVAILABLE,
                          "detail": detail,
                          "private_items": {**_EMPTY_PRIVATE_ITEMS, "error": detail}})
        return situation

    situation["private_items"] = {
        "ok": True,
        "owner_version_number": document.get("owner_version_number"),
        "doc_revision": document.get("doc_revision"),
        "error": "",
    }

    by_id = {str(row.get("item_id")): row for row in rows}
    positions = [row for row in rows
                 if str(row.get("item_type")) == pdi.ITEM_READING_POSITION
                 and str(row.get("source_id")) == source_id]
    acquisitions = [row for row in rows
                    if str(row.get("item_type")) == pdi.ITEM_SOURCE_ACQUISITION
                    and str(row.get("source_id")) == source_id]

    # A stop is valid only when *all five* hold: it is about this source, the entry it points at
    # is an acquisition **of this same source**, that acquisition is on the owner's **current**
    # revision, it really exists, and it was not revoked.  Anything less is a situation to
    # report, not a number to reuse.  (The old revision's highest page therefore cannot drive
    # a new revision: only a stop recorded on the current revision can.  A position pointing at
    # another source's acquisition -- however well-formed the document is -- is not a stop of
    # *this* material either.)
    valid: list[tuple[str, int, dict[str, Any]]] = []
    prior_pages: set[int] = set()
    for index, row in enumerate(positions):
        row_revision = str(row.get("source_revision") or "")
        position = row.get("position")
        if row_revision != revision:
            if _is_page(position):
                prior_pages.add(int(position))
            continue
        acquisition = by_id.get(str(row.get("acquisition_id") or ""))
        if acquisition is None:
            continue
        if str(acquisition.get("item_type")) != pdi.ITEM_SOURCE_ACQUISITION:
            continue
        if str(acquisition.get("source_id") or "") != source_id:
            continue
        if str(acquisition.get("source_revision") or "") != revision:
            continue
        if not acquisition.get("available", True):
            continue
        valid.append((str(row.get("set_at") or ""), index, row))
    valid.sort(key=lambda entry: (entry[0], entry[1]))
    latest = valid[-1][2] if valid else None

    current_acquisitions = [row for row in acquisitions
                            if str(row.get("source_revision") or "") == revision]
    revoked = bool(current_acquisitions) and not any(
        row.get("available", True) for row in current_acquisitions)

    # ``needs_reread`` is a fact about the stop that is actually used, not about history: when a
    # valid stop on the *current* revision exists, only that stop's own record decides (a fresh
    # current stop is normally ``False``).  The old revision's pages stay reported in
    # ``prior_revision_pages`` and old-anchored notes stay counted in ``stale_*`` -- they are
    # facts, they just cannot make a current stop stale for ever.  Only when no current stop
    # exists does an old revision's record mean "this still has to be re-read".
    stale_rows = [row for row in positions if row.get("needs_reread")]
    if latest is not None:
        needs_reread = bool(latest.get("needs_reread"))
        reason = ("" if not needs_reread
                  else str(latest.get("needs_reread_reason") or "") or "NEEDS_REREAD")
    else:
        needs_reread = bool(prior_pages) or bool(stale_rows)
        if prior_pages:
            reason = "SOURCE_REVISION_ADVANCED"
        elif needs_reread:
            reason = next((str(row.get("needs_reread_reason") or "") for row in stale_rows),
                          "") or "NEEDS_REREAD"
        else:
            reason = ""

    page: Optional[int] = None
    position_kind = None
    if latest is not None:
        raw_position = latest.get("position")
        if _is_page(raw_position):
            page, position_kind = int(raw_position), "PAGE"
        else:
            position_kind = "LOCATOR"
    situation["position"] = {
        "page": page,
        "next_page": None,
        "position_kind": position_kind,
        "set_at": (None if latest is None else latest.get("set_at")),
        "position_id": (None if latest is None else latest.get("item_id")),
        "acquisition_id": (None if latest is None else latest.get("acquisition_id")),
        "on_current_revision": page is not None,
        "needs_reread": needs_reread,
        "needs_reread_reason": reason,
        "prior_revision_pages": sorted(prior_pages),
    }

    # -- notes and questions beside the material: counts only, never their text ----------
    note_rows = [row for row in rows
                 if str(row.get("item_type")) == pdi.ITEM_MEMO
                 and isinstance(row.get("reading_anchor"), Mapping)
                 and str((row.get("reading_anchor") or {}).get("source_id")) == source_id]
    unclear_rows = [row for row in rows
                    if str(row.get("item_type")) == pdi.ITEM_UNCLEAR_MARKER
                    and str(row.get("source_id")) == source_id]
    situation["annotations"] = {
        "note": len(note_rows),
        "unclear": len(unclear_rows),
        "stale_note": sum(1 for row in note_rows
                          if str((row.get("reading_anchor") or {}).get("source_revision"))
                          != revision),
        "stale_unclear": sum(1 for row in unclear_rows
                             if str(row.get("source_revision")) != revision),
    }

    # -- the one question a planner asks: is there a next page, and which one? ------------
    page_count = int(len(pages))
    if revoked:
        state, detail = STATE_SOURCE_REVOKED, "SOURCE_UNAVAILABLE_ON_CURRENT_REVISION"
    elif latest is None:
        # No valid stop on this revision: reading starts at page 1 (an explicit earlier stop
        # on a *previous* revision is a situation, not a page number to carry over).
        state, detail = STATE_AVAILABLE, "NO_STOP_ON_CURRENT_REVISION"
        situation["position"]["next_page"] = 1
    elif position_kind != "PAGE":
        state, detail = STATE_POSITION_NOT_A_PAGE, "STOP_IS_A_LOCATOR"
    elif page is not None and page > page_count:
        state, detail = STATE_POSITION_OUT_OF_RANGE, "STOP_PAGE_%d>PAGES_%d" % (page, page_count)
    elif page is not None and page >= page_count:
        state, detail = STATE_EXHAUSTED, "LAST_PAGE_%d_OF_%d" % (page, page_count)
    else:
        state, detail = STATE_AVAILABLE, "NEXT_PAGE_%d_OF_%d" % (int(page) + 1, page_count)
        situation["position"]["next_page"] = int(page) + 1

    situation.update({
        "state": state,
        "reason_code": state,
        "detail": detail,
        "available": state == STATE_AVAILABLE,
        "exhausted": state == STATE_EXHAUSTED,
        "understanding": UNDERSTANDING_UNKNOWN,
    })
    return situation


def reading_fact_keys(situation: Optional[Mapping[str, Any]],
                      *, limit: int = MAX_READING_FACT_KEYS) -> dict[str, Any]:
    """The bounded ASCII fact keys for one situation (keys carry the numbers, not refs).

    Ordering is priority: the state first, then the page that matters for the next step,
    then the rest.  A key that would exceed the prompt's own bound is dropped rather than
    truncated into something that reads like a different fact.
    """
    if not isinstance(situation, Mapping):
        return {}
    material = situation.get("material") if isinstance(situation.get("material"), Mapping) else {}
    position = situation.get("position") if isinstance(situation.get("position"), Mapping) else {}
    annotations = (situation.get("annotations")
                   if isinstance(situation.get("annotations"), Mapping) else {})
    state = str(situation.get("state") or "UNKNOWN")
    facts: dict[str, Any] = {}

    def put(key: str, value: Any) -> None:
        if len(key) > MAX_FACT_KEY_CHARS or len(facts) >= int(limit):
            return
        if not key.isascii() or not key.replace("_", "").isalnum():
            return
        facts[key] = value

    put("reading_state_%s" % _token(state), state)
    if _is_page(position.get("page")):
        put("reading_page_%d" % int(position["page"]), int(position["page"]))
    if _is_page(position.get("next_page")):
        put("reading_next_page_%d" % int(position["next_page"]), int(position["next_page"]))
    if _is_page(material.get("pages")):
        put("reading_pages_%d" % int(material["pages"]), int(material["pages"]))
    put("reading_exhausted_%s" % ("true" if situation.get("exhausted") else "false"),
        bool(situation.get("exhausted")))
    put("reading_needs_reread_%s" % ("true" if position.get("needs_reread") else "false"),
        bool(position.get("needs_reread")))
    note_count = annotations.get("note")
    if _is_page(note_count) and int(note_count) > 0:
        put("reading_notes_%d" % int(note_count), int(note_count))
    unclear_count = annotations.get("unclear")
    if _is_page(unclear_count) and int(unclear_count) > 0:
        put("reading_unclear_%d" % int(unclear_count), int(unclear_count))
    return facts


def merge_decision_facts(
    world_facts: Optional[Mapping[str, Any]],
    reading_facts: Optional[Mapping[str, Any]],
    *,
    max_keys: int = MAX_FACT_KEYS,
) -> dict[str, Any]:
    """One bounded ``observed_facts`` map: the private situation first, the World beside it.

    The prompt keeps at most 24 keys.  Merging them here -- instead of letting the two sets
    race for that budget after a sort -- means a place dump can never crowd out the fact that
    she has a page to read, and a reading round can never hide the World's own verdict.  Keys
    that cannot survive the prompt's own projection are dropped, never truncated.
    """
    merged: dict[str, Any] = {}

    def add(source: Optional[Mapping[str, Any]]) -> None:
        if not isinstance(source, Mapping):
            return
        for key, value in source.items():
            if len(merged) >= int(max_keys):
                return
            text = str(key)
            if not text or len(text) > MAX_FACT_KEY_CHARS:
                continue
            if not text.isascii() or not text.replace("_", "").isalnum():
                continue
            if text in merged:
                continue
            merged[text] = value

    add(reading_facts)
    add(world_facts)
    return merged


__all__ = [
    "PAGE_MARKER",
    "UNDERSTANDING_UNKNOWN",
    "MAX_FACT_KEYS",
    "MAX_FACT_KEY_CHARS",
    "MAX_READING_FACT_KEYS",
    "STATE_AVAILABLE",
    "STATE_EXHAUSTED",
    "STATE_READING_NOT_BOUND",
    "STATE_MATERIAL_UNCOMMITTED",
    "STATE_MATERIAL_NOT_ACTIVE",
    "STATE_MATERIAL_EMPTY",
    "STATE_POSITION_NOT_A_PAGE",
    "STATE_POSITION_OUT_OF_RANGE",
    "STATE_SOURCE_REVOKED",
    "STATE_PRIVATE_ITEMS_UNAVAILABLE",
    "STATE_OWNER_UNAVAILABLE",
    "STATE_OWNER_REFUSED",
    "read_reading_situation",
    "reading_fact_keys",
    "merge_decision_facts",
]
