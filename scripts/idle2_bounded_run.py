#!/usr/bin/env python3
"""idle2 单批薄入口；只有父级运行 prepare/run，offline-check 永不触碰正式根。"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components/life/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import fast_day as fast
import demo_business_clock as dbc
from life_request_budget import FAILSTOP_FILENAME, LifeRequestBudget

ROOT = Path("/tmp/nyairo-fastday-20261106-idle2")
AUTHORITY = Path("/tmp/nyairo-fastday-20261106-idle2-memory-authority/authority.db")
RUN_NAME = "fastday-idle2-20261106"
RUN_ID = "run:" + RUN_NAME
DOMAIN = "fastday:" + RUN_NAME
LATEST_START = datetime.fromisoformat("2026-11-06T22:30:00+08:00")
DEADLINE = datetime.fromisoformat("2026-11-07T00:50:00+08:00")
ANCHOR = datetime.fromisoformat("2026-11-06T08:00:00+08:00")
IDLE = ("DAZE", "POTTER", "PEOPLE_WATCH")


@dataclass(frozen=True)
class Identity:
    """一个批次固定的身份与额度；显式不可变，默认值就是原 idle2 的全部取值。"""

    root: Path
    authority: Path
    run_name: str
    run_id: str
    domain: str
    latest_start: datetime
    deadline: datetime
    anchor: datetime
    utc_day_cap: int = 300
    cumulative_total: int = 1000
    historical_requests: int = 490
    new_cap: int = 20
    real_seconds: int = 7200
    logical_seconds: int = 86400
    rate: int = 12
    cleanup_seconds: int = 60
    max_cognition_calls_per_day: int = 300

    def profile(self):
        return {"run_id": self.run_id, "domain_id": self.domain,
                "utc_day_cap": self.utc_day_cap,
                "cumulative_total": self.cumulative_total,
                "historical_requests": self.historical_requests, "new_cap": self.new_cap,
                "worst_case_cumulative": self.historical_requests + self.new_cap,
                "real_seconds": self.real_seconds,
                "logical_seconds": self.logical_seconds, "rate": self.rate,
                "logical_anchor": self.anchor.isoformat(),
                "latest_start": self.latest_start.isoformat(),
                "request_deadline": self.deadline.isoformat(),
                "cleanup_seconds": self.cleanup_seconds}


IDLE2 = Identity(root=ROOT, authority=AUTHORITY, run_name=RUN_NAME, run_id=RUN_ID,
                 domain=DOMAIN, latest_start=LATEST_START, deadline=DEADLINE, anchor=ANCHOR)
PROFILE = IDLE2.profile()


def read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def rows(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def configure(root, authority, *, provision=True, deadline=None, identity=None):
    """仅正式 prepare 与离线 scratch 测试复用；不 source、不探测 provider。"""
    ident = identity or IDLE2
    if deadline is None:
        deadline = ident.deadline
    return fast.prepare(Path(root), run_id=ident.run_name, real_seconds=ident.real_seconds,
                        logical_seconds=ident.logical_seconds,
                        max_new_requests=ident.new_cap,
                        request_total_limit=ident.cumulative_total,
                        historical_requests=ident.historical_requests,
                        request_utc_day_max=ident.utc_day_cap,
                        max_cognition_calls_per_day=ident.max_cognition_calls_per_day,
                        bounded_failstop=True, request_deadline=deadline.isoformat(),
                        logical_anchor=ident.anchor, provision_instance=provision,
                        memory_authority=Path(authority))


def _identity(root):
    mode = read(fast.mode_path(root))
    state = Path(str(mode.get("state_root") or Path(root) / "state"))
    try:
        state.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return mode, None
    return mode, state


def _descriptor(source):
    if not isinstance(source, str):
        return None
    match = re.fullmatch(r"wopp:stay:[A-Za-z0-9_.-]+:r[0-9]+:(DAZE|POTTER|PEOPLE_WATCH)", source)
    return match.group(1) if match else None


def _safe_id(value):
    # 真实 Activity id 是 ``actv_<hex>``；旧式 ``act:`` 等安全形状一并保留，其余一律 UNKNOWN。
    return (value if isinstance(value, str) and
            re.fullmatch(r"(?:(?:cand|dec|act|pevid|artifactversion|read|world_fact|wrev):"
                         r"[A-Za-z0-9:_.-]{1,150}|actv_[A-Za-z0-9]{1,150})", value)
            else "UNKNOWN")


def agency_view(state):
    """只读拼接 AG1 快照与已提交 journal；供状态与安全投影共用。"""
    ag = read(state / "agency_decision_state.json")
    # allowed_candidates 来自正式 accessibility 的精确键，不用全部materialized候选冒充offered。
    # snapshot 可能滞后一轮；AG1 已提交 journal 的同名 map_delta 覆盖到当前只读视图。
    for entry in rows(state / "agency_decision_journal.jsonl"):
        maps = (entry.get("state_delta") or {}).get("map_updates") or {}
        for name in ("decisions_by_id", "context_snapshots_by_id", "accessibility_views_by_id"):
            if isinstance(maps.get(name), dict):
                ag.setdefault(name, {}).update(maps[name])
    return ag


def safe_observation(state):
    """只投影元字段。materialized≠offered；Decision≠Activity/World完成。"""
    candidate_rows = list(rows(state / "candidate_audit_journal.jsonl"))
    candidate_kind = {r.get("candidate_id"): _descriptor(r.get("source_ref"))
                      for r in candidate_rows if _descriptor(r.get("source_ref"))}
    ag = agency_view(state)
    decisions = ag.get("decisions_by_id", {})
    contexts = ag.get("context_snapshots_by_id", {})
    offered = set()
    accessibility = ag.get("accessibility_views_by_id", {})
    for view in accessibility.values():
        offered.update((view.get("candidate_entries") or {}).keys())
    counts = {kind: {"offered_candidates": sum(candidate_kind.get(c) == kind for c in offered),
                     "decision_selected": 0, "activity_completed": 0,
                     "world_completed": 0} for kind in IDLE}
    selected = {}
    no_action = 0
    for did, dec in decisions.items():
        if dec.get("decision") == "NO_ACTION" and "NO_ACTION_CHOSEN" in (dec.get("reason_codes") or []):
            no_action += 1
        kind = candidate_kind.get(dec.get("selected_candidate_ref"))
        if kind:
            selected[did] = kind
            counts[kind]["decision_selected"] += 1
    activity = read(state / "canonical_life_state.json")
    activities = activity.get("activities", {})
    progress = read(state / "activity_progress_state.json")
    world_complete = set()
    reading = []
    for consumed in progress.get("consumed_evidences_by_id", {}).values():
        ev = consumed.get("evidence") or {}
        facts = ev.get("observed_facts") or {}
        if (ev.get("authority_domain") == "world_authority"
                and facts.get("observation_source") == "world_gateway.get_snapshot"
                and facts.get("stay_affordance") in IDLE
                and isinstance(facts.get("stay_elapsed_seconds"), int)
                and isinstance(facts.get("stay_planned_seconds"), int)
                and facts["stay_elapsed_seconds"] >= facts["stay_planned_seconds"]
                and facts.get("world_stay_ref") == "world_fact:" + str(facts.get("stay_target_ref"))
                and consumed.get("disposition") == "CONSUMED"
                and consumed.get("bound_activity_id") == ev.get("activity_id")):
            world_complete.add(ev.get("activity_id"))
        if any(k in facts for k in ("page", "page_index", "material_revision", "version_id")):
            reading.append({"activity_id": _safe_id(ev.get("activity_id")),
                            **{key: facts[key] for key in ("page", "page_index", "total_pages")
                               if isinstance(facts.get(key), int)},
                            "owner_version": _safe_id(facts.get("version_id"))})
    for record in rows(state / "resident_journal.jsonl"):
        wakes = [record.get("due")] + list(record.get("events") or [])
        for wake in wakes:
            if not isinstance(wake, dict):
                continue
            extra = wake.get("extra") or {}
            report = extra.get("world_reading") or {}
            if report:
                states = {"APPLIED", "UNCHANGED", "NOT_CONFIGURED", "NO_READING_HERE",
                          "NOT_WRITTEN", "REFUSED", "UNKNOWN", "SKIPPED_EMPTY"}
                reading.append({"world_reading_state": report.get("state") if report.get("state") in states else "UNKNOWN",
                                "item_type": report.get("item_type") if report.get("item_type") in {"memo", "unclear_marker"} else "UNKNOWN",
                                "owner_version_number": report.get("version_number") if type(report.get("version_number")) is int else "UNKNOWN"})
    for aid, act in activities.items():
        kind = selected.get(act.get("decision_ref"))
        if not kind:
            continue
        if act.get("status") == "COMPLETED" and act.get("completion_evidence_ref"):
            counts[kind]["activity_completed"] += 1
        if aid in world_complete:
            counts[kind]["world_completed"] += 1
    for counter in counts.values():
        if not accessibility:
            counter["offered_candidates"] = "UNKNOWN"
        if not ag:
            counter["decision_selected"] = "UNKNOWN"
        if not activity:
            counter["activity_completed"] = "UNKNOWN"
        if not progress:
            counter["world_completed"] = "UNKNOWN"
    return {"idle": counts,
            "offered_evidence": "AG1_ALLOWED_ACCESSIBILITY_REFS" if accessibility else "UNKNOWN",
            "materialization_evidence": "PRESENT" if candidate_rows else "UNKNOWN",
            "activity_evidence": "PRESENT" if activity else "UNKNOWN",
            "world_completion_evidence": "PRESENT" if world_complete else "UNKNOWN",
            "no_action_chosen": no_action,
            "reading": reading or "UNKNOWN",
            "not_claimed": ["G1", "CAUSAL_EFFECT", "NATURAL_IDLE_GUARANTEE"]}


def status(root=None, identity=None):
    # 不调用 budget.view/_status_payload：它们可能创建 lock 或推进高水位。
    ident = identity or IDLE2
    root = Path(ident.root if root is None else root)
    mode, state = _identity(root)
    if not mode or state is None:
        return {"ok": False, "reason_code": "IDLE2_NOT_PREPARED", "profile": ident.profile()}
    nested = read(Path(root) / "one-day/instance.json")
    env = nested.get("environment") or {}
    lock_path = state / FAILSTOP_FILENAME
    lock = read(lock_path)
    ledger = read(state / "life_request_budget.json")
    exp = read(dbc.experiment_path(state))
    clock = read(dbc.clock_path(state))
    run = read(fast.run_path(state))
    caps = {"utc_day_cap": env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"),
            "run_window_cap": env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"),
            "cumulative_total": mode.get("request_total_limit"),
            "history": exp.get("historical_requests"), "new_cap": exp.get("max_new_requests"),
            "reserved": (ledger.get("windows", {}).get(ident.run_id) or {}).get("calls", 0),
            "utc_used": (ledger.get("days") or {}).get(ledger.get("active_day"), 0)}
    identity_ok = (mode.get("ledger_run_id") == ident.run_id
                   and mode.get("domain_id") == ident.domain
                   and clock.get("run_id") == ident.run_id
                   and clock.get("domain_id") == ident.domain
                   and mode.get("bounded_failstop") is True
                   and mode.get("request_deadline") == ident.deadline.isoformat()
                   and env.get("NYA02_BOUNDED_FAILSTOP") == "1"
                   and env.get("NYA02_REQUEST_DEADLINE") == ident.deadline.isoformat()
                   and env.get("NYA02_EXPERIMENT_CUMULATIVE_TOTAL") == str(ident.cumulative_total)
                   and caps["utc_day_cap"] == str(ident.utc_day_cap)
                   and caps["run_window_cap"] == str(ident.cumulative_total)
                   and caps["cumulative_total"] == ident.cumulative_total
                   and caps["history"] == ident.historical_requests
                   and caps["new_cap"] == ident.new_cap and mode.get("rate") == ident.rate
                   and mode.get("logical_seconds") == ident.logical_seconds)
    # 实际配置只输出数值，不把被篡改的配置字符串抄进结果。
    for key, value in list(caps.items()):
        if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) < 10:
            caps[key] = int(value)
        elif type(value) is not int or not 0 <= value <= 1000000:
            caps[key] = "UNKNOWN"
    categories = {"PROVIDER_OR_TRANSPORT_FAILURE", "OUTPUT_TRUNCATED", "LATE_RESULT_DISCARDED",
                  "STRUCTURE_OR_OTHER_FAILURE", "DEADLINE_REACHED"}
    return {"ok": identity_ok, "reason_code": "OK" if identity_ok else "IDENTITY_MISMATCH",
            "profile": ident.profile(), "actual_caps": caps,
            "failure_locked": lock_path.exists(),
            "failure_category": lock.get("failure_category") if lock.get("failure_category") in categories else "UNKNOWN",
            "started": bool(run), "stopped": bool(run.get("stopped_utc")),
            "observation": safe_observation(state)}


def prepare(identity=None):
    ident = identity or IDLE2
    if datetime.now(timezone.utc) > ident.latest_start:
        return {"ok": False, "reason_code": "LATEST_START_PASSED"}
    if ident.root.exists() or ident.authority.parent.exists():
        return {"ok": False, "reason_code": "FORMAL_ROOT_NOT_FRESH"}
    result = configure(ident.root, ident.authority, identity=ident)
    if not result.get("ok"):
        return {"ok": False, "reason_code": "PREPARE_REFUSED"}
    return status(identity=ident)


def run(identity=None):
    ident = identity or IDLE2
    if datetime.now(timezone.utc) > ident.latest_start:
        return {"ok": False, "reason_code": "LATEST_START_PASSED"}
    view = status(identity=ident)
    if not view.get("ok") or view.get("failure_locked") or view.get("started"):
        return {"ok": False, "reason_code": "IDLE2_RUN_REFUSED"}
    # 只改本次进程树；普通 resident 默认 gate 仍 off。ambient 沿旧 idle 未显式启用，不加种子。
    with mock.patch.dict(os.environ, {fast.WORLD_GATE_ENV: "canonical"}):
        supervisor = fast.FastDaySupervisor(ident.root, poll_seconds=1)
        supervisor.child_stop_timeout = 15
        supervisor.owner_stop_timeout = 15
        result = supervisor.start(duration_seconds=ident.real_seconds)
    view = status(identity=ident)
    view["ok"] = bool(result.get("ok")) and not view.get("failure_locked")
    view["run_result"] = "STOPPED" if view["ok"] else "STOPPED_WITH_FAILURE_OR_UNKNOWN"
    return view


def offline_check():
    import g1_real_adoption as g1
    import g2_explore_c as shared
    scratch = Path(tempfile.mkdtemp(prefix="idle2-offline-"))
    guard = shared._install_child_guard(scratch / "guard")
    try:
        with g1._offline_network_guard(scratch):
            selfcheck = shared._guard_selfcheck()
            suite = unittest.defaultTestLoader.discover(str(REPO / "tests"), pattern="test_idle2_bounded_run.py")
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
                    "product_calls": 0, "profile": PROFILE}
    finally:
        shared._restore_child_guard(guard)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("offline-check", "prepare", "audit", "run", "status"))
    args = parser.parse_args(argv)
    try:
        result = {"offline-check": offline_check, "prepare": prepare, "audit": status,
                  "run": run, "status": status}[args.command]()
    except Exception:
        result = {"ok": False, "reason_code": "IDLE2_ENTRY_FAILED"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
