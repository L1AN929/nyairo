#!/usr/bin/env python3
"""v0.4 phone world -- the owner path, end to end (real service, real socket).

Everything here is the *real* local stack: the one World state, the one body
substrate, the real ``WorldBodyService`` and its real gateway socket, the real
Resolver/ledger/authorization registry, and the real ``PhoneWorld`` facade.  Only
the World content (which fixture object is declared takeable, where the bed is)
is a fixture, and every fixture home lives under
``/tmp/nyaw05r.jdK3q/rc1-world-body-*`` where it is created once and **retained**
(no delete, no cleanup, no real-world side effect).

Asserted here, in contract order
--------------------------------
* the local-owner identity (uid/subject/home) is server-side and cannot be forged;
* ``snapshot()``/``operation()`` write **nothing** (byte-for-byte, mtime included);
* a timed MOVE reports a *departure* (position unchanged) and defers the walk's
  load; the owner tick realizes the arrival and settles the measured load exactly
  once;
* one ``operation_key`` names one operation: replay is ``already_applied``, a
  different action or target is refused, and a phone-shopping key cannot be reused;
* PICK_UP/PLACE/POSE are legal only through capability + reachability + ownership;
* REST needs the Body's own threshold, really opens a session, advances on the
  tick, refuses a second session, stops on command, and keeps an open session
  across a restart; a real physical load interrupts it.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_phone_world_owner.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import threading
import time
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
import world_body_config as wbc  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_execution_ledger as ledger  # noqa: E402
import world_phone_shopping as phone_shopping  # noqa: E402
import world_phone_world as phone_world  # noqa: E402
import world_property_economy as wpe  # noqa: E402
import world_runtime_data as wrd  # noqa: E402
import world_travel_delivery as wtd  # noqa: E402

FIXTURE_ROOT = pathlib.Path("/tmp/nyaw05r.jdK3q")
FIXTURE_PREFIX = "rc1-world-body-"

PLACE_HOME = "home"
AREA_HOME = "bedroom"
BED = "basic_bed"
SNACK = "snack_on_desk"

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

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


#: The fixture clock is "now": the World's own travel records are stamped by the
#: owner's clock, so the service's injected clock has to agree with it.
T0 = datetime.now(timezone.utc).replace(microsecond=0)


def unique_home() -> pathlib.Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    home = FIXTURE_ROOT / (FIXTURE_PREFIX + uuid.uuid4().hex[:12])
    home.mkdir(parents=True, exist_ok=False)
    for relative in ("data/world", "data/body", "state", "run"):
        (home / relative).mkdir(parents=True, exist_ok=True)
    return home


def seed_world(home: pathlib.Path) -> None:
    (home / "data" / "world" / "world_state.json").write_text(
        json.dumps(
            {
                "facts": [],
                "location": {"area_id": AREA_HOME, "place_id": PLACE_HOME},
                "revision": 5,
                "scene": {"revision": 5, "scene_id": "%s.%s" % (PLACE_HOME, AREA_HOME)},
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


def seed_substrate(home: pathlib.Path, pose: str = "standing", *, bed: bool = True):
    state = wbs.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
    state["pose"] = pose
    state = wbs.place_object(
        state, SNACK, {"kind": "room", "location_id": PLACE_HOME, "area_id": AREA_HOME}
    )
    if bed:
        state = wbs.place_object(
            state, BED, {"kind": "surface", "location_id": PLACE_HOME,
                         "surface_id": "bed_surface"}
        )
    wbs.SubstrateStore(wbs.substrate_path(home)).save(state)
    return state


def seed_body(home: pathlib.Path, fatigue: float = 0.15, at: datetime = T0):
    state = br.initial_runtime(at=at)
    state["somatic"]["fatigue"] = float(fatigue)
    br.BodyStore(home / "data" / "body").save_runtime(state)
    return state


def walking_edge(origin: str = PLACE_HOME) -> tuple[str, str, int]:
    world = wrd.active_world(ENVIRONMENT)
    for start in [origin] + sorted(world.locations):
        for target in world.reachable_of(start):
            seconds = world.route_duration_seconds(start, target)
            if isinstance(seconds, int) and seconds > 0 and start != target:
                return start, target, seconds
    raise AssertionError("the active world declares no walking edge at all")


def file_fingerprint(home: pathlib.Path) -> dict[str, tuple[int, int, str]]:
    """``path -> (mtime_ns, size, sha256)`` for every regular file under ``home``."""

    fingerprint: dict[str, tuple[int, int, str]] = {}
    for path in sorted(home.rglob("*")):
        if not path.is_file():
            continue
        try:
            payload = path.read_bytes()
            info = path.stat()
        except OSError:
            continue
        fingerprint[str(path.relative_to(home))] = (
            info.st_mtime_ns,
            info.st_size,
            hashlib.sha256(payload).hexdigest(),
        )
    return fingerprint


class PhoneWorldBase(unittest.TestCase):
    """Real service + real gateway socket + real stores, one retained home."""

    pose = "standing"
    fatigue = 0.15
    bed = True

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = wpe.OWNER
        for key, value in ENVIRONMENT.items():
            os.environ[key] = value
        self.home = unique_home()
        seed_world(self.home)
        seed_substrate(self.home, pose=self.pose, bed=self.bed)
        seed_body(self.home, fatigue=self.fatigue)
        self.service = None
        self._thread = None
        self.now = T0

    def tearDown(self) -> None:
        if self.service is not None:
            try:
                self.service.shutdown()
            except Exception:  # noqa: BLE001 - teardown must never mask a failure
                pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        os.environ.clear()
        os.environ.update(self._saved_env)

    # -- plumbing -------------------------------------------------------
    def config(self):
        return wbc.load_config(
            dict(ENVIRONMENT),
            runtime_root=RUNTIME_ROOT,
            world_home=self.home,
            runtime_mode="canonical",
            authority="production",
            world_id="shiomi_city",
        )

    def socket_path(self) -> pathlib.Path:
        return self.home / "run" / "gateway.sock"

    def start_server(self, *, moment: datetime | None = None):
        """Run the real service loop in a daemon thread (no auto-start anywhere)."""

        self.now = moment or self.now
        service = wbsvc.WorldBodyService(
            self.config(), now=self.now, instance_id="rc1-world-body", tick_seconds=0.05
        )
        service.startup()
        self.service = service
        thread = threading.Thread(
            target=service.serve, kwargs={"max_seconds": 120.0}, daemon=True
        )
        thread.start()
        self._thread = thread
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.socket_path().exists():
                return service
            time.sleep(0.02)
        self.fail("gateway socket never appeared at %s" % self.socket_path())

    def advance_to(self, moment: datetime) -> None:
        """Move the service's own clock forward (mechanism proof, not wall clock)."""

        self.now = moment
        if self.service is not None:
            self.service._now = moment

    def phone(self, **overrides):
        kwargs = {
            "local_owner_uid": os.getuid(),
            "subject": wpe.OWNER,
            "gateway_socket": self.socket_path(),
        }
        kwargs.update(overrides)
        return phone_world.PhoneWorld(str(self.home), dict(ENVIRONMENT), **kwargs)

    # -- owner reads ----------------------------------------------------
    def world_state(self) -> dict:
        return json.loads(
            (self.home / "data" / "world" / "world_state.json").read_text(encoding="utf-8")
        )

    def current_node(self) -> str:
        state = self.world_state()
        world = wrd.active_world(ENVIRONMENT)
        return world.state_node(
            state["location"]["place_id"], state["location"].get("area_id")
        )

    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.home)).load()

    def consequences(self) -> list[dict]:
        path = self.home / "data" / "body" / "consequences.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def consequences_for(self, execution_id: str) -> list[dict]:
        return [
            record
            for record in self.consequences()
            if str(record.get("execution_id")) == str(execution_id)
        ]

    def travels(self) -> list[dict]:
        path = self.home / "data" / "world" / "world_travel_delivery.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("travels") or []

    def body_runtime(self) -> dict:
        return json.loads(
            (self.home / "data" / "body" / "runtime.json").read_text(encoding="utf-8")
        )

    def settle(self, predicate, *, timeout: float = 8.0) -> bool:
        """Drive the owner tick until ``predicate()`` holds.

        The tick is the *only* writer of an arrival and of a recovery advance, so
        waiting for it here is the real mechanism, not a sleep.
        """

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.service is not None:
                try:
                    self.service.tick()
                except Exception:  # noqa: BLE001 - a busy tick is simply retried
                    pass
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()


