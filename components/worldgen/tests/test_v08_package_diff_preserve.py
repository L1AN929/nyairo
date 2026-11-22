"""v0.8 世界包编辑差量回写：matter_profile 九键保真与删除安全。

覆盖：既有/新建物件的完整九键物质档往返、未触碰实体零改写（NPC/环境事件/
地点规则原样保留）、删除被引用地点时逐条拒绝、老包五键档不被注入
``material_units``/``assembly_role``。
"""

from __future__ import annotations

import copy
import unittest

from ._setup import WorldGenTestCase, make_valid_docs
from components.worldgen import schema
from components.worldgen.editor_bridge import (
    EditorBridgeError,
    import_from_editor,
    to_editor_world,
)
from components.worldgen.loader import load as load_package

#: 九键齐全的工艺物质档：插入件、冷藏、24 小时保鲜、6 份材料。
CRAFT_PROFILE = {
    "ambient_zone": "cold",
    "water_source": False,
    "capacity_slots": 2,
    "item_kind": "paper",
    "paper_like": True,
    "shelf_life_hours": 24,
    "portions": 1,
    "material_units": 6,
    "assembly_role": "insert_part",
}

CRAFT_KEYS = {
    "ambient_zone",
    "water_source",
    "capacity_slots",
    "item_kind",
    "paper_like",
    "shelf_life_hours",
    "portions",
    "material_units",
    "assembly_role",
}


