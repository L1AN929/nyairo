#!/usr/bin/env python3
"""v0.4 C: the *one* real WAIT basis this instance can name -- and nothing else.

Why this module exists
----------------------
AG-1 only offers ``WAIT`` when its ``DecisionContextSnapshot`` carries a
``known_waiting_basis_ref`` (``agency_decision_ag1.py:2316-2321``), and AG-2 only adopts a WAIT
with a concrete ``waiting_spec`` (``life_integration_ag2.py:3321-3380``).  Both halves were
implemented and neither was ever fed, so WAIT was unreachable and a stopped-but-not-finished
Activity could only be paused or abandoned.

What counts as a known dependency (and what does not)
-----------------------------------------------------
Two sources only, both of them a *fact the owner already told this round*:

``WORK_HEAD_AFTER_NO_CHANGE``
    the foreground Activity continues a work whose writer answered ``no_change`` on the owner's
    **current** head (``life_step_readiness.REASON_WORK_NO_CHANGE``).  The dependency is: *this
    work gets a new version*.  The baseline is the head the owner reports now, never a version
    the model or this module remembered.
``READING_MATERIAL_NEXT_VERSION``
    the foreground Activity reads a material that is exhausted/revoked/stopped on its current
    revision (``READING_*``): *this material gets a new version*.

Everything else is deliberately **not** a basis: a World step that no adapter can execute, a
body/resource cell that is UNKNOWN, a budget that is spent, "maybe something will change later".
Those are facts about this caller or about the current instant, not a named dependency of the
current Activity, and turning them into a WAIT would be a self-invented future.

The vocabulary this module must obey
------------------------------------
LR-2 validates a WAIT's ``waiting_on_ref`` against the waiting-domain vocabulary
(``waiting_ref_vocab.VALID_WAITING_REF_PREFIXES``).  ``artifact:`` is **not** in it -- it is an
LR-5 result/action-target namespace -- so the reference is spelled ``wait_dep:`` (the dependency
family, ``waiting_ref_vocab.py:52``) and carries the object plus the baseline it is a dependency
on.  The same ref is used for the LR-4 condition *and* for the event that may satisfy it, because
LR-4 matches them by exact string equality (``activity_waiting_agenda.py:1679-1686``).

``SATISFIED`` is not something this module may decide
-----------------------------------------------------
``head_change_event`` returns an event **only** when the owner read really shows a different head
than the recorded baseline.  LR-4 then decides.  There is no timer, no deadline and no
"maybe it changed": an unreadable owner, an unchanged head, a revoked artifact and an UNKNOWN
result all produce ``None`` here, which can only ever leave the condition PENDING.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Mapping, Optional

SCHEMA_VERSION = "chiyo.life.wait_basis.v1"

#: The waiting-domain prefix this basis is spelled with (see the module docstring).
WAITING_ON_PREFIX = "wait_dep"
#: The basis reference handed to AG-1 as ``known_waiting_basis_ref``.
BASIS_REF_PREFIX = "waitbasis"
#: The LR-4 condition kind this basis produces.  The dependency is an external result (a new
#: version of a named object), never a clock.
CONDITION_KIND = "EXTERNAL_RESULT_EVENT"
EVENT_CATEGORY = "EXTERNAL_RESULT_EVENT"
EVENT_KIND_ARTIFACT_HEAD_CHANGED = "ARTIFACT_HEAD_CHANGED"

#: The two -- and only two -- sources of a basis.
SOURCE_WORK_HEAD = "WORK_HEAD_AFTER_NO_CHANGE"
SOURCE_READING = "READING_MATERIAL_NEXT_VERSION"
BASIS_SOURCES = (SOURCE_WORK_HEAD, SOURCE_READING)

#: The reading states that really are "this material owes me a next version".  Everything else
#: (``SOURCE_REVOKED``, ``MATERIAL_NOT_ACTIVE``, ``MATERIAL_EMPTY``, ``POSITION_OUT_OF_RANGE``,
#: ``PRIVATE_ITEMS_UNAVAILABLE``, ``OWNER_UNAVAILABLE``/``OWNER_REFUSED``, ``READING_NOT_BOUND``,
#: ``UNKNOWN``) is *not* a dependency on a future version: a revoked source will never deliver
#: one, and an unknown state is not a known dependency at all.
READING_WAIT_STATES = ("EXHAUSTED",)

#: ``ResumeCondition.parameters`` keys this module owns.  They are read back by the maintain
#: step to re-check the *same* baseline; none of them is a predicate LR-4 has to interpret.
PARAM_SOURCE = "basis_source"
PARAM_ARTIFACT_REF = "artifact_ref"
PARAM_BASELINE_HEAD = "baseline_head_ref"


def _token(value: Any, length: int = 16) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:length]


def _bare_artifact_id(value: Any) -> str:
    """The owner's own artifact id, with the ``artifact:`` wrapper stripped once."""

    text = str(value or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def waiting_on_ref(*, artifact_id: Any, baseline_head: Any) -> str:
    """``wait_dep:<identity>.<baseline>`` -- one dependency, one spelling, both ends the same.

    The identity is hashed so an owner id can never be confused with a prefix, and the baseline
    is part of the ref: waiting for "a new version of X" after version A is a *different*
    dependency from waiting after version B, and a stale ref cannot satisfy a newer condition.
    """

    identity = _token(_bare_artifact_id(artifact_id))
    baseline = _token(baseline_head)
    return "%s:artifact.%s.%s" % (WAITING_ON_PREFIX, identity, baseline)


def basis_ref(*, source: str, artifact_id: Any, baseline_head: Any) -> str:
    """The reference AG-1 receives: it names the dependency, not a hope for one."""

    raw = "%s|%s|%s|%s" % (SCHEMA_VERSION, source, _bare_artifact_id(artifact_id),
                           str(baseline_head or ""))
    return "%s:%s" % (BASIS_REF_PREFIX, _token(raw))


def _basis(*, source: str, artifact_id: Any, baseline_head: Any,
           activity_ref: Optional[str], detail: str) -> Optional[dict[str, Any]]:
    artifact = _bare_artifact_id(artifact_id)
    baseline = str(baseline_head or "")
    if source not in BASIS_SOURCES or not artifact or not baseline:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "basis_ref": basis_ref(source=source, artifact_id=artifact, baseline_head=baseline),
        "source": source,
        "artifact_ref": "artifact:%s" % artifact,
        "artifact_id": artifact,
        "baseline_head_ref": baseline,
        "waiting_on_ref": waiting_on_ref(artifact_id=artifact, baseline_head=baseline),
        "condition_kind": CONDITION_KIND,
        "activity_ref": str(activity_ref) if activity_ref else None,
        "detail": str(detail or ""),
    }


