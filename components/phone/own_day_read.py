#!/usr/bin/env python3
"""v0.5 手机只读面：她今天自己排的事、外部义务，以及可证的执行状态。

本模块**不拥有任何事实**，也不新增权威：

* 自选计划读 ``life_day_plan.DayPlan``（只存 ``fitem:`` 引用），状态按
  ``life_day_plan.project_plan_entries`` 的既有投影；``PLANNED`` 只表示「排进了今天」，
  不是「还没开始」，更不是完成。
* 外部义务读 ``life_day_plan.DayObligations``（operator 写、Life 只读），结果复用
  ``life_day_plan.persisted_obligation_evidence`` 的既有规则：只有 Action 目标同一、
  Decision 是 ``START``、其 ``selected_candidate_ref`` 正是该义务确定性预留位候选、且
  Action 成功并带有 owner 版本回执，才算 ``DONE``；没有回执是义务域既有的 ``PENDING``，
  关联不足是 ``UNKNOWN``。同作品的新版本、另一个 Decision 的动作都不认领完成。
* 今天的 Activity 读 ``canonical_life_state.json``；只有它的 ``decision_ref`` 或其中 Action
  的 ``decision_ref`` 精确选中了某条安排的确定性候选时，才算「已排进去」。缺 Decision /
  Decision 不可读 / 目标像但关联不能证明时，标 ``UNKNOWN``，不硬分成「未排」。
* 「未排进去的活动」只列能证明与今天任何安排无关的那些；证明不了就进 ``UNKNOWN`` 桶。

权限、host/origin 守护、来源扣留与业务日界沿用周围合同：日期只取业务 clock（+08:00，
缺失才按既有规则退回系统时钟并具名）。读取只走既有 owner 的读口与文件，零写；不启动
服务、不调用模型，也不创建结果 store。

为什么候选 id 要在这里重算
--------------------------
``life_day_plan`` 只给了义务的 ``obligation_option_ref``（公开）；自选计划的 ``popt:``
拼法在 ``reserved_options`` 内部，没有单独的公开函数。手机端只读面按同一公式重算一次，
并在 ``tests/test_phone_own_day_read.py`` 用真实 Decision 候选 id 钉住；若公式漂移，
这些执行状态会诚实地落回 ``UNKNOWN``，不会误报完成。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from . import service as phone_service

#: The response schema of ``GET /api/phone/today/arrangements``.
ARRANGEMENTS_SCHEMA = "phone.today_arrangements.v1"

#: Bounded read: one operator may define many obligations, but the phone screen never pages.
MAX_OBLIGATION_ROWS = 50
MAX_ACTIVITY_ROWS = 200

PLAN_KIND = "SELF_PLANNED"
OBLIGATION_KIND = "OBLIGATION"

ASSOCIATION_ARRANGEMENT = "ARRANGEMENT"
ASSOCIATION_UNPLANNED = "UNPLANNED"
ASSOCIATION_UNKNOWN = "UNKNOWN"

#: Execution states this read surface may speak.  ``DONE`` only ever comes from the persisted
#: Action/Decision/owner-receipt chain; ``UNKNOWN`` is every honest non-conclusion.
EXECUTION_DONE = "DONE"
EXECUTION_UNKNOWN = "UNKNOWN"


class _ReadersUnavailable(RuntimeError):
    """A read port could not be built; the caller reports it by name, never as empty."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}:{detail}"[:200])
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:160]


# ---------------------------------------------------------------------------------------------
# lazy life imports (service.py already put components/life/scripts on sys.path)
# ---------------------------------------------------------------------------------------------


def _ldp():
    import life_day_plan as module

    return module


def _future_items():
    import life_future_items as module

    return module


def _candidate_id(source_ref: Any, revision: int = 1) -> str:
    from artifact_continuation_source import ArtifactContinuationSource

    return ArtifactContinuationSource.extra_candidate_id(source_ref, revision)


def _plan_option_ref(ldp: Any, plan_ref: Any, item_ref: Any, day: str) -> str:
    """The same deterministic ``popt:`` ref ``life_day_plan.reserved_options`` builds.

    Kept byte-for-byte equal to that formula (kind | plan_ref | item_ref | day); the test pins it
    against a real reserved option.  A drift makes the read fall back to ``UNKNOWN``, never DONE.
    """

    identity = "|".join([ldp.KIND_SELF_PLANNED, str(plan_ref), str(item_ref), day])
    return "popt:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _reason_code(exc: BaseException, default: str) -> str:
    value = getattr(exc, "reason_code", None)
    return str(value or type(exc).__name__ or default)[:80]


def _detail(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:120]


# ---------------------------------------------------------------------------------------------
# execution read ports (the same stores the Life runtime reads; pure read)
# ---------------------------------------------------------------------------------------------


def build_action_reader(state_root: Path | str):
    """AR-0's own read port over this state root, or a named refusal."""

    try:
        import action_reality_ledger as ar0
        import life_runtime_production as lrp
    except Exception as exc:  # pragma: no cover - import environment
        raise _ReadersUnavailable("ACTION_READER_UNAVAILABLE", _detail(exc)) from exc
    try:
        store = ar0.ActionStore(state_root, namespace=lrp.NAMESPACE_AGENCY)
        return ar0.ActionReadService(store)
    except Exception as exc:
        raise _ReadersUnavailable("ACTION_READER_UNAVAILABLE", _detail(exc)) from exc


