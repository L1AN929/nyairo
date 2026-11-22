#!/usr/bin/env python3
"""HTTP view and action gate for ``/api/phone/v08/composable-body``.

Responsibilities
----------------
1. ``read_v08_composable_body_view`` — 100% zero-write projection of the two
   optional body packs.  A partner view carries *only* an active shared care
   hint and the recent ordinary care rows; every pack, enabled flag, sleep
   activity, supply flag and feeling line is stripped.
2. ``mutate_v08_composable_body_view`` — strict ``ACTION_ALLOWED_FIELDS``
   whitelist, subject/partner boundary and identity/score-key refusal.  A
   partner attempt at any subject action is a named ``403``.
3. Responses always carry ``llm_calls == 0`` and never echo client identity.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - flat life/scripts import path
    import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]

try:
    from components.world.scripts import v08_composable_body as engine
except ImportError:  # pragma: no cover - flat world/scripts import path
    import v08_composable_body as engine  # type: ignore[no-redef]

ROUTE_V08_COMPOSABLE_BODY = "/api/phone/v08/composable-body"
ENDPOINT_PATH = ROUTE_V08_COMPOSABLE_BODY
VIEW_SCHEMA = engine.VIEW_SCHEMA

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
})

SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "configure_body_pack",
    "use_cycle_supply",
    "bind_sleep_activity",
    "settle_sleep_wake",
    "resolve_garment_care",
    "share_body_care_hint_once",
    "clear_private_body_history",
})

PARTNER_ACTIONS: frozenset[str] = frozenset({"offer_ordinary_care"})

ALLOWED_ACTIONS: frozenset[str] = SUBJECT_ACTIONS | PARTNER_ACTIONS

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "configure_body_pack": COMMON_COMMAND_FIELDS | frozenset({
        "pack_id",
        "enabled",
        "anchor_iso",
        "cycle_length_days",
        "active_days",
        "discomfort_profile",
        "pre_symptom_enabled",
        "probability_permille",
    }),
    "use_cycle_supply": COMMON_COMMAND_FIELDS | frozenset({
        "supply_object_id",
        "note_cn",
    }),
    "bind_sleep_activity": COMMON_COMMAND_FIELDS | frozenset({
        "sleep_activity_id",
        "started_at_iso",
        "planned_wake_iso",
    }),
    "settle_sleep_wake": COMMON_COMMAND_FIELDS | frozenset({
        "sleep_activity_id",
        "woke_at_iso",
    }),
    "resolve_garment_care": COMMON_COMMAND_FIELDS | frozenset({
        "mode",
        "garment_object_id",
    }),
    "share_body_care_hint_once": COMMON_COMMAND_FIELDS | frozenset({
        "hint_cn",
        "ttl_minutes",
    }),
    "clear_private_body_history": COMMON_COMMAND_FIELDS | frozenset({"confirm"}),
    "offer_ordinary_care": COMMON_COMMAND_FIELDS | frozenset({
        "care_kind",
        "note_cn",
    }),
}

_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "configure_body_pack": frozenset({"pack_id", "enabled"}),
    "use_cycle_supply": frozenset(),
    "bind_sleep_activity": frozenset({
        "sleep_activity_id",
        "started_at_iso",
        "planned_wake_iso",
    }),
    "settle_sleep_wake": frozenset({"sleep_activity_id", "woke_at_iso"}),
    "resolve_garment_care": frozenset({"mode"}),
    "share_body_care_hint_once": frozenset({"hint_cn"}),
    "clear_private_body_history": frozenset({"confirm"}),
    "offer_ordinary_care": frozenset({"care_kind"}),
}


class _PartnerActionRefused(engine.V08ComposableBodyError):
    """A partner-door attempt at a subject-only body action (always 403)."""

    def __init__(self, reason_code: str = "FORBIDDEN_PARTNER_ACTION", message: str = "") -> None:
        super().__init__(reason_code, message or reason_code)
        self.status_code = 403


def _error_response(
    status_code: int,
    reason_code: str,
    detail: str = "",
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code)
    return int(status_code), {
        "ok": False,
        "status": "rejected" if status_code < 500 else "unavailable",
        "command_status": "REJECTED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "reason_code": code_str,
        "error_code": code_str,
        "reason": code_str,
        "detail": str(detail or code_str),
        "llm_calls": 0,
        "data": {},
    }


def _fail(reason_code: str, detail: str = "") -> None:
    raise engine.V08ComposableBodyError(reason_code, detail or reason_code)


def _validate_request_payload(
    payload: Any, *, actor_side: str
) -> tuple[str, str]:
    if actor_side not in ("partner", "subject"):
        _fail("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping) or not payload:
        _fail("INVALID_FIELDS", "payload must be a non-empty JSON object")
    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            _fail("INVALID_FIELDS", "blank or padded key forbidden")

    identity = sorted(set(payload) & engine.FORBIDDEN_IDENTITY_KEYS)
    if identity:
        _fail(
            "CLIENT_IDENTITY_REFUSED",
            "client identity or score keys are refused: " + ", ".join(identity),
        )

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        _fail("INVALID_FIELDS", "Missing required field 'action'")
    action = action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        _fail("INVALID_FIELDS", f"Unknown action: {action!r}")

    unknown = sorted(set(payload) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        _fail("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    missing = sorted(
        key for key in _REQUIRED_FIELDS[action] if payload.get(key) is None
    )
    if missing:
        _fail("INVALID_FIELDS", f"Missing fields for {action}: {missing}")

    if action in SUBJECT_ACTIONS:
        if actor_side != "subject":
            raise _PartnerActionRefused(
                "FORBIDDEN_PARTNER_ACTION",
                f"Action {action!r} is the subject's own body act;"
                " the partner door cannot call it",
            )
        effective_actor = "subject"
    else:
        effective_actor = "partner"

    _validate_scalar_fields(action, payload)
    return action, effective_actor


def _validate_scalar_fields(action: str, payload: Mapping[str, Any]) -> None:
    for key in ("operation_key", "intent_fingerprint"):
        value = payload.get(key)
        if value is not None and (
            not isinstance(value, str) or not value.strip() or value != value.strip()
        ):
            _fail("INVALID_FIELDS", f"{key} must be a trimmed non-empty string")

    for key in (
        "pack_id",
        "anchor_iso",
        "discomfort_profile",
        "supply_object_id",
        "note_cn",
        "sleep_activity_id",
        "started_at_iso",
        "planned_wake_iso",
        "woke_at_iso",
        "mode",
        "garment_object_id",
        "hint_cn",
        "care_kind",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            _fail("INVALID_FIELDS", f"{key} must be a string")
        if not value.strip():
            _fail("INVALID_FIELDS", f"{key} must not be blank")

    for key in ("enabled", "pre_symptom_enabled", "confirm"):
        if key in payload and payload[key] is not None:
            if not isinstance(payload[key], bool):
                _fail("INVALID_FIELDS", f"{key} must be a JSON boolean")

    for key in ("cycle_length_days", "active_days", "probability_permille", "ttl_minutes"):
        value = payload.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            _fail("INVALID_FIELDS", f"{key} must be an integer")


def _compose_envelope(
    result: Mapping[str, Any],
    *,
    action: str,
    perspective: str,
) -> dict[str, Any]:
    data = copy.deepcopy(dict(result))
    data["action"] = action
    data["perspective"] = perspective
    data["llm_calls"] = 0
    already = bool(result.get("already_applied") is True) or str(
        result.get("status") or ""
    ) == "already_applied"
    envelope: dict[str, Any] = {
        "ok": True,
        "status": "already_applied" if already else "applied",
        "command_status": "DUPLICATE" if already else "APPLIED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "perspective": perspective,
        "action": action,
        "llm_calls": 0,
        "data": data,
    }
    for key, value in data.items():
        if key in (
            "status",
            "command_status",
            "ok",
            "schema",
            "endpoint",
            "perspective",
            "action",
            "llm_calls",
        ):
            continue
        envelope[key] = value
    return envelope


def _apply_forget_barrier(
    projected: Mapping[str, Any],
    barrier: Mapping[str, Any],
) -> dict[str, Any]:
    """Filter composable-body activities, care rows, traces, and hints through the zero-write forget barrier."""
    out = copy.deepcopy(dict(projected))
    fail_closed = bool(barrier.get("fail_closed"))

    for list_key in ("sleep_activities", "recent_partner_care", "traces", "records", "supply_logs"):
        if isinstance(out.get(list_key), list):
            out[list_key] = _rlf.filter_collection_by_barrier(out[list_key], barrier)

    if out.get("shared_care_hint") is not None:
        if fail_closed or _rlf.is_entity_forget_blocked(out["shared_care_hint"], barrier):
            out["shared_care_hint"] = None

    def _filter_pack_block(block: Any) -> None:
        if not isinstance(block, dict):
            return
        for lkey in ("sleep_activities", "supply_logs", "care_events", "records", "traces"):
            if isinstance(block.get(lkey), list):
                block[lkey] = _rlf.filter_collection_by_barrier(block[lkey], barrier)
        if fail_closed:
            if "garment_care_note_cn" in block:
                block["garment_care_note_cn"] = ""
            if "pending_garment_care" in block:
                block["pending_garment_care"] = False
            if "last_supply_cycle_key" in block:
                block["last_supply_cycle_key"] = None

    _filter_pack_block(out.get("menstrual_cycle"))
    _filter_pack_block(out.get("nocturnal_emission"))
    packs = out.get("packs")
    if isinstance(packs, dict):
        _filter_pack_block(packs.get("menstrual_cycle"))
        _filter_pack_block(packs.get("nocturnal_emission"))

    if fail_closed:
        if "current_feeling_cn" in out:
            out["current_feeling_cn"] = ""
        if "garment_care_note_cn" in out:
            out["garment_care_note_cn"] = ""
        if "pending_garment_care" in out:
            out["pending_garment_care"] = False
        if "needs_supply_change" in out:
            out["needs_supply_change"] = False

    return out


def read_v08_composable_body_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read the composable-body projection (pure zero-write, partner stripped, forget barrier enforced)."""

    try:
        viewer = "subject" if has_subject_capability else "partner"
        barrier = _rlf.load_unified_forget_barrier_readonly(day_root)
        projected = _apply_forget_barrier(
            engine.read_composable_body_zero_write(
                day_root,
                has_subject_capability=bool(has_subject_capability),
                now_iso=now_iso,
            ),
            barrier,
        )
        data_block = copy.deepcopy(projected)
        data_block["perspective"] = viewer
        data_block["llm_calls"] = 0
        envelope: dict[str, Any] = {
            "ok": True,
            "status": "ok",
            "command_status": "READ",
            "schema": VIEW_SCHEMA,
            "endpoint": ENDPOINT_PATH,
            "perspective": viewer,
            "llm_calls": 0,
            "data": data_block,
        }
        for key, value in data_block.items():
            if key == "data":
                continue
            envelope[key] = value
        return 200, envelope
    except engine.V08ComposableBodyError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v08_composable_body_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Apply one body action behind the whitelist and permission boundary."""

    try:
        action, effective_actor = _validate_request_payload(
            payload, actor_side=actor_side
        )
        body = dict(payload)
        if action == "configure_body_pack":
            result = engine.configure_body_pack(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "use_cycle_supply":
            result = engine.use_cycle_supply(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "bind_sleep_activity":
            result = engine.bind_sleep_activity(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "settle_sleep_wake":
            result = engine.settle_sleep_wake(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "resolve_garment_care":
            result = engine.resolve_garment_care(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "share_body_care_hint_once":
            result = engine.share_body_care_hint_once(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "clear_private_body_history":
            result = engine.clear_private_body_history(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        else:  # offer_ordinary_care
            result = engine.offer_ordinary_care(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "partner"
        return 200, _compose_envelope(result, action=action, perspective=perspective)
    except engine.V08ComposableBodyError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "ALLOWED_ACTIONS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ACTIONS",
    "ROUTE_V08_COMPOSABLE_BODY",
    "SUBJECT_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v08_composable_body_view",
    "read_v08_composable_body_view",
]
