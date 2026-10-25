#!/usr/bin/env python3
"""Zero-model real-owner smoke: PAUSE frees the slot, another Activity starts, resume waits.

What it really does (local only, 0 product model, 0 network)
-----------------------------------------------------------
1. Starts a **real** Life Supply owner on its own Unix socket and prepares a real instance
   through ``one_day_life.prepare``.  A second real ManagedArtifact (work B) is created by the
   same Supply owner, so the two Activities are about two different works.
2. Assembles the **real** production Life runtime and ``assemble_autonomous_life``; Decision
   and content are scripted doubles, everything else (AG-0/AG-1/AG-2/LR-2/LR-4/AR-0/Supply)
   is real.
3. Round 1: START work A; the content writer answers the legal ``no_change``, so the next
   round owns a real pause/wait basis.
4. Round 2: the scripted Decision chooses PAUSE and names that basis.  AG-2 adopts a real
   LR-2 PAUSE, which now releases the foreground slot, and LR-4 binds the condition.
5. The real Supply owner commits a new head for A; ``maintain_waiting`` lets LR-4 satisfy the
   condition and emit one OPEN eligibility.  A stays PAUSED.
6. Round 3: a fresh Decision STARTS work B while A is suspended.  B runs; A is not resumed and
   B is not paused.  Canonical has at most one ACTIVE Activity and the foreground is it.
7. A real-owner conflict negative control: while B is ACTIVE, calling LR-4's explicit resume
   for A's OPEN eligibility is refused by LR-2's foreground rule -- B is not paused, A stays
   PAUSED, the eligibility stays OPEN and the canonical revision does not move.
8. Round 4: a fresh Decision PAUSEs B (unconditional), releasing the slot again.
9. Round 5: a fresh NO_ACTION keeps both Activities suspended and A's eligibility OPEN.
10. The owner changes A's head again (a real new fact, not a forced wake); round 6 RESUMEs A
    by its same activity id.  B stays PAUSED and A's eligibility is CONSUMED.

Boundaries: no real model, no product instance, no wall-clock wait.  The Decision and content
writer are scripted doubles.  The conflict control goes through LR-4/LR-2 (real owners), not
through AG-2's adoption door; the AG-2 adoption-level refusal is covered by
``tests.test_paused_background_single_execution``.  This is not G1, not a 24h run and not
evidence that a model naturally makes these choices.

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/paused_background_single_execution_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_continuity as ac  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import one_day_life as odl  # noqa: E402

from tests.test_one_day_life import Services  # noqa: E402

SCRATCH = Path(os.environ.get("NYAIRO_PAUSED_BACKGROUND_SMOKE_ROOT")
               or "/tmp/nyairo-paused-background-smoke")
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


def _artifact_refs(value) -> set[str]:
    text = str(value or "")
    if not text:
        return set()
    if text.startswith("artifact:artifact:"):
        return {text, text[len("artifact:"):]}
    if text.startswith("artifact:"):
        return {text, "artifact:" + text}
    return {"artifact:" + text}


def _is_closeout_candidate(row) -> bool:
    return any(ref.startswith(CLOSEOUT_REF_PREFIX) for ref in _candidate_refs(row))


def _start_matches(candidates, artifact_id) -> list[dict]:
    wanted = _artifact_refs(artifact_id)
    return [row for row in candidates
            if not _is_closeout_candidate(row)
            and wanted & set(_candidate_refs(row))
            and "START" in (row.get("legal_decisions") or [])]


class _ScriptedDecisionModel:
    """Decision double: only chooses inside the real AG-1 legal set, returns raw JSON."""

    model_id = "stub:paused-background-smoke"
    model_version = "paused-background-smoke-v1"

    def __init__(self, *, artifact_a: str, artifact_b: str) -> None:
        self.artifact_a = str(artifact_a)
        self.artifact_b = str(artifact_b)
        self.policy = "start_a"
        self.calls = 0
        self.last_offered: dict = {}
        self.last_selection: dict = {}

    def _raw(self, candidates, allowed):
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        if self.policy == "start_a":
            matches = _start_matches(candidates, self.artifact_a)
            self.last_selection = {"policy": self.policy,
                                   "matches": [row.get("candidate_id") for row in matches]}
            if matches:
                return {"decision": "START", "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: start work A",
                        "deferred_candidate_ids": []}
        if self.policy == "start_b":
            matches = _start_matches(candidates, self.artifact_b)
            self.last_selection = {"policy": self.policy,
                                   "matches": [row.get("candidate_id") for row in matches]}
            if matches:
                return {"decision": "START", "selected_candidate_id": matches[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: start work B",
                        "deferred_candidate_ids": []}
        if self.policy == "pause_with_condition":
            offered = self.last_offered.get("pause_condition") or {}
            pause_candidates = [row for row in candidates
                                if "PAUSE" in (row.get("legal_decisions") or [])]
            self.last_selection = {
                "policy": self.policy,
                "offered": offered,
                "pause_candidates": [row.get("candidate_id") for row in pause_candidates],
            }
            if offered.get("available") and offered.get("condition_ref") and pause_candidates:
                return {"decision": "PAUSE",
                        "selected_candidate_id": pause_candidates[0]["candidate_id"],
                        "reevaluation_hint_ref": str(offered["condition_ref"]),
                        "reason_codes": reason,
                        "short_rationale": "stub: pause A until the offered condition holds",
                        "deferred_candidate_ids": []}
        if self.policy == "pause_b":
            pause_candidates = [row for row in candidates
                                if "PAUSE" in (row.get("legal_decisions") or [])]
            self.last_selection = {"policy": self.policy,
                                   "pause_candidates": [row.get("candidate_id")
                                                        for row in pause_candidates]}
            if pause_candidates:
                return {"decision": "PAUSE",
                        "selected_candidate_id": pause_candidates[0]["candidate_id"],
                        "reason_codes": reason,
                        "short_rationale": "stub: pause B unconditionally",
                        "deferred_candidate_ids": []}
        if self.policy == "resume_a":
            resume_candidates = [row for row in candidates
                                 if "RESUME" in (row.get("legal_decisions") or [])]
            self.last_selection = {"policy": self.policy,
                                   "resume_candidates": [row.get("candidate_id")
                                                         for row in resume_candidates]}
            if len(resume_candidates) == 1:
                return {"decision": "RESUME",
                        "selected_candidate_id": resume_candidates[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "stub: resume A",
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
            model_run_ref="model_run:paused-background-smoke:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class _WorkWriter:
    """Content double: no_change for work A (the pause basis), ordinary text for work B."""

    def __init__(self, *, no_change_artifact: str) -> None:
        self.no_change_artifact = str(no_change_artifact)
        self.calls = 0

    def next_content(self, point, *, observed_at: str) -> str:
        self.calls += 1
        if str(getattr(point, "artifact_id", "") or "") == self.no_change_artifact:
            raise mcw.ModelNoChange("nothing to add on this head")
        return "%s\n\n（作品乙追加）%s\n" % (getattr(point, "content", "") or "", observed_at)


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


def _canonical(runtime) -> dict:
    state, _journal = runtime.activity_read.read_canonical_state(include_journal=False)
    return dict(state or {})


def _active_ids(state: dict) -> list[str]:
    return [aid for aid, row in (state.get("activities") or {}).items()
            if str(row.get("status") or "") == ac.STATUS_ACTIVE]


def _assert_single_execution(runtime, expected_fg, *, where: str) -> dict:
    state = _canonical(runtime)
    active = _active_ids(state)
    _check(len(active) <= 1, "%s: more than one ACTIVE Activity" % where,
           {"active": active, "foreground": state.get("foreground_activity_ref")})
    _check(state.get("foreground_activity_ref") == expected_fg,
           "%s: foreground is not the expected Activity" % where,
           {"expected": expected_fg, "state_foreground": state.get("foreground_activity_ref"),
            "active": active})
    if expected_fg is None:
        _check(active == [], "%s: an ACTIVE Activity exists without a foreground" % where, active)
    else:
        _check(active == [expected_fg], "%s: foreground is not the only ACTIVE Activity" % where,
               {"active": active, "foreground": expected_fg})
    return state


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    services = Services(RUN / "services")
    runtime = None
    summary: dict = {"root": str(RUN), "checks": {}, "limitations": [
        "the Decision and content writer are scripted doubles; no product model was called",
        "the two works and the changed head are real Supply owner state; the seed text is an operator fixture",
        "the conflict negative control calls LR-4/LR-2 directly (real owners), not AG-2's adoption door",
        "no wall-clock wait and no natural model adoption: this is not G1 and not a 24h run",
    ]}
    try:
        instance = RUN / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:paused-background",
            title="Background pause single execution smoke",
            seed_content="作品甲的第一句。\n")
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
        artifact_a = str(data["artifact_id"])
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        created_b = port.create_artifact(
            workspace_id=str(data["workspace_id"]),
            title="作品乙",
            content="作品乙的第一句。\n",
            grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("paused-background-smoke", "create-b", "b"))
        _check(created_b.applied and bool(created_b.artifact_id),
               "the owner did not create work B", created_b.to_dict())
        artifact_b = str(created_b.artifact_id)

        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        cognition = _ScriptedDecisionModel(artifact_a=artifact_a, artifact_b=artifact_b)
        writer = _WorkWriter(no_change_artifact=artifact_a)
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
        head_a1 = str((port.read_head(artifact_a) or {}).get("version_id") or "")

        # 1. real START of work A
        first = cycle.evaluate_once(trigger_ref="evt:bg-smoke:1", observed_at=_iso(t0), act=True)
        state = _canonical(runtime)
        active = [row for row in (state.get("activities") or {}).values()
                  if str(row.get("status") or "") == ac.STATUS_ACTIVE]
        _check(len(active) == 1, "round 1 did not leave exactly one ACTIVE Activity", state)
        activity_a = str(active[0]["activity_id"])
        _assert_single_execution(runtime, activity_a, where="round1-start-a")
        summary["round1"] = {"outcome": first.get("outcome"), "activity_a": activity_a,
                             "foreground": _canonical(runtime).get("foreground_activity_ref"),
                             "head_a": head_a1}

        # 2. conditional PAUSE of A: real LR-2 pause + LR-4 binding, slot released
        cognition.policy = "pause_with_condition"
        second = cycle.evaluate_once(trigger_ref="evt:bg-smoke:2", observed_at=_iso(t0), act=True)
        offered = dict(cognition.last_offered.get("pause_condition") or {})
        _check(bool(offered.get("available")),
               "round 2 did not offer a pause condition", cognition.last_offered)
        _check(second.get("decision") == "PAUSE" and second.get("adoption_status") == "APPLIED",
               "the conditional PAUSE was not adopted", second)
        paused_a = _activity(runtime, activity_a)
        _check(paused_a.get("status") == ac.STATUS_PAUSED, "A is not PAUSED", paused_a)
        _check(paused_a.get("foreground_released") is True,
               "the PAUSE did not mark the Activity as released", paused_a)
        _assert_single_execution(runtime, None, where="round2-pause-a")
        conditions = _conditions(runtime)
        _check(len(conditions) == 1, "no single pause condition was bound", conditions)
        condition = next(iter(conditions.values()))
        _check(str(condition.get("activity_id")) == activity_a,
               "the condition is not bound to A", condition)
        _check(int(condition.get("activity_revision_at_bind")) == int(paused_a["revision"]),
               "the condition is not bound at the paused revision",
               {"condition": condition, "activity": paused_a})
        summary["round2"] = {"decision": second.get("decision"),
                             "activity_a_status": paused_a.get("status"),
                             "foreground": None,
                             "condition_id": condition.get("condition_id"),
                             "offered_ref": offered.get("condition_ref")}

        # 3. the owner really changes A's head; LR-4 alone satisfies the condition
        head_a2_outcome = port.commit_version(
            artifact_id=artifact_a, expected_head=head_a1,
            content="%s\n\n作品甲第二版（owner 真实提交）\n" % (
                port.resume_point(artifact_a).content or ""),
            grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("paused-background-smoke", "v2-a", "a"))
        _check(head_a2_outcome.applied, "the owner did not commit A version 2",
               head_a2_outcome.to_dict())
        maintained = cycle.maintain_waiting(observed_at=_iso(t0))
        _check(list(maintained.get("satisfied") or []) == [condition["condition_id"]],
               "LR-4 did not satisfy exactly the bound condition", maintained)
        _check(int(maintained.get("llm_calls") or 0) == 0
               and not maintained.get("auto_resumed"),
               "maintenance spent a request or resumed by itself", maintained)
        eligibilities = _eligibilities(runtime)
        open_rows = [row for row in eligibilities.values()
                     if row.get("status") == "OPEN"]
        _check(len(open_rows) == 1, "maintenance did not emit one OPEN eligibility",
               eligibilities)
        eligibility_a = open_rows[0]
        _check(str(eligibility_a.get("activity_id")) == activity_a,
               "the eligibility is not for A", eligibility_a)
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "maintenance resumed A by itself", _activity(runtime, activity_a))
        _assert_single_execution(runtime, None, where="maintain-a")
        summary["maintenance"] = {"satisfied": maintained.get("satisfied"),
                                  "eligibility_id": eligibility_a.get("eligibility_id"),
                                  "eligibility_status": eligibility_a.get("status"),
                                  "llm_calls": maintained.get("llm_calls")}

        # 4. a fresh Decision STARTs work B while A stays suspended
        cognition.policy = "start_b"
        third = cycle.evaluate_once(trigger_ref="evt:bg-smoke:3", observed_at=_iso(t0), act=True)
        _check(third.get("decision") == "START" and third.get("adoption_status") == "APPLIED",
               "round 3 did not START work B",
               {key: third.get(key) for key in (
                   "outcome", "decision", "decision_route", "adoption_status",
                   "selected_candidate_ref", "detail", "error", "step_path",
                   "artifact_selection", "work_no_change")}
               | {"offered": cognition.last_offered,
                  "selection": cognition.last_selection,
                  "artifact_b": artifact_b})
        state = _canonical(runtime)
        active = _active_ids(state)
        _check(len(active) == 1, "round 3 did not leave exactly one ACTIVE Activity", state)
        activity_b = active[0]
        _check(activity_b != activity_a, "round 3 reused A as the running Activity", state)
        _assert_single_execution(runtime, activity_b, where="round3-start-b")
        paused_a = _activity(runtime, activity_a)
        _check(paused_a.get("status") == ac.STATUS_PAUSED,
               "starting B changed A's status", paused_a)
        _check(_eligibilities(runtime)[eligibility_a["eligibility_id"]]["status"] == "OPEN",
               "starting B consumed A's eligibility", _eligibilities(runtime))
        b_status_round3 = _activity(runtime, activity_b).get("status")
        _check(b_status_round3 == ac.STATUS_ACTIVE,
               "B did not start ACTIVE", _activity(runtime, activity_b))
        summary["round3"] = {"decision": third.get("decision"),
                             "activity_b": activity_b,
                             "activity_a_status": paused_a.get("status"),
                             "eligibility_a_status": _eligibilities(runtime)[
                                 eligibility_a["eligibility_id"]]["status"]}

        # 5. real-owner negative control: with B executing, A's background resume is refused
        #    by LR-2's foreground rule; nothing is paused, consumed or written.
        before = _canonical(runtime)
        revision_before = int(before["revision"])
        try:
            runtime.waiting_coordinator.explicit_resume_from_eligibility(
                eligibility_id=str(eligibility_a["eligibility_id"]),
                expected_revision=revision_before,
                idempotency_key="smoke-bg-conflict",
                decision_ref="dec:smoke-bg-conflict",
                adoption_ref="adp:smoke-bg-conflict",
                source_refs=["evt:smoke-bg-conflict"],
                occurred_at=_iso(t0), observed_at=_iso(t0))
        except ac.ForegroundConflictError:
            conflict_refused = True
        else:
            conflict_refused = False
        _check(conflict_refused, "a background resume was not refused while B was running")
        after = _canonical(runtime)
        _check(int(after["revision"]) == revision_before,
               "the refused resume changed the canonical revision",
               {"before": revision_before, "after": after["revision"]})
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_ACTIVE,
               "the refused resume paused B", _activity(runtime, activity_b))
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "the refused resume changed A", _activity(runtime, activity_a))
        _check(_eligibilities(runtime)[eligibility_a["eligibility_id"]]["status"] == "OPEN",
               "the refused resume consumed A's eligibility", _eligibilities(runtime))
        summary["conflict"] = {"refused": conflict_refused,
                               "revision_unchanged": int(after["revision"]) == revision_before,
                               "activity_b_status": _activity(runtime, activity_b).get("status"),
                               "eligibility_a_status": _eligibilities(runtime)[
                                   eligibility_a["eligibility_id"]]["status"]}

        # 6. PAUSE B: the slot is free again and B has no condition
        cognition.policy = "pause_b"
        fourth = cycle.evaluate_once(trigger_ref="evt:bg-smoke:4", observed_at=_iso(t0), act=True)
        _check(fourth.get("decision") == "PAUSE" and fourth.get("adoption_status") == "APPLIED",
               "round 4 did not pause B",
               {key: fourth.get(key) for key in (
                   "outcome", "decision", "decision_route", "adoption_status",
                   "selected_candidate_ref", "detail", "error", "step_path",
                   "foreground_work_ref")}
               | {"offered": cognition.last_offered,
                  "selection": cognition.last_selection,
                  "activity_b": activity_b})
        paused_b = _activity(runtime, activity_b)
        _check(paused_b.get("status") == ac.STATUS_PAUSED, "B is not PAUSED", paused_b)
        _check(paused_b.get("foreground_released") is True,
               "B's PAUSE did not release the slot", paused_b)
        _assert_single_execution(runtime, None, where="round4-pause-b")
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "pausing B resumed A", _activity(runtime, activity_a))
        summary["round4"] = {"decision": fourth.get("decision"),
                             "activity_b_status": paused_b.get("status"),
                             "foreground": None}

        # 7. NO_ACTION keeps both suspended Activities and A's OPEN eligibility exactly as they are
        cognition.policy = "no_action"
        fifth = cycle.evaluate_once(trigger_ref="evt:bg-smoke:5", observed_at=_iso(t0), act=True)
        _check(fifth.get("decision") == "NO_ACTION", "round 5 did not commit NO_ACTION",
               {key: fifth.get(key) for key in ("outcome", "decision", "detail", "error")}
               | {"offered": cognition.last_offered})
        _check(_activity(runtime, activity_a).get("status") == ac.STATUS_PAUSED,
               "NO_ACTION resumed A", _activity(runtime, activity_a))
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_PAUSED,
               "NO_ACTION changed B", _activity(runtime, activity_b))
        _check(_eligibilities(runtime)[eligibility_a["eligibility_id"]]["status"] == "OPEN",
               "NO_ACTION consumed A's eligibility", _eligibilities(runtime))
        _assert_single_execution(runtime, None, where="round5-no-action")
        summary["round5"] = {"decision": fifth.get("decision"),
                             "activity_a_status": _activity(
                                 runtime, activity_a).get("status"),
                             "activity_b_status": _activity(
                                 runtime, activity_b).get("status"),
                             "eligibility_a_status": _eligibilities(runtime)[
                                 eligibility_a["eligibility_id"]]["status"]}

        # The owner really changes A's head again.  NO_ACTION left the previous situation
        # settled; this is a new owner fact, not a forced wake, and it opens the next round.
        head_a3_outcome = port.commit_version(
            artifact_id=artifact_a, expected_head=str(
                (port.read_head(artifact_a) or {}).get("version_id") or ""),
            content="%s\n\n作品甲第三版（owner 真实提交）\n" % (
                port.resume_point(artifact_a).content or ""),
            grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("paused-background-smoke", "v3-a", "a"))
        _check(head_a3_outcome.applied, "the owner did not commit A version 3",
               head_a3_outcome.to_dict())

        # 8. RESUME A by its same id; B stays suspended.  The writer stops answering the legal
        #    ``no_change`` for A so the round report keeps the RESUME that really happened.
        writer.no_change_artifact = ""
        cognition.policy = "resume_a"
        sixth = cycle.evaluate_once(trigger_ref="evt:bg-smoke:6", observed_at=_iso(t0), act=True)
        _check(sixth.get("decision") == "RESUME" and sixth.get("adoption_status") == "APPLIED",
               "round 6 did not resume A",
               {key: sixth.get(key) for key in (
                   "outcome", "decision", "decision_route", "adoption_status",
                   "selected_candidate_ref", "detail", "error", "step_path")}
               | {"offered": cognition.last_offered,
                  "selection": cognition.last_selection,
                  "reconsideration": sixth.get("reconsideration")})
        resumed_a = _activity(runtime, activity_a)
        _check(resumed_a.get("status") == ac.STATUS_ACTIVE,
               "A did not return to ACTIVE", resumed_a)
        _check(str(resumed_a.get("activity_id")) == activity_a,
               "a different Activity came back", resumed_a)
        _check(_activity(runtime, activity_b).get("status") == ac.STATUS_PAUSED,
               "resuming A changed B", _activity(runtime, activity_b))
        consumed = _eligibilities(runtime)[eligibility_a["eligibility_id"]]
        _check(consumed.get("status") == "CONSUMED",
               "A's eligibility was not consumed", consumed)
        _assert_single_execution(runtime, activity_a, where="round6-resume-a")
        summary["round6"] = {"decision": sixth.get("decision"),
                             "activity_a_status": resumed_a.get("status"),
                             "activity_b_status": _activity(
                                 runtime, activity_b).get("status"),
                             "eligibility_a_status": consumed.get("status"),
                             "foreground": activity_a}

        summary["checks"] = {
            "round1_one_active_a": True,
            "pause_a_released_slot": paused_a.get("foreground_released") is True,
            "pause_a_condition_bound": (len(_conditions(runtime)) == 1),
            "maintenance_zero_model": int(maintained.get("llm_calls") or 0) == 0,
            "no_auto_resume": not maintained.get("auto_resumed"),
            "owner_head_satisfied_condition": list(maintained.get("satisfied") or [])
            == [condition["condition_id"]],
            "one_open_eligibility_for_a": len(open_rows) == 1,
            "start_b_while_a_suspended": activity_b != activity_a,
            "b_runs_while_a_paused": (b_status_round3 == ac.STATUS_ACTIVE
                                      and paused_a.get("status") == ac.STATUS_PAUSED),
            "conflict_refused_by_owner": conflict_refused,
            "conflict_left_b_running": True,
            "conflict_left_a_paused": True,
            "conflict_kept_eligibility_open": True,
            "no_action_kept_everything": True,
            "pause_b_released_slot": paused_b.get("foreground_released") is True,
            "resume_a_same_activity": str(resumed_a.get("activity_id")) == activity_a,
            "resume_a_consumed_eligibility": consumed.get("status") == "CONSUMED",
            "b_stayed_paused": _activity(runtime, activity_b).get("status") == ac.STATUS_PAUSED,
        }
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
