#!/usr/bin/env python3
"""v2 自然采集离线控制的单元测试（0 产品调用、0 网络、0 真实根）。

覆盖的是「门在不在它该在的位置」，不是「模型会怎么选」：

* 同一状态跨多次观察继续等待，不早退、不再发请求；
* 观察 31 次上限与 1800 秒窗口各自单独生效；
* 请求 cap 8 是**发送前**的持久硬门（第 9 次到不了 transport），观察/重开/时钟都不刷新；
* 现成 reconsideration 门在同一状态依旧拒绝，换了 owner 版本才重新开放；
* H 与两条合法可执行门满足即冻结；只有 H、没有另一件可执行就报不可识别，而不是「未检出」；
* 观察器零写零模型：零写由受控 read port ＋ 有限指定文件前后 bytes/元数据比较证明，
  不在运行时扫全树算哈希；整个控制器不产 OPEN/RESUME/Activity；
* 循环头与 advance 前的单调钟边界：睡眠过冲、慢快照跨界、恰好 1800 秒、账本已满重开、
  迟到格点不追赶——这些情况都不再观察/推进；
* 供应商失败立即停、不重试；真实 prepare/run 入口明确关闭；最底层 HTTP 一律不可用。
"""
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"),
               str(REPO_ROOT / "components"), str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import history_choice_experiment as baseline  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

class _Keep:
    """用完**不删**的临时目录（本项目的 scratch 规矩：全部保留，不清理）。"""

    def __enter__(self) -> str:
        self.path = tempfile.mkdtemp(prefix="histchoice-v2-test-")
        return self.path

    def __exit__(self, *_exc) -> bool:
        return False


def _budget(directory: Path, *, cap: int = 8, run_id: str = "run:unit-v2"):
    from life_request_budget import LifeRequestBudget

    budget = LifeRequestBudget(directory, utc_day_max=300, run_window_max=cap, run_id=run_id)
    budget.ensure_window(allow_new=True)
    return budget


def _record(directory: Path) -> "v2.ExperimentRecord":
    record = v2.ExperimentRecord(directory)
    record.begin_batch(note="unit")
    return record


class _StubActivityRead:
    def __init__(self, rows=None, *, write_path: Path | None = None) -> None:
        self.rows = list(rows or [])
        self.write_path = write_path

    def list_open_activities(self):
        if self.write_path is not None:  # 故意写东西：观察器必须抓到自己不干净
            self.write_path.write_text("{}", encoding="utf-8")
        return [dict(row) for row in self.rows]


class _Advance:
    """假推进：只回一份白名单报告，不碰任何状态。"""

    def __init__(self, rows) -> None:
        self.rows = list(rows)
        self.calls = 0

    def __call__(self):
        index = min(self.calls, len(self.rows) - 1)
        self.calls += 1
        row = dict(self.rows[index])
        row["llm_calls"] = int(row.get("llm_calls") or 0)
        return row


def _quiet_rows(count: int):
    return [{"kind": "CHECK_WINDOW", "status": "QUIET", "trigger_ref": "evt:autonomous:check",
             "outcome": "QUIET", "llm_calls": 0} for _ in range(count)]


def _watched(directory: Path) -> dict:
    """有限指定业务文件的前后比较：bytes ＋ 大小 ＋ mtime（不递归、不算哈希）。"""

    out = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        stat = path.stat()
        out[path.name] = (path.read_bytes(), stat.st_size, stat.st_mtime_ns)
    return out


def _controller(state_root: Path, record_dir: Path, advance, *, plan=None, budget=None,
                h_detector=None, freeze=None, activity_read=None):
    plan = plan or v2.ExperimentPlan()
    budget = budget or _budget(state_root, cap=plan.request_cap)
    virtual = v2.VirtualTime()
    controller = v2.NaturalObservationController(
        plan=plan,
        observer=v2.ReadOnlyObserver(state_root=state_root, run_id=plan.run_id,
                                     activity_read=activity_read),
        advance=advance, budget=budget, record=_record(record_dir),
        h_detector=h_detector, freeze=freeze,
        clock=virtual.monotonic, sleep=virtual.sleep)
    return controller, virtual


