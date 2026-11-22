#!/usr/bin/env python3
"""Offline clock-gap x neighbourhood weather x matter-physics coupling.

Ch.06/07 Iron Laws under test:

* Elapsed offline time advances **only objective slow physics** -- thermal,
  moisture, freshness and rain splash.  It never fabricates cognitive memories
  (``cognitive_backfill_forbidden == True``) and never turns an unresolved
  ``UNKNOWN`` action into a guessed outcome.
* Ticking the clock on a directory whose ``v08_matter_space.json`` does not
  exist is 100% zero-write: no world directory, no state file, no mtime change.

The weather-to-matter settlement itself lives in
``components.world.scripts.v08_universal_matter`` and the micro-zone/spot
conditions in ``components.world.scripts.v08_microzone_ecology``.  These tests
drive the *public* clock entrypoint (``mutate_v03_clock_view`` ->
``advance_projection``) exactly as the phone does, then read the settled matter
space through its own public view.

Every case is deterministic, zero-network and ``llm_calls == 0``.

Run (WSL)::

    /tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_weather_clock_coupling
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
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v03_clock_evaporation as clock  # noqa: E402
from components.world.scripts import v04_neighborhood_living as neighborhood  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

try:  # Worker 8A lands the ecology engine; absence must not crash the suite.
    from components.world.scripts import v08_microzone_ecology as microzone  # noqa: E402
except Exception:  # pragma: no cover - only before 8A lands
    microzone = None  # type: ignore[assignment]

#: The weather/wind integration hook exists only once worker 8A has landed it.
_HAS_WEATHER_COUPLING = microzone is not None and hasattr(
    matter, "_read_microzone_env_zero_write"
)


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
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _zone(zone_id: str, *, ambient_zone: str = "room", capacity_slots: int = 4) -> dict:
    return {
        "zone_id": zone_id,
        "label_cn": zone_id,
        "ambient_zone": ambient_zone,
        "water_source": False,
        "capacity_slots": capacity_slots,
        "visibility": "shared",
        "surface_cleanliness": "clean",
    }


def _object(object_id: str, label_cn: str, item_kind: str, parent_id: str, **overrides) -> dict:
    row = matter._object_row(  # shipped row factory keeps every physics anchor present
        object_id,
        label_cn,
        item_kind=item_kind,
        parent_id=parent_id,
        paper_like=item_kind == "paper",
    )
    row.update(overrides)
    return row


class _CouplingHarness(unittest.TestCase):
    """Shared helpers for the offline-gap weather coupling suite."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-weather-clock-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- state authored on disk (no engine writes needed) -----------------

    def _write_json(self, rel: str, doc: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_weather(self, weather: str) -> None:
        state = neighborhood.default_state()
        state["environment"]["weather"] = weather
        state["environment"]["airflow"] = "medium"
        state["environment"]["season"] = "autumn"
        state["environment"]["updated_at"] = T0
        neighborhood.save_state_atomic(self.root, state)

    def _write_matter_space(self, zones: list[dict], objects: list[dict]) -> None:
        doc = {
            "schema_version": matter.SCHEMA_VERSION,
            "revision": 0,
            "created_at": T0,
            "updated_at": T0,
            "zones": {row["zone_id"]: row for row in zones},
            "matter_objects": {row["object_id"]: row for row in objects},
            "recent_acts": [],
            "operations": {},
            "discards": [],
        }
        self._write_json("world/v08_matter_space.json", doc)

    def _write_microzone(self, **fields) -> None:
        doc = {"schema_version": "world.v08.microzone_ecology.v1", "revision": 0}
        doc.update(fields)
        self._write_json("world/v08_microzone_state.json", doc)

    # -- clock driving ----------------------------------------------------

    def _advance(self, now: str, *, sync_v02: bool = False) -> dict:
        code, body = clock.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": sync_v02},
            actor_side="subject",
            now_iso=now,
        )
        self.assertEqual(code, 200, body)
        self.assertTrue(body["ok"], body)
        return body["data"]

    def _last_receipt(self, data: dict) -> dict:
        return data["clocks"]["world_clock"]["gap_receipts"][-1]

    def _matter_object(self, object_id: str, now: str) -> dict:
        view = matter.read_matter_space_view(self.root, actor_side="subject", now_iso=now)
        for row in view["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in matter view: {object_id}")


# ---------------------------------------------------------------------------
# Zero-write invariants
# ---------------------------------------------------------------------------


