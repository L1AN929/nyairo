#!/usr/bin/env python3
"""HTTP 视图与权限封装：``/api/phone/v03/embodied-social``。

提供给 ``PhoneHTTPServer``、桌面端与聊天组装层调用的两个标准入口：
- ``read_v03_embodied_social_view(day_root, *, has_subject_capability=False, now_iso=None)``
  - 100% 零磁盘写入（目录/状态文件不存在时绝不创建任何文件或目录）；
  - ``partner_default`` 视角严格剥离主体未公开的私人街坊感言与未公开风格迁移草稿。
- ``mutate_v03_embodied_social_view(day_root, payload, *, actor_side, now_iso=None)``
  - 严格字段白名单（未知字段、空键或外籍越权键一律 ``400 INVALID_FIELDS``）；
  - ``actor_side == "partner"`` 仅允许 ``set_partner_presence``、``propose_contact``、
    ``release_contact``、``restock_pantry``，其余主体专属动作一律返回 ``403 FORBIDDEN_ACTOR_SIDE``；
  - 打通 ``somatic_comfort_view`` 饮品消费回执闭环（当 ``consume_dish`` 消费茶饮且存在待饮温饮槽时同步核销）。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
import sys
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
if str(_WORLD_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_WORLD_SCRIPTS))

import v03_embodied_npc_ecology as ecology  # noqa: E402
from . import service as phone_service  # noqa: E402
from . import somatic_comfort_view as somatic_view  # noqa: E402
from . import stance_containment_view as stance_view  # noqa: E402

VIEW_SCHEMA = "phone.v03.embodied_social.v1"
ENDPOINT_PATH = "/api/phone/v03/embodied-social"
CoPresenceAdapter = ecology.CoPresenceAdapter

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
        "set_partner_presence",
        "exit_copresence",
        "tidy_bed",
        "propose_contact",
        "respond_contact",
        "release_contact",
        "restock_pantry",
        "wash_dishes",
        "inspect_kitchen_zone",
        "feed_stray_cat",
    }
)

FORBIDDEN_FOREIGN_KEYS: frozenset[str] = frozenset(
    {
        "subject_id",
        "subject_ref",
        "actor_id",
        "actor_ref",
        "actor",
        "actor_side",
        "has_subject_capability",
        "affection_score",
        "relationship_score",
        "intimacy_score",
        "xp",
        "xp_points",
        "skill_level",
        "phase_lock",
        "adult_confirmed",
        "deploy_enabled",
    }
)

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    # Module A: Kitchen 6 zones, appliances, pantry & cooked dish lifecycle
    "restock_pantry": frozenset(
        {
            "action",
            "ingredient_id",
            "label",
            "category",
            "unit_name",
            "units_added",
            "items",
            "operation_key",
            "expected_revision",
        }
    ),
    "cook_or_brew": frozenset(
        {
            "action",
            "title",
            "serving_kind",
            "container_id",
            "ingredients_used",
            "portions",
            "operation_key",
            "expected_revision",
        }
    ),
    "refrigerate_dish": frozenset(
        {
            "action",
            "dish_id",
            "operation_key",
            "expected_revision",
        }
    ),
    "reheat_dish": frozenset(
        {
            "action",
            "dish_id",
            "operation_key",
            "expected_revision",
        }
    ),
    "consume_dish": frozenset(
        {
            "action",
            "dish_id",
            "sync_somatic_drink",
            "operation_key",
            "expected_revision",
        }
    ),
    "discard_dish": frozenset(
        {
            "action",
            "dish_id",
            "reason",
            "operation_key",
            "expected_revision",
        }
    ),
    "wash_dishes": frozenset(
        {
            "action",
            "operation_key",
            "expected_revision",
        }
    ),
    "inspect_kitchen_zone": frozenset(
        {
            "action",
            "zone_id",
            "operation_key",
            "expected_revision",
        }
    ),
    "set_snack_drawer_permission": frozenset(
        {
            "action",
            "partner_authorized",
            "operation_key",
            "expected_revision",
        }
    ),
    "operate_kitchen_appliance": frozenset(
        {
            "action",
            "appliance_id",
            "operation",
            "operation_key",
            "expected_revision",
        }
    ),
    # Module B: Partner co-presence, UserAvatarProxy, bed physics, hand-slot interlock & contact
    "set_partner_presence": frozenset(
        {
            "action",
            "presence_mode",
            "pose",
            "ttl_seconds",
            "expires_at",
            "subject_foreground_activity",
            "operation_key",
            "expected_revision",
        }
    ),
    "exit_copresence": frozenset(
        {
            "action",
            "operation_key",
            "expected_revision",
        }
    ),
    "tidy_bed": frozenset(
        {
            "action",
            "operation_key",
            "expected_revision",
        }
    ),
    "set_hand_slot": frozenset(
        {
            "action",
            "slot",
            "object_id",
            "label",
            "surface_id",
            "requires_two_hands",
            "operation_key",
            "expected_revision",
        }
    ),
    "set_subject_contact_posture": frozenset(
        {
            "action",
            "acute_illness",
            "solitude_requested",
            "busy_decline",
            "note",
            "operation_key",
            "expected_revision",
        }
    ),
    "propose_contact": frozenset(
        {
            "action",
            "contact_kind",
            "operation_key",
            "expected_revision",
        }
    ),
    "respond_contact": frozenset(
        {
            "action",
            "decision",
            "proposal_id",
            "reason_note",
            "operation_key",
            "expected_revision",
        }
    ),
    "release_contact": frozenset(
        {
            "action",
            "reason",
            "operation_key",
            "expected_revision",
        }
    ),
    # Module C: Town neighbor NPCs, slow lifecycles, commitments, privacy firewall, hobby 3-axes & relationship anchors
    "record_npc_knowledge": frozenset(
        {
            "action",
            "npc_id",
            "source_type",
            "epistemic_basis",
            "hearsay_from",
            "hearsay_sources",
            "source_ref",
            "material_kind",
            "artifact_state",
            "visibility",
            "summary",
            "npc_quote",
            "subject_private_impression",
            "impression_shared",
            "require_open",
            "operation_key",
            "expected_revision",
        }
    ),
    "revoke_npc_shared_ref": frozenset(
        {
            "action",
            "source_ref",
            "fact_id",
            "npc_id",
            "operation_key",
            "expected_revision",
        }
    ),
    "feed_stray_cat": frozenset(
        {
            "action",
            "ingredient_id",
            "food_label",
            "operation_key",
            "expected_revision",
        }
    ),
    "propose_npc_commitment": frozenset(
        {
            "action",
            "npc_id",
            "location_id",
            "window_start_iso",
            "window_end_iso",
            "purpose",
            "operation_key",
            "expected_revision",
        }
    ),
    "resolve_npc_commitment": frozenset(
        {
            "action",
            "commitment_id",
            "outcome",
            "note",
            "operation_key",
            "expected_revision",
        }
    ),
    "submit_bookstore_card_feedback": frozenset(
        {
            "action",
            "npc_id",
            "book_ref",
            "card_note",
            "feedback_kind",
            "shared_on_board",
            "require_open",
            "operation_key",
            "expected_revision",
        }
    ),
    "record_style_migration": frozenset(
        {
            "action",
            "style_id",
            "title",
            "origin_context",
            "origin_ref",
            "stage",
            "capability",
            "stability",
            "enjoyment",
            "work_ref",
            "private_note",
            "public_summary",
            "shared",
            "operation_key",
            "expected_revision",
        }
    ),
    "record_relationship_anchor": frozenset(
        {
            "action",
            "target_id",
            "anchor_category",
            "domain",
            "trust_stance",
            "boundary_statement",
            "object_kind",
            "label",
            "story_note",
            "phrase",
            "meaning_context",
            "secret_summary",
            "shared_with_partner",
            "operation_key",
            "expected_revision",
        }
    ),
}

RESTOCK_ITEM_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"ingredient_id", "label", "category", "unit_name", "units_added"}
)
COOK_INGREDIENT_ITEM_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"ingredient_id", "units_used"}
)

MAX_OPERATION_KEY_LEN = 120
MAX_OPERATION_RECEIPTS = 64


def _error_response(http_status: int, reason_code: str, detail: str = "") -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code)
    status_tag = "not_found" if http_status == 404 else ("unavailable" if http_status >= 500 else "rejected")
    return int(http_status), {
        "ok": False,
        "status": status_tag,
        "reason_code": code_str,
        "error_code": code_str,
        "detail": str(detail or code_str),
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "data": {},
    }


def _resolve_now_iso(day_root: Path, now_iso: Optional[str]) -> str:
    if now_iso is not None:
        dt = ecology.parse_iso_instant(now_iso)
        return ecology.format_iso(dt)
    bday = phone_service.today_business_day(day_root)
    dt = ecology.parse_iso_instant(bday["logical_at"])
    return ecology.format_iso(dt)


def _normalize_actor_side(actor_side: Any) -> Optional[str]:
    if not isinstance(actor_side, str):
        return None
    cleaned = actor_side.strip()
    if cleaned in ("subject", "her"):
        return "subject"
    if cleaned == "partner":
        return "partner"
    return None


def _scan_keys_recursive(node: Any, *, depth: int = 0) -> None:
    """Reject empty/whitespace keys, non-string keys, excessive nesting, or forbidden foreign keys."""

    if depth > 6:
        raise ecology.EmbodiedEcologyError(
            "INVALID_FIELDS", "Payload nesting depth exceeded", http_status=400
        )
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise ecology.EmbodiedEcologyError(
                    "INVALID_FIELDS", "Empty or whitespace-padded key is forbidden", http_status=400
                )
            if raw_key in FORBIDDEN_FOREIGN_KEYS:
                raise ecology.EmbodiedEcologyError(
                    "INVALID_FIELDS",
                    f"Foreign or privilege key {raw_key!r} is forbidden",
                    http_status=400,
                )
            _scan_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_keys_recursive(item, depth=depth + 1)


def _validate_payload_whitelist(payload: Any) -> str:
    if not isinstance(payload, Mapping) or len(payload) == 0:
        raise ecology.EmbodiedEcologyError(
            "INVALID_FIELDS", "Request body must be a non-empty JSON object", http_status=400
        )
    _scan_keys_recursive(payload)

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip() or action != action.strip():
        raise ecology.EmbodiedEcologyError(
            "INVALID_FIELDS", "Field 'action' must be a non-empty string", http_status=400
        )
    if action not in ACTION_ALLOWED_FIELDS:
        raise ecology.EmbodiedEcologyError(
            "INVALID_FIELDS", f"Unknown action {action!r}", http_status=400
        )

    allowed = ACTION_ALLOWED_FIELDS[action]
    extra = sorted(set(payload.keys()) - allowed)
    if extra:
        raise ecology.EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"Unknown fields for {action}: {extra}",
            http_status=400,
        )

    # Nested whitelist checks
    if action == "restock_pantry" and payload.get("items") is not None:
        raw_items = payload["items"]
        if not isinstance(raw_items, list) or not raw_items:
            raise ecology.EmbodiedEcologyError(
                "INVALID_FIELDS", "items must be a non-empty list", http_status=400
            )
        for idx, entry in enumerate(raw_items):
            if not isinstance(entry, Mapping):
                raise ecology.EmbodiedEcologyError(
                    "INVALID_FIELDS", f"items[{idx}] must be an object", http_status=400
                )
            nested_extra = sorted(set(entry.keys()) - RESTOCK_ITEM_ALLOWED_FIELDS)
            if nested_extra:
                raise ecology.EmbodiedEcologyError(
                    "INVALID_FIELDS",
                    f"Unknown fields in items[{idx}]: {nested_extra}",
                    http_status=400,
                )

    if action == "cook_or_brew" and isinstance(payload.get("ingredients_used"), list):
        for idx, entry in enumerate(payload["ingredients_used"]):
            if not isinstance(entry, Mapping):
                raise ecology.EmbodiedEcologyError(
                    "INVALID_FIELDS", f"ingredients_used[{idx}] must be an object", http_status=400
                )
            nested_extra = sorted(set(entry.keys()) - COOK_INGREDIENT_ITEM_ALLOWED_FIELDS)
            if nested_extra:
                raise ecology.EmbodiedEcologyError(
                    "INVALID_FIELDS",
                    f"Unknown fields in ingredients_used[{idx}]: {nested_extra}",
                    http_status=400,
                )

    return action


def _somatic_drink_bridge_readonly(
    day_root: Path,
    consumption_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    somatic_state, somatic_reason = somatic_view.read_state(day_root)
    care_drink = (somatic_state.get("care") or {}).get("drink")
    latest_drink = next(
        (r for r in reversed(consumption_receipts) if r.get("serving_kind") == "drink"),
        None,
    )
    latest_meal = next(
        (r for r in reversed(consumption_receipts) if r.get("serving_kind") == "meal"),
        None,
    )
    return {
        "somatic_state_status": "available" if somatic_reason is None else "degraded",
        "somatic_care_drink_state": care_drink.get("state") if isinstance(care_drink, Mapping) else None,
        "somatic_care_receipt_ref": care_drink.get("receipt_ref") if isinstance(care_drink, Mapping) else None,
        "latest_drink_receipt_id": latest_drink["receipt_id"] if latest_drink else None,
        "latest_meal_receipt_id": latest_meal["receipt_id"] if latest_meal else None,
        "closes_somatic_drink_gap": True,
    }


def _sync_somatic_drink_on_consume(
    day_root: Path,
    *,
    receipt: Mapping[str, Any],
    operation_key: Optional[str],
    now_iso: str,
) -> bool:
    """When a cooked drink is consumed and somatic_comfort has an active drink slot, mark it consumed."""

    somatic_path = somatic_view.state_path(day_root)
    if somatic_path.is_symlink() or not somatic_path.is_file():
        return False
    somatic_state, somatic_reason = somatic_view.read_state(day_root)
    if somatic_reason is not None:
        return False
    care_drink = (somatic_state.get("care") or {}).get("drink")
    if not isinstance(care_drink, Mapping) or care_drink.get("state") not in ("available", "taken"):
        return False
    next_somatic, changed = somatic_view.mark_drink_result(
        somatic_state,
        outcome="consumed",
        receipt_ref=receipt["receipt_id"],
        operation_key=operation_key or receipt["receipt_id"],
        now_iso=now_iso,
    )
    if changed:
        somatic_view.write_state(day_root, next_somatic)
    return changed


def _compose_view_envelope(
    day_root: Path,
    *,
    projected: Mapping[str, Any],
    has_subject_capability: bool,
    status_label: str = "applied",
    extra_top: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    kitchen = copy.deepcopy(dict(projected["kitchen"]))
    kitchen["somatic_comfort_drink_bridge"] = _somatic_drink_bridge_readonly(
        day_root, list(kitchen.get("consumption_receipts") or [])
    )
    data_block: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "view": projected["view"],
        "has_subject_capability": bool(has_subject_capability),
        "observed_at": projected["observed_at"],
        "revision": projected["revision"],
        "character": copy.deepcopy(projected["character"]),
        "kitchen": kitchen,
        "embodied_contact": copy.deepcopy(projected["embodied_contact"]),
        "npc_ecology": copy.deepcopy(projected["npc_ecology"]),
        "town_npcs": copy.deepcopy(projected["town_npcs"]),
        "style_migration": copy.deepcopy(projected["style_migration"]),
        "relationship_anchors": copy.deepcopy(projected["relationship_anchors"]),
    }
    if extra_top:
        for k, v in extra_top.items():
            data_block[k] = copy.deepcopy(v)

    envelope: dict[str, Any] = {
        "ok": True,
        "status": status_label,
        "reason_code": None,
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "read_only": status_label == "read_only",
        "view": projected["view"],
        "has_subject_capability": bool(has_subject_capability),
        "observed_at": projected["observed_at"],
        "revision": projected["revision"],
        "character": copy.deepcopy(data_block["character"]),
        "kitchen": copy.deepcopy(kitchen),
        "embodied_contact": copy.deepcopy(data_block["embodied_contact"]),
        "npc_ecology": copy.deepcopy(data_block["npc_ecology"]),
        "town_npcs": copy.deepcopy(data_block["town_npcs"]),
        "style_migration": copy.deepcopy(data_block["style_migration"]),
        "relationship_anchors": copy.deepcopy(data_block["relationship_anchors"]),
    }
    if extra_top:
        for k, v in extra_top.items():
            envelope[k] = copy.deepcopy(v)
    envelope["data"] = data_block
    return envelope


def read_v03_embodied_social_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Pure 100% zero-write GET handler for ``/api/phone/v03/embodied-social``."""

    root = Path(day_root)
    try:
        resolved_now = _resolve_now_iso(root, now_iso)
        projected = ecology.project_embodied_social_state(
            root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=resolved_now,
        )
        body = _compose_view_envelope(
            root,
            projected=projected,
            has_subject_capability=bool(has_subject_capability),
            status_label="applied",
        )
        body["read_only"] = True
        body["data"]["read_only"] = True
        return 200, body
    except ecology.EmbodiedEcologyError as exc:
        return _error_response(exc.http_status, exc.reason_code, exc.detail)


