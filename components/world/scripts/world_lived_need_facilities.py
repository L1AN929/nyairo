#!/usr/bin/env python3
"""World-owned *lived-need facility* declarations (toilet use).

Same capabilities document as the M15 object contract and the CONSUME
declarations, a separate top-level block so nothing already frozen is widened:

    state/world_object_capabilities.json
    {
      "schema_version": "world.object.capabilities.v1",
      "objects": {...},                 # M15, untouched
      "consumables": {...},             # F/CONSUME, untouched
      "lived_need_facilities": {        # this module
        "home-toilet-01": {
          "facility_kind": "toilet",
          "location_id": "home",
          "lived_need_effects": {"toilet": 0.9}
        }
      }
    }

A facility is a *World declaration with a real location*, not a name guess:
the declaration must name the current place, the place must be OPEN by the
World's own rhythm, and the effect must be explicit.  No inventory, no
consumption, no privacy text, no human physiological claim is produced here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Optional

FACILITIES_KEY = "lived_need_facilities"
FACILITY_KINDS = ("toilet",)
MAX_DECLARATIONS = 64
MAX_TEXT = 128

ALLOWED = "ALLOWED"
NOT_DECLARED = "NOT_DECLARED"
DECLARATION_INVALID = "DECLARATION_INVALID"
FACILITY_KIND_UNSUPPORTED = "FACILITY_KIND_UNSUPPORTED"
NOT_HERE = "NOT_HERE"
LOCATION_CLOSED = "LOCATION_CLOSED"
RHYTHM_UNAVAILABLE = "RHYTHM_UNAVAILABLE"
EFFECT_MISSING = "EFFECT_MISSING"


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_TEXT:
        return None
    return text


def load_declarations(hermes_home: Optional[Any] = None) -> dict[str, Any]:
    """The ``lived_need_facilities`` block, lenient and bounded.

    A missing/broken document declares nothing (fail closed); nothing is
    repaired or created.
    """

    import world_body_substrate as substrate

    try:
        path = substrate.capabilities_path(hermes_home)
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - unreadable declarations are no declarations
        return {}
    if not isinstance(raw, Mapping) or raw.get("schema_version") != substrate.CAPABILITIES_SCHEMA:
        return {}
    block = raw.get(FACILITIES_KEY)
    if not isinstance(block, Mapping):
        return {}
    out: dict[str, Any] = {}
    for name in sorted(block)[:MAX_DECLARATIONS]:
        key = _text(name)
        if key is not None:
            out[key] = block[name]
    return out


def declaration_of(facility_id: Any, hermes_home: Optional[Any] = None) -> dict[str, Any]:
    name = _text(facility_id)
    if name is None:
        return {}
    found = load_declarations(hermes_home).get(name)
    return dict(found) if isinstance(found, Mapping) else {}


def _need_effects(declaration: Mapping[str, Any]) -> dict[str, float]:
    import body_lived_needs as bln

    effects = bln.declaration_effects(declaration)
    return {name: amount for name, amount in effects.items() if amount > 0.0}


def facility_verdict(
    *,
    facility_id: Any,
    declaration: Optional[Mapping[str, Any]],
    current_place: Optional[str],
    now: Any = None,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """May this declared facility be used right now?  Facts in, verdict out."""

    name = _text(facility_id)
    if name is None:
        return {"allowed": False, "reason_code": DECLARATION_INVALID,
                "detail": "facility_id must be bounded text", "effects": {}}
    if not isinstance(declaration, Mapping) or not declaration:
        return {"allowed": False, "reason_code": NOT_DECLARED,
                "detail": "the World declares no lived-need facility %s" % name,
                "effects": {}}
    kind = _text(declaration.get("facility_kind"))
    if kind not in FACILITY_KINDS:
        return {"allowed": False, "reason_code": FACILITY_KIND_UNSUPPORTED,
                "detail": "facility_kind %r is not supported" % (kind,), "effects": {}}
    location = _text(declaration.get("location_id"))
    if not location or str(current_place or "") != location:
        return {"allowed": False, "reason_code": NOT_HERE,
                "detail": "%s is declared at %r, not here (%r)"
                          % (name, location, current_place), "effects": {}}
    effects = _need_effects(declaration)
    if not effects:
        return {"allowed": False, "reason_code": EFFECT_MISSING,
                "detail": "%s declares no explicit lived_need_effects" % name,
                "effects": {}}
    try:
        import world_consumable_contract as contract

        open_state = contract.place_open_state(current_place, now, environment)
    except Exception as error:  # noqa: BLE001
        return {"allowed": False, "reason_code": RHYTHM_UNAVAILABLE,
                "detail": str(error)[:200], "effects": {}}
    if open_state.get("status") != "OPEN":
        return {"allowed": False, "reason_code": LOCATION_CLOSED,
                "detail": "%s is %s (%s)" % (current_place, open_state.get("status"),
                                              open_state.get("reason_code")),
                "effects": {}, "open_state": open_state}
    return {"allowed": True, "reason_code": ALLOWED, "detail": "ok",
            "effects": effects, "open_state": open_state}


__all__ = [
    "ALLOWED",
    "DECLARATION_INVALID",
    "EFFECT_MISSING",
    "FACILITIES_KEY",
    "FACILITY_KINDS",
    "FACILITY_KIND_UNSUPPORTED",
    "LOCATION_CLOSED",
    "NOT_DECLARED",
    "NOT_HERE",
    "RHYTHM_UNAVAILABLE",
    "declaration_of",
    "facility_verdict",
    "load_declarations",
]
