#!/usr/bin/env python3
"""HTTP 视图与动作闸门封装：``/api/phone/v04/builder-trylive``（第 44 章世界编辑诊断与试住一小时沙盒）。

职责与边界：
1. ``read_v04_builder_trylive_view``：
   - 100% 零磁盘写入（零新建目录、零临时文件、零 mtime 变化）；
   - 同时对齐后端契约键（``route_audit``、``preset_scenarios``、``last_rehearsal``、
     ``last_scene_gap_check``、``last_trylive_run``、``last_impact_preview``）与桌面端锁定键
     （``revision``、``identity``、``sandbox_isolated``、``presets``、``inspectable_objects``、
     ``last_route_audit``、``last_prose_check``、``last_trylive_run``、``last_why_changed``、
     ``last_modification_preview``）。
2. ``mutate_v04_builder_trylive_view``：
   - 逐动作严格执行 ``ACTION_ALLOWED_FIELDS`` 白名单校验，未知字段、空键名或伪造身份/外籍计分字段
     一律返回 ``(400, {"ok": False, ..., "reason_code": "INVALID_FIELDS"})``；
   - ``actor_side`` 仅允许 ``"partner"`` 与 ``"subject"``，其余非法值一律返回
     ``(403, {"ok": False, ..., "reason_code": "FORBIDDEN_ACTOR_SIDE"})``；
   - 支持 ``operation_key`` 幂等重放与 ``expected_revision`` 并发校验；
   - 仅允许原子写入隔离沙盒报告 ``<day_root>/phone/v04_builder_sandbox.json``（``0600`` 权限），
     绝不触碰 ``<day_root>/world/`` 或正式角色生活记忆。
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v04_world_builder_trylive as trylive_engine

V04_BUILDER_TRYLIVE_SCHEMA = "phone.v04.builder_trylive.v1"
VIEW_SCHEMA = V04_BUILDER_TRYLIVE_SCHEMA
ENDPOINT_PATH = "/api/phone/v04/builder-trylive"

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
    "pronoun",
})

ALLOWED_PRONOUNS: frozenset[str] = frozenset({"她", "他", "它", "TA"})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "audit_routes_or_rehearse": COMMON_COMMAND_FIELDS | frozenset({
        "route_id",
        "rehearse_route_id",
        "blocked_edges",
        "start_place_id",
        "destination_place_id",
        "route_path",
        "inject_roadblock_route",
        "on_roadblock",
        "custom_places",
        "custom_routes",
        "object_templates",
        "available_keys",
    }),
    "check_scene_prose_gaps": COMMON_COMMAND_FIELDS | frozenset({
        "prose",
        "scene_prose",
        "text",
        "place_id",
        "place_override",
    }),
    "run_trylive_hour": COMMON_COMMAND_FIELDS | frozenset({
        "preset_id",
        "route_preset",
        "scenario_id",
        "injections",
        "inject_faults",
        "rain",
        "seat_taken",
        "seat_occupied",
        "roadblock",
        "restart_gap",
        "compare_lighting",
    }),
    "inspect_why_changed": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "target_kind",
        "target_id",
    }),
    "preview_world_modification": COMMON_COMMAND_FIELDS | frozenset({
        "mod_kind",
        "target_id",
        "to_place_id",
        "resolution_mode",
        "simulate_occupied_by",
        "simulate_items_on_surface",
        "simulate_bound_agreement",
        "worldpack_diff",
    }),
}

ALLOWED_ACTOR_SIDES: frozenset[str] = frozenset({"partner", "subject"})

# Optional string fields that UI forms may legitimately submit as "" when left blank
_OPTIONAL_BLANKABLE_STR_FIELDS: frozenset[str] = frozenset({
    "route_id",
    "rehearse_route_id",
    "place_id",
    "target_id",
    "to_place_id",
    "operation_key",
    "intent_fingerprint",
    "pronoun",
})


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
        "schema": V04_BUILDER_TRYLIVE_SCHEMA,
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
    """Reject non-string/blank/whitespace-padded keys, excessive nesting, or forbidden foreign keys."""
    if depth > 6:
        raise trylive_engine.V04ValidationError("INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise trylive_engine.V04ValidationError(
                    "INVALID_FIELDS",
                    "Empty or whitespace-padded field name is forbidden",
                )
            if raw_key in trylive_engine.FORBIDDEN_FOREIGN_KEYS or raw_key.lower() in trylive_engine.FORBIDDEN_FOREIGN_KEYS:
                raise trylive_engine.V04ValidationError(
                    "INVALID_FIELDS",
                    f"Forbidden foreign or identity field: {raw_key!r}",
                    details={"invalid_fields": [raw_key]},
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _validate_request_payload(payload: Any) -> str:
    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise trylive_engine.V04ValidationError(
            "INVALID_FIELDS",
            "Request payload must be a non-empty JSON object",
            details={"invalid_fields": ["<root>"]},
        )
    _scan_payload_keys_recursive(payload)

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        raise trylive_engine.V04ValidationError(
            "INVALID_FIELDS",
            "Field 'action' must be a non-empty trimmed string",
            details={"invalid_fields": ["action"]},
        )
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        raise trylive_engine.V04ValidationError(
            "INVALID_FIELDS",
            f"Unknown action: {action!r}",
            details={"invalid_fields": ["action"], "action": action},
        )

    allowed_keys = ACTION_ALLOWED_FIELDS[action]
    unknown_keys = sorted(set(payload.keys()) - allowed_keys)
    if unknown_keys:
        raise trylive_engine.V04ValidationError(
            "INVALID_FIELDS",
            f"Unknown fields for {action}: {unknown_keys}",
            details={"invalid_fields": unknown_keys},
        )

    bad_fields: list[str] = []
    for key, val in payload.items():
        if key == "action":
            continue
        if val is None:
            if key in _OPTIONAL_BLANKABLE_STR_FIELDS:
                continue
            bad_fields.append(key)
        elif isinstance(val, str) and not val.strip():
            if key in _OPTIONAL_BLANKABLE_STR_FIELDS:
                continue
            bad_fields.append(key)

    if bad_fields:
        raise trylive_engine.V04ValidationError(
            "INVALID_FIELDS",
            f"Blank or null fields are not allowed: {sorted(bad_fields)}",
            details={"invalid_fields": sorted(bad_fields)},
        )

    if "pronoun" in payload and payload["pronoun"] is not None:
        raw_pron = payload["pronoun"]
        if not isinstance(raw_pron, str) or (raw_pron.strip() and raw_pron.strip() not in ALLOWED_PRONOUNS):
            raise trylive_engine.V04ValidationError(
                "INVALID_FIELDS",
                f"Invalid pronoun: {raw_pron!r}; allowed: {sorted(ALLOWED_PRONOUNS)}",
                details={"invalid_fields": ["pronoun"]},
            )

    return action


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    read_only: bool,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    mirrored_keys = (
        "route_audit",
        "preset_scenarios",
        "presets",
        "inspectable_objects",
        "last_rehearsal",
        "last_route_audit",
        "last_scene_gap_check",
        "last_prose_check",
        "last_trylive_run",
        "last_why_changed",
        "last_impact_preview",
        "last_modification_preview",
    )

    data_block: dict[str, Any] = {
        "schema": V04_BUILDER_TRYLIVE_SCHEMA,
        "read_only": bool(read_only),
        "perspective": projected["perspective"],
        "revision": int(projected["revision"]),
        "observed_at": projected["observed_at"],
        "identity": copy.deepcopy(projected["identity"]),
        "sandbox_isolated": True,
        "llm_calls": 0,
    }
    for k in mirrored_keys:
        data_block[k] = copy.deepcopy(projected.get(k))

    if extra_fields:
        for k, v in extra_fields.items():
            data_block[k] = copy.deepcopy(v)

    envelope: dict[str, Any] = {
        "ok": True,
        "status": "ok",
        "reason_code": None,
        "schema": V04_BUILDER_TRYLIVE_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "read_only": bool(read_only),
        "perspective": projected["perspective"],
        "revision": int(projected["revision"]),
        "observed_at": projected["observed_at"],
        "identity": copy.deepcopy(projected["identity"]),
        "sandbox_isolated": True,
        "llm_calls": 0,
    }
    for k in mirrored_keys:
        envelope[k] = copy.deepcopy(projected.get(k))

    if extra_fields:
        for k, v in extra_fields.items():
            envelope[k] = copy.deepcopy(v)

    envelope["data"] = data_block
    return envelope


def read_v04_builder_trylive_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Pure 100% zero-write GET handler for ``/api/phone/v04/builder-trylive``."""
    try:
        projected = trylive_engine.project_v04_builder_trylive(
            Path(day_root),
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=True)
    except trylive_engine.V04BuilderError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, extra=exc.details)


