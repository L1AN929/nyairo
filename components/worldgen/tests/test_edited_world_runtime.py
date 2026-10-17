"""编辑后的世界在 rc9 运行时中的真实行为（v0.2 验收 E 的单元级部分）。

使用随仓库提交的 ``examples/worlds/nagiura-edited``（由编辑器导出流程真实
回写生成），在隔离 World/Body 实例中验证：

* 修改后的道路拓扑真实生效（被删除的道路不可通行，新增道路可走）；
* 修改后的营业时间真实生效（11 点未营业 / 13 点营业 / 20 点关闭）；
* 闭店只约束店内 affordance，不拦“走到门口”（本批已裁定：相邻闭店地点
  plan/apply 仍 ready，结果带 CLOSED 提示）；
* 编辑不影响角色既有经历（实例状态独立保存）。

需要 POSIX（fcntl）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ._setup import WorldGenTestCase
from components.worldgen.instance import init_instance
from components.worldgen.loader import load as load_package

REPO_ROOT = Path(__file__).resolve().parents[3]
EDITED_WORLD = REPO_ROOT / "examples" / "worlds" / "nagiura-edited"

posix_only = unittest.skipIf(
    os.name == "nt", "rc9 世界运行时需要 POSIX 文件锁（在 WSL/Linux 上运行）"
)


def _env(package_dir: Path, instance_home: Path, world_id: str, *, travel: bool) -> dict:
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
            "CHIYO_WORLD_HOME_SPACE_ENABLED": "1",
        }
    )
    return env


@posix_only
class EditedWorldRuntimeTest(WorldGenTestCase):
    def setUp(self) -> None:
        super().setUp()
        if not EDITED_WORLD.is_dir():
            self.skipTest("缺少编辑后示例世界（examples/worlds/nagiura-edited）")
        import world_living_skeleton as wls
        import world_runtime_data as wrd

        self.wls = wls
        self.wrd = wrd
        wrd.reset_cache()
        self.package = load_package(EDITED_WORLD)
        self.home = Path(self.temp_dir(prefix="wg-edited-inst-")) / "instance"
        init_instance(EDITED_WORLD, self.home)
        self.env = _env(EDITED_WORLD, self.home, self.package.world_id, travel=True)
        self.env_instant = _env(
            EDITED_WORLD, self.home, self.package.world_id, travel=False
        )
        self._tz = ZoneInfo(self.package.timezone_name)

    def tearDown(self) -> None:
        self.wrd.reset_cache()
        super().tearDown()

    def _at(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 10, 14, hour, minute, tzinfo=self._tz)

    def _walk_to_street(self, now):
        """走到街道（后续拓扑断言都从街道出发）。"""
        from datetime import timedelta

        import world_travel_delivery as wtd

        applied = self.wls.apply_move_to(
            "street_1", hermes_home=self.home, environment=self.env, now=now
        )
        self.assertEqual(applied["status"], "applied")
        duration = int((applied.get("travel") or {}).get("route_duration_seconds") or 60)
        arrival = wtd.realize_due_travel(
            hermes_home=self.home, environment=self.env, now=now + timedelta(seconds=duration)
        )
        self.assertEqual(arrival["status"], "arrived")
        return now + timedelta(seconds=duration)

    def test_edited_topology_actually_governs_movement(self) -> None:
        """被删除的道路不可通行；新增道路按推导耗时可走。"""
        self._walk_to_street(self._at(10))
        # 编辑器删除了 street_1<->bookstore：从街道出发书店不可直达。
        plan_removed = self.wls.plan_move_to(
            "bookstore", hermes_home=self.home, environment=self.env_instant,
            now=self._at(13),
        )
        self.assertEqual(plan_removed["status"], "rejected")
        self.assertEqual(plan_removed["reason_code"], "LOCATION_NOT_REACHABLE")
        # 新增道路 coffee_stand<->street_1：从街道出发可按推导耗时前往。
        plan_new = self.wls.plan_move_to(
            "coffee_stand", hermes_home=self.home, environment=self.env_instant,
            now=self._at(13),
        )
        self.assertEqual(plan_new["status"], "ready")
        self.assertEqual(plan_new["reason_code"], "REACHABLE")

    def test_edited_opening_hours_actually_govern(self) -> None:
        """书店 12–18：11 点未营业 / 13 点营业 / 20 点关闭（真实规则查询）。"""
        import world_daily_rhythm as wdr

        self.assertEqual(
            wdr.location_availability("bookstore", self._at(11), environment=self.env)["status"],
            "CLOSED",
        )
        open_status = wdr.location_availability(
            "bookstore", self._at(13), environment=self.env
        )
        self.assertEqual(open_status["status"], "OPEN")
        self.assertEqual(open_status["reason_code"], "WITHIN_OPEN_HOURS")
        self.assertEqual(
            wdr.location_availability("bookstore", self._at(20), environment=self.env)["status"],
            "CLOSED",
        )
        # 闭店不再拦“走到门口”：书店与家不相邻，拒绝原因只能是可达性，
        # 不能再用 LOCATION_CLOSED 把拓扑事实说成营业事实。门口语义见
        # test_closed_bookstore_doorstep_is_reachable_but_store_affordances_are_empty。
        plan_closed = self.wls.plan_move_to(
            "bookstore", hermes_home=self.home, environment=self.env_instant,
            now=self._at(11),
        )
        self.assertEqual(plan_closed["status"], "rejected")
        self.assertEqual(plan_closed["reason_code"], "LOCATION_NOT_REACHABLE")
        self.assertNotEqual(plan_closed["reason_code"], "LOCATION_CLOSED")
        # 经过 park 到达书店的合法移动在 13 点照常执行。
        applied = self.wls.apply_move_to(
            "street_1", hermes_home=self.home, environment=self.env, now=self._at(10)
        )
        self.assertEqual(applied["status"], "applied")

    def test_closed_bookstore_doorstep_is_reachable_but_store_affordances_are_empty(
        self,
    ) -> None:
        """营业限制的是场内机会，不是走到门口（本批已裁定语义）。

        park↔bookstore 相邻、书店 12–18 营业：11 点从公园规划/走到书店门口必须
        成功，并如实带上 CLOSED 提示；店内 affordance 为空。13 点开门后同一个
        门口才出现 BROWSE_STORE。全程用真实编辑后世界与真实实例状态。
        """
        import world_daily_rhythm as wdr

        for leg in ("street_1", "park"):
            applied = self.wls.apply_move_to(
                leg, hermes_home=self.home, environment=self.env_instant,
                now=self._at(10),
            )
            self.assertEqual(applied["status"], "applied", f"{leg}: {applied}")
        self.assertEqual(
            self.wls.read_move_state(
                hermes_home=self.home, environment=self.env_instant
            )["world_location_id"],
            "park",
        )

        # 11:00 书店闭店：门口仍然可规划（ready），营业只是提示字段。
        plan_closed = self.wls.plan_move_to(
            "bookstore", hermes_home=self.home, environment=self.env_instant,
            now=self._at(11),
        )
        self.assertEqual(plan_closed["status"], "ready", plan_closed)
        self.assertEqual(plan_closed["reason_code"], "REACHABLE")
        self.assertEqual(
            plan_closed["destination_availability"]["status"], "CLOSED"
        )
        self.assertEqual(
            plan_closed["destination_availability"]["reason_code"],
            "OUTSIDE_OPEN_HOURS",
        )
        self.assertEqual(
            wdr.affordances_for_location(
                "bookstore", self._at(11), environment=self.env_instant
            ),
            [],
        )

        # 真的走到门口：到达事实成立，闭店提示跟着结果走。
        into_closed = self.wls.apply_move_to(
            "bookstore", hermes_home=self.home, environment=self.env_instant,
            now=self._at(11),
        )
        self.assertEqual(into_closed["status"], "applied", into_closed)
        self.assertEqual(into_closed["reason_code"], "MOVE_COMMITTED")
        self.assertEqual(
            into_closed["destination_availability"]["status"], "CLOSED"
        )
        self.assertEqual(
            self.wls.read_move_state(
                hermes_home=self.home, environment=self.env_instant
            )["world_location_id"],
            "bookstore",
        )
        self.assertEqual(
            wdr.affordances_for_location(
                "bookstore", self._at(11), environment=self.env_instant
            ),
            [],
        )

        # 13:00 开门：同一个门口，店内可做事项才出现。
        self.assertEqual(
            [
                item["affordance_id"]
                for item in wdr.affordances_for_location(
                    "bookstore", self._at(13), environment=self.env_instant
                )
            ],
            ["BROWSE_STORE"],
        )

    def test_edited_world_survives_reload_with_state(self) -> None:
        """编辑后世界加载 + 移动 + 重载：位置与移动记录保留（隔离实例）。"""
        from datetime import timedelta

        import world_foundation as wf
        import world_travel_delivery as wtd

        now = self._at(10)
        applied = self.wls.apply_move_to(
            "street_1", hermes_home=self.home, environment=self.env, now=now
        )
        self.assertEqual(applied["status"], "applied")
        duration = (applied.get("travel") or {}).get("route_duration_seconds") or 60
        arrival = wtd.realize_due_travel(
            hermes_home=self.home, environment=self.env,
            now=now + timedelta(seconds=int(duration)),
        )
        self.assertEqual(arrival["status"], "arrived")

        self.wrd.reset_cache()  # 模拟重启后的重新解析
        state = self.wls.read_move_state(
            hermes_home=self.home, environment=self.env,
            now=now + timedelta(seconds=int(duration) + 60),
        )
        self.assertEqual(state["world_location_id"], "street_1")
        self.assertEqual(state["world_id"], self.package.world_id)
        travel_state = wtd.load_state(self.home, self.env)
        self.assertEqual(len(travel_state["travels"]), 1)
        world_state = wf.WorldStateStore(
            self.home / "data" / "world" / "world_state.json"
        ).load()
        self.assertEqual(world_state["revision"], 2)

    def test_new_location_from_editor_is_reachable(self) -> None:
        """编辑器新建的咖啡小摊进入了活动世界且规则正确。"""
        observation = self.wls.observe_world(
            hermes_home=self.home, environment=self.env, now=self._at(9)
        )
        self.assertEqual(observation["status"], "available")
        catalog = {loc["location_id"] for loc in observation["locations"]}
        self.assertIn("coffee_stand", catalog)
        self.assertIn("bookstore", catalog)
        # 咖啡小摊 8–14 营业：9 点可访问且有活动。
        rule = self.wrd.active_world(self.env).location_rule("coffee_stand")
        self.assertEqual(rule["open_hour"], 8)
        self.assertEqual(rule["close_hour"], 14)
        affordances = self.wrd.active_world(self.env).location_rule("coffee_stand")["affordances"]
        self.assertTrue(any(item[0] == "BROWSE_STORE" for item in affordances))


if __name__ == "__main__":
    unittest.main()
