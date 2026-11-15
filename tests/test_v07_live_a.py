"""v0.7 live-a 少量聚合离线用例；只碰 scratch，不 prepare/run 正式根。"""
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components",
              REPO / "components/life/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import autonomous_life_assembly as assembly
import autonomous_life_cycle as alc
import autonomous_life_resident as resident
import demo_business_clock as dbc
import life_pause  # noqa: F401 - the real module must import offline
import v07_live_a as v07
from agency_decision_ag1 import CognitiveDecisionRequest
from agency_model_client import AgencyModelClient, AgencyModelClientError
from life_request_budget import (BudgetedModelClient, FAILSTOP_FILENAME,
                                 LifeRequestBudget, RequestBudgetError)
from components.phone import demo, life as phone_life


def scratch():
    return Path(tempfile.mkdtemp(prefix="v07-live-a-case-"))


def request():
    return CognitiveDecisionRequest(
        opportunity_id="dopp:offline", candidate_set_id="cset:offline",
        context_snapshot_id="dctx:offline", snapshot_fingerprint="sfp:offline",
        candidates=(), current_activity_summary=None, capacity_summary={},
        legal_decision_enums=("NO_ACTION",), allowed_reason_codes=("NO_ACTION_CHOSEN",),
        observed_fact_keys=(), policy_version="offline")


class Transport:
    """只替 transport；正式 client/adapter/预算包装器仍真实执行。"""

    send_split_capable = True
    provider = "offline"
    model = "offline"
    sends = 0
    failure = "TRANSPORT_TIMEOUT"
    pause_guard = None

    def __init__(self, **kwargs):
        self.guard = kwargs.get("pause_guard")

    def complete(self, **kwargs):
        if self.guard is not None:
            with self.guard.sending("model"):
                type(self).sends += 1
        else:
            type(self).sends += 1
        if self.failure:
            raise AgencyModelClientError(self.failure, "PRIVATE_SENTINEL", status=400)
        return SimpleNamespace(text='{"decision":"NO_ACTION"}', latency_ms=1)


def _env(state):
    return dict(v07._ENV, CHIYO_AUTONOMOUS_RUN_ID=v07.IDENTITY.run_id,
                NYA02_BOUNDED_FAILSTOP="1",
                NYA02_EXPERIMENT_CUMULATIVE_TOTAL="1000",
                NYA02_FASTDAY_CLOCK=str(dbc.clock_path(state)))


def _state_with_cap(root):
    state = Path(root) / "state"
    dbc.create_clock(state, run_id=v07.IDENTITY.run_id, rate=1.0,
                     logical_seconds=1800, domain_id=v07.IDENTITY.domain)
    dbc.ExperimentRequestCap(state, run_id=v07.IDENTITY.run_id,
                             max_new_requests=20, total_limit=1000,
                             historical_requests=557).create(
        baseline_run_window_calls=0)
    return state


