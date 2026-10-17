"""W02：持久旅程、幂等推进与重启恢复。

场景对应（施工单 §7）：

* A 三段计时旅程 / 室内节点 / 汐见市兼容 / 每段恰好一次 travel 记录
* B 首次旅程耐久写失败：owner 未被调用
* C 四个故障窗口（其中 owner 已提交窗口用真实子进程终止）
* D 重复推进、多进程并发推进、幂等键重放与冲突
* E 第二段 owner 拒绝 / 证据未知：不丢目标、不改成功、不启动后段
* F 图变化 / world_id 不同 / 外部改动 / 外部在途 / 损坏账本
* G 纯 observe 在到期前后与反复读取时零写入
* H 计时开关变化 / 环境冲突 / 自定义世界真名 / 旧意图文件 / 禁用 feature
* 6 旧心跳入口：显式参数、一次性、无自主目标

需要 POSIX（fcntl 文件锁）与 tz 数据库；原生 Windows 自动跳过。
"""

from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.worldgen import schema
from components.worldgen.instance import init_instance
from components.worldgen.interior import apply_interiors_to_package
from components.worldgen.loader import load as load_package
from components.worldgen.tests._setup import WorldGenTestCase, make_valid_docs

from chiyo_bundle import world_journey as journey
from chiyo_bundle import world_move_tool as tool

posix_only = unittest.skipIf(
    os.name == "nt", "持久旅程链路需要 POSIX 文件锁（在 WSL/Linux 上运行）"
)

STATE_FILES = (
    "world_state.json",
    "world_travel_delivery.json",
    "world_journey.json",
    "intended_target.json",
)

_ENV_KEYS = (
    "CHIYO_WORLD_PACKAGE",
    "CHIYO_WORLD_HOME",
    "CHIYO_WORLD_ID",
    "XINXI_DIR",
)


def _clean_env() -> dict:
    env = dict(os.environ)
    for key in _ENV_KEYS:
        env.pop(key, None)
    return env


