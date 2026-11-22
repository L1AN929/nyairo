#!/usr/bin/env python3
"""S02 health projection — joins the live body runtime to the health machine.

``body_runtime`` owns the somatic variables (fatigue among them) and the
recovery state machine.  ``body_health`` owns the illness course and the
capability envelope.  This module is the one join between them:

    body.runtime.v2 state + persisted health record + attestation
        -> HealthSnapshot (status, episode, fatigue)
        -> CapabilityEnvelope (what S04 may still attempt)

Nothing here writes.  ``advance_record`` returns a *new* record for the caller
to persist; ``project`` only reads.  No clock, no randomness, no LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

try:  # package import (tests, tools)
    from components.world.scripts.body_health import (
        ACUTE_ENTER,
        ILLNESS_ORIGINS,
        FatigueAccumulator,
        HealthSnapshot,
        HealthStatus,
        IllnessEpisode,
        CapabilityEnvelope,
        accumulate_fatigue,
        advance_episode,
        close_episode,
        derive_envelope,
        episode_closed,
        open_episode,
        select_status,
    )
except ImportError:  # ``components/world/scripts`` is on sys.path (service)
    from body_health import (  # type: ignore[no-redef]
        ACUTE_ENTER,
        ILLNESS_ORIGINS,
        FatigueAccumulator,
        HealthSnapshot,
        HealthStatus,
        IllnessEpisode,
        CapabilityEnvelope,
        accumulate_fatigue,
        advance_episode,
        close_episode,
        derive_envelope,
        episode_closed,
        open_episode,
        select_status,
    )

try:  # package import (tests, tools)
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # ``components/world/scripts`` is on sys.path (service)
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:  # package import
    from components.world.scripts import body_lived_needs as _bln
except ImportError:
    try:
        import body_lived_needs as _bln  # type: ignore[no-redef]
    except ImportError:
        _bln = None  # type: ignore[assignment]

SCHEMA_RECORD = "body.health_record.v1"


@dataclass(frozen=True, slots=True)
class HealthRecord:
    """The small, persisted part that is not already in body_runtime.

    ``status_authority`` says who decided ``status``.  ``"derived"`` means the
    projection recomputes it from fatigue/severity; ``"v03_illness"`` means the
    real v03 course owns the phase and the generic projection must not rewrite
    it (a prodrome is STRAINED, never a made-up convalescence).
    ``source_reason`` is a named gap (e.g. ``ILLNESS_EPISODE_UNSOURCED``) the
    read surfaces can report instead of guessing.
    """

    body_id: str
    subject_id: str
    home_ref: str
    revision: int
    health_since: datetime
    status: HealthStatus = HealthStatus.UNATTESTED
    fatigue: FatigueAccumulator = FatigueAccumulator()
    episode: Optional[IllnessEpisode] = None
    status_authority: str = "derived"
    source_reason: Optional[str] = None
    schema_version: str = SCHEMA_RECORD


def initial_record(
    *,
    body_id: str,
    subject_id: str,
    home_ref: str,
    at: datetime,
) -> HealthRecord:
    """A fresh record starts UNATTESTED: nothing has been observed yet."""

    return HealthRecord(
        body_id=body_id,
        subject_id=subject_id,
        home_ref=home_ref,
        revision=0,
        health_since=at,
        status=HealthStatus.UNATTESTED,
    )


def _hours_between(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _somatic_fatigue(runtime_state: Mapping[str, Any]) -> Optional[float]:
    somatic = runtime_state.get("somatic")
    if not isinstance(somatic, Mapping):
        return None
    value = somatic.get("fatigue")
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def advance_record(
    record: HealthRecord,
    *,
    at: datetime,
    hours: float,
    load_hours: float = 0.0,
    exposure: float = 0.0,
    mode: Literal["rest", "sleep_like", "none"] = "none",
    rested: bool = False,
) -> HealthRecord:
    """Advance fatigue and the open episode by ``hours``.

    The caller decides whether the interval really carried load; an activity
    label never becomes a body fact on its own.
    """

    fatigue = accumulate_fatigue(
        record.fatigue,
        load_hours=load_hours,
        hours=hours,
        recovery_multiplier=1.0 if rested else 0.9,
        rested=rested,
    )
    episode = record.episode
    if episode is not None and episode.open:
        episode = advance_episode(episode, at=at, hours=hours, exposure=exposure, mode=mode)

    hours_out = 0.0
    if episode is not None and episode.left_acute_at is not None:
        hours_out = _hours_between(episode.left_acute_at, at)

    status = select_status(
        attested=True,
        previous=record.status,
        severity=episode.severity if episode else 0.0,
        fatigue=fatigue,
        hours_out_of_acute=hours_out,
        episode_open=bool(episode and episode.open),
    )

    if episode is not None and episode.open and episode_closed(
        severity=episode.severity, hours_out_of_acute=hours_out, fatigue=fatigue
    ):
        episode = close_episode(episode, at=at)

    return replace(
        record,
        revision=record.revision + 1,
        fatigue=fatigue,
        episode=episode,
        status=status,
        status_authority="derived",
        source_reason=None,
        health_since=at if status != record.status else record.health_since,
    )


def project(
    record: HealthRecord,
    runtime_state: Optional[Mapping[str, Any]],
    *,
    at: datetime,
    attested: Optional[bool] = None,
) -> tuple[HealthSnapshot, CapabilityEnvelope]:
    """Read-only view: runtime fatigue + record -> snapshot and envelope.

    ``attested`` defaults to "the runtime has a fresh somatic signal, or the
    record has really been advanced".  A runtime state that carries no somatic
    block downgrades it.  A body with no current signal is never reported
    HEALTHY, even if an old file says so: only an active course or a persisted
    strain may stand on the record alone.
    """

    runtime_fatigue = _somatic_fatigue(runtime_state) if runtime_state else None
    fatigue = record.fatigue
    if runtime_fatigue is not None:
        fatigue = replace(fatigue, acute=runtime_fatigue)

    if attested is None:
        is_attested = record.revision > 0 or runtime_fatigue is not None
    else:
        is_attested = attested
    if runtime_state is not None and runtime_fatigue is None:
        is_attested = False

    episode = record.episode
    hours_out = 0.0
    if episode is not None and episode.left_acute_at is not None:
        hours_out = _hours_between(episode.left_acute_at, at)

    if record.status_authority == "v03_illness" and record.status in (
        HealthStatus.STRAINED,
        HealthStatus.ACUTE_ILLNESS,
        HealthStatus.CONVALESCENT,
    ):
        # The real v03 course owns the phase.  The generic hysteresis must not
        # turn a prodrome into CONVALESCENT or a convalescence into HEALTHY.
        status = record.status
    elif record.status_authority == "v03_illness" and record.source_reason:
        # A named gap in the course (e.g. an onset with no source instant)
        # stays a named gap; a healthy runtime must not paper over it.
        status = HealthStatus.UNATTESTED
    else:
        status = select_status(
            attested=is_attested,
            previous=record.status,
            severity=episode.severity if episode else 0.0,
            fatigue=fatigue,
            hours_out_of_acute=hours_out,
            episode_open=bool(episode and episode.open),
        )
    if status is HealthStatus.HEALTHY and runtime_fatigue is None:
        # No current body signal is not health.  A persisted/derived "healthy"
        # cannot outlive the evidence it was based on.
        status = HealthStatus.UNATTESTED

    snapshot = HealthSnapshot(
        body_id=record.body_id,
        subject_id=record.subject_id,
        home_ref=record.home_ref,
        revision=record.revision,
        observed_at=at,
        attested=is_attested,
        status=status,
        health_since=record.health_since,
        fatigue=fatigue,
        episode=episode,
        hours_out_of_acute=hours_out,
        source_reason=record.source_reason,
    )
    return snapshot, derive_envelope(snapshot)


def admit_exposure(
    record: HealthRecord,
    *,
    episode_id: str,
    at: datetime,
    origin: str,
    severity: float,
) -> HealthRecord:
    """Open a course.  The origin allowlist lives in ``body_health``."""

    if record.episode is not None and record.episode.open:
        return record
    episode = open_episode(episode_id, opened_at=at, origin=origin, severity=severity)
    return replace(
        record,
        revision=record.revision + 1,
        episode=episode,
        status=HealthStatus.ACUTE_ILLNESS if severity >= ACUTE_ENTER else record.status,
        health_since=at,
    )


def _parse_source_instant(value: Any, fallback_tz: Optional[Any]) -> Optional[datetime]:
    """Parse a source-owned instant without ever substituting the read time."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None and fallback_tz is not None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed


