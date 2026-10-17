#!/usr/bin/env python3
"""PERCEPT/SITUATION-1: one injectable, rebuildable "where am I right now" read.

What this module is
-------------------
A pure read projection over inputs the caller already owns:

* the real Body read (``body_capacity_source`` over ``WorldBodyReadPort.body()``),
* an already-obtained resource-budget read (``life_request_budget``),
* the observations that were actually acquired, each with its own source ref and
  declared validity,
* the explicit subject / home / reference instant.

Three columns stay separate: ``body``, ``resource_budget``, ``knowledge``.  No
"mood", no score, no merged capability number.  Nothing here is an owner: no
store, no cache, no Candidate, no action.

Rules this view keeps
---------------------
* **No positive body fact is invented.**  With no body input the body column is
  ``UNKNOWN`` / ``BODY_SOURCE_NOT_INJECTED``; it is never ``AVAILABLE``.  The
  mapping from real signals to capacity belongs to ``body_capacity_source`` and
  is reused, never redone here.
* **Budget is its own dimension.**  The resource column comes from the budget
  owner's own read (``LifeRequestBudget.view()`` / ``.check()``).  It is never
  reported as a body fact, and this module never calls ``reserve`` /
  ``ensure_window``: it only projects a read the caller already made.
* **A source fault is not a resource fact.**  A corrupt or unreadable ledger is
  ``UNKNOWN``, not "exhausted" (``REQUEST_BUDGET_STATE_CORRUPT`` /
  ``REQUEST_BUDGET_UNREADABLE`` are the owner's own fail-closed codes).
* **No invented freshness threshold.**  An observation is ``CURRENT`` only when
  its own source declared a ``valid_until`` that still holds; with no declared
  bound it is ``UNVERIFIED`` and with a passed bound ``EXPIRED``.  There is no
  number of hours anywhere in this module.
* **The observation labels are this projection's own.**  No S03 validity owner
  exists in the tree yet, so ``CURRENT`` / ``EXPIRED`` / ``UNVERIFIED`` /
  ``INVALID`` are reported as projection labels (``owner_asserted: false``),
  never as an owner's claim, and the declared instants are echoed unchanged.
* **Observations cannot smuggle authority.**  An observation carrying a body or
  affect fact (``body_capacity_state``, ``fatigue``, ``mood``, ...) is refused as
  ``INVALID`` instead of being accepted as knowledge.
* **Zero write authority.**  ``writes_performed`` / ``mutations_attempted`` are
  constants ``0``, ``candidates_emitted`` / ``actions_authorized`` are ``0``, and
  the module performs no file or store operation at all.

What AG-1 gets from it
----------------------
``ag1_capacity_kwargs(view)`` returns the body *and* the resource pairs, always
state together with reference, so ``CapacityEvaluator.evaluate_capacity``
(``agency_decision_ag1.py:1956``) cannot fabricate ``AVAILABLE`` from a bare
reference (:1977-1988).  Device and privacy dimensions are deliberately not
returned: they belong to their own owners and this package must not guess them.
An honest ``UNKNOWN`` is not a defer by itself (``unknown_capacity_policy``
defaults to ``ALLOW_WITH_UNKNOWN``, :1971); see ``body_capacity_source``.

Raises versus UNKNOWN
---------------------
``LifeSituationViewError`` is raised only for a caller/wiring fault: an illegal
subject, a home that is not a directory, an unparseable reference instant, a
malformed expected revision, a body port without ``body()``, or both ``body`` and
``body_port`` supplied at once.  Everything the world can get wrong is a field in
the returned view.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

import body_capacity_source as body_capacity  # noqa: E402

__all__ = [
    "BUDGET_FAULT_REASON_CODES",
    "DIMENSION_BODY",
    "DIMENSION_RESOURCE_BUDGET",
    "ECHO_OBSERVATION_FIELDS",
    "FORBIDDEN_OBSERVATION_FIELDS",
    "KIND_LIFE_SITUATION_VIEW",
    "REASON_BODY_SOURCE_NOT_INJECTED",
    "REASON_BODY_VIEW_INVALID",
    "REASON_KNOWLEDGE_CURRENT",
    "REASON_NO_REFERENCE_INSTANT",
    "REASON_OBSERVATION_AUTHORITY_FIELD",
    "REASON_OBSERVATION_DUPLICATE",
    "REASON_OBSERVATION_EXPIRED",
    "REASON_OBSERVATION_NOT_MAPPING",
    "REASON_OBSERVATION_REF_MISSING",
    "REASON_OBSERVATION_SOURCE_MISSING",
    "REASON_OBSERVATION_SUBJECT_MISMATCH",
    "REASON_OBSERVATION_TIME_AHEAD",
    "REASON_OBSERVATION_TIME_INVALID",
    "REASON_OBSERVATION_TIME_MISSING",
    "REASON_OBSERVATION_VALIDITY_INVALID",
    "REASON_OBSERVATION_VALIDITY_MISSING",
    "REASON_RESOURCE_BUDGET_ALLOWED",
    "REASON_RESOURCE_BUDGET_NOT_INJECTED",
    "REASON_RESOURCE_BUDGET_READ_UNUSABLE",
    "REASON_RESOURCE_BUDGET_REFUSED",
    "REASON_RESOURCE_BUDGET_SOURCE_MISSING",
    "REASON_RESOURCE_BUDGET_VERDICT_MISSING",
    "SCHEMA_LIFE_SITUATION_VIEW",
    "VALIDITY_CURRENT",
    "VALIDITY_EXPIRED",
    "VALIDITY_INVALID",
    "VALIDITY_UNVERIFIED",
    "VALID_KNOWLEDGE_VALIDITIES",
    "LifeSituationView",
    "LifeSituationViewError",
    "ag1_capacity_kwargs",
    "read_life_situation",
]

SCHEMA_LIFE_SITUATION_VIEW = "chiyo.life.situation_view.v1"
KIND_LIFE_SITUATION_VIEW = "life_situation_view"

#: The two capacity dimensions this package owns.  Device and privacy are other
#: owners' dimensions and are deliberately absent.
DIMENSION_BODY = "body"
DIMENSION_RESOURCE_BUDGET = "resource_budget"

# --------------------------------------------------------------------------
# knowledge validity: this projection's own labels (there is no S03 owner yet)
# --------------------------------------------------------------------------
VALIDITY_CURRENT = "CURRENT"
VALIDITY_EXPIRED = "EXPIRED"
VALIDITY_UNVERIFIED = "UNVERIFIED"
VALIDITY_INVALID = "INVALID"
VALID_KNOWLEDGE_VALIDITIES = frozenset(
    {VALIDITY_CURRENT, VALIDITY_EXPIRED, VALIDITY_UNVERIFIED, VALIDITY_INVALID}
)

# --------------------------------------------------------------------------
# reason codes (closed set; every column and every observation carries one)
# --------------------------------------------------------------------------
REASON_BODY_SOURCE_NOT_INJECTED = "BODY_SOURCE_NOT_INJECTED"
REASON_BODY_VIEW_INVALID = "BODY_VIEW_INVALID"

REASON_RESOURCE_BUDGET_ALLOWED = "RESOURCE_BUDGET_ALLOWED"
REASON_RESOURCE_BUDGET_REFUSED = "RESOURCE_BUDGET_REFUSED"
REASON_RESOURCE_BUDGET_VERDICT_MISSING = "RESOURCE_BUDGET_VERDICT_MISSING"
REASON_RESOURCE_BUDGET_READ_UNUSABLE = "RESOURCE_BUDGET_READ_UNUSABLE"
REASON_RESOURCE_BUDGET_NOT_INJECTED = "RESOURCE_BUDGET_NOT_INJECTED"
REASON_RESOURCE_BUDGET_SOURCE_MISSING = "RESOURCE_BUDGET_SOURCE_MISSING"

REASON_KNOWLEDGE_CURRENT = "OK_OBSERVATION_CURRENT"
REASON_OBSERVATION_NOT_MAPPING = "OBSERVATION_NOT_A_MAPPING"
REASON_OBSERVATION_REF_MISSING = "OBSERVATION_REF_MISSING"
REASON_OBSERVATION_DUPLICATE = "OBSERVATION_DUPLICATE_REF"
REASON_OBSERVATION_AUTHORITY_FIELD = "OBSERVATION_AUTHORITY_FIELD_LEAK"
REASON_OBSERVATION_SOURCE_MISSING = "OBSERVATION_SOURCE_MISSING"
REASON_OBSERVATION_SUBJECT_MISMATCH = "OBSERVATION_SUBJECT_MISMATCH"
REASON_OBSERVATION_TIME_MISSING = "OBSERVATION_TIME_MISSING"
REASON_OBSERVATION_TIME_INVALID = "OBSERVATION_TIME_INVALID"
REASON_OBSERVATION_TIME_AHEAD = "OBSERVATION_TIME_AHEAD"
REASON_OBSERVATION_VALIDITY_MISSING = "OBSERVATION_VALIDITY_UNVERIFIED"
REASON_OBSERVATION_VALIDITY_INVALID = "OBSERVATION_VALIDITY_INVALID"
REASON_OBSERVATION_EXPIRED = "OBSERVATION_VALIDITY_EXPIRED"
REASON_NO_REFERENCE_INSTANT = "NO_REFERENCE_INSTANT"

#: The budget owner's own fail-closed codes (``life_request_budget.py:68,72``).
#: A ledger that cannot be read is a source fault, never "the budget is spent".
BUDGET_FAULT_REASON_CODES = frozenset(
    {"REQUEST_BUDGET_STATE_CORRUPT", "REQUEST_BUDGET_UNREADABLE"}
)
#: ``LifeRequestBudget.view()`` ledger states (``life_request_budget.py:59-61``).
BUDGET_STATE_CORRUPT = "corrupt"

#: Keys an observation may not carry: accepting them would turn "I saw this" into
#: a body or affect fact, which is exactly what S02/S03 keep apart (02:41, 02:50).
FORBIDDEN_OBSERVATION_FIELDS = frozenset(
    {
        "body_capacity_state",
        "body_capacity_ref",
        "capacity",
        "capacity_state",
        "fatigue",
        "fatigue_load",
        "mood",
        "emotion",
        "affect",
        "feeling",
        "health",
        "preference",
        "relationship",
        "personality",
        "self_belief",
    }
)

#: Echo-only observation fields: copied through verbatim, never interpreted.
ECHO_OBSERVATION_FIELDS = ("declared_scope", "uncertainty")

_DERIVED_BODY = "body_capacity_source.read_body_capacity"
_DERIVED_BUDGET = "life_request_budget view()/check() (read already obtained by the caller)"
_DERIVED_KNOWLEDGE = "life_situation_view projection (no owner for observation validity)"


class LifeSituationViewError(ValueError):
    """A caller/wiring fault, not a situation the world produced."""


# --------------------------------------------------------------------------
# the shared rules (owned by body_capacity_source) with this module's error type
# --------------------------------------------------------------------------


def _structured_ref(value: Any, label: str) -> str:
    try:
        return body_capacity.require_structured_ref(value, label)
    except body_capacity.BodyCapacitySourceError as error:
        raise LifeSituationViewError(str(error)) from error


def _instant(value: Any, label: str) -> datetime:
    try:
        return body_capacity.require_instant(value, label)
    except body_capacity.BodyCapacitySourceError as error:
        raise LifeSituationViewError(str(error)) from error


def _subject(subject_id: Optional[str]) -> Optional[str]:
    try:
        return body_capacity.resolve_subject_id(subject_id)
    except body_capacity.BodyCapacitySourceError as error:
        raise LifeSituationViewError(str(error)) from error


def _home_ref(home: Any) -> str:
    try:
        return body_capacity.bind_home_ref(home)
    except body_capacity.BodyCapacitySourceError as error:
        raise LifeSituationViewError(str(error)) from error


# --------------------------------------------------------------------------
# columns
# --------------------------------------------------------------------------


def _body_column(
    body: Optional[Mapping[str, Any]],
    *,
    home: Any,
    subject_id: Optional[str],
    now: Any,
    body_port: Any,
    expected_body_id: Optional[str],
    expected_revision: Optional[str],
    world_scripts_dir: Optional[Any],
) -> tuple[dict[str, Any], int]:
    """The body column: the Body owner's answer as ``body_capacity_source`` reads it.

    Returns ``(column, reads_performed)``.  The column copies the adapter's facts
    verbatim — this module never re-derives capacity from signals and never turns
    an absent input into a positive one.
    """

    reads = 0
    if body is None and body_port is not None:
        body = body_capacity.read_body_capacity(
            body_port,
            home=home,
            subject_id=subject_id,
            now=now,
            expected_body_id=expected_body_id,
            expected_revision=expected_revision,
            world_scripts_dir=world_scripts_dir,
        )
        reads = 1

    if body is None:
        return (
            _column(
                state=body_capacity.CAPACITY_UNKNOWN,
                ref=None,
                source=None,
                reason=REASON_BODY_SOURCE_NOT_INJECTED,
                derived_by=_DERIVED_BODY,
            ),
            reads,
        )

    if not isinstance(body, Mapping) or (
        body.get("body_capacity_state") not in body_capacity.VALID_CAPACITY_STATES
    ):
        return (
            _column(
                state=body_capacity.CAPACITY_UNKNOWN,
                ref=None,
                source=None,
                reason=REASON_BODY_VIEW_INVALID,
                derived_by=_DERIVED_BODY,
            ),
            reads,
        )

    column = _column(
        state=body["body_capacity_state"],
        ref=body.get("body_capacity_ref"),
        source=body.get("source"),
        reason=body.get("reason_code"),
        derived_by=_DERIVED_BODY,
    )
    column.update(
        {
            "freshness": body.get("freshness"),
            "observed_at": body.get("observed_at"),
            "observed_body_id": body.get("observed_body_id"),
            "observed_revision": body.get("observed_revision"),
            "evidence": list(body.get("evidence") or []),
            "identity_bound": bool(body.get("identity_bound")),
            "revision_bound": bool(body.get("revision_bound")),
            "observation_time_bound": bool(body.get("observation_time_bound")),
        }
    )
    return column, reads


def _resource_column(budget: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The resource-budget column: the budget owner's own read, projected.

    Never merged into the body column and never a body fact.  A corrupt or
    unreadable ledger is ``UNKNOWN`` (the owner failed closed), not a refusal by
    the resource.
    """

    if budget is None:
        return _column(
            state=body_capacity.CAPACITY_UNKNOWN,
            ref=None,
            source=None,
            reason=REASON_RESOURCE_BUDGET_NOT_INJECTED,
            derived_by=_DERIVED_BUDGET,
            owner_asserted=False,
        )
    if not isinstance(budget, Mapping):
        return _column(
            state=body_capacity.CAPACITY_UNKNOWN,
            ref=None,
            source=None,
            reason=REASON_RESOURCE_BUDGET_SOURCE_MISSING,
            derived_by=_DERIVED_BUDGET,
            owner_asserted=False,
        )

    source = budget.get("source")
    if not isinstance(source, str) or not source.strip():
        return _column(
            state=body_capacity.CAPACITY_UNKNOWN,
            ref=None,
            source=None,
            reason=REASON_RESOURCE_BUDGET_SOURCE_MISSING,
            derived_by=_DERIVED_BUDGET,
            owner_asserted=False,
        )

    owner_reason = budget.get("reason_code")
    owner_reason = owner_reason.strip() if isinstance(owner_reason, str) else None
    ledger_state = budget.get("state")
    ledger_state = ledger_state.strip() if isinstance(ledger_state, str) else None
    allowed = budget.get("allowed")
    observed_at = budget.get("observed_at")
    observed_at = observed_at.strip() if isinstance(observed_at, str) else None

    faulted = ledger_state == BUDGET_STATE_CORRUPT or owner_reason in BUDGET_FAULT_REASON_CODES
    if faulted:
        state, reason = body_capacity.CAPACITY_UNKNOWN, REASON_RESOURCE_BUDGET_READ_UNUSABLE
    elif allowed is True:
        state, reason = body_capacity.CAPACITY_AVAILABLE, REASON_RESOURCE_BUDGET_ALLOWED
    elif allowed is False:
        state, reason = body_capacity.CAPACITY_UNAVAILABLE, REASON_RESOURCE_BUDGET_REFUSED
    else:
        state, reason = (
            body_capacity.CAPACITY_UNKNOWN,
            REASON_RESOURCE_BUDGET_VERDICT_MISSING,
        )

    ref = _budget_ref(budget)
    column = _column(
        state=state,
        ref=ref,
        source=source.strip(),
        reason=reason,
        derived_by=_DERIVED_BUDGET,
        owner_asserted=allowed is not None and not faulted,
    )
    column.update(
        {
            "owner_reason_code": owner_reason,
            "ledger_state": ledger_state,
            "utc_day": budget.get("utc_day") if isinstance(budget.get("utc_day"), str) else None,
            "run_id": budget.get("run_id") if isinstance(budget.get("run_id"), str) else None,
            "utc_day_calls": budget.get("utc_day_calls"),
            "utc_day_max": budget.get("utc_day_max"),
            "run_window_calls": budget.get("run_window_calls"),
            "run_window_max": budget.get("run_window_max"),
            "observed_at": observed_at,
            "bound": ref is not None,
        }
    )
    return column


