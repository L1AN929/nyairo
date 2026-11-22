#!/usr/bin/env python3
"""S02 Body health — the illness state machine and the capacity envelope.

Implements design v3.0 §05 (身体负荷与生病自愈生态) §1–2:

* ``HealthStatus`` — HEALTHY / STRAINED / ACUTE_ILLNESS / CONVALESCENT /
  UNATTESTED, with double hysteresis so a value hovering at a threshold never
  flaps between states.
* ``FatigueAccumulator`` — acute fatigue (load integral), 7-day chronic EWMA
  and sleep debt, all advanced with injected elapsed time.
* ``IllnessEpisode`` — severity obeys ``ds/dt = α·u(t)·(1-s) - γ·s``; with no
  exposure ``u(t) = 0`` severity is strictly decreasing, so the body can never
  fall ill on its own, and conversation text is not an allowed origin.
* ``CapabilityEnvelope`` — the read-only S02 → S04 narrowing (capacity index,
  candidate cap, creative cut-off, verification, plan steps, concurrency,
  latency floor).  It narrows cognition; it never emits a Candidate and never
  authorises an action.

Hard rules (inherited from the design)
--------------------------------------
* No LLM, no network, no ``random``, no ``datetime.now()``.  Every instant is
  injected by the caller.
* No write to Affect / Desire / Memory / Relationship / Expression.
* No signal is not health: an unattested body yields ``UNATTESTED`` with an
  ``UNKNOWN`` capacity, never a silent ``HEALTHY``.
* A care action, a prop or a warm message can never skip a stage of the
  course: severity only moves through the ODE above.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

SCHEMA_HEALTH = "body.health.v1"
SCHEMA_EPISODE = "body.illness_episode.v1"
SCHEMA_ENVELOPE = "chiyo.s02.capability_envelope.v1"

# Fatigue dynamics (design §1.2(1)).
BASELINE_FATIGUE = 0.15
ACUTE_LOAD_GAIN = 0.35
CHRONIC_HALF_LIFE_HOURS = 168.0
DEBT_HALF_LIFE_HOURS = 36.0
LAMBDA_MIN = 0.05
LAMBDA_MAX = 0.90

# Severity dynamics (design §1.2(2)).
SEVERITY_GAIN = 0.55           # α
CLEARANCE_RATES = {"rest": 0.55, "sleep_like": 0.85, "none": 0.35}

# Double hysteresis (design §1.2(3)).
ACUTE_ENTER = 0.35
ACUTE_EXIT = 0.25
RECOVERED_SEVERITY = 0.08
RECOVERED_MIN_HOURS_OUT = 18.0
BASELINE_CHRONIC = 0.20        # chronic below this counts as "fatigue back to baseline"

# Illness origins.  ``conversation`` is deliberately absent and rejected.
ILLNESS_ORIGINS = ("carrier_selfcheck", "world_exposure", "governance_maintenance")


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    STRAINED = "STRAINED"
    ACUTE_ILLNESS = "ACUTE_ILLNESS"
    CONVALESCENT = "CONVALESCENT"
    UNATTESTED = "UNATTESTED"


@dataclass(frozen=True, slots=True)
class FatigueAccumulator:
    """Acute fatigue, 7-day chronic load and sleep debt (all in [0, 1])."""

    acute: float = BASELINE_FATIGUE
    chronic: float = 0.0
    debt: float = 0.0
    last_load_at: Optional[datetime] = None
    last_rest_at: Optional[datetime] = None

    def clamped(self) -> "FatigueAccumulator":
        return FatigueAccumulator(
            acute=_clamp(self.acute),
            chronic=_clamp(self.chronic),
            debt=_clamp(self.debt),
            last_load_at=self.last_load_at,
            last_rest_at=self.last_rest_at,
        )


@dataclass(frozen=True, slots=True)
class IllnessEpisode:
    episode_id: str
    opened_at: datetime
    origin: str
    severity: float = 0.0
    peak: float = 0.0
    left_acute_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    schema_version: str = SCHEMA_EPISODE

    @property
    def open(self) -> bool:
        return self.closed_at is None


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """One read-only view of the body's health at ``observed_at``."""

    body_id: str
    subject_id: str
    home_ref: str
    revision: int
    observed_at: datetime
    attested: bool
    status: HealthStatus
    health_since: datetime
    fatigue: FatigueAccumulator = field(default_factory=FatigueAccumulator)
    episode: Optional[IllnessEpisode] = None
    hours_out_of_acute: float = 0.0
    #: A named gap the caller may report (e.g. an unsourced v03 episode).  It
    #: is never a status: the projection still refuses to invent one.
    source_reason: Optional[str] = None
    schema_version: str = SCHEMA_HEALTH


