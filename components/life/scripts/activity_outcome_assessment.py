#!/usr/bin/env python3
"""S09 Activity Outcome Assessment | explicit FAILED scope + BLOCKED basis.

This module is a *gate*, not a second Activity store.  It owns no aggregate, no journal and
no persistent file: the only durable facts are the LR-2 Activity record and transition the
existing owner already writes.

Design (v2, Q8-Q10):
  * ``Action FAILED`` is evidence at most.  It never becomes an Activity FAILED by itself,
    and an UNKNOWN is never rewritten as a failure because it timed out, repeated, or looks
    the same as before.
  * An Activity FAILED exists only when the S09 owner states an exact scope (this Activity,
    this revision, these goal/target refs), the named criteria that was judged unreachable,
    and owner evidence that can be re-read.  A model string, an HTTP status, or a bare
    ``verified: true`` is refused by name.
  * BLOCKED is non-terminal: the owner records a named basis that the Activity cannot
    advance under the current conditions.  It creates no timer and no automatic recovery;
    the existing LR-4 condition/eligibility path plus a fresh Decision RESUME are required.

The verifier builders in ``activity_admission`` import the payload validators here lazily, so
the admission side can re-check the same facts inside its store lock without a second truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from activity_continuity import (
    VALID_ACTION_REF_PREFIXES,
    VALID_ASSESSMENT_PREFIXES,
    VALID_BLOCK_BASIS_PREFIXES,
    VALID_EVIDENCE_PREFIXES,
    validate_structured_ref,
)

SCHEMA_FAILURE_ASSESSMENT = "chiyo.life.failure_assessment.v1"
SCHEMA_BLOCKED_BASIS = "chiyo.life.blocked_basis.v1"

OWNER_LABEL = "s09_activity_owner"
VERDICT_FAILED = "FAILED"

#: A closed vocabulary of *criteria names*.  A caller cannot invent "the goal is unreachable"
#: out of any refusal it happens to have; each name says which owner fact the evidence must
#: be about, and the owner evidence is re-read independently.
FAILURE_CRITERION_ARTIFACT_ARCHIVED = "ARTIFACT_ARCHIVED"
FAILURE_CRITERION_ARTIFACT_TOMBSTONED = "ARTIFACT_TOMBSTONED"
FAILURE_CRITERION_SOURCE_WITHDRAWN = "SOURCE_WITHDRAWN"
FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL = "EXTERNAL_TERMINAL_REFUSAL"
ALLOWED_FAILURE_CRITERIA = frozenset(
    {
        FAILURE_CRITERION_ARTIFACT_ARCHIVED,
        FAILURE_CRITERION_ARTIFACT_TOMBSTONED,
        FAILURE_CRITERION_SOURCE_WITHDRAWN,
        FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL,
    }
)
CRITERION_REF_PREFIXES = frozenset({"criterion", "fcrit", "failure_criterion"})

#: Fields a model/HTTP adapter might try to smuggle in as if they were owner evidence.
FORBIDDEN_ASSESSMENT_KEYS = frozenset(
    {
        "verified",
        "verified_by_model",
        "model_id",
        "http_status",
        "self_asserted",
        "assistant_output",
        "prompt",
        "chain_of_thought",
    }
)

_ALLOWED_ASSESSMENT_KEYS = frozenset(
    {
        "schema_version",
        "assessment_id",
        "activity_id",
        "activity_revision",
        "assessed_by",
        "verdict",
        "goal_scope_refs",
        "criterion_refs",
        "evidence_refs",
        "unresolved_action_refs",
        "assessed_at",
        "notes_code",
    }
)


class FailureAssessmentError(ValueError):
    """The proposed failure does not meet the owner-assessment contract."""


class BlockedBasisError(ValueError):
    """The proposed BLOCK basis is not a named, structured owner basis."""


def _ref(value: Any, field_name: str, allowed: Optional[frozenset[str]],
         *, error_cls: type[ValueError] = FailureAssessmentError) -> str:
    try:
        return validate_structured_ref(value, field_name, allowed_prefixes=allowed)
    except Exception as exc:
        raise error_cls(str(exc)) from exc


def _ref_list(values: Any, field_name: str, *, allowed: Optional[frozenset[str]],
              allow_empty: bool,
              error_cls: type[ValueError] = FailureAssessmentError) -> list[str]:
    if values is None:
        values = []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise error_cls(f"{field_name} must be a sequence of structured refs")
    out: list[str] = []
    for item in values:
        ref = _ref(item, field_name, allowed, error_cls=error_cls)
        if ref not in out:
            out.append(ref)
    if not out and not allow_empty:
        raise error_cls(f"{field_name} must contain at least one ref")
    return out


#: Which owner fact each named criterion must actually be about.  A criterion name alone is
#: never evidence: the ref that carries it has to re-read as the *kind* of terminal fact the
#: name claims.  This is what stops "some ref that exists" from signing a failure.
_ARTIFACT_OWNER = "lifesupply.managed_artifact"
_ACTION_OWNER = "action_reality_ledger"
_SETTLEMENT_OWNER = "result_settlement_ledger"
_ARTIFACT_ARCHIVED_LIFECYCLES = frozenset({"ARCHIVED"})
_ARTIFACT_TOMBSTONED_LIFECYCLES = frozenset({"TOMBSTONED", "TOMBSTONE"})
_TERMINAL_ACTION_STATUS = "FAILED"
_TERMINAL_SETTLEMENT_STATUSES = frozenset({"FAILED"})
#: Action/settlement states that are still not evidence of a terminal refusal.  An UNKNOWN is
#: explicitly here: it is a timeout, not a verdict, and it is never rewritten as a failure.
_UNSETTLED_ACTION_STATUSES = frozenset({"UNKNOWN", "PREPARED", "SUBMITTED", "ACKNOWLEDGED"})


#: Owner rows may declare an explicit, terminal "this target can never be produced" fact.
#: A bare FAILED status is one failed attempt -- a GATE_OFF, a CAS conflict or a budget
#: refusal -- and never becomes "the goal is unreachable" by itself.
_TERMINAL_REFUSAL_MARKER = "terminal_refusal"


def _bare_activity_id(value: Any) -> str:
    """Normalise the two Activity id spellings the owners already use (actv_ / actv:)."""
    return str(value or "").replace("actv:", "").replace("actv_", "")


def _row_status(row: Mapping[str, Any]) -> str:
    return str(row.get("status") or row.get("status_claim") or "")


def _row_linked_to_activity(row: Mapping[str, Any], activity: Mapping[str, Any]) -> bool:
    """True only when the owner row itself names this Activity (never a caller claim)."""

    ref = row.get("activity_ref")
    if not isinstance(ref, str) or not ref:
        return False
    return _bare_activity_id(ref) == _bare_activity_id(activity.get("activity_id"))


def _bare_artifact_id(value: Any) -> str:
    """Strip the owner's own ``artifact:`` wrapper so ids compare exactly, not by prefix."""

    text = str(value or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def _activity_artifact_ids(activity: Mapping[str, Any]) -> set[str]:
    """The artifact ids this very Activity already binds as its own goal/checkpoint."""

    ids: set[str] = set()
    for ref in _activity_goal_scope(activity):
        prefix, _, rest = ref.partition(":")
        if prefix in ("artifact", "artifact_file", "artifact_rev", "art_rev") and rest:
            artifact_id = _bare_artifact_id(rest).split(":", 1)[0]
            if artifact_id:
                ids.add(artifact_id)
    return ids


def _evidence_matches_criterion(name: str, evidence_pairs: Sequence[tuple[str, Mapping[str, Any]]],
                                activity: Mapping[str, Any]) -> bool:
    """True when one re-read owner row really is the terminal fact ``name`` claims, *about this
    Activity*.  The owner row is paired with the ref that produced it, and artifact evidence
    must resolve to an artifact this Activity already bound -- an unrelated archived piece
    cannot sign a failure by owner+status alone."""

    rows = [row for _ref, row in evidence_pairs]
    if name == FAILURE_CRITERION_ARTIFACT_ARCHIVED:
        bound = _activity_artifact_ids(activity)
        return any(
            str(row.get("owner") or "") == _ARTIFACT_OWNER
            and str(row.get("lifecycle") or "") in _ARTIFACT_ARCHIVED_LIFECYCLES
            and _bare_artifact_id(row.get("artifact_id")) in bound
            for row in rows)
    if name == FAILURE_CRITERION_ARTIFACT_TOMBSTONED:
        bound = _activity_artifact_ids(activity)
        return any(
            str(row.get("owner") or "") == _ARTIFACT_OWNER
            and str(row.get("lifecycle") or "") in _ARTIFACT_TOMBSTONED_LIFECYCLES
            and _bare_artifact_id(row.get("artifact_id")) in bound
            for row in rows)
    if name == FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL:
        return any(
            str(row.get("owner") or "") in (_ACTION_OWNER, _SETTLEMENT_OWNER)
            and _row_status(row) in _TERMINAL_SETTLEMENT_STATUSES
            and _row_linked_to_activity(row, activity)
            and row.get(_TERMINAL_REFUSAL_MARKER) is True
            for row in rows)
    if name == FAILURE_CRITERION_SOURCE_WITHDRAWN:
        # No owner read for a withdrawn *source* is wired into this composition root.  The
        # criterion stays in the closed vocabulary but cannot be satisfied yet; claiming it
        # with an unrelated ref is refused instead of being treated as "close enough".
        return any(row.get("withdrawn") is True for row in rows)
    return False


def _criterion_refusal(name: str) -> str:
    """A named reason for an unsupported criterion -- never a generic "not verified"."""

    if name == FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL:
        return ("EXTERNAL_TERMINAL_REFUSAL is not supported by current owner records: a "
                "FAILED or UNKNOWN Action/settlement only records one attempt; a GATE_OFF / "
                "CAS / budget refusal is not proof the goal can never be produced, and no "
                "owner row carries a terminal_refusal fact")
    if name in (FAILURE_CRITERION_ARTIFACT_ARCHIVED, FAILURE_CRITERION_ARTIFACT_TOMBSTONED):
        return ("no re-read managed-artifact row for this Activity's own goal scope has the "
                "required lifecycle; an unrelated piece cannot sign this Activity's failure")
    if name == FAILURE_CRITERION_SOURCE_WITHDRAWN:
        return "no owner read for a withdrawn source is wired into this composition root"
    return "the re-read owner evidence does not support this criterion"


def _activity_goal_scope(activity: Mapping[str, Any]) -> set[str]:
    """The refs this very Activity already names as its own target/goal surface."""

    refs: set[str] = set()
    for key in ("origin_ref", "decision_ref", "adoption_ref", "target_ref",
                "checkpoint_ref", "last_action_ref"):
        value = activity.get(key)
        if isinstance(value, str) and value:
            refs.add(value)
    for key in ("subject_refs", "source_refs"):
        for value in activity.get(key) or ():
            if isinstance(value, str) and value:
                refs.add(value)
    return refs


def _criterion_name(ref: str) -> str:
    _ref(ref, "criterion_refs", CRITERION_REF_PREFIXES)
    prefix, _, name = ref.partition(":")
    if prefix not in CRITERION_REF_PREFIXES or name not in ALLOWED_FAILURE_CRITERIA:
        raise FailureAssessmentError(
            f"criterion {ref!r} is not one of the named failure criteria "
            f"{sorted(ALLOWED_FAILURE_CRITERIA)}")
    return name


@dataclass(frozen=True)
class ActivityFailureAssessment:
    """Immutable owner statement: this Activity's goal scope is judged unreachable."""

    assessment_id: str
    activity_id: str
    activity_revision: int
    goal_scope_refs: tuple[str, ...]
    criterion_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    unresolved_action_refs: tuple[str, ...]
    assessed_at: str
    assessed_by: str = OWNER_LABEL
    verdict: str = VERDICT_FAILED
    notes_code: Optional[str] = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_FAILURE_ASSESSMENT,
            "assessment_id": self.assessment_id,
            "activity_id": self.activity_id,
            "activity_revision": self.activity_revision,
            "assessed_by": self.assessed_by,
            "verdict": self.verdict,
            "goal_scope_refs": list(self.goal_scope_refs),
            "criterion_refs": list(self.criterion_refs),
            "evidence_refs": list(self.evidence_refs),
            "unresolved_action_refs": list(self.unresolved_action_refs),
            "assessed_at": self.assessed_at,
            "notes_code": self.notes_code,
        }