def _budget_ref(budget: Mapping[str, Any]) -> Optional[str]:
    """A stable, path-free reference for the ledger read the caller obtained.

    Built only from what the owner's own read reported (``path`` digested, never
    echoed; ``utc_day``; ``run_id``; the day counter).  Without a reported
    ``utc_day`` there is no ledger identity to bind to and the caller gets
    ``None`` rather than an invented reference.
    """

    day = budget.get("utc_day")
    if not isinstance(day, str) or not day.strip():
        return None
    identity = budget.get("path")
    if isinstance(identity, str) and identity.strip():
        digest = hashlib.sha256(identity.strip().encode("utf-8")).hexdigest()[:12]
        ledger = "ledger:" + digest
    else:
        ledger = "ledger:unnamed"
    run_id = budget.get("run_id")
    run = run_id.strip() if isinstance(run_id, str) and run_id.strip() else "no-run"
    calls = budget.get("utc_day_calls")
    used = str(calls) if isinstance(calls, int) and not isinstance(calls, bool) else "unknown"
    return body_capacity.require_structured_ref(
        "budget:%s:%s:run:%s:calls:%s" % (ledger, day.strip(), run, used),
        "resource_budget_ref",
    )


def _knowledge_column(
    observations: Sequence[Any], *, now: Any, subject_id: Optional[str]
) -> dict[str, Any]:
    """The knowledge column: what was actually acquired, and how old it is.

    The labels are this projection's own (no S03 validity owner exists), so they
    are reported with ``owner_asserted: false`` and no threshold of any kind is
    applied: an observation is current only while its own declared bound holds.
    """

    items: list[dict[str, Any]] = []
    seen: set = set()
    for raw in observations or ():
        items.append(_observation_item(raw, now=now, subject_id=subject_id, seen=seen))

    counts = {label.lower(): 0 for label in sorted(VALID_KNOWLEDGE_VALIDITIES)}
    for item in items:
        counts[item["validity"].lower()] += 1

    current_refs = [i["observation_ref"] for i in items if i["validity"] == VALIDITY_CURRENT]
    not_current = [
        {
            "observation_ref": i["observation_ref"],
            "validity": i["validity"],
            "reason_code": i["reason_code"],
        }
        for i in items
        if i["validity"] != VALIDITY_CURRENT
    ]
    return {
        "observations": items,
        "current_refs": current_refs,
        "not_current": not_current,
        "counts": {"total": len(items), **counts},
        "derived_by": _DERIVED_KNOWLEDGE,
        "owner_asserted": False,
        "declared_bound_only": True,
        "local_threshold_used": False,
        "knowledge_is_not_a_capacity_dimension": True,
    }


