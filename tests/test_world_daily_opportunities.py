"""W 包验收：有限街区静态场景包 + 只读机会投影（world_daily_opportunities）。

覆盖（只跑本行为定向用例，不替代全仓/World 全套）：

* 合法世界包真实加载 + 真实 World 初始化 + 真实 observe；
* open_hour 边界 / weekdays_only / 同日跨日按既有 owner（world_daily_rhythm）语义；
* 闭店时没有店内 affordance，但仍可规划并走到门口、也能按路段回家；
* 未初始化、世界包不可读、营业规则不可读时如实报告原因，不伪造机会；
* 观察 0 写 / 0 candidate / 0 模型；走道耗时为真实正秒；可见物件只在当前位置。

需要 POSIX（世界写锁用 fcntl）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    ROOT,
    ROOT / "components",
    ROOT / "components" / "world" / "scripts",
    ROOT / "tests",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

posix_only = unittest.skipIf(
    os.name == "nt", "rc9 世界运行时需要 POSIX 文件锁（在 WSL/Linux 上运行）"
)

PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
#: 并行写集保留的唯一工作目录；不存在时退回系统临时目录。
SANDBOX = Path("/tmp/nyaw05r.jdK3q")


def work_dir(prefix: str) -> Path:
    base = str(SANDBOX) if SANDBOX.is_dir() else None
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def build_env(package_dir: Path, home: Path, world_id: str, *, travel: bool) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("CHIYO_WORLD_PACKAGE", "CHIYO_WORLD_HOME", "CHIYO_WORLD_ID",
                          "XINXI_DIR")}
    env.update(
        {
            "CHIYO_WORLD_PACKAGE": str(package_dir),
            "CHIYO_WORLD_HOME": str(home),
            "CHIYO_WORLD_ID": world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1" if travel else "0",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
        }
    )
    return env


def seed_world_state(package, home: Path) -> None:
    """用 worldgen.instance 相同的 owner store 建世界状态/时间线（不含 Body 基座）。

    Body 基座（world_body_substrate）属于并行写集；本用例只验 World 侧事实，
    因此不 import 它。真实 ``python -m components.worldgen instance`` 的用例
    在模块可导入时单独跑。
    """
    import world_foundation as wf
    import world_runtime_data as wrd
    import world_timeline as wt

    world_dir = home / "data" / "world"
    world_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "world.foundation.v0",
        "revision": 1,
        "world_id": package.world_id,
        "timezone": package.timezone_name,
        "location": {"place_id": package.home_location_id,
                     "area_id": package.home_area_id},
        "scene": {"scene_id": "home.bedroom", "revision": 1},
        "facts": [],
    }
    wf.WorldStateStore(world_dir / "world_state.json").save(state, expected_revision=1)
    timeline = wt.build_initial_timeline(
        package.world_id, {wrd.CHIYO_WORLD_PACKAGE_ENV: str(PACKAGE)}
    )
    wt.WorldTimelineStore(world_dir / "world_timeline.json").save(
        timeline, expected_revision=1
    )
    (home / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")


def instance_digest(root: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digests[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digests


@posix_only
class DailyNeighborhoodTest(unittest.TestCase):
    """真实世界包 + 真实 owner 读写路径。"""

    def setUp(self) -> None:
        import world_daily_opportunities as wdo
        import world_foundation as wf
        import world_living_skeleton as wls
        import world_runtime_data as wrd
        import world_timeline as wt
        import world_travel_delivery as wtd
        from components.worldgen import loader
        from components.worldgen.validator import validate_package_dir

        self.wdo = wdo
        self.wf = wf
        self.wls = wls
        self.wrd = wrd
        self.wt = wt
        self.wtd = wtd
        self.loader = loader
        self.validate_package_dir = validate_package_dir
        wrd.reset_cache()
        self.addCleanup(wrd.reset_cache)
        self.package = loader.load(PACKAGE)
        self.home = work_dir("w-daily-t-")
        seed_world_state(self.package, self.home)
        self.env = build_env(PACKAGE, self.home, self.package.world_id, travel=False)
        self.tz = ZoneInfo(self.package.timezone_name)

    # -- helpers ---------------------------------------------------------------

    def at(self, year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
        return datetime(year, month, day, hour, minute, tzinfo=self.tz)

    def catalog(self, now: datetime, *, env: dict | None = None, home: Path | None = None):
        return self.wdo.location_opportunity_catalog(
            env or self.env, now, hermes_home=home or self.home
        )

    def entry(self, catalog: dict, location_id: str) -> dict:
        return next(item for item in catalog["opportunities"]
                    if item["location_id"] == location_id)

    def walk(self, target: str, now: datetime, *, env: dict | None = None) -> dict:
        env = env or self.env
        plan = self.wls.plan_move_to(target, hermes_home=self.home, environment=env, now=now)
        self.assertEqual(plan["status"], "ready", f"{target}: {plan}")
        applied = self.wls.apply_move_to(
            target, hermes_home=self.home, environment=env, now=now
        )
        self.assertEqual(applied["status"], "applied", f"{target}: {applied}")
        return applied

    def position(self, *, env: dict | None = None,
                 now: datetime | None = None) -> str:
        # ``read_move_state`` 在 travel 打开时会先结算到期行程；``now=None`` 用它自己的
        # 真实墙钟，所以在途断言必须传用例的时钟，否则会在真实时间越过后漂移。
        state = self.wls.read_move_state(
            hermes_home=self.home, environment=env or self.env, now=now
        )
        return state["world_location_id"]

    # -- 1. 合法包 + 真实加载 --------------------------------------------------

    def test_neighborhood_package_is_valid_and_loads(self) -> None:
        self.assertEqual(self.validate_package_dir(PACKAGE), [])
        self.assertEqual(self.package.world_id, "daily_neighborhood_v1")
        self.assertEqual(
            sorted(self.package.locations),
            ["cafe", "home", "park", "reading_point", "street_corner", "street_front"],
        )
        kinds = {key: value["kind"] for key, value in self.package.locations.items()}
        self.assertEqual(kinds["home"], "home")
        self.assertEqual(kinds["street_front"], "street")
        self.assertEqual(kinds["park"], "park")
        self.assertEqual(kinds["cafe"], "cafe")
        self.assertIn(kinds["reading_point"], {"library", "store"})
        scene = self.package.scene
        self.assertIn("home", scene["navigation"])
        self.assertEqual(
            scene["navigation"]["home"]["furniture_map"]["bedroom"],
            ["desk_home", "bed_home"],
        )

    def test_worldgen_init_instance_really_creates_the_neighborhood(self) -> None:
        try:
            import world_body_substrate  # noqa: F401
        except Exception as exc:  # 并行写集正在改 Body 基座时如实跳过，不冒认真实 init
            self.skipTest(f"world_body_substrate 当前不可导入：{type(exc).__name__}")
        from components.worldgen.instance import init_instance

        home = work_dir("w-daily-init-") / "instance"
        created = init_instance(PACKAGE, home)
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["initial_location"], "home/bedroom")
        env = build_env(PACKAGE, home, self.package.world_id, travel=False)
        observation = self.wls.observe_world(
            hermes_home=home, now=self.at(2026, 10, 16, 13), environment=env
        )
        self.assertEqual(observation["status"], "available")
        self.assertEqual(observation["reachable_opportunities"]["status"], "available")

    # -- 2. 真实 observe + 至少两个竞争机会 ------------------------------------

    def test_observe_projects_opportunities_with_real_competition(self) -> None:
        observation = self.wls.observe_world(
            hermes_home=self.home, now=self.at(2026, 10, 16, 13), environment=self.env
        )
        self.assertEqual(observation["status"], "available")
        furniture = {item["name"] for item in observation["local_environment"]["furniture"]}
        self.assertEqual(furniture, {"书桌", "床"})
        projection = observation["reachable_opportunities"]
        self.assertEqual(projection["status"], "available")
        self.assertEqual(projection["scope"], "NOW_ONLY")
        self.assertEqual(projection["future"], "not_promised")
        self.assertEqual(projection["schedule_source"], "world_package_schedule")
        open_items = [item for item in projection["opportunities"]
                      if item["availability"] == "OPEN" and item["affordance_ids"]]
        self.assertGreaterEqual(len(open_items), 2, projection)
        park = next(item for item in projection["opportunities"]
                    if item["location_id"] == "park")
        self.assertIn("SIT", park["affordance_ids"])
        self.assertTrue(all(item["action_grant"] is False
                            for item in projection["opportunities"]))
        observeds = {item["location_id"]: item["observed"]
                     for item in projection["opportunities"]}
        self.assertEqual(observeds["home"], "visible_here")
        for other in ("park", "cafe", "reading_point", "street_front", "street_corner"):
            self.assertEqual(observeds[other], "not_observed", other)

    # -- 3. 营业条件：边界 / 周末 / 跨日 ---------------------------------------

    def test_open_hour_boundary_and_weekday_flags_follow_owner_semantics(self) -> None:
        cases = [
            ((2026, 10, 16, 7, 59), "CLOSED", "OUTSIDE_OPEN_HOURS", []),
            ((2026, 10, 16, 8, 0), "OPEN", "WITHIN_OPEN_HOURS",
             ["SIT_AT_CAFE", "BUY_DRINK", "LOOK_AROUND"]),
            ((2026, 10, 16, 17, 59), "OPEN", "WITHIN_OPEN_HOURS",
             ["SIT_AT_CAFE", "BUY_DRINK", "LOOK_AROUND"]),
            ((2026, 10, 16, 18, 0), "CLOSED", "OUTSIDE_OPEN_HOURS", []),
            ((2026, 10, 17, 13, 0), "CLOSED", "WEEKEND_CLOSED", []),
            ((2026, 10, 19, 9, 0), "OPEN", "WITHIN_OPEN_HOURS",
             ["SIT_AT_CAFE", "BUY_DRINK", "LOOK_AROUND"]),
        ]
        for (year, month, day, hour, minute), status, reason, affordances in cases:
            with self.subTest(at=f"{year}-{month}-{day} {hour}:{minute:02d}"):
                catalog = self.catalog(self.at(year, month, day, hour, minute))
                cafe = self.entry(catalog, "cafe")
                self.assertEqual(cafe["availability"]["status"], status)
                self.assertEqual(cafe["availability"]["reason_code"], reason)
                self.assertEqual(
                    [item["affordance_id"] for item in cafe["affordances"]], affordances
                )
                self.assertEqual(cafe["availability"]["source"], "world_package_schedule")
                # 公园不受咖啡店营业条件影响：至少还有一个合法选择。
                park = self.entry(catalog, "park")
                self.assertEqual(park["availability"]["status"], "OPEN")

    def test_cross_day_boundary_uses_owner_local_date(self) -> None:
        friday_late = self.catalog(self.at(2026, 10, 16, 23, 59))
        saturday_early = self.catalog(self.at(2026, 10, 17, 0, 1))
        self.assertEqual(
            (friday_late["local_date"], friday_late["weekday"]), ("2026-10-16", 5)
        )
        self.assertEqual(
            (saturday_early["local_date"], saturday_early["weekday"]), ("2026-10-17", 6)
        )
        self.assertEqual(
            self.entry(friday_late, "cafe")["availability"]["reason_code"],
            "OUTSIDE_OPEN_HOURS",
        )
        self.assertEqual(
            self.entry(saturday_early, "cafe")["availability"]["reason_code"],
            "WEEKEND_CLOSED",
        )
        # 公园 6..22：跨日那一刻两侧都闭店，原因一致，不伪造开放。
        self.assertEqual(
            self.entry(friday_late, "park")["availability"]["status"], "CLOSED"
        )
        self.assertEqual(
            self.entry(saturday_early, "park")["availability"]["status"], "CLOSED"
        )

    # -- 4. 闭店仍可到门口 / 可回家 -------------------------------------------

    def test_closed_cafe_door_is_walkable_without_store_affordances(self) -> None:
        saturday_noon = self.at(2026, 10, 17, 13)
        applied = self.walk("street_front", saturday_noon)
        self.assertEqual(applied["destination_availability"]["status"], "OPEN")
        self.walk("street_corner", saturday_noon)
        into_cafe = self.walk("cafe", saturday_noon)
        self.assertEqual(into_cafe["destination_availability"]["status"], "CLOSED")
        self.assertEqual(self.position(), "cafe")

        catalog = self.catalog(saturday_noon)
        cafe = self.entry(catalog, "cafe")
        self.assertEqual(cafe["availability"]["status"], "CLOSED")
        self.assertEqual(cafe["affordances"], [])
        self.assertEqual(cafe["observed"]["status"], "visible_here")
        # 店里没有店内 affordance，但路线事实仍在：回家有真实路段。
        home = self.entry(catalog, "home")
        self.assertEqual(home["reachability"]["status"], "reachable")
        self.assertEqual(home["reachability"]["walk_seconds"], 330)
        self.assertEqual(home["reachability"]["route"],
                         ["street_corner", "street_front", "home"])

        self.walk("street_corner", saturday_noon)
        self.walk("street_front", saturday_noon)
        self.walk("home", saturday_noon)
        self.assertEqual(self.position(), "home")

    def test_closed_door_is_allowed_in_timed_travel_too(self) -> None:
        env = build_env(PACKAGE, self.home, self.package.world_id, travel=True)
        self.wrd.reset_cache()
        now = self.at(2026, 10, 17, 13)
        first = self.walk("street_front", now, env=env)
        self.assertEqual(first["travel"]["route_duration_seconds"], 90)
        now = now + timedelta(seconds=90)
        arrival = self.wtd.realize_due_travel(
            hermes_home=self.home, environment=env, now=now
        )
        self.assertEqual(arrival["status"], "arrived")
        self.assertEqual(self.position(env=env, now=now), "street_front")
        self.walk("street_corner", now, env=env)
        now = now + timedelta(seconds=120)
        self.wtd.realize_due_travel(hermes_home=self.home, environment=env, now=now)
        started = self.walk("cafe", now, env=env)
        self.assertEqual(started["status"], "applied")
        self.assertEqual(started["reason_code"], "TRAVEL_STARTED")
        self.assertEqual(started["destination_availability"]["status"], "CLOSED")
        self.assertEqual(
            started["postcondition"]["expected_destination_location_id"], "cafe"
        )
        # 计时旅程的出发事实不变：世界位置仍是街口，直到真实到达（用显式时钟读，
        # 免得 read_move_state 拿真实墙钟把这段在途行程提前结算成到达）。
        self.assertEqual(self.position(env=env, now=now), "street_corner")
        now = now + timedelta(seconds=120)
        arrival = self.wtd.realize_due_travel(
            hermes_home=self.home, environment=env, now=now
        )
        self.assertEqual(arrival["status"], "arrived")
        self.assertEqual(self.position(env=env, now=now), "cafe")
        self.assertEqual(
            self.entry(self.catalog(now, env=env), "cafe")["affordances"], []
        )

    # -- 5. 未知 / 不可读：如实报告原因 ---------------------------------------

    def test_unknown_and_unreadable_report_reasons_without_fake_offers(self) -> None:
        fresh = work_dir("w-daily-empty-")
        empty_env = build_env(PACKAGE, fresh, self.package.world_id, travel=False)
        empty = self.wdo.location_opportunity_catalog(
            empty_env, self.at(2026, 10, 16, 13), hermes_home=fresh
        )
        self.assertEqual(empty["status"], "unavailable")
        self.assertEqual(empty["reason_code"], "WORLD_NOT_INITIALIZED")
        self.assertEqual(empty["opportunities"], [])

        self.wrd.reset_cache()
        broken = {**self.env, "CHIYO_WORLD_PACKAGE": str(SANDBOX / "w-daily-missing")}
        unreadable = self.wdo.location_opportunity_catalog(
            broken, self.at(2026, 10, 16, 13), hermes_home=self.home
        )
        self.assertEqual(unreadable["status"], "unavailable")
        self.assertEqual(unreadable["reason_code"], "WORLD_PACKAGE_UNREADABLE")
        self.assertEqual(unreadable["opportunities"], [])
        self.wrd.reset_cache()

        with mock.patch.object(
            self.wdo.wdr, "location_availability", side_effect=RuntimeError("boom")
        ):
            degraded = self.catalog(self.at(2026, 10, 16, 13))
        self.assertEqual(degraded["status"], "available")
        cafe = self.entry(degraded, "cafe")
        self.assertEqual(cafe["availability"]["status"], "UNKNOWN")
        self.assertEqual(cafe["availability"]["reason_code"], "DAILY_RHYTHM_UNAVAILABLE")
        self.assertEqual(cafe["affordances"], [])
        self.assertFalse(cafe["action_grant"])

    # -- 6. 只读：0 写 / 0 candidate / 0 模型 ---------------------------------

    def test_observation_is_read_only(self) -> None:
        now = self.at(2026, 10, 16, 13)
        before = instance_digest(self.home)
        observation = self.wls.observe_world(
            hermes_home=self.home, now=now, environment=self.env
        )
        catalog = self.catalog(now)
        after = instance_digest(self.home)
        self.assertEqual(before, after)
        encoded = json.dumps({"observation": observation, "catalog": catalog},
                             ensure_ascii=False)
        self.assertNotIn("candidates", encoded)
        self.assertNotIn("model_calls", encoded)
        self.assertEqual(observation["world_revision"], 1)
        self.assertFalse(catalog["action_grant"])
        self.assertEqual((self.home / "data" / "life_execution.jsonl").read_text(
            encoding="utf-8"), "")

    # -- 7. 真实走道秒数 -------------------------------------------------------

    def test_walk_seconds_are_real_positive_route_sums(self) -> None:
        catalog = self.catalog(self.at(2026, 10, 16, 13))
        expectations = {
            "street_front": (["street_front"], 90),
            "street_corner": (["street_front", "street_corner"], 210),
            "reading_point": (["street_front", "reading_point"], 240),
            "cafe": (["street_front", "street_corner", "cafe"], 330),
            "park": (["street_front", "street_corner", "park"], 390),
        }
        for location_id, (route, seconds) in expectations.items():
            with self.subTest(location=location_id):
                item = self.entry(catalog, location_id)
                self.assertEqual(item["reachability"]["route"], route)
                self.assertEqual(item["reachability"]["walk_seconds"], seconds)
                self.assertGreater(seconds, 0)
                self.assertEqual(item["reachability"]["source"], "world_topology")
        self.assertEqual(self.entry(catalog, "home")["reachability"]["status"], "here")


if __name__ == "__main__":
    unittest.main()
