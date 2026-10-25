"""世界环境事件目录扩充与有限跨天的离线定向测试。

覆盖（只跑本行为，不替代全库/World 全套）：

* 默认汐见市有效模板 20–30 条且去重，日常/会变/有限跨天三类计数；
* 每日投影仍受既有 ``MAX_EVENTS_PER_DAY`` 与字段集合约束；
* 单日事件窗前/窗内/窗尾/窗后，时区午夜按 World 时区而不是墙钟；
* 有限跨天事件同一 ``event_id`` 连续两天、阶段叙述不同、期限后到期消失，
  +8/+16 天与远未来都不会自动复活，也不会换新身份重演；
* 地点过滤不串、未知地点如实拒绝；
* 世界包 v2 路径 16 单日 + 8 有限跨天；随仓未改 v1 包仍可加载；
  改前 v1 输出与固定 golden 完全一致；
* 事件只是环境事实，不含动作许可/后果/价格权威字段，不声称动作已发生。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
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

PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
LEGACY_PACKAGE = ROOT / "examples" / "worlds" / "nagiura"
FIXTURES = ROOT / "tests" / "fixtures"
TOKYO = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")


class AmbientEventExpansionTest(unittest.TestCase):
    """默认世界模板目录与投影的确定性行为。"""

    def setUp(self) -> None:
        import world_ambient_events as wae
        import world_runtime_data as wrd

        self.wae = wae
        self.wrd = wrd
        wrd.reset_cache()
        self.addCleanup(wrd.reset_cache)
        # 显式空环境：不继承宿主 CHIYO_WORLD_PACKAGE，确保是内建汐见市。
        self.env: dict[str, str] = {}

    # -- helpers ---------------------------------------------------------------

    def catalog(self) -> dict:
        return self.wae.ambient_template_catalog(self.env)

    def finite_types(self) -> set[str]:
        return {
            row["event_type"]
            for row in self.catalog()["templates"]
            if row["finite"]
        }

    def at(self, day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
        return datetime(2026, month, day, hour, minute, tzinfo=TOKYO)

    # -- 1. 目录规模与分类 -----------------------------------------------------

    def test_catalog_has_twenty_to_thirty_unique_templates_in_three_categories(self) -> None:
        catalog = self.catalog()
        self.assertEqual(catalog["world_id"], "shiomi_city")
        self.assertGreaterEqual(catalog["total"], 20)
        self.assertLessEqual(catalog["total"], 30)
        self.assertEqual(catalog["unique_total"], catalog["total"])
        self.assertEqual(
            catalog["categories"],
            {"daily": 8, "changing": 8, "cross_day": 8},
        )
        self.assertTrue(catalog["cross_day_supported"])
        self.assertEqual(catalog["max_events_per_day"], 3)
        event_types = [row["event_type"] for row in catalog["templates"]]
        self.assertEqual(len(event_types), len(set(event_types)))
        for row in catalog["templates"]:
            self.assertIn(row["location_id"], self.wae.KNOWN_LOCATION_IDS)
            self.assertIn(row["category"], {"daily", "changing", "cross_day"})
            if row["finite"]:
                self.assertTrue(row["cross_day"])
                self.assertTrue(row["identity"])
                self.assertTrue(row["end_reason"])
                self.assertTrue(row["phases"])
                self.assertLessEqual(row["starts_on"], row["ends_on"])
                for phase in row["phases"]:
                    self.assertLessEqual(row["starts_on"], phase["local_date"])
                    self.assertLessEqual(phase["local_date"], row["ends_on"])
            else:
                self.assertFalse(row["cross_day"])
                self.assertEqual(row["phases"], [])
                self.assertIsNone(row["identity"])

    def test_daily_projection_keeps_existing_bound_and_fields(self) -> None:
        expected_fields = {
            "event_id",
            "world_id",
            "local_date",
            "location_id",
            "event_type",
            "payload",
            "valid_from",
            "valid_until",
            "provenance",
        }
        finite_types = self.finite_types()
        for offset in range(0, 20):
            day = date(2026, 10, 20) + timedelta(days=offset)
            events = self.wae.materialize_ambient_events(day, environment=self.env)
            self.assertLessEqual(len(events), self.wae.MAX_EVENTS_PER_DAY)
            for event in events:
                self.assertEqual(set(event), expected_fields)
                self.assertEqual(set(event["payload"]), {"summary"})
                self.assertLessEqual(len(event["payload"]["summary"]), 240)
                self.assertEqual(event["world_id"], "shiomi_city")
                self.assertEqual(event["local_date"], day.isoformat())
                if event["event_type"] in finite_types:
                    self.assertEqual(event["provenance"], self.wae.FINITE_PROVENANCE)
                else:
                    self.assertEqual(
                        event["provenance"], "world_local_day_deterministic_v1"
                    )

    # -- 2. 单日窗口边界与时区午夜 --------------------------------------------

    def _find_single_day_event(self, start: date, days: int = 32):
        finite = self.finite_types()
        for offset in range(days):
            day = start + timedelta(days=offset)
            for event in self.wae.materialize_ambient_events(day, environment=self.env):
                if event["event_type"] in finite:
                    continue
                valid_from = datetime.fromisoformat(event["valid_from"])
                valid_until = datetime.fromisoformat(event["valid_until"])
                # 需要同一天内闭合的窗口，才能分别测窗前/窗后。
                if valid_until.date() == day and valid_from.hour > 0:
                    return day, event
        self.fail("没有找到可测的同日闭合单日事件")

    def test_single_day_event_window_before_inside_tail_after(self) -> None:
        day, event = self._find_single_day_event(date(2026, 10, 20))
        valid_from = datetime.fromisoformat(event["valid_from"])
        valid_until = datetime.fromisoformat(event["valid_until"])

        def active(now: datetime) -> list[dict]:
            return self.wae.active_ambient_events(
                day, now, timezone_name="Asia/Tokyo", environment=self.env
            )

        ids = lambda items: [item["event_id"] for item in items]
        self.assertNotIn(event["event_id"], ids(active(valid_from - timedelta(minutes=1))))
        self.assertIn(event["event_id"], ids(active(valid_from)))
        self.assertIn(event["event_id"], ids(active(valid_until - timedelta(minutes=1))))
        self.assertNotIn(event["event_id"], ids(active(valid_until)))

    def test_world_timezone_midnight_not_wall_clock(self) -> None:
        day = date(2026, 10, 25)
        # 东京 2026-10-25 00:30 = UTC 2026-10-24 15:30。
        tokyo_midnight = self.at(25, 0, 30)
        utc_equivalent = tokyo_midnight.astimezone(UTC)
        self.assertEqual(utc_equivalent.date(), date(2026, 10, 24))

        same_day = self.wae.active_ambient_events(
            day, utc_equivalent, timezone_name="Asia/Tokyo", environment=self.env
        )
        previous_day = self.wae.active_ambient_events(
            date(2026, 10, 24), utc_equivalent, timezone_name="Asia/Tokyo", environment=self.env
        )
        self.assertEqual(previous_day, [])
        # 东京当日事件按东京日期生成，不是按 UTC 的 10-24。
        for event in same_day:
            self.assertEqual(event["local_date"], "2026-10-25")

    # -- 3. 有限跨天身份、阶段连续与到期 --------------------------------------

    def _finite_event_on(self, day: date) -> dict:
        events = self.wae.materialize_ambient_events(day, environment=self.env)
        finite = [
            item
            for item in events
            if item["event_type"] in self.finite_types()
        ]
        self.assertEqual(len(finite), 1, finite)
        return finite[0]

    def test_finite_event_same_identity_across_two_days_then_expire(self) -> None:
        day = date(2026, 10, 24)
        event = self._finite_event_on(day)
        event_id = event["event_id"]
        event_type = event["event_type"]
        self.assertEqual(event["provenance"], self.wae.FINITE_PROVENANCE)

        next_events = self.wae.materialize_ambient_events(
            day + timedelta(days=1), environment=self.env
        )
        continued = [item for item in next_events if item["event_id"] == event_id]
        self.assertEqual(len(continued), 1, next_events)
        self.assertEqual(continued[0]["event_type"], event_type)
        self.assertNotEqual(
            continued[0]["payload"]["summary"], event["payload"]["summary"]
        )
        self.assertEqual(continued[0]["local_date"], (day + timedelta(days=1)).isoformat())

        # 期限结束（第二天之后）不再出现同一身份。
        expired_day = day + timedelta(days=2)
        expired = self.wae.materialize_ambient_events(expired_day, environment=self.env)
        self.assertNotIn(event_id, {item["event_id"] for item in expired})
        self.assertNotIn(event_type, {item["event_type"] for item in expired})

    def test_finite_event_never_revives_after_expiry(self) -> None:
        day = date(2026, 10, 24)
        event = self._finite_event_on(day)
        event_id = event["event_id"]
        event_type = event["event_type"]
        for offset in (8, 16, 40, 120, 365):
            later = self.wae.materialize_ambient_events(
                day + timedelta(days=offset), environment=self.env
            )
            self.assertNotIn(event_id, {item["event_id"] for item in later})
            self.assertNotIn(event_type, {item["event_type"] for item in later})
        # 该类型只在作者给定窗口出现，不会滚动生成新的身份。
        observed = []
        for offset in range(0, 30):
            current = day + timedelta(days=offset)
            observed.extend(
                item
                for item in self.wae.materialize_ambient_events(
                    current, environment=self.env
                )
                if item["event_type"] == event_type
            )
        self.assertTrue(observed)
        self.assertTrue(all(item["event_id"] == event_id for item in observed))

    def test_repeated_reads_at_same_time_are_stable(self) -> None:
        day = date(2026, 10, 25)
        first = self.wae.materialize_ambient_events(day, environment=self.env)
        second = self.wae.materialize_ambient_events(day, environment=self.env)
        self.assertEqual(first, second)
        moment = self.at(25, 12)
        a = self.wae.active_ambient_events(
            day, moment, timezone_name="Asia/Tokyo", environment=self.env
        )
        b = self.wae.active_ambient_events(
            day, moment, timezone_name="Asia/Tokyo", environment=self.env
        )
        self.assertEqual(a, b)

    # -- 4. 地点视野不串 -------------------------------------------------------

    def test_location_filter_and_unknown_location(self) -> None:
        day = date(2026, 10, 25)
        moment = self.at(25, 12)
        home_only = self.wae.active_ambient_events(
            day,
            moment,
            timezone_name="Asia/Tokyo",
            relevant_location_ids={"home"},
            environment=self.env,
        )
        for event in home_only:
            self.assertEqual(event["location_id"], "home")
        with self.assertRaises(self.wae.AmbientWorldEventError):
            self.wae.active_ambient_events(
                day,
                moment,
                timezone_name="Asia/Tokyo",
                relevant_location_ids={"moon"},
                environment=self.env,
            )

    # -- 5. 世界包 v2 路径：16 单日 + 8 有限跨天 ------------------------------

    def test_package_v2_world_has_sixteen_single_day_and_eight_finite(self) -> None:
        environment = {"CHIYO_WORLD_PACKAGE": str(PACKAGE)}
        self.wrd.reset_cache()
        catalog = self.wae.ambient_template_catalog(environment)
        self.assertEqual(catalog["world_id"], "daily_neighborhood_v1")
        self.assertEqual(catalog["total"], 24)
        self.assertEqual(catalog["categories"], {"daily": 16, "changing": 0, "cross_day": 8})
        self.assertTrue(catalog["cross_day_supported"])
        finite_rows = [row for row in catalog["templates"] if row["finite"]]
        self.assertEqual(len(finite_rows), 8)
        for row in finite_rows:
            self.assertTrue(row["identity"])
            self.assertTrue(row["end_reason"])
            self.assertEqual(len(row["phases"]), 2)
        allowed = self.wrd.active_world(environment).locations
        finite_types = {row["event_type"] for row in finite_rows}
        day = date(2026, 10, 24)
        for offset in range(0, 10):
            current = day + timedelta(days=offset)
            events = self.wae.materialize_ambient_events(
                current, environment=environment
            )
            self.assertLessEqual(len(events), 3)
            for event in events:
                self.assertIn(event["location_id"], allowed)
                if event["event_type"] in finite_types:
                    self.assertEqual(event["provenance"], self.wae.FINITE_PROVENANCE)
                    self.assertTrue(event["event_id"].startswith("ambient-finite-"))
                else:
                    # 单日身份仍直接带当天日期（旧语义）。
                    self.assertTrue(
                        event["event_id"].startswith(f"ambient-{current.isoformat()}-")
                    )

    def test_package_v2_finite_identity_is_package_authoritative(self) -> None:
        environment = {"CHIYO_WORLD_PACKAGE": str(PACKAGE)}
        self.wrd.reset_cache()
        day = date(2026, 10, 24)
        first = self.wae.materialize_ambient_events(day, environment=environment)
        next_day = self.wae.materialize_ambient_events(
            day + timedelta(days=1), environment=environment
        )
        finite_ids = {
            event["event_id"]
            for event in first
            if event["event_id"].startswith("ambient-finite-")
        }
        self.assertTrue(finite_ids)
        self.assertTrue(
            finite_ids
            & {event["event_id"] for event in next_day},
            (finite_ids, next_day),
        )
        # 期限内稳定；期限后（+8/+16）同一身份不复活。
        for offset in (8, 16):
            later = self.wae.materialize_ambient_events(
                day + timedelta(days=offset), environment=environment
            )
            self.assertFalse(finite_ids & {event["event_id"] for event in later})

    def test_shipped_v1_package_still_loads_as_single_day(self) -> None:
        environment = {"CHIYO_WORLD_PACKAGE": str(LEGACY_PACKAGE)}
        self.wrd.reset_cache()
        catalog = self.wae.ambient_template_catalog(environment)
        self.assertGreater(catalog["total"], 0)
        self.assertEqual(catalog["categories"]["cross_day"], 0)
        for row in catalog["templates"]:
            self.assertFalse(row["finite"])
            self.assertIsNone(row["identity"])
        day = date(2026, 10, 25)
        events = self.wae.materialize_ambient_events(day, environment=environment)
        self.assertEqual(len(events), 3)
        for event in events:
            self.assertTrue(event["event_id"].startswith(f"ambient-{day.isoformat()}-"))
            self.assertEqual(
                event["provenance"], "world_local_day_deterministic_v1"
            )

    def test_legacy_v1_fixture_matches_pre_change_golden(self) -> None:
        """改前 16 条 v1 包在固定日期上的输出必须与固定 golden 完全一致。"""

        from worldgen import schema

        legacy_doc = FIXTURES / "world_ambient_v1_legacy.json"
        golden = json.loads(
            (FIXTURES / "world_ambient_v1_legacy_golden.json").read_text(
                encoding="utf-8"
            )
        )
        tmp = Path(tempfile.mkdtemp(prefix="nyairo-ambient-v1-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        package = tmp / "package"
        shutil.copytree(PACKAGE, package)
        (package / "ambient.json").write_bytes(legacy_doc.read_bytes())
        schema.write_json(package / "MANIFEST.json", schema.build_manifest(package))
        environment = {"CHIYO_WORLD_PACKAGE": str(package)}
        self.wrd.reset_cache()
        self.addCleanup(self.wrd.reset_cache)
        for day_text, expected in golden["days"].items():
            actual = self.wae.materialize_ambient_events(
                date.fromisoformat(day_text), environment=environment
            )
            self.assertEqual(actual, expected, day_text)

    def test_unreadable_package_keeps_existing_failure_semantics(self) -> None:
        environment = {
            "CHIYO_WORLD_PACKAGE": str(ROOT / "examples" / "worlds" / "__missing__")
        }
        self.wrd.reset_cache()
        with self.assertRaises(self.wrd.WorldDataError):
            self.wae.materialize_ambient_events(date(2026, 10, 25), environment=environment)

    # -- 6. 只是环境事实，不冒充动作结果 -------------------------------------

    def test_templates_carry_no_action_or_consequence_authority(self) -> None:
        forbidden = {
            "action",
            "action_grant",
            "effect",
            "reward",
            "penalty",
            "quest",
            "goal",
            "emotion_effect",
            "price",
            "inventory",
        }
        catalog = self.catalog()
        for row in catalog["templates"]:
            self.assertFalse(forbidden.intersection(row))
            for phase in row["phases"]:
                self.assertFalse(forbidden.intersection(phase))
        for offset in range(0, 10):
            day = date(2026, 10, 20) + timedelta(days=offset)
            for event in self.wae.materialize_ambient_events(day, environment=self.env):
                self.assertFalse(forbidden.intersection(event))
                self.assertFalse(forbidden.intersection(event["payload"]))


if __name__ == "__main__":
    unittest.main()
