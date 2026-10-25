#!/usr/bin/env python3
"""v0.4 B: one encoding for the two *hand* verbs' whole parameter set, and nothing else.

Why this module exists
----------------------
AR-0's World adapter carries exactly one string per action (``verb`` + ``target``),
and the Life core reads the target's namespace back to decide which owner verb an
adopted opportunity implies.  ``place:`` / ``object:`` / ``stay:`` / ``bodyact:``
already work that way.  The World owner's two *object* verbs are different from all
of them:

* ``PICK_UP`` needs ``object_id`` **and** ``slot_id`` (``world_action_resolver:1198``);
* ``PLACE`` needs ``object_id`` **and** a whole ``target`` placement mapping
  (``world_action_resolver:1334``), whose fields depend on the placement kind
  (``world_body_substrate.validate_placement_target:234``).

Both are real, already-implemented owner verbs (``action_intent.EXPOSED_VERBS`` has
listed them since M15), and until now no Life-side spelling existed, so nothing that
decided "pick up the cup" could ever reach the owner.  This module is that spelling,
and only that:

    dailyact:pick_up:<slot_id>:<object_id>
    dailyact:place:room:<location_id>:<area_id>:<object_id>
    dailyact:place:surface:<location_id>:<surface_id>:<object_id>

Every plain field is percent-encoded (``urllib.parse.quote(value, safe="")``), because
a real surface id is ``desk_surface:<item_instance_id>`` and therefore contains the
separator.  The surface field is instead spelled with a **ref-safe token** (see
``_encode_surface_field``): the whole target must also pass AG-0's structured-ref
charset when it travels as an observed opportunity, and that charset has no ``%``.
Encoding is deterministic (same parameters -> same string) and reversible
(``parse`` returns exactly what ``encode`` was given), the field count is fixed per
action, and an extra or missing field is a refusal rather than a guess.

Old and new surface spellings are told apart without a version byte.  An old field is
``quote(value, safe="")``, whose output only uses ``[A-Za-z0-9_.~-]`` plus ``%XX``; a
new field only uses ``[A-Za-z0-9_.-]`` plus ``/`` escapes.  ``/`` cannot appear in the
old spelling (``quote`` writes it as ``%2F``) and ``%`` cannot appear in the new one, so
a field containing either marker belongs to exactly one spelling.  A field containing
neither is a plain literal in both spellings and therefore means the same value --
that is why an old ``desk-x3Asurface`` still reads as the literal ``desk-x3Asurface``
rather than being guessed into ``desk:surface``.

It provides the four things the Life core needs, and no others:

* ``encode_daily_action_target(verb, params)`` -- the target string;
* ``parse_daily_action_target(target)`` -- back to the owner's own parameters
  (``None`` for another namespace, an error for a malformed member of this one);
* ``build_daily_action_step(target, origin_ref=...)`` -- the AG-2 World step spec,
  same shape ``life_body_action_targets.build_body_action_step`` returns, with the
  idempotency key bound to every parameter;
* ``daily_action_completion(target)`` -- the LR-5 ``{fact_key, expected_value}`` a
  completion contract names, plus ``daily_action_observed_fact`` which reads what a
  *fresh* canonical World snapshot says about that exact placement, so a criterion
  can only be satisfied by a real observation.

What it deliberately does NOT do
--------------------------------
* it does not decide what may be picked up or where something may be put: the owner's
  ``get_available_actions`` verdict is the only source of candidates
  (``world_move_opportunity_source.observed_daily_action_opportunities``), and the
  Resolver re-reads capability, hands, pose and destination at commit time;
* it does not submit, mint, gate or receipt anything -- AR-0 still owns the ledger, the
  Intent Executor still mints the one-shot authorization and the Resolver is still the
  only committer;
* it does not claim that a *post* placement state proves *this* operation ran.  The
  fact below is a state fact read from the owner's own snapshot; attribution stays with
  AR-0's receipt and the World's execution record.

Narrow admission
----------------
``container`` and ``in_transit`` are legal substrate placement kinds but are refused
by the owner's destination rule for PLACE (``world_action_resolver._m15_destination_verdict``:
``DESTINATION_NOT_ALLOWED``, V1), so this encoding cannot express them either: a target
that cannot be built cannot be submitted by hand.  ``hand`` placement (moving something
from one hand to the other) is a real V1 destination but it is not "putting something
down", so it is not encoded this round; widening this is a separate, explicit change.

Enumerations
------------
``HAND_SLOTS`` is a *copy* of ``world_body_substrate.HAND_SLOTS`` and
``PLACEMENT_*``/the destination key sets are a copy of that module's placement
contract.  They are used only to refuse an incomplete or malformed encoding *before*
submission; the owner stays the only authority over what may commit, and a vocabulary
the owner extends must be extended here in the same change.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import quote, unquote

#: The target namespace this module owns.  Every other prefix is somebody else's.
DAILY_ACTION_REF_PREFIX = "dailyact:"

PICK_UP_VERB = "PICK_UP"
PLACE_VERB = "PLACE"
#: The verbs this encoding can express completely.
DAILY_ACTION_VERBS = (PICK_UP_VERB, PLACE_VERB)

PICK_UP_ACTION = "pick_up"
PLACE_ACTION = "place"
DAILY_ACTION_ACTIONS = (PICK_UP_ACTION, PLACE_ACTION)

#: Owner hand-slot vocabulary (copied, see the module docstring).
HAND_SLOTS = ("left_hand", "right_hand")

#: Owner placement kinds this encoding can spell (see "Narrow admission").
PLACEMENT_ROOM = "room"
PLACEMENT_SURFACE = "surface"
PLACEMENT_DESTINATION_KINDS = (PLACEMENT_ROOM, PLACEMENT_SURFACE)
_ROOM_DESTINATION_FIELDS = ("kind", "location_id", "area_id")
_SURFACE_DESTINATION_FIELDS = ("kind", "location_id", "surface_id")

#: The owner's own parameter names, taken from the Resolver itself.
PICK_UP_PARAMS = ("object_id", "slot_id")
PLACE_PARAMS = ("object_id", "target")

#: Bounded field length: the Resolver's own ``_text`` ceilings are the same order.
MAX_FIELD = 120

#: LR-5 observed-fact vocabulary.  Same ``world_fact:`` namespace LR-5 already owns
#: (``activity_progress_completion_lr5.POLICY_WORLD_FACT_V1``); no new policy version,
#: and a different key from the body verbs' (``world_body_pose_ref`` etc.) because the
#: fact is a *placement*, not a posture.
FACT_KEY_DAILY_PLACEMENT = "world_daily_placement_ref"
FACT_PREFIX = "world_fact:placement:"
HAND_FACT = FACT_PREFIX + "%s:hand:%s"
ROOM_FACT = FACT_PREFIX + "%s:room:%s:%s"
SURFACE_FACT = FACT_PREFIX + "%s:surface:%s"

#: Where the fact is really read from.  Named in the evidence so a report can say which
#: owner surface answered.
SOURCE_SNAPSHOT = "world_gateway.get_snapshot"

#: The two placement kinds the canonical snapshot can name without inventing a field.
_KIND_HAND = "hand"
_KIND_ROOM = "room"
_KIND_SURFACE = "surface"
_KIND_CONTAINER = "container"

#: Effect predicates AG-2 records for the step.  They are Life-side labels, not World
#: facts: the World fact is the one ``fact_key``/``expected_value`` names.
EFFECT_PICKED_UP = "world_object_picked_up"
EFFECT_PLACED = "world_object_placed"

#: Caveats that travel with an observation so "read" is never mistaken for "proved".
UNKNOWN_ROOM_LOCATION = "room_placement_location_from_snapshot_location"
UNKNOWN_OBJECT_NOT_VISIBLE = "object_not_visible_in_snapshot_after_place"
UNKNOWN_STILL_HELD = "object_still_in_hand"
#: ``PICK_UP``'s counterpart: the snapshot shows a real, non-hand placement, so the
#: hands are demonstrably *not* where the action wanted them.
UNKNOWN_NOT_IN_HAND = "object_not_in_hand"


class DailyActionTargetError(ValueError):
    """A ``dailyact:`` ref that cannot be expressed completely and legally."""


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------


def is_daily_action_target(ref: Any) -> bool:
    """Is this ref in the daily-action namespace at all?  Says nothing about validity."""

    return str(ref or "").strip().startswith(DAILY_ACTION_REF_PREFIX)


def _field(value: Any, label: str) -> str:
    """One bounded, non-empty identifier field (before quoting)."""

    if not isinstance(value, str):
        raise DailyActionTargetError("%s must be a string (got %r)" % (label, value))
    text = value.strip()
    if not text:
        raise DailyActionTargetError("%s must be non-empty" % label)
    if len(text) > MAX_FIELD:
        raise DailyActionTargetError(
            "%s is too long (%d > %d)" % (label, len(text), MAX_FIELD))
    if any(character.isspace() or ord(character) < 32 for character in text):
        raise DailyActionTargetError(
            "%s must not contain whitespace or control characters (got %r)"
            % (label, text))
    return text


def _require_exact(values: Mapping[str, Any], expected: tuple[str, ...],
                   label: str) -> None:
    """Exactly these keys, no extras -- a half-specified parameter set is never guessed."""

    given = {str(key) for key in values}
    missing = sorted(set(expected) - given)
    unknown = sorted(given - set(expected))
    if missing or unknown:
        raise DailyActionTargetError(
            "%s params must be exactly %s (missing %s, unknown %s)"
            % (label, list(expected), missing, unknown))


def encode_daily_action_target(verb: Any, params: Any) -> str:
    """``verb`` + its *whole* owner parameter mapping -> one deterministic target.

    Only the owner's own parameters are accepted, and all of them: ``PICK_UP`` takes
    ``object_id`` + ``slot_id``, ``PLACE`` takes ``object_id`` + a ``target`` placement
    mapping of kind ``room`` or ``surface``.  Anything else -- an unknown verb, an extra
    field, a missing field, a hand slot outside the owner's vocabulary, a container
    destination -- raises instead of producing a target that would look selectable and
    then fail at the owner.
    """

    verb_text = str(verb or "").strip().upper()
    if verb_text not in DAILY_ACTION_VERBS:
        raise DailyActionTargetError(
            "verb must be one of %s (got %r)" % (list(DAILY_ACTION_VERBS), verb))
    if not isinstance(params, Mapping):
        raise DailyActionTargetError("params must be an object for verb %s" % verb_text)

    if verb_text == PICK_UP_VERB:
        _require_exact(params, PICK_UP_PARAMS, PICK_UP_VERB)
        slot = _field(params.get("slot_id"), "slot_id")
        if slot not in HAND_SLOTS:
            raise DailyActionTargetError(
                "slot_id must be one of %s (got %r)" % (list(HAND_SLOTS), slot))
        object_id = _field(params.get("object_id"), "object_id")
        return "%s%s:%s:%s" % (DAILY_ACTION_REF_PREFIX, PICK_UP_ACTION,
                               quote(slot, safe=""), quote(object_id, safe=""))

    _require_exact(params, PLACE_PARAMS, PLACE_VERB)
    object_id = _field(params.get("object_id"), "object_id")
    target = params.get("target")
    if not isinstance(target, Mapping):
        raise DailyActionTargetError("PLACE target must be a placement object")
    kind = str(target.get("kind") or "").strip().lower()
    if kind == PLACEMENT_ROOM:
        _require_exact(target, _ROOM_DESTINATION_FIELDS, "PLACE target(room)")
        location = _field(target.get("location_id"), "placement.location_id")
        area = _field(target.get("area_id"), "placement.area_id")
        return "%s%s:%s:%s:%s:%s" % (
            DAILY_ACTION_REF_PREFIX, PLACE_ACTION, PLACEMENT_ROOM,
            quote(location, safe=""), quote(area, safe=""),
            quote(object_id, safe=""))
    if kind == PLACEMENT_SURFACE:
        _require_exact(target, _SURFACE_DESTINATION_FIELDS, "PLACE target(surface)")
        location = _field(target.get("location_id"), "placement.location_id")
        surface = _field(target.get("surface_id"), "placement.surface_id")
        return "%s%s:%s:%s:%s:%s" % (
            DAILY_ACTION_REF_PREFIX, PLACE_ACTION, PLACEMENT_SURFACE,
            quote(location, safe=""), _encode_surface_field(surface),
            quote(object_id, safe=""))
    raise DailyActionTargetError(
        "PLACE target kind %r cannot be expressed: this encoding carries %s "
        "(the owner refuses container/in_transit destinations for V1 PLACE)"
        % (kind, list(PLACEMENT_DESTINATION_KINDS)))


def _unquote_field(raw: str, label: str) -> str:
    return _field(unquote(raw), label)


#: Characters a surface token may carry literally.  This is AG-0's structured-ref id
#: charset (``[A-Za-z0-9_.:/-]``) minus the ``:`` field separator and minus the escape
#: introducer ``/``, so the encoded target itself survives as a target ref.
_SURFACE_LITERAL = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")

#: The surface field's escape introducer.  ``/`` is legal inside an AG-0 ref id but can
#: never occur literally in the older percent spelling (``quote(value, safe="")``
#: writes it as ``%2F``), which is what separates the two spellings without a version
#: byte.  ``-`` cannot play that role: it is a plain unreserved character in the old
#: spelling, so an old literal ``-x3A`` would be indistinguishable from a new escape.
_SURFACE_ESCAPE = "/"


def _encode_surface_field(value: str) -> str:
    """A reversible, ref-safe spelling of a surface id.

    ``urllib.parse.quote(value, safe="")`` would spell ``desk_surface:<item>`` as
    ``desk_surface%3A<item>``.  That is fine inside this module, but the same string also
    travels as an AG-0 ``target_ref``/opportunity id, and ``%`` is not in that contract's
    charset (``activity_continuity._STRUCTURED_REF_RE``).  So the surface field uses
    ``/`` as an escape: ``/`` doubles, every other non-literal character becomes one
    ``/xHH`` byte of its UTF-8 encoding.  The result contains only ``[A-Za-z0-9_./-]``,
    still no field separator, and is byte-identical after ``_decode_surface_field`` --
    ASCII and non-ASCII alike.
    """

    encoded: list[str] = []
    for character in value:
        if character == _SURFACE_ESCAPE:
            encoded.append(_SURFACE_ESCAPE * 2)
        elif character in _SURFACE_LITERAL:
            encoded.append(character)
        else:
            for byte in character.encode("utf-8"):
                encoded.append("%sx%02x" % (_SURFACE_ESCAPE, byte))
    return "".join(encoded)


def _decode_surface_field(token: str, label: str = "placement.surface_id") -> str:
    """Reverse :func:`_encode_surface_field`; an older ``%XX`` field still reads.

    Disambiguation needs no version: a token containing ``%`` is the older percent
    spelling and is unquoted as before; otherwise the token can only be the current
    spelling, where ``//`` is a literal ``/`` and ``/xHH`` is one UTF-8 byte.  Runs of
    ``/xHH`` bytes are decoded as UTF-8 together (a multi-byte character is several of
    them); a run that is not valid UTF-8 falls back to Latin-1, which is what the
    pre-UTF-8 two-digit spelling meant.

    A token with neither marker is a plain literal in both spellings -- every character
    in it is returned unchanged, which is exactly why an old ``desk-x3Asurface`` is not
    reinterpreted as ``desk:surface``.  A bare ``/`` followed by neither ``/`` nor a
    valid ``xHH`` is likewise kept literally; the encoder always doubles it, so no new
    target depends on that leniency.
    """

    if "%" in token:
        return _unquote_field(token, label)
    decoded: list[str] = []
    pending = bytearray()

    def flush() -> None:
        if not pending:
            return
        raw = bytes(pending)
        pending.clear()
        try:
            decoded.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            decoded.append(raw.decode("latin-1"))

    index = 0
    while index < len(token):
        character = token[index]
        if character != _SURFACE_ESCAPE:
            flush()
            decoded.append(character)
            index += 1
            continue
        following = token[index + 1:index + 2]
        if following == _SURFACE_ESCAPE:
            flush()
            decoded.append(_SURFACE_ESCAPE)
            index += 2
            continue
        if following == "x" and len(token) >= index + 4:
            try:
                byte = int(token[index + 2:index + 4], 16)
            except ValueError:
                pass
            else:
                pending.append(byte)
                index += 4
                continue
        flush()
        decoded.append(_SURFACE_ESCAPE)
        index += 1
    flush()
    return _field("".join(decoded), label)


def parse_daily_action_target(target: Any) -> Optional[dict[str, Any]]:
    """Split a ``dailyact:`` target back into the owner's exact parameters.

    Returns ``None`` when the target is not in this namespace at all (another namespace
    is not this module's business) and raises :class:`DailyActionTargetError` when it is
    one but is incomplete, carries extra fields, or names a value outside the owner's
    vocabulary -- a half-specified pick-up or destination is never guessed at.

    The returned mapping is the whole contract, and only these four keys:

    ``verb``       ``PICK_UP`` | ``PLACE``
    ``params``     the owner's own parameter mapping, nothing added:
                   ``{"object_id", "slot_id"}`` or
                   ``{"object_id", "target": {"kind", "location_id", ...}}``
    ``target_ref`` the encoded target, unchanged (AR-0's single-target identity)
    ``object_id``  the object the action names, for reports and verification
    """

    text = str(target or "").strip()
    if not text.startswith(DAILY_ACTION_REF_PREFIX):
        return None
    fields = text.split(":")
    if len(fields) < 2:
        raise DailyActionTargetError(
            "%r must be %s<action>:... (got no action field)"
            % (text, DAILY_ACTION_REF_PREFIX))
    action = fields[1]
    if action not in DAILY_ACTION_ACTIONS:
        raise DailyActionTargetError(
            "%r names action %r; this encoding carries %s"
            % (text, action, list(DAILY_ACTION_ACTIONS)))

    if action == PICK_UP_ACTION:
        if len(fields) != 4:
            raise DailyActionTargetError(
                "%r must have exactly 4 colon-separated fields "
                "(dailyact:pick_up:<slot_id>:<object_id>), got %d" % (text, len(fields)))
        slot = _unquote_field(fields[2], "slot_id")
        if slot not in HAND_SLOTS:
            raise DailyActionTargetError(
                "%r: slot_id must be one of %s" % (text, list(HAND_SLOTS)))
        object_id = _unquote_field(fields[3], "object_id")
        return {
            "verb": PICK_UP_VERB,
            "params": {"object_id": object_id, "slot_id": slot},
            "target_ref": text,
            "object_id": object_id,
        }

    if len(fields) != 6:
        raise DailyActionTargetError(
            "%r must have exactly 6 colon-separated fields "
            "(dailyact:place:<kind>:<destination>:<detail>:<object_id>), got %d"
            % (text, len(fields)))
    kind = fields[2]
    if kind == PLACEMENT_ROOM:
        location = _unquote_field(fields[3], "placement.location_id")
        area = _unquote_field(fields[4], "placement.area_id")
        object_id = _unquote_field(fields[5], "object_id")
        return {
            "verb": PLACE_VERB,
            "params": {"object_id": object_id,
                       "target": {"kind": PLACEMENT_ROOM, "location_id": location,
                                  "area_id": area}},
            "target_ref": text,
            "object_id": object_id,
        }
    if kind == PLACEMENT_SURFACE:
        location = _unquote_field(fields[3], "placement.location_id")
        surface = _decode_surface_field(fields[4], "placement.surface_id")
        object_id = _unquote_field(fields[5], "object_id")
        return {
            "verb": PLACE_VERB,
            "params": {"object_id": object_id,
                       "target": {"kind": PLACEMENT_SURFACE, "location_id": location,
                                  "surface_id": surface}},
            "target_ref": text,
            "object_id": object_id,
        }
    raise DailyActionTargetError(
        "%r names destination kind %r; this encoding carries %s"
        % (text, kind, list(PLACEMENT_DESTINATION_KINDS)))


# --------------------------------------------------------------------------
# step spec (AG-2 shape, same as the body verbs)
# --------------------------------------------------------------------------


def daily_action_effect_predicates(parsed: Mapping[str, Any]) -> list[str]:
    """The AG-2 effect label for one parsed daily action."""

    return [EFFECT_PICKED_UP if parsed["verb"] == PICK_UP_VERB else EFFECT_PLACED]


def build_daily_action_step(
    target: Any,
    *,
    origin_ref: Any = None,
) -> dict[str, Any]:
    """The AG-2 world step spec for one pick-up or placement.

    Same shape the core already builds for ``place:`` / ``object:`` and the body verbs
    (``step_kind`` ``WORLD_ACTION``, one frozen ``payload_spec`` with a single
    ``target``), so AR-0's payload contract does not change: the encoded ref itself is
    the parameter set, and the World bridge decodes it at submission time.

    ``origin_ref`` is the caller's own name for the situation this step was decided in
    (a place + revision, a perception ref, a checkpoint).  It travels in the
    idempotency key only, quoted so it can never be confused with a field, so a retry
    from the same origin reuses one execution and a different origin can never be
    mistaken for the same action.
    """

    parsed = parse_daily_action_target(target)
    if parsed is None:
        raise DailyActionTargetError("%r is not a daily action target" % (target,))

    import life_integration_ag2 as ag2
    from action_reality_ledger import ACTION_KIND_WORLD

    origin = "" if origin_ref is None else str(origin_ref).strip()
    if len(origin) > 256:
        raise DailyActionTargetError("origin_ref is too long (%d > 256)" % len(origin))
    target_ref = str(parsed["target_ref"])
    return {
        "step_kind": ag2.STEP_KIND_WORLD_ACTION,
        "action_kind": ACTION_KIND_WORLD,
        "target_ref": target_ref,
        "payload_spec": {
            "verb": str(parsed["verb"]),
            # AR-0 keeps its single-target contract: the whole parameter set is the
            # encoded ref, and the World bridge decodes it at submission time.
            "target": target_ref,
            # AR-0-side bookkeeping only: the World's own dependency evidence is
            # produced by the Intent Executor's fresh read, never by this payload.
            "expected_dependencies": {},
        },
        "expected_effect_predicates": daily_action_effect_predicates(parsed),
        "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
        "idempotency_key": "idem_step:v02:world:dailyact:%s:%s"
                           % (quote(origin, safe="") or "unknown", target_ref),
    }


# --------------------------------------------------------------------------
# observed fact (LR-5 completion criterion, readable from the canonical snapshot)
# --------------------------------------------------------------------------


def _expected_placement_fact(parsed: Mapping[str, Any]) -> str:
    """The fact value that means "this action's placement state holds"."""

    object_id = str(parsed["object_id"])
    params = parsed.get("params") or {}
    if parsed["verb"] == PICK_UP_VERB:
        return HAND_FACT % (object_id, str(params["slot_id"]))
    destination = params.get("target") or {}
    if str(destination.get("kind")) == PLACEMENT_SURFACE:
        return SURFACE_FACT % (object_id, str(destination["surface_id"]))
    return ROOM_FACT % (object_id, str(destination["location_id"]),
                        str(destination["area_id"]))


def daily_action_completion(target: Any) -> dict[str, Any]:
    """The ``{fact_key, expected_value}`` an LR-5 completion contract names.

    The value is a placement fact in the ``world_fact:`` namespace LR-5 already owns:

    * ``PICK_UP`` -> ``world_fact:placement:<object>:hand:<slot>``
    * ``PLACE`` room -> ``world_fact:placement:<object>:room:<location>:<area>``
    * ``PLACE`` surface -> ``world_fact:placement:<object>:surface:<surface>``

    It is *only* a state assertion.  It never proves which execution produced the
    state; that stays with AR-0's receipt and the World's own execution record.
    """

    parsed = parse_daily_action_target(target)
    if parsed is None:
        raise DailyActionTargetError("%r is not a daily action target" % (target,))
    return {
        "fact_key": FACT_KEY_DAILY_PLACEMENT,
        "expected_value": _expected_placement_fact(parsed),
    }


def _snapshot_placement(snapshot: Mapping[str, Any],
                        object_id: str) -> Optional[dict[str, Any]]:
    """The canonical snapshot's own entry for one object, or ``None``."""

    for entry in snapshot.get("visible_objects") or []:
        if isinstance(entry, Mapping) and str(entry.get("object_id")) == object_id:
            return dict(entry)
    return None


def _held_slot(snapshot: Mapping[str, Any], object_id: str) -> Optional[str]:
    """Which hand slot the snapshot says holds this object (``None`` when neither)."""

    hands = snapshot.get("hands")
    if not isinstance(hands, Mapping):
        return None
    for slot in HAND_SLOTS:
        if str(hands.get(slot) or "") == object_id:
            return slot
    return None


def daily_action_observed_fact(
    parsed: Any,
    snapshot: Any,
) -> dict[str, Any]:
    """What a *fresh* canonical World snapshot says about this exact placement.

    ``parsed`` is what :func:`parse_daily_action_target` returned (a target string is
    accepted too).  ``snapshot`` is the gateway's own ``get_snapshot`` payload, which is
    the only bounded read that exposes hands and the object's placement kind.

    The result is what an LR-5 ``WORLD_FACT_MATCH`` contract needs, and nothing more:
    ``value`` is ``None`` whenever the owner cannot answer, so an unreadable snapshot can
    never satisfy a criterion, and ``satisfied`` is true only when the read value is
    *exactly* the expected one.  ``unknowns`` names every place where the read is weaker
    than the fact -- a room placement's *place* is inferred from the body's own current
    location (that is the owner's destination rule), and it is labelled as such instead
    of being presented as a direct reading.
    """

    if isinstance(parsed, str) or not isinstance(parsed, Mapping):
        parsed = parse_daily_action_target(parsed)
    if parsed is None:
        raise DailyActionTargetError(
            "daily_action_observed_fact needs a parsed daily action target")

    object_id = str(parsed["object_id"])
    expected = _expected_placement_fact(parsed)
    unknowns: list[str] = []
    value: Optional[str] = None
    observed: dict[str, Any] = {"object_id": object_id, "hands": None,
                               "placement": None}
    source = SOURCE_SNAPSHOT

    if not isinstance(snapshot, Mapping):
        return {
            "fact_key": FACT_KEY_DAILY_PLACEMENT,
            "expected_value": expected,
            "value": None,
            "satisfied": False,
            "source": source,
            "observed": observed,
            "unknowns": ["snapshot_unavailable"],
        }

    hands = snapshot.get("hands") if isinstance(snapshot.get("hands"), Mapping) else {}
    observed["hands"] = dict(hands)
    held_slot = _held_slot(snapshot, object_id)
    placement = _snapshot_placement(snapshot, object_id)
    if placement is not None:
        observed["placement"] = {
            "kind": placement.get("kind"),
            "surface_id": placement.get("surface_id"),
            "container_id": placement.get("container_id"),
            "slot_id": placement.get("slot_id"),
            "in_hand": bool(placement.get("in_hand")),
        }

    if str(parsed["verb"]) == PICK_UP_VERB:
        if held_slot is not None:
            value = HAND_FACT % (object_id, held_slot)
        elif placement is not None and not placement.get("in_hand"):
            # A real placement that is not a hand placement: she has not picked it up
            # (or has already put it down again).  Reported as a mismatch, never as
            # "unreadable".
            unknowns.append(UNKNOWN_NOT_IN_HAND)
        else:
            unknowns.append("object_not_in_any_hand_and_not_visible")
    else:
        if held_slot is not None:
            # Placing never leaves the object in a hand: an honest mismatch.
            value = HAND_FACT % (object_id, held_slot)
            unknowns.append(UNKNOWN_STILL_HELD)
        elif placement is None:
            unknowns.append(UNKNOWN_OBJECT_NOT_VISIBLE)
        else:
            kind = str(placement.get("kind") or "")
            if kind == _KIND_SURFACE and placement.get("surface_id"):
                value = SURFACE_FACT % (object_id, str(placement["surface_id"]))
            elif kind == _KIND_CONTAINER and placement.get("container_id"):
                # Not encodable, so it can never equal the expected room/surface fact;
                # reported as what it really is.
                value = FACT_PREFIX + "%s:container:%s" % (
                    object_id, str(placement["container_id"]))
            elif kind == _KIND_ROOM:
                location = snapshot.get("location") or {}
                place_id = str(location.get("place_id") or "").strip()
                area_id = str(location.get("area_id") or "").strip()
                if place_id and area_id:
                    # The owner only commits a room placement into the body's *current*
                    # area (``NOT_CURRENT_AREA`` otherwise), so the body's own location
                    # is the bounded read of "which room".  Labelled, not asserted as a
                    # direct placement field.
                    value = ROOM_FACT % (object_id, place_id, area_id)
                    unknowns.append(UNKNOWN_ROOM_LOCATION)
                else:
                    unknowns.append("snapshot_location_unavailable")
            elif kind == _KIND_HAND:
                value = HAND_FACT % (object_id, str(placement.get("slot_id") or ""))
                unknowns.append(UNKNOWN_STILL_HELD)
            else:
                unknowns.append("placement_kind_unreadable")

    return {
        "fact_key": FACT_KEY_DAILY_PLACEMENT,
        "expected_value": expected,
        "value": value,
        "satisfied": bool(value is not None and value == expected),
        "source": source,
        "observed": observed,
        "unknowns": unknowns,
    }


__all__ = [
    "DAILY_ACTION_ACTIONS",
    "DAILY_ACTION_REF_PREFIX",
    "DAILY_ACTION_VERBS",
    "DailyActionTargetError",
    "EFFECT_PICKED_UP",
    "EFFECT_PLACED",
    "FACT_KEY_DAILY_PLACEMENT",
    "FACT_PREFIX",
    "HAND_FACT",
    "HAND_SLOTS",
    "MAX_FIELD",
    "PICK_UP_ACTION",
    "PICK_UP_PARAMS",
    "PICK_UP_VERB",
    "PLACEMENT_DESTINATION_KINDS",
    "PLACEMENT_ROOM",
    "PLACEMENT_SURFACE",
    "PLACE_ACTION",
    "PLACE_PARAMS",
    "PLACE_VERB",
    "ROOM_FACT",
    "SOURCE_SNAPSHOT",
    "SURFACE_FACT",
    "UNKNOWN_NOT_IN_HAND",
    "UNKNOWN_OBJECT_NOT_VISIBLE",
    "UNKNOWN_ROOM_LOCATION",
    "UNKNOWN_STILL_HELD",
    "build_daily_action_step",
    "daily_action_completion",
    "daily_action_effect_predicates",
    "daily_action_observed_fact",
    "encode_daily_action_target",
    "is_daily_action_target",
    "parse_daily_action_target",
]
