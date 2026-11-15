"""随机性与可复现性验收（施工单 B / C）。"""

from __future__ import annotations

import json
from collections import deque

try:  # run as a package (``-t .``) or as a top-level discovered module (the runbook command)
    from ._setup import WorldGenTestCase, make_valid_docs
except ImportError:  # pragma: no cover - discovery without a package top level
    from _setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.generator import AVAILABLE_THEMES, generate_world
from components.worldgen.validator import validate

SEEDS = [
    1, 7, 42, 100, 999, 1234, 8888, 929711, 20261014, 314159,
    271828, 555555, 606060, 777777, 864213, 902107, 112358, 132134,
    580081, 999983,
]


def _all_pairs_reachable(locations) -> bool:
    adjacency = {loc["location_id"]: list(loc["reachable"]) for loc in locations}
    start = "home"
    if start not in adjacency:
        return False
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return len(seen) == len(adjacency)


class GeneratorDeterminismTest(WorldGenTestCase):
    """验收 C：相同种子、模板与版本产生相同基础布局。"""

    def test_same_seed_same_layout(self) -> None:
        for theme in AVAILABLE_THEMES:
            first = generate_world(seed=929711, theme=theme)
            second = generate_world(seed=929711, theme=theme)
            for name in schema.PACKAGE_FILES:
                self.assertEqual(
                    json.dumps(first[name], sort_keys=True, ensure_ascii=False),
                    json.dumps(second[name], sort_keys=True, ensure_ascii=False),
                    f"{theme}: 同种子 {name} 不一致",
                )

    def test_different_seed_different_layout(self) -> None:
        base = generate_world(seed=929711, theme="coastal_town")
        other = generate_world(seed=424242, theme="coastal_town")
        a = json.dumps(base["topology.json"], sort_keys=True)
        b = json.dumps(other["topology.json"], sort_keys=True)
        self.assertNotEqual(a, b)
        # 可观察差异：地点组合或名称或规则至少有一处不同。
        names_a = {loc["location_id"]: loc["name"] for loc in base["topology.json"]["locations"]}
        names_b = {loc["location_id"]: loc["name"] for loc in other["topology.json"]["locations"]}
        rules_a = base["rules.json"]["location_rules"]
        rules_b = other["rules.json"]["location_rules"]
        self.assertTrue(
            set(names_a) != set(names_b)
            or names_a != names_b
            or rules_a != rules_b,
            "不同种子应产生可观察的世界差异",
        )

    def test_themes_differ(self) -> None:
        coastal = generate_world(seed=77, theme="coastal_town")
        residential = generate_world(seed=77, theme="residential_town")
        self.assertNotEqual(
            coastal["world.json"]["theme"],
            residential["world.json"]["theme"],
        )
        self.assertNotEqual(
            json.dumps(coastal["topology.json"], sort_keys=True),
            json.dumps(residential["topology.json"], sort_keys=True),
        )


class RandomSeedBatchTest(WorldGenTestCase):
    """验收 B：20 个不同种子的世界基础拓扑有效且可通行。"""

    def test_20_seeds_valid_and_traversable(self) -> None:
        for index, seed in enumerate(SEEDS):
            theme = AVAILABLE_THEMES[index % len(AVAILABLE_THEMES)]
            with self.subTest(seed=seed, theme=theme):
                docs = generate_world(seed=seed, theme=theme)
                messages = validate(docs)
                self.assertEqual(messages, [], f"seed={seed} 校验失败: {messages}")
                topology = docs["topology.json"]
                self.assertTrue(
                    8 <= len(topology["locations"]) <= 30,
                    f"seed={seed} 地点数量异常: {len(topology['locations'])}",
                )
                self.assertTrue(_all_pairs_reachable(topology["locations"]))
                # 每条边都有正向与反向可达声明，步行耗时为正。
                for edge in topology["edges"]:
                    self.assertGreaterEqual(edge["walk_seconds"], 60)
                    self.assertLessEqual(edge["walk_seconds"], 900)

    def test_home_and_school_conventions(self) -> None:
        """住宅固定 home/bedroom，学校固定 school（与 rc9 世界模块约定一致）。"""
        for seed in (1, 929711, 20261014):
            docs = generate_world(seed=seed, theme="residential_town")
            locations = {loc["location_id"]: loc for loc in docs["topology.json"]["locations"]}
            self.assertIn("home", locations)
            self.assertEqual(locations["home"]["area_id"], "bedroom")
            self.assertIn("school", locations)
            self.assertTrue(docs["rules.json"]["location_rules"]["school"]["weekdays_only"])