def _observation_item(
    raw: Any, *, now: Any, subject_id: Optional[str], seen: set
) -> dict[str, Any]:
    """One acquired observation, classified.  First failure wins; fail closed."""

    item = {
        "observation_ref": None,
        "source_ref": None,
        "subject_id": None,
        "observed_at": None,
        "valid_until": None,
        "validity": VALIDITY_INVALID,
        "reason_code": REASON_OBSERVATION_NOT_MAPPING,
        "declared_scope": None,
        "uncertainty": None,
        "echoed_only": True,
    }
    for field in ECHO_OBSERVATION_FIELDS:
        if isinstance(raw, Mapping) and isinstance(raw.get(field), str):
            item[field] = raw[field]

    if not isinstance(raw, Mapping):
        return item

    ref = raw.get("observation_ref")
    if isinstance(ref, str) and ref.strip():
        item["observation_ref"] = ref.strip()
    if not isinstance(ref, str) or not ref.strip():
        item["reason_code"] = REASON_OBSERVATION_REF_MISSING
        return item
    try:
        body_capacity.require_structured_ref(item["observation_ref"], "observation_ref")
    except body_capacity.BodyCapacitySourceError:
        item["reason_code"] = REASON_OBSERVATION_REF_MISSING
        return item

    if item["observation_ref"] in seen:
        item["reason_code"] = REASON_OBSERVATION_DUPLICATE
        return item
    seen.add(item["observation_ref"])

    leaked = sorted(key for key in raw if key in FORBIDDEN_OBSERVATION_FIELDS)
    if leaked:
        item["reason_code"] = REASON_OBSERVATION_AUTHORITY_FIELD
        item["leaked_fields"] = leaked
        return item

    source_ref = raw.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref.strip():
        item["reason_code"] = REASON_OBSERVATION_SOURCE_MISSING
        return item
    try:
        item["source_ref"] = body_capacity.require_structured_ref(source_ref, "source_ref")
    except body_capacity.BodyCapacitySourceError:
        item["reason_code"] = REASON_OBSERVATION_SOURCE_MISSING
        return item

    raw_subject = raw.get("subject_id")
    if raw_subject is not None:
        raw_subject = raw_subject.strip() if isinstance(raw_subject, str) else raw_subject
        if subject_id is None or raw_subject != subject_id:
            item["subject_id"] = raw_subject if isinstance(raw_subject, str) else None
            item["reason_code"] = REASON_OBSERVATION_SUBJECT_MISMATCH
            return item
    # an observation that got this far belongs to the one verified subject
    item["subject_id"] = subject_id

    observed_instant = body_capacity.parse_instant(raw.get("observed_at"))
    if raw.get("observed_at") is None or (
        isinstance(raw.get("observed_at"), str) and not raw["observed_at"].strip()
    ):
        item["reason_code"] = REASON_OBSERVATION_TIME_MISSING
        return item
    if observed_instant is None:
        item["reason_code"] = REASON_OBSERVATION_TIME_INVALID
        return item
    item["observed_at"] = observed_instant.isoformat()

    declared = raw.get("valid_until")
    if declared is None or (isinstance(declared, str) and not declared.strip()):
        item["validity"] = VALIDITY_UNVERIFIED
        item["reason_code"] = REASON_OBSERVATION_VALIDITY_MISSING
        return item
    valid_until = body_capacity.parse_instant(declared)
    if valid_until is None:
        item["reason_code"] = REASON_OBSERVATION_VALIDITY_INVALID
        return item
    item["valid_until"] = valid_until.isoformat()
    if valid_until < observed_instant:
        item["reason_code"] = REASON_OBSERVATION_VALIDITY_INVALID
        return item

    reference = body_capacity.parse_instant(now)
    if reference is None:
        # Without an injected reference instant nothing about timeliness can be
        # decided; that is "unverified", never "current".
        item["validity"] = VALIDITY_UNVERIFIED
        item["reason_code"] = REASON_NO_REFERENCE_INSTANT
        return item
    if observed_instant > reference:
        item["reason_code"] = REASON_OBSERVATION_TIME_AHEAD
        return item
    if reference > valid_until:
        item["validity"] = VALIDITY_EXPIRED
        item["reason_code"] = REASON_OBSERVATION_EXPIRED
        return item

    item["validity"] = VALIDITY_CURRENT
    item["reason_code"] = REASON_KNOWLEDGE_CURRENT
    return item


