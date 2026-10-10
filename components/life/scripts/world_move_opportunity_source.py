#!/usr/bin/env python3
"""B6-5: the observation -> observed-opportunity translation for a real MOVE.

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
* it emits one opportunity per reachable destination that the *selector-visible*
  action space already offers -- so the opportunity set and the choosable set are
  the same set, and a destination the World does not offer cannot appear here;
* it never ranks, scores, recommends or picks.  AG-1 decides; this only reports
  what was observed.

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

#: The verb this translation understands.  POSE needs a second parameter
#: (orientation) that a single observed target cannot supply.
MOVE_VERB = "MOVE"


def _revision_of(observation: Mapping[str, Any]) -> Any:
    return observation.get("world_revision")


def _place_of(observation: Mapping[str, Any]) -> Optional[str]:
    location = observation.get("location") or {}
    place = location.get("place_id")
    return str(place) if place else None


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

    valid_from = ag0._require_iso(observed_at, "observed_at")
    valid_until = ag0._require_iso(
        _plus_seconds(valid_from, valid_for_seconds), "valid_until"
    )
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
    ) -> None:
        self.client = client
        self.action_intent = action_intent
        self.bridge_dir = bridge_dir
        self.valid_for_seconds = int(valid_for_seconds)
        self.last_observation: Optional[dict[str, Any]] = None
        self.last_action_space: Optional[dict[str, Any]] = None
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
        moment = occurred_at or datetime.now(timezone.utc).isoformat()
        return observed_move_opportunities(
            snapshot.data,
            action_space=visible,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )

    def _world_body_client(self) -> Any:
        if self.bridge_dir:
            import sys

            if self.bridge_dir not in sys.path:
                sys.path.insert(0, self.bridge_dir)
        import world_body_client as wbc

        return wbc


__all__ = [
    "DEFAULT_VALIDITY_SECONDS",
    "MOVE_VERB",
    "WorldMoveOpportunityProvider",
    "observed_move_opportunities",
]
