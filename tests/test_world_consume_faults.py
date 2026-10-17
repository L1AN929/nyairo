#!/usr/bin/env python3
"""F: ``CONSUME`` fault and recovery battery (crash windows, receipts, migration).

The point of this file is the part that is easy to get wrong: after a crash, a
consumed object is *supposed* to be absent, so absence can never be the proof.
Every case here checks that the only accepted attribution is the World owner's
consumption receipt, matched against the execution, the payload and the body.

Real stack only: real ``WorldBodyService`` (single writer + lease), real
execution ledger, real substrate file, real Body runtime/consequence stores.
Fixture homes live under ``/tmp/nyaw05r.jdK3q/f-daily-consume-faults-*`` and are
created once and retained (nothing is deleted, no user data is touched).

Run:  python3 tests/test_world_consume_faults.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
RUNTIME_ROOT = ROOT / "components" / "world"
sys.path[:0] = [str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE)]

import world_action_resolver as war  # noqa: E402
import world_body_config as wbc  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as wbs  # noqa: E402

FIXTURE_ROOT = pathlib.Path("/tmp/nyaw05r.jdK3q")
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)  # 21:00 Asia/Tokyo

PLACE_STORE = "convenience_store"
ONIGIRI = "f-food-onigiri-01"
BENTO = "f-food-bento-01"
CHAIR = "f-chair-01"

CONSUMABLES = {
    ONIGIRI: {"consumable": True, "free_to_consume": True},
    BENTO: {"consumable": True, "free_to_consume": True},
}


def room(place: str) -> dict:
    return {"kind": "room", "location_id": place, "area_id": place}


class ConsumeFaultBase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
        self.home = FIXTURE_ROOT / ("f-daily-consume-faults-" + uuid.uuid4().hex[:12])
        self.home.mkdir(parents=True, exist_ok=False)
        for relative in (("data", "world"), ("data", "body"), ("state",), ("run",)):
            (self.home.joinpath(*relative)).mkdir(parents=True, exist_ok=True)
        self._seed_world()
        self._seed_substrate({ONIGIRI: room(PLACE_STORE)})
        self.service = None
        self.start("faults")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    # -- fixtures --------------------------------------------------------
    def _seed_world(self) -> None:
        (self.home / "data" / "world" / "world_state.json").write_text(
            json.dumps(
                {
                    "facts": [],
                    "location": {"area_id": PLACE_STORE, "place_id": PLACE_STORE},
                    "revision": 5,
                    "scene": {"revision": 5, "scene_id": PLACE_STORE},
                    "schema_version": "world.foundation.v0",
                    "timezone": "Asia/Tokyo",
                    "world_id": "shiomi_city",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (self.home / "data" / "world" / "world_timeline.json").write_text(
            json.dumps({"schema_version": "world.timeline.v1", "revision": 1,
                        "world_id": "shiomi_city", "processes": []}, sort_keys=True),
            encoding="utf-8",
        )
        (self.home / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")
        (self.home / "state" / "world_object_capabilities.json").write_text(
            json.dumps(
                {
                    "schema_version": wbs.CAPABILITIES_SCHEMA,
                    "objects": {},
                    "consumables": CONSUMABLES,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _seed_substrate(self, placements: dict, *, pose: str = "standing") -> dict:
        state = wbs.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
        state["pose"] = pose
        state["orientation"] = "south"
        for object_id, target in placements.items():
            state = wbs.place_object(state, object_id, target)
        wbs.SubstrateStore(wbs.substrate_path(self.home)).save(state)
        return state

    def config(self):
        return wbc.load_config(
            {"CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1", wbs.FEATURE_ENV: "1"},
            runtime_root=RUNTIME_ROOT,
            world_home=self.home,
            runtime_mode="canonical",
            authority="production",
            world_id="shiomi_city",
        )

    def start(self, instance_id: str):
        if self.service is not None:
            self.service.shutdown()
        service = wbsvc.WorldBodyService(
            self.config(), now=NOW, instance_id=instance_id
        )
        self.addCleanup(service.shutdown)
        service.startup()
        self.service = service
        return service

    # -- reads -----------------------------------------------------------
    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.home)).load()

    def substrate_bytes(self) -> bytes:
        return wbs.substrate_path(self.home).read_bytes()

    def receipt(self, execution_id: str):
        return wbs.consumption_receipt(self.substrate(), execution_id)

    def placement(self, object_id: str):
        return wbs.placement_of(self.substrate(), object_id)

    def consequence_records(self, execution_id: str | None = None) -> list[dict]:
        path = self.home / "data" / "body" / "consequences.jsonl"
        if not path.exists():
            return []
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return [
            record for record in records
            if record.get("action_type") == "CONSUME"
            and (execution_id is None or record.get("execution_id") == execution_id)
        ]

    def propose(self, *, params: dict, execution_id: str, crash_after=None):
        return self.service.resolver.resolve(
            {
                "action": "CONSUME",
                "execution_id": execution_id,
                "actor_id": "chiyo",
                "body_id": "chiyo_body",
                "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
                "confirm_apply": True,
                "observed_dependencies": self.service.resolver._dependencies(),
                "params": params,
            },
            crash_after=crash_after,
        )

    def act(self, *, params: dict, execution_id: str):
        return self.service.act(
            {
                "action": "CONSUME",
                "execution_id": execution_id,
                "actor_id": "chiyo",
                "body_id": "chiyo_body",
                "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
                "confirm_apply": True,
                "observed_dependencies": self.service.resolver._dependencies(),
                "params": params,
            }
        )


# ---------------------------------------------------------------------------
# crash windows
# ---------------------------------------------------------------------------


class ConsumeCrashWindowsTest(ConsumeFaultBase):
    def test_01_crash_after_prepared_consumes_nothing(self):
        before = self.substrate_bytes()
        with self.assertRaises(war.ResolverCrash):
            self.propose(
                params={"object_id": ONIGIRI},
                execution_id="world-exec:f-crash-prepared",
                crash_after=war.CRASH_AFTER_PREPARED,
            )
        # nothing was removed, and nothing proves a consumption happened
        self.assertIsNotNone(self.placement(ONIGIRI))
        self.assertIsNone(self.receipt("world-exec:f-crash-prepared"))
        self.assertEqual(self.substrate_bytes(), before)
        self.assertEqual(self.consequence_records(), [])

        service = self.start("faults-after-prepared")
        unresolved = service.startup_reconciliation["unresolved"]
        mine = [item for item in unresolved
                if item["execution_id"] == "world-exec:f-crash-prepared"]
        self.assertEqual(len(mine), 1, unresolved)
        self.assertEqual(mine[0]["resolver_verdict"], "UNKNOWN")
        self.assertTrue(mine[0]["reconciliation_required"])
        self.assertEqual(self.consequence_records(), [], "UNKNOWN must not apply early")

        # the execution is not graded either; a replay stays uncertain
        replay = self.propose(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-crash-prepared"
        )
        self.assertEqual(replay["status"], "uncertain", replay)

        # and a *different* execution can still consume the instance: the crash
        # really did not consume anything
        fresh = self.propose(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-crash-prepared-b"
        )
        self.assertEqual(fresh["status"], "applied", fresh)
        self.assertIsNone(self.placement(ONIGIRI))

    def test_02_crash_after_mutation_is_attributed_by_receipt_not_by_absence(self):
        with self.assertRaises(war.ResolverCrash):
            self.propose(
                params={"object_id": ONIGIRI},
                execution_id="world-exec:f-crash-mutation",
                crash_after=war.CRASH_AFTER_MUTATION,
            )
        # the object is gone -- by design.  The receipt is the only proof.
        self.assertIsNone(self.placement(ONIGIRI))
        receipt = self.receipt("world-exec:f-crash-mutation")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["object_id"], ONIGIRI)
        self.assertEqual(self.consequence_records(), [])

        service = self.start("faults-after-mutation")
        unresolved = service.startup_reconciliation["unresolved"]
        mine = [item for item in unresolved
                if item["execution_id"] == "world-exec:f-crash-mutation"]
        self.assertEqual(len(mine), 1, unresolved)
        self.assertEqual(mine[0]["resolver_verdict"], "UNKNOWN")
        self.assertEqual(self.consequence_records(), [],
                         "an in-flight execution must not reach the Body")

        view = service.gateway_surface.get_action_result("world-exec:f-crash-mutation")
        self.assertEqual(view["verdict"], "UNKNOWN")
        self.assertTrue(view["receipt_present"], view)
        self.assertEqual(view["consumption_receipt"]["object_id"], ONIGIRI)

        # a replay never consumes twice, and never gets graded from absence
        before = self.substrate_bytes()
        replay = self.propose(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-crash-mutation"
        )
        self.assertEqual(replay["status"], "uncertain", replay)
        self.assertEqual(self.substrate_bytes(), before)
        self.assertEqual(len(self.substrate()["consumptions"]), 1)
        self.assertEqual(self.consequence_records(), [])


# ---------------------------------------------------------------------------
# recovery: the body consequence, exactly once
# ---------------------------------------------------------------------------


class ConsumeRecoveryTest(ConsumeFaultBase):
    def test_03_a_committed_consumption_heals_the_body_exactly_once(self):
        first = self.propose(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-heal"
        )
        self.assertEqual(first["status"], "applied", first)
        # the world committed but the Body never heard (the M13A crash-A window)
        self.assertEqual(self.consequence_records("world-exec:f-heal"), [])

        service = self.start("faults-heal")
        healed = [item for item in service.startup_reconciliation["healed"]
                  if item["execution_id"] == "world-exec:f-heal"]
        self.assertEqual(len(healed), 1, service.startup_reconciliation)
        records = self.consequence_records("world-exec:f-heal")
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["state"], "applied")
        self.assertEqual(records[0]["resource_ref"], ONIGIRI)
        self.assertEqual(records[0]["inputs"][0]["class"], "PHYSICAL_RESULT")
        runtime = json.loads(
            (self.home / "data" / "body" / "runtime.json").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(runtime["version"], records[0]["body_version_after"])

        # restarting again re-reads the recorded consequence and writes nothing
        service = self.start("faults-heal-2")
        self.assertEqual(
            [item for item in service.startup_reconciliation["healed"]
             if item["execution_id"] == "world-exec:f-heal"], []
        )
        self.assertIn("world-exec:f-heal",
                      service.startup_reconciliation["already_applied"])
        self.assertEqual(len(self.consequence_records("world-exec:f-heal")), 1)

    def test_04_absence_without_a_receipt_is_never_attributed(self):
        first = self.propose(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-orphan"
        )
        self.assertEqual(first["status"], "applied", first)
        self.assertIsNone(self.placement(ONIGIRI))

        # Surgery on the *fixture*: drop the receipt, keeping the object absent.
        # This is the exact state in which "the object is gone" would be a lie.
        state = self.substrate()
        self.assertIn("world-exec:f-orphan", state["consumptions"])
        state["consumptions"] = {}
        wbs.SubstrateStore(wbs.substrate_path(self.home)).save(state)

        service = self.start("faults-orphan")
        unresolved = [item for item in service.startup_reconciliation["unresolved"]
                      if item["execution_id"] == "world-exec:f-orphan"]
        self.assertEqual(len(unresolved), 1, service.startup_reconciliation)
        self.assertEqual(unresolved[0]["reasons"], ["receipt_missing"])
        self.assertTrue(unresolved[0]["reconciliation_required"])
        self.assertEqual(self.consequence_records("world-exec:f-orphan"), [])

        view = service.gateway_surface.get_action_result("world-exec:f-orphan")
        self.assertEqual(view["verdict"], "SUCCESS")  # the ledger still says so
        self.assertFalse(view["receipt_present"], view)  # but nothing attributes it

    def test_07_a_broken_economy_loses_nothing_and_consumption_resumes_after_repair(
        self,
    ):
        path = self.home / "state" / "world_property_economy.json"
        path.write_text('{"items":', encoding="utf-8")
        before = self.substrate_bytes()

        refused = self.act(params={"object_id": ONIGIRI},
                           execution_id="world-exec:f-econ-repair")
        self.assertEqual(refused["status"], "rejected", refused)
        self.assertEqual(self.substrate_bytes(), before)
        self.assertEqual(self.substrate()["consumptions"], {})
        self.assertEqual(self.consequence_records(), [])
        self.assertIsNotNone(self.placement(ONIGIRI))

        # Repairing the owner's document is enough: no residue, no lost state, and
        # the same instance can still be consumed exactly once.  The home is this
        # test's own fixture, and nothing is deleted -- a legal empty economy is
        # simply written over the broken one.
        import world_property_economy as wpe

        path.write_text(
            json.dumps(
                wpe._empty_state(created_at="2026-09-27T00:00:00+00:00"),
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        applied = self.act(params={"object_id": ONIGIRI},
                           execution_id="world-exec:f-econ-repair-2")
        self.assertEqual(applied["status"], "applied", applied)
        self.assertIsNone(self.placement(ONIGIRI))
        self.assertIsNotNone(self.receipt("world-exec:f-econ-repair-2"))
        self.assertEqual(
            len(self.consequence_records("world-exec:f-econ-repair-2")), 1
        )


# ---------------------------------------------------------------------------
# payload identity
# ---------------------------------------------------------------------------


class ConsumePayloadTest(ConsumeFaultBase):
    placements = {ONIGIRI: room(PLACE_STORE), BENTO: room(PLACE_STORE)}

    def setUp(self) -> None:
        super().setUp()
        self._seed_substrate(self.placements)

    def test_05_the_same_execution_cannot_consume_a_different_object(self):
        first = self.act(params={"object_id": ONIGIRI}, execution_id="world-exec:f-payload")
        self.assertEqual(first["status"], "applied", first)
        self.assertEqual(len(self.consequence_records("world-exec:f-payload")), 1)

        replay = self.propose(
            params={"object_id": BENTO}, execution_id="world-exec:f-payload"
        )
        self.assertEqual(replay["status"], "rejected", replay)
        self.assertEqual(replay["reason_code"], war.CODE_INVALID_PROPOSAL)
        self.assertIn("EXECUTION_PAYLOAD_MISMATCH", replay["legacy_reason_code"])
        # the other instance is untouched and no second consumption was recorded
        self.assertEqual(self.placement(BENTO), room(PLACE_STORE))
        self.assertEqual(len(self.substrate()["consumptions"]), 1)
        self.assertEqual(len(self.consequence_records("world-exec:f-payload")), 1)

    def test_06_a_legacy_substrate_is_migrated_and_still_consumes(self):
        # Hand-written v1.1 substrate: the shape that already exists on disk
        # before this feature, with two placements.
        legacy = {
            "schema_version": "world.body.substrate.v1.1",
            "kind": "world_body_substrate",
            "revision": 3,
            "world_id": "shiomi_city",
            "body_id": "chiyo_body",
            "world_entity_id": "chiyo_body",
            "pose": "standing",
            "orientation": "south",
            "placements": {ONIGIRI: room(PLACE_STORE), CHAIR: room(PLACE_STORE)},
            "reservations": {},
        }
        wbs.substrate_path(self.home).write_text(
            json.dumps(legacy, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )

        loaded, migrated = wbs.SubstrateStore(
            wbs.substrate_path(self.home)
        ).load_with_migration()
        self.assertTrue(migrated)
        self.assertEqual(loaded["schema_version"], wbs.SCHEMA_VERSION)
        self.assertEqual(loaded["consumptions"], {})
        self.assertEqual(loaded["placements"][CHAIR], room(PLACE_STORE))
        # a read never rewrites the owner's file
        on_disk = json.loads(wbs.substrate_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["schema_version"], "world.body.substrate.v1.1")

        service = self.start("faults-migrated")
        result = self.act(params={"object_id": ONIGIRI}, execution_id="world-exec:f-migrated")
        self.assertEqual(result["status"], "applied", result)

        written = json.loads(wbs.substrate_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], wbs.SCHEMA_VERSION)
        self.assertIn("world-exec:f-migrated", written["consumptions"])
        # the untouched placement survives the upgrade + the consumption
        self.assertEqual(written["placements"][CHAIR], room(PLACE_STORE))
        self.assertEqual(len(written["placements"]), 1)
        self.assertEqual(service.world_summary()["world_id"], "shiomi_city")


if __name__ == "__main__":
    unittest.main()
