#!/usr/bin/env python3
"""v0.3 日常小心愿生命周期（turn_only 自然淡出）与 6 关切多层冲突裁决引擎（第 35 章）。

设计对齐
--------
* 第 35 章（欲望的形成、冲突与消退）：
  - 覆盖 6 大关切域（``CONCERN_DOMAINS``）：
    1. ``comfort``（舒适与身体休整，如解渴、添衣、困倦歇息）
    2. ``exploration``（探索与眼前机会，如翻新书、去街角散步）
    3. ``creation``（创作与持续手头作品，受断点/原子性保护）
    4. ``possession``（拥有与消费取舍，受真实预算信封约束）
    5. ``companionship``（交往、陪伴或独处分寸）
    6. ``intimacy``（私人亲密意向，普通/公开渠道严格隐藏）
  - 4 类合法来源（``ORIGIN_KINDS``）：
    ``body_need`` / ``immediate_opportunity`` / ``sustained_interest`` / ``relational_wish``，
    必须附带可核验 ``origin_refs``，严禁从世界全库凭空生成未见之物的欲望。
  - ``persistence`` 双轨：
    * ``turn_only``：一闪而过的念头只留在当轮窗口，换轮（``advance_turn``）、切换场景
      （``leave_scene``）或超时后静默自然淡出，**绝不落永久积压行、零挫败债、零逾期惩罚**；
    * ``retained``：跨回合保留的关切，可随时主动放下（``drop_concern``）或因来源失效退出，
      完成后亦不附送虚假“幸福分”。
  - 分层协调裁决（``arbitrate_six_concerns``）：严禁总效用分排行榜（``total_utility_score_forbidden=True``），
    按「可执行与权限过滤 -> 当前活动中断代价校验 -> 多时间尺度并存/推迟/礼让休息」给出可解释取舍。

铁律：纯 Python 标准库、零 ``random``、零 LLM 调用、``GET`` 路径 100% 零落盘写入。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONCERN_ITEM_KIND = "proposed.current_concern.v1"
DESIRE_STATE_SCHEMA = "life.v03.everyday_desire.v1"
DESIRE_VIEW_SCHEMA = "life.v03.everyday_desire_projection.v1"
STATE_SCHEMA_VERSION = 1

CONCERN_DOMAINS = (
    "comfort",
    "exploration",
    "creation",
    "possession",
    "companionship",
    "intimacy",
)

ORIGIN_KINDS = (
    "body_need",
    "immediate_opportunity",
    "sustained_interest",
    "relational_wish",
)

URGENCY_LEVELS = ("optional", "timely", "pressing_body")
PERSISTENCE_MODES = ("turn_only", "retained")
ACTION_COST_SCALES = ("micro_inline", "needs_breakpoint", "macro_outing")
CHANNEL_VISIBILITIES = ("public_or_shared", "ordinary_work", "subject_private", "private_intimate")

DEFAULT_TURN_ONLY_TTL_SECONDS = 900
MAX_RETAINED_CONCERNS = 16
MAX_EPHEMERAL_CONCERNS = 8
MAX_IDEMPOTENCY_RECORDS = 64


class EverydayDesireError(ValueError):
    """Structured HTTP-aligned exception for everyday desire lifecycle."""

    def __init__(self, reason_code: str, message: str = "", http_status: int = 400) -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code
        self.http_status = int(http_status)


def everyday_desire_state_path(day_root: Path | str) -> Path:
    return Path(day_root) / "life" / "everyday_desires.json"


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _require_now_dt(now_iso: str | datetime | None) -> datetime:
    if now_iso is None:
        return datetime.now(timezone.utc).astimezone()
    parsed = _parse_iso(now_iso)
    if parsed is None:
        raise EverydayDesireError("INVALID_TIMESTAMP", "Invalid ISO-8601 timestamp.", 400)
    return parsed


def _stable_id(prefix: str, *parts: Any) -> str:
    joined = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def default_everyday_desire_state() -> dict[str, Any]:
    return {
        "schema": DESIRE_STATE_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "turn_index": 0,
        "current_scene_id": "study_desk",
        "updated_at": None,
        "turn_only_buffer": [],
        "retained_concerns": [],
        "naturally_faded_count": 0,
        "satisfied_count": 0,
        "dropped_count": 0,
        "last_arbitration": None,
        "idempotency": {},
    }


def _validate_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("schema") != DESIRE_STATE_SCHEMA:
        raise ValueError("invalid everyday_desire schema")
    rev = raw.get("revision", 0)
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise ValueError("invalid revision")
    base = default_everyday_desire_state()
    base["revision"] = rev
    base["turn_index"] = max(0, int(raw.get("turn_index") or 0))
    base["current_scene_id"] = str(raw.get("current_scene_id") or "study_desk")
    base["updated_at"] = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None
    if isinstance(raw.get("turn_only_buffer"), list):
        base["turn_only_buffer"] = [
            copy.deepcopy(dict(x)) for x in raw["turn_only_buffer"] if isinstance(x, Mapping)
        ][-MAX_EPHEMERAL_CONCERNS:]
    if isinstance(raw.get("retained_concerns"), list):
        base["retained_concerns"] = [
            copy.deepcopy(dict(x)) for x in raw["retained_concerns"] if isinstance(x, Mapping)
        ][-MAX_RETAINED_CONCERNS:]
    base["naturally_faded_count"] = max(0, int(raw.get("naturally_faded_count") or 0))
    base["satisfied_count"] = max(0, int(raw.get("satisfied_count") or 0))
    base["dropped_count"] = max(0, int(raw.get("dropped_count") or 0))
    if isinstance(raw.get("last_arbitration"), Mapping):
        base["last_arbitration"] = copy.deepcopy(dict(raw["last_arbitration"]))
    if isinstance(raw.get("idempotency"), Mapping):
        base["idempotency"] = {
            str(k): copy.deepcopy(v)
            for k, v in list(raw["idempotency"].items())[-MAX_IDEMPOTENCY_RECORDS:]
            if isinstance(k, str) and isinstance(v, Mapping)
        }
    return base


def read_everyday_desire_state(day_root: Path | str) -> tuple[dict[str, Any], str | None]:
    """100% zero-write read of stored everyday_desire state."""
    path = everyday_desire_state_path(day_root)
    if path.exists() and path.is_symlink():
        return default_everyday_desire_state(), "DESIRE_STATE_SYMLINK"
    if not path.exists():
        return default_everyday_desire_state(), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _validate_state(raw), None
    except Exception:
        return default_everyday_desire_state(), "DESIRE_STATE_CORRUPTED"


def _atomic_write_state(day_root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_state(state)
    path = everyday_desire_state_path(day_root)
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise EverydayDesireError("DESIRE_STATE_SYMLINK", "Symlink directory forbidden.", 503)
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise EverydayDesireError("DESIRE_STATE_SYMLINK", "Symlink file forbidden.", 503)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".everyday_desires_", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return normalized


def _is_turn_only_live(
    item: Mapping[str, Any],
    *,
    now_dt: datetime,
    current_turn: int,
    current_scene_id: str,
) -> bool:
    if int(item.get("born_turn", current_turn)) < current_turn:
        return False
    bound_scene = item.get("scene_id")
    if isinstance(bound_scene, str) and bound_scene and bound_scene != current_scene_id:
        return False
    created_dt = _parse_iso(item.get("created_at"))
    ttl_sec = int(item.get("ttl_seconds") or DEFAULT_TURN_ONLY_TTL_SECONDS)
    if created_dt is not None and (now_dt - created_dt).total_seconds() >= ttl_sec:
        return False
    return True


def arbitrate_six_concerns(
    concerns: list[Mapping[str, Any]],
    *,
    foreground_activity: Mapping[str, Any] | None = None,
    body_context: Mapping[str, Any] | None = None,
    budget_context: Mapping[str, Any] | None = None,
    channel_visibility: str = "subject_private",
    adult_gate_open: bool = False,
    ownership_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic Ch. 35 §3 multi-scale 6-concern conflict arbitration (ZERO scalar utility score!)."""
    if channel_visibility not in CHANNEL_VISIBILITIES:
        channel_visibility = "ordinary_work"

    fg = dict(foreground_activity or {})
    fg_active = str(fg.get("status") or ("ACTIVE" if fg.get("activity_id") else "IDLE")).upper() == "ACTIVE"
    fg_interruptibility = str(fg.get("interruptibility") or "CHECKPOINTABLE").upper()
    fg_domain = str(fg.get("domain") or "creation")

    body = dict(body_context or {})
    high_fatigue = bool(body.get("high_fatigue")) or str(body.get("fatigue_band") or "") in ("strong", "high")
    thirst_noticeable = str(body.get("thirst_band") or "") in ("mild", "noticeable", "strong")

    budget = dict(budget_context or {})
    available_jpy = (
        budget.get("available_small_treat_jpy")
        if budget.get("available_small_treat_jpy") is not None
        else budget.get("available_jpy")
    )
    saving_for = budget.get("saving_for_label") or budget.get("saving_for")

    # 18 章 §二 Day2（「历史影响下一次选择」A/B 闭环）：心愿候选层对**已拥有同款**
    # 的 possession 关切做抑制。证据由调用方从经济 owner 的持有事实（OWNED 实例）
    # 只读带来（``owned_catalog_item_ids``），本模块零 I/O、零猜测：focus_ref 或
    # origin_refs 命中已在持有清单里的目录 id，即按 ALREADY_OWNED_SUPPRESSED 过滤。
    owned_catalog_item_ids: set[str] = set()
    if isinstance(ownership_context, Mapping):
        for ref in ownership_context.get("owned_catalog_item_ids") or []:
            text = str(ref or "").strip()
            if text:
                owned_catalog_item_ids.add(text)

    eligible: list[dict[str, Any]] = []
    filtered_out: list[dict[str, Any]] = []

    for raw_c in concerns:
        if not isinstance(raw_c, Mapping):
            continue
        c = copy.deepcopy(dict(raw_c))
        domain = str(c.get("domain") or "")
        cref = str(c.get("concern_ref") or "")
        origin_refs = c.get("origin_refs")
        if domain not in CONCERN_DOMAINS:
            filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "INVALID_DOMAIN"})
            continue
        if not isinstance(origin_refs, list) or not [x for x in origin_refs if str(x).strip()]:
            filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "MISSING_ORIGIN_EVIDENCE"})
            continue
        if domain == "intimacy":
            if not adult_gate_open:
                filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "ADULT_GATE_CLOSED"})
                continue
            if channel_visibility != "private_intimate":
                filtered_out.append(
                    {"concern_ref": cref, "domain": domain, "reason_code": "SENSITIVE_CONCERN_WITHHELD_ON_CHANNEL"}
                )
                continue
        if c.get("reachable", True) is False or c.get("permitted", True) is False:
            filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "UNEXECUTABLE_OR_UNPERMITTED"})
            continue
        if domain == "possession" and owned_catalog_item_ids:
            focus = str(c.get("focus_ref") or "").strip()
            origin_pool = {str(r or "").strip() for r in (c.get("origin_refs") or [])}
            if (focus and focus in owned_catalog_item_ids) or (
                    origin_pool & owned_catalog_item_ids):
                filtered_out.append(
                    {
                        "concern_ref": cref,
                        "domain": domain,
                        "reason_code": "ALREADY_OWNED_SUPPRESSED",
                    }
                )
                continue
        cost_jpy = c.get("estimated_cost_jpy")
        if (
            domain == "possession"
            and isinstance(cost_jpy, int)
            and isinstance(available_jpy, int)
            and cost_jpy > available_jpy
        ):
            filtered_out.append(
                {
                    "concern_ref": cref,
                    "domain": domain,
                    "reason_code": "DEFERRED_BY_BUDGET_ENVELOPE",
                    "saving_for": saving_for,
                }
            )
            continue
        eligible.append(c)

    coexisting_micro: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    chosen_primary: dict[str, Any] | None = None
    coordination_mode = "NO_ACTION_QUIET"

    # Separate inline micro-actions (e.g., sipping water at desk) from macro/breakpoint shifts
    pressing_comfort = [
        c
        for c in eligible
        if c.get("domain") == "comfort"
        and (c.get("urgency") == "pressing_body" or high_fatigue)
        and c.get("cost_scale") != "micro_inline"
    ]
    inline_micro = [
        c
        for c in eligible
        if str(c.get("cost_scale") or "needs_breakpoint") == "micro_inline"
        or (c.get("domain") == "comfort" and thirst_noticeable and c.get("cost_scale") != "macro_outing")
    ]
    macro_candidates = [c for c in eligible if c not in inline_micro]

    if pressing_comfort:
        chosen_primary = pressing_comfort[0]
        coordination_mode = "YIELD_TO_REST_OR_BODY_RECOVERY"
        for c in eligible:
            if c is not chosen_primary:
                if c in inline_micro and c.get("domain") == "comfort":
                    coexisting_micro.append(c)
                else:
                    deferred.append(
                        {
                            "concern_ref": c.get("concern_ref"),
                            "domain": c.get("domain"),
                            "reason_code": "DEFERRED_FOR_REST_RECOVERY",
                            "frustration_debt": 0,
                        }
                    )
    elif fg_active and fg_interruptibility == "ATOMIC":
        # Foreground work is in an uninterruptible atomic step (e.g., wet watercolor wash)
        creation_match = next((c for c in eligible if c.get("domain") == fg_domain), None)
        chosen_primary = creation_match
        coexisting_micro = list(inline_micro)
        coordination_mode = (
            "CONTINUE_ATOMIC_WORK_WITH_INLINE_MICRO"
            if coexisting_micro
            else "CONTINUE_ATOMIC_WORK_DEFER_OTHERS"
        )
        for c in macro_candidates:
            if c is not chosen_primary:
                deferred.append(
                    {
                        "concern_ref": c.get("concern_ref"),
                        "domain": c.get("domain"),
                        "reason_code": "FOREGROUND_ATOMIC_NOT_INTERRUPTED",
                        "frustration_debt": 0,
                    }
                )
    else:
        coexisting_micro = list(inline_micro)
        # Stable domain priority without numeric utility leaderboard:
        # explicit sustained creation/project -> companionship/shared -> exploration -> possession -> intimacy
        domain_order = ("creation", "comfort", "companionship", "exploration", "possession", "intimacy")
        macro_sorted = sorted(
            macro_candidates,
            key=lambda x: (
                0 if x.get("urgency") == "timely" else 1,
                domain_order.index(str(x.get("domain"))) if str(x.get("domain")) in domain_order else 99,
                str(x.get("concern_ref") or ""),
            ),
        )
        if macro_sorted:
            chosen_primary = macro_sorted[0]
            # Check if possession candidate chooses voluntary restraint for a bigger goal
            if (
                chosen_primary.get("domain") == "possession"
                and saving_for
                and chosen_primary.get("urgency") == "optional"
            ):
                deferred.append(
                    {
                        "concern_ref": chosen_primary.get("concern_ref"),
                        "domain": "possession",
                        "reason_code": "VOLUNTARY_RESTRAINT_SAVING_FOR_GOAL",
                        "saving_for": saving_for,
                        "frustration_debt": 0,
                    }
                )
                chosen_primary = macro_sorted[1] if len(macro_sorted) > 1 else None
                coordination_mode = (
                    "SAVE_BREAKPOINT_AND_SWITCH" if chosen_primary else "NO_ACTION_VOLUNTARY_RESTRAINT"
                )
            else:
                coordination_mode = (
                    "COEXIST_PRIMARY_AND_MICRO"
                    if coexisting_micro
                    else ("SAVE_BREAKPOINT_AND_SWITCH" if fg_active else "SELECT_FEASIBLE_CONCERN")
                )
            for c in macro_sorted[1:]:
                if c is not chosen_primary:
                    deferred.append(
                        {
                            "concern_ref": c.get("concern_ref"),
                            "domain": c.get("domain"),
                            "reason_code": "DEFERRED_TO_LATER_WINDOW",
                            "frustration_debt": 0,
                        }
                    )
        elif coexisting_micro:
            coordination_mode = "INLINE_MICRO_ONLY"

    return {
        "coordination_mode": coordination_mode,
        "channel_visibility": channel_visibility,
        "chosen_primary": (
            {
                "concern_ref": chosen_primary.get("concern_ref"),
                "domain": chosen_primary.get("domain"),
                "expression": chosen_primary.get("expression"),
            }
            if chosen_primary
            else None
        ),
        "coexisting_micro_actions": [
            {
                "concern_ref": m.get("concern_ref"),
                "domain": m.get("domain"),
                "expression": m.get("expression"),
            }
            for m in coexisting_micro
        ],
        "deferred_concerns": deferred,
        "filtered_concerns": filtered_out,
        "total_utility_score_forbidden": True,
        "overdue_penalty": 0,
        "frustration_debt": 0,
        "happiness_bonus_on_completion": 0,
    }


