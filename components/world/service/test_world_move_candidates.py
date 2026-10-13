#!/usr/bin/env python3
"""B6-1 / B6-3: the initialised World is readable, and MOVE candidates are real.

Two independent claims are pinned here:

* **B6-1** — a newly initialised isolated World is written with the timezone the
  World Living Skeleton declares, so observation and MOVE planning work at all.
  Before this, the initialiser wrote a different zone, ``_load_state`` rejected
  the state, and *every* MOVE came back ``WORLD_NOT_INITIALIZED``.
* **B6-3** — ``build_action_space()`` produces MOVE candidates, and they come from
  the World's own reachable relation at the current place.  The first acceptance
  scenario is the real pair ``home -> nearby_street``.

Nothing here touches a live World: every home is a fresh temp directory.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import unittest

import conftest  # noqa: F401  (path setup)

import action_intent as ai
import world_body_config as wbc
import world_body_service as wbsvc
import world_body_substrate as wbsub
import world_living_skeleton as wls

RUNTIME_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = RUNTIME_ROOT.parents[1]

#: The World's own location catalog, read through its public accessor.
CATALOG = {item["location_id"]: item for item in wls.location_catalog()}

SKELETON_ON = {wls.FEATURE_ENV: "1"}


def load_world_read_service():
    """Import ``chiyo_bundle/world_read_service.py`` (the initialiser under test)."""

    path = REPO_ROOT / "chiyo_bundle" / "world_read_service.py"
    spec = importlib.util.spec_from_file_location("chiyo_bundle_world_read_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_world_home(root: pathlib.Path, *, place_id: str = "home", revision: int = 5,
                    timezone_name: str = wls.WORLD_TIMEZONE) -> pathlib.Path:
    """Write one canonical World state, exactly as the initialiser would."""

    (root / "data" / "world").mkdir(parents=True, exist_ok=True)
    area_id = CATALOG[place_id]["area_id"]
    (root / "data" / "world" / "world_state.json").write_text(
        json.dumps({
            "facts": [],
            "location": {"area_id": area_id, "place_id": place_id},
            "revision": revision,
            "scene": {"revision": revision, "scene_id": "%s.%s" % (place_id, area_id)},
            "schema_version": "world.foundation.v0",
            "timezone": timezone_name,
            "world_id": wls.WORLD_ID,
        }, sort_keys=True),
        encoding="utf-8",
    )
    (root / "data" / "world" / "world_timeline.json").write_text(
        json.dumps({"schema_version": "world.timeline.v1", "revision": 1,
                    "world_id": wls.WORLD_ID, "processes": []}, sort_keys=True),
        encoding="utf-8",
    )
    (root / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")
    (root / "run").mkdir(parents=True, exist_ok=True)
    return root


def seed_substrate(config) -> None:
    """Seed absent embodiment through its canonical writer, as the read port does."""

    store = wbsub.SubstrateStore(wbsub.substrate_path(config.world_home))
    if store.load() is None:
        store.save(wbsub.initial_substrate(world_id=config.expected_world_id))


class InitialiserTimezoneTests(unittest.TestCase):
    """B6-1: the initialiser must agree with the World skeleton's timezone."""

    def setUp(self):
        self._saved_env = dict(os.environ)
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="b6-init-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "world-home"
        self.module = load_world_read_service()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_initialiser_writes_the_skeleton_timezone(self):
        self.module.initialize(str(self.home))
        state = json.loads(
            (self.home / "data" / "world" / "world_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["timezone"], wls.WORLD_TIMEZONE)
        self.assertEqual(state["world_id"], wls.WORLD_ID)
        self.assertEqual(state["location"]["place_id"], "home")

    def test_a_freshly_initialised_world_is_observable_and_plannable(self):
        """The real consequence of the timezone: observation and MOVE planning."""

        self.module.initialize(str(self.home))

        observation = wls.observe_world(hermes_home=self.home, environment=SKELETON_ON)
        self.assertNotEqual(observation.get("reason_code"), "WORLD_NOT_INITIALIZED")
        self.assertEqual(observation["current_location"]["location_id"], "home")
        reachable = [item["location_id"] for item in observation["reachable_locations"]]
        self.assertEqual(reachable, ["nearby_street"])

        plan = wls.plan_move_to(
            "nearby_street", hermes_home=self.home, environment=SKELETON_ON
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["reason_code"], "REACHABLE")
        self.assertEqual(plan["destination"]["location_id"], "nearby_street")

    def test_a_foreign_timezone_still_fails_closed(self):
        """The guard itself is load-bearing: a wrong zone is refused, not repaired."""

        seed_world_home(self.home, timezone_name="Asia/Shanghai")
        plan = wls.plan_move_to(
            "nearby_street", hermes_home=self.home, environment=SKELETON_ON
        )
        self.assertEqual(plan["status"], "unavailable")
        self.assertEqual(plan["reason_code"], "WORLD_NOT_INITIALIZED")


class MoveCandidateTests(unittest.TestCase):
    """B6-3: MOVE candidates are the World's own reachable neighbours."""

    def setUp(self):
        self._saved_env = dict(os.environ)
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="b6-cand-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)

    def _service(self, place_id: str = "home"):
        home = seed_world_home(self.tmp / ("home-%s" % place_id), place_id=place_id)
        config = wbc.load_config({}, runtime_root=RUNTIME_ROOT, world_home=home,
                                 runtime_mode="canonical", authority="production",
                                 world_id=wls.WORLD_ID)
        seed_substrate(config)
        service = wbsvc.WorldBodyService(config, instance_id="b6-cand")
        self.addCleanup(service.shutdown)
        service.startup()
        return service

    @staticmethod
    def _move_candidates(surface):
        for entry in surface.get_available_actions()["actions"]:
            if entry["verb"] == "MOVE":
                return entry["candidates"]
        raise AssertionError("no MOVE entry in the gateway action list")

    def test_at_home_the_only_reachable_place_is_the_street(self):
        service = self._service("home")
        candidates = self._move_candidates(service.gateway_surface)
        self.assertEqual([c["location_id"] for c in candidates], ["nearby_street"])
        self.assertEqual(candidates[0]["label"], CATALOG["nearby_street"]["name"])
        self.assertEqual(candidates[0]["kind"], "street")

    def test_the_candidates_follow_the_real_world_state(self):
        """Position B: the same surface, a different real place, different options."""

        service = self._service("nearby_street")
        candidates = self._move_candidates(service.gateway_surface)
        self.assertEqual(
            sorted(c["location_id"] for c in candidates),
            sorted(CATALOG["nearby_street"]["reachable"]),
        )
        offered = [c["location_id"] for c in candidates]
        self.assertIn("home", offered)
        self.assertIn("school", offered)
        # ...and the street is never offered as a destination from itself
        self.assertNotIn("nearby_street", offered)

    def test_the_action_space_projects_them_as_selectable_candidates(self):
        service = self._service("home")
        surface = service.gateway_surface
        snapshot = surface.get_snapshot()
        os.environ[ai.GATE_ENV] = ai.GATE_CANONICAL
        space = ai.build_action_space(
            snapshot, available_actions=surface.get_available_actions()
        )
        moves = [c for c in space["candidates"] if c["verb"] == "MOVE"]
        self.assertEqual([c["candidate_id"] for c in moves], ["move:nearby_street"])
        # the Resolver's own parameter name, so the candidate is submittable
        self.assertEqual(moves[0]["params"], {"destination_location_id": "nearby_street"})
        self.assertIn("MOVE", space["exposed_verbs"])

    def test_a_closed_gate_offers_no_move_candidate(self):
        service = self._service("home")
        surface = service.gateway_surface
        snapshot = surface.get_snapshot()
        os.environ[ai.GATE_ENV] = ai.GATE_OFF
        space = ai.build_action_space(
            snapshot, available_actions=surface.get_available_actions()
        )
        self.assertEqual([c for c in space["candidates"] if c["verb"] == "MOVE"], [])
        self.assertNotIn("MOVE", space["exposed_verbs"])

    def test_the_projection_keeps_the_raw_world_revision_out_of_the_selector_view(self):
        service = self._service("home")
        surface = service.gateway_surface
        os.environ[ai.GATE_ENV] = ai.GATE_CANONICAL
        space = ai.build_action_space(
            surface.get_snapshot(), available_actions=surface.get_available_actions()
        )
        redacted = ai.redact_action_space_for_selector(space)
        self.assertNotIn("world_revision", redacted)
        self.assertIn("move:nearby_street",
                      [c["candidate_id"] for c in redacted["candidates"]])


if __name__ == "__main__":
    unittest.main()
