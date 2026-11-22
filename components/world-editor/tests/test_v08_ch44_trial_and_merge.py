"""v0.4 Ch.44: isolated 1-hour trial, builder provenance and lived-safe merge.

Direct Python calls and the live loopback HTTP server are both exercised.  Every
result must report ``llm_calls == 0``: this feature is deterministic physics,
never a model call.
"""

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import model  # noqa: E402
import trial_sandbox  # noqa: E402
from server import create_server  # noqa: E402
from space import empty_space  # noqa: E402

EXAMPLE = BASE / "examples/cafe.nyairo-world.json"


def build_package(*, extra_room=False, extra_object=False):
    """A small deterministic home package (one home-ish room + a desk + a book)."""

    base = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    package = copy.deepcopy(base)
    room = copy.deepcopy(package["rooms"][0])
    room.update(
        id="home",
        name="家",
        description="一间安静的小屋",
        kind="room",
        x=100,
        y=100,
        hours=None,
        space=empty_space(),
    )
    package["rooms"] = [room]
    package["connections"] = []
    package["objects"] = []
    package["initial"]["room_id"] = "home"

    def obj(object_id, name, kind, x, y, *, portable=False, room_id="home"):
        return {
            "id": object_id,
            "name": name,
            "description": "",
            "kind": kind,
            "room_id": room_id,
            "x": x,
            "y": y,
            "actions": [],
            "portable": portable,
            "capacity": 0,
            "servings": 0,
            "visual": {"scale": 100, "rotation": 0, "layer": 0, "show_name": True},
            "behavior": model.default_behavior(kind),
            "asset_id": None,
            "interaction": {"near": False, "radius": 2},
            "initial_status": model.default_status(kind, 0),
        }

    package["objects"] = [
        obj("home-desk", "书桌", "table", 30, 40),
        obj("reading-book", "随便翻翻的书", "book", 60, 60, portable=True),
    ]
    if extra_room:
        study = copy.deepcopy(room)
        study.update(id="study", name="书房", x=100, y=250, w=300, h=200)
        package["rooms"].append(study)
        package["connections"] = [{"from": "home", "to": "study"}]
        package["objects"].append(obj("study-desk", "书房桌", "table", 40, 50, room_id="study"))
    if extra_object:
        package["objects"].append(obj("new-lamp", "新添的小灯", "lamp", 80, 20, portable=True))
    return model.validate_package(package)[0]


