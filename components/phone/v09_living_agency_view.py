#!/usr/bin/env python3
"""v0.9 Phone HTTP View: ``/api/phone/v09/living-agency`` (Pillars A + D).

Responsibilities & Contracts (Master Blueprint §6.1):
1. ``read_v09_living_agency_view``:
   - 100% zero-write (0 dirs, 0 files, 0 ``.lock`` files, 0 mtime changes, ``llm_calls == 0``).
   - Respects ``has_subject_capability`` (``view = "subject" | "partner"``):
     * Enforces asymmetric pre-reveal SHA-256 seal masking on ``blind_choices``.
     * Withholds unshared ``growth_stances``, private object notes, and private
       custom categories/items from partner view (zero hidden count leakage).
   - Enforces ``v02_representation_lease_forget.load_unified_forget_barrier_readonly``
     (fail-closed on unreadable/corrupt barrier).
   - Returns ``{ok, status, schema: "phone.v09.living_agency.v1", view, zero_write: True,
     llm_calls: 0, data: {blind_choices, growth_stances, repurposed_objects,
     custom_categories, categorized_items, capability_gaps_summary, ...}}``.
2. ``mutate_v09_living_agency_view``:
   - Strict action & field whitelists; rejects smuggled ``actor_side`` / ``author_side``
     (``400``) and ``clear_reservations`` (``409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN``).
   - Strict partner whitelist (CSRF only, no capability required):
     ``open_blind_choice``, ``partner_submit_blind_choice``,
     ``partner_discuss_blind_choice``, ``partner_persuade_stance``,
     ``register_unformed_topic``.
   - Subject-only actions (``403 FORBIDDEN_ACTOR_SIDE`` without verified capability):
     * Pillar A: ``subject_commit_blind_choice``, ``resolve_blind_choice``,
       ``advance_stance_from_experience``, ``evaluate_partner_persuasion``,
       ``set_stance_shared``.
     * Pillar D: ``register_or_repurpose_object``, ``repurpose_object``,
       ``store_item_in_object``, ``attach_label_to_object``, ``add_note_to_object``,
       ``remove_stored_item_from_object``, ``empty_stored_items``,
       ``create_custom_category``, ``assign_or_move_category_item``,
       ``revise_custom_category``, ``record_blocked_attempt``,
       ``use_object_capability``.
"""
from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS), str(_WORLD_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from components.life.scripts import v09_true_stance_engine as _tse
except ImportError:  # pragma: no cover - flat script import fallback
    import v09_true_stance_engine as _tse  # type: ignore[no-redef]

try:
    from components.world.scripts import personal_objects_taxonomy as _pot
except ImportError:  # pragma: no cover - flat script import fallback
    import personal_objects_taxonomy as _pot  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat script import fallback
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - flat script import fallback
    import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]

ROUTE_V09_LIVING_AGENCY = "/api/phone/v09/living-agency"
ENDPOINT_PATH = ROUTE_V09_LIVING_AGENCY
VIEW_SCHEMA = "phone.v09.living_agency.v1"

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "expected_revision",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "open_blind_choice",
    "partner_submit_blind_choice",
    "partner_discuss_blind_choice",
    "partner_persuade_stance",
    "register_unformed_topic",
})

PARTNER_ONLY_ACTIONS: frozenset[str] = frozenset({
    "partner_submit_blind_choice",
    "partner_discuss_blind_choice",
    "partner_persuade_stance",
})

STANCE_SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "subject_commit_blind_choice",
    "resolve_blind_choice",
    "advance_stance_from_experience",
    "evaluate_partner_persuasion",
    "set_stance_shared",
})

STANCE_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | STANCE_SUBJECT_ONLY_ACTIONS

TAXONOMY_SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "register_or_repurpose_object",
    "repurpose_object",
    "store_item_in_object",
    "attach_label_to_object",
    "add_note_to_object",
    "remove_stored_item_from_object",
    "empty_stored_items",
    "create_custom_category",
    "assign_or_move_category_item",
    "revise_custom_category",
    "record_blocked_attempt",
    "use_object_capability",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = STANCE_SUBJECT_ONLY_ACTIONS | TAXONOMY_SUBJECT_ONLY_ACTIONS
