#!/usr/bin/env python3
"""v0.5 手机只读时间线：她今天**有记录**的几件事，按来源合并成一条读面。

这不是新的真相层，也不是第二个日记/相册/钱包：

* 每条都来自既有 owner 自己的记录——Activity 读 ``canonical_life_state.json``（与
  ``/today`` 同一条日界），日计划读 ``life_day_plan``，World 动作读 owner 的
  execution ledger（同一份 ``world_execution_ledger`` 记录），日记/相册/钱包直接走已经
  存在的 phone facade。**没有任何新写入**，也不为「时间线好看」补事件。
* 计划条目只表示「她把这件事排进了今天」（``created_at`` 是排定时刻），不表示已执行。
  执行状态仍由 ``/today/arrangements`` 单独回答。
* 没有记录的时间段就是空白；不写「大概在休息」，也不按当前状态反推过去。

时域
----
Activity 与日计划写在实例自己的 Life 时钟上（业务 clock 或系统 clock，``/today`` 已经
按同一口径分日）；World 的 execution ledger 写 World owner 自己的时钟；日记/相册/钱包
写各自 owner 的真实记录时间。**不把不同时域硬排成全序**：每条带 ``time_basis``，顶层
``ordering`` 说明它们能否直接比较；不可比较时按域分组，域内再按时间。

隐私
----
聚合读面**不复制正文**：日记不带正文/摘要/派生标题，相册不带 caption/scene/
generation_prompt，钱包不带任何私有备注。operator 默认只有骨架（``skeleton_only``）；
主体工具拿到同样的骨架加原入口 ``detail_ref``，要正文仍走原来的 ``diary_read`` /
``album_image``，聚合入口不旁路它们各自的权限与扣留规则。图片字节永远只走原图片接口，
不塞进时间线。

本模块不启动服务、不调用模型、不写任何文件；读不到的来源是具名 ``unavailable``，
不是「她今天什么都没做」。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from . import own_day_read
from . import service as phone_service

#: The response schema of ``GET /api/phone/today/timeline``.
TIMELINE_SCHEMA = "phone.day_timeline.v1"

MAX_ENTRIES = 300
MAX_ACTIVITIES = 200
MAX_WORLD_EXECUTIONS = 200
MAX_DIARY = 200
MAX_ALBUM = 200
MAX_WALLET = 200

CLOCK_BUSINESS = "business_clock"
CLOCK_SYSTEM = "system_clock"
CLOCK_UNKNOWN = "unknown_time"

KIND_ACTIVITY = "activity"
KIND_PLAN = "plan"
KIND_WORLD_ACTION = "world_action"
KIND_WORLD_TRAVEL = "world_travel"
KIND_DIARY = "diary_write"
KIND_ALBUM = "album_saved"
KIND_WALLET = "wallet_transaction"

#: Detail the aggregate deliberately never copies.  Named on each row so a caller can see
#: *what* is missing without the aggregate becoming a second copy of the private record.
DIARY_DETAIL_NOT_IN_AGGREGATE = ("text", "summary", "derived_title")
ALBUM_DETAIL_NOT_IN_AGGREGATE = ("caption", "scene", "generation_prompt")
WALLET_DETAIL_NOT_IN_AGGREGATE = ("private_note",)


def _ldp():
    import life_day_plan as module

    return module


def _source_unavailable(reason_code: str, *, detail: Optional[str] = None,
                        limit: int = 0) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": "unavailable", "reason_code": str(reason_code),
                               "detail": detail, "count": 0, "truncated": False,
                               "limit": int(limit)}
    return payload


def _source_ok(*, count: int, truncated: bool = False, limit: int = 0,
               total: Optional[int] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": "ok", "reason_code": None, "detail": None,
                               "count": int(count), "truncated": bool(truncated),
                               "limit": int(limit)}
    if total is not None:
        payload["total"] = int(total)
    return payload


def _moment(value: Any):
    return phone_service.today_moment(value)


def _group_source(*, limit: int, rows: list[Any]) -> tuple[list[Any], dict[str, Any]]:
    total = len(rows)
    if total > limit:
        return rows[:limit], _source_ok(count=limit, truncated=True, limit=limit, total=total)
    return rows, _source_ok(count=total, truncated=False, limit=limit, total=total)


def _entry(
    *,
    entry_id: str,
    kind: str,
    occurred_at: Optional[str],
    time_basis: str,
    status: Optional[str],
    source: str,
    source_ref: str,
    title: Optional[str] = None,
    action: Optional[str] = None,
    place: Optional[dict[str, Any]] = None,
    detail_ref: Optional[str] = None,
    private_detail_withheld: bool = False,
    detail_not_in_aggregate: tuple[str, ...] = (),
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "entry_id": entry_id,
        "kind": kind,
        "occurred_at": occurred_at,
        "time_basis": time_basis if occurred_at else CLOCK_UNKNOWN,
        "status": status,
        "title": title,
        "action": action,
        "place": place,
        "source": source,
        "source_ref": source_ref,
        "detail_ref": detail_ref,
        "private_detail_withheld": bool(private_detail_withheld),
        "detail_not_in_aggregate": list(detail_not_in_aggregate),
    }
    for key, value in extra.items():
        if value is not None:
            row[key] = value
    return row


def _within_day(value: Any, day: str) -> tuple[bool, Optional[str]]:
    moment = _moment(value)
    if moment is None:
        return False, None
    iso = moment.isoformat(timespec="seconds")
    return moment.date().isoformat() == day, iso


# ---------------------------------------------------------------------------------------------
# Activity + 日计划（实例自己的 Life 时钟）
# ---------------------------------------------------------------------------------------------


def _activity_entries(state_root: Optional[Path], day: str, basis: str,
                      subject_view: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if state_root is None:
        return [], _source_unavailable("LIFE_STATE_UNBOUND", limit=MAX_ACTIVITIES)
    try:
        rows, source = own_day_read.read_today_activities(state_root, day)
    except Exception as exc:  # pragma: no cover - defensive; the reader reports named states
        return [], _source_unavailable("ACTIVITY_READ_FAILED",
                                       detail=type(exc).__name__, limit=MAX_ACTIVITIES)
    entries: list[dict[str, Any]] = []
    for row in rows[:MAX_ACTIVITIES]:
        rows_out = _entry(
            entry_id="activity:" + str(row.get("ref") or ""),
            kind=KIND_ACTIVITY,
            occurred_at=row.get("started_at"),
            time_basis=basis,
            status=row.get("status"),
            source="canonical_life_state",
            source_ref=str(row.get("ref") or ""),
            title=row.get("title") or None,
            action=row.get("kind") or None,
            detail_ref="/api/phone/today",
            ended_at=row.get("ended_at"),
            # 2026-10-28: FAILED carries its terminal reason; BLOCKED carries its own reason
            # code (no terminal).  Never guess a reason from the status alone.
            reason_code=row.get("status_reason_code") or row.get("terminal_reason_code"),
            # Read-only owner facts: terminal vs WAITING/PAUSED, the named reason, and
            # the single entry the owner data supports (``RESUME`` only when an OPEN
            # eligibility exists).  Absent facts stay absent, never guessed.
            recovery=row.get("recovery"),
            place=None,
            place_basis="NOT_IN_ACTIVITY_RECORD",
        )
        entries.append(rows_out)
    state = str(source.get("state") or "ok")
    if state != "ok":
        return [], _source_unavailable(str(source.get("reason_code") or "ACTIVITY_UNAVAILABLE"),
                                       detail=source.get("detail"), limit=MAX_ACTIVITIES)
    truncated = bool(source.get("truncated")) or len(rows) > MAX_ACTIVITIES
    return entries, _source_ok(count=len(entries), truncated=truncated, limit=MAX_ACTIVITIES,
                               total=int(source.get("count") or len(rows)))


def _plan_title(state_root: Optional[Path], subject_id: Optional[str],
                item_ref: Any) -> Optional[str]:
    if state_root is None:
        return None
    try:
        import life_future_items as lfi

        item = lfi.FutureItems(state_root, subject_id=subject_id).get(item_ref)
    except Exception:
        return None
    return item.title if item is not None else None


def _plan_entries(state_root: Optional[Path], subject_id: Optional[str], day: str,
                  basis: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if state_root is None:
        return [], _source_unavailable("LIFE_STATE_UNBOUND", limit=3)
    try:
        store = _ldp().DayPlan(state_root, subject_id=subject_id)
        rows = store.for_date(day)
    except Exception as exc:
        return [], _source_unavailable("DAY_PLAN_READ_FAILED",
                                       detail=type(exc).__name__, limit=3)
    entries: list[dict[str, Any]] = []
    for row in rows[:3]:
        item_ref = str(row.get("item_ref") or "")
        title = _plan_title(state_root, subject_id, item_ref)
        entries.append(_entry(
            entry_id="plan:" + str(row.get("plan_ref") or ""),
            kind=KIND_PLAN,
            occurred_at=phone_service.today_iso(row.get("created_at")),
            time_basis=basis,
            status=row.get("status"),
            source="life_day_plan",
            source_ref=str(row.get("plan_ref") or ""),
            title=title,
            action="PLAN_ESTABLISHED",
            detail_ref="/api/phone/today/arrangements",
            item_ref=item_ref or None,
            title_basis="future_item" if title else "ITEM_NOT_READABLE",
            note="计划只表示排进了今天；执行状态看 /api/phone/today/arrangements",
        ))
    return entries, _source_ok(count=len(entries), limit=3, total=len(rows))


# ---------------------------------------------------------------------------------------------
# World：动作与进行中的行程（World owner 自己的 execution ledger / travel 记录）
# ---------------------------------------------------------------------------------------------


def _place_names(snapshot_data: Mapping[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    location = snapshot_data.get("location")
    if isinstance(location, Mapping) and location.get("place_id"):
        names[str(location["place_id"])] = str(location.get("name") or location["place_id"])
    destinations = snapshot_data.get("destinations")
    places = destinations.get("places") if isinstance(destinations, Mapping) else None
    for item in places or ():
        if isinstance(item, Mapping) and item.get("location_id"):
            names[str(item["location_id"])] = str(item.get("name") or item["location_id"])
    return names


def _place(location_id: Any, names: Mapping[str, str],
           basis: str) -> Optional[dict[str, Any]]:
    if not location_id:
        return None
    key = str(location_id)
    return {"location_id": key, "name": names.get(key), "basis": basis}


def _ledger_records(world_home: Optional[Path]) -> list[dict[str, Any]]:
    import world_execution_ledger as ledger

    path = ledger.ledger_path(world_home) if world_home is not None else ledger.ledger_path()
    return ledger.ExecutionLedger(path).read_all()


def _execution_frames(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One frame per execution, in first-seen order; a terminal record wins over in-flight.

    The ledger appends one record per stage (prepared / committed / rejected ...), so replaying
    the same execution must not show up as several timeline events.
    """

    import world_execution_ledger as ledger

    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for record in records:
        execution_id = str(record.get("execution_id") or "")
        if not execution_id:
            continue
        if execution_id not in grouped:
            order.append(execution_id)
            grouped[execution_id] = []
        grouped[execution_id].append(record)
    terminal_statuses = ledger.TERMINAL_SUCCESS_STATUSES | ledger.TERMINAL_FAILURE_STATUSES
    frames: list[dict[str, Any]] = []
    for execution_id in order:
        mine = grouped[execution_id]
        terminal = [record for record in mine
                    if str(record.get("status") or "") in terminal_statuses]
        chosen = (terminal or mine)[-1]
        frames.append({
            "execution_id": execution_id,
            "action_type": chosen.get("action_type"),
            "status": chosen.get("status"),
            "result": chosen.get("result"),
            "reason_code": chosen.get("reason_code"),
            "timestamp": chosen.get("timestamp"),
            "reconciliation_required": bool(chosen.get("reconciliation_required")),
        })
    return frames


