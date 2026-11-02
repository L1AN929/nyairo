"""INFRA F155-160：有来源后果、分域处理与三轴结算的**只读**复核。

这个模块回答一个很具体的问题：一次真实动作之后，*动作本身*、*活动生命周期*、*后果交接*
各自到了哪一步，哪些域已经处理，哪些被阻塞。它不创建任何新协议，也不把 AR-1 现有的
``ResultSettlementRecord`` 改名成设计里的 ``ConsequenceEnvelope``。

三条轴，各自独立
----------------
1. ``action_outcome``：AR-0 的 Action / Attempt / Receipt / 观测结果，加上 AR-1 的
   ``ResultSettlementRecord``（effect claims、settlement_status、outcome_kind）。
2. ``activity_lifecycle``：LR-2 的 canonical Activity 状态，加 LR-5 自己的 progress 读面。
3. ``consequence_envelope``：设计 VIII.7 的 ``ConsequenceEnvelope`` / obligation compiler /
   target-owner receipt **在本仓库不存在**，这里一律 ``NOT_IMPLEMENTED``。AR-1 的
   outbox + consumer deliveries 只是**通知扇出**，不是 envelope closure，也不证明
   “必需义务都已结算”。

分域，各自说话
--------------
* AR-1 的每条 delivery 属于注册 consumer 的**回执**（ACCEPT/IGNORE/DEFER/REJECT/CONFLICT），
  不是设计要求的 target-owner receipt；``owner_receipt_verified`` 恒为 ``False``。
* LR-5（``life`` 域）能被它自己的读面交叉核实：只有当 LR-5 的 checkpoint update 的
  ``settlement_refs`` 真的包含这次 settlement 时，才写 ``VERIFIED_BY_OWNER_READ``。
* Memory 的**真实**后果走 AG-2 经历交接（``lexp:*`` → M0），不是 AR-1 的 memory
  consumer。两条分开显示：AR-1 memory 通知在这里按生产策略是 ``DELIVERY_FAILED``，
  而交接投递成功与否由 AG-2 的 ``handoff_deliveries_by_id`` 与 M0 读面决定。
* 没有绑定 Memory 时，Memory 读面写 ``UNVERIFIED``（附原因），不写“没有记录”。

只读
----
不取 lease、不建 capability、不写任何状态。发现 WAL / commit intent 未回收时直接返回
``*_PENDING_RECOVERY``，由实例自己的 runtime 处理，不在这里重放（重放是写）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from action_outcome_review import NOT_IMPLEMENTED, NOT_RECORDED, project_action

SCHEMA = "chiyo.infra.settlement_review.v1"

UNVERIFIED = "UNVERIFIED"

#: 三轴的名字：分开显示，永远不合并成一个“完成”。
AXIS_ACTION = "action_outcome"
AXIS_ACTIVITY = "activity_lifecycle"
AXIS_ENVELOPE = "consequence_envelope"

#: AR-1 delivery 状态的诚实映射：只改名不改事实。
DELIVERY_ACKNOWLEDGED = "ACKNOWLEDGED_BY_CONSUMER"
DELIVERY_IGNORED = "IGNORED_BY_CONSUMER"
DELIVERY_DEFERRED = "DEFERRED_BY_CONSUMER"
DELIVERY_REJECTED = "REJECTED_BY_CONSUMER"
DELIVERY_CONFLICT = "CONFLICT_BY_CONSUMER"
DELIVERY_FAILED = "DELIVERY_FAILED"
DELIVERY_PENDING = "PENDING"

#: 这些通知状态不算“处理完成”。
_NOTIFICATION_BAD = frozenset({
    DELIVERY_FAILED, DELIVERY_DEFERRED, DELIVERY_PENDING,
    DELIVERY_REJECTED, DELIVERY_CONFLICT,
})

#: 设计 VIII.7 需要、而本仓库没有的东西。名字写清楚，不用空实现冒充。
ENVELOPE_GAPS: dict[str, str] = {
    "consequence_envelope": "设计 VIII.7 的 ConsequenceEnvelope（冻结 consequential context）",
    "obligation_compiler": "从 owner refs + 冻结 contract 确定性编译 obligations 的 compiler",
    "required_conditional_advisory": "required/conditional/advisory 的编译规则与 blocking 语义",
    "target_owner_receipt": "目标 owner 自己可核验的 receipt（AR-1 只有 consumer disposition）",
    "not_applicable": "compiler 判定的 NOT_APPLICABLE（没有义务 ≠ 收到 NO_CHANGE）",
    "no_change_owner_receipt": "owner 真实评价过的 NO_CHANGE + reason code（不是超时/没 handler）",
    "causal_depth_capacity": "设计 Q19 的 depth 上限与每根 consequence 容量策略",
    "broadcast_to_all_domains": "设计明确不做全 14 域广播；本仓库只投递已注册 consumer",
}

#: AR-1 去重键：只按证据根与 Action 身份，不是设计的复合键。
DEDUP_NOTE = (
    "AR-1 按 root_evidence_id 去重证据、按 Action 身份做 settlement 幂等；"
    "设计要求的 source_fact_id + contract_version + target_owner + consequence_kind + "
    "target_identity 复合键在本仓库不存在。")


# ---------------------------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------------------------


def _wal_files(state_root: Path) -> tuple[str, ...]:
    return (
        "action_reality.wal",
        "result_settlement.wal",
        "activity_progress.wal",
        "commit_intent.json",
    )


def pending_recovery(state_root: Path | str) -> Optional[str]:
    """Return the first unrecovered commit file's name, or ``None``."""

    root = Path(state_root)
    for name in _wal_files(root):
        if (root / name).exists():
            return name
    return None


