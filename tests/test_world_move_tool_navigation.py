"""W01：world_move 通用世界导航（自定义世界、多段选路、室内区域、歧义、在途语义）。

覆盖施工单验收：自定义非汐见市地点、≥3 段路线、含环图、等成本稳定选路、
不可达、未知/歧义名称、入口区规范化、室内区域移动、请求 environment 与进程
环境不同、在途不改向、中途 owner 拒绝后不续走、旧别名兼容与不同世界不串扰。

需要 POSIX（fcntl 文件锁）与 tz 数据库；原生 Windows 自动跳过运行时用例。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.worldgen import schema
from components.worldgen.instance import init_instance
from components.worldgen.interior import apply_interiors_to_package
from components.worldgen.loader import load as load_package
from components.worldgen.tests._setup import WorldGenTestCase, make_valid_docs

from chiyo_bundle import world_move_tool as tool

posix_only = unittest.skipIf(
    os.name == "nt", "世界移动链路需要 POSIX 文件锁（在 WSL/Linux 上运行）"
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
class CustomWorldMoveToolTest(WorldGenTestCase):
    """自定义（非汐见市）世界上的世界导航与移动提交。"""

    def setUp(self) -> None:
        super().setUp()
        import world_living_skeleton as wls
        import world_runtime_data as wrd
        import world_travel_delivery as wtd

        self.wls, self.wrd, self.wtd = wls, wrd, wtd
        self.wrd.reset_cache()
        self.now = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
        self.package_dir = self.temp_package(
            make_valid_docs(seed=929711, theme="coastal_town"), prefix="w01-road-"
        )
        self.ids = self._rewrite_roads(self.package_dir)
        self.wrd.reset_cache()
        self.package = load_package(self.package_dir)
        self.home = Path(self.temp_dir(prefix="w01-road-inst-")) / "instance"
        init_instance(self.package_dir, self.home)
        self.env = self._env(travel=False)

    def tearDown(self) -> None:
        self.wrd.reset_cache()
        super().tearDown()

    # -- 夹具 ---------------------------------------------------------------

    def _rewrite_roads(self, package_dir: Path) -> dict:
        """把生成世界的道路图改写成可控小图（保留全部地点 ID 与营业规则）。

        图：home-s1(300) / s1-s2(200) / s1-s3(100) / s2-s3(100) / s2-s4(150)，
        其余地点各自单边挂在 s4(900)。s1 命名为"公园"（与汐见市旧别名同名），
        s2 与第一个死胡同地点同名"旧市场"（构造真实歧义）。
        """
        docs = schema.load_package(package_dir)
        topology = docs["topology.json"]
        location_ids = sorted(item["location_id"] for item in topology["locations"])
        self.assertIn("home", location_ids)
        others = [location_id for location_id in location_ids if location_id != "home"]
        self.assertGreaterEqual(len(others), 5)
        s1, s2, s3, s4 = others[0], others[1], others[2], others[3]
        dead = others[4:]
        self.assertLess(s2, s3)  # 等成本时的字典序平局由节点序列决定
        edges = [
            ("home", s1, 300),
            (s1, s2, 200),
            (s1, s3, 100),
            (s2, s3, 100),
            (s2, s4, 150),
        ]
        edges.extend((location_id, s4, 900) for location_id in dead)
        adjacency = {location_id: set() for location_id in location_ids}
        for first, second, _ in edges:
            adjacency[first].add(second)
            adjacency[second].add(first)
        by_id = {item["location_id"]: item for item in topology["locations"]}
        for location_id, item in by_id.items():
            item["reachable"] = sorted(adjacency[location_id])
        by_id[s1]["name"] = "公园"
        by_id[s2]["name"] = "旧市场"
        by_id[dead[0]]["name"] = "旧市场"
        topology["edges"] = [
            {"from": first, "to": second, "walk_seconds": seconds}
            for first, second, seconds in edges
        ]
        schema.write_package(package_dir, docs)
        return {"s1": s1, "s2": s2, "s3": s3, "s4": s4, "dead": dead}

    def _env(self, *, travel: bool, package_dir=None, instance_home=None) -> dict:
        env = _clean_env()
        env.update(
            {
                "CHIYO_WORLD_PACKAGE": str(package_dir or self.package_dir),
                "CHIYO_WORLD_HOME": str(instance_home or self.home),
                "CHIYO_WORLD_ID": self.package.world_id,
                "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
                "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1" if travel else "0",
                "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
                "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
                "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
            }
        )
        return env

    def move(self, location, env=None, now=None):
        return json.loads(
            tool.move_handler(
                {"location": location}, environment=env or self.env, now=now or self.now
            )
        )

    def world(self, env=None):
        return self.wrd.active_world(env or self.env)

    def position(self, env=None) -> str:
        env = env or self.env
        state = self.wls.read_move_state(
            hermes_home=env["CHIYO_WORLD_HOME"], environment=env, now=self.now
        )
        return state["world_location_id"]

    def state_path(self, env=None) -> Path:
        return Path((env or self.env)["CHIYO_WORLD_HOME"]) / "data/world/world_state.json"

    def state_bytes(self, env=None) -> bytes:
        return self.state_path(env).read_bytes()

    def revision(self, env=None) -> int:
        return json.loads(self.state_bytes(env).decode("utf-8"))["revision"]

    def travel_bytes(self, env=None) -> bytes:
        env = env or self.env
        return self.wtd.state_path(env["CHIYO_WORLD_HOME"]).read_bytes()

    # -- 选路语义 -----------------------------------------------------------

    def test_route_is_shortest_deterministic_and_tie_broken_by_node_order(self) -> None:
        world = self.world()
        s1, s2, s3, s4 = (self.ids[key] for key in ("s1", "s2", "s3", "s4"))

        # 三段路线：home → s1 → s2 → s4。
        route = world.route_to("home", s4)
        self.assertEqual(route, [s1, s2, s4])
        # 含环：s1-s2-s3-s1 三条边都在真实图上。
        self.assertIn(s2, world.reachable_of(s1))
        self.assertIn(s3, world.reachable_of(s1))
        self.assertIn(s2, world.reachable_of(s3))
        self.assertIn(s1, world.reachable_of(s3))
        # 等成本：经 s3 的绕行总耗时完全相同，但节点序列更大 → 不选。
        direct = (
            world.route_duration_seconds("home", s1)
            + world.route_duration_seconds(s1, s2)
            + world.route_duration_seconds(s2, s4)
        )
        detour = (
            world.route_duration_seconds("home", s1)
            + world.route_duration_seconds(s1, s3)
            + world.route_duration_seconds(s3, s2)
            + world.route_duration_seconds(s2, s4)
        )
        self.assertEqual(direct, detour)
        self.assertEqual(world.route_to("home", s2), [s1, s2])
        self.assertEqual(world.route_to("home", s3), [s1, s3])
        # 反向同样按 (成本, 节点序列) 全序取最优。
        self.assertEqual(world.route_to(s4, "home"), [s2, s1, "home"])
        # 稳定性：同一世界同一对节点，多次调用结果一致。
        self.assertEqual({tuple(world.route_to("home", s4)) for _ in range(3)}, {(s1, s2, s4)})
        self.assertEqual(world.route_to("home", "home"), [])
        self.assertIsNone(world.route_to("home", "不存在的节点"))

    # -- 即时模式（关闭真实步行计时）----------------------------------------

    def test_instant_multi_leg_move_commits_every_leg_through_the_owner(self) -> None:
        s1, s2, s4 = (self.ids[key] for key in ("s1", "s2", "s4"))
        before = self.revision()
        with patch.object(self.wls, "apply_move_to", wraps=self.wls.apply_move_to) as apply:
            result = self.move(s4)
        self.assertEqual([call.args[0] for call in apply.call_args_list], [s1, s2, s4])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "arrived")
        self.assertEqual(result["route"], [s1, s2, s4])
        self.assertEqual(result["location_id"], s4)
        self.assertEqual(self.position(), s4)
        # 三次提交 = 三次 owner 写入，工具本身不碰 world_state。
        self.assertEqual(self.revision(), before + 3)

    def test_instant_move_stops_at_first_owner_rejection_and_keeps_position(self) -> None:
        s1, s2, s4 = (self.ids[key] for key in ("s1", "s2", "s4"))
        real = self.wls.apply_move_to
        calls: list[str] = []

        def flaky(step, **kwargs):
            calls.append(step)
            if len(calls) == 2:
                return {"status": "rejected", "reason_code": "LOCATION_CLOSED"}
            return real(step, **kwargs)

        with patch.object(self.wls, "apply_move_to", side_effect=flaky):
            result = self.move(s4)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "LOCATION_CLOSED")
        self.assertEqual(calls, [s1, s2])  # 第三段没有被提交
        self.assertEqual(result["committed_route"], [s1])
        self.assertEqual(result["location_id"], s1)
        self.assertEqual(result["requested_location_id"], s4)
        # 世界状态如实停在最后确认的那一段。
        self.assertEqual(self.position(), s1)

    # -- 真实步行计时（只提交第一段）----------------------------------------

    def test_timed_move_submits_only_the_first_leg_and_keeps_origin(self) -> None:
        env = self._env(travel=True)
        s1, s2, s4 = (self.ids[key] for key in ("s1", "s2", "s4"))
        revision_before = self.revision(env)
        with patch.object(self.wls, "apply_move_to", wraps=self.wls.apply_move_to) as apply:
            result = self.move(s4, env=env)
        self.assertEqual([call.args[0] for call in apply.call_args_list], [s1])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "started")
        self.assertEqual(result["destination_location_id"], s1)
        self.assertEqual(result["requested_location_id"], s4)
        self.assertEqual(result["estimated_minutes"], 5)
        self.assertEqual(result["route"], [s1, s2, s4])
        self.assertIn("中转", result["message"])
        # 起点没被瞬移；在途状态由 travel 记录表达，不写 world_state。
        self.assertEqual(self.position(env), "home")
        self.assertEqual(self.revision(env), revision_before)

    def test_timed_move_in_progress_refuses_redirect_and_keeps_travel(self) -> None:
        env = self._env(travel=True)
        s1, s4 = self.ids["s1"], self.ids["s4"]
        self.assertEqual(self.move(s4, env=env)["destination_location_id"], s1)
        travel_before = self.travel_bytes(env)
        with patch.object(self.wls, "apply_move_to", wraps=self.wls.apply_move_to) as apply:
            ongoing = self.move(s4, env=env, now=self.now + timedelta(seconds=61))
            apply.assert_not_called()
        self.assertTrue(ongoing["ok"])
        self.assertEqual(ongoing["status"], "in_progress")
        self.assertEqual(ongoing["destination_location_id"], s1)
        self.assertEqual(ongoing["remaining_minutes"], 4)
        self.assertEqual(self.travel_bytes(env), travel_before)

    def test_timed_move_owner_rejection_stops_immediately(self) -> None:
        env = self._env(travel=True)
        s1, s4 = self.ids["s1"], self.ids["s4"]
        before = self.state_bytes(env)
        with patch.object(self.wls, "apply_move_to", return_value={
            "status": "rejected", "reason_code": "LOCATION_CLOSED"
        }) as apply:
            result = self.move(s4, env=env)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "LOCATION_CLOSED")
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(result["location_id"], "home")
        self.assertEqual(self.state_bytes(env), before)

    # -- 名称解析：真实目标优先、歧义拒绝、旧别名兼容 -----------------------

    def test_unique_name_beats_legacy_alias_in_custom_world(self) -> None:
        s1, s2 = self.ids["s1"], self.ids["s2"]
        world = self.world()
        self.assertNotIn("nearby_street", world.all_nodes())
        # s1 的真实名称就是"公园"：必须走到 s1，不能被旧别名 park 顶掉。
        result = self.move("公园")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["route"], [s1])
        self.assertEqual(self.position(), s1)
        # 名称索引里没有"公园"以外的同义项。
        self.assertEqual(tool._resolve_target("公园", world), (s1, None))
        # 世界内唯一名称同样可用。
        self.assertEqual(tool._resolve_target(s2, world), (s2, None))
        self.assertEqual(tool._resolve_target("  公园  ", world), (s1, None))

    def test_legacy_alias_still_works_and_never_crosses_worlds(self) -> None:
        world = self.world()
        # 汐见市旧别名在本世界有对应节点（home）时照常工作。
        self.assertEqual(tool._resolve_target("家", world), ("home", None))
        self.assertEqual(self.move("家")["status"], "already_there")
        self.assertEqual(tool._resolve_target(" HOME ", world), ("home", None))
        # 本世界没有 nearby_street：旧别名不得把目标悄悄落到别处。
        before = self.state_bytes()
        rejected = self.move("街道")
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["reason_code"], "UNKNOWN_LOCATION")
        self.assertEqual(self.state_bytes(), before)

    def test_ambiguous_name_is_refused_without_moving(self) -> None:
        world = self.world()
        s2, dead = self.ids["s2"], self.ids["dead"][0]
        self.assertNotEqual(s2, dead)
        self.assertEqual(
            {node for node in world.all_nodes() if world.location(node)["name"] == "旧市场"},
            {s2, dead},
        )
        before = self.state_bytes()
        result = self.move("旧市场")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "AMBIGUOUS_LOCATION")
        self.assertEqual(self.state_bytes(), before)
        # 歧义不影响按 ID 精确移动。
        self.assertEqual(self.move(s2)["status"], "arrived")
        self.assertEqual(self.position(), s2)

    def test_unknown_and_invalid_targets_never_fall_back_to_home(self) -> None:
        before = self.state_bytes()
        for args in (
            {"location": "不存在的名字"},
            {"location": "x" * 97},
            {"location": ""},
            {"location": 42},
            {"location": "home", "home": "/tmp"},
            {},
            None,
        ):
            with self.subTest(args=args):
                result = json.loads(
                    tool.move_handler(args, environment=self.env, now=self.now)
                )
                self.assertFalse(result["ok"])
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.position(), "home")

    # -- 请求 environment 与进程 environment 不同 ---------------------------

    def test_request_environment_wins_over_process_environment(self) -> None:
        s1 = self.ids["s1"]
        broken = Path(self.temp_dir(prefix="w01-broken-")) / "missing-world"
        other_home = Path(self.temp_dir(prefix="w01-other-inst-")) / "instance"
        # 进程环境指向一个不可读的世界包：基线实现会因此无法确认目标目录。
        with patch.dict(os.environ, {
            "CHIYO_WORLD_PACKAGE": str(broken),
            "CHIYO_WORLD_HOME": str(other_home),
        }):
            with self.assertRaises(Exception):
                self.wrd.active_world(dict(os.environ))
            result = tool.move_handler({"location": s1}, environment=self.env, now=self.now)
            self.wrd.reset_cache()
        self.assertTrue(json.loads(result)["ok"], result)
        self.assertEqual(self.position(), s1)
        self.assertEqual(
            json.loads(self.state_bytes().decode("utf-8"))["world_id"],
            self.package.world_id,
        )
        # 进程环境里的另一个 world home 完全没被碰过。
        self.assertFalse(other_home.exists())


@posix_only
class IndoorNavigationMoveToolTest(WorldGenTestCase):
    """室内区域导航：入口区规范化、跨建筑多段路线、不可达拒绝。"""

    def setUp(self) -> None:
        super().setUp()
        import world_living_skeleton as wls
        import world_runtime_data as wrd
        import world_travel_delivery as wtd

        self.wls, self.wrd, self.wtd = wls, wrd, wtd
        self.wrd.reset_cache()
        self.now = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
        self.package_dir = self.temp_package(
            make_valid_docs(seed=929711, theme="coastal_town"), prefix="w01-indoor-"
        )
        apply_interiors_to_package(self.package_dir)
        self.wrd.reset_cache()
        self.package = load_package(self.package_dir)
        scene = schema.load_package(self.package_dir)[schema.SCENE_FILE]
        navigation = scene["navigation"]
        candidates = sorted(
            place_id
            for place_id, nav in navigation.items()
            if place_id != "home" and len(nav["areas"]) >= 2
        )
        self.assertTrue(candidates, "生成世界应至少有一栋带多个区域的建筑")
        self.place = candidates[0]
        self.entrance = navigation[self.place]["entrance"]
        self.area = next(
            area["area_id"]
            for area in navigation[self.place]["areas"]
            if area["area_id"] != self.entrance
        )
        self.home = Path(self.temp_dir(prefix="w01-indoor-inst-")) / "instance"
        init_instance(self.package_dir, self.home)
        self.env = _clean_env()
        self.env.update({
            "CHIYO_WORLD_PACKAGE": str(self.package_dir),
            "CHIYO_WORLD_HOME": str(self.home),
            "CHIYO_WORLD_ID": self.package.world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "0",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
        })
        self.initial = self.position()

    def tearDown(self) -> None:
        self.wrd.reset_cache()
        super().tearDown()

    def move(self, location, env=None):
        return json.loads(
            tool.move_handler(
                {"location": location}, environment=env or self.env, now=self.now
            )
        )

    def world(self, env=None):
        return self.wrd.active_world(env or self.env)

    def position(self, env=None) -> str:
        env = env or self.env
        return self.wls.read_move_state(
            hermes_home=env["CHIYO_WORLD_HOME"], environment=env, now=self.now
        )["world_location_id"]

    def test_indoor_area_move_goes_through_the_entrance(self) -> None:
        world = self.world()
        node = f"{self.place}/{self.area}"
        start = self.position()
        route = world.route_to(start, node)
        self.assertIsNotNone(route, "室内区域应可达")
        self.assertEqual(route[-1], node)
        # 路径必须尊重真实连通：进非入口区域前先到该建筑的入口节点。
        self.assertEqual(route[-2], self.place)
        for origin, destination in zip([start] + route, route):
            self.assertIn(destination, world.reachable_of(origin),
                          f"{origin} → {destination}")
        result = self.move(node)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "arrived")
        self.assertEqual(result["location_id"], node)
        self.assertEqual(result["route"], route)
        self.assertEqual(self.position(), node)

    def test_entrance_area_is_normalised_to_the_place_node(self) -> None:
        world = self.world()
        entrance_node = f"{self.place}/{self.entrance}"
        # 入口区与 place 节点同义，规范化为 place。
        self.assertEqual(world.canonical_node(entrance_node), self.place)
        self.assertEqual(self.move(entrance_node)["location_id"], self.place)
        self.assertEqual(self.position(), self.place)
        self.assertEqual(self.move(entrance_node)["status"], "already_there")

    def test_unknown_area_and_disconnected_area_are_refused(self) -> None:
        world = self.world()
        self.assertIsNone(world.canonical_node(f"{self.place}/ghost_area"))
        before = (Path(self.env["CHIYO_WORLD_HOME"]) / "data/world/world_state.json").read_bytes()
        unknown = self.move(f"{self.place}/ghost_area")
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["reason_code"], "UNKNOWN_LOCATION")
        self.assertEqual(self.position(), self.initial)

        # 断开该建筑的区域连接 → 非入口区域不可达（校验允许，连通性由导航声明决定）。
        docs = schema.load_package(self.package_dir)
        scene = docs[schema.SCENE_FILE]
        scene["navigation"][self.place]["connections"] = []
        schema.write_package(self.package_dir, docs)
        self.wrd.reset_cache()
        edited = Path(self.temp_dir(prefix="w01-indoor-inst2-")) / "instance"
        init_instance(self.package_dir, edited)
        env = dict(self.env)
        env["CHIYO_WORLD_HOME"] = str(edited)
        disconnected = self.move(f"{self.place}/{self.area}", env=env)
        self.assertFalse(disconnected["ok"])
        self.assertEqual(disconnected["reason_code"], "LOCATION_NOT_REACHABLE")
        self.assertEqual(self.position(env), self.initial)
        # 原实例的状态未被这次拒绝改写。
        self.assertEqual(
            (Path(self.env["CHIYO_WORLD_HOME"]) / "data/world/world_state.json").read_bytes(),
            before,
        )


if __name__ == "__main__":
    unittest.main()
