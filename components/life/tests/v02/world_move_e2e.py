#!/usr/bin/env python3
"""B6-5 / B6-6: one real World MOVE, driven end to end by the real components.

The chain this script executes is the shipped one, in the shipped order:

    legal observation -> AG-0 candidate -> AG-1 Decision -> AG-2 Adoption
      -> AR-0 (proposal, fence, attempt, receipt) -> World Intent Executor
      -> one-shot authorization -> World Gateway socket -> World Resolver
      -> World owner state -> AR-1 settlement / LR-5 progress

Nothing here re-implements any of that: every step is a call into the production
module that already owns it.  What this script adds is the *driving* and the
*checking*, plus deliberate transport faults so the UNKNOWN path can be exercised
against a World that really did commit.

Phases
------
``move``    run the real round; the World must end up at ``nearby_street``.
``verify``  re-read the World after a service restart; the position must persist.
``faults``  the rejection and the reconciliation cases, on a fresh instance.

Environment (required)
----------------------
``NYA02_WORLD_HOME``        the isolated World home
``NYA02_WORLD_LIFE_STATE``  the isolated Life state root
``WORLD_BODY_SOCKET``       the gateway socket
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
COMPONENTS = HERE.parents[3]
LIFE_SCRIPTS = COMPONENTS / "life" / "scripts"
WORLD_BRIDGE = COMPONENTS / "world" / "service" / "bridge"
for _path in (str(LIFE_SCRIPTS), str(WORLD_BRIDGE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


# ---------------------------------------------------------------------------
# the deterministic cognition stand-in
# ---------------------------------------------------------------------------


def make_cognition():
    """A stand-in for the model, not a model.

    It answers inside the legal decision words AG-1 gave it and picks the World
    opportunity AG-0 actually produced.  The real model adapter
    (``agency_cognition_adapter.ProductionAgencyCognitionAdapter``) is exercised
    separately in the B5 A/B run; nothing here is evidence that a model chose.
    """

    import agency_decision_ag1 as ag1

    class WorldMoveCognition(ag1.FakeCognitionAdapter):
        def __init__(self) -> None:
            super().__init__(model_id="b6-world-move-double", model_version="b6-v1")
            self.calls = 0
            self.decisions: list[str] = []

        def invoke(self, request, *, schema_retry_index=0):
            self.calls += 1
            candidates = list(getattr(request, "candidates", []) or [])
            allowed = [str(x) for x in (getattr(request, "allowed_reason_codes", []) or [])]

            def pick(kind):
                return next((c for c in candidates if c.get("candidate_kind") == kind), None)

            current = pick("CONTINUE_CURRENT")
            world = pick("CONSIDER_WORLD_OPPORTUNITY")
            if current is not None:
                decision, chosen = "CONTINUE", current
            elif world is not None:
                decision, chosen = "START", world
            else:
                decision, chosen = "NO_ACTION", None
            reason = next((code for code in allowed if "WORLD" in code.upper()),
                          allowed[0] if allowed else "DECISION_RECORDED")
            raw = {
                "decision": decision,
                "selected_candidate_id": chosen["candidate_id"] if chosen else None,
                "reason_codes": [reason],
                "short_rationale": "去她刚看到的地方。" if chosen else "没有可做的。",
            }
            self.decisions.append(decision)
            return ag1.CognitiveDecisionResponse(
                model_run_ref="mrun:b6-world", model_id=self.model_id,
                model_version=self.model_version, raw_output=raw, latency_ms=0)

    return WorldMoveCognition()


# ---------------------------------------------------------------------------
# transport faults (the only non-production pieces here)
# ---------------------------------------------------------------------------

FAULT_NONE = None
FAULT_LOST_ANSWER = "lost_answer"        # the submit answer never came back
FAULT_BLIND_AFTER_LOSS = "blind"         # ...and the World cannot be asked either


def make_client(*, fault: str = FAULT_NONE):
    """The real gateway client, optionally one that loses answers.

    A lost submit answer is exactly the case that must never become a claimed
    success and must never be retried under a new id: the World may well have
    committed.  ``blind`` additionally loses the *read* that would resolve it, so
    the action really does enter UNKNOWN.
    """

    import world_body_client as wbc

    if not fault:
        return None

    class FaultyClient(wbc.WorldBodyClient):
        lost_submits = 0
        lost_reads = 0

        def submit_action(self, **kwargs):
            result = super().submit_action(**kwargs)
            self.lost_submits += 1
            return wbc.ClientResult(wbc.TIMEOUT, data=result.data,
                                    reason="injected_lost_response")

        def get_action_result(self, execution_id):
            if fault == FAULT_BLIND_AFTER_LOSS:
                self.lost_reads += 1
                return wbc.ClientResult(wbc.TIMEOUT, reason="injected_lost_read")
            return super().get_action_result(execution_id)

    return FaultyClient(environment=os.environ)


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------


def build(state_root: Path, *, client=None):
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import world_action_bridge_adapter as wba
    import world_move_opportunity_source as wmos
    import action_intent as ai
    from autonomous_life_cycle import AutonomousLifeCycle

    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True,
        action_execution_enabled=True, proactive_enabled=False)
    resolver = wba.WorldBridgeResolver(environment=os.environ, bridge_dir=WORLD_BRIDGE)
    if client is not None:
        resolver._client = client
    adapter = wba.RealWorldActionAdapter(resolver)
    cognition = make_cognition()
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=state_root, world_action_adapter=adapter)
    provider = wmos.WorldMoveOpportunityProvider(
        client=resolver.client, action_intent=ai, bridge_dir=str(WORLD_BRIDGE))
    cycle = AutonomousLifeCycle(
        runtime, world_opportunity_provider=provider,
        max_rounds_per_day=16, enabled=True)
    return {"runtime": runtime, "resolver": resolver, "adapter": adapter,
            "provider": provider, "cycle": cycle, "cognition": cognition, "ai": ai}


def read_world(client, action_intent):
    """One bounded, legal observation, plus the selector-visible action space."""

    import world_body_client as wbc

    status = client.get_status()
    snapshot = client.get_snapshot()
    available = client.get_available_actions()
    if status.outcome != wbc.OK or snapshot.outcome != wbc.OK:
        return None, None
    space = action_intent.build_action_space(
        snapshot.data,
        available_actions=(available.data if available.outcome == wbc.OK else None))
    return snapshot.data, action_intent.redact_action_space_for_selector(space)


def world_state_path() -> Path:
    return Path(os.environ["NYA02_WORLD_HOME"]) / "data" / "world" / "world_state.json"


def owner_location() -> str:
    """The World owner's own canonical state, read (never written) from disk."""

    return json.loads(world_state_path().read_text(encoding="utf-8"))["location"]["place_id"]


