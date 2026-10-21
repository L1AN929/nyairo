#!/usr/bin/env python3
"""Chiyo Agency | AG-1 Agency Decision (`agency_decision_ag1.py`).

Sole Responsibility of AG-1:
    "面对当前合法 CandidateSet，在当前约束、能力和已知现实下，千代选择什么？"

Allowed Flow in AG-1:
    看见候选 (CandidateSet)
    ↓
    检查现实约束 (HardConstraintEvaluator)
    ↓
    检查 Capacity (CapacityEvaluator)
    ↓
    构造 Accessibility 视图 (AgencyAccessibilityView)
    ↓
    作出选择 (DecisionPolicyRouter -> Rule / AgencyCognitionAdapter -> DecisionOutputValidator)
    ↓
    保存事前不可变记录 (DecisionRecord + AgencyDecisionJournal via WAL)

Top-Level Invariants Permanently Enforced:
    - Candidate != Decision
    - Decision != Activity Mutation
    - Decision != Action Proposal
    - Decision != Action Execution
    - Decision != Reality
    - NO_ACTION is a first-class autonomous outcome (even when CandidateSet is non-empty)
    - DEFER != NO_ACTION (and DEFER never auto-creates Agenda, timer, cron, or ResumeCondition)
    - Model failure (timeout, invalid JSON, refusal, schema error) != NO_ACTION and != DEFER
    - 0 Activity mutations, 0 Action creations, 0 World writes, 0 Memory writes,
      0 Goal writes, 0 Commitment writes, 0 Outbound messages.
"""

from __future__ import annotations

import copy
import dataclasses
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Protocol, Sequence

from activity_continuity import (
    COMMIT_INTENT_FILENAME,
    GENESIS_HASH,
    GLOBAL_SUBJECT_ID,
    INTERRUPTIBILITY_ATOMIC,
    INTERRUPTIBILITY_FREE,
    INTERRUPT_MODE_ATOMIC,
    INTERRUPT_MODE_IMMEDIATE,
    INTERRUPT_MODE_SAFE_BOUNDARY,
    NAMESPACE_ISOLATED_TEST,
    OPEN_ACTIVITY_STATUSES,
    STATUS_ABANDONED,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_EXPIRED,
    STATUS_PAUSED,
    STATUS_WAITING,
    TERMINAL_ACTIVITY_STATUSES,
    ActivityAuthorityLease,
    ActivityLockError,
    ActivityReadService,
    CanonicalActivityStore,
    ContractViolationError,
    RecoveryRequiredError,
    RevisionConflictError,
    SubjectIdentityError,
    WriterCapabilityError,
    _iso,
    canonical_json_line,
    now,
    sha256_hex,
    validate_subject_id,
)

INTERRUPTIBILITY_IMMEDIATE = INTERRUPT_MODE_IMMEDIATE
INTERRUPTIBILITY_SAFE_BOUNDARY = INTERRUPT_MODE_SAFE_BOUNDARY
StaleSnapshotError = RevisionConflictError


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass

from activity_waiting_agenda import (
    ELIG_STATUS_CONSUMED,
    ELIG_STATUS_OPEN,
    ResumeEligibilityStore,
)
from candidate_sources_ag0 import (
    CANDIDATE_KIND_CONSIDER_EXPLICIT_GOAL,
    CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
    CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
    CANDIDATE_KIND_CONTINUE_CURRENT,
    CANDIDATE_KIND_FULFILL_COMMITMENT,
    CANDIDATE_KIND_PURSUE_GOAL_STEP,
    CANDIDATE_KIND_RESPOND_USER_REQUEST,
    CANDIDATE_KIND_RESUME_ACTIVITY,
    CANDIDATE_STATUS_OPEN,
    NAMESPACE_PRODUCTION,
    NAMESPACE_SHADOW_VERIFY,
    SOURCE_COMMITMENT,
    SOURCE_CURRENT_ACTIVITY,
    SOURCE_EXPLICIT_GOAL,
    SOURCE_RESUME_ELIGIBLE,
    SOURCE_USER_REQUEST,
    SOURCE_WORLD_OPPORTUNITY,
    VALID_CANDIDATE_KINDS,
    VALID_CANDIDATE_SOURCE_KINDS,
    VALID_NAMESPACES,
    CandidateReadService,
    CandidateRecord,
    CandidateSet,
)
# v0.4 stage A: the future-items domain is the single definition of what a "later item" is; the
# decision validator borrows it rather than keeping a second, drifting copy of the shape.
import life_future_items


# ---------------------------------------------------------------------------
# Schema & Policy Version Constants
# ---------------------------------------------------------------------------

SCHEMA_DECISION_OPPORTUNITY = "chiyo.agency.decision_opportunity.v1"
SCHEMA_DECISION_CONTEXT_SNAPSHOT = "chiyo.agency.decision_context_snapshot.v1"
SCHEMA_CANDIDATE_CONSTRAINT_RESULT = "chiyo.agency.candidate_constraint_result.v1"
SCHEMA_CAPACITY_SNAPSHOT = "chiyo.agency.capacity_snapshot.v1"
SCHEMA_ACCESSIBILITY_VIEW = "chiyo.agency.accessibility_view.v1"
SCHEMA_DECISION_ATTEMPT = "chiyo.agency.decision_attempt.v1"
SCHEMA_DECISION_RECORD = "chiyo.agency.decision_record.v1"
SCHEMA_DECISION_STATE = "chiyo.agency.decision_state.v1"
SCHEMA_DECISION_JOURNAL_ENTRY = "chiyo.agency.decision_journal_entry.v1"
SCHEMA_DECISION_WAL = "chiyo.agency.decision_commit_intent.v1"

POLICY_VERSION_AG1_V1 = "ag1.decision_policy.v1"
REASON_REGISTRY_VERSION_V1 = "ag1.reason_codes.v1"

WRITER_DOMAIN_AGENCY_DECISION = "agency_decision_authority"

# Production Kill Switch (Section 105)
AGENCY_ENABLED_ENV = "AGENCY_ENABLED"
CHIYO_AGENCY_ENABLED_ENV = "CHIYO_AGENCY_ENABLED"
DEFAULT_AGENCY_ENABLED = False

# Bounded Cognitive Candidate Input Limit (Section 139)
MAX_COGNITIVE_CANDIDATES = 16
MAX_SHORT_RATIONALE_CHARS = 280
DEFAULT_COGNITION_TIMEOUT_MS = 5000
DEFAULT_COGNITION_MAX_TOKENS = 512


def is_agency_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Section 105: Production kill switch remains false by default."""
    env = os.environ if environ is None else environ
    raw = str(env.get(AGENCY_ENABLED_ENV) or env.get(CHIYO_AGENCY_ENABLED_ENV) or "false").strip().lower()
    return raw in {"1", "true", "on", "yes"}


# ---------------------------------------------------------------------------
# Section 3: Legal vs Forbidden Decision Outputs
# ---------------------------------------------------------------------------

DECISION_START = "START"
DECISION_CONTINUE = "CONTINUE"
DECISION_PAUSE = "PAUSE"
DECISION_WAIT = "WAIT"
DECISION_RESUME = "RESUME"
DECISION_ABANDON = "ABANDON"
DECISION_DEFER = "DEFER"
DECISION_NO_ACTION = "NO_ACTION"

VALID_DECISION_OUTPUTS = frozenset(
    {
        DECISION_START,
        DECISION_CONTINUE,
        DECISION_PAUSE,
        DECISION_WAIT,
        DECISION_RESUME,
        DECISION_ABANDON,
        DECISION_DEFER,
        DECISION_NO_ACTION,
    }
)

FORBIDDEN_DECISION_OUTPUTS = frozenset(
    {
        "CONTACT",
        "MESSAGE",
        "EXPLORE_WEB",
        "MAKE_PLAN",
        "CHANGE_GOAL",
        "CREATE_COMMITMENT",
        "COMPLETE",
        "SEND_MESSAGE",
        "SLEEP",
        "REST",
        "DO_NOTHING",
    }
)

# ---------------------------------------------------------------------------
# Section 16 & 113: DecisionOpportunity Triggers & Statuses
# ---------------------------------------------------------------------------

TRIGGER_CANDIDATE_SET_CHANGED = "CANDIDATE_SET_CHANGED"
TRIGGER_USER_REQUEST_EVENT = "USER_REQUEST_EVENT"
TRIGGER_RESUME_ELIGIBILITY_OPENED = "RESUME_ELIGIBILITY_OPENED"
TRIGGER_CURRENT_ACTIVITY_ENDED = "CURRENT_ACTIVITY_ENDED"
TRIGGER_CURRENT_ACTIVITY_RECONSIDERATION = "CURRENT_ACTIVITY_RECONSIDERATION"
TRIGGER_WORLD_OPPORTUNITY_EVENT = "WORLD_OPPORTUNITY_EVENT"
TRIGGER_EXPLICIT_REEVALUATION = "EXPLICIT_REEVALUATION"
TRIGGER_RECOVERY_RECONCILIATION = "RECOVERY_RECONCILIATION"

VALID_OPPORTUNITY_TRIGGERS = frozenset(
    {
        TRIGGER_CANDIDATE_SET_CHANGED,
        TRIGGER_USER_REQUEST_EVENT,
        TRIGGER_RESUME_ELIGIBILITY_OPENED,
        TRIGGER_CURRENT_ACTIVITY_ENDED,
        TRIGGER_CURRENT_ACTIVITY_RECONSIDERATION,
        TRIGGER_WORLD_OPPORTUNITY_EVENT,
        TRIGGER_EXPLICIT_REEVALUATION,
        TRIGGER_RECOVERY_RECONCILIATION,
    }
)

FORBIDDEN_OPPORTUNITY_TRIGGERS = frozenset(
    {
        "PERIODIC_LLM_TICK",
        "CRON_TICK",
        "IDLE_TIMER_POLL",
        "EVERY_10_MINUTES_ASK_LLM",
        "HEARTBEAT_LLM_POLL",
    }
)

OPPORTUNITY_STATUS_OPEN = "OPEN"
OPPORTUNITY_STATUS_DECIDED = "DECIDED"
OPPORTUNITY_STATUS_FAILED = "FAILED"
OPPORTUNITY_STATUS_STALE = "STALE"
OPPORTUNITY_STATUS_SUPERSEDED = "SUPERSEDED"

VALID_OPPORTUNITY_STATUSES = frozenset(
    {
        OPPORTUNITY_STATUS_OPEN,
        OPPORTUNITY_STATUS_DECIDED,
        OPPORTUNITY_STATUS_FAILED,
        OPPORTUNITY_STATUS_STALE,
        OPPORTUNITY_STATUS_SUPERSEDED,
    }
)

# ---------------------------------------------------------------------------
# Section 7: DecisionAttempt Statuses & Failure Classes
# ---------------------------------------------------------------------------

ATTEMPT_STATUS_OPEN = "OPEN"
ATTEMPT_STATUS_SUCCEEDED = "SUCCEEDED"
ATTEMPT_STATUS_FAILED = "FAILED"
ATTEMPT_STATUS_STALE = "STALE"
ATTEMPT_STATUS_CANCELLED = "CANCELLED"

VALID_ATTEMPT_STATUSES = frozenset(
    {
        ATTEMPT_STATUS_OPEN,
        ATTEMPT_STATUS_SUCCEEDED,
        ATTEMPT_STATUS_FAILED,
        ATTEMPT_STATUS_STALE,
        ATTEMPT_STATUS_CANCELLED,
    }
)

FAILURE_CLASS_MODEL_TIMEOUT = "DECISION_ATTEMPT_FAILED:MODEL_TIMEOUT"
FAILURE_CLASS_MODEL_REFUSAL = "NO_VALID_DECISION:MODEL_REFUSAL"
FAILURE_CLASS_INVALID_JSON = "DECISION_ATTEMPT_FAILED:INVALID_JSON"
FAILURE_CLASS_INVALID_SCHEMA = "DECISION_ATTEMPT_FAILED:INVALID_SCHEMA"
FAILURE_CLASS_INVENTED_CANDIDATE = "INVALID_MODEL_OUTPUT:INVENTED_CANDIDATE"
FAILURE_CLASS_HARD_CONSTRAINT_VIOLATION = "INVALID_MODEL_OUTPUT:HARD_CONSTRAINT_OVERRIDE_FORBIDDEN"
FAILURE_CLASS_ILLEGAL_DECISION_ENUM = "INVALID_MODEL_OUTPUT:ILLEGAL_DECISION_ENUM"
FAILURE_CLASS_ILLEGAL_DECISION_MATRIX = "INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE"
FAILURE_CLASS_ATOMIC_VIOLATION = "INVALID_MODEL_OUTPUT:ATOMIC_BOUNDARY_VIOLATION"
FAILURE_CLASS_UNKNOWN_REASON_CODE = "INVALID_MODEL_OUTPUT:UNKNOWN_REASON_CODE"
FAILURE_CLASS_STALE_BEFORE_COMMIT = "STALE_BEFORE_COMMIT"
FAILURE_CLASS_INTERRUPTED_DURING_COGNITION = "DECISION_ATTEMPT_FAILED:INTERRUPTED_DURING_COGNITION"
FAILURE_CLASS_CANDIDATE_EXPLOSION = "DECISION_ATTEMPT_FAILED:CANDIDATE_EXPLOSION"
#: v0.4 C: the model named a method handle this round does not offer (or named one at all with no
#: practice workspace wired).  Same class of refusal as an invented candidate: an identifier the
#: model was not given is not its to produce.
FAILURE_CLASS_INVENTED_METHOD_REF = "INVALID_MODEL_OUTPUT:INVENTED_METHOD_REF"
FAILURE_CLASS_INVALID_METHOD_TRIAL = "INVALID_MODEL_OUTPUT:INVALID_METHOD_TRIAL"
#: v0.4 schema-repair: two refusals that already existed but carried **no** failure class, so
#: ``decide`` labelled them with its ``INVALID_SCHEMA`` fallback (line 4423).  A hidden chain of
#: thought or a universal score in the answer is neither a schema shape error nor a bad JSON, and
#: recording it as one is exactly the kind of mislabel this module is being repaired for.
FAILURE_CLASS_FORBIDDEN_HIDDEN_REASONING = "INVALID_MODEL_OUTPUT:FORBIDDEN_HIDDEN_REASONING"
FAILURE_CLASS_FORBIDDEN_UNIVERSAL_UTILITY = "INVALID_MODEL_OUTPUT:FORBIDDEN_UNIVERSAL_UTILITY"

#: v0.4 C: the bounded handle a Decision may name for one of her own method drafts.  It is not an
#: owner id and it authorises nothing: it only says "this is the practice I am following now".
METHOD_REF_PREFIX = "method:"
#: The one narrow evaluation the model may attach: its own verdict on the method it followed.
METHOD_TRIAL_OUTCOMES = frozenset({"HELPED", "NOT_HELPED", "UNKNOWN"})
METHOD_TRIAL_KEYS = frozenset({"method_ref", "outcome", "detail", "experience_ids"})
MAX_METHOD_TRIAL_DETAIL_CHARS = 280
#: v0.4 C: the *one* bounded practice proposal a Decision may carry ("一件我想试的有范围做法").
#: It is saved by B's practice workspace as a TENTATIVE draft -- never as a verified Skill, never
#: as a preference, belief or persona trait, and never as an executable program.
METHOD_PROPOSAL_KEYS = frozenset({"title", "instruction", "scope", "uncertainty",
                                  "experience_ids", "review_refs"})
MAX_METHOD_TITLE_CHARS = 120
MAX_METHOD_INSTRUCTION_CHARS = 800
MAX_METHOD_SCOPE_CHARS = 240
MAX_METHOD_UNCERTAINTY_CHARS = 200

# ---------------------------------------------------------------------------
# Section 24: Hard Constraint Statuses & Codes
# ---------------------------------------------------------------------------

CONSTRAINT_STATUS_ALLOWED = "ALLOWED"
CONSTRAINT_STATUS_REJECTED = "REJECTED"

HARD_REJECT_PERMISSION_DENIED = "HARD_REJECT:PERMISSION_DENIED"
HARD_REJECT_PHYSICALLY_IMPOSSIBLE = "HARD_REJECT:PHYSICALLY_IMPOSSIBLE"
HARD_REJECT_STALE_SOURCE = "HARD_REJECT:STALE_SOURCE"
HARD_REJECT_TARGET_INVALID = "HARD_REJECT:TARGET_INVALID"
HARD_REJECT_TIME_WINDOW_CLOSED = "HARD_REJECT:TIME_WINDOW_CLOSED"
HARD_REJECT_POLICY_FORBIDDEN = "HARD_REJECT:POLICY_FORBIDDEN"
HARD_REJECT_COMMITMENT_CONFLICT = "HARD_REJECT:HARD_COMMITMENT_CONFLICT"
HARD_REJECT_IRREVERSIBLE_CONFLICT = "HARD_REJECT:IRREVERSIBLE_CONFLICT"

VALID_HARD_REJECT_CODES = frozenset(
    {
        HARD_REJECT_PERMISSION_DENIED,
        HARD_REJECT_PHYSICALLY_IMPOSSIBLE,
        HARD_REJECT_STALE_SOURCE,
        HARD_REJECT_TARGET_INVALID,
        HARD_REJECT_TIME_WINDOW_CLOSED,
        HARD_REJECT_POLICY_FORBIDDEN,
        HARD_REJECT_COMMITMENT_CONFLICT,
        HARD_REJECT_IRREVERSIBLE_CONFLICT,
    }
)

# ---------------------------------------------------------------------------
# Section 33: Capacity States & Executability
# ---------------------------------------------------------------------------

CAPACITY_AVAILABLE = "AVAILABLE"
CAPACITY_LIMITED = "LIMITED"
CAPACITY_UNAVAILABLE = "UNAVAILABLE"
CAPACITY_UNKNOWN = "UNKNOWN"

VALID_CAPACITY_STATES = frozenset(
    {
        CAPACITY_AVAILABLE,
        CAPACITY_LIMITED,
        CAPACITY_UNAVAILABLE,
        CAPACITY_UNKNOWN,
    }
)

EXEC_NOW = "EXECUTABLE_NOW"
EXEC_WITH_LIMITS = "EXECUTABLE_WITH_LIMITS"
EXEC_NOT_NOW = "NOT_EXECUTABLE_NOW"
EXEC_UNKNOWN = "UNKNOWN_CAPACITY"

VALID_EXECUTABILITY_STATES = frozenset(
    {
        EXEC_NOW,
        EXEC_WITH_LIMITS,
        EXEC_NOT_NOW,
        EXEC_UNKNOWN,
    }
)

# ---------------------------------------------------------------------------
# Section 47: Decision Routes
# ---------------------------------------------------------------------------

ROUTE_RULE_RESOLVED = "RULE_RESOLVED"
ROUTE_COGNITIVE_REQUIRED = "COGNITIVE_REQUIRED"
ROUTE_NO_DECISION = "NO_DECISION"

VALID_DECISION_ROUTES = frozenset(
    {
        ROUTE_RULE_RESOLVED,
        ROUTE_COGNITIVE_REQUIRED,
        ROUTE_NO_DECISION,
    }
)

# ---------------------------------------------------------------------------
# Section 73: DecisionReasonCode Registry
# ---------------------------------------------------------------------------

REASON_NO_LEGAL_CANDIDATE = "NO_LEGAL_CANDIDATE"
REASON_CAPACITY_UNAVAILABLE = "CAPACITY_UNAVAILABLE"
REASON_CAPACITY_LIMITED = "CAPACITY_LIMITED"
REASON_CAPACITY_UNKNOWN_DEFERRED = "CAPACITY_UNKNOWN_DEFERRED"
REASON_ATOMIC_IN_PROGRESS = "ATOMIC_IN_PROGRESS"
REASON_SAFE_BOUNDARY_PAUSE_RECORDED = "SAFE_BOUNDARY_PAUSE_RECORDED"
REASON_CONTINUITY_PREFERRED = "CONTINUITY_PREFERRED"
REASON_CURRENT_ACTIVITY_CONTINUITY = "CURRENT_ACTIVITY_CONTINUITY"
REASON_TIME_WINDOW_CLOSING = "TIME_WINDOW_CLOSING"
REASON_REQUEST_CURRENTLY_FEASIBLE = "REQUEST_CURRENTLY_FEASIBLE"
REASON_USER_REQUEST_VALID = "USER_REQUEST_VALID"
REASON_USER_REQUEST_DEFERRED = "USER_REQUEST_DEFERRED"
REASON_SWITCH_COST_HIGH = "SWITCH_COST_HIGH"
REASON_SWITCH_COST_ACCEPTABLE = "SWITCH_COST_ACCEPTABLE"
REASON_RESOURCE_LIMIT = "RESOURCE_LIMIT"
REASON_NO_ACTION_CHOSEN = "NO_ACTION_CHOSEN"
REASON_RESUME_ELIGIBLE_READY = "RESUME_ELIGIBLE_READY"
REASON_COMMITMENT_FEASIBLE = "COMMITMENT_FEASIBLE"
REASON_COMMITMENT_DEFERRED_BY_CAPACITY = "COMMITMENT_DEFERRED_BY_CAPACITY"
REASON_GOAL_STEP_ACTIONABLE = "GOAL_STEP_ACTIONABLE"
REASON_GOAL_ATTENTION_DEFERRED = "GOAL_ATTENTION_DEFERRED"
REASON_WORLD_OPPORTUNITY_OBSERVED = "WORLD_OPPORTUNITY_OBSERVED"
REASON_KNOWN_BLOCKING_WAIT = "KNOWN_BLOCKING_WAIT"
REASON_ABANDON_NONTERMINAL_CHOSEN = "ABANDON_NONTERMINAL_CHOSEN"
REASON_OPEN_ENDED_CONTINUATION = "OPEN_ENDED_CONTINUATION"
REASON_PURE_CONSUMPTION_CHOSEN = "PURE_CONSUMPTION_CHOSEN"
REASON_TIMING_NOT_APPROPRIATE = "TIMING_NOT_APPROPRIATE"

VALID_DECISION_REASON_CODES = frozenset(
    {
        REASON_NO_LEGAL_CANDIDATE,
        REASON_CAPACITY_UNAVAILABLE,
        REASON_CAPACITY_LIMITED,
        REASON_CAPACITY_UNKNOWN_DEFERRED,
        REASON_ATOMIC_IN_PROGRESS,
        REASON_SAFE_BOUNDARY_PAUSE_RECORDED,
        REASON_CONTINUITY_PREFERRED,
        REASON_CURRENT_ACTIVITY_CONTINUITY,
        REASON_TIME_WINDOW_CLOSING,
        REASON_REQUEST_CURRENTLY_FEASIBLE,
        REASON_USER_REQUEST_VALID,
        REASON_USER_REQUEST_DEFERRED,
        REASON_SWITCH_COST_HIGH,
        REASON_SWITCH_COST_ACCEPTABLE,
        REASON_RESOURCE_LIMIT,
        REASON_NO_ACTION_CHOSEN,
        REASON_RESUME_ELIGIBLE_READY,
        REASON_COMMITMENT_FEASIBLE,
        REASON_COMMITMENT_DEFERRED_BY_CAPACITY,
        REASON_GOAL_STEP_ACTIONABLE,
        REASON_GOAL_ATTENTION_DEFERRED,
        REASON_WORLD_OPPORTUNITY_OBSERVED,
        REASON_KNOWN_BLOCKING_WAIT,
        REASON_ABANDON_NONTERMINAL_CHOSEN,
        REASON_OPEN_ENDED_CONTINUATION,
        REASON_PURE_CONSUMPTION_CHOSEN,
        REASON_TIMING_NOT_APPROPRIATE,
    }
)


class DecisionReasonCodeRegistry:
    """Section 73-74: Versioned registry of structural DecisionReasonCodes (never psychological/preference truth)."""

    VERSION = REASON_REGISTRY_VERSION_V1

    DESCRIPTIONS: Mapping[str, str] = {
        REASON_NO_LEGAL_CANDIDATE: "CandidateSet was empty or all candidates were hard-rejected.",
        REASON_CAPACITY_UNAVAILABLE: "Candidate is currently not executable due to capacity unavailability.",
        REASON_CAPACITY_LIMITED: "Capacity is limited; choice reflects current resource/body bounds.",
        REASON_CAPACITY_UNKNOWN_DEFERRED: "Required capacity dimension is UNKNOWN; candidate deferred by policy.",
        REASON_ATOMIC_IN_PROGRESS: "Foreground Activity is in an ATOMIC non-interruptible segment.",
        REASON_SAFE_BOUNDARY_PAUSE_RECORDED: "Foreground Activity is at SAFE_BOUNDARY; pause/switch decision recorded for AG-2.",
        REASON_CONTINUITY_PREFERRED: "Continuing current active Activity was selected over switching.",
        REASON_CURRENT_ACTIVITY_CONTINUITY: "Foreground Activity remains active and coherent to continue.",
        REASON_TIME_WINDOW_CLOSING: "Candidate validity window is closing soon.",
        REASON_REQUEST_CURRENTLY_FEASIBLE: "User request candidate is legal and currently executable.",
        REASON_USER_REQUEST_VALID: "Observed explicit user request is valid and selected.",
        REASON_USER_REQUEST_DEFERRED: "Explicit user request is valid and preserved, but deferred for now.",
        REASON_SWITCH_COST_HIGH: "Switching away from current Activity carries high switch cost.",
        REASON_SWITCH_COST_ACCEPTABLE: "Switch cost from current context is acceptable.",
        REASON_RESOURCE_LIMIT: "Runtime/API/tool/file resource limit prevents immediate execution.",
        REASON_NO_ACTION_CHOSEN: "Autonomous decision not to claim any candidate on this opportunity.",
        REASON_RESUME_ELIGIBLE_READY: "Open ResumeEligibility candidate was selected for resumption.",
        REASON_COMMITMENT_FEASIBLE: "Active canonical commitment candidate is executable now.",
        REASON_COMMITMENT_DEFERRED_BY_CAPACITY: "Commitment remains valid but deferred due to capacity/conditions.",
        REASON_GOAL_STEP_ACTIONABLE: "Explicit goal has an actionable step candidate.",
        REASON_GOAL_ATTENTION_DEFERRED: "Goal attention candidate has no decomposed step; cannot START directly.",
        REASON_WORLD_OPPORTUNITY_OBSERVED: "Observed world opportunity candidate is valid and selected.",
        REASON_KNOWN_BLOCKING_WAIT: "Known external blocking condition justifies WAIT decision.",
        REASON_ABANDON_NONTERMINAL_CHOSEN: "Existing nonterminal Activity was chosen to be abandoned.",
        REASON_OPEN_ENDED_CONTINUATION: "Continuing an open-ended Activity without a CompletionContract.",
        REASON_PURE_CONSUMPTION_CHOSEN: "Pure consumption / non-artifact candidate selected.",
        REASON_TIMING_NOT_APPROPRIATE: "Candidate remains valid but current timing is not suitable.",
    }

    @classmethod
    def is_valid(cls, code: str) -> bool:
        return isinstance(code, str) and code in VALID_DECISION_REASON_CODES

    @classmethod
    def validate_codes(cls, codes: Sequence[str], *, allow_empty: bool = False) -> list[str]:
        if isinstance(codes, (str, bytes)) or not isinstance(codes, Iterable):
            raise DecisionValidationError(
                "reason_codes must be a list of registered reason code strings",
                schema_field=SCHEMA_FIELD_REASON_CODES, schema_rule=SCHEMA_RULE_NOT_A_LIST,
                schema_extra={"value_type": _json_type_name(codes)},
            )
        normalized: list[str] = []
        for raw in codes:
            if not isinstance(raw, str) or raw.strip() not in VALID_DECISION_REASON_CODES:
                raise DecisionValidationError(
                    f"unregistered reason_code={raw!r}; must belong to DecisionReasonCodeRegistry ({cls.VERSION})",
                    failure_class=FAILURE_CLASS_UNKNOWN_REASON_CODE,
                    schema_field=SCHEMA_FIELD_REASON_CODES,
                    schema_rule=SCHEMA_RULE_UNREGISTERED_CODE,
                )
            c = raw.strip()
            if c not in normalized:
                normalized.append(c)
        if not allow_empty and not normalized:
            raise DecisionValidationError(
                "reason_codes must contain at least one registered reason code",
                failure_class=FAILURE_CLASS_UNKNOWN_REASON_CODE,
                schema_field=SCHEMA_FIELD_REASON_CODES,
                schema_rule=SCHEMA_RULE_EMPTY_NOT_ALLOWED,
            )
        return normalized


# ---------------------------------------------------------------------------
# Forbidden Keys (Sections 10, 12, 41-42, 152)
# ---------------------------------------------------------------------------

FORBIDDEN_HIDDEN_COT_KEYS = frozenset(
    {
        "chain_of_thought",
        "full_chain_of_thought",
        "scratchpad",
        "internal_scratchpad",
        "hidden_reasoning_tokens",
        "provider_reasoning",
        "raw_thinking",
        "thinking_trace",
    }
)

FORBIDDEN_UNIVERSAL_SCORE_KEYS = frozenset(
    {
        "score",
        "total_score",
        "accessibility_score",
        "utility",
        "universal_utility",
        "reward",
        "argmax_score",
        "rank",
        "priority",
        "agency_quality_score",
        "user_compliance_score",
    }
)

FORBIDDEN_DOMAIN_BODY_KEYS = frozenset(
    {
        "activity_body",
        "activity_title",
        "activity_summary",
        "goal_body",
        "goal_description",
        "commitment_body",
        "world_state",
        "world_snapshot",
        "memory_content",
        "user_message_text",
        "raw_message_body",
        "prompt_text",
        "llm_narration",
    }
)

_STRUCTURED_REF_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]*[:_][a-zA-Z0-9_.:/-]+$")

# Unobserved world fact keywords used to detect hallucinated rationale claims (Section 56, H05)
_ENVIRONMENTAL_CLAIM_MARKERS: Mapping[str, tuple[str, ...]] = {
    "weather:rain": ("下雨", "暴雨", "rain", "raining", "rainstorm"),
    "weather:snow": ("下雪", "暴雪", "snow", "snowing"),
    "world:power_outage": ("停电", "断电", "power outage", "blackout"),
    "world:earthquake": ("地震", "earthquake"),
    "world:fire": ("着火", "火灾", "on fire"),
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgencyDecisionError(ContractViolationError):
    """Base error for AG-1 Agency Decision contract violations."""


class UnauthorizedAgencyMutationError(WriterCapabilityError):
    """Sections 2, 11, 83-87, 156-162: Raised when AG-1 is asked to mutate Activity, Action, World, Memory, Goal, Commitment, Agenda, or Outbound."""


class ForbiddenPeriodicTickError(AgencyDecisionError):
    """Section 17: Raised when a periodic cron/timer tries to invoke Agency without a real DecisionOpportunity."""


class ForbiddenUnobservedContextError(AgencyDecisionError):
    """Sections 21, 53: Raised when unobserved World DB facts or unauthorized Memory/secrets are passed into DecisionContextSnapshot."""


class MissingConstraintAuthorityError(AgencyDecisionError):
    """Section 25: Raised when a hard constraint rejection lacks a structured constraint_ref."""


class UniversalUtilityForbiddenError(AgencyDecisionError):
    """Sections 22, 41-42: Raised when universal utility scores or argmax rankings are introduced."""

    #: v0.4 schema-repair: same reason as ``HiddenReasoningForbiddenError``.
    failure_class = FAILURE_CLASS_FORBIDDEN_UNIVERSAL_UTILITY


class HiddenReasoningForbiddenError(AgencyDecisionError):
    """Section 10 & 75: Raised when full chain-of-thought or hidden scratchpad tokens are persisted."""

    #: v0.4 schema-repair: named, so a forbidden-CoT answer is not filed as a schema shape error.
    failure_class = FAILURE_CLASS_FORBIDDEN_HIDDEN_REASONING


class DecisionImmutabilityError(AgencyDecisionError):
    """Section 79: Raised when attempting to mutate an already committed DecisionRecord."""


class DecisionValidationError(AgencyDecisionError):
    """Section 59: Raised when model/decision output fails strict structural or semantic validation."""

    def __init__(self, message: str, *, failure_class: str = FAILURE_CLASS_INVALID_SCHEMA,
                 schema_field: Optional[str] = None, schema_rule: Optional[str] = None,
                 schema_extra: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        #: v0.4 schema-repair: *where* the refusal happened and *which* rule broke, both from the
        #: closed vocabularies below.  They exist because the observed failure only ever said
        #: ``INVALID_SCHEMA``: the human-readable message names the field but can also quote the
        #: model ("unknown keys: [...]", "reason_code=...", "method_ref=..."), so the message itself
        #: can never be journalled.  These two are nameable with no model text at all.
        self.schema_field = schema_field
        self.schema_rule = schema_rule
        self.schema_extra = dict(schema_extra or {})


#: v0.4 schema-repair vocabulary.  A *field* is a key name of this module's own DecisionOutput
#: contract; a *rule* is which check refused it.  Neither is model text.
SCHEMA_FIELD_DECISION_OUTPUT = "decision_output"
SCHEMA_FIELD_DECISION = "decision"
SCHEMA_FIELD_SELECTED_CANDIDATE_ID = "selected_candidate_id"
SCHEMA_FIELD_DEFERRED_CANDIDATE_IDS = "deferred_candidate_ids"
SCHEMA_FIELD_REASON_CODES = "reason_codes"
SCHEMA_FIELD_SHORT_RATIONALE = "short_rationale"
SCHEMA_FIELD_LATER_ITEM = "later_item"
SCHEMA_FIELD_FUTURE_ITEM_ACTIONS = "future_item_actions"
SCHEMA_FIELD_METHOD_REF = "method_ref"
SCHEMA_FIELD_METHOD_TRIAL = "method_trial"
SCHEMA_FIELD_METHOD_PROPOSAL = "method_proposal"
SCHEMA_FIELD_SHARING = "sharing"
SCHEMA_FIELDS = frozenset({
    SCHEMA_FIELD_DECISION_OUTPUT, SCHEMA_FIELD_DECISION, SCHEMA_FIELD_SELECTED_CANDIDATE_ID,
    SCHEMA_FIELD_DEFERRED_CANDIDATE_IDS, SCHEMA_FIELD_REASON_CODES, SCHEMA_FIELD_SHORT_RATIONALE,
    SCHEMA_FIELD_LATER_ITEM, SCHEMA_FIELD_FUTURE_ITEM_ACTIONS, SCHEMA_FIELD_METHOD_REF,
    SCHEMA_FIELD_METHOD_TRIAL, SCHEMA_FIELD_METHOD_PROPOSAL, SCHEMA_FIELD_SHARING,
})

#: The placeholder a refusal uses when it cannot name a field, and the word a bad value is replaced
#: by.  Deliberately *not* a member of ``SCHEMA_FIELDS``: it is the absence of a name.
SCHEMA_FIELD_UNKNOWN = "unknown"

SCHEMA_RULE_NOT_AN_OBJECT = "NOT_A_JSON_OBJECT"
SCHEMA_RULE_UNKNOWN_KEYS = "UNKNOWN_TOP_LEVEL_KEYS"
SCHEMA_RULE_MISSING_OR_WRONG_TYPE = "MISSING_OR_WRONG_TYPE"
SCHEMA_RULE_NOT_A_LIST = "NOT_A_LIST"
SCHEMA_RULE_EMPTY_NOT_ALLOWED = "EMPTY_NOT_ALLOWED"
SCHEMA_RULE_UNREGISTERED_CODE = "UNREGISTERED_CODE"
SCHEMA_RULE_TOO_LONG = "TOO_LONG"
SCHEMA_RULE_REFUSED_BY_DOMAIN = "REFUSED_BY_DOMAIN"
SCHEMA_RULE_FORBIDDEN_HIDDEN_REASONING = "FORBIDDEN_HIDDEN_REASONING"
SCHEMA_RULE_UNCLASSIFIED = "UNCLASSIFIED"
SCHEMA_RULES = frozenset({
    SCHEMA_RULE_NOT_AN_OBJECT, SCHEMA_RULE_UNKNOWN_KEYS, SCHEMA_RULE_MISSING_OR_WRONG_TYPE,
    SCHEMA_RULE_NOT_A_LIST, SCHEMA_RULE_EMPTY_NOT_ALLOWED, SCHEMA_RULE_UNREGISTERED_CODE,
    SCHEMA_RULE_TOO_LONG, SCHEMA_RULE_REFUSED_BY_DOMAIN, SCHEMA_RULE_FORBIDDEN_HIDDEN_REASONING,
    SCHEMA_RULE_UNCLASSIFIED,
})

#: An unknown top-level key is *model* output, so it is never journalled verbatim: a key can carry
#: someone's words (``{"notes": "<private text>"}``).  Only the count is recorded, plus a hit
#: against this fixed alias list -- names authored here, so a hit reads "the answer carried an
#: extra ``reasoning`` key" and can never echo whatever the model actually wrote.
SCHEMA_ALIAS_KEYS = frozenset({
    "answer", "analysis", "comment", "confidence", "explanation", "json", "meta", "metadata",
    "note", "notes", "output", "rationale", "reasoning", "reasoning_content", "response",
    "result", "thought", "thoughts", "thinking", "why",
})
SCHEMA_KEY_SHAPES = frozenset({"ascii_identifier", "other"})
#: The only keys a persisted refusal record may carry.  Applied again on the way out so a future
#: caller cannot accidentally store more than this (defence in depth, not the only defence).
SCHEMA_DETAIL_KEYS = ("failure_class", "field", "rule", "value_type", "unknown_key_count",
                      "unknown_key_alias_hits", "unknown_key_shapes")

#: Every failure class this module itself can record.  ``failure_class`` inside a refusal record
#: must be one of these constants -- an arbitrary value becomes ``SCHEMA_DETAIL_UNKNOWN`` instead
#: of being echoed back.
SCHEMA_DETAIL_FAILURE_CLASSES = frozenset({
    FAILURE_CLASS_MODEL_TIMEOUT, FAILURE_CLASS_MODEL_REFUSAL, FAILURE_CLASS_INVALID_JSON,
    FAILURE_CLASS_INVALID_SCHEMA, FAILURE_CLASS_INVENTED_CANDIDATE,
    FAILURE_CLASS_HARD_CONSTRAINT_VIOLATION, FAILURE_CLASS_ILLEGAL_DECISION_ENUM,
    FAILURE_CLASS_ILLEGAL_DECISION_MATRIX, FAILURE_CLASS_ATOMIC_VIOLATION,
    FAILURE_CLASS_UNKNOWN_REASON_CODE, FAILURE_CLASS_STALE_BEFORE_COMMIT,
    FAILURE_CLASS_INTERRUPTED_DURING_COGNITION, FAILURE_CLASS_CANDIDATE_EXPLOSION,
    FAILURE_CLASS_INVENTED_METHOD_REF, FAILURE_CLASS_INVALID_METHOD_TRIAL,
    FAILURE_CLASS_FORBIDDEN_HIDDEN_REASONING, FAILURE_CLASS_FORBIDDEN_UNIVERSAL_UTILITY,
})
SCHEMA_DETAIL_UNKNOWN = "UNKNOWN"
#: The seven JSON type names a ``value_type`` may be; anything else is dropped.
SCHEMA_VALUE_TYPES = frozenset({"null", "bool", "number", "string", "object", "array", "other"})
#: Upper bound for the derived integer counts (a bound, not a guess: nobody offers 64 extra keys).
SCHEMA_DETAIL_MAX_COUNT = 64


def _json_type_name(value: Any) -> str:
    """The JSON type of a model value.  A type name is not content."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "other"