def build_decision_reader(state_root: Path | str):
    """AG-1's own read port over this state root, or a named refusal."""

    try:
        import agency_decision_ag1 as ag1
        import life_runtime_production as lrp
    except Exception as exc:  # pragma: no cover - import environment
        raise _ReadersUnavailable("DECISION_READER_UNAVAILABLE", _detail(exc)) from exc
    try:
        store = ag1.AgencyDecisionStore(state_root, namespace=lrp.NAMESPACE_AGENCY)
        return ag1.AgencyDecisionReadService(store)
    except Exception as exc:
        raise _ReadersUnavailable("DECISION_READER_UNAVAILABLE", _detail(exc)) from exc


def _decision_review_module():
    """S04's read-only decision projection (same package tree as the AG-1 readers)."""

    import decision_review as _dr
    return _dr


def decision_review_envelope(*, state_root: Path | str, decision_id: Optional[str] = None,
                             limit: int = 10) -> dict[str, Any]:
    """``GET /api/phone/decisions/review``：决定的有限摘要、已记录未选项、Adoption 状态。

    纯读：只读 AG-1 的不可变 ``DecisionRecord``/处置表与 AG-2 自己的 Adoption 记录。
    rationale 正文在手机读面**始终扣留**（只报是否记录、长度）；实时适用性复核需要正式
    AG-2 准入读口，手机侧不复制规则，明确报 ``NOT_EVALUATED``。读取不写任何状态。
    """

    dr = _decision_review_module()
    try:
        readers = dr.open_readers(state_root)
    except Exception as exc:
        return phone_service.unavailable(
            "DECISION_REVIEW_READERS_UNAVAILABLE", read_only=True,
            detail="%s: %s" % (type(exc).__name__, exc),
            note="决定读口起不来；不把读不到说成没有决定。")
    if decision_id:
        row = dr.summarize_decision(
            readers["decision_read"], readers["integration_read"], decision_id,
            include_rationale=False)
        row["live_applicability"] = dr.review_live_applicability(
            readers["decision_read"], decision_id)
        return phone_service.envelope(
            True, phone_service.STATUS_OK,
            {"read_only": True, "decision": row})
    rows = dr.list_recent_decisions(readers["decision_read"], limit=limit)
    return phone_service.envelope(
        True, phone_service.STATUS_OK,
        {
            "read_only": True,
            "limit": max(1, min(int(limit), 50)),
            "count": len(rows),
            "decisions": rows,
            "note": "只返回有限骨架；选中项/未选项/adoption 需按 decision_id 单查。",
        })


# ---------------------------------------------------------------------------------------------
# today's activities (canonical authority; same day boundary as ``/today``)
# ---------------------------------------------------------------------------------------------


#: Activity statuses which are final; a successor is a new Activity, not a resume.
#: 2026-10-28: FAILED is terminal (the goal scope was assessed unreachable).  BLOCKED is
#: deliberately NOT in this set: it is suspended and can come back through an OPEN eligibility.
TERMINAL_ACTIVITY_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "ABANDONED", "CANCELLED", "EXPIRED"})

RECOVERY_ELIGIBILITY_OPEN = "RESUME_ELIGIBILITY_OPEN"
RECOVERY_CONDITION_PENDING = "RESUME_CONDITION_PENDING"
RECOVERY_NO_OPEN_ELIGIBILITY = "NO_OPEN_ELIGIBILITY"
RECOVERY_ACTIVE = "ACTIVE"
RECOVERY_TERMINAL_PREFIX = "TERMINAL_"
RECOVERY_ELIGIBILITY_UNREADABLE = "ELIGIBILITY_UNREADABLE"
RECOVERY_STATE_UNRECOGNIZED = "STATUS_UNRECOGNIZED"


def _load_waiting_store(state_root: Path | str, *parts: str
                        ) -> tuple[Optional[dict[str, Any]], str]:
    """Read one LR-4 aggregate state file; never turn a read failure into "no facts".

    Returns ``(payload, state)`` where ``state`` is ``"ok"``, ``"absent"`` (the store
    has simply never been created -- the normal empty case), ``"unreadable"`` or
    ``"corrupt"``.  Only ``ok``/``absent`` mean "there is no such record"; the other
    two are reported by name so a caller can say *unknown* instead of *none*.
    """

    path = Path(str(state_root)).joinpath(*parts)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, "absent"
    except OSError:
        return None, "unreadable"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "corrupt"
    return (data if isinstance(data, dict) else None), "ok"


def _eligibility_is_open(row: Mapping[str, Any], now: datetime) -> bool:
    if str(row.get("status") or "") != "OPEN":
        return False
    expires = row.get("expires_at")
    if not expires:
        return True
    try:
        moment = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment > now


