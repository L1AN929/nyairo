"""S04 F-049/F-050/F-051：决定的有限摘要、未选/延期项、Decision 与 Adoption 分离，
以及旧决定当前适用性的只读复核。

这个模块**只读**既有记录，不写 Decision、不写 Adoption、不写 Activity：

* AG-1 的不可变 ``DecisionRecord`` 与 AG-1 在提交时一并持久化的逐候选处置表
  （``get_decision`` / ``get_candidate_dispositions``）；
* AG-2 自己的 Adoption 记录（``adoption_id_by_decision``）；
* 可选：调用方传入的**正式** AG-2 准入检查
  （``DecisionAdoptionCoordinator.validate_decision_for_adoption``，本身是纯读）。

它不发明任何替代项、理由或 rationale：记录里没有的字段一律报 ``NOT_RECORDED``。
未选候选只按既有处置表列出，不推断“她当时为什么没选”。Decision 是发生过的历史，
Adoption 才是当前约束；两者分开报告，缺少 Adoption 时不给出任何未来承诺。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Optional

SCHEMA_DECISION_REVIEW = "chiyo.s04.decision_review.v1"

#: 记录里没有的字段：打这个名字，不用空字符串、不用事后补写。
NOT_RECORDED = "NOT_RECORDED"

#: 未采用的三种形态，分开说：从未采用 / 采纳被拒 / 已采纳。
STATE_DECIDED_AND_ADOPTED = "DECIDED_AND_ADOPTED"
STATE_DECIDED_ADOPTION_REJECTED = "DECIDED_ADOPTION_REJECTED"
STATE_DECIDED_NOT_ADOPTED = "DECIDED_NOT_ADOPTED"

#: 复核结论。
REVIEW_CURRENT_VALID = "CURRENT_VALID"
REVIEW_REJECTED = "REJECTED"
REVIEW_NOT_EVALUATED = "NOT_EVALUATED"
REVIEW_NOT_FOUND = "NOT_FOUND"
REVIEW_UNREADABLE = "UNREADABLE"


# ---------------------------------------------------------------------------------------------
# readers (pure)
# ---------------------------------------------------------------------------------------------


def open_readers(state_root: Path | str) -> dict[str, Any]:
    """在给定 state root 上打开 AG-1 / AG-2 的**只读**端口。

    两者都只做 ``load_state`` 读取；不取 lease、不建 capability、不写文件。
    """

    import agency_decision_ag1 as ag1
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp

    decision_store = ag1.AgencyDecisionStore(state_root, namespace=lrp.NAMESPACE_AGENCY)
    integration_store = ag2.IntegrationStore(state_root, namespace=lrp.NAMESPACE_ACTIVITY)
    return {
        "decision_read": ag1.AgencyDecisionReadService(decision_store),
        "integration_read": ag2.IntegrationReadPort(integration_store),
    }


def _adoption_for(integration_read: Any, decision_id: str) -> tuple[Optional[dict], Optional[str]]:
    """返回 ``(adoption_record, error_code)``；读不到是具名错误，不是“没有 adoption”。"""

    if integration_read is None:
        return None, "ADOPTION_READER_UNAVAILABLE"
    try:
        st = integration_read.read_state() or {}
    except Exception as exc:  # 读坏了不能当成“从没采纳过”
        return None, "ADOPTION_STATE_UNREADABLE:%s" % type(exc).__name__
    aid = (st.get("adoption_id_by_decision") or {}).get(decision_id)
    if not aid:
        return None, None
    rec = (st.get("adoptions_by_id") or {}).get(aid)
    if rec is None:
        return None, "ADOPTION_INDEX_POINTS_TO_MISSING:%s" % aid
    return dict(rec), None


# ---------------------------------------------------------------------------------------------
# summary projection (F-049 / F-050)
# ---------------------------------------------------------------------------------------------


def _candidate_row(candidate_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
    reasons = [str(code) for code in (row.get("reason_codes") or []) if str(code)]
    return {
        "candidate_ref": str(candidate_id),
        "candidate_kind": row.get("candidate_kind") or NOT_RECORDED,
        "source_kind": row.get("source_kind") or NOT_RECORDED,
        "source_ref": row.get("source_ref") or NOT_RECORDED,
        "disposition": row.get("disposition") or NOT_RECORDED,
        # 记录里没有理由就明确未记录，不用模型补写。
        "reason_codes": reasons or [NOT_RECORDED],
        "executability": row.get("executability") or NOT_RECORDED,
    }


def summarize_decision(
    decision_read: Any,
    integration_read: Any,
    decision_id: str,
    *,
    include_rationale: bool = False,
) -> dict[str, Any]:
    """一条已提交决定的有限摘要；不返回候选正文、作品正文或完整请求。

    ``include_rationale=False``（默认）时只报告 ``short_rationale`` 是否存在与长度，
    正文不进入操作者读面；``True`` 仅在本地 CLI 的显式开关下使用。
    """

    if not isinstance(decision_id, str) or not decision_id.strip():
        return {
            "schema": SCHEMA_DECISION_REVIEW,
            "status": REVIEW_NOT_FOUND,
            "formed_decision": False,
            "note": "没有给出决定 id；不推测当时选了什么。",
        }
    decision_id = decision_id.strip()
    try:
        dec = decision_read.get_decision(decision_id)
    except Exception as exc:
        return {
            "schema": SCHEMA_DECISION_REVIEW,
            "status": REVIEW_UNREADABLE,
            "decision_id": decision_id,
            "formed_decision": False,
            "reason_code": "DECISION_STATE_UNREADABLE:%s" % type(exc).__name__,
            "note": "决定库读不出来；不把读不到说成没有决定。",
        }
    if dec is None:
        return {
            "schema": SCHEMA_DECISION_REVIEW,
            "status": REVIEW_NOT_FOUND,
            "decision_id": decision_id,
            "formed_decision": False,
            "note": "没有这条决定记录；不推测当时选了什么。",
        }

    try:
        dispositions = decision_read.get_candidate_dispositions(decision_id) or {}
    except Exception:
        # 处置表读不到：摘要仍然成立，但未选项一栏明确未记录，不编。
        dispositions = {}

    rows = [_candidate_row(cid, row) for cid, row in sorted(dispositions.items())]
    selected_ref = dec.get("selected_candidate_ref")
    selected = next((r for r in rows if r["candidate_ref"] == selected_ref), None) if selected_ref else None
    others = [r for r in rows if r["candidate_ref"] != selected_ref]
    deferred = [r for r in others if r["disposition"] == "DEFERRED"]
    rejected = [r for r in others if r["disposition"] == "HARD_REJECTED"]
    not_executable = [r for r in others if r["disposition"] == "NOT_EXECUTABLE_NOW"]
    not_selected = [r for r in others if r["disposition"] == "NOT_SELECTED"]

    adoption_raw, adoption_error = _adoption_for(integration_read, decision_id)
    adoption, decision_state = _adoption_view(dec, adoption_raw, adoption_error)

    reasons = [str(code) for code in (dec.get("reason_codes") or []) if str(code)]
    rationale = dec.get("short_rationale")
    rationale_view: dict[str, Any] = {
        "recorded": isinstance(rationale, str) and bool(rationale.strip()),
        "chars": len(rationale.strip()) if isinstance(rationale, str) and rationale.strip() else 0,
        "withheld": not include_rationale,
    }
    if include_rationale and isinstance(rationale, str) and rationale.strip():
        rationale_view["text"] = rationale.strip()

    return {
        "schema": SCHEMA_DECISION_REVIEW,
        "status": "OK",
        "formed_decision": True,
        "decision_id": decision_id,
        "decision": dec.get("decision"),
        "decision_route": dec.get("decision_route"),
        "created_at": dec.get("created_at"),
        "revision": dec.get("revision"),
        "reason_codes": reasons or [NOT_RECORDED],
        "rationale": rationale_view,
        # 摘要就是记录本身：没有 rationale 时退回 decision+reason codes，而不是编一句话。
        "summary": _honest_summary(dec, reasons),
        "selected": selected,
        "alternatives": others,
        "deferred": deferred,
        "hard_rejected": rejected,
        "not_executable_now": not_executable,
        "not_selected": not_selected,
        # 记录只保存真正影响了取舍的替代项时才长期保留；这里只报“有没有”。
        "alternatives_recorded": bool(others),
        "candidate_count_recorded": len(rows),
        "decision_state": decision_state,
        "adoption": adoption,
        "read_only": True,
        "note": (
            "决定是发生过的事实；只有 Adoption 产生当前或未来约束。未写的理由一律 NOT_RECORDED，"
            "不做事后补写。"
        ),
    }


def _adoption_view(dec: Mapping[str, Any], adoption_raw: Optional[Mapping[str, Any]],
                   error_code: Optional[str]) -> tuple[dict[str, Any], str]:
    if error_code:
        return (
            {"status": "UNKNOWN", "reason_code": error_code, "owner": "S09"},
            "ADOPTION_STATE_UNKNOWN",
        )
    if adoption_raw is None:
        return (
            {"status": "NOT_ADOPTED", "adoption_id": None, "owner": "S09",
             "note": "这条决定没有被 S09 采纳；它只是历史，不产生当前活动或未来承诺。"},
            STATE_DECIDED_NOT_ADOPTED,
        )
    status = str(adoption_raw.get("adoption_status") or "UNKNOWN")
    view = {
        "status": status,
        "adoption_id": adoption_raw.get("adoption_id"),
        "owner": "S09",
        "decision_ref": adoption_raw.get("decision_ref"),
        "decision_revision": adoption_raw.get("decision_revision"),
        "adopted_at": adoption_raw.get("adopted_at"),
        "recorded_at": adoption_raw.get("recorded_at"),
        "rejection_reason_code": adoption_raw.get("rejection_reason_code"),
        "activity_ref": (adoption_raw.get("created_activity_ref")
                         or adoption_raw.get("target_activity_ref")),
        "adoption_revision": adoption_raw.get("revision"),
    }
    if status.startswith("REJECTED"):
        return view, STATE_DECIDED_ADOPTION_REJECTED
    return view, STATE_DECIDED_AND_ADOPTED


def _honest_summary(dec: Mapping[str, Any], reasons: list[str]) -> str:
    text = dec.get("short_rationale")
    if isinstance(text, str) and text.strip():
        # 摘要正文只在 include_rationale 时输出；这里给的是骨架。
        return "记录了一条简短理由（正文按读面策略扣留）；决定=%s" % dec.get("decision")
    codes = ",".join(reasons) if reasons else NOT_RECORDED
    return "决定=%s，理由代码=%s（记录里没有 rationale 正文）" % (dec.get("decision"), codes)


# ---------------------------------------------------------------------------------------------
# recent decisions (bounded)
# ---------------------------------------------------------------------------------------------


def list_recent_decisions(decision_read: Any, limit: int = 10) -> list[dict[str, Any]]:
    """按 ``created_at`` 倒序给出有限的骨架，不返回正文、不返回全量审计日志。"""

    limit = max(1, min(int(limit), 50))
    try:
        st = decision_read.store.load_state() or {}
    except Exception as exc:
        return [{"status": REVIEW_UNREADABLE,
                 "reason_code": "DECISION_STATE_UNREADABLE:%s" % type(exc).__name__}]
    rows = list((st.get("decisions_by_id") or {}).values())
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    out: list[dict[str, Any]] = []
    for rec in rows[:limit]:
        out.append({
            "decision_id": rec.get("decision_id"),
            "decision": rec.get("decision"),
            "decision_route": rec.get("decision_route"),
            "created_at": rec.get("created_at"),
            "selected_candidate_ref": rec.get("selected_candidate_ref"),
            "reason_codes": [str(c) for c in (rec.get("reason_codes") or [])] or [NOT_RECORDED],
            "has_rationale": bool(str(rec.get("short_rationale") or "").strip()),
            "deferred_candidate_refs": list(rec.get("deferred_candidate_refs") or []),
        })
    return out


# ---------------------------------------------------------------------------------------------
# F-051: live applicability review through the existing admission rule
# ---------------------------------------------------------------------------------------------


def review_live_applicability(
    decision_read: Any,
    decision_id: str,
    *,
    validator: Optional[Callable[..., tuple[bool, Optional[str]]]] = None,
    check_at: Optional[str] = None,
) -> dict[str, Any]:
    """用**既有** AG-2 准入检查复核一条旧决定当前是否适用；纯读、零副作用。

    ``validator`` 是调用方传入的 ``validate_decision_for_adoption``（或等价正式读口）。
    这个模块不复制它的规则；没有正式读口时明确报 ``NOT_EVALUATED``，绝不从旧 snapshot
    宣称 ``CURRENT_VALID``。
    """

    if not isinstance(decision_id, str) or not decision_id.strip():
        return {"status": REVIEW_NOT_FOUND, "decision_id": None,
                "note": "没有给出决定 id。"}
    decision_id = decision_id.strip()

    if validator is None:
        return {
            "status": REVIEW_NOT_EVALUATED,
            "decision_id": decision_id,
            "reason_code": "NO_LIVE_ADMISSION_READER",
            "note": "只读摘要可用；实时适用性需要正式 AG-2 准入读口，本模块不复制该规则。",
        }

    try:
        dec = decision_read.get_decision(decision_id)
    except Exception as exc:
        return {"status": REVIEW_UNREADABLE, "decision_id": decision_id,
                "reason_code": "DECISION_STATE_UNREADABLE:%s" % type(exc).__name__}
    if dec is None:
        return {"status": REVIEW_NOT_FOUND, "decision_id": decision_id}

    try:
        import agency_decision_ag1 as ag1
        decision = ag1.DecisionRecord.from_dict(dec)
    except Exception as exc:
        return {
            "status": REVIEW_UNREADABLE,
            "decision_id": decision_id,
            "reason_code": "DECISION_RECORD_UNREADABLE:%s" % type(exc).__name__,
            "note": "记录读得出来但无法按不可变记录重建；不据此宣称仍适用。",
        }

    try:
        ok, reason = validator(decision, check_at=check_at)
    except TypeError:
        # 调用方给的是不带 check_at 的等价读口。
        ok, reason = validator(decision)
    except Exception as exc:
        return {
            "status": REVIEW_NOT_EVALUATED,
            "decision_id": decision_id,
            "reason_code": "ADMISSION_READER_ERROR:%s" % type(exc).__name__,
            "note": "正式准入读口报错；结论按未评估处理。",
        }

    return {
        "status": REVIEW_CURRENT_VALID if ok else REVIEW_REJECTED,
        "decision_id": decision_id,
        "reason_code": None if ok else (reason or "UNSPECIFIED_REJECTION"),
        "checked_at": check_at,
        "note": (
            "这是复核结论，不是再次采用：即便 CURRENT_VALID 也只说明按现有准入可以重新进入"
            "既定流程，不继承旧许可、不自动执行。"
        ),
        "read_only": True,
    }
