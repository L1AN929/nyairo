"""v0.8: generic interaction traits in the world editor package contract."""
import copy
import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import legacy
from model import (
    AFFORDANCE_TRAITS,
    SUPPORTS_AFFORDANCE_TRAITS,
    WorldError,
    default_affordance_traits,
    export_package,
    projection,
    validate_package,
)

EXAMPLE = BASE / "examples/rainy-bookstore.nyairo-world.json"


class AffordanceTraitTests(unittest.TestCase):
    def setUp(self):
        upgraded, _ = validate_package(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        # Model the historical v3 shape: full package without the new optional key.
        for o in upgraded["objects"]:
            o.pop("affordance_traits", None)
        self.raw = upgraded

    def test_contract_exports_standard_vocabulary(self):
        self.assertIs(SUPPORTS_AFFORDANCE_TRAITS, True)
        self.assertEqual(
            AFFORDANCE_TRAITS,
            (
                "movable",
                "insertable",
                "container",
                "inscribable",
                "switchable",
                "thermal_or_damp",
                "usable",
                "combinable",
            ),
        )

    def test_defaults_follow_kind_and_hard_constraints(self):
        self.assertEqual(default_affordance_traits("lamp"), ["switchable", "usable"])
        self.assertEqual(default_affordance_traits("dispenser"), ["switchable", "usable"])
        self.assertEqual(default_affordance_traits("cup"), ["usable", "thermal_or_damp", "movable"])
        self.assertEqual(default_affordance_traits("cabinet"), ["container", "combinable"])
        self.assertEqual(default_affordance_traits("table"), ["container", "inscribable"])
        self.assertEqual(default_affordance_traits("chair"), ["usable", "movable"])
        self.assertEqual(default_affordance_traits("bed"), ["usable", "movable"])
        self.assertEqual(default_affordance_traits("book"), ["inscribable", "insertable", "movable"])
        self.assertEqual(default_affordance_traits("plant"), [])
        self.assertEqual(
            default_affordance_traits("table", portable=True, capacity=3),
            ["container", "inscribable", "movable"],
        )
        self.assertEqual(default_affordance_traits("plant", portable=True), ["movable"])
        for kind in ("lamp", "dispenser", "cup", "cabinet", "table", "chair", "bed", "book", "plant"):
            traits = default_affordance_traits(kind, portable=True, capacity=4)
            self.assertEqual(len(traits), len(set(traits)))
            self.assertTrue(set(traits) <= set(AFFORDANCE_TRAITS))

    def test_old_v3_package_without_traits_is_accepted_and_defaulted(self):
        p, _ = validate_package(self.raw)
        for o in p["objects"]:
            self.assertIsInstance(o["affordance_traits"], list)
            self.assertEqual(
                o["affordance_traits"],
                default_affordance_traits(o["kind"], o["portable"], o["capacity"]),
            )
        roundtrip, _ = validate_package(p)
        self.assertEqual([o["affordance_traits"] for o in roundtrip["objects"]],
                         [o["affordance_traits"] for o in p["objects"]])

    def test_valid_traits_survive_export_and_projection_strips_them(self):
        raw = copy.deepcopy(self.raw)
        raw["objects"][0]["affordance_traits"] = ["inscribable", "usable"]
        p, _ = validate_package(raw)
        self.assertEqual(p["objects"][0]["affordance_traits"], ["inscribable", "usable"])
        exported = export_package(copy.deepcopy(p))
        self.assertEqual(exported["objects"][0]["affordance_traits"], ["inscribable", "usable"])
        downgraded = projection(p)
        self.assertTrue(all("affordance_traits" not in o for o in downgraded["objects"]))
        legacy_package, _ = legacy.validate_package(downgraded)
        self.assertTrue(legacy_package["objects"])

    def test_invalid_traits_and_unknown_fields_are_rejected(self):
        cases = (
            {"affordance_traits": ["flying"]},
            {"affordance_traits": ["usable", "usable"]},
            {"affordance_traits": "usable"},
            {"affordance_traits": [1, 2]},
            {"affordance_traits": ["usable"], "colour": "#ffffff"},
        )
        for change in cases:
            raw = copy.deepcopy(self.raw)
            raw["objects"][0].update(change)
            with self.assertRaises(WorldError):
                validate_package(raw)

    def test_none_traits_are_filled_with_defaults(self):
        raw = copy.deepcopy(self.raw)
        raw["objects"][0]["affordance_traits"] = None
        p, _ = validate_package(raw)
        o = p["objects"][0]
        self.assertEqual(
            o["affordance_traits"],
            default_affordance_traits(o["kind"], o["portable"], o["capacity"]),
        )

    def test_web_panel_has_eight_chinese_traits_and_neutral_copy(self):
        app = (BASE / "web/app.js").read_text(encoding="utf-8")
        html = (BASE / "web/index.html").read_text(encoding="utf-8")
        labels = ("可挪放", "可夹入或放进别处", "能装东西", "可留字批注",
                  "可开关调档", "会冷热潮湿", "日常可用", "可凑合拼放")
        for label in labels:
            self.assertIn(label, html)
        self.assertEqual(html.count('data-trait="'), 8)
        self.assertIn("data-trait", app)
        self.assertIn("affordance-template", app)
        self.assertIn("affordance_traits", app)
        self.assertIn("TA", html)
        for text in (app, html):
            self.assertNotIn("她", text)
            self.assertNotIn("千代", text)


if __name__ == "__main__":
    unittest.main()
