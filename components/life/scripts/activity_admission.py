"""S5.1 candidate: owner-backed admission issuers, opaque one-shot permits and
typed ACTIVITY_CONSEQUENCE capabilities.

Authority model (this module introduces NO new fact owner):

  * ``AGENCY_AUTHORIZED``  - an AG-1 Decision owner record plus an AG-2 Adoption
    owner command-scoped authorization.  The caller holds no authority of its
    own; it holds two owner references that ``ProvenanceVerifier`` re-reads from
    the owners.

  * ``ACTIVITY_CONSEQUENCE`` - an already canonical Activity lineage plus
    canonical evidence produced by the *existing* owner that caused the change:
    LR-3 interruption assessments/pendings, LR-4 waiting conditions, LR-5
    progress/completion eligibilities, AR-0/AR-1 action and settlement facts.
    The proposal is a typed value object; it is authenticated by an opaque
    capability that only the composition root can mint, and the capability
    itself carries the evidence verifier bound to that owner's read port.

  * ``MIGRATION`` / ``RECOVERY`` - no owner is established in this candidate, so
    there is no capability to hand out and every attempt fails closed.

Capability possession, not caller self-report, is what grants authority: a
caller can neither construct a capability nor change its allowed transition
kinds or evidence verifier.  LR-2 only checks that the permit it consumes was
minted for exactly this command and this runtime.
"""
from __future__ import annotations
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from provenance_verifier import ProvenanceVerifier, ProvenanceRejected

TRANSITION_KINDS = frozenset(
    {
        "START",
        "PAUSE",
        "WAIT",
        "RESUME",
        "COMPLETE",
        "ABANDON",
        "CANCEL",
        "EXPIRE",
        "CHECKPOINT",
        "PROGRESS",
        # 2026-10-28: BLOCKED is a non-terminal suspension the owner applies from an explicit
        # blocking basis; FAILED is the owner's assessed terminal.  Neither is a model verb.
        "BLOCK",
        "FAIL",
        # External stop: a durable local-operator intent (never a model verb) and the terminal
        # that follows it once the owner reaches a safe boundary.
        "STOP_INTENT",
    }
)

AUTHORITY_AGENCY = "AGENCY_AUTHORIZED"
AUTHORITY_CONSEQUENCE = "ACTIVITY_CONSEQUENCE"
AUTHORITY_MIGRATION = "MIGRATION"
AUTHORITY_RECOVERY = "RECOVERY"

REASON_KIND_RUNTIME_CONSTRAINT = "RUNTIME_CONSTRAINT"
REASON_KIND_AGENCY_CHOICE = "AGENCY_CHOICE"

# Literal copies of the canonical status vocabulary (activity_continuity owns the
# originals; importing them here would create an import cycle).
STATUS_ACTIVE = "ACTIVE"
STATUS_PAUSED = "PAUSED"
STATUS_WAITING = "WAITING"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "FAILED"
# BLOCKED is non-terminal: the Activity has not been given up, it cannot advance right now.
NON_TERMINAL_ACTIVITY_STATUSES = frozenset(
    {STATUS_ACTIVE, STATUS_PAUSED, STATUS_WAITING, STATUS_BLOCKED}
)
TERMINAL_ACTIVITY_STATUSES = frozenset(
    {"COMPLETED", "ABANDONED", "CANCELLED", "CANCELED", "EXPIRED", STATUS_FAILED}
)

# Literal copy of activity_continuity.VALID_EVIDENCE_PREFIXES.
ALLOWED_COMPLETION_EVIDENCE_PREFIXES = frozenset(
    {
        "action_receipt",
        "act_receipt",
        "artifact_rev",
        "art_rev",
        "artifact",
        "artifact_file",
        "file",
        "world_fact",
        "world",
        "tool_result",
        "tool",
        "game_state",
        "game",
        "doc_state",
        "document",
        "doc",
        "book",
        "external_result",
        "export",
        "evd_receipt",
        "evidence",
        "settlement",
        "rset",
        "srev",
        "settled_result",
        "aevid",
        "arcpt",
        "eclaim",
        "celig",
        "comp_elig",
        "completion_eligibility",
        "pevid",
        "progress_evidence",
    }
)

# S7 Phase-F (order sections 12-13): the ACTIVITY_CONSEQUENCE waiting-evidence decision is
# DERIVED from the canonical waiting-domain schema owned by LR-4 (`waiting_ref_vocab`),
# instead of being a second hand-written prefix list. The historical name is kept for the
# downstream call sites; the set now has exactly one owner.
from waiting_ref_vocab import (  # noqa: E402
    ADMISSIBLE_WAITING_EVIDENCE_PREFIXES as ALLOWED_WAITING_REF_PREFIXES,
)

from waiting_ref_vocab import parse_waiting_ref as _parse_waiting_ref  # noqa: E402

INTERRUPTION_REASON_CODES = frozenset(
    {
        "USER_INTERRUPTION",
        "SAFE_BOUNDARY_PAUSE",
        "INTERRUPT_NOW",
        "EXTERNAL_INTERRUPT",
        "WORLD_URGENCY_PAUSE",
        "BODY_CAPACITY_PAUSE",
    }
)

NON_CANONICAL_COMPLETION_REASON_CODES = frozenset(
    {"ACTION_UNKNOWN", "ACTION_FAILED", "ACTION_FAILURE", "UNKNOWN", "FAILED"}
)


class AdmissionRejected(PermissionError):
    """Raised when a transition cannot be admitted under any authority class."""


class ConsequenceRejected(PermissionError):
    """Raised when a consequence proposal is not backed by canonical evidence."""


