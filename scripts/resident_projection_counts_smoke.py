#!/usr/bin/env python3
"""resident wake/arrival 读面计数的零模型冒烟（真实 resident/runner/World 记录）。

它做什么（全部本机、0 产品模型、0 网络）：

1. 用真实 ``world_journey`` 引擎在全新 ``/tmp`` 世界实例里跑完两段计时旅程；
   到达证据来自真实 owner 的 travel/世界状态记录，不是手写投影。
2. 用真实 ``AutonomousLifeRunner._record`` 写 ``autonomous_wakes.jsonl``：
   327 个 tick 只留下 17 条记录（连续静默的后续 tick 本来就不写盘）。
3. 每个 tick 都像真实 resident 一样再结算一次已完成的旅程（重复投影），并通过
   ``AutonomousLifeResident._journal/_accumulate/_write_summary`` 的真实报告入口
   累计；随后同根新建 resident 走 ``_replay_journal``，核对 summary 与权威记录
   一致、重启不重计。
4. 打印旧口径（每条投影都算）与修正口径的对照值，明确这是确定性夹具，不是她的
   自然生活，也没有调用模型。

所有自有状态都在唯一的 ``/tmp`` 子目录里保留，便于复查；没有外部服务。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_resident as resident  # noqa: E402
import autonomous_life_runner as runner_module  # noqa: E402
from chiyo_bundle import world_journey as journey  # noqa: E402
from components.worldgen import schema  # noqa: E402
from components.worldgen.instance import init_instance  # noqa: E402
from components.worldgen.tests._setup import make_valid_docs, write_temp_package  # noqa: E402

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
TICKS = 327
WAKE_RECORDS = 17


def _fresh_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="nyairo-projection-smoke-", dir="/tmp"))


def _clean_env() -> dict:
    env = dict(os.environ)
    for key in ("CHIYO_WORLD_PACKAGE", "CHIYO_WORLD_HOME", "CHIYO_WORLD_ID", "XINXI_DIR"):
        env.pop(key, None)
    return env


class DirectOwnerSubmitPort:
    """冒烟专用直连端口：调用真实 World owner，只跳过 ticket 授权。"""

    def submit_move(self, *, destination, origin, execution_id, hermes_home,
                    environment, now=None):
        import world_living_skeleton as wls

        return wls.apply_move_to(destination, hermes_home=hermes_home,
                                 environment=environment, now=now,
                                 execution_id=execution_id)


def _make_world(root: Path) -> SimpleNamespace:
    """一个 home→s1→s2 的三节点世界，两段旅程足够验证段级到达。"""

    package_dir = write_temp_package(make_valid_docs(seed=929711, theme="coastal_town"),
                                     prefix="proj-smoke-pkg-")
    docs = schema.load_package(package_dir)
    topology = docs["topology.json"]
    ids = sorted(item["location_id"] for item in topology["locations"])
    home = "home" if "home" in ids else ids[0]
    others = [item for item in ids if item != home]
    s1, s2, s3, s4 = others[0], others[1], others[2], others[3]
    # 与真实旅程回归同一张连通图；目标 s2 的路线正好是 home→s1→s2 两段。
    edges = [(home, s1, 300), (s1, s2, 200), (s1, s3, 100), (s2, s3, 100),
             (s2, s4, 150)]
    edges.extend((item, s4, 900) for item in others[4:])
    adjacency = {item: set() for item in ids}
    for first, second, _seconds in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    for item in topology["locations"]:
        item["reachable"] = sorted(adjacency[item["location_id"]])
    topology["edges"] = [{"from": first, "to": second, "walk_seconds": seconds}
                         for first, second, seconds in edges]
    schema.write_package(package_dir, docs)

    instance_home = Path(tempfile.mkdtemp(prefix="proj-smoke-inst-", dir=str(root))) / "instance"
    init_instance(package_dir, instance_home)
    env = _clean_env()
    env.update({
        "CHIYO_WORLD_PACKAGE": str(package_dir),
        "CHIYO_WORLD_HOME": str(instance_home),
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
        "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
        "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
    })
    return SimpleNamespace(package_dir=package_dir, home=instance_home, env=env,
                           home_node=home, first=s1, target=s2)


def _walk_journey(world: SimpleNamespace) -> dict:
    """真实引擎跑完两段；每次 advance 都是真实 owner 的 travel/世界记录。"""

    port = DirectOwnerSubmitPort()

    def advance(now):
        return journey.advance_journey(hermes_home=world.home, environment=world.env,
                                       now=now, submit_port=port)

    accepted = journey.accept_journey(target=world.target, hermes_home=world.home,
                                      environment=world.env, now=NOW, submit_port=port)
    if accepted.get("status") != "started":
        raise SystemExit("journey did not start: %r" % (accepted,))
    current = accepted
    for step in range(1, 8):
        current = advance(NOW + timedelta(minutes=10 * step))
        if current.get("status") == "completed":
            break
    if current.get("status") != "completed":
        raise SystemExit("journey did not complete: %r" % (current,))
    return current


class _PositionObservation:
    """确定性位置观察：始终返回旅程目标，好让旧的 ``arrived`` 布尔也每次都真。"""

    def __init__(self, target: str) -> None:
        self.last_observation = {"location": {"place_id": target}}
        self.last_catalog = None

    def __call__(self):
        return self.last_observation


def _settlement_cycle(world: SimpleNamespace) -> Any:
    """只装配 settle 需要的字段，让 ``settle_world_journey`` 读真实 owner 记录。"""

    cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
    cycle.world_home = str(world.home)
    cycle.env = dict(world.env)
    cycle.pause_state_root = world.home / "life_pause"
    cycle.pause_gate = lambda: {"blocked": False}
    cycle.last_world_error = None
    cycle.last_world_observation = None
    cycle.last_world_catalog = None
    cycle.last_journey_settlement = None
    cycle.runtime = SimpleNamespace(
        world_action_adapter=None,
        canonical_state=lambda: {})
    cycle.world_opportunity_provider = _PositionObservation(world.target)
    return cycle


def _recovery_record(tick: int, journey_id: str) -> dict:
    """一条确定性的 journal 行：只用于验证淘汰后的持久证据恢复。"""

    return {"at": NOW.isoformat(), "logical_at": None, "tick": tick,
            "events": [], "feed_errors": [], "due": {}, "reconciled": [],
            "journey": {"ok": True, "journey_id": journey_id, "segments_confirmed": 1,
                        "arrived": True, "journey_status": "completed"}}


def _wake_sequence() -> list[tuple[str, str, str]]:
    """327 个 tick 的真实写入序列；runner 只留下 17 行。

    8 组「静默 + 行动」= 16 行，随后一条新的静默 streak 首行 = 17 行，其余 310 条
    连续静默按 runner 既有语义不写盘。
    """

    rows: list[tuple[str, str, str]] = []
    for index in range(8):
        rows.append(("NOT_DUE", "silence", ""))
        rows.append(("ACTED", "acted", "ACTED"))
    rows.append(("NOT_DUE", "silence", ""))
    rows.extend([("NOT_DUE", "silence", "")] * (TICKS - len(rows)))
    return rows


def main() -> int:
    root = _fresh_root()
    world = _make_world(root)
    completed = _walk_journey(world)
    cycle = _settlement_cycle(world)

    # 真实 runner 的报告入口：同一条 _record 路径、同一份 wake journal。
    runner = runner_module.AutonomousLifeRunner.__new__(runner_module.AutonomousLifeRunner)
    runner.last_wake = None
    runner.wakes_run = 0
    runner.last_error = None
    runner._journal_quiet = False
    runner.wake_log = root / "autonomous_wakes.jsonl"

    state_root = root / "resident"
    state_root.mkdir(parents=True, exist_ok=True)
    loop = resident.AutonomousLifeResident(
        state_root=state_root, environment=dict(world.env), poll_seconds=180,
        clock=lambda: NOW, sleeper=lambda seconds: None, monotonic=lambda: 0.0)
    loop.assembled = {"cycle": cycle}

    legacy_wakes = 0
    legacy_arrivals = 0
    session = []
    for index, (status, status_class, outcome) in enumerate(_wake_sequence(), start=1):
        wake = runner._record(
            runner_module.WAKE_KIND_CHECK_WINDOW, "check-window", status, status_class,
            NOW, trigger_ref="evt:autonomous:check",
            trigger_kind=alc.TRIGGER_IDLE_WINDOW,
            llm_calls=1 if status == "ACTED" else 0, outcome=outcome or None,
            extra=({"decision_route": "MODEL"} if status == "ACTED" else None))
        wake_dict = wake.to_dict()
        settlement = loop.settle_journey(observed_at=NOW.isoformat())
        record = {"at": NOW.isoformat(), "logical_at": None, "tick": index,
                  "events": [], "feed_errors": [], "due": wake_dict,
                  "reconciled": [], "journey": settlement}
        loop.ticks = index
        loop.last_tick = record
        loop._journal(record)
        loop._accumulate(record)
        last_wake_dict = wake_dict

        legacy_wakes += 1  # 改前：每个 tick 的 due 投影都算一个 wake
        if settlement.get("arrived"):
            legacy_arrivals += 1  # 改前：完成投影每次重读都算一次到达

    loop._write_summary(loop.summary())
    summary = loop.summary()

    # 同根重启：新 resident 实例只从持久 journal 重建，不重读 owner。
    restarted = resident.AutonomousLifeResident(
        state_root=state_root, environment=dict(world.env), poll_seconds=180,
        clock=lambda: NOW, sleeper=lambda seconds: None, monotonic=lambda: 0.0)
    restarted.assembled = {"cycle": cycle}
    restarted._replay_journal()
    # 重启后再投影一次同一静默窗口与完成旅程：不重计。
    restarted._accumulate({"at": NOW.isoformat(), "tick": TICKS + 1, "events": [],
                           "feed_errors": [], "due": last_wake_dict, "reconciled": [],
                           "journey": restarted.settle_journey(
                               observed_at=NOW.isoformat())})
    restarted_summary = restarted.summary()

    # 缓存淘汰恢复：40 个旅程写进同一份真实 resident journal，再重读最早的旅程。
    # 32 条有界缓存装不下第 40 个；历史最大 confirmed 必须从既有行恢复，不能重算。
    recovery_root = root / "projection-recovery"
    recovery_root.mkdir(parents=True, exist_ok=True)
    recovery = resident.AutonomousLifeResident(
        state_root=recovery_root, environment=dict(world.env), poll_seconds=180,
        clock=lambda: NOW, sleeper=lambda seconds: None, monotonic=lambda: 0.0)
    for index in range(40):
        record = _recovery_record(index + 1, "journey:%d" % index)
        recovery._journal(record)
        recovery._accumulate(record)
    recovery_arrivals_live = recovery.counters["journey_arrivals"]

    repeat = _recovery_record(41, "journey:0")
    recovery._journal(repeat)
    recovery._accumulate(repeat)
    recovery_arrivals_after_evicted_repeat = recovery.counters["journey_arrivals"]

    # 同根重启：只从这份 journal 重放，淘汰过的旅程仍按历史高水位恢复。
    replayed = resident.AutonomousLifeResident(
        state_root=recovery_root, environment=dict(world.env), poll_seconds=180,
        clock=lambda: NOW, sleeper=lambda seconds: None, monotonic=lambda: 0.0)
    replayed._replay_journal()
    recovery_arrivals_after_replay = replayed.counters["journey_arrivals"]

    wake_lines = [json.loads(line) for line in
                  runner.wake_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = {
        "fixture": str(root),
        "note": "确定性夹具，不是她的自然生活；0 产品模型调用",
        "journey": {"status": completed.get("status"),
                    "segments_confirmed": completed.get("segments_confirmed")},
        "wake_journal_records": len(wake_lines),
        "ticks": TICKS,
        "summary_wakes": summary["counters"]["wakes"],
        "summary_journey_arrivals": summary["counters"]["journey_arrivals"],
        "summary_journey_settlements": summary["counters"]["journey_settlements"],
        "legacy_per_projection_wakes": legacy_wakes,
        "legacy_per_arrived_arrivals": legacy_arrivals,
        "restarted_wakes": restarted_summary["counters"]["wakes"],
        "restarted_arrivals": restarted_summary["counters"]["journey_arrivals"],
        "after_restart_projection_wakes": restarted.counters["wakes"],
        "after_restart_projection_arrivals": restarted.counters["journey_arrivals"],
        "recovery_arrivals_live": recovery_arrivals_live,
        "recovery_arrivals_after_evicted_repeat": recovery_arrivals_after_evicted_repeat,
        "recovery_arrivals_after_replay": recovery_arrivals_after_replay,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    checks = [
        (result["wake_journal_records"], WAKE_RECORDS, "wake journal 应正好 17 条"),
        (result["summary_wakes"], WAKE_RECORDS, "summary wakes 应与 wake journal 一致"),
        (result["summary_journey_arrivals"], 2, "两段真实旅程应计 2 次到达"),
        (result["restarted_wakes"], result["summary_wakes"], "重启后 wakes 不重计"),
        (result["restarted_arrivals"], result["summary_journey_arrivals"],
         "重启后 arrivals 不重计"),
        (result["after_restart_projection_wakes"], result["summary_wakes"],
         "重启后再投影不增 wake"),
        (result["after_restart_projection_arrivals"], result["summary_journey_arrivals"],
         "重启后再投影不增 arrival"),
        (result["recovery_arrivals_live"], 40, "40 个旅程应各计 1 次到达"),
        (result["recovery_arrivals_after_evicted_repeat"], 40,
         "缓存淘汰后重读 journey:0 不再增加到达"),
        (result["recovery_arrivals_after_replay"], 40,
         "同根重放不因缓存淘汰重计到达"),
    ]
    bad = [detail for actual, expected, detail in checks if actual != expected]
    if bad:
        print("FAIL: " + "; ".join(bad))
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