def record_from_illness(
    illness: Mapping[str, Any],
    *,
    body_id: str,
    subject_id: str,
    home_ref: str,
    at: datetime,
) -> HealthRecord:
    """Mirror the v03 illness course (rain / late nights) as a health record.

    ``v03_economy_illness_bridge`` already owns the multi-day curve and the
    real exposure events; this is *not* a second course.  It reads that state
    and speaks it in this module's vocabulary so the capacity envelope can see
    it.  No write happens here and no severity is invented.

    Identity and time come from the v03 file itself (``episode_id``,
    ``episode_opened_at`` / ``acute_entered_at`` / ``phase_entered_at``), never
    from the moment of this read, so two GETs of one course yield the same
    episode.  A disease phase with no source instant is reported as a named
    unknown instead of pretending the first observation is the onset.
    """

    phase = str(illness.get("phase") or "healthy")
    try:
        severity = float(illness.get("severity") or 0.0)
    except (TypeError, ValueError):
        severity = 0.0

    base = initial_record(body_id=body_id, subject_id=subject_id, home_ref=home_ref, at=at)
    if phase not in ("prodrome", "acute_illness", "convalescence"):
        return replace(base, revision=1, status=HealthStatus.HEALTHY)

    if phase == "prodrome":
        # A prodrome is the v03 owner's STRAINED phase, not an acute course.
        # Opening an episode here would let the generic hysteresis relabel it
        # CONVALESCENT simply because 0.20 >= RECOVERED_SEVERITY.
        return replace(
            base, revision=1, status=HealthStatus.STRAINED,
            status_authority="v03_illness",
        )

    opened_at = _parse_source_instant(
        illness.get("episode_opened_at")
        or illness.get("acute_entered_at")
        or illness.get("phase_entered_at"),
        at.tzinfo,
    )
    if opened_at is None:
        return replace(
            base, revision=1, status=HealthStatus.UNATTESTED,
            status_authority="v03_illness",
            source_reason="ILLNESS_EPISODE_UNSOURCED",
        )

    episode_id = str(illness.get("episode_id") or "").strip()
    if not episode_id:
        # Identity derived from the source-owned onset is stable across reads;
        # it is not a new course and not the observation time.
        episode_id = "v03:%s" % opened_at.isoformat()

    floor = 0.36 if phase == "acute_illness" else 0.20
    episode = open_episode(
        episode_id,
        opened_at=opened_at,
        origin="world_exposure",
        severity=max(severity, floor),
    )
    if phase == "convalescence":
        left_acute = _parse_source_instant(
            illness.get("phase_entered_at"), at.tzinfo
        ) or opened_at
        episode = replace(episode, left_acute_at=left_acute)
    status = {
        "acute_illness": HealthStatus.ACUTE_ILLNESS,
        "convalescence": HealthStatus.CONVALESCENT,
    }[phase]
    return replace(
        base, revision=1, status=status, episode=episode,
        health_since=opened_at, status_authority="v03_illness",
    )


