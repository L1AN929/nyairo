#!/usr/bin/env python3
"""F: ``CONSUME`` -- one free/held object instance, one receipt, one body effect.

Everything here is the *real* local stack: a real ``WorldBodyService`` (single
writer, real lease), the real gateway surface with its one-shot authorization
registry, the real World substrate file, the real execution ledger and the real
Body runtime/consequence stores.  Only the World content (a food instance and
its world-owned declaration) is a fixture, and it lives in a unique home under
``/tmp/nyaw05r.jdK3q/f-daily-*`` that is deliberately **created once and
retained** (no delete, no cleanup, no user data touched).

What is asserted, in contract order
-----------------------------------
* one food = one object instance: consuming it removes exactly that instance and
  writes exactly one receipt, in the same substrate state;
* the same execution cannot consume twice, a second execution cannot consume the
  same instance again, and no candidate "comes back" from a static declaration;
* refused: undeclared / not consumable / not free / elsewhere / bad pose / closed
  place supply / economy instance / no authorization / wrong scope / stale deps
  -- every refusal leaves the substrate byte-identical;
* the Body gets the small manipulation load of the operation, exactly once, and
  explicitly **no nutrition/hunger/satiety/health** claim.

Run:  python3 tests/test_world_consume.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
import unittest.mock
import uuid
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
RUNTIME_ROOT = ROOT / "components" / "world"
sys.path[:0] = [str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE)]

import body_consequence as bcon  # noqa: E402
import world_action_resolver as war  # noqa: E402
import world_body_config as wbc  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_consumable_contract as contract  # noqa: E402

#: Native scratch root: fixtures are created once and kept (never removed).
FIXTURE_ROOT = pathlib.Path("/tmp/nyaw05r.jdK3q")

#: 2026-09-27 12:00Z == 21:00 Asia/Tokyo -> convenience_store (07-23) is OPEN.
OPEN_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
#: 2026-09-27 20:00Z == 05:00 Asia/Tokyo -> convenience_store is CLOSED.
CLOSED_NOW = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)

PLACE_STORE = "convenience_store"
PLACE_HOME = "home"

ONIGIRI = "f-food-onigiri-01"
BENTO = "f-food-bento-01"
CHAIR = "f-chair-01"
ECONOMY_SNACK = "item-food-snack-01"

CONSUMABLES = {
    ONIGIRI: {"consumable": True, "free_to_consume": True},
    BENTO: {"consumable": True, "free_to_consume": True},
}


def unique_home(prefix: str = "f-daily-consume-") -> pathlib.Path:
    """A unique, retained fixture home (created once, never removed)."""

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    home = FIXTURE_ROOT / (prefix + uuid.uuid4().hex[:12])
    home.mkdir(parents=True, exist_ok=False)
    (home / "data" / "world").mkdir(parents=True, exist_ok=True)
    (home / "data" / "body").mkdir(parents=True, exist_ok=True)
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / "run").mkdir(parents=True, exist_ok=True)
    return home


def seed_world(
    home: pathlib.Path,
    *,
    place: str = PLACE_STORE,
    consumables: dict | None = None,
    objects: dict | None = None,
    economy: dict | None = None,
) -> None:
    (home / "data" / "world" / "world_state.json").write_text(
        json.dumps(
            {
                "facts": [],
                "location": {"area_id": place, "place_id": place},
                "revision": 5,
                "scene": {"revision": 5, "scene_id": place},
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
            {"schema_version": "world.timeline.v1", "revision": 1,
             "world_id": "shiomi_city", "processes": []},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (home / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")
    document = {
        "schema_version": wbs.CAPABILITIES_SCHEMA,
        "objects": objects or {},
        "consumables": CONSUMABLES if consumables is None else consumables,
    }
    (home / "state" / "world_object_capabilities.json").write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    if economy is not None:
        (home / "state" / "world_property_economy.json").write_text(
            json.dumps(economy, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )

def valid_economy(
    *, item_instance_ids=(), at: str = "2026-09-27T00:00:00+00:00"
) -> dict:
    """A property-economy document the *economy owner's own validator* accepts.

    Built from the owner's own ``_empty_state`` and validated by
    ``world_property_economy.validate_state`` before it is returned, so a
    "legal economy" fixture can never silently drift into an illegal one (which
    would make the fail-closed tests pass for the wrong reason).
    """

    import world_property_economy as wpe

    state = wpe._empty_state(created_at=at)
    items, transactions = [], []
    for index, object_id in enumerate(item_instance_ids):
        intent = "intent-f%02d" % index
        transaction_id = "txn-f%02d" % index
        provenance = {
            "source": "verified_main_self_buy_item",
            "purchase_intent_id": intent,
            "transaction_id": transaction_id,
            "delivery": "included_home_delivery",
            "source_turn_id": "turn-f%02d" % index,
            "source_message_id": "msg-f%02d" % index,
        }
        transactions.append(
            {
                "transaction_id": transaction_id,
                "transaction_kind": "PURCHASE",
                "direction": "DEBIT",
                "amount": 5800,
                "currency": wpe.CURRENCY,
                "occurred_at": at,
                "catalog_item_id": "basic_bed",
                "purchase_intent_id": intent,
                "delivery_location": wpe.HOME_ITEM_LOCATION,
                "provenance": provenance,
            }
        )
        items.append(
            {
                "item_instance_id": object_id,
                "catalog_item_id": "basic_bed",
                "owner": wpe.OWNER,
                "acquired_at": at,
                "acquisition_kind": "PURCHASE",
                "acquisition_provenance": provenance,
                "acquisition_price": 5800,
                "currency": wpe.CURRENCY,
                "current_location": wpe.HOME_ITEM_LOCATION,
                "disposition": wpe.DISPOSITION_OWNED,
            }
        )
    state["transactions"] = transactions
    state["items"] = items
    return wpe.validate_state(state)



def seed_substrate(
    home: pathlib.Path, placements: dict, *, pose: str = "standing"
) -> dict:
    state = wbs.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
    state["pose"] = pose
    state["orientation"] = "south"
    for object_id, target in placements.items():
        state = wbs.place_object(state, object_id, target)
    wbs.SubstrateStore(wbs.substrate_path(home)).save(state)
    return state


def room(place: str) -> dict:
    return {"kind": "room", "location_id": place, "area_id": place}


def held(slot: str = "right_hand") -> dict:
    return {"kind": "hand", "slot_id": slot}


class ConsumeBase(unittest.TestCase):
    """Real service + real gateway + real stores, one retained home per test."""

    credentials: dict | None = None
    place: str = PLACE_STORE
    now: datetime = OPEN_NOW
    pose: str = "standing"
    placements: dict = {ONIGIRI: room(PLACE_STORE)}

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        self.home = unique_home()
        seed_world(self.home, place=self.place, **(self.credentials or {}))
        seed_substrate(self.home, dict(self.placements), pose=self.pose)
        self.service = self.start(self.now)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    # -- plumbing -------------------------------------------------------
    def config(self):
        return wbc.load_config(
            {"CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1", wbs.FEATURE_ENV: "1"},
            runtime_root=RUNTIME_ROOT,
            world_home=self.home,
            runtime_mode="canonical",
            authority="production",
            world_id="shiomi_city",
        )

    def start(self, now: datetime, *, instance_id: str = "f-consume"):
        if getattr(self, "service", None) is not None:
            self.service.shutdown()
        service = wbsvc.WorldBodyService(
            self.config(), now=now, instance_id=instance_id
        )
        self.addCleanup(service.shutdown)
        service.startup()
        self.service = service
        return service

    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.home)).load()

    def substrate_bytes(self) -> bytes:
        return wbs.substrate_path(self.home).read_bytes()

    def placement(self, object_id: str):
        return wbs.placement_of(self.substrate(), object_id)

    def receipt(self, execution_id: str):
        return wbs.consumption_receipt(self.substrate(), execution_id)

    def receipts(self) -> dict:
        return self.substrate()["consumptions"]

    def consequence_records(self) -> list[dict]:
        path = self.home / "data" / "body" / "consequences.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def transition_records(self) -> list[dict]:
        path = self.home / "data" / "body" / "transitions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def authorize(self, *, verb: str, params: dict, execution_id: str, decision=None):
        return self.service.gateway_surface.authorizations.issue(
            decision_id=decision,
            intent_id="world-intent:" + execution_id,
            intent_fingerprint="fp-" + execution_id,
            execution_id=execution_id,
            actor="chiyo",
            verb=verb,
            params=params,
            authority_generation=5,
        )

    def submit(
        self,
        *,
        verb: str = "CONSUME",
        params: dict,
        execution_id: str,
        decision=None,
        authorization_id=None,
    ) -> dict:
        if authorization_id is None:
            record = self.authorize(
                verb=verb, params=params, execution_id=execution_id, decision=decision
            )
            authorization_id = record["authorization_id"]
        return self.service.gateway_surface.submit_action(
            {
                "action": verb,
                "execution_id": execution_id,
                "params": params,
                "authorization_id": authorization_id,
                "decision_ref": decision,
            }
        )

    def act(self, *, verb="CONSUME", params: dict, execution_id: str, deps=None):
        """The service's own mutation path (Resolver + body consequence)."""

        return self.service.act(
            {
                "action": verb,
                "execution_id": execution_id,
                "actor_id": "chiyo",
                "body_id": "chiyo_body",
                "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
                "confirm_apply": True,
                "observed_dependencies": (
                    deps if deps is not None
                    else self.service.resolver._dependencies()
                ),
                "params": params,
            }
        )

    def propose(self, *, verb="CONSUME", params: dict, execution_id: str, deps=None):
        return self.service.resolver.resolve(
            {
                "action": verb,
                "execution_id": execution_id,
                "actor_id": "chiyo",
                "body_id": "chiyo_body",
                "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
                "confirm_apply": True,
                "observed_dependencies": (
                    deps if deps is not None
                    else self.service.resolver._dependencies()
                ),
                "params": params,
            }
        )

    def consume_actions(self) -> dict:
        actions = self.service.gateway_surface.get_available_actions()["actions"]
        return next(action for action in actions if action["verb"] == "CONSUME")

    def consume_consequences(self, execution_id: str | None = None) -> list[dict]:
        return [
            record for record in self.consequence_records()
            if record.get("action_type") == "CONSUME"
            and (execution_id is None or record.get("execution_id") == execution_id)
        ]