def write_instance(root, *, package, state):
    (Path(root) / "world-package").mkdir(parents=True, exist_ok=True)
    (Path(root) / "world").mkdir(parents=True, exist_ok=True)
    (Path(root) / "world-package" / "editor_package.json").write_text(
        json.dumps(package, ensure_ascii=False), encoding="utf-8"
    )
    (Path(root) / "world" / "v08_matter_space.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def lived_state():
    return {
        "schema_version": "world.v08.universal_matter.v1",
        "revision": 1,
        "zones": {
            "home_desk": {
                "zone_id": "home_desk",
                "label_cn": "书桌台面",
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 8,
                "visibility": "shared",
                "surface_cleanliness": "clean",
            }
        },
        "matter_objects": {
            "lived_note": {
                "object_id": "lived_note",
                "label_cn": "抄了一半的卡片",
                "item_kind": "paper",
                "parent_id": "study-desk",
                "capacity_slots": 0,
                "material_units": 3,
                "assembly_role": "none",
                "paper_like": True,
                "inscribed_text_cn": "明天买牛奶",
                "moisture": "dry",
                "thermal": "room",
                "freshness": "non_perishable",
                "wear_state": "intact",
                "consumed": False,
                "discarded": False,
            }
        },
        "operations": {},
        "recent_acts": [],
        "discards": [],
    }


class IsolatedTrialTests(unittest.TestCase):
    def test_clear_vs_rain_open_window_changes_paper_legibility(self):
        clear = trial_sandbox.run_isolated_trial_hour(
            build_package(), weather="clear", window_state="closed", hour=14
        )
        rainy = trial_sandbox.run_isolated_trial_hour(
            build_package(), weather="rain", window_state="open", hour=14
        )

        self.assertEqual(clear["paper_note_state"]["moisture"], "dry")
        self.assertEqual(clear["paper_note_state"]["note_legibility"], "clear")
        self.assertEqual(rainy["paper_note_state"]["moisture"], "damp")
        self.assertEqual(rainy["paper_note_state"]["note_legibility"], "smudged")

        for result in (clear, rainy):
            self.assertIs(result["isolated_sandbox"], True)
            self.assertIs(result["real_lived_state_touched"], False)
            self.assertEqual(result["llm_calls"], 0)
            self.assertGreaterEqual(len(result["trial_steps_cn"]), 3)
            self.assertLessEqual(len(result["trial_steps_cn"]), 5)
            self.assertTrue(result["discovered_zones"])
            self.assertTrue(result["discovered_objects"])
            self.assertIn("observation_cn", result["microzone_summary"]["window_side"])
            self.assertIn("observation_cn", result["microzone_summary"]["room_side"])

        self.assertEqual(clear["microzone_summary"]["window_side"]["light_quality"], "glare")
        self.assertEqual(rainy["microzone_summary"]["window_side"]["draft_state"], "rain_splash")

    def test_ab_compare_reports_zone_and_object_deltas(self):
        result = trial_sandbox.run_isolated_trial_hour(
            build_package(),
            weather="clear",
            window_state="closed",
            hour=14,
            compare_package=build_package(extra_room=True, extra_object=True),
        )
        diff = result["ab_diff"]
        self.assertIsNotNone(diff)
        self.assertGreaterEqual(diff["zone_count_delta"], 1)
        self.assertGreaterEqual(diff["object_count_delta"], 1)
        self.assertFalse(diff["light_changed"])
        self.assertFalse(diff["legibility_changed"])
        self.assertTrue(diff["diff_summary_cn"])
        self.assertEqual(result["llm_calls"], 0)

    def test_hour_validation_and_determinism(self):
        first = trial_sandbox.run_isolated_trial_hour(build_package(), hour=9)
        second = trial_sandbox.run_isolated_trial_hour(build_package(), hour=9)
        self.assertEqual(first["trial_steps_cn"], second["trial_steps_cn"])
        self.assertEqual(first["paper_note_state"], second["paper_note_state"])
        with self.assertRaises(model.WorldError):
            trial_sandbox.run_isolated_trial_hour(build_package(), hour=24)
        with self.assertRaises(model.WorldError):
            trial_sandbox.run_isolated_trial_hour(build_package(), weather="snow")

    def test_winter_night_reports_seasonal_chill_and_defaults_to_autumn(self):
        default = trial_sandbox.run_isolated_trial_hour(build_package())
        self.assertEqual(default["season"], "autumn")

        winter = trial_sandbox.run_isolated_trial_hour(
            build_package(), season="winter", hour=20
        )
        self.assertEqual(winter["season"], "winter")
        self.assertIsInstance(winter["seasonal_hint_cn"], str)
        self.assertIn("微寒", winter["seasonal_hint_cn"])
        self.assertIn("微寒", winter["microzone_summary"]["seasonal_hint_cn"])
        self.assertTrue(any("微寒" in step for step in winter["trial_steps_cn"]))
        self.assertEqual(winter["llm_calls"], 0)

    def test_invalid_season_is_a_named_refusal(self):
        with self.assertRaises(model.WorldError) as caught:
            trial_sandbox.run_isolated_trial_hour(build_package(), season="monsoon")
        self.assertEqual(caught.exception.code, "INVALID_SEASON")
        self.assertEqual(caught.exception.status, 422)


class BuilderProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        state = lived_state()
        state["matter_objects"]["lived_note"].update(
            {
                "parent_id": "home_desk",
                "subject_last_seen_parent": "home_staging_corner",
                "impression_stale": True,
                "moisture": "damp",
                "last_thermal_at": "2026-11-16T13:00:00+08:00",
                "last_moisture_at": "2026-11-16T14:20:00+08:00",
            }
        )
        state["matter_objects"]["unlogged"] = {
            "object_id": "unlogged",
            "label_cn": "悄悄挪走的小物",
            "item_kind": "everyday",
            "parent_id": "home_desk",
            "capacity_slots": 0,
            "material_units": 1,
            "assembly_role": "none",
            "moisture": "dry",
            "thermal": "room",
            "freshness": "non_perishable",
            "subject_last_seen_parent": "kitchen_counter",
            "impression_stale": True,
            "consumed": False,
            "discarded": False,
        }
        state["operations"] = {
            "op-1": {
                "fingerprint": "x",
                "receipt": {
                    "at": "2026-11-16T12:00:00+08:00",
                    "intent_cn": "把卡片先放到书桌",
                    "step_results": [
                        {"op": "PLACE_OR_MOVE", "object_id": "lived_note", "target_parent_id": "home_desk"}
                    ],
                },
            }
        }
        write_instance(self.root, package=build_package(extra_room=True), state=state)
        (self.root / "world" / "v04_neighborhood_state.json").write_text(
            json.dumps({"environment": {"weather": "rain", "airflow": "medium", "season": "autumn"}}),
            encoding="utf-8",
        )
        (self.root / "world" / "v08_microzone_state.json").write_text(
            json.dumps({"window_state": "open"}), encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_builder_provenance_is_strictly_separate_from_character_surface(self):
        result = trial_sandbox.explain_matter_provenance_for_builder(self.root, "lived_note")
        builder = result["builder_provenance"]
        character = result["character_surface_view"]

        self.assertEqual(builder["parent_zone_id"], "home_desk")
        self.assertEqual(builder["moisture"], "damp")
        self.assertEqual(builder["thermal"], "room")
        self.assertEqual(builder["last_thermal_at"], "2026-11-16T13:00:00+08:00")
        self.assertEqual(builder["last_moisture_at"], "2026-11-16T14:20:00+08:00")
        self.assertEqual(builder["subject_last_seen_parent"], "home_staging_corner")
        self.assertIs(builder["impression_stale"], True)
        self.assertEqual(builder["weather"], "rain")
        self.assertEqual(builder["window_state"], "open")
        self.assertTrue(builder["recent_acts_mentioning_object"])
        self.assertTrue(any("雨" in factor for factor in builder["weather_factors_cn"]))

        self.assertEqual(
            sorted(character),
            sorted(
                [
                    "label_cn",
                    "parent_id",
                    "moisture",
                    "thermal",
                    "note_legibility",
                    "derived_role_cn",
                ]
            ),
        )
        for hidden in (
            "last_thermal_at",
            "last_moisture_at",
            "subject_last_seen_parent",
            "impression_stale",
            "weather",
            "window_state",
            "recent_acts_mentioning_object",
        ):
            self.assertNotIn(hidden, character)
        self.assertEqual(result["zero_write"], True)
        self.assertEqual(result["llm_calls"], 0)

    def test_missing_history_is_reported_honestly_not_invented(self):
        result = trial_sandbox.explain_matter_provenance_for_builder(self.root, "unlogged")
        note = result["builder_provenance"]["missing_history_note_cn"]
        self.assertIsInstance(note, str)
        self.assertTrue(note.strip())
        self.assertIn("没有", note)
        self.assertEqual(result["builder_provenance"]["recent_acts_mentioning_object"], [])

    def test_unknown_object_is_a_named_refusal(self):
        with self.assertRaises(model.WorldError) as caught:
            trial_sandbox.explain_matter_provenance_for_builder(self.root, "missing-thing")
        self.assertEqual(caught.exception.code, "NOT_FOUND")


class LivedSafeMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_instance(
            self.root,
            package=build_package(extra_room=True),
            state=lived_state(),
        )
        self.lived_path = self.root / "world" / "v08_matter_space.json"
        self.original_lived = self.lived_path.read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_dropping_a_zone_holding_lived_matter(self):
        result = trial_sandbox.merge_package_preserving_lived_matter(
            self.root, build_package(), dry_run=True
        )
        self.assertIs(result["ok"], False)
        self.assertEqual(result["code"], "ZONE_REFERENCED_BY_LIVED_MATTER")
        self.assertIs(result["lived_state_preserved"], True)
        self.assertTrue(result["blocking_refs"])
        refs = result["blocking_refs"][0]
        self.assertEqual(refs["object_id"], "lived_note")
        self.assertIn(refs["removed_id"], {"study", "study-desk"})
        self.assertIn("带着写下的字迹", refs["modification_reasons_cn"])
        self.assertEqual(self.lived_path.read_bytes(), self.original_lived)
        self.assertEqual(result["llm_calls"], 0)

    def test_non_destructive_merge_adds_object_and_preserves_lived_state(self):
        new_package = build_package(extra_room=True, extra_object=True)
        result = trial_sandbox.merge_package_preserving_lived_matter(
            self.root, new_package, dry_run=False
        )
        self.assertIs(result["ok"], True)
        self.assertIn("new-lamp", result["added_objects"])
        self.assertGreaterEqual(result["preserved_lived_objects_count"], 1)
        self.assertIs(result["lived_state_preserved"], True)
        self.assertTrue(result["written"])

        # The lived matter file is byte-identical: split units / inscriptions /
        # placement were not reset by the merge.
        self.assertEqual(self.lived_path.read_bytes(), self.original_lived)
        lived = json.loads(self.lived_path.read_text(encoding="utf-8"))
        note = lived["matter_objects"]["lived_note"]
        self.assertEqual(note["material_units"], 3)
        self.assertEqual(note["inscribed_text_cn"], "明天买牛奶")
        self.assertEqual(note["parent_id"], "study-desk")

        written = json.loads(
            (self.root / "world-package" / "editor_package.json").read_text(encoding="utf-8")
        )
        self.assertTrue(any(o["id"] == "new-lamp" for o in written["objects"]))
        self.assertEqual(result["llm_calls"], 0)

    def test_dry_run_never_writes(self):
        before = (self.root / "world-package" / "editor_package.json").read_bytes()
        result = trial_sandbox.merge_package_preserving_lived_matter(
            self.root, build_package(extra_room=True, extra_object=True), dry_run=True
        )
        self.assertIs(result["ok"], True)
        self.assertIsNone(result["written"])
        after = (self.root / "world-package" / "editor_package.json").read_bytes()
        self.assertEqual(before, after)


class HttpEndpointsTests(unittest.TestCase):
    def setUp(self):
        self.data_tmp = tempfile.TemporaryDirectory()
        self.instance_tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.instance_tmp.name)
        write_instance(self.root, package=build_package(extra_room=True), state=lived_state())
        self.server = create_server(self.data_tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.data_tmp.cleanup()
        self.instance_tmp.cleanup()

    def post(self, path, payload):
        request = Request(
            self.base + path,
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json", "X-Nyairo-Editor": "1"},
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_trial_hour_over_http(self):
        status, body = self.post(
            "/api/nyairo/trial-hour",
            {
                "package": build_package(),
                "weather": "rain",
                "window_state": "open",
                "hour": 14,
                "compare_package": build_package(extra_room=True),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["paper_note_state"]["note_legibility"], "smudged")
        self.assertIs(body["isolated_sandbox"], True)
        self.assertIs(body["real_lived_state_touched"], False)
        self.assertEqual(body["llm_calls"], 0)
        self.assertIsNotNone(body["ab_diff"])
        self.assertGreaterEqual(body["ab_diff"]["zone_count_delta"], 1)

    def test_trial_hour_season_over_http(self):
        status, body = self.post(
            "/api/nyairo/trial-hour",
            {"package": build_package(), "season": "summer"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["season"], "summer")
        self.assertEqual(body["llm_calls"], 0)

    def test_provenance_over_http(self):
        status, body = self.post(
            "/api/nyairo/provenance",
            {"instance_root": str(self.root), "object_id": "lived_note"},
        )
        self.assertEqual(status, 200)
        self.assertIn("builder_provenance", body)
        self.assertIn("character_surface_view", body)
        self.assertNotIn("last_thermal_at", body["character_surface_view"])
        self.assertEqual(body["llm_calls"], 0)

    def test_diff_merge_conflict_is_http_409(self):
        status, body = self.post(
            "/api/nyairo/diff-merge",
            {
                "instance_root": str(self.root),
                "new_editor_package": build_package(),
                "dry_run": True,
            },
        )
        self.assertEqual(status, 409)
        self.assertIs(body["ok"], False)
        self.assertEqual(body["code"], "ZONE_REFERENCED_BY_LIVED_MATTER")

    def test_diff_merge_rejects_non_bool_dry_run(self):
        status, body = self.post(
            "/api/nyairo/diff-merge",
            {
                "instance_root": str(self.root),
                "new_editor_package": build_package(),
                "dry_run": "yes",
            },
        )
        self.assertEqual(status, 422)
        self.assertIn("dry_run", body["error"])


if __name__ == "__main__":
    unittest.main()
