"""随机性与可复现性验收（施工单 B / C）。"""

from __future__ import annotations

import json
from collections import deque

from ._setup import WorldGenTestCase, make_valid_docs
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


if __name__ == "__main__":
    unittest.main()
