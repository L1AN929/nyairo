#!/usr/bin/env python3
"""BODY-CAP-1: narrow read adapter from the real World/Body read port to the
AG-1 body-capacity vocabulary.

What this module is
-------------------
One translation and nothing else: the already-existing, read-only World/Body
port (``components/native/integration/world_body_read_port.py``, whose
``body()`` speaks the gateway command ``get_body_signals``) becomes the one
``body_capacity_state`` / ``body_capacity_ref`` pair AG-1's
``CapacityEvaluator.evaluate_capacity`` accepts
(``agency_decision_ag1.py:1955-1980``).

Contract this adapter keeps
---------------------------
* **One read, zero writes.**  ``read_body_capacity`` performs exactly one read
  (``port.body()``) per call.  It never touches a mutation verb; the port it is
  given has none (``world_body_read_port.py:14-16``), and the returned view says
  so (``writes_performed`` / ``mutations_attempted`` are always ``0``).
* **No second Body store.**  Nothing is persisted and nothing is carried between
  calls, so a restart re-reads the owner and cannot inherit a cached body.
* **Budget is not an input.**  There is no budget/token/quota parameter here.
  Resource budget is a *different* AG-1 dimension
  (``agency_decision_ag1.py:1964-1965``) and must never be reported as fatigue.
* **A qualitative band never becomes a hard stop.**  A band can only ever lower
  capacity to ``LIMITED``; it can never produce ``UNAVAILABLE``.
* **No signal is not health.**  An available body with no burden signal yields
  ``UNKNOWN``, never ``AVAILABLE`` (see ``CHIYO-施工规划/05-身体与情绪现状勘察.md``
  §1: "不能无信号就推出身体健康").
* **No Candidate, no authority.**  Nothing here emits a Candidate, admits an
  Activity or authorises an action.

What the current Body contract can and cannot answer
----------------------------------------------------
The real surface (``world_body_gateway_api.py:390-418``) returns ``available``,
``recovery``, ``latch`` and ``signals``; each signal carries ``signal_type``,
``qualitative_band``, ``occurred_at``, ``source_body_version``,
``runtime_version`` and ``body_id`` (``body_signal.py:179-194``).

It does **not** return ``body_id``, ``occurred_at``, the body version or
``last_advanced_at`` at the payload top level.  The consequences are deliberate:

* with an empty signal set nothing can be bound (no identity, no revision, no
  observation instant), so the answer is ``UNKNOWN`` / ``NO_BURDEN_EVIDENCE``
  with all three ``*_bound`` flags false.  Reading ``get_status`` as well would
  add a ``body_id`` but neither a body version nor an instant, so the capacity
  answer would not change; the adapter therefore stays at one read;
* ``AVAILABLE`` is unreachable: the contract has no positive capability signal,
  and inferring one from the *absence* of burden signals is exactly the
  inference the Body contract forbids;
* ``UNAVAILABLE`` is unreachable: no signal in the closed vocabulary means
  "cannot do it", and the freshness bound is not a capability fact.  A source
  that is down is reported ``UNKNOWN`` (fail closed), not as a broken body.

Freshness uses the *declared* bound and vocabulary of
``body_reader.assess_freshness`` (``body_reader.py:63-68,204-255``); no new
threshold is introduced.  When that owner cannot be resolved the adapter reports
``UNKNOWN`` / ``BODY_CONTRACT_UNRESOLVED`` instead of inventing a number.

Raises versus UNKNOWN
---------------------
``BodyCapacitySourceError`` is raised only for a caller/wiring fault: an illegal
subject, a home that is not a directory, an unparseable ``now``, a malformed
``expected_revision``, or a port without ``body()``.  ``BodyCapacitySource``
checks subject and home in ``__init__`` so such a fault surfaces at the seam
rather than mid-loop.  Everything the *world* can get wrong (source down, stale
view, unbound identity, revision drift, corrupt payload) is returned as a view
with ``body_capacity_state == UNKNOWN`` and a precise ``reason_code``.

How AG-1 treats UNKNOWN and LIMITED (read this before wiring)
------------------------------------------------------------
The honest ``UNKNOWN`` this adapter returns is not a permanent defer by itself.
``CapacityEvaluator.evaluate_capacity`` (``agency_decision_ag1.py:1956``) takes
``unknown_capacity_policy`` and defaults to ``ALLOW_WITH_UNKNOWN`` (:1971):

* ``LIMITED`` -> ``EXECUTABLE_WITH_LIMITS`` (:2044-2045), no blocking reason;
* ``UNKNOWN`` + the default policy -> ``EXEC_UNKNOWN`` (:2050-2051): the
  candidate stays open (``candidate_remains_open``, :2060) and nothing is
  recorded as a block;
* ``UNKNOWN`` + ``BLOCK_ON_UNKNOWN`` -> ``EXEC_NOT_NOW`` with
  ``REASON_CAPACITY_UNKNOWN_DEFERRED`` (:2047-2049) -- *that* is the permanent
  DEFER, and it comes from the caller's policy, not from a body fact.

An unburdened body answers ``UNKNOWN`` / ``NO_BURDEN_EVIDENCE``, which is the
normal honest answer here, so a caller must not wire ``BLOCK_ON_UNKNOWN`` merely
because the state is UNKNOWN.  ``ag1_capacity_kwargs`` forwards the policy only
when the caller states it explicitly.

Reusable helpers (public, so this package's other read views share one rule)
---------------------------------------------------------------------------
``bind_home_ref`` / ``parse_instant`` / ``require_instant`` /
``require_structured_ref`` / ``resolve_subject_id`` are the same functions this
adapter uses internally; ``life_situation_view`` and ``short_appraisal_view``
import them rather than copying the rules.

See ``docs/engineering/life-seed-body-capacity.md`` for the interface, the
caller requirements and an example.
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

__all__ = [
    "AG1_UNKNOWN_CAPACITY_POLICIES",
    "AG1_UNKNOWN_CAPACITY_POLICY_ALLOW",
    "AG1_UNKNOWN_CAPACITY_POLICY_BLOCK",
    "BURDEN_SIGNAL_TYPES",
    "CAPACITY_AVAILABLE",
    "CAPACITY_LIMITED",
    "CAPACITY_UNAVAILABLE",
    "CAPACITY_UNKNOWN",
    "KIND_BODY_CAPACITY_VIEW",
    "NON_BURDEN_SIGNAL_TYPES",
    "REASON_BODY_SOURCE_PREFIX",
    "REASON_CONTRACT_UNRESOLVED",
    "REASON_HOME_UNBOUND",
    "REASON_IDENTITY_MISMATCH",
    "REASON_IDENTITY_MISSING",
    "REASON_NO_BURDEN_EVIDENCE",
    "REASON_OBSERVATION_TIME_AHEAD",
    "REASON_OBSERVATION_TIME_INVALID",
    "REASON_OBSERVATION_TIME_MISSING",
    "REASON_OK_LIMITED",
    "REASON_PAYLOAD_MALFORMED",
    "REASON_REVISION_MISMATCH",
    "REASON_REVISION_MISSING",
    "REASON_SIGNAL_RAW_VALUES",
    "REASON_SIGNAL_SCHEMA_INVALID",
    "REASON_SIGNAL_TYPE_UNCLASSIFIED",
    "REASON_SOURCE_RAISED",
    "REASON_STATE_NOT_PRESENT",
    "REASON_SUBJECT_OWNER_UNAVAILABLE",
    "REASON_SUBJECT_REJECTED",
    "REASON_VIEW_STALE",
    "REASON_FRESHNESS_UNVERIFIED",
    "SCHEMA_BODY_CAPACITY_VIEW",
    "SOURCE_WORLD_BODY_READ_PORT",
    "VALID_CAPACITY_STATES",
    "BodyCapacitySource",
    "BodyCapacitySourceError",
    "ag1_capacity_kwargs",
    "bind_home_ref",
    "parse_instant",
    "read_body_capacity",
    "require_instant",
    "require_structured_ref",
    "resolve_subject_id",
]

SCHEMA_BODY_CAPACITY_VIEW = "chiyo.life.body_capacity_view.v1"
KIND_BODY_CAPACITY_VIEW = "body_capacity_view"

#: The one read this adapter makes, named so a caller can see the provenance.
SOURCE_WORLD_BODY_READ_PORT = "world_body_read_port.get_body_signals"

# --------------------------------------------------------------------------
# AG-1 capacity vocabulary
# --------------------------------------------------------------------------
# Owner: components/life/scripts/agency_decision_ag1.py:336-346
# (``CAPACITY_AVAILABLE`` .. ``VALID_CAPACITY_STATES``).  Mirrored, not
# re-invented; ``tests/test_body_capacity_source.py`` asserts equality so the
# mirror cannot drift.
CAPACITY_AVAILABLE = "AVAILABLE"
CAPACITY_LIMITED = "LIMITED"
CAPACITY_UNAVAILABLE = "UNAVAILABLE"
CAPACITY_UNKNOWN = "UNKNOWN"
VALID_CAPACITY_STATES = frozenset(
    {CAPACITY_AVAILABLE, CAPACITY_LIMITED, CAPACITY_UNAVAILABLE, CAPACITY_UNKNOWN}
)

# --------------------------------------------------------------------------
# AG-1's policy for an UNKNOWN capacity dimension
# --------------------------------------------------------------------------
# Owner: agency_decision_ag1.py:1971 (the default) and :2047-2049 (the branch
# that turns UNKNOWN into a defer).  Mirrored so a caller cannot wire a policy
# the AG-1 owner does not have; the test asserts the values.
AG1_UNKNOWN_CAPACITY_POLICY_ALLOW = "ALLOW_WITH_UNKNOWN"
AG1_UNKNOWN_CAPACITY_POLICY_BLOCK = "BLOCK_ON_UNKNOWN"
AG1_UNKNOWN_CAPACITY_POLICIES = frozenset(
    {AG1_UNKNOWN_CAPACITY_POLICY_ALLOW, AG1_UNKNOWN_CAPACITY_POLICY_BLOCK}
)

# --------------------------------------------------------------------------
# the mapping: which real signals are capacity evidence
# --------------------------------------------------------------------------
# Owner of the vocabulary: components/world/scripts/body_signal.py:46-91
# (``SIGNAL_RULES`` + ``TREND_RULES``).  Every rule is a *deviation* rule; none
# of them says "capable".  The split below is the whole mapping, and the test
# asserts that the two sets together are exactly the owner's vocabulary, so a
# new rule in the Body contract fails the test instead of being silently
# ignored.
BURDEN_SIGNAL_TYPES = frozenset(
    {
        "fatigue_noticeable",      # fatigue at/above its hysteresis threshold
        "fatigue_high",
        "activation_low",
        "discomfort_mild",
        "discomfort_noticeable",
        "sensory_load_elevated",
        "tension_elevated",
        "recovery_interrupted",    # recovery opened and then broken by real load
    }
)

#: Real vocabulary members that are deliberately *not* capacity evidence.
#: Thermal signals say nothing about capability, and the three trend signals are
#: improvements: they neither lower nor raise the state.
NON_BURDEN_SIGNAL_TYPES = frozenset(
    {
        "warmth_high",
        "cold_tendency",
        "recovery_underway",
        "tension_releasing",
        "activation_recovering",
    }
)

# --------------------------------------------------------------------------
# reason codes (closed set; every view carries exactly one)
# --------------------------------------------------------------------------
REASON_OK_LIMITED = "OK_LIMITED_BY_BODY_SIGNAL"
REASON_SUBJECT_REJECTED = "SUBJECT_REJECTED"
REASON_SUBJECT_OWNER_UNAVAILABLE = "SUBJECT_OWNER_UNAVAILABLE"
REASON_HOME_UNBOUND = "HOME_UNBOUND"
REASON_CONTRACT_UNRESOLVED = "BODY_CONTRACT_UNRESOLVED"
#: Non-OK read outcomes become ``BODY_SOURCE_<OUTCOME>`` verbatim, e.g.
#: ``BODY_SOURCE_UNAVAILABLE``; the port's real vocabulary is never collapsed.
REASON_BODY_SOURCE_PREFIX = "BODY_SOURCE_"
REASON_SOURCE_RAISED = "BODY_SOURCE_RAISED"
REASON_PAYLOAD_MALFORMED = "BODY_PAYLOAD_MALFORMED"
REASON_STATE_NOT_PRESENT = "BODY_STATE_NOT_PRESENT"
REASON_SIGNAL_SCHEMA_INVALID = "BODY_SIGNAL_SCHEMA_INVALID"
REASON_SIGNAL_TYPE_UNCLASSIFIED = "BODY_SIGNAL_TYPE_UNCLASSIFIED"
REASON_SIGNAL_RAW_VALUES = "BODY_SIGNAL_RAW_VALUES"
REASON_IDENTITY_MISSING = "BODY_IDENTITY_MISSING"
REASON_IDENTITY_MISMATCH = "BODY_SUBJECT_MISMATCH"
REASON_REVISION_MISSING = "BODY_REVISION_MISSING"
REASON_REVISION_MISMATCH = "BODY_REVISION_MISMATCH"
REASON_OBSERVATION_TIME_MISSING = "BODY_OBSERVATION_TIME_MISSING"
REASON_OBSERVATION_TIME_INVALID = "BODY_OBSERVATION_TIME_INVALID"
REASON_OBSERVATION_TIME_AHEAD = "BODY_OBSERVATION_TIME_AHEAD"
REASON_FRESHNESS_UNVERIFIED = "BODY_FRESHNESS_UNVERIFIED"
REASON_VIEW_STALE = "BODY_VIEW_STALE"
REASON_NO_BURDEN_EVIDENCE = "NO_BURDEN_EVIDENCE"

#: Mirrors ``agency_decision_ag1.py:557`` so the reference this adapter emits is
#: accepted by ``_validate_structured_ref``.  The test asserts the two patterns
#: are identical.
_STRUCTURED_REF_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]*[:_][a-zA-Z0-9_.:/-]+$")

#: ``components/world/scripts`` — the Body contract owners live here.  Resolved
#: from this file, never by scanning the tree.
_WORLD_SCRIPTS = Path(__file__).resolve().parents[2] / "world" / "scripts"

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)


class BodyCapacitySourceError(ValueError):
    """A caller/wiring fault, not a runtime body condition."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def parse_instant(value: Any) -> Optional[datetime]:
    """ISO-8601 or ``datetime`` -> aware UTC instant, or ``None`` if unreadable."""

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def require_instant(value: Any, label: str) -> datetime:
    moment = parse_instant(value)
    if moment is None:
        raise BodyCapacitySourceError(f"{label} must be an ISO-8601 instant or datetime")
    return moment