# ---------------------------------------------------------------------------
# the happy path: one instance, one receipt, one body effect
# ---------------------------------------------------------------------------


class ConsumeSucceedsTest(ConsumeBase):
    def test_01_one_instance_is_consumed_once_through_the_gateway(self):
        before_placement = self.placement(ONIGIRI)
        self.assertEqual(before_placement, room(PLACE_STORE))
        before_revision = self.substrate()["revision"]

        response = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-01"
        )
        self.assertEqual(response["status"], "applied", response)
        self.assertEqual(response["reason_code"], war.CODE_OK)

        # the instance is gone from the live projection ...
        self.assertIsNone(self.placement(ONIGIRI))
        # ... and exactly one receipt says which execution ate it
        receipt = self.receipt("world-exec:f-consume-01")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["object_id"], ONIGIRI)
        self.assertEqual(receipt["body_id"], "chiyo_body")
        self.assertEqual(receipt["actor_id"], "chiyo")
        self.assertEqual(receipt["source"], contract.SOURCE_PLACE_SUPPLY)
        self.assertEqual(receipt["before_revision"], before_revision)
        self.assertEqual(
            receipt["proposal_fingerprint"], contract.proposal_fingerprint(ONIGIRI)
        )
        self.assertEqual(receipt["placement_before"], room(PLACE_STORE))
        self.assertEqual(len(self.receipts()), 1)
        self.assertTrue(contract.verify_receipt(
            receipt, execution_id="world-exec:f-consume-01", object_id=ONIGIRI,
            body_id="chiyo_body")["ok"])

        # the body got the manipulation load of the act, once
        records = self.consume_consequences("world-exec:f-consume-01")
        self.assertEqual(len(records), 1, records)
        record = records[0]
        self.assertEqual(record["state"], "applied")
        self.assertEqual(record["verdict"], "SUCCESS")
        self.assertEqual(record["resource_ref"], ONIGIRI)
        self.assertEqual(len(record["inputs"]), 1)
        self.assertEqual(record["inputs"][0]["class"], "PHYSICAL_RESULT")
        self.assertEqual(
            record["inputs"][0]["movement_duration_seconds"],
            bcon.CONSUME_OPERATION_SECONDS,
        )
        self.assertTrue(any("consume_operation_load_model" in note
                            for note in record["notes"]), record["notes"])
        if record.get("body_transition_ref"):
            matches = [t for t in self.transition_records()
                       if t.get("transition_id") == record["body_transition_ref"]]
            self.assertEqual(len(matches), 1, "one transition per consequence")

    def test_02_no_nutrition_hunger_or_satiety_claim_anywhere(self):
        self.submit(params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-02")
        record = self.consume_consequences("world-exec:f-consume-02")[0]
        blob = json.dumps(record, ensure_ascii=False).lower()
        classes = {item["class"] for item in record["inputs"]}
        self.assertEqual(classes, {"PHYSICAL_RESULT"})
        # Eating opens no recovery session and produces no intake input class.
        self.assertNotIn("RECOVERY", classes)
        # The only place those words may appear is the explicit disclaimer; no
        # field of the record may *assert* one of them.
        claim_surface = {
            key: value for key, value in record.items() if key != "notes"
        }
        blob = json.dumps(claim_surface, ensure_ascii=False).lower()
        for forbidden in ("nutrition", "calorie", "hunger", "satiety", "health",
                          "sated", "intake"):
            self.assertNotIn(
                forbidden, blob,
                "a consumption consequence must not claim %r" % forbidden,
            )
        self.assertTrue(
            any("not nutrition" in note for note in record["notes"]),
            record["notes"],
        )

    def test_03_a_second_execution_cannot_consume_the_same_instance(self):
        self.submit(params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-03a")
        after_first = self.substrate_bytes()

        response = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-03b"
        )
        self.assertIn(response["status"], ("rejected", "failed"), response)
        self.assertEqual(self.substrate_bytes(), after_first)
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(len(self.consume_consequences()), 1)

    def test_04_replay_through_the_gateway_consumes_nothing_twice(self):
        first = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-04"
        )
        self.assertEqual(first["status"], "applied")
        after_first = self.substrate_bytes()

        replay = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-04"
        )
        # The one-shot authorization, the (now empty) candidate list and the
        # resolver's own idempotency all refuse a second attempt; either way
        # nothing moves and nothing is eaten.
        self.assertIn(replay["status"], ("rejected", "failed", "already_applied"), replay)
        self.assertEqual(self.substrate_bytes(), after_first)
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(len(self.consume_consequences()), 1)

        view = self.service.gateway_surface.get_action_result("world-exec:f-consume-04")
        self.assertEqual(view["verdict"], "SUCCESS")
        self.assertTrue(view["receipt_present"], view)
        self.assertEqual(view["consumption_receipt"]["object_id"], ONIGIRI)

    def test_05_replay_through_the_service_path_is_already_applied(self):
        first = self.act(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-05"
        )
        self.assertEqual(first["status"], "applied", first)
        after_first = self.substrate_bytes()

        replay = self.act(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-05"
        )
        self.assertEqual(replay["status"], "already_applied", replay)
        self.assertEqual(self.substrate_bytes(), after_first)
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(len(self.consume_consequences()), 1)
    def test_06_candidate_projection_lists_then_drops_the_instance(self):
        action = self.consume_actions()
        self.assertTrue(action["available"], action)
        self.assertEqual(
            [c["object_id"] for c in action["candidates"]], [ONIGIRI]
        )
        self.assertEqual(action["candidates"][0]["source"], contract.SOURCE_PLACE_SUPPLY)
        self.assertTrue(action["contract"]["one_instance_per_consumption"])

        self.submit(params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-06")

        action = self.consume_actions()
        self.assertFalse(action["available"], action)
        self.assertEqual(action["candidates"], [])
        # a static declaration never resurrects a consumed instance
        self.assertEqual(self.placement(ONIGIRI), None)


class ConsumeHeldFoodTest(ConsumeBase):
    placements = {ONIGIRI: held()}

    def test_07_held_food_is_consumed(self):
        response = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-07"
        )
        self.assertEqual(response["status"], "applied", response)
        self.assertIsNone(self.placement(ONIGIRI))
        self.assertEqual(self.receipt("world-exec:f-consume-07")["source"],
                         contract.SOURCE_HELD)

    def test_08_held_food_is_not_refused_by_a_closed_shop(self):
        # Same instance, but the (closed) store is where the body stands now.
        self.start(CLOSED_NOW, instance_id="f-consume-closed-held")
        response = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-consume-08"
        )
        self.assertEqual(response["status"], "applied", response)
        self.assertIsNone(self.placement(ONIGIRI))


