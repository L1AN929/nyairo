#!/usr/bin/env python3
"""S07-AFFECT-OWNER-1: the minimal S07 Affect owner (v2.0 Ch.17 S07 row).

Before 2026-11-14 the tree had no S07 affect owner at all (see
``short_appraisal_view.py`` history); this module is the minimal owner that
closes that gap, deliberately smaller than a full psychological engine:

* **typed** — exactly three closed record kinds (``mood_shift`` / ``affect_pulse``
  / ``drive``); anything else is refused (``AFFECT_KIND_INVALID``).  The S07 row
  of the contract says "Emotion/Mood/Affect/Drive 有类型同 owner": one owner,
  typed records, no per-kind second authority;
* **one owner per record** — every record carries ``subject_id``; the view
  refuses records belonging to another subject (``AFFECT_SUBJECT_MISMATCH``) and
  never re-attributes them;
* **bounded intensity, quantized** — ``intensity`` is an integer 0..100
  (hundredths of [0,1]); continuous floats and out-of-range values are refused
  (``AFFECT_INTENSITY_INVALID``).  No aggregation: two records are never summed,
  averaged or ranked (``intensity_aggregation: "none"``);
* **sourced** — at least one structured ``source_refs`` entry is mandatory
  (``AFFECT_SOURCE_REFS_MISSING``); anonymous affect cannot be minted;
* **deterministic decay, zero RNG** — two closed decay models, both pure integer
  math: ``linear`` (``decay_per_hour_milli`` milli-hundredths lost per hour) and
  ``exponential`` (``decay_half_life_seconds``; every elapsed half-life floors
  the intensity by one bit-shift).  No ``random`` import anywhere;
* **expires naturally** — ``expires_at`` is mandatory (``AFFECT_VALID_UNTIL_MISSING``,
  same discipline as the short-appraisal projection); at or after ``expires_at``
  the record is unreadable.  Faded (decayed to 0) records are equally unreadable.
  Both vanish silently: the view exposes only counts for them, never a per-record
  tombstone (``zero_tombstone: True``), aligned with the evaporation channel;
* **no smuggling** — a closed forbidden-field vocabulary (``intimacy_score``,
  ``mood_delta``, ``affection_score`` ... tone/transcript keys ...) is refused
  outright; a record cannot sign a stable fact, a relationship fact, a body fact
  or a tone filter through this owner;
* **no model** — the module imports no Candidate source, calls no AG-0/AG-1 code,
  never talks to a model (``llm_calls: 0``, ``model_called: False``).

What "owner" means here
-----------------------
The owner owns the *admission and lifecycle rules* of the S07 affect domain: the
typed vocabulary, the single-owner attribution, the bounded quantized intensity,
the mandatory sources, the deterministic decay and the mandatory expiry.  Record
admission goes through :func:`mint_affect_record` — the only legal minting path.
Callers may either hold minted records in their own jurisdiction or persist them
through the opt-in zero-tombstone store (:class:`S07AffectStore`,
:func:`record_durable_affect`, :func:`read_affect_records_zero_write`,
:func:`project_durable_s07_affect_zero_write`) backed by
``state_tx_guard.state_file_lock`` at ``<state_root>/s07_affect_records.json``.
The pure projection :func:`build_s07_affect_view` remains stateless, writes no
file, and keeps no cache.

UNKNOWN safety
--------------
Without an injected reference instant, decay and expiry cannot be decided — so
nothing is decided: every record is reported ``INVALID`` with
``REASON_AFFECT_NO_REFERENCE_INSTANT`` and the view says ``reference_instant:
None``.  An unresolvable subject leaves ``identity_bound: False`` and zero
records are attributed.  Records may declare ``uncertainty``; undeclared ones
read with ``uncertainty_source: "UNKNOWN"`` rather than a invented confidence.

Raises versus refused items
---------------------------
``S07AffectOwnerError`` is raised only for a caller/wiring fault (illegal
subject, unusable home, unparseable reference instant) or an illegal *mint*
request.  Everything an already-minted record can get wrong is a per-item
reason code in the projection.
"""

from __future__ import annotations

import copy
import hashlib
import json
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

import body_capacity_source as body_capacity  # noqa: E402
import state_tx_guard as _tx  # noqa: E402

__all__ = [
    "AFFECT_KIND_DRIVE",
    "AFFECT_KIND_AFFECT_PULSE",
    "AFFECT_KIND_MOOD_SHIFT",
    "AFFECT_RECORD_KINDS",
    "AFFECT_STORE_FILENAME",
    "DECAY_MODES",
    "DECAY_MODE_EXPONENTIAL",
    "DECAY_MODE_LINEAR",
    "DECAY_MODE_LINEAR_DEFAULT_MILLI",
    "FORBIDDEN_AFFECT_FIELDS",
    "INTENSITY_SCALE_HUNDREDTHS",
    "KIND_S07_AFFECT_VIEW",
    "MAX_AFFECT_RECORDS",
    "MAX_AFFECT_SOURCE_REFS",
    "MAX_STORED_AFFECT_RECORDS",
    "REASON_AFFECT_BODY_FACT_REFUSED",
    "REASON_AFFECT_CORRECTED",
    "REASON_AFFECT_DUPLICATE_REF",
    "REASON_AFFECT_EXPIRED",
    "REASON_AFFECT_INTENSITY_INVALID",
    "REASON_AFFECT_KIND_INVALID",
    "REASON_AFFECT_NO_REFERENCE_INSTANT",
    "REASON_AFFECT_NOT_MAPPING",
    "REASON_AFFECT_OBSERVED_AT_AHEAD",
    "REASON_AFFECT_OBSERVED_AT_INVALID",
    "REASON_AFFECT_OBSERVED_AT_MISSING",
    "REASON_AFFECT_REF_MISSING",
    "REASON_AFFECT_SMUGGLING_REFUSED",
    "REASON_AFFECT_SOURCE_LOST",
    "REASON_AFFECT_SOURCE_REFS_MISSING",
    "REASON_AFFECT_SUBJECT_MISMATCH",
    "REASON_AFFECT_TONE_INFERENCE_REFUSED",
    "REASON_AFFECT_VALID_UNTIL_INVALID",
    "REASON_AFFECT_VALID_UNTIL_MISSING",
    "REASON_AFFECT_VALIDITY_INVERTED",
    "REASON_AFFECT_DECAY_INVALID",
    "REASON_NO_AFFECT_INPUT",
    "REASON_OK_SOURCED_AFFECT",
    "SCHEMA_S07_AFFECT_STORE",
    "SCHEMA_S07_AFFECT_VIEW",
    "S07_AFFECT_OWNER_ID",
    "STATUS_AFFECT_EXPIRED",
    "STATUS_AFFECT_FADED",
    "STATUS_AFFECT_INVALID",
    "STATUS_AFFECT_LIVE",
    "S07AffectOwnerError",
    "S07AffectStore",
    "affect_store_path",
    "build_s07_affect_view",
    "classify_affect_record",
    "effective_intensity_hundredths",
    "mint_affect_record",
    "project_durable_s07_affect_zero_write",
    "read_affect_records_zero_write",
    "record_durable_affect",
]

