#!/usr/bin/env python3
"""rc1 room-living: the physical store, owned furniture and the room lamp.

This is the *owner* side of the feature, tested against the real modules only
(the one property-economy document, the one body substrate, the one Resolver and
its ledger).  No UI, no HTTP, no second store.

What is asserted here
---------------------
* the counter is a place: an item can only be bought while she is really there
  and the shop is really open, and a GET of the shelf never delivers, arranges or
  moves money;
* one purchase is one atomic fact (debit + OWNED instance) under one operation
  key, which is the *same* namespace as every other phone operation;
* a pending delivery is never arrangeable, the owner tick realizes it exactly
  once, and a read never does;
* furniture is arranged (ARRANGE), not picked up: ``takeable`` is untouched, the
  bed can never be picked up, the destination must be the home she is standing
  in, and children on the furniture's own surface travel with it;
* the lamp is a declared fixture with an absolute power state: unknown is never
  "off", another area's lamp does not count, and a repeated state is a no-op.

Fixtures live under ``/tmp/nyaw05r.jdK3q/rc1-room-*`` and are created once and
retained (no delete, no cleanup, no real user data).

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_store_room_owner.py
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
sys.path[:0] = [str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE)]

import world_action_resolver as resolver  # noqa: E402
import world_body_binding as binding  # noqa: E402
import world_body_config as wbc  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as substrate  # noqa: E402
import world_phone_shopping as phone_shopping  # noqa: E402
import world_property_economy as economy  # noqa: E402
import world_room_environment as room  # noqa: E402

FIXTURE_ROOT = pathlib.Path("/tmp/nyaw05r.jdK3q")
FIXTURE_PREFIX = "rc1-room-store-"

DESK = "simple_desk"
CHAIR = "simple_chair"
LAMP = "bedside_lamp"
BED = "basic_bed"
SNACK = "virtual_snack_wafer"

ENVIRONMENT = {
    economy.FEATURE_ENV: "1",
    economy.WORLD_FEATURE_ENV: "1",
    economy.DAILY_FEATURE_ENV: "1",
    economy.DELIVERY_FEATURE_ENV: "1",
    substrate.FEATURE_ENV: "1",
}

#: A Wednesday, 12:00 Asia/Tokyo -- the counter is open.
OPEN_NOW = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
#: The same day, 21:00 Asia/Tokyo -- past closing time.
CLOSED_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def unique_home() -> pathlib.Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    home = FIXTURE_ROOT / (FIXTURE_PREFIX + uuid.uuid4().hex[:12])
    home.mkdir(parents=True, exist_ok=False)
    for relative in (("data", "world"), ("data", "body"), ("state",), ("run",)):
        home.joinpath(*relative).mkdir(parents=True, exist_ok=True)
    return home
def unique_named_root(prefix: str) -> pathlib.Path:
    """A free path for ``demo.prepare_demo``, which insists on creating the root itself.

    Unlike ``unique_home`` this must **not** exist yet (``prepare_demo`` refuses a
    non-empty root), so only the shared fixture directory is created here.  The
    instance is then retained like every other fixture (no delete, no cleanup).
    """

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    return FIXTURE_ROOT / (prefix + uuid.uuid4().hex[:12])


def seed_world(home: pathlib.Path, *, place: str = "home", area: str = "bedroom") -> None:
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
            {"schema_version": substrate.CAPABILITIES_SCHEMA, "objects": {}, "consumables": {}},
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def seed_substrate(home: pathlib.Path, placements: dict | None = None) -> dict:
    state = substrate.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
    for object_id, target in (placements or {}).items():
        state = substrate.place_object(state, object_id, target)
    substrate.SubstrateStore(substrate.substrate_path(home)).save(state)
    return state


def world_location(home: pathlib.Path) -> dict:
    raw = json.loads(
        (home / "data" / "world" / "world_state.json").read_text(encoding="utf-8")
    )
    return raw.get("location") or {}


def local_owner(home: pathlib.Path) -> dict:
    return {
        "source": economy.LOCAL_OWNER_SOURCE,
        "uid": os.getuid(),
        "subject": economy.OWNER,
        "home": str(home),
    }


def file_fingerprint(home: pathlib.Path) -> dict:
    out = {}
    for path in sorted(home.rglob("*")):
        if path.is_file() and not path.name.endswith(".lock"):
            out[str(path)] = (path.stat().st_size, path.stat().st_mtime_ns)
    return out


class StoreBase(unittest.TestCase):
    place = "home_goods_store"
    area = "counter"

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        for key, value in ENVIRONMENT.items():
            os.environ[key] = value
        self.home = unique_home()
        seed_world(self.home, place=self.place, area=self.area)
        seed_substrate(self.home)
        economy.initialize_genesis(hermes_home=self.home, environment=ENVIRONMENT)
        self.resolver = resolver.WorldResolver(self.home, environment=dict(ENVIRONMENT))

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    # owners -------------------------------------------------------------
    def state(self) -> dict:
        return economy.load_state(self.home)

    def balance(self) -> int:
        return economy.wallet_projection(
            hermes_home=self.home, environment=ENVIRONMENT
        )["current_balance"]

    def buy(self, catalog_item_id: str, key: str, *, now=OPEN_NOW) -> dict:
        return economy.store_purchase_item(
            catalog_item_id=catalog_item_id,
            operation_key=key,
            local_owner=local_owner(self.home),
            hermes_home=self.home,
            environment=ENVIRONMENT,
            now=now,
        )

    def buy_at_counter(
        self, catalog_item_id: str, key: str, *, delivery: bool = False
    ) -> dict:
        """Buy from wherever she is by stepping to the counter and back.

        The counter is a real place: a fixture that wants an owned instance must
        put her there for the command (that is the rule under test), then return
        her home.  No bypass, no second price.
        """

        seed_world(self.home, place="home_goods_store", area="counter")
        try:
            return economy.store_purchase_item(
                catalog_item_id=catalog_item_id,
                operation_key=key,
                local_owner=local_owner(self.home),
                hermes_home=self.home,
                environment={
                    **ENVIRONMENT,
                    economy.DELIVERY_FEATURE_ENV: "1" if delivery else "0",
                },
                now=OPEN_NOW,
            )
        finally:
            seed_world(self.home, place=self.place, area=self.area)

    def resolve(self, action: str, params: dict, execution_id: str) -> dict:
        return self.resolver.resolve(
            {
                "action": action,
                "execution_id": execution_id,
                "actor_id": "chiyo",
                "body_id": "chiyo_body",
                "authority": resolver.AUTHORITY_VERIFIED_MAIN_SELF,
                "confirm_apply": True,
                "observed_dependencies": self.resolver._dependencies(),
                "params": params,
            }
        )


# ---------------------------------------------------------------------------
# the shelf (GET) and the counter purchase (POST)
# ---------------------------------------------------------------------------


class StoreReadTests(StoreBase):
    def test_the_shelf_requires_the_counter_and_the_opening_hours(self):
        projection = economy.store_shop_projection(
            hermes_home=self.home, environment=ENVIRONMENT, now=OPEN_NOW
        )
        self.assertEqual(projection["status"], "available", projection)
        self.assertTrue(projection["at_store"])
        self.assertTrue(projection["purchase_available"])
        self.assertEqual(projection["channel"], economy.CHANNEL_HOME_GOODS_STORE)
        self.assertEqual(len(projection["catalog"]), 6)
        self.assertEqual(
            [entry["catalog_item_id"] for entry in projection["catalog"]],
            [BED, DESK, CHAIR, LAMP, "small_bookshelf", "floor_cushion"],
        )
        for entry in projection["catalog"]:
            self.assertEqual(entry["usable_kind"], "FURNISH")
            self.assertTrue(entry["affordable"])
        self.assertTrue(projection["read_is_pure"])
        self.assertFalse(projection["paid_real_money"])

        closed = economy.store_shop_projection(
            hermes_home=self.home, environment=ENVIRONMENT, now=CLOSED_NOW
        )
        self.assertEqual(closed["status"], "rejected")
        self.assertEqual(closed["reason_code"], "STORE_CLOSED")
        self.assertFalse(closed["purchase_available"])

        seed_world(self.home, place="home", area="bedroom")
        away = economy.store_shop_projection(
            hermes_home=self.home, environment=ENVIRONMENT, now=OPEN_NOW
        )
        self.assertEqual(away["reason_code"], "STORE_LOCATION_REQUIRED")
        self.assertFalse(away["at_store"])

    def test_a_read_never_delivers_anything(self):
        bought = self.buy(DESK, "op-store-read-pending")
        self.assertEqual(bought["status"], "applied", bought)
        self.assertEqual(bought["delivery_status"], economy.DELIVERY_STATUS_PENDING)
        before = file_fingerprint(self.home)
        # Due (3h later) but only a *tick* may realize it.
        projection = economy.store_shop_projection(
            hermes_home=self.home, environment=ENVIRONMENT, now=OPEN_NOW
        )
        deliveries = economy.deliveries_projection(
            hermes_home=self.home, environment=ENVIRONMENT
        )
        snapshot_like = economy.home_items_projection(
            hermes_home=self.home, environment=ENVIRONMENT, now=OPEN_NOW + timedelta(hours=3)
        )
        self.assertEqual(before, file_fingerprint(self.home))
        self.assertEqual(projection["pending_delivery_count"], 1)
        self.assertEqual(deliveries["pending_count"], 1)
        self.assertEqual(deliveries["read_delivers_items"], False)
        self.assertEqual(snapshot_like["pending_delivery_count"], 1)
        stored = self.state()["items"][0]
        self.assertTrue(stored["current_location"].startswith("in_transit/"))


class StorePurchaseTests(StoreBase):
    def test_a_purchase_is_one_atomic_debit_plus_owned_instance(self):
        result = self.buy(DESK, "op-store-desk-01")
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["price"], 4200)
        self.assertEqual(result["current_balance"], economy.MOVE_IN_GRANT - 4200)
        self.assertEqual(result["currency"], "JPY")
        self.assertEqual(result["ownership"], economy.DISPOSITION_OWNED)
        self.assertEqual(result["materialization"], "pending_arrange")
        state = self.state()
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(len(state["items"]), 1)
        transaction = state["transactions"][0]
        self.assertEqual(transaction["direction"], "DEBIT")
        self.assertEqual(
            transaction["provenance"]["source"], economy.STORE_PURCHASE_SOURCE
        )
        self.assertEqual(
            transaction["provenance"]["delivery"], economy.STORE_DELIVERY_PROVENANCE
        )
        self.assertNotEqual(
            transaction["provenance"]["source"], economy.MAIN_SELF_PURCHASE_SOURCE
        )
        # no placement was invented by buying
        self.assertIsNone(
            substrate.placement_of(
                substrate.SubstrateStore(substrate.substrate_path(self.home)).load(),
                result["item_instance_id"],
            )
        )
        expected = (
            OPEN_NOW + timedelta(seconds=economy.DELIVERY_DURATION_SECONDS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(result["expected_delivery_at"], expected)

    def test_the_same_key_never_charges_twice(self):
        first = self.buy(DESK, "op-store-replay")
        second = self.buy(DESK, "op-store-replay")
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "already_applied", second)
        self.assertEqual(second["reason_code"], "IDEMPOTENT_REPLAY")
        self.assertEqual(first["item_instance_id"], second["item_instance_id"])
        state = self.state()
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(len(state["items"]), 1)
        self.assertEqual(self.balance(), economy.MOVE_IN_GRANT - 4200)

    def test_the_same_key_with_a_different_item_conflicts(self):
        self.buy(DESK, "op-store-conflict")
        conflict = self.buy(CHAIR, "op-store-conflict")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason_code"], "OPERATION_TARGET_CONFLICT")
        self.assertEqual(len(self.state()["transactions"]), 1)
        self.assertEqual(self.balance(), economy.MOVE_IN_GRANT - 4200)

    def test_a_key_spent_on_the_phone_channel_cannot_buy_at_the_counter(self):
        phone_bought = economy.phone_purchase_item(
            catalog_item_id=SNACK,
            operation_key="op-shared-key",
            local_owner=local_owner(self.home),
            hermes_home=self.home,
            environment=ENVIRONMENT,
            now=OPEN_NOW,
        )
        self.assertEqual(phone_bought["status"], "applied", phone_bought)
        conflict = self.buy(DESK, "op-shared-key")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(
            conflict["recorded_source"], economy.PHONE_PURCHASE_SOURCE
        )
        # and the reverse direction refuses too
        reversed_conflict = economy.phone_purchase_item(
            catalog_item_id=SNACK,
            operation_key="op-store-replay-reverse",
            local_owner=local_owner(self.home),
            hermes_home=self.home,
            environment=ENVIRONMENT,
            now=OPEN_NOW,
        )
        self.assertEqual(reversed_conflict["status"], "applied")
        counter = self.buy(DESK, "op-store-replay-reverse")
        self.assertEqual(counter["reason_code"], "OPERATION_KEY_CONFLICT")

    def test_closed_wrong_place_and_funds_refuse_without_moving_money(self):
        before = self.balance()
        closed = self.buy(DESK, "op-store-closed", now=CLOSED_NOW)
        self.assertEqual(closed["status"], "rejected")
        self.assertEqual(closed["reason_code"], "STORE_CLOSED")
        seed_world(self.home, place="home", area="bedroom")
        away = self.buy(DESK, "op-store-away")
        self.assertEqual(away["status"], "rejected")
        self.assertEqual(away["reason_code"], "STORE_LOCATION_REQUIRED")
        self.assertEqual(self.balance(), before)
        self.assertEqual(len(self.state()["transactions"]), 0)

        seed_world(self.home, place="home_goods_store", area="counter")
        # A counter channel item that the phone store sells is not sold here.
        phone_only = self.buy(SNACK, "op-store-phone-item")
        self.assertEqual(phone_only["reason_code"], "ITEM_NOT_SOLD_AT_STORE")
        self.assertEqual(self.balance(), before)

    def test_pending_is_not_arrangeable_and_the_tick_delivers_exactly_once(self):
        bought = self.buy(DESK, "op-store-delivery")
        item_id = bought["item_instance_id"]
        placements = substrate.SubstrateStore(
            substrate.substrate_path(self.home)
        ).load()["placements"]
        pending = economy.arrange_verdict(
            item_instance_id=item_id,
            target={"kind": "room", "location_id": "home", "area_id": "bedroom"},
            placements=placements,
            world_location={"place_id": "home_goods_store", "area_id": "counter"},
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertFalse(pending["allowed"])
        self.assertIn(pending["reason_code"], ("NOT_DELIVERED", "NOT_HOME"))

        realized = economy.realize_due_deliveries(
            hermes_home=self.home,
            environment=ENVIRONMENT,
            now=OPEN_NOW + timedelta(hours=3),
        )
        self.assertEqual(realized["status"], "applied", realized)
        self.assertEqual(realized["realized"], 1)
        again = economy.realize_due_deliveries(
            hermes_home=self.home,
            environment=ENVIRONMENT,
            now=OPEN_NOW + timedelta(hours=4),
        )
        self.assertEqual(again["status"], "noop")
        self.assertEqual(again["realized"], 0)
        state = self.state()
        delivered = [d for d in state["deliveries"] if d["status"] == "DELIVERED"]
        self.assertEqual(len(delivered), 1)
        self.assertIsNotNone(delivered[0]["delivered_at"])
        item = next(i for i in state["items"] if i["item_instance_id"] == item_id)
        self.assertEqual(item["current_location"], economy.HOME_ITEM_LOCATION)
        self.assertTrue(delivered[0]["delivered_at"].startswith("2026-10-21T06:00"))


# ---------------------------------------------------------------------------
# arranging owned furniture (never a pick-up)
# ---------------------------------------------------------------------------


class ArrangeTests(StoreBase):
    place = "home"
    area = "bedroom"

    def setUp(self) -> None:
        super().setUp()
        # Two owned, delivered instances placed in the bedroom.
        self.desk = self._own_and_place(DESK, "op-arrange-desk")
        self.bed = self._own_and_place(BED, "op-arrange-bed")

    def _own_and_place(self, catalog_item_id: str, key: str) -> str:
        bought = self.buy_at_counter(catalog_item_id, key)
        self.assertEqual(bought["status"], "applied", bought)
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = store.load()
        store.save(
            substrate.place_object(
                live,
                bought["item_instance_id"],
                {"kind": "room", "location_id": "home", "area_id": "bedroom"},
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )
        return bought["item_instance_id"]

    def substrate(self) -> dict:
        return substrate.SubstrateStore(substrate.substrate_path(self.home)).load()

    def test_arranging_moves_furniture_and_never_becomes_a_pick_up(self):
        target = {
            "kind": "surface",
            "location_id": "home",
            "surface_id": substrate.surface_instance_id("desk_surface", self.desk),
        }
        result = self.resolve("ARRANGE", {"object_id": self.desk, "target": target}, "exec-arrange-1")
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["arranged"]["object_id"], self.desk)
        live = self.substrate()
        self.assertEqual(substrate.placement_of(live, self.desk), target)
        # a surface now really exists for this desk only
        surfaces = substrate.surfaces_here(
            live, binding.owned_catalog_map(self.home), location_id="home", area_id="bedroom"
        )
        mine = [s for s in surfaces if s["surface_id"] == target["surface_id"]]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["owner_object_id"], self.desk)
        self.assertEqual(mine[0]["surface_kind"], "desk_surface")
        # the desk's surface is *its* identity: a second desk would get its own
        self.assertNotIn(
            "desk_surface", [s["surface_id"] for s in surfaces]
        )

        # the desk is *not* takeable: arranging it did not make it portable
        picked = self.resolve(
            "PICK_UP", {"object_id": self.desk, "slot_id": "left_hand"}, "exec-pick-desk"
        )
        self.assertEqual(picked["status"], "rejected")
        self.assertEqual(picked["legacy_reason_code"].split(":")[0], "NOT_PORTABLE")

    def test_the_bed_can_be_arranged_but_never_picked_up(self):
        picked = self.resolve(
            "PICK_UP", {"object_id": self.bed, "slot_id": "left_hand"}, "exec-pick-bed"
        )
        self.assertEqual(picked["status"], "rejected")
        target = {
            "kind": "surface",
            "location_id": "home",
            "surface_id": substrate.surface_instance_id("bed_surface", self.bed),
        }
        arranged = self.resolve("ARRANGE", {"object_id": self.bed, "target": target}, "exec-arrange-bed")
        self.assertEqual(arranged["status"], "applied", arranged)

    def test_children_on_the_surface_travel_with_the_furniture(self):
        surface_id = substrate.surface_instance_id("desk_surface", self.desk)
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = store.load()
        live = substrate.place_object(
            live,
            self.desk,
            {"kind": "surface", "location_id": "home", "surface_id": surface_id},
            expected_revision=live["revision"],
        )
        store.save(live, expected_revision=live["revision"] - 1)
        child = "item-child-snack"
        live = store.load()
        store.save(
            substrate.place_object(
                live,
                child,
                {"kind": "surface", "location_id": "home", "surface_id": surface_id},
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )

        result = self.resolve(
            "ARRANGE",
            {
                "object_id": self.desk,
                "target": {"kind": "room", "location_id": "home", "area_id": "bedroom"},
            },
            "exec-arrange-children",
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["arranged"]["carried"], [child])
        live = self.substrate()
        self.assertEqual(
            substrate.placement_of(live, child),
            {"kind": "surface", "location_id": "home", "surface_id": surface_id},
        )
        # the child is not stranded: its surface is still derived from the desk
        surfaces = substrate.surfaces_here(
            live, binding.owned_catalog_map(self.home), location_id="home", area_id="bedroom"
        )
        mine = [s for s in surfaces if s["surface_id"] == surface_id]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["items"], [child])

    def test_arranging_from_another_place_or_into_another_area_is_refused(self):
        placements = self.substrate()["placements"]
        away = economy.arrange_verdict(
            item_instance_id=self.desk,
            target={"kind": "room", "location_id": "home", "area_id": "bedroom"},
            placements=placements,
            world_location={"place_id": "home_goods_store", "area_id": "counter"},
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertFalse(away["allowed"])
        self.assertEqual(away["reason_code"], "NOT_HOME")
        elsewhere = economy.arrange_verdict(
            item_instance_id=self.desk,
            target={
                "kind": "room",
                "location_id": "home_goods_store",
                "area_id": "counter",
            },
            placements=placements,
            world_location={"place_id": "home", "area_id": "bedroom"},
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertFalse(elsewhere["allowed"])
        self.assertEqual(elsewhere["reason_code"], "NOT_CURRENT_AREA")

    def test_a_surface_bound_to_another_desk_is_refused(self):
        verdict = economy.arrange_verdict(
            item_instance_id=self.desk,
            target={
                "kind": "surface",
                "location_id": "home",
                "surface_id": "desk_surface:item-someone-else",
            },
            placements=self.substrate()["placements"],
            world_location={"place_id": "home", "area_id": "bedroom"},
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], "SURFACE_OWNER_MISMATCH")

    def test_a_non_economy_object_cannot_be_arranged(self):
        verdict = economy.arrange_verdict(
            item_instance_id="M13A1_TEST_OBJECT",
            target={"kind": "room", "location_id": "home", "area_id": "bedroom"},
            placements={},
            world_location={"place_id": "home", "area_id": "bedroom"},
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], "NOT_AN_ECONOMY_INSTANCE")


# ---------------------------------------------------------------------------
# the room lamp
# ---------------------------------------------------------------------------


class LampTests(StoreBase):
    place = "home"
    area = "bedroom"

    def setUp(self) -> None:
        super().setUp()
        bought = self.buy_at_counter(LAMP, "op-lamp-01")
        self.assertEqual(bought["status"], "applied", bought)
        self.lamp = bought["item_instance_id"]
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = store.load()
        store.save(
            substrate.place_object(
                live,
                self.lamp,
                {"kind": "room", "location_id": "home", "area_id": "bedroom"},
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )

    def substrate(self) -> dict:
        return substrate.SubstrateStore(substrate.substrate_path(self.home)).load()

    def light(self, area: str = "bedroom") -> dict:
        live = self.substrate()
        return room.derive_light(
            placements=live["placements"],
            fixtures=live["fixtures"],
            catalog_by_instance=binding.owned_catalog_map(self.home),
            location_id="home",
            area_id=area,
            now=OPEN_NOW,
        )

    def test_a_lamp_in_another_area_is_not_light_here(self):
        elsewhere = self.light("living")
        self.assertEqual(elsewhere["status"], "unknown")
        self.assertEqual(elsewhere["reason_code"], room.REASON_NO_DECLARED_LIGHT)
        self.assertIsNone(elsewhere["power"])
        self.assertIsNone(room.environment_input(elsewhere))

    def test_absent_power_is_unknown_and_never_off_or_zero(self):
        here = self.light()
        self.assertEqual(here["status"], "unknown")
        self.assertEqual(here["reason_code"], room.REASON_POWER_UNKNOWN)
        self.assertIsNone(here["band"])
        self.assertIsNone(room.environment_input(here))

    def test_toggle_is_absolute_idempotent_and_area_bound(self):
        # move the lamp to the store: the command must refuse there
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = store.load()
        store.save(
            substrate.place_object(
                live,
                self.lamp,
                {"kind": "room", "location_id": "home_goods_store", "area_id": "counter"},
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )
        away = self.resolve(
            "TOGGLE_FIXTURE", {"object_id": self.lamp, "state": "on"}, "exec-lamp-away"
        )
        self.assertEqual(away["status"], "rejected")
        self.assertTrue(away["legacy_reason_code"].startswith("FIXTURE_NOT_HERE"))

        live = store.load()
        store.save(
            substrate.place_object(
                live,
                self.lamp,
                {"kind": "room", "location_id": "home", "area_id": "bedroom"},
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )
        on = self.resolve(
            "TOGGLE_FIXTURE", {"object_id": self.lamp, "state": "on"}, "exec-lamp-on"
        )
        self.assertEqual(on["status"], "applied", on)
        self.assertEqual(on["fixture"]["state"], "on")
        self.assertEqual(on["fixture"]["band"], "dim")
        revision = self.substrate()["revision"]

        again = self.resolve(
            "TOGGLE_FIXTURE", {"object_id": self.lamp, "state": "on"}, "exec-lamp-on-2"
        )
        self.assertEqual(again["status"], "noop")
        self.assertEqual(again["legacy_reason_code"], "fixture_already_on")
        self.assertEqual(self.substrate()["revision"], revision)

        lit = self.light()
        self.assertEqual(lit["status"], "available")
        self.assertEqual(lit["power"], "on")
        self.assertEqual(lit["band"], "dim")
        self.assertEqual(lit["fixture_ids"], [self.lamp])
        self.assertIsNotNone(lit["since"])
        self.assertEqual(room.environment_input(lit), {"class": "ENVIRONMENT", "sensory_intensity": 0.2})

        off = self.resolve(
            "TOGGLE_FIXTURE", {"object_id": self.lamp, "state": "off"}, "exec-lamp-off"
        )
        self.assertEqual(off["status"], "applied", off)
        dark = self.light()
        self.assertEqual(dark["status"], "available")
        self.assertEqual(dark["power"], "off")
        self.assertIsNone(room.environment_input(dark))

    def test_a_non_fixture_cannot_be_toggled(self):
        bought = self.buy_at_counter(DESK, "op-lamp-desk")
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = store.load()
        store.save(
            substrate.place_object(
                live,
                bought["item_instance_id"],
                {"kind": "room", "location_id": "home", "area_id": "bedroom"},
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )
        result = self.resolve(
            "TOGGLE_FIXTURE",
            {"object_id": bought["item_instance_id"], "state": "on"},
            "exec-toggle-desk",
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(result["legacy_reason_code"].startswith("NOT_A_FIXTURE"))


# ---------------------------------------------------------------------------
# store -> owner tick -> ARRANGE (no hand-placed fixture anywhere)
# ---------------------------------------------------------------------------


class StoreDeliveryToArrangeTests(StoreBase):
    """The chain the parent found broken: buy at the counter, let the tick
    deliver, then arrange -- with **no** fixture placement in between.

    Before the fix the economy moved to ``home/bedroom`` while the substrate had
    no placement at all, so ``arrange_verdict`` answered ``NOT_MATERIALIZED``
    forever.  The owner tick now materializes the arrived instance through the
    one seam (OWNED + DELIVERED + purchase voucher -> legal ``home/bedroom``
    placement), idempotently.
    """

    place = "home"
    area = "bedroom"

    def service(self):
        config = wbc.load_config(
            dict(ENVIRONMENT),
            runtime_root=ROOT / "components" / "world",
            world_home=self.home,
            runtime_mode="canonical",
            authority="production",
            world_id="shiomi_city",
        )
        service = wbsvc.WorldBodyService(
            config, instance_id="rc1-room-arrive", tick_seconds=0.05
        )
        self.addCleanup(service.shutdown)
        service.startup()
        return service

    def placement(self, item_id: str):
        return substrate.placement_of(
            substrate.SubstrateStore(substrate.substrate_path(self.home)).load(),
            item_id,
        )

    def test_temporal_delivery_becomes_arrangeable_only_after_the_owner_tick(self):
        bought = self.buy_at_counter(DESK, "op-chain-temporal", delivery=True)
        self.assertEqual(bought["status"], "applied", bought)
        self.assertEqual(bought["delivery_status"], economy.DELIVERY_STATUS_PENDING)
        item_id = bought["item_instance_id"]
        # nothing has placed it: this is the state the parent reported
        self.assertIsNone(self.placement(item_id))
        blocked = economy.arrange_verdict(
            item_instance_id=item_id,
            target={"kind": "room", "location_id": "home", "area_id": "bedroom"},
            placements={},
            world_location={"place_id": "home", "area_id": "bedroom"},
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn(blocked["reason_code"], ("NOT_DELIVERED", "NOT_MATERIALIZED"))

        service = self.service()
        service._moment = lambda: OPEN_NOW + timedelta(hours=3)
        report = service.tick()
        self.assertIsNotNone(report)
        deliveries = report.get("deliveries") or {}
        self.assertIn("realized", deliveries)
        materialized = deliveries.get("materialized") or []
        self.assertEqual([row["item_instance_id"] for row in materialized], [item_id])
        self.assertEqual(materialized[0]["status"], "materialized")
        self.assertEqual(
            self.placement(item_id),
            {"kind": "room", "location_id": "home", "area_id": "bedroom"},
        )

        # the same tick result is idempotent: nothing left to do
        self.assertIsNone(service.tick())
        self.assertEqual(
            self.state()["items"][0]["disposition"], economy.DISPOSITION_OWNED
        )
        delivered = [d for d in self.state()["deliveries"] if d["status"] == "DELIVERED"]
        self.assertEqual(len(delivered), 1)

        # and now ARRANGE is legal through the real Resolver commit
        target = {
            "kind": "surface",
            "location_id": "home",
            "surface_id": substrate.surface_instance_id("desk_surface", item_id),
        }
        arranged = self.resolve(
            "ARRANGE", {"object_id": item_id, "target": target}, "exec-chain-arrange"
        )
        self.assertEqual(arranged["status"], "applied", arranged)
        self.assertEqual(self.placement(item_id), target)

    def test_instant_delivery_is_materialized_by_the_tick_too(self):
        bought = self.buy_at_counter(DESK, "op-chain-instant", delivery=False)
        self.assertEqual(bought["status"], "applied", bought)
        self.assertEqual(bought["delivery_status"], economy.DELIVERY_STATUS_DELIVERED)
        self.assertEqual(bought["location"], economy.HOME_ITEM_LOCATION)
        item_id = bought["item_instance_id"]
        self.assertIsNone(self.placement(item_id))
        service = self.service()
        report = service.tick()
        self.assertIsNotNone(report)
        rows = (report.get("deliveries") or {}).get("materialized") or []
        self.assertEqual([row["item_instance_id"] for row in rows], [item_id])
        self.assertEqual(
            self.placement(item_id),
            {"kind": "room", "location_id": "home", "area_id": "bedroom"},
        )
        self.assertIsNone(service.tick())

    def test_the_seam_refuses_a_conflict_and_an_undelivered_instance(self):
        bought = self.buy_at_counter(DESK, "op-chain-guard", delivery=False)
        item_id = bought["item_instance_id"]
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = store.load()
        store.save(
            substrate.place_object(
                live,
                item_id,
                {"kind": "room", "location_id": "home", "area_id": "living"},
                expected_revision=live["revision"],
            ),
            expected_revision=live["revision"],
        )
        conflict = phone_shopping.materialize_owned_item(
            hermes_home=self.home, item_instance_id=item_id, environment=ENVIRONMENT
        )
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason_code"], "PLACEMENT_CONFLICT")
        # the existing placement is untouched (never overwritten)
        self.assertEqual(
            self.placement(item_id),
            {"kind": "room", "location_id": "home", "area_id": "living"},
        )
        # a pending (not delivered) instance is refused, nothing is placed
        pending = self.buy_at_counter(DESK, "op-chain-pending", delivery=True)
        refused = phone_shopping.materialize_owned_item(
            hermes_home=self.home,
            item_instance_id=pending["item_instance_id"],
            environment=ENVIRONMENT,
        )
        self.assertEqual(refused["status"], "rejected")
        self.assertEqual(refused["reason_code"], "NOT_DELIVERED")
        self.assertIsNone(self.placement(pending["item_instance_id"]))

    def test_a_read_never_materializes_anything(self):
        bought = self.buy_at_counter(DESK, "op-chain-read", delivery=False)
        item_id = bought["item_instance_id"]
        before = file_fingerprint(self.home)
        economy.store_shop_projection(
            hermes_home=self.home, environment=ENVIRONMENT, now=OPEN_NOW
        )
        economy.deliveries_projection(hermes_home=self.home, environment=ENVIRONMENT)
        economy.home_items_projection(
            hermes_home=self.home, environment=ENVIRONMENT, now=OPEN_NOW
        )
        self.assertEqual(before, file_fingerprint(self.home))
        self.assertIsNone(self.placement(item_id))


# ---------------------------------------------------------------------------
# a real generated (coastal) world package: the exact field failure
# ---------------------------------------------------------------------------


class CoastalGeneratedWorldTests(unittest.TestCase):
    """The counter purchase against a **generated** world package.

    Regression for a real failure: on ``coastal_town_929711`` a browser POST of
    ``simple_desk`` answered ``WORLD_LOCATION_UNAVAILABLE`` with no money moved,
    while the shop GET reported ``available`` / ``at_store=true``.

    Root cause: the write path read the location through
    ``world_living_skeleton.read_move_state``, which returns a *navigation node*
    rather than a place and is pinned to ``shiomi_city``; a packaged world
    therefore looked like "no location at all".  The read path always used the
    place straight out of ``world_state.json``.  Both now share that one
    read-only place lookup, and this test walks the owner there for real (timed
    travel, injected clock -- never a hand-written ``world_state.json``).
    """

    @classmethod
    def setUpClass(cls) -> None:
        from components.phone import demo

        cls.coastal_root = unique_named_root("rc1-room-coastal-")
        demo.prepare_demo(cls.coastal_root, with_world=True)
        cls.instance = json.loads(
            (cls.coastal_root / "instance.json").read_text(encoding="utf-8")
        )
        cls.features = demo.effective_features(cls.instance, room_living=True)
        cls.world_home = pathlib.Path(str(cls.instance["world_home"]))
        cls.home_root = unique_named_root("rc1-room-coastal-home-")
        demo.prepare_demo(cls.home_root, with_world=True)
        home_instance = json.loads(
            (cls.home_root / "instance.json").read_text(encoding="utf-8")
        )
        cls.home_features = demo.effective_features(home_instance, room_living=True)
        cls.home_world = pathlib.Path(str(home_instance["world_home"]))

    @staticmethod
    def _walk(world_home: pathlib.Path, features: dict, hops) -> dict:
        """Walk the **real** graph, one timed move per edge, injected clock."""

        import world_living_skeleton as skeleton
        import world_travel_delivery as travel

        clock = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
        for index, hop in enumerate(hops):
            plan = skeleton.plan_move_to(
                hop, hermes_home=world_home, environment=features, now=clock
            )
            assert plan.get("status") == "ready", plan
            result = skeleton.apply_move_to(
                hop,
                hermes_home=world_home,
                environment=features,
                now=clock,
                execution_id="coastal-move-%d-%s" % (index, hop),
            )
            assert result.get("status") == "applied", result
            pending = None
            travels = (travel.load_state(world_home, features) or {}).get("travels") or []
            for item in travels:
                if item.get("status") == travel.TRAVEL_STATUS_IN_PROGRESS:
                    pending = item
            if pending is not None:
                due = datetime.fromisoformat(
                    str(pending["expected_arrival_at"]).replace("Z", "+00:00")
                )
                clock = max(clock, due) + timedelta(seconds=5)
                travel.realize_due_travel(
                    hermes_home=world_home, environment=features, now=clock
                )
        return json.loads(
            (world_home / "data" / "world" / "world_state.json").read_text(
                encoding="utf-8"
            )
        )

    def _phone(self, world_home: pathlib.Path, features: dict):
        import world_phone_shopping as shopping

        return shopping.PhoneShopping(
            str(world_home),
            dict(features),
            local_owner_uid=os.getuid(),
            subject="chiyo",
        )

    def _balance(self, world_home: pathlib.Path, features: dict) -> int:
        return economy.wallet_projection(
            hermes_home=world_home, environment=dict(features)
        )["current_balance"]

    def test_a_packaged_world_place_is_read_like_the_shop_get_does(self):
        self.assertNotEqual(self.instance["world_id"], "shiomi_city")
        self.assertTrue(self.instance.get("world_package"))
        self.assertEqual(
            economy._current_world_location(
                hermes_home=self.world_home, environment=dict(self.features)
            ),
            "home",
        )
        state = self._walk(
            self.world_home, self.features, ("home", "street_1", "home_goods_store")
        )
        self.assertEqual(state["location"]["place_id"], "home_goods_store")
        self.assertEqual(
            economy._current_world_location(
                hermes_home=self.world_home, environment=dict(self.features)
            ),
            "home_goods_store",
        )
        phone = self._phone(self.world_home, self.features)
        shelf = phone.store()
        self.assertTrue(shelf["ok"], shelf)
        self.assertEqual(shelf["status"], "applied", shelf)
        self.assertTrue(shelf["data"]["at_store"])
        self.assertEqual(shelf["data"]["channel"], economy.CHANNEL_HOME_GOODS_STORE)

        before = self._balance(self.world_home, self.features)
        bought = phone.store_purchase("simple_desk", "coastal-counter-01")
        self.assertTrue(bought["ok"], bought)
        self.assertEqual(bought["status"], "applied", bought)
        self.assertEqual(bought["data"]["price"], 4200)
        self.assertEqual(bought["data"]["current_balance"], before - 4200)
        state = economy.load_state(self.world_home)
        desk = [
            item
            for item in state["items"]
            if item["catalog_item_id"] == "simple_desk"
        ]
        self.assertEqual(len(desk), 1)
        self.assertEqual(desk[0]["disposition"], economy.DISPOSITION_OWNED)
        self.assertTrue(
            any(
                t.get("purchase_intent_id") == "coastal-counter-01"
                and t["amount"] == 4200
                and t["direction"] == "DEBIT"
                for t in state["transactions"]
            )
        )
        again = phone.store_purchase("simple_desk", "coastal-counter-01")
        self.assertEqual(again["status"], "already_applied", again)
        self.assertEqual(self._balance(self.world_home, self.features), before - 4200)

    def test_standing_at_home_is_refused_without_moving_money(self):
        phone = self._phone(self.home_world, self.home_features)
        shelf = phone.store()
        # A pure read: the shelf is listed, but standing at home is never
        # "at the counter" and nothing is bought, delivered or arranged.
        self.assertFalse(shelf["ok"], shelf)
        self.assertEqual(shelf["status"], "rejected", shelf)
        self.assertEqual(shelf["reason_code"], "STORE_LOCATION_REQUIRED")
        self.assertFalse(shelf["data"]["at_store"])
        self.assertFalse(shelf["data"]["purchase_available"])
        self.assertEqual(shelf["data"]["location"]["location_id"], "home")
        self.assertEqual(len(shelf["data"]["catalog"]), 6)
        before = self._balance(self.home_world, self.home_features)
        refused = phone.store_purchase("simple_desk", "coastal-home-01")
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["reason_code"], "STORE_LOCATION_REQUIRED")
        self.assertEqual(refused["data"]["current_location_id"], "home")
        self.assertEqual(self._balance(self.home_world, self.home_features), before)
        self.assertFalse(
            [
                t
                for t in economy.load_state(self.home_world)["transactions"]
                if t.get("purchase_intent_id") == "coastal-home-01"
            ]
        )

    def test_a_missing_world_state_stays_fail_closed(self):
        home = unique_home()
        economy.initialize_genesis(hermes_home=home, environment=ENVIRONMENT)
        phone = self._phone(home, ENVIRONMENT)
        shelf = phone.store()
        self.assertEqual(shelf["status"], "unavailable", shelf)
        self.assertEqual(shelf["reason_code"], "WORLD_LOCATION_UNAVAILABLE")
        refused = phone.store_purchase("simple_desk", "no-world-01")
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["status"], "unavailable")
        self.assertEqual(refused["reason_code"], "WORLD_LOCATION_UNAVAILABLE")
        self.assertEqual(
            economy.wallet_projection(
                hermes_home=home, environment=ENVIRONMENT
            )["current_balance"],
            economy.MOVE_IN_GRANT,
        )
        self.assertEqual(len(economy.load_state(home)["transactions"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
