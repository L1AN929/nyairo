#!/usr/bin/env python3
"""AFFECT/SHORT-APPRAISAL-1: a minimal, sourced, bounded short-term appraisal read.

Status of this protocol (read before wiring)
--------------------------------------------
There is **no S07 affect owner** in the tree: the Affect reconnaissance found no
owner for stable affect (``CHIYO-施工规划/05-身体与情绪现状勘察.md`` §2), and the
design says a short appraisal is light, may fade and does not need a full
psychological engine (15:147).  So this module is deliberately the smallest thing
that can exist honestly:

* **experimental, rebuildable projection** — it takes appraisals the caller
  already has and projects them; it holds no store, writes no file, keeps no
  cache and never mints a second authority (``persistent_store: null``,
  ``canonical: false``, ``rebuildable: true``);
* **bounded** — every appraisal must declare ``observed_at`` *and*
  ``valid_until``; an appraisal with no declared expiry is refused
  (``APPRAISAL_VALID_UNTIL_MISSING``), because a short-term appraisal that cannot
  expire is a permanent claim in disguise;
* **sourced** — every appraisal needs at least one structured ``source_ref``; a
  correction is a ref with its own targets, so an anonymous "just correct it"
  cannot silently delete an appraisal;
* **no valid appraisal after loss, expiry or correction** — a lost source, a
  passed ``valid_until``, or a valid correction targeting the appraisal (or one of
  its sources) leaves *no* valid appraisal for that ref, and the projection says
  so with one reason code;
* **repeated sources do not stack** — intensity is taken verbatim from the
  source, kept once per source ref, and never summed, averaged or ranked
  (``intensity_aggregation: "none"``); two different declared values for the same
  source are reported as a conflict and the intensity is withheld instead of
  being resolved by this module;
* **no self-signed stable facts** — the projection asserts no stable emotion, no
  relationship fact, no preference fact and no body fact; keys that would carry
  one are refused (``APPRAISAL_STABLE_FACT_REFUSED`` /
  ``APPRAISAL_BODY_FACT_REFUSED``);
* **no tone inference** — there is no text, transcript or message input at all,
  and any item carrying ``tone`` / ``sentiment`` / ``mood`` / ``emotion`` /
  ``text`` / ``reply`` ... is refused (``APPRAISAL_TONE_INFERENCE_REFUSED``).
  Nothing here reads a sentence to guess a feeling.

Zero authority
--------------
``writes_performed`` / ``mutations_attempted`` are constants ``0``,
``candidates_emitted`` / ``actions_authorized`` are ``0``, ``reads_performed`` is
``0`` (the projection only consumes what the caller passes in), and the module
imports no Candidate source and calls no AG-0/AG-1 code.

Raises versus refused items
---------------------------
``ShortAppraisalViewError`` is raised only for a caller/wiring fault: an illegal
subject, a home that is not a directory, an unparseable reference instant.
Everything an appraisal or a correction can get wrong is a per-item reason code.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

import body_capacity_source as body_capacity  # noqa: E402

__all__ = [
    "APPRAISAL_STATUS_INVALID",
    "APPRAISAL_STATUS_VALID",
    "FORBIDDEN_APPRAISAL_FIELDS",
    "KIND_SHORT_APPRAISAL_VIEW",
    "REASON_APPRAISAL_BODY_FACT_REFUSED",
    "REASON_APPRAISAL_CORRECTED",
    "REASON_APPRAISAL_DUPLICATE_REF",
    "REASON_APPRAISAL_EXPIRED",
    "REASON_APPRAISAL_NOT_MAPPING",
    "REASON_APPRAISAL_NO_REFERENCE_INSTANT",
    "REASON_APPRAISAL_OBSERVED_AT_AHEAD",
    "REASON_APPRAISAL_OBSERVED_AT_INVALID",
    "REASON_APPRAISAL_OBSERVED_AT_MISSING",
    "REASON_APPRAISAL_REF_MISSING",
    "REASON_APPRAISAL_SOURCE_LOST",
    "REASON_APPRAISAL_SOURCE_REFS_MISSING",
    "REASON_APPRAISAL_STABLE_FACT_REFUSED",
    "REASON_APPRAISAL_SUBJECT_MISMATCH",
    "REASON_APPRAISAL_TONE_INFERENCE_REFUSED",
    "REASON_APPRAISAL_VALID_UNTIL_INVALID",
    "REASON_APPRAISAL_VALID_UNTIL_MISSING",
    "REASON_APPRAISAL_VALIDITY_INVERTED",
    "REASON_CORRECTION_REF_MISSING",
    "REASON_CORRECTION_TARGETS_MISSING",
    "REASON_NO_APPRAISAL_INPUT",
    "REASON_OK_SOURCED_APPRAISAL",
    "SCHEMA_SHORT_APPRAISAL_VIEW",
    "ShortAppraisalView",
    "ShortAppraisalViewError",
    "build_short_appraisal_view",
    "valid_appraisals",
]

SCHEMA_SHORT_APPRAISAL_VIEW = "chiyo.life.short_appraisal_view.v1"
KIND_SHORT_APPRAISAL_VIEW = "short_appraisal_view"

APPRAISAL_STATUS_VALID = "VALID"
APPRAISAL_STATUS_INVALID = "INVALID"
VALID_APPRAISAL_STATUSES = frozenset({APPRAISAL_STATUS_VALID, APPRAISAL_STATUS_INVALID})

#: This module's own closed reason vocabulary.  There is no S07 owner, so these
#: are projection labels and are never presented as an owner's assertion.
REASON_OK_SOURCED_APPRAISAL = "OK_SOURCED_BOUNDED_APPRAISAL"
REASON_APPRAISAL_NOT_MAPPING = "APPRAISAL_NOT_A_MAPPING"
REASON_APPRAISAL_REF_MISSING = "APPRAISAL_REF_MISSING"
REASON_APPRAISAL_DUPLICATE_REF = "APPRAISAL_DUPLICATE_REF"
REASON_APPRAISAL_TONE_INFERENCE_REFUSED = "APPRAISAL_TONE_INFERENCE_REFUSED"
REASON_APPRAISAL_STABLE_FACT_REFUSED = "APPRAISAL_STABLE_FACT_REFUSED"
REASON_APPRAISAL_BODY_FACT_REFUSED = "APPRAISAL_BODY_FACT_REFUSED"
REASON_APPRAISAL_SOURCE_REFS_MISSING = "APPRAISAL_SOURCE_REFS_MISSING"
REASON_APPRAISAL_SUBJECT_MISMATCH = "APPRAISAL_SUBJECT_MISMATCH"
REASON_APPRAISAL_OBSERVED_AT_MISSING = "APPRAISAL_OBSERVED_AT_MISSING"
REASON_APPRAISAL_OBSERVED_AT_INVALID = "APPRAISAL_OBSERVED_AT_INVALID"
REASON_APPRAISAL_OBSERVED_AT_AHEAD = "APPRAISAL_OBSERVED_AT_AHEAD"
REASON_APPRAISAL_VALID_UNTIL_MISSING = "APPRAISAL_VALID_UNTIL_MISSING"
REASON_APPRAISAL_VALID_UNTIL_INVALID = "APPRAISAL_VALID_UNTIL_INVALID"
REASON_APPRAISAL_VALIDITY_INVERTED = "APPRAISAL_VALIDITY_INVERTED"
REASON_APPRAISAL_NO_REFERENCE_INSTANT = "APPRAISAL_NO_REFERENCE_INSTANT"
REASON_APPRAISAL_EXPIRED = "APPRAISAL_EXPIRED"
REASON_APPRAISAL_CORRECTED = "APPRAISAL_CORRECTED"
REASON_APPRAISAL_SOURCE_LOST = "APPRAISAL_SOURCE_LOST"
REASON_CORRECTION_REF_MISSING = "CORRECTION_REF_MISSING"
REASON_CORRECTION_TARGETS_MISSING = "CORRECTION_TARGETS_MISSING"
REASON_NO_APPRAISAL_INPUT = "NO_APPRAISAL_INPUT"

#: Keys that would turn a projection into a tone filter: refused outright.  There
#: is no text input in this module's surface at all (15:147 "不是末尾语气滤镜").
TONE_INFERENCE_FIELDS = frozenset(
    {
        "tone",
        "sentiment",
        "mood",
        "emotion",
        "affect",
        "feeling",
        "text",
        "message",
        "transcript",
        "utterance",
        "reply",
        "words",
    }
)
#: Keys that would sign a stable fact (emotion / relationship / preference).
STABLE_FACT_FIELDS = frozenset(
    {
        "stable_emotion",
        "relationship",
        "relationship_state",
        "preference",
        "preference_claim",
        "personality",
        "self_belief",
        "identity",
    }
)
#: Keys that would write Body truth through an appraisal.
BODY_FACT_FIELDS = frozenset(
    {"body_capacity_state", "body_capacity_ref", "fatigue", "fatigue_load", "health"}
)

FORBIDDEN_APPRAISAL_FIELDS = (
    TONE_INFERENCE_FIELDS | STABLE_FACT_FIELDS | BODY_FACT_FIELDS
)

#: Echo-only item fields: copied through verbatim and never interpreted.
ECHO_APPRAISAL_FIELDS = ("declared_scope", "uncertainty")

#: The one reason this module is allowed to say "valid".
_UNCERTAINTY_UNKNOWN = "UNKNOWN"


class ShortAppraisalViewError(ValueError):
    """A caller/wiring fault, not an appraisal the caller's sources produced."""