# ---------------------------------------------------------------------------
# identity and zero-write reads
# ---------------------------------------------------------------------------


class IdentityTests(PhoneWorldBase):
    def test_an_unbound_owner_reads_and_writes_nothing(self):
        before = file_fingerprint(self.home)
        cases = [
            (
                {"local_owner_uid": os.getuid() + 1},
                "LOCAL_OWNER_UID_MISMATCH",
            ),
            ({"subject": "someone_else"}, "LOCAL_OWNER_SUBJECT_MISMATCH"),
        ]
        for overrides, expected in cases:
            phone = self.phone(**overrides)
            for envelope in (
                phone.snapshot(),
                phone.act("POSE", {"pose": "seated"}, "k-unbound"),
                phone.operation("k-unbound"),
            ):
                self.assertFalse(envelope["ok"], envelope)
                self.assertEqual(envelope["status"], "rejected")
                self.assertEqual(envelope["reason_code"], expected)
        relative = phone_world.PhoneWorld(
            "not/absolute",
            dict(ENVIRONMENT),
            local_owner_uid=os.getuid(),
            subject=wpe.OWNER,
        )
        self.assertEqual(
            relative.snapshot()["reason_code"], "LOCAL_OWNER_HOME_MISMATCH"
        )
        self.assertEqual(before, file_fingerprint(self.home))