@posix_only
class JourneyTestCase(WorldGenTestCase):
    """共用夹具：可定制道路图的世界包 + 隔离实例 + 请求环境。"""

    def setUp(self) -> None:
        super().setUp()
        import world_foundation as wf
        import world_living_skeleton as wls
        import world_runtime_data as wrd
        import world_travel_delivery as wtd

        self.wf, self.wls, self.wrd, self.wtd = wf, wls, wrd, wtd
        self.wrd.reset_cache()
        self.now = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
        self.world = self.make_world()

    def tearDown(self) -> None:
        self.wrd.reset_cache()
        super().tearDown()

    # -- 夹具 ---------------------------------------------------------------

    def make_world(self, *, seed=929711, theme="coastal_town", travel=True,
                   interiors=False, prefix="w02-", home_name=None) -> SimpleNamespace:
        """生成世界并把道路图改写成 home-s1-s2-s4（三段）+ 死胡同。"""
        package_dir = self.temp_package(
            make_valid_docs(seed=seed, theme=theme), prefix=f"{prefix}pkg-"
        )
        if interiors:
            apply_interiors_to_package(package_dir)
        ids = self._rewrite_roads(package_dir, home_name=home_name)
        self.wrd.reset_cache()
        package = load_package(package_dir)
        home = Path(self.temp_dir(prefix=f"{prefix}inst-")) / "instance"
        init_instance(package_dir, home)
        env = _clean_env()
        env.update({
            "CHIYO_WORLD_PACKAGE": str(package_dir),
            "CHIYO_WORLD_HOME": str(home),
            "CHIYO_WORLD_ID": package.world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1" if travel else "0",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
        })
        return SimpleNamespace(
            package_dir=package_dir, package=package, home=home, env=env, ids=ids,
            travel=travel,
        )

    def _rewrite_roads(self, package_dir: Path, *, home_name=None) -> dict:
        docs = schema.load_package(package_dir)
        topology = docs["topology.json"]
        location_ids = sorted(item["location_id"] for item in topology["locations"])
        self.assertIn("home", location_ids)
        others = [item for item in location_ids if item != "home"]
        self.assertGreaterEqual(len(others), 5)
        s1, s2, s3, s4 = others[0], others[1], others[2], others[3]
        dead = others[4:]
        edges = [
            ("home", s1, 300),
            (s1, s2, 200),
            (s1, s3, 100),
            (s2, s3, 100),
            (s2, s4, 150),
        ]
        edges.extend((item, s4, 900) for item in dead)
        adjacency = {item: set() for item in location_ids}
        for first, second, _ in edges:
            adjacency[first].add(second)
            adjacency[second].add(first)
        by_id = {item["location_id"]: item for item in topology["locations"]}
        for location_id, item in by_id.items():
            item["reachable"] = sorted(adjacency[location_id])
        if home_name:
            by_id["home"]["name"] = home_name
        topology["edges"] = [
            {"from": first, "to": second, "walk_seconds": seconds}
            for first, second, seconds in edges
        ]
        schema.write_package(package_dir, docs)
        return {"s1": s1, "s2": s2, "s3": s3, "s4": s4, "dead": dead}

    def change_edge_duration(self, world, first: str, second: str, seconds: int) -> None:
        docs = schema.load_package(world.package_dir)
        for edge in docs["topology.json"]["edges"]:
            if {edge["from"], edge["to"]} == {first, second}:
                edge["walk_seconds"] = seconds
        schema.write_package(world.package_dir, docs)
        self.wrd.reset_cache()

    # -- 状态读取 -----------------------------------------------------------

    def state_dir(self, world=None) -> Path:
        return Path((world or self.world).home) / "data" / "world"

    def snapshot(self, world=None) -> dict:
        base = self.state_dir(world)
        return {
            name: (base / name).read_bytes() if (base / name).is_file() else None
            for name in STATE_FILES
        }

    def mtimes(self, world=None) -> dict:
        base = self.state_dir(world)
        return {
            name: (base / name).stat().st_mtime_ns
            for name in STATE_FILES
            if (base / name).is_file()
        }

    def journey_state(self, world=None) -> dict:
        path = self.state_dir(world) / "world_journey.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def travels(self, world=None) -> list:
        state = self.wtd.load_state(Path((world or self.world).home),
                                    (world or self.world).env)
        return [] if state is None else state["travels"]

    def position(self, world=None, now=None) -> str:
        world = world or self.world
        state = self.wls.read_move_state(
            hermes_home=world.home, environment=world.env, now=now or self.now
        )
        return state["world_location_id"]

    def force_world_location(self, world, node: str) -> None:
        """外部写入者：用规范 WorldStateStore 把世界位置改到 node。"""
        active = self.wrd.active_world(world.env)
        path = self.wtd.world_state_path(world.home)
        store = self.wf.WorldStateStore(path)
        state = store.load()
        updated = json.loads(json.dumps(state))
        place = active.node_place(node)
        area = active.area_id_of(node)
        updated["revision"] = state["revision"] + 1
        updated["location"] = {"place_id": place, "area_id": area}
        updated["scene"] = {
            "scene_id": f"{place}.{area}",
            "revision": state["scene"]["revision"] + 1,
        }
        store.save(updated, expected_revision=updated["revision"])

    def world_revision(self, world=None) -> int:
        path = self.state_dir(world) / "world_state.json"
        return json.loads(path.read_text(encoding="utf-8"))["revision"]

    def owner_bytes(self, world=None) -> dict:
        """owner 侧字节快照（world_state + travel + 旧意图）。

        旅程账本在阻塞/未知时会被补写一条分类记录，那是它自己的账本，不算越权
        写世界状态；所以 fail-closed 断言只比较 owner 侧文件。
        """
        base = self.state_dir(world)
        names = ("world_state.json", "world_travel_delivery.json", "intended_target.json")
        return {
            name: (base / name).read_bytes() if (base / name).is_file() else None
            for name in names
        }

    # -- 引擎调用 -----------------------------------------------------------

    def accept(self, target, world=None, *, now=None, request_key=None) -> dict:
        world = world or self.world
        return journey.accept_journey(
            target=target, hermes_home=world.home, environment=world.env,
            now=now or self.now, request_key=request_key,
        )

    def advance(self, world=None, *, now=None) -> dict:
        world = world or self.world
        return journey.advance_journey(
            hermes_home=world.home, environment=world.env, now=now or self.now
        )

    def observe(self, world=None, *, now=None) -> dict:
        world = world or self.world
        return journey.observe_journey(
            hermes_home=world.home, environment=world.env, now=now or self.now
        )

    def move(self, location, world=None, *, now=None, env=None, request_key=None) -> dict:
        world = world or self.world
        return json.loads(tool.move_handler(
            {"location": location}, environment=env or world.env,
            now=now or self.now, request_key=request_key,
        ))

    # -- 子进程 -------------------------------------------------------------

    def run_child(self, code: str, world=None, *, env=None):
        world = world or self.world
        script = (
            "import os, sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'components' / 'world' / 'scripts')!r})\n"
            "import world_living_skeleton as wls\n"
            "real_apply = wls.apply_move_to\n"
            "from chiyo_bundle import world_journey as journey\n"
            + code
        )
        return subprocess.run(
            [sys.executable, "-c", script], env=env or world.env, cwd=str(ROOT),
            capture_output=True, text=True, timeout=180,
        )

    def env_literal(self, world=None) -> str:
        return repr(dict((world or self.world).env))

    def iso(self, moment: datetime) -> str:
        return moment.isoformat()


