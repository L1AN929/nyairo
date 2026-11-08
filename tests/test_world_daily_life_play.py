#!/usr/bin/env python3
"""I-r1：非产出停留（``stay:``）的**机会层**正负用例（真实 W 包 + 真实营业投影）。

本文件只证明"她此刻能在这儿待一会儿"这件事的事实来源与失败边界，不装配 Life runtime；
整条连续日常见 ``tests/test_world_daily_life_continuous.py``。两者不继承任何既有 TestCase，
所以不会误收旧模块里的几十个用例。

覆盖：
1. 同一份世界包里，公园在周六中午 OPEN → 停留描述符**来自公园自己的 affordance**：
   正事 ``SIT``（Life 侧固定表给 300s，目标 ``stay:park:SIT:300``）在前，闲散项
   ``PEOPLE_WATCH``（180s，目标 ``stay:park:PEOPLE_WATCH:180``）作为独立一条在后；
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


class WorldPackageFixture(unittest.TestCase):
    """一份真实世界包 + 真实营业投影的共用夹具（本身不带用例）。"""

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


class StayOfferTest(WorldPackageFixture):
    """一份真实世界包 + W 自己的营业投影，看停留机会从哪来、什么时候一个都不来。"""

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
        # 公园把自身声明的正经事项（坐下、看看周围、散步）按偏好排在前面，闲散项（看路人）排在末尾。
        self.assertEqual([item["target_refs"] for item in offers],
                         [["stay:park:SIT:300"], ["stay:park:LOOK_AROUND:180"],
                          ["stay:park:WALK:240"], ["stay:park:PEOPLE_WATCH:180"]], offers)
        offer, idle_offer = offers[0], offers[-1]
        self.assertEqual(offer["target_refs"], ["stay:park:SIT:300"], offer)
        self.assertEqual(offer["location_ref"], "place:park", offer)
        self.assertEqual(offer["subject_id"], "chiyo.global", offer)
        self.assertTrue(offer["observed"])
        self.assertTrue(offer["valid_from"] < offer["valid_until"])
        # 计划就是"公园 + SIT + 300 秒"，可被任何一端原样解析回来
        self.assertEqual(wmos.parse_stay_target(offer["target_refs"][0]),
                         {"place": "park", "descriptor": "SIT", "seconds": 300,
                          "target_ref": "stay:park:SIT:300"})
        # 闲散项是独立一条，同样来自公园自己的声明：看人来人往，180 秒，不冒充正事。
        self.assertIn("PEOPLE_WATCH", wmos.IDLE_STAY_AFFORDANCES)
        self.assertEqual(idle_offer["target_refs"], ["stay:park:PEOPLE_WATCH:180"], idle_offer)
        self.assertEqual(idle_offer["location_ref"], "place:park", idle_offer)
        self.assertEqual(idle_offer["subject_id"], "chiyo.global", idle_offer)
        self.assertTrue(idle_offer["observed"])
        self.assertTrue(idle_offer["valid_from"] < idle_offer["valid_until"])
        self.assertNotEqual(idle_offer["opportunity_id"], offer["opportunity_id"], idle_offer)
        self.assertEqual(wmos.parse_stay_target(idle_offer["target_refs"][0]),
                         {"place": "park", "descriptor": "PEOPLE_WATCH", "seconds": 180,
                          "target_ref": "stay:park:PEOPLE_WATCH:180"})

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
        # 同一时刻公园开着：同一次观察里既有"能走到"也有全部合法停留（坐一会儿、看看周围、散步 + 看路人）
        stay = self.offers("park", dawn)
        self.assertEqual([item["target_refs"] for item in stay],
                         [["stay:park:SIT:300"], ["stay:park:LOOK_AROUND:180"],
                          ["stay:park:WALK:240"], ["stay:park:PEOPLE_WATCH:180"]], stay)

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


def catalog_entry(place: str, affordances: list[str]) -> dict:
    """一份最小但形状真实的目录：一个 OPEN 的地点 + 它自己声明的 affordance。"""

    return {"status": "available", "opportunities": [{
        "location_id": place,
        "availability": {"status": "OPEN"},
        "affordances": [{"affordance_id": name, "description": ""} for name in affordances],
    }]}


def stay_descriptors(catalog: dict, place: str, moment: datetime) -> list[str]:
    """一次观察里，那个地点给出的停留描述符（按候选顺序）。"""

    moment_iso = moment.astimezone(timezone.utc).isoformat()
    offers = wmos.observed_stay_opportunities(
        {"location": {"place_id": place}, "world_revision": 1, "world_id": "t"},
        catalog=catalog, occurred_at=moment_iso, observed_at=moment_iso)
    return [wmos.parse_stay_target(item["target_refs"][0])["descriptor"] for item in offers]


class IdleStayRotationTest(unittest.TestCase):
    """「什么都不干」的选项必须真的轮流进候选，而不是被优先级表永远压住。

    这里用合成目录（形状与 W 目录一致）是因为要精确控制"这个地方声明了哪些 affordance"；
    真实世界包的那一半见 ``test_world_daily_life_play`` 上半段与下面的读书点用例。
    """

    #: 家里：三件有内容可做 + 两件什么都不用做
    HOME = ["REST", "READ", "USE_PHONE", "DAZE", "POTTER"]

    def test_the_idle_options_rotate_in_over_three_consecutive_rounds(self):
        catalog = catalog_entry("home", self.HOME)
        base = saturday(13)
        rounds = [stay_descriptors(catalog, "home", base + timedelta(hours=step))
                  for step in range(3)]

        # 每轮家里的正经日常（REST/READ/USE_PHONE）全部保留且 REST 排第一，发呆那条在末尾按钟点轮换
        for step, descriptors in enumerate(rounds):
            self.assertEqual(len(descriptors), 4, rounds)
            self.assertEqual(descriptors[:3], ["REST", "READ", "USE_PHONE"],
                             "第 %d 轮的家常候选不全或顺序不对：%r" % (step + 1, rounds))
        idle_seen = {descriptors[-1] for descriptors in rounds}
        self.assertEqual(idle_seen, {"DAZE", "POTTER"},
                         "连续三轮里两个新选项必须都出现过，实际只有 %r" % (idle_seen,))
        # 同一轮里重复观察必须给出同一份候选（轮换按钟点，不按调用次数）
        self.assertEqual(rounds[0], stay_descriptors(catalog, "home", base), rounds)

    def test_the_idle_stays_are_short(self):
        catalog = catalog_entry("home", self.HOME)
        seen: dict[str, int] = {}
        base = saturday(13)
        for step in range(4):
            moment_iso = (base + timedelta(hours=step)).astimezone(timezone.utc).isoformat()
            for item in wmos.observed_stay_opportunities(
                    {"location": {"place_id": "home"}, "world_revision": 1, "world_id": "t"},
                    catalog=catalog, occurred_at=moment_iso, observed_at=moment_iso):
                plan = wmos.parse_stay_target(item["target_refs"][0])
                seen[plan["descriptor"]] = plan["seconds"]
        # 发呆不再是最短的那一档：让它同时「时间最短」又「被标成不产出东西」，等于在选项里
        # 替她把这件事判成没用。三个 idle 选项现在同为一档。
        self.assertEqual(seen.get("DAZE"), 180, seen)
        self.assertEqual(seen.get("POTTER"), 180, seen)

    def test_a_place_that_only_declares_idle_options_still_offers_one(self):
        # 只有发呆可做的地方（比如街角）：不该退化成 QUIET_STOP，也不该什么都没有
        catalog = catalog_entry("street_corner", ["PEOPLE_WATCH"])
        descriptors = stay_descriptors(catalog, "street_corner", saturday(13))
        self.assertEqual(descriptors, ["PEOPLE_WATCH"], descriptors)

    def test_a_place_with_no_known_affordance_no_longer_invents_a_quiet_stop(self):
        # 2026-11-07 新裁定：没有可执行停留定义时不再自动补「待一会儿」；
        # 这条未知 affordance 只作环境描述，零候选。
        catalog = catalog_entry("pier", ["SOMETHING_THIS_TABLE_NEVER_HEARD_OF"])
        descriptors = stay_descriptors(catalog, "pier", saturday(13))
        self.assertEqual(descriptors, [], descriptors)

    def test_the_idle_options_are_last_in_the_preference_order(self):
        # 不许图省事把它们插到最前面：那样她每次在家都发呆
        self.assertNotIn(wmos.STAY_DESCRIPTOR_PREFERENCE[0], wmos.IDLE_STAY_AFFORDANCES)
        tail = wmos.STAY_DESCRIPTOR_PREFERENCE[-len(wmos.IDLE_STAY_AFFORDANCES):]
        self.assertEqual(set(tail), set(wmos.IDLE_STAY_AFFORDANCES))


class RealPlaceIsNotCrowdedOutTest(WorldPackageFixture):
    """真的有正事可做的地点（读书位）照旧给它的正事，发呆挤不进来。"""

    def test_the_reading_point_keeps_offering_reading_across_a_whole_afternoon(self):
        # 读书位在 daily_neighborhood_v1 里是 10:00-20:00，全天可开
        base = saturday(10)
        seen = []
        for step in range(8):
            moment = base + timedelta(hours=step)
            offers = self.offers("reading_point", moment)
            self.assertEqual(len(offers), 2, (moment, offers))
            plans = [wmos.parse_stay_target(item["target_refs"][0]) for item in offers]
            for plan in plans:
                self.assertNotIn(plan["descriptor"], wmos.IDLE_STAY_AFFORDANCES, plan)
            self.assertEqual([p["descriptor"] for p in plans],
                             ["READ_HERE", "BROWSE_BOOKS"], plans)
            self.assertEqual(plans[0]["seconds"], 600, plans)
            seen.append(tuple(p["descriptor"] for p in plans))
        self.assertEqual(len(set(seen)), 1, seen)


class WorldTemplateHasSomethingToDoNothingInTest(unittest.TestCase):
    """模板这一半：家里、街上、公园都得有"什么都不干"的选项，而且都得短。"""

    def template(self) -> dict:
        import json

        path = ROOT / "components" / "worldgen" / "templates" / "coastal_town.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_home_street_and_park_declare_an_idle_option(self):
        template = self.template()
        home = next(slot for slot in template["fixed_slots"] if slot["id"] == "home")
        park = next(slot for slot in template["slot_pool"] if slot["slot"] == "park")
        for slot, wanted in ((home, {"DAZE", "POTTER"}),
                             (template["street_slot"], {"PEOPLE_WATCH"}),
                             (park, {"PEOPLE_WATCH"})):
            declared = {item[0] for item in slot["affordances"]}
            self.assertTrue(wanted <= declared, (wanted, declared))
            # 那条中文说法也在（模板的 affordance 就是 (id, 说法) 两元组）
            self.assertTrue(all(item[1].strip() for item in slot["affordances"]), slot)

    def test_every_idle_option_has_a_short_stay_and_a_spoken_title(self):
        import autonomous_life_cycle as alc

        for affordance_id in wmos.IDLE_STAY_AFFORDANCES:
            self.assertIn(affordance_id, wmos.STAY_SECONDS_BY_AFFORDANCE)
            self.assertLessEqual(wmos.STAY_SECONDS_BY_AFFORDANCE[affordance_id], 180)
            self.assertIn(affordance_id, alc.STAY_TITLES)
            self.assertTrue(alc.STAY_TITLES[affordance_id].strip())
        # 兜底标题也得是日常说法，不是系统日志
        self.assertEqual(alc.STAY_TITLE_DEFAULT, "在这儿待一会儿")


if __name__ == "__main__":
    unittest.main()