def _world_entries(world_home: Optional[Path], snapshot: Mapping[str, Any], day: str,
                   basis: str, subject_view: bool
                   ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), Mapping) else {}
    names = _place_names(data)
    travel = data.get("travel") if isinstance(data.get("travel"), Mapping) else {}
    travel_by_execution: dict[str, dict[str, Any]] = {}
    if travel.get("execution_id"):
        travel_by_execution[str(travel["execution_id"])] = dict(travel)

    entries: list[dict[str, Any]] = []
    try:
        frames = _execution_frames(_ledger_records(world_home))
    except Exception as exc:
        return [], _source_unavailable("WORLD_LEDGER_UNAVAILABLE",
                                       detail=type(exc).__name__,
                                       limit=MAX_WORLD_EXECUTIONS), names
    today_frames: list[dict[str, Any]] = []
    for frame in frames:
        inside, iso = _within_day(frame.get("timestamp"), day)
        if not inside:
            continue
        frame["_iso"] = iso
        today_frames.append(frame)
    # The travel merge uses the *full* today set, not only the visible slice: a trip whose
    # execution fell past the bound must not appear a second time as a travel-only entry.
    today_execution_ids = {str(frame["execution_id"]) for frame in today_frames}
    today_frames, source = _group_source(limit=MAX_WORLD_EXECUTIONS, rows=today_frames)
    for frame in today_frames:
        execution_id = str(frame["execution_id"])
        found = travel_by_execution.get(execution_id)
        place = None
        origin = None
        if found is not None:
            place = _place(found.get("destination_location_id"), names,
                           "world_travel_destination")
            origin = _place(found.get("origin_location_id"), names, "world_travel_origin")
        entries.append(_entry(
            entry_id="world_execution:" + execution_id,
            kind=KIND_WORLD_ACTION,
            occurred_at=frame.get("_iso"),
            time_basis=basis,
            status=frame.get("status") or frame.get("result"),
            source="world_execution_ledger",
            source_ref=execution_id,
            action=frame.get("action_type"),
            place=place,
            place_basis="world_travel_record" if place else "NOT_IN_RECORD",
            detail_ref="/api/phone/world",
            result=frame.get("result"),
            reason_code=frame.get("reason_code"),
            origin=origin,
            reconciliation_required=bool(frame.get("reconciliation_required")),
        ))
    # An active travel whose execution is not in the ledger still names a real owner record.
    # It is merged into the execution entry when the id matches, so one trip is not counted twice.
    if travel.get("execution_id") and str(travel["execution_id"]) not in today_execution_ids:
        inside, iso = _within_day(travel.get("started_at"), day)
        if inside:
            entries.append(_entry(
                entry_id="world_travel:" + str(travel["execution_id"]),
                kind=KIND_WORLD_TRAVEL,
                occurred_at=iso,
                time_basis=basis,
                status=travel.get("status"),
                source="world_travel_delivery",
                source_ref=str(travel["execution_id"]),
                action="TRAVEL",
                place=_place(travel.get("destination_location_id"), names,
                             "world_travel_destination"),
                place_basis="world_travel_record",
                detail_ref="/api/phone/world",
                origin=_place(travel.get("origin_location_id"), names, "world_travel_origin"),
                expected_arrival_at=travel.get("expected_arrival_at"),
            ))
    return entries, source, names


