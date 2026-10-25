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
* The work writer may answer **no_change** (``model_continuation_writer.ModelNoChange``).  That
  is a legal no-op, not a failure: the round stops *before* any owner version, progress,
  experience or Activity completion exists, and is reported as ``NO_ACTION`` with
  ``work_no_change``.  Neither path fabricates an edit of the same bytes to look productive.
* The private page annotation is **optional and injected**: A's ``ModelReadingResponse`` writes
  one bounded private note/question/nothing, A's pure ``private_daily_items.plan_page_annotation``
  turns it into document bytes, and both are committed in the *same* private-items compare-and-swap
  as the acquisition and the stop.  With no response writer wired the round keeps its previous
  as the acquisition and the stop.  With no response writer wired the round keeps its previous
  behaviour exactly and reports ``NOT_CONFIGURED`` -- it never invents a note, and a response
  failure writes nothing at all (no half of the step).  A blank page is not sent to the content
  writer at all (``SKIPPED_EMPTY``) while its acquisition and stop are still saved, and the two
  owners are re-read after the request so a material/document/revocation move writes nothing.
"""
from __future__ import annotations

import hashlib
import inspect
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
#: v0.4 wake cost: the artifact owner is read several times inside one round (the
#: material check, the candidate refresh/materialise and the selected-work read all
#: want the same point-in-time answer).  The wrapper reuses those answers for that
#: one round only and drops them at every round boundary and every owner write.
from wake_candidate_cost import maybe_round_cached_port
from action_reality_ledger import (
    ACTION_KIND_FILE,
    ACTION_KIND_WORLD,
    ACTION_STATUS_PREPARED,
    ATTEMPT_STATUS_PRE_SUBMIT_FAILED,
    TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
)
from managed_artifact_action_adapter import OP_COMMIT_VERSION
import life_pause
import life_request_budget
#: v2 第一段连接（过去到候选 → 下一步准备）：only the *projection* of an owner record's own
#: target refs.  It reads no owner, mints no ref and authorizes nothing; the owner's resume point
#: and AR-0 admission still decide whether the object can really be continued.
import past_candidate_bridge as pcb
#: v0.4 day-life: the *read-only* step readiness of the foreground Activity, plus the bounded
#: reconsideration ledger.  Both are this cycle's own state; neither is an owner, a permission
#: or a second store of the subject.
import life_step_readiness as lsr
#: v0.4 C: the one real WAIT basis (a named dependency of the *current* Activity) and the
#: vocabulary its LR-4 condition/event is spelled with.  Pure: it reads nothing, writes
#: nothing and decides nothing.
import life_wait_basis as lwb
#: D (life-v1): the situation read (A) and the continuity read (C) are *injected*; this
#: module only consumes them.  Neither one authorizes anything.
import life_situation_view as lsv
#: LR-5 owns progress/completion; the cycle only *describes* contracts for it.
import activity_progress_completion_lr5 as lr5
#: v0.4 stage A: her own "later" (a store of deferred intentions) and the bounded options for a
#: *new* private project, plus the narrow gateway onto B's project owner.  All three are pure
#: seams: they read owners, they never decide and they never authorize an Activity verb.
import life_future_items as lfi
import life_project_options as lpo
#: v0.4 research: the bounded read/compose seam onto B's research workspace.  It carries no
#: marker definition of its own (that stays B's) and it is never an experience provider: the
#: research text it returns is *document* context the owner really holds.
import research_work_context as rwc
#: v0.4 C: the bounded seam onto B's practice workspace (reviews + methods) and A's Memory
#: service.  It borrows B's marker parser, invents no second library, and never executes a method:
#: a practice is document context for a prompt and an evaluation is B's to record.
import life_practice_context as lpc
#: v0.4 lived day: the bounded *live* view of where she really is (place by name, what is in her
#: hands, where she could walk to and how long that takes, whether those places are open, and the
#: concrete things this observation offers).  It reads the World seam's own contract when it
#: publishes one and is data only -- it grants, ranks and chooses nothing.
import life_live_context_view as llcv
#: v0.4 body participation: the bounded *qualitative* body situation (burden bands, whether a
#: recovery session is open, whether the owner says resting is possible now, and what could not
#: be read).  It is a pure projection over the body column the situation read already obtained
#: plus the World owner's own REST entry -- no second body read, no number, no Candidate.
import life_body_context as lbc
#: v0.4 sharing: the bounded seam onto B's ``LifeSharingService`` (open drafts + the local inbox).
#: It never sends anything itself and never claims an external delivery; it turns one Decision
#: request into exactly one owner call and reports the owner's own answer.
import life_sharing_context as lsc

READING_PAGE_MARKER = "%%PAGE%%"
#: rc1-C: the Activity shape of one bounded page read.  Named here (and used by
#: ``_reading_adoption_spec``) so the *foreground* reading Activity can be recognised from the
#: Activity owner's own record instead of from this module's bookkeeping.
READING_ACTIVITY_KIND = "READ_PERSONAL_MATERIAL"
#: rc1-v04: the *only* keys a reader response may hand to A's pure annotation planner.  A real
#: ``ModelReadingResponse.respond`` returns its source metadata beside them; that metadata is
#: checked against the page here and never forwarded, because the planner refuses unknown fields.
RESPONSE_ANNOTATION_FIELDS: tuple[str, ...] = ("kind", "title", "body", "note")


def private_materials_module() -> Any:
    """``subject_private_materials`` (the pure situation read), imported on demand.

    Kept behind a function for the same reason ``journey_module`` is: the cycle is imported by
    callers that have not necessarily put this directory on ``sys.path`` yet, and the situation
    read is only needed once a reading material is actually bound.
    """
    import subject_private_materials
    return subject_private_materials


def lr5_module() -> Any:
    """The LR-5 owner module (kept behind a function for the lazy call sites)."""
    return lr5

def body_action_targets_module() -> Any:
    """W's ``life_body_action_targets`` -- the *only* owner of the ``bodyact:`` encoding.

    Imported on demand, and a missing module is a named refusal instead of a silent fallback: this
    cycle must never invent a pose/rest encoding of its own, so a round that names a ``bodyact:``
    target without W's parser/builder wired is reported as blocked, not guessed at.
    """

    try:
        import life_body_action_targets
    except Exception as exc:  # noqa: BLE001 - a missing/broken module is a named refusal
        raise AutonomousLifeError(
            "BODY_ACTION_TARGETS_MODULE_UNAVAILABLE: %s: %s"
            % (type(exc).__name__, exc))
    return life_body_action_targets


def daily_action_targets_module() -> Any:
    """B's ``life_daily_action_targets`` -- the *only* owner of the ``dailyact:`` encoding.

    Same rule as the body-action module above: her everyday hand work (pick something up, put
    something down) travels as one encoded target, and this cycle neither spells that encoding
    itself nor guesses what an unknown one meant.  A stage without the module is a named refusal
    (``DAILY_ACTION_TARGETS_MODULE_UNAVAILABLE``), never a silent local re-encoding.
    """

    try:
        import life_daily_action_targets
    except Exception as exc:  # noqa: BLE001 - a missing/broken module is a named refusal
        raise AutonomousLifeError(
            "DAILY_ACTION_TARGETS_MODULE_UNAVAILABLE: %s: %s"
            % (type(exc).__name__, exc))
    return life_daily_action_targets

def no_change_signal() -> Any:
    """The work writer's explicit "nothing to change" answer, imported on demand.

    Kept behind a function for the same reason the owner modules are: this cycle never imports
    the model client itself, and a caller that never wires a model writer must not pay for the
    import.  ``is_no_change`` keeps working even if that import cannot be resolved, so a
    ``ModelNoChange`` raised by an *identical* class loaded under another name is still
    recognised by its type name instead of being reported as a content failure.
    """
    from model_continuation_writer import ModelNoChange
    return ModelNoChange


def is_no_change(exc: BaseException) -> bool:
    """True when ``exc`` is the work writer's legal ``no_change`` answer."""
    try:
        return isinstance(exc, no_change_signal())
    except Exception:  # pragma: no cover - the module is a sibling; name is the fallback
        return type(exc).__name__ == "ModelNoChange"


def _error_label(exc: BaseException) -> str:
    """``Type:CODE`` for a report -- never the message text of a private request.

    A response or plan failure's message may quote what the model said about a private page, and
    the round report is not a place for that.  Only the type and a machine-readable code travel.
    """
    code = str(getattr(exc, "reason_code", "") or getattr(exc, "status", "") or "")[:60]
    return "%s:%s" % (type(exc).__name__, code) if code else type(exc).__name__

def _response_intent(semantic: Mapping[str, Any]) -> str:
    """A text-free digest of one response intent (the kind plus its own text fields).

    The step's operation key covers *what was intended*, so a different answer about the same
    page is a different logical action instead of a silent retry under the first one's key --
    and the private-items planner still refuses to reuse one page slot for a second intent.
    Only the digest travels; the note itself never enters a key, a report or a journal line.
    """
    payload = json.dumps([str(semantic.get(name) or "") for name in RESPONSE_ANNOTATION_FIELDS],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

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
#: W (v0.4): the body's own pose and rest session, expressed as *one* AR-0 target so the existing
#: single-target contract is kept.  ``bodyact:`` is W's encoding; this cycle only reads the
#: namespace back and hands the ref to W's own parser/builder -- it never re-spells a target.
BODYACT_REF_PREFIX = "bodyact:"
POSE_VERB = "POSE"
REST_VERB = "REST"
POSE_VERB = "POSE"
REST_VERB = "REST"
#: The Activity shape of one body step.  W's targets are per-parameter, so the shape is one
#: bounded target state rather than a journey.
BODY_ACTION_ACTIVITY_KIND = "BODY_ACTION_AT_OBSERVED_PLACE"
#: v0.4 lived day (B/D contract): her everyday hand work -- ``PICK_UP`` / ``PLACE`` -- as *one*
#: encoded AR-0 target, exactly like ``bodyact:``.  The encoding, the step shape and the
#: completion fact all belong to ``life_daily_action_targets``; this cycle only recognises the
#: namespace, routes the committed Decision to the real World owner, and reads the result back
#: from a fresh observation.
DAILYACT_REF_PREFIX = "dailyact:"
PICK_UP_VERB = "PICK_UP"
PLACE_VERB = "PLACE"
#: The Activity shape of one everyday hand step: a bounded target state on an object that really
#: exists in the World, finished only by the owner's own post-action observation.
DAILY_ACTION_ACTIVITY_KIND = "DAILY_ACTION_IN_OBSERVED_WORLD"
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
#: A round whose Decision was NO_ACTION **but** whose one sharing request really reached the owner.
#: Reporting that as NO_ACTION would hide a side effect, so it gets its own outcome word.
OUTCOME_SHARE_ACTED = "SHARE_ACTED"
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
#: v0.4 day-life: a candidate was legally offered and a Decision was committed, but there was
#: positively no executable step for it.  That is *unavailable*, not her choice: it is never
#: reported as ``NO_ACTION`` and never as ``ACTED``.
OUTCOME_NO_STEP_AVAILABLE = "NO_STEP_AVAILABLE"
#: v0.4 day-life: this exact state (target + revision + no-step reason) was already considered and
#: she did not move it, so no request was spent.  Silence, not a choice and not a refusal.
OUTCOME_QUIET = "QUIET"

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


def _world_ref_token(value: Any) -> str:
    """One spelling of a World target that is legal *inside* a structured reference.

    Activity continuity accepts ``<namespace>:<id>`` with an id of
    ``[A-Za-z0-9][A-Za-z0-9_.:/-]`` (``activity_continuity._STRUCTURED_REF_RE``), so a target whose
    own encoding uses another separator (B's ``dailyact:place:<object>><surface>``) cannot be pasted
    into ``source_ref`` verbatim.  Only the *separator* is replaced: the id keeps its object,
    verb and surface, so nothing about what was acted on is lost.
    """

    text = "".join(ch if (ch.isalnum() or ch in "_.:/-") else "_"
                   for ch in str(value or "").strip())
    return text[:160] or "unknown"


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


class ReadingResponseFailed(AutonomousLifeError):
    """The private reading response -- or the pure plan built from it -- is unusable.

    Raised *before* anything is handed to the owner, so the round writes nothing at all: not the
    annotation, and not the page read it was going to be committed with.  A model that answered
    badly is not a success, and it is not a decision failure either -- ``evaluate_once`` reports
    it as a refused step.
    """


class ContinuationWriter(Protocol):
    """Produces the next version's text from the current head.  Pluggable on purpose.

    Besides returning the bytes to write, an implementation may raise
    ``model_continuation_writer.ModelNoChange`` to say, explicitly, that this work needs no
    change.  That is a legal answer, and the cycle reports it as a no-op before any owner call;
    it is never expressed by returning the unchanged text, which would mint a version that
    changes nothing.
    """

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
                 is_reading: Optional[Callable[[Any], bool]] = None,
                 closeout: Optional[Callable[[Any], Mapping[str, Any]]] = None,
                 is_closeout: Optional[Callable[[Any], bool]] = None,
                 project: Optional[Callable[[Any], Mapping[str, Any]]] = None,
                 is_project: Optional[Callable[[Any], bool]] = None) -> None:
        self._artifact = artifact
        self._world = world
        self._is_world = is_world
        self._reading = reading
        self._is_reading = is_reading or (lambda _record: False)
        #: v0.4 C: the wrap-up shape, chosen only by a Decision whose own candidate names the
        #: closeout target.  Never offered as a default: its completion contract and step both
        #: belong to W's plan, so handing it to another selection would be a different Activity.
        self._closeout = closeout
        self._is_closeout = is_closeout or (lambda _record: False)
        #: v0.4 stage A: a *new private project*.  Its shape can only be known after the owner
        #: created the work (the target is the artifact the owner just confirmed), so this branch
        #: both performs the one real owner write and describes the Activity it enables.
        self._project = project
        self._is_project = is_project or (lambda _record: False)
        self._cache: Optional[dict[str, Any]] = None

    def __call__(self, decision_record: Any) -> dict[str, Any]:
        if self._cache is None:
            if self._is_world(decision_record):
                source = self._world(decision_record)
            elif self._closeout is not None and self._is_closeout(decision_record):
                source = self._closeout(decision_record)
            elif self._project is not None and self._is_project(decision_record):
                source = self._project(decision_record)
            elif self._reading is not None and self._is_reading(decision_record):
                source = self._reading(decision_record)
            else:
                source = self._artifact
            # v0.4 stage A: the work shape is itself Decision-bound (which project she continues is
            # only known after the Decision), so it arrives as a callable like the other branches.
            if callable(source):
                source = source(decision_record)
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

def accepts_context(function: Any) -> bool:
    """True when ``function`` really takes the optional ``context=`` keyword.

    The content writer grew one optional keyword (``context``) for v0.4 research.  A caller must
    not guess: a writer built against the older two-argument protocol is called exactly as before,
    so wiring a research workspace can never break a writer that does not speak it.
    """

    try:
        return "context" in inspect.signature(function).parameters
    except (TypeError, ValueError):  # a builtin or a callable without a signature
        return False


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

    def next_content(self, point: Any, *, observed_at: str,
                     context: Optional[Mapping[str, Any]] = None) -> str:
        self._admit("content")
        # v0.4 research: the bounded research context is forwarded only to a writer that really
        # accepts it; a writer with the original two-argument protocol is called exactly as before.
        if context is not None and accepts_context(self._inner.next_content):
            return self._inner.next_content(point, observed_at=observed_at, context=context)
        return self._inner.next_content(point, observed_at=observed_at)

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this proxy does not define; ``_inner``/``_admit``
        # are set in ``__init__`` so a lookup before that cannot recurse forever.
        if name in {"_inner", "_admit"}:
            raise AttributeError(name)
        return getattr(self._inner, name)


class _PauseAdmittedReadingResponse:
    """The private page response behind the same request-start handshake as the decision.

    A reading response is a real model request about private material, so it is admitted at its
    own send boundary exactly like the content request: a pause confirmed before it cannot be
    overtaken by a request that starts afterwards, and the response wait itself never holds the
    shared lock.  Request budget is not re-implemented here -- the client handed in by the
    assembly is the same budgeted, pause-guarded client the rest of the instance uses.
    """

    def __init__(self, inner: Any, admit: Callable[[str], None]) -> None:
        self._inner = inner
        self._admit = admit

    def respond(self, context: Any, *, observed_at: str) -> Any:
        self._admit("reading_response")
        return self._inner.respond(context, observed_at=observed_at)

    def __getattr__(self, name: str) -> Any:
        if name in {"_inner", "_admit"}:
            raise AttributeError(name)
        return getattr(self._inner, name)

#: v0.4 C: the closeout seam.  ``artifact_closeout`` (W's pure plan) is the *only* module allowed
#: to describe how a private work is wrapped up; this cycle consumes its four contract functions
#: and never re-implements the plan.  While that module is absent nothing is imported, no closeout
#: candidate is offered anywhere, and the missing piece is reported by name.
CLOSEOUT_TARGET_NAMESPACE = "closeout:artifact"
CLOSEOUT_ACTIVITY_KIND = "CLOSE_PERSONAL_ARTIFACT"
CLOSEOUT_REQUIRED_FUNCTIONS = ("parse_closeout_target", "closeout_target",
                              "build_closeout_adoption_spec", "build_closeout_step")
CLOSEOUT_NOT_DELIVERED = "ARTIFACT_CLOSEOUT_NOT_DELIVERED"


def closeout_module() -> Any:
    """W's ``artifact_closeout`` module, or ``None``.

    Deliberately a function (not a module-level import) so a half-written file cannot be pulled
    into this process and so a test can inject a plan object.  The caller must still verify the
    four contract functions; this only answers "is it importable at all?".
    """

    try:
        import artifact_closeout  # noqa: F401  (resolved lazily on purpose)
    except Exception:
        return None
    return artifact_closeout