def mutate_v03_embodied_social_view(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Guarded mutation handler for ``/api/phone/v03/embodied-social``."""

    root = Path(day_root)
    try:
        action = _validate_payload_whitelist(payload)
        normalized_actor = _normalize_actor_side(actor_side)
        if normalized_actor is None:
            return _error_response(
                403, "FORBIDDEN_ACTOR_SIDE", f"Invalid actor_side: {actor_side!r}"
            )
        if normalized_actor == "partner" and action not in PARTNER_ALLOWED_ACTIONS:
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                f"actor_side='partner' is only allowed {sorted(PARTNER_ALLOWED_ACTIONS)}, not {action!r}",
            )
        if (
            normalized_actor == "partner"
            and action == "set_partner_presence"
            and payload.get("subject_foreground_activity") is not None
        ):
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                "partner cannot overwrite subject_foreground_activity",
            )

        resolved_now = _resolve_now_iso(root, now_iso)
        state = ecology.load_state_readonly(root)

        # Optional operation_key idempotency check
        op_key_raw = payload.get("operation_key")
        op_key: Optional[str] = None
        if op_key_raw is not None:
            if (
                not isinstance(op_key_raw, str)
                or not op_key_raw.strip()
                or len(op_key_raw.strip()) > MAX_OPERATION_KEY_LEN
            ):
                return _error_response(400, "INVALID_FIELDS", "Invalid operation_key")
            op_key = op_key_raw.strip()

        intent_for_hash = {
            k: v
            for k, v in payload.items()
            if k not in ("expected_revision",)
        }
        intent_fp = ecology._canonical_digest({"actor_side": normalized_actor, "payload": intent_for_hash})

        if op_key is not None and op_key in state["operation_receipts"]:
            prev_rec = state["operation_receipts"][op_key]
            if prev_rec.get("intent_fingerprint") != intent_fp:
                return _error_response(
                    409,
                    "OPERATION_KEY_CONFLICT",
                    "Same operation_key reused with a different request payload",
                )
            has_subj = normalized_actor == "subject"
            projected = ecology.project_embodied_social_state(
                root,
                state=state,
                has_subject_capability=has_subj,
                now_iso=resolved_now,
            )
            return 200, _compose_view_envelope(
                root,
                projected=projected,
                has_subject_capability=has_subj,
                status_label="already_applied",
                extra_top={
                    "action": action,
                    "actor_side": normalized_actor,
                    "already_applied": True,
                    "result": prev_rec.get("result", {}),
                },
            )

        # P0 RBAC: partner may call respond_contact ONLY when pending_initiator == "subject"
        if normalized_actor == "partner" and action == "respond_contact":
            derived_before = ecology.derive_contact_avatar_and_bed_readonly(
                state["embodied_contact"], ecology.parse_iso_instant(resolved_now)
            )
            pending_before = derived_before.get("pending_proposal")
            if not isinstance(pending_before, Mapping) or pending_before.get("proposed_by") != "subject":
                return _error_response(
                    403,
                    "FORBIDDEN_ACTOR_SIDE",
                    "partner may only call respond_contact when pending_initiator == 'subject' (cannot self-propose and self-accept)",
                )

        expected_rev = payload.get("expected_revision")
        if expected_rev is not None:
            if isinstance(expected_rev, bool) or not isinstance(expected_rev, int) or expected_rev < 0:
                return _error_response(400, "INVALID_FIELDS", "expected_revision must be a non-negative integer")
            if int(state["revision"]) != int(expected_rev):
                return _error_response(
                    409,
                    "REVISION_CONFLICT",
                    f"Expected revision {expected_rev}, current revision is {state['revision']}",
                )

        settled_info = ecology.settle_elapsed_contact_and_avatar(state, now_iso=resolved_now)
        if settled_info.get("avatar_expired_settled"):
            stance_view.cascade_pause_adult_scene_permit(
                root, now_iso=resolved_now, reason="avatar_expired"
            )
        ext_hand_locks = ecology.read_containment_hand_locks_readonly(root)

        char_info = ecology.read_character_identity_readonly(root)
        theme_info = ecology.detect_world_theme_readonly(root)
        pronouns = char_info["pronouns"]

        # Dispatch action
        if action == "restock_pantry":
            if payload.get("items") is not None:
                if payload.get("ingredient_id") is not None or payload.get("units_added") is not None:
                    return _error_response(
                        400,
                        "INVALID_FIELDS",
                        "Provide either single ingredient_id+units_added or batch items, not both",
                    )
                items_arg = list(payload["items"])
            else:
                items_arg = [
                    {
                        "ingredient_id": payload.get("ingredient_id"),
                        "label": payload.get("label"),
                        "category": payload.get("category"),
                        "unit_name": payload.get("unit_name"),
                        "units_added": payload.get("units_added"),
                    }
                ]
            action_result = ecology.apply_restock_pantry(
                state, items=items_arg, actor_side=normalized_actor, now_iso=resolved_now
            )
        elif action == "cook_or_brew":
            target_cup = payload.get("container_id") or (
                "ceramic_mug" if payload.get("serving_kind") == "drink" else None
            )
            cup_occ = bool(
                target_cup and somatic_view.is_cup_occupied_by_stationery(root, str(target_cup))
            )
            action_result = ecology.apply_cook_or_brew(
                state,
                title=payload.get("title"),
                serving_kind=payload.get("serving_kind"),
                ingredients_used=payload.get("ingredients_used"),
                portions=payload.get("portions"),
                container_id=payload.get("container_id"),
                cup_occupied_by_stationery=cup_occ,
                now_iso=resolved_now,
            )
        elif action == "refrigerate_dish":
            action_result = ecology.apply_refrigerate_dish(
                state,
                dish_id=payload.get("dish_id"),
                now_iso=resolved_now,
            )
        elif action == "reheat_dish":
            action_result = ecology.apply_reheat_dish(
                state,
                dish_id=payload.get("dish_id"),
                now_iso=resolved_now,
            )
        elif action == "consume_dish":
            sync_flag = payload.get("sync_somatic_drink", True)
            if not isinstance(sync_flag, bool):
                return _error_response(400, "INVALID_FIELDS", "sync_somatic_drink must be boolean")
            action_result = ecology.apply_consume_dish(
                state,
                dish_id=payload.get("dish_id"),
                now_iso=resolved_now,
            )
            receipt = action_result["consumption_receipt"]
            somatic_synced = False
            if sync_flag and receipt.get("serving_kind") == "drink":
                somatic_synced = _sync_somatic_drink_on_consume(
                    root, receipt=receipt, operation_key=op_key, now_iso=resolved_now
                )
            action_result["somatic_drink_synced"] = somatic_synced
            relief_map = receipt.get("lived_need_relief") or {}
            ingest_ack = somatic_view.apply_lived_needs_relief(
                root,
                hunger_relief=float(relief_map.get("hunger") or 0.0),
                thirst_relief=float(relief_map.get("thirst") or 0.0),
                receipt_id=str(receipt["receipt_id"]),
                now_iso=resolved_now,
                auto_initialize=True,
            )
            action_result["body_ingest_ack"] = ingest_ack
        elif action == "discard_dish":
            action_result = ecology.apply_discard_dish(
                state,
                dish_id=payload.get("dish_id"),
                reason=payload.get("reason"),
                now_iso=resolved_now,
            )
        elif action == "wash_dishes":
            action_result = ecology.apply_wash_dishes(
                state,
                actor_side=normalized_actor,
                now_iso=resolved_now,
            )
        elif action == "inspect_kitchen_zone":
            action_result = ecology.apply_inspect_kitchen_zone(
                state,
                zone_id=payload.get("zone_id"),
                actor_side=normalized_actor,
                now_iso=resolved_now,
            )
        elif action == "set_snack_drawer_permission":
            action_result = ecology.apply_set_snack_drawer_permission(
                state,
                partner_authorized=payload.get("partner_authorized"),
                now_iso=resolved_now,
            )
        elif action == "operate_kitchen_appliance":
            action_result = ecology.apply_operate_kitchen_appliance(
                state,
                appliance_id=payload.get("appliance_id"),
                operation=payload.get("operation"),
                now_iso=resolved_now,
            )
        elif action == "set_partner_presence":
            action_result = ecology.apply_set_partner_presence(
                state,
                presence_mode=payload.get("presence_mode"),
                pose=payload.get("pose"),
                ttl_seconds=payload.get("ttl_seconds"),
                expires_at=payload.get("expires_at"),
                subject_foreground_activity=payload.get("subject_foreground_activity"),
                actor_side=normalized_actor,
                now_iso=resolved_now,
            )
            if payload.get("presence_mode") == "absent":
                action_result["adult_permit_cascade"] = stance_view.cascade_pause_adult_scene_permit(
                    root, now_iso=resolved_now, reason="copresence_absent"
                )
        elif action == "exit_copresence":
            action_result = ecology.apply_exit_copresence(
                state,
                actor_side=normalized_actor,
                now_iso=resolved_now,
            )
            action_result["adult_permit_cascade"] = stance_view.cascade_pause_adult_scene_permit(
                root, now_iso=resolved_now, reason="exit_copresence"
            )
        elif action == "tidy_bed":
            action_result = ecology.apply_tidy_bed(
                state,
                actor_side=normalized_actor,
                now_iso=resolved_now,
            )
        elif action == "set_hand_slot":
            action_result = ecology.apply_set_hand_slot(
                state,
                slot=payload.get("slot"),
                object_id=payload.get("object_id"),
                label=payload.get("label"),
                surface_id=payload.get("surface_id"),
                requires_two_hands=payload.get("requires_two_hands", False),
                external_hand_locks=ext_hand_locks,
                now_iso=resolved_now,
            )
            ecology.sync_containment_hand_slots_if_present(
                root, state["embodied_contact"]["hand_slots"], now_iso=resolved_now
            )
        elif action == "set_subject_contact_posture":
            action_result = ecology.apply_set_subject_contact_posture(
                state,
                acute_illness=payload.get("acute_illness"),
                solitude_requested=payload.get("solitude_requested"),
                busy_decline=payload.get("busy_decline"),
                note=payload.get("note"),
                now_iso=resolved_now,
            )
            if "note" in payload and payload.get("note") is None:
                state["embodied_contact"]["subject_posture"]["note"] = None
                action_result["subject_posture"]["note"] = None
        elif action == "propose_contact":
            action_result = ecology.apply_propose_contact(
                state,
                contact_kind=payload.get("contact_kind"),
                actor_side=normalized_actor,
                pronouns=pronouns,
                external_hand_locks=ext_hand_locks,
                now_iso=resolved_now,
            )
        elif action == "respond_contact":
            action_result = ecology.apply_respond_contact(
                state,
                decision=payload.get("decision"),
                actor_side=normalized_actor,
                proposal_id=payload.get("proposal_id"),
                reason_note=payload.get("reason_note"),
                pronouns=pronouns,
                external_hand_locks=ext_hand_locks,
                now_iso=resolved_now,
            )
        elif action == "release_contact":
            action_result = ecology.apply_release_contact(
                state,
                actor_side=normalized_actor,
                reason=payload.get("reason"),
                external_hand_locks=ext_hand_locks,
                now_iso=resolved_now,
            )
        elif action == "record_npc_knowledge":
            action_result = ecology.apply_record_npc_knowledge(
                state,
                theme_info=theme_info,
                npc_id=payload.get("npc_id"),
                source_type=payload.get("source_type"),
                epistemic_basis=payload.get("epistemic_basis"),
                hearsay_from=payload.get("hearsay_from"),
                hearsay_sources=payload.get("hearsay_sources"),
                source_ref=payload.get("source_ref"),
                material_kind=payload.get("material_kind"),
                artifact_state=payload.get("artifact_state"),
                visibility=payload.get("visibility"),
                summary=payload.get("summary"),
                npc_quote=payload.get("npc_quote"),
                subject_private_impression=payload.get("subject_private_impression"),
                impression_shared=payload.get("impression_shared", False),
                require_open=payload.get("require_open", True),
                now_iso=resolved_now,
            )
        elif action == "revoke_npc_shared_ref":
            action_result = ecology.apply_revoke_npc_shared_ref(
                state,
                source_ref=payload.get("source_ref"),
                fact_id=payload.get("fact_id"),
                npc_id=payload.get("npc_id"),
                now_iso=resolved_now,
            )
        elif action == "feed_stray_cat":
            action_result = ecology.apply_feed_stray_cat(
                state,
                ingredient_id=payload.get("ingredient_id"),
                food_label=payload.get("food_label"),
                actor_side=normalized_actor,
                now_iso=resolved_now,
            )
        elif action == "propose_npc_commitment":
            action_result = ecology.apply_propose_npc_commitment(
                state,
                theme_info=theme_info,
                npc_id=payload.get("npc_id"),
                location_id=payload.get("location_id"),
                window_start_iso=payload.get("window_start_iso"),
                window_end_iso=payload.get("window_end_iso"),
                purpose=payload.get("purpose"),
                now_iso=resolved_now,
            )
        elif action == "resolve_npc_commitment":
            action_result = ecology.apply_resolve_npc_commitment(
                state,
                commitment_id=payload.get("commitment_id"),
                outcome=payload.get("outcome"),
                note=payload.get("note"),
                now_iso=resolved_now,
            )
        elif action == "submit_bookstore_card_feedback":
            action_result = ecology.apply_submit_bookstore_card_feedback(
                state,
                theme_info=theme_info,
                npc_id=payload.get("npc_id"),
                book_ref=payload.get("book_ref"),
                card_note=payload.get("card_note"),
                feedback_kind=payload.get("feedback_kind", "resonated"),
                shared_on_board=payload.get("shared_on_board", True),
                require_open=payload.get("require_open", True),
                now_iso=resolved_now,
            )
        elif action == "record_style_migration":
            action_result = ecology.apply_record_style_migration(
                state,
                style_id=payload.get("style_id"),
                title=payload.get("title"),
                origin_context=payload.get("origin_context"),
                origin_ref=payload.get("origin_ref"),
                stage=payload.get("stage"),
                capability=payload.get("capability"),
                stability=payload.get("stability"),
                enjoyment=payload.get("enjoyment"),
                work_ref=payload.get("work_ref"),
                private_note=payload.get("private_note"),
                public_summary=payload.get("public_summary"),
                shared=payload.get("shared"),
                now_iso=resolved_now,
            )
        elif action == "record_relationship_anchor":
            action_result = ecology.apply_record_relationship_anchor(
                state,
                target_id=payload.get("target_id", "partner"),
                anchor_category=payload.get("anchor_category"),
                domain=payload.get("domain"),
                trust_stance=payload.get("trust_stance"),
                boundary_statement=payload.get("boundary_statement"),
                object_kind=payload.get("object_kind"),
                label=payload.get("label"),
                story_note=payload.get("story_note"),
                phrase=payload.get("phrase"),
                meaning_context=payload.get("meaning_context"),
                secret_summary=payload.get("secret_summary"),
                shared_with_partner=payload.get("shared_with_partner", True),
                now_iso=resolved_now,
            )
        else:
            return _error_response(400, "INVALID_FIELDS", f"Unsupported action {action!r}")

        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = resolved_now

        if op_key is not None:
            receipts_map = state["operation_receipts"]
            receipts_map[op_key] = {
                "intent_fingerprint": intent_fp,
                "action": action,
                "actor_side": normalized_actor,
                "revision": state["revision"],
                "applied_at": resolved_now,
                "result": copy.deepcopy(action_result),
            }
            if len(receipts_map) > MAX_OPERATION_RECEIPTS:
                oldest_key = next(iter(receipts_map))
                del receipts_map[oldest_key]

        ecology.save_state_atomic(root, state)

        # Project using caller's capability boundary so partner mutations never leak private data
        has_subj = normalized_actor == "subject"
        projected = ecology.project_embodied_social_state(
            root,
            state=state,
            has_subject_capability=has_subj,
            now_iso=resolved_now,
        )
        return 200, _compose_view_envelope(
            root,
            projected=projected,
            has_subject_capability=has_subj,
            status_label="applied",
            extra_top={
                "action": action,
                "actor_side": normalized_actor,
                "already_applied": False,
                "result": action_result,
            },
        )
    except ecology.EmbodiedEcologyError as exc:
        return _error_response(exc.http_status, exc.reason_code, exc.detail)


def v03_embodied_social_state_path(day_root: Path | str) -> Path:
    """Return canonical state file path ``<day_root>/phone/v03_embodied_social_state.json``."""

    return ecology.state_file_path(day_root)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "CoPresenceAdapter",
    "ENDPOINT_PATH",
    "FORBIDDEN_FOREIGN_KEYS",
    "PARTNER_ALLOWED_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v03_embodied_social_view",
    "read_v03_embodied_social_view",
    "v03_embodied_social_state_path",
]
