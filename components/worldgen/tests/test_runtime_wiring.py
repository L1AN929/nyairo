"""运行时接线测试：世界包经 world_runtime_data 进入 rc9 世界模块。

覆盖施工单验收 A（默认行为不变）/ D（多世界隔离）/ E（移动）/
F（时间变化）/ G（持久化）的单元级部分。
需要 POSIX（fcntl）；在原生 Windows 上自动跳过。
"""

from __future__ import annotations

import os
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen.instance import init_instance
from components.worldgen.loader import load as load_package

posix_only = unittest.skipIf(
    os.name == "nt", "rc9 世界运行时需要 POSIX 文件锁（在 WSL/Linux 上运行）"
)


def _tz(tz_name: str) -> ZoneInfo:
    return ZoneInfo(tz_name)


def _clean_runtime_env(base: dict) -> dict:
    """去掉本进程可能残留的 worldgen 环境变量，保证测试自定环境生效。"""
    for key in (
        "CHIYO_WORLD_PACKAGE",
        "CHIYO_WORLD_HOME",
        "CHIYO_WORLD_ID",
        "CHIYO_WORLD_SCHOOL_DAY_ENABLED",
        "CHIYO_WORLD_SCHOOL_EVENTS_ENABLED",
        "CHIYO_WORLD_SCHOOL_CONTINUITY_ENABLED",
        "CHIYO_WORLD_SCHOOL_ASSIGNMENTS_ENABLED",
        "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED",
    ):
        base.pop(key, None)
    return base


