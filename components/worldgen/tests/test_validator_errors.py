"""结构验证器错误场景（施工单验收 H）。"""

from __future__ import annotations

import copy

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.validator import validate


def _with_mutated_topology(docs: dict, mutator) -> dict:
    broken = copy.deepcopy(docs)
    mutator(broken["topology.json"])
    return broken


class ValidatorErrorScenarioTest(WorldGenTestCase):
    """每种数据错误都必须得到明确提示，不静默替换世界。"""

    def test_valid_world_passes(self) -> None:
        self.assertEqual(validate(make_valid_docs(seed=1)), [])

    def test_duplicate_location_id_rejected(self) -> None:
        docs = make_valid_docs(seed=2)

        def duplicate(topology):
            extra = copy.deepcopy(topology["locations"][0])
            extra["name"] = "重复地点"
            topology["locations"].append(extra)

        broken = _with_mutated_topology(docs, duplicate)
        messages = validate(broken)
        self.assertTrue(any("重复的地点 ID" in message for message in messages), messages)

    def test_dangling_edge_rejected(self) -> None:
        docs = make_valid_docs(seed=3)

        def dangling(topology):
            topology["edges"].append({"from": "home", "to": "ghost_place", "walk_seconds": 120})

        broken = _with_mutated_topology(docs, dangling)
        messages = validate(broken)
        self.assertTrue(any("ghost_place" in message for message in messages), messages)

    def test_disconnected_road_rejected(self) -> None:
        docs = make_valid_docs(seed=4)

        def disconnect(topology):
            # 断开所有通往某条街道的边：该街道子树从 home 不可达。
            victims = [loc["location_id"] for loc in topology["locations"] if loc["kind"] == "street"][1:]
            victim = victims[0]
            topology["edges"] = [
                edge
                for edge in topology["edges"]
                if edge["from"] != victim and edge["to"] != victim
            ]
            # 同步破坏 reachable，让两边一致，暴露纯粹的连通性问题。
            for location in topology["locations"]:
                location["reachable"] = [
                    item for item in location["reachable"] if item != victim
                ]

        broken = _with_mutated_topology(docs, disconnect)
        messages = validate(broken)
        self.assertTrue(
            any("道路图不连通" in message for message in messages),
            messages,
        )

    def test_reachable_edge_mismatch_rejected(self) -> None:
        docs = make_valid_docs(seed=5)

        def mismatch(topology):
            topology["locations"][0]["reachable"] = []

        broken = _with_mutated_topology(docs, mismatch)
        messages = validate(broken)
        self.assertTrue(
            any("reachable 与道路不一致" in message for message in messages),
            messages,
        )

    def test_generator_version_mismatch_rejected(self) -> None:
        docs = make_valid_docs(seed=6)
        docs["world.json"]["generator_version"] = "worldgen.v0.0.1-fake"
        messages = validate(docs)
        self.assertTrue(any("版本不匹配" in message for message in messages), messages)

    def test_missing_rule_rejected(self) -> None:
        docs = make_valid_docs(seed=7)
        docs["rules.json"]["location_rules"].pop("home")
        messages = validate(docs)
        self.assertTrue(any("缺少营业规则" in message for message in messages), messages)

    def test_home_area_convention_rejected(self) -> None:
        docs = make_valid_docs(seed=8)
        for location in docs["topology.json"]["locations"]:
            if location["location_id"] == "home":
                location["area_id"] = "living_room"
        messages = validate(docs)
        self.assertTrue(any("bedroom" in message for message in messages), messages)

    def test_duplicate_npc_id_rejected(self) -> None:
        docs = make_valid_docs(seed=9)
        npcs = docs["npcs.json"]["npcs"]
        if len(npcs) < 2:
            self.skipTest("该种子没有足够的 NPC")
        npcs[1]["npc_id"] = npcs[0]["npc_id"]
        messages = validate(docs)
        self.assertTrue(any("npc_id 重复" in message for message in messages), messages)

    def test_invalid_timezone_rejected(self) -> None:
        docs = make_valid_docs(seed=10)
        docs["world.json"]["timezone"] = "Mars/Olympus"
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo("UTC")
            database_available = True
        except Exception:
            database_available = False
        messages = validate(docs)
        if database_available:
            self.assertTrue(messages, "有 tz 数据库时无效时区应被拒绝")
        else:
            # 无 tz 数据库的平台降级为格式校验；严格校验交给运行环境。
            self.assertEqual(messages, [])

    def test_bad_location_id_characters_rejected(self) -> None:
        docs = make_valid_docs(seed=11)
        for location in docs["topology.json"]["locations"]:
            if location["location_id"] == "home":
                location["location_id"] = "我的家"
                break
        messages = validate(docs)
        self.assertTrue(any("ID 规则" in message for message in messages), messages)

    def test_npc_window_to_unknown_location_rejected(self) -> None:
        docs = make_valid_docs(seed=12)
        npcs = docs["npcs.json"]["npcs"]
        if not npcs:
            self.skipTest("该种子没有 NPC")
        npcs[0]["windows"][0]["location_id"] = "ghost_place"
        # NPC 出场地点的交叉校验发生在包加载阶段。
        directory = self.temp_package(docs)
        with self.assertRaises(schema.WorldPackageError) as ctx:
            schema.load_package(directory)
        self.assertIn("不存在的地点", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
