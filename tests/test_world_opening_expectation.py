"""W 包（F-096/F-097 前置）定向验收：World 侧有限未来营业预期。

被测 owner：``world_daily_rhythm.location_opening_expectation``（以及目录
``world_daily_opportunities`` 新增的 ``opening_expectation`` 字段）。

覆盖范围（只跑本行为定向用例，不替代全仓 / World 全套）：

* 与 ``location_availability`` 同一小时语义：开/关边界、weekdays_only 周末、
  跨日翻页、常开；
* 严格 aware 时钟；未知世界 / 未知地点 / 规则读不懂 / schema 表达不了的跨午夜
  规则 / 8 天窗口内找不到，都 fail closed，不发明营业事实；
* 规则指纹只做源变更识别（不是签名、不是权限）；
* 目录新增字段不改 ``NOW_ONLY`` / ``future: not_promised``、不把远处内景当观察、
  零世界写入。

世界拓扑与写锁用 POSIX（原生 Windows 自动跳过）；纯 rhythm 用例不落盘。
fixture 目录保留在 ``/tmp/nyaw05r.jdK3q/fa-world-*``，不清理。
"""

from __future__ import annotations

import hashlib
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

import world_daily_rhythm as wdr  # noqa: E402
import world_runtime_data as wrd  # noqa: E402

posix_only = unittest.skipIf(
    os.name == "nt", "rc9 世界运行时需要 POSIX 文件锁（在 WSL/Linux 上运行）"
)

PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
#: 并行写集保留的唯一工作目录；不存在时退回系统临时目录。
SANDBOX = Path("/tmp/nyaw05r.jdK3q")
TOKYO = ZoneInfo("Asia/Tokyo")
#: 内建汐见市世界（没有 CHIYO_WORLD_PACKAGE 时的活动世界）。
BUILTIN_ENV: dict[str, str] = {}


def work_dir(prefix: str) -> Path:
    base = str(SANDBOX) if SANDBOX.is_dir() else None
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TOKYO)


class _StubWorld:
    """只有 ``location_rule`` 的假世界：用来造 schema 表达不了的规则。"""

    def __init__(
        self,
        rules: dict,
        *,
        world_id: str = "stub_world",
        timezone_name: str = "Asia/Tokyo",
        is_custom: bool = True,
    ) -> None:
        self.world_id = world_id
        self.timezone_name = timezone_name
        self.is_custom = is_custom
        self.locations = {key: {"name": key} for key in rules}
        self._rules = rules

    def location_rule(self, location_id: str):
        try:
            return self._rules[location_id]
        except KeyError:
            raise wrd.WorldDataError(f"unknown location_id: {location_id}") from None