# ---------------------------------------------------------------------------
# refusals: every one leaves the substrate byte-identical and the body quiet
# ---------------------------------------------------------------------------


class ConsumeRefusalsTest(ConsumeBase):
    """Refusals, each with its own reason and zero side effects.

    The specific consume-contract codes are observed on the Resolver path (that
    is the owner of the rule); the gateway's own layer is exercised where it
    owns the answer: authorization scope and the exposure policy.  Every refusal
    below leaves the substrate byte-identical and the Body untouched.
    """

    def assert_refused(self, response: dict, *, code: str | None = None,
                       detail: str | None = None) -> None:
        self.assertIn(response["status"], ("rejected", "failed"), response)
        if code is not None:
            self.assertEqual(response["reason_code"], code, response)
        if detail is not None:
            self.assertIn(detail, str(response.get("legacy_reason_code")))
        self.assertEqual(self.consume_consequences(), [])

    def test_10_undeclared_object_is_refused(self):
        seed_substrate(self.home, {CHAIR: room(PLACE_STORE)})
        before = self.substrate_bytes()
        r = self.propose(params={"object_id": CHAIR}, execution_id="world-exec:f-10")
        self.assert_refused(r, code=war.CODE_NOT_CONSUMABLE, detail="NOT_DECLARED")
        self.assertEqual(self.substrate_bytes(), before)
        self.assertIsNotNone(self.placement(CHAIR))

    def test_11_object_declared_not_consumable_is_refused(self):
        # A takeable, portable object is still not food: the M15 takeable
        # capability is not a consumable capability.
        seed_world(
            self.home,
            objects={CHAIR: {"takeable": True, "portable": True,
                             "placeable": True, "fixed": False}},
            consumables={CHAIR: {"consumable": False, "free_to_consume": True}},
        )
        seed_substrate(self.home, {CHAIR: room(PLACE_STORE)})
        before = self.substrate_bytes()
        r = self.propose(params={"object_id": CHAIR}, execution_id="world-exec:f-11")
        self.assert_refused(r, code=war.CODE_NOT_CONSUMABLE, detail="NOT_CONSUMABLE")
        self.assertEqual(self.substrate_bytes(), before)

    def test_12_declared_but_not_free_is_refused(self):
        seed_world(self.home, consumables={
            ONIGIRI: {"consumable": True, "free_to_consume": False}})
        before = self.substrate_bytes()
        r = self.propose(params={"object_id": ONIGIRI}, execution_id="world-exec:f-12")
        self.assert_refused(r, code=war.CODE_NOT_CONSUMABLE, detail="NOT_FREE")
        self.assertEqual(self.substrate_bytes(), before)
        self.assertIsNotNone(self.placement(ONIGIRI))

    def test_13_object_at_another_place_is_refused(self):
        seed_substrate(self.home, {ONIGIRI: room(PLACE_HOME)})
        before = self.substrate_bytes()
        r = self.propose(params={"object_id": ONIGIRI}, execution_id="world-exec:f-13")
        self.assert_refused(r, code=war.CODE_NOT_REACHABLE, detail="NOT_HELD_OR_HERE")
        self.assertEqual(self.substrate_bytes(), before)

    def test_14_pose_that_cannot_manipulate_is_refused(self):
        seed_substrate(self.home, {ONIGIRI: room(PLACE_STORE)}, pose="lying")
        self.start(OPEN_NOW, instance_id="f-consume-lying")
        before = self.substrate_bytes()
        r = self.propose(params={"object_id": ONIGIRI}, execution_id="world-exec:f-14")
        self.assert_refused(r, code=war.CODE_NOT_REACHABLE, detail="POSE_CANNOT_CONSUME")
        self.assertEqual(self.substrate_bytes(), before)

    def test_15_closed_store_supply_is_refused(self):
        self.start(CLOSED_NOW, instance_id="f-consume-closed")
        before = self.substrate_bytes()
        r = self.propose(params={"object_id": ONIGIRI}, execution_id="world-exec:f-15")
        self.assert_refused(r, code=war.CODE_NOT_ALLOWED, detail="LOCATION_CLOSED")
        self.assertEqual(self.substrate_bytes(), before)
        self.assertIsNotNone(self.placement(ONIGIRI))

        # the read path agrees, and the surface refuses as well
        action = self.consume_actions()
        self.assertFalse(action["available"])
        self.assertEqual(action["blocked_reason"], "LOCATION_CLOSED")
        g = self.submit(params={"object_id": ONIGIRI}, execution_id="world-exec:f-15g")
        self.assert_refused(g)
        self.assertEqual(g["reason_code"], "NOT_EXPOSED")
        self.assertEqual(self.substrate_bytes(), before)

    def test_16_economy_instance_is_refused_even_if_declared_free(self):
        # A *legal* economy document (validated by the economy owner itself)
        # that owns this very instance.  It must still be refused: this version
        # has no payment/ownership-consumption authority, and a price never
        # makes an instance free.
        economy = valid_economy(item_instance_ids=(ECONOMY_SNACK,))
        seed_world(
            self.home,
            consumables={ECONOMY_SNACK: {"consumable": True, "free_to_consume": True}},
            economy=economy,
        )
        seed_substrate(self.home, {ECONOMY_SNACK: room(PLACE_STORE)})
        before = self.substrate_bytes()
        r = self.propose(params={"object_id": ECONOMY_SNACK},
                         execution_id="world-exec:f-16")
        self.assert_refused(r, code=war.CODE_NOT_CONSUMABLE, detail="ECONOMY_OBJECT")
        self.assertEqual(self.substrate_bytes(), before)
        self.assertIsNotNone(self.placement(ECONOMY_SNACK))
        self.assertEqual(self.consume_actions()["blocked_reason"], "ECONOMY_OBJECT")

    def test_17_missing_authorization_is_refused(self):
        before = self.substrate_bytes()
        r = self.service.gateway_surface.submit_action(
            {
                "action": "CONSUME",
                "execution_id": "world-exec:f-17",
                "params": {"object_id": ONIGIRI},
            }
        )
        self.assert_refused(r)
        self.assertEqual(r["reason_code"], "AUTHORIZATION_REQUIRED")
        self.assertEqual(self.substrate_bytes(), before)

    def test_18_authorization_for_another_verb_is_refused(self):
        record = self.authorize(
            verb="PICK_UP", params={"object_id": ONIGIRI, "slot_id": "right_hand"},
            execution_id="world-exec:f-18",
        )
        before = self.substrate_bytes()
        r = self.service.gateway_surface.submit_action(
            {
                "action": "CONSUME",
                "execution_id": "world-exec:f-18",
                "params": {"object_id": ONIGIRI},
                "authorization_id": record["authorization_id"],
            }
        )
        self.assert_refused(r)
        self.assertEqual(r["reason_code"], "VERB_MISMATCH")
        self.assertEqual(self.substrate_bytes(), before)

    def test_19_authorization_scope_mismatch_is_refused(self):
        record = self.authorize(
            verb="CONSUME", params={"object_id": BENTO},
            execution_id="world-exec:f-19",
        )
        before = self.substrate_bytes()
        r = self.service.gateway_surface.submit_action(
            {
                "action": "CONSUME",
                "execution_id": "world-exec:f-19",
                "params": {"object_id": ONIGIRI},
                "authorization_id": record["authorization_id"],
            }
        )
        self.assert_refused(r)
        self.assertEqual(r["reason_code"], "ARGS_MISMATCH")
        self.assertEqual(self.substrate_bytes(), before)

    def test_20_stale_dependencies_are_refused(self):
        deps = dict(self.service.resolver._dependencies())
        deps["body.revision"] = 999
        before = self.substrate_bytes()
        r = self.propose(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-20", deps=deps
        )
        self.assert_refused(r, code=war.CODE_STALE_STATE)
        self.assertEqual(self.substrate_bytes(), before)

    def test_21_no_consumable_candidate_means_the_verb_is_not_exposed(self):
        seed_world(self.home, consumables={})
        seed_substrate(self.home, {CHAIR: room(PLACE_STORE)})
        self.start(OPEN_NOW, instance_id="f-consume-none")
        action = self.consume_actions()
        self.assertFalse(action["available"])
        self.assertEqual(action["blocked_reason"], "NO_CONSUMABLE_OBJECT")
        before = self.substrate_bytes()
        r = self.submit(params={"object_id": CHAIR}, execution_id="world-exec:f-21")
        self.assert_refused(r)
        self.assertEqual(r["reason_code"], "NOT_EXPOSED")
        self.assertEqual(self.substrate_bytes(), before)