def stray_seconds(record: HealthRecord, at: datetime) -> Optional[float]:
    """How stale the record is; None when it has never been advanced."""

    if record.revision == 0:
        return None
    return max(0.0, (at - record.health_since).total_seconds())


# --------------------------------------------------------------------------
# persistence


HEALTH_FILE_NAME = "health.json"


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse_instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 string")
    return datetime.fromisoformat(value)


def record_to_dict(record: HealthRecord) -> dict[str, Any]:
    episode = None
    if record.episode is not None:
        episode = {
            "episode_id": record.episode.episode_id,
            "opened_at": _iso(record.episode.opened_at),
            "origin": record.episode.origin,
            "severity": record.episode.severity,
            "peak": record.episode.peak,
            "left_acute_at": _iso(record.episode.left_acute_at)
            if record.episode.left_acute_at
            else None,
            "closed_at": _iso(record.episode.closed_at) if record.episode.closed_at else None,
        }
    return {
        "schema_version": SCHEMA_RECORD,
        "body_id": record.body_id,
        "subject_id": record.subject_id,
        "home_ref": record.home_ref,
        "revision": record.revision,
        "health_since": _iso(record.health_since),
        "status": record.status.value,
        "status_authority": record.status_authority,
        "source_reason": record.source_reason,
        "fatigue": {
            "acute": record.fatigue.acute,
            "chronic": record.fatigue.chronic,
            "debt": record.fatigue.debt,
        },
        "episode": episode,
    }


