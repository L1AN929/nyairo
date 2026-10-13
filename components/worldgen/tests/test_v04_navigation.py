"""v0.4 室内导航、局部感知与批量质检测试。

需要 POSIX（fcntl）与 tz 数据库；原生 Windows 自动跳过运行时用例。
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.instance import init_instance
from components.worldgen.interior import apply_interiors_to_package
from components.worldgen.loader import load as load_package

posix_runtime = unittest.skipIf(
    os.name == "nt", "rc9 世界运行时需要 POSIX 文件锁（在 WSL/Linux 上运行）"
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _runtime_env(package_dir: Path, instance_home: Path, world_id: str, *, travel: bool) -> dict:
    env = dict(os.environ)
    for key in ("CHIYO_WORLD_PACKAGE", "CHIYO_WORLD_HOME", "CHIYO_WORLD_ID"):
        env.pop(key, None)
    env.update(
        {
            "CHIYO_WORLD_PACKAGE": str(package_dir),
            "CHIYO_WORLD_HOME": str(instance_home),
            "CHIYO_WORLD_ID": world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1" if travel else "0",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "1",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "1",
        }
    )
    return env


class WorldWithNavigationTest(WorldGenTestCase):
    """带导航的测试世界工厂（室内已生成）。"""

    def make_navigated_world(self, seed: int = 929711) -> Path:
        directory = self.temp_package(make_valid_docs(seed=seed), prefix="wg-nav-")
        apply_interiors_to_package(directory)
        return directory


@posix_runtime
class IndoorNavigationTest(WorldWithNavigationTest):
    """验收 A：室内真实导航（住宅→街道→书店→书架区→阅读区→离开）。"""

    def setUp(self) -> None:
        super().setUp()
        import world_living_skeleton as wls
        import world_runtime_data as wrd
        import world_travel_delivery as wtd

        self.wls = wls
        self.wrd = wrd
        self.wtd = wtd
        wrd.reset_cache()
        self.package_dir = self.make_navigated_world()
        self.package = load_package(self.package_dir)
        self.home = Path(self.temp_dir(prefix="wg-nav-inst-")) / "instance"
        init_instance(self.package_dir, self.home)
        self.env = _runtime_env(self.package_dir, self.home, self.package.world_id, travel=True)
        self.tz = ZoneInfo(self.package.timezone_name)

    def tearDown(self) -> None:
        self.wrd.reset_cache()
        super().tearDown()

    def _at(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 10, 14, hour, minute, tzinfo=self.tz)

    def _walk(self, target: str, now: datetime) -> datetime:
        plan = self.wls.plan_move_to(target, hermes_home=self.home, environment=self.env, now=now)
        self.assertEqual(plan["status"], "ready", f"{target}: {plan}")
        duration = int((plan.get("travel") or {}).get("route_duration_seconds") or 30)
        applied = self.wls.apply_move_to(target, hermes_home=self.home, environment=self.env, now=now)
        self.assertEqual(applied["status"], "applied", f"{target}: {applied}")
        arrival = self.wtd.realize_due_travel(
            hermes_home=self.home, environment=self.env, now=now + __import__("datetime").timedelta(seconds=duration)
        )
        self.assertEqual(arrival["status"], "arrived")
        return now + __import__("datetime").timedelta(seconds=duration)

    def _where(self) -> str:
        state = self.wls.read_move_state(hermes_home=self.home, environment=self.env)
        return state["world_location_id"]

    def test_full_indoor_journey(self) -> None:
        # 初始位置是 home/bedroom（室内区域）：先到客厅，再上街。
        now = self._walk("home/living", self._at(12, 50))
        # 入口区与 place 节点同义：state_node 归一化为 "home"。
        self.assertIn(self._where(), ("home", "home/living"))
        now = self._walk("street_1", self._at(13))
        self.assertEqual(self._where(), "street_1")
        now = self._walk("bookstore", now)
        self.assertEqual(self._where(), "bookstore")
        now = self._walk("bookstore/reading_area", now)
        self.assertEqual(self._where(), "bookstore/reading_area")
        now = self._walk("bookstore", now)
        self.assertEqual(self._where(), "bookstore")
        now = self._walk("street_1", now)
        self.assertEqual(self._where(), "street_1")

    def test_strict_navigation_from_inner_area(self) -> None:
        self._walk("home/living", self._at(12, 50))
        self._walk("street_1", self._at(13))
        self._walk("bookstore", self._at(13, 5))
        self._walk("bookstore/reading_area", self._at(13, 10))
        # 非入口区域不能直达街道（必须先回到入口）。
        plan = self.wls.plan_move_to("street_1", hermes_home=self.home, environment=self.env, now=self._at(13, 15))
        self.assertEqual(plan["status"], "rejected")
        self.assertEqual(plan["reason_code"], "LOCATION_NOT_REACHABLE")
        # 入口区与街道连通。
        self.assertIn("bookstore", self.wrd.active_world(self.env).reachable_of("bookstore/reading_area"))

    def test_unknown_area_rejected(self) -> None:
        plan = self.wls.plan_move_to(
            "bookstore/ghost_area", hermes_home=self.home, environment=self.env, now=self._at(13)
        )
        self.assertEqual(plan["status"], "rejected")
        self.assertEqual(plan["reason_code"], "UNKNOWN_LOCATION")

    def test_persistence_after_indoor_moves(self) -> None:
        """验收 D：在室内保存、重启后位置与移动记录仍存在。"""
        now = self._walk("home/living", self._at(12, 50))
        now = self._walk("street_1", self._at(13))
        now = self._walk("bookstore", now)
        now = self._walk("bookstore/reading_area", now)
        revision_before = self.wls.read_move_state(
            hermes_home=self.home, environment=self.env
        )["world_revision"]
        self.wrd.reset_cache()  # 模拟重启后的重新解析
        state = self.wls.read_move_state(
            hermes_home=self.home, environment=self.env, now=now
        )
        self.assertEqual(state["world_location_id"], "bookstore/reading_area")
        self.assertEqual(state["world_revision"], revision_before)
        travels = self.wtd.load_state(self.home, self.env)
        self.assertEqual(len(travels["travels"]), 4)

    def test_perception_changes_with_position(self) -> None:
        """验收 B：观察内容随位置变化，全部来自真实世界包。"""
        import world_runtime_data as wrd

        self._walk("home/living", self._at(12, 50))
        self._walk("street_1", self._at(13))
        self._walk("bookstore", self._at(13, 5))
        self._walk("bookstore/reading_area", self._at(13, 10))
        observation = self.wls.observe_world(
            hermes_home=self.home, environment=self.env, now=self._at(13, 20)
        )
        env = observation.get("local_environment")
        self.assertIsNotNone(env)
        self.assertEqual(env["area"]["area_id"], "reading_area")
        names = {item["name"] for item in env["furniture"]}
        self.assertIn("阅读桌", names)
        self.assertTrue(all(item["object_id"].startswith("bookstore_") for item in env["furniture"]))
        # 感知边界：阅读区看不到其他建筑家具。
        self.assertFalse(any("home_" in item["object_id"] for item in env["furniture"]))
        # 离开建筑后观察内容按位置变化。
        now = self._walk("bookstore", self._at(13, 30))
        now = self._walk("street_1", now)
        outside = self.wls.observe_world(
            hermes_home=self.home, environment=self.env, now=self._at(13, 40)
        )
        self.assertIsNone(outside.get("local_environment"))

    def test_legacy_scene_without_navigation(self) -> None:
        """验收 G（部分）：v0.3 场景（无 navigation）仍正常加载，行为退化为单区域。"""
        # 手工移除导航声明，模拟 v0.3 世界包。
        docs = schema.load_package(self.package_dir)
        docs[schema.SCENE_FILE].pop("navigation")
        legacy_dir = self.temp_dir(prefix="wg-legacy-") / "world"
        schema.write_package(legacy_dir, docs)
        legacy_scene = docs[schema.SCENE_FILE]
        schema.write_json(legacy_dir / schema.SCENE_FILE, schema.validate_scene_doc(legacy_scene))
        schema.write_json(legacy_dir / "MANIFEST.json", schema.build_manifest(legacy_dir))
        loaded = schema.load_package(legacy_dir, verify=True)
        self.assertNotIn("navigation", loaded[schema.SCENE_FILE])
        # 运行时照常（无导航 = place 图两段移动，无区域节点）。
        env = _runtime_env(legacy_dir, self.home, loaded["world.json"]["world_id"], travel=True)
        now = self._at(13)
        plan_leg1 = self.wls.plan_move_to("street_1", hermes_home=self.home, environment=env, now=now)
        self.assertEqual(plan_leg1["status"], "ready")
        applied = self.wls.apply_move_to("street_1", hermes_home=self.home, environment=env, now=now)
        self.assertEqual(applied["status"], "applied")
        # 等待第一段到达落地（travel enabled 时 plan 内部会先 realize）。
        from datetime import timedelta

        arrival = self.wtd.realize_due_travel(
            hermes_home=self.home, environment=env,
            now=now + timedelta(seconds=int((applied.get("travel") or {}).get("route_duration_seconds") or 30)),
        )
        self.assertEqual(arrival["status"], "arrived")
        plan_leg2 = self.wls.plan_move_to(
            "bookstore", hermes_home=self.home, environment=env,
            now=now + timedelta(seconds=int((applied.get("travel") or {}).get("route_duration_seconds") or 30) + 60),
        )
        self.assertEqual(plan_leg2["status"], "ready")


class PerceptionAdapterTest(WorldWithNavigationTest):
    """observe_local 三层信息适配器。"""

    def test_three_layer_information(self) -> None:
        from components.worldgen.perception import observe_local

        package_dir = self.make_navigated_world()
        instance = Path(self.temp_dir(prefix="wg-perc-")) / "instance"
        init_instance(package_dir, instance)
        result = observe_local(
            package_dir, place_id="bookstore", area_id="reading_area",
            instance_home=instance,
        )
        self.assertEqual(result["node"], "bookstore/reading_area")
        allowed = result["allowed_observation"]
        self.assertEqual(allowed["building"]["location_id"], "bookstore")
        self.assertTrue(allowed["furniture"])
        self.assertTrue(allowed["adjacent_areas"])
        self.assertEqual(
            result["static_world_data"]["area_connections"],
            ["shopfront<->reading_area"],
        )
        self.assertIn("current_position", result["runtime_dependent"])
        self.assertEqual(result["source"], "real_instance")

    def test_unknown_place_refused(self) -> None:
        from components.worldgen.perception import PerceptionError, observe_local

        package_dir = self.make_navigated_world()
        with self.assertRaises(PerceptionError):
            observe_local(package_dir, place_id="ghost")


class BatchQualityTest(WorldWithNavigationTest):
    """批量质检单元（不跑全量 40 世界，用单世界冒烟）。"""

    def test_structure_check_passes_on_generated_world(self) -> None:
        from components.worldgen.quality import _structure_check

        directory = self.temp_package(make_valid_docs(seed=616), prefix="wg-q-")
        apply_interiors_to_package(directory)
        result = _structure_check(directory)
        self.assertTrue(result["ok"], result.get("problems"))
        self.assertGreaterEqual(result["navigation_buildings"], 1)

    def test_consistency_check_deterministic_days(self) -> None:
        from components.worldgen.quality import _consistency_check

        if os.name == "nt":
            self.skipTest("需要 tz 数据库")
        directory = self.temp_package(make_valid_docs(seed=617), prefix="wg-qc-")
        apply_interiors_to_package(directory)
        result = _consistency_check(directory, days=3)
        self.assertTrue(result["ok"], result.get("problems"))
        self.assertGreater(result["checks"], 0)

    def test_navigation_connection_edit_changes_reachability(self) -> None:
        """验收 C：修改房间连接 → 重新加载后可达性按新结构变化。"""
        from components.worldgen.editor_bridge import to_editor_world

        package_dir = self.make_navigated_world()
        package = load_package(package_dir)
        scene = package.scene
        nav = scene["navigation"]
        bookstore_nav = nav["bookstore"]
        # 断开 shopfront↔reading_area，改为经第三区域（虚构 "hall" 不可用）：
        # 直接删除唯一连接 → 阅读区不可达 → 回写应被校验拒绝（道路图连通语义）。
        broken = json.loads(json.dumps(scene))
        broken["navigation"]["bookstore"]["connections"] = []
        docs = package.snapshot()
        docs[schema.SCENE_FILE] = broken
        from components.worldgen.validator import validate

        # 断开连接本身不破坏世界级道路图（世界道路仍连通），但可达性应变化：
        merged = dict(docs)
        merged[schema.SCENE_FILE] = schema.validate_scene_doc(broken)
        self.assertEqual(validate(merged), [])
        out = self.temp_dir(prefix="wg-navedit-") / "edited"
        schema.write_package(out, docs)
        schema.write_json(out / schema.SCENE_FILE, schema.validate_scene_doc(broken))
        schema.write_json(out / "MANIFEST.json", schema.build_manifest(out))
        edited = load_package(out)
        edited_world_id = edited.world_id
        # 通过运行时数据验证：阅读区不再可达（导航图断开）。
        env = _runtime_env(out, Path(self.temp_dir(prefix="wg-navinst-")) / "i", edited_world_id, travel=False)
        import world_runtime_data as wrd

        wrd.reset_cache()
        try:
            world = wrd.active_world(env)
            self.assertNotIn(
                "bookstore/reading_area", world.reachable_of("bookstore")
            )
        finally:
            wrd.reset_cache()


if __name__ == "__main__":
    unittest.main()
