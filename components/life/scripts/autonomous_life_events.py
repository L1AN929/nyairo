#!/usr/bin/env python3
"""B8-1: the real event producers the wake runner was missing.

B7 built the runner's door (``note_event`` / ``wake_on_event``) and left it with no
caller.  This module is the caller.

Design, and why it is this small
--------------------------------
None of these owners publishes a subscribable event, and inventing a bus to carry
three signals would be the wrong trade.  Each feed therefore does what E09.1 already
permits: **a low-frequency read of a bounded owner state or change marker**, and it
keeps the identity of what it has already seen so that

* a repeat observation is not a new event,
* an already-consumed eligibility cannot re-trigger a continuation,
* an edit *we* made does not wake us again, and
* a revoked grant stops the read instead of silently returning stale content.

What each feed watches
----------------------
``RESUME_ELIGIBILITY_OPENED``  LR-4 ``WaitingCoordinator.list_eligibilities(only_open=True)``.
                               Only an eligibility that is *newly* OPEN is an event; one that
                               expired, was consumed or was superseded is simply absent from
                               the open set and can never fire again.
``ARTIFACT_CHANGED``           the grant-scoped work head, per artifact, through the same
                               read port the instance already uses.  A head we wrote
                               ourselves is recorded as ours, so our own edit is not an event.
``WORLD_OPPORTUNITY``          the *reachable set* the World actually offers.  Only a change
                               in it is an event; a repeated observation of the same place
                               and revision is not.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

EVENT_RESUME_ELIGIBILITY_OPENED = "RESUME_ELIGIBILITY_OPENED"
EVENT_ARTIFACT_CHANGED = "ARTIFACT_CHANGED"
EVENT_WORLD_OPPORTUNITY = "WORLD_OPPORTUNITY"

FEED_STATE_FILENAME = "autonomous_event_feeds.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class EventFeed:
    """Persisted per-feed memory of what has already been seen.

    The memory is deliberately tiny and lives beside the switch and the budget, so a
    restart recognises its own consumption position without a new store.
    """

    def __init__(self, state_root: Path | str, *, name: str) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / FEED_STATE_FILENAME
        self.name = str(name)

    def _all(self) -> dict[str, Any]:
        return _read_json(self.path)

    def load(self) -> dict[str, Any]:
        return dict(self._all().get(self.name) or {})

    def save(self, payload: Mapping[str, Any]) -> None:
        state = self._all()
        state[self.name] = dict(payload)
        _write_json(self.path, state)


class ResumeEligibilityFeed:
    """A newly OPEN LR-4 resume eligibility is a real reason to reconsider."""

    name = "resume_eligibility"

    def __init__(self, coordinator: Any, state_root: Path | str) -> None:
        self.coordinator = coordinator
        self.memory = EventFeed(state_root, name=self.name)

    def poll(self) -> list[dict[str, Any]]:
        try:
            open_rows = list(self.coordinator.list_eligibilities(only_open=True) or [])
        except Exception as exc:
            return [{"kind": "FEED_UNAVAILABLE", "ref": self.name,
                     "detail": "%s: %s" % (type(exc).__name__, exc)}]
        seen = set(self.memory.load().get("notified") or [])
        fresh: list[dict[str, Any]] = []
        for row in open_rows:
            eligibility_id = str(row.get("eligibility_id") or "")
            if not eligibility_id or eligibility_id in seen:
                continue
            seen.add(eligibility_id)
            fresh.append({
                "kind": EVENT_RESUME_ELIGIBILITY_OPENED,
                "ref": eligibility_id,
                "occurred_at": row.get("eligible_at") or row.get("created_at"),
                "detail": "activity=%s" % row.get("activity_id"),
            })
        # The set is authoritative: an eligibility that left the open set can never fire
        # again, so it is dropped rather than remembered forever.
        self.memory.save({"notified": sorted(
            eligibility_id for eligibility_id in seen
            if eligibility_id in {str(r.get("eligibility_id") or "") for r in open_rows})})
        return fresh


class ArtifactChangeFeed:
    """A head that moved without us is a real reason to reconsider.

    Scoped on purpose: only artifacts this subject can already read, through the port
    that enforces the grant.  A revoked grant makes the read refuse, and a refusal is
    reported as a feed error -- never as a change and never as content.
    """

    name = "artifact_head"

    def __init__(self, port: Any, state_root: Path | str, *, subject_id: Optional[str] = None,
                 owned_operation_prefix: str = "life:") -> None:
        self.port = port
        self.subject_id = subject_id
        self.owned_operation_prefix = str(owned_operation_prefix)
        self.memory = EventFeed(state_root, name=self.name)

    def poll(self) -> list[dict[str, Any]]:
        try:
            rows = list(self.port.list_artifacts() or [])
        except Exception as exc:
            return [{"kind": "FEED_UNAVAILABLE", "ref": self.name,
                     "detail": "%s: %s" % (type(exc).__name__, exc)}]
        known = dict(self.memory.load().get("heads") or {})
        fresh: list[dict[str, Any]] = []
        current: dict[str, str] = {}
        for row in rows:
            if str(row.get("lifecycle") or "ACTIVE") != "ACTIVE":
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            try:
                head = self.port.read_head(artifact_id) or {}
            except Exception as exc:
                # A refused read (revoked grant, unavailable owner) is a feed error: the
                # head is left exactly as it was and no content is taken.
                fresh.append({"kind": "FEED_UNAVAILABLE", "ref": artifact_id,
                              "detail": "%s: %s" % (type(exc).__name__, exc)})
                current[artifact_id] = known.get(artifact_id, "")
                continue
            head_id = str(head.get("version_id") or "")
            current[artifact_id] = head_id
            previous = known.get(artifact_id)
            if previous is None:
                # First sighting: remember it, do not wake.  An instance that has just
                # started must not treat everything it can already see as news.
                continue
            if head_id and head_id != previous:
                fresh.append({
                    "kind": EVENT_ARTIFACT_CHANGED,
                    "ref": "%s:%s" % (artifact_id, head_id),
                    "occurred_at": head.get("created_at"),
                    "detail": "head %s -> %s" % (previous, head_id),
                })
        self.memory.save({"heads": current})
        return fresh

    def note_own_write(self, artifact_id: str, head_version_id: str) -> None:
        """Record a head *we* wrote, so our own edit is not an event to ourselves."""

        state = self.memory.load()
        heads = dict(state.get("heads") or {})
        heads[str(artifact_id)] = str(head_version_id)
        self.memory.save({"heads": heads})


class WorldOpportunityFeed:
    """Only a change in what the World actually offers is an event."""

    name = "world_opportunity"

    def __init__(self, provider: Any, state_root: Path | str) -> None:
        self.provider = provider
        self.memory = EventFeed(state_root, name=self.name)

    def poll(self) -> list[dict[str, Any]]:
        try:
            opportunities = list(self.provider() or [])
        except Exception as exc:
            return [{"kind": "FEED_UNAVAILABLE", "ref": self.name,
                     "detail": "%s: %s" % (type(exc).__name__, exc)}]
        signature = sorted(
            "%s|%s" % (item.get("world_revision_ref"), ref)
            for item in opportunities for ref in (item.get("target_refs") or []))
        previous = self.memory.load().get("signature")
        self.memory.save({"signature": signature})
        if previous is None or list(previous) == signature:
            return []
        # The event's ``ref`` becomes AG-1's ``trigger_ref``, which must be a structured
        # reference (``<prefix>:<id>``, ``agency_decision_ag1._STRUCTURED_REF_RE``).  The old
        # spelling pasted the first ``wrev:...|place:...`` signature straight in, so every
        # reachable-set change was refused as free text and recorded as ``DECISION_FAILED`` --
        # a permanent "blocked" that was really an encoding bug in this feed.  The digest keeps
        # the identity (a different reachable set is a different event) and stays structured.
        digest = hashlib.sha256("\n".join(signature).encode("utf-8")).hexdigest()[:16]
        return [{"kind": EVENT_WORLD_OPPORTUNITY, "ref": "world:reachable:%s" % digest,
                 "detail": "reachable set changed"}]


class EventFeedPoller:
    """Runs the feeds and hands only the real, fresh events to the runner."""

    def __init__(self, feeds: Iterable[Any]) -> None:
        self.feeds = list(feeds)
        self.last_errors: list[dict[str, Any]] = []

    def poll(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        self.last_errors = []
        for feed in self.feeds:
            for item in feed.poll():
                if item.get("kind") == "FEED_UNAVAILABLE":
                    self.last_errors.append(item)
                    continue
                events.append(item)
        return events

    def wake(self, runner: Any, *, act: bool = True) -> list[Any]:
        """Hand each fresh event to the runner.  The runner's own dedupe still applies."""

        records = []
        for event in self.poll():
            records.append(runner.wake_on_event(event, act=act))
        return records


