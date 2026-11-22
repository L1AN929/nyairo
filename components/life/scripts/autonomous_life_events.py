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


def _resolve_day_root_from_feeds(feeds: Iterable[Any], explicit_day_root: Optional[Path | str]) -> Optional[Path]:
    if explicit_day_root is not None:
        return Path(explicit_day_root)
    for feed in feeds:
        if feed is None:
            continue
        mem = getattr(feed, "memory", None)
        mem_path = getattr(mem, "path", None)
        if isinstance(mem_path, Path):
            state_dir = mem_path.parent
            if state_dir.name == "state":
                return state_dir.parent
            return state_dir
    return None


def _build_default_wake_coalescer(
    *,
    hourly_wake_cap: Optional[int] = None,
    daily_wake_cap: Optional[int] = None,
    coalesce_window_seconds: Optional[int] = None,
) -> Any:
    try:
        import v02_runtime_governance_migration as v02_rgm

        kwargs: dict[str, Any] = {}
        if hourly_wake_cap is not None:
            kwargs["hourly_wake_cap"] = int(hourly_wake_cap)
        if daily_wake_cap is not None:
            kwargs["daily_wake_cap"] = int(daily_wake_cap)
        if coalesce_window_seconds is not None:
            kwargs["coalesce_window_seconds"] = int(coalesce_window_seconds)
        return v02_rgm.SemanticWakeCoalescer(**kwargs)
    except Exception:
        return None


def _resolve_runtime_migration_path(day_root: Optional[Path]) -> Optional[Path]:
    if day_root is None:
        return None
    root_p = Path(day_root)
    candidates = (
        root_p / "phone" / "v02_runtime_migration.json",
        root_p.parent / "phone" / "v02_runtime_migration.json",
        root_p / "state" / "v02_runtime_migration.json",
    )
    for cand in candidates:
        try:
            if not cand.is_symlink() and cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _sync_coalescer_governance_mode(coalescer: Any, day_root: Optional[Path]) -> None:
    if coalescer is None or day_root is None:
        return
    mig_path = _resolve_runtime_migration_path(day_root)
    if mig_path is None:
        return
    try:
        raw = json.loads(mig_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, Mapping):
        return
    mode_cfg = raw.get("mode_config")
    if isinstance(mode_cfg, Mapping) and isinstance(getattr(coalescer, "state", None), dict):
        target_cfg = coalescer.state.setdefault("mode_config", {})
        if isinstance(target_cfg, dict):
            for key in ("mode", "budget_scale_pct", "reason_cn", "dormant_since"):
                if key in mode_cfg:
                    target_cfg[key] = mode_cfg[key]