class ZeroWriteInvariantTest(_CouplingHarness):
    def test_01_clock_read_and_matter_settle_on_empty_root_create_nothing(self) -> None:
        before = _snapshot(self.root)

        status, env = clock.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(status, 200)
        self.assertEqual(env["data"]["llm_calls"], 0)

        settled = matter.settle_matter_physics(self.root, now_iso=T0)
        self.assertTrue(all(int(value) == 0 for value in settled.values()), settled)

        # A second read after the settle call is still a no-op.
        clock.read_v03_clock_view(self.root, has_subject_capability=False, now_iso=_at(40))

        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse((self.root / "world").exists())
        self.assertFalse(clock.v03_clock_state_path(self.root).exists())
        self.assertFalse(matter.state_path(self.root).exists())


# ---------------------------------------------------------------------------
# Case 1 -- rain splash across an offline gap smudges a paper note
# ---------------------------------------------------------------------------


class RainSplashAcrossGapTest(_CouplingHarness):
    def setUp(self) -> None:
        super().setUp()
        if not _HAS_WEATHER_COUPLING:
            self.skipTest("worker 8A microzone/weather matter coupling not landed yet")

    def test_02_offline_gap_wets_note_on_open_yard_and_smudges_it(self) -> None:
        # Tick the clock once *before* any matter exists: the absence of
        # ``v08_matter_space.json`` must stay zero-write and yield an empty
        # settlement block in the receipt.
        first = self._advance(T0)
        first_receipt = self._last_receipt(first)
        self.assertEqual(first_receipt["matter_physics_settled"], {})
        self.assertFalse(matter.state_path(self.root).exists())

        # The subject leaves a written note in the open yard, then goes offline.
        note = _object(
            "note_open_yard",
            "院子里的便条",
            "paper",
            "open_yard",
            inscribed_text_cn="记得收晾着的衣服",
            moisture="dry",
            last_moisture_at=T0,
            last_thermal_at=T0,
        )
        self._write_matter_space(zones=[_zone("open_yard", ambient_zone="open_air")], objects=[note])
        self._write_weather("rain")

        # 40m offline gap.
        data = self._advance(_at(40))
        receipt = self._last_receipt(data)

        self.assertEqual(receipt["elapsed_seconds"], 40 * 60)
        self.assertGreaterEqual(
            int(receipt["matter_physics_settled"].get("rain_dampened", 0)),
            1,
            "the rain splash must be reported as objective settlement",
        )
        self.assertEqual(receipt["unknown_actions_guessed"], 0)
        self.assertEqual(receipt["cognitive_backfill_count"], 0)
        self.assertEqual(receipt["llm_calls"], 0)
        self.assertEqual(data["llm_calls"], 0)

        row = self._matter_object("note_open_yard", _at(40))
        self.assertIn(row["moisture"], ("wet", "damp"))
        self.assertEqual(row["note_legibility"], "smudged")

    def test_03_open_window_rain_smudges_note_on_home_desk(self) -> None:
        self._advance(T0)  # baseline commit; the note and rain arrive afterwards
        note = _object(
            "note_home_desk",
            "桌角便条",
            "paper",
            "home_desk",
            inscribed_text_cn="周五前写完这一页",
            moisture="dry",
            last_moisture_at=T0,
            last_thermal_at=T0,
        )
        self._write_matter_space(zones=[], objects=[note])  # home_desk is a shipped default zone
        self._write_weather("rain")
        self._write_microzone(window_state="open", active_spot_id="window_side")

        data = self._advance(_at(45))
        receipt = self._last_receipt(data)

        self.assertEqual(receipt["elapsed_seconds"], 45 * 60)
        self.assertGreaterEqual(int(receipt["matter_physics_settled"].get("rain_dampened", 0)), 1)
        row = self._matter_object("note_home_desk", _at(45))
        self.assertIn(row["moisture"], ("wet", "damp"))
        self.assertEqual(row["note_legibility"], "smudged")

        # The clock view exposes the same objective ambient conditions the
        # matter settled against, for read-only UI alignment.
        ambient = data["clocks"]["world_clock"]["microzone_ambient"]
        self.assertEqual(ambient["weather"], "rain")
        self.assertEqual(ambient["window_state"], "open")
        self.assertEqual(ambient["active_conditions"]["draft_state"], "rain_splash")

    def test_04_closed_window_keeps_desk_note_clear_under_rain(self) -> None:
        self._advance(T0)  # baseline commit; the note and rain arrive afterwards
        note = _object(
            "note_home_desk_closed",
            "桌角便条",
            "paper",
            "home_desk",
            inscribed_text_cn="周五前写完这一页",
            moisture="dry",
            last_moisture_at=T0,
            last_thermal_at=T0,
        )
        self._write_matter_space(zones=[], objects=[note])
        self._write_weather("rain")
        self._write_microzone(window_state="closed", active_spot_id="desk_inner")

        data = self._advance(_at(45))
        receipt = self._last_receipt(data)

        self.assertEqual(int(receipt["matter_physics_settled"].get("rain_dampened", 0)), 0)
        row = self._matter_object("note_home_desk_closed", _at(45))
        self.assertEqual(row["moisture"], "dry")
        self.assertEqual(row["note_legibility"], "clear")


