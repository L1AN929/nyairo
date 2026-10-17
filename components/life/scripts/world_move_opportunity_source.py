#!/usr/bin/env python3
"""B6-5 / I: the observation -> observed-opportunity translation for real World actions.

AG-0 already declares ``CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY`` and already
owns ``WorldOpportunityCandidateSource``.  That source encodes one rule and one
rule only -- "World knows X != Chiyo knows X": a candidate may only come from a
World fact she actually perceived, carrying a perception reference and a
world-revision reference, and scanning the global World database to invent one is
explicitly forbidden.

What did not exist is the *translation*: nothing in the tree ever turned a real
gateway observation into those records (both composition roots construct the
source empty, and ``record_observed_opportunity`` had no caller).  This module is
that translation and nothing else.

It is deliberately dull:

* it reads one bounded observation plus the World's own availability truth;
* it emits one opportunity per action the World itself currently offers -- so the
  opportunity set and the choosable set are the same set, and an action the World
  does not offer cannot appear here;
* it never ranks, scores, recommends or picks.  AG-1 decides; this only reports
  what was observed.

Verbs translated (I adds the second one)
----------------------------------------
* ``MOVE`` -- one opportunity per reachable destination the selector-visible
  action space offers; the observed target is ``place:<location_id>``.
* ``CONSUME`` -- one opportunity per object the World gateway's own
  ``get_available_actions`` reports as consumable *right now* (F: that list is
  already the owner's verdict, computed from the live placement, the World's
  consumable declaration, the current place's opening state and the pose).  The
  observed target is ``object:<object_id>``.  This module never decides whether
  something is edible; it only reports what the owner already exposed.

The W catalog (``world_daily_opportunities.location_opportunity_catalog``) is read
as *facts about the places* -- walk seconds, opening state, whether she really sees
them -- and is attached to the richer, non-AG-0 view this module keeps for reports.
It is never a grant (every catalog entry is ``action_grant: false``) and never a
candidate list of its own: a place the World does not offer to move to still cannot
appear here.

Bounded observations only: the raw world revision travels inside the structured
references the World already uses for exactly-once bookkeeping, and the
selector-facing projection stays the one ``action_intent`` defines.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Optional

import candidate_sources_ag0 as ag0

#: How long one observation's opportunities stay valid.  Short on purpose: a
#: stale "the street is reachable" is exactly what requirement 5 forbids acting on.
DEFAULT_VALIDITY_SECONDS = 300

#: The verbs this translation understands as *World owner* verbs.  POSE needs a second
#: parameter (orientation) that a single observed target cannot supply, so it is not here.
MOVE_VERB = "MOVE"
CONSUME_VERB = "CONSUME"
#: I-r1: a non-productive stay is *not* a World verb.  No owner executes "stand here": the
#: World owner's part is only the observation that she really is in this place, plus the
#: opening/affordance facts the W catalog already projects.  ``STAY`` is therefore a
#: Life-side descriptor, and the ``stay:`` namespace below is what the Life core reads back
#: to know it must record an internal checkpoint instead of submitting a World verb.
STAY_VERB = "STAY"
STAY_REF_PREFIX = "stay:"
#: How long one stay lasts, by the affordance the place itself offers.  Fixed here -- not
#: derived from the model, a feeling, or the round budget -- so the same place + affordance
#: always declares the same stay, and the completion contract can name it in advance.
STAY_SECONDS_BY_AFFORDANCE = {
    "SIT": 300,
    "SIT_AT_CAFE": 300,
    "REST": 300,
    "READ_HERE": 600,
    "READ": 600,
    "LOOK_AROUND": 180,
    "WALK": 240,
    "USE_PHONE": 240,
    "BROWSE_BOOKS": 300,
}

#: The affinity order a stay prefers when a place offers several: a stationary, non-productive
#: option first (a bench, a reading table, a quiet pause), and walking last -- walking is not
#: staying, so it is only used when the place offers nothing else this table knows.
STAY_DESCRIPTOR_PREFERENCE = (
    "SIT", "SIT_AT_CAFE", "REST", "READ_HERE", "READ", "USE_PHONE",
    "BROWSE_BOOKS", "LOOK_AROUND", "WALK",
)
#: Used when the place offers no affordance this table knows: a quiet stop, which is legal
#: anywhere she really is and is never dressed up as one of the place's own affordances.
STAY_DESCRIPTOR_QUIET = "QUIET_STOP"
STAY_QUIET_SECONDS = 180

#: Target namespaces.  The prefix is what the Life core reads back to decide *which* owner
#: verb an adopted opportunity implies -- never the model, and never a guess.
PLACE_REF_PREFIX = "place:"
OBJECT_REF_PREFIX = "object:"


def _revision_of(observation: Mapping[str, Any]) -> Any:
    return observation.get("world_revision")


def _place_of(observation: Mapping[str, Any]) -> Optional[str]:
    location = observation.get("location") or {}
    place = location.get("place_id")
    return str(place) if place else None


def _validity_window(observed_at: str, valid_for_seconds: int) -> tuple[str, str]:
    valid_from = ag0._require_iso(observed_at, "observed_at")
    valid_until = ag0._require_iso(
        _plus_seconds(valid_from, valid_for_seconds), "valid_until"
    )
    return (valid_from, valid_until)


def observed_move_opportunities(
    observation: Mapping[str, Any],
    *,
    action_space: Mapping[str, Any],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per MOVE destination the observation supports.

    ``action_space`` must be the *selector-visible* projection (what a decision
    is actually allowed to choose from), not the raw gateway list: the point of
    the seam is that she can only consider what she could also pick.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None:
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")

    opportunities: list[dict[str, Any]] = []
    for candidate in action_space.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("verb") != MOVE_VERB:
            continue
        params = candidate.get("params") or {}
        destination = str(params.get("destination_location_id") or "").strip()
        if not destination:
            continue
        opportunities.append({
            "opportunity_id": "wopp:move:%s:r%s:%s" % (place, revision, destination),
            "perception_ref": "percept:gateway-snapshot:%s:r%s" % (place, revision),
            "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
            "target_refs": ["place:%s" % destination],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "occurred_at": occurred,
            "observed_at": valid_from,
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": ag0.GLOBAL_SUBJECT_ID,
            "location_ref": "place:%s" % place,
        })
    return opportunities


def observed_consume_opportunities(
    observation: Mapping[str, Any],
    *,
    available_actions: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per object the World itself says is consumable *now*.

    ``available_actions`` is the gateway's own ``get_available_actions`` payload.
    Its ``CONSUME`` entry carries the owner's availability verdict and the
    candidates it computed from the live substrate -- so a refused or unavailable
    entry produces no opportunity here, and nothing is inferred from a scene
    description.  An absent entry (an older gateway) is likewise no opportunity:
    "not exposed" is not "maybe available".
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(available_actions, Mapping):
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")

    opportunities: list[dict[str, Any]] = []
    for entry in available_actions.get("actions") or []:
        if not isinstance(entry, Mapping) or entry.get("verb") != CONSUME_VERB:
            continue
        if entry.get("available") is not True:
            continue
        for offered in entry.get("candidates") or []:
            if not isinstance(offered, Mapping):
                continue
            object_id = str(offered.get("object_id") or "").strip()
            if not object_id:
                continue
            opportunities.append({
                "opportunity_id": "wopp:consume:%s:r%s:%s" % (place, revision, object_id),
                "perception_ref": "percept:gateway-available-actions:%s:r%s"
                                  % (place, revision),
                "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
                "target_refs": ["%s%s" % (OBJECT_REF_PREFIX, object_id)],
                "valid_from": valid_from,
                "valid_until": valid_until,
                "occurred_at": occurred,
                "observed_at": valid_from,
                "observed": True,
                "status": "AVAILABLE",
                "revision": 1,
                "subject_id": ag0.GLOBAL_SUBJECT_ID,
                "location_ref": "place:%s" % place,
            })
    return opportunities
def stay_target(place: str, descriptor: str, seconds: int) -> str:
    """The one target spelling a stay's whole plan travels in.

    ``stay:<place>:<descriptor>:<seconds>`` -- so the Life core can rebuild the plan from the
    committed candidate, the Activity's own ``subject_refs`` and the checkpoint it recorded,
    without a second plan store and without asking a model what it meant.
    """

    return "%s%s:%s:%d" % (STAY_REF_PREFIX, str(place), str(descriptor), int(seconds))


def parse_stay_target(target: Any) -> Optional[dict[str, Any]]:
    """Split a ``stay:`` target back into its plan, or ``None`` when it is not one."""

    text = str(target or "").strip()
    if not text.startswith(STAY_REF_PREFIX):
        return None
    parts = text[len(STAY_REF_PREFIX):].split(":")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    try:
        seconds = int(parts[2])
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return {"place": parts[0], "descriptor": parts[1], "seconds": seconds,
            "target_ref": text}


def _stay_descriptor(affordances: Any) -> tuple[str, int]:
    """The descriptor + duration this place really offers, or the quiet-stop fallback.

    Only affordances the place itself lists are ever used, and among them the preference
    order picks the stationary, non-productive one first: the point of a stay is being
    somewhere, not producing anything, and never a walk dressed up as a stay.
    """

    offered = {str(item.get("affordance_id") or "").strip()
               for item in affordances or [] if isinstance(item, Mapping)}
    for affordance_id in STAY_DESCRIPTOR_PREFERENCE:
        if affordance_id in offered and affordance_id in STAY_SECONDS_BY_AFFORDANCE:
            return affordance_id, STAY_SECONDS_BY_AFFORDANCE[affordance_id]
    return STAY_DESCRIPTOR_QUIET, STAY_QUIET_SECONDS


def observed_stay_opportunities(
    observation: Mapping[str, Any],
    *,
    catalog: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One non-productive stay at the place she is really in, if that place is really open.

    What it is: a Life-side bounded stay whose only World-side inputs are (a) the canonical
    observation that she is in this place right now and (b) the W catalog's own opening /
    affordance facts for it.  What it is not: a World verb, a resting effect, a nutrition
    fact, or a claim that something was produced.

    Failure is closed: an unreadable catalog (or a place with no OPEN availability fact)
    yields no stay at all, so a place whose opening state is unknown never becomes somewhere
    she is invited to settle in for ten minutes.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(catalog, Mapping):
        return []
    if str(catalog.get("status") or "") != "available":
        return []
    entry: Mapping[str, Any] = {}
    for item in catalog.get("opportunities") or []:
        if isinstance(item, Mapping) and str(item.get("location_id")) == place:
            entry = item
            break
    if not entry:
        return []
    if str((entry.get("availability") or {}).get("status") or "") != "OPEN":
        return []

    descriptor, seconds = _stay_descriptor(entry.get("affordances"))
    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    return [{
        "opportunity_id": "wopp:stay:%s:r%s:%s" % (place, revision, descriptor),
        "perception_ref": "percept:gateway-snapshot:%s:r%s" % (place, revision),
        "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
        "target_refs": [stay_target(place, descriptor, seconds)],
        "valid_from": valid_from,
        "valid_until": valid_until,
        "occurred_at": occurred,
        "observed_at": valid_from,
        "observed": True,
        "status": "AVAILABLE",
        "revision": 1,
        "subject_id": ag0.GLOBAL_SUBJECT_ID,
        "location_ref": "place:%s" % place,
    }]


def read_location_catalog(
    environment: Optional[Mapping[str, str]],
    now: Any,
    *,
    hermes_home: Optional[str],
) -> dict[str, Any]:
    """The W opportunity catalog, as facts about places -- or an honest absence.

    Read-only and failure tolerant by design: an unreadable catalog means "no place
    facts this round", never a guessed availability.  The catalog is never a grant;
    every entry it returns carries ``action_grant: false`` of its own.
    """

    try:
        import world_daily_opportunities as wdo
    except Exception as error:  # noqa: BLE001 - a missing module is not an opportunity
        return {"status": "unavailable", "reason_code": "CATALOG_MODULE_UNAVAILABLE",
                "detail": "%s: %s" % (type(error).__name__, error), "opportunities": []}
    try:
        catalog = wdo.location_opportunity_catalog(
            environment, now, hermes_home=hermes_home)
    except Exception as error:  # noqa: BLE001 - same rule: report, never invent
        return {"status": "unavailable", "reason_code": "CATALOG_READ_FAILED",
                "detail": "%s: %s" % (type(error).__name__, error), "opportunities": []}
    return dict(catalog) if isinstance(catalog, Mapping) else {
        "status": "unavailable", "reason_code": "CATALOG_SHAPE_INVALID",
        "opportunities": []}


def opportunity_view(
    opportunities: list[dict[str, Any]],
    catalog: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """A richer, non-AG-0 view of the same opportunities, for reports and steps.

    AG-0's ``ObservedWorldOpportunity`` accepts exactly its declared fields, so the
    extra facts the Life core wants (which owner verb, how far, is the place really
    open, is the place fact uncertain) are kept *here* instead of being smuggled
    into the candidate contract.  Nothing in this view authorizes anything.
    """

    by_location: dict[str, Mapping[str, Any]] = {}
    for item in (catalog or {}).get("opportunities") or []:
        if isinstance(item, Mapping) and item.get("location_id"):
            by_location[str(item["location_id"])] = item

    view: list[dict[str, Any]] = []
    for item in opportunities or []:
        if not isinstance(item, Mapping):
            continue
        targets = [str(ref) for ref in (item.get("target_refs") or [])]
        target = targets[0] if targets else ""
        stay = parse_stay_target(target)
        affordance = None
        stay_seconds = None
        if stay is not None:
            kind = STAY_VERB
            object_id = None
            affordance = stay["descriptor"]
            stay_seconds = stay["seconds"]
            source = "life_daily_activity_from_world_observation"
            location = stay["place"]
        elif target.startswith(OBJECT_REF_PREFIX):
            kind = CONSUME_VERB
            object_id = target[len(OBJECT_REF_PREFIX):]
            source = "world_gateway_available_actions"
            location = str(item.get("location_ref") or "")[len(PLACE_REF_PREFIX):]
        elif target.startswith(PLACE_REF_PREFIX):
            kind = MOVE_VERB
            object_id = None
            source = "world_gateway_selector_action_space"
            location = target[len(PLACE_REF_PREFIX):]
        else:  # pragma: no cover - the three emitters above only produce these
            continue
        place_fact = by_location.get(location) or {}
        availability = (place_fact.get("availability") or {}) if place_fact else {}
        reachability = (place_fact.get("reachability") or {}) if place_fact else {}
        view.append({
            "opportunity_id": item.get("opportunity_id"),
            "kind": kind,
            "verb": kind,
            "target_ref": target,
            "object_id": object_id,
            "affordance": affordance,
            "stay_seconds": stay_seconds,
            "location_id": location,
            "subject_id": item.get("subject_id"),
            "source": source,
            "perception_ref": item.get("perception_ref"),
            "world_revision_ref": item.get("world_revision_ref"),
            "observed": bool(item.get("observed")),
            "valid_from": item.get("valid_from"),
            "valid_until": item.get("valid_until"),
            "occurred_at": item.get("occurred_at"),
            # ``UNKNOWN`` here means the owner's own rhythm read failed; it is reported
            # as uncertainty and never upgraded to "open" or "closed".
            "availability": availability.get("status") or "NOT_READ",
            "availability_reason": availability.get("reason_code"),
            "walk_seconds": reachability.get("walk_seconds"),
            "place_observed": (place_fact.get("observed") or {}).get("status")
                              if place_fact else None,
            "action_grant": False,
        })
    return view


def _plus_seconds(iso_text: str, seconds: int) -> str:
    from datetime import datetime, timezone

    moment = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment + timedelta(seconds=int(seconds))).astimezone(timezone.utc).isoformat()


class WorldMoveOpportunityProvider:
    """Read one real observation through the World gateway, then translate it.

    The read uses the same ``WorldBodyClient`` the AR-0 adapter submits through,
    so observation and action share one transport and one identity.  Anything
    unreadable yields an empty list: no observation means no opportunity, never a
    guessed one.
    """

    def __init__(
        self,
        *,
        client: Any,
        action_intent: Any,
        bridge_dir: Optional[str] = None,
        valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
        catalog_reader: Any = None,
        environment: Optional[Mapping[str, str]] = None,
        world_home: Optional[str] = None,
        now: Any = None,
        observed_now: Any = None,
    ) -> None:
        self.client = client
        self.action_intent = action_intent
        self.bridge_dir = bridge_dir
        self.valid_for_seconds = int(valid_for_seconds)
        #: Injected for tests; ``read_location_catalog`` by default.  Called with
        #: ``(environment, now, hermes_home=...)``.
        self.catalog_reader = catalog_reader or read_location_catalog
        self.environment = dict(environment) if environment is not None else None
        self.world_home = world_home
        self.now = now
        #: The instant *this observation* is taken at, when it must differ from the instant the
        #: schedule is read at.  ``None`` keeps them the same, which is the production shape.
        self.observed_now = observed_now
        self.last_observation: Optional[dict[str, Any]] = None
        self.last_action_space: Optional[dict[str, Any]] = None
        self.last_available_actions: Optional[dict[str, Any]] = None
        self.last_catalog: Optional[dict[str, Any]] = None
        self.last_opportunity_view: list[dict[str, Any]] = []
        self.last_reason: Optional[str] = None

    def __call__(self) -> list[dict[str, Any]]:
        return self.observe()

    def observe(self, *, occurred_at: Optional[str] = None) -> list[dict[str, Any]]:
        from datetime import datetime, timezone

        wbc = self._world_body_client()
        status = self.client.get_status()
        if status.outcome != wbc.OK or not (status.data or {}).get("ready"):
            self.last_reason = "runtime_not_ready:%s" % status.outcome
            return []
        snapshot = self.client.get_snapshot()
        if snapshot.outcome != wbc.OK or not isinstance(snapshot.data, dict):
            self.last_reason = "snapshot_unavailable:%s" % snapshot.outcome
            return []
        available = self.client.get_available_actions()
        available_actions = (
            available.data
            if available.outcome == wbc.OK and isinstance(available.data, dict)
            else None
        )
        space = self.action_intent.build_action_space(
            snapshot.data, available_actions=available_actions
        )
        visible = self.action_intent.redact_action_space_for_selector(space)
        self.last_observation = dict(snapshot.data)
        self.last_action_space = visible
        self.last_available_actions = dict(available_actions) if available_actions else None
        moment = occurred_at or self._observation_moment_iso()
        # The place facts are read *before* the stay is offered: a stay is only legal where
        # the owner's own opening fact says the place is open, so it cannot be constructed
        # from a catalog read that has not happened yet.
        self.last_catalog = self._catalog()
        move = observed_move_opportunities(
            snapshot.data,
            action_space=visible,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        consume = observed_consume_opportunities(
            snapshot.data,
            available_actions=available_actions,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        stay = observed_stay_opportunities(
            snapshot.data,
            catalog=self.last_catalog,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        self.last_opportunity_view = opportunity_view(move + consume + stay, self.last_catalog)
        self.last_reason = None
        return move + consume + stay

    # -- plumbing ----------------------------------------------------------

    def _observation_moment_iso(self) -> str:
        """The instant this observation is taken at (ISO), as the caller's own clock says.

        ``observed_now`` exists because the two instants are genuinely different in a
        rehearsal that pins the schedule (so "the shop is closed" is deterministic) while the
        Life clock is what every round and every stay is measured on.  With ``observed_now``
        unset the two instants are the same, which is the production shape.
        """

        if self.observed_now is None:
            return self._moment_iso()
        value = self.observed_now() if callable(self.observed_now) else self.observed_now
        if hasattr(value, "isoformat") and not isinstance(value, str):
            return value.isoformat()
        return str(value)

    def _moment(self) -> Any:
        """The instant this observation is taken at, as a real datetime when possible.

        The W catalog takes an aware ``datetime`` (its own ``_instant`` refuses a string),
        so the same instant the opportunity ids carry is handed to it as an object, and
        only the ids use the ISO spelling.
        """
        from datetime import datetime, timezone

        if self.now is not None:
            value = self.now() if callable(self.now) else self.now
            if hasattr(value, "isoformat") and not isinstance(value, str):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.now(timezone.utc)

    def _moment_iso(self) -> str:
        value = self._moment()
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def _catalog(self) -> Optional[dict[str, Any]]:
        """Read the place facts.  A reader that raises is reported, never guessed."""

        moment = self._moment()
        try:
            catalog = self.catalog_reader(self.environment, moment,
                                          hermes_home=self.world_home)
        except TypeError:
            # A reader that does not take ``hermes_home`` (older test doubles).
            try:
                catalog = self.catalog_reader(self.environment, moment)
            except Exception as error:  # noqa: BLE001
                self.last_reason = "catalog_read_failed:%s" % type(error).__name__
                return None
        except Exception as error:  # noqa: BLE001
            self.last_reason = "catalog_read_failed:%s" % type(error).__name__
            return None
        return dict(catalog) if isinstance(catalog, Mapping) else None

    def _world_body_client(self) -> Any:
        if self.bridge_dir:
            import sys

            if self.bridge_dir not in sys.path:
                sys.path.insert(0, self.bridge_dir)
        import world_body_client as wbc

        return wbc


__all__ = [
    "CONSUME_VERB",
    "DEFAULT_VALIDITY_SECONDS",
    "MOVE_VERB",
    "OBJECT_REF_PREFIX",
    "PLACE_REF_PREFIX",
    "STAY_DESCRIPTOR_QUIET",
    "STAY_QUIET_SECONDS",
    "STAY_REF_PREFIX",
    "STAY_SECONDS_BY_AFFORDANCE",
    "STAY_VERB",
    "WorldMoveOpportunityProvider",
    "observed_consume_opportunities",
    "observed_move_opportunities",
    "observed_stay_opportunities",
    "opportunity_view",
    "parse_stay_target",
    "read_location_catalog",
    "stay_target",
]
