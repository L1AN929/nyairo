"""idle2 八个聚合离线 case；scratch 保留，不运行正式入口。"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components/life/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import idle2_bounded_run as idle
import autonomous_life_assembly as assembly
import autonomous_life_resident as resident
import demo_business_clock as dbc
import fast_day
import life_pause
from agency_model_client import AgencyModelClient, AgencyModelClientError
from agency_decision_ag1 import CognitiveDecisionRequest
from life_request_budget import LifeRequestBudget, BudgetedModelClient, RequestBudgetError, FAILSTOP_FILENAME

ENV = {"NYA02_MODEL_PROVIDER": "custom", "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
       "NYA02_MODEL_NAME": "scripted-offline", "NYA02_MODEL_API_KEY": "offline-placeholder",
       "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "300",
       "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "1000",
       "CHIYO_AUTONOMOUS_RUN_ID": idle.RUN_ID, "NYA02_BOUNDED_FAILSTOP": "1"}


def scratch():
    return Path(tempfile.mkdtemp(prefix="idle2-case-"))


def request():
    return CognitiveDecisionRequest(opportunity_id="dopp:offline", candidate_set_id="cset:offline",
        context_snapshot_id="dctx:offline", snapshot_fingerprint="sfp:offline", candidates=(),
        current_activity_summary=None, capacity_summary={}, legal_decision_enums=("NO_ACTION",),
        allowed_reason_codes=("NO_ACTION_CHOSEN",), observed_fact_keys=(), policy_version="offline")


class Transport:
    """只替 transport，正式 client/adapter/预算包装器仍执行。"""
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


class Idle2Tests(unittest.TestCase):
    def test_06_real_supervisor_short_chain_and_regular_stop(self):
        root = scratch()
        future = datetime.now(timezone.utc) + timedelta(days=1)
        prepared = idle.configure(root / "instance", root / "authority/db", deadline=future)
        self.assertTrue(prepared["ok"], prepared)
        state = Path(prepared["state_root"])
        nested = idle.read(root / "instance/one-day/instance.json")
        env = nested["environment"]
        self.assertEqual(env["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"], "300")
        self.assertEqual(env["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"], "1000")
        supervisor = fast_day.FastDaySupervisor(root / "instance", no_act=True,
                                               offline_scripted=True, poll_seconds=0.1)
        original = supervisor._sleep
        failed = []
        def sleep(seconds):
            original(seconds)
            # 真 resident 至少装配并写一次 journal 后，在模型边界注入脚本失败。
            if not failed and (state / "resident_journal.jsonl").exists():
                with mock.patch("profile_model_transport.OpenAICompatibleTransport", Transport):
                    budget, cog, _ = self.pair(state)
                    with self.assertRaises(Exception):
                        cog.invoke(request())
                failed.append(True)
        supervisor._sleep = sleep
        report = supervisor.start(duration_seconds=25)
        self.assertTrue(failed, report)
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["supervisor"]["stop_reason"], "BOUNDED_RUN_FAILSTOP")
        self.assertTrue(report["supervisor"]["child"]["exited"])
        self.assertTrue(report["supervisor"]["owners_stopped"])
        self.assertEqual(idle.read(dbc.clock_path(state))["state"], "STOPPED")
        self.assertTrue(life_pause.gate(state)["paused"])
        self.assertFalse(supervisor.start(duration_seconds=1)["ok"])

    def test_07_safe_projection_exact_join_and_readonly(self):
        root = scratch()
        secret = "PRIVATE_SENTINEL prompt completion reasoning title endpoint"
        def put(name, value):
            (root / name).write_text(json.dumps(value), encoding="utf-8")
        put("candidate_audit_journal.jsonl", {"candidate_id": "cand:idle", "source_ref": "wopp:stay:home:r1:DAZE", "title": secret})
        put("agency_decision_state.json", {"decisions_by_id": {
            "dec:one": {"candidate_refs": ["cand:idle"], "selected_candidate_ref": "cand:idle", "decision": "START", "short_rationale": secret},
            "dec:two": {"candidate_refs": [], "decision": "NO_ACTION", "reason_codes": ["NO_ACTION_CHOSEN"]}}, "candidate_count": 99,
            "accessibility_views_by_id": {"acc:one": {"candidate_entries": {"cand:idle": {}}}}})
        put("canonical_life_state.json", {"activities": {"act:one": {"decision_ref": "dec:one", "status": "ACTIVE", "title": secret}}})
        value = idle.safe_observation(root)
        self.assertEqual(value["idle"]["DAZE"]["offered_candidates"], 1)
        self.assertEqual(value["idle"]["DAZE"]["decision_selected"], 1)
        self.assertEqual(value["idle"]["DAZE"]["activity_completed"], 0)
        self.assertEqual(value["no_action_chosen"], 1)
        # ACTED/ACTIVE不能冒完成；只有同decision Activity及消耗World完成事实精确关联才计。
        put("canonical_life_state.json", {"activities": {"act:one": {"decision_ref": "dec:one", "status": "COMPLETED", "completion_evidence_ref": "pevid:one"}}})
        put("activity_progress_state.json", {"consumed_evidences_by_id": {"pevid:one": {
            "disposition": "CONSUMED", "bound_activity_id": "act:one", "evidence": {
                "activity_id": "act:one", "authority_domain": "world_authority", "observed_facts": {
                    "observation_source": "world_gateway.get_snapshot", "stay_affordance": "DAZE",
                    "stay_elapsed_seconds": 180, "stay_planned_seconds": 180,
                    "stay_target_ref": "stay:home:DAZE:180", "world_stay_ref": "world_fact:stay:home:DAZE:180"}}}}})
        completed = idle.safe_observation(root)
        self.assertEqual(completed["idle"]["DAZE"]["activity_completed"], 1)
        self.assertEqual(completed["idle"]["DAZE"]["world_completed"], 1)
        self.assertEqual(idle.safe_observation(root / "missing")["idle"]["DAZE"]["offered_candidates"], "UNKNOWN")
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(value))
        before = sorted(p.name for p in root.iterdir())
        idle.status(root)
        self.assertEqual(before, sorted(p.name for p in root.iterdir()))
        absent = root / "absent"
        self.assertFalse(idle.status(absent)["ok"])
        self.assertFalse(absent.exists())

    def test_08_profile_and_local_note_structure(self):
        self.assertEqual((idle.PROFILE["real_seconds"], idle.PROFILE["rate"], idle.PROFILE["logical_seconds"]), (7200, 12, 86400))
        self.assertEqual(idle.PROFILE["worst_case_cumulative"], 510)
        note = REPO / ".agents/notes/implemented/testing/2026-11-06-idle2-bounded-observation.md"
        text = note.read_text(encoding="utf-8")
        for heading in ("## Problem", "## Decision", "## Alternatives considered", "## Consequences"):
            self.assertIn(heading, text)
        for target in __import__("re").findall(r"\]\(([^)]+\.md)\)", text):
            self.assertTrue((note.parent / target).resolve().is_file(), target)

    def setUp(self):
        Transport.sends = 0
        Transport.failure = "TRANSPORT_TIMEOUT"

    def pair(self, root, *, enabled=True):
        env = dict(ENV)
        if not enabled:
            env.pop("NYA02_BOUNDED_FAILSTOP")
        budget = assembly.request_budget(env, root)
        guard = assembly.pause_request_admission(root, budget)
        cog = assembly.model_cognition(env, requests=budget, admission=guard, pause_guard=guard)
        content = BudgetedModelClient(AgencyModelClient(transport=Transport(pause_guard=guard)), budget)
        return budget, cog, content

    def test_01_configuration_and_reserve_domains(self):
        root = scratch()
        prepared = idle.configure(root / "instance", root / "authority/db", provision=False)
        self.assertTrue(prepared["ok"], prepared)
        state = Path(prepared["state_root"])
        env = dict(ENV, NYA02_EXPERIMENT_CUMULATIVE_TOTAL="1000",
                   NYA02_FASTDAY_CLOCK=str(dbc.clock_path(state)))
        budget = assembly.request_budget(env, state)
        obj = resident.AutonomousLifeResident.__new__(resident.AutonomousLifeResident)
        obj.env, obj.state_root = env, state
        capped = obj._experiment_capped_budget(budget)
        self.assertEqual((budget.utc_day_max, budget.run_window_max), (300, 1000))
        self.assertEqual(budget.view()["run_window_calls"], 0)
        self.assertEqual(prepared["domain_id"], idle.DOMAIN)
        for _ in range(20):
            capped.reserve("content")
        with self.assertRaises(RequestBudgetError):
            capped.reserve("decision")
        self.assertEqual(budget.view()["run_window_calls"], 20)
        other = LifeRequestBudget(scratch(), utc_day_max=300, run_window_max=1000, run_id="run:test")
        other.ensure_window()
        for _ in range(300):
            other.reserve()
        with self.assertRaises(RequestBudgetError) as refusal:
            other.reserve()
        self.assertEqual(refusal.exception.reason_code, "REQUEST_BUDGET_UTC_DAY_LIMIT")
        run_budget = LifeRequestBudget(scratch(), utc_day_max=None, run_window_max=1000, run_id="run:boundary")
        run_budget.ensure_window()
        data = run_budget._load_locked()
        data["windows"]["run:boundary"]["calls"] = 999
        run_budget._save_locked(data)
        run_budget.reserve()
        with self.assertRaises(RequestBudgetError) as refusal:
            run_budget.reserve()
        self.assertEqual(refusal.exception.reason_code, "REQUEST_BUDGET_RUN_WINDOW_LIMIT")

    def test_02_cognition_failure_blocks_both_surfaces(self):
        with mock.patch("profile_model_transport.OpenAICompatibleTransport", Transport):
            budget, cog, content = self.pair(scratch())
            with self.assertRaises(Exception):
                cog.invoke(request())
            self.assertTrue((budget.state_root / FAILSTOP_FILENAME).exists())
            for call in (lambda: cog.invoke(request()), lambda: content.complete(system_prompt="offline", user_prompt="offline")):
                with self.assertRaises(RequestBudgetError):
                    call()
            self.assertEqual((budget.view()["run_window_calls"], Transport.sends), (1, 1))

    def test_03_content_failure_and_reassembly(self):
        with mock.patch("profile_model_transport.OpenAICompatibleTransport", Transport):
            root = scratch()
            budget, cog, content = self.pair(root)
            Transport.failure = "PROVIDER_REQUEST_REJECTED"
            with self.assertRaises(AgencyModelClientError):
                content.complete(system_prompt="offline", user_prompt="offline")
            with self.assertRaises(RequestBudgetError):
                cog.invoke(request())
            with self.assertRaises(RequestBudgetError):
                assembly.request_budget(ENV, root)
            self.assertEqual((budget.view()["run_window_calls"], Transport.sends), (1, 1))

    def test_04_failure_categories_and_send_check(self):
        with mock.patch("profile_model_transport.OpenAICompatibleTransport", Transport):
            for token, expected in (("TRANSPORT_CONNECTION", "PROVIDER_OR_TRANSPORT_FAILURE"),
                                    ("PROVIDER_OUTPUT_TRUNCATED", "OUTPUT_TRUNCATED")):
                budget, cog, _ = self.pair(scratch())
                Transport.failure = token
                with self.assertRaises(Exception):
                    cog.invoke(request())
                self.assertEqual(idle.read(budget.state_root / FAILSTOP_FILENAME)["failure_category"], expected)
            root = scratch()
            budget, cog, _ = self.pair(root)
            Transport.failure = None
            original_complete = Transport.complete
            def late_complete(transport, **kwargs):
                time.sleep(0.02)
                return original_complete(transport, **kwargs)
            cog.inner.timeout_s = 0.001
            with mock.patch.object(Transport, "complete", late_complete):
                with self.assertRaises(Exception):
                    cog.invoke(request())
            self.assertEqual(idle.read(root / FAILSTOP_FILENAME)["failure_category"], "LATE_RESULT_DISCARDED")
            guard = assembly.pause_request_admission(root, budget)
            with self.assertRaises(RequestBudgetError):
                with guard.sending():
                    self.fail("must never send")

        # 真正式 HTTP transport 的拒绝与发送异常，仅替 connection；guard 仍禁真实网络。
        class Connection:
            failure = False
            def __init__(self, *args, **kwargs): pass
            def connect(self):
                if self.failure:
                    raise ConnectionError("PRIVATE_SENTINEL")
            def putrequest(self, *args): pass
            def putheader(self, *args): pass
            def endheaders(self): pass
            def send(self, body): pass
            def getresponse(self):
                return SimpleNamespace(status=403, read=lambda: b"PRIVATE_SENTINEL")
            def close(self): pass
        for bad_connection in (False, True):
            Connection.failure = bad_connection
            root = scratch()
            with mock.patch("http.client.HTTPSConnection", Connection):
                budget = assembly.request_budget(ENV, root)
                guard = assembly.pause_request_admission(root, budget)
                cog = assembly.model_cognition(ENV, requests=budget, admission=guard, pause_guard=guard)
                with self.assertRaises(Exception):
                    cog.invoke(request())
                self.assertEqual(budget.view()["run_window_calls"], 1)
                self.assertEqual(idle.read(root / FAILSTOP_FILENAME)["failure_category"], "PROVIDER_OR_TRANSPORT_FAILURE")
                with self.assertRaises(RequestBudgetError):
                    cog.invoke(request())

    def test_05_default_compatibility_and_persist_failure(self):
        with mock.patch("profile_model_transport.OpenAICompatibleTransport", Transport):
            budget, _, content = self.pair(scratch(), enabled=False)
            for _ in range(2):
                with self.assertRaises(AgencyModelClientError):
                    content.complete(system_prompt="offline", user_prompt="offline")
            self.assertFalse((budget.state_root / FAILSTOP_FILENAME).exists())
            self.assertEqual(budget.view()["run_window_calls"], 2)
            enabled = assembly.request_budget(ENV, scratch())
            with mock.patch("life_request_budget._write_atomically", side_effect=OSError("PRIVATE_SENTINEL")):
                with self.assertRaises(RequestBudgetError):
                    enabled.reserve()
            with self.assertRaises(RequestBudgetError):
                assembly.request_budget(ENV, enabled.state_root)