def project_everyday_desires(
    day_root: Path | str,
    *,
    now_iso: str | datetime | None = None,
    channel_visibility: str = "subject_private",
    adult_gate_open: bool = False,
) -> dict[str, Any]:
    """100% zero-write read projection of active retained & live turn_only concerns."""
    now_dt = _require_now_dt(now_iso)
    state, err = read_everyday_desire_state(day_root)
    cur_turn = int(state["turn_index"])
    cur_scene = str(state["current_scene_id"])

    live_turn_only: list[dict[str, Any]] = []
    faded_in_projection = 0
    for item in state.get("turn_only_buffer", []):
        if _is_turn_only_live(item, now_dt=now_dt, current_turn=cur_turn, current_scene_id=cur_scene):
            if item.get("domain") == "intimacy" and (
                not adult_gate_open or channel_visibility != "private_intimate"
            ):
                continue
            live_turn_only.append(copy.deepcopy(item))
        else:
            faded_in_projection += 1

    visible_retained: list[dict[str, Any]] = []
    for item in state.get("retained_concerns", []):
        if item.get("status", "ACTIVE") != "ACTIVE":
            continue
        if item.get("domain") == "intimacy" and (
            not adult_gate_open or channel_visibility != "private_intimate"
        ):
            continue
        visible_retained.append(copy.deepcopy(item))

    return {
        "schema": DESIRE_VIEW_SCHEMA,
        "state_schema": DESIRE_STATE_SCHEMA,
        "read_only": True,
        "state_status": "degraded" if err else "ok",
        "state_reason_code": err,
        "revision": state["revision"],
        "turn_index": cur_turn,
        "current_scene_id": cur_scene,
        "observed_at": _format_iso(now_dt),
        "channel_visibility": channel_visibility,
        "turn_only_concerns": live_turn_only,
        "retained_concerns": visible_retained,
        "naturally_faded_count": int(state["naturally_faded_count"]) + faded_in_projection,
        "satisfied_count": int(state["satisfied_count"]),
        "dropped_count": int(state["dropped_count"]),
        "last_arbitration": copy.deepcopy(state.get("last_arbitration")),
        "guarantees": {
            "supported_domains": list(CONCERN_DOMAINS),
            "total_utility_score_forbidden": True,
            "turn_only_writes_permanent_row": False,
            "overdue_penalty": 0,
            "frustration_debt": 0,
            "sensitive_intimacy_isolated": True,
        },
        "llm_calls": 0,
    }


