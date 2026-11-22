#!/usr/bin/env python3
"""Deterministic micro-zone room ecology for the v3.0 Ch.33/34 + v0.4 Ch.41 loop.

A handful of real spots inside and just outside the home (window sill, desk,
entryway, porch eaves, open yard) each carry objective local conditions derived
from the real neighbourhood weather/airflow/season, the real micro-zone state
(window, lamp, chosen spot) and the real physical matter space (warm food).

Everything here is 100% deterministic, zero-network and zero-LLM
(``llm_calls == 0``).  Reads never create a directory, file or mtime: when the
state files are absent the module is a pure projection of honest defaults.

State file: ``<root>/world/v08_microzone_state.json``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_WORLD_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_WORLD_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_WORLD_SCRIPTS_DIR))

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat script import fallback
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.world.scripts import v05_season_solar_pulse as _season_solar_pulse
except ImportError:  # pragma: no cover - flat script import fallback
    import v05_season_solar_pulse as _season_solar_pulse  # type: ignore[no-redef]

STATE_REL_PATH: tuple[str, ...] = ("world", "v08_microzone_state.json")
#: Upstream room authorities: the phone owns the declared window comfort state,
#: the World body substrate owns the measured pose.  Both are read-only here.
SOMATIC_REL_PATH: tuple[str, ...] = ("phone", "somatic_comfort.json")
WORLD_POSE_REL_PATH: tuple[str, ...] = ("data", "world", "body_substrate.json")
MANAGED_WORLD_POSE_REL_PATH: tuple[str, ...] = ("one-day", "world-home", "data", "world", "body_substrate.json")
SCHEMA_VERSION = 1
VIEW_SCHEMA = "nyairo.v08.microzone_ecology.v1"

SPOT_IDS: tuple[str, ...] = (
    "window_side",
    "desk_inner",
    "entryway",
    "porch_eaves",
    "open_yard",
)
WINDOW_STATES: tuple[str, ...] = ("closed", "ajar", "open")
LAMP_ANGLES: tuple[str, ...] = ("direct", "soft_side")
LIGHT_QUALITIES: tuple[str, ...] = ("comfortable", "glare", "dim", "soft_lamp")
DRAFT_STATES: tuple[str, ...] = ("calm", "breeze", "chilly_draft", "rain_splash")
SHELTER_EXPOSURES: tuple[str, ...] = ("indoor_dry", "sheltered_slow_dry", "exposed_wet")

VISIBILITIES: tuple[str, ...] = ("shared", "private")
WEATHER_STATES: tuple[str, ...] = ("clear", "drizzle", "rain")
AIRFLOWS: tuple[str, ...] = ("weak", "medium", "strong")
SEASONS: tuple[str, ...] = ("spring", "summer", "autumn", "winter")

#: A winter night or an overcast strong-wind day really chills the open yard.
_WINTER_YARD_CHILL_HINT_CN = "冬日院风微寒，窗边或柔灯下更舒服"
#: A clear summer evening breeze is genuinely pleasant to sit in for a while.
_SUMMER_EVENING_HINT_CN = "夏傍晚院风凉爽，适合坐一会儿"
_SUMMER_EVENING_FROM_MIN = 17 * 60
_SUMMER_EVENING_TO_MIN = 20 * 60 + 30

#: The indoor spots a lamp actually lights (porch / yard stay dark at night).
_INDOOR_SPOTS = frozenset({"window_side", "desk_inner", "entryway"})
#: Zones whose warm/hot food really perfumes the room (Ch.41 §六).
_INDOOR_FOOD_ZONES = frozenset({
    "home_kitchen",
    "kitchen_counter",
    "kitchen_stove",
    "kitchen_sink",
    "dining_table",
    "home_desk",
    "home_staging_corner",
    "home_fridge_door",
})

#: A doorstep cue older than this is no longer a live sound.
_DOORSTEP_RECENT_MINUTES = 30.0

#: World pose (``world_body_substrate.POSES``) -> the subject's plain posture.
_POSE_POSTURE_CN: dict[str, str] = {
    "standing": "站着",
    "seated": "正常坐着",
    "lying": "躺着",
    "transitioning": "正在换姿势",
}

#: The phone's declared local zone -> the matching micro-zone spot.
_SOMATIC_ZONE_TO_SPOT: dict[str, str] = {
    "window_side": "window_side",
    "desk_side": "desk_inner",
}

LOCAL_TZ = timezone(timedelta(hours=8))

_ERROR_STATUS: dict[str, int] = {
    "INVALID_FIELDS": 400,
    "INVALID_TARGET": 400,
    "FORBIDDEN_ACTOR_SIDE": 403,
    "FORBIDDEN_PARTNER_ACTION": 403,
    "CLIENT_IDENTITY_REFUSED": 409,
    "OPERATION_KEY_CONFLICT": 409,
}

_FORBIDDEN_IDENTITY_KEYS = frozenset(
    {"uid", "by", "actor", "subject", "owner", "author_side", "euid", "gid", "authority"}
)

_ADJUST_FIELDS = frozenset({
    "window_state",
    "lamp_on",
    "lamp_angle",
    "target_spot_id",
    "posture_cn",
    "preserve_work_ref",
    "preserve_breakpoint_cn",
    "visibility",
    "doorstep_sound_cue_cn",
    "operation_key",
})

#: A caring co-living adjustment is window/lamp only; the subject's own spot,
#: posture, work breakpoints and visibility are never partner-editable.
_PARTNER_FORBIDDEN_FIELDS = frozenset({
    "target_spot_id",
    "posture_cn",
    "preserve_work_ref",
    "preserve_breakpoint_cn",
    "visibility",
})

#: Guard against the matter view calling back into this resolver (physics
#: coupling reads local conditions, and local conditions read the matter view).
_MATTER_READ_LOCAL = threading.local()


class V08MicrozoneError(ValueError):
    """A named refusal with the HTTP status the phone surface maps it to."""

    def __init__(self, reason_code: str, message: str = "") -> None:
        self.reason_code = str(reason_code)
        self.message = str(message or self.reason_code)
        self.status_code = _ERROR_STATUS.get(self.reason_code, 400)
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=LOCAL_TZ)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).isoformat(timespec="seconds")


def _resolve_now(
    now_iso: Optional[str],
    *,
    default_noon: bool = False,
) -> tuple[datetime, str]:
    dt = _parse_iso(now_iso) if now_iso is not None else None
    if dt is None:
        dt = _now()
        if default_noon:
            # Deterministic daytime default when the caller gave no clock.
            dt = dt.replace(hour=12, minute=0, second=0, microsecond=0)
    return dt, _format_iso(dt)


def _minutes_since(value: Any, now_dt: datetime) -> Optional[float]:
    dt = _parse_iso(value)
    if dt is None:
        return None
    return max(0.0, (now_dt - dt).total_seconds() / 60.0)


# ---------------------------------------------------------------------------
# Zero-write reads
# ---------------------------------------------------------------------------


def _canonical_root(root: Path | str) -> Path:
    raw = Path(root)
    if raw.exists() and raw.is_symlink():
        return raw
    return _instance_paths.resolve_instance_paths(raw).day_root


def state_path(root: Path | str) -> Path:
    return _canonical_root(root).joinpath(*STATE_REL_PATH)


def _read_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    """Parse an existing JSON file without creating or touching anything."""

    try:
        if not path.is_file() or path.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _choice_or(value: Any, choices: tuple[str, ...], default: str) -> str:
    if isinstance(value, str) and value.strip() in choices:
        return value.strip()
    return default


def _extract_explicit_v04_season(env: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(env, Mapping):
        return None
    norm = _season_solar_pulse.normalize_season(env.get("season"))
    if norm is None:
        return None
    if env.get("season_explicit") is True:
        return norm
    if env.get("season_explicit") is False:
        return None
    if env.get("updated_at") is not None:
        return norm
    if "season_explicit" not in env:
        return norm
    return None


def _read_neighborhood_env(
    root: Path | str,
    *,
    now_dt: Optional[datetime] = None,
) -> dict[str, str]:
    croot = _canonical_root(root)
    doc = _read_json_zero_write(croot / "world" / "v04_neighborhood_state.json") or {}
    env = doc.get("environment") if isinstance(doc.get("environment"), Mapping) else {}
    weather = _choice_or(env.get("weather") or doc.get("weather"), WEATHER_STATES, "clear")
    airflow = _choice_or(env.get("airflow"), AIRFLOWS, "medium")
    explicit_season = _extract_explicit_v04_season(env)
    if explicit_season is not None:
        season_text = explicit_season
    else:
        effective_dt = now_dt if now_dt is not None else _now()
        season_text = _season_solar_pulse.resolve_effective_season(effective_dt)[0]
    return {"weather": weather, "airflow": airflow, "season": season_text}


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "created_at": None,
        "updated_at": None,
        "window_state": "closed",
        "lamp_on": False,
        "lamp_angle": "direct",
        "active_spot_id": "desk_inner",
        "posture_cn": "正常坐着",
        "preserved_work_ref": None,
        "preserved_breakpoint_cn": None,
        "continuity_preserved": False,
        "doorstep_sound_cue_cn": None,
        "doorstep_sound_at": None,
        "visibility": "private",
        "operations": {},
    }


def _read_microzone_state(root: Path | str) -> dict[str, Any]:
    """Parse the micro-zone state, falling back cleanly per field."""

    state = _default_state()
    parsed = _read_json_zero_write(state_path(root))
    if not parsed:
        return state

    for key in ("schema_version", "created_at", "updated_at"):
        if key in parsed:
            state[key] = copy.deepcopy(parsed[key])
    revision = parsed.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool):
        state["revision"] = revision
    state["window_state"] = _choice_or(parsed.get("window_state"), WINDOW_STATES, "closed")
    if isinstance(parsed.get("lamp_on"), bool):
        state["lamp_on"] = parsed["lamp_on"]
    state["lamp_angle"] = _choice_or(parsed.get("lamp_angle"), LAMP_ANGLES, "direct")
    state["active_spot_id"] = _choice_or(parsed.get("active_spot_id"), SPOT_IDS, "desk_inner")
    posture = parsed.get("posture_cn")
    if isinstance(posture, str) and posture.strip():
        state["posture_cn"] = posture.strip()[:40]
    for key in ("preserved_work_ref", "preserved_breakpoint_cn", "doorstep_sound_cue_cn", "doorstep_sound_at"):
        value = parsed.get(key)
        state[key] = value.strip() if isinstance(value, str) and value.strip() else None
    state["continuity_preserved"] = parsed.get("continuity_preserved") is True
    state["visibility"] = _choice_or(parsed.get("visibility"), VISIBILITIES, "private")
    if isinstance(parsed.get("operations"), Mapping):
        state["operations"] = copy.deepcopy(dict(parsed["operations"]))
    return state


# ---------------------------------------------------------------------------
# Upstream room authorities (phone window comfort + World pose), zero-write
# ---------------------------------------------------------------------------


def _somatic_upstream_zero_write(root: Path | str) -> dict[str, Any]:
    """Read the phone's declared window/zone state without writing anything."""

    doc = _read_json_zero_write(_canonical_root(root).joinpath(*SOMATIC_REL_PATH))
    if not doc:
        return {}
    out: dict[str, Any] = {}
    window = doc.get("window")
    if isinstance(window, Mapping):
        raw_state = window.get("state")
        if isinstance(raw_state, str) and raw_state.strip() in WINDOW_STATES:
            out["window_state"] = raw_state.strip()
            out["window_updated_at"] = _parse_iso(window.get("updated_at"))
    position = doc.get("local_position")
    if isinstance(position, Mapping):
        zone = position.get("zone")
        if isinstance(zone, str) and zone.strip() in _SOMATIC_ZONE_TO_SPOT:
            out["zone_spot_id"] = _SOMATIC_ZONE_TO_SPOT[zone.strip()]
    return out


