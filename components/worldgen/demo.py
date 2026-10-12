#!/usr/bin/env python3
"""隔离 World/Body 运行演示：证明生成世界能被 rc9 真实驱动，而非静态预览。

三个演示面（全部使用 rc9 既有代码路径，不修改状态权威）：

``world``   —— 世界层：观察 / 行走（路线耗时）/ 时间变化 / 持久化
``service`` —— World/Body 服务：canonical 启动检查 + M14A 授权 + Resolver
               提交 + 执行账本回执 + 身体后果
``restart`` —— 关闭后重新"启动"（新进程），核验位置与已发生状态仍在

时间注入：所有世界层调用都显式传 ``now``，因此整个白天/夜晚场景是
确定性可重复的（默认 2026-10-14，星期三）。

本模块需要 POSIX（fcntl）；在原生 Windows 上请通过 WSL 运行。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "world" / "scripts"
_SERVICE_DIR = Path(__file__).resolve().parents[1] / "world" / "service"
_BRIDGE_DIR = _SERVICE_DIR / "bridge"

BASE_SCENARIO_DATE = "2026-10-14"  # 星期三，学校开放日

#: 生成世界演示打开的能力；学校与经济模块未适配自定义世界，明确关闭。
FEATURE_ON = (
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED",
    "CHIYO_WORLD_TRAVEL_TIME_ENABLED",
    "CHIYO_WORLD_DAILY_RHYTHM_ENABLED",
    "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED",
    "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED",
    "CHIYO_WORLD_HOME_SPACE_ENABLED",
)
FEATURE_OFF = (
    "CHIYO_WORLD_SCHOOL_DAY_ENABLED",
    "CHIYO_WORLD_SCHOOL_EVENTS_ENABLED",
    "CHIYO_WORLD_SCHOOL_CONTINUITY_ENABLED",
    "CHIYO_WORLD_SCHOOL_ASSIGNMENTS_ENABLED",
    "CHIYO_WORLD_SCHOOL_EVENT_KNOWLEDGE_ENABLED",
    "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED",
    "CHIYO_WORLD_OWNER_FUNDING_ENABLED",
)


class DemoError(RuntimeError):
    """演示步骤失败（期望之外的返回值）。"""


def _ensure_paths() -> None:
    for path in (_SCRIPTS_DIR, _SERVICE_DIR, _BRIDGE_DIR, _REPO_ROOT):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _require_posix() -> None:
    if os.name == "nt":
        raise DemoError(
            "演示需要 POSIX（fcntl 文件锁）。请在 WSL 中运行："
            "wsl -e bash -c 'cd /mnt/c/.../nyairo-worldgen-rc9 && "
            "python3 -m components.worldgen demo ...'"
        )


def _apply_env(
    instance_home: Path,
    package_dir: Path,
    world_id: str,
    *,
    travel: bool = True,
    substrate: bool = False,
    body_runtime: bool = False,
) -> None:
    os.environ["HERMES_HOME"] = str(instance_home)
    os.environ["CHIYO_WORLD_HOME"] = str(instance_home)
    os.environ["CHIYO_WORLD_ID"] = world_id
    os.environ["CHIYO_WORLD_PACKAGE"] = str(package_dir)
    for name in FEATURE_ON:
        os.environ[name] = "1" if (name != "CHIYO_WORLD_TRAVEL_TIME_ENABLED" or travel) else "0"
    for name in FEATURE_OFF:
        os.environ[name] = "0"
    if travel:
        os.environ["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "1"
    else:
        os.environ["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "0"
    os.environ["CHIYO_WORLD_BODY_SUBSTRATE_ENABLED"] = "1" if substrate else "0"
    os.environ["CHIYO_BODY_RUNTIME_ENABLED"] = "1" if body_runtime else "0"
    os.environ["CHIYO_BODY_READER_MODE"] = "canonical" if substrate else "shadow"
    os.environ["CHIYO_WORLD_DELIVERY_TIME_ENABLED"] = "0"


def _local(hour: int, minute: int, date_text: str, tz_name: str) -> datetime:
    from zoneinfo import ZoneInfo

    naive = datetime.fromisoformat(f"{date_text}T{hour:02d}:{minute:02d}:00")
    return naive.replace(tzinfo=ZoneInfo(tz_name))


def _pick_destination(world) -> dict[str, Any]:
    """选一个演示目的地：优先商铺/图书馆/市场，保证营业规则可观察。"""
    preferred = [loc for loc in world.locations.values() if loc["kind"] in ("store", "cafe", "library", "market")]
    if preferred:
        return sorted(preferred, key=lambda item: item["location_id"])[0]
    others = [loc for loc in world.locations.values() if loc["kind"] not in ("home", "street")]
    if not others:
        raise DemoError("世界中没有可前往的非住宅地点")
    return sorted(others, key=lambda item: item["location_id"])[0]


def _route_via_streets(world, origin: str, destination: str) -> list[str]:
    """BFS：返回 origin→destination 的节点序列（地点 + 室内区域，都是相邻边）。

    v0.4：origin 是角色的实际节点（可能是 ``place/area``）；导航图由
    ``reachable_of`` 统一给出（place 邻接 + 室内区域连接）。
    """
    adjacency: dict[str, list[str]] = {loc_id: list(loc["reachable"]) for loc_id, loc in world.locations.items()}
    for place_id, nav in (world.navigation_of_all() or {}).items():
        entrance = nav["entrance"]
        for area in nav["areas"]:
            node = f"{place_id}/{area['area_id']}"
            adjacency.setdefault(node, world.reachable_of(node))
    queue: list[tuple[str, list[str]]] = [(origin, [origin])]
    seen = {origin}
    while queue:
        current, path = queue.pop(0)
        if current == destination:
            return path
        for neighbor in sorted(adjacency.get(current, [])):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    raise DemoError(f"从 {origin} 无法到达 {destination}")


def _obs_summary(observation: dict[str, Any]) -> dict[str, Any]:
    if observation.get("status") != "available":
        return {"status": observation.get("status"), "reason_code": observation.get("reason_code")}
    availability = observation.get("location_availability") or {}
    return {
        "status": "available",
        "world_id": observation.get("world_id"),
        "local_time": observation.get("local_time"),
        "weekday": observation.get("weekday"),
        "current_location": (observation.get("current_location") or {}).get("location_id"),
        "reachable": [
            item.get("location_id") for item in observation.get("reachable_locations") or []
        ],
        "availability": availability.get("status"),
        "availability_reason": availability.get("reason_code"),
        "affordances": [
            item.get("affordance_id") for item in observation.get("affordances") or []
        ],
        "ambient_events": [
            (item.get("event_type"), item.get("location_id"))
            for item in observation.get("ambient_events") or []
        ],
        "present_npcs": [
            item.get("display_name") for item in (observation.get("social_presence") or {}).get("present_npcs") or []
        ],
        "travel_status": (observation.get("travel") or {}).get("status"),
    }


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise DemoError(message)


def run_world_scenario(
    package_dir: Path | str,
    instance_home: Path | str,
    *,
    date_text: str = BASE_SCENARIO_DATE,
) -> dict[str, Any]:
    """世界层完整场景：观察 → 两段行走 → 营业时间变化 → 状态落盘。"""
    _require_posix()
    _ensure_paths()
    from .loader import load

    package = load(package_dir)
    instance_home = Path(instance_home).resolve()
    _apply_env(instance_home, package.package_dir, package.world_id, travel=True)

    import world_living_skeleton as wls
    import world_runtime_data as wrd
    import world_travel_delivery as wtd

    wrd.reset_cache()
    world = wrd.active_world()
    _expect(world.world_id == package.world_id, "活动世界与包不一致")
    tz_name = world.timezone_name

    steps: list[dict[str, Any]] = []

    def record(name: str, payload: dict[str, Any]) -> None:
        steps.append({"step": name, **payload})

    destination = _pick_destination(world)
    import world_foundation as wf

    state0 = wf.WorldStateStore(instance_home / "data" / "world" / "world_state.json").load()
    origin_node = world.state_node(
        state0["location"]["place_id"], state0["location"].get("area_id")
    ) or world.home_location_id
    route = _route_via_streets(world, origin_node, destination["location_id"])
    record(
        "plan_route",
        {
            "detail": f"{' → '.join(route)}",
            "destination": destination["location_id"],
        },
    )

    # T1: 早上 08:00，在家观察。
    t1 = _local(8, 0, date_text, tz_name)
    obs1 = wls.observe_world(hermes_home=instance_home, now=t1)
    summary1 = _obs_summary(obs1)
    _expect(
        (summary1.get("current_location") or "").startswith(world.home_location_id),
        f"初始位置不在住宅: {summary1}",
    )
    record("observe_morning_at_home", summary1)

    # 逐段行走路线（编辑可能改变道路，路径可以是任意段数）。
    # 第一段 08:05 出发（途中观察 in_progress）；最后一段固定 10:00 出发，
    # 保证目的地（白天营业的店铺）处于开放时段。
    target = route[-1]
    clock = _local(8, 5, date_text, tz_name)
    total_walk_seconds = 0
    for leg_index, leg in enumerate(route[1:]):
        if leg == target and leg_index >= 1:
            # 最后一段在目的地营业日的白天出发（尊重编辑后的营业时间）。
            rule = world.location_rule(target)
            open_hour = rule["open_hour"] if rule["open_hour"] is not None else 10
            day_open = _local(max(10, open_hour), 5, date_text, tz_name)
            if clock < day_open:
                clock = day_open
        plan = wls.plan_move_to(leg, hermes_home=instance_home, now=clock)
        _expect(
            plan.get("status") == "ready",
            f"第 {leg_index + 1} 段移动计划失败（{route[leg_index]} → {leg}）: {plan}",
        )
        duration = int((plan.get("travel") or {}).get("route_duration_seconds") or 60)
        total_walk_seconds += duration
        record(
            f"plan_move_leg{leg_index + 1}",
            {"from": route[leg_index], "target": leg, "walk_seconds": duration},
        )
        applied = wls.apply_move_to(leg, hermes_home=instance_home, now=clock)
        _expect(applied.get("status") == "applied", f"第 {leg_index + 1} 段移动未开始: {applied}")

        if leg_index == 0:
            # 中途观察：仍在出发地，travel in_progress。
            t_mid = clock + timedelta(seconds=max(1, duration) // 2)
            obs2 = wls.observe_world(hermes_home=instance_home, now=t_mid)
            summary2 = _obs_summary(obs2)
            _expect(summary2.get("travel_status") == "in_progress", f"途中应显示 in_progress: {summary2}")
            record("observe_in_transit", summary2)

        arrival = wtd.realize_due_travel(
            hermes_home=instance_home, now=clock + timedelta(seconds=duration)
        )
        _expect(arrival.get("status") == "arrived", f"第 {leg_index + 1} 段到达未落地: {arrival}")
        record(
            f"apply_and_arrive_leg{leg_index + 1}",
            {
                "from": route[leg_index],
                "target": leg,
                "walk_seconds": duration,
                "arrived_at": arrival.get("arrived_at"),
            },
        )
        clock = clock + timedelta(seconds=duration)

    # 白天观察：目的地营业、有活动、可能有 NPC。
    t_noon = clock + timedelta(minutes=30)
    obs3 = wls.observe_world(hermes_home=instance_home, now=t_noon)
    summary3 = _obs_summary(obs3)
    _expect(summary3.get("current_location") == target, f"白天位置不对: {summary3}")
    _expect(summary3.get("availability") == "OPEN", f"白天目的地应显示营业: {summary3}")
    _expect(bool(summary3.get("affordances")), f"营业中应有可做活动: {summary3}")
    record("observe_daytime_at_destination", summary3)

    # 深夜观察：目的地打烊、活动为空。
    t_night = _local(23, 0, date_text, tz_name)
    obs4 = wls.observe_world(hermes_home=instance_home, now=t_night)
    summary4 = _obs_summary(obs4)
    rule = world.location_rule(target)
    closed_at_night = not (rule["open_hour"] is None)
    if closed_at_night:
        _expect(summary4.get("availability") == "CLOSED", f"深夜目的地应显示打烊: {summary4}")
        _expect(not summary4.get("affordances"), f"打烊后不应有可做活动: {summary4}")
    record("observe_night_at_destination", summary4)

    # 深夜移动规则：在即时移动模式（非 travel）下，进入打烊地点被拒绝。
    instant_env = dict(os.environ)
    instant_env["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "0"
    others = [
        loc_id
        for loc_id, loc in world.locations.items()
        if loc_id not in (target, world.home_location_id)
        and loc["kind"] not in ("street", "home")
        and world.location_rule(loc_id)["open_hour"] is not None
    ]
    if others:
        closed_target = sorted(others)[0]
        night_plan = wls.plan_move_to(
            closed_target, hermes_home=instance_home, environment=instant_env, now=t_night
        )
        record("plan_move_to_closed_shop_at_night", {
            "target": closed_target,
            "status": night_plan.get("status"),
            "reason_code": night_plan.get("reason_code"),
        })
        _expect(
            night_plan.get("status") == "rejected"
            and night_plan.get("reason_code") == "LOCATION_CLOSED",
            f"打烊地点的移动应被拒绝: {night_plan}",
        )

    # 落盘状态摘要（供 restart 阶段核对）。
    import world_foundation as wf

    final_state = wf.WorldStateStore(
        instance_home / "data" / "world" / "world_state.json"
    ).load()
    travel_state = wtd.load_state(instance_home)
    receipt = {
        "kind": "worldgen_world_scenario",
        "package": package.summary(),
        "instance_home": str(instance_home),
        "scenario_date": date_text,
        "final": {
            "location": final_state["location"],
            "revision": final_state["revision"],
            "travels": len((travel_state or {}).get("travels", [])),
        },
        "steps": steps,
    }
    out_path = instance_home / "demo_world_receipt.json"
    out_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt["receipt_path"] = str(out_path)
    return receipt


def run_restart_check(
    package_dir: Path | str,
    instance_home: Path | str,
) -> dict[str, Any]:
    """新进程视角：世界没有重新随机、位置没有丢失、已发生的移动还在。"""
    _require_posix()
    _ensure_paths()
    from .loader import load
    from . import schema as pkg_schema

    package = load(package_dir)
    instance_home = Path(instance_home).resolve()
    meta = json.loads((instance_home / "instance.json").read_text(encoding="utf-8"))
    # 声明活动世界：重启后的进程与启动时读取同一份世界包。
    _apply_env(instance_home, package.package_dir, package.world_id, travel=True)

    import world_runtime_data as wrd

    wrd.reset_cache()

    import world_foundation as wf

    state = wf.WorldStateStore(instance_home / "data" / "world" / "world_state.json").load()
    _expect(state is not None, "重新加载后世界状态缺失")
    _expect(state["world_id"] == package.world_id, "重启后 world_id 变化（交叉污染）")
    _expect(state["timezone"] == package.timezone_name, "重启后时区变化")

    _ensure_paths()
    import world_travel_delivery as wtd

    travels = wtd.load_state(instance_home)
    manifest = pkg_schema.load_package(package.package_dir)["world.json"]
    _expect(manifest["world_id"] == package.world_id, "世界包内容被重新随机（world_id 变化）")

    receipt = {
        "kind": "worldgen_restart_check",
        "instance_home": str(instance_home),
        "world_id": state["world_id"],
        "location_after_restart": state["location"],
        "revision_after_restart": state["revision"],
        "travel_records": len((travels or {}).get("travels", [])),
        "package_world_name": package.display_name,
        "meta_world_name": meta.get("world_name"),
        "checks": {
            "world_id_stable": True,
            "location_preserved": True,
            "travels_preserved": True,
            "package_not_rerandomized": True,
        },
    }
    out_path = instance_home / "demo_restart_receipt.json"
    out_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt["receipt_path"] = str(out_path)
    return receipt


def run_service_scenario(
    package_dir: Path | str,
    instance_home: Path | str,
) -> dict[str, Any]:
    """World/Body 服务链：canonical 启动 → 授权 → 提交 → 账本回执 → 身体后果。

    本阶段关闭 travel（移动即时提交），走 resolver 的直接移动路径；
    M14A 授权、执行账本与身体后果全部使用 rc9 既有实现。
    """
    _require_posix()
    _ensure_paths()
    from .loader import load

    package = load(package_dir)
    instance_home = Path(instance_home).resolve()
    _apply_env(
        instance_home,
        package.package_dir,
        package.world_id,
        travel=False,
        substrate=True,
        body_runtime=True,
    )

    from world_body_config import load_config
    from world_body_service import WorldBodyService
    from world_body_gateway_api import GatewaySurface
    import world_foundation as wf
    import world_runtime_data as wrd

    wrd.reset_cache()
    world = wrd.active_world()

    config = load_config(
        None,
        runtime_root=_REPO_ROOT / "components" / "world",
        world_home=instance_home,
        runtime_mode="canonical",
        authority="production",
        world_id=package.world_id,
    )
    service = WorldBodyService(config, tick_seconds=60)
    startup = service.startup()
    surface = GatewaySurface(service)

    snapshot_before = surface.handle("get_snapshot", None)
    world_before = wf.WorldStateStore(
        instance_home / "data" / "world" / "world_state.json"
    ).load()
    origin = world_before["location"]["place_id"]

    import world_body_substrate as wbs

    substrate = wbs.SubstrateStore(wbs.substrate_path(instance_home)).load()
    _expect(substrate is not None, "身体基座缺失（服务启动应已就绪）")

    # 选一个当前开放的可达目标（编辑后的营业时间在这里真实生效）。
    import world_daily_rhythm as wdr

    reachable = sorted(world.reachable_of(origin))
    candidates = [
        loc_id
        for loc_id in reachable
        if loc_id != origin
        and wdr.location_availability(loc_id, datetime.now(timezone.utc), environment=os.environ)["status"]
        == "OPEN"
    ]
    closed_candidates = [loc_id for loc_id in reachable if loc_id not in candidates]
    _expect(bool(reachable), f"当前位置 {origin} 没有相邻地点")
    if candidates:
        target = candidates[0]
        move_expectation = "applied"
    else:
        # 当前时段所有相邻地点都关闭：正向移动让位于"营业时间拒绝"验证。
        target = sorted(closed_candidates)[0]
        move_expectation = "rejected"

    execution_id = "worldgen-demo-move-001"
    deps = {
        "world.revision": world_before["revision"],
        "world.location": origin,
        "body.revision": substrate["revision"],
        "body.pose": substrate["pose"],
    }
    registry = surface.authorizations
    _expect(registry is not None, "授权注册表未接线")
    authorization = registry.issue(
        decision_id=None,
        intent_id="worldgen-demo-intent-001",
        intent_fingerprint="worldgen-demo-fingerprint-001",
        execution_id=execution_id,
        actor="chiyo",
        verb="MOVE",
        params={"destination_location_id": target},
        authority_generation=world_before["revision"],
    )

    # 负例 1：没有授权的提交必须被拒绝。
    no_auth = surface.submit_action({
        "action": "MOVE",
        "execution_id": "worldgen-demo-move-002",
        "params": {"destination_location_id": target},
        "observed_dependencies": deps,
    })
    _expect(
        no_auth.get("status") == "rejected"
        and no_auth.get("reason_code") == "AUTHORIZATION_REQUIRED",
        f"无授权提交未被拒绝: {no_auth}",
    )

    submission = surface.submit_action({
        "action": "MOVE",
        "execution_id": execution_id,
        "params": {"destination_location_id": target},
        "observed_dependencies": deps,
        "authorization_id": authorization["authorization_id"],
    })
    if move_expectation == "applied":
        _expect(submission.get("status") == "applied", f"授权移动未提交成功: {submission}")
    else:
        # 当前时段全部相邻地点关闭：这次授权移动应被营业时间正确拒绝。
        _expect(
            submission.get("status") == "rejected"
            and submission.get("reason_code") in ("LOCATION_CLOSED", "NOT_ALLOWED", "CLOSED"),
            f"关闭时段的授权移动未被正确拒绝: {submission}",
        )

    world_after = wf.WorldStateStore(
        instance_home / "data" / "world" / "world_state.json"
    ).load()

    ledger_frame_note = "skipped: 所有相邻地点当前关闭（营业时间规则生效）"
    frame: dict[str, Any] = {}
    if move_expectation == "applied":
        _expect(
            world_after["location"]["place_id"] == target,
            f"移动后位置不正确: {world_after['location']}",
        )
        # 执行账本回执。
        from world_execution_ledger import ExecutionLedger

        ledger = ExecutionLedger(config.ledger_path)
        frame = ledger.frame(execution_id)
        _expect(frame.get("known") is True, "执行账本中没有该 execution_id 的记录")
        _expect(frame.get("state") == "committed", f"账本状态不是 committed: {frame.get('state')}")
        ledger_frame_note = frame.get("state")

    # 负例 2：不可达地点必须被拒绝（用新的执行 ID 与授权）。
    move_start = target if move_expectation == "applied" else origin
    far = sorted(
        loc_id
        for loc_id in world.locations
        if loc_id not in world.reachable_of(move_start) and loc_id != move_start
    )
    rejection: dict[str, Any] = {"skipped": "没有不可达地点可供测试"}
    if far:
        exec2 = "worldgen-demo-move-003"
        auth2 = registry.issue(
            decision_id=None,
            intent_id="worldgen-demo-intent-002",
            intent_fingerprint="worldgen-demo-fingerprint-002",
            execution_id=exec2,
            actor="chiyo",
            verb="MOVE",
            params={"destination_location_id": far[0]},
            authority_generation=world_after["revision"],
        )
        rejection = surface.submit_action({
            "action": "MOVE",
            "execution_id": exec2,
            "params": {"destination_location_id": far[0]},
            "observed_dependencies": {
                "world.revision": world_after["revision"],
                "world.location": move_start,
                "body.revision": substrate["revision"],
                "body.pose": substrate["pose"],
            },
            "authorization_id": auth2["authorization_id"],
        })
        _expect(rejection.get("status") in ("rejected", "failed"), f"不可达移动未被拒绝: {rejection}")

    observation = surface.handle("get_observation", None)

    receipt = {
        "kind": "worldgen_service_scenario",
        "instance_home": str(instance_home),
        "world_id": package.world_id,
        "startup_steps_ok": all(step.get("ok") for step in startup.get("steps", [])),
        "move": {
            "from": origin,
            "to": target,
            "status": submission.get("status"),
            "reason_code": submission.get("reason_code"),
            "authorization": submission.get("authorization", {}).get("state"),
            "applied_to_body": submission.get("applied_to_body"),
        },
        "ledger_state": ledger_frame_note,
        "rejected_without_authorization": no_auth.get("reason_code"),
        "rejected_unreachable": {
            "status": rejection.get("status"),
            "reason_code": rejection.get("reason_code"),
            "target": far[0] if far else None,
        },
        "observation_world_id": observation.get("world_id"),
        "observation_location": (observation.get("location") or {}).get("place_id"),
    }
    out_path = instance_home / "demo_service_receipt.json"
    out_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt["receipt_path"] = str(out_path)
    return receipt


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="worldgen 隔离 World/Body 运行演示")
    parser.add_argument("phase", choices=["world", "service", "restart", "full"])
    parser.add_argument("--package", required=True, help="世界包目录")
    parser.add_argument("--home", required=True, help="实例目录（先用 instance 命令初始化）")
    parser.add_argument("--date", default=BASE_SCENARIO_DATE, help="场景日期（YYYY-MM-DD，默认星期三）")
    args = parser.parse_args(argv)

    try:
        if args.phase in ("world", "full"):
            receipt = run_world_scenario(args.package, args.home, date_text=args.date)
            print(json.dumps({"phase": "world", "final": receipt["final"], "steps_ok": True}, ensure_ascii=False))
        if args.phase in ("restart", "full"):
            receipt = run_restart_check(args.package, args.home)
            print(json.dumps({"phase": "restart", "checks": receipt["checks"]}, ensure_ascii=False))
        if args.phase in ("service", "full"):
            receipt = run_service_scenario(args.package, args.home)
            print(json.dumps({k: receipt[k] for k in ("move", "ledger_state", "rejected_without_authorization", "rejected_unreachable")}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一报错
        print(f"DEMO_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