# ---------------------------------------------------------------------------
# 计划与身份
# ---------------------------------------------------------------------------

class PlanTest(unittest.TestCase):
    def test_plan_is_its_own_identity_and_budget_is_only_a_ceiling(self):
        plan = v2.ExperimentPlan()
        self.assertNotEqual(plan.run_id, baseline.RUN_ID)
        self.assertNotEqual(plan.domain_id, baseline.DOMAIN_ID)
        self.assertEqual(plan.request_cap, 8)
        self.assertEqual(plan.window_seconds, 1800)
        self.assertEqual(plan.observation_interval_seconds, 60)
        self.assertEqual(plan.max_observations, 31)
        self.assertEqual(plan.future_candidate_total,
                         plan.request_cap + plan.future_shadow_requests
                         + plan.future_continuation_requests
                         + plan.future_diagnostic_requests)
        self.assertEqual(plan.future_diagnostic_requests, 0)
        self.assertEqual(plan.arm_request_cap, 2)
        self.assertIsNone(plan.real_root)

    def test_v1_constants_are_not_touched_by_import_or_use(self):
        plan = v2.ExperimentPlan()
        self.assertEqual(baseline.RUN_ID, "run:histchoice-20261029")
        self.assertEqual(baseline.TOTAL_REQUEST_CAP, 60)
        self.assertEqual(baseline.STAGE_CAPS,
                         {"acquisition": 8, "shadow": 40, "continuation": 8, "diagnostic": 4})
        self.assertEqual(baseline.ARM_REQUEST_CAP, 2)
        self.assertEqual(baseline.DEADLINE.isoformat(), "2026-10-30T01:00:00+08:00")
        # 用一次控制器不改变 v1 的任何参数。
        with _Keep() as tmp:
            base = Path(tmp)
            controller, _ = _controller(base / "state", base / "rec", _Advance(_quiet_rows(1)))
            controller.run()
        self.assertEqual(baseline.TOTAL_REQUEST_CAP, 60)
        self.assertNotEqual(plan.run_id, baseline.RUN_ID)

    def test_real_run_entry_is_closed(self):
        import contextlib
        import io

        refusal = v2.product_run()
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["reason_code"], v2.PRODUCT_RUN_NOT_AUTHORIZED)
        self.assertEqual(refusal["requests_sent"], 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(v2.main(["run"]), v2.EXIT_REFUSED)
            self.assertEqual(v2.main(["prepare"]), v2.EXIT_REFUSED)


# ---------------------------------------------------------------------------
# 观察器：零写、零模型
# ---------------------------------------------------------------------------

class ObserverTest(unittest.TestCase):
    def test_snapshot_is_read_only_and_reports_metadata_only(self):
        with _Keep() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            (state / "life_request_budget.json").write_text(json.dumps({
                "schema_version": "life.request_budget.v1", "kind": "life_request_budget",
                "active_day": "2026-10-29", "days": {"2026-10-29": 2},
                "windows": {"run:unit-v2": {"calls": 2, "opened_at": "x"}},
                "tokens": []}), encoding="utf-8")
            (state / "autonomous_wakes.jsonl").write_text(
                json.dumps({"kind": "CHECK_WINDOW", "status": "QUIET", "llm_calls": 0}) + "\n",
                encoding="utf-8")
            # 零写由**测试**证明：有限指定文件的前后 bytes/元数据比较，不是运行时全树扫描。
            before = _watched(state)
            observer = v2.ReadOnlyObserver(
                state_root=state, run_id="run:unit-v2",
                activity_read=_StubActivityRead([{"activity_id": "actv:1", "status": "PAUSED",
                                                  "revision": 4}]))
            snapshot = observer.snapshot(index=1)
            after = _watched(state)
            self.assertEqual(before, after, "observer changed the business state")
            self.assertEqual(snapshot["requests"]["run_window_calls"], 2)
            self.assertEqual(snapshot["activities"]["count"], 1)
            self.assertEqual(snapshot["wakes"]["last"]["status"], "QUIET")
            self.assertEqual(observer.observations, 1)
            # 不含任何正文/提示/推理字段
            text = json.dumps(snapshot, ensure_ascii=False)
            for forbidden in ("prompt", "completion", "reasoning", "rationale", "api_key"):
                self.assertNotIn(forbidden, text)

    def test_zero_write_is_a_test_level_check_not_a_runtime_tree_scan(self):
        """观察器不再在运行时扫全树算哈希；写没写由测试的前后比较发现。"""

        with _Keep() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            (state / "seed.json").write_text("{}", encoding="utf-8")
            observer = v2.ReadOnlyObserver(
                state_root=state, run_id="run:unit-v2",
                activity_read=_StubActivityRead([], write_path=state / "observer-was-here.json"))
            before = _watched(state)
            observer.snapshot(index=1)
            after = _watched(state)
            # 注入的写是**测试**看见的：测试自己就能抓到它，不依赖生产观察器自证。
            self.assertNotEqual(before, after)
            self.assertIn("observer-was-here.json", after)
            self.assertFalse(hasattr(observer, "fingerprint"))
            snapshot = observer.snapshot(index=2)
            self.assertNotIn("writes_detected", snapshot)
            self.assertNotIn("state_files_before", snapshot)


# ---------------------------------------------------------------------------
# 控制器：等待、上限、窗口
# ---------------------------------------------------------------------------

class ControllerWaitingTest(unittest.TestCase):
    def test_same_state_quiet_keeps_waiting_well_past_two_rounds(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan(max_observations=5)
            advance = _Advance(_quiet_rows(5))
            controller, virtual = _controller(base / "state", base / "rec", advance,
                                              plan=plan)
            summary = controller.run()
            self.assertEqual(summary["observations"], 5, "a quiet streak must not stop early")
            self.assertEqual(advance.calls, 5)
            self.assertEqual(summary["stop_reason"], v2.STOP_OBSERVATION_LIMIT)
            self.assertEqual(summary["result"], v2.RESULT_NO_H)
            self.assertEqual(summary["requests_used"], 0)
            self.assertTrue(summary["virtual_clock"])
            self.assertEqual(virtual.sleeps, [60.0, 60.0, 60.0, 60.0])
            for row in summary["timeline"]:
                self.assertEqual(row["llm_calls"], 0)

    def test_the_observation_limit_is_31(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan(observation_interval_seconds=30)
            controller, _ = _controller(base / "state", base / "rec",
                                        _Advance(_quiet_rows(40)), plan=plan)
            summary = controller.run()
            self.assertEqual(summary["observations"], 31)
            self.assertEqual(summary["stop_reason"], v2.STOP_OBSERVATION_LIMIT)
            self.assertLessEqual(summary["window_elapsed_s"], plan.window_seconds)

    def test_the_window_closes_at_1800_real_seconds(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan(observation_interval_seconds=120)
            controller, _ = _controller(base / "state", base / "rec",
                                        _Advance(_quiet_rows(40)), plan=plan)
            summary = controller.run()
            self.assertEqual(summary["stop_reason"], v2.STOP_WINDOW_ELAPSED)
            self.assertEqual(summary["window_elapsed_s"], 1800.0)
            self.assertEqual(summary["observations"], 16)
            self.assertEqual(summary["result"], v2.RESULT_NO_H)

    def test_nothing_opens_eligibility_or_resume_after_the_window(self):
        with _Keep() as tmp:
            base = Path(tmp)
            state = base / "state"
            state.mkdir()
            controller, _ = _controller(state, base / "rec", _Advance(_quiet_rows(3)),
                                        plan=v2.ExperimentPlan(max_observations=3))
            summary = controller.run()
            self.assertEqual(summary["result"], v2.RESULT_NO_H)
            eligibility_dir = state / "waiting_agenda" / "eligibility"
            self.assertFalse((eligibility_dir / "resume_eligibility_state.json").exists())
            self.assertEqual([p.name for p in state.iterdir()
                              if p.name.startswith("life_reconsideration")], [])
            self.assertEqual([p.name for p in state.iterdir()
                              if p.name.startswith("life_activity")], [])


class _SlowObserver(v2.ReadOnlyObserver):
    """快照本身花掉一段（虚拟）时间：用来验「观察耗时跨界后不推进」。"""

    def __init__(self, *, cost: float, clock_advance, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cost = float(cost)
        self._advance_clock = clock_advance

    def snapshot(self, *, index=None):
        self._advance_clock(self.cost)
        return super().snapshot(index=index)


class BoundaryRoundTest(unittest.TestCase):
    """循环头/推进前的单调钟边界：睡眠过冲、慢快照、恰好窗口、已满账本、迟到不追赶。"""

    def _run(self, base: Path, plan, advance, *, observer=None, sleep=None, budget=None):
        virtual = v2.VirtualTime()
        controller = v2.NaturalObservationController(
            plan=plan,
            observer=observer or v2.ReadOnlyObserver(state_root=base / "state",
                                                     run_id=plan.run_id),
            advance=advance,
            budget=budget or _budget(base / "state", cap=plan.request_cap),
            record=_record(base / "rec"),
            clock=virtual.monotonic, sleep=sleep or virtual.sleep)
        return controller.run(), virtual

    def test_sleep_overshoot_past_the_window_never_advances_again(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan(window_seconds=180, observation_interval_seconds=60)
            advance = _Advance(_quiet_rows(10))
            virtual = v2.VirtualTime()
            controller = v2.NaturalObservationController(
                plan=plan,
                observer=v2.ReadOnlyObserver(state_root=base / "state", run_id=plan.run_id),
                advance=advance, budget=_budget(base / "state", cap=plan.request_cap),
                record=_record(base / "rec"),
                clock=virtual.monotonic,
                sleep=lambda seconds: virtual.sleep(seconds + 90))
            summary = controller.run()
            self.assertEqual(summary["stop_reason"], v2.STOP_WINDOW_ELAPSED)
            self.assertEqual(advance.calls, 2, "an overslept window must not advance again")
            self.assertEqual(summary["observations"], 2)
            for row in summary["timeline"]:
                self.assertLessEqual(row["elapsed_s"], plan.window_seconds)

    def test_a_slow_snapshot_that_crosses_the_window_does_not_advance(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan(window_seconds=130, observation_interval_seconds=60,
                                     max_observations=10)
            advance = _Advance(_quiet_rows(10))
            virtual = v2.VirtualTime()
            observer = _SlowObserver(cost=100.0, clock_advance=virtual.sleep,
                                     state_root=base / "state", run_id=plan.run_id)
            controller = v2.NaturalObservationController(
                plan=plan, observer=observer, advance=advance,
                budget=_budget(base / "state", cap=plan.request_cap),
                record=_record(base / "rec"), clock=virtual.monotonic, sleep=virtual.sleep)
            summary = controller.run()
            self.assertEqual(advance.calls, 1, "a round that spent the window must not advance")
            self.assertEqual(summary["observations"], 2)
            self.assertEqual(summary["timeline"][-1]["status"], "NOT_ADVANCED")
            self.assertEqual(summary["timeline"][-1]["advance_skipped"], v2.STOP_WINDOW_ELAPSED)
            self.assertEqual(summary["stop_reason"], v2.STOP_WINDOW_ELAPSED)

    def test_exactly_the_window_allows_one_last_read_and_no_advance(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan()  # 1800 秒 / 60 秒 / 31 次
            advance = _Advance(_quiet_rows(40))
            summary, _ = self._run(base, plan, advance)
            self.assertEqual(summary["observations"], 31)
            self.assertEqual(advance.calls, 30, "the final round at exactly 1800 must not advance")
            last = summary["timeline"][-1]
            self.assertEqual(last["elapsed_s"], 1800.0)
            self.assertEqual(last["status"], "NOT_ADVANCED")
            self.assertEqual(last["advance_skipped"], v2.STOP_WINDOW_ELAPSED)
            self.assertEqual(summary["stop_reason"], v2.STOP_WINDOW_ELAPSED)

    def test_a_full_persisted_cap_on_reopen_never_observes_or_advances(self):
        from life_request_budget import LifeRequestBudget

        with _Keep() as tmp:
            base = Path(tmp)
            state = base / "state"
            full = _budget(state, cap=8)
            for _ in range(8):
                full.reserve("decision")
            # 「重开」：新对象、同一 root/run，计数不刷新也不退款。
            reopened = LifeRequestBudget(state, utc_day_max=300, run_window_max=8,
                                         run_id="run:unit-v2")
            plan = v2.ExperimentPlan()
            advance = _Advance(_quiet_rows(4))
            summary, virtual = self._run(base, plan, advance, budget=reopened)
            self.assertEqual(summary["stop_reason"], v2.STOP_REQUEST_CAP)
            self.assertEqual(advance.calls, 0, "a full ledger must not advance")
            self.assertEqual(summary["observations"], 0)
            self.assertEqual(summary["requests_used"], 8)
            self.assertEqual(virtual.sleeps, [])
            again = LifeRequestBudget(state, utc_day_max=300, run_window_max=8,
                                      run_id="run:unit-v2")
            self.assertEqual(again.ensure_window(allow_new=False)["calls"], 8, "no refund")

    def test_late_ticks_do_not_catch_up_into_dense_observations(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan(window_seconds=600, observation_interval_seconds=60,
                                     max_observations=20)
            advance = _Advance(_quiet_rows(20))
            virtual = v2.VirtualTime()
            controller = v2.NaturalObservationController(
                plan=plan,
                observer=v2.ReadOnlyObserver(state_root=base / "state", run_id=plan.run_id),
                advance=advance, budget=_budget(base / "state", cap=plan.request_cap),
                record=_record(base / "rec"), clock=virtual.monotonic,
                sleep=lambda seconds: virtual.sleep(seconds + 130))
            summary = controller.run()
            self.assertEqual(summary["stop_reason"], v2.STOP_WINDOW_ELAPSED)
            elapsed = [row["elapsed_s"] for row in summary["timeline"]]
            gaps = [later - earlier for earlier, later in zip(elapsed, elapsed[1:])]
            self.assertTrue(gaps, "the scenario must produce more than one observation")
            for gap in gaps:
                self.assertGreaterEqual(gap, plan.observation_interval_seconds,
                                        "missed grid points must be skipped, not replayed")


# ---------------------------------------------------------------------------
# 请求 cap 8：发送前持久硬门
# ---------------------------------------------------------------------------

class RequestCapTest(unittest.TestCase):
    def test_the_ninth_send_never_reaches_the_transport(self):
        from agency_decision_ag1 import CognitiveDecisionRequest
        from life_request_budget import BudgetedCognitionAdapter, RequestBudgetError

        with _Keep() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            budget = _budget(state, cap=8)
            transport = v2.CountingTransport(v2._no_action_text(), label="cap")
            inner = baseline.raw_cognition_adapter(v2.offline_model_env(), transport=transport)
            adapter = BudgetedCognitionAdapter(inner, budget, admission=None)

            def _request(index: int):
                # 每次都是新机会：客户端按机会身份缓存响应，复用同一个就不会真的再发。
                return CognitiveDecisionRequest(
                    opportunity_id="opp:cap:%02d" % index,
                    candidate_set_id="cset:cap:%02d" % index,
                    context_snapshot_id="ctx:cap",
                    snapshot_fingerprint="fp:cap:%02d" % index,
                    candidates=(), current_activity_summary=None,
                    capacity_summary={"overall_capacity_state": "AVAILABLE"},
                    legal_decision_enums=("NO_ACTION",),
                    allowed_reason_codes=("NO_ACTION_CHOSEN",),
                    observed_fact_keys=(), policy_version="ag0-v1")

            for index in range(8):
                adapter.invoke(_request(index))
            self.assertEqual(len(transport.calls), 8)
            with self.assertRaises(RequestBudgetError):
                adapter.invoke(_request(8))
            self.assertEqual(len(transport.calls), 8, "the 9th attempt reached the transport")
            # 门在发送前就给出具名 reason code，而不是等到发出去之后才发现。
            with self.assertRaises(baseline.BatchRefused) as caught:
                v2.PlanGateLedger(v2.ExperimentPlan()).assert_room("acquisition", budget.view())
            self.assertEqual(caught.exception.reason_code, v2.STOP_REQUEST_CAP)

    def test_the_count_survives_a_reopen_and_never_refunds(self):
        from life_request_budget import LifeRequestBudget

        with _Keep() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            budget = _budget(state, cap=8)
            for _ in range(5):
                budget.reserve("decision")
            budget.reserve("content")
            # 「重开」：同一 state root、同一 run 身份、换业务时钟，计数不刷新。
            reopened = LifeRequestBudget(state, utc_day_max=300, run_window_max=8,
                                         run_id="run:unit-v2")
            window = reopened.ensure_window(allow_new=False)
            self.assertEqual(window["calls"], 6)
            for _ in range(2):
                reopened.reserve("decision")
            verdict = reopened.check()
            self.assertFalse(verdict["allowed"])
            self.assertEqual(verdict["reason_code"], "REQUEST_BUDGET_RUN_WINDOW_LIMIT")
            # 失败也不退：再打开一次看到仍是 8。
            again = LifeRequestBudget(state, utc_day_max=300, run_window_max=8,
                                      run_id="run:unit-v2")
            self.assertEqual(again.ensure_window(allow_new=False)["calls"], 8)

    def test_observations_do_not_spend_the_request_budget(self):
        with _Keep() as tmp:
            base = Path(tmp)
            budget = _budget(base / "state", cap=8)
            controller, _ = _controller(base / "state", base / "rec", _Advance(_quiet_rows(6)),
                                        plan=v2.ExperimentPlan(max_observations=6),
                                        budget=budget)
            summary = controller.run()
            self.assertEqual(summary["requests_used"], 0)
            self.assertEqual(budget.run_window_max, 8, "the batch ceiling must be restored")


# ---------------------------------------------------------------------------
# H 判定器本身（不靠回调：规则就在这个类里）
# ---------------------------------------------------------------------------

class _StubCandidateRead:
    def __init__(self, targets) -> None:
        self.targets = list(targets)

    def get_candidate(self, candidate_id):
        if candidate_id != "cand:aaa":
            return None
        return {"candidate_id": candidate_id, "target_refs": list(self.targets)}


class _StubMemoryPort:
    def __init__(self, artifact_refs=()) -> None:
        self.artifact_refs = list(artifact_refs)
        self.reads = 0

    def recent(self, limit: int = 50):
        self.reads += 1

        class _Row:
            def __init__(self, ref):
                self.artifact_ref = ref
                self.experience_id = "exp:1"
                self.version_ref = "artifact-version:1"

        return [_Row(ref) for ref in self.artifact_refs]


class HistoryDetectorTest(unittest.TestCase):
    def _advance(self, *, created, selected="cand:aaa", version="artifactversion:v2"):
        return {"owner": {"owner_version_created": created, "owner_version_id": version,
                          "step_path": "AR0"},
                "selected_candidate_ref": selected}

    def test_a_confirmed_version_with_a_readable_experience_is_h(self):
        detector = v2.HistoryDetector(
            candidate_read=_StubCandidateRead(["artifact:artifact:aaa"]),
            memory_port=_StubMemoryPort(["artifact-version:1|artifact:artifact:aaa"]))
        found = detector.candidate(self._advance(created=True))
        self.assertIsNotNone(found)
        self.assertEqual(found["artifact_ref"], "artifact:artifact:aaa")
        self.assertEqual(found["experience_id"], "exp:1")

    def test_no_owner_version_is_not_h(self):
        detector = v2.HistoryDetector(
            candidate_read=_StubCandidateRead(["artifact:artifact:aaa"]),
            memory_port=_StubMemoryPort(["artifact:artifact:aaa"]))
        self.assertIsNone(detector.candidate(self._advance(created=False)))
        self.assertIsNone(detector.candidate(self._advance(created=None)))

    def test_a_checkpoint_receipt_without_an_artifact_target_is_not_h(self):
        # owner_version_created=True 也可能是 checkpoint/receipt；没有 artifact 目标就不算 H。
        detector = v2.HistoryDetector(candidate_read=_StubCandidateRead([]),
                                      memory_port=_StubMemoryPort(["artifact:artifact:aaa"]))
        self.assertIsNone(detector.candidate(self._advance(created=True, selected=None)))

    def test_an_unreadable_experience_is_not_h(self):
        detector = v2.HistoryDetector(
            candidate_read=_StubCandidateRead(["artifact:artifact:aaa"]),
            memory_port=_StubMemoryPort([]))
        self.assertIsNone(detector.candidate(self._advance(created=True)))

    def test_no_memory_port_is_not_h(self):
        detector = v2.HistoryDetector(
            candidate_read=_StubCandidateRead(["artifact:artifact:aaa"]), memory_port=None)
        self.assertIsNone(detector.candidate(self._advance(created=True)))


# ---------------------------------------------------------------------------
# 现成 reconsideration 门（真实 store）
# ---------------------------------------------------------------------------

class ReconsiderationGateTest(unittest.TestCase):
    def _parts(self, revision: str = "ver:1"):
        # 只用 SITUATION_KEYS 里真实存在的键：指纹只哈希这些 owner 事实。
        return {"foreground_activity_ref": "actv:1", "foreground_status": "PAUSED",
                "target_ref": "artifact:artifact:aaa", "target_revision": revision,
                "step_reason": "NO_STEP_AVAILABLE"}

    def test_the_real_gate_refuses_the_same_state_and_reopens_on_a_new_revision(self):
        from life_step_readiness import ReconsiderationLedger

        with _Keep() as tmp:
            store = ReconsiderationLedger(Path(tmp), ttl_seconds=3600)
            first = store.block_situation(parts=self._parts())
            self.assertFalse(first["blocked"])
            self.assertEqual(first["reason_code"], "STATE_NOT_CONSIDERED_YET")
            store.record_situation(parts=self._parts(), outcome="NO_ACTION",
                                   at="2026-10-29T12:00:00+00:00")
            same = store.block_situation(parts=self._parts(),
                                        at="2026-10-29T12:01:00+00:00")
            self.assertTrue(same["blocked"])
            self.assertEqual(same["reason_code"], "SAME_STATE_ALREADY_CONSIDERED")
            # 只有真的换了 owner 版本（不是时间流逝）才是新问题。
            newer = store.block_situation(parts=self._parts("ver:2"),
                                         at="2026-10-29T12:02:00+00:00")
            self.assertFalse(newer["blocked"])
            # 而且控制器没有任何主动 release 的入口。
            self.assertFalse(hasattr(v2.NaturalObservationController, "release"))


# ---------------------------------------------------------------------------
# H 与结构门
# ---------------------------------------------------------------------------

class _StubDetector:
    def __init__(self, h_ref: str) -> None:  # noqa: D401 - 测试替身
        self.h_ref = h_ref
        self.calls = 0

    def candidate(self, advance):
        self.calls += 1
        if not (advance.get("owner") or {}).get("owner_version_created"):
            return None
        return {"artifact_ref": self.h_ref, "owner_version_id": "artifactversion:v2",
                "experience_id": "exp:1", "selected_candidate_ref": "cand:aaa"}


class HGateTest(unittest.TestCase):
    def _started(self):
        return [{"kind": "CHECK_WINDOW", "status": "ACTED", "trigger_ref": "evt:autonomous:check",
                 "outcome": "ACTED", "llm_calls": 2,
                 "owner_version_created": True, "owner_version_id": "artifactversion:v2",
                 "selected_candidate_ref": "cand:aaa"}]

    def test_h_with_both_legal_gates_freezes_at_once(self):
        with _Keep() as tmp:
            base = Path(tmp)
            detector = _StubDetector("artifact:artifact:aaa")
            freezes = []

            def freeze(h_ref):
                freezes.append(h_ref)
                return {"ok": True, "reason_code": "", "frozen": {"observed_at": "t",
                                                                 "candidate_refs": ["c1", "c2"],
                                                                 "prior_experiences": [{}]},
                        "gate": {"ok": True, "candidate_count": 2, "h_executable": True,
                                 "other_work_executable": True, "no_action_legal": True}}

            controller, _ = _controller(base / "state", base / "rec",
                                        _Advance(self._started()), h_detector=detector,
                                        freeze=freeze)
            summary = controller.run()
            self.assertEqual(summary["result"], v2.RESULT_FROZEN)
            self.assertEqual(summary["stop_reason"], v2.STOP_H_AND_SCENARIO_FROZEN)
            self.assertEqual(summary["observations"], 1, "freeze must stop the batch at once")
            self.assertEqual(freezes, ["artifact:artifact:aaa"])
            self.assertTrue(summary["gate"]["no_action_legal"])

    def test_h_without_a_second_work_is_not_identifiable_not_undetected(self):
        with _Keep() as tmp:
            base = Path(tmp)
            detector = _StubDetector("artifact:artifact:aaa")
            freezes = []

            def freeze(h_ref):
                freezes.append(h_ref)
                return {"ok": False, "reason_code": baseline.REASON_SECOND_WORK_MISSING,
                        "frozen": None, "gate": {"ok": False}}

            plan = v2.ExperimentPlan(max_observations=4)
            controller, _ = _controller(base / "state", base / "rec",
                                        _Advance(self._started() * 4),
                                        plan=plan, h_detector=detector, freeze=freeze)
            summary = controller.run()
            self.assertEqual(summary["result"], v2.RESULT_NOT_IDENTIFIABLE)
            self.assertNotEqual(summary["result"], v2.RESULT_NO_H)
            self.assertEqual(summary["stop_reason"], v2.STOP_OBSERVATION_LIMIT)
            self.assertEqual(len(freezes), 4, "each round after H retries the structural gate")
            self.assertEqual(summary["h_ref"], "artifact:artifact:aaa")

    def test_no_h_reports_no_effective_history(self):
        with _Keep() as tmp:
            base = Path(tmp)
            plan = v2.ExperimentPlan(max_observations=2)
            controller, _ = _controller(base / "state", base / "rec",
                                        _Advance([{"status": "NO_ACTION", "llm_calls": 1,
                                                   "owner_version_created": False}]), plan=plan)
            summary = controller.run()
            self.assertEqual(summary["result"], v2.RESULT_NO_H)
            self.assertIsNone(summary["h_ref"])


# ---------------------------------------------------------------------------
# 失败与网络
# ---------------------------------------------------------------------------

class FailureTest(unittest.TestCase):
    def test_a_provider_failure_stops_the_batch_without_retrying(self):
        with _Keep() as tmp:
            base = Path(tmp)
            advance = _Advance([{"status": "BLOCKED", "outcome": "CALL_FAILURE",
                                 "llm_calls": 1, "failure_class": "transport_error"}])
            controller, _ = _controller(base / "state", base / "rec", advance)
            summary = controller.run()
            self.assertEqual(summary["stop_reason"], v2.STOP_PROVIDER_FAILURE)
            self.assertEqual(summary["result"], v2.RESULT_ABORTED)
            self.assertEqual(advance.calls, 1, "a failure must not be retried")

    def test_a_closed_send_latch_stops_the_batch_at_once(self):
        class _Latched:
            def __init__(self, reason):
                self.stop_reason = reason
                self.closed = bool(reason)

        for reason, expected in ((baseline.REASON_PROVIDER_FAILURE, v2.STOP_PROVIDER_FAILURE),
                                 (baseline.REASON_DEADLINE_PASSED, v2.STOP_DEADLINE_REACHED)):
            with _Keep() as tmp:
                base = Path(tmp)
                plan = v2.ExperimentPlan()
                virtual = v2.VirtualTime()
                controller = v2.NaturalObservationController(
                    plan=plan,
                    observer=v2.ReadOnlyObserver(state_root=base / "state",
                                                 run_id=plan.run_id),
                    advance=_Advance(_quiet_rows(3)),
                    budget=_budget(base / "state", cap=plan.request_cap),
                    record=_record(base / "rec"),
                    clock=virtual.monotonic, sleep=virtual.sleep,
                    latch=_Latched(reason))
                summary = controller.run()
                self.assertEqual(summary["stop_reason"], expected)
                self.assertEqual(summary["result"], v2.RESULT_ABORTED)
                self.assertEqual(summary["observations"], 1)

    def test_http_connect_is_forbidden_on_every_default_path(self):
        original = http.client.HTTPConnection.connect
        restore = v2._hard_disconnect(http.client.HTTPConnection, "connect")
        try:
            from profile_model_transport import OpenAICompatibleTransport

            transport = OpenAICompatibleTransport(base_url="https://offline.invalid/v1",
                                                 api_key="x", model="offline-model")
            with self.assertRaises(AssertionError):
                transport.complete(system_prompt="s", user_prompt="u")
        finally:
            restore()
        self.assertIs(http.client.HTTPConnection.connect, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