# ---------------------------------------------------------------------------
# economy facts: "no economy owner" is not the same as "economy unreadable"
# ---------------------------------------------------------------------------


class ConsumeEconomyFactsTest(ConsumeBase):
    """A broken property-economy document must never become permission.

    The distinction this class locks down:

    * **absent** -- the world has no property-economy document, so "this is not
      an economy object" is provable and a declared free/held consumable still
      works (unchanged behaviour);
    * **unreadable** -- the document exists but cannot be probed, read, parsed
      or validated, so nothing is provable and the consumption is refused by
      both the candidate projection and the resolver, with the substrate, the
      receipt block and the Body all untouched.
    """

    def economy_path(self) -> pathlib.Path:
        return self.home / "state" / "world_property_economy.json"

    def write_economy(self, text: str) -> None:
        self.economy_path().write_text(text, encoding="utf-8")

    def write_legal_economy(self) -> None:
        self.write_economy(
            json.dumps(valid_economy(), ensure_ascii=False, sort_keys=True)
        )

    def assert_economy_unreadable(self, tag: str = "1") -> None:
        """Every entry point refuses, and nothing observable moves.

        ``tag`` keeps the one-shot authorization ids distinct between
        sub-cases: an authorization is bound to (execution, verb, args, second),
        so re-using an execution id inside one second would be answered by the
        authorization firewall instead of the economy rule.
        """

        action = self.consume_actions()
        self.assertFalse(action["available"], action)
        self.assertEqual(action["blocked_reason"], "ECONOMY_FACTS_UNAVAILABLE")
        self.assertEqual(action["candidates"], [])

        before = self.substrate_bytes()

        # gateway: the only formal write path
        gateway = self.submit(
            params={"object_id": ONIGIRI},
            execution_id="world-exec:f-econ-gateway-" + tag,
        )
        self.assertEqual(gateway["status"], "rejected", gateway)
        self.assertEqual(gateway["reason_code"], "NOT_EXPOSED")
        self.assertTrue(gateway.get("world_unchanged"))

        # service path (resolver + body consequence seam)
        service = self.act(
            params={"object_id": ONIGIRI},
            execution_id="world-exec:f-econ-service-" + tag,
        )
        self.assertEqual(service["status"], "rejected", service)
        self.assertEqual(service["reason_code"], war.CODE_NOT_CONSUMABLE)

        # resolver: the contract's own reason must be explicit, not "not declared"
        resolver = self.propose(
            params={"object_id": ONIGIRI},
            execution_id="world-exec:f-econ-resolver-" + tag,
        )
        self.assertEqual(resolver["status"], "rejected", resolver)
        self.assertEqual(resolver["reason_code"], war.CODE_NOT_CONSUMABLE)
        self.assertIn("ECONOMY_FACTS_UNAVAILABLE", resolver["legacy_reason_code"])

        self.assertEqual(self.substrate_bytes(), before)
        self.assertEqual(self.receipts(), {})
        self.assertEqual(self.consume_consequences(), [])
        self.assertIsNotNone(self.placement(ONIGIRI), "the instance is still there")

    def test_30_corrupt_json_is_refused_everywhere(self):
        # The exact shape the parent probe used: a truncated object.
        self.write_economy('{"items":')
        self.assert_economy_unreadable()

    def test_31_truncated_legal_prefix_is_refused(self):
        self.write_economy(
            '{"schema_version": "world.property.economy.v1", "items": ['
        )
        self.assert_economy_unreadable()

    def test_32_non_object_root_is_refused(self):
        for index, text in enumerate(("[]", "12", '"economy"', "null")):
            with self.subTest(root=text):
                self.write_economy(text)
                self.assert_economy_unreadable(tag=str(index))

    def test_33_legal_json_with_an_illegal_economy_structure_is_refused(self):
        # Valid JSON, but not an economy the owner's own schema accepts.
        self.write_economy(
            json.dumps({"schema_version": "world.property.economy.v1", "items": []})
        )
        self.assert_economy_unreadable()

    def test_34_os_error_is_not_absence(self):
        from types import SimpleNamespace

        def _denied(_path):
            raise PermissionError(13, "permission denied")

        original = contract.os
        contract.os = SimpleNamespace(stat=_denied, environ=dict(os.environ))
        self.addCleanup(setattr, contract, "os", original)
        # The path does not even exist here: a failing probe must still be
        # reported as unreadable, never as "no economy owner".
        self.assertFalse(self.economy_path().exists())
        self.assert_economy_unreadable()

    def test_35_non_regular_file_is_refused(self):
        self.economy_path().mkdir()
        self.assert_economy_unreadable()

    def test_36_economy_past_the_read_bound_is_refused(self):
        self.write_legal_economy()
        with unittest.mock.patch.object(contract, "MAX_ECONOMY_BYTES", 64):
            self.assert_economy_unreadable()

    def test_37_corrupt_economy_blocks_held_food_too(self):
        seed_substrate(self.home, {ONIGIRI: held()})
        self.write_economy('{"items":')
        self.assert_economy_unreadable()

    def test_38_absent_economy_keeps_the_existing_behaviour(self):
        self.assertFalse(self.economy_path().exists())
        response = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-econ-absent"
        )
        self.assertEqual(response["status"], "applied", response)
        self.assertIsNone(self.placement(ONIGIRI))
        self.assertIsNotNone(self.receipt("world-exec:f-econ-absent"))

    def test_39_legal_empty_economy_keeps_the_existing_behaviour(self):
        self.write_legal_economy()
        response = self.submit(
            params={"object_id": ONIGIRI}, execution_id="world-exec:f-econ-empty"
        )
        self.assertEqual(response["status"], "applied", response)
        self.assertEqual(len(self.consume_consequences("world-exec:f-econ-empty")), 1)

    def test_40_facts_loader_separates_absent_present_and_unreadable(self):
        # Direct regression on the loader: the pre-fix behaviour answered
        # "absent" (or an empty document) for every failure.
        absent = contract.economy_facts(self.home)
        self.assertEqual(absent["status"], contract.ECONOMY_ABSENT)
        self.assertIsNone(absent["economy"])
        self.assertIsNone(absent["reason_code"])

        self.write_economy('{"items":')
        corrupt = contract.economy_facts(self.home)
        self.assertEqual(corrupt["status"], contract.ECONOMY_UNREADABLE)
        self.assertIsNone(corrupt["economy"])
        self.assertEqual(corrupt["reason_code"], contract.ECONOMY_FACTS_UNAVAILABLE)
        self.assertIn("corrupt JSON", corrupt["detail"])

        self.write_legal_economy()
        present = contract.economy_facts(self.home)
        self.assertEqual(present["status"], contract.ECONOMY_PRESENT)
        self.assertIsInstance(present["economy"], dict)
        self.assertEqual(present["economy"]["items"], [])


if __name__ == "__main__":
    unittest.main()
