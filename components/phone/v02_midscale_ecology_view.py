#!/usr/bin/env python3
"""HTTP view and action gate for ``/api/phone/v02/ecology-growth`` (Wave B1: F-175..F-178).

Contracts:
1. ``read_v02_ecology_growth_view``:
   - 100% zero-write on empty, non-existent, or populated ``day_root``
     (zero directories, files, locks, or mtime touches).
   - Filters all ``shared == False`` items when ``has_subject_capability=False``
     and strips host paths and forbidden keys.
2. ``mutate_v02_ecology_growth_view``:
   - Strict ``ACTION_ALLOWED_FIELDS`` allowlist and recursive forbidden
     identity/score key scanning (``400 INVALID_FIELDS``).
   - Strict ``PARTNER_ALLOWED_ACTIONS`` vs ``SUBJECT_ALLOWED_ACTIONS``
     boundary (partner calling subject-only action returns
     ``403 FORBIDDEN_ACTOR_SIDE``).
   - ``operation_key`` idempotency (``DUPLICATE`` / ``already_applied`` on
     identical replay, ``409 OPERATION_KEY_CONFLICT`` on conflicting payload).
   - Zero-write preflight before locking on errors and idempotent replays.
   - ``llm_calls == 0``, pure LF, zero emojis, neutral ``"TA"``.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_midscale_ecology_engine as eco_engine  # noqa: E402

ENDPOINT_PATH = "/api/phone/v02/ecology-growth"
VIEW_SCHEMA = "nyairo.v02.ecology_growth.v1"
V02_ECOLOGY_GROWTH_SCHEMA = VIEW_SCHEMA

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "suggest_ecology_spark",
    "acknowledge_absence_observation",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "snapshot_room_expectations",
    "record_absence_observation",
    "record_ecology_chain_link",
    "record_practice_standard_evolution",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "snapshot_room_expectations": COMMON_COMMAND_FIELDS | frozenset({
        "items",
        "replace",
        "shared",
    }),
    "record_absence_observation": COMMON_COMMAND_FIELDS | frozenset({
        "observation_id",
        "item_id",
        "title_cn",
        "kind",
        "expected_place_cn",
        "observed_place_cn",
        "summary_cn",
        "shared",
    }),
    "acknowledge_absence_observation": COMMON_COMMAND_FIELDS | frozenset({
        "observation_id",
        "note_cn",
        "who_moved",
    }),
    "record_ecology_chain_link": COMMON_COMMAND_FIELDS | frozenset({
        "chain_id",
        "theme_cn",
        "step_kind",
        "ref_id",
        "title_cn",
        "note_cn",
        "shared",
    }),
    "suggest_ecology_spark": COMMON_COMMAND_FIELDS | frozenset({
        "spark_id",
        "theme_cn",
        "spark_text_cn",
        "linked_kind",
    }),
    "record_practice_standard_evolution": COMMON_COMMAND_FIELDS | frozenset({
        "evolution_id",
        "domain",
        "before_criterion_cn",
        "after_criterion_cn",
        "practice_evidence_count",
        "evidence_refs",
        "distinction_note_cn",
        "shared",
    }),
}

_SNAPSHOT_ITEM_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "item_id",
    "title_cn",
    "expected_place_cn",
    "kind",
    "shared",
})

_OPTIONAL_NULLABLE_FIELDS: frozenset[str] = frozenset({
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
    "items",
    "replace",
    "shared",
    "observation_id",
    "observed_place_cn",
    "summary_cn",
    "note_cn",
    "who_moved",
    "chain_id",
    "theme_cn",
    "spark_id",
    "linked_kind",
    "evolution_id",
    "evidence_refs",
    "distinction_note_cn",
})

_OPTIONAL_BLANK_STRING_FIELDS: frozenset[str] = frozenset({
    "intent_fingerprint",
    "observed_place_cn",
    "summary_cn",
    "note_cn",
    "theme_cn",
    "linked_kind",
    "distinction_note_cn",
})

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "perspective",
    "revision",
    "observed_at",
    "room_expectations_count",
    "absence_observations",
    "autobiography_opportunities",
    "ecology_chains",
    "partner_ecology_sparks",
    "practice_standard_evolutions",
    "action_receipt",
    "zero_write",
    "llm_calls",
)


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


def _scan_payload_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise eco_engine.V02ValidationError("INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise eco_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded key forbidden: {raw_key!r}",
                )
            lowered = raw_key.lower()
            if (
                raw_key in eco_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
                or lowered in eco_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
            ):
                raise eco_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    f"Forbidden identity or score field rejected: {raw_key!r}",
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _validate_request_payload(payload: Any, *, actor_side: str) -> str:
    if not isinstance(actor_side, str) or actor_side not in ("partner", "subject"):
        raise eco_engine.V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Forbidden actor_side: {actor_side!r}",
        )
    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise eco_engine.V02ValidationError("INVALID_FIELDS", "Payload must be a non-empty JSON object")

    _scan_payload_keys_recursive(payload)

    if "action" not in payload:
        raise eco_engine.V02ValidationError("INVALID_FIELDS", "Missing required field 'action'")
    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        raise eco_engine.V02ValidationError(
            "INVALID_FIELDS",
            "Field 'action' must be a non-empty trimmed string",
        )
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        raise eco_engine.V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    allowed_actions = SUBJECT_ALLOWED_ACTIONS if actor_side == "subject" else PARTNER_ALLOWED_ACTIONS
    if action not in allowed_actions:
        raise eco_engine.V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is not permitted for {actor_side}",
        )

    unknown = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        raise eco_engine.V02ValidationError("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    for k, v in payload.items():
        if k == "action":
            continue
        if v is None:
            if k in _OPTIONAL_NULLABLE_FIELDS and not (
                (action == "acknowledge_absence_observation" and k == "observation_id")
                or (action == "suggest_ecology_spark" and k == "theme_cn")
            ):
                continue
            raise eco_engine.V02ValidationError("INVALID_FIELDS", f"Null field not allowed: {k}")
        if isinstance(v, str) and not v.strip():
            if k in _OPTIONAL_BLANK_STRING_FIELDS and not (
                action == "suggest_ecology_spark" and k == "theme_cn"
            ):
                continue
            raise eco_engine.V02ValidationError("INVALID_FIELDS", f"Blank string not allowed: {k}")

    if action == "snapshot_room_expectations" and payload.get("items") is not None:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise eco_engine.V02ValidationError("INVALID_FIELDS", "Field 'items' must be a list")
        for idx, item in enumerate(raw_items):
            if not isinstance(item, Mapping):
                raise eco_engine.V02ValidationError("INVALID_FIELDS", f"items[{idx}] must be an object")
            bad_sub = sorted(set(item.keys()) - _SNAPSHOT_ITEM_ALLOWED_FIELDS)
            if bad_sub:
                raise eco_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    f"Unknown fields in items[{idx}]: {bad_sub}",
                )

    return action


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    read_only: bool,
    command_status: str = "READ",
) -> dict[str, Any]:
    data_block: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "read_only": bool(read_only),
    }
    for key in _MIRRORED_DATA_KEYS:
        if key in projected:
            data_block[key] = copy.deepcopy(projected[key])

    is_dup = command_status == "DUPLICATE"
    if is_dup:
        data_block["idempotent_replay"] = True

    envelope: dict[str, Any] = {
        "ok": True,
        "status": "ok" if read_only else ("already_applied" if is_dup else "applied"),
        "command_status": command_status,
        "idempotent_replay": is_dup,
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "llm_calls": 0,
        "data": data_block,
    }
    for key, val in data_block.items():
        if key not in envelope:
            envelope[key] = copy.deepcopy(val)
    return envelope


def read_v02_ecology_growth_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
    now: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read the combined F-175..F-178 mid-scale ecology view (100% zero-write)."""
    eff_now = now_iso if now_iso is not None else now
    try:
        projected = eco_engine.read_midscale_ecology_view(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=eff_now,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except eco_engine.V02MidscaleEcologyError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v02_ecology_growth_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Execute a validated mutation on ``/api/phone/v02/ecology-growth``."""
    try:
        _validate_request_payload(payload, actor_side=actor_side)
        projected, cmd_status = eco_engine.apply_midscale_ecology_mutation(
            day_root,
            payload,
            actor_side=actor_side,
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)
    except eco_engine.V02MidscaleEcologyError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "V02_ECOLOGY_GROWTH_SCHEMA",
    "VIEW_SCHEMA",
    "mutate_v02_ecology_growth_view",
    "read_v02_ecology_growth_view",
]
