#!/usr/bin/env python3
"""HTTP 视图与动作闸门封装：``/api/phone/v03/ecology``（v3.0 经济独立 / 生病自愈 / 现实桥梁）。

职责边界：
1. ``read_v03_ecology_view``：
   - 100% 零磁盘写入（零新建目录、零临时文件、零 mtime 变动）；
   - 按 ``has_subject_capability`` 严格区分 ``"her_private"`` 与 ``"partner_default"`` 视角，
     伴侣默认视角自动脱敏未共享攒钱目标、私密权衡草稿与内部备注。
2. ``mutate_v03_ecology_view``：
   - 逐 ``action`` 严格执行允许字段白名单，任何多余未知字段、伪造身份字段或空白外籍字段一律返回
     ``(400, {"ok": False, ..., "reason_code": "INVALID_FIELDS"})``；
   - 严格隔离 ``actor_side``：``"partner"`` 允许 ``offer_stipend``、``withdraw_stipend``、
     ``offer_sick_care``、``push_external_fact``、``ack_outbound_read``、``configure_outbound_quota``，
     越权调用主体专属动作一律返回 ``(403, {"ok": False, ..., "reason_code": "FORBIDDEN_ACTOR_SIDE"})``。
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_representation_lease_forget as _rlf
from components.world.scripts import v03_economy_illness_bridge as bridge

V03_ECOLOGY_SCHEMA = "phone.v03.ecology.v1"

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
    "expected_revision",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    # Module A: Economy & Independent Consumption (Ch. 04 + ADR-039)
    "complete_independent_work": COMMON_COMMAND_FIELDS | frozenset({
        "job_kind",
        "work_ref",
        "amount_cents",
        "title",
        "hours_spent",
        "private_note",
    }),
    "configure_living_floor": COMMON_COMMAND_FIELDS | frozenset({
        "living_floor_cents",
    }),
    "set_savings_goal": COMMON_COMMAND_FIELDS | frozenset({
        "goal_id",
        "item_title",
        "target_cents",
        "allocated_cents",
        "category",
        "shared_with_partner",
        "private_note",
        "gave_up_item",
        "saved_cents",
    }),
    "deliberate_purchase": COMMON_COMMAND_FIELDS | frozenset({
        "item_title",
        "price_cents",
        "verdict",
        "goal_id",
        "commit_spend",
        "shared_with_partner",
        "private_note",
    }),
    "offer_stipend": COMMON_COMMAND_FIELDS | frozenset({
        "amount_cents",
        "note",
        "context_tag",
    }),
    "withdraw_stipend": COMMON_COMMAND_FIELDS | frozenset({
        "escrow_id",
        "stipend_id",
    }),
    "decide_stipend": COMMON_COMMAND_FIELDS | frozenset({
        "escrow_id",
        "decision",
        "reason_note",
        "private_note",
    }),
    "spend_from_stipend": COMMON_COMMAND_FIELDS | frozenset({
        "item_title",
        "amount_cents",
        "note",
        "shared_with_partner",
    }),
    "offer_item_gift": COMMON_COMMAND_FIELDS | frozenset({
        "item_label_cn",
        "item_kind",
        "estimated_jpy",
        "target_zone_id",
        "note",
        "context_tag",
    }),
    "withdraw_item_gift": COMMON_COMMAND_FIELDS | frozenset({
        "gift_id",
    }),
    "decide_item_gift": COMMON_COMMAND_FIELDS | frozenset({
        "gift_id",
        "decision",
        "note",
        "private_note",
    }),
    # Module B: Embodied Strain & Multi-Day Illness Self-Healing (Ch. 05 + ADR-041)
    "record_strain_or_rest": COMMON_COMMAND_FIELDS | frozenset({
        "event_kind",
        "intensity",
        "duration_hours",
        "unchanged_damp_clothes",
        "private_note",
    }),
    "advance_illness_recovery": COMMON_COMMAND_FIELDS | frozenset({
        "target_now_iso",
        "rest_mode",
    }),
    "offer_sick_care": COMMON_COMMAND_FIELDS | frozenset({
        "care_kind",
        "note",
    }),
    # Module C: Reality Bridge ExternalClaim & Outbound Gate (Ch. 09-11 + ADR-043)
    "push_external_fact": COMMON_COMMAND_FIELDS | frozenset({
        "category",
        "source",
        "summary",
        "raw_quote",
        "observed_at",
        "valid_until",
        "truth",
        "provenance",
        "payload_ref",
    }),
    "configure_outbound_quota": COMMON_COMMAND_FIELDS | frozenset({
        "daily_proactive_quota",
        "daily_proactive_cap",
        "weekly_proactive_cap",
        "monthly_letter_cap",
        "daily_max_messages",
        "daily_max",
        "daily_limit",
        "desktop_note_weekly_cap",
        "min_outbound_interval_hours",
        "quiet_start_hhmm",
        "quiet_end_hhmm",
        "quiet_hours_start",
        "quiet_hours_end",
        "backoff_streak_limit",
        "enabled_channels",
    }),
    "send_contextual_reply": COMMON_COMMAND_FIELDS | frozenset({
        "reply_kind",
        "channel",
        "text",
        "conversation_ref",
        "reminder_ref",
    }),
    "attempt_proactive_outbound": COMMON_COMMAND_FIELDS | frozenset({
        "channel",
        "text",
        "reason_kind",
        "recipient_ref",
        "simulated_transport_outcome",
        "private_note",
    }),
    "reconcile_outbound": COMMON_COMMAND_FIELDS | frozenset({
        "outbound_id",
        "target_operation_key",
        "resolution",
    }),
    "ack_outbound_read": COMMON_COMMAND_FIELDS | frozenset({
        "outbound_id",
        "record_id",
        "note_id",
        "id",
    }),
    "withdraw_expired_outbound": COMMON_COMMAND_FIELDS,
}

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "offer_stipend",
    "withdraw_stipend",
    "offer_item_gift",
    "withdraw_item_gift",
    "offer_sick_care",
    "push_external_fact",
    "ack_outbound_read",
    "configure_outbound_quota",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "complete_independent_work",
    "configure_living_floor",
    "set_savings_goal",
    "deliberate_purchase",
    "decide_stipend",
    "spend_from_stipend",
    "decide_item_gift",
    "record_strain_or_rest",
    "advance_illness_recovery",
    "push_external_fact",
    "configure_outbound_quota",
    "send_contextual_reply",
    "attempt_proactive_outbound",
    "reconcile_outbound",
    "withdraw_expired_outbound",
})


def _error_response(
    status_code: int,
    reason_code: str,
    *,
    details: Optional[Mapping[str, Any]] = None,
) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {
        "ok": False,
        "status": "rejected",
        "command_status": "REJECTED",
        "schema": V03_ECOLOGY_SCHEMA,
        "reason_code": str(reason_code),
        "llm_calls": 0,
    }
    if details:
        body.update(dict(details))
    return int(status_code), body


def _apply_forget_barrier(
    envelope: Mapping[str, Any],
    barrier: Mapping[str, Any],
) -> dict[str, Any]:
    """Filter economy, illness, and reality-bridge collections through the zero-write forget barrier."""
    out = copy.deepcopy(dict(envelope))
    data = out.get("data")
    if not isinstance(data, dict):
        return out
    fail_closed = bool(barrier.get("fail_closed"))

    eco = data.get("economy")
    if isinstance(eco, dict):
        for eco_list in (
            "savings_goals",
            "budget_deliberations",
            "partner_stipend_escrow",
            "item_gift_escrow",
            "ledger_entries",
        ):
            if isinstance(eco.get(eco_list), list):
                eco[eco_list] = _rlf.filter_collection_by_barrier(eco[eco_list], barrier)
        if isinstance(eco.get("item_gift_escrow"), list) and "item_gift_escrow_pending_count" in eco:
            eco["item_gift_escrow_pending_count"] = sum(
                1
                for item in eco["item_gift_escrow"]
                if isinstance(item, Mapping)
                and str(item.get("status") or "").strip().lower() in ("offered", "pending")
            )

    ill = data.get("illness")
    if isinstance(ill, dict):
        for ill_list in ("strain_events", "archived_episodes", "recovery_supports", "care_logs"):
            if isinstance(ill.get(ill_list), list):
                ill[ill_list] = _rlf.filter_collection_by_barrier(ill[ill_list], barrier)
        ep_id = ill.get("episode_id")
        if fail_closed or (isinstance(ep_id, str) and ep_id.strip() and _rlf.is_entity_forget_blocked(ill, barrier)):
            ill["episode_id"] = None
            ill["episode_origin"] = None
            ill["episode_opened_at"] = None
            if isinstance(ill.get("strain_events"), list):
                ill["strain_events"] = []
            if isinstance(ill.get("archived_episodes"), list):
                ill["archived_episodes"] = []

    rb = data.get("reality_bridge")
    if isinstance(rb, dict):
        for rb_list in ("external_claims", "slow_letters"):
            if isinstance(rb.get(rb_list), list):
                rb[rb_list] = _rlf.filter_collection_by_barrier(rb[rb_list], barrier)
        og = rb.get("outbound_gate")
        if isinstance(og, dict):
            for og_list in ("outbound_records", "contextual_replies"):
                if isinstance(og.get(og_list), list):
                    og[og_list] = _rlf.filter_collection_by_barrier(og[og_list], barrier)

    return out


def read_v03_ecology_view(
    day_root: Path,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Read v0.3/v3.0 ecology projection with 100% zero disk writes and unified forget barrier enforcement."""
    try:
        barrier = _rlf.load_unified_forget_barrier_readonly(day_root)
        envelope = _apply_forget_barrier(
            bridge.project_v03_ecology(
                Path(day_root),
                has_subject_capability=bool(has_subject_capability),
                now_iso=now_iso,
            ),
            barrier,
        )
        return 200, envelope
    except bridge.V03EcologyError as exc:
        return _error_response(exc.status_code, exc.reason_code, details=exc.details)


