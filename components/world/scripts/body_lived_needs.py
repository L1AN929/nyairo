#!/usr/bin/env python3
"""Optional *virtual* lived needs: hunger / thirst / toilet-use tendency.

Why this exists
---------------
The Body runtime has six somatic variables (fatigue, activation, comfort,
sensory load, tension, warmth) and deliberately no intake or hunger.  The
2026-10-17 note refused to invent nutrition because a hunger without an owner
would need a punishment/health rule to mean anything.

This module is the later, explicit product decision: a *virtual* need profile
that an instance may opt into.  It is not a human body measurement, it has no
death/pain/health penalty, and it never schedules anything.  What it gives the
Life layer is a sourced state ("this instance currently reports thirst at a
noticeable band") plus a real owner-side relief when she actually eats, drinks
or uses a declared facility through the normal World Resolver.

Hard rules
----------
* **Opt-in.**  A ``lived_needs_profile.json`` must exist with ``enabled: true``
  and explicit initial values.  Without it, body.runtime stays exactly v1 and
  no need exists anywhere.
* **No offline punishment.**  Drift only accumulates inside a *live window*:
  two service-owned instants that were both observed while the process was
  serving (a normal tick gap).  A stopped process is therefore not "starving"
  when it comes back -- the long gap is recorded as skipped/unknown and never
  backfilled.  Waiting does not relax this: there is no wall-clock catch-up.
* **No invented intake.**  A relief value only exists when the World
  declaration named it (``lived_need_effects``).  Not every consumable heals
  every need; the old CONSUME non-nutrition rule is unchanged for every
  declaration that does not opt in.
* **Explicit epochs only.**  No midnight reset, no timer, no automatic action.
  Levels are clamped 0..1 and only the Body owner writes them.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROFILE_SCHEMA = "body.lived.needs.profile.v1"
STATE_SCHEMA = "body.lived.needs.v1"
KIND_PROFILE = "body_lived_needs_profile"
KIND_STATE = "body_lived_needs_state"

NEEDS = ("hunger", "thirst", "toilet")
#: The only bands a caller may see.  Raw levels stay inside the Body owner.
BANDS = ("none", "mild", "noticeable", "strong")
BAND_THRESHOLDS = ((0.88, "strong"), (0.70, "noticeable"), (0.50, "mild"))

PROFILE_NAME = "lived_needs_profile.json"
MAX_PROFILE_BYTES = 64 * 1024
#: A single live window may add at most this much need.  It is a bound on one
#: settle call, not a backfill mechanism.
MAX_DRIFT_SECONDS_PER_ADVANCE = 900.0
#: A gap longer than this cannot be shown to be a live service window (sleep,
#: suspend, a stopped process, a clock jump).  It is skipped, not guessed.
LIVE_WINDOW_MAX_GAP_SECONDS = 120.0
#: Below this many accounted seconds the window is left open: the next tick
#: only widens it.  This keeps disk writes to one per settle gap.
LIVE_ADVANCE_SETTLE_SECONDS = 30.0
#: A relief declaration larger than this is a malformed fixture, not a meal.
MAX_RELIEF_PER_ACTION = 1.0

_PROFILE_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "enabled",
        "body_id",
        "needs",
        "max_drift_seconds_per_advance",
        "note",
    }
)
_NEED_KEYS = frozenset({"initial", "rise_per_hour"})
#: ``rates`` is copied from the explicit profile at enable time, so an advance is
#: fully determined by the persisted owner state (no second profile read, no
#: clock, no I/O).  Re-running the explicit enable entry is the only way to
#: change it.
_STATE_KEYS_V1 = frozenset({"schema_version", "levels", "rates", "profile_ref"})
#: Minimal live-window fields.  A v1 state read from disk is accepted and
#: upgraded in memory on the next owner write; no migration pass is run.
_STATE_KEYS_WINDOW = frozenset(
    {"live_seconds", "last_live_at", "offline_skipped_seconds"})
_STATE_KEYS = frozenset(_STATE_KEYS_V1 | _STATE_KEYS_WINDOW)


class LivedNeedsError(ValueError):
    """The lived-need profile or state violated its contract."""


def _number(value: Any, label: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LivedNeedsError(f"{label} must be a number")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise LivedNeedsError(f"{label} must be finite")
    if number < low or number > high:
        raise LivedNeedsError(f"{label} must be within [{low}, {high}]")
    return number


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def profile_path(body_root: Path | str) -> Path:
    return Path(body_root) / PROFILE_NAME


def load_profile(body_root: Path | str) -> Optional[dict[str, Any]]:
    """The explicit profile, or ``None`` when this instance never opted in."""

    path = profile_path(body_root)
    try:
        info = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise LivedNeedsError("lived-needs profile cannot be probed") from error
    if not os.path.isfile(path) or info.st_size > MAX_PROFILE_BYTES:
        raise LivedNeedsError("lived-needs profile is not a bounded regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LivedNeedsError("lived-needs profile is not readable JSON") from error
    return validate_profile(raw)


def validate_profile(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, Mapping) or set(profile) - _PROFILE_KEYS:
        raise LivedNeedsError("lived-needs profile has unknown fields")
    missing = _PROFILE_KEYS - set(profile)
    # ``note`` and ``body_id`` are optional in hand-written fixtures; everything
    # that decides behaviour is mandatory.
    required_missing = missing - {"note", "body_id"}
    if required_missing:
        raise LivedNeedsError(
            "lived-needs profile is missing %s" % sorted(required_missing))
    if profile.get("schema_version") != PROFILE_SCHEMA:
        raise LivedNeedsError("lived-needs profile schema is not supported")
    if profile.get("kind") != KIND_PROFILE:
        raise LivedNeedsError("lived-needs profile kind is not supported")
    if profile.get("enabled") is not True:
        raise LivedNeedsError("lived-needs profile must be enabled with literal true")
    needs_raw = profile.get("needs")
    if not isinstance(needs_raw, Mapping) or set(needs_raw) != set(NEEDS):
        raise LivedNeedsError("lived-needs profile must declare exactly %s" % list(NEEDS))
    needs: dict[str, dict[str, float]] = {}
    for name in NEEDS:
        entry = needs_raw[name]
        if not isinstance(entry, Mapping) or set(entry) != _NEED_KEYS:
            raise LivedNeedsError(
                "need %r must have exactly initial/rise_per_hour" % name)
        needs[name] = {
            "initial": _number(entry["initial"], f"needs.{name}.initial",
                               low=0.0, high=1.0),
            "rise_per_hour": _number(entry["rise_per_hour"],
                                     f"needs.{name}.rise_per_hour",
                                     low=0.0, high=0.5),
        }
    cap = _number(profile.get("max_drift_seconds_per_advance",
                              MAX_DRIFT_SECONDS_PER_ADVANCE),
                  "max_drift_seconds_per_advance", low=0.0, high=86400.0)
    result = {
        "schema_version": PROFILE_SCHEMA,
        "kind": KIND_PROFILE,
        "enabled": True,
        "body_id": str(profile.get("body_id") or "") or None,
        "needs": needs,
        "max_drift_seconds_per_advance": cap,
        "note": str(profile.get("note") or "")[:240] or None,
    }
    return result


def save_profile(body_root: Path | str, profile: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_profile(profile)
    path = profile_path(body_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=1)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return normalized


def build_profile(
    *,
    hunger: float,
    thirst: float,
    toilet: float,
    rise_per_hour: Optional[Mapping[str, float]] = None,
    body_id: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Explicit initial values only -- never a fabricated past experience."""

    rates = dict(rise_per_hour or {})
    return validate_profile({
        "schema_version": PROFILE_SCHEMA,
        "kind": KIND_PROFILE,
        "enabled": True,
        "body_id": body_id,
        "needs": {
            "hunger": {"initial": hunger,
                       "rise_per_hour": rates.get("hunger", 0.08)},
            "thirst": {"initial": thirst,
                       "rise_per_hour": rates.get("thirst", 0.12)},
            "toilet": {"initial": toilet,
                       "rise_per_hour": rates.get("toilet", 0.10)},
        },
        "max_drift_seconds_per_advance": MAX_DRIFT_SECONDS_PER_ADVANCE,
        "note": note or "explicit virtual lived-needs profile; not a human measurement",
    })