def require_structured_ref(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BodyCapacitySourceError(f"{label} must be a non-empty structured reference")
    cleaned = value.strip()
    if not _STRUCTURED_REF_RE.fullmatch(cleaned):
        raise BodyCapacitySourceError(
            f"{label}={value!r} must be a structured reference '<prefix>:<id>'"
        )
    return cleaned


def bind_home_ref(home: Any) -> str:
    """A stable, path-free binding for the instance home.

    The reference is a digest, so no filesystem path leaks into a decision
    record, and it is stable across restarts for the same home.
    """

    if isinstance(home, (str, Path)):
        text = str(home).strip()
    else:
        text = ""
    if not text:
        raise BodyCapacitySourceError("home must be a non-empty path")
    resolved = Path(text).expanduser()
    if not resolved.is_dir():
        raise BodyCapacitySourceError(f"home {text!r} is not a directory")
    digest = hashlib.sha256(str(resolved.resolve()).encode("utf-8")).hexdigest()
    return "home:%s" % digest[:12]


def _resolve_subject_owner() -> Optional[Any]:
    """The single-subject rule owner, or ``None`` when it cannot be resolved."""

    try:
        import activity_continuity as ac
    except Exception:  # noqa: BLE001 - fail closed, never guess the subject rule
        return None
    return ac


def resolve_subject_id(subject_id: Optional[str]) -> Optional[str]:
    """``None`` when the owner is unreachable, else the canonical subject.

    Raises ``BodyCapacitySourceError`` when the owner rejects the value; the
    owner (``activity_continuity.validate_subject_id``) is the only authority on
    which subjects are legal.
    """

    owner = _resolve_subject_owner()
    if owner is None:
        return None
    try:
        return owner.validate_subject_id(subject_id)
    except Exception as error:  # noqa: BLE001 - the owner's refusal is final
        raise BodyCapacitySourceError(f"illegal subject_id: {error}") from error


# The underscore spellings are the historical internal names; the public names
# above are the reusable ones.  They are the same function objects.
_parse_instant = parse_instant
_require_instant = require_instant
_require_structured_ref = require_structured_ref
_bind_home_ref = bind_home_ref
_validated_subject_id = resolve_subject_id


def _contract_from_imports() -> dict[str, Any]:
    """Read the two Body contract owners off ``sys.path`` (no side effects)."""

    import body_reader as bread
    import body_signal as bsig

    return {
        "fresh": bread.FRESHNESS_FRESH,
        "stale": bread.FRESHNESS_STALE,
        "unavailable": bread.FRESHNESS_UNAVAILABLE,
        "unverified": bread.FRESHNESS_UNVERIFIED,
        "max_age_hours": float(bread.FRESHNESS_MAX_AGE_HOURS),
        "bands": tuple(bsig.BANDS),
        "forbidden": frozenset(bsig.FORBIDDEN_SIGNAL_FIELDS),
    }


def _resolve_contract(world_scripts_dir: Optional[Any] = None) -> Optional[dict[str, Any]]:
    """The Body contract owners (freshness bound/vocabulary + signal vocabulary).

    Whatever the process already has on ``sys.path`` wins, because that is the
    copy the running World/Body service itself loaded; the directory hints are
    only consulted when the owners are not importable at all (a bare checkout).
    ``None`` means they could not be resolved: the caller must then report
    UNKNOWN rather than invent a bound or a band vocabulary.
    """

    try:
        return _contract_from_imports()
    except ImportError:
        pass
    for candidate in (world_scripts_dir, _WORLD_SCRIPTS):
        if candidate is None:
            continue
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)
    try:
        return _contract_from_imports()
    except ImportError:
        return None