def _column(
    *,
    state: str,
    ref: Optional[str],
    source: Optional[str],
    reason: Optional[str],
    derived_by: str,
    owner_asserted: bool = False,
) -> dict[str, Any]:
    """Every column has the same shape, so a caller never guesses."""

    if state not in body_capacity.VALID_CAPACITY_STATES:
        raise LifeSituationViewError(f"column state {state!r} is not an AG-1 capacity state")
    return {
        "state": state,
        "ref": ref,
        "source": source,
        "reason_code": reason,
        "derived_by": derived_by,
        "owner_asserted": bool(owner_asserted),
        "read_only": True,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
    }


# --------------------------------------------------------------------------
# the situation read
# --------------------------------------------------------------------------


def _identity_unbound_view(*, home_ref: Optional[str], reason: str) -> dict[str, Any]:
    """The one-subject rule could not be resolved: nothing can be attributed."""

    body_column = _column(
        state=body_capacity.CAPACITY_UNKNOWN,
        ref=None,
        source=None,
        reason=reason,
        derived_by=_DERIVED_BODY,
    )
    budget_column = _column(
        state=body_capacity.CAPACITY_UNKNOWN,
        ref=None,
        source=None,
        reason=reason,
        derived_by=_DERIVED_BUDGET,
    )
    return _assemble(
        subject_id=None,
        home_ref=home_ref,
        observed_at=None,
        identity_bound=False,
        body=body_column,
        budget=budget_column,
        knowledge={
            "observations": [],
            "current_refs": [],
            "not_current": [],
            "counts": {"total": 0},
            "derived_by": _DERIVED_KNOWLEDGE,
            "owner_asserted": False,
            "declared_bound_only": True,
            "local_threshold_used": False,
            "knowledge_is_not_a_capacity_dimension": True,
        },
        reads_performed=0,
        identity_reason=reason,
    )


