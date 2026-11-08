#!/usr/bin/env python3
"""combined-natural-A 薄入口：复用 idle2 的正式 prepare/run/守卫，只加一次只读组合投影。

正式身份由本模块的不可变 ``BATCH_A`` 固定，CLI 不接受任意 root/run；prepare/run 只有
父级执行，offline-check 与 audit/status 永不触碰正式根。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components/life/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import idle2_bounded_run as idle

BATCH_A = idle.Identity(
    root=Path("/tmp/nyairo-combined-natural-20261106-a"),
    authority=Path("/tmp/nyairo-combined-natural-20261106-a-memory-authority/authority.db"),
    run_name="combined-natural-20261106-a",
    run_id="run:combined-natural-20261106-a",
    domain="fastday:combined-natural-20261106-a",
    latest_start=datetime.fromisoformat("2026-11-06T22:30:00+08:00"),
    deadline=datetime.fromisoformat("2026-11-07T00:50:00+08:00"),
    anchor=datetime.fromisoformat("2026-11-06T08:00:00+08:00"),
    historical_requests=522,
)

DECISIONS = frozenset({"START", "CONTINUE", "PAUSE", "ABANDON", "DEFER", "NO_ACTION"})
ROUTES = frozenset({"RULE_RESOLVED", "COGNITIVE_REQUIRED", "NO_DECISION"})
OUTCOMES = frozenset({
    "DISABLED", "BUDGET_EXHAUSTED", "NO_MATERIAL", "NO_ACTION", "DEFERRED",
    "SHARE_ACTED", "PAUSED", "ABANDONED", "DECISION_FAILED", "ADOPTION_REJECTED",
    "ACTED", "STEP_REFUSED", "STEP_UNKNOWN", "ALREADY_DONE", "STEP_UNSETTLED",
    "IN_TRANSIT", "PAUSED_BY_OPERATOR", "NO_STEP_AVAILABLE", "QUIET",
})
ACTIVITY_STATUSES = frozenset({
    "ACTIVE", "PAUSED", "WAITING", "BLOCKED", "COMPLETED", "FAILED", "ABANDONED",
    "CANCELLED", "EXPIRED",
})
ACTION_STATUSES = frozenset({
    "PROPOSED", "PREPARED", "SCHEDULED", "SUBMITTED", "ACKNOWLEDGED", "SUCCEEDED",
    "FAILED", "CANCELLED", "UNKNOWN",
})
STEP_REASONS = frozenset({
    "STEP_AVAILABLE", "NO_FOREGROUND_ACTIVITY", "FOREGROUND_NOT_ACTIVE",
    "CALLER_CANNOT_ACT", "NO_BOUND_TARGET", "WORK_NO_CHANGE_CONFIRMED",
    "WORLD_STEP_AVAILABLE", "WORLD_STEP_NOT_WIRED", "BODY_SINGLE_ACTION_NO_FURTHER_STEP",
    "WORLD_MOVE_NO_FURTHER_STEP", "ARTIFACT_STEP_AVAILABLE", "STAY_CHECKPOINT_STEP",
    "TARGET_NAMESPACE_UNKNOWN",
})
JOURNEY_STATUSES = frozenset({"pending", "in_transit", "completed", "blocked", "unknown"})
SEGMENT_STATUSES = frozenset({"pending", "started", "confirmed"})
TRAVEL_STATUSES = frozenset({"ARRIVED", "IN_PROGRESS"})
NO_CHANGE_REASONS = frozenset({"NO_CHANGE"})

_ID_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/_-]{0,159}$")
_GO_TO_KIND = "GO_TO_OBSERVED_PLACE"
JOIN_STATUSES = frozenset({
    "SAME_ACTIVITY", "NO_INTEGRATION_STORE", "NO_INTEGRATION_RECORD",
    "NO_ACTION_REFS", "NO_ACTION_STORE", "NO_SAME_ACTIVITY_ACTION",
    "NO_ACTION_IDEMPOTENCY_KEY", "NO_ACTIVE_JOURNEY",
    "JOURNEY_REQUEST_KEY_MISMATCH",
})


def _idref(value):
    return value if isinstance(value, str) and _ID_REF.fullmatch(value) else "UNKNOWN"


def _flag(value):
    return value if isinstance(value, bool) else "UNKNOWN"


def _count(value):
    return value if type(value) is int and value >= 0 else "UNKNOWN"


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "UNKNOWN"


# -- 只读世界/日志视图 -------------------------------------------------------


def _world_home(root):
    raw = idle.read(Path(root) / "one-day/instance.json").get("world_home")
    if not isinstance(raw, str) or not raw:
        return None
    home = Path(raw)
    try:
        home.resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return None
    return home


def _world_file(home, name):
    return None if home is None else home / "data" / "world" / name


def _active_journey(home):
    path = _world_file(home, "world_journey.json")
    if path is None or not path.is_file():
        return None
    active = idle.read(path).get("active")
    return active if isinstance(active, dict) else None


def _travel_index(home):
    """返回 {execution_id: travel}；文件缺失/不可读返回 None（不是空账）。"""
    path = _world_file(home, "world_travel_delivery.json")
    if path is None or not path.is_file():
        return None
    rows = idle.read(path).get("travels")
    if not isinstance(rows, list):
        return None
    return {row["execution_id"]: row for row in rows
            if isinstance(row, dict) and isinstance(row.get("execution_id"), str)}


def _integration_index(state):
    """AG2 已提交集成记录；文件缺失或形状不对返回 None（不是空账）。"""
    path = Path(state) / "life_integration_state.json"
    if not path.is_file():
        return None
    rows = idle.read(path).get("integrations_by_id")
    return rows if isinstance(rows, dict) else None


def _action_index(state):
    """AR-0 已提交 Action 记录；文件缺失或形状不对返回 None（不是空账）。"""
    path = Path(state) / "action_reality_state.json"
    if not path.is_file():
        return None
    rows = idle.read(path).get("actions")
    return rows if isinstance(rows, dict) else None


def _same_activity_action(actions, wakes, activity_ref):
    """只从 activity_ref 明确相同的 Action 记录取动作；不借最后一条无关 wake。"""
    if actions is not None:
        found = None
        for action_id, record in actions.items():
            if not isinstance(record, dict):
                continue
            if str(record.get("activity_ref") or "") != activity_ref:
                continue
            revision = record.get("revision")
            rank = revision if type(revision) is int else -1
            key = (rank, str(action_id))
            if found is None or key > found[0]:
                found = (key, action_id, record)
        if found is not None:
            return (_idref(found[1]),
                    _enum(found[2].get("status"), ACTION_STATUSES), "ACTION_STORE")
    same = None
    for wake in wakes or ():
        if not isinstance(wake, dict):
            continue
        if str(wake.get("activity_ref") or "") != activity_ref:
            continue
        if wake.get("action_id") is None and wake.get("action_status") is None:
            continue
        same = wake
    if same is None:
        return "UNKNOWN", "UNKNOWN", "UNKNOWN"
    return (_idref(same.get("action_id")),
            _enum(same.get("action_status"), ACTION_STATUSES), "SAME_ACTIVITY_WAKE")


def _same_activity_journey(home, activity_ref, actions, integrations):
    """Activity→AG2 action_refs→Action.idempotency_key→journey.request_key 的精确 join。

    只有整条链都指向同一个 activity_ref 与同一次请求时才返回 SAME_ACTIVITY；
    位置相同、别的 Activity/Action/旅程都不算。
    """
    if integrations is None:
        return "NO_INTEGRATION_STORE"
    records = [row for row in integrations.values()
               if isinstance(row, dict)
               and str(row.get("activity_ref") or "") == activity_ref]
    if not records:
        return "NO_INTEGRATION_RECORD"
    refs = []
    for record in sorted(records, key=lambda row: str(row.get("integration_id") or "")):
        for ref in record.get("action_refs") or []:
            if isinstance(ref, str) and ref and ref not in refs:
                refs.append(ref)
    if not refs:
        return "NO_ACTION_REFS"
    if actions is None:
        return "NO_ACTION_STORE"
    keys = []
    matched = False
    for ref in refs:
        record = actions.get(ref)
        if not isinstance(record, dict):
            continue
        if str(record.get("activity_ref") or "") != activity_ref:
            continue
        matched = True
        key = record.get("idempotency_key")
        if isinstance(key, str) and key and key not in keys:
            keys.append(key)
    if not matched:
        return "NO_SAME_ACTIVITY_ACTION"
    if not keys:
        return "NO_ACTION_IDEMPOTENCY_KEY"
    active = _active_journey(home)
    if active is None:
        return "NO_ACTIVE_JOURNEY"
    if str(active.get("request_key") or "") in keys:
        return "SAME_ACTIVITY"
    return "JOURNEY_REQUEST_KEY_MISMATCH"


def _location(home):
    path = _world_file(home, "world_state.json")
    if path is None or not path.is_file():
        return "UNKNOWN"
    location = idle.read(path).get("location")
    place = location.get("place_id") if isinstance(location, dict) else None
    return _idref(place)


def _wakes(state):
    """返回 wake 行列表；文件缺失返回 None，存在但为空返回 []。"""
    path = Path(state) / "autonomous_wakes.jsonl"
    if not path.is_file():
        return None
    return [row for row in idle.rows(path) if isinstance(row, dict)]


def _step_readiness(wakes, activity_ref):
    chosen = None
    if activity_ref == "UNKNOWN":
        return None
    for wake in wakes or ():
        extra = wake.get("extra")
        ready = extra.get("step_readiness") if isinstance(extra, dict) else None
        if isinstance(ready, dict) and str(ready.get("activity_ref") or "") == activity_ref:
            chosen = ready
    return chosen


def _go_to(state):
    """只取 revision 最高的一个 GO_TO；返回 (选中, GO_TO 总数)；文件缺失总数 None。"""
    activities = idle.read(Path(state) / "canonical_life_state.json").get("activities")
    if not isinstance(activities, dict):
        return None, None
    found = []
    for activity_id, activity in activities.items():
        if not isinstance(activity, dict) or activity.get("activity_kind") != _GO_TO_KIND:
            continue
        revision = activity.get("revision")
        rank = revision if type(revision) is int else -1
        found.append((rank, activity_id, activity))
    if not found:
        return None, 0
    best = max(found, key=lambda row: (row[0], str(row[1])))
    return (best[1], best[2]), len(found)


def _decision_record(agency, decision_ref):
    decisions = agency.get("decisions_by_id") if isinstance(agency, dict) else None
    if not isinstance(decisions, dict) or decision_ref == "UNKNOWN":
        return None
    record = decisions.get(decision_ref)
    return record if isinstance(record, dict) else None


# -- 组合投影 ----------------------------------------------------------------


def _decision_layer(agency, wakes, activity_ref):
    decisions = agency.get("decisions_by_id") if isinstance(agency, dict) else None
    decisions = decisions if isinstance(decisions, dict) else None
    if decisions is not None:
        model_quiet = rule_quiet = 0
        for record in decisions.values():
            if not isinstance(record, dict):
                continue
            codes = record.get("reason_codes")
            codes = codes if isinstance(codes, (list, tuple)) else ()
            if record.get("decision") == "NO_ACTION" and "NO_ACTION_CHOSEN" in codes:
                model_quiet += 1
            if (record.get("decision_route") == "RULE_RESOLVED"
                    and "NO_LEGAL_CANDIDATE" in codes):
                rule_quiet += 1
    else:
        model_quiet = rule_quiet = "UNKNOWN"
    if wakes is None:
        continue_quiet = "UNKNOWN"
    else:
        continue_quiet = sum(
            1 for wake in wakes
            if wake.get("decision") == "CONTINUE"
            and isinstance(wake.get("extra"), dict)
            and isinstance(wake["extra"].get("work_no_change"), dict))
    last = wakes[-1] if wakes else None
    extra = last.get("extra") if isinstance(last, dict) else None
    extra = extra if isinstance(extra, dict) else {}
    # 正式 runner 把本轮 Decision id 放在 extra.decision_id；顶层没有这个字段。
    decision_ref = _idref(extra.get("decision_id"))
    record = _decision_record(agency, decision_ref)
    present = ("UNKNOWN" if decisions is None or decision_ref == "UNKNOWN"
               else decision_ref in decisions)
    return {
        "records": {"model_no_action_chosen": model_quiet,
                    "rule_no_legal_candidate": rule_quiet,
                    "continue_with_work_no_change": continue_quiet},
        "last_wake": {
            "decision": _enum(last.get("decision"), DECISIONS)
                        if isinstance(last, dict) else "UNKNOWN",
            # route / selected ref 只认正式 Decision 表里的已提交记录，不从 wake 或结果猜。
            "decision_route": (_enum(record.get("decision_route"), ROUTES)
                               if record else "UNKNOWN"),
            "decision_id": decision_ref,
            "selected_candidate_ref": (_idref(record.get("selected_candidate_ref"))
                                       if record else "UNKNOWN"),
            "committed_record_present": present,
            "owner_version_created": _flag(extra.get("owner_version_created")),
        },
        "source": "COMMITTED_DECISION_AND_WAKE_FIELDS" if isinstance(last, dict) else "UNKNOWN",
    }


def _outcome_layer(wakes):
    last = wakes[-1] if wakes else None
    extra = last.get("extra") if isinstance(last, dict) else None
    extra = extra if isinstance(extra, dict) else {}
    raw = extra.get("work_no_change")
    if isinstance(raw, dict):
        work = {"reason_code": _enum(raw.get("reason_code"), NO_CHANGE_REASONS),
                "owner_called": _flag(raw.get("owner_called")),
                "version_created": _flag(raw.get("version_created"))}
    else:
        work = "UNKNOWN"
    return {
        "last_wake": (_enum(last.get("outcome"), OUTCOMES)
                      if isinstance(last, dict) else "UNKNOWN"),
        "work_no_change": work,
        "decision_not_inferred_from_outcome": True,
    }


def _go_to_layer(agency, wakes, state, home, world_journey):
    found, total = _go_to(state)
    activity_id, activity = found if found else ("UNKNOWN", None)
    row = {
        "activity_ref": "UNKNOWN", "activity_status": "UNKNOWN",
        "decision_ref": "UNKNOWN", "decision": "UNKNOWN",
        "decision_route": "UNKNOWN", "selected_candidate_ref": "UNKNOWN",
        "step_available": "UNKNOWN", "step_reason_code": "UNKNOWN",
        "step_target_ref": "UNKNOWN", "adopted": "UNKNOWN",
        "action_ref": "UNKNOWN", "action_status": "UNKNOWN",
        "action_source": "UNKNOWN",
        "activity_journey_join": "UNKNOWN",
        "arrived": "UNKNOWN", "activity_completed": "UNKNOWN",
        "completion_evidence_ref": "UNKNOWN", "journey_ref": "UNKNOWN",
        "journey_status": "UNKNOWN", "segment_execution_id": "UNKNOWN",
        "segment_status": "UNKNOWN", "travel_join": "UNKNOWN",
        "travel_status": "UNKNOWN", "world_location_ref": "UNKNOWN",
        "location_matches_journey_target": "UNKNOWN",
        "same_activity_continue_no_step": "UNKNOWN",
        "scope": "LATEST_REVISION_GO_TO_ONLY",
        "candidate_count": total if total is not None else "UNKNOWN",
    }
    if activity is None:
        return row
    status = _enum(activity.get("status"), ACTIVITY_STATUSES)
    decision_ref = _idref(activity.get("decision_ref"))
    record = _decision_record(agency, decision_ref)
    completion_ref = _idref(activity.get("completion_evidence_ref"))
    ready = _step_readiness(wakes, activity_id) if wakes is not None else None
    if wakes is None or _idref(activity_id) == "UNKNOWN":
        no_step = "UNKNOWN"
    else:
        no_step = sum(
            1 for wake in wakes
            if str(wake.get("activity_ref") or "") == activity_id
            and wake.get("decision") == "CONTINUE"
            and wake.get("outcome") == "NO_STEP_AVAILABLE")
    actions = _action_index(state)
    integrations = _integration_index(state)
    action_ref, action_status, action_source = _same_activity_action(
        actions, wakes, activity_id)
    join = _same_activity_journey(home, activity_id, actions, integrations)
    row.update({
        "activity_ref": _idref(activity_id),
        "activity_status": status,
        "decision_ref": decision_ref,
        "decision": _enum(record.get("decision"), DECISIONS) if record else "UNKNOWN",
        "decision_route": _enum(record.get("decision_route"), ROUTES) if record else "UNKNOWN",
        "selected_candidate_ref": (_idref(record.get("selected_candidate_ref"))
                                   if record else "UNKNOWN"),
        # 候选面“有步”与执行端“没步”分别报；step_readiness 只认绑定同一 activity 的正式字段。
        "step_available": _flag(ready.get("available")) if ready else "UNKNOWN",
        "step_reason_code": _enum(ready.get("reason_code"), STEP_REASONS) if ready else "UNKNOWN",
        "step_target_ref": _idref(ready.get("target_ref")) if ready else "UNKNOWN",
        "adopted": ("UNKNOWN" if decision_ref == "UNKNOWN" else True),
        "action_ref": action_ref,
        "action_status": action_status,
        "action_source": action_source,
        "activity_journey_join": _enum(join, JOIN_STATUSES),
        "activity_completed": ("UNKNOWN" if status == "UNKNOWN"
                               else status == "COMPLETED" and completion_ref != "UNKNOWN"),
        "completion_evidence_ref": completion_ref,
        "same_activity_continue_no_step": no_step,
    })
    # 只有整条 Activity→Action→journey.request_key 链对上，才把世界旅程字段贴给这个 Activity。
    # 否则 go_to 下的 journey/travel/arrived 保持 UNKNOWN；世界旅程本身另报 world_journey。
    if join == "SAME_ACTIVITY":
        row.update({key: world_journey[key] for key in (
            "journey_ref", "journey_status", "segment_execution_id", "segment_status",
            "travel_join", "travel_status", "arrived")})
    return row


def _journey_layer(home):
    active = _active_journey(home)
    layer = {"journey_ref": "UNKNOWN", "journey_status": "UNKNOWN",
             "segment_execution_id": "UNKNOWN", "segment_status": "UNKNOWN",
             "travel_join": "UNKNOWN", "travel_status": "UNKNOWN",
             "target_location_id": "UNKNOWN", "arrived": "UNKNOWN"}
    if active is None:
        return layer
    layer["journey_ref"] = _idref(active.get("journey_id"))
    layer["journey_status"] = _enum(active.get("status"), JOURNEY_STATUSES)
    layer["target_location_id"] = _idref(active.get("target_location_id"))
    segments = [row for row in (active.get("segments") or []) if isinstance(row, dict)]
    current = active.get("current_segment")
    segment = None
    if type(current) is int and 0 <= current < len(segments):
        segment = segments[current]
    elif segments:
        segment = segments[-1]
    if segment is not None:
        layer["segment_execution_id"] = _idref(segment.get("execution_id"))
        layer["segment_status"] = _enum(segment.get("status"), SEGMENT_STATUSES)
    travels = _travel_index(home)
    execution = layer["segment_execution_id"]
    if travels is None or execution == "UNKNOWN":
        layer["travel_join"] = "UNKNOWN"
    elif execution in travels:
        layer["travel_join"] = "SAME_EXECUTION"
        layer["travel_status"] = _enum(travels[execution].get("status"), TRAVEL_STATUSES)
    else:
        layer["travel_join"] = "NO_MATCHING_EXECUTION"
    # “到达”只认同一 execution_id 的 travel 记录确实 ARRIVED 且旅程 completed；
    # 位置相同、别的 execution 到达、Activity 有标题都不能升格成到达。
    if (layer["journey_status"] == "completed"
            and layer["travel_join"] == "SAME_EXECUTION"
            and layer["travel_status"] == "ARRIVED"):
        layer["arrived"] = True
    elif (layer["journey_status"] == "UNKNOWN" or layer["travel_join"] == "UNKNOWN"
          or layer["travel_status"] == "UNKNOWN"):
        layer["arrived"] = "UNKNOWN"
    else:
        layer["arrived"] = False
    return layer


def combined_observation(root, state=None, identity=BATCH_A):
    """只读组合：Decision 与 outcome 分层，GO_TO 的 step/action/travel/journey 用正式字段。"""
    root = Path(root)
    if state is None:
        _mode, state = idle._identity(root)
    state_path = Path(state) if state is not None else root / "missing" / "state"
    agency = idle.agency_view(state_path)
    idle_view = idle.safe_observation(state_path)
    wakes = _wakes(state_path)
    home = _world_home(root)
    journey = _journey_layer(home)
    go_to = _go_to_layer(agency, wakes, state_path, home, journey)
    location = _location(home)
    target = journey["target_location_id"]
    location_matches = (location == target
                        if location != "UNKNOWN" and target != "UNKNOWN" else "UNKNOWN")
    go_to["world_location_ref"] = location
    go_to["location_matches_journey_target"] = location_matches
    world_journey = dict(journey)
    world_journey["world_location_ref"] = location
    world_journey["location_matches_journey_target"] = location_matches
    decision = _decision_layer(agency, wakes, go_to["activity_ref"])
    integrations = _integration_index(state_path)
    actions = _action_index(state_path)
    return {
        "profile": identity.profile(),
        "evidence": {
            "decision_records": ("PRESENT" if isinstance(agency.get("decisions_by_id"), dict)
                                 and agency.get("decisions_by_id") else "UNKNOWN"),
            "wakes": "PRESENT" if wakes else "UNKNOWN",
            "go_to_activity": "PRESENT" if go_to["activity_ref"] != "UNKNOWN" else "UNKNOWN",
            "journey": "PRESENT" if journey["journey_ref"] != "UNKNOWN" else "UNKNOWN",
            "travel": "PRESENT" if journey["travel_join"] == "SAME_EXECUTION" else "UNKNOWN",
            "world_location": "PRESENT" if location != "UNKNOWN" else "UNKNOWN",
            "integration": "PRESENT" if integrations else "UNKNOWN",
            "actions": "PRESENT" if actions else "UNKNOWN",
        },
        "decision": decision,
        "outcome": _outcome_layer(wakes),
        "go_to": go_to,
        "world_journey": world_journey,
        "idle": idle_view["idle"],
        "offered_evidence": idle_view["offered_evidence"],
        "materialization_evidence": idle_view["materialization_evidence"],
        "activity_evidence": idle_view["activity_evidence"],
        "world_completion_evidence": idle_view["world_completion_evidence"],
        "reading": {"events": idle_view["reading"],
                    "owner_version_created": decision["last_wake"]["owner_version_created"]},
        "not_claimed": ["G1", "CAUSAL_EFFECT", "NATURAL_IDLE_GUARANTEE",
                        "DECISION_FROM_OUTCOME"],
    }


# -- 入口 --------------------------------------------------------------------


def _augment(view):
    if not isinstance(view, dict):
        return view
    _mode, state = idle._identity(BATCH_A.root)
    view["combined"] = combined_observation(BATCH_A.root, state)
    return view


def status():
    # audit/status 纯读：不建根、不建锁、不推进台账。
    return _augment(idle.status(BATCH_A.root, identity=BATCH_A))


def audit():
    return status()


def prepare():
    return _augment(idle.prepare(identity=BATCH_A))


def run():
    return _augment(idle.run(identity=BATCH_A))


def offline_check():
    import g1_real_adoption as g1
    import g2_explore_c as shared
    scratch = Path(tempfile.mkdtemp(prefix="combined-a-offline-"))
    guard = shared._install_child_guard(scratch / "guard")
    try:
        with g1._offline_network_guard(scratch):
            selfcheck = shared._guard_selfcheck()
            suite = unittest.defaultTestLoader.discover(
                str(REPO / "tests"), pattern="test_combined_observation_a.py")
            output = io.StringIO()
            result = unittest.TextTestRunner(stream=output, verbosity=0).run(suite)
            child = shared._child_guard_status(guard["status_dir"])
            ok = (result.testsRun > 0 and not result.skipped and result.wasSuccessful()
                  and all(selfcheck.values()) and child["all_refuse"])
            return {"ok": ok, "tests": result.testsRun, "failures": len(result.failures),
                    "errors": len(result.errors), "skipped": len(result.skipped),
                    "failed_cases": [test.id() for test, _ in result.failures + result.errors],
                    "error_locations": [[(Path(p).name, int(n)) for p, n in
                                         re.findall(r'File "([^"]+)", line ([0-9]+)', trace)]
                                        for _, trace in result.failures + result.errors],
                    "error_types": [trace.strip().splitlines()[-1].split(":", 1)[0]
                                    for _, trace in result.errors],
                    "refusal_codes": [re.findall(r"'reason(?:_code)?': '([A-Z_]+)'", trace)
                                      for _, trace in result.failures + result.errors],
                    "network_guard": selfcheck, "child_guard": child,
                    "scratch": str(scratch), "formal_root_touched": False,
                    "product_calls": 0, "profile": BATCH_A.profile()}
    finally:
        shared._restore_child_guard(guard)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",
                        choices=("offline-check", "prepare", "audit", "run", "status"))
    args = parser.parse_args(argv)
    try:
        result = {"offline-check": offline_check, "prepare": prepare, "audit": audit,
                  "run": run, "status": status}[args.command]()
    except Exception:
        result = {"ok": False, "reason_code": "COMBINED_A_ENTRY_FAILED"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