def _unknown_key_facts(keys: Iterable[Any]) -> dict[str, Any]:
    """Counts and alias hits for unknown keys -- never the keys themselves."""
    names = [str(key) for key in keys]
    lowered = [name.strip().lower() for name in names]
    ascii_identifier = sum(1 for name in names
                           if name.isascii() and name.replace("_", "").isalnum())
    return {
        "unknown_key_count": len(names),
        "unknown_key_alias_hits": sorted({name for name in lowered if name in SCHEMA_ALIAS_KEYS}),
        "unknown_key_shapes": {"ascii_identifier": ascii_identifier,
                               "other": len(names) - ascii_identifier},
    }


def describe_schema_refusal(exc: BaseException) -> dict[str, Any]:
    """A value-free account of *why* an answer was refused: field, rule and derived counts.

    This is the record a real failed attempt leaves behind, so the next run can say
    "``short_rationale`` was TOO_LONG" instead of only "INVALID_SCHEMA".  The raw values come from
    the exception's own attributes, and every one of them is re-checked by
    ``sanitize_schema_refusal`` -- an attribute that somehow carried arbitrary text cannot reach a
    journal through here.
    """

    extra = getattr(exc, "schema_extra", None)
    raw: dict[str, Any] = {
        "failure_class": getattr(exc, "failure_class", None),
        "field": getattr(exc, "schema_field", None),
        "rule": getattr(exc, "schema_rule", None),
    }
    if isinstance(extra, Mapping):
        for key in ("value_type", "unknown_key_count", "unknown_key_alias_hits",
                    "unknown_key_shapes"):
            if key in extra:
                raw[key] = extra[key]
    return sanitize_schema_refusal(raw) or {}


