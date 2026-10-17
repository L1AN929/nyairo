"""v0.2 WP-01: the real autonomous life cycle entry.

What was missing (WP-00 evidence)
---------------------------------
``LifeIntegrationCoordinator.run_decision_and_adoption_cycle`` (``life_integration_ag2.py:4526``)
is the only code path that reaches a canonical Activity write, and the whole repository called it
from exactly one place: an isolated diagnostic CLI (``components/alpha/chiyo/cli.py:400``).  There
was no production entry, and no ``tick``/``run_once``/``cycle`` of any kind in ``components/life``.
This module is that entry.

What it is
----------
One call = at most one real life step.  It:

1. refuses immediately when the feature is off (zero owner reads, zero model calls);
2. refuses when the day's round budget is spent, and says so;
3. asks the real owners whether there is anything at all to evaluate -- *before* spending a
   single cognition call.  "Nothing to do" is a first-class answer, not an error;
4. runs the existing AG-0 -> AG-1 -> AG-2 cycle (no parallel decision path);
5. when a Decision is adopted, performs the single narrow step through the ManagedArtifact
   narrow port and records the owner's real receipt;
6. returns to IDLE.  It never re-wakes itself, and it never recurses.

Timers are not decision triggers
--------------------------------
AG-1 permanently forbids ``PERIODIC_LLM_TICK`` / ``CRON_TICK`` / ``IDLE_TIMER_POLL`` as
opportunity triggers (``agency_decision_ag1.py:242``, enforced at ``:967``).  So a timer may
only decide *that it is worth asking*, and the question is always asked as
``EXPLICIT_REEVALUATION`` -- one of the eight legal triggers.  A timer never writes a diary
entry, a goal or a message, and a timer with no material stops here without any model call.

Honest limits of this revision
------------------------------
* The narrow step is executed through the port **after** adoption, not yet through AR-0: the
  production action adapters are all fail-closed (``life_runtime_production.py:112``-``:139``) and
  wiring a real adapter for the artifact verbs is WP-05.  ``execute_initial_step`` is therefore
  passed as ``False`` so nothing pretends an AR-0 action happened.  The effect is still real, and
  its receipt comes from the owner, not from this module.
* ``DeterministicContinuationWriter`` is a **placeholder** content writer used to prove the
  mechanism end to end.  It is not the subject's writing and is not presented as such; the
  model-backed writer is the next step, and its output goes through the same port and the same
  compare-and-swap.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping as AbcMapping
import json
import sys
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

import agency_decision_ag1 as ag1
import life_integration_ag2 as ag2
from artifact_continuation_source import ArtifactContinuationSource
from life_artifact_port import (
    ArtifactOutcome,
    ArtifactOwnerRefused,
    ArtifactResumePoint,
    EffectState,
    LifeArtifactPort,
    LifeArtifactUnavailable,
    operation_key_for,
)
from action_reality_ledger import ACTION_KIND_FILE, ACTION_KIND_WORLD
from managed_artifact_action_adapter import OP_COMMIT_VERSION
import life_pause
import life_request_budget
#: D (life-v1): the situation read (A) and the continuity read (C) are *injected*; this
#: module only consumes them.  Neither one authorizes anything.
import life_situation_view as lsv
#: LR-5 owns progress/completion; the cycle only *describes* contracts for it.
import activity_progress_completion_lr5 as lr5
#: D (life-v1): how the operator's reading material marks its pages.
READING_PAGE_MARKER = "%%PAGE%%"


def lr5_module() -> Any:
    """The LR-5 owner module (kept behind a function for the lazy call sites)."""
    return lr5

#: Master switch.  Autonomous life is OFF unless the operator turns it on.
ENV_AUTONOMOUS_ENABLED = "CHIYO_AUTONOMOUS_LIFE_ENABLED"
#: Rounds per day the cycle may even *consider* running.  A round that stops before any model
#: call does not spend budget.
ENV_MAX_ROUNDS_PER_DAY = "CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY"
DEFAULT_MAX_ROUNDS_PER_DAY = 4
#: E09.2 reference values.  They are limits, not a personality rhythm: the point is
#: that a long unattended day has a bounded cost, not that she acts this often.
DEFAULT_MAX_COGNITION_CALLS_PER_DAY = 12
DEFAULT_MAX_ACTIONS_PER_DAY = 8
#: E09.1 low-frequency check window, in minutes.
DEFAULT_CHECK_INTERVAL_MINUTES = 120
ENV_MAX_COGNITION_CALLS_PER_DAY = "CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY"
ENV_MAX_ACTIONS_PER_DAY = "CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY"
ENV_CHECK_INTERVAL_MINUTES = "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"

#: The only trigger a timer may produce.  See the module docstring.
TRIGGER_IDLE_WINDOW = ag1.TRIGGER_EXPLICIT_REEVALUATION
#: Trigger used when a real inbound event asked for the evaluation.
TRIGGER_USER_EVENT = ag1.TRIGGER_USER_REQUEST_EVENT
#: Trigger used when a previously waiting activity became eligible again.
TRIGGER_RESUME_OPENED = ag1.TRIGGER_RESUME_ELIGIBILITY_OPENED
#: Trigger used right after a restart, before anything else is considered.
TRIGGER_RECOVERY = ag1.TRIGGER_RECOVERY_RECONCILIATION

BUDGET_FILENAME = "autonomous_budget.json"
JOURNAL_FILENAME = "life_step_journal.jsonl"
#: D (life-v1): where C's read watermark is persisted.  It lives in the cycle's own state
#: root, which is the existing run-state location -- no second store is introduced.
CONTINUITY_WATERMARK_FILENAME = "continuity_watermark.json"

#: B6-5: the one World verb this cycle issues.  POSE needs a second parameter
#: (orientation) that a single observed target cannot supply, so it is not offered.
MOVE_VERB = "MOVE"

#: W05R-R1: the place fact the World progress evidence carries, and the ``world_fact``
#: namespace LR-5 already owns for it.  ``WORLD_PLACE_FACT_KEY`` is the observed-fact
#: key the completion contract matches on, so the criterion is satisfied only by the
#: World owner's own canonical observation of the place the Decision chose.
WORLD_PLACE_FACT_PREFIX = "world_fact:place:"
WORLD_PLACE_FACT_KEY = "world_place_ref"

#: I: the same shape for the *other* World verb this loop issues.  ``CONSUME`` has no
#: arrival to confirm, so its completion criterion is the World owner's own consumption
#: receipt for the exact object instance the Decision chose -- never "the object is gone",
#: which is precisely the attribution F's substrate receipt exists to prevent.
PLACE_REF_PREFIX = "place:"
WORLD_OBJECT_REF_PREFIX = "object:"
WORLD_CONSUME_FACT_KEY = "world_object_consumed_ref"
CONSUME_VERB = "CONSUME"

#: I-r1: a non-productive stay (the park bench, a quiet stop on the way) is not a World
#: verb and not a resting/nutrition effect.  It is a bounded Life Activity whose only
#: World-side inputs are the canonical observation that she really is in that place, the W
#: catalog's own opening/affordance fact, and the cycle's own clock.  ``stay:`` is the
#: target namespace that carries the whole plan, ``STAY_ACTIVITY_KIND`` is the Activity
#: shape, and ``WORLD_STAY_FACT_KEY`` is the observed-fact key whose value is emitted only
#: once the declared stretch of time has really elapsed *and* the World still puts her
#: there -- so "she stood there" can never be claimed by a NO_ACTION round or a time skip
#: that no owner confirmed.
STAY_REF_PREFIX = "stay:"
STAY_ACTIVITY_KIND = "STAY_AT_OBSERVED_PLACE"
WORLD_STAY_FACT_PREFIX = "world_fact:stay:"
WORLD_STAY_FACT_KEY = "world_stay_ref"
STAY_PHASE_START = "START"
STAY_PHASE_END = "END"
STAY_PHASE_ONGOING = "ONGOING"
#: I-r3: a stay can also be *left by choice* before its declared stretch has run out.  The
#: phase has its own value so "she left early" can never be read as "the declared time was
#: served": nothing about such a round submits ``WORLD_STAY_FACT_KEY``, and the record says
#: ``completed: false`` in as many words.
STAY_PHASE_ENDED_EARLY = "ENDED_EARLY"
#: The exit a Decision may choose for a stay it is in.  Paused is reported as paused; this
#: cycle never resumes a paused stay as a stay (elapsed time would count being elsewhere).
STAY_EXIT_ABANDON = "ABANDON"
STAY_EXIT_PAUSE = "PAUSE"

#: Outcome vocabulary.  Each one is a distinct real thing; they are never merged.
OUTCOME_DISABLED = "DISABLED"
OUTCOME_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
OUTCOME_NO_MATERIAL = "NO_MATERIAL"
OUTCOME_NO_ACTION = "NO_ACTION"
OUTCOME_DEFERRED = "DEFERRED"
OUTCOME_PAUSED = "PAUSED"
OUTCOME_ABANDONED = "ABANDONED"
OUTCOME_DECISION_FAILED = "DECISION_FAILED"
OUTCOME_ADOPTION_REJECTED = "ADOPTION_REJECTED"
OUTCOME_ACTED = "ACTED"
OUTCOME_STEP_REFUSED = "STEP_REFUSED"
OUTCOME_STEP_UNKNOWN = "STEP_UNKNOWN"
OUTCOME_ALREADY_DONE = "ALREADY_DONE"
OUTCOME_STEP_UNSETTLED = "STEP_UNSETTLED"
OUTCOME_IN_TRANSIT = "IN_TRANSIT"
OUTCOME_PAUSED_BY_OPERATOR = "PAUSED_BY_OPERATOR"

#: Reason code used when a step is not attempted because the work already moved.
REASON_HEAD_ADVANCED = "ARTIFACT_HEAD_ADVANCED"
#: Reason code used when the owner refuses the write.
REASON_OWNER_REFUSED = "ARTIFACT_OWNER_REFUSED"


def _compact_column(column: Any) -> Optional[dict[str, Any]]:
    """One capacity column, without the read-only bookkeeping fields.

    The round record is what an operator reads, so it keeps the owner's answer
    (state, reference, source, reason, freshness, evidence) and drops the constants.
    """
    if not isinstance(column, Mapping):
        return None
    keys = ("state", "ref", "source", "reason_code", "freshness", "observed_at",
            "observed_body_id", "observed_revision", "evidence", "identity_bound",
            "revision_bound", "observation_time_bound", "owner_reason_code",
            "ledger_state", "utc_day", "run_id", "utc_day_calls", "utc_day_max",
            "run_window_calls", "run_window_max", "bound", "owner_asserted")
    return {key: column.get(key) for key in keys if key in column}


def _compact_knowledge(knowledge: Any) -> Optional[dict[str, Any]]:
    """The knowledge column's counts and validity labels, not its bookkeeping."""
    if not isinstance(knowledge, Mapping):
        return None
    return {
        "counts": knowledge.get("counts"),
        "current_refs": knowledge.get("current_refs"),
        "not_current": knowledge.get("not_current"),
        "declared_bound_only": knowledge.get("declared_bound_only"),
        "local_threshold_used": knowledge.get("local_threshold_used"),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _seconds_between(started_at: Any, ended_at: Any) -> Optional[int]:
    """Whole seconds from one ISO instant to another, or ``None`` when unreadable.

    Both ends come from explicit timestamps (the owner's own checkpoint and the cycle's own
    ``observed_at``), so this is arithmetic on declared facts -- never a guess about how long
    something "felt".
    """

    def _parse(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment

    start, end = _parse(started_at), _parse(ended_at)
    if start is None or end is None:
        return None
    return int((end - start).total_seconds())


def stay_plan(target: Any) -> Optional[dict[str, Any]]:
    """Split a ``stay:<place>:<descriptor>:<seconds>`` target back into its plan.

    The plan travels inside the target the Decision selected, so the step spec, the check
    that the declared end has really come, and the report all read the *same* declaration;
    nothing is re-derived from a model, a feeling or the round budget.
    """

    text = str(target or "").strip()
    if not text.startswith(STAY_REF_PREFIX):
        return None
    parts = text[len(STAY_REF_PREFIX):].split(":")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    try:
        seconds = int(parts[2])
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return {"place": parts[0], "descriptor": parts[1], "seconds": seconds,
            "target_ref": text}


def _epoch_seconds(value: Any) -> int:
    """Whole epoch seconds of one ISO instant, or ``0`` when it cannot be read.

    Used only to build a structured, offset-free reference (the reference vocabulary refuses
    the ``+hh:mm`` offset), never to compute a duration.
    """

    text = str(value or "").strip()
    if not text:
        return 0
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _fact_token(value: Any) -> str:
    """One place/status/reason spelling that survives AG-1's bounded fact projection.

    ``observed_fact_keys`` reaches the model through a projection that keeps only ASCII
    alphanumerics and underscores (``agency_decision_ag1.py:3759`` and the prompt side at
    ``agency_cognition_adapter.py:203``), so a fact that is worth reading has to be spelled
    that way.  Nothing else is changed: the token is a *spelling*, and the value beside it
    stays exactly what the World owner said.
    """

    text = "".join(ch if (ch.isascii() and (ch.isalnum() or ch == "_")) else "_"
                   for ch in str(value or ""))
    return text.strip("_")[:24] or "unknown"


def journey_module() -> Any:
    """``chiyo_bundle.world_journey`` by either import spelling (W05).

    The resident puts the bundle directory itself on ``sys.path`` while the tests put the
    repository root there, so both spellings are real.  Nothing here writes anything.
    """

    import importlib
    for name in ("chiyo_bundle.world_journey", "world_journey"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("chiyo_bundle.world_journey")

def _int_setting(source: Mapping[str, str], name: str, default: int) -> int:
    """One integer setting, with an unusable value falling back to the reference.

    A malformed limit must not disable the limit: falling back to the documented
    default keeps the budget bounded instead of silently unbounded.
    """

    try:
        return int(str(source.get(name) or default).strip())
    except (TypeError, ValueError):
        return int(default)


class AutonomousLifeError(RuntimeError):
    """A real, non-recoverable condition inside one autonomous round.

    Raised only when the round cannot be honestly completed -- for example a committed
    Decision that names no observed destination.  It is never used to paper over a
    refusal that should be reported as one.
    """


class ContinuationWriter(Protocol):
    """Produces the next version's text from the current head.  Pluggable on purpose."""

    def next_content(self, point: ArtifactResumePoint, *, observed_at: str) -> str: ...


class DeterministicContinuationWriter:
    """PLACEHOLDER writer: appends one dated line derived from the real head.

    This exists so the mechanism (adopt -> commit -> resume) can be proven with a real effect and
    zero model cost.  It is **not** the subject's writing, and it must not be described as such.
    """

    def __init__(self, note: str = "（继续了一小段）") -> None:
        self.note = str(note)

    def next_content(self, point: ArtifactResumePoint, *, observed_at: str) -> str:
        current = point.content or ""
        stamp = observed_at[:19].replace("T", " ")
        line = f"\n\n<!-- v{point.version_number or 0} -> next @ {stamp} -->\n{self.note}\n"
        return current + line


@dataclass
class StepReceipt:
    """The real, owner-confirmed result of one narrow step."""

    observed_at: str
    artifact_id: str
    expected_head: Optional[str]
    operation_key: str
    effect_state: str
    status: str
    reason: str = ""
    version_id: Optional[str] = None
    version_number: Optional[int] = None
    head_version_id: Optional[str] = None
    correlation_id: Optional[str] = None
    decision_id: Optional[str] = None
    adoption_id: Optional[str] = None
    activity_ref: Optional[str] = None
    detail: str = ""

    @classmethod
    def from_outcome(cls, outcome: ArtifactOutcome, **extra: Any) -> "StepReceipt":
        # Pop unconditionally: using ``or`` here would short-circuit and leave the key inside
        # ``extra``, which then collides with the explicit keyword argument below.
        artifact_id = extra.pop("artifact_id", None)
        operation_key = extra.pop("operation_key", None)
        expected_head = extra.pop("expected_head", None)
        fields: dict[str, Any] = dict(
            observed_at=_utc_now_iso(),
            artifact_id=str(outcome.artifact_id or artifact_id or ""),
            expected_head=expected_head,
            operation_key=str(outcome.operation_key or operation_key or ""),
            effect_state=outcome.effect_state,
            status=outcome.status,
            reason=outcome.reason,
            version_id=outcome.version_id,
            version_number=outcome.version_number,
            head_version_id=outcome.head_version_id,
            detail=outcome.detail,
        )
        fields.update(extra)
        return cls(**fields)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class RoundBudget:
    """A small persisted per-day counter.  Exhaustion never destroys state.

    Three counters live in the same file because they are three different things:
    rounds *considered*, cognition calls actually made (a decision AND a content
    generation both count), and real actions executed.  Keeping them apart is what
    stops "she thought about it" from being reported as "she did something", and
    what makes a per-wake permission check possible.
    """

    def __init__(self, state_root: Path | str, *, max_rounds_per_day: int,
                 max_cognition_calls_per_day: int = DEFAULT_MAX_COGNITION_CALLS_PER_DAY,
                 max_actions_per_day: int = DEFAULT_MAX_ACTIONS_PER_DAY,
                 requests: Any = None) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / BUDGET_FILENAME
        self.max_rounds_per_day = max(0, int(max_rounds_per_day))
        self.max_cognition_calls_per_day = max(0, int(max_cognition_calls_per_day))
        self.max_actions_per_day = max(0, int(max_actions_per_day))
        #: W05R: the persisted, atomically reserved request ledger.  When it is wired it
        #: owns the cognition/request counters outright (see ``cognition_used_today``).
        self.requests = requests

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _today(self) -> dict[str, Any]:
        """Today's counters.  A stale day reads as zero without rewriting the file.

        Reading must not reset anything: a restart on the same day keeps the spend,
        which is exactly what E09.2 requires.
        """

        data = self._load()
        if str(data.get("day")) != _local_day():
            return {}
        return data

    def _write(self, **changes: Any) -> None:
        data = self._today()
        data.update(changes)
        data["day"] = _local_day()
        data["updated_at"] = _utc_now_iso()
        data["max_rounds_per_day"] = self.max_rounds_per_day
        data["max_cognition_calls_per_day"] = self.max_cognition_calls_per_day
        data["max_actions_per_day"] = self.max_actions_per_day
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def spent_today(self) -> int:
        return int(self._today().get("spent") or 0)

    def remaining(self) -> int:
        return max(0, self.max_rounds_per_day - self.spent_today())

    def spend(self) -> int:
        """Count one round.  Returns the new total for today."""
        spent = self.spent_today() + 1
        self._write(spent=spent)
        return spent

    # -- cognition calls (decisions, content generation AND retries) ------------
    #
    # W05R: this counter used to be written *after* the round finished, in the same
    # file, from the round's own ``llm_calls`` -- so a content generation was counted
    # per process and a crash between the HTTP call and the accounting lost the spend
    # entirely.  When a persisted ``LifeRequestBudget`` is wired, it is the single
    # source of truth: every attempt is reserved before it is sent, and reading it
    # here can never double-count.
    def cognition_used_today(self) -> int:
        if self.requests is not None:
            view = self.requests.view()
            if view.get("utc_day_calls") is None:
                # A corrupt ledger is not "zero spent": report the ceiling as reached.
                return self.max_cognition_calls_per_day
            return int(view["utc_day_calls"])
        return int(self._today().get("cognition_used") or 0)

    def cognition_remaining(self) -> int:
        return max(0, self.max_cognition_calls_per_day - self.cognition_used_today())

    def note_cognition(self, count: int = 1) -> int:
        if self.requests is not None:
            # The reservation already happened at the HTTP boundary.  Counting again
            # here is exactly the double count W05R forbids, so this is a read.
            return self.cognition_used_today()
        used = self.cognition_used_today() + max(0, int(count))
        self._write(cognition_used=used)
        return used

    # -- real actions ----------------------------------------------------------
    def actions_used_today(self) -> int:
        return int(self._today().get("actions_used") or 0)

    def actions_remaining(self) -> int:
        return max(0, self.max_actions_per_day - self.actions_used_today())

    def note_action(self, count: int = 1) -> int:
        used = self.actions_used_today() + max(0, int(count))
        self._write(actions_used=used)
        return used

    def may_start_wake(self) -> tuple[bool, str]:
        """May one wake begin at all?  A refusal names the exact exhausted limit."""

        if self.remaining() <= 0:
            return False, "ROUND_BUDGET_EXHAUSTED"
        if self.cognition_remaining() <= 0:
            return False, "COGNITION_BUDGET_EXHAUSTED"
        return True, "ALLOWED"

    def snapshot(self) -> dict[str, Any]:
        out = {
            "max_rounds_per_day": self.max_rounds_per_day,
            "spent_today": self.spent_today(),
            "remaining_today": self.remaining(),
            "max_cognition_calls_per_day": self.max_cognition_calls_per_day,
            "cognition_used_today": self.cognition_used_today(),
            "cognition_remaining_today": self.cognition_remaining(),
            "max_actions_per_day": self.max_actions_per_day,
            "actions_used_today": self.actions_used_today(),
            "actions_remaining_today": self.actions_remaining(),
            "cognition_counter_source": ("life_request_budget" if self.requests is not None
                                         else "autonomous_budget.json"),
        }
        if self.requests is not None:
            out["requests"] = self.requests.view()
        return out


class _LazyStepSpec(AbcMapping):
    """A step spec built only when AG-2 materialises it.

    ``run_decision_and_adoption_cycle`` resolves ``step_spec_override`` with ``dict(...)`` *after*
    the decision, so a Mapping here means the content model call happens only when a step is
    really going to be executed -- never for a NO_ACTION or DEFER round.
    """

    def __init__(self, build: Callable[[], Mapping[str, Any]]) -> None:
        self._build = build
        self._cache: Optional[dict[str, Any]] = None

    def _value(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = dict(self._build())
        return self._cache

    def __getitem__(self, key: str) -> Any:
        return self._value()[key]

    def __iter__(self) -> Any:
        return iter(self._value())

    def __len__(self) -> int:
        return len(self._value())


class _DecisionBoundStepSpec:
    """A step spec built only once the Decision is committed.

    Same laziness contract as ``_LazyStepSpec``, with one difference that the World
    path needs: a MOVE's shape depends on *which* place the Decision chose, so it
    cannot be described before the choice exists.  AG-2 calls this with the
    committed ``DecisionRecord``, so the step stays bound to the Decision that
    produced it instead of to a guess made before it.
    """

    def __init__(self, build: Callable[[Any], Mapping[str, Any]]) -> None:
        self._build = build
        self._cache: Optional[dict[str, Any]] = None

    def __call__(self, decision_record: Any) -> dict[str, Any]:
        if self._cache is None:
            self._cache = dict(self._build(decision_record))
        return self._cache



class _RoutedAdoptionSpec:
    """The Activity shape for whichever candidate the Decision actually chose.

    Only used when a continuable work *and* an observed reachable place are both legal
    in the same round.  AG-2 converts the returned mapping into an
    ``ActivityAdoptionSpec`` and registers it for the *selected* candidate, so handing
    back the other path's shape would create an Activity for work she did not choose.
    Both shapes are therefore built lazily and the choice is made from the committed
    Decision's own selected candidate.  The World shape is built *from that Decision*
    (W05R-R1): its completion contract has to name the destination the Decision chose,
    so a builder that could not see the Decision could not describe a completable
    Activity at all.
    """

    def __init__(self, *, artifact: Mapping[str, Any],
                 world: Callable[[Any], Mapping[str, Any]],
                 is_world: Callable[[Any], bool],
                 reading: Optional[Callable[[Any], Mapping[str, Any]]] = None,
                 is_reading: Optional[Callable[[Any], bool]] = None) -> None:
        self._artifact = artifact
        self._world = world
        self._is_world = is_world
        self._reading = reading
        self._is_reading = is_reading or (lambda _record: False)
        self._cache: Optional[dict[str, Any]] = None

    def __call__(self, decision_record: Any) -> dict[str, Any]:
        if self._cache is None:
            if self._is_world(decision_record):
                source = self._world(decision_record)
            elif self._reading is not None and self._is_reading(decision_record):
                source = self._reading(decision_record)
            else:
                source = self._artifact
            self._cache = dict(source)
        return self._cache


class _DecisionBoundAdoptionSpec:
    """An Activity shape built only once the Decision is committed.

    The same laziness contract as ``_DecisionBoundStepSpec``, used for the *Activity*
    rather than the step: the World Activity's completion contract may only name a
    destination after the Decision that chose one exists.  AG-2 accepts a callable
    ``adoption_spec`` for exactly this reason.
    """

    def __init__(self, build: Callable[[Any], Mapping[str, Any]]) -> None:
        self._build = build
        self._cache: Optional[dict[str, Any]] = None

    def __call__(self, decision_record: Any) -> dict[str, Any]:
        if self._cache is None:
            self._cache = dict(self._build(decision_record))
        return self._cache


class _PauseAdmittedWriter:
    """W05R-R1b: the content request behind the same start handshake as the decision.

    The writer call *is* the content request's send boundary, so the admission runs
    immediately before the real writer is called: a pause confirmed after any earlier
    check still stops the content request from starting.  Nothing is held across the
    writer's own wait -- the admission releases the shared lock before it returns.
    """

    def __init__(self, inner: Any, admit: Callable[[str], None]) -> None:
        self._inner = inner
        self._admit = admit

    def next_content(self, point: Any, *, observed_at: str) -> str:
        self._admit("content")
        return self._inner.next_content(point, observed_at=observed_at)

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this proxy does not define; ``_inner``/``_admit``
        # are set in ``__init__`` so a lookup before that cannot recurse forever.
        if name in {"_inner", "_admit"}:
            raise AttributeError(name)
        return getattr(self._inner, name)

class AutonomousLifeCycle:
    """The real entry point.  One call, at most one step, then IDLE."""

    def __init__(
        self,
        runtime: Any,
        *,
        artifact_port: Optional[LifeArtifactPort] = None,
        subject_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        artifact_grant_id: Optional[str] = None,
        writer: Optional[ContinuationWriter] = None,
        experience_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
        world_opportunity_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
        max_rounds_per_day: Optional[int] = None,
        enabled: Optional[bool] = None,
        env: Optional[Mapping[str, str]] = None,
        world_home: Optional[str] = None,
        pause_state_root: Optional[Path | str] = None,
        requests: Any = None,
        situation_source: Any = None,
        continuity_source: Any = None,
        appraisal_source: Any = None,
        work_artifact_id: Optional[str] = None,
        continuity_state_root: Optional[Path | str] = None,
        artifact_exclusions: Optional[Sequence[str]] = None,
        reading_artifact_id: Optional[str] = None,
        private_items_artifact_id: Optional[str] = None,
    ) -> None:
        self.runtime = runtime
        self.state_root = Path(runtime.state_root)
        self.artifact_port = artifact_port
        self.subject_id = subject_id
        self.workspace_id = workspace_id
        self.artifact_grant_id = artifact_grant_id
        self.writer = writer or DeterministicContinuationWriter()

        src = os.environ if env is None else env

        def _flag(name: str, default: bool) -> bool:
            raw = str(src.get(name, "true" if default else "false")).strip().lower()
            return raw in {"1", "true", "yes", "on"}

        self.enabled = _flag(ENV_AUTONOMOUS_ENABLED, False) if enabled is None else bool(enabled)
        if max_rounds_per_day is None:
            try:
                max_rounds_per_day = int(src.get(ENV_MAX_ROUNDS_PER_DAY) or DEFAULT_MAX_ROUNDS_PER_DAY)
            except ValueError:
                max_rounds_per_day = DEFAULT_MAX_ROUNDS_PER_DAY
        self.budget = RoundBudget(
            self.state_root,
            max_rounds_per_day=max_rounds_per_day,
            max_cognition_calls_per_day=_int_setting(
                src, ENV_MAX_COGNITION_CALLS_PER_DAY, DEFAULT_MAX_COGNITION_CALLS_PER_DAY),
            max_actions_per_day=_int_setting(
                src, ENV_MAX_ACTIONS_PER_DAY, DEFAULT_MAX_ACTIONS_PER_DAY),
            requests=requests,
        )
        #: W05: the request environment this cycle decides in.  The journey engine is called
        #: with exactly these values, so no second world can be reached by accident.
        self.env = dict(src)
        #: W05: where the W04 journey ledger and the World's own state live.  ``CHIYO_WORLD_HOME``
        #: is the World service's name and ``HERMES_HOME`` the owner functions' name for the same
        #: directory, so either resolves the same home.
        self.world_home: Optional[str] = (
            str(world_home).strip() if world_home
            else (str(src.get("CHIYO_WORLD_HOME") or "").strip()
                  or str(src.get("HERMES_HOME") or "").strip() or None))
        #: W05: the life-side persistent pause bit (``life_pause.json``), under the Life state
        #: root by default.  Distinct from the World's own ``world_control.json``.
        self.pause_state_root = (Path(pause_state_root) if pause_state_root
                                 else self.state_root)
        #: W05R-R1: the shared confirmation lock, held *only* across a finite owner call
        #: (never across a model or content request).  See ``_pause_fault_hook``.
        self._pause_lease = life_pause.boundary_lease(self.pause_state_root)
        #: W05R-R1b: the content request goes through the same request-start handshake as
        #: the decision, at the writer call itself (the content request's send boundary).
        self.writer = _PauseAdmittedWriter(self.writer, self._admit_request)
        self.last_journey_settlement: Optional[dict[str, Any]] = None
        #: D (life-v1): the injected situation reader (``life_situation_view.LifeSituationView``)
        #: and the injected continuity reader (``life_continuity_inputs.LifeContinuityInputs``).
        #: With neither wired, AG-1 receives UNKNOWN for both capacity dimensions -- the round
        #: budget is never presented as a body fact again (A's own finding: no signal means
        #: UNKNOWN, and "connection worked" is not "the body can").
        self.situation_source = situation_source
        self.continuity_source = continuity_source
        #: A (AFFECT/SHORT-APPRAISAL-1): the read-only short-appraisal projection, as a callable
        #: returning its view (or ``None`` when the instance has no source at all).  This cycle
        #: consumes only the items the projection still calls VALID, and only as bounded
        #: *consideration* context -- it never turns one into a Candidate, a permission or a
        #: stable fact, and it defines no store of its own (there is no S07 owner to ask).
        self.appraisal_source = appraisal_source
        self.continuity_state_root = (Path(continuity_state_root) if continuity_state_root
                                      else self.state_root)
        #: D (life-v1): the work artifact this instance continues, bound *explicitly*.  With it
        #: set, ``latest_resumable`` is never consulted, so a private-items document (or any
        #: other artifact that happens to be newer) cannot be mistaken for the work.
        self.work_artifact_id = (str(work_artifact_id).strip() if work_artifact_id else None)
        self.last_situation: Optional[dict[str, Any]] = None
        self.last_continuity: Optional[dict[str, Any]] = None
        self.last_resume_selection: Optional[dict[str, Any]] = None
        self.last_appraisal: Optional[dict[str, Any]] = None
        self.last_capacity_kwargs: Optional[dict[str, Any]] = None
        #: D (life-v1): artifacts that are *not* work (the reading material, the private-items
        #: document).  A continuity hint naming one of these is dropped instead of retargeting
        #: the round.
        self.artifact_exclusions = {str(a) for a in (artifact_exclusions or ()) if str(a)}
        #: D (life-v1): the reading material and the private-items document this instance owns.
        #: Both are named explicitly (never by recency) and both are excluded from the *work*
        #: selection; the reading material is also a legal PERSONAL_OPPORTUNITY candidate, and a
        #: Decision that selects it runs the real page-read step instead of a work commit.
        self.reading_artifact_id = (str(reading_artifact_id).strip()
                                    if reading_artifact_id else None)
        self.private_items_artifact_id = (str(private_items_artifact_id).strip()
                                         if private_items_artifact_id else None)
        for _named in (self.reading_artifact_id, self.private_items_artifact_id):
            if _named:
                self.artifact_exclusions.add(_named)
        self.last_reading_plan: Optional[dict[str, Any]] = None

        #: The artifact continuation source, when a port was supplied.  Registered so AG-0 can
        #: actually see the work; the registry refuses unknown or forbidden source kinds itself.
        self.artifact_source: Optional[ArtifactContinuationSource] = None
        if artifact_port is not None and subject_id:
            self.artifact_source = ArtifactContinuationSource(
                artifact_port, subject_id=subject_id,
                # D (life-v1): the private-items document is not a piece of writing; it must
                # never be offered as one.  The reading material *is* offered here -- that is
                # how a reading opportunity becomes an admitted PERSONAL_OPPORTUNITY candidate.
                exclude_artifact_ids=([private_items_artifact_id]
                                      if private_items_artifact_id else []))
            runtime.candidate_registry.register_adapter(self.artifact_source)

        self.last_outcome: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_receipt: Optional[StepReceipt] = None
        #: E07: the same bounded, governance-filtered experience view the content writer gets, so a
        #: decision may weigh the same past the writing does.  Empty means memory is not consulted.
        self.experience_provider = experience_provider
        #: B6-5: the World observation seam.  The provider returns the observed opportunities a
        #: *real* observation supports; it never picks one.  With no provider wired the World is
        #: not consulted at all, and this cycle behaves exactly as before.
        self.world_opportunity_provider = world_opportunity_provider
        #: Told about a version *we* just committed, so the artifact feed can advance its cursor
        #: and our own edit is not an event to ourselves.  ``None`` keeps the previous behaviour.
        self.own_write_recorder: Optional[Callable[[str, str], None]] = None
        self.last_world_observation: Optional[dict[str, Any]] = None
        self.last_world_error: Optional[str] = None
        self.last_world_opportunities: list[dict[str, Any]] = []
        #: I: the provider's richer, non-AG-0 view of the same opportunities (which owner
        #: verb, how far, is the place fact open or uncertain) plus the W catalog read it
        #: came from.  Both are report-only: they authorize nothing and are never turned
        #: into a Candidate, a Decision or a permission.
        self.last_world_opportunity_view: list[dict[str, Any]] = []
        self.last_world_catalog: Optional[dict[str, Any]] = None
        #: The object the step spec built for *this* round reports its progress on.  The AR-0
        #: payload is whitelisted by the owner adapter (``managed_artifact_action_adapter``),
        #: so a payload key cannot carry it: a page read commits the private-items document but
        #: the progress belongs to the reading material, and that is not derivable from the
        #: frozen payload alone.
        self.last_step_progress_target_ref: Optional[str] = None
        self.rounds_run = 0

    # -- D (life-v1): continuity, the work binding and the capacity columns --------------

    def continuity_path(self) -> Path:
        """Where C's read watermark lives: this cycle's own state root."""
        return self.continuity_state_root / CONTINUITY_WATERMARK_FILENAME

    def read_continuity_watermark(self) -> dict[str, Any]:
        """The watermark from the existing run state.  Absent is reported, never guessed.

        A missing or unreadable file means "this instance has not seen any eligibility
        yet".  It is not a licence to re-offer everything, and it is not an error.
        """
        try:
            data = json.loads(self.continuity_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"seen_eligibility_refs": [], "adopted_eligibility_refs": [],
                    "eligibility_watermark": None, "state": "absent"}
        except (OSError, ValueError) as exc:
            return {"seen_eligibility_refs": [], "adopted_eligibility_refs": [],
                    "eligibility_watermark": None,
                    "state": "unreadable", "detail": f"{type(exc).__name__}: {exc}"}
        if not isinstance(data, Mapping):
            return {"seen_eligibility_refs": [], "adopted_eligibility_refs": [],
                    "eligibility_watermark": None, "state": "invalid"}
        return {
            "seen_eligibility_refs": [str(r) for r in (data.get("seen_eligibility_refs") or [])
                                      if str(r)],
            "adopted_eligibility_refs": [str(r)
                                         for r in (data.get("adopted_eligibility_refs") or [])
                                         if str(r)],
            "eligibility_watermark": data.get("eligibility_watermark"),
            "state": "ok",
        }

    def read_continuity(self) -> Optional[dict[str, Any]]:
        """One read-only continuity view (C), or ``None`` when nothing is wired.

        The reader gets exactly two things from here: the eligibility refs already *seen* and
        the instant of the last consideration.  It returns read hints -- never a Candidate, a
        Decision, a permission or a location.

        **Reading changes nothing.**  The watermark is written by ``evaluate_once`` only after a
        Decision was really committed (see ``_record_continuity_progress``), so a status read, a
        paused round or a failed round cannot silently mark an eligibility as known.
        """
        source = self.continuity_source
        if source is None:
            return None
        if not hasattr(source, "read") and callable(source):
            # A factory, so every read carries a *fresh* binding instant instead of one frozen
            # at assembly time.
            try:
                source = source()
            except Exception as exc:
                self.last_continuity = {"read_error": f"{type(exc).__name__}: {exc}"}
                return self.last_continuity
        mark = self.read_continuity_watermark()
        try:
            view = source.read(known_eligibility_refs=tuple(mark["seen_eligibility_refs"]),
                               since=mark.get("eligibility_watermark"))
        except Exception as exc:
            self.last_continuity = {"read_error": f"{type(exc).__name__}: {exc}"}
            return self.last_continuity
        if hasattr(view, "to_dict"):
            view = view.to_dict()
        view = dict(view) if isinstance(view, Mapping) else {"read_error": "NOT_A_MAPPING"}
        self.last_continuity = view
        return view

    def _record_continuity_progress(self, *, observed_at: str,
                                    adopted_refs: Sequence[str] = ()) -> None:
        """Advance C's watermark -- only from the legal phase, never from a mere read.

        * ``seen`` is advanced after a Decision was really committed: the round considered the
          eligibility C offered, which is what "already seen" has to mean.
        * ``adopted`` is advanced when the owner reported a RESUME adoption, i.e. the
          eligibility was really consumed.
        * the ``since`` watermark is the instant of this consideration -- never C's future
          ``next_recheck_at`` (a due time is not a consumption time).
        """
        view = self.last_continuity or {}
        observed = [str(r) for r in (view.get("observed_eligibility_refs") or []) if str(r)]
        adopted = [str(r) for r in (adopted_refs or []) if str(r)]
        if not observed and not adopted:
            return
        try:
            self._write_continuity_watermark(seen_refs=observed, adopted_refs=adopted,
                                            considered_at=observed_at)
        except OSError as exc:
            if isinstance(view, dict):
                view["watermark_write_error"] = f"{type(exc).__name__}: {exc}"

    def _write_continuity_watermark(self, *, seen_refs: Sequence[str],
                                    adopted_refs: Sequence[str] = (),
                                    considered_at: Optional[str] = None) -> None:
        """Persist what the round legally processed.  Reading is not adopting."""
        previous = self.read_continuity_watermark()
        merged_seen = sorted(set(previous.get("seen_eligibility_refs") or [])
                             | {str(r) for r in seen_refs if str(r)})
        merged_adopted = sorted(set(previous.get("adopted_eligibility_refs") or [])
                                | {str(r) for r in adopted_refs if str(r)})
        payload = {
            "schema_version": "chiyo.life.continuity_watermark.v1",
            "kind": "life_continuity_watermark",
            "seen_eligibility_refs": merged_seen,
            "adopted_eligibility_refs": merged_adopted,
            # The instant of the consideration that advanced this watermark -- a *past* instant,
            # never C's future recheck time.
            "eligibility_watermark": (considered_at
                                      or previous.get("eligibility_watermark")),
            "updated_at": _utc_now_iso(),
        }
        path = self.continuity_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)

    #: ``activity_waiting_agenda.ELIG_STATUS_CONSUMED`` (LR-4 owns this vocabulary; the cycle
    #: only mirrors the one value it has to recognise, it never writes it).
    ELIGIBILITY_STATUS_CONSUMED = "CONSUMED"

    def _resumed_consumed_eligibility(self, record: Any, adp_rec: Any,
                                      int_rec: Any) -> tuple[Optional[str], str]:
        """The **one** eligibility this round really consumed, or ``(None, reason_code)``.

        The watermark may record a *consumption*, so the ref must be attributable to an
        owner-side fact and nothing else: the committed Decision's own selected candidate
        (``ResumeEligibleCandidateSource`` names the eligibility it offered,
        ``candidate_sources_ag0.py:1414``), read back from LR-4 as ``CONSUMED`` for the
        Activity this adoption really resumed (the integration record's own ``activity_ref``).

        Anything shorter than that chain -- no selected candidate, a candidate that is not
        readable, an eligibility LR-4 still reports ``OPEN``, an Activity that does not match
        -- yields ``(None, reason)`` and writes **nothing**: a consumption that cannot be
        attributed is not a consumption, and "everything C happened to show us" is not the
        owner's CONSUMED set.
        """
        candidate_ref = str(getattr(record, "selected_candidate_ref", "") or "")
        if not candidate_ref:
            return None, "SELECTED_CANDIDATE_MISSING"
        reader = getattr(self.runtime, "candidate_read", None)
        getter = getattr(reader, "get_candidate", None)
        if not callable(getter):
            return None, "CANDIDATE_READ_NOT_WIRED"
        try:
            candidate = getter(candidate_ref)
        except Exception as exc:  # a refused read is reported, never guessed around
            return None, f"CANDIDATE_READ_FAILED:{type(exc).__name__}"
        if not isinstance(candidate, Mapping):
            return None, "CANDIDATE_NOT_READABLE"
        eligibility_ref = str(candidate.get("resume_eligibility_ref") or "")
        if not eligibility_ref:
            return None, "CANDIDATE_NAMES_NO_ELIGIBILITY"
        eligibility = self._owner_eligibility(eligibility_ref)
        if eligibility is None:
            return None, "ELIGIBILITY_NOT_READABLE"
        status = str(eligibility.get("status") or "")
        if status != self.ELIGIBILITY_STATUS_CONSUMED:
            return None, f"ELIGIBILITY_NOT_CONSUMED:{status or 'UNKNOWN'}"
        resumed_activity = str(getattr(int_rec, "activity_ref", "") or "")
        if not resumed_activity:
            return None, "RESUMED_ACTIVITY_UNKNOWN"
        if str(eligibility.get("activity_id") or "") != resumed_activity:
            return None, "ELIGIBILITY_ACTIVITY_MISMATCH"
        return eligibility_ref, "OK"

    def _owner_eligibility(self, eligibility_ref: str) -> Optional[Mapping[str, Any]]:
        """LR-4's own record of one eligibility (read-only), or ``None`` when unreadable."""
        waiting = getattr(self.runtime, "waiting_coordinator", None)
        getter = getattr(waiting, "get_eligibility", None)
        if callable(getter):
            try:
                found = getter(eligibility_ref)
            except Exception:
                found = None
            if isinstance(found, Mapping):
                return found
        store = getattr(self.runtime, "eligibility_store", None)
        loader = getattr(store, "load_state", None)
        if not callable(loader):
            return None
        try:
            state = loader() or {}
        except Exception:
            return None
        items = state.get("eligibilities") if isinstance(state, Mapping) else None
        found = items.get(eligibility_ref) if isinstance(items, Mapping) else None
        return found if isinstance(found, Mapping) else None

    # -- A (life-v1): the short-appraisal context ------------------------------------------

    def read_appraisal(self) -> Optional[dict[str, Any]]:
        """One read-only short-appraisal view (A's projection), or ``None`` when unwired.

        The cycle contributes nothing to this read: it forwards whatever the injected callable
        projected (``ShortAppraisalView.build``), and a failing source is recorded instead of
        being guessed around.
        """
        source = self.appraisal_source
        if source is None:
            return None
        try:
            view = source() if callable(source) else source
        except Exception as exc:
            self.last_appraisal = {"read_error": f"{type(exc).__name__}: {exc}"}
            return self.last_appraisal
        self.last_appraisal = (dict(view) if isinstance(view, Mapping)
                               else {"read_error": "NOT_A_MAPPING"})
        return self.last_appraisal

    def appraisal_context(self) -> list[dict[str, Any]]:
        """The still-valid appraisals this round may weigh, as bounded context items.

        Only ``VALID`` items are eligible -- A's projection already refuses a missing source,
        a missing/invalid ``valid_until``, a correction of the item or of one of its sources,
        a lost source and an expired window.  Each item keeps its own ``appraisal_ref`` /
        ``source_refs`` / ``valid_until`` so a reader can audit where it came from and when it
        stops holding.  Nothing here reads text or tone, sums intensity, carries a permission,
        or becomes a Candidate or a stable fact.
        """
        view = self.read_appraisal()
        if not isinstance(view, Mapping):
            return []
        items: list[dict[str, Any]] = []
        for item in view.get("appraisals") or ():
            if not isinstance(item, Mapping):
                continue
            if str(item.get("status") or "") != "VALID":
                continue
            items.append({
                "kind": "short_appraisal",
                "owner": "short_appraisal_view (experimental rebuildable projection; no S07 owner)",
                "appraisal_ref": item.get("appraisal_ref"),
                "source_refs": list(item.get("source_refs") or []),
                "observed_at": item.get("observed_at"),
                "valid_until": item.get("valid_until"),
                "declared_scope": item.get("declared_scope"),
                "uncertainty": item.get("uncertainty"),
                "uncertainty_source": item.get("uncertainty_source"),
                "intensity": item.get("intensity"),
                "reason_code": item.get("reason_code"),
                # The four refusals that keep this from becoming a feeling, a fact or a move:
                "authority": None,
                "candidate": False,
                "stable_fact": False,
                "tone_inference_used": False,
            })
        return items

    def consideration_context(self) -> list[dict[str, Any]]:
        """What the round may weigh: admitted experiences + still-valid sourced appraisals."""
        return list(self._experience_view()) + self.appraisal_context()

    @staticmethod
    def _compact_appraisal(view: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
        """The audit line for the round record: what was read, what stayed valid, why empty."""
        if not isinstance(view, Mapping):
            return None
        return {
            "kind": view.get("kind"),
            "identity_bound": view.get("identity_bound"),
            "identity_reason_code": view.get("identity_reason_code"),
            "has_valid_appraisal": bool(view.get("has_valid_appraisal")),
            "valid_appraisal_refs": list(view.get("valid_appraisal_refs") or []),
            "invalid_appraisals": list(view.get("invalid_appraisals") or []),
            "empty_reason_code": view.get("empty_reason_code"),
            "source_revisions": dict(view.get("source_revisions") or {}),
            "read_error": view.get("read_error"),
        }

    def continuity_continue_object(self) -> Optional[str]:
        """The artifact id C's own read points at, or ``None``.

        Only an owner-confirmed continuation counts: C says
        ``CONTINUE_FROM_OWNER_CONFIRMED_OBJECT`` and its admitted past row carries the
        ``artifact`` ref the consequence really happened on.  A checkpoint-only step, a
        weak hint or a failed read yields ``None``, which means "keep the bound work".
        """
        view = self.last_continuity or {}
        step = view.get("next_step") or {}
        if not isinstance(step, Mapping):
            return None
        if str(step.get("reason_code") or "") != "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT":
            return None
        item_ref = str(step.get("item_ref") or "")
        # Match the row C actually selected: the admitted past row whose own evidence refs are
        # the ones the step names.  Falling back to "the first row with an artifact" would pick
        # a row C never chose.
        selected_refs = {str(r) for r in (step.get("evidence_refs") or []) if str(r)}
        candidates: list[Mapping[str, Any]] = []
        for row in view.get("past") or []:
            if not isinstance(row, Mapping):
                continue
            activity_ref = str(row.get("activity_ref") or "")
            if item_ref and activity_ref and activity_ref != item_ref:
                continue
            candidates.append(row)
        if selected_refs:
            matched = [row for row in candidates
                       if str(row.get("source_ref") or "") in selected_refs
                       or str(row.get("experience_id") or "") in selected_refs
                       or str(row.get("event_id") or "") in selected_refs]
            if not matched:
                return None
            candidates = matched
        for row in candidates:
            # The owner's own id is kept verbatim (``artifact:...``): the id is what the owner
            # mints, and rewriting it here is how a Memory string would end up naming a
            # different object.
            artifact = str(row.get("artifact_ref") or "").strip()
            if artifact:
                return artifact
        return None

    def selected_artifact_id(self) -> Optional[str]:
        """Which artifact this round continues.  Recency is never the answer."""
        from_history = self.continuity_continue_object()
        if from_history:
            return from_history
        return self.work_artifact_id

    def _history_continuation(self) -> dict[str, str]:
        """What the admitted history named, plus the version its *own* Action really produced.

        C's hint names an owner result (``rset:…``) or a version, never a permission, and a
        string in the hint is not evidence about the object: the version is read from the
        receipt the owner holds for **that row's** Action (``past[].action_ref``).  Nothing here
        is inferred from the hint text, and an unreadable receipt yields no version at all.
        """
        view = self.last_continuity or {}
        step = view.get("next_step") or {}
        if not isinstance(step, Mapping):
            return {}
        named = step.get("continue_from_ref") or step.get("object_ref")
        out: dict[str, str] = {"continuation_ref": str(named) if named else ""}
        selected = {str(ref) for ref in (step.get("evidence_refs") or []) if str(ref)}
        row: Optional[Mapping[str, Any]] = None
        for item in view.get("past") or []:
            if not isinstance(item, Mapping):
                continue
            if selected and not ({str(item.get("source_ref") or ""),
                                  str(item.get("experience_id") or "")} & selected):
                continue
            row = item
            break
        action_ref = str((row or {}).get("action_ref") or "")
        if action_ref:
            out["action_ref"] = action_ref
            version = self._owner_action_version(action_ref)
            if version:
                out["confirmed_version_ref"] = version
        return out

    def _owner_action_version(self, action_ref: str) -> Optional[str]:
        """The version the owner's own receipt for ``action_ref`` reports, or ``None``."""
        reader = getattr(self.runtime, "action_read", None)
        lister = getattr(reader, "list_receipts", None)
        if not callable(lister):
            return None
        try:
            receipts = lister(action_ref) or []
        except Exception:  # a refused read yields no version; it is never guessed
            return None
        for receipt in reversed(list(receipts)):
            details = (receipt or {}).get("details") or {}
            version = details.get("version_id")
            if version:
                return str(version)
        return None

    def _read_resume_point(self) -> tuple[Optional[ArtifactResumePoint], dict[str, Any]]:
        """The continuation point of the selected work, plus how it was selected.

        The choice is *verified against the owner*: the artifact C named must exist, be ACTIVE
        and have a committed head, and it must not be one of this instance's non-work documents
        (the reading material or the private-items document).  A choice that fails any of those
        checks is dropped and the bound work is used instead -- a Memory string never retargets
        the round, and history is never treated as permission.
        """
        history_choice = self.continuity_continue_object()
        selection: dict[str, Any] = {
            "artifact_id": history_choice or self.work_artifact_id,
            "selected_by": ("CONTINUITY_HISTORY" if history_choice
                            else ("BOUND_WORK" if self.work_artifact_id else "LATEST_RESUMABLE")),
            "bound_work_artifact_id": self.work_artifact_id,
            "history_artifact_ref": history_choice,
        }
        artifact_id = history_choice or self.work_artifact_id
        if history_choice and history_choice in self.artifact_exclusions:
            # C named a document that is not a work (reading material / private items): keep the
            # bound work and say exactly why.
            selection.update({"selected_by": "CONTINUITY_ARTIFACT_EXCLUDED",
                              "artifact_id": self.work_artifact_id,
                              "excluded_artifact_ref": history_choice})
            artifact_id = self.work_artifact_id
        if self.artifact_port is None:
            selection["selected_by"] = "NO_PORT"
            self.last_resume_selection = selection
            return None, selection
        try:
            point = (self.artifact_port.resume_point(str(artifact_id)) if artifact_id
                     else self.artifact_port.latest_resumable())
        except (LifeArtifactUnavailable, ArtifactOwnerRefused) as exc:
            if history_choice:
                # The history-named artifact is not usable: fall back to the bound work rather
                # than continuing something the owner does not confirm.
                selection.update({"selected_by": "CONTINUITY_ARTIFACT_UNVERIFIED",
                                  "history_error": str(exc)[:200],
                                  "artifact_id": self.work_artifact_id})
                try:
                    point = (self.artifact_port.resume_point(str(self.work_artifact_id))
                             if self.work_artifact_id else None)
                except (LifeArtifactUnavailable, ArtifactOwnerRefused) as fallback_exc:
                    selection["error"] = str(fallback_exc)[:200]
                    self.last_resume_selection = selection
                    return None, selection
            else:
                selection["error"] = str(exc)[:200]
                self.last_resume_selection = selection
                return None, selection
        if point is not None:
            selection["artifact_id"] = point.artifact_id
            selection["head_version_id"] = point.head_version_id
            selection["resumable"] = point.resumable
            selection["owner_verified"] = True
            selection["lifecycle"] = point.lifecycle
            if history_choice:
                # The admitted history names a *result* (its own ``rset:…``), and the version it
                # really produced comes from that row's Action receipt -- the owner's record.
                # The step is always pinned to the owner's **current** head, so history can only
                # agree, be unverifiable, or be stale: it is reported, never written.
                continuation = self._history_continuation()
                selection["history_continuation_ref"] = continuation.get("continuation_ref")
                selection["history_action_ref"] = continuation.get("action_ref")
                confirmed = continuation.get("confirmed_version_ref")
                selection["history_version_ref"] = confirmed
                selection["expected_head_source"] = "OWNER_CURRENT_HEAD"
                if not confirmed:
                    selection["history_state"] = "VERSION_UNVERIFIED"
                elif str(confirmed) != str(point.head_version_id):
                    selection["history_state"] = "STALE_HEAD"
                else:
                    selection["history_state"] = "OK"
        self.last_resume_selection = selection
        return point, selection

    # -- D (life-v1): the reading action ---------------------------------------------------

    def reading_source_id(self) -> Optional[str]:
        """The private-items ``source_id`` for this instance's reading material."""
        if not self.reading_artifact_id:
            return None
        return ("artifact:%s" % self.reading_artifact_id
                if not self.reading_artifact_id.startswith("artifact:")
                else self.reading_artifact_id)

    def reading_available(self) -> bool:
        """Is a reading opportunity legal in this round at all?"""
        return bool(self.artifact_port and self.reading_artifact_id
                    and self.private_items_artifact_id)

    @staticmethod
    def _artifact_identity(ref: Any) -> Optional[str]:
        """The owner's own artifact id named by ``ref``, or None if it names no artifact.

        The ManagedArtifact owner mints its ids *with* the prefix
        (``lifesupply/artifact/store.py:217``: ``aid = "artifact:" + uuid4().hex``), while the
        admitted PERSONAL_OPPORTUNITY source prefixes its source ref with ``artifact:`` again
        (``artifact_continuation_source.py:167``).  The same object is therefore spelled
        ``artifact:<id>`` or ``artifact:artifact:<id>`` depending on who is talking, so the
        match has to be about the object, not about the spelling: strip every ``artifact:``
        prefix and compare what is left.  A version ref names a revision, not the artifact.
        """
        text = str(ref or "").strip()
        if not text or text.startswith("artifactversion:"):
            return None
        while text.startswith("artifact:"):
            text = text[len("artifact:"):]
        return text or None

    @staticmethod
    def _owner_artifact_ref(ref: Any) -> Optional[str]:
        """The ManagedArtifact owner's *own* spelling of the artifact ``ref`` names, or None.

        The owner mints ``artifact:<hex>``, while the admitted PERSONAL_OPPORTUNITY source
        spells the same object ``artifact:artifact:<hex>``.  A handoff that stacked one more
        prefix on top would name an object the owner cannot read back
        (``NOT_FOUND:ARTIFACT_NOT_FOUND``), so the id is normalized to the owner's own
        spelling here instead of being re-prefixed.  A ref that is not an artifact id
        (a version ref, free text, a bare name containing ``:``) yields None.
        """
        text = str(ref or "").strip()
        while text.startswith("artifact:"):
            text = text[len("artifact:"):]
        if not text or ":" in text:
            return None
        return "artifact:%s" % text

    def _candidate_is_reading(self, candidate: Optional[Mapping[str, Any]]) -> bool:
        """A reading candidate is one whose target (or source) is the reading material."""
        if not isinstance(candidate, Mapping) or not self.reading_artifact_id:
            return False
        wanted = self._artifact_identity(self.reading_artifact_id)
        if not wanted:
            return False
        refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
        refs.append(candidate.get("source_ref"))
        return any(self._artifact_identity(ref) == wanted for ref in refs)

    def _decision_chose_reading(self, decision_record: Any) -> bool:
        return self._candidate_is_reading(self._selected_candidate(decision_record))

    def _reading_step_spec(self, *, observed_at: str) -> dict[str, Any]:
        """One bounded real action: read one page of the material, then commit the bookmark.

        The page comes from the owner's own read of the reading artifact (``read_artifact``);
        the bookmark is planned purely (``private_daily_items.plan_page_read``) and committed by
        the owner as one compare-and-swap version of the private-items document.  The step is
        therefore a real AR-0 file action with a real receipt -- and the same operation key means
        a retry can never mint a second version.
        """
        import private_daily_items as pdi

        if not self.reading_available():
            raise AutonomousLifeError("no reading material bound to this instance")
        reading_id = str(self.reading_artifact_id)
        # A page read commits the private-items document, but the progress (and the memory
        # handoff) belongs to the *reading material*: the AR-0 payload is whitelisted by the
        # owner adapter, so the target is carried here instead of inside the payload.
        self.last_step_progress_target_ref = reading_id
        doc_id = str(self.private_items_artifact_id)
        reading = self.artifact_port.read_artifact(reading_id)
        reading_head = dict(reading.get("head") or {})
        content = reading.get("content")
        if not isinstance(content, str) or not content:
            raise AutonomousLifeError("the reading material has no content to read")
        pages = [page for page in content.split(READING_PAGE_MARKER)]
        document_view = self.artifact_port.read_artifact(doc_id)
        doc_head = dict(document_view.get("head") or {})
        document = pdi.decode_document(document_view.get("content") or "")
        source_id = self.reading_source_id()
        last_page = pdi.reading_position_page(document, source_id=source_id)
        page = int(last_page or 0) + 1
        if page > len(pages):
            raise AutonomousLifeError("no unread page left in the reading material")
        text = pages[page - 1]
        revision = str(reading_head.get("version_id") or "")
        if not revision:
            raise AutonomousLifeError("the reading material has no committed version")
        planned = pdi.plan_page_read(
            document, artifact_id=doc_id, source_id=source_id, source_revision=revision,
            receipt_ref="read:%s:%s:page-%s" % (reading_id, revision, page),
            obtained={"pages": [page], "chars": len(text)}, excerpt=text[:200],
            page=page, grant_ref=self.artifact_grant_id,
            head_version_id=doc_head.get("version_id"),
            head_number=doc_head.get("version_number"), now=observed_at)
        self.last_reading_plan = {
            "reading_artifact_id": reading_id, "private_items_artifact_id": doc_id,
            "source_id": source_id, "source_revision": revision, "page": page,
            "pages": len(pages), "obtained": {"pages": [page], "chars": len(text)},
            "acquisition_id": planned["acquisition_id"],
            "position_id": planned["position_id"], "changed": planned["changed"],
            "understanding": "UNKNOWN",
        }
        return {
            "step_kind": ag2.STEP_KIND_FILE_ACTION,
            "action_kind": ACTION_KIND_FILE,
            "target_ref": "artifact:%s" % doc_id,
            "payload_spec": {
                "op": OP_COMMIT_VERSION,
                "artifact_id": doc_id,
                "expected_head": doc_head.get("version_id"),
                "content": planned["content"],
                "grant_id": str(self.artifact_grant_id),
                # The progress/experience this step reports belongs to the *reading material*,
                # not to the document the bytes happen to live in.
                "life_progress_target_ref": ("artifact:%s" % reading_id
                                             if not reading_id.startswith("artifact:")
                                             else reading_id),
            },
            "expected_effect_predicates": ["artifact_written_verified"],
            "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
            "idempotency_key": planned["operation_key"],
        }

    def _reading_adoption_spec(self) -> dict[str, Any]:
        """The Activity shape for reading: a positional read, open-ended like the work.

        One page is progress, not the end of the material, so there is deliberately no
        completion contract here -- and the checkpoint follows the page position the document
        really records.
        """
        reading_ref = ("artifact:%s" % self.reading_artifact_id
                       if not str(self.reading_artifact_id).startswith("artifact:")
                       else str(self.reading_artifact_id))
        doc_ref = ("artifact:%s" % self.private_items_artifact_id
                   if not str(self.private_items_artifact_id).startswith("artifact:")
                   else str(self.private_items_artifact_id))
        spec = ag2.ActivityAdoptionSpec(
            activity_kind="READ_PERSONAL_MATERIAL",
            title="读一份自己的材料",
            target_refs=[reading_ref, doc_ref],
            origin_ref=reading_ref,
            progress_contract_spec={
                "progress_kind": lr5_module().PROGRESS_KIND_POSITIONAL_READING,
                "checkpoint_semantics": lr5_module().CHECKPOINT_SEMANTICS_LATEST_POSITION,
                "target_refs": [reading_ref],
                "policy_version": lr5_module().POLICY_ARTIFACT_V1,
            },
            result_binding_spec={
                "target_refs": [reading_ref, doc_ref],
                "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"],
                "authority_domains": ["result_settlement_authority", "artifact_authority"],
                "policy_version": lr5_module().POLICY_ARTIFACT_V1,
            },
        )
        return spec.to_dict()

    def _classify_reading_step(self, out: Mapping[str, Any], base: dict[str, Any],
                               obs: str) -> dict[str, Any]:
        """Report the page read as the real owner result it is."""
        result = self._classify_ar0_step(out, base, obs)
        base["reading"] = dict(self.last_reading_plan or {})
        base["step_path"] = "READING_AR0"
        return result


    # -- D (life-v1): the capacity columns (A) -------------------------------------------

    def budget_column(self) -> Optional[dict[str, Any]]:
        """This instance's own request-ledger read, in the shape A's view wants.

        It is a projection of a read the owner already performed: no reservation is
        made here.  An unreadable ledger is reported as a source fault -- never as
        "the budget is spent", which is the distinction A's column is built around.
        """
        requests = getattr(self.budget, "requests", None)
        if requests is None:
            return None
        try:
            checked = dict(requests.check())
        except Exception as exc:
            checked = {"allowed": None, "reason_code": life_request_budget.REASON_UNREADABLE,
                       "detail": f"{type(exc).__name__}: {exc}"}
        try:
            view = dict(requests.view())
        except Exception as exc:
            view = {"state": life_request_budget.STATE_CORRUPT,
                    "reason_code": life_request_budget.REASON_UNREADABLE,
                    "detail": f"{type(exc).__name__}: {exc}"}
        return {
            "source": "life_request_budget.check",
            "allowed": checked.get("allowed"),
            "reason_code": checked.get("reason_code") or None,
            "state": view.get("state"),
            "path": view.get("path"),
            "utc_day": checked.get("utc_day") or view.get("utc_day"),
            "run_id": view.get("run_id"),
            "utc_day_calls": checked.get("utc_day_calls"),
            "utc_day_max": view.get("utc_day_max"),
            "run_window_calls": checked.get("run_window_calls"),
            "run_window_max": view.get("run_window_max"),
        }

    def observations(self, now: str) -> list[dict[str, Any]]:
        """What was actually acquired this round, as A's knowledge column reads it.

        Only a real World observation is listed, and only what the source itself
        declared: the World snapshot carries no validity bound, so nothing here
        invents one -- the column reports it UNVERIFIED rather than "current".
        """
        observed = self.last_world_observation
        if not isinstance(observed, Mapping) or not observed:
            return []
        revision = observed.get("world_revision")
        place = str((observed.get("location") or {}).get("place_id") or "")
        item: dict[str, Any] = {
            "observation_ref": "world:observation:%s"
                               % (revision if revision is not None else "unknown"),
            "source_ref": "world_body_read_port.get_observation",
        }
        if observed.get("observed_at"):
            item["observed_at"] = observed["observed_at"]
        if place:
            item["declared_scope"] = "place:%s" % place
        item["uncertainty"] = "declared_by_source"
        return [item]

    def read_situation(self, now: str) -> dict[str, Any]:
        """One real situation read (A), or an honest UNKNOWN pair.

        The body column comes from the real Body read through the port A was given,
        and the resource column from this instance's own ledger read.  A wiring fault
        degrades to UNKNOWN -- never to AVAILABLE.
        """
        source = self.situation_source
        if source is None:
            view: dict[str, Any] = {"reason_code": "SITUATION_SOURCE_NOT_WIRED"}
        else:
            try:
                view = dict(source.build(now=now, budget=self.budget_column(),
                                         observations=self.observations(now)))
            except Exception as exc:
                view = {"reason_code": "SITUATION_READ_FAILED",
                        "detail": f"{type(exc).__name__}: {exc}"}
        self.last_situation = view
        return view

    def capacity_kwargs(self, view: Mapping[str, Any]) -> dict[str, Any]:
        """AG-1's four capacity keywords, or the honest UNKNOWN pair.

        ``unknown_capacity_policy`` is deliberately left unset: AG-1's default
        ``ALLOW_WITH_UNKNOWN`` is what keeps an honest UNKNOWN from meaning "defer
        forever" (``agency_decision_ag1.py:1971``); ``BLOCK_ON_UNKNOWN`` is exactly the
        permanent deferral A warned about.
        """
        try:
            kwargs = dict(lsv.ag1_capacity_kwargs(view))
        except Exception as exc:
            self.last_capacity_kwargs = {"source_error": f"{type(exc).__name__}: {exc}",
                                         "reason_code": "CAPACITY_COLUMNS_UNAVAILABLE"}
            return {"body_capacity_state": ag1.CAPACITY_UNKNOWN, "body_capacity_ref": None,
                    "resource_availability_state": ag1.CAPACITY_UNKNOWN,
                    "resource_budget_ref": None}
        self.last_capacity_kwargs = kwargs
        return kwargs
    # -- I-r2: the opening/opportunity facts the Decision may actually weigh ------------

    def world_observation_facts(self) -> dict[str, Any]:
        """The World's own opening facts for the places *this* round may choose between.

        Source: the World package's own catalog, read by the observation seam at the same
        injected instant the observation was taken (``last_world_catalog``), plus the place
        that observation really puts her in.  Only the place she is in and the places the
        observation itself offered to walk to are listed, so the fact set is bounded by the
        choice in front of her -- never a dump of the neighborhood and never a permission.

        The status is spelled into the key because AG-1's bounded projection hands the model
        ``observed_fact_keys`` (``agency_decision_ag1.py:3759``) and the production prompt
        keeps only ASCII/underscore keys (``agency_cognition_adapter.py:203``).  A model --
        real or deterministic -- therefore reads the owner's own verdict (``CLOSED`` /
        ``OPEN``) instead of having to infer it from which offers happen to be missing.
        """

        catalog = self.last_world_catalog or {}
        if str(catalog.get("status") or "") != "available":
            # An unreadable catalog grounds nothing: no fact is better than a guessed one.
            return {}
        entries: dict[str, dict[str, Any]] = {}
        for item in catalog.get("opportunities") or []:
            if not isinstance(item, Mapping):
                continue
            place = str(item.get("location_id") or "")
            if place:
                entries[place] = dict(item)
        observation = self.last_world_observation or {}
        here = str((observation.get("location") or {}).get("place_id") or "")
        wanted: set[str] = {here} if here else set()
        for opportunity in self.last_world_opportunities or []:
            for ref in opportunity.get("target_refs") or []:
                text = str(ref)
                if text.startswith(PLACE_REF_PREFIX):
                    wanted.add(text.split(":", 1)[1])
        facts: dict[str, Any] = {}
        for place in sorted(wanted):
            entry = entries.get(place)
            if not entry:
                continue
            availability = dict(entry.get("availability") or {})
            status = str(availability.get("status") or "NOT_READ")
            reason = str(availability.get("reason_code") or "")
            facts["world_place_%s_availability_%s"
                  % (_fact_token(place), _fact_token(status))] = status
            if reason:
                facts["world_place_%s_unavailable_because_%s"
                      % (_fact_token(place), _fact_token(reason))] = reason
            if place == here:
                facts["world_here_place_%s" % _fact_token(place)] = place
        return facts

    # -- zero-LLM material check -------------------------------------------------------

    def materials(self) -> dict[str, Any]:
        """Is there anything real to evaluate?  Costs nothing and calls no model."""
        found: dict[str, Any] = {
            "foreground_activity": None,
            "continuable_artifact": None,
            "source_availability": {},
        }
        try:
            state = self.runtime.canonical_state()
            found["foreground_activity"] = state.get("foreground_activity_ref")
        except Exception as exc:  # pragma: no cover - owner read failure
            found["canonical_read_error"] = f"{type(exc).__name__}: {exc}"

        for adapter in self.runtime.candidate_registry.list_adapters():
            try:
                status = adapter.availability_status()
            except Exception as exc:  # pragma: no cover
                status = {"status": "ERROR", "detail": f"{type(exc).__name__}: {exc}"}
            found["source_availability"][adapter.source_kind] = status

        # D (life-v1): the continuity read comes first, because it is what decides *which*
        # work this round continues.  It is a read of the owners C was given: it authorizes
        # nothing, and a failed read is reported instead of being read as "no history".
        continuity = self.read_continuity()
        if continuity is not None:
            step = continuity.get("next_step") or {}
            found["continuity"] = {
                "next_step": (step.get("reason_code") if isinstance(step, Mapping) else None),
                "item_ref": (step.get("item_ref") if isinstance(step, Mapping) else None),
                "continue_from_ref": (step.get("continue_from_ref")
                                      if isinstance(step, Mapping) else None),
                "past_state": continuity.get("past_state"),
                "past_count": len(continuity.get("past") or []),
                "unfinished_count": len(continuity.get("unfinished") or []),
                "waiting_count": len(continuity.get("waiting") or []),
                "refusals": list(continuity.get("refusals") or []),
                "read_error": continuity.get("read_error"),
                "may_start_activity": continuity.get("may_start_activity"),
            }

        if self.artifact_port is not None:
            point, selection = self._read_resume_point()
            found["artifact_selection"] = selection
            if point is not None:
                found["continuable_artifact"] = point.to_dict()
            elif selection.get("error"):
                found["artifact_read_error"] = selection["error"]

        # D (life-v1): the reading material is material in its own right -- an instance whose
        # only real opportunity is a page to read must still be able to consider it.
        if self.reading_available():
            found["reading_material"] = {"artifact_id": self.reading_artifact_id,
                                         "private_items_artifact_id":
                                             self.private_items_artifact_id,
                                         "source_kind": "PERSONAL_OPPORTUNITY"}

        # A (life-v1): whether this instance has a short-appraisal source at all.  It is *not*
        # material: an empty appraisal never makes a round happen (it cannot starve or wake
        # anything), it only adds bounded context when it is valid.
        found["appraisal_wired"] = self.appraisal_source is not None

        # B6-5: ask the World what she can actually perceive right now.  This is a bounded read
        # through the gateway, not a model call, and it is the only way AG-0 may ever see a
        # World opportunity: the source is fed from outside by design.
        observed = self._refresh_world_opportunities()
        found["world_opportunities"] = [item.get("opportunity_id") for item in observed]
        # I: what she saw, with the owner verb each opportunity implies and the place facts
        # (distance, opening state, uncertainty).  Report only -- nothing here is a grant.
        found["world_opportunity_view"] = [
            {key: item.get(key) for key in (
                "opportunity_id", "kind", "target_ref", "object_id", "location_id",
                "source", "observed", "availability", "availability_reason",
                "walk_seconds", "valid_until")}
            for item in self.last_world_opportunity_view]
        if self.last_world_catalog is not None:
            found["world_catalog"] = {
                "status": self.last_world_catalog.get("status"),
                "reason_code": self.last_world_catalog.get("reason_code"),
                "schedule_source": self.last_world_catalog.get("schedule_source"),
                "current_location_id": self.last_world_catalog.get("current_location_id"),
            }
        if self.last_world_error:
            found["world_error"] = self.last_world_error

        found["has_material"] = bool(
            found["foreground_activity"] or found["continuable_artifact"] or observed
            or found.get("reading_material"))
        return found

    # -- the cycle ---------------------------------------------------------------------

    def evaluate_once(
        self,
        *,
        trigger_ref: str,
        trigger_kind: str = TRIGGER_IDLE_WINDOW,
        observed_at: Optional[str] = None,
        act: bool = True,
    ) -> dict[str, Any]:
        """One real evaluation.  Returns a compact, honest report; never raises for a refusal."""
        obs = observed_at or _utc_now_iso()
        base: dict[str, Any] = {"trigger_kind": trigger_kind, "trigger_ref": trigger_ref,
                                "observed_at": obs}

        if not self.enabled:
            return self._finish(base, OUTCOME_DISABLED, llm_calls=0,
                                detail="autonomous life is off")
        hold = self.pause_gate()
        if hold["blocked"]:
            # W05: the operator's persistent pause.  Nothing is considered, no budget is spent
            # and no external action is started; an already departed segment still settles.
            return self._finish(base, OUTCOME_PAUSED_BY_OPERATOR, llm_calls=0,
                                detail="life is held: %s" % hold["reason_code"])
        if self.budget.remaining() <= 0:
            return self._finish(base, OUTCOME_BUDGET_EXHAUSTED, llm_calls=0,
                                detail=f"day budget of {self.budget.max_rounds_per_day} rounds spent")

        material = self.materials()
        base["material"] = material
        if not material["has_material"]:
            # No round is charged: nothing was even asked.
            return self._finish(base, OUTCOME_NO_MATERIAL, llm_calls=0,
                                detail="no real material to evaluate; no model call made")

        self.budget.spend()
        self.rounds_run += 1

        # D (life-v1): the same selection the material check made -- the bound work, or the
        # artifact the admitted history points at.  ``latest_resumable`` is only reached when
        # no work was bound at all, so a newer unrelated artifact cannot hijack the round.
        resume_point: Optional[ArtifactResumePoint] = None
        if self.artifact_port is not None:
            resume_point, selection = self._read_resume_point()
            base["artifact_selection"] = selection
            if resume_point is None and selection.get("error"):
                base["artifact_read_error"] = selection["error"]

        has_foreground = bool(material.get("foreground_activity"))
        artifact_available = resume_point is not None
        world_available = bool(self.last_world_opportunities)

        # W05R: when a continuable work *and* an observed reachable place are both legal
        # in the same round, neither may be starved by a priority rule fixed before the
        # Decision.  Both Activity shapes are described, and the committed Decision's own
        # selected candidate decides which one is registered.  With only one path
        # available the previous behaviour is kept exactly.
        # An adoption spec describes how to *create* an Activity, so it is only supplied when
        # nothing is running.  With a foreground Activity present the only legal move is to
        # continue/resume that one, and offering a creation spec there would be asking AG-2 to
        # start a second foreground activity -- which it correctly refuses.
        # W05R-R1: the World Activity's completion contract names the destination the
        # committed Decision chose, so the shape is built from that Decision -- both when
        # it is the only path and when it is routed against the artifact path.  AG-2
        # accepts a callable ``adoption_spec`` for exactly this.
        # D (life-v1): the reading material is a *second* legal opportunity in the same round.
        # It comes from the admitted PERSONAL_OPPORTUNITY source (the artifact continuation
        # source lists every ACTIVE artifact), and the Decision -- not a fixed priority -- is
        # what chooses between continuing the work and reading.
        reading_available = self.reading_available()

        if not has_foreground and (artifact_available or world_available or reading_available):
            work_spec = self._adoption_spec(resume_point) if artifact_available else None
            if (world_available and reading_available) or (artifact_available
                                                          and (world_available
                                                               or reading_available)):
                adoption_spec: Any = _RoutedAdoptionSpec(
                    artifact=work_spec or self._reading_adoption_spec(),
                    world=lambda decision: self._world_adoption_spec(
                        self.last_world_opportunities, decision=decision),
                    is_world=self._decision_chose_world,
                    reading=lambda decision: self._reading_adoption_spec(),
                    is_reading=self._decision_chose_reading)
            elif reading_available and not artifact_available:
                adoption_spec = self._reading_adoption_spec()
            elif artifact_available:
                adoption_spec = work_spec
            else:
                # B6-5: the Activity shape is derived from the observed opportunity *set*,
                # never from a chosen destination: choosing is AG-1's job and it has not
                # happened yet at this point.
                adoption_spec = _DecisionBoundAdoptionSpec(
                    lambda decision: self._world_adoption_spec(
                        self.last_world_opportunities, decision=decision))
        else:
            adoption_spec = None

        # When a real AR-0 adapter is wired, the single step goes through AR-0: proposal, the
        # durable submission fence, an attempt, the owner's receipt, and reconciliation by the
        # original operation.  Without one, nothing crosses a submission fence at all.
        ar0_step = None
        if resume_point is not None and act and self._ar0_enabled():
            # Lazy on purpose: AG-2 materialises this override only *after* the decision, so the
            # content model call happens when a step will really be executed -- never for a
            # NO_ACTION or DEFER round.  A writer failure surfaces from that materialisation.
            ar0_step = _LazyStepSpec(
                lambda: self._ar0_step_spec(resume_point, observed_at=obs))

        # D (life-v1): the reading step.  Its own owner action: read one page from the material
        # and commit the bookmark, both through the existing port and owner.
        reading_step = None
        if act and not has_foreground and reading_available and self._ar0_enabled():
            reading_step = _DecisionBoundStepSpec(
                lambda _decision: self._reading_step_spec(observed_at=obs))

        # B6-5: the World step.  Same seam, same laziness, one difference -- a MOVE's shape
        # depends on *which* place the Decision chose, so this override is a callable of the
        # committed Decision rather than a fixed Mapping.
        world_step = None
        if act and not has_foreground and self._world_step_enabled() and world_available:
            world_step = _DecisionBoundStepSpec(self._world_step_spec)

        # I-r1: a stay she is already in is the *only* legal continuation of its own round --
        # the foreground slot is a single one (AG-2 refuses a second), so while she is staying
        # neither an artifact step nor a reading step may be offered in its place.  The step is
        # Life's own internal checkpoint (no World action, see ``_stay_step_spec``), and it can
        # only end the stay after the declared stretch of time really elapsed.
        stay_activity = self._foreground_stay_activity() if act else None
        if stay_activity is not None:
            stay_target = (self._stay_plan_from_activity(stay_activity) or {}).get("target_ref")
            if not stay_target:
                return self._finish(
                    base, OUTCOME_STEP_REFUSED, llm_calls=1,
                    detail="the foreground Activity is a stay but the owner's own record "
                           "carries no stay plan; nothing is continued")
            step_spec = _LazyStepSpec(
                lambda: self._stay_step_spec(str(stay_target), phase=STAY_PHASE_END,
                                             observed_at=obs))
            base["step_path"] = "STAY_CHECKPOINT"
            base["stay_activity_ref"] = stay_activity.get("activity_ref") or stay_activity.get(
                "activity_id")
            routed = False
        else:
            # W05R: with more than one path available the step is routed by the committed
            # Decision, not by a fixed "the artifact keeps priority" rule.  That rule is what
            # let real work starve every observed place for a whole day -- and it would let
            # work starve reading.
            live_paths = [name for name, spec in (("AR0", ar0_step), ("READING", reading_step),
                                                  ("WORLD", world_step)) if spec is not None]
            if len(live_paths) > 1:
                step_spec = _DecisionBoundStepSpec(
                    self._routed_step_builder(resume_point, observed_at=obs))
                base["step_path"] = ("AR0_WORLD_ROUTED" if set(live_paths) == {"AR0", "WORLD"}
                                     else "%s_ROUTED" % "_".join(live_paths))
            elif live_paths == ["AR0"]:
                step_spec = ar0_step
                base["step_path"] = "AR0"
            elif live_paths == ["READING"]:
                step_spec = reading_step
                base["step_path"] = "READING_AR0"
            elif live_paths == ["WORLD"]:
                step_spec = world_step
                base["step_path"] = "WORLD_AR0"
            else:
                step_spec = None
                base["step_path"] = "PORT_DIRECT"
            routed = len(live_paths) > 1

        # D (life-v1): the capacity columns are the owners', not this module's.  A's
        # situation view supplies the body column (a real Body read, or UNKNOWN) and the
        # resource column (this instance's own request-ledger read, where a corrupt
        # ledger is a fault and not "spent").  The previous revision derived "capacity"
        # from the round budget, which presented a budget fact as a body fact; that
        # derivation is gone.  ``act=False`` still means "cannot execute here", which is
        # a statement about this caller, so it is carried as its own fact.
        situation = self.read_situation(obs)
        capacity_kwargs = self.capacity_kwargs(situation)
        base["situation"] = {
            "reason_code": situation.get("reason_code"),
            "observed_at": situation.get("observed_at"),
            "identity_bound": situation.get("identity_bound"),
            "body": _compact_column(situation.get("body")),
            "resource_budget": _compact_column(situation.get("resource_budget")),
            "dimensions": situation.get("dimensions"),
            "limiting_dimensions": situation.get("limiting_dimensions"),
            "unknown_dimensions": situation.get("unknown_dimensions"),
            "knowledge": _compact_knowledge(situation.get("knowledge")),
            "source_revisions": situation.get("source_revisions"),
        }
        base["capacity_kwargs"] = dict(capacity_kwargs)
        base["capacity_state"] = capacity_kwargs.get("body_capacity_state")
        base["resource_capacity_state"] = capacity_kwargs.get("resource_availability_state")
        # ``act=False`` and "no step owner is wired" are facts about *this caller*, not
        # about the body: they are recorded as their own field and never written into a
        # capacity column (A's rule: a missing signal is UNKNOWN, not a body fact).
        base["execution_possible"] = bool(
            act and (bool(self.artifact_grant_id) or self._world_step_enabled()))
        base["execution_possible_basis"] = (
            "act" if not act else ("ARTIFACT_GRANT" if self.artifact_grant_id
                                   else ("WORLD_STEP" if self._world_step_enabled()
                                         else "NO_STEP_OWNER_WIRED")))

        # W05R-R1: the round is deliberately *not* wrapped in one pause boundary.  A
        # boundary held across the decision and content requests made the operator's
        # ``pause`` wait behind a model call, which is not a finite commit and is exactly
        # what a confirmation must never be queued behind.  The bit is instead re-read at
        # every boundary that immediately precedes an external effect:
        #
        #   * here, before the decision request starts -- an entry gate that was passed
        #     while the pause was being confirmed cannot start a request;
        #   * ``_pause_gate_hook``, after the decision commits and before adoption/content;
        #   * ``_pause_fault_hook``, after the content request and again immediately before
        #     the owner call, where the shared lock is held across that one finite call.
        #
        # A request that has already started is allowed to finish; the re-read after it
        # returns is what stops its result from reaching any owner.
        hold = self._pause_hold()
        if hold["blocked"]:
            base["pause_boundary"] = {"held_after": "before_request",
                                      "reason_code": hold["reason_code"]}
            return self._finish(base, OUTCOME_PAUSED_BY_OPERATOR, llm_calls=0,
                                detail="life is held at the request boundary: %s"
                                       % hold["reason_code"])
        # A (life-v1): the round's bounded consideration context is admitted experiences **plus**
        # the still-valid sourced short appraisals.  Reading it here (after the pause gate, before
        # the request) means an unwired / empty / corrected / expired appraisal costs nothing at
        # all: it cannot create a round, and it can never become a Candidate or a permission.
        consideration = self.consideration_context()
        # I-r2: what the World itself said about opening hours for the places this round can
        # choose between.  It is a *fact* channel, not an offer: it can inform the choice (the
        # model reads it as ``observed_fact_keys``) and it can never create a candidate, a
        # permission or a target.  Read from the observation that was already taken, so it
        # costs neither an owner call nor a second clock.
        world_facts = self.world_observation_facts()
        base["world_facts"] = world_facts
        base["appraisal"] = self._compact_appraisal(self.last_appraisal)
        try:
            out = self.runtime.coordinator.run_decision_and_adoption_cycle(
                trigger_kind=trigger_kind,
                trigger_ref=trigger_ref,
                observed_at=obs,
                adoption_spec=adoption_spec,
                step_spec_override=step_spec,
                execute_initial_step=(step_spec is not None),
                # D (life-v1): the four columns AG-1 reads, taken from the situation view
                # (state and reference always together, so AG-1 cannot fabricate AVAILABLE
                # from a reference alone).  ``unknown_capacity_policy`` stays unset.
                prior_experiences=consideration,
                observed_facts=world_facts,
                **capacity_kwargs,
                pre_adoption_mutation_hook=self._pause_gate_hook,
                fault_hook=self._pause_fault_hook,
            )
        except life_pause.PauseConfirmedError as exc:
            return self._pause_abort(base, exc, llm_calls=1)
        except ag2.KillSwitchDisabledError as exc:
            return self._finish(base, OUTCOME_DISABLED, llm_calls=0, detail=str(exc))
        except life_request_budget.RequestBudgetError as exc:
            # W05R: a request-budget refusal is neither a model choice nor a model
            # failure.  It is reported as exactly what it is.
            return self._finish(base, OUTCOME_BUDGET_EXHAUSTED, llm_calls=0,
                                detail="request budget refused: %s" % exc.reason_code)
        except Exception as exc:  # a real failure, reported as one
            self.last_error = f"{type(exc).__name__}: {exc}"
            if "REQUEST_BUDGET_" in str(exc):
                return self._finish(base, OUTCOME_BUDGET_EXHAUSTED, llm_calls=0,
                                    detail=self.last_error[:300])
            if type(exc).__name__ == "ModelContentError" or "content writer" in str(exc):
                # The decision happened; producing the content did not.  Nothing was written.
                return self._finish(base, OUTCOME_STEP_REFUSED, llm_calls=1,
                                    detail=f"content writer failed: {self.last_error}"[:300])
            return self._finish(base, OUTCOME_DECISION_FAILED, llm_calls=0,
                                detail=self.last_error)
        finally:
            # The lease is only ever held across a finite owner call; releasing it here is
            # what makes a raise anywhere in the round unable to lock the operator out.
            self._pause_lease.release()

        dec_res = out.get("decision_result") or {}
        record = dec_res.get("decision_record")
        adp_rec = (out.get("adoption_result") or {}).get("adoption_record")
        int_rec = out.get("integration_record")
        base.update({
            "correlation_id": out.get("correlation_id"),
            "decision_route": dec_res.get("decision_route"),
            "decision": getattr(record, "decision", None),
            "reason_codes": list(getattr(record, "reason_codes", []) or []),
            "short_rationale": getattr(record, "short_rationale", None),
            "selected_candidate_ref": getattr(record, "selected_candidate_ref", None),
            "adoption_status": getattr(adp_rec, "adoption_status", None),
            "activity_ref": getattr(int_rec, "activity_ref", None),
            # I-r2: the integration this round really committed.  It is what an owner-confirmed
            # World consequence is handed to Memory through (``emit_experience_handoff``), so it
            # travels with the round instead of being recovered later.
            "integration_id": getattr(int_rec, "integration_id", None),
        })

        if record is None:
            # No Decision was committed: the round never legally considered the eligibility C
            # offered, so nothing about "already seen" may be advanced here.
            failure = str(dec_res.get("failure_class") or "DECISION_NOT_COMMITTED")
            self.last_error = failure
            return self._finish(base, OUTCOME_DECISION_FAILED, llm_calls=1, detail=failure)

        # D (life-v1): the Decision committed, so this round really considered what C offered.
        # Only now does the "seen" watermark advance; the eligibility itself stays OPEN in LR-4
        # until the owner reports it consumed (reading is not adopting).
        self._record_continuity_progress(observed_at=obs)
        decision = str(getattr(record, "decision", ""))
        if decision == ag1.DECISION_RESUME and str(
                getattr(adp_rec, "adoption_status", "") or "") == "APPLIED":
            # The owner really resumed the private work from *one* eligibility: that one -- and
            # only that one -- is consumed.  The ref comes from the committed Decision's own
            # selected candidate, read back from LR-4 as CONSUMED for the Activity the adoption
            # really resumed; an unattributable consumption is reported and writes nothing
            # (the OPEN eligibilities C merely showed us are "seen", never "adopted").
            consumed_ref, consumed_reason = self._resumed_consumed_eligibility(
                record, adp_rec, int_rec)
            if consumed_ref:
                self._record_continuity_progress(observed_at=obs, adopted_refs=[consumed_ref])
                base["continuity_consumed_eligibility_ref"] = consumed_ref
            else:
                base["continuity_adoption_unresolved"] = consumed_reason
        base["continuity_watermark"] = self.read_continuity_watermark()
        # I-r3: a stay she is in can also be *left by choice*.  AG-1 may name PAUSE or ABANDON
        # only where that is legal for the running Activity, AG-2 applies its own existing
        # state command (LR-2), and this branch reports what the owner really did.  It never
        # fabricates a completion, never moves the recorded elapsed time, and never leaves a
        # second Activity in the foreground: an early exit is its own outcome, not a served
        # stay.  A Decision the owner refuses is reported as refused and changes nothing.
        if stay_activity is not None and decision in {ag1.DECISION_PAUSE, ag1.DECISION_ABANDON}:
            return self._close_stay_by_choice(
                base, obs, activity=stay_activity, decision=decision,
                adoption_status=str(getattr(adp_rec, "adoption_status", "") or ""),
                rejection=str(getattr(adp_rec, "rejection_reason_code", "") or ""))
        if decision in {ag1.DECISION_NO_ACTION, ag1.DECISION_DEFER}:
            self.last_error = None
            return self._finish(base, OUTCOME_NO_ACTION if decision == ag1.DECISION_NO_ACTION
                                else OUTCOME_DEFERRED, llm_calls=1,
                                detail=f"decision={decision}")
        if decision in {ag1.DECISION_PAUSE, ag1.DECISION_WAIT}:
            return self._finish(base, OUTCOME_PAUSED, llm_calls=1, detail=f"decision={decision}")
        if decision == ag1.DECISION_ABANDON:
            return self._finish(base, OUTCOME_ABANDONED, llm_calls=1, detail="decision=ABANDON")

        adoption_status = str(getattr(adp_rec, "adoption_status", "") or "")
        if adoption_status.startswith("REJECTED"):
            return self._finish(base, OUTCOME_ADOPTION_REJECTED, llm_calls=1,
                                detail=str(getattr(adp_rec, "rejection_reason_code", adoption_status)))

        # Adopted.  Now do the one narrow real step -- the owner it goes to is the one the
        # committed Decision's own candidate named.
        # I-r1: the staying round.  Its step is the internal checkpoint, so the World half of
        # ``_classify_world_step`` is not reached; the stay's own facts are recorded there.
        if stay_activity is not None:
            return self._classify_world_step(out, base, obs)

        if self._decision_chose_reading(record) and reading_step is not None:
            return self._classify_reading_step(out, base, obs)

        if routed:
            if self._decision_chose_world(record):
                return self._classify_world_step(out, base, obs)
            return self._classify_ar0_step(out, base, obs)

        if world_step is not None:
            return self._classify_world_step(out, base, obs)

        if reading_step is not None:
            return self._classify_reading_step(out, base, obs)

        if ar0_step is not None:
            return self._classify_ar0_step(out, base, obs)


        if not act or resume_point is None or not self.artifact_grant_id or not self.workspace_id:
            return self._finish(base, OUTCOME_ACTED, llm_calls=1,
                                detail="adopted with no narrow step available in this revision")

        # No AR-0 adapter is wired, so no action crosses a submission fence.  The step is still
        # performed against the real owner through the narrow port and its receipt is journalled.
        # W05R-R1: the content request inside this call is a model wait and is therefore made
        # without the pause lock; the owner call inside it is guarded by the lease, and a
        # confirmation that arrived while the content request was in flight aborts here.
        try:
            receipt = self._commit_next_version(resume_point, base, obs)
        except life_pause.PauseConfirmedError as exc:
            return self._pause_abort(base, exc, llm_calls=1)

        self.last_receipt = receipt
        base["step_receipt"] = receipt.to_dict()
        if receipt.effect_state == EffectState.APPLIED:
            return self._finish(base, OUTCOME_ACTED, llm_calls=1,
                                detail=f"committed version {receipt.version_number}")
        if receipt.effect_state == EffectState.UNKNOWN:
            return self._finish(base, OUTCOME_STEP_UNKNOWN, llm_calls=1,
                                detail=f"{receipt.status}:{receipt.reason}")
        if receipt.effect_state == EffectState.NO_CHANGE:
            return self._finish(base, OUTCOME_ALREADY_DONE, llm_calls=1,
                                detail=receipt.reason or "no change needed")
        return self._finish(base, OUTCOME_STEP_REFUSED, llm_calls=1,
                            detail=f"{receipt.status}:{receipt.reason}")

    # -- AR-0 step (E01/E05) -----------------------------------------------------------

    def _ar0_enabled(self) -> bool:
        """True when the composition root has a real AR-0 adapter for the artifact verbs."""
        return getattr(self.runtime, "artifact_action_adapter", None) is not None

    def _ar0_step_spec(self, point: ArtifactResumePoint, *, observed_at: str) -> dict[str, Any]:
        """One bounded step, described for AR-0.  No second planner is introduced."""
        content = self.writer.next_content(point, observed_at=observed_at)
        self.last_step_progress_target_ref = point.artifact_id
        return {
            "step_kind": ag2.STEP_KIND_FILE_ACTION,
            "action_kind": ACTION_KIND_FILE,
            "target_ref": f"artifact:{point.artifact_id}",
            "payload_spec": {
                "op": OP_COMMIT_VERSION,
                "artifact_id": point.artifact_id,
                "expected_head": point.head_version_id,
                "content": content,
                "grant_id": str(self.artifact_grant_id),
            },
            "expected_effect_predicates": ["artifact_written_verified"],
            "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
            # Deterministic per (work, head): one intended edit, one logical action, one Supply
            # operation -- so a reconciliation can never mint a second version.
            # The key encodes *what is intended*: the work, the head it was based on, and the
            # bytes to write.  Two different intended edits are two different logical actions;
            # repeating the same intent reuses the same action and the same Supply operation
            # instead of writing a second version.
            "idempotency_key": (
                f"idem_step:v02:{point.artifact_id}:{point.head_version_id}:"
                f"{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"),
        }

    def _classify_ar0_step(self, out: Mapping[str, Any], base: dict[str, Any],
                           obs: str) -> dict[str, Any]:
        """Report the AR-0 result as exactly what the owner confirmed -- never more."""
        step_res = out.get("step_execution_result") or {}
        submit = step_res.get("submit_result") or {}
        action = submit.get("action") or {}
        status = str(action.get("status") or "")
        receipts: list[dict[str, Any]] = []
        if action.get("action_id"):
            try:
                receipts = self.runtime.action_read.list_receipts(str(action["action_id"]))
            except Exception:  # pragma: no cover - read failure is reported, not invented
                receipts = []
        receipt = receipts[-1] if receipts else None
        details = dict((receipt or {}).get("details") or {})

        base.update({
            "action_id": action.get("action_id"),
            "action_status": status,
            "provider_operation_ref": action.get("provider_operation_ref"),
            "owner_status": details.get("owner_status"),
            "owner_version_id": details.get("version_id"),
            "owner_version_created": details.get("version_created"),
            "owner_receipt": receipt,
        })

        # Journal the step with the same shape the direct-port path uses, so one file answers
        # "what did she actually do" regardless of which path executed it.
        payload = dict(action.get("frozen_payload") or {})
        if status == "SUCCEEDED" and details.get("version_created") is not False:
            effect_state = EffectState.APPLIED
        elif status == "SUCCEEDED":
            effect_state = EffectState.NO_CHANGE
        elif status == "FAILED":
            effect_state = EffectState.REFUSED
        else:
            effect_state = EffectState.UNKNOWN
        step_receipt = StepReceipt(
            observed_at=_utc_now_iso(),
            artifact_id=str(payload.get("artifact_id") or ""),
            expected_head=payload.get("expected_head"),
            operation_key=str(action.get("provider_operation_ref") or ""),
            effect_state=effect_state,
            status=status,
            reason=str(details.get("owner_status") or ""),
            version_id=details.get("version_id"),
            version_number=details.get("version_number"),
            head_version_id=details.get("version_id"),
            correlation_id=base.get("correlation_id"),
            decision_id=base.get("decision_id"),
            activity_ref=base.get("activity_ref"),
            detail=str(details.get("detail") or "")[:200],
        )
        self.last_receipt = step_receipt
        base["step_receipt"] = step_receipt.to_dict()
        self._journal(step_receipt)

        if status == "SUCCEEDED":
            if details.get("version_created") is False:
                # The action happened; the work did not change.  That is not a new version and it
                # is not counted as one.
                return self._finish(base, OUTCOME_ALREADY_DONE, llm_calls=1,
                                    detail=f"owner answered {details.get('owner_status')}; no new version")
            # The progress/experience belongs to the object the step was *about*: for a page read
            # that is the reading material, not the document the bytes live in.
            progress_ref = (payload.get("life_progress_target_ref")
                            or self.last_step_progress_target_ref
                            or payload.get("artifact_id"))
            self._record_artifact_progress(
                base, activity_ref=base.get("activity_ref"),
                artifact_id=progress_ref, version_id=details.get("version_id"),
                version_number=details.get("version_number"),
                correlation_id=base.get("correlation_id"), action_ref=action.get("action_id"))
            # A real, owner-confirmed change is the milestone Memory cares about.  AG-2 only
            # emitted COMPLETED/ABANDONED/CONFLICT before, so "she actually changed something"
            # had no handoff at all.
            self._emit_progress_experience(
                out, base, artifact_ref=progress_ref,
                version_ref=details.get("version_id"))
            return self._finish(base, OUTCOME_ACTED, llm_calls=1,
                                detail=f"owner committed {details.get('version_id')}")
        if status == "UNKNOWN":
            return self._finish(
                base, OUTCOME_STEP_UNKNOWN, llm_calls=1,
                detail=f"action {action.get('action_id')} result unknown; "
                       "reconcile the original operation before any retry")
        if status == "FAILED":
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail=f"owner refused: {details.get('owner_status')} "
                       f"{details.get('detail') or ''}".strip())
        return self._finish(base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                            detail=f"action status {status!r} did not settle in this round")

    # -- World step (B6-5) --------------------------------------------------------------

    def _world_step_enabled(self) -> bool:
        """True when a real World action adapter AND a real observation seam are wired.

        Both halves are required: an observation with no adapter could only ever be
        described, and an adapter with no observation would have nothing real to act on.
        """
        return (self.world_opportunity_provider is not None
                and getattr(self.runtime, "world_action_adapter", None) is not None)

    def _refresh_world_opportunities(self) -> list[dict[str, Any]]:
        """Feed AG-0 the World opportunities one real observation supports.

        AG-0's ``WorldOpportunityCandidateSource`` is fed from outside by design -- it
        may not scan the World itself -- so this is the only door through which a World
        fact can become a candidate.  Recording is idempotent: the opportunity id is
        derived from the place, the World revision and the destination, so observing
        twice from the same state produces the same records, not duplicates.
        """
        self.last_world_opportunities = []
        self.last_world_error = None
        if self.world_opportunity_provider is None:
            return []
        source = getattr(self.runtime, "world_opportunity_source", None)
        if source is None:
            self.last_world_error = "runtime has no world_opportunity_source"
            return []
        try:
            observed = list(self.world_opportunity_provider() or [])
        except Exception as exc:  # a broken seam is reported, never papered over
            self.last_world_error = f"{type(exc).__name__}: {exc}"
            return []
        # The provider read one observation; keep it so the step spec can name the place
        # she was in and the World revision the observation came from.
        seen = getattr(self.world_opportunity_provider, "last_observation", None)
        if isinstance(seen, dict):
            self.last_world_observation = dict(seen)
        recorded: list[dict[str, Any]] = []
        for item in observed:
            try:
                source.record_observed_opportunity(item)
            except Exception as exc:
                self.last_world_error = f"{type(exc).__name__}: {exc}"
                continue
            recorded.append(dict(item))
        self.last_world_opportunities = recorded
        # I-r1: an opportunity the *current* observation no longer supports is expired in
        # AG-0's own source.  Without this, every round would keep its predecessor's places and
        # objects alive until their validity window ran out, and a longer day would fill AG-1's
        # candidate ceiling (16) with offers nobody can pick any more instead of with real ones.
        # The call is AG-0's own public API; nothing about a candidate is written here.
        current_ids = {str(item.get("opportunity_id")) for item in recorded}
        stale_ids = set(getattr(self, "_fed_world_opportunity_ids", set())) - current_ids
        self._fed_world_opportunity_ids = current_ids
        expire = getattr(source, "expire_opportunity", None)
        if stale_ids and callable(expire):
            moment = str((recorded[0].get("observed_at") if recorded else "")
                         or _utc_now_iso())
            for opportunity_id in sorted(stale_ids):
                try:
                    expire(opportunity_id, observed_at=moment)
                except Exception as exc:  # an unknown id is reported, never fatal
                    self.last_world_error = f"{type(exc).__name__}: {exc}"
        # I: the provider's richer view of the *same* opportunities.  AG-0's own record
        # contract accepts exactly its declared fields, so the extra facts (owner verb, walk
        # seconds, opening state, uncertainty) are kept beside it instead of being smuggled
        # into the candidate contract.
        view = getattr(self.world_opportunity_provider, "last_opportunity_view", None)
        self.last_world_opportunity_view = (
            [dict(item) for item in view if isinstance(item, Mapping)]
            if isinstance(view, list) else [])
        catalog = getattr(self.world_opportunity_provider, "last_catalog", None)
        self.last_world_catalog = dict(catalog) if isinstance(catalog, Mapping) else None
        return recorded

    def _chosen_target_ref(self, decision_record: Any) -> str:
        """The World target the committed Decision selected, from its own candidate record.

        The discriminator is the candidate's own ``target_refs`` namespace -- ``place:``
        for a MOVE, ``object:`` for a CONSUME, ``stay:`` for a bounded non-productive stay
        -- so the completion contract, the step spec and the report all name the same
        target and none of them re-derives it from the model or from a fresh guess.
        """

        candidate = self._selected_candidate(decision_record)
        for ref in (candidate or {}).get("target_refs") or []:
            text = str(ref)
            if (text.startswith(PLACE_REF_PREFIX)
                    or text.startswith(WORLD_OBJECT_REF_PREFIX)
                    or text.startswith(STAY_REF_PREFIX)):
                return text
        return ""

    def _chosen_place(self, decision_record: Any) -> str:
        """The place the committed Decision selected (``""`` for an object or a stay).

        A stay names the place she is *already* in, so it is not a destination: returning it
        here would let a stay satisfy a MOVE's arrival criterion.
        """

        target = self._chosen_target_ref(decision_record)
        if target.startswith(PLACE_REF_PREFIX):
            return target.split(":", 1)[1]
        return ""

    def _world_adoption_spec(self, opportunities: list[dict[str, Any]], *,
                             decision: Any = None) -> dict[str, Any]:
        """The Activity shape for going somewhere she actually observed.

        LR-5 already owns this vocabulary (``WORLD_ACTION_BOUNDED`` /
        ``MAX_CONFIRMED_POSITION`` / ``WORLD_CANONICAL_OBSERVATION``), so no new
        progress semantics are invented.  The target set is every place the observation
        offered -- this method does not choose, because AG-1 has not chosen yet.

        W05R-R1: the same Activity also gets the **completion contract** its progress
        contract has always implied.  Without one, reaching the place advanced a
        checkpoint and nothing else: the Activity stayed ACTIVE forever, so its
        foreground slot could never be released and every later round that wanted to do
        something else was refused with ``CANNOT_START_WHILE_FOREGROUND_ACTIVE``.  The
        criterion is the existing ``WORLD_FACT_MATCH`` evaluated against the World
        owner's own canonical observation, and it names the destination the committed
        Decision chose -- ``decision`` is passed in by the caller for exactly that
        reason.  With no committed Decision to name a place, no completion contract is
        invented: the previous behaviour is kept rather than a wrong target declared.
        """
        targets: list[str] = []
        origin = ""
        for item in opportunities:
            if not origin:
                origin = str(item.get("location_ref") or "")
            for ref in item.get("target_refs") or []:
                # LR-5's checkpoint reference must live in a namespace it recognises,
                # and its binding matches on a prefix: the World's own `world_fact`
                # namespace is the one it already owns for exactly this.
                text = "world_fact:%s" % ref
                if text not in targets:
                    targets.append(text)
        if not targets:
            raise AutonomousLifeError("no observed destination to adopt an Activity for")
        if ":" not in origin:
            origin = targets[0]
        origin = "world_fact:%s" % origin
        # LR-5 owns these policy versions (activity_progress_completion_lr5): a MOVE advances
        # a *position*, a CONSUME reaches an object's target state, so no new policy version
        # is invented here.
        import activity_progress_completion_lr5 as lr5

        chosen_target = self._chosen_target_ref(decision) if decision is not None else ""
        if chosen_target.startswith(WORLD_OBJECT_REF_PREFIX):
            # An instant owner mutation, not a journey: its own Activity shape and its own
            # completion criterion, because "the object is gone" is not evidence -- the World
            # owner's own receipt for *this* instance is.
            return self._consume_adoption_spec(targets, origin, chosen_target, lr5)
        if chosen_target.startswith(STAY_REF_PREFIX):
            # A non-productive stay: bounded, checkpointed, and finished only by the World
            # owner's own observation at the declared end.  It mints no World action.
            return self._stay_adoption_spec(targets, origin, chosen_target, lr5)

        completion_spec = None
        chosen = self._chosen_place(decision) if decision is not None else ""
        chosen_ref = ("%s%s" % (WORLD_PLACE_FACT_PREFIX, chosen)) if chosen else ""
        if chosen_ref and chosen_ref in targets:
            # ``WORLD_FACT_MATCH`` on the place fact ``_record_world_progress`` submits with
            # the World owner's own observation: arriving where the Decision chose satisfies
            # it, and nothing else does.
            completion_spec = {
                "criteria_kind": lr5.CRITERIA_WORLD_FACT_MATCH,
                "target_refs": targets,
                "criteria": {"fact_key": WORLD_PLACE_FACT_KEY, "expected_value": chosen_ref},
                "required_evidence_kinds": [lr5.SOURCE_KIND_WORLD_FACT],
                "required_authority_domains": ["world_authority"],
                "policy_version": lr5.POLICY_WORLD_FACT_V1,
            }

        spec = ag2.ActivityAdoptionSpec(
            activity_kind="GO_TO_OBSERVED_PLACE",
            title="去她观察到的地方",
            target_refs=targets,
            origin_ref=origin,
            # Reaching a place is a bounded action with a real end state, unlike an
            # open-ended piece of work: the checkpoint is the furthest place the World
            # owner has actually confirmed.
            progress_contract_spec={
                "progress_kind": "WORLD_ACTION_BOUNDED",
                "checkpoint_semantics": "MAX_CONFIRMED_POSITION",
                "target_refs": targets,
                "policy_version": lr5.POLICY_POSITION_V1,
            },
            completion_contract_spec=completion_spec,
            result_binding_spec={
                "target_refs": targets,
                "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "WORLD_CANONICAL_OBSERVATION"],
                "authority_domains": ["result_settlement_authority", "world_authority"],
                "policy_version": lr5.POLICY_POSITION_V1,
            },
        )
        return spec.to_dict()

    def _consume_adoption_spec(self, targets: list[str], origin: str, chosen_target: str,
                               lr5: Any) -> dict[str, Any]:
        """The Activity shape for using up one observed object instance.

        ``WORLD_ACTION_BOUNDED`` + ``TARGET_STATE`` is LR-5's own vocabulary for a bounded
        World action whose end state belongs to the object, and the completion criterion is
        the World owner's consumption receipt for exactly the instance the Decision chose.
        """

        object_fact_ref = "world_fact:%s" % chosen_target
        spec = ag2.ActivityAdoptionSpec(
            activity_kind="CONSUME_OBSERVED_OBJECT",
            title="用掉一份她观察到的东西",
            target_refs=targets,
            origin_ref=origin,
            progress_contract_spec={
                "progress_kind": "WORLD_ACTION_BOUNDED",
                "checkpoint_semantics": lr5.CHECKPOINT_SEMANTICS_TARGET_STATE,
                "target_refs": targets,
                "policy_version": lr5.POLICY_POSITION_V1,
            },
            completion_contract_spec={
                "criteria_kind": lr5.CRITERIA_WORLD_FACT_MATCH,
                "target_refs": targets,
                "criteria": {"fact_key": WORLD_CONSUME_FACT_KEY,
                             "expected_value": "%s:consumed" % object_fact_ref},
                "required_evidence_kinds": [lr5.SOURCE_KIND_WORLD_FACT],
                "required_authority_domains": ["world_authority"],
                "policy_version": lr5.POLICY_WORLD_FACT_V1,
            },
            result_binding_spec={
                "target_refs": targets,
                "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "WORLD_CANONICAL_OBSERVATION"],
                "authority_domains": ["result_settlement_authority", "world_authority"],
                "policy_version": lr5.POLICY_POSITION_V1,
            },
        )
        return spec.to_dict()

    def _stay_adoption_spec(self, targets: list[str], origin: str, chosen_target: str,
                            lr5: Any) -> dict[str, Any]:
        """The Activity shape for a bounded, non-productive stay she really chose.

        It reuses LR-5's existing vocabulary only: ``WORLD_ACTION_BOUNDED`` progress with
        ``LATEST_POSITION`` checkpoints, and a ``WORLD_FACT_MATCH`` completion criterion
        whose expected value names *the place and the declared duration*
        (``world_fact:stay:<place>:<descriptor>:<seconds>``).  That value is submitted by
        ``_record_world_stay_progress`` and only after two facts hold in the same round:
        the World owner's own fresh observation still puts her in that place, and the cycle's
        declared end time has passed.  A stay therefore cannot be completed by a NO_ACTION
        round, by elapsed time alone, or by a place she is not in.
        """
        plan = stay_plan(chosen_target)
        if plan is None:
            raise AutonomousLifeError(
                "the committed Decision chose %r, which is not a whole stay plan"
                % (chosen_target,))
        expected = "%s%s" % (WORLD_STAY_FACT_PREFIX, chosen_target[len(STAY_REF_PREFIX):])
        spec = ag2.ActivityAdoptionSpec(
            activity_kind=STAY_ACTIVITY_KIND,
            title="在一个地方待一会儿",
            target_refs=targets,
            origin_ref=origin,
            progress_contract_spec={
                "progress_kind": "WORLD_ACTION_BOUNDED",
                "checkpoint_semantics": lr5.CHECKPOINT_SEMANTICS_LATEST_POSITION,
                "target_refs": targets,
                "policy_version": lr5.POLICY_POSITION_V1,
            },
            completion_contract_spec={
                "criteria_kind": lr5.CRITERIA_WORLD_FACT_MATCH,
                "target_refs": targets,
                "criteria": {"fact_key": WORLD_STAY_FACT_KEY, "expected_value": expected},
                "required_evidence_kinds": [lr5.SOURCE_KIND_WORLD_FACT],
                "required_authority_domains": ["world_authority"],
                "policy_version": lr5.POLICY_WORLD_FACT_V1,
            },
            result_binding_spec={
                "target_refs": targets,
                "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "WORLD_CANONICAL_OBSERVATION"],
                "authority_domains": ["result_settlement_authority", "world_authority"],
                "policy_version": lr5.POLICY_POSITION_V1,
            },
        )
        return spec.to_dict()

    # -- routing the step by the committed Decision (W05R) -------------------------

    def _selected_candidate(self, decision_record: Any) -> Optional[dict[str, Any]]:
        """The candidate record the Decision actually selected, or ``None``."""

        ref = getattr(decision_record, "selected_candidate_ref", None)
        if not ref:
            return None
        try:
            candidate = self.runtime.candidate_read.get_candidate(str(ref))
        except Exception as exc:
            self.last_world_error = f"{type(exc).__name__}: {exc}"
            return None
        return dict(candidate) if isinstance(candidate, Mapping) else None

    @staticmethod
    def _candidate_is_world(candidate: Optional[Mapping[str, Any]]) -> bool:
        """A World candidate is one whose target is a place, an object or a stay she observed.

        The discriminator is the committed candidate record itself -- the same ``place:`` /
        ``object:`` / ``stay:`` namespaces ``_world_step_spec`` reads its target from -- and
        not a cached action space or a re-derived guess.  A World opportunity whose target is
        none of them (there is none today) is not routed to the World owner.
        """

        if not isinstance(candidate, Mapping):
            return False
        return any(str(ref).startswith(PLACE_REF_PREFIX)
                   or str(ref).startswith(WORLD_OBJECT_REF_PREFIX)
                   or str(ref).startswith(STAY_REF_PREFIX)
                   for ref in (candidate.get("target_refs") or []))

    def _decision_chose_world(self, decision_record: Any) -> bool:
        return self._candidate_is_world(self._selected_candidate(decision_record))

    def _routed_step_builder(self, point: Optional[ArtifactResumePoint], *, observed_at: str
                             ) -> Callable[[Any], Mapping[str, Any]]:
        """One step, whose owner is chosen by the committed Decision."""

        def build(decision_record: Any) -> Mapping[str, Any]:
            if self._decision_chose_world(decision_record):
                return self._world_step_spec(decision_record)
            if self._decision_chose_reading(decision_record):
                return self._reading_step_spec(observed_at=observed_at)
            if point is None:
                raise AutonomousLifeError(
                    "the committed Decision chose neither an observed place, the reading "
                    "material, nor a continuable work")
            return self._ar0_step_spec(point, observed_at=observed_at)

        return build

    def _world_step_spec(self, decision_record: Any) -> dict[str, Any]:
        """One bounded World action, described from the Decision that actually chose it.

        The target is read back from the committed Decision's own candidate record -- not
        re-derived from the model, and not from a fresh guess.  A Decision that names no
        observed target is a refusal, not a default.  The target's own namespace decides
        what happens: ``place:`` is a timed MOVE, ``object:`` is the instant CONSUME, and
        ``stay:`` is an internal Life checkpoint (see ``_stay_step_spec``) -- none of them
        is ever treated as another's evidence.
        """
        candidate_ref = getattr(decision_record, "selected_candidate_ref", None)
        candidate: Optional[dict[str, Any]] = None
        if candidate_ref:
            try:
                candidate = self.runtime.candidate_read.get_candidate(str(candidate_ref))
            except Exception as exc:
                self.last_world_error = f"{type(exc).__name__}: {exc}"
        target_ref = ""
        if candidate:
            for ref in candidate.get("target_refs") or []:
                text = str(ref)
                if (text.startswith(PLACE_REF_PREFIX)
                        or text.startswith(WORLD_OBJECT_REF_PREFIX)
                        or text.startswith(STAY_REF_PREFIX)):
                    target_ref = text
                    break
        if not target_ref:
            raise AutonomousLifeError(
                "the committed Decision %r names no observed World target" % (candidate_ref,))

        observation = self.last_world_observation or {}
        place = str((observation.get("location") or {}).get("place_id") or "unknown")
        revision = observation.get("world_revision")
        if target_ref.startswith(WORLD_OBJECT_REF_PREFIX):
            object_id = target_ref[len(WORLD_OBJECT_REF_PREFIX):]
            return {
                "step_kind": ag2.STEP_KIND_WORLD_ACTION,
                "action_kind": ACTION_KIND_WORLD,
                "target_ref": target_ref,
                "payload_spec": {
                    "verb": CONSUME_VERB,
                    "target": object_id,
                    # AR-0-side bookkeeping only: the World's own dependency evidence is
                    # produced by the Intent Executor's fresh read, never by this payload.
                    "expected_dependencies": {},
                },
                "expected_effect_predicates": ["world_object_consumed"],
                "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
                # One intended consumption, one logical action: the key names the place she
                # was in, the World revision the observation came from and the instance, so a
                # retry reuses the same execution instead of eating two.
                "idempotency_key": "idem_step:v02:world:consume:%s:r%s:%s"
                                   % (place, revision, object_id),
            }
        if target_ref.startswith(STAY_REF_PREFIX):
            # A stay is Life's own bounded checkpoint step: it submits no World verb, so it
            # must not be dressed up as one.  The plan travels in the target.
            return self._stay_step_spec(target_ref, phase=STAY_PHASE_START, observed_at=None)

        destination = target_ref[len(PLACE_REF_PREFIX):]
        return {
            "step_kind": ag2.STEP_KIND_WORLD_ACTION,
            "action_kind": ACTION_KIND_WORLD,
            "target_ref": "place:%s" % destination,
            "payload_spec": {
                "verb": MOVE_VERB,
                "target": destination,
                # AR-0-side bookkeeping only: the World's own dependency evidence is
                # produced by the Intent Executor's fresh read, never by this payload.
                "expected_dependencies": {},
            },
            "expected_effect_predicates": ["world_location_confirmed"],
            "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
            # One intended move, one logical action: the key encodes where she was, the World
            # revision the observation came from, and where she is going -- so a retry reuses
            # the same execution instead of moving twice.
            "idempotency_key": "idem_step:v02:world:%s:r%s:%s" % (place, revision, destination),
        }

    def _stay_step_spec(self, target_ref: str, *, phase: str,
                        observed_at: Optional[str]) -> dict[str, Any]:
        """One narrow, structured, *internal* step for a bounded stay.

        ``STEP_KIND_INTERNAL_CHECKPOINT`` with ``action_kind=None`` -- AG-2's own vocabulary
        for a Life step that crosses no submission fence.  AG-2 records the step and proposes
        no Action, which is exactly right: nothing in the World changes when she stands
        somewhere, so there is nothing to fence, authorize or receipt.  The stay's facts are
        recorded by LR-5 from the World owner's own observations instead.
        """

        plan = stay_plan(target_ref)
        if plan is None:
            raise AutonomousLifeError(
                "the committed Decision chose %r, which is not a whole stay plan"
                % (target_ref,))
        payload = {
            "stay_phase": phase,
            "stay_place": plan["place"],
            "stay_affordance": plan["descriptor"],
            "stay_planned_seconds": plan["seconds"],
            "stay_target_ref": target_ref,
        }
        if observed_at:
            payload["stay_at"] = observed_at
        key = "idem_step:v02:world:stay:%s:%s:%s:%s" % (
            phase.lower(), plan["place"], plan["descriptor"], observed_at or "start")
        return {
            "step_kind": ag2.STEP_KIND_INTERNAL_CHECKPOINT,
            # No owner executes a stay, so there is no Action: AG-2 records the step and
            # returns without proposing one (life_integration_ag2.create_step_and_propose_action).
            "action_kind": None,
            "target_ref": target_ref,
            "payload_spec": payload,
            "expected_effect_predicates": [],
            "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
            "idempotency_key": key,
        }

    def _foreground_stay_activity(self) -> Optional[dict[str, Any]]:
        """The foreground Activity when -- and only when -- it is a bounded stay.

        Read from the Activity owner itself, so a stay's continuation cannot be inferred
        from this module's own bookkeeping.
        """

        try:
            view = dict(self.runtime.activity_read.get_current_life_view() or {})
        except Exception as exc:  # a read failure is reported, never guessed
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return None
        if str(view.get("foreground_activity_kind") or "") != STAY_ACTIVITY_KIND:
            return None
        ref = view.get("foreground_activity_ref")
        if not ref:
            return None
        try:
            record = self.runtime.activity_read.get_activity(str(ref))
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return None
        if not isinstance(record, Mapping):
            return None
        if str(record.get("status") or "") != "ACTIVE":
            return None
        return dict(record)

    @staticmethod
    def _stay_plan_from_activity(activity: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Rebuild the stay plan from the Activity owner's own record.

        The adopted stay target was registered as one of the Activity's ``subject_refs``
        (and the checkpoint it produced carries the same spelling), so the plan is read
        back from the owner instead of from a second store in this module.
        """

        candidates = list(activity.get("subject_refs") or [])
        if activity.get("checkpoint_ref"):
            candidates.append(activity["checkpoint_ref"])
        for ref in candidates:
            text = str(ref)
            if text.startswith(WORLD_STAY_FACT_PREFIX):
                text = text[len("world_fact:"):]
            plan = stay_plan(text)
            if plan is not None:
                return plan
        return None

    def _close_stay_by_choice(self, base: dict[str, Any], obs: str, *,
                              activity: Mapping[str, Any], decision: str,
                              adoption_status: str, rejection: str = "") -> dict[str, Any]:
        """Report a stay the Decision ended *early* -- never one that served its time.

        Three facts this method will not invent:

        * **that it really ended** -- the Activity is re-read from the Activity owner itself;
          while it is still ``ACTIVE`` nothing ended, so the exit is reported as refused
          instead of being announced;
        * **how long it really lasted** -- the same arithmetic the completion path uses (the
          first checkpoint LR-5 recorded, to this round's explicit ``observed_at``), with the
          remaining seconds reported as *remaining* rather than quietly written off;
        * **what it did not become** -- no ``WORLD_STAY_FACT_KEY`` completion value is ever
          submitted here, so an early exit can never satisfy the Activity's declared-duration
          criterion, and the record carries ``completed: false`` in as many words.

        A stay left early is not a stay that can be picked up later: the elapsed time would
        include everything that happened while she was elsewhere, which is exactly the
        "being away is not being there" rule.  A paused stay is therefore reported as paused
        and this cycle never resumes it as a stay.
        """

        ref = str(activity.get("activity_ref") or activity.get("activity_id") or "")
        plan = self._stay_plan_from_activity(activity) or {}
        started_at, checkpoint_ref = self._stay_began_at(ref)
        elapsed = _seconds_between(started_at, obs) if started_at else None
        owner_record: dict[str, Any] = {}
        if ref:
            try:
                record = self.runtime.activity_read.get_activity(ref)
            except Exception as exc:  # a failed read is reported, never guessed
                base["stay_owner_read_error"] = "%s: %s" % (type(exc).__name__, exc)
                record = None
            if isinstance(record, Mapping):
                owner_record = dict(record)
        owner_status = str(owner_record.get("status") or "")
        planned = int(plan.get("seconds") or 0)
        remaining = (max(0, planned - int(elapsed))
                     if (elapsed is not None and planned) else None)
        stay: dict[str, Any] = {
            "activity_ref": ref,
            "target_ref": plan.get("target_ref"),
            "place": plan.get("place"),
            "affordance": plan.get("descriptor"),
            "planned_seconds": planned or None,
            "started_at": started_at,
            "checkpoint_ref": checkpoint_ref,
            "observed_at": obs,
            "observed_place": (self.last_world_observation or {}).get("location", {}).get("place_id")
                              if isinstance(self.last_world_observation, Mapping) else None,
            "elapsed_seconds": elapsed,
            "remaining_seconds": remaining,
            # The declared stretch was *not* served: this is a fact, not a phase label.
            "completed": False,
            "completion_claimed": False,
            "exit_decision": decision,
            "exit_kind": (STAY_EXIT_PAUSE if decision == ag1.DECISION_PAUSE
                          else STAY_EXIT_ABANDON),
            # Being away is not being there: a stay left early is not resumed as a stay.
            "resumable_as_stay": False,
            "owner_status": owner_status or None,
            "owner_ended_at": owner_record.get("ended_at"),
            "owner_terminal_reason_code": owner_record.get("terminal_reason_code"),
            "owner_last_transition_ref": owner_record.get("last_transition_ref"),
        }
        stay["foreground_released"] = self._foreground_activity_ref() != ref
        base["stay"] = stay
        base["step_path"] = "STAY_ENDED_BY_CHOICE"

        if adoption_status.startswith("REJECTED"):
            # An illegal or refused exit changes nothing; say that instead of "she left".
            stay["exit_refused"] = rejection or adoption_status
            stay.pop("exit_kind", None)
            return self._finish(base, OUTCOME_ADOPTION_REJECTED, llm_calls=1,
                                detail="the owner refused the stay exit: %s"
                                       % (rejection or adoption_status))
        if owner_status in {"", "ACTIVE"}:
            stay["exit_refused"] = "OWNER_DID_NOT_APPLY_THE_EXIT"
            stay.pop("exit_kind", None)
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="the Activity owner still holds this stay ACTIVE; nothing ended and "
                       "the foreground is unchanged")
        stay["phase"] = (STAY_PHASE_PAUSED if owner_status == "PAUSED"
                         else STAY_PHASE_ENDED_EARLY)
        duration = ("%ds" % elapsed) if elapsed is not None else "an unmeasurable stretch"
        if owner_status == "PAUSED":
            return self._finish(
                base, OUTCOME_PAUSED, llm_calls=1,
                detail="left %s early after %s of %ds and paused it; it is not resumed as a "
                       "stay" % (plan.get("place"), duration, planned))
        return self._finish(
            base, OUTCOME_ABANDONED, llm_calls=1,
            detail="left %s early after %s of %ds (%s); the declared stretch was not served "
                   "and no completion is claimed"
                   % (plan.get("place"), duration, planned,
                      stay.get("owner_terminal_reason_code") or owner_status))

    def _place_availability(self, place: Any) -> Optional[str]:
        """The World owner's own opening verdict for one place, or ``None`` when unread.

        Source: the catalog the *same* observation seam read in this same round (the
        provider's own ``last_catalog``, refreshed by ``_observe_position``).  A verdict is
        only ever reported as the owner spelled it; an unreadable catalog is ``None`` --
        never silently upgraded to ``OPEN`` and never downgraded to ``CLOSED``.
        """

        name = str(place or "")
        if not name:
            return None
        catalog = self.last_world_catalog
        if not isinstance(catalog, Mapping) or str(catalog.get("status") or "") != "available":
            return None
        for item in catalog.get("opportunities") or []:
            if not isinstance(item, Mapping) or str(item.get("location_id")) != name:
                continue
            status = (item.get("availability") or {}).get("status")
            return str(status) if status else None
        return None
    def pause_gate(self) -> dict[str, Any]:
        """The life-side persistent pause bit (W05), fail closed and read-only.

        This is *not* the World's pause (``world_control.json``, W04).  That one stops new
        travel segments at the engine's submission boundary; this one stops new decision
        windows, which is what keeps a paused instance from starting any new external action.
        """
        return life_pause.gate(self.pause_state_root)

    # -- W05R-R1: the confirmation boundary around each external effect -----------------

    def _pause_hold(self) -> dict[str, Any]:
        """Re-read the pause bit under the shared confirmation lock, fail closed.

        This is the only way this cycle asks "is the operator holding her?" at a point
        where the answer has to be current: a boundary that cannot take the lock reports
        ``blocked`` rather than guessing.  It is taken and released immediately -- the
        lock is never carried across a model or content request.
        """

        with life_pause.commit_boundary(self.pause_state_root) as hold:
            return dict(hold)

    def _pause_confirmed(self, stage: str) -> None:
        """Raise :class:`life_pause.PauseConfirmedError` when the bit is set."""

        hold = self._pause_hold()
        if hold.get("blocked"):
            raise life_pause.PauseConfirmedError(
                str(hold.get("reason_code") or life_pause.REASON_PAUSED), stage=stage)

    def _admit_request(self, kind: str) -> None:
        """W05R-R1b: the request-start handshake for one real request attempt.

        This is the admission a request must pass immediately before it is sent (the
        decision, every schema retry, and the content request all call it).  It takes the
        shared confirmation lock, re-reads the bit while holding it and releases the lock
        before returning -- so a confirmation that returned at any earlier point cannot be
        overtaken by a request that starts after it, while the network *response* wait is
        never under a pause/shared/control lock.
        """

        life_pause.admit_request(self.pause_state_root,
                                 stage="before_%s_request" % str(kind or "request"))

    def _pause_gate_hook(self, decision_record: Any = None) -> None:
        """AG-2 ``pre_adoption_mutation_hook``: the boundary after the Decision commits.

        The decision request was already started, so it is allowed to finish; its result
        may not be adopted, may not produce a content request and may not reach an owner.
        The round has no "cancelled adoption" status, so the abort is a raise.
        """

        self._pause_confirmed("after_decision_before_adoption")
        return None

    def _pause_fault_hook(self, name: str) -> None:
        """AG-2/AG-1/AR-0 ``fault_hook``: the boundaries between the requests and the owner.

        Hook points used, in the order they fire:

        * ``before_cognition`` / ``during_cognition`` -- AG-1's per-attempt boundary,
          immediately before ``cognition_adapter.invoke`` on **every** attempt including
          schema retries.  The request-start handshake runs here, so a pause confirmed
          after any earlier check still stops the decision request from starting;
        * ``after_step_spec_before_action_propose`` -- the content request has returned
          (materialising the lazy step spec is what performs it) and nothing has been
          proposed yet, so a confirmation that arrived while the content request was in
          flight stops the round here;
        * ``before_adapter_call`` -- immediately before AR-0 invokes the owner adapter,
          after its own durable submission fence.  The shared lock is taken here and held
          across the owner call, so a confirmation can neither be overtaken by this
          submission nor be blocked by anything but the submission itself;
        * ``after_adapter_call`` -- the owner call returned, so the lock is released.

        Unknown hook names are ignored: this is a policy hook riding the existing
        fault-injection seam, not a second control path.
        """

        if name in {"before_cognition", "during_cognition"}:
            self._admit_request("decision")
            return
        if name == "after_step_spec_before_action_propose":
            self._pause_confirmed("after_content_before_propose")
            return
        if name == "before_adapter_call":
            if not self._pause_lease.acquire():
                raise life_pause.PauseConfirmedError(
                    life_pause.REASON_PAUSE_CONTENDED, stage="before_owner_submit")
            view = life_pause.gate(self.pause_state_root)
            if view.get("blocked"):
                raise life_pause.PauseConfirmedError(
                    str(view.get("reason_code") or life_pause.REASON_PAUSED),
                    stage="before_owner_submit")
            return
        if name == "after_adapter_call":
            self._pause_lease.release()

    def _pause_abort(self, base: dict[str, Any], exc: life_pause.PauseConfirmedError,
                     *, llm_calls: int) -> dict[str, Any]:
        """Report a round the operator's pause stopped, as exactly that."""

        base["pause_boundary"] = {"held_after": exc.stage, "reason_code": exc.reason_code,
                                  "released_lease": not self._pause_lease.held}
        return self._finish(base, OUTCOME_PAUSED_BY_OPERATOR, llm_calls=llm_calls,
                            detail="life was held %s: %s" % (exc.stage, exc.reason_code))

    def _foreground_activity_ref(self) -> Optional[str]:
        """The activity the owner currently holds as foreground, or None."""
        try:
            state = dict(self.runtime.canonical_state() or {})
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return None
        ref = state.get("foreground_activity_ref")
        return str(ref) if ref else None

    def _observe_position(self) -> tuple[Optional[str], Any]:
        """Read where the World says she is, through the same observation seam.

        Only the World's own answer counts as evidence of a position: the journey's summary is
        the journey's own bookkeeping, and a destination is never inferred from the request.
        """
        provider = self.world_opportunity_provider
        if provider is None:
            return (None, None)
        try:
            provider()
        except Exception as exc:
            self.last_world_error = "%s: %s" % (type(exc).__name__, exc)
            return (None, None)
        seen = getattr(provider, "last_observation", None)
        if not isinstance(seen, Mapping):
            return (None, None)
        self.last_world_observation = dict(seen)
        # I-r3: the same observation also re-read the place facts, so the opening verdict a
        # stay is judged on comes from *this* observation -- one seam, one instant, no second
        # catalog read and no second clock.
        catalog = getattr(provider, "last_catalog", None)
        if isinstance(catalog, Mapping):
            self.last_world_catalog = dict(catalog)
        place = (seen.get("location") or {}).get("place_id")
        return (str(place) if place else None, seen.get("world_revision"))

    def settle_world_journey(self, *, observed_at: Optional[str] = None,
                             settle_only: Optional[bool] = None) -> dict[str, Any]:
        """Let real time settle the W04 journey, and only then record an arrival.

        This is the other half of ``OUTCOME_IN_TRANSIT``.  ``world_journey.advance_journey``
        realizes the travel record the World owner already wrote, confirms the segment, and --
        unless paused -- starts the *next* segment of the same single target.  One target, one
        segment at a time, every segment timed: the autonomous loop cannot cross several places
        in one round, and it cannot claim a place it has not reached.

        An arrival is recorded only when the World's own observation puts her at the
        destination.  A journey still in transit therefore can never be turned into a completed
        activity, and a journey that completed but whose observed position disagrees is reported
        as refused rather than silently accepted.

        While the life-side pause is set, ``settle_only`` defaults to True: an already departed
        segment still arrives (a pause does not freeze physics) but no new segment starts.
        """
        home = self.world_home
        if not home:
            return {"ok": False, "reason_code": "WORLD_HOME_NOT_CONFIGURED"}
        if settle_only is None:
            settle_only = bool(self.pause_gate()["blocked"])
        # W05R: starting the *next* segment is a commit, so it happens inside the pause
        # boundary too.  A pause confirmed after the read above therefore turns this
        # advance into a settle-only pass: the already-departed segment still arrives,
        # and no new one is submitted.
        with life_pause.commit_boundary(self.pause_state_root) as hold:
            if hold["blocked"]:
                settle_only = True
            try:
                journey = journey_module()
                projection = journey.advance_journey(
                    hermes_home=home, environment=self.env, now=observed_at,
                    settle_only=bool(settle_only))
            except Exception as exc:
                self.last_world_error = "%s: %s" % (type(exc).__name__, exc)
                return {"ok": False, "reason_code": "JOURNEY_UNAVAILABLE",
                        "detail": self.last_world_error}
        record: dict[str, Any] = {
            "ok": True,
            "journey_id": projection.get("journey_id"),
            "journey_status": projection.get("status"),
            "reason_code": projection.get("reason_code"),
            "location_id": projection.get("location_id"),
            "target_location_id": projection.get("target_location_id"),
            "current_segment": projection.get("current_segment"),
            "segments_confirmed": projection.get("segments_confirmed"),
            # I-r2: the segment's own declared timing, so a caller that has to reconcile can
            # settle the same departure instead of guessing when it was due.
            "segment_status": projection.get("segment_status"),
            "expected_arrival_at": projection.get("expected_arrival_at"),
            "settle_only": bool(settle_only),
            "arrived": False,
        }
        self.last_journey_settlement = record
        target = str(projection.get("target_location_id") or "")
        if str(projection.get("status") or "") != "completed" or not target:
            return record
        place, _revision = self._observe_position()
        record["observed_location_id"] = place
        if place != target:
            record["arrival_refused"] = "WORLD_POSITION_DOES_NOT_MATCH_TARGET"
            return record
        record["arrived"] = True
        activity_ref = self._foreground_activity_ref()
        record["activity_ref"] = activity_ref
        if not activity_ref:
            # The journey really completed, but no activity claims that destination, so no
            # progress is invented for one.
            return record
        base: dict[str, Any] = {"trigger_kind": TRIGGER_IDLE_WINDOW,
                                "trigger_ref": "evt:journey:arrival",
                                "observed_at": observed_at or _utc_now_iso()}
        self._record_world_progress(
            base, activity_ref=activity_ref, destination="place:%s" % target,
            action_ref=None, correlation_id=None)
        record["progress_state"] = base.get("progress_state")
        record["checkpoint_ref"] = base.get("checkpoint_ref")
        record["progress_ingest_error"] = base.get("progress_ingest_error")
        return record

    def reconcile_unknown_step(self, *, action_id: Any, occurred_at: Optional[str] = None,
                               reason: str = "") -> dict[str, Any]:
        """Close the loop on a World step the owner answered ``UNKNOWN`` -- or say why not.

        The order is the owner's own, and none of it is a retry:

          1. the journey ledger is *read back* against the World owner's real travel record
             (``advance_journey`` reconciles an unconfirmed segment from that record; it runs
             ``settle_only`` here, so it can never start a new segment or a new departure);
          2. AR-0 reconciles the **same** action id -- same idempotency key, same execution
             id, same submission fence -- through its own World-ledger lookup
             (``reconcile_action``), which is the deterministic one the ledger already owns;
          3. a resolved status is settled into the **original** Activity through AG-2's own
             ``settle_and_propagate_action_result``, so LR-5 and the completion contract see
             exactly what a synchronous answer would have handed them.

        Nothing here upgrades an absence: a still-unknown answer is reported as such, claims
        no success and settles nothing.  No ``START``, no second submission and no new
        Activity is created anywhere in this method -- a caller that wants to try again is
        asking for a *decision*, not for a replay of the unknown one.
        """

        observed = str(occurred_at or _utc_now_iso())
        record: dict[str, Any] = {"ok": False, "action_id": str(action_id or ""),
                                  "reason": reason or "WORLD_STEP_UNKNOWN",
                                  "action_status": None, "reconciled": False,
                                  "observed_at": observed}
        if not action_id:
            record["reason_code"] = "NO_ACTION_TO_RECONCILE"
            return record
        # 1. owner evidence first: the travel record, never the journey's own bookkeeping.
        settlement = self.settle_world_journey(observed_at=observed, settle_only=True)
        record["journey"] = {key: settlement.get(key) for key in (
            "ok", "journey_status", "reason_code", "location_id", "target_location_id",
            "segment_status", "expected_arrival_at", "arrived", "settle_only")}
        # 2. the same action id, reconciled by the World ledger lookup.
        try:
            resolved = self.runtime.action_command.reconcile_action(
                action_id=str(action_id), occurred_at=observed)
        except Exception as exc:
            record["reason_code"] = "RECONCILE_REFUSED"
            record["error"] = "%s: %s" % (type(exc).__name__, exc)
            return record
        action = dict(resolved.get("action") or {})
        reconciliation = dict(resolved.get("reconciliation") or {})
        status = str(action.get("status") or "")
        record["action_status"] = status
        record["reconciliation"] = {key: reconciliation.get(key) for key in (
            "reconciliation_id", "strategy", "result", "previous_status",
            "resolved_status", "evidence_refs", "world_query_ref")}
        record["reconciled"] = status in {"SUCCEEDED", "FAILED"}
        if not record["reconciled"]:
            # STILL_UNKNOWN (or a conflict): the owner has not said what happened, so nothing
            # is claimed, nothing is settled and no new attempt is started.
            record["reason_code"] = "STILL_UNKNOWN"
            return record
        # 3. settle the *original* Activity from the resolved status.
        try:
            settled = dict(self.runtime.coordinator.settle_and_propagate_action_result(
                action_id=str(action_id), occurred_at=observed) or {})
        except Exception as exc:
            record["settlement_error"] = "%s: %s" % (type(exc).__name__, exc)
            record["ok"] = status == "SUCCEEDED"
            return record
        settlement_row = dict((settled.get("settlement_result") or {}).get("settlement") or {})
        progress = settled.get("progress_state")
        handoff = settled.get("handoff_result")
        record["settlement"] = {
            "settlement_id": settlement_row.get("settlement_id"),
            "settlement_status": settlement_row.get("settlement_status"),
            "outcome_kind": settlement_row.get("outcome_kind"),
            "progress_state": (progress.get("progress_state")
                               if isinstance(progress, Mapping) else progress),
            "activity_status": getattr(settled.get("activity"), "status", None),
            "handoff_event_kind": (handoff or {}).get("event_kind")
            if isinstance(handoff, Mapping) else None,
        }
        record["ok"] = status == "SUCCEEDED"
        record["reason_code"] = ("RECONCILED_SUCCEEDED" if status == "SUCCEEDED"
                                 else "RECONCILED_FAILED")
        return record

    def _classify_world_step(self, out: Mapping[str, Any], base: dict[str, Any],
                             obs: str) -> dict[str, Any]:
        """Report the World result as exactly what the World owner confirmed.

        A stay never reaches the AR-0 half of this path (it submits no Action), so the first
        thing checked is whether the executed step is Life's own internal checkpoint step.
        """
        step_res = out.get("step_execution_result") or {}
        bridge = step_res.get("bridge_result") or {}
        step_spec = bridge.get("step_spec")
        if (step_spec is not None
                and str(getattr(step_spec, "step_kind", ""))
                == ag2.STEP_KIND_INTERNAL_CHECKPOINT):
            return self._classify_stay_step(step_spec, base, obs)
        submit = step_res.get("submit_result") or {}
        action = submit.get("action") or {}
        status = str(action.get("status") or "")
        verb = str((action.get("frozen_payload") or {}).get("verb") or MOVE_VERB)
        receipts: list[dict[str, Any]] = []
        if action.get("action_id"):
            try:
                receipts = self.runtime.action_read.list_receipts(str(action["action_id"]))
            except Exception:  # pragma: no cover - read failure is reported, not invented
                receipts = []
        receipt = receipts[-1] if receipts else None
        details = dict((receipt or {}).get("details") or {})
        evidence = list((receipt or {}).get("evidence_refs") or [])

        base.update({
            "action_kind": ACTION_KIND_WORLD,
            "action_id": action.get("action_id"),
            "action_status": status,
            "world_verb": verb,
            "provider_operation_ref": action.get("provider_operation_ref"),
            "world_evidence_refs": evidence,
            "world_revision": details.get("world_revision"),
            "world_mutation_ref": evidence[-1] if evidence else None,
        })

        if status == "SUCCEEDED" and verb == CONSUME_VERB:
            return self._classify_world_consume(base, details)

        if status == "SUCCEEDED":
            arrived = details.get("arrived")
            if arrived is False:
                # W05: the owner confirmed a *departure*, not an arrival.  A timed segment is
                # still in transit, so no destination observation may be handed to LR-5 and this
                # round is not ACTED.  She set off; she is not there.  The arrival is settled by
                # real time in ``settle_world_journey``, against the World's own observation.
                base["journey"] = {key: details.get(key) for key in (
                    "journey_id", "journey_status", "origin_location_id",
                    "destination_location_id", "location_id", "current_segment",
                    "segment_status", "expected_arrival_at", "remaining_seconds",
                    "segments_confirmed", "route", "arrived")}
                base["location_id"] = details.get("location_id")
                return self._finish(
                    base, OUTCOME_IN_TRANSIT, llm_calls=1,
                    detail="departed %s toward %s; the arrival is settled by real time, "
                           "not in this round"
                           % (details.get("location_id"),
                              details.get("destination_location_id")))
            self._record_world_progress(
                base, activity_ref=base.get("activity_ref"),
                destination=(action.get("frozen_payload") or {}).get("target"),
                action_ref=action.get("action_id"),
                correlation_id=base.get("correlation_id"))
            return self._finish(base, OUTCOME_ACTED, llm_calls=1,
                                detail="world owner applied %s" % (evidence or "the move"))
        if status == "UNKNOWN":
            return self._finish(
                base, OUTCOME_STEP_UNKNOWN, llm_calls=1,
                detail="world action result unknown; reconcile the original execution "
                       "before any retry")
        if status == "FAILED":
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="world owner refused: %s %s" % (details.get("code") or status,
                                                       details.get("detail") or ""))
        return self._finish(base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                            detail="world action status %r did not settle in this round" % status)

    def _classify_world_consume(self, base: dict[str, Any],
                                details: Mapping[str, Any]) -> dict[str, Any]:
        """A consumed instance is not an arrival: report the owner's own receipt.

        The World owner deletes the placement and writes the consumption receipt in the
        *same* atomic save, so the receipt -- not "the object is gone" -- is what says which
        instance this execution really used up.  A missing or mismatched receipt is reported
        as such and produces no LR-5 progress and no completion claim.
        """

        receipt = details.get("consumption_receipt")
        present = bool(details.get("receipt_present")) and isinstance(receipt, Mapping)
        base["consumption_receipt"] = dict(receipt) if isinstance(receipt, Mapping) else None
        base["consumption_receipt_present"] = present
        if not present:
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="the World owner committed a consumption without a matching receipt; "
                       "nothing is attributed to this execution")
        object_id = str(receipt.get("object_id") or "")
        base["consumed_object_id"] = object_id
        base["consumption_execution_id"] = receipt.get("execution_id")
        base["consumption_receipt_ref"] = receipt.get("receipt_id")
        self._record_world_consume_progress(
            base, activity_ref=base.get("activity_ref"), object_id=object_id,
            action_ref=base.get("action_id"), correlation_id=base.get("correlation_id"))
        return self._finish(
            base, OUTCOME_ACTED, llm_calls=1,
            detail="world owner consumed %s (receipt %s)"
                   % (object_id, receipt.get("receipt_id")))


    def _stay_began_at(self, activity_ref: Any) -> tuple[Optional[str], Optional[str]]:
        """When LR-5 recorded this stay's first checkpoint, and that checkpoint's ref.

        The start is read from the Activity owner's own progress journal -- the moment the
        start evidence really carried -- never from this module's memory and never from
        "the round before".
        """

        if not activity_ref:
            return (None, None)
        reader = getattr(self.runtime, "progress_read", None)
        if reader is None:
            return (None, None)
        try:
            updates = list(reader.list_checkpoint_updates(str(activity_ref)) or [])
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return (None, None)
        if not updates:
            return (None, None)
        first = updates[0]
        return (str(first.get("occurred_at") or "") or None,
                str(first.get("new_checkpoint_ref") or "") or None)

    def _classify_stay_step(self, step_spec: Any, base: dict[str, Any],
                            obs: str) -> dict[str, Any]:
        """A bounded stay: record where she really is, and end only when the time is real.

        The three facts this method will not invent:

        * **where she is** -- a fresh read through the World gateway in this same round; if
          the owner puts her somewhere else, nothing is recorded and the round is refused;
        * **when it began** -- the first checkpoint LR-5 recorded for this Activity;
        * **how long it has lasted** -- the cycle's explicit ``observed_at`` minus that start.

        Nothing here advances a body value, wakes an offline experience, or claims pleasure:
        a stay is a reported stretch of time in a place, and the only consequence it has is
        its own checkpoint / completion.
        """

        payload = dict(getattr(step_spec, "payload_spec", {}) or {})
        activity_ref = getattr(step_spec, "activity_ref", None) or base.get("activity_ref")
        target_ref = str(payload.get("stay_target_ref") or getattr(step_spec, "target_ref", ""))
        plan = stay_plan(target_ref)
        phase = str(payload.get("stay_phase") or STAY_PHASE_START)
        if plan is None:
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="the stay step names no whole stay plan")

        place_now, revision = self._observe_position()
        stay = {
            "target_ref": plan["target_ref"],
            "place": plan["place"],
            "affordance": plan["descriptor"],
            "planned_seconds": plan["seconds"],
            "phase": phase,
            "observed_place": place_now,
            "observed_revision": revision,
            "observed_at": obs,
        }
        base["stay"] = stay
        base["action_kind"] = "STAY_CHECKPOINT"
        if place_now is None:
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="no World observation of where she is; a stay cannot be recorded")
        if place_now != plan["place"]:
            stay["refused"] = "WORLD_POSITION_DOES_NOT_MATCH_STAY"
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="the World owner puts her in %r, not %r; nothing is recorded"
                       % (place_now, plan["place"]))

        # I-r3: a stay is measured from its *first* real checkpoint.  A round that would begin
        # it a second time (a resumed stay, a replayed adoption) would silently re-baseline the
        # clock and let the declared stretch be served twice, so it is refused instead.
        began_at, began_checkpoint = self._stay_began_at(activity_ref)
        if phase == STAY_PHASE_START and began_at:
            stay["started_at"] = began_at
            stay["checkpoint_ref"] = began_checkpoint
            stay["refused"] = "STAY_ALREADY_STARTED"
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="this stay already began at %s; starting it again would reset its "
                       "declared stretch" % began_at)
        # I-r3: the place's own opening verdict, from the same fresh observation.  A stay whose
        # place the World now calls CLOSED records nothing: sitting in a shut shop is not
        # something the owner says is happening, so neither a checkpoint nor a completion may
        # be claimed for it -- leaving is a Decision, not a rewrite of this one.
        availability = self._place_availability(plan["place"])
        stay["place_availability"] = availability
        if availability == "CLOSED":
            stay["refused"] = "WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT"
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="the World owner says %r is CLOSED now; no stay checkpoint and no "
                       "completion is recorded for it" % plan["place"])
        if phase == STAY_PHASE_START:
            stay["started_at"] = obs
            stay["elapsed_seconds"] = 0
            self._record_world_stay_progress(
                base, activity_ref=activity_ref, plan=plan,
                started_at=obs, elapsed_seconds=0, complete=False,
                correlation_id=base.get("correlation_id"))
            return self._finish(
                base, OUTCOME_ACTED, llm_calls=1,
                detail="staying at %s (%s, %ds planned); the end is settled by the declared "
                       "stretch of time, not in this round"
                       % (plan["place"], plan["descriptor"], plan["seconds"]))

        started_at, checkpoint_ref = self._stay_began_at(activity_ref)
        stay["started_at"] = started_at
        stay["checkpoint_ref"] = checkpoint_ref
        if not started_at:
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="the Activity owner recorded no stay start, so no end can be claimed")
        elapsed = _seconds_between(started_at, obs)
        if elapsed is None:
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="the stay's own timestamps could not be ordered (%r -> %r)"
                       % (started_at, obs))
        stay["elapsed_seconds"] = elapsed
        stay["remaining_seconds"] = max(0, int(plan["seconds"]) - int(elapsed))
        complete = elapsed >= int(plan["seconds"])
        self._record_world_stay_progress(
            base, activity_ref=activity_ref, plan=plan,
            started_at=started_at, elapsed_seconds=elapsed, complete=complete,
            correlation_id=base.get("correlation_id"))
        if not complete:
            stay["phase"] = STAY_PHASE_ONGOING
            return self._finish(
                base, OUTCOME_ACTED, llm_calls=1,
                detail="still at %s: %ds of %ds elapsed; the end has not come yet"
                       % (plan["place"], elapsed, plan["seconds"]))
        stay["phase"] = STAY_PHASE_END
        stay["ended_at"] = obs
        return self._finish(
            base, OUTCOME_ACTED, llm_calls=1,
            detail="stayed at %s for %ds (declared %ds); the World owner's own end "
                   "observation is the only reason this counts"
                   % (plan["place"], elapsed, plan["seconds"]))
    def _record_world_progress(self, base: dict[str, Any], *, activity_ref: Any,
                               destination: Any, action_ref: Any,
                               correlation_id: Any) -> None:
        """Hand LR-5 an observation of the World owner's own state.

        The place comes from the World owner's canonical state, never from the action
        payload or the model.  LR-5 already owns ``WORLD_CANONICAL_OBSERVATION`` and
        ``world_authority``, so nothing new is invented here either.
        """
        import activity_progress_completion_lr5 as lr5

        if not activity_ref or not destination:
            return
        place = str(destination).split(":", 1)[-1]
        revision = (self.last_world_observation or {}).get("world_revision")
        # The canonical place the World owner reports, in LR-5's own world_fact
        # namespace, with the revision the observation was taken from.
        fact_ref = ("world_fact:place:%s:r%s" % (place, revision)
                    if revision is not None else "world_fact:place:%s" % place)
        now = _utc_now_iso()
        evidence = lr5.LifeProgressEvidence(
            progress_evidence_id=(
                f"pevid:{hashlib.sha256(f'{activity_ref}:{place}'.encode('utf-8')).hexdigest()[:16]}"),
            source_kind=lr5.SOURCE_KIND_WORLD_FACT,
            source_ref=f"world:state:{place}",
            authority_domain="world_authority",
            epistemic_kind=lr5.EPISTEMIC_CANONICAL_OBSERVATION,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            idempotency_key=f"pevid_idem:{activity_ref}:{place}",
            activity_id=str(activity_ref),
            action_ref=str(action_ref) if action_ref else None,
            world_fact_ref=fact_ref,
            target_ref=fact_ref,
            # W05R-R1: the bare place fact (no revision) is what a completion contract
            # declared *before* the arrival can name -- the revision of the observation
            # that confirms the arrival does not exist yet when the Activity is adopted.
            observed_facts={"world_location_ref": fact_ref,
                            WORLD_PLACE_FACT_KEY: "%s%s" % (WORLD_PLACE_FACT_PREFIX, place),
                            "observation_source": "world_gateway.get_snapshot"},
            correlation_id=str(correlation_id) if correlation_id else None,
        )
        try:
            result = self.runtime.ag2_progress_port.ingest_progress_evidence(evidence)
        except Exception as exc:
            base["progress_ingest_error"] = f"{type(exc).__name__}: {exc}"
            return
        state = result.get("progress_state")
        base["progress_state"] = (state.get("progress_state")
                                  if isinstance(state, dict) else state)
        update = result.get("checkpoint_update") or {}
        base["checkpoint_update_ref"] = update.get("checkpoint_update_id")
        base["checkpoint_ref"] = update.get("checkpoint_ref") or update.get("new_checkpoint_ref")
        # I-r2: the *World* consequence is now something Memory may learn about, through the
        # same outbox the artifact path uses.  The refs are the owner's own facts, and the
        # consumer -- not this module -- decides what it admits.
        self._emit_world_experience(base, activity_ref=activity_ref,
                                    world_refs=[fact_ref, "world:place:%s" % place])


    def _record_world_consume_progress(self, base: dict[str, Any], *, activity_ref: Any,
                                       object_id: str, action_ref: Any,
                                       correlation_id: Any) -> None:
        """Hand LR-5 the World owner's own consumption fact for *this* instance.

        Same seam as the place fact: ``world_authority`` + ``WORLD_CANONICAL_OBSERVATION``, in
        LR-5's own ``world_fact`` namespace, carrying the receipt's own object id.  Nothing
        here is derived from "the object disappeared" -- the receipt is the only reason this
        function is called at all.
        """
        import activity_progress_completion_lr5 as lr5

        if not activity_ref or not object_id:
            return
        fact_ref = "world_fact:object:%s:consumed" % object_id
        now = _utc_now_iso()
        evidence = lr5.LifeProgressEvidence(
            progress_evidence_id=(
                f"pevid:{hashlib.sha256(f'{activity_ref}:consume:{object_id}'.encode('utf-8')).hexdigest()[:16]}"),
            source_kind=lr5.SOURCE_KIND_WORLD_FACT,
            source_ref=f"world:consumption:{object_id}",
            authority_domain="world_authority",
            epistemic_kind=lr5.EPISTEMIC_CANONICAL_OBSERVATION,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            idempotency_key=f"pevid_idem:{activity_ref}:consume:{object_id}",
            activity_id=str(activity_ref),
            action_ref=str(action_ref) if action_ref else None,
            world_fact_ref=fact_ref,
            target_ref=fact_ref,
            observed_facts={"world_object_ref": "world_fact:object:%s" % object_id,
                            WORLD_CONSUME_FACT_KEY: fact_ref,
                            "observation_source": "world_gateway.get_action_result"},
            correlation_id=str(correlation_id) if correlation_id else None,
        )
        try:
            result = self.runtime.ag2_progress_port.ingest_progress_evidence(evidence)
        except Exception as exc:
            base["progress_ingest_error"] = f"{type(exc).__name__}: {exc}"
            return
        state = result.get("progress_state")
        base["progress_state"] = (state.get("progress_state")
                                  if isinstance(state, dict) else state)
        update = result.get("checkpoint_update") or {}
        base["checkpoint_update_ref"] = update.get("checkpoint_update_id")
        base["checkpoint_ref"] = update.get("checkpoint_ref") or update.get("new_checkpoint_ref")
        # I-r2: the consumed instance is a real consequence of hers, so Memory is told through
        # the same outbox (the receipt -- not "the object is gone" -- is why this runs at all).
        self._emit_world_experience(base, activity_ref=activity_ref,
                                    world_refs=[fact_ref, "world:object:%s" % object_id])

    def _record_world_stay_progress(self, base: dict[str, Any], *, activity_ref: Any,
                                    plan: Mapping[str, Any], started_at: str,
                                    elapsed_seconds: int, complete: bool,
                                    correlation_id: Any) -> None:
        """Hand LR-5 the stay's own facts, with the completion value only when it is true.

        The evidence carries the World owner's own place fact (the observation of *this*
        round), the stay's declared plan, and the elapsed seconds measured against the start
        LR-5 itself recorded.  ``WORLD_STAY_FACT_KEY`` -- the key the Activity's completion
        contract matches on -- is present **only** when ``complete`` is true, so an early
        round cannot satisfy the criterion no matter how many times it is evaluated.
        """

        import activity_progress_completion_lr5 as lr5

        if not activity_ref:
            return
        place = str(plan["place"])
        revision = (self.last_world_observation or {}).get("world_revision")
        place_fact = (WORLD_PLACE_FACT_PREFIX + place if revision is None
                      else "%s%s:r%s" % (WORLD_PLACE_FACT_PREFIX, place, revision))
        stay_ref = str(plan["target_ref"])
        # The evidence must name a ref the Activity's own binding declares: the adopted target
        # set is in LR-5's ``world_fact`` namespace, so the stay's plan travels as
        # ``world_fact:stay:<place>:<descriptor>:<seconds>`` -- the very value the completion
        # criterion expects.
        expected = "%s%s" % (WORLD_STAY_FACT_PREFIX, stay_ref[len(STAY_REF_PREFIX):])
        facts: dict[str, Any] = {
            "world_location_ref": place_fact,
            WORLD_PLACE_FACT_KEY: "%s%s" % (WORLD_PLACE_FACT_PREFIX, place),
            "stay_target_ref": stay_ref,
            "stay_affordance": plan["descriptor"],
            "stay_planned_seconds": int(plan["seconds"]),
            "stay_started_at": started_at,
            "stay_elapsed_seconds": int(elapsed_seconds),
            "stay_observed_at": base.get("observed_at"),
            "observation_source": "world_gateway.get_snapshot",
        }
        if complete:
            facts[WORLD_STAY_FACT_KEY] = expected
            # A structured, offset-free checkpoint ref (the ref vocabulary rejects "+hh:mm"),
            # so the end checkpoint names the stay and the declared end it really reached.
            facts["checkpoint_ref"] = "checkpoint:stay:%s:at:%d" % (
                plan["descriptor"], _epoch_seconds(started_at))
        now = str(base.get("observed_at") or _utc_now_iso())
        evidence = lr5.LifeProgressEvidence(
            progress_evidence_id=(
                "pevid:%s" % hashlib.sha256(
                    ("%s:stay:%s:%s:%s" % (activity_ref, stay_ref,
                                            "end" if complete else "start",
                                            started_at)).encode("utf-8")).hexdigest()[:16]),
            source_kind=lr5.SOURCE_KIND_WORLD_FACT,
            # The stay's own refs: the place fact stays in ``observed_facts`` (that is where the
            # World observation lives), while the evidence's *identity* refs name the stay, so
            # the Activity's admission check cannot attribute this consequence to the MOVE
            # Activity that happened to observe the same place a moment earlier.
            source_ref="world:stay:%s:%s" % (place, plan["descriptor"]),
            authority_domain="world_authority",
            epistemic_kind=lr5.EPISTEMIC_CANONICAL_OBSERVATION,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            idempotency_key="pevid_idem:%s:stay:%s:%s:%s" % (
                activity_ref, stay_ref, "end" if complete else "start",
                started_at if complete else str(elapsed_seconds)),
            activity_id=str(activity_ref),
            world_fact_ref=expected,
            target_ref=expected,
            observed_facts=facts,
            correlation_id=str(correlation_id) if correlation_id else None,
        )
        try:
            result = self.runtime.ag2_progress_port.ingest_progress_evidence(evidence)
        except Exception as exc:
            base["progress_ingest_error"] = "%s: %s" % (type(exc).__name__, exc)
            return
        if result.get("disposition"):
            # An ingested-but-unbound evidence is reported, never silently dropped: LR-5 is the
            # owner of "is this correlated", and its own disposition is the answer.
            base["progress_disposition"] = result.get("disposition")
            base["progress_reason_code"] = result.get("reason_code")
        state = result.get("progress_state")
        base["progress_state"] = (state.get("progress_state")
                                  if isinstance(state, dict) else state)
        update = result.get("checkpoint_update") or {}
        base["checkpoint_update_ref"] = update.get("checkpoint_update_id")
        base["checkpoint_ref"] = (update.get("checkpoint_ref")
                                  or update.get("new_checkpoint_ref"))
        if complete:
            # I-r2: a stay whose declared stretch really elapsed is an owner-confirmed
            # consequence too, so it reaches Memory through the same outbox -- naming the stay
            # and the place fact the World itself observed, and no artifact at all.
            self._emit_world_experience(base, activity_ref=activity_ref,
                                        world_refs=[expected, place_fact])
    def _experience_view(self) -> list[dict[str, Any]]:
        """The bounded, governance-filtered experiences this round may weigh.

        Empty when no provider is installed or the provider fails, which is also exactly how the
        A/B control removes memory from a decision without deleting anything.
        """
        if self.experience_provider is None:
            return []
        try:
            return [dict(item) for item in (self.experience_provider() or []) if isinstance(item, Mapping)]
        except Exception:
            return []

    def _emit_progress_experience(self, out: Mapping[str, Any], base: dict[str, Any],
                                  *, artifact_ref: Any, version_ref: Any) -> None:
        """Tell the experience consumer that a verified change really happened.

        Delivery failures are recorded, never raised: AG-2 keeps the handoff pending for a retry and
        nothing about the work is rolled back.
        """
        int_rec = out.get("integration_record")
        integration_id = getattr(int_rec, "integration_id", None)
        activity_ref = base.get("activity_ref")
        if not integration_id or not activity_ref:
            return
        artifact_refs = [r for r in (self._owner_artifact_ref(artifact_ref),
                                     f"artifactversion:{version_ref}" if version_ref else None)
                         if r]
        try:
            result = self.runtime.coordinator.emit_experience_handoff(
                integration_id=str(integration_id),
                activity_id=str(activity_ref),
                terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
                occurred_at=base.get("observed_at"),
                artifact_refs=artifact_refs,
            )
        except Exception as exc:
            base["experience_handoff_error"] = f"{type(exc).__name__}: {exc}"
            return
        dispatch = result.get("dispatch_result") or {}
        deliveries = dispatch.get("deliveries") or []
        handoff_event = result.get("handoff_event")
        base["experience_handoff"] = {
            "handoff_id": getattr(handoff_event, "handoff_id", None),
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "disposition": deliveries[-1].get("consumer_disposition") if deliveries else None,
            "status": deliveries[-1].get("status") if deliveries else None,
            "pending": len(dispatch.get("pending_handoff_ids") or []),
        }

    def _emit_world_experience(self, base: dict[str, Any], *, activity_ref: Any,
                               world_refs: Sequence[str], kind: Optional[str] = None) -> None:
        """Tell the experience consumer that a *World* consequence really happened.

        The artifact path already had this (``_emit_progress_experience``); a stay, an arrival
        or a consumed instance had no handoff at all, so the only thing Memory could ever
        learn about her day outside was a document version.  This uses the same AG-2 outbox
        (``emit_experience_handoff``) with the refs Memory's admission contract asks for:
        the Activity, the action, and the World facts the owner itself confirmed.  No artifact
        ref is invented for a World-only consequence, and no Memory row is written here -- the
        consumer decides what it admits.
        """

        integration_id = base.get("integration_id")
        if not integration_id or not activity_ref:
            return
        refs = [str(ref) for ref in world_refs if ref]
        try:
            result = self.runtime.coordinator.emit_experience_handoff(
                integration_id=str(integration_id),
                activity_id=str(activity_ref),
                terminal_or_milestone_kind=kind or ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
                occurred_at=base.get("observed_at"),
                world_refs=refs,
            )
        except Exception as exc:
            base["experience_handoff_error"] = "%s: %s" % (type(exc).__name__, exc)
            return
        dispatch = result.get("dispatch_result") or {}
        deliveries = dispatch.get("deliveries") or []
        handoff_event = result.get("handoff_event")
        base["world_experience_handoff"] = {
            "handoff_id": getattr(handoff_event, "handoff_id", None),
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "disposition": deliveries[-1].get("consumer_disposition") if deliveries else None,
            "status": deliveries[-1].get("status") if deliveries else None,
            "world_refs": refs,
        }

    def _record_artifact_progress(self, base: dict[str, Any], *, activity_ref: Any,
                                  artifact_id: Any, version_id: Any, version_number: Any,
                                  correlation_id: Any, action_ref: Any) -> None:
        """Hand LR-5 the only thing it may act on: an observation of the owner's own state.

        The version reference comes from the owner's ``read_head``, never from the model or from
        the action payload, and it is submitted as canonical artifact state, not as a claim about
        the action.  If the contract or the observation is refused, the refusal is reported in the
        round result instead of being swallowed.
        """
        import activity_progress_completion_lr5 as lr5

        # The owner confirmed this version and we are the ones who wrote it.  Handing it to the
        # artifact feed advances that feed's cursor, which is how our own edit stops being an
        # event to ourselves -- the feed's own bookkeeping, not a suppression of the work's
        # future changes: another writer's version still differs from the cursor and still wakes.
        if version_id and self.own_write_recorder is not None:
            try:
                self.own_write_recorder(str(artifact_id or ""), str(version_id))
            except Exception as exc:  # a bookkeeping failure is reported, never hidden
                base["own_write_record_error"] = "%s: %s" % (type(exc).__name__, exc)
        if not activity_ref or not version_id:
            return
        artifact_bare = str(artifact_id or "").split(":", 1)[-1]
        version_bare = str(version_id).split(":", 1)[-1]
        now = _utc_now_iso()
        evidence = lr5.LifeProgressEvidence(
            progress_evidence_id=(
                f"pevid:{hashlib.sha256(f'{activity_ref}:{version_id}'.encode('utf-8')).hexdigest()[:16]}"),
            source_kind="ARTIFACT_CANONICAL_STATE",
            source_ref=f"artifactstate:{artifact_bare}:{version_bare}",
            authority_domain="artifact_authority",
            epistemic_kind="CANONICAL_OBSERVATION",
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            idempotency_key=f"pevid_idem:{activity_ref}:{version_id}",
            activity_id=str(activity_ref),
            action_ref=str(action_ref) if action_ref else None,
            artifact_ref=f"artifact:{artifact_bare}",
            target_ref=f"artifact:{artifact_bare}",
            observed_facts={"latest_version_ref": f"artifactversion:{version_bare}",
                            "version_number": version_number,
                            "observation_source": "life_supply.read_head"},
            correlation_id=str(correlation_id) if correlation_id else None,
        )
        try:
            result = self.runtime.ag2_progress_port.ingest_progress_evidence(evidence)
        except Exception as exc:
            base["progress_ingest_error"] = f"{type(exc).__name__}: {exc}"
            return
        state = result.get("progress_state")
        base["progress_state"] = (state.get("progress_state")
                                  if isinstance(state, dict) else state)
        update = result.get("checkpoint_update") or {}
        base["checkpoint_update_ref"] = update.get("checkpoint_update_id")
        base["checkpoint_ref"] = update.get("checkpoint_ref") or update.get("new_checkpoint_ref")

    # -- narrow step -------------------------------------------------------------------

    def _adoption_spec(self, point: ArtifactResumePoint) -> dict[str, Any]:
        # LR-2 validates these as "<namespace>:<id>", so the owner's own id prefix is stripped
        # once and re-added as a namespace instead of being nested twice.
        def _ref(namespace: str, value: str, *owner_prefixes: str) -> str:
            text = str(value or "")
            for prefix in owner_prefixes:
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break
            return f"{namespace}:{text}"

        artifact_ref = _ref("artifact", point.artifact_id, "artifact:")
        version_ref = (_ref("artifactversion", point.head_version_id, "artifact-version:")
                       if point.head_version_id else "")
        target_refs = [r for r in (artifact_ref, version_ref) if r]
        spec = ag2.ActivityAdoptionSpec(
            activity_kind="CONTINUE_PERSONAL_ARTIFACT",
            title=f"继续《{point.title or point.artifact_id}》",
            target_refs=target_refs,
            origin_ref=artifact_ref,
            # This is an open-ended work: one committed version is progress, not the end of the
            # activity.  So there is deliberately no automatic completion contract -- wrapping up
            # is her explicit choice (ABANDON/收尾), never something a single write decides.  The
            # checkpoint follows the latest version the owner actually confirmed.
            progress_contract_spec={
                "progress_kind": "OPEN_ENDED",
                "checkpoint_semantics": "LATEST_ARTIFACT_VERSION",
                "target_refs": target_refs,
                "policy_version": "chiyo.life.progress.open_ended.v1",
            },
            result_binding_spec={
                "target_refs": target_refs,
                "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"],
                "authority_domains": ["result_settlement_authority", "artifact_authority"],
                "policy_version": "chiyo.life.progress.artifact.v1",
            },
        )
        return spec.to_dict()

    def _commit_next_version(
        self, point: ArtifactResumePoint, base: dict[str, Any], obs: str
    ) -> StepReceipt:
        """Commit the next real version, with the head read before the edit as the precondition."""
        assert self.artifact_port is not None
        activity_ref = str(base.get("activity_ref") or "")
        correlation_id = str(base.get("correlation_id") or "")
        decision_id = str(base.get("decision_id") or "")
        # The operation key is derived from what is intended (this activity step), never from the
        # attempt, so an UNKNOWN can be resolved and retried without minting a second version.
        key = operation_key_for("commit", point.artifact_id, str(point.head_version_id),
                               activity_ref, correlation_id)
        try:
            content = self.writer.next_content(point, observed_at=obs)
        except (life_pause.PauseConfirmedError, life_pause.RequestNotSentError):
            # W05R-R1c: a pause that stopped the content request (or a transport that cannot
            # coordinate it) is not a content failure -- it is reported as exactly what it is
            # by ``evaluate_once``, and nothing was written.
            raise
        except Exception as exc:
            outcome = ArtifactOutcome(effect_state=EffectState.REFUSED, status="CONTENT_FAILED",
                                      reason=f"{type(exc).__name__}", detail=str(exc)[:200],
                                      artifact_id=point.artifact_id, operation_key=key)
            receipt = StepReceipt.from_outcome(
                outcome, expected_head=point.head_version_id, artifact_id=point.artifact_id,
                operation_key=key, correlation_id=correlation_id or None,
                decision_id=decision_id or None, activity_ref=activity_ref or None)
            self._journal(receipt)
            return receipt
        # W05R-R1: the content request above is a model wait and is made without the pause
        # lock.  The owner call is a finite commit, so it is made *inside* the shared
        # confirmation lock: a pause confirmed at any point before this returns fails the
        # acquire (fail closed, nothing written), and one confirmed while the owner call is
        # running waits for that call instead of being overtaken by a later write.
        if not self._pause_lease.acquire():
            raise life_pause.PauseConfirmedError(life_pause.REASON_PAUSE_CONTENDED,
                                                stage="before_owner_submit")
        try:
            held = life_pause.gate(self.pause_state_root)
            if held["blocked"]:
                raise life_pause.PauseConfirmedError(str(held["reason_code"]),
                                                     stage="before_owner_submit")
            try:
                outcome = self.artifact_port.commit_version(
                    artifact_id=point.artifact_id,
                    expected_head=point.head_version_id,
                    content=content,
                    grant_id=str(self.artifact_grant_id),
                    operation_key=key,
                )
            except LifeArtifactUnavailable as exc:
                outcome = ArtifactOutcome(effect_state=EffectState.UNKNOWN, status="UNREACHABLE",
                                          reason="ARTIFACT_OWNER_UNREACHABLE", detail=str(exc)[:200],
                                          artifact_id=point.artifact_id, operation_key=key)
            except ArtifactOwnerRefused as exc:
                outcome = ArtifactOutcome(effect_state=EffectState.REFUSED, status=exc.status,
                                          reason=exc.reason, detail=exc.detail[:200],
                                          artifact_id=point.artifact_id, operation_key=key)
        finally:
            self._pause_lease.release()

        if outcome.unknown:
            # Resolve before reporting: never call an unknown effect a success or a failure.
            try:
                resolved = self.artifact_port.resolve_operation(key)
                outcome = resolved
            except (LifeArtifactUnavailable, ArtifactOwnerRefused):
                pass

        receipt = StepReceipt.from_outcome(
            outcome, expected_head=point.head_version_id, artifact_id=point.artifact_id,
            operation_key=key, correlation_id=correlation_id or None,
            decision_id=decision_id or None, activity_ref=activity_ref or None)
        self._journal(receipt)
        return receipt

    def _journal(self, receipt: StepReceipt) -> None:
        path = self.state_root / JOURNAL_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    # -- reporting ---------------------------------------------------------------------

    def _finish(self, base: dict[str, Any], outcome: str, *, llm_calls: int,
                detail: str = "") -> dict[str, Any]:
        self.last_outcome = outcome
        # E09.2: charge the budget here, not at the call sites, so every entry point
        # (timer, event wake, run-once) is charged identically.  A real cognition call
        # is spent whether or not it produced an action, and a failed real request is
        # not free.  A round that never reached a model spends nothing.
        if llm_calls:
            self.budget.note_cognition(int(llm_calls))
        if base.get("action_id") or outcome in {OUTCOME_ACTED, OUTCOME_ALREADY_DONE}:
            self.budget.note_action()
        base.update({"outcome": outcome, "detail": detail, "llm_calls": llm_calls,
                     "budget_remaining": self.budget.remaining(),
                     "budget": self.budget.snapshot()})
        return base

    def status(self) -> dict[str, Any]:
        """Read-only projection for the thin status entry (WP-09)."""
        point = None
        selection: Optional[dict[str, Any]] = None
        if self.artifact_port is not None:
            found, selection = self._read_resume_point()
            point = found.to_dict() if found else None
            if point is None and selection.get("error"):
                point = {"error": selection["error"]}
        return {
            "autonomous_enabled": self.enabled,
            "rounds_run": self.rounds_run,
            "budget": self.budget.snapshot(),
            "last_outcome": self.last_outcome,
            "last_error": self.last_error,
            "last_step_receipt": self.last_receipt.to_dict() if self.last_receipt else None,
            "current_activity_ref": self.runtime.canonical_state().get("foreground_activity_ref"),
            "current_work": point,
            "current_work_selection": selection,
            "switches": self.runtime.kill_switches.to_dict(),
            # A (life-v1): the projection is a read-only context, so status may only report
            # whether one is wired and what the last read said -- never a feeling of its own.
            "appraisal_wired": self.appraisal_source is not None,
            "last_appraisal": self._compact_appraisal(self.last_appraisal),
        }