SCHEMA_S07_AFFECT_VIEW = "chiyo.life.s07_affect_view.v1"
SCHEMA_S07_AFFECT_STORE = "chiyo.life.s07_affect_store.v1"
KIND_S07_AFFECT_VIEW = "s07_affect_view"
S07_AFFECT_OWNER_ID = "s07_affect_owner.v1"
AFFECT_STORE_FILENAME = "s07_affect_records.json"

#: The closed typed vocabulary of the S07 affect domain (v2.0 Ch.17 S07 row:
#: "Emotion/Mood/Affect/Drive 有类型同 owner", minimal three-kind cut).
AFFECT_KIND_MOOD_SHIFT = "mood_shift"
AFFECT_KIND_AFFECT_PULSE = "affect_pulse"
AFFECT_KIND_DRIVE = "drive"
AFFECT_RECORD_KINDS: tuple[str, ...] = (
    AFFECT_KIND_MOOD_SHIFT,
    AFFECT_KIND_AFFECT_PULSE,
    AFFECT_KIND_DRIVE,
)

DECAY_MODE_LINEAR = "linear"
DECAY_MODE_EXPONENTIAL = "exponential"
DECAY_MODES: tuple[str, ...] = (DECAY_MODE_LINEAR, DECAY_MODE_EXPONENTIAL)
DECAY_MODE_LINEAR_DEFAULT_MILLI = 2500  # 2.5 hundredths of [0,1] per hour when undeclared

#: Intensity is stored as integer hundredths of [0,1]: 0..100.  Continuous floats
#: are refused — the project's integer-habit rule, applied to affect.
INTENSITY_SCALE_HUNDREDTHS = 100
MAX_AFFECT_RECORDS = 64
MAX_STORED_AFFECT_RECORDS = 64
MAX_AFFECT_SOURCE_REFS = 8
MAX_AFFECT_OP_RECEIPTS = 128
MAX_AFFECT_OP_RING = 512

STATUS_AFFECT_LIVE = "LIVE"
STATUS_AFFECT_FADED = "FADED"
STATUS_AFFECT_EXPIRED = "EXPIRED"
STATUS_AFFECT_INVALID = "INVALID"
LIVE_AFFECT_STATUSES = frozenset({STATUS_AFFECT_LIVE})

#: This owner's closed reason vocabulary.
REASON_OK_SOURCED_AFFECT = "OK_SOURCED_BOUNDED_AFFECT"
REASON_AFFECT_NOT_MAPPING = "AFFECT_NOT_A_MAPPING"
REASON_AFFECT_REF_MISSING = "AFFECT_REF_MISSING"
REASON_AFFECT_DUPLICATE_REF = "AFFECT_DUPLICATE_REF"
REASON_AFFECT_KIND_INVALID = "AFFECT_KIND_INVALID"
REASON_AFFECT_INTENSITY_INVALID = "AFFECT_INTENSITY_INVALID"
REASON_AFFECT_SOURCE_REFS_MISSING = "AFFECT_SOURCE_REFS_MISSING"
REASON_AFFECT_SUBJECT_MISMATCH = "AFFECT_SUBJECT_MISMATCH"
REASON_AFFECT_OBSERVED_AT_MISSING = "AFFECT_OBSERVED_AT_MISSING"
REASON_AFFECT_OBSERVED_AT_INVALID = "AFFECT_OBSERVED_AT_INVALID"
REASON_AFFECT_OBSERVED_AT_AHEAD = "AFFECT_OBSERVED_AT_AHEAD"
REASON_AFFECT_VALID_UNTIL_MISSING = "AFFECT_VALID_UNTIL_MISSING"
REASON_AFFECT_VALID_UNTIL_INVALID = "AFFECT_VALID_UNTIL_INVALID"
REASON_AFFECT_VALIDITY_INVERTED = "AFFECT_VALIDITY_INVERTED"
REASON_AFFECT_DECAY_INVALID = "AFFECT_DECAY_INVALID"
REASON_AFFECT_NO_REFERENCE_INSTANT = "AFFECT_NO_REFERENCE_INSTANT"
REASON_AFFECT_EXPIRED = "AFFECT_EXPIRED"
REASON_AFFECT_CORRECTED = "AFFECT_CORRECTED"
REASON_AFFECT_SOURCE_LOST = "AFFECT_SOURCE_LOST"
REASON_NO_AFFECT_INPUT = "NO_AFFECT_INPUT"
#: Smuggling reasons (kept distinct so audits can tell *what* was refused):
REASON_AFFECT_TONE_INFERENCE_REFUSED = "AFFECT_TONE_INFERENCE_REFUSED"
REASON_AFFECT_SMUGGLING_REFUSED = "AFFECT_STABLE_OR_SCORE_FIELD_REFUSED"
REASON_AFFECT_BODY_FACT_REFUSED = "AFFECT_BODY_FACT_REFUSED"

#: Tone-filter keys: this owner mints declared affect states, it never reads a
#: sentence to guess a feeling.
TONE_INFERENCE_FIELDS = frozenset({
    "tone",
    "sentiment",
    "text",
    "message",
    "transcript",
    "utterance",
    "reply",
    "words",
    "speech",
})
#: Score/state smuggling keys: forbidden channels that would sign a stable fact
#: or couple affect to a numeric relationship/accounting score.
SMUGGLING_FIELDS = frozenset({
    "intimacy_score",
    "mood_delta",
    "affection_score",
    "attachment_score",
    "trust_score",
    "relationship_score",
    "relationship_state",
    "stable_emotion",
    "preference",
    "preference_claim",
    "personality",
    "self_belief",
    "identity",
})
#: Body-truth keys: affect does not write Body through an emotion record.
BODY_FACT_FIELDS = frozenset({
    "body_capacity_state",
    "body_capacity_ref",
    "fatigue",
    "fatigue_load",
    "health",
})

FORBIDDEN_AFFECT_FIELDS = TONE_INFERENCE_FIELDS | SMUGGLING_FIELDS | BODY_FACT_FIELDS

#: Echo-only record fields: copied through verbatim and never interpreted.
ECHO_AFFECT_FIELDS = ("declared_scope", "uncertainty", "note")
_UNCERTAINTY_UNKNOWN = "UNKNOWN"


class S07AffectOwnerError(ValueError):
    """A caller/wiring or minting fault — not a record the caller's sources produced."""


def _instant(value: Any, label: str) -> datetime:
    try:
        return body_capacity.require_instant(value, label)
    except body_capacity.BodyCapacitySourceError as error:
        raise S07AffectOwnerError(str(error)) from error


def _subject(subject_id: Optional[str]) -> Optional[str]:
    try:
        return body_capacity.resolve_subject_id(subject_id)
    except body_capacity.BodyCapacitySourceError as error:
        raise S07AffectOwnerError(str(error)) from error


def _home_ref(home: Any) -> str:
    try:
        return body_capacity.bind_home_ref(home)
    except body_capacity.BodyCapacitySourceError as error:
        raise S07AffectOwnerError(str(error)) from error