ALL_ALLOWED_ACTIONS: frozenset[str] = STANCE_ACTIONS | TAXONOMY_SUBJECT_ONLY_ACTIONS

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    # Pillar A: Blind Simultaneous Choice & Growth-from-Blank
    "open_blind_choice": COMMON_COMMAND_FIELDS | frozenset({
        "choice_id",
        "topic_cn",
        "prompt_cn",
        "options",
        "context_ref",
    }),
    "subject_commit_blind_choice": COMMON_COMMAND_FIELDS | frozenset({
        "choice_id",
        "selected_choice_id",
        "subject_choice_id",
        "initial_choice_id",
        "option_id",
        "reasons",
        "subject_reasons",
        "initial_subject_reasons",
        "commit_nonce",
        "nonce",
    }),
    "partner_submit_blind_choice": COMMON_COMMAND_FIELDS | frozenset({
        "choice_id",
        "selected_choice_id",
        "partner_choice_id",
        "option_id",
        "partner_reason_cn",
        "reason_cn",
    }),
    "partner_discuss_blind_choice": COMMON_COMMAND_FIELDS | frozenset({
        "choice_id",
        "message_cn",
        "note_cn",
        "argument_cn",
        "evidence_ref",
        "insists_on_partner_choice",
    }),
    "resolve_blind_choice": COMMON_COMMAND_FIELDS | frozenset({
        "choice_id",
        "resolution_mode",
        "resolution_note_cn",
        "adopted_action_choice_id",
        "preferred_judgment_choice_id",
        "compromise_option_id",
        "compromise_label_cn",
        "tradeoff_note_cn",
        "retained_reservations",
        "judgment_flipped",
        "new_evidence",
        "what_changed_mind_cn",
        "new_choice_id",
    }),
    "register_unformed_topic": COMMON_COMMAND_FIELDS | frozenset({
        "stance_id",
        "topic_key",
        "topic_cn",
        "prompt_cn",
        "stance_summary_cn",
        "shared_with_partner",
    }),
    "advance_stance_from_experience": COMMON_COMMAND_FIELDS | frozenset({
        "stance_id",
        "target_status",
        "provenance_tier",
        "stance_summary_cn",
        "change_reason_cn",
        "receipt_ref",
        "episode_ref",
        "material_ref",
        "page_or_anchor",
        "partner_argument_cn",
        "tone_pressure",
        "demand_agree",
        "reasons",
        "doubts_cn",
        "replaces_stance_id",
        "shared_with_partner",
    }),
    "partner_persuade_stance": COMMON_COMMAND_FIELDS | frozenset({
        "stance_id",
        "partner_argument_cn",
        "argument_cn",
        "evidence_ref",
        "tone_pressure",
        "demand_agree",
        "promote_to_formed",
    }),
    "evaluate_partner_persuasion": COMMON_COMMAND_FIELDS | frozenset({
        "stance_id",
        "persuasion_id",
        "persuasion_verdict",
        "subject_response_cn",
        "kept_doubt_cn",
        "stance_summary_cn",
        "promote_to_formed",
    }),
    "set_stance_shared": COMMON_COMMAND_FIELDS | frozenset({
        "stance_id",
        "shared_with_partner",
    }),
    # Pillar D: Emergent Object Reuse, Self-Invented Taxonomy & Capability Gaps
    "register_or_repurpose_object": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "object_kind",
        "base_name_cn",
        "custom_label_cn",
        "supported_capabilities",
        "capacity_slots",
        "drinkware",
        "emptied_from_consumable",
        "consumable_portions_remaining",
        "origin_provenance",
        "economy_item_instance_id",
        "catalog_item_id",
        "reusable_container_spec",
        "location_hint_cn",
        "visibility",
    }),
    "repurpose_object": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "object_kind",
        "base_name_cn",
        "custom_label_cn",
        "supported_capabilities",
        "capacity_slots",
        "drinkware",
        "emptied_from_consumable",
        "consumable_portions_remaining",
        "origin_provenance",
        "economy_item_instance_id",
        "catalog_item_id",
        "reusable_container_spec",
        "location_hint_cn",
        "visibility",
    }),
    "store_item_in_object": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "item_label_cn",
        "item_kind",
        "stored_item_id",
        "source_ref",
        "note_cn",
    }),
    "attach_label_to_object": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "custom_label_cn",
    }),
    "add_note_to_object": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "note_cn",
        "visibility",
        "source_ref",
    }),
    "remove_stored_item_from_object": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "stored_item_id",
        "item_label_cn",
        "remove_all",
    }),
    "empty_stored_items": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "stored_item_id",
        "item_label_cn",
        "remove_all",
    }),
    "create_custom_category": COMMON_COMMAND_FIELDS | frozenset({
        "category_id",
        "title_cn",
        "scope_kinds",
        "description_cn",
        "visibility",
    }),
    "assign_or_move_category_item": COMMON_COMMAND_FIELDS | frozenset({
        "item_id",
        "category_id",
        "target_kind",
        "target_ref",
        "target_title_cn",
        "annotation_cn",
        "reason_for_move_cn",
        "visibility",
    }),
    "revise_custom_category": COMMON_COMMAND_FIELDS | frozenset({
        "category_id",
        "title_cn",
        "description_cn",
        "scope_kinds",
        "visibility",
        "archived",
        "reason_cn",
    }),
    "record_blocked_attempt": COMMON_COMMAND_FIELDS | frozenset({
        "goal_cn",
        "attempted_action_cn",
        "blocker_code",
        "blocker_detail_cn",
        "target_ref",
        "visibility",
    }),
    "use_object_capability": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "requested_capability",
    }),
}