def mutate_v03_ecology_view(
    day_root: Path,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Validate strict field whitelist & actor_side RBAC, then apply one ecology command."""
    if not isinstance(payload, Mapping):
        return _error_response(400, "INVALID_FIELDS", details={"invalid_fields": ["<root>"]})

    # 1. Detect blank/non-string field names immediately
    bad_key_names = [str(k) for k in payload.keys() if not isinstance(k, str) or not k.strip()]
    if bad_key_names:
        return _error_response(400, "INVALID_FIELDS", details={"invalid_fields": bad_key_names})

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        return _error_response(400, "INVALID_ACTION")
    action = raw_action.strip()

    if action not in ACTION_ALLOWED_FIELDS:
        return _error_response(400, "INVALID_ACTION", details={"action": action})

    # 2. Strict per-action field whitelist (extra unknown fields, mood_delta, or empty foreign/optional strings -> 400 INVALID_FIELDS)
    allowed_keys = ACTION_ALLOWED_FIELDS[action]
    invalid_fields: list[str] = []
    mood_smuggle = False
    for key, val in payload.items():
        if key.strip().lower() in bridge.FORBIDDEN_MOOD_KEYS:
            mood_smuggle = True
        if key not in allowed_keys:
            invalid_fields.append(key)
            continue
        if key == "enabled_channels" and isinstance(val, list):
            continue
        if val is None or (isinstance(val, str) and not val.strip()) or val in ([], {}):
            invalid_fields.append(key)

    if invalid_fields:
        extra_meta: dict[str, Any] = {"invalid_fields": sorted(set(invalid_fields))}
        if mood_smuggle:
            extra_meta["mood_delta_forbidden"] = True
        return _error_response(400, "INVALID_FIELDS", details=extra_meta)

    # 3. Strict actor_side authorization gate (403 FORBIDDEN_ACTOR_SIDE)
    if not isinstance(actor_side, str):
        return _error_response(403, "FORBIDDEN_ACTOR_SIDE")
    norm_actor = actor_side.strip().lower()
    if norm_actor == "partner":
        if action not in PARTNER_ALLOWED_ACTIONS:
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                details={"actor_side": "partner", "action": action},
            )
    elif norm_actor in {"subject", "her"}:
        if action not in SUBJECT_ALLOWED_ACTIONS:
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                details={"actor_side": norm_actor, "action": action},
            )
        norm_actor = "subject"
    else:
        return _error_response(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            details={"actor_side": actor_side, "action": action},
        )

    # 4. Dispatch into domain engine
    try:
        result = bridge.apply_v03_ecology_command(
            Path(day_root),
            payload,
            actor_side=norm_actor,
            now_iso=now_iso,
        )
        return 200, result
    except bridge.V03EcologyError as exc:
        return _error_response(exc.status_code, exc.reason_code, details=exc.details)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "V03_ECOLOGY_SCHEMA",
    "mutate_v03_ecology_view",
    "read_v03_ecology_view",
]
