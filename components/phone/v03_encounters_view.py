#!/usr/bin/env python3
"""HTTP 视图与动作闸门：``/api/phone/v03/encounters``（有限视野、递材料与听说边界）。

职责与边界：
1. ``read_v03_encounters_view``：100% 零磁盘写入；无凭据（伴侣视角）时过滤所有
   ``shared is False`` 的遇见条目，且绝不泄露隐藏计数。
2. ``mutate_v03_encounters_view``：严格 ``ACTION_ALLOWED_FIELDS`` 白名单、身份键拒绝与
   ``PARTNER_ALLOWED_ACTIONS`` / 主体动作权限边界；``operation_key`` 幂等重放与冲突拦截。
3. ``summarize_encounters_for_context``：供 ``lived_context_bridge`` 复用的零写只读摘要，
   ``WITHDRAWN`` 材料绝不输出任何旧正文。
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

try:
    from components.life.scripts import v03_epistemic_encounters as engine
except ImportError:  # pragma: no cover - flat life/scripts import path
    import v03_epistemic_encounters as engine  # type: ignore[no-redef]

ROUTE_V03_ENCOUNTERS = "/api/phone/v03/encounters"
ENDPOINT_PATH = ROUTE_V03_ENCOUNTERS
VIEW_SCHEMA = engine.VIEW_SCHEMA

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "drop_shared_material",
    "withdraw_shared_material",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "record_first_glance",
    "revisit_with_question",
    "record_nuanced_recognition",
    "open_or_use_shared_material",
    "record_npc_errand_or_hearsay",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "record_first_glance": COMMON_COMMAND_FIELDS | frozenset({
        "target_ref",
        "target_title_cn",
        "seen_pages",
        "total_pages",
        "current_impression_cn",
        "shared",
    }),
    "revisit_with_question": COMMON_COMMAND_FIELDS | frozenset({
        "encounter_id",
        "target_ref",
        "newly_seen_pages",
        "active_question_cn",
        "noticed_detail_cn",
        "current_impression_cn",
    }),
    "record_nuanced_recognition": COMMON_COMMAND_FIELDS | frozenset({
        "encounter_id",
        "current_impression_cn",
        "partial_disagreement_kept",
        "tried_in_work_ref",
    }),
    "drop_shared_material": COMMON_COMMAND_FIELDS | frozenset({
        "title_cn",
        "excerpt_cn",
        "topic_tag",
        "page_count",
    }),
    "withdraw_shared_material": COMMON_COMMAND_FIELDS | frozenset({"material_id"}),
    "open_or_use_shared_material": COMMON_COMMAND_FIELDS | frozenset({
        "material_id",
        "mode",
        "opened_pages",
        "reaction_cn",
        "used_in_artifact_id",
    }),
    "record_npc_errand_or_hearsay": COMMON_COMMAND_FIELDS | frozenset({
        "npc_id",
        "npc_label_cn",
        "place_id",
        "kind",
        "summary_cn",
        "hearsay_source_cn",
        "verified_in_person",
        "verified_place_note",
        "subject_help_status",
    }),
}

_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "record_first_glance": frozenset({
        "target_ref",
        "target_title_cn",
        "seen_pages",
        "total_pages",
        "current_impression_cn",
    }),
    "revisit_with_question": frozenset({
        "newly_seen_pages",
        "active_question_cn",
        "noticed_detail_cn",
    }),
    "record_nuanced_recognition": frozenset({
        "encounter_id",
        "current_impression_cn",
        "partial_disagreement_kept",
    }),
    "drop_shared_material": frozenset({"title_cn", "excerpt_cn"}),
    "withdraw_shared_material": frozenset({"material_id"}),
    "open_or_use_shared_material": frozenset({"material_id", "mode"}),
    "record_npc_errand_or_hearsay": frozenset({
        "npc_id",
        "npc_label_cn",
        "place_id",
        "kind",
        "summary_cn",
    }),
}

_IDENTITY_KEYS: frozenset[str] = frozenset({
    "uid", "author_side", "by", "actor", "subject", "owner",
})

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "perspective",
    "revision",
    "observed_at",
    "encounters",
    "shared_materials",
    "npc_errands_and_hearsay",
    "attention_cues",
    "llm_calls",
)


def _error_response(status_code: int, reason_code: str, detail: str = "") -> tuple[int, dict[str, Any]]:
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
    raise engine.V03EncountersError(400, reason_code, detail or reason_code)


def _validate_scalar_fields(action: str, payload: Mapping[str, Any]) -> None:
    for key in ("operation_key", "intent_fingerprint"):
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip()):
            _fail("INVALID_FIELDS", f"{key} must be a trimmed non-empty string")

    if "shared" in payload and payload.get("shared") is not None:
        if not isinstance(payload["shared"], bool):
            _fail("INVALID_FIELDS", "shared must be a JSON boolean")

    for key in (
        "target_ref",
        "target_title_cn",
        "current_impression_cn",
        "active_question_cn",
        "noticed_detail_cn",
        "partial_disagreement_kept",
        "tried_in_work_ref",
        "title_cn",
        "excerpt_cn",
        "topic_tag",
        "material_id",
        "mode",
        "reaction_cn",
        "used_in_artifact_id",
        "npc_id",
        "npc_label_cn",
        "place_id",
        "kind",
        "summary_cn",
        "hearsay_source_cn",
        "verified_place_note",
        "subject_help_status",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            _fail("INVALID_FIELDS", f"{key} must be a string")
        if key not in ("tried_in_work_ref", "topic_tag", "reaction_cn", "hearsay_source_cn"):
            if not value.strip():
                _fail("INVALID_FIELDS", f"{key} must not be blank")

    if "total_pages" in payload and payload.get("total_pages") is not None:
        total = payload["total_pages"]
        if isinstance(total, bool) or not isinstance(total, int) or total < 1:
            _fail("INVALID_FIELDS", "total_pages must be an integer >= 1")

    if "page_count" in payload and payload.get("page_count") is not None:
        count = payload["page_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            _fail("INVALID_FIELDS", "page_count must be an integer >= 1")

    for key in ("seen_pages", "newly_seen_pages", "opened_pages"):
        if key not in payload or payload.get(key) is None:
            continue
        if not isinstance(payload[key], list):
            _fail("INVALID_FIELDS", f"{key} must be a list")
        for item in payload[key]:
            if isinstance(item, bool) or not isinstance(item, int):
                _fail("INVALID_FIELDS", f"{key} must contain integers")

    if "verified_in_person" in payload and payload.get("verified_in_person") is not None:
        if not isinstance(payload["verified_in_person"], bool):
            _fail("INVALID_FIELDS", "verified_in_person must be a JSON boolean")

    if action == "revisit_with_question":
        if payload.get("encounter_id") is None and payload.get("target_ref") is None:
            _fail("INVALID_FIELDS", "revisit_with_question needs encounter_id or target_ref")


def _validate_request_payload(payload: Any, *, actor_side: str) -> tuple[str, str]:
    if actor_side not in ("partner", "subject"):
        raise engine.V03PermissionError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping) or not payload:
        _fail("INVALID_FIELDS", "payload must be a non-empty JSON object")
    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            _fail("INVALID_FIELDS", "blank or padded key forbidden")

    identity = sorted(set(payload) & _IDENTITY_KEYS)
    if identity:
        raise engine.V03ConflictError(
            "CLIENT_IDENTITY_REFUSED", "client identity keys are refused: " + ", ".join(identity)
        )

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip() or action != action.strip():
        _fail("INVALID_FIELDS", "Missing required field 'action'")
    action = action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        _fail("INVALID_FIELDS", f"Unknown action: {action!r}")

    unknown = sorted(set(payload) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        _fail("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    missing = sorted(key for key in _REQUIRED_FIELDS[action] if payload.get(key) is None)
    if missing:
        _fail("INVALID_FIELDS", f"Missing fields for {action}: {missing}")

    # Partner door is CSRF-only; every other action needs a verified subject capability.
    if action in PARTNER_ALLOWED_ACTIONS:
        effective_actor = "partner"
    else:
        if actor_side != "subject":
            raise engine.V03PermissionError(
                "FORBIDDEN_ACTOR_SIDE", f"Action {action!r} requires the subject side"
            )
        effective_actor = "subject"

    _validate_scalar_fields(action, payload)
    return action, effective_actor


def _compose_envelope(projected: Mapping[str, Any], *, read_only: bool, command_status: str = "READ") -> dict[str, Any]:
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
        "perspective": data_block.get("perspective"),
        "llm_calls": 0,
        "data": data_block,
    }
    return envelope


def read_v03_encounters_view(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """读取有限视野、递材料与听说边界投影（100% 零磁盘写入）。"""

    try:
        projected = engine.project_v03_encounters(
            root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except engine.V03EncountersError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v03_encounters_view(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """执行一次遇见/材料/听说写动作（白名单闸门 + 权限隔离 + 幂等重放）。"""

    try:
        action, effective_actor = _validate_request_payload(payload, actor_side=actor_side)
        projected, cmd_status = engine.apply_encounters_mutation(
            root,
            dict(payload),
            actor_side=effective_actor,
            now_iso=now_iso,
        )
        envelope = _compose_envelope(projected, read_only=False, command_status=cmd_status)
        envelope["action"] = action
        return 200, envelope
    except engine.V03EncountersError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def summarize_encounters_for_context(
    root: Path | str,
    *,
    include_private: bool = False,
) -> dict[str, Any]:
    """零写只读摘要，供聊天背景桥接；``WITHDRAWN`` 材料绝不出词。"""

    summary: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "perspective": "subject" if include_private else "partner",
        "state_exists": False,
        "encounters": [],
        "shared_materials": [],
        "npc_entries": [],
        "llm_calls": 0,
    }
    try:
        projected = engine.project_v03_encounters(
            root, has_subject_capability=bool(include_private)
        )
    except Exception:  # noqa: BLE001 - a broken state is an empty, honest summary
        return summary

    summary["state_exists"] = bool(projected.get("state_exists"))
    for row in (projected.get("encounters") or [])[:3]:
        if not isinstance(row, Mapping):
            continue
        summary["encounters"].append({
            "encounter_id": row.get("encounter_id"),
            "target_title_cn": row.get("target_title_cn"),
            "stage": row.get("stage"),
            "seen_pages": list(row.get("seen_pages") or []),
            "total_pages": row.get("total_pages"),
            "current_impression_cn": row.get("current_impression_cn"),
            "active_question_cn": row.get("active_question_cn"),
            "noticed_detail_cn": row.get("noticed_detail_cn"),
            "partial_disagreement_kept": row.get("partial_disagreement_kept"),
            "shared": bool(row.get("shared") is True),
        })

    for row in (projected.get("shared_materials") or [])[:3]:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "") == engine.MATERIAL_WITHDRAWN:
            # Never mention a withdrawn material, not even its title.
            continue
        summary["shared_materials"].append({
            "material_id": row.get("material_id"),
            "title_cn": row.get("title_cn"),
            "status": row.get("status"),
            "page_count": row.get("page_count"),
            "opened_pages": list(row.get("opened_pages") or []),
        })

    for row in (projected.get("npc_errands_and_hearsay") or [])[:3]:
        if not isinstance(row, Mapping):
            continue
        summary["npc_entries"].append({
            "npc_label_cn": row.get("npc_label_cn"),
            "place_id": row.get("place_id"),
            "kind": row.get("kind"),
            "summary_cn": row.get("summary_cn"),
            "hearsay_source_cn": row.get("hearsay_source_cn"),
            "verified_in_person": bool(row.get("verified_in_person") is True),
            "subject_help_status": row.get("subject_help_status"),
        })
    return summary


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "ROUTE_V03_ENCOUNTERS",
    "SUBJECT_ALLOWED_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v03_encounters_view",
    "read_v03_encounters_view",
    "summarize_encounters_for_context",
]