class PackageDiffPreserveTest(WorldGenTestCase):
    """生成包 → 编辑器视图 → 差量回写的保真与安全。"""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(CRAFT_KEYS, set(schema.MATTER_PROFILE_KEYS))
        self.assertEqual(len(schema.MATTER_PROFILE_KEYS), 9)
        self.package_dir = self.temp_package(
            make_valid_docs(seed=929711, theme="coastal_town")
        )
        self.package = load_package(self.package_dir)
        self.view = to_editor_world(self.package)
        self.original_docs = schema.load_package(self.package_dir)

    # -- 辅助 ---------------------------------------------------------------

    def _write_back(self, view: dict):
        out = self.temp_dir(prefix="wg-preserve-") / "edited"
        receipt = import_from_editor(self.package_dir, view, out_dir=out)
        docs = schema.load_package(out, verify=True)
        return out, receipt, docs

    def _object(self, docs: dict, object_id: str) -> dict:
        return next(
            obj for obj in docs["objects.json"]["objects"]
            if obj["object_id"] == object_id
        )

    def _view_without(self, editor_id: str) -> dict:
        view = copy.deepcopy(self.view)
        view["rooms"] = [room for room in view["rooms"] if room["id"] != editor_id]
        view["connections"] = [
            conn for conn in view["connections"]
            if editor_id not in (conn["from"], conn["to"])
        ]
        return view

    def _assert_refused_deletion(self, editor_id: str, expected_fragments: list[str]) -> None:
        out = self.temp_dir(prefix="wg-delete-") / "must-not-exist"
        with self.assertRaises(EditorBridgeError) as ctx:
            import_from_editor(
                self.package_dir, self._view_without(editor_id), out_dir=out
            )
        message = str(ctx.exception)
        self.assertIn("仍被世界事实引用", message)
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, message)
        self.assertFalse(out.exists(), "拒绝删除时不应写出任何世界包")

    # -- 既有物件：九键全保真，其余实体零改写 -------------------------------

    def test_existing_object_full_nine_key_profile_roundtrips(self) -> None:
        view = copy.deepcopy(self.view)
        desk = next(obj for obj in view["objects"] if obj["id"] == "desk_home")
        desk["matter_profile"] = copy.deepcopy(CRAFT_PROFILE)

        _out, receipt, docs = self._write_back(view)

        self.assertIn("desk_home", receipt["updated_objects"])
        written = self._object(docs, "desk_home")
        self.assertEqual(written["matter_profile"], CRAFT_PROFILE)
        self.assertEqual(set(written["matter_profile"]), CRAFT_KEYS)
        # 再次进入编辑器，九键仍在。
        second = to_editor_world(load_package(_out))
        second_obj = next(obj for obj in second["objects"] if obj["id"] == "desk_home")
        self.assertEqual(second_obj["matter_profile"], CRAFT_PROFILE)

        # 未触碰的物件保持原样。
        for object_id in ("bed_home", "lamp_home"):
            with self.subTest(object_id=object_id):
                self.assertEqual(
                    self._object(docs, object_id),
                    self._object(self.original_docs, object_id),
                )
        # NPC 与环境事件不被差量回写改写。
        self.assertEqual(docs["npcs.json"], self.original_docs["npcs.json"])
        self.assertEqual(docs["ambient.json"], self.original_docs["ambient.json"])

    # -- 新建物件：九键全保真 ----------------------------------------------

    def test_new_object_full_nine_key_profile_roundtrips(self) -> None:
        view = copy.deepcopy(self.view)
        new_obj = copy.deepcopy(view["objects"][0])
        new_obj.update(
            {
                "id": "craft_insert_part",
                "name": "可插入工艺件",
                "description": "一片准备嵌进宿主框架的纸板。",
                "kind": "cabinet",
                "room_id": "home",
                "x": 45,
                "y": 45,
                "actions": ["store", "retrieve"],
                "behavior": {},
                "portable": False,
                "capacity": 10,
                "servings": 0,
                "initial_status": "neutral",
                "asset_id": None,
                "matter_profile": copy.deepcopy(CRAFT_PROFILE),
            }
        )
        view["objects"].append(new_obj)

        out, receipt, docs = self._write_back(view)

        self.assertIn("craft_insert_part", receipt["added_objects"])
        written = self._object(docs, "craft_insert_part")
        self.assertEqual(written["matter_profile"], CRAFT_PROFILE)
        self.assertEqual(set(written["matter_profile"]), CRAFT_KEYS)
        second = to_editor_world(load_package(out))
        second_obj = next(
            obj for obj in second["objects"] if obj["id"] == "craft_insert_part"
        )
        self.assertEqual(second_obj["matter_profile"], CRAFT_PROFILE)

    # -- 无改动往返：对象、NPC、事件、地点全量零改写 ------------------------

    def test_unedited_roundtrip_is_zero_clobber(self) -> None:
        _out, receipt, docs = self._write_back(copy.deepcopy(self.view))

        self.assertEqual(receipt["updated_objects"], {})
        self.assertEqual(receipt["added_objects"], [])
        self.assertEqual(receipt["removed_objects"], [])
        self.assertEqual(receipt["updated_locations"], {})
        for filename in (
            "objects.json",
            "npcs.json",
            "ambient.json",
            "topology.json",
            "rules.json",
            "content.json",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(docs[filename], self.original_docs[filename])

    # -- 老包：缺省键不注入，既有键不丢失 -----------------------------------

    def test_legacy_profiles_do_not_gain_craft_keys(self) -> None:
        docs = make_valid_docs(seed=929711, theme="coastal_town")
        for obj in docs["objects.json"]["objects"]:
            if obj["object_id"] == "bed_home":
                obj.pop("matter_profile", None)
        legacy_dir = self.temp_package(docs, prefix="wg-legacy-")
        legacy_package = load_package(legacy_dir)
        view = to_editor_world(legacy_package)

        # 正向视图：五键档不被补 material_units，声明过的 assembly_role 仍在。
        desk_view = next(obj for obj in view["objects"] if obj["id"] == "desk_home")
        self.assertEqual(
            set(desk_view["matter_profile"]),
            {
                "ambient_zone",
                "water_source",
                "capacity_slots",
                "item_kind",
                "paper_like",
            },
        )
        lamp_view = next(obj for obj in view["objects"] if obj["id"] == "lamp_home")
        self.assertEqual(lamp_view["matter_profile"].get("assembly_role"), "host_frame")
        self.assertNotIn("material_units", lamp_view["matter_profile"])

        out = self.temp_dir(prefix="wg-legacy-out-") / "edited"
        import_from_editor(legacy_dir, copy.deepcopy(view), out_dir=out)
        written = schema.load_package(out, verify=True)

        bed = self._object(written, "bed_home")
        self.assertNotIn("matter_profile", bed, "未声明的物质档不应被回写注入")
        desk = self._object(written, "desk_home")
        self.assertEqual(
            set(desk["matter_profile"]),
            {
                "ambient_zone",
                "water_source",
                "capacity_slots",
                "item_kind",
                "paper_like",
            },
        )
        lamp = self._object(written, "lamp_home")
        self.assertEqual(lamp["matter_profile"].get("assembly_role"), "host_frame")
        for obj in written["objects.json"]["objects"]:
            with self.subTest(object_id=obj["object_id"]):
                self.assertNotIn("material_units", obj.get("matter_profile", {}))

    # -- 删除安全：被 NPC/环境事件引用的地点逐条拒绝 -------------------------

    def test_deleting_place_referenced_by_npc_and_event_is_refused(self) -> None:
        self._assert_refused_deletion(
            "school",
            [
                "NPC coastal_npc_school_01",
                "的出场时段在 school",
                "环境事件 school_notice 绑定在 school",
            ],
        )

    def test_deleting_place_referenced_only_by_event_is_refused(self) -> None:
        self._assert_refused_deletion(
            "park",
            ["环境事件 park_wind_chime 绑定在 park"],
        )

    def test_deleting_unreferenced_place_succeeds(self) -> None:
        _out, receipt, docs = self._write_back(self._view_without("lighthouse"))

        self.assertEqual(receipt["removed_locations"], ["lighthouse"])
        self.assertNotIn(
            "lighthouse",
            {loc["location_id"] for loc in docs["topology.json"]["locations"]},
        )
        self.assertNotIn("lighthouse", docs["rules.json"]["location_rules"])
        self.assertEqual(docs["npcs.json"], self.original_docs["npcs.json"])
        self.assertEqual(docs["ambient.json"], self.original_docs["ambient.json"])


if __name__ == "__main__":
    unittest.main()
