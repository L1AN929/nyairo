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
import hashlib
import json
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import s07_affect_owner as _s07_affect
from components.life.scripts import v02_antipuppet_scorecard
from components.life.scripts import v02_autobiography_skill_provenance as asp_engine
from components.life.scripts import v02_crash_and_partial_reconciler as _reconciler
from components.life.scripts import v02_personality_drift_and_model_recheck as drift_engine
from components.life.scripts import v02_representation_lease_forget as _rep_lease
from components.life.scripts import v02_runtime_governance_migration as rm_engine
from components.phone import v02_honest_ux_view as _honest_ux
from components.phone import v02_news_carrier_lineage_view as _world_lineage

ENDPOINT_PATH = "/api/phone/v02/runtime-migration"
VIEW_SCHEMA = "nyairo.v02.runtime_migration.v1"
MIGRATION_LOSS_MANIFEST_SCHEMA = rm_engine.MIGRATION_LOSS_MANIFEST_SCHEMA
SUITCASE_ALLOWED_SLICE_FILES = rm_engine.SUITCASE_ALLOWED_SLICE_FILES
SUITCASE_ALLOWED_WORLD_FILES = rm_engine.SUITCASE_ALLOWED_WORLD_FILES
SUITCASE_ALLOWED_WORLD_PACKAGE_FILES = rm_engine.SUITCASE_ALLOWED_WORLD_PACKAGE_FILES

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

#: 委托给 ``v02_honest_ux_view`` 执行的主体独占动作（本视图不复制其状态机）。
#: 这些动作在进入通用校验器之前就被单独放行：它们的字段与状态属于 honest-ux 视图，
#: 不并入 ``SUBJECT_ALLOWED_ACTIONS`` 以免与本视图自有动作集的口径混淆。
HONEST_UX_DELEGATED_ACTIONS: frozenset[str] = frozenset({
    "explicit_import_representation",
    "approve_pending_forget_hold",
})

SUITCASE_PORTABLE_ACTIONS: frozenset[str] = frozenset({
    "export_suitcase",
    "import_suitcase",
})

VC_SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "reconcile_backup_restore",
})

DEVICE_PRESENCE_ACTIONS: frozenset[str] = frozenset({
    "claim_device_presence_lease",
    "handoff_device_presence_lease",
})

GROWTH_AND_SKILL_PARTNER_ACTIONS: frozenset[str] = frozenset({
    "request_skill_migration_recheck",
    "start_model_preference_recheck",
    "record_preference_recheck_answer",
    "log_partner_taste_observation",
})

GROWTH_AND_SKILL_SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "register_skill_release",
    "record_skill_execution_outcome",
    "record_targeted_method_patch",
    "retire_skill_release",
    "register_standard_revision",
    "capture_personality_drift_snapshot",
})

#: F-137 技能来源吊销（主体独占动作，独立集合以保持既有动作集合契约不变）。
SKILL_PROVENANCE_SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "revoke_skill_source_material",
})

#: 委托给 ``v02_news_carrier_lineage_view`` 执行的 Wave B2 主体独占动作（伴侣端走 ``/world-lineage``）。
WORLD_LINEAGE_DELEGATED_ACTIONS: frozenset[str] = frozenset({
    "record_body_history_entry",
    "record_meaning_version",
    "record_lineage_branch",
    "record_layered_news_item",
    "register_external_fact_source",
    "sync_carrier_profile_and_adapt_habits",
})

#: 表征租约门控动作（独立集合以保持既有 PARTNER/SUBJECT_ALLOWED_ACTIONS 冻结契约不变）。
REPRESENTATION_LEASE_PARTNER_ACTIONS: frozenset[str] = frozenset({
    "revoke_representation_lease",
})

REPRESENTATION_LEASE_SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "issue_representation_lease",
    "revoke_representation_lease",
})

#: F-148 两步体面只读封存归档动作集合（独立集合以保持既有动作集合冻结契约不变）。
DIGNIFIED_RETIREMENT_ACTIONS: frozenset[str] = frozenset({
    "prepare_dignified_retirement",
    "cancel_dignified_retirement",
    "confirm_dignified_retirement",
})

#: F-113 主动联络门控动作集合（伴侣端配置与回执 / 主体端受控入队）。
PROACTIVE_OUTBOUND_PARTNER_ACTIONS: frozenset[str] = frozenset({
    "configure_proactive_outbound",
    "acknowledge_or_cancel_proactive_message",
})

PROACTIVE_OUTBOUND_SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "enqueue_proactive_message",
})

#: F-076 阶段自我声明 & F-011/012 外部编辑观察与规范投影重建（主体独占动作）。
CLOSURE_SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "record_self_claim",
    "observe_external_edit",
    "rebuild_projection_from_canonical",
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
        "method_id",
        "tool_version",
        "model_id",
        "scope",
        "bare_model_score",
        "method_with_new_model_score",
        "old_baseline_score",
        "held_out_trials",
        "pass_threshold",
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
    "reconcile_backup_restore": frozenset({
        "action",
        "external_watermark_seal",
        "operation_key",
    }),
    "claim_device_presence_lease": frozenset({
        "action",
        "device_id",
        "continuation_ref",
        "lease_seconds",
        "expected_epoch",
        "operation_key",
    }),
    "handoff_device_presence_lease": frozenset({
        "action",
        "from_device_id",
        "to_device_id",
        "lease_epoch",
        "continuation_ref",
        "lease_seconds",
        "operation_key",
    }),
    "explicit_import_representation": frozenset({
        "action",
        "artifact_id",
        "observed_head_hash",
        "expected_observed_sha256",
        "observed_version",
        "note_cn",
        "base_projection_version",
        "workspace_root",
        "operation_key",
    }),
    "approve_pending_forget_hold": frozenset({
        "action",
        "case_id",
        "hold_id",
        "scope_choice",
        "window_minutes",
        "resolution",
        "unreachable_copies",
        "operation_key",
    }),
    "request_skill_migration_recheck": frozenset({
        "action",
        "method_id",
        "scope_key",
        "note_cn",
        "request_id",
        "operation_key",
    }),
    "start_model_preference_recheck": frozenset({
        "action",
        "from_model_id",
        "to_model_id",
        "note_cn",
        "recheck_id",
        "operation_key",
    }),
    "record_preference_recheck_answer": frozenset({
        "action",
        "recheck_id",
        "item_id",
        "post_switch_answer_cn",
        "alignment",
        "note_cn",
        "finalize",
        "operation_key",
    }),
    "log_partner_taste_observation": frozenset({
        "action",
        "domain",
        "observation_cn",
        "new_stance_cn",
        "previous_stance_cn",
        "evidence_ref",
        "shared",
        "operation_key",
    }),
    "register_skill_release": frozenset({
        "action",
        "method_id",
        "tool_version",
        "model_id",
        "scope",
        "domain",
        "title_cn",
        "attribution",
        "criteria_id",
        "standard_revision",
        "status",
        "held_out",
        "success_rate",
        "trials",
        "successes",
        "ablation_gain_vs_bare_model",
        "source_dependencies",
        "operation_key",
    }),
    "record_skill_execution_outcome": frozenset({
        "action",
        "method_id",
        "tool_version",
        "model_id",
        "scope",
        "outcome",
        "error_kind",
        "is_transient_network",
        "safety_violation",
        "bug_signature",
        "retry_group_id",
        "recovery_verified",
        "held_out_recovery_passed",
        "operation_key",
    }),
    "record_targeted_method_patch": frozenset({
        "action",
        "patch_id",
        "method_id",
        "scope",
        "failure_symptom_cn",
        "root_cause_hypothesis",
        "method_change_cn",
        "retest_status",
        "shared",
        "operation_key",
    }),
    "retire_skill_release": frozenset({
        "action",
        "scope_key",
        "method_id",
        "tool_version",
        "model_id",
        "scope",
        "retired_reason",
        "retirement_note_cn",
        "operation_key",
    }),
    "register_standard_revision": frozenset({
        "action",
        "criteria_id",
        "standard_revision",
        "domain",
        "pass_threshold",
        "rubric_summary",
        "change_reason",
        "overlap_comparison_note",
        "axis",
        "previous_stance_cn",
        "new_stance_cn",
        "evidence_ref",
        "shared",
        "operation_key",
    }),
    "capture_personality_drift_snapshot": frozenset({
        "action",
        "note_cn",
        "operation_key",
    }),
    "revoke_skill_source_material": frozenset({
        "action",
        "source_id",
        "source_ref",
        "reason_cn",
        "operation_key",
    }),
    "issue_representation_lease": frozenset({
        "action",
        "scope",
        "lease_id",
        "ttl_minutes",
        "reason_cn",
        "operation_key",
    }),
    "revoke_representation_lease": frozenset({
        "action",
        "scope",
        "lease_id",
        "reason_cn",
        "operation_key",
    }),
    "prepare_dignified_retirement": frozenset({
        "action",
        "reason_cn",
        "farewell_note_cn",
        "operation_key",
    }),
    "cancel_dignified_retirement": frozenset({
        "action",
        "operation_key",
    }),
    "confirm_dignified_retirement": frozenset({
        "action",
        "confirmation_phrase",
        "farewell_note_cn",
        "operation_key",
    }),
    "configure_proactive_outbound": frozenset({
        "action",
        "enabled",
        "quiet_start_hm",
        "quiet_end_hm",
        "daily_cap",
        "min_cooldown_minutes",
        "operation_key",
    }),
    "enqueue_proactive_message": frozenset({
        "action",
        "message_cn",
        "reason_cn",
        "channel_hint",
        "operation_key",
    }),
    "acknowledge_or_cancel_proactive_message": frozenset({
        "action",
        "message_id",
        "decision",
        "operation_key",
    }),
    "record_self_claim": frozenset({
        "action",
        "period_id",
        "claim_key",
        "domain",
        "statement",
        "statement_cn",
        "lane",
        "polarity",
        "stance",
        "visibility",
        "shared",
        "evidence_refs",
        "claim_id",
        "importance_score",
        "operation_key",
    }),
    "observe_external_edit": frozenset({
        "action",
        "artifact_id",
        "workspace_root",
        "workspace_rel_path",
        "actor_ref",
        "trusted_actor_identity",
        "operation_key",
    }),
    "rebuild_projection_from_canonical": frozenset({
        "action",
        "artifact_id",
        "workspace_root",
        "workspace_rel_path",
        "operation_key",
    }),
}

