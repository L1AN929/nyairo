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

from datetime import datetime, timedelta, timezone
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
        "updated_at": None,
    }


def _load_state_readonly(day_root: Path | str) -> dict[str, Any]:
    path = _state_path(day_root)
    if not path.is_file():
        return _default_state()
    try:
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
    }


def mutate_v02_common_ground(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """统一 POST 写入口。"""
    action = str(payload.get("action") or "").strip()
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
    return {
        "ok": False,
        "status_code": 400,
        "error_code": "UNKNOWN_COMMON_GROUND_ACTION",
        "action": action,
    }


__all__ = [
    "COMMON_GROUND_REL_PATH",
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
    "OUTCOME_CLARIFIED_BY_PARTNER",
    "OUTCOME_FOUND_AT_EXPECTED",
    "OUTCOME_FOUND_BY_SEARCH",
    "OUTCOME_MISSED_AT_EXPECTED",
    "OUTCOME_STILL_MISSING_ASK",
    "PENDING_CLARIFICATION",
    "PRIVATE_UNSHARED",
    "SCHEMA_VERSION",
    "SHARED_WITH_PARTNER",
    "advance_communication_receipt",
    "attempt_use_or_fetch_object",
    "mark_shared_with_partner",
    "mutate_v02_common_ground",
    "read_v02_common_ground_view",
    "record_ground_item",
    "record_partner_unseen_move",
    "register_object_belief",
    "resolve_unseen_move_with_partner",
    "revise_shared_plan",
    "search_nearby_spots",
    "summarize_common_ground_for_cognition",
]