def _call_plan(function: Any, **kwargs: Any) -> Any:
    """Retired: W's ``artifact_closeout`` API is delivered and called directly now.

    Kept only as a named place so a re-introduced indirection has an obvious home; nothing in
    this module calls it.
    """

    return function(**kwargs)


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
        #: rc1-v04: the optional private page response (A's ``ModelReadingResponse``).
        reading_response_writer: Optional[Any] = None,
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
        #: v0.4 stage A: her own "later" items (``life_future_items.FutureItems``) and the narrow
        #: gateway onto the private-project owner (``life_project_options.ProjectOwnerGateway``).
        #: Both are optional and ``None`` is a first-class state: with neither wired the round
        #: behaves exactly as before, invents no item and creates no project.
        future_items: Any = None,
        project_owner: Any = None,
        project_questions_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
        max_project_options: Optional[int] = None,
        #: v0.4 research: B's ``ResearchWorkspaceService`` instance (or ``None``).  It is wrapped
        #: in A's bounded seam, so this cycle can *see* real material hits and real research
        #: results and can ask it to create one research work when a Decision selects that option
        #: -- and nothing else.  With none wired no research option is offered at all.
        research_workspace: Any = None,
        #: v0.4 C: B's ``PersonalPracticeService`` instance (or ``None``).  It is wrapped in C's
        #: bounded seam, so this cycle can read her own reviews/methods and A's recallable
        #: experiences, offer one bounded *review* option, ask for one review or one method when a
        #: committed Decision selected that option/proposal, and record one evidence-checked trial.
        #: With none wired no review option exists and no method is ever named or written.
        practice_workspace: Any = None,
        #: v0.4 sharing: B's ``LifeSharingService`` (or ``None``).  With none wired the round
        #: offers no sharing action at all -- an unwired owner is never replaced by a stand-in,
        #: and there is no other place a share could go.
        sharing_service: Any = None,
        #: 显式压缩演示模式（COMPRESSED_DEMO）的业务时钟：一个无参 callable，返回
        #: 本域的业务 now。``None`` 是普通真实时间路径；非 None 时业务到期、安静、
        #: 未来事项、LR-5 业务证据与事件时刻都读同一个域。真实安全面（账本 UTC 日、
        #: lease、权限、provider 超时）不经过这里。
        clock: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.runtime = runtime
        self._business_clock = clock
        self.state_root = Path(runtime.state_root)
        self.artifact_port = maybe_round_cached_port(artifact_port)
        self.subject_id = subject_id
        self.workspace_id = workspace_id
        self.artifact_grant_id = artifact_grant_id
        #: v0.4 work-revisit: the ``PrivateProjectService`` built on demand for the *one*
        #: earlier-version read a writing step may make, cached for the life of this cycle so a
        #: second piece of writing does not rebuild it.  ``None`` until it is really needed.
        self._earlier_project_service: Any = None
        self.writer = writer or DeterministicContinuationWriter()
        #: The page-response writer is *optional* and injected.  ``None`` is a first-class state:
        #: the reading step keeps its previous behaviour exactly (acquisition + stop only) and
        #: reports ``NOT_CONFIGURED``.  It is never replaced by a stand-in that fabricates a
        #: note, because a fabricated note would be a private work the subject never wrote.
        self.reading_response_writer = reading_response_writer

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
        #: rc1-v04: a page response is a real private request, so it passes the same request-start
        #: handshake immediately before it is sent (see ``_PauseAdmittedReadingResponse``).
        if self.reading_response_writer is not None:
            self.reading_response_writer = _PauseAdmittedReadingResponse(
                self.reading_response_writer, self._admit_request)
        self.last_journey_settlement: Optional[dict[str, Any]] = None
        #: D (life-v1): the injected situation reader (``life_situation_view.LifeSituationView``)
        #: and the injected continuity reader (``life_continuity_inputs.LifeContinuityInputs``).
        #: With neither wired, AG-1 receives UNKNOWN for both capacity dimensions -- the round
        #: budget is never presented as a body fact again (A's own finding: no signal means
        #: UNKNOWN, and "connection worked" is not "the body can").
        self.situation_source = situation_source
        self.continuity_source = continuity_source
        #: v0.4 B: the local-contact seam.  ``social_contacts`` is the resident's own pass over
        #: A's transport ledger, ``contact_inbox`` is that ledger and ``co_presence`` is the
        #: finite co-presence protocol.  All three stay ``None`` when the profile has no seam;
        #: nothing is polled then, and an invitation can only arrive through a real inbox.
        self.social_contacts: Any = None
        self.contact_inbox: Any = None
        self.co_presence: Any = None
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
        #: v0.4 body participation: the bounded qualitative body situation built from the same
        #: situation read this round already made (``life_body_context``).  Prompt data only.
        self.last_body_context: Optional[dict[str, Any]] = None
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
        #: rc1-v04: the shape of the private annotation the last page read planned -- kind, item
        #: id, whether it changed.  Never the note itself (that stays in the private document and
        #: in the model request that produced it).
        self.last_reading_annotation: Optional[dict[str, Any]] = None
        #: rc1-C: the last *read-only* situation of the bound materials (owner revision, valid
        #: stop on that revision, next page, page count, note counts).  It is a report and a
        #: cache for one round -- never a second store: the owners remain the only truth.
        self.last_reading_situation: Optional[dict[str, Any]] = None
        self._reading_situation_cache: Optional[dict[str, Any]] = None
        #: v0.4 day-life: the last read-only step readiness of the foreground Activity (report only)
        #: and the *round's own* readiness, which is what ``_finish`` records into the ledger.  It is
        #: reset at the top of every round, so an early exit can never reuse the previous round's.
        self.last_step_readiness: Optional[dict[str, Any]] = None
        self.last_reconsideration: Optional[dict[str, Any]] = None
        self._round_readiness: Optional[lsr.StepReadiness] = None
        #: The Activity record this round's readiness was read from (so the situation parts and the
        #: judgement can never be about two different Activities), the round's own situation parts,
        #: and the work this round *selected* (target + owner head) for a no_change that happens
        #: before any Activity exists.
        self._last_foreground_record: Optional[dict[str, Any]] = None
        self._round_situation: Optional[dict[str, Any]] = None
        self._round_work_binding: Optional[dict[str, Any]] = None
        #: v0.4 stage A: the Activity this round's park offer named (if any).  A park writes its
        #: "later" item *after* adoption has already released the foreground, so the item must name
        #: the Activity from the offer, not from a foreground read that is now empty.
        self._round_park_activity_ref: Optional[str] = None
        #: The bounded "this exact state was already considered" cache.  It lives in the cycle's own
        #: state root beside the step journal, holds no private text and never stores a Decision.
        self.reconsideration = lsr.ReconsiderationLedger(self.state_root)
        #: v0.4 stage A: her own "later" and the private-project seam.  ``future_items`` is the
        #: store (a read/write owner of its own, in this cycle's state root); ``project_gateway``
        #: is the narrow client of B's private-project service and is *unavailable* unless one is
        #: really wired, in which case selecting a project option refuses by name instead of
        #: inventing a work.  Neither of them can start, resume or finish an Activity.
        self.future_items = future_items
        self.project_gateway = project_owner
        self.project_questions_provider = project_questions_provider
        self.max_project_options = (int(max_project_options) if max_project_options is not None
                                    else lpo.MAX_OPTIONS)
        #: The last read views (report only): the items the clock has reached, the project options
        #: this round could offer, what the owners did with the writes she asked for, and the work
        #: the owner created when she chose to start something new.
        self.last_future_items: Optional[dict[str, Any]] = None
        self.last_project_options: list[dict[str, Any]] = []
        self.last_project_projects: list[dict[str, Any]] = []
        self.last_future_item_writes: list[dict[str, Any]] = []
        self.last_project_created: Optional[dict[str, Any]] = None
        self._round_future_items: list[dict[str, Any]] = []
        self._round_project_options: list[dict[str, Any]] = []
        #: v0.4 research: the bounded seam onto B's research workspace.  It reads material hits and
        #: research heads (the decision prompt and the writing prompt both get bounded slices) and
        #: it is the one thing that may ask the workspace to create a research work -- only when a
        #: Decision selected that very option.  ``None`` service == no research option at all.
        self.research = rwc.ResearchSeam(research_workspace)
        self.last_research_created: Optional[dict[str, Any]] = None
        self.last_research_context: Optional[dict[str, Any]] = None
        self.last_research_error: Optional[str] = None
        #: v0.4 C: the bounded practice seam (reviews, methods, recallable experiences) and this
        #: round's own reads.  ``None`` service == no review option and no method_ref at all.
        self.practice = lpc.PracticeSeam(practice_workspace)
        #: v0.4 sharing: B's owner seam and this round's own reads.  ``None`` means this instance
        #: has no sharing owner at all: no share is offered, and nothing pretends one was sent.
        self.sharing_service = sharing_service
        self.last_sharing_block: Optional[dict[str, Any]] = None
        self.last_sharing: Optional[dict[str, Any]] = None
        self.last_review_created: Optional[dict[str, Any]] = None
        self.last_method_created: Optional[dict[str, Any]] = None
        self.last_method_usage: Optional[dict[str, Any]] = None
        self.last_method_trial: Optional[dict[str, Any]] = None
        self.last_practice_error: Optional[str] = None
        self.last_practice_context: Optional[dict[str, Any]] = None
        self._round_methods: list[dict[str, Any]] = []
        self._round_practice_context: Optional[dict[str, Any]] = None
        #: The practice this round's committed Decision named (filled after the record is read) and
        #: the bounded trial it asked for.  Both settle in ``_finish`` so every exit path reports
        #: them, and neither ever runs a method.
        self._pending_method_usage: Optional[dict[str, Any]] = None
        self._pending_method_trial: Optional[dict[str, Any]] = None

        #: The artifact continuation source, when a port was supplied.  Registered so AG-0 can
        #: actually see the work; the registry refuses unknown or forbidden source kinds itself.
        self.artifact_source: Optional[ArtifactContinuationSource] = None
        if artifact_port is not None and subject_id:
            #: The source reads through ``self.artifact_port`` -- the *round-scoped* memo, not the
            #: raw transport.  That is what makes one wake's material check, candidate build and
            #: selection share one owner snapshot instead of asking four times; the snapshot is
            #: never used to *authorize* anything across it, because the pre-adoption freshness
            #: check drops the memo first (``_pause_gate_hook``) and re-reads the owners.
            self.artifact_source = ArtifactContinuationSource(
                self.artifact_port, subject_id=subject_id,
                # D (life-v1): the private-items document is not a piece of writing; it must
                # never be offered as one.  The reading material *is* offered here -- that is
                # how a reading opportunity becomes an admitted PERSONAL_OPPORTUNITY candidate.
                exclude_artifact_ids=([private_items_artifact_id]
                                      if private_items_artifact_id else []),
                # rc1-C: ... but only while its own situation says a page is left on the
                # current revision.  The source names no other artifact and classifies nothing:
                # the reading material is the one object whose opportunity depends on the
                # private reading state, and the provider is a read, not a permission.
                reading_artifact_id=(str(reading_artifact_id)
                                     if reading_artifact_id else None),
                reading_state_provider=self.private_material_situation,
                #: v0.4 day-life: an artifact whose Activity is PAUSED/WAITING is *resume*'s
                #: object, not a new opportunity.  Offering it here as a fresh personal
                #: opportunity would let the round start a second Activity for the same object
                #: and bypass the resume eligibility the Activity owner already owns.
                paused_target_provider=self._paused_activity_targets,
                #: v0.4 C: the bare ids of the artifacts this round may *offer* to wrap up (W's
                #: plan exists, the artifact is the explicitly bound work, it is ACTIVE and the
                #: foreground slot is free).  Empty until ``artifact_closeout`` is delivered, so
                #: this seam adds no candidate of its own.
                closeout_target_provider=self.closeout_target_ids,
                #: v0.4 research: the imported materials of her research workspace are *reference
                #: text*, never work.  The set is read fresh (the workspace owns it), so a material
                #: imported a moment ago is already out of the continuation and closeout offers.
                exclude_artifact_provider=self._non_work_artifact_ids,
                #: v0.4 stage A: the two extra *personal opportunities* of this round -- the items
                #: of hers the clock reached, and the project options her material and her own
                #: questions support.  They ride the same admitted PERSONAL_OPPORTUNITY source (no
                #: new source kind, no second adapter) and are read fresh each round.
                extra_opportunity_provider=self._extra_opportunities)
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
        #: v0.4 lived day: the bounded live view of where she really is, read from the World seam
        #: that produced this round's observation.  It is the only thing the decision prompt shows
        #: about the scene (place by name, hands, walkable places, opening facts, concrete offers),
        #: and it is data: no grant, no ranking, no choice.
        self.last_live_context: Optional[dict[str, Any]] = None
        #: The instant the live view was taken at (the seam's own spelling), or ``None``.
        self.last_world_observed_at: Optional[str] = None
        #: The object the step spec built for *this* round reports its progress on.  The AR-0
        #: payload is whitelisted by the owner adapter (``managed_artifact_action_adapter``),
        #: so a payload key cannot carry it: a page read commits the private-items document but
        #: the progress belongs to the reading material, and that is not derivable from the
        #: frozen payload alone.
        self.last_step_progress_target_ref: Optional[str] = None
        #: v0.4 day-life: the World target the last step spec was built for (report only; the
        #: authoritative spelling is always the frozen payload the owner received).
        self.last_world_target_ref: Optional[str] = None
        #: v0.4 C: the WAIT basis this round offered (report only) and the last bounded upkeep
        #: report (due scan + dependency re-check).  Neither is a decision or a permission.
        self.last_wait_basis: Optional[dict[str, Any]] = None
        #: v0.4 A (pacing): the body-conditioned breakpoint this round offered, and the named
        #: reason when it did not.  Report/prompt only -- it grants nothing and resumes nothing.
        self.last_body_wait_basis: Optional[dict[str, Any]] = None
        self.last_body_wait_reason: Optional[str] = None
        self.last_waiting_maintenance: Optional[dict[str, Any]] = None
        #: v0.4 C: injectable override for W's closeout plan (tests / a caller that already holds
        #: the module).  ``None`` means "look it up through ``closeout_module()``".
        self.closeout_plan_module: Any = None
        #: The correlation id of the round in flight, so a step plan can name its own intent.
        self.last_correlation_id: Optional[str] = None
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
            "updated_at": self._now_iso(),
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

    def _latest_resumable_work(self) -> tuple[Optional[ArtifactResumePoint], list[str]]:
        """The newest ACTIVE artifact that is *not* a document this instance knows is not work.

        ``LifeArtifactPort.latest_resumable`` is the port's generic notion ("the subject's most
        recently revised ACTIVE artifact") and is left exactly as it is: it knows nothing about
        which artifact is a piece of writing.  The cycle *does* know which artifacts are not
        work (the reading material and the private-items document, both named explicitly by the
        operator), so the fallback applies that one exclusion instead of letting a newer
        reading material or a private JSON document become "the work".

        Only reached when no work is bound and no admitted history names one; the artifacts it
        skipped are reported so a round never looks like "there was nothing" when the truth is
        "the newest artifact was my notes".
        """
        skipped: list[str] = []
        rows: list[dict[str, Any]] = []
        for row in self.artifact_port.list_artifacts():
            if str(row.get("lifecycle")) != "ACTIVE":
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            if artifact_id in self.artifact_exclusions:
                skipped.append(artifact_id)
                continue
            rows.append(dict(row))
        if not rows:
            return None, skipped
        best = max(rows, key=lambda row: int(row.get("revision") or 0))
        point = self.artifact_port.resume_point(str(best["artifact_id"]))
        return (point if point.resumable else None), skipped

    def resume_selection(self) -> tuple[Optional[ArtifactResumePoint], dict[str, Any]]:
        """Read-only: the *same* work selection ``evaluate_once`` will use (no write, no spend).

        The resident's start-time baseline needs the selection this cycle actually makes, not a
        second recency rule of its own -- otherwise a reader could be told the work is one
        artifact while the round continues another.
        """
        if self.artifact_port is None:
            return None, {"selected_by": "NO_PORT", "artifact_id": self.work_artifact_id,
                          "bound_work_artifact_id": self.work_artifact_id}
        return self._read_resume_point()

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
        skipped_non_work: list[str] = []
        try:
            if artifact_id:
                point = self.artifact_port.resume_point(str(artifact_id))
            else:
                # No work bound and no admitted history: the *cycle's* fallback, which applies
                # this instance's own "these artifacts are not work" knowledge to the port's
                # recency rule (the generic ``latest_resumable`` semantics are untouched).
                point, skipped_non_work = self._latest_resumable_work()
                if skipped_non_work:
                    selection["latest_resumable_skipped_non_work"] = list(skipped_non_work)
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
        if point is not None and not point.resumable:
            # v0.4 C: a bound (or history-named) work the owner no longer reports as continuable
            # is not a step target.  ``_latest_resumable_work`` already filters this; without the
            # same check here, a finished/archived work kept producing a WORK step the owner
            # refused ("artifact is not active") on every round.  Nothing is retargeted --
            # silently continuing a *different* work would be a decision nobody made -- so this
            # round simply reports why it has no work to continue.
            selection.update({
                "selected_by": "NOT_RESUMABLE",
                "artifact_id": point.artifact_id,
                "head_version_id": point.head_version_id,
                "resumable": False,
                "lifecycle": point.lifecycle,
                "owner_verified": True,
                "detail": ("the owner reports this work as %s (head=%s); no step is planned"
                           % (point.lifecycle or "UNKNOWN", point.head_version_id or "NONE")),
            })
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

    # -- v0.4 C: the one real WAIT basis, its bounded upkeep, and the closeout seam --------

    #: How many LR-4 dependency conditions one tick may re-check (zero-model upkeep).
    MAX_WAIT_CONDITIONS_PER_TICK = 8
    #: How many satisfied observations one tick may hand to LR-4 (a bound, not a policy).
    MAX_WAIT_EVENTS_PER_TICK = 4

    def wait_basis(self, *, readiness: Any = None) -> Optional[dict[str, Any]]:
        """The one dependency of the *current* Activity this round may legally wait on.

        This is the whole of what the cycle is allowed to feed AG-1's
        ``known_waiting_basis_ref``.  It answers ``None`` unless **all** of the following hold:

        * the foreground Activity is ``ACTIVE`` (a PAUSED/WAITING one is resume's business, and
          the owner's own RESUME path already owns it -- see ``AG-1:2331-2351``);
        * its step readiness is positively ``False`` -- the owner said "no step right now" -- and
          the reason is one of the two named dependencies (``WORK_NO_CHANGE_CONFIRMED`` on the
          owner's current head, or ``READING_*`` on the material's current revision);
        * the baseline is the *owner's* revision this round read (``StepReadiness.source_revision``
          comes from ``_readiness_revision``), never a version this cycle remembered.

        Everything else -- a World step with no adapter, an UNKNOWN body cell, a spent budget --
        is a fact about this caller or this instant and is deliberately **not** turned into a
        wait.  "It might change later" is not a known dependency.
        """

        view = readiness if readiness is not None else self._round_readiness
        if view is None or not getattr(view, "bound", False):
            return None
        if getattr(view, "available", None) is not False:
            return None
        activity = self._last_foreground_record or {}
        if str(activity.get("status") or "") != "ACTIVE":
            return None
        state = None
        if str(getattr(view, "reason_code", "") or "").startswith(lsr.REASON_READING_PREFIX):
            situation = self.private_material_situation() or {}
            state = situation.get("state")
        basis = lwb.basis_from_readiness(view, state=state)
        self.last_wait_basis = basis
        return basis

    # -- v0.4 A (pacing): the body-conditioned breakpoint --------------------------------
    #: Why a body breakpoint was not offered this round (named, never "nothing to see").
    BODY_BREAKPOINT_NO_ACTIVITY = "NO_RUNNING_ACTIVITY"
    BODY_BREAKPOINT_NOT_READ = "BODY_NOT_READ"
    BODY_BREAKPOINT_NOT_LIMITED = "BODY_NOT_LIMITED"
    BODY_BREAKPOINT_NO_BURDEN = "BODY_HAS_NO_NAMED_BURDEN"

    def body_wait_basis(self, *, observed_at: Optional[str] = None) -> Optional[dict[str, Any]]:
        """The breakpoint this round may offer from a *body* fact -- or ``None``.

        "I put this down because my body is like this" is a dependency this tree can really
        check: ``life_wait_basis.body_capacity_basis`` takes the body identity, revision and the
        named burden signals out of **this round's** own body read (``read_situation`` ->
        ``life_body_context``), so the baseline is never a remembered value and a second body read
        is never paid.  The only other input is the Activity that currently holds the foreground:
        a breakpoint is something a *running* Activity takes, so a WAITING/PAUSED/absent Activity
        gets none (resume owns those, not a new wait).

        It is prompt-side and AG-1-side only: no Candidate, no permission, no rest order.  An
        UNKNOWN capacity, an unread body, a body with no named burden and no running Activity all
        answer ``None`` with a named reason in ``last_body_wait_reason``.
        """

        self.last_body_wait_basis = None
        self.last_body_wait_reason = None
        activity = self._last_foreground_record or {}
        if str(activity.get("status") or "") != "ACTIVE":
            self.last_body_wait_reason = self.BODY_BREAKPOINT_NO_ACTIVITY
            return None
        context = self.last_body_context
        if not isinstance(context, Mapping) or not context:
            self.last_body_wait_reason = self.BODY_BREAKPOINT_NOT_READ
            return None
        recovery = context.get("recovery") if isinstance(context.get("recovery"), Mapping) else {}
        progress = recovery.get("progress")
        if progress == lbc.RECOVERY_PROGRESS_NOT_READ:
            progress = None
        basis = lwb.body_capacity_basis(
            body_id=context.get("body_id"),
            revision=context.get("revision"),
            capacity=context.get("capacity"),
            burden=context.get("burden"),
            recovery_progress=progress,
            trend=recovery.get("trend"),
            session=recovery.get("session"),
            observed_at=context.get("observed_at") or observed_at,
            activity_ref=(activity.get("activity_ref") or activity.get("activity_id")),
        )
        if basis is None:
            if str(context.get("capacity") or "") != lbc.CAPACITY_LIMITED:
                self.last_body_wait_reason = self.BODY_BREAKPOINT_NOT_LIMITED
            else:
                self.last_body_wait_reason = self.BODY_BREAKPOINT_NO_BURDEN
            return None
        self.last_body_wait_basis = basis
        return basis

    def body_breakpoint_view(self) -> dict[str, Any]:
        """The bounded breakpoint block the decision reads (facts, never authority).

        When it is on offer it names *what* the Activity keeps (its own ref, and the material and
        page it stopped at) and *what it is waiting for* (the body dependency and the evidence it
        was bound to), plus the one legal move: choose ``WAIT``.  When it is not, the reason is a
        named token rather than an empty block.  ``not_recovery`` travels with the offer because
        an eased body is still not a claim that she recovered -- only a new Decision resumes.
        """

        basis = self.last_body_wait_basis
        if not isinstance(basis, Mapping):
            return {
                "schema_version": "chiyo.life.body_breakpoint.v1",
                "available": False,
                "why_not": (self.last_body_wait_reason
                            or self.BODY_BREAKPOINT_NOT_READ),
                "how": ("a breakpoint needs a running Activity and a body the owner itself reports "
                        "as LIMITED with a named burden signal"),
            }
        activity = self._last_foreground_record or {}
        situation = self.private_material_situation() or {}
        material = situation.get("material") if isinstance(
            situation.get("material"), Mapping) else {}
        position = situation.get("position") if isinstance(
            situation.get("position"), Mapping) else {}
        keeps: dict[str, Any] = {
            "activity_ref": (activity.get("activity_ref") or activity.get("activity_id")),
            "activity_kind": activity.get("activity_kind"),
        }
        if material:
            keeps["material_title"] = str(material.get("title") or "")[:120]
            keeps["page"] = position.get("page")
            keeps["next_page"] = position.get("next_page")
        if self.work_artifact_id:
            keeps["work_artifact_ref"] = str(self.work_artifact_id)[:96]
        return {
            "schema_version": "chiyo.life.body_breakpoint.v1",
            "available": True,
            "kind": lwb.SOURCE_BODY_REST,
            "activity_ref": keeps["activity_ref"],
            "keeps": keeps,
            "condition": {
                "waiting_on_ref": basis.get("waiting_on_ref"),
                "source": lwb.SOURCE_BODY_REST,
                "baseline": {
                    "body_ref": basis.get("body_ref"),
                    "capacity": basis.get("baseline_capacity"),
                    "burden": [dict(row) for row in (basis.get("baseline_burden") or [])],
                    "recovery_progress": basis.get("baseline_progress"),
                    "trend": basis.get("baseline_trend"),
                },
                "satisfied_by": ("a newer read of the same body: a higher recovery band, a named "
                                 "burden no longer reported (or lighter), or an improving trend"),
                "releases_foreground": True,
            },
            "how": ("choose WAIT: the Activity keeps its place (same material, same page) and the "
                    "slot is free for a real posture or rest step; coming back is a later RESUME "
                    "you have to make yourself"),
            "not_recovery": "an eased body is not a claim that she recovered",
        }

    def pause_condition_view(self, basis: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """The bounded condition a PAUSE may name this round -- or a named "none offered".

        It is the *same* basis the round already offers for WAIT (never a second source): a body
        the owner really reports as limited, a work/material whose owner owes a next version, or
        a time she herself named in this Decision.  The block says only what the condition is,
        that a PAUSE keeps the foreground, and that she has to choose RESUME later.  It grants
        nothing and creates nothing; without a basis ``available: false`` carries a reason code.
        """

        if not isinstance(basis, Mapping) or not str(basis.get("basis_ref") or ""):
            return {
                "schema_version": "chiyo.life.pause_condition.v1",
                "available": False,
                "why_not": "NO_OFFERED_CONDITION",
                "how": ("a conditional PAUSE needs a condition this round can really check; "
                        "without one PAUSE is still allowed, but it is unconditional and can "
                        "never become a resume candidate"),
            }
        source = str(basis.get("source") or "")
        if source == lwb.SOURCE_BODY_REST:
            satisfied_by = ("a newer read of the same body: a higher recovery band, a named "
                            "burden no longer reported (or lighter), or an improving trend")
        elif source == lfi.PARK_SOURCE:
            satisfied_by = ("the instant this Decision itself names in its later_item proposal; "
                            "there is no default timer")
        else:
            satisfied_by = ("the owner of the named work/material reporting a new head version "
                            "after the one recorded in the baseline")
        how = ("choose PAUSE and copy condition_ref into reevaluation_hint_ref: the Activity "
               "stays PAUSED and keeps the foreground, the owner alone decides when the "
               "condition holds, and a later Decision may choose RESUME")
        if source == lfi.PARK_SOURCE:
            how += ("; the instant comes from this same answer's later_item with "
                    "park_current=true -- there is no default timer")
        return {
            "schema_version": "chiyo.life.pause_condition.v1",
            "available": True,
            "condition_ref": str(basis["basis_ref"]),
            "source": source,
            "activity_ref": (basis.get("activity_ref")
                             or (self._last_foreground_record or {}).get("activity_ref")
                             or (self._last_foreground_record or {}).get("activity_id")),
            "waiting_on_ref": basis.get("waiting_on_ref"),
            "keeps_foreground": True,
            "satisfied_by": satisfied_by,
            "how": how,
            "not_automatic": ("an OPEN eligibility is only the right to ask; nothing resumes by "
                              "itself and no condition means no resume candidate"),
        }

    def _owner_head(self, artifact_ref: Any) -> Optional[str]:
        """The head the artifact owner reports *now*, or ``None`` when it cannot be read.

        One bounded read through the same port the round already uses.  An unavailable owner, a
        refusal, a revoked grant and an artifact with no head all answer ``None`` -- and ``None``
        can only ever leave a condition PENDING, never satisfy it.
        """

        if self.artifact_port is None:
            return None
        ref = str(artifact_ref or "")
        if not ref:
            return None
        try:
            reader = getattr(self.artifact_port, "read_head", None)
            head = reader(ref) if callable(reader) else None
            if isinstance(head, Mapping) and head.get("version_id"):
                return str(head["version_id"])
            point = self.artifact_port.resume_point(ref)
            return str(point.head_version_id) if point.head_version_id else None
        except Exception as exc:  # a refused/unreadable read is reported, never guessed
            self.last_error = ("owner head read failed for %s: %s: %s"
                               % (ref, type(exc).__name__, exc))
            return None

    def _pending_wait_conditions(self) -> list[dict[str, Any]]:
        """This module's own PENDING dependency conditions, read from LR-4's store (read-only).

        LR-4 owns them; this is a read.  Bounded by ``MAX_WAIT_CONDITIONS_PER_TICK`` and sorted
        by condition id so the same tick always looks at the same set.
        """

        store = getattr(self.runtime, "condition_store", None)
        loader = getattr(store, "load_state", None)
        if not callable(loader):
            return []
        try:
            state = loader() or {}
        except Exception as exc:
            self.last_error = "condition store read failed: %s: %s" % (type(exc).__name__, exc)
            return []
        rows = state.get("conditions") if isinstance(state, Mapping) else None
        if not isinstance(rows, Mapping):
            return []
        out: list[dict[str, Any]] = []
        for cid in sorted(rows):
            row = rows[cid]
            if not isinstance(row, Mapping):
                continue
            if str(row.get("status") or "") != "PENDING":
                continue
            # v0.4 A (pacing): the body-conditioned breakpoints this module binds are re-checked
            # on the same bounded upkeep -- still only conditions *this* module wrote (its own
            # parameter keys), never anything else's.
            if not (lwb.is_artifact_head_condition(row)
                    or lwb.is_body_capacity_condition(row)):
                continue
            out.append(dict(row))
            if len(out) >= self.MAX_WAIT_CONDITIONS_PER_TICK:
                break
        return out

    def _fresh_body_easing_read(self, observed_at: str) -> Optional[Mapping[str, Any]]:
        """One bounded, zero-model re-read of the body for a pending body breakpoint.

        It goes through the *same* seam a round uses (``read_situation`` -> the situation view's
        body column -> ``life_body_context``), so the evidence compared with the baseline has the
        same shape the baseline was taken from -- identity, revision, freshness, burden bands and
        the owner's own recovery band/trend.  One read per upkeep tick even when several body
        conditions are pending (the caller passes the result in), and a failure is ``None``: an
        unread body can never satisfy a condition.
        """

        if self.situation_source is None:
            return None
        try:
            self.read_situation(observed_at)
        except Exception as exc:  # noqa: BLE001 - a failed read is reported, never filled
            self.last_error = "BODY_EASING_READ_FAILED: %s: %s" % (type(exc).__name__, exc)
            return None
        context = self.last_body_context
        return dict(context) if isinstance(context, Mapping) and context else None

    def maintain_waiting(self, *, observed_at: Optional[str] = None) -> dict[str, Any]:
        """One bounded, zero-model upkeep step: due-agenda scan plus dependency re-check.

        Called from the runner's *existing* wake (``AutonomousLifeRunner.wake``), so it is not a
        timer of its own and it adds no schedule.  It reads the owners and, when a real
        observation really shows the awaited change, hands **one** event to LR-4, which alone may
        write SATISFIED and emit an OPEN eligibility.  It never resumes, never replays a Decision
        and never spends a request.
        """

        obs = observed_at or self._now_iso()
        report: dict[str, Any] = {
            "observed_at": obs, "state": "OK", "checked": 0, "unchanged": 0, "changed": 0,
            "evaluated": 0, "satisfied": [], "expired": [], "emitted_eligibilities": [],
            "due_agenda": None, "errors": [], "llm_calls": 0, "auto_resumed": False,
            #: v0.4 A (pacing): the body breakpoints re-read on this tick (counted apart from the
            #: artifact-head dependencies so neither report can be read as the other).
            "body_checked": 0, "body_changed": 0,
        }
        waiting = getattr(self.runtime, "waiting_coordinator", None)
        if waiting is None:
            report["state"] = "NOT_WIRED"
            report["errors"].append("NO_LR4_COORDINATOR")
            self.last_waiting_maintenance = dict(report)
            return report
        # 1. the owner's own due scan (time conditions / agenda items).  Zero model, zero
        #    auto-resume; it only lets LR-4 reach its own due/expired verdicts while the process
        #    is running instead of only at a cold start.
        scan = getattr(waiting, "scan_due_agenda", None)
        if callable(scan):
            try:
                scanned = scan(now_at=obs) or {}
                report["due_agenda"] = {
                    "due": list(scanned.get("due_agenda_ids") or []),
                    "expired": list(scanned.get("expired_agenda_ids") or []),
                    "emitted_eligibilities": len(scanned.get("emitted_eligibilities") or []),
                    "auto_resumed": bool(scanned.get("auto_resumed")),
                }
            except Exception as exc:
                report["errors"].append("SCAN_DUE_AGENDA:%s: %s"
                                        % (type(exc).__name__, str(exc)[:160]))
        else:
            report["errors"].append("SCAN_DUE_AGENDA_NOT_WIRED")
        # 2. the dependency conditions this module bound: re-read the owner, and only a really
        #    different head becomes an event.
        #    v0.4 A (pacing): the *body* breakpoints go through the same upkeep and the same
        #    rule -- one read of the body owner, an event only when that read is newer, is of the
        #    same body and really shows easing.  Neither path resumes anything: LR-4 alone decides
        #    SATISFIED, and a new Decision is still needed to come back.
        events: list[tuple[dict[str, Any], dict[str, Any]]] = []
        body_read: Any = None
        for condition in self._pending_wait_conditions():
            if lwb.is_body_capacity_condition(condition):
                basis = lwb.body_basis_from_condition(condition)
                if basis is None:
                    continue
                report["body_checked"] += 1
                if body_read is None:
                    body_read = self._fresh_body_easing_read(obs)
                if not isinstance(body_read, Mapping) or not body_read:
                    report["errors"].append("BODY_READ_UNAVAILABLE")
                    continue
                recovery = (body_read.get("recovery")
                            if isinstance(body_read.get("recovery"), Mapping) else {})
                event = lwb.body_easing_event(
                    basis,
                    observed_body_id=body_read.get("body_id"),
                    observed_revision=body_read.get("revision"),
                    burden=body_read.get("burden"),
                    capacity=body_read.get("capacity"),
                    recovery_progress=recovery.get("progress"),
                    trend=recovery.get("trend"),
                    freshness=body_read.get("freshness"),
                    observed_at=obs,
                )
                if event is None:
                    report["unchanged"] += 1
                    continue
                report["changed"] += 1
                report["body_changed"] += 1
                if len(events) < self.MAX_WAIT_EVENTS_PER_TICK:
                    events.append((basis, event))
                continue
            basis = lwb.basis_from_condition(condition)
            if basis is None:
                continue
            report["checked"] += 1
            head = self._owner_head(basis.get("artifact_ref"))
            if not head:
                report["errors"].append("OWNER_HEAD_UNREADABLE:%s" % (basis.get("artifact_ref"),))
                continue
            event = lwb.head_change_event(basis, current_head=head, observed_at=obs)
            if event is None:
                report["unchanged"] += 1
                continue
            report["changed"] += 1
            if len(events) < self.MAX_WAIT_EVENTS_PER_TICK:
                events.append((basis, event))
        evaluate = getattr(waiting, "evaluate_event", None)
        for basis, event in events:
            if not callable(evaluate):
                report["errors"].append("EVALUATE_EVENT_NOT_WIRED")
                break
            try:
                outcome = evaluate(event) or {}
            except Exception as exc:
                report["errors"].append("EVALUATE_EVENT:%s: %s"
                                        % (type(exc).__name__, str(exc)[:160]))
                continue
            report["evaluated"] += 1
            report["satisfied"].extend(list(outcome.get("satisfied_condition_ids") or []))
            report["expired"].extend(list(outcome.get("expired_condition_ids") or []))
            report["emitted_eligibilities"].extend(
                [str((row or {}).get("eligibility_id") or "")
                 for row in (outcome.get("emitted_eligibilities") or [])])
        report["events"] = [event.get("event_id") for _basis, event in events]
        self.last_waiting_maintenance = dict(report)
        return report

    # -- v0.4 C: the closeout seam (W's pure plan; inert until that module is delivered) ---

    def closeout_plan(self) -> Any:
        """W's ``artifact_closeout`` plan module, or ``None`` while it is not delivered.

        Nothing half-written is imported: the module is looked up through
        ``closeout_module()`` and accepted only when *all four* contract functions exist.  While
        it is missing, no closeout candidate is offered anywhere, so a half-integrated plan can
        never make a round archive something.
        """

        if self.closeout_plan_module is not None:
            return self.closeout_plan_module
        module = closeout_module()
        if module is None:
            return None
        if not all(callable(getattr(module, name, None)) for name in CLOSEOUT_REQUIRED_FUNCTIONS):
            return None
        return module

    def closeout_target_ids(self) -> set[str]:
        """The bare artifact ids the candidate source may offer a wrap-up for (report-safe).

        Read-only: it asks the owner once and answers the source's question.  An unavailable or
        refused owner yields an empty set, which simply means "no closeout on offer this round".
        """

        point = self._closeout_point()
        if not point or point.get("state") != "READY":
            return set()
        # The source matches on the *bare* id (it strips the owner's own ``artifact:`` prefix), so
        # the seam speaks the same spelling as the resume point it came from.
        bare = str(point.get("bare_id") or "")
        return {bare} if bare else set()

    def closeout_state(self) -> dict[str, Any]:
        """Report-only: is the closeout plan integrated, and if not, exactly why not."""

        if self.closeout_plan() is None:
            return {"state": "NOT_AVAILABLE", "reason": CLOSEOUT_NOT_DELIVERED,
                    "expected_module": "artifact_closeout",
                    "required_functions": list(CLOSEOUT_REQUIRED_FUNCTIONS)}
        return {"state": "READY", "activity_kind": CLOSEOUT_ACTIVITY_KIND,
                "target_namespace": CLOSEOUT_TARGET_NAMESPACE}

    def _closeout_point(self) -> Optional[dict[str, Any]]:
        """The one bound work this round may *offer* a closeout for, or ``None``.

        Rules (all of them, or nothing):

        * the closeout plan really exists (see ``closeout_plan``);
        * an artifact is explicitly bound as this instance's work -- never a recency pick, so a
          material or a private-items document can never be closed out "by accident";
        * the foreground slot is free: while an Activity is *executing* this round does not
          quietly switch her away from it.  A PAUSED Activity released by the v0.4 background
          pause does not hold the slot, so it does not block this candidate;
        * the owner reports the artifact ``ACTIVE`` (an already archived one is not a candidate)
          and publishes its own ``revision`` -- W's step needs it, and a substituted "current"
          revision would archive a work the Decision was not about.
        """

        module = self.closeout_plan()
        if module is None or self.artifact_port is None:
            return None
        if self._foreground_activity_ref():
            return None
        bound = self._artifact_identity(self.work_artifact_id) if self.work_artifact_id else None
        if not bound:
            return None
        if self.work_artifact_id in self.artifact_exclusions:
            return None
        try:
            point = self.artifact_port.resume_point(str(self.work_artifact_id))
        except Exception as exc:
            self.last_error = ("closeout owner read failed: %s: %s"
                               % (type(exc).__name__, exc))
            return None
        owner_id = str(point.artifact_id or "")
        if not owner_id:
            return None
        if str(point.lifecycle or "") != "ACTIVE":
            return {"artifact_id": owner_id, "state": "NOT_ACTIVE", "lifecycle": point.lifecycle,
                    "reason": "ONLY_AN_ACTIVE_WORK_CAN_BE_CLOSED_OUT"}
        revision = int(point.revision or 0)
        if revision < 1:
            return {"artifact_id": owner_id, "state": "NO_OWNER_REVISION",
                    "reason": "OWNER_REVISION_UNREADABLE"}
        # W owns the target namespace: the owner's own id is kept **verbatim** (a real id already
        # begins with ``artifact:``, so the target reads ``closeout:artifact:artifact:<uuid>``).
        target_ref = str(module.closeout_target(owner_id))
        parsed = module.parse_closeout_target(target_ref) or {}
        if str(parsed.get("owner_artifact_id") or "") != owner_id:
            return None
        return {
            "state": "READY",
            "artifact_id": owner_id,
            "bare_id": str(self._artifact_identity(owner_id) or bound),
            "artifact_ref": owner_id,
            "target_ref": target_ref,
            "expected_revision": revision,
            "head_version_ref": point.head_version_id,
            "version_number": point.version_number,
            "title": point.title,
            "lifecycle": point.lifecycle,
        }

    def _closeout_adoption_spec(self, point: Mapping[str, Any]) -> dict[str, Any]:
        """AG-2's Activity shape for one closeout, from W's pure plan (never rebuilt here)."""

        module = self.closeout_plan()
        if module is None:
            raise AutonomousLifeError(CLOSEOUT_NOT_DELIVERED)
        spec = module.build_closeout_adoption_spec(
            str(point.get("artifact_id")),
            title=point.get("title") or None)
        data = dict(spec or {})
        built = ag2.ActivityAdoptionSpec.from_dict(data).to_dict()
        if str(built.get("activity_kind") or "") != CLOSEOUT_ACTIVITY_KIND:
            raise AutonomousLifeError("closeout adoption spec names activity_kind=%r"
                                      % (built.get("activity_kind"),))
        if str(point.get("target_ref")) not in [str(r) for r in built.get("target_refs") or []]:
            raise AutonomousLifeError("closeout adoption spec does not target the closeout ref")
        return built

    def _closeout_step_spec(self, point: Mapping[str, Any], *, decision_record: Any,
                            observed_at: str) -> dict[str, Any]:
        """AR-0's one closeout step, from W's pure plan.  It must name the closeout target."""

        module = self.closeout_plan()
        if module is None:
            raise AutonomousLifeError(CLOSEOUT_NOT_DELIVERED)
        # W's key needs a stable "which activity" component.  At START time the Activity does not
        # exist yet (AG-2 mints it and fills its own identity fields into the step), so the
        # committed Decision's own candidate stands in for it: one candidate is one closeout.
        activity_ref = str(getattr(decision_record, "target_activity_ref", "") or
                           getattr(decision_record, "selected_candidate_ref", "") or
                           point.get("target_ref") or "")
        # No correlation id is passed: AG-2 mints its own *inside* this call, after the step spec
        # is materialised, and W's key deliberately does not depend on the conversation.
        step = module.build_closeout_step(
            str(point.get("artifact_id")),
            int(point.get("expected_revision") or 0),
            activity_ref=activity_ref,
            grant_id=str(self.artifact_grant_id) if self.artifact_grant_id else None)
        data = dict(step or {})
        for key in ("step_kind", "action_kind", "target_ref", "payload_spec"):
            if not data.get(key):
                raise AutonomousLifeError("closeout step is missing %s" % key)
        if str(data.get("target_ref")) != str(point.get("target_ref")):
            raise AutonomousLifeError("closeout step does not target the closeout ref")
        if str((data.get("payload_spec") or {}).get("op")) != "archive_artifact":
            raise AutonomousLifeError("closeout step is not an archive operation")
        return data

    def _decision_chose_closeout(self, decision_record: Any) -> bool:
        """Did the committed Decision select the closeout candidate of this round?

        An **instance** method on purpose: it resolves the selected candidate through
        ``self._selected_candidate`` (the one reader of ``runtime.candidate_read``), so it must be
        passed as a bound callable -- ``is_closeout=self._decision_chose_closeout`` -- exactly like
        ``_decision_chose_world``.  A ``@staticmethod`` calling
        ``AutonomousLifeCycle._selected_candidate(record)`` was the gen1 defect: that attribute is a
        plain function on the class, so the record went to ``self`` and the call raised
        ``TypeError: missing 1 required positional argument: 'decision_record'`` inside the router.
        """

        candidate = self._selected_candidate(decision_record)
        if not isinstance(candidate, Mapping):
            return False
        return any(str(ref).startswith(CLOSEOUT_TARGET_NAMESPACE)
                   for ref in (candidate.get("target_refs") or []))

    # -- D (life-v1): the reading action ---------------------------------------------------

    def reading_source_id(self) -> Optional[str]:
        """The private-items ``source_id`` for this instance's reading material."""
        if not self.reading_artifact_id:
            return None
        return ("artifact:%s" % self.reading_artifact_id
                if not self.reading_artifact_id.startswith("artifact:")
                else self.reading_artifact_id)

    # -- v0.4 wake cost: one round, one owner read --------------------------------

    def begin_round_reads(self, observed_at: Any = None) -> None:
        """Open this round's read memo (a no-op for ports that do not cache).

        Every round starts empty.  Even when two wakes carry the same observed
        instant, the previous round's answers are not reused: the owner may have
        moved between them.
        """

        begin = getattr(self.artifact_port, "begin_round", None)
        if callable(begin):
            begin(observed_at)

    def end_round_reads(self) -> None:
        """Drop this round's read memo; the next round starts from the owners."""

        end = getattr(self.artifact_port, "end_round", None)
        if callable(end):
            end()

    def _invalidate_owner_reads(self) -> None:
        """Forget cached owner reads before a deliberate fresh re-check."""

        invalidate = getattr(self.artifact_port, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def private_material_situation(self, *, refresh: bool = False) -> Optional[dict[str, Any]]:
        """The read-only situation of this instance's own materials, or ``None`` if unbound.

        One read per round (cached, and ``refresh=True`` re-reads the owners) so the answer a
        candidate was built from and the answer a step plans from cannot silently disagree
        within one round.  It reports; it never decides and never writes.
        """
        if not (self.artifact_port and self.reading_artifact_id
                and self.private_items_artifact_id):
            return None
        if self._reading_situation_cache is not None and not refresh:
            return dict(self._reading_situation_cache)
        if refresh:
            # ``refresh=True`` is this module's contract for "ask the owners again";
            # that also means the round's read memo cannot answer this one.
            self._invalidate_owner_reads()
            self._reading_situation_cache = None
        situation = private_materials_module().read_reading_situation(
            self.artifact_port,
            reading_artifact_id=self.reading_artifact_id,
            private_items_artifact_id=self.private_items_artifact_id)
        self._reading_situation_cache = dict(situation)
        self.last_reading_situation = dict(situation)
        return dict(situation)

    def private_material_facts(self, *, refresh: bool = False) -> dict[str, Any]:
        """The bounded ASCII fact keys for that situation (``observed_facts`` channel)."""
        situation = self.private_material_situation(refresh=refresh)
        if situation is None:
            return {}
        return private_materials_module().reading_fact_keys(situation)

    def reading_available(self) -> bool:
        """Is there a page left to read on the material's **current** revision?

        Bound is not the same as available: an exhausted material, a revoked source, an empty
        material, an out-of-range stop and an unreadable private-items document are all
        reported situations in which no reading step may be planned.  With no situation reader
        wired at all (a bare cycle with only a port) the historical "bound means available"
        answer is kept unchanged.
        """
        if not (self.artifact_port and self.reading_artifact_id
                and self.private_items_artifact_id):
            return False
        situation = self.private_material_situation()
        if situation is None:
            return True
        return bool(situation.get("available"))

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

        The page comes from the owners' own records: the material's **current** head revision,
        and the *latest valid stop* on that revision (a position recorded on that revision, whose
        acquisition really exists for it and was not revoked).  With no such stop -- a fresh
        material, or a material that changed since the last read -- reading starts at page 1;
        an older revision's highest page never drives a newer revision.  A finished material has
        no next page at all, so no page is invented and the last page is not read again.

        The bookmark is planned purely (``private_daily_items.plan_page_read``) and committed by
        the owner as one compare-and-swap version of the private-items document.  The step is
        therefore a real AR-0 file action with a real receipt -- and the same operation key means
        a retry can never mint a second version.

        When a private response writer is wired, the *same* page also gets at most one private
        annotation (A's ``ModelReadingResponse`` decides note / unclear / no_change, and A's pure
        ``plan_page_annotation`` turns that answer into document bytes).  Acquisition, stop and
        annotation therefore travel in **one** compare-and-swap of one document version, stamped
        with the *one* version number the owner will really create; the page text only ever
        enters that private request, and a failed response writes nothing at all rather than half
        a step.  A blank page is never sent to the content writer -- there is no text to answer
        about -- but its acquisition and stop are still saved and the round reports
        ``SKIPPED_EMPTY``.  Because the request is a real model wait and the material and the
        private-items document are two different owners, both heads are re-read afterwards: a
        material revision, a document head or a source revocation that moved in the meantime
        means nothing is written.  With no writer wired the previous behaviour is unchanged and
        the round reports ``NOT_CONFIGURED``.
        """
        import private_daily_items as pdi

        if not (self.artifact_port and self.reading_artifact_id
                and self.private_items_artifact_id):
            raise AutonomousLifeError("no reading material bound to this instance")
        reading_id = str(self.reading_artifact_id)
        # A page read commits the private-items document, but the progress (and the memory
        # handoff) belongs to the *reading material*: the AR-0 payload is whitelisted by the
        # owner adapter, so the target is carried here instead of inside the payload.
        self.last_step_progress_target_ref = reading_id
        doc_id = str(self.private_items_artifact_id)
        situation = self.private_material_situation(refresh=True) or {}
        state = str(situation.get("state") or "")
        if not situation.get("available"):
            # Fail closed with the situation's own words: exhausted, revoked, empty,
            # out-of-range and unreadable are different answers and none of them is a page.
            raise AutonomousLifeError(
                "no page to read in the reading material: %s (%s)"
                % (state or "UNKNOWN", str(situation.get("detail") or "")[:120]))
        material = situation.get("material") or {}
        position = situation.get("position") or {}
        page = position.get("next_page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise AutonomousLifeError("the reading situation named no next page")
        reading = self.artifact_port.read_artifact(reading_id)
        reading_head = dict(reading.get("head") or {})
        content = reading.get("content")
        if not isinstance(content, str) or not content:
            raise AutonomousLifeError("the reading material has no content to read")
        revision = str(reading_head.get("version_id") or "")
        if not revision:
            raise AutonomousLifeError("the reading material has no committed version")
        if str(material.get("version_id") or "") != revision:
            # The material moved between the situation read and this read: the plan is stale,
            # not wrong -- nothing is read and nothing is written this round.
            raise AutonomousLifeError(
                "the reading material changed while planning the page: %s -> %s"
                % (str(material.get("version_id") or "")[:40], revision[:40]))
        pages = [item for item in content.split(READING_PAGE_MARKER)]
        if page > len(pages):
            raise AutonomousLifeError(
                "no unread page left in the reading material: page %d of %d" % (page, len(pages)))
        text = pages[page - 1]
        document_view = self.artifact_port.read_artifact(doc_id)
        doc_head = dict(document_view.get("head") or {})
        document = pdi.decode_document(document_view.get("content") or "")
        source_id = self.reading_source_id()
        # The stop this page continues: the situation's own answer, reported beside the step.
        planned = pdi.plan_page_read(
            document, artifact_id=doc_id, source_id=source_id, source_revision=revision,
            receipt_ref="read:%s:%s:page-%s" % (reading_id, revision, page),
            obtained={"pages": [page], "chars": len(text)}, excerpt=text[:200],
            page=page, grant_ref=self.artifact_grant_id,
            head_version_id=doc_head.get("version_id"),
            head_number=doc_head.get("version_number"), now=observed_at)
        # rc1-v04: the optional private annotation for *this* page.  It is planned from the
        # document the page read just planned, so the annotation lands in the same version --
        # and it is planned *after* the read so it can see the acquisition it belongs to.
        # A blank page is not a page to talk about: A's own parser refuses empty text
        # (``READING_TEXT_REQUIRED``) and there is nothing private to send, so the content
        # request is *skipped*.  The acquisition and the stop are still real and are still
        # committed -- an empty page she really reached is a real stop, not a missing step.
        # No writer wired is *not* "the page was blank": with no writer the round keeps its old
        # behaviour exactly and reports ``NOT_CONFIGURED`` (``_reading_response_plan`` answers
        # ``None`` for that one reason), so the blank-page skip only applies to a wired writer.
        requested = bool(text.strip()) and self.reading_response_writer is not None
        if self.reading_response_writer is not None and not text.strip():
            annotation: Optional[dict[str, Any]] = {
                "state": "SKIPPED_EMPTY", "kind": None, "item_id": None, "changed": False,
                "content": None, "operation_key": None}
        else:
            annotation = self._reading_response_plan(
                document=pdi.decode_document(planned["content"]), artifact_id=doc_id,
                source_id=source_id, revision=revision, page=page, text=text,
                pages=len(pages), previous_page=position.get("page"), observed_at=observed_at)
        if requested:
            # The private request is a model wait of up to ``timeout_s``.  The material and the
            # private-items document have two *different* owners, so no transaction spans them;
            # rather than pretend one exists, both are re-read here and the step is refused --
            # nothing written, not half of it -- if either moved while the request was in flight.
            self._recheck_reading_sources(
                reading_id=reading_id, revision=revision, doc_id=doc_id,
                doc_head_version=doc_head.get("version_id"), source_id=source_id,
                before=document)
        commit_content = planned["content"]
        operation_key = planned["operation_key"]
        if annotation is None:
            annotation_view: dict[str, Any] = {
                "state": "NOT_CONFIGURED", "kind": None, "item_id": None, "applied": False}
        else:
            annotation_view = {"state": annotation["state"], "kind": annotation["kind"],
                               "item_id": annotation["item_id"],
                               "applied": bool(annotation["changed"])}
            if annotation["changed"]:
                planned_content = annotation.get("content")
                if not isinstance(planned_content, str) or not planned_content:
                    raise ReadingResponseFailed(
                        "the page annotation claimed a change but planned no document bytes")
                # One intended effect, one version: ``plan_page_read`` already stamped the
                # document with the owner's next number and the annotation stamped it once more,
                # so the merged bytes are re-stamped to the *single* number the owner will really
                # create.  Committing +2 would make the document lie about its own version.
                commit_content = self._rebase_merged_version(
                    planned_content,
                    owner_version_number=int(doc_head.get("version_number") or 0) + 1)
                # One intended effect, one logical action: the read *and* its annotation were
                # decided together, so the key covers both -- including *what* was intended, so a
                # different answer about the same page is a different logical action instead of a
                # silent retry.  A different intent on the same page is still refused by the
                # private-items planner, not quietly written under a new key.
                operation_key = operation_key_for(
                    str(operation_key), "page_annotation",
                    str(annotation.get("operation_key") or annotation.get("item_id") or ""),
                    str(annotation.get("intent") or ""))
        # Never the text: the report carries the *shape* of the annotation, not the note.
        self.last_reading_annotation = dict(annotation_view)
        self.last_reading_plan = {
            "reading_artifact_id": reading_id, "private_items_artifact_id": doc_id,
            "source_id": source_id, "source_revision": revision, "page": page,
            "pages": len(pages), "obtained": {"pages": [page], "chars": len(text)},
            "acquisition_id": planned["acquisition_id"],
            "position_id": planned["position_id"], "changed": planned["changed"],
            "understanding": "UNKNOWN",
            # Where the page came from: the latest valid stop on *this* revision (or page 1
            # when there is none), plus the situation that said so.  Report only.
            "planned_from": ("NO_STOP_ON_CURRENT_REVISION" if position.get("page") is None
                             else "LATEST_STOP_ON_CURRENT_REVISION"),
            "previous_page": position.get("page"),
            "reading_state": state,
            "needs_reread": bool(position.get("needs_reread")),
            "annotation": dict(annotation_view),
        }
        return {
            "step_kind": ag2.STEP_KIND_FILE_ACTION,
            "action_kind": ACTION_KIND_FILE,
            "target_ref": "artifact:%s" % doc_id,
            "payload_spec": {
                "op": OP_COMMIT_VERSION,
                "artifact_id": doc_id,
                "expected_head": doc_head.get("version_id"),
                "content": commit_content,
                "grant_id": str(self.artifact_grant_id),
                # The progress/experience this step reports belongs to the *reading material*,
                # not to the document the bytes happen to live in.
                "life_progress_target_ref": ("artifact:%s" % reading_id
                                             if not reading_id.startswith("artifact:")
                                             else reading_id),
            },
            "expected_effect_predicates": ["artifact_written_verified"],
            "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
            "idempotency_key": operation_key,
        }

    def _reading_response_plan(self, *, document: Mapping[str, Any], artifact_id: str,
                               source_id: str, revision: str, page: int, text: str,
                               pages: int, previous_page: Any,
                               observed_at: str) -> Optional[dict[str, Any]]:
        """The optional private note/question for one page, planned purely -- or ``None``.

        ``None`` means exactly one thing: no response writer is wired, so this instance keeps its
        previous behaviour (acquisition + stop only) and the round reports ``NOT_CONFIGURED``.

        The page text enters this request and nothing else: it is not put into the round report,
        the decision facts, the workspace facts or a log line.  Only the *shape* of the answer
        (kind / item id / whether it changed) is reported afterwards.

        Everything else fails closed.  A response that cannot be produced, that is not one of the
        three legal kinds, or whose pure plan cannot be built, raises
        :class:`ReadingResponseFailed` -- and because that happens *before* the step is handed to
        the owner, the page read is not committed either.  A pause and a request-budget refusal
        are not swallowed here: they keep their own honest outcomes.
        """
        writer = self.reading_response_writer
        if writer is None:
            return None
        import private_daily_items as pdi

        planner = getattr(pdi, "plan_page_annotation", None)
        if not callable(planner):
            # Wired writer, unwired planner: refusing is the honest answer.  Dropping the note
            # silently would report a read the model did annotate as if it had not.
            raise ReadingResponseFailed(
                "the pure page-annotation planner is not wired "
                "(private_daily_items.plan_page_annotation)")
        context = {
            "source_id": source_id,
            "source_revision": revision,
            "page": page,
            "text": text,
            "previous_page": previous_page,
            "total_pages": pages,
        }
        try:
            response = writer.respond(dict(context), observed_at=observed_at)
        except (life_pause.PauseConfirmedError, life_pause.RequestNotSentError):
            raise
        except life_request_budget.RequestBudgetError:
            raise
        except Exception as exc:
            raise ReadingResponseFailed(
                "the private reading response failed: %s" % _error_label(exc)) from exc
        # A real writing wait elapsed.  Whatever was read before it is no longer
        # proof of the owners' state; the following re-check must hit them again.
        self._invalidate_owner_reads()
        self._reading_situation_cache = None
        if not isinstance(response, Mapping):
            raise ReadingResponseFailed("the reading response is not a mapping")
        kind = str(response.get("kind") or "")
        if kind not in {"note", "unclear", "no_change"}:
            raise ReadingResponseFailed(
                "the reading response kind is not note/unclear/no_change: %r" % kind[:40])
        # The boundary: a real ``ModelReadingResponse.respond`` also returns its own source
        # metadata (``source_id`` / ``source_revision`` / ``page`` / ``observed_at`` /
        # ``prompt_chars`` / ``page_chars`` / ``page_text_truncated``), while A's pure planner
        # accepts exactly ``kind``/``title``/``body``/``note`` (plus the tolerated ``self_note``,
        # which may hold private text and is dropped here).  Handing the whole mapping over would
        # be refused as an unknown field, so a real answer could never land; the metadata is
        # therefore *checked* instead of forwarded, and only the semantic fields travel on.
        self._check_response_metadata(response, source_id=source_id, revision=revision,
                                      page=page)
        if kind == "no_change":
            # Nothing to annotate: the page read *is* the whole intent, so the pure planner is
            # not asked to plan an empty annotation, and the commit carries the read alone.
            return {"state": "NO_CHANGE", "kind": kind, "item_id": None, "changed": False,
                    "content": None, "operation_key": None, "intent": None}
        semantic = {name: response[name] for name in RESPONSE_ANNOTATION_FIELDS
                    if name in response}
        intent = _response_intent(semantic)
        # The annotation's identity is the page slot, never the attempt: the same page of the same
        # revision is one slot, so the same intent replays as one no-op and a different intent is
        # refused by the planner (one annotation per page, never two).
        annotation_key = operation_key_for(
            "page_annotation", str(artifact_id), source_id, revision, "page-%s" % page)
        try:
            plan = planner(document, artifact_id=str(artifact_id), source_id=source_id,
                           source_revision=revision, page=page, response=semantic,
                           operation_key=annotation_key, now=observed_at)
        except Exception as exc:
            raise ReadingResponseFailed(
                "the page annotation could not be planned: %s" % _error_label(exc)) from exc
        if not isinstance(plan, Mapping):
            raise ReadingResponseFailed("the page annotation plan is not a mapping")
        changed = bool(plan.get("changed"))
        return {"state": "APPLIED" if changed else "UNCHANGED", "kind": kind,
                "item_id": plan.get("item_id"), "changed": changed,
                "content": plan.get("content"),
                "operation_key": plan.get("operation_key") or annotation_key,
                "intent": intent}

    @staticmethod
    def _check_response_metadata(response: Mapping[str, Any], *, source_id: str,
                                 revision: str, page: Any) -> None:
        """Refuse a response whose own source metadata contradicts the page it answers.

        The metadata is not forwarded to the planner (it would be an unknown field there), so
        this is the only place the answer is held against the page it claims to be about: a
        response naming another source, another revision or another page is refused outright
        rather than annotated onto this one.  Only the field *name* travels into the error --
        the answer itself may quote private material.
        """
        for name, expected in (("source_id", source_id), ("source_revision", revision),
                               ("page", page)):
            if name not in response:
                continue
            actual = response.get(name)
            if isinstance(actual, bool) or str(actual) != str(expected):
                raise ReadingResponseFailed(
                    "the reading response contradicts the page it answers: %s" % name)

    def _recheck_reading_sources(self, *, reading_id: str, revision: str, doc_id: str,
                                 doc_head_version: Any, source_id: str,
                                 before: Mapping[str, Any]) -> None:
        """Both owners are re-read after the private request; a move means *no write*.

        There is no transaction spanning the reading material and the private-items document --
        two owners, two compare-and-swaps -- so this does not pretend to be one.  Instead it
        re-reads both heads and the availability of the acquisitions this source already had,
        and refuses the step outright (nothing written, not half of it) when

        * the material's head moved: the page text the answer is about is no longer the page the
          owner holds, so the annotation would be pinned to the wrong bytes;
        * the private-items head moved: the plan was built on a version that no longer exists,
          and the caller's own ``expected_head`` would be a guess;
        * or an acquisition of this source/revision was revoked while the request was in flight:
          the material she was reading is no longer hers to annotate.
        """
        import private_daily_items as pdi

        # The private request is a real wait: the material and the private-items
        # document may have moved while it was in flight.  The round's read memo
        # must not answer this re-check, and the situation cached before the wait
        # is no longer evidence about the owners.
        self._invalidate_owner_reads()
        self._reading_situation_cache = None
        current = self.artifact_port.read_artifact(reading_id)
        now_revision = str((current.get("head") or {}).get("version_id") or "")
        if now_revision != str(revision):
            raise ReadingResponseFailed(
                "the material changed during the private response: %s -> %s"
                % (str(revision)[:24], now_revision[:24]))
        view = self.artifact_port.read_artifact(doc_id)
        after = pdi.decode_document(view.get("content") or "")
        # The revocation is checked *before* the head, so a round refused for a revoked source
        # says so instead of hiding behind "the document moved" (a revocation is itself a write).
        revoked = self._revoked_acquisitions(before, after, source_id=source_id,
                                             revision=revision)
        if revoked:
            raise ReadingResponseFailed(
                "the source was revoked during the private response: %d acquisition(s)"
                % len(revoked))
        now_head = str((view.get("head") or {}).get("version_id") or "")
        if now_head != str(doc_head_version or ""):
            raise ReadingResponseFailed(
                "the private-items document changed during the private response: %s"
                % now_head[:24])

    @staticmethod
    def _revoked_acquisitions(before: Mapping[str, Any], after: Mapping[str, Any], *,
                              source_id: str, revision: str) -> list[str]:
        """Acquisitions of this source/revision that were available and no longer are."""
        import private_daily_items as pdi

        def _availability(document: Mapping[str, Any]) -> dict[str, bool]:
            items = document.get("items") if isinstance(document, Mapping) else None
            out: dict[str, bool] = {}
            for item_id, item in (items or {}).items():
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("item_type") or "") != pdi.ITEM_SOURCE_ACQUISITION:
                    continue
                if str(item.get("source_id") or "") != str(source_id):
                    continue
                if str(item.get("source_revision") or "") != str(revision):
                    continue
                out[str(item_id)] = bool(item.get("available", True))
            return out

        was = _availability(before)
        now = _availability(after)
        return sorted(item_id for item_id, available in was.items()
                      if available and not now.get(item_id, False))

    @staticmethod
    def _rebase_merged_version(content: str, *, owner_version_number: int) -> str:
        """Put a merged document back on the owner's *single* next version number.

        ``plan_page_read`` stamps the document with the owner's next number (+1) and
        ``plan_page_annotation`` stamps whatever document it is handed (+1 again), so planning
        both from the same base lands on +2 while the owner will really create +1: committing
        those bytes would make the document lie about its own version.  Only the two version
        fields are rewritten here; every item, key and timestamp stays exactly as the planners
        wrote it.
        """
        import private_daily_items as pdi

        document = pdi.decode_document(content)
        target = int(owner_version_number)
        if (int(document.get("owner_version_number") or 0) == target
                and int(document.get("doc_revision") or 0) == target):
            return content
        document["owner_version_number"] = target
        document["doc_revision"] = target
        return pdi.encode_document(document)

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
            activity_kind=READING_ACTIVITY_KIND,
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
    def _foreground_reading_activity(self) -> Optional[dict[str, Any]]:
        """The foreground Activity when -- and only when -- it is *this* material's page read.

        Read from the Activity owner itself.  Three things must be true, and a missing one is
        answered with ``None`` rather than by assuming: the foreground Activity's kind is the
        page read, its status is still ``ACTIVE``, and its own refs name the reading material
        this cycle is bound to.  A foreign foreground Activity is therefore never treated as a
        reading continuation.
        """
        if not self.reading_artifact_id:
            return None
        try:
            view = dict(self.runtime.activity_read.get_current_life_view() or {})
        except Exception as exc:  # a read failure is reported, never guessed
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return None
        if str(view.get("foreground_activity_kind") or "") != READING_ACTIVITY_KIND:
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
        if str(record.get("activity_kind") or READING_ACTIVITY_KIND) != READING_ACTIVITY_KIND:
            return None
        wanted = self._artifact_identity(self.reading_artifact_id)
        if not wanted:
            return None
        refs = list(record.get("subject_refs") or [])
        if record.get("target_ref"):
            refs.append(record.get("target_ref"))
        if record.get("checkpoint_ref"):
            refs.append(record.get("checkpoint_ref"))
        if not any(self._artifact_identity(item) == wanted for item in refs):
            return None
        return dict(record)

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
        #: The body situation is derived from *this* read (never a second body read): the body
        #: column the situation view already obtained, plus the World owner's own REST entry.
        self.last_body_context = self.body_decision_context(view)
        return view

    def body_decision_context(self, situation: Optional[Mapping[str, Any]] = None,
                              *, observed_at: Optional[str] = None) -> dict[str, Any]:
        """The bounded *qualitative* body situation a decision may read -- data, never authority.

        It answers four things the id-and-capacity answer could not: which burden signals the
        Body owner really reported (type + band), whether a recovery **session** is open right
        now, whether the owner says resting is possible *now* (and its own reason if not), and
        what could not be read.  Two boundaries are the point of the module:

        * it performs **no read**: the body column comes from the situation read this round
          already made (``read_situation`` -> ``life_situation_view`` -> one real body read), so
          a round cannot pay twice or disagree with itself;
        * a session is **not** recovery.  ``life_body_context`` always reports
          ``recovery.claim = NOT_ASSERTED``; nothing here may be quoted as "she recovered".

        ``None``/unreadable input is a named UNKNOWN (``BODY_SITUATION_NOT_READ``), never a
        healthy body and never a resting one.
        """

        view = self.last_situation if situation is None else situation
        column: Optional[Mapping[str, Any]] = None
        observed: Optional[str] = None
        if isinstance(view, Mapping):
            body = view.get("body")
            if isinstance(body, Mapping):
                column = body
            value = view.get("observed_at")
            observed = str(value) if value else None
        context = lbc.build(
            body_column=column,
            rest=self._world_rest_availability(),
            recovery=self._body_recovery_facts(),
            observed_at=observed_at or observed,
        )
        self.last_body_context = context
        return context
    # -- v0.4 sharing: what is open, what could be shared from, and the one owner call ---------

    def shareable_sources(self, *, limit: int = 3) -> list[dict[str, Any]]:
        """The artifacts she is *actually* working on or reading right now, with their real ids.

        Only what this round already holds: the work artifact this round is about and the private
        material she is reading.  No listing of the artifact library, no search, no title guessing:
        a source she is not currently engaged with is not "shareable" just because it exists.
        """

        sources: list[dict[str, Any]] = []
        work = self.work_artifact_id
        if work:
            sources.append({"artifact_id": "artifact:%s" % str(work).replace("artifact:", ""),
                            "kind": "current_work"})
        reading = self.reading_artifact_id
        if reading:
            sources.append({"artifact_id": "artifact:%s" % str(reading).replace("artifact:", ""),
                            "kind": "reading_material"})
        seen: set = set()
        out: list[dict[str, Any]] = []
        for row in sources:
            if row["artifact_id"] in seen:
                continue
            seen.add(row["artifact_id"])
            out.append(row)
            if len(out) >= limit:
                break
        return out

    def sharing_decision_context(self, *, limit: int = lsc.MAX_PROMPT_SHARES) -> dict[str, Any]:
        """What she may do about sharing this round -- bounds and facts only, never a send.

        One read (``list_shares`` over the open states), the sources she could prepare from, and
        the exact handles a decision may echo back (``allowed_share_ids`` + each share's own
        ``head_version_id``, which is the CAS the owner will check).  With no owner wired the block
        says so by reason code and offers nothing.
        """

        block = dict(lsc.prompt_block(self.sharing_service, limit=limit))
        block["sources"] = self.shareable_sources()
        block["allowed_share_ids"] = list(lsc.open_share_ids(block))
        block["how"] = (
            "prepare: one artifact_id from sources + explicit share_text (or excerpt_chars); "
            "defer/send_local/drop: one share_id from allowed_share_ids + that share's "
            "head_version_id. A draft is not a delivery; nothing leaves this instance."
        )
        self.last_sharing_block = block
        return block

    def _apply_sharing_action(self, decision_record: Any) -> Optional[dict[str, Any]]:
        """Turn the Decision's one sharing request into exactly one owner call.

        The Decision only ever *asks*: this method calls B's service with the documented keywords,
        refuses a reference this round did not offer (before touching the owner), and reports the
        owner's own answer -- including its local receipt.  A refusal or an ``unknown`` is recorded
        as such; nothing here retries blind or turns "I asked" into "it was sent".
        """

        action = getattr(decision_record, "sharing_action", None)
        if not isinstance(action, Mapping):
            return None
        op = str(action.get("op") or "")
        if self.sharing_service is None:
            result = {"ok": False, "status": "unavailable",
                      "reason_code": lsc.REASON_NOT_WIRED, "op": op, "delivered": False}
            self.last_sharing = result
            return result
        block = self.last_sharing_block or {}
        if op == ag1.SHARING_ACTION_PREPARE:
            source = str(action.get("source_artifact_id") or "")
            offered = {str(row.get("artifact_id")) for row in (block.get("sources") or [])}
            if source not in offered:
                result = {"ok": False, "status": "refused", "op": op, "delivered": False,
                          "reason_code": "SHARING_SOURCE_NOT_OFFERED"}
                self.last_sharing = result
                return result
            result = lsc.prepare_draft(
                self.sharing_service,
                source_artifact_id=source,
                share_text=action.get("share_text") or None,
                excerpt_chars=action.get("excerpt_chars"),
                title=action.get("title") or None,
                expires_after_s=action.get("expires_after_s"),
            )
        else:
            share_id = str(action.get("share_id") or "")
            offered_ids = {str(x) for x in (block.get("allowed_share_ids") or [])}
            if share_id not in offered_ids:
                result = {"ok": False, "status": "refused", "op": op, "delivered": False,
                          "share_id": share_id, "reason_code": "SHARING_DRAFT_NOT_OFFERED"}
                self.last_sharing = result
                return result
            result = lsc.apply_request(
                self.sharing_service,
                share_id=share_id,
                action=op,
                expected_head=str(action.get("expected_head") or ""),
                defer_condition=action.get("defer_condition") or None,
                opportunity_ref=action.get("opportunity_ref") or None,
                reason=action.get("reason") or None,
            )
        result = dict(result)
        result["op"] = op
        # ``delivered`` is already the owner's own wording (LOCAL_DELIVERED only); make sure a
        # caller cannot read "ok" as "sent".
        result.setdefault("delivered", False)
        self.last_sharing = result
        if not result.get("ok"):
            self.last_error = ("SHARING_%s_%s" % (op.upper(),
                                                  str(result.get("reason_code") or "REFUSED")))[:200]
        return result
    @staticmethod
    def _sharing_side_effect_applied(base: Mapping[str, Any]) -> bool:
        """Did this round's one sharing request really reach the owner and land a version?

        ``applied`` / ``already_applied`` are the owner's own words for "a version now exists".
        A refusal, a conflict, a stale source or an ``unknown`` is not a side effect.
        """

        sharing = (base or {}).get("sharing")
        if not isinstance(sharing, Mapping):
            return False
        if not sharing.get("ok"):
            return False
        return str(sharing.get("status") or "") in ("applied", "already_applied")

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

    # -- v0.4 lived day: the bounded live scene the decision is made in --------------------

    def world_live_context(self, *, observed_at: Optional[str] = None,
                           limit: int = llcv.MAX_ENTRIES) -> dict[str, Any]:
        """Where she is right now, read through the same seam that took this round's observation.

        One read, no owner call of its own: the World seam already observed the world for the
        candidate sources, so this only *describes* that same observation -- place **by name**,
        what is in her hands, the places she could walk to with their walking time and the owner's
        own opening verdict, and the concrete things the observation really offers (including her
        ordinary hand work, when the seam publishes it).

        Two things it refuses to do:

        * pretend a place she is not in is visible (the owner's own ``observed`` verdict travels
          with every entry, and its furniture is never copied in);
        * fill a gap: whatever could not be read stays in ``unknowns`` by reason code, so "unknown"
          and "nothing there" cannot be confused by the model.

        It is prompt data only.  Nothing here becomes a Candidate, a permission or a Decision.
        """

        provider = self.world_opportunity_provider
        if provider is None:
            context = llcv.empty_context(llcv.REASON_NOT_WIRED)
        else:
            context = llcv.read_live_context(
                provider, limit=limit,
                observed_at=observed_at or self.last_world_observed_at)
        self.last_live_context = dict(context)
        return dict(context)


    # -- v0.4 stage A: her own "later", and the options for a new private project ---------

    def park_basis_for_round(self, *, observed_at: str, act: bool) -> Optional[dict[str, Any]]:
        """The park basis this round may offer, or ``None``.

        Offered only while a *running* (ACTIVE) Activity holds the foreground and this caller may
        act -- the one situation "set this down until a time I name" describes.  Nothing is
        invented here: the instant comes from the committed Decision's own ``later_item``
        proposal, and AG-2 refuses a WAIT that carries none (``PARK_TIME_REQUIRED``).
        """

        if not act:
            return None
        activity = self._last_foreground_record or {}
        if str(activity.get("status") or "") != "ACTIVE":
            return None
        activity_ref = activity.get("activity_ref") or activity.get("activity_id")
        if not activity_ref:
            return None
        try:
            return lfi.park_basis(
                activity_ref=activity_ref, observed_at=observed_at,
                artifact_ref=(self._round_readiness.target_ref
                              if self._round_readiness is not None else None))
        except Exception as exc:  # a failed basis is reported, never guessed
            self.last_error = f"PARK_BASIS_FAILED: {type(exc).__name__}: {exc}"[:200]
            return None

    def decision_situation_context(self, *, now: str, park: Any = None,
                                   park_basis: Optional[Mapping[str, Any]] = None,
                                   wait_basis: Optional[Mapping[str, Any]] = None
                                   ) -> dict[str, Any]:
        """The bounded, present-tense situation the decision model needs -- data, not authority.

        The *first* thing in it is the live scene (``world``): the place she is standing in by
        name, what is in her hands, the places she could walk to with their walking time and the
        owner's opening verdict, and the concrete things this observation really offers -- because
        a decision about a day starts from where she actually is, not from a list of ids.
        After that come her own open "later" items with their real refs (so it can cancel or
        reschedule one by name), the project options this round supports (with a bounded seed
        excerpt, because a project with no text is not a choice), what she is reading, the question
        she wrote, and whether parking the running Activity is on offer.  The research/practice
        material comes last and is the first thing the prompt drops when it does not fit.  Nothing
        here grants anything: the owners still decide, and the store write still happens in
        ``_apply_future_item_writes``.
        """

        ctx: dict[str, Any] = {"schema_version": "chiyo.life.decision_situation.v1"}
        #: v0.4 lived day: the live World scene for *this* round.  Read in
        #: ``_refresh_world_opportunities`` from the same observation that fed the candidates; if
        #: no observation happened this round it stays the honest absence (a named reason code),
        #: never an empty-looking world.
        ctx["world"] = dict(self.last_live_context or
                            llcv.empty_context(llcv.REASON_NO_OBSERVATION))
        #: v0.4 body participation: her body, qualitatively and with its source.  Built from the
        #: same situation read this round already made (``read_situation`` -> body column) plus
        #: the World owner's own REST entry, so it costs no extra read and cannot disagree with
        #: the capacity AG-1 gets.  ``recovery.claim`` is always ``NOT_ASSERTED``: an open REST
        #: session is a session, not evidence that she recovered.
        ctx["body"] = dict(self.last_body_context
                           or lbc.empty_context(lbc.REASON_BODY_COLUMN_ABSENT))
        #: v0.4 A (pacing): the *bounded breakpoint* on offer -- what the running Activity keeps
        #: (same material, same page), which owner fact it would wait on, and the one legal move
        #: (WAIT, which frees the foreground).  When it is not on offer the block carries a named
        #: reason instead of looking like "there is nothing to put down".
        ctx["breakpoint"] = self.body_breakpoint_view()
        #: v0.4 explicit-condition PAUSE: the one condition this round may be paused *until*, if
        #: she explicitly names it.  Same basis as WAIT; the block is data and an allow-list, not
        #: an offer of authority.  Without a basis it carries a named ``why_not`` so "nothing to
        #: wait for" never looks like "the field was forgotten".
        ctx["pause_condition"] = self.pause_condition_view(wait_basis)
        #: v0.4 sharing: her open drafts (ids, states, titles, CAS heads) and the sources she could
        #: share from -- read once per decision round through B's owner.  It is a menu of facts:
        #: nothing here sends, and ``LOCAL_DELIVERED`` is the only delivery this tree has.
        ctx["sharing"] = self.sharing_decision_context()
        items: list[Any] = []
        if self.future_items is not None:
            try:
                items = self.future_items.open_items()
            except Exception as exc:
                ctx["later_items_error"] = f"{type(exc).__name__}: {exc}"[:160]
        due_refs = {str(row.get("item_ref")) for row in self._round_future_items}
        ctx["later_items"] = [
            {"item_ref": row.item_ref, "title": row.title[:120], "due_at": row.due_at,
             "origin": row.origin, "due": row.item_ref in due_refs,
             "considered": int(row.times_considered)}
            for row in items[:6]]
        ctx["due_item_refs"] = sorted(due_refs)
        ctx["project_options"] = [
            {"option_ref": str(option.get("option_ref") or ""),
             "title": str(option.get("title") or "")[:120],
             "kind": option.get("kind"),
             "seed_excerpt": str(option.get("seed_text") or "")[:240],
             "basis_ref": option.get("basis_ref"),
             #: v0.4 research: a research option names the material hits it would cite (owner id +
             #: the revision the hit was read at) and what it would say about its own standing, so
             #: the Decision can tell "研究一个真的有资料的问题" from "把疑问写成一篇空白".
             "research": bool(option.get("research")),
             "materials": [{"artifact_id": str(row.get("artifact_id") or ""),
                            "source_revision": str(row.get("source_revision") or ""),
                            "title": str(row.get("title") or "")[:120],
                            "origin": row.get("origin"),
                            "excerpt": str(row.get("excerpt") or "")[:160]}
                           for row in (option.get("materials") or ())
                           if isinstance(row, Mapping)][:3],
             #: v0.4 C: a *review* option names the experiences it would cite (A's own ids + when +
             #: a bounded excerpt), so the Decision can tell "a real review of what I can still read"
             #: from "a blank page".
             "review": bool(option.get("review")),
             "experiences": [{"experience_id": str(row.get("experience_id") or ""),
                              "occurred_at": str(row.get("occurred_at") or "")[:40],
                              "excerpt": str(row.get("excerpt") or "")[:160]}
                             for row in (option.get("experiences") or ())
                             if isinstance(row, Mapping)][:2],
             "scope": str(option.get("scope") or "")[:200],
             "uncertainty": str(option.get("uncertainty") or "")[:120]}
            for option in self._round_project_options][:4]
        #: v0.4 research: the material hits she can really read and the research results she already
        #: holds (bounded, with their own provenance and source checks).  Both are *document*
        #: context read through the owner -- never a memory and never a claim of currency.
        ctx["research"] = self.research_decision_context()
        #: v0.4 C: her own recallable experiences and the practices of hers that are relevant right
        #: now (each with its handle).  This is what makes a review choosable and a method nameable;
        #: nothing here is a permission, an executable step or a persona trait.
        ctx["practice"] = self.practice_decision_context()
        reading = self.private_material_situation() or {}
        if isinstance(reading, Mapping) and reading:
            material = reading.get("material") if isinstance(reading.get("material"), Mapping) else {}
            position = reading.get("position") if isinstance(reading.get("position"), Mapping) else {}
            annotations = (reading.get("annotations")
                           if isinstance(reading.get("annotations"), Mapping) else {})
            ctx["reading"] = {
                "state": reading.get("state"),
                "material_title": str(material.get("title") or "")[:120],
                "pages": material.get("pages"),
                "page": position.get("page"),
                "next_page": position.get("next_page"),
                "notes": annotations.get("note"),
                "unclear": annotations.get("unclear"),
            }
        questions = self._bounded_questions()
        if questions:
            ctx["questions"] = questions
        activity = self._last_foreground_record or {}
        if activity:
            ctx["activity"] = {
                "activity_ref": activity.get("activity_ref") or activity.get("activity_id"),
                "status": activity.get("status"),
                "activity_kind": activity.get("activity_kind"),
                "target_ref": (self._round_readiness.target_ref
                               if self._round_readiness is not None else None),
                "step_available": (self._round_readiness.available
                                   if self._round_readiness is not None else None),
                "step_reason": (self._round_readiness.reason_code
                                if self._round_readiness is not None else None),
            }
        basis = park or park_basis
        ctx["park"] = ({"available": True, "activity_ref": basis.get("activity_ref"),
                        "how": "choose WAIT and set later_item.park_current=true with a time"}
                       if isinstance(basis, Mapping)
                       else {"available": False,
                             "how": "parking needs a running Activity to set down"})
        # v0.4 B: who is at the door and whether a joint time is open -- counts, the invitation's
        # own scope label and the session state.  Data only: accepting an invitation is a
        # Decision on an event A's ledger really holds, never something this context can do.
        ctx["contact"] = self.contact_parts()
        ctx["now"] = str(now)
        return ctx

    def _bounded_questions(self, *, limit: int = 2, chars: int = 200) -> list[dict[str, Any]]:
        """Her own written questions, bounded -- the material a project option may grow from."""

        provider = self.project_questions_provider
        if provider is None:
            return []
        try:
            rows = list(provider() or [])
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            note = " ".join(str(row.get("note") or "").split())[:chars]
            if not note:
                continue
            out.append({"note": note,
                        "from": f"pnote:{str(row.get('marker_id') or '')[:64]}"})
            if len(out) >= limit:
                break
        return out
    def future_items_view(self, *, now: Optional[str] = None) -> dict[str, Any]:
        """The store's own read-only view (report).  Unwired is a first-class answer."""

        store = self.future_items
        if store is None:
            return {"status": "NOT_WIRED", "open_count": 0, "due_refs": [], "due": [], "open": []}
        view = store.view(now=now or self._now_iso())
        self.last_future_items = view
        return view

    def due_future_items(self, now: Optional[str] = None) -> list[dict[str, Any]]:
        """The OPEN items the clock has reached.  A read; nothing is mutated and nothing acts."""

        store = self.future_items
        if store is None:
            return []
        try:
            return [row.to_dict() for row in store.due(now or self._now_iso())]
        except Exception as exc:  # an unreadable store is reported, never read as "none"
            self.last_error = f"FUTURE_ITEMS_READ_FAILED: {type(exc).__name__}: {exc}"[:200]
            return []

    def project_options(self, *, now: Optional[str] = None,
                        due_items: Optional[Sequence[Mapping[str, Any]]] = None
                        ) -> list[dict[str, Any]]:
        """Bounded options for a *new* private project, or ``[]`` (0 LLM).

        Nothing is offered unless the owner service is really wired: an option the round could
        not actually create would be a menu of things that fail when chosen.
        """

        gateway = self.project_gateway
        project_available = bool(gateway is not None
                                 and getattr(gateway, "available", lambda: False)())
        #: v0.4 research: a research option is created by the *research* service, not by the
        #: project gateway, so either seam being wired is enough to offer what that seam can really
        #: do.  Both are read fresh; an unavailable one contributes nothing (never a stand-in).
        research_available = self.research.available()
        #: v0.4 C: a *review* option is created by the practice workspace, so that seam being wired
        #: (and A's Memory really returning an experience) is what makes it offerable.  Read fresh:
        #: an unavailable seam contributes nothing, and no experience means no review option.
        practice_available = self.practice_available()
        if not (project_available or research_available or practice_available):
            self.last_project_options = []
            self._round_methods = []
            return []
        questions: list[dict[str, Any]] = []
        provider = self.project_questions_provider
        if provider is not None:
            try:
                questions = [dict(row) for row in (provider() or []) if isinstance(row, Mapping)]
            except Exception as exc:  # a failed read is reported, never guessed
                self.last_error = f"PROJECT_QUESTIONS_READ_FAILED: {type(exc).__name__}: {exc}"[:200]
                questions = []
        projects = gateway.list_projects() if project_available else []
        # v0.4 sharing: share artifacts live in the same owner library, so they would otherwise show
        # up as "projects to continue".  B's own marker decides what a share is (imported, not
        projects = [row for row in (projects or [])
                    if not (isinstance(row, Mapping) and lsc.is_share_title(row.get("title")))]
        self.last_project_projects = lpo.projects_report(projects)
        materials = self.research.materials() if research_available else []
        if research_available and self.research.last_error:
            self.last_research_error = self.research.last_error
        #: v0.4 C: the experiences a review could actually cite, read through A's Memory this round.
        #: The seam is asked once; an unbound Memory yields ``[]`` *and* a named error (never a lie).
        experiences: list[dict[str, Any]] = []
        if practice_available:
            experiences = self.practice.experiences(query=self._practice_query())
            if self.practice.last_error:
                self.last_practice_error = self.practice.last_error
        if due_items is not None:
            self._round_future_items = [dict(row) for row in due_items]
        options = lpo.build_options(
            questions=questions,
            due_items=self._round_future_items,
            existing_projects=projects,
            research_materials=materials,
            research_available=research_available,
            experiences=experiences,
            practice_available=practice_available,
            limit=self.max_project_options)
        # The source reads this in the same round, so the candidates and this report agree.
        self._round_project_options = options
        self.last_project_options = options

        return options
    @staticmethod
    def _future_item_artifact_ref(item: Mapping[str, Any]) -> Optional[str]:
        """The one artifact a due item can be *carried out on*, if it named one."""

        for ref in item.get("source_refs") or []:
            text = str(ref)
            if text.startswith("artifact:") and len(text) > len("artifact:"):
                return text
        return None

    def _extra_opportunities(self) -> list[dict[str, Any]]:
        """What the PERSONAL_OPPORTUNITY source may offer beyond artifacts, this round (0 LLM).

        A due item is offered only when it names an object a step can really act on; an item with
        no object is offered as a *project option* instead (see ``project_options``), so selecting
        it can never start an Activity with nothing to do.  Both are her own refs -- no other
        owner's namespace is reachable from here.
        """

        out: list[dict[str, Any]] = []
        for item in self._round_future_items:
            artifact_ref = self._future_item_artifact_ref(item)
            if not artifact_ref:
                continue
            out.append({
                "source_ref": str(item.get("item_ref")),
                "target_refs": [artifact_ref],
                "title": item.get("title"),
                "due_at": item.get("due_at"),
                "kind": "FUTURE_ITEM_DUE",
                "revision": int(item.get("revision") or 1),
            })
        for option in self._round_project_options:
            out.append({
                "source_ref": str(option.get("option_ref")),
                "target_refs": [str(option.get("option_ref"))],
                "title": option.get("title"),
                "kind": option.get("kind"),
            })
        return out

    def _selected_candidate_payload(self, decision_record: Any) -> Optional[dict[str, Any]]:
        ref = getattr(decision_record, "selected_candidate_ref", None)
        if not ref:
            return None
        try:
            candidate = self.runtime.candidate_read.get_candidate(str(ref))
        except Exception as exc:  # a failed read is reported, never guessed
            self.last_error = f"CANDIDATE_READ_FAILED: {type(exc).__name__}: {exc}"[:200]
            return None
        return dict(candidate) if isinstance(candidate, Mapping) else None

    def _selected_extra_ref(self, decision_record: Any) -> Optional[str]:
        candidate = self._selected_candidate_payload(decision_record)
        if not candidate:
            return None
        ref = str(candidate.get("source_ref") or "")
        if ref.startswith((lfi.ITEM_REF_NAMESPACE, lpo.OPTION_REF_NAMESPACE)):
            return ref
        return None

    def _decision_chose_project(self, decision_record: Any) -> bool:
        ref = self._selected_extra_ref(decision_record)
        return bool(ref and ref.startswith(lpo.OPTION_REF_NAMESPACE))

    def _option_for_ref(self, option_ref: Any) -> Optional[dict[str, Any]]:
        wanted = str(option_ref or "")
        for option in self._round_project_options:
            if str(option.get("option_ref")) == wanted:
                return dict(option)
        return None

    def resume_point_for_artifact(self, artifact_id: Any) -> Optional[ArtifactResumePoint]:
        """The owner's own continuation point for *that* artifact (multi-project routing).

        The source's per-round cache is used first, because it is the same read the candidate was
        built from; only when it does not hold the object is the port asked directly.  A work the
        owner no longer calls resumable is ``None`` -- never the bound work standing in for it.
        """

        text = str(artifact_id or "")
        while text.startswith("artifact:"):
            text = text[len("artifact:"):]
        if not text:
            return None
        # The owner answers on its *own* spelling of the id (``artifact:<id>``); a bare id is
        # NOT_FOUND there, which would silently downgrade every work to "not continuable".
        owner_id = f"artifact:{text}"
        cached = getattr(self.artifact_source, "last_resume_points", None) or {}
        for key in (owner_id, text):
            point = cached.get(key)
            if point is not None:
                return point
        if self.artifact_port is None:
            return None
        try:
            point = self.artifact_port.resume_point(owner_id)
        except Exception as exc:
            self.last_error = f"ARTIFACT_READ_FAILED: {type(exc).__name__}: {exc}"[:200]
            return None
        return point if getattr(point, "resumable", False) else None

    def round_point_for(self, decision_record: Any) -> Optional[ArtifactResumePoint]:
        """The work the committed Decision actually named, if it named an artifact candidate.

        "Named" is the same answer the step builder uses (``_named_work_ref``): a due item's
        artifact, an artifact ``source_ref``, or the owner targets of the running ``CURRENT_ACTIVITY``
        candidate.  The round report and the executed step therefore cannot point at two different
        objects.
        """

        ref = self._named_work_ref(decision_record)
        if not ref:
            return None
        return self.resume_point_for_artifact(ref)

    def _work_adoption_spec_for(self, *, artifact_id: str, title: str,
                                head_version_id: Optional[str]) -> dict[str, Any]:
        """One CONTINUE_PERSONAL_ARTIFACT shape for an explicitly named work.

        Built from the owner's own ids (never from a cached candidate), so the same code serves
        project creation and multi-project continuation without a second Activity schema.
        """

        bare = str(artifact_id or "")
        while bare.startswith("artifact:"):
            bare = bare[len("artifact:"):]
        artifact_ref = f"artifact:{bare}"
        version_ref = ""
        if head_version_id:
            head = str(head_version_id)
            if head.startswith("artifact-version:"):
                head = head[len("artifact-version:"):]
            version_ref = f"artifactversion:{head}"
        target_refs = [ref for ref in (artifact_ref, version_ref) if ref]
        spec = ag2.ActivityAdoptionSpec(
            activity_kind="CONTINUE_PERSONAL_ARTIFACT",
            title=f"继续《{title or bare}》",
            target_refs=target_refs,
            origin_ref=artifact_ref,
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

    def _project_adoption_spec(self, decision_record: Any) -> dict[str, Any]:
        """Selecting a project option *is* the write: ask the owner, then describe the Activity.

        This runs only for a committed Decision that selected that very option (AG-2 calls the
        routed spec with the Decision).  A refusal raises a named error and creates no Activity;
        a confirmed creation is reported on the round (``project_created``) and the new work then
        becomes an ordinary continuable artifact -- not a second kind of project.
        """

        option_ref = self._selected_extra_ref(decision_record) or ""
        option = self._option_for_ref(option_ref)
        if option is None:
            raise AutonomousLifeError(
                "PROJECT_OPTION_NOT_IN_THIS_ROUND: %r" % (option_ref,))
        if option.get("review"):
            return self._review_adoption_spec(option, option_ref=option_ref)
        if option.get("research"):
            return self._research_adoption_spec(option, option_ref=option_ref)
        gateway = self.project_gateway
        if gateway is None or not getattr(gateway, "available", lambda: False)():
            raise lpo.ProjectOptionError(lpo.REASON_OWNER_UNAVAILABLE,
                                         "no private-project owner service is wired")
        created = gateway.create_project(
            title=str(option.get("title") or ""),
            seed_text=str(option.get("seed_text") or ""),
            source_refs=[str(ref) for ref in (option.get("source_refs") or [])],
            option_ref=option_ref,
        )
        self.last_project_created = dict(created, option_ref=option_ref,
                                         title=option.get("title"))
        point = self.resume_point_for_artifact(created.get("artifact_id"))
        if point is not None:
            return self._adoption_spec(point)
        return self._work_adoption_spec_for(
            artifact_id=str(created.get("artifact_id") or ""),
            title=str(option.get("title") or ""),
            head_version_id=created.get("version_id"))

    def _research_adoption_spec(self, option: Mapping[str, Any], *,
                                option_ref: str) -> dict[str, Any]:
        """Selecting a *research* option is the one owner write: create the research work.

        The sources handed over are exactly the hits the option was built from (owner ids + the
        revisions the workspace reported), so the document can only cite material that existed when
        she chose.  B's service asks the ManagedArtifact owner to create it; nothing here invents an
        artifact, and a refusal raises by name and creates no Activity.
        """

        if not self.research.available():
            raise lpo.ProjectOptionError(lpo.REASON_OWNER_UNAVAILABLE,
                                         "no research workspace service is wired")
        refs = [dict(row) for row in (option.get("materials") or [])
                if isinstance(row, Mapping)]
        try:
            created = self.research.create_research(
                question=str(option.get("seed_text") or ""),
                source_refs=refs,
                title=str(option.get("title") or ""),
                scope=str(option.get("scope") or ""),
                uncertainty=str(option.get("uncertainty") or lpo.DEFAULT_UNCERTAINTY),
                operation_key=self._research_operation_key(option_ref, refs),
            )
        except rwc.ResearchSeamError as exc:
            self.last_research_error = f"{exc.reason_code}:{exc.detail}"[:200]
            raise lpo.ProjectOptionError(lpo.REASON_OWNER_REFUSED,
                                         str(self.last_research_error)[:180]) from exc
        self.last_research_created = dict(created, option_ref=option_ref,
                                          title=option.get("title"))
        # The round reports the created research work with the *same* key the ordinary project path
        # uses, so a reader sees one shape and can tell the kind from ``research: true``.
        self.last_project_created = dict(
            created, option_ref=option_ref, title=option.get("title"),
            research=True, source_refs=refs,
            artifact_id=created.get("artifact_id"),
            version_id=created.get("head_version_id"))
        point = self.resume_point_for_artifact(created.get("artifact_id"))
        if point is not None:
            return self._adoption_spec(point)
        return self._work_adoption_spec_for(
            artifact_id=str(created.get("artifact_id") or ""),
            title=str(option.get("title") or ""),
            head_version_id=created.get("head_version_id"))

    @staticmethod
    def _research_operation_key(option_ref: str, refs: Sequence[Mapping[str, Any]]) -> str:
        """One intended creation, one key: the option *and* the exact sources it cited.

        Derived from what is intended (never from the attempt), so resolving an UNKNOWN and asking
        again cannot mint a second research work; a changed material is a different intent and a
        different key.
        """

        material = "|".join("%s@%s" % (row.get("artifact_id"), row.get("source_revision"))
                            for row in refs)
        digest = hashlib.sha256(("%s|%s" % (option_ref, material)).encode("utf-8")).hexdigest()
        return "research:%s" % digest[:24]

    def _review_adoption_spec(self, option: Mapping[str, Any], *,
                              option_ref: str) -> dict[str, Any]:
        """Selecting a *review* option is the one owner write: create the review.

        The experience ids handed over are exactly the ones the option was built from and A's
        Memory returned this round; B's service re-reads every one of them through A before it
        writes, so the document can only cite what is still recallable.  Nothing here invents an
        artifact, and a refusal raises by name and creates no Activity.
        """

        if not self.practice.available():
            raise lpo.ProjectOptionError(lpo.REASON_OWNER_UNAVAILABLE,
                                         "no practice workspace service is wired")
        rows = [dict(row) for row in (option.get("experiences") or ())
                if isinstance(row, Mapping)]
        experience_ids = [str(row.get("experience_id")) for row in rows
                          if str(row.get("experience_id") or "").strip()]
        if not experience_ids:
            raise lpo.ProjectOptionError(
                lpo.REASON_OWNER_REFUSED,
                "the review option names no recallable experience")
        try:
            created = self.practice.create_review(
                question=str(option.get("seed_text") or option.get("title") or ""),
                title=str(option.get("title") or ""),
                experience_ids=experience_ids,
                operation_key=self._review_operation_key(option_ref, experience_ids),
            )
        except lpc.PracticeSeamError as exc:
            self.last_practice_error = ("%s:%s" % (exc.reason_code, exc.detail)
                                        if exc.detail else exc.reason_code)[:200]
            raise lpo.ProjectOptionError(lpo.REASON_OWNER_REFUSED,
                                         str(self.last_practice_error)[:180]) from exc
        self.last_review_created = dict(created, option_ref=option_ref,
                                        title=option.get("title"))
        # The round reports the created review with the *same* key the ordinary project path uses,
        # so a reader sees one shape and can tell the kind from ``review: true``.
        self.last_project_created = dict(
            created, option_ref=option_ref, title=option.get("title"), review=True,
            experience_ids=experience_ids,
            artifact_id=created.get("artifact_id"),
            version_id=created.get("head_version_id"))
        point = self.resume_point_for_artifact(created.get("artifact_id"))
        if point is not None:
            return self._adoption_spec(point)
        return self._work_adoption_spec_for(
            artifact_id=str(created.get("artifact_id") or ""),
            title=str(option.get("title") or ""),
            head_version_id=created.get("head_version_id"))

    @staticmethod
    def _review_operation_key(option_ref: str, experience_ids: Sequence[str]) -> str:
        """One intended review, one key: the option *and* the experiences it cites.

        Derived from what is intended (never from the attempt), so resolving an UNKNOWN and asking
        again cannot mint a second review; a different set of experiences is a different intent.
        """

        material = "|".join(str(x) for x in experience_ids)
        digest = hashlib.sha256(("%s|%s" % (option_ref, material)).encode("utf-8")).hexdigest()

    def _save_method_proposal(self, record: Any) -> None:
        """Save at most one bounded practice proposal through B's practice workspace.

        It is the Decision's own proposal, saved *after* the commit and keyed by that Decision, so
        a retry cannot mint a second draft.  A host with no practice workspace, or a refusal, is
        reported as exactly that: nothing is invented, and nothing is upgraded to a belief, a
        preference, a persona trait or an executable program.
        """

        proposal = getattr(record, "method_proposal", None)
        if not isinstance(proposal, Mapping):
            return
        if not self.practice.available():
            self.last_practice_error = lpc.REASON_SERVICE_MISSING
            return
        try:
            created = self.practice.create_method(
                title=str(proposal.get("title") or ""),
                instruction=str(proposal.get("instruction") or ""),
                scope=str(proposal.get("scope") or ""),
                uncertainty=str(proposal.get("uncertainty") or ""),
                experience_ids=proposal.get("experience_ids") or (),
                review_refs=proposal.get("review_refs") or (),
                operation_key=self._method_operation_key(record, proposal))
        except lpc.PracticeSeamError as exc:
            self.last_practice_error = ("%s:%s" % (exc.reason_code, exc.detail)
                                        if exc.detail else exc.reason_code)[:200]
            return
        except Exception as exc:  # a seam failure is reported, never swallowed as success
            self.last_practice_error = "%s: %s" % (type(exc).__name__, exc)
            return
        self.last_method_created = dict(created, decision_id=getattr(record, "decision_id", None),
                                        title=str(proposal.get("title") or ""))

    @staticmethod
    def _method_operation_key(record: Any, proposal: Mapping[str, Any]) -> str:
        """One intended draft, one key -- the Decision that proposed it and its own words."""

        seed = "|".join([str(getattr(record, "decision_id", "") or ""),
                         str(proposal.get("title") or ""),
                         str(proposal.get("instruction") or "")])
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return "practice-method:%s" % digest[:24]

    def _bind_method_usage(self, record: Any) -> None:
        """Note, from the *committed* Decision, which of her practices this round followed.

        AG-1 already validated the handle against the list this round offered, so resolving it here
        is a lookup in that same read -- never a second source of truth.  What is recorded is the
        method's artifact, its head version as this round read it, and (once the step settles) the
        consequence refs the owner really confirmed.  No method is executed, and none is given any
        power over the step or over the owners.
        """

        ref = str(getattr(record, "method_ref", "") or "")
        if not ref:
            return
        # Resolve against the *exact* read AG-1 validated the handle in.  ``practice.last_methods``
        # is overwritten by any later read this round (the writing prompt's own method search), and
        # a method can also gain a new head between the decision and this settlement.  Looking the
        # handle up there would silently bind the claim -- and its trial -- to a version she never
        # saw.  The offered row's head is the version this usage and its trial are about.
        row = self._offered_method(ref)
        if row is None:
            self.last_practice_error = ("%s:%s" % (lpc.REASON_METHOD_REF_UNKNOWN, ref))[:200]
            return
        self._pending_method_usage = {
            "method_ref": ref,
            "artifact_id": row.get("artifact_id"),
            "version_ref": row.get("head_version_id"),
            "version_number": row.get("version_number"),
            "title": row.get("title"),
            "method_state": row.get("method_state"),
            "decision_id": getattr(record, "decision_id", None),
            "consequence_refs": [],
        }
        trial = getattr(record, "method_trial", None)
        if isinstance(trial, Mapping):
            self._pending_method_trial = dict(trial, artifact_id=row.get("artifact_id"),
                                              expected_head=row.get("head_version_id"))

    def _offered_method(self, method_ref: str) -> Optional[dict[str, Any]]:
        """The method row this round really offered for ``method_ref`` (or ``None``).

        This is the same list AG-1 validated the handle against, so a handle that was legal when
        the Decision committed can never resolve to a row that was read later -- or to a newer
        head -- during the same round.
        """

        wanted = str(method_ref or "").strip()
        if not wanted:
            return None
        for row in self._round_methods:
            if str(row.get("method_ref") or "") == wanted:
                return dict(row)
        return None

    def _settle_method_binding(self, base: dict[str, Any]) -> None:
        """Report the practice this round followed and record the trial it named (at most once).

        Called from ``_finish`` so every exit path reports it.  The consequence refs come from the
        owner's own step receipt -- not from "we wrote a version", which is why a successful write
        is *never* turned into ``HELPED`` here: the verdict (and the evidence behind it) is the
        model's, and the seam refuses HELPED/NOT_HELPED without experiences A can still read.
        """

        usage = self._pending_method_usage
        if usage is None:
            return
        receipt = base.get("step_receipt")
        receipt = dict(receipt) if isinstance(receipt, Mapping) else {}
        consequence: list[str] = []
        # Only the owner's own step receipt can name a result.  A round with no receipt (NO_ACTION,
        # WAIT, PAUSE, ...) produced no step result, so this list stays empty: naming the *method
        # document itself* as its own consequence would invent an outcome the owner never
        # confirmed.  The method artifact already travels in ``usage["artifact_id"]``.
        artifact_ref = self._owner_artifact_ref(receipt.get("artifact_id"))
        if artifact_ref:
            consequence.append(artifact_ref)
        version_written = receipt.get("version_id")
        if version_written:
            consequence.append("artifactversion:%s" % version_written)
        usage["consequence_refs"] = consequence
        usage["step_effect"] = receipt.get("effect_state")
        usage["version_written"] = version_written
        self.last_method_usage = dict(usage)
        base["method_usage"] = dict(usage)

        pending = self._pending_method_trial
        self._pending_method_usage = None
        self._pending_method_trial = None
        if pending is None:
            return
        try:
            view = self.practice.record_trial(
                artifact_id=str(pending.get("artifact_id") or ""),
                outcome=str(pending.get("outcome") or ""),
                detail=str(pending.get("detail") or ""),
                experience_ids=[str(x) for x in (pending.get("experience_ids") or ())],
                expected_head=pending.get("expected_head"))
        except lpc.PracticeSeamError as exc:
            self.last_practice_error = ("%s:%s" % (exc.reason_code, exc.detail)
                                        if exc.detail else exc.reason_code)[:200]
            base["method_trial"] = {"status": "REFUSED", "method_ref": pending.get("method_ref"),
                                    "reason_code": exc.reason_code,
                                    "detail": exc.detail[:160]}
            return
        except Exception as exc:  # a seams failure is reported, never fabricated as a result
            self.last_practice_error = "%s: %s" % (type(exc).__name__, exc)
            base["method_trial"] = {"status": "FAILED", "method_ref": pending.get("method_ref"),
                                    "detail": self.last_practice_error[:160]}
            return
        view["method_ref"] = pending.get("method_ref")
        view["consequence_refs"] = list(consequence)
        self.last_method_trial = dict(view)
        base["method_trial"] = dict(view)

    def _project_step_spec(self, observed_at: str) -> dict[str, Any]:
        """The one real step after a project was created: write its first version.

        The target is the work the *owner* just confirmed, read back through the same port as any
        other work.  With no readable head there is no step (a named refusal), never a fabricated
        version.
        """

        created = self.last_project_created or {}
        point = self.resume_point_for_artifact(created.get("artifact_id"))
        if point is None:
            raise AutonomousLifeError(
                "PRIVATE_PROJECT_NOT_CONTINUABLE: %r" % (created.get("artifact_id"),))
        return self._ar0_step_spec(point, observed_at=observed_at)

    def _apply_future_item_writes(self, decision_record: Any,
                                  observed_at: str) -> list[dict[str, Any]]:
        """Record what the Decision said about "later", one owner write each.

        The proposal was validated by the decision validator; this is the *write*, and its result
        is reported per row (``APPLIED`` / the store's own refusal).  Nothing here acts on an item.
        """

        store = self.future_items
        writes: list[dict[str, Any]] = []
        proposal = getattr(decision_record, "later_item_proposal", None)
        actions = list(getattr(decision_record, "future_item_actions", ()) or ())
        if proposal is None and not actions:
            self.last_future_item_writes = writes
            return writes
        if store is None:
            for row in ([{"op": "propose"}] if proposal is not None else []) + \
                    [{"op": str(a.get("op"))} for a in actions]:
                writes.append(dict(row, status="REFUSED", reason_code="FUTURE_ITEMS_NOT_WIRED"))
            self.last_future_item_writes = writes
            return writes
        if proposal is not None:
            try:
                due_kwargs: dict[str, Any] = {}
                if proposal.get("in_minutes") is not None:
                    # Resolve the delay the decision named against *this round's* observed instant,
                    # so a replayed round proposes the same instant instead of drifting.
                    due_kwargs["in_minutes"] = proposal.get("in_minutes")
                else:
                    due_kwargs["due_at"] = proposal.get("due_at")
                # A park proposal is a different kind of "later": it names the running Activity it
                # sets down, so the item can be read back as such (and the WAIT it authorised is
                # the owner's, not this store's).
                parked = bool(proposal.get("park_current"))
                result = store.propose(
                    title=proposal.get("title"),
                    origin=(lfi.ORIGIN_PARK if parked else lfi.ORIGIN_MODEL_PROPOSAL),
                    source_refs=list(proposal.get("source_refs") or []),
                    # A park names the Activity the *offer* named (the foreground is already
                    # released by the time this write happens); anything else names the Activity
                    # that is still running, when there is one.
                    activity_ref=((self._round_park_activity_ref or self._foreground_activity_ref())
                                  if parked else self._foreground_activity_ref()),
                    workspace_id=self.workspace_id,
                    now=observed_at,
                    **due_kwargs)
                item = dict(result.get("item") or {})
                writes.append({"op": "propose", "status": "APPLIED", "park": parked,
                               "created": bool(result.get("created")),
                               "item_ref": item.get("item_ref"), "due_at": item.get("due_at")})
            except Exception as exc:
                writes.append({"op": "propose", "status": "REFUSED",
                               "reason_code": str(getattr(exc, "reason_code",
                                                          type(exc).__name__))[:80]})
        if actions:
            writes.extend(lfi.apply_actions(store, actions, now=observed_at))
        self.last_future_item_writes = writes
        return writes

    def _project_owner_ready(self) -> bool:
        gateway = self.project_gateway
        return bool(gateway is not None and getattr(gateway, "available", lambda: False)())

    # -- v0.4 research: options, creation, writing context, quiet state -----------------

    def research_available(self) -> bool:
        """True when B's research workspace is really wired and answers the documented API."""

        return self.research.available()

    def _research_material_ids(self) -> list[str]:
        """The bare ids the research workspace calls *material* (never work).

        Handed to the candidate source as a provider, so this is a read: the workspace owns the
        answer and this cycle only asks.  A failing read yields ``[]`` (no extra exclusion).

        The reading material and the private-items document are deliberately *not* returned even
        when the workspace lists them as material: their own opportunities (a page to read, the
        private document's own owner surface) stay exactly what they were, and the reading
        material's page-read path must not be switched off by a research workspace being wired.
        """

        if not self.research.available():
            return []
        protected = {self._bare_artifact_id(self.reading_artifact_id),
                     self._bare_artifact_id(self.private_items_artifact_id)}
        return [text for text in self.research.material_ids() if text not in protected]

    @staticmethod
    def _bare_artifact_id(value: Any) -> str:
        text = str(value or "")
        while text.startswith("artifact:"):
            text = text[len("artifact:"):]
        return text

    def research_decision_context(self) -> dict[str, Any]:
        """The bounded research slice of the decision prompt, or a named not-wired answer."""

        if not self.research.available():
            return {"status": "NOT_WIRED",
                    "reason_code": rwc.REASON_SERVICE_MISSING,
                    "material_hits": [], "research_results": []}
        view = self.research.decision_context()
        if self.research.last_error:
            self.last_research_error = self.research.last_error
        return view

    def research_writing_context(self, artifact_id: Any) -> Optional[dict[str, Any]]:
        """The bounded context one work's writing prompt may carry (document context only).

        For a research work this carries its own question / source refs / scope / uncertainty plus
        the **real body text** of each source at the version the research named; for any other work
        it carries the research results she already has as a digest she may refer to.  Never an
        experience, never a belief, and never a claim that a moved source is still current.
        """

        if not self.research.available():
            return None
        try:
            context = self.research.writing_context(str(artifact_id or ""))
        except Exception as exc:  # a failed read is reported, never guessed
            self.last_research_error = f"{type(exc).__name__}: {exc}"[:200]
            return None
        self.last_research_context = dict(context) if isinstance(context, Mapping) else None
        return self.last_research_context

    # -- v0.4 C: her own practices (reviews, methods, recallable experiences) ------------

    def practice_available(self) -> bool:
        """True when B's practice workspace is really wired and answers the documented API."""

        return self.practice.available()

    def _practice_method_ids(self) -> list[str]:
        """The bare ids the workspace calls *method*: reference documents, never work.

        A method is prose a writing prompt may consult; it is not something to continue, wrap up or
        offer as a personal opportunity, so it is withdrawn from the continuation and closeout
        offers exactly like the research materials.  Its *review* siblings stay ordinary works --
        a review is real writing, and AR-0 may keep writing it.
        """

        if not self.practice.available():
            return []
        try:
            return self.practice.method_artifact_ids()
        except Exception as exc:
            self.last_practice_error = "%s: %s" % (type(exc).__name__, exc)
            return []

    def _non_work_artifact_ids(self) -> list[str]:
        """Everything this round must not offer as work: research materials + her method drafts."""

        ids = list(self._research_material_ids())
        for text in self._practice_method_ids():
            if text and text not in ids:
                ids.append(text)
        return ids

    def _practice_query(self) -> str:
        """A bounded, deterministic thing-to-ask-about for B's own method search (no new service).

        It is the work this round selected, or the review option's own title, and nothing else: no
        model, no memory text, no second index.  B does the matching; this only names the subject.
        """

        title = str(getattr(self, "_round_work_title", "") or "").strip()
        if title:
            return title[:80]
        for option in self._round_project_options:
            if option.get("review"):
                return str(option.get("title") or "")[:80]
        return ""

    def practice_decision_context(self) -> dict[str, Any]:
        """The bounded practice slice of the decision prompt, or a named not-wired answer.

        Two halves, both real: the experiences A's Memory still returns for this subject, and the
        methods of hers that are relevant to what she is doing (each with its own handle and the
        head version it was read at).  The methods are *reference*; naming one authorises nothing.
        """

        if not self.practice.available() and not self.practice.memory_bound():
            return {"status": "NOT_WIRED", "experiences": [], "methods": [], "method_refs": []}
        view = self.practice.decision_context(query=self._practice_query())
        #: Only methods the owner still lets this round use become handles AG-1 may accept.  A
        #: retired method, or one whose evidence can no longer be read, stays in the workspace
        #: (and in the context's own ``withheld_methods``) but is never offered as usable here.
        self._round_methods = [dict(row) for row in self.practice.last_methods
                               if row.get("available", True)]
        if self.practice.last_error:
            self.last_practice_error = self.practice.last_error
        self._round_practice_context = view
        return view

    def practice_method_refs(self) -> list[str]:
        """The practice handles this round really offered (the Decision's allow-list)."""

        return [str(row.get("method_ref")) for row in self._round_methods if row.get("method_ref")]

    def practice_writing_context(self, artifact_id: Any = "") -> Optional[dict[str, Any]]:
        """The bounded practice document context one writing prompt may carry (or ``None``).

        Relevant methods with instruction / scope / uncertainty, plus -- when this very work is one
        of her reviews -- its question and the experiences it cited.  Never an experience provider,
        never a permission, never a claim that a method was executed.
        """

        if not self.practice.available():
            return None
        try:
            context = self.practice.writing_context(str(artifact_id or ""),
                                                    query=self._practice_query())
        except Exception as exc:  # a failed read is reported, never guessed
            self.last_practice_error = "%s: %s" % (type(exc).__name__, exc)
            return None
        self.last_practice_context = dict(context) if isinstance(context, Mapping) else None
        return self.last_practice_context

    def _next_content(self, point: Any, *, observed_at: str) -> str:
        """Ask the wired writer for the next text, adding document context when it accepts it.

        Both the AR-0 step path and the direct-port path go through here, so "the writing prompt
        really carries the sources and her own practices" is true wherever the content is produced.
        A writer that does not accept the keyword is called exactly as before -- nothing is
        smuggled through.  Research context and practice context stay *separate* keys, because one
        is a source of the work and the other is her own draft practice.
        """

        artifact_id = getattr(point, "artifact_id", "")
        research_context = self.research_writing_context(artifact_id)
        practice_context = self.practice_writing_context(artifact_id)
        next_content = self.writer.next_content
        if not accepts_context(next_content):
            return next_content(point, observed_at=observed_at)
        payload: dict[str, Any] = {}
        if research_context:
            payload["research_context"] = research_context
        if practice_context:
            payload["practice_context"] = practice_context
        earlier_versions = self._earlier_versions_context(artifact_id)
        if earlier_versions:
            payload["earlier_versions"] = earlier_versions
        if not payload:
            return next_content(point, observed_at=observed_at)
        return next_content(point, observed_at=observed_at, context=payload)

    def _earlier_versions_context(self, artifact_id: Any) -> list[dict[str, Any]]:
        """At most one **version-pinned** excerpt of this work's previous version (or ``[]``).

        This is a bounded, version-fixed look at what the same work said one version ago -- not
        history browsing, and it never replaces the current head body, which still reaches the
        writer through the resume point.  The read happens only when text is really being written,
        through the owner's ``continuation_excerpt`` (source gate included), and any failure --
        unwired port/workspace/grant, an owner refusal, a withheld source, an exception -- is
        simply "no earlier context this time": it never fails the writing step and never invents
        body text.
        """

        if not (self.artifact_port and self.workspace_id and self.artifact_grant_id):
            return []
        target = str(artifact_id or "").strip()
        if not target:
            return []
        try:
            service = self._project_service_for_earlier_versions()
            answer = service.continuation_excerpt(target, max_chars=600)
        except Exception:
            return []
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            return []
        excerpt = answer.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip():
            return []
        return [{
            "version_id": str(answer.get("version_id") or ""),
            "version_number": answer.get("version_number"),
            "excerpt": excerpt,
        }]

    def _project_service_for_earlier_versions(self) -> Any:
        """The cached project service used for the one bounded earlier-version read.

        Built lazily and reused; a failed construction leaves the cache empty so a later writing
        step may try again rather than this instance being poisoned for the rest of its life.
        """

        if self._earlier_project_service is None:
            import private_project_service
            self._earlier_project_service = private_project_service.PrivateProjectService(
                self.artifact_port, self.workspace_id, self.artifact_grant_id)
        return self._earlier_project_service

    def _named_work_ref(self, decision_record: Any) -> Optional[str]:
        """The artifact the committed Decision named, as an ``artifact:`` ref (or ``None``).

        A ``CONTINUE_CURRENT`` candidate names its work only through the **owner's own** target
        refs: its ``source_ref`` is the Activity id, not an artifact.  Reading it here is what
        makes the formal next step continue the object the running Activity was really started
        on instead of whatever this round happened to select as a fallback.  A candidate that
        names no work ref still answers ``None`` and the caller keeps its fallback.
        """

        candidate = self._selected_candidate_payload(decision_record)
        if not candidate:
            return None
        ref = str(candidate.get("source_ref") or "")
        if ref.startswith(lfi.ITEM_REF_NAMESPACE):
            item = next((row for row in self._round_future_items
                         if str(row.get("item_ref")) == ref), None)
            return self._future_item_artifact_ref(item or {})
        if ref.startswith("artifact:"):
            return ref
        if (str(candidate.get("candidate_kind") or "") == "CONTINUE_CURRENT"
                or str(candidate.get("source_kind") or "") == "CURRENT_ACTIVITY"):
            return pcb.work_ref_from_candidate(
                candidate, exclusions=self._artifact_exclusion_ids())
        return None

    def _artifact_exclusion_ids(self) -> set[str]:
        """The bare ids this instance must never treat as a work target."""

        return {ident for ident in (self._artifact_identity(ref)
                                    for ref in self.artifact_exclusions) if ident}

    def _work_adoption_spec_for_decision(self, decision_record: Any, *,
                                         fallback: Optional[ArtifactResumePoint]) -> dict[str, Any]:
        """The Activity shape for the work this Decision named -- never for a different one.

        A candidate that named an artifact the owner no longer calls resumable is a named refusal
        (``WORK_NOT_CONTINUABLE``), not a quiet retarget onto whatever else was bound.
        """

        named = self._named_work_ref(decision_record)
        if named:
            point = self.resume_point_for_artifact(named)
            if point is None:
                raise AutonomousLifeError(f"WORK_NOT_CONTINUABLE: {named}")
            return self._adoption_spec(point)
        if fallback is None:
            raise AutonomousLifeError("DECISION_NAMED_NO_WORK: nothing to adopt")
        return self._adoption_spec(fallback)

    def _work_step_spec_for_decision(self, decision_record: Any, *,
                                     fallback: Optional[ArtifactResumePoint],
                                     observed_at: str) -> Mapping[str, Any]:
        """The one real step: the work the Decision named, or the project it just created."""

        if self._decision_chose_project(decision_record):
            return self._project_step_spec(observed_at)
        named = self._named_work_ref(decision_record)
        if named:
            point = self.resume_point_for_artifact(named)
            if point is None:
                raise AutonomousLifeError(f"WORK_NOT_CONTINUABLE: {named}")
            return self._ar0_step_spec(point, observed_at=observed_at)
        if fallback is None:
            raise AutonomousLifeError("DECISION_NAMED_NO_WORK: no step to run")
        return self._ar0_step_spec(fallback, observed_at=observed_at)

    # -- zero-LLM material check -------------------------------------------------------

    def materials(self, *, now: Optional[str] = None) -> dict[str, Any]:
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

        # v0.4 day-life: a parked Activity with a *valid* LR-4 eligibility is material in its own
        # right -- exactly like a page left to read or a work with a head.  Without this, an
        # instance whose only real offer is "the thing she set down and can now come back to"
        # returned NO_MATERIAL before AG-0 ever ran, so the offer could never be considered.
        # The read is the same RESUME_ELIGIBLE source the Decision uses; it consumes nothing.
        found["parked_candidates"] = self.parked_resume_candidates(
            observed_at=now or getattr(self, "_round_observed_at", None))

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
                #: v0.4 C: the selected work's own title, so the practice search can be *about*
                #: what she is doing (B matches; this only names the subject).
                self._round_work_title = str(point.title or "")
            elif selection.get("error"):
                found["artifact_read_error"] = selection["error"]

        # D (life-v1): the reading material is material in its own right -- an instance whose
        # only real opportunity is a page to read must still be able to consider it.
        # rc1-C: the situation is reported whether or not a page is left, but only a material
        # with something to read on the *current* revision counts as material.  A finished
        # material is a normal state, not a reason to wake up and consider nothing every round.
        reading_situation = self.private_material_situation(refresh=True)
        if reading_situation is not None:
            found["private_materials"] = {
                "state": reading_situation.get("state"),
                "available": bool(reading_situation.get("available")),
                "exhausted": bool(reading_situation.get("exhausted")),
                "detail": reading_situation.get("detail"),
                "material": dict(reading_situation.get("material") or {}),
                "position": dict(reading_situation.get("position") or {}),
                "annotations": dict(reading_situation.get("annotations") or {}),
                "understanding": reading_situation.get("understanding"),
            }
            if reading_situation.get("available"):
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
        observed = self._refresh_world_opportunities(
            observed_at=now or getattr(self, "_round_observed_at", None))
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

        # v0.4 stage A: her own "later" and the project options she could start are material in
        # their own right.  A due item that nothing else covers is exactly the case where the
        # round must be able to happen at all -- and an unwired store/owner adds nothing.  The
        # round reads them once, at its own observed instant; any other caller reads right now.
        if now is None or now != getattr(self, "_round_observed_at", None):
            self._round_future_items = self.due_future_items(now)
            self._round_project_options = self.project_options(now=now)
        found["future_items"] = {
            "status": "WIRED" if self.future_items is not None else "NOT_WIRED",
            "due_count": len(self._round_future_items),
            "due_refs": [str(item.get("item_ref")) for item in self._round_future_items],
            "open_count": (self.future_items_view().get("open_count")
                           if self.future_items is not None else 0),
        }
        found["project_options"] = [str(option.get("option_ref"))
                                    for option in self._round_project_options]
        if self.project_gateway is not None:
            found["project_owner"] = self.project_gateway.availability()
        # v0.4 research: whether a workspace is wired, and what (bounded) the workspace says it
        # holds.  A material is not an *opportunity* by itself -- it becomes one only through a
        # question she wrote -- so this is report only and never `has_material`.
        found["research"] = {
            "workspace": self.research.availability(),
            "materials": [str(row.get("artifact_id")) for row in self.research.last_materials],
            "research_works": [str(row.get("artifact_id")) for row in self.research.last_research],
            "error": (self.last_research_error or self.research.last_error or None),
        }
        # v0.4 C: whether the practice workspace is wired, what it says it holds, and whether the
        # review option / a relevant method exists this round.  Refs and states only -- no text.
        found["practice"] = {
            "workspace": self.practice.availability(),
            "experiences": [str(row.get("experience_id")) for row in self.practice.last_experiences],
            "methods": [str(row.get("artifact_id")) for row in self.practice.last_methods],
            "method_refs": self.practice_method_refs(),
            "error": (self.last_practice_error or self.practice.last_error or None),
        }

        found["has_material"] = bool(
            found["foreground_activity"] or found["continuable_artifact"] or observed
            or found.get("reading_material") or self._round_future_items
            or self._round_project_options
            or int((found.get("parked_candidates") or {}).get("count") or 0) > 0)
        return found

    # -- v0.4 day-life: read-only step readiness + the bounded reconsideration ledger -----

    def open_activities(self) -> list[dict[str, Any]]:
        """The Activity owner's own OPEN activities (ACTIVE / PAUSED / WAITING), or ``[]``.

        Read from the owner's read port; a failed read is reported as an empty list *and* as an
        error, never guessed into "nothing is open".
        """

        reader = getattr(self.runtime, "activity_read", None)
        if reader is None:
            return []
        try:
            rows = list(reader.list_open_activities() or [])
        except Exception as exc:  # a read failure is reported, never guessed
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return []
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def _paused_activity_targets(self) -> set[str]:
        """Artifact ids that already have a PAUSED/WAITING Activity of their own.

        This is the seam the personal-opportunity source reads: such an object is *resume*'s
        business (LR-4 eligibility plus the owner's own RESUME path), so offering it as a fresh
        START would be a second Activity for the same object and would bypass that eligibility.
        """

        wanted: set[str] = set()
        for activity in self.open_activities():
            if str(activity.get("status") or "") not in {"PAUSED", "WAITING"}:
                continue
            for ref in self._activity_target_refs(activity):
                identity = self._artifact_identity(ref)
                if identity:
                    wanted.add(identity)
        return wanted

    @staticmethod
    def _activity_target_refs(activity: Mapping[str, Any]) -> list[str]:
        """Every ref an Activity's own record carries, in the order the owner wrote them."""

        refs = [str(ref) for ref in (activity.get("subject_refs") or []) if str(ref)]
        for key in ("target_ref", "checkpoint_ref", "origin_ref"):
            text = str(activity.get(key) or "")
            if text:
                refs.append(text)
        return refs

    @staticmethod
    def _activity_lineage_refs(activity: Mapping[str, Any]) -> list[str]:
        """Every ref the Activity's own record carries, including its adoption lineage.

        ``source_refs`` is where the Activity owner records the Decision/Adoption/candidate it was
        created from.  That lineage -- not ``subject_refs``, which is the *whole* target set the
        adoption was offered with -- is the only honest way to know which single target an Activity
        was adopted for.
        """

        refs: list[str] = []
        for key in ("source_refs", "subject_refs"):
            for ref in activity.get(key) or []:
                text = str(ref)
                if text and text not in refs:
                    refs.append(text)
        for key in ("target_ref", "checkpoint_ref", "origin_ref", "adoption_ref", "decision_ref"):
            text = str(activity.get(key) or "")
            if text and text not in refs:
                refs.append(text)
        return refs


    @staticmethod
    def _record_artifact_refs(activity: Mapping[str, Any]) -> list[str]:
        """Every artifact *object* the owner's own record names, single-valued fields first.

        ``target_ref``/``checkpoint_ref``/``origin_ref`` are one-value bindings the owner persists;
        ``subject_refs`` is the *whole* observed target set (that is how the completion contract was
        declared).  Ordering therefore follows the record's own specificity, and this is not
        "whichever ref happens to be listed first".  Revisions (``artifactversion:``) are dropped:
        a revision is not a target.  Both the owner's ``artifact:<id>`` and this tree's
        ``artifact:artifact:<id>`` wrapper fold to the owner's spelling, so the same object is
        stored, compared and looked up as one identity.
        """

        refs: list[Any] = [activity.get(key)
                           for key in ("target_ref", "checkpoint_ref", "origin_ref")]
        refs.extend(activity.get("subject_refs") or [])
        out: list[str] = []
        for ref in refs:
            canonical = lsr.canonical_target_ref(ref)
            if canonical and lsr.is_artifact_target(canonical) and canonical not in out:
                out.append(canonical)
        return out

    def _owner_record_target_ref(self, activity: Mapping[str, Any], *,
                                 reading_identity: Optional[str] = None
                                 ) -> Optional[str]:
        """The one target the owner's *own record* binds this Activity to, or ``None`` (unknown).

        This is the cross-process answer: an Activity's ``subject_refs``/``origin_ref``/``adoption``
        lineage lives in the Activity owner and outlives the process, while a candidate cache is an
        in-process read that can simply be gone after a rebuild.  A single distinct artifact object
        is an answer; several are only resolved by a single-valued owner field (a reading Activity
        names the material *and* its private-items document), and otherwise this yields ``None`` --
        never the first of a set, and never a ref from another namespace (a World Activity's
        ``origin_ref`` is its *place*, not its step target).
        """

        objects = self._record_artifact_refs(activity)
        if reading_identity:
            for text in objects:
                if self._artifact_identity(text) == reading_identity:
                    return text
        if not objects:
            return None
        if len({self._artifact_identity(item) for item in objects}) == 1:
            return objects[0]
        if str(activity.get("activity_kind") or "") in self.ARTIFACT_ACTIVITY_KINDS:
            for key in ("target_ref", "checkpoint_ref", "origin_ref"):
                canonical = lsr.canonical_target_ref(activity.get(key))
                if canonical and lsr.is_artifact_target(canonical):
                    return canonical
        return None

    def _readiness_target_ref(self, activity: Mapping[str, Any]) -> Optional[str]:
        """The one target this Activity was *adopted for*, read from its own record.

        A World adoption registers the whole observed target set as ``subject_refs``, so "the first
        ref with a known namespace" is not the Activity's target -- it is whoever happened to be
        listed first (for a body step, the place).  The owner's own single-valued bindings are read
        first, then the candidate the Activity itself names (the adoption lineage), and only when
        exactly one ref remains is that ref used.  An ambiguous record yields ``None`` (unknown,
        never a borrowed target), and a candidate is never *required*: after a rebuild the cache may
        be empty, and the target must still be the same one.
        """

        reading_identity = (self._artifact_identity(self.reading_artifact_id)
                            if self.reading_artifact_id else None)
        # 1. The owner's own persisted binding: the same answer before and after a rebuild.
        record_target = self._owner_record_target_ref(activity, reading_identity=reading_identity)
        if record_target:
            return record_target
        # 2. The candidate this Activity names (its freshest single choice) -- only when it names
        #    exactly one executable target.  A cache miss falls through instead of yielding None.
        for ref in self._activity_lineage_refs(activity):
            if not str(ref).startswith("cand:"):
                continue
            try:
                candidate = self.runtime.candidate_read.get_candidate(str(ref))
            except Exception as exc:  # a failed read is reported, never guessed
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                candidate = None
            if not isinstance(candidate, Mapping):
                continue
            targets: list[str] = []
            for item in (candidate.get("target_refs") or []):
                canonical = lsr.canonical_target_ref(item)
                if (canonical and lsr.namespace_of(canonical) is not None
                        and canonical not in targets):
                    targets.append(canonical)
            if not targets:
                continue
            if reading_identity:
                for text in targets:
                    if self._artifact_identity(text) == reading_identity:
                        return text
            if len(targets) == 1:
                return targets[0]
        known: list[str] = []
        for ref in self._activity_target_refs(activity):
            if lsr.namespace_of(ref) is not None and ref not in known:
                known.append(ref)
        if len(known) == 1:
            return known[0]
        if reading_identity:
            # A reading Activity names the material *and* its private-items document; the material is
            # this cycle's own bound object, so it is the target rather than an ambiguity.
            for text in known:
                if self._artifact_identity(text) == reading_identity:
                    return lsr.canonical_target_ref(text) or text
        return None

    def _readiness_revision(self, activity: Mapping[str, Any], target_ref: str) -> Optional[str]:
        """The source revision this target is at, read from the owner that already owns it.

        For the private material that is its *current* head version (so a re-versioned material is
        a different state); for a work it is the owner's own head version id; for a World target it
        is the World revision the last observation carried.

        The artifact owner's ids are spelled ``artifact:<hex>`` and its reads match that spelling
        exactly, so the ref is handed over in the *owner's* spelling (``_owner_artifact_ref``) --
        passing the bare identity back is a ``NOT_FOUND``, which would silently degrade every work
        state to "no revision".  A refusal is reported in ``last_error`` and answered ``None``
        (never a guessed revision).
        """

        namespace = lsr.namespace_of(target_ref)
        if namespace == lsr.TARGET_ARTIFACT:
            identity = self._artifact_identity(target_ref)
            if not identity:
                return None
            reading_identity = (self._artifact_identity(self.reading_artifact_id)
                                if self.reading_artifact_id else None)
            if reading_identity and identity == reading_identity:
                situation = self.private_material_situation()
                if isinstance(situation, Mapping):
                    material = situation.get("material")
                    if isinstance(material, Mapping) and material.get("version_id"):
                        return str(material["version_id"])
                return None
            if self.artifact_port is None:
                return None
            owner_ref = self._owner_artifact_ref(target_ref) or str(target_ref)
            try:
                reader = getattr(self.artifact_port, "read_head", None)
                head = reader(str(owner_ref)) if callable(reader) else None
                if isinstance(head, Mapping) and head.get("version_id"):
                    return str(head["version_id"])
                point = self.artifact_port.resume_point(str(owner_ref))
                head_id = getattr(point, "head_version_id", None)
                return str(head_id) if head_id else None
            except Exception as exc:  # an unreadable owner is reported, never guessed
                self.last_error = ("readiness revision read failed for %s: %s: %s"
                                   % (owner_ref, type(exc).__name__, exc))
                return None
        if namespace in {lsr.TARGET_PLACE, lsr.TARGET_OBJECT, lsr.TARGET_BODY_ACTION,
                         getattr(lsr, "TARGET_DAILY_ACTION", "dailyact:")}:
            observation = self.last_world_observation or {}
            revision = observation.get("world_revision")
            return None if revision is None else str(revision)
        return None

    def step_readiness(self, *, act: bool = True,
                       activity: Optional[Mapping[str, Any]] = None
                       ) -> "lsr.StepReadiness":
        """Read-only: does the foreground Activity still have one executable step?

        One owner read (the Activity record) plus at most one revision read through the owner that
        already owns the target.  No model call, no candidate, no write.  ``act=False`` is a fact
        about *this caller* (it may not execute anything), so it is never turned into "no step".
        """

        record = (dict(activity) if isinstance(activity, Mapping)
                  else self._foreground_activity_record())
        #: The record this readiness was computed from, so the round's situation parts and this
        #: judgement can never be about two different Activities.
        self._last_foreground_record = record
        target_ref = self._readiness_target_ref(record) if record else None
        revision = (self._readiness_revision(record, target_ref)
                    if (record and target_ref) else None)
        reading_state = None
        no_change = None
        if target_ref and lsr.namespace_of(target_ref) == lsr.TARGET_ARTIFACT:
            identity = self._artifact_identity(target_ref)
            reading_identity = (self._artifact_identity(self.reading_artifact_id)
                                if self.reading_artifact_id else None)
            if identity and reading_identity and identity == reading_identity:
                reading_state = self.private_material_situation()
            elif identity:
                no_change = self.reconsideration.no_change(target_ref, revision)
        readiness = lsr.assess_step_readiness(
            activity=record, target_ref=target_ref, source_revision=revision,
            reading_state=reading_state, no_change=no_change,
            world_step_enabled=self._world_step_enabled(), caller_can_act=bool(act))
        self.last_step_readiness = readiness.to_dict()
        return readiness

    def _foreground_activity_record(self) -> Optional[dict[str, Any]]:
        """The owner's own record for the Activity it holds in the foreground, or ``None``."""

        reader = getattr(self.runtime, "activity_read", None)
        ref = self._foreground_activity_ref()
        if reader is None or not ref:
            return None
        try:
            record = reader.get_activity(str(ref))
        except Exception as exc:  # a read failure is reported, never guessed
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return None
        return dict(record) if isinstance(record, Mapping) else None

    def _situation_parts(self, *, material: Optional[Mapping[str, Any]],
                         readiness: "lsr.StepReadiness") -> dict[str, Any]:
        """The narrow, owner-fact-only parts of this round's situation.

        Deliberately no timestamps, no validity windows, no opportunity ids and no raw World
        revision: a World tick that changes nothing she can perceive is not a new situation, while a
        new page, head, place, pose or opportunity *target* is.  Everything here is read from an
        owner (the Activity record, the private-material situation, the resume point, the World
        observation) -- nothing is inferred from a clock.
        """

        activity = self._last_foreground_record
        parts: dict[str, Any] = {
            "foreground_activity_ref": ((activity or {}).get("activity_ref")
                                        or (activity or {}).get("activity_id")),
            "foreground_status": (activity or {}).get("status"),
            "foreground_kind": (activity or {}).get("activity_kind"),
            "target_ref": readiness.target_ref,
            # Only an *artifact* revision is a state change (a new head is new content).  A World
            # revision is a tick counter: what she could act on is already in place/pose/
            # orientation/opportunities, so a bare tick must not reopen a settled question.
            "target_revision": (readiness.source_revision
                                if lsr.namespace_of(readiness.target_ref or "") == lsr.TARGET_ARTIFACT
                                else None),
            "step_reason": readiness.reason_code,
        }
        if activity:
            plan = self._stay_plan_from_activity(activity) or {}
            parts["stay_target"] = plan.get("target_ref")
        situation = self.private_material_situation()
        if isinstance(situation, Mapping):
            reading = situation.get("material")
            position = situation.get("position")
            parts["reading_state"] = situation.get("state")
            if isinstance(reading, Mapping):
                parts["reading_revision"] = reading.get("version_id")
            if isinstance(position, Mapping):
                parts["reading_next_page"] = position.get("next_page")
        work = material.get("continuable_artifact") if isinstance(material, Mapping) else None
        if isinstance(work, Mapping):
            parts["work_target"] = work.get("artifact_id")
            parts["work_revision"] = work.get("head_version_id")
        observation = self.last_world_observation or {}
        parts["place"] = (observation.get("location") or {}).get("place_id")
        parts["pose"] = observation.get("pose")
        parts["orientation"] = observation.get("orientation")
        parts["opportunities"] = [
            str(ref) for item in (self.last_world_opportunities or [])
            for ref in (item.get("target_refs") or [])]
        # v0.4 stage A: which of her own items the clock has reached, and which project options
        # this round could offer.  Both are *state*, so a newly due item or a newly supported
        # option is a new situation (it reopens a settled question) while the same set stays
        # quiet -- the bounded silence this loop already had, applied to what she herself planned.
        parts["due_items"] = [str(item.get("item_ref")) for item in self._round_future_items]
        parts["project_options"] = [str(option.get("option_ref"))
                                    for option in self._round_project_options]
        # v0.4 research: what the workspace really holds -- the readable materials *at their
        # revisions* and the research heads *with the source checks the owner reported*.  A
        # re-imported / archived / unreadable material or a new research head is a new situation,
        # and a mere clock tick is not (nothing here is time-derived, and no text enters).
        for key, value in self.research.fingerprint_parts().items():
            parts[key] = value
        # v0.4 C: what her practice workspace really holds -- the recallable experiences (id + when +
        # outcome word), the methods (id + head + state) and the recent reviews (id + head).  A new
        # experience, a withdrawn one, a re-written method or a method moving to TRIAL/CONTESTED is a
        # new situation; nothing here is time-derived and no text enters (ids, heads, states only).
        for key, value in self.practice.fingerprint_parts().items():
            parts[key] = value
        # v0.4 B: who is at the door, and whether a joint time is open.  Counts, the invitation's
        # own scope label and the session state only -- no message body, no private material.  A
        # new arrival or a session opening/closing is a new situation; an idle tick is not (no
        # clock enters here), which is the bounded silence this loop already had.
        parts["contact"] = self.contact_parts()
        # v0.4 B: an eligibility the owner just opened is a *new* situation -- it reopens a
        # question this loop may already have settled (a decline recorded before the eligibility
        # existed must not silence the resume).  Ids only, bounded, from LR-4's own store.
        parts["resume_eligibility_refs"] = self.open_resume_refs()
        return parts

    def contact_parts(self) -> dict[str, Any]:
        """The bounded local-contact view this round may see (or a named "no seam")."""

        port = self.social_contacts
        if port is None:
            return {"available": False, "reason_code": "NO_LOCAL_CONTACT_SEAM"}
        try:
            return dict(port.cognition_block())
        except Exception as exc:  # a read failure is reported, never guessed
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return {"available": False, "reason_code": "LOCAL_CONTACT_VIEW_FAILED"}

    def poll_local_contacts(self, *, limit: Optional[int] = None) -> dict[str, Any]:
        """One bounded local-contact pass -- the cycle's own entry to the resident's step.

        ``runner_tick`` already drives this; the method exists so a host that drives the cycle
        directly (a drill, the CLI) uses the *same* pass instead of inventing one.
        """

        port = self.social_contacts
        if port is None:
            return {"ok": False, "reason_code": "NO_LOCAL_CONTACT_SEAM", "processed": [],
                    "llm_calls": 0}
        return dict(port.tick(limit=limit))

    def parked_resume_candidates(self, *, observed_at: Optional[str] = None) -> dict[str, Any]:
        """Read-only: which parked Activities LR-4 would currently let her resume.

        This is the *same* AG-0 source the Decision materializes (``RESUME_ELIGIBLE``), asked
        one step earlier: a due check window whose only real offer is "a thing she set down and
        can now come back to" must be able to see it *before* the quiet gate decides that
        nothing is on offer.  The source applies LR-4's own predicates (OPEN, not expired,
        WAITING/PAUSED, matching revision/episode, no foreground held elsewhere), so this adds
        no second eligibility system, consumes nothing, resumes nothing and writes nothing.

        A source that is unwired, disabled or fails to read is reported by name and yields
        **zero** material: "cannot prove it is resumable" is never counted as an offer.
        """

        obs = observed_at or getattr(self, "_round_observed_at", None) or self._now_iso()
        base: dict[str, Any] = {
            "source_kind": ag2.SOURCE_RESUME_ELIGIBLE,
            "available": False,
            "count": 0,
            "refs": [],
            "activity_refs": [],
        }
        registry = getattr(self.runtime, "candidate_registry", None)
        getter = getattr(registry, "get_adapter", None)
        adapter: Any = None
        if callable(getter):
            try:
                adapter = getter(ag2.SOURCE_RESUME_ELIGIBLE)
            except Exception as exc:
                base["reason_code"] = "RESUME_SOURCE_NOT_WIRED"
                base["detail"] = "%s: %s" % (type(exc).__name__, exc)
                return base
        if adapter is None:
            base["reason_code"] = "RESUME_SOURCE_NOT_WIRED"
            return base
        enabled = getattr(adapter, "is_enabled", None)
        if callable(enabled):
            try:
                if not enabled():
                    base["reason_code"] = "RESUME_SOURCE_DISABLED"
                    return base
            except Exception as exc:
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                base["reason_code"] = "RESUME_SOURCE_DISABLED"
                base["detail"] = self.last_error
                return base
        discover = getattr(adapter, "discover", None)
        if not callable(discover):
            base["reason_code"] = "RESUME_SOURCE_WITHOUT_READ"
            return base
        try:
            items, snapshot_ref, meta = discover(observed_at=obs)
        except Exception as exc:  # a failed owner read is reported, never guessed around
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            base["reason_code"] = "RESUME_SOURCE_READ_FAILED"
            base["detail"] = self.last_error
            return base
        refs: list[str] = []
        activity_refs: list[str] = []
        for row in items:
            if not isinstance(row, Mapping):
                continue
            eligibility_id = str(row.get("eligibility_id") or "")
            if not eligibility_id:
                continue
            refs.append(eligibility_id)
            activity_ref = str(row.get("activity_id") or "")
            if activity_ref and activity_ref not in activity_refs:
                activity_refs.append(activity_ref)
        base.update({
            "available": True,
            "reason_code": "READ_OK",
            "count": len(refs),
            "refs": refs,
            "activity_refs": activity_refs,
            "snapshot_ref": (snapshot_ref if isinstance(snapshot_ref, str) else None),
            "discovered": int((meta or {}).get("discovered") or 0),
        })
        return base

    def open_resume_refs(self) -> list[str]:
        """The resume eligibilities LR-4 currently reports OPEN -- ids only, bounded."""

        try:
            state = self.runtime.eligibility_store.load_state() or {}
        except Exception as exc:  # a read failure is reported, never guessed
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return []
        rows = state.get("eligibilities") or {}
        refs = [str(key) for key, row in rows.items()
                if isinstance(row, Mapping) and str(row.get("status")) == "OPEN"]
        return sorted(refs)[:6]

    def reconsideration_block(self, *, material: Optional[Mapping[str, Any]] = None,
                              readiness: Optional["lsr.StepReadiness"] = None,
                              observed_at: Optional[str] = None) -> dict[str, Any]:
        """Should this round skip the request because this *whole* situation was already declined?

        The gate is the situation fingerprint, not "is something visible": a visible World
        opportunity is part of the identity, so a re-observed identical set stays quiet while a real
        new one reopens the question.  Nothing here hides the World, disables an owner or replays a
        Decision.
        """

        view = readiness if readiness is not None else self.step_readiness(act=True)
        parts = self._situation_parts(material=material, readiness=view)
        self._round_situation = parts
        block = self.reconsideration.block_situation(parts=parts, at=observed_at)
        self.last_reconsideration = dict(block)
        return block

    def _material_names_identity(self, material: Optional[Mapping[str, Any]],
                                 identity: str) -> bool:
        """Does the material set name this artifact as its reading/work opportunity?"""

        if not isinstance(material, Mapping):
            return False
        refs: list[str] = []
        reading = material.get("reading_material")
        if isinstance(reading, Mapping) and reading.get("artifact_id"):
            refs.append(str(reading["artifact_id"]))
        artifact = material.get("continuable_artifact")
        if isinstance(artifact, Mapping) and artifact.get("artifact_id"):
            refs.append(str(artifact["artifact_id"]))
        for ref in refs:
            if self._artifact_identity(ref) == identity:
                return True
        return False

    def release_reconsideration(self, *, refs: Sequence[Any] = (),
                                reason: str = "EXPLICIT") -> dict[str, Any]:
        """Lift the suppression for the refs an explicit reason names (an event, an operator).

        Only entries whose own ``target_ref``/``activity_ref`` is one of ``refs`` are dropped, so an
        unrelated event cannot be used to turn a quiet state back into a paid request.
        """

        try:
            released = self.reconsideration.release(refs=refs)
        except OSError as exc:  # a failed write is reported, never hidden
            return {"released": [], "reason": reason,
                    "error": "%s: %s" % (type(exc).__name__, exc)}
        return {"released": released, "reason": reason}

    # -- the cycle ---------------------------------------------------------------------
    # -- business time -----------------------------------------------------

    def _now(self) -> datetime:
        """本域的业务 now。普通路径就是系统 UTC 墙钟；演示模式是压缩逻辑时间。"""

        clock = self._business_clock
        if clock is None:
            return datetime.now(timezone.utc)
        return clock()

    def _now_iso(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="milliseconds")

    def evaluate_once(
        self,
        *,
        trigger_ref: str,
        trigger_kind: str = TRIGGER_IDLE_WINDOW,
        observed_at: Optional[str] = None,
        act: bool = True,
    ) -> dict[str, Any]:
        """One real evaluation.  Returns a compact, honest report; never raises for a refusal.

        The round's read memo is opened and closed here, not only in ``_finish`` or in the
        runner's ``finally``.  A caller that drives ``evaluate_once`` directly -- a probe, a
        tool, a test -- gets the same guarantee as the resident: a round that raises, exits
        early or returns normally always drops the memo, so the next wake (even one carrying
        the same instant) reads the owners again.
        """

        try:
            return self._evaluate_once_round(
                trigger_ref=trigger_ref, trigger_kind=trigger_kind,
                observed_at=observed_at, act=act)
        finally:
            # ``_finish`` already ends the round on every normal exit; ending it again is a
            # no-op and makes the exception path explicit.
            self.end_round_reads()

    def _evaluate_once_round(
        self,
        *,
        trigger_ref: str,
        trigger_kind: str = TRIGGER_IDLE_WINDOW,
        observed_at: Optional[str] = None,
        act: bool = True,
    ) -> dict[str, Any]:
        obs = observed_at or self._now_iso()
        base: dict[str, Any] = {"trigger_kind": trigger_kind, "trigger_ref": trigger_ref,
                                "observed_at": obs}
        # rc1-C: one round, one instant.  The read-only material situation is cached for this
        # round only (the candidate, the step plan and the facts must not disagree inside one
        # round, and the next round must see whatever the owners then say).
        self.begin_round_reads(obs)
        self._reading_situation_cache = None
        # v0.4 day-life: the round's own readiness is reset here, so an early exit below can never
        # record the previous round's state into the reconsideration ledger.
        self._round_readiness = None
        self._last_foreground_record = None
        self._round_situation = None
        self._round_work_binding = None
        self._round_park_activity_ref = None
        # v0.4 C: per-round report state.  ``last_wait_basis`` is recomputed from this round's
        # owner reads only, and ``last_correlation_id`` names the round a step plan belongs to.
        self.last_wait_basis = None
        # v0.4 A (pacing): the body breakpoint is recomputed from this round's own body read too.
        self.last_body_wait_basis = None
        self.last_body_wait_reason = None
        self.last_correlation_id = None
        # v0.4 stage A: her own "later" and the options it supports are read *once* per round, at
        # this round's own observed instant -- never at the wall clock of a later read.  The
        # projection reads in ``materials()`` are reused for this round, so the candidate set and
        # the report can never be about two different instants.
        self._round_observed_at = obs
        #: v0.4 C: this round's own practice state.  Reset *before* anything reads it, so no method
        #: handle is legal unless this round offered it and no verdict is pending on entry.
        self._round_methods = []
        self._round_practice_context = None
        self._round_work_title = ""
        self._pending_method_usage = None
        self._pending_method_trial = None
        self.last_review_created = None
        self.last_method_created = None
        self.last_method_usage = None
        self.last_method_trial = None
        self._round_future_items = self.due_future_items(obs)
        self._round_project_options = self.project_options(now=obs)

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

        material = self.materials(now=obs)
        base["material"] = material
        # v0.4 stage A: what the round can see of her own "later" and of the projects she could
        # start.  Report only -- the candidates themselves come from the owner-backed source.
        base["future_items"] = dict(material.get("future_items") or {})
        base["project_options"] = [dict(option) for option in self._round_project_options]
        base["project_owner"] = (self.project_gateway.availability()
                                 if self.project_gateway is not None else {"status": "NOT_WIRED"})
        if not material["has_material"]:
            # No round is charged: nothing was even asked.
            return self._finish(base, OUTCOME_NO_MATERIAL, llm_calls=0,
                                detail="no real material to evaluate; no model call made")

        # v0.4 day-life: read-only step readiness for the foreground Activity.  Two owner reads at
        # most, no request: it is what this round hands AG-1 as the *sourced* fact
        # ``current_step_unavailable_true`` (which withdraws the current CONTINUE and nothing else),
        # and it is what decides whether this exact state is worth asking about again at all.
        self._round_readiness = self.step_readiness(act=act)
        base["step_readiness"] = self._round_readiness.to_dict()
        # v0.4 C: the one dependency this round may legally wait on -- derived from the readiness
        # the owner just answered (a named no-step reason plus the owner's current revision) and
        # from nothing else -- or, when a *running* Activity may be set down until a time she names,
        # The park basis (if any) is the one dependency this round may legally wait on: the
        # *running* Activity, set down until a time the Decision itself names.  ``None`` here means
        # WAIT stays outside AG-1's legal set for this round (no "maybe it changes later" basis is
        # ever invented).
        wait_basis = self.wait_basis(readiness=self._round_readiness)
        park = None if wait_basis else self.park_basis_for_round(observed_at=obs, act=act)
        # Report the offer as an offer; the basis itself is reported under ``wait_basis``.
        base["park"] = ({"available": True, "activity_ref": park.get("activity_ref"),
                         "basis_ref": park.get("basis_ref")}
                        if isinstance(park, Mapping) else {"available": False})
        if park:
            wait_basis = park
            # Remembered for the *write*: by the time the Decision's "later" item is recorded, the
            # owner has already released the foreground, so this is the only honest Activity ref.
            self._round_park_activity_ref = str(park.get("activity_ref") or "") or None
        base["wait_basis"] = dict(wait_basis) if wait_basis else None
        # The WAIT spec is built from the committed Decision when the basis is a self-chosen park
        # (the instant lives in that Decision's own proposal); a dependency wait keeps its static
        # spec byte for byte.
        if wait_basis is None:
            waiting_spec: Any = None
        elif str(wait_basis.get("source") or "") == lfi.PARK_SOURCE:
            waiting_spec = (
                lambda decision, _basis=wait_basis: lfi.park_wait_spec_for_decision(
                    decision, observed_at=obs, activity_ref=_basis.get("activity_ref")))
        else:
            waiting_spec = lwb.waiting_spec(wait_basis)
        block = self.reconsideration_block(material=material, readiness=self._round_readiness,
                                           observed_at=obs)
        base["reconsideration"] = block
        if block["blocked"]:
            # This exact state -- same target, same revision, same no-step reason -- was already
            # considered and she did not move it, and nothing else is on offer this round.  No
            # request is spent.  This is silence: not a choice she made, not a completion, and the
            # Activity stays exactly as its owner has it.
            entry = block.get("entry") or {}
            return self._finish(
                base, OUTCOME_QUIET, llm_calls=0,
                detail="this exact state was already considered at %s (%s); no request was spent"
                       % (entry.get("last_at"), block.get("reason_code")))

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
        if resume_point is not None:
            # The work this round *selected*, with the owner's own head: a ``no_change`` raised while
            # the START is still being adopted has no Activity to bind to yet, and this is the only
            # honest target/revision it can be recorded against.
            self._round_work_binding = {
                "target_ref": "artifact:%s" % self._artifact_identity(resume_point.artifact_id),
                "source_revision": (str(resume_point.head_version_id)
                                    if resume_point.head_version_id else None)}

        # WAIT the Activity is ``WAITING``: LR-2 keeps the foreground *pointer* on it (she will come
        # back to it), but the slot itself is free -- AG-2 accepts a new Activity there, which is
        # exactly what "park it and do something else" means.  Treating the pointer alone as
        # "busy" hid every World step (and the reading/closeout step) for the whole parked stretch,
        # so the only thing a parked round could do was continue the *work*.
        # v0.4 background pause: a successful PAUSE now clears the pointer and marks the PAUSED
        # record ``foreground_released``, so the slot is free while the Activity stays suspended.
        # ``ACTIVE`` still occupies it; a *legacy* PAUSED record whose pointer predates the rule
        # also still occupies it (nothing migrates it in a read), and an unread status keeps the
        # old pointer rule.  The cycle reads the pointer, so it needs no second status list: the
        # owner is the one that decides whether a pointer exists.
        foreground_record = self._last_foreground_record or {}
        foreground_status = str(foreground_record.get("status") or "")
        has_foreground = ((foreground_status != "WAITING") if foreground_status
                          else bool(material.get("foreground_activity")))
        artifact_available = resume_point is not None
        world_available = bool(self.last_world_opportunities)
        #: The work object the *running* Activity itself names (its owner's own target refs), or
        #: ``None``.  It is a projection, not a permission: the step builder still asks the owner
        #: for a resumable point and refuses by name when it has none.  Reported for the round.
        foreground_work_ref = (pcb.work_ref_from_activity(
            foreground_record, exclusions=self._artifact_exclusion_ids())
            if has_foreground else None)
        base["foreground_work_ref"] = foreground_work_ref

        # rc1-C: is the *foreground* Activity this material's page read?  Asked of the Activity
        # owner itself (``_foreground_reading_activity``), never inferred from the fact that
        # some activity exists.  When it is, CONTINUE must mean "read the next page of this
        # material" -- and nothing else may be executed in its place.
        foreground_reading = self._foreground_reading_activity() if act else None
        if foreground_reading is not None:
            base["foreground_reading_activity_ref"] = (
                foreground_reading.get("activity_ref") or foreground_reading.get("activity_id"))

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
        # v0.4 C: the one work this round may *offer* to wrap up (W's plan, the bound work, an
        # ACTIVE owner record and a free foreground slot -- see ``_closeout_point``).  It is a
        # candidate, never an action: only a committed Decision that selects it can archive
        # anything, and while an Activity holds the foreground nothing is switched away from.
        closeout_point = self._closeout_point() if act else None
        base["closeout"] = ({key: closeout_point.get(key)
                             for key in ("state", "artifact_ref", "target_ref", "lifecycle")}
                            if closeout_point else self.closeout_state())
        closeout_available = bool(closeout_point and closeout_point.get("state") == "READY")
        # v0.4 stage A: a *new private project* is a fourth kind of new work she may start, and
        # only when the owner service is really wired -- an option nobody could create is not a
        # choice.  Choosing one performs the one owner write (see ``_project_adoption_spec``).
        #: v0.4 C: a review option is created by the practice workspace, so it counts as a live
        #: adoption path on its own (the same way the research option does).
        project_available = bool(self._round_project_options) and (
            self._project_owner_ready() or self.practice_available())
        live_adoption_paths = sum(1 for flag in (artifact_available, world_available,
                                                 reading_available, closeout_available,
                                                 project_available) if flag)

        if not has_foreground and live_adoption_paths:
            # A work shape is described for *the candidate that won*: several of her works can be
            # offered at once, so the one bound at start-up can no longer decide the round by
            # itself.  The fallback is the round's own selection, used only when the Decision
            # named no artifact at all.
            work_spec: Any = _DecisionBoundAdoptionSpec(
                lambda decision: self._work_adoption_spec_for_decision(decision,
                                                                      fallback=resume_point))
            closeout_spec = (lambda _decision: self._closeout_adoption_spec(closeout_point)
                             ) if closeout_available else None
            project_spec = ((lambda decision: self._project_adoption_spec(decision))
                            if project_available else None)
            routed_kwargs: dict[str, Any] = dict(
                world=lambda decision: self._world_adoption_spec(
                    self.last_world_opportunities, decision=decision),
                is_world=self._decision_chose_world,
                reading=lambda decision: self._reading_adoption_spec(),
                is_reading=self._decision_chose_reading,
                closeout=closeout_spec,
                is_closeout=self._decision_chose_closeout,
                project=project_spec,
                is_project=self._decision_chose_project)
            if live_adoption_paths > 1:
                adoption_spec: Any = _RoutedAdoptionSpec(
                    artifact=(work_spec if artifact_available
                              else self._reading_adoption_spec()),
                    **routed_kwargs)
            elif closeout_available or project_available:
                # Only one of these is on offer: the shape is still bound to the Decision, so a
                # Decision that names something else cannot register that Activity.
                adoption_spec = _RoutedAdoptionSpec(
                    artifact=self._reading_adoption_spec(), **routed_kwargs)
            elif reading_available and not artifact_available:
                adoption_spec = self._reading_adoption_spec()
            elif artifact_available:
                adoption_spec = work_spec
            else:
                # B6-5: the Activity shape is derived from the observed opportunity *set*,
                # never from a chosen destination: choosing is AG-1's job and it has not
                # happened yet at this point.
                adoption_spec = _DecisionBoundAdoptionSpec(
                    lambda decision: self._world_adoption_for_decision(
                        self.last_world_opportunities, decision))
        else:
            adoption_spec = None

        # When a real AR-0 adapter is wired, the single step goes through AR-0: proposal, the
        # durable submission fence, an attempt, the owner's receipt, and reconciliation by the
        # original operation.  Without one, nothing crosses a submission fence at all.
        ar0_step = None
        if (act and self._ar0_enabled() and foreground_reading is None
                and (resume_point is not None or artifact_available or project_available
                     # The running Activity may name a work object even when this round's own
                     # selection (bound work / history / latest) points elsewhere or at nothing.
                     # The step builder asks the owner for a resumable point and refuses by name.
                     or foreground_work_ref is not None)):
            # Lazy on purpose: AG-2 materialises this override only *after* the decision, so the
            # content model call happens when a step will really be executed -- never for a
            # NO_ACTION or DEFER round.  A writer failure surfaces from that materialisation.
            # v0.4 stage A: bound to the Decision, so the step belongs to the work the Decision
            # actually named (multi-project) and to the work the owner just created (project).
            ar0_step = _DecisionBoundStepSpec(
                lambda decision: self._work_step_spec_for_decision(decision, fallback=resume_point,
                                                                   observed_at=obs))

        # v0.4 C: the closeout step.  Built by W's pure plan *after* the Decision, and only for a
        # Decision whose own candidate named the closeout target -- this cycle never re-plans the
        # wrap-up and never lets another path's payload run under a closeout Activity.
        closeout_step = None
        if closeout_available and act and not has_foreground and self._ar0_enabled():
            closeout_step = _DecisionBoundStepSpec(
                lambda decision: self._closeout_step_spec(closeout_point, decision_record=decision,
                                                          observed_at=obs))

        # D (life-v1): the reading step.  Its own owner action: read one page from the material
        # and commit the bookmark, both through the existing port and owner.
        reading_step = None
        if (act and reading_available and self._ar0_enabled()
                and (not has_foreground or foreground_reading is not None)):
            reading_step = _DecisionBoundStepSpec(
                lambda _decision: self._reading_step_spec(observed_at=obs))

        # B6-5: the World step.  Same seam, same laziness, one difference -- a MOVE's shape
        # depends on *which* place the Decision chose, so this override is a callable of the
        # committed Decision rather than a fixed Mapping.
        world_step = None
        if act and not has_foreground and self._world_step_enabled() and world_available:
            # A Decision that selected something other than a World candidate is refused by the
            # step builder itself (``_world_step_for_decision``), before any shape is described.
            world_step = _DecisionBoundStepSpec(self._world_step_for_decision)

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
                                                  ("CLOSEOUT", closeout_step),
                                                  ("WORLD", world_step)) if spec is not None]
            if len(live_paths) > 1:
                step_spec = _DecisionBoundStepSpec(
                    self._routed_step_builder(resume_point, observed_at=obs,
                                              closeout=closeout_point))
                base["step_path"] = ("AR0_WORLD_ROUTED" if set(live_paths) == {"AR0", "WORLD"}
                                     else "%s_ROUTED" % "_".join(live_paths))
            elif live_paths == ["AR0"]:
                step_spec = ar0_step
                base["step_path"] = "AR0"
            elif live_paths == ["READING"]:
                step_spec = reading_step
                base["step_path"] = "READING_AR0"
            elif live_paths == ["CLOSEOUT"]:
                step_spec = closeout_step
                base["step_path"] = "CLOSEOUT_AR0"
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

        # v0.4 A (pacing): the *body*-conditioned breakpoint, computed from the same situation
        # read this round already made (so no second body read and no disagreement with the
        # capacity AG-1 gets).  It fills the breakpoint only when no *dependency* basis is on
        # offer, and it takes precedence over a self-chosen park: "until my body eases" is a
        # fact somebody can check, while a chosen instant is not.  Nothing resumes here.
        body_basis: Optional[dict[str, Any]] = None
        if wait_basis is None or str(wait_basis.get("source") or "") == lfi.PARK_SOURCE:
            body_basis = self.body_wait_basis(observed_at=obs)
        if body_basis is not None:
            superseded = str((wait_basis or {}).get("source") or "")
            wait_basis = body_basis
            park = None
            self._round_park_activity_ref = None
            waiting_spec = lwb.body_waiting_spec(body_basis)
            base["wait_basis"] = dict(body_basis)
            base["park"] = {"available": False, "superseded_by": lwb.SOURCE_BODY_REST}
            if superseded:
                base["park_superseded_source"] = superseded
        base["body_breakpoint"] = {
            "available": bool(self.last_body_wait_basis),
            "why_not": self.last_body_wait_reason,
            "waiting_on_ref": ((self.last_body_wait_basis or {}).get("waiting_on_ref")
                               if self.last_body_wait_basis else None),
        }

        # v0.4 explicit-condition PAUSE: the *same* basis this round already offers for WAIT is
        # the only condition a PAUSE may name.  The spec is the existing waiting spec with the
        # basis ref added so AG-2 can re-check that the committed Decision quoted exactly this
        # offer.  A round with no basis gets ``None``: unconditional PAUSE stays legal, but a
        # Decision that names a condition is refused rather than given an invented one.
        pause_condition_spec: Any = None
        if wait_basis is not None and str(wait_basis.get("basis_ref") or ""):
            _pause_basis_ref = str(wait_basis["basis_ref"])
            _pause_source = waiting_spec
            if callable(_pause_source):
                def pause_condition_spec(decision, _source=_pause_source,
                                         _ref=_pause_basis_ref):
                    spec = dict(_source(decision) or {})
                    spec["basis_ref"] = _ref
                    return spec
            else:
                _pause_static = dict(_pause_source or {})
                _pause_static["basis_ref"] = _pause_basis_ref
                def pause_condition_spec(decision, _spec=_pause_static):
                    return dict(_spec)

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
        # rc1-C: her own private material situation travels through the *same* bounded channel
        # (``observed_facts`` -> AG-1's ``observed_fact_keys`` -> the production prompt), spelled
        # as ASCII keys that carry the page numbers themselves.  It is a fact, not a candidate
        # and not a permission: no new kind, no Memory prior, and no page text ever leaves here.
        material_facts = self.private_material_facts()
        decision_facts = private_materials_module().merge_decision_facts(
            world_facts, material_facts)
        # v0.4 day-life: the readiness fact goes through the *same* bounded channel, with priority:
        # it is one key, and a place dump must not be able to crowd out "this Activity has no step
        # left".  AG-1 withdraws exactly the current CONTINUE on it and nothing else
        # (``agency_decision_ag1.observed_fact_is_true`` requires the boolean ``True``).
        readiness_facts = self._round_readiness.fact_keys() if self._round_readiness else {}
        if readiness_facts:
            decision_facts = lsr.merge_priority_facts(decision_facts, readiness_facts)
            base["step_readiness_facts"] = sorted(readiness_facts)
        if material_facts:
            base["private_material_facts"] = sorted(material_facts)
        if len(decision_facts) < (len(world_facts) + len(material_facts)):
            # Whatever the 24-key budget could not carry is reported, never silently implied.
            base["decision_facts_dropped"] = sorted(
                (set(world_facts) | set(material_facts)) - set(decision_facts))
        base["appraisal"] = self._compact_appraisal(self.last_appraisal)
        #: v0.4 C: the situation is built *first* and held, because building it is what reads the
        #: practice slice (and therefore what decides which method handles this round offers).  The
        #: allow-list below must be the same read, not a second one that could disagree.
        situation_context = self.decision_situation_context(
            now=obs, park=park, wait_basis=wait_basis)
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
                # v0.4 stage A: the bounded present situation (her open "later" items with their
                # real refs, the project options of this round with a seed excerpt, what she is
                # reading, the question she wrote, whether parking is on offer, and the running
                # Activity's own step state).  Data only: it authorizes nothing and no owner verb
                # is reachable from it.
                situation_context=situation_context,
                # v0.4 C: the practice handles this round really offered.  AG-1 refuses a
                # ``method_ref`` (or a trial's) that is not in this list -- an invented handle is an
                # invented identifier, exactly like an invented candidate.  It grants nothing.
                allowed_method_refs=self.practice_method_refs(),
                observed_facts=decision_facts,
                # v0.4 C: AG-1 may offer WAIT only with this reference, and AG-2 adopts it only
                # with a concrete waiting_spec.  Both are `None` unless a named dependency of the
                # current Activity was really read this round (``wait_basis``).  A WAIT still
                # spends one decision request and produces no content.
                known_waiting_basis_ref=(str(wait_basis["basis_ref"]) if wait_basis else None),
                waiting_spec=waiting_spec,
                # v0.4 explicit-condition PAUSE: the same offered basis, with the same spec but
                # carrying its own ``basis_ref`` so AG-2 can prove the Decision quoted it.
                pause_condition_spec=pause_condition_spec,
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
            if is_no_change(exc):
                # A legal "nothing to change": the content request happened, the model answered
                # and no owner was called.  It is reported as a no-op, not as a failure.
                return self._work_no_change(base, llm_calls=1)
            if isinstance(exc, ReadingResponseFailed):
                # The private response (or its pure plan) is unusable: nothing was written, and
                # this is a refused step rather than a decision failure.
                return self._finish(base, OUTCOME_STEP_REFUSED, llm_calls=1,
                                    detail=f"reading response failed, nothing written: "
                                           f"{self.last_error}"[:300])
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
        self.last_correlation_id = out.get("correlation_id")
        base.update({
            "correlation_id": out.get("correlation_id"),
            # The route lives on the committed DecisionRecord itself.  ``evaluate_opportunity``
            # returns the record (and its attempt) but no separate top-level route, so reading
            # only the result dict left this ``None`` in the real chain.
            "decision_route": (getattr(record, "decision_route", None)
                               or dec_res.get("decision_route")),
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

        # v0.4 C: the practice the committed Decision named (if any), resolved inside this round's
        # own read.  It is recorded here and settled in ``_finish``, so the consequence refs the
        # owner confirms travel with it instead of being guessed before the step ran.
        self._bind_method_usage(record)
        # v0.4 C: and the one practice *proposal* the Decision made, saved through the workspace as
        # a TENTATIVE draft.  A refusal is left in ``last_practice_error`` and reported by the round.
        self._save_method_proposal(record)
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
        # v0.4 stage A: what the Decision said about "later" is recorded *now*, one owner write per
        # row and each reported (a refusal is a refusal).  The round's work is also re-pointed at
        # the artifact the Decision actually named, so a second or third project can be continued
        # without the one bound at start-up deciding the round.
        base["future_item_writes"] = self._apply_future_item_writes(record, obs)
        # v0.4 sharing: the Decision may have asked for *one* sharing step.  It is a narrow
        # life-side effect, so it is settled here -- after the Decision committed, before the
        # round's own step runs -- and it never changes which work this round routes to.  Only the
        # owner's answer is recorded: a printed intention is not a delivery.
        sharing = self._apply_sharing_action(record)
        if sharing is not None:
            base["sharing"] = dict(sharing)
        if self.last_project_created:
            base["project_created"] = dict(self.last_project_created)
        routed_point = self.round_point_for(record)
        if routed_point is None and self.last_project_created:
            # The owner just confirmed a *new* work for this round: that is the work this round is
            # about, so the report and any direct-port path name it instead of the previous one.
            routed_point = self.resume_point_for_artifact(
                (self.last_project_created or {}).get("artifact_id"))
        if routed_point is not None:
            resume_point = routed_point
            resume_point = routed_point
        base["step_work_target"] = (f"artifact:{resume_point.artifact_id}"
                                    if resume_point is not None else None)
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
            if foreground_reading is not None:
                # v0.4 day-life: the round reports the reading material's own state whenever the
                # foreground *is* that page read, even when she chose to do nothing with it.  This
                # is the same report the executed-step branch writes, and it keeps "she did not
                # continue the reading" a fact rather than something a caller has to infer.
                situation_now = self.private_material_situation() or {}
                base["reading_continued"] = False
                base["reading_state"] = situation_now.get("state")
            outcome = (OUTCOME_NO_ACTION if decision == ag1.DECISION_NO_ACTION
                       else OUTCOME_DEFERRED)
            if outcome == OUTCOME_NO_ACTION and self._sharing_side_effect_applied(base):
                # A quiet decision that still wrote a share is not "nothing happened".
                outcome = OUTCOME_SHARE_ACTED
                base["sharing_outcome_note"] = (
                    "decision=NO_ACTION; one sharing step was applied by the owner, so this round "
                    "is reported as SHARE_ACTED and not as NO_ACTION")
            # A rule-resolved Decision (empty / all-hard-rejected CandidateSet) never reached a
            # model: AG-1's own contract says ``RULE_RESOLVED`` is 0 LLM calls.  Counting it as
            # one made "eight wakes" read as "eight model calls" while the ledger showed four
            # decision tokens.  The count follows the recorded route, never the outcome name.
            route = str(base.get("decision_route") or "")
            calls = 0 if route == ag1.ROUTE_RULE_RESOLVED else 1
            reasons = list(getattr(record, "reason_codes", []) or [])
            detail = f"decision={decision} route={route or 'UNKNOWN'}"
            if reasons:
                detail += " reason=%s" % ",".join(str(r) for r in reasons)
            return self._finish(base, outcome, llm_calls=calls, detail=detail)
        # v0.4 day-life: a chosen exit is reported only after the owner really applied it.  The
        # adoption record is checked first, and then the Activity owner's own state is re-read: a
        # Decision the owner refused is an ``ADOPTION_REJECTED`` (never "she stopped"), and an
        # adoption that says APPLIED while the Activity is still ACTIVE is reported as unsettled,
        # because nothing may be announced that the owner does not itself report.
        if decision in {ag1.DECISION_PAUSE, ag1.DECISION_WAIT, ag1.DECISION_ABANDON}:
            return self._report_chosen_exit(
                base, obs, decision=decision,
                activity_ref=material.get("foreground_activity"),
                adoption_status=str(getattr(adp_rec, "adoption_status", "") or ""),
                rejection=str(getattr(adp_rec, "rejection_reason_code", "") or ""))

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

        # rc1-C: the foreground is this material's page read, so nothing else may be executed
        # in its place.  Reading CONTINUE continues the *same* material's next page; anything
        # else -- a foreign selection, or a material with no page left to read -- ends the round
        # here, never by falling through to a work commit (that fall-through is exactly how a
        # reading CONTINUE used to become a new version of her writing).
        # v0.4 day-life: and it ends it *named*.  A committed Decision whose step cannot run is
        # ``NO_STEP_AVAILABLE`` -- unavailable, not her choice -- so it can never be reported as
        # ``NO_ACTION`` (a choice she did not make) or as ``ACTED`` (a step that did not run).
        if foreground_reading is not None:
            if self._decision_chose_reading(record) and reading_step is not None:
                return self._classify_reading_step(out, base, obs)
            situation_now = self.private_material_situation() or {}
            base["reading_continued"] = False
            base["reading_state"] = situation_now.get("state")
            reason_code = "READING_STATE_%s" % str(situation_now.get("state") or "UNKNOWN")
            detail = ("the material has no page left to read (%s): nothing was read and "
                      "nothing was written" % (situation_now.get("state") or "UNKNOWN"))
            if not self._decision_chose_reading(record):
                reason_code = "DECISION_NAMED_NO_EXECUTABLE_TARGET"
                detail = ("the committed Decision did not name this reading material and no "
                          "other step could run in its place")
            base["no_step"] = {"reason_code": reason_code,
                               "activity_ref": material.get("foreground_activity"),
                               "target_ref": (self._round_readiness.target_ref
                                              if self._round_readiness else None)}
            return self._finish(base, OUTCOME_NO_STEP_AVAILABLE, llm_calls=1, detail=detail)

        if self._decision_chose_reading(record) and reading_step is not None:
            return self._classify_reading_step(out, base, obs)

        if routed:
            if self._decision_chose_world(record):
                return self._classify_world_step(out, base, obs)
            # v0.4 C: a routed closeout is still one real AR-0 FILE action, so it is reported by
            # the same classifier that reports every other owner receipt.
            return self._classify_ar0_step(out, base, obs)

        if world_step is not None:
            return self._classify_world_step(out, base, obs)

        if reading_step is not None:
            return self._classify_reading_step(out, base, obs)

        if ar0_step is not None:
            return self._classify_ar0_step(out, base, obs)


        if not act or resume_point is None or not self.artifact_grant_id or not self.workspace_id:
            # v0.4 day-life: adopted, but no step of any kind could run here.  That is
            # unavailable -- never ``ACTED``.
            base["no_step"] = {
                "reason_code": ("CALLER_CANNOT_ACT" if not act else "NO_STEP_OWNER_WIRED"),
                "activity_ref": material.get("foreground_activity"),
                "target_ref": (self._round_readiness.target_ref
                               if self._round_readiness else None),
            }
            return self._finish(
                base, OUTCOME_NO_STEP_AVAILABLE, llm_calls=1,
                detail="adopted, but no executable step exists in this revision")

        # No AR-0 adapter is wired, so no action crosses a submission fence.  The step is still
        # performed against the real owner through the narrow port and its receipt is journalled.
        # W05R-R1: the content request inside this call is a model wait and is therefore made
        # without the pause lock; the owner call inside it is guarded by the lease, and a
        # confirmation that arrived while the content request was in flight aborts here.
        try:
            receipt = self._commit_next_version(resume_point, base, obs)
        except life_pause.PauseConfirmedError as exc:
            return self._pause_abort(base, exc, llm_calls=1)
        except Exception as exc:
            if is_no_change(exc):
                # The direct-port path may see the writer's legal no_change too: nothing was
                # written, no version was asked for, and the round reports a no-op.
                return self._work_no_change(base, llm_calls=1)
            raise

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
        content = self._next_content(point, observed_at=observed_at)
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
            observed_at=self._now_iso(),
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

    def _refresh_world_opportunities(
            self, *, observed_at: Optional[str] = None) -> list[dict[str, Any]]:
        """Feed AG-0 the World opportunities one real observation supports.

        AG-0's ``WorldOpportunityCandidateSource`` is fed from outside by design -- it
        may not scan the World itself -- so this is the only door through which a World
        fact can become a candidate.  Recording is idempotent: the opportunity id is
        derived from the place, the World revision and the destination, so observing
        twice from the same state produces the same records, not duplicates.

        ``observed_at`` is **the round's own instant** and is handed to the provider so the
        opportunity's validity window is anchored to the same clock the candidate set is
        validated on.  Without it the provider stamped a real-wall-clock window while a
        compressed-demo round read a business-clock instant: every opportunity was then
        either "not yet open" or "expired" and a real observed place produced no candidate.
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
            observer = getattr(self.world_opportunity_provider, "observe", None)
            if observed_at and callable(observer):
                try:
                    observed = list(observer(occurred_at=observed_at) or [])
                except TypeError:
                    # A provider that does not take ``occurred_at`` keeps its old call shape;
                    # the reading is still real, only the window is its own clock's.
                    observed = list(self.world_opportunity_provider() or [])
            else:
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
                         or self._now_iso())
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
        #: v0.4 lived day: the instant this observation was taken at, as the seam's own records
        #: spell it.  Used only to date the live view; never a new clock and never a schedule.
        self.last_world_observed_at = next(
            (str(item.get("observed_at")) for item in recorded
             if isinstance(item, Mapping) and item.get("observed_at")), None)
        # The live scene, read from the *same* observation that just fed the candidate sources --
        # one read, no second owner call, and it is prompt data only.
        if self.world_opportunity_provider is not None:
            self.world_live_context(observed_at=self.last_world_observed_at)
        elif self.last_live_context is None:
            self.last_live_context = llcv.empty_context(llcv.REASON_NOT_WIRED)
        return recorded

    def _chosen_target_ref(self, decision_record: Any) -> str:
        """The World target the committed Decision selected, from its own candidate record.

        The discriminator is the candidate's own ``target_refs`` namespace -- ``place:``
        for a MOVE, ``object:`` for a CONSUME, ``stay:`` for a bounded non-productive stay,
        ``bodyact:`` for W's POSE/REST -- so the completion contract, the step spec and the
        report all name the same target and none of them re-derives it from the model or from a
        fresh guess.
        """

        candidate = self._selected_candidate(decision_record)
        for ref in (candidate or {}).get("target_refs") or []:
            text = str(ref)
            if (text.startswith(PLACE_REF_PREFIX)
                    or text.startswith(WORLD_OBJECT_REF_PREFIX)
                    or text.startswith(STAY_REF_PREFIX)
                    or text.startswith(BODYACT_REF_PREFIX)
                    or text.startswith(DAILYACT_REF_PREFIX)):
                return text
        return ""
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
        if chosen_target.startswith(BODYACT_REF_PREFIX):
            # W's own pose/rest step: bounded, one target state, and finished only by the World
            # owner's own observation of the pose (or of the rest *session state*).  The fact key
            # and the expected value come from W's parser, so this cycle never re-spells them.
            return self._body_action_adoption_spec(targets, origin, chosen_target, lr5)
        if chosen_target.startswith(DAILYACT_REF_PREFIX):
            # Her own everyday hand work: bounded, one target state on an object that really exists
            # in the World, and finished only by the owner's own post-action observation.  The fact
            # key and the expected value come from B's ``daily_action_completion``, so this cycle
            # never re-spells them.
            return self._daily_action_adoption_spec(targets, origin, chosen_target, lr5)

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

    def _body_action_adoption_spec(self, targets: list[str], origin: str, chosen_target: str,
                                   lr5: Any) -> dict[str, Any]:
        """The Activity shape for one body step W really offered (POSE / REST start-stop).

        Two things are *not* invented here:

        * the parameters and the completion fact -- ``parse_body_action_target`` (W) returns
          ``verb``/``params``/``fact_key``/``expected_value`` for the encoded ref, and the contract
          below is built from exactly those;
        * the completion criterion -- it is LR-5's existing ``WORLD_FACT_MATCH`` on the World
          owner's own observation, with ``TARGET_STATE`` checkpoints (a pose or a rest session is a
          bounded end state, not a position on a journey).

        A ref W's own parser refuses is an error, never a default: this cycle cannot guess what a
        body target meant.
        """

        module = body_action_targets_module()
        parsed = module.parse_body_action_target(chosen_target)
        if not isinstance(parsed, Mapping):
            raise AutonomousLifeError(
                "the committed Decision chose %r, which W's parser does not accept as a body "
                "action target" % (chosen_target,))
        fact_key = str(parsed.get("fact_key") or "")
        expected = str(parsed.get("expected_value") or "")
        if not fact_key or not expected:
            raise AutonomousLifeError(
                "W's parser named no completion fact for %r" % (chosen_target,))
        verb = str(parsed.get("verb") or "")
        # v0.4 A (pacing): the progress evidence this step submits carries the owner's own
        # ``world_body_recovery_ref`` as its ``world_fact_ref``/``target_ref``, and LR-5 binds
        # evidence to an Activity through the Activity's own target refs.  Without that value here
        # the owner-confirmed fact has nothing to bind to: LR-5 answered ``progress_state=UNKNOWN``
        # and the completion criterion could never be evaluated, so a REST Activity stayed ACTIVE
        # and held the foreground (measured on the real chain; the daily-action path already
        # carries the same line for the same reason).
        if expected not in targets:
            targets = list(targets) + [expected]
        spec = ag2.ActivityAdoptionSpec(
            activity_kind=BODY_ACTION_ACTIVITY_KIND,
            title="用一下自己的身体（%s）" % (verb or "BODY"),
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
                "criteria": {"fact_key": fact_key, "expected_value": expected},
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

    def _daily_action_adoption_spec(self, targets: list[str], origin: str, chosen_target: str,
                                    lr5: Any) -> dict[str, Any]:
        """The Activity shape for one everyday hand step she really chose (pick up / put down).

        Two things are *not* invented here:

        * the parameters and the completion fact -- B's ``parse_daily_action_target`` (verb, params,
          object) and ``daily_action_completion`` (``fact_key`` / ``expected_value``) own them;
        * the completion criterion -- LR-5's existing ``WORLD_FACT_MATCH`` with ``TARGET_STATE``
          checkpoints, because picking something up or setting it down is a bounded end state on
          the object, not a position on a journey.

        A target B's own parser refuses is an error, never a default: this cycle cannot guess what
        a hand action meant.
        """

        module = daily_action_targets_module()
        try:
            parsed = module.parse_daily_action_target(chosen_target)
            completion = module.daily_action_completion(chosen_target)
        except Exception as exc:  # noqa: BLE001 - B's refusal is named, never guessed at
            raise AutonomousLifeError(
                "B's daily action contract refuses %r: %s"
                % (chosen_target, str(exc)[:200]))
        if not isinstance(parsed, Mapping):
            raise AutonomousLifeError(
                "the committed Decision chose %r, which B's parser does not accept as a daily "
                "action target" % (chosen_target,))
        if not isinstance(completion, Mapping):
            raise AutonomousLifeError(
                "B's daily action contract named no completion fact for %r" % (chosen_target,))
        fact_key = str(completion.get("fact_key") or "")
        expected = str(completion.get("expected_value") or "")
        if not fact_key or not expected:
            raise AutonomousLifeError(
                "B's daily action contract named an incomplete completion fact for %r"
                % (chosen_target,))
        verb = str(parsed.get("verb") or "")
        verb = str(parsed.get("verb") or "")
        object_id = str(parsed.get("object_id") or "")
        # The progress evidence this step submits carries the owner's own placement fact as its
        # ``world_fact_ref``/``target_ref``, and LR-5 binds evidence to an Activity through the
        # Activity's own target refs.  Without that value here the owner-confirmed fact has nothing
        # to bind to: LR-5 answered ``progress_state=UNKNOWN`` and the completion could never be
        # evaluated (measured on the real chain, not inferred).
        if expected not in targets:
            targets = list(targets) + [expected]
        spec = ag2.ActivityAdoptionSpec(
            activity_kind=DAILY_ACTION_ACTIVITY_KIND,
            title="手边的事（%s %s）" % (verb or "HAND", object_id or "?"),
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
                "criteria": {"fact_key": fact_key, "expected_value": expected},
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
        """A World candidate is one whose target is a place, an object, a stay or a body step.

        The discriminator is the committed candidate record itself -- the same ``place:`` /
        ``object:`` / ``stay:`` / ``bodyact:`` namespaces ``_world_step_spec`` reads its target
        from -- and not a cached action space or a re-derived guess.  A World opportunity whose
        target is none of them (there is none today) is not routed to the World owner.
        """

        if not isinstance(candidate, Mapping):
            return False
        return any(str(ref).startswith(PLACE_REF_PREFIX)
                   or str(ref).startswith(WORLD_OBJECT_REF_PREFIX)
                   or str(ref).startswith(STAY_REF_PREFIX)
                   or str(ref).startswith(BODYACT_REF_PREFIX)
                   or str(ref).startswith(DAILYACT_REF_PREFIX)
                   for ref in (candidate.get("target_refs") or []))

    def _decision_chose_world(self, decision_record: Any) -> bool:
        return self._candidate_is_world(self._selected_candidate(decision_record))

    def _require_world_candidate(self, decision_record: Any) -> None:
        """A single-World round may only build the World shape for a World candidate.

        The routed branch asks ``_candidate_is_world`` before it reaches a World builder.  When
        the World path is the *only* one available there is no such routing step, so the same
        predicate is applied here, before any World Activity shape or step is described: a
        Decision that selected a personal candidate is refused up front, instead of being
        discovered only inside ``_world_step_spec`` after the Activity shape was already built.
        The predicate and the refusal text are the existing ones; nothing is re-selected,
        retried or rewritten.
        """

        candidate_ref = getattr(decision_record, "selected_candidate_ref", None)
        if not self._candidate_is_world(self._selected_candidate(decision_record)):
            raise AutonomousLifeError(
                "the committed Decision %r names no observed World target" % (candidate_ref,))

    def _world_step_for_decision(self, decision_record: Any) -> dict[str, Any]:
        """The single-World step: only for a Decision that really selected a World candidate."""

        self._require_world_candidate(decision_record)
        return self._world_step_spec(decision_record)

    def _world_adoption_for_decision(self, opportunities: list[dict[str, Any]],
                                     decision_record: Any) -> dict[str, Any]:
        """The single-World Activity shape: the same guard, before any shape is built."""

        self._require_world_candidate(decision_record)
        return self._world_adoption_spec(opportunities, decision=decision_record)

    def _routed_step_builder(self, point: Optional[ArtifactResumePoint], *, observed_at: str,
                             closeout: Optional[Mapping[str, Any]] = None
                             ) -> Callable[[Any], Mapping[str, Any]]:
        """One step, whose owner is chosen by the committed Decision."""

        def build(decision_record: Any) -> Mapping[str, Any]:
            if self._decision_chose_world(decision_record):
                return self._world_step_spec(decision_record)
            if closeout is not None and self._decision_chose_closeout(decision_record):
                # v0.4 C: the wrap-up step is W's plan; it is built only for a Decision whose own
                # candidate named the closeout target, never as a fallback.
                return self._closeout_step_spec(closeout, decision_record=decision_record,
                                                observed_at=observed_at)
            if self._decision_chose_reading(decision_record):
                return self._reading_step_spec(observed_at=observed_at)
            # v0.4 stage A: the work step belongs to the work the Decision named -- a *new* project
            # it just had the owner create, another of her works, or (when it named none) the one
            # this round selected.  A Decision that named an unresumable work is a named refusal,
            # never a quiet retarget onto whatever else happened to be bound.
            return self._work_step_spec_for_decision(decision_record, fallback=point,
                                                    observed_at=observed_at)

        return build

    @staticmethod
    def _daily_action_state_token(observation: Mapping[str, Any]) -> str:
        """A stable token for the body/World state a hand action was decided in.

        Only facts that a real hand action changes: where every visible object is (kind,
        surface/container/slot, in-hand), which hands hold what, the pose/orientation, the
        place, and the World's own revision.  It is a *fallback* origin for daily-action
        steps that carry no committed Decision identity -- not an action identity: it cannot
        tell two Decisions apart when an ABA cycle brings the same placements back, and it
        changes when the owner executed an action whose answer the client lost.  Both cases
        are why the real path keys on ``decision_id`` instead.  It grants nothing and is not
        persisted as a second state record.
        """

        visible: list[list[Any]] = []
        for item in observation.get("visible_objects") or []:
            if not isinstance(item, Mapping):
                continue
            visible.append([
                str(item.get("object_id") or ""),
                item.get("kind"),
                item.get("surface_id"),
                item.get("container_id"),
                item.get("slot_id"),
                bool(item.get("in_hand")),
            ])
        visible.sort(key=lambda row: row[0])
        hands = observation.get("hands")
        facts = {
            "world_revision": observation.get("world_revision"),
            "location": observation.get("location"),
            "pose": observation.get("pose"),
            "orientation": observation.get("orientation"),
            "hands": ({str(slot): held for slot, held in hands.items()}
                      if isinstance(hands, Mapping) else None),
            "visible": visible,
        }
        text = json.dumps(facts, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

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
                        or text.startswith(STAY_REF_PREFIX)
                        or text.startswith(BODYACT_REF_PREFIX)
                        or text.startswith(DAILYACT_REF_PREFIX)):
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
        if target_ref.startswith(BODYACT_REF_PREFIX):
            # W owns the encoding *and* the step shape: the whole parameter set travels as the one
            # AR-0 target, and the World bridge decodes it at submission time.  Nothing about a
            # pose or a rest session is re-spelled here.
            #
            # The identity is the committed Decision, exactly as with ``dailyact:`` below.  A
            # retry or an UNKNOWN reconciliation for this Decision must keep this execution even
            # when the observed revision/pose moved on; a different Decision that happens to
            # choose the same target in the same place is a different logical body action and
            # must not inherit this one's step/Action/authorization.  Observation coordinates
            # alone cannot tell those two cases apart.
            self.last_world_target_ref = target_ref
            decision_identity = str(
                getattr(decision_record, "decision_id", "") or "").strip()
            return dict(body_action_targets_module().build_body_action_step(
                target_ref, place=place, revision=revision,
                decision_ref=decision_identity or None))
        if target_ref.startswith(DAILYACT_REF_PREFIX):
            # B owns the encoding *and* the step shape, exactly as with ``bodyact:``: the whole
            # parameter set travels inside the one AR-0 target and the World bridge decodes it at
            # submission time.  Nothing about picking something up or putting it down is re-spelled
            # here, so an encoding change on B's side cannot drift from the step that is submitted.
            self.last_world_target_ref = target_ref
            module = daily_action_targets_module()
            builder = getattr(module, "build_daily_action_step", None)
            if not callable(builder):
                raise AutonomousLifeError(
                    "DAILY_ACTION_STEP_BUILDER_MISSING: life_daily_action_targets publishes no "
                    "build_daily_action_step")
            # The origin names the committed *Decision*, not the observation.  Action identity
            # belongs to the Decision: a retry or an UNKNOWN reconciliation must keep the same
            # execution even when the owner already changed the world (that is exactly what the
            # AR-0 submission fence is for), while a *different* Decision is a different logical
            # action even if the world happens to look identical again.
            #
            # An observation hash cannot express that.  PICK_UP/PLACE move substrate placements
            # without bumping ``world_revision``, so after two full pick/place cycles the visible
            # facts are byte-identical: a state-hash key would rebuild the earlier key and AR-0
            # would answer the first SUCCEEDED execution instead of touching the owner.  The
            # reverse is just as wrong: if the owner executed but the client lost the answer, the
            # observation changes and a state-hash key would mint a *new* action for the same
            # Decision.  The state token therefore only remains the fallback for callers that
            # carry no Decision identity (hand-built steps); the real Decision path is stable.
            decision_identity = str(getattr(decision_record, "decision_id", "") or "").strip()
            if decision_identity:
                origin = "place:%s@%s" % (place, decision_identity)
            else:
                origin = "place:%s@%s" % (place, self._daily_action_state_token(observation))
            try:
                spec = builder(target_ref, origin_ref=origin)
            except TypeError:
                # B's published shape is ``(target, *, origin_ref=None)``; a builder that also
                # wants the observation's own place/revision is called with those instead of being
                # skipped.
                spec = builder(target_ref, place=place, revision=revision)
            if not isinstance(spec, Mapping):
                raise AutonomousLifeError(
                    "DAILY_ACTION_STEP_SHAPE_INVALID: build_daily_action_step returned %s"
                    % type(spec).__name__)
            return dict(spec)

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

    #: The owner status a chosen exit must really have produced, per Decision.
    EXIT_EXPECTED_STATUSES = {
        "ABANDON": ("ABANDONED", "CANCELLED", "EXPIRED"),
        "PAUSE": ("PAUSED", "WAITING"),
        "WAIT": ("WAITING", "PAUSED"),
    }

    def _report_chosen_exit(self, base: dict[str, Any], obs: str, *, decision: str,
                            activity_ref: Any, adoption_status: str,
                            rejection: str = "") -> dict[str, Any]:
        """Report a chosen PAUSE/WAIT/ABANDON only when the owner really applied it.

        Two independent facts are required and both are reported:

        * the Adoption owner applied the Decision (``APPLIED``, not ``REJECTED*``);
        * the Activity owner's own record now holds the status that exit implies (LR-2 owns the
          transition; this cycle only reads it back).

        A refusal is ``ADOPTION_REJECTED`` -- never "she paused".  An adoption that claims APPLIED
        while the Activity is still ``ACTIVE`` is ``STEP_UNSETTLED``: an unknown result is not a
        stop, and nothing is written about it either way.
        """

        ref = str(activity_ref or "")
        owner_status = ""
        read_error = ""
        if ref:
            try:
                record = self.runtime.activity_read.get_activity(ref)
            except Exception as exc:  # a failed read is reported, never guessed
                read_error = "%s: %s" % (type(exc).__name__, exc)
                record = None
            if isinstance(record, Mapping):
                owner_status = str(record.get("status") or "")
        applied = str(adoption_status or "") == "APPLIED"
        expected = tuple(self.EXIT_EXPECTED_STATUSES.get(str(decision), ()))
        exit_view: dict[str, Any] = {
            "decision": str(decision),
            "activity_ref": ref or None,
            "adoption_status": str(adoption_status or "") or None,
            "rejection_reason_code": rejection or None,
            "owner_status": owner_status or None,
            "expected_owner_statuses": list(expected),
            "applied": bool(applied),
            "owner_read_error": read_error or None,
        }
        base["exit"] = exit_view
        if not applied:
            return self._finish(
                base, OUTCOME_ADOPTION_REJECTED, llm_calls=1,
                detail="decision=%s was not applied by the owner (%s); nothing is reported as "
                       "stopped" % (decision, rejection or adoption_status or "NO_ADOPTION"))
        if owner_status not in expected:
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="the owner applied decision=%s but reports the Activity as %r; an unknown "
                       "result is not a stop" % (decision, owner_status or "UNREAD"))
        return self._finish(
            base, OUTCOME_PAUSED if str(decision) in {"PAUSE", "WAIT"} else OUTCOME_ABANDONED,
            llm_calls=1,
            detail="decision=%s applied by the owner; the Activity is now %s"
                   % (decision, owner_status))


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
        # The Decision request was a real wait: what the round read before it is no longer
        # proof of the owners' state.  The very next thing that happens is the owner-side
        # final freshness check (``validate_decision_for_adoption`` -> ``validate_candidate``
        # -> ``ArtifactContinuationSource.refresh``), which is a re-check *at use time*.  Drop
        # the round's read memo here so that check really asks the owner: an archive, a
        # tombstone or a newer head that landed while the model was answering is observed and
        # blocks adoption instead of being papered over by the pre-Decision snapshot.
        self._invalidate_owner_reads()
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
        # 旅程进度是业务时间：显式演示模式读同一个压缩域；普通路径仍是真实 now。
        observed_at = observed_at or self._now_iso()
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
                # 每一段都是新的 Action：结算之后若要发起下一段，也必须经 World
                # 自己的正式授权入口。端口来自本 runtime 的旅程 resolver——同一个
                # 已用于首段提交的正式口；没有正式口的 runtime 交给引擎失败关闭。
                adapter = getattr(self.runtime, "world_action_adapter", None)
                resolver = getattr(adapter, "resolver", None)
                submit_port = getattr(resolver, "submit_port", None)
                projection = journey.advance_journey(
                    hermes_home=home, environment=self.env, now=observed_at,
                    settle_only=bool(settle_only), submit_port=submit_port)
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
                                "observed_at": observed_at or self._now_iso()}
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

        observed = str(occurred_at or self._now_iso())
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

    @staticmethod
    def _definite_pre_submit_refusal(action_status: Any,
                                     attempt: Any) -> Optional[dict[str, Any]]:
        """A World step the owner *provably* never received, or ``None``.

        AR-0 keeps the Action at ``PREPARED`` after a definite pre-submission failure
        (gate off / shadow / invalid) so the same action id may be retried, and records
        the attempt as ``PRE_SUBMIT_FAILED``.  That status is neither SUCCEEDED nor
        FAILED nor UNKNOWN; treating it as unsettled hides a clean refusal behind
        "something may have happened".  Only this exact evidence is a refusal -- a
        fenced/submitted attempt, a missing attempt, or a terminal Action stays on the
        unsettled/owner path.
        """

        if str(action_status or "") != ACTION_STATUS_PREPARED:
            return None
        if not isinstance(attempt, Mapping):
            return None
        if str(attempt.get("status") or "") != ATTEMPT_STATUS_PRE_SUBMIT_FAILED:
            return None
        if str(attempt.get("transport_result") or "") != TRANSPORT_ERR_DEFINITE_PRE_SUBMIT:
            return None
        return {
            "reason_code": str(attempt.get("error_class") or "PRE_SUBMIT_FAILED"),
            "transport_result": TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
            "attempt_id": attempt.get("attempt_id"),
            "attempt_status": ATTEMPT_STATUS_PRE_SUBMIT_FAILED,
            "provider_operation_ref": attempt.get("provider_ref"),
            # This is the fact the report is allowed to state: nothing crossed the fence.
            "not_sent": True,
        }

    @staticmethod
    def _step_submit_attempt(submit: Mapping[str, Any],
                             action: Mapping[str, Any]) -> Mapping[str, Any]:
        """AR-0's attempt for the action this step submitted, in either shipped shape.

        The bridge's own result is flat (``action`` + ``attempt``), but the production
        ``LifeIntegrationCoordinator.execute_activity_step`` puts that bridge result under
        its own ``submit_result`` key.  Reading only the flat key meant a real
        pre-submission refusal never matched, so "nothing was sent" was reported as
        "something may have happened".

        Only one explicit layer is unwrapped, and only an attempt that names the *same*
        action is used: records from two executions are never stitched into one identity.
        A missing, non-mapping or mismatched attempt is ``{}`` -- the caller then keeps
        its existing unknown/unsettled semantics instead of inventing a refusal.
        """

        action_id = str(action.get("action_id") or "")
        flat = submit.get("attempt")
        nested = submit.get("submit_result")

        def belongs_to_the_action(candidate: Any) -> bool:
            if not isinstance(candidate, Mapping) or not candidate:
                return False
            candidate_id = str(candidate.get("action_id") or "")
            return not (action_id and candidate_id) or action_id == candidate_id

        if belongs_to_the_action(flat):
            return flat
        if isinstance(nested, Mapping):
            nested_action = nested.get("action")
            nested_action_id = (str(nested_action.get("action_id") or "")
                                if isinstance(nested_action, Mapping) else "")
            same_layer = (not (action_id and nested_action_id)
                          or action_id == nested_action_id)
            candidate = nested.get("attempt")
            if same_layer and belongs_to_the_action(candidate):
                return candidate
        return {}

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
        submit = step_res.get("submit_result")
        if not isinstance(submit, Mapping):
            submit = {}
        action = submit.get("action")
        if not isinstance(action, Mapping):
            action = {}
        attempt = self._step_submit_attempt(submit, action)
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

        # A definite pre-submission refusal (gate off / shadow / invalid) never reached the
        # World owner: AR-0 keeps the Action at PREPARED and records the attempt as
        # PRE_SUBMIT_FAILED.  Reporting that as "unsettled" is how a clean refusal used to
        # read as an unknown execution.  It is a refusal -- nothing was sent, nothing may be
        # reconciled -- and only this exact evidence is treated that way.
        refusal = self._definite_pre_submit_refusal(status, attempt)
        if refusal is not None:
            base["pre_submit"] = dict(refusal)
            base["transport_result"] = refusal["transport_result"]
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="world action was refused before submission (%s); nothing was sent "
                       "to the World owner" % refusal["reason_code"])

        # v0.4 day-life / W: a pose or a rest session is not an arrival.  Its own classifier reads
        # the World owner's own observation (the snapshot for POSE, the REST session state for REST)
        # and only then hands LR-5 the fact the completion contract names.
        if status == "SUCCEEDED" and verb in {POSE_VERB, REST_VERB}:
            return self._classify_body_action(base, verb=verb, action=action)
        # v0.4 lived day: her everyday hand work.  "The owner accepted the command" is not the fact
        # -- the placement/hand state the World reports *after* the action is, and B's contract says
        # which one to read.  A re-read that cannot answer stays unsettled.
        if status == "SUCCEEDED" and verb in {PICK_UP_VERB, PLACE_VERB}:
            return self._classify_daily_action(base, verb=verb, action=action)
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

    def _body_recovery_facts(self) -> Optional[Mapping[str, Any]]:
        """The Body owner's own recovery block (state / mode / trend / progress_band), or ``None``.

        Read from the *same* port the situation view already used, and only when that port is
        reachable: this is the one place where the qualitative progress of an open session is
        available at all (``WorldBodyReadPort.body()`` -> ``data.recovery``).  It costs one extra
        read on the rounds where a body is really wired -- a documented price for showing the model
        a *measured* trend instead of a permanent ``NOT_READ`` -- and it never invents a value: a
        failed read is ``None``, which the projection reports as ``NOT_READ`` by name.

        It is not a capability number, not a threshold, and not a recovery claim; the band is the
        owner's own word for the session it is running.
        """

        port = getattr(self.situation_source, "body_port", None)
        if not callable(getattr(port, "body", None)):
            return None
        try:
            payload = port.body()
        except Exception:  # noqa: BLE001 - a failed read is reported, never filled
            return None
        data = payload.get("data") if isinstance(payload, Mapping) else None
        recovery = data.get("recovery") if isinstance(data, Mapping) else None
        return dict(recovery) if isinstance(recovery, Mapping) else None


    def _world_rest_availability(self) -> Optional[Mapping[str, Any]]:
        """The World owner's own ``REST`` entry, from the observation seam's *latest* read.

        It is the *only* surface that exposes the recovery **session** state (``current``), so it is
        read from the same seam as the snapshot -- and only ever after the seam was re-read (see
        ``_fresh_world_body_observation``).  ``None`` means "cannot be known", never "inactive".
        """

        available = getattr(self.world_opportunity_provider, "last_available_actions", None)
        if not isinstance(available, Mapping):
            return None
        for entry in available.get("actions") or []:
            if isinstance(entry, Mapping) and str(entry.get("verb") or "") == REST_VERB:
                return dict(entry)
        return None

    def _fresh_world_body_observation(self) -> dict[str, Any]:
        """Re-read the World owner's own state **after** the action, through the same seam.

        The round's observation was taken *before* the step was submitted, so it cannot answer
        "does the owner report this pose now" -- reusing it is how a committed ``POSE``/``REST``
        could be judged against the state it was meant to change.  This therefore re-observes once,
        through the same provider (one seam, one identity, no second client), and it reports
        whether that read really happened.

        A failed re-read is *unknown*: it returns an empty snapshot and no REST entry, so the fact
        can never be satisfied by the pre-action state, and it is never answered by the target ref
        either (the target says what was *asked for*, not what the owner reports).
        """

        provider = self.world_opportunity_provider
        if provider is None or not callable(provider):
            return {"ok": False, "reason_code": "WORLD_OBSERVATION_NOT_WIRED",
                    "snapshot": {}, "rest_availability": None, "world_revision": None,
                    "observed_place": None}
        place, revision = self._observe_position()
        if place is None and revision is None:
            return {"ok": False,
                    "reason_code": (self.last_world_error
                                    and "WORLD_OBSERVATION_FAILED:%s" % self.last_world_error
                                    or "WORLD_OBSERVATION_UNAVAILABLE"),
                    "snapshot": {}, "rest_availability": None, "world_revision": None,
                    "observed_place": None}
        return {"ok": True, "reason_code": "WORLD_OBSERVATION_RE_READ",
                "snapshot": dict(self.last_world_observation or {}),
                "rest_availability": self._world_rest_availability(),
                "world_revision": revision, "observed_place": place}

    def _classify_body_action(self, base: dict[str, Any], *, verb: str,
                              action: Mapping[str, Any]) -> dict[str, Any]:
        """Report a POSE/REST step as exactly what the World owner's own state says *now*.

        The command succeeding is *not* the fact, and neither is the round's pre-action snapshot.
        W's ``body_action_observed_fact`` reads the canonical snapshot for a pose (``pose`` +
        ``orientation``) and the REST session state for a rest start/stop, so this method first
        re-reads the World (``_fresh_world_body_observation``) and then asks it.  A read that failed
        or a state that disagrees is ``STEP_UNSETTLED``: no LR-5 progress, no completion, and in
        particular no claim that a body "recovered".
        """

        module = body_action_targets_module()
        payload = dict(action.get("frozen_payload") or {})
        target_ref = str(payload.get("target") or self.last_world_target_ref or "")
        parsed = module.parse_body_action_target(target_ref)
        if not isinstance(parsed, Mapping):
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="the executed body step names no parsable body action target")
        fresh = self._fresh_world_body_observation()
        fact = module.body_action_observed_fact(
            parsed, fresh["snapshot"], rest_availability=fresh["rest_availability"])
        base["body_action"] = {
            "verb": str(parsed.get("verb") or verb),
            "target_ref": target_ref,
            "params": dict(parsed.get("params") or {}),
            "fact_key": fact.get("fact_key"),
            "expected_value": fact.get("expected_value"),
            "observed": dict(fact.get("observed") or {}),
            "observed_value": fact.get("value"),
            "satisfied": bool(fact.get("satisfied")),
            "source": fact.get("source"),
            # Which read answered, and whether it was a *fresh* one: the pre-action snapshot is
            # never an answer, and a failed re-read stays unknown.
            "observation": {"reason_code": fresh["reason_code"],
                            "re_read": bool(fresh["ok"]),
                            "world_revision": fresh["world_revision"],
                            "observed_place": fresh["observed_place"]},
        }
        if not fact.get("satisfied"):
            if not fresh["ok"]:
                return self._finish(
                    base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                    detail="the World could not be re-read after the action (%s); nothing is "
                           "attributed to this step" % fresh["reason_code"])
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="the World owner's own %s does not show %r yet; nothing is attributed "
                       "to this step" % (fact.get("source") or "observation",
                                         fact.get("expected_value")))
        self._record_world_body_progress(
            base, activity_ref=base.get("activity_ref"), parsed=parsed, fact=fact,
            action_ref=action.get("action_id"), correlation_id=base.get("correlation_id"))
        return self._finish(
            base, OUTCOME_ACTED, llm_calls=1,
            detail="the World owner's own observation satisfies %s=%s"
                   % (fact.get("fact_key"), fact.get("expected_value")))

    def _record_world_body_progress(self, base: dict[str, Any], *, activity_ref: Any,
                                    parsed: Mapping[str, Any], fact: Mapping[str, Any],
                                    action_ref: Any, correlation_id: Any) -> None:
        """Hand LR-5 the body fact the owner itself confirmed -- and only then.

        Same seam as the place/object facts: ``world_authority`` + ``WORLD_CANONICAL_OBSERVATION`` in
        LR-5's own ``world_fact`` namespace, carrying W's ``fact_key``/``expected_value``.  For a
        rest stop the fact is the session being closed, which is *not* a recovery claim.
        """

        import activity_progress_completion_lr5 as lr5

        if not activity_ref:
            return
        fact_key = str(fact.get("fact_key") or "")
        expected = str(fact.get("expected_value") or "")
        if not fact_key or not expected:
            return
        target_ref = str(parsed.get("target") or "")
        source = str(fact.get("source") or "world_gateway")
        now = self._now_iso()
        evidence = lr5.LifeProgressEvidence(
            progress_evidence_id=(
                f"pevid:{hashlib.sha256(f'{activity_ref}:{target_ref}'.encode('utf-8')).hexdigest()[:16]}"),
            source_kind=lr5.SOURCE_KIND_WORLD_FACT,
            source_ref=f"world:body:{target_ref}",
            authority_domain="world_authority",
            epistemic_kind=lr5.EPISTEMIC_CANONICAL_OBSERVATION,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            idempotency_key=f"pevid_idem:{activity_ref}:{target_ref}",
            activity_id=str(activity_ref),
            action_ref=str(action_ref) if action_ref else None,
            world_fact_ref=expected,
            target_ref=expected,
            observed_facts={fact_key: expected, "observation_source": source},
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
        self._emit_world_experience(base, activity_ref=activity_ref,
                                    world_refs=[expected, "world:body:%s" % target_ref])

    def _classify_daily_action(self, base: dict[str, Any], *, verb: str,
                               action: Mapping[str, Any]) -> dict[str, Any]:
        """Report a PICK_UP / PLACE step as exactly what the World reports *after* it.

        The owner accepting the command is not the fact, and neither is the round's pre-action
        snapshot: what matters is where the object really is now.  B's contract says which fact to
        read (``daily_action_completion``); this method re-reads the World through the same
        observation seam (``_fresh_world_body_observation``) and asks for that fact to be checked
        against the *fresh* state:

        * B's own ``daily_action_observed_fact`` when that module publishes one (preferred -- it is
          the owner of the encoding, so it is also the owner of how the encoding is read back);
        * otherwise the fresh snapshot's own hand/placement structures, compared with the object the
          parsed target names.  Nothing is derived from "the ref I asked for".

        A read that failed, or a state that disagrees, is ``STEP_UNSETTLED``: no LR-5 progress, no
        completion, and in particular no claim that she picked something up or put it down.
        """

        module = daily_action_targets_module()
        payload = dict(action.get("frozen_payload") or {})
        target_ref = str(payload.get("target") or self.last_world_target_ref or "")
        try:
            parsed = module.parse_daily_action_target(target_ref)
            completion = module.daily_action_completion(target_ref)
        except Exception as exc:  # noqa: BLE001 - B's own refusal is a named refusal, not a crash
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="the executed daily step names a target B's contract refuses: %s"
                       % (str(exc)[:200],))
        if not isinstance(parsed, Mapping):
            return self._finish(
                base, OUTCOME_STEP_REFUSED, llm_calls=1,
                detail="the executed daily step names no parsable daily action target")
        fact_key = str((completion or {}).get("fact_key") or "") if isinstance(
            completion, Mapping) else ""
        expected = str((completion or {}).get("expected_value") or "") if isinstance(
            completion, Mapping) else ""
        fresh = self._fresh_world_body_observation()
        observed = self._daily_action_observed_state(
            module, parsed, fresh["snapshot"], verb=str(parsed.get("verb") or verb))
        satisfied = bool(observed.get("satisfied")) if fresh["ok"] else False
        base["daily_action"] = {
            "verb": str(parsed.get("verb") or verb),
            "target_ref": target_ref,
            "params": dict(parsed.get("params") or {}),
            "object_id": parsed.get("object_id"),
            "fact_key": fact_key or None,
            "expected_value": expected or None,
            "observed": dict(observed.get("observed") or {}),
            "observed_value": observed.get("value"),
            "satisfied": satisfied,
            "source": observed.get("source"),
            # Which read answered, and whether it was a *fresh* one: the pre-action snapshot is
            # never an answer, and a failed re-read stays unknown.
            "observation": {"reason_code": fresh["reason_code"],
                            "re_read": bool(fresh["ok"]),
                            "world_revision": fresh["world_revision"],
                            "observed_place": fresh["observed_place"]},
        }
        if not fact_key or not expected:
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="B's daily action contract named no completion fact for this step; "
                       "nothing is attributed to it")
        if not satisfied:
            if not fresh["ok"]:
                return self._finish(
                    base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                    detail="the World could not be re-read after the action (%s); nothing is "
                           "attributed to this step" % fresh["reason_code"])
            return self._finish(
                base, OUTCOME_STEP_UNSETTLED, llm_calls=1,
                detail="the World owner's own state does not show %r yet; nothing is attributed "
                       "to this step" % (observed.get("value") or expected))
        self._record_daily_action_progress(
            base, activity_ref=base.get("activity_ref"), target_ref=target_ref,
            fact_key=fact_key, expected=expected,
            source=str(observed.get("source") or "world_gateway.get_snapshot"),
            observation=observed.get("observed") or {},
            action_ref=action.get("action_id"), correlation_id=base.get("correlation_id"))
        return self._finish(
            base, OUTCOME_ACTED, llm_calls=1,
            detail="the World owner's own observation satisfies %s" % (expected,))

    @staticmethod
    def _daily_action_observed_state(module: Any, parsed: Mapping[str, Any],
                                     snapshot: Mapping[str, Any], *, verb: str
                                     ) -> dict[str, Any]:
        """Is the object really where the verb says, in the owner's own fresh snapshot?

        Prefers B's own reader when it exists.  Otherwise the check is deliberately narrow and
        owner-shaped: a ``PICK_UP`` is satisfied when the object is in a hand slot, and a ``PLACE``
        when it is not -- both read from the snapshot's own ``hands`` structure.  When that
        structure is not published, the answer is *unknown* (``satisfied: False`` with an empty
        observed map), never "assume it worked".
        """

        reader = getattr(module, "daily_action_observed_fact", None)
        if callable(reader):
            try:
                fact = reader(parsed, snapshot)
            except Exception:  # noqa: BLE001 - an owner reader that breaks answers nothing
                fact = None
            if isinstance(fact, Mapping):
                return {"satisfied": bool(fact.get("satisfied")),
                        "observed": dict(fact.get("observed") or {}),
                        "value": fact.get("value"),
                        "source": str(fact.get("source") or "daily_action_targets")}
        object_id = str(parsed.get("object_id") or "")
        hands = snapshot.get("hands") if isinstance(snapshot, Mapping) else None
        if not object_id or not isinstance(hands, Mapping):
            return {"satisfied": False, "observed": {}, "value": None,
                    "source": "world_unreadable"}
        held = sorted(str(value) for value in hands.values() if value)
        in_hand = object_id in held
        satisfied = in_hand if str(verb).upper() == PICK_UP_VERB else (not in_hand)
        return {
            "satisfied": satisfied,
            "observed": {"held_object_ids": held, "object_id": object_id, "in_hand": in_hand},
            "value": ("held:%s" % object_id) if in_hand else ("placed:%s" % object_id),
            "source": "world_gateway.get_snapshot.hands",
        }

    def _record_daily_action_progress(self, base: dict[str, Any], *, activity_ref: Any,
                                      target_ref: str, fact_key: str, expected: str,
                                      source: str, observation: Mapping[str, Any],
                                      action_ref: Any, correlation_id: Any) -> None:
        """Hand LR-5 the hand/placement fact the owner itself confirmed -- and only then.

        Same seam as the body and place facts: ``world_authority`` +
        ``WORLD_CANONICAL_OBSERVATION`` in LR-5's own ``world_fact`` namespace, carrying B's
        ``fact_key``/``expected_value`` and the freshly observed objects.  A step succeeding writes
        progress; whether the Activity is *complete* stays LR-5's own criterion, so "I picked it up"
        and "this Activity is over" can never be the same claim.
        """

        import activity_progress_completion_lr5 as lr5

        if not activity_ref or not fact_key or not expected:
            return
        now = self._now_iso()
        observed_facts: dict[str, Any] = {fact_key: expected, "observation_source": source}
        held = observation.get("held_object_ids") if isinstance(observation, Mapping) else None
        if isinstance(held, list):
            observed_facts["world_hands_held_count"] = len(held)
        evidence = lr5.LifeProgressEvidence(
            progress_evidence_id=(
                f"pevid:{hashlib.sha256(f'{activity_ref}:{target_ref}'.encode('utf-8')).hexdigest()[:16]}"),
            source_kind=lr5.SOURCE_KIND_WORLD_FACT,
            source_ref="world:daily:%s" % _world_ref_token(target_ref),
            authority_domain="world_authority",
            epistemic_kind=lr5.EPISTEMIC_CANONICAL_OBSERVATION,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            idempotency_key="pevid_idem:%s:%s" % (activity_ref, target_ref),
            activity_id=str(activity_ref),
            action_ref=str(action_ref) if action_ref else None,
            world_fact_ref=expected,
            target_ref=expected,
            observed_facts=observed_facts,
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
        self._emit_world_experience(base, activity_ref=activity_ref,
                                    world_refs=[expected,
                                                "world:daily:%s" % _world_ref_token(target_ref)])

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
        now = self._now_iso()
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
        now = self._now_iso()
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
        now = str(base.get("observed_at") or self._now_iso())
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

    def _owner_confirmed_context(self, *, artifact_ref: Any, version_ref: Any
                                 ) -> Optional[dict[str, Any]]:
        """What the owner really holds at the version this round just wrote -- or ``None``.

        Read back through the artifact port: the title, the new version's own text (bounded, and
        with any machine header stripped by the *generic* Markdown rule -- no marker spelling lives
        here), and the two refs the handoff will carry.  It is never the writer's ``self_note`` and
        never the model's word: a description that cannot be supported by a readable version is not
        produced at all, so the handoff keeps its generic sentence instead.
        """

        if self.artifact_port is None:
            return None
        owner_ref = self._owner_artifact_ref(artifact_ref)
        version = str(version_ref or "").strip()
        if not owner_ref or not version:
            return None
        # This is a deliberate re-check of the version the owner just confirmed, not a reuse of a
        # read made earlier in the round.  The write itself went through AR-0's own port (it
        # predates the round memo), so the memo can still hold the *pre-write* head and would make
        # a real confirmed change look unsupported.  Drop it before asking the owner.
        self._invalidate_owner_reads()
        try:
            view = self.artifact_port.read_artifact(owner_ref)
        except Exception as exc:  # a failed read is reported, never dressed as a description
            base_error = "%s: %s" % (type(exc).__name__, exc)
            self.last_error = base_error[:200]
            return None
        if not isinstance(view, Mapping):
            return None
        artifact = dict(view.get("artifact") or {}) if isinstance(view.get("artifact"), Mapping) else {}
        head = dict(view.get("head") or {}) if isinstance(view.get("head"), Mapping) else {}
        head_id = str(head.get("version_id") or artifact.get("head_version_id") or "")
        if head_id and head_id != version:
            # The head moved on: a description of today's head would be about a *different* version
            # than the one this handoff names, so nothing is claimed.
            return None
        body = view.get("content") if isinstance(view.get("content"), str) else ""
        if body.lstrip().startswith("<!--"):
            end = body.find("-->")
            if end >= 0:
                body = body[end + len("-->"):]
        description = " ".join(body.split())
        if not description:
            return None
        return {
            "artifact_id": owner_ref,
            "version_ref": "artifactversion:%s" % version,
            "title": str(artifact.get("title") or "") or None,
            # The tail is what she just wrote; the head of the document is the old framing.
            "result_description": description[-240:],
            "outcome_confirmed": True,
        }

    def _emit_progress_experience(self, out: Mapping[str, Any], base: dict[str, Any],
                                  *, artifact_ref: Any, version_ref: Any) -> None:
        """Tell the experience consumer that a verified change really happened.

        Delivery failures are recorded, never raised: AG-2 keeps the handoff pending for a retry and
        nothing about the work is rolled back.  When the owner's own version can be read back, the
        handoff carries C's optional ``owner_confirmed_context`` so Memory can say *what* changed
        instead of only "some artifact moved".
        """
        int_rec = out.get("integration_record")
        integration_id = getattr(int_rec, "integration_id", None)
        activity_ref = base.get("activity_ref")
        if not integration_id or not activity_ref:
            return
        artifact_refs = [r for r in (self._owner_artifact_ref(artifact_ref),
                                     f"artifactversion:{version_ref}" if version_ref else None)
                         if r]
        confirmed = self._owner_confirmed_context(artifact_ref=artifact_ref,
                                                  version_ref=version_ref)
        try:
            result = self.runtime.coordinator.emit_experience_handoff(
                integration_id=str(integration_id),
                activity_id=str(activity_ref),
                terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
                occurred_at=base.get("observed_at"),
                artifact_refs=artifact_refs,
                owner_confirmed_context=confirmed,
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
            # What the *consumer* said it did with the confirmed mapping (a content-free code).
            "owner_context": (deliveries[-1].get("owner_confirmed_context")
                              if deliveries else None),
            "confirmed_used": bool(confirmed),
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
        now = self._now_iso()
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
            content = self._next_content(point, observed_at=obs)
        except (life_pause.PauseConfirmedError, life_pause.RequestNotSentError):
            # W05R-R1c: a pause that stopped the content request (or a transport that cannot
            # coordinate it) is not a content failure -- it is reported as exactly what it is
            # by ``evaluate_once``, and nothing was written.
            raise
        except Exception as exc:
            if is_no_change(exc):
                # A legal "nothing to change" is not a content failure: no receipt is minted,
                # no version is asked for, and ``evaluate_once`` reports the no-op.
                raise
            # A real content failure: nothing is written, and the receipt says exactly that.
            # (One clause only -- a second ``except Exception`` here would be dead code, and the
            # no_change branch above must not fall through into the commit below with no content.)
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

    def _work_no_change(self, base: dict[str, Any], *, llm_calls: int) -> dict[str, Any]:
        """Report the work writer's explicit ``no_change`` as the legal no-op it is.

        No owner is called, no version/progress/experience exists and nothing is completed --
        the round really did consider the work and the subject really did have nothing to add.
        The model's own self-note is *not* copied here: it is text about a private work, and a
        report is not the place for it.
        """
        base["work_no_change"] = {"reason_code": "NO_CHANGE", "owner_called": False,
                                  "version_created": False}
        # v0.4 day-life: the no-op is *remembered*, not just reported.  The ledger entry is keyed by
        # this work's target **and the owner's own head revision it was said on**, so the very same
        # state is not asked again while a new revision (or a new page, or a different target) is a
        # fresh question.  It stores no text and no rationale -- only the fingerprint, refs, instant.
        #
        # A ``no_change`` can arrive while a *START* is still being adopted (the content writer is
        # called to build the first step), so there is no Activity and no bound readiness yet.  In
        # that case the round's own selected work -- target plus the head the owner reported -- is
        # what is recorded, never another Activity's target and never a guessed revision.
        recorded = None
        try:
            readiness = self._round_readiness
            binding = self._round_work_binding or {}
            target_ref = (readiness.target_ref if readiness is not None and readiness.target_ref
                          else binding.get("target_ref"))
            revision = (readiness.source_revision
                        if readiness is not None and readiness.source_revision
                        else binding.get("source_revision"))
            recorded = self.reconsideration.record(
                readiness=(readiness if readiness is not None else lsr.StepReadiness(
                    available=False, reason_code=lsr.REASON_WORK_NO_CHANGE)),
                outcome=OUTCOME_NO_ACTION,
                at=str(base.get("observed_at") or self._now_iso()),
                target_ref=target_ref, source_revision=revision,
                reason_code=lsr.REASON_WORK_NO_CHANGE,
                activity_ref=(readiness.activity_ref if readiness is not None else None))
        except OSError as exc:  # a failed ledger write never fails the round
            base["reconsideration_error"] = "%s: %s" % (type(exc).__name__, exc)
        if recorded is not None:
            base["work_no_change"]["recorded"] = recorded.get("fingerprint")
            base["work_no_change"]["recorded_target_ref"] = recorded.get("target_ref")
            base["work_no_change"]["recorded_source_revision"] = recorded.get("source_revision")
        else:
            # Nothing to bind: say so rather than implying a no_change was remembered.
            base["work_no_change"]["recorded"] = None
        return self._finish(base, OUTCOME_NO_ACTION, llm_calls=llm_calls,
                            detail="the writer reported no change: nothing was written and "
                                   "no owner was called")

    def _journal(self, receipt: StepReceipt) -> None:
        path = self.state_root / JOURNAL_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    # -- reporting ---------------------------------------------------------------------

    def _finish(self, base: dict[str, Any], outcome: str, *, llm_calls: int,
                detail: str = "") -> dict[str, Any]:
        # A round whose committed Decision says ``RULE_RESOLVED`` made no model request by
        # AG-1's contract.  Clamped here (not only at one call site) so every exit path --
        # including a missing Decision on a rule-resolved attempt -- keeps the model-round
        # count low instead of turning a local rule into a provider call.
        if str(base.get("decision_route") or "") == ag1.ROUTE_RULE_RESOLVED:
            llm_calls = 0
        self.last_outcome = outcome
        # v0.4 C: the practice this round followed (and the one trial it named) are settled here, so
        # every exit path reports the same fields and the trial is written at most once.
        self._settle_method_binding(base)
        # v0.4 C: what the practice workspace really confirmed this round (a created review, a
        # saved method draft) -- reported per round, never as an inference from anything else.
        if self.last_review_created:
            base["review_created"] = dict(self.last_review_created)
        if self.last_method_created:
            base["method_created"] = dict(self.last_method_created)
        if self.last_practice_error:
            base["practice_error"] = str(self.last_practice_error)[:200]
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
        # v0.4 day-life: remember that this *whole* situation was considered and declined, so an
        # identical one -- in this process or after a restart -- is not asked again.  The keyed entry
        # is kept beside it for the no-step report and for release-by-ref; neither stores text, a
        # rationale or a Decision, and a real change (new page/head/place/pose/opportunity set)
        # produces a different fingerprint instead of needing a release.
        if outcome in lsr.RECORDED_OUTCOMES:
            at = str(base.get("observed_at") or self._now_iso())
            readiness = self._round_readiness
            try:
                situation = self._round_situation
                if isinstance(situation, Mapping) and situation:
                    entry = self.reconsideration.record_situation(
                        parts=situation, outcome=outcome, at=at,
                        target_ref=(readiness.target_ref if readiness is not None else None),
                        activity_ref=(readiness.activity_ref if readiness is not None else None))
                    if entry is not None:
                        base["reconsideration_situation"] = entry.get("fingerprint")
                if readiness is not None and readiness.bound:
                    keyed = self.reconsideration.record(
                        readiness=readiness, outcome=outcome, at=at)
                    if keyed is not None:
                        base["reconsideration_recorded"] = keyed.get("fingerprint")
            except OSError as exc:  # a failed ledger write never fails the round
                base["reconsideration_error"] = "%s: %s" % (type(exc).__name__, exc)
        # v0.4 day-life / R: what the owners *really* confirmed this round, and nothing else.  A
        # version id is only ever a ref an owner handed back; with no such evidence the answer is
        # ``None`` (unknown), never a guess and never "0 means healthy".
        base["owner_evidence"] = self._owner_evidence(base, outcome)
        # One round is over: the next wake must read the owners again, never reuse
        # this round's answers (even if it happens to carry the same instant).
        self.end_round_reads()
        return base

    #: Outcomes in which nothing reached an owner write, so "no version was created" is *known*.
    NO_WRITE_OUTCOMES = frozenset({
        OUTCOME_DISABLED, OUTCOME_BUDGET_EXHAUSTED, OUTCOME_NO_MATERIAL, OUTCOME_NO_ACTION,
        OUTCOME_DEFERRED, OUTCOME_PAUSED, OUTCOME_ABANDONED, OUTCOME_DECISION_FAILED,
        OUTCOME_ADOPTION_REJECTED, OUTCOME_STEP_REFUSED, OUTCOME_PAUSED_BY_OPERATOR,
        OUTCOME_NO_STEP_AVAILABLE, OUTCOME_QUIET,
    })
    #: Outcomes in which an owner may have acted and did not report back: the answer is unknown.
    UNKNOWN_WRITE_OUTCOMES = frozenset({OUTCOME_STEP_UNKNOWN, OUTCOME_STEP_UNSETTLED})

    def _owner_evidence(self, base: Mapping[str, Any], outcome: str) -> dict[str, Any]:
        """The owner-confirmed result of this round, as refs only -- or an honest unknown.

        ``owner_version_created`` is ``True`` only when an owner really handed back a record of a
        version/checkpoint/receipt it created, ``False`` when the round provably never reached an
        owner write, and ``None`` when an owner may have acted without a result being known.  This
        is what the runner forwards to the runtime-coverage accounting, so the coverage side never
        has to infer a version from ``ACTED``.
        """

        step_path = base.get("step_path")
        evidence: dict[str, Any] = {"owner_version_created": None, "owner_version_id": None,
                                    "step_path": str(step_path) if step_path else None}
        receipt = base.get("step_receipt")
        if isinstance(receipt, Mapping):
            effect_state = str(receipt.get("effect_state") or "")
            version_id = receipt.get("version_id")
            if effect_state == str(EffectState.APPLIED) and version_id:
                evidence["owner_version_created"] = True
                evidence["owner_version_id"] = "artifactversion:%s" % str(version_id)
                return evidence
            if effect_state == str(EffectState.NO_CHANGE):
                evidence["owner_version_created"] = False
                return evidence
        for key, prefix in (("checkpoint_update_ref", "checkpoint"),
                            ("consumption_receipt_ref", "receipt")):
            ref = base.get(key)
            if ref:
                evidence["owner_version_created"] = True
                evidence["owner_version_id"] = "%s:%s" % (prefix, str(ref))
                return evidence
        if outcome in self.UNKNOWN_WRITE_OUTCOMES:
            return evidence
        # A definite pre-submission refusal is same-round evidence, not an inference: AR-0
        # kept the Action at PREPARED and recorded the attempt as PRE_SUBMIT_FAILED, so no
        # owner write can have happened.  Only the classified refusal may use this shortcut,
        # so a misclassified or ambiguous round still reports ``None`` (unknown).
        pre_submit = base.get("pre_submit")
        if (outcome == OUTCOME_STEP_REFUSED
                and isinstance(pre_submit, Mapping)
                and pre_submit.get("not_sent") is True):
            evidence["owner_version_created"] = False
            return evidence
        evidence["owner_version_created"] = False if outcome in self.NO_WRITE_OUTCOMES else None
        return evidence

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
            # v0.4 day-life: read-only projections of the two things this round's suppression is
            # built on.  They report; they authorize nothing.
            "step_readiness": self.last_step_readiness,
            "reconsideration": self.last_reconsideration,
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
    "OUTCOME_NO_STEP_AVAILABLE",
    "OUTCOME_PAUSED",
    "OUTCOME_QUIET",
    "OUTCOME_STEP_REFUSED",
    "OUTCOME_STEP_UNKNOWN",
    "TRIGGER_IDLE_WINDOW",
    "TRIGGER_RECOVERY",
    "TRIGGER_RESUME_OPENED",
    "TRIGGER_USER_EVENT",
]
