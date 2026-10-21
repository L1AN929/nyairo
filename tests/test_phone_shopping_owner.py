#!/usr/bin/env python3
"""v0.4 phone shopping -- the World-side owner path (W's own positive/negative).

Everything here is the *real* local stack: the one property-economy document,
the one body substrate (placement + receipts), the real ``WorldBodyService`` and
its real gateway socket, the real Resolver/ledger and the one-shot authorization
registry.  Only the World content (which catalog item is declared consumable) is
a fixture, and every fixture home lives under
``/tmp/nyaw05r.jdK3q/v04-phone-w-*`` where it is created once and **retained**
(no delete, no cleanup, no user data touched).

Asserted here, in contract order
--------------------------------
* buying on the phone is one atomic economy fact (debit + OWNED instance) and
  the World owner materializes that instance at the real ``home/bedroom``
  placement -- never ``hand`` (holding is the Resolver's job);
* the same ``operation_key`` never charges twice, and an unpaired
  transaction/item document is refused instead of raising;
* insufficient funds / a broken economy / capacity refuse without money moving,
  and a read never initializes anything;
* an economy instance without a world declaration stays refused
  (``ECONOMY_OBJECT``): the v0.3 rule is not opened globally;
* an instance that was bought but not materialized is *not usable*;
* consuming goes through the original Resolver: one receipt for one execution,
  replay is ``already_applied``, a second key is ``ALREADY_CONSUMED``, the
  economy keeps ownership, and the Body only gets its ordinary light
  manipulation consequence (no nutrition/healing claim);
* the local-owner identity (uid/subject/home, server-side binding) cannot be
  forged from the client side.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_phone_shopping_owner.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
RUNTIME_ROOT = ROOT / "components" / "world"
sys.path[:0] = [str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE)]

import world_body_config as wbc  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_consumable_contract as contract  # noqa: E402
import world_phone_shopping as phone_shopping  # noqa: E402
import world_property_economy as wpe  # noqa: E402

FIXTURE_ROOT = pathlib.Path("/tmp/nyaw05r.jdK3q")
FIXTURE_PREFIX = "v04-phone-w-"

SNACK = "virtual_snack_wafer"
PLACE_HOME = "home"
AREA_HOME = "bedroom"
BED = "basic_bed"

CONSUMABLE_CATALOG = {
    SNACK: {
        "consumable": True,
        "free_to_consume": False,
        "consumption_permit": contract.PERMIT_OWNED_PURCHASE,
    }
}

ENVIRONMENT = {
    "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
    wbs.FEATURE_ENV: "1",
}


def unique_home() -> pathlib.Path:
    """A unique, retained fixture home (created once, never removed)."""

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    home = FIXTURE_ROOT / (FIXTURE_PREFIX + uuid.uuid4().hex[:12])
    home.mkdir(parents=True, exist_ok=False)
    (home / "data" / "world").mkdir(parents=True, exist_ok=True)
    (home / "data" / "body").mkdir(parents=True, exist_ok=True)
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / "run").mkdir(parents=True, exist_ok=True)
    return home


def seed_world(
    home: pathlib.Path,
    *,
    place: str = PLACE_HOME,
    area: str = AREA_HOME,
    consumable_catalog: dict | None = CONSUMABLE_CATALOG,
    consumables: dict | None = None,
    objects: dict | None = None,
) -> None:
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
    document = {
        "schema_version": wbs.CAPABILITIES_SCHEMA,
        "objects": objects or {},
        "consumables": consumables or {},
    }
    if consumable_catalog is not None:
        document["consumable_catalog"] = consumable_catalog
    (home / "state" / "world_object_capabilities.json").write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def seed_substrate(home: pathlib.Path, placements: dict | None = None, pose: str = "standing"):
    state = wbs.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
    state["pose"] = pose
    state["orientation"] = "south"
    for object_id, target in (placements or {}).items():
        state = wbs.place_object(state, object_id, target)
    wbs.SubstrateStore(wbs.substrate_path(home)).save(state)
    return state


def genesis(home: pathlib.Path) -> dict:
    return wpe.initialize_genesis(hermes_home=home, environment=ENVIRONMENT)


class PhoneBase(unittest.TestCase):
    """Real service + real gateway socket + real stores, one retained home."""

    pose = "standing"
    place = PLACE_HOME
    area = AREA_HOME
    consumable_catalog = CONSUMABLE_CATALOG
    economy_on_genesis: dict | None = None

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = wpe.OWNER
        for key, value in ENVIRONMENT.items():
            os.environ[key] = value
        self.home = unique_home()
        seed_world(
            self.home,
            place=self.place,
            area=self.area,
            consumable_catalog=self.consumable_catalog,
        )
        seed_substrate(self.home, pose=self.pose)
        genesis(self.home)
        if self.economy_on_genesis is not None:
            path = wpe.state_path(self.home)
            path.write_text(
                json.dumps(self.economy_on_genesis, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        self.service = None
        self._server_thread = None

    def tearDown(self) -> None:
        if self.service is not None:
            try:
                self.service.shutdown()
            except Exception:  # noqa: BLE001 - teardown must never mask a failure
                pass
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

    def service_instance(self, *, instance_id: str = "v04-phone-w"):
        service = wbsvc.WorldBodyService(
            self.config(), instance_id=instance_id, tick_seconds=0.05
        )
        self.addCleanup(service.shutdown)
        service.startup()
        self.service = service
        return service

    def socket_path(self) -> pathlib.Path:
        return self.home / "run" / "gateway.sock"

    def start_server(self):
        """Run the real service loop in a daemon thread (no auto-start anywhere)."""

        service = self.service_instance()
        thread = threading.Thread(
            target=service.serve, kwargs={"max_seconds": 120.0}, daemon=True
        )
        thread.start()
        self._server_thread = thread
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.socket_path().exists():
                return service
            time.sleep(0.02)
        self.fail("gateway socket never appeared at %s" % self.socket_path())

    def phone(self, **overrides):
        kwargs = {
            "local_owner_uid": os.getuid(),
            "subject": wpe.OWNER,
            "gateway_socket": self.socket_path(),
        }
        kwargs.update(overrides)
        return phone_shopping.PhoneShopping(
            str(self.home), dict(ENVIRONMENT), **kwargs
        )

    # -- owners (read-only assertions) ----------------------------------
    def economy_state(self) -> dict:
        return wpe.load_state(self.home)

    def balance(self) -> int:
        return wpe.wallet_projection(
            hermes_home=self.home, environment=ENVIRONMENT
        )["current_balance"]

    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.home)).load()

    def placement(self, object_id: str):
        return wbs.placement_of(self.substrate(), object_id)

    def consequences(self) -> list[dict]:
        path = self.home / "data" / "body" / "consequences.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def phone_item_state(self, catalog_item_id: str, *, intent: str | None = None) -> dict:
        """Write a *legal* economy document holding one such item (fixture only)."""

        state = wpe._empty_state(created_at="2026-10-20T00:00:00+00:00")
        transaction_id = "purchase-fixture-01"
        intent_id = intent or "op-fixture-01"
        provenance = {
            "source": wpe.MAIN_SELF_PURCHASE_SOURCE,
            "purchase_intent_id": intent_id,
            "transaction_id": transaction_id,
            "delivery": wpe.MAIN_SELF_DELIVERY_PROVENANCE,
            "source_turn_id": "turn-fixture",
        }
        state["transactions"] = [
            {
                "transaction_id": transaction_id,
                "transaction_kind": "PURCHASE",
                "direction": "DEBIT",
                "amount": 5_800,
                "currency": wpe.CURRENCY,
                "occurred_at": "2026-10-20T00:00:00+00:00",
                "catalog_item_id": catalog_item_id,
                "purchase_intent_id": intent_id,
                "delivery_location": wpe.HOME_ITEM_LOCATION,
                "provenance": provenance,
            }
        ]
        state["items"] = [
            {
                "item_instance_id": "item-fixture-01",
                "catalog_item_id": catalog_item_id,
                "owner": wpe.OWNER,
                "acquired_at": "2026-10-20T00:00:00+00:00",
                "acquisition_kind": "PURCHASE",
                "acquisition_provenance": provenance,
                "acquisition_price": 5_800,
                "currency": wpe.CURRENCY,
                "current_location": wpe.HOME_ITEM_LOCATION,
                "disposition": wpe.DISPOSITION_OWNED,
            }
        ]
        validated = wpe.validate_state(state)
        wpe.state_path(self.home).write_text(
            json.dumps(validated, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        return validated


# ---------------------------------------------------------------------------
# the store channel and the purchase
# ---------------------------------------------------------------------------


class PhoneShopTests(PhoneBase):
    def test_shop_is_its_own_channel_and_read_does_not_initialize(self):
        fresh = unique_home()
        seed_world(fresh, consumable_catalog=self.consumable_catalog)
        phone = phone_shopping.PhoneShopping(
            str(fresh), dict(ENVIRONMENT), local_owner_uid=os.getuid()
        )
        shop = phone.shop()
        self.assertTrue(shop["ok"], shop)
        self.assertEqual(shop["data"]["channel"], wpe.CHANNEL_PHONE_VIRTUAL_STORE)
        self.assertEqual(shop["data"]["availability"]["scheme"], wpe.PHONE_STORE_SCHEME)
        self.assertEqual(shop["data"]["availability"]["status"], "OPEN")
        self.assertEqual(
            [item["catalog_item_id"] for item in shop["data"]["catalog"]], [SNACK]
        )
        self.assertFalse(shop["data"]["purchase_available"])
        self.assertEqual(shop["data"]["reason_code"], "GENESIS_NOT_INITIALIZED")
        # the read created nothing and granted nothing
        self.assertFalse(wpe.state_path(fresh).exists())
        wallet = phone.wallet()
        self.assertFalse(wallet["ok"])
        self.assertEqual(wallet["reason_code"], "GENESIS_NOT_INITIALIZED")
        self.assertFalse(wpe.state_path(fresh).exists())

    def test_physical_store_channel_is_untouched(self):
        # the phone channel sells only its own item, and the physical catalog
        # does not gain it; the physical shop keeps its own location rule.
        self.assertEqual(
            [entry["catalog_item_id"] for entry in wpe.phone_catalog_entries()], [SNACK]
        )
        projection = wpe.shop_projection(
            hermes_home=self.home, environment={**ENVIRONMENT, "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1"}
        )
        # not at the physical store -> the legacy refusal, unchanged
        self.assertEqual(projection["status"], "rejected" if "status" in projection else None)
        self.assertNotEqual(projection.get("reason_code"), None)
        physical = wpe.purchase_item(
            catalog_item_id=SNACK,
            purchase_intent_id="op-physical-01",
            main_self_verified=True,
            hermes_home=self.home,
            environment={**ENVIRONMENT, "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1"},
        )
        self.assertEqual(physical["status"], "rejected")
        self.assertIn(
            physical["reason_code"],
            {"ITEM_NOT_SOLD_AT_STORE", "STORE_LOCATION_REQUIRED"},
        )
        self.assertEqual(self.balance(), wpe.MOVE_IN_GRANT)

    def test_purchase_is_atomic_materialized_and_owned(self):
        phone = self.phone()
        result = phone.purchase(SNACK, "op-buy-01")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "applied")
        item_id = result["data"]["item_instance_id"]
        self.assertEqual(result["data"]["current_balance"], wpe.MOVE_IN_GRANT - 240)
        self.assertEqual(result["data"]["materialization"], "materialized")

        state = self.economy_state()
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(len(state["items"]), 1)
        transaction = state["transactions"][0]
        item = state["items"][0]
        self.assertEqual(transaction["amount"], 240)
        self.assertEqual(transaction["provenance"]["source"], wpe.PHONE_PURCHASE_SOURCE)
        self.assertNotEqual(
            transaction["provenance"]["source"], wpe.MAIN_SELF_PURCHASE_SOURCE
        )
        self.assertEqual(item["disposition"], wpe.DISPOSITION_OWNED)
        self.assertEqual(item["current_location"], wpe.HOME_ITEM_LOCATION)

        placement = self.placement(item_id)
        self.assertEqual(
            placement,
            {"kind": "room", "location_id": PLACE_HOME, "area_id": AREA_HOME},
        )

        inventory = phone.inventory()
        self.assertTrue(inventory["ok"], inventory)
        entry = inventory["data"]["items"][0]
        self.assertEqual(entry["ownership"], wpe.DISPOSITION_OWNED)
        self.assertEqual(entry["materialization"], "materialized")
        self.assertTrue(entry["usable"])
        self.assertEqual(entry["consumption_source"], contract.SOURCE_PURCHASED)
        self.assertEqual(entry["purchase_voucher"]["transaction_id"], transaction["transaction_id"])
        wallet = phone.wallet()
        self.assertTrue(wallet["ok"], wallet)
        self.assertEqual(wallet["data"]["balance"], wpe.MOVE_IN_GRANT - 240)
        transaction_view = wallet["data"]["transactions"][0]
        self.assertEqual(transaction_view["kind"], "PURCHASE")
        self.assertEqual(transaction_view["direction"], "DEBIT")
        self.assertEqual(transaction_view["purchase_intent_id"], "op-buy-01")

    def test_same_key_replay_never_charges_twice(self):
        phone = self.phone()
        first = phone.purchase(SNACK, "op-buy-replay")
        second = phone.purchase(SNACK, "op-buy-replay")
        self.assertTrue(first["ok"], first)
        self.assertEqual(second["status"], "already_applied")
        self.assertEqual(
            first["data"]["item_instance_id"], second["data"]["item_instance_id"]
        )
        state = self.economy_state()
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(len(state["items"]), 1)
        self.assertEqual(self.balance(), wpe.MOVE_IN_GRANT - 240)

    def test_same_key_different_item_is_a_conflict(self):
        phone = self.phone()
        # the key was already used for a *different* item (a legal bed purchase)
        self.phone_item_state(BED, intent="op-buy-conflict")
        conflict = phone.purchase(SNACK, "op-buy-conflict")
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["reason_code"], "PHONE_PURCHASE_OPERATION_CONFLICT")
        state = self.economy_state()
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(state["transactions"][0]["catalog_item_id"], BED)

    def test_insufficient_funds_and_capacity(self):
        state = self.economy_state()
        phone = self.phone()
        # spend the wallet down to 100 by an economy fixture, not by a fake call
        drained = dict(state)
        drained["transactions"] = list(state["transactions"]) + [
            {
                "transaction_id": "purchase-drain-01",
                "transaction_kind": "PURCHASE",
                "direction": "DEBIT",
                "amount": wpe.MOVE_IN_GRANT - 100,
                "currency": wpe.CURRENCY,
                "occurred_at": "2026-10-20T00:00:00+00:00",
                "catalog_item_id": BED,
                "purchase_intent_id": "op-drain-01",
                "delivery_location": wpe.HOME_ITEM_LOCATION,
                "provenance": {
                    "source": wpe.MAIN_SELF_PURCHASE_SOURCE,
                    "purchase_intent_id": "op-drain-01",
                    "transaction_id": "purchase-drain-01",
                    "delivery": wpe.MAIN_SELF_DELIVERY_PROVENANCE,
                },
            }
        ]
        drained["items"] = list(state["items"]) + [
            {
                "item_instance_id": "item-drain-01",
                "catalog_item_id": BED,
                "owner": wpe.OWNER,
                "acquired_at": "2026-10-20T00:00:00+00:00",
                "acquisition_kind": "PURCHASE",
                "acquisition_provenance": {
                    "source": wpe.MAIN_SELF_PURCHASE_SOURCE,
                    "purchase_intent_id": "op-drain-01",
                    "transaction_id": "purchase-drain-01",
                    "delivery": wpe.MAIN_SELF_DELIVERY_PROVENANCE,
                },
                "acquisition_price": wpe.MOVE_IN_GRANT - 100,
                "currency": wpe.CURRENCY,
                "current_location": wpe.HOME_ITEM_LOCATION,
                "disposition": wpe.DISPOSITION_OWNED,
            }
        ]
        drained["revision"] = state["revision"] + 1
        drained["updated_at"] = "2026-10-20T00:00:00+00:00"
        wpe.state_path(self.home).write_text(
            json.dumps(drained, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        result = phone.purchase(SNACK, "op-buy-poor")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "INSUFFICIENT_FUNDS")
        self.assertEqual(result["data"]["current_balance"], 100)
        self.assertEqual(len(self.economy_state()["transactions"]), 1)
        self.assertIsNone(self.placement("item-" + "x"))

        # capacity: the 256 bound is kept, and the refusal is explicit (no prune)
        full = self.economy_state()
        base = full["transactions"][0]
        base_item = full["items"][0]
        transactions = []
        items = []
        for index in range(wpe.MAX_TRANSACTIONS):
            transactions.append(
                dict(
                    base,
                    transaction_id="purchase-full-%03d" % index,
                    purchase_intent_id="op-full-%03d" % index,
                    amount=1,
                )
            )
            items.append(dict(base_item, item_instance_id="item-full-%03d" % index))
        full["transactions"] = transactions
        full["items"] = items
        wpe.state_path(self.home).write_text(
            json.dumps(full, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        capacity = phone.purchase(SNACK, "op-buy-full")
        self.assertFalse(capacity["ok"])
        self.assertEqual(capacity["reason_code"], "ECONOMY_CAPACITY_EXCEEDED")
        self.assertEqual(len(self.economy_state()["transactions"]), wpe.MAX_TRANSACTIONS)

    def test_broken_economy_is_never_read_as_empty(self):
        phone = self.phone()
        path = wpe.state_path(self.home)
        path.write_text('{"items":', encoding="utf-8")
        wallet = phone.wallet()
        self.assertFalse(wallet["ok"])
        self.assertEqual(wallet["reason_code"], "ECONOMY_STATE_ERROR")
        inventory = phone.inventory()
        self.assertEqual(inventory["reason_code"], "ECONOMY_STATE_ERROR")
        purchase = phone.purchase(SNACK, "op-buy-broken")
        self.assertEqual(purchase["reason_code"], "ECONOMY_STATE_ERROR")
        # the broken document is left exactly as it was: no repair, no money
        self.assertEqual(path.read_text(encoding="utf-8"), '{"items":')

    def test_unpaired_ledger_document_is_refused_not_raised(self):
        # A *schema-valid* economy (cadinality only: #debits == #items) whose
        # purchase transaction names no matching item instance.  The economy
        # owner's validator accepts it, so a replay must answer, not crash.
        state = self.economy_state()
        broken = dict(state)
        broken["transactions"] = [
            {
                "transaction_id": "purchase-op-unpaired",
                "transaction_kind": "PURCHASE",
                "direction": "DEBIT",
                "amount": 240,
                "currency": wpe.CURRENCY,
                "occurred_at": "2026-10-20T00:00:00+00:00",
                "catalog_item_id": SNACK,
                "purchase_intent_id": "op-unpaired",
                "delivery_location": wpe.HOME_ITEM_LOCATION,
                "provenance": {
                    "source": wpe.PHONE_PURCHASE_SOURCE,
                    "purchase_intent_id": "op-unpaired",
                    "transaction_id": "purchase-op-unpaired",
                    "delivery": wpe.PHONE_DELIVERY_PROVENANCE,
                },
            }
        ]
        broken["items"] = [
            {
                "item_instance_id": "item-someone-else",
                "catalog_item_id": SNACK,
                "owner": wpe.OWNER,
                "acquired_at": "2026-10-20T00:00:00+00:00",
                "acquisition_kind": "PURCHASE",
                "acquisition_provenance": {
                    "source": wpe.PHONE_PURCHASE_SOURCE,
                    "purchase_intent_id": "op-unpaired",
                    "transaction_id": "purchase-op-unpaired",
                    "delivery": wpe.PHONE_DELIVERY_PROVENANCE,
                },
                "acquisition_price": 240,
                "currency": wpe.CURRENCY,
                "current_location": wpe.HOME_ITEM_LOCATION,
                "disposition": wpe.DISPOSITION_OWNED,
            }
        ]
        validated = wpe.validate_state(broken)  # the schema really does accept it
        wpe.state_path(self.home).write_text(
            json.dumps(validated, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        phone = self.phone()
        replay = phone.purchase(SNACK, "op-unpaired")
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["reason_code"], "PURCHASE_LEDGER_INCONSISTENT")
        self.assertEqual(self.balance(), wpe.MOVE_IN_GRANT - 240)
        operation = phone.operation("op-unpaired")
        self.assertEqual(operation["status"], "unknown")
        self.assertEqual(operation["reason_code"], "PURCHASE_LEDGER_INCONSISTENT")


class PhoneIdentityTests(PhoneBase):
    def test_uid_and_subject_cannot_be_forged(self):
        for overrides, expected in (
            ({"local_owner_uid": os.getuid() + 1}, "LOCAL_OWNER_UID_MISMATCH"),
            ({"subject": "someone_else"}, "LOCAL_OWNER_SUBJECT_MISMATCH"),
            ({"local_owner_uid": True}, "LOCAL_OWNER_UID_MISMATCH"),
        ):
            phone = self.phone(**overrides)
            for envelope in (
                phone.wallet(),
                phone.shop(),
                phone.inventory(),
                phone.purchase(SNACK, "op-forge"),
                phone.use("item-whatever", "op-forge-use"),
                phone.operation("op-forge"),
            ):
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["reason_code"], expected)
        self.assertEqual(self.economy_state()["transactions"], [])

    def test_bad_home_is_refused(self):
        phone = self.phone()
        phone.hermes_home = None
        phone._auth_error = "LOCAL_OWNER_HOME_MISMATCH"
        self.assertEqual(phone.purchase(SNACK, "op-x")["reason_code"], "LOCAL_OWNER_HOME_MISMATCH")

    def test_request_uid_is_not_an_identity(self):
        # the economy owner's own validator re-checks the context: a context with
        # a foreign uid never becomes a purchase, whatever the facade did.
        result = wpe.phone_purchase_item(
            catalog_item_id=SNACK,
            operation_key="op-body-uid",
            local_owner={
                "source": wpe.LOCAL_OWNER_SOURCE,
                "uid": os.getuid() + 1,
                "subject": wpe.OWNER,
                "home": str(self.home),
            },
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "LOCAL_OWNER_UID_MISMATCH")
        self.assertEqual(self.balance(), wpe.MOVE_IN_GRANT)


class PhoneUseTests(PhoneBase):
    def test_use_requires_a_real_service(self):
        phone = self.phone(gateway_socket=self.home / "run" / "absent.sock")
        bought = phone.purchase(SNACK, "op-use-noservice")
        self.assertTrue(bought["ok"], bought)
        unused = phone.use(bought["data"]["item_instance_id"], "op-use-noservice-1")
        self.assertFalse(unused["ok"])
        # if the service never ran, the refusal is about the port, not about the
        # (perfectly valid) purchase
        self.assertIn(
            unused["reason_code"], {"WORLD_BODY_SERVICE_UNAVAILABLE", "NOT_MATERIALIZED"}
        )
        self.assertEqual(
            self.consequences(), [], "a refused use must not touch the Body"
        )

    def test_unmaterialized_instance_is_not_usable_and_purchase_recovers_it(self):
        phone = self.phone()
        # money + ownership only: exactly the crash window between the economy
        # save and the substrate materialization.
        result = wpe.phone_purchase_item(
            catalog_item_id=SNACK,
            operation_key="op-pending-01",
            local_owner={
                "source": wpe.LOCAL_OWNER_SOURCE,
                "uid": os.getuid(),
                "subject": wpe.OWNER,
                "home": str(self.home),
            },
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertEqual(result["status"], "applied")
        item_id = result["item_instance_id"]
        self.assertIsNone(self.placement(item_id))

        inventory = phone.inventory()
        entry = inventory["data"]["items"][0]
        self.assertEqual(entry["delivery_status"], "PENDING_MATERIALIZATION")
        self.assertEqual(entry["materialization"], "pending")
        self.assertFalse(entry["usable"])
        self.assertEqual(entry["blocked_reason"], "NOT_MATERIALIZED")
        self.assertEqual(inventory["data"]["pending_count"], 1)

        use = phone.use(item_id, "op-pending-use")
        self.assertFalse(use["ok"])
        self.assertEqual(use["reason_code"], "NOT_MATERIALIZED")
        self.assertEqual(self.consequences(), [])

        # recovery: the same operation key finishes the materialization and the
        # wallet is not charged again
        recovered = phone.purchase(SNACK, "op-pending-01")
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "already_applied")
        self.assertEqual(recovered["data"]["materialization"], "materialized")
        self.assertEqual(recovered["data"]["current_balance"], wpe.MOVE_IN_GRANT - 240)
        self.assertEqual(len(self.economy_state()["transactions"]), 1)
        self.assertIsNotNone(self.placement(item_id))
        self.assertTrue(phone.inventory()["data"]["items"][0]["usable"])

    def test_consume_once_through_the_resolver(self):
        self.start_server()
        phone = self.phone()
        bought = phone.purchase(SNACK, "op-eat-buy")
        self.assertTrue(bought["ok"], bought)
        item_id = bought["data"]["item_instance_id"]

        used = phone.use(item_id, "op-eat-01")
        self.assertTrue(used["ok"], used)
        self.assertEqual(used["status"], "applied")
        receipt = used["data"]["receipt"]
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["object_id"], item_id)
        self.assertEqual(receipt["source"], contract.SOURCE_PURCHASED)
        self.assertEqual(
            used["data"]["purchase_voucher"]["transaction_id"],
            bought["data"]["transaction_id"],
        )
        # the local owner was recorded as such, never as a main-self decision
        self.assertEqual(used["data"]["local_owner"]["source"], "local_owner_phone")
        self.assertEqual(used["data"]["local_owner"]["uid"], os.getuid())
        self.assertEqual(
            used["data"]["local_owner"]["binding"]["subject"], wpe.OWNER
        )

        # the placement is gone; the economy still says OWNED (ownership remains,
        # the exhaust truth is the receipt)
        self.assertIsNone(self.placement(item_id))
        state = self.economy_state()
        self.assertEqual(state["items"][0]["disposition"], wpe.DISPOSITION_OWNED)
        self.assertIn(receipt["execution_id"], self.substrate()["consumptions"])

        # replaying the same key is a success without a second receipt
        replay = phone.use(item_id, "op-eat-01")
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(len(self.substrate()["consumptions"]), 1)

        # a different key cannot consume the same instance again
        second = phone.use(item_id, "op-eat-02")
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason_code"], contract.ALREADY_CONSUMED)
        self.assertEqual(len(self.substrate()["consumptions"]), 1)

        inventory = phone.inventory()
        entry = inventory["data"]["items"][0]
        self.assertEqual(entry["delivery_status"], "CONSUMED")
        self.assertFalse(entry["usable"])
        self.assertEqual(entry["blocked_reason"], contract.ALREADY_CONSUMED)

        # the Body only got its ordinary light manipulation consequence
        # the Body only got its ordinary light manipulation consequence: a 2s
        # operation load, no nutrition/hunger/health/recovery values at all.
        records = self.consequences()
        self.assertEqual(len(records), 1, records)
        record = records[0]
        self.assertEqual(record["action_type"], "CONSUME")
        self.assertFalse(record["raw_body_values"])
        self.assertEqual(record["inputs"][0]["movement_duration_seconds"], 2.0)
        self.assertEqual(record["inputs"][0]["effort"], "light")
        self.assertIn("manipulation only", record["notes"][0])
        for field in ("fatigue", "comfort", "activation", "sensory_load", "tension",
                      "warmth", "hunger", "satiety", "health", "nutrition"):
            self.assertNotIn(field, record, "the consequence must not carry %r" % field)

        # the operation lookup reconciles both halves from the owners
        purchased = phone.operation("op-eat-buy")
        self.assertTrue(purchased["ok"])
        self.assertEqual(purchased["data"]["operation"], "purchase")
        consumed = phone.operation("op-eat-01")
        self.assertTrue(consumed["ok"])
        self.assertEqual(consumed["data"]["operation"], "use")
        self.assertEqual(consumed["data"]["receipt"]["receipt_id"], receipt["receipt_id"])
        unknown = phone.operation("op-never-used")
        self.assertEqual(unknown["status"], "not_found")
        self.assertEqual(unknown["reason_code"], "OPERATION_NOT_FOUND")

    def test_use_refused_when_body_is_not_here(self):
        self.start_server()
        phone = self.phone()
        bought = phone.purchase(SNACK, "op-away-buy")
        self.assertTrue(bought["ok"], bought)
        # the World says she is at the park: the item is real, but not reachable
        state = json.loads(
            (self.home / "data" / "world" / "world_state.json").read_text(encoding="utf-8")
        )
        state["location"] = {"place_id": "park", "area_id": "park"}
        (self.home / "data" / "world" / "world_state.json").write_text(
            json.dumps(state, sort_keys=True), encoding="utf-8"
        )
        used = phone.use(bought["data"]["item_instance_id"], "op-away-01")
        self.assertFalse(used["ok"])
        self.assertEqual(used["reason_code"], contract.NOT_HELD_OR_HERE)
        self.assertEqual(len(self.substrate()["consumptions"]), 0)
        self.assertEqual(self.consequences(), [])


class PhoneConsumeRuleTests(PhoneBase):
    consumable_catalog = None  # nothing declared at all

    def test_economy_instance_without_declaration_is_still_refused(self):
        self.phone_item_state(BED)
        seed_substrate(
            self.home,
            {"item-fixture-01": {"kind": "room", "location_id": PLACE_HOME, "area_id": AREA_HOME}},
        )
        phone = self.phone()
        inventory = phone.inventory()
        entry = inventory["data"]["items"][0]
        self.assertFalse(entry["usable"])
        self.assertEqual(entry["blocked_reason"], contract.NOT_DECLARED)
        used = phone.use("item-fixture-01", "op-nopermit")
        self.assertFalse(used["ok"])
        self.assertEqual(used["reason_code"], contract.NOT_DECLARED)
        self.assertEqual(len(self.substrate()["consumptions"]), 0)



    def test_free_declaration_does_not_open_an_economy_instance(self):
        # A world-declared *free* consumable that is also an economy instance is
        # still refused: v0.4 opens exactly one permit, not the old rule.
        seed_world(
            self.home,
            consumable_catalog={
                BED: {"consumable": True, "free_to_consume": True}
            },
        )
        self.phone_item_state(BED)
        seed_substrate(
            self.home,
            {
                "item-fixture-01": {
                    "kind": "room",
                    "location_id": PLACE_HOME,
                    "area_id": AREA_HOME,
                }
            },
        )
        phone = self.phone()
        entry = phone.inventory()["data"]["items"][0]
        self.assertFalse(entry["usable"])
        self.assertEqual(entry["blocked_reason"], contract.ECONOMY_OBJECT)
        used = phone.use("item-fixture-01", "op-free-claim")
        self.assertFalse(used["ok"])
        self.assertEqual(used["reason_code"], contract.ECONOMY_OBJECT)
        self.assertEqual(len(self.substrate()["consumptions"]), 0)


class PhoneReadOnlyAndKeyTests(PhoneBase):
    """GET is read-only; one key names one operation and one target."""

    def substrate_bytes(self) -> bytes:
        return wbs.substrate_path(self.home).read_bytes()

    def test_get_operation_does_not_materialize_and_post_recovers(self):
        phone = self.phone()
        # money + ownership only: the crash window between the economy save and
        # the substrate materialization
        bought = wpe.phone_purchase_item(
            catalog_item_id=SNACK,
            operation_key="op-get-pending",
            local_owner={
                "source": wpe.LOCAL_OWNER_SOURCE,
                "uid": os.getuid(),
                "subject": wpe.OWNER,
                "home": str(self.home),
            },
            hermes_home=self.home,
            environment=ENVIRONMENT,
        )
        self.assertEqual(bought["status"], "applied")
        item_id = bought["item_instance_id"]
        self.assertIsNone(self.placement(item_id))
        before = self.substrate_bytes()

        # every GET is read-only: none of them may place the instance
        reported = phone.operation("op-get-pending")
        self.assertTrue(reported["ok"], reported)
        self.assertEqual(reported["data"]["operation"], "purchase")
        self.assertEqual(reported["data"]["materialization"], "pending")
        self.assertEqual(
            reported["data"]["materialization_reason_code"], "NOT_MATERIALIZED"
        )
        self.assertIn("POST /api/phone/purchases", reported["data"]["recovery"])
        self.assertFalse(reported["data"]["consumed"])
        self.assertEqual(phone.inventory()["data"]["pending_count"], 1)
        phone.wallet()
        phone.shop()
        phone.inventory()
        self.assertIsNone(self.placement(item_id))
        self.assertEqual(
            self.substrate_bytes(), before, "a GET must never write the substrate"
        )

        # the POST with the same key is the only thing that completes it
        recovered = phone.purchase(SNACK, "op-get-pending")
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(recovered["status"], "already_applied")
        self.assertEqual(recovered["data"]["materialization"], "materialized")
        self.assertEqual(recovered["data"]["current_balance"], wpe.MOVE_IN_GRANT - 240)
        self.assertEqual(len(self.economy_state()["transactions"]), 1)
        self.assertIsNotNone(self.placement(item_id))
        after = phone.operation("op-get-pending")
        self.assertEqual(after["data"]["materialization"], "materialized")
        self.assertEqual(after["data"]["placement"]["location_id"], PLACE_HOME)

    def test_same_key_with_a_different_target_is_a_conflict(self):
        self.start_server()
        phone = self.phone()
        first = phone.purchase(SNACK, "op-target-a")
        second = phone.purchase(SNACK, "op-target-b")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        item_a = first["data"]["item_instance_id"]
        item_b = second["data"]["item_instance_id"]
        self.assertNotEqual(item_a, item_b)

        used = phone.use(item_a, "op-target-use")
        self.assertTrue(used["ok"], used)
        self.assertEqual(used["status"], "applied")
        self.assertEqual(used["data"]["receipt"]["object_id"], item_a)

        # the same key may not be replayed against a different instance
        hijack = phone.use(item_b, "op-target-use")
        self.assertFalse(hijack["ok"])
        self.assertEqual(hijack["reason_code"], "OPERATION_TARGET_CONFLICT")
        self.assertEqual(hijack["data"]["object_id"], item_a)
        self.assertEqual(hijack["data"]["requested_object_id"], item_b)
        # B is untouched: still placed, no receipt naming it, still usable
        self.assertIsNotNone(self.placement(item_b))
        receipts = self.substrate()["consumptions"]
        self.assertEqual(
            [r for r in receipts.values() if r["object_id"] == item_b], []
        )
        inventory = phone.inventory()["data"]["items"]
        by_id = {entry["item_instance_id"]: entry for entry in inventory}
        self.assertTrue(by_id[item_b]["usable"])
        self.assertFalse(by_id[item_b]["consumed"])
        self.assertTrue(by_id[item_a]["consumed"])

    def test_key_cannot_cross_purchase_and_use(self):
        self.start_server()
        phone = self.phone()
        bought = phone.purchase(SNACK, "op-cross-key")
        self.assertTrue(bought["ok"], bought)
        item_id = bought["data"]["item_instance_id"]

        # (i) the key already names a purchase: it may not be used to consume
        cross_use = phone.use(item_id, "op-cross-key")
        self.assertFalse(cross_use["ok"])
        self.assertEqual(cross_use["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(cross_use["data"]["operation"], "purchase")
        self.assertEqual(len(self.substrate()["consumptions"]), 0)
        self.assertIsNotNone(self.placement(item_id))

        # (ii) a key that already names a use execution may not be reused to buy
        second = phone.purchase(SNACK, "op-cross-buy2")
        self.assertTrue(second["ok"], second)
        used = phone.use(second["data"]["item_instance_id"], "op-cross-use")
        self.assertTrue(used["ok"], used)
        cross_buy = phone.purchase(SNACK, "op-cross-use")
        self.assertFalse(cross_buy["ok"])
        self.assertEqual(cross_buy["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(cross_buy["data"]["operation"], "use")
        # no third transaction was written by the refused purchase
        self.assertEqual(len(self.economy_state()["transactions"]), 2)

    def test_consumed_instance_is_terminal_and_replay_does_not_resurrect(self):
        """buy -> use -> inventory(no pending) -> same-key replay -> still gone."""

        self.start_server()
        phone = self.phone()
        bought = phone.purchase(SNACK, "op-terminal-buy")
        self.assertTrue(bought["ok"], bought)
        item_id = bought["data"]["item_instance_id"]
        balance_after_buy = self.balance()
        self.assertEqual(balance_after_buy, wpe.MOVE_IN_GRANT - 240)

        used = phone.use(item_id, "op-terminal-use")
        self.assertTrue(used["ok"], used)
        self.assertEqual(used["status"], "applied")
        receipt = used["data"]["receipt"]
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["object_id"], item_id)
        self.assertIsNone(self.placement(item_id))
        self.assertEqual(len(self.substrate()["consumptions"]), 1)

        # 1. inventory: one terminal state -- not "consumed" + "pending" at once
        inventory = phone.inventory()
        self.assertTrue(inventory["ok"], inventory)
        self.assertEqual(inventory["data"]["pending_count"], 0)
        entry = inventory["data"]["items"][0]
        self.assertEqual(entry["delivery_status"], "CONSUMED")
        self.assertEqual(entry["materialization"], "consumed")
        self.assertEqual(entry["location"], "consumed")
        self.assertTrue(entry["consumed"])
        self.assertFalse(entry["usable"])
        self.assertEqual(entry["blocked_reason"], contract.ALREADY_CONSUMED)
        self.assertEqual(entry["receipt_id"], receipt["receipt_id"])

        capabilities_path = (
            self.home / "state" / "world_object_capabilities.json"
        )
        capabilities_before = capabilities_path.read_bytes()
        substrate_before = self.substrate_bytes()

        # 2. the same purchase key replayed after the consumption: the original
        #    purchase is still applied, in its terminal state -- and nothing is
        #    resurrected.
        replay = phone.purchase(SNACK, "op-terminal-buy")
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(replay["data"]["materialization"], "consumed")
        self.assertTrue(replay["data"]["consumed"])
        self.assertEqual(replay["data"]["delivery_status"], "CONSUMED")
        self.assertEqual(replay["data"]["location"], "consumed")
        self.assertNotIn("placement", replay["data"])
        self.assertNotIn("materialization_reason_code", replay["data"])
        self.assertEqual(replay["data"]["current_balance"], balance_after_buy)
        self.assertEqual(replay["data"]["receipt_id"], receipt["receipt_id"])

        # 3. no resurrection, no new receipt, no declaration write, no charge
        self.assertIsNone(self.placement(item_id))
        consumptions = self.substrate()["consumptions"]
        self.assertEqual(len(consumptions), 1)
        self.assertIn(receipt["execution_id"], consumptions)
        self.assertEqual(
            self.substrate_bytes(), substrate_before,
            "a consumed replay must not touch the substrate",
        )
        self.assertEqual(
            capabilities_path.read_bytes(), capabilities_before,
            "a consumed replay must not write a declaration",
        )
        state = self.economy_state()
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(state["items"][0]["disposition"], wpe.DISPOSITION_OWNED)
        self.assertEqual(self.balance(), balance_after_buy)

        # 4. the GET view agrees, and offers no recovery that could resurrect it
        after = phone.operation("op-terminal-buy")
        self.assertTrue(after["ok"], after)
        self.assertEqual(after["data"]["materialization"], "consumed")
        self.assertTrue(after["data"]["consumed"])
        self.assertEqual(after["data"]["location"], "consumed")
        self.assertNotIn("recovery", after["data"])
        inventory_again = phone.inventory()
        self.assertEqual(inventory_again["data"]["pending_count"], 0)
        self.assertEqual(
            inventory_again["data"]["items"][0]["materialization"], "consumed"
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