# --------------------------------------------------------------------------
# the shared rules (owned by body_capacity_source) with this module's error type
# --------------------------------------------------------------------------


def _instant(value: Any, label: str) -> datetime:
    try:
        return body_capacity.require_instant(value, label)
    except body_capacity.BodyCapacitySourceError as error:
        raise ShortAppraisalViewError(str(error)) from error


def _subject(subject_id: Optional[str]) -> Optional[str]:
    try:
        return body_capacity.resolve_subject_id(subject_id)
    except body_capacity.BodyCapacitySourceError as error:
        raise ShortAppraisalViewError(str(error)) from error


def _home_ref(home: Any) -> str:
    try:
        return body_capacity.bind_home_ref(home)
    except body_capacity.BodyCapacitySourceError as error:
        raise ShortAppraisalViewError(str(error)) from error


# --------------------------------------------------------------------------
# corrections
# --------------------------------------------------------------------------


def _corrections(corrections: Sequence[Any]) -> tuple[dict[str, Any], set, list]:
    """Read the caller's corrections: which refs are corrected, by which ref.

    A correction is honoured only when it carries its own structured
    ``correction_ref`` *and* a non-empty ``corrects_refs``.  An anonymous or
    target-less correction is refused and reported, so nothing can erase an
    appraisal without a source of its own.
    """

    accepted: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    corrected: set = set()

    for raw in corrections or ():
        entry = {
            "correction_ref": None,
            "corrects_refs": [],
            "source_ref": None,
            "observed_at": None,
            "reason_code": None,
        }
        if not isinstance(raw, Mapping):
            entry["reason_code"] = REASON_CORRECTION_REF_MISSING
            refused.append(entry)
            continue
        ref = raw.get("correction_ref")
        if isinstance(ref, str) and ref.strip():
            entry["correction_ref"] = ref.strip()
        source_ref = raw.get("source_ref")
        if isinstance(source_ref, str) and source_ref.strip():
            entry["source_ref"] = source_ref.strip()
        observed_at = raw.get("observed_at")
        if isinstance(observed_at, str) and observed_at.strip():
            entry["observed_at"] = observed_at.strip()
        targets = raw.get("corrects_refs")
        targets = [t.strip() for t in targets if isinstance(t, str) and t.strip()] \
            if isinstance(targets, (list, tuple)) else []

        if entry["correction_ref"] is None:
            entry["reason_code"] = REASON_CORRECTION_REF_MISSING
            refused.append(entry)
            continue
        try:
            body_capacity.require_structured_ref(entry["correction_ref"], "correction_ref")
        except body_capacity.BodyCapacitySourceError:
            entry["reason_code"] = REASON_CORRECTION_REF_MISSING
            refused.append(entry)
            continue
        if not targets:
            entry["reason_code"] = REASON_CORRECTION_TARGETS_MISSING
            refused.append(entry)
            continue
        entry["corrects_refs"] = sorted(set(targets))
        corrected.update(targets)
        accepted.append(entry)

    return {"accepted": accepted, "refused": refused}, corrected, accepted