def _assemble(
    *,
    subject_id: Optional[str],
    home_ref: Optional[str],
    observed_at: Optional[str],
    identity_bound: bool,
    body: Mapping[str, Any],
    budget: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    reads_performed: int,
    identity_reason: Optional[str] = None,
) -> dict[str, Any]:
    dimensions = {
        DIMENSION_BODY: body["state"],
        DIMENSION_RESOURCE_BUDGET: budget["state"],
    }
    limiting = [name for name, state in dimensions.items() if state == body_capacity.CAPACITY_LIMITED]
    blocking = [
        name for name, state in dimensions.items() if state == body_capacity.CAPACITY_UNAVAILABLE
    ]
    unknown = [name for name, state in dimensions.items() if state == body_capacity.CAPACITY_UNKNOWN]
    unknowns = [
        {"dimension": name, "reason_code": column["reason_code"]}
        for name, column in ((DIMENSION_BODY, body), (DIMENSION_RESOURCE_BUDGET, budget))
        if name in unknown
    ]
    return {
        "schema_version": SCHEMA_LIFE_SITUATION_VIEW,
        "kind": KIND_LIFE_SITUATION_VIEW,
        "subject_id": subject_id,
        "home_ref": home_ref,
        "observed_at": observed_at,
        "identity_bound": bool(identity_bound),
        "identity_reason_code": identity_reason,
        "source_revisions": {
            "home": home_ref,
            DIMENSION_BODY: body["ref"],
            DIMENSION_RESOURCE_BUDGET: budget["ref"],
        },
        "body": body,
        "resource_budget": budget,
        "knowledge": knowledge,
        "dimensions": dimensions,
        "limiting_dimensions": limiting,
        "blocking_dimensions": blocking,
        "unknown_dimensions": unknown,
        "unknowns": unknowns,
        "read_only": True,
        "reads_performed": int(reads_performed),
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
        "actions_authorized": 0,
        "persistent_store": None,
        "cache_held": False,
        "rebuildable": True,
    }