def open_readers(state_root: Path | str) -> dict[str, Any]:
    """Open the existing pure-read ports over one state root.  Never takes a lease."""

    import action_reality_ledger as ar0
    import activity_continuity as ac
    import activity_progress_completion_lr5 as lr5
    import life_integration_ag2 as ag2
    import result_settlement_ar1 as ar1

    # Same values as ``life_runtime_production`` (pinned by tests).
    namespace_agency = "production"
    namespace_activity = ac.NAMESPACE_CANONICAL

    root = Path(state_root)
    action_store = ar0.ActionStore(root, namespace=namespace_agency)
    settlement_store = ar1.SettlementStore(root, namespace=namespace_agency)
    integration_store = ag2.IntegrationStore(root, namespace=namespace_activity)
    activity_store = ac.CanonicalActivityStore(root, namespace=namespace_activity)
    progress_store = lr5.ProgressStore(root, namespace=namespace_activity)
    return {
        "action_read": ar0.ActionReadService(action_store),
        "settlement_read": ar1.SettlementReadService(
            settlement_store=settlement_store, action_store=action_store),
        "integration_read": ag2.IntegrationReadPort(integration_store),
        "activity_read": ac.ActivityReadService(activity_store),
        "progress_read": lr5.ActivityProgressReadService(progress_store=progress_store),
    }


# ---------------------------------------------------------------------------------------------
# axis 1: action outcome
# ---------------------------------------------------------------------------------------------


