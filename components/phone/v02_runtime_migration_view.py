#!/usr/bin/env python3
"""HTTP 视图与动作门控封装：``/api/phone/v02/runtime-migration``。

职责与契约边界：
1. ``read_v02_runtime_migration_view``：
   - 100% 零磁盘写入（不创建目录、不生成临时文件、不触碰 mtime）；
   - 返回 ``200`` 信封（顶层与 ``data`` 镜像）包含 ``runtime_mode``、``wake_coalescer``、
     ``today_capability_card``、``migration_suitcase``、``source_freshness``、
     ``workspace_audits``（伴侣视角自动脱敏未公开私人草稿标题）与 ``llm_calls=0``。
2. ``mutate_v02_runtime_migration_view``：
   - 逐动作严格字段白名单校验（未知/空白/伪造身份字段返回 ``400 INVALID_FIELDS``，
     非法运行或装箱模式返回 ``400 INVALID_MODE``，越权动作返回 ``403 FORBIDDEN_ACTOR_SIDE``）；
   - 原生支持 ``operation_key`` 幂等重放。
3. ``summarize_runtime_migration_for_context``：
   - 100% 零磁盘写入辅助摘要，供聊天背景与日常候选桥接读取省电/休眠档位与待复验能力提示。
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

from components.life.scripts import v02_runtime_governance_migration as rm_engine

ENDPOINT_PATH = "/api/phone/v02/runtime-migration"
VIEW_SCHEMA = "nyairo.v02.runtime_migration.v1"

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "set_budget_mode",
    "record_carrier_check",
    "pack_selective_suitcase",
    "coalesce_probe",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "reverify_migrated_skill",
    "record_workspace_audit",
})

SUITCASE_PORTABLE_ACTIONS: frozenset[str] = frozenset({
    "export_suitcase",
    "import_suitcase",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "set_budget_mode": frozenset({
        "action",
        "mode",
        "budget_scale_pct",
        "reason_cn",
        "operation_key",
    }),
    "record_carrier_check": frozenset({
        "action",
        "model_id",
        "carrier_name",
        "preview_available",
        "operation_key",
    }),
    "pack_selective_suitcase": frozenset({
        "action",
        "profile_mode",
        "keep_skill_ids",
        "leave_behind_note",
        "operation_key",
    }),
    "export_suitcase": frozenset({
        "action",
        "profile_mode",
        "keep_practice",
        "keep_skill_ids",
        "leave_behind_note",
        "operation_key",
    }),
    "import_suitcase": frozenset({
        "action",
        "bundle",
        "target_model_id",
        "target_carrier_mode",
        "operation_key",
    }),
    "coalesce_probe": frozenset({
        "action",
        "family",
        "summary_cn",
        "is_control",
        "operation_key",
    }),
    "reverify_migrated_skill": frozenset({
        "action",
        "skill_id",
        "passed",
        "trial_passed",
        "note_cn",
        "operation_key",
    }),
    "record_workspace_audit": frozenset({
        "action",
        "wake_reason",
        "visible_candidate_count",
        "entered_workspace_count",
        "admitted_candidates",
        "trimmed_candidates",
        "trimmed_reasons",
        "private_draft_titles",
        "selection_rule",
        "operation_key",
    }),
}

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "perspective",
    "revision",
    "observed_at",
    "identity",
    "runtime_mode",
    "wake_coalescer",
    "today_capability_card",
    "last_migration_verdict",
    "migration_suitcase",
    "source_freshness",
    "workspace_audits",
    "suitcase_bundle",
    "summary",
    "import_receipt",
    "llm_calls",
)


def _error_response(
    status_code: int,
    reason_code: str,
    detail: str = "",
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code)
    body: dict[str, Any] = {
        "ok": False,
        "status": "rejected" if status_code < 500 else "unavailable",
        "command_status": "REJECTED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "reason_code": code_str,
        "error_code": code_str,
        "detail": str(detail or code_str),
        "data": {},
    }
    if extra:
        body.update(dict(extra))
    return int(status_code), body


def _scan_payload_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise rm_engine.V02ValidationError("INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded field key forbidden: {raw_key!r}",
                )
            lowered = raw_key.lower()
            if (
                raw_key in rm_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
                or lowered in rm_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
            ):
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    f"Forged identity or forbidden field rejected: {raw_key!r}",
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _require_non_empty_trimmed_str(payload: Mapping[str, Any], field: str) -> str:
    val = payload.get(field)
    if not isinstance(val, str) or not val.strip() or val != val.strip():
        raise rm_engine.V02ValidationError(
            "INVALID_FIELDS",
            f"Field {field!r} must be a non-empty trimmed string",
        )
    return val


def _validate_optional_blankable_str(payload: Mapping[str, Any], field: str) -> None:
    if field not in payload:
        return
    val = payload[field]
    if val is not None and not isinstance(val, str):
        raise rm_engine.V02ValidationError(
            "INVALID_FIELDS",
            f"Field {field!r} must be a string or null",
        )


def _validate_optional_bool(payload: Mapping[str, Any], field: str) -> None:
    if field not in payload:
        return
    val = payload[field]
    if not isinstance(val, bool):
        raise rm_engine.V02ValidationError(
            "INVALID_FIELDS",
            f"Field {field!r} must be a boolean",
        )


def _normalize_and_validate_request(payload: Any, *, actor_side: str) -> str:
    if not isinstance(actor_side, str) or not actor_side.strip():
        raise rm_engine.V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Invalid actor_side")
    norm_actor = actor_side.strip().lower()
    if norm_actor == "her":
        norm_actor = "subject"
    if norm_actor not in ("partner", "subject"):
        raise rm_engine.V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Unsupported actor_side: {actor_side!r}",
        )

    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise rm_engine.V02ValidationError("INVALID_FIELDS", "Payload must be a non-empty JSON object")

    for raw_top_key in payload.keys():
        if not isinstance(raw_top_key, str) or not raw_top_key.strip() or raw_top_key != raw_top_key.strip():
            raise rm_engine.V02ValidationError(
                "INVALID_FIELDS",
                f"Blank or whitespace-padded field key forbidden: {raw_top_key!r}",
            )
        if (
            raw_top_key in rm_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
            or raw_top_key.lower() in rm_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
        ):
            raise rm_engine.V02ValidationError(
                "INVALID_FIELDS",
                f"Forged identity or forbidden field rejected: {raw_top_key!r}",
            )

    action = _require_non_empty_trimmed_str(payload, "action")
    if action != "import_suitcase":
        _scan_payload_keys_recursive(payload)
    if action not in ACTION_ALLOWED_FIELDS:
        raise rm_engine.V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    allowed_actions = (
        SUBJECT_ALLOWED_ACTIONS if norm_actor == "subject" else PARTNER_ALLOWED_ACTIONS
    ) | SUITCASE_PORTABLE_ACTIONS
    if action not in allowed_actions:
        raise rm_engine.V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is forbidden for actor_side={norm_actor!r}",
        )

    unknown_fields = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
    if unknown_fields:
        raise rm_engine.V02ValidationError(
            "INVALID_FIELDS",
            f"Unknown fields for {action!r}: {unknown_fields}",
            details={"invalid_fields": unknown_fields},
        )

    if "operation_key" in payload:
        _require_non_empty_trimmed_str(payload, "operation_key")

    if action == "set_budget_mode":
        raw_mode = _require_non_empty_trimmed_str(payload, "mode")
        if raw_mode.upper() not in rm_engine.BUDGET_MODE_ALIASES:
            raise rm_engine.V02ValidationError("INVALID_MODE", f"Invalid budget mode: {raw_mode!r}")
        if "budget_scale_pct" in payload:
            pct = payload["budget_scale_pct"]
            if isinstance(pct, bool) or not isinstance(pct, int) or not (1 <= pct <= 100):
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    "budget_scale_pct must be an integer in 1..100",
                )
        _validate_optional_blankable_str(payload, "reason_cn")

    elif action == "record_carrier_check":
        _require_non_empty_trimmed_str(payload, "model_id")
        _validate_optional_blankable_str(payload, "carrier_name")
        _validate_optional_bool(payload, "preview_available")

    elif action == "pack_selective_suitcase":
        raw_profile = _require_non_empty_trimmed_str(payload, "profile_mode")
        if raw_profile not in rm_engine.SUITCASE_PROFILE_MODES:
            raise rm_engine.V02ValidationError("INVALID_MODE", f"Invalid suitcase profile_mode: {raw_profile!r}")
        if "keep_skill_ids" in payload:
            ks = payload["keep_skill_ids"]
            if not isinstance(ks, list):
                raise rm_engine.V02ValidationError("INVALID_FIELDS", "keep_skill_ids must be a list of strings")
            for item in ks:
                if not isinstance(item, str) or not item.strip() or item != item.strip():
                    raise rm_engine.V02ValidationError(
                        "INVALID_FIELDS",
                        "Each skill_id in keep_skill_ids must be a non-empty trimmed string",
                    )
        _validate_optional_blankable_str(payload, "leave_behind_note")

    elif action == "export_suitcase":
        if "profile_mode" in payload and payload["profile_mode"] is not None:
            raw_profile = _require_non_empty_trimmed_str(payload, "profile_mode")
            if raw_profile not in rm_engine.SUITCASE_PROFILE_MODES:
                raise rm_engine.V02ValidationError("INVALID_MODE", f"Invalid suitcase profile_mode: {raw_profile!r}")
        _validate_optional_bool(payload, "keep_practice")
        if "keep_skill_ids" in payload and payload["keep_skill_ids"] is not None:
            ks = payload["keep_skill_ids"]
            if not isinstance(ks, list):
                raise rm_engine.V02ValidationError("INVALID_FIELDS", "keep_skill_ids must be a list of strings")
            for item in ks:
                if not isinstance(item, str) or not item.strip() or item != item.strip():
                    raise rm_engine.V02ValidationError(
                        "INVALID_FIELDS",
                        "Each skill_id in keep_skill_ids must be a non-empty trimmed string",
                    )
        _validate_optional_blankable_str(payload, "leave_behind_note")

    elif action == "import_suitcase":
        if "bundle" not in payload or not isinstance(payload.get("bundle"), Mapping):
            raise rm_engine.V02ValidationError("INVALID_FIELDS", "bundle must be a JSON object")
        _validate_optional_blankable_str(payload, "target_model_id")
        _validate_optional_blankable_str(payload, "target_carrier_mode")

    elif action == "coalesce_probe":
        _require_non_empty_trimmed_str(payload, "family")
        _require_non_empty_trimmed_str(payload, "summary_cn")
        _validate_optional_bool(payload, "is_control")

    elif action == "reverify_migrated_skill":
        _require_non_empty_trimmed_str(payload, "skill_id")
        _validate_optional_bool(payload, "passed")
        _validate_optional_bool(payload, "trial_passed")
        _validate_optional_blankable_str(payload, "note_cn")

    elif action == "record_workspace_audit":
        _require_non_empty_trimmed_str(payload, "wake_reason")
        for cnt_field in ("visible_candidate_count", "entered_workspace_count"):
            if cnt_field in payload:
                cval = payload[cnt_field]
                if isinstance(cval, bool) or not isinstance(cval, int) or cval < 0:
                    raise rm_engine.V02ValidationError(
                        "INVALID_FIELDS",
                        f"{cnt_field} must be a non-negative integer",
                    )
        for lst_field in ("admitted_candidates", "trimmed_candidates", "trimmed_reasons", "private_draft_titles"):
            if lst_field in payload and not isinstance(payload[lst_field], list):
                raise rm_engine.V02ValidationError("INVALID_FIELDS", f"{lst_field} must be a list")
        _validate_optional_blankable_str(payload, "selection_rule")

    return norm_actor


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
        "data": data_block,
    }
    for key, val in data_block.items():
        if key not in envelope:
            envelope[key] = copy.deepcopy(val)
    return envelope


def read_v02_runtime_migration_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read v0.2 runtime governance & migration view with 100% zero disk writes."""
    try:
        projected = rm_engine.project_v02_runtime_migration(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except rm_engine.V02RuntimeMigrationError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message, extra=exc.details)


def mutate_v02_runtime_migration_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Validate strict field whitelist & actor_side permissions, then mutate runtime-migration state."""
    try:
        norm_actor = _normalize_and_validate_request(payload, actor_side=actor_side)
        projected, cmd_status = rm_engine.apply_runtime_migration_mutation(
            day_root,
            payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)
    except rm_engine.V02RuntimeMigrationError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message, extra=exc.details)


def summarize_runtime_migration_for_context(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-write helper returning budget/dormant conservation & unvalidated skill hints."""
    return rm_engine.build_runtime_migration_context_summary(day_root, now_iso=now_iso)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "SUITCASE_PORTABLE_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v02_runtime_migration_view",
    "read_v02_runtime_migration_view",
    "summarize_runtime_migration_for_context",
]