@dataclass(frozen=True, slots=True)
class CapabilityEnvelope:
    """S02 → S04 narrowing.  Read-only; never a Candidate, never an order."""

    body_id: str
    health: HealthStatus
    capacity_index: float
    candidate_cap: int
    creative_block: bool
    action_verify_required: bool
    action_reliability_ceiling: float
    max_plan_steps: int
    concurrent_activity_cap: int
    latency_floor_ms: int
    capacity_state: Literal["LIMITED", "UNKNOWN"]
    schema_version: str = SCHEMA_ENVELOPE
    read_only: bool = True
    writes_performed: int = 0


# --------------------------------------------------------------------------
# small helpers


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _half_life_decay(value: float, hours: float, half_life: float) -> float:
    if hours <= 0:
        return _clamp(value)
    return _clamp(value * math.exp(-math.log(2.0) * hours / half_life))


def _lerp(low: float, high: float, t: float) -> float:
    return low + (high - low) * _clamp(t)


# --------------------------------------------------------------------------
# fatigue


def accumulate_fatigue(
    previous: FatigueAccumulator,
    *,
    load_hours: float,
    hours: float,
    recovery_multiplier: float = 1.0,
    rested: bool = False,
) -> FatigueAccumulator:
    """Advance the fatigue accumulator over ``hours``.

    ``load_hours`` is the load integral for this interval (Λ·Δh form already
    folded in by the caller).  Recovery accelerates with ``recovery_multiplier``
    (a real rest, not a label) and never goes below the baseline.
    """

    if hours < 0:
        raise ValueError("hours must be >= 0")
    if load_hours < 0:
        raise ValueError("load_hours must be >= 0")

    raised = _clamp(previous.acute + ACUTE_LOAD_GAIN * load_hours)
    effective = max(LAMBDA_MIN, LAMBDA_MAX * max(0.0, recovery_multiplier) * (1.0 - 0.40 * previous.chronic))
    # F' = baseline + (F - baseline) * e^{-λ·Δt}, once the load is folded in.
    acute = BASELINE_FATIGUE + (raised - BASELINE_FATIGUE) * math.exp(-effective * hours)

    chronic = _lerp(
        previous.chronic,
        _clamp(previous.chronic + 0.5 * load_hours),
        1.0 - math.exp(-hours / CHRONIC_HALF_LIFE_HOURS * math.log(2.0)),
    )
    chronic = _half_life_decay(chronic, hours, CHRONIC_HALF_LIFE_HOURS)

    debt_source = previous.debt + (0.0 if rested else load_hours)
    debt = _half_life_decay(debt_source, hours, DEBT_HALF_LIFE_HOURS)

    return FatigueAccumulator(
        acute=_clamp(acute),
        chronic=_clamp(chronic),
        debt=_clamp(debt),
        last_load_at=previous.last_load_at if load_hours == 0 else previous.last_load_at,
        last_rest_at=previous.last_rest_at,
    ).clamped()


# --------------------------------------------------------------------------
# illness severity