for _wl_action in WORLD_LINEAGE_DELEGATED_ACTIONS:
    if _wl_action in _world_lineage.ACTION_ALLOWED_FIELDS:
        ACTION_ALLOWED_FIELDS[_wl_action] = _world_lineage.ACTION_ALLOWED_FIELDS[_wl_action]

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
    "migration_loss_manifest",
    "source_freshness",
    "workspace_audits",
    "annual_decay_matrix_22",
    "antipuppet_scorecard",
    "vc_authority_status",
    "device_presence",
    "retirement_status",
    "proactive_outbound",
    "chatless_life_review",
    "personal_skill_book",
    "personality_drift_and_recheck",
    "canonical_ledger_status",
    "s07_affect_summary",
    "representation_lease_health",
    "observability_status",
    "budget_quota_status",
    "budget_survival_status",
    "action_receipt",
    "suitcase_bundle",
    "summary",
    "import_receipt",
    "vc_reconcile_receipt",
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

    if action == "log_partner_taste_observation" and norm_actor != "partner":
        raise rm_engine.V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            "Subject cannot forge partner taste observations; use register_standard_revision",
        )

    allowed_actions = (
        (
            SUBJECT_ALLOWED_ACTIONS
            | VC_SUBJECT_ONLY_ACTIONS
            | GROWTH_AND_SKILL_SUBJECT_ACTIONS
            | SKILL_PROVENANCE_SUBJECT_ACTIONS
            | REPRESENTATION_LEASE_SUBJECT_ACTIONS
            | PROACTIVE_OUTBOUND_SUBJECT_ACTIONS
            | CLOSURE_SUBJECT_ONLY_ACTIONS
        )
        if norm_actor == "subject"
        else (
            PARTNER_ALLOWED_ACTIONS
            | REPRESENTATION_LEASE_PARTNER_ACTIONS
            | PROACTIVE_OUTBOUND_PARTNER_ACTIONS
        )
    ) | SUITCASE_PORTABLE_ACTIONS | DEVICE_PRESENCE_ACTIONS | GROWTH_AND_SKILL_PARTNER_ACTIONS | DIGNIFIED_RETIREMENT_ACTIONS
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
        if "method_id" in payload:
            _require_non_empty_trimmed_str(payload, "method_id")
            _require_non_empty_trimmed_str(payload, "tool_version")
            _require_non_empty_trimmed_str(payload, "model_id")
            _require_non_empty_trimmed_str(payload, "scope")
            for num_field in ("bare_model_score", "method_with_new_model_score", "old_baseline_score"):
                nval = payload.get(num_field)
                if isinstance(nval, bool) or not isinstance(nval, (int, float)):
                    raise rm_engine.V02ValidationError("INVALID_FIELDS", f"{num_field} must be numeric")
            trials_val = payload.get("held_out_trials")
            if isinstance(trials_val, bool) or not isinstance(trials_val, int) or trials_val < 1:
                raise rm_engine.V02ValidationError("INVALID_FIELDS", "held_out_trials must be a positive integer")
            if "pass_threshold" in payload and payload["pass_threshold"] is not None:
                pt = payload["pass_threshold"]
                if isinstance(pt, bool) or not isinstance(pt, (int, float)):
                    raise rm_engine.V02ValidationError("INVALID_FIELDS", "pass_threshold must be numeric")
        else:
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

    elif action == "reconcile_backup_restore":
        if "external_watermark_seal" in payload and payload["external_watermark_seal"] is not None:
            if not isinstance(payload["external_watermark_seal"], Mapping):
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    "external_watermark_seal must be a JSON object",
                )

    elif action == "claim_device_presence_lease":
        _require_non_empty_trimmed_str(payload, "device_id")
        _validate_optional_blankable_str(payload, "continuation_ref")
        if "lease_seconds" in payload and payload["lease_seconds"] is not None:
            ls = payload["lease_seconds"]
            if isinstance(ls, bool) or not isinstance(ls, int) or not (1 <= ls <= rm_engine.MAX_DEVICE_LEASE_SECONDS):
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    f"lease_seconds must be an integer in 1..{rm_engine.MAX_DEVICE_LEASE_SECONDS}",
                )
        if "expected_epoch" in payload and payload["expected_epoch"] is not None:
            ee = payload["expected_epoch"]
            if isinstance(ee, bool) or not isinstance(ee, int) or ee < 0:
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    "expected_epoch must be a non-negative integer",
                )

    elif action == "handoff_device_presence_lease":
        _require_non_empty_trimmed_str(payload, "from_device_id")
        _require_non_empty_trimmed_str(payload, "to_device_id")
        if "lease_epoch" not in payload or payload["lease_epoch"] is None:
            raise rm_engine.V02ValidationError("INVALID_FIELDS", "lease_epoch is required")
        le = payload["lease_epoch"]
        if isinstance(le, bool) or not isinstance(le, int) or le < 0:
            raise rm_engine.V02ValidationError(
                "INVALID_FIELDS",
                "lease_epoch must be a non-negative integer",
            )
        _validate_optional_blankable_str(payload, "continuation_ref")
        if "lease_seconds" in payload and payload["lease_seconds"] is not None:
            ls = payload["lease_seconds"]
            if isinstance(ls, bool) or not isinstance(ls, int) or not (1 <= ls <= rm_engine.MAX_DEVICE_LEASE_SECONDS):
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS",
                    f"lease_seconds must be an integer in 1..{rm_engine.MAX_DEVICE_LEASE_SECONDS}",
                )

    elif action == "request_skill_migration_recheck":
        _require_non_empty_trimmed_str(payload, "method_id")
        _validate_optional_blankable_str(payload, "scope_key")
        _validate_optional_blankable_str(payload, "note_cn")
        _validate_optional_blankable_str(payload, "request_id")

    elif action == "start_model_preference_recheck":
        _validate_optional_blankable_str(payload, "from_model_id")
        _validate_optional_blankable_str(payload, "to_model_id")
        _validate_optional_blankable_str(payload, "note_cn")
        _validate_optional_blankable_str(payload, "recheck_id")

    elif action == "record_preference_recheck_answer":
        _require_non_empty_trimmed_str(payload, "recheck_id")
        _require_non_empty_trimmed_str(payload, "item_id")
        _require_non_empty_trimmed_str(payload, "post_switch_answer_cn")
        align = _require_non_empty_trimmed_str(payload, "alignment")
        if align not in drift_engine.RECHECK_ALIGNMENTS:
            raise rm_engine.V02ValidationError(
                "INVALID_FIELDS",
                f"alignment must be one of {sorted(drift_engine.RECHECK_ALIGNMENTS)}",
            )
        _validate_optional_blankable_str(payload, "note_cn")
        _validate_optional_bool(payload, "finalize")

    elif action == "log_partner_taste_observation":
        _require_non_empty_trimmed_str(payload, "domain")
        if "observation_cn" in payload:
            _require_non_empty_trimmed_str(payload, "observation_cn")
        elif "new_stance_cn" in payload:
            _require_non_empty_trimmed_str(payload, "new_stance_cn")
        else:
            raise rm_engine.V02ValidationError("INVALID_FIELDS", "observation_cn is required")
        _validate_optional_blankable_str(payload, "previous_stance_cn")
        _validate_optional_blankable_str(payload, "evidence_ref")
        _validate_optional_bool(payload, "shared")

    elif action == "register_skill_release":
        for req_f in ("method_id", "tool_version", "model_id", "scope", "attribution", "criteria_id", "standard_revision"):
            _require_non_empty_trimmed_str(payload, req_f)
        _validate_optional_blankable_str(payload, "domain")
        _validate_optional_blankable_str(payload, "title_cn")
        _validate_optional_blankable_str(payload, "status")
        _validate_optional_bool(payload, "held_out")
        for num_f in ("success_rate", "ablation_gain_vs_bare_model"):
            if num_f in payload and payload[num_f] is not None:
                v = payload[num_f]
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise rm_engine.V02ValidationError("INVALID_FIELDS", f"{num_f} must be numeric")
        for int_f in ("trials", "successes"):
            if int_f in payload and payload[int_f] is not None:
                v = payload[int_f]
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise rm_engine.V02ValidationError("INVALID_FIELDS", f"{int_f} must be a non-negative integer")
        if "source_dependencies" in payload and payload["source_dependencies"] is not None:
            if not isinstance(payload["source_dependencies"], list):
                raise rm_engine.V02ValidationError("INVALID_FIELDS", "source_dependencies must be a list")

    elif action == "record_skill_execution_outcome":
        for req_f in ("method_id", "tool_version", "model_id", "scope", "outcome"):
            _require_non_empty_trimmed_str(payload, req_f)
        for opt_s in ("error_kind", "bug_signature", "retry_group_id"):
            _validate_optional_blankable_str(payload, opt_s)
        for opt_b in ("is_transient_network", "safety_violation", "recovery_verified", "held_out_recovery_passed"):
            _validate_optional_bool(payload, opt_b)

    elif action == "record_targeted_method_patch":
        _validate_optional_blankable_str(payload, "patch_id")
        _validate_optional_blankable_str(payload, "retest_status")
        _validate_optional_bool(payload, "shared")
        is_verified_update = (
            isinstance(payload.get("patch_id"), str)
            and bool(payload["patch_id"].strip())
            and payload.get("retest_status") == "VERIFIED"
        )
        if is_verified_update:
            for opt_s in ("method_id", "scope", "failure_symptom_cn", "root_cause_hypothesis", "method_change_cn"):
                _validate_optional_blankable_str(payload, opt_s)
        else:
            for req_f in ("method_id", "scope", "failure_symptom_cn", "root_cause_hypothesis", "method_change_cn"):
                _require_non_empty_trimmed_str(payload, req_f)

    elif action == "retire_skill_release":
        has_sk = isinstance(payload.get("scope_key"), str) and bool(payload["scope_key"].strip())
        has_mid = isinstance(payload.get("method_id"), str) and bool(payload["method_id"].strip())
        if not has_sk and not has_mid:
            raise rm_engine.V02ValidationError("INVALID_FIELDS", "scope_key or method_id is required")
        if "scope_key" in payload and payload["scope_key"] is not None:
            _require_non_empty_trimmed_str(payload, "scope_key")
        if "method_id" in payload and payload["method_id"] is not None:
            _require_non_empty_trimmed_str(payload, "method_id")
        for opt_s in ("tool_version", "model_id", "scope", "retired_reason", "retirement_note_cn"):
            _validate_optional_blankable_str(payload, opt_s)

    elif action == "register_standard_revision":
        _require_non_empty_trimmed_str(payload, "domain")
        has_eval_fields = ("criteria_id" in payload) or ("rubric_summary" in payload)
        if has_eval_fields:
            _require_non_empty_trimmed_str(payload, "criteria_id")
            _require_non_empty_trimmed_str(payload, "standard_revision")
            _require_non_empty_trimmed_str(payload, "rubric_summary")
            if "pass_threshold" in payload and payload["pass_threshold"] is not None:
                pt = payload["pass_threshold"]
                if isinstance(pt, bool) or not isinstance(pt, (int, float)):
                    raise rm_engine.V02ValidationError("INVALID_FIELDS", "pass_threshold must be numeric")
            _validate_optional_blankable_str(payload, "change_reason")
            _validate_optional_blankable_str(payload, "overlap_comparison_note")
        else:
            _require_non_empty_trimmed_str(payload, "new_stance_cn")
        _validate_optional_blankable_str(payload, "axis")
        _validate_optional_blankable_str(payload, "previous_stance_cn")
        _validate_optional_blankable_str(payload, "evidence_ref")
        _validate_optional_bool(payload, "shared")

    elif action == "capture_personality_drift_snapshot":
        _validate_optional_blankable_str(payload, "note_cn")

    elif action == "revoke_skill_source_material":
        has_ref = isinstance(payload.get("source_ref"), str) and bool(payload["source_ref"].strip())
        has_id = isinstance(payload.get("source_id"), str) and bool(payload["source_id"].strip())
        if not has_ref and not has_id:
            raise rm_engine.V02ValidationError("INVALID_FIELDS", "source_ref or source_id is required")
        if "source_ref" in payload and payload["source_ref"] is not None:
            _require_non_empty_trimmed_str(payload, "source_ref")
        if "source_id" in payload and payload["source_id"] is not None:
            _require_non_empty_trimmed_str(payload, "source_id")
        _validate_optional_blankable_str(payload, "reason_cn")

    elif action == "prepare_dignified_retirement":
        _validate_optional_blankable_str(payload, "reason_cn")
        _validate_optional_blankable_str(payload, "farewell_note_cn")

    elif action == "cancel_dignified_retirement":
        pass

    elif action == "confirm_dignified_retirement":
        _validate_optional_blankable_str(payload, "confirmation_phrase")
        _validate_optional_blankable_str(payload, "farewell_note_cn")

    elif action == "configure_proactive_outbound":
        _validate_optional_bool(payload, "enabled")
        if "quiet_start_hm" in payload and payload["quiet_start_hm"] is not None:
            _require_non_empty_trimmed_str(payload, "quiet_start_hm")
        if "quiet_end_hm" in payload and payload["quiet_end_hm"] is not None:
            _require_non_empty_trimmed_str(payload, "quiet_end_hm")
        if "daily_cap" in payload and payload["daily_cap"] is not None:
            dc = payload["daily_cap"]
            if isinstance(dc, bool) or not isinstance(dc, int) or not (1 <= dc <= 6):
                raise rm_engine.V02ValidationError("INVALID_FIELDS", "daily_cap must be an integer in 1..6")
        if "min_cooldown_minutes" in payload and payload["min_cooldown_minutes"] is not None:
            mc = payload["min_cooldown_minutes"]
            if isinstance(mc, bool) or not isinstance(mc, int) or not (30 <= mc <= 1440):
                raise rm_engine.V02ValidationError(
                    "INVALID_FIELDS", "min_cooldown_minutes must be an integer in 30..1440"
                )

    elif action == "enqueue_proactive_message":
        _require_non_empty_trimmed_str(payload, "message_cn")
        _validate_optional_blankable_str(payload, "reason_cn")
        _validate_optional_blankable_str(payload, "channel_hint")

    elif action == "acknowledge_or_cancel_proactive_message":
        _require_non_empty_trimmed_str(payload, "message_id")
        dec = _require_non_empty_trimmed_str(payload, "decision").lower()
        if dec not in ("acknowledge", "cancel"):
            raise rm_engine.V02ValidationError("INVALID_FIELDS", "decision must be 'acknowledge' or 'cancel'")

    elif action == "record_self_claim":
        _require_non_empty_trimmed_str(payload, "domain")
        has_stmt = isinstance(payload.get("statement"), str) and bool(payload["statement"].strip())
        has_stmt_cn = isinstance(payload.get("statement_cn"), str) and bool(payload["statement_cn"].strip())
        if not has_stmt and not has_stmt_cn:
            raise rm_engine.V02ValidationError("INVALID_FIELDS", "statement or statement_cn is required")
        for opt_s in ("period_id", "claim_key", "lane", "polarity", "stance", "visibility", "claim_id"):
            _validate_optional_blankable_str(payload, opt_s)
        _validate_optional_bool(payload, "shared")
        if "evidence_refs" in payload and payload["evidence_refs"] is not None:
            if not isinstance(payload["evidence_refs"], list):
                raise rm_engine.V02ValidationError("INVALID_FIELDS", "evidence_refs must be a list")
        if "importance_score" in payload and payload["importance_score"] is not None:
            imp = payload["importance_score"]
            if isinstance(imp, bool) or not isinstance(imp, (int, float)):
                raise rm_engine.V02ValidationError("INVALID_FIELDS", "importance_score must be numeric")

    elif action in ("observe_external_edit", "rebuild_projection_from_canonical"):
        _require_non_empty_trimmed_str(payload, "artifact_id")
        _validate_optional_blankable_str(payload, "workspace_root")
        _validate_optional_blankable_str(payload, "workspace_rel_path")
        if action == "observe_external_edit":
            _validate_optional_blankable_str(payload, "actor_ref")
            _validate_optional_bool(payload, "trusted_actor_identity")

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