# ---------------------------------------------------------------------------------------------
# 日记 / 相册 / 钱包（各自 owner 的既有 facade；聚合只取骨架）
# ---------------------------------------------------------------------------------------------


def _diary_entries(service: Any, day: str, basis: str,
                   subject_view: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        answer = service.diary_list()
    except Exception as exc:
        return [], _source_unavailable("DIARY_READ_FAILED", detail=type(exc).__name__,
                                       limit=MAX_DIARY)
    if not isinstance(answer, Mapping) or not answer.get("ok"):
        return [], _source_unavailable(
            str((answer or {}).get("reason_code") or "DIARY_UNAVAILABLE"),
            detail=(answer or {}).get("detail"), limit=MAX_DIARY)
    rows: list[dict[str, Any]] = []
    for raw in answer.get("entries") or ():
        if not isinstance(raw, Mapping):
            continue
        inside, iso = _within_day(raw.get("created_at"), day)
        if not inside:
            continue
        item_id = str(raw.get("id") or "")
        rows.append(_entry(
            entry_id="diary:" + item_id,
            kind=KIND_DIARY,
            occurred_at=iso,
            time_basis=basis,
            status="written",
            source="private_daily_items",
            source_ref=item_id,
            chars=int(raw.get("chars") or 0),
            revisions=int(raw.get("revisions") or 0),
            detail_ref="/api/phone/diary/" + item_id,
            private_detail_withheld=not subject_view,
            detail_not_in_aggregate=DIARY_DETAIL_NOT_IN_AGGREGATE,
            title=None,
        ))
    rows, source = _group_source(limit=MAX_DIARY, rows=rows)
    return rows, source


def _album_entries(service: Any, day: str, basis: str,
                   subject_view: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        answer = service.album_list()
    except Exception as exc:
        return [], _source_unavailable("ALBUM_READ_FAILED", detail=type(exc).__name__,
                                       limit=MAX_ALBUM)
    if not isinstance(answer, Mapping) or not answer.get("ok"):
        return [], _source_unavailable(
            str((answer or {}).get("reason_code") or "ALBUM_UNAVAILABLE"),
            detail=(answer or {}).get("detail"), limit=MAX_ALBUM)
    rows: list[dict[str, Any]] = []
    for raw in answer.get("images") or ():
        if not isinstance(raw, Mapping):
            continue
        occurred = raw.get("created_at_iso")
        if occurred is None and raw.get("created_at") is not None:
            occurred = raw.get("created_at")
        inside, iso = _within_day(occurred, day)
        if not inside:
            continue
        artifact_id = str(raw.get("artifact_id") or "")
        if not artifact_id:
            continue
        rows.append(_entry(
            entry_id="album:" + artifact_id,
            kind=KIND_ALBUM,
            occurred_at=iso,
            time_basis=basis,
            status=str(raw.get("lifecycle") or "saved"),
            source="ManagedArtifact",
            source_ref=artifact_id,
            content_type=raw.get("content_type"),
            source_label=raw.get("source_label"),
            detail_ref="/api/phone/album/" + artifact_id,
            private_detail_withheld=not subject_view,
            detail_not_in_aggregate=ALBUM_DETAIL_NOT_IN_AGGREGATE,
        ))
    rows, source = _group_source(limit=MAX_ALBUM, rows=rows)
    return rows, source


def _wallet_entries(service: Any, day: str, basis: str) -> tuple[list[dict[str, Any]],
                                                                 dict[str, Any]]:
    try:
        answer = service.wallet()
    except Exception as exc:
        return [], _source_unavailable("WALLET_READ_FAILED", detail=type(exc).__name__,
                                       limit=MAX_WALLET)
    if not isinstance(answer, Mapping) or not answer.get("ok"):
        return [], _source_unavailable(
            str((answer or {}).get("reason_code") or "WALLET_UNAVAILABLE"),
            detail=(answer or {}).get("detail"), limit=MAX_WALLET)
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    for raw in data.get("transactions") or ():
        if not isinstance(raw, Mapping):
            continue
        inside, iso = _within_day(raw.get("occurred_at"), day)
        if not inside:
            continue
        transaction_id = str(raw.get("transaction_id") or "")
        if not transaction_id:
            continue
        rows.append(_entry(
            entry_id="wallet:" + transaction_id,
            kind=KIND_WALLET,
            occurred_at=iso,
            time_basis=basis,
            status="recorded",
            source="world_property_economy",
            source_ref=transaction_id,
            transaction_kind=raw.get("kind"),
            direction=raw.get("direction"),
            amount=raw.get("amount"),
            currency=data.get("currency"),
            catalog_item_id=raw.get("catalog_item_id"),
            detail_ref="/api/phone/wallet",
            detail_not_in_aggregate=WALLET_DETAIL_NOT_IN_AGGREGATE,
        ))
    rows, source = _group_source(limit=MAX_WALLET, rows=rows)
    return rows, source


# ---------------------------------------------------------------------------------------------
# body：只有读取时的当前快照，不是历史
# ---------------------------------------------------------------------------------------------


def _body_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), Mapping) else {}
    body = data.get("body") if isinstance(data.get("body"), Mapping) else {}
    if not body:
        return {"status": "unavailable",
                "reason_code": snapshot.get("reason_code") or "BODY_NOT_REPORTED",
                "time_basis": "current_state", "history": "NOT_RECORDED",
                "source": "world_phone_world.snapshot.body",
                "note": "没有可读的当前身体快照；不补历史。"}
    signals = []
    for item in body.get("signals") or ():
        if not isinstance(item, Mapping):
            continue
        signals.append({"signal_type": item.get("signal_type"),
                        "qualitative_band": item.get("qualitative_band")})
    return {
        "status": body.get("status"),
        "pose": body.get("pose"),
        "orientation": body.get("orientation"),
        "recovery": body.get("recovery"),
        "signals": signals,
        "bands_only": bool(body.get("bands_only", True)),
        "raw_values_exposed": bool(body.get("raw_values_exposed")),
        "time_basis": "current_state",
        "history": "NOT_RECORDED",
        "source": "world_phone_world.snapshot.body",
        "note": "这是读取时的当前快照，不是当天某个过去时刻的身体记录；不插进时间线。",
    }


