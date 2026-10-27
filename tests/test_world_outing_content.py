#!/usr/bin/env python3
"""街区世界包的两条真实内容声明：实体家居店与公园花圃。

只校验世界包里已经落地的内容，不启动服务、不写实例、不调用模型：

* ``daily_neighborhood_v1`` 仍能通过自己的校验与 MANIFEST；
* 家居店是一个真实可达的地点（``kind=store``、营业时间、货架声明），
  从家走过去的路由和秒数来自世界包自己的拓扑；
* 公园花圃是场景里声明的静态对象，出现在 ``park_path`` 的摆位表里，
  不是运行时凭空生成的文案。

店面货架与经济目录的接线（``home_goods_store`` 渠道、价格、产权、交付）
由 ``world_property_economy`` 自己的既有测试覆盖，这里不重复。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen.validator import validate_package_dir  # noqa: E402

PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"


class OutingContentTest(unittest.TestCase):
    def test_package_still_validates_with_manifest(self) -> None:
        self.assertEqual(validate_package_dir(PACKAGE), [])

    def test_store_is_a_real_reachable_place_with_its_own_hours(self) -> None:
        import world_runtime_data as wrd

        world = wrd.active_world({"CHIYO_WORLD_PACKAGE": str(PACKAGE)})
        store = world.location("home_goods_store")
        self.assertIsNotNone(store)
        self.assertEqual(store["kind"], "store")
        self.assertEqual(
            world.route_to("home", "home_goods_store"),
            ["street_front", "home_goods_store"],
        )
        self.assertEqual(world.route_duration_seconds("street_front", "home_goods_store"), 150)
        rule = world.location_rule("home_goods_store")
        self.assertEqual((rule["open_hour"], rule["close_hour"]), (10, 20))
        self.assertIn("BROWSE_STORE", [item[0] for item in rule["affordances"]])

    def test_flowerbed_is_declared_in_the_parks_own_scene(self) -> None:
        from components.worldgen import loader

        package = loader.load(PACKAGE)
        objects = {item["object_id"]: item for item in package.objects}
        flowerbed = objects.get("flowerbed_park")
        self.assertIsNotNone(flowerbed)
        self.assertEqual(flowerbed["kind"], "plant")
        self.assertIn("不能摘", flowerbed["note"])
        park = package.scene["navigation"]["park"]
        self.assertIn("flowerbed_park", park["furniture_map"]["park_path"])

    def test_store_shelf_objects_are_declared_as_furniture(self) -> None:
        from components.worldgen import loader

        package = loader.load(PACKAGE)
        objects = {item["object_id"]: item for item in package.objects}
        self.assertEqual(objects["home_goods_store_shelf"]["kind"], "cabinet")
        self.assertEqual(objects["home_goods_store_counter"]["kind"], "table")
        nav = package.scene["navigation"]["home_goods_store"]
        self.assertEqual(nav["entrance"], "main")
        self.assertIn("home_goods_store_shelf", nav["furniture_map"]["main"])


if __name__ == "__main__":
    unittest.main()
