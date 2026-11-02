#!/usr/bin/env python3
"""Zero-model real-owner demo: Activity FAILED / BLOCKED outcomes.

What it really does (local only, 0 product model, 0 network)
------------------------------------------------------------
1. Starts a **real** Life Supply owner on its own Unix socket and prepares a real instance
   through ``one_day_life.prepare`` (real workspace/grant/seed artifact).
2. Assembles the **real** production Life runtime and ``assemble_autonomous_life``; Decision
   and content are scripted doubles, everything else (AG-0/AG-1/AG-2/LR-2/LR-4/LR-5/AR-0/
   Supply/Memory port) is real.
3. A: START the seed work and commit one real version.  The owner then archives the artifact.
   A following CONTINUE is refused by the owner (WORK_NOT_CONTINUABLE) but the Activity stays
   ACTIVE -- an Action / step refusal never becomes an Activity failure by itself.
4. S09 assessment: the owner reads the live Activity and the archived artifact, names the one
   failure criterion and the exact goal scope, and commits FAILED through the existing
   ACTIVITY_CONSEQUENCE capability.  The terminal releases the slot, keeps the committed
   version and checkpoint, writes no completion evidence, and emits exactly one handoff.
5. B: START a second real artifact, record BLOCKED on a real artifact-head condition, then
   start D on a third artifact.  The owner commits a new head for B: LR-4 alone emits OPEN,
   but D's foreground is not preempted and B stays BLOCKED.  D PAUSEs, a fresh NO_ACTION keeps
   B BLOCKED and the eligibility OPEN, then a fresh Decision RESUME returns the *same* B.
6. Ordered close and same-root re-assembly: A is still FAILED with its reason/refs, the one
   handoff is not duplicated, and B is still the same Activity.

Boundaries: no real model, no product instance, no wall-clock wait, no external send.  The
scripted Decision always chooses inside the AG-1 legal set; the failure assessment is an owner
fact re-read from the real Supply owner.  This is not G1, not a 24h run and not evidence that
a model naturally makes these choices.

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/activity_outcome_demo.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_outcome_assessment as ao  # noqa: E402
import activity_waiting_agenda as awa  # noqa: E402
import action_reality_ledger as ar0  # noqa: E402
import life_continuity_inputs as lci  # noqa: E402
from app.evidence import EvidenceStore  # noqa: E402
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402
from life_experience_port import LifeExperiencePort, life_conversation_id  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import life_wait_basis as lwb  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import one_day_life as odl  # noqa: E402
from components.phone import own_day_read  # noqa: E402

from tests.test_one_day_life import Services  # noqa: E402

SCRATCH = Path(os.environ.get("NYAIRO_ACTIVITY_OUTCOME_ROOT")
               or "/tmp/nyairo-activity-outcome")
RUN = SCRATCH / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
CLOSEOUT_REF_PREFIX = "closeout:"


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("DEMO_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:8000])
    raise SystemExit(1)


def _candidate_refs(row) -> list[str]:
    return [str(ref) for ref in (row.get("target_refs") or [])]


def _is_closeout_candidate(row) -> bool:
    return any(ref.startswith(CLOSEOUT_REF_PREFIX) for ref in _candidate_refs(row))


def _bare_artifact(artifact_id: str) -> str:
    """The owner's own id without any ``artifact:`` wrapper (candidates add one)."""

    text = str(artifact_id or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def _artifact_ref(artifact_id: str) -> str:
    return "artifact:" + _bare_artifact(artifact_id)


def _candidate_matches_artifact(row, artifact_id: str) -> bool:
    bare = _bare_artifact(artifact_id)
    return any(_bare_artifact(ref) == bare for ref in _candidate_refs(row))


def _offered_for(candidates, *, activity_id: str, artifact_id: str,
                 eligibility_id: str = "") -> list[dict]:
    """The prompt projection may omit ``activity_ref``; match its own target/eligibility refs."""

    bare = _bare_artifact(artifact_id)
    out: list[dict] = []
    for row in candidates:
        refs = [str(ref) for ref in (row.get("target_refs") or [])]
        if (
            str(row.get("activity_ref") or "") == activity_id
            or (eligibility_id
                and str(row.get("resume_eligibility_ref") or "") == eligibility_id)
            or any(_bare_artifact(ref) == bare for ref in refs)
        ):
            out.append(row)
    return out


class _ScriptedDecisionModel:
    """Decision double: chooses only inside the real AG-1 legal set."""

    model_id = "stub:activity-outcome-demo"
    model_version = "activity-outcome-demo-v1"

    def __init__(self) -> None:
        self.policy = "start"
        self.target_artifact = ""
        self.target_activity = ""
        self.calls = 0
        self.last_offered: dict = {}

    def _raw(self, candidates, allowed):
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        if self.policy == "start":
            matches = [row for row in candidates
                       if "START" in (row.get("legal_decisions") or [])
                       and not _is_closeout_candidate(row)
                       and (not self.target_artifact
                            or _candidate_matches_artifact(row, self.target_artifact))]
            if len(matches) >= 1:
                return {"decision": "START",
                        "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: start the target work",
                        "deferred_candidate_ids": []}
        if self.policy == "continue":
            matches = [row for row in candidates
                       if "CONTINUE" in (row.get("legal_decisions") or [])]
            if matches:
                return {"decision": "CONTINUE",
                        "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: continue",
                        "deferred_candidate_ids": []}
        if self.policy == "pause":
            matches = [row for row in candidates
                       if "PAUSE" in (row.get("legal_decisions") or [])]
            if matches:
                return {"decision": "PAUSE",
                        "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: pause",
                        "deferred_candidate_ids": []}
        if self.policy == "abandon":
            matches = [row for row in candidates
                       if "ABANDON" in (row.get("legal_decisions") or [])]
            if self.target_artifact:
                matches = [row for row in matches
                           if _candidate_matches_artifact(row, self.target_artifact)]
            if matches:
                return {"decision": "ABANDON",
                        "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason,
                        "short_rationale": "stub: end the suspended Activity",
                        "deferred_candidate_ids": []}
        if self.policy == "resume":
            matches = [row for row in candidates
                       if "RESUME" in (row.get("legal_decisions") or [])
                       and (not self.target_activity
                            or str(row.get("activity_ref") or "") == self.target_activity
                            or (self.target_artifact
                                and _candidate_matches_artifact(row, self.target_artifact)))]
            if matches:
                return {"decision": "RESUME",
                        "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: resume the same Activity",
                        "deferred_candidate_ids": []}
        return {"decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": ["NO_ACTION_CHOSEN"],
                "short_rationale": "stub: no action", "deferred_candidate_ids": []}

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        self.last_offered = {
            "policy": self.policy,
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "candidate_kind": row.get("candidate_kind"),
                            "source_kind": row.get("source_kind"),
                            "activity_ref": row.get("activity_ref"),
                            "target_refs": row.get("target_refs"),
                            "legal_decisions": row.get("legal_decisions")} for row in candidates],
        }
        raw = self._raw(candidates, allowed)
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:activity-outcome-demo:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class _Writer:
    def __init__(self) -> None:
        self.calls = 0

    def next_content(self, point, *, observed_at: str) -> str:
        self.calls += 1
        return "%s\n\n（真实版本追加 %s）\n" % (point.content or "", observed_at)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _activity(runtime, activity_id: str) -> dict:
    return dict(runtime.activity_read.get_activity(activity_id) or {})


def _head(port, artifact_id: str) -> str:
    row = port.read_head(artifact_id) or {}
    return str(row.get("version_id") or "")


def _handoffs(runtime, activity_id: str, *, kind: str | None = None) -> list[dict]:
    state = runtime.coordinator.integration_commit.read_state() or {}
    return [dict(row) for row in (state.get("handoff_events_by_id") or {}).values()
            if str(row.get("activity_ref") or "") == activity_id
            and (kind is None or row.get("terminal_or_milestone_kind") == kind)]


def _integration_id(runtime, activity_id: str) -> str:
    state = runtime.coordinator.integration_commit.read_state() or {}
    rows = (state.get("integration_ids_by_activity") or {}).get(activity_id) or []
    return str(rows[0]) if rows else ""


