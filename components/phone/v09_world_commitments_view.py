#!/usr/bin/env python3
"""v0.9 Phone HTTP View: ``/api/phone/v09/world-commitments`` (Pillars B + C).

Responsibilities & Contracts (Master Blueprint §6.1):
1. ``read_v09_world_commitments_view``:
   - 100% zero-write (0 dirs, 0 files, 0 ``.lock`` files, 0 mtime changes, ``llm_calls == 0``).
   - Respects ``has_subject_capability`` (``view = "subject" | "partner"``) and epistemic
     ``subject_observed_venues`` boundaries.
   - Enforces ``v02_representation_lease_forget.load_unified_forget_barrier_readonly``
     (fail-closed on unreadable/corrupt barrier).
   - Returns ``{ok, status, schema: "phone.v09.world_commitments.v1", view, zero_write: True,
     llm_calls: 0, data: {dynamic_catalog, llm_generator_status, npcs, commitments,
     active_commitments, venue_notices, observed_venue_notices, min_next_due_at, ...}}``.
2. ``mutate_v09_world_commitments_view``:
   - Strict action & field whitelists; rejects smuggled ``actor_side`` / ``author_side`` /
     identity / retroactive-fabrication fields cleanly.
   - Partner-allowed actions:
     * ``place_partner_reservation``
     * ``propose_shared_appointment``
     * ``operator_generate_shop_arrivals`` (optional ``generation_mode`` in ``"offline"`` | ``"llm"``,
       default ``"offline"`` with ``llm_calls == 0``)
   - Subject-only actions (``403 FORBIDDEN_ACTOR_SIDE`` without verified capability):
     * ``create_commitment``
     * ``contact_npc_or_venue``
     * ``advance_due_commitments``
     * ``generate_world_content_batch`` (optional ``generation_mode`` in ``"offline"`` | ``"llm"``,
       default ``"offline"`` with ``llm_calls == 0``)
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
    from components.world.scripts import world_commitments_engine as _wce
except ImportError:  # pragma: no cover - flat script import fallback
    import world_commitments_engine as _wce  # type: ignore[no-redef]

try:
    from components.world.scripts import world_content_catalog as _wcc
except ImportError:  # pragma: no cover - flat script import fallback
    import world_content_catalog as _wcc  # type: ignore[no-redef]

try:
    from components.world.scripts import world_content_generator as _wcg
except ImportError:  # pragma: no cover - flat script import fallback
    import world_content_generator as _wcg  # type: ignore[no-redef]

try:
    from components.world.scripts import world_content_llm_adapter as _llm_adapter
except ImportError:  # pragma: no cover - flat script import fallback
    import world_content_llm_adapter as _llm_adapter  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat script import fallback
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - flat script import fallback
    import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]

ROUTE_V09_WORLD_COMMITMENTS = "/api/phone/v09/world-commitments"
ENDPOINT_PATH = ROUTE_V09_WORLD_COMMITMENTS
VIEW_SCHEMA = "phone.v09.world_commitments.v1"

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "place_partner_reservation",
    "propose_shared_appointment",
    "operator_generate_shop_arrivals",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "create_commitment",
    "contact_npc_or_venue",
    "advance_due_commitments",
    "generate_world_content_batch",
})

ALL_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | SUBJECT_ONLY_ACTIONS

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "place_partner_reservation": COMMON_COMMAND_FIELDS | frozenset({
        "venue_id",
        "npc_id",
        "title_cn",
        "item_name_cn",
        "title",
        "item_id",
        "source_ref",
        "commitment_id",
        "arrival_due_at",
        "due_at",
        "arrival_in_hours",
        "hold_hours",
        "hold_until_at",
        "catalog_item_spec",
        "weather_sensitive",
        "current_weather",
        "weather",
    }),
    "propose_shared_appointment": COMMON_COMMAND_FIELDS | frozenset({
        "venue_id",
        "npc_id",
        "title_cn",
        "title",
        "commitment_id",
        "source_ref",
        "window_start_at",
        "window_end_at",
        "start_at",
        "end_at",
        "weather_sensitive",
        "indoor_fallback_venue_id",
        "current_weather",
        "weather",
    }),
    "operator_generate_shop_arrivals": COMMON_COMMAND_FIELDS | frozenset({
        "store_id",
        "trigger_reason",
        "world_context",
        "force_cooldown_bypass",
        "generation_mode",
    }),
    "create_commitment": COMMON_COMMAND_FIELDS | frozenset({
        "kind",
        "title_cn",
        "item_name_cn",
        "title",
        "npc_id",
        "venue_id",
        "requested_by",
        "commitment_id",
        "source_ref",
        "item_id",
        "current_weather",
        "weather",
        "arrival_due_at",
        "due_at",
        "arrival_in_hours",
        "hold_hours",
        "hold_until_at",
        "supply_delayed",
        "delay_reason_cn",
        "weather_sensitive",
        "catalog_item_spec",
        "window_start_at",
        "start_at",
        "window_end_at",
        "end_at",
        "indoor_fallback_venue_id",
        "effect_kind",
        "door_notice_cn",
        "topic_summary_cn",
    }),
    "contact_npc_or_venue": COMMON_COMMAND_FIELDS | frozenset({
        "venue_id",
        "npc_id",
        "contact_action",
        "commitment_id",
        "extra_params",
    }),
    "advance_due_commitments": COMMON_COMMAND_FIELDS | frozenset({
        "current_weather",
        "weather",
        "target_now_iso",
    }),
    "generate_world_content_batch": COMMON_COMMAND_FIELDS | frozenset({
        "store_id",
        "trigger_reason",
        "world_context",
        "force_cooldown_bypass",
        "generation_mode",
    }),
}

ALLOWED_GENERATION_MODES: frozenset[str] = frozenset({"offline", "llm"})

SMUGGLED_ACTOR_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "author_side",
    "created_by_side",
    "clear_reservations",
})

FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset({
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
    "actor",
    "price",
    "balance",
    "grant_id",
    "workspace_id",
    "artifact_id",
    "affection_delta",
    "force_subject_preference",
    "custom_code",
    "code",
    "grant_capability",
})

FORBIDDEN_NESTED_KEYS: frozenset[str] = (
    (FORBIDDEN_IDENTITY_KEYS - frozenset({"price"}))
    | _wcc.FORBIDDEN_CANDIDATE_FIELDS
    | frozenset({"code", "custom_code"})
)


def _error_response(
    status_code: int,
    reason_code: str,
    detail: str = "",
    *,
    view: str = "partner",
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code or "REQUEST_REFUSED")
    safe_detail = _llm_adapter.redact_secrets(str(detail or code_str))[:240]
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
        "detail": safe_detail,
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


def _build_world_commitments_data_zero_write(
    root: Path | str,
    *,
    actor_side: str,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the zero-write world-commitments projection with unified forget barrier."""
    resolved_root = _instance_paths.resolve_instance_paths(root).day_root
    barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
    llm_status = _llm_adapter.read_external_llm_status_zero_write()

    if bool(barrier.get("fail_closed")):
        return {
            "dynamic_catalog": {
                "schema": _wcc.SCHEMA,
                "exists": False,
                "revision": 0,
                "updated_at": None,
                "items": [],
                "events": [],
                "store_cooldowns": {},
                "barrier_fail_closed": True,
                "llm_calls": 0,
            },
            "llm_generator_status": llm_status,
            "npcs": [],
            "commitments": [],
            "active_commitments": [],
            "venue_notices": [],
            "observed_venue_notices": [],
            "unobserved_notice_count": 0,
            "min_next_due_at": None,
            "last_weather_seen": None,
            "state_exists": False,
            "revision": 0,
            "observed_at": now_iso,
            "forget_barrier_active": True,
            "forget_barrier_fail_closed": True,
            "zero_write": True,
            "llm_calls": 0,
        }

    catalog_proj = _wcc.read_dynamic_catalog_zero_write(
        resolved_root,
        include_out_of_stock=True,
        now_iso=now_iso,
    )
    commit_proj = _wce.read_world_commitments_zero_write(
        resolved_root,
        actor_side=actor_side,
        now_iso=now_iso,
    )

    dynamic_catalog_block: dict[str, Any] = {
        "schema": str(catalog_proj.get("schema") or _wcc.SCHEMA),
        "exists": bool(catalog_proj.get("exists")),
        "revision": int(catalog_proj.get("revision") or 0),
        "updated_at": catalog_proj.get("updated_at"),
        "items": copy.deepcopy(list(catalog_proj.get("items") or [])),
        "events": copy.deepcopy(list(catalog_proj.get("events") or [])),
        "store_cooldowns": copy.deepcopy(dict(catalog_proj.get("store_cooldowns") or {})),
        "barrier_fail_closed": bool(catalog_proj.get("barrier_fail_closed")),
        "llm_calls": 0,
    }

    return {
        "dynamic_catalog": dynamic_catalog_block,
        "llm_generator_status": llm_status,
        "npcs": copy.deepcopy(list(commit_proj.get("npcs") or [])),
        "commitments": copy.deepcopy(list(commit_proj.get("commitments") or [])),
        "active_commitments": copy.deepcopy(list(commit_proj.get("active_commitments") or [])),
        "venue_notices": copy.deepcopy(list(commit_proj.get("venue_notices") or [])),
        "observed_venue_notices": copy.deepcopy(list(commit_proj.get("observed_venue_notices") or [])),
        "unobserved_notice_count": int(commit_proj.get("unobserved_notice_count") or 0),
        "min_next_due_at": commit_proj.get("min_next_due_at"),
        "last_weather_seen": commit_proj.get("last_weather_seen"),
        "instance_binding": copy.deepcopy(dict(commit_proj.get("instance_binding") or {})),
        "state_exists": bool(commit_proj.get("state_exists")) or bool(catalog_proj.get("exists")),
        "revision": int(commit_proj.get("revision") or 0),
        "observed_at": commit_proj.get("observed_at"),
        "forget_barrier_active": bool(
            commit_proj.get("forget_barrier_active") or barrier.get("barrier_active")
        ),
        "forget_barrier_fail_closed": bool(
            commit_proj.get("forget_barrier_fail_closed") or catalog_proj.get("barrier_fail_closed")
        ),
        "zero_write": True,
        "llm_calls": 0,
    }


