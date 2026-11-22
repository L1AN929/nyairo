"""0.2 第 10/11 章：共同认知账本（CommonGround）与未见证挪动（UNSEEN_MOVE）寻物提问闭环。

核心契约（对齐总体设计第 10 章、第 11 章与第 15 章 §1）：
1. 共同认知三态闭集：
   - ``SHARED_WITH_PARTNER``：已与伴侣共同经历、当面聊过或明确达成一致；后续引用共同背景而非当成初见重讲；
   - ``PRIVATE_UNSHARED``：主体独处发生的私下小事/草稿/念头，伴侣尚不知情；严禁默认伴侣全知，伴侣读侧严格过滤；
   - ``PENDING_CLARIFICATION``：消息未获确认、约定改期使旧共识失效、或未见证挪动引发认知悬置，待寻找或自然开口确认。
2. 沟通认知链严格区分：``SENT != DELIVERED != READ != ACKNOWLEDGED != UNDERSTOOD != AGREED``。
   未到 ``AGREED`` 的计划提议停留在 ``PENDING_CLARIFICATION``；修改已约定计划立即令旧确认过期。
3. 未见证挪动（``UNSEEN_MOVE``）反全知闭环：
   - 伴侣趁主体不在场把物件从 A 挪到 B 时，世界真值 ``actual_spot=B``，但主体信念必须保持 ``subject_believed_spot=A``；
   - 主体需要使用该物件时，第一步必先按旧印象去 A 摸空（``MISSED_AT_REMEMBERED_SPOT``）；
   - 摸空后转入 ``PENDING_CLARIFICATION``，衔接“顺手在附近找一圈（``search_nearby_spots``）”或“自然开口问伴侣（``prepare_partner_inquiry``）”；
   - 找到或伴侣告知后，更新 ``subject_believed_spot=B`` 并收口为 ``SHARED_WITH_PARTNER``。
4. GET / 认知摘要 100% 零磁盘写入。
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
import uuid

SCHEMA_VERSION = "nya02.common_ground.v1"
COMMON_GROUND_REL_PATH = "memory/v02_common_ground_state.json"

SHARED_WITH_PARTNER = "SHARED_WITH_PARTNER"
PRIVATE_UNSHARED = "PRIVATE_UNSHARED"
PENDING_CLARIFICATION = "PENDING_CLARIFICATION"
GROUND_STATUSES: tuple[str, ...] = (
    SHARED_WITH_PARTNER,
    PRIVATE_UNSHARED,
    PENDING_CLARIFICATION,
)

DELIVERY_SENT = "SENT"
DELIVERY_DELIVERED = "DELIVERED"
DELIVERY_READ = "READ"
DELIVERY_ACKNOWLEDGED = "ACKNOWLEDGED"
DELIVERY_UNDERSTOOD = "UNDERSTOOD"
DELIVERY_AGREED = "AGREED"
DELIVERY_STATES: tuple[str, ...] = (
    DELIVERY_SENT,
    DELIVERY_DELIVERED,
    DELIVERY_READ,
    DELIVERY_ACKNOWLEDGED,
    DELIVERY_UNDERSTOOD,
    DELIVERY_AGREED,
)

EVENT_UNSEEN_MOVE = "UNSEEN_MOVE"
DISCREPANCY_ALIGNED = "ALIGNED"
DISCREPANCY_UNWITNESSED = "UNWITNESSED_DIVERGENCE"
DISCREPANCY_MISSED_SEEKING = "MISSED_SEEKING_OR_ASKING"
DISCREPANCY_RESOLVED_SEARCH = "RESOLVED_BY_SEARCH"
DISCREPANCY_RESOLVED_PARTNER = "RESOLVED_WITH_PARTNER"

OUTCOME_FOUND_AT_EXPECTED = "FOUND_AT_REMEMBERED_SPOT"
OUTCOME_MISSED_AT_EXPECTED = "MISSED_AT_REMEMBERED_SPOT"
OUTCOME_FOUND_BY_SEARCH = "FOUND_BY_LOCAL_SEARCH"
OUTCOME_STILL_MISSING_ASK = "NOT_FOUND_NEARBY_NEED_CLARIFICATION"
OUTCOME_CLARIFIED_BY_PARTNER = "CLARIFIED_BY_PARTNER"

MISSED_STATUS_UNSEEN = "MISSED_UNSEEN"
MISSED_STATUS_MATERIAL_SUPPLIED = "MATERIAL_SUPPLIED"
MISSED_STATUS_RECONNECTED = "RECONNECTED_NOW_KNOWN"
MISSED_MOMENT_STATUSES: tuple[str, ...] = (
    MISSED_STATUS_UNSEEN,
    MISSED_STATUS_MATERIAL_SUPPLIED,
    MISSED_STATUS_RECONNECTED,
)

CONTRIB_RESOLUTIONS: frozenset[str] = frozenset({
    "accept_diff",
    "propose_edit",
    "keep_own_branch",
})
CONTRIB_STATUS_PENDING = "PENDING_REVIEW"
CONTRIB_STATUS_BY_RESOLUTION: dict[str, str] = {
    "accept_diff": "ACCEPTED_DIFF",
    "propose_edit": "PROPOSED_EDIT",
    "keep_own_branch": "KEPT_OWN_BRANCH",
}

VALID_ACTOR_SIDES: frozenset[str] = frozenset({"subject", "partner"})
MAX_OPERATION_LEDGER = 256
PHONE_COMMON_GROUND_REL_PATH = ("phone", "v02_common_ground.json")
V04_NEIGHBORHOOD_REL_PATH = ("world", "v04_neighborhood_state.json")

DEFAULT_BUSINESS_TZ = timezone(timedelta(hours=8))


def _now_iso(now_iso: str | None = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        return now_iso.strip()
    return datetime.now(DEFAULT_BUSINESS_TZ).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:10]}.tmp")
    body = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _state_path(day_root: Path | str) -> Path:
    return Path(day_root) / COMMON_GROUND_REL_PATH


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "items": {},
        "object_beliefs": {},
        "unseen_move_log": [],
        "dual_rationale_decisions": {},
        "missed_moment_reconnections": {},
        "async_coauthor_contributions": {},
        "receipts": [],
        "operation_ledger": {},
        "updated_at": None,
    }


def _load_state_readonly(day_root: Path | str) -> dict[str, Any]:
    path = _state_path(day_root)
    try:
        if path.is_symlink() or not path.is_file():
            return _default_state()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _default_state()
    if not isinstance(raw, dict):
        return _default_state()
    state = _default_state()
    if isinstance(raw.get("items"), dict):
        state["items"] = dict(raw["items"])
    if isinstance(raw.get("object_beliefs"), dict):
        state["object_beliefs"] = dict(raw["object_beliefs"])
    if isinstance(raw.get("unseen_move_log"), list):
        state["unseen_move_log"] = list(raw["unseen_move_log"])
    if isinstance(raw.get("dual_rationale_decisions"), dict):
        state["dual_rationale_decisions"] = dict(raw["dual_rationale_decisions"])
    if isinstance(raw.get("missed_moment_reconnections"), dict):
        state["missed_moment_reconnections"] = dict(raw["missed_moment_reconnections"])
    if isinstance(raw.get("async_coauthor_contributions"), dict):
        state["async_coauthor_contributions"] = dict(raw["async_coauthor_contributions"])
    if isinstance(raw.get("receipts"), list):
        state["receipts"] = list(raw["receipts"])
    if isinstance(raw.get("operation_ledger"), dict):
        state["operation_ledger"] = dict(raw["operation_ledger"])
    state["updated_at"] = raw.get("updated_at")
    return state


def record_ground_item(
    day_root: Path | str,
    *,
    item_id: str | None = None,
    topic: str,
    summary: str,
    ground_status: str = PRIVATE_UNSHARED,
    item_kind: str = "experience",
    delivery_state: str | None = None,
    requires_agreement: bool = False,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """登记一条经历、话题或约定的共同认知状态。"""
    stamp = _now_iso(now_iso)
    gid = str(item_id or f"cg_{uuid.uuid4().hex[:10]}").strip()
    clean_topic = str(topic or "").strip()
    clean_summary = str(summary or "").strip()
    if not gid or not clean_topic:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_GROUND_TOPIC"}

    status = (
        ground_status
        if ground_status in GROUND_STATUSES
        else PRIVATE_UNSHARED
    )
    d_state = (
        delivery_state
        if delivery_state in DELIVERY_STATES
        else (DELIVERY_AGREED if status == SHARED_WITH_PARTNER and not requires_agreement else None)
    )
    # 契约硬约束：需要双方同意的约定/提议，只要尚未 AGREED，哪怕已读/已收到也只能算 PENDING_CLARIFICATION
    if requires_agreement and d_state != DELIVERY_AGREED:
        status = PENDING_CLARIFICATION

    state = _load_state_readonly(day_root)
    existing = dict(state["items"].get(gid) or {})
    version = int(existing.get("version") or 0) + 1
    record = {
        "item_id": gid,
        "topic": clean_topic,
        "summary": clean_summary or str(existing.get("summary") or ""),
        "item_kind": str(item_kind or existing.get("item_kind") or "experience").strip(),
        "ground_status": status,
        "delivery_state": d_state,
        "requires_agreement": bool(requires_agreement),
        "partner_knows": status == SHARED_WITH_PARTNER,
        "agreement_expired": False,
        "previous_agreed_summary": existing.get("previous_agreed_summary"),
        "version": version,
        "updated_at": stamp,
    }
    state["items"][gid] = record
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {"ok": True, "status_code": 200, "item": record}


def advance_communication_receipt(
    day_root: Path | str,
    *,
    item_id: str,
    delivery_state: str,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """推进消息认知链（SENT -> DELIVERED -> READ -> ACKNOWLEDGED -> UNDERSTOOD -> AGREED）。"""
    gid = str(item_id or "").strip()
    if delivery_state not in DELIVERY_STATES:
        return {"ok": False, "status_code": 400, "error_code": "INVALID_DELIVERY_STATE"}
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    item = dict(state["items"].get(gid) or {})
    if not item:
        return {"ok": False, "status_code": 404, "error_code": "GROUND_ITEM_NOT_FOUND"}

    requires_agreement = bool(item.get("requires_agreement", False))
    item["delivery_state"] = delivery_state
    if requires_agreement:
        if delivery_state == DELIVERY_AGREED:
            item["ground_status"] = SHARED_WITH_PARTNER
            item["partner_knows"] = True
            item["agreement_expired"] = False
        else:
            item["ground_status"] = PENDING_CLARIFICATION
            item["partner_knows"] = delivery_state in {
                DELIVERY_READ,
                DELIVERY_ACKNOWLEDGED,
                DELIVERY_UNDERSTOOD,
            }
    else:
        if delivery_state in {DELIVERY_UNDERSTOOD, DELIVERY_AGREED}:
            item["ground_status"] = SHARED_WITH_PARTNER
            item["partner_knows"] = True
        else:
            item["ground_status"] = PENDING_CLARIFICATION
            item["partner_knows"] = delivery_state in {
                DELIVERY_READ,
                DELIVERY_ACKNOWLEDGED,
            }
    item["updated_at"] = stamp
    state["items"][gid] = item
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {"ok": True, "status_code": 200, "item": item}


def mark_shared_with_partner(
    day_root: Path | str,
    *,
    item_id: str,
    shared_note: str = "",
    now_iso: str | None = None,
) -> dict[str, Any]:
    """把原本私下发生的事在真实沟通后推进为 SHARED_WITH_PARTNER。"""
    gid = str(item_id or "").strip()
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    item = dict(state["items"].get(gid) or {})
    if not item:
        return {"ok": False, "status_code": 404, "error_code": "GROUND_ITEM_NOT_FOUND"}
    item["ground_status"] = SHARED_WITH_PARTNER
    item["delivery_state"] = DELIVERY_AGREED if item.get("requires_agreement") else DELIVERY_UNDERSTOOD
    item["partner_knows"] = True
    item["agreement_expired"] = False
    if shared_note.strip():
        item["shared_note"] = shared_note.strip()
    item["shared_at"] = stamp
    item["updated_at"] = stamp
    state["items"][gid] = item
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {"ok": True, "status_code": 200, "item": item}


def revise_shared_plan(
    day_root: Path | str,
    *,
    item_id: str,
    revised_summary: str,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """约定改期或变更内容：旧确认立即失效，退回 PENDING_CLARIFICATION 等待伴侣重新确认。"""
    gid = str(item_id or "").strip()
    clean_revised = str(revised_summary or "").strip()
    if not gid or not clean_revised:
        return {"ok": False, "status_code": 400, "error_code": "INVALID_PLAN_REVISION"}
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    item = dict(state["items"].get(gid) or {})
    if not item:
        return {"ok": False, "status_code": 404, "error_code": "GROUND_ITEM_NOT_FOUND"}

    if item.get("ground_status") == SHARED_WITH_PARTNER:
        item["previous_agreed_summary"] = item.get("summary")
    item["summary"] = clean_revised
    item["requires_agreement"] = True
    item["ground_status"] = PENDING_CLARIFICATION
    item["delivery_state"] = DELIVERY_SENT
    item["partner_knows"] = False
    item["agreement_expired"] = True
    item["version"] = int(item.get("version") or 1) + 1
    item["updated_at"] = stamp
    state["items"][gid] = item
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {"ok": True, "status_code": 200, "item": item}


def register_object_belief(
    day_root: Path | str,
    *,
    object_id: str,
    object_label: str = "",
    spot: str,
    shared_with_partner: bool = False,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """登记日常物件的初始安放位置与主体印象。"""
    oid = str(object_id or "").strip()
    clean_spot = str(spot or "").strip()
    if not oid or not clean_spot:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_OBJECT_OR_SPOT"}
    stamp = _now_iso(now_iso)
    label = str(object_label or oid).strip()

    state = _load_state_readonly(day_root)
    record = {
        "object_id": oid,
        "object_label": label,
        "subject_believed_spot": clean_spot,
        "actual_spot": clean_spot,
        "discrepancy_state": DISCREPANCY_ALIGNED,
        "last_witnessed_at": stamp,
        "missed_attempts": 0,
        "inspected_spots": [],
        "pending_ground_item_id": None,
        "natural_question_prompt": None,
        "updated_at": stamp,
    }
    state["object_beliefs"][oid] = record
    if shared_with_partner:
        gid = f"obj_ground:{oid}"
        state["items"][gid] = {
            "item_id": gid,
            "topic": f"物件位置：{label}",
            "summary": f"{label}平时放在{clean_spot}。",
            "item_kind": "object_location",
            "ground_status": SHARED_WITH_PARTNER,
            "delivery_state": DELIVERY_UNDERSTOOD,
            "requires_agreement": False,
            "partner_knows": True,
            "agreement_expired": False,
            "version": 1,
            "updated_at": stamp,
        }
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {"ok": True, "status_code": 200, "object_belief": record}


def record_partner_unseen_move(
    day_root: Path | str,
    *,
    object_id: str,
    new_spot: str,
    object_label: str = "",
    moved_by: str = "partner",
    witnessed_by_subject: bool = False,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """伴侣趁主体不在场挪动物件（UNSEEN_MOVE）：只改世界真值，绝不偷改主体的旧位置印象！"""
    oid = str(object_id or "").strip()
    target_spot = str(new_spot or "").strip()
    if not oid or not target_spot:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_OBJECT_OR_NEW_SPOT"}
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    existing = dict(state["object_beliefs"].get(oid) or {})
    old_believed = str(existing.get("subject_believed_spot") or "original_spot").strip()
    old_actual = str(existing.get("actual_spot") or old_believed).strip()
    label = str(object_label or existing.get("object_label") or oid).strip()

    if witnessed_by_subject:
        believed_after = target_spot
        discrepancy = DISCREPANCY_ALIGNED
    else:
        # 核心反全知防线：未亲眼见证时，主体印象必须留在旧位置
        believed_after = old_believed
        discrepancy = (
            DISCREPANCY_UNWITNESSED
            if target_spot != old_believed
            else DISCREPANCY_ALIGNED
        )

    updated = {
        "object_id": oid,
        "object_label": label,
        "subject_believed_spot": believed_after,
        "actual_spot": target_spot,
        "discrepancy_state": discrepancy,
        "last_witnessed_at": stamp if witnessed_by_subject else existing.get("last_witnessed_at"),
        "missed_attempts": int(existing.get("missed_attempts") or 0),
        "inspected_spots": list(existing.get("inspected_spots") or []),
        "pending_ground_item_id": existing.get("pending_ground_item_id"),
        "natural_question_prompt": existing.get("natural_question_prompt"),
        "last_unseen_move": (
            {
                "event_kind": EVENT_UNSEEN_MOVE,
                "from_spot": old_actual,
                "to_spot": target_spot,
                "moved_by": str(moved_by or "partner"),
                "moved_at": stamp,
            }
            if not witnessed_by_subject
            else None
        ),
        "updated_at": stamp,
    }
    state["object_beliefs"][oid] = updated
    if not witnessed_by_subject:
        log = list(state["unseen_move_log"])[-63:]
        log.append(
            {
                "event_kind": EVENT_UNSEEN_MOVE,
                "object_id": oid,
                "object_label": label,
                "from_spot": old_actual,
                "to_spot": target_spot,
                "subject_still_believes": believed_after,
                "moved_by": str(moved_by or "partner"),
                "at": stamp,
            }
        )
        state["unseen_move_log"] = log
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "event_kind": EVENT_UNSEEN_MOVE if not witnessed_by_subject else "WITNESSED_MOVE",
        "object_belief": updated,
    }


def attempt_use_or_fetch_object(
    day_root: Path | str,
    *,
    object_id: str,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """主体去拿日常物件：第一步必按自己记忆里的旧位置伸手，若已被挪走则真实摸空并开启寻物/提问悬置。"""
    oid = str(object_id or "").strip()
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    belief = dict(state["object_beliefs"].get(oid) or {})
    if not belief:
        return {"ok": False, "status_code": 404, "error_code": "OBJECT_BELIEF_NOT_FOUND"}

    believed_spot = str(belief.get("subject_believed_spot") or "").strip()
    actual_spot = str(belief.get("actual_spot") or "").strip()
    label = str(belief.get("object_label") or oid).strip()

    if believed_spot == actual_spot:
        belief["discrepancy_state"] = DISCREPANCY_ALIGNED
        belief["last_witnessed_at"] = stamp
        belief["updated_at"] = stamp
        state["object_beliefs"][oid] = belief
        state["updated_at"] = stamp
        _atomic_write_json(_state_path(day_root), state)
        return {
            "ok": True,
            "status_code": 200,
            "outcome": OUTCOME_FOUND_AT_EXPECTED,
            "checked_spot": believed_spot,
            "should_search_or_ask": False,
            "object_belief": belief,
        }

    # 按旧印象摸空！此时主体只知道“旧位置空了”，绝不知道新位置 actual_spot 在哪
    inspected = list(belief.get("inspected_spots") or [])
    if believed_spot and believed_spot not in inspected:
        inspected.append(believed_spot)
    question = f"刚才去{believed_spot}拿{label}结果摸空了，是你顺手收到别的地方了吗？"
    gid = f"clarify_unseen_move:{oid}"

    belief["missed_attempts"] = int(belief.get("missed_attempts") or 0) + 1
    belief["inspected_spots"] = inspected
    belief["discrepancy_state"] = DISCREPANCY_MISSED_SEEKING
    belief["pending_ground_item_id"] = gid
    belief["natural_question_prompt"] = question
    belief["updated_at"] = stamp
    state["object_beliefs"][oid] = belief

    ground_item = {
        "item_id": gid,
        "topic": f"寻物待确认：{label}",
        "summary": f"去原本记得的「{believed_spot}」找{label}时发现不在原位，准备附近找找或问问伴侣。",
        "item_kind": "unseen_move_clarification",
        "object_id": oid,
        "remembered_spot": believed_spot,
        "ground_status": PENDING_CLARIFICATION,
        "delivery_state": None,
        "requires_agreement": False,
        "partner_knows": False,
        "natural_question_prompt": question,
        "intent_kind": "CLARIFICATION_REQUEST",
        "request_status": "PENDING_CLARIFICATION",
        "version": int((state["items"].get(gid) or {}).get("version") or 0) + 1,
        "updated_at": stamp,
    }
    state["items"][gid] = ground_item
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "outcome": OUTCOME_MISSED_AT_EXPECTED,
        "checked_spot": believed_spot,
        "should_search_or_ask": True,
        "next_actions": ["search_nearby_spots", "ask_partner_naturally"],
        "natural_question_prompt": question,
        "pending_ground_item": ground_item,
        "object_belief": {
            k: v for k, v in belief.items() if k != "actual_spot"
        },
    }


def search_nearby_spots(
    day_root: Path | str,
    *,
    object_id: str,
    candidate_spots: Iterable[str],
    now_iso: str | None = None,
) -> dict[str, Any]:
    """摸空后顺手在附近几个可能位置找一圈：找着则更新认知，没找着则自然衔接开口问伴侣。"""
    oid = str(object_id or "").strip()
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    belief = dict(state["object_beliefs"].get(oid) or {})
    if not belief:
        return {"ok": False, "status_code": 404, "error_code": "OBJECT_BELIEF_NOT_FOUND"}

    actual_spot = str(belief.get("actual_spot") or "").strip()
    believed_spot = str(belief.get("subject_believed_spot") or "").strip()
    label = str(belief.get("object_label") or oid).strip()
    inspected = list(belief.get("inspected_spots") or [])
    gid = str(belief.get("pending_ground_item_id") or f"clarify_unseen_move:{oid}")

    found = False
    for raw_spot in candidate_spots or ():
        spot = str(raw_spot or "").strip()
        if not spot:
            continue
        if spot not in inspected:
            inspected.append(spot)
        if spot == actual_spot:
            found = True
            break

    belief["inspected_spots"] = inspected
    belief["updated_at"] = stamp

    if found:
        belief["subject_believed_spot"] = actual_spot
        belief["discrepancy_state"] = DISCREPANCY_RESOLVED_SEARCH
        belief["last_witnessed_at"] = stamp
        belief["natural_question_prompt"] = None
        state["object_beliefs"][oid] = belief
        # 自己是自己翻出来的（伴侣还没聊起过），转为可随口提一句的 PRIVATE_UNSHARED 发现记录
        state["items"][gid] = {
            "item_id": gid,
            "topic": f"寻物找到：{label}",
            "summary": f"原本以为在{believed_spot}的{label}，找了一圈在{actual_spot}看见了。",
            "item_kind": "unseen_move_found_solo",
            "object_id": oid,
            "ground_status": PRIVATE_UNSHARED,
            "delivery_state": None,
            "requires_agreement": False,
            "partner_knows": False,
            "version": int((state["items"].get(gid) or {}).get("version") or 0) + 1,
            "updated_at": stamp,
        }
        state["updated_at"] = stamp
        _atomic_write_json(_state_path(day_root), state)
        return {
            "ok": True,
            "status_code": 200,
            "outcome": OUTCOME_FOUND_BY_SEARCH,
            "found_at_spot": actual_spot,
            "inspected_spots": inspected,
            "should_ask_partner": False,
            "ground_item": state["items"][gid],
        }

    spots_label = "、".join(inspected[-3:]) if inspected else believed_spot
    question = f"刚去{spots_label}都没找到{label}，你之前看到它放哪儿了吗？"
    belief["discrepancy_state"] = DISCREPANCY_MISSED_SEEKING
    belief["natural_question_prompt"] = question
    state["object_beliefs"][oid] = belief

    ground_item = dict(state["items"].get(gid) or {})
    ground_item.update(
        {
            "item_id": gid,
            "topic": f"寻物待问伴侣：{label}",
            "summary": f"找过{spots_label}都没见着{label}，需要问问伴侣是不是挪过位置。",
            "item_kind": "unseen_move_clarification",
            "object_id": oid,
            "ground_status": PENDING_CLARIFICATION,
            "partner_knows": False,
            "natural_question_prompt": question,
            "intent_kind": "CLARIFICATION_REQUEST",
            "request_status": "PENDING_CLARIFICATION",
            "version": int(ground_item.get("version") or 0) + 1,
            "updated_at": stamp,
        }
    )
    state["items"][gid] = ground_item
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "outcome": OUTCOME_STILL_MISSING_ASK,
        "found_at_spot": None,
        "inspected_spots": inspected,
        "should_ask_partner": True,
        "natural_question_prompt": question,
        "ground_item": ground_item,
    }


def resolve_unseen_move_with_partner(
    day_root: Path | str,
    *,
    object_id: str,
    partner_told_spot: str,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """伴侣回答物件新去向后，主体更新位置信念并把该共同事实收口为 SHARED_WITH_PARTNER。"""
    oid = str(object_id or "").strip()
    told_spot = str(partner_told_spot or "").strip()
    if not oid or not told_spot:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_OBJECT_OR_TOLD_SPOT"}
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    belief = dict(state["object_beliefs"].get(oid) or {})
    if not belief:
        return {"ok": False, "status_code": 404, "error_code": "OBJECT_BELIEF_NOT_FOUND"}

    label = str(belief.get("object_label") or oid).strip()
    old_believed = str(belief.get("subject_believed_spot") or "").strip()
    gid = str(belief.get("pending_ground_item_id") or f"clarify_unseen_move:{oid}")

    belief["subject_believed_spot"] = told_spot
    belief["actual_spot"] = told_spot
    belief["discrepancy_state"] = DISCREPANCY_RESOLVED_PARTNER
    belief["last_witnessed_at"] = stamp
    belief["natural_question_prompt"] = None
    belief["updated_at"] = stamp
    state["object_beliefs"][oid] = belief

    ground_item = {
        "item_id": gid,
        "topic": f"物件新位置共识：{label}",
        "summary": f"问过伴侣后确认，{label}从{old_believed}挪到了{told_spot}。",
        "item_kind": "object_location",
        "object_id": oid,
        "ground_status": SHARED_WITH_PARTNER,
        "delivery_state": DELIVERY_UNDERSTOOD,
        "requires_agreement": False,
        "partner_knows": True,
        "agreement_expired": False,
        "intent_kind": "ORDINARY_COMMUNICATION",
        "request_status": "RESOLVED",
        "version": int((state["items"].get(gid) or {}).get("version") or 0) + 1,
        "updated_at": stamp,
    }
    state["items"][gid] = ground_item
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "outcome": OUTCOME_CLARIFIED_BY_PARTNER,
        "updated_believed_spot": told_spot,
        "object_belief": belief,
        "ground_item": ground_item,
    }


def _error_result(
    status_code: int,
    error_code: str,
    detail: str = "",
    **extra: Any,
) -> dict[str, Any]:
    code_str = str(error_code)
    out: dict[str, Any] = {
        "ok": False,
        "status_code": int(status_code),
        "error_code": code_str,
        "reason_code": code_str,
        "detail": str(detail or code_str),
    }
    out.update(extra)
    return out


def _clean_required_str(value: Any, error_code: str, detail: str) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(value, str) or "\x00" in value or not value.strip():
        return None, _error_result(400, error_code, detail)
    return value.strip(), None


def _clean_optional_str(value: Any, error_code: str = "INVALID_FIELDS") -> tuple[str, dict[str, Any] | None]:
    if value is None:
        return "", None
    if not isinstance(value, str) or "\x00" in value:
        return "", _error_result(400, error_code, "Optional string field must be a valid string")
    return value.strip(), None


def _resolve_actor_side(
    raw_actor: Any,
    *,
    default: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    if raw_actor is None or (isinstance(raw_actor, str) and not raw_actor.strip()):
        if default is not None:
            return default, None
        return None, _error_result(400, "MISSING_ACTOR_SIDE", "actor_side is required")
    if not isinstance(raw_actor, str) or "\x00" in raw_actor:
        return None, _error_result(403, "FORBIDDEN_ACTOR_SIDE", "Invalid actor_side type")
    side = raw_actor.strip().lower()
    if side not in VALID_ACTOR_SIDES:
        return None, _error_result(403, "FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {raw_actor!r}")
    return side, None


def _compute_op_fingerprint(action: str, fields: Mapping[str, Any]) -> str:
    blob = json.dumps(
        {"action": action, "fields": dict(fields)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _check_operation_ledger(
    state: Mapping[str, Any],
    op_key: str,
    fp: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not op_key:
        return None, None
    ledger = state.get("operation_ledger")
    if not isinstance(ledger, Mapping):
        return None, None
    entry = ledger.get(op_key)
    if not isinstance(entry, Mapping):
        return None, None
    if entry.get("fingerprint") != fp:
        return None, _error_result(
            409,
            "OPERATION_KEY_CONFLICT",
            f"operation_key {op_key!r} already used with different payload",
        )
    cached = copy.deepcopy(dict(entry.get("result") or {}))
    cached["ok"] = True
    cached["status_code"] = 200
    cached["already_applied"] = True
    cached["command_status"] = "DUPLICATE"
    return cached, None


def _record_operation_ledger(
    state: dict[str, Any],
    op_key: str,
    *,
    action: str,
    fp: str,
    stamp: str,
    result: Mapping[str, Any],
) -> None:
    if not op_key:
        return
    ledger = state.setdefault("operation_ledger", {})
    ledger[op_key] = {
        "fingerprint": fp,
        "action": action,
        "applied_at": stamp,
        "result": copy.deepcopy(dict(result)),
    }
    if len(ledger) > MAX_OPERATION_LEDGER:
        oldest = list(ledger.keys())[: len(ledger) - MAX_OPERATION_LEDGER]
        for old_k in oldest:
            ledger.pop(old_k, None)


def record_dual_rationale_decision(
    day_root: Path | str,
    *,
    decision_id: str | None = None,
    topic_cn: str,
    scope_cn: str,
    adopted_choice_cn: str,
    subject_rationale_cn: str,
    partner_rationale_cn: str,
    subject_kept_detail_cn: str = "",
    operation_key: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """第 15 章 §10.B：登记共同决定并保留可各自独立修订的双份理由（绝不晋升永久偏好）。"""
    clean_topic, err = _clean_required_str(topic_cn, "MISSING_DECISION_FIELDS", "topic_cn is required")
    if err:
        return err
    clean_scope, err = _clean_required_str(scope_cn, "MISSING_DECISION_FIELDS", "scope_cn is required")
    if err:
        return err
    clean_choice, err = _clean_required_str(
        adopted_choice_cn, "MISSING_DECISION_FIELDS", "adopted_choice_cn is required"
    )
    if err:
        return err
    clean_subj_rat, err = _clean_required_str(
        subject_rationale_cn, "MISSING_DECISION_FIELDS", "subject_rationale_cn is required"
    )
    if err:
        return err
    clean_part_rat, err = _clean_required_str(
        partner_rationale_cn, "MISSING_DECISION_FIELDS", "partner_rationale_cn is required"
    )
    if err:
        return err
    clean_kept, err = _clean_optional_str(subject_kept_detail_cn)
    if err:
        return err
    op_key, err = _clean_optional_str(operation_key)
    if err:
        return err
    did_opt, err = _clean_optional_str(decision_id)
    if err:
        return err
    did = did_opt or f"dec_{uuid.uuid4().hex[:10]}"

    fp = _compute_op_fingerprint(
        "record_dual_rationale_decision",
        {
            "decision_id": did_opt,
            "topic_cn": clean_topic,
            "scope_cn": clean_scope,
            "adopted_choice_cn": clean_choice,
            "subject_rationale_cn": clean_subj_rat,
            "partner_rationale_cn": clean_part_rat,
            "subject_kept_detail_cn": clean_kept,
        },
    )
    state = _load_state_readonly(day_root)
    dup, conflict = _check_operation_ledger(state, op_key, fp)
    if conflict:
        return conflict
    if dup:
        return dup

    stamp = _now_iso(now_iso)
    existing = dict(state["dual_rationale_decisions"].get(did) or {})
    record = {
        "decision_id": did,
        "topic_cn": clean_topic,
        "scope_cn": clean_scope,
        "adopted_choice_cn": clean_choice,
        "subject_rationale_cn": clean_subj_rat,
        "partner_rationale_cn": clean_part_rat,
        "subject_kept_detail_cn": clean_kept,
        "promotes_permanent_preference": False,
        "subject_rationale_revision": int(existing.get("subject_rationale_revision") or 1),
        "partner_rationale_revision": int(existing.get("partner_rationale_revision") or 1),
        "rationale_history": list(existing.get("rationale_history") or []),
        "decided_at": existing.get("decided_at") or stamp,
        "updated_at": stamp,
    }
    state["dual_rationale_decisions"][did] = record
    state["updated_at"] = stamp
    result = {
        "ok": True,
        "status_code": 200,
        "command_status": "APPLIED",
        "decision": record,
    }
    _record_operation_ledger(
        state,
        op_key,
        action="record_dual_rationale_decision",
        fp=fp,
        stamp=stamp,
        result=result,
    )
    _atomic_write_json(_state_path(day_root), state)
    return result


def revise_own_decision_rationale(
    day_root: Path | str,
    *,
    decision_id: str,
    actor_side: str,
    new_rationale_cn: str,
    target_side: str | None = None,
    operation_key: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """仅允许修订己方理由（subject 只改 subject_rationale_cn，partner 只改 partner_rationale_cn）。"""
    side, err = _resolve_actor_side(actor_side)
    if err:
        return err
    assert side is not None

    if target_side is not None and str(target_side).strip():
        clean_target = str(target_side).strip().lower()
        if clean_target != side:
            return _error_result(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                f"Actor {side!r} cannot revise {clean_target!r}'s rationale",
                cross_side_blocked=True,
            )

    clean_did, err = _clean_required_str(decision_id, "MISSING_DECISION_ID", "decision_id is required")
    if err:
        return err
    clean_rat, err = _clean_required_str(
        new_rationale_cn, "MISSING_RATIONALE", "new_rationale_cn is required"
    )
    if err:
        return err
    op_key, err = _clean_optional_str(operation_key)
    if err:
        return err

    fp = _compute_op_fingerprint(
        "revise_own_decision_rationale",
        {
            "decision_id": clean_did,
            "actor_side": side,
            "new_rationale_cn": clean_rat,
        },
    )
    state = _load_state_readonly(day_root)
    dup, conflict = _check_operation_ledger(state, op_key, fp)
    if conflict:
        return conflict
    if dup:
        return dup

    existing = dict(state["dual_rationale_decisions"].get(clean_did) or {})
    if not existing:
        return _error_result(404, "DECISION_NOT_FOUND", f"Decision not found: {clean_did!r}")

    stamp = _now_iso(now_iso)
    history = list(existing.get("rationale_history") or [])
    if side == "subject":
        old_val = str(existing.get("subject_rationale_cn") or "")
        existing["subject_rationale_cn"] = clean_rat
        existing["subject_rationale_revision"] = int(existing.get("subject_rationale_revision") or 1) + 1
    else:
        old_val = str(existing.get("partner_rationale_cn") or "")
        existing["partner_rationale_cn"] = clean_rat
        existing["partner_rationale_revision"] = int(existing.get("partner_rationale_revision") or 1) + 1

    history.append(
        {
            "side": side,
            "previous_rationale_cn": old_val,
            "revised_to_cn": clean_rat,
            "revised_at": stamp,
        }
    )
    existing["rationale_history"] = history[-16:]
    existing["promotes_permanent_preference"] = False
    existing["updated_at"] = stamp

    state["dual_rationale_decisions"][clean_did] = existing
    state["updated_at"] = stamp
    result = {
        "ok": True,
        "status_code": 200,
        "command_status": "APPLIED",
        "decision": existing,
    }
    _record_operation_ledger(
        state,
        op_key,
        action="revise_own_decision_rationale",
        fp=fp,
        stamp=stamp,
        result=result,
    )
    _atomic_write_json(_state_path(day_root), state)
    return result


def record_missed_moment(
    day_root: Path | str,
    *,
    moment_id: str | None = None,
    summary_cn: str,
    actor_side: str = "subject",
    operation_key: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """第 15 章 §10.E：登记“当时没看到”的错过瞬间（missed_originally=True, status=MISSED_UNSEEN）。"""
    side, err = _resolve_actor_side(actor_side, default="subject")
    if err:
        return err
    clean_summary, err = _clean_required_str(
        summary_cn, "MISSING_MISSED_MOMENT_SUMMARY", "summary_cn is required"
    )
    if err:
        return err
    mid_opt, err = _clean_optional_str(moment_id)
    if err:
        return err
    op_key, err = _clean_optional_str(operation_key)
    if err:
        return err
    mid = mid_opt or f"mom_{uuid.uuid4().hex[:10]}"

    fp = _compute_op_fingerprint(
        "record_missed_moment",
        {
            "moment_id": mid_opt,
            "summary_cn": clean_summary,
            "actor_side": side,
        },
    )
    state = _load_state_readonly(day_root)
    dup, conflict = _check_operation_ledger(state, op_key, fp)
    if conflict:
        return conflict
    if dup:
        return dup

    if mid in state["missed_moment_reconnections"]:
        return _error_result(
            409, "MISSED_MOMENT_ALREADY_EXISTS", f"Missed moment already exists: {mid!r}"
        )

    stamp = _now_iso(now_iso)
    record = {
        "moment_id": mid,
        "summary_cn": clean_summary,
        "recorded_by": side,
        "missed_originally": True,
        "status": MISSED_STATUS_UNSEEN,
        "material_note_cn": "",
        "material_ref": None,
        "material_supplied_by": None,
        "material_supplied_at": None,
        "catchup_note_cn": "",
        "known_since": None,
        "reconnected_at": None,
        "recorded_at": stamp,
        "updated_at": stamp,
    }
    state["missed_moment_reconnections"][mid] = record
    state["updated_at"] = stamp
    result = {
        "ok": True,
        "status_code": 200,
        "command_status": "APPLIED",
        "missed_moment": record,
    }
    _record_operation_ledger(
        state,
        op_key,
        action="record_missed_moment",
        fp=fp,
        stamp=stamp,
        result=result,
    )
    _atomic_write_json(_state_path(day_root), state)
    return result


def supply_missed_moment_material(
    day_root: Path | str,
    *,
    moment_id: str,
    material_note_cn: str,
    material_ref: str | None = None,
    actor_side: str = "partner",
    operation_key: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """第 15 章 §10.E：伴侣补送错过事件的材料，仅推进到 MATERIAL_SUPPLIED，绝不单方面强行已读重接。"""
    side, err = _resolve_actor_side(actor_side, default="partner")
    if err:
        return err
    clean_mid, err = _clean_required_str(moment_id, "MISSING_MOMENT_ID", "moment_id is required")
    if err:
        return err
    clean_note, err = _clean_required_str(
        material_note_cn, "MISSING_MATERIAL_NOTE", "material_note_cn is required"
    )
    if err:
        return err
    clean_ref, err = _clean_optional_str(material_ref)
    if err:
        return err
    op_key, err = _clean_optional_str(operation_key)
    if err:
        return err

    fp = _compute_op_fingerprint(
        "supply_missed_moment_material",
        {
            "moment_id": clean_mid,
            "material_note_cn": clean_note,
            "material_ref": clean_ref or None,
            "actor_side": side,
        },
    )
    state = _load_state_readonly(day_root)
    dup, conflict = _check_operation_ledger(state, op_key, fp)
    if conflict:
        return conflict
    if dup:
        return dup

    existing = dict(state["missed_moment_reconnections"].get(clean_mid) or {})
    if not existing:
        return _error_result(404, "MISSED_MOMENT_NOT_FOUND", f"Missed moment not found: {clean_mid!r}")
    if existing.get("status") == MISSED_STATUS_RECONNECTED:
        return _error_result(
            409,
            "MISSED_MOMENT_ALREADY_RECONNECTED",
            f"Missed moment {clean_mid!r} is already reconnected",
        )

    stamp = _now_iso(now_iso)
    existing["material_note_cn"] = clean_note
    existing["material_ref"] = clean_ref or None
    existing["material_supplied_by"] = side
    existing["material_supplied_at"] = stamp
    existing["status"] = MISSED_STATUS_MATERIAL_SUPPLIED
    existing["missed_originally"] = True
    existing["known_since"] = None
    existing["updated_at"] = stamp

    state["missed_moment_reconnections"][clean_mid] = existing
    state["updated_at"] = stamp
    result = {
        "ok": True,
        "status_code": 200,
        "command_status": "APPLIED",
        "missed_moment": existing,
    }
    _record_operation_ledger(
        state,
        op_key,
        action="supply_missed_moment_material",
        fp=fp,
        stamp=stamp,
        result=result,
    )
    _atomic_write_json(_state_path(day_root), state)
    return result


def acknowledge_missed_moment_catchup(
    day_root: Path | str,
    *,
    moment_id: str,
    catchup_note_cn: str,
    actor_side: str = "subject",
    now_iso: str | None = None,
    operation_key: str | None = None,
) -> dict[str, Any]:
    """第 15 章 §10.E：主体亲手补看后确认重接（RECONNECTED_NOW_KNOWN），永久保留 missed_originally=True。"""
    side, err = _resolve_actor_side(actor_side, default="subject")
    if err:
        return err
    if side != "subject":
        return _error_result(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "Only subject can confirm catch-up to RECONNECTED_NOW_KNOWN",
        )
    clean_mid, err = _clean_required_str(moment_id, "MISSING_MOMENT_ID", "moment_id is required")
    if err:
        return err
    clean_note, err = _clean_required_str(
        catchup_note_cn, "MISSING_CATCHUP_NOTE", "catchup_note_cn is required"
    )
    if err:
        return err
    op_key, err = _clean_optional_str(operation_key)
    if err:
        return err

    fp = _compute_op_fingerprint(
        "acknowledge_missed_moment_catchup",
        {
            "moment_id": clean_mid,
            "catchup_note_cn": clean_note,
            "actor_side": side,
        },
    )
    state = _load_state_readonly(day_root)
    dup, conflict = _check_operation_ledger(state, op_key, fp)
    if conflict:
        return conflict
    if dup:
        return dup

    existing = dict(state["missed_moment_reconnections"].get(clean_mid) or {})
    if not existing:
        return _error_result(404, "MISSED_MOMENT_NOT_FOUND", f"Missed moment not found: {clean_mid!r}")

    stamp = _now_iso(now_iso)
    existing["status"] = MISSED_STATUS_RECONNECTED
    existing["catchup_note_cn"] = clean_note
    existing["known_since"] = existing.get("known_since") or stamp
    existing["reconnected_at"] = stamp
    # 反事后全知铁律：补看重接后也永久保留“当时确实没看见”的历史事实
    existing["missed_originally"] = True
    existing["updated_at"] = stamp

    state["missed_moment_reconnections"][clean_mid] = existing
    state["updated_at"] = stamp
    result = {
        "ok": True,
        "status_code": 200,
        "command_status": "APPLIED",
        "missed_moment": existing,
    }
    _record_operation_ledger(
        state,
        op_key,
        action="acknowledge_missed_moment_catchup",
        fp=fp,
        stamp=stamp,
        result=result,
    )
    _atomic_write_json(_state_path(day_root), state)
    return result


def submit_async_coauthor_contribution(
    day_root: Path | str,
    *,
    contrib_id: str | None = None,
    work_ref: str,
    author_id: str,
    author_label_cn: str,
    is_simulated: bool = True,
    diff_summary_cn: str,
    operation_key: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """第 15 章 §10.J：提交共同作者的异步改动贡献（模拟作者严格保留 is_simulated=True）。"""
    if not isinstance(is_simulated, bool):
        return _error_result(400, "INVALID_FIELDS", "is_simulated must be a boolean")
    clean_work_ref, err = _clean_required_str(work_ref, "MISSING_CONTRIBUTION_FIELDS", "work_ref is required")
    if err:
        return err
    clean_author_id, err = _clean_required_str(author_id, "MISSING_CONTRIBUTION_FIELDS", "author_id is required")
    if err:
        return err
    clean_author_label, err = _clean_required_str(
        author_label_cn, "MISSING_CONTRIBUTION_FIELDS", "author_label_cn is required"
    )
    if err:
        return err
    clean_diff, err = _clean_required_str(
        diff_summary_cn, "MISSING_CONTRIBUTION_FIELDS", "diff_summary_cn is required"
    )
    if err:
        return err
    cid_opt, err = _clean_optional_str(contrib_id)
    if err:
        return err
    op_key, err = _clean_optional_str(operation_key)
    if err:
        return err
    cid = cid_opt or f"contrib_{uuid.uuid4().hex[:10]}"

    fp = _compute_op_fingerprint(
        "submit_async_coauthor_contribution",
        {
            "contrib_id": cid_opt,
            "work_ref": clean_work_ref,
            "author_id": clean_author_id,
            "author_label_cn": clean_author_label,
            "is_simulated": bool(is_simulated),
            "diff_summary_cn": clean_diff,
        },
    )
    state = _load_state_readonly(day_root)
    dup, conflict = _check_operation_ledger(state, op_key, fp)
    if conflict:
        return conflict
    if dup:
        return dup

    stamp = _now_iso(now_iso)
    record = {
        "contrib_id": cid,
        "work_ref": clean_work_ref,
        "author_id": clean_author_id,
        "author_label_cn": clean_author_label,
        "is_simulated": bool(is_simulated),
        "identity_kind": "simulated_coauthor" if is_simulated else "authorized_coauthor",
        "address_is_not_identity": True,
        "diff_summary_cn": clean_diff,
        "status": CONTRIB_STATUS_PENDING,
        "resolution": None,
        "resolution_note_cn": "",
        "resolved_by": None,
        "resolved_at": None,
        "submitted_at": stamp,
        "updated_at": stamp,
    }
    state["async_coauthor_contributions"][cid] = record
    state["updated_at"] = stamp
    result = {
        "ok": True,
        "status_code": 200,
        "command_status": "APPLIED",
        "contribution": record,
    }
    _record_operation_ledger(
        state,
        op_key,
        action="submit_async_coauthor_contribution",
        fp=fp,
        stamp=stamp,
        result=result,
    )
    _atomic_write_json(_state_path(day_root), state)
    return result


def resolve_async_contribution(
    day_root: Path | str,
    *,
    contrib_id: str,
    resolution: str,
    note_cn: str = "",
    actor_side: str = "subject",
    operation_key: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """第 15 章 §10.J：主体恢复后处理异步合写差异（accept_diff / propose_edit / keep_own_branch）。"""
    side, err = _resolve_actor_side(actor_side, default="subject")
    if err:
        return err
    if side != "subject":
        return _error_result(
            403, "FORBIDDEN_ACTOR_SIDE", "Only subject can resolve async coauthor contributions"
        )
    clean_cid, err = _clean_required_str(contrib_id, "MISSING_CONTRIBUTION_ID", "contrib_id is required")
    if err:
        return err
    clean_res, err = _clean_required_str(
        resolution, "INVALID_CONTRIBUTION_RESOLUTION", "resolution is required"
    )
    if err:
        return err
    if clean_res not in CONTRIB_RESOLUTIONS:
        return _error_result(
            400,
            "INVALID_CONTRIBUTION_RESOLUTION",
            f"Unsupported resolution: {clean_res!r}",
        )
    clean_note, err = _clean_optional_str(note_cn)
    if err:
        return err
    op_key, err = _clean_optional_str(operation_key)
    if err:
        return err

    fp = _compute_op_fingerprint(
        "resolve_async_contribution",
        {
            "contrib_id": clean_cid,
            "resolution": clean_res,
            "note_cn": clean_note,
            "actor_side": side,
        },
    )
    state = _load_state_readonly(day_root)
    dup, conflict = _check_operation_ledger(state, op_key, fp)
    if conflict:
        return conflict
    if dup:
        return dup

    existing = dict(state["async_coauthor_contributions"].get(clean_cid) or {})
    if not existing:
        return _error_result(
            404, "CONTRIBUTION_NOT_FOUND", f"Async contribution not found: {clean_cid!r}"
        )

    stamp = _now_iso(now_iso)
    # 模拟作者身份标记绝不因合写裁决而伪造成真人身份
    preserved_simulated = bool(existing.get("is_simulated", True))
    existing["is_simulated"] = preserved_simulated
    existing["identity_kind"] = "simulated_coauthor" if preserved_simulated else "authorized_coauthor"
    existing["resolution"] = clean_res
    existing["status"] = CONTRIB_STATUS_BY_RESOLUTION[clean_res]
    existing["resolution_note_cn"] = clean_note
    existing["resolved_by"] = "subject"
    existing["resolved_at"] = stamp
    existing["updated_at"] = stamp

    state["async_coauthor_contributions"][clean_cid] = existing
    state["updated_at"] = stamp
    result = {
        "ok": True,
        "status_code": 200,
        "command_status": "APPLIED",
        "contribution": existing,
    }
    _record_operation_ledger(
        state,
        op_key,
        action="resolve_async_contribution",
        fp=fp,
        stamp=stamp,
        result=result,
    )
    _atomic_write_json(_state_path(day_root), state)
    return result


def _read_json_mapping_readonly(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _extract_state_from_receipt_entry(entry: Any) -> tuple[str, str]:
    if isinstance(entry, str):
        return entry.strip().upper(), ""
    if isinstance(entry, Mapping):
        for key in ("delivery_state", "receipt_state", "receipt_stage", "state", "stage", "status"):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                stamp = str(
                    entry.get("updated_at") or entry.get("at") or entry.get("timestamp") or ""
                ).strip()
                return val.strip().upper(), stamp
    return "", ""


def _pick_latest_receipt_from_doc(doc: Mapping[str, Any]) -> tuple[str, str]:
    raw_receipts = doc.get("receipts")
    candidates: list[tuple[str, int, str]] = []
    idx = 0
    if isinstance(raw_receipts, list):
        for entry in raw_receipts:
            st, ts = _extract_state_from_receipt_entry(entry)
            if st:
                candidates.append((ts, idx, st))
            idx += 1
    elif isinstance(raw_receipts, Mapping):
        for key in sorted(raw_receipts.keys()):
            st, ts = _extract_state_from_receipt_entry(raw_receipts[key])
            if st:
                candidates.append((ts, idx, st))
            idx += 1
    if candidates:
        if any(c[0] for c in candidates):
            best = max(candidates, key=lambda c: (c[0], c[1]))
        else:
            best = candidates[-1]
        return best[2], "receipts"

    raw_items = doc.get("items")
    item_iter: Iterable[Any] = (
        raw_items.values()
        if isinstance(raw_items, Mapping)
        else (raw_items if isinstance(raw_items, list) else ())
    )
    for item in item_iter:
        if isinstance(item, Mapping):
            st, ts = _extract_state_from_receipt_entry(item)
            if st:
                candidates.append((ts, idx, st))
            idx += 1
    if candidates:
        if any(c[0] for c in candidates):
            best = max(candidates, key=lambda c: (c[0], c[1]))
        else:
            best = candidates[-1]
        return best[2], "items"
    return "", ""


def _extract_latest_receipt_state_readonly(
    day_root: Path,
    state: Mapping[str, Any],
) -> tuple[str, str]:
    """只读提取最新沟通回执状态（绝不把 SENT/DELIVERED/READ 升格为已理解）。"""
    for rel in (PHONE_COMMON_GROUND_REL_PATH, ("v02_common_ground.json",)):
        doc = _read_json_mapping_readonly(day_root.joinpath(*rel))
        if doc is not None:
            st, kind = _pick_latest_receipt_from_doc(doc)
            if st:
                return st, f"{'/'.join(rel)}#{kind}"

    st, kind = _pick_latest_receipt_from_doc(state)
    if st:
        return st, f"{COMMON_GROUND_REL_PATH}#{kind}"
    return "NONE", "none"


def _extract_neighborhood_simulated_contacts_readonly(day_root: Path) -> list[dict[str, Any]]:
    """仅当磁盘真实存在 world/v04_neighborhood_state.json 时只读摘要模拟联系人，空目录绝不伪造。"""
    nb_path = day_root.joinpath(*V04_NEIGHBORHOOD_REL_PATH)
    doc = _read_json_mapping_readonly(nb_path)
    if doc is None:
        return []

    contacts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add_contact(
        *,
        cid: str,
        display_name_cn: str,
        role_cn: str,
        venue: str,
        activity_cn: str = "",
    ) -> None:
        clean_id = str(cid or "").strip()
        clean_name = str(display_name_cn or clean_id).strip()
        if not clean_id or clean_id in seen_ids:
            return
        seen_ids.add(clean_id)
        contacts.append(
            {
                "contact_id": clean_id,
                "display_name_cn": clean_name,
                "role_cn": str(role_cn or "").strip(),
                "venue": str(venue or "").strip(),
                "current_activity_cn": str(activity_cn or "").strip(),
                "is_simulated": True,
                "address_is_not_identity": True,
                "source": "world/v04_neighborhood_state.json",
            }
        )

    pub = doc.get("public_life")
    if isinstance(pub, Mapping):
        bookstore = pub.get("bookstore")
        if isinstance(bookstore, Mapping) and isinstance(bookstore.get("shopkeeper_life"), Mapping):
            sk = bookstore["shopkeeper_life"]
            _add_contact(
                cid=str(sk.get("resident_id") or "nukui_shinichi"),
                display_name_cn=str(sk.get("shopkeeper_name") or sk.get("resident_name") or "温井慎一"),
                role_cn=str(sk.get("role") or "街角旧书店店主"),
                venue="bookstore",
                activity_cn=str(sk.get("current_task_cn") or ""),
            )
        cafe = pub.get("cafe_resident")
        if isinstance(cafe, Mapping):
            _add_contact(
                cid=str(cafe.get("resident_id") or "saeki_aoi"),
                display_name_cn=str(cafe.get("resident_name") or "佐伯葵"),
                role_cn=str(cafe.get("role") or "街角焙煎咖啡屋店主"),
                venue=str(cafe.get("home_venue") or "cafe"),
                activity_cn=str(cafe.get("current_phase_cn") or ""),
            )

    for container in (doc, pub if isinstance(pub, Mapping) else {}):
        for list_key in ("independent_residents", "residents", "neighbors", "contacts", "simulated_contacts"):
            rows = container.get(list_key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                cid = str(row.get("resident_id") or row.get("contact_id") or row.get("id") or "").strip()
                name = str(
                    row.get("resident_name")
                    or row.get("shopkeeper_name")
                    or row.get("display_name_cn")
                    or row.get("display_name")
                    or row.get("name")
                    or cid
                ).strip()
                _add_contact(
                    cid=cid,
                    display_name_cn=name,
                    role_cn=str(row.get("role_cn") or row.get("role") or ""),
                    venue=str(row.get("home_venue") or row.get("venue") or ""),
                    activity_cn=str(row.get("current_task_cn") or row.get("current_phase_cn") or ""),
                )

    if (
        not contacts
        and doc.get("schema") == "world.v04.neighborhood_living.v1"
        and isinstance(pub, Mapping)
        and isinstance(pub.get("bookstore"), Mapping)
    ):
        _add_contact(
            cid="nukui_shinichi",
            display_name_cn="温井慎一",
            role_cn="街角旧书店店主",
            venue="bookstore",
        )
        _add_contact(
            cid="saeki_aoi",
            display_name_cn="佐伯葵",
            role_cn="街角焙煎咖啡屋店主",
            venue="cafe",
        )

    return contacts


def _build_contact_directory_readonly(
    day_root: Path | str,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """第 13 章 §3 + 第 15 章 §10.J：纯零写只读联系人与通道名录（区分伴侣真实通道与明确模拟联系人）。"""
    root = Path(day_root)
    latest_receipt_state, receipt_source = _extract_latest_receipt_state_readonly(root, state)
    partner_channels = [
        {
            "channel_id": "local_partner",
            "channel_kind": "local_partner_channel",
            "label_cn": "本地伴侣沟通通道",
            "address_is_not_identity": True,
            "is_simulated": False,
            "latest_receipt_state": latest_receipt_state,
            "has_receipt": latest_receipt_state != "NONE",
            "understood_claimed": latest_receipt_state in (DELIVERY_UNDERSTOOD, DELIVERY_AGREED),
            "receipt_chain_distinction_enforced": True,
            "receipt_source": receipt_source,
        }
    ]

    simulated_contacts = _extract_neighborhood_simulated_contacts_readonly(root)
    sim_by_id: dict[str, dict[str, Any]] = {
        c["contact_id"]: c for c in simulated_contacts if isinstance(c.get("contact_id"), str)
    }
    contribs = state.get("async_coauthor_contributions")
    if isinstance(contribs, Mapping):
        for cid in sorted(contribs.keys()):
            row = contribs[cid]
            if not isinstance(row, Mapping) or not bool(row.get("is_simulated", False)):
                continue
            aid = str(row.get("author_id") or "").strip()
            if not aid:
                continue
            work_ref = str(row.get("work_ref") or "").strip()
            if aid in sim_by_id:
                refs = list(sim_by_id[aid].get("work_refs") or [])
                if work_ref and work_ref not in refs:
                    refs.append(work_ref)
                sim_by_id[aid]["work_refs"] = refs
                continue
            entry = {
                "contact_id": aid,
                "display_name_cn": str(row.get("author_label_cn") or aid).strip(),
                "role_cn": "模拟共同作者",
                "venue": "async_coauthor",
                "is_simulated": True,
                "address_is_not_identity": True,
                "source": "async_coauthor_contributions",
                "work_refs": [work_ref] if work_ref else [],
            }
            sim_by_id[aid] = entry
            simulated_contacts.append(entry)

    return {
        "partner_channels": partner_channels,
        "simulated_contacts": simulated_contacts,
        "address_is_not_identity": True,
        "zero_write": True,
    }


def read_v02_common_ground_view(
    day_root: Path | str,
    *,
    viewer: str = "subject",
    now_iso: str | None = None,
) -> dict[str, Any]:
    """只读投影共同认知账本与物件信念状态（100% 零磁盘写入，严格区分 subject / partner 视角）。"""
    stamp = _now_iso(now_iso)
    clean_viewer = "partner" if str(viewer).strip().lower() == "partner" else "subject"
    state = _load_state_readonly(day_root)

    counts = {status: 0 for status in GROUND_STATUSES}
    visible_items: list[dict[str, Any]] = []
    pending_questions: list[dict[str, Any]] = []

    for gid in sorted(state["items"].keys()):
        raw_item = state["items"][gid]
        if not isinstance(raw_item, Mapping):
            continue
        status = str(raw_item.get("ground_status") or PRIVATE_UNSHARED)
        if status in counts:
            counts[status] += 1
        if clean_viewer == "partner" and status == PRIVATE_UNSHARED:
            # 隐私边界：未分享给伴侣的私下发生事项绝不在伴侣视角裸露详情
            continue
        projected = dict(raw_item)
        visible_items.append(projected)
        if status == PENDING_CLARIFICATION and projected.get("natural_question_prompt"):
            pending_questions.append(
                {
                    "item_id": gid,
                    "object_id": projected.get("object_id"),
                    "prompt": projected["natural_question_prompt"],
                    "intent_kind": projected.get("intent_kind") or "CLARIFICATION_REQUEST",
                    "request_status": projected.get("request_status") or "PENDING_CLARIFICATION",
                }
            )

    projected_objects: list[dict[str, Any]] = []
    for oid in sorted(state["object_beliefs"].keys()):
        raw_obj = state["object_beliefs"][oid]
        if not isinstance(raw_obj, Mapping):
            continue
        # 主体视角绝不剧透尚未观测到的 actual_spot，彻底杜绝全知视角作弊
        obj_view = {
            "object_id": raw_obj.get("object_id"),
            "object_label": raw_obj.get("object_label"),
            "subject_believed_spot": raw_obj.get("subject_believed_spot"),
            "discrepancy_state": (
                DISCREPANCY_ALIGNED
                if clean_viewer == "subject"
                and raw_obj.get("discrepancy_state") == DISCREPANCY_UNWITNESSED
                else raw_obj.get("discrepancy_state")
            ),
            "missed_attempts": int(raw_obj.get("missed_attempts") or 0),
            "inspected_spots": list(raw_obj.get("inspected_spots") or []),
            "natural_question_prompt": raw_obj.get("natural_question_prompt"),
        }
        if clean_viewer == "partner":
            obj_view["actual_spot"] = raw_obj.get("actual_spot")
        projected_objects.append(obj_view)

    dual_decisions = [
        copy.deepcopy(state["dual_rationale_decisions"][did])
        for did in sorted(state["dual_rationale_decisions"].keys())
        if isinstance(state["dual_rationale_decisions"][did], Mapping)
    ]
    missed_moments = [
        copy.deepcopy(state["missed_moment_reconnections"][mid])
        for mid in sorted(state["missed_moment_reconnections"].keys())
        if isinstance(state["missed_moment_reconnections"][mid], Mapping)
    ]
    async_contributions = [
        copy.deepcopy(state["async_coauthor_contributions"][cid])
        for cid in sorted(state["async_coauthor_contributions"].keys())
        if isinstance(state["async_coauthor_contributions"][cid], Mapping)
    ]
    contact_directory = _build_contact_directory_readonly(day_root, state)

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": stamp,
        "viewer": clean_viewer,
        "status_counts": (
            counts
            if clean_viewer == "subject"
            else {
                SHARED_WITH_PARTNER: counts[SHARED_WITH_PARTNER],
                PENDING_CLARIFICATION: counts[PENDING_CLARIFICATION],
                PRIVATE_UNSHARED: 0,
            }
        ),
        "private_withheld_count": (
            counts[PRIVATE_UNSHARED] if clean_viewer == "partner" else 0
        ),
        "items": visible_items,
        "object_beliefs": projected_objects,
        "pending_partner_questions": pending_questions,
        "dual_rationale_decisions": dual_decisions,
        "missed_moment_reconnections": missed_moments,
        "async_coauthor_contributions": async_contributions,
        "contact_directory": contact_directory,
    }


def summarize_common_ground_for_cognition(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """供认知循环只读消费的有界共同认知摘要（零写入）。"""
    view = read_v02_common_ground_view(day_root, viewer="subject", now_iso=now_iso)
    shared = [
        {"item_id": i["item_id"], "topic": i["topic"], "summary": i["summary"]}
        for i in view["items"]
        if i.get("ground_status") == SHARED_WITH_PARTNER
    ][:6]
    private_unshared = [
        {"item_id": i["item_id"], "topic": i["topic"], "summary": i["summary"]}
        for i in view["items"]
        if i.get("ground_status") == PRIVATE_UNSHARED
    ][:6]
    pending = [
        {
            "item_id": i["item_id"],
            "topic": i["topic"],
            "summary": i["summary"],
            "natural_question_prompt": i.get("natural_question_prompt"),
        }
        for i in view["items"]
        if i.get("ground_status") == PENDING_CLARIFICATION
    ][:6]
    return {
        "evaluated_at": view["evaluated_at"],
        "status_counts": view["status_counts"],
        "shared_with_partner": shared,
        "private_unshared": private_unshared,
        "pending_clarifications": pending,
        "pending_partner_questions": view["pending_partner_questions"][:4],
        "dual_rationale_decisions": view["dual_rationale_decisions"][:4],
        "pending_missed_moment_catchups": [
            m
            for m in view["missed_moment_reconnections"]
            if m.get("status") != MISSED_STATUS_RECONNECTED
        ][:4],
        "pending_async_contributions": [
            c
            for c in view["async_coauthor_contributions"]
            if c.get("status") == CONTRIB_STATUS_PENDING
        ][:4],
        "contact_directory": view["contact_directory"],
    }


def mutate_v02_common_ground(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """统一 POST 写入口。"""
    if not isinstance(payload, Mapping):
        return _error_result(400, "INVALID_FIELDS", "payload must be a mapping")
    action = str(payload.get("action") or "").strip()

    if actor_side is not None:
        kw_side, kw_err = _resolve_actor_side(actor_side)
        if kw_err:
            return kw_err
        raw_pl_actor = payload.get("actor_side")
        if raw_pl_actor is not None and str(raw_pl_actor).strip():
            pl_side, pl_err = _resolve_actor_side(raw_pl_actor)
            if pl_err:
                return pl_err
            if pl_side != kw_side:
                return _error_result(
                    403,
                    "FORBIDDEN_ACTOR_SIDE",
                    f"Payload actor_side {pl_side!r} does not match caller actor_side {kw_side!r}",
                )

    def _eff_actor(default: str | None = None) -> tuple[str | None, dict[str, Any] | None]:
        raw = actor_side if actor_side is not None else payload.get("actor_side")
        return _resolve_actor_side(raw, default=default)

    if action == "record_ground_item":
        return record_ground_item(
            day_root,
            item_id=payload.get("item_id"),
            topic=str(payload.get("topic") or ""),
            summary=str(payload.get("summary") or ""),
            ground_status=str(payload.get("ground_status") or PRIVATE_UNSHARED),
            item_kind=str(payload.get("item_kind") or "experience"),
            delivery_state=payload.get("delivery_state"),
            requires_agreement=bool(payload.get("requires_agreement", False)),
            now_iso=now_iso,
        )
    if action == "advance_communication_receipt":
        return advance_communication_receipt(
            day_root,
            item_id=str(payload.get("item_id") or ""),
            delivery_state=str(payload.get("delivery_state") or ""),
            now_iso=now_iso,
        )
    if action == "mark_shared_with_partner":
        return mark_shared_with_partner(
            day_root,
            item_id=str(payload.get("item_id") or ""),
            shared_note=str(payload.get("shared_note") or ""),
            now_iso=now_iso,
        )
    if action == "revise_shared_plan":
        return revise_shared_plan(
            day_root,
            item_id=str(payload.get("item_id") or ""),
            revised_summary=str(payload.get("revised_summary") or ""),
            now_iso=now_iso,
        )
    if action == "register_object_belief":
        return register_object_belief(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            object_label=str(payload.get("object_label") or ""),
            spot=str(payload.get("spot") or ""),
            shared_with_partner=bool(payload.get("shared_with_partner", False)),
            now_iso=now_iso,
        )
    if action == "record_partner_unseen_move":
        return record_partner_unseen_move(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            new_spot=str(payload.get("new_spot") or ""),
            object_label=str(payload.get("object_label") or ""),
            moved_by=str(payload.get("moved_by") or "partner"),
            witnessed_by_subject=bool(payload.get("witnessed_by_subject", False)),
            now_iso=now_iso,
        )
    if action == "attempt_use_or_fetch_object":
        return attempt_use_or_fetch_object(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            now_iso=now_iso,
        )
    if action == "search_nearby_spots":
        return search_nearby_spots(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            candidate_spots=list(payload.get("candidate_spots") or []),
            now_iso=now_iso,
        )
    if action == "resolve_unseen_move_with_partner":
        return resolve_unseen_move_with_partner(
            day_root,
            object_id=str(payload.get("object_id") or ""),
            partner_told_spot=str(payload.get("partner_told_spot") or ""),
            now_iso=now_iso,
        )
    if action == "record_dual_rationale_decision":
        return record_dual_rationale_decision(
            day_root,
            decision_id=payload.get("decision_id"),
            topic_cn=payload.get("topic_cn"),  # type: ignore[arg-type]
            scope_cn=payload.get("scope_cn"),  # type: ignore[arg-type]
            adopted_choice_cn=payload.get("adopted_choice_cn"),  # type: ignore[arg-type]
            subject_rationale_cn=payload.get("subject_rationale_cn"),  # type: ignore[arg-type]
            partner_rationale_cn=payload.get("partner_rationale_cn"),  # type: ignore[arg-type]
            subject_kept_detail_cn=payload.get("subject_kept_detail_cn", ""),  # type: ignore[arg-type]
            operation_key=payload.get("operation_key"),
            now_iso=now_iso,
        )
    if action == "revise_own_decision_rationale":
        eff_side, err = _eff_actor(default=None)
        if err:
            return err
        assert eff_side is not None
        if eff_side == "subject" and payload.get("partner_rationale_cn") is not None:
            return _error_result(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                "Subject cannot revise partner_rationale_cn",
                cross_side_blocked=True,
            )
        if eff_side == "partner" and payload.get("subject_rationale_cn") is not None:
            return _error_result(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                "Partner cannot revise subject_rationale_cn",
                cross_side_blocked=True,
            )
        raw_tfield = payload.get("target_field")
        if raw_tfield is not None:
            expected_field = "subject_rationale_cn" if eff_side == "subject" else "partner_rationale_cn"
            if str(raw_tfield).strip() != expected_field:
                return _error_result(
                    403,
                    "FORBIDDEN_ACTOR_SIDE",
                    f"Actor {eff_side!r} cannot target field {raw_tfield!r}",
                    cross_side_blocked=True,
                )
        return revise_own_decision_rationale(
            day_root,
            decision_id=payload.get("decision_id"),  # type: ignore[arg-type]
            actor_side=eff_side,
            new_rationale_cn=payload.get("new_rationale_cn"),  # type: ignore[arg-type]
            target_side=payload.get("target_side"),
            operation_key=payload.get("operation_key"),
            now_iso=now_iso,
        )
    if action == "record_missed_moment":
        eff_side, err = _eff_actor(default="subject")
        if err:
            return err
        assert eff_side is not None
        return record_missed_moment(
            day_root,
            moment_id=payload.get("moment_id"),
            summary_cn=payload.get("summary_cn"),  # type: ignore[arg-type]
            actor_side=eff_side,
            operation_key=payload.get("operation_key"),
            now_iso=now_iso,
        )
    if action == "supply_missed_moment_material":
        eff_side, err = _eff_actor(default="partner")
        if err:
            return err
        assert eff_side is not None
        raw_status = str(payload.get("status") or "").strip().upper()
        if raw_status == MISSED_STATUS_RECONNECTED or bool(payload.get("force_reconnected")) or payload.get("known_since") is not None:
            return _error_result(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                "Cannot unilaterally force RECONNECTED_NOW_KNOWN without subject catch-up",
            )
        return supply_missed_moment_material(
            day_root,
            moment_id=payload.get("moment_id"),  # type: ignore[arg-type]
            material_note_cn=payload.get("material_note_cn"),  # type: ignore[arg-type]
            material_ref=payload.get("material_ref"),
            actor_side=eff_side,
            operation_key=payload.get("operation_key"),
            now_iso=now_iso,
        )
    if action == "acknowledge_missed_moment_catchup":
        eff_side, err = _eff_actor(default="subject")
        if err:
            return err
        assert eff_side is not None
        return acknowledge_missed_moment_catchup(
            day_root,
            moment_id=payload.get("moment_id"),  # type: ignore[arg-type]
            catchup_note_cn=payload.get("catchup_note_cn"),  # type: ignore[arg-type]
            actor_side=eff_side,
            now_iso=now_iso or payload.get("now_iso"),
            operation_key=payload.get("operation_key"),
        )
    if action == "submit_async_coauthor_contribution":
        raw_sim = payload.get("is_simulated", True)
        if not isinstance(raw_sim, bool):
            return _error_result(400, "INVALID_FIELDS", "is_simulated must be a boolean")
        return submit_async_coauthor_contribution(
            day_root,
            contrib_id=payload.get("contrib_id"),
            work_ref=payload.get("work_ref"),  # type: ignore[arg-type]
            author_id=payload.get("author_id"),  # type: ignore[arg-type]
            author_label_cn=payload.get("author_label_cn"),  # type: ignore[arg-type]
            is_simulated=raw_sim,
            diff_summary_cn=payload.get("diff_summary_cn"),  # type: ignore[arg-type]
            operation_key=payload.get("operation_key"),
            now_iso=now_iso,
        )
    if action == "resolve_async_contribution":
        eff_side, err = _eff_actor(default="subject")
        if err:
            return err
        assert eff_side is not None
        return resolve_async_contribution(
            day_root,
            contrib_id=payload.get("contrib_id"),  # type: ignore[arg-type]
            resolution=payload.get("resolution"),  # type: ignore[arg-type]
            note_cn=payload.get("note_cn", ""),  # type: ignore[arg-type]
            actor_side=eff_side,
            operation_key=payload.get("operation_key"),
            now_iso=now_iso,
        )
    return {
        "ok": False,
        "status_code": 400,
        "error_code": "UNKNOWN_COMMON_GROUND_ACTION",
        "action": action,
    }


__all__ = [
    "COMMON_GROUND_REL_PATH",
    "CONTRIB_RESOLUTIONS",
    "CONTRIB_STATUS_BY_RESOLUTION",
    "CONTRIB_STATUS_PENDING",
    "DELIVERY_ACKNOWLEDGED",
    "DELIVERY_AGREED",
    "DELIVERY_DELIVERED",
    "DELIVERY_READ",
    "DELIVERY_SENT",
    "DELIVERY_STATES",
    "DELIVERY_UNDERSTOOD",
    "DISCREPANCY_ALIGNED",
    "DISCREPANCY_MISSED_SEEKING",
    "DISCREPANCY_RESOLVED_PARTNER",
    "DISCREPANCY_RESOLVED_SEARCH",
    "DISCREPANCY_UNWITNESSED",
    "EVENT_UNSEEN_MOVE",
    "GROUND_STATUSES",
    "MISSED_MOMENT_STATUSES",
    "MISSED_STATUS_MATERIAL_SUPPLIED",
    "MISSED_STATUS_RECONNECTED",
    "MISSED_STATUS_UNSEEN",
    "OUTCOME_CLARIFIED_BY_PARTNER",
    "OUTCOME_FOUND_AT_EXPECTED",
    "OUTCOME_FOUND_BY_SEARCH",
    "OUTCOME_MISSED_AT_EXPECTED",
    "OUTCOME_STILL_MISSING_ASK",
    "PENDING_CLARIFICATION",
    "PRIVATE_UNSHARED",
    "SCHEMA_VERSION",
    "SHARED_WITH_PARTNER",
    "VALID_ACTOR_SIDES",
    "acknowledge_missed_moment_catchup",
    "advance_communication_receipt",
    "attempt_use_or_fetch_object",
    "mark_shared_with_partner",
    "mutate_v02_common_ground",
    "read_v02_common_ground_view",
    "record_dual_rationale_decision",
    "record_ground_item",
    "record_missed_moment",
    "record_partner_unseen_move",
    "register_object_belief",
    "resolve_async_contribution",
    "resolve_unseen_move_with_partner",
    "revise_own_decision_rationale",
    "revise_shared_plan",
    "search_nearby_spots",
    "submit_async_coauthor_contribution",
    "summarize_common_ground_for_cognition",
    "supply_missed_moment_material",
]