def _conditions(runtime) -> dict:
    state = runtime.waiting_coordinator.condition_store.load_state() or {}
    return dict(state.get("conditions") or {})


def _eligibilities(runtime) -> dict:
    state = runtime.waiting_coordinator.eligibility_store.load_state() or {}
    return dict(state.get("eligibilities") or {})


def _new_activity_id(runtime, before: set[str]) -> str:
    after = {str(row.get("activity_id"))
             for row in runtime.activity_read.list_open_activities()}
    new = sorted(after - before)
    return new[0] if len(new) == 1 else ""


MEMORY_OWNER = "chiyo"


def _memory_fixture(root: Path) -> dict:
    """The real M0 boundary: one evidence store, one control ledger, the existing consumer."""

    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    evidence = root / "evidence.sqlite"
    controls = root / "controls.sqlite"
    EvidenceStore(evidence)
    MemoryControlLedger(controls, evidence, owner=MEMORY_OWNER,
                        conversation=life_conversation_id(MEMORY_OWNER),
                        authority="").initialize()
    port = LifeExperiencePort(evidence, controls_db=controls, subject=MEMORY_OWNER,
                              authority_db="")
    consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True)
    return {"root": root, "evidence": evidence, "controls": controls,
            "port": port, "consumer": consumer}


