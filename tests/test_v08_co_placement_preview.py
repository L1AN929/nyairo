#!/usr/bin/env python3
"""v3.0 Ch.27 §四.1 -- zero-write co-placement preview.

Coverage:
* ``preview_placement_zero_write`` never creates ``world/v08_matter_space.json``
  and never touches bytes or mtimes, whether called directly or through the
  ``/api/phone/v08/living-traces`` view;
* a full cooler refuses a new child with ``can_place=False`` and a concrete
  Chinese ``blocking_reason_cn``;
* live target physics yield the right predictions (cold fridge = 6x preservation
  + chilling, paper near a water sink = smudge risk, non-food in a cup = the cup
  is occupied);
* a partner targeting the private drawer or a private object is a 404 that never
  leaks capacity or slots;
* custom WorldGen/World-Editor zones preview cleanly with their authored physics.

Run (WSL): ``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_co_placement_preview -v``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_universal_matter as matter  # noqa: E402
from components.phone import v08_living_traces_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (int(stat.st_size), int(stat.st_mtime_ns), hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


class CoPlacementPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-co-preview-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _act(self, payload: dict, *, now: str = T0) -> dict:
        return matter.compose_open_act(self.root, payload, now_iso=now)

    def _introduce(self, label: str, kind: str, target: str, **extra) -> str:
        step = {"op": "INTRODUCE_ITEM", "label_cn": label, "item_kind": kind,
                "target_parent_id": target}
        step.update(extra)
        receipt = self._act({"intent_cn": f"放下{label}", "steps": [step]})
        return receipt["step_results"][0]["object_id"]

    def _write_json(self, rel: str, doc: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    # -- 1 --------------------------------------------------------------
    def test_01_zero_write_direct_and_http(self) -> None:
        # Fresh root: never create the world directory or state file.
        fresh = self.root / "fresh"
        before_fresh = _snapshot(self.root)
        result = matter.preview_placement_zero_write(
            fresh, {"target_parent_id": "home_fridge", "item_kind": "drink"}, now_iso=T0,
        )
        self.assertFalse(result["mutated_state"])
        self.assertFalse(fresh.exists(), "a preview must never create a directory")
        self.assertFalse((fresh / "world" / "v08_matter_space.json").exists())
        self.assertEqual(_snapshot(self.root), before_fresh)

        # Existing state: bytes and mtimes must be byte-for-byte unchanged.
        self._introduce("生番茄", "ingredient", "kitchen_counter", portions=1)
        self.assertTrue((self.root / "world" / "v08_matter_space.json").is_file())
        before = _snapshot(self.root)

        matter.preview_placement_zero_write(
            self.root,
            {"target_parent_id": "home_fridge", "item_kind": "prepared_food", "shelf_life_hours": 6},
            now_iso=T0,
        )
        code, body = view.mutate_v08_living_traces_view(
            self.root,
            {"action": "preview_placement", "target_parent_id": "home_fridge",
             "item_kind": "prepared_food", "shelf_life_hours": 6},
            actor_side="subject", now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body.get("status"), "preview")
        self.assertFalse(body.get("mutated_state"))
        self.assertEqual(_snapshot(self.root), before, "preview must be 100% zero-write")

    # -- 2 --------------------------------------------------------------
    def test_02_full_cooler_blocks_with_chinese_reason(self) -> None:
        self._write_json("world/package/objects.json", {
            "objects": [
                {"object_id": "dock_cooler", "name": "码头小冷柜", "kind": "cooler",
                 "location_id": "home",
                 "matter_profile": {"ambient_zone": "cold", "capacity_slots": 2}},
            ],
        })
        self._introduce("第一盒", "prepared_food", "dock_cooler")
        self._introduce("第二盒", "prepared_food", "dock_cooler")

        result = matter.preview_placement_zero_write(
            self.root,
            {"target_parent_id": "dock_cooler", "item_kind": "prepared_food"},
            now_iso=T0,
        )
        self.assertFalse(result["can_place"])
        self.assertEqual(result["capacity_slots"], 2)
        self.assertEqual(result["slots_used_before"], 2)
        self.assertEqual(result["slots_after"], 2, "a refused placement adds no slot")
        self.assertIn("满", result["blocking_reason_cn"])
        self.assertIn("腾出", result["blocking_reason_cn"])

    # -- 3 --------------------------------------------------------------
    def test_03_live_physics_predictions(self) -> None:
        # Cold fridge: chilling + 6x preservation.
        cold = matter.preview_placement_zero_write(
            self.root,
            {"target_parent_id": "home_fridge", "item_kind": "prepared_food", "shelf_life_hours": 6},
            now_iso=T0,
        )
        self.assertEqual(cold["target_ambient_zone"], "cold")
        joined = "".join(cold["predicted_effects_cn"])
        self.assertIn("20 分钟", joined)
        self.assertIn("6 倍", joined)

        # Paper near the water sink: smudge risk.
        smudge = matter.preview_placement_zero_write(
            self.root,
            {"target_parent_id": "kitchen_sink", "item_kind": "paper", "label_cn": "便签"},
            now_iso=T0,
        )
        self.assertTrue(any("水汽" in line and "洇" in line for line in smudge["predicted_effects_cn"]))

        # Non-food in a cup: the cup is occupied until emptied.
        cup = matter.preview_placement_zero_write(
            self.root,
            {"target_parent_id": "ceramic_cup_01", "item_kind": "tool", "label_cn": "小刀"},
            now_iso=T0,
        )
        self.assertTrue(any("容器" in line and "挪出来" in line for line in cup["predicted_effects_cn"]))

    # -- 4 --------------------------------------------------------------
    def test_04_partner_private_target_is_404_without_leak(self) -> None:
        private_id = self._introduce("私藏小盒", "keepsake", "subject_private_drawer")
        before = _snapshot(self.root)

        for target in ("subject_private_drawer", private_id):
            code, body = view.mutate_v08_living_traces_view(
                self.root,
                {"action": "preview_placement", "target_parent_id": target,
                 "item_kind": "everyday"},
                actor_side="partner", now_iso=T0,
            )
            self.assertEqual(code, 404, body)
            self.assertEqual(body.get("reason_code"), "OBJECT_NOT_FOUND")
            blob = json.dumps(body, ensure_ascii=False)
            self.assertNotIn("capacity_slots", blob)
            self.assertNotIn("slots", blob)

        # Partner previewing a private object is likewise invisible.
        code_obj, body_obj = view.mutate_v08_living_traces_view(
            self.root,
            {"action": "preview_placement", "object_id": private_id,
             "target_parent_id": "home_desk"},
            actor_side="partner", now_iso=T0,
        )
        self.assertEqual(code_obj, 404, body_obj)
        self.assertEqual(body_obj.get("reason_code"), "OBJECT_NOT_FOUND")
        self.assertEqual(_snapshot(self.root), before, "a refused preview is still zero-write")

    # -- 5 --------------------------------------------------------------
    def test_05_custom_worldgen_zone_previews(self) -> None:
        self._write_json("world/editor_package.json", {
            "objects": [
                {"id": "attic_freezer", "name": "阁楼冰柜", "room_id": "home",
                 "matter_profile": {"ambient_zone": "cold", "capacity_slots": 4}},
                {"id": "attic_heater", "name": "阁楼小炉", "room_id": "home",
                 "matter_profile": {"ambient_zone": "heating", "capacity_slots": 2}},
            ],
        })
        before = _snapshot(self.root)

        cold = matter.preview_placement_zero_write(
            self.root, {"target_parent_id": "attic_freezer", "item_kind": "drink"}, now_iso=T0,
        )
        self.assertEqual(cold["target_ambient_zone"], "cold")
        self.assertEqual(cold["capacity_slots"], 4)
        self.assertEqual(cold["slots_used_before"], 0)
        self.assertEqual(cold["slots_after"], 1)
        self.assertTrue(cold["can_place"])

        hot = matter.preview_placement_zero_write(
            self.root, {"target_parent_id": "attic_heater", "item_kind": "ingredient"}, now_iso=T0,
        )
        self.assertEqual(hot["target_ambient_zone"], "heating")
        self.assertTrue(any("热源" in line for line in hot["predicted_effects_cn"]))

        self.assertEqual(_snapshot(self.root), before, "custom-zone preview stays zero-write")
        self.assertFalse(matter.state_path(self.root).exists(), "no state file for a pure preview")


if __name__ == "__main__":
    unittest.main(verbosity=2)