# ---------------------------------------------------------------------------
# Case 2 -- porch eaves shelter a wet cloth and halve the drying rate
# ---------------------------------------------------------------------------


class ShelteredSlowDryingTest(_CouplingHarness):
    def setUp(self) -> None:
        super().setUp()
        if not _HAS_WEATHER_COUPLING:
            self.skipTest("worker 8A microzone/weather matter coupling not landed yet")

    def test_05_porch_eaves_not_rained_on_and_dries_twice_as_slow(self) -> None:
        self._advance(T0)  # baseline commit; wet cloth and rain arrive afterwards
        porch_cloth = _object(
            "cloth_porch",
            "檐下湿布",
            "cloth",
            "porch_eaves",
            moisture="damp",
            last_moisture_at=T0,
            last_thermal_at=T0,
        )
        indoor_cloth = _object(
            "cloth_indoor",
            "室内湿布",
            "cloth",
            "home_desk",
            moisture="damp",
            last_moisture_at=T0,
            last_thermal_at=T0,
        )
        self._write_matter_space(
            zones=[_zone("porch_eaves", ambient_zone="room")],
            objects=[porch_cloth, indoor_cloth],
        )
        self._write_weather("rain")

        # 130m: the indoor cloth crosses the 120m room-dry threshold, the
        # sheltered cloth (needing 2x) must still be damp.
        data = self._advance(_at(130))
        receipt = self._last_receipt(data)

        self.assertEqual(receipt["elapsed_seconds"], 130 * 60)
        self.assertEqual(
            int(receipt["matter_physics_settled"].get("rain_dampened", 0)),
            0,
            "eaves shelter must never be counted as rain-wetted",
        )
        indoor = self._matter_object("cloth_indoor", _at(130))
        porch = self._matter_object("cloth_porch", _at(130))
        self.assertEqual(indoor["moisture"], "dry")
        self.assertEqual(
            porch["moisture"],
            "damp",
            "sheltered cloth must not be rained on and must dry at half rate",
        )

        # 260m total: the sheltered cloth finally dries (2 * 120m).
        data2 = self._advance(_at(260))
        porch_late = self._matter_object("cloth_porch", _at(260))
        self.assertEqual(porch_late["moisture"], "dry")
        self.assertEqual(self._last_receipt(data2)["unknown_actions_guessed"], 0)


# ---------------------------------------------------------------------------
# Case 3 -- an open window's breeze cools hot tea in under 20m
# ---------------------------------------------------------------------------


class BreezeCoolingTest(_CouplingHarness):
    def setUp(self) -> None:
        super().setUp()
        if not _HAS_WEATHER_COUPLING:
            self.skipTest("worker 8A microzone/weather matter coupling not landed yet")

    def _tea(self) -> dict:
        return _object(
            "tea_01",
            "热茶",
            "drink",
            "home_desk",
            thermal="hot",
            last_thermal_at=T0,
            last_moisture_at=T0,
        )

    def test_06_open_window_cools_hot_tea_within_20m(self) -> None:
        self._advance(T0)  # baseline commit at pouring time
        self._write_matter_space(zones=[], objects=[self._tea()])
        self._write_microzone(window_state="open", active_spot_id="desk_inner")

        data = self._advance(_at(20))
        receipt = self._last_receipt(data)

        self.assertEqual(receipt["elapsed_seconds"], 20 * 60)
        self.assertGreaterEqual(int(receipt["matter_physics_settled"].get("cooled", 0)), 1)
        self.assertEqual(self._matter_object("tea_01", _at(20))["thermal"], "room")

    def test_07_closed_window_keeps_tea_hot_at_20m(self) -> None:
        self._advance(T0)  # baseline commit at pouring time
        self._write_matter_space(zones=[], objects=[self._tea()])
        self._write_microzone(window_state="closed", active_spot_id="desk_inner")

        data = self._advance(_at(20))
        receipt = self._last_receipt(data)

        self.assertEqual(receipt["elapsed_seconds"], 20 * 60)
        self.assertEqual(int(receipt["matter_physics_settled"].get("cooled", 0)), 0)
        self.assertIn(self._matter_object("tea_01", _at(20))["thermal"], ("hot", "warm"))