def _persist_coalescer_counts_to_runtime_migration(
    day_root: Optional[Path],
    decisions: list[dict[str, Any]],
) -> None:
    if not decisions or day_root is None:
        return
    mig_path = _resolve_runtime_migration_path(day_root)
    if mig_path is None:
        return
    try:
        import state_tx_guard as _tx
    except ImportError:
        from components.life.scripts import state_tx_guard as _tx  # type: ignore[no-redef]

    effective_delta = sum(1 for d in decisions if bool(d.get("effective_wake", True)))
    control_delta = sum(
        1 for d in decisions if bool(d.get("effective_wake", True)) and bool(d.get("is_control"))
    )
    control_bypass_delta = sum(
        1
        for d in decisions
        if bool(d.get("effective_wake", True))
        and (d.get("wake_disposition") == "CONTROL_IMMEDIATE_BYPASS" or bool(d.get("is_control")))
    )
    fg_bypass_delta = sum(
        1
        for d in decisions
        if bool(d.get("effective_wake", True))
        and d.get("wake_disposition") == "FOREGROUND_GUARANTEED"
    )
    coalesced_delta = sum(
        1
        for d in decisions
        if bool(d.get("coalesced")) or d.get("wake_disposition") == "COALESCED_SAME_FAMILY"
    )
    quota_delta = sum(1 for d in decisions if bool(d.get("quota_blocked")))

    try:
        with _tx.state_file_lock(mig_path):
            if mig_path.is_symlink() or not mig_path.is_file():
                return
            try:
                doc = json.loads(mig_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            if not isinstance(doc, dict):
                return
            router = doc.get("wake_router")
            if not isinstance(router, dict):
                router = {}
                doc["wake_router"] = router
            router["effective_wakes_total"] = int(router.get("effective_wakes_total") or 0) + effective_delta
            router["control_wakes_total"] = int(router.get("control_wakes_total") or 0) + control_delta
            router["control_lane_bypasses"] = int(router.get("control_lane_bypasses") or 0) + control_bypass_delta
            router["foreground_chat_bypasses"] = int(router.get("foreground_chat_bypasses") or 0) + fg_bypass_delta
            router["coalesced_saved_wakes"] = int(router.get("coalesced_saved_wakes") or 0) + coalesced_delta
            router["quota_blocked_count"] = int(router.get("quota_blocked_count") or 0) + quota_delta
            router["last_probe_result"] = dict(decisions[-1])
            _tx.atomic_write_json(mig_path, doc, sort_keys=True)
    except OSError:
        return


def _classify_event_for_coalescer(item: Mapping[str, Any]) -> tuple[str, str, bool, str]:
    kind = str(item.get("kind") or "").strip()
    ref = str(item.get("ref") or "").strip()
    detail = str(item.get("detail") or kind or "event_wake").strip()

    if (
        kind == EVENT_RESUME_ELIGIBILITY_OPENED
        or bool(item.get("is_control", False))
        or str(item.get("lane") or "").strip() == "control"
    ):
        family = str(item.get("family") or f"resume_eligibility:{ref or 'open'}")
        return family, detail, True, "control"

    lane = str(item.get("lane") or "background").strip() or "background"
    if kind == EVENT_ARTIFACT_CHANGED:
        artifact_id = ref.split(":", 1)[0] if ":" in ref else (ref or "unknown_artifact")
        family = str(item.get("family") or f"artifact_changed:{artifact_id}")
        return family, detail, False, lane

    if kind == EVENT_WORLD_OPPORTUNITY:
        family = str(item.get("family") or "world_opportunity")
        return family, detail, False, lane

    family = str(item.get("family") or (kind.lower() if kind else "ambient_generic"))
    return family, detail, False, lane


class EventFeedPoller:
    """Runs the feeds and hands only the real, fresh, coalesced events to the runner."""

    def __init__(
        self,
        feeds: Iterable[Any],
        *,
        coalescer: Any = None,
        enable_coalescer: bool = True,
        day_root: Optional[Path | str] = None,
        hourly_wake_cap: Optional[int] = None,
        daily_wake_cap: Optional[int] = None,
        coalesce_window_seconds: Optional[int] = None,
    ) -> None:
        self.feeds = list(feeds)
        self.enable_coalescer = bool(enable_coalescer)
        self.day_root: Optional[Path] = _resolve_day_root_from_feeds(self.feeds, day_root)
        self.coalescer: Any = (
            coalescer
            if coalescer is not None
            else (
                _build_default_wake_coalescer(
                    hourly_wake_cap=hourly_wake_cap,
                    daily_wake_cap=daily_wake_cap,
                    coalesce_window_seconds=coalesce_window_seconds,
                )
                if self.enable_coalescer
                else None
            )
        )
        self.last_errors: list[dict[str, Any]] = []
        self.last_coalesced_events: list[dict[str, Any]] = []
        self.last_routing_decisions: list[dict[str, Any]] = []

    def coalesce_events(
        self,
        raw_events: Iterable[Mapping[str, Any]],
        *,
        now_iso: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Deduplicate and backpressure wake events through SemanticWakeCoalescer."""
        items = [dict(ev) for ev in raw_events if isinstance(ev, Mapping)]
        self.last_coalesced_events = []
        self.last_routing_decisions = []
        if not self.enable_coalescer or self.coalescer is None:
            return items

        resolved_day_root = _resolve_day_root_from_feeds(self.feeds, self.day_root)
        _sync_coalescer_governance_mode(self.coalescer, resolved_day_root)

        admitted: list[dict[str, Any]] = []
        for item in items:
            family, summary_cn, is_control, lane = _classify_event_for_coalescer(item)
            ev_now_iso = item.get("now_iso") or now_iso
            if ev_now_iso is None:
                occ = item.get("occurred_at")
                if isinstance(occ, str) and occ.strip():
                    ev_now_iso = occ.strip()
            try:
                decision = self.coalescer.route_event(
                    family=family,
                    summary_cn=summary_cn,
                    is_control=is_control,
                    lane=lane,
                    now_iso=ev_now_iso,
                )
            except Exception:
                # If a caller supplied a non-ISO occurred_at, fall back to wall-clock routing.
                decision = self.coalescer.route_event(
                    family=family,
                    summary_cn=summary_cn,
                    is_control=is_control,
                    lane=lane,
                    now_iso=now_iso,
                )
            self.last_routing_decisions.append(decision)
            if bool(decision.get("effective_wake", True)):
                admitted.append(item)
            else:
                suppressed = dict(item)
                suppressed["wake_disposition"] = decision.get("wake_disposition")
                suppressed["wake_reason"] = decision.get("wake_reason")
                self.last_coalesced_events.append(suppressed)
        _persist_coalescer_counts_to_runtime_migration(
            resolved_day_root, self.last_routing_decisions
        )
        return admitted

    def poll(self, *, now_iso: Optional[str] = None) -> list[dict[str, Any]]:
        raw_events: list[dict[str, Any]] = []
        self.last_errors = []
        for feed in self.feeds:
            if feed is None:
                continue
            for item in feed.poll():
                if item.get("kind") == "FEED_UNAVAILABLE":
                    self.last_errors.append(item)
                    continue
                raw_events.append(item)
        return self.coalesce_events(raw_events, now_iso=now_iso)

    def wake(self, runner: Any, *, act: bool = True, now_iso: Optional[str] = None) -> list[Any]:
        """Hand each fresh, coalesced event to the runner.  The runner's own dedupe still applies."""

        records = []
        for event in self.poll(now_iso=now_iso):
            records.append(runner.wake_on_event(event, act=act))
        return records


def feeds_from_environment(environment: Mapping[str, str], *, coordinator: Any = None,
                           artifact_port: Any = None, world_provider: Any = None,
                           state_root: Optional[Path | str] = None) -> EventFeedPoller:
    """Assemble whichever feeds this instance can actually support.

    A missing owner means a missing feed, never a guessed one: an instance with no
    Supply owner has no artifact feed, and that is the honest state of affairs.
    """

    base_root = Path(state_root or environment.get("CHIYO_LIFE_INSTALL_ROOT") or "./data/life")
    root = base_root / "state"
    feeds: list[Any] = []
    if coordinator is not None:
        feeds.append(ResumeEligibilityFeed(coordinator, root))
    if artifact_port is not None:
        feeds.append(ArtifactChangeFeed(artifact_port, root,
                                        subject_id=environment.get("NYA02_SUBJECT")))
    if world_provider is not None:
        feeds.append(WorldOpportunityFeed(world_provider, root))
    return EventFeedPoller(feeds, day_root=base_root)


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
