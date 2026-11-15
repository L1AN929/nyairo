#!/usr/bin/env python3
"""HTTP 视图与动作闸门封装：``/api/phone/v02/agency-depth``（品味演化、后悔与二阶意愿引擎）。

职责与边界：
1. ``read_v02_agency_depth_view``：
   - 100% 零磁盘写入（零新建目录、零临时文件、零 mtime 变化）；
   - ``has_subject_capability=False``（伴侣视角）时自动过滤所有 ``shared is False`` 条目，
     并强制 ``regret_reflections: []``、``regret_reflections_withheld: True``。
2. ``mutate_v02_agency_depth_view``：
   - 严格执行 ``ACTION_ALLOWED_FIELDS`` 白名单校验与 ``PARTNER_ALLOWED_ACTIONS`` / ``SUBJECT_ALLOWED_ACTIONS`` 权限边界；
   - 畸形/外籍/空白字段返回 ``400 INVALID_FIELDS``，越权动作返回 ``403 FORBIDDEN_ACTOR_SIDE``，
     无反事实依据的机会损失后悔返回 ``409 MISSING_OBSERVABLE_COUNTERFACTUAL``；
   - 支持 ``operation_key`` 幂等重放（``DUPLICATE`` / ``already_applied``）与冲突拦截（``409 OPERATION_KEY_CONFLICT``）。
3. ``summarize_agency_depth_for_context``：
   - 100% 零磁盘写入背景摘要，``include_private=False`` 时严禁暴露任何未共享条目或后悔内容。
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

from components.life.scripts import v02_agency_depth_engine as ad_engine

ENDPOINT_PATH = "/api/phone/v02/agency-depth"
VIEW_SCHEMA = "nyairo.v02.agency_depth.v1"
V02_AGENCY_DEPTH_SCHEMA = VIEW_SCHEMA

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "offer_low_cost_option",
    "propose_shared_gentle_rule",
    "share_incubation_clue",
    "withdraw_partner_option",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "record_choice_episode",
    "record_exposure_satiation",
    "park_or_resolve_conflict",
    "record_regret_reflection",
    "manage_second_order_rule",
    "manage_open_incubation",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "record_choice_episode": COMMON_COMMAND_FIELDS | frozenset({
        "domain",
        "chosen",
        "options",
        "choice_mode",
        "forgone_cost",
        "note_cn",
        "shared",
    }),
    "record_exposure_satiation": COMMON_COMMAND_FIELDS | frozenset({
        "domain",
        "title",
        "chosen",
        "intensity",
        "elapsed_hours",
        "note_cn",
        "shared",
    }),
    "offer_low_cost_option": COMMON_COMMAND_FIELDS | frozenset({
        "domain",
        "option_title",
        "note",
    }),
    "withdraw_partner_option": COMMON_COMMAND_FIELDS | frozenset({
        "option_id",
    }),
    "park_or_resolve_conflict": COMMON_COMMAND_FIELDS | frozenset({
        "conflict_op",
        "conflict_id",
        "title",
        "side_a",
        "side_b",
        "note_cn",
        "shared",
    }),
    "record_regret_reflection": COMMON_COMMAND_FIELDS | frozenset({
        "regret_kind",
        "summary",
        "counterfactual_note",
        "has_observable_alternative",
        "method_adjustment_cn",
        "shared",
    }),
    "propose_shared_gentle_rule": COMMON_COMMAND_FIELDS | frozenset({
        "title",
        "trigger_window",
        "reason_cn",
    }),
    "manage_second_order_rule": COMMON_COMMAND_FIELDS | frozenset({
        "rule_op",
        "rule_id",
        "title",
        "trigger_window",
        "reason_cn",
        "shared",
    }),
    "manage_open_incubation": COMMON_COMMAND_FIELDS | frozenset({
        "inc_op",
        "question_id",
        "topic",
        "evidence_ref",
        "clue_text",
        "note_cn",
        "shared",
    }),
    "share_incubation_clue": COMMON_COMMAND_FIELDS | frozenset({
        "clue_text",
        "question_id",
        "topic",
    }),
}

_OPTIONAL_NULLABLE_FIELDS: frozenset[str] = frozenset({
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
    "forgone_cost",
    "note_cn",
    "shared",
    "title",
    "chosen",
    "intensity",
    "elapsed_hours",
    "note",
    "conflict_id",
    "side_a",
    "side_b",
    "counterfactual_note",
    "has_observable_alternative",
    "method_adjustment_cn",
    "reason_cn",
    "rule_id",
    "trigger_window",
    "question_id",
    "topic",
    "evidence_ref",
    "clue_text",
})

_OPTIONAL_BLANK_STRING_FIELDS: frozenset[str] = frozenset({
    "operation_key",
    "intent_fingerprint",
    "forgone_cost",
    "note_cn",
    "note",
    "conflict_id",
    "side_a",
    "side_b",
    "counterfactual_note",
    "method_adjustment_cn",
    "reason_cn",
    "rule_id",
    "question_id",
    "topic",
    "evidence_ref",
    "clue_text",
})

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "perspective",
    "revision",
    "observed_at",
    "preferences",
    "choice_episodes",
    "partner_options",
    "low_cost_options",
    "parked_conflicts",
    "open_incubations",
    "second_order_rules",
    "method_adjustments",
    "regret_reflections",
    "regret_reflections_withheld",
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
        raise ad_engine.V02ValidationError("INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise ad_engine.V02ValidationError("INVALID_FIELDS", "Blank or whitespace-padded key forbidden")
            if (
                raw_key in ad_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
                or raw_key.lower() in ad_engine.FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS
            ):
                raise ad_engine.V02ValidationError("INVALID_FIELDS", f"Forbidden key: {raw_key!r}")
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _validate_request_payload(payload: Any, *, actor_side: str) -> str:
    if actor_side not in ("partner", "subject"):
        raise ad_engine.V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise ad_engine.V02ValidationError("INVALID_FIELDS", "Payload must be a non-empty JSON object")

    _scan_payload_keys_recursive(payload)

    if "action" not in payload:
        raise ad_engine.V02ValidationError("INVALID_FIELDS", "Missing required field 'action'")
    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        raise ad_engine.V02ValidationError("INVALID_FIELDS", "Field 'action' must be a non-empty trimmed string")
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        raise ad_engine.V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    allowed_actions = SUBJECT_ALLOWED_ACTIONS if actor_side == "subject" else PARTNER_ALLOWED_ACTIONS
    if action not in allowed_actions:
        raise ad_engine.V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is not permitted for {actor_side}",
        )

    unknown = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        raise ad_engine.V02ValidationError("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    for k, v in payload.items():
        if k == "action":
            continue
        if v is None:
            if k in _OPTIONAL_NULLABLE_FIELDS:
                continue
            raise ad_engine.V02ValidationError("INVALID_FIELDS", f"Null field not allowed: {k}")
        if isinstance(v, str) and not v.strip():
            if k in _OPTIONAL_BLANK_STRING_FIELDS and not (
                (action == "share_incubation_clue" and k == "clue_text")
                or (action == "record_choice_episode" and k == "chosen")
                or (action == "propose_shared_gentle_rule" and k in ("title", "trigger_window"))
            ):
                continue
            raise ad_engine.V02ValidationError("INVALID_FIELDS", f"Blank string not allowed: {k}")

    return action


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    read_only: bool,
    command_status: str = "READ",
) -> dict[str, Any]:
    data_block: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "read_only": bool(read_only),
    }
    for key in _MIRRORED_DATA_KEYS:
        if key in projected:
            data_block[key] = copy.deepcopy(projected[key])

    envelope: dict[str, Any] = {
        "ok": True,
        "status": "ok" if read_only else ("already_applied" if command_status == "DUPLICATE" else "applied"),
        "command_status": command_status,
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "data": data_block,
    }
    for key, val in data_block.items():
        if key not in envelope:
            envelope[key] = copy.deepcopy(val)
    return envelope


def read_v02_agency_depth_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """读取品味演化与二阶意愿视图（100% 零磁盘写入，伴侣视角过滤未共享项与后悔内容）。"""
    try:
        projected = ad_engine.project_v02_agency_depth(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except ad_engine.V02AgencyDepthError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v02_agency_depth_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """执行品味演化、后悔反思或二阶小规矩写动作（白名单闸门 + 权限隔离 + 幂等重放）。"""
    try:
        _validate_request_payload(payload, actor_side=actor_side)
        projected, cmd_status = ad_engine.apply_agency_depth_mutation(
            day_root,
            payload,
            actor_side=actor_side,
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)
    except ad_engine.V02AgencyDepthError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def summarize_agency_depth_for_context(
    day_root: Path | str,
    *,
    include_private: bool = True,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% 零磁盘写入辅助摘要，供聊天背景与日常候选桥接读取。"""
    return ad_engine.summarize_agency_depth_state_for_context(
        day_root,
        include_private=bool(include_private),
        now_iso=now_iso,
    )


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "V02_AGENCY_DEPTH_SCHEMA",
    "VIEW_SCHEMA",
    "mutate_v02_agency_depth_view",
    "read_v02_agency_depth_view",
    "summarize_agency_depth_for_context",
]
