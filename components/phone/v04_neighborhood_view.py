#!/usr/bin/env python3
"""HTTP 视图与权限闸门封装：``/api/phone/v04/neighborhood``（v0.4 街区院子·手作守恒·书店公共生活）。

职责边界：
1. ``read_v04_neighborhood_view``：
   - 100% 零磁盘写入（零新建目录、零临时文件、零 mtime 变动）；
   - 按 ``has_subject_capability`` 严格区分 ``"her_private"`` 与 ``"partner_default"`` 视角：
     - ``partner_default`` 隐藏未公开私人卡片正文（``inscribed_text=None, withheld=True``）、
       全部私人草稿（``draft_text=None``）及未共享的地点小册（``withheld=True``）；
     - ``her_private`` 严格遵守前方封路非全知与公共告示未走近阅读不展开正文边界。
2. ``mutate_v04_neighborhood_view``：
   - 严格校验 ``ACTION_ALLOWED_FIELDS``，拒绝未知字段、空键及伪造身份/好感分数键（``400 INVALID_FIELDS``）；
   - 按 ``actor_side``（``"partner"`` vs ``"subject"``，兼容 ``"her"`` 归一为 ``"subject"``）校验权限，
     越权调用一律返回 ``403 FORBIDDEN_ACTOR_SIDE``；
   - 支持 ``operation_key`` 幂等重放（``status: "applied" | "already_applied"``）与 ``expected_revision`` CAS。
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

from components.world.scripts import v04_neighborhood_living as engine

V04_NEIGHBORHOOD_SCHEMA = "phone.v04.neighborhood.v1"
ENDPOINT_PATH = "/api/phone/v04/neighborhood"

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
})

# Forbidden identity, privilege, economy & mood/affection score keys (aligned with PhoneHTTPServer)
FORBIDDEN_SCORE_AND_MOOD_KEYS: frozenset[str] = frozenset({
    "mood_delta",
    "affection_score",
    "affection_delta",
    "favorability",
    "intimacy_score",
    "relationship_score",
    "obedience_score",
    "xp",
    "xp_points",
    "skill_level",
})

FORBIDDEN_FOREIGN_KEYS: frozenset[str] = FORBIDDEN_SCORE_AND_MOOD_KEYS | frozenset({
    "uid",
    "euid",
    "gid",
    "authority",
    "authorities",
    "main_self_verified",
    "confirm_apply",
    "home",
    "hermes_home",
    "world_home",
    "profile",
    "profile_home",
    "owner",
    "subject",
    "subject_id",
    "subject_ref",
    "price",
    "balance",
    "current_balance",
    "currency",
    "binding",
    "source",
    "actor",
    "actor_id",
    "actor_ref",
    "actor_side",
    "has_subject_capability",
    "verified_main_self",
    "grant_id",
    "workspace_id",
    "artifact_id",
    "adult_confirmed",
    "deploy_enabled",
    "phase_lock",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    # Partner + Subject shared environment & neighborhood actions (6)
    "set_local_weather": COMMON_COMMAND_FIELDS | frozenset({
        "weather",
        "airflow",
        "season",
        "note",
    }),
    "water_or_move_plant": COMMON_COMMAND_FIELDS | frozenset({
        "plant_id",
        "operation",
        "op_kind",
        "op",
        "target_place",
        "target_place_id",
        "public_care_enabled",
        "water_amount",
        "note",
    }),
    "hang_or_collect_cloth": COMMON_COMMAND_FIELDS | frozenset({
        "item_id",
        "cloth_id",
        "name",
        "label",
        "operation",
        "op_kind",
        "op",
        "target_support",
        "spot_id",
        "moisture",
        "note",
    }),
    "post_public_notice": COMMON_COMMAND_FIELDS | frozenset({
        "notice_id",
        "title",
        "body",
        "author_name",
        "valid_hours",
        "expires_at",
        "related_route_id",
        "roadblock_edge",
        "roadblock_claim",
    }),
    "set_roadblock": COMMON_COMMAND_FIELDS | frozenset({
        "route_id",
        "blocked",
        "reason",
    }),
    "shared_shelf_loan": COMMON_COMMAND_FIELDS | frozenset({
        "item_id",
        "tool_id",
        "operation",
        "op_kind",
        "op",
        "loan_hours",
        "extra_hours",
        "extension_hours",
        "reason",
        "note",
    }),
    # Subject-exclusive embodied journey, craft, bookstore & booklet actions (+6 = 12)
    "start_journey": COMMON_COMMAND_FIELDS | frozenset({
        "target_place",
        "destination",
        "route_id",
        "preferred_branch",
        "carry_item_ids",
        "note",
    }),
    "advance_or_turnback_journey": COMMON_COMMAND_FIELDS | frozenset({
        "operation",
        "return_to_place",
        "note",
    }),
    "read_public_notice": COMMON_COMMAND_FIELDS | frozenset({
        "notice_id",
    }),
    "craft_paper_or_box": COMMON_COMMAND_FIELDS | frozenset({
        "operation",
        "units",
        "scrap_loss",
        "card_id",
        "title",
        "text",
        "draft_text",
        "shared_or_displayed",
        "target_step",
        "fit_note",
        "patch_note",
        "source_artifact_id",
        "work_ref",
    }),
    "bookstore_service": COMMON_COMMAND_FIELDS | frozenset({
        "service",
        "operation",
        "book_id",
        "hold_hours",
        "expires_at",
        "loan_days",
        "via_return_box",
        "return_location",
        "card_id",
        "source_artifact_id",
        "work_ref",
        "note",
    }),
    "update_place_booklet": COMMON_COMMAND_FIELDS | frozenset({
        "operation",
        "place_id",
        "quietness_band",
        "impression",
        "note",
        "next_step_hint",
        "title",
        "purpose",
        "shared_with_partner",
        "pause",
        "source_artifact_id",
        "work_ref",
    }),
}

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "set_local_weather",
    "water_or_move_plant",
    "hang_or_collect_cloth",
    "post_public_notice",
    "set_roadblock",
    "shared_shelf_loan",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "start_journey",
    "advance_or_turnback_journey",
    "read_public_notice",
    "craft_paper_or_box",
    "bookstore_service",
    "update_place_booklet",
})


def _error_response(
    status_code: int,
    reason_code: str,
    *,
    detail: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code)
    status_tag = (
        "not_found"
        if status_code == 404
        else ("unavailable" if status_code >= 500 else "rejected")
    )
    body: dict[str, Any] = {
        "ok": False,
        "status": status_tag,
        "command_status": "REJECTED",
        "schema": V04_NEIGHBORHOOD_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "reason_code": code_str,
        "error_code": code_str,
        "detail": str(detail or code_str),
        "llm_calls": 0,
        "data": {},
    }
    if extra:
        body.update(dict(extra))
    return int(status_code), body


def _scan_payload_keys_recursive(
    node: Any, *, depth: int = 0
) -> tuple[list[str], bool]:
    """Scan nested structures for blank/non-string keys, excessive depth, or forbidden keys."""
    if depth > 6:
        return ["<max_depth_exceeded>"], False
    bad_keys: list[str] = []
    mood_smuggle = False
    if isinstance(node, Mapping):
        for raw_k, raw_v in node.items():
            if not isinstance(raw_k, str) or not raw_k.strip() or raw_k != raw_k.strip():
                bad_keys.append(str(raw_k))
                continue
            low_k = raw_k.lower()
            if low_k in FORBIDDEN_SCORE_AND_MOOD_KEYS:
                mood_smuggle = True
                bad_keys.append(raw_k)
                continue
            if raw_k in FORBIDDEN_FOREIGN_KEYS or low_k in FORBIDDEN_FOREIGN_KEYS:
                bad_keys.append(raw_k)
                continue
            sub_bad, sub_mood = _scan_payload_keys_recursive(raw_v, depth=depth + 1)
            bad_keys.extend(sub_bad)
            mood_smuggle = mood_smuggle or sub_mood
    elif isinstance(node, (list, tuple)):
        for item in node:
            sub_bad, sub_mood = _scan_payload_keys_recursive(item, depth=depth + 1)
            bad_keys.extend(sub_bad)
            mood_smuggle = mood_smuggle or sub_mood
    return bad_keys, mood_smuggle


def _normalize_actor_side(actor_side: Any) -> Optional[str]:
    if not isinstance(actor_side, str):
        return None
    cleaned = actor_side.strip().lower()
    if cleaned in ("subject", "her"):
        return "subject"
    if cleaned == "partner":
        return "partner"
    return None


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    status_label: str,
    read_only: bool,
    extra_top: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    data_block = copy.deepcopy(dict(projected["data"]))
    data_block["read_only"] = bool(read_only)
    data_block.setdefault("schema", V04_NEIGHBORHOOD_SCHEMA)
    data_block.setdefault("revision", int(projected["revision"]))
    data_block.setdefault("perspective", projected["perspective"])
    data_block.setdefault("character", copy.deepcopy(projected["character"]))

    envelope: dict[str, Any] = {
        "ok": True,
        "status": status_label,
        "reason_code": None,
        "schema": V04_NEIGHBORHOOD_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "read_only": bool(read_only),
        "perspective": projected["perspective"],
        "view": projected["perspective"],
        "has_subject_capability": bool(projected["has_subject_capability"]),
        "revision": int(projected["revision"]),
        "observed_at": projected["observed_at"],
        "llm_calls": 0,
        "character": copy.deepcopy(projected["character"]),
    }

    # Mirror all core data keys at top-level for ergonomic access by PhoneHTTPServer, desktop & tests
    for key, val in data_block.items():
        if key not in ("read_only",):
            envelope[key] = copy.deepcopy(val)

    if extra_top:
        for key, val in extra_top.items():
            envelope[key] = copy.deepcopy(val)
            data_block[key] = copy.deepcopy(val)

    envelope["data"] = data_block
    return envelope


def read_v04_neighborhood_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Read v0.4 neighborhood view with 100% zero disk writes."""
    try:
        projected = engine.project_v04_neighborhood(
            Path(day_root),
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        envelope = _compose_envelope(
            projected,
            status_label="ok",
            read_only=True,
        )
        return 200, envelope
    except engine.V04NeighborhoodError as exc:
        return _error_response(
            exc.http_status, exc.reason_code, detail=exc.detail, extra=exc.extra
        )


def _normalize_command_aliases(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if action == "water_or_move_plant":
        if "operation" not in out:
            if "op_kind" in out:
                out["operation"] = out.pop("op_kind")
            elif "op" in out:
                out["operation"] = out.pop("op")
        else:
            out.pop("op_kind", None)
            out.pop("op", None)
        if "target_place" not in out and "target_place_id" in out:
            out["target_place"] = out.pop("target_place_id")
        else:
            out.pop("target_place_id", None)
    elif action == "hang_or_collect_cloth":
        if "item_id" not in out and "cloth_id" in out:
            out["item_id"] = out.pop("cloth_id")
        else:
            out.pop("cloth_id", None)
        if "name" not in out and "label" in out:
            out["name"] = out.pop("label")
        else:
            out.pop("label", None)
        if "target_support" not in out and "spot_id" in out:
            out["target_support"] = out.pop("spot_id")
        else:
            out.pop("spot_id", None)
        if "operation" not in out:
            if "op_kind" in out:
                out["operation"] = out.pop("op_kind")
            elif "op" in out:
                out["operation"] = out.pop("op")
        else:
            out.pop("op_kind", None)
            out.pop("op", None)
        raw_op = str(out.get("operation") or "").strip().lower()
        if raw_op == "wet_and_hang":
            out["operation"] = "wet"
            out.setdefault("target_support", "yard_line")
        elif raw_op == "move_spot":
            out["operation"] = "move"
        elif raw_op == "hang":
            out.setdefault("target_support", "yard_line")
    elif action == "post_public_notice":
        if "related_route_id" not in out and "roadblock_edge" in out:
            out["related_route_id"] = out.pop("roadblock_edge")
        else:
            out.pop("roadblock_edge", None)
    elif action == "shared_shelf_loan":
        if "item_id" not in out and "tool_id" in out:
            out["item_id"] = out.pop("tool_id")
        else:
            out.pop("tool_id", None)
        if "operation" not in out:
            if "op_kind" in out:
                out["operation"] = out.pop("op_kind")
            elif "op" in out:
                out["operation"] = out.pop("op")
        else:
            out.pop("op_kind", None)
            out.pop("op", None)
        if "extra_hours" not in out and "extension_hours" in out:
            out["extra_hours"] = out.pop("extension_hours")
        else:
            out.pop("extension_hours", None)
    return out


def mutate_v04_neighborhood_view(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Validate strict field whitelist & actor_side gate, then execute one v0.4 command."""
    if not isinstance(payload, Mapping) or len(payload) == 0:
        return _error_response(
            400,
            "INVALID_FIELDS",
            detail="Request payload must be a non-empty JSON object",
            extra={"invalid_fields": ["<root>"]},
        )

    bad_nested, mood_smuggle = _scan_payload_keys_recursive(payload)
    if bad_nested:
        meta: dict[str, Any] = {"invalid_fields": sorted(set(bad_nested))}
        if mood_smuggle:
            meta["mood_delta_forbidden"] = True
        return _error_response(
            400,
            "INVALID_FIELDS",
            detail=f"Forbidden or malformed fields: {sorted(set(bad_nested))}",
            extra=meta,
        )

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        return _error_response(
            400,
            "INVALID_FIELDS",
            detail="Field 'action' must be a non-empty trimmed string",
            extra={"invalid_fields": ["action"]},
        )
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        return _error_response(
            400,
            "INVALID_FIELDS",
            detail=f"Unknown action {action!r}",
            extra={"action": action, "invalid_fields": ["action"]},
        )

    allowed_keys = ACTION_ALLOWED_FIELDS[action]
    invalid_fields: list[str] = []
    for key, val in payload.items():
        if key not in allowed_keys:
            invalid_fields.append(key)
            continue
        if val is None or (isinstance(val, str) and not val.strip()) or val == {}:
            invalid_fields.append(key)

    if invalid_fields:
        return _error_response(
            400,
            "INVALID_FIELDS",
            detail=f"Invalid or unknown fields for {action}: {sorted(set(invalid_fields))}",
            extra={"invalid_fields": sorted(set(invalid_fields))},
        )

    norm_actor = _normalize_actor_side(actor_side)
    if norm_actor is None:
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            detail=f"Invalid actor_side: {actor_side!r}",
            extra={"actor_side": actor_side, "action": action},
        )
    if norm_actor == "partner" and action not in PARTNER_ALLOWED_ACTIONS:
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            detail=f"actor_side='partner' cannot execute {action!r}",
            extra={"actor_side": "partner", "action": action},
        )
    if norm_actor == "subject" and action not in SUBJECT_ALLOWED_ACTIONS:
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            detail=f"actor_side='subject' cannot execute {action!r}",
            extra={"actor_side": "subject", "action": action},
        )

    try:
        norm_payload = _normalize_command_aliases(action, payload)
        status_label, action_result, projected = engine.apply_v04_neighborhood_command(
            Path(day_root),
            norm_payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
        envelope = _compose_envelope(
            projected,
            status_label=status_label,
            read_only=False,
            extra_top={
                "action": action,
                "actor_side": norm_actor,
                "already_applied": status_label == "already_applied",
                "command_status": "DUPLICATE" if status_label == "already_applied" else "APPLIED",
                "result": action_result,
            },
        )
        return 200, envelope
    except engine.V04NeighborhoodError as exc:
        return _error_response(
            exc.http_status, exc.reason_code, detail=exc.detail, extra=exc.extra
        )


def v04_neighborhood_state_path(day_root: Path | str) -> Path:
    """Return canonical state file path ``<day_root>/world/v04_neighborhood_state.json``."""
    return engine.state_file_path(day_root)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "FORBIDDEN_FOREIGN_KEYS",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "V04_NEIGHBORHOOD_SCHEMA",
    "mutate_v04_neighborhood_view",
    "read_v04_neighborhood_view",
    "v04_neighborhood_state_path",
]