class SnapshotReadTests(PhoneWorldBase):
    def test_the_snapshot_has_the_nine_blocks_and_writes_nothing(self):
        self.start_server()
        phone = self.phone()
        first = phone.snapshot()
        self.assertTrue(first["ok"], first)
        data = first["data"]
        self.assertEqual(
            set(data),
            {
                "world",
                "location",
                "local_environment",
                "destinations",
                "travel",
                "objects",
                "body",
                "actions",
                "recent_executions",
            },
        )
        self.assertEqual(data["world"]["status"], "available")
        # shiomi_city declares no indoor navigation, so the canonical node is the
        # place itself; the area is still reported separately.
        self.assertEqual(data["location"]["node"], "home")
        self.assertEqual(data["location"]["area_id"], "bedroom")
        self.assertEqual(data["objects"]["status"], "available")
        self.assertIn(SNACK, [item["object_id"] for item in data["objects"]["here"]])
        self.assertEqual(data["body"]["pose"], "standing")
        self.assertFalse(data["body"]["raw_values_exposed"])
        self.assertIn("REST", [verb["verb"] for verb in data["actions"]["verbs"]])

        before = file_fingerprint(self.home)
        for _ in range(2):
            self.assertTrue(phone.snapshot()["ok"])
            self.assertEqual(phone.operation("never-used-key")["status"], "not_found")
        self.assertEqual(before, file_fingerprint(self.home))

    def test_body_projection_is_bands_only_and_has_no_raw_values(self):
        self.start_server()
        body = self.phone().snapshot()["data"]["body"]
        blob = json.dumps(body, ensure_ascii=False)
        for variable in br.SOMATIC_VARIABLES:
            self.assertNotIn('"%s"' % variable, blob)
        self.assertIn(body["status"], ("available", "partial", "unavailable"))
        self.assertIn("rest_evidence", body)


# ---------------------------------------------------------------------------
# the walk: departure now, measured load on arrival
# ---------------------------------------------------------------------------