def activity_recovery_projection(state_root: Path | str
                                 ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read-only owner facts: can this Activity come back, and through which entry?

    The projection deliberately speaks only what the LR-4 stores already say:

    * an ``OPEN`` (unexpired) ``ResumeEligibility`` for this Activity -> ``RESUME``;
    * a ``PENDING`` ``ResumeCondition`` -> the dependency is still pending, no entry;
    * a terminal Activity -> no resume (a successor is a new Activity);
    * an open normal Activity with neither -> ``NO_OPEN_ELIGIBILITY`` (no invented timer);
    * an ``ACTIVE`` foreground -> ``ACTIVE`` (CONTINUE legality also depends on step
      readiness, which is not a phone fact, so no next entry is claimed).

    A missing store file is the ordinary empty case.  An unreadable/corrupt store is
    reported as ``ELIGIBILITY_UNREADABLE`` and never silently treated as "none".
    """

    eligibility_state, eligibility_read = _load_waiting_store(
        state_root, "waiting_agenda", "eligibility", "resume_eligibility_state.json")
    condition_state, condition_read = _load_waiting_store(
        state_root, "waiting_agenda", "resume_conditions", "resume_conditions_state.json")
    unreadable = eligibility_read in {"unreadable", "corrupt"} \
        or condition_read in {"unreadable", "corrupt"}
    now = datetime.now(timezone.utc)
    open_eligibility: dict[str, dict[str, Any]] = {}
    for row in ((eligibility_state or {}).get("eligibilities") or {}).values():
        if isinstance(row, Mapping) and _eligibility_is_open(row, now):
            activity_id = str(row.get("activity_id") or "")
            if activity_id:
                open_eligibility[activity_id] = dict(row)
    pending_condition: dict[str, dict[str, Any]] = {}
    for row in ((condition_state or {}).get("conditions") or {}).values():
        if isinstance(row, Mapping) and str(row.get("status") or "") == "PENDING":
            activity_id = str(row.get("activity_id") or "")
            if activity_id:
                pending_condition[activity_id] = dict(row)
    source = {
        "state": "ok" if not unreadable else "unavailable",
        "eligibility": eligibility_read,
        "condition": condition_read,
        "reason_code": RECOVERY_ELIGIBILITY_UNREADABLE if unreadable else None,
    }
    return {
        "eligibility": open_eligibility,
        "condition": pending_condition,
        "unreadable": unreadable,
        "source": source,
    }, source


def recovery_row(activity: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """One Activity's recovery view, purely from the owner facts handed in."""

    status = str(activity.get("status") or "")
    activity_id = str(activity.get("ref") or activity.get("activity_id") or "")

    def _with_blocked(row: dict[str, Any]) -> dict[str, Any]:
        # BLOCKED is not a failure and not a pause: show the owner's own basis/reason next to
        # the recovery fact.  Only structured refs/reason codes leave the owner; no free text.
        if status == "BLOCKED":
            row.setdefault("blocked_on_ref", activity.get("blocked_on_ref"))
            row.setdefault("blocked_reason_code", activity.get("blocked_reason_code"))
        return row

    if status in TERMINAL_ACTIVITY_STATUSES:
        return _with_blocked({"recoverable": False,
                              "reason_code": RECOVERY_TERMINAL_PREFIX + status,
                              "next_legal_entry": None})
    if facts.get("unreadable"):
        return _with_blocked({"recoverable": None,
                              "reason_code": RECOVERY_ELIGIBILITY_UNREADABLE,
                              "next_legal_entry": None})
    if status == "ACTIVE":
        return _with_blocked({"recoverable": None, "reason_code": RECOVERY_ACTIVE,
                              "next_legal_entry": None})
    eligibility = (facts.get("eligibility") or {}).get(activity_id)
    if eligibility is not None:
        return _with_blocked({"recoverable": True, "reason_code": RECOVERY_ELIGIBILITY_OPEN,
                              "next_legal_entry": "RESUME",
                              "eligibility_ref": eligibility.get("eligibility_id"),
                              "activity_revision": eligibility.get("activity_revision")})
    condition = (facts.get("condition") or {}).get(activity_id)
    if condition is not None:
        return _with_blocked({"recoverable": False, "reason_code": RECOVERY_CONDITION_PENDING,
                              "next_legal_entry": None,
                              "condition_ref": condition.get("condition_id"),
                              "waiting_on_ref": condition.get("waiting_on_ref")})
    if status in {"WAITING", "PAUSED", "BLOCKED"}:
        return _with_blocked({"recoverable": False,
                              "reason_code": RECOVERY_NO_OPEN_ELIGIBILITY,
                              "next_legal_entry": None})
    return _with_blocked({"recoverable": None, "reason_code": RECOVERY_STATE_UNRECOGNIZED,
                          "next_legal_entry": None})


def _external_stop_projection(act: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The durable stop intent, read-only.  ``None`` when this Activity never had one."""

    stop = act.get("external_stop")
    if not isinstance(stop, Mapping):
        return None
    refs = stop.get("unresolved_action_refs")
    return {
        "state": str(stop.get("state") or "") or None,
        "intent_id": str(stop.get("intent_id") or "") or None,
        "reason_code": str(stop.get("reason_code") or "") or None,
        "requested_at": stop.get("requested_at"),
        "accepted_at": stop.get("accepted_at"),
        "finalized_at": stop.get("finalized_at"),
        "boundary_mode": str(stop.get("boundary_mode") or "") or None,
        "unresolved_action_refs": ([str(value) for value in refs if isinstance(value, str)][:20]
                                   if isinstance(refs, (list, tuple)) else []),
        "by_local_operator": str(stop.get("authority") or "") == "LOCAL_OPERATOR",
    }


def read_today_activities(state_root: Path | str, day: str
                          ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Public alias of the canonical Activity read used by ``/today``'s day rule.

    ``day_timeline`` (2026-10-27) reuses this instead of parsing the canonical file a third
    time: one day rule, one set of named failure codes, and the same ``decision_ref`` the
    arrangement association already relies on.  It stays a pure read.
    """

    return _read_activities(state_root, day)


def _read_activities(state_root: Path | str, day: str
                     ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Today's Activity rows with the owner's own ``decision_ref``/``source_refs``.

    The existing ``/today`` projection deliberately carries no decision ref.  This read needs it
    to associate an activity with an arrangement without guessing from the title, so it parses
    the same canonical file with the same +08:00 day rule and the same named failure codes.
    """

    path = Path(str(state_root)) / phone_service.CANONICAL_ACTIVITY_FILENAME
    source: dict[str, Any] = {
        "state": "ok", "source": phone_service.CANONICAL_ACTIVITY_FILENAME,
        "reason_code": None, "detail": None, "count": 0,
        "activities_without_started_at": 0, "truncated": False,
    }
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_ABSENT"})
        return [], source
    except OSError as exc:
        source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_UNREADABLE",
                       "detail": type(exc).__name__})
        return [], source
    try:
        data: Any = json.loads(raw)
    except ValueError:
        source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_CORRUPT"})
        return [], source
    activities = data.get("activities") if isinstance(data, Mapping) else None
    if not isinstance(activities, Mapping):
        source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_CORRUPT",
                       "detail": "activities"})
        return [], source
    rows: list[dict[str, Any]] = []
    for ref, act in activities.items():
        if not isinstance(act, Mapping):
            continue
        started = phone_service.today_moment(act.get("started_at"))
        if started is None:
            source["activities_without_started_at"] += 1
            continue
        if started.date().isoformat() != day:
            continue
        refs = act.get("source_refs")
        source_refs = [str(value) for value in refs if isinstance(value, str)][:20] \
            if isinstance(refs, (list, tuple)) else []
        # 2026-10-28: the read face carries the owner's status vocabulary as-is.  FAILED and
        # BLOCKED add only structured refs and reason codes; no private content is projected.
        status = str(act.get("status") or "")
        blocked_reason = act.get("blocked_reason_code")

        def _owner_refs(key: str, limit: int = 20) -> list[str]:
            """Only the owner's own structured refs; never free text or private content."""
            raw = act.get(key)
            if not isinstance(raw, (list, tuple)):
                return []
            return [str(value) for value in raw if isinstance(value, str)][:limit]

        rows.append({
            "ref": str(ref),
            # 2026-10-28: an external stop is the *operator's* act, so the read face says so
            # by name instead of letting a CANCELLED row look like her own choice.  Only
            # structured refs and codes; never a body or a token.
            "external_stop": _external_stop_projection(act),
            "title": str(act.get("title") or "")[:200],
            "kind": str(act.get("activity_kind") or ""),
            "status": status,
            "started_at": started.isoformat(timespec="seconds"),
            "ended_at": phone_service.today_iso(act.get("ended_at")),
            "terminal_reason_code": act.get("terminal_reason_code"),
            "status_reason_code": act.get("terminal_reason_code") or blocked_reason,
            "blocked_reason_code": blocked_reason,
            "blocked_on_ref": act.get("blocked_on_ref"),
            "blocked_criteria_refs": _owner_refs("blocked_criteria_refs"),
            "failure_assessment_ref": act.get("failure_assessment_ref"),
            # The judged criterion and the re-readable evidence are owner facts; the next
            # round must be able to read them here instead of seeing only a generic status.
            "failure_criteria_refs": _owner_refs("failure_criteria_refs"),
            "failure_evidence_refs": _owner_refs("failure_evidence_refs"),
            "unresolved_action_refs": _owner_refs("unresolved_action_refs"),
            "unresolved_action_count": len(act.get("unresolved_action_refs") or []),
            "decision_ref": str(act.get("decision_ref") or "") or None,
            "source_refs": source_refs,
        })
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("ref") or "")))
    facts, recovery_source = activity_recovery_projection(state_root)
    for row in rows:
        row["recovery"] = recovery_row(row, facts)
    source["recovery"] = recovery_source
    source["count"] = len(rows)
    if len(rows) > MAX_ACTIVITY_ROWS:
        rows = rows[:MAX_ACTIVITY_ROWS]
        source["truncated"] = True
    return rows, source