def read_life_situation(
    *,
    home: Any,
    subject_id: Optional[str] = None,
    now: Any = None,
    body: Optional[Mapping[str, Any]] = None,
    body_port: Any = None,
    expected_body_id: Optional[str] = None,
    expected_revision: Optional[str] = None,
    budget: Optional[Mapping[str, Any]] = None,
    observations: Sequence[Any] = (),
    world_scripts_dir: Optional[Any] = None,
) -> dict[str, Any]:
    """Build one situation view from already-owned inputs.

    ``body`` is a body-capacity view (``body_capacity_source``); ``body_port`` is
    the read-only World/Body port, read exactly once instead.  ``budget`` is an
    already-obtained budget read; ``observations`` are already-acquired
    observations.  ``now`` is injected by the caller — nothing here reads a wall
    clock.
    """

    if body is not None and body_port is not None:
        raise LifeSituationViewError("inject either body or body_port, not both")
    if body_port is not None and not callable(getattr(body_port, "body", None)):
        raise LifeSituationViewError(
            "body_port must expose the read-only method 'body' (WorldBodyReadPort.body)"
        )
    if expected_revision is not None:
        expected_revision = _structured_ref(expected_revision, "expected_revision")
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

    body_column, reads = _body_column(
        body,
        home=home,
        subject_id=resolved_subject,
        now=reference_iso,
        body_port=body_port,
        expected_body_id=expected_body_id,
        expected_revision=expected_revision,
        world_scripts_dir=world_scripts_dir,
    )
    budget_column = _resource_column(budget)
    knowledge = _knowledge_column(observations, now=reference_iso, subject_id=resolved_subject)

    return _assemble(
        subject_id=resolved_subject,
        home_ref=home_ref,
        observed_at=reference_iso,
        identity_bound=True,
        body=body_column,
        budget=budget_column,
        knowledge=knowledge,
        reads_performed=reads,
    )