# ---------------------------------------------------------------------------
# Case 4 -- offline gaps never fabricate cognition or guess UNKNOWN actions
# ---------------------------------------------------------------------------


class OfflineCognitionIronLawTest(_CouplingHarness):
    def _seed_clock_with_unknown(self) -> None:
        state = clock.default_v03_clock_state()
        state["revision"] = 3
        state["updated_at"] = T0
        state["world_clock"]["last_committed_at"] = T0
        state["world_clock"]["ambient_date"] = "2026-11-16"
        state["world_clock"]["pending_unknown_actions"] = [
            {
                "unknown_id": "unk_01",
                "description_cn": "出门前到底有没有关窗，记不清了",
                "effect_state": "UNKNOWN",
                "guessed_by_clock": False,
            }
        ]
        state["cognitive_clock"]["causal_ticks"] = 7
        state["cognitive_clock"]["last_cognitive_at"] = T0
        path = clock.v03_clock_state_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def test_08_three_hour_gap_never_backfills_cognition_or_guesses_unknown(self) -> None:
        self._seed_clock_with_unknown()

        data = self._advance(_at(180))
        receipt = self._last_receipt(data)

        self.assertEqual(receipt["elapsed_seconds"], 3 * 60 * 60)
        self.assertEqual(receipt["cognitive_backfill_count"], 0)
        self.assertEqual(receipt["unknown_actions_guessed"], 0)
        self.assertEqual(receipt["llm_calls"], 0)
        self.assertEqual(data["llm_calls"], 0)
        self.assertTrue(data["clocks"]["cognitive_clock"]["cognitive_backfill_forbidden"])
        self.assertEqual(data["clocks"]["cognitive_clock"]["causal_ticks"], 7)

        state, _exists = clock.read_v03_clock_state(self.root)
        pending = state["world_clock"]["pending_unknown_actions"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["effect_state"], "UNKNOWN")
        self.assertFalse(pending[0]["guessed_by_clock"])
        self.assertEqual(state["cognitive_clock"]["causal_ticks"], 7)
        self.assertTrue(state["cognitive_clock"]["cognitive_backfill_forbidden"])

    def test_09_gap_receipt_schema_stays_backwards_compatible(self) -> None:
        data = self._advance(_at(40))
        receipt = self._last_receipt(data)

        for key in (
            "kind",
            "host_gap_recorded",
            "honest_blank",
            "from_at",
            "to_at",
            "elapsed_seconds",
            "settled_slow_vars",
            "evaporated_count",
            "faded_missing_notes",
            "slow_ledger",
            "evaporated_living_traces",
            "matter_physics_settled",
            "ambient_date_switched",
        ):
            self.assertIn(key, receipt)
        self.assertEqual(receipt["unknown_actions_guessed"], 0)
        self.assertEqual(receipt["cognitive_backfill_count"], 0)
        self.assertEqual(receipt["llm_calls"], 0)
        self.assertIsInstance(receipt["matter_physics_settled"], dict)
        # A clock view on this same directory must also stay read-only and may
        # carry the ambient alignment block without leaking private breakpoints.
        before = _snapshot(self.root)
        status, view = clock.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso=_at(41)
        )
        self.assertEqual(status, 200)
        if _HAS_WEATHER_COUPLING:
            ambient = view["data"]["clocks"]["world_clock"]["microzone_ambient"]
            self.assertIsInstance(ambient, dict)
            self.assertIn("weather", ambient)
            self.assertIn("window_state", ambient)
            self.assertNotIn("preserved_work_ref", ambient)
            self.assertNotIn("preserved_breakpoint_cn", ambient)
        self.assertEqual(_snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