SMUGGLED_ACTOR_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "author_side",
    "created_by_side",
    "has_subject_capability",
    "subject_view",
    "capability",
})

FORBIDDEN_CLIENT_KEYS: frozenset[str] = frozenset({
    "uid",
    "user_id",
    "owner_uid",
    "local_owner_uid",
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
    "actor",
    "intimacy_score",
    "affection_score",
    "favorability",
    "relationship_xp",
    "obedience_level",
    "compliance_score",
    "sycophancy_score",
    "force_flip",
    "auto_agree",
    "custom_code",
    "code",
    "script",
    "eval",
    "exec",
    "python_code",
    "shell_command",
    "grant_capability",
})


def _error_response(
    status_code: int,
    reason_code: str,
    detail: str = "",
    *,
    view: str = "partner",
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code or "REQUEST_REFUSED")
    return int(status_code), {
        "ok": False,
        "status": "rejected" if status_code < 500 else "unavailable",
        "command_status": "REJECTED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "view": view,
        "perspective": view,
        "zero_write": True,
        "reason_code": code_str,
        "error_code": code_str,
        "reason": code_str,
        "detail": str(detail or code_str)[:240],
        "llm_calls": 0,
        "data": {},
    }


def _scan_keys_recursive_lower(
    obj: Any,
    forbidden_lower: frozenset[str],
    *,
    _depth: int = 0,
) -> Optional[str]:
    if _depth > 10:
        return None
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if isinstance(k, str) and k.strip().lower() in forbidden_lower:
                return k
            nested = _scan_keys_recursive_lower(v, forbidden_lower, _depth=_depth + 1)
            if nested is not None:
                return nested
    elif isinstance(obj, (list, tuple)):
        for elem in obj:
            nested = _scan_keys_recursive_lower(elem, forbidden_lower, _depth=_depth + 1)
            if nested is not None:
                return nested
    return None


def _exceeds_max_depth(obj: Any, *, max_depth: int = 10, _depth: int = 0) -> bool:
    if _depth > max_depth:
        return True
    if isinstance(obj, Mapping):
        for v in obj.values():
            if _exceeds_max_depth(v, max_depth=max_depth, _depth=_depth + 1):
                return True
    elif isinstance(obj, (list, tuple)):
        for elem in obj:
            if _exceeds_max_depth(elem, max_depth=max_depth, _depth=_depth + 1):
                return True
    return False


def _scan_keys_recursive(obj: Any, forbidden: frozenset[str], *, _depth: int = 0) -> Optional[str]:
    if _depth > 10:
        return None
    forbidden_lower = frozenset(str(item).strip().lower() for item in forbidden)
    return _scan_keys_recursive_lower(obj, forbidden_lower, _depth=_depth)