def _view(
    *,
    subject_id: Optional[str],
    home_ref: Optional[str],
    source_outcome: Optional[str],
    degraded: Optional[bool],
    freshness: Optional[str],
    state: str,
    reason: str,
    capacity_ref: Optional[str] = None,
    observed_body_id: Optional[str] = None,
    observed_revision: Optional[str] = None,
    observed_at: Optional[str] = None,
    observed_recovery_state: Optional[str] = None,
    evidence: Sequence[str] = (),
    identity_bound: bool = False,
    revision_bound: bool = False,
    observation_time_bound: bool = False,
) -> dict[str, Any]:
    """Every return path builds the same shape, so a caller never guesses."""

    if state not in VALID_CAPACITY_STATES:
        raise BodyCapacitySourceError(f"capacity state {state!r} is not in the AG-1 vocabulary")
    return {
        "schema_version": SCHEMA_BODY_CAPACITY_VIEW,
        "kind": KIND_BODY_CAPACITY_VIEW,
        "source": SOURCE_WORLD_BODY_READ_PORT,
        "subject_id": subject_id,
        "home_ref": home_ref,
        "source_outcome": source_outcome,
        "degraded": degraded,
        "freshness": freshness,
        "observed_at": observed_at,
        "observed_body_id": observed_body_id,
        "observed_revision": observed_revision,
        "observed_recovery_state": observed_recovery_state,
        "identity_bound": bool(identity_bound),
        "revision_bound": bool(revision_bound),
        "observation_time_bound": bool(observation_time_bound),
        "body_capacity_state": state,
        "body_capacity_ref": capacity_ref,
        "reason_code": reason,
        "evidence": list(evidence),
        "qualitative_only": True,
        "raw_values_exposed": False,
        # BODY-CAP-1 is a read surface: these are constants, not counters.
        "read_only": True,
        "reads_performed": 1,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
        "actions_authorized": 0,
    }


