#!/usr/bin/env python3
"""HTTP 视图与动作闸门封装：``/api/phone/v02/crossmodal``（第 13/15/16 章跨模态真读图配色、试色窗与未懂书签）。

职责与边界：
1. ``read_v02_crossmodal_view``：
   - 100% 零磁盘写入（零新建目录、零临时文件、零 mtime 变化）；
   - 每次读取实时复核绑定 ``album_id`` 的原文件是否仍在磁盘上，一旦原图被删或失效立即扣留色板（``source_available: False``、``dominant_hex: []``）；
   - ``has_subject_capability=False``（伴侣视角）时自动剥离 ``shared=False`` 的私人配色卡、``album_id`` 私密原始路径与私人未公开书签。
2. ``mutate_v02_crossmodal_view``：
   - 严格执行 ``ACTION_ALLOWED_FIELDS`` 白名单校验与 ``PARTNER_ALLOWED_ACTIONS`` / ``SUBJECT_ALLOWED_ACTIONS`` 权限隔离；
   - 畸形/外籍字段返回 ``400 INVALID_FIELDS``，非法色值返回 ``400 INVALID_HEX_COLOR``，损坏图片返回 ``400 INVALID_IMAGE_BYTES``，越权动作返回 ``403 FORBIDDEN_ACTOR_SIDE``；
   - 支持 ``operation_key`` 幂等重放。
3. ``summarize_crossmodal_for_context``：
   - 100% 零写辅助摘要，供聊天背景与候选桥接读取当前试色窗色块、最近提取色调与未懂阅读书签。
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

from components.life.scripts import v02_crossmodal_perception as cm_engine

V02_CROSSMODAL_SCHEMA = cm_engine.VIEW_SCHEMA
VIEW_SCHEMA = V02_CROSSMODAL_SCHEMA
ENDPOINT_PATH = "/api/phone/v02/crossmodal"

extract_real_image_palette = cm_engine.extract_real_image_palette

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "analyze_image_palette",
    "try_color_swatches",
    "clear_color_trial_pad",
    "mark_reading_uncertain",
    "curate_shared_collection",
    "ingest_audio_note",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "apply_palette_to_work",
    "check_collection_staleness",
    "invalidate_deleted_media_source",
    "curate_unshown_box_item",
    "withdraw_box_show_intent",
    "invalidate_deleted_box_source",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "analyze_image_palette": COMMON_COMMAND_FIELDS | frozenset({
        "title",
        "image_base64",
        "album_id",
        "share_with_room",
        "work_id",
    }),
    "try_color_swatches": COMMON_COMMAND_FIELDS | frozenset({
        "swatches",
        "note_cn",
    }),
    "clear_color_trial_pad": COMMON_COMMAND_FIELDS,
    "mark_reading_uncertain": COMMON_COMMAND_FIELDS | frozenset({
        "mark_id",
        "book_title",
        "page",
        "passage_hint",
        "state",
        "share_with_room",
    }),
    "apply_palette_to_work": COMMON_COMMAND_FIELDS | frozenset({
        "study_id",
        "work_id",
        "work_title",
        "version_hint",
        "selected_hex",
        "usage_note_cn",
        "share_with_room",
    }),
    "check_collection_staleness": COMMON_COMMAND_FIELDS | frozenset({
        "item_id",
        "title",
        "source_kind",
        "album_id",
        "source_ref",
        "status",
        "source_alive",
        "note_cn",
        "share_with_room",
    }),
    "curate_shared_collection": COMMON_COMMAND_FIELDS | frozenset({
        "item_id",
        "title",
        "source_kind",
        "source_ref",
        "status",
        "note_cn",
        "share_with_room",
    }),
    "invalidate_deleted_media_source": COMMON_COMMAND_FIELDS | frozenset({
        "study_id",
        "album_id",
        "sha256",
        "reason_cn",
    }),
    "curate_unshown_box_item": COMMON_COMMAND_FIELDS | frozenset({
        "box_item_id",
        "artifact_id",
        "version_id",
        "title_cn",
        "curation_state",
        "reuse_mode",
        "note_cn",
        "source_deleted",
    }),
    "withdraw_box_show_intent": COMMON_COMMAND_FIELDS | frozenset({
        "box_item_id",
    }),
    "invalidate_deleted_box_source": COMMON_COMMAND_FIELDS | frozenset({
        "artifact_id",
    }),
    "ingest_audio_note": COMMON_COMMAND_FIELDS | frozenset({
        "title_cn",
        "duration_seconds",
        "audio_format",
        "source_kind",
        "sha256_hex",
        "transcript_cn",
        "transcript_status",
        "ambient_tag_cn",
        "shared",
    }),
}

_OPTIONAL_NULLABLE_FIELDS: frozenset[str] = frozenset({
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
    "image_base64",
    "album_id",
    "share_with_room",
    "note_cn",
    "mark_id",
    "state",
    "work_id",
    "work_title",
    "version_hint",
    "selected_hex",
    "usage_note_cn",
    "item_id",
    "title",
    "source_kind",
    "source_ref",
    "status",
    "source_alive",
    "study_id",
    "sha256",
    "reason_cn",
    "box_item_id",
    "version_id",
    "reuse_mode",
    "source_deleted",
    "sha256_hex",
    "transcript_cn",
    "transcript_status",
    "ambient_tag_cn",
    "shared",
})

_OPTIONAL_BLANK_STRING_FIELDS: frozenset[str] = frozenset({
    "operation_key",
    "intent_fingerprint",
    "image_base64",
    "album_id",
    "note_cn",
    "mark_id",
    "work_id",
    "work_title",
    "version_hint",
    "usage_note_cn",
    "item_id",
    "source_ref",
    "study_id",
    "sha256",
    "reason_cn",
    "box_item_id",
    "version_id",
    "sha256_hex",
    "transcript_cn",
    "transcript_status",
    "ambient_tag_cn",
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
        "schema": V02_CROSSMODAL_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "reason_code": code_str,
        "error_code": code_str,
        "detail": str(detail or code_str),
        "llm_calls": 0,
        "data": {},
    }


def _scan_payload_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise cm_engine.V02ValidationError("INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise cm_engine.V02ValidationError("INVALID_FIELDS", "Blank or whitespace-padded key forbidden")
            if (
                raw_key in cm_engine.FORBIDDEN_FOREIGN_AND_SCORE_KEYS
                or raw_key.lower() in cm_engine.FORBIDDEN_FOREIGN_AND_SCORE_KEYS
            ):
                raise cm_engine.V02ValidationError("INVALID_FIELDS", f"Forbidden key: {raw_key!r}")
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _validate_request_payload(payload: Any, *, actor_side: str) -> str:
    if actor_side not in ("partner", "subject"):
        raise cm_engine.V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise cm_engine.V02ValidationError("INVALID_FIELDS", "Payload must be a non-empty JSON object")

    _scan_payload_keys_recursive(payload)

    if "action" not in payload:
        raise cm_engine.V02ValidationError("INVALID_FIELDS", "Missing required field 'action'")
    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        raise cm_engine.V02ValidationError("INVALID_FIELDS", "Field 'action' must be a non-empty trimmed string")
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        raise cm_engine.V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    allowed_actions = SUBJECT_ALLOWED_ACTIONS if actor_side == "subject" else PARTNER_ALLOWED_ACTIONS
    if action not in allowed_actions:
        raise cm_engine.V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is not permitted for {actor_side}",
        )

    unknown = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        raise cm_engine.V02ValidationError("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    for k, v in payload.items():
        if k == "action":
            continue
        if v is None:
            if k in _OPTIONAL_NULLABLE_FIELDS:
                continue
            raise cm_engine.V02ValidationError("INVALID_FIELDS", f"Null field not allowed: {k}")
        if isinstance(v, str) and not v.strip():
            if k in _OPTIONAL_BLANK_STRING_FIELDS:
                continue
            raise cm_engine.V02ValidationError("INVALID_FIELDS", f"Blank string not allowed: {k}")

    return action


_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "perspective",
    "revision",
    "observed_at",
    "palette_studies",
    "work_palette_applications",
    "color_trial_pad",
    "reading_uncertainty_marks",
    "collection_health",
    "unshown_work_box",
    "audio_notes",
    "audio_note_count",
    "private_studies_withheld_count",
    "private_marks_withheld_count",
    "action_receipt",
    "llm_calls",
)


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    read_only: bool,
    command_status: str = "READ",
) -> dict[str, Any]:
    data_block: dict[str, Any] = {
        "schema": V02_CROSSMODAL_SCHEMA,
        "read_only": bool(read_only),
    }
    for k in _MIRRORED_DATA_KEYS:
        if k in projected:
            data_block[k] = copy.deepcopy(projected[k])

    envelope: dict[str, Any] = {
        "ok": True,
        "status": "ok" if read_only else ("already_applied" if command_status == "DUPLICATE" else "applied"),
        "command_status": command_status,
        "schema": V02_CROSSMODAL_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "data": data_block,
    }
    for k, v in data_block.items():
        if k not in envelope:
            envelope[k] = copy.deepcopy(v)
    return envelope


def read_v02_crossmodal_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """读取跨模态视图（100% 零磁盘写入，实时复核原图是否存活并剥离伴侣侧不可见隐私）。"""
    try:
        projected = cm_engine.project_v02_crossmodal(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except cm_engine.V02CrossmodalError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v02_crossmodal_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """执行跨模态写动作（白名单字段闸门 + 角色权限边界 + ``operation_key`` 幂等重放）。"""
    try:
        _validate_request_payload(payload, actor_side=actor_side)
        projected, cmd_status = cm_engine.apply_crossmodal_mutation(
            day_root,
            payload,
            actor_side=actor_side,
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=False, command_status=cmd_status)
    except cm_engine.V02CrossmodalError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def summarize_crossmodal_for_context(
    day_root: Path | str,
    *,
    include_private: bool = True,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """零磁盘写入辅助摘要，供聊天背景与候选桥接读取桌面在试的颜色、最近照片色调与未懂书签。"""
    return cm_engine.summarize_crossmodal_state_for_context(
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
    "V02_CROSSMODAL_SCHEMA",
    "VIEW_SCHEMA",
    "extract_real_image_palette",
    "mutate_v02_crossmodal_view",
    "read_v02_crossmodal_view",
    "summarize_crossmodal_for_context",
]