def build_failure_assessment(
    activity: Mapping[str, Any],
    *,
    goal_scope_refs: Sequence[str],
    criterion_refs: Sequence[str],
    evidence_refs: Sequence[str],
    unresolved_action_refs: Sequence[str] = (),
    assessed_at: str,
    assessment_id: Optional[str] = None,
    notes_code: Optional[str] = None,
) -> ActivityFailureAssessment:
    """Build the owner statement from an Activity the owner just read.

    This only *constructs*; nothing is committed.  ``validate_failure_payload_against_activity``
    and the admission verifier re-check it before a transition can exist.
    """

    activity_id = str(activity.get("activity_id") or "").strip()
    if not activity_id:
        raise FailureAssessmentError("the Activity record has no activity_id")
    revision = activity.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise FailureAssessmentError("the Activity record has no integer revision")

    # Goal scope is not restricted to one namespace: it must be a subset of refs this Activity
    # itself names.  Validate the grammar first, then compare with the live record.
    scope = _ref_list(goal_scope_refs, "goal_scope_refs", allowed=None, allow_empty=False)
    named = _activity_goal_scope(activity)
    outside = [ref for ref in scope if ref not in named]
    if outside:
        raise FailureAssessmentError(
            f"goal_scope_refs not named by this Activity: {outside}")
    criteria = [_ref(ref, "criterion_refs", CRITERION_REF_PREFIXES) for ref in criterion_refs]
    if not criteria:
        raise FailureAssessmentError("criterion_refs must name at least one failure criterion")
    for ref in criteria:
        _criterion_name(ref)
    evidence = _ref_list(evidence_refs, "evidence_refs", allowed=VALID_EVIDENCE_PREFIXES,
                         allow_empty=False)
    unresolved = _ref_list(unresolved_action_refs, "unresolved_action_refs",
                           allowed=VALID_ACTION_REF_PREFIXES, allow_empty=True)
    if not isinstance(assessed_at, str) or not assessed_at.strip():
        raise FailureAssessmentError("assessed_at is required")
    if assessment_id is None:
        seed = "|".join([activity_id, str(revision), *scope, *criteria, *evidence, assessed_at])
        import hashlib
        assessment_id = "foassess:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    assessment_id = _ref(assessment_id, "assessment_id", VALID_ASSESSMENT_PREFIXES)
    return ActivityFailureAssessment(
        assessment_id=assessment_id,
        activity_id=activity_id,
        activity_revision=revision,
        goal_scope_refs=tuple(scope),
        criterion_refs=tuple(criteria),
        evidence_refs=tuple(evidence),
        unresolved_action_refs=tuple(unresolved),
        assessed_at=assessed_at.strip(),
        notes_code=notes_code,
    )


