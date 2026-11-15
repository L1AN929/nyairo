#!/usr/bin/env python3
"""HTTP 视图与动作闸门封装：``/api/phone/v04/elastic-play``（第 45–53 章弹性方向、共同试验与玩心）。

职责与边界：
1. ``read_v04_elastic_play_view``：
   - 100% 零磁盘写入（零新建目录、零临时文件、零 mtime 变化）；
   - ``has_subject_capability=False`` 时自动剥离 ``prepared_waiting`` 未拿出惊喜与 ``private_two_phrasings`` 私密草稿。
2. ``mutate_v04_elastic_play_view``：
   - 逐动作严格执行 ``ACTION_ALLOWED_FIELDS`` 白名单校验与 ``PARTNER_ALLOWED_ACTIONS`` / ``SUBJECT_ALLOWED_ACTIONS`` 权限隔离；
   - 未知字段、空键名或伪造身份/计分板字段一律返回 ``(400, {"ok": False, ..., "reason_code": "INVALID_FIELDS"})``；
   - 越权动作一律返回 ``(403, {"ok": False, ..., "reason_code": "FORBIDDEN_ACTOR_SIDE"})``。
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

from components.life.scripts import v04_elastic_play_learning as ep_engine

V04_ELASTIC_PLAY_SCHEMA = ep_engine.VIEW_SCHEMA
VIEW_SCHEMA = V04_ELASTIC_PLAY_SCHEMA
ENDPOINT_PATH = "/api/phone/v04/elastic-play"

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "append_shared_notebook",
    "share_reading_notice",
    "propose_or_respond_experiment",
    "teach_scoped_preference",
    "request_or_answer_practice",
    "play_temporary_game",
    "respond_prepared_surprise",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "set_day_direction",
    "append_shared_notebook",
    "share_reading_notice",
    "propose_or_respond_experiment",
    "save_two_phrasings",
    "teach_scoped_preference",
    "request_or_answer_practice",
    "play_temporary_game",
    "prepare_or_offer_surprise",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "set_day_direction": COMMON_COMMAND_FIELDS | frozenset({
        "mode",
        "direction",
        "theme",
        "title",
        "direction_text",
        "optional_directions",
        "commitments",
        "current_focus",
        "interrupt_reason",
        "completed_summary",
        "receipt_ref",
        "clear",
    }),
    "append_shared_notebook": COMMON_COMMAND_FIELDS | frozenset({
        "text",
        "body",
        "expected_head_revision",
    }),
    "share_reading_notice": COMMON_COMMAND_FIELDS | frozenset({
        "material_title",
        "page",
        "line_note",
    }),
    "propose_or_respond_experiment": COMMON_COMMAND_FIELDS | frozenset({
        "exp_op",
        "operation",
        "experiment_id",
        "title",
        "question",
        "original_snippet",
        "candidate_snippet",
        "take_text",
        "outcome",
    }),
    "save_two_phrasings": COMMON_COMMAND_FIELDS | frozenset({
        "context_note",
        "phrasing_a",
        "phrasing_b",
        "chosen",
    }),
    "teach_scoped_preference": COMMON_COMMAND_FIELDS | frozenset({
        "scope",
        "instruction",
        "preference",
    }),
    "request_or_answer_practice": COMMON_COMMAND_FIELDS | frozenset({
        "practice_op",
        "topic",
        "prompt_text",
        "answer_text",
        "feedback_text",
    }),
    "play_temporary_game": COMMON_COMMAND_FIELDS | frozenset({
        "game_op",
        "game_kind",
        "prop_label",
        "rule_summary",
        "turn_text",
        "keepsake_title",
    }),
    "prepare_or_offer_surprise": COMMON_COMMAND_FIELDS | frozenset({
        "stage",
        "surprise_op",
        "surprise_id",
        "kind",
        "title",
        "content",
        "invite_hint",
    }),
    "respond_prepared_surprise": COMMON_COMMAND_FIELDS | frozenset({
        "surprise_id",
        "decision",
        "response",
    }),
}

_OPTIONAL_BLANKABLE_FIELDS: frozenset[str] = frozenset({
    "operation_key",
    "intent_fingerprint",
    "question",
    "original_snippet",
    "candidate_snippet",
    "outcome",
    "context_note",
    "topic",
    "prop_label",
    "rule_summary",
    "keepsake_title",
    "invite_hint",
    "receipt_ref",
})


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
        "schema": V04_ELASTIC_PLAY_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "reason_code": code_str,
        "error_code": code_str,
        "detail": str(detail or code_str),
        "data": {},
    }


def _scan_payload_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise ep_engine.V04ValidationError("INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise ep_engine.V04ValidationError("INVALID_FIELDS", "Blank or whitespace-padded key forbidden")
            if raw_key in ep_engine.FORBIDDEN_FOREIGN_AND_SCORE_KEYS or raw_key.lower() in ep_engine.FORBIDDEN_FOREIGN_AND_SCORE_KEYS:
                raise ep_engine.V04ValidationError("INVALID_FIELDS", f"Forbidden key: {raw_key!r}")
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _validate_request_payload(payload: Any, *, actor_side: str) -> str:
    if actor_side not in ("partner", "subject"):
        raise ep_engine.V04PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise ep_engine.V04ValidationError("INVALID_FIELDS", "Payload must be a non-empty JSON object")

    _scan_payload_keys_recursive(payload)

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        raise ep_engine.V04ValidationError("INVALID_ACTION", "Field 'action' must be a non-empty trimmed string")
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        raise ep_engine.V04ValidationError("INVALID_ACTION", f"Unknown action: {action!r}")

    allowed_actions = SUBJECT_ALLOWED_ACTIONS if actor_side == "subject" else PARTNER_ALLOWED_ACTIONS
    if action not in allowed_actions:
        raise ep_engine.V04PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is not permitted for {actor_side}",
        )

    unknown = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        raise ep_engine.V04ValidationError("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    for k, v in payload.items():
        if k == "action":
            continue
        if k == "receipt_ref":
            # Let engine enforce 409 MISSING_COMPLETION_RECEIPT when blank/None in record_completed
            continue
        if v is None:
            if k in _OPTIONAL_BLANKABLE_FIELDS:
                continue
            raise ep_engine.V04ValidationError("INVALID_FIELDS", f"Null field not allowed: {k}")
        if isinstance(v, str) and not v.strip():
            if k in _OPTIONAL_BLANKABLE_FIELDS:
                continue
            raise ep_engine.V04ValidationError("INVALID_FIELDS", f"Blank string not allowed: {k}")

    return action


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    read_only: bool,
    command_status: str = "READ",
) -> dict[str, Any]:
    mirrored_keys = (
        "state_exists",
        "perspective",
        "revision",
        "observed_at",
        "identity",
        "day_direction",
        "shared_notebook",
        "reading_notices",
        "experiments",
        "scoped_preferences",
        "practice_sessions",
        "temporary_game",
        "game_keepsakes",
        "prepared_surprises",
        "private_two_phrasings",
        "private_two_phrasings_withheld",
        "llm_calls",
    )
    data_block: dict[str, Any] = {
        "schema": V04_ELASTIC_PLAY_SCHEMA,
        "read_only": bool(read_only),
    }
    for k in mirrored_keys:
        if k in projected:
            data_block[k] = copy.deepcopy(projected[k])
    if "hidden_waiting_surprise_count" in projected:
        data_block["hidden_waiting_surprise_count"] = int(projected["hidden_waiting_surprise_count"])

    envelope: dict[str, Any] = {
        "ok": True,
        "status": "ok" if read_only else ("already_applied" if command_status == "DUPLICATE" else "applied"),
        "command_status": command_status,
        "schema": V04_ELASTIC_PLAY_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "data": data_block,
    }
    for k, v in data_block.items():
        if k not in envelope:
            envelope[k] = copy.deepcopy(v)
    return envelope


def read_v04_elastic_play_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    try:
        projected = ep_engine.project_v04_elastic_play(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except ep_engine.V04ElasticPlayError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v04_elastic_play_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    try:
        _validate_request_payload(payload, actor_side=actor_side)
        projected, cmd_status = ep_engine.apply_elastic_play_mutation(
            day_root,
            payload,
            actor_side=actor_side,
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)
    except ep_engine.V04ElasticPlayError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)