def mutate_v04_builder_trylive_view(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Guarded mutation handler for ``/api/phone/v04/builder-trylive`` (writes ONLY isolated sandbox JSON)."""
    try:
        action = _validate_request_payload(payload)
    except trylive_engine.V04BuilderError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, extra=exc.details)

    if not isinstance(actor_side, str) or actor_side.strip() not in ALLOWED_ACTOR_SIDES:
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Unsupported actor_side: {actor_side!r}",
            extra={"actor_side": actor_side, "action": action},
        )
    norm_actor = actor_side.strip()

    try:
        applied = trylive_engine.apply_v04_builder_trylive_command(
            Path(day_root),
            payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
        envelope = _compose_envelope(
            applied["projected"],
            read_only=False,
            extra_fields={
                "command_status": applied["command_status"],
                "already_applied": bool(applied["already_applied"]),
                "action": applied["action"],
                "actor_side": norm_actor,
                "result": applied["result"],
            },
        )
        return 200, envelope
    except trylive_engine.V04BuilderError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, extra=exc.details)


def v04_builder_sandbox_state_path(day_root: Path | str) -> Path:
    """Return canonical isolated sandbox report path ``<day_root>/phone/v04_builder_sandbox.json``."""
    return trylive_engine.builder_sandbox_state_path(day_root)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "ALLOWED_ACTOR_SIDES",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "V04_BUILDER_TRYLIVE_SCHEMA",
    "VIEW_SCHEMA",
    "mutate_v04_builder_trylive_view",
    "read_v04_builder_trylive_view",
    "v04_builder_sandbox_state_path",
]