def _build_living_agency_data_zero_write(
    root: Path | str,
    *,
    actor_side: str,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the zero-write Pillars A + D projection with unified forget barrier."""
    resolved_root = _instance_paths.resolve_instance_paths(root).day_root
    barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)

    if bool(barrier.get("fail_closed")):
        return {
            "blind_choices": [],
            "active_blind_choices": [],
            "growth_stances": [],
            "repurposed_objects": [],
            "custom_categories": [],
            "categorized_items": [],
            "blocked_capability_log": [],
            "ranked_capability_gaps": [],
            "capability_gaps_summary": {
                "total_clusters": 0,
                "total_occurrences": 0,
                "top_gaps": [],
            },
            "summary": {
                "blind_choice_count": 0,
                "active_blind_choice_count": 0,
                "growth_stance_count": 0,
                "repurposed_object_count": 0,
                "custom_category_count": 0,
                "categorized_item_count": 0,
                "capability_gap_cluster_count": 0,
            },
            "state_exists": False,
            "observed_at": now_iso,
            "forget_barrier_active": True,
            "forget_barrier_fail_closed": True,
            "zero_write": True,
            "llm_calls": 0,
        }

    stance_proj = _tse.read_true_stance_zero_write(
        resolved_root,
        actor_side=actor_side,
        now_iso=now_iso,
    )
    tax_proj = _pot.read_personal_objects_taxonomy_zero_write(
        resolved_root,
        actor_side=actor_side,
        now_iso=now_iso,
    )

    blind_choices = copy.deepcopy(list(stance_proj.get("blind_choices") or []))
    active_blind_choices = copy.deepcopy(list(stance_proj.get("active_blind_choices") or []))
    growth_stances = copy.deepcopy(list(stance_proj.get("growth_stances") or []))
    repurposed_objects = copy.deepcopy(list(tax_proj.get("repurposed_objects") or []))
    custom_categories = copy.deepcopy(list(tax_proj.get("custom_categories") or []))
    categorized_items = copy.deepcopy(list(tax_proj.get("categorized_items") or []))
    blocked_capability_log = copy.deepcopy(list(tax_proj.get("blocked_capability_log") or []))
    ranked_capability_gaps = copy.deepcopy(list(tax_proj.get("ranked_capability_gaps") or []))
    cap_summary = copy.deepcopy(
        dict(
            tax_proj.get("capability_gaps_summary")
            or {"total_clusters": 0, "total_occurrences": 0, "top_gaps": []}
        )
    )

    stance_summary = dict(stance_proj.get("summary") or {})
    merged_summary: dict[str, Any] = dict(stance_summary)
    merged_summary.update({
        "blind_choice_count": len(blind_choices),
        "active_blind_choice_count": len(active_blind_choices),
        "growth_stance_count": len(growth_stances),
        "repurposed_object_count": len(repurposed_objects),
        "custom_category_count": len(custom_categories),
        "categorized_item_count": len(categorized_items),
        "capability_gap_cluster_count": int(cap_summary.get("total_clusters") or len(ranked_capability_gaps)),
    })

    return {
        "blind_choices": blind_choices,
        "active_blind_choices": active_blind_choices,
        "growth_stances": growth_stances,
        "repurposed_objects": repurposed_objects,
        "custom_categories": custom_categories,
        "categorized_items": categorized_items,
        "blocked_capability_log": blocked_capability_log,
        "ranked_capability_gaps": ranked_capability_gaps,
        "capability_gaps_summary": cap_summary,
        "summary": merged_summary,
        "state_exists": bool(stance_proj.get("state_exists")) or bool(tax_proj.get("state_exists")),
        "observed_at": stance_proj.get("observed_at") or tax_proj.get("observed_at") or now_iso,
        "forget_barrier_active": bool(
            stance_proj.get("forget_barrier_active")
            or tax_proj.get("forget_barrier_active")
            or barrier.get("barrier_active")
        ),
        "forget_barrier_fail_closed": bool(
            stance_proj.get("forget_barrier_fail_closed")
            or tax_proj.get("forget_barrier_fail_closed")
        ),
        "zero_write": True,
        "llm_calls": 0,
    }


def read_v09_living_agency_view(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read ``GET /api/phone/v09/living-agency`` (100% zero-write, seal & barrier enforced)."""
    actor_side = "subject" if bool(has_subject_capability) else "partner"
    try:
        data = _build_living_agency_data_zero_write(
            root,
            actor_side=actor_side,
            now_iso=now_iso,
        )
    except _tse.V09TrueStanceError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, view=actor_side)
    except _pot.PersonalObjectsTaxonomyError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, view=actor_side)
    except Exception as exc:  # pragma: no cover - defensive read guard
        return _error_response(503, "LIVING_AGENCY_UNAVAILABLE", str(exc)[:180], view=actor_side)

    return 200, {
        "ok": True,
        "status": "ok",
        "command_status": "READ",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "view": actor_side,
        "perspective": actor_side,
        "zero_write": True,
        "llm_calls": 0,
        "data": data,
    }