class MoveTests(PhoneWorldBase):
    def setUp(self) -> None:
        super().setUp()
        self.start_node, self.target, self.seconds = walking_edge(PLACE_HOME)
        if self.start_node != PLACE_HOME:
            self.skipTest("fixture home has no walking edge in this world")
        self.start_server()

    def test_departure_does_not_move_or_charge_the_walk(self):
        phone = self.phone()
        envelope = phone.act(
            "MOVE", {"destination_location_id": self.target}, "k-move-1"
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["status"], "applied", envelope)
        data = envelope["data"]
        self.assertEqual(data["reason_code"], "move_departed")
        self.assertEqual(data["travel"]["status"], "in_progress")
        self.assertEqual(data["travel"]["destination_location_id"], self.target)
        self.assertTrue(data["world"]["location_unchanged"])
        self.assertEqual(self.current_node(), PLACE_HOME)
        self.assertEqual(data["body"]["movement_settlement"], "pending")
        self.assertFalse(data["body"]["applied_to_body"])
        # exactly one consequence, and it is owed - not paid
        records = self.consequences_for(data["execution_id"])
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["state"], "pending")
        self.assertEqual(records[0]["inputs"], [])
        self.assertEqual(records[0]["movement_settlement"], "pending")

    def test_arrival_settles_the_measured_load_exactly_once(self):
        phone = self.phone()
        first = phone.act("MOVE", {"destination_location_id": self.target}, "k-move-2")
        execution_id = first["data"]["execution_id"]
        self.advance_to(T0 + timedelta(seconds=self.seconds + 5))
        self.assertTrue(
            self.settle(lambda: self.current_node() == self.target),
            "the owner tick never realized the arrival",
        )

        records = self.consequences_for(execution_id)
        applied = [record for record in records if record["state"] == "applied"]
        self.assertEqual(len(applied), 1, records)
        # both labels mean "measured by the owner"; the second one says the load
        # was settled after the arrival had already been written.
        self.assertIn(
            applied[0]["movement_measurement"]["source"],
            ("measured_travel", "measured_travel_recovered"),
        )
        self.assertEqual(
            applied[0]["movement_measurement"]["duration_seconds"], float(self.seconds)
        )

        # further ticks settle nothing twice
        self.settle(lambda: False, timeout=0.4)
        applied = [
            record
            for record in self.consequences_for(execution_id)
            if record["state"] == "applied"
        ]
        self.assertEqual(len(applied), 1)

        # and the snapshot now reads the arrived place, with no walk in progress
        snapshot = phone.snapshot()["data"]
        self.assertEqual(snapshot["location"]["node"], self.target)
        self.assertEqual(snapshot["travel"]["status"], "none")

    def test_one_key_never_means_two_operations(self):
        phone = self.phone()
        first = phone.act("MOVE", {"destination_location_id": self.target}, "k-move-3")
        self.assertTrue(first["ok"])
        execution_id = first["data"]["execution_id"]

        other_action = phone.act("POSE", {"pose": "seated"}, "k-move-3")
        self.assertFalse(other_action["ok"])
        self.assertEqual(other_action["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(other_action["data"]["action_type"], "MOVE")

        replay = phone.act("MOVE", {"destination_location_id": self.target}, "k-move-3")
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(replay["data"]["replay_evidence"], "matched")
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(len(self.consequences_for(execution_id)), 1)

        elsewhere = next(
            (
                candidate
                for candidate in wrd.active_world(ENVIRONMENT).all_nodes()
                if candidate not in (PLACE_HOME, self.target)
            ),
            None,
        )
        if elsewhere is not None:
            conflict = phone.act(
                "MOVE", {"destination_location_id": elsewhere}, "k-move-3"
            )
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["reason_code"], "OPERATION_TARGET_CONFLICT")
            self.assertEqual(conflict["data"]["requested_params"]["destination_location_id"],
                             elsewhere)

        # and the key cannot be reused by the phone-shopping channel either
        shopping = phone_shopping.PhoneShopping(
            str(self.home),
            dict(ENVIRONMENT),
            local_owner_uid=os.getuid(),
            subject=wpe.OWNER,
            gateway_socket=self.socket_path(),
        )
        purchase = shopping.purchase("virtual_snack_wafer", "k-move-3")
        self.assertFalse(purchase["ok"])
        self.assertEqual(purchase["reason_code"], "OPERATION_KEY_CONFLICT")

    def test_operation_reports_the_same_execution_without_executing(self):
        phone = self.phone()
        first = phone.act("MOVE", {"destination_location_id": self.target}, "k-move-4")
        execution_id = first["data"]["execution_id"]
        before = file_fingerprint(self.home)
        report = phone.operation("k-move-4")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["data"]["execution_id"], execution_id)
        self.assertEqual(report["data"]["operation"], "world")
        self.assertEqual(report["data"]["action_type"], "MOVE")
        self.assertEqual(report["data"]["reason_code"], "move_departed")
        self.assertEqual(report["data"]["travel"]["status"], "in_progress")
        self.assertEqual(before, file_fingerprint(self.home))

    def test_a_second_move_from_the_new_place_uses_its_own_key(self):
        phone = self.phone()
        first = phone.act("MOVE", {"destination_location_id": self.target}, "k-move-5")
        self.assertTrue(first["ok"])
        self.advance_to(T0 + timedelta(seconds=self.seconds + 5))
        self.assertTrue(self.settle(lambda: self.current_node() == self.target))
        again = phone.act("MOVE", {"destination_location_id": self.target}, "k-move-6")
        # already there: the World refuses a noop move, honestly
        self.assertFalse(again["ok"])
        self.assertIn(again["reason_code"], ("NOOP", "ALREADY_AT_LOCATION"))