def validate_failure_payload_against_activity(
    activity: Mapping[str, Any],
    payload: Any,
    *,
    evidence_resolver: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Re-check an assessment payload against the live Activity and owner evidence."""

    if not isinstance(payload, Mapping):
        raise FailureAssessmentError("failure_assessment must be a mapping")
    unknown = set(str(key) for key in payload) - _ALLOWED_ASSESSMENT_KEYS
    if unknown:
        raise FailureAssessmentError(f"unknown failure_assessment fields: {sorted(unknown)}")
    forbidden = FORBIDDEN_ASSESSMENT_KEYS.intersection(str(key) for key in payload)
    if forbidden:
        raise FailureAssessmentError(
            f"self-asserted/model fields are not owner evidence: {sorted(forbidden)}")
    if payload.get("schema_version") != SCHEMA_FAILURE_ASSESSMENT:
        raise FailureAssessmentError("unsupported failure_assessment schema_version")
    if payload.get("assessed_by") != OWNER_LABEL:
        raise FailureAssessmentError(
            f"failure_assessment must be produced by {OWNER_LABEL!r}")
    if payload.get("verdict") != VERDICT_FAILED:
        raise FailureAssessmentError("failure_assessment verdict must be FAILED")

    activity_id = str(activity.get("activity_id") or "")
    if payload.get("activity_id") != activity_id:
        raise FailureAssessmentError(
            f"assessment names {payload.get('activity_id')!r}, live Activity is {activity_id!r}")
    live_revision = activity.get("revision")
    if isinstance(live_revision, bool) or not isinstance(live_revision, int):
        raise FailureAssessmentError("live Activity has no integer revision")
    if payload.get("activity_revision") != live_revision:
        raise FailureAssessmentError(
            "assessment revision does not match the live Activity revision")
    if str(activity.get("status") or "") in {
        "COMPLETED", "FAILED", "ABANDONED", "CANCELLED", "EXPIRED",
    }:
        raise FailureAssessmentError("a terminal Activity cannot be assessed FAILED again")

    assessment_id = _ref(payload.get("assessment_id"), "assessment_id",
                         VALID_ASSESSMENT_PREFIXES)

    scope = _ref_list(payload.get("goal_scope_refs"), "goal_scope_refs", allowed=None,
                      allow_empty=False)
    named = _activity_goal_scope(activity)
    outside = [ref for ref in scope if ref not in named]
    if outside:
        raise FailureAssessmentError(f"goal_scope_refs are not this Activity's own: {outside}")

    criteria = _ref_list(payload.get("criterion_refs"), "criterion_refs",
                         allowed=CRITERION_REF_PREFIXES, allow_empty=False)
    criteria_names = [_criterion_name(ref) for ref in criteria]
    evidence = _ref_list(payload.get("evidence_refs"), "evidence_refs",
                         allowed=VALID_EVIDENCE_PREFIXES, allow_empty=False)
    unresolved = _ref_list(payload.get("unresolved_action_refs"), "unresolved_action_refs",
                           allowed=VALID_ACTION_REF_PREFIXES, allow_empty=True)
    assessed_at = payload.get("assessed_at")
    if not isinstance(assessed_at, str) or not assessed_at.strip():
        raise FailureAssessmentError("assessed_at is required")

    owners: dict[str, str] = {}
    evidence_pairs: list[tuple[str, Mapping[str, Any]]] = []
    for ref in evidence:
        try:
            result = evidence_resolver(ref)
        except FailureAssessmentError:
            raise
        except Exception as exc:
            raise FailureAssessmentError(
                f"owner evidence {ref!r} could not be re-read ({type(exc).__name__})") from exc
        if not isinstance(result, Mapping) or result.get("verified") is not True:
            raise FailureAssessmentError(f"owner evidence {ref!r} is not verified")
        owner = str(result.get("owner") or "").strip()
        if not owner:
            raise FailureAssessmentError(f"owner evidence {ref!r} names no owner")
        owners[ref] = owner
        evidence_pairs.append((ref, result))

    # A named criterion must be *about* the evidence that carries it, and the evidence must be
    # about *this* Activity's own bound goal -- not merely some readable owner row.  A refusal,
    # a timeout, or an unrelated archived piece is not "this goal scope is unreachable".
    for ref, name in zip(criteria, criteria_names):
        if not _evidence_matches_criterion(name, evidence_pairs, activity):
            raise FailureAssessmentError(
                "criterion %r is not supported by the re-read owner evidence: %s "
                "(an UNKNOWN/timeout Action is never one)" % (ref, _criterion_refusal(name)))

    # An unresolved Action is a live obligation, not evidence.  It has to be a real record of
    # *this* Activity and it has to still be unresolved: naming an already-succeeded/failed
    # Action -- or another Activity's open Action -- would rewrite what really happened.
    for ref in unresolved:
        try:
            row = evidence_resolver(ref)
        except FailureAssessmentError:
            raise
        except Exception as exc:
            raise FailureAssessmentError(
                f"unresolved Action {ref!r} could not be re-read ({type(exc).__name__})") from exc
        if not isinstance(row, Mapping) or row.get("verified") is not True:
            raise FailureAssessmentError(f"unresolved Action {ref!r} is not an owner record")
        status = str(row.get("status") or "")
        if status and status not in _UNSETTLED_ACTION_STATUSES:
            raise FailureAssessmentError(
                f"unresolved Action {ref!r} is already {status!r}; it is not an open obligation")
        if not _row_linked_to_activity(row, activity):
            raise FailureAssessmentError(
                f"unresolved Action {ref!r} does not name this Activity "
                f"({activity.get('activity_id')!r}); another Activity's open Action cannot be "
                "borrowed as this one's obligation")

    return {
        "assessment_id": assessment_id,
        "activity_id": activity_id,
        "activity_revision": live_revision,
        "assessed_by": OWNER_LABEL,
        "verdict": VERDICT_FAILED,
        "goal_scope_refs": scope,
        "criterion_refs": criteria,
        "criterion_names": criteria_names,
        "evidence_refs": evidence,
        "evidence_owners": owners,
        "unresolved_action_refs": unresolved,
        "assessed_at": assessed_at.strip(),
    }


def build_blocked_payload(
    activity: Mapping[str, Any],
    *,
    blocked_on_ref: str,
    blocked_reason_code: str,
    criterion_refs: Sequence[str] = (),
    observed_at: str,
) -> dict[str, Any]:
    """A bounded BLOCK basis payload; no free text, no timer and no recovery claim."""

    activity_id = str(activity.get("activity_id") or "")
    revision = activity.get("revision")
    if not activity_id or isinstance(revision, bool) or not isinstance(revision, int):
        raise BlockedBasisError("the Activity record is not readable for a BLOCK basis")
    basis = _ref(blocked_on_ref, "blocked_on_ref", VALID_BLOCK_BASIS_PREFIXES,
                 error_cls=BlockedBasisError)
    criteria = _ref_list(criterion_refs, "blocked_criteria_refs",
                         allowed=CRITERION_REF_PREFIXES, allow_empty=True,
                         error_cls=BlockedBasisError)
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise BlockedBasisError("observed_at is required")
    return {
        "schema_version": SCHEMA_BLOCKED_BASIS,
        "activity_id": activity_id,
        "activity_revision": revision,
        "blocked_on_ref": basis,
        "blocked_reason_code": str(blocked_reason_code or "BLOCKED_BY_CONDITION"),
        "criterion_refs": criteria,
        "observed_at": observed_at.strip(),
        "assessed_by": OWNER_LABEL,
    }


def validate_blocked_payload_against_activity(
    activity: Mapping[str, Any], payload: Any
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BlockedBasisError("blocked_assessment must be a mapping")
    if payload.get("schema_version") != SCHEMA_BLOCKED_BASIS:
        raise BlockedBasisError("unsupported blocked_assessment schema_version")
    if payload.get("assessed_by") != OWNER_LABEL:
        raise BlockedBasisError(f"blocked_assessment must be produced by {OWNER_LABEL!r}")
    activity_id = str(activity.get("activity_id") or "")
    if payload.get("activity_id") != activity_id:
        raise BlockedBasisError("blocked_assessment names a different Activity")
    if payload.get("activity_revision") != activity.get("revision"):
        raise BlockedBasisError("blocked_assessment revision is stale")
    basis = _ref(payload.get("blocked_on_ref"), "blocked_on_ref", VALID_BLOCK_BASIS_PREFIXES,
                 error_cls=BlockedBasisError)
    criteria = _ref_list(payload.get("criterion_refs"), "blocked_criteria_refs",
                         allowed=CRITERION_REF_PREFIXES, allow_empty=True,
                         error_cls=BlockedBasisError)
    return {
        "blocked_on_ref": basis,
        "blocked_reason_code": str(payload.get("blocked_reason_code") or "BLOCKED_BY_CONDITION"),
        "blocked_criteria_refs": criteria,
    }


class ActivityOutcomeService:
    """Thin owner-side wrapper: assess with a read, then call the existing LR-2 command.

    No state of its own.  The capability handed in is the existing opaque
    ACTIVITY_CONSEQUENCE capability minted by the composition root; without it the command
    fails closed inside admission.
    """

    def __init__(
        self,
        *,
        activity_command: Any,
        activity_read: Any,
        activity_store: Any,
        failure_capability: Any,
        blocked_capability: Any,
        evidence_resolver: Callable[[str], Mapping[str, Any]],
        now_iso: Callable[[], str],
        #: The composition root's own AG-2 handoff entry.  Optional so the pure gate can be
        #: built without one; when wired, a *committed* FAILED finishes its one experience
        #: handoff right here instead of leaving it to a demo or a crash recovery.
        failure_handoff: Optional[Callable[[str, "ActivityFailureAssessment"],
                                           Mapping[str, Any]]] = None,
    ) -> None:
        if not callable(evidence_resolver):
            raise TypeError("evidence_resolver must be callable")
        if failure_handoff is not None and not callable(failure_handoff):
            raise TypeError("failure_handoff must be callable when supplied")
        self.activity_command = activity_command
        self.activity_read = activity_read
        self.activity_store = activity_store
        self.failure_capability = failure_capability
        self.blocked_capability = blocked_capability
        self.evidence_resolver = evidence_resolver
        self.failure_handoff = failure_handoff
        self._now_iso = now_iso
        #: What the last committed FAILED really did about its handoff (``None`` before any).
        self.last_failure_handoff: Optional[dict[str, Any]] = None

    def _activity(self, activity_id: str) -> Mapping[str, Any]:
        act = self.activity_read.get_activity(activity_id)
        if act is None:
            raise FailureAssessmentError(f"activity_id={activity_id!r} not found")
        return act

    def _store_revision(self) -> int:
        state, _ = self.activity_store.load_verified_state_and_journal(_include_journal=False)
        return int(state["revision"])

    def assess_failure(
        self,
        activity_id: str,
        *,
        goal_scope_refs: Sequence[str],
        criterion_refs: Sequence[str],
        evidence_refs: Sequence[str],
        unresolved_action_refs: Sequence[str] = (),
        assessed_at: Optional[str] = None,
        notes_code: Optional[str] = None,
    ) -> ActivityFailureAssessment:
        act = self._activity(activity_id)
        assessment = build_failure_assessment(
            act,
            goal_scope_refs=goal_scope_refs,
            criterion_refs=criterion_refs,
            evidence_refs=evidence_refs,
            unresolved_action_refs=unresolved_action_refs,
            assessed_at=assessed_at or self._now_iso(),
            notes_code=notes_code,
        )
        # Fail before any command when the owner evidence cannot be re-read right now.
        validate_failure_payload_against_activity(
            act, assessment.to_payload(), evidence_resolver=self.evidence_resolver)
        return assessment

    def fail_activity(
        self,
        activity_id: str,
        assessment: ActivityFailureAssessment,
        *,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
        causal_parent_refs: Optional[Sequence[str]] = None,
        occurred_at: Optional[str] = None,
        observed_at: Optional[str] = None,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> Any:
        if not isinstance(assessment, ActivityFailureAssessment):
            raise FailureAssessmentError(
                "fail_activity requires an owner-built ActivityFailureAssessment; a mapping or "
                "a model boolean is refused")
        act = self._activity(activity_id)
        # A repeated *operation* (same committed assessment, same terminal) is a replay, not a
        # second assessment: the owner command below recognises its own idempotency key and
        # returns the original transition, and the handoff replays its one event.  Anything
        # else -- a fresh assessment, a stale revision, different evidence -- is validated.
        replaying = (
            str(act.get("status") or "") == "FAILED"
            and str(act.get("failure_assessment_ref") or "") == assessment.assessment_id
        )
        if not replaying:
            validate_failure_payload_against_activity(
                act, assessment.to_payload(), evidence_resolver=self.evidence_resolver)
        revision = (self._store_revision() if expected_revision is None
                    else int(expected_revision))
        result = self.activity_command.fail_activity(
            activity_id=activity_id,
            expected_revision=revision,
            idempotency_key=idempotency_key,
            source_refs=[assessment.assessment_id, *assessment.evidence_refs],
            failure_assessment=assessment.to_payload(),
            causal_parent_refs=list(causal_parent_refs or []),
            occurred_at=occurred_at,
            observed_at=observed_at,
            fault_hook=fault_hook,
            consequence_capability=self.failure_capability,
            proposal_ref=assessment.assessment_id,
        )
        # The terminal is committed; the handoff is a second, exactly-once fact.  A handoff
        # failure is reported on the result and never rolls the Activity back -- the existing
        # recovery path (AG-2 reconcile Phase F) still sees FAILED and emits it later.
        handoff: Optional[dict[str, Any]] = None
        committed_or_replay = (bool(getattr(result, "committed", False))
                               or bool(getattr(result, "idempotent_replay", False)))
        if committed_or_replay and self.failure_handoff is not None:
            try:
                handoff = dict(self.failure_handoff(activity_id, assessment) or {})
            except Exception as exc:
                handoff = {"status": "HANDOFF_FAILED",
                           "error": "%s: %s" % (type(exc).__name__, exc)}
        self.last_failure_handoff = handoff
        if handoff is not None:
            try:
                result.failure_handoff = handoff
            except Exception:  # a frozen result keeps the facts on the service instead
                pass
        return result

    def block_activity(
        self,
        activity_id: str,
        *,
        blocked_on_ref: str,
        blocked_reason_code: str = "BLOCKED_BY_CONDITION",
        criterion_refs: Sequence[str] = (),
        idempotency_key: str,
        expected_revision: Optional[int] = None,
        causal_parent_refs: Optional[Sequence[str]] = None,
        occurred_at: Optional[str] = None,
        observed_at: Optional[str] = None,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> Any:
        act = self._activity(activity_id)
        moment = observed_at or occurred_at or self._now_iso()
        payload = build_blocked_payload(
            act,
            blocked_on_ref=blocked_on_ref,
            blocked_reason_code=blocked_reason_code,
            criterion_refs=criterion_refs,
            observed_at=moment,
        )
        validate_blocked_payload_against_activity(act, payload)
        revision = (self._store_revision() if expected_revision is None
                    else int(expected_revision))
        return self.activity_command.block_activity(
            activity_id=activity_id,
            expected_revision=revision,
            idempotency_key=idempotency_key,
            source_refs=[blocked_on_ref, *criterion_refs],
            blocked_on_ref=blocked_on_ref,
            blocked_reason_code=blocked_reason_code,
            blocked_criteria_refs=list(criterion_refs),
            blocked_assessment=payload,
            causal_parent_refs=list(causal_parent_refs or []),
            occurred_at=occurred_at,
            observed_at=observed_at,
            fault_hook=fault_hook,
            consequence_capability=self.blocked_capability,
            proposal_ref="block:" + str(payload["activity_id"]),
        )


__all__ = [
    "ActivityFailureAssessment",
    "ActivityOutcomeService",
    "ALLOWED_FAILURE_CRITERIA",
    "BlockedBasisError",
    "FailureAssessmentError",
    "OWNER_LABEL",
    "SCHEMA_BLOCKED_BASIS",
    "SCHEMA_FAILURE_ASSESSMENT",
    "build_blocked_payload",
    "build_failure_assessment",
    "validate_blocked_payload_against_activity",
    "validate_failure_payload_against_activity",
]
