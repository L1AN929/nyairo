#!/usr/bin/env python3
"""v0.4 Ch.51 composable optional body process packs — deterministic, zero-LLM.

Two *optional* body process packs, both **default OFF** and strictly decoupled
from any character-pack pronoun or adult-intimacy state:

1. ``menstrual_cycle`` — a closed clock model.  Once enabled with a clean
   start anchor, the projection derives the cycle index, the day in cycle, the
   active/pre windows, whether supplies need changing and a plain neutral
   feeling line.  Nothing is ever back-filled: only a fresh anchor when
   enabling cleanly is accepted.  Merely talking about it never consumes
   supplies — only ``use_cycle_supply`` clears the supply flag.
2. ``nocturnal_emission`` — a closed stochastic model with *no* periodic
   schedule, *no* absence penalty and *no* generated dream content.  A bind of
   a real sleep activity samples exactly once (idempotent by
   ``sleep_activity_id``) into a hidden internal ledger; the hidden time and
   the unrealised sample are never exposed until the subject actually wakes at
   or after it.

Every helper is deterministic (``llm_calls == 0``) and stdlib-only.  State lives
at ``<root>/body/v08_composable_body_pack.json``.  Reads are **100% zero-write**
(no directory, no file, no mtime change); writes are one atomic replace of that
single file.

Disabling a pack stops future sensations/events but keeps historical receipts
(``history_deleted == False``); only the explicit, confirmed
``clear_private_body_history`` deletes stored sleep/supply logs.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

#: The interoception boundary owns the qualitative-copy firewall.  Import it
#: both as a package module and as a flat ``world/scripts`` module; if neither
#: resolves (standalone stdlib use), a local mirror of the same guard applies.
try:  # pragma: no cover - import path depends on the caller
    from components.world.scripts import body_observation as _body_observation
except Exception:  # noqa: BLE001 - fall back to the flat import below
    try:
        import body_observation as _body_observation  # type: ignore[no-redef]
    except Exception:  # noqa: BLE001 - stdlib-only standalone fallback
        _body_observation = None

try:  # package import (repo root on sys.path)
    from components.life.scripts import state_tx_guard as _tx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat ``life/scripts`` bootstrap
    try:
        import state_tx_guard as _tx  # type: ignore[import-not-found,no-redef]
    except ImportError:
        _LIFE_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "life" / "scripts"
        if str(_LIFE_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
        import state_tx_guard as _tx  # type: ignore[import-not-found,no-redef]

_FALLBACK_RAW_VARIABLES: tuple[str, ...] = (
    "fatigue",
    "activation",
    "comfort",
    "sensory_load",
    "tension",
    "warmth",
)
_FALLBACK_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "raw", "value", "values", "state", "somatic", "fatigue", "activation",
    "comfort", "sensory_load", "tension", "warmth", "energy", "reserve",
    "heartbeat", "temperature", "emotion", "affect", "mood", "desire",
    "feeling", "interpretation", "advice", "diagnosis", "expression",
    "relationship", "memory", "goal", "attention", "salience", "percept",
})


def _fallback_raw_free(payload: Mapping[str, Any]) -> bool:
    """Stdlib mirror of ``body_observation._assert_raw_free`` (no import)."""

    if any(key in _FALLBACK_FORBIDDEN_FIELDS for key in payload):
        return False
    if any(isinstance(value, float) for value in payload.values()):
        return False
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return not any(f'"{name}"' in blob for name in _FALLBACK_RAW_VARIABLES)


def _qualitative_copy_allowed(text: str, *, label: str) -> bool:
    """True when a qualitative line carries no raw body value or telemetry key."""

    # Plain-text smuggling: the structural JSON guard below cannot see a raw
    # variable name embedded inside a quoted string, so scan the text itself.
    if any(char.isdigit() for char in text):
        return False
    lowered = text.lower()
    if any(name in lowered for name in _FALLBACK_RAW_VARIABLES):
        return False
    payload = {label: text}
    guard = getattr(_body_observation, "_assert_raw_free", None) if _body_observation else None
    if guard is not None:
        try:
            guard(payload, label=label)
            return True
        except ImportError:  # guard's own bare ``body_runtime`` import is absent
            pass
        except Exception:  # noqa: BLE001 - a real guard refusal is final
            return False
    return _fallback_raw_free(payload)


def _qualitative_or_empty(text: Any, *, label: str) -> str:
    line = str(text or "")
    return line if line and _qualitative_copy_allowed(line, label=label) else ""


try:  # package import (repo root on sys.path)
    from components.world.scripts import instance_paths as _instance_paths  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat ``world/scripts`` bootstrap
    try:
        import instance_paths as _instance_paths  # type: ignore[import-not-found,no-redef]
    except ImportError:
        _WORLD_PATHS_DIR = Path(__file__).resolve().parent
        if str(_WORLD_PATHS_DIR) not in sys.path:
            sys.path.insert(0, str(_WORLD_PATHS_DIR))
        import instance_paths as _instance_paths  # type: ignore[import-not-found,no-redef]

SCHEMA_VERSION = "body.v08.composable_pack.v1"
VIEW_SCHEMA = "nyairo.v08.composable_body.v1"
STATE_DIRNAME = "body"
STATE_FILENAME = "v08_composable_body_pack.json"

LOCAL_TZ = timezone(timedelta(hours=8))

PACK_MENSTRUAL = "menstrual_cycle"
PACK_NOCTURNAL = "nocturnal_emission"
PACK_IDS: frozenset[str] = frozenset({PACK_MENSTRUAL, PACK_NOCTURNAL})

MENSTRUAL_DISCOMFORT_PROFILES: frozenset[str] = frozenset({
    "none",
    "mild_cramps",
    "mild_fatigue",
    "cramps_and_fatigue",
})

GARMENT_CARE_MODES: frozenset[str] = frozenset({"change_garment", "wash_garment"})

CARE_KINDS: frozenset[str] = frozenset({
    "warm_pad",
    "warm_drink",
    "fresh_supplies",
    "quiet_space",
})

MIN_CYCLE_DAYS = 21
MAX_CYCLE_DAYS = 40
DEFAULT_CYCLE_DAYS = 28
MIN_ACTIVE_DAYS = 2
MAX_ACTIVE_DAYS = 8
DEFAULT_ACTIVE_DAYS = 5
DEFAULT_DISCOMFORT_PROFILE = "mild_cramps"

MIN_PROBABILITY_PERMILLE = 0
MAX_PROBABILITY_PERMILLE = 500
DEFAULT_PROBABILITY_PERMILLE = 120

MIN_HINT_TTL_MINUTES = 15
MAX_HINT_TTL_MINUTES = 720
DEFAULT_HINT_TTL_MINUTES = 180

MAX_PARTNER_CARE_VIEW = 12
MAX_PARTNER_CARE_LOG = 50
MAX_EVENT_ROWS = 200
MAX_OPERATION_RECEIPTS = 200
MAX_COMMITTED_OP_RING = 4096

#: Plain, neutral, non-personality body notes.  No emotional or irritable copy.
_FEELING_BY_PROFILE: dict[str, str] = {
    "none": "",
    "mild_cramps": "有轻微腹部不适，仍可按节奏继续手头事或暂歇",
    "mild_fatigue": "有些轻微疲乏，日常用品该换一换了",
    "cramps_and_fatigue": "腹部有些不适，也容易觉得疲乏，可以放慢节奏，按需要歇一歇",
}

GARMENT_CARE_NOTE_CN = "醒来留意到贴身衣物需要清洗或更换"

_ERROR_STATUS: dict[str, int] = {
    "INVALID_FIELDS": 400,
    "CLIENT_IDENTITY_REFUSED": 409,
    "FORBIDDEN_ACTOR_SIDE": 403,
    "PACK_DISABLED": 409,
    "PACK_UNKNOWN": 400,
    "OPERATION_KEY_CONFLICT": 409,
    "SLEEP_ACTIVITY_NOT_FOUND": 404,
    "NO_PENDING_GARMENT_CARE": 409,
    "CONFIRMATION_REQUIRED": 400,
    "STATE_INVALID": 409,
    "STATE_SYMLINK": 409,
}

#: Client-side identity / score fields are never accepted through this surface.
FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset({
    "uid",
    "user_id",
    "owner_uid",
    "actor_side",
    "has_subject_capability",
    "subject_view",
    "capability",
    "intimacy_score",
    "affection_score",
    "favorability",
})


class V08ComposableBodyError(ValueError):
    """A named refusal with the HTTP status the phone surface maps it to."""

    def __init__(self, reason_code: str, message: str = "") -> None:
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
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


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    dt = _parse_iso(now_iso) if now_iso is not None else None
    if dt is None:
        dt = _now()
    return dt, _format_iso(dt)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _text(value: Any, *, min_len: int, max_len: int, field: str) -> str:
    if not isinstance(value, str):
        raise V08ComposableBodyError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if len(text) < min_len:
        raise V08ComposableBodyError("INVALID_FIELDS", f"{field} is too short")
    if len(text) > max_len:
        raise V08ComposableBodyError(
            "INVALID_FIELDS", f"{field} exceeds {max_len} characters"
        )
    return text


def _optional_text(value: Any, *, max_len: int, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise V08ComposableBodyError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_len:
        raise V08ComposableBodyError(
            "INVALID_FIELDS", f"{field} exceeds {max_len} characters"
        )
    return text


def _strict_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise V08ComposableBodyError("INVALID_FIELDS", f"{field} must be a JSON boolean")


def _int_in_range(value: Any, *, low: int, high: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise V08ComposableBodyError("INVALID_FIELDS", f"{field} must be an integer")
    if not (low <= value <= high):
        raise V08ComposableBodyError(
            "INVALID_FIELDS", f"{field} must be {low}..{high}"
        )
    return int(value)


def _require_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not payload:
        raise V08ComposableBodyError(
            "INVALID_FIELDS", "payload must be a non-empty object"
        )
    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise V08ComposableBodyError(
                "INVALID_FIELDS", "blank or padded key forbidden"
            )
    return dict(payload)


def _assert_no_identity_keys(payload: Mapping[str, Any]) -> None:
    found = sorted(set(payload) & FORBIDDEN_IDENTITY_KEYS)
    if found:
        raise V08ComposableBodyError(
            "CLIENT_IDENTITY_REFUSED",
            "identity or score keys are never accepted: " + ", ".join(found),
        )


# ---------------------------------------------------------------------------
# State persistence (reads are 100% zero-write)
# ---------------------------------------------------------------------------


def state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / STATE_FILENAME


def _default_menstrual_pack() -> dict[str, Any]:
    return {
        "enabled": False,
        "anchor_iso": None,
        "cycle_length_days": DEFAULT_CYCLE_DAYS,
        "active_days": DEFAULT_ACTIVE_DAYS,
        "discomfort_profile": DEFAULT_DISCOMFORT_PROFILE,
        "pre_symptom_enabled": False,
        "last_supply_cycle_key": None,
        "supply_events": [],
    }


def _default_nocturnal_pack() -> dict[str, Any]:
    return {
        "enabled": False,
        "probability_permille": DEFAULT_PROBABILITY_PERMILLE,
        "no_absence_penalty": True,
        "generates_dream_content": False,
        "sleep_activities": {},
        "events": [],
        "pending_garment_care": False,
        "garment_care_note_cn": "",
        "garment_care_log": [],
    }


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "created_at": None,
        "updated_at": None,
        "pronoun_decoupled": True,
        "independent_of_adult_intimacy": True,
        "instance_salt": None,
        "packs": {
            PACK_MENSTRUAL: _default_menstrual_pack(),
            PACK_NOCTURNAL: _default_nocturnal_pack(),
        },
        "shared_care_hint": None,
        "partner_care_log": [],
        "operations": {},
        "next_op_seq": 0,
        "committed_op_ring": [],
    }


def _merge_pack(defaults: dict[str, Any], src: Any) -> dict[str, Any]:
    if isinstance(src, Mapping):
        for key in defaults:
            if key in src:
                defaults[key] = copy.deepcopy(src[key])
    for key in ("supply_events", "events", "garment_care_log"):
        if key in defaults and not isinstance(defaults[key], list):
            defaults[key] = []
    if "sleep_activities" in defaults and not isinstance(
        defaults["sleep_activities"], Mapping
    ):
        defaults["sleep_activities"] = {}
    return defaults


def _read_state_zero_write(root: Path | str) -> dict[str, Any]:
    """Parse the state file without creating anything.  Missing/corrupt reads empty."""

    path = state_path(root)
    try:
        if not path.is_file() or path.is_symlink():
            return _empty_state()
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _empty_state()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return _empty_state()
    if not isinstance(parsed, Mapping):
        return _empty_state()

    state = _empty_state()
    for key in (
        "schema_version",
        "revision",
        "created_at",
        "updated_at",
        "instance_salt",
    ):
        if key in parsed:
            state[key] = copy.deepcopy(parsed[key])
    for flag in ("pronoun_decoupled", "independent_of_adult_intimacy"):
        if flag in parsed:
            state[flag] = bool(parsed[flag])
    packs = parsed.get("packs")
    if isinstance(packs, Mapping):
        state["packs"][PACK_MENSTRUAL] = _merge_pack(
            _default_menstrual_pack(), packs.get(PACK_MENSTRUAL)
        )
        state["packs"][PACK_NOCTURNAL] = _merge_pack(
            _default_nocturnal_pack(), packs.get(PACK_NOCTURNAL)
        )
    hint = parsed.get("shared_care_hint")
    state["shared_care_hint"] = copy.deepcopy(hint) if isinstance(hint, Mapping) else None
    if isinstance(parsed.get("partner_care_log"), list):
        state["partner_care_log"] = [
            copy.deepcopy(row)
            for row in parsed["partner_care_log"]
            if isinstance(row, Mapping)
        ]
    if isinstance(parsed.get("operations"), Mapping):
        state["operations"] = copy.deepcopy(dict(parsed["operations"]))
    # Legacy archives lack these keys; default to 0 / [] without writing anything.
    seq = parsed.get("next_op_seq")
    if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0:
        state["next_op_seq"] = seq
    ring = parsed.get("committed_op_ring")
    if isinstance(ring, list):
        state["committed_op_ring"] = [
            copy.deepcopy(row) for row in ring if isinstance(row, Mapping)
        ]
    return state


def _atomic_write_state(root: Path | str, state: Mapping[str, Any]) -> None:
    path = state_path(root)
    if path.exists() and path.is_symlink():
        raise V08ComposableBodyError("STATE_SYMLINK", "state file is a symlink")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise V08ComposableBodyError("STATE_SYMLINK", "state directory is a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(state))
    encoded = (
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=".v08_composable_body_", suffix=".tmp", dir=str(parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
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


def _bump(state: dict[str, Any], now_iso: str) -> None:
    state["schema_version"] = SCHEMA_VERSION
    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_iso
    if not state.get("created_at"):
        state["created_at"] = now_iso


def _ensure_salt(state: dict[str, Any]) -> str:
    salt = state.get("instance_salt")
    if not isinstance(salt, str) or not salt:
        salt = os.urandom(16).hex()
        state["instance_salt"] = salt
    return salt


# ---------------------------------------------------------------------------
# Character identity (pure zero-write, neutral "TA" fallback)
# ---------------------------------------------------------------------------


def read_character_identity(root: Path | str) -> dict[str, str]:
    """Read display_name/pronouns with zero disk writes; neutral fallback ``"TA"``."""

    pack_path = Path(root) / "phone" / "character_pack_state.json"
    try:
        if pack_path.is_symlink() or not pack_path.is_file():
            return {"display_name": "TA", "pronouns": "TA"}
        raw = json.loads(pack_path.read_text(encoding="utf-8"))
        pack = raw.get("pack") if isinstance(raw, Mapping) else None
        if isinstance(pack, Mapping):
            display_name = str(pack.get("display_name") or "TA").strip() or "TA"
            pronouns = str(pack.get("pronouns") or "TA").strip() or "TA"
            return {"display_name": display_name, "pronouns": pronouns}
    except (OSError, ValueError):
        pass
    return {"display_name": "TA", "pronouns": "TA"}


# ---------------------------------------------------------------------------
# Operation-key idempotency
# ---------------------------------------------------------------------------


def _fingerprint(action: str, actor_side: str, core: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"action": action, "actor_side": actor_side, "core": core},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _core_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in ("action", "operation_key", "intent_fingerprint")
    }


def _coerce_op_seq(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _next_op_seq(state: Mapping[str, Any], operations: Mapping[str, Any]) -> int:
    """Monotonic submission sequence: ``max(next_op_seq, every row op_seq) + 1``."""

    highest = _coerce_op_seq(state.get("next_op_seq")) or 0
    for row in operations.values():
        if isinstance(row, Mapping):
            existing = _coerce_op_seq(row.get("op_seq"))
            if existing is not None and existing > highest:
                highest = existing
    return highest + 1


def _ring_replay(
    state: Mapping[str, Any], operation_key: str, fingerprint: str
) -> Optional[dict[str, Any]]:
    """Replay an evicted receipt from the bounded ``committed_op_ring`` (newest first)."""

    ring = state.get("committed_op_ring")
    if not isinstance(ring, list):
        return None
    for candidate in reversed(ring):
        if not isinstance(candidate, Mapping):
            continue
        if str(candidate.get("operation_key") or "") != operation_key:
            continue
        if str(candidate.get("fingerprint") or "") != fingerprint:
            raise V08ComposableBodyError(
                "OPERATION_KEY_CONFLICT",
                "the same operation_key was used for a different action",
            )
        receipt = candidate.get("receipt")
        out = copy.deepcopy(dict(receipt)) if isinstance(receipt, Mapping) else {}
        out["already_applied"] = True
        return out
    return None


def _check_replay(
    state: Mapping[str, Any],
    *,
    operation_key: Optional[str],
    fingerprint: str,
) -> Optional[dict[str, Any]]:
    if not operation_key:
        return None
    operations = (
        state.get("operations") if isinstance(state.get("operations"), Mapping) else {}
    )
    existing = operations.get(operation_key)
    if not isinstance(existing, Mapping):
        return _ring_replay(state, operation_key, fingerprint)
    if str(existing.get("fingerprint") or "") != fingerprint:
        raise V08ComposableBodyError(
            "OPERATION_KEY_CONFLICT",
            "the same operation_key was used for a different action",
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
    op_seq = _next_op_seq(state, operations)
    state["next_op_seq"] = op_seq
    operations[str(operation_key)] = {
        "fingerprint": fingerprint,
        "receipt": copy.deepcopy(dict(receipt)),
        "op_seq": op_seq,
    }
    ring = state.get("committed_op_ring")
    if not isinstance(ring, list):
        ring = []
    # Evict strictly by submission order: ``op_seq`` first, then the row's real
    # insertion position for legacy rows that predate the sequence (never by the
    # alphabetical order of ``sort_keys=True``).
    while len(operations) > MAX_OPERATION_RECEIPTS:
        ordered = list(operations.items())
        position = {key: index for index, (key, _row) in enumerate(ordered)}
        victim_key, victim_row = min(
            ordered,
            key=lambda item: (
                (_coerce_op_seq(item[1].get("op_seq")) if isinstance(item[1], Mapping) else 0)
                or 0,
                position[item[0]],
            ),
        )
        victim_map: Mapping[str, Any] = (
            victim_row if isinstance(victim_row, Mapping) else {}
        )
        del operations[victim_key]
        ring.append(
            {
                "operation_key": str(victim_key),
                "op_seq": _coerce_op_seq(victim_map.get("op_seq")) or 0,
                "fingerprint": str(victim_map.get("fingerprint") or ""),
                "receipt": copy.deepcopy(victim_map.get("receipt"))
                if isinstance(victim_map.get("receipt"), Mapping)
                else {},
            }
        )
    if len(ring) > MAX_COMMITTED_OP_RING:
        del ring[: len(ring) - MAX_COMMITTED_OP_RING]
    state["operations"] = operations
    state["committed_op_ring"] = ring


# ---------------------------------------------------------------------------
# Menstrual-cycle projection (pure deterministic clock math)
# ---------------------------------------------------------------------------


def _empty_menstrual_projection(pack: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(pack.get("enabled")),
        "anchor_iso": pack.get("anchor_iso"),
        "cycle_length_days": int(
            pack.get("cycle_length_days") or DEFAULT_CYCLE_DAYS
        ),
        "active_days": int(pack.get("active_days") or DEFAULT_ACTIVE_DAYS),
        "discomfort_profile": str(
            pack.get("discomfort_profile") or DEFAULT_DISCOMFORT_PROFILE
        ),
        "pre_symptom_enabled": bool(pack.get("pre_symptom_enabled")),
        "last_supply_cycle_key": pack.get("last_supply_cycle_key"),
        "days_since_anchor": None,
        "cycle_index": None,
        "day_in_cycle": None,
        "in_active_window": False,
        "in_pre_window": False,
        "needs_supply_change": False,
        "current_feeling_cn": "",
    }


def project_menstrual_cycle(
    pack: Mapping[str, Any], now_dt: datetime
) -> dict[str, Any]:
    """Deterministic cycle/clocks projection.  Never writes, never back-fills."""

    out = _empty_menstrual_projection(pack)
    if not out["enabled"]:
        return out
    anchor_dt = _parse_iso(pack.get("anchor_iso"))
    if anchor_dt is None:
        return out

    cycle_length = max(1, int(pack.get("cycle_length_days") or DEFAULT_CYCLE_DAYS))
    active_days = int(pack.get("active_days") or DEFAULT_ACTIVE_DAYS)
    delta_seconds = (now_dt - anchor_dt).total_seconds()
    days_since_anchor = max(0, int(math.floor(delta_seconds / 86400)))

    cycle_index = days_since_anchor // cycle_length
    day_in_cycle = (days_since_anchor % cycle_length) + 1
    in_active_window = 1 <= day_in_cycle <= active_days
    in_pre_window = bool(pack.get("pre_symptom_enabled")) and (
        day_in_cycle >= cycle_length - 1
    )
    cycle_key = f"{cycle_index}:{day_in_cycle}"
    needs_supply_change = bool(
        in_active_window and str(pack.get("last_supply_cycle_key")) != cycle_key
    )
    profile = str(pack.get("discomfort_profile") or DEFAULT_DISCOMFORT_PROFILE)
    feeling = (
        _qualitative_or_empty(
            _FEELING_BY_PROFILE.get(profile, ""), label="current_feeling_cn"
        )
        if in_active_window
        else ""
    )

    out.update(
        {
            "days_since_anchor": days_since_anchor,
            "cycle_index": cycle_index,
            "day_in_cycle": day_in_cycle,
            "in_active_window": bool(in_active_window),
            "in_pre_window": bool(in_pre_window),
            "needs_supply_change": needs_supply_change,
            "current_feeling_cn": feeling,
        }
    )
    return out


# ---------------------------------------------------------------------------
# Nocturnal-emission sampling (closed, hidden until realised)
# ---------------------------------------------------------------------------


def _sample_permille(salt: str, sleep_activity_id: str) -> int:
    digest = hashlib.sha256(
        f"{salt}|{sleep_activity_id}".encode("utf-8")
    ).hexdigest()
    return int(digest, 16) % 1000


def _hidden_scheduled_at(
    started_dt: datetime,
    planned_dt: datetime,
    *,
    salt: str,
    sleep_activity_id: str,
) -> datetime:
    """A deterministic hidden moment inside ``[started+45m, planned_wake-10m]``."""

    low = started_dt + timedelta(minutes=45)
    high = planned_dt - timedelta(minutes=10)
    if high <= low:
        return started_dt + (planned_dt - started_dt) / 2
    span_seconds = int((high - low).total_seconds())
    if span_seconds <= 0:
        return low
    digest = hashlib.sha256(
        f"{salt}|{sleep_activity_id}|when".encode("utf-8")
    ).hexdigest()
    offset = int(digest, 16) % span_seconds
    return low + timedelta(seconds=offset)


def _public_sleep_activity(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only what is safe: no hidden time and no unrealised sample."""

    row: dict[str, Any] = {
        "sleep_activity_id": ledger.get("sleep_activity_id"),
        "started_at_iso": ledger.get("started_at_iso"),
        "planned_wake_iso": ledger.get("planned_wake_iso"),
        "settled": bool(ledger.get("settled")),
    }
    if ledger.get("settled"):
        row["woke_at_iso"] = ledger.get("woke_at_iso")
        row["cancelled_by_early_wake"] = bool(ledger.get("cancelled_by_early_wake"))
        row["occurred"] = bool(ledger.get("occurred"))
        if ledger.get("occurred"):
            row["occurred_at_iso"] = ledger.get("hidden_scheduled_at_iso")
    return row