def record_from_dict(raw: Any) -> HealthRecord:
    """Strict read.  An unknown status or a broken episode is refused, never
    silently reset to HEALTHY."""

    if not isinstance(raw, Mapping):
        raise ValueError("health record must be an object")
    if raw.get("schema_version") != SCHEMA_RECORD:
        raise ValueError(f"unsupported health record schema: {raw.get('schema_version')!r}")
    try:
        status = HealthStatus(raw.get("status"))
    except ValueError as exc:
        raise ValueError(f"unknown health status: {raw.get('status')!r}") from exc
    fatigue_raw = raw.get("fatigue") or {}
    if not isinstance(fatigue_raw, Mapping):
        raise ValueError("fatigue must be an object")
    fatigue = FatigueAccumulator(
        acute=float(fatigue_raw.get("acute", 0.0)),
        chronic=float(fatigue_raw.get("chronic", 0.0)),
        debt=float(fatigue_raw.get("debt", 0.0)),
    )
    episode = None
    episode_raw = raw.get("episode")
    if episode_raw is not None:
        if not isinstance(episode_raw, Mapping):
            raise ValueError("episode must be an object")
        if episode_raw.get("origin") not in ILLNESS_ORIGINS:
            raise ValueError(f"unknown episode origin: {episode_raw.get('origin')!r}")
        episode = IllnessEpisode(
            episode_id=str(episode_raw["episode_id"]),
            opened_at=_parse_instant(episode_raw.get("opened_at"), "opened_at"),
            origin=str(episode_raw["origin"]),
            severity=float(episode_raw.get("severity", 0.0)),
            peak=float(episode_raw.get("peak", 0.0)),
            left_acute_at=(
                _parse_instant(episode_raw["left_acute_at"], "left_acute_at")
                if episode_raw.get("left_acute_at")
                else None
            ),
            closed_at=(
                _parse_instant(episode_raw["closed_at"], "closed_at")
                if episode_raw.get("closed_at")
                else None
            ),
        )
    return HealthRecord(
        body_id=str(raw["body_id"]),
        subject_id=str(raw.get("subject_id") or ""),
        home_ref=str(raw.get("home_ref") or ""),
        revision=int(raw.get("revision") or 0),
        health_since=_parse_instant(raw.get("health_since"), "health_since"),
        status=status,
        fatigue=fatigue.clamped(),
        episode=episode,
        status_authority=str(raw.get("status_authority") or "derived"),
        source_reason=(
            str(raw["source_reason"]) if raw.get("source_reason") else None
        ),
    )


class HealthStore:
    """Atomic, single-file persistence for the health record."""

    def __init__(self, root: Any):
        from pathlib import Path

        self.root = Path(root)
        self.path = self.root / HEALTH_FILE_NAME

    def load(self) -> Optional[HealthRecord]:
        import json

        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("health record is not readable JSON") from exc
        return record_from_dict(raw)

    def save(self, record: HealthRecord) -> HealthRecord:
        import json
        import os
        import tempfile

        payload = record_to_dict(record)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".health.", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return record


__all__ = [
    "HealthRecord",
    "SCHEMA_RECORD",
    "admit_exposure",
    "advance_record",
    "initial_record",
    "project",
    "stray_seconds",
]

#: How far above ``world_home`` to look for the instance root that owns
#: ``phone/v03_ecology.json`` (``<day_root>/one-day/world-home`` is two levels).
V03_ROOT_SEARCH_DEPTH = 4