# ---------------------------------------------------------------------------
# room life: pose, pick up, put down
# ---------------------------------------------------------------------------


class RoomTests(PhoneWorldBase):
    def test_pose_pick_and_place_are_legal_and_visible(self):
        self.start_server()
        phone = self.phone()
        seated = phone.act("POSE", {"pose": "seated"}, "k-pose-1")
        self.assertTrue(seated["ok"], seated)
        self.assertEqual(seated["data"]["body"]["applied_to_body"], True)
        self.assertEqual(self.substrate()["pose"], "seated")

        picked = phone.act(
            "PICK_UP", {"object_id": SNACK, "slot_id": "right_hand"}, "k-pick-1"
        )
        self.assertTrue(picked["ok"], picked)
        self.assertEqual(self.substrate()["placements"][SNACK]["slot_id"], "right_hand")
        snapshot = phone.snapshot()["data"]
        self.assertEqual(snapshot["objects"]["hands"]["right_hand"], SNACK)
        self.assertEqual(snapshot["objects"]["counts"]["hands"], 1)

        placed = phone.act(
            "PLACE",
            {
                "object_id": SNACK,
                "target": {"kind": "room", "location_id": PLACE_HOME,
                           "area_id": AREA_HOME},
            },
            "k-place-1",
        )
        self.assertTrue(placed["ok"], placed)
        self.assertEqual(self.substrate()["placements"][SNACK]["kind"], "room")
        snapshot = phone.snapshot()["data"]
        self.assertIsNone(snapshot["objects"]["hands"]["right_hand"])
        self.assertTrue(
            all(item["object_id"] != SNACK for item in snapshot["objects"]["here"]) is False
        )
        snack = next(
            item for item in snapshot["objects"]["here"] if item["object_id"] == SNACK
        )
        self.assertTrue(snack["portable"])

    def test_a_lying_pose_cannot_pick_something_up(self):
        self.start_server()
        phone = self.phone()
        self.assertTrue(phone.act("POSE", {"pose": "lying"}, "k-pose-2")["ok"])
        refused = phone.act(
            "PICK_UP", {"object_id": SNACK, "slot_id": "right_hand"}, "k-pick-2"
        )
        self.assertFalse(refused["ok"])
        self.assertIn(
            refused["reason_code"],
            ("NOT_REACHABLE", "REST_REQUIRES_RESTING_POSE", "NOT_TAKEABLE"),
        )
        self.assertEqual(self.substrate()["placements"][SNACK]["kind"], "room")

    def test_a_body_value_or_elapsed_time_is_not_a_parameter(self):
        self.start_server()
        phone = self.phone()
        for params in (
            {"pose": "seated", "elapsed_seconds": 3600},
            {"pose": "seated", "fatigue": 0.1},
            {"pose": "seated", "authority": "verified_main_self_tool"},
        ):
            refused = phone.act("POSE", params, "k-forbidden")
            self.assertFalse(refused["ok"], refused)
            self.assertEqual(refused["reason_code"], "INVALID_PARAMS")
        self.assertEqual(self.substrate()["pose"], self.pose)


# ---------------------------------------------------------------------------
# put down: the room the body is in is the only room a hand can reach
# ---------------------------------------------------------------------------