def _source_reason(outcome: Any) -> str:
    label = re.sub(r"[^A-Z0-9_]", "_", str(outcome).upper()) or "UNKNOWN"
    return REASON_BODY_SOURCE_PREFIX + label


# --------------------------------------------------------------------------
# the read
# --------------------------------------------------------------------------


def read_body_capacity(
    port: Any,
    *,
    home: Any,
    subject_id: Optional[str] = None,
    now: Any = None,
    expected_body_id: Optional[str] = None,
    expected_revision: Optional[str] = None,
    world_scripts_dir: Optional[Any] = None,
) -> dict[str, Any]:
    """Read one body view and translate it into AG-1's capacity vocabulary.

    ``port`` is the existing read-only World/Body port
    (``WorldBodyReadPort``); only ``port.body()`` is used.  ``home`` binds the
    adapter to one instance.  ``now`` is injected by the caller — freshness is
    never read from a wall clock here.  ``expected_body_id`` /
    ``expected_revision`` let a caller refuse a view that drifted away from the
    body it was told to watch.
    """

    if not callable(getattr(port, "body", None)):
        raise BodyCapacitySourceError(
            "port must expose the read-only method 'body' (WorldBodyReadPort.body)"
        )
    if expected_revision is not None:
        expected_revision = _require_structured_ref(expected_revision, "expected_revision")
    reference = None if now is None else _require_instant(now, "now")

    resolved_subject = _validated_subject_id(subject_id)

    try:
        home_ref = _bind_home_ref(home)
    except BodyCapacitySourceError:
        return _view(
            subject_id=resolved_subject, home_ref=None, source_outcome=None,
            degraded=None, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_HOME_UNBOUND,
        )

    # ``_validated_subject_id`` returns None only when the rule owner itself
    # cannot be resolved; a rejected subject has already raised.
    if resolved_subject is None:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=None,
            degraded=None, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_SUBJECT_OWNER_UNAVAILABLE,
        )

    contract = _resolve_contract(world_scripts_dir)
    if contract is None:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=None,
            degraded=None, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_CONTRACT_UNRESOLVED,
        )

    # ---- the single read -------------------------------------------------
    try:
        field = port.body()
    except Exception as error:  # noqa: BLE001 - a fault is a result, not a crash
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=None,
            degraded=True, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_SOURCE_RAISED,
        )

    if not isinstance(field, Mapping):
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=None,
            degraded=True, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_PAYLOAD_MALFORMED,
        )

    outcome = field.get("outcome")
    degraded = bool(field.get("degraded")) if field.get("degraded") is not None else None
    if str(outcome) != "OK":
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=_source_reason(outcome),
        )

    payload = field.get("data")
    if not isinstance(payload, Mapping):
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_PAYLOAD_MALFORMED,
        )

    if "available" not in payload or not isinstance(payload["available"], bool):
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_PAYLOAD_MALFORMED,
        )
    if not payload["available"]:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_STATE_NOT_PRESENT,
        )

    recovery_state = None
    recovery = payload.get("recovery")
    if isinstance(recovery, Mapping):
        recovery_state = recovery.get("state")
        if recovery_state is not None:
            recovery_state = str(recovery_state)

    signals = payload.get("signals")
    if not isinstance(signals, list):
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_SIGNAL_SCHEMA_INVALID, observed_recovery_state=recovery_state,
        )

    bands = contract["bands"]
    forbidden = contract["forbidden"]
    body_ids: set = set()
    versions: set = set()
    instants: set = set()
    burden: list[str] = []

    for signal in signals:
        if not isinstance(signal, Mapping):
            return _view(
                subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
                degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
                reason=REASON_SIGNAL_SCHEMA_INVALID, observed_recovery_state=recovery_state,
            )
        leaked = sorted(key for key in signal if key in forbidden)
        if leaked:
            return _view(
                subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
                degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
                reason=REASON_SIGNAL_RAW_VALUES, observed_recovery_state=recovery_state,
            )
        signal_type = signal.get("signal_type")
        band = signal.get("qualitative_band")
        if not isinstance(signal_type, str) or not signal_type.strip():
            return _view(
                subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
                degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
                reason=REASON_SIGNAL_SCHEMA_INVALID, observed_recovery_state=recovery_state,
            )
        if band not in bands:
            return _view(
                subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
                degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
                reason=REASON_SIGNAL_SCHEMA_INVALID, observed_recovery_state=recovery_state,
            )
        if signal_type not in BURDEN_SIGNAL_TYPES and signal_type not in NON_BURDEN_SIGNAL_TYPES:
            return _view(
                subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
                degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
                reason=REASON_SIGNAL_TYPE_UNCLASSIFIED,
                observed_recovery_state=recovery_state,
            )
        if signal_type in BURDEN_SIGNAL_TYPES:
            burden.append("%s:%s" % (signal_type, band))
        body_id = signal.get("body_id")
        if isinstance(body_id, str) and body_id.strip():
            body_ids.add(body_id.strip())
        version = signal.get("source_body_version")
        if isinstance(version, bool) or not isinstance(version, int):
            version = None
        if version is not None:
            versions.add(version)
        raw_instant = signal.get("occurred_at")
        if isinstance(raw_instant, str) and raw_instant.strip():
            instants.add(raw_instant.strip())
        elif isinstance(raw_instant, datetime):
            instants.add(raw_instant.isoformat())

    evidence = sorted(set(burden))

    if not signals:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=contract["unverified"], state=CAPACITY_UNKNOWN,
            reason=REASON_NO_BURDEN_EVIDENCE, observed_recovery_state=recovery_state,
        )

    if len(body_ids) != 1:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_IDENTITY_MISSING, observed_recovery_state=recovery_state,
            evidence=evidence,
        )
    observed_body_id = next(iter(body_ids))
    if expected_body_id is not None and observed_body_id != str(expected_body_id):
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_IDENTITY_MISMATCH, observed_body_id=observed_body_id,
            observed_recovery_state=recovery_state, evidence=evidence,
        )

    if len(versions) != 1:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_REVISION_MISSING, observed_body_id=observed_body_id,
            observed_recovery_state=recovery_state, evidence=evidence, identity_bound=True,
        )
    observed_revision = "bodyrev:%s" % next(iter(versions))
    if expected_revision is not None and observed_revision != expected_revision:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_REVISION_MISMATCH, observed_body_id=observed_body_id,
            observed_revision=observed_revision, observed_recovery_state=recovery_state,
            evidence=evidence, identity_bound=True, revision_bound=True,
        )

    if len(instants) != 1:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_OBSERVATION_TIME_MISSING, observed_body_id=observed_body_id,
            observed_revision=observed_revision, observed_recovery_state=recovery_state,
            evidence=evidence, identity_bound=True, revision_bound=True,
        )
    observed_instant = _parse_instant(next(iter(instants)))
    if observed_instant is None:
        return _view(
            subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
            degraded=degraded, freshness=None, state=CAPACITY_UNKNOWN,
            reason=REASON_OBSERVATION_TIME_INVALID, observed_body_id=observed_body_id,
            observed_revision=observed_revision, observed_recovery_state=recovery_state,
            evidence=evidence, identity_bound=True, revision_bound=True,
        )
    observed_at = observed_instant.isoformat()
    bound = dict(
        subject_id=resolved_subject, home_ref=home_ref, source_outcome=str(outcome),
        degraded=degraded, observed_body_id=observed_body_id,
        observed_revision=observed_revision, observed_at=observed_at,
        observed_recovery_state=recovery_state, evidence=evidence,
        identity_bound=True, revision_bound=True, observation_time_bound=True,
    )

    if reference is None:
        return _view(freshness=contract["unverified"], state=CAPACITY_UNKNOWN,
                     reason=REASON_FRESHNESS_UNVERIFIED, **bound)

    age_hours = (reference - observed_instant).total_seconds() / 3600.0
    if age_hours < 0:
        # A view observed "in the future" means the clock moved backwards; that
        # is not freshness and it is not a body fact.
        return _view(freshness=contract["unverified"], state=CAPACITY_UNKNOWN,
                     reason=REASON_OBSERVATION_TIME_AHEAD, **bound)
    if age_hours > contract["max_age_hours"]:
        return _view(freshness=contract["stale"], state=CAPACITY_UNKNOWN,
                     reason=REASON_VIEW_STALE, **bound)

    if evidence:
        return _view(
            freshness=contract["fresh"], state=CAPACITY_LIMITED,
            reason=REASON_OK_LIMITED, capacity_ref=_capacity_ref(observed_body_id, observed_revision),
            **bound,
        )

    return _view(
        freshness=contract["fresh"], state=CAPACITY_UNKNOWN,
        reason=REASON_NO_BURDEN_EVIDENCE,
        capacity_ref=_capacity_ref(observed_body_id, observed_revision), **bound,
    )