@posix_only
class JourneyScenarioTest(JourneyTestCase):
    """A/B/C/D/E/F/G：引擎级行为。"""

    # -- A：三段计时旅程 ------------------------------------------------

    def test_three_segment_journey_advances_to_target_once_per_segment(self) -> None:
        world = self.world
        s1, s2, s4 = world.ids["s1"], world.ids["s2"], world.ids["s4"]
        revision_before = self.world_revision()

        started = self.accept(s4)
        self.assertEqual(started["status"], "started")
        self.assertEqual(started["route"], [s1, s2, s4])
        self.assertEqual(started["destination_location_id"], s1)
        self.assertEqual(started["estimated_minutes"], 5)
        self.assertEqual(started["segments_confirmed"], 0)
        self.assertEqual(self.position(), "home")

        second = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(second["status"], "started")
        self.assertEqual(second["destination_location_id"], s2)
        self.assertEqual(second["segments_confirmed"], 1)
        self.assertEqual(self.position(), s1)

        third = self.advance(now=self.now + timedelta(seconds=500))
        self.assertEqual(third["status"], "started")
        self.assertEqual(third["destination_location_id"], s4)
        self.assertEqual(third["segments_confirmed"], 2)
        self.assertEqual(self.position(), s2)

        done = self.advance(now=self.now + timedelta(seconds=650))
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["segments_confirmed"], 3)
        self.assertEqual(self.position(), s4)
        self.assertEqual(done["location_id"], s4)

        # 每段恰好一条 travel 记录，execution_id 与旅程账本一致，全部 ARRIVED。
        state = self.journey_state()
        segments = state["active"]["segments"]
        records = self.travels()
        self.assertEqual(len(records), 3)
        self.assertEqual(
            [record["execution_id"] for record in records],
            [segment["execution_id"] for segment in segments],
        )
        self.assertTrue(all(record["status"] == "ARRIVED" for record in records))
        self.assertEqual(
            [segment["status"] for segment in segments],
            ["confirmed", "confirmed", "confirmed"],
        )
        self.assertEqual(
            [segment["travel_id"] for segment in segments],
            [record["travel_id"] for record in records],
        )
        # 三次到达 = 三次 owner 写入，旅程服务没有额外写世界状态。
        self.assertEqual(self.world_revision(), revision_before + 3)

        # 完成后重复推进不产生任何写入或新记录。
        bytes_before = self.snapshot()
        again = self.advance(now=self.now + timedelta(seconds=700))
        self.assertEqual(again["status"], "completed")
        self.assertEqual(self.snapshot(), bytes_before)
        self.assertEqual(len(self.travels()), 3)

    def test_journey_targets_indoor_area_node(self) -> None:
        world = self.make_world(interiors=True, prefix="w02-indoor-")
        scene = schema.load_package(world.package_dir)[schema.SCENE_FILE]
        navigation = scene["navigation"]
        candidates = sorted(
            place for place, nav in navigation.items()
            if place != "home" and len(nav["areas"]) >= 2
        )
        self.assertTrue(candidates, "生成世界应至少有一栋带多个区域的建筑")
        place = candidates[0]
        entrance = navigation[place]["entrance"]
        area = next(
            item["area_id"] for item in navigation[place]["areas"]
            if item["area_id"] != entrance
        )
        node = f"{place}/{area}"
        active = self.wrd.active_world(world.env)
        route = active.route_to("home/bedroom", node)
        self.assertIsNotNone(route)
        self.assertEqual(route[-2], place)
        started = self.accept(node, world=world)
        self.assertEqual(started["route"], route)
        # 按注入时间逐段推进到最终目标。
        moment = self.now
        for step in range(len(route)):
            duration = active.route_duration_seconds(
                "home/bedroom" if step == 0 else route[step - 1], route[step]
            )
            moment = moment + timedelta(seconds=duration)
            result = self.advance(world=world, now=moment)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.position(world), node)
        self.assertEqual(len(self.travels(world)), len(route))

    def test_shiomi_builtin_world_journey_completes_with_aliases(self) -> None:
        home = Path(self.temp_dir(prefix="w02-shiomi-")) / "instance"
        from chiyo_bundle.world_read_service import initialize
        initialize(home)
        env = _clean_env()
        env.update({
            "CHIYO_WORLD_HOME": str(home),
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "true",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "true",
        })
        world = SimpleNamespace(home=home, env=env, ids={}, package_dir=None)
        # 名称解析属于工具层：引擎只接受规范节点 ID。
        active = self.wrd.active_world(env)
        target, reason = tool._resolve_target("便利店", active)
        self.assertEqual((target, reason), ("convenience_store", None))
        started = self.accept(target, world=world)
        self.assertEqual(started["status"], "started")
        self.assertEqual(started["route"], ["nearby_street", "convenience_store"])
        self.assertEqual(started["destination_location_id"], "nearby_street")
        self.assertEqual(started["estimated_minutes"], 5)
        second = self.advance(world=world, now=self.now + timedelta(seconds=300))
        self.assertEqual(second["destination_location_id"], "convenience_store")
        done = self.advance(world=world, now=self.now + timedelta(seconds=600))
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.position(world), "convenience_store")
        self.assertEqual(len(self.travels(world)), 2)

    # -- B：耐久意图写失败 ----------------------------------------------

    def test_durable_write_failure_never_calls_owner(self) -> None:
        world = self.world
        before = self.snapshot(world)
        from unittest.mock import patch
        # 注入真实的原子写失败（等价于磁盘满/权限错），断言 owner 完全没被调用。
        with patch.object(journey, "_write_state",
                          side_effect=journey.JourneyError("durable write failed")):
            with patch.object(self.wls, "apply_move_to",
                              wraps=self.wls.apply_move_to) as apply:
                result = self.accept(world.ids["s4"])
                apply.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "JOURNEY_STORE_WRITE_FAILED")
        self.assertEqual(self.snapshot(world), before)
        self.assertEqual(self.position(), "home")
        self.assertEqual(self.travels(), [])

    def test_unreadable_journey_store_never_calls_owner(self) -> None:
        world = self.world
        # 用目录占住账本路径：读取即失败，必须 fail closed 且不调 owner。
        self.state_dir(world).joinpath("world_journey.json").mkdir()
        before = self.owner_bytes(world)
        from unittest.mock import patch
        with patch.object(self.wls, "apply_move_to", wraps=self.wls.apply_move_to) as apply:
            result = self.accept(world.ids["s4"])
            apply.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "JOURNEY_STATE_CORRUPT")
        self.assertEqual(self.owner_bytes(world), before)
        self.assertEqual(self.position(), "home")
        self.assertEqual(self.travels(), [])

    # -- C：故障窗口（真实子进程终止）------------------------------------

    def test_window_intent_written_owner_not_called(self) -> None:
        world = self.world
        s1 = world.ids["s1"]
        target = world.ids["s4"]
        completed = self.run_child(
            "def killer(*args, **kwargs):\n"
            "    os._exit(9)\n"
            "wls.apply_move_to = killer\n"
            f"journey.accept_journey(target={target!r}, hermes_home={str(world.home)!r},\n"
            f"    environment={self.env_literal(world)}, now={self.iso(self.now)!r})\n",
            world,
        )
        self.assertEqual(completed.returncode, 9, completed.stderr)
        # 意图已落盘，owner 没被调用：段仍是 pending、没有 travel 记录。
        state = self.journey_state(world)
        self.assertEqual(state["active"]["status"], "pending")
        self.assertEqual(state["active"]["segments"][0]["status"], "pending")
        self.assertEqual(self.travels(), [])
        self.assertEqual(self.position(), "home")

        # 恢复：只提交这一段，位置与记录都只前进一次。
        resumed = self.advance()
        self.assertEqual(resumed["status"], "started")
        self.assertEqual(resumed["destination_location_id"], s1)
        self.assertEqual(len(self.travels()), 1)

    def test_window_owner_committed_journey_not_updated(self) -> None:
        world = self.world
        s1 = world.ids["s1"]
        target = world.ids["s4"]
        completed = self.run_child(
            "def killer(*args, **kwargs):\n"
            "    result = real_apply(*args, **kwargs)\n"
            "    os._exit(9)\n"
            "wls.apply_move_to = killer\n"
            f"journey.accept_journey(target={target!r}, hermes_home={str(world.home)!r},\n"
            f"    environment={self.env_literal(world)}, now={self.iso(self.now)!r})\n",
            world,
        )
        self.assertEqual(completed.returncode, 9, completed.stderr)
        records = self.travels()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "IN_PROGRESS")
        self.assertEqual(records[0]["origin_location_id"], "home")
        self.assertEqual(records[0]["destination_location_id"], s1)
        state = self.journey_state(world)
        # 旅程账本还停在"待提交"，但 owner 侧已经有这次提交的持久记录。
        self.assertEqual(state["active"]["segments"][0]["status"], "pending")

        # 恢复：认领既有记录（不重复提交），段状态补写为 started。
        resumed = self.advance()
        self.assertEqual(resumed["status"], "in_transit")
        self.assertEqual(resumed["travel_id"], records[0]["travel_id"])
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(
            self.journey_state(world)["active"]["segments"][0]["status"], "started"
        )

    def test_window_arrival_realized_segment_not_confirmed(self) -> None:
        world = self.world
        s1, s2 = world.ids["s1"], world.ids["s2"]
        target = world.ids["s4"]
        self.assertEqual(self.accept(target)["status"], "started")
        revision_after_start = self.world_revision()
        completed = self.run_child(
            f"import world_travel_delivery as wtd\n"
            f"wtd.realize_due_travel(hermes_home={str(world.home)!r},\n"
            f"    environment={self.env_literal(world)},\n"
            f"    now={self.iso(self.now + timedelta(seconds=300))!r})\n"
            "os._exit(9)\n",
            world,
        )
        self.assertEqual(completed.returncode, 9, completed.stderr)
        # 到达已经落地，但旅程还没确认这一段。
        self.assertEqual(self.position(), s1)
        self.assertEqual(self.world_revision(), revision_after_start + 1)
        self.assertEqual(
            self.journey_state(world)["active"]["segments"][0]["status"], "started"
        )

        resumed = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(resumed["status"], "started")
        self.assertEqual(resumed["destination_location_id"], s2)
        self.assertEqual(resumed["segments_confirmed"], 1)
        # 确认只补写账本，不再写世界状态。
        self.assertEqual(self.world_revision(), revision_after_start + 1)
        self.assertEqual(len(self.travels()), 2)

    def test_window_segment_confirmed_next_not_started(self) -> None:
        world = self.world
        s1, s2 = world.ids["s1"], world.ids["s2"]
        target = world.ids["s4"]
        completed = self.run_child(
            "calls = {'n': 0}\n"
            "def killer(*args, **kwargs):\n"
            "    calls['n'] += 1\n"
            "    if calls['n'] == 2:\n"
            "        os._exit(9)\n"
            "    return real_apply(*args, **kwargs)\n"
            "wls.apply_move_to = killer\n"
            f"journey.accept_journey(target={target!r}, hermes_home={str(world.home)!r},\n"
            f"    environment={self.env_literal(world)}, now={self.iso(self.now)!r})\n"
            f"journey.advance_journey(hermes_home={str(world.home)!r},\n"
            f"    environment={self.env_literal(world)},\n"
            f"    now={self.iso(self.now + timedelta(seconds=300))!r})\n",
            world,
        )
        self.assertEqual(completed.returncode, 9, completed.stderr)
        state = self.journey_state(world)
        self.assertEqual(state["active"]["segments"][0]["status"], "confirmed")
        self.assertEqual(state["active"]["segments"][1]["status"], "pending")
        self.assertEqual(state["active"]["current_segment"], 1)
        self.assertEqual(self.position(), s1)
        self.assertEqual(len(self.travels()), 1)

        resumed = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(resumed["status"], "started")
        self.assertEqual(resumed["destination_location_id"], s2)
        records = self.travels()
        self.assertEqual(len(records), 2)
        self.assertEqual(
            records[1]["execution_id"],
            self.journey_state(world)["active"]["segments"][1]["execution_id"],
        )

    # -- D：幂等与并发 ---------------------------------------------------

    def test_replay_and_idempotency_key_conflicts(self) -> None:
        world = self.world
        s2, s4 = world.ids["s2"], world.ids["s4"]
        first = self.accept(s4, request_key="k1")
        self.assertEqual(first["status"], "started")
        # 相同请求键 + 相同输入 → 返回既有旅程，不生成第二条。
        replay = self.accept(s4, request_key="k1")
        self.assertEqual(replay["journey_id"], first["journey_id"])
        self.assertEqual(replay["status"], "in_transit")
        self.assertEqual(replay["destination_location_id"], first["destination_location_id"])
        self.assertEqual(replay["travel_id"], first["travel_id"])
        self.assertEqual(len(self.travels()), 1)
        # 相同请求键 + 不同输入 → 冲突拒绝。
        conflict = self.accept(s2, request_key="k1")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason_code"], "REQUEST_KEY_CONFLICT")
        self.assertEqual(len(self.travels()), 1)
        # 无键但目标相同 → 视为重复请求，不生成第二条旅程。
        repeat = self.accept(s4)
        self.assertEqual(repeat["journey_id"], first["journey_id"])
        # 无键且目标不同 → 明确拒绝，不覆盖目标。
        locked = self.accept(s2)
        self.assertEqual(locked["status"], "rejected")
        self.assertEqual(locked["reason_code"], "JOURNEY_ALREADY_ACTIVE")
        self.assertEqual(locked["active_target_location_id"], s4)
        self.assertEqual(len(self.travels()), 1)
        # 同一时刻重复推进：第二次不产生新提交。
        self.assertEqual(self.advance(now=self.now + timedelta(seconds=300))["status"], "started")
        records = self.travels()
        self.assertEqual(len(records), 2)
        again = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(again["status"], "in_transit")
        self.assertEqual(len(self.travels()), 2)

    def test_concurrent_advance_starts_exactly_one_segment(self) -> None:
        world = self.world
        s2 = world.ids["s2"]
        self.assertEqual(self.accept(world.ids["s4"])["status"], "started")
        moment = self.now + timedelta(seconds=300)
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        results = context.Queue()
        environment = dict(world.env)

        def worker():
            barrier.wait()
            results.put(journey.advance_journey(
                hermes_home=str(world.home), environment=environment, now=moment
            ))

        processes = [context.Process(target=worker) for _ in range(2)]
        for process in processes:
            process.start()
        payloads = [results.get(timeout=60) for _ in processes]
        for process in processes:
            process.join(60)
            self.assertEqual(process.exitcode, 0)

        self.assertTrue(all(payload["journey_id"] for payload in payloads), payloads)
        records = self.travels()
        self.assertEqual(len(records), 2, records)
        state = self.journey_state(world)
        self.assertEqual(state["active"]["segments"][0]["status"], "confirmed")
        self.assertEqual(state["active"]["segments"][1]["status"], "started")
        self.assertEqual(state["active"]["current_segment"], 1)
        self.assertEqual(
            records[1]["execution_id"], state["active"]["segments"][1]["execution_id"]
        )
        self.assertEqual(records[1]["destination_location_id"], s2)
        self.assertEqual(self.world_revision(), 2)

    # -- E：第二段拒绝 / 证据未知 ----------------------------------------

    def test_second_segment_rejection_blocks_and_keeps_target(self) -> None:
        world = self.world
        s2, s4 = world.ids["s2"], world.ids["s4"]
        self.accept(s4)
        self.assertEqual(
            self.advance(now=self.now + timedelta(seconds=300))["destination_location_id"], s2
        )
        from unittest.mock import patch
        with patch.object(self.wls, "apply_move_to", return_value={
            "status": "rejected", "reason_code": "LOCATION_CLOSED"
        }) as apply:
            blocked = self.advance(now=self.now + timedelta(seconds=500))
            self.assertEqual(apply.call_count, 1)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["reason_code"], "LOCATION_CLOSED")
        self.assertEqual(blocked["target_location_id"], s4)
        self.assertEqual(blocked["destination_location_id"], s4)
        self.assertEqual(self.position(), s2)
        self.assertEqual(len(self.travels()), 2)
        self.assertEqual(
            self.journey_state(world)["active"]["segments"][2]["status"], "pending"
        )
        # 停止状态真实阻止启动新段：再次推进不再命令 owner。
        with patch.object(self.wls, "apply_move_to", wraps=self.wls.apply_move_to) as apply:
            again = self.advance(now=self.now + timedelta(seconds=900))
            apply.assert_not_called()
        self.assertEqual(again["status"], "blocked")
        self.assertEqual(len(self.travels()), 2)

    def test_external_position_change_holds_target_without_command(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])
        # 外部写入者（用规范 WorldStateStore）把位置改到别处。
        self.force_world_location(world, world.ids["s3"])
        before = self.owner_bytes()
        result = self.advance(now=self.now + timedelta(seconds=10))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason_code"], "WORLD_LOCATION_CHANGED")
        self.assertEqual(result["target_location_id"], world.ids["s4"])
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.owner_bytes(), before)

    def test_arrived_record_with_moved_position_is_evidence_conflict(self) -> None:
        world = self.world
        s1 = world.ids["s1"]
        self.accept(world.ids["s4"])
        realized = self.wtd.realize_due_travel(
            hermes_home=world.home, environment=world.env,
            now=self.now + timedelta(seconds=300),
        )
        self.assertEqual(realized["status"], "arrived")
        self.assertEqual(self.position(), s1)
        # 外部写入者把位置挪到别处：记录说已到达，位置却不一致。
        self.force_world_location(world, world.ids["s3"])
        before = self.owner_bytes()
        result = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason_code"], "EVIDENCE_CONFLICT")
        self.assertEqual(result["target_location_id"], world.ids["s4"])
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.owner_bytes(), before)

    # -- F：世界/图变化、外部在途、损坏 ----------------------------------

    def test_graph_change_blocks_before_any_command(self) -> None:
        world = self.world
        s1, s2 = world.ids["s1"], world.ids["s2"]
        self.accept(world.ids["s4"])
        self.change_edge_duration(world, s1, s2, 260)
        before = self.owner_bytes()
        result = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "GRAPH_CHANGED")
        self.assertEqual(result["target_location_id"], world.ids["s4"])
        self.assertFalse(result["graph_matches"])
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.owner_bytes(), before)
        # 只读观察如实报告指纹不匹配。
        self.assertFalse(self.observe()["graph_matches"])

    def test_world_id_mismatch_blocks(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])
        path = self.state_dir(world) / "world_journey.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["active"]["world_id"] = "other_world_123"
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        before = self.owner_bytes()
        result = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "WORLD_CHANGED")
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.owner_bytes(), before)

    def test_external_travel_in_progress_blocks(self) -> None:
        world = self.world
        s1 = world.ids["s1"]
        self.accept(world.ids["s4"])
        realized = self.wtd.realize_due_travel(
            hermes_home=world.home, environment=world.env,
            now=self.now + timedelta(seconds=300),
        )
        self.assertEqual(realized["status"], "arrived")
        external = self.wtd.start_travel(
            execution_id="external-probe-1", origin_location_id=s1,
            destination_location_id=world.ids["s3"],
            hermes_home=world.home, environment=world.env,
            now=self.now + timedelta(seconds=310),
        )
        self.assertEqual(external["status"], "started")
        before = self.owner_bytes()
        result = self.advance(now=self.now + timedelta(seconds=320))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "EXTERNAL_TRAVEL_IN_PROGRESS")
        self.assertEqual(result["target_location_id"], world.ids["s4"])
        self.assertEqual(len(self.travels()), 2)
        self.assertEqual(self.owner_bytes(), before)

    def test_corrupt_journey_store_fails_closed(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])
        path = self.state_dir(world) / "world_journey.json"
        path.write_text("{not json", encoding="utf-8")
        before = self.owner_bytes()
        observed = self.observe()
        self.assertEqual(observed["status"], "unavailable")
        self.assertEqual(observed["reason_code"], "JOURNEY_STATE_CORRUPT")
        advanced = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(advanced["status"], "unavailable")
        self.assertEqual(advanced["reason_code"], "JOURNEY_STATE_CORRUPT")
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(self.position(), "home")
        self.assertEqual(len(self.travels()), 1)

    def test_corrupt_travel_store_blocks_without_moving(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])
        travel_path = self.wtd.state_path(world.home)
        travel_path.write_text("{not json", encoding="utf-8")
        before = self.owner_bytes()
        result = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "TRAVEL_STATE_UNREADABLE")
        self.assertEqual(result["target_location_id"], world.ids["s4"])
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(self.position(), "home")

    # -- G：纯 observe ---------------------------------------------------

    def test_observe_is_read_only_with_and_without_state(self) -> None:
        world = self.world
        # 无旅程：不创建目录/文件。
        base = self.state_dir(world)
        listing = sorted(path.name for path in base.iterdir())
        result = self.observe()
        self.assertEqual(result["status"], "none")
        self.assertEqual(sorted(path.name for path in base.iterdir()), listing)
        self.assertFalse((base / "world_journey.json").exists())

        self.accept(world.ids["s4"])
        self.assertEqual(self.position(), "home")
        snapshot = self.snapshot()
        mtimes = self.mtimes()
        for moment in (
            self.now + timedelta(seconds=10),
            self.now + timedelta(seconds=300),
            self.now + timedelta(seconds=301),
            self.now + timedelta(days=3),
            self.now + timedelta(seconds=300),
        ):
            observed = self.observe(now=moment)
            self.assertEqual(observed["status"], "in_transit")
            self.assertEqual(observed["destination_location_id"], world.ids["s1"])
        # 到期前后反复读取：零写入、不结算到达、位置不变。
        self.assertEqual(self.snapshot(), snapshot)
        self.assertEqual(self.mtimes(), mtimes)
        self.assertEqual(self.position(), "home")
        records = self.travels()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "IN_PROGRESS")
        self.assertEqual(self.world_revision(), 1)