class PlaceAreaTests(PhoneWorldBase):
    """The live rc1 shape: a bought item in hand, standing in home/bedroom.

    ``home/kitchen`` is the same building and only a few steps away, but it is
    not where the body is, so a PLACE aimed there must be refused without
    touching the substrate or the ledger.  The exact reason the phone UI showed
    live (a bare ``UNKNOWN``) is also pinned: a redacted object id is a
    ``UNKNOWN_TARGET`` fact, not a shrug.
    """

    def owner_state(self):
        """The world/body/ledger bytes a refusal must leave alone.

        The authorization registry under ``run/`` *is* written by a submission
        (the ticket is issued and consumed), so it is deliberately not part of
        this fingerprint: what must not move is the World, the substrate, the
        ledger and the Body.
        """

        return (
            wbs.substrate_path(self.home).read_bytes(),
            (self.home / "data" / "life_execution.jsonl").read_bytes(),
            (self.home / "data" / "body" / "runtime.json").read_bytes(),
        )

    def place(self, phone, target, key, object_id=SNACK):
        return phone.act(
            "PLACE",
            {"object_id": object_id, "target": dict(target)},
            key,
        )

    def test_the_current_room_accepts_the_held_item(self):
        self.start_server()
        phone = self.phone()
        picked = phone.act("PICK_UP", {"object_id": SNACK, "slot_id": "left_hand"}, "k-al-pick")
        self.assertTrue(picked["ok"], picked)
        picked_revision = self.substrate()["revision"]
        snapshot = phone.snapshot()["data"]
        here = snapshot["location"]
        self.assertEqual((here["place_id"], here["area_id"]), (PLACE_HOME, AREA_HOME))

        placed = self.place(
            phone,
            {"kind": "room", "location_id": here["place_id"], "area_id": here["area_id"]},
            "k-al-place",
        )
        self.assertTrue(placed["ok"], placed)
        self.assertEqual(placed["status"], "applied")
        self.assertEqual(
            self.substrate()["placements"][SNACK],
            {"kind": "room", "location_id": PLACE_HOME, "area_id": AREA_HOME},
        )
        self.assertEqual(self.substrate()["revision"], picked_revision + 1)

    def test_another_area_is_refused_and_the_item_stays_in_hand(self):
        self.start_server()
        phone = self.phone()
        self.assertTrue(
            phone.act("PICK_UP", {"object_id": SNACK, "slot_id": "left_hand"}, "k-al-pick2")["ok"]
        )
        before = self.owner_state()
        refused = self.place(
            phone,
            {"kind": "room", "location_id": PLACE_HOME, "area_id": "kitchen"},
            "k-al-far",
        )
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["status"], "rejected")
        self.assertEqual(refused["reason_code"], "NOT_CURRENT_AREA")
        self.assertNotEqual(refused["reason_code"], "UNKNOWN")
        self.assertEqual(self.owner_state(), before, "a refusal writes nothing")
        self.assertEqual(
            self.substrate()["placements"][SNACK], {"kind": "hand", "slot_id": "left_hand"}
        )
        self.assertEqual(
            phone.snapshot()["data"]["objects"]["hands"]["left_hand"], SNACK
        )
        # the refused key names nothing at all: no execution was ever recorded
        self.assertEqual(phone.operation("k-al-far")["reason_code"], "OPERATION_NOT_FOUND")

    def test_a_redacted_object_id_is_a_code_not_unknown(self):
        # The web layer hands the *display* label of the held item back as the
        # object id (its 64-hex run becomes [已隐藏的长标识]); the owner cannot
        # place an id it has never heard of, and must say which fact refused it.
        self.start_server()
        phone = self.phone()
        self.assertTrue(
            phone.act("PICK_UP", {"object_id": SNACK, "slot_id": "left_hand"}, "k-al-pick3")["ok"]
        )
        before = self.owner_state()
        refused = self.place(
            phone,
            {"kind": "room", "location_id": PLACE_HOME, "area_id": AREA_HOME},
            "k-al-masked",
            object_id="item-[已隐藏的长标识]",
        )
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["reason_code"], "UNKNOWN_TARGET")
        self.assertNotEqual(refused["reason_code"], "UNKNOWN")
        self.assertEqual(self.owner_state(), before)
        self.assertEqual(
            self.substrate()["placements"][SNACK], {"kind": "hand", "slot_id": "left_hand"}
        )

    def test_one_key_places_once_and_keeps_its_target_binding(self):
        self.start_server()
        phone = self.phone()
        self.assertTrue(
            phone.act("PICK_UP", {"object_id": SNACK, "slot_id": "left_hand"}, "k-al-pick4")["ok"]
        )
        target = {"kind": "room", "location_id": PLACE_HOME, "area_id": AREA_HOME}
        first = self.place(phone, target, "k-al-replay")
        self.assertTrue(first["ok"], first)
        after_place = self.owner_state()

        replay = self.place(phone, target, "k-al-replay")
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(replay["data"]["replay_evidence"], "matched")
        self.assertEqual(self.owner_state(), after_place, "replay wrote nothing")

        other = self.place(
            phone,
            {"kind": "room", "location_id": PLACE_HOME, "area_id": "living"},
            "k-al-replay",
        )
        self.assertFalse(other["ok"], other)
        self.assertEqual(other["reason_code"], "OPERATION_TARGET_CONFLICT")
        self.assertEqual(self.owner_state(), after_place)


