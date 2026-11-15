"""v0.7 live-c 少量聚合离线用例；只碰 scratch，不 prepare/run 正式根。

覆盖三件事：
1. B 的 30 分钟窗口与 1800 秒窗口同长 → 整段只有首窗口；C 的 5 分钟 → 多次 due（都用真实
   ``AutonomousLifeRunner.due``，不手算公式）。
2. PLACE 完成、前台空之后的下一次 due 仍会自然打开；候选源里 MOVE 只是被 World 真正提供的
   选项之一，不是被强制的选择。
3. QUIET 门禁合法保留（连续拦截有上界），额度/身份/缺失 UNKNOWN 都不猜。
"""
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components",
              REPO / "components/life/scripts", REPO / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import autonomous_life_assembly as assembly
import autonomous_life_cycle as alc
import autonomous_life_resident as resident
import autonomous_life_runner as alr
import demo_business_clock as dbc
import life_step_readiness as lsr
import v07_live_c as c07
import world_move_opportunity_source as wmos
from life_request_budget import (FAILSTOP_FILENAME, LifeRequestBudget,
                                 RequestBudgetError)

INTERVAL_ENV = "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"
T0 = datetime(2026, 11, 9, 2, 0, 0, tzinfo=timezone.utc)


def scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="v07-live-c-case-"))


def _runner(interval: int, root: Path) -> alr.AutonomousLifeRunner:
    return alr.AutonomousLifeRunner(
        SimpleNamespace(state_root=root, enabled=True), state_root=root,
        env={INTERVAL_ENV: str(int(interval))})


def _count_windows(interval: int, seconds: int = 1796) -> int:
    """真实 runner 的 due() 在一段真实秒数里到底打开了几次窗口。"""

    runner = _runner(interval, scratch())
    count = 0
    if runner.due(now=T0)[0]:
        runner._mark_checked(T0)
        count += 1
    for offset in range(1, seconds + 1):
        now = T0 + timedelta(seconds=offset)
        if runner.due(now=now)[0]:
            runner._mark_checked(now)
            count += 1
    return count