# ---------------------------------------------------------------------------------------------
# plan / obligation projections
# ---------------------------------------------------------------------------------------------


def _first_artifact_ref(item: Optional[Mapping[str, Any]], ldp: Any) -> Optional[str]:
    if not isinstance(item, Mapping):
        return None
    for ref in item.get("source_refs") or ():
        identity = ldp.artifact_identity(ref)
        if identity:
            return identity
    return None


def _matching_owner_receipt(action_id: Any, *, receipt_lister: Any) -> Optional[dict[str, Any]]:
    """The last owner receipt for this Action that claims success and names a version."""

    if not action_id or not callable(receipt_lister):
        return None
    try:
        receipts = [dict(row) for row in (receipt_lister(str(action_id)) or [])
                    if isinstance(row, Mapping)]
    except Exception:
        return None
    for receipt in reversed(receipts):
        details = receipt.get("details")
        details = dict(details) if isinstance(details, Mapping) else {}
        if str(receipt.get("status_claim") or "").upper() == "SUCCEEDED" \
                and details.get("version_id"):
            return receipt
    return None


def _unknown(reason_code: str, *, evidence: Optional[Mapping[str, Any]] = None
             ) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": EXECUTION_UNKNOWN, "reason_code": str(reason_code)}
    if evidence:
        payload["evidence"] = dict(evidence)
    return payload