def _world_pose_zero_write(root: Path | str) -> Optional[str]:
    """Read the World body substrate's measured pose, zero-write."""

    paths = _instance_paths.resolve_instance_paths(root)
    doc = (
        _read_json_zero_write(paths.body_substrate_json)
        or _read_json_zero_write(paths.day_root.joinpath(*MANAGED_WORLD_POSE_REL_PATH))
        or _read_json_zero_write(paths.day_root.joinpath(*WORLD_POSE_REL_PATH))
    )
    if not doc:
        return None
    pose = doc.get("pose")
    if isinstance(pose, str) and pose.strip() in _POSE_POSTURE_CN:
        return pose.strip()
    return None


def _state_file_present(root: Path | str) -> bool:
    path = state_path(root)
    return path.is_file() and not path.is_symlink()


def _upstream_window_should_win(
    state: Mapping[str, Any],
    *,
    state_present: bool,
    upstream_updated_at: Optional[datetime],
) -> bool:
    """A fresh micro-zone has no window opinion; a newer phone update wins."""

    if not state_present:
        return True
    local_updated_at = _parse_iso(state.get("updated_at"))
    if upstream_updated_at is None:
        return False
    return local_updated_at is None or upstream_updated_at > local_updated_at


def _align_upstream_room_authority(
    root: Path | str, state: Mapping[str, Any], *, state_present: bool
) -> dict[str, str]:
    """Resolve the effective window/spot/posture without touching drying clocks."""

    somatic = _somatic_upstream_zero_write(root)
    window_state = str(state["window_state"])
    upstream_window = somatic.get("window_state")
    if upstream_window and _upstream_window_should_win(
        state,
        state_present=state_present,
        upstream_updated_at=somatic.get("window_updated_at"),
    ):
        window_state = str(upstream_window)

    active_spot_id = str(state["active_spot_id"])
    if not state_present:
        if somatic.get("zone_spot_id"):
            active_spot_id = str(somatic["zone_spot_id"])
        else:
            world_loc = _instance_paths.read_canonical_world_location_zero_write(root)
            if world_loc.get("found") and str(world_loc.get("v08_spot_id") or "") in SPOT_IDS:
                active_spot_id = str(world_loc["v08_spot_id"])

    posture_cn = str(state["posture_cn"])
    if not state_present:
        pose = _world_pose_zero_write(root)
        if pose is not None:
            posture_cn = _POSE_POSTURE_CN[pose]
    return {
        "window_state": window_state,
        "active_spot_id": active_spot_id,
        "posture_cn": posture_cn,
    }