class OpeningExpectationRuleTest(unittest.TestCase):
    """内建汐见市规则上的开/关边界、周末、跨日、常开。"""

    def setUp(self) -> None:
        wrd.reset_cache()
        self.addCleanup(wrd.reset_cache)

    def expectation(self, location_id: str, now: datetime, **kwargs) -> dict:
        return wdr.location_opening_expectation(
            location_id, now, environment=kwargs.pop("environment", BUILTIN_ENV), **kwargs
        )

    def availability(self, location_id: str, now: datetime) -> dict:
        return wdr.location_availability(location_id, now, environment=BUILTIN_ENV)

    def test_open_close_boundaries_match_the_availability_owner(self) -> None:
        cases = [
            # (时刻, 现在开没开, window_state, expected_open_at, window_end)
            (at(2026, 10, 19, 5, 59), "CLOSED", "opens_later",
             at(2026, 10, 19, 6), at(2026, 10, 19, 22)),
            (at(2026, 10, 19, 6, 0), "OPEN", "open_now",
             at(2026, 10, 19, 6), at(2026, 10, 19, 22)),
            (at(2026, 10, 19, 12, 0), "OPEN", "open_now",
             at(2026, 10, 19, 12), at(2026, 10, 19, 22)),
            (at(2026, 10, 19, 21, 59), "OPEN", "open_now",
             at(2026, 10, 19, 21, 59), at(2026, 10, 19, 22)),
            (at(2026, 10, 19, 22, 0), "CLOSED", "opens_later",
             at(2026, 10, 20, 6), at(2026, 10, 20, 22)),
        ]
        for now, status, window_state, expected, window_end in cases:
            with self.subTest(as_of=now.isoformat()):
                result = self.expectation("park", now)  # 公园 6..22，无工作日限制
                self.assertEqual(result["status"], "available", result)
                # 与营业 owner 的“现在开没开”完全一致，不另造一套判断。
                self.assertEqual(self.availability("park", now)["status"], status)
                self.assertEqual(result["window_state"], window_state)
                self.assertEqual(result["expected_open_at"], expected.isoformat())
                self.assertEqual(result["window_end"], window_end.isoformat())
                self.assertEqual(result["window_end_utc"], window_end.astimezone(
                    ZoneInfo("UTC")).isoformat())
                self.assertEqual(result["rule"], {
                    "open_hour": 6, "close_hour": 22, "weekdays_only": False,
                })
                self.assertEqual(result["rule_kind"], "hourly_window")
                self.assertFalse(result["always_open"])
                self.assertTrue(result["requires_recheck"])
                self.assertFalse(result["action_grant"])
                self.assertEqual(result["world_id"], wrd.SHIOMI_WORLD_ID)
                self.assertEqual(result["location_id"], "park")
                self.assertEqual(result["source"], wdr.RULE_SOURCE_BUILTIN)
                self.assertEqual(result["timezone"], "Asia/Tokyo")
                self.assertEqual(result["timezone_source"], "world")
                self.assertEqual(result["as_of"], now.isoformat())
        # 现在开着时 expected_open_at 就等于 as_of（可考虑的起点），窗口起点另给。
        open_now = self.expectation("park", at(2026, 10, 19, 12))
        self.assertEqual(open_now["expected_open_at"], open_now["as_of"])
        self.assertEqual(open_now["window_open_at"], at(2026, 10, 19, 6).isoformat())

    def test_weekdays_only_skips_the_weekend_and_crosses_the_week(self) -> None:
        saturday = at(2026, 10, 17, 13)
        weekend = self.expectation("school", saturday)  # 学校 8..18 且 weekdays_only
        self.assertEqual(self.availability("school", saturday)["reason_code"],
                         "WEEKEND_CLOSED")
        self.assertEqual(weekend["status"], "available")
        self.assertEqual(weekend["window_state"], "opens_later")
        self.assertEqual(weekend["expected_open_at"], at(2026, 10, 19, 8).isoformat())
        self.assertEqual(weekend["window_end"], at(2026, 10, 19, 18).isoformat())
        self.assertEqual(weekend["rule"]["weekdays_only"], True)
        self.assertTrue(
            datetime.fromisoformat(weekend["expected_open_at"]) > saturday
        )

        friday_night = at(2026, 10, 16, 18)
        across_week = self.expectation("school", friday_night)
        self.assertEqual(across_week["window_state"], "opens_later")
        self.assertEqual(across_week["expected_open_at"],
                         at(2026, 10, 19, 8).isoformat())

        monday = at(2026, 10, 19, 9)
        inside = self.expectation("school", monday)
        self.assertEqual(inside["window_state"], "open_now")
        self.assertEqual(inside["window_end"], at(2026, 10, 19, 18).isoformat())
        self.assertEqual(inside["horizon_days"], 8)
        self.assertEqual(
            datetime.fromisoformat(inside["horizon_end"]) - monday,
            timedelta(days=8),
        )

    def test_always_open_locations_are_explicit_and_not_bounded(self) -> None:
        now = at(2026, 10, 19, 3, 15)
        home = self.expectation("home", now)  # home: open_hour/close_hour 都是 None
        self.assertEqual(home["status"], "available")
        self.assertEqual(home["window_state"], "always_open")
        self.assertTrue(home["always_open"])
        self.assertEqual(home["expected_open_at"], home["as_of"])
        self.assertIsNone(home["window_open_at"])
        self.assertIsNone(home["window_end"])
        self.assertEqual(home["rule_kind"], "always_open")
        self.assertEqual(home["rule"],
                         {"open_hour": None, "close_hour": None, "weekdays_only": False})
        self.assertTrue(home["requires_recheck"])
        self.assertFalse(home["action_grant"])

    def test_cross_day_boundary_moves_the_window_to_the_next_local_date(self) -> None:
        # 便利店 7..23（无工作日限制）：跨午夜前后窗口要落在正确的本地日期。
        late = self.expectation("convenience_store", at(2026, 10, 16, 22, 59))
        self.assertEqual(late["window_state"], "open_now")
        self.assertEqual(late["window_end"], at(2026, 10, 16, 23).isoformat())

        crossing = self.expectation("convenience_store", at(2026, 10, 16, 23, 59))
        self.assertEqual(crossing["window_state"], "opens_later")
        self.assertEqual(crossing["expected_open_at"], at(2026, 10, 17, 7).isoformat())
        self.assertEqual(crossing["window_end"], at(2026, 10, 17, 23).isoformat())
        self.assertNotEqual(crossing["as_of"][:10], crossing["expected_open_at"][:10])

        just_after = self.expectation("convenience_store", at(2026, 10, 17, 0, 1))
        self.assertEqual(self.availability("convenience_store",
                                           at(2026, 10, 17, 0, 1))["status"], "CLOSED")
        self.assertEqual(just_after["expected_open_at"], at(2026, 10, 17, 7).isoformat())

    def test_naive_now_is_rejected_instead_of_guessed(self) -> None:
        with self.assertRaises(wdr.DailyRhythmError):
            wdr.location_opening_expectation(
                "park", datetime(2026, 10, 19, 9), environment=BUILTIN_ENV
            )
        with self.assertRaises(wdr.DailyRhythmError):
            wdr.location_opening_expectation("park", "2026-10-19T09:00", environment=BUILTIN_ENV)  # type: ignore[arg-type]

    def test_unknown_timezone_fails_closed_and_override_is_declared(self) -> None:
        unknown = self.expectation(
            "park", at(2026, 10, 19, 12), timezone_name="Not/AZone"
        )
        self.assertEqual(unknown["status"], "unavailable")
        self.assertEqual(unknown["reason_code"], "TIMEZONE_UNKNOWN")
        self.assertIsNone(unknown["expected_open_at"])
        self.assertFalse(unknown["action_grant"])

        utc_now = datetime(2026, 10, 19, 23, 0, tzinfo=ZoneInfo("UTC"))
        override = self.expectation("park", utc_now, timezone_name="UTC")
        self.assertEqual(override["status"], "available")
        self.assertEqual(override["timezone"], "UTC")
        self.assertEqual(override["timezone_source"], "caller_override")
        self.assertEqual(override["world_timezone"], "Asia/Tokyo")
        # UTC 时钟 23:00 在公园 6..22 之外 → 次日 06:00 UTC 开门。
        self.assertEqual(override["expected_open_at"], "2026-10-20T06:00:00+00:00")

    def test_unknown_world_and_location_fail_closed(self) -> None:
        missing = {**os.environ, "CHIYO_WORLD_PACKAGE": str(SANDBOX / "fa-world-missing")}
        unreadable = wdr.location_opening_expectation(
            "park", at(2026, 10, 19, 12), environment=missing
        )
        self.assertEqual(unreadable["status"], "unavailable")
        self.assertEqual(unreadable["reason_code"], "WORLD_PACKAGE_UNREADABLE")
        self.assertIsNone(unreadable["expected_open_at"])
        self.assertNotIn("world_id", unreadable)
        self.assertTrue(unreadable["requires_recheck"])
        wrd.reset_cache()

        for bad in ("nowhere", "", "   ", None):
            with self.subTest(location=bad):
                unknown = wdr.location_opening_expectation(
                    bad, at(2026, 10, 19, 12), environment=BUILTIN_ENV  # type: ignore[arg-type]
                )
                self.assertEqual(unknown["status"], "unavailable")
                self.assertEqual(unknown["reason_code"], "LOCATION_UNKNOWN")
                self.assertEqual(unknown["world_id"], wrd.SHIOMI_WORLD_ID)
                self.assertIsNone(unknown["window_end"])

    def test_unreadable_and_unsupported_rules_do_not_invent_opening_facts(self) -> None:
        now = at(2026, 10, 19, 9)
        broken_rules = {
            "bad_hour_type": {"open_hour": "8", "close_hour": 18, "weekdays_only": False},
            "bad_weekday_flag": {"open_hour": 8, "close_hour": 18, "weekdays_only": "no"},
            "out_of_range": {"open_hour": 24, "close_hour": 30, "weekdays_only": False},
            "not_a_mapping": ["open_hour", 8],
        }
        for location_id, rule in broken_rules.items():
            with self.subTest(location=location_id):
                stub = _StubWorld({location_id: rule})
                with mock.patch.object(wrd, "active_world", return_value=stub):
                    result = wdr.location_opening_expectation(location_id, now)
                self.assertEqual(result["status"], "unavailable", result)
                self.assertEqual(result["reason_code"], "LOCATION_RULE_UNREADABLE")
                self.assertIsNone(result["expected_open_at"])
                self.assertNotIn("rule_fingerprint", result)
                self.assertFalse(result["action_grant"])

        unsupported = {
            "cross_midnight": {"open_hour": 20, "close_hour": 4, "weekdays_only": False},
            "missing_close_hour": {"open_hour": 9, "close_hour": None, "weekdays_only": False},
        }
        for rule_kind, rule in unsupported.items():
            with self.subTest(rule_kind=rule_kind):
                stub = _StubWorld({"night_shop": rule})
                with mock.patch.object(wrd, "active_world", return_value=stub):
                    result = wdr.location_opening_expectation("night_shop", now)
                self.assertEqual(result["status"], "unavailable", result)
                self.assertEqual(result["reason_code"], "OPENING_RULE_UNSUPPORTED")
                self.assertEqual(result["rule_kind"], rule_kind)
                # 跨午夜规则不另造营业事实：没有任何“预计开门”时间。
                self.assertIsNone(result["expected_open_at"])
                self.assertIsNone(result["window_end"])

        empty = _StubWorld({"closed_all_day": {
            "open_hour": 9, "close_hour": 9, "weekdays_only": False,
        }})
        with mock.patch.object(wrd, "active_world", return_value=empty):
            result = wdr.location_opening_expectation("closed_all_day", now)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "NO_OPENING_WINDOW")
        self.assertEqual(result["rule_kind"], "empty_window")
        self.assertIsNone(result["expected_open_at"])

    def test_horizon_is_bounded_and_reports_when_no_window_fits(self) -> None:
        self.assertEqual(wdr.MAX_EXPECTATION_HORIZON_DAYS, 8)
        now = at(2026, 10, 19, 23)
        result = self.expectation("park", now)
        self.assertLessEqual(result["horizon_days"], 8)
        self.assertEqual(
            datetime.fromisoformat(result["horizon_end"]) - now, timedelta(days=8)
        )
        # 上界收紧到 0 天时，闭店地点必须明确“窗口内没有开门”，而不是编一个。
        with mock.patch.object(wdr, "MAX_EXPECTATION_HORIZON_DAYS", 0):
            none_in_horizon = self.expectation("park", now)
        self.assertEqual(none_in_horizon["status"], "unavailable")
        self.assertEqual(none_in_horizon["reason_code"], "NO_WINDOW_WITHIN_HORIZON")
        self.assertIsNone(none_in_horizon["expected_open_at"])
        # 常开地点不受搜索上界影响。
        with mock.patch.object(wdr, "MAX_EXPECTATION_HORIZON_DAYS", 0):
            always = self.expectation("home", now)
        self.assertEqual(always["status"], "available")
        self.assertTrue(always["always_open"])

    def test_rule_fingerprint_is_change_detection_only(self) -> None:
        first = self.expectation("park", at(2026, 10, 19, 12))
        later = self.expectation("park", at(2026, 10, 20, 3))
        self.assertEqual(first["rule_fingerprint"], later["rule_fingerprint"])
        self.assertTrue(first["rule_fingerprint"].startswith("sha256:"))
        self.assertEqual(len(first["rule_fingerprint"]), len("sha256:") + 64)
        self.assertEqual(first["rule_fingerprint_purpose"], "source_change_detection_only")
        self.assertIn("open_hour", first["rule_fingerprint_scope"])
        for forbidden in ("signature", "permission", "granted", "allowed"):
            self.assertNotIn(forbidden, first)

        other = self.expectation("convenience_store", at(2026, 10, 19, 12))
        self.assertNotEqual(first["rule_fingerprint"], other["rule_fingerprint"])

        changed_rule = _StubWorld({"park": {
            "open_hour": 6, "close_hour": 21, "weekdays_only": False,
        }})
        with mock.patch.object(wrd, "active_world", return_value=changed_rule):
            changed = wdr.location_opening_expectation("park", at(2026, 10, 19, 12))
        self.assertNotEqual(first["rule_fingerprint"], changed["rule_fingerprint"])
        self.assertEqual(changed["window_end"], at(2026, 10, 19, 21).isoformat())


