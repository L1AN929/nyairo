"""程序化室内生成器与包级应用测试（v0.3 验收 B/C 及阶段六）。"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.editor_bridge import to_editor_world
from components.worldgen.interior import (
    InteriorError,
    apply_interiors_to_package,
    generate_interior,
    infer_building,
    interior_seed,
)
from components.worldgen.providers.glm import _validate_design
from components.worldgen.loader import load as load_package

BUILDINGS = ("home", "bookstore", "cafe", "convenience_store", "classroom")
EDITOR_DIR = Path(__file__).resolve().parents[2] / "world-editor"


def _editor():
    import sys

    if str(EDITOR_DIR) not in sys.path:
        sys.path.insert(0, str(EDITOR_DIR))
    import model as editor_model

    return editor_model


class InteriorGenerationTest(unittest.TestCase):
    """验收 B：五类建筑自动生成；验收 C：种子多样性。"""

    def test_five_building_types_generate_valid_spaces(self) -> None:
        editor = _editor()
        for building in BUILDINGS:
            with self.subTest(building=building):
                result = generate_interior(building=building, seed=929711, location_id="x")
                editor.validate_space(result["space"], {})
                self.assertTrue(result["space"]["enabled"])
                self.assertGreaterEqual(len(result["furniture"]), 4)
                self.assertGreaterEqual(result["passability"]["ratio"], 0.55)
                kinds = {item["kind"] for item in result["furniture"]}
                self.assertTrue(kinds.issubset({
                    "bed", "book", "cabinet", "chair", "cup", "dispenser",
                    "lamp", "plant", "table",
                }))

    def test_classroom_has_desks_facing_front(self) -> None:
        result = generate_interior(building="classroom", seed=7, location_id="school")
        chairs = [item for item in result["furniture"] if item["kind"] == "chair"]
        self.assertGreaterEqual(len(chairs), 4)
        # 座椅面向讲台（rotation 180），讲台在教室前部（小 y）。
        self.assertTrue(all(item["visual"]["rotation"] == 180 for item in chairs))
        desks = [item for item in result["furniture"] if item["kind"] == "table"]
        front = min(item["y"] for item in result["furniture"] if item["kind"] == "table")
        self.assertEqual(front, min(item["y"] for item in desks), "讲台应是最靠前的桌子")

    def test_seed_diversity(self) -> None:
        """验收 C：不同种子产生实际布局差异。"""
        for building in BUILDINGS:
            layouts = {
                json.dumps(
                    [(item["kind"], item["x"], item["y"]) for item in
                     generate_interior(building=building, seed=seed, location_id="x")["furniture"]],
                    sort_keys=True,
                )
                for seed in range(1, 9)
            }
            self.assertGreaterEqual(
                len(layouts), 6, f"{building} 8 个种子只有 {len(layouts)} 种布局"
            )

    def test_determinism(self) -> None:
        for building in BUILDINGS:
            first = generate_interior(building=building, seed=12345, location_id="loc")
            second = generate_interior(building=building, seed=12345, location_id="loc")
            self.assertEqual(
                json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
            )

    def test_bulk_seed_robustness(self) -> None:
        for building in (*BUILDINGS, "library", "market", "hall", "shop_generic"):
            for seed in (0, 1, 999, 65535, 2**31 - 1):
                with self.subTest(building=building, seed=seed):
                    generate_interior(building=building, seed=seed, location_id="x")

    def test_infer_building_prefers_exact_id_and_names(self) -> None:
        self.assertEqual(infer_building({"location_id": "bookstore", "kind": "store", "name": "文浜堂书店"}), "bookstore")
        self.assertEqual(infer_building({"location_id": "home", "kind": "home", "name": "住居"}), "home")
        self.assertEqual(infer_building({"location_id": "fish_market", "kind": "market", "name": "朝市鱼店"}), "market")
        self.assertIsNone(infer_building({"location_id": "beach", "kind": "beach", "name": "沙滩"}))
        self.assertEqual(infer_building({"location_id": "shop_x", "kind": "store", "name": "浜道具店"}), "shop_generic")


class ApplyInteriorPackageTest(WorldGenTestCase):
    """包级应用：scene.json + objects.json 写入、幂等、往返与一致性。"""

    def make_package(self, seed: int = 929711) -> Path:
        return self.temp_package(make_valid_docs(seed=seed), prefix="wg-int-")

    def test_apply_writes_scene_and_objects(self) -> None:
        package_dir = self.make_package()
        receipt = apply_interiors_to_package(package_dir)
        self.assertTrue(receipt["interiors"])
        new_docs = schema.load_package(package_dir, verify=True)
        scene = new_docs[schema.SCENE_FILE]
        self.assertTrue(scene["spaces"])
        for location_id, space in scene["spaces"].items():
            self.assertIn(location_id, {loc["location_id"] for loc in new_docs["topology.json"]["locations"]})
            self.assertTrue(space["enabled"])
        # 家具都进了 objects.json 且有摆位。
        placements = scene["placements"]
        object_ids = {obj["object_id"] for obj in new_docs["objects.json"]["objects"]}
        self.assertTrue(set(placements).issubset(object_ids))

    def test_idempotent_regeneration(self) -> None:
        """阶段六：同种子重复生成结果一致（世界包逐字节一致）。"""
        package_dir = self.make_package()
        apply_interiors_to_package(package_dir)
        first = {
            name: (package_dir / name).read_text(encoding="utf-8")
            for name in (*schema.PACKAGE_FILES, schema.SCENE_FILE)
        }
        apply_interiors_to_package(package_dir)
        for name, content in first.items():
            self.assertEqual((package_dir / name).read_text(encoding="utf-8"), content)

    def test_interiors_survive_bridge_roundtrip(self) -> None:
        """阶段六：室内经正向桥接进入编辑器场景，回写后不丢失。"""
        package_dir = self.make_package()
        apply_interiors_to_package(package_dir)
        before = schema.load_package(package_dir, verify=True)
        view = to_editor_world(load_package(package_dir))
        # 编辑器场景中出现了室内空间与家具。
        restored = {room["id"]: room["space"] for room in view["rooms"]}
        for location_id, space in before[schema.SCENE_FILE]["spaces"].items():
            self.assertIn(location_id, restored)
            self.assertEqual(restored[location_id]["walls"], space["walls"])
        # 无修改回写：室内与对象摆位保留。
        out = self.temp_dir(prefix="wg-int-rt-") / "edited"
        from components.worldgen.editor_bridge import import_from_editor

        import_from_editor(package_dir, view, out_dir=out)
        after = schema.load_package(out, verify=True)
        self.assertEqual(
            after[schema.SCENE_FILE]["spaces"], before[schema.SCENE_FILE]["spaces"]
        )
        self.assertEqual(
            after[schema.SCENE_FILE]["placements"], before[schema.SCENE_FILE]["placements"]
        )
        # 世界事实不丢失。
        self.assertEqual(after["npcs.json"], before["npcs.json"])
        self.assertEqual(after["ambient.json"], before["ambient.json"])

    def test_single_location_regeneration(self) -> None:
        package_dir = self.make_package()
        apply_interiors_to_package(package_dir)
        before = schema.load_package(package_dir, verify=True)
        apply_interiors_to_package(package_dir, locations=["cafe"], seed_override=777)
        after = schema.load_package(package_dir, verify=True)
        # 只有 cafe 的场景被改写；home 的空间与摆位保持不变。
        self.assertEqual(
            after[schema.SCENE_FILE]["spaces"]["home"],
            before[schema.SCENE_FILE]["spaces"]["home"],
        )
        self.assertEqual(
            after[schema.SCENE_FILE]["placements"].get("home_bed01"),
            before[schema.SCENE_FILE]["placements"].get("home_bed01"),
        )
        cafe_changed = (
            after[schema.SCENE_FILE]["spaces"]["cafe"] != before[schema.SCENE_FILE]["spaces"]["cafe"]
            or after[schema.SCENE_FILE]["placements"] != before[schema.SCENE_FILE]["placements"]
        )
        self.assertTrue(cafe_changed, "覆盖种子后 cafe 的场景或摆位应有变化")

    def test_glm_failure_falls_back_to_procedural(self) -> None:
        package_dir = self.make_package()
        receipt = apply_interiors_to_package(
            package_dir, locations=["bookstore"], use_glm=True, api_key=""
        )
        self.assertTrue(receipt["warnings"], "GLM 不可用应有警告")
        self.assertEqual(receipt["interiors"][0]["design_source"], "procedural")
        schema.load_package(package_dir, verify=True)

    def test_unknown_location_refused(self) -> None:
        package_dir = self.make_package()
        with self.assertRaises(InteriorError):
            apply_interiors_to_package(package_dir, locations=["ghost_place"])


class DesignValidationTest(unittest.TestCase):
    """GLM 语义建议的受限词汇校验：模型不能发明家具种类。"""

    def test_unknown_furniture_kinds_dropped(self) -> None:
        design = _validate_design({
            "style": "老派旧书店",
            "furniture": [
                {"kind": "cabinet", "count": 3, "purpose": "书架"},
                {"kind": "portal_gun", "count": 2, "purpose": "幻觉家具"},
                {"kind": "dragon", "count": 1},
            ],
            "details": "靠窗的位置适合阅读。",
            "activities": ["店主整理旧书"],
        })
        kinds = [item["kind"] for item in design["furniture"]]
        self.assertEqual(kinds, ["cabinet"])
        self.assertEqual(design["activities"], ["店主整理旧书"])

    def test_counts_clamped_and_activities_bounded(self) -> None:
        design = _validate_design({
            "furniture": [{"kind": "chair", "count": 99}],
            "activities": [f"活动{i}" for i in range(10)],
        })
        chairs = next(item for item in design["furniture"] if item["kind"] == "chair")
        self.assertEqual(chairs["count"], 6, "GLM 建议数量应收敛到 1..6")
        self.assertLessEqual(len(design["activities"]), 4)

    def test_empty_design_refused(self) -> None:
        with self.assertRaises(Exception):
            _validate_design({"furniture": [{"kind": "dragon", "count": 1}]})


if __name__ == "__main__":
    unittest.main()