def read_v09_world_commitments_view(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read ``GET /api/phone/v09/world-commitments`` (100% zero-write, barrier-enforced)."""
    actor_side = "subject" if bool(has_subject_capability) else "partner"
    try:
        data = _build_world_commitments_data_zero_write(
            root,
            actor_side=actor_side,
            now_iso=now_iso,
        )
    except _wce.WorldCommitmentsError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, view=actor_side)
    except _wcc.WorldCatalogError as exc:
        return _error_response(400, exc.reason_code, exc.detail, view=actor_side)
    except Exception as exc:  # pragma: no cover - defensive read guard
        return _error_response(503, "WORLD_COMMITMENTS_UNAVAILABLE", str(exc)[:180], view=actor_side)

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


def mutate_v09_world_commitments_view(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Execute one guarded write on ``POST /api/phone/v09/world-commitments``."""
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

    smuggled = _scan_keys_recursive(payload, SMUGGLED_ACTOR_KEYS)
    if smuggled is not None:
        return _error_response(
            400,
            "SMUGGLED_ACTOR_FIELD",
            f"Client may not supply {smuggled}",
            view=side,
        )

    if (
        payload.get("action") in ("operator_generate_shop_arrivals", "generate_world_content_batch")
        and isinstance(payload.get("world_context"), Mapping)
    ):
        try:
            _wcg.anti_spoil_guard(payload["world_context"])
        except _wcg.AntiSpoilViolationError as exc:
            return _error_response(400, exc.reason_code, str(exc), view=side)

    retro_key = _scan_keys_recursive(payload, _wce.FORBIDDEN_RETROACTIVE_KEYS)
    if retro_key is not None:
        return _error_response(
            400,
            "RETROACTIVE_FABRICATION_FORBIDDEN",
            f"Retroactive fabrication or private smuggling key forbidden: {retro_key}",
            view=side,
        )

    top_forbidden = sorted(
        k for k in payload if isinstance(k, str) and k.strip().lower() in FORBIDDEN_IDENTITY_KEYS
    )
    if top_forbidden:
        return _error_response(
            409,
            "CLIENT_IDENTITY_REFUSED",
            f"Forbidden client fields: {', '.join(top_forbidden)}",
            view=side,
        )

    nested_forbidden = _scan_keys_recursive(payload, FORBIDDEN_NESTED_KEYS)
    if nested_forbidden is not None:
        return _error_response(
            400,
            "FORBIDDEN_CLIENT_KEY",
            f"Forbidden nested client key supplied: {nested_forbidden}",
            view=side,
        )

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
        return _error_response(400, "INVALID_FIELDS", "Missing required field 'action'", view=side)
    action = raw_action.strip()

    if action not in ALL_ALLOWED_ACTIONS:
        return _error_response(400, "INVALID_FIELDS", f"Unknown action: {action!r}", view=side)

    if action in SUBJECT_ONLY_ACTIONS and side != "subject":
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} requires a verified subject capability",
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

    llm_calls_used = 0
    generation_mode_used: Optional[str] = None
    llm_response_meta: Optional[dict[str, Any]] = None

    try:
        action_result: dict[str, Any]
        if action == "place_partner_reservation":
            cmd_payload = {k: v for k, v in payload.items() if k != "action"}
            cmd_payload["kind"] = "BOOK_OR_ITEM_RESERVATION"
            cmd_payload["requested_by"] = side
            action_result = _wce.create_commitment(
                root,
                cmd_payload,
                actor_side=side,
                operation_key=op_key,
                now_iso=now_iso,
            )
        elif action == "propose_shared_appointment":
            cmd_payload = {k: v for k, v in payload.items() if k != "action"}
            cmd_payload["kind"] = "SCHEDULED_APPOINTMENT"
            cmd_payload["requested_by"] = side
            action_result = _wce.create_commitment(
                root,
                cmd_payload,
                actor_side=side,
                operation_key=op_key,
                now_iso=now_iso,
            )
        elif action in ("operator_generate_shop_arrivals", "generate_world_content_batch"):
            store_id = payload.get("store_id")
            if not isinstance(store_id, str) or not store_id.strip():
                return _error_response(400, "INVALID_STORE_ID", "store_id is required", view=side)
            trigger_reason = str(payload.get("trigger_reason") or "OPERATOR_RESTOCK").strip() or "OPERATOR_RESTOCK"
            world_context = payload.get("world_context")
            if world_context is not None and not isinstance(world_context, Mapping):
                return _error_response(400, "INVALID_FIELDS", "world_context must be a JSON object", view=side)
            bypass = payload.get("force_cooldown_bypass", False)
            if not isinstance(bypass, bool):
                return _error_response(400, "INVALID_FIELDS", "force_cooldown_bypass must be a boolean", view=side)
            if "generation_mode" not in payload:
                gen_mode = "offline"
            else:
                raw_mode = payload.get("generation_mode")
                if not isinstance(raw_mode, str) or raw_mode not in ALLOWED_GENERATION_MODES:
                    return _error_response(
                        400,
                        "INVALID_GENERATION_MODE",
                        "generation_mode must be 'offline' or 'llm'",
                        view=side,
                    )
                gen_mode = raw_mode
            generation_mode_used = gen_mode

            # Anti-spoil guard always runs BEFORE config resolution, budget reservation, or HTTP calls.
            if world_context is not None:
                _wcg.anti_spoil_guard(world_context)

            if gen_mode == "offline":
                action_result = _wcg.generate_world_content_batch(
                    root,
                    store_id=store_id.strip(),
                    trigger_reason=trigger_reason,
                    world_context=world_context,
                    llm_proposal_fn=None,
                    force_cooldown_bypass=bypass,
                    operation_key=op_key,
                    now_iso=now_iso,
                )
                if not action_result.get("ok"):
                    rc = str(action_result.get("reason_code") or "GENERATION_REFUSED")
                    http_code = 409 if rc in ("GENERATION_COOLDOWN_ACTIVE", "OPERATION_KEY_CONFLICT") else 400
                    return _error_response(http_code, rc, str(action_result.get("detail") or rc), view=side)
                action_result["generation_mode"] = "offline"
                action_result["source_kind"] = "offline"
            else:
                proposal_fn = _llm_adapter.build_live_world_content_llm_proposal_fn()
                resolved_paths = _instance_paths.resolve_instance_paths(root)
                budget = _llm_adapter.open_content_request_budget(resolved_paths.life_state_root)
                try:
                    action_result = _wcg.generate_world_content_batch(
                        root,
                        store_id=store_id.strip(),
                        trigger_reason=trigger_reason,
                        world_context=world_context,
                        llm_proposal_fn=proposal_fn,
                        budget=budget,
                        est_tokens=_wcg.DEFAULT_EST_TOKENS,
                        force_cooldown_bypass=bypass,
                        operation_key=op_key,
                        now_iso=now_iso,
                    )
                except _llm_adapter.WorldContentLLMGenerationError as exc:
                    res_on_exc = getattr(exc, "budget_reservation", None)
                    meta_exc = dict(getattr(proposal_fn, "last_metadata", None) or {})
                    usage_exc = dict(meta_exc.get("usage") or {})
                    if res_on_exc and usage_exc:
                        _llm_adapter.record_actual_usage_in_request_budget(
                            budget,
                            res_on_exc,
                            usage_exc,
                            model_name=str(meta_exc.get("model_name") or proposal_fn.status.get("model_name") or ""),
                            provider=str(meta_exc.get("provider") or proposal_fn.status.get("provider") or ""),
                        )
                    raise
                is_llm_replay = bool(
                    action_result.get("already_applied")
                    or action_result.get("idempotent_replay")
                    or action_result.get("status") == "already_applied"
                )
                meta = dict(getattr(proposal_fn, "last_metadata", None) or {})
                usage = dict(meta.get("usage") or {})
                model_name = str(meta.get("model_name") or proposal_fn.status.get("model_name") or "")
                provider_name = str(meta.get("provider") or proposal_fn.status.get("provider") or "")
                if not is_llm_replay and action_result.get("budget_reservation") and usage:
                    budget_commit = _llm_adapter.record_actual_usage_in_request_budget(
                        budget,
                        action_result.get("budget_reservation"),
                        usage,
                        model_name=model_name,
                        provider=provider_name,
                    )
                    action_result["budget_usage_recorded"] = bool(budget_commit.get("recorded"))

                if not action_result.get("ok"):
                    rc = str(action_result.get("reason_code") or "GENERATION_REFUSED")
                    if rc in ("GENERATION_COOLDOWN_ACTIVE", "OPERATION_KEY_CONFLICT"):
                        return _error_response(409, rc, str(action_result.get("detail") or rc), view=side)
                    if action_result.get("status") == "budget_refused":
                        b_code = 503 if rc == "REQUEST_BUDGET_STATE_CORRUPT" else 429
                        return _error_response(b_code, rc, str(action_result.get("detail") or rc), view=side)
                    return _error_response(
                        502,
                        "LLM_GENERATION_FAILED",
                        f"外接模型返回的商品未通过校验（{rc}）",
                        view=side,
                    )

                if (
                    not is_llm_replay
                    and not (action_result.get("registered_items") or [])
                    and not (action_result.get("registered_events") or [])
                ):
                    return _error_response(
                        502,
                        "LLM_GENERATION_FAILED",
                        "外接模型未生成可用的有效商品",
                        view=side,
                    )

                llm_calls_used = 0 if is_llm_replay else int(action_result.get("llm_calls") or 1)
                action_result["generation_mode"] = "llm"
                action_result["source_kind"] = "llm"
                action_result["llm_calls"] = llm_calls_used
                action_result["model_name"] = model_name
                action_result["provider"] = provider_name
                action_result["usage"] = copy.deepcopy(usage)
                llm_response_meta = {
                    "model_name": model_name,
                    "provider": provider_name,
                    "usage": copy.deepcopy(usage),
                }
        elif action == "create_commitment":
            cmd_payload = {k: v for k, v in payload.items() if k != "action"}
            action_result = _wce.create_commitment(
                root,
                cmd_payload,
                actor_side="subject",
                operation_key=op_key,
                now_iso=now_iso,
            )
        elif action == "contact_npc_or_venue":
            venue_id = payload.get("venue_id")
            if not isinstance(venue_id, str) or not venue_id.strip():
                return _error_response(400, "INVALID_FIELDS", "venue_id is required", view=side)
            extra = payload.get("extra_params")
            if extra is not None and not isinstance(extra, Mapping):
                return _error_response(400, "INVALID_FIELDS", "extra_params must be a JSON object", view=side)
            action_result = _wce.contact_npc_or_venue(
                root,
                venue_id=venue_id.strip(),
                npc_id=payload.get("npc_id"),
                contact_action=str(payload.get("contact_action") or "observe_or_greet"),
                commitment_id=payload.get("commitment_id"),
                actor_side="subject",
                operation_key=op_key,
                now_iso=now_iso,
                extra_params=extra,
            )
        elif action == "advance_due_commitments":
            target_now = payload.get("target_now_iso") or now_iso
            if target_now is not None and (not isinstance(target_now, str) or not target_now.strip()):
                return _error_response(400, "INVALID_FIELDS", "target_now_iso must be a non-empty ISO string", view=side)
            eff_now = str(target_now).strip() if target_now else _wce._resolve_now(None)[1]
            cur_weather = payload.get("current_weather") or payload.get("weather")
            if cur_weather is not None and not isinstance(cur_weather, str):
                return _error_response(400, "INVALID_FIELDS", "current_weather must be a string", view=side)
            action_result = _wce.advance_due_commitments(
                root,
                now_iso=eff_now,
                current_weather=cur_weather,
            )
        else:  # pragma: no cover - defensive fallback
            return _error_response(400, "INVALID_FIELDS", f"Unhandled action: {action!r}", view=side)
    except _wcg.AntiSpoilViolationError as exc:
        return _error_response(400, exc.reason_code, str(exc), view=side)
    except _llm_adapter.WorldContentLLMUnconfiguredError as exc:
        return _error_response(503, "LLM_UNCONFIGURED", exc.detail, view=side)
    except _llm_adapter.WorldContentLLMGenerationError as exc:
        return _error_response(502, "LLM_GENERATION_FAILED", exc.detail, view=side)
    except _wce.WorldCommitmentsError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail, view=side)
    except _wcc.WorldCatalogError as exc:
        rc = str(exc.reason_code or "CATALOG_ERROR")
        http_code = 409 if rc in ("OPERATION_KEY_CONFLICT", "ITEM_OUT_OF_STOCK", "RETRY_WINDOW_EXPIRED") else 400
        return _error_response(http_code, rc, exc.detail, view=side)
    except Exception as exc:  # pragma: no cover - unexpected engine failure
        return _error_response(503, "WORLD_COMMITMENTS_MUTATION_FAILED", str(exc)[:180], view=side)

    is_dup = bool(
        action_result.get("already_applied")
        or action_result.get("idempotent_replay")
        or action_result.get("status") == "already_applied"
        or action_result.get("command_status") == "DUPLICATE"
    )

    fresh_data = _build_world_commitments_data_zero_write(
        root,
        actor_side=side,
        now_iso=now_iso,
    )
    fresh_data["action"] = action
    fresh_data["action_receipt"] = copy.deepcopy(action_result)

    response_body: dict[str, Any] = {
        "ok": True,
        "status": "already_applied" if is_dup else "applied",
        "command_status": "DUPLICATE" if is_dup else "APPLIED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "view": side,
        "perspective": side,
        "zero_write": False,
        "action": action,
        "receipt": copy.deepcopy(action_result),
        "llm_calls": llm_calls_used,
        "data": fresh_data,
    }
    if generation_mode_used is not None:
        response_body["generation_mode"] = generation_mode_used
        response_body["source_kind"] = "llm" if generation_mode_used == "llm" else "offline"
    if llm_response_meta is not None:
        response_body["model_name"] = llm_response_meta["model_name"]
        response_body["provider"] = llm_response_meta["provider"]
        response_body["usage"] = copy.deepcopy(llm_response_meta["usage"])

    return 200, response_body


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "ALL_ALLOWED_ACTIONS",
    "ALLOWED_GENERATION_MODES",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "FORBIDDEN_IDENTITY_KEYS",
    "PARTNER_ALLOWED_ACTIONS",
    "ROUTE_V09_WORLD_COMMITMENTS",
    "SMUGGLED_ACTOR_KEYS",
    "SUBJECT_ONLY_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v09_world_commitments_view",
    "read_v09_world_commitments_view",
]