def ag1_capacity_kwargs(
    view: Mapping[str, Any], *, unknown_capacity_policy: Optional[str] = None
) -> dict[str, Any]:
    """The capacity keywords ``CapacityEvaluator.evaluate_capacity`` wants.

    Always all four keys, state together with reference, so AG-1 cannot fabricate
    ``AVAILABLE`` from a reference alone (``agency_decision_ag1.py:1977-1988``).
    ``unknown_capacity_policy`` is forwarded only when stated explicitly; see
    ``body_capacity_source.ag1_capacity_kwargs`` for why ``BLOCK_ON_UNKNOWN``
    deserves care.
    """

    if not isinstance(view, Mapping) or "dimensions" not in view:
        raise LifeSituationViewError("view must be a life-situation view")
    body = view.get(DIMENSION_BODY)
    budget = view.get(DIMENSION_RESOURCE_BUDGET)
    if not isinstance(body, Mapping) or not isinstance(budget, Mapping):
        raise LifeSituationViewError("view must carry the body and resource_budget columns")
    kwargs = {
        "body_capacity_state": body.get("state"),
        "body_capacity_ref": body.get("ref"),
        "resource_availability_state": budget.get("state"),
        "resource_budget_ref": budget.get("ref"),
    }
    for name, state in (
        ("body_capacity_state", kwargs["body_capacity_state"]),
        ("resource_availability_state", kwargs["resource_availability_state"]),
    ):
        if state not in body_capacity.VALID_CAPACITY_STATES:
            raise LifeSituationViewError(f"{name} {state!r} is not an AG-1 capacity state")
    if unknown_capacity_policy is not None:
        if unknown_capacity_policy not in body_capacity.AG1_UNKNOWN_CAPACITY_POLICIES:
            raise LifeSituationViewError(
                f"unknown_capacity_policy {unknown_capacity_policy!r} is not an AG-1 policy"
            )
        kwargs["unknown_capacity_policy"] = unknown_capacity_policy
    return kwargs


