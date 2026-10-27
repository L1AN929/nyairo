#!/usr/bin/env python3
"""One real, isolated use of the optional virtual lived-needs feature.

What it proves (and what it does not)
=====================================
* A fresh isolated instance explicitly enables the profile (hunger/thirst/
  toilet initial values are operator parameters, stated in the summary).
* The real Life runtime + real Supply owner + real World/Body gateway execute
  a *scripted* Decision (no product model, zero model requests) that chooses:
  drink water -> use the declared toilet -> eat a riceball -> sit -> start
  rest -> stop rest -> NO_ACTION.
* The Body owner's own needs really change after the owner commits the action,
  and the *next* request material (``request.situation_context``) shows the new
  bands.  A NO_ACTION round changes nothing.
* The same root is stopped cleanly and re-opened: the persisted bands are read
  back, and a read does not write.

Not claimed: natural model adoption, a human body measurement, 24h/G1/V-C, or
that a REST session is recovery.  The Decision and content writer are scripted
doubles; the World/Supply/Body owners are real.

WSL:

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/body_lived_needs_demo.py [--reuse-last]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import action_intent  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from world_body_client import WorldBodyClient  # noqa: E402
from world_journey_action_adapter import (  # noqa: E402
    JourneyWorldResolver, RealJourneyWorldActionAdapter)

from tests.test_one_day_life import (  # noqa: E402
    WORLD_RUNTIME_ROOT, Services, StubWriter, WorldGateway)
from tests.test_world_daily_life_continuous import (  # noqa: E402
    WORLD_PACKAGE, Clock, episode_instant)

SCRATCH = Path(os.environ.get("NYAIRO_LIVED_NEEDS_ROOT")
               or "/tmp/nyairo-body-lived-needs")
GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
SUBJECT = "demo:body-lived-needs"
FOOD = "riceball-01"
WATER = "water-bottle-01"
TOILET = "home-toilet-01"
READING = ("第一页：桌上放着一杯水。\n%PAGE%\n第二页：窗外有风。\n")


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("DEMO_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:8000])
    raise SystemExit(1)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class ScriptedDecision:
    """A deterministic decision double: picks only inside AG-1's real legal set."""

    model_id = "stub:body-lived-needs-demo"
    model_version = "body-lived-needs-demo-v1"

    def __init__(self) -> None:
        self.policy: tuple[str, str | None] = ("no_action", None)
        self.calls = 0
        self.requests: list[dict] = []
        self.last_situation: dict = {}

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        situation = dict(getattr(request, "situation_context", None) or {})
        self.last_situation = situation
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        body_needs = ((situation.get("body") or {}).get("needs")
                      if isinstance(situation.get("body"), dict) else None)
        world_body = ((situation.get("world") or {}).get("body")
                      if isinstance(situation.get("world"), dict) else None)
        world_needs = (world_body.get("needs") if isinstance(world_body, dict) else None)
        self.requests.append({
            "policy": list(self.policy),
            "body_needs": body_needs,
            "world_body_needs": world_needs,
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "candidate_kind": row.get("candidate_kind"),
                            "target_refs": list(row.get("target_refs") or []),
                            "legal_decisions": list(row.get("legal_decisions") or [])}
                           for row in candidates],
        })

        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        kind, wanted = self.policy
        chosen = None
        for row in candidates:
            if "START" not in (row.get("legal_decisions") or []):
                continue
            refs = [str(ref) for ref in (row.get("target_refs") or [])]
            if kind == "consume" and wanted and ("object:%s" % wanted) in refs:
                chosen = row
            elif kind == "toilet" and any(ref.startswith("bodyact:toilet:") for ref in refs):
                chosen = row
            elif kind == "pose" and wanted and any(
                    ref.startswith("bodyact:pose:%s:" % wanted) for ref in refs):
                chosen = row
            elif kind == "rest" and any(
                    ref == "bodyact:rest:%s:rest" % wanted for ref in refs):
                chosen = row
            if chosen is not None:
                break
        if chosen is None:
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": ["NO_ACTION_CHOSEN"],
                   "short_rationale": "demo: no matching legal candidate",
                   "deferred_candidate_ids": []}
        else:
            raw = {"decision": "START",
                   "selected_candidate_id": chosen.get("candidate_id"),
                   "reason_codes": reason,
                   "short_rationale": "demo: scripted choice %s" % (wanted or kind),
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:body-lived-needs-demo:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


def _body_dir(world_home: Path) -> Path:
    return world_home / "data" / "body"


def _enable_profile(world_home: Path, start_at: datetime) -> dict:
    """Explicit fixture initial values; no past experience, no seed workload."""

    body_dir = _body_dir(world_home)
    profile = bln.build_profile(
        hunger=0.72, thirst=0.78, toilet=0.66,
        note="demo fixture: explicit virtual needs, not a human measurement")
    bln.save_profile(body_dir, profile)
    store = br.BodyStore(body_dir)
    state = store.load_runtime()
    if state is None:
        state = br.initial_runtime(at=start_at)
        # The synthetic fixture starts a little tired so REST is reachable; this
        # is an explicit fixture value, not an inferred history.
        state["somatic"]["fatigue"] = 0.45
        state = br.validate_runtime(state)
    state = br.upgrade_to_v2(state, bln.initial_state(profile))
    store.save_runtime(state)
    return {"profile_ref": bln.initial_state(profile)["profile_ref"],
            "initial_bands": bln.bands(state),
            "initial_levels": dict(state["lived_needs"]["levels"])}


def _declare_world(world_home: Path) -> None:
    path = world_home / "state" / "world_object_capabilities.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {}
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    document.setdefault("schema_version", wbs.CAPABILITIES_SCHEMA)
    document.setdefault("objects", {})
    document["consumables"] = {
        WATER: {"consumable": True, "free_to_consume": True,
                "lived_need_effects": {"thirst": 0.8}},
        FOOD: {"consumable": True, "free_to_consume": True,
               "lived_need_effects": {"hunger": 0.65}},
    }
    document["lived_need_facilities"] = {
        TOILET: {"facility_kind": "toilet", "location_id": "home",
                 "lived_need_effects": {"toilet": 0.9},
                 "label": "家里的洗手间"}
    }
    path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8")

    store = wbs.SubstrateStore(wbs.substrate_path(world_home))
    state = store.load()
    _check(state is not None, "prepare wrote no substrate")
    for object_id in (WATER, FOOD):
        state = wbs.place_object(state, object_id,
                                 {"kind": "room", "location_id": "home",
                                  "area_id": "home"})
    store.save(state)


def _bands(client: WorldBodyClient) -> dict:
    answer = client.call("get_body_signals")
    _check(answer.outcome == "OK" and isinstance(answer.data, dict),
           "get_body_signals failed", getattr(answer, "outcome", None))
    return dict(answer.data.get("needs") or {})


def _snapshot_bands(client: WorldBodyClient) -> dict:
    answer = client.get_snapshot()
    _check(answer.outcome == "OK" and isinstance(answer.data, dict),
           "get_snapshot failed", getattr(answer, "outcome", None))
    return dict(answer.data.get("lived_needs") or {})


def _run_round(cycle, cognition, clock, policy, expected_verb, trigger) -> dict:
    cognition.policy = policy
    out = cycle.evaluate_once(trigger_ref=trigger, observed_at=clock.iso(), act=True)
    out["_request_call_index"] = len(cognition.requests) - 1
    if expected_verb is not None:
        envelope = {key: out.get(key) for key in (
            "outcome", "decision", "adoption_status", "selected_candidate_ref",
            "activity_ref", "action_id", "action_status", "world_verb", "detail", "error")}
        detail = {"envelope": envelope,
                  "request": (cognition.requests[-1] if cognition.requests else None)}
        _check(out.get("adoption_status") == "APPLIED",
               "round %s was not adopted" % trigger, detail)
        _check(str(out.get("world_verb")) == expected_verb,
               "round %s did not execute %s" % (trigger, expected_verb), detail)
    return out


def run(run_root: Path) -> dict:
    started = datetime.now(timezone.utc)
    env_gates = {"REAL_WORKSPACE_PROVISION": "ON", "REAL_ARTIFACT_ACTION": "ON"}
    saved = {GATE_ENV: os.environ.get(GATE_ENV)}
    os.environ[GATE_ENV] = "canonical"
    services = Services(run_root / "supply")
    gateway = None
    runtime = None
    summary: dict = {
        "kind": "body_lived_needs_demo",
        "root": str(run_root),
        "started_at": _iso(started),
        "scripted_decision": True,
        "product_model_requests": 0,
        "synthetic_initial_profile": True,
        "limitations": [
            "Decision and content writer are scripted doubles; no product model was called",
            "the initial hunger/thirst/toilet values are explicit fixture parameters",
            "a REST session is not recovery; no claim that she recovered",
            "not G1, not 24h, not natural model adoption",
        ],
    }
    try:
        instance = run_root / "instance"
        with _env(env_gates):
            prepared = odl.prepare(
                instance=instance, supply_socket=services.supply_path,
                governance_socket=services.gov_path, subject=SUBJECT,
                title="Body lived-needs demo",
                world_package=WORLD_PACKAGE,
                reading_title="桌上的水", reading_content=READING,
                create_private_items=True)
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        summary["profile"] = _enable_profile(world_home, started)
        _declare_world(world_home)

        gateway = WorldGateway(world_home, world_id=data["world_id"],
                               environment=odl.instance_environment(instance))
        env = odl.instance_environment(instance)
        env.update(gateway.environment())
        env[GATE_ENV] = "canonical"
        client = WorldBodyClient(environment=env)
        clock = Clock(episode_instant(after=datetime.now(timezone.utc)))
        provider = wmos.WorldMoveOpportunityProvider(
            client=client, action_intent=action_intent, bridge_dir=str(BRIDGE_DIR),
            environment=env, world_home=str(world_home),
            now=lambda: clock.now, observed_now=lambda: clock.now,
            valid_for_seconds=60)
        cognition = ScriptedDecision()
        state_root = odl.life_state_root(instance)
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"],
            workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root,
            world_provider=provider, writer=StubWriter(), enabled=True)
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]
        cycle.world_home = str(world_home)
        cycle.work_artifact_id = str(data.get("work_artifact_id") or "")
        cycle.reading_artifact_id = str(data.get("reading_artifact_id") or "")

        initial_bands = _bands(client)
        summary["before"] = {"snapshot_bands": _snapshot_bands(client),
                             "body_bands": initial_bands}
        _check(initial_bands.get("thirst") in ("mild", "noticeable", "strong"),
               "the demo did not start with a readable thirst need", initial_bands)

        rounds: list[dict] = []

        # 1) drink water -> thirst relieved
        water_out = _run_round(cycle, cognition, clock, ("consume", WATER),
                               "CONSUME", "evt:drink")
        after_water = _bands(client)
        rounds.append({"action": "drink_water", "verb": "CONSUME",
                       "object": WATER, "bands_after": after_water,
                       "request_call_index": water_out["_request_call_index"]})
        _check(after_water.get("thirst") == "none",
               "drinking water did not relieve thirst", after_water)

        # 2) use the declared toilet -> toilet need relieved
        toilet_out = _run_round(cycle, cognition, clock, ("toilet", None),
                                "TOILET", "evt:toilet")
        after_toilet = _bands(client)
        rounds.append({"action": "use_declared_toilet", "verb": "TOILET",
                       "facility": TOILET, "bands_after": after_toilet,
                       "request_call_index": toilet_out["_request_call_index"]})
        _check(after_toilet.get("toilet") == "none",
               "the declared toilet did not relieve the need", after_toilet)

        # 3) eat a riceball -> hunger relieved
        food_out = _run_round(cycle, cognition, clock, ("consume", FOOD),
                              "CONSUME", "evt:eat")
        after_food = _bands(client)
        rounds.append({"action": "eat_riceball", "verb": "CONSUME",
                       "object": FOOD, "bands_after": after_food,
                       "request_call_index": food_out["_request_call_index"]})
        _check(after_food.get("hunger") == "none",
               "eating did not relieve hunger", after_food)

        # 4) sit, then 5) open a rest session (a session, not recovery)
        _run_round(cycle, cognition, clock, ("pose", "seated"), "POSE", "evt:sit")
        _run_round(cycle, cognition, clock, ("rest", "start"), "REST", "evt:rest-start")
        bodies = client.call("get_body_signals").data or {}
        recovery = bodies.get("recovery") or {}
        rounds.append({"action": "start_rest", "verb": "REST",
                       "recovery_state": recovery.get("state"),
                       "claim": "NOT_ASSERTED (a session is not recovery)"})
        _check(str(recovery.get("state")) == "active",
               "REST start did not open a session", recovery)

        # 6) close the session
        _run_round(cycle, cognition, clock, ("rest", "stop"), "REST", "evt:rest-stop")
        bodies = client.call("get_body_signals").data or {}
        rounds.append({"action": "stop_rest", "verb": "REST",
                       "recovery_state": (bodies.get("recovery") or {}).get("state")})

        # 7) a NO_ACTION round: legal, and it changes nothing
        before_no_action = _bands(client)
        receipts_before = len((wbs.SubstrateStore(wbs.substrate_path(world_home))
                               .load() or {}).get("consumptions") or {})
        out = _run_round(cycle, cognition, clock, ("no_action", None),
                         None, "evt:no-action")
        after_no_action = _bands(client)
        receipts_after = len((wbs.SubstrateStore(wbs.substrate_path(world_home))
                              .load() or {}).get("consumptions") or {})
        _check(out.get("decision") == "NO_ACTION", "round 7 was not NO_ACTION", out)
        _check(before_no_action == after_no_action,
               "a NO_ACTION round changed the body needs",
               {"before": before_no_action, "after": after_no_action})
        _check(receipts_before == receipts_after == 2,
               "the two consumptions were not exactly two receipts",
               {"before": receipts_before, "after": receipts_after})
        rounds.append({"action": "no_action", "bands_unchanged": True,
                       "consumptions": receipts_after})

        requests = cognition.requests
        for row in rounds:
            index = row.pop("request_call_index", None)
            row["needs_at_decision"] = (
                requests[index]["body_needs"] if isinstance(index, int)
                and 0 <= index < len(requests) else None)
            row["needs_in_next_request"] = (
                requests[index + 1]["body_needs"] if isinstance(index, int)
                and 0 <= index + 1 < len(requests) else None)
        summary["rounds"] = rounds
        summary["band_changes"] = {
            name: [summary["profile"]["initial_bands"].get(name),
                   after_food.get(name)]
            for name in bln.NEEDS
        }
        # The required "next round really sees the change" evidence: the request
        # material for the round *after* each care action.
        next_after_drink = rounds[0]["needs_in_next_request"] or {}
        next_after_toilet = rounds[1]["needs_in_next_request"] or {}
        next_after_eat = rounds[2]["needs_in_next_request"] or {}
        _check(next_after_drink.get("thirst") == "none",
               "the request after drinking still showed thirst", next_after_drink)
        _check(next_after_toilet.get("toilet") == "none",
               "the request after the toilet still showed the need", next_after_toilet)
        _check(next_after_eat.get("hunger") == "none",
               "the request after eating still showed hunger", next_after_eat)
        summary["needs_seen_by_next_request"] = {
            "before": summary["profile"]["initial_bands"],
            "after_drink": next_after_drink,
            "after_toilet": next_after_toilet,
            "after_eat": next_after_eat,
            "after_all": (requests[-1]["body_needs"] if requests else None),
            "world_block_after_all": (requests[-1]["world_body_needs"]
                                      if requests else None),
        }
        _check(bool(requests) and requests[-1]["body_needs"] is not None,
               "the next request material carried no body needs block",
               requests[-1] if requests else None)
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        if gateway is not None:
            try:
                gateway.stop()
            except Exception:
                pass
        try:
            services.stop()
        except Exception:
            pass
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return summary


