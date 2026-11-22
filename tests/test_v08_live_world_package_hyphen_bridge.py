#!/usr/bin/env python3
"""v0.8 live ``world-package`` (hyphen) bridge and ``scene.json`` placement sync.

Real desktop/daemon instances (``v05.go`` / ``v07_extra.go``) store the switched
/ generated / edited active world package at ``<root>/world-package/`` (HYPHEN),
next to an optional sibling ``<root>/world-package/scene.json``.

Coverage:
* both shipped discovery modules pick objects up from the hyphen path, so a
  generated town or a World-Editor save becomes reachable with no backend change;
* ``discover_reachable_objects`` still keeps all seven fixed home anchors;
* every read path (object discovery, the matter view, the co-placement preview)
  is 100% zero-write -- a recursive (rel_path, size, mtime_ns) snapshot is
  identical before and after;
* a ``scene.json`` placement supplies ``portable`` / ``capacity`` only as a
  fallback (an explicit ``objects.json`` or ``matter_profile`` value wins);
* ``compose_open_act`` really cooks on the custom heater and really chills in the
  custom cold zone -- six hours in the icebox ages a dish six times slower than
  the same dish on the room-temperature counter;
* the modified sources stay person-neutral and model-free.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_live_world_package_hyphen_bridge -v``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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

from components.world.scripts import v08_open_affordance as affordance  # noqa: E402
from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"

BASE_HOME_OBJECT_IDS = (
    "home_window",
    "home_desk",
    "home_bed",
    "home_door_sign",
    "home_shelf",
    "courtyard_rack",
    "indoor_plant",
)


def _at(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Recursive (rel_path -> (size, mtime_ns)) snapshot; directories included."""

    snap: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (int(stat.st_size), int(stat.st_mtime_ns))
        else:
            snap[rel] = (0, int(stat.st_mtime_ns))
    return snap


class V08LiveWorldPackageHyphenBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-live-world-package-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- fixtures --------------------------------------------------------

    def _write_json(self, rel: str, doc: dict) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return path

    def _write_world_package(self) -> None:
        """A coastal/bakery town authored the way the desktop daemon writes it."""

        self._write_json("world-package/objects.json", {
            "world_id": "harbor_bakery_v1",
            "objects": [
                {
                    "object_id": "harbor_icebox",
                    "name": "港湾冰柜",
                    "kind": "cooler",
                    "location_id": "home",
                    "note": "海边小屋里的旧冰柜",
                    # Capacity deliberately lives in scene.json, not here.
                    "matter_profile": {"ambient_zone": "cold"},
                },
                {
                    "object_id": "tea_heater",
                    "name": "煮茶小灶",
                    "kind": "stove",
                    "location_id": "home",
                    "note": "灶上一直温着水",
                    "matter_profile": {"ambient_zone": "heating", "capacity_slots": 2},
                },
                {
                    "object_id": "sea_salt_biscuit",
                    "name": "海盐小饼",
                    "kind": "prepared_food",
                    "location_id": "home",
                    "matter_profile": {
                        "item_kind": "prepared_food",
                        "shelf_life_hours": 36,
                        "portions": 3,
                    },
                },
                {
                    "object_id": "dock_lantern",
                    "name": "码头旧灯",
                    "kind": "lamp",
                    "location_id": "home",
                    # Explicit matter_profile capacity must beat the scene value.
                    "matter_profile": {"ambient_zone": "heating", "capacity_slots": 1},
                },
            ],
        })
        self._write_json("world-package/scene.json", {
            "schema_version": "nyairo.worldgen.scene.v1",
            "spaces": {},
            "assets": {},
            "placements": {
                "harbor_icebox": {
                    "kind": "cooler",
                    "x": 2,
                    "y": 3,
                    "visual": "cooler_a",
                    "actions": ["open"],
                    "behavior": "static",
                    "portable": False,
                    "capacity": 6,
                    "servings": 0,
                    "initial_status": "closed",
                    "interaction": "container",
                    "asset_id": None,
                },
                "sea_salt_biscuit": {
                    "kind": "prepared_food",
                    "x": 1,
                    "y": 1,
                    "visual": "biscuit_a",
                    "actions": ["eat"],
                    "behavior": "static",
                    "portable": True,
                    "capacity": 0,
                    "servings": 3,
                    "initial_status": "fresh",
                    "interaction": "pick",
                    "asset_id": None,
                },
                "dock_lantern": {
                    "kind": "lamp",
                    "x": 4,
                    "y": 2,
                    "visual": "lantern_a",
                    "actions": ["toggle"],
                    "behavior": "static",
                    "portable": True,
                    "capacity": 9,
                    "servings": 0,
                    "initial_status": "off",
                    "interaction": "switch",
                    "asset_id": None,
                },
            },
        })

    def _view(self, *, actor_side: str = "subject", now: str = T0) -> dict:
        return engine.read_matter_space_view(self.root, actor_side=actor_side, now_iso=now)

    def _zone(self, zone_id: str, *, now: str = T0) -> dict:
        for row in self._view(now=now)["zones"]:
            if row["zone_id"] == zone_id:
                return row
        raise AssertionError(f"zone not in view: {zone_id}")

    def _obj(self, object_id: str, *, now: str = T0) -> dict:
        for row in self._view(now=now)["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in view: {object_id}")

    def _act(self, payload: dict, *, now: str = T0) -> dict:
        return engine.compose_open_act(self.root, payload, now_iso=now)

    # -- tests -----------------------------------------------------------

    def test_01_hyphen_world_package_is_discovered_and_keeps_base_home(self) -> None:
        self._write_world_package()

        before = _snapshot(self.root)
        objects = affordance.discover_reachable_objects(self.root)
        self.assertEqual(_snapshot(self.root), before, "object discovery must stay zero-write")

        ids = {row["object_id"] for row in objects}
        for authored in ("harbor_icebox", "tea_heater", "sea_salt_biscuit", "dock_lantern"):
            self.assertIn(authored, ids)
        for anchor in BASE_HOME_OBJECT_IDS:
            self.assertIn(anchor, ids, "the seven fixed home anchors must survive")
        self.assertEqual(len(BASE_HOME_OBJECT_IDS), 7)

        by_id = {row["object_id"]: row for row in objects}
        self.assertEqual(by_id["harbor_icebox"]["source"], "world_package")
        self.assertIn("container", by_id["harbor_icebox"]["traits"])

        # The whole read projection is zero-write too.
        projected = affordance.read_living_traces_view(self.root, viewer="subject")
        self.assertEqual(len(BASE_HOME_OBJECT_IDS), 7)
        self.assertTrue(any(row["object_id"] == "harbor_icebox" for row in projected["reachable_objects"]))
        self.assertEqual(_snapshot(self.root), before)

    def test_02_scene_placement_is_only_a_fallback(self) -> None:
        self._write_world_package()

        rows = {row["object_id"]: row for row in engine._custom_world_rows(self.root)}
        self.assertIn("harbor_icebox", rows)
        # Capacity came from scene.json because objects.json carried none.
        self.assertEqual(rows["harbor_icebox"]["matter_profile"]["capacity_slots"], 6)
        # Portable came from scene.json because objects.json carried none.
        self.assertTrue(rows["sea_salt_biscuit"]["portable"])
        # An explicit matter_profile capacity wins over the scene value (9).
        self.assertEqual(rows["dock_lantern"]["matter_profile"]["capacity_slots"], 1)

        zones = {row["zone_id"]: row for row in self._view()["zones"]}
        self.assertEqual(zones["harbor_icebox"]["ambient_zone"], "cold")
        self.assertEqual(zones["harbor_icebox"]["capacity_slots"], 6)
        self.assertEqual(zones["tea_heater"]["ambient_zone"], "heating")
        biscuit = self._obj("sea_salt_biscuit")
        self.assertEqual(biscuit["item_kind"], "prepared_food")
        self.assertEqual(biscuit["shelf_life_hours"], 36.0)

        # Starter zones and starter objects are untouched by the overlay.
        for starter_zone in ("home_desk", "home_fridge", "kitchen_stove"):
            self.assertIn(starter_zone, zones)
        self.assertIn("ceramic_cup_01", {row["object_id"] for row in self._view()["objects"]})

    def test_03_matter_view_and_preview_are_zero_write(self) -> None:
        self._write_world_package()
        self.assertFalse((self.root / "world" / "v08_matter_space.json").exists())

        before = _snapshot(self.root)
        view = self._view()
        self.assertIn("harbor_icebox", {row["zone_id"] for row in view["zones"]})
        preview = engine.preview_placement_zero_write(
            self.root,
            {"target_parent_id": "harbor_icebox", "item_kind": "prepared_food"},
            now_iso=T0,
        )
        self.assertFalse(preview["mutated_state"])
        self.assertTrue(preview["can_place"])
        self.assertEqual(preview["capacity_slots"], 6)
        self.assertEqual(_snapshot(self.root), before, "reads and preview must never touch bytes or mtimes")
        self.assertFalse((self.root / "world" / "v08_matter_space.json").exists())

    def test_04_cooks_on_custom_heater_and_chills_six_times_slower(self) -> None:
        self._write_world_package()

        cooked = self._act({
            "intent_cn": "用煮茶小灶做一壶热汤",
            "steps": [
                {
                    "op": "INTRODUCE_ITEM",
                    "object_id": "coastal_kelp",
                    "label_cn": "晒干的海带",
                    "item_kind": "ingredient",
                    "target_parent_id": "kitchen_counter",
                    "portions": 4,
                    "shelf_life_hours": 48,
                },
                {
                    "op": "TRANSFORM_CREATE",
                    "source_object_ids": ["coastal_kelp"],
                    "output_label_cn": "热海带汤",
                    "output_kind": "prepared_food",
                    "medium_object_id": "tea_heater",
                },
            ],
        })
        self.assertEqual(cooked["status"], "applied")
        soup_step = cooked["step_results"][1]
        self.assertTrue(soup_step["heated"])
        self.assertEqual(soup_step["thermal"], "hot")
        soup_id = soup_step["output_object_id"]

        moved = self._act({
            "intent_cn": "把热海带汤收进港湾冰柜",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": soup_id, "target_parent_id": "harbor_icebox"}],
        })
        self.assertEqual(moved["status"], "applied")
        self.assertEqual(moved["step_results"][0]["target_parent_id"], "harbor_icebox")

        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["zones"]["harbor_icebox"]["ambient_zone"], "cold")
        self.assertTrue(state["zones"]["harbor_icebox"]["from_custom_world"])
        self.assertEqual(state["matter_objects"][soup_id]["parent_id"], "harbor_icebox")

        # A twin dish stays on the room-temperature counter as the control.
        control = self._act({
            "intent_cn": "再煮一壶留在备餐台上作对照",
            "steps": [{
                "op": "TRANSFORM_CREATE",
                "source_object_ids": ["coastal_kelp"],
                "output_label_cn": "热海带汤（桌上对照）",
                "output_kind": "prepared_food",
                "medium_object_id": "tea_heater",
            }],
        })
        self.assertEqual(control["status"], "applied")
        control_id = control["step_results"][0]["output_object_id"]

        counts = engine.settle_matter_physics(self.root, now_iso=_at(360))
        self.assertGreaterEqual(counts["chilled"], 1)
        final = engine._read_state_zero_write(self.root)
        chilled = final["matter_objects"][soup_id]
        room = final["matter_objects"][control_id]
        self.assertEqual(chilled["thermal"], "cold")
        self.assertEqual(room["thermal"], "room")
        self.assertAlmostEqual(float(chilled["effective_exposure_hours"]), 1.0, places=3)
        self.assertAlmostEqual(float(room["effective_exposure_hours"]), 6.0, places=3)
        self.assertAlmostEqual(
            float(room["effective_exposure_hours"]) / float(chilled["effective_exposure_hours"]),
            6.0,
            places=3,
        )
        zone_ids = {row["zone_id"] for row in self._view(now=_at(360))["zones"]}
        self.assertIn("harbor_icebox", zone_ids)

    def test_05_modified_sources_are_person_neutral_and_model_free(self) -> None:
        self._write_world_package()
        for module in (affordance, engine):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("她", source)
            self.assertNotIn("千代", source)
        receipt = self._act({
            "intent_cn": "把海盐小饼放到暂放角",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "sea_salt_biscuit", "target_parent_id": "home_staging_corner"}],
        })
        self.assertEqual(receipt["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()
