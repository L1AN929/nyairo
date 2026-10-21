"""v0.4 W: closing one's own private work -- the pure closeout plan (no owner, no cycle).

What this module is
-------------------
Three pure functions and one parser that describe *how one private work is closed*, so the cycle
(C's write set) can adopt, bind and finish a closeout without inventing a second planner, a second
candidate kind or a second completion vocabulary:

* :func:`closeout_target` / :func:`parse_closeout_target` -- the one target namespace a closeout
  activity is bound to;
* :func:`build_closeout_adoption_spec` -- the AG-2 ``ActivityAdoptionSpec`` content (kind, refs,
  progress contract, the LR-5 completion contract, the result binding);
* :func:`build_closeout_step` -- the one AR-0 step spec that asks the Supply owner to archive the
  work.

Every fact about *whether* the archive happened stays with the owner
(``life_artifact_port.LifeArtifactPort.archive_artifact``), the AR-0 ledger and LR-5/AR-1.  This
module only says what the intent is; it never reads or writes an owner, never judges success, and
holds no state.

The target namespace, exactly
-----------------------------
``closeout:artifact:<owner-id>``, where ``<owner-id>`` is the Supply owner's own ``artifact_id``
**byte for byte**.  The owner mints ids that already start with ``artifact:``
(``artifact/store.py:217`` -- ``aid = "artifact:" + uuid4``), so a real target reads
``closeout:artifact:artifact:<uuid>``.  That is not a double-prefix bug, and it is not "cleaned up"
anywhere:

* :func:`closeout_target` appends the namespace and never strips an inner ``artifact:``;
* :func:`parse_closeout_target` removes **exactly** the namespace prefix and returns the remainder
  verbatim, so ``parse(closeout_target(x))["owner_artifact_id"] == x`` for every accepted ``x``.

That bijection is the whole point: the same bytes that name the work in the target also name it in
the AR-0 payload, in the owner's operation row, in the evidence ``artifact_ref`` and in LR-5's
``expected_artifact_ref``.  A "helpful" strip-and-re-add (the cycle does that for *display* refs,
``autonomous_life_cycle.py:5282-5291``) would silently retarget a work whose real id begins with
``artifact:``, so this module never does it.

The completion contract, and why the predicate is the one it is
---------------------------------------------------------------
This module declares two names, and they are not the same name:

* :data:`CLOSEOUT_PREDICATE` = ``artifact_archived_verified`` -- the attestation the Supply action
  adapter forms from the owner's own operation row + effect receipt + read-back
  (``managed_artifact_action_adapter._observed_archive_result``).  It is what the closeout *means*
  and it is what the step declares in ``expected_effect_predicates``.
* :data:`CLOSEOUT_SETTLED_PREDICATE` = ``artifact_written_verified`` -- the predicate AR-1's
  ``FILE_ACTION`` evaluator actually emits for an observer-verified artifact effect
  (``result_settlement_ar1.py:1790``), and the only artifact predicate that can therefore reach
  LR-5's ``ACTION_RESULT_MATCH`` without changing the LR-5 evaluator or adding a criteria kind.

The completion contract therefore requires :data:`CLOSEOUT_SETTLED_PREDICATE` **plus** the owner's
artifact ref, and demands both ``SETTLED_RESULT_EVENT`` and ``ARTIFACT_CANONICAL_STATE`` evidence.
Archived-ness is not carried by a string in that criteria: it is carried by the fact that the only
step of this activity is the archive, and that its evidence exists only when the owner's own records
say *this operation* archived *this* artifact (see the adapter).

Nothing here completes an activity: an action that SUCCEEDED still has to settle, and the settled
evidence still has to satisfy this contract.  Since :func:`build_closeout_adoption_spec` puts a
completion contract in place, a closeout that is merely "action succeeded" stays open.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

#: The one namespace a closeout target lives in.  Nothing else may use it.
NAMESPACE = "closeout:artifact:"

#: The Activity kind for "end this one private work".  A decision to close is a START of this kind,
#: not a new decision enum: AG-1's own vocabulary is untouched.
ACTIVITY_KIND = "CLOSE_PERSONAL_ARTIFACT"

#: The attestation the Supply action adapter forms from the owner's own records.
CLOSEOUT_PREDICATE = "artifact_archived_verified"
#: The predicate AR-1's existing FILE_ACTION evaluator emits for an observed artifact effect, i.e.
#: the only one LR-5's ACTION_RESULT_MATCH can actually match today.
CLOSEOUT_SETTLED_PREDICATE = "artifact_written_verified"

#: LR-5 progress policy used by every artifact activity (``activity_progress_completion_lr5:150``).
PROGRESS_POLICY_VERSION = "chiyo.life.progress.artifact.v1"
PROGRESS_KIND_ARTIFACT_EDIT = "ARTIFACT_EDIT"
CHECKPOINT_SEMANTICS_LATEST_ARTIFACT_VERSION = "LATEST_ARTIFACT_VERSION"

#: AG-2's own step vocabulary (``life_integration_ag2:438``, ``:455``).
STEP_KIND_FILE_ACTION = "FILE_ACTION"
ACTION_KIND_FILE_ACTION = "FILE_ACTION"
POST_SUBMIT_KEEP_ACTIVE = "KEEP_ACTIVITY_ACTIVE"

#: AR-1's criteria kind for "the settled result of this action matches" (not a new kind).
CRITERIA_ACTION_RESULT_MATCH = "ACTION_RESULT_MATCH"

#: The artifact half of the Supply service's command vocabulary (``life_supply.py:230-233``).
ARCHIVE_OP = "archive_artifact"

#: Evidence the closeout completion may be established from (LR-5's own source kinds).
REQUIRED_EVIDENCE_KINDS = ("SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE")
REQUIRED_AUTHORITY_DOMAINS = ("result_settlement_authority", "artifact_authority")


class CloseoutPlanError(ValueError):
    """A closeout target, spec or step cannot be built from what was given."""


class CloseoutTargetError(CloseoutPlanError):
    """A ref is not a closeout target, or a work id cannot be named by one."""


# ---------------------------------------------------------------------------------
# the target namespace
# ---------------------------------------------------------------------------------


def closeout_target(owner_artifact_id: Any) -> str:
    """``<owner id>`` -> ``closeout:artifact:<owner id>``, all bytes of the id preserved.

    The owner id is taken verbatim (the Supply owner's ids look like ``artifact:<uuid>``), so a real
    target reads ``closeout:artifact:artifact:<uuid>``.  Nothing is stripped and nothing is
    re-prefixed: :func:`parse_closeout_target` returns exactly this id again.
    """
    if not isinstance(owner_artifact_id, str):
        raise CloseoutTargetError(
            f"owner artifact id must be a string, got {type(owner_artifact_id).__name__}")
    artifact_id = owner_artifact_id
    if not artifact_id or artifact_id != artifact_id.strip():
        raise CloseoutTargetError(
            f"owner artifact id must be non-empty with no surrounding whitespace, got "
            f"{owner_artifact_id!r}")
    if any(ch.isspace() for ch in artifact_id):
        raise CloseoutTargetError(
            f"owner artifact id must not contain whitespace, got {owner_artifact_id!r}")
    return f"{NAMESPACE}{artifact_id}"


def parse_closeout_target(ref: Any) -> dict[str, Any]:
    """``closeout:artifact:<owner id>`` -> the work it names, byte for byte.

    ``artifact_ref`` in the result is the owner's own id, i.e. exactly what the AR-0 payload, the
    owner's operation row and the evidence ``artifact_ref`` (and therefore LR-5's
    ``expected_artifact_ref``) use.  A ref in another namespace is refused, not guessed at.
    """
    if not isinstance(ref, str):
        raise CloseoutTargetError(f"closeout target must be a string, got {type(ref).__name__}")
    if not ref.startswith(NAMESPACE):
        raise CloseoutTargetError(f"not a closeout target: {ref!r} (expected {NAMESPACE!r} prefix)")
    owner_artifact_id = ref[len(NAMESPACE):]
    if not owner_artifact_id or owner_artifact_id != owner_artifact_id.strip():
        raise CloseoutTargetError(f"closeout target carries no work id: {ref!r}")
    if any(ch.isspace() for ch in owner_artifact_id):
        raise CloseoutTargetError(f"closeout target work id contains whitespace: {ref!r}")
    return {
        "target_ref": ref,
        "namespace": NAMESPACE,
        "owner_artifact_id": owner_artifact_id,
        "artifact_ref": owner_artifact_id,
    }


def is_closeout_target(ref: Any) -> bool:
    """True only for a well-formed closeout target.  Never raises."""
    try:
        parse_closeout_target(ref)
    except CloseoutPlanError:
        return False
    return True


# ---------------------------------------------------------------------------------
# what a continuation selection may still offer
# ---------------------------------------------------------------------------------


def is_resumable_selection(point: Any) -> bool:
    """True only when the owner says this work can still be continued.

    ``point`` is whatever the caller has: the port's ``ArtifactResumePoint`` (which already answers
    ``resumable``), or a mapping with ``lifecycle`` (+ optionally ``head_version_id``/``content``).
    An ``ARCHIVED``/``TOMBSTONED`` work, or one with no head or no content, is not resumable -- a
    closed work must never be offered as something to continue, and must never be turned into a
    step the owner will refuse.
    """
    if point is None:
        return False
    resumable = getattr(point, "resumable", None)
    if isinstance(resumable, bool):
        return resumable
    if isinstance(point, Mapping):
        if str(point.get("lifecycle") or "") != "ACTIVE":
            return False
        if point.get("head_version_id") is None:
            return False
        if "content" in point and point.get("content") is None:
            return False
        return True
    lifecycle = getattr(point, "lifecycle", None)
    if lifecycle is None:
        return False
    return str(lifecycle) == "ACTIVE"


# ---------------------------------------------------------------------------------
# the identity of one intended archive
# ---------------------------------------------------------------------------------


def archive_idempotency_key(
    owner_artifact_id: Any,
    expected_revision: Any,
    activity_ref: Any,
) -> str:
    """One intended archive of one work, at one revision, from one activity.

    This is the AR-0 ``idempotency_key``.  AR-0 derives the Supply ``operation_key`` from it
    (``SupplyManagedArtifactActionAdapter.operation_key_for``), so the same intent replayed -- after
    an ``UNKNOWN``, or in a later round of the same activity -- resolves the *original* operation
    instead of archiving twice.

    The conversation ``correlation_id`` is deliberately **not** part of the key: the same intent in
    the same activity at the same revision is the same logical action even if it is retried in a
    different round.  A *different* revision is a different intent, and gets its own key (which is
    what makes "the owner moved on" a CONFLICT instead of a silent re-target).
    """
    artifact_id = str(closeout_target(owner_artifact_id)[len(NAMESPACE):])
    revision = _positive_int(expected_revision, "expected_revision")
    activity = str(activity_ref or "").strip()
    if not activity:
        raise CloseoutPlanError("activity_ref is required: one closeout belongs to one activity")
    return f"idem:v04:closeout:{artifact_id}:{revision}:{activity}"


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CloseoutPlanError(f"{field} must be an integer >= 1, got {value!r}")
    return int(value)


# ---------------------------------------------------------------------------------
# the AG-2 adoption spec
# ---------------------------------------------------------------------------------


def build_closeout_adoption_spec(
    owner_artifact_id: Any,
    *,
    title: Optional[str] = None,
) -> dict[str, Any]:
    """The ``ActivityAdoptionSpec`` content for "end this one work".

    The result is a plain dict of real ``ActivityAdoptionSpec`` field names, so the caller builds
    the dataclass with ``ag2.ActivityAdoptionSpec.from_dict(spec)`` (fields left out keep their
    dataclass defaults).  Returned keys:

    ``activity_kind``, ``title``, ``target_refs``, ``origin_ref``, ``progress_contract_spec``,
    ``completion_contract_spec``, ``result_binding_spec``.

    ``target_refs`` = ``[closeout target, owner artifact ref]`` -- exactly two refs, both in AG-2's
    structured form (``^[a-z][a-z0-9_]{1,39}:[A-Za-z0-9][A-Za-z0-9_.:/-]{0,160}$``,
    ``activity_continuity.py:227``).  The owner ref is the same string the settlement evidence
    carries, which is what lets the result binding match it.

    The owner's **version** id is deliberately not a target ref: the owner spells it
    ``artifact-version:<uuid>``, whose namespace has a hyphen and therefore is *not* a valid AG-2
    structured ref (the cycle re-namespaces it as ``artifactversion:<bare>`` for display only), and
    a closeout is about the work, not about one version of it.

    Nothing here decides *whether* to close: that is the caller's candidacy rule (a work that is
    still ACTIVE, bound to this activity, with no occupancy conflict, and never a material, a PDI
    document or another work).
    """
    target = closeout_target(owner_artifact_id)
    artifact_ref = parse_closeout_target(target)["owner_artifact_id"]
    refs = [target, artifact_ref]
    return {
        "activity_kind": ACTIVITY_KIND,
        "title": str(title) if title else f"收尾 {artifact_ref}",
        "target_refs": refs,
        "origin_ref": target,
        "progress_contract_spec": {
            # Progress on a work is still "the latest version the owner confirmed" (LR-5's own
            # artifact policy); an archive adds no version, it ends the work.  Progress is not the
            # completion mechanism.
            "progress_kind": PROGRESS_KIND_ARTIFACT_EDIT,
            "checkpoint_semantics": CHECKPOINT_SEMANTICS_LATEST_ARTIFACT_VERSION,
            "target_refs": refs,
            "policy_version": PROGRESS_POLICY_VERSION,
        },
        "completion_contract_spec": {
            "criteria_kind": CRITERIA_ACTION_RESULT_MATCH,
            "criteria": {
                # What AR-1 really settles for an observed artifact effect (see module docstring),
                # scoped to this work by the owner's own ref.
                "required_predicate": CLOSEOUT_SETTLED_PREDICATE,
                "expected_artifact_ref": artifact_ref,
            },
            "target_refs": refs,
            "required_evidence_kinds": list(REQUIRED_EVIDENCE_KINDS),
            "required_authority_domains": list(REQUIRED_AUTHORITY_DOMAINS),
            "policy_version": PROGRESS_POLICY_VERSION,
        },
        "result_binding_spec": {
            "target_refs": refs,
            "allowed_evidence_kinds": list(REQUIRED_EVIDENCE_KINDS),
            "authority_domains": list(REQUIRED_AUTHORITY_DOMAINS),
            "policy_version": PROGRESS_POLICY_VERSION,
        },
    }


# ---------------------------------------------------------------------------------
# the one AR-0 step
# ---------------------------------------------------------------------------------


def build_closeout_step(
    owner_artifact_id: Any,
    expected_revision: Any,
    *,
    activity_ref: Any,
    correlation_id: Any = None,
    grant_id: Optional[str] = None,
) -> dict[str, Any]:
    """The one bounded step that asks the owner to archive this work.

    Returned keys are the same seven the cycle's existing work step uses
    (``autonomous_life_cycle._ar0_step_spec``), so the caller adds the identity fields it alone
    owns (``step_spec_id``, ``activity_ref``, ``activity_revision``, ``decision_ref``,
    ``adoption_ref``, ``requires_waiting``, ``waiting_condition_spec``, ``created_at``):

    * ``step_kind`` = ``FILE_ACTION``, ``action_kind`` = ``FILE_ACTION`` -- the same AR-0 kind a
      version commit uses; the closeout is not a new action kind;
    * ``target_ref`` = the closeout target;
    * ``payload_spec`` = ``{"op": "archive_artifact", "artifact_id": <owner id>, "expected_revision":
      <int>}`` (+ ``grant_id`` when given) -- the complete intent.  The Supply operation key is
      *not* in the payload: AR-0 derives it from ``idempotency_key``;
    * ``expected_effect_predicates`` = ``["artifact_archived_verified"]`` -- the attestation the
      adapter forms from the owner's own records;
    * ``post_submit_life_behavior`` = ``KEEP_ACTIVITY_ACTIVE`` -- submitting is not completing;
    * ``idempotency_key`` = :func:`archive_idempotency_key` (the original key an ``UNKNOWN`` is
      resolved with, never a fresh one).

    ``expected_revision`` must be the owner's artifact ``revision`` read before deciding; this
    function never substitutes a "current" revision, because a swapped revision would archive a
    work the decision was not about.
    """
    target = closeout_target(owner_artifact_id)
    artifact_ref = parse_closeout_target(target)["owner_artifact_id"]
    revision = _positive_int(expected_revision, "expected_revision")
    payload: dict[str, Any] = {
        "op": ARCHIVE_OP,
        "artifact_id": artifact_ref,
        "expected_revision": revision,
    }
    if grant_id is not None:
        if not str(grant_id).strip():
            raise CloseoutPlanError(f"grant_id must be non-empty when given, got {grant_id!r}")
        payload["grant_id"] = str(grant_id)
    return {
        "step_kind": STEP_KIND_FILE_ACTION,
        "action_kind": ACTION_KIND_FILE_ACTION,
        "target_ref": target,
        "payload_spec": payload,
        "expected_effect_predicates": [CLOSEOUT_PREDICATE],
        "post_submit_life_behavior": POST_SUBMIT_KEEP_ACTIVE,
        "idempotency_key": archive_idempotency_key(artifact_ref, revision, activity_ref),
    }


__all__ = [
    "ACTION_KIND_FILE_ACTION",
    "ACTIVITY_KIND",
    "ARCHIVE_OP",
    "CHECKPOINT_SEMANTICS_LATEST_ARTIFACT_VERSION",
    "CLOSEOUT_PREDICATE",
    "CLOSEOUT_SETTLED_PREDICATE",
    "CRITERIA_ACTION_RESULT_MATCH",
    "CloseoutPlanError",
    "CloseoutTargetError",
    "NAMESPACE",
    "POST_SUBMIT_KEEP_ACTIVE",
    "PROGRESS_KIND_ARTIFACT_EDIT",
    "PROGRESS_POLICY_VERSION",
    "REQUIRED_AUTHORITY_DOMAINS",
    "REQUIRED_EVIDENCE_KINDS",
    "STEP_KIND_FILE_ACTION",
    "archive_idempotency_key",
    "build_closeout_adoption_spec",
    "build_closeout_step",
    "closeout_target",
    "is_closeout_target",
    "is_resumable_selection",
    "parse_closeout_target",
]