# --------------------------------------------------------------------------
# one appraisal
# --------------------------------------------------------------------------


def _empty_item() -> dict[str, Any]:
    return {
        "appraisal_ref": None,
        "subject_id": None,
        "source_refs": [],
        "duplicate_source_refs": [],
        "observed_at": None,
        "valid_until": None,
        "declared_scope": None,
        "uncertainty": None,
        "uncertainty_source": _UNCERTAINTY_UNKNOWN,
        "intensity": None,
        "correction_refs": [],
        "corrected_source_refs": [],
        "lost_source_refs": [],
        "status": APPRAISAL_STATUS_INVALID,
        "reason_code": REASON_APPRAISAL_NOT_MAPPING,
    }


def _appraisal_item(
    raw: Any,
    *,
    now: Optional[str],
    subject_id: Optional[str],
    seen: set,
    corrected_refs: set,
    lost_sources: set,
) -> dict[str, Any]:
    """One appraisal, classified.  First failure wins; fail closed."""

    item = _empty_item()
    if isinstance(raw, Mapping):
        for field in ECHO_APPRAISAL_FIELDS:
            if isinstance(raw.get(field), str):
                item[field] = raw[field]
                if field == "uncertainty":
                    item["uncertainty_source"] = "declared"

    if not isinstance(raw, Mapping):
        return item

    ref = raw.get("appraisal_ref")
    if not isinstance(ref, str) or not ref.strip():
        item["reason_code"] = REASON_APPRAISAL_REF_MISSING
        return item
    item["appraisal_ref"] = ref.strip()
    try:
        body_capacity.require_structured_ref(item["appraisal_ref"], "appraisal_ref")
    except body_capacity.BodyCapacitySourceError:
        item["reason_code"] = REASON_APPRAISAL_REF_MISSING
        return item

    if item["appraisal_ref"] in seen:
        item["reason_code"] = REASON_APPRAISAL_DUPLICATE_REF
        return item
    seen.add(item["appraisal_ref"])

    for fields, reason in (
        (TONE_INFERENCE_FIELDS, REASON_APPRAISAL_TONE_INFERENCE_REFUSED),
        (STABLE_FACT_FIELDS, REASON_APPRAISAL_STABLE_FACT_REFUSED),
        (BODY_FACT_FIELDS, REASON_APPRAISAL_BODY_FACT_REFUSED),
    ):
        leaked = sorted(key for key in raw if key in fields)
        if leaked:
            item["reason_code"] = reason
            item["refused_fields"] = leaked
            return item

    raw_subject = raw.get("subject_id")
    if raw_subject is not None:
        raw_subject = raw_subject.strip() if isinstance(raw_subject, str) else raw_subject
        if subject_id is None or raw_subject != subject_id:
            item["subject_id"] = raw_subject if isinstance(raw_subject, str) else None
            item["reason_code"] = REASON_APPRAISAL_SUBJECT_MISMATCH
            return item
    # every item that got this far belongs to the one verified subject, whether or
    # not it declared the subject itself
    item["subject_id"] = subject_id

    declared_sources = raw.get("source_refs")
    if not isinstance(declared_sources, (list, tuple)):
        item["reason_code"] = REASON_APPRAISAL_SOURCE_REFS_MISSING
        return item
    sources: list[str] = []
    for candidate in declared_sources:
        if not isinstance(candidate, str) or not candidate.strip():
            item["reason_code"] = REASON_APPRAISAL_SOURCE_REFS_MISSING
            return item
        try:
            sources.append(body_capacity.require_structured_ref(candidate, "source_refs"))
        except body_capacity.BodyCapacitySourceError:
            item["reason_code"] = REASON_APPRAISAL_SOURCE_REFS_MISSING
            return item
    if not sources:
        item["reason_code"] = REASON_APPRAISAL_SOURCE_REFS_MISSING
        return item
    item["source_refs"] = sorted(set(sources))
    item["duplicate_source_refs"] = sorted(
        {ref for ref in sources if sources.count(ref) > 1}
    )

    observed_instant = body_capacity.parse_instant(raw.get("observed_at"))
    raw_observed = raw.get("observed_at")
    if raw_observed is None or (isinstance(raw_observed, str) and not raw_observed.strip()):
        item["reason_code"] = REASON_APPRAISAL_OBSERVED_AT_MISSING
        return item
    if observed_instant is None:
        item["reason_code"] = REASON_APPRAISAL_OBSERVED_AT_INVALID
        return item
    item["observed_at"] = observed_instant.isoformat()

    declared_until = raw.get("valid_until")
    if declared_until is None or (
        isinstance(declared_until, str) and not declared_until.strip()
    ):
        item["reason_code"] = REASON_APPRAISAL_VALID_UNTIL_MISSING
        return item
    valid_until = body_capacity.parse_instant(declared_until)
    if valid_until is None:
        item["reason_code"] = REASON_APPRAISAL_VALID_UNTIL_INVALID
        return item
    item["valid_until"] = valid_until.isoformat()
    if valid_until <= observed_instant:
        item["reason_code"] = REASON_APPRAISAL_VALIDITY_INVERTED
        return item

    declared_corrections = raw.get("correction_refs")
    if isinstance(declared_corrections, (list, tuple)):
        item["correction_refs"] = sorted(
            c.strip() for c in declared_corrections if isinstance(c, str) and c.strip()
        )
    corrected_targets = [source for source in item["source_refs"] if source in corrected_refs]
    if item["correction_refs"] or item["appraisal_ref"] in corrected_refs or corrected_targets:
        if corrected_targets:
            item["corrected_source_refs"] = corrected_targets
        item["reason_code"] = REASON_APPRAISAL_CORRECTED
        return item

    lost = sorted(source for source in item["source_refs"] if source in lost_sources)
    if lost:
        item["lost_source_refs"] = lost
        item["reason_code"] = REASON_APPRAISAL_SOURCE_LOST
        return item

    intensity = raw.get("intensity")
    if isinstance(intensity, (int, float, str)) and not isinstance(intensity, bool):
        item["intensity"] = intensity

    reference = body_capacity.parse_instant(now)
    if reference is None:
        # Without an injected reference instant, "still valid?" cannot be decided:
        # that is not a valid appraisal, and it is not expiry either.
        item["reason_code"] = REASON_APPRAISAL_NO_REFERENCE_INSTANT
        return item
    if observed_instant > reference:
        item["reason_code"] = REASON_APPRAISAL_OBSERVED_AT_AHEAD
        return item
    if reference >= valid_until:
        item["reason_code"] = REASON_APPRAISAL_EXPIRED
        return item

    item["status"] = APPRAISAL_STATUS_VALID
    item["reason_code"] = REASON_OK_SOURCED_APPRAISAL
    return item