def advance_severity(
    severity: float,
    *,
    hours: float,
    exposure: float = 0.0,
    mode: Literal["rest", "sleep_like", "none"] = "none",
) -> float:
    """Closed-form step of ``ds/dt = α·u·(1-s) - γ·s``.

    With ``exposure == 0`` the map is strictly decreasing for any positive
    ``hours`` — an unattended body never becomes ill on its own.
    """

    if hours < 0:
        raise ValueError("hours must be >= 0")
    if mode not in CLEARANCE_RATES:
        raise ValueError(f"unknown recovery mode: {mode}")
    s0 = _clamp(severity)
    if hours == 0:
        return s0
    u = max(0.0, float(exposure))
    gamma = CLEARANCE_RATES[mode]
    rate = SEVERITY_GAIN * u + gamma
    steady = (SEVERITY_GAIN * u) / rate if rate > 0 else 0.0
    return _clamp(steady + (s0 - steady) * math.exp(-rate * hours))


def open_episode(
    episode_id: str,
    *,
    opened_at: datetime,
    origin: str,
    severity: float,
) -> IllnessEpisode:
    if origin not in ILLNESS_ORIGINS:
        raise ValueError(
            f"origin {origin!r} is not an allowed illness source; "
            "conversation text can never open a course"
        )
    if not episode_id:
        raise ValueError("episode_id is required")
    return IllnessEpisode(
        episode_id=episode_id,
        opened_at=opened_at,
        origin=origin,
        severity=_clamp(severity),
        peak=_clamp(severity),
    )


def advance_episode(
    episode: IllnessEpisode,
    *,
    at: datetime,
    hours: float,
    exposure: float = 0.0,
    mode: Literal["rest", "sleep_like", "none"] = "none",
) -> IllnessEpisode:
    """Move an open episode forward.  Closed episodes are returned unchanged."""

    if not episode.open:
        return episode
    severity = advance_severity(episode.severity, hours=hours, exposure=exposure, mode=mode)
    peak = max(episode.peak, severity)
    left_acute = episode.left_acute_at
    if left_acute is None and severity < ACUTE_EXIT:
        left_acute = at
    return replace(episode, severity=severity, peak=peak, left_acute_at=left_acute)


def close_episode(episode: IllnessEpisode, *, at: datetime) -> IllnessEpisode:
    """Archive a course.  Only ``episode_closed`` should lead here."""

    if not episode.open:
        return episode
    return replace(episode, closed_at=at)


# --------------------------------------------------------------------------
# state selection with double hysteresis


def select_status(
    *,
    attested: bool,
    previous: HealthStatus,
    severity: float,
    fatigue: FatigueAccumulator,
    hours_out_of_acute: float,
    episode_open: bool,
) -> HealthStatus:
    """Pick the health status for this instant.

    * No attestation is never health.
    * A course enters ACUTE_ILLNESS at s >= 0.35 and leaves only below 0.25.
    * A course closes at s < 0.08 *and* at least 18h out of the acute phase
      *and* chronic fatigue back to baseline.
    """

    if not attested:
        return HealthStatus.UNATTESTED

    if episode_open:
        if previous is HealthStatus.ACUTE_ILLNESS:
            if severity >= ACUTE_EXIT:
                return HealthStatus.ACUTE_ILLNESS
            return HealthStatus.CONVALESCENT
        if severity >= ACUTE_ENTER:
            return HealthStatus.ACUTE_ILLNESS
        if previous is HealthStatus.CONVALESCENT:
            return HealthStatus.CONVALESCENT
        return HealthStatus.CONVALESCENT if severity >= RECOVERED_SEVERITY else HealthStatus.HEALTHY

    if previous is HealthStatus.STRAINED:
        if fatigue.chronic >= BASELINE_CHRONIC or fatigue.acute >= ACUTE_ENTER:
            return HealthStatus.STRAINED
        return HealthStatus.HEALTHY
    if fatigue.chronic >= BASELINE_CHRONIC or fatigue.acute >= ACUTE_ENTER:
        return HealthStatus.STRAINED
    return HealthStatus.HEALTHY


