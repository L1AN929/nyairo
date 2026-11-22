"""v0.8: generic matter physics profile in the world editor package contract."""
import copy
import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import legacy
from model import (
    MATTER_AMBIENT_ZONES,
    MATTER_ASSEMBLY_ROLES,
    MATTER_ITEM_KINDS,
    MATTER_PROFILE_KEYS,
    SUPPORTS_MATTER_PROFILE,
    WorldError,
    default_matter_profile,
    export_package,
    projection,
    validate_package,
)

EXAMPLE = BASE / "examples/rainy-bookstore.nyairo-world.json"


class MatterProfileTests(unittest.TestCase):
    def setUp(self):
        upgraded, _ = validate_package(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        # Model the historical v3 shape: full package without the new optional key.
        for o in upgraded["objects"]:
            o.pop("matter_profile", None)
        self.raw = upgraded

    def test_contract_exports_vocabulary(self):
        self.assertIs(SUPPORTS_MATTER_PROFILE, True)
        self.assertEqual(MATTER_AMBIENT_ZONES, ("room", "cold", "heating"))
        self.assertEqual(
            MATTER_ITEM_KINDS,
            ("ingredient", "prepared_food", "drink", "vessel", "tool", "paper", "keepsake", "everyday"),
        )
        self.assertEqual(MATTER_ASSEMBLY_ROLES, ("none", "host_frame", "insert_part"))
        self.assertEqual(
            MATTER_PROFILE_KEYS,
            ("ambient_zone", "water_source", "capacity_slots", "item_kind", "paper_like",
             "shelf_life_hours", "portions", "material_units", "assembly_role"),
        )

    def test_defaults_follow_kind_and_name(self):
        self.assertEqual(
            default_matter_profile("cup"),
            {"ambient_zone": "room", "water_source": False, "capacity_slots": 1,
             "item_kind": "vessel", "paper_like": False, "portions": 1},
        )
        self.assertEqual(
            default_matter_profile("book"),
            {"ambient_zone": "room", "water_source": False, "capacity_slots": 0,
             "item_kind": "paper", "paper_like": True},
        )
        self.assertEqual(
            default_matter_profile("cabinet"),
            {"ambient_zone": "room", "water_source": False, "capacity_slots": 4,
             "item_kind": "everyday", "paper_like": False},
        )
        self.assertEqual(default_matter_profile("table", capacity=0)["capacity_slots"], 4)
        self.assertEqual(
            default_matter_profile("dispenser"),
            {"ambient_zone": "room", "water_source": True, "capacity_slots": 2,
             "item_kind": "everyday", "paper_like": False},
        )
        for kind in ("chair", "bed", "lamp", "plant"):
            self.assertEqual(default_matter_profile(kind)["item_kind"], "everyday")
            self.assertEqual(default_matter_profile(kind)["capacity_slots"], 0)
        self.assertEqual(default_matter_profile("cabinet", name="冷柜")["ambient_zone"], "cold")
        self.assertEqual(default_matter_profile("cabinet", name="冷柜")["capacity_slots"], 8)
        self.assertEqual(default_matter_profile("cabinet", capacity=12, name="fridge")["capacity_slots"], 12)
        self.assertEqual(default_matter_profile("table", name="炉台")["ambient_zone"], "heating")
        self.assertEqual(default_matter_profile("table", name="水壶")["capacity_slots"], 3)
        self.assertEqual(default_matter_profile("table", capacity=6, name="oven")["capacity_slots"], 6)

    def test_old_v3_package_without_profile_is_accepted_and_defaulted(self):
        p, _ = validate_package(self.raw)
        for o in p["objects"]:
            self.assertIsInstance(o["matter_profile"], dict)
            self.assertEqual(
                o["matter_profile"],
                default_matter_profile(o["kind"], o["portable"], o["capacity"], o["name"]),
            )
        roundtrip, _ = validate_package(p)
        self.assertEqual([o["matter_profile"] for o in roundtrip["objects"]],
                         [o["matter_profile"] for o in p["objects"]])

    def test_none_profile_is_filled_with_defaults(self):
        raw = copy.deepcopy(self.raw)
        raw["objects"][0]["matter_profile"] = None
        p, _ = validate_package(raw)
        o = p["objects"][0]
        self.assertEqual(
            o["matter_profile"],
            default_matter_profile(o["kind"], o["portable"], o["capacity"], o["name"]),
        )

    def test_valid_profile_survives_export_and_projection_strips_it(self):
        profile = {"ambient_zone": "cold", "water_source": False, "capacity_slots": 8,
                   "item_kind": "prepared_food", "paper_like": False,
                   "shelf_life_hours": 24, "portions": 2}
        raw = copy.deepcopy(self.raw)
        raw["objects"][0]["matter_profile"] = copy.deepcopy(profile)
        p, _ = validate_package(raw)
        self.assertEqual(p["objects"][0]["matter_profile"], profile)
        exported = export_package(copy.deepcopy(p))
        self.assertEqual(exported["objects"][0]["matter_profile"], profile)
        downgraded = projection(p)
        self.assertTrue(all("matter_profile" not in o for o in downgraded["objects"]))
        legacy_package, _ = legacy.validate_package(downgraded)
        self.assertTrue(legacy_package["objects"])

    def test_partial_profile_receives_defaults(self):
        raw = copy.deepcopy(self.raw)
        raw["objects"][0]["matter_profile"] = {"item_kind": "tool"}
        p, _ = validate_package(raw)
        mp = p["objects"][0]["matter_profile"]
        self.assertEqual(mp["item_kind"], "tool")
        self.assertEqual(mp["ambient_zone"], "room")
        self.assertIs(mp["water_source"], False)
        self.assertEqual(mp["capacity_slots"], 0)
        self.assertIs(mp["paper_like"], False)

    def test_craft_fields_survive_validation_and_projection_strips_profile(self):
        profile = {"ambient_zone": "room", "water_source": False, "capacity_slots": 4,
                   "item_kind": "everyday", "paper_like": False,
                   "material_units": 4, "assembly_role": "host_frame"}
        raw = copy.deepcopy(self.raw)
        raw["objects"][0]["matter_profile"] = copy.deepcopy(profile)
        p, _ = validate_package(raw)
        self.assertEqual(p["objects"][0]["matter_profile"], profile)
        exported = export_package(copy.deepcopy(p))
        self.assertEqual(exported["objects"][0]["matter_profile"], profile)
        downgraded = projection(p)
        self.assertTrue(all("matter_profile" not in o for o in downgraded["objects"]))

    def test_craft_fields_are_not_injected_by_validation(self):
        p, _ = validate_package(copy.deepcopy(self.raw))
        mp = p["objects"][0]["matter_profile"]
        self.assertNotIn("material_units", mp)
        self.assertNotIn("assembly_role", mp)

    def test_invalid_craft_fields_are_rejected(self):
        cases = (
            {"material_units": 0},
            {"material_units": 25},
            {"material_units": True},
            {"material_units": 1.5},
            {"material_units": "4"},
            {"assembly_role": "frame"},
            {"assembly_role": 1},
        )
        for change in cases:
            raw = copy.deepcopy(self.raw)
            raw["objects"][0]["matter_profile"] = dict(change)
            with self.assertRaises(WorldError, msg=str(change)):
                validate_package(raw)

    def test_valid_craft_units_boundaries(self):
        for units in (1, 4, 24):
            raw = copy.deepcopy(self.raw)
            raw["objects"][0]["matter_profile"] = {"material_units": units}
            p, _ = validate_package(raw)
            self.assertEqual(p["objects"][0]["matter_profile"]["material_units"], units)
        for role in MATTER_ASSEMBLY_ROLES:
            raw = copy.deepcopy(self.raw)
            raw["objects"][0]["matter_profile"] = {"assembly_role": role}
            p, _ = validate_package(raw)
            self.assertEqual(p["objects"][0]["matter_profile"]["assembly_role"], role)

    def test_invalid_profiles_and_unknown_fields_are_rejected(self):
        cases = (
            {"matter_profile": {"ambient_zone": "hot"}},
            {"matter_profile": {"capacity_slots": -1}},
            {"matter_profile": {"capacity_slots": 25}},
            {"matter_profile": {"capacity_slots": 2.5}},
            {"matter_profile": {"item_kind": "food"}},
            {"matter_profile": {"water_source": "yes"}},
            {"matter_profile": {"paper_like": 1}},
            {"matter_profile": {"shelf_life_hours": 0}},
            {"matter_profile": {"shelf_life_hours": 8761}},
            {"matter_profile": {"shelf_life_hours": "24"}},
            {"matter_profile": {"shelf_life_hours": True}},
            {"matter_profile": {"portions": 0}},
            {"matter_profile": {"portions": 25}},
            {"matter_profile": {"portions": 1.5}},
            {"matter_profile": {"nonsense": 1}},
            {"matter_profile": "cold"},
            {"matter_profile": ["cold"]},
        )
        for change in cases:
            raw = copy.deepcopy(self.raw)
            raw["objects"][0].update(change)
            with self.assertRaises(WorldError, msg=str(change)):
                validate_package(raw)

    def test_web_panel_has_chinese_matter_labels_and_neutral_copy(self):
        app = (BASE / "web/app.js").read_text(encoding="utf-8")
        html = (BASE / "web/index.html").read_text(encoding="utf-8")
        labels = ("物理与盛放属性", "温区", "常温区", "冷藏保鲜区", "加热热源区",
                  "可供清水洗涤", "盛放格位数", "材质类别", "食材原料", "熟食茶点",
                  "饮品汤水", "容器器皿", "日常工具", "纸质书页", "纪念小物", "普通物件",
                  "遇水易浸湿的纸质品", "保鲜小时数", "可用份数/张数", "装配角色",
                  "普通单件", "可装入隔板/配件的容器", "可拆装配件/隔板")
        for label in labels:
            self.assertIn(label, html)
        self.assertIn('id="matter-template"', html)
        self.assertIn("data-matter", app)
        self.assertIn("matter_profile", app)
        self.assertIn("defaultMatterProfile", app)
        self.assertIn("matter:", app)
        for text in (app, html):
            self.assertNotIn("她", text)
            self.assertNotIn("千代", text)


if __name__ == "__main__":
    unittest.main()