# ---------------------------------------------------------------------------------------------
# public projection
# ---------------------------------------------------------------------------------------------


def timeline_envelope(*, service: Any, state_root: Optional[Path], subject_id: Optional[str],
                      clock: Mapping[str, Any], subject_view: bool,
                      world_home: Optional[Path],
                      world_clock_injected: bool) -> dict[str, Any]:
    """Build the one read-only envelope for ``GET /api/phone/today/timeline``."""

    moment = phone_service.today_moment((clock or {}).get("logical_at"))
    if moment is None:
        return phone_service.unavailable("BUSINESS_CLOCK_INVALID",
                                         clock=dict(clock or {}),
                                         note="业务时钟读不出当天；不猜日期")
    day = moment.date().isoformat()
    life_basis = (CLOCK_BUSINESS if str(clock.get("source")) == CLOCK_BUSINESS
                  else CLOCK_SYSTEM)
    world_basis = (CLOCK_BUSINESS if life_basis == CLOCK_BUSINESS and world_clock_injected
                   else CLOCK_SYSTEM)
    recorded_basis = CLOCK_SYSTEM

    activity_rows, activity_source = _activity_entries(state_root, day, life_basis,
                                                       subject_view)
    plan_rows, plan_source = _plan_entries(state_root, subject_id, day, life_basis)

    try:
        raw_world = service.world()
    except Exception as exc:
        raw_world = {"ok": False, "reason_code": "WORLD_READ_FAILED",
                     "detail": type(exc).__name__}
    world_snapshot: Mapping[str, Any] = (raw_world if isinstance(raw_world, Mapping)
                                         else {"ok": False, "reason_code": "WORLD_ANSWER_INVALID"})
    world_rows: list[dict[str, Any]] = []
    world_source: dict[str, Any]
    if world_snapshot.get("ok"):
        world_rows, world_source, _names = _world_entries(
            world_home, world_snapshot, day, world_basis, subject_view)
    else:
        world_source = _source_unavailable(
            str(world_snapshot.get("reason_code") or "WORLD_UNAVAILABLE"),
            detail=world_snapshot.get("detail"), limit=MAX_WORLD_EXECUTIONS)

    diary_rows, diary_source = _diary_entries(service, day, recorded_basis, subject_view)
    album_rows, album_source = _album_entries(service, day, recorded_basis, subject_view)
    wallet_rows, wallet_source = _wallet_entries(service, day, recorded_basis)

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in (activity_rows + plan_rows + world_rows + diary_rows + album_rows
                + wallet_rows):
        entry_id = str(row.get("entry_id") or "")
        if not entry_id or entry_id in seen:
            continue
        seen.add(entry_id)
        entries.append(row)

    domains: dict[str, list[str]] = {}
    for row in entries:
        domains.setdefault(str(row.get("time_basis") or CLOCK_UNKNOWN), []).append(
            str(row["entry_id"]))
    known_bases = sorted(basis for basis in domains if basis != CLOCK_UNKNOWN)
    comparable = len(known_bases) <= 1
    if comparable:
        entries.sort(key=lambda row: (row.get("occurred_at") is None,
                                      str(row.get("occurred_at") or ""),
                                      str(row.get("entry_id") or "")))
        sorted_by = "occurred_at"
    else:
        entries.sort(key=lambda row: (str(row.get("time_basis") or CLOCK_UNKNOWN),
                                      row.get("occurred_at") is None,
                                      str(row.get("occurred_at") or ""),
                                      str(row.get("entry_id") or "")))
        sorted_by = "time_basis_then_occurred_at"

    truncated = len(entries) > MAX_ENTRIES
    if truncated:
        entries = entries[:MAX_ENTRIES]

    sources = {
        "activities": activity_source,
        "plan": plan_source,
        "world": world_source,
        "diary": diary_source,
        "album": album_source,
        "wallet": wallet_source,
    }
    data = {
        "schema": TIMELINE_SCHEMA,
        "day": day,
        "day_basis": "business_clock_plus_08",
        "timezone": phone_service.TODAY_TIMEZONE,
        "clock": dict(clock or {}),
        "view": "subject" if subject_view else "operator",
        "skeleton_only": not subject_view,
        "entries": entries,
        "count": len(entries),
        "truncated": bool(truncated),
        "ordering": {
            "comparable": comparable,
            "sorted_by": sorted_by,
            "known_time_bases": known_bases,
            "domains": domains,
            "note": ("所有条目同属一个已知时钟域，时间可直接比较"
                     if comparable else
                     "条目不属同一时钟域；已按域分组，域内按时间，不跨域伪造全序"),
        },
        "body_snapshot": _body_snapshot(world_snapshot
                                        if isinstance(world_snapshot, Mapping) else {}),
        "limits": {"entries": MAX_ENTRIES, "activities": MAX_ACTIVITIES,
                   "world_executions": MAX_WORLD_EXECUTIONS, "diary": MAX_DIARY,
                   "album": MAX_ALBUM, "wallet": MAX_WALLET},
        "sources": sources,
        "read_only": True,
        "note": ("只合并已有记录；没有记录的时间段就是空白，不补、不推测。"
                 "计划只表示排进今天，不表示已执行。"),
    }
    if all(source.get("state") != "ok" for source in sources.values()):
        return phone_service.unavailable(
            "DAY_TIMELINE_SOURCES_UNAVAILABLE", **data,
            note="没有任何可读来源；这不等于她今天什么都没做")
    return {"ok": True, "status": phone_service.STATUS_OK, "data": data}


__all__ = [
    "ALBUM_DETAIL_NOT_IN_AGGREGATE",
    "CLOCK_BUSINESS",
    "CLOCK_SYSTEM",
    "CLOCK_UNKNOWN",
    "DIARY_DETAIL_NOT_IN_AGGREGATE",
    "KIND_ACTIVITY",
    "KIND_ALBUM",
    "KIND_DIARY",
    "KIND_PLAN",
    "KIND_WALLET",
    "KIND_WORLD_ACTION",
    "KIND_WORLD_TRAVEL",
    "MAX_ENTRIES",
    "TIMELINE_SCHEMA",
    "timeline_envelope",
]