# --------------------------------------------------------------------------
# the projection
# --------------------------------------------------------------------------


def _source_intensity(items: Sequence[Mapping[str, Any]]) -> tuple[dict, list]:
    """Intensity per source ref, taken once and never combined.

    Two appraisals citing the same source with different declared values is a
    conflict: the projection withholds that source's intensity instead of
    picking, summing or averaging (repeated sources must not stack).
    """

    declared: dict[str, Any] = {}
    conflicts: set = set()
    for item in items:
        if item.get("intensity") is None:
            continue
        for source in item.get("source_refs") or ():
            if source not in declared:
                declared[source] = item["intensity"]
            elif declared[source] != item["intensity"]:
                conflicts.add(source)
    for source in sorted(conflicts):
        declared.pop(source, None)
    return declared, sorted(conflicts)


def build_short_appraisal_view(
    *,
    home: Any,
    subject_id: Optional[str] = None,
    now: Any = None,
    appraisals: Sequence[Any] = (),
    corrections: Sequence[Any] = (),
    lost_source_refs: Sequence[Any] = (),
) -> dict[str, Any]:
    """Project the appraisals the caller already has into a bounded read view.

    ``appraisals`` are mappings with ``appraisal_ref``, ``source_refs``,
    ``observed_at`` and ``valid_until`` (``uncertainty`` / ``declared_scope`` /
    ``intensity`` / ``correction_refs`` optional).  ``corrections`` carry
    ``correction_ref`` + ``corrects_refs``.  ``lost_source_refs`` are sources the
    caller can no longer read.  ``now`` is injected by the caller.
    """

    reference = None if now is None else _instant(now, "now")
    reference_iso = reference.isoformat() if reference is not None else None
    resolved_subject = _subject(subject_id)

    try:
        home_ref = body_capacity.bind_home_ref(home)
    except body_capacity.BodyCapacitySourceError:
        return _identity_unbound_view(home_ref=None, reason=body_capacity.REASON_HOME_UNBOUND)
    if resolved_subject is None:
        return _identity_unbound_view(
            home_ref=home_ref, reason=body_capacity.REASON_SUBJECT_OWNER_UNAVAILABLE
        )

    corrections_view, corrected_refs, accepted_corrections = _corrections(corrections)

    lost: set = set()
    for raw in lost_source_refs or ():
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            lost.add(body_capacity.require_structured_ref(raw, "lost_source_refs"))
        except body_capacity.BodyCapacitySourceError:
            continue

    seen: set = set()
    items = [
        _appraisal_item(
            raw,
            now=reference_iso,
            subject_id=resolved_subject,
            seen=seen,
            corrected_refs=corrected_refs,
            lost_sources=lost,
        )
        for raw in appraisals or ()
    ]

    valid_refs = [
        item["appraisal_ref"] for item in items if item["status"] == APPRAISAL_STATUS_VALID
    ]
    invalid = [
        {"appraisal_ref": item["appraisal_ref"], "reason_code": item["reason_code"]}
        for item in items
        if item["status"] != APPRAISAL_STATUS_VALID
    ]
    intensity, conflicts = _source_intensity(items)

    if valid_refs:
        empty_reason = None
    elif not items:
        empty_reason = REASON_NO_APPRAISAL_INPUT
    else:
        empty_reason = invalid[0]["reason_code"]

    return {
        "schema_version": SCHEMA_SHORT_APPRAISAL_VIEW,
        "kind": KIND_SHORT_APPRAISAL_VIEW,
        "subject_id": resolved_subject,
        "home_ref": home_ref,
        "observed_at": reference_iso,
        "identity_bound": True,
        "identity_reason_code": None,
        "source_revisions": {
            "home": home_ref,
            "appraisals": sorted(ref for ref in seen),
            "corrections": sorted(c["correction_ref"] for c in accepted_corrections),
        },
        "appraisals": items,
        "valid_appraisal_refs": valid_refs,
        "invalid_appraisals": invalid,
        "has_valid_appraisal": bool(valid_refs),
        "empty_reason_code": empty_reason,
        "corrections": corrections_view["accepted"],
        "invalid_corrections": corrections_view["refused"],
        "lost_source_refs": sorted(lost),
        "source_intensity": intensity,
        "conflicting_source_refs": conflicts,
        "intensity_aggregation": "none",
        "protocol": {
            "kind": "experimental_rebuildable_projection",
            "canonical": False,
            "persistent_store": None,
            "cache_held": False,
            "rebuildable": True,
            "s07_owner": None,
            "requires_declared_valid_until": True,
            "requires_source_refs": True,
            "stable_emotion_asserted": False,
            "relationship_fact_asserted": False,
            "preference_fact_asserted": False,
            "body_fact_asserted": False,
            "tone_inference_used": False,
            "intensity_aggregation": "none",
        },
        "read_only": True,
        "reads_performed": 0,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
        "actions_authorized": 0,
    }


