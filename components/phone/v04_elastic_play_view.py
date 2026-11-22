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

from components.life.scripts import v02_representation_lease_forget as _rlf
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
    "share_distinct_annotation",
    "propose_or_respond_experiment",
    "teach_scoped_preference",
    "request_or_answer_practice",
    "play_temporary_game",
    "respond_prepared_surprise",
    "note_admin_world_change",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "set_day_direction",
    "append_shared_notebook",
    "share_reading_notice",
    "share_distinct_annotation",
    "propose_or_respond_experiment",
    "save_two_phrasings",
    "teach_scoped_preference",
    "request_or_answer_practice",
    "play_temporary_game",
    "prepare_or_offer_surprise",
    "curate_personal_project",
    "propose_grounded_alternative",
    "note_admin_world_change",
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
        # Ch.49 一天可以有方向，但不应提前发生：可选步骤推进、打断保留草稿与离线断点。
        "active_step_ref",
        "preserve_draft_note",
        "offline_gap_hours",
        "offline_gap_note",
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
    "share_distinct_annotation": COMMON_COMMAND_FIELDS | frozenset({
        "material_title",
        "source_ref",
        "page",
        "noticed_detail_cn",
        "quote_excerpt_cn",
        "annotation_ref",
        "shared_with_partner",
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
        "target_work_ref",
        "divergence_memo_cn",
    }),
    "save_two_phrasings": COMMON_COMMAND_FIELDS | frozenset({
        "context_note",
        "phrasing_a",
        "phrasing_b",
        "chosen",
        "imagined_reply_cn",
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
        "fictional_prop_object_id",
        "fictional_role_cn",
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
    "curate_personal_project": COMMON_COMMAND_FIELDS | frozenset({
        "op",
        "project_op",
        "project_id",
        "purpose_cn",
        "medium_kind",
        "linked_work_ref",
        "next_step_cn",
        "claimed_real_audio_recording",
        "place_id",
        "entry_status",
        "note_cn",
        "private_raw_note_cn",
        "heard_sources",
        "entry_index",
        "evidence_receipt_ref",
    }),
    "propose_grounded_alternative": COMMON_COMMAND_FIELDS | frozenset({
        "target_suggestion_cn",
        "alternative_cn",
        "grounded_basis_cn",
        "action_ref",
    }),
    "note_admin_world_change": COMMON_COMMAND_FIELDS | frozenset({
        "change_summary_cn",
        "subject_opinion_cn",
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
    "linked_work_ref",
    "next_step_cn",
    "place_id",
    "private_raw_note_cn",
    "evidence_receipt_ref",
    "action_ref",
    "subject_opinion_cn",
    "source_ref",
    "quote_excerpt_cn",
    "annotation_ref",
    "imagined_reply_cn",
    "target_work_ref",
    "divergence_memo_cn",
    "fictional_prop_object_id",
    "fictional_role_cn",
    "active_step_ref",
    "preserve_draft_note",
    "offline_gap_note",
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
        "distinct_annotations",
        "distinct_annotation_pairs",
        "experiments",
        "unresolved_bound_divergences",
        "scoped_preferences",
        "practice_sessions",
        "temporary_game",
        "game_keepsakes",
        "prepared_surprises",
        "personal_projects",
        "grounded_alternatives",
        "admin_world_edits",
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


def _apply_forget_barrier(
    projected: Mapping[str, Any],
    barrier: Mapping[str, Any],
) -> dict[str, Any]:
    """Filter all elastic-play collections and active play/direction refs through the zero-write forget barrier."""
    out = copy.deepcopy(dict(projected))
    fail_closed = bool(barrier.get("fail_closed"))

    for list_key in (
        "reading_notices",
        "distinct_annotations",
        "distinct_annotation_pairs",
        "experiments",
        "scoped_preferences",
        "game_keepsakes",
        "prepared_surprises",
        "personal_projects",
        "grounded_alternatives",
        "admin_world_edits",
        "private_two_phrasings",
    ):
        if isinstance(out.get(list_key), list):
            out[list_key] = _rlf.filter_collection_by_barrier(out[list_key], barrier)

    nb = out.get("shared_notebook")
    if isinstance(nb, dict):
        for nb_key in ("entries", "branches"):
            if isinstance(nb.get(nb_key), list):
                nb[nb_key] = _rlf.filter_collection_by_barrier(nb[nb_key], barrier)

    ubd = out.get("unresolved_bound_divergences")
    if isinstance(ubd, dict) and isinstance(ubd.get("rows"), list):
        ubd["rows"] = _rlf.filter_collection_by_barrier(ubd["rows"], barrier)
        ubd["count"] = len(ubd["rows"])

    ps = out.get("practice_sessions")
    if isinstance(ps, dict):
        if isinstance(ps.get("history"), list):
            ps["history"] = _rlf.filter_collection_by_barrier(ps["history"], barrier)
        if fail_closed or _rlf.is_entity_forget_blocked(ps, barrier):
            for pkey in ("topic", "current_prompt", "last_answer", "gentle_feedback"):
                if pkey in ps:
                    ps[pkey] = None if ps[pkey] is None else ""

    dd = out.get("day_direction")
    if isinstance(dd, dict):
        for dlist in ("optional_directions", "commitments", "completed_items"):
            if isinstance(dd.get(dlist), list):
                dd[dlist] = _rlf.filter_collection_by_barrier(dd[dlist], barrier)
        if fail_closed or _rlf.is_entity_forget_blocked(dd, barrier):
            for dkey in ("direction_id", "direction", "current_focus", "interrupt_note"):
                if dkey in dd:
                    dd[dkey] = None if dkey == "direction_id" else ""

    tg = out.get("temporary_game")
    if isinstance(tg, dict):
        if isinstance(tg.get("turns"), list):
            tg["turns"] = _rlf.filter_collection_by_barrier(tg["turns"], barrier)
        if fail_closed or _rlf.is_entity_forget_blocked(tg, barrier):
            tg["active"] = False
            tg["game_id"] = None
            tg["turns"] = []

    if fail_closed and "hidden_waiting_surprise_count" in out:
        out["hidden_waiting_surprise_count"] = 0

    return out


def read_v04_elastic_play_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    try:
        barrier = _rlf.load_unified_forget_barrier_readonly(day_root)
        projected = _apply_forget_barrier(
            ep_engine.project_v04_elastic_play(
                day_root,
                has_subject_capability=bool(has_subject_capability),
                now_iso=now_iso,
            ),
            barrier,
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