def _capacity_ref(body_id: str, revision: str) -> str:
    """A stable, source-derived reference for one observed body revision."""

    return _require_structured_ref("bodycap:%s:%s" % (body_id, revision), "body_capacity_ref")


def ag1_capacity_kwargs(
    view: Mapping[str, Any], *, unknown_capacity_policy: Optional[str] = None
) -> dict[str, Any]:
    """The exact keyword pair ``CapacityEvaluator.evaluate_capacity`` wants.

    ``agency_decision_ag1.py:1977-1980`` fabricates ``AVAILABLE`` when a
    ``body_capacity_ref`` is supplied *without* a state.  Always pass both, which
    is what this helper does: an UNKNOWN view keeps its reference and still says
    UNKNOWN.

    ``unknown_capacity_policy`` is AG-1's own switch (:1971).  It is forwarded
    only when the caller states it, and ``BLOCK_ON_UNKNOWN`` is refused unless the
    caller really means it: that policy turns every honest UNKNOWN into
    ``EXEC_NOT_NOW`` + ``CAPACITY_UNKNOWN_DEFERRED`` (:2047-2049), which is how an
    adapter becomes a permanent DEFER.
    """

    if not isinstance(view, Mapping) or "body_capacity_state" not in view:
        raise BodyCapacitySourceError("view must be a body-capacity view")
    state = view["body_capacity_state"]
    if state not in VALID_CAPACITY_STATES:
        raise BodyCapacitySourceError(f"view carries an illegal capacity state {state!r}")
    kwargs = {
        "body_capacity_state": state,
        "body_capacity_ref": view.get("body_capacity_ref"),
    }
    if unknown_capacity_policy is not None:
        if unknown_capacity_policy not in AG1_UNKNOWN_CAPACITY_POLICIES:
            raise BodyCapacitySourceError(
                f"unknown_capacity_policy {unknown_capacity_policy!r} is not an AG-1 policy"
            )
        kwargs["unknown_capacity_policy"] = unknown_capacity_policy
    return kwargs


