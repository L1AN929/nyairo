#!/usr/bin/env python3
"""Room environment v1: declared light sources, derived light, Body input.

One job: turn a *declared* fixture plus its *recorded* power into one honest
room-light fact, and (only then) into the one legal Body ENVIRONMENT input.

Ownership
---------
World owns the fixture declaration and the power state.  The declaration lives
in this module (a versioned table keyed by catalog item), the state lives in the
substrate's ``fixtures`` block (``world.body.substrate.v1.3``).  There is no
second world store, no ambient clock guess and no per-room copy.

Hard rules (they are the reason this module is small)
----------------------------------------------------
* **No declaration means no light source.**  A lamp *label* never becomes a
  light: only ``catalog:<item>`` declared fixtures count.
* **Absent power means UNKNOWN, never "off".**  ``unknown`` is reported as
  ``unknown`` with a reason code; it is never folded into ``0`` intensity and
  never rendered as "dark".
* **Nothing is derived from the clock.**  No daylight model, no sunrise, no
  temperature, no noise: v1 only knows "a declared lamp in this area is on".
* **Area-scoped.**  A lamp in the bedroom is not light in the living room: the
  fixture's own placement area is compared with the requested area.
* **The band table is one versioned map** (``environment.light.v1``).  The Body's
  own mathematics, thresholds and signal bands are untouched; a ``dim`` lamp that
  does not reach the existing ``sensory_load_elevated`` band is a legal result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import world_body_substrate as substrate

SCHEMA = "world.room.environment.v1"
KIND = "world_room_environment"
FEATURE_ENV = "CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"

#: Status vocabulary of a derived light fact.
STATUS_AVAILABLE = "available"
STATUS_UNKNOWN = "unknown"

POWER_ON = "on"
POWER_OFF = "off"

#: Reason codes -- every ``unknown`` says *why*, so a reader never has to guess.
REASON_NO_DECLARED_LIGHT = "NO_DECLARED_LIGHT_IN_AREA"
REASON_POWER_UNKNOWN = "FIXTURE_POWER_UNKNOWN"
REASON_NO_PLACEMENT = "NO_DECLARED_FIXTURE_PLACED_IN_AREA"
REASON_SUBSTRATE_UNAVAILABLE = "SUBSTRATE_UNAVAILABLE"

#: **The** versioned light model.  One table, one meaning, no per-call tuning.
#: These are the *only* numbers this feature hands the Body; they are inputs to
#: the existing ``sensory_load`` rule, not a new scale and not a new signal.
LIGHT_BAND_INTENSITY: dict[str, float] = {"dim": 0.20, "moderate": 0.45, "bright": 0.70}
ENVIRONMENT_MODEL = "environment.light.v1"

#: Declared light fixtures, keyed by the catalog item that really declares them.
#: ``powerable`` is the only thing that allows a TOGGLE; ``light_band`` is what the
#: fixture emits when it is on (a fixed property of the fixture, not of the
#: moment).  Anything not listed here is **not a light**.
FIXTURE_DECLARATIONS: dict[str, dict[str, Any]] = {
    "bedside_lamp": {
        "catalog_item_id": "bedside_lamp",
        "powerable": True,
        "light_band": "dim",
        "declared_source": "catalog:bedside_lamp",
        "label": "床头灯",
    },
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def feature_enabled(environment: Optional[Mapping[str, str]] = None) -> bool:
    """The opt-in gate.  Default deny: an existing world is never switched on
    by a read; the operator/demo turns it on explicitly."""

    return _truthy((environment or {}).get(FEATURE_ENV))


def declaration_of_catalog(catalog_item_id: Any) -> Optional[dict[str, Any]]:
    """The declared light property of a catalog item (``None`` = not a light)."""

    if not isinstance(catalog_item_id, str):
        return None
    entry = FIXTURE_DECLARATIONS.get(catalog_item_id)
    return dict(entry) if entry else None


def declaration_of_object(
    object_id: Any, catalog_by_instance: Mapping[str, str]
) -> Optional[dict[str, Any]]:
    """The declared light property of a placed *instance* (``None`` when none)."""

    catalog_item = catalog_by_instance.get(str(object_id))
    return declaration_of_catalog(catalog_item) if catalog_item else None


def _instant(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def _now(now: Any = None) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    if now is not None:
        parsed = _instant(now)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _unknown(reason_code: str, **extra: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "status": STATUS_UNKNOWN,
        "power": None,
        "band": None,
        "band_source": ENVIRONMENT_MODEL,
        "source": KIND,
        "declared_source": None,
        "since": None,
        "on_seconds": None,
        "fixture_ids": [],
        "reason_code": reason_code,
        "light_source_is_declaration": True,
        "temperature_or_noise_inferred": False,
    }
    block.update(extra)
    return block


def derive_light(
    *,
    placements: Mapping[str, Any],
    fixtures: Mapping[str, Any],
    catalog_by_instance: Mapping[str, str],
    location_id: Optional[str],
    area_id: Optional[str] = None,
    now: Any = None,
) -> dict[str, Any]:
    """The authoritative room-light fact for one place/area.

    ``available`` means *this area's declared fixture power is known* (on or off).
    ``unknown`` means it is not -- with a reason code, and never as intensity 0.
    """

    if not location_id:
        return _unknown(REASON_NO_PLACEMENT, place_id=None, area_id=area_id)

    present: list[tuple[str, dict[str, Any]]] = []
    for object_id in sorted(placements):
        target = placements[object_id] or {}
        declaration = declaration_of_object(object_id, catalog_by_instance)
        if declaration is None:
            continue
        if str(target.get("location_id")) != str(location_id):
            continue
        placement_area = target.get("area_id")
        if area_id is not None and placement_area is not None:
            if str(placement_area) != str(area_id):
                continue
        present.append((str(object_id), declaration))

    if not present:
        return _unknown(
            REASON_NO_DECLARED_LIGHT, place_id=location_id, area_id=area_id
        )

    on: list[tuple[str, dict[str, Any], str]] = []
    unknown_power: list[str] = []
    for object_id, declaration in present:
        record = fixtures.get(object_id) if isinstance(fixtures, Mapping) else None
        power = str((record or {}).get("power") or "").strip().lower()
        if power == POWER_ON:
            on.append((object_id, declaration, str((record or {}).get("since") or "")))
        elif power != POWER_OFF:
            unknown_power.append(object_id)

    if on:
        # The brightest declared band wins when several lamps are on; the earliest
        # recorded ``since`` is how long light has been on (a real measurement).
        bands = [item[1].get("light_band") for item in on]
        band = _brightest(bands)
        since_values = [item[2] for item in on if item[2]]
        since = min(since_values) if since_values else None
        moment = _now(now)
        started = _instant(since)
        on_seconds = (
            int((moment - started).total_seconds()) if started is not None else None
        )
        if on_seconds is not None and on_seconds < 0:
            on_seconds = None
        return {
            "status": STATUS_AVAILABLE,
            "power": POWER_ON,
            "band": band,
            "band_source": ENVIRONMENT_MODEL,
            "source": KIND,
            "declared_source": on[0][1].get("declared_source"),
            "since": since,
            "on_seconds": on_seconds,
            "fixture_ids": [item[0] for item in on],
            "reason_code": None,
            "place_id": location_id,
            "area_id": area_id,
            "light_source_is_declaration": True,
            "temperature_or_noise_inferred": False,
        }

    if unknown_power:
        return _unknown(
            REASON_POWER_UNKNOWN,
            place_id=location_id,
            area_id=area_id,
            fixture_ids=unknown_power,
        )

    # Every present fixture is a known "off": this is a real fact, not darkness
    # invented from an absent reading.
    return {
        "status": STATUS_AVAILABLE,
        "power": POWER_OFF,
        "band": None,
        "band_source": ENVIRONMENT_MODEL,
        "source": KIND,
        "declared_source": present[0][1].get("declared_source"),
        "since": (fixtures.get(present[0][0]) or {}).get("since"),
        "on_seconds": None,
        "fixture_ids": [item[0] for item in present],
        "reason_code": None,
        "place_id": location_id,
        "area_id": area_id,
        "light_source_is_declaration": True,
        "temperature_or_noise_inferred": False,
    }


def _brightest(bands: list[Any]) -> Optional[str]:
    order = ["dim", "moderate", "bright"]
    known = [band for band in bands if band in order]
    if not known:
        return None
    return max(known, key=order.index)


def environment_input(light: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """The one legal Body ENVIRONMENT input for this light fact, or ``None``.

    ``None`` is the honest answer for "no declared light here", "power unknown"
    and "the lamp is off": there is no stimulus, so nothing is fabricated as a
    zero.  ``temperature_tendency`` is *never* produced -- v1 has no temperature
    producer and will not pretend to.
    """

    if not isinstance(light, Mapping):
        return None
    if light.get("status") != STATUS_AVAILABLE:
        return None
    if str(light.get("power") or "") != POWER_ON:
        return None
    intensity = LIGHT_BAND_INTENSITY.get(str(light.get("band") or ""))
    if intensity is None:
        return None
    return {"class": "ENVIRONMENT", "sensory_intensity": intensity}


def environment_note(light: Mapping[str, Any]) -> str:
    """One bounded provenance line for the Body transition."""

    return "%s:%s@%s/%s:light=%s:power=%s" % (
        ENVIRONMENT_MODEL,
        light.get("source") or KIND,
        light.get("place_id"),
        light.get("area_id"),
        light.get("band"),
        light.get("power"),
    )


def light_for_home(
    hermes_home: Any,
    *,
    now: Any = None,
    location: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Read-only derivation for one home: substrate + economy join, no writes.

    A missing/unreadable owner document is reported as ``unknown`` with a reason:
    it is never read as "the lamp is off".
    """

    import world_body_binding as binding

    if location is None:
        state = binding.load_world_state(hermes_home) or {}
        location = state.get("location") or {}
    place_id = location.get("place_id")
    area_id = location.get("area_id")
    try:
        live = substrate.SubstrateStore(
            substrate.substrate_path(hermes_home)
        ).load()
    except (substrate.BodySubstrateError, OSError):
        return _unknown(
            REASON_SUBSTRATE_UNAVAILABLE, place_id=place_id, area_id=area_id
        )
    if live is None:
        return _unknown(
            REASON_SUBSTRATE_UNAVAILABLE, place_id=place_id, area_id=area_id
        )
    return derive_light(
        placements=live.get("placements") or {},
        fixtures=live.get("fixtures") or {},
        catalog_by_instance=binding.owned_catalog_map(hermes_home),
        location_id=place_id,
        area_id=area_id,
        now=now,
    )


__all__ = [
    "SCHEMA",
    "KIND",
    "FEATURE_ENV",
    "LIGHT_BAND_INTENSITY",
    "ENVIRONMENT_MODEL",
    "FIXTURE_DECLARATIONS",
    "declaration_of_catalog",
    "declaration_of_object",
    "derive_light",
    "environment_input",
    "environment_note",
    "feature_enabled",
    "light_for_home",
]