class _Permit:
    __slots__ = ("_issuer", "_store", "_nonce", "_binding", "_owner_snapshot")

    def __init__(
        self,
        issuer: object,
        store: object,
        nonce: object,
        binding: str,
        owner_snapshot: Any,
    ):
        self._issuer = issuer
        self._store = store
        self._nonce = nonce
        self._binding = binding
        self._owner_snapshot = owner_snapshot

    def __reduce__(self):
        raise TypeError("admission permits are process-local and non-serializable")


class ActivityConsequenceCapability:
    """Opaque, composition-root-only capability for one consequence producer.

    The holder can propose exactly the transition kinds it was granted, and only
    with evidence that its own owner read port accepts.  It cannot widen its own
    scope: every field is fixed at construction and the object is neither
    constructible nor serializable by a caller.
    """

    __slots__ = (
        "_issuer",
        "_epoch",
        "_identity",
        "_allowed_kinds",
        "_owner_label",
        "_evidence_verifier",
        "_nonce",
    )

    def __init__(
        self,
        issuer: object,
        epoch: str,
        allowed_kinds: Sequence[str],
        owner_label: str,
        evidence_verifier: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ):
        self._issuer = issuer
        self._epoch = epoch
        self._identity = object()
        self._allowed_kinds = frozenset(allowed_kinds)
        self._owner_label = owner_label
        self._evidence_verifier = evidence_verifier
        self._nonce = object()

    @property
    def owner_label(self) -> str:
        return self._owner_label

    @property
    def allowed_kinds(self) -> frozenset:
        return self._allowed_kinds

    def __reduce__(self):
        raise TypeError(
            "activity consequence capabilities are process-local and non-serializable"
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"<ActivityConsequenceCapability owner={self._owner_label!r} "
            f"kinds={sorted(self._allowed_kinds)}>"
        )