def _identity_unbound_view(*, home_ref: Optional[str], reason: str) -> dict[str, Any]:
    """The one-subject rule could not be resolved: nothing can be attributed."""

    return {
        "schema_version": SCHEMA_SHORT_APPRAISAL_VIEW,
        "kind": KIND_SHORT_APPRAISAL_VIEW,
        "subject_id": None,
        "home_ref": home_ref,
        "observed_at": None,
        "identity_bound": False,
        "identity_reason_code": reason,
        "source_revisions": {"home": home_ref, "appraisals": [], "corrections": []},
        "appraisals": [],
        "valid_appraisal_refs": [],
        "invalid_appraisals": [],
        "has_valid_appraisal": False,
        "empty_reason_code": reason,
        "corrections": [],
        "invalid_corrections": [],
        "lost_source_refs": [],
        "source_intensity": {},
        "conflicting_source_refs": [],
        "intensity_aggregation": "none",
        "protocol": {
            "kind": "experimental_rebuildable_projection",
            "canonical": False,
            "persistent_store": None,
            "cache_held": False,
            "rebuildable": True,
            "s07_owner": None,
            "requires_declared_valid_until": True,
            "requires_source_refs": True,
            "stable_emotion_asserted": False,
            "relationship_fact_asserted": False,
            "preference_fact_asserted": False,
            "body_fact_asserted": False,
            "tone_inference_used": False,
            "intensity_aggregation": "none",
        },
        "read_only": True,
        "reads_performed": 0,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
        "actions_authorized": 0,
    }