def _read_json_zero_write(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, Mapping) else None
    except (OSError, ValueError, TypeError):
        return None


def _build_sixth_dim_and_receipt_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Compute sixth-dim antipuppet check & plain-Chinese receipt with 0 disk writes."""
    root = Path(day_root)
    try:
        visible_objs = _honest_ux._read_matter_space_objects_zero_write(
            root, viewer="partner", now_iso=now_iso
        )
    except Exception:
        visible_objs = []
    item_count = len(visible_objs)

    neigh_doc = _read_json_zero_write(root / "world" / "v04_neighborhood_state.json") or {}
    clock_doc = _read_json_zero_write(root / "phone" / "v03_clock_state.json") or {}
    traces_doc = _read_json_zero_write(root / "phone" / "v08_living_traces.json") or {}
    fam_doc = _read_json_zero_write(root / "phone" / "familiarity.json") or {}

    acquired_spot_items: list[Any] = []
    raw_spot_items = neigh_doc.get("acquired_spot_items") or neigh_doc.get("spot_items") or []
    if isinstance(raw_spot_items, list):
        acquired_spot_items = list(raw_spot_items)

    visited_places: set[str] = {"home"}
    for vp_key in ("visited_places", "places_visited"):
        vp_raw = neigh_doc.get(vp_key)
        if isinstance(vp_raw, list):
            for p in vp_raw:
                if isinstance(p, str) and p.strip():
                    visited_places.add(p.strip())
                elif isinstance(p, Mapping) and str(p.get("place_id") or "").strip():
                    visited_places.add(str(p.get("place_id")).strip())

    public_texts: list[str] = []
    for pc in neigh_doc.get("postcards") or neigh_doc.get("public_board") or ():
        if isinstance(pc, str) and pc.strip():
            public_texts.append(pc)
        elif isinstance(pc, Mapping):
            for tf in ("text", "text_cn", "summary_cn", "title_cn", "content"):
                if isinstance(pc.get(tf), str) and pc[tf].strip():
                    public_texts.append(pc[tf])
    for tr in traces_doc.get("traces") or traces_doc.get("entries") or ():
        if isinstance(tr, Mapping) and str(tr.get("visibility") or "shared").lower() == "shared":
            for tf in ("gesture_cn", "state_sticker_cn", "note_cn", "label_cn"):
                if isinstance(tr.get(tf), str) and tr[tf].strip():
                    public_texts.append(tr[tf])

    days_observed = 0
    observed_quiet_days = 0
    raw_days = clock_doc.get("observed_days") or clock_doc.get("day_keys") or clock_doc.get("history_days")
    if isinstance(raw_days, list):
        days_observed = len(raw_days)
    elif isinstance(clock_doc.get("days_observed"), int) and not isinstance(clock_doc.get("days_observed"), bool):
        days_observed = max(0, int(clock_doc["days_observed"]))
    if isinstance(clock_doc.get("rolling_7d_quiet_days"), int) and not isinstance(clock_doc.get("rolling_7d_quiet_days"), bool):
        observed_quiet_days = max(0, int(clock_doc["rolling_7d_quiet_days"]))
        if days_observed == 0:
            days_observed = 7
    elif isinstance(clock_doc.get("quiet_days"), int) and not isinstance(clock_doc.get("quiet_days"), bool):
        observed_quiet_days = max(0, int(clock_doc["quiet_days"]))

    eval_quiet_days = observed_quiet_days if days_observed >= 7 else 3
    raw_gap = clock_doc.get("min_rare_event_gap_days")
    min_gap = (
        int(raw_gap)
        if isinstance(raw_gap, int) and not isinstance(raw_gap, bool)
        else None
    )

    sixth_dim = v02_antipuppet_scorecard.evaluate_v05_antipuppet_sixth_dim(
        acquired_spot_items=acquired_spot_items,
        visited_places=sorted(visited_places),
        public_postcards_and_board=public_texts,
        rolling_7d_quiet_days=eval_quiet_days,
        min_rare_event_gap_days=min_gap,
        familiarity_ledger=fam_doc if fam_doc else dict(neigh_doc.get("familiarity") or {}),
    )
    if sixth_dim.get("risk") != v02_antipuppet_scorecard.RISK_LOW:
        receipt_str = v02_antipuppet_scorecard.format_plain_chinese_scorecard_receipt(
            sixth_dim, item_count=item_count, quiet_days=observed_quiet_days
        )
    elif days_observed < 7:
        receipt_str = (
            f"刚核对完本地记录：屋里可见 {item_count} 件物品都有真实来处，"
            "未检出越界字段或路径外泄（运行未满 7 天，清静日基线持续累计中）。"
        )
    else:
        receipt_str = v02_antipuppet_scorecard.format_plain_chinese_scorecard_receipt(
            sixth_dim, item_count=item_count, quiet_days=observed_quiet_days
        )
    return sixth_dim, receipt_str


def _read_budget_quota_status_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Strictly zero-write inspection of life_request_budget.json + F-168 4-slot telemetry."""
    root = Path(day_root)
    now_dt, _ = rm_engine._resolve_now(now_iso)
    local_dt = now_dt.astimezone(rm_engine.DEFAULT_TZ)
    local_hour = int(local_dt.hour)
    local_day = local_dt.date().isoformat()

    try:
        from components.life.scripts import life_request_budget as _lrb
    except ImportError:
        import life_request_budget as _lrb  # type: ignore[no-redef]

    def _slot_telemetry(doc_map: Optional[Mapping[str, Any]]) -> dict[str, Any]:
        day_bucket: Mapping[str, Any] = {}
        if isinstance(doc_map, Mapping):
            four_days = doc_map.get("v03_four_slot_days")
            if isinstance(four_days, Mapping):
                bkey = str(doc_map.get("active_day") or doc_map.get("day_key") or local_day)
                cand_bucket = four_days.get(bkey) or four_days.get(local_day)
                if isinstance(cand_bucket, Mapping):
                    day_bucket = cand_bucket
        eff = _lrb.v03_four_slot_effective(day_bucket, local_hour)
        active_slot = str(eff["current_slot"])
        active_info = eff["slots"][active_slot]
        return {
            "slot_mode": "four_slot_v03",
            "active_slot": active_slot,
            "slot_cap": int(active_info["calls"]["effective"]),
            "slot_base_cap": int(_lrb.V03_FOUR_SLOT_QUOTAS[active_slot]["calls"]),
            "slot_token_cap": int(active_info["tokens"]["effective"]),
            "slot_used": int(active_info["calls"]["used"]),
            "slot_remaining": int(active_info["calls"]["remaining"]),
            "carry_ratio": float(_lrb.V03_SLOT_CARRY_RATIO),
            "four_slot_schedule": copy.deepcopy(_lrb.V03_FOUR_SLOT_QUOTAS),
        }

    for rel in ("state/life_request_budget.json", "one-day/life_request_budget.json", "life_request_budget.json"):
        cand = root / rel
        if not cand.is_file() or cand.is_symlink():
            continue
        try:
            doc = json.loads(cand.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(doc, Mapping):
            day_key = doc.get("day_key") or doc.get("business_day") or doc.get("active_day")
            if any(k in doc for k in ("used", "requests_used", "cognition_calls")):
                used = int(doc.get("used") or doc.get("requests_used") or doc.get("cognition_calls") or 0)
            elif isinstance(doc.get("days"), Mapping) and day_key:
                used = int((doc["days"]).get(str(day_key)) or 0)
            else:
                used = 0
            raw_cap = doc.get("daily_cap") or doc.get("limit") or doc.get("max_requests") or doc.get("utc_day_max")
            cap = int(raw_cap) if (isinstance(raw_cap, int) and not isinstance(raw_cap, bool) and raw_cap > 0) else 0
            remaining = max(0, cap - used) if cap > 0 else None
            out = {
                "present": True,
                "source_file": rel,
                "day_key": day_key,
                "used": used,
                "daily_cap": cap if cap > 0 else None,
                "remaining": remaining,
                "exhausted": bool(cap > 0 and used >= cap),
                "llm_calls": 0,
            }
            out.update(_slot_telemetry(doc))
            return out
    out_empty = {
        "present": False,
        "source_file": None,
        "day_key": None,
        "used": 0,
        "daily_cap": None,
        "remaining": None,
        "exhausted": False,
        "llm_calls": 0,
    }
    out_empty.update(_slot_telemetry(None))
    return out_empty


def _read_observability_status_zero_write(
    day_root: Path | str,
    projected: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Strictly zero-write F-171 observability classification (stalled NEVER disguised as quiet)."""
    rm_health = str((projected.get("runtime_mode") or {}).get("health_state") or "")
    stalled_flag = rm_health == rm_engine.HEALTH_STALLED_OR_DEGRADED
    try:
        from components.life.scripts import v02_v03_daily_loop_bridge as _loop_bridge

        obs = _loop_bridge.classify_day_observability(day_root, now_iso=now_iso)
    except Exception:
        obs = {
            "observability_class": "QUIET_EVENTLESS_DAY",
            "has_pending_unknown": False,
            "pending_unknown_refs": [],
            "is_intentional_rest": False,
            "is_quiet_unblocked": True,
            "observed_at": now_iso,
        }
    return {
        "observability_class": str(obs.get("observability_class") or "QUIET_EVENTLESS_DAY"),
        "has_pending_unknown": bool(obs.get("has_pending_unknown", False)),
        "pending_unknown_refs": list(obs.get("pending_unknown_refs") or []),
        "is_intentional_rest": bool(obs.get("is_intentional_rest", False)) and not stalled_flag,
        "is_quiet_unblocked": bool(obs.get("is_quiet_unblocked", False)) and not stalled_flag,
        "stalled_flag": stalled_flag,
        "runtime_health_state": rm_health,
        "observed_at": obs.get("observed_at") or now_iso,
        "llm_calls": 0,
    }


def _read_budget_survival_status_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Strictly zero-write F-173 budget survival tier & UNKNOWN reservation summary."""
    try:
        surv = asp_engine.project_budget_survival(day_root, now_iso=now_iso)
    except Exception:
        return {
            "present": False,
            "tier": "NORMAL",
            "mode": asp_engine.BUDGET_MODE_NORMAL,
            "budget_scale_pct": 100,
            "active_unknown_reserved_cost": 0.0,
            "active_unknown_reserved_tokens": 0,
            "active_unknown_reservation_ids": [],
            "carried_across_midnight_ids": [],
            "excuse_fabricated": False,
            "llm_calls": 0,
        }
    if not bool(surv.get("state_exists", False)):
        return {
            "present": False,
            "tier": "NORMAL",
            "mode": asp_engine.BUDGET_MODE_NORMAL,
            "budget_scale_pct": 100,
            "active_unknown_reserved_cost": 0.0,
            "active_unknown_reserved_tokens": 0,
            "active_unknown_reservation_ids": [],
            "carried_across_midnight_ids": [],
            "excuse_fabricated": False,
            "llm_calls": 0,
        }
    scale_pct = int(surv.get("budget_scale_pct") or 100)
    mode_val = str(surv.get("mode") or asp_engine.BUDGET_MODE_NORMAL)
    if mode_val == asp_engine.BUDGET_MODE_CONSERVATION or scale_pct <= 25:
        tier = "LOW_BUDGET_CONSERVATION"
    elif scale_pct < 100:
        tier = "REDUCED"
    else:
        tier = "NORMAL"
    return {
        "present": True,
        "tier": tier,
        "mode": mode_val,
        "budget_scale_pct": scale_pct,
        "status_reason": str(surv.get("status_reason") or "NORMAL_OPERATION"),
        "available_cost_budget": surv.get("available_cost_budget"),
        "available_token_budget": surv.get("available_token_budget"),
        "active_unknown_reserved_cost": float(surv.get("active_unknown_reserved_cost") or 0.0),
        "active_unknown_reserved_tokens": int(surv.get("active_unknown_reserved_tokens") or 0),
        "active_unknown_reservation_ids": list(surv.get("active_unknown_reservation_ids") or []),
        "carried_across_midnight_ids": list(surv.get("carried_across_midnight_ids") or []),
        "allow_exploratory_cognition": bool(surv.get("allow_exploratory_cognition", True)),
        "allow_essential_reconciliation": bool(surv.get("allow_essential_reconciliation", True)),
        "excuse_fabricated": bool(surv.get("excuse_fabricated", False)),
        "llm_calls": 0,
    }


def _attach_growth_and_skill_projections_zero_write(
    day_root: Path | str,
    projected: dict[str, Any],
    *,
    has_subject_capability: bool,
) -> None:
    obs_iso = projected.get("observed_at")
    projected["personal_skill_book"] = asp_engine.project_personal_skill_book_zero_write(
        day_root,
        has_subject_capability=bool(has_subject_capability),
        now_iso=obs_iso,
    )
    projected["personality_drift_and_recheck"] = drift_engine.read_personality_drift_and_recheck_view(
        day_root,
        has_subject_capability=bool(has_subject_capability),
        now_iso=obs_iso,
    )
    projected["canonical_ledger_status"] = _reconciler.read_canonical_ledger_summary_zero_write(day_root)
    projected["s07_affect_summary"] = _s07_affect.read_affect_summary_zero_write(day_root, now_iso=obs_iso)
    projected["representation_lease_health"] = _rep_lease.read_lease_health_zero_write(day_root, now_iso=obs_iso)
    projected["observability_status"] = _read_observability_status_zero_write(
        day_root, projected, now_iso=obs_iso
    )
    projected["budget_quota_status"] = _read_budget_quota_status_zero_write(day_root, now_iso=obs_iso)
    projected["budget_survival_status"] = _read_budget_survival_status_zero_write(
        day_root, now_iso=obs_iso
    )


def _enforce_skill_book_lease_or_raise(day_root: Path | str, *, now_iso: Optional[str]) -> None:
    gate = _rep_lease.enforce_active_representation_lease(day_root, scope="skill_book", now_iso=now_iso)
    if not gate.get("allowed", True):
        raise rm_engine.V02PermissionError(
            str(gate.get("reason") or "REPRESENTATION_LEASE_REVOKED"),
            str(gate.get("reason_cn") or "表征权限租约已失效或吊销，本次操作已拦截"),
        )


def _validate_safe_representation_paths_or_raise(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    artifact_id: str,
) -> Path:
    """Strictly reject '..' traversal or symlink/junction paths and return safe workspace_root."""
    raw_ws = payload.get("workspace_root")
    raw_rel = payload.get("workspace_rel_path")

    def _assert_no_traversal_or_symlink(raw_val: str, *, field_name: str) -> None:
        if "\x00" in raw_val:
            raise rm_engine.V02PermissionError(
                "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN", f"NUL byte forbidden in {field_name}"
            )
        normalized = raw_val.replace("\\", "/")
        segments = [seg for seg in normalized.split("/") if seg]
        if ".." in segments:
            raise rm_engine.V02PermissionError(
                "PATH_TRAVERSAL_FORBIDDEN", f"Path traversal '..' forbidden in {field_name}"
            )
        p = Path(raw_val)
        if p.is_symlink() or _rep_lease._is_junction(p):
            raise rm_engine.V02PermissionError(
                "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN", f"Symlink/junction forbidden in {field_name}"
            )
        for parent in p.parents:
            if parent.is_symlink() or _rep_lease._is_junction(parent):
                raise rm_engine.V02PermissionError(
                    "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN", f"Symlink/junction ancestor forbidden in {field_name}"
                )

    if isinstance(raw_ws, str) and raw_ws.strip():
        _assert_no_traversal_or_symlink(raw_ws.strip(), field_name="workspace_root")
    if isinstance(raw_rel, str) and raw_rel.strip():
        _assert_no_traversal_or_symlink(raw_rel.strip(), field_name="workspace_rel_path")

    rlf_ro, _ = _rep_lease._load_state_readonly(day_root)
    binding = (rlf_ro.get("representations") or {}).get(artifact_id)
    bound_ws_raw = str(binding.get("workspace_root") or "").strip() if isinstance(binding, Mapping) else ""

    if isinstance(raw_ws, str) and raw_ws.strip():
        ws_path = Path(raw_ws.strip())
        if bound_ws_raw:
            try:
                if ws_path.resolve() != Path(bound_ws_raw).resolve():
                    raise rm_engine.V02PermissionError(
                        "WORKSPACE_ROOT_MISMATCH",
                        "workspace_root does not match the bound representation workspace",
                    )
            except OSError as exc:
                raise rm_engine.V02PermissionError(
                    "WORKSPACE_ROOT_MISMATCH", "Invalid workspace_root"
                ) from exc
    elif bound_ws_raw:
        _assert_no_traversal_or_symlink(bound_ws_raw, field_name="workspace_root")
        ws_path = Path(bound_ws_raw)
    else:
        ws_path = Path(day_root)

    if isinstance(raw_rel, str) and raw_rel.strip():
        ok_p, _, code_p = _rep_lease._resolve_safe_workspace_path(ws_path, raw_rel.strip())
        if not ok_p:
            raise rm_engine.V02PermissionError(code_p, f"Unsafe workspace_rel_path: {code_p}")
        if isinstance(binding, Mapping):
            bound_rel = str(binding.get("workspace_rel_path") or "").strip()
            if bound_rel and raw_rel.strip().replace("\\", "/") != bound_rel.replace("\\", "/"):
                raise rm_engine.V02PermissionError(
                    "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN",
                    "workspace_rel_path does not match bound representation path",
                )
    return ws_path


def _dispatch_growth_and_skill_action(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    action: str,
    norm_actor: str,
    now_iso: Optional[str],
) -> tuple[dict[str, Any], str]:
    clean_payload = {k: v for k, v in payload.items() if k != "action"}

    if action == "issue_representation_lease":
        if norm_actor != "subject":
            raise rm_engine.V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can issue representation leases")
        scope = _require_non_empty_trimmed_str(payload, "scope")
        ttl = int(payload.get("ttl_minutes", 120) if payload.get("ttl_minutes") is not None else 120)
        try:
            receipt = _rep_lease.upsert_or_revoke_representation_lease(
                day_root,
                action="issue",
                scope=scope,
                lease_id=payload.get("lease_id"),
                ttl_minutes=ttl,
                reason=str(payload.get("reason_cn") or ""),
                actor_side=norm_actor,
                now_iso=now_iso,
            )
        except ValueError as exc:
            raise rm_engine.V02ValidationError("INVALID_FIELDS", str(exc)) from exc
    elif action == "revoke_representation_lease":
        scope = _require_non_empty_trimmed_str(payload, "scope")
        try:
            receipt = _rep_lease.upsert_or_revoke_representation_lease(
                day_root,
                action="revoke",
                scope=scope,
                lease_id=payload.get("lease_id"),
                reason=str(payload.get("reason_cn") or ""),
                actor_side=norm_actor,
                now_iso=now_iso,
            )
        except ValueError as exc:
            raise rm_engine.V02ValidationError("INVALID_FIELDS", str(exc)) from exc
    elif action == "request_skill_migration_recheck":
        _enforce_skill_book_lease_or_raise(day_root, now_iso=now_iso)
        receipt = asp_engine.request_skill_recheck(
            day_root,
            clean_payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
    elif action == "start_model_preference_recheck":
        receipt = drift_engine.create_model_preference_recheck_pack(
            day_root,
            clean_payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
    elif action == "record_preference_recheck_answer":
        receipt = drift_engine.record_model_recheck_response(
            day_root,
            clean_payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
    elif action == "log_partner_taste_observation":
        if norm_actor != "partner":
            raise rm_engine.V02PermissionError(
                "FORBIDDEN_ACTOR_SIDE",
                "Subject cannot forge partner taste observations; use register_standard_revision",
            )
        receipt = drift_engine.record_taste_or_standard_shift(
            day_root,
            clean_payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
    elif action == "register_skill_release":
        _enforce_skill_book_lease_or_raise(day_root, now_iso=now_iso)
        receipt = asp_engine.register_skill_release(
            day_root,
            clean_payload,
            now_iso=now_iso,
        )
    elif action == "record_skill_execution_outcome":
        receipt = asp_engine.record_skill_execution_outcome(
            day_root,
            clean_payload,
            now_iso=now_iso,
        )
    elif action == "record_targeted_method_patch":
        _enforce_skill_book_lease_or_raise(day_root, now_iso=now_iso)
        receipt = asp_engine.record_targeted_method_patch(
            day_root,
            clean_payload,
            now_iso=now_iso,
        )
    elif action == "retire_skill_release":
        receipt = asp_engine.retire_skill_release(
            day_root,
            clean_payload,
            now_iso=now_iso,
        )
    elif action == "register_standard_revision":
        if "criteria_id" in clean_payload or "rubric_summary" in clean_payload:
            asp_keys = {
                "criteria_id",
                "standard_revision",
                "domain",
                "pass_threshold",
                "rubric_summary",
                "change_reason",
                "overlap_comparison_note",
                "operation_key",
            }
            asp_pl = {k: v for k, v in clean_payload.items() if k in asp_keys}
            receipt = asp_engine.register_standard_revision(day_root, asp_pl, now_iso=now_iso)
            if isinstance(clean_payload.get("new_stance_cn"), str) and clean_payload["new_stance_cn"].strip():
                shift_pl = {
                    k: v
                    for k, v in clean_payload.items()
                    if k in {"domain", "axis", "previous_stance_cn", "new_stance_cn", "evidence_ref", "shared"}
                }
                if "operation_key" in clean_payload:
                    shift_pl["operation_key"] = f"{clean_payload['operation_key']}::shift"
                shift_res = drift_engine.record_taste_or_standard_shift(
                    day_root,
                    shift_pl,
                    actor_side="subject",
                    now_iso=now_iso,
                )
                receipt["standard_shift"] = shift_res.get("standard_shift")
        else:
            receipt = drift_engine.record_taste_or_standard_shift(
                day_root,
                clean_payload,
                actor_side="subject",
                now_iso=now_iso,
            )
    elif action == "capture_personality_drift_snapshot":
        receipt = drift_engine.capture_drift_snapshot(
            day_root,
            payload=clean_payload,
            now_iso=now_iso,
        )
    elif action == "revoke_skill_source_material":
        receipt = asp_engine.revoke_skill_source_material(
            day_root,
            clean_payload,
            now_iso=now_iso,
        )
    elif action == "reverify_migrated_skill" and "method_id" in clean_payload:
        receipt = asp_engine.reverify_migrated_skill(
            day_root,
            clean_payload,
            now_iso=now_iso,
        )
    elif action == "record_self_claim":
        if norm_actor != "subject":
            raise rm_engine.V02PermissionError(
                "FORBIDDEN_ACTOR_SIDE", "Only subject can record autobiographical self-claims"
            )
        now_dt, _ = rm_engine._resolve_now(now_iso)
        stmt = str(clean_payload.get("statement") or clean_payload.get("statement_cn") or "").strip()
        domain = str(clean_payload.get("domain") or "").strip()
        period_id = str(clean_payload.get("period_id") or "").strip() or now_dt.astimezone(
            rm_engine.DEFAULT_TZ
        ).strftime("%Y-%m")
        claim_key = str(clean_payload.get("claim_key") or "").strip()
        if not claim_key:
            digest8 = hashlib.sha256(f"{domain}:{stmt}".encode("utf-8")).hexdigest()[:8]
            claim_key = f"{domain}:{digest8}"
        vis = clean_payload.get("visibility")
        if vis is None and "shared" in clean_payload and isinstance(clean_payload["shared"], bool):
            vis = "shared" if clean_payload["shared"] else "private"
        claim_pl: dict[str, Any] = {
            "period_id": period_id,
            "claim_key": claim_key,
            "domain": domain,
            "statement": stmt,
        }
        if vis is not None:
            claim_pl["visibility"] = vis
        for opt_k in ("lane", "polarity", "stance", "evidence_refs", "claim_id", "importance_score", "operation_key"):
            if opt_k in clean_payload and clean_payload[opt_k] is not None:
                claim_pl[opt_k] = clean_payload[opt_k]
        receipt = asp_engine.record_self_claim(
            day_root,
            claim_pl,
            now_iso=now_iso,
        )
    elif action == "observe_external_edit":
        if norm_actor != "subject":
            raise rm_engine.V02PermissionError(
                "FORBIDDEN_ACTOR_SIDE", "Only subject can observe external representation edits"
            )
        artifact_id = _require_non_empty_trimmed_str(payload, "artifact_id")
        ws_path = _validate_safe_representation_paths_or_raise(
            day_root, payload, artifact_id=artifact_id
        )
        op_id = str(payload.get("operation_key") or "").strip() or f"op:observe_external_edit:{artifact_id}:{uuid.uuid4().hex[:12]}"
        st_code, rep_res = _rep_lease.observe_external_edit(
            day_root,
            artifact_id=artifact_id,
            workspace_root=ws_path,
            operation_id=op_id,
            actor_ref=str(payload.get("actor_ref") or "external_unverified").strip() or "external_unverified",
            trusted_actor_identity=bool(payload.get("trusted_actor_identity", False)),
            now_iso=now_iso,
        )
        if st_code >= 400 or not rep_res.get("ok"):
            raise rm_engine.V02RuntimeMigrationError(
                st_code,
                str(rep_res.get("code") or "OBSERVE_EXTERNAL_EDIT_FAILED"),
                str(rep_res.get("reason") or rep_res.get("code") or "Failed to observe external edit"),
            )
        receipt = dict(rep_res)
    elif action == "rebuild_projection_from_canonical":
        if norm_actor != "subject":
            raise rm_engine.V02PermissionError(
                "FORBIDDEN_ACTOR_SIDE", "Only subject can rebuild projection from canonical head"
            )
        artifact_id = _require_non_empty_trimmed_str(payload, "artifact_id")
        ws_path = _validate_safe_representation_paths_or_raise(
            day_root, payload, artifact_id=artifact_id
        )
        op_id = str(payload.get("operation_key") or "").strip() or f"op:rebuild_projection:{artifact_id}:{uuid.uuid4().hex[:12]}"
        st_code, rep_res = _rep_lease.rebuild_projection_from_canonical(
            day_root,
            artifact_id=artifact_id,
            workspace_root=ws_path,
            operation_id=op_id,
            now_iso=now_iso,
        )
        if st_code >= 400 or not rep_res.get("ok"):
            raise rm_engine.V02RuntimeMigrationError(
                st_code,
                str(rep_res.get("code") or "REBUILD_PROJECTION_FAILED"),
                str(rep_res.get("reason") or rep_res.get("code") or "Failed to rebuild projection"),
            )
        receipt = dict(rep_res)
    else:
        raise rm_engine.V02ValidationError("INVALID_FIELDS", f"Unsupported delegated action: {action!r}")

    cmd_status = "DUPLICATE" if bool(receipt.get("idempotent_replay", False)) else "APPLIED"
    return receipt, cmd_status


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
        # R0–R5 反傀儡自检 + 五维 Scorecard + 第六维实况白话回执：纯只读挂载，零写盘。
        scorecard = v02_antipuppet_scorecard.evaluate_antipuppet_scorecard(
            day_root,
            now_iso=projected.get("observed_at"),
        )
        sixth_dim, receipt_str = _build_sixth_dim_and_receipt_zero_write(
            day_root,
            now_iso=projected.get("observed_at"),
        )
        scorecard["sixth_dim"] = sixth_dim
        scorecard["plain_chinese_receipt"] = receipt_str
        projected["antipuppet_scorecard"] = scorecard
        _attach_growth_and_skill_projections_zero_write(
            day_root,
            projected,
            has_subject_capability=bool(has_subject_capability),
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except (
        rm_engine.V02RuntimeMigrationError,
        asp_engine.AutobiographySkillProvenanceError,
        drift_engine.PersonalityDriftRecheckError,
    ) as exc:
        extra = getattr(exc, "details", None)
        return _error_response(exc.status_code, exc.reason_code, exc.message, extra=extra)


def mutate_v02_runtime_migration_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Validate strict field whitelist & actor_side permissions, then mutate runtime-migration state."""
    raw_action = payload.get("action") if isinstance(payload, Mapping) else None
    if isinstance(raw_action, str) and raw_action.strip() in HONEST_UX_DELEGATED_ACTIONS:
        norm_side = str(actor_side or "").strip().lower()
        if norm_side == "her":
            norm_side = "subject"
        try:
            for raw_top_key in payload.keys():
                if not isinstance(raw_top_key, str) or not raw_top_key.strip() or raw_top_key != raw_top_key.strip():
                    raise rm_engine.V02ValidationError(
                        "INVALID_FIELDS",
                        f"Blank or whitespace-padded field key forbidden: {raw_top_key!r}",
                    )
            _scan_payload_keys_recursive(payload)
        except rm_engine.V02ValidationError as exc:
            return _error_response(exc.status_code, exc.reason_code, exc.message, extra=exc.details)
        if norm_side != "subject":
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                f"Action {raw_action.strip()!r} is forbidden for actor_side={actor_side!r}",
            )
        allowed = ACTION_ALLOWED_FIELDS[raw_action.strip()]
        unknown_fields = sorted(set(payload.keys()) - allowed)
        if unknown_fields:
            return _error_response(
                400,
                "INVALID_FIELDS",
                f"Unknown fields for {raw_action.strip()!r}: {unknown_fields}",
                extra={"invalid_fields": unknown_fields},
            )
        return _honest_ux.mutate_v02_honest_ux_view(
            day_root,
            payload,
            actor_side="subject",
            now_iso=now_iso,
        )
    if isinstance(raw_action, str) and raw_action in WORLD_LINEAGE_DELEGATED_ACTIONS:
        norm_side = str(actor_side or "").strip().lower()
        if norm_side == "her":
            norm_side = "subject"
        try:
            for raw_top_key in payload.keys():
                if not isinstance(raw_top_key, str) or not raw_top_key.strip() or raw_top_key != raw_top_key.strip():
                    raise rm_engine.V02ValidationError(
                        "INVALID_FIELDS",
                        f"Blank or whitespace-padded field key forbidden: {raw_top_key!r}",
                    )
            _scan_payload_keys_recursive(payload)
        except rm_engine.V02ValidationError as exc:
            return _error_response(exc.status_code, exc.reason_code, exc.message, extra=exc.details)
        if norm_side != "subject":
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                f"Action {raw_action!r} is forbidden for actor_side={actor_side!r}",
            )
        return _world_lineage.mutate_v02_world_lineage_view(
            day_root,
            payload,
            actor_side="subject",
            now_iso=now_iso,
        )
    try:
        norm_actor = _normalize_and_validate_request(payload, actor_side=actor_side)
        action_name = str(payload["action"]).strip()
        is_growth_skill_action = (
            action_name in GROWTH_AND_SKILL_PARTNER_ACTIONS
            or action_name in GROWTH_AND_SKILL_SUBJECT_ACTIONS
            or action_name in SKILL_PROVENANCE_SUBJECT_ACTIONS
            or action_name in REPRESENTATION_LEASE_SUBJECT_ACTIONS
            or action_name in CLOSURE_SUBJECT_ONLY_ACTIONS
            or (action_name == "reverify_migrated_skill" and "method_id" in payload)
        )
        if is_growth_skill_action:
            receipt, cmd_status = _dispatch_growth_and_skill_action(
                day_root,
                payload,
                action=action_name,
                norm_actor=norm_actor,
                now_iso=now_iso,
            )
            projected = rm_engine.project_v02_runtime_migration(
                day_root,
                has_subject_capability=(norm_actor == "subject"),
                now_iso=now_iso,
            )
            _attach_growth_and_skill_projections_zero_write(
                day_root,
                projected,
                has_subject_capability=(norm_actor == "subject"),
            )
            projected["action_receipt"] = receipt
            return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)

        projected, cmd_status = rm_engine.apply_runtime_migration_mutation(
            day_root,
            payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
        _attach_growth_and_skill_projections_zero_write(
            day_root,
            projected,
            has_subject_capability=(norm_actor == "subject"),
        )
        return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)
    except (
        rm_engine.V02RuntimeMigrationError,
        asp_engine.AutobiographySkillProvenanceError,
        drift_engine.PersonalityDriftRecheckError,
    ) as exc:
        extra = getattr(exc, "details", None)
        return _error_response(exc.status_code, exc.reason_code, exc.message, extra=extra)


def summarize_runtime_migration_for_context(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-write helper returning budget/dormant conservation & unvalidated skill hints."""
    return rm_engine.build_runtime_migration_context_summary(day_root, now_iso=now_iso)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "CLOSURE_SUBJECT_ONLY_ACTIONS",
    "DEVICE_PRESENCE_ACTIONS",
    "DIGNIFIED_RETIREMENT_ACTIONS",
    "ENDPOINT_PATH",
    "GROWTH_AND_SKILL_PARTNER_ACTIONS",
    "GROWTH_AND_SKILL_SUBJECT_ACTIONS",
    "MIGRATION_LOSS_MANIFEST_SCHEMA",
    "PARTNER_ALLOWED_ACTIONS",
    "PROACTIVE_OUTBOUND_PARTNER_ACTIONS",
    "PROACTIVE_OUTBOUND_SUBJECT_ACTIONS",
    "REPRESENTATION_LEASE_PARTNER_ACTIONS",
    "REPRESENTATION_LEASE_SUBJECT_ACTIONS",
    "SKILL_PROVENANCE_SUBJECT_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "SUITCASE_ALLOWED_SLICE_FILES",
    "SUITCASE_ALLOWED_WORLD_FILES",
    "SUITCASE_ALLOWED_WORLD_PACKAGE_FILES",
    "SUITCASE_PORTABLE_ACTIONS",
    "VC_SUBJECT_ONLY_ACTIONS",
    "VIEW_SCHEMA",
    "WORLD_LINEAGE_DELEGATED_ACTIONS",
    "mutate_v02_runtime_migration_view",
    "read_v02_runtime_migration_view",
    "summarize_runtime_migration_for_context",
]
