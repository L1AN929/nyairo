#!/usr/bin/env python3
"""零模型冒烟：正式 ``execute_activity_step`` 的嵌套返回下，"明确没发"仍是拒绝。

做什么
======
在全新的 ``/tmp`` 根里，用**真实** Supply owner、**真实** World gateway/registry 和
**真实** ``build_production_life_runtime`` + ``assemble`` 装配 Life cycle。Decision /
内容 / 观察候选是脚本替身，但：

1. gate=off：本轮开始时先按 canonical 取到真实 ``action_intent.build_action_space``
   的候选（gate 关闭时它本来不提供 MOVE），随后把 gate 关掉再走**正式**
   ``AutonomousLifeRunner.wake_on_event`` → ``evaluate_once`` → AG-2
   ``execute_activity_step``（返回真实嵌套形状）→ cycle 分类。结果必须是
   ``STEP_REFUSED`` / wake ``BLOCKED``：Action 仍 ``PREPARED``、attempt
   ``PRE_SUBMIT_FAILED``、零回执、零票、零 travel、世界状态不变、owner 证据
   ``owner_version_created=False``（不是 ``None``），且不在 UNKNOWN 对账队列里。
2. gate=canonical：另一份全新实例走同一条正式链，必须真出发（``IN_TRANSIT``）、
   恰好消费一张票、写一条 travel；不被误判成拒绝，owner 证据保持 ``None``。

不做什么
========
不调用任何产品模型、不起 HTTP/浏览器、不读写任何既有实例、不改全局默认 gate
（只在脚本进程内设置并在 finally 恢复）、不删数据。Decision/内容替身，候选来自
真实 action space；临时根保留。

退出码
======
0 = 两组断言都成立；1 = 任一组失败或服务起不来。
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import components.worldgen.schema as worldgen_schema  # noqa: E402
from components.worldgen.generator import generate_world  # noqa: E402

import action_reality_ledger as ar0  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_runner as runner  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
from world_journey_action_adapter import (  # noqa: E402
    JourneyWorldResolver, RealJourneyWorldActionAdapter)

from tests.test_one_day_life import (  # noqa: E402
    ENV_GATES, Services, StubCognition, StubObservation, StubWriter, WorldGateway)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")
REGISTRY_NAME = "action_authorizations.json"
SCRATCH = Path(os.environ.get("NYAIRO_WORLD_REFUSAL_SHAPE_SMOKE_ROOT")
               or "/tmp/nyairo-world-refusal-shape-smoke")


class _EnvRestore:
    """在脚本自己的进程里设置环境，退出时原样恢复（不改外部默认）。"""

    def __init__(self, values: dict) -> None:
        self.values = {str(key): str(value) for key, value in values.items()}
        self.saved: dict = {}

    def __enter__(self):
        for key in self.values:
            self.saved[key] = os.environ.get(key)
        os.environ.update(self.values)
        return self

    def __exit__(self, *_exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("SMOKE_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:4000])
    raise SystemExit(1)


def _world_state_bytes(home: Path) -> bytes:
    return (home / "data" / "world" / "world_state.json").read_bytes()


def _travels(home: Path) -> list:
    path = home.joinpath(*TRAVEL_RELATIVE)
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("travels") or [])


def _authorizations(env) -> list:
    path = Path(env["WORLD_BODY_SOCKET"]).parent / REGISTRY_NAME
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("records") or [])


def _build(root: Path, gate: str) -> dict:
    """一份全新隔离实例 + 真实 owner 服务 + 真实 production runtime + 正式 runner。"""

    package_dir = root / "package"
    package_dir.mkdir(parents=True)
    worldgen_schema.write_package(
        package_dir, generate_world(seed=929711, theme="coastal_town"))

    services = Services(root / "services")
    instance = root / "instance"
    runtime = None
    gateway = None
    try:
        with _EnvRestore({**ENV_GATES, GATE_ENV: "canonical"}):
            prepared = odl.prepare(
                instance=instance, supply_socket=services.supply_path,
                governance_socket=services.gov_path,
                subject="smoke:world-refusal-shape",
                title="World refusal result shape", world_package=package_dir)
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        env = odl.instance_environment(instance)
        gateway = WorldGateway(world_home, world_id=data["world_id"],
                               environment=env)
        env.update(gateway.environment())

        state_root = odl.life_state_root(instance)
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=StubCognition("move"),
            install_root=instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        observation = StubObservation(env)
        with _EnvRestore({GATE_ENV: "canonical"}):
            opportunities = observation()
        _check(bool(opportunities), "the real action space offered no move opportunity")
        provider = lambda: copy.deepcopy(opportunities)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root,
            world_provider=provider, writer=StubWriter(), enabled=True)
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]
        life_runner = runner.AutonomousLifeRunner(cycle, env=env, enabled=True)

        with _EnvRestore({GATE_ENV: gate}):
            world_before = _world_state_bytes(world_home)
            record = life_runner.wake_on_event(
                {"kind": runner.EVENT_WORLD_OPPORTUNITY,
                 "ref": "evt:world-refusal-shape:%s" % gate})
        return {
            "root": str(root), "gate": gate, "runtime": runtime,
            "services": services, "gateway": gateway, "cycle": cycle,
            "runner": life_runner, "env": env, "world_home": world_home,
            "record": record, "world_before": world_before,
        }
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        if gateway is not None:
            gateway.stop()
        services.stop()
        raise


def _close(case: dict) -> None:
    try:
        case["runtime"].close()
    except Exception:
        pass
    case["gateway"].stop()
    case["services"].stop()


def _refusal_summary(case: dict) -> dict:
    record = case["record"]
    runtime = case["runtime"]
    action_id = str(record.action_id or "")
    action = runtime.action_read.get_action(action_id) if action_id else None
    attempts = runtime.action_read.list_attempts(action_id) if action_id else []
    receipts = runtime.action_read.list_receipts(action_id) if action_id else []
    summary = {
        "gate": case["gate"],
        "wake_status": record.status,
        "wake_class": record.status_class,
        "outcome": record.outcome,
        "action_status": record.action_status,
        "action_found": action is not None,
        "attempts": len(attempts),
        "attempt_status": (attempts[0].get("status") if attempts else None),
        "attempt_transport": (attempts[0].get("transport_result") if attempts else None),
        "attempt_error_class": (attempts[0].get("error_class") if attempts else None),
        "receipts": len(receipts),
        "tickets": len(_authorizations(case["env"])),
        "travels": len(_travels(case["world_home"])),
        "pending_unknown": len(case["runner"].pending_unknown_actions()),
        "owner_version_created": (record.extra or {}).get("owner_version_created"),
        "world_state_unchanged": _world_state_bytes(case["world_home"]) == case["world_before"],
    }
    _check(record.status == runner.WAKE_BLOCKED, "wake is not BLOCKED", summary)
    _check(record.status_class == "blocked", "wake class is not blocked", summary)
    _check(record.outcome == alc.OUTCOME_STEP_REFUSED, "not a refusal", summary)
    _check(record.action_status == "PREPARED", "Action is not PREPARED", summary)
    _check(action is not None and action.get("status") == "PREPARED",
           "the stored Action is not PREPARED", summary)
    _check(len(attempts) == 1, "expected exactly one attempt", summary)
    _check(attempts[0].get("status") == "PRE_SUBMIT_FAILED",
           "attempt is not PRE_SUBMIT_FAILED", summary)
    _check(attempts[0].get("transport_result")
           == ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
           "attempt is not a definite pre-submission failure", summary)
    _check(attempts[0].get("error_class") == "GATE_OFF",
           "attempt lost GATE_OFF", summary)
    _check(receipts == [], "a refusal produced a receipt", summary)
    _check(summary["tickets"] == 0, "a refusal minted a ticket", summary)
    _check(summary["travels"] == 0, "a refusal wrote travel", summary)
    _check(summary["pending_unknown"] == 0,
           "a PREPARED refusal entered the UNKNOWN queue", summary)
    _check(summary["owner_version_created"] is False,
           "owner evidence is not False", summary)
    _check(summary["world_state_unchanged"], "world state changed", summary)
    return summary


def _success_summary(case: dict) -> dict:
    record = case["record"]
    records = _authorizations(case["env"])
    summary = {
        "gate": case["gate"],
        "wake_status": record.status,
        "outcome": record.outcome,
        "action_status": record.action_status,
        "tickets": len(records),
        "ticket_states": [str(row.get("state")).upper() for row in records],
        "travels": len(_travels(case["world_home"])),
        "owner_version_created": (record.extra or {}).get("owner_version_created"),
        "journey_id": ((case["cycle"].last_outcome_journey or {}).get("journey_id")
                       if getattr(case["cycle"], "last_outcome_journey", None) else None),
    }
    _check(record.outcome == alc.OUTCOME_IN_TRANSIT, "canonical round did not depart", summary)
    _check(record.outcome != alc.OUTCOME_STEP_REFUSED, "departure was misread as refusal",
           summary)
    _check(record.status == runner.WAKE_IN_TRANSIT, "wake is not IN_TRANSIT", summary)
    _check(record.action_status == "SUCCEEDED", "Action did not succeed", summary)
    _check(summary["tickets"] == 1, "expected exactly one consumed ticket", summary)
    _check(summary["ticket_states"] == ["CONSUMED"], "ticket was not consumed", summary)
    _check(summary["travels"] == 1, "expected exactly one travel record", summary)
    _check(summary["owner_version_created"] is None,
           "a world departure invented artifact-version evidence", summary)
    return summary


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = "%d" % os.getpid()
    cases: list[dict] = []
    summary: dict = {"ok": False, "cases": {}}
    try:
        off = _build(SCRATCH / ("off-%s" % stamp), "off")
        cases.append(off)
        summary["cases"]["off"] = _refusal_summary(off)

        on = _build(SCRATCH / ("canonical-%s" % stamp), "canonical")
        cases.append(on)
        summary["cases"]["canonical"] = _success_summary(on)
        summary["ok"] = True
        summary["root"] = str(SCRATCH)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        for case in cases:
            _close(case)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # a failed setup is a failed smoke, never silent
        print("SMOKE_FAIL: %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(1)
