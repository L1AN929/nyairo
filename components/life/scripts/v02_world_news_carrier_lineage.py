#!/usr/bin/env python3
"""v0.2 Wave B2 domain engine (deterministic, zero-LLM):
- F-018 / F-019: External Fact Source Registry & Real vs Fictional World News Layering
- F-029 / F-030 / F-031: Body State Life History & Multi-Carrier Adaptation
- F-144 / F-145 / F-146: Backup-Restore Honesty, Inherited Meaning Versions & Fork Lineage

All read paths are strictly 100% zero-write (no directories, files, or lock files created).
All write paths perform zero-write preflight validation and idempotency/conflict checks
before acquiring ``state_tx_guard.state_file_lock`` and persisting atomically via
``state_tx_guard.atomic_write_json``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from components.life.scripts import state_tx_guard as _tx_guard
except ImportError:
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import state_tx_guard as _tx_guard  # type: ignore[no-redef]


STATE_SCHEMA = "nyairo.v02.world_news_carrier_lineage.v1"
STATE_REL_PATH: tuple[str, ...] = ("state", "v02_world_news_carrier_lineage.json")
LOCK_REL_PATH: tuple[str, ...] = (".locks", "v02_world_news_carrier_lineage.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

CONTROL_ROOT_ENV_VAR = "NYAIRO_V02_CONTROL_ROOT"
RUNTIME_MIGRATION_REL_PATH: tuple[str, ...] = ("phone", "v02_runtime_migration.json")
WORLD_AMBIENT_EVENTS_REL_PATH: tuple[str, ...] = ("world", "world_ambient_events.json")
V04_NEIGHBORHOOD_REL_PATH: tuple[str, ...] = ("world", "v04_neighborhood_state.json")

MAX_FACT_SOURCES = 120
MAX_LAYERED_NEWS = 160
MAX_BODY_HISTORY = 60
MAX_CARRIER_SWITCH_HISTORY = 20
MAX_MEANING_VERSIONS = 200
MAX_PARTNER_MEANING_NOTES = 120
MAX_LINEAGE_BRANCHES = 80
MAX_OPERATION_RECEIPTS = 200
MAX_COMMITTED_RING = 1024

SOURCE_KINDS: frozenset[str] = frozenset({
    "article",
    "book",
    "website",
    "person_note",
    "rss",
})

TRUST_TIERS: frozenset[str] = frozenset({
    "PRIMARY_RECORD",
    "TRUSTED_REFERENCE",
    "UNVERIFIED_HEARSAY",
})

NEWS_LAYERS: frozenset[str] = frozenset({
    "LOCAL_WORLD_NEWS",
    "EXTERNAL_REAL_FACT",
    "UNVERIFIED_HEARSAY",
})

LAYER_LABELS_CN: dict[str, str] = {
    "LOCAL_WORLD_NEWS": "小镇虚构动态",
    "EXTERNAL_REAL_FACT": "外部现实信息·独立于小镇",
    "UNVERIFIED_HEARSAY": "口头听闻·未核证",
}

CARRIER_KINDS: frozenset[str] = frozenset({
    "desktop_gui",
    "pocket_web",
    "headless_cli",
    "mobile_bridge",
})

CARRIER_DEFAULT_LABELS_CN: dict[str, str] = {
    "desktop_gui": "桌面完整载体",
    "pocket_web": "随身网页载体",
    "headless_cli": "无头命令行载体",
    "mobile_bridge": "移动端桥接载体",
}

CARRIER_CAPABILITY_DEFAULTS: dict[str, dict[str, bool]] = {
    "desktop_gui": {
        "visual_preview": True,
        "audio_output": True,
        "desk_stage": True,
        "camera_input": True,
    },
    "pocket_web": {
        "visual_preview": True,
        "audio_output": True,
        "desk_stage": False,
        "camera_input": False,
    },
    "headless_cli": {
        "visual_preview": False,
        "audio_output": False,
        "desk_stage": False,
        "camera_input": False,
    },
    "mobile_bridge": {
        "visual_preview": True,
        "audio_output": False,
        "desk_stage": False,
        "camera_input": True,
    },
}

FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "actor",
    "has_subject_capability",
    "capability_token",
    "subject_id",
    "grant_id",
    "private_override",
    "intimacy_score",
    "affection_score",
    "love_points",
    "obedience_score",
    "trust_score",
    "mood_score",
    "life_score",
    "auto_locked",
    "uid",
    "euid",
    "gid",
    "authority",
    "authorities",
    "verified_main_self",
    "main_self_verified",
    "__proto__",
    "constructor",
    "prototype",
})

_FILE_URI_RE = re.compile(r"file://[^\s\"'<>]+", re.IGNORECASE)
_WIN_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"'<>]+")
_UNC_PATH_RE = re.compile(r"\\\\[^\s\"'<>]+")
_POSIX_HOST_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:./-])/(?:Users|home|mnt|tmp|var|etc|private|root|opt|proc|sys)(?:/[^\s\"'<>]*)?"
)
_PARENT_TRAVERSAL_RE = re.compile(r"(?:^|(?<=\s))(?:\.\.[\\/])+[^\s\"'<>]*")

_URL_SCHEME_RE = re.compile(r"(?:https?|ftp)://", re.IGNORECASE)
_WWW_HOST_RE = re.compile(r"(?:^|[\s(])www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.IGNORECASE)
_BARE_EXTERNAL_DOMAIN_RE = re.compile(
    r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|gov|edu|io|cn|jp|uk|de|fr|news|co|app|dev|info|tv|ai)(?:[:/].*)?$",
    re.IGNORECASE,
)


class V02WorldLineageError(Exception):
    """Structured domain exception for Wave B2 world-news / carrier / lineage engine."""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"{self.reason_code}: {self.message}")


# ---------------------------------------------------------------------------
# Basic validation, path scrubbing, and time utilities
# ---------------------------------------------------------------------------


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith(("Z", "z")):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_iso(now_iso: Optional[str] = None) -> str:
    if now_iso is not None:
        if not isinstance(now_iso, str) or not now_iso.strip() or _parse_iso_utc(now_iso) is None:
            raise V02WorldLineageError(400, "INVALID_FIELDS", f"Malformed ISO timestamp: {now_iso!r}")
        return now_iso.strip()
    return datetime.now(tz=DEFAULT_TZ).isoformat(timespec="seconds")


def _normalize_actor_side(actor_side: Any) -> str:
    if not isinstance(actor_side, str) or not actor_side.strip():
        raise V02WorldLineageError(403, "FORBIDDEN_ACTOR_SIDE", "Invalid actor_side")
    norm = actor_side.strip().lower()
    if norm not in ("partner", "subject"):
        raise V02WorldLineageError(403, "FORBIDDEN_ACTOR_SIDE", f"Unsupported actor_side: {actor_side!r}")
    return norm


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _scan_forbidden_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise V02WorldLineageError(400, "INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise V02WorldLineageError(
                    400,
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded key forbidden: {raw_key!r}",
                )
            if (
                raw_key in FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
                or raw_key.lower() in FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
            ):
                raise V02WorldLineageError(
                    400,
                    "INVALID_FIELDS",
                    f"Forged identity or forbidden key rejected: {raw_key!r}",
                )
            _scan_forbidden_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_forbidden_keys_recursive(item, depth=depth + 1)


def _require_str(value: Any, field: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} must be a string")
    if "\x00" in value:
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} contains NUL byte")
    trimmed = value.strip()
    if not trimmed:
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} must not be blank")
    if len(trimmed) > max_len:
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} exceeds max length {max_len}")
    return trimmed


def _optional_str(value: Any, field: str, *, max_len: int = 320) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} must be a string")
    if "\x00" in value:
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) > max_len:
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} exceeds max length {max_len}")
    return trimmed


def _optional_bool(payload: Mapping[str, Any], field: str, default: bool) -> bool:
    if field not in payload or payload[field] is None:
        return default
    val = payload[field]
    if not isinstance(val, bool):
        raise V02WorldLineageError(400, "INVALID_FIELDS", f"Field {field!r} must be a boolean")
    return val


def _scrub_host_paths(text: str) -> str:
    if not text:
        return ""
    out = _FILE_URI_RE.sub("[local-path-redacted]", text)
    out = _WIN_DRIVE_PATH_RE.sub("[local-path-redacted]", out)
    out = _UNC_PATH_RE.sub("[local-path-redacted]", out)
    out = _POSIX_HOST_PATH_RE.sub("[local-path-redacted]", out)
    out = _PARENT_TRAVERSAL_RE.sub("[local-path-redacted]", out)
    return out


def _scrub_value_recursive(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_host_paths(value)
    if isinstance(value, list):
        return [_scrub_value_recursive(item) for item in value]
    if isinstance(value, tuple):
        return [_scrub_value_recursive(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _scrub_value_recursive(v) for k, v in value.items()}
    return value


def _sanitize_source_ref(raw_ref: str) -> str:
    return _scrub_host_paths(raw_ref.strip())


def _looks_like_external_url(raw_ref: str) -> bool:
    if not raw_ref:
        return False
    cleaned = raw_ref.strip()
    if not cleaned:
        return False
    low = cleaned.lower()
    if low.startswith(("http://", "https://", "ftp://", "www.")):
        return True
    if _URL_SCHEME_RE.search(cleaned):
        return True
    if _WWW_HOST_RE.search(cleaned):
        return True
    if _BARE_EXTERNAL_DOMAIN_RE.match(cleaned):
        return True
    return False


# ---------------------------------------------------------------------------
# State file I/O (zero-write reads + atomic locked writes)
# ---------------------------------------------------------------------------


def _state_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _lock_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*LOCK_REL_PATH)


def _is_safe_nonsymlink_rel(root: Path, rel_parts: tuple[str, ...]) -> bool:
    try:
        if os.path.lexists(root) and root.is_symlink():
            return False
    except OSError:
        return False
    cur = root
    for part in rel_parts:
        cur = cur / part
        try:
            if os.path.lexists(cur) and cur.is_symlink():
                return False
        except OSError:
            return False
    return True


def _read_json_zero_write(root: Path, rel_parts: tuple[str, ...]) -> tuple[bool, Any]:
    """Read JSON from ``root/rel_parts`` with strictly zero writes and symlink guard."""
    if not _is_safe_nonsymlink_rel(root, rel_parts):
        return False, None
    path = root.joinpath(*rel_parts)
    try:
        if not path.is_file():
            return False, None
        raw = path.read_text(encoding="utf-8")
        return True, json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError):
        return True, None


def build_default_state() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "next_seq": 1,
        "next_op_seq": 0,
        "fact_sources": [],
        "layered_news": [],
        "body_history": [],
        "carrier_adaptation": None,
        "carrier_switch_history": [],
        "meaning_versions": [],
        "partner_meaning_notes": [],
        "lineage_branches": [],
        "operations": {},
        "committed_op_ring": [],
    }


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    root = Path(day_root)
    exists, parsed = _read_json_zero_write(root, STATE_REL_PATH)
    base = build_default_state()
    if exists and isinstance(parsed, Mapping) and parsed.get("schema") == STATE_SCHEMA:
        base.update(copy.deepcopy(dict(parsed)))
    for list_key in (
        "fact_sources",
        "layered_news",
        "body_history",
        "carrier_switch_history",
        "meaning_versions",
        "partner_meaning_notes",
        "lineage_branches",
        "committed_op_ring",
    ):
        if not isinstance(base.get(list_key), list):
            base[list_key] = []
    if not isinstance(base.get("operations"), dict):
        base["operations"] = {}
    if not _is_strict_int(base.get("revision")) or base["revision"] < 0:
        base["revision"] = 0
    if not _is_strict_int(base.get("next_seq")) or base["next_seq"] < 1:
        base["next_seq"] = 1
    if not _is_strict_int(base.get("next_op_seq")) or base["next_op_seq"] < 0:
        base["next_op_seq"] = 0
    return bool(exists and isinstance(parsed, Mapping)), base


def _allocate_id(state: dict[str, Any], prefix: str) -> str:
    seq = int(state.get("next_seq") or 1)
    state["next_seq"] = seq + 1
    return f"{prefix}-{seq:04d}"


def _assert_write_paths_safe(root: Path) -> None:
    if not _is_safe_nonsymlink_rel(root, STATE_REL_PATH):
        raise V02WorldLineageError(503, "STATE_SYMLINK_REFUSED", "State path contains symlink")
    if not _is_safe_nonsymlink_rel(root, LOCK_REL_PATH):
        raise V02WorldLineageError(503, "STATE_SYMLINK_REFUSED", "Lock path contains symlink")


def _run_locked_tx(
    day_root: Path | str,
    *,
    action_name: str,
    actor_side: str,
    payload: Mapping[str, Any],
    now_iso: Optional[str],
    mutator: Callable[[dict[str, Any], str], dict[str, Any]],
    preflight: Optional[Callable[[dict[str, Any], str], None]] = None,
) -> dict[str, Any]:
    """Execute one atomic RMW transaction with zero-write preflight before locking."""
    root = Path(day_root)
    stamp = _now_iso(now_iso)

    expected_rev = payload.get("expected_revision")
    if expected_rev is not None:
        if not _is_strict_int(expected_rev) or expected_rev < 0:
            raise V02WorldLineageError(
                400,
                "INVALID_FIELDS",
                "expected_revision must be a non-negative integer",
            )

    op_key = _optional_str(payload.get("operation_key"), "operation_key", max_len=128)
    fp = _tx_guard.payload_fingerprint({
        "action": action_name,
        "actor_side": actor_side,
        "payload": dict(payload),
    })

    # Zero-write preflight before creating any directories or lock files.
    pre_exists, pre_state = _load_state_readonly(root)
    if expected_rev is not None and int(pre_state.get("revision") or 0) != expected_rev:
        raise V02WorldLineageError(409, "REVISION_MISMATCH", "State revision does not match expected_revision")

    if op_key and pre_exists:
        kind, existing = _tx_guard.reconcile_evicted_or_active_op(
            root,
            pre_state,
            op_key,
            dict_key="operations",
            expected_revision=expected_rev,
            payload_hash=fp,
        )
        if kind == "conflict":
            raise V02WorldLineageError(
                409,
                "OPERATION_KEY_CONFLICT",
                f"Conflicting payload for operation_key={op_key!r}",
            )
        if kind == "expired":
            raise V02WorldLineageError(409, "RETRY_WINDOW_EXPIRED", "Operation key retry window expired")
        if kind in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
            replay = copy.deepcopy(dict(existing.get("result") or existing))
            replay["idempotent_replay"] = True
            replay["command_status"] = "DUPLICATE"
            replay["revision"] = int(existing.get("revision") or pre_state.get("revision") or 0)
            replay["llm_calls"] = 0
            return replay

    if preflight is not None:
        preflight(pre_state, stamp)

    _assert_write_paths_safe(root)

    with _tx_guard.state_file_lock(_lock_path(root)):
        _exists, state = _load_state_readonly(root)

        if expected_rev is not None and int(state.get("revision") or 0) != expected_rev:
            raise V02WorldLineageError(
                409,
                "REVISION_MISMATCH",
                "State revision does not match expected_revision",
            )

        if op_key:
            kind, existing = _tx_guard.reconcile_evicted_or_active_op(
                root,
                state,
                op_key,
                dict_key="operations",
                expected_revision=expected_rev,
                payload_hash=fp,
            )
            if kind == "conflict":
                raise V02WorldLineageError(
                    409,
                    "OPERATION_KEY_CONFLICT",
                    f"Conflicting payload for operation_key={op_key!r}",
                )
            if kind == "expired":
                raise V02WorldLineageError(409, "RETRY_WINDOW_EXPIRED", "Operation key retry window expired")
            if kind in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
                replay = copy.deepcopy(dict(existing.get("result") or existing))
                replay["idempotent_replay"] = True
                replay["command_status"] = "DUPLICATE"
                replay["revision"] = int(existing.get("revision") or state.get("revision") or 0)
                replay["llm_calls"] = 0
                return replay

        if preflight is not None:
            preflight(state, stamp)

        current_rev = int(state.get("revision") or 0)
        result = mutator(state, stamp)
        new_rev = current_rev + 1
        state["revision"] = new_rev
        state["updated_at"] = stamp

        if op_key:
            _tx_guard.stamp_receipt(
                state,
                op_key,
                {"status": "applied", "ok": True, "action": action_name, "result": copy.deepcopy(result)},
                revision=new_rev,
                dict_key="operations",
                payload_hash=fp,
                committed_at=stamp,
            )
            _tx_guard.trim_receipts_by_op_seq(
                state,
                dict_key="operations",
                max_keep=MAX_OPERATION_RECEIPTS,
                max_ring=MAX_COMMITTED_RING,
            )

        _tx_guard.atomic_write_json(_state_path(root), state)
        out = copy.deepcopy(result)
        out["revision"] = new_rev
        out["updated_at"] = stamp
        out["idempotent_replay"] = False
        out["command_status"] = "APPLIED"
        out["llm_calls"] = 0
        return out


# ---------------------------------------------------------------------------
# F-018 + F-019: External Fact Sources & Layered World vs Real News
# ---------------------------------------------------------------------------


def _find_fact_source(state: Mapping[str, Any], source_id: str) -> Optional[Mapping[str, Any]]:
    if not source_id:
        return None
    for src in state.get("fact_sources") or []:
        if isinstance(src, Mapping) and str(src.get("source_id") or "") == source_id:
            return src
    return None


def register_external_fact_source(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """F-018: Register an external fact source with host-path sanitization."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise V02WorldLineageError(400, "INVALID_FIELDS", "payload must be a mapping")
    _scan_forbidden_keys_recursive(payload)

    title_cn = _require_str(payload.get("title_cn"), "title_cn", max_len=180)
    source_kind = _require_str(payload.get("source_kind"), "source_kind", max_len=32)
    if source_kind not in SOURCE_KINDS:
        raise V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            f"source_kind must be one of {sorted(SOURCE_KINDS)}, got {source_kind!r}",
        )

    raw_ref = _require_str(payload.get("source_ref"), "source_ref", max_len=320)
    sanitized_ref = _sanitize_source_ref(raw_ref)

    raw_tier = _optional_str(payload.get("trust_tier"), "trust_tier", max_len=40) or "TRUSTED_REFERENCE"
    if raw_tier not in TRUST_TIERS:
        raise V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            f"trust_tier must be one of {sorted(TRUST_TIERS)}, got {raw_tier!r}",
        )

    note_cn = _scrub_host_paths(_optional_str(payload.get("note_cn"), "note_cn", max_len=300))
    req_shared = _optional_bool(payload, "shared", True)
    shared = True if norm_actor == "partner" else req_shared

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        source_id = _allocate_id(state, "fsrc")
        entry = {
            "source_id": source_id,
            "title_cn": _scrub_host_paths(title_cn),
            "source_kind": source_kind,
            "source_ref": sanitized_ref,
            "trust_tier": raw_tier,
            "note_cn": note_cn,
            "shared": shared,
            "registered_by": norm_actor,
            "registered_at": stamp,
        }
        items = state.setdefault("fact_sources", [])
        items.append(entry)
        if len(items) > MAX_FACT_SOURCES:
            del items[: len(items) - MAX_FACT_SOURCES]
        return {"fact_source": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="register_external_fact_source",
        actor_side=norm_actor,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def record_layered_news_item(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """F-019: Record a layered news/fact/hearsay item under strict anti-contamination barriers."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise V02WorldLineageError(400, "INVALID_FIELDS", "payload must be a mapping")
    _scan_forbidden_keys_recursive(payload)

    layer = _require_str(payload.get("layer"), "layer", max_len=40)
    if layer not in NEWS_LAYERS:
        raise V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            f"layer must be one of {sorted(NEWS_LAYERS)}, got {layer!r}",
        )

    # Partner can NEVER fabricate fictional town news (checked before op_key/lock!).
    if layer == "LOCAL_WORLD_NEWS" and norm_actor != "subject":
        raise V02WorldLineageError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "Partner cannot write or fabricate LOCAL_WORLD_NEWS",
        )

    raw_headline = payload.get("headline_cn")
    if raw_headline is None:
        raw_headline = payload.get("title_cn")
    headline_cn = _require_str(raw_headline, "headline_cn", max_len=200)
    summary_raw = _optional_str(payload.get("summary_cn"), "summary_cn", max_len=500)
    summary_cn = summary_raw if summary_raw else headline_cn

    source_id = _optional_str(payload.get("source_id"), "source_id", max_len=80)
    raw_source_ref = _optional_str(payload.get("source_ref"), "source_ref", max_len=320)
    explicit_tier = _optional_str(payload.get("trust_tier"), "trust_tier", max_len=40)
    if explicit_tier and explicit_tier not in TRUST_TIERS:
        raise V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            f"trust_tier must be one of {sorted(TRUST_TIERS)}, got {explicit_tier!r}",
        )

    topic_tag = _scrub_host_paths(_optional_str(payload.get("topic_tag"), "topic_tag", max_len=64))
    location_id = _scrub_host_paths(_optional_str(payload.get("location_id"), "location_id", max_len=80))
    req_shared = _optional_bool(payload, "shared", True)
    shared = True if norm_actor == "partner" else req_shared

    if layer == "LOCAL_WORLD_NEWS" and _looks_like_external_url(raw_source_ref):
        raise V02WorldLineageError(
            409,
            "CROSS_LAYER_CONTAMINATION",
            "External real-world URL cannot enter fictional LOCAL_WORLD_NEWS",
        )

    def _preflight(state: dict[str, Any], _stamp: str) -> None:
        matched_src = _find_fact_source(state, source_id) if source_id else None
        if layer == "LOCAL_WORLD_NEWS":
            if matched_src is not None:
                src_ref = str(matched_src.get("source_ref") or "")
                src_kind = str(matched_src.get("source_kind") or "")
                if _looks_like_external_url(src_ref) or src_kind in ("website", "rss"):
                    raise V02WorldLineageError(
                        409,
                        "CROSS_LAYER_CONTAMINATION",
                        "External registered fact source cannot enter fictional LOCAL_WORLD_NEWS",
                    )
        elif layer == "EXTERNAL_REAL_FACT":
            if source_id and matched_src is None:
                raise V02WorldLineageError(
                    400,
                    "MISSING_FACT_SOURCE",
                    f"Registered fact source not found: {source_id!r}",
                )
            has_ref = bool(raw_source_ref) or (
                matched_src is not None and bool(str(matched_src.get("source_ref") or "").strip())
            )
            if not has_ref and matched_src is None:
                raise V02WorldLineageError(
                    400,
                    "MISSING_FACT_SOURCE",
                    "EXTERNAL_REAL_FACT requires a non-empty source_ref or valid registered source_id",
                )

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        matched_src = _find_fact_source(state, source_id) if source_id else None
        resolved_source_id = str(matched_src.get("source_id") or "") if matched_src is not None else source_id
        resolved_ref = raw_source_ref or (
            str(matched_src.get("source_ref") or "") if matched_src is not None else ""
        )
        sanitized_ref = _sanitize_source_ref(resolved_ref) if resolved_ref else ""

        if layer == "LOCAL_WORLD_NEWS":
            resolved_tier = explicit_tier or "PRIMARY_RECORD"
            is_fictional = True
            verified = resolved_tier != "UNVERIFIED_HEARSAY"
        elif layer == "EXTERNAL_REAL_FACT":
            default_tier = (
                str(matched_src.get("trust_tier") or "TRUSTED_REFERENCE")
                if matched_src is not None
                else "TRUSTED_REFERENCE"
            )
            resolved_tier = explicit_tier or default_tier
            is_fictional = False
            verified = resolved_tier != "UNVERIFIED_HEARSAY"
        else:  # UNVERIFIED_HEARSAY
            resolved_tier = "UNVERIFIED_HEARSAY"
            is_fictional = False
            verified = False

        news_id = _allocate_id(state, "news")
        clean_headline = _scrub_host_paths(headline_cn)
        entry = {
            "news_id": news_id,
            "layer": layer,
            "layer_label_cn": LAYER_LABELS_CN[layer],
            "is_fictional_town": is_fictional,
            "verified": verified,
            "headline_cn": clean_headline,
            "title_cn": clean_headline,
            "summary_cn": _scrub_host_paths(summary_cn),
            "source_id": resolved_source_id,
            "source_ref": sanitized_ref,
            "trust_tier": resolved_tier,
            "topic_tag": topic_tag,
            "location_id": location_id,
            "origin": "recorded_state",
            "shared": shared,
            "recorded_by": norm_actor,
            "recorded_at": stamp,
        }
        items = state.setdefault("layered_news", [])
        items.append(entry)
        if len(items) > MAX_LAYERED_NEWS:
            del items[: len(items) - MAX_LAYERED_NEWS]
        return {"news_item": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="record_layered_news_item",
        actor_side=norm_actor,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
        preflight=_preflight,
    )


def _read_live_local_world_news_readonly(
    day_root: Path,
    *,
    has_subject_capability: bool,
) -> list[dict[str, Any]]:
    """Zero-write read of live fictional town events from world ambient & neighborhood states."""
    items: list[dict[str, Any]] = []

    # 1) world/world_ambient_events.json
    amb_exists, amb_doc = _read_json_zero_write(day_root, WORLD_AMBIENT_EVENTS_REL_PATH)
    if amb_exists and amb_doc is not None:
        raw_events: list[Any] = []
        if isinstance(amb_doc, list):
            raw_events = amb_doc
        elif isinstance(amb_doc, Mapping):
            for key in ("events", "items", "ambient_events"):
                candidate = amb_doc.get(key)
                if isinstance(candidate, list):
                    raw_events = candidate
                    break
        for idx, ev in enumerate(raw_events):
            if not isinstance(ev, Mapping):
                continue
            payload_obj = ev.get("payload") if isinstance(ev.get("payload"), Mapping) else {}
            summary = str(
                payload_obj.get("summary")
                or ev.get("summary_cn")
                or ev.get("summary")
                or ev.get("title_cn")
                or ev.get("title")
                or ""
            ).strip()
            headline = str(
                ev.get("headline_cn")
                or ev.get("title_cn")
                or ev.get("title")
                or summary[:64]
            ).strip()
            if not headline and not summary:
                continue
            ev_id = str(ev.get("event_id") or ev.get("id") or f"idx-{idx + 1}").strip()
            clean_headline = _scrub_host_paths(headline)
            items.append({
                "news_id": f"world-ambient-{ev_id}",
                "layer": "LOCAL_WORLD_NEWS",
                "layer_label_cn": LAYER_LABELS_CN["LOCAL_WORLD_NEWS"],
                "is_fictional_town": True,
                "verified": True,
                "headline_cn": clean_headline,
                "title_cn": clean_headline,
                "summary_cn": _scrub_host_paths(summary or headline),
                "source_id": "",
                "source_ref": "world/world_ambient_events.json",
                "trust_tier": "PRIMARY_RECORD",
                "topic_tag": _scrub_host_paths(str(ev.get("event_type") or "ambient_event")),
                "location_id": _scrub_host_paths(str(ev.get("location_id") or "")),
                "local_date": str(ev.get("local_date") or ""),
                "origin": "world_ambient_events",
                "shared": True,
                "recorded_by": "world",
                "recorded_at": str(ev.get("valid_from") or ""),
            })

    # 2) world/v04_neighborhood_state.json
    nb_exists, nb_doc = _read_json_zero_write(day_root, V04_NEIGHBORHOOD_REL_PATH)
    if nb_exists and isinstance(nb_doc, Mapping):
        public_life = nb_doc.get("public_life") if isinstance(nb_doc.get("public_life"), Mapping) else {}
        notices = public_life.get("public_notices")
        if not isinstance(notices, list):
            notices = nb_doc.get("public_notices") if isinstance(nb_doc.get("public_notices"), list) else []
        for idx, notice in enumerate(notices):
            if not isinstance(notice, Mapping):
                continue
            title = str(notice.get("title") or notice.get("title_cn") or "").strip()
            body = str(notice.get("body") or notice.get("summary_cn") or notice.get("summary") or "").strip()
            if not title and not body:
                continue
            is_read = bool(notice.get("read_by_subject", True))
            is_expired = bool(notice.get("expired", False))
            show_body = bool(is_read or not has_subject_capability)
            summary_out = body if show_body else ("告示板有一张已过期的旧纸" if is_expired else "告示板有一张待细看的新纸")
            notice_id = str(notice.get("notice_id") or f"notice-{idx + 1}").strip()
            clean_notice_headline = _scrub_host_paths(title or summary_out[:48])
            items.append({
                "news_id": f"world-notice-{notice_id}",
                "layer": "LOCAL_WORLD_NEWS",
                "layer_label_cn": LAYER_LABELS_CN["LOCAL_WORLD_NEWS"],
                "is_fictional_town": True,
                "verified": True,
                "headline_cn": clean_notice_headline,
                "title_cn": clean_notice_headline,
                "summary_cn": _scrub_host_paths(summary_out or title),
                "source_id": "",
                "source_ref": "world/v04_neighborhood_state.json",
                "trust_tier": "PRIMARY_RECORD",
                "topic_tag": "neighborhood_notice",
                "location_id": "street_corner",
                "expired": is_expired,
                "origin": "v04_neighborhood_state",
                "shared": True,
                "recorded_by": "world",
                "recorded_at": str(notice.get("posted_at") or ""),
            })

    return items


# ---------------------------------------------------------------------------
# F-029 + F-030 + F-031: Body State Life History & Multi-Carrier Adaptation
# ---------------------------------------------------------------------------


def record_body_history_entry(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """F-029: Append a durable body-state life-history record (subject-only)."""
    norm_actor = _normalize_actor_side(actor_side)
    if norm_actor != "subject":
        raise V02WorldLineageError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "record_body_history_entry requires subject capability",
        )
    if not isinstance(payload, Mapping):
        raise V02WorldLineageError(400, "INVALID_FIELDS", "payload must be a mapping")
    _scan_forbidden_keys_recursive(payload)

    period_label = _require_str(payload.get("period_label"), "period_label", max_len=80)
    comfort_summary_cn = _require_str(payload.get("comfort_summary_cn"), "comfort_summary_cn", max_len=360)
    illness_or_strain_tag = _scrub_host_paths(
        _optional_str(payload.get("illness_or_strain_tag"), "illness_or_strain_tag", max_len=80)
    )
    rest_adjustment_cn = _scrub_host_paths(
        _optional_str(payload.get("rest_adjustment_cn"), "rest_adjustment_cn", max_len=240)
    )
    shared = _optional_bool(payload, "shared", True)

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        entry_id = _allocate_id(state, "bhist")
        entry = {
            "entry_id": entry_id,
            "period_label": _scrub_host_paths(period_label),
            "comfort_summary_cn": _scrub_host_paths(comfort_summary_cn),
            "illness_or_strain_tag": illness_or_strain_tag,
            "rest_adjustment_cn": rest_adjustment_cn,
            "shared": shared,
            "recorded_by": "subject",
            "recorded_at": stamp,
        }
        history = state.setdefault("body_history", [])
        history.append(entry)
        if len(history) > MAX_BODY_HISTORY:
            del history[: len(history) - MAX_BODY_HISTORY]
        return {"body_history_entry": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="record_body_history_entry",
        actor_side=norm_actor,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def _compute_carrier_adaptations(
    *,
    visual_preview: bool,
    audio_output: bool,
    desk_stage: bool,
    camera_input: bool,
) -> list[dict[str, str]]:
    adaptations: list[dict[str, str]] = []
    if not visual_preview:
        adaptations.append({
            "code": "NO_VISUAL_PREVIEW",
            "habit_adjustment_cn": "当前载体没有画面预览：暂停看图配色，改用文字手账与纯文本共读",
        })
    if not audio_output:
        adaptations.append({
            "code": "NO_AUDIO_OUTPUT",
            "habit_adjustment_cn": "当前载体无声景播放：窗外环境声改为文字声音卡呈现",
        })
    if not desk_stage:
        adaptations.append({
            "code": "NO_DESK_STAGE",
            "habit_adjustment_cn": "当前为轻量化文字载体：不假装渲染 2D 房间，所有操作走诚实列表与备忘",
        })
    if not camera_input:
        adaptations.append({
            "code": "NO_CAMERA_INPUT",
            "habit_adjustment_cn": "当前载体未接入镜头：不假装看见现实画面，改用文字描述或主动上传图片",
        })
    if not adaptations:
        adaptations.append({
            "code": "FULL_DESKTOP_CARRIER",
            "habit_adjustment_cn": "当前桌面载体感官齐全：房间光影、声音卡与看图配色均按常规步调运行",
        })
    return adaptations


def _default_carrier_adaptation_view() -> dict[str, Any]:
    caps = copy.deepcopy(CARRIER_CAPABILITY_DEFAULTS["desktop_gui"])
    return {
        "synced": False,
        "carrier_kind": "desktop_gui",
        "carrier_label_cn": CARRIER_DEFAULT_LABELS_CN["desktop_gui"],
        "capabilities": caps,
        "active_adaptations": _compute_carrier_adaptations(**caps),
        "note_cn": "",
        "synced_by": None,
        "synced_at": None,
        "honest_capability_boundary": True,
        "carrier_switch_history": [],
    }


def sync_carrier_profile_and_adapt_habits(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """F-030 / F-031: Sync active carrier profile and compute honest habit adaptations."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise V02WorldLineageError(400, "INVALID_FIELDS", "payload must be a mapping")
    _scan_forbidden_keys_recursive(payload)

    carrier_kind = _require_str(payload.get("carrier_kind"), "carrier_kind", max_len=32)
    if carrier_kind not in CARRIER_KINDS:
        raise V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            f"carrier_kind must be one of {sorted(CARRIER_KINDS)}, got {carrier_kind!r}",
        )

    defaults = CARRIER_CAPABILITY_DEFAULTS[carrier_kind]
    visual_preview = _optional_bool(payload, "visual_preview", defaults["visual_preview"])
    audio_output = _optional_bool(payload, "audio_output", defaults["audio_output"])
    desk_stage = _optional_bool(payload, "desk_stage", defaults["desk_stage"])
    camera_input = _optional_bool(payload, "camera_input", defaults["camera_input"])

    carrier_label_cn = _scrub_host_paths(
        _optional_str(payload.get("carrier_label_cn"), "carrier_label_cn", max_len=120)
        or CARRIER_DEFAULT_LABELS_CN[carrier_kind]
    )
    note_cn = _scrub_host_paths(_optional_str(payload.get("note_cn"), "note_cn", max_len=240))

    adaptations = _compute_carrier_adaptations(
        visual_preview=visual_preview,
        audio_output=audio_output,
        desk_stage=desk_stage,
        camera_input=camera_input,
    )

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        prev = state.get("carrier_adaptation")
        prev_kind = str(prev.get("carrier_kind") or "") if isinstance(prev, Mapping) else None
        profile = {
            "synced": True,
            "carrier_kind": carrier_kind,
            "carrier_label_cn": carrier_label_cn,
            "capabilities": {
                "visual_preview": visual_preview,
                "audio_output": audio_output,
                "desk_stage": desk_stage,
                "camera_input": camera_input,
            },
            "active_adaptations": copy.deepcopy(adaptations),
            "note_cn": note_cn,
            "synced_by": norm_actor,
            "synced_at": stamp,
            "honest_capability_boundary": True,
        }
        state["carrier_adaptation"] = profile

        switch_id = _allocate_id(state, "csw")
        switch_row = {
            "switch_id": switch_id,
            "from_carrier_kind": prev_kind,
            "to_carrier_kind": carrier_kind,
            "adaptation_codes": [a["code"] for a in adaptations],
            "synced_by": norm_actor,
            "synced_at": stamp,
        }
        history = state.setdefault("carrier_switch_history", [])
        history.append(switch_row)
        if len(history) > MAX_CARRIER_SWITCH_HISTORY:
            del history[: len(history) - MAX_CARRIER_SWITCH_HISTORY]

        out_profile = copy.deepcopy(profile)
        out_profile["carrier_switch_history"] = copy.deepcopy(history)
        return {"carrier_adaptation": out_profile, "switch_entry": copy.deepcopy(switch_row)}

    return _run_locked_tx(
        day_root,
        action_name="sync_carrier_profile_and_adapt_habits",
        actor_side=norm_actor,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


# ---------------------------------------------------------------------------
# F-144 + F-145 + F-146: Backup-Restore Honesty, Meaning Versions & Fork Lineage
# ---------------------------------------------------------------------------


def _path_is_inside_or_same(candidate: Path, base: Path) -> bool:
    try:
        cand_res = candidate.resolve()
        base_res = base.resolve()
        if cand_res == base_res:
            return True
        cand_res.relative_to(base_res)
        return True
    except (OSError, ValueError):
        return False


def read_backup_restore_honesty_zero_write(day_root: Path | str) -> dict[str, Any]:
    """F-144: 100% zero-write check of external control isolation and backup-restore honesty."""
    root = Path(day_root)
    external_control_isolated = False
    isolation_source = "in_root_only"

    env_raw = str(os.environ.get(CONTROL_ROOT_ENV_VAR) or "").strip()
    if env_raw:
        env_path = Path(env_raw)
        try:
            is_link = os.path.lexists(env_path) and env_path.is_symlink()
        except OSError:
            is_link = True
        if not is_link and not _path_is_inside_or_same(env_path, root):
            external_control_isolated = True
            isolation_source = "env_control_root"
    else:
        try:
            root_resolved = root.resolve()
            sibling_ctrl = root_resolved.parent / f"{root_resolved.name}-memory-authority" / "v02-control"
            if (
                os.path.lexists(sibling_ctrl)
                and not sibling_ctrl.is_symlink()
                and sibling_ctrl.is_dir()
            ):
                external_control_isolated = True
                isolation_source = "sibling_authority_dir"
        except OSError:
            pass

    vc_observed: dict[str, Any] = {
        "present": False,
        "watermark_exists": False,
        "authority_generation": 0,
        "restore_epoch": 0,
        "sealed_digest_count": 0,
        "enforced": True,
    }
    mig_exists, mig_doc = _read_json_zero_write(root, RUNTIME_MIGRATION_REL_PATH)
    if mig_exists and isinstance(mig_doc, Mapping):
        vc_raw = mig_doc.get("vc_authority_status")
        if isinstance(vc_raw, Mapping):
            vc_observed = {
                "present": True,
                "watermark_exists": bool(vc_raw.get("watermark_exists", False)),
                "authority_generation": int(vc_raw.get("authority_generation") or 0),
                "restore_epoch": int(vc_raw.get("restore_epoch") or 0),
                "sealed_digest_count": int(vc_raw.get("sealed_digest_count") or 0),
                "enforced": bool(vc_raw.get("enforced", True)),
            }

    if external_control_isolated:
        if vc_observed["present"] and vc_observed["watermark_exists"]:
            plain_status_cn = (
                f"外部独立控制面已与实例快照隔离（世代 {vc_observed['authority_generation']}，"
                f"已封存 {vc_observed['sealed_digest_count']} 条防复活指纹）："
                "备份恢复不会悄悄覆盖删除与遗忘边界，历史不会被篡改。"
            )
        else:
            plain_status_cn = (
                "外部独立控制面已与实例快照隔离：备份恢复不会悄悄抹掉外部删除与遗忘边界，"
                "历史记录保持诚实且不被回滚篡改。"
            )
    else:
        plain_status_cn = (
            "当前未检测到独立外部控制面目录，仍按本地只增不改与防篡改规则守护记录；"
            "若需跨快照恢复防止已忘内容回弹，请配置独立的外部控制目录。"
        )

    return {
        "external_control_isolated": bool(external_control_isolated),
        "isolation_source": isolation_source,
        "history_rewrite_blocked": True,
        "vc_authority_observed": vc_observed,
        "plain_status_cn": plain_status_cn,
        "zero_write": True,
        "llm_calls": 0,
    }


def _resolve_meaning_ref(payload: Mapping[str, Any], *, required: bool) -> str:
    """Resolve subject_ref or its safe aliases (item_ref / target_ref) for meaning actions."""
    resolved = ""
    for field_name in ("subject_ref", "item_ref", "target_ref"):
        if field_name in payload and payload.get(field_name) is not None:
            cand = _optional_str(payload.get(field_name), field_name, max_len=120)
            if cand and not resolved:
                resolved = cand
    if required and not resolved:
        raise V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            "Field 'subject_ref' (or alias 'item_ref' / 'target_ref') must be a non-empty string",
        )
    return resolved


def record_meaning_version(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """F-145: Append a new meaning version without ever overwriting earlier versions (subject-only)."""
    norm_actor = _normalize_actor_side(actor_side)
    if norm_actor != "subject":
        raise V02WorldLineageError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "record_meaning_version requires subject capability",
        )
    if not isinstance(payload, Mapping):
        raise V02WorldLineageError(400, "INVALID_FIELDS", "payload must be a mapping")
    _scan_forbidden_keys_recursive(payload)

    subject_ref = _resolve_meaning_ref(payload, required=True)
    subject_title_cn = _require_str(payload.get("subject_title_cn"), "subject_title_cn", max_len=160)
    current_meaning_cn = _require_str(payload.get("current_meaning_cn"), "current_meaning_cn", max_len=400)
    explicit_earlier_cn = _optional_str(payload.get("earlier_meaning_cn"), "earlier_meaning_cn", max_len=400)
    shared = _optional_bool(payload, "shared", True)

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        versions = state.setdefault("meaning_versions", [])
        same_ref_versions = [
            v for v in versions
            if isinstance(v, Mapping) and str(v.get("subject_ref") or "") == subject_ref
        ]
        max_ver = 0
        latest_current = ""
        for prev in same_ref_versions:
            vnum = int(prev.get("version_num") or 0)
            if vnum >= max_ver:
                max_ver = vnum
                latest_current = str(prev.get("current_meaning_cn") or "")

        version_num = max_ver + 1
        resolved_earlier = explicit_earlier_cn if explicit_earlier_cn else latest_current

        meaning_id = _allocate_id(state, "mver")
        entry = {
            "meaning_id": meaning_id,
            "subject_ref": _scrub_host_paths(subject_ref),
            "subject_title_cn": _scrub_host_paths(subject_title_cn),
            "version_num": version_num,
            "earlier_meaning_cn": _scrub_host_paths(resolved_earlier),
            "current_meaning_cn": _scrub_host_paths(current_meaning_cn),
            "kept_earlier_intact": True,
            "shared": shared,
            "recorded_by": "subject",
            "recorded_at": stamp,
        }
        versions.append(entry)
        if len(versions) > MAX_MEANING_VERSIONS:
            del versions[: len(versions) - MAX_MEANING_VERSIONS]
        return {"meaning_version": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="record_meaning_version",
        actor_side=norm_actor,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def propose_meaning_note(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """F-145 partner boundary: Append a gentle partner note without modifying TA's meaning versions."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise V02WorldLineageError(400, "INVALID_FIELDS", "payload must be a mapping")
    _scan_forbidden_keys_recursive(payload)

    subject_title_cn = _require_str(payload.get("subject_title_cn"), "subject_title_cn", max_len=160)
    raw_ref = _resolve_meaning_ref(payload, required=False)
    subject_ref = raw_ref or f"note_{hashlib.sha256(subject_title_cn.encode('utf-8')).hexdigest()[:10]}"
    note_cn = _require_str(payload.get("note_cn"), "note_cn", max_len=400)

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        note_id = _allocate_id(state, "mnote")
        entry = {
            "note_id": note_id,
            "subject_ref": _scrub_host_paths(subject_ref),
            "subject_title_cn": _scrub_host_paths(subject_title_cn),
            "note_cn": _scrub_host_paths(note_cn),
            "status": "PARTNER_NOTE",
            "shared": True,
            "recorded_by": norm_actor,
            "recorded_at": stamp,
        }
        notes = state.setdefault("partner_meaning_notes", [])
        notes.append(entry)
        if len(notes) > MAX_PARTNER_MEANING_NOTES:
            del notes[: len(notes) - MAX_PARTNER_MEANING_NOTES]
        return {"partner_meaning_note": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="propose_meaning_note",
        actor_side=norm_actor,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def record_lineage_branch(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """F-146: Record an honest fork/successor lineage branch (subject-only)."""
    norm_actor = _normalize_actor_side(actor_side)
    if norm_actor != "subject":
        raise V02WorldLineageError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "record_lineage_branch requires subject capability",
        )
    if not isinstance(payload, Mapping):
        raise V02WorldLineageError(400, "INVALID_FIELDS", "payload must be a mapping")
    _scan_forbidden_keys_recursive(payload)

    origin_instance_id = _require_str(payload.get("origin_instance_id"), "origin_instance_id", max_len=120)
    branch_label_cn = _require_str(payload.get("branch_label_cn"), "branch_label_cn", max_len=160)
    reason_cn = _require_str(payload.get("reason_cn"), "reason_cn", max_len=320)
    parent_branch_id = _scrub_host_paths(
        _optional_str(payload.get("parent_branch_id"), "parent_branch_id", max_len=120)
    )
    shared = _optional_bool(payload, "shared", True)

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        branch_id = _allocate_id(state, "lbr")
        entry = {
            "branch_id": branch_id,
            "parent_branch_id": parent_branch_id,
            "origin_instance_id": _scrub_host_paths(origin_instance_id),
            "branch_label_cn": _scrub_host_paths(branch_label_cn),
            "reason_cn": _scrub_host_paths(reason_cn),
            "independent_life_from": stamp,
            "shared_origin_only": True,
            "not_same_person_claim": True,
            "shared": shared,
            "recorded_by": "subject",
            "recorded_at": stamp,
        }
        branches = state.setdefault("lineage_branches", [])
        branches.append(entry)
        if len(branches) > MAX_LINEAGE_BRANCHES:
            del branches[: len(branches) - MAX_LINEAGE_BRANCHES]
        return {"lineage_branch": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="record_lineage_branch",
        actor_side=norm_actor,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


# ---------------------------------------------------------------------------
# Combined 100% Zero-Write Reader
# ---------------------------------------------------------------------------


def _filter_visible(entries: list[Any], *, has_subject_capability: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        if not has_subject_capability and item.get("shared") is False:
            continue
        out.append(_scrub_value_recursive(copy.deepcopy(dict(item))))
    return out


def read_world_news_carrier_lineage_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write combined projection for Wave B2 world-news, carrier, and lineage."""
    root = Path(day_root)
    stamp = _now_iso(now_iso)
    cap = bool(has_subject_capability)
    state_exists, state = _load_state_readonly(root)

    fact_sources = _filter_visible(state.get("fact_sources", []), has_subject_capability=cap)

    recorded_local: list[dict[str, Any]] = []
    external_real_facts: list[dict[str, Any]] = []
    unverified_hearsay: list[dict[str, Any]] = []
    for item in _filter_visible(state.get("layered_news", []), has_subject_capability=cap):
        hl = str(item.get("headline_cn") or item.get("title_cn") or "").strip()
        item["headline_cn"] = hl
        item["title_cn"] = hl
        layer = str(item.get("layer") or "")
        if layer == "LOCAL_WORLD_NEWS":
            item["is_fictional_town"] = True
            item["layer_label_cn"] = LAYER_LABELS_CN["LOCAL_WORLD_NEWS"]
            recorded_local.append(item)
        elif layer == "EXTERNAL_REAL_FACT":
            item["is_fictional_town"] = False
            item["layer_label_cn"] = LAYER_LABELS_CN["EXTERNAL_REAL_FACT"]
            external_real_facts.append(item)
        elif layer == "UNVERIFIED_HEARSAY":
            item["is_fictional_town"] = False
            item["verified"] = False
            item["layer_label_cn"] = LAYER_LABELS_CN["UNVERIFIED_HEARSAY"]
            unverified_hearsay.append(item)

    live_local = _read_live_local_world_news_readonly(root, has_subject_capability=cap)
    local_world_news = _scrub_value_recursive(live_local + recorded_local)
    for item in local_world_news:
        if isinstance(item, dict):
            hl = str(item.get("headline_cn") or item.get("title_cn") or "").strip()
            item["headline_cn"] = hl
            item["title_cn"] = hl

    body_history = _filter_visible(state.get("body_history", []), has_subject_capability=cap)

    raw_carrier = state.get("carrier_adaptation")
    if isinstance(raw_carrier, Mapping):
        carrier_adaptation = _scrub_value_recursive(copy.deepcopy(dict(raw_carrier)))
        carrier_adaptation["synced"] = True
        carrier_adaptation["carrier_switch_history"] = _scrub_value_recursive(
            copy.deepcopy(list(state.get("carrier_switch_history", [])))
        )
    else:
        carrier_adaptation = _default_carrier_adaptation_view()

    backup_restore_guard = read_backup_restore_honesty_zero_write(root)
    meaning_versions = _filter_visible(state.get("meaning_versions", []), has_subject_capability=cap)
    partner_meaning_notes = _filter_visible(state.get("partner_meaning_notes", []), has_subject_capability=True)
    lineage_branches = _filter_visible(state.get("lineage_branches", []), has_subject_capability=cap)

    return {
        "state_exists": bool(state_exists),
        "perspective": "subject" if cap else "partner",
        "revision": int(state.get("revision") or 0),
        "observed_at": stamp,
        "fact_sources": fact_sources,
        "layered_news": {
            "local_world_news": local_world_news,
            "external_real_facts": external_real_facts,
            "unverified_hearsay": unverified_hearsay,
        },
        "body_history": body_history,
        "carrier_adaptation": carrier_adaptation,
        "backup_restore_guard": backup_restore_guard,
        "meaning_versions": meaning_versions,
        "partner_meaning_notes": partner_meaning_notes,
        "lineage_branches": lineage_branches,
        "llm_calls": 0,
        "zero_write": True,
    }


__all__ = [
    "CARRIER_CAPABILITY_DEFAULTS",
    "CARRIER_KINDS",
    "CONTROL_ROOT_ENV_VAR",
    "FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS",
    "LAYER_LABELS_CN",
    "NEWS_LAYERS",
    "SOURCE_KINDS",
    "STATE_SCHEMA",
    "TRUST_TIERS",
    "V02WorldLineageError",
    "build_default_state",
    "propose_meaning_note",
    "read_backup_restore_honesty_zero_write",
    "read_world_news_carrier_lineage_view",
    "record_body_history_entry",
    "record_layered_news_item",
    "record_lineage_branch",
    "record_meaning_version",
    "register_external_fact_source",
    "sync_carrier_profile_and_adapt_habits",
]