def sanitize_schema_refusal(detail: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Rebuild a refusal record from **validated values only** -- the whitelist is the easy half.

    A key being allowed says nothing about its *value*: ``{"field": "<someone's words>"}`` passes a
    key-only filter and lands in the journal.  So every value is re-derived here:

    * ``failure_class`` must be one of this module's own failure-class constants (else ``UNKNOWN``);
    * ``field`` / ``rule`` must be members of the closed vocabularies (else ``unknown`` /
      ``UNCLASSIFIED``);
    * ``value_type`` must be one of the seven JSON type names (else dropped);
    * ``unknown_key_count`` must be a real int in ``0..SCHEMA_DETAIL_MAX_COUNT`` (bools dropped);
    * ``unknown_key_alias_hits`` may only hold names from ``SCHEMA_ALIAS_KEYS`` (deduped, capped);
    * ``unknown_key_shapes`` may only hold ``SCHEMA_KEY_SHAPES`` keys with ints in the same bound.

    Nothing is echoed back: a value that fails validation becomes a fixed word or is dropped.
    """

    if not isinstance(detail, Mapping) or not detail:
        return None
    out: dict[str, Any] = {}

    failure_class = detail.get("failure_class")
    out["failure_class"] = (failure_class if isinstance(failure_class, str)
                            and failure_class in SCHEMA_DETAIL_FAILURE_CLASSES
                            else SCHEMA_DETAIL_UNKNOWN)

    field = detail.get("field")
    out["field"] = field if isinstance(field, str) and field in SCHEMA_FIELDS \
        else SCHEMA_FIELD_UNKNOWN

    rule = detail.get("rule")
    out["rule"] = rule if isinstance(rule, str) and rule in SCHEMA_RULES \
        else SCHEMA_RULE_UNCLASSIFIED

    value_type = detail.get("value_type")
    if isinstance(value_type, str) and value_type in SCHEMA_VALUE_TYPES:
        out["value_type"] = value_type

    count = detail.get("unknown_key_count")
    if isinstance(count, int) and not isinstance(count, bool) \
            and 0 <= count <= SCHEMA_DETAIL_MAX_COUNT:
        out["unknown_key_count"] = count

    hits = detail.get("unknown_key_alias_hits")
    if isinstance(hits, (list, tuple)):
        clean_hits = sorted({name for name in hits
                             if isinstance(name, str) and name in SCHEMA_ALIAS_KEYS})
        if clean_hits:
            out["unknown_key_alias_hits"] = clean_hits[:len(SCHEMA_ALIAS_KEYS)]

    shapes = detail.get("unknown_key_shapes")
    if isinstance(shapes, Mapping):
        clean_shapes: dict[str, int] = {}
        for key, value in shapes.items():
            if key in SCHEMA_KEY_SHAPES and isinstance(value, int) \
                    and not isinstance(value, bool) and 0 <= value <= SCHEMA_DETAIL_MAX_COUNT:
                clean_shapes[key] = value
        if clean_shapes:
            out["unknown_key_shapes"] = clean_shapes
    if (out["failure_class"] == SCHEMA_DETAIL_UNKNOWN
            and out["field"] == SCHEMA_FIELD_UNKNOWN
            and out["rule"] == SCHEMA_RULE_UNCLASSIFIED):
        # Nothing was nameable.  An all-placeholder record is noise, not evidence, and writing one
        # would let a future reader mistake "we know nothing" for "we checked".
        return None
    return out


def failure_journal_payload(
    *,
    opportunity_id: str,
    attempt_id: str,
    failure_class: str,
    failure_ref: Optional[str],
    model_run_ref: Optional[str],
    schema_detail: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """The ``DECISION_ATTEMPT_FAILED`` journal payload, now naming *why* when it is known.

    Without a schema detail the payload is byte-for-byte the six keys older entries carry, so
    historical journals stay comparable.  With one, a single additive ``schema_refusal`` object
    says which field and which rule -- still no model text.
    """

    payload: dict[str, Any] = {
        "event_type": "DECISION_ATTEMPT_FAILED",
        "opportunity_id": opportunity_id,
        "attempt_id": attempt_id,
        "failure_class": failure_class,
        "failure_ref": failure_ref,
        "model_run_ref": model_run_ref,
    }
    clean = sanitize_schema_refusal(schema_detail)
    if clean:
        payload["schema_refusal"] = clean
    return payload


def _annotate_schema_field(exc: "DecisionValidationError", field: str,
                           rule: Optional[str] = None) -> "DecisionValidationError":
    """Name the field a nested refusal came from (``later_item``, ``sharing``, ...), once."""

    if getattr(exc, "schema_field", None) is None:
        exc.schema_field = field
    if getattr(exc, "schema_rule", None) is None:
        exc.schema_rule = rule or SCHEMA_RULE_UNCLASSIFIED
    return exc


class CognitionAdapterError(RuntimeError):
    """Section 6, 62-63: Raised by AgencyCognitionAdapter on timeout, refusal, or transport error."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str = FAILURE_CLASS_MODEL_TIMEOUT,
        model_run_ref: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.model_run_ref = model_run_ref


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------


def _require_iso(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgencyDecisionError(f"{field_name} must be a non-empty ISO-8601 string")
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgencyDecisionError(f"{field_name} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _iso(dt)


def _optional_iso(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _require_iso(value, field_name)


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _validate_structured_ref(
    value: Any,
    field_name: str,
    *,
    allowed_prefixes: Optional[Iterable[str]] = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgencyDecisionError(f"{field_name} must be a non-empty structured reference")
    cleaned = value.strip()
    if not _STRUCTURED_REF_RE.fullmatch(cleaned):
        raise AgencyDecisionError(
            f"{field_name}={value!r} must be a structured reference '<prefix>:<id>', not free text"
        )
    if allowed_prefixes is not None:
        prefix = cleaned.split(":", 1)[0] if ":" in cleaned else cleaned.split("_", 1)[0]
        allowed_set = set(allowed_prefixes)
        if prefix not in allowed_set:
            raise AgencyDecisionError(
                f"{field_name}={value!r} has prefix {prefix!r}; expected one of {sorted(allowed_set)}"
            )
    return cleaned


def _validate_ref_list(
    values: Any,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if values is None:
        if allow_empty:
            return []
        raise AgencyDecisionError(f"{field_name} cannot be None")
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise AgencyDecisionError(f"{field_name} must be a sequence of structured reference strings")
    result: list[str] = []
    for item in values:
        ref = _validate_structured_ref(item, field_name)
        if ref not in result:
            result.append(ref)
    if not allow_empty and not result:
        raise AgencyDecisionError(f"{field_name} must contain at least one structured reference")
    return result


def _reject_forbidden_dict_keys(mapping: Mapping[str, Any], context_name: str) -> None:
    lowered = {str(k).lower(): k for k in mapping.keys()}
    bad_cot = FORBIDDEN_HIDDEN_COT_KEYS.intersection(lowered.keys())
    if bad_cot:
        raise HiddenReasoningForbiddenError(
            f"{context_name} contains forbidden hidden chain-of-thought/scratchpad keys: {sorted(bad_cot)}"
        )
    bad_score = FORBIDDEN_UNIVERSAL_SCORE_KEYS.intersection(lowered.keys())
    if bad_score:
        raise UniversalUtilityForbiddenError(
            f"{context_name} contains forbidden universal score/utility/ranking keys: {sorted(bad_score)}"
        )
    bad_body = FORBIDDEN_DOMAIN_BODY_KEYS.intersection(lowered.keys())
    if bad_body:
        raise AgencyDecisionError(
            f"{context_name} contains forbidden duplicated domain body keys: {sorted(bad_body)}"
        )


def sanitize_short_rationale(
    raw_rationale: Optional[str],
    *,
    observed_facts: Mapping[str, Any],
) -> tuple[Optional[str], bool]:
    """Sections 10, 56, 75: Validate and sanitize short_rationale.

    - Rejects/strips hidden reasoning tags (<think>, scratchpad).
    - If rationale claims an unobserved environmental fact (e.g., '因为今天下雨' when 'weather:rain'
      is not in observed_facts), drops the ungrounded claim and returns (None, True) so hallucinated
      facts are NEVER persisted in DecisionRecord (Section 56, H05).
    """
    if raw_rationale is None:
        return None, False
    if not isinstance(raw_rationale, str):
        raise DecisionValidationError(
            "short_rationale must be a string or null",
            schema_field=SCHEMA_FIELD_SHORT_RATIONALE,
            schema_rule=SCHEMA_RULE_MISSING_OR_WRONG_TYPE,
            schema_extra={"value_type": _json_type_name(raw_rationale)},
        )
    cleaned = raw_rationale.strip()
    if not cleaned:
        return None, False
    if "<think>" in cleaned.lower() or "scratchpad:" in cleaned.lower():
        raise HiddenReasoningForbiddenError("short_rationale must not contain <think> or scratchpad dumps")
    if len(cleaned) > MAX_SHORT_RATIONALE_CHARS:
        raise DecisionValidationError(
            f"short_rationale exceeds MAX_SHORT_RATIONALE_CHARS ({len(cleaned)} > {MAX_SHORT_RATIONALE_CHARS})",
            schema_field=SCHEMA_FIELD_SHORT_RATIONALE, schema_rule=SCHEMA_RULE_TOO_LONG,
        )

    lower_text = cleaned.lower()
    obs_keys = {str(k).lower() for k in observed_facts.keys()}
    obs_vals = {str(v).lower() for v in observed_facts.values() if v is not None}
    for fact_key, markers in _ENVIRONMENTAL_CLAIM_MARKERS.items():
        if any(m in lower_text for m in markers):
            fk_lower = fact_key.lower()
            short_key = fk_lower.split(":", 1)[-1]
            if fk_lower not in obs_keys and short_key not in obs_keys and short_key not in obs_vals:
                # Section 56: invalid rationale claim -> drop and do not persist as Decision fact
                return None, True

    return cleaned, False


# ---------------------------------------------------------------------------
# Sections 83-87, 164: AgencyDecisionAuthority & Capability Guards
def validate_method_trial(raw: Any, *, method_ref: Optional[str] = None) -> dict[str, Any]:
    """v0.4 C: one bounded trial evaluation, validated by *its own* small contract.

    The shape is deliberately tiny -- which practice, which verdict, a bounded sentence and the
    experiences it rests on -- because the *evaluation* is the whole object here.  It is not a
    write: B's practice workspace records it, and only against evidence A can still read, so a
    successful version write can never be laundered into "it helped".
    """

    if not isinstance(raw, Mapping):
        raise DecisionValidationError(
            "method_trial must be a JSON object", failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL)
    unknown = {str(key) for key in raw} - METHOD_TRIAL_KEYS
    if unknown:
        raise DecisionValidationError(
            f"method_trial carries unknown keys: {sorted(unknown)}",
            failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL)
    ref = raw.get("method_ref")
    text = ref.strip() if isinstance(ref, str) else ""
    if not text or not text.startswith(METHOD_REF_PREFIX) or ":" in text[len(METHOD_REF_PREFIX):]:
        raise DecisionValidationError(
            "method_trial.method_ref must be a bounded practice handle",
            failure_class=FAILURE_CLASS_INVENTED_METHOD_REF)
    if method_ref is not None and text != str(method_ref).strip():
        # One trial, one subject: a verdict may not be filed against a different practice than
        # the one this Decision said it was following.
        raise DecisionValidationError(
            f"method_trial.method_ref={text!r} does not match the chosen method_ref={method_ref!r}",
            failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL)
    outcome = raw.get("outcome")
    verdict = outcome.strip().upper() if isinstance(outcome, str) else ""
    if verdict not in METHOD_TRIAL_OUTCOMES:
        raise DecisionValidationError(
            f"method_trial.outcome must be one of {sorted(METHOD_TRIAL_OUTCOMES)}",
            failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL)
    detail = raw.get("detail")
    if detail is not None and not isinstance(detail, str):
        raise DecisionValidationError(
            "method_trial.detail must be text", failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL)
    ids = raw.get("experience_ids") or []
    if isinstance(ids, (str, bytes)) or not isinstance(ids, Iterable):
        raise DecisionValidationError(
            "method_trial.experience_ids must be a list",
            failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL)
    clean_ids: list[str] = []
    for item in list(ids)[:8]:
        if not isinstance(item, str) or not item.strip():
            raise DecisionValidationError(
                "method_trial.experience_ids must be non-empty strings",
                failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL)
        if item.strip() not in clean_ids:
            clean_ids.append(item.strip())
    return {
        "method_ref": text,
        "outcome": verdict,
        "detail": (detail or "").strip()[:MAX_METHOD_TRIAL_DETAIL_CHARS],
        "experience_ids": clean_ids,
    }


# ---------------------------------------------------------------------------


def validate_method_proposal(raw: Any) -> dict[str, Any]:
    """v0.4 C: one bounded, *scoped* practice proposal, checked before it can be saved.

    A method is one concrete thing to try, the conditions it is believed to hold under, and what is
    still unknown -- never a general preference, a persona trait or a program.  The references it
    cites are re-checked by B's service against A's Memory at write time; here they are only
    required to be well-formed, because a host with no Memory must be able to propose a *draft*.
    """

    if not isinstance(raw, Mapping):
        raise DecisionValidationError(
            "method_proposal must be a JSON object", failure_class=FAILURE_CLASS_INVALID_SCHEMA)
    unknown = {str(key) for key in raw} - METHOD_PROPOSAL_KEYS
    if unknown:
        raise DecisionValidationError(
            f"method_proposal carries unknown keys: {sorted(unknown)}",
            failure_class=FAILURE_CLASS_INVALID_SCHEMA)

    def _text(name: str, limit: int, *, required: bool) -> str:
        value = raw.get(name)
        if value is None and not required:
            return ""
        if not isinstance(value, str):
            raise DecisionValidationError(
                f"method_proposal.{name} must be text",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        text = " ".join(value.split())
        if required and not text:
            raise DecisionValidationError(
                f"method_proposal.{name} must not be empty",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        return text[:limit]

    def _refs(name: str, limit: int) -> list[str]:
        value = raw.get(name) or []
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise DecisionValidationError(
                f"method_proposal.{name} must be a list",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        out: list[str] = []
        for item in list(value)[:limit]:
            if not isinstance(item, str) or not item.strip():
                raise DecisionValidationError(
                    f"method_proposal.{name} must be non-empty strings",
                    failure_class=FAILURE_CLASS_INVALID_SCHEMA)
            if item.strip() not in out:
                out.append(item.strip())
        return out

    return {
        "title": _text("title", MAX_METHOD_TITLE_CHARS, required=True),
        "instruction": _text("instruction", MAX_METHOD_INSTRUCTION_CHARS, required=True),
        "scope": _text("scope", MAX_METHOD_SCOPE_CHARS, required=False),
        "uncertainty": _text("uncertainty", MAX_METHOD_UNCERTAINTY_CHARS, required=False),
        "experience_ids": _refs("experience_ids", 8),
        "review_refs": _refs("review_refs", 6),
    }

#: v0.4 sharing: the ONE extra life-side action a Decision may propose, and the closed vocabulary
#: it is spelled with.  The owner of every field is B's ``life_sharing_service``; this module only
#: refuses a proposal whose *shape* could not be a legal request (wrong op, invented identifier,
#: unbounded text, a free-form recipient).  Nothing here sends anything: the cycle asks the owner,
#: and only the owner's own answer is recorded.
SHARING_ACTION_PREPARE = "prepare"
SHARING_ACTION_SEND_LOCAL = "send_local"
SHARING_ACTION_DEFER = "defer"
SHARING_ACTION_DROP = "drop"
SHARING_ACTIONS = (SHARING_ACTION_PREPARE, SHARING_ACTION_SEND_LOCAL,
                   SHARING_ACTION_DEFER, SHARING_ACTION_DROP)

SHARING_PROPOSAL_KEYS = frozenset({
    "op", "source_artifact_id", "share_text", "excerpt_chars", "title", "expires_after_s",
    "share_id", "expected_head", "defer_condition", "opportunity_ref", "reason",
})
#: Fields an audience/channel must never travel in: B fixes both (`local:owner` / `local_inbox`)
#: and refuses anything else.  Naming one here is an invention, not a narrower request.
SHARING_FORBIDDEN_KEYS = frozenset({
    "audience", "channel", "recipient", "to", "email", "url", "telegram", "api",
})
MAX_SHARE_TEXT_CHARS = 2000
MAX_SHARE_EXCERPT_CHARS = 400
MAX_SHARE_TITLE_CHARS = 80
MAX_SHARE_CONDITION_CHARS = 200
MAX_SHARE_REASON_CHARS = 200
MIN_SHARE_EXPIRY_S = 60
MAX_SHARE_EXPIRY_S = 2_592_000


def validate_sharing_action(raw: Any) -> dict[str, Any]:
    """One bounded sharing request: its *shape* only, never an audience and never a send.

    The ops are exactly B's ``SHARE_ACTIONS`` minus ``expire`` (an expired draft is something the
    owner's own clock reports; A does not get to declare a draft dead by hand).  ``prepare`` binds a
    real source artifact and an *explicit* bounded text (or an explicit prefix length) -- a share
    with nothing to say is refused here exactly as the owner would refuse it.
    """

    if not isinstance(raw, Mapping):
        raise DecisionValidationError(
            "sharing must be a JSON object", failure_class=FAILURE_CLASS_INVALID_SCHEMA)
    forbidden = {str(key) for key in raw} & SHARING_FORBIDDEN_KEYS
    if forbidden:
        raise DecisionValidationError(
            f"sharing may not name an audience or channel ({sorted(forbidden)}): the owner fixes "
            "them, and there is no free-form recipient",
            failure_class=FAILURE_CLASS_INVALID_SCHEMA)
    unknown = {str(key) for key in raw} - SHARING_PROPOSAL_KEYS
    if unknown:
        raise DecisionValidationError(
            f"sharing carries unknown keys: {sorted(unknown)}",
            failure_class=FAILURE_CLASS_INVALID_SCHEMA)

    op = raw.get("op")
    op = op.strip() if isinstance(op, str) else ""
    if op not in SHARING_ACTIONS:
        raise DecisionValidationError(
            f"sharing.op={raw.get('op')!r} must be one of {list(SHARING_ACTIONS)}",
            failure_class=FAILURE_CLASS_INVALID_SCHEMA)

    def _text(name: str, limit: int, *, required: bool = False) -> str:
        value = raw.get(name)
        if value is None:
            if required:
                raise DecisionValidationError(
                    f"sharing.{name} is required for op={op}",
                    failure_class=FAILURE_CLASS_INVALID_SCHEMA)
            return ""
        if not isinstance(value, str):
            raise DecisionValidationError(
                f"sharing.{name} must be text", failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        text = " ".join(value.split()) if limit <= MAX_SHARE_CONDITION_CHARS else value.strip()
        if required and not text:
            raise DecisionValidationError(
                f"sharing.{name} must not be empty for op={op}",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        return text[:limit]

    def _count(name: str, low: int, high: int) -> Optional[int]:
        value = raw.get(name)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise DecisionValidationError(
                f"sharing.{name} must be an integer",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        if value < low or value > high:
            raise DecisionValidationError(
                f"sharing.{name}={value} is outside [{low}, {high}]",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        return value

    def _ref(text: str, name: str, prefix: str) -> str:
        """One structured reference of the expected prefix, or a decision-schema refusal."""

        try:
            return _validate_structured_ref(text, name, allowed_prefixes=frozenset({prefix}))
        except AgencyDecisionError as exc:
            raise DecisionValidationError(
                f"{name}={text!r} is not a bounded {prefix} reference",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA) from exc

    def _share_ref(name: str) -> str:
        value = raw.get(name)
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            raise DecisionValidationError(
                f"sharing.{name} is required for op={op}",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        if not text.startswith("artifact:"):
            raise DecisionValidationError(
                f"sharing.{name}={value!r} is not a share artifact reference",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        return _ref(text, f"sharing.{name}", "artifact")

    def _head_ref() -> str:
        value = raw.get("expected_head")
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            raise DecisionValidationError(
                "sharing.expected_head is required: the draft version this action was decided on",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        return _ref(text, "sharing.expected_head", "artifact-version")

    if op == SHARING_ACTION_PREPARE:
        source = raw.get("source_artifact_id")
        text = source.strip() if isinstance(source, str) else ""
        if not text or not text.startswith("artifact:"):
            raise DecisionValidationError(
                "sharing.source_artifact_id must be one of the source artifacts this round offered",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        share_text = _text("share_text", MAX_SHARE_TEXT_CHARS)
        excerpt = _count("excerpt_chars", 1, MAX_SHARE_EXCERPT_CHARS)
        if not share_text and excerpt is None:
            # The owner refuses this too (SHARE_TEXT_REQUIRED); refusing it here keeps the
            # "never send the whole private material by accident" rule visible in the prompt.
            raise DecisionValidationError(
                "sharing needs share_text (explicit words) or excerpt_chars (an explicit prefix)",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        return {
            "op": op,
            "source_artifact_id": _ref(text, "sharing.source_artifact_id", "artifact"),
            "share_text": share_text,
            "excerpt_chars": excerpt,
            "title": _text("title", MAX_SHARE_TITLE_CHARS),
            "expires_after_s": _count("expires_after_s", MIN_SHARE_EXPIRY_S, MAX_SHARE_EXPIRY_S),
        }

    share_id = _share_ref("share_id")
    expected_head = _head_ref()
    action: dict[str, Any] = {"op": op, "share_id": share_id, "expected_head": expected_head}
    if op == SHARING_ACTION_DEFER:
        condition = _text("defer_condition", MAX_SHARE_CONDITION_CHARS)
        opportunity = _text("opportunity_ref", MAX_SHARE_CONDITION_CHARS)
        if not condition and not opportunity:
            raise DecisionValidationError(
                "sharing.defer needs defer_condition or opportunity_ref: what are we waiting for?",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA)
        action["defer_condition"] = condition
        action["opportunity_ref"] = opportunity
    elif op == SHARING_ACTION_DROP:
        action["reason"] = _text("reason", MAX_SHARE_REASON_CHARS)
    return action

@dataclass(frozen=True)
class AgencyDecisionCapability:
    """Write capability restricted strictly to `AgencyDecisionStore` (`agency_decision_authority`).

    Carries ZERO permission to mutate Activity, Action, World, Memory, Goal, Commitment, Agenda, or Outbound.
    """

    writer_domain: str
    caller_module: str
    lease_id: str
    subject_id: str
    namespace: str
    store_root: Path
    issued_at: str
    signature: str
    can_write_activity: bool = False
    can_write_action: bool = False
    can_write_world: bool = False
    can_write_memory: bool = False
    can_write_goal: bool = False
    can_write_commitment: bool = False
    can_write_agenda_or_resume: bool = False
    can_send_outbound: bool = False

    def __post_init__(self) -> None:
        if self.writer_domain != WRITER_DOMAIN_AGENCY_DECISION:
            raise UnauthorizedAgencyMutationError(
                f"invalid AgencyDecisionCapability writer_domain={self.writer_domain!r}"
            )
        if self.namespace not in VALID_NAMESPACES:
            raise AgencyDecisionError(f"invalid namespace={self.namespace!r}")
        if any(
            (
                self.can_write_activity,
                self.can_write_action,
                self.can_write_world,
                self.can_write_memory,
                self.can_write_goal,
                self.can_write_commitment,
                self.can_write_agenda_or_resume,
                self.can_send_outbound,
            )
        ):
            raise UnauthorizedAgencyMutationError(
                "AG-1 AgencyDecisionCapability must NEVER grant Activity/Action/World/Memory/Goal/Commitment/Agenda/Outbound write capability"
            )


class AgencyDecisionAuthority:
    """Sections 83-87, 156-162: Sole authority for committing pre-adoption DecisionRecords in AG-1."""

    WRITER_DOMAIN = WRITER_DOMAIN_AGENCY_DECISION
    ALLOWED_CALLERS = frozenset(
        {
            "agency_decision_service",
            "agency_decision_store",
            "agency_decision_replay_verifier",
            "agency_shadow_runner",
            "ag1_test_harness",
        }
    )

    @classmethod
    def issue_capability(
        cls,
        lease: ActivityAuthorityLease,
        *,
        caller_module: str,
        store_root: Path | str,
        namespace: str = NAMESPACE_ISOLATED_TEST,
        environ: Optional[Mapping[str, str]] = None,
    ) -> AgencyDecisionCapability:
        if not isinstance(lease, ActivityAuthorityLease) or not lease.held:
            raise UnauthorizedAgencyMutationError(
                "AgencyDecisionAuthority requires an active held ActivityAuthorityLease"
            )
        if caller_module not in cls.ALLOWED_CALLERS:
            raise UnauthorizedAgencyMutationError(
                f"caller_module={caller_module!r} is not authorized to hold AgencyDecisionCapability"
            )
        if namespace == NAMESPACE_PRODUCTION and not is_agency_enabled(environ):
            raise UnauthorizedAgencyMutationError(
                "AG-1 production execution is disabled by kill switch (AGENCY_ENABLED=false)"
            )
        root_path = Path(store_root).expanduser().resolve()
        issued_ts = _iso(now())
        lease_id = str(getattr(lease, "lease_id", None) or getattr(lease, "instance_id", "lease:default"))
        subject_id = str(getattr(lease, "subject_id", None) or GLOBAL_SUBJECT_ID)
        msg = f"{cls.WRITER_DOMAIN}:{caller_module}:{lease_id}:{subject_id}:{namespace}:{root_path}:{issued_ts}"
        sig = hmac.new(lease._secret, msg.encode("utf-8"), hashlib.sha256).hexdigest()
        return AgencyDecisionCapability(
            writer_domain=cls.WRITER_DOMAIN,
            caller_module=caller_module,
            lease_id=lease_id,
            subject_id=subject_id,
            namespace=namespace,
            store_root=root_path,
            issued_at=issued_ts,
            signature=sig,
        )

    @classmethod
    def verify_capability(
        cls,
        lease: ActivityAuthorityLease,
        capability: AgencyDecisionCapability,
    ) -> None:
        if not isinstance(lease, ActivityAuthorityLease) or not lease.held:
            raise UnauthorizedAgencyMutationError("ActivityAuthorityLease is not held")
        if not isinstance(capability, AgencyDecisionCapability):
            raise UnauthorizedAgencyMutationError(
                f"expected AgencyDecisionCapability, got {type(capability).__name__}"
            )
        if capability.writer_domain != cls.WRITER_DOMAIN:
            raise UnauthorizedAgencyMutationError(
                f"invalid writer_domain={capability.writer_domain!r} for AgencyDecisionAuthority"
            )
        lease_id = str(getattr(lease, "lease_id", None) or getattr(lease, "instance_id", "lease:default"))
        subject_id = str(getattr(lease, "subject_id", None) or GLOBAL_SUBJECT_ID)
        if capability.lease_id != lease_id or capability.subject_id != subject_id:
            raise UnauthorizedAgencyMutationError("AgencyDecisionCapability lease/subject mismatch")
        msg = (
            f"{capability.writer_domain}:{capability.caller_module}:{capability.lease_id}:"
            f"{capability.subject_id}:{capability.namespace}:{capability.store_root}:{capability.issued_at}"
        )
        expected_sig = hmac.new(lease._secret, msg.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, capability.signature):
            raise UnauthorizedAgencyMutationError("AgencyDecisionCapability signature verification failed")

    # -- Hard Capability Guards (Sections 83-87, 156-162) --------------------

    @staticmethod
    def acquire_activity_writer(*args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError(
            "Section 83 violation: AG-1 cannot obtain an Activity writer capability"
        )

    @staticmethod
    def acquire_action_writer(*args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError(
            "Section 84 violation: AG-1 cannot obtain an Action Reality writer capability"
        )

    @staticmethod
    def acquire_world_writer(*args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError(
            "Section 85 violation: AG-1 cannot obtain a World writer capability"
        )

    @staticmethod
    def acquire_memory_writer(*args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError(
            "Section 86 violation: AG-1 cannot obtain a Memory writer capability"
        )

    @staticmethod
    def acquire_goal_writer(*args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError(
            "Section 87 violation: AG-1 cannot obtain a Goal writer capability"
        )


# ---------------------------------------------------------------------------
# Domain Models (Sections 7, 9, 18, 20, 24, 31, 40)
# ---------------------------------------------------------------------------


@dataclass
class DecisionOpportunity:
    """Sections 16-19, 113: Event-driven opportunity for Agency evaluation (`chiyo.agency.decision_opportunity.v1`)."""

    opportunity_id: str
    trigger_kind: str
    trigger_ref: str
    candidate_set_ref: str
    occurred_at: str
    observed_at: str
    recorded_at: str
    causal_parent_refs: list[str]
    idempotency_key: str
    candidate_snapshot_fingerprint: str = ""
    current_activity_ref: Optional[str] = None
    valid_until: Optional[str] = None
    status: str = OPPORTUNITY_STATUS_OPEN
    decision_ref: Optional[str] = None
    superseded_by_ref: Optional[str] = None
    subject_id: str = GLOBAL_SUBJECT_ID
    policy_version: str = POLICY_VERSION_AG1_V1
    schema_version: str = SCHEMA_DECISION_OPPORTUNITY

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_DECISION_OPPORTUNITY:
            raise AgencyDecisionError(f"invalid DecisionOpportunity schema_version={self.schema_version!r}")
        self.subject_id = validate_subject_id(self.subject_id)
        self.opportunity_id = _validate_structured_ref(
            self.opportunity_id,
            "opportunity_id",
            allowed_prefixes=frozenset({"dopp"}),
        )
        if self.trigger_kind in FORBIDDEN_OPPORTUNITY_TRIGGERS:
            raise ForbiddenPeriodicTickError(
                f"Section 17 violation: periodic tick trigger {self.trigger_kind!r} is permanently forbidden"
            )
        if self.trigger_kind not in VALID_OPPORTUNITY_TRIGGERS:
            raise AgencyDecisionError(
                f"unsupported trigger_kind={self.trigger_kind!r}; allowed={sorted(VALID_OPPORTUNITY_TRIGGERS)}"
            )
        self.trigger_ref = _validate_structured_ref(self.trigger_ref, "trigger_ref")
        self.candidate_set_ref = _validate_structured_ref(
            self.candidate_set_ref,
            "candidate_set_ref",
            allowed_prefixes=frozenset({"cset"}),
        )
        if self.current_activity_ref is not None:
            self.current_activity_ref = _validate_structured_ref(
                self.current_activity_ref,
                "current_activity_ref",
                allowed_prefixes=frozenset({"act", "actv", "activity"}),
            )
        self.occurred_at = _require_iso(self.occurred_at, "occurred_at")
        self.observed_at = _require_iso(self.observed_at, "observed_at")
        self.recorded_at = _require_iso(self.recorded_at, "recorded_at")
        self.valid_until = _optional_iso(self.valid_until, "valid_until")
        self.causal_parent_refs = _validate_ref_list(
            self.causal_parent_refs, "causal_parent_refs", allow_empty=False
        )
        if self.status not in VALID_OPPORTUNITY_STATUSES:
            raise AgencyDecisionError(f"invalid DecisionOpportunity status={self.status!r}")
        if self.decision_ref is not None:
            self.decision_ref = _validate_structured_ref(
                self.decision_ref, "decision_ref", allowed_prefixes=frozenset({"dec"})
            )
        if self.superseded_by_ref is not None:
            self.superseded_by_ref = _validate_structured_ref(
                self.superseded_by_ref, "superseded_by_ref", allowed_prefixes=frozenset({"dopp"})
            )
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise AgencyDecisionError("DecisionOpportunity.idempotency_key must be non-empty")
        self.idempotency_key = self.idempotency_key.strip()

    @classmethod
    def create(
        cls,
        *,
        trigger_kind: str,
        trigger_ref: str,
        candidate_set: CandidateSet | Mapping[str, Any],
        current_activity_ref: Optional[str] = None,
        occurred_at: Optional[str] = None,
        observed_at: Optional[str] = None,
        recorded_at: Optional[str] = None,
        causal_parent_refs: Optional[Sequence[str]] = None,
        valid_until: Optional[str] = None,
        policy_version: str = POLICY_VERSION_AG1_V1,
        subject_id: str = GLOBAL_SUBJECT_ID,
    ) -> "DecisionOpportunity":
        if trigger_kind in FORBIDDEN_OPPORTUNITY_TRIGGERS:
            raise ForbiddenPeriodicTickError(
                f"Section 17 violation: periodic LLM tick trigger {trigger_kind!r} is forbidden"
            )
        cset_dict = candidate_set.to_dict() if isinstance(candidate_set, CandidateSet) else dict(candidate_set)
        cset_id = str(cset_dict["candidate_set_id"])
        cand_refs = list(cset_dict.get("candidate_refs") or [])
        snap_refs = list(cset_dict.get("source_snapshot_refs") or [])
        cand_fp = f"cfp:{sha256_hex(json.dumps({'cset': cset_id, 'refs': cand_refs, 'snaps': snap_refs}, sort_keys=True))[:16]}"
        idem_key = f"idem_dopp:{sha256_hex(f'{trigger_ref}:{cand_fp}:{policy_version}')[:24]}"
        opp_id = f"dopp:{sha256_hex(f'{subject_id}:{trigger_kind}:{idem_key}')[:16]}"
        ts_now = _iso(now())
        occ = occurred_at or str(cset_dict.get("observed_at") or ts_now)
        obs = observed_at or str(cset_dict.get("observed_at") or occ)
        rec = recorded_at or ts_now
        parents = list(causal_parent_refs) if causal_parent_refs else [trigger_ref, cset_id]
        return cls(
            opportunity_id=opp_id,
            subject_id=subject_id,
            trigger_kind=trigger_kind,
            trigger_ref=trigger_ref,
            candidate_set_ref=cset_id,
            candidate_snapshot_fingerprint=cand_fp,
            current_activity_ref=current_activity_ref,
            occurred_at=occ,
            observed_at=obs,
            recorded_at=rec,
            valid_until=valid_until,
            causal_parent_refs=parents,
            policy_version=policy_version,
            idempotency_key=idem_key,
        )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionOpportunity":
        _reject_forbidden_dict_keys(data, "DecisionOpportunity")
        return cls(**dict(data))


@dataclass
class DecisionContextSnapshot:
    """Sections 20-21, 76, 97-98: Frozen authorized view of what Chiyo knows at evaluation time."""

    context_snapshot_id: str
    opportunity_ref: str
    candidate_set_ref: str
    life_frame_ref: str
    captured_at: str
    source_revisions: dict[str, Any]
    snapshot_fingerprint: str
    current_activity_ref: Optional[str] = None
    current_activity_status: Optional[str] = None
    current_activity_revision: Optional[int] = None
    interruptibility: str = INTERRUPTIBILITY_IMMEDIATE
    current_atomic_boundary_ref: Optional[str] = None
    known_waiting_basis_ref: Optional[str] = None
    body_capacity_ref: Optional[str] = None
    permission_refs: list[str] = field(default_factory=list)
    denied_permission_by_candidate: dict[str, str] = field(default_factory=dict)
    resource_refs: list[str] = field(default_factory=list)
    commitment_constraint_refs: list[str] = field(default_factory=list)
    hard_conflict_by_candidate: dict[str, dict[str, str]] = field(default_factory=dict)
    observed_world_refs: list[str] = field(default_factory=list)
    observed_facts: dict[str, Any] = field(default_factory=dict)
    candidate_revisions: dict[str, Any] = field(default_factory=dict)
    channel: Optional[str] = None
    subject_id: str = GLOBAL_SUBJECT_ID
    schema_version: str = SCHEMA_DECISION_CONTEXT_SNAPSHOT

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_DECISION_CONTEXT_SNAPSHOT:
            raise AgencyDecisionError(f"invalid DecisionContextSnapshot schema_version={self.schema_version!r}")
        self.subject_id = validate_subject_id(self.subject_id)
        self.context_snapshot_id = _validate_structured_ref(
            self.context_snapshot_id, "context_snapshot_id", allowed_prefixes=frozenset({"dctx"})
        )
        self.opportunity_ref = _validate_structured_ref(
            self.opportunity_ref, "opportunity_ref", allowed_prefixes=frozenset({"dopp"})
        )
        self.candidate_set_ref = _validate_structured_ref(
            self.candidate_set_ref, "candidate_set_ref", allowed_prefixes=frozenset({"cset"})
        )
        self.life_frame_ref = _validate_structured_ref(
            self.life_frame_ref, "life_frame_ref", allowed_prefixes=frozenset({"lframe", "lifeframe"})
        )
        if self.current_activity_ref is not None:
            self.current_activity_ref = _validate_structured_ref(
                self.current_activity_ref,
                "current_activity_ref",
                allowed_prefixes=frozenset({"act", "actv", "activity"}),
            )
        if self.current_atomic_boundary_ref is not None:
            self.current_atomic_boundary_ref = _validate_structured_ref(
                self.current_atomic_boundary_ref,
                "current_atomic_boundary_ref",
            )
        if self.known_waiting_basis_ref is not None:
            self.known_waiting_basis_ref = _validate_structured_ref(
                self.known_waiting_basis_ref,
                "known_waiting_basis_ref",
            )
        if self.body_capacity_ref is not None:
            self.body_capacity_ref = _validate_structured_ref(
                self.body_capacity_ref, "body_capacity_ref"
            )
        self.permission_refs = _validate_ref_list(self.permission_refs, "permission_refs", allow_empty=True)
        self.resource_refs = _validate_ref_list(self.resource_refs, "resource_refs", allow_empty=True)
        self.commitment_constraint_refs = _validate_ref_list(
            self.commitment_constraint_refs, "commitment_constraint_refs", allow_empty=True
        )
        self.observed_world_refs = _validate_ref_list(
            self.observed_world_refs, "observed_world_refs", allow_empty=True
        )
        for wref in self.observed_world_refs:
            if wref.startswith(("unobserved:", "hidden_world:", "raw_world_db:")):
                raise ForbiddenUnobservedContextError(
                    f"Section 21 violation: unobserved world reference {wref!r} cannot enter DecisionContextSnapshot"
                )
        self.captured_at = _require_iso(self.captured_at, "captured_at")
        self.snapshot_fingerprint = _validate_structured_ref(
            self.snapshot_fingerprint, "snapshot_fingerprint", allowed_prefixes=frozenset({"sfp"})
        )

    @classmethod
    def build(
        cls,
        *,
        opportunity: DecisionOpportunity,
        candidate_set: CandidateSet,
        candidates: Mapping[str, CandidateRecord],
        activity_read_service: Optional[ActivityReadService] = None,
        life_frame: Optional[Mapping[str, Any]] = None,
        body_capacity_ref: Optional[str] = None,
        permission_refs: Optional[Sequence[str]] = None,
        denied_permission_by_candidate: Optional[Mapping[str, str]] = None,
        resource_refs: Optional[Sequence[str]] = None,
        commitment_constraint_refs: Optional[Sequence[str]] = None,
        hard_conflict_by_candidate: Optional[Mapping[str, Mapping[str, str]]] = None,
        observed_world_refs: Optional[Sequence[str]] = None,
        observed_facts: Optional[Mapping[str, Any]] = None,
        current_atomic_boundary_ref: Optional[str] = None,
        known_waiting_basis_ref: Optional[str] = None,
        channel: Optional[str] = None,
        unobserved_world_refs: Optional[Sequence[str]] = None,
        unauthorized_memory_refs: Optional[Sequence[str]] = None,
        captured_at: Optional[str] = None,
    ) -> "DecisionContextSnapshot":
        if unobserved_world_refs:
            raise ForbiddenUnobservedContextError(
                "Section 21 violation: Agency cannot read unobserved World DB facts ('World knows X != Chiyo knows X')"
            )
        if unauthorized_memory_refs:
            raise ForbiddenUnobservedContextError(
                "Section 53 violation: Agency cannot read unauthorized Memory or secrets"
            )

        cap_iso = _require_iso(captured_at or opportunity.observed_at, "captured_at")
        lf: dict[str, Any] = {}
        if life_frame is not None:
            lf = dict(life_frame)
        elif activity_read_service is not None:
            lf = activity_read_service.get_current_life_view()

        cur_act_ref = (
            lf.get("foreground_activity_ref")
            or lf.get("current_activity_ref")
            or opportunity.current_activity_ref
        )
        cur_act_status = lf.get("foreground_status") or lf.get("life_state")
        if cur_act_status == "IDLE":
            cur_act_status = None
        cur_act_rev = lf.get("activity_revision")
        if activity_read_service is not None:
            store_obj = getattr(activity_read_service, "store", None) or getattr(
                activity_read_service, "_store", None
            )
            if store_obj is not None:
                st, _ = store_obj.load_verified_state_and_journal(_include_journal=False)
                if cur_act_rev is None:
                    cur_act_rev = int(st.get("revision", 0))
                if cur_act_ref and cur_act_ref in (st.get("activities") or {}):
                    act_rec = st["activities"][cur_act_ref]
                    cur_act_status = act_rec.get("status")
                    if not lf.get("interruptibility"):
                        lf["interruptibility"] = (
                            act_rec.get("interrupt_mode")
                            or act_rec.get("interruptibility")
                        )

        interruptibility = str(lf.get("interruptibility") or INTERRUPTIBILITY_IMMEDIATE)
        if interruptibility == INTERRUPTIBILITY_ATOMIC and not current_atomic_boundary_ref:
            current_atomic_boundary_ref = f"atomic_bound:{cur_act_ref or 'current'}:r{cur_act_rev or 0}"

        lframe_ref = str(
            lf.get("life_frame_id")
            or f"lframe:{sha256_hex(f'{cur_act_ref}:{cur_act_status}:{cur_act_rev}:{interruptibility}')[:16]}"
        )

        cand_revs: dict[str, Any] = {}
        for cid in candidate_set.candidate_refs:
            c = candidates.get(cid)
            if c is not None:
                cand_revs[cid] = {
                    "revision": c.revision,
                    "source_revision": c.source_revision,
                    "availability_status": c.availability_status,
                }

        src_revs: dict[str, Any] = {
            "candidate_set_id": candidate_set.candidate_set_id,
            "source_snapshot_refs": list(candidate_set.source_snapshot_refs),
            "activity_revision": cur_act_rev,
            "current_activity_ref": cur_act_ref,
            "current_activity_status": cur_act_status,
            "interruptibility": interruptibility,
            "candidate_revisions": cand_revs,
        }

        denied_map = {str(k): str(v) for k, v in (denied_permission_by_candidate or {}).items()}
        conflict_map = {
            str(k): {str(ck): str(cv) for ck, cv in dict(v).items()}
            for k, v in (hard_conflict_by_candidate or {}).items()
        }

        # Section 97-98: Fingerprint is channel-neutral so Telegram/Desktop/Web produce identical semantic context!
        fp_payload = {
            "subject_id": GLOBAL_SUBJECT_ID,
            "candidate_set_ref": candidate_set.candidate_set_id,
            "source_revisions": src_revs,
            "denied_permission_by_candidate": denied_map,
            "hard_conflict_by_candidate": conflict_map,
            "permission_refs": sorted(permission_refs or []),
            "resource_refs": sorted(resource_refs or []),
            "commitment_constraint_refs": sorted(commitment_constraint_refs or []),
            "observed_world_refs": sorted(observed_world_refs or []),
            "known_waiting_basis_ref": known_waiting_basis_ref,
        }
        sfp = f"sfp:{sha256_hex(canonical_json_line(fp_payload))[:32]}"
        ctx_id = f"dctx:{sha256_hex(f'{opportunity.opportunity_id}:{sfp}')[:16]}"

        return cls(
            context_snapshot_id=ctx_id,
            subject_id=GLOBAL_SUBJECT_ID,
            opportunity_ref=opportunity.opportunity_id,
            candidate_set_ref=candidate_set.candidate_set_id,
            life_frame_ref=lframe_ref,
            current_activity_ref=cur_act_ref,
            current_activity_status=cur_act_status,
            current_activity_revision=cur_act_rev,
            interruptibility=interruptibility,
            current_atomic_boundary_ref=current_atomic_boundary_ref,
            known_waiting_basis_ref=known_waiting_basis_ref,
            body_capacity_ref=body_capacity_ref,
            permission_refs=list(permission_refs or []),
            denied_permission_by_candidate=denied_map,
            resource_refs=list(resource_refs or []),
            commitment_constraint_refs=list(commitment_constraint_refs or []),
            hard_conflict_by_candidate=conflict_map,
            observed_world_refs=list(observed_world_refs or []),
            observed_facts=copy.deepcopy(dict(observed_facts or {})),
            candidate_revisions=cand_revs,
            channel=channel,
            captured_at=cap_iso,
            source_revisions=src_revs,
            snapshot_fingerprint=sfp,
        )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionContextSnapshot":
        _reject_forbidden_dict_keys(data, "DecisionContextSnapshot")
        return cls(**dict(data))


@dataclass
class CandidateConstraintResult:
    """Sections 23-27: Deterministic Layer-1 Hard Constraint evaluation result (`0 LLM`)."""

    constraint_result_id: str
    candidate_ref: str
    status: str
    reason_codes: list[str]
    constraint_refs: list[str]
    evaluated_at: str
    policy_version: str = POLICY_VERSION_AG1_V1
    schema_version: str = SCHEMA_CANDIDATE_CONSTRAINT_RESULT

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_CANDIDATE_CONSTRAINT_RESULT:
            raise AgencyDecisionError(f"invalid CandidateConstraintResult schema_version={self.schema_version!r}")
        self.constraint_result_id = _validate_structured_ref(
            self.constraint_result_id, "constraint_result_id", allowed_prefixes=frozenset({"hcres"})
        )
        self.candidate_ref = _validate_structured_ref(
            self.candidate_ref, "candidate_ref", allowed_prefixes=frozenset({"cand"})
        )
        if self.status not in {CONSTRAINT_STATUS_ALLOWED, CONSTRAINT_STATUS_REJECTED}:
            raise AgencyDecisionError(f"invalid constraint status={self.status!r}")
        self.evaluated_at = _require_iso(self.evaluated_at, "evaluated_at")
        if self.status == CONSTRAINT_STATUS_REJECTED:
            if not self.reason_codes:
                raise MissingConstraintAuthorityError("REJECTED CandidateConstraintResult must include reason_codes")
            for rc in self.reason_codes:
                if rc not in VALID_HARD_REJECT_CODES:
                    raise MissingConstraintAuthorityError(
                        f"Section 25 violation: subjective or unrecognized hard reject code {rc!r} is forbidden"
                    )
            self.constraint_refs = _validate_ref_list(
                self.constraint_refs, "constraint_refs", allow_empty=False
            )
        else:
            self.constraint_refs = _validate_ref_list(
                self.constraint_refs, "constraint_refs", allow_empty=True
            )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateConstraintResult":
        _reject_forbidden_dict_keys(data, "CandidateConstraintResult")
        return cls(**dict(data))


@dataclass
class AgencyCapacitySnapshot:
    """Sections 28-37: Layer-2 Capacity evaluation snapshot separating Motive from Capacity."""

    capacity_snapshot_id: str
    captured_at: str
    source_revisions: list[str]
    body_capacity_ref: Optional[str] = None
    body_capacity_state: str = CAPACITY_UNKNOWN
    atomic_state_ref: Optional[str] = None
    interruptibility: str = INTERRUPTIBILITY_IMMEDIATE
    device_access_ref: Optional[str] = None
    device_availability_state: str = CAPACITY_UNKNOWN
    resource_budget_ref: Optional[str] = None
    resource_availability_state: str = CAPACITY_UNKNOWN
    privacy_availability_ref: Optional[str] = None
    privacy_availability_state: str = CAPACITY_UNKNOWN
    permission_ref: Optional[str] = None
    runtime_availability_ref: Optional[str] = None
    runtime_load_state: str = CAPACITY_AVAILABLE
    overall_capacity_state: str = CAPACITY_AVAILABLE
    candidate_executability: dict[str, dict[str, Any]] = field(default_factory=dict)
    subject_id: str = GLOBAL_SUBJECT_ID
    schema_version: str = SCHEMA_CAPACITY_SNAPSHOT

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_CAPACITY_SNAPSHOT:
            raise AgencyDecisionError(f"invalid AgencyCapacitySnapshot schema_version={self.schema_version!r}")
        self.subject_id = validate_subject_id(self.subject_id)
        self.capacity_snapshot_id = _validate_structured_ref(
            self.capacity_snapshot_id, "capacity_snapshot_id", allowed_prefixes=frozenset({"cap"})
        )
        for field_name, val in (
            ("body_capacity_state", self.body_capacity_state),
            ("device_availability_state", self.device_availability_state),
            ("resource_availability_state", self.resource_availability_state),
            ("privacy_availability_state", self.privacy_availability_state),
            ("runtime_load_state", self.runtime_load_state),
            ("overall_capacity_state", self.overall_capacity_state),
        ):
            if val not in VALID_CAPACITY_STATES:
                raise AgencyDecisionError(f"invalid {field_name}={val!r}; must be in {sorted(VALID_CAPACITY_STATES)}")
        self.captured_at = _require_iso(self.captured_at, "captured_at")
        self.source_revisions = _validate_ref_list(self.source_revisions, "source_revisions", allow_empty=True)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgencyCapacitySnapshot":
        _reject_forbidden_dict_keys(data, "AgencyCapacitySnapshot")
        return cls(**dict(data))


@dataclass
class AgencyAccessibilityView:
    """Sections 40-46: Multi-dimensional structured accessibility view without any universal score."""

    accessibility_id: str
    opportunity_ref: str
    candidate_entries: dict[str, dict[str, Any]]
    created_at: str
    subject_id: str = GLOBAL_SUBJECT_ID
    schema_version: str = SCHEMA_ACCESSIBILITY_VIEW

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_ACCESSIBILITY_VIEW:
            raise AgencyDecisionError(f"invalid AgencyAccessibilityView schema_version={self.schema_version!r}")
        self.subject_id = validate_subject_id(self.subject_id)
        self.accessibility_id = _validate_structured_ref(
            self.accessibility_id, "accessibility_id", allowed_prefixes=frozenset({"acc"})
        )
        self.opportunity_ref = _validate_structured_ref(
            self.opportunity_ref, "opportunity_ref", allowed_prefixes=frozenset({"dopp"})
        )
        self.created_at = _require_iso(self.created_at, "created_at")
        for cid, entry in self.candidate_entries.items():
            if not isinstance(entry, Mapping):
                raise AgencyDecisionError(f"accessibility entry for {cid!r} must be a mapping")
            _reject_forbidden_dict_keys(entry, f"AgencyAccessibilityView[{cid}]")

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgencyAccessibilityView":
        _reject_forbidden_dict_keys(data, "AgencyAccessibilityView")
        return cls(**dict(data))


@dataclass
class DecisionAttempt:
    """Section 7-8: Auditable record of a decision evaluation attempt (`chiyo.agency.decision_attempt.v1`)."""

    attempt_id: str
    decision_opportunity_id: str
    candidate_set_ref: str
    context_snapshot_ref: str
    policy_version: str
    route: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    model_run_ref: Optional[str] = None
    failure_class: Optional[str] = None
    failure_ref: Optional[str] = None
    retry_count: int = 0
    schema_version: str = SCHEMA_DECISION_ATTEMPT

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_DECISION_ATTEMPT:
            raise AgencyDecisionError(f"invalid DecisionAttempt schema_version={self.schema_version!r}")
        self.attempt_id = _validate_structured_ref(
            self.attempt_id, "attempt_id", allowed_prefixes=frozenset({"datt"})
        )
        self.decision_opportunity_id = _validate_structured_ref(
            self.decision_opportunity_id, "decision_opportunity_id", allowed_prefixes=frozenset({"dopp"})
        )
        self.candidate_set_ref = _validate_structured_ref(
            self.candidate_set_ref, "candidate_set_ref", allowed_prefixes=frozenset({"cset"})
        )
        self.context_snapshot_ref = _validate_structured_ref(
            self.context_snapshot_ref, "context_snapshot_ref", allowed_prefixes=frozenset({"dctx"})
        )
        if self.route not in VALID_DECISION_ROUTES:
            raise AgencyDecisionError(f"invalid DecisionAttempt route={self.route!r}")
        if self.status not in VALID_ATTEMPT_STATUSES:
            raise AgencyDecisionError(f"invalid DecisionAttempt status={self.status!r}")
        self.started_at = _require_iso(self.started_at, "started_at")
        self.completed_at = _optional_iso(self.completed_at, "completed_at")
        if self.model_run_ref is not None:
            self.model_run_ref = _validate_structured_ref(
                self.model_run_ref, "model_run_ref", allowed_prefixes=frozenset({"mrun", "model_run"})
            )
        if self.failure_ref is not None:
            self.failure_ref = _validate_structured_ref(
                self.failure_ref, "failure_ref", allowed_prefixes=frozenset({"dfail", "err"})
            )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionAttempt":
        _reject_forbidden_dict_keys(data, "DecisionAttempt")
        return cls(**dict(data))


@dataclass(frozen=True)
class DecisionRecord:
    """Sections 8-12, 78-82: Immutable pre-adoption Agency Decision record (`chiyo.agency.decision_record.v1`).

    Once created and committed, a DecisionRecord is strictly immutable.
    Future reality changes create a new DecisionOpportunity (or superseding DecisionRecord), never mutating this record.
    """

    decision_id: str
    revision: int
    subject_id: str
    decision_opportunity_ref: str
    candidate_set_ref: str
    candidate_refs: tuple[str, ...]
    context_snapshot_ref: str
    hard_constraint_result_refs: tuple[str, ...]
    decision: str
    reason_codes: tuple[str, ...]
    decision_route: str
    policy_version: str
    snapshot_fingerprint: str
    created_at: str
    current_activity_ref: Optional[str] = None
    capacity_snapshot_ref: Optional[str] = None
    accessibility_ref: Optional[str] = None
    selected_candidate_ref: Optional[str] = None
    target_activity_ref: Optional[str] = None
    deferred_candidate_refs: tuple[str, ...] = ()
    reevaluation_hint_ref: Optional[str] = None
    #: v0.4 stage A: her own "later" as the Decision itself bounded it -- one proposal (already
    #: validated by ``life_future_items``) and at most a few actions on items she already has.
    #: Both are *records of what she said*, never a write: the cycle performs the store write and
    #: reports its own result.  Absent is ``None`` / ``()``, never an empty invented intent.
    later_item_proposal: Optional[dict[str, Any]] = None
    future_item_actions: tuple[dict[str, Any], ...] = ()
    #: v0.4 C: the *one* of her own practices this round chose to follow (a bounded handle the
    #: session offered), and -- optionally -- her own bounded verdict on whether it helped.
    #: Neither is authority: a method is document context, and a trial is an evaluation that only
    #: B's practice workspace may record, against evidence A can still read.
    method_ref: Optional[str] = None
    method_trial: Optional[dict[str, Any]] = None
    #: v0.4 C: at most one bounded practice proposal ("一件我想试的做法", with its scope and its
    #: unknown).  It is a *proposal*: the cycle asks B's practice workspace to save it as a
    #: TENTATIVE draft after the Decision commits, and a refusal is reported as a refusal.
    method_proposal: Optional[dict[str, Any]] = None
    #: v0.4 sharing: at most one bounded life-side sharing request (prepare / defer / send_local /
    #: drop).  It is a *record of what she asked for*, never a send: the cycle calls B's
    #: ``LifeSharingService`` after the Decision commits, and only the owner's own answer (with its
    #: local receipt) is written back.  ``audience``/``channel`` are absent by construction.
    sharing_action: Optional[dict[str, Any]] = None
    short_rationale: Optional[str] = None
    invalid_rationale_claim_dropped: bool = False
    model_run_ref: Optional[str] = None
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    attempt_ref: Optional[str] = None
    supersedes_ref: Optional[str] = None
    exploration_seed: Optional[int] = None
    schema_version: str = SCHEMA_DECISION_RECORD

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_DECISION_RECORD:
            raise AgencyDecisionError(f"invalid DecisionRecord schema_version={self.schema_version!r}")
        object.__setattr__(self, "subject_id", validate_subject_id(self.subject_id))
        object.__setattr__(
            self,
            "decision_id",
            _validate_structured_ref(self.decision_id, "decision_id", allowed_prefixes=frozenset({"dec"})),
        )
        if not isinstance(self.revision, int) or self.revision < 1:
            raise AgencyDecisionError("DecisionRecord.revision must be int >= 1")
        object.__setattr__(
            self,
            "decision_opportunity_ref",
            _validate_structured_ref(
                self.decision_opportunity_ref, "decision_opportunity_ref", allowed_prefixes=frozenset({"dopp"})
            ),
        )
        object.__setattr__(
            self,
            "candidate_set_ref",
            _validate_structured_ref(self.candidate_set_ref, "candidate_set_ref", allowed_prefixes=frozenset({"cset"})),
        )
        object.__setattr__(
            self,
            "candidate_refs",
            tuple(_validate_ref_list(self.candidate_refs, "candidate_refs", allow_empty=True)),
        )
        object.__setattr__(
            self,
            "context_snapshot_ref",
            _validate_structured_ref(
                self.context_snapshot_ref, "context_snapshot_ref", allowed_prefixes=frozenset({"dctx"})
            ),
        )
        object.__setattr__(
            self,
            "hard_constraint_result_refs",
            tuple(_validate_ref_list(self.hard_constraint_result_refs, "hard_constraint_result_refs", allow_empty=True)),
        )
        if self.decision in FORBIDDEN_DECISION_OUTPUTS:
            raise DecisionValidationError(
                f"Section 3 violation: decision={self.decision!r} is forbidden in AG-1",
                failure_class=FAILURE_CLASS_ILLEGAL_DECISION_ENUM,
            )
        if self.decision not in VALID_DECISION_OUTPUTS:
            raise DecisionValidationError(
                f"invalid decision={self.decision!r}; allowed={sorted(VALID_DECISION_OUTPUTS)}",
                failure_class=FAILURE_CLASS_ILLEGAL_DECISION_ENUM,
            )
        if self.decision_route not in {ROUTE_RULE_RESOLVED, ROUTE_COGNITIVE_REQUIRED}:
            raise AgencyDecisionError(f"invalid DecisionRecord decision_route={self.decision_route!r}")
        norm_reasons = DecisionReasonCodeRegistry.validate_codes(self.reason_codes, allow_empty=False)
        object.__setattr__(self, "reason_codes", tuple(norm_reasons))
        object.__setattr__(
            self,
            "snapshot_fingerprint",
            _validate_structured_ref(
                self.snapshot_fingerprint, "snapshot_fingerprint", allowed_prefixes=frozenset({"sfp"})
            ),
        )
        object.__setattr__(self, "created_at", _require_iso(self.created_at, "created_at"))

        if self.current_activity_ref is not None:
            object.__setattr__(
                self,
                "current_activity_ref",
                _validate_structured_ref(
                    self.current_activity_ref,
                    "current_activity_ref",
                    allowed_prefixes=frozenset({"act", "actv", "activity"}),
                ),
            )
        if self.capacity_snapshot_ref is not None:
            object.__setattr__(
                self,
                "capacity_snapshot_ref",
                _validate_structured_ref(
                    self.capacity_snapshot_ref, "capacity_snapshot_ref", allowed_prefixes=frozenset({"cap"})
                ),
            )
        if self.accessibility_ref is not None:
            object.__setattr__(
                self,
                "accessibility_ref",
                _validate_structured_ref(
                    self.accessibility_ref, "accessibility_ref", allowed_prefixes=frozenset({"acc"})
                ),
            )
        if self.selected_candidate_ref is not None:
            object.__setattr__(
                self,
                "selected_candidate_ref",
                _validate_structured_ref(
                    self.selected_candidate_ref, "selected_candidate_ref", allowed_prefixes=frozenset({"cand"})
                ),
            )
        if self.target_activity_ref is not None:
            object.__setattr__(
                self,
                "target_activity_ref",
                _validate_structured_ref(
                    self.target_activity_ref,
                    "target_activity_ref",
                    allowed_prefixes=frozenset({"act", "actv", "activity"}),
                ),
            )
        object.__setattr__(
            self,
            "deferred_candidate_refs",
            tuple(_validate_ref_list(self.deferred_candidate_refs, "deferred_candidate_refs", allow_empty=True)),
        )
        if self.reevaluation_hint_ref is not None:
            object.__setattr__(
                self,
                "reevaluation_hint_ref",
                _validate_structured_ref(self.reevaluation_hint_ref, "reevaluation_hint_ref"),
            )
        # v0.4 stage A: the two "later" projections are re-validated through their own domain, so
        # a record rebuilt from disk can never carry a shape the validator would refuse.
        if self.later_item_proposal is not None:
            try:
                object.__setattr__(self, "later_item_proposal",
                                   life_future_items.validate_proposal(self.later_item_proposal))
            except life_future_items.FutureItemError as exc:
                raise DecisionValidationError(
                    f"later_item_proposal is not a valid future-item proposal: {exc}",
                    failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                ) from exc
        if self.future_item_actions is None:
            object.__setattr__(self, "future_item_actions", ())
        else:
            try:
                object.__setattr__(
                    self, "future_item_actions",
                    tuple(life_future_items.validate_actions(list(self.future_item_actions))))
            except life_future_items.FutureItemError as exc:
                raise DecisionValidationError(
                    f"future_item_actions is not a valid action list: {exc}",
                    failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                ) from exc
        # v0.4 C: the chosen practice handle and her own bounded verdict are re-validated on every
        # rebuild, so a record read back from disk can never carry a shape the validator refuses.
        if self.method_ref is not None:
            text = self.method_ref.strip() if isinstance(self.method_ref, str) else ""
            if not text or not text.startswith(METHOD_REF_PREFIX) or ":" in text[len(METHOD_REF_PREFIX):]:
                raise DecisionValidationError(
                    f"method_ref={self.method_ref!r} is not a bounded practice handle",
                    failure_class=FAILURE_CLASS_INVENTED_METHOD_REF,
                )
            object.__setattr__(self, "method_ref", text)
        if self.method_trial is not None:
            object.__setattr__(self, "method_trial",
                               validate_method_trial(self.method_trial,
                                                     method_ref=self.method_ref))
        if self.method_proposal is not None:
            object.__setattr__(self, "method_proposal",
                               validate_method_proposal(self.method_proposal))
        if self.sharing_action is not None:
            object.__setattr__(self, "sharing_action",
                               validate_sharing_action(self.sharing_action))
        if self.model_run_ref is not None:
            object.__setattr__(
                self,
                "model_run_ref",
                _validate_structured_ref(
                    self.model_run_ref, "model_run_ref", allowed_prefixes=frozenset({"mrun", "model_run"})
                ),
            )
        if self.attempt_ref is not None:
            object.__setattr__(
                self,
                "attempt_ref",
                _validate_structured_ref(self.attempt_ref, "attempt_ref", allowed_prefixes=frozenset({"datt"})),
            )
        if self.supersedes_ref is not None:
            object.__setattr__(
                self,
                "supersedes_ref",
                _validate_structured_ref(self.supersedes_ref, "supersedes_ref", allowed_prefixes=frozenset({"dec"})),
            )

        # Section 72: NO_ACTION requires selected_candidate_ref = None
        if self.decision == DECISION_NO_ACTION and self.selected_candidate_ref is not None:
            raise DecisionValidationError(
                "Section 72 violation: NO_ACTION must have selected_candidate_ref = None (no fake idle candidate)",
                failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
            )

    # -- Section 79: Immutability & Forbidden Adoption Guards ----------------

    def mutate(self, *args: Any, **kwargs: Any) -> None:
        raise DecisionImmutabilityError("Section 79 violation: DecisionRecord is immutable once created")

    def apply_to_activity(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError(
            "Sections 2 & 81 violation: DecisionRecord in AG-1 cannot directly mutate Activity (belongs to AG-2)"
        )

    def create_action_proposal(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError(
            "Sections 2 & 84 violation: DecisionRecord in AG-1 cannot create ActionProposal or ActionRecord"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "revision": self.revision,
            "subject_id": self.subject_id,
            "decision_opportunity_ref": self.decision_opportunity_ref,
            "candidate_set_ref": self.candidate_set_ref,
            "candidate_refs": list(self.candidate_refs),
            "current_activity_ref": self.current_activity_ref,
            "context_snapshot_ref": self.context_snapshot_ref,
            "hard_constraint_result_refs": list(self.hard_constraint_result_refs),
            "capacity_snapshot_ref": self.capacity_snapshot_ref,
            "accessibility_ref": self.accessibility_ref,
            "selected_candidate_ref": self.selected_candidate_ref,
            "decision": self.decision,
            "target_activity_ref": self.target_activity_ref,
            "deferred_candidate_refs": list(self.deferred_candidate_refs),
            "reevaluation_hint_ref": self.reevaluation_hint_ref,
            "reason_codes": list(self.reason_codes),
            "short_rationale": self.short_rationale,
            "invalid_rationale_claim_dropped": self.invalid_rationale_claim_dropped,
            "decision_route": self.decision_route,
            "model_run_ref": self.model_run_ref,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "attempt_ref": self.attempt_ref,
            "policy_version": self.policy_version,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "supersedes_ref": self.supersedes_ref,
            "exploration_seed": self.exploration_seed,
            # v0.4 stage A: what she said she would do later, and what she decided about items she
            # already had.  Recorded with the Decision so a rebuilt record keeps the same intent.
            "later_item_proposal": copy.deepcopy(self.later_item_proposal),
            "future_item_actions": [copy.deepcopy(row) for row in self.future_item_actions],
            # v0.4 C: the practice handle she followed this round and her own bounded verdict on
            # it (if she gave one).  Recorded with the Decision so a rebuilt record keeps intent.
            "method_ref": self.method_ref,
            "method_trial": copy.deepcopy(self.method_trial),
            "method_proposal": copy.deepcopy(self.method_proposal),
            # v0.4 sharing: the one sharing request this Decision made, if any.  It is the
            # *request*, never the delivery: the delivery fact lives in B's owner and is read back.
            "sharing_action": copy.deepcopy(self.sharing_action),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionRecord":
        if not isinstance(data, Mapping):
            raise AgencyDecisionError("DecisionRecord input must be a mapping")
        _reject_forbidden_dict_keys(data, "DecisionRecord")
        d = dict(data)
        d["candidate_refs"] = tuple(d.get("candidate_refs") or ())
        d["hard_constraint_result_refs"] = tuple(d.get("hard_constraint_result_refs") or ())
        d["reason_codes"] = tuple(d.get("reason_codes") or ())
        d["future_item_actions"] = tuple(d.get("future_item_actions") or ())
        d["deferred_candidate_refs"] = tuple(d.get("deferred_candidate_refs") or ())
        return cls(**d)


# ---------------------------------------------------------------------------
# Layer 1: HardConstraintEvaluator (Sections 23-27, 38-39, 122 B01-B05)
# ---------------------------------------------------------------------------


class HardConstraintEvaluator:
    """Sections 23-27, 38-39: Deterministic 0-LLM Hard Constraint filter.

    Checks:
      1. Permission denied (`HARD_REJECT:PERMISSION_DENIED`)
      2. Stale / non-open candidate or stale source revision / consumed resume eligibility (`HARD_REJECT:STALE_SOURCE`)
      3. Invalid target (e.g. terminal target Activity or invalid target ref) (`HARD_REJECT:TARGET_INVALID`)
      4. Closed time window (`observed_at < valid_from` or `observed_at > valid_until`) (`HARD_REJECT:TIME_WINDOW_CLOSED`)
      5. Physically impossible (`HARD_REJECT:PHYSICALLY_IMPOSSIBLE`)
      6. Policy forbidden (`HARD_REJECT:POLICY_FORBIDDEN`)
      7. Hard commitment conflict (`HARD_REJECT:HARD_COMMITMENT_CONFLICT`)
      8. Irreversible conflict (`HARD_REJECT:IRREVERSIBLE_CONFLICT`)

    Every rejection is bound to at least one structured `constraint_ref`.
    """

    @staticmethod
    def evaluate_candidates(
        *,
        candidates: Sequence[CandidateRecord],
        context_snapshot: DecisionContextSnapshot,
        candidate_read_service: Optional[CandidateReadService] = None,
        activity_read_service: Optional[ActivityReadService] = None,
        resume_eligibility_store: Optional[ResumeEligibilityStore] = None,
        invalid_target_refs: Optional[Iterable[str]] = None,
        evaluated_at: Optional[str] = None,
        policy_version: str = POLICY_VERSION_AG1_V1,
    ) -> tuple[list[CandidateConstraintResult], list[CandidateRecord]]:
        eval_iso = _require_iso(evaluated_at or context_snapshot.captured_at, "evaluated_at")
        eval_dt = _parse_iso(eval_iso)
        bad_targets = set(invalid_target_refs or ())

        live_activities: dict[str, Any] = {}
        if activity_read_service is not None:
            store_obj = getattr(activity_read_service, "store", None) or getattr(
                activity_read_service, "_store", None
            )
            if store_obj is not None:
                st, _ = store_obj.load_verified_state_and_journal(_include_journal=False)
                live_activities = st.get("activities") or {}

        live_eligibilities: dict[str, Any] = {}
        if resume_eligibility_store is not None:
            est = resume_eligibility_store.load_state()
            live_eligibilities = est.get("eligibilities") or {}

        results: list[CandidateConstraintResult] = []
        allowed_candidates: list[CandidateRecord] = []

        for cand in candidates:
            cid = cand.candidate_id
            reject_codes: list[str] = []
            constraint_refs: list[str] = []

            # 1. Permission Denied check (Section 23, 38, B01)
            if cid in context_snapshot.denied_permission_by_candidate:
                perm_ref = context_snapshot.denied_permission_by_candidate[cid]
                reject_codes.append(HARD_REJECT_PERMISSION_DENIED)
                constraint_refs.append(perm_ref)

            # 2. Explicit Hard Conflicts from Context (physically impossible, policy, commitment, irreversible)
            explicit_conflicts = context_snapshot.hard_conflict_by_candidate.get(cid) or {}
            for conflict_kind, cref in explicit_conflicts.items():
                ck = conflict_kind.upper()
                if ck in {"PERMISSION_DENIED", HARD_REJECT_PERMISSION_DENIED}:
                    if HARD_REJECT_PERMISSION_DENIED not in reject_codes:
                        reject_codes.append(HARD_REJECT_PERMISSION_DENIED)
                elif ck in {"PHYSICALLY_IMPOSSIBLE", HARD_REJECT_PHYSICALLY_IMPOSSIBLE}:
                    reject_codes.append(HARD_REJECT_PHYSICALLY_IMPOSSIBLE)
                elif ck in {"POLICY_FORBIDDEN", HARD_REJECT_POLICY_FORBIDDEN}:
                    reject_codes.append(HARD_REJECT_POLICY_FORBIDDEN)
                elif ck in {"HARD_COMMITMENT_CONFLICT", "COMMITMENT_CONFLICT", HARD_REJECT_COMMITMENT_CONFLICT}:
                    reject_codes.append(HARD_REJECT_COMMITMENT_CONFLICT)
                elif ck in {"IRREVERSIBLE_CONFLICT", HARD_REJECT_IRREVERSIBLE_CONFLICT}:
                    reject_codes.append(HARD_REJECT_IRREVERSIBLE_CONFLICT)
                elif ck in {"TARGET_INVALID", HARD_REJECT_TARGET_INVALID}:
                    reject_codes.append(HARD_REJECT_TARGET_INVALID)
                elif ck in {"TIME_WINDOW_CLOSED", HARD_REJECT_TIME_WINDOW_CLOSED}:
                    reject_codes.append(HARD_REJECT_TIME_WINDOW_CLOSED)
                elif ck in {"STALE_SOURCE", HARD_REJECT_STALE_SOURCE}:
                    reject_codes.append(HARD_REJECT_STALE_SOURCE)
                else:
                    raise MissingConstraintAuthorityError(
                        f"Section 25 violation: unrecognized hard constraint kind {conflict_kind!r}"
                    )
                constraint_refs.append(cref)

            # 3. Time Window Check (Section 23, 26, B04)
            if cand.valid_from and eval_dt < _parse_iso(cand.valid_from):
                if HARD_REJECT_TIME_WINDOW_CLOSED not in reject_codes:
                    reject_codes.append(HARD_REJECT_TIME_WINDOW_CLOSED)
                constraint_refs.append(f"win_not_open:{cid}")
            if cand.valid_until and eval_dt > _parse_iso(cand.valid_until):
                if HARD_REJECT_TIME_WINDOW_CLOSED not in reject_codes:
                    reject_codes.append(HARD_REJECT_TIME_WINDOW_CLOSED)
                constraint_refs.append(f"win_closed:{cid}")

            # 4. Candidate Stale Re-verification against AG-0 & Canonical Owners (Sections 26-27, B02, K04, L03)
            if cand.availability_status != CANDIDATE_STATUS_OPEN:
                if HARD_REJECT_STALE_SOURCE not in reject_codes:
                    reject_codes.append(HARD_REJECT_STALE_SOURCE)
                constraint_refs.append(f"cval_status:{cid}:{cand.availability_status}")
            elif candidate_read_service is not None and candidate_read_service.get_candidate(cid) is not None:
                v_info = candidate_read_service.validate_candidate(cid, observed_at=eval_iso)
                if not v_info.get("valid", False):
                    rc = str(v_info.get("reason_code") or "STALE")
                    if "EXPIRED" in rc or v_info.get("availability_status") == "EXPIRED":
                        if HARD_REJECT_TIME_WINDOW_CLOSED not in reject_codes:
                            reject_codes.append(HARD_REJECT_TIME_WINDOW_CLOSED)
                        constraint_refs.append(f"win_expired:{cid}")
                    else:
                        if HARD_REJECT_STALE_SOURCE not in reject_codes:
                            reject_codes.append(HARD_REJECT_STALE_SOURCE)
                        constraint_refs.append(f"cval_stale:{cid}:{rc}")

            # Check live Activity state for CURRENT_ACTIVITY and RESUME_ELIGIBLE
            if cand.source_kind == SOURCE_CURRENT_ACTIVITY and cand.activity_ref:
                act_obj = live_activities.get(cand.activity_ref)
                if act_obj is not None:
                    act_status = act_obj.get("status")
                    act_rev = act_obj.get("revision")
                    if act_status in TERMINAL_ACTIVITY_STATUSES:
                        if HARD_REJECT_STALE_SOURCE not in reject_codes:
                            reject_codes.append(HARD_REJECT_STALE_SOURCE)
                        if HARD_REJECT_TARGET_INVALID not in reject_codes:
                            reject_codes.append(HARD_REJECT_TARGET_INVALID)
                        constraint_refs.append(f"act_terminal:{cand.activity_ref}:{act_status}")
                    elif act_status != STATUS_ACTIVE:
                        if HARD_REJECT_STALE_SOURCE not in reject_codes:
                            reject_codes.append(HARD_REJECT_STALE_SOURCE)
                        constraint_refs.append(f"act_not_active:{cand.activity_ref}:{act_status}")
                    elif cand.source_revision is not None and int(act_rev) != int(cand.source_revision):
                        if HARD_REJECT_STALE_SOURCE not in reject_codes:
                            reject_codes.append(HARD_REJECT_STALE_SOURCE)
                        constraint_refs.append(f"act_rev_mismatch:{cand.activity_ref}:r{act_rev}")

            if cand.source_kind == SOURCE_RESUME_ELIGIBLE and cand.resume_eligibility_ref:
                elig_obj = live_eligibilities.get(cand.resume_eligibility_ref)
                if elig_obj is not None:
                    if elig_obj.get("status") != ELIG_STATUS_OPEN:
                        if HARD_REJECT_STALE_SOURCE not in reject_codes:
                            reject_codes.append(HARD_REJECT_STALE_SOURCE)
                        constraint_refs.append(
                            f"elig_not_open:{cand.resume_eligibility_ref}:{elig_obj.get('status')}"
                        )
                if cand.activity_ref and cand.activity_ref in live_activities:
                    act_obj = live_activities[cand.activity_ref]
                    if act_obj.get("status") in TERMINAL_ACTIVITY_STATUSES:
                        if HARD_REJECT_TARGET_INVALID not in reject_codes:
                            reject_codes.append(HARD_REJECT_TARGET_INVALID)
                        constraint_refs.append(f"act_terminal:{cand.activity_ref}")

            # 5. Invalid Target Check (Section 23, B03)
            for tref in cand.target_refs:
                if tref in bad_targets or tref.startswith("invalid_target:"):
                    if HARD_REJECT_TARGET_INVALID not in reject_codes:
                        reject_codes.append(HARD_REJECT_TARGET_INVALID)
                    constraint_refs.append(f"target_invalid:{tref}")
                if tref in live_activities and live_activities[tref].get("status") in TERMINAL_ACTIVITY_STATUSES:
                    if HARD_REJECT_TARGET_INVALID not in reject_codes:
                        reject_codes.append(HARD_REJECT_TARGET_INVALID)
                    constraint_refs.append(f"target_act_terminal:{tref}")

            status = CONSTRAINT_STATUS_REJECTED if reject_codes else CONSTRAINT_STATUS_ALLOWED
            if status == CONSTRAINT_STATUS_ALLOWED:
                allowed_candidates.append(cand)
                constraint_refs = [f"hcallow:{cid}"]

            res_id = f"hcres:{sha256_hex(f'{context_snapshot.context_snapshot_id}:{cid}:{status}:{eval_iso}')[:16]}"
            results.append(
                CandidateConstraintResult(
                    constraint_result_id=res_id,
                    candidate_ref=cid,
                    status=status,
                    reason_codes=reject_codes if reject_codes else ["HARD_CONSTRAINT_PASSED"],
                    constraint_refs=constraint_refs,
                    evaluated_at=eval_iso,
                    policy_version=policy_version,
                )
            )

        return results, allowed_candidates


# ---------------------------------------------------------------------------
# Layer 2: CapacityEvaluator (Sections 28-37, 123 C01-C05, 124 D01-D05)
# ---------------------------------------------------------------------------


class CapacityEvaluator:
    """Sections 28-37: Evaluates current Capacity without deleting/invalidating Candidates.

    Enforces:
      - Motive != Capacity (Section 29)
      - Capacity block does NOT invalidate CandidateRecord (Section 30)
      - Missing capacity owner -> UNKNOWN, never fabricated as AVAILABLE (Sections 32-33)
      - interruptibility == ATOMIC blocks immediate START/RESUME/PAUSE/ABANDON while allowing
        CONTINUE / DEFER / NO_ACTION (Section 34)
      - Body only provides Capacity; high fatigue never invents a sleep candidate (Section 36)
    """

    @staticmethod
    def evaluate_capacity(
        *,
        allowed_candidates: Sequence[CandidateRecord],
        context_snapshot: DecisionContextSnapshot,
        body_capacity_state: Optional[str] = None,
        body_capacity_ref: Optional[str] = None,
        device_availability_state: Optional[str] = None,
        device_access_ref: Optional[str] = None,
        resource_availability_state: Optional[str] = None,
        resource_budget_ref: Optional[str] = None,
        privacy_availability_state: Optional[str] = None,
        privacy_availability_ref: Optional[str] = None,
        runtime_load_state: Optional[str] = None,
        runtime_availability_ref: Optional[str] = None,
        candidate_capacity_overrides: Optional[Mapping[str, str]] = None,
        unknown_capacity_policy: str = "ALLOW_WITH_UNKNOWN",
        captured_at: Optional[str] = None,
    ) -> AgencyCapacitySnapshot:
        cap_iso = _require_iso(captured_at or context_snapshot.captured_at, "captured_at")

        # Section 32: Do not fabricate missing capacity! If no canonical ref/state is supplied, use UNKNOWN.
        b_ref = body_capacity_ref or context_snapshot.body_capacity_ref
        b_state = body_capacity_state if body_capacity_state is not None else (
            CAPACITY_AVAILABLE if b_ref is not None else CAPACITY_UNKNOWN
        )
        d_state = device_availability_state if device_availability_state is not None else (
            CAPACITY_AVAILABLE if device_access_ref is not None else CAPACITY_UNKNOWN
        )
        r_state = resource_availability_state if resource_availability_state is not None else (
            CAPACITY_AVAILABLE if (resource_budget_ref is not None or context_snapshot.resource_refs) else CAPACITY_UNKNOWN
        )
        p_state = privacy_availability_state if privacy_availability_state is not None else (
            CAPACITY_AVAILABLE if privacy_availability_ref is not None else CAPACITY_UNKNOWN
        )
        rt_state = runtime_load_state if runtime_load_state is not None else CAPACITY_AVAILABLE

        interruptibility = context_snapshot.interruptibility
        atomic_ref = context_snapshot.current_atomic_boundary_ref

        # Compute overall capacity state across explicitly known dimensions
        known_states = [s for s in (b_state, d_state, r_state, p_state, rt_state) if s != CAPACITY_UNKNOWN]
        if any(s == CAPACITY_UNAVAILABLE for s in known_states):
            overall_state = CAPACITY_UNAVAILABLE
        elif any(s == CAPACITY_LIMITED for s in known_states):
            overall_state = CAPACITY_LIMITED
        elif not known_states or (
            b_state == CAPACITY_UNKNOWN
            and d_state == CAPACITY_UNKNOWN
            and r_state == CAPACITY_UNKNOWN
            and p_state == CAPACITY_UNKNOWN
            and runtime_load_state is None
        ):
            overall_state = CAPACITY_UNKNOWN
        else:
            overall_state = CAPACITY_AVAILABLE

        overrides = dict(candidate_capacity_overrides or {})
        cand_exec: dict[str, dict[str, Any]] = {}

        for cand in allowed_candidates:
            cid = cand.candidate_id
            c_state = overrides.get(cid, overall_state)
            if c_state not in VALID_CAPACITY_STATES:
                raise AgencyDecisionError(f"invalid candidate capacity state={c_state!r}")

            blocking_reasons: list[str] = []

            # Section 34: ATOMIC boundary protection
            is_continuing_current = (
                cand.candidate_kind == CANDIDATE_KIND_CONTINUE_CURRENT
                and cand.activity_ref == context_snapshot.current_activity_ref
                and context_snapshot.current_activity_status == STATUS_ACTIVE
            )
            if interruptibility == INTERRUPTIBILITY_ATOMIC and not is_continuing_current:
                blocking_reasons.append(REASON_ATOMIC_IN_PROGRESS)

            # Section 65: CONSIDER_EXPLICIT_GOAL without decomposed step cannot be immediately STARTED as Activity
            if cand.candidate_kind == CANDIDATE_KIND_CONSIDER_EXPLICIT_GOAL and not cand.goal_step_ref:
                blocking_reasons.append(REASON_GOAL_ATTENTION_DEFERRED)

            if c_state == CAPACITY_UNAVAILABLE:
                if r_state == CAPACITY_UNAVAILABLE:
                    blocking_reasons.append(REASON_RESOURCE_LIMIT)
                else:
                    blocking_reasons.append(REASON_CAPACITY_UNAVAILABLE)

            if blocking_reasons:
                exec_status = EXEC_NOT_NOW
            elif c_state == CAPACITY_LIMITED:
                exec_status = EXEC_WITH_LIMITS
            elif c_state == CAPACITY_UNKNOWN:
                if unknown_capacity_policy == "BLOCK_ON_UNKNOWN":
                    exec_status = EXEC_NOT_NOW
                    blocking_reasons.append(REASON_CAPACITY_UNKNOWN_DEFERRED)
                else:
                    exec_status = EXEC_UNKNOWN
            else:
                exec_status = EXEC_NOW

            cand_exec[cid] = {
                "candidate_id": cid,
                "capacity_state": c_state,
                "executability": exec_status,
                "blocking_reasons": blocking_reasons,
                "candidate_remains_open": cand.availability_status == CANDIDATE_STATUS_OPEN,
            }

        src_revs: list[str] = [f"ctx:{context_snapshot.context_snapshot_id}"]
        if b_ref:
            src_revs.append(b_ref)
        if atomic_ref:
            src_revs.append(atomic_ref)
        if device_access_ref:
            src_revs.append(device_access_ref)
        if resource_budget_ref:
            src_revs.append(resource_budget_ref)

        cap_id = f"cap:{sha256_hex(f'{context_snapshot.context_snapshot_id}:{overall_state}:{interruptibility}:{cap_iso}')[:16]}"
        return AgencyCapacitySnapshot(
            capacity_snapshot_id=cap_id,
            subject_id=GLOBAL_SUBJECT_ID,
            body_capacity_ref=b_ref,
            body_capacity_state=b_state,
            atomic_state_ref=atomic_ref,
            interruptibility=interruptibility,
            device_access_ref=device_access_ref,
            device_availability_state=d_state,
            resource_budget_ref=resource_budget_ref,
            resource_availability_state=r_state,
            privacy_availability_ref=privacy_availability_ref,
            privacy_availability_state=p_state,
            permission_ref=context_snapshot.permission_refs[0] if context_snapshot.permission_refs else None,
            runtime_availability_ref=runtime_availability_ref,
            runtime_load_state=rt_state,
            overall_capacity_state=overall_state,
            candidate_executability=cand_exec,
            captured_at=cap_iso,
            source_revisions=src_revs,
        )


# ---------------------------------------------------------------------------
# Layer 3: AccessibilityBuilder (Sections 40-46)
# ---------------------------------------------------------------------------


class AccessibilityBuilder:
    """Sections 40-46: Builds multi-dimensional structured `AgencyAccessibilityView` without any universal utility score."""

    @staticmethod
    def build_view(
        *,
        opportunity: DecisionOpportunity,
        allowed_candidates: Sequence[CandidateRecord],
        context_snapshot: DecisionContextSnapshot,
        capacity_snapshot: AgencyCapacitySnapshot,
        created_at: Optional[str] = None,
    ) -> AgencyAccessibilityView:
        ts = _require_iso(created_at or context_snapshot.captured_at, "created_at")
        obs_dt = _parse_iso(context_snapshot.captured_at)
        entries: dict[str, dict[str, Any]] = {}

        cur_act = context_snapshot.current_activity_ref
        cur_status = context_snapshot.current_activity_status
        intr = context_snapshot.interruptibility

        for cand in allowed_candidates:
            cid = cand.candidate_id
            exec_info = capacity_snapshot.candidate_executability.get(cid) or {}
            exec_now = str(exec_info.get("executability") or EXEC_NOW)
            cap_state = str(exec_info.get("capacity_state") or capacity_snapshot.overall_capacity_state)

            if cand.candidate_kind == CANDIDATE_KIND_CONTINUE_CURRENT and cand.activity_ref == cur_act:
                rel = "SAME_ACTIVITY"
                switch_cost = "NONE"
                prep_cost = "NONE"
                cont_rel = "CONTINUES_FOREGROUND"
            elif cand.candidate_kind == CANDIDATE_KIND_RESUME_ACTIVITY:
                rel = "RESUME_PAUSED_OR_WAITING"
                switch_cost = (
                    "ATOMIC_BLOCKED"
                    if intr == INTERRUPTIBILITY_ATOMIC
                    else ("MEDIUM" if cur_act and cur_status == STATUS_ACTIVE else "LOW")
                )
                prep_cost = "LOW"
                cont_rel = "RESUMES_PRIOR_EPISODE"
            elif cand.candidate_kind == CANDIDATE_KIND_CONSIDER_EXPLICIT_GOAL and not cand.goal_step_ref:
                rel = "ATTENTION_ONLY"
                switch_cost = "LOW"
                prep_cost = "HIGH"
                cont_rel = "ATTENTION_ONLY"
            else:
                rel = "SWITCH_REQUIRED" if (cur_act and cur_status == STATUS_ACTIVE) else "FROM_IDLE"
                if intr == INTERRUPTIBILITY_ATOMIC:
                    switch_cost = "ATOMIC_BLOCKED"
                elif intr == INTERRUPTIBILITY_SAFE_BOUNDARY:
                    switch_cost = "MEDIUM"
                elif cur_act and cur_status == STATUS_ACTIVE:
                    switch_cost = "MEDIUM"
                else:
                    switch_cost = "LOW"
                prep_cost = "LOW"
                cont_rel = "NEW_EPISODE"

            if cand.valid_until:
                rem_sec = (_parse_iso(cand.valid_until) - obs_dt).total_seconds()
                win_state = "CLOSING" if rem_sec <= 900 else "OPEN"
            else:
                win_state = "UNBOUNDED"

            if cand.candidate_kind == CANDIDATE_KIND_RESUME_ACTIVITY:
                wait_state = "RESUME_ELIGIBLE_OPEN"
            elif cur_status == STATUS_WAITING and cand.activity_ref == cur_act:
                wait_state = "WAITING_BLOCKED"
            else:
                wait_state = "NOT_WAITING"

            if capacity_snapshot.resource_availability_state == CAPACITY_UNAVAILABLE:
                res_cost = "INSUFFICIENT"
            elif capacity_snapshot.resource_availability_state == CAPACITY_LIMITED:
                res_cost = "MEDIUM"
            elif capacity_snapshot.resource_availability_state == CAPACITY_UNKNOWN:
                res_cost = "UNKNOWN"
            else:
                res_cost = "LOW"

            entries[cid] = {
                "candidate_id": cid,
                "candidate_kind": cand.candidate_kind,
                "source_kind": cand.source_kind,
                "relation_to_current_activity": rel,
                "switch_cost_class": switch_cost,
                "preparation_cost_class": prep_cost,
                "window_state": win_state,
                "body_capacity_class": cap_state,
                "current_wait_state": wait_state,
                "continuity_relation": cont_rel,
                "known_resource_cost": res_cost,
                "executability_now": exec_now,
            }

        acc_id = f"acc:{sha256_hex(f'{opportunity.opportunity_id}:{context_snapshot.context_snapshot_id}:{ts}')[:16]}"
        return AgencyAccessibilityView(
            accessibility_id=acc_id,
            subject_id=GLOBAL_SUBJECT_ID,
            opportunity_ref=opportunity.opportunity_id,
            candidate_entries=entries,
            created_at=ts,
        )


# ---------------------------------------------------------------------------
# v0.4 Day-Life Legality: sourced facts & the single-foreground contract
# ---------------------------------------------------------------------------

#: The one sourced fact key through which the life cycle tells AG-1 that the current foreground
#: Activity has no executable step left (material read out, source revoked, unknown revision...).
#: It is a *fact*, never an offer: it can withdraw the current CONTINUE and it can never create a
#: decision, change capacity, or alter a world fact.
OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE = "current_step_unavailable_true"


def observed_fact_is_true(
    observed_facts: Optional[Mapping[str, Any]], fact_key: str
) -> bool:
    """True only when ``fact_key`` is an ASCII key whose value *is* the boolean ``True``.

    ``"true"``, ``1`` and ``False`` are not the fact: the channel is typed, so a merely truthy
    value can never withdraw a legal decision.
    """
    if not isinstance(observed_facts, Mapping):
        return False
    for raw_key, raw_value in observed_facts.items():
        if not isinstance(raw_key, str) or not raw_key.isascii():
            continue
        if raw_key.strip() != fact_key:
            continue
        return raw_value is True
    return False


# ---------------------------------------------------------------------------
# Decision Legality Matrix & DecisionOutputValidator (Sections 55-60, 64-75)
# ---------------------------------------------------------------------------


class DecisionOutputValidator:
    """Sections 55-60, 64-75: Strict structural and semantic validator for all Decision outputs."""

    @staticmethod
    def compute_legal_decisions_for_candidate(
        candidate: CandidateRecord,
        *,
        context_snapshot: DecisionContextSnapshot,
        capacity_snapshot: AgencyCapacitySnapshot,
    ) -> list[str]:
        """Section 64-72: Compute which Decision enums are legal for a specific allowed CandidateRecord."""
        cid = candidate.candidate_id
        exec_info = capacity_snapshot.candidate_executability.get(cid) or {}
        exec_now = str(exec_info.get("executability") or EXEC_NOW)
        intr = context_snapshot.interruptibility
        cur_act = context_snapshot.current_activity_ref
        cur_status = context_snapshot.current_activity_status
        # v0.4 day-life legality: the single-foreground contract is owned by the Activity store and
        # by AG-2, not by AG-1.  LR-2 refuses START while *any* foreground reference is still held
        # (activity_continuity.py:1911-1922) and LR-4 refuses RESUME of another Activity under the
        # same condition (activity_continuity.py:2144-2157); AG-2 re-checks the ACTIVE case before
        # adoption (life_integration_ag2.py:2637-2641).  Offering those enums would be offering a
        # decision that is certain to be refused, so they are withheld here instead.
        # The reference is only "free" for START/RESUME when it is gone, or when it points at a
        # WAITING Activity -- the single state both owners pass ``allow_background_waiting`` for
        # (life_integration_ag2.py:3093-3096 and :3387-3390; activity_continuity.py:2144-2157).
        # PAUSED does NOT release it: pausing leaves foreground_activity_ref pointing at the paused
        # Activity (activity_continuity.py:2290-2293), so START/RESUME of another Activity is still
        # refused there.  Only the ACTIVE case is additionally refused earlier, by AG-2.
        foreground_holds_occupancy = bool(cur_act) and cur_status != STATUS_WAITING
        # A sourced fact from the life cycle: the current Activity has no executable step left.
        # It withdraws the current CONTINUE and nothing else -- no capacity change, no world fact,
        # no new decision, and no other candidate's legality.
        step_unavailable = observed_fact_is_true(
            context_snapshot.observed_facts, OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE
        )

        legal: list[str] = [DECISION_DEFER]

        # Section 65: START only for actionable new candidate (never CONSIDER_EXPLICIT_GOAL without step, never during ATOMIC)
        # v0.2 (Autonomous Life WP-02): a PERSONAL_OPPORTUNITY that is executable now is an
        # actionable new candidate in exactly the same sense as a world opportunity.  Before
        # this line existed the only legal decision for the subject's own work was DEFER, so
        # "continue my own booklet" could not be started at all -- see docs/v02-baseline.md.
        # v0.4: START always opens a *new* Activity (AG-2 start_activity, life_integration_ag2.py
        # :3098-3163), so while a foreground reference is held every START is a second foreground.
        if candidate.candidate_kind in {
            CANDIDATE_KIND_RESPOND_USER_REQUEST,
            CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
            CANDIDATE_KIND_FULFILL_COMMITMENT,
            CANDIDATE_KIND_PURSUE_GOAL_STEP,
            CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
        }:
            if (
                not foreground_holds_occupancy
                and intr != INTERRUPTIBILITY_ATOMIC
                and exec_now in {EXEC_NOW, EXEC_WITH_LIMITS, EXEC_UNKNOWN}
            ):
                legal.append(DECISION_START)

        # v0.4 B: the one formal exception, and it is *narrow by vocabulary*, not by scenario.
        #
        # An explicit local co-presence invitation is a typed user request whose target refs live
        # in the ``co_presence:`` namespace the protocol owns -- nothing else carries one.  Unlike
        # every other candidate, accepting it does not open a *second* foreground: the adoption
        # hands the running foreground over to the owner's own WAITING transition (LR-4, with a
        # resume condition on the invitation's end) and only then starts the joint Activity, so
        # the single-foreground rule is kept by the owner, not bypassed here.  That is why START
        # is legal for *this* candidate while a pausable foreground runs.  An ATOMIC foreground
        # still withdraws it, and so does a missing/foreign foreground.
        invitation_refs = [str(ref) for ref in (getattr(candidate, "target_refs", None) or ())]
        if (
            candidate.candidate_kind == CANDIDATE_KIND_RESPOND_USER_REQUEST
            and candidate.source_kind == SOURCE_USER_REQUEST
            and any(ref.startswith("co_presence:") for ref in invitation_refs)
            and cur_act is not None
            and cur_status == STATUS_ACTIVE
            and intr != INTERRUPTIBILITY_ATOMIC
            and DECISION_START not in legal
        ):
            legal.append(DECISION_START)

        # Section 66: CONTINUE only for CURRENT_ACTIVITY candidate when Activity is ACTIVE
        if (
            candidate.candidate_kind == CANDIDATE_KIND_CONTINUE_CURRENT
            and candidate.source_kind == SOURCE_CURRENT_ACTIVITY
            and candidate.activity_ref == cur_act
            and cur_status == STATUS_ACTIVE
        ):
            # CONTINUE keeps the execution gate: continuing a step that cannot run now is not a
            # legal choice, and a sourced "no step left" fact withdraws it entirely.
            if (
                not step_unavailable
                and exec_now in {EXEC_NOW, EXEC_WITH_LIMITS, EXEC_UNKNOWN}
            ):
                legal.append(DECISION_CONTINUE)
            # Section 69: WAIT only when known_waiting_basis_ref exists
            if (
                context_snapshot.known_waiting_basis_ref is not None
                and exec_now in {EXEC_NOW, EXEC_WITH_LIMITS, EXEC_UNKNOWN}
            ):
                legal.append(DECISION_WAIT)
            # Sections 68 & 70: PAUSE/ABANDON are the exits from *this* Activity.  Their legality
            # is the Activity's own identity (this candidate is the ACTIVE foreground Activity,
            # checked above) plus interruptibility -- never body/resource executability, otherwise
            # a spent budget would trap her inside an Activity she can no longer advance.
            if intr != INTERRUPTIBILITY_ATOMIC:
                legal.append(DECISION_PAUSE)
                legal.append(DECISION_ABANDON)

        # Section 67: RESUME only for RESUME_ELIGIBLE candidate with OPEN fresh eligibility
        if (
            candidate.candidate_kind == CANDIDATE_KIND_RESUME_ACTIVITY
            and candidate.source_kind == SOURCE_RESUME_ELIGIBLE
            and candidate.availability_status == CANDIDATE_STATUS_OPEN
            and candidate.resume_eligibility_ref is not None
        ):
            # v0.4: resuming *another* Activity while a foreground reference is held would be
            # refused by LR-4 (activity_continuity.py:2144-2157) -- and AG-2 would first pause the
            # ACTIVE foreground (life_integration_ag2.py:3391-3415), which is a real side effect for
            # a command that still fails (the paused foreground keeps the reference, :2290-2293).
            # So it is not offered at all.  Resuming the foreground Activity itself keeps its
            # legality: LR-4 skips the foreground check when fg_id == act_id.
            resumes_another_activity = (
                foreground_holds_occupancy and candidate.activity_ref != cur_act
            )
            if (
                not resumes_another_activity
                and intr != INTERRUPTIBILITY_ATOMIC
                and exec_now in {EXEC_NOW, EXEC_WITH_LIMITS, EXEC_UNKNOWN}
            ):
                legal.append(DECISION_RESUME)
            # ABANDON of an Activity that is not the current foreground is refused by LR-2 unless it
            # is WAITING *and* already released the foreground (activity_continuity.py:2158-2175).
            # This snapshot carries no ``foreground_released`` fact, so only the matching foreground
            # Activity keeps the offer -- never a decision the owner must refuse.
            if (
                intr != INTERRUPTIBILITY_ATOMIC
                and cur_act is not None
                and candidate.activity_ref == cur_act
            ):
                legal.append(DECISION_ABANDON)

        return legal

    @classmethod
    def validate_decision_output(
        cls,
        raw_output: Mapping[str, Any],
        *,
        candidate_set: CandidateSet,
        candidates_by_id: Mapping[str, CandidateRecord],
        constraint_results_by_id: Mapping[str, CandidateConstraintResult],
        context_snapshot: DecisionContextSnapshot,
        capacity_snapshot: AgencyCapacitySnapshot,
        #: v0.4 C: the practice handles *this* round offered (from the cycle's own bounded read of
        #: her method drafts).  ``None`` means no practice workspace was consulted, so no handle is
        #: legal at all -- an empty tuple means "consulted, nothing relevant", and both refuse a
        #: handle the model invented.  This is an allow-list, not authority: it enables nothing.
        allowed_method_refs: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """Sections 55-60, 64-75: Validate raw model or rule decision output."""
        if not isinstance(raw_output, Mapping):
            raise DecisionValidationError(
                "decision output must be a JSON object",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                schema_field=SCHEMA_FIELD_DECISION_OUTPUT, schema_rule=SCHEMA_RULE_NOT_AN_OBJECT,
                schema_extra={"value_type": _json_type_name(raw_output)},
            )
        _reject_forbidden_dict_keys(raw_output, "DecisionOutput")

        allowed_top_keys = {
            "decision",
            "selected_candidate_id",
            "selected_candidate_ref",
            "reason_codes",
            "short_rationale",
            "deferred_candidate_ids",
            "deferred_candidate_refs",
            "target_activity_ref",
            "reevaluation_hint_ref",
            # v0.4 stage A: her own "later" -- one proposal, and at most three decisions about
            # items she already has.  Both are validated by the future-items domain itself
            # (``life_future_items.validate_proposal`` / ``validate_actions``) so there is exactly
            # one definition of what an item is, and neither can name another owner's object.
            "later_item",
            "future_item_actions",
            # v0.4 C: the one practice she says she is following, and her own bounded verdict on
            # it.  Both are checked against the allow-list this round offered (and a verdict may
            # not name a different practice than the chosen one), so an invented handle is a
            # refusal like an invented candidate -- never a silently dropped field.
            "method_ref",
            "method_trial",
            "method_proposal",
            "sharing",
        }
        unknown_keys = set(raw_output.keys()) - allowed_top_keys
        if unknown_keys:
            raise DecisionValidationError(
                f"unknown keys in decision output: {sorted(unknown_keys)}",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                # The keys themselves are model output and may carry private words; only counts,
                # syntactic shape and a hit against our own alias list are recorded.
                schema_field=SCHEMA_FIELD_DECISION_OUTPUT, schema_rule=SCHEMA_RULE_UNKNOWN_KEYS,
                schema_extra=_unknown_key_facts(unknown_keys),
            )

        decision = raw_output.get("decision")
        if not isinstance(decision, str) or not decision.strip():
            raise DecisionValidationError(
                "decision must be a non-empty string",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                schema_field=SCHEMA_FIELD_DECISION,
                schema_rule=SCHEMA_RULE_MISSING_OR_WRONG_TYPE,
                schema_extra={"value_type": _json_type_name(decision)},
            )
        decision = decision.strip()
        if decision in FORBIDDEN_DECISION_OUTPUTS:
            raise DecisionValidationError(
                f"decision={decision!r} is forbidden in AG-1",
                failure_class=FAILURE_CLASS_ILLEGAL_DECISION_ENUM,
            )
        if decision not in VALID_DECISION_OUTPUTS:
            raise DecisionValidationError(
                f"decision={decision!r} is not a valid AG-1 decision enum",
                failure_class=FAILURE_CLASS_ILLEGAL_DECISION_ENUM,
            )

        sel_id = raw_output.get("selected_candidate_id")
        if sel_id is None and "selected_candidate_ref" in raw_output:
            sel_id = raw_output.get("selected_candidate_ref")

        if sel_id is not None:
            if not isinstance(sel_id, str) or not sel_id.strip():
                raise DecisionValidationError(
                    "selected_candidate_id must be a non-empty string or null",
                    failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                    schema_field=SCHEMA_FIELD_SELECTED_CANDIDATE_ID,
                    schema_rule=SCHEMA_RULE_MISSING_OR_WRONG_TYPE,
                    schema_extra={"value_type": _json_type_name(sel_id)},
                )
            sel_id = sel_id.strip()
            # Section 55: Model cannot invent a Candidate
            if sel_id not in candidate_set.candidate_refs or sel_id not in candidates_by_id:
                raise DecisionValidationError(
                    f"Section 55 violation: selected_candidate_id={sel_id!r} does not exist in CandidateSet",
                    failure_class=FAILURE_CLASS_INVENTED_CANDIDATE,
                )
            # Section 57: Model cannot override Hard Constraint
            c_res = constraint_results_by_id.get(sel_id)
            if c_res is None or c_res.status != CONSTRAINT_STATUS_ALLOWED:
                raise DecisionValidationError(
                    f"Section 57 violation: candidate {sel_id!r} was hard-rejected and cannot be selected",
                    failure_class=FAILURE_CLASS_HARD_CONSTRAINT_VIOLATION,
                )

        raw_deferred = raw_output.get("deferred_candidate_ids")
        if raw_deferred is None:
            raw_deferred = raw_output.get("deferred_candidate_refs") or []
        if isinstance(raw_deferred, (str, bytes)) or not isinstance(raw_deferred, Iterable):
            raise DecisionValidationError(
                "deferred_candidate_ids must be a list",
                failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                schema_field=SCHEMA_FIELD_DEFERRED_CANDIDATE_IDS,
                schema_rule=SCHEMA_RULE_NOT_A_LIST,
                schema_extra={"value_type": _json_type_name(raw_deferred)},
            )
        deferred_ids: list[str] = []
        for did in raw_deferred:
            if not isinstance(did, str) or did not in candidate_set.candidate_refs or did not in candidates_by_id:
                raise DecisionValidationError(
                    f"deferred_candidate_id={did!r} does not exist in CandidateSet",
                    failure_class=FAILURE_CLASS_INVENTED_CANDIDATE,
                )
            c_res = constraint_results_by_id.get(did)
            if c_res is None or c_res.status != CONSTRAINT_STATUS_ALLOWED:
                raise DecisionValidationError(
                    f"deferred_candidate_id={did!r} was hard-rejected",
                    failure_class=FAILURE_CLASS_HARD_CONSTRAINT_VIOLATION,
                )
            if did not in deferred_ids:
                deferred_ids.append(did)

        # Validate reason_codes against registry (Section 73)
        reason_codes = DecisionReasonCodeRegistry.validate_codes(
            raw_output.get("reason_codes") or [], allow_empty=False
        )

        # Sanitize short_rationale and drop hallucinated environmental facts (Section 56)
        short_rationale, dropped_claim = sanitize_short_rationale(
            raw_output.get("short_rationale"),
            observed_facts=context_snapshot.observed_facts,
        )

        intr = context_snapshot.interruptibility
        cur_act = context_snapshot.current_activity_ref
        cur_status = context_snapshot.current_activity_status
        target_activity_ref = raw_output.get("target_activity_ref")

        # Section 34: ATOMIC boundary check
        if intr == INTERRUPTIBILITY_ATOMIC and decision in {
            DECISION_START,
            DECISION_RESUME,
            DECISION_PAUSE,
            DECISION_ABANDON,
        }:
            raise DecisionValidationError(
                f"Section 34 violation: decision={decision!r} is forbidden while interruptibility == ATOMIC",
                failure_class=FAILURE_CLASS_ATOMIC_VIOLATION,
            )

        # Decision-specific legality checks (Sections 65-72)
        if decision == DECISION_NO_ACTION:
            if sel_id is not None:
                raise DecisionValidationError(
                    "Section 72 violation: NO_ACTION requires selected_candidate_id = null",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )

        elif decision == DECISION_START:
            if sel_id is None:
                raise DecisionValidationError(
                    "Section 65 violation: START requires a selected_candidate_id",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            sel_cand = candidates_by_id[sel_id]
            legal_for_cand = cls.compute_legal_decisions_for_candidate(
                sel_cand, context_snapshot=context_snapshot, capacity_snapshot=capacity_snapshot
            )
            if DECISION_START not in legal_for_cand:
                raise DecisionValidationError(
                    f"Section 65 violation: START is not legal for candidate {sel_id!r} (kind={sel_cand.candidate_kind!r})",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )

        elif decision == DECISION_CONTINUE:
            if sel_id is None:
                raise DecisionValidationError(
                    "Section 66 violation: CONTINUE requires selecting the CURRENT_ACTIVITY candidate",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            sel_cand = candidates_by_id[sel_id]
            if (
                sel_cand.candidate_kind != CANDIDATE_KIND_CONTINUE_CURRENT
                or sel_cand.source_kind != SOURCE_CURRENT_ACTIVITY
                or cur_status != STATUS_ACTIVE
            ):
                raise DecisionValidationError(
                    f"Section 66 violation: CONTINUE requires an ACTIVE CURRENT_ACTIVITY candidate (got kind={sel_cand.candidate_kind!r}, status={cur_status!r})",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            target_activity_ref = sel_cand.activity_ref or cur_act

        elif decision == DECISION_RESUME:
            if sel_id is None:
                raise DecisionValidationError(
                    "Section 67 violation: RESUME requires selecting an OPEN RESUME_ELIGIBLE candidate",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            sel_cand = candidates_by_id[sel_id]
            legal_for_cand = cls.compute_legal_decisions_for_candidate(
                sel_cand, context_snapshot=context_snapshot, capacity_snapshot=capacity_snapshot
            )
            if DECISION_RESUME not in legal_for_cand:
                raise DecisionValidationError(
                    f"Section 67 violation: RESUME is not legal for candidate {sel_id!r} (kind={sel_cand.candidate_kind!r})",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            target_activity_ref = sel_cand.activity_ref

        elif decision == DECISION_PAUSE:
            if not cur_act or cur_status != STATUS_ACTIVE:
                raise DecisionValidationError(
                    "Section 68 violation: PAUSE requires a currently ACTIVE foreground Activity",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            if sel_id is not None:
                sel_cand = candidates_by_id[sel_id]
                if sel_cand.activity_ref != cur_act:
                    raise DecisionValidationError(
                        "Section 68 violation: PAUSE can only target the current active Activity",
                        failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                    )
            target_activity_ref = cur_act

        elif decision == DECISION_WAIT:
            if context_snapshot.known_waiting_basis_ref is None:
                raise DecisionValidationError(
                    "Section 69 violation: WAIT requires a known_waiting_basis_ref in DecisionContextSnapshot",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            if not cur_act and sel_id is None:
                raise DecisionValidationError(
                    "Section 69 violation: WAIT requires an active Activity or selected candidate target",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )
            target_activity_ref = cur_act or (candidates_by_id[sel_id].activity_ref if sel_id else None)

        elif decision == DECISION_ABANDON:
            if sel_id is not None:
                sel_cand = candidates_by_id[sel_id]
                if not sel_cand.activity_ref:
                    raise DecisionValidationError(
                        "Section 70 violation: cannot ABANDON an unstarted Candidate without an existing nonterminal Activity",
                        failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                    )
                target_activity_ref = sel_cand.activity_ref
            elif cur_act and cur_status in OPEN_ACTIVITY_STATUSES:
                target_activity_ref = cur_act
            else:
                raise DecisionValidationError(
                    "Section 70 violation: ABANDON requires an existing nonterminal Activity",
                    failure_class=FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                )

        elif decision == DECISION_DEFER:
            if sel_id is not None and sel_id not in deferred_ids:
                deferred_ids.insert(0, sel_id)
            if not deferred_ids and sel_id is None:
                # Deferring the entire opportunity: record all hard-allowed candidate IDs as deferred
                deferred_ids = [
                    cid
                    for cid in candidate_set.candidate_refs
                    if cid in constraint_results_by_id
                    and constraint_results_by_id[cid].status == CONSTRAINT_STATUS_ALLOWED
                ]

        # v0.4 stage A: her own "later".  One proposal and at most three actions on items she
        # already has, both validated by the future-items domain (``life_future_items``) and both
        # carried on the committed Decision so the round can record them honestly.  A proposal is
        # *not* a write: the cycle records it in the store after the Decision commits, and a store
        # refusal is reported as a refusal.
        later_item = None
        if raw_output.get("later_item") is not None:
            try:
                later_item = life_future_items.validate_proposal(raw_output.get("later_item"))
            except life_future_items.FutureItemError as exc:
                raise DecisionValidationError(
                    f"later_item proposal refused by the future-items domain: {exc}",
                    failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                    schema_field=SCHEMA_FIELD_LATER_ITEM,
                    schema_rule=SCHEMA_RULE_REFUSED_BY_DOMAIN,
                ) from exc
        future_item_actions: list[dict[str, Any]] = []
        if raw_output.get("future_item_actions") is not None:
            try:
                future_item_actions = life_future_items.validate_actions(
                    raw_output.get("future_item_actions"))
            except life_future_items.FutureItemError as exc:
                raise DecisionValidationError(
                    f"future_item_actions refused by the future-items domain: {exc}",
                    failure_class=FAILURE_CLASS_INVALID_SCHEMA,
                    schema_field=SCHEMA_FIELD_FUTURE_ITEM_ACTIONS,
                    schema_rule=SCHEMA_RULE_REFUSED_BY_DOMAIN,
                ) from exc

        # v0.4 C: the practice this Decision follows (optional).  The handle must be one *this
        # round* offered; with no practice workspace consulted, naming one at all is an invented
        # identifier.  A trial verdict must name the same practice and a legal outcome.
        method_ref: Optional[str] = None
        if raw_output.get("method_ref") is not None:
            raw_ref = raw_output.get("method_ref")
            text = raw_ref.strip() if isinstance(raw_ref, str) else ""
            allowed = {str(item) for item in (allowed_method_refs or ()) if str(item)}
            if (not text or not text.startswith(METHOD_REF_PREFIX)
                    or ":" in text[len(METHOD_REF_PREFIX):]):
                raise DecisionValidationError(
                    f"method_ref={raw_ref!r} is not a bounded practice handle",
                    failure_class=FAILURE_CLASS_INVENTED_METHOD_REF,
                )
            if text not in allowed:
                raise DecisionValidationError(
                    f"method_ref={text!r} was not offered to this Decision"
                    f" (offered: {sorted(allowed)})",
                    failure_class=FAILURE_CLASS_INVENTED_METHOD_REF,
                )
            method_ref = text
        method_trial = None
        if raw_output.get("method_trial") is not None:
            try:
                method_trial = validate_method_trial(raw_output.get("method_trial"),
                                                     method_ref=method_ref)
            except DecisionValidationError as exc:
                raise _annotate_schema_field(exc, SCHEMA_FIELD_METHOD_TRIAL)
            if method_ref is None:
                raise DecisionValidationError(
                    "method_trial requires the Decision to name the method_ref it evaluates",
                    failure_class=FAILURE_CLASS_INVALID_METHOD_TRIAL,
                    schema_field=SCHEMA_FIELD_METHOD_TRIAL,
                )
        # v0.4 C: at most one bounded practice proposal ("one thing I want to try, under which
        # conditions, with what is still unknown").  It is a *proposal*: nothing is saved here, and
        # nothing about it becomes a preference, a belief or an executable program.
        method_proposal = None
        if raw_output.get("method_proposal") is not None:
            try:
                method_proposal = validate_method_proposal(raw_output.get("method_proposal"))
            except DecisionValidationError as exc:
                raise _annotate_schema_field(exc, SCHEMA_FIELD_METHOD_PROPOSAL)
        # v0.4 sharing: at most one bounded request against a draft she may already have or a
        # source artifact this round offered.  Shape only -- the owner refuses the rest.
        sharing_action = None
        if raw_output.get("sharing") is not None:
            try:
                sharing_action = validate_sharing_action(raw_output.get("sharing"))
            except DecisionValidationError as exc:
                raise _annotate_schema_field(exc, SCHEMA_FIELD_SHARING)

        return {
            "decision": decision,
            "selected_candidate_ref": sel_id,
            "target_activity_ref": target_activity_ref,
            "deferred_candidate_refs": deferred_ids,
            "reevaluation_hint_ref": raw_output.get("reevaluation_hint_ref"),
            "reason_codes": reason_codes,
            "short_rationale": short_rationale,
            "invalid_rationale_claim_dropped": dropped_claim,
            "later_item": later_item,
            "method_ref": method_ref,
            "method_trial": method_trial,
            "method_proposal": method_proposal,
            "sharing_action": sharing_action,
            "future_item_actions": future_item_actions,
        }


# ---------------------------------------------------------------------------
# DecisionPolicyRouter (Sections 47-50, 107-110)
# ---------------------------------------------------------------------------


class DecisionPolicyRouter:
    """Sections 47-50, 107-110: Routes Opportunities to `RULE_RESOLVED`, `COGNITIVE_REQUIRED`, or `NO_DECISION`.

    - Empty CandidateSet -> `RULE_RESOLVED` (`NO_ACTION`, `0 LLM`)
    - All candidates hard-rejected -> `RULE_RESOLVED` (`NO_ACTION`, `0 LLM`)
    - All allowed candidates blocked from immediate execution (`NOT_EXECUTABLE_NOW`) ->
      `RULE_RESOLVED` (`DEFER` or `NO_ACTION` per deterministic policy, `0 LLM`)
    - Never auto-selects a single executable candidate via rule (Section 49: even with 1 candidate,
      `NO_ACTION` is possible, so it routes to `COGNITIVE_REQUIRED`).
    """

    def __init__(
        self,
        *,
        all_capacity_blocked_decision: str = DECISION_DEFER,
    ) -> None:
        if all_capacity_blocked_decision not in {DECISION_DEFER, DECISION_NO_ACTION}:
            raise AgencyDecisionError("all_capacity_blocked_decision must be DEFER or NO_ACTION")
        self.all_capacity_blocked_decision = all_capacity_blocked_decision

    def route_opportunity(
        self,
        *,
        opportunity: DecisionOpportunity,
        allowed_candidates: Sequence[CandidateRecord],
        context_snapshot: DecisionContextSnapshot,
        capacity_snapshot: AgencyCapacitySnapshot,
    ) -> dict[str, Any]:
        if opportunity.status != OPPORTUNITY_STATUS_OPEN:
            return {
                "route": ROUTE_NO_DECISION,
                "rule_output": None,
                "reason": f"OPPORTUNITY_STATUS_{opportunity.status}",
            }

        # Case 1: Empty CandidateSet or all candidates hard-rejected (Sections 48, 108, 109)
        if not allowed_candidates:
            return {
                "route": ROUTE_RULE_RESOLVED,
                "rule_output": {
                    "decision": DECISION_NO_ACTION,
                    "selected_candidate_id": None,
                    "reason_codes": [REASON_NO_LEGAL_CANDIDATE],
                    "short_rationale": "当前无通过硬约束的合法候选。",
                    "deferred_candidate_ids": [],
                },
                "reason": "EMPTY_OR_ALL_HARD_REJECTED",
            }

        # Inspect executability of allowed candidates
        executable_candidates: list[CandidateRecord] = []
        blocked_candidates: list[CandidateRecord] = []
        blocking_reason_set: list[str] = []

        for cand in allowed_candidates:
            exec_info = capacity_snapshot.candidate_executability.get(cand.candidate_id) or {}
            exec_state = str(exec_info.get("executability") or EXEC_NOW)
            if exec_state == EXEC_NOT_NOW:
                blocked_candidates.append(cand)
                for r in exec_info.get("blocking_reasons") or []:
                    if r not in blocking_reason_set:
                        blocking_reason_set.append(r)
            else:
                executable_candidates.append(cand)

        # Case 2: All allowed candidates are currently NOT_EXECUTABLE_NOW (Section 110)
        if not executable_candidates and blocked_candidates:
            reason_codes = blocking_reason_set if blocking_reason_set else [REASON_CAPACITY_UNAVAILABLE]
            if self.all_capacity_blocked_decision == DECISION_DEFER:
                sel_id = blocked_candidates[0].candidate_id if len(blocked_candidates) == 1 else None
                return {
                    "route": ROUTE_RULE_RESOLVED,
                    "rule_output": {
                        "decision": DECISION_DEFER,
                        "selected_candidate_id": sel_id,
                        "reason_codes": reason_codes,
                        "short_rationale": "当前候选受容量或不可中断边界限制，暂缓处理。",
                        "deferred_candidate_ids": [c.candidate_id for c in blocked_candidates],
                    },
                    "reason": "ALL_ALLOWED_CANDIDATES_CAPACITY_BLOCKED",
                }
            return {
                "route": ROUTE_RULE_RESOLVED,
                "rule_output": {
                    "decision": DECISION_NO_ACTION,
                    "selected_candidate_id": None,
                    "reason_codes": reason_codes,
                    "short_rationale": "当前无立即具备执行条件的候选。",
                    "deferred_candidate_ids": [],
                },
                "reason": "ALL_ALLOWED_CANDIDATES_CAPACITY_BLOCKED_NO_ACTION",
            }

        # Case 3: At least one candidate is executable (1 candidate vs NO_ACTION, or multiple candidates)
        # Per Section 49 & 50: NEVER auto-select a single candidate by rule; route to COGNITIVE_REQUIRED.
        return {
            "route": ROUTE_COGNITIVE_REQUIRED,
            "rule_output": None,
            "reason": "COGNITIVE_CHOICE_AMONG_LEGAL_OPTIONS",
        }


# ---------------------------------------------------------------------------
# Provider-Neutral AgencyCognitionAdapter & FakeCognitionAdapter (Sections 51-63, 95-100, 106)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CognitiveDecisionRequest:
    """Sections 53-54, 96, 106, 139: Bounded, source-attributed, provider-neutral input for cognition."""

    opportunity_id: str
    candidate_set_id: str
    context_snapshot_id: str
    snapshot_fingerprint: str
    candidates: tuple[dict[str, Any], ...]
    current_activity_summary: Optional[dict[str, Any]]
    capacity_summary: dict[str, Any]
    legal_decision_enums: tuple[str, ...]
    allowed_reason_codes: tuple[str, ...]
    observed_fact_keys: tuple[str, ...]
    policy_version: str
    #: v0.2 (E07): a bounded, attributed slice of her own admitted experiences, supplied by the
    #: Memory boundary port and already governance-filtered.  It may inform a choice; it can never
    #: carry a command, and it never replaces the work's or the activity's real state.
    prior_experiences: tuple[dict[str, Any], ...] = ()
    #: v0.4 stage A: the bounded, *present* situation the model needs in order to choose well --
    #: what she is reading, the question she wrote, her own open "later" items (with their refs, so
    #: it can reschedule/cancel one by name), the project options this round supports, and whether
    #: parking the running Activity is on offer.  It is DATA: no authority field, no grant, and it
    #: never replaces the owners' own state.  Free text is allowed but bounded (see the adapter);
    #: it is never written to the run journal.
    situation_context: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = DEFAULT_COGNITION_TIMEOUT_MS
    max_output_tokens: int = DEFAULT_COGNITION_MAX_TOKENS

    def __post_init__(self) -> None:
        if len(self.candidates) > MAX_COGNITIVE_CANDIDATES:
            raise CognitionAdapterError(
                f"Section 139 violation: cognition candidate count ({len(self.candidates)}) exceeds MAX_COGNITIVE_CANDIDATES ({MAX_COGNITIVE_CANDIDATES})",
                failure_class=FAILURE_CLASS_CANDIDATE_EXPLOSION,
            )
        for c in self.candidates:
            if not c.get("candidate_id") or not c.get("source_kind") or not c.get("source_ref"):
                raise AgencyDecisionError(
                    "Section 54 violation: every cognitive candidate must carry candidate_id, source_kind, and source_ref"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "candidate_set_id": self.candidate_set_id,
            "context_snapshot_id": self.context_snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "candidates": copy.deepcopy(list(self.candidates)),
            "current_activity_summary": copy.deepcopy(self.current_activity_summary),
            "capacity_summary": copy.deepcopy(self.capacity_summary),
            "legal_decision_enums": list(self.legal_decision_enums),
            "allowed_reason_codes": list(self.allowed_reason_codes),
            "observed_fact_keys": list(self.observed_fact_keys),
            "prior_experiences": copy.deepcopy(list(self.prior_experiences)),
            "situation_context": copy.deepcopy(dict(self.situation_context)),
            "policy_version": self.policy_version,
            "timeout_ms": self.timeout_ms,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True)
class CognitiveDecisionResponse:
    """Provider-neutral response envelope from `AgencyCognitionAdapter`."""

    model_run_ref: str
    model_id: str
    model_version: str
    raw_output: Any
    latency_ms: int = 1


class AgencyCognitionAdapter(Protocol):
    """Section 52: Provider-neutral Cognition Adapter interface."""

    model_id: str
    model_version: str

    def invoke(
        self,
        request: CognitiveDecisionRequest,
        *,
        schema_retry_index: int = 0,
    ) -> CognitiveDecisionResponse:
        ...


class FakeCognitionAdapter:
    """Section 100, 128, 135, 138, 164: Configurable deterministic/scriptable Cognition Adapter for testing and shadow verification."""

    def __init__(
        self,
        *,
        model_id: str = "fake-cognition-provider",
        model_version: str = "ag1-test-v1",
        handler: Optional[Callable[[CognitiveDecisionRequest, int], Any]] = None,
        default_decision: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.model_id = model_id
        self.model_version = model_version
        self._handler = handler
        self._default_decision = copy.deepcopy(dict(default_decision)) if default_decision else None
        self.call_count: int = 0
        self.requests_seen: list[CognitiveDecisionRequest] = []

    def invoke(
        self,
        request: CognitiveDecisionRequest,
        *,
        schema_retry_index: int = 0,
    ) -> CognitiveDecisionResponse:
        self.call_count += 1
        self.requests_seen.append(request)
        run_ref = f"mrun:{sha256_hex(f'{self.model_id}:{request.opportunity_id}:{self.call_count}:{schema_retry_index}')[:16]}"

        if self._handler is not None:
            result = self._handler(request, schema_retry_index)
            if isinstance(result, Exception):
                if isinstance(result, CognitionAdapterError) and not result.model_run_ref:
                    result.model_run_ref = run_ref
                raise result
            if isinstance(result, CognitiveDecisionResponse):
                return result
            return CognitiveDecisionResponse(
                model_run_ref=run_ref,
                model_id=self.model_id,
                model_version=self.model_version,
                raw_output=result,
            )

        if self._default_decision is not None:
            return CognitiveDecisionResponse(
                model_run_ref=run_ref,
                model_id=self.model_id,
                model_version=self.model_version,
                raw_output=copy.deepcopy(self._default_decision),
            )

        # Default deterministic behavior when no custom handler is provided:
        # Pick the first executable candidate with a legal action, or NO_ACTION if none.
        for c in request.candidates:
            legal = c.get("legal_decisions") or []
            if DECISION_CONTINUE in legal:
                return CognitiveDecisionResponse(
                    model_run_ref=run_ref,
                    model_id=self.model_id,
                    model_version=self.model_version,
                    raw_output={
                        "decision": DECISION_CONTINUE,
                        "selected_candidate_id": c["candidate_id"],
                        "reason_codes": [REASON_CURRENT_ACTIVITY_CONTINUITY],
                        "short_rationale": "继续当前正在进行的活动。",
                        "deferred_candidate_ids": [],
                    },
                )
            if DECISION_RESUME in legal:
                return CognitiveDecisionResponse(
                    model_run_ref=run_ref,
                    model_id=self.model_id,
                    model_version=self.model_version,
                    raw_output={
                        "decision": DECISION_RESUME,
                        "selected_candidate_id": c["candidate_id"],
                        "reason_codes": [REASON_RESUME_ELIGIBLE_READY],
                        "short_rationale": "恢复已满足条件的活动。",
                        "deferred_candidate_ids": [],
                    },
                )
            if DECISION_START in legal:
                rc = (
                    REASON_USER_REQUEST_VALID
                    if c.get("source_kind") == SOURCE_USER_REQUEST
                    else (
                        REASON_COMMITMENT_FEASIBLE
                        if c.get("source_kind") == SOURCE_COMMITMENT
                        else (
                            REASON_GOAL_STEP_ACTIONABLE
                            if c.get("source_kind") == SOURCE_EXPLICIT_GOAL
                            else REASON_WORLD_OPPORTUNITY_OBSERVED
                        )
                    )
                )
                return CognitiveDecisionResponse(
                    model_run_ref=run_ref,
                    model_id=self.model_id,
                    model_version=self.model_version,
                    raw_output={
                        "decision": DECISION_START,
                        "selected_candidate_id": c["candidate_id"],
                        "reason_codes": [rc],
                        "short_rationale": "选择当前合法且可执行的候选。",
                        "deferred_candidate_ids": [],
                    },
                )

        return CognitiveDecisionResponse(
            model_run_ref=run_ref,
            model_id=self.model_id,
            model_version=self.model_version,
            raw_output={
                "decision": DECISION_NO_ACTION,
                "selected_candidate_id": None,
                "reason_codes": [REASON_NO_ACTION_CHOSEN],
                "short_rationale": "本次机会不认领任何候选。",
                "deferred_candidate_ids": [],
            },
        )


# ---------------------------------------------------------------------------
# Storage: AgencyDecisionJournal & AgencyDecisionStore (Sections 114-120)
# ---------------------------------------------------------------------------


class AgencyDecisionJournal:
    """Section 114: Append-only hash-chained journal view over `AgencyDecisionStore`."""

    def __init__(self, store: "AgencyDecisionStore") -> None:
        self.store = store

    def list_entries(
        self,
        *,
        opportunity_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        entries = self.store.read_journal()
        if opportunity_id is not None:
            entries = [
                e for e in entries if (e.get("payload") or {}).get("opportunity_id") == opportunity_id
            ]
        if decision_id is not None:
            entries = [
                e for e in entries if (e.get("payload") or {}).get("decision_id") == decision_id
            ]
        if event_type is not None:
            entries = [
                e for e in entries if (e.get("payload") or {}).get("event_type") == event_type
            ]
        return entries

    def verify_integrity(self) -> bool:
        try:
            self.store.read_journal()
            return True
        except RecoveryRequiredError:
            return False


class AgencyDecisionStore:
    """Sections 114-120: WAL + commit_intent + hash-chained journal + atomic snapshot replace store.

    Reuses the exact LR-2/LR-4/LR-5 storage & recovery protocol without inventing a 6th protocol.
    Guarantees DecisionRecord + Journal atomicity (Section 116) and full crash recovery across all
    stages of evaluation (Sections 117-120).
    """

    STATE_FILENAME = "agency_decision_state.json"
    JOURNAL_FILENAME = "agency_decision_journal.jsonl"
    WAL_FILENAME = COMMIT_INTENT_FILENAME

    def __init__(self, store_root: Path | str, *, namespace: str = NAMESPACE_ISOLATED_TEST) -> None:
        self.store_root = Path(store_root).expanduser().resolve()
        self.namespace = namespace
        self.state_path = self.store_root / self.STATE_FILENAME
        self.journal_path = self.store_root / self.JOURNAL_FILENAME
        self.wal_path = self.store_root / self.WAL_FILENAME
        self.run_dir = self.store_root / "run"
        self._verified_cache: Optional[dict[str, Any]] = None
        self._dirty_snapshot: Optional[dict[str, Any]] = None

    @contextmanager
    def _io_lock(self, *, shared: bool = False, timeout_seconds: float = 5.0) -> Iterator[None]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        lock_file = self.run_dir / ".agency_decision_io.lock"
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with lock_file.open("a+", encoding="utf-8") as handle:
            while True:
                try:
                    fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                        raise ActivityLockError(f"AG-1 store IO lock failed: {exc}") from exc
                    if time.monotonic() >= deadline:
                        raise ActivityLockError("AG-1 store IO lock timeout") from exc
                    time.sleep(0.005)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _empty_state(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_DECISION_STATE,
            "subject_id": GLOBAL_SUBJECT_ID,
            "namespace": self.namespace,
            "writer_domain": WRITER_DOMAIN_AGENCY_DECISION,
            "revision": 0,
            "last_entry_hash": GENESIS_HASH,
            "updated_at": _iso(now()),
            "opportunities_by_id": {},
            "opportunity_id_by_idempotency_key": {},
            "context_snapshots_by_id": {},
            "constraint_results_by_id": {},
            "constraint_refs_by_opportunity": {},
            "capacity_snapshots_by_id": {},
            "accessibility_views_by_id": {},
            "attempts_by_id": {},
            "attempt_ids_by_opportunity": {},
            "decisions_by_id": {},
            "decision_id_by_opportunity": {},
            "candidate_dispositions_by_decision": {},
            "staged_cognition_responses_by_attempt": {},
        }

    def _disk_signature(self) -> Optional[tuple[int, int, int, int]]:
        if not self.state_path.exists() or not self.journal_path.exists():
            return None
        try:
            st_s = self.state_path.stat()
            st_j = self.journal_path.stat()
            return (st_s.st_mtime_ns, st_s.st_size, st_j.st_mtime_ns, st_j.st_size)
        except OSError:
            return None

    def flush_snapshot(self) -> None:
        if self._dirty_snapshot is None:
            return
        state_to_write = self._dirty_snapshot
        self._dirty_snapshot = None
        self._write_snapshot_sync(state_to_write)
        sig = self._disk_signature()
        if sig is not None:
            self._verified_cache = {"sig": sig, "state": copy.deepcopy(state_to_write)}

    def _write_snapshot_sync(self, state_obj: dict[str, Any]) -> None:
        self.store_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state_obj, ensure_ascii=False, separators=(",", ":")) + "\n"
        tmp = self.store_root / f".{self.STATE_FILENAME}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        try:
            with tmp.open("w", encoding="utf-8") as sh:
                sh.write(payload)
                sh.flush()
                os.fsync(sh.fileno())
            os.replace(tmp, self.state_path)
            _fsync_dir(self.store_root)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def read_journal(self) -> list[dict[str, Any]]:
        self.flush_snapshot()
        if not self.journal_path.exists():
            return []
        raw = self.journal_path.read_text(encoding="utf-8")
        if raw and not raw.endswith("\n"):
            raise RecoveryRequiredError(
                "AG1_INCOMPLETE_JOURNAL_TAIL",
                f"{self.JOURNAL_FILENAME} does not end with newline",
            )
        entries: list[dict[str, Any]] = []
        prev_hash = GENESIS_HASH
        expected_rev = 1
        for idx, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise RecoveryRequiredError(
                    "AG1_CORRUPTED_JOURNAL_JSON",
                    f"invalid JSON at line {idx}: {exc}",
                ) from exc
            if not isinstance(item, dict) or item.get("schema_version") != SCHEMA_DECISION_JOURNAL_ENTRY:
                raise RecoveryRequiredError(
                    "AG1_INVALID_JOURNAL_SCHEMA",
                    f"invalid schema at line {idx}",
                )
            if item.get("store_revision") != expected_rev:
                raise RecoveryRequiredError(
                    "AG1_JOURNAL_REVISION_GAP",
                    f"expected store_revision={expected_rev}, got {item.get('store_revision')}",
                )
            if item.get("prev_entry_hash") != prev_hash:
                raise RecoveryRequiredError(
                    "AG1_JOURNAL_HASH_CHAIN_BROKEN",
                    f"broken hash chain at line {idx}",
                )
            rec_hash = item.get("entry_hash")
            unsigned = dict(item)
            unsigned.pop("entry_hash", None)
            if rec_hash != sha256_hex(canonical_json_line(unsigned)):
                raise RecoveryRequiredError(
                    "AG1_JOURNAL_ENTRY_TAMPERED",
                    f"entry_hash mismatch at line {idx}",
                )
            prev_hash = rec_hash
            expected_rev += 1
            entries.append(item)
        return entries

    @staticmethod
    def _apply_delta_into_state(state: dict[str, Any], delta: Mapping[str, Any]) -> None:
        for map_key, sub_updates in (delta.get("map_updates") or {}).items():
            target_map = state.setdefault(map_key, {})
            for k, v in sub_updates.items():
                if v == "__ABSENT__":
                    target_map.pop(k, None)
                else:
                    target_map[k] = copy.deepcopy(v)

    def rebuild_state_from_journal(self, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        state = self._empty_state()
        for entry in entries:
            delta = entry.get("state_delta") or {}
            self._apply_delta_into_state(state, delta)
            state["revision"] = int(entry["store_revision"])
            state["last_entry_hash"] = str(entry["entry_hash"])
            state["updated_at"] = str(entry["recorded_at"])
        return state

    def recover_from_wal_if_needed(
        self,
        *,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> bool:
        self.store_root.mkdir(parents=True, exist_ok=True)
        self._dirty_snapshot = None
        self._verified_cache = None

        # Repair torn journal tail if present
        if self.journal_path.exists():
            raw_j = self.journal_path.read_text(encoding="utf-8")
            if raw_j and not raw_j.endswith("\n"):
                lines = raw_j.splitlines()
                valid_lines: list[str] = []
                for ln in lines:
                    try:
                        json.loads(ln)
                        valid_lines.append(ln)
                    except ValueError:
                        break
                repaired = ("\n".join(valid_lines) + "\n") if valid_lines else ""
                self.journal_path.write_text(repaired, encoding="utf-8")

        if not self.wal_path.exists():
            if self.journal_path.exists():
                entries = self.read_journal()
                if not self.state_path.exists():
                    self._write_snapshot_sync(self.rebuild_state_from_journal(entries))
                else:
                    try:
                        st = json.loads(self.state_path.read_text(encoding="utf-8"))
                        if st.get("revision") != len(entries):
                            self._write_snapshot_sync(self.rebuild_state_from_journal(entries))
                    except ValueError:
                        self._write_snapshot_sync(self.rebuild_state_from_journal(entries))
            return False

        if fault_hook is not None:
            fault_hook("wal_recovery")

        try:
            wal_data = json.loads(self.wal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.wal_path.unlink(missing_ok=True)
            return True

        if not isinstance(wal_data, dict) or wal_data.get("schema_version") != SCHEMA_DECISION_WAL:
            self.wal_path.unlink(missing_ok=True)
            return True

        journal_entry = wal_data.get("journal_entry")
        if not isinstance(journal_entry, dict):
            self.wal_path.unlink(missing_ok=True)
            return True

        entries = self.read_journal()
        target_rev = int(journal_entry["store_revision"])
        if len(entries) == target_rev - 1:
            j_line = canonical_json_line(journal_entry) + "\n"
            with self.journal_path.open("a", encoding="utf-8") as jh:
                jh.write(j_line)
                jh.flush()
                os.fsync(jh.fileno())
            entries = self.read_journal()

        if len(entries) == target_rev:
            rebuilt = self.rebuild_state_from_journal(entries)
            self._write_snapshot_sync(rebuilt)

        self.wal_path.unlink(missing_ok=True)
        _fsync_dir(self.store_root)
        return True

    def load_state(self, *, _mutable: bool = False) -> dict[str, Any]:
        self.store_root.mkdir(parents=True, exist_ok=True)
        if self.wal_path.exists():
            self._dirty_snapshot = None
            self._verified_cache = None
            self.recover_from_wal_if_needed()

        if self._dirty_snapshot is not None:
            return self._dirty_snapshot if _mutable else copy.deepcopy(self._dirty_snapshot)

        sig = self._disk_signature()
        if (
            self._verified_cache is not None
            and sig is not None
            and self._verified_cache.get("sig") == sig
        ):
            cached = self._verified_cache["state"]
            return cached if _mutable else copy.deepcopy(cached)

        if not self.state_path.exists() and not self.journal_path.exists():
            empty = self._empty_state()
            return empty if _mutable else copy.deepcopy(empty)

        entries = self.read_journal()
        if not self.state_path.exists():
            rebuilt = self.rebuild_state_from_journal(entries)
            self._write_snapshot_sync(rebuilt)
            return rebuilt if _mutable else copy.deepcopy(rebuilt)

        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RecoveryRequiredError(
                "AG1_CORRUPTED_DECISION_STATE",
                f"agency_decision_state.json is corrupted: {exc}",
            ) from exc

        if state.get("schema_version") != SCHEMA_DECISION_STATE:
            raise RecoveryRequiredError("AG1_INVALID_STATE_SCHEMA", "invalid decision state schema")
        validate_subject_id(state.get("subject_id"))

        if state.get("revision") != len(entries):
            rebuilt = self.rebuild_state_from_journal(entries)
            self._write_snapshot_sync(rebuilt)
            state = rebuilt

        new_sig = self._disk_signature()
        if new_sig is not None:
            self._verified_cache = {"sig": new_sig, "state": state}
        return state if _mutable else copy.deepcopy(state)

    def commit_delta(
        self,
        *,
        lease: ActivityAuthorityLease,
        capability: AgencyDecisionCapability,
        map_updates: Mapping[str, Mapping[str, Any]],
        journal_payload: Mapping[str, Any],
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Any]:
        """Atomically commit a state delta + journal entry using WAL commit_intent.json."""
        AgencyDecisionAuthority.verify_capability(lease, capability)
        if capability.store_root != self.store_root:
            raise UnauthorizedAgencyMutationError("AgencyDecisionStore root mismatch")

        current = self.load_state(_mutable=True)

        # Enforce Section 79: Existing DecisionRecords in decisions_by_id cannot be overwritten or modified!
        dec_updates = map_updates.get("decisions_by_id") or {}
        for did, new_val in dec_updates.items():
            if did in current["decisions_by_id"] and current["decisions_by_id"][did] != new_val:
                raise DecisionImmutabilityError(
                    f"Section 79 violation: committed DecisionRecord {did!r} is immutable and cannot be modified"
                )

        next_rev = int(current["revision"]) + 1
        prev_hash = str(current["last_entry_hash"])
        ts_now = _iso(now())

        state_delta = {"map_updates": copy.deepcopy(dict(map_updates))}
        unsigned_entry: dict[str, Any] = {
            "schema_version": SCHEMA_DECISION_JOURNAL_ENTRY,
            "entry_id": f"djen:{next_rev:08d}:{uuid.uuid4().hex[:8]}",
            "store_revision": next_rev,
            "prev_entry_hash": prev_hash,
            "recorded_at": ts_now,
            "writer_domain": capability.writer_domain,
            "payload": copy.deepcopy(dict(journal_payload)),
            "state_delta": state_delta,
        }
        entry_hash = sha256_hex(canonical_json_line(unsigned_entry))
        signed_entry = dict(unsigned_entry)
        signed_entry["entry_hash"] = entry_hash

        next_state = current if self._dirty_snapshot is not None else copy.deepcopy(current)
        self._apply_delta_into_state(next_state, state_delta)
        next_state["schema_version"] = SCHEMA_DECISION_STATE
        next_state["subject_id"] = GLOBAL_SUBJECT_ID
        next_state["namespace"] = self.namespace
        next_state["revision"] = next_rev
        next_state["updated_at"] = ts_now
        next_state["last_entry_hash"] = entry_hash

        if fault_hook is not None:
            fault_hook("before_decision_commit")
            fault_hook("wal_prepare")

        wal_doc = {
            "schema_version": SCHEMA_DECISION_WAL,
            "store_revision": next_rev,
            "journal_entry": signed_entry,
        }
        self.store_root.mkdir(parents=True, exist_ok=True)
        wal_tmp = self.store_root / f".{self.WAL_FILENAME}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        with wal_tmp.open("w", encoding="utf-8") as wh:
            wh.write(canonical_json_line(wal_doc) + "\n")
            wh.flush()
            if fault_hook is not None:
                os.fsync(wh.fileno())
        os.replace(wal_tmp, self.wal_path)

        if fault_hook is not None:
            fault_hook("wal_persist")

        j_line = canonical_json_line(signed_entry) + "\n"
        with self.journal_path.open("a", encoding="utf-8") as jh:
            jh.write(j_line)
            jh.flush()
            if fault_hook is not None:
                os.fsync(jh.fileno())

        if fault_hook is not None:
            fault_hook("wal_commit")

        if fault_hook is not None:
            self._write_snapshot_sync(next_state)
            self.wal_path.unlink(missing_ok=True)
            _fsync_dir(self.store_root)
            sig = self._disk_signature()
            if sig is not None:
                self._verified_cache = {"sig": sig, "state": copy.deepcopy(next_state)}
            fault_hook("after_decision_commit")
        else:
            self.wal_path.unlink(missing_ok=True)
            self._dirty_snapshot = next_state
            self._verified_cache = None

        return copy.deepcopy(signed_entry)


# ---------------------------------------------------------------------------
# AgencyDecisionService (Orchestrating Pipeline, Freshness Recheck, Crash Recovery)
# ---------------------------------------------------------------------------


class AgencyDecisionService:
    """Orchestrates the 3-layer AG-1 Agency Decision pipeline with full provenance, freshness CAS, and crash recovery.

    Pipeline:
      1. Opportunity Idempotency check (Section 19)
      2. DecisionContextSnapshot capture (Sections 20-21)
      3. Layer 1: HardConstraintEvaluator (Sections 23-27, 0 LLM)
      4. Layer 2: CapacityEvaluator (Sections 28-37, 0 LLM)
      5. Layer 3: AccessibilityBuilder (Sections 40-46, 0 LLM)
      6. DecisionPolicyRouter (Sections 47-50):
         - RULE_RESOLVED (0 LLM)
         - COGNITIVE_REQUIRED (via AgencyCognitionAdapter)
      7. DecisionOutputValidator (Sections 55-60, 64-75)
      8. Snapshot Freshness Recheck & CAS before commit (Sections 76-78)
      9. Atomic DecisionRecord + AgencyDecisionJournal commit (Sections 114-116)
    """

    def __init__(
        self,
        *,
        store: AgencyDecisionStore,
        lease: ActivityAuthorityLease,
        capability: AgencyDecisionCapability,
        candidate_read_service: Optional[CandidateReadService] = None,
        activity_read_service: Optional[ActivityReadService] = None,
        resume_eligibility_store: Optional[ResumeEligibilityStore] = None,
        cognition_adapter: Optional[AgencyCognitionAdapter] = None,
        policy_router: Optional[DecisionPolicyRouter] = None,
        max_schema_retries: int = 0,
    ) -> None:
        AgencyDecisionAuthority.verify_capability(lease, capability)
        self.store = store
        self.journal = AgencyDecisionJournal(store)
        self.lease = lease
        self.capability = capability
        self.candidate_read_service = candidate_read_service
        self.activity_read_service = activity_read_service
        self.resume_eligibility_store = resume_eligibility_store
        self.cognition_adapter = cognition_adapter or FakeCognitionAdapter()
        self.policy_router = policy_router or DecisionPolicyRouter()
        if max_schema_retries < 0 or max_schema_retries > 1:
            raise AgencyDecisionError("Section 61: max_schema_retries must be 0 or 1")
        self.max_schema_retries = int(max_schema_retries)

        # Telemetry & Zero Cross-Domain Write Counters (Sections 107, 133, 152)
        self.total_opportunities: int = 0
        self.rule_resolved_count: int = 0
        self.cognitive_required_count: int = 0
        self.cognition_call_count: int = 0
        self.valid_cognition_count: int = 0
        self.invalid_cognition_count: int = 0
        self.timeout_cognition_count: int = 0
        self.failed_cognition_count: int = 0
        self.stale_before_commit_count: int = 0

        # Hard zero side-effect counters
        self.activity_mutation_count: int = 0
        self.action_creation_count: int = 0
        self.world_write_count: int = 0
        self.memory_write_count: int = 0
        self.goal_write_count: int = 0
        self.commitment_write_count: int = 0
        self.agenda_creation_count: int = 0
        self.resume_condition_creation_count: int = 0
        self.outbound_count: int = 0

    # -- Forbidden Cross-Domain Mutation Methods (Sections 83-87, 156-162) ---

    def start_activity(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 158: AG-1 cannot start an Activity")

    def resume_activity(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 158: AG-1 cannot resume an Activity")

    def pause_activity(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 68 & 158: AG-1 cannot pause an Activity")

    def abandon_activity(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 70 & 158: AG-1 cannot abandon an Activity")

    def create_action_proposal(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 84 & 157: AG-1 cannot create an ActionProposal")

    def create_agenda_item(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 5 & 160: AG-1 cannot create an AgendaItem")

    def create_resume_condition(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 5 & 159: AG-1 cannot create a ResumeCondition")

    def send_outbound_message(self, *args: Any, **kwargs: Any) -> None:
        raise UnauthorizedAgencyMutationError("Section 156: AG-1 cannot send outbound messages")

    # -- Freshness Verification (Sections 76-78, 129 I01-I05) ----------------

    def verify_snapshot_freshness(
        self,
        *,
        opportunity: DecisionOpportunity,
        context_snapshot: DecisionContextSnapshot,
        candidate_set: CandidateSet,
        selected_candidate_ref: Optional[str],
        constraint_results: Optional[Sequence[CandidateConstraintResult]] = None,
        live_capacity_override: Optional[Mapping[str, Any]] = None,
        check_at: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Sections 76-78: Re-verify candidate revisions, Activity revision, opportunity expiry, and capacity before commit."""
        chk_iso = _require_iso(check_at or _iso(now()), "check_at")
        chk_dt = _parse_iso(chk_iso)

        # 1. Opportunity expiration check (I03)
        if opportunity.valid_until and chk_dt > _parse_iso(opportunity.valid_until):
            return False, "OPPORTUNITY_EXPIRED_BEFORE_COMMIT"
        if opportunity.status in {OPPORTUNITY_STATUS_STALE, OPPORTUNITY_STATUS_SUPERSEDED}:
            return False, f"OPPORTUNITY_ALREADY_{opportunity.status}"

        # 2. Activity revision & status check (I02)
        if self.activity_read_service is not None:
            store_obj = getattr(self.activity_read_service, "store", None) or getattr(
                self.activity_read_service, "_store", None
            )
            st: dict[str, Any] = {}
            if store_obj is not None:
                st, _ = store_obj.load_verified_state_and_journal(_include_journal=False)
            live_rev = int(st.get("revision", 0))
            if (
                context_snapshot.current_activity_revision is not None
                and live_rev != int(context_snapshot.current_activity_revision)
            ):
                return False, f"ACTIVITY_REVISION_CHANGED:{context_snapshot.current_activity_revision}->{live_rev}"
            if context_snapshot.current_activity_ref:
                act_obj = (st.get("activities") or {}).get(context_snapshot.current_activity_ref)
                if act_obj is not None and act_obj.get("status") != context_snapshot.current_activity_status:
                    return False, f"ACTIVITY_STATUS_CHANGED:{context_snapshot.current_activity_status}->{act_obj.get('status')}"

        # 3. Candidate freshness & revision check for hard-constraint ALLOWED candidates (I01)
        if self.candidate_read_service is not None:
            if constraint_results is not None:
                cands_to_check = [
                    r.candidate_ref
                    for r in constraint_results
                    if r.status == CONSTRAINT_STATUS_ALLOWED
                ]
            else:
                cands_to_check = list(candidate_set.candidate_refs)
            if selected_candidate_ref and selected_candidate_ref not in cands_to_check:
                cands_to_check.append(selected_candidate_ref)
            for cid in cands_to_check:
                if self.candidate_read_service.get_candidate(cid) is None:
                    continue
                v_res = self.candidate_read_service.validate_candidate(cid, observed_at=chk_iso)
                if not v_res.get("valid", False):
                    # If the selected candidate or any allowed candidate in the snapshot changed/invalidated during model thinking:
                    return False, f"CANDIDATE_STALE_DURING_EVALUATION:{cid}:{v_res.get('reason_code')}"
                snap_cinfo = context_snapshot.candidate_revisions.get(cid)
                if snap_cinfo is not None:
                    if v_res.get("source_revision") != snap_cinfo.get("source_revision"):
                        return False, f"CANDIDATE_SOURCE_REVISION_CHANGED:{cid}"

        # 4. ResumeEligibility live check if selected candidate is RESUME_ELIGIBLE
        if selected_candidate_ref and self.resume_eligibility_store is not None and self.candidate_read_service is not None:
            c_dict = self.candidate_read_service.get_candidate(selected_candidate_ref)
            if c_dict and c_dict.get("resume_eligibility_ref"):
                est = self.resume_eligibility_store.load_state()
                el_obj = (est.get("eligibilities") or {}).get(c_dict["resume_eligibility_ref"])
                if el_obj is not None and el_obj.get("status") != ELIG_STATUS_OPEN:
                    return False, f"RESUME_ELIGIBILITY_CONSUMED:{c_dict['resume_eligibility_ref']}"

        # 5. Capacity change check during model call (I04)
        if live_capacity_override is not None:
            new_cap_state = live_capacity_override.get("overall_capacity_state")
            new_intr = live_capacity_override.get("interruptibility")
            if new_cap_state == CAPACITY_UNAVAILABLE:
                return False, "CAPACITY_BECAME_UNAVAILABLE_DURING_COGNITION"
            if new_intr == INTERRUPTIBILITY_ATOMIC and context_snapshot.interruptibility != INTERRUPTIBILITY_ATOMIC:
                return False, "INTERRUPTIBILITY_BECAME_ATOMIC_DURING_COGNITION"

        return True, None

    # -- Main Evaluation Entrypoint ------------------------------------------

    def evaluate_opportunity(
        self,
        *,
        opportunity: DecisionOpportunity,
        candidate_set: CandidateSet,
        candidates: Optional[Sequence[CandidateRecord]] = None,
        life_frame: Optional[Mapping[str, Any]] = None,
        body_capacity_state: Optional[str] = None,
        body_capacity_ref: Optional[str] = None,
        device_availability_state: Optional[str] = None,
        device_access_ref: Optional[str] = None,
        resource_availability_state: Optional[str] = None,
        resource_budget_ref: Optional[str] = None,
        privacy_availability_state: Optional[str] = None,
        privacy_availability_ref: Optional[str] = None,
        runtime_load_state: Optional[str] = None,
        runtime_availability_ref: Optional[str] = None,
        candidate_capacity_overrides: Optional[Mapping[str, str]] = None,
        unknown_capacity_policy: str = "ALLOW_WITH_UNKNOWN",
        permission_refs: Optional[Sequence[str]] = None,
        denied_permission_by_candidate: Optional[Mapping[str, str]] = None,
        resource_refs: Optional[Sequence[str]] = None,
        commitment_constraint_refs: Optional[Sequence[str]] = None,
        hard_conflict_by_candidate: Optional[Mapping[str, Mapping[str, str]]] = None,
        invalid_target_refs: Optional[Iterable[str]] = None,
        observed_world_refs: Optional[Sequence[str]] = None,
        observed_facts: Optional[Mapping[str, Any]] = None,
        #: E07: the bounded, governance-filtered experience view from the Memory boundary port.
        prior_experiences: Optional[Sequence[Mapping[str, Any]]] = None,
        #: v0.4 stage A: the bounded present situation (reading, her own question, her open "later"
        #: items with their refs, the project options of this round, whether parking is on offer).
        #: Passed straight to the cognition request as data; it authorizes nothing.
        situation_context: Optional[Mapping[str, Any]] = None,
        current_atomic_boundary_ref: Optional[str] = None,
        known_waiting_basis_ref: Optional[str] = None,
        channel: Optional[str] = None,
        unobserved_world_refs: Optional[Sequence[str]] = None,
        unauthorized_memory_refs: Optional[Sequence[str]] = None,
        supersedes_decision_ref: Optional[str] = None,
        exploration_seed: Optional[int] = None,
        post_cognition_mutation_hook: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
        fault_hook: Optional[Callable[[str], None]] = None,
        #: v0.4 C: the practice handles this round offered, as an allow-list for the optional
        #: ``method_ref`` / ``method_trial`` the model may name.  ``None`` = no practice workspace
        #: was consulted, so no handle is legal; it enables nothing and grants nothing.
        allowed_method_refs: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """Evaluate one DecisionOpportunity through the 3-layer AG-1 pipeline."""
        AgencyDecisionAuthority.verify_capability(self.lease, self.capability)

        state = self.store.load_state(_mutable=True)

        # Section 19: Opportunity Idempotency check
        existing_opp_id = state["opportunity_id_by_idempotency_key"].get(opportunity.idempotency_key)
        if existing_opp_id is not None and not supersedes_decision_ref:
            existing_opp = state["opportunities_by_id"].get(existing_opp_id)
            existing_dec_id = state["decision_id_by_opportunity"].get(existing_opp_id)
            if existing_dec_id and existing_dec_id in state["decisions_by_id"]:
                dec_dict = state["decisions_by_id"][existing_dec_id]
                att_ids = state["attempt_ids_by_opportunity"].get(existing_opp_id) or []
                att_dict = state["attempts_by_id"].get(att_ids[-1]) if att_ids else None
                return {
                    "status": "IDEMPOTENT_REUSE",
                    "opportunity": copy.deepcopy(existing_opp),
                    "decision_record": DecisionRecord.from_dict(dec_dict),
                    "attempt": DecisionAttempt.from_dict(att_dict) if att_dict else None,
                }

        self.total_opportunities += 1

        # Resolve CandidateRecord objects
        cands_list: list[CandidateRecord] = []
        if candidates is not None:
            cands_list = list(candidates)
        elif self.candidate_read_service is not None:
            for cid in candidate_set.candidate_refs:
                cdict = self.candidate_read_service.get_candidate(cid)
                if cdict is not None:
                    cands_list.append(CandidateRecord.from_dict(cdict))
        candidates_by_id: dict[str, CandidateRecord] = {c.candidate_id: c for c in cands_list}

        if fault_hook is not None:
            fault_hook("before_context_snapshot")

        # 1. Build DecisionContextSnapshot (Sections 20-21)
        context_snapshot = DecisionContextSnapshot.build(
            opportunity=opportunity,
            candidate_set=candidate_set,
            candidates=candidates_by_id,
            activity_read_service=self.activity_read_service,
            life_frame=life_frame,
            body_capacity_ref=body_capacity_ref,
            permission_refs=permission_refs,
            denied_permission_by_candidate=denied_permission_by_candidate,
            resource_refs=resource_refs,
            commitment_constraint_refs=commitment_constraint_refs,
            hard_conflict_by_candidate=hard_conflict_by_candidate,
            observed_world_refs=observed_world_refs,
            observed_facts=observed_facts,
            current_atomic_boundary_ref=current_atomic_boundary_ref,
            known_waiting_basis_ref=known_waiting_basis_ref,
            channel=channel,
            unobserved_world_refs=unobserved_world_refs,
            unauthorized_memory_refs=unauthorized_memory_refs,
            captured_at=opportunity.observed_at,
        )

        if fault_hook is not None:
            fault_hook("after_context_snapshot")

        # 2. Layer 1: HardConstraintEvaluator (Sections 23-27, 0 LLM)
        constraint_results, allowed_candidates = HardConstraintEvaluator.evaluate_candidates(
            candidates=cands_list,
            context_snapshot=context_snapshot,
            candidate_read_service=self.candidate_read_service,
            activity_read_service=self.activity_read_service,
            resume_eligibility_store=self.resume_eligibility_store,
            invalid_target_refs=invalid_target_refs,
            evaluated_at=context_snapshot.captured_at,
            policy_version=opportunity.policy_version,
        )
        constraint_results_by_id = {r.candidate_ref: r for r in constraint_results}

        if fault_hook is not None:
            fault_hook("after_constraint_evaluation")

        # 3. Layer 2: CapacityEvaluator (Sections 28-37, 0 LLM)
        capacity_snapshot = CapacityEvaluator.evaluate_capacity(
            allowed_candidates=allowed_candidates,
            context_snapshot=context_snapshot,
            body_capacity_state=body_capacity_state,
            body_capacity_ref=body_capacity_ref,
            device_availability_state=device_availability_state,
            device_access_ref=device_access_ref,
            resource_availability_state=resource_availability_state,
            resource_budget_ref=resource_budget_ref,
            privacy_availability_state=privacy_availability_state,
            privacy_availability_ref=privacy_availability_ref,
            runtime_load_state=runtime_load_state,
            runtime_availability_ref=runtime_availability_ref,
            candidate_capacity_overrides=candidate_capacity_overrides,
            unknown_capacity_policy=unknown_capacity_policy,
            captured_at=context_snapshot.captured_at,
        )

        if fault_hook is not None:
            fault_hook("after_capacity_evaluation")

        # 4. Layer 3: AccessibilityBuilder (Sections 40-46, 0 LLM)
        accessibility_view = AccessibilityBuilder.build_view(
            opportunity=opportunity,
            allowed_candidates=allowed_candidates,
            context_snapshot=context_snapshot,
            capacity_snapshot=capacity_snapshot,
            created_at=context_snapshot.captured_at,
        )

        # 5. DecisionPolicyRouter (Sections 47-50)
        routing = self.policy_router.route_opportunity(
            opportunity=opportunity,
            allowed_candidates=allowed_candidates,
            context_snapshot=context_snapshot,
            capacity_snapshot=capacity_snapshot,
        )
        route = str(routing["route"])

        att_seq = len(state["attempt_ids_by_opportunity"].get(opportunity.opportunity_id) or []) + 1
        attempt_id = f"datt:{sha256_hex(f'{opportunity.opportunity_id}:{att_seq}:{context_snapshot.snapshot_fingerprint}')[:16]}"
        started_iso = context_snapshot.captured_at

        attempt = DecisionAttempt(
            attempt_id=attempt_id,
            decision_opportunity_id=opportunity.opportunity_id,
            candidate_set_ref=candidate_set.candidate_set_id,
            context_snapshot_ref=context_snapshot.context_snapshot_id,
            policy_version=opportunity.policy_version,
            route=route,
            status=ATTEMPT_STATUS_OPEN,
            started_at=started_iso,
        )

        # Fast-path for RULE_RESOLVED (0 LLM calls, single atomic commit at end)
        if route == ROUTE_RULE_RESOLVED:
            self.rule_resolved_count += 1
            validated = DecisionOutputValidator.validate_decision_output(
                routing["rule_output"],
                candidate_set=candidate_set,
                candidates_by_id=candidates_by_id,
                constraint_results_by_id=constraint_results_by_id,
                context_snapshot=context_snapshot,
                capacity_snapshot=capacity_snapshot,
                allowed_method_refs=allowed_method_refs,
            )
            return self._finalize_and_commit_decision(
                opportunity=opportunity,
                candidate_set=candidate_set,
                candidates_by_id=candidates_by_id,
                context_snapshot=context_snapshot,
                constraint_results=constraint_results,
                capacity_snapshot=capacity_snapshot,
                accessibility_view=accessibility_view,
                attempt=attempt,
                validated_output=validated,
                model_run_ref=None,
                model_id=None,
                model_version=None,
                supersedes_decision_ref=supersedes_decision_ref,
                exploration_seed=exploration_seed,
                live_capacity_override=None,
                fault_hook=fault_hook,
            )

        # COGNITIVE_REQUIRED path
        self.cognitive_required_count += 1

        # Persist OPEN attempt before invoking external cognition if fault_hook is active (Section 117-118)
        if fault_hook is not None:
            self._persist_pre_cognition_state(
                opportunity=opportunity,
                context_snapshot=context_snapshot,
                constraint_results=constraint_results,
                capacity_snapshot=capacity_snapshot,
                accessibility_view=accessibility_view,
                attempt=attempt,
            )
            fault_hook("before_cognition")

        # Build bounded CognitiveDecisionRequest (only hard-constraint ALLOWED candidates! Section 27, B05, 139)
        if len(allowed_candidates) > MAX_COGNITIVE_CANDIDATES:
            self.failed_cognition_count += 1
            return self._record_failed_attempt(
                opportunity=opportunity,
                context_snapshot=context_snapshot,
                constraint_results=constraint_results,
                capacity_snapshot=capacity_snapshot,
                accessibility_view=accessibility_view,
                attempt=attempt,
                failure_class=FAILURE_CLASS_CANDIDATE_EXPLOSION,
                model_run_ref=None,
            )

        cog_candidates: list[dict[str, Any]] = []
        union_legal_enums: set[str] = {DECISION_NO_ACTION, DECISION_DEFER}
        for ac in allowed_candidates:
            legal_for_c = DecisionOutputValidator.compute_legal_decisions_for_candidate(
                ac,
                context_snapshot=context_snapshot,
                capacity_snapshot=capacity_snapshot,
            )
            union_legal_enums.update(legal_for_c)
            exec_info = capacity_snapshot.candidate_executability.get(ac.candidate_id) or {}
            acc_info = accessibility_view.candidate_entries.get(ac.candidate_id) or {}
            cog_candidates.append(
                {
                    "candidate_id": ac.candidate_id,
                    "candidate_kind": ac.candidate_kind,
                    "source_kind": ac.source_kind,
                    "source_ref": ac.source_ref,
                    "target_refs": list(ac.target_refs),
                    "bounded_description": f"{ac.candidate_kind} from {ac.source_kind} ({ac.source_ref})",
                    "executability_now": exec_info.get("executability", EXEC_NOW),
                    "accessibility_dimensions": copy.deepcopy(acc_info),
                    "legal_decisions": legal_for_c,
                }
            )

        cur_act_summary = None
        if context_snapshot.current_activity_ref:
            cur_act_summary = {
                "activity_ref": context_snapshot.current_activity_ref,
                "status": context_snapshot.current_activity_status,
                "interruptibility": context_snapshot.interruptibility,
            }

        cog_request = CognitiveDecisionRequest(
            opportunity_id=opportunity.opportunity_id,
            candidate_set_id=candidate_set.candidate_set_id,
            context_snapshot_id=context_snapshot.context_snapshot_id,
            snapshot_fingerprint=context_snapshot.snapshot_fingerprint,
            candidates=tuple(cog_candidates),
            current_activity_summary=cur_act_summary,
            capacity_summary={
                "overall_capacity_state": capacity_snapshot.overall_capacity_state,
                "body_capacity_state": capacity_snapshot.body_capacity_state,
                "device_availability_state": capacity_snapshot.device_availability_state,
                "resource_availability_state": capacity_snapshot.resource_availability_state,
                "interruptibility": capacity_snapshot.interruptibility,
            },
            legal_decision_enums=tuple(sorted(union_legal_enums)),
            allowed_reason_codes=tuple(sorted(VALID_DECISION_REASON_CODES)),
            observed_fact_keys=tuple(sorted(str(k) for k in context_snapshot.observed_facts.keys())),
            situation_context=dict(situation_context or {}),
            prior_experiences=tuple(dict(e) for e in (prior_experiences or ())),
            policy_version=opportunity.policy_version,
        )

        validated_output: Optional[dict[str, Any]] = None
        cog_response: Optional[CognitiveDecisionResponse] = None
        last_failure_class: str = FAILURE_CLASS_INVALID_SCHEMA
        last_schema_detail: Optional[dict[str, Any]] = None

        for retry_idx in range(self.max_schema_retries + 1):
            attempt.retry_count = retry_idx
            # One response, one refusal: a retry that ends in INVALID_JSON / an empty answer must
            # not inherit the previous answer's schema detail as if it explained this one.
            last_schema_detail = None
            try:
                if fault_hook is not None:
                    fault_hook("during_cognition")
                self.cognition_call_count += 1
                cog_response = self.cognition_adapter.invoke(
                    cog_request,
                    schema_retry_index=retry_idx,
                )
            except CognitionAdapterError as exc:
                if "TIMEOUT" in exc.failure_class:
                    self.timeout_cognition_count += 1
                self.failed_cognition_count += 1
                return self._record_failed_attempt(
                    opportunity=opportunity,
                    context_snapshot=context_snapshot,
                    constraint_results=constraint_results,
                    capacity_snapshot=capacity_snapshot,
                    accessibility_view=accessibility_view,
                    attempt=attempt,
                    failure_class=exc.failure_class,
                    model_run_ref=exc.model_run_ref,
                )

            attempt.model_run_ref = cog_response.model_run_ref

            # Parse raw_output if string
            raw_obj: Any = cog_response.raw_output
            if raw_obj is None or (isinstance(raw_obj, str) and not raw_obj.strip()):
                last_failure_class = FAILURE_CLASS_MODEL_REFUSAL
                self.invalid_cognition_count += 1
                continue
            if isinstance(raw_obj, str):
                try:
                    raw_obj = json.loads(raw_obj)
                except ValueError:
                    last_failure_class = FAILURE_CLASS_INVALID_JSON
                    self.invalid_cognition_count += 1
                    continue

            try:
                validated_output = DecisionOutputValidator.validate_decision_output(
                    raw_obj,
                    candidate_set=candidate_set,
                    candidates_by_id=candidates_by_id,
                    constraint_results_by_id=constraint_results_by_id,
                    context_snapshot=context_snapshot,
                    capacity_snapshot=capacity_snapshot,
                    allowed_method_refs=allowed_method_refs,
                )
                self.valid_cognition_count += 1
                break
            except (DecisionValidationError, HiddenReasoningForbiddenError, UniversalUtilityForbiddenError) as v_exc:
                last_failure_class = getattr(v_exc, "failure_class", FAILURE_CLASS_INVALID_SCHEMA)
                # *Why* it was refused, in the same value-free vocabulary the journal uses: the
                # message itself may quote the model ("unknown keys: [...]"), so it is never the
                # thing that gets recorded.  A later retry overwrites this with its own refusal --
                # the last answer is the one the attempt failed on.
                last_schema_detail = describe_schema_refusal(v_exc)
                self.invalid_cognition_count += 1
                # Section 60 & 61: Only retry on schema/JSON format errors; never retry on invented candidate or hard constraint violation
                if last_failure_class in {
                    FAILURE_CLASS_INVENTED_CANDIDATE,
                    FAILURE_CLASS_HARD_CONSTRAINT_VIOLATION,
                    FAILURE_CLASS_ILLEGAL_DECISION_ENUM,
                    FAILURE_CLASS_ILLEGAL_DECISION_MATRIX,
                    FAILURE_CLASS_ATOMIC_VIOLATION,
                }:
                    break

        if validated_output is None or cog_response is None:
            self.failed_cognition_count += 1
            return self._record_failed_attempt(
                opportunity=opportunity,
                context_snapshot=context_snapshot,
                constraint_results=constraint_results,
                capacity_snapshot=capacity_snapshot,
                accessibility_view=accessibility_view,
                attempt=attempt,
                failure_class=last_failure_class,
                model_run_ref=cog_response.model_run_ref if cog_response else None,
                schema_detail=last_schema_detail,
            )

        # Stage raw validated model response before freshness recheck when fault_hook is active (Section 119)
        if fault_hook is not None:
            self._stage_cognition_response(
                attempt=attempt,
                validated_output=validated_output,
                cog_response=cog_response,
                candidate_set=candidate_set,
            )
            fault_hook("after_cognition")

        # Optional test hook simulating world/activity/candidate change during model call (Section 76-77, I01-I05)
        live_cap_override: Optional[dict[str, Any]] = None
        if post_cognition_mutation_hook is not None:
            live_cap_override = post_cognition_mutation_hook()

        return self._finalize_and_commit_decision(
            opportunity=opportunity,
            candidate_set=candidate_set,
            candidates_by_id=candidates_by_id,
            context_snapshot=context_snapshot,
            constraint_results=constraint_results,
            capacity_snapshot=capacity_snapshot,
            accessibility_view=accessibility_view,
            attempt=attempt,
            validated_output=validated_output,
            model_run_ref=cog_response.model_run_ref,
            model_id=cog_response.model_id,
            model_version=cog_response.model_version,
            supersedes_decision_ref=supersedes_decision_ref,
            exploration_seed=exploration_seed,
            live_capacity_override=live_cap_override,
            fault_hook=fault_hook,
        )

    def _persist_pre_cognition_state(
        self,
        *,
        opportunity: DecisionOpportunity,
        context_snapshot: DecisionContextSnapshot,
        constraint_results: Sequence[CandidateConstraintResult],
        capacity_snapshot: AgencyCapacitySnapshot,
        accessibility_view: AgencyAccessibilityView,
        attempt: DecisionAttempt,
    ) -> None:
        state = self.store.load_state()
        att_list = list(state["attempt_ids_by_opportunity"].get(opportunity.opportunity_id) or [])
        if attempt.attempt_id not in att_list:
            att_list.append(attempt.attempt_id)
        map_updates: dict[str, dict[str, Any]] = {
            "opportunities_by_id": {opportunity.opportunity_id: opportunity.to_dict()},
            "opportunity_id_by_idempotency_key": {opportunity.idempotency_key: opportunity.opportunity_id},
            "context_snapshots_by_id": {context_snapshot.context_snapshot_id: context_snapshot.to_dict()},
            "constraint_results_by_id": {r.constraint_result_id: r.to_dict() for r in constraint_results},
            "constraint_refs_by_opportunity": {
                opportunity.opportunity_id: [r.constraint_result_id for r in constraint_results]
            },
            "capacity_snapshots_by_id": {capacity_snapshot.capacity_snapshot_id: capacity_snapshot.to_dict()},
            "accessibility_views_by_id": {accessibility_view.accessibility_id: accessibility_view.to_dict()},
            "attempts_by_id": {attempt.attempt_id: attempt.to_dict()},
            "attempt_ids_by_opportunity": {opportunity.opportunity_id: att_list},
        }
        self.store.commit_delta(
            lease=self.lease,
            capability=self.capability,
            map_updates=map_updates,
            journal_payload={
                "event_type": "DECISION_ATTEMPT_OPENED",
                "opportunity_id": opportunity.opportunity_id,
                "attempt_id": attempt.attempt_id,
                "context_snapshot_id": context_snapshot.context_snapshot_id,
                "route": attempt.route,
            },
        )
        self.store.flush_snapshot()

    def _stage_cognition_response(
        self,
        *,
        attempt: DecisionAttempt,
        validated_output: Mapping[str, Any],
        cog_response: CognitiveDecisionResponse,
        candidate_set: CandidateSet,
    ) -> None:
        self.store.commit_delta(
            lease=self.lease,
            capability=self.capability,
            map_updates={
                "staged_cognition_responses_by_attempt": {
                    attempt.attempt_id: {
                        "attempt_id": attempt.attempt_id,
                        "opportunity_id": attempt.decision_opportunity_id,
                        "candidate_set": candidate_set.to_dict(),
                        "validated_output": copy.deepcopy(dict(validated_output)),
                        "model_run_ref": cog_response.model_run_ref,
                        "model_id": cog_response.model_id,
                        "model_version": cog_response.model_version,
                    }
                }
            },
            journal_payload={
                "event_type": "COGNITION_RESPONSE_STAGED",
                "opportunity_id": attempt.decision_opportunity_id,
                "attempt_id": attempt.attempt_id,
                "model_run_ref": cog_response.model_run_ref,
            },
        )
        self.store.flush_snapshot()

    def _record_failed_attempt(
        self,
        *,
        opportunity: DecisionOpportunity,
        context_snapshot: DecisionContextSnapshot,
        constraint_results: Sequence[CandidateConstraintResult],
        capacity_snapshot: AgencyCapacitySnapshot,
        accessibility_view: AgencyAccessibilityView,
        attempt: DecisionAttempt,
        failure_class: str,
        model_run_ref: Optional[str],
        schema_detail: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Sections 6-8, 62-63: Record DecisionAttempt = FAILED and NEVER fabricate a NO_ACTION or DEFER DecisionRecord.

        ``schema_detail`` (v0.4 schema-repair) is the value-free reason the answer was refused: a
        field/rule pair from ``describe_schema_refusal``.  It rides in the journal payload and the
        returned dict; the persisted ``DecisionAttempt`` schema itself is deliberately unchanged,
        because an attempt record is a historical contract.
        """
        comp_iso = _iso(now())
        attempt.status = ATTEMPT_STATUS_FAILED
        attempt.completed_at = comp_iso
        attempt.failure_class = failure_class
        attempt.model_run_ref = model_run_ref
        attempt.failure_ref = f"dfail:{sha256_hex(f'{attempt.attempt_id}:{failure_class}:{comp_iso}')[:16]}"

        opp_copy = copy.deepcopy(opportunity)
        opp_copy.status = OPPORTUNITY_STATUS_FAILED

        state = self.store.load_state(_mutable=True)
        att_list = list(state["attempt_ids_by_opportunity"].get(opportunity.opportunity_id) or [])
        if attempt.attempt_id not in att_list:
            att_list.append(attempt.attempt_id)

        map_updates: dict[str, dict[str, Any]] = {
            "opportunities_by_id": {opp_copy.opportunity_id: opp_copy.to_dict()},
            "opportunity_id_by_idempotency_key": {opp_copy.idempotency_key: opp_copy.opportunity_id},
            "context_snapshots_by_id": {context_snapshot.context_snapshot_id: context_snapshot.to_dict()},
            "constraint_results_by_id": {r.constraint_result_id: r.to_dict() for r in constraint_results},
            "constraint_refs_by_opportunity": {
                opp_copy.opportunity_id: [r.constraint_result_id for r in constraint_results]
            },
            "capacity_snapshots_by_id": {capacity_snapshot.capacity_snapshot_id: capacity_snapshot.to_dict()},
            "accessibility_views_by_id": {accessibility_view.accessibility_id: accessibility_view.to_dict()},
            "attempts_by_id": {attempt.attempt_id: attempt.to_dict()},
            "attempt_ids_by_opportunity": {opp_copy.opportunity_id: att_list},
            "staged_cognition_responses_by_attempt": {attempt.attempt_id: "__ABSENT__"},
        }
        self.store.commit_delta(
            lease=self.lease,
            capability=self.capability,
            map_updates=map_updates,
            journal_payload=failure_journal_payload(
                opportunity_id=opp_copy.opportunity_id,
                attempt_id=attempt.attempt_id,
                failure_class=failure_class,
                failure_ref=attempt.failure_ref,
                model_run_ref=model_run_ref,
                schema_detail=schema_detail,
            ),
        )
        result = {
            "status": ATTEMPT_STATUS_FAILED,
            "opportunity": opp_copy.to_dict(),
            "decision_record": None,
            "attempt": copy.deepcopy(attempt),
            "failure_class": failure_class,
        }
        clean_detail = sanitize_schema_refusal(schema_detail)
        if clean_detail:
            # Same value-free record as the journal entry, so a caller (the cycle's own step
            # record, the co-presence contact) can report *why* without reading the journal.
            result["schema_refusal"] = clean_detail
        return result

    def _finalize_and_commit_decision(
        self,
        *,
        opportunity: DecisionOpportunity,
        candidate_set: CandidateSet,
        candidates_by_id: Mapping[str, CandidateRecord],
        context_snapshot: DecisionContextSnapshot,
        constraint_results: Sequence[CandidateConstraintResult],
        capacity_snapshot: AgencyCapacitySnapshot,
        accessibility_view: AgencyAccessibilityView,
        attempt: DecisionAttempt,
        validated_output: Mapping[str, Any],
        model_run_ref: Optional[str],
        model_id: Optional[str],
        model_version: Optional[str],
        supersedes_decision_ref: Optional[str],
        exploration_seed: Optional[int],
        live_capacity_override: Optional[Mapping[str, Any]],
        fault_hook: Optional[Callable[[str], None]],
    ) -> dict[str, Any]:
        if fault_hook is not None:
            fault_hook("before_freshness_recheck")

        # Sections 76-78: Freshness Recheck before committing DecisionRecord
        is_fresh, stale_reason = self.verify_snapshot_freshness(
            opportunity=opportunity,
            context_snapshot=context_snapshot,
            candidate_set=candidate_set,
            selected_candidate_ref=validated_output.get("selected_candidate_ref"),
            constraint_results=constraint_results,
            live_capacity_override=live_capacity_override,
            check_at=context_snapshot.captured_at,
        )

        if fault_hook is not None:
            fault_hook("after_freshness_recheck")

        if not is_fresh:
            self.stale_before_commit_count += 1
            comp_iso = _iso(now())
            attempt.status = ATTEMPT_STATUS_STALE
            attempt.completed_at = comp_iso
            attempt.failure_class = f"{FAILURE_CLASS_STALE_BEFORE_COMMIT}:{stale_reason}"
            attempt.failure_ref = f"dfail:{sha256_hex(f'{attempt.attempt_id}:{stale_reason}:{comp_iso}')[:16]}"

            opp_copy = copy.deepcopy(opportunity)
            opp_copy.status = OPPORTUNITY_STATUS_STALE

            state = self.store.load_state(_mutable=True)
            att_list = list(state["attempt_ids_by_opportunity"].get(opportunity.opportunity_id) or [])
            if attempt.attempt_id not in att_list:
                att_list.append(attempt.attempt_id)

            map_updates: dict[str, dict[str, Any]] = {
                "opportunities_by_id": {opp_copy.opportunity_id: opp_copy.to_dict()},
                "opportunity_id_by_idempotency_key": {opp_copy.idempotency_key: opp_copy.opportunity_id},
                "context_snapshots_by_id": {context_snapshot.context_snapshot_id: context_snapshot.to_dict()},
                "constraint_results_by_id": {r.constraint_result_id: r.to_dict() for r in constraint_results},
                "constraint_refs_by_opportunity": {
                    opp_copy.opportunity_id: [r.constraint_result_id for r in constraint_results]
                },
                "capacity_snapshots_by_id": {capacity_snapshot.capacity_snapshot_id: capacity_snapshot.to_dict()},
                "accessibility_views_by_id": {accessibility_view.accessibility_id: accessibility_view.to_dict()},
                "attempts_by_id": {attempt.attempt_id: attempt.to_dict()},
                "attempt_ids_by_opportunity": {opp_copy.opportunity_id: att_list},
                "staged_cognition_responses_by_attempt": {attempt.attempt_id: "__ABSENT__"},
            }
            self.store.commit_delta(
                lease=self.lease,
                capability=self.capability,
                map_updates=map_updates,
                journal_payload={
                    "event_type": "DECISION_ATTEMPT_STALE_BEFORE_COMMIT",
                    "opportunity_id": opp_copy.opportunity_id,
                    "attempt_id": attempt.attempt_id,
                    "stale_reason": stale_reason,
                },
            )
            return {
                "status": ATTEMPT_STATUS_STALE,
                "opportunity": opp_copy.to_dict(),
                "decision_record": None,
                "attempt": copy.deepcopy(attempt),
                "stale_reason": stale_reason,
            }

        # Build immutable DecisionRecord (Section 9)
        created_iso = context_snapshot.captured_at
        dec_hash_input = {
            "opportunity_id": opportunity.opportunity_id,
            "attempt_id": attempt.attempt_id,
            "decision": validated_output["decision"],
            "selected_candidate_ref": validated_output["selected_candidate_ref"],
            "snapshot_fingerprint": context_snapshot.snapshot_fingerprint,
            "supersedes_ref": supersedes_decision_ref,
        }
        decision_id = f"dec:{sha256_hex(canonical_json_line(dec_hash_input))[:16]}"

        attempt.status = ATTEMPT_STATUS_SUCCEEDED
        attempt.completed_at = created_iso

        decision_record = DecisionRecord(
            decision_id=decision_id,
            revision=1,
            subject_id=GLOBAL_SUBJECT_ID,
            decision_opportunity_ref=opportunity.opportunity_id,
            candidate_set_ref=candidate_set.candidate_set_id,
            candidate_refs=tuple(candidate_set.candidate_refs),
            current_activity_ref=context_snapshot.current_activity_ref,
            context_snapshot_ref=context_snapshot.context_snapshot_id,
            hard_constraint_result_refs=tuple(r.constraint_result_id for r in constraint_results),
            capacity_snapshot_ref=capacity_snapshot.capacity_snapshot_id,
            accessibility_ref=accessibility_view.accessibility_id,
            selected_candidate_ref=validated_output["selected_candidate_ref"],
            decision=validated_output["decision"],
            target_activity_ref=validated_output["target_activity_ref"],
            deferred_candidate_refs=tuple(validated_output["deferred_candidate_refs"]),
            reevaluation_hint_ref=validated_output.get("reevaluation_hint_ref"),
            reason_codes=tuple(validated_output["reason_codes"]),
            short_rationale=validated_output["short_rationale"],
            invalid_rationale_claim_dropped=bool(validated_output.get("invalid_rationale_claim_dropped", False)),
            decision_route=attempt.route,
            model_run_ref=model_run_ref,
            model_id=model_id,
            model_version=model_version,
            attempt_ref=attempt.attempt_id,
            policy_version=opportunity.policy_version,
            snapshot_fingerprint=context_snapshot.snapshot_fingerprint,
            supersedes_ref=supersedes_decision_ref,
            exploration_seed=exploration_seed,
            created_at=created_iso,
            # v0.4 stage A: carried through unchanged from the validated output, so the round can
            # record "later" and never has to guess what she said.
            later_item_proposal=validated_output.get("later_item"),
            future_item_actions=tuple(validated_output.get("future_item_actions") or ()),
            # v0.4 C: the practice this round chose to follow, and her own bounded verdict on it
            # (validated above against what the round really offered).  A method is document
            # context; the trial is an evaluation B's workspace records, never a success claim.
            method_ref=validated_output.get("method_ref"),
            method_trial=validated_output.get("method_trial"),
            sharing_action=validated_output.get("sharing_action"),
        )

        # Build per-candidate disposition audit table (Section 89 & 144)
        dispositions: dict[str, dict[str, Any]] = {}
        c_res_map = {r.candidate_ref: r for r in constraint_results}
        for cid in candidate_set.candidate_refs:
            cres = c_res_map.get(cid)
            exec_info = capacity_snapshot.candidate_executability.get(cid) or {}
            cand_obj = candidates_by_id.get(cid)
            if cres and cres.status == CONSTRAINT_STATUS_REJECTED:
                disp = "HARD_REJECTED"
                reasons = list(cres.reason_codes)
            elif cid == decision_record.selected_candidate_ref and decision_record.decision != DECISION_DEFER:
                disp = f"SELECTED_FOR_{decision_record.decision}"
                reasons = list(decision_record.reason_codes)
            elif cid in decision_record.deferred_candidate_refs:
                disp = "DEFERRED"
                reasons = list(decision_record.reason_codes)
            elif exec_info.get("executability") == EXEC_NOT_NOW:
                disp = "NOT_EXECUTABLE_NOW"
                reasons = list(exec_info.get("blocking_reasons") or [REASON_CAPACITY_UNAVAILABLE])
            else:
                disp = "NOT_SELECTED"
                reasons = list(decision_record.reason_codes)

            dispositions[cid] = {
                "candidate_id": cid,
                "candidate_kind": cand_obj.candidate_kind if cand_obj else None,
                "source_kind": cand_obj.source_kind if cand_obj else None,
                "source_ref": cand_obj.source_ref if cand_obj else None,
                "disposition": disp,
                "reason_codes": reasons,
                "constraint_result_ref": cres.constraint_result_id if cres else None,
                "executability": exec_info.get("executability"),
            }

        opp_copy = copy.deepcopy(opportunity)
        opp_copy.status = OPPORTUNITY_STATUS_DECIDED
        opp_copy.decision_ref = decision_id

        state = self.store.load_state(_mutable=True)
        att_list = list(state["attempt_ids_by_opportunity"].get(opportunity.opportunity_id) or [])
        if attempt.attempt_id not in att_list:
            att_list.append(attempt.attempt_id)

        map_updates = {
            "opportunities_by_id": {opp_copy.opportunity_id: opp_copy.to_dict()},
            "opportunity_id_by_idempotency_key": {opp_copy.idempotency_key: opp_copy.opportunity_id},
            "context_snapshots_by_id": {context_snapshot.context_snapshot_id: context_snapshot.to_dict()},
            "constraint_results_by_id": {r.constraint_result_id: r.to_dict() for r in constraint_results},
            "constraint_refs_by_opportunity": {
                opp_copy.opportunity_id: [r.constraint_result_id for r in constraint_results]
            },
            "capacity_snapshots_by_id": {capacity_snapshot.capacity_snapshot_id: capacity_snapshot.to_dict()},
            "accessibility_views_by_id": {accessibility_view.accessibility_id: accessibility_view.to_dict()},
            "attempts_by_id": {attempt.attempt_id: attempt.to_dict()},
            "attempt_ids_by_opportunity": {opp_copy.opportunity_id: att_list},
            "decisions_by_id": {decision_id: decision_record.to_dict()},
            "decision_id_by_opportunity": {opp_copy.opportunity_id: decision_id},
            "candidate_dispositions_by_decision": {decision_id: dispositions},
            "staged_cognition_responses_by_attempt": {attempt.attempt_id: "__ABSENT__"},
        }

        self.store.commit_delta(
            lease=self.lease,
            capability=self.capability,
            map_updates=map_updates,
            journal_payload={
                "event_type": "DECISION_COMMITTED",
                "opportunity_id": opp_copy.opportunity_id,
                "attempt_id": attempt.attempt_id,
                "decision_id": decision_id,
                "decision": decision_record.decision,
                "selected_candidate_ref": decision_record.selected_candidate_ref,
                "decision_route": decision_record.decision_route,
                "model_run_ref": decision_record.model_run_ref,
                "snapshot_fingerprint": decision_record.snapshot_fingerprint,
                "supersedes_ref": decision_record.supersedes_ref,
            },
            fault_hook=fault_hook,
        )

        return {
            "status": ATTEMPT_STATUS_SUCCEEDED,
            "opportunity": opp_copy.to_dict(),
            "decision_record": decision_record,
            "attempt": copy.deepcopy(attempt),
            "constraint_results": constraint_results,
            "capacity_snapshot": capacity_snapshot,
            "accessibility_view": accessibility_view,
        }

    # -- Crash Recovery Reconciliation (Sections 117-120) --------------------

    def reconcile_after_crash(self) -> dict[str, Any]:
        """Sections 117-120: Reconcile any interrupted attempts or staged cognition responses after restart.

        1. Recover WAL if needed (`recover_from_wal_if_needed`).
        2. For any `OPEN` attempt without a staged response (Crash during model call, Section 118):
           -> Mark attempt `FAILED` (`INTERRUPTED_DURING_COGNITION`), never pretending the model decided!
        3. For any `OPEN` attempt WITH a staged response (Crash after model response before commit, Section 119):
           -> Re-run `verify_snapshot_freshness`:
              - If still fresh: complete commit of `DecisionRecord`.
              - If stale: mark attempt `STALE` (`STALE_BEFORE_COMMIT`) and do not commit `DecisionRecord`.
        """
        AgencyDecisionAuthority.verify_capability(self.lease, self.capability)
        wal_recovered = self.store.recover_from_wal_if_needed()
        state = self.store.load_state()

        interrupted_attempts: list[str] = []
        committed_from_staged: list[str] = []
        stale_from_staged: list[str] = []

        for att_id, att_dict in list(state["attempts_by_id"].items()):
            if att_dict.get("status") != ATTEMPT_STATUS_OPEN:
                continue
            opp_id = str(att_dict["decision_opportunity_id"])
            opp_dict = state["opportunities_by_id"].get(opp_id)
            ctx_dict = state["context_snapshots_by_id"].get(att_dict["context_snapshot_ref"])
            if not opp_dict or not ctx_dict:
                continue

            opp = DecisionOpportunity.from_dict(opp_dict)
            ctx = DecisionContextSnapshot.from_dict(ctx_dict)
            attempt = DecisionAttempt.from_dict(att_dict)
            staged = state.get("staged_cognition_responses_by_attempt", {}).get(att_id)

            if staged is None:
                # Section 118: Crash during model call -> FAILED (INTERRUPTED_DURING_COGNITION)
                comp_iso = _iso(now())
                attempt.status = ATTEMPT_STATUS_FAILED
                attempt.completed_at = comp_iso
                attempt.failure_class = FAILURE_CLASS_INTERRUPTED_DURING_COGNITION
                attempt.failure_ref = f"dfail:{sha256_hex(f'{att_id}:interrupted:{comp_iso}')[:16]}"
                opp.status = OPPORTUNITY_STATUS_FAILED
                self.store.commit_delta(
                    lease=self.lease,
                    capability=self.capability,
                    map_updates={
                        "attempts_by_id": {att_id: attempt.to_dict()},
                        "opportunities_by_id": {opp_id: opp.to_dict()},
                    },
                    journal_payload={
                        "event_type": "RECOVERY_INTERRUPTED_COGNITION_FAILED",
                        "opportunity_id": opp_id,
                        "attempt_id": att_id,
                        "failure_class": FAILURE_CLASS_INTERRUPTED_DURING_COGNITION,
                    },
                )
                interrupted_attempts.append(att_id)
            else:
                # Section 119: Crash after model response before commit -> freshness revalidation required!
                cset = CandidateSet.from_dict(staged["candidate_set"])
                val_out = staged["validated_output"]
                c_refs = state["constraint_refs_by_opportunity"].get(opp_id) or []
                c_results = [
                    CandidateConstraintResult.from_dict(state["constraint_results_by_id"][cr])
                    for cr in c_refs
                    if cr in state["constraint_results_by_id"]
                ]
                cap_snap = None
                for cs in state["capacity_snapshots_by_id"].values():
                    if f"ctx:{ctx.context_snapshot_id}" in (cs.get("source_revisions") or []):
                        cap_snap = AgencyCapacitySnapshot.from_dict(cs)
                        break
                acc_view = None
                for av in state["accessibility_views_by_id"].values():
                    if av.get("opportunity_ref") == opp_id:
                        acc_view = AgencyAccessibilityView.from_dict(av)
                        break

                if cap_snap is not None and acc_view is not None:
                    cands_by_id: dict[str, CandidateRecord] = {}
                    if self.candidate_read_service is not None:
                        for cid in cset.candidate_refs:
                            cd = self.candidate_read_service.get_candidate(cid)
                            if cd:
                                cands_by_id[cid] = CandidateRecord.from_dict(cd)
                    res = self._finalize_and_commit_decision(
                        opportunity=opp,
                        candidate_set=cset,
                        candidates_by_id=cands_by_id,
                        context_snapshot=ctx,
                        constraint_results=c_results,
                        capacity_snapshot=cap_snap,
                        accessibility_view=acc_view,
                        attempt=attempt,
                        validated_output=val_out,
                        model_run_ref=staged.get("model_run_ref"),
                        model_id=staged.get("model_id"),
                        model_version=staged.get("model_version"),
                        supersedes_decision_ref=None,
                        exploration_seed=None,
                        live_capacity_override=None,
                        fault_hook=None,
                    )
                    if res["status"] == ATTEMPT_STATUS_SUCCEEDED and res["decision_record"] is not None:
                        committed_from_staged.append(res["decision_record"].decision_id)
                    else:
                        stale_from_staged.append(att_id)

        self.store.flush_snapshot()
        return {
            "wal_recovered": wal_recovered,
            "interrupted_attempts_failed": interrupted_attempts,
            "committed_from_staged": committed_from_staged,
            "stale_from_staged": stale_from_staged,
            "activity_mutations": self.activity_mutation_count,
        }


# ---------------------------------------------------------------------------
# AgencyDecisionReadService (Sections 144-146, 152)
# ---------------------------------------------------------------------------


class AgencyDecisionReadService:
    """Sections 144-146, 152: Read-only audit, provenance, and explainability queries for AG-1."""

    def __init__(
        self,
        store: AgencyDecisionStore,
        *,
        candidate_read_service: Optional[CandidateReadService] = None,
        decision_service: Optional[AgencyDecisionService] = None,
    ) -> None:
        self.store = store
        self.candidate_read_service = candidate_read_service
        # S7 Phase-B ruling 2: a read port must be pure. Keep only the numeric
        # snapshot the audit needs; never retain the writing service.
        self._cognition_call_count_at_construction = (
            int(decision_service.cognition_call_count)
            if decision_service is not None
            else 0
        )

    def get_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        """Section 144: get_decision(decision_id)."""
        st = self.store.load_state()
        rec = st["decisions_by_id"].get(decision_id)
        return copy.deepcopy(rec) if rec is not None else None

    def get_decision_attempts(self, opportunity_id: str) -> list[dict[str, Any]]:
        """Section 144: get_decision_attempts(opportunity_id)."""
        st = self.store.load_state()
        att_ids = st["attempt_ids_by_opportunity"].get(opportunity_id) or []
        return [
            copy.deepcopy(st["attempts_by_id"][aid])
            for aid in att_ids
            if aid in st["attempts_by_id"]
        ]

    def get_candidate_dispositions(self, decision_id: str) -> dict[str, dict[str, Any]]:
        """Section 144: get_candidate_dispositions(decision_id)."""
        st = self.store.load_state()
        if decision_id not in st["decisions_by_id"]:
            raise AgencyDecisionError(f"unknown decision_id={decision_id!r}")
        return copy.deepcopy(st["candidate_dispositions_by_decision"].get(decision_id) or {})

    def get_constraint_results(self, decision_id: str) -> list[dict[str, Any]]:
        """Section 144: get_constraint_results(decision_id)."""
        st = self.store.load_state()
        dec = st["decisions_by_id"].get(decision_id)
        if dec is None:
            raise AgencyDecisionError(f"unknown decision_id={decision_id!r}")
        refs = dec.get("hard_constraint_result_refs") or []
        return [
            copy.deepcopy(st["constraint_results_by_id"][r])
            for r in refs
            if r in st["constraint_results_by_id"]
        ]

    def get_capacity_snapshot(self, decision_id: str) -> Optional[dict[str, Any]]:
        """Section 144: get_capacity_snapshot(decision_id)."""
        st = self.store.load_state()
        dec = st["decisions_by_id"].get(decision_id)
        if dec is None:
            raise AgencyDecisionError(f"unknown decision_id={decision_id!r}")
        cap_ref = dec.get("capacity_snapshot_ref")
        if not cap_ref:
            return None
        return copy.deepcopy(st["capacity_snapshots_by_id"].get(cap_ref))

    def get_context_snapshot(self, ref: Optional[str]) -> Optional[dict[str, Any]]:
        """Read-only DecisionContextSnapshot lookup (AG-1 owned)."""
        if not ref:
            return None
        state = self.store.load_state(_mutable=True)
        snap = state["context_snapshots_by_id"].get(str(ref))
        return copy.deepcopy(snap) if snap is not None else None

    def get_opportunity(self, ref: Optional[str]) -> Optional[dict[str, Any]]:
        """Read-only DecisionOpportunity lookup (AG-1 owned)."""
        if not ref:
            return None
        state = self.store.load_state(_mutable=True)
        opp = state["opportunities_by_id"].get(str(ref))
        return copy.deepcopy(opp) if opp is not None else None

    def find_decision_id_by_opportunity(self, ref: Optional[str]) -> Optional[str]:
        """Read-only lookup of the Decision that belongs to an Opportunity."""
        if not ref:
            return None
        state = self.store.load_state(_mutable=True)
        found = state["decision_id_by_opportunity"].get(str(ref))
        return str(found) if found else None

    def list_failed_decisions(self) -> list[dict[str, Any]]:
        """Section 144: list_failed_decisions()."""
        st = self.store.load_state()
        failed = [
            copy.deepcopy(att)
            for att in st["attempts_by_id"].values()
            if att.get("status") == ATTEMPT_STATUS_FAILED
        ]
        failed.sort(key=lambda x: str(x["attempt_id"]))
        return failed

    def list_stale_attempts(self) -> list[dict[str, Any]]:
        """Section 144: list_stale_attempts()."""
        st = self.store.load_state()
        stale = [
            copy.deepcopy(att)
            for att in st["attempts_by_id"].values()
            if att.get("status") == ATTEMPT_STATUS_STALE
        ]
        stale.sort(key=lambda x: str(x["attempt_id"]))
        return stale

    def trace_decision_provenance(self, decision_id: str) -> dict[str, Any]:
        """Section 144: trace_decision_provenance(decision_id).

        Traces DecisionRecord -> DecisionOpportunity -> DecisionContextSnapshot ->
        HardConstraintResults -> AgencyCapacitySnapshot -> AgencyAccessibilityView ->
        CandidateSet & Candidate provenance.
        """
        st = self.store.load_state()
        dec = st["decisions_by_id"].get(decision_id)
        if dec is None:
            raise AgencyDecisionError(f"unknown decision_id={decision_id!r}")

        opp_ref = dec["decision_opportunity_ref"]
        ctx_ref = dec["context_snapshot_ref"]
        cap_ref = dec.get("capacity_snapshot_ref")
        acc_ref = dec.get("accessibility_ref")
        sel_ref = dec.get("selected_candidate_ref")

        selected_cand_provenance = None
        if sel_ref and self.candidate_read_service is not None:
            try:
                selected_cand_provenance = self.candidate_read_service.trace_candidate_provenance(sel_ref)
            except Exception:
                selected_cand_provenance = self.candidate_read_service.get_candidate(sel_ref)

        return {
            "decision_id": decision_id,
            "decision": dec["decision"],
            "decision_route": dec["decision_route"],
            "selected_candidate_ref": sel_ref,
            "reason_codes": list(dec.get("reason_codes") or []),
            "short_rationale": dec.get("short_rationale"),
            "snapshot_fingerprint": dec["snapshot_fingerprint"],
            "model_run_ref": dec.get("model_run_ref"),
            "supersedes_ref": dec.get("supersedes_ref"),
            "opportunity": copy.deepcopy(st["opportunities_by_id"].get(opp_ref)),
            "context_snapshot": copy.deepcopy(st["context_snapshots_by_id"].get(ctx_ref)),
            "constraint_results": self.get_constraint_results(decision_id),
            "capacity_snapshot": copy.deepcopy(st["capacity_snapshots_by_id"].get(cap_ref)) if cap_ref else None,
            "accessibility_view": copy.deepcopy(st["accessibility_views_by_id"].get(acc_ref)) if acc_ref else None,
            "candidate_dispositions": self.get_candidate_dispositions(decision_id),
            "selected_candidate_provenance": selected_cand_provenance,
        }

    def explain_decision(self, decision_id: Optional[str]) -> dict[str, Any]:
        """Sections 145-146: Honest explainability based strictly on DecisionRecord provenance.

        If no Candidate/Decision existed at that time, honestly states that no such choice was formed
        rather than confabulating '其实我一直想做，只是……'.
        """
        if not decision_id:
            return {
                "formed_decision": False,
                "explanation_code": "NO_DECISION_FORMED",
                "honest_summary": "当时没有形成那个选择。",
            }
        dec = self.get_decision(decision_id)
        if dec is None:
            return {
                "formed_decision": False,
                "explanation_code": "NO_DECISION_FORMED",
                "honest_summary": "当时没有形成那个选择。",
            }
        reasons = list(dec.get("reason_codes") or [])
        rationale = dec.get("short_rationale") or f"Decision={dec['decision']} ({','.join(reasons)})"
        return {
            "formed_decision": True,
            "decision_id": decision_id,
            "decision": dec["decision"],
            "selected_candidate_ref": dec.get("selected_candidate_ref"),
            "reason_codes": reasons,
            "honest_summary": rationale,
        }

    def get_shadow_metrics(self) -> dict[str, Any]:
        """Sections 107, 152-154: Diagnostic shadow telemetry without any 'agency_quality_score' or compliance KPI."""
        st = self.store.load_state()
        decisions = list(st["decisions_by_id"].values())
        attempts = list(st["attempts_by_id"].values())

        dec_dist: dict[str, int] = {k: 0 for k in sorted(VALID_DECISION_OUTPUTS)}
        route_dist: dict[str, int] = {ROUTE_RULE_RESOLVED: 0, ROUTE_COGNITIVE_REQUIRED: 0}
        for d in decisions:
            dec_dist[d["decision"]] = dec_dist.get(d["decision"], 0) + 1
            r = d["decision_route"]
            route_dist[r] = route_dist.get(r, 0) + 1

        hard_reject_dist: dict[str, int] = {}
        for cr in st["constraint_results_by_id"].values():
            if cr.get("status") == CONSTRAINT_STATUS_REJECTED:
                for rc in cr.get("reason_codes") or []:
                    hard_reject_dist[rc] = hard_reject_dist.get(rc, 0) + 1

        total_dec = len(decisions)
        total_att = len(attempts)
        failed_att = sum(1 for a in attempts if a.get("status") == ATTEMPT_STATUS_FAILED)
        stale_att = sum(1 for a in attempts if a.get("status") == ATTEMPT_STATUS_STALE)

        return {
            "total_opportunities": len(st["opportunities_by_id"]),
            "total_attempts": total_att,
            "total_decisions": total_dec,
            "route_distribution": route_dist,
            "decision_distribution": dec_dist,
            "hard_rejection_distribution": hard_reject_dist,
            "no_action_rate": (dec_dist.get(DECISION_NO_ACTION, 0) / total_dec) if total_dec else 0.0,
            "defer_rate": (dec_dist.get(DECISION_DEFER, 0) / total_dec) if total_dec else 0.0,
            "model_failures": failed_att,
            "stale_before_commit_count": stale_att,
            "stale_before_commit_rate": (stale_att / total_att) if total_att else 0.0,
            "cognition_calls": self._cognition_call_count_at_construction,
        }


# ---------------------------------------------------------------------------
# DecisionReplayVerifier (Sections 101-102, 134 N01-N04)
# ---------------------------------------------------------------------------


class DecisionReplayVerifier:
    """Sections 101-102, 134: Deterministic replay verifier with 0 LLM calls and 0 side effects.

    - Rule-resolved decisions are re-evaluated deterministically (`0 LLM`) and verified to match the stored DecisionRecord.
    - Cognitive decisions NEVER re-invoke the LLM on replay (`0 LLM`); they reconstruct/verify directly from the stored
      `DecisionRecord` + `model_run_ref` + journal audit trail.
    """

    @staticmethod
    def replay_decision(
        *,
        store: AgencyDecisionStore,
        decision_id: str,
        cognition_adapter: Optional[FakeCognitionAdapter] = None,
    ) -> dict[str, Any]:
        calls_before = cognition_adapter.call_count if cognition_adapter is not None else 0
        state = store.load_state()
        stored_dict = state["decisions_by_id"].get(decision_id)
        if stored_dict is None:
            raise AgencyDecisionError(f"cannot replay unknown decision_id={decision_id!r}")

        stored_rec = DecisionRecord.from_dict(stored_dict)
        opp_dict = state["opportunities_by_id"][stored_rec.decision_opportunity_ref]
        ctx_dict = state["context_snapshots_by_id"][stored_rec.context_snapshot_ref]
        cap_dict = state["capacity_snapshots_by_id"][stored_rec.capacity_snapshot_ref]

        opp = DecisionOpportunity.from_dict(opp_dict)
        opp.status = OPPORTUNITY_STATUS_OPEN
        ctx = DecisionContextSnapshot.from_dict(ctx_dict)
        cap = AgencyCapacitySnapshot.from_dict(cap_dict)

        if stored_rec.decision_route == ROUTE_RULE_RESOLVED:
            # Re-run deterministic router on captured snapshot (0 LLM)
            allowed_cands: list[CandidateRecord] = []
            for cr_id in stored_rec.hard_constraint_result_refs:
                cr = state["constraint_results_by_id"][cr_id]
                if cr["status"] == CONSTRAINT_STATUS_ALLOWED:
                    cid = cr["candidate_ref"]
                    exec_info = cap.candidate_executability.get(cid) or {}
                    # Build minimal CandidateRecord stub for deterministic rule router verification
                    allowed_cands.append(
                        CandidateRecord(
                            candidate_id=cid,
                            candidate_kind=CANDIDATE_KIND_RESPOND_USER_REQUEST,
                            source_kind=SOURCE_USER_REQUEST,
                            source_ref="ureq:replay_stub",
                            user_event_ref="ureq:replay_stub",
                            availability_status=CANDIDATE_STATUS_OPEN,
                            created_at=ctx.captured_at,
                            observed_at=ctx.captured_at,
                            recorded_at=ctx.captured_at,
                            causal_parent_refs=["ureq:replay_stub"],
                            provenance_refs=["ureq:replay_stub"],
                            idempotency_key=f"idem:{cid}",
                        )
                    )
            router = DecisionPolicyRouter(
                all_capacity_blocked_decision=(
                    DECISION_DEFER if stored_rec.decision == DECISION_DEFER else DECISION_NO_ACTION
                )
            )
            routed = router.route_opportunity(
                opportunity=opp,
                allowed_candidates=allowed_cands,
                context_snapshot=ctx,
                capacity_snapshot=cap,
            )
            rule_out = routed["rule_output"] or {}
            deterministic_match = (
                routed["route"] == ROUTE_RULE_RESOLVED
                and rule_out.get("decision") == stored_rec.decision
                and rule_out.get("selected_candidate_id") == stored_rec.selected_candidate_ref
            )
        else:
            # Section 101-102: Cognitive Decision replay NEVER re-calls the model; reads stored DecisionRecord / model_run_ref
            deterministic_match = (
                stored_rec.model_run_ref is not None
                and stored_rec.snapshot_fingerprint == ctx.snapshot_fingerprint
            )

        calls_after = cognition_adapter.call_count if cognition_adapter is not None else 0
        cognition_calls_during_replay = calls_after - calls_before

        return {
            "decision_id": decision_id,
            "decision_route": stored_rec.decision_route,
            "deterministic_match": deterministic_match,
            "replayed_decision": stored_rec.to_dict(),
            "model_run_ref": stored_rec.model_run_ref,
            "replay_cognition_calls": cognition_calls_during_replay,
            "replay_activity_mutations": 0,
            "replay_action_creations": 0,
            "side_effects": 0,
        }


__all__ = [
    "AGENCY_ENABLED_ENV",
    "ATTEMPT_STATUS_CANCELLED",
    "ATTEMPT_STATUS_FAILED",
    "ATTEMPT_STATUS_OPEN",
    "ATTEMPT_STATUS_STALE",
    "ATTEMPT_STATUS_SUCCEEDED",
    "AccessibilityBuilder",
    "AgencyAccessibilityView",
    "AgencyCapacitySnapshot",
    "AgencyCognitionAdapter",
    "AgencyDecisionAuthority",
    "AgencyDecisionCapability",
    "AgencyDecisionError",
    "AgencyDecisionJournal",
    "AgencyDecisionReadService",
    "AgencyDecisionService",
    "AgencyDecisionStore",
    "CAPACITY_AVAILABLE",
    "CAPACITY_LIMITED",
    "CAPACITY_UNAVAILABLE",
    "CAPACITY_UNKNOWN",
    "CONSTRAINT_STATUS_ALLOWED",
    "CONSTRAINT_STATUS_REJECTED",
    "CandidateConstraintResult",
    "CapacityEvaluator",
    "CognitionAdapterError",
    "CognitiveDecisionRequest",
    "CognitiveDecisionResponse",
    "DECISION_ABANDON",
    "DECISION_CONTINUE",
    "DECISION_DEFER",
    "DECISION_NO_ACTION",
    "DECISION_PAUSE",
    "DECISION_RESUME",
    "DECISION_START",
    "DECISION_WAIT",
    "DecisionAttempt",
    "DecisionContextSnapshot",
    "DecisionImmutabilityError",
    "DecisionOpportunity",
    "DecisionOutputValidator",
    "DecisionPolicyRouter",
    "DecisionReasonCodeRegistry",
    "DecisionRecord",
    "DecisionReplayVerifier",
    "DecisionValidationError",
    "EXEC_NOT_NOW",
    "EXEC_NOW",
    "EXEC_UNKNOWN",
    "EXEC_WITH_LIMITS",
    "FAILURE_CLASS_ATOMIC_VIOLATION",
    "FAILURE_CLASS_CANDIDATE_EXPLOSION",
    "FAILURE_CLASS_FORBIDDEN_HIDDEN_REASONING",
    "FAILURE_CLASS_FORBIDDEN_UNIVERSAL_UTILITY",
    "FAILURE_CLASS_HARD_CONSTRAINT_VIOLATION",
    "FAILURE_CLASS_ILLEGAL_DECISION_ENUM",
    "FAILURE_CLASS_ILLEGAL_DECISION_MATRIX",
    "FAILURE_CLASS_INTERRUPTED_DURING_COGNITION",
    "FAILURE_CLASS_INVALID_JSON",
    "FAILURE_CLASS_INVALID_SCHEMA",
    "FAILURE_CLASS_INVENTED_CANDIDATE",
    "FAILURE_CLASS_MODEL_REFUSAL",
    "FAILURE_CLASS_MODEL_TIMEOUT",
    "FAILURE_CLASS_STALE_BEFORE_COMMIT",
    "FAILURE_CLASS_UNKNOWN_REASON_CODE",
    "FORBIDDEN_DECISION_OUTPUTS",
    "FORBIDDEN_OPPORTUNITY_TRIGGERS",
    "FakeCognitionAdapter",
    "ForbiddenPeriodicTickError",
    "ForbiddenUnobservedContextError",
    "HARD_REJECT_COMMITMENT_CONFLICT",
    "HARD_REJECT_IRREVERSIBLE_CONFLICT",
    "HARD_REJECT_PERMISSION_DENIED",
    "HARD_REJECT_PHYSICALLY_IMPOSSIBLE",
    "HARD_REJECT_POLICY_FORBIDDEN",
    "HARD_REJECT_STALE_SOURCE",
    "HARD_REJECT_TARGET_INVALID",
    "HARD_REJECT_TIME_WINDOW_CLOSED",
    "HardConstraintEvaluator",
    "HiddenReasoningForbiddenError",
    "MAX_COGNITIVE_CANDIDATES",
    "MissingConstraintAuthorityError",
    "OPPORTUNITY_STATUS_DECIDED",
    "OPPORTUNITY_STATUS_FAILED",
    "OPPORTUNITY_STATUS_OPEN",
    "OPPORTUNITY_STATUS_STALE",
    "OPPORTUNITY_STATUS_SUPERSEDED",
    "POLICY_VERSION_AG1_V1",
    "REASON_ABANDON_NONTERMINAL_CHOSEN",
    "REASON_ATOMIC_IN_PROGRESS",
    "REASON_CAPACITY_LIMITED",
    "REASON_CAPACITY_UNAVAILABLE",
    "REASON_CAPACITY_UNKNOWN_DEFERRED",
    "REASON_COMMITMENT_DEFERRED_BY_CAPACITY",
    "REASON_COMMITMENT_FEASIBLE",
    "REASON_CONTINUITY_PREFERRED",
    "REASON_CURRENT_ACTIVITY_CONTINUITY",
    "REASON_GOAL_ATTENTION_DEFERRED",
    "REASON_GOAL_STEP_ACTIONABLE",
    "REASON_KNOWN_BLOCKING_WAIT",
    "REASON_NO_ACTION_CHOSEN",
    "REASON_NO_LEGAL_CANDIDATE",
    "REASON_OPEN_ENDED_CONTINUATION",
    "REASON_PURE_CONSUMPTION_CHOSEN",
    "REASON_REQUEST_CURRENTLY_FEASIBLE",
    "REASON_RESOURCE_LIMIT",
    "REASON_RESUME_ELIGIBLE_READY",
    "REASON_SAFE_BOUNDARY_PAUSE_RECORDED",
    "REASON_SWITCH_COST_ACCEPTABLE",
    "REASON_SWITCH_COST_HIGH",
    "REASON_TIME_WINDOW_CLOSING",
    "REASON_TIMING_NOT_APPROPRIATE",
    "REASON_USER_REQUEST_DEFERRED",
    "REASON_USER_REQUEST_VALID",
    "REASON_WORLD_OPPORTUNITY_OBSERVED",
    "ROUTE_COGNITIVE_REQUIRED",
    "ROUTE_NO_DECISION",
    "ROUTE_RULE_RESOLVED",
    "TRIGGER_CANDIDATE_SET_CHANGED",
    "TRIGGER_CURRENT_ACTIVITY_ENDED",
    "TRIGGER_CURRENT_ACTIVITY_RECONSIDERATION",
    "TRIGGER_EXPLICIT_REEVALUATION",
    "TRIGGER_RECOVERY_RECONCILIATION",
    "TRIGGER_RESUME_ELIGIBILITY_OPENED",
    "TRIGGER_USER_REQUEST_EVENT",
    "TRIGGER_WORLD_OPPORTUNITY_EVENT",
    "UnauthorizedAgencyMutationError",
    "UniversalUtilityForbiddenError",
    "VALID_CAPACITY_STATES",
    "VALID_DECISION_OUTPUTS",
    "VALID_DECISION_REASON_CODES",
    "VALID_DECISION_ROUTES",
    "VALID_OPPORTUNITY_STATUSES",
    "VALID_OPPORTUNITY_TRIGGERS",
    "is_agency_enabled",
]