class _env:
    def __init__(self, values: dict):
        self.values = values

    def __enter__(self):
        self.saved = {key: os.environ.get(key) for key in self.values}
        os.environ.update(self.values)
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def _restart_read(run_root: Path, summary: dict) -> dict:
    """Stop -> re-open the same root -> read persisted bands.  A read writes nothing."""

    data = odl.read_instance(run_root / "instance")
    world_home = Path(str(data["world_home"]))
    saved = {GATE_ENV: os.environ.get(GATE_ENV)}
    os.environ[GATE_ENV] = "canonical"
    gateway = None
    try:
        gateway = WorldGateway(world_home, world_id=data["world_id"],
                               environment=odl.instance_environment(run_root / "instance"))
        env = odl.instance_environment(run_root / "instance")
        env.update(gateway.environment())
        env[GATE_ENV] = "canonical"
        client = WorldBodyClient(environment=env)
        # Startup does the owner's own bounded catch-up (a legitimate lifecycle
        # write).  The read-only claim is about *reads*: compare files around the
        # GET calls only.
        before_files = {path.name: path.read_bytes()
                        for path in sorted(world_home.joinpath("data", "body").glob("*.json"))}
        after = _bands(client)
        snapshot_bands = _snapshot_bands(client)
        after_files = {path.name: path.read_bytes()
                       for path in sorted(world_home.joinpath("data", "body").glob("*.json"))}
        _check(after == summary["needs_seen_by_next_request"]["after_all"],
               "the same root re-read different persisted bands",
               {"expected": summary["needs_seen_by_next_request"]["after_all"],
                "after": after})
        _check(before_files == after_files, "a read rewrote a body file",
               {"before": list(before_files), "after": list(after_files)})
        summary["restart"] = {
            "bands": after, "snapshot_bands": snapshot_bands,
            "body_files_unchanged": True,
            "duplicate_effects": "none: the two consumptions stayed two receipts",
        }
        return summary
    finally:
        if gateway is not None:
            try:
                gateway.stop()
            except Exception:
                pass
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


