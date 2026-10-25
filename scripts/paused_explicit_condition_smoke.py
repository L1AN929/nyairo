#!/usr/bin/env python3
"""Zero-model real-owner smoke: explicit-condition PAUSE -> owner proof -> new RESUME.

What it really does (local only, 0 product model, 0 network)
-----------------------------------------------------------
1. Starts a **real** Life Supply owner on its own Unix socket and prepares a real instance
   through ``one_day_life.prepare`` (real workspace/grant/seed artifact).
2. Assembles the **real** production Life runtime and ``assemble_autonomous_life``; Decision
   and content are scripted doubles, everything else (AG-0/AG-1/AG-2/LR-2/LR-4/AR-0/Supply)
   is real.
3. Round 1: a real START of the seed work; the content writer answers its legal ``no_change``,
   so the next round's readiness is ``WORK_NO_CHANGE_CONFIRMED`` on the owner's own head --
   that is the basis this round offers.
4. Round 2: the scripted Decision chooses **PAUSE** and copies the offered
   ``pause_condition.condition_ref`` into ``reevaluation_hint_ref``.  AG-1 validates it against
   the snapshot, AG-2 adopts a real LR-2 PAUSE and LR-4 binds the condition at the paused
   revision.  No model is needed for the condition itself.
5. The real Supply owner commits a new version.  ``maintain_waiting`` re-reads the owner head
   and hands one event to LR-4, which alone marks SATISFIED and emits one OPEN eligibility.
6. Round 3: a fresh Decision chooses **NO_ACTION** -- the Activity must stay PAUSED and the
   eligibility OPEN.
7. The owner commits another version (a new situation part), then round 4: a fresh Decision
   chooses **RESUME** for the real AG-0 candidate.  The same Activity becomes ACTIVE and LR-4
   marks that eligibility CONSUMED.

Boundaries: no real model, no product instance, no wall-clock waiting.  The condition is an
artifact-head dependency the owner really reports; the seed text is the operator's fixture.
This is not G1, not a 24h run and not evidence that a model naturally makes this choice.

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/paused_explicit_condition_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_waiting_agenda as awa  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import one_day_life as odl  # noqa: E402

from tests.test_one_day_life import Services  # noqa: E402

SCRATCH = Path(os.environ.get("NYAIRO_PAUSED_CONDITION_SMOKE_ROOT")
               or "/tmp/nyairo-paused-condition-smoke")
RUN = SCRATCH / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
CLOSEOUT_REF_PREFIX = "closeout:"


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("SMOKE_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:6000])
    raise SystemExit(1)


def _candidate_refs(row) -> list[str]:
    return [str(ref) for ref in (row.get("target_refs") or [])]


def _is_closeout_candidate(row) -> bool:
    return any(ref.startswith(CLOSEOUT_REF_PREFIX) for ref in _candidate_refs(row))


def _work_ref_for_seed(value) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("artifact:artifact:"):
        return text
    return "artifact:" + text


def _work_start_matches(candidates, seed_artifact) -> list[dict]:
    seed_ref = _work_ref_for_seed(seed_artifact)
    return [row for row in candidates
            if not _is_closeout_candidate(row)
            and seed_ref in _candidate_refs(row)
            and "START" in (row.get("legal_decisions") or [])]


class _ScriptedDecisionModel:
    """Decision double: only chooses inside the real AG-1 legal set, returns raw JSON."""

    model_id = "stub:paused-condition-smoke"
    model_version = "paused-condition-smoke-v1"

    def __init__(self, *, seed_artifact: str) -> None:
        self.seed_artifact = str(seed_artifact)
        self.policy = "start_seed"
        self.calls = 0
        self.last_offered: dict = {}
        self.last_selection: dict = {}

    def _raw(self, candidates, allowed):
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        if self.policy == "start_seed":
            matches = _work_start_matches(candidates, self.seed_artifact)
            self.last_selection = {"policy": self.policy,
                                   "work_start_matches": [row.get("candidate_id") for row in matches]}
            if len(matches) == 1:
                return {"decision": "START", "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: start the seed work",
                        "deferred_candidate_ids": []}
        if self.policy == "pause_with_condition":
            pause = self.last_offered.get("pause_condition") or {}
            pause_candidates = [row for row in candidates
                                if "PAUSE" in (row.get("legal_decisions") or [])]
            self.last_selection = {
                "policy": self.policy,
                "pause_condition": pause,
                "pause_candidates": [row.get("candidate_id") for row in pause_candidates],
            }
            if pause.get("available") and pause.get("condition_ref") and pause_candidates:
                return {"decision": "PAUSE",
                        "selected_candidate_id": pause_candidates[0]["candidate_id"],
                        "reevaluation_hint_ref": str(pause["condition_ref"]),
                        "reason_codes": reason,
                        "short_rationale": "stub: pause until the offered condition holds",
                        "deferred_candidate_ids": []}
        if self.policy == "pause_unconditional":
            # The round *does* offer a condition; this double deliberately does not name it.
            # That is the legal unconditional PAUSE and it must never grow a binding.
            pause = self.last_offered.get("pause_condition") or {}
            pause_candidates = [row for row in candidates
                                if "PAUSE" in (row.get("legal_decisions") or [])]
            self.last_selection = {
                "policy": self.policy,
                "pause_condition_offered": bool(pause.get("available")),
                "hint": None,
                "pause_candidates": [row.get("candidate_id") for row in pause_candidates],
            }
            if pause.get("available") and pause_candidates:
                return {"decision": "PAUSE",
                        "selected_candidate_id": pause_candidates[0]["candidate_id"],
                        "reason_codes": reason,
                        "short_rationale": "stub: pause without naming a condition",
                        "deferred_candidate_ids": []}
        if self.policy == "resume":
            resume_candidates = [row for row in candidates
                                 if "RESUME" in (row.get("legal_decisions") or [])]
            self.last_selection = {"policy": self.policy,
                                   "resume_candidates": [row.get("candidate_id")
                                                         for row in resume_candidates]}
            if len(resume_candidates) == 1:
                return {"decision": "RESUME",
                        "selected_candidate_id": resume_candidates[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: resume the same activity",
                        "deferred_candidate_ids": []}
        self.last_selection = {"policy": self.policy}
        return {"decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": ["NO_ACTION_CHOSEN"],
                "short_rationale": "stub: no action", "deferred_candidate_ids": []}

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        situation = dict(getattr(request, "situation_context", None) or {})
        self.last_offered = {
            "policy": self.policy,
            "pause_condition": situation.get("pause_condition"),
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "candidate_kind": row.get("candidate_kind"),
                            "source_kind": row.get("source_kind"),
                            "resume_eligibility_ref": row.get("resume_eligibility_ref"),
                            "target_refs": row.get("target_refs"),
                            "legal_decisions": row.get("legal_decisions")} for row in candidates],
        }
        raw = self._raw(candidates, allowed)
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:paused-condition-smoke:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class _ToggleWriter:
    """Content double: its legal ``no_change`` first, then ordinary text."""

    def __init__(self) -> None:
        self.mode = "no_change"
        self.calls = 0

    def next_content(self, point, *, observed_at: str) -> str:
        self.calls += 1
        if self.mode == "no_change":
            raise mcw.ModelNoChange("nothing to add on this head")
        return "%s\n\n（追加）%s\n" % (point.content or "", observed_at)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _conditions(runtime) -> dict:
    state = runtime.waiting_coordinator.condition_store.load_state() or {}
    return dict(state.get("conditions") or {})


def _eligibilities(runtime) -> dict:
    state = runtime.waiting_coordinator.eligibility_store.load_state() or {}
    return dict(state.get("eligibilities") or {})


def _activity(runtime, activity_id: str) -> dict:
    return dict(runtime.activity_read.get_activity(activity_id) or {})


def _head(port, artifact_id: str):
    row = port.read_head(artifact_id) or {}
    return str(row.get("version_id") or "")


def _run_unconditional_pause_negative(services: Services, run_root: Path) -> dict:
    """The formal chain for a PAUSE that names no condition.

    The round *offers* the same condition it offers for WAIT, and the scripted Decision
    deliberately declines to name it.  That is a legal unconditional PAUSE: AG-2 must adopt
    the real LR-2 pause, bind nothing in LR-4, and later owner head changes must not conjure
    a condition, an eligibility or a ``RESUME_ELIGIBLE`` candidate.
    """

    runtime = None
    root = run_root / "unconditional"
    root.mkdir(parents=True, exist_ok=True)
    try:
        instance = root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:paused-unconditional",
            title="Unconditional pause negative smoke",
            seed_content="无条件暂停负控的种子正文。\n")
        _check(bool(prepared.get("ok")), "unconditional instance preparation failed", prepared)
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
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        cognition = _ScriptedDecisionModel(seed_artifact=seed)
        writer = _ToggleWriter()
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root, writer=writer, enabled=True)
        _check(assembled.get("state") == assembly.READY,
               "unconditional assembly not READY", assembled)
        cycle = assembled["cycle"]
        t0 = datetime.now(timezone.utc)

        first = cycle.evaluate_once(trigger_ref="evt:pause-unconditional:1",
                                    observed_at=_iso(t0), act=True)
        activity_id = str(first.get("activity_ref") or "")
        if not activity_id:
            open_activities = [row for row in runtime.activity_read.list_open_activities()
                               if str(row.get("status") or "") == "ACTIVE"]
            if len(open_activities) == 1:
                activity_id = str(open_activities[0].get("activity_id") or "")
        _check(bool(activity_id), "unconditional round 1 created no Activity", first)
        head_before = _head(port, seed)

        cognition.policy = "pause_unconditional"
        second = cycle.evaluate_once(trigger_ref="evt:pause-unconditional:2",
                                     observed_at=_iso(t0), act=True)
        _check(cognition.last_selection.get("pause_condition_offered") is True,
               "the round did not offer a condition to decline", cognition.last_offered)
        _check(second.get("decision") == "PAUSE",
               "the unconditional PAUSE was not committed", second)
        _check(second.get("adoption_status") == "APPLIED",
               "the unconditional PAUSE was not adopted", second)
        paused = _activity(runtime, activity_id)
        _check(paused.get("status") == "PAUSED", "the Activity is not PAUSED", paused)
        _check(_conditions(runtime) == {}, "an unconditional pause bound a condition",
               _conditions(runtime))
        _check(_eligibilities(runtime) == {}, "an unconditional pause emitted an eligibility",
               _eligibilities(runtime))

        # A real owner head change must still have nothing to satisfy.
        head_v2 = port.commit_version(
            artifact_id=seed, expected_head=head_before,
            content="%s\n\n第二版（owner 真实提交，无条件暂停仍不该有资格）\n"
                    % (port.resume_point(seed).content or ""),
            grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("paused-unconditional-smoke", "v2", seed))
        _check(head_v2.applied, "the owner did not commit version 2", head_v2.to_dict())
        maintained = cycle.maintain_waiting(observed_at=_iso(t0))
        _check(list(maintained.get("satisfied") or []) == [],
               "a condition was satisfied without a binding", maintained)
        _check(_conditions(runtime) == {} and _eligibilities(runtime) == {},
               "maintenance invented a condition/eligibility",
               {"conditions": _conditions(runtime),
                "eligibilities": _eligibilities(runtime)})
        _check(int(maintained.get("llm_calls") or 0) == 0,
               "maintenance spent a request", maintained)

        cognition.policy = "no_action"
        third = cycle.evaluate_once(trigger_ref="evt:pause-unconditional:3",
                                    observed_at=_iso(t0), act=True)
        offered = list(cognition.last_offered.get("candidates") or [])
        resume_offered = [row for row in offered
                          if "RESUME" in (row.get("legal_decisions") or [])
                          or row.get("source_kind") == "RESUME_ELIGIBLE"]
        _check(resume_offered == [],
               "an unconditional pause showed a RESUME_ELIGIBLE candidate", resume_offered)
        still_paused = _activity(runtime, activity_id)
        _check(still_paused.get("status") == "PAUSED",
               "the unconditional pause did not stay PAUSED", still_paused)

        return {
            "instance": str(instance),
            "activity_ref": activity_id,
            "round1": {"outcome": first.get("outcome"), "head_before": head_before},
            "round2": {"decision": second.get("decision"),
                       "adoption_status": second.get("adoption_status"),
                       "activity_status": paused.get("status"),
                       "condition_count": len(_conditions(runtime)),
                       "eligibility_count": len(_eligibilities(runtime))},
            "maintenance": {"satisfied": maintained.get("satisfied"),
                            "emitted_eligibilities": maintained.get("emitted_eligibilities"),
                            "llm_calls": maintained.get("llm_calls")},
            "round3": {"decision": third.get("decision"),
                       "resume_candidates_offered": resume_offered,
                       "activity_status": still_paused.get("status")},
            "checks": {
                "condition_offered_but_not_named": True,
                "unconditional_pause_adopted": second.get("adoption_status") == "APPLIED",
                "no_condition_bound": _conditions(runtime) == {},
                "no_eligibility_emitted": _eligibilities(runtime) == {},
                "head_change_emits_nothing": list(maintained.get("satisfied") or []) == [],
                "no_resume_eligible_candidate": resume_offered == [],
                "stays_paused": still_paused.get("status") == "PAUSED",
            },
            "model_calls": cognition.calls,
            "writer_calls": writer.calls,
        }
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    services = Services(RUN / "services")
    runtime = None
    summary: dict = {"root": str(RUN), "checks": {}, "limitations": [
        "the Decision and content writer are scripted doubles; no product model was called",
        "the condition source is the real Supply owner's head; the seed text is an operator fixture",
        "no wall-clock wait: the owner commits versions on request, not after real hours",
        "this is not G1, not a 24h run and not evidence of natural model adoption",
    ]}
    try:
        instance = RUN / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:paused-condition",
            title="Explicit condition pause smoke",
            seed_content="种子正文第一句。\n")
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
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        cognition = _ScriptedDecisionModel(seed_artifact=seed)
        writer = _ToggleWriter()
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root, writer=writer, enabled=True)
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]

        t0 = datetime.now(timezone.utc)
        head_v1 = _head(port, seed)

        # 1. real START; the writer answers no_change, so the next readiness is a named dependency
        first = cycle.evaluate_once(trigger_ref="evt:pause-smoke:1", observed_at=_iso(t0), act=True)
        _check(first.get("outcome") == "NO_ACTION",
               "round 1 did not report the writer's no_change", first)
        _check((first.get("work_no_change") or {}).get("reason_code") == "NO_CHANGE",
               "round 1 did not record no_change", first)
        activity_id = str(first.get("activity_ref") or "")
        if not activity_id:
            # A START whose *initial content step* answers the legal ``no_change`` raises before
            # the coordinator returns, so the round report cannot carry the new Activity ref.
            # The owner still created it; read the real canonical Activity instead of guessing.
            open_activities = [row for row in runtime.activity_read.list_open_activities()
                               if str(row.get("status") or "") == "ACTIVE"]
            if len(open_activities) == 1:
                activity_id = str(open_activities[0].get("activity_id") or "")
        _check(bool(activity_id), "round 1 created no Activity", {
            key: first.get(key) for key in (
                "outcome", "decision", "decision_route", "adoption_status",
                "selected_candidate_ref", "step_path", "detail", "error",
                "work_no_change", "artifact_selection", "step_work_target")}
            | {"offered": cognition.last_offered, "selection": cognition.last_selection})
        summary["round1"] = {"outcome": first.get("outcome"), "activity_ref": activity_id,
                             "decision": first.get("decision"),
                             "head_before": head_v1}

        # 2. fresh Decision: PAUSE with the one offered condition
        cognition.policy = "pause_with_condition"
        second = cycle.evaluate_once(trigger_ref="evt:pause-smoke:2",
                                     observed_at=_iso(t0), act=True)
        offered_pause = dict(cognition.last_offered.get("pause_condition") or {})
        _check(bool(offered_pause.get("available")),
               "round 2 did not offer a pause condition", cognition.last_offered)
        _check(second.get("decision") == "PAUSE",
               "round 2 did not commit a PAUSE Decision",
               {key: second.get(key) for key in (
                   "outcome", "decision", "decision_route", "adoption_status",
                   "selected_candidate_ref", "detail", "error", "wait_basis",
                   "reconsideration", "pause_condition")}
               | {"offered": cognition.last_offered, "selection": cognition.last_selection})
        _check(second.get("adoption_status") == "APPLIED",
               "round 2 PAUSE was not adopted", second)
        paused = _activity(runtime, activity_id)
        _check(paused.get("status") == "PAUSED", "the Activity is not PAUSED", paused)
        conditions = _conditions(runtime)
        _check(len(conditions) == 1, "no single pause condition was bound", conditions)
        condition = next(iter(conditions.values()))
        _check(awa.is_pause_bound_condition(condition),
               "the bound condition is not marked pause-bound", condition)
        _check(int(condition["activity_revision_at_bind"]) == int(paused["revision"]),
               "the condition was not bound at the paused revision",
               {"condition": condition, "activity": paused})
        _check(str(condition["parameters"].get("baseline_head_ref") or "") == head_v1,
               "the condition baseline is not the owner's current head",
               {"condition": condition, "head": head_v1})
        summary["round2"] = {
            "decision": second.get("decision"),
            "outcome": second.get("outcome"),
            "adoption_status": second.get("adoption_status"),
            "activity_status": paused.get("status"),
            "activity_revision": paused.get("revision"),
            "offered_condition_ref": offered_pause.get("condition_ref"),
            "condition": {k: condition.get(k) for k in (
                "condition_id", "condition_kind", "status", "waiting_on_ref",
                "activity_revision_at_bind", "waiting_episode_id")},
        }

        # 3. the real owner commits a new version; LR-4's own upkeep satisfies the condition
        head_v2_outcome = port.commit_version(
            artifact_id=seed, expected_head=head_v1,
            content="%s\n\n第二版（owner 真实提交）\n" % (port.resume_point(seed).content or ""),
            grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("paused-condition-smoke", "v2", seed))
        _check(head_v2_outcome.applied, "the owner did not commit version 2",
               head_v2_outcome.to_dict())
        head_v2 = _head(port, seed)
        maintained = cycle.maintain_waiting(observed_at=_iso(t0))
        _check(list(maintained.get("satisfied") or []) == [condition["condition_id"]],
               "LR-4 upkeep did not satisfy exactly the bound condition", maintained)
        _check(int(maintained.get("llm_calls") or 0) == 0
               and not maintained.get("auto_resumed"),
               "upkeep spent a request or resumed by itself", maintained)
        eligibilities = _eligibilities(runtime)
        open_rows = [row for row in eligibilities.values()
                     if row.get("status") == awa.ELIG_STATUS_OPEN]
        _check(len(open_rows) == 1, "maintenance did not emit one OPEN eligibility",
               eligibilities)
        eligibility = open_rows[0]
        still_paused = _activity(runtime, activity_id)
        _check(still_paused.get("status") == "PAUSED",
               "maintenance changed the Activity status", still_paused)
        summary["maintenance"] = {
            "checked": maintained.get("checked"), "changed": maintained.get("changed"),
            "satisfied": maintained.get("satisfied"),
            "emitted_eligibilities": maintained.get("emitted_eligibilities"),
            "llm_calls": maintained.get("llm_calls"),
            "auto_resumed": maintained.get("auto_resumed"),
            "head_after_commit": head_v2,
            "eligibility": {k: eligibility.get(k) for k in (
                "eligibility_id", "status", "activity_id", "activity_revision",
                "waiting_episode_id", "resume_condition_ref")},
        }

        # 4. a fresh NO_ACTION leaves it exactly PAUSED
        cognition.policy = "no_action"
        third = cycle.evaluate_once(trigger_ref="evt:pause-smoke:3",
                                    observed_at=_iso(t0), act=True)
        _check(third.get("decision") == "NO_ACTION",
               "round 3 did not commit NO_ACTION", third)
        after_no_action = _activity(runtime, activity_id)
        _check(after_no_action.get("status") == "PAUSED",
               "NO_ACTION changed the paused Activity", after_no_action)
        _check(int(after_no_action.get("revision", -1)) == int(still_paused.get("revision", -2)),
               "NO_ACTION changed the Activity revision", after_no_action)
        after_no_action_elig = _eligibilities(runtime).get(eligibility["eligibility_id"]) or {}
        _check(after_no_action_elig.get("status") == awa.ELIG_STATUS_OPEN,
               "NO_ACTION consumed the eligibility", after_no_action_elig)
        summary["round3"] = {"decision": third.get("decision"), "outcome": third.get("outcome"),
                             "activity_status": after_no_action.get("status"),
                             "eligibility_status": after_no_action_elig.get("status")}

        # 5. another real owner head makes the situation fresh, then RESUME the same Activity
        writer.mode = "write"
        head_v3_outcome = port.commit_version(
            artifact_id=seed, expected_head=head_v2,
            content="%s\n\n第三版（owner 真实提交）\n" % (port.resume_point(seed).content or ""),
            grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("paused-condition-smoke", "v3", seed))
        _check(head_v3_outcome.applied, "the owner did not commit version 3",
               head_v3_outcome.to_dict())
        cognition.policy = "resume"
        fourth = cycle.evaluate_once(trigger_ref="evt:pause-smoke:4",
                                     observed_at=_iso(t0), act=True)
        _check(fourth.get("decision") == "RESUME",
               "round 4 did not commit RESUME", fourth)
        _check(fourth.get("adoption_status") == "APPLIED",
               "round 4 RESUME was not adopted", fourth)
        resumed = _activity(runtime, activity_id)
        _check(resumed.get("status") == "ACTIVE",
               "the same Activity was not resumed", resumed)
        _check(str(resumed.get("activity_id")) == activity_id,
               "a different Activity came back", resumed)
        consumed = _eligibilities(runtime).get(eligibility["eligibility_id"]) or {}
        _check(consumed.get("status") == awa.ELIG_STATUS_CONSUMED,
               "the eligibility was not consumed by the resume", consumed)
        summary["round4"] = {
            "decision": fourth.get("decision"), "outcome": fourth.get("outcome"),
            "adoption_status": fourth.get("adoption_status"),
            "activity_status": resumed.get("status"),
            "activity_ref": resumed.get("activity_id"),
            "eligibility_status": consumed.get("status"),
            "eligibility_reason": consumed.get("resolution_reason_code"),
        }

        summary["checks"] = {
            "round1_no_change": True,
            "pause_condition_offered": bool(offered_pause.get("available")),
            "pause_adopted": second.get("adoption_status") == "APPLIED",
            "activity_paused": paused.get("status") == "PAUSED",
            "one_pause_bound_condition": (len(conditions) == 1
                                          and awa.is_pause_bound_condition(condition)),
            "owner_head_satisfied_condition": (list(maintained.get("satisfied") or [])
                                               == [condition["condition_id"]]),
            "one_open_eligibility": len(open_rows) == 1,
            "maintenance_zero_model": int(maintained.get("llm_calls") or 0) == 0,
            "no_auto_resume": not maintained.get("auto_resumed"),
            "no_action_keeps_paused": after_no_action.get("status") == "PAUSED",
            "no_action_keeps_eligibility_open":
                after_no_action_elig.get("status") == awa.ELIG_STATUS_OPEN,
            "fresh_resume_same_activity": (fourth.get("decision") == "RESUME"
                                           and resumed.get("status") == "ACTIVE"
                                           and str(resumed.get("activity_id")) == activity_id),
            "eligibility_consumed": consumed.get("status") == awa.ELIG_STATUS_CONSUMED,
        }

        # Independent isolated chain: a legal unconditional PAUSE, offered the same
        # condition, must never grow one.  It does not alter the positive chain above.
        unconditional = _run_unconditional_pause_negative(services, RUN)
        summary["unconditional"] = unconditional
        summary["checks"].update({
            "unconditional_" + key: value
            for key, value in unconditional["checks"].items()})

        passed = all(bool(value) for value in summary["checks"].values())
        summary["ok"] = passed
        summary["model_calls"] = cognition.calls
        summary["writer_calls"] = writer.calls
        print("SMOKE_OK" if passed else "SMOKE_FAIL")
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