class V07LiveCTests(unittest.TestCase):
    def test_01_identity_is_closed_and_one_to_one(self):
        profile = c07.PROFILE
        self.assertEqual(profile["run_id"], "run:v07-live:20261109-c")
        self.assertEqual(profile["domain_id"], "v07:live:20261109-c")
        self.assertEqual((profile["real_seconds"], profile["logical_seconds"]), (1800, 1800))
        self.assertEqual(profile["rate"], 1.0)
        self.assertEqual(profile["logical_anchor"], "2026-11-09T11:00:00+09:00")
        self.assertEqual(profile["latest_start"], "2026-11-09T23:15:00+08:00")
        self.assertEqual(profile["request_deadline"], "2026-11-09T23:50:00+08:00")
        self.assertEqual(profile["historical_requests"], 566)
        self.assertEqual(profile["new_cap"], 20)
        self.assertEqual(profile["utc_day_cap"], 300)
        self.assertEqual(profile["cumulative_total"], 1000)
        self.assertEqual(profile["worst_case_cumulative"], 586)
        self.assertEqual(profile["remaining_after_worst_case"], 414)
        self.assertEqual(profile["check_interval_minutes"], 5)
        refused = c07.configure(Path("/tmp"), Path("/tmp/x/authority.db"))
        self.assertEqual(refused["reason_code"], "OFFLINE_PATH_REFUSED")

    def test_02_b_30min_equals_1800s_yields_only_the_first_window(self):
        # 真实 B：首窗口在 02:00:06.103，last_check_at 最后被事件改到 02:00:29.134；
        # 无论哪种，下一次自然窗口都越过 02:30:00 的 horizon。
        runner = _runner(30, scratch())
        self.assertEqual(runner.due(now=T0), (True, "NEVER_CHECKED"))
        runner._mark_checked(T0 + timedelta(seconds=6, milliseconds=103))
        self.assertEqual(runner.due(now=T0 + timedelta(seconds=1800)), (False, "NOT_DUE"))
        self.assertEqual(runner.due(now=T0 + timedelta(seconds=1806, milliseconds=103)),
                         (True, "INTERVAL_ELAPSED"))
        self.assertEqual(_count_windows(30), 1)

    def test_03_c_5min_opens_multiple_windows_inside_the_same_1800s(self):
        count = _count_windows(5)
        self.assertGreaterEqual(count, 5)
        self.assertLessEqual(count, 7)
        runner = _runner(5, scratch())
        runner._mark_checked(T0)
        self.assertEqual(runner.due(now=T0 + timedelta(seconds=299)), (False, "NOT_DUE"))
        self.assertEqual(runner.due(now=T0 + timedelta(seconds=300)), (True, "INTERVAL_ELAPSED"))
        self.assertEqual(runner.due(now=T0 + timedelta(seconds=600)), (True, "INTERVAL_ELAPSED"))

    def test_04_after_place_foreground_null_next_due_and_natural_candidates(self):
        # PLACE 在 t0 正常结束、前台空：下一次 due 不是因为"完成事件"强制触发，而是 5 分钟窗口。
        runner = _runner(5, scratch())
        runner._mark_checked(T0)
        self.assertEqual(runner.due(now=T0 + timedelta(seconds=299))[0], False)
        self.assertEqual(runner.due(now=T0 + timedelta(seconds=300))[0], True)

        occurred = "2026-11-09T02:00:41+00:00"
        observed = dict(location={"place_id": "home"}, world_revision=7,
                        world_id="world:offline", pose="standing", orientation="north")
        action_space = {"candidates": [
            {"verb": "MOVE", "params": {"destination_location_id": "street_1"}},
            {"verb": "MOVE", "params": {"destination_location_id": "street_2"}},
            {"verb": "CONSUME", "params": {"object_id": "object:tea"}},
        ]}
        moves = wmos.observed_move_opportunities(
            observed, action_space=action_space, occurred_at=occurred,
            observed_at=occurred)
        body = wmos.observed_body_action_opportunities(
            observed,
            available_actions={"actions": [
                {"verb": "POSE", "available": True,
                 "params": {"pose": ["seated", "lying"], "orientation": ["north"]}}]},
            occurred_at=occurred, observed_at=occurred)
        self.assertEqual(len(moves), 2)
        self.assertEqual([row["target_refs"] for row in moves],
                         [["place:street_1"], ["place:street_2"]])
        self.assertGreaterEqual(len(body), 1)
        opportunities = moves + body
        verbs = {row["opportunity_id"].split(":")[1] for row in opportunities}
        self.assertGreaterEqual(len(verbs), 2)  # MOVE 有同类之外的选项
        self.assertFalse(any("forced" in row or "required" in row for row in opportunities))
        # World 没提供任何 MOVE 目的地时，不凭空造 MOVE：MOVE 从不是被强制的。
        self.assertEqual(wmos.observed_move_opportunities(
            observed, action_space={"candidates": []}, occurred_at=occurred,
            observed_at=occurred), [])

    def test_05_quiet_gate_is_legal_and_bounded_not_forced_open(self):
        ledger = lsr.ReconsiderationLedger(scratch())
        parts = {"foreground_ref": None, "target_ref": None,
                 "source_revision": "r1",
                 "opportunity_set": ["wopp:move:home:r1:street_1"]}
        at = "2026-11-09T02:00:00+00:00"
        ledger.record_situation(parts=parts, outcome="NO_ACTION", at=at)
        later = "2026-11-09T02:01:00+00:00"
        first = alc.reconsideration_gate(ledger, parts=parts, at=later)
        self.assertTrue(first["blocked"])          # 合法 QUIET：同样的情形不再问
        self.assertFalse(first["forced_release"])
        self.assertEqual(first["reason_code"], "SAME_STATE_ALREADY_CONSIDERED")
        for _ in range(2):
            self.assertTrue(alc.reconsideration_gate(ledger, parts=parts, at=later)["blocked"])
        released = alc.reconsideration_gate(ledger, parts=parts, at=later)
        self.assertTrue(released["forced_release"])  # 连续拦截有上界，放行一轮
        self.assertFalse(released["blocked"])
        self.assertEqual(released["suppressed_reason_code"], "SAME_STATE_ALREADY_CONSIDERED")

    def test_06_caps_566_20_300_1000_bound_before_any_send(self):
        state = scratch() / "state"
        dbc.create_clock(state, run_id=c07.IDENTITY.run_id, rate=1.0,
                         logical_seconds=1800, domain_id=c07.IDENTITY.domain)
        dbc.ExperimentRequestCap(state, run_id=c07.IDENTITY.run_id,
                                 max_new_requests=20, total_limit=1000,
                                 historical_requests=566).create(
                                     baseline_run_window_calls=0)
        env = dict(c07.base._ENV, CHIYO_AUTONOMOUS_RUN_ID=c07.IDENTITY.run_id,
                   NYA02_BOUNDED_FAILSTOP="1",
                   NYA02_EXPERIMENT_CUMULATIVE_TOTAL="1000",
                   NYA02_FASTDAY_CLOCK=str(dbc.clock_path(state)))
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
                                     run_id=c07.IDENTITY.run_id,
                                     max_new_requests=435, total_limit=1000,
                                     historical_requests=566)
        utc = LifeRequestBudget(scratch(), utc_day_max=300, run_window_max=1000,
                                run_id="run:utc-independent")
        utc.ensure_window()
        for _ in range(300):
            utc.reserve()
        with self.assertRaises(RequestBudgetError) as refusal:
            utc.reserve()
        self.assertEqual(refusal.exception.reason_code, "REQUEST_BUDGET_UTC_DAY_LIMIT")

    def test_07_audit_unknown_is_not_zero_and_target_differs_from_child(self):
        root = scratch()
        state = root / "state"
        state.mkdir()
        unknown = c07.base._ledger_view_pure(state, c07.IDENTITY.run_id, {})
        self.assertEqual(unknown["state"], "absent")
        self.assertEqual(unknown["run_window_calls"], "UNKNOWN")
        zero = c07.base._ledger_view_pure(
            state, c07.IDENTITY.run_id, {"reserved_calls": 0, "used_high_water": 0})
        self.assertEqual((zero["run_window_calls"], zero["utc_day_calls"]), (0, 0))

        prepared = c07.audit(root)
        self.assertEqual(prepared["reason_code"], "NOT_PREPARED")
        self.assertFalse(prepared["model_called"])
        self.assertEqual(prepared["check_interval"]["target_minutes"], 5)
        self.assertEqual(prepared["check_interval"]["persisted_minutes"], "UNKNOWN")
        self.assertEqual(prepared["check_interval"]["runner_minutes"], "UNKNOWN")
        self.assertEqual(sorted(path.name for path in root.iterdir()), ["state"])

    def test_08_one_shot_and_precheck_stop(self):
        refused = c07.run(execute_approved=False)
        self.assertEqual(refused["reason_code"], "EXECUTE_FLAG_MISSING")
        identity = replace(c07.IDENTITY, root=scratch() / "one-shot",
                           authority=scratch() / "authority" / "authority.db")
        (identity.root / "run").mkdir(parents=True, exist_ok=True)
        self.assertTrue(c07._consume_once(identity))
        self.assertFalse(c07._consume_once(identity))
        self.assertTrue((identity.root / "run" / c07.CONSUME_FILENAME).exists())

        base_report = {"ok": True, "prepared": True,
                       "start": {"started": False, "consumed": False},
                       "failure": {"locked": False}, "window": {"present": False}}
        early = c07.IDENTITY.latest_start - timedelta(minutes=1)
        self.assertIsNone(c07.base._run_precheck(c07.IDENTITY, base_report, early))
        self.assertLessEqual(c07.IDENTITY.latest_start + timedelta(seconds=1800),
                             c07.IDENTITY.deadline)
        late = c07.IDENTITY.latest_start + timedelta(seconds=1)
        self.assertEqual(c07.base._run_precheck(c07.IDENTITY, base_report, late),
                         "LATEST_START_PASSED")


if __name__ == "__main__":
    unittest.main()