def work_head_basis(*, artifact_id: Any, baseline_head: Any,
                    activity_ref: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The basis for a work whose writer confirmed ``no_change`` on this head."""

    return _basis(source=SOURCE_WORK_HEAD, artifact_id=artifact_id, baseline_head=baseline_head,
                  activity_ref=activity_ref,
                  detail="the work is at this head and its writer confirmed no_change on it")


def reading_basis(*, artifact_id: Any, baseline_head: Any,
                  state: Optional[Any] = None,
                  activity_ref: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The basis for a reading material whose current revision has nothing left to read."""

    return _basis(source=SOURCE_READING, artifact_id=artifact_id, baseline_head=baseline_head,
                  activity_ref=activity_ref,
                  detail="the material is %s on this revision" % (str(state or "UNKNOWN"),))


def basis_from_readiness(readiness: Any, *, artifact_id: Any = None,
                         baseline_head: Any = None, state: Optional[Any] = None,
                         activity_ref: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Map one read-only ``StepReadiness`` to a basis, or ``None``.

    Only the two named sources qualify.  The mapping is from the *readiness reason* -- the
    cycle's own report of what the owner said -- and never from "the Activity exists".
    """

    reason = str(getattr(readiness, "reason_code", "") or "")
    available = getattr(readiness, "available", None)
    ref = str(getattr(readiness, "target_ref", "") or "")
    owner_artifact = artifact_id if artifact_id is not None else ref
    owner_head = baseline_head if baseline_head is not None else getattr(
        readiness, "source_revision", None)
    activity = activity_ref if activity_ref is not None else getattr(
        readiness, "activity_ref", None)
    if available is not False:
        return None
    if reason == "WORK_NO_CHANGE_CONFIRMED":
        return work_head_basis(artifact_id=owner_artifact, baseline_head=owner_head,
                               activity_ref=activity)
    if reason.startswith("READING_"):
        # Only "read to the end of this revision" is a dependency on the material's *next*
        # version.  A revoked source, an inactive/empty material, an unreadable private document
        # and an UNKNOWN state are not: nothing will arrive, and a wait on them would be a wait
        # on nothing.  The state token is the readiness reason with its ``READING_`` prefix
        # removed, exactly as ``life_step_readiness`` spells it.
        state_token = str(state or reason[len("READING_"):]).upper()
        if state_token not in READING_WAIT_STATES:
            return None
        return reading_basis(artifact_id=owner_artifact, baseline_head=owner_head,
                             state=state_token, activity_ref=activity)
    return None


def condition_parameters(basis: Mapping[str, Any]) -> dict[str, Any]:
    """The ``ResumeCondition.parameters`` for this basis (bounded, no text, no deadline).

    ``waiting_on_ref`` is repeated here because LR-4's EXTERNAL_RESULT_EVENT leaf reads its
    expected ref from the parameters first (``activity_waiting_agenda.py:1682``).
    """

    return {
        "waiting_on_ref": str(basis["waiting_on_ref"]),
        PARAM_SOURCE: str(basis["source"]),
        PARAM_ARTIFACT_REF: str(basis["artifact_ref"]),
        PARAM_BASELINE_HEAD: str(basis["baseline_head_ref"]),
    }


def waiting_spec(basis: Mapping[str, Any]) -> dict[str, Any]:
    """The ``waiting_spec`` AG-2 hands to ``enter_waiting_with_condition``.

    Deliberately absent: ``condition_expires_at`` and ``schedule_agenda``.  A dependency on
    another owner's next version has no deadline this tree may invent, and no agenda item is
    scheduled for it, so nothing here is a timer pretending to be autonomy.
    """

    return {
        "waiting_on_ref": str(basis["waiting_on_ref"]),
        "condition_kind": CONDITION_KIND,
        "parameters": condition_parameters(basis),
        # The foreground stays with the waiting Activity unless the owner's own contract decides
        # otherwise; this module never releases it on her behalf.
        "release_foreground": False,
    }


def is_artifact_head_condition(condition: Mapping[str, Any]) -> bool:
    """Is this LR-4 condition one of *this* module's dependency conditions?"""

    parameters = condition.get("parameters")
    if not isinstance(parameters, Mapping):
        return False
    if str(parameters.get(PARAM_SOURCE) or "") not in BASIS_SOURCES:
        return False
    return bool(str(parameters.get(PARAM_ARTIFACT_REF) or "").startswith("artifact:")
                and str(parameters.get(PARAM_BASELINE_HEAD) or ""))


def head_change_event(basis: Mapping[str, Any], *, current_head: Any, observed_at: str,
                      event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """One LR-4 event **only** if the owner read really shows a new head.

    ``None`` is the answer for: no head, the same head (no change), an unreadable owner (the
    caller passes no head at all) and a missing baseline.  A same-head observation is exactly the
    "same string matched by accident" case that must never satisfy a condition.
    """

    baseline = str(basis.get("baseline_head_ref") or "")
    observed = str(current_head or "")
    if not baseline or not observed or observed == baseline:
        return None
    ref = str(basis.get("waiting_on_ref") or "")
    if not ref:
        return None
    parent = str(basis.get("basis_ref") or "")
    event = event_id or "evt:waitbasis.%s" % _token("%s|%s" % (ref, observed))
    if parent:
        return {
            "event_id": str(event),
            "event_category": EVENT_CATEGORY,
            "event_kind": EVENT_KIND_ARTIFACT_HEAD_CHANGED,
            "occurred_at": str(observed_at),
            "observed_at": str(observed_at),
            "recorded_at": str(observed_at),
            "enqueued_at": str(observed_at),
            "source_refs": [str(event), parent],
            "causal_parent_refs": [parent],
            "waiting_on_ref": ref,
            "is_observed_by_chiyo": True,
            "attributes": {
                "artifact_ref": str(basis.get("artifact_ref") or ""),
                "baseline_head_ref": baseline,
                "observed_head_ref": observed,
            },
        }
    return {
        "event_id": str(event),
        "event_category": EVENT_CATEGORY,
        "event_kind": EVENT_KIND_ARTIFACT_HEAD_CHANGED,
        "occurred_at": str(observed_at),
        "observed_at": str(observed_at),
        "recorded_at": str(observed_at),
        "enqueued_at": str(observed_at),
        "source_refs": [str(event)],
        "causal_parent_refs": [],
        "waiting_on_ref": ref,
        "is_observed_by_chiyo": True,
        "attributes": {
            "artifact_ref": str(basis.get("artifact_ref") or ""),
            "baseline_head_ref": baseline,
            "observed_head_ref": observed,
        },
    }


# =====================================================================================
# v0.4 A (pacing): the *body* dependency -- "put this down while my body is like this"
# =====================================================================================
#
# Why this is a dependency and not a timer
# ----------------------------------------
# A chosen clock instant says nothing about a body.  "She set it down for forty minutes and
# then said she was rested" is exactly the claim this tree refuses to make: a session is not
# recovery and an elapsed interval is not a measurement.  What *can* be waited on is a fact the
# Body owner itself publishes: the burden signals it reported and the qualitative band of the
# recovery session it is running (``world_body_read_port`` -> ``body_capacity_source`` ->
# ``life_body_context``).  So the basis below records that observation as its baseline, and the
# only thing that can satisfy it is a *newer* read of the **same** body that really shows easing.
#
# What may become a basis
# -----------------------
# Only a body the owner really reported as ``LIMITED`` with at least one named burden signal, and
# only with a body identity, a body revision and an observation instant.  An UNKNOWN capacity, a
# read with no identity/revision, a quiet body with no burden signal and a body that is fine all
# produce ``None``: "no signal" is not health, and an unread body is not a reason to wait.
#
# What may satisfy it (and what may not)
# --------------------------------------
# ``body_easing_event`` answers ``None`` for: a missing or different body identity (wrong body, no
# identity), a non-FRESH read, an unchanged body revision (nothing happened), an observation that
# is not later than the baseline, and a body whose evidence did not move.  Capacity alone is
# **not** used: measured on the real owner, capacity moves ``LIMITED -> UNKNOWN -> LIMITED`` while
# one session runs (the burden that remains is a different signal), so a capacity token would both
# miss real easing and invent it.  The qualifying evidence is the owner's own words -- a higher
# recovery progress band, a named burden signal that is no longer reported or whose band got
# lighter, and an improving trend where the baseline had none.
#
# The event is only ever a *right to ask*: LR-4 turns it into an OPEN resume eligibility and the
# next Decision is still hers.  Nothing here resumes, replays or claims recovery.

#: The third basis source (the two above are dependencies on another owner's next version).
SOURCE_BODY_REST = "BODY_CAPACITY_RESTED"
#: The reference AG-1 receives for a body-conditioned breakpoint.
BODY_BASIS_REF_PREFIX = "bodybasis"
EVENT_KIND_BODY_EASED = "BODY_CAPACITY_EASED"

#: ``ResumeCondition.parameters`` keys this half owns (all plain, bounded values).
PARAM_BODY_REF = "body_ref"
PARAM_BASELINE_BODY_REF = "baseline_body_ref"
PARAM_BASELINE_REVISION = "baseline_body_revision"
PARAM_BASELINE_CAPACITY = "baseline_capacity"
PARAM_BASELINE_BURDEN = "baseline_burden"
PARAM_BASELINE_PROGRESS = "baseline_progress"
PARAM_BASELINE_TREND = "baseline_trend"
PARAM_BASELINE_OBSERVED_AT = "baseline_observed_at"

#: The one capacity token that may open a body-conditioned breakpoint.
_BODY_LIMITED = "LIMITED"
_FRESH = "FRESH"

#: ``body_signal.BANDS`` order, mirrored (mild < noticeable < strong).  Used only to compare two
#: bands the owner itself emitted -- never to derive one.
PROGRESS_BAND_RANK = {"mild": 1, "noticeable": 2, "strong": 3}
#: The owner's own improving trend word (``body_runtime.RECOVERY_TRENDS``).
EASING_TRENDS = ("improving",)


def _band_rank(value: Any) -> int:
    return PROGRESS_BAND_RANK.get(str(value or "").strip().lower(), 0)


def _burden_rows(value: Any) -> list[dict[str, str]]:
    """Bounded ``[{signal, band}]`` rows from ``life_body_context``'s own shape."""

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in list(value or ()):
        if isinstance(item, Mapping):
            signal = str(item.get("signal") or "").strip()
            band = str(item.get("band") or "").strip()
        else:
            text = str(item or "").strip()
            if ":" not in text:
                continue
            signal, band = text.split(":", 1)
        if not signal or not band or (signal, band) in seen:
            continue
        seen.add((signal, band))
        rows.append({"signal": signal, "band": band})
        if len(rows) >= 4:
            break
    return rows


def body_baseline_token(basis: Mapping[str, Any]) -> str:
    """One token for the whole baseline (identity + revision + evidence)."""

    burden = ";".join("%s:%s" % (row["signal"], row["band"])
                      for row in _burden_rows(basis.get("baseline_burden")))
    raw = "|".join([
        SCHEMA_VERSION, SOURCE_BODY_REST,
        str(basis.get("baseline_body_ref") or basis.get("body_id") or ""),
        str(basis.get("baseline_revision") or ""),
        str(basis.get("baseline_capacity") or ""),
        burden,
        str(basis.get("baseline_progress") or ""),
        str(basis.get("baseline_trend") or ""),
    ])
    return _token(raw)


def body_waiting_on_ref(*, body_id: Any, baseline_token: str) -> str:
    """``wait_dep:body.<identity>.<baseline>`` -- one dependency, both ends spelled the same."""

    return "%s:body.%s.%s" % (WAITING_ON_PREFIX, _token(str(body_id or "").strip()),
                              str(baseline_token or ""))


def body_capacity_basis(*, body_id: Any, revision: Any, capacity: Any,
                        burden: Any = (), recovery_progress: Any = None,
                        trend: Any = None, session: Any = None,
                        observed_at: Any, activity_ref: Optional[str] = None
                        ) -> Optional[dict[str, Any]]:
    """The basis for "this Activity waits until *my own body* is not like this" -- or ``None``.

    ``None`` is the answer for every input this tree may not build a future out of: no body
    identity, no revision, no observation instant, a capacity the owner did not report as
    ``LIMITED`` (UNKNOWN / AVAILABLE / UNAVAILABLE included) and a body with no named burden
    signal.  The baseline is what the owner said *now*; nothing is remembered from a past round.
    """

    body = str(body_id or "").strip()
    rev = str(revision or "").strip()
    observed = str(observed_at or "").strip()
    if not body or not rev or not observed:
        return None
    if str(capacity or "").strip().upper() != _BODY_LIMITED:
        return None
    rows = _burden_rows(burden)
    if not rows:
        return None
    basis = {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_BODY_REST,
        "body_id": body,
        "body_ref": "body:%s" % body,
        "baseline_body_ref": body,
        "baseline_revision": rev,
        "baseline_capacity": _BODY_LIMITED,
        "baseline_burden": rows,
        "baseline_progress": (str(recovery_progress) if recovery_progress else None),
        "baseline_trend": (str(trend) if trend else None),
        "baseline_session": (str(session) if session else None),
        "baseline_observed_at": observed,
        "condition_kind": CONDITION_KIND,
        "activity_ref": str(activity_ref) if activity_ref else None,
        "detail": ("the Body owner reports %s as LIMITED with %d named burden signal(s); this "
                   "Activity waits on the same body really easing"
                   % (body, len(rows))),
    }
    token = body_baseline_token(basis)
    basis["baseline_ref"] = "bodybase:%s" % token
    basis["waiting_on_ref"] = body_waiting_on_ref(body_id=body, baseline_token=token)
    basis["basis_ref"] = "%s:%s" % (BODY_BASIS_REF_PREFIX, _token(
        "|".join([SCHEMA_VERSION, SOURCE_BODY_REST, token])))
    return basis


def body_condition_parameters(basis: Mapping[str, Any]) -> dict[str, Any]:
    """The ``ResumeCondition.parameters`` for a body-conditioned breakpoint (no deadline)."""

    rows = _burden_rows(basis.get("baseline_burden"))
    return {
        "waiting_on_ref": str(basis["waiting_on_ref"]),
        PARAM_SOURCE: SOURCE_BODY_REST,
        PARAM_BODY_REF: str(basis.get("body_ref") or ""),
        PARAM_BASELINE_BODY_REF: str(basis.get("baseline_body_ref") or ""),
        PARAM_BASELINE_REVISION: str(basis.get("baseline_revision") or ""),
        PARAM_BASELINE_CAPACITY: str(basis.get("baseline_capacity") or ""),
        PARAM_BASELINE_BURDEN: ["%s:%s" % (row["signal"], row["band"]) for row in rows],
        PARAM_BASELINE_PROGRESS: str(basis.get("baseline_progress") or ""),
        PARAM_BASELINE_TREND: str(basis.get("baseline_trend") or ""),
        PARAM_BASELINE_OBSERVED_AT: str(basis.get("baseline_observed_at") or ""),
    }


def body_waiting_spec(basis: Mapping[str, Any]) -> dict[str, Any]:
    """The AG-2 ``waiting_spec`` for a body-conditioned breakpoint.

    Deliberately absent: ``condition_expires_at`` and ``schedule_agenda``.  A body does not owe a
    deadline, and no agenda item is scheduled for it -- so nothing here is a timer pretending to
    be a body measurement.  ``release_foreground`` is **true**: the Activity keeps its place (its
    waiting pointer, its material and its page) while the slot is free for a real posture or rest
    step, which is the whole point of a breakpoint.  Nothing resumes by itself.
    """

    return {
        "waiting_on_ref": str(basis["waiting_on_ref"]),
        "condition_kind": CONDITION_KIND,
        "parameters": body_condition_parameters(basis),
        "release_foreground": True,
    }


def is_body_capacity_condition(condition: Mapping[str, Any]) -> bool:
    """Is this LR-4 condition one of *this* module's body breakpoints?"""

    parameters = condition.get("parameters")
    if not isinstance(parameters, Mapping):
        return False
    if str(parameters.get(PARAM_SOURCE) or "") != SOURCE_BODY_REST:
        return False
    return bool(str(parameters.get(PARAM_BASELINE_BODY_REF) or "")
                and str(parameters.get(PARAM_BASELINE_REVISION) or "")
                and str(parameters.get("waiting_on_ref") or condition.get("waiting_on_ref")
                         or ""))


def body_basis_from_condition(condition: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Rebuild the body baseline a stored LR-4 condition was bound to (maintain only)."""

    if not is_body_capacity_condition(condition):
        return None
    parameters = condition.get("parameters") or {}
    waiting = str(parameters.get("waiting_on_ref") or condition.get("waiting_on_ref") or "")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_BODY_REST,
        "body_id": str(parameters.get(PARAM_BASELINE_BODY_REF) or ""),
        "body_ref": str(parameters.get(PARAM_BODY_REF) or ""),
        "baseline_body_ref": str(parameters.get(PARAM_BASELINE_BODY_REF) or ""),
        "baseline_revision": str(parameters.get(PARAM_BASELINE_REVISION) or ""),
        "baseline_capacity": str(parameters.get(PARAM_BASELINE_CAPACITY) or ""),
        "baseline_burden": _burden_rows(parameters.get(PARAM_BASELINE_BURDEN)),
        "baseline_progress": str(parameters.get(PARAM_BASELINE_PROGRESS) or "") or None,
        "baseline_trend": str(parameters.get(PARAM_BASELINE_TREND) or "") or None,
        "baseline_observed_at": str(parameters.get(PARAM_BASELINE_OBSERVED_AT) or ""),
        "waiting_on_ref": waiting,
        "condition_kind": CONDITION_KIND,
        "activity_ref": str(condition.get("activity_id") or "") or None,
        "detail": "re-read from the LR-4 condition's own parameters",
    }


def _later(observed_at: Any, baseline_at: Any) -> bool:
    """Is ``observed_at`` really after the baseline?  Unparseable instants answer *no*."""

    try:
        observed = str(observed_at or "").replace("Z", "+00:00")
        baseline = str(baseline_at or "").replace("Z", "+00:00")
        if not observed or not baseline:
            return False
        return datetime.fromisoformat(observed) > datetime.fromisoformat(baseline)
    except (TypeError, ValueError):
        return False


def body_easing_event(basis: Mapping[str, Any], *, observed_body_id: Any,
                      observed_revision: Any, burden: Any = (), capacity: Any = None,
                      recovery_progress: Any = None, trend: Any = None,
                      freshness: Any = None, observed_at: str,
                      event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """One LR-4 event **only** when a newer read of the same body really shows easing.

    ``None`` for: a different or missing body identity, a non-FRESH read, the same body revision
    (nothing was measured since the baseline), an observation that is not later than the baseline,
    and a body whose own evidence did not move.  An UNREAD body therefore cannot satisfy the
    condition, and neither can the same observation being evaluated twice.
    """

    if str(basis.get("source") or "") != SOURCE_BODY_REST:
        return None
    body = str(basis.get("baseline_body_ref") or "").strip()
    seen_body = str(observed_body_id or "").strip()
    if not body or not seen_body or seen_body != body:
        return None
    if freshness is not None and str(freshness).strip().upper() != _FRESH:
        return None
    revision = str(observed_revision or "").strip()
    if not revision or revision == str(basis.get("baseline_revision") or ""):
        return None
    if not _later(observed_at, basis.get("baseline_observed_at")):
        return None
    observed_at = str(observed_at)
    eased: list[str] = []
    if _band_rank(recovery_progress) > _band_rank(basis.get("baseline_progress")):
        eased.append("PROGRESS_BAND_ADVANCED")
    now_bands = {row["signal"]: row["band"] for row in _burden_rows(burden)}
    for row in _burden_rows(basis.get("baseline_burden")):
        signal, band = row["signal"], row["band"]
        if signal not in now_bands:
            eased.append("BURDEN_%s_NOT_REPORTED" % signal.upper())
        elif _band_rank(now_bands[signal]) and _band_rank(now_bands[signal]) < _band_rank(band):
            eased.append("BURDEN_%s_LIGHTER" % signal.upper())
    now_trend = str(trend or "").strip().lower()
    base_trend = str(basis.get("baseline_trend") or "").strip().lower()
    if now_trend and now_trend in EASING_TRENDS and base_trend not in EASING_TRENDS:
        eased.append("TREND_%s" % now_trend.upper())
    if not eased:
        return None
    ref = str(basis.get("waiting_on_ref") or "")
    if not ref:
        return None
    parent = str(basis.get("basis_ref") or "")
    event = event_id or "evt:bodybasis.%s" % _token("%s|%s" % (ref, revision))
    return {
        "event_id": str(event),
        "event_category": EVENT_CATEGORY,
        "event_kind": EVENT_KIND_BODY_EASED,
        "occurred_at": observed_at,
        "observed_at": observed_at,
        "recorded_at": observed_at,
        "enqueued_at": observed_at,
        "source_refs": [str(event)] + ([parent] if parent else []),
        "causal_parent_refs": [parent] if parent else [],
        "waiting_on_ref": ref,
        "is_observed_by_chiyo": True,
        "attributes": {
            "body_ref": str(basis.get("body_ref") or ""),
            "baseline_body_revision": str(basis.get("baseline_revision") or ""),
            "observed_body_revision": revision,
            "observed_capacity": (str(capacity) if capacity else None),
            "eased_by": sorted(set(eased)),
        },
    }


def basis_from_condition(condition: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Rebuild the basis a stored LR-4 condition was bound to (report/maintain only).

    The condition is the owner's record; this only re-reads the parameters *this* module wrote,
    so the maintain step can re-check the same baseline instead of remembering one.
    """

    if not is_artifact_head_condition(condition):
        return None
    parameters = condition.get("parameters") or {}
    artifact_ref = str(parameters.get(PARAM_ARTIFACT_REF) or "")
    baseline = str(parameters.get(PARAM_BASELINE_HEAD) or "")
    source = str(parameters.get(PARAM_SOURCE) or "")
    return _basis(source=source, artifact_id=artifact_ref, baseline_head=baseline,
                  activity_ref=str(condition.get("activity_id") or "") or None,
                  detail="re-read from the LR-4 condition's own parameters")


__all__ = [
    "BASIS_REF_PREFIX",
    "BASIS_SOURCES",
    "BODY_BASIS_REF_PREFIX",
    "CONDITION_KIND",
    "EASING_TRENDS",
    "EVENT_CATEGORY",
    "EVENT_KIND_ARTIFACT_HEAD_CHANGED",
    "EVENT_KIND_BODY_EASED",
    "PARAM_ARTIFACT_REF",
    "PARAM_BASELINE_BODY_REF",
    "PARAM_BASELINE_BURDEN",
    "PARAM_BASELINE_CAPACITY",
    "PARAM_BASELINE_HEAD",
    "PARAM_BASELINE_OBSERVED_AT",
    "PARAM_BASELINE_PROGRESS",
    "PARAM_BASELINE_REVISION",
    "PARAM_BASELINE_TREND",
    "PARAM_BODY_REF",
    "PARAM_SOURCE",
    "PROGRESS_BAND_RANK",
    "SCHEMA_VERSION",
    "SOURCE_BODY_REST",
    "SOURCE_READING",
    "SOURCE_WORK_HEAD",
    "WAITING_ON_PREFIX",
    "basis_from_condition",
    "basis_from_readiness",
    "basis_ref",
    "body_basis_from_condition",
    "body_baseline_token",
    "body_capacity_basis",
    "body_condition_parameters",
    "body_easing_event",
    "body_waiting_on_ref",
    "body_waiting_spec",
    "condition_parameters",
    "head_change_event",
    "is_artifact_head_condition",
    "is_body_capacity_condition",
    "reading_basis",
    "waiting_on_ref",
    "waiting_spec",
    "work_head_basis",
]