def feeds_from_environment(environment: Mapping[str, str], *, coordinator: Any = None,
                           artifact_port: Any = None, world_provider: Any = None,
                           state_root: Optional[Path | str] = None) -> EventFeedPoller:
    """Assemble whichever feeds this instance can actually support.

    A missing owner means a missing feed, never a guessed one: an instance with no
    Supply owner has no artifact feed, and that is the honest state of affairs.
    """

    root = Path(state_root or environment.get("CHIYO_LIFE_INSTALL_ROOT") or "./data/life") / "state"
    feeds: list[Any] = []
    if coordinator is not None:
        feeds.append(ResumeEligibilityFeed(coordinator, root))
    if artifact_port is not None:
        feeds.append(ArtifactChangeFeed(artifact_port, root,
                                        subject_id=environment.get("NYA02_SUBJECT")))
    if world_provider is not None:
        feeds.append(WorldOpportunityFeed(world_provider, root))
    return EventFeedPoller(feeds)


__all__ = [
    "ArtifactChangeFeed",
    "EVENT_ARTIFACT_CHANGED",
    "EVENT_RESUME_ELIGIBILITY_OPENED",
    "EVENT_WORLD_OPPORTUNITY",
    "EventFeed",
    "EventFeedPoller",
    "FEED_STATE_FILENAME",
    "ResumeEligibilityFeed",
    "WorldOpportunityFeed",
    "feeds_from_environment",
]