def initial_state(profile: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_profile(profile)
    return {
        "schema_version": STATE_SCHEMA,
        "levels": {name: normalized["needs"][name]["initial"] for name in NEEDS},
        "rates": {name: normalized["needs"][name]["rise_per_hour"] for name in NEEDS},
        "profile_ref": "livedneeds-profile:" + _digest(normalized)[:32],
        "live_seconds": 0.0,
        "last_live_at": None,
        "offline_skipped_seconds": 0.0,
    }


def _instant(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as error:
            raise LivedNeedsError(f"{label} must be an ISO instant") from error
    else:
        raise LivedNeedsError(f"{label} must be an ISO instant")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LivedNeedsError("lived-needs state must be an object")
    keys = set(value)
    if _STATE_KEYS_V1 - keys or keys - _STATE_KEYS:
        raise LivedNeedsError(
            "lived-needs state must carry its v1 fields plus at most the "
            "live-window fields")
    if value.get("schema_version") != STATE_SCHEMA:
        raise LivedNeedsError("lived-needs state schema is not supported")
    levels_raw = value.get("levels")
    rates_raw = value.get("rates")
    if not isinstance(levels_raw, Mapping) or set(levels_raw) != set(NEEDS):
        raise LivedNeedsError("lived-needs state must carry exactly %s" % list(NEEDS))
    if not isinstance(rates_raw, Mapping) or set(rates_raw) != set(NEEDS):
        raise LivedNeedsError("lived-needs state must carry its rates for %s" % list(NEEDS))
    last_live_at = value.get("last_live_at")
    if last_live_at is not None:
        last_live_at = _iso(_instant(last_live_at, "last_live_at"))
    return {
        "schema_version": STATE_SCHEMA,
        "levels": {name: _number(levels_raw[name], f"levels.{name}", low=0.0, high=1.0)
                   for name in NEEDS},
        "rates": {name: _number(rates_raw[name], f"rates.{name}", low=0.0, high=0.5)
                  for name in NEEDS},
        "profile_ref": str(value.get("profile_ref") or "") or None,
        "live_seconds": _number(value.get("live_seconds", 0.0), "live_seconds",
                                low=0.0, high=1.0e12),
        "last_live_at": last_live_at,
        "offline_skipped_seconds": _number(
            value.get("offline_skipped_seconds", 0.0),
            "offline_skipped_seconds", low=0.0, high=1.0e12),
    }


def state_present(runtime_state: Mapping[str, Any]) -> bool:
    return isinstance(runtime_state.get("lived_needs"), Mapping)


def band_for(level: float) -> str:
    for threshold, band in BAND_THRESHOLDS:
        if level >= threshold:
            return band
    return "none"


def bands(runtime_state: Mapping[str, Any]) -> Optional[dict[str, str]]:
    """Qualitative need bands, or ``None`` when this body has no profile."""

    block = runtime_state.get("lived_needs")
    if not isinstance(block, Mapping):
        return None
    state = validate_state(block)
    return {name: band_for(state["levels"][name]) for name in NEEDS}


def normalize_relief_input(entry: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"class", "need", "amount", "source_ref"}
    unknown = set(entry) - allowed
    if unknown:
        raise LivedNeedsError(f"LIVED_NEED_RELIEF input has unknown fields {sorted(unknown)}")
    need = str(entry.get("need") or "").strip().lower()
    if need not in NEEDS:
        raise LivedNeedsError(f"need must be one of {list(NEEDS)}")
    amount = _number(entry.get("amount"), "amount", low=0.0, high=MAX_RELIEF_PER_ACTION)
    return {
        "class": "LIVED_NEED_RELIEF",
        "need": need,
        "amount": amount,
        "source_ref": str(entry.get("source_ref") or "")[:256] or None,
    }


def declaration_effects(declaration: Any) -> dict[str, float]:
    """The World declaration's explicit ``lived_need_effects`` block.

    Missing/unknown/out-of-range values fail closed *as no effect*: the old
    CONSUME non-nutrition rule stays the default for every declaration that did
    not opt in.
    """

    if not isinstance(declaration, Mapping):
        return {}
    raw = declaration.get("lived_need_effects")
    if not isinstance(raw, Mapping):
        return {}
    effects: dict[str, float] = {}
    for name in NEEDS:
        if name not in raw:
            continue
        try:
            effects[name] = _number(raw[name], f"lived_need_effects.{name}",
                                    low=0.0, high=MAX_RELIEF_PER_ACTION)
        except LivedNeedsError:
            return {}
    return effects


def apply_advance(
    state: Mapping[str, Any],
    *,
    hours: float,
    relief: Optional[list[Mapping[str, Any]]] = None,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Deterministic drift + explicit relief.  Pure; no clock, no I/O.

    ``hours`` must already be a *live-window* duration chosen by the owner's
    service (see ``advance_live_window``); this function never looks at a
    wall-clock gap itself.
    """

    current = validate_state(state)
    levels = dict(current["levels"])
    if hours > 0.0:
        for name in NEEDS:
            levels[name] = min(
                1.0, max(0.0, levels[name] + current["rates"][name] * hours))
    deltas: dict[str, float] = {}
    for item in relief or ():
        need = str(item.get("need") or "")
        amount = float(item.get("amount") or 0.0)
        if need not in NEEDS or amount <= 0.0:
            continue
        before = levels[need]
        levels[need] = min(1.0, max(0.0, before - amount))
        deltas[need] = deltas.get(need, 0.0) + (levels[need] - before)
    return {
        "schema_version": STATE_SCHEMA,
        "levels": levels,
        "rates": dict(current["rates"]),
        "profile_ref": current.get("profile_ref"),
        "live_seconds": current["live_seconds"],
        "last_live_at": current["last_live_at"],
        "offline_skipped_seconds": current["offline_skipped_seconds"],
    }, deltas


def advance_live_window(
    state: Mapping[str, Any],
    *,
    now: Any,
    max_gap_seconds: float = LIVE_WINDOW_MAX_GAP_SECONDS,
) -> dict[str, Any]:
    """Accumulate need only over an interval the service actually witnessed.

    The Body owner's service calls this from its own tick (and once at startup
    to anchor).  If ``last_live_at`` is absent this call only anchors the
    window.  If the gap is longer than ``max_gap_seconds`` (or the clock moved
    backwards) the interval has no evidence: it is recorded as skipped/unknown
    and levels are left untouched.  Never derives drift from a persisted
    cursor such as ``last_advanced_at``.
    """

    current = validate_state(state)
    moment = _instant(now, "now")
    last_raw = current["last_live_at"]
    if last_raw is None:
        return {
            "state": {**current, "last_live_at": _iso(moment)},
            "reason": "ANCHORED",
            "counted_seconds": 0.0,
            "skipped_seconds": 0.0,
            "levels_changed": False,
        }
    last = _instant(last_raw, "last_live_at")
    delta = (moment - last).total_seconds()
    if delta < 0.0 or delta > float(max_gap_seconds):
        skipped = max(0.0, delta)
        return {
            "state": {
                **current,
                "last_live_at": _iso(moment),
                "offline_skipped_seconds":
                    current["offline_skipped_seconds"] + skipped,
            },
            "reason": "CLOCK_REWOUND" if delta < 0.0 else "OFFLINE_GAP",
            "counted_seconds": 0.0,
            "skipped_seconds": skipped,
            "levels_changed": False,
        }
    drifted, _ = apply_advance(
        current, hours=min(delta, MAX_DRIFT_SECONDS_PER_ADVANCE) / 3600.0)
    return {
        "state": {
            **drifted,
            "live_seconds": current["live_seconds"] + delta,
            "last_live_at": _iso(moment),
        },
        "reason": "LIVE_WINDOW",
        "counted_seconds": delta,
        "skipped_seconds": 0.0,
        "levels_changed": any(
            abs(drifted["levels"][name] - current["levels"][name]) > 0.0
            for name in NEEDS),
    }


def relief_from_result(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The explicit effects a World result carries, as Body relief inputs."""

    effects = declaration_effects({"lived_need_effects": result.get("lived_need_effects")})
    source = str(result.get("execution_id") or "")[:256] or None
    return [
        {"class": "LIVED_NEED_RELIEF", "need": name, "amount": amount,
         "source_ref": source}
        for name, amount in sorted(effects.items())
    ]


__all__ = [
    "BANDS",
    "BAND_THRESHOLDS",
    "KIND_PROFILE",
    "KIND_STATE",
    "LIVE_ADVANCE_SETTLE_SECONDS",
    "LIVE_WINDOW_MAX_GAP_SECONDS",
    "LivedNeedsError",
    "MAX_DRIFT_SECONDS_PER_ADVANCE",
    "MAX_RELIEF_PER_ACTION",
    "NEEDS",
    "PROFILE_NAME",
    "PROFILE_SCHEMA",
    "STATE_SCHEMA",
    "advance_live_window",
    "apply_advance",
    "band_for",
    "bands",
    "build_profile",
    "declaration_effects",
    "initial_state",
    "load_profile",
    "normalize_relief_input",
    "profile_path",
    "relief_from_result",
    "save_profile",
    "state_present",
    "validate_profile",
    "validate_state",
]
