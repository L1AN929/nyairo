#!/usr/bin/env python3
"""B8-3 / E10.2: five negative scenarios through the real entry points.

Every scenario runs through the real chain -- ``AgencyDecisionService.evaluate_opportunity``
via ``AutonomousLifeCycle.evaluate_once``, the real Decision adoption, the real
``ActionCommandService`` and the real Supply/World owners.  Only the *cognition output*
is a controlled stand-in, which is what E10.2 allows: a model can be wrong on purpose,
the owners cannot be replaced.

Runs inside the isolated environment (a real Life Supply owner on a socket, a real
subject, a real artifact grant).  No model is called.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
COMPONENTS = HERE.parents[3]
LIFE_SCRIPTS = COMPONENTS / "life" / "scripts"
NATIVE = COMPONENTS / "native"
for _path in (str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
              str(NATIVE / "memory_runtime_v1"), str(COMPONENTS.parent / "chiyo_bundle")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


class BadCognition:
    """A controlled cognition output.  It decides nothing; it only answers."""

    def __init__(self, *, decision="START", candidate_id=None, rationale="", text=""):
        self.decision = decision
        self.candidate_id = candidate_id
        self.rationale = rationale
        self.text = text
        self.calls = 0

    def invoke(self, request, *, schema_retry_index=0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        allowed = [str(x) for x in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = self.candidate_id
        if chosen is None and self.decision != "NO_ACTION":
            # A well-behaved stand-in picks a candidate that is really in the set, so the
            # scenarios that are *about the owner* reach the owner.  The scenarios that
            # are about a bad model name a bogus id on purpose.
            candidates = list(getattr(request, "candidates", []) or [])
            chosen = candidates[0].get("candidate_id") if candidates else None
        raw = {"decision": self.decision, "selected_candidate_id": chosen,
               "reason_codes": [allowed[0] if allowed else "DECISION_RECORDED"],
               "short_rationale": self.rationale or "controlled output"}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:b8-negative", model_id="b8-negative",
            model_version="b8", raw_output=raw, latency_ms=0)


class IdentityWriter:
    def next_content(self, point, *, observed_at: str) -> str:
        return point.content or ""


class RacingWriter:
    """Someone else edits the work between our read and our commit."""

    def __init__(self, port, artifact_id, grant_id):
        self.port = port
        self.artifact_id = artifact_id
        self.grant_id = grant_id
        self.interference = None

    def next_content(self, point, *, observed_at: str) -> str:
        self.interference = self.port.commit_version(
            artifact_id=self.artifact_id, expected_head=point.head_version_id,
            content=(point.content or "") + "\n\n（别人先改了一笔）\n",
            grant_id=self.grant_id, operation_key="op:b8:race:1")
        return (point.content or "") + "\n\n（我的这一笔）\n"


def main() -> int:
    import agency_decision_ag1 as ag1
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from autonomous_life_cycle import (AutonomousLifeCycle, OUTCOME_ACTED,
                                       OUTCOME_ALREADY_DONE, OUTCOME_STEP_REFUSED,
                                       OUTCOME_STEP_UNKNOWN)
    from autonomous_life_runner import AutonomousLifeRunner
    from life_artifact_port import LifeArtifactPort
    from life_projection import LifeProjection
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

    subject = os.environ["NYA02_SUBJECT"]
    workspace_id = os.environ["NYA02_WORKSPACE_ID"]
    grant_id = os.environ["NYA02_ARTIFACT_GRANT"]
    install_root = Path(os.environ["NYA02_LIFE_STATE"]).parent
    state = install_root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(install_root, 0o700)

    _scenario_counter = {"n": 0}

    def build(cognition, *, writer=None, drop_response=False, enabled=True, label=None):
        # Each scenario gets its own Life state root: an Activity adopted by one scenario
        # is a real foreground Activity, and AG-2 correctly refuses to start a second one
        # in the same root.  Sharing a root would make the later scenarios fail for a
        # reason that has nothing to do with what they are testing.
        _scenario_counter["n"] += 1
        label = label or "s%02d" % _scenario_counter["n"]
        scenario_root = install_root / ("n-" + label)
        scenario_state = scenario_root / "state"
        scenario_state.mkdir(parents=True, exist_ok=True)
        os.chmod(scenario_root, 0o700)
        (scenario_state / "autonomous_budget.json").unlink(missing_ok=True)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], subject)
        adapter = SupplyManagedArtifactActionAdapter(
            port, grant_id=grant_id, workspace_id=workspace_id,
            fault_injection_drop_response_after_write=drop_response)
        runtime = lrp.build_production_life_runtime(
            scenario_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=scenario_root, artifact_action_adapter=adapter)
        cycle = AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=subject, workspace_id=workspace_id,
            artifact_grant_id=grant_id, writer=writer, enabled=True,
            max_rounds_per_day=8)
        runner = AutonomousLifeRunner(cycle, state_root=scenario_state, enabled=enabled)
        return runtime, port, cycle, adapter, runner

    def owner_state(port, artifact_id):
        head = port.read_head(artifact_id) or {}
        return {"version_number": head.get("version_number"), "version_id": head.get("version_id")}

    # ================================================================= N01
    print("[N01] a candidate ID that is not in the set")
    cognition = BadCognition(candidate_id="cand:not-a-real-candidate",
                             rationale="我选了它")
    runtime, port, cycle, adapter, runner = build(cognition, writer=IdentityWriter())
    try:
        created = port.create_artifact(
            workspace_id=workspace_id, title="N01 验收", content="# N01\n\n第一行。\n",
            grant_id=grant_id, operation_key="op:b8n01:create:1")
        artifact_id = created.artifact_id
        before = owner_state(port, artifact_id)
        out = cycle.evaluate_once(trigger_ref="evt:b8n01")
        after = owner_state(port, artifact_id)
        check(out.get("decision") is None or out.get("decision") != "START",
              "AG-1 did not accept the illegal selection",
              (out.get("decision"), out.get("detail")))
        check(out.get("activity_ref") is None, "AG-2 created no Activity for it",
              out.get("activity_ref"))
        check(out.get("action_id") is None, "AR-0 submitted no action", out.get("action_id"))
        check(before == after, "the artifact is unchanged", (before, after))
        check(len(runtime.canonical_state()["activities"]) == 0,
              "no canonical Activity exists", runtime.canonical_state()["activities"])
    finally:
        runtime.close()

    # ================================================================= N02
    print("[N02] a candidate whose expected version became stale")
    cognition = BadCognition(candidate_id=None, rationale="继续写")
    runtime, port, cycle, adapter, runner = build(cognition, writer=IdentityWriter())
    try:
        out = cycle.evaluate_once(trigger_ref="evt:b8n02:prime")
        check(out.get("decision") == "START", "a legal round runs first", out.get("decision"))
        artifact_id = (port.latest_resumable() or None)
        artifact_id = port.list_artifacts()[0]["artifact_id"]
    finally:
        runtime.close()
    racing = RacingWriter(port, artifact_id, grant_id)
    runtime, port2, cycle, adapter, runner = build(cognition, writer=racing)
    try:
        before = owner_state(port, artifact_id)
        stale = cycle.evaluate_once(trigger_ref="evt:b8n02")
        after = owner_state(port, artifact_id)
        check(racing.interference is not None and str(getattr(racing.interference, "effect_state", "")) == "APPLIED",
              "the concurrent edit really landed first",
              getattr(racing.interference, "status", None))
        check(stale["outcome"] == OUTCOME_STEP_REFUSED,
              "the stale candidate was refused, not applied", stale.get("detail"))
        check(stale.get("action_status") == "FAILED", "AR-0 recorded a refusal",
              stale.get("action_status"))
        check(after["version_id"] == getattr(racing.interference, "version_id", None),
              "the head is the other writer's version, not the stale one's",
              (before, after, getattr(racing.interference, "version_id", None)))
        content = port.read_artifact(artifact_id).get("content") or ""
        check("我的这一笔" not in content,
              "and the refused edit is not in the work", content[-40:])
    finally:
        runtime.close()

    # ================================================================= N03
    print("[N03] a World action that is not in the legal candidate set")
    cognition = BadCognition(candidate_id="move:school",
                             rationale="我想直接去学校")
    runtime, port, cycle, adapter, runner = build(cognition, writer=IdentityWriter())
    try:
        before = owner_state(port, artifact_id)
        out = cycle.evaluate_once(trigger_ref="evt:b8n03")
        after = owner_state(port, artifact_id)
        check(out.get("decision") is None or out.get("decision") != "START",
              "the model cannot add a legal candidate by naming one",
              (out.get("decision"), out.get("detail")))
        check(out.get("action_id") is None,
              "nothing was submitted through the Intent Executor", out.get("action_id"))
        check(before == after, "no owner state changed", (before, after))
        check(len(runtime.canonical_state()["activities"]) <= 1,
              "no Activity was created for an out-of-range action",
              len(runtime.canonical_state()["activities"]))
    finally:
        runtime.close()

    # ================================================================= N04
    print("[N04] the model claims the work is written and remembered")
    cognition = BadCognition(candidate_id=None,
                             rationale="作品已经修改完成，已经写入记忆。",
                             text="作品已经修改完成，已经写入记忆。")
    runtime, port, cycle, adapter, runner = build(cognition, writer=IdentityWriter())
    try:
        before = owner_state(port, artifact_id)
        out = cycle.evaluate_once(trigger_ref="evt:b8n04")
        after = owner_state(port, artifact_id)
        check(out.get("outcome") == OUTCOME_ALREADY_DONE,
              "the owner's own NO_CHANGE decides the outcome, not the text",
              (out.get("outcome"), out.get("detail")))
        check(after == before, "no fake artifact version was created", (before, after))
        check((out.get("owner_version_created") is False),
              "the owner reported no version was created", out.get("owner_version_created"))
        handoff = out.get("experience_handoff") or {}
        check(not handoff.get("handoff_id"),
              "no completed experience was handed to memory", handoff)
        head = port.read_head(artifact_id) or {}
        projection = LifeProjection(runtime=runtime, artifact_port=port)
        text = projection.render()
        check(str(head.get("version_id")) in text,
              "the chat projection still cites the real owner head", text)
        check("已经写入记忆" not in text,
              "and the model's claim is nowhere in it", text)
    finally:
        runtime.close()

    # ================================================================= N05
    print("[N05] a leftover candidate after autonomous life is switched off")
    cognition = BadCognition(candidate_id=None, rationale="继续写")
    runtime, port, cycle, adapter, runner = build(
        cognition, writer=IdentityWriter(), drop_response=True)
    try:
        unknown = cycle.evaluate_once(trigger_ref="evt:b8n05:prime")
        check(unknown.get("outcome") == OUTCOME_STEP_UNKNOWN,
              "a round with a lost answer enters UNKNOWN", unknown.get("detail"))
        activity_before = dict(runtime.canonical_state()["activities"])
        check(bool(activity_before), "an Activity exists before the switch is turned off",
              list(activity_before))
        runner.disable()
        check(runner.enabled is False, "autonomous life is off", runner.enabled)
        refused = runner.wake(kind="CHECK_WINDOW", reason="test", trigger_ref="evt:b8n05:off")
        check(refused.status == "DISABLED", "no new autonomous decision is started",
              refused.status)
        after_off = dict(runtime.canonical_state()["activities"])
        check(after_off == activity_before,
              "the Activity and its checkpoint are retained", (activity_before, after_off))
        action_id = unknown.get("action_id")
        check(bool(action_id), "the UNKNOWN action exists", action_id)
        row = runtime.action_read.get_action(str(action_id)) if action_id else {}
        execution_id = str((row or {}).get("provider_operation_ref") or "")
        resolved = runner.reconcile_pending()
        check(resolved and resolved[0]["action_id"] == action_id,
              "the already-submitted UNKNOWN is still reconciled while off", resolved)
        check(execution_id and runner.cycle.runtime.action_read.get_action(str(action_id)),
              "and it is reconciled by its original execution id", execution_id)
        runner.enable()
        check(runner.enabled is True, "re-enabling works", runner.enabled)
        fresh = runner.wake(kind="CHECK_WINDOW", reason="test", trigger_ref="evt:b8n05:on")
        check(fresh.status != "DISABLED",
              "after re-enabling, candidates are re-validated by a real round",
              fresh.status)
        check(fresh.action_id != action_id or fresh.outcome != "STEP_UNKNOWN",
              "the pre-disable action is not executed again",
              (fresh.action_id, action_id))
    finally:
        runtime.close()

    print()
    if FAILURES:
        print(f"B8_NEGATIVE: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("B8_NEGATIVE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