def _settlement_summary(record: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not record:
        return {
            "status": NOT_RECORDED,
            "reason": "AR1_HAS_NO_SETTLEMENT_FOR_ACTION",
            "detail": ("AR-1 只为已到达终态结果的动作生成 ResultSettlementRecord；"
                       "这里没有记录，不等于动作没有发生"),
        }
    claims = []
    for claim in record.get("effect_claims") or []:
        claims.append({
            "domain": claim.get("domain"),
            "predicate": claim.get("predicate"),
            "epistemic_status": claim.get("epistemic_status"),
            "authority_grade": claim.get("authority_grade"),
            "authority_domain": claim.get("authority_domain"),
            "object_ref": claim.get("object_ref"),
            "claim_id": claim.get("claim_id"),
        })
    return {
        "status": record.get("settlement_status"),
        "outcome_kind": record.get("outcome_kind"),
        "settlement_id": record.get("settlement_id"),
        "revision": record.get("revision"),
        "policy_version": record.get("policy_version"),
        "effect_claims": claims,
        "effect_summary": {
            "confirmed": len(record.get("confirmed_effect_refs") or []),
            "missing": len(record.get("missing_effect_refs") or []),
            "failed": len(record.get("failed_effect_refs") or []),
            "conflicts": len(record.get("conflict_refs") or []),
            "unresolved": len(record.get("unresolved_claims") or []),
        },
        "closed": str(record.get("settlement_status") or "") in {"SETTLED", "PARTIAL", "CONFLICT"},
        "note": ("这是 AR-1 对**动作结果**的结算，不是 Activity 生命周期，也不是 envelope closure"),
    }


def _action_axis(projected: Mapping[str, Any], settlement: Mapping[str, Any],
                 history: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "action": projected,
        "settlement": dict(settlement),
        "settlement_history": [
            {key: row.get(key) for key in
             ("settlement_id", "revision", "settlement_status", "outcome_kind", "supersedes_ref")}
            for row in history
        ],
        "separation_note": ("Action 状态、AR-0 receipt、AR-1 结果结算各自是不同事实；"
                            "settlement_status=SETTLED 只说明结果已结算，不说明生活后果完成"),
    }


# ---------------------------------------------------------------------------------------------
# axis 2: activity lifecycle
# ---------------------------------------------------------------------------------------------


def _activity_axis(activity_read: Any, progress_read: Any,
                   activity_ref: Optional[str], settlement_id: Optional[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "activity_ref": activity_ref or NOT_RECORDED,
        "status": NOT_RECORDED,
        "revision": None,
        "completion_evidence_ref": None,
        "progress": {
            "progress_state": NOT_RECORDED,
            "completion_status": NOT_RECORDED,
            "checkpoint_ref": None,
        },
        "settlement_seen_by_lr5": UNVERIFIED,
        "closed": False,
        "read": "LR2_CANONICAL_ACTIVITY+LR5_PROGRESS",
        "note": ("Activity 的生命周期由 LR-2 拥有；动作成功、结果结算都不自动结束它"),
    }
    if not activity_ref:
        out["status"] = NOT_RECORDED
        out["note"] = "动作没有绑定 Activity，因此没有可读的生命周期"
        return out
    try:
        act = activity_read.get_activity(str(activity_ref))
    except Exception as exc:  # 读坏了不能说“没有活动”
        out["status"] = "%s:%s" % (UNVERIFIED, type(exc).__name__)
        return out
    if act is None:
        out["status"] = "NOT_FOUND"
        return out
    out.update({
        "status": act.get("status"),
        "revision": act.get("revision"),
        "completion_evidence_ref": act.get("completion_evidence_ref"),
        "closed": str(act.get("status") or "") == "COMPLETED",
    })
    consumed: dict[str, Any] = {}
    try:
        state = progress_read.get_progress_state(str(activity_ref)) or {}
        out["progress"] = {
            "progress_state": state.get("progress_state", NOT_RECORDED),
            "completion_status": state.get("completion_status", NOT_RECORDED),
            "completion_reason": state.get("completion_reason", NOT_RECORDED),
            "completion_evidence_refs": list(state.get("completion_evidence_refs") or []),
            "checkpoint_ref": state.get("checkpoint_ref"),
        }
        updates = progress_read.list_checkpoint_updates(str(activity_ref)) or []
    except Exception as exc:
        out["progress"]["progress_state"] = "%s:%s" % (UNVERIFIED, type(exc).__name__)
        updates = []
    try:
        # LR-5's own consumed-evidence rows: an AR-1 event it really ingested carries
        # ``settlement_ref`` there.  A new checkpoint is not required for that to be true.
        consumed = dict((progress_read.progress_store.load_state()
                         or {}).get("consumed_evidences_by_id") or {})
    except Exception:
        consumed = {}
    if settlement_id:
        seen = any(str(settlement_id) in {str(ref) for ref in (row.get("settlement_refs") or [])}
                   for row in updates)
        if not seen:
            # ``consumed_evidences_by_id`` rows wrap the evidence: ``{"evidence": {...}}``.
            seen = any(str(((row or {}).get("evidence") or {}).get("settlement_ref") or "")
                       == str(settlement_id) for row in consumed.values())
        out["settlement_seen_by_lr5"] = (
            "VERIFIED_BY_OWNER_READ" if seen else "NOT_CONFIRMED_BY_LR5")
    if updates:
        last = updates[-1]
        out["progress"]["last_checkpoint_progress_state"] = last.get("progress_state")
        out["progress"]["last_checkpoint_update_id"] = last.get("update_id")
    return out


# ---------------------------------------------------------------------------------------------
# axis 3 + per-domain rows
# ---------------------------------------------------------------------------------------------


def _delivery_status(delivery: Mapping[str, Any]) -> str:
    status = str(delivery.get("status") or "")
    disposition = str(delivery.get("consumer_disposition") or "")
    if status == "DELIVERED_TO_CONSUMER":
        if disposition == "ACCEPT":
            return DELIVERY_ACKNOWLEDGED
        if disposition == "IGNORE":
            return DELIVERY_IGNORED
        if disposition == "DEFER":
            return DELIVERY_DEFERRED
        if disposition == "REJECT":
            return DELIVERY_REJECTED
        if disposition == "CONFLICT":
            return DELIVERY_CONFLICT
        return "DELIVERED_WITH_DISPOSITION_%s" % (disposition or NOT_RECORDED)
    if status == "DELIVERY_FAILED":
        return DELIVERY_FAILED
    if status == "PENDING":
        return DELIVERY_PENDING
    return status or NOT_RECORDED


def domain_rows(deliveries: Iterable[Mapping[str, Any]],
                outbox_events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per AR-1 consumer domain.  Latest attempt wins; all attempts are counted."""

    events = {str(row.get("event_id")): row for row in outbox_events}
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in deliveries:
        grouped.setdefault(str(row.get("consumer_domain") or NOT_RECORDED), []).append(row)
    rows: list[dict[str, Any]] = []
    for domain in sorted(grouped):
        items = sorted(grouped[domain], key=lambda r: (
            int(events.get(str(r.get("event_id")), {}).get("settlement_revision") or 0),
            int(r.get("attempt") or 0), str(r.get("delivery_id") or "")))
        latest = items[-1]
        event_ids = sorted({str(r.get("event_id")) for r in items})
        rows.append({
            "domain": domain,
            "transport": "AR1_SETTLEMENT_OUTBOX_DELIVERY",
            "delivery_id": latest.get("delivery_id"),
            "event_id": latest.get("event_id"),
            "event_ids": event_ids,
            "settlement_revisions": sorted({
                int(events[eid].get("settlement_revision") or 0)
                for eid in event_ids if eid in events}),
            "attempt": latest.get("attempt"),
            "raw_status": latest.get("status"),
            "consumer_disposition": latest.get("consumer_disposition") or NOT_RECORDED,
            "status": _delivery_status(latest),
            "reason_or_error": latest.get("last_error_ref") or NOT_RECORDED,
            "downstream_proposal_ref": latest.get("downstream_proposal_ref") or NOT_RECORDED,
            # AR-1 delivery 不是 target-owner receipt。
            "receipt_kind": "AR1_CONSUMER_ACK",
            "owner_receipt_verified": False,
            "receipt_note": ("AR-1 记录注册 consumer 的 disposition；设计要求的 target-owner "
                             "可核验 receipt 在本仓库未实现"),
        })
    return rows


def _life_domain_verification(rows: list[dict[str, Any]],
                              activity_axis: Mapping[str, Any]) -> None:
    """Cross-check the ``life`` row against LR-5's own read.  No shorthand."""

    for row in rows:
        if row["domain"] != "life":
            continue
        seen = str(activity_axis.get("settlement_seen_by_lr5") or "")
        row["owner_read"] = {
            "reader": "LR5_PROGRESS",
            "status": seen,
            "progress": dict(activity_axis.get("progress") or {}),
            "note": ("LR-5 真的消费了带这次 settlement_ref 的证据（或 checkpoint update 的"
                     "settlement_refs 含它）时，才把 LIFE 域标成 owner 读面可核实"),
        }


def _handoff_rows(state: Mapping[str, Any], *, action_id: str,
                  settlement_ids: Iterable[str],
                  memory_service: Any) -> list[dict[str, Any]]:
    """AG-2 experience handoffs that belong to this action, with their own delivery row."""

    import life_memory_review as memreview

    wanted_settlements = {str(value) for value in settlement_ids}
    events = dict(state.get("handoff_events_by_id") or {})
    rows: list[dict[str, Any]] = []
    for hid in sorted(events):
        event = dict(events[hid] or {})
        action_refs = {str(ref) for ref in (event.get("action_refs") or [])}
        settlement_refs = {str(ref) for ref in (event.get("settlement_refs") or [])}
        if action_id in action_refs:
            basis = "ACTION_REF"
        elif settlement_refs & wanted_settlements:
            basis = "SETTLEMENT_REF"
        else:
            continue
        row = memreview.summarize_handoff(dict(state), hid)
        row["match_basis"] = basis
        row["kind_note"] = ("AG-2 经历交接是 Memory 的真实入口；它不是 AR-1 的 memory "
                            "consumer，也不由 settlement 的 delivery 状态决定")
        memory = memreview.find_memory_records(memory_service, hid, limit=5) \
            if memory_service is not None else {
                "status": UNVERIFIED, "reason": "MEMORY_BINDING_NOT_PROVIDED", "records": None}
        row["memory_record"] = {
            "status": memory.get("status"),
            "reason": memory.get("reason"),
            "count": len(memory.get("records") or []) if memory.get("records") is not None else None,
            "detail": ("M0 读面按 source_ref 精确匹配；没有绑定 Memory 时这里是 UNVERIFIED，"
                       "不是“没有记录”"),
        }
        rows.append(row)
    return rows


def _envelope_axis() -> dict[str, Any]:
    return {
        "status": NOT_IMPLEMENTED,
        "closure_declarable": False,
        "obligations": NOT_IMPLEMENTED,
        "required_obligations_settled": NOT_IMPLEMENTED,
        "recorded_equivalent": "AR1_RESULT_SETTLEMENT_NOTIFICATION_FANOUT",
        "detail": ("本仓库记录的是 AR-1 的 ResultSettlementRecord + outbox consumer "
                   "deliveries：它按已注册 consumer 扇出通知，并由每个 consumer 各自回应。"
                   "它不是设计 VIII.7 的 ConsequenceEnvelope，也不能据此宣称"
                   "“必需义务都已结算”"),
        "named_gaps": dict(ENVELOPE_GAPS),
        "dedup": {"status": "PARTIAL", "note": DEDUP_NOTE},
    }


#: 后果域状态：由该域自己的 owner 读面/交接状态决定，不由 AR-1 通知状态决定。
DOMAIN_APPLIED = "APPLIED"
DOMAIN_APPLIED_VERIFIED = "APPLIED_VERIFIED_BY_OWNER_READ"
DOMAIN_ACKNOWLEDGED_UNVERIFIED = "ACKNOWLEDGED_UNVERIFIED"
DOMAIN_STORAGE_IDEMPOTENT = "NO_CHANGE_AT_M0_STORAGE_IDEMPOTENT"
DOMAIN_DELIVERED_NOT_RECALLABLE = "DELIVERED_NOT_CURRENTLY_RECALLABLE"
DOMAIN_DELIVERED_UNVERIFIED = "DELIVERED_MEMORY_UNVERIFIED"
DOMAIN_PENDING = "PENDING"
DOMAIN_BLOCKED = "BLOCKED"
DOMAIN_NOT_DELIVERED = "NOT_DELIVERED"

_CONSEQUENCE_BAD = frozenset({
    DOMAIN_PENDING, DOMAIN_BLOCKED, DOMAIN_NOT_DELIVERED,
    DOMAIN_DELIVERED_UNVERIFIED, NOT_RECORDED, UNVERIFIED,
})


def _memory_domain_status(handoffs: list[dict[str, Any]],
                          settlement: Mapping[str, Any],
                          read_error: Optional[str] = None) -> dict[str, Any]:
    if read_error:
        return {"status": UNVERIFIED,
                "detail": "AG-2 交接状态读不出来，不能声称 Memory 已处理：%s" % read_error}
    outcome_kind = str(settlement.get("outcome_kind") or "")
    if not handoffs:
        if outcome_kind in {"SUCCESS_EFFECT", "PARTIAL_EFFECT"}:
            return {"status": NOT_RECORDED,
                    "detail": "这次结果有真实效果，但没有记录任何经历交接"}
        return {"status": "NOT_APPLICABLE_FOR_THIS_OUTCOME",
                "detail": "这个结果没有真实变化，不要求 Memory 处理"}
    states = []
    pending_ids = []
    for row in handoffs:
        receipt = row.get("admission_receipt") or {}
        memory = row.get("memory_record") or {}
        delivery = str(receipt.get("status") or "")
        disposition = str(receipt.get("consumer_disposition") or "")
        recall = str(memory.get("status") or "")
        if delivery in {"FAILED_PENDING_RETRY", "DEFERRED"} or delivery in {NOT_RECORDED, ""}:
            state = DOMAIN_PENDING
            pending_ids.append(row.get("handoff_id"))
        elif delivery == "DELIVERED" and disposition == "IGNORE":
            # The Memory consumer explicitly did not take it (e.g. admission disabled).
            state = DOMAIN_BLOCKED
        elif delivery == "DELIVERED" and recall == "AVAILABLE":
            state = (DOMAIN_STORAGE_IDEMPOTENT if disposition == "DUPLICATE"
                     else DOMAIN_APPLIED)
        elif delivery == "DELIVERED" and recall == "NOT_RECALLABLE":
            state = DOMAIN_DELIVERED_NOT_RECALLABLE
        elif delivery == "DELIVERED" and recall in {UNVERIFIED, None, ""}:
            state = DOMAIN_DELIVERED_UNVERIFIED
        elif delivery == "DELIVERED":
            state = DOMAIN_DELIVERED_NOT_RECALLABLE
        else:
            state = DOMAIN_BLOCKED
        states.append({"handoff_id": row.get("handoff_id"), "status": state,
                       "delivery_status": delivery or NOT_RECORDED,
                       "disposition": disposition or NOT_RECORDED,
                       "memory_record_status": recall or NOT_RECORDED})
    if all(s["status"] in {DOMAIN_APPLIED, DOMAIN_STORAGE_IDEMPOTENT,
                           DOMAIN_DELIVERED_NOT_RECALLABLE} for s in states):
        overall = DOMAIN_APPLIED
    elif any(s["status"] == DOMAIN_PENDING for s in states):
        overall = DOMAIN_PENDING
    elif any(s["status"] == DOMAIN_DELIVERED_UNVERIFIED for s in states):
        overall = UNVERIFIED
    else:
        overall = DOMAIN_BLOCKED
    return {"status": overall, "handoffs": states, "pending_handoff_ids": pending_ids}


def consequence_domains(action_axis: Mapping[str, Any], domains: list[dict[str, Any]],
                        handoffs: list[dict[str, Any]],
                        handoff_read_error: Optional[str] = None) -> dict[str, Any]:
    """The domains that actually carry consequences, each with its own evidence source.

    This is deliberately **not** the AR-1 notification list: AR-1's ``memory`` delivery is a
    sandbox/refusing notification consumer in production, while Memory's real consequence is
    the AG-2 experience handoff.  Both are shown, in different places.
    """

    settlement = dict(action_axis.get("settlement") or {})
    life_row = next((row for row in domains if row["domain"] == "life"), {})
    owner_read = str((life_row.get("owner_read") or {}).get("status") or "")
    raw_life = str(life_row.get("status") or "")
    if owner_read == "VERIFIED_BY_OWNER_READ":
        life = {"status": DOMAIN_APPLIED_VERIFIED, "source": "LR5_PROGRESS"}
    elif raw_life == DELIVERY_ACKNOWLEDGED:
        life = {"status": DOMAIN_ACKNOWLEDGED_UNVERIFIED,
                "source": "AR1_DELIVERY_ONLY",
                "detail": "consumer 已确认，但 LR-5 读面没有记录这次 settlement"}
    elif raw_life in {DELIVERY_FAILED, DELIVERY_DEFERRED, DELIVERY_IGNORED,
                      DELIVERY_REJECTED, DELIVERY_CONFLICT, DELIVERY_PENDING}:
        life = {"status": DOMAIN_PENDING, "source": "AR1_DELIVERY",
                "delivery_status": raw_life}
    else:
        life = {"status": NOT_RECORDED, "source": NOT_RECORDED}

    claims = settlement.get("effect_claims") or []
    if claims:
        confirmed = any(claim.get("epistemic_status") == "CONFIRMED" for claim in claims)
        artifact = {"status": DOMAIN_APPLIED if confirmed else DOMAIN_ACKNOWLEDGED_UNVERIFIED,
                    "source": "AR1_EFFECT_CLAIM",
                    "predicates": [claim.get("predicate") for claim in claims]}
    else:
        artifact = {"status": NOT_RECORDED, "source": NOT_RECORDED}

    return {
        "life": life,
        "artifact": artifact,
        "memory": _memory_domain_status(handoffs, settlement, handoff_read_error),
    }


def _closure(action_axis: Mapping[str, Any], activity_axis: Mapping[str, Any],
             domains: list[dict[str, Any]], handoffs: list[dict[str, Any]],
             handoff_read_error: Optional[str] = None) -> dict[str, Any]:
    blocked_notifications = [row["domain"] for row in domains
                             if row["status"] in _NOTIFICATION_BAD]
    pending_handoffs = [row["handoff_id"] for row in handoffs
                        if str((row.get("admission_receipt") or {}).get("status") or "")
                        not in {"DELIVERED"}]
    consequence = consequence_domains(action_axis, domains, handoffs, handoff_read_error)
    consequence_ok = all(
        str((consequence[name] or {}).get("status") or "") not in _CONSEQUENCE_BAD
        for name in ("life", "artifact", "memory"))
    action_closed = bool((action_axis.get("settlement") or {}).get("closed"))
    activity_closed = bool(activity_axis.get("closed"))
    if action_closed and consequence_ok and activity_closed:
        summary = "ACTION_ACTIVITY_AND_RECORDED_CONSEQUENCE_DOMAINS_CLOSED"
    elif action_closed and consequence_ok:
        summary = "ACTION_AND_CONSEQUENCE_DOMAINS_SETTLED_ACTIVITY_STILL_OPEN"
    elif action_closed:
        summary = "ACTION_SETTLED_BUT_CONSEQUENCE_DOMAINS_BLOCKED_OR_PENDING"
    else:
        summary = "ACTION_OUTCOME_NOT_CLOSED"
    return {
        "action_outcome": "CLOSED" if action_closed else "OPEN",
        "activity_lifecycle": "COMPLETED" if activity_closed else "OPEN",
        "required_obligations": NOT_IMPLEMENTED,
        "envelope_closure_declarable": False,
        "ar1_notification_delivery_complete": not blocked_notifications,
        "ar1_blocked_or_pending_notifications": blocked_notifications,
        "consequence_domains_processed": consequence_ok,
        "consequence_domains": consequence,
        "pending_handoff_ids": pending_handoffs,
        "summary": summary,
        "note": ("AR-1 通知扇出与真实后果域分开：生产里 memory 通知 consumer 按策略拒写，"
                 "但 Memory 的真实后果由 AG-2 经历交接与 M0 读面决定。即使动作已保存、"
                 "结果已结算，只要某个后果域失败/挂起，就不能说“所有生活后果已经结算”；"
                 "envelope closure 本身仍 NOT_IMPLEMENTED"),
    }


# ---------------------------------------------------------------------------------------------
# one action
# ---------------------------------------------------------------------------------------------


def review_action(*, action_id: str, readers: Mapping[str, Any],
                  memory_service: Any = None) -> dict[str, Any]:
    """Project one Action across the three axes and per-domain states.  Pure read."""

    action_read = readers["action_read"]
    settlement_read = readers["settlement_read"]
    act = action_read.get_action(str(action_id))
    if act is None:
        return {"ok": False, "reason": "ACTION_NOT_FOUND", "action_id": str(action_id)}

    lineage = settlement_read.trace_result_lineage(str(action_id)) or {}
    latest = lineage.get("latest_settlement")
    history = lineage.get("settlement_history") or []
    events = lineage.get("settled_result_events") or []
    deliveries = lineage.get("consumer_deliveries") or []

    projected = project_action(
        act,
        attempts=action_read.list_attempts(str(action_id)),
        receipts=action_read.list_receipts(str(action_id)),
        evidences=action_read.list_result_evidences(str(action_id)),
        reconciliations=action_read.list_reconciliations(str(action_id)),
    )
    settlement = _settlement_summary(latest)
    action_axis = _action_axis(projected, settlement, history)

    activity_ref = act.get("activity_ref") or (
        (events[-1].get("activity_ref") if events else None))
    activity_axis = _activity_axis(
        readers["activity_read"], readers["progress_read"], activity_ref,
        settlement.get("settlement_id"))

    domains = domain_rows(deliveries, events)
    _life_domain_verification(domains, activity_axis)

    integration_state: dict[str, Any] = {}
    try:
        integration_state = dict(readers["integration_read"].read_state() or {})
    except Exception as exc:  # 读失败是具名的，不是空状态
        integration_state = {"__read_error__": "%s: %s" % (type(exc).__name__, exc)}
    settlement_ids = [str(row.get("settlement_id")) for row in history
                      if row.get("settlement_id")]
    if settlement.get("settlement_id"):
        settlement_ids.append(str(settlement["settlement_id"]))
    handoff_read_error = integration_state.get("__read_error__") if integration_state else None
    handoffs = [] if handoff_read_error else _handoff_rows(
        integration_state, action_id=str(action_id), settlement_ids=settlement_ids,
        memory_service=memory_service)

    return {
        "ok": True,
        "schema": SCHEMA,
        "action_id": str(action_id),
        "read_at": _now_iso(),
        "axes": {
            AXIS_ACTION: action_axis,
            AXIS_ACTIVITY: activity_axis,
            AXIS_ENVELOPE: _envelope_axis(),
        },
        "domains": domains,
        "handoffs": handoffs,
        "handoffs_read": {
            "status": "UNREADABLE" if handoff_read_error else "READ",
            "error": handoff_read_error,
        },
        "closure": _closure(action_axis, activity_axis, domains, handoffs,
                            handoff_read_error),
        "content_policy": "只输出不透明 ref、状态与计数；不输出正文、prompt 或 reasoning",
    }


def build_settlement_review(state_root: Path | str, *, action_id: Optional[str] = None,
                            limit: int = 50, memory_service: Any = None) -> dict[str, Any]:
    """Read one action (or a bounded recent list).  Never writes and never recovers."""

    root = Path(state_root)
    pending = pending_recovery(root)
    if pending is not None:
        return {
            "ok": False,
            "schema": SCHEMA,
            "state_root": str(root),
            "reason": "COMMIT_WAL_PENDING_RECOVERY",
            "detail": ("%s 存在：上一次写入可能在提交中途中断。重放是写操作，"
                       "请让实例自己的 runtime 先恢复再读" % pending),
            "read_at": _now_iso(),
        }
    if not root.exists():
        return {"ok": False, "schema": SCHEMA, "state_root": str(root),
                "reason": "STATE_ROOT_NOT_FOUND", "read_at": _now_iso()}

    try:
        readers = open_readers(root)
    except Exception as exc:
        # A production-denied path or a corrupt store is a named refusal, not an empty view.
        return {
            "ok": False,
            "schema": SCHEMA,
            "state_root": str(root),
            "reason": "READERS_UNAVAILABLE",
            "detail": "%s: %s" % (type(exc).__name__, exc),
            "read_at": _now_iso(),
        }
    if action_id:
        single = review_action(action_id=str(action_id), readers=readers,
                               memory_service=memory_service)
        if single.get("ok"):
            single["state_root"] = str(root)
        return single

    actions = list(readers["action_read"].list_actions())
    actions.sort(key=lambda row: (str(row.get("created_at") or ""),
                                  str(row.get("action_id") or "")), reverse=True)
    if limit is not None:
        actions = actions[: max(0, int(limit))]
    rows = [review_action(action_id=str(row.get("action_id")), readers=readers,
                          memory_service=memory_service) for row in actions]
    return {
        "ok": True,
        "schema": SCHEMA,
        "state_root": str(root),
        "read_at": _now_iso(),
        "actions": rows,
        "count": len(rows),
    }


# ---------------------------------------------------------------------------------------------
# text rendering (bounded, content-free)
# ---------------------------------------------------------------------------------------------


def render_action_text(row: Mapping[str, Any]) -> str:
    lines = ["action %s" % row.get("action_id")]
    axes = row.get("axes") or {}
    action = (axes.get(AXIS_ACTION) or {}).get("action") or {}
    settlement = (axes.get(AXIS_ACTION) or {}).get("settlement") or {}
    activity = axes.get(AXIS_ACTIVITY) or {}
    envelope = axes.get(AXIS_ENVELOPE) or {}
    lines.append("  axis action_outcome : action=%s settlement=%s/%s" % (
        action.get("status"), settlement.get("status"), settlement.get("outcome_kind")))
    lines.append("  axis activity       : %s (%s)" % (
        activity.get("status"), activity.get("activity_ref")))
    lines.append("  axis envelope       : %s" % (envelope.get("status"),))
    for domain in row.get("domains") or []:
        lines.append("  domain %-8s : %-24s disposition=%s err=%s" % (
            domain.get("domain"), domain.get("status"),
            domain.get("consumer_disposition"), domain.get("reason_or_error")))
    for handoff in row.get("handoffs") or []:
        receipt = handoff.get("admission_receipt") or {}
        memory = handoff.get("memory_record") or {}
        lines.append("  handoff %-22s : delivery=%s disposition=%s memory=%s" % (
            handoff.get("handoff_id"), receipt.get("status"),
            receipt.get("consumer_disposition"), memory.get("status")))
    closure = row.get("closure") or {}
    consequence = closure.get("consequence_domains") or {}
    lines.append("  consequence domains : life=%s artifact=%s memory=%s" % (
        (consequence.get("life") or {}).get("status"),
        (consequence.get("artifact") or {}).get("status"),
        (consequence.get("memory") or {}).get("status")))
    lines.append("  closure             : %s" % (closure.get("summary"),))
    lines.append("  consequence processed: %s | AR-1 notifications complete: %s" % (
        closure.get("consequence_domains_processed"),
        closure.get("ar1_notification_delivery_complete")))
    return "\n".join(lines)


def render_text(envelope: Mapping[str, Any]) -> str:
    if not envelope.get("ok"):
        return "NOT OK: %s (%s)" % (envelope.get("reason"), envelope.get("detail"))
    if "actions" in envelope:
        return "\n\n".join(render_action_text(row) for row in envelope.get("actions") or [])
    return render_action_text(envelope)


def named_gaps() -> dict[str, str]:
    return dict(ENVELOPE_GAPS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "SCHEMA", "NOT_IMPLEMENTED", "NOT_RECORDED", "UNVERIFIED",
    "AXIS_ACTION", "AXIS_ACTIVITY", "AXIS_ENVELOPE",
    "ENVELOPE_GAPS", "DEDUP_NOTE",
    "DELIVERY_ACKNOWLEDGED", "DELIVERY_IGNORED", "DELIVERY_DEFERRED",
    "DELIVERY_REJECTED", "DELIVERY_CONFLICT", "DELIVERY_FAILED", "DELIVERY_PENDING",
    "DOMAIN_APPLIED", "DOMAIN_APPLIED_VERIFIED", "DOMAIN_ACKNOWLEDGED_UNVERIFIED",
    "DOMAIN_STORAGE_IDEMPOTENT", "DOMAIN_DELIVERED_NOT_RECALLABLE",
    "DOMAIN_DELIVERED_UNVERIFIED", "DOMAIN_PENDING", "DOMAIN_BLOCKED",
    "DOMAIN_NOT_DELIVERED",
    "open_readers", "pending_recovery", "domain_rows", "consequence_domains",
    "review_action", "build_settlement_review", "render_action_text", "render_text",
    "named_gaps",
]