def build_env(package_dir: Path, home: Path, world_id: str) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("CHIYO_WORLD_PACKAGE", "CHIYO_WORLD_HOME", "CHIYO_WORLD_ID",
                          "XINXI_DIR")}
    env.update(
        {
            "CHIYO_WORLD_PACKAGE": str(package_dir),
            "CHIYO_WORLD_HOME": str(home),
            "CHIYO_WORLD_ID": world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "0",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
        }
    )
    return env


def seed_world_state(package, home: Path) -> None:
    """用 owner store 建世界状态/时间线（World 侧事实，不 import Body 基座）。"""
    import world_foundation as wf
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
class OpeningExpectationCatalogTest(unittest.TestCase):
    """真实世界包的目录字段：只加“预计窗口”，不升级 NOW_ONLY、不写世界。"""

    def setUp(self) -> None:
        import world_daily_opportunities as wdo
        from components.worldgen import loader

        self.wdo = wdo
        self.wdr = wdr
        wrd.reset_cache()
        self.addCleanup(wrd.reset_cache)
        self.package = loader.load(PACKAGE)
        self.home = work_dir("fa-world-opening-")
        seed_world_state(self.package, self.home)
        self.env = build_env(PACKAGE, self.home, self.package.world_id)

    def catalog(self, now: datetime) -> dict:
        return self.wdo.location_opportunity_catalog(
            self.env, now, hermes_home=self.home
        )

    def entry(self, catalog: dict, location_id: str) -> dict:
        return next(item for item in catalog["opportunities"]
                    if item["location_id"] == location_id)

    def test_catalog_entry_adds_window_without_upgrading_validity(self) -> None:
        before = instance_digest(self.home)
        now = at(2026, 10, 19, 7, 30)  # 周一早上：咖啡店还没开
        catalog = self.catalog(now)
        after = instance_digest(self.home)
        self.assertEqual(before, after)  # 零世界写入
        self.assertEqual(catalog["status"], "available")
        self.assertEqual(catalog["validity"]["scope"], "NOW_ONLY")
        self.assertEqual(catalog["validity"]["future"], "not_promised")
        self.assertFalse(catalog["action_grant"])

        cafe = self.entry(catalog, "cafe")
        self.assertEqual(cafe["availability"]["status"], "CLOSED")
        self.assertEqual(cafe["availability"]["reason_code"], "OUTSIDE_OPEN_HOURS")
        self.assertEqual(cafe["observed"]["status"], "not_observed")  # 远处内景仍不可见
        expectation = cafe["opening_expectation"]
        self.assertEqual(expectation["status"], "available")
        self.assertEqual(expectation["window_state"], "opens_later")
        self.assertEqual(expectation["expected_open_at"],
                         at(2026, 10, 19, 8).isoformat())
        self.assertEqual(expectation["window_end"], at(2026, 10, 19, 18).isoformat())
        self.assertEqual(expectation["source"], "world_package_schedule")
        self.assertEqual(expectation["world_id"], self.package.world_id)
        self.assertEqual(expectation["rule"]["weekdays_only"], True)
        self.assertTrue(expectation["requires_recheck"])
        self.assertFalse(expectation["action_grant"])
        # 与同一 owner 的直接调用等价（目录不自己算第二套）。
        direct = wdr.location_opening_expectation(
            "cafe", now, timezone_name=self.package.timezone_name, environment=self.env
        )
        self.assertEqual(expectation, direct)

        home = self.entry(catalog, "home")
        self.assertEqual(home["opening_expectation"]["window_state"], "always_open")
        self.assertIsNone(home["opening_expectation"]["window_end"])

        # 短视图故意不带未来窗口（observe_world 的 16KB 预算）。
        compact = self.wdo.compact_projection(catalog)
        self.assertEqual(compact["scope"], "NOW_ONLY")
        self.assertEqual(compact["future"], "not_promised")
        for item in compact["opportunities"]:
            self.assertNotIn("opening_expectation", item)

    def test_weekend_closed_store_reports_next_weekday_window_in_catalog(self) -> None:
        saturday = at(2026, 10, 17, 13)
        catalog = self.catalog(saturday)
        cafe = self.entry(catalog, "cafe")
        self.assertEqual(cafe["availability"]["reason_code"], "WEEKEND_CLOSED")
        self.assertEqual(cafe["affordances"], [])
        expectation = cafe["opening_expectation"]
        self.assertEqual(expectation["status"], "available")
        self.assertEqual(expectation["expected_open_at"],
                         at(2026, 10, 19, 8).isoformat())
        self.assertEqual(expectation["window_end"], at(2026, 10, 19, 18).isoformat())
        self.assertEqual(expectation["requires_recheck"], True)


if __name__ == "__main__":
    unittest.main()
