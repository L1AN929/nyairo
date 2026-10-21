#!/usr/bin/env python3
"""F: the World-owned *consumable* contract (one food = one object instance).

This is a narrow contract module, not a second store.  It answers three
questions, each from an owner that already exists:

1. **May this object instance be consumed right now?** — the live placement
   (World substrate) plus a *world-owned declaration* in
   ``state/world_object_capabilities.json``.  The declaration lives in a
   separate top-level block (``consumables``), so the M15 six-key object
   capability contract (``objects``) is not touched, not widened and not
   reinterpreted.
2. **What proves the consumption happened?** — a receipt/tombstone that the
   substrate stores *in the same atomic save* that removes the placement.  A
   missing placement is never evidence: absence is not attribution.
3. **Does a receipt belong to this execution, this object and this body?** —
   :func:`verify_receipt`.

Non-goals (deliberate, this version)

* No servings / portions / stock model: one object instance is one consumable,
  and consuming it removes that one instance.
* No economy, payment, balance or price reasoning.  An instance the property
  economy knows about is refused outright (``ECONOMY_OBJECT``) rather than
  treated as free because some price happens to read zero -- and a property
  economy document that exists but cannot be *proven* readable is refused too
  (``ECONOMY_FACTS_UNAVAILABLE``): an unreadable owner is not an absent one.
* No nutrition, hunger, satiety or health.  This module produces no Body input
  at all; the Body side (``body_consequence``) only ever records the small
  manipulation load of the operation itself.

The verdict function is read-only and total: it never writes, never raises for
caller data and always returns the same shape.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_DECLARATIONS = "world.object.capabilities.v1"
DECLARATIONS_KEY = "consumables"
#: v0.4: the catalog-level declaration block.  A purchased instance id is
#: derived at purchase time, so it cannot be declared in advance by instance
#: id; the world declares the *catalog item* instead.  Same document, same
#: owner, no second declaration store.
CATALOG_DECLARATIONS_KEY = "consumable_catalog"
MAX_DECLARATIONS = 512

SCHEMA_RECEIPT = "world.consumption.receipt.v1"
KIND_RECEIPT = "world_consumption_receipt"

#: Retention bound for the receipt block inside the substrate.  Reaching it
#: fails closed (a fresh consumption is refused) instead of pruning an old
#: receipt: pruning would silently drop replay protection.
MAX_RECEIPTS = 256

SOURCE_HELD = "held"
SOURCE_PLACE_SUPPLY = "place_supply"
#: v0.4 phone shopping: the one narrow third source -- an instance the owner
#: *bought* and whose purchase voucher is proven below.  A world-declared free
#: consumable keeps its old sources; nothing else gains this one.
SOURCE_PURCHASED = "purchased_item"
SOURCES = (SOURCE_HELD, SOURCE_PLACE_SUPPLY, SOURCE_PURCHASED)

#: Exact receipt fields.  ``placement_before`` records *what* was removed,
#: which is evidence the placement map can no longer carry afterwards.
RECEIPT_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "receipt_id",
        "execution_id",
        "object_id",
        "body_id",
        "actor_id",
        "proposal_fingerprint",
        "before_revision",
        "consumed_at",
        "source",
        "placement_before",
    }
)

#: placement kinds a single consumable may live in.
CONSUMABLE_PLACEMENT_KINDS = ("hand", "room", "surface")

# -- verdict vocabulary (never a capability claim, always a reason) ---------
ALLOWED = "ALLOWED"
NO_PLACEMENT = "NO_PLACEMENT"
PLACEMENT_KIND_NOT_CONSUMABLE = "PLACEMENT_KIND_NOT_CONSUMABLE"
NOT_HELD_OR_HERE = "NOT_HELD_OR_HERE"
NOT_DECLARED = "NOT_DECLARED"
DECLARATION_INVALID = "DECLARATION_INVALID"
NOT_CONSUMABLE = "NOT_CONSUMABLE"
NOT_FREE = "NOT_FREE"
ECONOMY_OBJECT = "ECONOMY_OBJECT"
ALREADY_CONSUMED = "ALREADY_CONSUMED"
POSE_CANNOT_CONSUME = "POSE_CANNOT_CONSUME"
LOCATION_CLOSED = "LOCATION_CLOSED"
RHYTHM_UNAVAILABLE = "RHYTHM_UNAVAILABLE"

# -- v0.4: the owned-purchase consumption permit ----------------------------
#: ``consumption_permit`` values a declaration may carry.  ``free`` is the
#: v0.3 vocabulary (``free_to_consume=true``); ``owned_purchase`` is the one
#: narrow exception opened in v0.4 and it requires a proven purchase voucher.
PERMIT_FREE = "free"
PERMIT_OWNED_PURCHASE = "owned_purchase"
PERMITS = (PERMIT_FREE, PERMIT_OWNED_PURCHASE)
#: Verdict codes for the permit path.
PURCHASE_VOUCHER_MISSING = "PURCHASE_VOUCHER_MISSING"
PURCHASE_NOT_OWNED = "PURCHASE_NOT_OWNED"
NOT_AN_ECONOMY_INSTANCE = "NOT_AN_ECONOMY_INSTANCE"

# -- property-economy facts ------------------------------------------------
#: Where the property economy keeps its single document (same home as the rest
#: of the World).  This module never creates, locks, migrates or repairs it.
ECONOMY_RELATIVE = ("state", "world_property_economy.json")
#: Bounded read: the whole economy document is parsed in memory, so it may not
#: be unboundedly large.  A larger file is treated as unreadable, not truncated.
MAX_ECONOMY_BYTES = 4 * 1024 * 1024

#: Tri-state outcome of reading the economy facts.
ECONOMY_ABSENT = "absent"
ECONOMY_PRESENT = "present"
ECONOMY_UNREADABLE = "unreadable"
#: Verdict code: the object's non-economy identity cannot be proven.
ECONOMY_FACTS_UNAVAILABLE = "ECONOMY_FACTS_UNAVAILABLE"

MAX_TEXT = 128


def _true(value: Any) -> bool:
    """Literal-true test.  Anything else (missing/None/"unknown"/0) is not."""

    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_TEXT:
        return None
    return text


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# world-owned declarations
# --------------------------------------------------------------------------


def _capabilities_path(hermes_home: Optional[Any] = None):
    import world_body_substrate as substrate

    return substrate.capabilities_path(hermes_home), substrate.CAPABILITIES_SCHEMA


def declarations_document(hermes_home: Optional[Any] = None) -> Optional[dict[str, Any]]:
    """The raw declaration document, or ``None`` when it is absent/unusable.

    Read-only and never repaired: a broken file means *no declaration*, which
    fails closed at the verdict.
    """

    try:
        path, schema = _capabilities_path(hermes_home)
    except Exception:
        return None
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping) or raw.get("schema_version") != schema:
        return None
    return dict(raw)


def load_consumable_declarations(
    hermes_home: Optional[Any] = None,
) -> dict[str, Any]:
    """The ``consumables`` block.  Lenient parse, bounded, no invention.

    Only the ``consumables`` key is read.  The M15 ``objects`` block (the
    six-key takeable/portable/placeable/fixed/hand_requirement/weight_class
    contract) is never consulted here and never extended by this module.
    """

    document = declarations_document(hermes_home)
    if document is None:
        return {}
    block = document.get(DECLARATIONS_KEY)
    if not isinstance(block, Mapping):
        return {}
    out: dict[str, Any] = {}
    for name in sorted(block)[:MAX_DECLARATIONS]:
        key = _text(name)
        if key is None:
            continue
        out[key] = block[name]
    return out


def load_catalog_declarations(hermes_home: Optional[Any] = None) -> dict[str, Any]:
    """The ``consumable_catalog`` block (declarations keyed by catalog item).

    Same document, same world owner as ``consumables``.  Lenient and bounded,
    never invented: a missing/broken block simply declares nothing.
    """

    document = declarations_document(hermes_home)
    if document is None:
        return {}
    block = document.get(CATALOG_DECLARATIONS_KEY)
    if not isinstance(block, Mapping):
        return {}
    out: dict[str, Any] = {}
    for name in sorted(block)[:MAX_DECLARATIONS]:
        key = _text(name)
        if key is None:
            continue
        out[key] = block[name]
    return out


def declaration_of(
    object_id: Any,
    hermes_home: Optional[Any] = None,
    *,
    economy: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """One object's consumable declaration (``{}`` when it declares nothing).

    Resolution order, and only this order:

    1. an instance-id declaration in ``consumables`` (v0.3 behaviour, unchanged);
    2. v0.4: for a *provably present* economy instance, the catalog-level
       declaration of the catalog item that instance belongs to.  An instance id
       minted at purchase time cannot be declared in advance, so the world
       declares the catalog item instead.  No economy facts, no lookup 2.
    """

    name = _text(object_id)
    if name is None:
        return {}
    found = load_consumable_declarations(hermes_home).get(name)
    if isinstance(found, Mapping):
        return dict(found)
    if economy is None:
        return {}
    catalog_item_id = economy_instances(economy).get(name)
    if not catalog_item_id:
        return {}
    catalog_found = load_catalog_declarations(hermes_home).get(catalog_item_id)
    return dict(catalog_found) if isinstance(catalog_found, Mapping) else {}


def economy_instances(economy: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """``{item_instance_id: catalog_item_id}`` for every economy instance.

    Used only to *refuse*: this version has no payment/ownership-consumption
    authority, so an economy instance is never consumable, whatever its price
    reads.
    """

    out: dict[str, str] = {}
    if not isinstance(economy, Mapping):
        return out
    for key in ("items", "item_instances", "owned_items"):
        value = economy.get(key)
        entries = []
        if isinstance(value, list):
            entries = value
        elif isinstance(value, Mapping):
            entries = [dict(entry, item_instance_id=name) if isinstance(entry, Mapping)
                       else {"item_instance_id": name}
                       for name, entry in value.items()]
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            name = _text(entry.get("item_instance_id"))
            if name is None:
                continue
            out[name] = str(entry.get("catalog_item_id") or "")
    return out


def is_economy_instance(economy: Optional[Mapping[str, Any]], object_id: Any) -> bool:
    """Is this object id a property-economy item instance (per given facts)?"""

    name = _text(object_id)
    return bool(name and name in economy_instances(economy))


def purchase_voucher(
    economy: Optional[Mapping[str, Any]], object_id: Any
) -> dict[str, Any]:
    """The owner's purchase evidence for one economy instance, or a refusal.

    This is the *only* thing that can turn an economy instance into consumable
    food, and it is derived, never supplied: the instance must be an economy
    item whose ``acquisition_kind`` is ``PURCHASE``, whose ``disposition`` is
    ``OWNED``, and which is named by a matching ``PURCHASE`` / ``DEBIT``
    transaction (same ``purchase_intent_id``, ``catalog_item_id`` and
    ``transaction_id`` as the item's own acquisition provenance).

    The economy record is *not* mutated by a consumption: ownership stays as it
    was and the "it is gone" truth lives in the substrate receipt.
    """

    name = _text(object_id)
    if name is None:
        return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                "detail": "object_id must be bounded text"}
    if not isinstance(economy, Mapping):
        return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                "detail": "no readable property-economy facts"}
    items = economy.get("items")
    if not isinstance(items, list):
        return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                "detail": "economy declares no item instances"}
    item = None
    for entry in items:
        if isinstance(entry, Mapping) and entry.get("item_instance_id") == name:
            item = entry
            break
    if item is None:
        return {"ok": False, "reason_code": NOT_AN_ECONOMY_INSTANCE,
                "detail": "%s is not a property-economy instance" % name}
    if item.get("acquisition_kind") != "PURCHASE":
        return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                "detail": "item was not acquired by purchase"}
    if item.get("disposition") != "OWNED":
        return {"ok": False, "reason_code": PURCHASE_NOT_OWNED,
                "detail": "item disposition is %r, not OWNED"
                % (item.get("disposition"),)}
    provenance = item.get("acquisition_provenance")
    if not isinstance(provenance, Mapping):
        return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                "detail": "item carries no acquisition provenance"}
    transaction_id = provenance.get("transaction_id")
    intent_id = provenance.get("purchase_intent_id")
    transactions = economy.get("transactions")
    if not isinstance(transactions, list):
        return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                "detail": "economy declares no transactions"}
    for transaction in transactions:
        if not isinstance(transaction, Mapping):
            continue
        if transaction.get("transaction_id") != transaction_id:
            continue
        if (
            transaction.get("transaction_kind") != "PURCHASE"
            or transaction.get("direction") != "DEBIT"
        ):
            return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                    "detail": "the named transaction is not a purchase debit"}
        if (
            transaction.get("purchase_intent_id") != intent_id
            or transaction.get("catalog_item_id") != item.get("catalog_item_id")
        ):
            return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
                    "detail": "purchase transaction does not match the item"}
        return {
            "ok": True,
            "reason_code": None,
            "detail": None,
            "voucher": {
                "item_instance_id": name,
                "catalog_item_id": item.get("catalog_item_id"),
                "transaction_id": transaction_id,
                "purchase_intent_id": intent_id,
                "amount": transaction.get("amount"),
                "occurred_at": transaction.get("occurred_at"),
                "source": (provenance.get("source")
                           if isinstance(provenance, Mapping) else None),
                "disposition": item.get("disposition"),
            },
        }
    return {"ok": False, "reason_code": PURCHASE_VOUCHER_MISSING,
            "detail": "no purchase transaction names this item"}

def economy_path(hermes_home: Optional[Any] = None) -> Path:
    """The property-economy document this home owns (never created here)."""

    home = (
        Path(hermes_home).expanduser()
        if hermes_home is not None
        else Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    )
    return home.joinpath(*ECONOMY_RELATIVE)


def _facts(
    status: str,
    *,
    economy: Optional[Mapping[str, Any]] = None,
    detail: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "economy": dict(economy) if economy is not None else None,
        "reason_code": None if status != ECONOMY_UNREADABLE else ECONOMY_FACTS_UNAVAILABLE,
        "detail": detail,
    }


def economy_facts(hermes_home: Optional[Any] = None) -> dict[str, Any]:
    """Can *property-economy facts* be proven for this home?

    Three outcomes, and the difference between them is the whole point:

    ``absent``
        The document does not exist: this world has no property-economy owner,
        so "this instance is not an economy object" is provable and a
        world-declared free/held consumable may proceed (unchanged behaviour).
    ``present``
        The document exists and the *economy owner's own* validator
        (``world_property_economy.validate_state``) accepts it.
    ``unreadable``
        The path exists but cannot be probed, read, parsed or validated:
        permission/OS error, non-regular file, over the read bound, non-UTF-8,
        corrupt JSON, a non-object root, or a structure the economy schema
        rejects.  Nothing can be proven, so a consumption is refused -- the
        document is never replaced by ``{}``/``None`` and never "repaired".

    Read-only and bounded: one ``stat`` plus a capped binary read; no lock, no
    migration, no creation, no write.  The strict economy schema is not
    re-implemented here -- it is imported and reused, so there is one truth.
    """

    path = economy_path(hermes_home)
    try:
        info = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return _facts(ECONOMY_ABSENT)
    except OSError as error:
        return _facts(
            ECONOMY_UNREADABLE,
            detail="economy state cannot be probed (%s)" % type(error).__name__,
        )

    if not stat.S_ISREG(info.st_mode):
        return _facts(ECONOMY_UNREADABLE, detail="economy state is not a regular file")
    if info.st_size > MAX_ECONOMY_BYTES:
        return _facts(
            ECONOMY_UNREADABLE,
            detail="economy state is larger than the %d byte read bound"
            % MAX_ECONOMY_BYTES,
        )

    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_ECONOMY_BYTES + 1)
    except OSError as error:
        return _facts(
            ECONOMY_UNREADABLE,
            detail="economy state cannot be read (%s)" % type(error).__name__,
        )
    if len(raw) > MAX_ECONOMY_BYTES:
        return _facts(
            ECONOMY_UNREADABLE,
            detail="economy state grew past the read bound while reading",
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _facts(ECONOMY_UNREADABLE, detail="economy state is not UTF-8")
    try:
        parsed = json.loads(text)
    except ValueError:
        return _facts(ECONOMY_UNREADABLE, detail="economy state is corrupt JSON")
    if not isinstance(parsed, Mapping):
        return _facts(
            ECONOMY_UNREADABLE,
            detail="economy state root is %s, not an object" % type(parsed).__name__,
        )

    try:
        import world_property_economy as economy_module

        validated = economy_module.validate_state(parsed)
    except Exception as error:  # import failure or schema rejection: both unprovable
        return _facts(
            ECONOMY_UNREADABLE,
            detail="economy state was rejected (%s): %s"
            % (type(error).__name__, str(error)[:120]),
        )
    return _facts(ECONOMY_PRESENT, economy=validated)


def economy_view(value: Any) -> dict[str, Any]:
    """Normalize an ``economy`` argument into the tri-state facts shape.

    Accepts the facts dict from :func:`economy_facts`, a plain validated
    economy mapping (``present``) or ``None`` (``absent``).  Anything else is
    *unreadable*, never "absent": the default must not invent a healthy answer.
    """

    if isinstance(value, Mapping) and value.get("status") in (
        ECONOMY_ABSENT,
        ECONOMY_PRESENT,
        ECONOMY_UNREADABLE,
    ):
        return dict(value)
    if value is None:
        return _facts(ECONOMY_ABSENT)
    if isinstance(value, Mapping):
        return _facts(ECONOMY_PRESENT, economy=value)
    return _facts(
        ECONOMY_UNREADABLE, detail="economy facts are not an object"
    )



# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------


def _denied(code: str, detail: str, **extra: Any) -> dict[str, Any]:
    verdict = {"allowed": False, "reason_code": code, "detail": detail, "source": None}
    verdict.update(extra)
    return verdict


def consumption_verdict(
    *,
    object_id: Any,
    placement: Optional[Mapping[str, Any]],
    declaration: Optional[Mapping[str, Any]],
    economy: Optional[Mapping[str, Any]],
    current_place: Optional[str],
    hands_usable: bool,
    now: Optional[datetime] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """May this instance be consumed right now?  Facts in, verdict out.

    Order of refusal is the contract's, not an accident:

    1. the live placement must exist (absence is never assumed to be success);
    2. a single held/room/surface instance, held *or* in the current place;
    3. the property-economy facts must be *provable*: an unreadable economy
       document means "this is not an economy instance" cannot be proven, so
       the consumption is refused (see :func:`economy_facts`);
    4. the world-owned declaration must say consumable **and** free;
    5. the instance must not be an economy object;
    6. the body's pose must allow manipulation;
    7. for place-supplied (not held) food, the place must be OPEN according to
       ``world_daily_rhythm`` *at this moment*.  Held food never depends on a
       shop: a store closing does not poison what is already in hand.
    """

    name = _text(object_id)
    if name is None:
        return _denied(PLACEMENT_KIND_NOT_CONSUMABLE, "object_id must be bounded text")

    if not isinstance(placement, Mapping) or not placement:
        return _denied(NO_PLACEMENT, "%s has no live placement" % name)

    kind = placement.get("kind")
    if kind not in CONSUMABLE_PLACEMENT_KINDS:
        return _denied(
            PLACEMENT_KIND_NOT_CONSUMABLE,
            "placement kind %r cannot be consumed by one operation" % (kind,),
        )

    held = kind == "hand"
    if not held and str(placement.get("location_id")) != str(current_place):
        return _denied(
            NOT_HELD_OR_HERE,
            "%s is at %r, not here (%r)" % (
                name, placement.get("location_id"), current_place,
            ),
        )

    # Economy facts before any declaration: "not an economy instance" is a
    # permission claim, and it may only be made when the owner's document is
    # genuinely readable.  A broken document is not an absent one.
    facts = economy_view(economy)
    if facts["status"] == ECONOMY_UNREADABLE:
        return _denied(
            ECONOMY_FACTS_UNAVAILABLE,
            "property-economy facts are unreadable (%s); a non-economy identity "
            "cannot be proven, so this consumption is refused"
            % (facts.get("detail") or "unknown"),
        )

    if declaration is None:
        declaration = {}
    if not isinstance(declaration, Mapping):
        return _denied(DECLARATION_INVALID, "declaration for %s is not an object" % name)
    if not declaration:
        return _denied(
            NOT_DECLARED,
            "the world declares no consumable capability for %s" % name,
        )
    if not _true(declaration.get("consumable")):
        return _denied(
            NOT_CONSUMABLE,
            "consumable=%r for %s" % (declaration.get("consumable"), name),
        )
    # -- v0.4: which permit does this declaration actually carry? -----------
    # ``free`` is the v0.3 rule (free_to_consume=true).  ``owned_purchase`` is
    # the one narrow exception v0.4 opens, and it is not a claim the caller can
    # make: it needs the owner's own purchase voucher, proven below.  Anything
    # else is NOT_FREE, exactly as before.
    permit = _text(declaration.get("consumption_permit"))
    voucher: Optional[dict[str, Any]] = None
    if _true(declaration.get("free_to_consume")):
        if permit is not None and permit != PERMIT_FREE:
            return _denied(
                NOT_FREE,
                "free_to_consume=true conflicts with consumption_permit=%r for %s"
                % (permit, name),
            )
        permit = PERMIT_FREE
    elif permit == PERMIT_OWNED_PURCHASE:
        if facts["status"] != ECONOMY_PRESENT:
            return _denied(
                PURCHASE_VOUCHER_MISSING,
                "%s declares an owned-purchase permit but no readable property-"
                "economy facts are present" % name,
            )
        proof = purchase_voucher(facts["economy"], name)
        if not proof["ok"]:
            return _denied(proof["reason_code"], proof["detail"])
        voucher = dict(proof["voucher"])
    else:
        return _denied(
            NOT_FREE,
            "free_to_consume=%r for %s" % (declaration.get("free_to_consume"), name),
        )

    # Only a *validated, present* economy can name an instance, and an economy
    # instance stays refused unless the owned-purchase permit proved a voucher
    # above.  ``absent`` keeps the pre-existing behaviour (no economy owner ->
    # no economy objects).
    if (
        facts["status"] == ECONOMY_PRESENT
        and is_economy_instance(facts["economy"], name)
        and voucher is None
    ):
        return _denied(
            ECONOMY_OBJECT,
            "%s is a property-economy instance; this version has no payment or "
            "ownership-consumption authority and never reads a price as free" % name,
        )

    if not hands_usable:
        return _denied(
            POSE_CANNOT_CONSUME,
            "the body's current pose cannot bring one portion to the mouth",
        )

    source = SOURCE_HELD if held else SOURCE_PLACE_SUPPLY
    if voucher is not None:
        # A bought instance is never *place supply*: the receipt names the
        # purchase, so a later reader can tell "the shop gave it" from "she
        # bought it and it is hers".
        source = SOURCE_PURCHASED
    if not held and voucher is None:
        open_state = place_open_state(current_place, now, environment)
        if open_state["status"] != "OPEN":
            return _denied(
                LOCATION_CLOSED,
                "%s supplies food but is %s (%s)"
                % (current_place, open_state["status"], open_state["reason_code"]),
                open_state=open_state,
            )

    allowed = {
        "allowed": True,
        "reason_code": ALLOWED,
        "detail": "ok",
        "source": source,
        "declaration": dict(declaration),
    }
    if voucher is not None:
        # The proven voucher travels with the verdict so the receipt and any
        # reconciler can cite the purchase instead of re-deriving it.
        allowed["purchase_voucher"] = voucher
    return allowed


def place_open_state(
    place_id: Optional[str],
    now: Optional[datetime],
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """``world_daily_rhythm``'s own availability for one place.

    Unknown place / unavailable rhythm source fails closed as
    ``RHYTHM_UNAVAILABLE`` — never as OPEN.
    """

    try:
        import world_daily_rhythm as rhythm

        state = rhythm.location_availability(
            str(place_id), now, environment=environment
        )
    except Exception as error:  # unknown place, unreadable world package, ...
        return {
            "location_id": place_id,
            "status": "UNKNOWN",
            "reason_code": RHYTHM_UNAVAILABLE,
            "detail": str(error)[:200],
        }
    return {
        "location_id": state.get("location_id"),
        "status": state.get("status"),
        "reason_code": state.get("reason_code"),
        "detail": None,
    }


# --------------------------------------------------------------------------
# receipts
# --------------------------------------------------------------------------


def proposal_fingerprint(object_id: Any, action: str = "CONSUME") -> str:
    """The payload identity of one consumption proposal (action + object)."""

    return "sha256:" + _digest({"action": str(action), "object_id": str(object_id)})


def build_receipt(
    *,
    execution_id: Any,
    object_id: Any,
    body_id: Any,
    actor_id: Any,
    before_revision: int,
    consumed_at: str,
    source: str,
    placement_before: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Deterministic receipt for one committed consumption."""

    fingerprint = proposal_fingerprint(object_id)
    receipt_id = "world-consumption:" + _digest(
        {"execution_id": str(execution_id), "object_id": str(object_id),
         "body_id": str(body_id), "proposal_fingerprint": fingerprint}
    )[:32]
    return {
        "kind": KIND_RECEIPT,
        "schema_version": SCHEMA_RECEIPT,
        "receipt_id": receipt_id,
        "execution_id": str(execution_id),
        "object_id": str(object_id),
        "body_id": str(body_id),
        "actor_id": str(actor_id),
        "proposal_fingerprint": fingerprint,
        "before_revision": int(before_revision),
        "consumed_at": str(consumed_at),
        "source": str(source),
        "placement_before": dict(placement_before) if placement_before else None,
    }


def verify_receipt(
    receipt: Any,
    *,
    execution_id: Any,
    object_id: Optional[Any] = None,
    body_id: Optional[Any] = None,
) -> dict[str, Any]:
    """Does this owner receipt belong to *this* execution/object/body?

    Returns ``{"ok": bool, "reasons": [...]}``.  It never raises: a malformed
    receipt is a failed match, which is exactly what fail-closed means here.
    """

    reasons: list[str] = []
    if not isinstance(receipt, Mapping):
        return {"ok": False, "reasons": ["receipt_missing"]}
    if set(receipt) != RECEIPT_KEYS:
        reasons.append("receipt_fields")
    if receipt.get("kind") != KIND_RECEIPT:
        reasons.append("receipt_kind")
    if receipt.get("schema_version") != SCHEMA_RECEIPT:
        reasons.append("receipt_schema")
    if str(receipt.get("execution_id")) != str(execution_id):
        reasons.append("execution_mismatch")
    if object_id is not None and str(receipt.get("object_id")) != str(object_id):
        reasons.append("object_mismatch")
    if body_id is not None and str(receipt.get("body_id")) != str(body_id):
        reasons.append("body_mismatch")
    if receipt.get("proposal_fingerprint") != proposal_fingerprint(receipt.get("object_id")):
        reasons.append("payload_mismatch")
    if receipt.get("source") not in SOURCES:
        reasons.append("receipt_source")
    return {"ok": not reasons, "reasons": reasons}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
__all__ = [
    "ALLOWED",
    "ALREADY_CONSUMED",
    "CONSUMABLE_PLACEMENT_KINDS",
    "DECLARATIONS_KEY",
    "DECLARATION_INVALID",
    "ECONOMY_ABSENT",
    "ECONOMY_FACTS_UNAVAILABLE",
    "ECONOMY_OBJECT",
    "ECONOMY_PRESENT",
    "ECONOMY_RELATIVE",
    "ECONOMY_UNREADABLE",
    "KIND_RECEIPT",
    "LOCATION_CLOSED",
    "MAX_ECONOMY_BYTES",
    "MAX_RECEIPTS",
    "NOT_CONSUMABLE",
    "NOT_DECLARED",
    "NOT_FREE",
    "NOT_HELD_OR_HERE",
    "NO_PLACEMENT",
    "PLACEMENT_KIND_NOT_CONSUMABLE",
    "POSE_CANNOT_CONSUME",
    "RECEIPT_KEYS",
    "RHYTHM_UNAVAILABLE",
    "SCHEMA_DECLARATIONS",
    "SCHEMA_RECEIPT",
    "SOURCES",
    "SOURCE_HELD",
    "SOURCE_PLACE_SUPPLY",
    "SOURCE_PURCHASED",
    "CATALOG_DECLARATIONS_KEY",
    "NOT_AN_ECONOMY_INSTANCE",
    "PERMIT_FREE",
    "PERMIT_OWNED_PURCHASE",
    "PERMITS",
    "PURCHASE_NOT_OWNED",
    "PURCHASE_VOUCHER_MISSING",
    "build_receipt",
    "consumption_verdict",
    "declaration_of",
    "declarations_document",
    "economy_facts",
    "economy_instances",
    "economy_path",
    "economy_view",
    "is_economy_instance",
    "load_catalog_declarations",
    "load_consumable_declarations",
    "now_iso",
    "place_open_state",
    "proposal_fingerprint",
    "purchase_voucher",
    "verify_receipt",
]