class V07LiveATests(unittest.TestCase):
    def setUp(self):
        Transport.sends = 0
        Transport.failure = "TRANSPORT_TIMEOUT"

    def test_01_identity_is_closed_and_one_to_one(self):
        profile = v07.PROFILE
        self.assertEqual(profile["run_id"], "run:v07-live:20261108-a")
        self.assertEqual(profile["domain_id"], "v07:live:20261108-a")
        self.assertEqual((profile["real_seconds"], profile["logical_seconds"]), (1800, 1800))
        self.assertEqual(profile["rate"], 1.0)
        self.assertEqual(profile["logical_anchor"], "2026-11-09T11:00:00+09:00")
        self.assertEqual(profile["latest_start"], "2026-11-09T00:15:00+08:00")
        self.assertEqual(profile["request_deadline"], "2026-11-09T00:50:00+08:00")
        self.assertEqual(profile["historical_requests"], 557)
        self.assertEqual(profile["new_cap"], 20)
        self.assertEqual(profile["utc_day_cap"], 300)
        self.assertEqual(profile["cumulative_total"], 1000)
        self.assertEqual(profile["worst_case_cumulative"], 577)
        self.assertEqual(profile["remaining_after_worst_case"], 423)
        refused = v07.configure(Path("/tmp"), Path("/tmp/x/authority.db"))
        self.assertEqual(refused["reason_code"], "OFFLINE_PATH_REFUSED")

    def test_02_binding_carries_canonical_room_rhythm_economy(self):
        root = scratch()
        instance = {
            "root": str(root), "world_home": str(root / "world"),
            "world_package": str(root / "package"), "world_id": "world:offline",
            "subject": "chiyo", "supply_socket": str(root / "supply.sock"),
            "artifact_grant_id": "grant:offline", "workspace_id": "ws:offline",
            "world_body_socket": str(root / "gateway.sock"),
            "reading_artifact_id": "", "work_artifact_id": "",
            "private_items_artifact_id": "",
            "features": {"CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
                         "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0"},
        }
        overrides = {"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"}
        overrides.update(demo._serve_feature_overrides(room_living=True))
        life_env = phone_life.map_life_environment(instance, {"environment": {}},
                                                   base_env={}, feature_overrides=overrides)
        self.assertEqual(life_env["WORLD_BODY_INTENT_MUTATION_GATE"], "canonical")
        self.assertEqual(life_env["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "1")
        self.assertEqual(life_env["CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"], "1")
        self.assertEqual(life_env["CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED"], "1")
        child = demo._world_child(root, instance, room_living=True,
                                  world_intent_gate="canonical")
        self.assertEqual(child.environment["WORLD_BODY_INTENT_MUTATION_GATE"], "canonical")
        self.assertEqual(child.environment["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "1")
        self.assertEqual(child.environment["CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"], "1")
        self.assertEqual(child.environment["CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED"], "1")

    def test_03_cap_20_history_and_utc_stay_independent(self):
        state = _state_with_cap(scratch())
        env = _env(state)
        budget = assembly.request_budget(env, state)
        holder = resident.AutonomousLifeResident.__new__(resident.AutonomousLifeResident)
        holder.env, holder.state_root = env, state
        capped = holder._experiment_capped_budget(budget)
        for _ in range(20):
            capped.reserve("content")
        with self.assertRaises(RequestBudgetError):
            capped.reserve("decision")
        self.assertEqual(budget.view()["run_window_calls"], 20)

        with self.assertRaises(dbc.BusinessClockError):
            dbc.ExperimentRequestCap(scratch() / "state",
                                     run_id=v07.IDENTITY.run_id,
                                     max_new_requests=444, total_limit=1000,
                                     historical_requests=557)
        utc = LifeRequestBudget(scratch(), utc_day_max=300, run_window_max=1000,
                                run_id="run:utc-independent")
        utc.ensure_window()
        for _ in range(300):
            utc.reserve()
        with self.assertRaises(RequestBudgetError) as refusal:
            utc.reserve()
        self.assertEqual(refusal.exception.reason_code, "REQUEST_BUDGET_UTC_DAY_LIMIT")

    def test_04_one_shot_and_run_precheck(self):
        root = scratch()
        identity = replace(v07.IDENTITY, root=root / "one-shot",
                           authority=root / "authority" / "authority.db")
        (identity.root / "run").mkdir(parents=True, exist_ok=True)
        self.assertTrue(v07._consume_once(identity))
        self.assertFalse(v07._consume_once(identity))
        self.assertTrue((identity.root / "run" / v07.CONSUME_FILENAME).exists())
        marker = identity.root / "run" / v07.CONSUME_FILENAME
        self.assertTrue(marker.exists())

        base = {"ok": True, "prepared": True,
                "start": {"started": False, "consumed": False},
                "failure": {"locked": False}, "window": {"present": False}}
        early = v07.IDENTITY.latest_start - timedelta(minutes=1)
        self.assertIsNone(v07._run_precheck(v07.IDENTITY, base, early))
        self.assertLessEqual(v07.IDENTITY.latest_start + timedelta(seconds=1800),
                             v07.IDENTITY.deadline)
        late = v07.IDENTITY.latest_start + timedelta(seconds=1)
        self.assertEqual(v07._run_precheck(v07.IDENTITY, base, late),
                         "LATEST_START_PASSED")
        tight = replace(v07.IDENTITY,
                        latest_start=v07.IDENTITY.deadline - timedelta(seconds=100))
        overrun = tight.deadline - timedelta(seconds=200)
        self.assertEqual(v07._run_precheck(tight, base, overrun),
                         "WINDOW_WOULD_OVERRUN")
        cases = [
            ({"start": {"started": True, "consumed": False}}, "ALREADY_STARTED"),
            ({"failure": {"locked": True}}, "FAILURE_LOCKED"),
            ({"start": {"started": False, "consumed": True}}, "ALREADY_CONSUMED"),
            ({"window": {"present": True}}, "WINDOW_ALREADY_OPEN"),
        ]
        for patch, expected in cases:
            report = dict(base, **patch)
            self.assertEqual(v07._run_precheck(v07.IDENTITY, report, early), expected)
        refused = v07.run(execute_approved=False)
        self.assertEqual(refused["reason_code"], "EXECUTE_FLAG_MISSING")

    def test_05_audit_missing_ledger_is_unknown_not_zero(self):
        root = scratch()
        state = root / "state"
        state.mkdir()
        unknown = v07._ledger_view_pure(state, v07.IDENTITY.run_id, {})
        self.assertEqual(unknown["state"], "absent")
        self.assertEqual(unknown["run_window_calls"], "UNKNOWN")
        zero = v07._ledger_view_pure(
            state, v07.IDENTITY.run_id,
            {"reserved_calls": 0, "used_high_water": 0})
        self.assertEqual((zero["run_window_calls"], zero["utc_day_calls"]), (0, 0))
        (state / "life_request_budget.json").write_text("{not-json", encoding="utf-8")
        corrupt = v07._ledger_view_pure(state, v07.IDENTITY.run_id, {})
        self.assertEqual(corrupt["state"], "corrupt")
        self.assertEqual(corrupt["run_window_calls"], "UNKNOWN")
        self.assertFalse((state / "life_request_budget.lock").exists())

        prepared = v07.audit(root)
        self.assertEqual(prepared["reason_code"], "NOT_PREPARED")
        self.assertFalse(prepared["model_called"])
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["state"])

    def test_06_unknown_send_is_not_resent_and_failure_is_charged(self):
        root = scratch()
        state = _state_with_cap(root)
        env = _env(state)
        with mock.patch("profile_model_transport.OpenAICompatibleTransport", Transport):
            budget = assembly.request_budget(env, state)
            content = BudgetedModelClient(
                AgencyModelClient(transport=Transport()), budget)
            with self.assertRaises(AgencyModelClientError):
                content.complete(system_prompt="offline", user_prompt="offline")
            self.assertEqual((Transport.sends, budget.view()["run_window_calls"]), (1, 1))
            self.assertTrue((state / FAILSTOP_FILENAME).exists())
            with self.assertRaises(RequestBudgetError):
                content.complete(system_prompt="offline", user_prompt="offline")
            self.assertEqual(Transport.sends, 1)

    def test_07_safe_observation_drops_private_text(self):
        state = scratch()
        sentinel = "PRIVATE_SENTINEL prompt completion reasoning short_rationale"
        (state / "resident_summary.json").write_text(json.dumps({
            "ticks": 3, "running": False, "stop_reason": "WALL_TIME_LIMIT",
            "covered_seconds": 12, "private": sentinel}), encoding="utf-8")
        (state / "autonomous_wakes.jsonl").write_text(json.dumps({
            "at": "2026-11-09T02:00:00Z", "kind": "due", "status": "NO_ACTION",
            "status_class": "chose", "decision": "NO_ACTION", "outcome": "NO_ACTION",
            "activity_ref": "activity:one", "action_id": "actv_" + "a" * 12,
            "action_status": "COMPLETED", "llm_calls": 1, "detail": sentinel}) + "\n",
            encoding="utf-8")
        (state / "canonical_life_state.json").write_text(json.dumps({
            "activities": {"act:one": {"kind": "READ", "status": "ACTIVE",
                                       "title": sentinel}}}), encoding="utf-8")
        view = v07._safe_observation(state)
        blob = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("PRIVATE_SENTINEL", blob)
        self.assertNotIn("short_rationale", blob)
        self.assertEqual(view["wakes"][0]["status"], "NO_ACTION")
        self.assertEqual(view["wakes"][0]["action_status"], "COMPLETED")
        self.assertEqual(view["activities"][0]["status"], "ACTIVE")
        self.assertEqual(view["not_claimed"][0], "Action成功不等于Activity完成")

    def test_09_step_refused_trips_bounded_failstop_only_for_the_experiment(self):
        """明确 owner 拒绝立即落实验 failstop，下一轮 reserve/send 前就拒绝。

        普通日常预算（failstop=False）在同一条接缝上什么都不写，所以不会给日常实例
        留下永久锁。IN_TRANSIT / nochange / NO_ACTION 没有 world_step_failure，不受影响。
        """

        state = _state_with_cap(scratch())
        bounded = LifeRequestBudget(state, run_id=v07.IDENTITY.run_id, failstop=True)
        cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
        cycle.budget = SimpleNamespace(requests=bounded)
        # A multi-segment trip / no-op is not a failure and must not trip the gate.
        cycle._note_bounded_first_fault({"outcome": "IN_TRANSIT"})
        cycle._note_bounded_first_fault({"outcome": "NO_ACTION"})
        self.assertFalse((state / FAILSTOP_FILENAME).exists())
        # The definite owner refusal this round writes the fixed marker immediately.
        cycle._note_bounded_first_fault({
            "world_step_failure": {"status": "FAILED", "code": "FAILURE", "verb": "PLACE"}})
        marker = json.loads((state / FAILSTOP_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(marker["failure_category"], "STEP_REFUSED")
        self.assertEqual(sorted(marker), ["at", "failure_category", "reason_code", "run_id"])
        with self.assertRaises(RequestBudgetError):
            bounded.check_failstop()
        with self.assertRaises(RequestBudgetError):
            bounded.reserve()

        daily_root = scratch()
        daily = LifeRequestBudget(daily_root, run_id="run:daily-no-failstop", failstop=False)
        cycle.budget = SimpleNamespace(requests=daily)
        cycle._note_bounded_first_fault({
            "world_step_failure": {"status": "FAILED", "code": "FAILURE", "verb": "PLACE"}})
        self.assertFalse((daily_root / FAILSTOP_FILENAME).exists())

    def test_08_run_projection_never_wraps_failure_as_success(self):
        result = {
            "ok": False,
            "supervisor": {
                "stop_reason": "BOUNDED_RUN_FAILSTOP", "pid": 101,
                "child": {"pid": 102, "returncode": 1, "exited": True},
                "owners_stopped": {"supply": {"pid": 103, "exit_code": 0},
                                   "world": {"pid": 104, "exit_code": -15}},
                "duration_limit_seconds": 1800.0,
            },
            "wall_budget": {"consumed_seconds": 12.5, "remaining_seconds": 7187.5},
        }
        post = {"ok": True,
                "failure": {"locked": True,
                            "category": "PROVIDER_OR_TRANSPORT_FAILURE"},
                "ledger": {"run_window_calls": 1},
                "observation": {"wakes": [], "activities": []}}
        out = v07._run_projection(result, post, v07.IDENTITY)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "STOPPED_WITH_FAILURE_OR_UNKNOWN")
        self.assertEqual(out["stop_reason"], "BOUNDED_RUN_FAILSTOP")
        self.assertEqual(out["coverage"], "NOT_COVERED")
        self.assertFalse(out["cap_reached"])
        self.assertEqual(out["exit_codes"], {"resident": 1, "supply": 0, "world": -15})
        self.assertEqual(out["logical"]["duration_limit_seconds"], 1800.0)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