def autonomous_life_cycle_from_env(runtime: Any, *, artifact_port: LifeArtifactPort,
                                   subject_id: str, workspace_id: str, artifact_grant_id: str,
                                   writer: Optional[ContinuationWriter] = None,
                                   experience_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
                                   situation_source: Any = None,
                                   continuity_source: Any = None,
                                   appraisal_source: Any = None,
                                   work_artifact_id: Optional[str] = None,
                                   env: Optional[Mapping[str, str]] = None) -> AutonomousLifeCycle:
    """Assemble the cycle with the switches and budget read from the environment."""
    return AutonomousLifeCycle(
        runtime,
        artifact_port=artifact_port,
        subject_id=subject_id,
        workspace_id=workspace_id,
        artifact_grant_id=artifact_grant_id,
        writer=writer,
        experience_provider=experience_provider,
        situation_source=situation_source,
        continuity_source=continuity_source,
        appraisal_source=appraisal_source,
        env=env,
    )


__all__ = [
    "AutonomousLifeCycle",
    "ContinuationWriter",
    "DeterministicContinuationWriter",
    "RoundBudget",
    "StepReceipt",
    "autonomous_life_cycle_from_env",
    "DEFAULT_MAX_ROUNDS_PER_DAY",
    "ENV_AUTONOMOUS_ENABLED",
    "ENV_MAX_ROUNDS_PER_DAY",
    "OUTCOME_ACTED",
    "OUTCOME_ADOPTION_REJECTED",
    "OUTCOME_ALREADY_DONE",
    "OUTCOME_ABANDONED",
    "OUTCOME_BUDGET_EXHAUSTED",
    "OUTCOME_DECISION_FAILED",
    "OUTCOME_DEFERRED",
    "OUTCOME_DISABLED",
    "OUTCOME_NO_ACTION",
    "OUTCOME_NO_MATERIAL",
    "OUTCOME_PAUSED",
    "OUTCOME_STEP_REFUSED",
    "OUTCOME_STEP_UNKNOWN",
    "TRIGGER_IDLE_WINDOW",
    "TRIGGER_RECOVERY",
    "TRIGGER_RESUME_OPENED",
    "TRIGGER_USER_EVENT",
]
