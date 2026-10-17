#!/usr/bin/env python3
"""I-r1：非产出停留（``stay:``）的**机会层**正负用例（真实 W 包 + 真实营业投影）。

本文件只证明"她此刻能在这儿待一会儿"这件事的事实来源与失败边界，不装配 Life runtime；
整条连续日常见 ``tests/test_world_daily_life_continuous.py``。两者不继承任何既有 TestCase，
所以不会误收旧模块里的几十个用例。

覆盖：
1. 同一份世界包里，公园在周六中午 OPEN → 停留描述符**来自公园自己的 affordance**
   （``SIT``），时长由 Life 侧固定表给出（300s），目标写成 ``stay:park:SIT:300``；
2. 同一世界包，周六 06:00 公园闭门 → 该地点**没有任何停留机会**（不补一个"可能开着"）；
   咖啡店（周末闭店）同理：店门口仍可走到（MOVE 由 owner 的 action space 给出），
   但店内没有 affordance、也没有停留机会；
3. 目录不可读 → 一个停留机会都不产出（fail closed），绝不把"读不到"变成"就在这儿待着"。

需要 POSIX 只因为仓库其它夹具同一套 sys.path 约定；这里没有 socket。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen.instance import init_instance  # noqa: E402

import world_move_opportunity_source as wmos  # noqa: E402

WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
TMP_ROOT = Path("/tmp/nyaw05r.jdK3q")
JST = timezone(timedelta(hours=9))


def saturday(hour: int, minute: int = 0, *, after: datetime | None = None) -> datetime:
    """A Saturday at ``hour`` JST, chosen but never invented from a model.

    ``after`` is only used to keep the injected clock in the same week as the caller's
    reference instant; the date itself is always a real Saturday.
    """

    anchor = (after or datetime.now(timezone.utc)).astimezone(JST)
    day = anchor.date()
    while True:
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)
        if candidate.weekday() == 5 and candidate > anchor:
            return candidate
        day = day + timedelta(days=1)


class StayOfferTest(unittest.TestCase):
    """一份真实世界包 + W 自己的营业投影，看停留机会从哪来、什么时候一个都不来。"""

    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        # ``mkdtemp`` already made the parent; the instance itself must be a fresh directory
        # (``init_instance`` refuses to overwrite anything).
        self.root = Path(tempfile.mkdtemp(prefix="ir1-play-", dir=str(TMP_ROOT)))
        self.home = self.root / "world-home"
        init_instance(WORLD_PACKAGE, self.home)
        self.environment = {"CHIYO_WORLD_HOME": str(self.home),
                            "HERMES_HOME": str(self.home),
                            "CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE)}

    # -- helpers -----------------------------------------------------------------

    def catalog_at(self, moment: datetime) -> dict:
        return wmos.read_location_catalog(self.environment, moment, hermes_home=str(self.home))

    def observation(self, place: str) -> dict:
        return {"location": {"place_id": place}, "world_revision": 1,
                "world_id": "daily_neighborhood_v1"}

    def offers(self, place: str, moment: datetime) -> list[dict]:
        moment_iso = moment.astimezone(timezone.utc).isoformat()
        return wmos.observed_stay_opportunities(
            self.observation(place), catalog=self.catalog_at(moment),
            occurred_at=moment_iso, observed_at=moment_iso)

    def entry(self, catalog: dict, place: str) -> dict:
        for item in catalog.get("opportunities") or []:
            if str(item.get("location_id")) == place:
                return item
        return {}

    # -- 1. 公园中午：描述符来自公园自己的 affordance ----------------------------

    def test_a_park_stay_names_the_parks_own_affordance_and_a_fixed_stretch(self):
        noon = saturday(12)
        catalog = self.catalog_at(noon)
        self.assertEqual(catalog["status"], "available", catalog)
        self.assertEqual(catalog["schedule_source"], "world_package_schedule", catalog)
        park = self.entry(catalog, "park")
        self.assertEqual((park.get("availability") or {}).get("status"), "OPEN", park)
        self.assertIn("SIT", [item["affordance_id"] for item in park.get("affordances") or []])

        offers = self.offers("park", noon)
        self.assertEqual(len(offers), 1, offers)
        offer = offers[0]
        self.assertEqual(offer["target_refs"], ["stay:park:SIT:300"], offer)
        self.assertEqual(offer["location_ref"], "place:park", offer)
        self.assertEqual(offer["subject_id"], "chiyo.global", offer)
        self.assertTrue(offer["observed"])
        self.assertTrue(offer["valid_from"] < offer["valid_until"])
        # 计划就是"公园 + SIT + 300 秒"，可被任何一端原样解析回来
        self.assertEqual(wmos.parse_stay_target(offer["target_refs"][0]),
                         {"place": "park", "descriptor": "SIT", "seconds": 300,
                          "target_ref": "stay:park:SIT:300"})

    # -- 2. 闭店：门口能到，店内没有机会 ----------------------------------------

    def test_a_closed_shop_has_no_stay_and_no_inside_affordance_but_the_door_is_reachable(self):
        dawn = saturday(6)
        catalog = self.catalog_at(dawn)
        cafe = self.entry(catalog, "cafe")
        self.assertEqual((cafe.get("availability") or {}).get("status"), "CLOSED", cafe)
        self.assertEqual((cafe.get("availability") or {}).get("reason_code"),
                         "WEEKEND_CLOSED", cafe)
        self.assertEqual(cafe.get("affordances"), [], cafe)

        # 站在咖啡店门口：店内没有任何停留机会（也不冒充店内 affordance）
        self.assertEqual(self.offers("cafe", dawn), [])
        # 站在这儿也*能*走到（MOVE 由 owner 自己的 action space 给出，闭店不拦出发）
        moment_iso = dawn.astimezone(timezone.utc).isoformat()
        move = wmos.observed_move_opportunities(
            self.observation("street_corner"),
            action_space={"candidates": [
                {"verb": "MOVE", "params": {"destination_location_id": "cafe"}}]},
            occurred_at=moment_iso, observed_at=moment_iso)
        self.assertEqual([item["target_refs"] for item in move], [["place:cafe"]], move)
        # 同一时刻公园开着：同一次观察里既有"能走到"也有"能待一会儿"，两者互不冒充
        stay = self.offers("park", dawn)
        self.assertEqual([item["target_refs"] for item in stay], [["stay:park:SIT:300"]], stay)

    def test_a_park_outside_its_hours_offers_no_stay_either(self):
        late = saturday(23)
        park = self.entry(self.catalog_at(late), "park")
        self.assertEqual((park.get("availability") or {}).get("status"), "CLOSED", park)
        self.assertEqual(park.get("affordances"), [], park)
        self.assertEqual(self.offers("park", late), [])

    # -- 3. 目录读不到：一个停留机会都不来 --------------------------------------

    def test_an_unreadable_catalog_produces_no_stay_at_all(self):
        noon = saturday(12)
        moment_iso = noon.astimezone(timezone.utc).isoformat()
        for broken in (None, {"status": "unavailable", "reason_code": "CATALOG_READ_FAILED",
                              "opportunities": []}):
            self.assertEqual(wmos.observed_stay_opportunities(
                self.observation("park"), catalog=broken,
                occurred_at=moment_iso, observed_at=moment_iso), [], broken)
        # 真实的读失败也走同一条路（没有世界包时目录不可读）
        broken_catalog = wmos.read_location_catalog({}, noon, hermes_home=None)
        self.assertEqual(broken_catalog["status"], "unavailable", broken_catalog)
        self.assertEqual(wmos.observed_stay_opportunities(
            self.observation("park"), catalog=broken_catalog,
            occurred_at=moment_iso, observed_at=moment_iso), [])


if __name__ == "__main__":
    unittest.main()