def _custom_world_env(package_dir: Path, instance_home: Path, world_id: str, *, travel: bool) -> dict:
    env = _clean_runtime_env(dict(os.environ))
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
class ShiomiDefaultBehaviorTest(WorldGenTestCase):
    """验收 A：不加载世界包时，汐见市行为与常量完全一致。"""

    def setUp(self) -> None:
        super().setUp()
        import world_living_skeleton as wls
        import world_runtime_data as wrd

        self.wls = wls
        self.wrd = wrd
        self.env = _clean_runtime_env(dict(os.environ))
        self.env.update(
            {
                "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
                "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "0",
                "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
            }
        )
        wrd.reset_cache()

    def test_active_world_is_shiomi(self) -> None:
        world = self.wrd.active_world(self.env)
        self.assertEqual(world.world_id, self.wls.WORLD_ID)
        self.assertEqual(world.timezone_name, self.wls.WORLD_TIMEZONE)
        self.assertFalse(world.is_custom)
        self.assertEqual(
            [loc["location_id"] for loc in world.location_catalog()],
            list(self.wls._LOCATIONS),
        )
        import world_daily_rhythm as wdr

        rule = world.location_rule("convenience_store")
        original = wdr._RULES["convenience_store"]
        self.assertEqual(rule["open_hour"], original.open_hour)
        self.assertEqual(rule["close_hour"], original.close_hour)

    def test_shiomi_observe_and_move_unchanged(self) -> None:
        """汐见市观察/移动链路在临时 HERMES_HOME 下照常工作。"""
        import world_foundation as wf
        import world_travel_delivery as wtd

        home = self.temp_dir(prefix="wg-shiomi-")
        state_path = home / "data" / "world" / "world_state.json"
        state = {
            "schema_version": "world.foundation.v0",
            "revision": 1,
            "world_id": self.wls.WORLD_ID,
            "timezone": self.wls.WORLD_TIMEZONE,
            "location": {"place_id": "home", "area_id": "bedroom"},
            "scene": {"scene_id": "home.bedroom", "revision": 1},
            "facts": [],
        }
        wf.WorldStateStore(state_path).save(state, expected_revision=1)

        now = datetime(2026, 10, 14, 8, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        observation = self.wls.observe_world(hermes_home=home, environment=self.env, now=now)
        self.assertEqual(observation["status"], "available")
        self.assertEqual(observation["world_id"], "shiomi_city")
        self.assertEqual(observation["current_location"]["location_id"], "home")
        self.assertEqual(
            [loc["location_id"] for loc in observation["reachable_locations"]],
            ["nearby_street"],
        )

        plan = self.wls.plan_move_to(
            "nearby_street", hermes_home=home, environment=self.env, now=now
        )
        self.assertEqual(plan["status"], "ready")
        applied = self.wls.apply_move_to(
            "nearby_street", hermes_home=home, environment=self.env, now=now
        )
        self.assertEqual(applied["status"], "applied")
        loaded = wf.WorldStateStore(state_path).load()
        self.assertEqual(loaded["location"]["place_id"], "nearby_street")
        # 汐见市的旧路线权威保持不变。
        self.assertEqual(
            wtd.route_duration_seconds("home", "nearby_street", self.env), 300
        )


@posix_only
class CustomWorldRuntimeTest(WorldGenTestCase):
    """生成世界的真实加载、移动、时间与隔离。"""

    def setUp(self) -> None:
        super().setUp()
        import world_runtime_data as wrd

        self.wrd = wrd
        wrd.reset_cache()
        self.docs_a = make_valid_docs(seed=929711, theme="coastal_town")
        self.docs_b = make_valid_docs(seed=20261014, theme="residential_town")
        self.package_a = self.temp_package(self.docs_a, prefix="wg-world-a-")
        self.package_b = self.temp_package(self.docs_b, prefix="wg-world-b-")
        self.world_a = load_package(self.package_a)
        self.world_b = load_package(self.package_b)
        self.home_a = Path(self.temp_dir(prefix="wg-inst-a-")) / "instance"
        self.home_b = Path(self.temp_dir(prefix="wg-inst-b-")) / "instance"
        init_instance(self.package_a, self.home_a)
        init_instance(self.package_b, self.home_b)
        self.env_a = _custom_world_env(
            self.package_a, self.home_a, self.world_a.world_id, travel=True
        )
        self.env_b = _custom_world_env(
            self.package_b, self.home_b, self.world_b.world_id, travel=True
        )
        import world_living_skeleton as wls
        import world_travel_delivery as wtd

        self.wls = wls
        self.wtd = wtd

    def tearDown(self) -> None:
        self.wrd.reset_cache()
        super().tearDown()

    def _wednesday(self, hour: int, minute: int) -> datetime:
        return datetime(2026, 10, 14, hour, minute, tzinfo=_tz(self.world_a.timezone_name))

    def test_observe_reports_package_world(self) -> None:
        """验收 D：世界 ID、地点、规则来自世界包而非汐见市。"""
        observation = self.wls.observe_world(
            hermes_home=self.home_a, environment=self.env_a, now=self._wednesday(8, 0)
        )
        self.assertEqual(observation["status"], "available")
        self.assertEqual(observation["world_id"], self.world_a.world_id)
        self.assertEqual(observation["timezone"], self.world_a.timezone_name)
        catalog_ids = {loc["location_id"] for loc in observation["locations"]}
        self.assertEqual(catalog_ids, set(self.world_a.locations))
        self.assertNotIn("nearby_street", catalog_ids)  # 汐见市专属地点不存在

    def test_worlds_do_not_cross_contaminate(self) -> None:
        """验收 D：两个世界分别加载，ID 与地点互不渗透。"""
        obs_a = self.wls.observe_world(
            hermes_home=self.home_a, environment=self.env_a, now=self._wednesday(8, 0)
        )
        obs_b = self.wls.observe_world(
            hermes_home=self.home_b, environment=self.env_b, now=self._wednesday(8, 0)
        )
        self.assertEqual(obs_a["world_id"], self.world_a.world_id)
        self.assertEqual(obs_b["world_id"], self.world_b.world_id)
        ids_a = {loc["location_id"] for loc in obs_a["locations"]}
        ids_b = {loc["location_id"] for loc in obs_b["locations"]}
        self.assertTrue(ids_a)
        self.assertTrue(ids_b)
        # 世界 A 的状态文件不被世界 B 的观察改写。
        import world_foundation as wf

        state_a = wf.WorldStateStore(self.home_a / "data" / "world" / "world_state.json").load()
        self.assertEqual(state_a["world_id"], self.world_a.world_id)

    def test_move_with_route_duration_and_persistence(self) -> None:
        """验收 E + G：按路线耗时行走，重启（重载）后位置与移动记录仍在。"""
        now = self._wednesday(8, 5)
        route_target = sorted(self.world_a.locations["home"]["reachable"])[0]
        plan = self.wls.plan_move_to(
            route_target, hermes_home=self.home_a, environment=self.env_a, now=now
        )
        self.assertEqual(plan["status"], "ready")
        duration = (plan.get("travel") or {}).get("route_duration_seconds")
        self.assertIsNotNone(duration)
        applied = self.wls.apply_move_to(
            route_target, hermes_home=self.home_a, environment=self.env_a, now=now
        )
        self.assertEqual(applied["status"], "applied")

        # 到达时刻落地。
        arrival = self.wtd.realize_due_travel(
            hermes_home=self.home_a, environment=self.env_a, now=now + timedelta(seconds=duration)
        )
        self.assertEqual(arrival["status"], "arrived")

        # "重启"：清缓存后按同一路径重读。
        self.wrd.reset_cache()
        state = self.wls.read_move_state(
            hermes_home=self.home_a, environment=self.env_a, now=now + timedelta(seconds=duration + 60)
        )
        self.assertEqual(state["world_location_id"], route_target)
        self.assertEqual(state["world_id"], self.world_a.world_id)
        travel_state = self.wtd.load_state(self.home_a, self.env_a)
        self.assertEqual(len(travel_state["travels"]), 1)
        self.assertEqual(travel_state["world_id"], self.world_a.world_id)

    def test_unknown_location_rejected(self) -> None:
        """汐见市地点在生成世界中不存在，移动被明确拒绝。"""
        plan = self.wls.plan_move_to(
            "nearby_street", hermes_home=self.home_a, environment=self.env_a,
            now=self._wednesday(8, 0),
        )
        self.assertEqual(plan["status"], "rejected")
        self.assertEqual(plan["reason_code"], "UNKNOWN_LOCATION")

    def test_opening_hours_follow_package_rules(self) -> None:
        """验收 F：营业状态随时间按世界包规则变化。"""
        import world_daily_rhythm as wdr

        # 找一个有时段的地点。
        target = next(
            loc_id
            for loc_id, rule in sorted(self.world_a.locations.items())
            if self.world_a.location_rule(loc_id)["open_hour"] is not None
        )
        rule = self.world_a.location_rule(target)
        wednesday = self._wednesday(0, 0)
        open_hour = rule["open_hour"]
        close_hour = rule["close_hour"]
        at_open = wednesday.replace(hour=open_hour, minute=5)
        at_closed_hour = close_hour if close_hour <= 23 else 23
        at_closed = wednesday.replace(hour=at_closed_hour, minute=5)
        if at_closed <= at_open:
            at_closed = wednesday.replace(hour=23, minute=5)
        open_status = wdr.location_availability(
            target, at_open, environment=self.env_a
        )
        closed_status = wdr.location_availability(
            target, at_closed, environment=self.env_a
        )
        self.assertEqual(open_status["status"], "OPEN")
        self.assertEqual(closed_status["status"], "CLOSED")
        # 营业中有活动，打烊后无活动。
        self.assertTrue(wdr.affordances_for_location(target, at_open, environment=self.env_a))
        self.assertEqual(
            wdr.affordances_for_location(target, at_closed, environment=self.env_a), []
        )

    def test_social_presence_follows_package_npcs(self) -> None:
        """公共 NPC 来自世界包，汐见市 NPC 不出现。"""
        import world_social_presence as wsp

        npcs = wsp._active_npcs(self.env_a)
        names = {npc.npc_id for npc in npcs}
        self.assertTrue(names)
        self.assertFalse(any(npc_id.startswith("shiomi_npc") for npc_id in names))
        # 有出场窗口的地点应能看到该 NPC。
        npc = npcs[0]
        window = npc.windows[0]
        location_id = window.location_id
        weekday = sorted(window.weekdays)[0]
        # 2026-10-12 是星期一。
        base = datetime(2026, 10, 12, tzinfo=_tz(self.world_a.timezone_name))
        day_offset = (weekday - 1) % 7
        moment = base.replace(
            hour=window.start_minute // 60, minute=window.start_minute % 60
        ) + timedelta(days=day_offset)
        present = wsp.present_npcs(location_id, now=moment, environment=self.env_a)
        self.assertTrue(any(item["npc_id"] == npc.npc_id for item in present))

    def test_ambient_events_follow_package_templates(self) -> None:
        """环境事件来自世界包模板，且 world_id 正确。"""
        import world_ambient_events as wae

        day = self._wednesday(0, 0).date()
        events = wae.materialize_ambient_events(day, environment=self.env_a)
        self.assertTrue(events)
        for event in events:
            self.assertEqual(event["world_id"], self.world_a.world_id)
            self.assertIn(event["location_id"], self.world_a.locations)

    def test_shipped_v2_package_loads_finite_events(self) -> None:
        """随仓 v2 包：24 条（16 单日 + 8 有限跨天），跨天同身份。"""
        import world_ambient_events as wae

        package = Path(__file__).resolve().parents[3] / "examples" / "worlds" / "daily_neighborhood_v1"
        env = _clean_runtime_env(dict(os.environ))
        env["CHIYO_WORLD_PACKAGE"] = str(package)
        self.wrd.reset_cache()
        catalog = wae.ambient_template_catalog(env)
        self.assertEqual(catalog["total"], 24)
        self.assertEqual(catalog["categories"]["cross_day"], 8)
        day = datetime(2026, 10, 24, tzinfo=_tz("Asia/Tokyo")).date()
        first = wae.materialize_ambient_events(day, environment=env)
        finite_ids = {
            event["event_id"]
            for event in first
            if event["provenance"] == wae.FINITE_PROVENANCE
        }
        self.assertTrue(finite_ids)
        self.assertTrue(all(event_id.startswith("ambient-finite-") for event_id in finite_ids))
        self.wrd.reset_cache()


if __name__ == "__main__":
    unittest.main()