# ---------------------------------------------------------------------------
# rest: the Body's threshold, a real session, the tick, and interruption
# ---------------------------------------------------------------------------


class RestTests(PhoneWorldBase):
    pose = "seated"
    fatigue = 0.60

    def setUp(self) -> None:
        super().setUp()
        self.start_server()

    def test_a_low_fatigue_body_truthfully_refuses_to_rest(self):
        restarted = PhoneWorldBase.__new__(PhoneWorldBase)
        restarted.home = unique_home()
        seed_world(restarted.home)
        seed_substrate(restarted.home, pose="seated")
        seed_body(restarted.home, fatigue=0.15)
        saved = dict(os.environ)
        try:
            for key, value in ENVIRONMENT.items():
                os.environ[key] = value
            os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = wpe.OWNER
            service = wbsvc.WorldBodyService(
                wbc.load_config(
                    dict(ENVIRONMENT),
                    runtime_root=RUNTIME_ROOT,
                    world_home=restarted.home,
                    runtime_mode="canonical",
                    authority="production",
                    world_id="shiomi_city",
                ),
                now=T0,
                instance_id="rc1-world-body-low",
                tick_seconds=0.05,
            )
            service.startup()
            thread = threading.Thread(
                target=service.serve, kwargs={"max_seconds": 60.0}, daemon=True
            )
            thread.start()
            socket_path = restarted.home / "run" / "gateway.sock"
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not socket_path.exists():
                time.sleep(0.02)
            phone = phone_world.PhoneWorld(
                str(restarted.home),
                dict(ENVIRONMENT),
                local_owner_uid=os.getuid(),
                subject=wpe.OWNER,
                gateway_socket=socket_path,
            )
            envelope = phone.act("REST", {"state": "start"}, "k-rest-low")
            self.assertFalse(envelope["ok"], envelope)
            self.assertIn(
                envelope["reason_code"],
                ("RECOVERY_NOT_NEEDED", "NOT_EXPOSED", "REST_REQUIRES_RESTING_POSE"),
            )
            snapshot = phone.snapshot()["data"]
            rest = next(v for v in snapshot["actions"]["verbs"] if v["verb"] == "REST")
            self.assertEqual(rest["blocked_reason"], "RECOVERY_NOT_NEEDED")
            self.assertFalse(rest["available"])
            self.assertEqual(snapshot["body"]["recovery"]["state"], "inactive")
            service.shutdown()
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_a_legal_rest_opens_advances_stops_and_refuses_a_second_session(self):
        phone = self.phone()
        opened = phone.act("REST", {"state": "start"}, "k-rest-open")
        self.assertTrue(opened["ok"], opened)
        self.assertEqual(opened["status"], "applied")
        self.assertEqual(opened["data"]["reason_code"], "recovery_start_committed")
        self.assertTrue(opened["data"]["body"]["applied_to_body"])
        self.assertEqual(opened["data"]["body"]["recovery"]["state"], "active")
        self.assertEqual(opened["data"]["body"]["recovery"]["mode"], "rest")

        second = phone.act("REST", {"state": "start"}, "k-rest-open-2")
        self.assertFalse(second["ok"], second)
        self.assertEqual(second["reason_code"], "RECOVERY_ALREADY_ACTIVE")

        # the owner tick advances the session; time is injected, not waited for
        # the owner tick advances the session; time is injected, not waited for
        before = self.body_runtime()["somatic"]["fatigue"]
        self.advance_to(T0 + timedelta(hours=1))
        self.assertTrue(
            self.settle(
                lambda: self.body_runtime()["somatic"]["fatigue"] < before,
                timeout=3.0,
            ),
            "the tick never advanced the open recovery session",
        )
        recovery = phone.snapshot()["data"]["body"]["recovery"]
        self.assertEqual(recovery["state"], "active")
        self.assertIn(recovery["progress_band"], ("mild", "noticeable", "strong"))

        stopped = phone.act("REST", {"state": "stop"}, "k-rest-stop")
        self.assertTrue(stopped["ok"], stopped)
        self.assertEqual(stopped["data"]["reason_code"], "recovery_stop_committed")
        self.assertEqual(phone.snapshot()["data"]["body"]["recovery"]["state"], "completed")

        nothing = phone.act("REST", {"state": "stop"}, "k-rest-stop-2")
        self.assertFalse(nothing["ok"])
        self.assertEqual(nothing["reason_code"], "NO_OPEN_RECOVERY")

    def test_sleep_like_needs_a_lying_pose(self):
        phone = self.phone()
        refused = phone.act(
            "REST", {"state": "start", "mode": "sleep_like"}, "k-sleep-1"
        )
        self.assertFalse(refused["ok"], refused)
        self.assertTrue(
            str(refused["reason_code"]).startswith("SLEEP_LIKE_REQUIRES_LYING_POSE")
        )
        self.assertTrue(phone.act("POSE", {"pose": "lying"}, "k-pose-3")["ok"])
        accepted = phone.act(
            "REST", {"state": "start", "mode": "sleep_like"}, "k-sleep-2"
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["data"]["body"]["recovery"]["mode"], "sleep_like")
        body = phone.snapshot()["data"]["body"]
        self.assertEqual(body["rest_evidence"], "pose:lying@bed_surface")

    def test_a_real_walk_interrupts_the_open_session(self):
        phone = self.phone()
        opened = phone.act("REST", {"state": "start"}, "k-rest-walk-open")
        self.assertTrue(opened["ok"], opened)
        start, target, seconds = walking_edge(PLACE_HOME)
        if start != PLACE_HOME:
            self.skipTest("fixture home has no walking edge in this world")
        departed = phone.act("MOVE", {"destination_location_id": target}, "k-rest-walk")
        self.assertTrue(departed["ok"], departed)
        # departing alone is not a load yet: the session is still open
        self.assertEqual(
            phone.snapshot()["data"]["body"]["recovery"]["state"], "active"
        )
        self.advance_to(T0 + timedelta(seconds=seconds + 5))
        self.assertTrue(
            self.settle(
                lambda: phone.snapshot()["data"]["body"]["recovery"]["state"]
                == "interrupted"
            ),
            "the arrival never interrupted the open session",
        )
        recovery = phone.snapshot()["data"]["body"]["recovery"]
        self.assertEqual(recovery["state"], "interrupted")
        self.assertEqual(recovery["trend"], "worsening")
        body = phone.snapshot()["data"]["body"]
        self.assertIn(
            "recovery_interrupted",
            [signal["signal_type"] for signal in body["signals"]],
        )


class RestartTests(PhoneWorldBase):
    pose = "seated"
    fatigue = 0.60

    def test_an_open_session_survives_a_restart(self):
        self.start_server()
        phone = self.phone()
        opened = phone.act("REST", {"state": "start"}, "k-rest-restart")
        self.assertTrue(opened["ok"], opened)
        execution_id = opened["data"]["execution_id"]
        self.service.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        self.service = None

        self.socket_path().unlink(missing_ok=True)
        self.start_server(moment=T0 + timedelta(seconds=90))
        self.assertEqual(self.body_runtime()["recovery"]["state"], "active")
        snapshot = self.phone().snapshot()["data"]
        self.assertEqual(snapshot["body"]["recovery"]["state"], "active")
        # the restart fabricated no second session and no second command
        self.assertEqual(len(self.consequences_for(execution_id)), 1)
        self.assertEqual(self.body_runtime()["recovery"]["mode"], "rest")
        self.assertEqual(ledger.ExecutionLedger(ledger.ledger_path(self.home))
                         .frame(execution_id)["state"], "committed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
