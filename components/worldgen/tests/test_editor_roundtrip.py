"""双向桥接回写测试（v0.2 验收 A/B/C/D + 场景持久化）。

模拟编辑器修改的方式：对正向转换出的 v3 场景做与编辑器界面等价的字段
修改，然后交给 ``import_from_editor`` 回写，断言世界包层面的结果。
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.editor_bridge import (
    EditorBridgeError,
    import_from_editor,
    to_editor_world,
)
from components.worldgen.loader import load as load_package


class RoundtripTestBase(WorldGenTestCase):
    def make_world(self, seed: int = 929711, theme: str = "coastal_town"):
        """创建临时世界包，返回 (package_dir, docs)。"""
        directory = self.temp_package(make_valid_docs(seed=seed, theme=theme))
        return directory, schema.load_package(directory)

    def edited_view(self, package_dir: Path) -> dict:
        """模拟：导入编辑器 → 不做修改。"""
        package = load_package(package_dir)
        return to_editor_world(package)


class RoundtripIdentityTest(RoundtripTestBase):
    """验收 A：不做修改直接回写，世界事实零丢失、零漂移。"""

    def test_roundtrip_is_byte_identical(self) -> None:
        package_dir, docs = self.make_world()
        fresh = self.edited_view(package_dir)
        out = self.temp_dir(prefix="wg-rt-") / "edited"
        receipt = import_from_editor(package_dir, fresh, out_dir=out)

        self.assertEqual(receipt["added_locations"], [])
        self.assertEqual(receipt["removed_locations"], [])
        self.assertEqual(receipt["added_connections"], [])
        self.assertEqual(receipt["removed_connections"], [])
        self.assertEqual(receipt["added_objects"], [])
        self.assertEqual(receipt["removed_objects"], [])
        self.assertEqual(receipt["updated_locations"], {})
        self.assertEqual(receipt["updated_objects"], {})
        for name in schema.PACKAGE_FILES:
            self.assertEqual(
                (package_dir / name).read_text(encoding="utf-8"),
                (out / name).read_text(encoding="utf-8"),
                f"{name} 在无修改往返后不一致",
            )
        self.assertFalse((out / schema.SCENE_FILE).exists(), "无修改不应产生场景文件")
        # 原始包完好（清单仍通过校验）。
        schema.load_package(package_dir, verify=True)

    def test_repeated_roundtrips_stay_identical(self) -> None:
        package_dir, _ = self.make_world(seed=424242)
        current = package_dir
        for index in range(3):
            fresh = self.edited_view(current)
            out = self.temp_dir(prefix=f"wg-rt{index}-") / "edited"
            import_from_editor(current, fresh, out_dir=out)
            for name in schema.PACKAGE_FILES:
                self.assertEqual(
                    (current / name).read_text(encoding="utf-8"),
                    (out / name).read_text(encoding="utf-8"),
                )
            current = out


def _room(view: dict, room_id: str) -> dict:
    return next(room for room in view["rooms"] if room["id"] == room_id)


class RealModificationTest(RoundtripTestBase):
    """验收 B：改名/改营业时间/改道路/放床/新地点/画墙，全部保留。"""

    def setUp(self) -> None:
        super().setUp()
        self.package_dir, self.docs = self.make_world()
        self.view = self.edited_view(self.package_dir)

    def _write_back(self) -> tuple[Path, dict, dict]:
        out = self.temp_dir(prefix="wg-edit-") / "edited"
        receipt = import_from_editor(self.package_dir, self.view, out_dir=out)
        return out, receipt, schema.load_package(out, verify=True)

    def test_all_supported_modifications(self) -> None:
        view = self.view
        # 1) 修改地点名称与描述。
        _room(view, "cafe")["name"] = "鸥岬咖啡屋"
        _room(view, "cafe")["description"] = "换了新招牌的咖啡馆，木门会吱呀作响。"
        # 2) 修改营业时间（10–20 → 12–18）。
        _room(view, "bookstore")["hours"] = {"open": 12, "close": 18}
        # 3) 调整道路：删除 bookstore↔street_1，新增 bookstore↔park。
        view["connections"] = [
            c for c in view["connections"]
            if {c["from"], c["to"]} != {"bookstore", "street_1"}
        ]
        view["connections"].append({"from": "bookstore", "to": "park"})
        # 4) 放置一张床到书店，并移动已有的桌子。
        view["objects"].append({
            "id": "guest_bed", "name": "客房床", "description": "新买的折叠床。",
            "kind": "bed", "room_id": "bookstore", "x": 20, "y": 30,
            "actions": ["rest"], "portable": False, "capacity": 0, "servings": 0,
            "visual": {"scale": 100, "rotation": 0, "layer": 0, "show_name": True},
            "behavior": {"rest": {"minutes": 45, "energy_gain": 20}},
            "asset_id": None, "interaction": {"near": False, "radius": 2},
            "initial_status": "neutral",
        })
        _room_obj = next(o for o in view["objects"] if o["id"] == "desk_home")
        _room_obj["x"], _room_obj["y"] = 60, 60
        # 5) 新建地点（咖啡小摊，接在 street_1 上）。
        view["rooms"].append({
            "id": "coffee_stand", "name": "咖啡小摊", "description": "街角的移动咖啡摊。",
            "kind": "shop", "x": 500, "y": 500, "w": 170, "h": 110,
            "hours": {"open": 8, "close": 14},
            "visual": {"theme": "cafe", "floor": "#eee7df", "border": "#b7a79a",
                        "background": "#faf6ef", "texture": "tiles", "border_style": "solid"},
            "space": {"enabled": False, "entry": {"x": 5, "y": 10},
                      "walls": [], "doors": [], "windows": [], "floors": []},
        })
        view["connections"].append({"from": "coffee_stand", "to": "street_1"})
        # 6) 在咖啡馆画一面墙（室内场景数据）。
        _room(view, "cafe")["space"] = {
            "enabled": True, "entry": {"x": 5, "y": 10},
            "walls": [{"id": "wall-1", "x1": 50, "y1": 0, "x2": 50, "y2": 100,
                        "color": "#8c8a7d", "width": 7}],
            "doors": [{"id": "door-1", "wall_id": "wall-1", "offset": 45, "length": 15,
                        "color": "#bd976e", "state": "closed",
                        "actions": ["open_door", "close_door"]}],
            "windows": [],
            "floors": [{"id": "floor-1", "x": 0, "y": 0, "w": 50, "h": 100,
                         "color": "#eee7d8", "texture": "planks", "asset_id": None}],
        }

        out, receipt, new_docs = self._write_back()

        # 收据反映全部修改。
        self.assertIn("cafe", receipt["updated_locations"])
        self.assertIn("bookstore", receipt["updated_locations"])
        self.assertIn("coffee_stand", receipt["added_locations"])
        self.assertIn("guest_bed", receipt["added_objects"])
        self.assertIn("bookstore<->park", receipt["added_connections"])
        self.assertIn("bookstore<->street_1", receipt["removed_connections"])

        # 世界包层面的持久化。
        locations = {loc["location_id"]: loc for loc in new_docs["topology.json"]["locations"]}
        self.assertEqual(locations["cafe"]["name"], "鸥岬咖啡屋")
        self.assertEqual(
            new_docs["rules.json"]["location_rules"]["bookstore"]["open_hour"], 12
        )
        self.assertEqual(
            new_docs["rules.json"]["location_rules"]["bookstore"]["close_hour"], 18
        )
        self.assertIn("coffee_stand", locations)
        self.assertEqual(locations["coffee_stand"]["kind"], "store")
        self.assertEqual(
            new_docs["rules.json"]["location_rules"]["coffee_stand"]["open_hour"], 8
        )
        object_ids = {o["object_id"] for o in new_docs["objects.json"]["objects"]}
        self.assertIn("guest_bed", object_ids)
        # NPC 与环境事件原样保留（不因编辑而丢失）。
        self.assertEqual(
            new_docs["npcs.json"], self.docs["npcs.json"], "NPC 事实被修改"
        )
        self.assertEqual(
            new_docs["ambient.json"], self.docs["ambient.json"], "环境事件被修改"
        )
        self.assertEqual(
            new_docs["world.json"]["seed"], self.docs["world.json"]["seed"]
        )
        # 场景数据持久化：咖啡馆的墙与移动过的桌子。
        scene = new_docs[schema.SCENE_FILE]
        self.assertIn("cafe", scene["spaces"])
        self.assertEqual(scene["spaces"]["cafe"]["walls"][0]["id"], "wall-1")
        self.assertIn("desk_home", scene["placements"])
        self.assertEqual(scene["placements"]["desk_home"]["x"], 60)
        self.assertIn("guest_bed", scene["placements"])

        # 可达性仍有效（worldgen 校验已在回写时通过）。
        adjacency = {loc["location_id"]: set(loc["reachable"]) for loc in new_docs["topology.json"]["locations"]}
        self.assertIn("park", adjacency["bookstore"])
        self.assertNotIn("street_1", adjacency["bookstore"])
        self.assertIn("street_1", adjacency["coffee_stand"])

        # 4.3：再次进入编辑器时，编辑成果完整恢复。
        reloaded = load_package(out)
        second_view = to_editor_world(reloaded)
        self.assertEqual(_room(second_view, "cafe")["name"], "鸥岬咖啡屋")
        self.assertEqual(_room(second_view, "cafe")["description"], "换了新招牌的咖啡馆，木门会吱呀作响。")
        self.assertEqual(_room(second_view, "bookstore")["hours"], {"open": 12, "close": 18})
        self.assertEqual(_room(second_view, "cafe")["space"]["walls"][0]["id"], "wall-1")
        placed = next(o for o in second_view["objects"] if o["id"] == "guest_bed")
        self.assertEqual((placed["x"], placed["y"]), (20, 30))
        self.assertEqual(placed["behavior"]["rest"]["minutes"], 45)
        moved = next(o for o in second_view["objects"] if o["id"] == "desk_home")
        self.assertEqual((moved["x"], moved["y"]), (60, 60))
        self.assertTrue(any(r["id"] == "coffee_stand" for r in second_view["rooms"]))

    def test_original_package_untouched(self) -> None:
        before = {
            name: (self.package_dir / name).read_text(encoding="utf-8")
            for name in schema.PACKAGE_FILES
        }
        _room(self.view, "cafe")["name"] = "改名尝试"
        out = self.temp_dir(prefix="wg-orig-") / "edited"
        import_from_editor(self.package_dir, self.view, out_dir=out)
        for name, content in before.items():
            self.assertEqual(
                (self.package_dir / name).read_text(encoding="utf-8"),
                content,
                f"原始包的 {name} 被覆盖",
            )

    def test_cross_night_hours_refused(self) -> None:
        _room(self.view, "cafe")["hours"] = {"open": 22, "close": 6}
        with self.assertRaises(EditorBridgeError) as ctx:
            import_from_editor(
                self.package_dir, self.view,
                out_dir=self.temp_dir(prefix="wg-x-") / "edited",
            )
        self.assertIn("跨夜", str(ctx.exception))

    def test_wrong_world_refused(self) -> None:
        other = self.make_world(seed=111111, theme="residential_town")[0]
        fresh = self.edited_view(other)
        fresh["meta"]["id"] = "coastal_town_929711"
        with self.assertRaises(EditorBridgeError) as ctx:
            import_from_editor(
                other, self.view, out_dir=self.temp_dir(prefix="wg-w-") / "edited"
            )
        self.assertIn("不是世界包", str(ctx.exception))


class DeletionAndReferenceTest(RoundtripTestBase):
    """验收 C：删除与引用。"""

    def setUp(self) -> None:
        super().setUp()
        self.package_dir, _ = self.make_world()
        self.view = self.edited_view(self.package_dir)

    def test_delete_referenced_location_refused_with_details(self) -> None:
        """删除被 NPC 引用的书店 → 明确列出冲突，不自动搬迁。"""
        self.view["rooms"] = [r for r in self.view["rooms"] if r["id"] != "bookstore"]
        self.view["connections"] = [
            c for c in self.view["connections"]
            if "bookstore" not in (c["from"], c["to"])
        ]
        with self.assertRaises(EditorBridgeError) as ctx:
            import_from_editor(
                self.package_dir, self.view,
                out_dir=self.temp_dir(prefix="wg-del-") / "edited",
            )
        message = str(ctx.exception)
        self.assertIn("bookstore", message)
        self.assertIn("coastal_npc_bookstore_01", message)
        self.assertIn("不会自动搬迁", message)

    def test_delete_unreferenced_location_succeeds(self) -> None:
        """删除无引用的地点（车站）→ 拓扑/规则/内容同步清理。"""
        docs = schema.load_package(self.package_dir)
        self.assertFalse(
            any(
                npc["home_base_area"] == "station"
                or any(w["location_id"] == "station" for w in npc["windows"])
                for npc in docs["npcs.json"]["npcs"]
            ),
            "测试前提：该种子车站无 NPC 引用",
        )
        self.view["rooms"] = [r for r in self.view["rooms"] if r["id"] != "station"]
        self.view["connections"] = [
            c for c in self.view["connections"]
            if "station" not in (c["from"], c["to"])
        ]
        out = self.temp_dir(prefix="wg-del2-") / "edited"
        receipt = import_from_editor(self.package_dir, self.view, out_dir=out)
        self.assertIn("station", receipt["removed_locations"])
        new_docs = schema.load_package(out, verify=True)
        location_ids = {
            loc["location_id"] for loc in new_docs["topology.json"]["locations"]
        }
        self.assertNotIn("station", location_ids)
        self.assertNotIn("station", new_docs["rules.json"]["location_rules"])
        self.assertNotIn("station", new_docs["content.json"]["entries"])
        for edge in new_docs["topology.json"]["edges"]:
            self.assertNotIn("station", (edge["from"], edge["to"]))

    def test_disconnecting_road_fails_validation(self) -> None:
        """删除最后一条通往某地点的道路 → 连通性校验明确失败。"""
        docs = schema.load_package(self.package_dir)
        book_edges = [
            edge for edge in docs["topology.json"]["edges"]
            if "bookstore" in (edge["from"], edge["to"])
        ]
        self.assertEqual(len(book_edges), 1, "测试前提：书店只有一条路")
        self.view["connections"] = [
            c for c in self.view["connections"]
            if {c["from"], c["to"]} != {"bookstore", "street_1"}
        ]
        # 用一条不接回主图的新路替代（书店挂在孤立的咖啡摊上）。
        self.view["rooms"].append({
            "id": "isolated_hub", "name": "孤立节点", "description": "不接主路的空地。",
            "kind": "outdoor", "x": 100, "y": 100, "w": 170, "h": 110,
            "hours": None,
            "visual": {"theme": "street", "floor": "#d0d9e8", "border": "#8a9cb7",
                        "background": "#e4eaf4", "texture": "tiles", "border_style": "dashed"},
            "space": {"enabled": False, "entry": {"x": 5, "y": 10},
                      "walls": [], "doors": [], "windows": [], "floors": []},
        })
        self.view["connections"].append({"from": "bookstore", "to": "isolated_hub"})
        with self.assertRaises(EditorBridgeError) as ctx:
            import_from_editor(
                self.package_dir, self.view,
                out_dir=self.temp_dir(prefix="wg-disc-") / "edited",
            )
        self.assertIn("道路图不连通", str(ctx.exception))
        self.assertIn("bookstore", str(ctx.exception))


class WorldIsolationTest(RoundtripTestBase):
    """验收 D：两个种子分别编辑回写，互不混淆。"""

    def test_edited_worlds_stay_isolated(self) -> None:
        dir_a, docs_a = self.make_world(seed=111111, theme="coastal_town")
        dir_b, docs_b = self.make_world(seed=222222, theme="residential_town")
        view_a = self.edited_view(dir_a)
        view_b = self.edited_view(dir_b)
        # 各自做不同的修改。
        _room(view_a, "cafe")["name"] = "A 世界咖啡馆"
        shop_b = next(r for r in view_b["rooms"] if r["id"] == "bookstore")
        shop_b["hours"] = {"open": 11, "close": 17}

        out_a = self.temp_dir(prefix="wg-iso-a-") / "edited"
        out_b = self.temp_dir(prefix="wg-iso-b-") / "edited"
        receipt_a = import_from_editor(dir_a, view_a, out_dir=out_a)
        receipt_b = import_from_editor(dir_b, view_b, out_dir=out_b)
        self.assertNotEqual(receipt_a["world_id"], receipt_b["world_id"])

        final_a = schema.load_package(out_a, verify=True)
        final_b = schema.load_package(out_b, verify=True)
        self.assertEqual(final_a["world.json"]["world_id"], "coastal_town_111111")
        self.assertEqual(final_b["world.json"]["world_id"], "residential_town_222222")
        names_a = {loc["name"] for loc in final_a["topology.json"]["locations"]}
        names_b = {loc["name"] for loc in final_b["topology.json"]["locations"]}
        self.assertIn("A 世界咖啡馆", names_a)
        self.assertNotIn("A 世界咖啡馆", names_b)
        self.assertEqual(
            final_b["rules.json"]["location_rules"]["bookstore"]["open_hour"], 11
        )
        self.assertEqual(
            final_a["rules.json"]["location_rules"]["bookstore"],
            docs_a["rules.json"]["location_rules"]["bookstore"],
            "B 的修改不得影响 A",
        )
        # NPC 不交叉（NPC ID 以主题为前缀）。
        npcs_a = {npc["npc_id"] for npc in final_a["npcs.json"]["npcs"]}
        npcs_b = {npc["npc_id"] for npc in final_b["npcs.json"]["npcs"]}
        self.assertTrue(npcs_a and npcs_b)
        self.assertTrue(all(npc_id.startswith("coastal_") for npc_id in npcs_a))
        self.assertTrue(all(npc_id.startswith("residential_") for npc_id in npcs_b))


if __name__ == "__main__":
    unittest.main()
