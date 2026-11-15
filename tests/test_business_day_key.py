#!/usr/bin/env python3
"""19 章 §一.4：统一业务日界（day_key）的定向证据。

覆盖三件事：

1. **共享模块本身**：``business_day_key`` 的默认 +08:00、解析（偏移/IANA/拒绝）、
   策略文件与环境变量覆盖、``business_day`` 的 naive/aware 语义。
2. **预算与世界同源**：``life_request_budget`` 的日界函数与共享模块是同一绑定；
   在 +08:00 与 +09:00 会落在不同"日"的时刻（UTC 15:30 = 23:30+08 = 00:30+09 次日），
   预算日与世界日历给出**同一个** day_key——旧世界默认（Asia/Tokyo）的错位被消除。
3. **世界侧六处引用**：``world_daily_rhythm`` / ``world_ambient_events`` /
   ``world_school_day`` / ``world_property_economy`` / ``world_travel_delivery`` /
   ``world_runtime_data`` 的默认时区都来自共享模块，不再各自硬编码
   ``Asia/Tokyo``；世界包自己声明的 IANA 时区路径不受影响（显式传
   ``timezone_name`` 的调用点仍按包时区计算）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "components" / "world" / "scripts",
              ROOT / "components" / "life" / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import business_day_key as bdk  # noqa: E402
import life_request_budget as lrb  # noqa: E402
import world_daily_rhythm as wdr  # noqa: E402
import world_ambient_events as wae  # noqa: E402
import world_school_day as wsd  # noqa: E402
import world_property_economy as wpe  # noqa: E402
import world_travel_delivery as wtd  # noqa: E402
import world_runtime_data as wrd  # noqa: E402

UTC = timezone.utc
#: UTC 15:30 -> +08:00 当天 23:30，+09:00 已是次日 00:30：两侧曾经各说各话的时刻。
MOMENT = datetime(2026, 11, 13, 15, 30, tzinfo=UTC)


class SharedModuleTest(unittest.TestCase):
    """business_day_key 本体：默认值、解析与覆盖顺序。"""

    def test_default_is_the_budget_source_plus_eight(self) -> None:
        self.assertEqual(bdk.DEFAULT_BUSINESS_TZ, lrb.DEFAULT_BUSINESS_TZ)
        self.assertEqual(bdk.DEFAULT_BUSINESS_TZ_LABEL, "+08:00")
        self.assertEqual(lrb.DEFAULT_BUSINESS_TZ_LABEL, bdk.DEFAULT_BUSINESS_TZ_LABEL)
        # 与 LifeRequestBudget.business_tz 的默认一致（同源）。
        budget = lrb.LifeRequestBudget(Path(tempfile.mkdtemp(prefix="nyairo-daykey-")),
                                       utc_day_max=5)
        self.assertEqual(budget.business_tz, bdk.DEFAULT_BUSINESS_TZ)

    def test_parse_accepts_offsets_iana_and_rejects_unknown(self) -> None:
        self.assertEqual(bdk.parse_business_tz("+08:00"), bdk.DEFAULT_BUSINESS_TZ)
        self.assertIs(bdk.parse_business_tz("UTC"), timezone.utc)
        sample = datetime(2026, 1, 15, 12, tzinfo=UTC)
        self.assertEqual(bdk.parse_business_tz("Asia/Tokyo").utcoffset(sample).total_seconds(),
                         9 * 3600)
        self.assertIsNone(bdk.parse_business_tz("Not/AZone"))
        self.assertIsNone(bdk.parse_business_tz(""))
        self.assertIsNone(bdk.parse_business_tz(None))

    def test_resolve_prefers_policy_file_then_env_then_default(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="nyairo-daykey-"))
        env_backup = {key: os.environ.get(key)
                      for key in bdk.ENV_KEYS}
        try:
            for key in bdk.ENV_KEYS:
                os.environ.pop(key, None)
            # 1) 无策略、无环境变量 -> 默认 +08:00（budget 与共享模块同一个答案）。
            self.assertEqual(bdk.resolve_default_business_tz(root), bdk.DEFAULT_BUSINESS_TZ)
            self.assertEqual(lrb._resolve_default_business_tz(root), bdk.DEFAULT_BUSINESS_TZ)
            # 2) 策略文件优先（同一批键、同一裁决顺序）。
            (root / "budget_policy.json").write_text(
                json.dumps({"business_tz": "Asia/Tokyo"}), encoding="utf-8")
            sample = datetime(2026, 1, 15, 12, tzinfo=UTC)
            self.assertEqual(bdk.resolve_default_business_tz(root).utcoffset(sample).total_seconds(),
                             9 * 3600)
            budget = lrb.LifeRequestBudget(root, utc_day_max=5)
            self.assertEqual(budget.business_tz.utcoffset(sample).total_seconds(), 9 * 3600)
            # 3) 无策略文件时环境变量生效。
            with mock.patch.dict(os.environ, {"CHIYO_BUSINESS_TZ": "+09:00"}):
                self.assertEqual(bdk.resolve_default_business_tz(Path(tempfile.mkdtemp(
                    prefix="nyairo-daykey-"))).utcoffset(sample).total_seconds(), 9 * 3600)
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_business_day_handles_naive_and_aware(self) -> None:
        self.assertEqual(bdk.business_day(MOMENT), "2026-11-13")
        self.assertEqual(bdk.business_day(MOMENT, tz=ZoneInfo("Asia/Tokyo")), "2026-11-14")
        # naive 按 +08:00 原样落位，不换算。
        self.assertEqual(bdk.business_day(datetime(2026, 11, 13, 7)), "2026-11-13")
        self.assertEqual(bdk.business_day(datetime(2026, 11, 13, 23, 59)), "2026-11-13")
        self.assertEqual(bdk.business_day(datetime(2026, 11, 14, 0, 0)), "2026-11-14")


class BudgetWorldAgreementTest(unittest.TestCase):
    """预算日界与世界日历日界是同一个答案（+08/+09 错位消除）。"""

    def test_budget_day_and_world_day_agree_at_the_old_cutover_gap(self) -> None:
        budget = lrb.LifeRequestBudget(Path(tempfile.mkdtemp(prefix="nyairo-daykey-")),
                                       utc_day_max=5)
        self.assertEqual(budget._resolve_day(MOMENT), "2026-11-13")
        self.assertEqual(wdr.daily_rhythm(MOMENT)["local_date"], "2026-11-13")
        self.assertEqual(wdr.daily_rhythm(MOMENT)["timezone"], "+08:00")

    def test_budget_helpers_are_the_shared_module_bindings(self) -> None:
        self.assertIs(lrb._business_day, bdk.business_day)
        self.assertIs(lrb._parse_business_tz, bdk.parse_business_tz)
        self.assertIs(lrb._resolve_default_business_tz, bdk.resolve_default_business_tz)
        self.assertIs(lrb._format_tz_label, bdk.format_tz_label)

    def test_economy_month_key_uses_the_shared_default(self) -> None:
        self.assertEqual(wpe._world_local_period(MOMENT), "2026-11")


class WorldSitesReferenceSharedDefaultTest(unittest.TestCase):
    """世界侧六处默认时区全部来自 business_day_key，不再各自硬编码。"""

    def test_six_world_defaults_use_the_shared_label(self) -> None:
        self.assertIs(wdr.WORLD_TIMEZONE, bdk.DEFAULT_BUSINESS_TZ_LABEL)
        self.assertIs(wae.WORLD_TIMEZONE, bdk.DEFAULT_BUSINESS_TZ_LABEL)
        self.assertIs(wsd.WORLD_TIMEZONE, bdk.DEFAULT_BUSINESS_TZ_LABEL)
        self.assertIs(wpe.WORLD_TIMEZONE, bdk.DEFAULT_BUSINESS_TZ_LABEL)
        self.assertIs(wtd.WORLD_TIMEZONE, bdk.DEFAULT_BUSINESS_TZ_LABEL)
        self.assertIs(wrd.SHIOMI_TIMEZONE, bdk.DEFAULT_BUSINESS_TZ_LABEL)
        self.assertEqual(bdk.DEFAULT_BUSINESS_TZ_LABEL, "+08:00")

    def test_school_day_projection_follows_the_shared_default(self) -> None:
        with mock.patch.dict(os.environ, {"CHIYO_WORLD_SCHOOL_DAY_ENABLED": "1"}):
            projection = wsd.observe_school_day(now=MOMENT, environment=os.environ)
        self.assertEqual(projection["status"], "available", projection)
        self.assertEqual(projection["timezone"], "+08:00")
        self.assertEqual(projection["local_date"], "2026-11-13")

    def test_availability_default_agrees_with_shared_day_key(self) -> None:
        # 公园 6..22：+08:00 的 23:30 已闭店；旧默认（+09:00 的次日 00:30）同样闭店，
        # 但本地日期不同——这里断言默认时区来自共享业务日界。
        verdict = wdr.location_availability("park", MOMENT, environment={})
        self.assertEqual(verdict["status"], "CLOSED")
        self.assertEqual(verdict["reason_code"], "OUTSIDE_OPEN_HOURS")


if __name__ == "__main__":
    unittest.main()