def valid_appraisals(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The items that still hold, or an empty list.  Never a new appraisal."""

    if not isinstance(view, Mapping) or "appraisals" not in view:
        raise ShortAppraisalViewError("view must be a short-appraisal view")
    return [
        item
        for item in view["appraisals"]
        if isinstance(item, Mapping) and item.get("status") == APPRAISAL_STATUS_VALID
    ]


class ShortAppraisalView:
    """Injectable projection: one instance, one home, one subject.

    Construction is where wiring faults happen (illegal subject, unusable home,
    unparseable instant).  The object stores nothing between calls: every
    ``build`` projects exactly the input it was given, which is what
    "rebuildable" means here.
    """

    def __init__(self, *, home: Any, subject_id: Optional[str] = None) -> None:
        resolved_subject = _subject(subject_id)
        if resolved_subject is None:
            raise ShortAppraisalViewError(
                "the subject owner (activity_continuity) is unavailable; the subject rule "
                "cannot be verified, so no appraisal projection is allowed"
            )
        self.home = home
        self.home_ref = _home_ref(home)
        self.subject_id = resolved_subject
        self.last_view: Optional[dict[str, Any]] = None

    def build(
        self,
        *,
        now: Any = None,
        appraisals: Sequence[Any] = (),
        corrections: Sequence[Any] = (),
        lost_source_refs: Sequence[Any] = (),
    ) -> dict[str, Any]:
        view = build_short_appraisal_view(
            home=self.home,
            subject_id=self.subject_id,
            now=now,
            appraisals=appraisals,
            corrections=corrections,
            lost_source_refs=lost_source_refs,
        )
        self.last_view = view
        return view

    def valid_appraisals(self, view: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
        return valid_appraisals(self.build() if view is None else view)
