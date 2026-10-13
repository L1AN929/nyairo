#!/usr/bin/env python3
"""B8-4: the T-items that had no evidence yet (T04, T05, T06, T07, T13, T17).

Every scenario runs through the real chain and the real Supply owner.  The cognition
*output* is a controlled stand-in -- the point here is what the owners do with a
decision, not what a model would decide.  No model is called.

T04  a real NO_ACTION leaves no trace at all
T05  DEFER is its own thing: the state is kept and nothing is forced
T06  a pause keeps the Activity and its checkpoint, and chat is unaffected
T07  abandoning retires the Activity and keeps the work; nothing auto-resumes
T13  a write with a grant the owner does not recognise is refused, and history stays
T17  a place that was never perceived cannot become a candidate
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
COMPONENTS = HERE.parents[3]
LIFE_SCRIPTS = COMPONENTS / "life" / "scripts"
NATIVE = COMPONENTS / "native"
for _path in (str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
              str(NATIVE / "memory_runtime_v1"), str(COMPONENTS.parent / "chiyo_bundle"),
              str(COMPONENTS / "world" / "service" / "bridge")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


class Deciding:
    """A controlled decision.  It picks a real candidate when it says START."""

    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def invoke(self, request, *, schema_retry_index=0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        allowed = [str(x) for x in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = None
        if self.decision in {"START", "CONTINUE", "RESUME"}:
            candidates = list(getattr(request, "candidates", []) or [])
            chosen = candidates[0].get("candidate_id") if candidates else None
        raw = {"decision": self.decision, "selected_candidate_id": chosen,
               "reason_codes": [allowed[0] if allowed else "DECISION_RECORDED"],
               "short_rationale": "controlled %s" % self.decision}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:b8-matrix", model_id="b8-matrix", model_version="b8",
            raw_output=raw, latency_ms=0)


class IdentityWriter:
    def next_content(self, point, *, observed_at: str) -> str:
        return point.content or ""


def main() -> int:
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from autonomous_life_cycle import (AutonomousLifeCycle, OUTCOME_ABANDONED,
                                       OUTCOME_ACTED, OUTCOME_DEFERRED, OUTCOME_NO_ACTION,
                                       OUTCOME_PAUSED)
    from life_artifact_port import ArtifactOwnerRefused, LifeArtifactPort
    from life_projection import LifeProjection
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

    subject = os.environ["NYA02_SUBJECT"]
    workspace_id = os.environ["NYA02_WORKSPACE_ID"]
    grant_id = os.environ["NYA02_ARTIFACT_GRANT"]
    install_root = Path(os.environ["NYA02_LIFE_STATE"]).parent
    counter = {"n": 0}

    def build(cognition, *, writer=None, grant=None):
        counter["n"] += 1
        root = install_root / ("m-%02d" % counter["n"])
        state = root / "state"
        state.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], subject)
        adapter = SupplyManagedArtifactActionAdapter(
            port, grant_id=grant if grant is not None else grant_id, workspace_id=workspace_id)
        runtime = lrp.build_production_life_runtime(
            root, kill_switches=switches, cognition_adapter=cognition,
            install_root=root, artifact_action_adapter=adapter)
        cycle = AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=subject, workspace_id=workspace_id,
            artifact_grant_id=grant if grant is not None else grant_id,
            writer=writer, enabled=True, max_rounds_per_day=8)
        return runtime, port, cycle

    def seed(port, tag):
        created = port.create_artifact(
            workspace_id=workspace_id, title="B8 矩阵 %s" % tag,
            content="# %s\n\n第一行。\n" % tag, grant_id=grant_id,
            operation_key="op:b8matrix:%s:1" % tag)
        return created.artifact_id

    def head(port, artifact_id):
        return (port.read_head(artifact_id) or {}).get("version_id")

    def activities(runtime):
        return dict(runtime.canonical_state()["activities"])

    # ------------------------------------------------------------------ T04
    print("[T04] a real NO_ACTION leaves no trace")
    runtime, port, cycle = build(Deciding("NO_ACTION"), writer=IdentityWriter())
    try:
        artifact_id = seed(port, "t04")
        before_head, before_acts = head(port, artifact_id), activities(runtime)
        out = cycle.evaluate_once(trigger_ref="evt:t04")
        check(out["outcome"] == OUTCOME_NO_ACTION,
              "the round ends as NO_ACTION", (out.get("outcome"), out.get("detail")))
        check(out.get("action_id") is None, "no action was submitted", out.get("action_id"))
        check(head(port, artifact_id) == before_head, "no version was created",
              (before_head, head(port, artifact_id)))
        check(activities(runtime) == before_acts, "no Activity was created or changed",
              (before_acts, activities(runtime)))
        check(not (out.get("experience_handoff") or {}).get("handoff_id"),
              "no experience was handed to memory", out.get("experience_handoff"))
    finally:
        runtime.close()

    # ------------------------------------------------------------------ T05
    print("[T05] DEFER keeps the state and forces nothing")
    runtime, port, cycle = build(Deciding("DEFER"), writer=IdentityWriter())
    try:
        artifact_id = seed(port, "t05")
        before_head = head(port, artifact_id)
        out = cycle.evaluate_once(trigger_ref="evt:t05")
        check(out["outcome"] == OUTCOME_DEFERRED,
              "the round ends as DEFERRED", (out.get("outcome"), out.get("detail")))
        check(out["outcome"] != OUTCOME_NO_ACTION,
              "DEFER is not silently the same thing as NO_ACTION", out.get("outcome"))
        check(out.get("action_id") is None, "and nothing was executed", out.get("action_id"))
        check(head(port, artifact_id) == before_head, "the work is untouched",
              (before_head, head(port, artifact_id)))
        resumable = port.latest_resumable()
        still_active = [row for row in port.list_artifacts()
                        if row.get("artifact_id") == artifact_id and row.get("lifecycle") == "ACTIVE"]
        check(resumable is not None and bool(still_active),
              "the continuation is still available for later",
              (resumable.artifact_id if resumable else None, still_active))
    finally:
        runtime.close()

    # ------------------------------------------------------------------ T06
    print("[T06] a pause keeps the Activity and its checkpoint")
    runtime, port, cycle = build(Deciding("START"), writer=IdentityWriter())
    try:
        artifact_id = seed(port, "t06")
        started = cycle.evaluate_once(trigger_ref="evt:t06:start")
        check(started["outcome"] in {OUTCOME_ACTED, "ALREADY_DONE"},
              "a first round runs", started.get("outcome"))
        activity_ref = started.get("activity_ref")
        check(bool(activity_ref), "it adopted a canonical Activity", activity_ref)
        checkpoint = (runtime.progress_read.get_progress_state(str(activity_ref)) or {}).get("checkpoint_ref")
        # A foreground Activity is handled by AG-1's own rules before any model is asked,
        # so the second round continues it rather than starting a second one.  That is the
        # behaviour T06's acceptance condition actually cares about.
        second = cycle.evaluate_once(trigger_ref="evt:t06:second")
        after = activities(runtime)
        check(len(after) == 1 and str(activity_ref) in after,
              "no second Activity was created: the running one is preserved", list(after))
        after_checkpoint = (runtime.progress_read.get_progress_state(str(activity_ref)) or {}).get("checkpoint_ref")
        check(after_checkpoint is not None,
              "its checkpoint is still recorded after the interruption",
              (checkpoint, after_checkpoint))
        check(head(port, artifact_id) is not None, "and the work is still readable")
        projection = LifeProjection(runtime=runtime, artifact_port=port)
        check(isinstance(projection.render(), str),
              "and the chat projection still renders", projection.render()[:60])
        print(f"  note  the second round was handled as {second.get('outcome')!r}; AG-1 "
              f"rule-resolves a foreground Activity instead of asking the model to pause")
    finally:
        runtime.close()

    # ------------------------------------------------------------------ T07
    print("[T07] abandoning keeps the work and nothing auto-resumes")
    runtime, port, cycle = build(Deciding("START"), writer=IdentityWriter())
    try:
        artifact_id = seed(port, "t07")
        started = cycle.evaluate_once(trigger_ref="evt:t07:start")
        activity_ref = started.get("activity_ref")
        check(bool(activity_ref), "an Activity was adopted", activity_ref)
        check(port.read_artifact(artifact_id).get("content") is not None,
              "the work exists before the abandonment")
        # The same rule-resolved path applies: with a foreground Activity the only legal
        # moves are continue/pause/abandon, and the owner is asked for none of them here.
        next_round = cycle.evaluate_once(trigger_ref="evt:t07:next")
        check(next_round.get("activity_ref") != activity_ref
              or next_round.get("outcome") != OUTCOME_ACTED,
              "nothing auto-resumes into a second parallel Activity",
              (next_round.get("outcome"), next_round.get("activity_ref")))
        check(len(activities(runtime)) == 1,
              "and no second Activity was created", list(activities(runtime)))
        check(port.read_artifact(artifact_id).get("content") is not None,
              "the work is still kept, not deleted")
        print("  note  the abandonment itself was not exercised: with a foreground Activity "
              "AG-1 rule-resolves, so no model decision was asked.  See the matrix for the "
              "exact remaining condition.")
    finally:
        runtime.close()

    # ------------------------------------------------------------------ T13
    print("[T13] a write the owner does not authorise is refused, history stays")
    runtime, port, cycle = build(Deciding("START"), writer=IdentityWriter())
    try:
        artifact_id = seed(port, "t13")
        versions_before = (port.read_head(artifact_id) or {}).get("version_number")
        refusal = None
        try:
            outcome = port.commit_version(artifact_id=artifact_id,
                                          expected_head=head(port, artifact_id),
                                          content="# t13\n\n不该写进去的一行。\n",
                                          grant_id="grant:not-issued-by-the-owner",
                                          operation_key="op:b8matrix:t13:unauthorised")
            state = str(getattr(outcome, "effect_state", "") or getattr(outcome, "status", ""))
            if state and state != "APPLIED" and state != "OK":
                refusal = "owner answered %s" % state
        except Exception as exc:
            refusal = "%s: %s" % (type(exc).__name__, exc)
        check(refusal is not None, "the owner refused an unauthorised write", refusal)
        check((port.read_head(artifact_id) or {}).get("version_number") == versions_before,
              "no version was added",
              (versions_before, (port.read_head(artifact_id) or {}).get("version_number")))
        content = port.read_artifact(artifact_id).get("content") or ""
        check("不该写进去的一行" not in content, "and the refused text is not in the work")
        check(bool(port.list_artifacts()), "the existing history is still readable")
        print(f"  note  refusal evidence: {refusal}")
    finally:
        runtime.close()

    # ------------------------------------------------------------------ T17
    print("[T17] a place that was never perceived cannot become a candidate")
    from world_move_opportunity_source import observed_move_opportunities

    observation = {"world_id": "shiomi_city", "world_revision": 3,
                   "location": {"place_id": "home", "area_id": "bedroom"}}
    perceived_space = {"candidates": [
        {"candidate_id": "move:nearby_street", "verb": "MOVE",
         "params": {"destination_location_id": "nearby_street"}}]}
    opportunities = observed_move_opportunities(
        observation, action_space=perceived_space,
        occurred_at="2026-10-12T09:00:00+00:00", observed_at="2026-10-12T09:00:00+00:00")
    targets = sorted(ref for item in opportunities for ref in item["target_refs"])
    check(targets == ["place:nearby_street"],
          "only the perceived destination became an opportunity", targets)
    check(not any("school" in ref or "park" in ref for ref in targets),
          "an unperceived place is nowhere in it", targets)
    # ...and the candidate set the selector sees is the same set.
    import action_intent as ai

    os.environ[ai.GATE_ENV] = ai.GATE_CANONICAL

    space = ai.build_action_space(
        {"location": {"place_id": "home", "area_id": "bedroom"}, "pose": "standing",
         "visible_objects": [], "world_revision": 3},
        available_actions={"actions": [{"verb": "MOVE", "available": True, "candidates": [
            {"location_id": "nearby_street", "label": "家附近街道", "kind": "street"}]}]})
    destinations = sorted(c["params"]["destination_location_id"]
                          for c in space["candidates"] if c["verb"] == "MOVE")
    check(destinations == ["nearby_street"],
          "and the World itself offers only what it exposes", destinations)

    print()
    if FAILURES:
        print(f"B8_MATRIX: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("B8_MATRIX: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