def _filtered_partner_care(log: Any) -> list[dict[str, Any]]:
    if not isinstance(log, list):
        return []
    rows = [copy.deepcopy(dict(row)) for row in log if isinstance(row, Mapping)]
    return rows[-MAX_PARTNER_CARE_VIEW:]


def _active_shared_hint(
    hint: Any, now_dt: datetime
) -> Optional[dict[str, Any]]:
    if not isinstance(hint, Mapping):
        return None
    expires_dt = _parse_iso(hint.get("expires_at"))
    if expires_dt is None or now_dt >= expires_dt:
        return None
    text_cn = _qualitative_or_empty(hint.get("text_cn"), label="hint_cn")
    if not text_cn:
        # A stored hint that smuggled raw telemetry is withheld, never echoed.
        return None
    return {
        "text_cn": text_cn,
        "created_at": hint.get("created_at"),
        "expires_at": hint.get("expires_at"),
    }


# ---------------------------------------------------------------------------
# Zero-write reads / projections
# ---------------------------------------------------------------------------


def read_composable_body_zero_write(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Project both packs without writing.  Partner view carries only care surfaces."""

    now_dt, now_text = _resolve_now(now_iso)
    state = _read_state_zero_write(root)
    hint = _active_shared_hint(state.get("shared_care_hint"), now_dt)
    partner_rows = _filtered_partner_care(state.get("partner_care_log"))

    if not has_subject_capability:
        return {
            "schema": VIEW_SCHEMA,
            "perspective": "partner",
            "read_only": True,
            "observed_at": now_text,
            "llm_calls": 0,
            "shared_care_hint": hint,
            "recent_partner_care": partner_rows,
        }

    packs = state.get("packs") or {}
    menstrual_pack = packs.get(PACK_MENSTRUAL) or _default_menstrual_pack()
    nocturnal_pack = packs.get(PACK_NOCTURNAL) or _default_nocturnal_pack()
    menstrual = project_menstrual_cycle(menstrual_pack, now_dt)

    sleep_activities = [
        _public_sleep_activity(ledger)
        for ledger in (nocturnal_pack.get("sleep_activities") or {}).values()
        if isinstance(ledger, Mapping)
    ]
    sleep_activities.sort(
        key=lambda row: (
            str(row.get("started_at_iso") or ""),
            str(row.get("sleep_activity_id") or ""),
        )
    )
    nocturnal = {
        "enabled": bool(nocturnal_pack.get("enabled")),
        "probability_permille": int(
            nocturnal_pack.get("probability_permille")
            if nocturnal_pack.get("probability_permille") is not None
            else DEFAULT_PROBABILITY_PERMILLE
        ),
        "no_absence_penalty": True,
        "generates_dream_content": False,
        "pending_garment_care": bool(nocturnal_pack.get("pending_garment_care")),
        "garment_care_note_cn": _qualitative_or_empty(
            nocturnal_pack.get("garment_care_note_cn"),
            label="garment_care_note_cn",
        ),
        "realized_event_count": len(
            nocturnal_pack.get("events")
            if isinstance(nocturnal_pack.get("events"), list)
            else []
        ),
        "sleep_activities": sleep_activities,
    }

    character = read_character_identity(root)
    return {
        "schema": VIEW_SCHEMA,
        "perspective": "subject",
        "read_only": True,
        "observed_at": now_text,
        "state_exists": state_path(root).is_file(),
        "revision": int(state.get("revision") or 0),
        "llm_calls": 0,
        "pronoun_decoupled": True,
        "independent_of_adult_intimacy": True,
        "character": character,
        "packs": {
            PACK_MENSTRUAL: menstrual,
            PACK_NOCTURNAL: nocturnal,
        },
        "menstrual_cycle": menstrual,
        "nocturnal_emission": nocturnal,
        "sleep_activities": sleep_activities,
        "pending_garment_care": nocturnal["pending_garment_care"],
        "garment_care_note_cn": nocturnal["garment_care_note_cn"],
        "current_feeling_cn": menstrual["current_feeling_cn"],
        "needs_supply_change": menstrual["needs_supply_change"],
        "shared_care_hint": hint,
        "recent_partner_care": partner_rows,
    }


def project_composable_body_zero_write(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Alias of :func:`read_composable_body_zero_write`; still 100% zero-write."""

    return read_composable_body_zero_write(
        root, has_subject_capability=has_subject_capability, now_iso=now_iso
    )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _prepare(
    root: Path | str,
    payload: Any,
    *,
    action: str,
    actor_side: str,
    now_iso: Optional[str],
) -> tuple[dict[str, Any], dict[str, Any], str, str, Optional[str], Optional[dict[str, Any]]]:
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    operation_key = _optional_text(
        body.get("operation_key"), max_len=120, field="operation_key"
    )
    _, now_text = _resolve_now(now_iso)
    state = _read_state_zero_write(root)
    fingerprint = _fingerprint(action, actor_side, _core_fields(body))
    replay = _check_replay(state, operation_key=operation_key, fingerprint=fingerprint)
    return body, state, now_text, fingerprint, operation_key, replay


def _require_subject(actor_side: str) -> None:
    if actor_side != "subject":
        raise V08ComposableBodyError(
            "FORBIDDEN_ACTOR_SIDE", "this action belongs to the subject side only"
        )


def _serialized_mutation(func: Any) -> Any:
    """Serialize the full read -> replay check -> validate -> mutate -> write cycle.

    The decorated mutator re-reads ``body/v08_composable_body_pack.json`` inside the
    per-path ``state_file_lock``, so two concurrent writers can never observe the same
    revision and silently overwrite each other.  Pure reads (``read_*`` / projections)
    never call this and stay lock-free and 100% zero-write.
    """

    @functools.wraps(func)
    def wrapper(root: Path | str, *args: Any, **kwargs: Any) -> Any:
        with _tx.state_file_lock(state_path(root)):
            return func(root, *args, **kwargs)

    return wrapper


@_serialized_mutation
def configure_body_pack(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Enable/disable and configure one closed body pack.  Disabling keeps history."""

    _require_subject(actor_side)
    now_dt, now_text = _resolve_now(now_iso)
    body, state, _, fingerprint, operation_key, replay = _prepare(
        root,
        payload,
        action="configure_body_pack",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    if replay is not None:
        return replay

    pack_id = _text(body.get("pack_id"), min_len=1, max_len=40, field="pack_id")
    if pack_id not in PACK_IDS:
        raise V08ComposableBodyError("PACK_UNKNOWN", f"unknown pack_id: {pack_id!r}")
    enabled = _strict_bool(body.get("enabled"), field="enabled")
    pack = state["packs"][pack_id]
    was_enabled = bool(pack.get("enabled"))

    if pack_id == PACK_MENSTRUAL:
        anchor_iso: Optional[str] = None
        if body.get("anchor_iso") is not None:
            anchor_dt = _parse_iso(body.get("anchor_iso"))
            if anchor_dt is None:
                raise V08ComposableBodyError(
                    "INVALID_FIELDS", "anchor_iso must be a valid ISO timestamp"
                )
            anchor_iso = _format_iso(anchor_dt)
        if enabled and not was_enabled and anchor_iso is None:
            raise V08ComposableBodyError(
                "INVALID_FIELDS",
                "anchor_iso is required when enabling menstrual_cycle cleanly",
            )
        if anchor_iso is not None:
            pack["anchor_iso"] = anchor_iso
        if body.get("cycle_length_days") is not None:
            pack["cycle_length_days"] = _int_in_range(
                body["cycle_length_days"],
                low=MIN_CYCLE_DAYS,
                high=MAX_CYCLE_DAYS,
                field="cycle_length_days",
            )
        if body.get("active_days") is not None:
            pack["active_days"] = _int_in_range(
                body["active_days"],
                low=MIN_ACTIVE_DAYS,
                high=MAX_ACTIVE_DAYS,
                field="active_days",
            )
        if body.get("discomfort_profile") is not None:
            profile = body["discomfort_profile"]
            if profile not in MENSTRUAL_DISCOMFORT_PROFILES:
                raise V08ComposableBodyError(
                    "INVALID_FIELDS",
                    "discomfort_profile must be none, mild_cramps, mild_fatigue"
                    " or cramps_and_fatigue",
                )
            pack["discomfort_profile"] = profile
        if body.get("pre_symptom_enabled") is not None:
            pack["pre_symptom_enabled"] = _strict_bool(
                body["pre_symptom_enabled"], field="pre_symptom_enabled"
            )
        if enabled and not was_enabled:
            # A clean enable starts a fresh anchor: today's supply is not assumed
            # changed just because an older cycle shared the same index key.
            pack["last_supply_cycle_key"] = None
        pack["enabled"] = enabled
        projection = project_menstrual_cycle(pack, now_dt)
        receipt = {
            "status": "applied",
            "pack_id": pack_id,
            "enabled": enabled,
            "history_deleted": False,
            "anchor_iso": pack.get("anchor_iso"),
            "cycle_length_days": pack["cycle_length_days"],
            "active_days": pack["active_days"],
            "discomfort_profile": pack["discomfort_profile"],
            "pre_symptom_enabled": pack["pre_symptom_enabled"],
            "current_feeling_cn": projection["current_feeling_cn"],
            "needs_supply_change": projection["needs_supply_change"],
        }
    else:  # PACK_NOCTURNAL
        if body.get("probability_permille") is not None:
            pack["probability_permille"] = _int_in_range(
                body["probability_permille"],
                low=MIN_PROBABILITY_PERMILLE,
                high=MAX_PROBABILITY_PERMILLE,
                field="probability_permille",
            )
        pack["enabled"] = enabled
        pack["no_absence_penalty"] = True
        pack["generates_dream_content"] = False
        if not enabled:
            # Stop future events and drop the active care note; realised event
            # receipts stay in history (``history_deleted`` is false).
            pack["pending_garment_care"] = False
            pack["garment_care_note_cn"] = ""
        receipt = {
            "status": "applied",
            "pack_id": pack_id,
            "enabled": enabled,
            "history_deleted": False,
            "probability_permille": pack["probability_permille"],
            "no_absence_penalty": True,
            "generates_dream_content": False,
        }

    _bump(state, now_text)
    _store_operation(
        state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


@_serialized_mutation
def use_cycle_supply(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Mark today's supply as used so ``needs_supply_change`` clears honestly."""

    _require_subject(actor_side)
    now_dt, now_text = _resolve_now(now_iso)
    body, state, _, fingerprint, operation_key, replay = _prepare(
        root,
        payload,
        action="use_cycle_supply",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    if replay is not None:
        return replay

    pack = state["packs"][PACK_MENSTRUAL]
    if not pack.get("enabled"):
        raise V08ComposableBodyError(
            "PACK_DISABLED", "menstrual_cycle is disabled; no supply can be consumed"
        )
    supply_object_id = _optional_text(
        body.get("supply_object_id"), max_len=64, field="supply_object_id"
    )
    note_cn = _optional_text(body.get("note_cn"), max_len=100, field="note_cn")

    projection = project_menstrual_cycle(pack, now_dt)
    cycle_key = f"{projection['cycle_index']}:{projection['day_in_cycle']}"
    pack["last_supply_cycle_key"] = cycle_key
    events = pack.get("supply_events")
    if not isinstance(events, list):
        events = []
    events.append(
        {
            "at": now_text,
            "cycle_key": cycle_key,
            "supply_object_id": supply_object_id,
            "note_cn": note_cn,
        }
    )
    pack["supply_events"] = events[-MAX_EVENT_ROWS:]

    receipt = {
        "status": "applied",
        "pack_id": PACK_MENSTRUAL,
        "supply_used": True,
        "cycle_key": cycle_key,
        "needs_supply_change": False,
        "history_deleted": False,
    }
    _bump(state, now_text)
    _store_operation(
        state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


@_serialized_mutation
def bind_sleep_activity(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Bind a real sleep activity; sample exactly once and keep it hidden."""

    _require_subject(actor_side)
    _, now_text = _resolve_now(now_iso)
    body, state, _, fingerprint, operation_key, replay = _prepare(
        root,
        payload,
        action="bind_sleep_activity",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    if replay is not None:
        return replay

    pack = state["packs"][PACK_NOCTURNAL]
    if not pack.get("enabled"):
        raise V08ComposableBodyError(
            "PACK_DISABLED", "nocturnal_emission is disabled; no sleep can be bound"
        )

    sleep_activity_id = _text(
        body.get("sleep_activity_id"),
        min_len=1,
        max_len=64,
        field="sleep_activity_id",
    )
    started_dt = _parse_iso(body.get("started_at_iso"))
    planned_dt = _parse_iso(body.get("planned_wake_iso"))
    if started_dt is None or planned_dt is None:
        raise V08ComposableBodyError(
            "INVALID_FIELDS", "started_at_iso and planned_wake_iso must be valid ISO"
        )
    if planned_dt <= started_dt:
        raise V08ComposableBodyError(
            "INVALID_FIELDS", "planned_wake_iso must be later than started_at_iso"
        )

    activities = pack.get("sleep_activities")
    if not isinstance(activities, dict):
        activities = {}
    existing = activities.get(sleep_activity_id)
    if isinstance(existing, Mapping):
        # Re-submitting / restarting never re-rolls the same sleep activity.
        return {
            "status": "applied",
            "already_applied": True,
            "pack_id": PACK_NOCTURNAL,
            "sleep_activity_id": sleep_activity_id,
            "bound": True,
            "started_at_iso": existing.get("started_at_iso"),
            "planned_wake_iso": existing.get("planned_wake_iso"),
        }

    salt = _ensure_salt(state)
    sampled = _sample_permille(salt, sleep_activity_id) < int(
        pack.get("probability_permille") or 0
    )
    scheduled_iso: Optional[str] = None
    if sampled:
        scheduled_iso = _format_iso(
            _hidden_scheduled_at(
                started_dt, planned_dt, salt=salt, sleep_activity_id=sleep_activity_id
            )
        )

    activities[sleep_activity_id] = {
        "sleep_activity_id": sleep_activity_id,
        "started_at_iso": _format_iso(started_dt),
        "planned_wake_iso": _format_iso(planned_dt),
        "sampled_occurred": bool(sampled),
        "hidden_scheduled_at_iso": scheduled_iso,
        "settled": False,
        "occurred": False,
        "cancelled_by_early_wake": False,
        "event_recorded": False,
        "woke_at_iso": None,
        "bound_at": now_text,
    }
    pack["sleep_activities"] = activities

    receipt = {
        "status": "applied",
        "pack_id": PACK_NOCTURNAL,
        "sleep_activity_id": sleep_activity_id,
        "bound": True,
        "started_at_iso": _format_iso(started_dt),
        "planned_wake_iso": _format_iso(planned_dt),
        "history_deleted": False,
    }
    _bump(state, now_text)
    _store_operation(
        state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


def auto_bind_world_sleep_if_enabled(
    day_root: Path | str,
    *,
    sleep_activity_id: str,
    started_at_iso: str,
    planned_wake_iso: str,
) -> dict[str, Any]:
    """Bind a real World ``REST(sleep_like)`` session when ``nocturnal_emission`` is enabled.

    Pure 100% zero-write no-op (creates no files, directories, or lockfiles) when
    ``body/v08_composable_body_pack.json`` is absent or ``nocturnal_emission`` is disabled.
    """

    paths = _instance_paths.resolve_instance_paths(day_root)
    resolved_root = paths.day_root
    clean_sleep_id = str(sleep_activity_id or "").strip()[:64]

    pack_path = state_path(resolved_root)
    if pack_path.is_symlink() or not pack_path.is_file():
        return {
            "ok": True,
            "bound": False,
            "skipped": True,
            "already_applied": False,
            "reason_code": "PACK_DISABLED",
            "pack_id": PACK_NOCTURNAL,
            "sleep_activity_id": clean_sleep_id,
            "writes_performed": 0,
            "llm_calls": 0,
        }

    state = _read_state_zero_write(resolved_root)
    nocturnal_pack = (state.get("packs") or {}).get(PACK_NOCTURNAL)
    if not isinstance(nocturnal_pack, Mapping) or not bool(nocturnal_pack.get("enabled")):
        return {
            "ok": True,
            "bound": False,
            "skipped": True,
            "already_applied": False,
            "reason_code": "PACK_DISABLED",
            "pack_id": PACK_NOCTURNAL,
            "sleep_activity_id": clean_sleep_id,
            "writes_performed": 0,
            "llm_calls": 0,
        }

    if not clean_sleep_id:
        return {
            "ok": False,
            "bound": False,
            "skipped": True,
            "already_applied": False,
            "reason_code": "INVALID_SLEEP_ACTIVITY_ID",
            "pack_id": PACK_NOCTURNAL,
            "sleep_activity_id": "",
            "writes_performed": 0,
            "llm_calls": 0,
        }

    started_dt = _parse_iso(started_at_iso)
    planned_dt = _parse_iso(planned_wake_iso)
    if started_dt is None or planned_dt is None or planned_dt <= started_dt:
        return {
            "ok": False,
            "bound": False,
            "skipped": True,
            "already_applied": False,
            "reason_code": "INVALID_SLEEP_WINDOW",
            "pack_id": PACK_NOCTURNAL,
            "sleep_activity_id": clean_sleep_id,
            "writes_performed": 0,
            "llm_calls": 0,
        }

    norm_started = _format_iso(started_dt)
    norm_planned = _format_iso(planned_dt)
    op_key = f"auto-world-sleep:{clean_sleep_id}"[:120]
    try:
        receipt = bind_sleep_activity(
            resolved_root,
            {
                "sleep_activity_id": clean_sleep_id,
                "started_at_iso": norm_started,
                "planned_wake_iso": norm_planned,
                "operation_key": op_key,
            },
            actor_side="subject",
            now_iso=norm_started,
        )
    except V08ComposableBodyError as exc:
        return {
            "ok": exc.reason_code == "PACK_DISABLED",
            "bound": False,
            "skipped": True,
            "already_applied": False,
            "reason_code": exc.reason_code,
            "pack_id": PACK_NOCTURNAL,
            "sleep_activity_id": clean_sleep_id,
            "writes_performed": 0,
            "llm_calls": 0,
        }

    already = bool(receipt.get("already_applied", False))
    return {
        "ok": True,
        "bound": bool(receipt.get("bound", True)),
        "skipped": False,
        "already_applied": already,
        "reason_code": "ALREADY_APPLIED" if already else None,
        "pack_id": PACK_NOCTURNAL,
        "sleep_activity_id": str(receipt.get("sleep_activity_id") or clean_sleep_id),
        "started_at_iso": receipt.get("started_at_iso") or norm_started,
        "planned_wake_iso": receipt.get("planned_wake_iso") or norm_planned,
        "writes_performed": 0 if already else 1,
        "llm_calls": 0,
    }


@_serialized_mutation
def settle_sleep_wake(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Settle a wake; early wake cancels cleanly, otherwise record at most once."""

    _require_subject(actor_side)
    _, now_text = _resolve_now(now_iso)
    body, state, _, fingerprint, operation_key, replay = _prepare(
        root,
        payload,
        action="settle_sleep_wake",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    if replay is not None:
        return replay

    pack = state["packs"][PACK_NOCTURNAL]
    sleep_activity_id = _text(
        body.get("sleep_activity_id"),
        min_len=1,
        max_len=64,
        field="sleep_activity_id",
    )
    activities = pack.get("sleep_activities")
    if not isinstance(activities, Mapping):
        activities = {}
    ledger = activities.get(sleep_activity_id)
    if not isinstance(ledger, dict):
        raise V08ComposableBodyError(
            "SLEEP_ACTIVITY_NOT_FOUND", "no bound sleep activity with that id"
        )
    woke_dt = _parse_iso(body.get("woke_at_iso"))
    if woke_dt is None:
        raise V08ComposableBodyError(
            "INVALID_FIELDS", "woke_at_iso must be a valid ISO timestamp"
        )

    if ledger.get("settled"):
        return {
            "status": "applied",
            "already_applied": True,
            "pack_id": PACK_NOCTURNAL,
            "sleep_activity_id": sleep_activity_id,
            "settled": True,
            "occurred": bool(ledger.get("occurred")),
            "cancelled_by_early_wake": bool(ledger.get("cancelled_by_early_wake")),
            "pending_garment_care": bool(pack.get("pending_garment_care")),
        }

    scheduled_dt = _parse_iso(ledger.get("hidden_scheduled_at_iso"))
    cancelled = False
    occurred = False
    if scheduled_dt is not None and woke_dt < scheduled_dt:
        cancelled = True
        occurred = False
    elif bool(ledger.get("sampled_occurred")):
        occurred = True
        events = pack.get("events")
        if not isinstance(events, list):
            events = []
        if not ledger.get("event_recorded"):
            events.append(
                {
                    "sleep_activity_id": sleep_activity_id,
                    "occurred_at_iso": ledger.get("hidden_scheduled_at_iso"),
                    "recorded_at": now_text,
                }
            )
            pack["events"] = events[-MAX_EVENT_ROWS:]
            ledger["event_recorded"] = True
        pack["pending_garment_care"] = True
        pack["garment_care_note_cn"] = GARMENT_CARE_NOTE_CN
    else:
        occurred = False

    ledger["settled"] = True
    ledger["woke_at_iso"] = _format_iso(woke_dt)
    ledger["cancelled_by_early_wake"] = bool(cancelled)
    ledger["occurred"] = bool(occurred)
    activities[sleep_activity_id] = ledger
    pack["sleep_activities"] = dict(activities)

    receipt = {
        "status": "applied",
        "pack_id": PACK_NOCTURNAL,
        "sleep_activity_id": sleep_activity_id,
        "settled": True,
        "occurred": bool(occurred),
        "cancelled_by_early_wake": bool(cancelled),
        "pending_garment_care": bool(pack.get("pending_garment_care")),
        "history_deleted": False,
    }
    _bump(state, now_text)
    _store_operation(
        state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


@_serialized_mutation
def resolve_garment_care(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Clear a pending garment-care note with a brief factual receipt."""

    _require_subject(actor_side)
    _, now_text = _resolve_now(now_iso)
    body, state, _, fingerprint, operation_key, replay = _prepare(
        root,
        payload,
        action="resolve_garment_care",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    if replay is not None:
        return replay

    pack = state["packs"][PACK_NOCTURNAL]
    if not pack.get("pending_garment_care"):
        raise V08ComposableBodyError(
            "NO_PENDING_GARMENT_CARE", "there is no pending garment care to resolve"
        )
    mode = body.get("mode")
    if mode not in GARMENT_CARE_MODES:
        raise V08ComposableBodyError(
            "INVALID_FIELDS", "mode must be change_garment or wash_garment"
        )
    garment_object_id = _optional_text(
        body.get("garment_object_id"), max_len=64, field="garment_object_id"
    )

    pack["pending_garment_care"] = False
    pack["garment_care_note_cn"] = ""
    log = pack.get("garment_care_log")
    if not isinstance(log, list):
        log = []
    log.append(
        {
            "mode": mode,
            "garment_object_id": garment_object_id,
            "at": now_text,
        }
    )
    pack["garment_care_log"] = log[-MAX_EVENT_ROWS:]

    receipt = {
        "status": "applied",
        "pack_id": PACK_NOCTURNAL,
        "mode": mode,
        "pending_garment_care": False,
        "history_deleted": False,
    }
    _bump(state, now_text)
    _store_operation(
        state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


@_serialized_mutation
def clear_private_body_history(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Explicitly delete stored sleep/supply logs; pack configuration is kept."""

    _require_subject(actor_side)
    _, now_text = _resolve_now(now_iso)
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    if body.get("confirm") is not True:
        raise V08ComposableBodyError(
            "CONFIRMATION_REQUIRED", "clear_private_body_history requires confirm=true"
        )

    state = _read_state_zero_write(root)
    menstrual = state["packs"][PACK_MENSTRUAL]
    nocturnal = state["packs"][PACK_NOCTURNAL]
    menstrual["supply_events"] = []
    menstrual["last_supply_cycle_key"] = None
    nocturnal["sleep_activities"] = {}
    nocturnal["events"] = []
    nocturnal["garment_care_log"] = []
    nocturnal["pending_garment_care"] = False
    nocturnal["garment_care_note_cn"] = ""
    # Replay receipts referencing deleted history must not survive the deletion.
    state["operations"] = {}
    state["committed_op_ring"] = []

    receipt = {
        "status": "applied",
        "history_deleted": True,
        "cleared_at": now_text,
    }
    _bump(state, now_text)
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


@_serialized_mutation
def share_body_care_hint_once(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Publish one expiring care hint the partner may see, with no score coupling."""

    _require_subject(actor_side)
    now_dt, now_text = _resolve_now(now_iso)
    body, state, _, fingerprint, operation_key, replay = _prepare(
        root,
        payload,
        action="share_body_care_hint_once",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    if replay is not None:
        return replay

    hint_cn = _text(body.get("hint_cn"), min_len=2, max_len=100, field="hint_cn")
    if not _qualitative_copy_allowed(hint_cn, label="hint_cn"):
        raise V08ComposableBodyError(
            "INVALID_FIELDS",
            "hint_cn must stay qualitative and carry no raw body value",
        )
    ttl_minutes = DEFAULT_HINT_TTL_MINUTES
    if body.get("ttl_minutes") is not None:
        ttl_minutes = _int_in_range(
            body["ttl_minutes"],
            low=MIN_HINT_TTL_MINUTES,
            high=MAX_HINT_TTL_MINUTES,
            field="ttl_minutes",
        )
    expires_at = _format_iso(now_dt + timedelta(minutes=ttl_minutes))
    state["shared_care_hint"] = {
        "text_cn": hint_cn,
        "created_at": now_text,
        "expires_at": expires_at,
    }

    receipt = {
        "status": "applied",
        "shared": True,
        "shared_care_hint": {
            "text_cn": hint_cn,
            "created_at": now_text,
            "expires_at": expires_at,
        },
        "history_deleted": False,
    }
    _bump(state, now_text)
    _store_operation(
        state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


@_serialized_mutation
def offer_ordinary_care(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Append one ordinary factual partner care act; zero relationship score."""

    if actor_side not in ("partner", "subject"):
        raise V08ComposableBodyError(
            "FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}"
        )
    _, now_text = _resolve_now(now_iso)
    body, state, _, fingerprint, operation_key, replay = _prepare(
        root,
        payload,
        action="offer_ordinary_care",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    if replay is not None:
        return replay

    care_kind = body.get("care_kind")
    if care_kind not in CARE_KINDS:
        raise V08ComposableBodyError(
            "INVALID_FIELDS",
            "care_kind must be warm_pad, warm_drink, fresh_supplies or quiet_space",
        )
    note_cn = _optional_text(body.get("note_cn"), max_len=100, field="note_cn")
    log = state.get("partner_care_log")
    if not isinstance(log, list):
        log = []
    log.append({"care_kind": care_kind, "note_cn": note_cn, "at": now_text})
    state["partner_care_log"] = log[-MAX_PARTNER_CARE_LOG:]

    receipt = {
        "status": "applied",
        "care_kind": care_kind,
        "note_cn": note_cn,
        "relationship_score_unchanged": True,
        "history_deleted": False,
    }
    _bump(state, now_text)
    _store_operation(
        state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, state)
    return copy.deepcopy(receipt)


__all__ = [
    "CARE_KINDS",
    "DEFAULT_ACTIVE_DAYS",
    "DEFAULT_CYCLE_DAYS",
    "DEFAULT_DISCOMFORT_PROFILE",
    "DEFAULT_HINT_TTL_MINUTES",
    "DEFAULT_PROBABILITY_PERMILLE",
    "FORBIDDEN_IDENTITY_KEYS",
    "GARMENT_CARE_MODES",
    "GARMENT_CARE_NOTE_CN",
    "MAX_PARTNER_CARE_VIEW",
    "MENSTRUAL_DISCOMFORT_PROFILES",
    "PACK_IDS",
    "PACK_MENSTRUAL",
    "PACK_NOCTURNAL",
    "SCHEMA_VERSION",
    "STATE_DIRNAME",
    "STATE_FILENAME",
    "V08ComposableBodyError",
    "VIEW_SCHEMA",
    "auto_bind_world_sleep_if_enabled",
    "bind_sleep_activity",
    "clear_private_body_history",
    "configure_body_pack",
    "offer_ordinary_care",
    "project_composable_body_zero_write",
    "project_menstrual_cycle",
    "read_character_identity",
    "read_composable_body_zero_write",
    "resolve_garment_care",
    "settle_sleep_wake",
    "share_body_care_hint_once",
    "state_path",
    "use_cycle_supply",
]