class AdmissionIssuer:
    """Fixed verifier/permit authority. Not dynamically replaceable after construction."""

    __slots__ = (
        "_verifier",
        "_epoch",
        "_identity",
        "_store_identity",
        "_issued",
        "_lock",
        "_capabilities",
    )

    def __init__(self, decision_reader: Any, adoption_reader: Any, runtime_epoch: str):
        if not runtime_epoch:
            raise TypeError("runtime_epoch is required")
        self._verifier = ProvenanceVerifier(decision_reader, adoption_reader)
        self._epoch = runtime_epoch
        self._identity = object()
        self._store_identity = None
        self._issued: dict[int, tuple[Any, Any, str]] = {}
        self._lock = threading.Lock()
        self._capabilities: dict[int, ActivityConsequenceCapability] = {}

    # -- binding -----------------------------------------------------------------
    def _bind_store_once(self, store_identity: object) -> None:
        if store_identity is None:
            raise TypeError("store identity required")
        with self._lock:
            if self._store_identity is not None:
                raise AdmissionRejected("ISSUER_ALREADY_BOUND_TO_STORE")
            self._store_identity = store_identity

    @staticmethod
    def _binding(transition: Mapping[str, Any]) -> str:
        fields = {
            k: transition.get(k)
            for k in (
                "transition_id",
                "transition_type",
                "activity_id",
                "expected_revision",
                "result_revision",
                "idempotency_key",
                "decision_ref",
                "adoption_ref",
                "proposal_ref",
            )
        }
        return hashlib.sha256(
            json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    @property
    def runtime_epoch(self) -> str:
        return self._epoch

    # -- capabilities ------------------------------------------------------------
    def build_consequence_capability(
        self,
        *,
        allowed_kinds: Sequence[str],
        owner_label: str,
        evidence_verifier: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> ActivityConsequenceCapability:
        """Composition-root factory. No other code path can mint a capability."""
        kinds = frozenset(allowed_kinds)
        if not kinds:
            raise TypeError("at least one allowed transition kind is required")
        unknown = kinds - TRANSITION_KINDS
        if unknown:
            raise TypeError(f"unknown transition kinds requested: {sorted(unknown)}")
        if not isinstance(owner_label, str) or not owner_label.strip():
            raise TypeError("owner_label is required")
        if not callable(evidence_verifier):
            raise TypeError("evidence_verifier must be callable")
        capability = ActivityConsequenceCapability(
            self._identity, self._epoch, kinds, owner_label.strip(), evidence_verifier
        )
        with self._lock:
            self._capabilities[id(capability._identity)] = capability
        return capability

    def _require_consequence_capability(self, capability: Any) -> ActivityConsequenceCapability:
        if not isinstance(capability, ActivityConsequenceCapability):
            raise AdmissionRejected("CONSEQUENCE_CAPABILITY_REQUIRED")
        if capability._issuer is not self._identity:
            raise AdmissionRejected("CONSEQUENCE_CAPABILITY_WRONG_RUNTIME")
        with self._lock:
            known = self._capabilities.get(id(capability._identity))
        if known is not capability:
            raise AdmissionRejected("CONSEQUENCE_CAPABILITY_UNKNOWN")
        if capability._epoch != self._epoch:
            raise AdmissionRejected("CONSEQUENCE_CAPABILITY_STALE_RUNTIME_EPOCH")
        return capability

    # -- permit minting ----------------------------------------------------------
    def issue(self, transition: Mapping[str, Any]) -> _Permit:
        """AGENCY_AUTHORIZED permit: Decision owner record + Adoption owner grant."""
        kind = transition.get("transition_type")
        if kind not in TRANSITION_KINDS:
            raise AdmissionRejected("UNKNOWN_MUTATION_CLASS")
        decision_ref = transition.get("decision_ref")
        adoption_ref = transition.get("adoption_ref")
        try:
            proof = self._verifier.verify(
                decision_ref=decision_ref,
                adoption_ref=adoption_ref,
                expected_activity_ref=transition.get("activity_id"),
                expected_command_id=transition.get("idempotency_key"),
                expected_mutation_kind=kind,
            )
        except (ProvenanceRejected, TypeError, ValueError) as exc:
            raise AdmissionRejected(
                f"OWNER_PROVENANCE_REJECTED:{type(exc).__name__}:{exc}"
            ) from exc
        snapshot = (
            proof.decision_id,
            proof.decision_revision,
            proof.adoption_id,
            proof.adoption_revision,
            proof.adoption_status,
            self._epoch,
        )
        return self._mint(transition, snapshot)

    def issue_consequence(
        self,
        capability: Any,
        transition: Mapping[str, Any],
        *,
        canonical_state: Any = None,
    ) -> _Permit:
        """ACTIVITY_CONSEQUENCE permit: canonical Activity lineage + owner evidence."""
        capability = self._require_consequence_capability(capability)
        kind = transition.get("transition_type")
        if kind not in TRANSITION_KINDS:
            raise AdmissionRejected("UNKNOWN_MUTATION_CLASS")
        if kind not in capability._allowed_kinds:
            raise AdmissionRejected(
                f"CONSEQUENCE_KIND_NOT_GRANTED:{kind}:{capability._owner_label}"
            )
        if transition.get("decision_ref") or transition.get("adoption_ref"):
            raise AdmissionRejected("CONSEQUENCE_PROPOSAL_MUST_NOT_CARRY_AGENCY_REFS")
        try:
            evidence = capability._evidence_verifier(transition, canonical_state)
        except ConsequenceRejected as exc:
            raise AdmissionRejected(f"CONSEQUENCE_EVIDENCE_REJECTED:{exc}") from exc
        except (TypeError, ValueError, KeyError) as exc:
            raise AdmissionRejected(
                f"CONSEQUENCE_EVIDENCE_REJECTED:{type(exc).__name__}:{exc}"
            ) from exc
        if not isinstance(evidence, Mapping):
            raise AdmissionRejected("CONSEQUENCE_EVIDENCE_MALFORMED")
        snapshot = dict(evidence)
        snapshot.update(
            {
                "authority_class": AUTHORITY_CONSEQUENCE,
                "owner_label": capability._owner_label,
                "runtime_epoch": self._epoch,
                "validated_at_command_id": transition.get("idempotency_key"),
                "proposal_ref": transition.get("proposal_ref"),
            }
        )
        return self._mint(transition, snapshot)

    def _mint(self, transition: Mapping[str, Any], snapshot: Any) -> _Permit:
        store_identity = self._store_identity
        if store_identity is None:
            raise AdmissionRejected("ISSUER_NOT_BOUND_TO_STORE")
        binding = self._binding(transition)
        nonce = object()
        permit = _Permit(self._identity, store_identity, nonce, binding, snapshot)
        with self._lock:
            self._issued[id(permit)] = (permit, nonce, binding)
        return permit

    def consume(self, permit: Any, transition: Mapping[str, Any]) -> Any:
        with self._lock:
            item = self._issued.pop(id(permit), None)
        if item is None or item[0] is not permit:
            raise AdmissionRejected("PERMIT_UNKNOWN_OR_REPLAYED")
        if not isinstance(permit, _Permit) or permit._issuer is not self._identity:
            raise AdmissionRejected("PERMIT_WRONG_RUNTIME")
        if permit._store is not self._store_identity:
            raise AdmissionRejected("PERMIT_WRONG_STORE")
        binding = self._binding(transition)
        if item[2] != binding or permit._binding != binding:
            raise AdmissionRejected("PERMIT_COMMAND_BINDING_MISMATCH")
        return permit._owner_snapshot


# ---------------------------------------------------------------------------
# Typed consequence proposals (value objects; carry claims, never authority)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WaitingProposal:

    activity_id: str
    expected_revision: int
    waiting_on_ref: str
    proposal_id: str
    reason_kind: str = REASON_KIND_RUNTIME_CONSTRAINT
    resume_condition_ref: Optional[str] = None
    waiting_episode_id: Optional[str] = None
    release_foreground: bool = False
    idempotency_key: Optional[str] = None
    source_refs: tuple[str, ...] = ()
    causal_parent_refs: tuple[str, ...] = ()
    occurred_at: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class CompletionProposal:
    """LR-5: canonical completion eligibility reached for a real Activity."""

    activity_id: str
    expected_revision: int
    completion_evidence_ref: str
    proposal_id: str
    eligibility_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    checkpoint_ref: Optional[str] = None
    last_action_ref: Optional[str] = None
    source_refs: tuple[str, ...] = ()
    causal_parent_refs: tuple[str, ...] = ()
    occurred_at: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class InterruptionProposal:
    """LR-3: a persisted interruption assessment/boundary requests a pause."""

    activity_id: str
    expected_revision: int
    proposal_id: str
    interruption_ref: str
    reason_code: str = "USER_INTERRUPTION"
    idempotency_key: Optional[str] = None
    source_refs: tuple[str, ...] = ()
    causal_parent_refs: tuple[str, ...] = ()
    occurred_at: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class BlockedProposal:
    """S09/suspension owner: a named basis says this Activity cannot advance now.

    Non-terminal by construction.  It carries the basis the owner read, never a model word
    and never a timer; a later recovery still needs an explicit condition plus a fresh
    Decision RESUME.
    """

    activity_id: str
    expected_revision: int
    proposal_id: str
    blocked_on_ref: str
    blocked_reason_code: str = "BLOCKED_BY_CONDITION"
    blocked_criteria_refs: tuple[str, ...] = ()
    blocked_assessment: Optional[Mapping[str, Any]] = None
    idempotency_key: Optional[str] = None
    source_refs: tuple[str, ...] = ()
    causal_parent_refs: tuple[str, ...] = ()
    occurred_at: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class FailureProposal:
    """S09 owner: an explicit assessed failure of this Activity's goal scope.

    The payload is produced by the owner gate (``activity_outcome_assessment``) and is
    re-verified with owner evidence inside admission.  It is not a completion, not an
    Action outcome, and it never rewrites an UNKNOWN as a failure.
    """

    activity_id: str
    expected_revision: int
    proposal_id: str
    failure_assessment: Mapping[str, Any]
    idempotency_key: Optional[str] = None
    source_refs: tuple[str, ...] = ()
    causal_parent_refs: tuple[str, ...] = ()
    occurred_at: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class ExternalStopProposal:
    """Local-operator stop: a durable "stop this Activity" fact, not a cancel.

    The payload was built by the composition root after the operator credential was checked;
    it names no actor, subject or authority of its own.  The owner accepts it, and from then
    on every new dispatch for this Activity is blocked until the terminal is reached.
    """

    activity_id: str
    expected_revision: int
    proposal_id: str
    external_stop: Mapping[str, Any]
    idempotency_key: Optional[str] = None
    source_refs: tuple[str, ...] = ()
    causal_parent_refs: tuple[str, ...] = ()
    occurred_at: Optional[str] = None
    observed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Typed ports: the composition root hands out exactly one port per producer.
# ---------------------------------------------------------------------------


class ActivityConsequencePort:
    """Typed ACTIVITY_CONSEQUENCE port. Nothing generic, no authority class string."""

    __slots__ = ("_capability", "_command", "_issuer")

    def __init__(self, capability: ActivityConsequenceCapability, command_service: Any):
        if command_service is None:
            raise TypeError("LR-2 command service is required")
        self._capability = capability
        self._command = command_service
        self._issuer = capability._issuer

    @property
    def owner_label(self) -> str:
        return self._capability.owner_label

    @property
    def allowed_kinds(self) -> frozenset:
        return self._capability.allowed_kinds

    @property
    def capability(self) -> ActivityConsequenceCapability:
        """The opaque capability, passed to LR-2 as proof of possession."""
        return self._capability

    def propose_waiting(self, proposal: WaitingProposal) -> Any:
        return self._command.wait_activity(
            activity_id=proposal.activity_id,
            waiting_on_ref=proposal.waiting_on_ref,
            resume_condition_ref=proposal.resume_condition_ref,
            waiting_episode_id=proposal.waiting_episode_id,
            release_foreground=proposal.release_foreground,
            expected_revision=proposal.expected_revision,
            idempotency_key=proposal.idempotency_key
            or f"lr4_wait:{proposal.proposal_id}",
            source_refs=list(proposal.source_refs) or [proposal.proposal_id],
            causal_parent_refs=list(proposal.causal_parent_refs),
            reason_code=_wait_reason_code(proposal.reason_kind),
            occurred_at=proposal.occurred_at,
            observed_at=proposal.observed_at,
            consequence_capability=self._capability,
            proposal_ref=proposal.proposal_id,
            authority_reason_kind=proposal.reason_kind,
        )

    def propose_completion(self, proposal: CompletionProposal) -> Any:
        return self._command.complete_activity(
            activity_id=proposal.activity_id,
            completion_evidence_ref=proposal.completion_evidence_ref,
            expected_revision=proposal.expected_revision,
            idempotency_key=proposal.idempotency_key
            or f"lr5_complete:{proposal.proposal_id}",
            source_refs=list(proposal.source_refs) or [proposal.proposal_id],
            causal_parent_refs=list(proposal.causal_parent_refs),
            checkpoint_ref=proposal.checkpoint_ref,
            last_action_ref=proposal.last_action_ref,
            reason_code="COMPLETED_WITH_EVIDENCE",
            occurred_at=proposal.occurred_at,
            observed_at=proposal.observed_at,
            consequence_capability=self._capability,
            proposal_ref=proposal.proposal_id,
        )

    def propose_interruption_pause(self, proposal: InterruptionProposal) -> Any:
        return self._command.pause_activity(
            activity_id=proposal.activity_id,
            expected_revision=proposal.expected_revision,
            idempotency_key=proposal.idempotency_key
            or f"lr3_pause:{proposal.proposal_id}",
            source_refs=list(proposal.source_refs) or [proposal.interruption_ref],
            causal_parent_refs=list(proposal.causal_parent_refs)
            or [proposal.interruption_ref],
            reason_code=proposal.reason_code,
            occurred_at=proposal.occurred_at,
            observed_at=proposal.observed_at,
            consequence_capability=self._capability,
            proposal_ref=proposal.proposal_id,
        )

    def propose_checkpoint_or_progress(self, transition_type: str, **kwargs: Any) -> Any:
        if transition_type not in {"CHECKPOINT", "PROGRESS"}:
            raise ConsequenceRejected("CONSEQUENCE_PORT_ONLY_SERVES_CHECKPOINT_OR_PROGRESS")
        kwargs.setdefault("consequence_capability", self._capability)
        kwargs.setdefault("proposal_ref", kwargs.get("idempotency_key"))
        return self._command.update_checkpoint(**kwargs)

    def propose_blocked(self, proposal: BlockedProposal) -> Any:
        return self._command.block_activity(
            activity_id=proposal.activity_id,
            expected_revision=proposal.expected_revision,
            idempotency_key=proposal.idempotency_key
            or f"lr2_block:{proposal.proposal_id}",
            source_refs=list(proposal.source_refs) or [proposal.blocked_on_ref],
            blocked_on_ref=proposal.blocked_on_ref,
            blocked_reason_code=proposal.blocked_reason_code,
            blocked_criteria_refs=list(proposal.blocked_criteria_refs),
            blocked_assessment=proposal.blocked_assessment,
            causal_parent_refs=list(proposal.causal_parent_refs),
            occurred_at=proposal.occurred_at,
            observed_at=proposal.observed_at,
            consequence_capability=self._capability,
            proposal_ref=proposal.proposal_id,
        )

    def propose_failure(self, proposal: FailureProposal) -> Any:
        return self._command.fail_activity(
            activity_id=proposal.activity_id,
            expected_revision=proposal.expected_revision,
            idempotency_key=proposal.idempotency_key
            or f"lr2_fail:{proposal.proposal_id}",
            source_refs=list(proposal.source_refs) or ["foassess:missing"],
            failure_assessment=proposal.failure_assessment,
            causal_parent_refs=list(proposal.causal_parent_refs),
            occurred_at=proposal.occurred_at,
            observed_at=proposal.observed_at,
            consequence_capability=self._capability,
            proposal_ref=proposal.proposal_id,
        )

    def propose_external_stop(self, proposal: ExternalStopProposal) -> Any:
        return self._command.record_external_stop(
            activity_id=proposal.activity_id,
            expected_revision=proposal.expected_revision,
            idempotency_key=proposal.idempotency_key
            or f"opstop_intent:{proposal.proposal_id}",
            source_refs=list(proposal.source_refs) or [proposal.proposal_id],
            external_stop=dict(proposal.external_stop),
            causal_parent_refs=list(proposal.causal_parent_refs),
            occurred_at=proposal.occurred_at,
            observed_at=proposal.observed_at,
            consequence_capability=self._capability,
            proposal_ref=proposal.proposal_id,
        )

    def propose_external_cancel(
        self,
        *,
        activity_id: str,
        expected_revision: int,
        intent_id: str,
        idempotency_key: str,
        source_refs: Sequence[str],
        occurred_at: Optional[str] = None,
        observed_at: Optional[str] = None,
        causal_parent_refs: Sequence[str] = (),
    ) -> Any:
        """Finish a durable stop as CANCELLED at the owner-confirmed safe boundary.

        The capability must also carry CANCEL; the verifier re-reads the live Activity and
        refuses when the pending intent named here is not the one the owner already accepted.
        """

        return self._command.cancel_activity(
            activity_id=activity_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            source_refs=list(source_refs) or [intent_id],
            causal_parent_refs=list(causal_parent_refs),
            reason_code="CANCELLED_BY_EXTERNAL_OPERATOR",
            occurred_at=occurred_at,
            observed_at=observed_at,
            consequence_capability=self._capability,
            proposal_ref=intent_id,
        )


def _wait_reason_code(reason_kind: str) -> str:
    if reason_kind == REASON_KIND_AGENCY_CHOICE:
        # Canonical agency wait reason code (activity_continuity.REASON_CODES).
        return "ADOPTED_DECISION"
    # Canonical runtime-constraint wait reason code (activity_continuity.REASON_CODES).
    return "EXTERNAL_DEPENDENCY_WAIT"


def _reason_kind_of(transition: Mapping[str, Any]) -> Optional[str]:
    """Recover the declared reason kind, falling back to the recorded reason code.

    The fallback only recognises the two reason codes this module emits, so a
    caller cannot smuggle an arbitrary string in as an authority class.
    """
    declared = transition.get("authority_reason_kind")
    if declared is not None:
        return declared
    reason_code = transition.get("reason_code")
    if reason_code == "RUNTIME_CONSTRAINT_WAIT":
        return REASON_KIND_RUNTIME_CONSTRAINT
    if reason_code in {"VOLUNTARY_WAIT", "ADOPTED_DECISION"}:
        return REASON_KIND_AGENCY_CHOICE
    if reason_code == "EXTERNAL_DEPENDENCY_WAIT":
        return REASON_KIND_RUNTIME_CONSTRAINT
    return reason_code


# ---------------------------------------------------------------------------
# Evidence verifiers. Each closes over the read port of the owner that really
# holds the evidence; none of them invents a new fact domain.
# ---------------------------------------------------------------------------


def build_waiting_evidence_verifier(
    activity_reader: Any,
    *,
    allowed_reason_kinds: Sequence[str] = (REASON_KIND_RUNTIME_CONSTRAINT,),
):
    allowed_reason_kinds = frozenset(allowed_reason_kinds)

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        activity_id = transition.get("activity_id")
        expected_revision = transition.get("expected_revision")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ConsequenceRejected("CONSEQUENCE_MISSING_ACTIVITY_ID")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        status = act.get("status")
        if status not in NON_TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(f"CONSEQUENCE_ACTIVITY_STATUS_FORBIDS_WAIT:{status}")
        actual_revision = int(act.get("revision", -1))
        # `expected_revision` is LR-2's canonical-STORE revision -- the same token the
        # journal replays as `result_revision - 1` -- and NOT the per-Activity snapshot
        # revision. Comparing it against `act["revision"]` only happened to work while
        # the target Activity was the most recently transitioned one, and it rejected
        # every legitimate consequence on a store holding more than one Activity.
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        reason_kind = _reason_kind_of(transition)
        if reason_kind not in allowed_reason_kinds:
            raise ConsequenceRejected(f"CONSEQUENCE_WRONG_REASON_KIND:{reason_kind}")
        waiting_on_ref = _evidence_field(transition, "waiting_on_ref")
        # Order section 12: ask the waiting domain's own parser. The kind is READ from the
        # reference and checked against the canonical schema; it is never inferred from how
        # the string happens to be shaped.
        if _parse_waiting_ref(waiting_on_ref) is None:
            raise ConsequenceRejected("CONSEQUENCE_WAITING_CONSTRAINT_REF_MISSING")
        if not list(transition.get("source_refs") or []):
            raise ConsequenceRejected("CONSEQUENCE_WAITING_REQUIRES_SOURCE_REFS")
        return {
            "activity_id": activity_id,
            "activity_revision": actual_revision,
            "activity_status": status,
            "waiting_on_ref": waiting_on_ref,
            "reason_kind": reason_kind,
        }

    return verify


def build_completion_evidence_verifier(
    activity_reader: Any,
    progress_reader: Any,
    *,
    allowed_evidence_prefixes: Sequence[str] = tuple(ALLOWED_COMPLETION_EVIDENCE_PREFIXES),
):
    allowed_evidence_prefixes = frozenset(allowed_evidence_prefixes)

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        activity_id = transition.get("activity_id")
        expected_revision = transition.get("expected_revision")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ConsequenceRejected("CONSEQUENCE_MISSING_ACTIVITY_ID")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        status = act.get("status")
        if status in TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(f"CONSEQUENCE_ACTIVITY_ALREADY_TERMINAL:{status}")
        if status not in NON_TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(f"CONSEQUENCE_ACTIVITY_STATUS_FORBIDS_COMPLETE:{status}")
        actual_revision = int(act.get("revision", -1))
        # `expected_revision` is LR-2's canonical-STORE revision -- the same token the
        # journal replays as `result_revision - 1` -- and NOT the per-Activity snapshot
        # revision. Comparing it against `act["revision"]` only happened to work while
        # the target Activity was the most recently transitioned one, and it rejected
        # every legitimate consequence on a store holding more than one Activity.
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        reason_code = transition.get("reason_code")
        if reason_code in NON_CANONICAL_COMPLETION_REASON_CODES:
            raise ConsequenceRejected(f"CONSEQUENCE_COMPLETION_REASON_NOT_CANONICAL:{reason_code}")
        evidence_ref = _evidence_field(transition, "completion_evidence_ref")
        prefix = _prefix_of(evidence_ref)
        if prefix is None or prefix not in allowed_evidence_prefixes:
            raise ConsequenceRejected("CONSEQUENCE_COMPLETION_EVIDENCE_REF_INVALID")
        eligibilities = list(progress_reader.list_completion_eligibilities())
        if not eligibilities:
            raise ConsequenceRejected("CONSEQUENCE_COMPLETION_ELIGIBILITY_MISSING")
        own = [e for e in eligibilities if e.get("activity_id") == activity_id]
        if not own:
            raise ConsequenceRejected("CONSEQUENCE_COMPLETION_ELIGIBILITY_MISSING")
        for other in eligibilities:
            if other.get("activity_id") == activity_id:
                continue
            if evidence_ref in list(other.get("authoritative_result_refs") or []):
                raise ConsequenceRejected(
                    "CONSEQUENCE_COMPLETION_EVIDENCE_BELONGS_TO_OTHER_ACTIVITY"
                )
        match = [
            e
            for e in own
            if evidence_ref in list(e.get("authoritative_result_refs") or [])
            or e.get("eligibility_id") == evidence_ref
        ]
        if not match:
            raise ConsequenceRejected("CONSEQUENCE_COMPLETION_EVIDENCE_NOT_CANONICAL")
        chosen = match[0]
        bound_revision = chosen.get("activity_revision")
        if bound_revision is not None and int(bound_revision) != actual_revision:
            raise ConsequenceRejected("CONSEQUENCE_COMPLETION_ELIGIBILITY_REVISION_MISMATCH")
        return {
            "activity_id": activity_id,
            "activity_revision": actual_revision,
            "activity_status": status,
            "completion_evidence_ref": evidence_ref,
            "completion_eligibility_id": chosen.get("eligibility_id"),
            "completion_eligibility_status": chosen.get("status"),
        }

    return verify


def build_interruption_evidence_verifier(
    activity_reader: Any,
    interruption_state_reader: Callable[[], Mapping[str, Any]],
    *,
    allowed_reason_codes: Sequence[str] = tuple(INTERRUPTION_REASON_CODES),
):
    allowed_reason_codes = frozenset(allowed_reason_codes)

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        activity_id = transition.get("activity_id")
        expected_revision = transition.get("expected_revision")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ConsequenceRejected("CONSEQUENCE_MISSING_ACTIVITY_ID")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        status = act.get("status")
        if status not in NON_TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(
                f"CONSEQUENCE_ACTIVITY_STATUS_FORBIDS_INTERRUPTION:{status}"
            )
        actual_revision = int(act.get("revision", -1))
        # `expected_revision` is LR-2's canonical-STORE revision -- the same token the
        # journal replays as `result_revision - 1` -- and NOT the per-Activity snapshot
        # revision. Comparing it against `act["revision"]` only happened to work while
        # the target Activity was the most recently transitioned one, and it rejected
        # every legitimate consequence on a store holding more than one Activity.
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        reason_code = transition.get("reason_code")
        if reason_code not in allowed_reason_codes:
            raise ConsequenceRejected(f"CONSEQUENCE_WRONG_REASON_CODE:{reason_code}")
        state = interruption_state_reader()
        known: set[str] = set()
        for key in ("assessments", "pending_interruptions", "processed_events"):
            known.update(str(k) for k in (state.get(key) or {}))
        claimed = set(str(r) for r in (transition.get("source_refs") or []))
        claimed.update(str(r) for r in (transition.get("causal_parent_refs") or []))
        if not (known & claimed):
            raise ConsequenceRejected("CONSEQUENCE_INTERRUPTION_EVIDENCE_NOT_CANONICAL")
        return {
            "activity_id": activity_id,
            "activity_revision": actual_revision,
            "activity_status": status,
            "reason_code": reason_code,
            "interruption_refs": sorted(known & claimed),
        }

    return verify


def build_blocked_evidence_verifier(activity_reader: Any):
    """ACTIVITY_CONSEQUENCE evidence for BLOCK: a named basis an owner can re-read.

    BLOCKED is not a model verb and not a "give up": it is the owner recording that the
    Activity cannot advance under the current conditions.  The verifier refuses a transition
    with no structured basis, no source refs, a stale store revision, or a terminal/unknown
    Activity.  It answers no question about *why* the basis holds -- that is the owner read
    service's job (``activity_outcome_assessment``), and no new authority is created here.
    """

    from activity_outcome_assessment import (
        BlockedBasisError,
        validate_blocked_payload_against_activity,
    )

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        activity_id = transition.get("activity_id")
        expected_revision = transition.get("expected_revision")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ConsequenceRejected("CONSEQUENCE_MISSING_ACTIVITY_ID")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        status = act.get("status")
        if status not in NON_TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(f"CONSEQUENCE_ACTIVITY_STATUS_FORBIDS_BLOCK:{status}")
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        if not list(transition.get("source_refs") or []):
            raise ConsequenceRejected("CONSEQUENCE_BLOCK_REQUIRES_SOURCE_REFS")
        try:
            summary = validate_blocked_payload_against_activity(
                act, transition.get("blocked_assessment")
            )
        except BlockedBasisError as exc:
            raise ConsequenceRejected(f"CONSEQUENCE_BLOCK_BASIS_REJECTED:{exc}") from exc
        return {
            "activity_id": activity_id,
            "activity_revision": int(act.get("revision", -1)),
            "activity_status": status,
            **summary,
        }

    return verify


def build_failure_evidence_verifier(activity_reader: Any, evidence_resolver: Callable[[str], Mapping[str, Any]]):
    """S09 ACTIVITY_CONSEQUENCE evidence for FAIL: owner-assessed, evidence re-read.

    The transition carries the assessment the S09 owner gate produced.  This verifier does not
    trust a bare ``verified: true`` or a model/HTTP field: it re-checks the exact Activity and
    its revision, the stated scope of the goal, the named criteria, and asks the composition
    root's ``evidence_resolver`` (existing owner reads) for every evidence ref.  Any refusal is
    a typed rejection -- which is not a FAILED outcome.
    """

    from activity_outcome_assessment import (
        FailureAssessmentError,
        validate_failure_payload_against_activity,
    )

    if not callable(evidence_resolver):
        raise TypeError("evidence_resolver must be callable")

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        activity_id = transition.get("activity_id")
        expected_revision = transition.get("expected_revision")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ConsequenceRejected("CONSEQUENCE_MISSING_ACTIVITY_ID")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        status = act.get("status")
        if status in TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(f"CONSEQUENCE_ACTIVITY_ALREADY_TERMINAL:{status}")
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        try:
            summary = validate_failure_payload_against_activity(
                act,
                transition.get("failure_assessment"),
                evidence_resolver=evidence_resolver,
            )
        except FailureAssessmentError as exc:
            raise ConsequenceRejected(f"CONSEQUENCE_FAILURE_ASSESSMENT_REJECTED:{exc}") from exc
        return {
            "activity_id": activity_id,
            "activity_revision": int(act.get("revision", -1)),
            "activity_status": status,
            **summary,
        }

    return verify


def build_activity_lineage_verifier(activity_reader: Any):
    """Minimal lineage check for LR-5 CHECKPOINT/PROGRESS consequences."""

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        activity_id = transition.get("activity_id")
        expected_revision = transition.get("expected_revision")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        status = act.get("status")
        if status in TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(f"CONSEQUENCE_ACTIVITY_ALREADY_TERMINAL:{status}")
        actual_revision = int(act.get("revision", -1))
        # `expected_revision` is LR-2's canonical-STORE revision -- the same token the
        # journal replays as `result_revision - 1` -- and NOT the per-Activity snapshot
        # revision. Comparing it against `act["revision"]` only happened to work while
        # the target Activity was the most recently transitioned one, and it rejected
        # every legitimate consequence on a store holding more than one Activity.
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        return {
            "activity_id": activity_id,
            "activity_revision": actual_revision,
            "activity_status": status,
        }

    return verify


def build_external_stop_evidence_verifier(activity_reader: Any, *, subject_id: Optional[str] = None):
    """ACTIVITY_CONSEQUENCE check for one durable local-operator stop intent.

    The request body never grants this: the composition root builds the payload after the
    operator credential was verified, and this verifier re-reads the live Activity so a stale
    revision, a different Activity or a terminal one is refused inside the store lock.
    """

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        from activity_continuity import validate_external_stop_payload

        activity_id = transition.get("activity_id")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        if act.get("status") in TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(
                "CONSEQUENCE_ACTIVITY_ALREADY_TERMINAL:%s" % act.get("status"))
        expected_revision = transition.get("expected_revision")
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        payload = transition.get("external_stop")
        if not isinstance(payload, Mapping):
            raise ConsequenceRejected("EXTERNAL_STOP_PAYLOAD_REQUIRED")
        validated = validate_external_stop_payload(
            payload, activity=act, subject_id=subject_id)
        return {
            "verified": True,
            "owner": "local_operator_stop",
            "activity_id": activity_id,
            "activity_revision": int(act.get("revision", -1)),
            "intent_id": validated["intent_id"],
            "reason_code": validated["reason_code"],
        }

    return verify


def build_external_stop_cancel_verifier(activity_reader: Any):
    """CANCEL is admitted only while the exact accepted stop intent is still pending."""

    def verify(
        transition: Mapping[str, Any], canonical_state: Any = None
    ) -> Mapping[str, Any]:
        activity_id = transition.get("activity_id")
        act = _canonical_activity(canonical_state, activity_reader, activity_id)
        if act is None:
            raise ConsequenceRejected("CONSEQUENCE_UNKNOWN_ACTIVITY_LINEAGE")
        if act.get("status") in TERMINAL_ACTIVITY_STATUSES:
            raise ConsequenceRejected(
                "CONSEQUENCE_ACTIVITY_ALREADY_TERMINAL:%s" % act.get("status"))
        expected_revision = transition.get("expected_revision")
        if int(expected_revision if expected_revision is not None else -2) != (
            _canonical_state_revision(canonical_state, activity_reader)
        ):
            raise ConsequenceRejected("CONSEQUENCE_STALE_ACTIVITY_REVISION")
        stop = act.get("external_stop") or {}
        if not isinstance(stop, Mapping) or stop.get("state") != "PENDING":
            raise ConsequenceRejected("EXTERNAL_STOP_INTENT_NOT_PENDING")
        intent_id = str(transition.get("proposal_ref") or "")
        if intent_id != str(stop.get("intent_id") or ""):
            raise ConsequenceRejected("EXTERNAL_STOP_INTENT_MISMATCH")
        return {
            "verified": True,
            "owner": "local_operator_stop",
            "activity_id": activity_id,
            "activity_revision": int(act.get("revision", -1)),
            "intent_id": intent_id,
        }

    return verify


def _canonical_activity(
    canonical_state: Any, activity_reader: Any, activity_id: Any
) -> Optional[Mapping[str, Any]]:
    """Read the Activity from the canonical state LR-2 already holds.

    LR-2 mints permits from inside its exclusive store IO lock, so an evidence
    verifier must never call a read port that takes that same lock again. LR-2 is
    the canonical state authority, so the state it already loaded is the
    authoritative snapshot; the read port is only a documented fallback for
    callers that are not inside the lock.
    """
    if canonical_state is not None:
        if not isinstance(activity_id, str) or not activity_id:
            return None
        activities = canonical_state.get("activities")
        if not isinstance(activities, Mapping):
            return None
        act = activities.get(activity_id)
        return act if isinstance(act, Mapping) else None
    if activity_reader is None:
        raise ConsequenceRejected("CONSEQUENCE_NO_CANONICAL_ACTIVITY_SOURCE")
    return activity_reader.get_activity(activity_id) if activity_id else None


def _canonical_state_revision(canonical_state: Any, activity_reader: Any) -> int:
    """The revision LR-2's ``expected_revision`` actually means.

    LR-2's optimistic-concurrency token is the canonical STORE revision (the value the
    journal replays as ``result_revision - 1``), not the per-Activity snapshot revision,
    so a consequence proposal has to be checked against the store revision it was minted
    from. Like ``_canonical_activity`` this prefers the canonical state LR-2 already
    loaded -- LR-2 mints permits from inside its own store IO lock, where re-entering a
    read port would deadlock -- and only falls back to the declared read port for callers
    outside that lock.
    """
    if canonical_state is not None:
        if isinstance(canonical_state, Mapping):
            return int(canonical_state.get("revision", -1))
        raise ConsequenceRejected("CONSEQUENCE_NO_CANONICAL_ACTIVITY_SOURCE")
    read_state = getattr(activity_reader, "read_canonical_state", None)
    if callable(read_state):
        state, _ = read_state()
        if isinstance(state, Mapping):
            return int(state.get("revision", -1))
    raise ConsequenceRejected("CONSEQUENCE_NO_CANONICAL_ACTIVITY_SOURCE")


def _prefix_of(value: Any) -> Optional[str]:
    if not isinstance(value, str) or ":" not in value:
        return None
    return value.split(":", 1)[0]


def _evidence_field(transition: Mapping[str, Any], key: str) -> Any:
    """Read a proposal's evidence field from the transition or its canonical snapshot.

    LR-2 records evidence references (for example ``completion_evidence_ref`` or
    ``waiting_on_ref``) on the post-commit Activity snapshot rather than as
    top-level transition keys, so the verifier accepts exactly those two sources
    and never a caller-supplied side channel.
    """
    value = transition.get(key)
    if value is not None:
        return value
    snapshot = transition.get("activity_snapshot")
    if isinstance(snapshot, Mapping):
        return snapshot.get(key)
    return None


@dataclass(frozen=True)
class ActivityCommitReceipt:
    """Immutable LR-2 commit receipt. LR-2 never writes Adoption state itself."""

    receipt_id: str
    command_id: str
    activity_id: str
    transition_id: str
    transition_type: str
    previous_revision: int
    new_revision: int
    authority_class: str
    evidence_digest: str
    runtime_epoch: str
    decision_ref: Optional[str] = None
    decision_revision: Optional[int] = None
    adoption_ref: Optional[str] = None
    adoption_revision: Optional[int] = None
    committed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "chiyo.life.activity-commit-receipt.v1",
            "receipt_id": self.receipt_id,
            "command_id": self.command_id,
            "activity_id": self.activity_id,
            "transition_id": self.transition_id,
            "transition_type": self.transition_type,
            "previous_revision": self.previous_revision,
            "new_revision": self.new_revision,
            "authority_class": self.authority_class,
            "evidence_digest": self.evidence_digest,
            "runtime_epoch": self.runtime_epoch,
            "decision_ref": self.decision_ref,
            "decision_revision": self.decision_revision,
            "adoption_ref": self.adoption_ref,
            "adoption_revision": self.adoption_revision,
            "committed_at": self.committed_at,
        }

    @staticmethod
    def build_receipt_id(
        *, command_id: str, activity_id: str, transition_id: str
    ) -> str:
        digest = hashlib.sha256(
            f"{command_id}|{activity_id}|{transition_id}".encode()
        ).hexdigest()[:16]
        return f"actrcpt:{digest}"


def build_admission_issuer(
    decision_reader: Any, adoption_reader: Any, runtime_epoch: str
) -> AdmissionIssuer:
    """Composition-root factory. Each isolated runtime gets its own owner-bound issuer."""
    return AdmissionIssuer(decision_reader, adoption_reader, runtime_epoch)
