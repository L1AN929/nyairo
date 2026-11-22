#!/usr/bin/env python3
"""HTTP view & action gate for ``/api/phone/v02/world-lineage``.

Contracts:
1. ``read_v02_world_lineage_view``:
   - 100% zero-write on empty, non-existent, or populated ``day_root``;
   - Returns HTTP 200 envelope (mirrored top-level and ``data``) containing
     ``fact_sources``, ``layered_news`` (``local_world_news``, ``external_real_facts``,
     ``unverified_hearsay``), ``body_history``, ``carrier_adaptation``,
     ``backup_restore_guard``, ``meaning_versions``, ``partner_meaning_notes``,
     ``lineage_branches``, ``llm_calls=0``, ``zero_write=True``.
   - Filters ``shared is False`` entries when ``has_subject_capability=False``.
2. ``mutate_v02_world_lineage_view``:
   - Strict per-action field allowlists and recursive forbidden identity/score key scan
     (``400 INVALID_FIELDS``);
   - Strict role gate (``403 FORBIDDEN_ACTOR_SIDE``) for subject-only actions and for
     partner attempts to write ``LOCAL_WORLD_NEWS``;
   - Native ``operation_key`` idempotency (``DUPLICATE`` / ``409 OPERATION_KEY_CONFLICT``)
     with zero-write preflight before locking.
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

from components.life.scripts import v02_world_news_carrier_lineage as wl_engine

ENDPOINT_PATH = "/api/phone/v02/world-lineage"
VIEW_SCHEMA = "nyairo.v02.world_lineage.v1"

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "expected_revision",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "register_external_fact_source",
    "record_layered_news_item",
    "sync_carrier_profile_and_adapt_habits",
    "propose_meaning_note",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "record_body_history_entry",
    "record_meaning_version",
    "record_lineage_branch",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "register_external_fact_source": COMMON_COMMAND_FIELDS | frozenset({
        "title_cn",
        "source_kind",
        "source_ref",
        "trust_tier",
        "note_cn",
        "shared",
    }),
    "record_layered_news_item": COMMON_COMMAND_FIELDS | frozenset({
        "layer",
        "headline_cn",
        "title_cn",
        "summary_cn",
        "source_id",
        "source_ref",
        "trust_tier",
        "topic_tag",
        "location_id",
        "shared",
    }),
    "sync_carrier_profile_and_adapt_habits": COMMON_COMMAND_FIELDS | frozenset({
        "carrier_kind",
        "carrier_label_cn",
        "visual_preview",
        "audio_output",
        "desk_stage",
        "camera_input",
        "note_cn",
    }),
    "propose_meaning_note": COMMON_COMMAND_FIELDS | frozenset({
        "subject_ref",
        "item_ref",
        "target_ref",
        "subject_title_cn",
        "note_cn",
    }),
    "record_body_history_entry": COMMON_COMMAND_FIELDS | frozenset({
        "period_label",
        "comfort_summary_cn",
        "illness_or_strain_tag",
        "rest_adjustment_cn",
        "shared",
    }),
    "record_meaning_version": COMMON_COMMAND_FIELDS | frozenset({
        "subject_ref",
        "item_ref",
        "target_ref",
        "subject_title_cn",
        "earlier_meaning_cn",
        "current_meaning_cn",
        "shared",
    }),
    "record_lineage_branch": COMMON_COMMAND_FIELDS | frozenset({
        "parent_branch_id",
        "origin_instance_id",
        "branch_label_cn",
        "reason_cn",
        "shared",
    }),
}

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "perspective",
    "revision",
    "observed_at",
    "fact_sources",
    "layered_news",
    "body_history",
    "carrier_adaptation",
    "backup_restore_guard",
    "meaning_versions",
    "partner_meaning_notes",
    "lineage_branches",
    "action_receipt",
    "llm_calls",
    "zero_write",
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
        raise wl_engine.V02WorldLineageError(400, "INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise wl_engine.V02WorldLineageError(
                    400,
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded key forbidden: {raw_key!r}",
                )
            if (
                raw_key in wl_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
                or raw_key.lower() in wl_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
            ):
                raise wl_engine.V02WorldLineageError(
                    400,
                    "INVALID_FIELDS",
                    f"Forged identity or forbidden field rejected: {raw_key!r}",
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _validate_request_payload(payload: Any, *, actor_side: str) -> tuple[str, str]:
    if not isinstance(actor_side, str) or not actor_side.strip():
        raise wl_engine.V02WorldLineageError(403, "FORBIDDEN_ACTOR_SIDE", "Invalid actor_side")
    norm_actor = actor_side.strip().lower()
    if norm_actor not in ("partner", "subject"):
        raise wl_engine.V02WorldLineageError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Unsupported actor_side: {actor_side!r}",
        )

    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise wl_engine.V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            "Payload must be a non-empty JSON object",
        )

    _scan_payload_keys_recursive(payload)

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        raise wl_engine.V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            "Field 'action' must be a non-empty trimmed string",
        )
    action = raw_action

    if action not in ACTION_ALLOWED_FIELDS:
        raise wl_engine.V02WorldLineageError(400, "INVALID_FIELDS", f"Unknown action: {action!r}")

    allowed_actions = SUBJECT_ALLOWED_ACTIONS if norm_actor == "subject" else PARTNER_ALLOWED_ACTIONS
    if action not in allowed_actions:
        raise wl_engine.V02WorldLineageError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is forbidden for actor_side={norm_actor!r}",
        )

    unknown_fields = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
    if unknown_fields:
        raise wl_engine.V02WorldLineageError(
            400,
            "INVALID_FIELDS",
            f"Unknown fields for {action!r}: {unknown_fields}",
        )

    if "operation_key" in payload:
        op_val = payload.get("operation_key")
        if not isinstance(op_val, str) or not op_val.strip() or op_val != op_val.strip():
            raise wl_engine.V02WorldLineageError(
                400,
                "INVALID_FIELDS",
                "Field 'operation_key' must be a non-empty trimmed string",
            )

    if action == "record_layered_news_item":
        raw_layer = payload.get("layer")
        if isinstance(raw_layer, str) and raw_layer.strip() == "LOCAL_WORLD_NEWS" and norm_actor != "subject":
            raise wl_engine.V02WorldLineageError(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                "Partner cannot write LOCAL_WORLD_NEWS",
            )

    return norm_actor, action


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
    data_block["idempotent_replay"] = is_dup

    envelope: dict[str, Any] = {
        "ok": True,
        "status": "ok" if read_only else ("already_applied" if is_dup else "applied"),
        "command_status": command_status,
        "idempotent_replay": is_dup,
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "data": data_block,
    }
    for key, val in data_block.items():
        if key not in envelope:
            envelope[key] = copy.deepcopy(val)
    return envelope


def read_v02_world_lineage_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
    now: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """100% zero-write projection for ``GET /api/phone/v02/world-lineage``."""
    eff_now = now_iso if now_iso is not None else now
    try:
        projected = wl_engine.read_world_news_carrier_lineage_view(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=eff_now,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except wl_engine.V02WorldLineageError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v02_world_lineage_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Validated, role-gated, idempotent mutation for ``POST /api/phone/v02/world-lineage``."""
    try:
        norm_actor, action = _validate_request_payload(payload, actor_side=actor_side)
        action_payload = {k: v for k, v in payload.items() if k != "action"}

        dispatch = {
            "register_external_fact_source": wl_engine.register_external_fact_source,
            "record_layered_news_item": wl_engine.record_layered_news_item,
            "sync_carrier_profile_and_adapt_habits": wl_engine.sync_carrier_profile_and_adapt_habits,
            "propose_meaning_note": wl_engine.propose_meaning_note,
            "record_body_history_entry": wl_engine.record_body_history_entry,
            "record_meaning_version": wl_engine.record_meaning_version,
            "record_lineage_branch": wl_engine.record_lineage_branch,
        }
        receipt = dispatch[action](
            day_root,
            action_payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
        cmd_status = "DUPLICATE" if receipt.get("idempotent_replay") else "APPLIED"
        projected = wl_engine.read_world_news_carrier_lineage_view(
            day_root,
            has_subject_capability=(norm_actor == "subject"),
            now_iso=now_iso,
        )
        projected["action_receipt"] = copy.deepcopy(receipt)
        projected["zero_write"] = bool(cmd_status == "DUPLICATE")
        return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)
    except wl_engine.V02WorldLineageError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v02_world_lineage_view",
    "read_v02_world_lineage_view",
]
