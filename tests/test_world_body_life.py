#!/usr/bin/env python3
"""v0.4 world/body mechanics -- the C owner's own positive/negative checks.

This file is about *mechanism*, not about a browser: the local-owner verb
vocabulary, the measured-vs-assumed movement load, the REST command's World-side
conditions, and the body runtime's unchanged thresholds.

Everything here is the real modules (Resolver, body_consequence, body_runtime,
gateway constants, the PhoneWorld facade).  Only the World content is a fixture,
and every fixture home lives under ``/tmp/nyaw05r.jdK3q/rc1-world-body-*`` where
it is created once and **retained** (no delete, no cleanup).

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_world_body_life.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
RUNTIME_ROOT = ROOT / "components" / "world"
sys.path[:0] = [str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE)]

import body_consequence as bcon  # noqa: E402
import body_runtime as br  # noqa: E402
import world_action_resolver as war  # noqa: E402
import world_body_gateway_api as gateway  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_consumable_contract as contract  # noqa: E402
import world_daily_opportunities as opportunities  # noqa: E402
import world_foundation as wf  # noqa: E402
import world_phone_world as phone_world  # noqa: E402
import world_runtime_data as wrd  # noqa: E402
import world_travel_delivery as wtd  # noqa: E402

FIXTURE_ROOT = pathlib.Path("/tmp/nyaw05r.jdK3q")
FIXTURE_PREFIX = "rc1-world-body-"

PLACE_HOME = "home"
AREA_HOME = "bedroom"
BED = "basic_bed"
SNACK = "snack_on_desk"

ENVIRONMENT = {
    "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
    "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
    "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
    wbs.FEATURE_ENV: "1",
}

CAPABILITIES = {
    "objects": {
        SNACK: {"takeable": True, "portable": True, "placeable": True},
        BED: {"takeable": False, "portable": False, "placeable": True},
    },
    "consumables": {},
}


def unique_home() -> pathlib.Path:
    """A unique, retained fixture home (created once, never removed)."""

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    home = FIXTURE_ROOT / (FIXTURE_PREFIX + uuid.uuid4().hex[:12])
    home.mkdir(parents=True, exist_ok=False)
    for relative in ("data/world", "data/body", "state", "run"):
        (home / relative).mkdir(parents=True, exist_ok=True)
    return home


def seed_world(home: pathlib.Path, *, place: str = PLACE_HOME, area: str = AREA_HOME) -> None:
    (home / "data" / "world" / "world_state.json").write_text(
        json.dumps(
            {
                "facts": [],
                "location": {"area_id": area, "place_id": place},
                "revision": 5,
                "scene": {"revision": 5, "scene_id": "%s.%s" % (place, area)},
                "schema_version": "world.foundation.v0",
                "timezone": "Asia/Tokyo",
                "world_id": "shiomi_city",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (home / "data" / "world" / "world_timeline.json").write_text(
        json.dumps(
            {
                "schema_version": "world.timeline.v1",
                "revision": 1,
                "world_id": "shiomi_city",
                "processes": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (home / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")
    (home / "state" / "world_object_capabilities.json").write_text(
        json.dumps(
            {
                "schema_version": wbs.CAPABILITIES_SCHEMA,
                "objects": CAPABILITIES["objects"],
                "consumables": CAPABILITIES["consumables"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def seed_substrate(
    home: pathlib.Path,
    placements: dict | None = None,
    pose: str = "standing",
    orientation: str = "south",
):
    state = wbs.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
    state["pose"] = pose
    state["orientation"] = orientation
    for object_id, target in (placements or {}).items():
        state = wbs.place_object(state, object_id, target)
    wbs.SubstrateStore(wbs.substrate_path(home)).save(state)
    return state


def seed_body(home: pathlib.Path, *, fatigue: float | None = None, at=None):
    moment = at or datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
    state = br.initial_runtime(at=moment)
    if fatigue is not None:
        state["somatic"]["fatigue"] = float(fatigue)
    br.BodyStore(home / "data" / "body").save_runtime(state)
    return state


def room(location_id: str = PLACE_HOME, area_id: str = AREA_HOME) -> dict:
    return {"kind": "room", "location_id": location_id, "area_id": area_id}


def bed_surface(location_id: str = PLACE_HOME) -> dict:
    return {"kind": "surface", "location_id": location_id, "surface_id": "bed_surface"}


def first_edge(origin: str = PLACE_HOME) -> tuple[str, str, int]:
    """One real walking edge of the active world (never invented)."""

    world = wrd.active_world(ENVIRONMENT)
    for start in [origin] + sorted(world.locations):
        for target in world.reachable_of(start):
            seconds = world.route_duration_seconds(start, target)
            if isinstance(seconds, int) and seconds > 0 and start != target:
                return start, target, seconds
    raise AssertionError("the active world declares no walking edge at all")


# ---------------------------------------------------------------------------
# vocabulary: one permission statement, three consumers
# ---------------------------------------------------------------------------


class VerbVocabularyTests(unittest.TestCase):
    def test_local_owner_verbs_are_identical_across_the_three_owners(self):
        self.assertEqual(
            tuple(war.LOCAL_OWNER_ACTIONS), tuple(gateway.GATEWAY_LOCAL_OWNER_VERBS)
        )
        self.assertTrue(set(phone_world.VERBS) <= set(war.LOCAL_OWNER_ACTIONS))
        self.assertIn("REST", war.ACTIONS)
        self.assertIn("REST", gateway.GATEWAY_LOCAL_OWNER_VERBS)
        # the phone facade deliberately does not offer CONSUME (shopping owns it)
        self.assertNotIn("CONSUME", phone_world.VERBS)

    def test_resting_poses_and_modes_are_mirrored_not_redecided(self):
        self.assertEqual(tuple(war.RESTING_POSES), tuple(gateway.RESTING_POSES))
        self.assertEqual(tuple(war.REST_MODES), tuple(br.RECOVERY_MODES))
        self.assertEqual(tuple(war.REST_STATES), ("start", "stop"))

    def test_param_spec_covers_exactly_the_facade_verbs(self):
        self.assertEqual(set(phone_world.PARAM_SPEC), set(phone_world.VERBS))


# ---------------------------------------------------------------------------
# movement load: measured vs assumed
# ---------------------------------------------------------------------------


class MovementMeasurementTests(unittest.TestCase):
    def test_a_measured_walk_is_used_as_is(self):
        seconds, source = bcon._movement_measurement(
            "MOVE", {"movement_seconds": 150, "movement_source": "measured_travel"}
        )
        self.assertEqual(seconds, 150.0)
        self.assertEqual(source, "measured_travel")

    def test_no_measurement_falls_back_and_says_so(self):
        self.assertEqual(
            bcon._movement_measurement("MOVE", None),
            (600.0, "assumed_default"),
        )
        self.assertEqual(
            bcon._movement_measurement("MOVE", {"movement_seconds": None}),
            (600.0, "assumed_default"),
        )

    def test_a_bogus_measurement_is_never_dressed_up_as_one(self):
        for facts in (
            {"movement_seconds": -5, "movement_source": "measured_travel"},
            {"movement_seconds": "soon", "movement_source": "measured_travel"},
            {"movement_seconds": 120, "movement_source": "made_up_source"},
            {"movement_seconds": True, "movement_source": "measured_travel"},
        ):
            self.assertEqual(
                bcon._movement_measurement("MOVE", facts),
                (600.0, "assumed_default"),
            )

    def test_an_absurd_duration_is_bounded(self):
        seconds, source = bcon._movement_measurement(
            "MOVE", {"movement_seconds": 10**9, "movement_source": "measured_travel"}
        )
        self.assertEqual(seconds, bcon.MAX_MEASUREMENT_SECONDS)
        self.assertEqual(source, "measured_travel")

    def test_the_consequence_records_where_the_duration_came_from(self):
        result = {
            "execution_id": "phone-exec:measured",
            "action": "MOVE",
            "status": "applied",
        }
        measured = bcon.build_consequence(
            result,
            body_id="chiyo_body",
            world_facts={"movement_seconds": 150, "movement_source": "measured_travel"},
        )
        moved = [
            item for item in measured["inputs"] if item["class"] == "PHYSICAL_RESULT"
        ]
        self.assertEqual(moved[0]["movement_duration_seconds"], 150.0)
        self.assertEqual(measured["movement_measurement"]["source"], "measured_travel")
        self.assertTrue(
            any(note.startswith("movement_load_measured:") for note in measured["notes"])
        )

        assumed = bcon.build_consequence(result, body_id="chiyo_body")
        self.assertEqual(assumed["movement_measurement"]["source"], "assumed_default")
        self.assertIn(
            "movement_load_assumed:600.0s:route_duration_unavailable", assumed["notes"]
        )


# ---------------------------------------------------------------------------
# REST: the command, the one legal RECOVERY input, the unchanged thresholds
# ---------------------------------------------------------------------------


class RestConsequenceTests(unittest.TestCase):
    def _result(self, state: str, mode: str = "rest") -> dict:
        return {
            "execution_id": "phone-exec:rest-%s" % state,
            "action": "REST",
            "status": "applied",
            "recovery_state": state,
            "recovery_mode": mode,
        }

    def test_start_and_stop_become_the_one_legal_recovery_input(self):
        started = bcon.build_consequence(self._result("start", "sleep_like"),
                                         body_id="chiyo_body")
        recovery = [item for item in started["inputs"] if item["class"] == "RECOVERY"]
        self.assertEqual(recovery, [{"class": "RECOVERY", "mode": "sleep_like",
                                     "state": "start"}])
        stopped = bcon.build_consequence(self._result("stop"), body_id="chiyo_body")
        recovery = [item for item in stopped["inputs"] if item["class"] == "RECOVERY"]
        self.assertEqual(recovery, [{"class": "RECOVERY", "mode": "rest",
                                     "state": "stop"}])

    def test_the_runtime_still_owns_the_threshold(self):
        # fatigue below RECOVERY_OPEN_FATIGUE: the command does not open anything.
        state = br.initial_runtime(at=datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc))
        state["somatic"]["fatigue"] = br.RECOVERY_OPEN_FATIGUE - 0.02
        result = br.advance(
            state,
            datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 21, 0, 1, tzinfo=timezone.utc),
            [{"class": "RECOVERY", "mode": "rest", "state": "start"}],
        )
        self.assertEqual(result["recovery_after"]["state"], "inactive")
        self.assertIn("recovery_not_opened_below_threshold", result["notes"])

    def test_an_open_session_is_interrupted_by_real_load(self):
        state = br.initial_runtime(at=datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc))
        state["somatic"]["fatigue"] = 0.60
        opened = br.advance(
            state,
            datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 21, 0, 1, tzinfo=timezone.utc),
            [{"class": "RECOVERY", "mode": "rest", "state": "start"}],
        )
        self.assertEqual(opened["recovery_after"]["state"], "active")
        interrupted = br.advance(
            opened["state"],
            datetime(2026, 10, 21, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 21, 0, 3, tzinfo=timezone.utc),
            [
                {
                    "class": "PHYSICAL_RESULT",
                    "movement_duration_seconds": 150.0,
                    "effort": "moderate",
                    "pose": "standing",
                }
            ],
        )
        self.assertEqual(interrupted["recovery_after"]["state"], "interrupted")
        self.assertEqual(interrupted["recovery_after"]["trend"], "worsening")
        self.assertIn("recovery_interrupted_by_physical_load", interrupted["notes"])

    def test_a_timed_departure_is_recorded_pending_and_charged_later(self):
        home = unique_home()
        ledger = bcon.ConsequenceLedger(home / "data" / "body" / "consequences.jsonl")
        runtime = br.initial_runtime(at=datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc))
        runtime["somatic"]["fatigue"] = 0.20
        result = {
            "execution_id": "phone-exec:depart",
            "action": "MOVE",
            "status": "applied",
            "reason_code": "move_departed",
        }
        deferred = bcon.defer_for_execution(
            result,
            body_id="chiyo_body",
            ledger=ledger,
            at="2026-10-21T00:00:00+00:00",
        )
        self.assertTrue(deferred["deferred"])
        records = ledger.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "pending")
        self.assertEqual(records[0]["inputs"], [])
        self.assertEqual(records[0]["movement_settlement"], "pending")
        self.assertIn(bcon.MOVEMENT_DEFERRED_NOTE, records[0]["notes"])
        # replaying the departure never creates a second record
        again = bcon.defer_for_execution(
            result,
            body_id="chiyo_body",
            ledger=ledger,
            at="2026-10-21T00:00:00+00:00",
        )
        self.assertFalse(again["deferred"])
        self.assertEqual(len(ledger.read_all()), 1)
        self.assertEqual(len(bcon.pending_movements(ledger)), 1)

        # arrival settles the same execution once, with the measured seconds
        applied = bcon.apply_for_execution(
            {
                "execution_id": "phone-exec:depart",
                "action": "MOVE",
                "status": "applied",
            },
            body_id="chiyo_body",
            ledger=ledger,
            runtime_state=runtime,
            at=datetime(2026, 10, 21, 0, 3, tzinfo=timezone.utc),
            world_facts={"movement_seconds": 150, "movement_source": "measured_travel"},
        )
        self.assertTrue(applied["applied"])
        self.assertEqual(bcon.pending_movements(ledger), [])
        latest = ledger.current_state(ledger.read_all())[
            records[0]["consequence_id"]
        ]
        self.assertEqual(latest["state"], "applied")
        self.assertEqual(latest["movement_measurement"]["source"], "measured_travel")


class ServiceMeasurementTests(unittest.TestCase):
    def test_measured_seconds_is_the_minimum_of_elapsed_and_declared(self):
        started = "2026-10-21T00:00:00+00:00"
        now = datetime(2026, 10, 21, 0, 5, tzinfo=timezone.utc)
        short = {"started_at": started, "arrived_at": "2026-10-21T00:01:30+00:00",
                 "route_duration_seconds": 150}
        self.assertEqual(wbsvc.WorldBodyService._measured_seconds(short, now), 90.0)
        late = {"started_at": started, "arrived_at": "2026-10-21T01:00:00+00:00",
                "route_duration_seconds": 150}
        self.assertEqual(wbsvc.WorldBodyService._measured_seconds(late, now), 150.0)

    def test_no_readable_numbers_means_no_measurement(self):
        self.assertIsNone(
            wbsvc.WorldBodyService._measured_seconds({"started_at": "nope"}, None)
        )


# ---------------------------------------------------------------------------
# the Resolver's REST verb (World side only)
# ---------------------------------------------------------------------------


class ResolverRestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        for key, value in ENVIRONMENT.items():
            os.environ[key] = value
        self.home = unique_home()
        seed_world(self.home)
        seed_substrate(self.home, {SNACK: room(), BED: bed_surface()},
                       pose="standing")
        self.resolver = war.WorldResolver(
            self.home, environment=dict(ENVIRONMENT), now=lambda: datetime(
                2026, 10, 21, 12, 0, tzinfo=timezone.utc
            )
        )

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def proposal(self, params: dict, execution_id: str) -> dict:
        return {
            "action": "REST",
            "execution_id": execution_id,
            "actor_id": "chiyo",
            "body_id": "chiyo_body",
            "authority": war.AUTHORITY_LOCAL_OWNER_PHONE,
            "confirm_apply": True,
            "observed_dependencies": self.resolver._dependencies(),
            "params": params,
        }

    def set_pose(self, pose: str) -> None:
        store = wbs.SubstrateStore(wbs.substrate_path(self.home))
        live = store.load()
        store.save(wbs.set_pose(live, pose), expected_revision=live["revision"])

    def test_standing_cannot_rest(self):
        result = self.resolver.resolve(self.proposal({"state": "start"}, "exec-rest-1"))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], war.CODE_NOT_ALLOWED)
        self.assertTrue(
            result["legacy_reason_code"].startswith("REST_REQUIRES_RESTING_POSE")
        )

    def test_seated_can_open_and_close_a_session(self):
        self.set_pose("seated")
        opened = self.resolver.resolve(
            self.proposal({"state": "start"}, "exec-rest-open")
        )
        self.assertEqual(opened["status"], "applied", opened)
        self.assertEqual(opened["recovery_state"], "start")
        self.assertEqual(opened["recovery_mode"], "rest")
        self.assertEqual(opened["ledger_status"], "committed")
        self.assertEqual(opened["local_owner"]["source"], "local_owner_phone")

        frame = self.resolver.ledger.frame("exec-rest-open")
        raw = frame["records"][-1]["raw"]
        self.assertIn("recovery:start:rest", raw["world_mutation_refs"])
        self.assertTrue(
            any(ref.startswith("world.local_owner_phone:")
                for ref in raw["cross_authority_refs"])
        )

        # same execution replayed: already applied, no second commit
        replay = self.resolver.resolve(
            self.proposal({"state": "start"}, "exec-rest-open")
        )
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(replay["reason_code"], war.CODE_ALREADY_APPLIED)

        closed = self.resolver.resolve(
            self.proposal({"state": "stop"}, "exec-rest-close")
        )
        self.assertEqual(closed["status"], "applied")
        self.assertEqual(closed["recovery_state"], "stop")
        raw = self.resolver.ledger.frame("exec-rest-close")["records"][-1]["raw"]
        self.assertIn("recovery:stop", raw["world_mutation_refs"])

    def test_sleep_like_needs_a_lying_pose_and_a_real_bed_here(self):
        self.set_pose("seated")
        not_lying = self.resolver.resolve(
            self.proposal({"state": "start", "mode": "sleep_like"}, "exec-sleep-1")
        )
        self.assertEqual(not_lying["status"], "rejected")
        self.assertTrue(
            not_lying["legacy_reason_code"].startswith("SLEEP_LIKE_REQUIRES_LYING_POSE")
        )

        self.set_pose("lying")
        # the bed object is a placement; remove it and the surface is gone
        store = wbs.SubstrateStore(wbs.substrate_path(self.home))
        live = store.load()
        without_bed = dict(live)
        without_bed["placements"] = {
            key: value for key, value in live["placements"].items() if key != BED
        }
        store.save(without_bed, expected_revision=live["revision"])
        no_bed = self.resolver.resolve(
            self.proposal({"state": "start", "mode": "sleep_like"}, "exec-sleep-2")
        )
        self.assertEqual(no_bed["status"], "rejected")
        self.assertTrue(
            no_bed["legacy_reason_code"].startswith("SLEEP_LIKE_REQUIRES_REST_SURFACE")
        )

    def test_sleep_like_with_the_bed_present_is_accepted(self):
        self.set_pose("lying")
        result = self.resolver.resolve(
            self.proposal({"state": "start", "mode": "sleep_like"}, "exec-sleep-3")
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["recovery_mode"], "sleep_like")
        self.assertEqual(result["rest_surface_id"], "bed_surface")

    def test_rest_is_refused_while_walking(self):
        self.set_pose("seated")
        start, destination, _seconds = first_edge(PLACE_HOME)
        # the fixture home must be the edge's origin for a readable travel record
        if start != PLACE_HOME:
            self.skipTest("fixture home has no walking edge in this world")
        with wtd._world_lock(self.home / "data" / "world" / "world_state.json"):
            seeded = wtd.start_travel(
                execution_id="world-exec-rest-travel-1",
                origin_location_id=PLACE_HOME,
                destination_location_id=destination,
                hermes_home=self.home,
                environment=dict(ENVIRONMENT),
                now=datetime(2026, 10, 21, 11, 59, tzinfo=timezone.utc),
            )
        self.assertIn(seeded["status"], ("started", "already_started"), seeded)
        result = self.resolver.resolve(self.proposal({"state": "start"}, "exec-rest-travel"))
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            result["legacy_reason_code"].startswith("REST_WHILE_TRAVELLING")
        )

    def test_mode_is_only_accepted_with_start(self):
        self.set_pose("seated")
        result = self.resolver.resolve(
            self.proposal({"state": "stop", "mode": "rest"}, "exec-rest-mode")
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], war.CODE_INVALID_PROPOSAL)

# ---------------------------------------------------------------------------
# PLACE: the room a hand can put something down in
# ---------------------------------------------------------------------------


class ResolverPlaceAreaTests(unittest.TestCase):
    """PLACE is a put-down, not a teleport: the room must be the one we are in.

    The regression this guards is a *live* one: the rc1 handover world holds a
    bought item in ``left_hand`` while standing in ``home/bedroom``, and PLACE
    accepted *any* room of the same building -- ``home/kitchen`` included -- as
    a destination.  Nothing here widens a gate: an unknown object, a room the
    body is not in, an unreachable place and an undeclared surface all stay
    refused, and a refusal writes neither substrate nor ledger.
    """

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        for key, value in ENVIRONMENT.items():
            os.environ[key] = value
        self.home = unique_home()
        seed_world(self.home)
        seed_substrate(
            self.home,
            {SNACK: {"kind": "hand", "slot_id": "left_hand"}, BED: bed_surface()},
            pose="standing",
        )
        self.resolver = war.WorldResolver(
            self.home,
            environment=dict(ENVIRONMENT),
            now=lambda: datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    # -- helpers --------------------------------------------------------
    def proposal(self, params: dict, execution_id: str) -> dict:
        return {
            "action": "PLACE",
            "execution_id": execution_id,
            "actor_id": "chiyo",
            "body_id": "chiyo_body",
            "authority": war.AUTHORITY_LOCAL_OWNER_PHONE,
            "confirm_apply": True,
            "observed_dependencies": self.resolver._dependencies(),
            "params": params,
        }

    def place(self, target: dict, execution_id: str, object_id: str = SNACK) -> dict:
        return self.resolver.resolve(
            self.proposal({"object_id": object_id, "target": target}, execution_id)
        )

    def substrate(self) -> dict:
        return json.loads(
            wbs.substrate_path(self.home).read_text(encoding="utf-8")
        )

    def substrate_bytes(self) -> bytes:
        return wbs.substrate_path(self.home).read_bytes()

    def ledger_rows(self) -> list[dict]:
        path = self.home / "data" / "life_execution.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def set_world_area(self, area_id: str) -> None:
        path = self.home / "data" / "world" / "world_state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["location"]["area_id"] = area_id
        path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

    # -- the legal case -------------------------------------------------
    def test_the_room_the_body_is_in_accepts_the_held_object(self):
        result = self.place(room(), "exec-place-here")
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["ledger_status"], "committed")
        self.assertEqual(self.substrate()["placements"][SNACK], room())
        self.assertEqual(wbs.slot_occupant(self.substrate(), "left_hand"), None)

    # -- the refusal, and that it really writes nothing ------------------
    def test_another_area_is_refused_and_changes_nothing(self):
        before = self.substrate_bytes()
        rows = len(self.ledger_rows())
        result = self.place(room(PLACE_HOME, "kitchen"), "exec-place-far")
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["reason_code"], war.CODE_NOT_REACHABLE)
        self.assertTrue(
            result["legacy_reason_code"].startswith("NOT_CURRENT_AREA"),
            result["legacy_reason_code"],
        )
        self.assertEqual(self.substrate_bytes(), before, "no substrate write")
        self.assertEqual(len(self.ledger_rows()), rows, "no ledger entry")
        self.assertEqual(
            self.substrate()["placements"][SNACK],
            {"kind": "hand", "slot_id": "left_hand"},
            "the object stays in hand",
        )

    def test_a_room_target_without_an_area_is_refused_before_anything_moves(self):
        # The substrate contract needs a room target to name kind/place/area;
        # an unplaced target is refused before anything is written.
        before = self.substrate_bytes()
        result = self.place(room(PLACE_HOME, ""), "exec-place-blank-area")
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["reason_code"], war.CODE_INVALID_PROPOSAL)
        self.assertEqual(wbs.slot_occupant(self.substrate(), "left_hand"), SNACK)
        self.assertEqual(self.substrate_bytes(), before)

    # -- the rule follows the World's own state, not a constant ----------
    def test_the_rule_reads_the_worlds_area_not_a_constant(self):
        self.set_world_area("kitchen")
        refused = self.place(room(PLACE_HOME, AREA_HOME), "exec-place-old-area")
        self.assertEqual(refused["status"], "rejected", refused)
        self.assertTrue(
            refused["legacy_reason_code"].startswith("NOT_CURRENT_AREA"),
            refused["legacy_reason_code"],
        )
        applied = self.place(room(PLACE_HOME, "kitchen"), "exec-place-new-area")
        self.assertEqual(applied["status"], "applied", applied)
        self.assertEqual(self.substrate()["placements"][SNACK], room(PLACE_HOME, "kitchen"))

    # -- the reason is a code, never a shrug ----------------------------
    def test_an_object_with_no_placement_is_a_code_not_a_shrug(self):
        # Exactly what the phone UI sent live: the display label of the held
        # item (its 64-hex run redacted by the web layer) instead of the id.
        masked = "item-[已隐藏的长标识]"
        before = self.substrate_bytes()
        result = self.place(room(), "exec-place-masked", object_id=masked)
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["reason_code"], war.CODE_UNKNOWN_TARGET)
        self.assertTrue(
            result["legacy_reason_code"].startswith("UNKNOWN_TARGET"),
            result["legacy_reason_code"],
        )
        self.assertEqual(phone_world._reason_of(result), "UNKNOWN_TARGET")
        self.assertNotEqual(phone_world._reason_of(result), "UNKNOWN")
        self.assertEqual(self.substrate_bytes(), before)

    def test_the_facade_no_longer_turns_a_real_reason_into_unknown(self):
        payload = {
            "reason_code": "UNKNOWN_TARGET",
            "legacy_reason_code": "UNKNOWN: item-x has no placement",
        }
        self.assertEqual(phone_world._reason_of(payload), "UNKNOWN_TARGET")
        # a specific legacy spelling still wins over the canonical code
        self.assertEqual(
            phone_world._reason_of(
                {"reason_code": "NOT_ALLOWED",
                 "legacy_reason_code": "REST_WHILE_TRAVELLING: travel home -> park"}
            ),
            "REST_WHILE_TRAVELLING",
        )

    # -- one execution id is one put-down -------------------------------
    def test_one_execution_id_places_exactly_once(self):
        first = self.place(room(), "exec-place-replay")
        self.assertEqual(first["status"], "applied", first)
        after = self.substrate_bytes()
        replayed = self.place(room(), "exec-place-replay")
        self.assertEqual(replayed["status"], "already_applied", replayed)
        self.assertEqual(self.substrate_bytes(), after, "replay wrote nothing")
        self.assertEqual(
            len([row for row in self.ledger_rows()
                 if row.get("execution_id") == "exec-place-replay"]),
            2,
            "prepared + committed, and the replay added no third record",
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
