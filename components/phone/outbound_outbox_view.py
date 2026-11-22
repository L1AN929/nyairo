#!/usr/bin/env python3
"""Unified pollable outbound outbox HTTP view (Stage 3 Pillar 3, Blueprint §3.2 C).

Exposes two transport-facing contracts for Desktop / WeChat / Telegram adapters:

1. ``GET /api/phone/outbound/pending`` (``read_outbound_pending_view``):
   - 100% zero-write on non-existent, empty, freshly initialized, or populated roots
     (zero files, zero directories, zero ``-wal``/``-shm``/``.lock`` created).
   - Merges ``APPROVED_FOR_DELIVERY`` / ``CLAIMED_BY_TRANSPORT`` proactive outreach
     items (``item_kind: "proactive_outreach"``) from ``v03_economy_illness_bridge``
     and ``READY_TO_REPLY`` / ``CLAIMED_BY_TRANSPORT`` deferred replies
     (``item_kind: "deferred_reply_ready"``) from ``autonomy_rhythm_view`` into one
     chronologically sorted list (``created_at`` / ``ready_at`` ascending).
   - Strictly whitelists clean public/transport fields so private subject drafts,
     internal notes, or tokens can never leak.
   - Reports ``quiet_hours_active`` honestly and marks quiet-hours holds clearly so
     adapters know not to buzz during quiet hours.

2. ``POST /api/phone/outbound/ack`` (``mutate_outbound_ack_view``):
   - Strict allowed top-level field set:
     ``{"action", "item_id", "item_kind", "operation_key", "transport_receipt_id"}``.
   - Rejects forged identity keys (e.g. ``actor_side``), unknown fields, missing
     ``operation_key``, invalid ``action``, or invalid ``item_kind`` with HTTP 400.
   - Routes ``proactive_outreach`` to
     ``v03_economy_illness_bridge.ack_proactive_outbound_delivery`` and
     ``deferred_reply_ready`` to ``autonomy_rhythm_view.ack_deferred_reply_delivery``
     with defensive standalone fallbacks.
   - Idempotent on ``operation_key`` replay; ``llm_calls == 0``.
"""

from __future__ import annotations

import copy
import inspect
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from ..life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover - flat sys.path layout
    import state_tx_guard as _tx  # type: ignore[no-redef]

from components.world.scripts import v03_economy_illness_bridge as eco_bridge

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - standalone fallback
    _instance_paths = None  # type: ignore[assignment]

try:
    from . import autonomy_rhythm_view as rhythm_view
except ImportError:  # pragma: no cover - flat sys.path layout
    import autonomy_rhythm_view as rhythm_view  # type: ignore[no-redef]

DEFAULT_TZ = timezone(timedelta(hours=8))

ENDPOINT_PENDING = "/api/phone/outbound/pending"
ENDPOINT_ACK = "/api/phone/outbound/ack"

OUTBOX_PENDING_SCHEMA = "phone.outbound.pending.v1"
OUTBOX_ACK_SCHEMA = "phone.outbound.ack.v1"

ITEM_KIND_PROACTIVE = "proactive_outreach"
ITEM_KIND_DEFERRED = "deferred_reply_ready"
OUTBOUND_ITEM_KINDS: tuple[str, ...] = (ITEM_KIND_PROACTIVE, ITEM_KIND_DEFERRED)

OUTBOUND_ACK_ACTIONS: tuple[str, ...] = (
    "claim",
    "ack_delivered",
    "ack_failed",
    "ack_unknown",
    "ack_read",
)

OUTBOUND_ACK_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "action",
    "item_id",
    "item_kind",
    "operation_key",
    "transport_receipt_id",
})

PROACTIVE_PENDING_STATUSES: frozenset[str] = frozenset({
    "APPROVED_FOR_DELIVERY",
    "CLAIMED_BY_TRANSPORT",
})

DEFERRED_PENDING_STATUSES: frozenset[str] = frozenset({
    "READY_TO_REPLY",
    "CLAIMED_BY_TRANSPORT",
})

FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "author_side",
    "actor",
    "role",
    "caller",
    "caller_side",
    "is_subject",
    "subject_capability",
    "has_subject_capability",
    "visitor_side",
    "viewer_side",
    "perspective",
    "subject",
    "owner",
    "uid",
    "euid",
    "gid",
    "authority",
    "authorities",
    "main_self_verified",
    "confirm_apply",
    "csrf_token",
    "capability",
    "internal_token",
})

_PRIVATE_OR_INTERNAL_KEYS: frozenset[str] = frozenset({
    "private_note",
    "subject_draft",
    "draft_text",
    "raw_draft",
    "internal_token",
    "secret",
    "token",
    "csrf_token",
    "capability",
    "fence_id",
    "content_dedup_hash",
    "recipient_ref",
    "raw_state",
    "path",
    "root",
    "day_root",
    "world_home",
})

_PUBLIC_RECEIPT_ALLOWED_KEYS: frozenset[str] = frozenset({
    "item_id",
    "outbound_id",
    "msg_id",
    "item_kind",
    "action",
    "ack_action",
    "ack_state",
    "status",
    "previous_status",
    "current_status",
    "outbound_status",
    "delivery_ack_state",
    "epistemic_state",
    "partner_read_confirmed",
    "delivered_is_not_read",
    "transport_receipt_id",
    "operation_key",
    "already_applied",
    "claimed_at",
    "delivered_at",
    "failed_at",
    "read_at",
    "expires_at",
    "unanswered_streak",
    "proactive_paused_by_backoff",
    "cleared_count",
})

_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_MAX_TEXT_CHARS = 4000
_MAX_RECEIPT_ID_CHARS = 128
_MAX_FALLBACK_RECEIPTS = 256
_FALLBACK_DEFERRED_RECEIPTS: dict[str, dict[str, Any]] = {}


