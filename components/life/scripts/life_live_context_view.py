"""v0.4 lived day: the bounded *live* view of where she actually is right now.

What this module is
-------------------
One read of the World observation seam turned into the present-tense facts a decision needs:
**which place she is in (by name), what is in her hands, which places she could walk to and how
long that takes, whether they are open, and which concrete things this observation really offers
there** -- plus the honest list of what could *not* be read.  It replaces the old situation
context, where a place was only an id and the model had to guess that ``place:home`` was a room.

Where the data comes from (and what it never does)
--------------------------------------------------
* Preferred source: the World seam's own contract ``provider.daily_life_context(limit=8)``
  (B's ``world_move_opportunity_source``).  When that callable exists it is the *only* thing this
  module reads, and the result is bounded here so an owner-side change cannot enlarge the prompt.
* Fallback source, only while that contract is not published yet: the read-only attributes the
  same seam already exposes for reports (``last_observation``, ``last_opportunity_view``,
  ``last_catalog``).  It is marked ``source: "provider.observation_fallback"`` in the output so a
  reader can never mistake it for the contract shape.
* Never: a file read, a SQLite read, a second observation, a recommendation, a score, a ranking,
  an automatic choice, or a grant.  Every entry carries data only.

Boundary rules kept on purpose
------------------------------
* A place she is **not** in is reported with its own ``observed`` verdict (``not_observed`` /
  ``OUT_OF_VIEW``); its furniture, notes and "what is on the shelf" are never copied in, because
  standing in the street is not seeing the inside of a shop.
* Every string is length-capped, every list is count-capped by ``limit``, and unknown facts are
  listed in ``unknowns`` by reason code instead of being filled with a guess.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

#: Output schema of this view (deliberately not the owner's schema).
SCHEMA = "chiyo.life.daily_life_context.v1"

#: The World seam's own contract name.  Present -> used; absent -> documented fallback.
CONTRACT_FUNCTION = "daily_life_context"

STATUS_OBSERVED = "observed"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"

SOURCE_CONTRACT = "provider.daily_life_context"
SOURCE_FALLBACK = "provider.observation_fallback"

#: How many entries one list may carry, before the caller's own ``limit`` is applied.
MAX_ENTRIES = 8
#: Character caps.  Names and labels are hers to read; refs stay intact (they are ids).
MAX_NAME_CHARS = 80
MAX_TEXT_CHARS = 120
MAX_REF_CHARS = 160
MAX_SECONDS = 24 * 3600

REASON_NOT_WIRED = "WORLD_OBSERVATION_NOT_WIRED"
REASON_CONTRACT_FAILED = "DAILY_LIFE_CONTEXT_FAILED"
REASON_CONTRACT_SHAPE = "DAILY_LIFE_CONTEXT_SHAPE_INVALID"
REASON_NO_OBSERVATION = "NO_WORLD_OBSERVATION"

UNKNOWN_PLACE_NAMES = "PLACE_NAMES_UNREAD"
UNKNOWN_HANDS = "HANDS_UNREAD"
UNKNOWN_OPENING = "OPENING_HOURS_NOT_READ"
UNKNOWN_WALK = "WALK_TIME_NOT_READ"
UNKNOWN_PREPARATION = "PREPARATION_NOT_KNOWN"
UNKNOWN_OPPORTUNITY_DETAIL = "OPPORTUNITY_DETAIL_NOT_READ"


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """One whitespace-collapsed, length-capped string (never a paragraph)."""

    return " ".join(str(value if value is not None else "").split())[:limit]


def _ref(value: Any) -> str:
    return str(value if value is not None else "").strip()[:MAX_REF_CHARS]


def _seconds(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return min(number, MAX_SECONDS)


def contract_available(provider: Any) -> bool:
    """Does this seam publish the ``daily_life_context`` contract?"""

    return callable(getattr(provider, CONTRACT_FUNCTION, None))


def empty_context(reason_code: str, *, detail: str = "") -> dict[str, Any]:
    """The honest absence: no place, no hands, no offer -- and the reason."""

    return {
        "schema": SCHEMA,
        "source": SOURCE_CONTRACT,
        "status": STATUS_UNAVAILABLE,
        "reason_code": reason_code,
        "detail": _text(detail, 200),
        "observed_at": None,
        "location": {},
        "body": {},
        "nearby": [],
        "opportunities": [],
        "unknowns": [reason_code],
    }


def read_live_context(provider: Any, *, limit: int = MAX_ENTRIES,
                      observed_at: Optional[str] = None) -> dict[str, Any]:
    """The bounded live view this round may show, or a named absence.

    Calls the seam's contract when it exists; falls back to the seam's own observation/report
    attributes otherwise.  ``limit`` caps every list, so the caller's bound is the only knob.
    """

    try:
        bound = max(1, int(limit))
    except (TypeError, ValueError):
        bound = MAX_ENTRIES
    if provider is None:
        return empty_context(REASON_NOT_WIRED)
    reader = getattr(provider, CONTRACT_FUNCTION, None)
    if callable(reader):
        try:
            raw = reader(limit=bound)
        except TypeError:
            # A contract that spells the bound positionally is still the contract.
            try:
                raw = reader(bound)
            except Exception as error:  # noqa: BLE001 - reported, never guessed
                return empty_context(REASON_CONTRACT_FAILED,
                                     detail="%s: %s" % (type(error).__name__, error))
        except Exception as error:  # noqa: BLE001
            return empty_context(REASON_CONTRACT_FAILED,
                                 detail="%s: %s" % (type(error).__name__, error))
        return _bounded_contract(raw, limit=bound, observed_at=observed_at)
    return _fallback_from_observation(provider, limit=bound, observed_at=observed_at)


# ----------------------------------------------------------------------------- contract shape


def _bounded_contract(raw: Any, *, limit: int, observed_at: Optional[str]) -> dict[str, Any]:
    """Bound the seam's own ``daily_life_context`` payload without re-interpreting it.

    The contract already decided *what* is knowable; this only enforces the size rules this prompt
    needs (counts, string caps, no nested documents).  An unusable shape is reported as such
    instead of being silently emptied.
    """

    if not isinstance(raw, Mapping):
        return empty_context(REASON_CONTRACT_SHAPE,
                             detail="daily_life_context returned %s" % type(raw).__name__)
    status = _text(raw.get("status") or "", 24) or STATUS_OBSERVED
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "source": SOURCE_CONTRACT,
        "status": status,
        "observed_at": _text(raw.get("observed_at") or observed_at or "", 40) or None,
        "location": _bound_location(raw.get("location")),
        "body": _bound_body(raw.get("body")),
        "nearby": _bound_nearby(raw.get("nearby"), limit=limit),
        "opportunities": _bound_opportunities(raw.get("opportunities"), limit=limit),
        "unknowns": [_text(item, 60) for item in list(raw.get("unknowns") or [])[:limit]
                     if _text(item, 60)],
    }
    if status == STATUS_UNAVAILABLE:
        out["reason_code"] = _text(raw.get("reason_code") or "", 60) or None
    return out


def _bound_location(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, limit in (("place_id", MAX_REF_CHARS), ("name", MAX_NAME_CHARS),
                       ("kind", 40), ("area_id", MAX_REF_CHARS), ("area_name", MAX_NAME_CHARS),
                       ("scene_id", MAX_REF_CHARS), ("local_time", 40), ("timezone", 40)):
        text = _text(raw.get(key), limit)
        if text:
            out[key] = text
    for key in ("here", "indoor"):
        if key in raw:
            out[key] = bool(raw.get(key))
    return out


def _bound_body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key in ("pose", "orientation"):
        text = _text(raw.get(key), 40)
        if text:
            out[key] = text
    hands: list[dict[str, Any]] = []
    for item in list(raw.get("hands") or [])[:MAX_ENTRIES]:
        if isinstance(item, Mapping):
            entry = {"slot": _text(item.get("slot"), 24) or None,
                     "object_id": _ref(item.get("object_id")) or None,
                     "name": _text(item.get("name"), MAX_NAME_CHARS) or None}
        else:
            entry = {"slot": None, "object_id": _ref(item) or None, "name": None}
        if entry["object_id"] or entry["slot"]:
            hands.append(entry)
    if hands:
        out["hands"] = hands
    if "hands_known" in raw:
        out["hands_known"] = bool(raw.get("hands_known"))
    return out


def _bound_nearby(raw: Any, *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in list(raw or [])[:limit]:
        if not isinstance(item, Mapping):
            continue
        entry: dict[str, Any] = {
            "place_id": _ref(item.get("place_id") or item.get("location_id")),
            "name": _text(item.get("name"), MAX_NAME_CHARS),
        }
        if not entry["place_id"] and not entry["name"]:
            continue
        for key, limit_chars in (("area_id", MAX_REF_CHARS), ("kind", 40),
                                 ("availability", 40), ("availability_reason", 60),
                                 ("reach", 40), ("observed", 40), ("open_expectation", 60),
                                 ("detail", MAX_TEXT_CHARS)):
            text = _text(item.get(key), limit_chars)
            if text:
                entry[key] = text
        seconds = _seconds(item.get("walk_seconds"))
        if seconds is not None:
            entry["walk_seconds"] = seconds
        places = [_text(row, MAX_TEXT_CHARS) for row in list(item.get("premises") or [])[:3]
                  if _text(row, MAX_TEXT_CHARS)]
        if places:
            entry["premises"] = places
        out.append(entry)
    return out


def _bound_opportunities(raw: Any, *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in list(raw or [])[:limit]:
        if not isinstance(item, Mapping):
            continue
        target = _ref(item.get("target_ref"))
        if not target:
            continue
        entry: dict[str, Any] = {"target_ref": target,
                                 "verb": _text(item.get("verb"), 32)}
        for key, limit_chars in (("object_id", MAX_REF_CHARS), ("label", MAX_NAME_CHARS),
                                 ("description", MAX_TEXT_CHARS),
                                 ("availability", 40), ("availability_reason", 60),
                                 ("preparation", MAX_TEXT_CHARS),
                                 ("location_id", MAX_REF_CHARS),
                                 ("place_name", MAX_NAME_CHARS),
                                 ("observed", 40), ("params", MAX_TEXT_CHARS)):
            text = _text(item.get(key), limit_chars)
            if text:
                entry[key] = text
        seconds = _seconds(item.get("duration_seconds"))
        if seconds is not None:
            entry["duration_seconds"] = seconds
        for key in ("available", "in_hand", "observed_now"):
            if key in item:
                entry[key] = bool(item.get(key))
        out.append(entry)
    return out


# --------------------------------------------------------------------- observation fallback


def _fallback_from_observation(provider: Any, *, limit: int,
                               observed_at: Optional[str]) -> dict[str, Any]:
    """Build the same bounded view from the seam's *existing* read-only attributes.

    This is the documented bridge for the stage where the ``daily_life_context`` contract is not
    published yet: the same one observation, the same catalog, the same opportunity view the
    reports already read -- nothing re-observed, nothing re-derived from a model.
    """

    observation = getattr(provider, "last_observation", None)
    if not isinstance(observation, Mapping) or not observation:
        return empty_context(REASON_NO_OBSERVATION)
    view = [item for item in (getattr(provider, "last_opportunity_view", None) or [])
            if isinstance(item, Mapping)]
    catalog = getattr(provider, "last_catalog", None)
    catalog = dict(catalog) if isinstance(catalog, Mapping) else {}
    places = {}
    for entry in catalog.get("opportunities") or []:
        if isinstance(entry, Mapping) and entry.get("location_id"):
            places[str(entry["location_id"])] = entry
    visible = {}
    for entry in observation.get("visible_objects") or []:
        if isinstance(entry, Mapping) and entry.get("object_id"):
            visible[str(entry["object_id"])] = entry

    unknowns: list[str] = []
    location = {
        "place_id": _ref((observation.get("location") or {}).get("place_id")),
        "area_id": _ref((observation.get("location") or {}).get("area_id")),
    }
    here = places.get(location["place_id"]) or {}
    if here.get("name"):
        location["name"] = _text(here["name"], MAX_NAME_CHARS)
        location["here"] = True
    else:
        unknowns.append(UNKNOWN_PLACE_NAMES)
    if here.get("kind"):
        location["kind"] = _text(here["kind"], 40)
    if catalog.get("local_time"):
        location["local_time"] = _text(catalog["local_time"], 40)

    body: dict[str, Any] = {}
    if observation.get("pose"):
        body["pose"] = _text(observation["pose"], 40)
    if observation.get("orientation"):
        body["orientation"] = _text(observation["orientation"], 40)
    raw_hands = observation.get("hands")
    if isinstance(raw_hands, Mapping):
        hands = []
        for slot in sorted(raw_hands):
            held = raw_hands.get(slot)
            if not held:
                continue
            object_id = _ref(held)
            hands.append({"slot": _text(slot, 24) or None, "object_id": object_id,
                          "name": _text((visible.get(object_id) or {}).get("label"),
                                        MAX_NAME_CHARS) or None})
        body["hands"] = hands[:MAX_ENTRIES]
        body["hands_known"] = True
    else:
        held = [entry for entry in visible.values() if entry.get("in_hand")]
        if held:
            body["hands"] = [{"slot": None, "object_id": _ref(entry.get("object_id")),
                              "name": _text(entry.get("label"), MAX_NAME_CHARS) or None}
                             for entry in held[:MAX_ENTRIES]]
        body["hands_known"] = False
        unknowns.append(UNKNOWN_HANDS)

    nearby: list[dict[str, Any]] = []
    for place_id, entry in places.items():
        if place_id == location["place_id"]:
            continue
        reachability = entry.get("reachability") or {}
        availability = entry.get("availability") or {}
        observed = entry.get("observed") or {}
        item = {
            "place_id": _ref(place_id),
            "name": _text(entry.get("name"), MAX_NAME_CHARS) or place_id,
            "kind": _text(entry.get("kind"), 40),
            "reach": _text(reachability.get("status"), 40),
            "availability": _text(availability.get("status"), 40) or "NOT_READ",
            "availability_reason": _text(availability.get("reason_code"), 60),
            # Whether she can *see* into it from here.  Anything but ``visible_here`` means the
            # inside of that place is not hers to know yet, and no furniture is copied in.
            "observed": _text(observed.get("status"), 40) or "not_observed",
        }
        seconds = _seconds(reachability.get("walk_seconds"))
        if seconds is None:
            unknowns.append(UNKNOWN_WALK)
        else:
            item["walk_seconds"] = seconds
        if not item["availability"] == "OPEN" and not availability.get("status"):
            unknowns.append(UNKNOWN_OPENING)
        expectation = entry.get("opening_expectation") or {}
        if expectation.get("status"):
            item["open_expectation"] = _text(expectation.get("status"), 60)
        if item["observed"] == "visible_here":
            # Only a place she is really in has an inside she can read.  Standing in the street
            # does not make the shop's counter visible, so its affordances are not copied in --
            # and the caller is told the detail was not read.
            premises = [_text(row.get("description"), MAX_TEXT_CHARS)
                        for row in (entry.get("affordances") or [])
                        if isinstance(row, Mapping) and row.get("description")]
            if premises:
                item["premises"] = premises[:3]
        elif entry.get("affordances"):
            unknowns.append(UNKNOWN_OPPORTUNITY_DETAIL)
        nearby.append({key: value for key, value in item.items() if value not in ("", None)})
    nearby.sort(key=lambda row: (row.get("walk_seconds", MAX_SECONDS), row["place_id"]))
    nearby = nearby[:limit]

    opportunities: list[dict[str, Any]] = []
    for item in view:
        target = _ref(item.get("target_ref"))
        verb = _text(item.get("verb") or item.get("kind"), 32)
        if not target or verb == "MOVE":
            # A move is already described as a place above; spelling it twice would fill the
            # prompt with the same fact.
            continue
        place_id = _ref(item.get("location_id"))
        place = places.get(place_id) or {}
        place_observed = ((place.get("observed") or {}).get("status") or "")
        object_id = _ref(item.get("object_id"))
        entry: dict[str, Any] = {
            "target_ref": target,
            "verb": verb,
            "location_id": place_id,
            "place_name": _text(place.get("name"), MAX_NAME_CHARS),
            "availability": _text((place.get("availability") or {}).get("status"), 40)
            or _text(item.get("availability"), 40),
            "availability_reason": _text(item.get("owner_blocked_reason")
                                         or (place.get("availability") or {}).get("reason_code"), 60),
            "observed": _text(place_observed, 40) or "not_observed",
        }
        if object_id:
            entry["object_id"] = object_id
            label = _text((visible.get(object_id) or {}).get("label"), MAX_NAME_CHARS)
            if label:
                entry["label"] = label
            entry["in_hand"] = bool((visible.get(object_id) or {}).get("in_hand"))
        # What the owner's own decoding said about this offer (pose/orientation/rest/stay).  It is
        # copied as a bounded string so the model reads the *meaning*, not an opaque ref.
        params = item.get("params")
        if isinstance(params, Mapping) and params:
            entry["params"] = _text(
                ", ".join("%s=%s" % (key, params[key]) for key in sorted(params)), MAX_TEXT_CHARS)
        elif item.get("pose") or item.get("orientation"):
            entry["params"] = _text("pose=%s, orientation=%s"
                                    % (item.get("pose"), item.get("orientation")), MAX_TEXT_CHARS)
        if item.get("affordance"):
            entry["label"] = _text("%s (%s)" % (item.get("affordance"), item.get("stay_seconds")),
                                   MAX_NAME_CHARS)
        seconds = _seconds(item.get("stay_seconds"))
        if seconds is not None:
            entry["duration_seconds"] = seconds
        if place_observed == "not_observed":
            # Standing somewhere else is not seeing this place's inside; the offer is still real
            # (the owner published it), but its detail is not hers yet.
            unknowns.append(UNKNOWN_OPPORTUNITY_DETAIL)
        opportunities.append({key: value for key, value in entry.items()
                              if value not in ("", None)})
    opportunities = opportunities[:limit]

    status = STATUS_OBSERVED
    if unknowns:
        status = STATUS_PARTIAL
    if not places:
        unknowns.append(UNKNOWN_PLACE_NAMES)
    if any(row["verb"] in {"PICK_UP", "PLACE", "CONSUME"} for row in opportunities):
        unknowns.append(UNKNOWN_PREPARATION)
    return {
        "schema": SCHEMA,
        "source": SOURCE_FALLBACK,
        "status": status,
        "observed_at": _text(observed_at or catalog.get("local_time") or "", 40) or None,
        "location": {key: value for key, value in location.items() if value},
        "body": body,
        "nearby": nearby,
        "opportunities": opportunities,
        "unknowns": sorted(set(unknowns)),
    }


def bound_for_prompt(context: Any, *, limit: int = MAX_ENTRIES) -> dict[str, Any]:
    """Re-bound an already-built view for a prompt (idempotent, safe on ``None``/garbage)."""

    if not isinstance(context, Mapping) or not context:
        return {}
    status = _text(context.get("status") or "", 24)
    out: dict[str, Any] = {
        "status": status or STATUS_OBSERVED,
        "source": _text(context.get("source") or "", 40),
        "observed_at": _text(context.get("observed_at") or "", 40),
        "location": _bound_location(context.get("location")),
        "body": _bound_body(context.get("body")),
        "nearby": _bound_nearby(context.get("nearby"), limit=limit),
        "opportunities": _bound_opportunities(context.get("opportunities"), limit=limit),
        "unknowns": [_text(item, 60) for item in list(context.get("unknowns") or [])[:limit]][:limit],
    }
    if context.get("reason_code"):
        out["reason_code"] = _text(context.get("reason_code"), 60)
    return {key: value for key, value in out.items() if value not in ("", [], {})}


__all__ = [
    "CONTRACT_FUNCTION",
    "MAX_ENTRIES",
    "REASON_CONTRACT_FAILED",
    "REASON_CONTRACT_SHAPE",
    "REASON_NO_OBSERVATION",
    "REASON_NOT_WIRED",
    "SCHEMA",
    "SOURCE_CONTRACT",
    "SOURCE_FALLBACK",
    "STATUS_OBSERVED",
    "STATUS_PARTIAL",
    "STATUS_UNAVAILABLE",
    "bound_for_prompt",
    "contract_available",
    "empty_context",
    "read_live_context",
]