def read_v03_illness(world_home: Any) -> Optional[Mapping[str, Any]]:
    """The v03 illness block from the instance root above ``world_home`` (zero-write)."""

    if world_home is None:
        return None
    candidates: list[Path] = []
    try:
        paths = _instance_paths.resolve_instance_paths(world_home)
        candidates.append(paths.day_root / "phone" / "v03_ecology.json")
    except Exception:
        pass
    try:
        current = Path(world_home).resolve()
        for _ in range(V03_ROOT_SEARCH_DEPTH + 1):
            candidates.append(current / "phone" / "v03_ecology.json")
            if current.parent == current:
                break
            current = current.parent
    except (OSError, RuntimeError):
        pass
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file() and not candidate.is_symlink():
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            else:
                raw = None
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, Mapping):
            illness = raw.get("illness")
            if isinstance(illness, Mapping):
                return illness
    return None


SCHEMA_PHONE_HEALTH = "phone.body_health.v1"


def _severity_band(value: float) -> str:
    return "mild" if value < 0.34 else "noticeable" if value < 0.67 else "strong"


def _thermal_band_from_runtime(runtime_state: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Qualitative thermal band from ``runtime.json`` warmth (zero raw numbers)."""
    if not isinstance(runtime_state, Mapping):
        return None
    somatic = runtime_state.get("somatic")
    if not isinstance(somatic, Mapping):
        return None
    warmth = somatic.get("warmth")
    if isinstance(warmth, bool) or not isinstance(warmth, (int, float)):
        return None
    val = max(0.0, min(1.0, float(warmth)))
    if val <= 0.32:
        return "cold_tendency"
    if val >= 0.68:
        return "warmth_high"
    return "neutral"


def _lived_needs_bands_from_runtime(
    runtime_state: Optional[Mapping[str, Any]],
) -> Optional[dict[str, str]]:
    """Qualitative lived-needs bands from ``runtime.json`` (zero raw numbers)."""
    if not isinstance(runtime_state, Mapping) or _bln is None:
        return None
    try:
        bands = _bln.bands(runtime_state)
        return dict(bands) if isinstance(bands, Mapping) else None
    except Exception:
        return None


def _read_runtime_state(world_home: Any) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
    """The persisted body runtime, or a named reason it could not be read.

    This is the same file the World/Body owner writes; the phone process never
    invents a signal when it is missing.  A corrupt file is *not* an empty body.
    """

    if world_home is None:
        return None, "RUNTIME_UNAVAILABLE"
    candidates: list[Path] = []
    try:
        paths = _instance_paths.resolve_instance_paths(world_home)
        candidates.append(paths.body_runtime_json)
    except Exception:
        pass
    try:
        candidates.append(Path(world_home) / "data" / "body" / "runtime.json")
    except (OSError, RuntimeError):
        pass
    chosen: Optional[Path] = None
    for cand in candidates:
        try:
            if cand.is_file() and not cand.is_symlink():
                chosen = cand
                break
        except OSError:
            continue
    if chosen is None:
        return None, "RUNTIME_UNAVAILABLE"
    try:
        raw = json.loads(chosen.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "RUNTIME_UNREADABLE"
    if not isinstance(raw, Mapping):
        return None, "RUNTIME_MALFORMED"
    return raw, None


def _unknown_health_view(
    *,
    source: str,
    reason: str,
    body_id: str = "chiyo_body",
    illness_authority: Optional[str] = None,
    acute_body_authority: str = "unavailable",
    thermal_band: Optional[str] = None,
    lived_needs_bands: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """A named unknown: no health claim, no reset, no fabricated episode."""

    return {
        "schema_version": SCHEMA_PHONE_HEALTH,
        "ok": True,
        "health_status": HealthStatus.UNATTESTED.value,
        "attested": False,
        "since": None,
        "episode": None,
        "capacity": {
            "index": 0.0,
            # No evidence is not a verdict: an unattested body has no health
            # opinion, so the panel must not claim "no intense creative work"
            # (that is a refusal) or cap options to zero.  The state stays
            # UNKNOWN and ``attested`` stays False, which is the honest fact.
            "candidate_cap": None,
            "max_plan_steps": 0,
            "creative_block": False,
            "latency_floor_ms": 4000,
            "state": "UNKNOWN",
        },
        "health_source": source,
        "illness_authority": illness_authority or source,
        "acute_body_authority": acute_body_authority,
        "thermal_band": thermal_band,
        "lived_needs_bands": lived_needs_bands,
        "reason_code": reason,
        "read_only": True,
        "writes_performed": 0,
    }


def body_health_view(world_home: Any, *, at: datetime, body_id: str = "chiyo_body") -> dict[str, Any]:
    """The bounded, read-only view the desktop panel renders.

    One honest source chain: ``v03_ecology.json`` is the sole illness FSM when
    present, and ``runtime.json`` is the sole acute fatigue / warmth / lived-needs
    authority.  A missing or corrupt file is a named unknown, never HEALTHY;
    nothing is written here.
    """

    runtime_state, runtime_reason = _read_runtime_state(world_home)
    thermal_band = _thermal_band_from_runtime(runtime_state)
    lived_needs_bands = _lived_needs_bands_from_runtime(runtime_state)
    acute_body_authority = "body_runtime" if runtime_state is not None else "unavailable"

    illness = read_v03_illness(world_home)
    v03_present = isinstance(illness, Mapping)
    v03_phase = str(illness.get("phase") or "healthy").strip().lower() if v03_present else ""
    health_source = "none"
    reason_code: Optional[str] = runtime_reason
    record: Optional[HealthRecord] = None

    try:
        body_dir = _instance_paths.resolve_instance_paths(world_home).body_dir
    except Exception:
        body_dir = Path(world_home) / "data" / "body"

    if v03_present and v03_phase in ("prodrome", "acute_illness", "convalescence"):
        record = record_from_illness(
            illness, body_id=body_id, subject_id="self", home_ref="", at=at
        )
        health_source = "v03_illness"
        reason_code = record.source_reason or runtime_reason
    else:
        try:
            record = HealthStore(body_dir).load()
            health_source = "v03_illness" if v03_present else ("health_record" if record is not None else "none")
        except (OSError, ValueError, TypeError):
            # A corrupt record is refused, not silently re-initialised into a
            # fresh HEALTHY body.
            return _unknown_health_view(
                source="health_record",
                reason="HEALTH_RECORD_UNREADABLE",
                body_id=body_id,
                illness_authority="v03_ecology" if v03_present else "health_record",
                acute_body_authority=acute_body_authority,
                thermal_band=thermal_band,
                lived_needs_bands=lived_needs_bands,
            )
        # When v03_ecology.json is present and explicitly reports "healthy",
        # v03_ecology is the sole illness FSM: never let a stale health.json
        # episode override it into ACUTE_ILLNESS or CONVALESCENT.
        if v03_present and record is not None and record.episode is not None:
            record = replace(
                record,
                episode=None,
                status=HealthStatus.HEALTHY,
                status_authority="derived",
                source_reason=None,
            )

    if record is None:
        record = initial_record(body_id=body_id, subject_id="self", home_ref="", at=at)

    snapshot, envelope = project(record, runtime_state, at=at)
    episode = snapshot.episode
    # An unattested body has no health opinion.  The envelope's own conservative
    # constants (0 / True) stay where they are for the decision layer, but the
    # phone view must not turn "no readings yet" into "intense creative work is
    # refused" -- that is a verdict the body never gave.
    health_known = snapshot.status is not HealthStatus.UNATTESTED
    capacity_cap = envelope.candidate_cap if health_known else None
    capacity_creative_block = envelope.creative_block if health_known else False
    return {
        "schema_version": SCHEMA_PHONE_HEALTH,
        "ok": True,
        "health_status": snapshot.status.value,
        "attested": snapshot.attested,
        "since": snapshot.health_since.isoformat(),
        "episode": None
        if episode is None
        else {
            "open": episode.open,
            "origin": episode.origin,
            # A qualitative band only; raw severity never leaves the body.
            "severity_band": _severity_band(episode.severity),
        },
        "capacity": {
            "index": envelope.capacity_index,
            "candidate_cap": capacity_cap,
            "max_plan_steps": envelope.max_plan_steps,
            "creative_block": capacity_creative_block,
            "latency_floor_ms": envelope.latency_floor_ms,
            "state": envelope.capacity_state,
        },
        "health_source": health_source,
        "illness_authority": "v03_ecology" if v03_present else health_source,
        "acute_body_authority": acute_body_authority,
        "thermal_band": thermal_band,
        "lived_needs_bands": lived_needs_bands,
        "reason_code": reason_code,
        "read_only": True,
        "writes_performed": 0,
    }