def _plan_execution(
    entry: Mapping[str, Any],
    *,
    ldp: Any,
    day: str,
    actions: Optional[list[dict[str, Any]]],
    actions_ok: bool,
    decision_getter: Any,
    decisions_ok: bool,
    receipt_lister: Any,
) -> dict[str, Any]:
    """Prove a self-planned entry's execution from the persisted Action/Decision/receipt chain."""

    if not (actions_ok and decisions_ok):
        return _unknown("EXECUTION_SOURCE_UNAVAILABLE")
    item = None
    try:
        item = entry.get("_item")
    except Exception:
        item = None
    target = _first_artifact_ref(item, ldp)
    if target is None:
        # No bound work object: an activity may exist, but no owner receipt can prove a version.
        return _unknown("PLAN_WORK_TARGET_UNKNOWN")
    try:
        option_ref = _plan_option_ref(ldp, entry.get("plan_ref"), entry.get("item_ref"), day)
        expected_candidate = _candidate_id(option_ref, 1)
    except Exception as exc:
        return _unknown(_reason_code(exc, "PLAN_CANDIDATE_ID_UNREADABLE"))
    matched: list[dict[str, Any]] = []
    unverified: Optional[str] = None
    for action in actions or ():
        if ldp.artifact_identity(action.get("target_ref")) != target:
            continue
        decision_ref = str(action.get("decision_ref") or "")
        decision = decision_getter(decision_ref) if decision_ref else None
        if not isinstance(decision, Mapping):
            # There *is* an Action on this plan's own work object, but the Decision that would
            # prove (or rule out) the plan is not readable.  That is UNKNOWN, not "no evidence".
            unverified = unverified or (
                "DECISION_NOT_READABLE" if decision_ref else "ACTION_DECISION_REF_MISSING")
            continue
        if str(decision.get("decision") or "").upper() != "START":
            continue
        if str(decision.get("selected_candidate_ref") or "") != expected_candidate:
            continue
        matched.append(action)
    if not matched:
        return _unknown(unverified or "NO_EXECUTION_EVIDENCE")
    fallback = "EXECUTION_NOT_SETTLED"
    for action in matched:
        status = str(action.get("status") or "").upper()
        activity_ref = str(action.get("activity_ref") or "")
        if status == "UNKNOWN":
            fallback = "ACTION_OUTCOME_UNKNOWN"
            continue
        if status in {"FAILED", "CANCELLED"}:
            fallback = "ACTION_NOT_SUCCEEDED"
            continue
        if status != "SUCCEEDED":
            continue
        if not activity_ref:
            fallback = "OBLIGATION_ACTIVITY_ASSOCIATION_MISSING"
            continue
        receipt = _matching_owner_receipt(action.get("action_id"),
                                          receipt_lister=receipt_lister)
        details = dict((receipt or {}).get("details") or {})
        version_id = str(details.get("version_id") or "")
        if receipt is None or not version_id:
            fallback = "OWNER_RECEIPT_MISSING"
            continue
        return {
            "state": EXECUTION_DONE,
            "reason_code": None,
            "evidence": {
                "artifact_ref": f"artifact:{target}",
                "version_id": version_id,
                "version_number": details.get("version_number"),
                "decision_id": str(action.get("decision_ref") or ""),
                "activity_ref": activity_ref,
                "action_id": str(action.get("action_id") or ""),
                "receipt_ref": str(receipt.get("receipt_id") or ""),
                "observed_at": str(receipt.get("observed_at") or ""),
                "evidence_source": ldp.COMPLETION_KIND_STEP_RECEIPT,
                "evidence_path": ldp.EVIDENCE_PATH_PERSISTED,
            },
        }
    return _unknown(fallback)


def _obligation_rows(
    *,
    ldp: Any,
    store: Any,
    day: str,
    now: Optional[str],
    actions: Optional[list[dict[str, Any]]],
    actions_ok: bool,
    decision_getter: Any,
    decisions_ok: bool,
    receipt_lister: Any,
) -> tuple[list[dict[str, Any]], bool, int]:
    """The obligation domain's own result projection plus a uniform ``execution`` row."""

    entries = store.for_date(day)
    if actions_ok and decisions_ok:
        try:
            evidence = ldp.persisted_obligation_evidence(
                entries, day=day,
                action_lister=lambda: list(actions or []),
                decision_getter=decision_getter,
                receipt_lister=receipt_lister,
                candidate_id_for=_candidate_id)
        except Exception:
            evidence = {str(row.get("obligation_ref")): {
                "status": ldp.OBLIGATION_RESULT_UNKNOWN,
                "evidence_path": ldp.EVIDENCE_PATH_PERSISTED,
                "reason_code": "OBLIGATION_EVIDENCE_READ_FAILED",
            } for row in entries if row.get("obligation_ref")}
    else:
        evidence = {str(row.get("obligation_ref")): {
            "status": ldp.OBLIGATION_RESULT_UNKNOWN,
            "evidence_path": ldp.EVIDENCE_PATH_PERSISTED,
            "reason_code": "EXECUTION_SOURCE_UNAVAILABLE",
        } for row in entries if row.get("obligation_ref")}
    view = ldp.obligation_result_view(store, date=day, now=now, evidence=evidence)
    rows: list[dict[str, Any]] = []
    for raw in view.get("entries") or ():
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        found = row.get("evidence")
        found = dict(found) if isinstance(found, Mapping) else {}
        # ``result`` is the obligation domain's own word (PENDING / DONE / UNKNOWN).  PENDING
        # means "no owner receipt yet", never "not started" and never a failure.
        row["execution"] = {
            "state": str(row.get("result") or ldp.OBLIGATION_RESULT_UNKNOWN),
            "evidence_path": found.get("evidence_path"),
            "reason_code": found.get("reason_code"),
        }
        rows.append(row)
    total = len(rows)
    # The caller associates activities against **all** of them, then trims only the visible
    # list; a truncated row still proves its own Activity, so it cannot make that Activity look
    # "unplanned" just because it fell off the screen.
    return rows, total > MAX_OBLIGATION_ROWS, total