def owner_revision() -> int:
    return int(json.loads(world_state_path().read_text(encoding="utf-8"))["revision"])


def move_destinations(space) -> list[str]:
    return sorted(c["params"]["destination_location_id"]
                  for c in (space or {}).get("candidates") or [] if c.get("verb") == "MOVE")


def action_payload(runtime, action_id):
    row = runtime.action_read.get_action(str(action_id)) if action_id else None
    return row, dict((row or {}).get("frozen_payload") or {})


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------


def phase_move() -> int:
    from autonomous_life_cycle import OUTCOME_ACTED

    state_root = Path(os.environ["NYA02_WORLD_LIFE_STATE"])
    built = build(state_root)
    runtime = built["runtime"]
    try:
        print("[1] a legal observation of the real World")
        snapshot, space = read_world(built["resolver"].client, built["ai"])
        check(snapshot is not None, "the gateway answered an observation")
        if snapshot is None:
            return 1
        check(owner_location() == "home", "she starts at home", owner_location())
        check(move_destinations(space) == ["nearby_street"],
              "the only reachable place from home is nearby_street",
              move_destinations(space))

        print("[2] the real autonomous round")
        result = built["cycle"].evaluate_once(trigger_ref="evt:b6:world:1")
        check(result.get("step_path") == "WORLD_AR0",
              "the step went through AR-0's World slot", result.get("step_path"))
        check(result.get("decision") == "START", "AG-1 decided START", result.get("decision"))
        check(result.get("adoption_status") == "APPLIED", "AG-2 adopted the decision",
              result.get("adoption_status"))
        check(result.get("action_status") == "SUCCEEDED", "the AR-0 action SUCCEEDED",
              result.get("action_status"))
        check(result.get("outcome") == OUTCOME_ACTED, "the round acted", result.get("detail"))
        check(bool(result.get("world_evidence_refs")),
              "the receipt carries World-owner evidence", result.get("world_evidence_refs"))
        check(bool(result.get("activity_ref")), "a canonical Activity exists",
              result.get("activity_ref"))
        check(result.get("progress_state") in {"ADVANCED", "COMPLETION_ELIGIBLE"},
              "LR-5 recorded progress from the World's own state",
              (result.get("progress_state"), result.get("progress_ingest_error"),
               result.get("checkpoint_ref")))

        print("[3] the World owner really moved")
        check(owner_location() == "nearby_street",
              "world_state.location is nearby_street", owner_location())

        print("[4] a second legal observation reads the new place")
        snapshot2, space2 = read_world(built["resolver"].client, built["ai"])
        check((snapshot2 or {}).get("location", {}).get("place_id") == "nearby_street",
              "the gateway now reports nearby_street", (snapshot2 or {}).get("location"))
        check("home" in move_destinations(space2),
              "the street offers a way back home", move_destinations(space2))

        print("[5] B6-6: the candidate set follows the real World state")
        check(move_destinations(space) != move_destinations(space2),
              "position A and position B produce different legal MOVE candidates",
              f"{move_destinations(space)} vs {move_destinations(space2)}")
        opportunities = built["provider"]()
        destinations = sorted(ref.split(":", 1)[1] for item in opportunities
                              for ref in item["target_refs"])
        check(destinations == move_destinations(space2),
              "AG-0's observed opportunities match the World's own reachable set",
              destinations)
        source = runtime.world_opportunity_source
        for item in opportunities:
            source.record_observed_opportunity(item)
        records, _snap, _meta = source.materialize(
            observed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        kinds = {r.candidate_kind for r in records}
        check(kinds == {"CONSIDER_WORLD_OPPORTUNITY"},
              "AG-0 materialised World-opportunity candidates", kinds)
        per_destination = {ref for r in records for ref in r.target_refs}
        check(all(("place:%s" % d) in per_destination for d in destinations),
              "AG-0 offers a candidate for every place the World currently offers",
              sorted(per_destination))
        check(len(records) >= len(destinations),
              "one candidate per observed destination, plus any still-valid earlier "
              "observation", (len(records), len(destinations)))

        print("[6] replaying the same intent must not move her twice")
        revision_before = owner_revision()
        row, payload = action_payload(runtime, result.get("action_id"))
        check(row is not None, "the round produced a real AR-0 action")
        replay = built["resolver"].resolve_action(
            idempotency_key=str((row or {}).get("idempotency_key")),
            verb=str(payload.get("verb")), target=str(payload.get("target")),
            expected_dependencies={})
        check(replay["execution_id"] == row.get("provider_operation_ref"),
              "the replay reuses the original execution id",
              (replay["execution_id"], row.get("provider_operation_ref")))
        check(owner_revision() == revision_before,
              "the replay did not touch the World again",
              f"{revision_before} -> {owner_revision()}")
        check(owner_location() == "nearby_street",
              "she is still exactly where she went", owner_location())
    finally:
        runtime.close()
    return 0


def phase_verify() -> int:
    """After a service restart: the position is the owner's, not the process's."""

    built = build(Path(os.environ["NYA02_WORLD_LIFE_STATE"]))
    try:
        print("[7] the position survived a service restart")
        check(owner_location() == "nearby_street",
              "world_state still says nearby_street", owner_location())
        snapshot, space = read_world(built["resolver"].client, built["ai"])
        check((snapshot or {}).get("location", {}).get("place_id") == "nearby_street",
              "the restarted gateway reports the same place", (snapshot or {}).get("location"))
        check(move_destinations(space) == sorted(
            ["home", "school", "convenience_store", "park", "home_goods_store"]),
            "the restarted gateway offers the street's real neighbours",
            move_destinations(space))
    finally:
        built["runtime"].close()
    return 0


def phase_faults() -> int:
    """Rejection, and UNKNOWN resolved by the original execution id."""
    import world_action_bridge_adapter as wba
    from autonomous_life_cycle import OUTCOME_STEP_UNKNOWN

    state_root = Path(os.environ["NYA02_WORLD_LIFE_STATE"])

    print("[8] an unreachable MOVE is refused, and nothing is claimed")
    built = build(state_root)
    runtime = built["runtime"]
    try:
        check(owner_location() == "home", "she is at home", owner_location())
        before = owner_revision()
        refused = built["resolver"].resolve_action(
            idempotency_key="idem:b6:unreachable:1", verb="MOVE", target="school",
            expected_dependencies={})
        check(refused["transport_result"] == wba.ar0.TRANSPORT_ERR_DEFINITE_REJECT,
              "the World refused it definitively", refused["transport_result"])
        check(owner_revision() == before, "the refused move changed nothing",
              f"{before} -> {owner_revision()}")
        check(owner_location() == "home", "she did not move", owner_location())
        view = built["resolver"].reconcile_execution(refused["execution_id"])
        check(view["outcome"] != "SUCCEEDED",
              "the World never claims the refused move succeeded", view)
    finally:
        runtime.close()

    print("[9] a lost answer is reconciled with the original execution id")
    blind = make_client(fault=FAULT_BLIND_AFTER_LOSS)
    built = build(state_root, client=blind)
    runtime = built["runtime"]
    try:
        revision_before = owner_revision()
        result = built["cycle"].evaluate_once(trigger_ref="evt:b6:world:2")
        check(result.get("outcome") == OUTCOME_STEP_UNKNOWN,
              "the lost answer became UNKNOWN, never a claimed success",
              result.get("detail"))
        check(result.get("action_status") == "UNKNOWN",
              "the AR-0 action entered UNKNOWN", result.get("action_status"))
        check(owner_location() == "nearby_street",
              "the World did commit (that is exactly why UNKNOWN must be resolved)",
              owner_location())
        check(owner_revision() == revision_before + 1,
              "the World moved exactly once", f"{revision_before} -> {owner_revision()}")
        check("progress_state" not in result or result.get("progress_state") is None,
              "no progress was claimed for an unresolved action",
              result.get("progress_state"))

        row, payload = action_payload(runtime, result.get("action_id"))
        check(row is not None, "the UNKNOWN action exists in AR-0")
        execution_id = str((row or {}).get("provider_operation_ref") or "")
        check(execution_id.startswith("world-exec:"),
              "the action carries a World execution id", execution_id)

        # The World can be reached again: ask it about the ORIGINAL execution.
        built["resolver"]._client = None
        adapter = built["adapter"]
        if row is None:
            check(False, "the UNKNOWN action exists in AR-0",
                  "no action row, so nothing can be reconciled")
            return 1
        reconciled = adapter.query_status_for_reconcile(action=row, attempt=None)
        check(reconciled["result"] == wba.ar0.RECON_RESULT_CONFIRMED_SUCCEEDED,
              "reconciliation finds the original execution succeeded",
              reconciled["result"])
        check(reconciled["world_query_ref"] == execution_id,
              "it asked with the original execution id, not a new one",
              reconciled["world_query_ref"])
        resolved = runtime.action_command.reconcile_action(action_id=str(result.get("action_id")))
        check(str((resolved.get("action") or {}).get("status")) in {"SUCCEEDED", "FAILED"},
              "AR-0 resolved the UNKNOWN action from World evidence",
              (resolved.get("action") or {}).get("status"))
        check(owner_revision() == revision_before + 1,
              "reconciling moved nothing a second time",
              f"{revision_before} -> {owner_revision()}")
        check(owner_location() == "nearby_street", "she is where the World says she is",
              owner_location())
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    _phase = (sys.argv[1] if len(sys.argv) > 1 else "move").strip().lower()
    _handler = {"move": phase_move, "verify": phase_verify, "faults": phase_faults}[_phase]
    _code = _handler()
    print()
    if FAILURES:
        print("WORLD_MOVE_E2E: FAIL (%d assertion(s))" % len(FAILURES))
        for _item in FAILURES:
            print("  -", _item)
        raise SystemExit(1)
    print("WORLD_MOVE_E2E: PASS (%s)" % _phase)
    raise SystemExit(_code)