class BodyCapacitySource:
    """Injectable reader: one instance, one home, one subject, one port.

    Construction is where wiring faults are supposed to happen (illegal subject,
    unusable home, a port without ``body()``); ``read()`` is where the world's
    own answers are turned into a capacity view.  The object holds no cache and
    writes nothing, so two instances over the same port behave identically —
    which is what "restart reads the owner again" means here.
    """

    def __init__(
        self,
        *,
        port: Any,
        home: Any,
        subject_id: Optional[str] = None,
        expected_body_id: Optional[str] = None,
        expected_revision: Optional[str] = None,
        world_scripts_dir: Optional[Any] = None,
    ) -> None:
        if not callable(getattr(port, "body", None)):
            raise BodyCapacitySourceError(
                "port must expose the read-only method 'body' (WorldBodyReadPort.body)"
            )
        if expected_revision is not None:
            expected_revision = _require_structured_ref(expected_revision, "expected_revision")
        resolved_subject = _validated_subject_id(subject_id)
        if resolved_subject is None:
            raise BodyCapacitySourceError(
                "the subject owner (activity_continuity) is unavailable; the subject rule "
                "cannot be verified, so no body read is allowed"
            )
        self.port = port
        self.home = home
        self.home_ref = _bind_home_ref(home)
        self.subject_id = resolved_subject
        self.expected_body_id = expected_body_id
        self.expected_revision = expected_revision
        self.world_scripts_dir = world_scripts_dir
        self.last_view: Optional[dict[str, Any]] = None

    def read(self, *, now: Any = None, expected_revision: Optional[str] = None) -> dict[str, Any]:
        view = read_body_capacity(
            self.port,
            home=self.home,
            subject_id=self.subject_id,
            now=now,
            expected_body_id=self.expected_body_id,
            expected_revision=(
                self.expected_revision if expected_revision is None else expected_revision
            ),
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
            self.read() if view is None else view,
            unknown_capacity_policy=unknown_capacity_policy,
        )