class OutboundOutboxError(ValueError):
    """Structured HTTP validation / dispatch refusal for the pollable outbox."""

    def __init__(
        self,
        status_code: int,
        reason_code: str,
        detail: str = "",
        *,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(detail or reason_code)
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.extra = dict(extra or {})


def _resolve_day_root(day_root: Path | str) -> Path:
    raw = Path(str(day_root))
    if _instance_paths is not None:
        try:
            return _instance_paths.resolve_instance_paths(raw).day_root
        except Exception:
            return raw
    return raw


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TZ)
    return parsed.astimezone(DEFAULT_TZ)


def _resolve_now_dt(now_iso: Optional[str]) -> datetime:
    if now_iso is not None:
        parsed = _parse_iso_dt(now_iso)
        if parsed is not None:
            return parsed.replace(microsecond=0)
    return datetime.now(tz=DEFAULT_TZ).replace(microsecond=0)


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(DEFAULT_TZ).replace(microsecond=0).isoformat(timespec="seconds")


def _clean_short_str(value: Any, *, max_len: int = 128, default: str = "") -> str:
    if not isinstance(value, str):
        return default
    cleaned = value.replace("\x00", "").replace("\r", "").replace("\n", " ").strip()
    if not cleaned:
        return default
    return cleaned[:max_len]