@posix_only
class JourneyToolIntegrationTest(JourneyTestCase):
    """H：工具接线、环境一致性、真名、旧意图退役、feature 门禁。"""

    def test_tool_creates_journey_and_reports_in_progress(self) -> None:
        world = self.world
        s1, s2, s4 = world.ids["s1"], world.ids["s2"], world.ids["s4"]
        started = self.move(s4)
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["status"], "started")
        self.assertEqual(started["journey_id"], self.journey_state()["active"]["journey_id"])
        self.assertEqual(started["destination_location_id"], s1)
        self.assertEqual(started["target_location_id"], s4)
        self.assertEqual(started["route"], [s1, s2, s4])
        # 工具不再写旧意图文件。
        self.assertFalse((self.state_dir(world) / "intended_target.json").exists())
        # 同目标再调用 → 返回既有旅程状态，不生成第二条。
        ongoing = self.move(s4, now=self.now + timedelta(seconds=61))
        self.assertEqual(ongoing["status"], "in_progress")
        self.assertEqual(ongoing["destination_location_id"], s1)
        self.assertEqual(ongoing["remaining_minutes"], 4)
        self.assertEqual(len(self.travels()), 1)
        # 不同目标 → 拒绝且不覆盖。
        locked = self.move(s2)
        self.assertEqual(locked["ok"], False)
        self.assertEqual(locked["reason_code"], "JOURNEY_TARGET_LOCKED")
        self.assertEqual(locked["active_target_location_id"], s4)
        self.assertEqual(len(self.travels()), 1)

    def test_tool_flag_off_never_teleports_active_journey(self) -> None:
        world = self.world
        s1 = world.ids["s1"]
        self.move(world.ids["s4"])
        off_env = dict(world.env)
        off_env["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "0"
        before = self.snapshot()
        blocked = self.move(world.ids["s4"], env=off_env)
        self.assertEqual(blocked["ok"], False)
        self.assertEqual(blocked["reason_code"], "JOURNEY_ACTIVE_TRAVEL_DISABLED")
        self.assertEqual(blocked["active_target_location_id"], world.ids["s4"])
        self.assertEqual(self.position(), "home")
        self.assertEqual(self.snapshot(), before)
        # 引擎层面同样拒绝：关闭计时时不启动新段。
        advanced = journey.advance_journey(
            hermes_home=world.home, environment=off_env, now=self.now
        )
        # W03：feature gate 关闭现在是**非终态**拒绝（feature_disabled），不再把旅程写成
        # blocked 终态——原来的"不启动新段/不瞬移"保障不变，并额外保证开关恢复后可续行。
        self.assertEqual(advanced["status"], "feature_disabled")
        self.assertEqual(advanced["reason_code"], "TRAVEL_TIME_DISABLED")
        self.assertEqual(advanced["target_location_id"], world.ids["s4"])
        self.assertEqual(self.position(), "home")
        self.assertEqual(len(self.travels()), 1)
        # 关闭计时只是拒绝推进，不把旅程写成终态：开关恢复后仍可续行。
        self.assertEqual(self.observe()["status"], "in_transit")
        self.assertEqual(self.journey_state()["active"]["status"], "in_transit")
        # 重新开启后照常续行。
        self.assertEqual(
            self.move(world.ids["s4"], now=self.now + timedelta(seconds=300))["status"],
            "started",
        )
        self.assertEqual(self.position(), s1)

    def test_request_environment_wins_for_activity_and_journey(self) -> None:
        world = self.world
        other = Path(self.temp_dir(prefix="w02-xinxi-")) / "xinxi"
        other.mkdir(parents=True)
        (other / "current_activity.json").write_text(
            json.dumps({"status": "active"}), encoding="utf-8"
        )
        broken = Path(self.temp_dir(prefix="w02-broken-")) / "missing-world"
        from unittest.mock import patch
        with patch.dict(os.environ, {
            "XINXI_DIR": str(other),
            "CHIYO_WORLD_PACKAGE": str(broken),
            "CHIYO_WORLD_HOME": str(self.temp_dir(prefix="w02-elsewhere-")),
        }):
            self.wrd.reset_cache()
            result = self.move(world.ids["s4"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "started")
        # 请求环境的 XINXI_DIR 为空 → 使用实例自己的 xinxi（不存在 → 无活动冲突）。
        self.assertFalse((other / "current_activity.json").read_text(encoding="utf-8") == "")
        self.assertEqual(self.position(), "home")
        self.assertEqual(len(self.travels()), 1)

    def test_label_prefers_real_name_in_custom_world(self) -> None:
        world = self.make_world(home_name="海边的家", prefix="w02-label-", travel=False)
        active = self.wrd.active_world(world.env)
        self.assertTrue(active.is_custom)
        self.assertEqual(tool._label("home", active), "海边的家")
        shiomi = self.wrd.active_world(_clean_env())
        self.assertEqual(tool._label("home", shiomi), "家")
        self.assertEqual(tool._label("convenience_store", shiomi), "便利店")
        # 即时模式下按 ID 移动：消息必须用当前世界的真名，而不是汐见市旧短名。
        arrived = self.move(world.ids["s1"], world=world)
        self.assertEqual(arrived["status"], "arrived")
        back_home = self.move("home", world=world)
        self.assertEqual(back_home["status"], "arrived")
        self.assertIn("海边的家", back_home["message"])
        self.assertNotIn("千代", back_home["message"])

    def test_legacy_intent_file_is_inert_and_byte_identical(self) -> None:
        world = self.world
        intent = self.state_dir(world) / "intended_target.json"
        payload = json.dumps({"target": world.ids["dead"][0], "set_at": 123.0},
                             ensure_ascii=False).encode("utf-8")
        intent.write_bytes(payload)
        started = self.move(world.ids["s4"])
        self.assertEqual(started["status"], "started")
        # 旧文件不驱动移动：路线仍由用户目标决定。
        self.assertEqual(started["route"][-1], world.ids["s4"])
        self.assertNotIn(world.ids["dead"][0], started["route"])
        self.assertEqual(intent.read_bytes(), payload)
        self.assertTrue(self.observe()["legacy_intent_present"])
        # 走完整段旅程后字节仍不变。
        self.advance(now=self.now + timedelta(seconds=300))
        self.advance(now=self.now + timedelta(seconds=500))
        self.advance(now=self.now + timedelta(seconds=650))
        self.assertEqual(intent.read_bytes(), payload)
        self.assertEqual(self.position(), world.ids["s4"])

    def test_feature_gate_disabled_starts_nothing(self) -> None:
        world = self.world
        disabled = dict(world.env)
        disabled["CHIYO_WORLD_LIVING_SKELETON_ENABLED"] = "0"
        before = self.snapshot()
        result = self.move(world.ids["s4"], env=disabled)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["reason_code"], "FEATURE_DISABLED")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.state_dir(world) / "world_journey.json").exists())
        self.assertEqual(self.travels(), [])