# ---------------------------------------------------------------------------------------------
# activity association
# ---------------------------------------------------------------------------------------------


def _arrangement_candidate(ldp: Any, row: Mapping[str, Any], day: str
                           ) -> Optional[dict[str, Any]]:
    """One arrangement's exact reserved candidate identity, or ``None`` when unprovable."""

    if str(row.get("kind")) == OBLIGATION_KIND:
        ref = str(row.get("ref") or "")
        target = ldp.owner_ref(row.get("target_ref"))
        if not ref or target is None:
            return None
        try:
            option_ref = ldp.obligation_option_ref(ref, target, day)
        except Exception:
            return None
    else:
        ref = str(row.get("ref") or "")
        item_ref = str(row.get("item_ref") or "")
        if not ref or not item_ref:
            return None
        try:
            option_ref = _plan_option_ref(ldp, ref, item_ref, day)
        except Exception:
            return None
    try:
        candidate = _candidate_id(option_ref, 1)
    except Exception:
        return None
    return {"ref": str(row.get("ref") or ""), "kind": str(row.get("kind") or ""),
            "option_ref": option_ref, "candidate": candidate,
            "target": ldp.artifact_identity(row.get("target_ref")) or None}


def _target_matches(action: Mapping[str, Any], arrangements: list[dict[str, Any]],
                    ldp: Any) -> bool:
    target = ldp.artifact_identity(action.get("target_ref"))
    return bool(target and any(row.get("target") == target for row in arrangements))