# ---------------------------------------------------------------------------
# Atomic write (0600 temp + fsync + os.replace, symlinks refused)
# ---------------------------------------------------------------------------


def _atomic_write_state(root: Path | str, state: Mapping[str, Any]) -> None:
    path = state_path(root)
    if path.exists() and path.is_symlink():
        raise V08MicrozoneError("INVALID_TARGET", "state file is a symlink")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise V08MicrozoneError("INVALID_TARGET", "state directory is a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(state))
    encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".v08_microzone_", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, str(path))
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _bump(state: dict[str, Any], now_text: str) -> None:
    state["schema_version"] = SCHEMA_VERSION
    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_text
    if not state.get("created_at"):
        state["created_at"] = now_text


# ---------------------------------------------------------------------------
# Operation-key idempotency
# ---------------------------------------------------------------------------


def _fingerprint(actor_side: str, core: Mapping[str, Any]) -> str:
    import hashlib

    encoded = json.dumps(
        {"action": "adjust_microzone_comfort", "actor_side": actor_side, "core": core},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_replay(
    state: Mapping[str, Any],
    *,
    operation_key: Optional[str],
    fingerprint: str,
) -> Optional[dict[str, Any]]:
    if not operation_key:
        return None
    operations = state.get("operations") if isinstance(state.get("operations"), Mapping) else {}
    existing = operations.get(operation_key)
    if not isinstance(existing, Mapping):
        return None
    if str(existing.get("fingerprint") or "") != fingerprint:
        raise V08MicrozoneError(
            "OPERATION_KEY_CONFLICT",
            "the same operation_key was used for a different adjustment",
        )
    receipt = existing.get("receipt")
    out = copy.deepcopy(dict(receipt)) if isinstance(receipt, Mapping) else {}
    out["already_applied"] = True
    return out


def _store_operation(
    state: dict[str, Any],
    *,
    operation_key: Optional[str],
    fingerprint: str,
    receipt: Mapping[str, Any],
) -> None:
    if not operation_key:
        return
    operations = state.get("operations")
    if not isinstance(operations, dict):
        operations = {}
    operations[str(operation_key)] = {
        "fingerprint": fingerprint,
        "receipt": copy.deepcopy(dict(receipt)),
    }
    state["operations"] = operations


# ---------------------------------------------------------------------------
# Matter-derived aroma (Ch.41 §六 iron gate: never hallucinate cooking smells)
# ---------------------------------------------------------------------------


def _matter_view_zero_write(root: Path | str, now_text: str) -> Optional[Mapping[str, Any]]:
    if getattr(_MATTER_READ_LOCAL, "depth", 0) > 0:
        return None
    try:
        from components.world.scripts import v08_universal_matter as matter
    except ImportError:  # pragma: no cover - flat world/scripts import path
        try:
            import v08_universal_matter as matter  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001 - absent matter module is simply no aroma
            return None
    _MATTER_READ_LOCAL.depth = getattr(_MATTER_READ_LOCAL, "depth", 0) + 1
    try:
        return matter.read_matter_space_view(root, actor_side="subject", now_iso=now_text)
    except Exception:  # noqa: BLE001 - a broken matter file is simply absent
        return None
    finally:
        _MATTER_READ_LOCAL.depth = getattr(_MATTER_READ_LOCAL, "depth", 1) - 1


def _zone_of_object(
    obj: Mapping[str, Any],
    objects_by_id: Mapping[str, Mapping[str, Any]],
    zone_ids: frozenset[str],
) -> Optional[str]:
    parent_id = obj.get("parent_id")
    for _ in range(8):
        if not parent_id:
            return None
        pid = str(parent_id)
        if pid in zone_ids:
            return pid
        parent = objects_by_id.get(pid)
        if not isinstance(parent, Mapping):
            return None
        parent_id = parent.get("parent_id")
    return None


def _aroma_cue_cn(matter_view: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Only real warm/hot, non-spoiled food or drink in an indoor zone smells."""

    if not isinstance(matter_view, Mapping):
        return None
    objects = [row for row in (matter_view.get("objects") or []) if isinstance(row, Mapping)]
    zone_ids = frozenset(
        str(row.get("zone_id"))
        for row in (matter_view.get("zones") or [])
        if isinstance(row, Mapping) and row.get("zone_id")
    )
    objects_by_id = {
        str(row.get("object_id")): row for row in objects if row.get("object_id")
    }
    for row in sorted(objects, key=lambda item: str(item.get("object_id") or "")):
        if row.get("active") is False:
            continue
        if str(row.get("item_kind") or "") not in ("prepared_food", "drink"):
            continue
        if str(row.get("thermal") or "room") not in ("hot", "warm"):
            continue
        if str(row.get("freshness") or "") == "spoiled":
            continue
        zone_id = _zone_of_object(row, objects_by_id, zone_ids)
        if zone_id not in _INDOOR_FOOD_ZONES:
            continue
        label = str(row.get("label_cn") or row.get("object_id") or "饭菜").strip()
        return f"屋里有刚温好的「{label}」香味"
    return None


# ---------------------------------------------------------------------------
# Local condition derivation (pure, no writes)
# ---------------------------------------------------------------------------


def _light_quality(
    spot_id: str,
    *,
    hour: int,
    weather: str,
    lamp_on: bool,
    lamp_angle: str,
    is_night: bool,
) -> str:
    afternoon_sun = 13 <= hour < 17 and weather == "clear"
    if spot_id == "window_side" and afternoon_sun:
        # Daylight sun at the sill is natural glare; a side lamp cannot undo it.
        return "glare"
    if spot_id not in _INDOOR_SPOTS:
        return "dim" if is_night else "comfortable"
    if is_night:
        if not lamp_on:
            return "dim"
        return "soft_lamp" if lamp_angle == "soft_side" else "comfortable"
    if spot_id == "desk_inner" and afternoon_sun:
        return "comfortable"
    if lamp_on and lamp_angle == "soft_side":
        return "soft_lamp"
    return "comfortable"


def _spot_climate(
    spot_id: str,
    *,
    weather: str,
    airflow: str,
    season: str,
    window_state: str,
) -> tuple[str, str, float, float]:
    """Return (draft_state, shelter_exposure, drying_multiplier, cooling_multiplier)."""

    if spot_id == "window_side":
        if weather == "rain" and window_state in ("open", "ajar"):
            cooling = 1.5 if window_state == "open" else 1.2
            return "rain_splash", "exposed_wet", 0.0, cooling
        if window_state == "open":
            draft = "chilly_draft" if (season == "winter" or airflow == "strong") else "breeze"
            return draft, "indoor_dry", 1.0, 1.5
        if window_state == "ajar":
            return "breeze", "indoor_dry", 1.2, 1.2
        return "calm", "indoor_dry", 1.0, 1.0

    if spot_id == "porch_eaves":
        wet = weather in ("drizzle", "rain")
        draft = "breeze" if airflow == "strong" else "calm"
        return (
            draft,
            "sheltered_slow_dry" if wet else "indoor_dry",
            0.5 if wet else 1.0,
            1.0,
        )

    if spot_id == "open_yard":
        wet = weather in ("drizzle", "rain")
        if wet:
            return "rain_splash", "exposed_wet", 0.0, 1.0
        draft = "breeze" if airflow == "strong" else "calm"
        return draft, "indoor_dry", 1.0, 1.0

    # desk_inner / entryway: calm unless a strong draft passes an open window.
    draft = "breeze" if (window_state == "open" and airflow == "strong") else "calm"
    return draft, "indoor_dry", 1.0, 1.5 if window_state == "open" else 1.0


def _season_open_yard(
    season: str, *, weather: str, airflow: str, now_dt: datetime
) -> tuple[bool, Optional[str]]:
    """Return (seasonal_chill, seasonal_hint_cn) for the open yard.

    Only real season + local clock + real weather/airflow decide this; nothing
    is invented.  A winter night (or an overcast strong-wind day) chills the
    yard, while a clear summer evening is just pleasantly cool.
    """

    hour = int(now_dt.hour)
    is_night = hour >= 18 or hour < 6
    overcast = weather != "clear"
    if season == "winter" and (is_night or (overcast and airflow == "strong")):
        return True, _WINTER_YARD_CHILL_HINT_CN
    if season == "summer" and weather == "clear":
        minute_of_day = hour * 60 + int(now_dt.minute)
        if _SUMMER_EVENING_FROM_MIN <= minute_of_day <= _SUMMER_EVENING_TO_MIN:
            return False, _SUMMER_EVENING_HINT_CN
    return False, None


def _doorstep_recent(cue: Any, at: Any, now_dt: datetime) -> Optional[str]:
    if not isinstance(cue, str) or not cue.strip():
        return None
    elapsed = _minutes_since(at, now_dt)
    if elapsed is not None and elapsed > _DOORSTEP_RECENT_MINUTES:
        return None
    return cue.strip()


def resolve_local_conditions_zero_write(
    root: Path | str,
    *,
    now_iso: Optional[str] = None,
    character_pack: Optional[str] = None,
) -> dict[str, Any]:
    """Project the objective micro-zone conditions.  100% zero-write."""

    now_dt, now_text = _resolve_now(now_iso, default_noon=True)
    pack = character_pack.strip() if isinstance(character_pack, str) and character_pack.strip() else "TA"

    env = _read_neighborhood_env(root, now_dt=now_dt)
    state = _read_microzone_state(root)
    aligned = _align_upstream_room_authority(
        root, state, state_present=_state_file_present(root)
    )
    weather = env["weather"]
    airflow = env["airflow"]
    season = env["season"]
    window_state = aligned["window_state"]
    lamp_on = bool(state["lamp_on"])
    lamp_angle = str(state["lamp_angle"])
    hour = int(now_dt.hour)
    is_night = hour >= 18 or hour < 6

    spots: list[dict[str, Any]] = []
    for spot_id in SPOT_IDS:
        light = _light_quality(
            spot_id,
            hour=hour,
            weather=weather,
            lamp_on=lamp_on,
            lamp_angle=lamp_angle,
            is_night=is_night,
        )
        draft, shelter, drying, cooling = _spot_climate(
            spot_id,
            weather=weather,
            airflow=airflow,
            season=season,
            window_state=window_state,
        )
        spots.append({
            "spot_id": spot_id,
            "light_quality": light,
            "draft_state": draft,
            "shelter_exposure": shelter,
            "drying_rate_multiplier": drying,
            "cooling_rate_multiplier": cooling,
        })
    spot_map = {row["spot_id"]: row for row in spots}

    # Real season + local clock + weather texture the open yard.  A chill never
    # overwrites a real rain splash, and the window seat is offered as the warm
    # alternative without altering any other existing spot field.
    yard_spot = spot_map["open_yard"]
    seasonal_chill, seasonal_hint_cn = _season_open_yard(
        season, weather=weather, airflow=airflow, now_dt=now_dt
    )
    if seasonal_chill:
        yard_spot["seasonal_chill"] = True
        if yard_spot["draft_state"] != "rain_splash":
            yard_spot["draft_state"] = "chilly_draft"
        yard_spot["seasonal_hint_cn"] = seasonal_hint_cn
        spot_map["window_side"]["recommended_seat"] = True
    elif seasonal_hint_cn:
        yard_spot["seasonal_hint_cn"] = seasonal_hint_cn

    active_spot_id = aligned["active_spot_id"]
    active = spot_map.get(active_spot_id) or {}
    aroma_cue_cn = _aroma_cue_cn(_matter_view_zero_write(root, now_text))
    doorstep_cue = _doorstep_recent(
        state.get("doorstep_sound_cue_cn"), state.get("doorstep_sound_at"), now_dt
    )

    window_spot = spot_map["window_side"]
    reasons: list[str] = []
    if window_spot["light_quality"] == "glare":
        reasons.append("窗边光线刺眼")
    if is_night and any(spot_map[spot]["light_quality"] == "dim" for spot in _INDOOR_SPOTS):
        reasons.append("夜晚屋里光线偏暗")
    if weather == "rain" and window_state in ("open", "ajar"):
        reasons.append("开着窗雨点容易潲进来")
    if window_state == "open" and window_spot["draft_state"] == "chilly_draft":
        reasons.append("窗边穿堂风偏凉")
    if aroma_cue_cn:
        reasons.append("食物茶水暖香")
    if state.get("continuity_preserved") is True and state.get("preserved_work_ref"):
        reasons.append("换了座位继续刚才的事")

    return {
        "schema": VIEW_SCHEMA,
        "character_pack": pack,
        "generated_at": now_text,
        "weather": weather,
        "airflow": airflow,
        "season": season,
        "seasonal_hint_cn": seasonal_hint_cn,
        "is_night": is_night,
        "window_state": window_state,
        "lamp_on": lamp_on,
        "lamp_angle": lamp_angle,
        "active_spot_id": active_spot_id,
        "active_conditions": copy.deepcopy(active or None),
        # Flat mirrors of the active spot's conditions keep the lived-context
        # salience gate a one-line read of the projected mapping.
        "light_quality": active.get("light_quality"),
        "draft_state": active.get("draft_state"),
        "shelter_exposure": active.get("shelter_exposure"),
        "posture_cn": aligned["posture_cn"],
        "visibility": state["visibility"],
        "preserved_work_ref": state.get("preserved_work_ref"),
        "preserved_breakpoint_cn": state.get("preserved_breakpoint_cn"),
        "continuity_preserved": state.get("continuity_preserved") is True,
        "doorstep_sound_cue_cn": doorstep_cue,
        "aroma_cue_cn": aroma_cue_cn,
        "spots": spots,
        "salient_reasons": reasons,
        "is_quiet_background": len(reasons) == 0,
        "llm_calls": 0,
    }


def read_microzone_ecology_view(
    root: Path | str,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
    character_pack: Optional[str] = None,
) -> dict[str, Any]:
    """A zero-write micro-zone view; the partner door never sees private work."""

    if actor_side not in ("subject", "partner"):
        raise V08MicrozoneError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")

    snapshot = resolve_local_conditions_zero_write(
        root, now_iso=now_iso, character_pack=character_pack
    )
    snapshot["actor_side"] = actor_side
    if actor_side == "partner":
        if snapshot.get("visibility") != "shared":
            snapshot["preserved_work_ref"] = None
            snapshot["preserved_breakpoint_cn"] = None
            snapshot["continuity_preserved"] = False
            snapshot["doorstep_sound_cue_cn"] = None
        reasons = [
            reason
            for reason in snapshot["salient_reasons"]
            if reason != "换了座位继续刚才的事"
        ]
        snapshot["salient_reasons"] = reasons
        snapshot["is_quiet_background"] = len(reasons) == 0
    return snapshot


# ---------------------------------------------------------------------------
# The one mutation: adjust local comfort (subject + caring partner)
# ---------------------------------------------------------------------------


def _text_field(value: Any, *, field: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise V08MicrozoneError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if not text:
        raise V08MicrozoneError("INVALID_FIELDS", f"{field} must not be blank")
    if len(text) > max_len:
        raise V08MicrozoneError("INVALID_FIELDS", f"{field} exceeds {max_len} characters")
    return text


def _choice_field(value: Any, choices: tuple[str, ...], *, field: str) -> str:
    if not isinstance(value, str) or value.strip() not in choices:
        raise V08MicrozoneError("INVALID_FIELDS", f"{field} must be one of {list(choices)}")
    return value.strip()


def _validate_adjustment(payload: Any, *, actor_side: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not payload:
        raise V08MicrozoneError("INVALID_FIELDS", "payload must be a non-empty JSON object")

    identity = sorted(set(payload) & _FORBIDDEN_IDENTITY_KEYS)
    if identity:
        raise V08MicrozoneError(
            "CLIENT_IDENTITY_REFUSED",
            "client identity keys are refused: " + ", ".join(identity),
        )
    # ``action`` / ``intent_fingerprint`` are command envelope fields the phone
    # door forwards verbatim; they are not comfort state.
    unknown = sorted(set(payload) - _ADJUST_FIELDS - {"action", "intent_fingerprint"})
    if unknown:
        raise V08MicrozoneError("INVALID_FIELDS", f"unknown fields: {unknown}")

    if actor_side == "partner":
        forbidden = sorted(set(payload) & _PARTNER_FORBIDDEN_FIELDS)
        if forbidden:
            raise V08MicrozoneError(
                "FORBIDDEN_PARTNER_ACTION",
                "the partner door cannot set the subject's own spot/workspace fields: "
                + ", ".join(forbidden),
            )

    fields: dict[str, Any] = {}
    if payload.get("window_state") is not None:
        fields["window_state"] = _choice_field(payload["window_state"], WINDOW_STATES, field="window_state")
    if "lamp_on" in payload and payload["lamp_on"] is not None:
        if not isinstance(payload["lamp_on"], bool):
            raise V08MicrozoneError("INVALID_FIELDS", "lamp_on must be a JSON boolean")
        fields["lamp_on"] = payload["lamp_on"]
    if payload.get("lamp_angle") is not None:
        fields["lamp_angle"] = _choice_field(payload["lamp_angle"], LAMP_ANGLES, field="lamp_angle")
    if payload.get("target_spot_id") is not None:
        fields["target_spot_id"] = _choice_field(payload["target_spot_id"], SPOT_IDS, field="target_spot_id")
    if payload.get("posture_cn") is not None:
        fields["posture_cn"] = _text_field(payload["posture_cn"], field="posture_cn", max_len=40)
    if payload.get("preserve_work_ref") is not None:
        fields["preserve_work_ref"] = _text_field(
            payload["preserve_work_ref"], field="preserve_work_ref", max_len=80
        )
    if payload.get("preserve_breakpoint_cn") is not None:
        fields["preserve_breakpoint_cn"] = _text_field(
            payload["preserve_breakpoint_cn"], field="preserve_breakpoint_cn", max_len=80
        )
    if payload.get("visibility") is not None:
        fields["visibility"] = _choice_field(payload["visibility"], VISIBILITIES, field="visibility")
    if payload.get("doorstep_sound_cue_cn") is not None:
        fields["doorstep_sound_cue_cn"] = _text_field(
            payload["doorstep_sound_cue_cn"], field="doorstep_sound_cue_cn", max_len=80
        )
    if payload.get("operation_key") is not None:
        fields["operation_key"] = _text_field(
            payload["operation_key"], field="operation_key", max_len=120
        )

    actionable = {key for key in fields if key != "operation_key"}
    if not actionable:
        raise V08MicrozoneError("INVALID_FIELDS", "at least one comfort field is required")
    return fields


def adjust_microzone_comfort(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Adjust the real window/lamp/spot state with an atomic, idempotent write."""

    if actor_side not in ("subject", "partner"):
        raise V08MicrozoneError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")

    fields = _validate_adjustment(payload, actor_side=actor_side)
    operation_key = fields.get("operation_key")
    core = {key: value for key, value in fields.items() if key != "operation_key"}
    fingerprint = _fingerprint(actor_side, core)

    now_dt, now_text = _resolve_now(now_iso)
    state = _read_microzone_state(root)
    state_present = _state_file_present(root)
    replay = _check_replay(state, operation_key=operation_key, fingerprint=fingerprint)
    if replay is not None:
        replay["microzone_ecology"] = read_microzone_ecology_view(
            root, actor_side=actor_side, now_iso=now_text
        )
        return replay

    applied: list[str] = []
    if "window_state" in fields:
        state["window_state"] = fields["window_state"]
        applied.append("window_state")
    if "lamp_on" in fields:
        state["lamp_on"] = bool(fields["lamp_on"])
        applied.append("lamp_on")
    if "lamp_angle" in fields:
        state["lamp_angle"] = fields["lamp_angle"]
        applied.append("lamp_angle")
    if "posture_cn" in fields:
        state["posture_cn"] = fields["posture_cn"]
        applied.append("posture_cn")
    if "preserve_work_ref" in fields:
        state["preserved_work_ref"] = fields["preserve_work_ref"]
        applied.append("preserve_work_ref")
    if "preserve_breakpoint_cn" in fields:
        state["preserved_breakpoint_cn"] = fields["preserve_breakpoint_cn"]
        applied.append("preserve_breakpoint_cn")
    if "visibility" in fields:
        state["visibility"] = fields["visibility"]
        applied.append("visibility")
    if "doorstep_sound_cue_cn" in fields:
        state["doorstep_sound_cue_cn"] = fields["doorstep_sound_cue_cn"]
        state["doorstep_sound_at"] = now_text
        applied.append("doorstep_sound_cue_cn")

    target_spot = fields.get("target_spot_id")
    if target_spot is not None and target_spot != state.get("active_spot_id"):
        state["active_spot_id"] = target_spot
        if state.get("preserved_work_ref"):
            state["continuity_preserved"] = True
        applied.append("target_spot_id")

    # A mutation that does not itself speak about the room keeps the upstream
    # phone/world truth deterministic instead of freezing a stale local default.
    if "window_state" not in fields:
        somatic = _somatic_upstream_zero_write(root)
        if somatic.get("window_state") and _upstream_window_should_win(
            state,
            state_present=state_present,
            upstream_updated_at=somatic.get("window_updated_at"),
        ):
            state["window_state"] = str(somatic["window_state"])
    if not state_present:
        if "posture_cn" not in fields:
            pose = _world_pose_zero_write(root)
            if pose is not None:
                state["posture_cn"] = _POSE_POSTURE_CN[pose]
        if "target_spot_id" not in fields:
            zone_spot = _somatic_upstream_zero_write(root).get("zone_spot_id")
            if zone_spot:
                state["active_spot_id"] = str(zone_spot)

    _bump(state, now_text)
    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "adjust_microzone_comfort",
        "actor_side": actor_side,
        "at": now_text,
        "applied_fields": applied,
        "revision": state["revision"],
        "llm_calls": 0,
    }
    _store_operation(
        state,
        operation_key=operation_key,
        fingerprint=fingerprint,
        receipt=receipt,
    )
    _atomic_write_state(root, state)
    receipt["microzone_ecology"] = read_microzone_ecology_view(
        root, actor_side=actor_side, now_iso=now_text
    )
    return receipt


__all__ = [
    "DRAFT_STATES",
    "LAMP_ANGLES",
    "LIGHT_QUALITIES",
    "SEASONS",
    "SHELTER_EXPOSURES",
    "SOMATIC_REL_PATH",
    "SPOT_IDS",
    "STATE_REL_PATH",
    "WORLD_POSE_REL_PATH",
    "V08MicrozoneError",
    "VIEW_SCHEMA",
    "WINDOW_STATES",
    "adjust_microzone_comfort",
    "read_microzone_ecology_view",
    "resolve_local_conditions_zero_write",
    "state_path",
]
