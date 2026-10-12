"""worldgen 城镇包 → 世界编辑器 v3 场景桥接测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.editor_bridge import (
    EditorBridgeError,
    export_for_editor,
    to_editor_world,
)
from components.worldgen.loader import load as load_package

EDITOR_DIR = Path(__file__).resolve().parents[2] / "world-editor"


def _editor_model():
    import sys

    if str(EDITOR_DIR) not in sys.path:
        sys.path.insert(0, str(EDITOR_DIR))
    import model as editor_model

    return editor_model


class EditorBridgeTest(WorldGenTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.model = _editor_model()
        self.package_dir = self.temp_package(make_valid_docs(seed=929711, theme="coastal_town"))
        self.package = load_package(self.package_dir)

    def test_converted_world_passes_editor_validation(self) -> None:
        editor_world = to_editor_world(self.package)
        validated, warnings = self.model.validate_package(json.loads(json.dumps(editor_world)))
        self.assertEqual(validated["format"], "nyairo.world-package.v3")
        self.assertEqual(validated["requires"], "nyairo.editor-runtime.v3")
        self.assertEqual(warnings, [])

    def test_regions_and_connections_match_topology(self) -> None:
        editor_world = to_editor_world(self.package)
        self.assertEqual(len(editor_world["rooms"]), len(self.package.locations))
        self.assertEqual(
            len(editor_world["connections"]),
            len(self.package._topology["edges"]),
        )
        region_ids = {room["id"] for room in editor_world["rooms"]}
        self.assertIn("home", region_ids)
        for edge in editor_world["connections"]:
            self.assertIn(edge["from"], region_ids)
            self.assertIn(edge["to"], region_ids)

    def test_regions_inside_canvas_and_hours_mapped(self) -> None:
        editor_world = to_editor_world(self.package)
        for room in editor_world["rooms"]:
            self.assertGreaterEqual(room["x"], 0)
            self.assertGreaterEqual(room["y"], 0)
            self.assertLessEqual(room["x"] + room["w"], 1000)
            self.assertLessEqual(room["y"] + room["h"], 620)
            location_id = room["id"]
            rule = self.package.location_rule(location_id)
            if rule["open_hour"] is None:
                self.assertIsNone(room["hours"])
            else:
                self.assertEqual(room["hours"]["open"], rule["open_hour"])
                self.assertEqual(room["hours"]["close"], rule["close_hour"])

    def test_home_objects_mapped(self) -> None:
        editor_world = to_editor_world(self.package)
        by_id = {obj["id"]: obj for obj in editor_world["objects"]}
        self.assertTrue(by_id)
        kinds = {obj["kind"] for obj in editor_world["objects"]}
        self.assertTrue(kinds.issubset({"bed", "table", "cabinet", "lamp"}))
        for obj in editor_world["objects"]:
            self.assertEqual(obj["room_id"], "home")
            self.assertTrue(5 <= obj["x"] <= 95)
            self.assertTrue(15 <= obj["y"] <= 90)
        if "bed_home" in by_id:
            self.assertEqual(by_id["bed_home"]["actions"], ["rest"])

    def test_interiors_left_empty_for_manual_drawing(self) -> None:
        """桥接不发明室内空间：墙/门/地面留空，由使用者在编辑器绘制。"""
        editor_world = to_editor_world(self.package)
        for room in editor_world["rooms"]:
            space = room["space"]
            self.assertFalse(space["enabled"])
            self.assertEqual(space["walls"], [])
            self.assertEqual(space["doors"], [])
            self.assertEqual(space["floors"], [])

    def test_export_writes_file_and_receipt(self) -> None:
        receipt = export_for_editor(self.package_dir)
        out = Path(receipt["output"])
        self.assertTrue(out.exists())
        loaded = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(loaded["meta"]["id"], "coastal_town_929711")
        validated, warnings = self.model.validate_package(loaded)
        self.assertEqual(warnings, [])

    def test_close_hour_24_refused_with_clear_message(self) -> None:
        docs = make_valid_docs(seed=777, theme="coastal_town")
        # 人为构造 close_hour=24（schema 允许、生成器不产生）。
        for rule in docs["rules.json"]["location_rules"].values():
            if rule["close_hour"] is not None:
                rule["close_hour"] = 24
                break
        else:
            self.skipTest("该种子没有限时地点")
        package = load_package(self.temp_package(docs))
        with self.assertRaises(EditorBridgeError) as ctx:
            to_editor_world(package)
        self.assertIn("结束小时", str(ctx.exception))

    def test_editor_examples_still_validate(self) -> None:
        """随编辑器附带的示例在仓库副本中保持可校验。"""
        example = EDITOR_DIR / "examples" / "created-cafe.nyairo-world.json"
        world = json.loads(example.read_text(encoding="utf-8"))
        _, warnings = self.model.validate_package(world)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
