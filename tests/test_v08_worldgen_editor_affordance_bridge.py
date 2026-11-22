#!/usr/bin/env python3
"""v0.8 WorldGen / world-editor straight-through affordance bridge.

Coverage:
* ``discover_reachable_objects`` reads a real WorldGen ``objects.json`` and a
  world-editor ``nyairo-world.json`` with 100% zero writes, honouring explicit
  ``affordance_traits`` and inferring traits from kind/name/portability/capacity;
* a custom object authored outside the backend is immediately usable through
  every existing contract: six-verb execution (TOGGLE_ADJUST -> INSCRIBE), a
  living-trace sticker, a saved personal routine that runs, and the partner
  projection still strips private rows;
* the real current place moves the reachable set: in a cafe the cafe corkboard is
  present and the home bed is not; back home the home objects return;
* the ``focused`` cue is claimed only while a real focus signal exists (an
  unsettled workbench tray), and a quiet home never claims it.  All product paths
  keep ``llm_calls == 0`` and person-neutral copy.

Run (WSL): ``python -m unittest tests.test_v08_worldgen_editor_affordance_bridge -v``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_open_affordance as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_worldgen_objects(root: Path) -> None:
    _write_json(
        root / "world_package" / "objects.json",
        {
            "schema_version": "world.objects.v1",
            "objects": [
                {
                    "object_id": "custom_vinyl_player",
                    "name": "旧唱机",
                    "kind": "dispenser",
                    "location_id": "home",
                    "note": "桌角那台能放黑胶的旧唱机。",
                    "affordance_traits": ["switchable", "usable", "inscribable"],
                },
                {
                    "object_id": "cafe_corkboard",
                    "name": "咖啡馆软木留言板",
                    "kind": "table",
                    "location_id": "cafe",
                    "note": "进门左手边，插着几枚图钉。",
                    "affordance_traits": ["inscribable", "container"],
                },
            ],
        },
    )


def _write_editor_world(root: Path) -> None:
    _write_json(
        root / "world" / "nyairo-world.json",
        {
            "format": "nyairo.world-package.v3",
            "requires": "nyairo.editor-runtime.v3",
            "meta": {
                "id": "custom-home",
                "name": "自己画的小家",
                "author": "resident",
                "version": "0.4.0",
                "description": "",
                "license": "CC0-1.0",
                "cover": "generated-floorplan",
            },
            "rooms": [
                {"id": "home", "name": "家 · 小屋", "description": "", "kind": "room"},
            ],
            "connections": [],
            "objects": [
                {
                    "id": "editor_wall_loom",
                    "name": "挂毯织机",
                    "description": "墙角自己搭起来的小织机。",
                    "kind": "cabinet",
                    "room_id": "home",
                    "portable": False,
                    "capacity": 2,
                }
            ],
            "initial": {"room_id": "home", "time": "2026-11-16T09:00", "resident_name": "住户"},
            "scene": {},
            "assets": {},
        },
    )


def _ids(root: Path) -> set[str]:
    return {row["object_id"] for row in engine.discover_reachable_objects(root)}


def _by_id(root: Path, object_id: str) -> dict[str, Any]:
    for row in engine.discover_reachable_objects(root):
        if row["object_id"] == object_id:
            return row
    raise AssertionError(f"object {object_id} is not reachable")


class V08WorldBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-world-bridge-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()
        _write_worldgen_objects(self.root)
        _write_editor_world(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- scenario 1 ----------------------------------------------------------
    def test_01_worldgen_and_editor_objects_are_discovered_zero_write(self) -> None:
        before = _snapshot(self.root)
        objects = engine.discover_reachable_objects(self.root)
        self.assertEqual(before, _snapshot(self.root), "discovery must not write a byte or mtime")
        self.assertEqual(engine.current_location_id(self.root), "home")

        ids = {row["object_id"] for row in objects}
        for anchor in (
            "home_window", "home_desk", "home_bed", "home_door_sign",
            "home_shelf", "courtyard_rack", "indoor_plant",
        ):
            self.assertIn(anchor, ids, "home anchors must stay exactly as before")

        vinyl = _by_id(self.root, "custom_vinyl_player")
        self.assertEqual(vinyl["label_cn"], "旧唱机")
        self.assertEqual(vinyl["source"], "world_package")
        self.assertEqual(vinyl["traits"], ["switchable", "usable", "inscribable"])
        self.assertFalse(vinyl["is_outdoor"])

        loom = _by_id(self.root, "editor_wall_loom")
        self.assertEqual(loom["source"], "world_editor")
        self.assertEqual(loom["location_hint"], "家 · 小屋")
        self.assertIn("container", loom["traits"])
        self.assertIn("insertable", loom["traits"])
        self.assertFalse(loom["is_outdoor"])

        # A cafe catalogue row is not in front of the resident while at home.
        self.assertNotIn("cafe_corkboard", ids)

        view = engine.read_living_traces_view(self.root, viewer="subject", now_iso=T0)
        self.assertEqual(view["llm_calls"], 0)

    # -- scenario 2 ----------------------------------------------------------
    def test_02_custom_object_supports_every_existing_contract(self) -> None:
        object_id = "custom_vinyl_player"

        toggled = engine.execute_universal_verb(
            self.root,
            {"verb": "TOGGLE_ADJUST", "object_id": object_id, "setting_value": "on"},
            now_iso=T0,
        )
        self.assertEqual(toggled["status"], "applied")
        self.assertEqual(toggled["llm_calls"], 0)

        inscribed = engine.execute_universal_verb(
            self.root,
            {"verb": "INSCRIBE", "object_id": object_id, "note_text": "A 面第三首"},
            now_iso=T0,
        )
        self.assertEqual(inscribed["inscription"]["note_text"], "A 面第三首")

        private_trace = engine.apply_living_trace(
            self.root,
            {
                "object_id": object_id,
                "gesture_cn": "放下唱针",
                "state_sticker_cn": "黑胶慢慢转起来",
                "visibility": "private",
            },
            now_iso=T0,
        )
        self.assertEqual(private_trace["visibility"], "private")

        routine = engine.save_personal_routine(
            self.root,
            {
                "title_cn": "桌角放一张旧唱片",
                "when_cue": "focused",
                "steps": [
                    {"kind": "verb", "object_id": object_id, "verb": "TOGGLE_ADJUST", "setting_value": "on"},
                    {"kind": "trace", "object_id": object_id, "gesture_cn": "放下唱针", "state_sticker_cn": "唱针落进音轨"},
                ],
            },
            now_iso=T0,
        )
        self.assertEqual(routine["step_count"], 2)
        ran = engine.run_personal_routine(self.root, {"routine_id": routine["routine_id"]}, now_iso=T0)
        self.assertEqual(ran["status"], "applied")
        self.assertEqual(ran["executed_count"], 2)
        self.assertEqual(ran["llm_calls"], 0)

        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["object_settings"][object_id]["setting_value"], "on")
        self.assertIn(object_id, state["inscriptions_by_object"])
        self.assertTrue(engine.state_path(self.root).is_file())

        subject = engine.read_living_traces_view(self.root, viewer="subject", now_iso=T0)
        partner = engine.read_living_traces_view(self.root, viewer="partner", now_iso=T0)
        self.assertTrue(any(row["object_id"] == object_id for row in subject["active_traces"]))
        self.assertTrue(all(row["visibility"] == "shared" for row in partner["active_traces"]))
        self.assertNotIn("黑胶慢慢转起来", json.dumps(partner, ensure_ascii=False))
        self.assertEqual(partner["llm_calls"], 0)

    # -- scenario 3 ----------------------------------------------------------
    def test_03_real_location_switches_the_reachable_space(self) -> None:
        _write_json(
            self.root / "world" / "world_runtime.json",
            {"schema": "world.runtime.v1", "current_location_id": "cafe"},
        )
        cafe_ids = _ids(self.root)
        self.assertIn("cafe_corkboard", cafe_ids)
        self.assertNotIn("home_bed", cafe_ids, "the home bed is not in front of a resident in a cafe")
        self.assertNotIn("courtyard_rack", cafe_ids)
        self.assertNotIn("custom_vinyl_player", cafe_ids)

        corkboard = _by_id(self.root, "cafe_corkboard")
        self.assertEqual(corkboard["traits"], ["inscribable", "container"])
        self.assertEqual(corkboard["location_hint"], "进门左手边，插着几枚图钉。")

        # The custom cafe object is really usable, with no new backend code.
        engine.execute_universal_verb(
            self.root,
            {"verb": "INSCRIBE", "object_id": "cafe_corkboard", "note_text": "午后在这里坐一会儿"},
            now_iso=T0,
        )

        _write_json(self.root / "world" / "world_runtime.json", {"current_location_id": "home"})
        home_ids = _ids(self.root)
        self.assertIn("home_bed", home_ids)
        self.assertIn("custom_vinyl_player", home_ids)
        self.assertNotIn("cafe_corkboard", home_ids)

    # -- scenario 4 ----------------------------------------------------------
    def test_04_focused_cue_requires_a_real_focus_signal(self) -> None:
        focused = engine.save_personal_routine(
            self.root,
            {
                "title_cn": "工作台前收一小段",
                "when_cue": "focused",
                "steps": [
                    {"kind": "verb", "object_id": "home_desk", "verb": "INSCRIBE", "note_text": "先写三行"},
                    {"kind": "trace", "object_id": "home_desk", "gesture_cn": "把纸摊平", "state_sticker_cn": "桌面空出一块"},
                ],
            },
            now_iso=T0,
        )
        morning = engine.save_personal_routine(
            self.root,
            {
                "title_cn": "早起开一开窗",
                "when_cue": "morning",
                "steps": [
                    {"kind": "verb", "object_id": "home_window", "verb": "TOGGLE_ADJUST", "setting_value": "ajar"},
                    {"kind": "trace", "object_id": "home_window", "gesture_cn": "推开窗", "state_sticker_cn": "窗缝有风"},
                ],
            },
            now_iso=T0,
        )

        self.assertFalse(engine.focused_state_zero_write(self.root))
        quiet_cues = engine.context_cues_zero_write(self.root, now_iso=T0)
        self.assertNotIn("focused", quiet_cues)
        self.assertIn("morning", quiet_cues)
        quiet = engine.match_routines_for_context(self.root, now_iso=T0)
        self.assertEqual([row["routine_id"] for row in quiet], [morning["routine_id"]])

        _write_json(
            self.root / "phone" / "workbench_tray.json",
            {
                "schema": "nyairo.workbench_tray.v1",
                "revision": 1,
                "tray": {
                    "images": [],
                    "materials": [{"artifact_id": "mat_linen", "baseline_source_revision": 1}],
                    "work": {"artifact_id": "work_letter", "baseline_version_id": "v1", "baseline_version_number": 1},
                    "note": None,
                },
            },
        )
        self.assertTrue(engine.focused_state_zero_write(self.root))
        busy_cues = engine.context_cues_zero_write(self.root, now_iso=T0)
        self.assertIn("focused", busy_cues)
        matched = engine.match_routines_for_context(self.root, now_iso=T0)
        self.assertEqual(
            [row["routine_id"] for row in matched],
            [focused["routine_id"], morning["routine_id"]],
        )

        # The exact same state can be forced by a caller that already knows.
        forced = engine.match_routines_for_context(self.root, cues={"any"})
        self.assertEqual(len(forced), 0)
        only_focused = engine.match_routines_for_context(self.root, cues={"focused"})
        self.assertEqual([row["routine_id"] for row in only_focused], [focused["routine_id"]])

        view = engine.read_living_traces_view(self.root, viewer="subject", now_iso=T0)
        self.assertEqual(view["llm_calls"], 0)

    def test_05_person_neutral_copy(self) -> None:
        text = (ROOT / "components" / "world" / "scripts" / "v08_open_affordance.py").read_text(
            encoding="utf-8"
        )
        for token in ("她", "千代"):
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
