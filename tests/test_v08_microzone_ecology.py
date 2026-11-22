#!/usr/bin/env python3
"""v3.0 Ch.33/34 + v0.4 Ch.41: deterministic micro-zone room ecology.

Coverage (``llm_calls == 0`` throughout):
* a fresh root stays 100% zero-write for both the micro-zone view and the
  phone living-traces read (no world dir, no file, no mtime);
* afternoon clear sun glares at the sill while the inner desk stays
  comfortable, and a subject who moves spots keeps its work breakpoint
  (``continuity_preserved``), while the partner door never sees it;
* an evening room is dim until the partner softly lights a side lamp; the
  partner may adjust window/lamp but gets 403 on the subject's own spot;
* Ch.41 §六 aroma is strictly derived from real warm/hot, non-spoiled food in
  an indoor zone and disappears once the food cools;
* open-window rain wets paper at the sill and in the open yard, the porch
  eaves stay un-rained and dry at half rate, and an open window cools hot tea
  faster.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_microzone_ecology -v``
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
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_microzone_ecology as microzone  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402
from components.phone import v08_living_traces_view as view  # noqa: E402

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


def _write_v04_env(root: Path, *, weather: str, airflow: str = "medium", season: str = "autumn") -> None:
    world = root / "world"
    world.mkdir(parents=True, exist_ok=True)
    (world / "v04_neighborhood_state.json").write_text(
        json.dumps(
            {"environment": {"weather": weather, "airflow": airflow, "season": season}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_matter_state(root: Path, zones: list[dict], objects: list[dict]) -> None:
    state = matter._default_state()
    for zone in zones:
        state["zones"][zone["zone_id"]] = zone
    state["matter_objects"] = {obj["object_id"]: obj for obj in objects}
    world = root / "world"
    world.mkdir(parents=True, exist_ok=True)
    (world / "v08_matter_space.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _zone(zone_id: str, label: str) -> dict:
    return matter._zone_row(
        zone_id, label, ambient_zone="room", water_source=False, capacity_slots=6
    )


def _matter_object(
    object_id: str,
    label: str,
    *,
    parent_id: str,
    item_kind: str = "everyday",
    paper_like: bool = False,
    inscription: str | None = None,
    thermal: str = "room",
    moisture: str = "dry",
    shelf_life_hours: float | None = None,
) -> dict:
    obj = matter._object_row(
        object_id, label, item_kind=item_kind, parent_id=parent_id, paper_like=paper_like
    )
    obj["inscribed_text_cn"] = inscription
    obj["thermal"] = thermal
    obj["moisture"] = moisture
    obj["last_thermal_at"] = T0
    obj["last_moisture_at"] = T0
    obj["state_clock_iso"] = T0
    if shelf_life_hours is not None:
        obj["shelf_life_hours"] = shelf_life_hours
        obj["freshness"] = "fresh"
        obj["freshness_anchor_at"] = T0
        obj["freshness_settled_at"] = T0
        obj["effective_exposure_hours"] = 0.0
    elif item_kind in ("prepared_food",):
        obj["freshness"] = "fresh"
    return obj


class MicrozoneEcologyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-microzone-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _resolve(self, *, now: str = T0, actor_side: str = "subject") -> dict:
        return microzone.read_microzone_ecology_view(
            self.root, actor_side=actor_side, now_iso=now
        )

    def _spots(self, where: dict) -> dict[str, dict]:
        return {row["spot_id"]: row for row in where["spots"]}

    def _adjust(self, payload: dict, *, actor_side: str = "subject", now: str = T0) -> dict:
        receipt = microzone.adjust_microzone_comfort(
            self.root, payload, actor_side=actor_side, now_iso=now
        )
        self.assertEqual(receipt["llm_calls"], 0)
        return receipt

    def _matter_obj(self, object_id: str, *, now: str = T0) -> dict:
        space = matter.read_matter_space_view(self.root, now_iso=now)
        for row in space["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in view: {object_id}")

    # -- 1 ---------------------------------------------------------------

    def test_fresh_root_reads_are_100_percent_zero_write(self) -> None:
        before = _snapshot(self.root)
        subject = self._resolve(now="2026-11-16T14:00:00+08:00")
        partner = self._resolve(now="2026-11-16T14:00:00+08:00", actor_side="partner")
        status, payload = view.read_v08_living_traces_view(self.root, now_iso=T0)
        self.assertEqual(status, 200)
        self.assertEqual(subject["llm_calls"], 0)
        self.assertEqual(partner["llm_calls"], 0)
        self.assertIsNone(payload["data"]["microzone_ecology"]["aroma_cue_cn"])
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse((self.root / "world").exists())
        self.assertFalse(microzone.state_path(self.root).exists())

    # -- 2 ---------------------------------------------------------------

    def test_afternoon_glare_and_continuity_when_moving_spots(self) -> None:
        _write_v04_env(self.root, weather="clear", airflow="medium")
        afternoon = "2026-11-16T14:00:00+08:00"

        before = self._resolve(now=afternoon)
        spots = self._spots(before)
        self.assertEqual(spots["window_side"]["light_quality"], "glare")
        self.assertEqual(spots["desk_inner"]["light_quality"], "comfortable")
        self.assertIn("窗边光线刺眼", before["salient_reasons"])
        self.assertFalse(before["is_quiet_background"])

        self._adjust(
            {
                "target_spot_id": "window_side",
                "posture_cn": "盘腿坐在窗边",
                "preserve_work_ref": "没画完的速写",
                "preserve_breakpoint_cn": "停在第三条线的阴影处",
            },
            now=afternoon,
        )
        moved = self._resolve(now=afternoon)
        self.assertEqual(moved["active_spot_id"], "window_side")
        self.assertEqual(moved["preserved_work_ref"], "没画完的速写")
        self.assertEqual(moved["preserved_breakpoint_cn"], "停在第三条线的阴影处")
        self.assertTrue(moved["continuity_preserved"])
        self.assertIn("换了座位继续刚才的事", moved["salient_reasons"])

        # Moving back to the inner desk carries the same work breakpoint.
        self._adjust({"target_spot_id": "desk_inner"}, now=afternoon)
        carried = self._resolve(now=afternoon)
        self.assertEqual(carried["active_spot_id"], "desk_inner")
        self.assertEqual(carried["preserved_work_ref"], "没画完的速写")
        self.assertEqual(carried["preserved_breakpoint_cn"], "停在第三条线的阴影处")
        self.assertTrue(carried["continuity_preserved"])

        # Default visibility is private, so the partner door never sees the work.
        partner = self._resolve(now=afternoon, actor_side="partner")
        self.assertIsNone(partner["preserved_work_ref"])
        self.assertIsNone(partner["preserved_breakpoint_cn"])
        self.assertFalse(partner["continuity_preserved"])
        self.assertNotIn("换了座位继续刚才的事", partner["salient_reasons"])

    # -- 3 ---------------------------------------------------------------

    def test_evening_dim_lamp_and_partner_permission_boundary(self) -> None:
        _write_v04_env(self.root, weather="clear")
        evening = "2026-11-16T20:00:00+08:00"

        dark = self._resolve(now=evening)
        self.assertTrue(dark["is_night"])
        self.assertEqual(self._spots(dark)["desk_inner"]["light_quality"], "dim")
        self.assertIn("夜晚屋里光线偏暗", dark["salient_reasons"])

        status, envelope = view.mutate_v08_living_traces_view(
            self.root,
            {"action": "adjust_microzone_comfort", "lamp_on": True, "lamp_angle": "soft_side"},
            actor_side="partner",
            now_iso=evening,
        )
        self.assertEqual(status, 200)
        self.assertEqual(envelope["perspective"], "partner")
        lit = envelope["data"]["microzone_ecology"]
        self.assertEqual(self._spots(lit)["desk_inner"]["light_quality"], "soft_lamp")
        self.assertNotIn("夜晚屋里光线偏暗", lit["salient_reasons"])

        denied, refusal = view.mutate_v08_living_traces_view(
            self.root,
            {"action": "adjust_microzone_comfort", "target_spot_id": "window_side"},
            actor_side="partner",
            now_iso=evening,
        )
        self.assertEqual(denied, 403)
        self.assertEqual(refusal["reason_code"], "FORBIDDEN_PARTNER_ACTION")

        with self.assertRaises(microzone.V08MicrozoneError) as caught:
            microzone.adjust_microzone_comfort(
                self.root,
                {"target_spot_id": "window_side"},
                actor_side="partner",
                now_iso=evening,
            )
        self.assertEqual(caught.exception.status_code, 403)

    # -- 4 ---------------------------------------------------------------

    def test_aroma_is_strictly_derived_from_real_warm_food(self) -> None:
        _write_v04_env(self.root, weather="clear")
        self.assertIsNone(self._resolve()["aroma_cue_cn"])

        matter.compose_open_act(
            self.root,
            {
                "intent_cn": "把温着的汤面放到备餐台",
                "steps": [{
                    "op": "INTRODUCE_ITEM",
                    "object_id": "warm_noodles_01",
                    "label_cn": "热汤面",
                    "item_kind": "prepared_food",
                    "target_parent_id": "kitchen_counter",
                    "thermal": "warm",
                    "cook_state": "cooked",
                    "shelf_life_hours": 24,
                }],
            },
            now_iso=T0,
        )

        warm = self._resolve(now=_at(1))
        self.assertEqual(warm["aroma_cue_cn"], "屋里有刚温好的「热汤面」香味")
        self.assertIn("食物茶水暖香", warm["salient_reasons"])

        # 31 minutes later the room air has cooled the food: no aroma remains.
        cooled = self._resolve(now=_at(31))
        self.assertIsNone(cooled["aroma_cue_cn"])
        self.assertNotIn("食物茶水暖香", cooled["salient_reasons"])
        self.assertEqual(self._matter_obj("warm_noodles_01", now=_at(31))["thermal"], "room")

    # -- 5 ---------------------------------------------------------------

    def test_rain_wetting_porch_shelter_and_faster_window_cooling(self) -> None:
        _write_v04_env(self.root, weather="rain", airflow="strong")
        _write_matter_state(
            self.root,
            zones=[
                _zone("open_yard", "院子"),
                _zone("window_side", "窗边"),
                _zone("porch_eaves", "檐下"),
                _zone("home_desk", "书桌台面"),
            ],
            objects=[
                _matter_object(
                    "yard_note", "院子里的备忘", parent_id="open_yard",
                    item_kind="paper", paper_like=True, inscription="记得收画架",
                ),
                _matter_object(
                    "sill_note", "窗台便笺", parent_id="window_side",
                    item_kind="paper", paper_like=True, inscription="明天带伞",
                ),
                _matter_object(
                    "porch_note", "檐下便笺", parent_id="porch_eaves",
                    item_kind="paper", paper_like=True, inscription="钥匙在花盆下",
                ),
                _matter_object(
                    "porch_cloth", "檐下抹布", parent_id="porch_eaves",
                    item_kind="everyday", moisture="wet",
                ),
                _matter_object(
                    "hot_tea", "热茶", parent_id="home_desk",
                    item_kind="drink", thermal="hot",
                ),
            ],
        )
        self._adjust({"window_state": "open"}, now=T0)

        counts = matter.settle_matter_physics(self.root, now_iso=_at(20))
        self.assertGreaterEqual(counts["rain_dampened"], 2)
        self.assertGreaterEqual(counts["cooled"], 1)

        yard = self._matter_obj("yard_note", now=_at(20))
        sill = self._matter_obj("sill_note", now=_at(20))
        porch_note = self._matter_obj("porch_note", now=_at(20))
        porch_cloth = self._matter_obj("porch_cloth", now=_at(20))
        tea = self._matter_obj("hot_tea", now=_at(20))

        self.assertEqual(yard["moisture"], "wet")
        self.assertEqual(yard["note_legibility"], "smudged")
        self.assertEqual(sill["moisture"], "damp")
        self.assertEqual(sill["note_legibility"], "smudged")
        self.assertEqual(porch_note["moisture"], "dry")
        self.assertEqual(porch_cloth["moisture"], "wet")
        # Open window: hot tea cooled in 18 minutes, not the usual 30.
        self.assertEqual(tea["thermal"], "room")

        # The sheltered cloth needs 2x the room drying time, then really dries.
        still_wet = self._matter_obj("porch_cloth", now=_at(150))
        self.assertEqual(still_wet["moisture"], "wet")
        dry_counts = matter.settle_matter_physics(self.root, now_iso=_at(250))
        self.assertGreaterEqual(dry_counts["dried"], 1)
        self.assertEqual(self._matter_obj("porch_cloth", now=_at(250))["moisture"], "dry")


if __name__ == "__main__":
    unittest.main()
