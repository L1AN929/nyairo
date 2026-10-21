#!/usr/bin/env python3
"""v0.4 phone shopping -- the World-side facade the phone server (P) calls.

One virtual phone, one *real* virtual store, one real body: buying on the phone
charges the one property-economy wallet, gives her one owned instance, and the
World owner materializes that instance in the real ``home/bedroom`` placement.
Using it afterwards goes through the ordinary Resolver (``CONSUME``) -- there is
no second write path in this module, and no second store.

Authorities this module only *composes*, never replaces:

============================  =========================================
money + ownership             ``world_property_economy`` (one document)
physical placement + receipt  ``world_body_substrate`` (one document)
declaration / verdict         ``world_consumable_contract``
commit / idempotency          ``world_action_resolver`` + ledger
one-shot submission ticket    ``world_action_authorization`` registry
============================  =========================================

Cross-store honesty: the money+ownership write is atomic inside the economy
document, and materialization is a second, *idempotent* substrate write.  If the
process dies between them the purchase is still reported (``applied``) with
``materialization="pending"``; replaying the same ``operation_key`` re-charges
nothing and finishes the materialization.

Usage (P's server process, same uid as the World/Body service)::

    phone = PhoneShopping(
        os.environ["CHIYO_WORLD_HOME"],
        os.environ,
        local_owner_uid=os.getuid(),
        subject="chiyo",
        gateway_socket=Path(home) / "run" / "gateway.sock",
    )
    phone.shop()                                   # browse
    phone.purchase("virtual_snack_wafer", key)     # buy (idempotent)
    phone.inventory()                               # what she really owns/where
    phone.use(item_instance_id, key)                # consume via the Resolver
    phone.operation(key)                            # reconcile one key
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_SERVICE_DIR = _HERE.parent / "service"
for _path in (str(_HERE), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import world_body_substrate as substrate  # noqa: E402
import world_consumable_contract as contract  # noqa: E402
import world_execution_ledger as ledger  # noqa: E402
import world_property_economy as economy  # noqa: E402

SCHEMA_PHONE_SHOPPING = "world.phone.shopping.v1"
KIND_PHONE_SHOPPING = "world_phone_shopping"

#: The real place the purchased instance is materialized in.  ``home`` is the
#: World's own home node and ``bedroom`` its area (world_living_skeleton).
HOME_PLACE_ID = "home"
HOME_AREA_ID = "bedroom"

#: Envelope statuses (mirrors the施工单 HTTP contract).
STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"
STATUS_DISABLED = "disabled"
STATUS_NOT_FOUND = "not_found"

#: Operation statuses.
MATERIALIZED = "materialized"
ALREADY_MATERIALIZED = "already_materialized"
MATERIALIZATION_PENDING = "pending"
#: The single terminal materialization state of a consumed instance: it is not
#: "pending" (there is nothing left to place) and it is not "materialized" (its
#: placement was removed by the consumption, on purpose).  Terminal, forever.
MATERIALIZATION_CONSUMED = "consumed"
#: A consumed instance has no current position any more.
LOCATION_CONSUMED = "consumed"

#: The execution id is derived from the operation key, so a retry can never
#: become a second consumption.
EXECUTION_PREFIX = "phone-exec:"
EXECUTION_NAMESPACE = "chiyo|phone_use|"

#: Local-owner context (also stamped by the World/Body service).
LOCAL_OWNER_SOURCE = economy.LOCAL_OWNER_SOURCE
LOCAL_OWNER_VERB = "CONSUME"
LOCAL_OWNER_COMMAND = "submit_local_owner_action"

DEFAULT_READ_TIMEOUT = 2.0
#: A mutation is timed above the Resolver's own 10s commit budget.
DEFAULT_MUTATION_TIMEOUT = 15.0

MAX_OPERATION_KEY = 128


# ---------------------------------------------------------------------------
# envelope helpers
# ---------------------------------------------------------------------------


def _envelope(
    ok: bool,
    status: str,
    data: Optional[Mapping[str, Any]] = None,
    reason_code: Optional[str] = None,
) -> dict[str, Any]:
    """The one envelope shape P's HTTP layer copies verbatim."""

    result: dict[str, Any] = {
        "kind": KIND_PHONE_SHOPPING,
        "schema_version": SCHEMA_PHONE_SHOPPING,
        "ok": bool(ok),
        "status": status,
        "data": dict(data or {}),
    }
    if reason_code is not None:
        result["reason_code"] = reason_code
    return result


