"""S05 F-066..F-071：经历交接、准入与 Memory 记录的分层只读复核。

这个模块**只读**，不写 Memory、不写 AG-2、不改治理：

* AG-2 自己的交接事件（``handoff_events_by_id``）与投递记录
  （``handoff_deliveries_by_id``）；
* 通过既有 :class:`LifeMemoryService` / ``LifeExperiencePort`` 读回的
  Memory 记录（治理投影已经生效）；
* 记录里没有的字段一律 ``NOT_RECORDED``，没有实现的层级一律
  ``NOT_IMPLEMENTED``，不生成空表、不把普通 M0 事件改名成 Episode。

它回答的问题是分开的：**交接成功**（AG-2 已有 handoff、投递记录写着
ACCEPT/DUPLICATE/DEFER）不等于 **Memory 当前仍可召回**（治理后读面）。
两者不是一件事，读面必须分开显示。

不输出正文、描述、prompt 或 reasoning；只给不透明 ref、状态和计数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

SCHEMA_MEMORY_REVIEW = "chiyo.s05.memory_review.v1"

#: 记录里没有的字段与尚未实现的层级：打名字，不用空值冒充。
NOT_RECORDED = "NOT_RECORDED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

#: 当前召回的状态。
RECALL_AVAILABLE = "AVAILABLE"
RECALL_NOT_RECALLABLE = "NOT_RECALLABLE"

#: 交接身份命名域（只从 id 前缀读，不重新计算身份）。
IDENTITY_RESULT = "lexp:result"
IDENTITY_TERMINAL = "lexp:terminal_or_activity"
IDENTITY_LEGACY = "lexp:activity_revision"

#: 读面允许透出的 Memory 字段。``text`` 不在其中：正文不进入复核输出。
MEMORY_IDENTITY_FIELDS = (
    "experience_id",
    "event_id",
    "occurred_at",
    "source_ref",
    "activity_ref",
    "action_ref",
    "artifact_ref",
    "version_ref",
    "owner_result",
    "outcome_kind",
)

#: 设计 VIII.8 Q27/Q28 的派生层。当前实现没有它们，不能拿空表冒已验。
DERIVED_LAYERS = {
    "episode_grouping": "设计 Q27 的 grouping projection（episode_id + membership）",
    "episode_summary": "设计 Q27 的 EPISODE_SUMMARY 派生记录（依赖确切 member revisions）",
    "embedding_index": "设计 Q28 的 embedding/检索缓存（依赖 eligibility generation）",
}


def open_state(state_root: Path | str) -> dict[str, Any]:
    """打开同一 state root 的 AG-2 只读状态。失败具名抛出，不返回空库。"""

    from decision_review import open_readers

    readers = open_readers(state_root)
    state = readers["integration_read"].read_state() or {}
    return dict(state)


def handoff_identity(handoff_id: str) -> str:
    hid = str(handoff_id or "")
    if hid.startswith("lexp:result:"):
        return IDENTITY_RESULT
    if hid.startswith("lexp:"):
        return IDENTITY_LEGACY
    return NOT_RECORDED


def list_handoffs(state: dict[str, Any], *, limit: int = 50) -> list[dict[str, Any]]:
    """Every persisted handoff as a content-free identity row, newest-looking last."""

    events = dict(state.get("handoff_events_by_id") or {})
    deliveries = dict(state.get("handoff_deliveries_by_id") or {})
    rows: list[dict[str, Any]] = []
    for hid in sorted(events):
        rows.append(_handoff_row(hid, events[hid], deliveries.get(hid)))
    if limit >= 0:
        rows = rows[-int(limit):]
    return rows


def summarize_handoff(state: dict[str, Any], handoff_id: str) -> dict[str, Any]:
    """One handoff's own identity, source refs and last delivery status.

    Raises ``KeyError`` when the id is not in the state: "not persisted" is not
    "delivered and forgotten".
    """

    hid = str(handoff_id or "")
    event = dict(state.get("handoff_events_by_id") or {})[hid]
    delivery = (state.get("handoff_deliveries_by_id") or {}).get(hid)
    return _handoff_row(hid, event, delivery)


def _handoff_row(handoff_id: str, event: Any, delivery: Any) -> dict[str, Any]:
    event = dict(event or {})
    confirmed = event.get("owner_confirmed_context") or {}
    confirmed = dict(confirmed) if isinstance(confirmed, dict) else {}
    delivery = dict(delivery) if isinstance(delivery, dict) else None
    return {
        "handoff_id": handoff_id,
        "identity_namespace": handoff_identity(handoff_id),
        "kind": event.get("terminal_or_milestone_kind") or NOT_RECORDED,
        "occurred_at": event.get("occurred_at") or NOT_RECORDED,
        "activity_ref": event.get("activity_ref") or NOT_RECORDED,
        "activity_revision": event.get("activity_revision"),
        "action_refs": list(event.get("action_refs") or []),
        "settlement_refs": list(event.get("settlement_refs") or []),
        "artifact_refs": list(event.get("artifact_refs") or []),
        "world_refs": list(event.get("world_refs") or []),
        # The owner's confirmed result, reduced to identity and presence flags: the
        # description itself is never echoed here.
        "owner_confirmed": {
            "present": bool(confirmed),
            "artifact_id": confirmed.get("artifact_id"),
            "version_ref": confirmed.get("version_ref"),
            "outcome_confirmed": confirmed.get("outcome_confirmed"),
            "description_present": bool(str(confirmed.get("result_description") or "").strip()),
            "title_present": bool(str(confirmed.get("title") or "").strip()),
        },
        # This is the *historical* admission receipt AG-2 persisted, not a live claim.
        "admission_receipt": {
            "status": (delivery or {}).get("status") or NOT_RECORDED,
            "consumer_disposition": (delivery or {}).get("consumer_disposition") or NOT_RECORDED,
            "attempt": (delivery or {}).get("attempt"),
            "delivered_at": (delivery or {}).get("delivered_at"),
            "last_error": (delivery or {}).get("last_error"),
        } if delivery is not None else {
            "status": NOT_RECORDED,
            "consumer_disposition": NOT_RECORDED,
            "attempt": None,
            "delivered_at": None,
            "last_error": None,
        },
    }


def find_memory_records(memory_service: Any, handoff_id: str, *,
                        limit: int = 20) -> dict[str, Any]:
    """The Memory rows whose own ``source_ref`` is exactly this handoff id.

    The search goes through the existing product seam, so governance is applied.
    A ``READY`` binding with zero matching rows means *not currently recallable*
    (never admitted, or withheld) -- it does not mean the handoff failed.
    """

    hid = str(handoff_id or "")
    envelope = memory_service.list_experiences(query=hid, limit=int(limit))
    if not envelope.get("ok"):
        return {"status": envelope.get("status"),
                "reason": envelope.get("reason"),
                "records": None}
    rows = []
    for row in (envelope.get("data") or {}).get("experiences") or []:
        if str(row.get("source_ref") or "") != hid:
            continue
        rows.append({key: row.get(key) for key in MEMORY_IDENTITY_FIELDS})
    return {"status": RECALL_AVAILABLE if rows else RECALL_NOT_RECALLABLE,
            "reason": None if rows else "MEMORY_RECORD_NOT_RECALLABLE",
            "records": rows}


def layers(handoff: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    """The four layers, each with an honest status.

    ``source_evidence`` and ``accepted_memory`` exist in the current implementation;
    every derived layer is ``NOT_IMPLEMENTED`` rather than an empty table.
    """

    source_available = bool(handoff.get("artifact_refs") or handoff.get("world_refs")
                            or handoff.get("action_refs") or handoff.get("settlement_refs"))
    accepted = memory.get("status") == RECALL_AVAILABLE
    out: dict[str, Any] = {
        "source_evidence": {
            "status": "AVAILABLE" if source_available else NOT_RECORDED,
            "detail": "the AG-2 handoff's own refs; no body text is echoed",
        },
        "accepted_memory_record": {
            "status": "AVAILABLE" if accepted else (
                RECALL_NOT_RECALLABLE if memory.get("status") == RECALL_NOT_RECALLABLE
                else str(memory.get("status") or NOT_RECORDED)),
            "records": len(memory.get("records") or []) if memory.get("records") is not None else None,
            "detail": "one M0 Life experience row per admitted handoff; this is the accepted record",
        },
    }
    for name, detail in DERIVED_LAYERS.items():
        out[name] = {"status": NOT_IMPLEMENTED, "detail": detail}
    out["derived_invalidation"] = {
        "status": NOT_IMPLEMENTED,
        "detail": "there is no derived layer yet, so there is no dependency edge to invalidate",
    }
    out["read_gate"] = {
        "status": "AVAILABLE",
        "detail": "existing governance projection filters the source row on search/read/context",
    }
    return out


def review_handoff(state: dict[str, Any], handoff_id: str, memory_service: Any, *,
                   limit: int = 20) -> dict[str, Any]:
    """One handoff: identity + historical admission receipt + current recall + layers."""

    handoff = summarize_handoff(state, handoff_id)
    memory = find_memory_records(memory_service, handoff_id, limit=limit)
    return {
        "schema": SCHEMA_MEMORY_REVIEW,
        "handoff": handoff,
        "current_recall": memory,
        "layers": layers(handoff, memory),
        "separation": {
            "handoff_succeeded": bool(
                (handoff.get("admission_receipt") or {}).get("status") in {"DELIVERED"}
                or (handoff.get("admission_receipt") or {}).get(
                    "consumer_disposition") in {"ACCEPT", "DUPLICATE"}),
            "memory_recallable": memory.get("status") == RECALL_AVAILABLE,
            "note": "a delivered handoff is not the same claim as a currently recallable Memory row",
        },
        # F-071's legal "no new record" answer, when the existing admission path gives one.
        "no_admission": no_admission_view(handoff, memory),
    }


def no_admission_view(handoff: dict[str, Any],
                      memory: dict[str, Any]) -> dict[str, Any]:
    """How the *existing* admission path answered "no new record".

    ``DUPLICATE`` is M0's own idempotent admission result -- a real storage decision
    with zero new rows.  It is **not** the design's semantic ``DO_NOT_ADMIT``
    evaluation, which does not exist in this codebase; that gap is named instead of
    being filled by the writer's ``no_change`` or by re-labelling a duplicate.
    """

    receipt = handoff.get("admission_receipt") or {}
    disposition = str(receipt.get("consumer_disposition") or "")
    storage_no_change = disposition == "DUPLICATE"
    return {
        "storage_level": ("NO_CHANGE_AT_M0_IDEMPOTENT_ADMISSION" if storage_no_change
                          else NOT_RECORDED),
        "zero_new_record": storage_no_change,
        "semantic_do_not_admit": NOT_IMPLEMENTED,
        "note": ("M0 dedupe on (source_origin, primary_source_ref_id) is a real admission "
                 "answer, but it is not a semantic importance evaluation; that evaluator "
                 "does not exist here"),
    }
