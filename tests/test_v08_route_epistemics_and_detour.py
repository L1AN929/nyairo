#!/usr/bin/env python3
"""v0.8 Wave F2: route closure epistemics, sheltered detour & v08 bag bridge.

Covers ``CHIYO-总体设计-v0.4`` Ch.39 §三–§六 over the real owners:

* ``v04_neighborhood_living`` journey state (roadblocks, reroute, turn_back),
* ``v04_neighborhood_view`` honest mid-route projection,
* strict read-only recognition of portable matter owned by ``v08_universal_matter``
  (``<root>/world/v08_matter_space.json`` is never written by this owner).

Run:  python3 tests/test_v08_route_epistemics_and_detour.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(_WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as engine  # noqa: E402

T_ROADBLOCK = "2026-11-15T09:45:00+08:00"
T_DEPART = "2026-11-15T10:00:00+08:00"
T_AT_YARD = "2026-11-15T10:02:00+08:00"


def _tree_snapshot(root: Path) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rows.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return rows


def _matter_fixture(objects: dict[str, dict]) -> dict:
    return {"schema_version": 1, "revision": 3, "matter_objects": objects}


def _bag_row(**overrides) -> dict:
    row = {
        "object_id": "canvas_bag",
        "label_cn": "米白帆布包",
        "item_kind": "carrier",
        "parent_id": "home_desk",
        "consumed": False,
        "discarded": False,
        "returned_out": False,
        "subject_last_seen_parent": "home_desk",
        "subject_last_seen_snapshot": {"parent_id": "home_desk"},
        "portability": "portable",
    }
    row.update(overrides)
    return row


class TestRouteEpistemicsAndDetour(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdirs: list[tempfile.TemporaryDirectory] = []
        self.day_root = self._new_root()

    def tearDown(self) -> None:
        for tmp in self._tmpdirs:
            tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def _new_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory(prefix="v08-route-detour-")
        self._tmpdirs.append(tmp)
        return Path(tmp.name)

    def _write_v08(self, root: Path, objects: dict[str, dict]) -> Path:
        world = root / "world"
        world.mkdir(parents=True, exist_ok=True)
        path = world / "v08_matter_space.json"
        path.write_text(json.dumps(_matter_fixture(objects), ensure_ascii=False), encoding="utf-8")
        return path

    def _mutate(self, payload: dict, *, actor: str = "subject", now_iso: str, root: Path | None = None):
        return mutate_v04_neighborhood_view(
            root or self.day_root, payload, actor_side=actor, now_iso=now_iso
        )

    def _read(self, *, now_iso: str, subject: bool = True, root: Path | None = None):
        return read_v04_neighborhood_view(
            root or self.day_root, has_subject_capability=subject, now_iso=now_iso
        )

    def _block(self, route_id: str, *, root: Path | None = None, now_iso: str = T_ROADBLOCK) -> None:
        code, body = self._mutate(
            {"action": "set_roadblock", "route_id": route_id, "blocked": True, "reason": "临时围栏施工"},
            actor="partner",
            now_iso=now_iso,
            root=root,
        )
        self.assertEqual(code, 200, body)

    def _depart_to_bookstore(self, *, root: Path | None = None, now_iso: str = T_DEPART):
        return self._mutate(
            {"action": "start_journey", "target_place": "bookstore"},
            now_iso=now_iso,
            root=root,
        )

    def _pause_at_yard(self, *, root: Path | None = None):
        code, body = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            now_iso=T_AT_YARD,
            root=root,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["journey"]["state"], "paused_at_node")
        self.assertEqual(body["journey"]["current_place_id"], "yard")
        return body

    # ------------------------------------------------------------------- tests
    def test_01_roadblock_hidden_indoors_then_honestly_discovered_at_yard(self) -> None:
        self._block("open_shortcut")

        # Subject is indoors at home: no omniscient leak of the partner's closure.
        _, indoors = self._read(now_iso="2026-11-15T09:50:00+08:00")
        self.assertEqual(indoors["journey"]["discovered_roadblocks"], [])
        self.assertEqual(indoors["roadblocks"], [])
        self.assertEqual(indoors["journey"]["transit_location_cn"], "在家")

        code, departed = self._depart_to_bookstore()
        self.assertEqual(code, 200, departed)
        self.assertEqual(departed["journey"]["state"], "in_transit")
        self.assertIsNone(departed["journey"]["current_place_id"])
        self.assertEqual(departed["journey"]["active_route_id"], "home_to_porch")
        self.assertTrue(departed["journey"]["active_route_sheltered"])
        self.assertEqual(
            departed["journey"]["transit_location_cn"], "在从家前往门廊的路上（有檐长廊）"
        )

        halted = self._pause_at_yard()
        self.assertEqual(halted["journey"]["halt_reason"], "ROADBLOCK_ENCOUNTERED")
        self.assertIn("open_shortcut", halted["journey"]["discovered_roadblocks"])
        self.assertFalse(halted["journey"]["active_route_sheltered"])
        self.assertEqual(
            halted["journey"]["transit_location_cn"], "停在小院路口（发现前方临时围栏）"
        )
        self.assertEqual(halted["journey"]["accumulated_walk_seconds"], 120)
        # Only now does the subject's own view carry the discovered closure.
        self.assertEqual([r["route_id"] for r in halted["roadblocks"]], ["open_shortcut"])

    def test_02_reroute_detours_sheltered_eaves_and_arrives_honestly(self) -> None:
        self._block("open_shortcut")
        code, _ = self._depart_to_bookstore()
        self.assertEqual(code, 200)
        paused = self._pause_at_yard()
        self.assertEqual(paused["journey"]["accumulated_walk_seconds"], 120)

        code, rerouted = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "reroute"},
            now_iso="2026-11-15T10:05:00+08:00",
        )
        self.assertEqual(code, 200, rerouted)
        self.assertEqual(rerouted["result"]["outcome"], "rerouted")
        journey = rerouted["journey"]
        self.assertEqual(journey["state"], "in_transit")
        self.assertIsNone(journey["current_place_id"])
        self.assertEqual(journey["active_route_id"], "eaves_walkway")
        self.assertTrue(journey["active_route_sheltered"])
        self.assertEqual(journey["expected_arrival_at"], "2026-11-15T10:17:00+08:00")
        self.assertEqual(journey["halt_reason"], None)
        self.assertEqual(journey["blocked_route_id"], None)
        self.assertEqual(journey["accumulated_walk_seconds"], 120)  # never rolled back
        self.assertEqual(
            journey["planned_route_ids"], ["eaves_walkway", "street_to_bookstore"]
        )

        _, mid = self._read(now_iso="2026-11-15T10:06:00+08:00")
        mid_j = mid["journey"]
        self.assertEqual(mid_j["state"], "in_transit")
        self.assertIsNone(mid_j["current_place_id"])  # no early arrival claim
        self.assertEqual(mid_j["active_route_id"], "eaves_walkway")
        self.assertTrue(mid_j["active_route_sheltered"])
        self.assertEqual(
            mid_j["transit_location_cn"], "在从小院前往街角的路上（有檐长廊）"
        )
        self.assertEqual(mid_j["accumulated_walk_seconds"], 180)

        code, arrived = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            now_iso="2026-11-15T10:18:00+08:00",
        )
        self.assertEqual(code, 200, arrived)
        self.assertEqual(arrived["journey"]["state"], "arrived")
        self.assertEqual(arrived["journey"]["current_place_id"], "bookstore")
        self.assertEqual(arrived["journey"]["accumulated_walk_seconds"], 840)
        self.assertEqual(arrived["journey"]["transit_location_cn"], "在旧书店")

    def test_03_mid_route_turn_back_preserves_walk_and_projects_return_transit(self) -> None:
        code, departed = self._mutate(
            {
                "action": "start_journey",
                "target_place": "bookstore",
                "preferred_branch": "eaves_walkway",
            },
            now_iso=T_DEPART,
        )
        self.assertEqual(code, 200, departed)
        self.assertEqual(departed["journey"]["planned_route_ids"][0], "home_to_porch")

        code, turned = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "turn_back"},
            now_iso="2026-11-15T10:06:00+08:00",
        )
        self.assertEqual(code, 200, turned)
        self.assertEqual(turned["journey"]["state"], "in_transit")
        self.assertTrue(turned["journey"]["is_turning_back"])
        self.assertIsNone(turned["journey"]["current_place_id"])
        self.assertEqual(turned["journey"]["target_place_id"], "home")
        self.assertEqual(turned["journey"]["accumulated_walk_seconds"], 360)
        self.assertEqual(turned["journey"]["expected_arrival_at"], "2026-11-15T10:12:00+08:00")
        self.assertEqual(
            turned["journey"]["transit_location_cn"], "在从街角折返小院的路上"
        )

        code, home = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            now_iso="2026-11-15T10:13:00+08:00",
        )
        self.assertEqual(code, 200, home)
        self.assertEqual(home["journey"]["state"], "arrived")
        self.assertEqual(home["journey"]["current_place_id"], "home")
        self.assertEqual(home["journey"]["accumulated_walk_seconds"], 720)
        self.assertEqual(home["journey"]["transit_location_cn"], "在家")

    def test_04_reroute_honestly_discovers_blocked_eaves_and_never_loops(self) -> None:
        self._block("open_shortcut")
        self._block("eaves_walkway")
        code, _ = self._mutate(
            {"action": "start_journey", "target_place": "street_corner"},
            now_iso=T_DEPART,
        )
        self.assertEqual(code, 200)
        paused = self._pause_at_yard()
        self.assertEqual(paused["journey"]["accumulated_walk_seconds"], 120)

        code, detour = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "reroute"},
            now_iso="2026-11-15T10:03:00+08:00",
        )
        self.assertEqual(code, 200, detour)
        self.assertEqual(detour["result"]["outcome"], "halted_by_roadblock_at_entrance")
        self.assertEqual(detour["result"]["encountered_roadblock"], "eaves_walkway")
        self.assertEqual(detour["journey"]["state"], "paused_at_node")
        self.assertEqual(detour["journey"]["current_place_id"], "yard")
        self.assertEqual(detour["journey"]["halt_reason"], "ROADBLOCK_ENCOUNTERED")
        self.assertIn("eaves_walkway", detour["journey"]["discovered_roadblocks"])
        self.assertEqual(detour["journey"]["accumulated_walk_seconds"], 120)

        # Rerouting again finds the other known-blocked entrance and stays put:
        # no in_transit flip-flop, no infinite loop, no invented progress.
        code, again = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "reroute"},
            now_iso="2026-11-15T10:04:00+08:00",
        )
        self.assertEqual(code, 200, again)
        self.assertEqual(again["result"]["outcome"], "halted_by_roadblock_at_entrance")
        self.assertEqual(again["result"]["encountered_roadblock"], "open_shortcut")
        self.assertEqual(again["journey"]["state"], "paused_at_node")
        self.assertEqual(again["journey"]["current_place_id"], "yard")
        self.assertEqual(again["journey"]["accumulated_walk_seconds"], 120)

    def test_05_v08_canvas_bag_is_carriable_and_hidden_or_fixed_matter_is_not(self) -> None:
        root = self._new_root()
        v08_path = self._write_v08(
            root,
            {
                "canvas_bag": _bag_row(),
                "pocket_mirror": _bag_row(
                    object_id="pocket_mirror",
                    label_cn="小圆镜",
                    portability=None,
                    matter_profile={"portability": "portable"},
                ),
                "wall_clock": _bag_row(object_id="wall_clock", portability="fixed"),
                "mystery_parcel": _bag_row(
                    object_id="mystery_parcel",
                    subject_last_seen_parent=None,
                    subject_last_seen_snapshot={"parent_id": "__subject_unknown__"},
                ),
                "spent_candle": _bag_row(object_id="spent_candle", consumed=True),
            },
        )
        before_bytes = v08_path.read_bytes()
        before_stat = v08_path.lstat()

        code, carried = self._mutate(
            {
                "action": "start_journey",
                "target_place": "porch",
                "carry_item_ids": ["canvas_bag", "pocket_mirror"],
            },
            now_iso=T_DEPART,
            root=root,
        )
        self.assertEqual(code, 200, carried)
        self.assertEqual(carried["journey"]["carried_item_ids"], ["canvas_bag", "pocket_mirror"])
        self.assertEqual(carried["journey"]["carried_effective_location"], "in_transit:home_to_porch")

        # The matter owner's file is strictly read-only from this domain.
        self.assertEqual(v08_path.read_bytes(), before_bytes)
        after_stat = v08_path.lstat()
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(after_stat.st_size, before_stat.st_size)

        for item_id in ("wall_clock", "mystery_parcel", "spent_candle"):
            case_root = self._new_root()
            self._write_v08(case_root, {item_id: _bag_row(**{
                "object_id": item_id,
                **(
                    {"portability": "fixed"}
                    if item_id == "wall_clock"
                    else {"subject_last_seen_parent": None,
                          "subject_last_seen_snapshot": {"parent_id": "__subject_unknown__"}}
                    if item_id == "mystery_parcel"
                    else {"consumed": True}
                ),
            })})
            code, body = self._mutate(
                {"action": "start_journey", "target_place": "porch", "carry_item_ids": [item_id]},
                now_iso=T_DEPART,
                root=case_root,
            )
            self.assertEqual(code, 409, (item_id, body))
            self.assertEqual(body["reason_code"], "ITEM_NOT_CARRIABLE")

        # No v08 file at all -> the item simply cannot be carried.
        bare_root = self._new_root()
        code, body = self._mutate(
            {"action": "start_journey", "target_place": "porch", "carry_item_ids": ["canvas_bag"]},
            now_iso=T_DEPART,
            root=bare_root,
        )
        self.assertEqual(code, 409, body)
        self.assertEqual(body["reason_code"], "ITEM_NOT_CARRIABLE")

    def test_06_views_and_projection_are_zero_write_including_v08_file(self) -> None:
        self._write_v08(self.day_root, {"canvas_bag": _bag_row()})
        code, _ = self._depart_to_bookstore()
        self.assertEqual(code, 200)

        # Every read path (subject/partner view + both projections) is pure:
        # the whole tree -- v04 state AND the v08 owner file -- stays untouched.
        before = _tree_snapshot(self.day_root)
        self.assertTrue(any(row[0].endswith("v08_matter_space.json") for row in before))
        for now_iso in ("2026-11-15T10:01:00+08:00", "2026-11-15T10:02:00+08:00"):
            code, _ = self._read(now_iso=now_iso)
            self.assertEqual(code, 200)
            code, _ = self._read(now_iso=now_iso, subject=False)
            self.assertEqual(code, 200)
            engine.project_v04_neighborhood(self.day_root, now_iso=now_iso)
            engine.project_v04_neighborhood(
                self.day_root, has_subject_capability=True, now_iso=now_iso
            )
        self.assertEqual(_tree_snapshot(self.day_root), before)

        # A pure read round on an unchanged clock is byte/mtime identical too.
        stable = _tree_snapshot(self.day_root)
        engine.project_v04_neighborhood(self.day_root, now_iso="2026-11-15T10:02:00+08:00")
        self._read(now_iso="2026-11-15T10:02:00+08:00")
        self.assertEqual(_tree_snapshot(self.day_root), stable)


if __name__ == "__main__":
    unittest.main()