def mutate_v09_living_agency_view(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Execute one guarded write on ``POST /api/phone/v09/living-agency``."""
    side = str(actor_side or "partner").strip().lower()
    if side not in ("partner", "subject"):
        return _error_response(403, "FORBIDDEN_ACTOR_SIDE", f"Unsupported actor_side: {actor_side!r}")

    if not isinstance(payload, Mapping) or not payload:
        return _error_response(400, "INVALID_FIELDS", "payload must be a non-empty JSON object", view=side)

    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            return _error_response(400, "INVALID_FIELDS", "blank or padded key forbidden", view=side)

    if _exceeds_max_depth(payload, max_depth=10):
        return _error_response(400, "INVALID_FIELDS", "payload nesting depth exceeds limit", view=side)

    # Anti-sycophancy erasure guard (always 409 even if mixed-case or nested alongside valid fields)
    if _scan_keys_recursive(payload, frozenset({"clear_reservations"})) is not None:
        return _error_response(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
            view=side,
        )

    smuggled = _scan_keys_recursive(payload, SMUGGLED_ACTOR_KEYS)
    if smuggled is not None:
        return _error_response(
            400,
            "SMUGGLED_ACTOR_FIELD",
            f"Client may not supply {smuggled}",
            view=side,
        )

    forbidden = _scan_keys_recursive(payload, FORBIDDEN_CLIENT_KEYS)
    if forbidden is not None:
        return _error_response(
            400,
            "FORBIDDEN_CLIENT_KEY",
            f"Forbidden client key supplied: {forbidden}",
            view=side,
        )

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        return _error_response(400, "INVALID_FIELDS", "Missing required field 'action'", view=side)
    action = raw_action.strip()

    if action not in ALL_ALLOWED_ACTIONS:
        return _error_response(400, "UNKNOWN_ACTION", f"Unknown action: {action!r}", view=side)

    # Permission boundary BEFORE per-action field check
    if side == "partner" and action not in PARTNER_ALLOWED_ACTIONS:
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} requires a verified subject capability",
            view=side,
        )
    if side == "subject" and action in PARTNER_ONLY_ACTIONS:
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} belongs exclusively to the partner door",
            view=side,
        )

    unknown = sorted(set(payload) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        return _error_response(
            400,
            "INVALID_FIELDS",
            f"Unknown fields for {action}: {', '.join(unknown)}",
            view=side,
        )

    op_key = payload.get("operation_key")
    if op_key is not None and (not isinstance(op_key, str) or not op_key.strip() or op_key != op_key.strip()):
        return _error_response(400, "INVALID_FIELDS", "operation_key must be a trimmed non-empty string", view=side)

    try:
        if action in STANCE_ACTIONS:
            action_result = _tse.apply_true_stance_command(
                root,
                dict(payload),
                actor_side=side,
                now_iso=now_iso,
            )
        else:
            action_result = _pot.apply_personal_objects_taxonomy_command(
                root,
                dict(payload),
                actor_side=side,
                now_iso=now_iso,
            )
    except _tse.V09TrueStanceError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, view=side)
    except _pot.PersonalObjectsTaxonomyError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, view=side)
    except Exception as exc:  # pragma: no cover - defensive write guard
        return _error_response(503, "LIVING_AGENCY_MUTATION_FAILED", str(exc)[:180], view=side)

    is_dup = bool(
        action_result.get("already_applied")
        or action_result.get("idempotent_replay")
        or action_result.get("status") == "already_applied"
        or action_result.get("command_status") == "DUPLICATE"
    )

    fresh_data = _build_living_agency_data_zero_write(
        root,
        actor_side=side,
        now_iso=now_iso,
    )
    fresh_data["action"] = action
    fresh_data["action_receipt"] = copy.deepcopy(action_result)

    return 200, {
        "ok": True,
        "status": "already_applied" if is_dup else "applied",
        "command_status": "DUPLICATE" if is_dup else "APPLIED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "view": side,
        "perspective": side,
        "zero_write": bool(action_result.get("zero_write", False)),
        "action": action,
        "receipt": copy.deepcopy(action_result),
        "llm_calls": 0,
        "data": fresh_data,
    }


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "ALL_ALLOWED_ACTIONS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "FORBIDDEN_CLIENT_KEYS",
    "PARTNER_ALLOWED_ACTIONS",
    "PARTNER_ONLY_ACTIONS",
    "ROUTE_V09_LIVING_AGENCY",
    "SMUGGLED_ACTOR_KEYS",
    "STANCE_ACTIONS",
    "STANCE_SUBJECT_ONLY_ACTIONS",
    "SUBJECT_ONLY_ACTIONS",
    "TAXONOMY_SUBJECT_ONLY_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v09_living_agency_view",
    "read_v09_living_agency_view",
]