LIVE_TICK_SECONDS = 60.0
LIVE_TICKS = 20


def _live_window_scenario(run_root: Path) -> dict:
    """A real service lifecycle: grow -> clean stop -> re-open after a gap.

    The ten-hour gap is injected into the same service clock the demo passes
    in; it is a mechanical simulation, not a real ten-hour wait.  What is
    checked is the rule: only ticks this service witnessed may grow the needs,
    and the downtime between two processes may not be backfilled.
    """

    import world_body_config as wcfg
    import world_body_service as wbsvc

    data = odl.read_instance(run_root / "instance")
    world_home = Path(str(data["world_home"]))
    instance = run_root / "instance"
    environment = dict(odl.instance_environment(instance))
    moment = {"now": datetime.now(timezone.utc)}

    def clock():
        return moment["now"]

    def open_service():
        config = wcfg.load_config(
            dict(environment), runtime_root=WORLD_RUNTIME_ROOT,
            world_home=world_home, runtime_mode="canonical",
            authority="production", world_id=data["world_id"])
        service = wbsvc.WorldBodyService(
            config, clock=clock, tick_seconds=LIVE_TICK_SECONDS,
            instance_id="body-lived-needs-live")
        service.startup()
        return service

    def block(service):
        return dict((service.body_state or {}).get("lived_needs") or {})

    def levels(service):
        return dict(block(service).get("levels") or {})

    first = open_service()
    try:
        anchor = levels(first)
        for _ in range(LIVE_TICKS):
            moment["now"] = moment["now"] + timedelta(seconds=LIVE_TICK_SECONDS)
            first.tick()
        grown = levels(first)
        _check(grown["thirst"] > anchor["thirst"],
               "a witnessed tick window did not grow the needs",
               {"anchor": anchor, "grown": grown})
        stopped = grown
    finally:
        first.shutdown()

    # Clean stop on disk; the same root is re-opened after a *simulated* gap.
    moment["now"] = moment["now"] + timedelta(hours=10)
    second = open_service()
    try:
        after_restart = levels(second)
        _check(after_restart == stopped,
               "the restart backfilled the offline gap",
               {"before_stop": stopped, "after_restart": after_restart})
        skipped = float(block(second).get("offline_skipped_seconds") or 0.0)
        _check(skipped >= 10 * 3600,
               "the offline gap was not recorded as skipped/unknown",
               {"offline_skipped_seconds": skipped})
        for _ in range(LIVE_TICKS):
            moment["now"] = moment["now"] + timedelta(seconds=LIVE_TICK_SECONDS)
            second.tick()
        regrown = levels(second)
        _check(regrown["thirst"] > after_restart["thirst"],
               "needs did not resume growing after the restart",
               {"after_restart": after_restart, "regrown": regrown})
    finally:
        second.shutdown()

    return {
        "clock": "injected; the ten-hour gap is mechanical, not a real wait",
        "tick_seconds": LIVE_TICK_SECONDS,
        "ticks_per_window": LIVE_TICKS,
        "levels_after_start_anchor": anchor,
        "levels_after_first_window": grown,
        "levels_after_restart": after_restart,
        "levels_after_second_window": regrown,
        "offline_skipped_seconds": round(skipped, 3),
        "downtime_was_not_backfilled": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-last", action="store_true",
                        help="read the last saved summary; start nothing, act nothing")
    args = parser.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    pointer = SCRATCH / "LAST.json"
    if args.reuse_last:
        if not pointer.exists():
            print("DEMO_REUSE: no previous run")
            return 1
        summary = json.loads(pointer.read_text(encoding="utf-8"))
        print("DEMO_REUSE_OK")
        print(json.dumps(_public(summary), ensure_ascii=False, indent=1))
        return 0

    stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    run_root = SCRATCH / stamp
    run_root.mkdir(parents=True, exist_ok=False)
    summary = run(run_root)
    summary = _restart_read(run_root, summary)
    summary["live_window"] = _live_window_scenario(run_root)
    summary["ok"] = True
    summary["finished_at"] = _iso(datetime.now(timezone.utc))
    (run_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=1),
        encoding="utf-8")
    pointer.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=1),
                       encoding="utf-8")
    print("DEMO_OK")
    print(json.dumps(_public(summary), ensure_ascii=False, indent=1))
    return 0


def _public(summary: dict) -> dict:
    """Only status/action/result/difference; no private text, reasoning or env."""

    return {
        "root": summary.get("root"),
        "scripted_decision": summary.get("scripted_decision"),
        "product_model_requests": summary.get("product_model_requests"),
        "synthetic_initial_profile": summary.get("synthetic_initial_profile"),
        "before_bands": (summary.get("before") or {}).get("body_bands"),
        "rounds": [
            {key: row.get(key) for key in
             ("action", "verb", "object", "facility", "bands_after",
              "recovery_state", "claim", "bands_unchanged", "needs_in_next_request")}
            for row in summary.get("rounds") or []
        ],
        "needs_seen_by_next_request": summary.get("needs_seen_by_next_request"),
        "restart": summary.get("restart"),
        "live_window": summary.get("live_window"),
        "limitations": summary.get("limitations"),
    }


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # a failed setup is a failed demo, never silent
        print("DEMO_FAIL: %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(1)
