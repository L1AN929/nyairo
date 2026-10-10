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
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol

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
from action_reality_ledger import ACTION_KIND_FILE
from managed_artifact_action_adapter import OP_COMMIT_VERSION

#: Master switch.  Autonomous life is OFF unless the operator turns it on.
ENV_AUTONOMOUS_ENABLED = "CHIYO_AUTONOMOUS_LIFE_ENABLED"
#: Rounds per day the cycle may even *consider* running.  A round that stops before any model
#: call does not spend budget.
ENV_MAX_ROUNDS_PER_DAY = "CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY"
DEFAULT_MAX_ROUNDS_PER_DAY = 4

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

#: Reason code used when a step is not attempted because the work already moved.
REASON_HEAD_ADVANCED = "ARTIFACT_HEAD_ADVANCED"
#: Reason code used when the owner refuses the write.
REASON_OWNER_REFUSED = "ARTIFACT_OWNER_REFUSED"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


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
    """A small persisted per-day round counter.  Exhaustion never destroys state."""

    def __init__(self, state_root: Path | str, *, max_rounds_per_day: int) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / BUDGET_FILENAME
        self.max_rounds_per_day = max(0, int(max_rounds_per_day))

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def spent_today(self) -> int:
        data = self._load()
        if str(data.get("day")) != _local_day():
            return 0
        return int(data.get("spent") or 0)

    def remaining(self) -> int:
        return max(0, self.max_rounds_per_day - self.spent_today())

    def spend(self) -> int:
        """Count one round.  Returns the new total for today."""
        day = _local_day()
        spent = self.spent_today() + 1
        payload = {"day": day, "spent": spent, "max_rounds_per_day": self.max_rounds_per_day,
                   "updated_at": _utc_now_iso()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        return spent


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
        max_rounds_per_day: Optional[int] = None,
        enabled: Optional[bool] = None,
        env: Optional[Mapping[str, str]] = None,
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
        self.budget = RoundBudget(self.state_root, max_rounds_per_day=max_rounds_per_day)

        #: The artifact continuation source, when a port was supplied.  Registered so AG-0 can
        #: actually see the work; the registry refuses unknown or forbidden source kinds itself.
        self.artifact_source: Optional[ArtifactContinuationSource] = None
        if artifact_port is not None and subject_id:
            self.artifact_source = ArtifactContinuationSource(
                artifact_port, subject_id=subject_id)
            runtime.candidate_registry.register_adapter(self.artifact_source)

        self.last_outcome: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_receipt: Optional[StepReceipt] = None
        #: E07: the same bounded, governance-filtered experience view the content writer gets, so a
        #: decision may weigh the same past the writing does.  Empty means memory is not consulted.
        self.experience_provider = experience_provider
        self.rounds_run = 0

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

        if self.artifact_port is not None:
            try:
                point = self.artifact_port.latest_resumable()
            except (LifeArtifactUnavailable, ArtifactOwnerRefused) as exc:
                found["artifact_read_error"] = str(exc)[:200]
                point = None
            if point is not None:
                found["continuable_artifact"] = point.to_dict()

        found["has_material"] = bool(
            found["foreground_activity"] or found["continuable_artifact"])
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

        resume_point: Optional[ArtifactResumePoint] = None
        if self.artifact_port is not None:
            try:
                resume_point = self.artifact_port.latest_resumable()
            except (LifeArtifactUnavailable, ArtifactOwnerRefused) as exc:
                base["artifact_read_error"] = str(exc)[:200]

        # An adoption spec describes how to *create* an Activity, so it is only supplied when
        # nothing is running.  With a foreground Activity present the only legal move is to
        # continue/resume that one, and offering a creation spec there would be asking AG-2 to
        # start a second foreground activity -- which it correctly refuses.
        has_foreground = bool(material.get("foreground_activity"))
        adoption_spec = (self._adoption_spec(resume_point)
                         if resume_point and not has_foreground else None)

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
        base["step_path"] = "AR0" if ar0_step is not None else "PORT_DIRECT"

        # A real capacity signal instead of "unknown": what she actually has left is the round
        # budget this cycle is allowed to spend and whether a step can be executed at all.  With
        # UNKNOWN the model reasonably defers forever, which is honest but not living.
        if not act or not self.artifact_grant_id:
            capacity = ag1.CAPACITY_UNAVAILABLE
        elif self.budget.remaining() <= 1:
            capacity = ag1.CAPACITY_LIMITED
        else:
            capacity = ag1.CAPACITY_AVAILABLE
        base["capacity_state"] = capacity

        try:
            out = self.runtime.coordinator.run_decision_and_adoption_cycle(
                trigger_kind=trigger_kind,
                trigger_ref=trigger_ref,
                observed_at=obs,
                adoption_spec=adoption_spec,
                step_spec_override=ar0_step,
                execute_initial_step=ar0_step is not None,
                body_capacity_state=capacity,
                prior_experiences=self._experience_view(),
            )
        except ag2.KillSwitchDisabledError as exc:
            return self._finish(base, OUTCOME_DISABLED, llm_calls=0, detail=str(exc))
        except Exception as exc:  # a real failure, reported as one
            self.last_error = f"{type(exc).__name__}: {exc}"
            if type(exc).__name__ == "ModelContentError" or "content writer" in str(exc):
                # The decision happened; producing the content did not.  Nothing was written.
                return self._finish(base, OUTCOME_STEP_REFUSED, llm_calls=1,
                                    detail=f"content writer failed: {self.last_error}"[:300])
            return self._finish(base, OUTCOME_DECISION_FAILED, llm_calls=0, detail=self.last_error)

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
        })

        if record is None:
            failure = str(dec_res.get("failure_class") or "DECISION_NOT_COMMITTED")
            self.last_error = failure
            return self._finish(base, OUTCOME_DECISION_FAILED, llm_calls=1, detail=failure)

        decision = str(getattr(record, "decision", ""))
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

        # Adopted.  Now do the one narrow real step.
        if ar0_step is not None:
            return self._classify_ar0_step(out, base, obs)

        if not act or resume_point is None or not self.artifact_grant_id or not self.workspace_id:
            return self._finish(base, OUTCOME_ACTED, llm_calls=1,
                                detail="adopted with no narrow step available in this revision")

        # No AR-0 adapter is wired, so no action crosses a submission fence.  The step is still
        # performed against the real owner through the narrow port and its receipt is journalled.
        receipt = self._commit_next_version(resume_point, base, obs)

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
            self._record_artifact_progress(
                base, activity_ref=base.get("activity_ref"),
                artifact_id=payload.get("artifact_id"), version_id=details.get("version_id"),
                version_number=details.get("version_number"),
                correlation_id=base.get("correlation_id"), action_ref=action.get("action_id"))
            # A real, owner-confirmed change is the milestone Memory cares about.  AG-2 only
            # emitted COMPLETED/ABANDONED/CONFLICT before, so "she actually changed something"
            # had no handoff at all.
            self._emit_progress_experience(
                out, base, artifact_ref=payload.get("artifact_id"),
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
        artifact_refs = [r for r in (f"artifact:{artifact_ref}" if artifact_ref else None,
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
        base.update({"outcome": outcome, "detail": detail, "llm_calls": llm_calls,
                     "budget_remaining": self.budget.remaining()})
        return base

    def status(self) -> dict[str, Any]:
        """Read-only projection for the thin status entry (WP-09)."""
        point = None
        if self.artifact_port is not None:
            try:
                found = self.artifact_port.latest_resumable()
                point = found.to_dict() if found else None
            except (LifeArtifactUnavailable, ArtifactOwnerRefused) as exc:
                point = {"error": str(exc)[:200]}
        return {
            "autonomous_enabled": self.enabled,
            "rounds_run": self.rounds_run,
            "budget": {"max_rounds_per_day": self.budget.max_rounds_per_day,
                       "spent_today": self.budget.spent_today(),
                       "remaining_today": self.budget.remaining()},
            "last_outcome": self.last_outcome,
            "last_error": self.last_error,
            "last_step_receipt": self.last_receipt.to_dict() if self.last_receipt else None,
            "current_activity_ref": self.runtime.canonical_state().get("foreground_activity_ref"),
            "current_work": point,
            "switches": self.runtime.kill_switches.to_dict(),
        }


def autonomous_life_cycle_from_env(runtime: Any, *, artifact_port: LifeArtifactPort,
                                   subject_id: str, workspace_id: str, artifact_grant_id: str,
                                   writer: Optional[ContinuationWriter] = None,
                                   experience_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
                                   env: Optional[Mapping[str, str]] = None) -> AutonomousLifeCycle:
    """Assemble the cycle with the switches and budget read from the environment."""
    return AutonomousLifeCycle(
        runtime,
        artifact_port=artifact_port,
        subject_id=subject_id,
        workspace_id=workspace_id,
        artifact_grant_id=artifact_grant_id,
        writer=writer,
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