def episode_closed(
    *,
    severity: float,
    hours_out_of_acute: float,
    fatigue: FatigueAccumulator,
) -> bool:
    return (
        severity < RECOVERED_SEVERITY
        and hours_out_of_acute >= RECOVERED_MIN_HOURS_OUT
        and fatigue.chronic < BASELINE_CHRONIC
    )


# --------------------------------------------------------------------------
# capability envelope


def derive_envelope(snapshot: HealthSnapshot) -> CapabilityEnvelope:
    """Narrow the S04 working set to what this body can actually carry."""

    status = snapshot.status
    fatigue = snapshot.fatigue

    if status is HealthStatus.UNATTESTED:
        return CapabilityEnvelope(
            body_id=snapshot.body_id,
            health=status,
            capacity_index=0.0,
            candidate_cap=0,
            creative_block=True,
            action_verify_required=True,
            action_reliability_ceiling=0.0,
            max_plan_steps=0,
            concurrent_activity_cap=1,
            latency_floor_ms=4000,
            capacity_state="UNKNOWN",
        )

    if status is HealthStatus.HEALTHY:
        return CapabilityEnvelope(
            body_id=snapshot.body_id,
            health=status,
            capacity_index=1.0,
            candidate_cap=12,
            creative_block=False,
            action_verify_required=False,
            action_reliability_ceiling=1.0,
            max_plan_steps=6,
            concurrent_activity_cap=2,
            latency_floor_ms=400,
            capacity_state="LIMITED",
        )

    if status is HealthStatus.STRAINED:
        t = _clamp(fatigue.chronic)
        return CapabilityEnvelope(
            body_id=snapshot.body_id,
            health=status,
            capacity_index=round(_lerp(0.80, 0.60, t), 3),
            candidate_cap=int(round(_lerp(10, 8, t))),
            creative_block=False,
            action_verify_required=True,
            action_reliability_ceiling=round(_lerp(0.90, 0.80, t), 3),
            max_plan_steps=int(round(_lerp(5, 4, t))),
            concurrent_activity_cap=2,
            latency_floor_ms=int(round(_lerp(540, 980, t))),
            capacity_state="LIMITED",
        )

    severity = snapshot.episode.severity if snapshot.episode else 0.0
    if status is HealthStatus.ACUTE_ILLNESS:
        t = _clamp(severity)
        return CapabilityEnvelope(
            body_id=snapshot.body_id,
            health=status,
            # Aligned with v03's illness capability table (acute = 4 options,
            # capacity 0.35 - 0.20 * severity): one number, one source.
            capacity_index=round(max(0.10, 0.35 - 0.20 * t), 3),
            candidate_cap=4,
            creative_block=True,
            action_verify_required=True,
            action_reliability_ceiling=round(_lerp(0.70, 0.55, t), 3),
            max_plan_steps=int(round(_lerp(3, 1, t))),
            concurrent_activity_cap=1,
            latency_floor_ms=int(round(_lerp(1920, 3320, t))),
            capacity_state="LIMITED",
        )

    # CONVALESCENT: recovering, still narrow; creative work is at the edge.
    t = _clamp((severity - RECOVERED_SEVERITY) / (ACUTE_EXIT - RECOVERED_SEVERITY))
    return CapabilityEnvelope(
        body_id=snapshot.body_id,
        health=HealthStatus.CONVALESCENT,
        # Aligned with v03's convalescence row (8 options, 0.72 - 0.40 * severity).
        capacity_index=round(max(0.45, 0.72 - 0.40 * severity), 3),
        candidate_cap=8,
        creative_block=False,
        action_verify_required=True,
        action_reliability_ceiling=round(_lerp(0.85, 0.65, t), 3),
        max_plan_steps=int(round(_lerp(5, 3, t))),
        concurrent_activity_cap=2,
        latency_floor_ms=int(round(_lerp(625, 1500, t))),
        capacity_state="LIMITED",
    )
