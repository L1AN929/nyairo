#!/usr/bin/env python3
"""零模型冒烟：多段旅程的每一段都经 World 自己的正式授权入口。

做什么
======
在一个全新的临时根（``/tmp/nyairo-journey-auth-*``）里生成一个最小的真实
World 实例，在**本脚本进程内**起一个真实的 gateway.sock 服务线程（真实 socket
传输与真实 registry；不是独立 owner 子进程），然后用**同一个正式提交口**：

1. 接受一个多段旅程，提交第一段（真实 Intent Executor → gateway → Resolver）；
2. 结算第一段到达，提交第二段（必须是新的 execution id + 新的一次性授权）；
3. 关掉 mutation gate 后结算第二段：已到达的段合法结算，第三段不得出发。

不做什么
========
不调用任何模型、不启动 HTTP/浏览器服务、不读写任何既有实例、不删数据或 socket、
不杀别人的进程。收尾只停本脚本自己起的服务，临时根保留（便于父级复核）。

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

WORLD_RUNTIME_ROOT = ROOT / "components" / "world"
JOURNEY_RELATIVE = ("data", "world", "world_journey.json")
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")


class _WorldService:
    """本脚本自己的真实 World 服务（线程 + 真 socket）。"""

    def __init__(self, home: Path, environment: dict, world_id: str) -> None:
        self.home = Path(home)
        self.config = wcfg.load_config(
            dict(environment), runtime_root=WORLD_RUNTIME_ROOT, world_home=self.home,
            runtime_mode="canonical", authority="production", world_id=world_id)
        self.service = wbsvc.WorldBodyService(
            self.config, tick_seconds=3600.0, instance_id="journey-auth-smoke-%d" % os.getpid())
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


def _position(home: Path, environment: dict) -> str:
    state = json.loads(
        home.joinpath("data", "world", "world_state.json").read_text(encoding="utf-8"))
    return str((state.get("location") or {}).get("place_id"))


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
    root = Path(tempfile.mkdtemp(prefix="nyairo-journey-auth-"))
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
        "WORLD_BODY_INTENT_MUTATION_GATE": "canonical",
    })
    wrd.reset_cache()
    world = wrd.active_world(environment)
    environment["CHIYO_WORLD_ID"] = world.world_id

    service = _WorldService(home, environment, world.world_id)
    summary: dict = {"root": str(root), "world_id": world.world_id, "checks": {}}
    ok = True
    previous_gate = os.environ.get("WORLD_BODY_INTENT_MUTATION_GATE")
    try:
        environment["WORLD_BODY_SOCKET"] = str(service.socket_path)
        os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = "canonical"
        port = journey_authorization.default_submit_port(environment=environment)
        origin = _position(home, environment)
        target, route = _longest_route(world, origin)
        summary["origin"] = origin
        summary["target"] = target
        summary["route"] = route
        summary["checks"]["route_has_at_least_two_segments"] = len(route) >= 2
        ok = ok and len(route) >= 2

        accepted = world_journey.accept_journey(
            target=target, hermes_home=home, environment=environment, submit_port=port)
        stored = _journey(home)["active"]
        first = stored["segments"][0]
        summary["checks"]["first_segment_formal"] = bool(
            accepted.get("status") == "started"
            and port.submissions == 1
            and port.last_intent
            and first["execution_id"] == port.last_intent["execution_id"]
            and len(_travels(home)) == 1)
        ok = ok and summary["checks"]["first_segment_formal"]

        arrival = str(first["expected_arrival_at"]).replace("Z", "+00:00")
        second = world_journey.advance_journey(
            hermes_home=home, environment=environment, now=arrival, submit_port=port)
        stored = _journey(home)["active"]
        seg2 = stored["segments"][1]
        summary["checks"]["second_segment_new_authorization"] = bool(
            second.get("status") in {"started", "in_transit"}
            and port.submissions == 2
            and seg2["execution_id"] == port.last_intent["execution_id"]
            and seg2["execution_id"] != first["execution_id"]
            and len(_travels(home)) == 2)
        ok = ok and summary["checks"]["second_segment_new_authorization"]

        # gate 关闭：已到达的第二段合法结算，第三段不得出发
        os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = "off"
        arrival2 = str(seg2["expected_arrival_at"]).replace("Z", "+00:00")
        travels_before = len(_travels(home))
        gated = world_journey.advance_journey(
            hermes_home=home, environment=environment, now=arrival2, submit_port=port)
        stored = _journey(home)["active"]
        summary["checks"]["gate_off_blocks_next_segment"] = bool(
            gated.get("status") == "blocked"
            and gated.get("reason_code") == "GATE_OFF"
            and len(_travels(home)) == travels_before
            and port.submissions == 2
            and stored["segments"][1]["status"] == "confirmed")
        ok = ok and summary["checks"]["gate_off_blocks_next_segment"]

        summary["submissions"] = port.submissions
        summary["journey_status"] = stored["status"]
        summary["journey_reason"] = stored["error"]
        summary["travel_execution_ids"] = [row.get("execution_id") for row in _travels(home)]
        summary["position"] = _position(home, environment)
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 - 冒烟要把失败原因完整交出去
        summary["error"] = "%s: %s" % (type(exc).__name__, exc)
        return 1
    finally:
        if previous_gate is None:
            os.environ.pop("WORLD_BODY_INTENT_MUTATION_GATE", None)
        else:
            os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = previous_gate
        service.stop()
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