# ---------------------------------------------------------------------------
# integer decay math (zero float, zero RNG)
# ---------------------------------------------------------------------------


def effective_intensity_hundredths(
    *,
    intensity_hundredths: int,
    decay_mode: str,
    elapsed_seconds: int,
    decay_per_hour_milli: Optional[int] = None,
    decay_half_life_seconds: Optional[int] = None,
) -> int:
    """Remaining quantized intensity after ``elapsed_seconds``; pure integer math.

    ``linear``: ``decay_per_hour_milli`` milli-hundredths lost per hour, floor
    division on exact seconds; ``exponential``: one right-shift (halving with
    integer floor) per elapsed half-life.  Both saturate at 0 and never go
    negative.  Same inputs always give the same output — deterministic.
    """
    base = max(0, int(intensity_hundredths))
    if elapsed_seconds <= 0:
        return min(INTENSITY_SCALE_HUNDREDTHS, base)
    if decay_mode == DECAY_MODE_LINEAR:
        rate = int(decay_per_hour_milli or 0)
        lost_milli = (rate * int(elapsed_seconds)) // 3600
        remaining_milli = base * 1000 - lost_milli
        if remaining_milli <= 0:
            return 0
        # ceiling: a not-yet-faded record never displays as a zero-intensity lie
        return min(INTENSITY_SCALE_HUNDREDTHS, (remaining_milli + 999) // 1000)
    if decay_mode == DECAY_MODE_EXPONENTIAL:
        half_life = max(1, int(decay_half_life_seconds or 1))
        shifts = int(elapsed_seconds) // half_life
        return base >> shifts if shifts < 64 else 0
    raise S07AffectOwnerError("AFFECT_DECAY_INVALID")


# ---------------------------------------------------------------------------
# minting — the only legal admission path into the S07 affect domain
# ---------------------------------------------------------------------------


def mint_affect_record(
    *,
    record_id: Any,
    kind: Any,
    subject_id: Any,
    intensity: Any,
    source_refs: Any,
    observed_at: Any,
    expires_at: Any,
    decay_mode: Any = DECAY_MODE_LINEAR,
    decay_per_hour_milli: Any = None,
    decay_half_life_seconds: Any = None,
    uncertainty: Any = None,
    declared_scope: Any = None,
    note: Any = None,
) -> dict[str, Any]:
    """Mint one typed, owner-attributed, bounded, sourced, expiring affect record.

    ``record_id`` is mandatory and caller-chosen (the owner never derives an id
    from wall-clock or randomness).  Raises :class:`S07AffectOwnerError` on any
    contract violation — a minting fault is a wiring fault, never silently
    downgraded.  The returned mapping is a plain dict the caller stores under
    its own jurisdiction; this module keeps no store.
    """

    resolved_subject = _subject(subject_id if isinstance(subject_id, str) else None)
    if resolved_subject is None:
        raise S07AffectOwnerError("AFFECT_SUBJECT_REQUIRED")

    if not isinstance(record_id, str) or not record_id.strip():
        raise S07AffectOwnerError("AFFECT_REF_MISSING")
    try:
        clean_record_id = body_capacity.require_structured_ref(record_id, "record_id")
    except body_capacity.BodyCapacitySourceError as error:
        raise S07AffectOwnerError(str(error)) from error

    if kind not in AFFECT_RECORD_KINDS:
        raise S07AffectOwnerError(f"AFFECT_KIND_INVALID:{kind!r}")

    # intensity: integer hundredths only — floats/bools/strings/out-of-range refused
    if isinstance(intensity, bool) or not isinstance(intensity, int):
        raise S07AffectOwnerError(f"AFFECT_INTENSITY_INVALID:{intensity!r}")
    if not (0 <= intensity <= INTENSITY_SCALE_HUNDREDTHS):
        raise S07AffectOwnerError(f"AFFECT_INTENSITY_INVALID:{intensity!r}")

    if not isinstance(source_refs, (list, tuple)) or not source_refs:
        raise S07AffectOwnerError("AFFECT_SOURCE_REFS_MISSING")
    sources: list[str] = []
    for candidate in source_refs:
        if not isinstance(candidate, str) or not candidate.strip():
            raise S07AffectOwnerError("AFFECT_SOURCE_REFS_MISSING")
        try:
            sources.append(body_capacity.require_structured_ref(candidate, "source_refs"))
        except body_capacity.BodyCapacitySourceError as error:
            raise S07AffectOwnerError(str(error)) from error
    sources = sorted(set(sources))

    observed_dt = _instant(observed_at, "observed_at")
    expires_dt = _instant(expires_at, "expires_at")
    if expires_dt <= observed_dt:
        raise S07AffectOwnerError("AFFECT_VALIDITY_INVERTED")

    if decay_mode not in DECAY_MODES:
        raise S07AffectOwnerError(f"AFFECT_DECAY_INVALID:{decay_mode!r}")
    linear_rate: Optional[int] = None
    half_life: Optional[int] = None
    if decay_mode == DECAY_MODE_LINEAR:
        rate = DECAY_MODE_LINEAR_DEFAULT_MILLI if decay_per_hour_milli is None else decay_per_hour_milli
        if isinstance(rate, bool) or not isinstance(rate, int) or not (0 <= rate <= 100000):
            raise S07AffectOwnerError(f"AFFECT_DECAY_INVALID:{rate!r}")
        linear_rate = int(rate)
        if decay_half_life_seconds is not None:
            raise S07AffectOwnerError("AFFECT_DECAY_INVALID:decay_half_life_seconds_with_linear")
    else:
        if decay_per_hour_milli is not None:
            raise S07AffectOwnerError("AFFECT_DECAY_INVALID:decay_per_hour_milli_with_exponential")
        if isinstance(decay_half_life_seconds, bool) or not isinstance(decay_half_life_seconds, int) or decay_half_life_seconds <= 0:
            raise S07AffectOwnerError(f"AFFECT_DECAY_INVALID:{decay_half_life_seconds!r}")
        half_life = int(decay_half_life_seconds)

    record: dict[str, Any] = {
        "record_id": clean_record_id,
        "kind": kind,
        "subject_id": resolved_subject,
        "intensity": int(intensity),
        "source_refs": sources,
        "observed_at": observed_dt.isoformat(),
        "expires_at": expires_dt.isoformat(),
        "decay_mode": decay_mode,
        "uncertainty": uncertainty if isinstance(uncertainty, str) and uncertainty.strip() else None,
        "declared_scope": declared_scope if isinstance(declared_scope, str) and declared_scope.strip() else None,
        "note": note if isinstance(note, str) and note.strip() else None,
    }
    if linear_rate is not None:
        record["decay_per_hour_milli"] = linear_rate
    if half_life is not None:
        record["decay_half_life_seconds"] = half_life
    return record


# ---------------------------------------------------------------------------
# classification & projection
# ---------------------------------------------------------------------------


def _empty_affect_item() -> dict[str, Any]:
    return {
        "record_id": None,
        "kind": None,
        "subject_id": None,
        "source_refs": [],
        "duplicate_source_refs": [],
        "observed_at": None,
        "expires_at": None,
        "declared_scope": None,
        "uncertainty": None,
        "uncertainty_source": _UNCERTAINTY_UNKNOWN,
        "note": None,
        "intensity": None,
        "effective_intensity": None,
        "seconds_until_expiry": None,
        "status": STATUS_AFFECT_INVALID,
        "reason_code": REASON_AFFECT_NOT_MAPPING,
    }


def classify_affect_record(
    raw: Any,
    *,
    subject_id: Optional[str],
    reference: Optional[datetime],
    seen: Optional[set] = None,
    corrected_refs: Optional[set] = None,
    lost_sources: Optional[set] = None,
) -> dict[str, Any]:
    """One record, classified.  First failure wins; fail closed; zero tombstones."""

    item = _empty_affect_item()
    if not isinstance(raw, Mapping):
        return item

    for field in ECHO_AFFECT_FIELDS:
        if isinstance(raw.get(field), str):
            item[field] = raw[field]
            if field == "uncertainty":
                item["uncertainty_source"] = "declared"

    ref = raw.get("record_id")
    if not isinstance(ref, str) or not ref.strip():
        item["reason_code"] = REASON_AFFECT_REF_MISSING
        return item
    try:
        item["record_id"] = body_capacity.require_structured_ref(ref, "record_id")
    except body_capacity.BodyCapacitySourceError:
        item["reason_code"] = REASON_AFFECT_REF_MISSING
        return item
    if seen is not None:
        if item["record_id"] in seen:
            item["reason_code"] = REASON_AFFECT_DUPLICATE_REF
            return item
        seen.add(item["record_id"])

    leaked_tone = sorted(k for k in raw if k in TONE_INFERENCE_FIELDS)
    if leaked_tone:
        item["reason_code"] = REASON_AFFECT_TONE_INFERENCE_REFUSED
        item["refused_fields"] = leaked_tone
        return item
    leaked_smuggle = sorted(k for k in raw if k in SMUGGLING_FIELDS)
    if leaked_smuggle:
        item["reason_code"] = REASON_AFFECT_SMUGGLING_REFUSED
        item["refused_fields"] = leaked_smuggle
        return item
    leaked_body = sorted(k for k in raw if k in BODY_FACT_FIELDS)
    if leaked_body:
        item["reason_code"] = REASON_AFFECT_BODY_FACT_REFUSED
        item["refused_fields"] = leaked_body
        return item

    kind = raw.get("kind")
    if kind not in AFFECT_RECORD_KINDS:
        item["reason_code"] = REASON_AFFECT_KIND_INVALID
        return item
    item["kind"] = kind

    raw_subject = raw.get("subject_id")
    if raw_subject is not None:
        raw_subject = raw_subject.strip() if isinstance(raw_subject, str) else raw_subject
        if subject_id is None or raw_subject != subject_id:
            item["subject_id"] = raw_subject if isinstance(raw_subject, str) else None
            item["reason_code"] = REASON_AFFECT_SUBJECT_MISMATCH
            return item
    item["subject_id"] = subject_id

    declared_sources = raw.get("source_refs")
    if not isinstance(declared_sources, (list, tuple)) or not declared_sources:
        item["reason_code"] = REASON_AFFECT_SOURCE_REFS_MISSING
        return item
    sources: list[str] = []
    for candidate in declared_sources:
        if not isinstance(candidate, str) or not candidate.strip():
            item["reason_code"] = REASON_AFFECT_SOURCE_REFS_MISSING
            return item
        try:
            sources.append(body_capacity.require_structured_ref(candidate, "source_refs"))
        except body_capacity.BodyCapacitySourceError:
            item["reason_code"] = REASON_AFFECT_SOURCE_REFS_MISSING
            return item
    item["source_refs"] = sorted(set(sources))
    item["duplicate_source_refs"] = sorted(
        {r for r in sources if sources.count(r) > 1}
    )

    raw_observed = raw.get("observed_at")
    if raw_observed is None or (isinstance(raw_observed, str) and not raw_observed.strip()):
        item["reason_code"] = REASON_AFFECT_OBSERVED_AT_MISSING
        return item
    observed_instant = body_capacity.parse_instant(raw_observed)
    if observed_instant is None:
        item["reason_code"] = REASON_AFFECT_OBSERVED_AT_INVALID
        return item
    item["observed_at"] = observed_instant.isoformat()

    declared_until = raw.get("expires_at")
    if declared_until is None or (isinstance(declared_until, str) and not declared_until.strip()):
        item["reason_code"] = REASON_AFFECT_VALID_UNTIL_MISSING
        return item
    valid_until = body_capacity.parse_instant(declared_until)
    if valid_until is None:
        item["reason_code"] = REASON_AFFECT_VALID_UNTIL_INVALID
        return item
    item["expires_at"] = valid_until.isoformat()
    if valid_until <= observed_instant:
        item["reason_code"] = REASON_AFFECT_VALIDITY_INVERTED
        return item

    intensity = raw.get("intensity")
    if isinstance(intensity, bool) or not isinstance(intensity, int) or not (
        0 <= intensity <= INTENSITY_SCALE_HUNDREDTHS
    ):
        item["reason_code"] = REASON_AFFECT_INTENSITY_INVALID
        return item
    item["intensity"] = int(intensity)

    decay_mode = raw.get("decay_mode", DECAY_MODE_LINEAR)
    if decay_mode not in DECAY_MODES:
        item["reason_code"] = REASON_AFFECT_DECAY_INVALID
        return item
    decay_per_hour_milli = raw.get("decay_per_hour_milli")
    decay_half_life_seconds = raw.get("decay_half_life_seconds")
    if decay_mode == DECAY_MODE_LINEAR:
        if decay_half_life_seconds is not None:
            item["reason_code"] = REASON_AFFECT_DECAY_INVALID
            return item
        if decay_per_hour_milli is None:
            decay_per_hour_milli = DECAY_MODE_LINEAR_DEFAULT_MILLI
        if isinstance(decay_per_hour_milli, bool) or not isinstance(decay_per_hour_milli, int) or not (
            0 <= decay_per_hour_milli <= 100000
        ):
            item["reason_code"] = REASON_AFFECT_DECAY_INVALID
            return item
    else:
        if decay_per_hour_milli is not None:
            item["reason_code"] = REASON_AFFECT_DECAY_INVALID
            return item
        if isinstance(decay_half_life_seconds, bool) or not isinstance(decay_half_life_seconds, int) or decay_half_life_seconds <= 0:
            item["reason_code"] = REASON_AFFECT_DECAY_INVALID
            return item

    if corrected_refs and (
        item["record_id"] in corrected_refs
        or any(source in corrected_refs for source in item["source_refs"])
    ):
        item["reason_code"] = REASON_AFFECT_CORRECTED
        return item
    if lost_sources:
        lost = sorted(source for source in item["source_refs"] if source in lost_sources)
        if lost:
            item["lost_source_refs"] = lost
            item["reason_code"] = REASON_AFFECT_SOURCE_LOST
            return item

    if reference is None:
        item["reason_code"] = REASON_AFFECT_NO_REFERENCE_INSTANT
        return item
    if observed_instant > reference:
        item["reason_code"] = REASON_AFFECT_OBSERVED_AT_AHEAD
        return item
    seconds_until_expiry = int((valid_until - reference).total_seconds())
    if seconds_until_expiry <= 0:
        item["status"] = STATUS_AFFECT_EXPIRED
        item["reason_code"] = REASON_AFFECT_EXPIRED
        return item

    try:
        effective = effective_intensity_hundredths(
            intensity_hundredths=int(item["intensity"]),
            decay_mode=decay_mode,
            elapsed_seconds=max(0, int((reference - observed_instant).total_seconds())),
            decay_per_hour_milli=decay_per_hour_milli,
            decay_half_life_seconds=decay_half_life_seconds,
        )
    except S07AffectOwnerError:
        item["reason_code"] = REASON_AFFECT_DECAY_INVALID
        return item
    item["seconds_until_expiry"] = seconds_until_expiry
    if effective <= 0:
        item["status"] = STATUS_AFFECT_FADED
        item["effective_intensity"] = 0
        item["reason_code"] = REASON_OK_SOURCED_AFFECT
        return item

    item["status"] = STATUS_AFFECT_LIVE
    item["effective_intensity"] = effective
    item["reason_code"] = REASON_OK_SOURCED_AFFECT
    return item


def build_s07_affect_view(
    records_pos: Optional[Sequence[Any]] = None,
    *,
    home: Any,
    subject_id: Optional[str] = None,
    now: Any = None,
    reference_instant: Any = None,
    records: Sequence[Any] = (),
) -> dict[str, Any]:
    """Project the caller's affect records through the S07 owner contract.

    Live records are returned with their *current* quantized intensity (decay
    applied by elapsed time); expired and faded records disappear silently and
    are counted, never tombstoned.  ``now`` (or ``reference_instant``) is
    injected by the caller — without it nothing is decided (UNKNOWN safety).
    """

    eff_now = reference_instant if reference_instant is not None else now
    eff_records = records_pos if records_pos is not None else records
    reference = None if eff_now is None else _instant(eff_now, "now")
    reference_iso = reference.isoformat() if reference is not None else None
    resolved_subject = _subject(subject_id)

    try:
        home_ref = _home_ref(home)
    except S07AffectOwnerError:
        return _identity_unbound_view(home_ref=None, reason=body_capacity.REASON_HOME_UNBOUND)
    if resolved_subject is None:
        return _identity_unbound_view(
            home_ref=home_ref, reason=body_capacity.REASON_SUBJECT_OWNER_UNAVAILABLE
        )

    bounded = list(eff_records or ())[:MAX_AFFECT_RECORDS]
    seen: set = set()
    items = [
        classify_affect_record(
            raw,
            subject_id=resolved_subject,
            reference=reference,
            seen=seen,
        )
        for raw in bounded
    ]

    live = [item for item in items if item["status"] == STATUS_AFFECT_LIVE]
    faded_count = sum(1 for item in items if item["status"] == STATUS_AFFECT_FADED)
    expired_count = sum(1 for item in items if item["status"] == STATUS_AFFECT_EXPIRED)
    invalid = [
        {"record_id": item["record_id"], "reason_code": item["reason_code"]}
        for item in items
        if item["status"] == STATUS_AFFECT_INVALID
    ]
    # zero tombstone: only still-readable records are listed.  Faded and expired
    # ones vanish silently (counts only); refused ones keep just ref + reason.
    readable = list(live)

    if live:
        empty_reason = None
    elif not items:
        empty_reason = REASON_NO_AFFECT_INPUT
    elif faded_count or expired_count:
        empty_reason = REASON_AFFECT_EXPIRED if expired_count else REASON_OK_SOURCED_AFFECT
    else:
        empty_reason = invalid[0]["reason_code"]

    live_refs = [item["record_id"] for item in live]

    return {
        "schema_version": SCHEMA_S07_AFFECT_VIEW,
        "kind": KIND_S07_AFFECT_VIEW,
        "owner_module": S07_AFFECT_OWNER_ID,
        "subject_id": resolved_subject,
        "home_ref": home_ref,
        "observed_at": reference_iso,
        "identity_bound": True,
        "identity_reason_code": None,
        "record_kinds": list(AFFECT_RECORD_KINDS),
        "records": readable,
        "live_records": live,
        "live_record_refs": live_refs,
        "has_live_affect": bool(live),
        "faded_count": faded_count,
        "expired_count": expired_count,
        "invalid_records": invalid,
        "empty_reason_code": empty_reason,
        "intensity_aggregation": "none",
        "zero_tombstone": True,
        "protocol": {
            "kind": "s07_affect_owner",
            "owner_module": S07_AFFECT_OWNER_ID,
            "typed_record_kinds": list(AFFECT_RECORD_KINDS),
            "single_owner_attribution": True,
            "intensity_scale_hundredths": INTENSITY_SCALE_HUNDREDTHS,
            "continuous_float_intensity": False,
            "requires_source_refs": True,
            "requires_expires_at": True,
            "decay_modes": list(DECAY_MODES),
            "zero_rng": True,
            "zero_tombstone_on_expiry": True,
            "stable_emotion_asserted": False,
            "relationship_fact_asserted": False,
            "preference_fact_asserted": False,
            "body_fact_asserted": False,
            "tone_inference_used": False,
            "forbidden_fields": sorted(FORBIDDEN_AFFECT_FIELDS),
            "intensity_aggregation": "none",
            "llm_calls": 0,
            "model_called": False,
            "persistent_store": None,
            "rebuildable": True,
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
        "schema_version": SCHEMA_S07_AFFECT_VIEW,
        "kind": KIND_S07_AFFECT_VIEW,
        "owner_module": S07_AFFECT_OWNER_ID,
        "subject_id": None,
        "home_ref": home_ref,
        "observed_at": None,
        "identity_bound": False,
        "identity_reason_code": reason,
        "record_kinds": list(AFFECT_RECORD_KINDS),
        "records": [],
        "live_records": [],
        "live_record_refs": [],
        "has_live_affect": False,
        "faded_count": 0,
        "expired_count": 0,
        "invalid_records": [],
        "empty_reason_code": reason,
        "intensity_aggregation": "none",
        "zero_tombstone": True,
        "protocol": {
            "kind": "s07_affect_owner",
            "owner_module": S07_AFFECT_OWNER_ID,
            "typed_record_kinds": list(AFFECT_RECORD_KINDS),
            "single_owner_attribution": True,
            "intensity_scale_hundredths": INTENSITY_SCALE_HUNDREDTHS,
            "continuous_float_intensity": False,
            "requires_source_refs": True,
            "requires_expires_at": True,
            "decay_modes": list(DECAY_MODES),
            "zero_rng": True,
            "zero_tombstone_on_expiry": True,
            "stable_emotion_asserted": False,
            "relationship_fact_asserted": False,
            "preference_fact_asserted": False,
            "body_fact_asserted": False,
            "tone_inference_used": False,
            "forbidden_fields": sorted(FORBIDDEN_AFFECT_FIELDS),
            "intensity_aggregation": "none",
            "llm_calls": 0,
            "model_called": False,
            "persistent_store": None,
            "rebuildable": True,
        },
        "read_only": True,
        "reads_performed": 0,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
        "actions_authorized": 0,
    }


# ---------------------------------------------------------------------------
# Durable zero-tombstone S07 affect store (<state_root>/s07_affect_records.json)
# ---------------------------------------------------------------------------

_UNSET = object()


def affect_store_path(state_root: Path | str) -> Path:
    """Return ``<state_root>/s07_affect_records.json`` without touching disk."""

    if isinstance(state_root, (str, Path)):
        text = str(state_root).strip()
    else:
        text = ""
    if not text:
        raise S07AffectOwnerError("AFFECT_STATE_ROOT_INVALID")
    return Path(text) / AFFECT_STORE_FILENAME


def _is_safe_regular_store_file(path: Path, *, anchor: Optional[Path] = None) -> bool:
    """True when ``path`` is a regular file with zero symlink hops up to ``anchor``."""

    try:
        if path.is_symlink():
            return False
        cur = path.parent
        visited = 0
        while cur is not None and visited < 16:
            if cur.is_symlink():
                return False
            if anchor is not None and cur == anchor:
                break
            if anchor is None or cur.parent == cur:
                break
            cur = cur.parent
            visited += 1
        st = path.lstat()
        return stat.S_ISREG(st.st_mode)
    except OSError:
        return False


def _empty_affect_store_doc(*, subject_id: str, home_ref: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_S07_AFFECT_STORE,
        "owner_module": S07_AFFECT_OWNER_ID,
        "subject_id": subject_id,
        "home_ref": home_ref,
        "revision": 0,
        "next_record_seq": 0,
        "next_op_seq": 0,
        "updated_at": None,
        "zero_tombstone": True,
        "records": [],
        "operations": {},
        "committed_op_ring": [],
    }


def _check_extra_forbidden_fields(extra: Mapping[str, Any]) -> None:
    if not extra:
        return
    tone_hits = sorted(str(k) for k in extra if str(k) in TONE_INFERENCE_FIELDS)
    if tone_hits:
        raise S07AffectOwnerError(f"{REASON_AFFECT_TONE_INFERENCE_REFUSED}:{','.join(tone_hits)}")
    smuggle_hits = sorted(str(k) for k in extra if str(k) in SMUGGLING_FIELDS)
    if smuggle_hits:
        raise S07AffectOwnerError(f"{REASON_AFFECT_SMUGGLING_REFUSED}:{','.join(smuggle_hits)}")
    body_hits = sorted(str(k) for k in extra if str(k) in BODY_FACT_FIELDS)
    if body_hits:
        raise S07AffectOwnerError(f"{REASON_AFFECT_BODY_FACT_REFUSED}:{','.join(body_hits)}")
    other_hits = sorted(str(k) for k in extra)
    raise S07AffectOwnerError(f"AFFECT_UNKNOWN_FIELD_REFUSED:{','.join(other_hits)}")


def read_affect_records_zero_write(state_root: Path | str) -> list[Any]:
    """Read stored S07 affect records with 100% zero disk writes.

    When ``state_root`` or ``s07_affect_records.json`` is absent, returns ``[]``,
    creates zero directories or lock files, and modifies zero mtimes.
    """

    store_file = affect_store_path(state_root)
    root_path = Path(str(state_root).strip())
    try:
        if root_path.is_symlink():
            return []
    except OSError:
        return []
    if not _is_safe_regular_store_file(store_file, anchor=root_path):
        return []
    try:
        doc = json.loads(store_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if isinstance(doc, Mapping):
        raw_records = doc.get("records")
    elif isinstance(doc, list):
        raw_records = doc
    else:
        return []
    if not isinstance(raw_records, list):
        return []
    return [
        copy.deepcopy(dict(item)) if isinstance(item, Mapping) else copy.deepcopy(item)
        for item in raw_records[:MAX_STORED_AFFECT_RECORDS]
    ]


def record_durable_affect(
    state_root: Path | str,
    *,
    home: Any = _UNSET,
    reference_instant: Any = None,
    now: Any = None,
    observed_at: Any = None,
    subject_id: Optional[str] = None,
    record_id: Optional[str] = None,
    record_kind: Any = None,
    kind: Any = None,
    intensity: Any,
    source_refs: Any,
    expires_at: Any,
    decay_mode: Any = DECAY_MODE_LINEAR,
    decay_per_hour_milli: Any = None,
    decay_half_life_seconds: Any = None,
    uncertainty: Any = None,
    declared_scope: Any = None,
    note: Any = None,
    operation_key: Optional[str] = None,
    extra_fields: Optional[Mapping[str, Any]] = None,
    **unexpected_kwargs: Any,
) -> dict[str, Any]:
    """Validate, mint, prune with zero tombstones, and atomically persist an S07 affect record."""

    merged_extra: dict[str, Any] = {}
    if extra_fields is not None:
        if not isinstance(extra_fields, Mapping):
            raise S07AffectOwnerError("AFFECT_EXTRA_FIELDS_INVALID")
        merged_extra.update({str(k): v for k, v in extra_fields.items()})
    if unexpected_kwargs:
        merged_extra.update(unexpected_kwargs)
    _check_extra_forbidden_fields(merged_extra)

    store_file = affect_store_path(state_root)
    root_path = Path(str(state_root).strip())
    try:
        if root_path.is_symlink() or store_file.is_symlink():
            raise S07AffectOwnerError("AFFECT_STORE_SYMLINK_REFUSED")
    except OSError as error:
        raise S07AffectOwnerError(str(error)) from error

    if home is _UNSET:
        effective_home: Any = root_path if root_path.is_dir() else root_path.parent
    else:
        effective_home = home
    home_ref = _home_ref(effective_home)
    resolved_subject = _subject(subject_id)
    if resolved_subject is None:
        raise S07AffectOwnerError("AFFECT_SUBJECT_REQUIRED")

    raw_ref = reference_instant if reference_instant is not None else now
    ref_dt = _instant(raw_ref, "reference_instant")
    ref_iso = ref_dt.isoformat()
    obs_dt = _instant(observed_at if observed_at is not None else ref_dt, "observed_at")
    if obs_dt > ref_dt:
        raise S07AffectOwnerError(REASON_AFFECT_OBSERVED_AT_AHEAD)

    eff_kind = record_kind if record_kind is not None else kind
    clean_op_key: Optional[str] = None
    if operation_key is not None:
        if not isinstance(operation_key, str) or not operation_key.strip():
            raise S07AffectOwnerError("AFFECT_OPERATION_KEY_INVALID")
        clean_op_key = operation_key.strip()

    provisional_id = record_id if record_id is not None else "affect:preflight"
    preflight = mint_affect_record(
        record_id=provisional_id,
        kind=eff_kind,
        subject_id=resolved_subject,
        intensity=intensity,
        source_refs=source_refs,
        observed_at=obs_dt,
        expires_at=expires_at,
        decay_mode=decay_mode,
        decay_per_hour_milli=decay_per_hour_milli,
        decay_half_life_seconds=decay_half_life_seconds,
        uncertainty=uncertainty,
        declared_scope=declared_scope,
        note=note,
    )

    payload_hash = _tx.payload_fingerprint({
        "subject_id": resolved_subject,
        "record_id": preflight["record_id"] if record_id is not None else None,
        "kind": preflight["kind"],
        "intensity": preflight["intensity"],
        "source_refs": preflight["source_refs"],
        "observed_at": preflight["observed_at"],
        "expires_at": preflight["expires_at"],
        "decay_mode": preflight["decay_mode"],
        "decay_per_hour_milli": preflight.get("decay_per_hour_milli"),
        "decay_half_life_seconds": preflight.get("decay_half_life_seconds"),
        "uncertainty": preflight.get("uncertainty"),
        "declared_scope": preflight.get("declared_scope"),
        "note": preflight.get("note"),
    })

    with _tx.state_file_lock(store_file):
        if store_file.is_symlink():
            raise S07AffectOwnerError("AFFECT_STORE_SYMLINK_REFUSED")
        if store_file.exists():
            if not _is_safe_regular_store_file(store_file, anchor=root_path):
                raise S07AffectOwnerError("AFFECT_STORE_SYMLINK_REFUSED")
            try:
                loaded = json.loads(store_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as error:
                raise S07AffectOwnerError(f"AFFECT_STORE_CORRUPT:{error}") from error
            if not isinstance(loaded, Mapping):
                raise S07AffectOwnerError("AFFECT_STORE_CORRUPT")
            store_doc = dict(loaded)
        else:
            store_doc = _empty_affect_store_doc(subject_id=resolved_subject, home_ref=home_ref)

        if clean_op_key is not None:
            reconcile_kind, matched_row = _tx.reconcile_evicted_or_active_op(
                None,
                store_doc,
                clean_op_key,
                dict_key="operations",
                payload_hash=payload_hash,
            )
            if reconcile_kind == "conflict":
                raise S07AffectOwnerError(f"OPERATION_KEY_CONFLICT:{clean_op_key}")
            if reconcile_kind in ("hit_full", "hit_evicted"):
                current_records = (
                    list(store_doc.get("records"))
                    if isinstance(store_doc.get("records"), list)
                    else []
                )
                matched_record_id = (matched_row or {}).get("record_id")
                matched_record: Optional[dict[str, Any]] = None
                if isinstance(matched_record_id, str) and matched_record_id:
                    for item in current_records:
                        if isinstance(item, Mapping) and item.get("record_id") == matched_record_id:
                            matched_record = copy.deepcopy(dict(item))
                            break
                return {
                    "ok": True,
                    "status": "already_applied",
                    "already_applied": True,
                    "idempotent_replay": True,
                    "operation_key": clean_op_key,
                    "record_id": matched_record_id,
                    "record": matched_record,
                    "persisted": matched_record is not None,
                    "reconciled_from": (
                        "operations" if reconcile_kind == "hit_full" else "committed_op_ring"
                    ),
                    "subject_id": resolved_subject,
                    "home_ref": home_ref,
                    "revision": int(store_doc.get("revision") or 0),
                    "stored_count": len(current_records),
                    "pruned_count": 0,
                    "zero_tombstone": True,
                    "store_path": str(store_file),
                    "llm_calls": 0,
                }

        next_record_seq = int(store_doc.get("next_record_seq") or 0) + 1
        if record_id is not None:
            final_record_id = preflight["record_id"]
        else:
            seed_material = f"{resolved_subject}|{next_record_seq}|{clean_op_key or ''}|{payload_hash}"
            digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16]
            final_record_id = f"affect:{digest}"

        minted = mint_affect_record(
            record_id=final_record_id,
            kind=eff_kind,
            subject_id=resolved_subject,
            intensity=intensity,
            source_refs=source_refs,
            observed_at=obs_dt,
            expires_at=expires_at,
            decay_mode=decay_mode,
            decay_per_hour_milli=decay_per_hour_milli,
            decay_half_life_seconds=decay_half_life_seconds,
            uncertainty=uncertainty,
            declared_scope=declared_scope,
            note=note,
        )

        existing_raw = store_doc.get("records")
        existing_list = list(existing_raw) if isinstance(existing_raw, list) else []
        kept_records: list[dict[str, Any]] = []
        pruned_count = 0
        seen_ids: set[str] = set()
        for raw_rec in existing_list:
            if isinstance(raw_rec, Mapping) and raw_rec.get("record_id") == final_record_id:
                continue
            classified = classify_affect_record(
                raw_rec,
                subject_id=resolved_subject,
                reference=ref_dt,
                seen=seen_ids,
            )
            if classified["status"] == STATUS_AFFECT_LIVE and isinstance(raw_rec, Mapping):
                kept_records.append(copy.deepcopy(dict(raw_rec)))
            else:
                pruned_count += 1

        new_classified = classify_affect_record(
            minted,
            subject_id=resolved_subject,
            reference=ref_dt,
            seen=set(),
        )
        persisted_live = new_classified["status"] == STATUS_AFFECT_LIVE
        if persisted_live:
            kept_records.append(copy.deepcopy(minted))
        else:
            pruned_count += 1

        if len(kept_records) > MAX_STORED_AFFECT_RECORDS:
            overflow = len(kept_records) - MAX_STORED_AFFECT_RECORDS
            del kept_records[:overflow]
            pruned_count += overflow

        live_ids = {str(r.get("record_id")) for r in kept_records if isinstance(r, Mapping)}
        ops_map = store_doc.get("operations")
        if isinstance(ops_map, dict):
            for op_row in ops_map.values():
                if isinstance(op_row, dict) and "record_id" in op_row:
                    if str(op_row.get("record_id")) not in live_ids:
                        op_row.pop("record_id", None)

        new_rev = int(store_doc.get("revision") or 0) + 1
        store_doc["schema_version"] = SCHEMA_S07_AFFECT_STORE
        store_doc["owner_module"] = S07_AFFECT_OWNER_ID
        store_doc["subject_id"] = resolved_subject
        store_doc["home_ref"] = home_ref
        store_doc["revision"] = new_rev
        store_doc["next_record_seq"] = next_record_seq
        store_doc["updated_at"] = ref_iso
        store_doc["zero_tombstone"] = True
        store_doc["records"] = kept_records
        store_doc.setdefault("operations", {})
        store_doc.setdefault("committed_op_ring", [])

        if clean_op_key is not None:
            receipt_meta: dict[str, Any] = {
                "status": "applied",
                "ok": True,
            }
            if persisted_live:
                receipt_meta["record_id"] = final_record_id
            _tx.stamp_receipt(
                store_doc,
                clean_op_key,
                receipt_meta,
                revision=new_rev,
                dict_key="operations",
                payload_hash=payload_hash,
                committed_at=ref_iso,
            )
            _tx.trim_receipts_by_op_seq(
                store_doc,
                dict_key="operations",
                max_keep=MAX_AFFECT_OP_RECEIPTS,
                max_ring=MAX_AFFECT_OP_RING,
            )

        _tx.atomic_write_json(store_file, store_doc)

    return {
        "ok": True,
        "status": "applied",
        "already_applied": False,
        "idempotent_replay": False,
        "operation_key": clean_op_key,
        "record_id": final_record_id,
        "record": copy.deepcopy(minted) if persisted_live else None,
        "persisted": persisted_live,
        "subject_id": resolved_subject,
        "home_ref": home_ref,
        "revision": new_rev,
        "stored_count": len(kept_records),
        "pruned_count": pruned_count,
        "zero_tombstone": True,
        "store_path": str(store_file),
        "llm_calls": 0,
    }


def project_durable_s07_affect_zero_write(
    state_root: Path | str,
    *,
    home: Any = _UNSET,
    reference_instant: Any = None,
    now: Any = None,
    subject_id: Optional[str] = None,
) -> dict[str, Any]:
    """Read ``s07_affect_records.json`` and project ``build_s07_affect_view`` with zero writes."""

    root_path = Path(str(state_root).strip()) if isinstance(state_root, (str, Path)) and str(state_root).strip() else None
    if home is _UNSET:
        effective_home: Any = state_root
        if root_path is not None and not root_path.is_dir() and root_path.parent.is_dir():
            effective_home = root_path.parent
    else:
        effective_home = home
    effective_now = reference_instant if reference_instant is not None else now
    records = read_affect_records_zero_write(state_root)
    return build_s07_affect_view(
        records,
        home=effective_home,
        subject_id=subject_id,
        reference_instant=effective_now,
    )


class S07AffectStore:
    """Thin convenience handle over the durable zero-tombstone S07 affect store."""

    def __init__(
        self,
        state_root: Path | str,
        *,
        home: Any = _UNSET,
        subject_id: Optional[str] = None,
    ) -> None:
        self.path = affect_store_path(state_root)
        self.state_root = Path(str(state_root).strip())
        self._home = home
        self.subject_id = subject_id

    def read_records_zero_write(self) -> list[Any]:
        return read_affect_records_zero_write(self.state_root)

    def record(self, **kwargs: Any) -> dict[str, Any]:
        if "home" not in kwargs and self._home is not _UNSET:
            kwargs["home"] = self._home
        if "subject_id" not in kwargs and self.subject_id is not None:
            kwargs["subject_id"] = self.subject_id
        return record_durable_affect(self.state_root, **kwargs)

    def project_zero_write(
        self,
        *,
        reference_instant: Any = None,
        now: Any = None,
    ) -> dict[str, Any]:
        return project_durable_s07_affect_zero_write(
            self.state_root,
            home=self._home,
            reference_instant=reference_instant,
            now=now,
            subject_id=self.subject_id,
        )


def _live_s07_state_root(day_root: Path | str) -> Path:
    root = Path(str(day_root).strip())
    life_dir = root / "life"
    if (life_dir / AFFECT_STORE_FILENAME).is_file():
        return life_dir
    if (root / AFFECT_STORE_FILENAME).is_file():
        return root
    return life_dir


def _resolve_live_subject_id_zero_write(day_root: Path | str) -> str:
    return body_capacity.resolve_subject_id(None)


def read_affect_summary_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Read a strictly zero-write summary of durable S07 affect records for ``day_root``."""
    state_root = _live_s07_state_root(day_root)
    store_file = affect_store_path(state_root)
    if not store_file.is_file() or store_file.is_symlink():
        return {
            "present": False,
            "entry_count": 0,
            "active_count": 0,
            "latest_kind": None,
            "latest_intensity": None,
            "latest_note": None,
            "latest_observed_at": None,
            "zero_tombstone": True,
            "llm_calls": 0,
        }
    records = read_affect_records_zero_write(state_root)
    subj = _resolve_live_subject_id_zero_write(day_root)
    ref_iso = now_iso
    if not (isinstance(ref_iso, str) and ref_iso.strip()):
        from datetime import timezone
        ref_iso = datetime.now(timezone.utc).isoformat()
    view = build_s07_affect_view(
        records,
        home=Path(str(day_root).strip()),
        subject_id=subj,
        reference_instant=ref_iso,
    )
    active_items = [
        item for item in (view.get("live_records") or [])
        if isinstance(item, Mapping)
    ]
    latest = active_items[-1] if active_items else (
        records[-1] if records and isinstance(records[-1], Mapping) else None
    )
    return {
        "present": True,
        "entry_count": len(records),
        "active_count": len(active_items),
        "latest_kind": latest.get("kind") if isinstance(latest, Mapping) else None,
        "latest_intensity": (
            latest.get("effective_intensity")
            if isinstance(latest, Mapping) and latest.get("effective_intensity") is not None
            else (latest.get("intensity") if isinstance(latest, Mapping) else None)
        ),
        "latest_note": latest.get("note") if isinstance(latest, Mapping) else None,
        "latest_observed_at": latest.get("observed_at") if isinstance(latest, Mapping) else None,
        "zero_tombstone": True,
        "llm_calls": 0,
    }


def record_live_affect_pulse(
    day_root: Path | str,
    *,
    source_ref: str,
    kind: str = AFFECT_KIND_AFFECT_PULSE,
    intensity: int = 35,
    note: str = "",
    ttl_hours: int = 6,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
    subject_id: Optional[str] = None,
) -> dict[str, Any]:
    """Record a bounded, sourced S07 affect pulse into ``<day_root>/life/s07_affect_records.json``."""
    from datetime import timedelta, timezone

    root = Path(str(day_root).strip())
    state_root = root / "life"
    if isinstance(now_iso, str) and now_iso.strip():
        ref_dt = _instant(now_iso.strip(), "now_iso")
    else:
        ref_dt = datetime.now(timezone.utc)
    bounded_ttl = max(1, min(72, int(ttl_hours)))
    exp_dt = ref_dt + timedelta(hours=bounded_ttl)
    bounded_intensity = max(1, min(INTENSITY_SCALE_HUNDREDTHS, int(intensity)))
    clean_kind = kind if kind in AFFECT_RECORD_KINDS else AFFECT_KIND_AFFECT_PULSE
    clean_ref = str(source_ref or "cycle:live").strip()
    if ":" not in clean_ref:
        clean_ref = f"cycle:{clean_ref}"
    clean_ref = clean_ref[:120]
    subj = _subject(subject_id) or _resolve_live_subject_id_zero_write(root)
    rec_hash = hashlib.sha256(
        f"{clean_kind}|{clean_ref}|{ref_dt.isoformat()}|{operation_key or ''}".encode("utf-8")
    ).hexdigest()[:12]
    return record_durable_affect(
        state_root,
        home=root,
        reference_instant=ref_dt.isoformat(),
        observed_at=ref_dt.isoformat(),
        subject_id=subj,
        record_id=f"affect:live_{rec_hash}",
        kind=clean_kind,
        intensity=bounded_intensity,
        source_refs=[clean_ref],
        expires_at=exp_dt.isoformat(),
        decay_mode=DECAY_MODE_LINEAR,
        decay_per_hour_milli=10000,
        note=(str(note or "").strip()[:180] or None),
        operation_key=operation_key,
    )
