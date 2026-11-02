#!/usr/bin/env python3
"""S08 F-093..F-097：未来组织的**只读**审阅面（谁认领了什么，什么还不是 Activity）。

这份模块只回答"现在是怎样"，不写任何东西：

* 未来事项（``fitem:``）是谁提出的、来自哪一条已提交 Decision；
* 今天的计划（``plan:``）绑定了哪一条 Decision；
* operator 的当日义务（``oblig:``）是外部给的，**她不能改**；
* Activity（S09）是已采用的现在，不是未来事项；
* LR-4 的 Agenda/Eligibility 只是恢复资格，不执行任何动作；
* S08 的 **Commitment 还没有 owner**：本模块把这一点具名报出来，绝不把 ``fitem:``、
  ``oblig:`` 或 Agenda 重新命名为承诺。

读失败如实报 ``UNAVAILABLE`` + ``reason_code``；空表与读失败是两件事，绝不互相冒充。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

SCHEMA_VERSION = "chiyo.s08.future_organization_view.v1"

ACTOR_SUBJECT = "subject"
ACTOR_OPERATOR = "operator"

OWNER_FUTURE_ITEMS = "life_future_items.FutureItems"
OWNER_DAY_PLAN = "life_day_plan.DayPlan"
OWNER_OBLIGATIONS = "life_day_plan.DayObligations"
OWNER_ACTIVITY = "AG-2 Activity canonical"
OWNER_AGENDA = "LR-4 ResumeCondition/Agenda/Eligibility"

#: 本单最重要的一句：S08 的承诺域还没有正式 owner。任何把其他对象改叫承诺的投影都算拒绝。
COMMITMENT_STATE_ABSENT = "NOT_IMPLEMENTED"
COMMITMENT_REASON = "S08_COMMITMENT_OWNER_ABSENT"

MAX_ITEMS = 24
MAX_AGENDA_ROWS = 12


def _life_modules() -> tuple[Any, Any]:
    """延迟导入，保证这个模块在 Windows 上也能作为纯读面被导入和测试。"""

    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import life_future_items as lfi  # noqa: PLC0415
    import life_day_plan as ldp  # noqa: PLC0415
    return lfi, ldp


def _agenda_module() -> Any:
    """LR-4 的 store 在 POSIX 上可用；Windows 缺 ``fcntl`` 时如实报不可读，不当空表。"""

    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import activity_waiting_agenda as awa  # noqa: PLC0415
    return awa


def _now_iso(ldp: Any, now: Any) -> str:
    if now is None:
        moment = datetime.now(timezone.utc)
    else:
        moment = ldp.parse_instant(now)
    if moment is None:
        raise ValueError("BUSINESS_CLOCK_INVALID: %r" % (now,))
    return moment.isoformat(timespec="seconds")


def _decision_claims(decision_state: Mapping[str, Any], lfi: Any) -> dict[str, dict[str, Any]]:
    """Recompute the item identity each committed ``later_item`` proposal asked for.

    A Decision does not store the ``fitem:`` ref (that would duplicate the store's identity); it
    stores the proposal.  The same *public* key formula the store uses is re-applied here, so the
    binding is provable rather than guessed.  A proposal whose instant cannot be reproduced stays
    unattributed instead of being attached to a nearby item.
    """

    claims: dict[str, dict[str, Any]] = {}
    for rec in (decision_state.get("decisions_by_id") or {}).values():
        if not isinstance(rec, Mapping):
            continue
        proposal = rec.get("later_item_proposal")
        if not isinstance(proposal, Mapping):
            continue
        park = bool(proposal.get("park_current"))
        origin = (lfi.ORIGIN_PARK if park else lfi.ORIGIN_MODEL_PROPOSAL)
        covered = False
        dues = [proposal.get("due_at")]
        if proposal.get("in_minutes") is not None and rec.get("created_at"):
            try:
                dues.append(lfi.resolve_due_at(in_minutes=proposal.get("in_minutes"),
                                               now=rec.get("created_at")))
            except Exception:
                pass
        for due in dues:
            if not due:
                continue
            try:
                key = lfi.proposal_intent_key(title=proposal.get("title"), due_at=due,
                                              source_refs=proposal.get("source_refs"),
                                              origin=origin)
            except Exception:
                continue
            claims.setdefault(key, {"decision_id": str(rec.get("decision_id") or ""),
                                    "decision_at": rec.get("created_at"),
                                    "park": park})
            covered = True
        if not covered:
            claims.setdefault("unattributed:%s" % (rec.get("decision_id") or ""),
                              {"decision_id": str(rec.get("decision_id") or ""),
                               "decision_at": rec.get("created_at"), "park": park,
                               "unattributed": True})
    return claims


def _future_items_view(state_root: Path | str, subject_id: Optional[str], now_iso: str,
                       day: str, lfi: Any, ldp: Any,
                       claims: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    source = {"owner": OWNER_FUTURE_ITEMS, "actor": ACTOR_SUBJECT, "state": "ok",
              "reason_code": None, "detail": None, "count": 0, "open_count": 0,
              "is_commitment": False, "is_activity": False,
              "note": "deferred intention she proposed; not a Commitment, not an Activity"}
    try:
        store = lfi.FutureItems(state_root, subject_id=subject_id)
        rows = [row.to_dict() for row in store.items()]
    except Exception as exc:
        source.update({"state": "unavailable",
                       "reason_code": str(getattr(exc, "reason_code", type(exc).__name__))[:80],
                       "detail": f"{type(exc).__name__}: {exc}"[:160]})
        return {"status": "UNAVAILABLE", "items": [], "source": source}

    now_moment = ldp.parse_instant(now_iso)
    items: list[dict[str, Any]] = []
    open_rows = [row for row in rows if str(row.get("status")) == lfi.STATUS_OPEN]
    open_rows.sort(key=lambda row: (str(row.get("due_at") or ""), str(row.get("item_ref"))))
    for row in open_rows[:MAX_ITEMS]:
        key = str(row.get("idempotency_key") or "")
        claim = dict(claims.get(key) or {})
        due_moment = ldp.parse_instant(row.get("due_at"))
        items.append({
            "item_ref": row.get("item_ref"),
            "title": row.get("title"),
            "due_at": row.get("due_at"),
            "status": row.get("status"),
            "origin": row.get("origin"),
            "due": bool(now_moment and due_moment and due_moment <= now_moment),
            "times_considered": int(row.get("times_considered") or 0),
            "source_refs": list(row.get("source_refs") or []),
            "claim": {
                "proposed_by": ACTOR_SUBJECT,
                "decision_ref": claim.get("decision_id") or None,
                "decision_at": claim.get("decision_at"),
                "attribution": ("DECISION_RECORDED" if claim.get("decision_id")
                                else "NO_DECISION_RECORD_FOUND"),
                "park": bool(claim.get("park")),
            },
            "is_commitment": False,
            "is_activity": False,
        })
    #: The attribution of *every* item (also the cancelled/closed ones): a closed item keeps the
    #: Decision that claimed it, and a report must be able to say so without reopening it.
    claims_rows = []
    for row in sorted(rows, key=lambda r: (str(r.get("created_at") or ""),
                                           str(r.get("item_ref"))))[:MAX_ITEMS]:
        key = str(row.get("idempotency_key") or "")
        claim = dict(claims.get(key) or {})
        claims_rows.append({
            "item_ref": row.get("item_ref"),
            "status": row.get("status"),
            "origin": row.get("origin"),
            "decision_ref": claim.get("decision_id") or None,
            "decision_at": claim.get("decision_at"),
            "attribution": ("DECISION_RECORDED" if claim.get("decision_id")
                            else "NO_DECISION_RECORD_FOUND"),
            "park": bool(claim.get("park")),
        })
    source.update({"count": len(rows), "open_count": len(open_rows), "day": day})
    return {"status": "OK", "items": items, "claims": claims_rows, "source": source,
            "truncated": len(open_rows) > MAX_ITEMS}


def _day_plan_view(state_root: Path | str, subject_id: Optional[str], day: str,
                   lfi: Any, ldp: Any,
                   futures_lookup: Any) -> dict[str, Any]:
    source = {"owner": OWNER_DAY_PLAN, "actor": ACTOR_SUBJECT, "state": "ok",
              "reason_code": None, "detail": None, "count": 0,
              "note": "what she put into today; binds the committed Decision that asked for it"}
    try:
        store = ldp.DayPlan(state_root, subject_id=subject_id)
        entries = store.for_date(day)
        projected = ldp.project_plan_entries(entries, futures_lookup=futures_lookup)
    except Exception as exc:
        source.update({"state": "unavailable",
                       "reason_code": str(getattr(exc, "reason_code", type(exc).__name__))[:80],
                       "detail": f"{type(exc).__name__}: {exc}"[:160]})
        return {"status": "UNAVAILABLE", "entries": [], "source": source}
    rows = []
    for row in projected[:MAX_ITEMS]:
        rows.append({
            "plan_ref": row.get("plan_ref"),
            "item_ref": row.get("item_ref"),
            "date": row.get("date"),
            "class": row.get("class"),
            "definition_status": row.get("status"),
            "reason_code": row.get("reason_code"),
            "decision_ref": row.get("decision_ref"),
            "title": row.get("title"),
            "due_at": row.get("due_at"),
            "due": bool(row.get("due")),
            "is_commitment": False,
            "is_activity": False,
        })
    source["count"] = len(rows)
    return {"status": "OK", "entries": rows, "source": source}


def _obligations_view(state_root: Path | str, subject_id: Optional[str], day: str,
                      ldp: Any) -> dict[str, Any]:
    source = {"owner": OWNER_OBLIGATIONS, "actor": ACTOR_OPERATOR, "state": "ok",
              "reason_code": None, "detail": None, "count": 0,
              "writable_by_subject": False, "is_commitment": False,
              "note": "given from outside; she may work on it but cannot reschedule/cancel it"}
    try:
        rows = ldp.DayObligations(state_root, subject_id=subject_id).for_date(day)
    except Exception as exc:
        source.update({"state": "unavailable",
                       "reason_code": str(getattr(exc, "reason_code", type(exc).__name__))[:80],
                       "detail": f"{type(exc).__name__}: {exc}"[:160]})
        return {"status": "UNAVAILABLE", "entries": [], "source": source}
    entries = []
    for row in rows[:MAX_ITEMS]:
        entries.append({
            "obligation_ref": row.get("obligation_ref"),
            "date": row.get("date"),
            "title": row.get("title"),
            "due_at": row.get("due_at"),
            "target_ref": row.get("target_ref"),
            "source": row.get("source"),
            "definition_status": row.get("status"),
            "writable_by_subject": False,
        })
    source["count"] = len(entries)
    return {"status": "OK", "entries": entries, "source": source}


def _agenda_view(state_root: Path | str, awa: Any) -> dict[str, Any]:
    source = {"owner": OWNER_AGENDA, "state": "ok", "reason_code": None, "detail": None,
              "conditions": 0, "agenda_items": 0, "eligibilities": 0,
              "note": "an eligibility is only the right to ask to resume; nothing executes here"}
    try:
        conditions = awa.ResumeConditionStore(state_root).load_state()
        agenda = awa.AgendaStore(state_root).load_state()
        eligibility = awa.ResumeEligibilityStore(state_root).load_state()
    except Exception as exc:
        source.update({"state": "unavailable",
                       "reason_code": str(getattr(exc, "reason_code", type(exc).__name__))[:80],
                       "detail": f"{type(exc).__name__}: {exc}"[:160]})
        return {"status": "UNAVAILABLE", "open": [], "source": source}

    def rows(state: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
        return [dict(row) for row in (state.get(key) or {}).values()
                if isinstance(row, Mapping)]

    open_conditions = [row for row in rows(conditions, "conditions")
                       if str(row.get("status")) in getattr(awa, "OPEN_CONDITION_STATUSES", ())]
    open_agenda = [row for row in rows(agenda, "agenda_items")
                   if str(row.get("status")) in getattr(awa, "OPEN_AGENDA_STATUSES", ())]
    open_eligibilities = [row for row in rows(eligibility, "eligibilities")
                          if str(row.get("status")) in getattr(awa, "OPEN_ELIGIBILITY_STATUSES", ())]
    source.update({"conditions": len(open_conditions), "agenda_items": len(open_agenda),
                   "eligibilities": len(open_eligibilities)})
    return {
        "status": "OK",
        "open": [{
            "condition_id": row.get("condition_id"),
            "activity_id": row.get("activity_id"),
            "condition_kind": row.get("condition_kind"),
            "status": row.get("status"),
            "target_time": (row.get("parameters") or {}).get("target_time"),
        } for row in open_conditions[:MAX_AGENDA_ROWS]],
        "eligibilities": [{
            "eligibility_id": row.get("eligibility_id"),
            "activity_id": row.get("activity_id"),
            "status": row.get("status"),
        } for row in open_eligibilities[:MAX_AGENDA_ROWS]],
        "source": source,
    }


def build_future_organization_view(*, state_root: Path | str,
                                   subject_id: Optional[str] = None,
                                   now: Any = None,
                                   activities: Optional[Iterable[Mapping[str, Any]]] = None,
                                   activity_source: Optional[Mapping[str, Any]] = None,
                                   decisions_limit: int = 60) -> dict[str, Any]:
    """The one read-only envelope for S08 (future items / plan / obligations / agenda)."""

    lfi, ldp = _life_modules()
    now_iso = _now_iso(ldp, now)
    day = ldp.business_date(now_iso)

    # -- committed Decisions: which one proposed which item --------------------------------
    decision_source = {"owner": "AG-1 AgencyDecisionStore", "state": "ok",
                       "reason_code": None, "detail": None, "count": 0}
    claims: dict[str, dict[str, Any]] = {}
    try:
        import decision_review  # noqa: PLC0415
        readers = decision_review.open_readers(state_root)
        state = readers["decision_read"].store.load_state() or {}
        claims = _decision_claims(state, lfi)
        decision_source["count"] = len(state.get("decisions_by_id") or {})
    except Exception as exc:
        decision_source.update({"state": "unavailable",
                                "reason_code": str(getattr(exc, "reason_code",
                                                           type(exc).__name__))[:80],
                                "detail": f"{type(exc).__name__}: {exc}"[:160]})

    def futures_lookup(item_ref: Any) -> Optional[dict[str, Any]]:
        store = lfi.FutureItems(state_root, subject_id=subject_id)
        row = store.get(item_ref)
        return row.to_dict() if row is not None else None

    future = _future_items_view(state_root, subject_id, now_iso, day, lfi, ldp, claims)
    plan = _day_plan_view(state_root, subject_id, day, lfi, ldp, futures_lookup)
    obligations = _obligations_view(state_root, subject_id, day, ldp)
    try:
        agenda = _agenda_view(state_root, _agenda_module())
    except Exception as exc:
        agenda = {
            "status": "UNAVAILABLE", "open": [], "eligibilities": [],
            "source": {"owner": OWNER_AGENDA, "state": "unavailable",
                       "reason_code": "AGENDA_READER_UNAVAILABLE",
                       "detail": f"{type(exc).__name__}: {exc}"[:160],
                       "conditions": 0, "agenda_items": 0, "eligibilities": 0,
                       "note": "LR-4 reader not importable on this host; not an empty table"},
        }

    activity_rows = [dict(row) for row in (activities or ())]
    activity_source_row = dict(activity_source or {})
    # A missing canonical file means "no Activity has ever been adopted on this root", which is a
    # real empty answer -- not an unreadable one.  Anything else that is not ``ok`` stays named.
    absent = (str(activity_source_row.get("reason_code") or "") == "CANONICAL_STATE_ABSENT")
    activity_state = str(activity_source_row.get("state") or "ok")
    activities_block = {
        "status": ("OK" if activity_state == "ok"
                   else ("EMPTY" if absent and not activity_rows else "UNAVAILABLE")),
        "source": {"owner": OWNER_ACTIVITY, "actor": ACTOR_SUBJECT,
                   "state": ("absent" if absent and activity_state != "ok" else activity_state),
                   "reason_code": activity_source_row.get("reason_code"),
                   "detail": activity_source_row.get("detail"),
                   "count": len(activity_rows),
                   "note": "Activity is what she has already adopted now; not a future item"},
        "entries": [{
            "activity_ref": row.get("ref") or row.get("activity_ref"),
            "status": row.get("status"),
            "title": row.get("title"),
            "activity_kind": row.get("kind") or row.get("activity_kind"),
            "decision_ref": row.get("decision_ref"),
        } for row in activity_rows[:MAX_ITEMS]],
    }

    sources = {"future_items": future["source"], "day_plan": plan["source"],
               "obligations": obligations["source"], "agenda": agenda["source"],
               "activities": activities_block["source"], "decisions": decision_source}
    failure = next((name for name, src in sources.items()
                    if str(src.get("state")) not in ("ok", "absent")), None)

    data = {
        "schema": SCHEMA_VERSION,
        "read_only": True,
        "now": now_iso,
        "day": day,
        "day_basis": "business_clock_plus_08",
        "future_items": {"status": future["status"], "entries": future["items"],
                         "claims": future.get("claims") or [],
                         "open_count": sources["future_items"].get("open_count"),
                         "truncated": bool(future.get("truncated"))},
        "day_plan": {"status": plan["status"], "entries": plan["entries"],
                     "count": sources["day_plan"].get("count")},
        "obligations": {"status": obligations["status"],
                        "entries": obligations["entries"],
                        "count": sources["obligations"].get("count"),
                        "writable_by_subject": False},
        "activities": activities_block,
        "agenda": {"status": agenda["status"], "open_conditions": agenda["open"],
                   "open_eligibilities": agenda["eligibilities"]},
        "commitment": {
            "state": COMMITMENT_STATE_ABSENT,
            "reason_code": COMMITMENT_REASON,
            "owner": None,
            "note": ("S08 还没有承诺 owner：future items / operator obligations / agenda "
                     "都不能被当作 Commitment，也没有 promised=true 这回事。"),
        },
        "renegotiation": {
            "state": COMMITMENT_STATE_ABSENT,
            "reason_code": COMMITMENT_REASON,
            "note": "改期/取消只作用于她自己的 fitem；没有承诺就没有重新协商协议。",
        },
        "sources": sources,
    }
    if failure is not None:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": str(sources[failure].get("reason_code") or failure),
                "data": data}
    return {"ok": True, "status": "OK", "data": data}


__all__ = [
    "COMMITMENT_REASON",
    "COMMITMENT_STATE_ABSENT",
    "SCHEMA_VERSION",
    "build_future_organization_view",
]
