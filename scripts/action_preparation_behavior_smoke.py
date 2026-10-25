#!/usr/bin/env python3
"""第二段「动作到准备条件」真 owner 冒烟。

一条最小但完整的链（不是测试工程，只是给父级复跑的实证）：

    World owner（真 socket / 真 lease / 真 Resolver / 真 substrate）
      -> 正式 WorldMoveOpportunityProvider
      -> cycle._refresh_world_opportunities（真实 AG-0 WorldOpportunityCandidateSource）
      -> 正式 AR-0 / RealWorldActionAdapter -> Intent Executor -> gateway -> Resolver
      -> 真实 PICK_UP -> 真实 PLACE 到 owner 投影的台面
      -> 停掉 owner，同根重启
      -> 重新物化候选：owner 当前 placement 仍让"拿起"成为真实可走路径
      -> 再走一次正式 AR-0 PICK_UP，证明不是只在 prompt 里说准备好了

Decision 是脚本替身（按 target 前缀选项），不是模型；不称自然采用。全程 0 产品调用。
gate 仅在本进程临时设为 canonical（隔离夹具），生产默认 off 未改。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s :: %s" % (label, detail))
        FAILURES.append("%s :: %s" % (label, detail))


class PreparationCognition:
    """按 target 前缀选候选的 Decision 脚本替身（不是模型）。"""

    model_id = "stub:action-preparation"
    model_version = "action-preparation-v1"

    def __init__(self) -> None:
        self.intent = "pick_up"
        self.offered: list[list[str]] = []

    @staticmethod
    def _refs(candidate):
        return [str(ref) for ref in (candidate.get("target_refs") or [])]

    @staticmethod
    def _legal(candidate):
        return [str(item) for item in (candidate.get("legal_decisions") or [])]

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        self.offered.append(sorted({ref for c in candidates for ref in self._refs(c)}))
        prefix = {"pick_up": "dailyact:pick_up:",
                  "place_surface": "dailyact:place:surface:"}[self.intent]
        chosen = None
        for candidate in candidates:
            legal = self._legal(candidate) or ["START"]
            if "START" in legal and any(ref.startswith(prefix) for ref in self._refs(candidate)):
                chosen = candidate
                break
        decision = "START" if chosen is not None else "NO_ACTION"
        raw = {
            "decision": decision,
            "selected_candidate_id": chosen.get("candidate_id") if chosen else None,
            "reason_codes": [allowed[0]] if allowed else ["DECISION_RECORDED"],
            "short_rationale": ("按脚本选的 %s" % self.intent) if chosen
                               else "这一轮没有可选的 %s" % self.intent,
        }
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:prep:%s" % self.intent,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=0)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def daily_refs(items) -> list[str]:
    return sorted({str(ref) for item in items for ref in (item.get("target_refs") or [])
                   if str(ref).startswith("dailyact:")})


def prepare_fixture() -> tuple[Path, Path, str, dict]:
    import world_runtime_data as wrd
    from components.worldgen.instance import init_instance
    import world_body_substrate as wbs

    package_dir = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
    base = Path("/tmp/nyaw04b.preparation-smoke")
    base.mkdir(parents=True, exist_ok=True)
    root = base / ("prep-" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    world_home = root / "world"
    package = wrd.active_world({"CHIYO_WORLD_PACKAGE": str(package_dir)})
    init_instance(package_dir, world_home, world_id=package.world_id)

    capabilities = world_home / "state" / "world_object_capabilities.json"
    capabilities.parent.mkdir(parents=True, exist_ok=True)
    capabilities.write_text(json.dumps({
        "schema_version": wbs.CAPABILITIES_SCHEMA,
        "objects": {
            "i-cup-01": {"takeable": "true", "portable": "true", "placeable": "true"},
            "i-frame-02": {"takeable": "true", "portable": "true", "placeable": "true"},
            "i-desk-01": {"takeable": "false", "portable": "false", "placeable": "false"},
        }}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    economy = world_home / "state" / "world_property_economy.json"
    economy.write_text(json.dumps({
        "version": 1,
        "items": [{"item_instance_id": "i-desk-01", "catalog_item_id": "simple_desk",
                   "disposition": "OWNED"}]}, ensure_ascii=False), encoding="utf-8")

    store = wbs.SubstrateStore(wbs.substrate_path(world_home))
    state = store.load()
    if state is None:
        raise SystemExit("init_instance did not write a substrate")
    for object_id, target in (
            ("i-cup-01", {"kind": "room", "location_id": "home", "area_id": "bedroom"}),
            ("i-frame-02", {"kind": "room", "location_id": "park", "area_id": "park"}),
            ("i-desk-01", {"kind": "room", "location_id": "home", "area_id": "bedroom"})):
        state = wbs.place_object(state, object_id, target)
    store.save(state)
    return root, world_home, package.world_id, {
        "CHIYO_WORLD_PACKAGE": str(package_dir),
        "CHIYO_WORLD_HOME": str(world_home),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
    }


def build(world_home: Path, world_id: str, env: dict, state_root: Path, *,
          cognition=None):
    from test_one_day_life import WorldGateway
    import action_intent as ai
    import autonomous_life_cycle as alc
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import world_action_bridge_adapter as bridge
    import world_body_client as wbc
    import world_move_opportunity_source as wmos

    gateway = WorldGateway(world_home, world_id=world_id, environment=dict(env))
    full_env = dict(env)
    full_env.update(gateway.environment())
    resolver = bridge.WorldBridgeResolver(
        client=wbc.WorldBodyClient(environment=full_env), environment=full_env,
        bridge_dir=str(ROOT / "components" / "world" / "service" / "bridge"))
    adapter = bridge.RealWorldActionAdapter(resolver)
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True,
        action_execution_enabled=True, proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition or PreparationCognition(),
        install_root=state_root, world_action_adapter=adapter)
    provider = wmos.WorldMoveOpportunityProvider(
        client=resolver.client, action_intent=ai,
        bridge_dir=str(ROOT / "components" / "world" / "service" / "bridge"),
        environment=full_env, world_home=str(world_home))
    cycle = alc.AutonomousLifeCycle(
        runtime, enabled=True, max_rounds_per_day=16, env=full_env,
        world_home=str(world_home), world_opportunity_provider=provider)
    return {"gateway": gateway, "resolver": resolver, "runtime": runtime,
            "provider": provider, "cycle": cycle, "env": full_env}


def check_surface_token_compatibility() -> None:
    """The ref-safe surface token and the legacy percent spelling must both read."""

    import life_daily_action_targets as targets

    legacy = "dailyact:place:surface:home:desk_surface%3Ai-desk-01:i-cup-01"
    parsed = targets.parse_daily_action_target(legacy)
    check(parsed["params"]["target"]["surface_id"] == "desk_surface:i-desk-01",
          "legacy percent surface target still parses", parsed)
    check(parsed["target_ref"] == legacy, "legacy target ref is unchanged", parsed)

    # 旧拼写的字面 `-`/`-xHH` 必须逐字读回，不能被当成新转义。
    for surface in ("desk-x3Asurface", "a--b", "-x3A", "desk_surface-i-desk-01"):
        legacy = "dailyact:place:surface:home:%s:i-cup-01" % surface
        back = targets.parse_daily_action_target(legacy)["params"]["target"]["surface_id"]
        check(back == surface, "old literal surface stays literal: %s" % surface, legacy)
    check(targets.parse_daily_action_target(
        "dailyact:place:surface:home:%E4%B9%A6%E6%A1%8C:i-cup-01"
    )["params"]["target"]["surface_id"] == "书桌",
          "old non-ASCII percent surface still parses")

    for surface in ("desk_surface:i-desk-01", "desk-x3Asurface", "a--b",
                    "100%", "书桌:台面", "-x3A", "a/b", "/x41"):
        target = targets.encode_daily_action_target(
            "PLACE", {"object_id": "i-cup-01", "target": {
                "kind": "surface", "location_id": "home", "surface_id": surface}})
        back = targets.parse_daily_action_target(target)["params"]["target"]["surface_id"]
        check(back == surface and "%" not in target,
              "surface token round-trips: %s" % surface, target)
    # 旧字面与新冒号编码是两个不同目标，不能互串。
    check(targets.encode_daily_action_target(
        "PLACE", {"object_id": "i-cup-01", "target": {
            "kind": "surface", "location_id": "home", "surface_id": "desk:surface"}})
        != "dailyact:place:surface:home:desk-x3Asurface:i-cup-01",
          "colon surface is not the old literal target")


def main() -> int:
    import world_body_substrate as wbs

    #: 隔离进程临时 gate；生产默认 off 未改。
    os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = "canonical"
    print("[0] surface token compatibility")
    check_surface_token_compatibility()
    root, world_home, world_id, env = prepare_fixture()
    state_root = root / "life"
    state_root.mkdir(parents=True, exist_ok=True)
    os.chmod(state_root, 0o700)   # production root guard: owner-only state root
    cognition = PreparationCognition()
    built = build(world_home, world_id, env, state_root, cognition=cognition)
    gateway, cycle, runtime = built["gateway"], built["cycle"], built["runtime"]
    try:
        print("[1] first legal observation")
        first = cycle._refresh_world_opportunities(observed_at=now_iso())
        refs = daily_refs(first)
        check(any(ref.startswith("dailyact:pick_up:") for ref in refs),
              "owner offers a real pick-up", refs)
        check(not [ref for ref in refs if ":place:surface:" in ref],
              "empty hands expose no surface destination yet", refs)

        print("[2] real PICK_UP through AG-1/AG-2/AR-0/Intent/Resolver")
        cognition.intent = "pick_up"
        picked = cycle.evaluate_once(trigger_ref="prep:pick", observed_at=now_iso())
        check(picked.get("step_path") == "WORLD_AR0", "step went through AR-0", picked)
        check(picked.get("decision") == "START", "AG-1 said START", picked)
        check(picked.get("action_status") == "SUCCEEDED", "AR-0 action SUCCEEDED", picked)
        placement = wbs.placement_of(wbs.SubstrateStore(
            wbs.substrate_path(world_home)).load(), "i-cup-01")
        check(placement and placement.get("kind") == "hand",
              "owner really holds the cup", placement)

        print("[3] real PLACE onto the owner-projected desk surface")
        cycle._refresh_world_opportunities(observed_at=now_iso())
        cognition.intent = "place_surface"
        placed = cycle.evaluate_once(trigger_ref="prep:place", observed_at=now_iso())
        offered = cognition.offered[-1]
        surface_refs = [ref for ref in offered if ":place:surface:" in ref]
        check(bool(surface_refs), "the desk surface is an offered destination", offered)
        check(placed.get("step_path") == "WORLD_AR0", "place went through AR-0", placed)
        check(placed.get("action_status") == "SUCCEEDED", "place SUCCEEDED", placed)
        substrate = wbs.SubstrateStore(wbs.substrate_path(world_home)).load()
        placement = wbs.placement_of(substrate, "i-cup-01")
        check(placement.get("kind") == "surface"
              and placement.get("surface_id") == "desk_surface:i-desk-01",
              "owner really placed it on the desk", placement)
        revision_before = substrate["revision"]

        print("[4] stop the owner and restart at the same world root")
        gateway.stop()
        from test_one_day_life import WorldGateway
        gateway = WorldGateway(world_home, world_id=world_id, environment=dict(env))
        built["gateway"] = gateway
        full_env = dict(env)
        full_env.update(gateway.environment())
        import world_body_client as wbc
        import world_move_opportunity_source as wmos
        client = wbc.WorldBodyClient(environment=full_env)
        provider = wmos.WorldMoveOpportunityProvider(
            client=client, action_intent=__import__("action_intent"),
            bridge_dir=str(ROOT / "components" / "world" / "service" / "bridge"),
            environment=full_env, world_home=str(world_home))
        cycle.world_opportunity_provider = provider
        refs = daily_refs(cycle._refresh_world_opportunities(observed_at=now_iso()))
        check(any(ref.startswith("dailyact:pick_up:") for ref in refs),
              "after restart the cup is a real pick-up target again", refs)
        surfaces = client.call("get_environment").data.get("surfaces") or []
        desk = next((item for item in surfaces
                     if item.get("surface_id") == "desk_surface:i-desk-01"), None)
        check(desk is not None and "i-cup-01" in (desk.get("items") or []),
              "the restarted owner reports the cup on the desk", surfaces)
        revised = wbs.SubstrateStore(wbs.substrate_path(world_home)).load()
        check(revised["revision"] >= revision_before,
              "the same substrate (not a new world) carries the layout",
              (revision_before, revised["revision"]))

        print("[5] AG-0 re-materialises the post-restart path")
        records, _snap, _meta = runtime.world_opportunity_source.materialize(
            observed_at=now_iso())
        record_refs = {ref for row in records for ref in row.target_refs}
        check(any(str(ref).startswith("dailyact:pick_up:") for ref in record_refs),
              "AG-0 offers the post-restart pick-up candidate", sorted(record_refs))

        print("[6] the later path is executable, not just visible")
        cognition.intent = "pick_up"
        again = cycle.evaluate_once(trigger_ref="prep:pick-again", observed_at=now_iso())
        check(again.get("step_path") == "WORLD_AR0", "second pick went through AR-0", again)
        check(again.get("action_status") == "SUCCEEDED", "second pick SUCCEEDED", again)
        placement = wbs.placement_of(wbs.SubstrateStore(
            wbs.substrate_path(world_home)).load(), "i-cup-01")
        check(placement.get("kind") == "hand", "owner holds it again", placement)

        print("[7] ABA at the real owner: two Decisions in the same state both execute")
        from types import SimpleNamespace
        import autonomous_life_cycle as alc
        import life_daily_action_targets as targets

        pick = targets.encode_daily_action_target(
            "PICK_UP", {"object_id": "i-cup-01", "slot_id": "left_hand"})
        place = targets.encode_daily_action_target(
            "PLACE", {"object_id": "i-cup-01", "target": {
                "kind": "surface", "location_id": "home",
                "surface_id": "desk_surface:i-desk-01"}})

        def owner_step(snapshot, target_ref, decision_id):
            runtime_stub = SimpleNamespace(
                state_root=state_root,
                candidate_read=SimpleNamespace(
                    get_candidate=lambda ref: {"target_refs": [target_ref]}))
            owner_cycle = alc.AutonomousLifeCycle(runtime_stub, enabled=True)
            owner_cycle.last_world_observation = snapshot
            decision = SimpleNamespace(selected_candidate_ref="cand:" + decision_id,
                                       decision_id=decision_id)
            return owner_cycle._world_step_spec(decision)

        resolver = built["resolver"]
        executions, pick_steps, after_pick = [], [], []
        snapshot = dict(client.get_snapshot().data)
        for index, target_ref in enumerate((place, pick, place, pick)):
            step = owner_step(snapshot, target_ref, "dec:smokeaba%03d" % index)
            parsed = targets.parse_daily_action_target(step["target_ref"])
            result = resolver.resolve_action(
                idempotency_key=step["idempotency_key"], verb=parsed["verb"],
                target=step["target_ref"], expected_dependencies={})
            executions.append(result["execution_id"])
            snapshot = dict(client.get_snapshot().data)
            placement = wbs.placement_of(wbs.SubstrateStore(
                wbs.substrate_path(world_home)).load(), "i-cup-01")
            expected_kind = "surface" if index % 2 == 0 else "hand"
            check(result["status"] == "committed"
                  and placement.get("kind") == expected_kind,
                  "Decision %d really moved the cup (%s)" % (index, expected_kind),
                  (result, placement))
            if target_ref == pick:
                pick_steps.append(step)
                after_pick.append(snapshot)
        check(len(set(executions)) == 4, "four Decisions -> four executions", executions)
        check(alc.AutonomousLifeCycle._daily_action_state_token(after_pick[0])
              == alc.AutonomousLifeCycle._daily_action_state_token(after_pick[1]),
              "the two pick cycles returned to the same observed state", after_pick)
        check(pick_steps[0]["idempotency_key"] != pick_steps[1]["idempotency_key"],
              "the second pick is a new Decision, not the cached first one", pick_steps)
        print("  scratch root kept: %s" % root)
    finally:
        for step in (built.get("gateway"),):
            if step is not None:
                try:
                    step.stop()
                except Exception:
                    pass
        runtime.close()

    print("RESULT: %s" % ("PASS" if not FAILURES else "FAIL"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