@posix_only
class HeartbeatEntryTest(JourneyTestCase):
    """旧心跳入口：显式参数、一次性推进、无自主目标。"""

    def heartbeat(self, argv):
        from scripts import life_heartbeat
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = life_heartbeat.main(argv)
        return code, stream.getvalue()

    def test_requires_explicit_world_home_and_never_moves_on_bad_args(self) -> None:
        world = self.world
        before = self.snapshot()
        with self.assertRaises(SystemExit) as missing:
            self.heartbeat([])
        self.assertEqual(missing.exception.code, 2)
        code, output = self.heartbeat(["--world-home", "relative/world"])
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        code, output = self.heartbeat(
            ["--world-home", str(world.home), "--env", "not-a-pair"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.state_dir(world) / "world_journey.json").exists())
        self.assertEqual(self.travels(), [])

    def test_one_shot_advance_only_continues_existing_journey(self) -> None:
        world = self.world
        # W03：入口不再无条件强制启用行为特性，feature gate 必须显式给出。
        gates = ["--env", "CHIYO_WORLD_LIVING_SKELETON_ENABLED=1",
                 "--env", "CHIYO_WORLD_TRAVEL_TIME_ENABLED=1"]
        empty_code, empty_output = self.heartbeat(
            ["--world-home", str(world.home), "--world-package", str(world.package_dir),
             *gates]
        )
        self.assertEqual(empty_code, 0)
        self.assertEqual(json.loads(empty_output)["status"], "none")
        self.assertFalse((self.state_dir(world) / "world_journey.json").exists())

        self.move(world.ids["s4"])
        code, output = self.heartbeat([
            "--world-home", str(world.home),
            "--world-package", str(world.package_dir),
            "--now", self.iso(self.now + timedelta(seconds=300)),
            *gates,
        ])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["status"], "started")
        self.assertEqual(payload["destination_location_id"], world.ids["s2"])
        self.assertEqual(payload["segments_confirmed"], 1)
        self.assertEqual(len(self.travels()), 2)
        # 旧入口不再生成任何自主目标：目标始终是用户提出的那个。
        self.assertEqual(payload["target_location_id"], world.ids["s4"])


if __name__ == "__main__":
    unittest.main()
