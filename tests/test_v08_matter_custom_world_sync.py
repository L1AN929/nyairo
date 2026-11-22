#!/usr/bin/env python3
"""v0.8 universal matter: custom WorldGen / World-Editor world sync.

Coverage:
* a WorldGen package catalogue (``world/package/objects.json``) contributes
  custom zones (cold cooler, heating stove, water sink) and a portable custom
  ingredient with its exact ``matter_profile`` -- discovered by a 100% zero-write
  read (no file, no directory, no mtime change);
* a World-Editor package (``world/editor_package.json``) contributes custom
  ``matter_profile`` matter per room, and respects current-location separation;
* the first real write materialises a custom zone into state, a custom stove
  really heats (cooked/hot), a custom cold zone really chills, and six hours in
  the custom fridge ages a dish six times slower than the same dish at room
  temperature;
* a partner may drop a real item into a shared custom zone, an unknown zone is
  still a 404, and the modified source stays person-neutral and model-free.

Run (WSL): ``python -B -m unittest tests.test_v08_matter_custom_world_sync -v``.
"""

from __future__ import annotations

import hashlib
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

from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _at(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


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


class V08MatterCustomWorldSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-matter-custom-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _write_json(self, rel: str, doc: dict) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return path

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

    def _write_worldgen_package(self) -> None:
        self._write_json("world/package/objects.json", {
            "objects": [
                {
                    "object_id": "harbor_fridge",
                    "name": "海边小冷柜",
                    "kind": "cooler",
                    "location_id": "home",
                    "matter_profile": {"ambient_zone": "cold", "capacity_slots": 6},
                },
                {
                    "object_id": "camping_stove",
                    "name": "露营小灶",
                    "kind": "stove",
                    "location_id": "home",
                    "matter_profile": {"ambient_zone": "heating", "capacity_slots": 2},
                },
                {
                    "object_id": "garden_sink",
                    "name": "院角水槽",
                    "kind": "sink",
                    "location_id": "home",
                    "matter_profile": {"water_source": True},
                },
                {
                    "object_id": "fresh_lemon",
                    "name": "新鲜柠檬",
                    "kind": "ingredient",
                    "location_id": "home",
                    "matter_profile": {
                        "item_kind": "ingredient",
                        "shelf_life_hours": 24,
                        "portions": 2,
                    },
                },
            ],
        })

    # -- tests -----------------------------------------------------------

    def test_01_worldgen_custom_world_is_discovered_zero_write(self) -> None:
        self._write_worldgen_package()

        before = _snapshot(self.root)
        view = self._view()
        self.assertEqual(_snapshot(self.root), before, "custom-world discovery must stay zero-write")
        self.assertFalse(engine.state_path(self.root).exists())
        self.assertFalse((self.root / "world" / "v08_matter_space.json").exists())

        zones = {row["zone_id"]: row for row in view["zones"]}
        self.assertIn("harbor_fridge", zones)
        self.assertIn("camping_stove", zones)
        self.assertIn("garden_sink", zones)

        self.assertEqual(zones["harbor_fridge"]["ambient_zone"], "cold")
        self.assertEqual(zones["harbor_fridge"]["capacity_slots"], 6)
        self.assertEqual(zones["harbor_fridge"]["visibility"], "shared")
        self.assertEqual(zones["camping_stove"]["ambient_zone"], "heating")
        self.assertEqual(zones["camping_stove"]["capacity_slots"], 2)
        self.assertTrue(zones["garden_sink"]["water_source"])
        self.assertEqual(zones["garden_sink"]["capacity_slots"], 4)

        lemon = self._obj("fresh_lemon")
        self.assertEqual(lemon["item_kind"], "ingredient")
        self.assertEqual(lemon["parent_id"], "home_staging_corner")
        self.assertEqual(lemon["shelf_life_hours"], 24.0)
        self.assertEqual(lemon["portions_left"], 2)
        self.assertEqual(lemon["portions_total"], 2)
        # The default starter space is untouched by the overlay.
        for starter in ("home_desk", "home_fridge", "kitchen_stove"):
            self.assertIn(starter, zones)
        self.assertIn("ceramic_cup_01", {row["object_id"] for row in view["objects"]})

        partner = self._view(actor_side="partner")
        self.assertIn("harbor_fridge", {row["zone_id"] for row in partner["zones"]})
        self.assertIn("fresh_lemon", {row["object_id"] for row in partner["objects"]})

    def test_02_world_editor_custom_matter_respects_current_room(self) -> None:
        self._write_json("world/editor_package.json", {
            "format": "nyairo.world-package.v3",
            "rooms": [
                {"id": "nest", "name": "我的小屋", "kind": "room"},
                {"id": "attic", "name": "阁楼", "kind": "room"},
            ],
            "objects": [
                {
                    "id": "ice_cabinet",
                    "name": "小屋冷柜",
                    "kind": "appliance",
                    "room_id": "nest",
                    "matter_profile": {"ambient_zone": "cold", "capacity_slots": 5},
                },
                {
                    "id": "idea_notebook",
                    "name": "点子笔记本",
                    "kind": "notebook",
                    "room_id": "nest",
                    "matter_profile": {"item_kind": "paper", "paper_like": True},
                },
                {
                    "id": "attic_lantern",
                    "name": "阁楼旧灯",
                    "kind": "lamp",
                    "room_id": "attic",
                    "matter_profile": {"ambient_zone": "heating", "capacity_slots": 1},
                },
            ],
        })

        # At home only the home-named room's custom matter is real.
        home_zones = {row["zone_id"] for row in self._view()["zones"]}
        home_objects = {row["object_id"] for row in self._view()["objects"]}
        self.assertIn("ice_cabinet", home_zones)
        self.assertNotIn("attic_lantern", home_zones)
        self.assertIn("idea_notebook", home_objects)
        self.assertEqual(self._zone("ice_cabinet")["ambient_zone"], "cold")
        self.assertEqual(self._zone("ice_cabinet")["capacity_slots"], 5)
        notebook = self._obj("idea_notebook")
        self.assertEqual(notebook["item_kind"], "paper")
        self.assertTrue(notebook["paper_like"])

        # Standing in the attic swaps the visible room, not the whole world.
        self._write_json("world/world_runtime.json", {"current_location": "attic"})
        attic_zones = {row["zone_id"] for row in self._view()["zones"]}
        attic_objects = {row["object_id"] for row in self._view()["objects"]}
        self.assertIn("attic_lantern", attic_zones)
        self.assertEqual(self._zone("attic_lantern")["ambient_zone"], "heating")
        self.assertNotIn("ice_cabinet", attic_zones)
        self.assertNotIn("idea_notebook", attic_objects)

        # Back home: a partner may drop a real item into the shared custom zone,
        # may move shared matter there, and still gets 404 for an unknown place.
        self._write_json("world/world_runtime.json", {"current_location": "home"})
        brought = engine.partner_bring_item(
            self.root,
            {"label_cn": "一袋小柠檬", "item_kind": "ingredient", "target_parent_id": "ice_cabinet"},
            now_iso=T0,
        )
        self.assertEqual(brought["status"], "applied")
        moved = engine.partner_move_shared_object(
            self.root,
            {"object_id": "pen_01", "target_parent_id": "ice_cabinet"},
            now_iso=T0,
        )
        self.assertEqual(moved["status"], "applied")
        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["zones"]["ice_cabinet"]["ambient_zone"], "cold")
        self.assertTrue(state["zones"]["ice_cabinet"]["from_custom_world"])
        self.assertEqual(state["matter_objects"][brought["object_id"]]["parent_id"], "ice_cabinet")
        self.assertEqual(state["matter_objects"]["pen_01"]["parent_id"], "ice_cabinet")

        with self.assertRaises(engine.V08MatterError) as ctx:
            engine.partner_bring_item(
                self.root,
                {"label_cn": "放错地方", "item_kind": "everyday", "target_parent_id": "ghost_zone"},
                now_iso=T0,
            )
        self.assertEqual(ctx.exception.reason_code, "OBJECT_NOT_FOUND")

    def test_03_custom_stove_cooks_and_custom_fridge_chills_six_times_slower(self) -> None:
        self._write_worldgen_package()

        cooked = self._act({
            "intent_cn": "在水槽边洗手，再用露营小灶煮一壶热柠檬茶",
            "steps": [
                {"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "garden_sink"},
                {
                    "op": "TRANSFORM_CREATE",
                    "source_object_ids": ["fresh_lemon"],
                    "output_label_cn": "热柠檬茶",
                    "output_kind": "prepared_food",
                    "medium_object_id": "camping_stove",
                },
            ],
        })
        self.assertEqual(cooked["status"], "applied")
        self.assertEqual(cooked["step_results"][0]["target_kind"], "zone")
        tea_step = cooked["step_results"][1]
        self.assertTrue(tea_step["heated"])
        self.assertEqual(tea_step["cook_state"], "cooked")
        self.assertEqual(tea_step["thermal"], "hot")
        tea_id = tea_step["output_object_id"]

        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["zones"]["camping_stove"]["ambient_zone"], "heating")
        self.assertTrue(state["zones"]["camping_stove"]["from_custom_world"])
        self.assertEqual(state["matter_objects"]["fresh_lemon"]["portions_left"], 1)

        moved = self._act({
            "intent_cn": "把热柠檬茶收进海边小冷柜",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": tea_id, "target_parent_id": "harbor_fridge"}],
        })
        self.assertEqual(moved["status"], "applied")
        self.assertEqual(moved["step_results"][0]["target_parent_id"], "harbor_fridge")
        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["zones"]["harbor_fridge"]["ambient_zone"], "cold")
        self.assertTrue(state["zones"]["harbor_fridge"]["from_custom_world"])
        self.assertEqual(state["matter_objects"][tea_id]["parent_id"], "harbor_fridge")

        # A twin dish lands on the room-temperature counter as the control.
        control = self._act({
            "intent_cn": "再煮一壶放在备餐台上作对照",
            "steps": [{
                "op": "TRANSFORM_CREATE",
                "source_object_ids": ["fresh_lemon"],
                "output_label_cn": "热柠檬茶（桌上对照）",
                "output_kind": "prepared_food",
                "medium_object_id": "camping_stove",
            }],
        })
        self.assertEqual(control["status"], "applied")
        control_id = control["step_results"][0]["output_object_id"]
        self._act({
            "intent_cn": "把对照那壶放到备餐台",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": control_id, "target_parent_id": "kitchen_counter"}],
        })
        state = engine._read_state_zero_write(self.root)
        self.assertTrue(state["matter_objects"]["fresh_lemon"]["consumed"])

        counts = engine.settle_matter_physics(self.root, now_iso=_at(360))
        self.assertGreaterEqual(counts["chilled"], 1)
        state = engine._read_state_zero_write(self.root)
        chilled = state["matter_objects"][tea_id]
        room = state["matter_objects"][control_id]
        self.assertEqual(chilled["thermal"], "cold")
        self.assertEqual(room["thermal"], "room")
        self.assertAlmostEqual(float(chilled["effective_exposure_hours"]), 1.0, places=3)
        self.assertAlmostEqual(float(room["effective_exposure_hours"]), 6.0, places=3)
        self.assertAlmostEqual(
            float(room["effective_exposure_hours"]) / float(chilled["effective_exposure_hours"]),
            6.0,
            places=3,
        )
        later = self._view(now=_at(360))
        self.assertIn("harbor_fridge", {row["zone_id"] for row in later["zones"]})
        final = engine._read_state_zero_write(self.root)
        self.assertEqual(final["matter_objects"]["fresh_lemon"]["portions_left"], 0)

    def test_04_modified_source_is_person_neutral_and_model_free(self) -> None:
        source = Path(engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("她", source)
        self.assertNotIn("千代", source)
        receipt = self._act({
            "intent_cn": "把一支笔放到暂放角",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_staging_corner"}],
        })
        self.assertEqual(receipt["llm_calls"], 0)

    def test_05_materialized_custom_object_anchors_thermal_and_freshness_clocks(self) -> None:
        # A custom cold zone plus a custom perishable that the engine has never
        # seen before.  The first real reference materialises both; the move
        # must anchor the item's clocks in the fridge, not leave them ``None``.
        self._write_json("world/package/objects.json", {
            "objects": [
                {
                    "object_id": "seaside_cooler",
                    "name": "海风小冰柜",
                    "kind": "cooler",
                    "location_id": "home",
                    "matter_profile": {"ambient_zone": "cold", "capacity_slots": 6},
                },
                {
                    "object_id": "fresh_fig_basket",
                    "name": "刚摘的无花果",
                    "kind": "ingredient",
                    "location_id": "home",
                    "matter_profile": {
                        "item_kind": "ingredient",
                        "shelf_life_hours": 12.0,
                        "portions": 2,
                    },
                },
            ],
        })

        ten = _at(60)          # 10:00, the move lands in the cold zone
        sixteen = _at(60 + 360)  # 16:00, six hours later
        moved = self._act({
            "intent_cn": "把刚摘的无花果放进海风小冰柜保鲜",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "fresh_fig_basket", "target_parent_id": "seaside_cooler"}],
        }, now=ten)
        self.assertEqual(moved["status"], "applied")
        self.assertEqual(moved["step_results"][0]["target_parent_id"], "seaside_cooler")

        state = engine._read_state_zero_write(self.root)
        live = state["matter_objects"]["fresh_fig_basket"]
        self.assertEqual(live["last_thermal_at"], ten)
        self.assertEqual(live["freshness_anchor_at"], ten)
        self.assertEqual(live["freshness_settled_at"], ten)

        fig = self._obj("fresh_fig_basket", now=sixteen)
        self.assertEqual(fig["thermal"], "cold")
        # 6h at the 1/6 fridge rate = 1h exposure out of a 12h shelf life.
        self.assertEqual(fig["freshness"], "fresh")


if __name__ == "__main__":
    unittest.main()