def _build_runtime(instance: Path, state_root: Path, services, data, cognition, writer,
                   port: lap.LifeArtifactPort, env: dict, *, consumer=None):
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=instance / "life", artifact_action_adapter=adapter,
        experience_consumer=consumer)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=env, state_root=state_root, writer=writer, enabled=True)
    if assembled.get("state") != assembly.READY:
        _check(False, "assembly not READY", assembled)
    return runtime, assembled["cycle"]


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    services = Services(RUN / "services")
    runtime = None
    summary: dict = {
        "root": str(RUN),
        "checks": {},
        "limitations": [
            "the Decision and content writer are scripted doubles; no product model was called",
            "the artifact owner, Activity/LR-2..LR-5/AR-0 stack, AG-0/AG-1/AG-2, the M0"
            " EvidenceStore/control ledger and the Memory admission consumer are real",
            "the failure criterion is owner evidence (ARCHIVED artifact), not an Action refusal;"
            " the UNKNOWN case injects a lost response after a real owner write",
            "BLOCKED/BLOCK is recorded by the S09 owner gate on a named basis; the unconditional"
            " case binds no LR-4 condition and the exit is a fresh scripted Decision ABANDON",
            "this is not G1, not a 24h run and not evidence of natural model adoption",
        ],
    }
    try:
        instance = RUN / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:activity-outcome",
            title="Activity outcome demo",
            seed_content="种子正文第一句：真实 Supply 作品。\n")
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        })
        state_root = odl.life_state_root(instance)
        seed = str(data["artifact_id"])
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        memory = _memory_fixture(RUN / "memory")
        cognition = _ScriptedDecisionModel()
        writer = _Writer()
        cognition.target_artifact = seed
        runtime, cycle = _build_runtime(instance, state_root, services, data,
                                        cognition, writer, port, env,
                                        consumer=memory["consumer"])
        t0 = datetime.now(timezone.utc)
        _clock = {"n": 0}

        def _tick() -> str:
            _clock["n"] += 1
            return _iso(t0 + timedelta(minutes=_clock["n"]))

        # ---------------------------------------------------------------- A: real START + one step
        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        first = cycle.evaluate_once(trigger_ref="evt:outcome:1", observed_at=_tick(), act=True)
        act_a = _new_activity_id(runtime, open_before) or str(first.get("activity_ref") or "")
        _check(bool(act_a), "round 1 created no Activity", {
            key: first.get(key) for key in (
                "outcome", "decision", "adoption_status", "selected_candidate_ref",
                "detail", "error", "work_no_change")
        } | {"offered": cognition.last_offered})
        a_after_start = _activity(runtime, act_a)
        head_v1 = _head(port, seed)
        _check(a_after_start.get("status") == "ACTIVE",
               "A is not ACTIVE after its first real step", a_after_start)
        _check(bool(head_v1), "the owner reports no head after the first step", a_after_start)
        summary["round_a_start"] = {
            "activity_ref": act_a, "status": a_after_start.get("status"),
            "head": head_v1, "outcome": first.get("outcome"),
            "decision": first.get("decision"), "adoption_status": first.get("adoption_status"),
        }

        # The real owner archives the work.  A following CONTINUE must be refused as a *step*,
        # not silently turned into an Activity failure.
        artifact_view = port.read_artifact(seed) or {}
        artifact_row = artifact_view.get("artifact") or {}
        archived = port.archive_artifact(
            artifact_id=seed, expected_revision=int(artifact_row.get("revision") or 0),
            grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("activity-outcome-demo", "archive-a", seed))
        _check(archived.applied, "the owner did not archive the work", archived.to_dict())
        a_before_continue = _activity(runtime, act_a)
        cognition.policy = "continue"
        second = cycle.evaluate_once(trigger_ref="evt:outcome:2", observed_at=_tick(), act=True)
        a_after_continue = _activity(runtime, act_a)
        _check(a_after_continue.get("status") == "ACTIVE",
               "a definite step refusal turned the Activity into a terminal state by itself",
               {"before": a_before_continue, "after": a_after_continue,
                "round": {k: second.get(k) for k in (
                    "outcome", "decision", "decision_route", "adoption_status",
                    "step_path", "detail", "error")}})
        _check(_head(port, seed) == head_v1,
               "a refused step still changed the owner head", _head(port, seed))
        summary["round_a_refused_step"] = {
            "status": a_after_continue.get("status"),
            "decision": second.get("decision"),
            "outcome": second.get("outcome"),
            "step_path": second.get("step_path"),
            "detail": second.get("detail"),
            "head_unchanged": _head(port, seed) == head_v1,
        }

        # ---------------------------------------------------------------- S09 explicit FAIL
        act_live = _activity(runtime, act_a)
        scope = [ref for ref in (act_live.get("subject_refs") or []) if str(ref)]
        _check(bool(scope), "A names no goal scope to assess", act_live)
        assessment = runtime.activity_outcome.assess_failure(
            act_a,
            goal_scope_refs=scope[:1],
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_ARTIFACT_ARCHIVED],
            evidence_refs=[_artifact_ref(seed)],
            unresolved_action_refs=[],
            assessed_at=_tick(),
        )

        # Negative controls, before anything is written:
        # 1. the step refusal above left NO_FAIL transition of its own;
        # 2. a stale revision is refused;
        # 3. an unreadable evidence ref is refused (it is not "the goal is unreachable");
        # 4. a bare mapping / model-style payload is refused outright.
        pre_fail_history = runtime.activity_read.get_transition_history(activity_id=act_a) or []
        _check(not any(row.get("transition_type") == "FAIL" for row in pre_fail_history),
               "an Action/step refusal wrote a FAIL transition by itself", pre_fail_history)
        negative: dict[str, Any] = {"no_automatic_fail_transition": True}
        stale = dataclasses.replace(assessment, activity_revision=assessment.activity_revision - 1)
        try:
            runtime.activity_outcome.fail_activity(
                act_a, stale, idempotency_key="lr2_fail:stale-revision")
            _check(False, "a stale-revision assessment was accepted")
        except ao.FailureAssessmentError as exc:
            negative["stale_revision_refused"] = str(exc)[:160]
        try:
            runtime.activity_outcome.assess_failure(
                act_a, goal_scope_refs=scope[:1],
                criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_ARTIFACT_ARCHIVED],
                evidence_refs=["artifact:" + "0" * 32],
                assessed_at=_tick())
            _check(False, "unreadable owner evidence was accepted")
        except ao.FailureAssessmentError as exc:
            negative["unreadable_evidence_refused"] = str(exc)[:160]
        try:
            runtime.activity_outcome.fail_activity(
                act_a,
                {"assessment_id": assessment.assessment_id, "verdict": "FAILED",
                 "verified": True},
                idempotency_key="lr2_fail:forged-mapping")
            _check(False, "a bare/forged mapping was accepted as an assessment")
        except ao.FailureAssessmentError as exc:
            negative["forged_mapping_refused"] = str(exc)[:160]
        _check(_activity(runtime, act_a).get("status") == "ACTIVE",
               "a refused negative control changed the Activity", _activity(runtime, act_a))

        # Cross-item negative: a *different* piece is archived while A's own bound artifact is
        # not.  B's owner fact must not sign A's failure -- the goal scope is A's, not any
        # readable archived row.  Both entries (assess and fail) must refuse, and A must not
        # move (status/revision/checkpoint/last action/handoff).
        other_artifact = port.create_artifact(
            workspace_id=str(data["workspace_id"]), title="别件作品",
            content="与本次活动无关的另一件作品。\n", grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "create-other", "neg"))
        _check(other_artifact.applied, "the owner did not create the unrelated piece",
               other_artifact.to_dict())
        other_artifact_id = str(other_artifact.artifact_id or "")
        other_row = (port.read_artifact(other_artifact_id) or {}).get("artifact") or {}
        other_archived = port.archive_artifact(
            artifact_id=other_artifact_id, expected_revision=int(other_row.get("revision") or 0),
            grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "archive-other",
                                                other_artifact_id))
        _check(other_archived.applied, "the owner did not archive the unrelated piece",
               other_archived.to_dict())
        a_before_cross = _activity(runtime, act_a)
        try:
            runtime.activity_outcome.assess_failure(
                act_a, goal_scope_refs=scope[:1],
                criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_ARTIFACT_ARCHIVED],
                evidence_refs=[_artifact_ref(other_artifact_id)], assessed_at=_tick())
            _check(False, "an unrelated archived artifact signed A's failure through assess",
                   {"other": other_artifact_id, "activity": a_before_cross})
        except ao.FailureAssessmentError as exc:
            negative["unrelated_archived_artifact_assess_refused"] = str(exc)[:200]
            _check("goal scope" in str(exc),
                   "the unrelated artifact was refused by grammar, not by goal-scope binding",
                   str(exc))
        cross_assessment = ao.build_failure_assessment(
            a_before_cross, goal_scope_refs=scope[:1],
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_ARTIFACT_ARCHIVED],
            evidence_refs=[_artifact_ref(other_artifact_id)], assessed_at=_tick())
        try:
            runtime.activity_outcome.fail_activity(
                act_a, cross_assessment,
                idempotency_key="lr2_fail:activity-outcome-demo:cross-item")
            _check(False, "an unrelated archived artifact signed A's failure through fail",
                   {"other": other_artifact_id, "activity": a_before_cross})
        except ao.FailureAssessmentError as exc:
            negative["unrelated_archived_artifact_fail_refused"] = str(exc)[:200]
        a_after_cross = _activity(runtime, act_a)
        _check(a_after_cross.get("status") == "ACTIVE"
               and int(a_after_cross.get("revision", -1)) == int(a_before_cross.get("revision", -1))
               and a_after_cross.get("checkpoint_ref") == a_before_cross.get("checkpoint_ref")
               and a_after_cross.get("last_action_ref") == a_before_cross.get("last_action_ref")
               and not _handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED),
               "a refused cross-item failure moved A or emitted a handoff",
               {"before": a_before_cross, "after": a_after_cross})
        summary["cross_item_negative"] = {
            "other_artifact_archived": other_archived.applied,
            "a_own_artifact_archived": False,
            "assess_refused": bool(negative.get("unrelated_archived_artifact_assess_refused")),
            "fail_refused": bool(negative.get("unrelated_archived_artifact_fail_refused")),
            "a_status_unchanged": a_after_cross.get("status"),
            "a_revision_unchanged": a_after_cross.get("revision")
            == a_before_cross.get("revision"),
            "handoff_count": len(_handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)),
        }

        fail_result = runtime.activity_outcome.fail_activity(
            act_a, assessment,
            idempotency_key="lr2_fail:activity-outcome-demo:%s" % act_a,
            occurred_at=_tick(), observed_at=_tick())
        _check(bool(fail_result.committed), "the FAIL transition did not commit", fail_result)
        a_failed = _activity(runtime, act_a)
        _check(a_failed.get("status") == "FAILED", "A is not FAILED", a_failed)
        _check(runtime.activity_read.get_current_life_view().get("foreground_activity_ref") is None,
               "FAILED did not release the foreground")
        _check(a_failed.get("failure_assessment_ref") == assessment.assessment_id,
               "the failure assessment ref was not stored", a_failed)
        _check("criterion:%s" % ao.FAILURE_CRITERION_ARTIFACT_ARCHIVED
               in (a_failed.get("failure_criteria_refs") or []),
               "the failure criterion was not stored", a_failed)
        _check(a_failed.get("completion_evidence_ref") is None,
               "a failure wrote completion evidence", a_failed)
        _check(a_failed.get("checkpoint_ref") == act_live.get("checkpoint_ref")
               and a_failed.get("last_action_ref") == act_live.get("last_action_ref"),
               "the failure erased the checkpoint/last action", a_failed)
        _check(_head(port, seed) == head_v1,
               "the failure rewrote the already committed version", _head(port, seed))

        # The *production* fail entry -- not this script -- must finish the one AG-2 handoff.
        op_handoff = (getattr(fail_result, "failure_handoff", None)
                      or runtime.activity_outcome.last_failure_handoff or {})
        _check(bool(op_handoff.get("handoff_id")),
               "the committed FAILED did not finish its AG-2 handoff", op_handoff)
        _check(op_handoff.get("handoff_kind") == ag2.HANDOFF_KIND_FAILED,
               "the production handoff is not FAILED", op_handoff)
        _check(op_handoff.get("consumer_disposition") in {"ACCEPT", "DUPLICATE"},
               "the real M0 consumer did not take the FAILED handoff", op_handoff)
        events = _handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)
        _check(len(events) == 1, "the FAILED activity does not have exactly one handoff", events)
        ev = events[0]
        _check(ev.get("terminal_or_milestone_kind") == "FAILED",
               "the handoff kind is not FAILED", ev)
        _check(ev.get("completion_evidence_ref") is None,
               "the FAILED handoff carries completion evidence", ev)
        _check(assessment.assessment_id in (ev.get("fact_refs") or []),
               "the FAILED handoff does not carry the assessment ref", ev)
        # Repeating the same fail *operation* reuses the original transition and handoff.
        replayed_fail = runtime.activity_outcome.fail_activity(
            act_a, assessment,
            idempotency_key="lr2_fail:activity-outcome-demo:%s" % act_a,
            occurred_at=_tick(), observed_at=_tick())
        _check(bool(replayed_fail.idempotent_replay),
               "a repeated fail operation was not an owner replay", replayed_fail)
        replay_handoff = (getattr(replayed_fail, "failure_handoff", None)
                          or runtime.activity_outcome.last_failure_handoff or {})
        _check(bool(replay_handoff.get("idempotent_replay")),
               "a repeated fail operation minted a second handoff", replay_handoff)
        _check(len(_handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)) == 1,
               "a repeated fail operation created a second event",
               _handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED))
        summary["failed"] = {
            "activity_ref": act_a,
            "status": a_failed.get("status"),
            "terminal_reason_code": a_failed.get("terminal_reason_code"),
            "failure_assessment_ref": a_failed.get("failure_assessment_ref"),
            "failure_criteria_refs": a_failed.get("failure_criteria_refs"),
            "failure_evidence_refs": a_failed.get("failure_evidence_refs"),
            "unresolved_action_refs": a_failed.get("unresolved_action_refs"),
            "completion_evidence_ref": a_failed.get("completion_evidence_ref"),
            "checkpoint_preserved": a_failed.get("checkpoint_ref") == act_live.get("checkpoint_ref"),
            "handoff_id": ev.get("handoff_id"),
            "handoff_kind": ev.get("terminal_or_milestone_kind"),
            "handoff_fact_refs": ev.get("fact_refs"),
            "handoff_finished_by_fail_entry": op_handoff.get("status"),
            "handoff_consumer_disposition": op_handoff.get("consumer_disposition"),
            "handoff_replay_on_repeat": bool(replay_handoff.get("idempotent_replay")),
            "negative_controls": negative,
        }

        # A terminal Activity must not be re-offered CONTINUE/RESUME or revive an old event.
        revision_after_fail = int(_activity(runtime, act_a).get("revision", -1))
        cognition.policy = "no_action"
        third = cycle.evaluate_once(trigger_ref="evt:outcome:3", observed_at=_tick(), act=True)
        offered_a = [row for row in (cognition.last_offered.get("candidates") or [])
                     if str(row.get("activity_ref") or "") == act_a]
        _check(not any("CONTINUE" in (row.get("legal_decisions") or [])
                       or "RESUME" in (row.get("legal_decisions") or [])
                       for row in offered_a),
               "a FAILED Activity is still offered CONTINUE/RESUME", offered_a)
        _check(int(_activity(runtime, act_a).get("revision", -1)) == revision_after_fail,
               "a later round mutated the FAILED Activity",
               _activity(runtime, act_a))
        _check(len(_handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)) == 1,
               "a later round emitted another FAILED handoff", _handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED))
        summary["after_failed_round"] = {
            "decision": third.get("decision"), "outcome": third.get("outcome"),
            "offered_a": offered_a, "revision_unchanged": True,
            "handoff_count": len(_handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)),
        }

        # ------------------------------------------------ E: unconditional BLOCKED, legal exit
        artifact_e = port.create_artifact(
            workspace_id=str(data["workspace_id"]), title="第四件作品",
            content="第四件作品的正文。\n", grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "create-e", "e"))
        _check(artifact_e.applied, "the owner did not create artifact E", artifact_e.to_dict())
        artifact_e_id = str(artifact_e.artifact_id or "")
        cognition.policy = "start"
        cognition.target_artifact = artifact_e_id
        cognition.target_activity = ""
        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        ninth = cycle.evaluate_once(trigger_ref="evt:outcome:e1", observed_at=_tick(), act=True)
        act_e = _new_activity_id(runtime, open_before) or str(ninth.get("activity_ref") or "")
        _check(bool(act_e), "no Activity was started for E", {
            "round": ninth, "offered": cognition.last_offered})
        _check(_activity(runtime, act_e).get("status") == "ACTIVE",
               "E is not ACTIVE", _activity(runtime, act_e))

        # An *unconditional* BLOCK: a named owner basis, no LR-4 condition, no eligibility.
        e_conditions_before = set(_conditions(runtime))
        e_eligibilities_before = set(_eligibilities(runtime))
        blocked_e = runtime.activity_outcome.block_activity(
            act_e,
            blocked_on_ref="block_basis:activity-outcome-demo.e.unconditional",
            blocked_reason_code="BLOCKED_BY_CONDITION",
            idempotency_key="lr2_block:activity-outcome-demo:%s" % act_e,
            occurred_at=_tick(), observed_at=_tick())
        _check(bool(blocked_e.committed), "the unconditional BLOCK did not commit", blocked_e)
        e_blocked = _activity(runtime, act_e)
        _check(e_blocked.get("status") == "BLOCKED", "E is not BLOCKED", e_blocked)
        _check(runtime.activity_read.get_current_life_view().get("foreground_activity_ref") is None,
               "the unconditional BLOCK did not release the foreground", e_blocked)
        _check(set(_conditions(runtime)) == e_conditions_before,
               "an unconditional BLOCK invented a recovery condition", _conditions(runtime))
        _check(set(_eligibilities(runtime)) == e_eligibilities_before,
               "an unconditional BLOCK invented an eligibility", _eligibilities(runtime))

        # Next round's material: the suspension is visible as unfinished, is *not* a
        # continuation and offers no RESUME without an eligibility of its own.
        binding = lci.ContinuityBinding(
            subject=str(data["subject_id"]), home=str(state_root), now=_tick())
        continuity = lci.LifeContinuityInputs(
            binding=binding, activity_source=runtime.activity_read,
            agenda_source=runtime.waiting_coordinator).read()
        e_item = next((item for item in continuity.unfinished
                       if str(item.get("item_ref")) == act_e), None)
        _check(e_item is not None and e_item.get("status") == "BLOCKED",
               "the BLOCKED Activity is missing from the unfinished material", {
                   "unfinished": continuity.unfinished, "e": act_e})
        _check(e_item.get("continue_allowed") is False and e_item.get("resumable") is False,
               "a BLOCKED Activity was projected as continuable/resumable", e_item)
        _check((continuity.next_step or {}).get("kind") == lci.NEXT_STEP_BLOCKED,
               "the next-step material did not name the suspension",
               {"next_step": continuity.next_step,
                "unfinished": continuity.unfinished})
        e_choice = next((row for row in continuity.choices
                         if str(row.get("subject_ref")) == act_e), None)
        _check(e_choice is not None and e_choice.get("kind") == lci.CHOICE_BLOCKED,
               "the suspension was offered as a continuation/resume", e_choice)

        # The phone-side read carries the owner's own status/basis, not a re-labelled ACTIVE.
        day_e = (datetime.fromisoformat(str(e_blocked.get("started_at") or _tick()))
                 .astimezone(timezone(timedelta(hours=8))).date().isoformat())
        e_rows, _e_source = own_day_read.read_today_activities(state_root, day_e)
        e_read = next((row for row in e_rows if str(row.get("ref")) == act_e), None)
        _check(e_read is not None and e_read.get("status") == "BLOCKED",
               "the read face did not carry the BLOCKED Activity", e_rows)
        _check(e_read.get("blocked_on_ref") == e_blocked.get("blocked_on_ref")
               and bool(e_read.get("blocked_reason_code")),
               "the read face lost the owner's BLOCK basis", e_read)
        _check((e_read.get("recovery") or {}).get("recoverable") is False
               and (e_read.get("recovery") or {}).get("reason_code")
               == own_day_read.RECOVERY_NO_OPEN_ELIGIBILITY,
               "the read face invented a recovery entry for an unconditional BLOCK", e_read)

        # The next Decision sees only E's own exit: no CONTINUE, no START, no RESUME.
        cognition.policy = "no_action"
        cognition.target_activity = act_e
        tenth = cycle.evaluate_once(trigger_ref="evt:outcome:e2", observed_at=_tick(), act=True)
        offered_e = _offered_for(cognition.last_offered.get("candidates") or [],
                                 activity_id=act_e, artifact_id=artifact_e_id)
        _check(bool(offered_e), "the BLOCKED Activity was not offered at all", {
            "round": tenth, "offered": cognition.last_offered.get("candidates")})
        e_legal = {code for row in offered_e for code in (row.get("legal_decisions") or [])}
        _check("ABANDON" in e_legal,
               "the unconditional BLOCKED has no legal exit in the offer", {
                   "offered_e": offered_e,
                   "withheld": runtime.blocked_activity_source.last_withheld,
                   "all_candidates": cognition.last_offered.get("candidates")})
        _check(not ({"CONTINUE", "START", "RESUME", "PAUSE"} & e_legal),
               "the BLOCKED Activity was offered a continuation/start/resume", offered_e)
        e_no_restart_offer = not any(
            _candidate_matches_artifact(row, artifact_e_id)
            and "START" in (row.get("legal_decisions") or [])
            for row in (cognition.last_offered.get("candidates") or []))
        _check(e_no_restart_offer,
               "the blocked object was offered as a fresh START", cognition.last_offered)
        _check(_activity(runtime, act_e).get("status") == "BLOCKED",
               "a NO_ACTION round changed E", _activity(runtime, act_e))

        # The NO_ACTION settled that exact situation; the same explicit re-evaluation the
        # runner uses on a real event reopens it (consumes nothing, resumes nothing).
        e_reconsideration_key = (cycle.last_reconsideration or {}).get("fingerprint")
        cycle.release_reconsideration(
            refs=[act_e, artifact_e_id, _artifact_ref(artifact_e_id),
                  str(e_blocked.get("blocked_on_ref") or "")],
            reason="EXPLICIT_REEVALUATION")
        if e_reconsideration_key:
            cycle.reconsideration.release(key=e_reconsideration_key)

        # A fresh Decision chooses ABANDON; AG-2/LR-2 really end the same Activity.
        cognition.policy = "abandon"
        cognition.target_activity = act_e
        eleventh = cycle.evaluate_once(trigger_ref="evt:outcome:e3", observed_at=_tick(), act=True)
        e_after = _activity(runtime, act_e)
        _check(eleventh.get("decision") == "ABANDON"
               and eleventh.get("adoption_status") == "APPLIED",
               "the BLOCKED exit was not adopted", {
                   "round": {k: eleventh.get(k) for k in (
                       "decision", "outcome", "adoption_status", "detail", "error")},
                   "offered": cognition.last_offered})
        _check(e_after.get("status") == "ABANDONED", "E did not become ABANDONED", e_after)
        e_handoffs = _handoffs(runtime, act_e, kind=ag2.HANDOFF_KIND_ABANDONED)
        _check(len(e_handoffs) == 1,
               "the ABANDONED exit did not emit exactly one handoff", e_handoffs)
        summary["blocked_unconditional_exit"] = {
            "activity_ref": act_e,
            "blocked_reason_code": e_blocked.get("blocked_reason_code"),
            "blocked_on_ref": e_blocked.get("blocked_on_ref"),
            "no_condition_or_eligibility_invented": True,
            "unfinished_visible": True,
            "continue_allowed": e_item.get("continue_allowed"),
            "next_step_kind": (continuity.next_step or {}).get("kind"),
            "offered_legal_decisions": sorted(e_legal),
            "final_status": e_after.get("status"),
            "abandoned_handoff_count": len(e_handoffs),
        }

        # ------------------------------------------------ C: real UNKNOWN Action, no rewrite
        artifact_c = port.create_artifact(
            workspace_id=str(data["workspace_id"]), title="第五件作品",
            content="第五件作品的正文。\n", grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "create-c", "c"))
        _check(artifact_c.applied, "the owner did not create artifact C", artifact_c.to_dict())
        artifact_c_id = str(artifact_c.artifact_id or "")
        cognition.policy = "start"
        cognition.target_artifact = artifact_c_id
        cognition.target_activity = ""
        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        twelfth = cycle.evaluate_once(trigger_ref="evt:outcome:c1", observed_at=_tick(), act=True)
        act_c = _new_activity_id(runtime, open_before) or str(twelfth.get("activity_ref") or "")
        _check(bool(act_c), "no Activity was started for C", {
            "round": twelfth, "offered": cognition.last_offered})
        _check(_activity(runtime, act_c).get("status") == "ACTIVE",
               "C is not ACTIVE", _activity(runtime, act_c))

        # The owner really writes the next version; the caller never sees the answer -> UNKNOWN.
        c_actions_before = {str(row.get("action_id"))
                            for row in runtime.action_read.list_actions()}
        c_head_before_unknown = _head(port, artifact_c_id)
        runtime.artifact_action_adapter.fault_injection_drop_response_after_write = True
        cognition.policy = "continue"
        thirteenth = cycle.evaluate_once(trigger_ref="evt:outcome:c2", observed_at=_tick(), act=True)
        c_after_unknown = _activity(runtime, act_c)
        _check(c_after_unknown.get("status") == "ACTIVE",
               "an UNKNOWN Action changed the Activity by itself", c_after_unknown)
        _check(_head(port, artifact_c_id) != c_head_before_unknown,
               "the owner did not really write although the response was lost", {
                   "before": c_head_before_unknown, "after": _head(port, artifact_c_id),
                   "round": {k: thirteenth.get(k) for k in (
                       "outcome", "decision", "detail", "error")}})

        def _unknown_action_for(activity_id: str) -> Optional[dict]:
            bare = activity_id.replace("actv:", "").replace("actv_", "")
            for row in runtime.action_read.list_actions(status="UNKNOWN"):
                ref = str(row.get("activity_ref") or "").replace("actv:", "").replace("actv_", "")
                if ref == bare:
                    return dict(row)
            return None

        unknown_action = _unknown_action_for(act_c)
        if unknown_action is None:
            unknown_action = next(
                (dict(row) for row in runtime.action_read.list_actions(status="UNKNOWN")
                 if str(row.get("action_id")) not in c_actions_before), None)
        _check(unknown_action is not None, "no real UNKNOWN Action was recorded", {
            "round": {k: twelfth.get(k) for k in ("outcome", "detail", "error")},
            "actions": [row.get("status") for row in runtime.action_read.list_actions()]})
        unknown_ref = str(unknown_action["action_id"])

        # An UNKNOWN/timeout is not a terminal refusal and cannot sign a FAILED.
        failed_negative: dict = {}
        c_scope = [ref for ref in (_activity(runtime, act_c).get("subject_refs") or []) if ref]

        # Legal-prefix semantic negative: record a *real* AR-0 receipt that claims UNKNOWN for
        # this very Action, then hand it to the criterion.  A receipt/settlement ref is an
        # allowed evidence prefix, so this is refused on the facts (UNKNOWN != terminal
        # refusal), not by an illegal `actn:` prefix.
        attempts = runtime.action_read.list_attempts(unknown_ref)
        _check(bool(attempts), "the UNKNOWN Action has no attempt to attach a receipt to",
               runtime.action_read.get_action(unknown_ref))
        unknown_receipt_ref = "arcpt:00000000000000a1"
        ingested = runtime.action_command.ingest_receipt({
            "receipt_id": unknown_receipt_ref,
            "action_id": unknown_ref,
            "attempt_id": str(attempts[-1]["attempt_id"]),
            "receipt_kind": "executor_reported",
            "provider": "managed_artifact",
            "status_claim": "UNKNOWN",
            "evidence_kind": "executor-reported",
            "authority_domain": "lifesupply",
            "occurred_at": _tick(), "observed_at": _tick(),
            "recorded_at": _tick(), "enqueued_at": _tick(),
        })
        _check(str((ingested.get("action") or {}).get("status")) == "UNKNOWN",
               "recording an UNKNOWN receipt changed the Action", ingested)
        try:
            runtime.activity_outcome.assess_failure(
                act_c, goal_scope_refs=c_scope[:1],
                criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL],
                evidence_refs=[unknown_receipt_ref], assessed_at=_tick())
            _check(False, "an UNKNOWN receipt was accepted as a terminal failure criterion",
                   {"receipt": unknown_receipt_ref})
        except ao.FailureAssessmentError as exc:
            failed_negative["unknown_receipt_not_a_criterion"] = str(exc)[:200]
            _check("EXTERNAL_TERMINAL_REFUSAL" in str(exc),
                   "the UNKNOWN receipt was refused by grammar, not by the semantic gate",
                   str(exc))
        _check(str((runtime.action_read.get_action(unknown_ref) or {}).get("status")) == "UNKNOWN",
               "the refused UNKNOWN receipt rewrote the Action",
               runtime.action_read.get_action(unknown_ref))
        _check(_activity(runtime, act_c).get("status") == "ACTIVE",
               "the refused UNKNOWN receipt changed the Activity", _activity(runtime, act_c))
        try:
            runtime.activity_outcome.assess_failure(
                act_c, goal_scope_refs=c_scope[:1],
                criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_EXTERNAL_TERMINAL_REFUSAL],
                evidence_refs=[unknown_ref], assessed_at=_tick())
            _check(False, "an UNKNOWN Action was accepted as a terminal failure criterion")
        except ao.FailureAssessmentError as exc:
            failed_negative["unknown_action_not_a_criterion"] = str(exc)[:160]
        _check(str((runtime.action_read.get_action(unknown_ref) or {}).get("status")) == "UNKNOWN",
               "the refused assessment rewrote the UNKNOWN Action",
               runtime.action_read.get_action(unknown_ref))
        _check(_activity(runtime, act_c).get("status") == "ACTIVE",
               "the refused assessment changed the Activity", _activity(runtime, act_c))

        # The owner archives C, then the S09 assessment names the real criterion and keeps
        # the still-unresolved Action as an obligation (not as evidence).
        c_view = port.read_artifact(artifact_c_id) or {}
        c_row = c_view.get("artifact") or {}
        c_archived = port.archive_artifact(
            artifact_id=artifact_c_id, expected_revision=int(c_row.get("revision") or 0),
            grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "archive-c", artifact_c_id))
        _check(c_archived.applied, "the owner did not archive C", c_archived.to_dict())
        assessment_c = runtime.activity_outcome.assess_failure(
            act_c, goal_scope_refs=c_scope[:1],
            criterion_refs=["criterion:%s" % ao.FAILURE_CRITERION_ARTIFACT_ARCHIVED],
            evidence_refs=[_artifact_ref(artifact_c_id)],
            unresolved_action_refs=[unknown_ref],
            assessed_at=_tick())
        fail_c = runtime.activity_outcome.fail_activity(
            act_c, assessment_c,
            idempotency_key="lr2_fail:activity-outcome-demo:%s" % act_c,
            occurred_at=_tick(), observed_at=_tick())
        _check(bool(fail_c.committed), "C's FAILED transition did not commit", fail_c)
        c_failed = _activity(runtime, act_c)
        c_revision_after_fail = int(c_failed.get("revision", -1))
        _check(c_failed.get("status") == "FAILED", "C is not FAILED", c_failed)
        _check(unknown_ref in (c_failed.get("unresolved_action_refs") or []),
               "the unresolved Action was dropped from the failure", c_failed)
        _check(c_failed.get("completion_evidence_ref") is None,
               "C's failure wrote completion evidence", c_failed)

        # The late original success settles in AR-0 only.  It must not revive C, move its
        # revision, restore completion evidence, or rewrite what the owner already produced.
        c_head_before = _head(port, artifact_c_id)
        reconciled = runtime.action_command.reconcile_action(
            action_id=unknown_ref, occurred_at=_tick())
        _check(str((reconciled.get("action") or {}).get("status")) == "SUCCEEDED",
               "the late original success did not settle", reconciled)
        c_after_reconcile = _activity(runtime, act_c)
        _check(c_after_reconcile.get("status") == "FAILED"
               and int(c_after_reconcile.get("revision", -1)) == c_revision_after_fail,
               "a late Action success revived or advanced the FAILED Activity",
               c_after_reconcile)
        _check(c_after_reconcile.get("failure_assessment_ref") == assessment_c.assessment_id
               and c_after_reconcile.get("completion_evidence_ref") is None,
               "a late Action success rewrote the failure facts", c_after_reconcile)
        _check(unknown_ref in (c_after_reconcile.get("unresolved_action_refs") or []),
               "a late Action success erased the recorded obligation", c_after_reconcile)
        _check(_head(port, artifact_c_id) == c_head_before,
               "a late Action success rewrote the already produced version",
               _head(port, artifact_c_id))
        _check(len(_handoffs(runtime, act_c, kind=ag2.HANDOFF_KIND_FAILED)) == 1,
               "C's FAILED handoff is not exactly one",
               _handoffs(runtime, act_c, kind=ag2.HANDOFF_KIND_FAILED))
        summary["unknown_action_negative"] = {
            "activity_ref": act_c,
            "unknown_action_ref": unknown_ref,
            "refused_criterion": failed_negative,
            "unknown_stayed_unknown_after_refusal": True,
            "failed_with_unresolved_obligation": True,
            "late_success_status": str((reconciled.get("action") or {}).get("status")),
            "activity_unchanged_after_late_success": True,
            "artifact_effect_preserved": _head(port, artifact_c_id) == c_head_before,
            "failed_handoff_count": len(_handoffs(runtime, act_c, kind=ag2.HANDOFF_KIND_FAILED)),
        }

        # ---------------------------------------------------------------- B: real BLOCKED
        artifact_b = port.create_artifact(
            workspace_id=str(data["workspace_id"]), title="第二件作品",
            content="第二件作品的正文。\n", grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "create-b", "b"))
        _check(artifact_b.applied, "the owner did not create artifact B", artifact_b.to_dict())
        artifact_b_id = str(artifact_b.artifact_id or "")
        artifact_b_view = port.read_artifact(artifact_b_id) or {}
        artifact_b_row = artifact_b_view.get("artifact") or {}
        artifact_b_id = str(artifact_b_row.get("artifact_id") or artifact_b_id)
        _check(bool(artifact_b_id), "artifact B has no id", artifact_b.to_dict())

        cognition.policy = "start"
        cognition.target_artifact = artifact_b_id
        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        fourth = cycle.evaluate_once(trigger_ref="evt:outcome:4", observed_at=_tick(), act=True)
        act_b = _new_activity_id(runtime, open_before) or str(fourth.get("activity_ref") or "")
        _check(bool(act_b), "no Activity was started for B", {
            "round": fourth, "offered": cognition.last_offered})
        _check(_activity(runtime, act_b).get("status") == "ACTIVE",
               "B is not ACTIVE", _activity(runtime, act_b))

        head_b = _head(port, artifact_b_id)
        basis = lwb.work_head_basis(artifact_id=artifact_b_id, baseline_head=head_b,
                                    activity_ref=act_b)
        _check(isinstance(basis, dict) and bool(basis.get("waiting_on_ref")),
               "a real artifact-head basis could not be built", basis)
        blocked = runtime.activity_outcome.block_activity(
            act_b,
            blocked_on_ref=basis["waiting_on_ref"],
            blocked_reason_code="BLOCKED_BY_CONDITION",
            idempotency_key="lr2_block:activity-outcome-demo:%s" % act_b,
            occurred_at=_tick(), observed_at=_tick())
        _check(bool(blocked.committed), "the BLOCK transition did not commit", blocked)
        b_blocked = _activity(runtime, act_b)
        _check(b_blocked.get("status") == "BLOCKED", "B is not BLOCKED", b_blocked)
        _check(runtime.activity_read.get_current_life_view().get("foreground_activity_ref") is None,
               "BLOCKED did not release the foreground")
        _check(bool(b_blocked.get("blocked_on_ref")),
               "the blocking basis was not stored", b_blocked)
        bound = runtime.waiting_coordinator.bind_blocked_condition(
            activity_id=act_b,
            expected_revision=int(b_blocked.get("revision", -1)),
            waiting_on_ref=basis["waiting_on_ref"],
            condition_kind=str(basis["condition_kind"]),
            parameters=lwb.condition_parameters(basis),
            source_refs=[str(basis["basis_ref"])],
            idempotency_key="lr4_block_cond:activity-outcome-demo:%s" % act_b,
            occurred_at=_tick(), observed_at=_tick())
        condition_id = str((bound.get("resume_condition") or {}).get("condition_id") or "")
        _check(bool(condition_id), "no block-bound condition was stored", bound)
        summary["blocked"] = {
            "activity_ref": act_b, "status": b_blocked.get("status"),
            "blocked_on_ref": b_blocked.get("blocked_on_ref"),
            "blocked_reason_code": b_blocked.get("blocked_reason_code"),
            "condition_id": condition_id,
            "baseline_head": head_b,
        }

        # ---------------------------------------------------------------- D: a second running Activity
        artifact_d = port.create_artifact(
            workspace_id=str(data["workspace_id"]), title="第三件作品",
            content="第三件作品的正文。\n", grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "create-d", "d"))
        _check(artifact_d.applied, "the owner did not create artifact D", artifact_d.to_dict())
        artifact_d_id = str(artifact_d.artifact_id or "")
        cognition.policy = "start"
        cognition.target_artifact = artifact_d_id
        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        fifth = cycle.evaluate_once(trigger_ref="evt:outcome:5", observed_at=_tick(), act=True)
        act_d = _new_activity_id(runtime, open_before) or str(fifth.get("activity_ref") or "")
        _check(bool(act_d), "no Activity was started for D", {
            "round": fifth, "offered": cognition.last_offered})
        _check(act_d != act_b, "D reused B's Activity id", {"b": act_b, "d": act_d})

        # With D really ACTIVE, B's suspension is still offered only its own exit: ABANDON is
        # legal for B, nothing execution-shaped is, and the running foreground is untouched.
        offered_b_while_d = _offered_for(
            cognition.last_offered.get("candidates") or [],
            activity_id=act_b, artifact_id=artifact_b_id)
        b_legal_while_d = {code for row in offered_b_while_d
                           for code in (row.get("legal_decisions") or [])}
        _check("ABANDON" in b_legal_while_d,
               "a BLOCKED Activity lost its ABANDON exit while another Activity runs",
               offered_b_while_d)
        _check(not ({"CONTINUE", "START", "RESUME", "PAUSE"} & b_legal_while_d),
               "a BLOCKED Activity was offered an execution choice while another runs",
               offered_b_while_d)
        _check(_activity(runtime, act_d).get("status") == "ACTIVE"
               and _activity(runtime, act_b).get("status") == "BLOCKED",
               "reading B's exit changed a running or suspended Activity", {
                   "b": _activity(runtime, act_b), "d": _activity(runtime, act_d)})
        summary["blocked_exit_while_other_active"] = {
            "activity_ref": act_b,
            "offered_legal_decisions": sorted(b_legal_while_d),
            "other_activity_still_active": _activity(runtime, act_d).get("status"),
        }

        # The owner really commits a new head for B: LR-4 alone may emit OPEN.  D must not be
        # preempted, and B must stay BLOCKED until a fresh Decision says RESUME.
        head_b2 = port.commit_version(
            artifact_id=artifact_b_id, expected_head=head_b,
            content="%s\n\n第二版（owner 真实提交，使 B 的阻塞条件被满足）\n"
                    % ((port.resume_point(artifact_b_id).content or "")),
            grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("activity-outcome-demo", "b-v2", artifact_b_id))
        _check(head_b2.applied, "the owner did not commit B version 2", head_b2.to_dict())
        maintained = cycle.maintain_waiting(observed_at=_tick())
        _check(condition_id in (maintained.get("satisfied") or []),
               "LR-4 upkeep did not satisfy the block-bound condition", maintained)
        open_b = [row for row in _eligibilities(runtime).values()
                  if row.get("activity_id") == act_b
                  and row.get("status") == awa.ELIG_STATUS_OPEN]
        _check(len(open_b) == 1, "no single OPEN eligibility was emitted for B", _eligibilities(runtime))
        eligibility_id = str(open_b[0].get("eligibility_id") or "")
        _check(_activity(runtime, act_b).get("status") == "BLOCKED",
               "maintenance changed B's status by itself", _activity(runtime, act_b))
        _check(int(maintained.get("llm_calls") or 0) == 0 and not maintained.get("auto_resumed"),
               "maintenance spent a request or resumed by itself", maintained)

        # D leaves on its own Decision.  Before it does, the same round proves B is *not*
        # offered while D holds the foreground -- the OPEN eligibility is not a preemption.
        cognition.policy = "pause"
        sixth = cycle.evaluate_once(trigger_ref="evt:outcome:6", observed_at=_tick(), act=True)
        offered_b = _offered_for(
            cognition.last_offered.get("candidates") or [],
            activity_id=act_b, artifact_id=artifact_b_id, eligibility_id=eligibility_id)
        resume_offered_b = [row for row in offered_b
                            if "RESUME" in (row.get("legal_decisions") or [])]
        _check(resume_offered_b == [],
               "B was offered for RESUME while D holds the foreground (preemption)",
               resume_offered_b)
        _check(_activity(runtime, act_b).get("status") == "BLOCKED",
               "B changed while the foreground was held", _activity(runtime, act_b))
        _check((_eligibilities(runtime).get(eligibility_id) or {}).get("status")
               == awa.ELIG_STATUS_OPEN,
               "the OPEN eligibility was consumed without a RESUME",
               _eligibilities(runtime).get(eligibility_id))
        _check(_activity(runtime, act_d).get("status") == "PAUSED",
               "D did not pause on its own Decision", {
                   "round": {k: sixth.get(k) for k in (
                       "decision", "outcome", "adoption_status", "detail", "error")},
                   "d": _activity(runtime, act_d)})

        # A fresh NO_ACTION keeps B BLOCKED and the eligibility OPEN, even though RESUME is
        # now legally offered.
        cognition.policy = "no_action"
        seventh = cycle.evaluate_once(trigger_ref="evt:outcome:7", observed_at=_tick(), act=True)
        offered_b2 = _offered_for(
            cognition.last_offered.get("candidates") or [],
            activity_id=act_b, artifact_id=artifact_b_id, eligibility_id=eligibility_id)
        _check(any("RESUME" in (row.get("legal_decisions") or []) for row in offered_b2),
               "the now-legal RESUME of B was not offered at all",
               {"offered_b2": offered_b2,
                "eligibilities": _eligibilities(runtime),
                "b_status": _activity(runtime, act_b).get("status"),
                "all_candidates": cognition.last_offered.get("candidates")})
        _check(_activity(runtime, act_b).get("status") == "BLOCKED",
               "NO_ACTION changed B", _activity(runtime, act_b))
        _check((_eligibilities(runtime).get(eligibility_id) or {}).get("status")
               == awa.ELIG_STATUS_OPEN,
               "NO_ACTION consumed B's eligibility",
               _eligibilities(runtime).get(eligibility_id))

        # The recorded NO_ACTION settled that exact situation.  An explicit re-evaluation reason
        # naming this eligibility is the existing way to reopen it (the runner does the same on
        # a real event); it consumes nothing and resumes nothing by itself.
        blocked_key = (cycle.last_reconsideration or {}).get("fingerprint")
        cycle.release_reconsideration(
            refs=[eligibility_id, act_b, artifact_b_id, _artifact_ref(artifact_b_id)],
            reason="EXPLICIT_REEVALUATION")
        if blocked_key:
            cycle.reconsideration.release(key=blocked_key)

        # A fresh Decision returns the *same* B.
        cognition.policy = "resume"
        cognition.target_activity = act_b
        cognition.target_artifact = artifact_b_id
        eighth = cycle.evaluate_once(trigger_ref="evt:outcome:8", observed_at=_tick(), act=True)
        b_resumed = _activity(runtime, act_b)
        _check(eighth.get("decision") == "RESUME" and eighth.get("adoption_status") == "APPLIED",
               "the RESUME of B was not adopted", {
                   "round": {k: eighth.get(k) for k in (
                       "decision", "outcome", "adoption_status", "detail", "error")},
                   "offered": cognition.last_offered})
        _check(b_resumed.get("status") == "ACTIVE",
               "B did not return to ACTIVE", b_resumed)
        _check(str(b_resumed.get("activity_id")) == act_b,
               "a different Activity came back instead of B", b_resumed)
        _check((_eligibilities(runtime).get(eligibility_id) or {}).get("status")
               == awa.ELIG_STATUS_CONSUMED,
               "B's eligibility was not consumed by its RESUME",
               _eligibilities(runtime).get(eligibility_id))
        summary["blocked_recovery"] = {
            "activity_ref": act_b,
            "condition_id": condition_id,
            "eligibility_id": eligibility_id,
            "no_preemption_while_d_active": resume_offered_b == [],
            "no_action_kept_blocked": True,
            "resumed_same_activity": str(b_resumed.get("activity_id")) == act_b,
            "final_status": b_resumed.get("status"),
            "eligibility_status": (_eligibilities(runtime).get(eligibility_id) or {}).get("status"),
        }

        # ---------------------------------------------------------------- ordered same-root re-assembly
        runtime.close()
        runtime = None
        cognition2 = _ScriptedDecisionModel()
        writer2 = _Writer()
        runtime, cycle2 = _build_runtime(instance, state_root, services, data,
                                         cognition2, writer2, port, env,
                                         consumer=memory["consumer"])
        a_reloaded = _activity(runtime, act_a)
        b_reloaded = _activity(runtime, act_b)
        _check(a_reloaded.get("status") == "FAILED",
               "A is not FAILED after same-root re-assembly", a_reloaded)
        _check(a_reloaded.get("failure_assessment_ref") == assessment.assessment_id,
               "A lost its failure assessment ref after re-assembly", a_reloaded)
        _check(a_reloaded.get("terminal_reason_code") == "FAILED_BY_OWNER_ASSESSMENT",
               "A lost its terminal reason after re-assembly", a_reloaded)
        _check(str(b_reloaded.get("activity_id")) == act_b
               and b_reloaded.get("status") == "ACTIVE",
               "B did not survive same-root re-assembly", b_reloaded)
        _check(len(_handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)) == 1,
               "re-assembly duplicated A's FAILED handoff", _handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED))
        # The same fail operation after a same-root re-assembly replays the original event.
        replay2 = runtime.activity_outcome.fail_activity(
            act_a, assessment,
            idempotency_key="lr2_fail:activity-outcome-demo:%s" % act_a,
            occurred_at=_tick(), observed_at=_tick())
        replay2_handoff = (getattr(replay2, "failure_handoff", None)
                           or runtime.activity_outcome.last_failure_handoff or {})
        _check(bool(replay2_handoff.get("idempotent_replay")),
               "the same-root FAILED replay minted a new handoff", replay2_handoff)
        summary["reassembly"] = {
            "a_status": a_reloaded.get("status"),
            "a_terminal_reason_code": a_reloaded.get("terminal_reason_code"),
            "a_failure_assessment_ref": a_reloaded.get("failure_assessment_ref"),
            "b_status": b_reloaded.get("status"),
            "a_handoff_count": len(_handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)),
            "handoff_replay_idempotent": bool(replay2_handoff.get("idempotent_replay")),
        }

        # What the *next* round may read: the real M0 view (one row per outcome) and the
        # owner's own criterion/evidence refs through the read face -- not a generic status.
        recent = memory["port"].recent(limit=20)
        a_rows = [row for row in recent
                  if str(row.activity_ref or "") == act_a
                  and str(row.outcome_kind or "") == "FAILED"]
        _check(len(a_rows) == 1,
               "the FAILED handoff is not readable exactly once from the real M0 view",
               [row.to_dict() for row in recent])
        _check(_bare_artifact(str(a_rows[0].artifact_ref or "")) == _bare_artifact(seed),
               "the admitted FAILED row lost its real artifact identity", a_rows[0].to_dict())
        day_a = (datetime.fromisoformat(str(a_failed.get("started_at") or _tick()))
                 .astimezone(timezone(timedelta(hours=8))).date().isoformat())
        a_rows_read, _a_source = own_day_read.read_today_activities(state_root, day_a)
        a_read = next((row for row in a_rows_read if str(row.get("ref")) == act_a), None)
        _check(a_read is not None and a_read.get("status") == "FAILED",
               "the read face did not carry the FAILED Activity", a_rows_read)
        _check(a_read.get("failure_assessment_ref") == assessment.assessment_id,
               "the read face lost the failure assessment ref", a_read)
        _check(bool(a_read.get("failure_criteria_refs"))
               and bool(a_read.get("failure_evidence_refs")),
               "the read face lost the judged criterion/evidence refs", a_read)
        summary["next_round_read"] = {
            "memory_rows_for_failed": len(a_rows),
            "memory_outcome_kind": str(a_rows[0].outcome_kind or ""),
            "memory_artifact_ref": str(a_rows[0].artifact_ref or ""),
            "read_face_failure_assessment_ref": a_read.get("failure_assessment_ref"),
            "read_face_failure_criteria_refs": a_read.get("failure_criteria_refs"),
            "read_face_failure_evidence_refs": a_read.get("failure_evidence_refs"),
        }

        summary["checks"] = {
            "a_real_step_committed": bool(head_v1),
            "refused_step_did_not_fail_activity": True,
            "failed_terminal": a_reloaded.get("status") == "FAILED",
            "failed_released_foreground": True,
            "failure_kept_checkpoint": True,
            "failure_wrote_no_completion_evidence": a_failed.get("completion_evidence_ref") is None,
            "failed_handoff_single_and_idempotent": len(_handoffs(runtime, act_a, kind=ag2.HANDOFF_KIND_FAILED)) == 1,
            "no_automatic_fail_from_step_refusal": negative.get("no_automatic_fail_transition") is True,
            "stale_revision_refused": bool(negative.get("stale_revision_refused")),
            "unreadable_evidence_refused": bool(negative.get("unreadable_evidence_refused")),
            "forged_mapping_refused": bool(negative.get("forged_mapping_refused")),
            "unrelated_archived_artifact_refused":
                bool(negative.get("unrelated_archived_artifact_assess_refused"))
                and bool(negative.get("unrelated_archived_artifact_fail_refused")),
            "unrelated_artifact_did_not_move_activity":
                summary["cross_item_negative"]["a_status_unchanged"] == "ACTIVE"
                and summary["cross_item_negative"]["a_revision_unchanged"]
                and summary["cross_item_negative"]["handoff_count"] == 0,
            "failed_not_offered_continue_or_resume": True,
            "blocked_is_nonterminal": b_blocked.get("status") == "BLOCKED",
            "blocked_released_foreground": True,
            "condition_satisfied_emits_open": len(open_b) == 1,
            "maintenance_zero_model": int(maintained.get("llm_calls") or 0) == 0,
            "no_preemption": resume_offered_b == [],
            "no_action_keeps_blocked_and_open": True,
            "resume_returns_same_activity": str(b_resumed.get("activity_id")) == act_b,
            "eligibility_consumed": (_eligibilities(runtime).get(eligibility_id) or {}).get(
                "status") == awa.ELIG_STATUS_CONSUMED,
            "same_root_reassembly_preserves_state": True,
            "blocked_unconditional_visible_in_unfinished":
                summary["blocked_unconditional_exit"]["unfinished_visible"],
            "blocked_unconditional_not_continuable":
                summary["blocked_unconditional_exit"]["continue_allowed"] is False,
            "blocked_unconditional_has_abandon_exit":
                "ABANDON" in summary["blocked_unconditional_exit"]["offered_legal_decisions"],
            "blocked_unconditional_ends_abandoned":
                summary["blocked_unconditional_exit"]["final_status"] == "ABANDONED",
            "blocked_object_not_restarted": e_no_restart_offer,
            "blocked_exit_offered_while_other_active":
                "ABANDON" in summary["blocked_exit_while_other_active"][
                    "offered_legal_decisions"]
                and "ACTIVE" == summary["blocked_exit_while_other_active"][
                    "other_activity_still_active"],
            "failed_handoff_finished_by_fail_entry":
                bool(op_handoff.get("handoff_id")),
            "failed_handoff_consumed_by_real_m0":
                op_handoff.get("consumer_disposition") in {"ACCEPT", "DUPLICATE"},
            "failed_handoff_replay_on_repeat":
                summary["failed"]["handoff_replay_on_repeat"],
            "unknown_action_not_a_failure_criterion":
                bool(failed_negative.get("unknown_action_not_a_criterion")),
            "unknown_receipt_not_a_failure_criterion":
                bool(failed_negative.get("unknown_receipt_not_a_criterion")),
            "unknown_action_stayed_unknown": True,
            "late_success_did_not_revive_failed":
                summary["unknown_action_negative"]["activity_unchanged_after_late_success"],
            "late_success_kept_artifact_effect":
                summary["unknown_action_negative"]["artifact_effect_preserved"],
            "next_round_read_has_failure_refs":
                bool(summary["next_round_read"]["read_face_failure_criteria_refs"])
                and bool(summary["next_round_read"]["read_face_failure_evidence_refs"]),
            "m0_view_has_one_failed_row":
                summary["next_round_read"]["memory_rows_for_failed"] == 1,
            "read_face_blocked_basis_visible": True,
        }
        passed = all(bool(value) for value in summary["checks"].values())
        summary["ok"] = passed
        summary["model_calls"] = cognition.calls + cognition2.calls
        summary["writer_calls"] = writer.calls + writer2.calls
        print("DEMO_OK" if passed else "DEMO_FAIL")
        print(json.dumps(summary, ensure_ascii=False, default=str, indent=1))
        return 0 if passed else 1
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        try:
            services.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
