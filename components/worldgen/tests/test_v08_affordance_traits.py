"""v0.8 通用物件交互属性（affordance_traits）验收。

覆盖：schema 校验与向后兼容、四种风貌的自动派生、编辑器桥接的正向导出
与反向回写（含自创物件与既有物件修改）、以及新增代码不写死角色代词。
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.editor_bridge import (
    EditorBridgeError,
    _editor,
    _supports_affordance_traits,
    import_from_editor,
    to_editor_world,
)
from components.worldgen.generator import (
    AVAILABLE_THEMES,
    generate_world,
    infer_affordance_traits,
)
from components.worldgen.loader import load as load_package
from components.worldgen.validator import validate

TRAITS = set(schema.CANONICAL_AFFORDANCE_TRAITS)


def objects_doc(objects: list[dict]) -> dict:
    return {"schema_version": schema.OBJECTS_SCHEMA, "objects": objects}


def valid_object(**overrides) -> dict:
    item = {
        "object_id": "lamp_home",
        "name": "台灯",
        "kind": "lamp",
        "location_id": "home",
        "note": "旧台灯。",
    }
    item.update(overrides)
    return item


class SchemaValidationTest(unittest.TestCase):
    """affordance_traits 的结构校验与老包兼容。"""

    def test_valid_traits_pass_and_preserve_order(self) -> None:
        doc = objects_doc(
            [valid_object(affordance_traits=["switchable", "usable"])]
        )
        normalized = schema.validate_objects_doc(doc)
        self.assertEqual(
            normalized["objects"][0]["affordance_traits"], ["switchable", "usable"]
        )

    def test_legacy_object_without_traits_keeps_five_keys(self) -> None:
        normalized = schema.validate_objects_doc(objects_doc([valid_object()]))
        entry = normalized["objects"][0]
        self.assertEqual(
            set(entry),
            {"object_id", "name", "kind", "location_id", "note"},
            "老包没有 affordance_traits 时不应被硬塞默认字段",
        )

    def test_invalid_traits_are_rejected(self) -> None:
        bad_values = [
            "switchable",
            {"switchable": True},
            ["flying"],
            ["switchable", "switchable"],
            [trait for trait in schema.CANONICAL_AFFORDANCE_TRAITS] + ["movable"],
            ["switchable", 3],
        ]
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(schema.WorldPackageError):
                    schema.validate_objects_doc(
                        objects_doc([valid_object(affordance_traits=value)])
                    )

    def test_unknown_object_key_is_rejected(self) -> None:
        with self.assertRaises(schema.WorldPackageError):
            schema.validate_objects_doc(
                objects_doc([valid_object(color="red")])
            )

    def test_traits_must_be_a_list_even_if_tuple_like(self) -> None:
        with self.assertRaises(schema.WorldPackageError):
            schema.normalize_affordance_traits(("usable",), "测试")


class InferTraitsTest(unittest.TestCase):
    """确定性推断函数本身。"""

    def test_known_categories(self) -> None:
        cases = {
            ("lamp", "台灯"): {"switchable", "usable"},
            ("furniture", "书桌"): {"container", "combinable", "inscribable"},
            ("furniture", "书架"): {"container", "combinable"},
            ("book", "旧书"): {"inscribable", "insertable", "movable"},
            ("cup", "茶杯"): {"usable", "thermal_or_damp", "movable"},
            ("plant", "盆栽"): {"movable", "usable"},
        }
        for (kind, name), expected in cases.items():
            with self.subTest(kind=kind, name=name):
                traits = infer_affordance_traits(kind, name=name)
                self.assertTrue(traits, "推断结果不应为空")
                self.assertTrue(set(traits) <= TRAITS)
                self.assertTrue(expected <= set(traits), f"{traits} 缺少 {expected}")

    def test_portable_fallback_adds_movable(self) -> None:
        traits = infer_affordance_traits("gadget", name="杂物", portable=True)
        self.assertEqual(traits[0], "usable")
        self.assertIn("movable", traits)

    def test_thermal_hint_from_note(self) -> None:
        traits = infer_affordance_traits("lamp", name="旧灯", note="摆在暖炉边。")
        self.assertIn("thermal_or_damp", traits)


class FourThemesTraitsTest(WorldGenTestCase):
    """四套风貌生成的世界包都带合规且贴合品类的属性。"""

    #: 名称关键字 → 必须包含的标签（模板里的 kind 只是 furniture/item）。
    EXPECTED_BY_NAME = (
        ("床", {"usable"}),
        ("书桌", {"container", "inscribable"}),
        ("台灯", {"switchable"}),
        ("暖炉灯", {"switchable", "thermal_or_damp"}),
        ("书架", {"container"}),
        ("农具架", {"container"}),
    )

    def test_four_themes_generate_valid_traits(self) -> None:
        for theme in AVAILABLE_THEMES:
            with self.subTest(theme=theme):
                docs = generate_world(seed=929711, theme=theme)
                self.assertEqual(validate(docs), [], f"{theme} 校验失败")
                objects = docs["objects.json"]["objects"]
                self.assertTrue(objects)
                for obj in objects:
                    traits = obj.get("affordance_traits")
                    self.assertIsInstance(traits, list)
                    self.assertTrue(traits)
                    self.assertTrue(set(traits) <= TRAITS)
                    for keyword, expected in self.EXPECTED_BY_NAME:
                        if keyword in obj["name"]:
                            self.assertTrue(
                                expected <= set(traits),
                                f"{theme}/{obj['name']} 的 {traits} 缺少 {expected}",
                            )
                            break
                    else:  # pragma: no cover - 模板里目前都会命中
                        self.assertIn("usable", traits)


class EditorBridgeTraitsTest(WorldGenTestCase):
    """正向导出 + 反向回写完整保真。"""

    def setUp(self) -> None:
        super().setUp()
        self.editor = _editor()
        self._original_support = getattr(
            self.editor, "SUPPORTS_AFFORDANCE_TRAITS", None
        )
        setattr(self.editor, "SUPPORTS_AFFORDANCE_TRAITS", True)
        self.package_dir = self.temp_package(
            make_valid_docs(seed=929711, theme="coastal_town")
        )
        self.package = load_package(self.package_dir)
        self.view = to_editor_world(self.package)

    def tearDown(self) -> None:
        if self._original_support is None:
            if hasattr(self.editor, "SUPPORTS_AFFORDANCE_TRAITS"):
                delattr(self.editor, "SUPPORTS_AFFORDANCE_TRAITS")
        else:
            setattr(
                self.editor, "SUPPORTS_AFFORDANCE_TRAITS", self._original_support
            )
        super().tearDown()

    def _write_back(self, view: dict):
        out = self.temp_dir(prefix="wg-v08-") / "edited"
        receipt = import_from_editor(self.package_dir, view, out_dir=out)
        return out, receipt, load_package(out, verify=True)

    @staticmethod
    def _objects(package) -> list[dict]:
        return package.objects

    def test_export_carries_inferred_traits(self) -> None:
        by_id = {obj["id"]: obj for obj in self.view["objects"]}
        self.assertIn("desk_home", by_id)
        for obj in self.view["objects"]:
            traits = obj.get("affordance_traits")
            self.assertIsInstance(traits, list)
            self.assertTrue(traits)
            self.assertTrue(set(traits) <= TRAITS)
        self.assertTrue(
            {"container", "inscribable"} <= set(by_id["desk_home"]["affordance_traits"])
        )
        self.assertIn("switchable", by_id["lamp_home"]["affordance_traits"])

    def test_roundtrip_preserves_traits(self) -> None:
        _out, _receipt, docs = self._write_back(copy.deepcopy(self.view))
        original = {
            obj["object_id"]: obj.get("affordance_traits")
            for obj in self.package.objects
        }
        reloaded = {
            obj["object_id"]: obj.get("affordance_traits")
            for obj in docs.objects
        }
        self.assertEqual(reloaded, original)

    def test_import_new_object_with_traits(self) -> None:
        view = copy.deepcopy(self.view)
        new_obj = copy.deepcopy(view["objects"][0])
        new_obj.update(
            {
                "id": "old_gramophone",
                "name": "旧留声机",
                "description": "还能转动的老留声机。",
                "kind": "lamp",
                "room_id": "home",
                "x": 40,
                "y": 40,
                "actions": [],
                "behavior": {"toggle": {"minutes": 1}},
                "portable": False,
                "capacity": 0,
                "servings": 0,
                "initial_status": "off",
                "asset_id": None,
                "interaction": {"near": False, "radius": 2},
                "affordance_traits": [
                    "switchable",
                    "usable",
                    "inscribable",
                ],
            }
        )
        view["objects"].append(new_obj)
        as_written = copy.deepcopy(view)

        out, receipt, docs = self._write_back(as_written)

        self.assertIn("old_gramophone", receipt["added_objects"])
        written = next(
            obj
            for obj in docs.objects
            if obj["object_id"] == "old_gramophone"
        )
        self.assertEqual(
            written["affordance_traits"],
            ["switchable", "usable", "inscribable"],
        )
        # MANIFEST 随回写一起重算：verify=True 的加载已证明摘要一致。
        self.assertTrue((out / "MANIFEST.json").exists())
        # 再次进入编辑器时属性不丢。
        second_view = to_editor_world(load_package(out))
        second = next(
            obj for obj in second_view["objects"] if obj["id"] == "old_gramophone"
        )
        self.assertEqual(
            second["affordance_traits"], ["switchable", "usable", "inscribable"]
        )

    def test_import_updates_existing_object_traits(self) -> None:
        view = copy.deepcopy(self.view)
        desk = next(obj for obj in view["objects"] if obj["id"] == "desk_home")
        desk["affordance_traits"] = ["inscribable", "movable"]

        _out, receipt, docs = self._write_back(view)

        self.assertIn("desk_home", receipt["updated_objects"])
        self.assertEqual(
            receipt["updated_objects"]["desk_home"]["affordance_traits"],
            ["inscribable", "movable"],
        )
        written = next(
            obj
            for obj in docs.objects
            if obj["object_id"] == "desk_home"
        )
        self.assertEqual(written["affordance_traits"], ["inscribable", "movable"])

    def test_invalid_editor_traits_are_refused(self) -> None:
        view = copy.deepcopy(self.view)
        desk = next(obj for obj in view["objects"] if obj["id"] == "desk_home")
        desk["affordance_traits"] = ["flying"]
        out = self.temp_dir(prefix="wg-v08-bad-") / "edited"
        with self.assertRaises(EditorBridgeError):
            import_from_editor(self.package_dir, view, out_dir=out)

    def test_support_probe_gate(self) -> None:
        self.assertTrue(
            _supports_affordance_traits(
                SimpleNamespace(SUPPORTS_AFFORDANCE_TRAITS=True)
            )
        )
        self.assertFalse(_supports_affordance_traits(SimpleNamespace()))


class NoHardcodedPronounTest(unittest.TestCase):
    """新增代码不得写死角色代词或具体角色名。"""

    def test_new_code_has_no_hardcoded_pronoun(self) -> None:
        root = Path(__file__).resolve().parents[1]
        targets = [
            root / "schema.py",
            root / "generator.py",
            root / "interior.py",
            root / "editor_bridge.py",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("她", text)
                self.assertNotIn("千代", text)


if __name__ == "__main__":
    unittest.main()