def _ok(status: str, data: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    return _envelope(True, status, data)


def _fail(
    status: str, reason_code: str, data: Optional[Mapping[str, Any]] = None
) -> dict[str, Any]:
    return _envelope(False, status, data, reason_code)


def execution_id_for(operation_key: Any) -> str:
    """The stable execution id of one phone operation key (no randomness)."""

    digest = hashlib.sha256(
        (EXECUTION_NAMESPACE + str(operation_key)).encode("utf-8")
    ).hexdigest()
    return EXECUTION_PREFIX + digest[:32]


def _bounded_key(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_OPERATION_KEY:
        return None
    return text


# ---------------------------------------------------------------------------
# World owner materialization
# ---------------------------------------------------------------------------


def materialize_purchase(
    *,
    hermes_home: Any,
    item_instance_id: Any,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Place one *purchased and owned* instance at ``home/bedroom``.

    This is the substrate owner's own write (``place_object`` + one atomic
    ``SubstrateStore.save``).  It never writes ``hand``: holding something is
    what the Resolver's ``PICK_UP`` produces, not what a purchase may fake.

    Idempotent: the same target is ``already_materialized``; a different
    existing placement is refused rather than overwritten.
    """

    item_id = _bounded_key(item_instance_id)
    if item_id is None:
        return {"status": STATUS_REJECTED, "reason_code": "INVALID_ITEM_INSTANCE_ID"}
    try:
        state = economy.load_state(hermes_home)
    except economy.PropertyEconomyError:
        # A broken economy is never read as "no purchase".
        return {"status": STATUS_UNAVAILABLE, "reason_code": "ECONOMY_STATE_ERROR"}
    if state is None:
        return {"status": STATUS_UNAVAILABLE, "reason_code": "GENESIS_NOT_INITIALIZED"}
    item = next(
        (
            entry
            for entry in state["items"]
            if entry["item_instance_id"] == item_id
        ),
        None,
    )
    if item is None:
        return {"status": STATUS_REJECTED, "reason_code": "NOT_AN_ECONOMY_INSTANCE"}
    if item["disposition"] != economy.DISPOSITION_OWNED:
        return {"status": STATUS_REJECTED, "reason_code": "NOT_OWNED"}
    if item["current_location"] != economy.HOME_ITEM_LOCATION:
        return {"status": STATUS_REJECTED, "reason_code": "NOT_DELIVERED"}
    voucher = contract.purchase_voucher(state, item_id)
    if not voucher["ok"]:
        return {
            "status": STATUS_REJECTED,
            "reason_code": voucher["reason_code"],
            "detail": voucher["detail"],
        }

    target = {
        "kind": "room",
        "location_id": HOME_PLACE_ID,
        "area_id": HOME_AREA_ID,
    }
    path = substrate.substrate_path(hermes_home)
    store = substrate.SubstrateStore(path)
    try:
        live = store.load()
    except substrate.BodySubstrateError:
        return {"status": STATUS_UNAVAILABLE, "reason_code": "SUBSTRATE_STATE_ERROR"}
    except OSError:
        return {"status": STATUS_UNAVAILABLE, "reason_code": "SUBSTRATE_STATE_ERROR"}
    if live is None:
        # The service owns bootstrap; a purchase never invents a body.
        return {"status": STATUS_UNAVAILABLE, "reason_code": "SUBSTRATE_NOT_INITIALIZED"}
    # A consumed instance is terminal.  Ownership stays in the economy record
    # (the exhaust truth is the substrate receipt), so "the placement is gone"
    # must NOT be read as "it still needs materializing" -- otherwise a replay
    # would resurrect it with a fresh placement.
    consumed_receipts = [
        receipt
        for receipt in (live.get("consumptions") or {}).values()
        if str(receipt.get("object_id")) == item_id
    ]
    if consumed_receipts:
        return {
            "status": STATUS_REJECTED,
            "reason_code": contract.ALREADY_CONSUMED,
            "consumed": True,
            "materialization": MATERIALIZATION_CONSUMED,
            "receipt_id": consumed_receipts[-1].get("receipt_id"),
            "substrate_revision": live["revision"],
        }
    existing = substrate.placement_of(live, item_id)
    if existing is not None:
        if existing == target:
            return {
                "status": ALREADY_MATERIALIZED,
                "placement": dict(existing),
                "substrate_revision": live["revision"],
            }
        return {
            "status": STATUS_REJECTED,
            "reason_code": "PLACEMENT_CONFLICT",
            "placement": dict(existing),
        }
    try:
        updated = substrate.place_object(live, item_id, target)
        store.save(updated, expected_revision=live["revision"])
    except substrate.BodySubstrateError as error:
        return {
            "status": STATUS_REJECTED,
            "reason_code": "SUBSTRATE_REJECTED",
            "detail": str(error)[:200],
        }
    except OSError:
        return {"status": STATUS_UNAVAILABLE, "reason_code": "SUBSTRATE_STATE_ERROR"}
    fresh = store.load() or updated
    return {
        "status": MATERIALIZED,
        "placement": dict(substrate.placement_of(fresh, item_id) or target),
        "substrate_revision": fresh["revision"],
    }


def materialize_owned_item(
    *,
    hermes_home: Any,
    item_instance_id: Any,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """**The** one substrate materialization seam for an owned instance.

    Two callers, one implementation:

    * the phone-channel purchase path (``PhoneShopping.purchase``), and
    * the owner tick (``WorldBodyService._settle_deliveries``), which delivers
      counter purchases and then puts the arrived instance where it legally
      belongs: ``home/bedroom``.

    The rules are the same either way and none of them is negotiable: the
    instance must be an economy instance that is OWNED and delivered at home,
    its purchase voucher must be derived from the real PURCHASE/DEBIT records,
    a conflicting existing placement is *refused* (never overwritten), a
    consumption terminal is *never* resurrected, and a hand is never written
    (holding is the Resolver's PICK_UP, not a delivery).  Idempotent: the same
    target is ``already_materialized``.
    """

    return materialize_purchase(
        hermes_home=hermes_home,
        item_instance_id=item_instance_id,
        environment=environment,
    )


# ---------------------------------------------------------------------------
# the facade
# ---------------------------------------------------------------------------


class PhoneShopping:
    """The phone's World-side adapter for one bound local owner.

    Everything is derived from the constructor's *server-side* arguments: the
    real uid, the bound subject and the World home.  Nothing a request body
    carries can become an identity here.
    """

    def __init__(
        self,
        hermes_home: Any,
        environment: Optional[Mapping[str, str]],
        *,
        local_owner_uid: Any,
        subject: str = "chiyo",
        gateway_socket: Any = None,
    ):
        self.environment: dict[str, str] = dict(environment or {})
        self._auth_error: Optional[str] = None
        try:
            raw_home = Path(str(hermes_home)).expanduser()
        except (TypeError, ValueError):
            raw_home = None
        if raw_home is None or not raw_home.is_absolute():
            self.hermes_home = None
            self._auth_error = "LOCAL_OWNER_HOME_MISMATCH"
        else:
            self.hermes_home = raw_home
            if not raw_home.is_dir():
                self._auth_error = "LOCAL_OWNER_HOME_MISMATCH"
        real_uid = os.getuid() if hasattr(os, "getuid") else None
        if self._auth_error is None:
            if (
                isinstance(local_owner_uid, bool)
                or not isinstance(local_owner_uid, int)
                or real_uid is None
                or local_owner_uid != real_uid
            ):
                self._auth_error = "LOCAL_OWNER_UID_MISMATCH"
        self.local_owner_uid = local_owner_uid
        if self._auth_error is None and subject != economy.OWNER:
            self._auth_error = "LOCAL_OWNER_SUBJECT_MISMATCH"
        self.subject = subject
        if gateway_socket is not None:
            self.gateway_socket = Path(str(gateway_socket)).expanduser()
        elif self.hermes_home is not None:
            self.gateway_socket = self.hermes_home / "run" / "gateway.sock"
        else:
            self.gateway_socket = None

    # -- identity ---------------------------------------------------------

    @property
    def bound(self) -> bool:
        return self._auth_error is None

    def _local_owner_context(self) -> Optional[dict[str, Any]]:
        if self._auth_error is not None or self.hermes_home is None:
            return None
        return {
            "source": LOCAL_OWNER_SOURCE,
            "uid": self.local_owner_uid,
            "subject": self.subject,
            "home": str(self.hermes_home),
        }

    def _guard(self) -> Optional[dict[str, Any]]:
        """Reject before any read/write when the binding does not hold."""

        if self._auth_error is None:
            return None
        return _fail(STATUS_REJECTED, self._auth_error)

    # -- reads ------------------------------------------------------------

    def wallet(self) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        try:
            projection = economy.wallet_projection(
                hermes_home=self.hermes_home, environment=self.environment
            )
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        status = projection.get("status")
        if status != "available":
            return _fail(
                STATUS_DISABLED if status == "disabled" else STATUS_UNAVAILABLE,
                projection.get("reason_code") or "WALLET_UNAVAILABLE",
            )
        return _ok(
            STATUS_APPLIED,
            {
                "owner": projection["owner"],
                "currency": projection["currency"],
                "balance": projection["current_balance"],
                "starting_grant": projection["starting_grant"],
                "transactions": [
                    {
                        key_: value_
                        for key_, value_ in (
                            ("transaction_id", entry["transaction_id"]),
                            ("kind", entry.get("transaction_kind")),
                            ("direction", entry["direction"]),
                            ("amount", entry["amount"]),
                            ("occurred_at", entry["occurred_at"]),
                            ("catalog_item_id", entry.get("catalog_item_id")),
                            ("purchase_intent_id", entry.get("purchase_intent_id")),
                        )
                        if value_ is not None
                    }
                    for entry in projection["recent_transactions"]
                ],
            },
        )

    def shop(self) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        try:
            projection = economy.phone_shop_projection(
                hermes_home=self.hermes_home, environment=self.environment
            )
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if projection.get("status") == "disabled":
            return _fail(STATUS_DISABLED, "FEATURE_DISABLED")
        data = {
            "store_id": projection["store_id"],
            "channel": projection["channel"],
            "store_kind": projection["store_kind"],
            "availability": projection["availability"],
            "currency": projection["currency"],
            "catalog": projection["catalog"],
            "purchase_available": projection["purchase_available"],
            "payment": projection["payment"],
            "delivery": projection["delivery"],
        }
        if projection.get("reason_code"):
            data["reason_code"] = projection["reason_code"]
        return _ok(STATUS_APPLIED, data)

    # -- the physical counter (v0.4 room-living) ---------------------------

    def store(self) -> dict[str, Any]:
        """GET /api/phone/store -- the counter shelf, read-only.

        It answers four things with one envelope: is the shop open, is she
        really at the counter, what is on the shelf and what can she afford.
        Reading never delivers, never arranges and never moves money.
        """

        blocked = self._guard()
        if blocked is not None:
            return blocked
        try:
            projection = economy.store_shop_projection(
                hermes_home=self.hermes_home, environment=self.environment
            )
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if projection.get("status") == "disabled":
            return _fail(STATUS_DISABLED, "FEATURE_DISABLED")
        data = {
            key_: projection[key_]
            for key_ in (
                "store_id",
                "channel",
                "store_kind",
                "availability",
                "location",
                "at_store",
                "currency",
                "catalog",
                "purchase_available",
                "payment",
                "delivery",
                "balance",
                "pending_delivery_count",
                "owned_furniture_count",
                "capacity_available",
                "read_is_pure",
                "paid_real_money",
            )
            if key_ in projection
        }
        if projection.get("reason_code"):
            data["reason_code"] = projection["reason_code"]
        status = projection.get("status")
        if status == "available":
            return _ok(STATUS_APPLIED, data)
        if status == "unavailable":
            return _fail(STATUS_UNAVAILABLE, projection.get("reason_code") or "UNAVAILABLE", data)
        return _fail(STATUS_REJECTED, projection.get("reason_code") or "STORE_UNAVAILABLE", data)

    def store_purchase(self, catalog_item_id: Any, operation_key: Any) -> dict[str, Any]:
        """POST /api/phone/store/purchases -- one counter sale, one key.

        The key lives in the *same* namespace as every other phone operation: a
        key already spent on a walk or on a phone-channel purchase is a
        conflict, never a second meaning.  Success is a debit + one OWNED
        instance; it is **not** delivery and **not** a placement -- the owner
        tick delivers and ``ARRANGE`` arranges.
        """

        blocked = self._guard()
        if blocked is not None:
            return blocked
        key = _bounded_key(operation_key)
        if key is None:
            return _fail(STATUS_REJECTED, "INVALID_OPERATION_KEY")
        frame_check = self._ledger_frame(execution_id_for(key))
        if frame_check["status"] == "corrupt":
            return _fail(STATUS_UNAVAILABLE, "LEDGER_CORRUPT")
        if frame_check["frame"] is not None:
            return _fail(
                STATUS_REJECTED,
                "OPERATION_KEY_CONFLICT",
                {
                    "operation": "world",
                    "execution_id": execution_id_for(key),
                    "ledger_state": frame_check["frame"].get("state"),
                    "ledger_result": frame_check["frame"].get("result"),
                },
            )
        try:
            recorded = economy.find_purchase_intent(key, hermes_home=self.hermes_home)
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if recorded is not None:
            if (
                recorded.get("channel") != economy.CHANNEL_HOME_GOODS_STORE
                or recorded.get("catalog_item_id") != str(catalog_item_id)
            ):
                return _fail(
                    STATUS_REJECTED,
                    "OPERATION_KEY_CONFLICT",
                    {
                        "operation": "purchase",
                        "channel": recorded.get("channel"),
                        "catalog_item_id": recorded.get("catalog_item_id"),
                        "transaction_id": recorded.get("transaction_id"),
                    },
                )
        context = self._local_owner_context()
        try:
            result = economy.store_purchase_item(
                catalog_item_id=catalog_item_id,
                operation_key=key,
                local_owner=context,
                hermes_home=self.hermes_home,
                environment=self.environment,
            )
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        status = result.get("status")
        if status == "disabled":
            return _fail(STATUS_DISABLED, "FEATURE_DISABLED")
        if status == "unavailable":
            return _fail(STATUS_UNAVAILABLE, result.get("reason_code") or "UNAVAILABLE")
        if status == "failed":
            return _fail(STATUS_UNAVAILABLE, result.get("reason_code") or "ECONOMY_STATE_ERROR")
        if status == "rejected":
            data = {
                key_: result[key_]
                for key_ in (
                    "current_balance",
                    "price",
                    "currency",
                    "purchase_intent_id",
                    "current_location_id",
                    "availability",
                )
                if key_ in result
            }
            return _fail(
                STATUS_REJECTED, result.get("reason_code") or "STORE_PURCHASE_REJECTED", data
            )
        data = {
            "channel": economy.CHANNEL_HOME_GOODS_STORE,
            "transaction_id": result["transaction_id"],
            "purchase_intent_id": result["purchase_intent_id"],
            "catalog_item_id": result["catalog_item_id"],
            "item_instance_id": result["item_instance_id"],
            "price": result["price"],
            "currency": result["currency"],
            "occurred_at": result["occurred_at"],
            "ownership": result["ownership"],
            "location": result["location"],
            "delivery_location": result["delivery_location"],
            "delivery_status": result["delivery_status"],
            "expected_delivery_at": result["expected_delivery_at"],
            "delivered_at": result["delivered_at"],
            "materialization": result["materialization"],
            "current_balance": result["current_balance"],
            "delivery_timeline_status": result.get("delivery_timeline_status"),
            "placement_requires": "ARRANGE",
        }
        return _ok(
            STATUS_APPLIED if status == "applied" else STATUS_ALREADY_APPLIED, data
        )

    def inventory(self) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        try:
            state = economy.load_state(self.hermes_home)
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if state is None:
            return _fail(STATUS_UNAVAILABLE, "GENESIS_NOT_INITIALIZED")
        placements, receipts, pose, here = self._live_world()
        if placements is None:
            return _fail(STATUS_UNAVAILABLE, "SUBSTRATE_STATE_ERROR")
        catalog_names = {
            entry["catalog_item_id"]: entry["name"] for entry in economy.CATALOG
        }
        facts = contract.economy_facts(self.hermes_home)
        items: list[dict[str, Any]] = []
        pending = 0
        for item in state["items"]:
            item_id = item["item_instance_id"]
            placement = placements.get(item_id)
            consumed = [r for r in receipts if r["object_id"] == item_id]
            entry: dict[str, Any] = {
                "item_instance_id": item_id,
                "name": catalog_names.get(item["catalog_item_id"], item["catalog_item_id"]),
                "catalog_item_id": item["catalog_item_id"],
                "ownership": item["disposition"],
                "location": (
                    self._placement_label(placement)
                    if placement is not None
                    else item["current_location"]
                ),
                "delivery_status": "OWNED",
                "materialization": MATERIALIZED if placement is not None else MATERIALIZATION_PENDING,
                "consumed": bool(consumed),
                "usable": False,
            }
            if consumed:
                # Terminal state: the instance was consumed, so it has no
                # position any more and nothing left to materialize.  Reporting
                # it as "pending" next to delivery_status=CONSUMED would claim
                # both "it is gone" and "it is still on its way".
                entry["delivery_status"] = "CONSUMED"
                entry["materialization"] = MATERIALIZATION_CONSUMED
                entry["location"] = LOCATION_CONSUMED
                entry["blocked_reason"] = contract.ALREADY_CONSUMED
                entry["receipt_id"] = consumed[-1]["receipt_id"]
                items.append(entry)
                continue
            if placement is None:
                pending += 1
                entry["delivery_status"] = "PENDING_MATERIALIZATION"
                entry["blocked_reason"] = "NOT_MATERIALIZED"
                items.append(entry)
                continue
            verdict = contract.consumption_verdict(
                object_id=item_id,
                placement=placement,
                declaration=contract.declaration_of(
                    item_id, self.hermes_home, economy=facts.get("economy")
                ),
                economy=facts,
                current_place=here,
                hands_usable=pose in ("standing", "seated"),
                now=None,
                environment=self.environment,
            )
            entry["usable"] = bool(verdict["allowed"])
            if verdict["allowed"]:
                entry["delivery_status"] = "DELIVERED"
                entry["consumption_source"] = verdict["source"]
                if verdict.get("purchase_voucher") is not None:
                    entry["purchase_voucher"] = dict(verdict["purchase_voucher"])
            else:
                entry["delivery_status"] = "DELIVERED"
                entry["blocked_reason"] = verdict["reason_code"]
            items.append(entry)
        return _ok(STATUS_APPLIED, {"items": items, "pending_count": pending})

    # -- purchase ---------------------------------------------------------

    def purchase(self, catalog_item_id: Any, operation_key: Any) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        key = _bounded_key(operation_key)
        if key is None:
            return _fail(STATUS_REJECTED, "INVALID_OPERATION_KEY")
        frame_check = self._ledger_frame(execution_id_for(key))
        if frame_check["status"] == "corrupt":
            return _fail(STATUS_UNAVAILABLE, "LEDGER_CORRUPT")
        if frame_check["frame"] is not None:
            # One key names one operation.  Its use execution already exists, so
            # the key may not be reused for a purchase (the execution id is
            # derived from the key; reusing it would mean two meanings).
            return _fail(
                STATUS_REJECTED,
                "OPERATION_KEY_CONFLICT",
                {
                    "operation": "use",
                    "execution_id": execution_id_for(key),
                    "ledger_state": frame_check["frame"].get("state"),
                    "ledger_result": frame_check["frame"].get("result"),
                },
            )
        context = self._local_owner_context()
        try:
            result = economy.phone_purchase_item(
                catalog_item_id=catalog_item_id,
                operation_key=key,
                local_owner=context,
                hermes_home=self.hermes_home,
                environment=self.environment,
            )
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        status = result.get("status")
        if status == "disabled":
            return _fail(STATUS_DISABLED, "FEATURE_DISABLED")
        if status == "unavailable":
            return _fail(STATUS_UNAVAILABLE, result.get("reason_code") or "UNAVAILABLE")
        if status == "failed":
            return _fail(STATUS_UNAVAILABLE, result.get("reason_code") or "ECONOMY_STATE_ERROR")
        if status == "rejected":
            return _fail(
                STATUS_REJECTED,
                result.get("reason_code") or "PURCHASE_REJECTED",
                {
                    key_: result[key_]
                    for key_ in ("current_balance", "price", "currency", "purchase_intent_id")
                    if key_ in result
                },
            )
        data = {
            "transaction_id": result["transaction_id"],
            "purchase_intent_id": result["purchase_intent_id"],
            "catalog_item_id": result["catalog_item_id"],
            "item_instance_id": result["item_instance_id"],
            "price": result["price"],
            "currency": result["currency"],
            "occurred_at": result["occurred_at"],
            "ownership": result["ownership"],
            "delivery_location": result["delivery_location"],
            "current_balance": result["current_balance"],
        }
        materialization = materialize_purchase(
            hermes_home=self.hermes_home,
            item_instance_id=result["item_instance_id"],
            environment=self.environment,
        )
        if materialization.get("status") == STATUS_REJECTED and (
            materialization.get("reason_code") == contract.ALREADY_CONSUMED
        ):
            # Replaying a purchase whose instance was already consumed returns
            # the original purchase as applied, in its *terminal* state: nothing
            # is placed, no declaration is written, no second receipt appears.
            data["materialization"] = MATERIALIZATION_CONSUMED
            data["consumed"] = True
            data["delivery_status"] = "CONSUMED"
            data["location"] = LOCATION_CONSUMED
            if materialization.get("receipt_id") is not None:
                data["receipt_id"] = materialization["receipt_id"]
            return _ok(
                STATUS_APPLIED if status == "applied" else STATUS_ALREADY_APPLIED, data
            )
        data["materialization"] = (
            MATERIALIZED
            if materialization["status"] == MATERIALIZED
            else ALREADY_MATERIALIZED
            if materialization["status"] == ALREADY_MATERIALIZED
            else MATERIALIZATION_PENDING
        )
        if materialization["status"] in (STATUS_REJECTED, STATUS_UNAVAILABLE):
            data["materialization_reason_code"] = materialization.get("reason_code")
        if materialization.get("placement") is not None:
            data["placement"] = materialization["placement"]
        return _ok(
            STATUS_APPLIED if status == "applied" else STATUS_ALREADY_APPLIED, data
        )

    # -- use (through the Resolver, never a direct write) ------------------

    def use(self, item_instance_id: Any, operation_key: Any) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        key = _bounded_key(operation_key)
        if key is None:
            return _fail(STATUS_REJECTED, "INVALID_OPERATION_KEY")
        item_id = _bounded_key(item_instance_id)
        if item_id is None:
            return _fail(STATUS_REJECTED, "INVALID_ITEM_INSTANCE_ID")
        execution_id = execution_id_for(key)

        # 1. the owner's own purchase voucher + ownership (read-only)
        try:
            state = economy.load_state(self.hermes_home)
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if state is None:
            return _fail(STATUS_UNAVAILABLE, "GENESIS_NOT_INITIALIZED")
        voucher = contract.purchase_voucher(state, item_id)
        if not voucher["ok"]:
            return _fail(
                STATUS_REJECTED,
                voucher["reason_code"],
                {"detail": voucher["detail"]},
            )

        # 2. one key names one operation: if the economy already recorded a
        #    *purchase* under this key, that key may not be reused to consume.
        colliding = next(
            (
                transaction
                for transaction in state["transactions"]
                if transaction.get("purchase_intent_id") == key
            ),
            None,
        )
        if colliding is not None:
            return _fail(
                STATUS_REJECTED,
                "OPERATION_KEY_CONFLICT",
                {
                    "operation": "purchase",
                    "transaction_id": colliding.get("transaction_id"),
                    "purchase_intent_id": key,
                },
            )

        # 3. this very operation may already have consumed *this* instance: the
        #    receipt for this execution id is the only accepted proof, and a
        #    replay of the same key+target is a success, never a second
        #    consumption.  The same key replayed with a *different* item is a
        #    payload conflict, never "already applied" for the new item.
        own_receipt = self._consumption_receipt(execution_id)
        if own_receipt is not None:
            if str(own_receipt.get("object_id")) != item_id:
                return _fail(
                    STATUS_REJECTED,
                    "OPERATION_TARGET_CONFLICT",
                    {
                        "execution_id": execution_id,
                        "object_id": own_receipt.get("object_id"),
                        "requested_object_id": item_id,
                        "receipt_id": own_receipt.get("receipt_id"),
                    },
                )
            return _ok(
                STATUS_ALREADY_APPLIED,
                {
                    "execution_id": execution_id,
                    "object_id": item_id,
                    "receipt": own_receipt,
                },
            )

        # 4. any *other* execution that consumed this instance is a refusal, and
        #    an unmaterialized instance cannot be used: owning something is not
        #    reaching it, and nothing is invented to pretend otherwise.
        placements, receipts, pose, _here = self._live_world()
        if placements is None:
            return _fail(STATUS_UNAVAILABLE, "SUBSTRATE_STATE_ERROR")
        consumed = [r for r in receipts if r["object_id"] == item_id]
        if consumed:
            return _fail(
                STATUS_REJECTED,
                contract.ALREADY_CONSUMED,
                {"receipt_id": consumed[-1]["receipt_id"]},
            )
        if item_id not in placements:
            return _fail(STATUS_REJECTED, "NOT_MATERIALIZED")

        # 5. the World's own consumability rule, evaluated from the same owner
        #    facts (read-only) so a refusal is precise: not declared, not here,
        #    wrong pose, closed place.  The Resolver still has the final say, and
        #    nothing is committed here.
        facts = contract.economy_facts(self.hermes_home)
        verdict = contract.consumption_verdict(
            object_id=item_id,
            placement=placements.get(item_id),
            declaration=contract.declaration_of(
                item_id, self.hermes_home, economy=facts.get("economy")
            ),
            economy=facts,
            current_place=_here,
            hands_usable=pose in ("standing", "seated"),
            now=None,
            environment=self.environment,
        )
        if not verdict["allowed"]:
            return _fail(
                STATUS_REJECTED,
                verdict["reason_code"],
                {"detail": verdict["detail"]},
            )

        # 6. the trusted local port.  No socket -> unavailable, never a start.
        if self.gateway_socket is None or not self.gateway_socket.exists():
            return _fail(STATUS_UNAVAILABLE, "WORLD_BODY_SERVICE_UNAVAILABLE")
        snapshot = self._gateway_call("get_snapshot", timeout=DEFAULT_READ_TIMEOUT)
        if snapshot["outcome"] != "OK" or not isinstance(snapshot.get("result"), Mapping):
            return _fail(
                STATUS_UNAVAILABLE,
                "WORLD_BODY_SERVICE_UNAVAILABLE",
                {"detail": snapshot.get("reason")},
            )
        view = snapshot["result"]
        location = view.get("location") or {}
        dependencies = {
            "world.revision": view.get("world_revision"),
            "world.location": location.get("place_id"),
            "body.pose": view.get("pose"),
        }
        if any(value is None for value in dependencies.values()):
            return _fail(STATUS_UNAVAILABLE, "WORLD_BODY_SERVICE_UNAVAILABLE")

        # 7. one-shot ticket for exactly this execution/verb/args, issued
        #    through the service's own registry (no new store, no bypass).
        issuance = self._issue_authorization(
            execution_id=execution_id,
            key=key,
            params={"object_id": item_id},
        )
        if issuance is None:
            return _fail(STATUS_UNAVAILABLE, "AUTHORIZATION_REGISTRY_UNAVAILABLE")

        response = self._gateway_call(
            LOCAL_OWNER_COMMAND,
            {
                "action": LOCAL_OWNER_VERB,
                "execution_id": execution_id,
                "params": {"object_id": item_id},
                "observed_dependencies": dependencies,
                "authorization_id": issuance["authorization_id"],
            },
            timeout=DEFAULT_MUTATION_TIMEOUT,
        )
        if response["outcome"] == "UNAVAILABLE":
            return _fail(
                STATUS_UNAVAILABLE,
                "WORLD_BODY_SERVICE_UNAVAILABLE",
                {"detail": response.get("reason")},
            )
        if response["outcome"] == "TIMEOUT":
            # The commit may or may not have happened.  Never retried here with
            # a new key: the caller reconciles with the same operation key.
            return _fail(
                STATUS_UNKNOWN,
                "SERVICE_TIMEOUT",
                {"execution_id": execution_id},
            )
        payload = response.get("result")
        if not isinstance(payload, Mapping):
            return _fail(STATUS_UNAVAILABLE, "WORLD_BODY_SERVICE_UNAVAILABLE")
        status = str(payload.get("status") or "")
        data = {
            "execution_id": payload.get("execution_id") or execution_id,
            "object_id": item_id,
            "receipt": payload.get("consumption_receipt"),
            "purchase_voucher": payload.get("purchase_voucher") or voucher.get("voucher"),
            "applied_to_body": payload.get("applied_to_body"),
            "local_owner": payload.get("local_owner"),
        }
        if status in ("applied", "already_applied"):
            return _ok(
                STATUS_APPLIED if status == "applied" else STATUS_ALREADY_APPLIED, data
            )
        if status == "uncertain":
            return _fail(STATUS_UNKNOWN, "WORLD_POSTCONDITION_FAILED", data)
        return _fail(
            STATUS_REJECTED,
            payload.get("reason_code") or "CONSUME_REJECTED",
            data,
        )

    # -- one operation key, reconciled -------------------------------------

    def operation(self, operation_key: Any) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        key = _bounded_key(operation_key)
        if key is None:
            return _fail(STATUS_REJECTED, "INVALID_OPERATION_KEY")
        try:
            purchase = economy.find_purchase_intent(key, hermes_home=self.hermes_home)
        except economy.PropertyEconomyError:
            return _fail(STATUS_UNAVAILABLE, "ECONOMY_STATE_ERROR")
        if purchase is not None:
            data = {
                "operation": "purchase",
                "transaction_id": purchase.get("transaction_id"),
                "item_instance_id": purchase.get("item_instance_id"),
                "catalog_item_id": purchase.get("catalog_item_id"),
                "current_balance": purchase.get("current_balance"),
            }
            if purchase.get("status") == "unknown":
                return _fail(
                    STATUS_UNKNOWN,
                    purchase.get("reason_code") or "PURCHASE_LEDGER_INCONSISTENT",
                    data,
                )
            # Read-only reconciliation: this is a GET, so it observes the live
            # substrate and *reports* what is still missing -- it never places
            # anything.  Materialization only ever happens on the POST path
            # (``purchase()``) under the same operation key.
            placements, receipts, _pose = self._read_placements_and_receipts()
            if placements is None:
                return _fail(STATUS_UNAVAILABLE, "SUBSTRATE_STATE_ERROR", data)
            item_id = purchase["item_instance_id"]
            placement = placements.get(item_id)
            naming = [r for r in receipts if r.get("object_id") == item_id]
            if naming:
                # Terminal: consumed.  Never "pending" (nothing left to place)
                # and no recovery hint (a POST replay must not resurrect it).
                data["materialization"] = MATERIALIZATION_CONSUMED
                data["delivery_status"] = "CONSUMED"
                data["location"] = LOCATION_CONSUMED
                data["consumed"] = True
                data["receipt"] = naming[-1]
                data["receipt_id"] = naming[-1].get("receipt_id")
                return _ok(STATUS_APPLIED, data)
            data["consumed"] = False
            data["materialization"] = (
                MATERIALIZED if placement is not None else MATERIALIZATION_PENDING
            )
            if placement is not None:
                data["placement"] = dict(placement)
            else:
                data["materialization_reason_code"] = "NOT_MATERIALIZED"
                data["recovery"] = (
                    "POST /api/phone/purchases with the same operation_key"
                )
            return _ok(STATUS_APPLIED, data)

        execution_id = execution_id_for(key)
        try:
            frame = ledger.ExecutionLedger(
                ledger.ledger_path(self.hermes_home)
            ).frame(execution_id)
        except (ledger.LedgerError, ledger.LedgerCorruptError):
            return _fail(STATUS_UNAVAILABLE, "LEDGER_CORRUPT")
        if not frame.get("known"):
            return _fail(STATUS_NOT_FOUND, "OPERATION_NOT_FOUND")
        data = {
            "operation": "use",
            "execution_id": execution_id,
            "ledger_state": frame.get("state"),
            "ledger_result": frame.get("result"),
            "action_type": frame.get("action_type"),
            "reconciliation_required": frame.get("reconciliation_required"),
        }
        receipt = self._consumption_receipt(execution_id)
        if receipt is not None:
            data["receipt"] = receipt
        if frame.get("result") == ledger.RESULT_SUCCESS:
            return _ok(STATUS_APPLIED, data)
        if frame.get("result") == ledger.RESULT_FAILURE:
            return _fail(
                STATUS_REJECTED, frame.get("reason_code") or "OPERATION_FAILED", data
            )
        return _fail(STATUS_UNKNOWN, frame.get("reason_code") or "OPERATION_UNKNOWN", data)

    def _ledger_frame(self, execution_id: str) -> dict[str, Any]:
        """``{"status": "ok"|"corrupt", "frame": Optional[dict]}`` (read-only).

        A corrupt ledger is reported as corrupt, never as "no such operation":
        "this key was never used" would be a permission-shaped claim.
        """

        try:
            frame = ledger.ExecutionLedger(
                ledger.ledger_path(self.hermes_home)
            ).frame(execution_id)
        except (ledger.LedgerError, ledger.LedgerCorruptError):
            return {"status": "corrupt", "frame": None}
        return {"status": "ok", "frame": frame if frame.get("known") else None}

    # -- plumbing ---------------------------------------------------------

    def _read_placements_and_receipts(
        self,
    ) -> tuple[Optional[dict], list[dict], str]:
        """Read-only ``(placements, receipts, pose)`` from the substrate owner.

        ``placements is None`` means the owner document could not be read at all
        (a *broken* owner file is never reported as "no objects").
        ``SubstrateStore.load`` never rewrites the file -- an older schema is
        upgraded *in memory* only -- so no GET path here can migrate or write the
        owner's disk.
        """

        try:
            store = substrate.SubstrateStore(substrate.substrate_path(self.hermes_home))
            live = store.load()
        except (substrate.BodySubstrateError, OSError):
            return None, [], ""
        if live is None:
            return {}, [], ""
        return (
            dict(live.get("placements") or {}),
            list((live.get("consumptions") or {}).values()),
            str(live.get("pose") or ""),
        )

    def _live_world(self) -> tuple[Optional[dict], list[dict], str, Optional[str]]:
        """``(placements, receipts, pose, current_place)``; never writes."""

        placements, receipts, pose = self._read_placements_and_receipts()
        if placements is None:
            return None, [], "", None
        here = None
        try:
            raw = json.loads(
                (self.hermes_home / "data" / "world" / "world_state.json").read_text(
                    encoding="utf-8"
                )
            )
            here = (raw.get("location") or {}).get("place_id")
        except (OSError, ValueError, AttributeError):
            here = None
        return placements, receipts, pose, here

    @staticmethod
    def _placement_label(placement: Optional[Mapping[str, Any]]) -> str:
        if not placement:
            return "unknown"
        kind = placement.get("kind")
        if kind == "hand":
            return "hand:%s" % placement.get("slot_id")
        if kind == "in_transit":
            return "in_transit:%s->%s" % (
                placement.get("from_location_id"),
                placement.get("to_location_id"),
            )
        area = (
            placement.get("area_id")
            or placement.get("surface_id")
            or placement.get("container_id")
        )
        return "%s:%s/%s" % (kind, placement.get("location_id"), area)

    def _consumption_receipt(self, execution_id: str) -> Optional[dict[str, Any]]:
        """The owner receipt for one execution (read-only; may be ``None``)."""

        try:
            store = substrate.SubstrateStore(substrate.substrate_path(self.hermes_home))
            live = store.load()
        except (substrate.BodySubstrateError, OSError):
            return None
        if live is None:
            return None
        return (live.get("consumptions") or {}).get(execution_id)

    def _gateway_call(
        self,
        command: str,
        argument: Any = None,
        *,
        timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> dict[str, Any]:
        """One request on the trusted local socket.  Never raises for transport."""

        request = json.dumps({"command": command, "argument": argument}) + "\n"
        sock = str(self.gateway_socket) if self.gateway_socket is not None else ""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(float(timeout))
                client.connect(sock)
                client.sendall(request.encode())
                buffer = b""
                while b"\n" not in buffer:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
        except socket.timeout:
            return {"outcome": "TIMEOUT", "reason": "socket_timeout", "result": None}
        except FileNotFoundError:
            return {"outcome": "UNAVAILABLE", "reason": "socket_missing", "result": None}
        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, OSError) as error:
            return {
                "outcome": "UNAVAILABLE",
                "reason": "socket_error:%s" % type(error).__name__,
                "result": None,
            }
        if not buffer:
            return {"outcome": "UNAVAILABLE", "reason": "empty_response", "result": None}
        try:
            payload = json.loads(buffer.decode("utf-8").strip().splitlines()[0])
        except (ValueError, IndexError):
            return {"outcome": "UNAVAILABLE", "reason": "malformed_response", "result": None}
        if not payload.get("ok"):
            return {
                "outcome": "FAILURE",
                "reason": str(payload.get("error")),
                "result": payload,
            }
        return {"outcome": "OK", "reason": None, "result": payload.get("result")}

    def _issue_authorization(
        self,
        *,
        execution_id: str,
        key: str,
        params: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Issue the one-shot ticket for this exact execution.

        The registry is the service's own file in its run directory -- the same
        store the Intent Executor uses.  The service still verifies and consumes
        it; this side only *requests* the capability, and only for the operation
        key the local owner asked for.
        """

        try:
            import world_action_authorization as waa
        except ImportError:  # pragma: no cover - flat/packaged import spelling
            return None
        if self.gateway_socket is None:
            return None
        try:
            registry = waa.AuthorizationRegistry(self.gateway_socket.parent)
            return registry.issue(
                decision_id=None,
                intent_id="local-owner-phone:%s" % key,
                intent_fingerprint="sha256:%s"
                % hashlib.sha256(key.encode("utf-8")).hexdigest()[:32],
                execution_id=execution_id,
                actor=economy.OWNER,
                verb=LOCAL_OWNER_VERB,
                params=dict(params),
                authority_generation=None,
            )
        except (OSError, ValueError):
            return None


__all__ = [
    "PhoneShopping",
    "execution_id_for",
    "materialize_purchase",
    "materialize_owned_item",
    "HOME_PLACE_ID",
    "HOME_AREA_ID",
]