class LifeSituationView:
    """Injectable situation reader: one instance, one home, one subject.

    Construction is where wiring faults happen (illegal subject, unusable home, a
    port without ``body()``); ``build`` is where the world's own answers become a
    view.  The object holds no cache: ``build`` re-reads the port, so a restart
    means "read the owner again" rather than "inherit a remembered situation".
    """

    def __init__(
        self,
        *,
        home: Any,
        subject_id: Optional[str] = None,
        body_port: Any = None,
        expected_body_id: Optional[str] = None,
        expected_revision: Optional[str] = None,
        world_scripts_dir: Optional[Any] = None,
    ) -> None:
        if body_port is not None and not callable(getattr(body_port, "body", None)):
            raise LifeSituationViewError(
                "body_port must expose the read-only method 'body' (WorldBodyReadPort.body)"
            )
        if expected_revision is not None:
            expected_revision = _structured_ref(expected_revision, "expected_revision")
        resolved_subject = _subject(subject_id)
        if resolved_subject is None:
            raise LifeSituationViewError(
                "the subject owner (activity_continuity) is unavailable; the subject rule "
                "cannot be verified, so no situation read is allowed"
            )
        self.home = home
        self.home_ref = _home_ref(home)
        self.subject_id = resolved_subject
        self.body_port = body_port
        self.expected_body_id = expected_body_id
        self.expected_revision = expected_revision
        self.world_scripts_dir = world_scripts_dir
        self.last_view: Optional[dict[str, Any]] = None

    def build(
        self,
        *,
        now: Any = None,
        body: Optional[Mapping[str, Any]] = None,
        budget: Optional[Mapping[str, Any]] = None,
        observations: Sequence[Any] = (),
        expected_revision: Optional[str] = None,
    ) -> dict[str, Any]:
        view = read_life_situation(
            home=self.home,
            subject_id=self.subject_id,
            now=now,
            body=body,
            body_port=None if body is not None else self.body_port,
            expected_body_id=self.expected_body_id,
            expected_revision=(
                self.expected_revision if expected_revision is None else expected_revision
            ),
            budget=budget,
            observations=observations,
            world_scripts_dir=self.world_scripts_dir,
        )
        self.last_view = view
        return view

    def ag1_capacity_kwargs(
        self,
        view: Optional[Mapping[str, Any]] = None,
        *,
        unknown_capacity_policy: Optional[str] = None,
    ) -> dict[str, Any]:
        return ag1_capacity_kwargs(
            self.build() if view is None else view,
            unknown_capacity_policy=unknown_capacity_policy,
        )