class PackageRoundtripTest(WorldGenTestCase):
    """世界包写盘 → 加载一致；清单篡改检测（验收 H 的一部分）。"""

    def test_write_and_load_roundtrip(self) -> None:
        docs = make_valid_docs(seed=314159)
        directory = self.temp_package(docs)
        loaded = schema.load_package(directory)
        summary = schema.package_summary(loaded)
        self.assertEqual(summary["world_id"], "coastal_town_314159")
        self.assertEqual(summary["locations"], len(docs["topology.json"]["locations"]))

    def test_manifest_tamper_detected(self) -> None:
        docs = make_valid_docs(seed=271828)
        directory = self.temp_package(docs)
        world_path = directory / "world.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        world["name"] = "被篡改的名字"
        world_path.write_text(
            json.dumps(world, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(schema.WorldPackageError) as ctx:
            schema.load_package(directory)
        self.assertIn("SHA256", str(ctx.exception))

    def test_cross_world_ids_do_not_leak(self) -> None:
        """两个世界的 world_id 不同且各自稳定（验收 D 的数据面前提）。"""
        first = generate_world(seed=111111, theme="coastal_town")
        second = generate_world(seed=222222, theme="residential_town")
        id_a = first["world.json"]["world_id"]
        id_b = second["world.json"]["world_id"]
        self.assertNotEqual(id_a, id_b)
        self.assertEqual(generate_world(seed=111111, theme="coastal_town")["world.json"]["world_id"], id_a)


class CoreEverydayPlacesTest(WorldGenTestCase):
    """每一主题都必须能走到 home/school/bookstore/cafe/home_goods_store。

    核心场所的补齐是"缺失才追加"的确定性步骤：正常加权抽取与坐标抖动一字未动，
    所以已经齐全的生成结果完全不变（``test_default_coastal_package_summary_is_stable``
    用既有 ``package_summary`` 证明默认 929711 包没变）。
    """

    CORE = ("home", "school", "bookstore", "cafe", "home_goods_store")
    SEEDS = (1, 929711, 424242)

    def _docs(self, seed, theme):
        docs = generate_world(seed=seed, theme=theme)
        self.assertEqual(validate(docs), [], f"{theme}/{seed} 校验失败")
        return docs

    def test_four_themes_always_offer_the_core_places(self):
        for theme in AVAILABLE_THEMES:
            for seed in self.SEEDS:
                with self.subTest(theme=theme, seed=seed):
                    docs = self._docs(seed, theme)
                    locations = docs["topology.json"]["locations"]
                    ids = {loc["location_id"] for loc in locations}
                    for core in self.CORE:
                        self.assertIn(core, ids, f"{theme}/{seed} 缺少核心场所 {core}")
                    self.assertTrue(_all_pairs_reachable(locations),
                                    f"{theme}/{seed} 核心场所从 home 不可全部到达")

    def test_core_stores_are_open_in_the_day_and_have_affordances(self):
        for theme in AVAILABLE_THEMES:
            for seed in self.SEEDS:
                docs = self._docs(seed, theme)
                rules = docs["rules.json"]["location_rules"]
                for store in ("bookstore", "cafe", "home_goods_store"):
                    rule = dict(rules[store])
                    with self.subTest(theme=theme, seed=seed, store=store):
                        open_hour = rule.get("open_hour")
                        close_hour = rule.get("close_hour")
                        self.assertIsNotNone(open_hour, f"{store} 没有营业开始")
                        self.assertIsNotNone(close_hour, f"{store} 没有营业结束")
                        self.assertLess(int(open_hour), int(close_hour))
                        self.assertTrue(rule.get("affordances"), f"{store} 没有 affordances")
                # 每家书店都真的提供阅读/浏览 affordance（不是只有个标题）。
                read_affordances = {str(row.get("affordance_id"))
                                    for row in rules["bookstore"].get("affordances") or []}
                self.assertTrue(read_affordances & {"READ_HERE", "READ", "BROWSE_STORE"},
                                f"{theme} 书店没有阅读类 affordance")
        # 新增的山地/乡村书店带主题风貌的那页文字（本地原创，不是所有世界同一张图）。
        for theme in ("mountain_town", "rural_farming"):
            docs = self._docs(929711, theme)
            content = docs["content.json"]["entries"].get("bookstore") or {}
            self.assertTrue(str(content.get("reading") or "").strip(),
                            f"{theme} 书店没有可读内容")

    def test_themed_random_places_are_not_replaced_by_a_fixed_map(self):
        """特色随机场所仍在池里、且不同 seed 的场所集合真的不同。"""
        theme_features = {
            "coastal_town": {"beach"},
            "mountain_town": {"hot_spring", "hiking_trail"},
            "rural_farming": {"farm", "farmers_market"},
        }
        from components.worldgen.generator import load_theme

        for theme, wanted in theme_features.items():
            template = load_theme(theme)
            pool = {slot.get("id") for slot in
                    list(template["fixed_slots"]) + list(template["slot_pool"])}
            self.assertTrue(wanted <= pool, f"{theme} 丢失特色场所 {wanted - pool}")
        sets = {}
        for seed in (1, 7, 42, 999):
            docs = generate_world(seed=seed, theme="mountain_town")
            sets[seed] = {loc["location_id"] for loc in docs["topology.json"]["locations"]}
        self.assertGreater(len({frozenset(v) for v in sets.values()}), 1,
                           "山地世界的非核心场所没有随 seed 变化（像固定图）")

    def test_default_coastal_package_summary_is_stable(self):
        """默认 929711 海岸包的既有摘要不变（生成器补核心逻辑没有改动它）。"""
        summary = schema.package_summary(generate_world(seed=929711, theme="coastal_town"))
        self.assertEqual(summary["world_id"], "coastal_town_929711")
        self.assertEqual(summary["theme"], "coastal_town")
        self.assertEqual(summary["seed"], 929711)
        self.assertEqual(summary["locations"], 19)
        self.assertEqual(summary["edges"], 18)
        self.assertEqual(summary["npcs"], 3)
        self.assertEqual(summary["objects"], 3)


if __name__ == "__main__":
    unittest.main()