def apply_everyday_desire_action(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: str | datetime | None = None,
    channel_visibility: str = "subject_private",
    adult_gate_open: bool = False,
) -> dict[str, Any]:
    """Deterministic mutation entrypoint for Ch. 35 everyday desires & 6-concern arbitration."""
    if not isinstance(payload, Mapping):
        raise EverydayDesireError("INVALID_PAYLOAD", "Payload must be a JSON object.", 400)
    root = Path(day_root)
    now_dt = _require_now_dt(now_iso)
    now_str = _format_iso(now_dt)

    state, err = read_everyday_desire_state(root)
    if err is not None:
        raise EverydayDesireError(err, "Corrupted everyday_desires state.", 503)

    action = str(payload.get("action") or "").strip()
    if not action:
        raise EverydayDesireError("MISSING_ACTION", "Field 'action' is required.", 400)

    op_key = payload.get("operation_key")
    fp_body = {k: v for k, v in payload.items() if k != "operation_key"}
    fp_hash = hashlib.sha256(
        json.dumps(fp_body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if op_key is not None:
        if not isinstance(op_key, str) or not op_key.strip():
            raise EverydayDesireError("INVALID_OPERATION_KEY", "operation_key must be non-empty.", 400)
        op_key = op_key.strip()
        cached = state["idempotency"].get(op_key)
        if cached is not None:
            if cached.get("fingerprint") != fp_hash:
                raise EverydayDesireError("OPERATION_KEY_CONFLICT", "Conflicting payload for operation_key.", 409)
            proj = project_everyday_desires(
                root,
                now_iso=now_dt,
                channel_visibility=channel_visibility,
                adult_gate_open=adult_gate_open,
            )
            proj["status"] = "already_applied"
            proj["replayed"] = True
            proj["action_receipt"] = copy.deepcopy(cached.get("receipt") or {})
            return proj

    # Sweep expired turn_only items cleanly before mutating
    cur_turn = int(state["turn_index"])
    cur_scene = str(state["current_scene_id"])
    still_live: list[dict[str, Any]] = []
    swept_faded = 0
    for item in state["turn_only_buffer"]:
        if _is_turn_only_live(item, now_dt=now_dt, current_turn=cur_turn, current_scene_id=cur_scene):
            still_live.append(item)
        else:
            swept_faded += 1
    state["turn_only_buffer"] = still_live
    state["naturally_faded_count"] = int(state["naturally_faded_count"]) + swept_faded

    receipt: dict[str, Any]

    if action == "emerge_concern":
        domain = str(payload.get("domain") or "").strip()
        if domain not in CONCERN_DOMAINS:
            raise EverydayDesireError("INVALID_CONCERN_DOMAIN", f"Unsupported domain: {domain}", 400)
        origin_kind = str(payload.get("origin_kind") or "immediate_opportunity").strip()
        if origin_kind not in ORIGIN_KINDS:
            raise EverydayDesireError("INVALID_ORIGIN_KIND", f"Unsupported origin_kind: {origin_kind}", 400)
        origin_refs_raw = payload.get("origin_refs")
        if not isinstance(origin_refs_raw, list):
            raise EverydayDesireError("MISSING_ORIGIN_REFS", "origin_refs must be a non-empty list.", 400)
        origin_refs = [str(r).strip() for r in origin_refs_raw if isinstance(r, str) and str(r).strip()]
        if not origin_refs:
            raise EverydayDesireError("MISSING_ORIGIN_REFS", "Cannot invent desire without valid origin_refs.", 409)

        expression = str(payload.get("expression") or "").strip()[:200]
        if not expression:
            raise EverydayDesireError("EMPTY_EXPRESSION", "expression is required.", 400)
        persistence = str(payload.get("persistence") or "turn_only").strip()
        if persistence not in PERSISTENCE_MODES:
            raise EverydayDesireError("INVALID_PERSISTENCE", f"Unsupported persistence: {persistence}", 400)
        urgency = str(payload.get("urgency") or "optional").strip()
        if urgency not in URGENCY_LEVELS:
            raise EverydayDesireError("INVALID_URGENCY", f"Unsupported urgency: {urgency}", 400)
        cost_scale = str(payload.get("cost_scale") or "needs_breakpoint").strip()
        if cost_scale not in ACTION_COST_SCALES:
            raise EverydayDesireError("INVALID_COST_SCALE", f"Unsupported cost_scale: {cost_scale}", 400)

        focus_ref = str(payload.get("focus_ref") or origin_refs[0]).strip()[:120]
        cref = str(payload.get("concern_ref") or _stable_id("ccn", domain, focus_ref, now_str, state["revision"] + 1))

        concern_doc: dict[str, Any] = {
            "kind": CONCERN_ITEM_KIND,
            "concern_ref": cref,
            "subject_ref": str(payload.get("subject_ref") or "runtime_subject"),
            "domain": domain,
            "origin_kind": origin_kind,
            "focus_ref": focus_ref,
            "origin_refs": origin_refs,
            "expression": expression,
            "urgency": urgency,
            "persistence": persistence,
            "cost_scale": cost_scale,
            "scene_id": cur_scene,
            "born_turn": cur_turn,
            "created_at": now_str,
            "status": "ACTIVE",
            "estimated_cost_jpy": payload.get("estimated_cost_jpy")
            if isinstance(payload.get("estimated_cost_jpy"), int) and not isinstance(payload.get("estimated_cost_jpy"), bool)
            else None,
        }
        if persistence == "turn_only":
            ttl_sec = payload.get("ttl_seconds", DEFAULT_TURN_ONLY_TTL_SECONDS)
            if isinstance(ttl_sec, bool) or not isinstance(ttl_sec, int) or ttl_sec <= 0:
                raise EverydayDesireError("INVALID_TTL", "ttl_seconds must be positive int.", 400)
            concern_doc["ttl_seconds"] = ttl_sec
            # Deduplicate identical focus_ref + domain in same turn
            state["turn_only_buffer"] = [
                x
                for x in state["turn_only_buffer"]
                if not (x.get("domain") == domain and x.get("focus_ref") == focus_ref)
            ]
            state["turn_only_buffer"].append(concern_doc)
            state["turn_only_buffer"] = state["turn_only_buffer"][-MAX_EPHEMERAL_CONCERNS:]
        else:
            state["retained_concerns"] = [
                x
                for x in state["retained_concerns"]
                if not (x.get("domain") == domain and x.get("focus_ref") == focus_ref and x.get("status") == "ACTIVE")
            ]
            state["retained_concerns"].append(concern_doc)
            state["retained_concerns"] = state["retained_concerns"][-MAX_RETAINED_CONCERNS:]

        receipt = {"action": "emerge_concern", "concern": copy.deepcopy(concern_doc)}

    elif action in ("advance_turn", "leave_scene"):
        faded_now = len(state["turn_only_buffer"])
        state["turn_only_buffer"] = []
        state["naturally_faded_count"] = int(state["naturally_faded_count"]) + faded_now
        if action == "advance_turn":
            state["turn_index"] = cur_turn + 1
        else:
            new_scene = str(payload.get("new_scene_id") or "elsewhere").strip() or "elsewhere"
            state["current_scene_id"] = new_scene
        receipt = {
            "action": action,
            "faded_turn_only_count": faded_now,
            "frustration_debt": 0,
            "failure_recorded": False,
        }

    elif action in ("satisfy_concern", "drop_concern", "invalidate_origin"):
        target_ref = str(payload.get("concern_ref") or "").strip()
        origin_ref = str(payload.get("origin_ref") or "").strip()
        matched = 0
        next_turn_only: list[dict[str, Any]] = []
        for item in state["turn_only_buffer"]:
            hit = (target_ref and item.get("concern_ref") == target_ref) or (
                origin_ref and origin_ref in (item.get("origin_refs") or [])
            )
            if hit:
                matched += 1
                if action == "satisfy_concern":
                    state["satisfied_count"] = int(state["satisfied_count"]) + 1
                else:
                    state["naturally_faded_count"] = int(state["naturally_faded_count"]) + 1
            else:
                next_turn_only.append(item)
        state["turn_only_buffer"] = next_turn_only

        next_retained: list[dict[str, Any]] = []
        for item in state["retained_concerns"]:
            hit = (target_ref and item.get("concern_ref") == target_ref) or (
                origin_ref and origin_ref in (item.get("origin_refs") or [])
            )
            if hit:
                matched += 1
                if action == "satisfy_concern":
                    state["satisfied_count"] = int(state["satisfied_count"]) + 1
                else:
                    state["dropped_count"] = int(state["dropped_count"]) + 1
            else:
                next_retained.append(item)
        state["retained_concerns"] = next_retained

        if matched == 0 and target_ref:
            raise EverydayDesireError("CONCERN_NOT_FOUND", f"No active concern {target_ref}.", 404)

        receipt = {
            "action": action,
            "resolved_count": matched,
            "happiness_score_bonus": 0,
            "frustration_debt": 0,
        }

    elif action == "arbitrate_concerns":
        combined = list(state["retained_concerns"]) + list(state["turn_only_buffer"])
        arb = arbitrate_six_concerns(
            combined,
            foreground_activity=payload.get("foreground_activity")
            if isinstance(payload.get("foreground_activity"), Mapping)
            else None,
            body_context=payload.get("body_context")
            if isinstance(payload.get("body_context"), Mapping)
            else None,
            budget_context=payload.get("budget_context")
            if isinstance(payload.get("budget_context"), Mapping)
            else None,
            ownership_context=payload.get("ownership_context")
            if isinstance(payload.get("ownership_context"), Mapping)
            else None,
            channel_visibility=str(payload.get("channel_visibility") or channel_visibility),
            adult_gate_open=bool(payload.get("adult_gate_open", adult_gate_open)),
        )
        state["last_arbitration"] = copy.deepcopy(arb)
        receipt = {"action": "arbitrate_concerns", "arbitration": arb}

    else:
        raise EverydayDesireError("UNKNOWN_ACTION", f"Unsupported everyday_desire action: {action}", 400)

    state["revision"] = int(state["revision"]) + 1
    state["updated_at"] = now_str
    if op_key is not None:
        state["idempotency"][op_key] = {
            "fingerprint": fp_hash,
            "receipt": copy.deepcopy(receipt),
        }
    _atomic_write_state(root, state)

    eff_vis = str(payload.get("channel_visibility") or channel_visibility)
    eff_gate = bool(payload.get("adult_gate_open", adult_gate_open))
    proj = project_everyday_desires(
        root,
        now_iso=now_dt,
        channel_visibility=eff_vis,
        adult_gate_open=eff_gate,
    )
    proj["read_only"] = False
    proj["status"] = "applied"
    proj["replayed"] = False
    proj["action_receipt"] = receipt
    return proj


__all__ = [
    "ACTION_COST_SCALES",
    "CHANNEL_VISIBILITIES",
    "CONCERN_DOMAINS",
    "CONCERN_ITEM_KIND",
    "DEFAULT_TURN_ONLY_TTL_SECONDS",
    "DESIRE_STATE_SCHEMA",
    "DESIRE_VIEW_SCHEMA",
    "EverydayDesireError",
    "ORIGIN_KINDS",
    "PERSISTENCE_MODES",
    "URGENCY_LEVELS",
    "apply_everyday_desire_action",
    "arbitrate_six_concerns",
    "default_everyday_desire_state",
    "everyday_desire_state_path",
    "project_everyday_desires",
    "read_everyday_desire_state",
]