def _clean_body_text(value: Any, *, max_len: int = _MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned[:max_len]


def _is_quiet_hours_safe(now_dt: datetime, quiet_start: str, quiet_end: str) -> bool:
    helper = getattr(eco_bridge, "_is_quiet_hours", None)
    if callable(helper):
        try:
            return bool(helper(now_dt, quiet_start, quiet_end))
        except Exception:
            pass
    try:
        sh, sm = (int(part) for part in str(quiet_start).split(":", 1))
        eh, em = (int(part) for part in str(quiet_end).split(":", 1))
        cur_min = now_dt.hour * 60 + now_dt.minute
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        if start_min == end_min:
            return False
        if start_min < end_min:
            return start_min <= cur_min < end_min
        return cur_min >= start_min or cur_min < end_min
    except Exception:
        return False


def _read_quiet_hours_active_zero_write(resolved_root: Path, now_dt: datetime) -> bool:
    try:
        state, _err = eco_bridge.read_stored_ecology_zero_write(resolved_root)
    except Exception:
        state = {}
    rb = state.get("reality_bridge") if isinstance(state, Mapping) else None
    policy = rb.get("outbound_policy") if isinstance(rb, Mapping) else None
    default_start = str(getattr(eco_bridge, "DEFAULT_QUIET_START_HHMM", "23:30"))
    default_end = str(getattr(eco_bridge, "DEFAULT_QUIET_END_HHMM", "07:30"))
    quiet_start = (
        str(policy.get("quiet_start_hhmm") or default_start)
        if isinstance(policy, Mapping)
        else default_start
    )
    quiet_end = (
        str(policy.get("quiet_end_hhmm") or default_end)
        if isinstance(policy, Mapping)
        else default_end
    )
    return _is_quiet_hours_safe(now_dt, quiet_start, quiet_end)


def _call_with_supported_kwargs(
    fn: Any,
    root: Path,
    candidate_kwargs: Mapping[str, Any],
) -> Any:
    """Invoke an upstream helper passing only kwargs its signature accepts."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(root, **dict(candidate_kwargs))
    params = sig.parameters
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if accepts_var_kw:
        filtered = {k: v for k, v in candidate_kwargs.items() if v is not None or k in params}
        return fn(root, **filtered)
    filtered = {k: v for k, v in candidate_kwargs.items() if k in params}
    return fn(root, **filtered)


def _extract_item_list(result: Any, *keys: str) -> list[Mapping[str, Any]]:
    if isinstance(result, (list, tuple)):
        return [item for item in result if isinstance(item, Mapping)]
    if not isinstance(result, Mapping):
        return []
    for key in keys:
        val = result.get(key)
        if isinstance(val, (list, tuple)):
            return [item for item in val if isinstance(item, Mapping)]
    nested = result.get("data")
    if isinstance(nested, Mapping):
        for key in keys:
            val = nested.get(key)
            if isinstance(val, (list, tuple)):
                return [item for item in val if isinstance(item, Mapping)]
    return []


def _extract_bool_field(result: Any, key: str) -> Optional[bool]:
    if not isinstance(result, Mapping):
        return None
    if isinstance(result.get(key), bool):
        return bool(result[key])
    nested = result.get("data")
    if isinstance(nested, Mapping) and isinstance(nested.get(key), bool):
        return bool(nested[key])
    return None


def _read_proactive_outbox_zero_write(
    resolved_root: Path,
    *,
    now_dt: datetime,
    observed_at: str,
) -> tuple[list[Mapping[str, Any]], Optional[bool]]:
    reader = getattr(eco_bridge, "read_pending_proactive_outbox_zero_write", None)
    if callable(reader):
        raw_res = _call_with_supported_kwargs(
            reader,
            resolved_root,
            {"now_iso": observed_at, "include_quiet_held": True},
        )
        items = _extract_item_list(
            raw_res,
            "items",
            "pending_items",
            "outbound_items",
            "records",
        )
        quiet_flag = _extract_bool_field(raw_res, "quiet_hours_active")
        return items, quiet_flag

    # Defensive standalone zero-write fallback reading phone/v03_ecology.json directly.
    state, _err = eco_bridge.read_stored_ecology_zero_write(resolved_root)
    rb = state.get("reality_bridge") if isinstance(state, Mapping) else None
    records = rb.get("outbound_records") if isinstance(rb, Mapping) else None
    pending: list[Mapping[str, Any]] = []
    if isinstance(records, list):
        for rec in records:
            if not isinstance(rec, Mapping):
                continue
            status = str(rec.get("status") or "").strip().upper()
            if status in PROACTIVE_PENDING_STATUSES:
                pending.append(rec)
    quiet_flag = _read_quiet_hours_active_zero_write(resolved_root, now_dt)
    return pending, quiet_flag


def _read_deferred_replies_zero_write(
    resolved_root: Path,
    *,
    now_dt: datetime,
    observed_at: str,
) -> list[Mapping[str, Any]]:
    reader = getattr(rhythm_view, "read_ready_deferred_replies_zero_write", None)
    if callable(reader):
        raw_res = _call_with_supported_kwargs(
            reader,
            resolved_root,
            {"now_iso": observed_at},
        )
        return _extract_item_list(
            raw_res,
            "items",
            "ready_messages",
            "ready_items",
            "pending_messages",
        )

    # Defensive standalone zero-write fallback reading phone/autonomy_rhythm.json directly.
    state, _err = rhythm_view.read_autonomy_rhythm_state(resolved_root)
    raw_msgs = state.get("pending_messages") if isinstance(state, Mapping) else None
    ready: list[Mapping[str, Any]] = []
    if isinstance(raw_msgs, list):
        for msg in raw_msgs:
            if not isinstance(msg, Mapping):
                continue
            status = str(msg.get("status") or "").strip().upper()
            due_dt = _parse_iso_dt(msg.get("deliver_after"))
            if status in DEFERRED_PENDING_STATUSES:
                ready.append(msg)
            elif status == "QUEUED_ON_PHONE" and due_dt is not None and due_dt <= now_dt:
                promoted = dict(msg)
                promoted["status"] = "READY_TO_REPLY"
                ready.append(promoted)
    return ready


def _sanitize_proactive_item(
    raw: Mapping[str, Any],
    *,
    quiet_hours_active: bool,
    observed_at: str,
) -> Optional[dict[str, Any]]:
    item_id = _clean_short_str(raw.get("item_id") or raw.get("outbound_id"), max_len=128)
    if not item_id:
        return None
    status = _clean_short_str(raw.get("status"), max_len=48).upper()
    if status not in PROACTIVE_PENDING_STATUSES:
        return None
    channel = _clean_short_str(raw.get("channel"), max_len=64, default="desktop_note")
    reason_kind = _clean_short_str(
        raw.get("reason_kind"),
        max_len=64,
        default="genuine_life_share",
    )
    text = _clean_body_text(raw.get("text"))
    created_at = _clean_short_str(raw.get("created_at"), max_len=64, default=observed_at)
    held = bool(quiet_hours_active or raw.get("quiet_hours_held", False))

    item: dict[str, Any] = {
        "item_id": item_id,
        "item_kind": ITEM_KIND_PROACTIVE,
        "channel": channel,
        "status": status,
        "reason_kind": reason_kind,
        "text": text,
        "created_at": created_at,
        "quiet_hours_held": held,
        "deliverable_now": not held,
    }
    claimed_at = _clean_short_str(raw.get("claimed_at"), max_len=64)
    if claimed_at:
        item["claimed_at"] = claimed_at
    expires_at = _clean_short_str(raw.get("expires_at"), max_len=64)
    if expires_at:
        item["expires_at"] = expires_at
    return item


def _sanitize_deferred_reply_item(
    raw: Mapping[str, Any],
    *,
    observed_at: str,
) -> Optional[dict[str, Any]]:
    item_id = _clean_short_str(raw.get("item_id") or raw.get("msg_id"), max_len=128)
    if not item_id:
        return None
    raw_status = _clean_short_str(raw.get("status"), max_len=48).upper()
    if raw_status == "QUEUED_ON_PHONE":
        status = "READY_TO_REPLY"
    elif raw_status in DEFERRED_PENDING_STATUSES:
        status = raw_status
    else:
        return None
    channel = _clean_short_str(raw.get("channel"), max_len=64, default="session_reply")
    session_id = _clean_short_str(raw.get("session_id"), max_len=96, default="default")
    user_full_text = _clean_body_text(
        raw.get("user_full_text")
        if raw.get("user_full_text") is not None
        else (
            raw.get("full_text")
            if raw.get("full_text") is not None
            else raw.get("preview_text")
        )
    )
    ready_at = _clean_short_str(
        raw.get("ready_at")
        or raw.get("promoted_at")
        or raw.get("deliver_after")
        or raw.get("queued_at"),
        max_len=64,
        default=observed_at,
    )
    held = bool(raw.get("quiet_hours_held", False))

    item: dict[str, Any] = {
        "item_id": item_id,
        "item_kind": ITEM_KIND_DEFERRED,
        "channel": channel,
        "session_id": session_id,
        "status": status,
        "user_full_text": user_full_text,
        "ready_at": ready_at,
        "quiet_hours_held": held,
        "deliverable_now": not held,
    }
    queued_at = _clean_short_str(raw.get("queued_at"), max_len=64)
    if queued_at:
        item["queued_at"] = queued_at
    reason_code = _clean_short_str(raw.get("reason_code"), max_len=64)
    if reason_code:
        item["reason_code"] = reason_code
    if isinstance(raw.get("urgent"), bool):
        item["urgent"] = bool(raw["urgent"])
    claimed_at = _clean_short_str(raw.get("claimed_at"), max_len=64)
    if claimed_at:
        item["claimed_at"] = claimed_at
    return item


def _sort_key_for_item(item: Mapping[str, Any]) -> tuple[int, float, str, str, str]:
    raw_ts = (
        str(item.get("created_at") or "")
        if item.get("item_kind") == ITEM_KIND_PROACTIVE
        else str(item.get("ready_at") or "")
    )
    parsed = _parse_iso_dt(raw_ts)
    if parsed is not None:
        return (0, parsed.timestamp(), raw_ts, str(item.get("item_id") or ""), str(item.get("item_kind") or ""))
    return (1, float("inf"), raw_ts, str(item.get("item_id") or ""), str(item.get("item_kind") or ""))


_WEATHER_CARE_LEGACY_MAX_AGE_SECONDS = 6 * 3600
_LEGACY_WEATHER_OUTREACH_TEXT_CUES: tuple[str, ...] = (
    "外头突然落雨",
    "外头突然飘起细雨",
    "外头飘雪降温",
    "出门的话记得顺手带把伞",
    "出门顺手留意外套别淋潮",
)


def _is_weather_care_item(raw: Mapping[str, Any]) -> bool:
    reason = str(raw.get("reason_kind") or "").strip().lower()
    if reason in ("sudden_weather_care", "weather_care"):
        return True
    trigger = str(raw.get("trigger_kind") or "").strip().lower()
    if trigger == "sudden_weather_care":
        return True
    fact_ref = str(raw.get("fact_ref") or "").strip().lower()
    if fact_ref.startswith("weather:"):
        return True
    op_key = str(raw.get("operation_key") or "").strip().lower()
    if op_key.startswith("outreach_weather_"):
        return True
    text = str(raw.get("text") or "")
    if any(cue in text for cue in _LEGACY_WEATHER_OUTREACH_TEXT_CUES):
        return True
    return False


def _read_v04_weather_clear_and_dry_zero_write(resolved_root: Path) -> bool:
    """Return True only when world/v04_neighborhood_state.json exists and shows clear weather with dry ground."""
    nb_path = resolved_root / "world" / "v04_neighborhood_state.json"
    try:
        if not nb_path.is_file() or nb_path.is_symlink():
            return False
        doc = json.loads(nb_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(doc, Mapping):
        return False
    env = doc.get("environment")
    if not isinstance(env, Mapping):
        return False
    weather = str(env.get("weather") or "").strip().lower()
    if weather != "clear":
        return False
    if bool(env.get("yard_bench_damp")):
        return False
    places = doc.get("places")
    if isinstance(places, Mapping):
        for p in places.values():
            if isinstance(p, Mapping):
                gs = str(p.get("ground_state") or "").strip().lower()
                if gs and gs != "dry":
                    return False
    lt_path = resolved_root / "world" / "v05_living_town.json"
    try:
        if lt_path.is_file() and not lt_path.is_symlink():
            lt_doc = json.loads(lt_path.read_text(encoding="utf-8"))
            if isinstance(lt_doc, Mapping):
                gs_obj = lt_doc.get("ground_surface")
                if isinstance(gs_obj, Mapping):
                    puddle = gs_obj.get("puddle_depth")
                    if isinstance(puddle, (int, float)) and not isinstance(puddle, bool) and float(puddle) > 0.0:
                        return False
    except Exception:
        pass
    return True


def _is_proactive_item_expired(
    raw: Mapping[str, Any],
    *,
    now_dt: datetime,
    weather_clear_and_dry: bool,
) -> bool:
    exp_dt = _parse_iso_dt(raw.get("expires_at"))
    if exp_dt is not None and exp_dt <= now_dt:
        return True
    if _is_weather_care_item(raw):
        if weather_clear_and_dry:
            return True
        created_dt = _parse_iso_dt(raw.get("created_at"))
        if created_dt is not None and (now_dt - created_dt).total_seconds() > _WEATHER_CARE_LEGACY_MAX_AGE_SECONDS:
            return True
    return False


def read_pending_outbound_outbox_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Read merged pending proactive outreach + ready deferred replies with 0 disk writes."""
    resolved_root = _resolve_day_root(day_root)
    now_dt = _resolve_now_dt(now_iso)
    observed_at = _format_iso(now_dt)

    raw_proactive, quiet_from_reader = _read_proactive_outbox_zero_write(
        resolved_root,
        now_dt=now_dt,
        observed_at=observed_at,
    )
    quiet_hours_active = (
        bool(quiet_from_reader)
        if isinstance(quiet_from_reader, bool)
        else _read_quiet_hours_active_zero_write(resolved_root, now_dt)
    )
    raw_deferred = _read_deferred_replies_zero_write(
        resolved_root,
        now_dt=now_dt,
        observed_at=observed_at,
    )
    weather_clear_and_dry = _read_v04_weather_clear_and_dry_zero_write(resolved_root)

    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []

    for rec in raw_proactive:
        if _is_proactive_item_expired(
            rec,
            now_dt=now_dt,
            weather_clear_and_dry=weather_clear_and_dry,
        ):
            continue
        clean = _sanitize_proactive_item(
            rec,
            quiet_hours_active=quiet_hours_active,
            observed_at=observed_at,
        )
        if clean is None:
            continue
        dedup_key = (clean["item_kind"], clean["item_id"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        merged.append(clean)

    for rec in raw_deferred:
        clean = _sanitize_deferred_reply_item(rec, observed_at=observed_at)
        if clean is None:
            continue
        dedup_key = (clean["item_kind"], clean["item_id"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        merged.append(clean)

    merged.sort(key=_sort_key_for_item)

    return {
        "schema": OUTBOX_PENDING_SCHEMA,
        "observed_at": observed_at,
        "quiet_hours_active": bool(quiet_hours_active),
        "pending_count": len(merged),
        "items": merged,
    }


def read_outbound_pending_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """HTTP GET handler projection for ``GET /api/phone/outbound/pending`` (100% zero-write)."""
    _ = bool(has_subject_capability)  # Transport surface never leaks private subject drafts.
    data = read_pending_outbound_outbox_zero_write(day_root, now_iso=now_iso)
    return 200, {
        "ok": True,
        "status": "ok",
        "zero_write": True,
        "llm_calls": 0,
        "data": data,
    }


def _error_response(
    status_code: int,
    reason_code: str,
    detail: str = "",
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code)
    status_str = "not_found" if int(status_code) == 404 else (
        "rejected" if int(status_code) < 500 else "unavailable"
    )
    body: dict[str, Any] = {
        "ok": False,
        "status": status_str,
        "command_status": "REJECTED",
        "endpoint": ENDPOINT_ACK,
        "reason_code": code_str,
        "error_code": code_str,
        "detail": str(detail or code_str),
        "llm_calls": 0,
        "data": {},
    }
    if extra:
        body.update(dict(extra))
    return int(status_code), body


def _scan_payload_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise OutboundOutboxError(400, "INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise OutboundOutboxError(
                    400,
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded key forbidden: {raw_key!r}",
                )
            low = raw_key.lower()
            if raw_key in FORBIDDEN_IDENTITY_KEYS or low in FORBIDDEN_IDENTITY_KEYS:
                raise OutboundOutboxError(
                    400,
                    "INVALID_FIELDS",
                    f"Forged identity or forbidden field rejected: {raw_key!r}",
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _validate_ack_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise OutboundOutboxError(
            400,
            "INVALID_FIELDS",
            "Payload must be a non-empty JSON object",
        )
    _scan_payload_keys_recursive(payload)

    unknown = sorted(set(payload.keys()) - OUTBOUND_ACK_ALLOWED_FIELDS)
    if unknown:
        raise OutboundOutboxError(
            400,
            "INVALID_FIELDS",
            f"Unknown fields not allowed: {unknown}",
            extra={"invalid_fields": unknown},
        )

    raw_action = payload.get("action")
    if (
        not isinstance(raw_action, str)
        or not raw_action.strip()
        or raw_action != raw_action.strip()
        or raw_action not in OUTBOUND_ACK_ACTIONS
    ):
        raise OutboundOutboxError(
            400,
            "INVALID_ACTION",
            f"Field 'action' must be one of {list(OUTBOUND_ACK_ACTIONS)}",
        )

    raw_item_kind = payload.get("item_kind")
    if (
        not isinstance(raw_item_kind, str)
        or not raw_item_kind.strip()
        or raw_item_kind != raw_item_kind.strip()
        or raw_item_kind not in OUTBOUND_ITEM_KINDS
    ):
        raise OutboundOutboxError(
            400,
            "INVALID_ITEM_KIND",
            f"Field 'item_kind' must be one of {list(OUTBOUND_ITEM_KINDS)}",
        )

    raw_item_id = payload.get("item_id")
    if not isinstance(raw_item_id, str) or not raw_item_id.strip():
        raise OutboundOutboxError(400, "INVALID_ITEM_ID", "Field 'item_id' is required")
    item_id = raw_item_id.strip()
    if raw_item_id != item_id or not _ID_PATTERN.match(item_id):
        raise OutboundOutboxError(400, "INVALID_ITEM_ID", "Field 'item_id' is malformed")

    if "operation_key" not in payload or payload.get("operation_key") is None:
        raise OutboundOutboxError(
            400,
            "OPERATION_KEY_REQUIRED",
            "Field 'operation_key' is mandatory for outbound ack",
        )
    raw_op_key = payload.get("operation_key")
    if not isinstance(raw_op_key, str) or not raw_op_key.strip():
        raise OutboundOutboxError(
            400,
            "OPERATION_KEY_REQUIRED",
            "Field 'operation_key' must be a non-empty string",
        )
    operation_key = raw_op_key.strip()
    if raw_op_key != operation_key or not _ID_PATTERN.match(operation_key):
        raise OutboundOutboxError(
            400,
            "OPERATION_KEY_INVALID",
            "Field 'operation_key' is malformed",
        )

    transport_receipt_id: Optional[str] = None
    if "transport_receipt_id" in payload and payload.get("transport_receipt_id") is not None:
        raw_receipt_id = payload.get("transport_receipt_id")
        if not isinstance(raw_receipt_id, str) or not raw_receipt_id.strip():
            raise OutboundOutboxError(
                400,
                "INVALID_TRANSPORT_RECEIPT_ID",
                "Field 'transport_receipt_id' must be a non-empty string when provided",
            )
        cleaned_receipt = raw_receipt_id.strip()
        if (
            len(cleaned_receipt) > _MAX_RECEIPT_ID_CHARS
            or any(ch in raw_receipt_id for ch in ("\x00", "\r", "\n"))
        ):
            raise OutboundOutboxError(
                400,
                "INVALID_TRANSPORT_RECEIPT_ID",
                "Field 'transport_receipt_id' contains invalid characters or exceeds limit",
            )
        transport_receipt_id = cleaned_receipt

    return {
        "action": raw_action,
        "item_kind": raw_item_kind,
        "item_id": item_id,
        "operation_key": operation_key,
        "transport_receipt_id": transport_receipt_id,
    }


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(dict(data), ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(encoded, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _fallback_ack_proactive_outbound(
    resolved_root: Path,
    *,
    outbound_id: str,
    action: str,
    operation_key: str,
    transport_receipt_id: Optional[str],
    observed_at: str,
    now_dt: datetime,
) -> dict[str, Any]:
    """Defensive standalone fallback if v03_economy_illness_bridge lacks ack_proactive_outbound_delivery."""
    state_path = eco_bridge.ecology_state_path(resolved_root)
    with _tx.state_file_lock(state_path):
        state, load_err = eco_bridge.read_stored_ecology_zero_write(resolved_root)
        if load_err:
            raise OutboundOutboxError(409, load_err, f"Cannot load ecology state: {load_err}")

        receipts = state.setdefault("operation_receipts", {})
        if not isinstance(receipts, dict):
            receipts = {}
            state["operation_receipts"] = receipts
        receipt_key = f"outbound_ack:{operation_key}"
        if receipt_key in receipts and isinstance(receipts[receipt_key], Mapping):
            cached = copy.deepcopy(dict(receipts[receipt_key]))
            cached["already_applied"] = True
            cached["command_status"] = "DUPLICATE"
            cached["status"] = "already_applied"
            return cached

        rb = state.setdefault("reality_bridge", {})
        records = rb.get("outbound_records")
        found: Optional[dict[str, Any]] = None
        if isinstance(records, list):
            for rec in records:
                if isinstance(rec, dict) and str(rec.get("outbound_id") or "") == outbound_id:
                    found = rec
                    break
        if found is None:
            raise OutboundOutboxError(404, "OUTBOUND_NOT_FOUND", f"Outbound item not found: {outbound_id}")

        prev_status = str(found.get("status") or "").upper()
        if transport_receipt_id:
            found["transport_receipt_id"] = transport_receipt_id

        if action == "claim":
            if prev_status != "APPROVED_FOR_DELIVERY":
                raise OutboundOutboxError(
                    409,
                    "OUTBOUND_STATE_CONFLICT",
                    f"Cannot claim item in status {prev_status!r}",
                )
            found["status"] = "CLAIMED_BY_TRANSPORT"
            found["delivery_ack_state"] = "CLAIMED_BY_TRANSPORT"
            found["epistemic_state"] = "claimed_by_transport"
            found["claimed_at"] = found.get("claimed_at") or observed_at
        elif action == "ack_delivered":
            if prev_status not in ("APPROVED_FOR_DELIVERY", "CLAIMED_BY_TRANSPORT"):
                raise OutboundOutboxError(
                    409,
                    "OUTBOUND_STATE_CONFLICT",
                    f"Cannot ack_delivered item in status {prev_status!r}",
                )
            found["status"] = "DELIVERED"
            found["delivery_ack_state"] = "SENT_PENDING_ACK"
            found["epistemic_state"] = "sent_pending_ack"
            found["partner_read_confirmed"] = False
            found["delivered_is_not_read"] = True
            found["delivered_at"] = observed_at
            found["expires_at"] = _format_iso(now_dt + timedelta(hours=24))
            found["reconcile_required"] = False
            rb["unanswered_streak"] = int(rb.get("unanswered_streak") or 0) + 1
        elif action == "ack_failed":
            if prev_status not in ("APPROVED_FOR_DELIVERY", "CLAIMED_BY_TRANSPORT"):
                raise OutboundOutboxError(
                    409,
                    "OUTBOUND_STATE_CONFLICT",
                    f"Cannot ack_failed item in status {prev_status!r}",
                )
            found["status"] = "FAILED"
            found["delivery_ack_state"] = "FAILED"
            found["epistemic_state"] = "delivery_failed"
            found["partner_read_confirmed"] = False
            found["failed_at"] = observed_at
            found["reconcile_required"] = False
        elif action == "ack_unknown":
            if prev_status not in ("APPROVED_FOR_DELIVERY", "CLAIMED_BY_TRANSPORT"):
                raise OutboundOutboxError(
                    409,
                    "OUTBOUND_STATE_CONFLICT",
                    f"Cannot ack_unknown item in status {prev_status!r}",
                )
            found["status"] = "UNKNOWN"
            found["delivery_ack_state"] = "TRANSPORT_UNKNOWN"
            found["epistemic_state"] = "transport_unknown"
            found["partner_read_confirmed"] = False
            found["reconcile_required"] = True
        elif action == "ack_read":
            if prev_status not in ("DELIVERED", "READ"):
                raise OutboundOutboxError(
                    409,
                    "OUTBOUND_STATE_CONFLICT",
                    f"Cannot ack_read item in status {prev_status!r}",
                )
            found["status"] = "READ"
            found["delivery_ack_state"] = "READ"
            found["epistemic_state"] = "partner_read_acked"
            found["partner_read_confirmed"] = True
            found["epistemic_ceiling"] = "read"
            found["read_at"] = found.get("read_at") or observed_at
            found["reconcile_required"] = False
            rb["unanswered_streak"] = 0

        receipt = {
            "outbound_id": outbound_id,
            "action": action,
            "previous_status": prev_status,
            "current_status": found["status"],
            "outbound_status": found["status"],
            "delivery_ack_state": found.get("delivery_ack_state"),
            "epistemic_state": found.get("epistemic_state"),
            "partner_read_confirmed": bool(found.get("partner_read_confirmed", False)),
            "transport_receipt_id": found.get("transport_receipt_id"),
            "operation_key": operation_key,
            "unanswered_streak": int(rb.get("unanswered_streak") or 0),
            "already_applied": False,
            "command_status": "APPLIED",
            "status": "applied",
        }
        receipts[receipt_key] = copy.deepcopy(receipt)
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = observed_at
        _atomic_write_json(state_path, state)
        return receipt


def _fallback_ack_deferred_reply(
    resolved_root: Path,
    *,
    msg_id: str,
    action: str,
    operation_key: str,
    transport_receipt_id: Optional[str],
    observed_at: str,
    now_dt: datetime,
) -> dict[str, Any]:
    """Defensive standalone fallback if autonomy_rhythm_view lacks ack_deferred_reply_delivery."""
    cache_key = f"{resolved_root.resolve()}|deferred|{operation_key}"
    if cache_key in _FALLBACK_DEFERRED_RECEIPTS:
        cached = copy.deepcopy(_FALLBACK_DEFERRED_RECEIPTS[cache_key])
        cached["already_applied"] = True
        cached["command_status"] = "DUPLICATE"
        cached["status"] = "already_applied"
        return cached

    state, load_err = rhythm_view.read_autonomy_rhythm_state(resolved_root)
    if load_err:
        raise OutboundOutboxError(409, load_err, f"Cannot load autonomy_rhythm state: {load_err}")

    pending = state.get("pending_messages") if isinstance(state, Mapping) else []
    target: Optional[Mapping[str, Any]] = None
    if isinstance(pending, list):
        for item in pending:
            if isinstance(item, Mapping) and str(item.get("msg_id") or "") == msg_id:
                target = item
                break
    if target is None:
        raise OutboundOutboxError(404, "DEFERRED_REPLY_NOT_FOUND", f"Deferred reply not found: {msg_id}")

    prev_status = str(target.get("status") or "").upper()
    due_dt = _parse_iso_dt(target.get("deliver_after"))
    is_ready = prev_status in DEFERRED_PENDING_STATUSES or (
        prev_status == "QUEUED_ON_PHONE" and due_dt is not None and due_dt <= now_dt
    )
    if not is_ready and action != "ack_read":
        raise OutboundOutboxError(
            409,
            "DEFERRED_REPLY_NOT_READY",
            f"Deferred reply {msg_id!r} is not ready yet",
        )

    if action == "claim":
        rhythm_view.apply_autonomy_rhythm_action(
            resolved_root,
            {"action": "claim_ready_messages"},
            subject_view=False,
            now_iso=observed_at,
        )
        new_status = "CLAIMED_BY_TRANSPORT"
    elif action in ("ack_delivered", "ack_read"):
        rhythm_view.apply_autonomy_rhythm_action(
            resolved_root,
            {"action": "clear_delivered_messages", "msg_ids": [msg_id]},
            subject_view=False,
            now_iso=observed_at,
        )
        new_status = "DELIVERED" if action == "ack_delivered" else "READ"
    elif action == "ack_failed":
        new_status = "FAILED"
    else:
        new_status = "UNKNOWN"

    receipt = {
        "msg_id": msg_id,
        "item_id": msg_id,
        "action": action,
        "previous_status": prev_status,
        "current_status": new_status,
        "transport_receipt_id": transport_receipt_id,
        "operation_key": operation_key,
        "already_applied": False,
        "command_status": "APPLIED",
        "status": "applied",
    }
    if len(_FALLBACK_DEFERRED_RECEIPTS) >= _MAX_FALLBACK_RECEIPTS:
        oldest_key = next(iter(_FALLBACK_DEFERRED_RECEIPTS))
        _FALLBACK_DEFERRED_RECEIPTS.pop(oldest_key, None)
    _FALLBACK_DEFERRED_RECEIPTS[cache_key] = copy.deepcopy(receipt)
    return receipt


def _sanitize_receipt(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    source: Mapping[str, Any] = raw
    nested = raw.get("receipt")
    if isinstance(nested, Mapping):
        merged = dict(raw)
        merged.update(nested)
        source = merged
    data_nested = raw.get("data")
    if isinstance(data_nested, Mapping):
        merged2 = dict(source)
        for k, v in data_nested.items():
            merged2.setdefault(k, v)
        source = merged2
    out: dict[str, Any] = {}
    for key, val in source.items():
        if not isinstance(key, str):
            continue
        low = key.lower()
        if low in _PRIVATE_OR_INTERNAL_KEYS:
            continue
        if key in _PUBLIC_RECEIPT_ALLOWED_KEYS and val is not None:
            if isinstance(val, (str, int, float, bool)):
                out[key] = val
    return out


def _detect_idempotent_replay(raw: Any) -> bool:
    if not isinstance(raw, Mapping):
        return False
    if (
        raw.get("already_applied") is True
        or raw.get("duplicate") is True
        or raw.get("idempotent_replay") is True
        or raw.get("replayed") is True
    ):
        return True
    if str(raw.get("command_status") or "").upper() == "DUPLICATE":
        return True
    if str(raw.get("status") or "").lower() in ("already_applied", "duplicate"):
        return True
    nested = raw.get("data")
    if isinstance(nested, Mapping):
        if (
            nested.get("already_applied") is True
            or nested.get("duplicate") is True
            or nested.get("idempotent_replay") is True
            or nested.get("replayed") is True
        ):
            return True
        if str(nested.get("command_status") or "").upper() == "DUPLICATE":
            return True
    return False


def _extract_item_status(raw: Any, clean_receipt: Mapping[str, Any], action: str) -> str:
    for key in ("current_status", "outbound_status", "item_status"):
        val = clean_receipt.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().upper()
    if isinstance(raw, Mapping):
        for key in ("current_status", "outbound_status", "item_status"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().upper()
        nested = raw.get("data")
        if isinstance(nested, Mapping):
            for key in ("current_status", "outbound_status", "item_status"):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip().upper()
    fallback_map = {
        "claim": "CLAIMED_BY_TRANSPORT",
        "ack_delivered": "DELIVERED",
        "ack_failed": "FAILED",
        "ack_unknown": "UNKNOWN",
        "ack_read": "READ",
    }
    return fallback_map.get(action, "UNKNOWN")


def handle_outbound_ack_action(
    day_root: Path | str,
    payload: Any,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Validate and dispatch ``POST /api/phone/outbound/ack`` idempotently."""
    actor_side = "subject" if bool(has_subject_capability) else "partner"
    resolved_root = _resolve_day_root(day_root)
    now_dt = _resolve_now_dt(now_iso)
    observed_at = _format_iso(now_dt)

    try:
        validated = _validate_ack_payload(payload)
    except OutboundOutboxError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, extra=exc.extra)

    action = validated["action"]
    item_kind = validated["item_kind"]
    item_id = validated["item_id"]
    operation_key = validated["operation_key"]
    transport_receipt_id = validated["transport_receipt_id"]

    # Subject capability must never forge partner read confirmation.
    if actor_side == "subject" and action == "ack_read":
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "Subject side cannot forge partner ack_read confirmation",
        )

    try:
        if item_kind == ITEM_KIND_PROACTIVE:
            ack_fn = getattr(eco_bridge, "ack_proactive_outbound_delivery", None)
            if callable(ack_fn):
                raw_result = _call_with_supported_kwargs(
                    ack_fn,
                    resolved_root,
                    {
                        "outbound_id": item_id,
                        "item_id": item_id,
                        "action": action,
                        "operation_key": operation_key,
                        "transport_receipt_id": transport_receipt_id,
                        "now_iso": observed_at,
                    },
                )
            else:
                raw_result = _fallback_ack_proactive_outbound(
                    resolved_root,
                    outbound_id=item_id,
                    action=action,
                    operation_key=operation_key,
                    transport_receipt_id=transport_receipt_id,
                    observed_at=observed_at,
                    now_dt=now_dt,
                )
        else:
            ack_fn = getattr(rhythm_view, "ack_deferred_reply_delivery", None)
            if callable(ack_fn):
                raw_result = _call_with_supported_kwargs(
                    ack_fn,
                    resolved_root,
                    {
                        "msg_id": item_id,
                        "item_id": item_id,
                        "action": action,
                        "operation_key": operation_key,
                        "transport_receipt_id": transport_receipt_id,
                        "now_iso": observed_at,
                    },
                )
            else:
                raw_result = _fallback_ack_deferred_reply(
                    resolved_root,
                    msg_id=item_id,
                    action=action,
                    operation_key=operation_key,
                    transport_receipt_id=transport_receipt_id,
                    observed_at=observed_at,
                    now_dt=now_dt,
                )
    except OutboundOutboxError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, extra=exc.extra)
    except eco_bridge.V03EcologyError as exc:
        return _error_response(
            int(getattr(exc, "status_code", 400)),
            str(getattr(exc, "reason_code", "OUTBOUND_ACK_FAILED")),
            str(exc),
        )
    except rhythm_view.AutonomyRhythmNotFoundError as exc:
        return _error_response(404, exc.reason_code, str(exc))
    except rhythm_view.AutonomyRhythmConflictError as exc:
        return _error_response(409, exc.reason_code, str(exc))
    except rhythm_view.AutonomyRhythmValidationError as exc:
        return _error_response(400, exc.reason_code, str(exc))
    except (TypeError, ValueError) as exc:
        return _error_response(400, "INVALID_FIELDS", str(exc))

    if isinstance(raw_result, Mapping) and raw_result.get("ok") is False:
        reason = str(raw_result.get("reason_code") or raw_result.get("error_code") or "OUTBOUND_ACK_REJECTED")
        raw_code = raw_result.get("http_status")
        if isinstance(raw_code, int) and not isinstance(raw_code, bool) and 400 <= raw_code < 600:
            code = raw_code
        elif str(raw_result.get("status") or "").lower() == "not_found" or "NOT_FOUND" in reason.upper():
            code = 404
        elif "CONFLICT" in reason.upper():
            code = 409
        else:
            code = 400
        return _error_response(code, reason, str(raw_result.get("detail") or reason))

    is_dup = _detect_idempotent_replay(raw_result)
    clean_receipt = _sanitize_receipt(raw_result)
    item_status = _extract_item_status(raw_result, clean_receipt, action)
    resolved_receipt_id = (
        clean_receipt.get("transport_receipt_id")
        if isinstance(clean_receipt.get("transport_receipt_id"), str)
        else transport_receipt_id
    )

    data: dict[str, Any] = {
        "schema": OUTBOX_ACK_SCHEMA,
        "observed_at": observed_at,
        "action": action,
        "item_id": item_id,
        "item_kind": item_kind,
        "operation_key": operation_key,
        "already_applied": bool(is_dup),
        "item_status": item_status,
        "transport_receipt_id": resolved_receipt_id,
        "receipt": clean_receipt,
    }
    return 200, {
        "ok": True,
        "status": "already_applied" if is_dup else "applied",
        "already_applied": bool(is_dup),
        "command_status": "DUPLICATE" if is_dup else "APPLIED",
        "zero_write": False,
        "llm_calls": 0,
        "data": data,
    }


def mutate_outbound_ack_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Slice adapter wrapper for ``POST /api/phone/outbound/ack``."""
    norm = str(actor_side or "partner").strip().lower()
    if norm == "her":
        norm = "subject"
    if norm not in ("partner", "subject"):
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Unsupported actor_side: {actor_side!r}",
        )
    return handle_outbound_ack_action(
        day_root,
        payload,
        has_subject_capability=(norm == "subject"),
        now_iso=now_iso,
    )


__all__ = [
    "DEFERRED_PENDING_STATUSES",
    "ENDPOINT_ACK",
    "ENDPOINT_PENDING",
    "FORBIDDEN_IDENTITY_KEYS",
    "ITEM_KIND_DEFERRED",
    "ITEM_KIND_PROACTIVE",
    "OUTBOUND_ACK_ACTIONS",
    "OUTBOUND_ACK_ALLOWED_FIELDS",
    "OUTBOUND_ITEM_KINDS",
    "OUTBOX_ACK_SCHEMA",
    "OUTBOX_PENDING_SCHEMA",
    "PROACTIVE_PENDING_STATUSES",
    "OutboundOutboxError",
    "handle_outbound_ack_action",
    "mutate_outbound_ack_view",
    "read_outbound_pending_view",
    "read_pending_outbound_outbox_zero_write",
]
