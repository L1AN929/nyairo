"""v0.8 物件物质档（matter_profile）验收。

覆盖：schema 校验与老包向后兼容、四种风貌的确定性推断、室内家具落盘、
编辑器桥接的正向导出与反向回写（含自创物件与既有物件修改），以及新增
代码不写死角色代词。
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import editor_bridge, schema
from components.worldgen.editor_bridge import (
    EditorBridgeError,
    _editor,
    _supports_matter_profile,
    import_from_editor,
    object_id_map,
    to_editor_world,
)
from components.worldgen.generator import (
    AVAILABLE_THEMES,
    generate_world,
    infer_matter_profile,
)
from components.worldgen.interior import apply_interiors_to_package
from components.worldgen.loader import load as load_package
from components.worldgen.validator import validate

FULL_PROFILE = {
    "ambient_zone": "cold",
    "water_source": False,
    "capacity_slots": 10,
    "item_kind": "everyday",
    "paper_like": False,
    "shelf_life_hours": 48,
    "portions": 2,
}


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


def normalize(profile: dict) -> dict:
    return schema.normalize_matter_profile(profile, "测试.matter_profile")


class NormalizeMatterProfileTest(unittest.TestCase):
    """结构校验：合法值规范化、非法值一律拒绝。"""

    def test_valid_profile_is_preserved(self) -> None:
        self.assertEqual(normalize(dict(FULL_PROFILE)), FULL_PROFILE)

    def test_empty_profile_stays_empty(self) -> None:
        self.assertEqual(normalize({}), {}, "未提供的字段不应被补默认值")

    def test_partial_profile_keeps_only_present_keys(self) -> None:
        normalized = normalize({"ambient_zone": "heating", "portions": 1})
        self.assertEqual(normalized, {"ambient_zone": "heating", "portions": 1})

    def test_craft_fields_are_preserved_when_present(self) -> None:
        profile = dict(FULL_PROFILE, material_units=4, assembly_role="host_frame")
        self.assertEqual(normalize(profile), profile)

    def test_craft_fields_are_not_injected(self) -> None:
        normalized = normalize({"material_units": 1})
        self.assertEqual(normalized, {"material_units": 1})
        self.assertNotIn("assembly_role", normalized)
        self.assertNotIn("material_units", normalize(dict(FULL_PROFILE)))
        self.assertNotIn("assembly_role", normalize(dict(FULL_PROFILE)))

    def test_craft_units_boundaries(self) -> None:
        for units in (1, 4, 24):
            with self.subTest(units=units):
                self.assertEqual(normalize({"material_units": units}), {"material_units": units})
        for role in schema.CANONICAL_ASSEMBLY_ROLES:
            with self.subTest(role=role):
                self.assertEqual(normalize({"assembly_role": role}), {"assembly_role": role})

    def test_shelf_life_accepts_int_and_float(self) -> None:
        self.assertEqual(normalize({"shelf_life_hours": 24}), {"shelf_life_hours": 24})
        self.assertEqual(
            normalize({"shelf_life_hours": 12.5}), {"shelf_life_hours": 12.5}
        )

    def test_invalid_inputs_are_rejected(self) -> None:
        bad_values = [
            None,
            [],
            "cold",
            True,
            3,
            {"bogus": 1},
            {"ambient_zone": "frozen"},
            {"ambient_zone": 3},
            {"water_source": 1},
            {"water_source": "yes"},
            {"capacity_slots": True},
            {"capacity_slots": 2.0},
            {"capacity_slots": -1},
            {"capacity_slots": 25},
            {"item_kind": "food"},
            {"paper_like": "yes"},
            {"shelf_life_hours": 0},
            {"shelf_life_hours": -1},
            {"shelf_life_hours": 9000},
            {"shelf_life_hours": True},
            {"shelf_life_hours": float("inf")},
            {"portions": True},
            {"portions": 0},
            {"portions": 25},
            {"material_units": 0},
            {"material_units": 25},
            {"material_units": True},
            {"material_units": 4.0},
            {"material_units": "4"},
            {"material_units": None},
            {"assembly_role": "frame"},
            {"assembly_role": "HOST_FRAME"},
            {"assembly_role": 1},
            {"assembly_role": None},
        ]
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(schema.WorldPackageError):
                    schema.normalize_matter_profile(value, "测试")


class LegacyObjectTest(unittest.TestCase):
    """objects.json 的可选字段与老包兼容。"""

    def test_legacy_object_without_profile_keeps_five_keys(self) -> None:
        normalized = schema.validate_objects_doc(objects_doc([valid_object()]))
        entry = normalized["objects"][0]
        self.assertEqual(
            set(entry),
            {"object_id", "name", "kind", "location_id", "note"},
            "老包没有 matter_profile 时不应被硬塞默认字段",
        )

    def test_profile_is_validated_and_preserved(self) -> None:
        normalized = schema.validate_objects_doc(
            objects_doc([valid_object(matter_profile=dict(FULL_PROFILE))])
        )
        self.assertEqual(normalized["objects"][0]["matter_profile"], FULL_PROFILE)

    def test_invalid_profile_in_objects_doc_is_rejected(self) -> None:
        with self.assertRaises(schema.WorldPackageError):
            schema.validate_objects_doc(
                objects_doc(
                    [valid_object(matter_profile={"ambient_zone": "frozen"})]
                )
            )

    def test_unknown_object_key_is_still_rejected(self) -> None:
        with self.assertRaises(schema.WorldPackageError):
            schema.validate_objects_doc(objects_doc([valid_object(color="red")]))


class InferMatterProfileTest(unittest.TestCase):
    """确定性推断函数本身。"""

    def _infer(self, kind: str, name: str, **kwargs):
        profile = infer_matter_profile(kind, name=name, **kwargs)
        # 推断结果必须能通过 schema 规范化（保证落盘合法）。
        self.assertEqual(normalize(profile), profile)
        return profile

    def test_cold_appliance(self) -> None:
        profile = self._infer("furniture", "冰箱", capacity=4)
        self.assertEqual(profile["ambient_zone"], "cold")
        self.assertGreaterEqual(profile["capacity_slots"], 6)
        self.assertFalse(profile["water_source"])

    def test_heating_appliance(self) -> None:
        profile = self._infer("furniture", "烤箱")
        self.assertEqual(profile["ambient_zone"], "heating")
        self.assertGreaterEqual(profile["capacity_slots"], 2)

    def test_water_source(self) -> None:
        profile = self._infer("furniture", "洗手池")
        self.assertTrue(profile["water_source"])

    def test_paper_like(self) -> None:
        profile = self._infer("item", "日记本")
        self.assertEqual(profile["item_kind"], "paper")
        self.assertTrue(profile["paper_like"])

    def test_vessel(self) -> None:
        profile = self._infer("cup", "茶杯")
        self.assertEqual(profile["item_kind"], "vessel")
        self.assertGreaterEqual(profile["capacity_slots"], 1)
        self.assertEqual(profile["portions"], 1)

    def test_drink(self) -> None:
        profile = self._infer("item", "咖啡")
        self.assertEqual(profile["item_kind"], "drink")
        self.assertGreater(profile["shelf_life_hours"], 0)

    def test_prepared_food(self) -> None:
        profile = self._infer("item", "面包")
        self.assertEqual(profile["item_kind"], "prepared_food")
        self.assertGreater(profile["shelf_life_hours"], 0)

    def test_ingredient(self) -> None:
        profile = self._infer("item", "鸡蛋")
        self.assertEqual(profile["item_kind"], "ingredient")

    def test_furniture_container(self) -> None:
        profile = self._infer("furniture", "衣柜")
        self.assertEqual(profile["item_kind"], "everyday")
        self.assertGreaterEqual(profile["capacity_slots"], 3)
        self.assertEqual(profile["assembly_role"], "host_frame")

    def test_single_book_has_no_material_units(self) -> None:
        profile = self._infer("item", "日记本")
        self.assertNotIn("material_units", profile)

    def test_stack_paper_has_material_units(self) -> None:
        profile = self._infer("item", "一叠报纸")
        self.assertEqual(profile["material_units"], 4)
        self.assertNotIn("assembly_role", profile)

    def test_fallback(self) -> None:
        profile = self._infer("gadget", "有些年头的小玩意")
        self.assertEqual(profile["ambient_zone"], "room")
        self.assertEqual(profile["capacity_slots"], 0)
        self.assertEqual(profile["item_kind"], "everyday")


class FourThemesMatterProfileTest(WorldGenTestCase):
    """四套风貌生成的世界包都带合规的物质档，并可写盘/重载。"""

    def test_four_themes_generate_valid_profiles(self) -> None:
        for theme in AVAILABLE_THEMES:
            with self.subTest(theme=theme):
                docs = generate_world(seed=929711, theme=theme)
                self.assertEqual(validate(docs), [], f"{theme} 校验失败")
                objects = docs["objects.json"]["objects"]
                self.assertTrue(objects)
                for obj in objects:
                    profile = obj.get("matter_profile")
                    self.assertIsInstance(profile, dict)
                    self.assertEqual(normalize(profile), profile)
                package_dir = self.temp_package(docs)
                reloaded = schema.load_package(package_dir, verify=True)
                for obj in reloaded["objects.json"]["objects"]:
                    self.assertIn("matter_profile", obj)


class InteriorMatterProfileTest(WorldGenTestCase):
    """室内家具写入 objects.json 时携带合法物质档。"""

    def test_apply_interiors_emits_valid_matter_profiles(self) -> None:
        package_dir = self.temp_package(
            make_valid_docs(seed=929711, theme="coastal_town")
        )
        result = apply_interiors_to_package(package_dir, locations=["home"])
        self.assertEqual(result["status"], "written")
        docs = schema.load_package(package_dir, verify=True)
        home_objects = [
            obj
            for obj in docs["objects.json"]["objects"]
            if obj["location_id"] == "home"
        ]
        self.assertTrue(home_objects)
        for obj in home_objects:
            profile = obj.get("matter_profile")
            self.assertIsInstance(profile, dict)
            self.assertEqual(normalize(profile), profile)
        self.assertTrue(
            any(obj["matter_profile"].get("water_source") for obj in home_objects),
            "家里的饮水机应被推断为水源",
        )
        self.assertTrue(
            any(obj["matter_profile"].get("paper_like") for obj in home_objects),
            "桌上的笔记本应被推断为纸质物件",
        )


class _EditorProxy:
    """把真实编辑器模块包一层，模拟对 ``matter_profile`` 的支持。

    当前 ``world-editor/model.py`` 尚未声明 ``SUPPORTS_MATTER_PROFILE`` 时，
    用它验证桥接的正向/反向行为；真实模块支持后代理行为保持一致。
    """

    def __init__(self, target, *, supports_matter: bool) -> None:
        self.__dict__["_target"] = target
        self.__dict__["SUPPORTS_MATTER_PROFILE"] = supports_matter

    def __getattr__(self, name):
        return getattr(self.__dict__["_target"], name)

    def validate_package(self, raw):
        target = self.__dict__["_target"]
        if not self.__dict__["SUPPORTS_MATTER_PROFILE"]:
            # 记录桥接是否主动塞了物质档键（真实编辑器是否支持都适用）。
            self.__dict__["input_had_profile"] = any(
                isinstance(obj, dict) and "matter_profile" in obj
                for obj in raw.get("objects", [])
            )
            return target.validate_package(raw)
        trimmed = copy.deepcopy(raw)
        saved: dict = {}
        for obj in trimmed.get("objects", []):
            if isinstance(obj, dict) and "matter_profile" in obj:
                saved[obj.get("id")] = obj.pop("matter_profile")
        validated, warnings = target.validate_package(trimmed)
        for obj in validated.get("objects", []):
            if obj.get("id") in saved:
                obj["matter_profile"] = saved[obj["id"]]
        return validated, warnings


class EditorBridgeMatterProfileTest(WorldGenTestCase):
    """正向导出 + 反向回写完整保真（编辑器声明支持时不丢物质档）。"""

    def setUp(self) -> None:
        super().setUp()
        self.real_editor = _editor()
        self._original_factory = editor_bridge._editor
        editor_bridge._editor = lambda: _EditorProxy(
            self.real_editor, supports_matter=True
        )
        self.package_dir = self.temp_package(
            make_valid_docs(seed=929711, theme="coastal_town")
        )
        self.package = load_package(self.package_dir)
        self.view = to_editor_world(self.package)

    def tearDown(self) -> None:
        editor_bridge._editor = self._original_factory
        super().tearDown()

    def _write_back(self, view: dict):
        out = self.temp_dir(prefix="wg-matter-") / "edited"
        receipt = import_from_editor(self.package_dir, view, out_dir=out)
        return out, receipt, load_package(out, verify=True)

    def test_export_carries_package_profiles(self) -> None:
        mapping = object_id_map(self.package)
        world_by_id = {obj["object_id"]: obj for obj in self.package.objects}
        for editor_obj in self.view["objects"]:
            world_obj = world_by_id[mapping[editor_obj["id"]]]
            self.assertEqual(
                editor_obj["matter_profile"], world_obj["matter_profile"]
            )

    def test_roundtrip_preserves_profiles(self) -> None:
        _out, _receipt, docs = self._write_back(copy.deepcopy(self.view))
        original = {
            obj["object_id"]: obj.get("matter_profile")
            for obj in self.package.objects
        }
        reloaded = {
            obj["object_id"]: obj.get("matter_profile") for obj in docs.objects
        }
        self.assertEqual(reloaded, original)

    def test_import_new_cold_cabinet(self) -> None:
        view = copy.deepcopy(self.view)
        new_obj = copy.deepcopy(view["objects"][0])
        new_obj.update(
            {
                "id": "cold_cabinet",
                "name": "冷藏柜",
                "description": "嗡嗡作响的冷藏柜。",
                "kind": "cabinet",
                "room_id": "home",
                "x": 40,
                "y": 40,
                "actions": ["store", "retrieve"],
                "behavior": {},
                "portable": False,
                "capacity": 10,
                "servings": 0,
                "initial_status": "neutral",
                "asset_id": None,
                "interaction": {"near": False, "radius": 2},
                "affordance_traits": ["container", "combinable"],
                "matter_profile": {
                    "ambient_zone": "cold",
                    "water_source": False,
                    "capacity_slots": 10,
                    "item_kind": "everyday",
                    "paper_like": False,
                },
            }
        )
        view["objects"].append(new_obj)
        as_written = copy.deepcopy(view)

        out, receipt, docs = self._write_back(as_written)

        self.assertIn("cold_cabinet", receipt["added_objects"])
        written = next(
            obj for obj in docs.objects if obj["object_id"] == "cold_cabinet"
        )
        self.assertEqual(written["matter_profile"], new_obj["matter_profile"])
        self.assertTrue((out / "MANIFEST.json").exists())
        # 再次进入编辑器时物质档不丢。
        second_view = to_editor_world(load_package(out))
        second = next(
            obj for obj in second_view["objects"] if obj["id"] == "cold_cabinet"
        )
        self.assertEqual(second["matter_profile"], new_obj["matter_profile"])

    def test_import_updates_existing_profile(self) -> None:
        view = copy.deepcopy(self.view)
        desk = next(obj for obj in view["objects"] if obj["id"] == "desk_home")
        new_profile = {
            "ambient_zone": "heating",
            "water_source": False,
            "capacity_slots": 2,
            "item_kind": "everyday",
            "paper_like": False,
        }
        desk["matter_profile"] = dict(new_profile)

        _out, receipt, docs = self._write_back(view)

        self.assertIn("desk_home", receipt["updated_objects"])
        self.assertEqual(
            receipt["updated_objects"]["desk_home"]["matter_profile"], new_profile
        )
        written = next(
            obj for obj in docs.objects if obj["object_id"] == "desk_home"
        )
        self.assertEqual(written["matter_profile"], new_profile)

    def test_invalid_editor_profile_is_refused(self) -> None:
        view = copy.deepcopy(self.view)
        desk = next(obj for obj in view["objects"] if obj["id"] == "desk_home")
        desk["matter_profile"] = {"ambient_zone": "cold", "capacity_slots": 99}
        out = self.temp_dir(prefix="wg-matter-bad-") / "edited"
        with self.assertRaises(EditorBridgeError):
            import_from_editor(self.package_dir, view, out_dir=out)


class EditorWithoutMatterSupportTest(WorldGenTestCase):
    """编辑器未声明支持时，桥接不主动强加 matter_profile 键。"""

    def test_bridge_does_not_force_profile_when_unsupported(self) -> None:
        real_editor = _editor()
        proxy = _EditorProxy(real_editor, supports_matter=False)
        original_factory = editor_bridge._editor
        editor_bridge._editor = lambda: proxy
        try:
            package_dir = self.temp_package(
                make_valid_docs(seed=929711, theme="coastal_town")
            )
            view = to_editor_world(load_package(package_dir))
        finally:
            editor_bridge._editor = original_factory
        self.assertTrue(view["objects"])
        # 传给编辑器的物件里不含桥接强加的 matter_profile（编辑器自身可能
        # 支持并补默认值，那不归桥接管）。
        self.assertFalse(proxy.__dict__.get("input_had_profile", True))

    def test_support_probe_gate(self) -> None:
        self.assertTrue(
            _supports_matter_profile(
                SimpleNamespace(SUPPORTS_MATTER_PROFILE=True)
            )
        )
        self.assertFalse(_supports_matter_profile(SimpleNamespace()))


class RealEditorMatterProfileTest(WorldGenTestCase):
    """真实编辑器已声明支持时的整链回写（未声明则跳过）。"""

    def setUp(self) -> None:
        super().setUp()
        if not _supports_matter_profile(_editor()):
            self.skipTest(
                "当前 world-editor/model.py 尚未声明 SUPPORTS_MATTER_PROFILE"
            )
        self.package_dir = self.temp_package(
            make_valid_docs(seed=929711, theme="coastal_town")
        )
        self.package = load_package(self.package_dir)

    def test_real_editor_roundtrip_preserves_profiles(self) -> None:
        view = to_editor_world(self.package)
        for obj in view["objects"]:
            self.assertIn("matter_profile", obj)
        out = self.temp_dir(prefix="wg-matter-real-") / "edited"
        import_from_editor(self.package_dir, copy.deepcopy(view), out_dir=out)
        reloaded = load_package(out, verify=True)
        original = {
            obj["object_id"]: obj["matter_profile"] for obj in self.package.objects
        }
        written = {
            obj["object_id"]: obj["matter_profile"] for obj in reloaded.objects
        }
        self.assertEqual(written, original)


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
