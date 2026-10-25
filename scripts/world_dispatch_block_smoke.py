#!/usr/bin/env python3
"""零模型冒烟：World 出门被闸门挡住时"明确没发"，打开后真走真结算。

做什么
======
在一个全新的临时根（``/tmp/nyairo-world-dispatch-*``）里生成一个最小的真实
World 实例，在**本脚本进程内**起真实 gateway.sock（真实 socket 传输与真实
registry；不拉起别的实例、不开 HTTP），然后：

1. gate=off：经 Life 自己的 World bridge 提交一个 POSE。真实 Intent Executor
   在接触 owner 之前拒绝，结果必须是 ``GATE_OFF`` + 明确预提交失败；
   registry 零票、world_state 零变化、零 travel。
2. gate=canonical：同一个 POSE 真提交，世界姿态变化且恰好消费一张票。
3. gate=canonical：接受一条多段旅程，结算第一段后自动提交第二段；两段各自
   新 execution id、各自一张已消费票、两条 travel。

不做什么
========
不调用任何模型、不启动 HTTP/浏览器、不读写任何既有实例、不删数据或 socket、
不杀别人的进程。gate 只在本脚本窗口内设置，finally 恢复原值；临时根保留。

退出码
======
0 = 全部断言成立；1 = 任一断言失败或服务起不来。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "worldgen")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import components.worldgen.schema as worldgen_schema  # noqa: E402
from components.worldgen.generator import generate_world  # noqa: E402
from components.worldgen.instance import init_instance  # noqa: E402

import world_body_config as wcfg  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_runtime_data as wrd  # noqa: E402
from chiyo_bundle import journey_authorization, world_journey  # noqa: E402

import action_reality_ledger as ar0  # noqa: E402
import fast_day  # noqa: E402
from world_action_bridge_adapter import WorldBridgeResolver  # noqa: E402

WORLD_RUNTIME_ROOT = ROOT / "components" / "world"
GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
POSE_TARGET = "bodyact:pose:seated:north"
JOURNEY_RELATIVE = ("data", "world", "world_journey.json")
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")
REGISTRY_NAME = "action_authorizations.json"


class _WorldService:
    """本脚本自己的真实 World 服务（线程 + 真 socket）。"""

    def __init__(self, home: Path, environment: dict, world_id: str) -> None:
        self.home = Path(home)
        self.config = wcfg.load_config(
            dict(environment), runtime_root=WORLD_RUNTIME_ROOT, world_home=self.home,
            runtime_mode="canonical", authority="production", world_id=world_id)
        self.service = wbsvc.WorldBodyService(
            self.config, tick_seconds=3600.0,
            instance_id="world-dispatch-smoke-%d" % os.getpid())
        self.startup_report = self.service.startup()
        self.socket_path = self.service.gateway_socket_path
        self._thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 600.0}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not Path(self.socket_path).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("World gateway socket never appeared: %s"
                                   % self.socket_path)
            time.sleep(0.02)

    def stop(self) -> None:
        try:
            self.service.shutdown()
        except Exception:
            pass
        self._thread.join(timeout=5)


def _journey(home: Path) -> dict:
    return json.loads(home.joinpath(*JOURNEY_RELATIVE).read_text(encoding="utf-8"))


def _travels(home: Path) -> list:
    path = home.joinpath(*TRAVEL_RELATIVE)
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("travels") or [])


def _state(home: Path) -> dict:
    return json.loads(home.joinpath("data", "world", "world_state.json")
                      .read_text(encoding="utf-8"))


def _authorizations(home: Path) -> list:
    path = home / "run" / REGISTRY_NAME
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("records") or [])


def _position(home: Path) -> str:
    return str((_state(home).get("location") or {}).get("place_id"))


def _pose(environment: dict) -> dict:
    import world_body_client as wbc

    result = wbc.WorldBodyClient(environment=environment).get_snapshot()
    if result.outcome != wbc.OK or not isinstance(result.data, dict):
        raise RuntimeError("snapshot read failed: %r / %r"
                           % (result.outcome, result.reason))
    return {"pose": result.data.get("pose"),
            "orientation": result.data.get("orientation"),
            "world_revision": result.data.get("world_revision")}


def _longest_route(world, origin: str):
    seen, frontier = {origin}, [origin]
    while frontier:
        node = frontier.pop()
        for nxt in world.reachable_of(node):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    target = max(seen, key=lambda dest: len(world.route_to(origin, dest) or [origin]))
    return target, list(world.route_to(origin, target) or [])


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nyairo-world-dispatch-"))
    package_dir = root / "package"
    package_dir.mkdir()
    worldgen_schema.write_package(
        package_dir, generate_world(seed=929711, theme="coastal_town"))
    home = root / "instance"
    init_instance(package_dir, home)

    environment = dict(os.environ)
    environment.update({
        "CHIYO_WORLD_PACKAGE": str(package_dir),
        "CHIYO_WORLD_HOME": str(home),
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        "NYAIRO_WORLD_BRIDGE_DIR": str(ROOT / "components" / "world" / "service" / "bridge"),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
    })
    wrd.reset_cache()
    world = wrd.active_world(environment)
    environment["CHIYO_WORLD_ID"] = world.world_id

    service = _WorldService(home, environment, world.world_id)
    summary: dict = {"root": str(root), "world_id": world.world_id, "checks": {}}
    ok = True
    previous_gate = os.environ.get(GATE_ENV)
    try:
        environment["WORLD_BODY_SOCKET"] = str(service.socket_path)

        # 1. gate off：解析到的值交给同一个真实 bridge，必须在 owner 之前停下。
        resolved = fast_day.resolve_world_intent_gate({GATE_ENV: "off"})
        summary["gate_off_resolved"] = resolved
        os.environ[GATE_ENV] = str(resolved["value"])
        delegate = WorldBridgeResolver(environment=environment)
        before_state = _state(home)
        off = delegate.resolve_action(
            idempotency_key="idem:smoke:pose:off", verb="POSE",
            target=POSE_TARGET, expected_dependencies={})
        summary["gate_off_result"] = {
            "status": off.get("status"), "code": off.get("code"),
            "transport_result": off.get("transport_result"),
        }
        summary["checks"]["gate_off_not_sent"] = bool(
            resolved.get("value") == "off"
            and off.get("status") == "rejected"
            and off.get("code") == "GATE_OFF"
            and off.get("transport_result") == ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT
            and _authorizations(home) == []
            and _travels(home) == []
            and _state(home) == before_state)
        ok = ok and summary["checks"]["gate_off_not_sent"]

        # 2. gate canonical：同一真实链真提交，恰好一张票被消费。
        canonical = fast_day.resolve_world_intent_gate({GATE_ENV: "canonical"})
        summary["gate_canonical_resolved"] = canonical
        os.environ[GATE_ENV] = str(canonical["value"])
        before_pose = _pose(environment)
        on = WorldBridgeResolver(environment=environment).resolve_action(
            idempotency_key="idem:smoke:pose:on", verb="POSE",
            target=POSE_TARGET, expected_dependencies={})
        pose_records = _authorizations(home)
        after_pose = _pose(environment)
        summary["canonical_pose_result"] = {
            "status": on.get("status"), "code": on.get("code"),
            "before": before_pose, "after": after_pose,
        }
        summary["checks"]["canonical_pose_committed"] = bool(
            canonical.get("value") == "canonical"
            and on.get("status") == "committed"
            and (after_pose.get("pose"), after_pose.get("orientation"))
            != (before_pose.get("pose"), before_pose.get("orientation"))
            and len(pose_records) == 1
            and str(pose_records[0].get("state")).upper() == "CONSUMED")
        ok = ok and summary["checks"]["canonical_pose_committed"]

        # 3. canonical 多段：每段各自新票、各自消费；第二段由真实结算发起。
        port = journey_authorization.default_submit_port(environment=environment)
        origin = _position(home)
        target, route = _longest_route(world, origin)
        summary.update({"origin": origin, "target": target, "route": route})
        accepted = world_journey.accept_journey(
            target=target, hermes_home=home, environment=environment, submit_port=port)
        stored = _journey(home)["active"]
        first = stored["segments"][0]
        arrival = str(first["expected_arrival_at"]).replace("Z", "+00:00")
        settled = world_journey.advance_journey(
            hermes_home=home, environment=environment, now=arrival, submit_port=port)
        stored = _journey(home)["active"]
        second = stored["segments"][1]
        tickets = [record for record in _authorizations(home)
                   if record.get("execution_id") in
                   {first["execution_id"], second["execution_id"]}]
        summary["journey"] = {
            "accepted": accepted.get("status"), "settled": settled.get("status"),
            "first_execution": first["execution_id"],
            "second_execution": second["execution_id"],
            "submissions": port.submissions,
        }
        summary["checks"]["multi_segment_new_ticket_each"] = bool(
            len(route) >= 2
            and accepted.get("status") == "started"
            and settled.get("status") in {"started", "in_transit"}
            and second["status"] == "started"
            and second["execution_id"] != first["execution_id"]
            and port.submissions == 2
            and len(_travels(home)) == 2
            and len(tickets) == 2
            and all(str(record.get("state")).upper() == "CONSUMED"
                    for record in tickets))
        ok = ok and summary["checks"]["multi_segment_new_ticket_each"]

        summary["position"] = _position(home)
        summary["consumed_tickets"] = sum(
            1 for record in _authorizations(home)
            if str(record.get("state")).upper() == "CONSUMED")
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 - 冒烟要把失败原因完整交出去
        summary["error"] = "%s: %s" % (type(exc).__name__, exc)
        return 1
    finally:
        if previous_gate is None:
            os.environ.pop(GATE_ENV, None)
        else:
            os.environ[GATE_ENV] = previous_gate
        service.stop()
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