def _associate_activities(
    activities: list[dict[str, Any]],
    arrangements: list[dict[str, Any]],
    *,
    ldp: Any,
    actions: Optional[list[dict[str, Any]]],
    actions_ok: bool,
    decision_getter: Any,
    decisions_ok: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Split today's activities into proven-unplanned and honestly-unknown.

    ``ARRANGEMENT`` rows are not returned; their refs are attached to the arrangement's own
    execution view only when the owner receipt names them.  An activity whose decision or action
    link cannot be proven is ``UNKNOWN`` -- never silently called "not planned".
    """

    if not arrangements:
        # Nothing was arranged today: every Activity that happened is unplanned by definition,
        # and no Decision read is needed to prove it.
        return ([dict(activity, association=ASSOCIATION_UNPLANNED)
                 for activity in activities], [], {})

    by_candidate = {row["candidate"]: row for row in arrangements}
    actions_by_activity: dict[str, list[dict[str, Any]]] = {}
    for action in actions or ():
        activity_ref = str(action.get("activity_ref") or "")
        if activity_ref:
            actions_by_activity.setdefault(activity_ref, []).append(dict(action))
    unplanned: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    associated: dict[str, list[dict[str, Any]]] = {}
    for activity in activities:
        ref = str(activity.get("ref") or "")
        matched: dict[str, dict[str, Any]] = {}
        unverified: Optional[str] = None
        known_signal = False
        decision_ref = str(activity.get("decision_ref") or "")
        if decision_ref:
            decision = decision_getter(decision_ref) if decisions_ok else None
            if not decisions_ok:
                unverified = "DECISION_READER_UNAVAILABLE"
            elif decision is None:
                unverified = "DECISION_NOT_READABLE"
            else:
                known_signal = True
                if str(decision.get("decision") or "").upper() == "START":
                    found = by_candidate.get(
                        str(decision.get("selected_candidate_ref") or ""))
                    if found is not None:
                        matched[found["ref"]] = found
        if not actions_ok and arrangements and not matched:
            # Without the Action ledger an Activity could still be linked to an arrangement by
            # the Action's own decision_ref; not being able to rule that out is UNKNOWN, not
            # "未排".
            unverified = unverified or "ACTION_READER_UNAVAILABLE"
        action_rows = actions_by_activity.get(ref, [])
        for action in action_rows:
            action_decision_ref = str(action.get("decision_ref") or "")
            if not action_decision_ref:
                if _target_matches(action, arrangements, ldp):
                    unverified = unverified or "ACTION_DECISION_REF_MISSING"
                continue
            if not decisions_ok:
                if _target_matches(action, arrangements, ldp):
                    unverified = unverified or "DECISION_READER_UNAVAILABLE"
                continue
            decision = decision_getter(action_decision_ref)
            if decision is None:
                if _target_matches(action, arrangements, ldp):
                    unverified = unverified or "DECISION_NOT_READABLE"
                continue
            known_signal = True
            if str(decision.get("decision") or "").upper() != "START":
                continue
            found = by_candidate.get(str(decision.get("selected_candidate_ref") or ""))
            if found is not None:
                matched[found["ref"]] = found
        if matched:
            for arrangement_ref in matched:
                associated.setdefault(arrangement_ref, []).append({
                    "activity_ref": ref,
                    "title": activity.get("title"),
                    "status": activity.get("status"),
                    "started_at": activity.get("started_at"),
                })
            continue
        row = dict(activity)
        if unverified is not None:
            row["association"] = ASSOCIATION_UNKNOWN
            row["reason_code"] = unverified
            unknown.append(row)
        elif arrangements and not decision_ref and not action_rows:
            # Nothing in this Activity can be tied to any arrangement (or shown unrelated):
            # an Activity with no decision link at all is not called "未排" by default.
            row["association"] = ASSOCIATION_UNKNOWN
            row["reason_code"] = "DECISION_REF_MISSING"
            unknown.append(row)
        elif arrangements and not known_signal and not decision_ref and action_rows:
            row["association"] = ASSOCIATION_UNKNOWN
            row["reason_code"] = "DECISION_LINK_UNREADABLE"
            unknown.append(row)
        else:
            row["association"] = ASSOCIATION_UNPLANNED
            unplanned.append(row)
    return unplanned, unknown, associated


# ---------------------------------------------------------------------------------------------
# public projection
# ---------------------------------------------------------------------------------------------


def arrangements_envelope(*, state_root: Path | str, subject_id: Optional[str],
                          clock: Mapping[str, Any]) -> dict[str, Any]:
    """Build the one read-only envelope for ``GET /api/phone/today/arrangements``."""

    ldp = _ldp()
    moment = phone_service.today_moment((clock or {}).get("logical_at"))
    if moment is None:
        return phone_service.unavailable("BUSINESS_CLOCK_INVALID",
                                         clock=dict(clock or {}),
                                         note="业务时钟读不出当天；不猜日期")
    now_iso = moment.isoformat(timespec="seconds")
    day = ldp.business_date(now_iso)

    # -- self-planned entries -------------------------------------------------------------
    plan_source: dict[str, Any] = {"state": "ok", "reason_code": None, "detail": None,
                                   "count": 0, "truncated": False}
    plan_rows: list[dict[str, Any]] = []
    plan_items: dict[str, Optional[dict[str, Any]]] = {}
    futures_lookup = _futures_lookup(state_root, subject_id)
    try:
        plan_store = ldp.DayPlan(state_root, subject_id=subject_id)
        plan_entries = plan_store.for_date(day)
        projected = ldp.project_plan_entries(plan_entries, futures_lookup=futures_lookup)
    except Exception as exc:
        plan_source.update({"state": "unavailable",
                            "reason_code": _reason_code(exc, "DAY_PLAN_READ_FAILED"),
                            "detail": _detail(exc)})
        projected = []
    for row in projected:
        item = _item_for(futures_lookup, row.get("item_ref"))
        plan_ref = str(row.get("plan_ref") or "")
        plan_items[plan_ref] = item
        plan_rows.append({
            "plan_ref": plan_ref,
            "item_ref": str(row.get("item_ref") or ""),
            "date": str(row.get("date") or ""),
            "class": str(row.get("class") or ldp.CLASS_SELF_PLANNED),
            "definition_status": str(row.get("status") or ""),
            "reason_code": row.get("reason_code"),
            "title": row.get("title"),
            "due_at": row.get("due_at"),
            "due": bool(row.get("due")),
        })
    plan_source["count"] = len(plan_rows)

    # -- execution read ports ------------------------------------------------------------
    receipt_lister: Any = None
    actions: Optional[list[dict[str, Any]]] = None
    actions_ok = False
    decision_getter: Any = None
    decisions_ok = False
    execution_source: dict[str, Any] = {"state": "ok", "reason_code": None, "detail": None}
    try:
        action_read = build_action_reader(state_root)
        try:
            actions = [dict(row) for row in (action_read.list_actions() or [])
                       if isinstance(row, Mapping)]
            actions_ok = True
            receipt_lister = getattr(action_read, "list_receipts", None)
        except Exception as exc:
            execution_source.update({"state": "unavailable",
                                     "reason_code": _reason_code(exc, "ACTION_READ_FAILED"),
                                     "detail": _detail(exc)})
    except _ReadersUnavailable as exc:
        execution_source.update({"state": "unavailable", "reason_code": exc.reason_code,
                                 "detail": exc.detail})
    try:
        decision_read = build_decision_reader(state_root)
        decisions_ok = True
        cache: dict[str, Optional[dict[str, Any]]] = {}

        def decision_getter(decision_id: Any) -> Optional[dict[str, Any]]:  # noqa: F811
            key = str(decision_id or "")
            if not key:
                return None
            if key not in cache:
                try:
                    found = decision_read.get_decision(key)
                except Exception:
                    found = None
                cache[key] = dict(found) if isinstance(found, Mapping) else None
            return cache[key]

    except _ReadersUnavailable as exc:
        if execution_source["state"] == "ok":
            execution_source.update({"state": "unavailable", "reason_code": exc.reason_code,
                                     "detail": exc.detail})
        else:
            execution_source["detail"] = ((execution_source.get("detail") or "")
                                          + "; " + exc.reason_code)[:160]

    # -- obligation result projection ----------------------------------------------------
    obligation_source: dict[str, Any] = {"state": "ok", "reason_code": None, "detail": None,
                                         "count": 0, "truncated": False, "total": 0}
    all_obligation_rows: list[dict[str, Any]] = []
    obligation_rows: list[dict[str, Any]] = []
    try:
        obligation_store = ldp.DayObligations(state_root, subject_id=subject_id)
        all_obligation_rows, truncated, total = _obligation_rows(
            ldp=ldp, store=obligation_store, day=day, now=now_iso,
            actions=actions, actions_ok=actions_ok,
            decision_getter=decision_getter, decisions_ok=decisions_ok,
            receipt_lister=receipt_lister)
        obligation_rows = all_obligation_rows[:MAX_OBLIGATION_ROWS]
        obligation_source.update({"count": len(obligation_rows),
                                  "truncated": bool(truncated), "total": int(total)})
    except Exception as exc:
        obligation_source.update({"state": "unavailable",
                                  "reason_code": _reason_code(exc, "OBLIGATIONS_READ_FAILED"),
                                  "detail": _detail(exc)})

    # -- today's activities and their association ---------------------------------------
    activities, activity_source = _read_activities(state_root, day)
    arrangements: list[dict[str, Any]] = []
    for row in plan_rows:
        target_bare = _first_artifact_ref(plan_items.get(row["plan_ref"]), ldp)
        candidate = _arrangement_candidate(
            ldp, {"kind": PLAN_KIND, "ref": row["plan_ref"], "item_ref": row["item_ref"],
                  "target_ref": f"artifact:{target_bare}" if target_bare else ""}, day)
        if candidate is not None:
            arrangements.append(candidate)
    for row in all_obligation_rows:
        candidate = _arrangement_candidate(
            ldp, {"kind": OBLIGATION_KIND, "ref": row.get("obligation_ref"),
                  "target_ref": row.get("target_ref")}, day)
        if candidate is not None:
            arrangements.append(candidate)
    unplanned, unclassified, associated = _associate_activities(
        activities, arrangements, ldp=ldp,
        actions=actions, actions_ok=actions_ok,
        decision_getter=decision_getter, decisions_ok=decisions_ok)

    # -- plan execution status (after activity association is known) --------------------
    for row in plan_rows:
        execution = _plan_execution(
            {"plan_ref": row["plan_ref"], "item_ref": row["item_ref"],
             "_item": plan_items.get(row["plan_ref"])},
            ldp=ldp, day=day, actions=actions, actions_ok=actions_ok,
            decision_getter=decision_getter, decisions_ok=decisions_ok,
            receipt_lister=receipt_lister)
        row["execution"] = execution
        if row["plan_ref"] in associated:
            execution["activity_refs"] = associated[row["plan_ref"]]
    for row in all_obligation_rows:
        if str(row.get("obligation_ref") or "") in associated:
            row["execution"]["activity_refs"] = associated[
                str(row.get("obligation_ref") or "")]

    sources = {"plan": plan_source, "obligations": obligation_source,
               "activities": activity_source, "execution": execution_source}
    data = {
        "schema": ARRANGEMENTS_SCHEMA,
        "day": day,
        "day_basis": "business_clock_plus_08",
        "timezone": phone_service.TODAY_TIMEZONE,
        "clock": dict(clock or {}),
        "plan": {"entries": plan_rows, "count": len(plan_rows),
                 "status": plan_source["state"], "reason_code": plan_source["reason_code"]},
        "obligations": {"entries": obligation_rows, "count": len(obligation_rows),
                        "status": obligation_source["state"],
                        "reason_code": obligation_source["reason_code"],
                        "truncated": bool(obligation_source.get("truncated")),
                        "total": int(obligation_source.get("total") or 0)},
        "unplanned_activities": unplanned,
        "unplanned_count": len(unplanned),
        "unclassified_activities": unclassified,
        "unclassified_count": len(unclassified),
        "activity_count": int(activity_source.get("count") or len(activities)),
        "activities_truncated": bool(activity_source.get("truncated")),
        "sources": sources,
        "read_only": True,
    }
    failure = next((source for source in (plan_source, obligation_source, activity_source)
                    if source.get("state") != "ok"), None)
    if failure is not None:
        return phone_service.unavailable(
            str(failure.get("reason_code") or "ARRANGEMENTS_UNAVAILABLE"),
            **data,
            note="来源读不到时只报不可用，不把它当成今天没有安排")
    return {"ok": True, "status": phone_service.STATUS_OK, "data": data}


def _futures_lookup(state_root: Path | str, subject_id: Optional[str]):
    """A read-only ``fitem:`` lookup; a missing store is an empty answer, an unreadable one raises."""

    try:
        module = _future_items()
        store = module.FutureItems(state_root, subject_id=subject_id)
    except Exception as exc:
        def _raise(_ref: Any) -> None:
            raise exc

        return _raise

    def lookup(item_ref: Any) -> Optional[dict[str, Any]]:
        row = store.get(item_ref)
        return row.to_dict() if row is not None else None

    return lookup


def _item_for(futures_lookup: Any, item_ref: Any) -> Optional[dict[str, Any]]:
    try:
        return futures_lookup(item_ref) if callable(futures_lookup) else None
    except Exception:
        return None


__all__ = [
    "ARRANGEMENTS_SCHEMA",
    "ASSOCIATION_ARRANGEMENT",
    "ASSOCIATION_UNKNOWN",
    "ASSOCIATION_UNPLANNED",
    "EXECUTION_DONE",
    "EXECUTION_UNKNOWN",
    "MAX_ACTIVITY_ROWS",
    "MAX_OBLIGATION_ROWS",
    "arrangements_envelope",
    "build_action_reader",
    "build_decision_reader",
    "read_today_activities",
]
