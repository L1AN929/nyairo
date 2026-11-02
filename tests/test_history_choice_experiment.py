"""历史影响选择实验 harness 的最小回归（离线，0 产品调用）。

只覆盖本 harness 自己的机制：中性占位是等长等换行、请求身份只忽略历史文本、20 对顺序
表、阶段额度与总上限、根目录拒绝、run 的准入开关、McNemar 与分类。真实 owner 与正式
runtime 的那一段由 ``offline-check`` 自己跑（同样 0 调用，最底层 HTTP 被硬断开）。
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import history_choice_experiment as hce  # noqa: E402

# 批次的预登记截止已过真实墙钟。需要越过截止去测「第二次 run 被 started 挡住」这一条的
# 用例，只在本进程里 mock 模块自己的 ``_utc_now``（截止前 1 小时），不改常量、不改其它门。


class NeutralHistoryTest(unittest.TestCase):
    def test_same_length_and_same_newlines(self):
        original = "第一行经历。\n第二行更长一些的经历，带标点。\n\n第三行。"
        neutral = hce.neutral_history_text(original)
        self.assertEqual(len(neutral), len(original))
        self.assertEqual([i for i, ch in enumerate(neutral) if ch == "\n"],
                         [i for i, ch in enumerate(original) if ch == "\n"])
        self.assertNotIn("经历", neutral)
        # 纯函数：同样输入永远同样输出。
        self.assertEqual(neutral, hce.neutral_history_text(original))

    def test_neutralise_keeps_refs_and_ids(self):
        rows = [{"experience_id": "exp:1", "text": "她写了一段。", "artifact_ref": "artifact:a",
                 "version_ref": "ver:1"}]
        out = hce.neutralise_history(rows)
        self.assertEqual(out[0]["experience_id"], "exp:1")
        self.assertEqual(out[0]["artifact_ref"], "artifact:a")
        self.assertEqual(out[0]["version_ref"], "ver:1")
        self.assertEqual(len(out[0]["text"]), len("她写了一段。"))
        self.assertNotEqual(out[0]["text"], "她写了一段。")


class PromptIdentityTest(unittest.TestCase):
    def _payload(self, text: str) -> dict:
        return {"candidates": [{"candidate_id": "cand:1", "legal_decisions": ["START"]}],
                "situation_context": {"world": {"place": "room"}},
                "prior_experiences": [{"experience_id": "exp:1", "text": text,
                                       "artifact_ref": "artifact:h",
                                       "version_ref": "ver:1"}]}

    def test_identity_ignores_only_history_text(self):
        a = hce.prompt_identity(self._payload("真实经历正文"))
        b = hce.prompt_identity(self._payload("完全不同的占位文本"))
        self.assertEqual(a, b)

    def test_identity_changes_when_other_field_changes(self):
        left = self._payload("x")
        right = self._payload("x")
        right["situation_context"] = {"world": {"place": "street"}}
        self.assertNotEqual(hce.prompt_identity(left), hce.prompt_identity(right))

    def test_identity_changes_when_refs_change(self):
        left = self._payload("x")
        right = self._payload("x")
        right["prior_experiences"][0]["version_ref"] = "ver:2"
        self.assertNotEqual(hce.prompt_identity(left), hce.prompt_identity(right))


class PairOrderTest(unittest.TestCase):
    def test_order_is_the_preregistered_table(self):
        self.assertEqual(len(hce.PAIR_ORDER), hce.PAIR_COUNT)
        self.assertEqual(hce.PAIR_ORDER.count("AB"), 10)
        self.assertEqual(hce.PAIR_ORDER.count("BA"), 10)
        self.assertEqual(hce.PAIR_ORDER[0], "BA")

    def test_caps_sum_to_the_documented_regular_maximum(self):
        # 常规最多 52 = 采集 8 + 影子 40 + 承接实际 4（每臂 1 决策 + 1 内容）；
        # 承接阶段硬上限 8，剩 4 次不自动用来补失败。
        regular = hce.STAGE_CAPS["acquisition"] + hce.STAGE_CAPS["shadow"] + 4
        self.assertEqual(regular, 52)
        self.assertLessEqual(hce.STAGE_CAPS["acquisition"] + hce.STAGE_CAPS["shadow"]
                             + hce.STAGE_CAPS["continuation"]
                             + hce.STAGE_CAPS["diagnostic"], hce.TOTAL_REQUEST_CAP)


class ClassificationTest(unittest.TestCase):
    def test_classify_shadow(self):
        targets = {"cand:h": ["artifact:h"], "cand:o": ["artifact:other"]}
        self.assertEqual(hce.classify_shadow_result(
            decision="START", selected_candidate_ref="cand:h",
            candidate_targets=targets, h_refs=["artifact:h"]), hce.CLASS_H)
        self.assertEqual(hce.classify_shadow_result(
            decision="START", selected_candidate_ref="cand:o",
            candidate_targets=targets, h_refs=["artifact:h"]), hce.CLASS_OTHER)
        self.assertEqual(hce.classify_shadow_result(
            decision="NO_ACTION", selected_candidate_ref=None,
            candidate_targets=targets, h_refs=["artifact:h"]), hce.CLASS_NO_ACTION)
        self.assertEqual(hce.classify_shadow_result(
            decision=None, selected_candidate_ref=None,
            candidate_targets=targets, h_refs=["artifact:h"]), hce.CLASS_INVALID)

    def test_failure_class_mapping(self):
        self.assertEqual(hce.classify_failure("DECISION_ATTEMPT_FAILED:MODEL_TIMEOUT"),
                         hce.CLASS_FAILURE)
        self.assertEqual(hce.classify_failure("DECISION_ATTEMPT_FAILED:INVALID_JSON"),
                         hce.CLASS_INVALID)

    def test_mcnemar_known_values(self):
        self.assertEqual(hce.mcnemar_exact_two_sided(0, 0), 1.0)
        # 6 比 0 的双侧精确 p = 2*(1/2^6) = 0.03125
        self.assertAlmostEqual(hce.mcnemar_exact_two_sided(6, 0), 0.03125)
        # 5 比 0 → 2/32 = 0.0625（达不到 0.05）
        self.assertAlmostEqual(hce.mcnemar_exact_two_sided(5, 0), 0.0625)
        self.assertAlmostEqual(hce.mcnemar_exact_two_sided(3, 3), 1.0)
        # 对称
        self.assertAlmostEqual(hce.mcnemar_exact_two_sided(7, 2),
                               hce.mcnemar_exact_two_sided(2, 7))


class BatchLedgerTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.root = Path(tempfile.mkdtemp(prefix="hce-test-"))
        self.ledger = hce.BatchLedger(self.root)
        self.ledger.save({"schema": "histchoice.batch.v1", "state": "prepared",
                          "stages": {"acquisition": {"start_calls": 0},
                                     "shadow": {"start_calls": 0},
                                     "continuation": {"start_calls": 0}}})

    def test_stage_cap_refuses_the_ninth_acquisition_request(self):
        view = {"run_window_calls": 7}
        self.assertTrue(self.ledger.assert_room("acquisition", view)["stage_remaining"] >= 1)
        with self.assertRaises(hce.BatchRefused) as caught:
            self.ledger.assert_room("acquisition", {"run_window_calls": 8})
        self.assertEqual(caught.exception.reason_code, hce.REASON_STAGE_CAP)

    def test_shadow_cap_and_total_cap(self):
        data = self.ledger.load()
        data["stages"]["shadow"] = {"start_calls": 8}
        self.ledger.save(data)
        self.ledger.assert_room("shadow", {"run_window_calls": 8 + 39})
        with self.assertRaises(hce.BatchRefused) as caught:
            self.ledger.assert_room("shadow", {"run_window_calls": 8 + 40})
        self.assertEqual(caught.exception.reason_code, hce.REASON_STAGE_CAP)
        # 影子阶段内部没满、但整批已经到 60：总上限单独具名拒绝。
        data["stages"]["continuation"] = {"start_calls": 48}
        data["stages"]["shadow"] = {"start_calls": 48}
        self.ledger.save(data)
        with self.assertRaises(hce.BatchRefused) as caught:
            self.ledger.assert_room("shadow", {"run_window_calls": hce.TOTAL_REQUEST_CAP})
        self.assertEqual(caught.exception.reason_code, hce.REASON_TOTAL_CAP)

    def test_unreadable_ledger_fails_closed(self):
        with self.assertRaises(hce.BatchRefused) as caught:
            self.ledger.assert_room("shadow", {"run_window_calls": None})
        self.assertEqual(caught.exception.reason_code, hce.REASON_LEDGER_UNREADABLE)

    def test_stage_without_start_is_refused(self):
        with self.assertRaises(hce.BatchRefused) as caught:
            self.ledger.assert_room("diagnostic", {"run_window_calls": 0})
        self.assertEqual(caught.exception.reason_code, hce.REASON_STAGE_CAP)

    def test_attempt_rows_persist_after_refusal(self):
        attempt = self.ledger.begin_attempt(stage="shadow", kind="decision", slot=1, arm="A")
        self.ledger.finish_attempt(attempt, status="H", decision="START")
        rows = hce.BatchLedger(self.root).load()["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "H")
        self.assertEqual(rows[0]["decision"], "START")


class RootGuardTest(unittest.TestCase):
    def test_non_preregistered_root_is_refused(self):
        result = hce.prepare(Path("/tmp/not-the-preregistered-root"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_ROOT_NOT_PREREGISTERED)

    def test_relative_root_is_refused(self):
        result = hce.prepare(Path("relative/root"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_ROOT_NOT_ABSOLUTE)

    def test_reserved_old_root_is_refused(self):
        result = hce.prepare(Path("/root/nyairo-day-20261022/child"),
                             require_preregistered=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_ROOT_RESERVED)

    def test_non_empty_scratch_root_is_refused(self):
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="hce-nonempty-"))
        (root / "something").write_text("x", encoding="utf-8")
        result = hce.prepare(root, require_preregistered=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_ROOT_NOT_FRESH)


class RunGateTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.root = Path(tempfile.mkdtemp(prefix="hce-run-"))
        hce._write_json(self.root / hce.EXPERIMENT_DIRNAME / hce.PREREG_FILENAME,
                        {"schema": "histchoice.prereg.v1", "state_root": str(self.root)})
        hce.BatchLedger(self.root).save({"schema": "histchoice.batch.v1",
                                         "state": "prepared", "stages": {}})

    def test_run_requires_execute_approved(self):
        result = hce.run(self.root, execute_approved=False, require_preregistered=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_EXECUTE_FLAG_MISSING)

    def test_second_run_is_refused(self):
        batch = hce.BatchLedger(self.root)
        data = batch.load()
        data["run"] = {"started_at": "2026-10-29T00:00:00Z", "status": "running"}
        batch.save(data)
        # 只在本进程 mock 模块自己的时钟：截止前 1 小时，够走到 started 判据。
        frozen = hce.DEADLINE - timedelta(hours=1)
        with mock.patch.object(hce, "_utc_now", return_value=frozen):
            result = hce.run(self.root, execute_approved=True, require_preregistered=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_ALREADY_STARTED)

    def test_unprepared_root_is_refused(self):
        import tempfile

        empty = Path(tempfile.mkdtemp(prefix="hce-empty-"))
        result = hce.run(empty, execute_approved=True, require_preregistered=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_NOT_PREPARED)


class RequestParameterTest(unittest.TestCase):
    """不联网、不建实例，只构造真实 adapter/writer，核对最终 payload 的参数。"""

    def test_decision_and_content_payloads_match_the_pre_registration(self):
        measured = hce.measure_request_parameters()
        actual = measured["actual"]
        self.assertEqual(actual["decision_max_tokens"], hce.DECISION_OUTPUT_CAP)
        self.assertEqual(actual["content_max_tokens"], hce.CONTENT_OUTPUT_CAP)
        self.assertEqual(actual["decision_temperature"], 0.0)
        self.assertEqual(actual["content_temperature"], 0.0)
        self.assertFalse(actual["decision_stream"])
        self.assertFalse(actual["content_stream"])
        self.assertTrue(measured["ok"], measured.get("mismatches"))

    def test_hard_disconnect_blocks_http_connect(self):
        import http.client

        restore = hce._hard_disconnect(http.client.HTTPConnection, "connect")
        try:
            with self.assertRaises(AssertionError):
                http.client.HTTPConnection("offline.invalid", 443).connect()
        finally:
            restore()


class AuditTest(unittest.TestCase):
    def test_audit_of_unprepared_root_is_a_named_refusal(self):
        import tempfile

        result = hce.audit(Path(tempfile.mkdtemp(prefix="hce-audit-")))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], hce.REASON_NOT_PREPARED)

    def test_audit_returns_only_counts_and_refs(self):
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="hce-audit2-"))
        hce._write_json(root / hce.EXPERIMENT_DIRNAME / hce.PREREG_FILENAME,
                        {"state_root": "", "deadline": hce.DEADLINE.isoformat()})
        ledger = hce.BatchLedger(root)
        data = ledger.load()
        data["state"] = "partial"
        data["rows"] = [{"attempt_id": "a", "stage": "shadow", "status": "H", "pair": 1,
                         "arm": "A"}]
        ledger.save(data)
        result = hce.audit(root)
        self.assertTrue(result["ok"])
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("prior_experiences", blob)
        self.assertNotIn('"candidates"', blob)
        self.assertEqual(result["shadow"]["classes"].get("H"), 1)


class _Budget:
    def __init__(self, calls: int = 0) -> None:
        self.calls = calls

    def view(self) -> dict:
        return {"run_window_calls": self.calls, "utc_day_calls": self.calls,
                "state": "ok"}


class _Cycle:
    def __init__(self, reports=None) -> None:
        self.reports = list(reports or [])
        self.calls = 0

    def evaluate_once(self, **kwargs):
        self.calls += 1
        return self.reports.pop(0) if self.reports else {"outcome": "QUIET",
                                                         "llm_calls": 0}


def _ledger_with_stages() -> tuple["hce.BatchLedger", dict]:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="hce-safe-"))
    ledger = hce.BatchLedger(root)
    ledger.save({"schema": "histchoice.batch.v1", "state": "prepared",
                 "stages": {name: {"start_calls": 0} for name in hce.STAGE_CAPS}})
    return ledger, {"run_window_calls": 0, "utc_day_calls": 0, "state": "ok"}


class DefaultTransportTest(unittest.TestCase):
    """结构门默认值本身就得是离线替身：调用方忘了传也不会落到真实 provider。"""

    def test_structural_gate_builds_offline_transport_when_none_is_passed(self):
        seen: dict = {}
        original = hce.raw_cognition_adapter

        def spy(env, *, transport=None, admission=None):
            seen["transport"] = transport
            raise hce.BatchRefused("SPY_STOP", "stop before any service is built")

        hce.raw_cognition_adapter = spy
        try:
            with self.assertRaises(hce.BatchRefused):
                hce.structural_gate({"root": "/tmp/nowhere", "runtime": None,
                                     "admission": None, "assembled_env": {}},
                                    {"h_ref": "artifact:x", "candidate_refs": []})
        finally:
            hce.raw_cognition_adapter = original
        self.assertIsInstance(seen["transport"], hce.CaptureTransport)
        self.assertEqual(seen["transport"].calls, [])


class SendStopTest(unittest.TestCase):
    """截止/供应商失败在发送口立刻停；内容请求也不许偷发。"""

    def test_acquisition_does_not_send_after_deadline(self):
        ledger, view = _ledger_with_stages()
        cycle = _Cycle()
        result = hce.run_acquisition({"cycle": cycle}, ledger=ledger,
                                     budget=_Budget(), deadline_check=lambda: True)
        self.assertEqual(result["stop_reason"], hce.REASON_DEADLINE_PASSED)
        self.assertEqual(cycle.calls, 0)
        self.assertEqual(result["rounds"], [])

    def test_provider_failure_stops_the_batch_and_latches_content(self):
        ledger, view = _ledger_with_stages()
        latch = hce.SendLatch(lambda: False)
        cycle = _Cycle([{"outcome": "DECISION_FAILED", "llm_calls": 1,
                         "failure_class": "DECISION_ATTEMPT_FAILED:MODEL_TIMEOUT"}])
        result = hce.run_acquisition({"cycle": cycle}, ledger=ledger, budget=_Budget(),
                                     deadline_check=lambda: False, latch=latch)
        self.assertEqual(result["stop_reason"], hce.REASON_PROVIDER_FAILURE)
        self.assertTrue(latch.closed)
        self.assertEqual(cycle.calls, 1)  # 失败后没有再拿下一轮当重试

        class _Content:
            def __init__(self):
                self.calls = 0

            def complete(self, *a, **k):
                self.calls += 1
                return None

        inner = _Content()
        client = hce.LatchModelClient(inner, latch)
        with self.assertRaises(hce.HistoryGateError):
            client.complete(system_prompt="s", user_prompt="u")
        self.assertEqual(inner.calls, 0)

    def test_schema_retry_is_refused_before_any_send(self):
        ledger, view = _ledger_with_stages()
        original = hce.raw_cognition_adapter

        class _Inner:
            def __init__(self):
                self.calls = 0
                self.send_split_capable = True

            def build_prompt(self, request):
                return "{}", {}

            def invoke(self, request, *, schema_retry_index=0):
                self.calls += 1
                return None

        inner = _Inner()
        gate = hce.HistoryGateCognitionAdapter(inner, stage="shadow", ledger=ledger,
                                               budget=_Budget())
        with self.assertRaises(hce.HistoryGateError) as caught:
            gate.invoke(object(), schema_retry_index=1)
        self.assertEqual(caught.exception.reason_code, hce.REASON_ATTEMPT_RETRY_REFUSED)
        self.assertEqual(inner.calls, 0)
        self.assertIs(hce.raw_cognition_adapter, original)

    def test_shadow_stop_blocks_both_continuation_arms(self):
        latch = hce.SendLatch(lambda: False)
        reason = hce.shadow_blocks_continuation({"stop_reason": hce.REASON_LEDGER_DRIFT},
                                                latch)
        self.assertEqual(reason, hce.REASON_LEDGER_DRIFT)
        self.assertTrue(latch.closed)
        skipped = hce.skipped_continuation(reason)
        self.assertEqual(skipped["A"]["status"], "NOT_RUN")
        self.assertEqual(skipped["B"]["status"], "NOT_RUN")
        self.assertTrue(skipped["skipped"])

    def test_clean_shadow_leaves_the_latch_open(self):
        latch = hce.SendLatch(lambda: False)
        self.assertEqual(hce.shadow_blocks_continuation({"stop_reason": ""}, latch), "")
        self.assertFalse(latch.closed)


class BranchOrderTest(unittest.TestCase):
    """B 必须从 A 写之前的同一冻结起点复制出来；复制不了就两臂都不跑。"""

    def setUp(self):
        self.ledger, self.view = _ledger_with_stages()
        self.originals = (hce.clone_branch, hce.continue_round, hce.continue_branch)

    def tearDown(self):
        hce.clone_branch, hce.continue_round, hce.continue_branch = self.originals

    def test_clone_runs_before_arm_a(self):
        order: list[str] = []

        def clone(main, branch):
            order.append("clone")
            return {"ok": True, "copy": {"files_copied": 1, "sqlite_snapshots": 2}}

        def arm_a(bundle, **kwargs):
            order.append("A")
            return {"status": "RAN", "requests": 2}

        def arm_b(branch, **kwargs):
            order.append("B")
            return {"status": "RAN", "requests": 2}

        hce.clone_branch, hce.continue_round, hce.continue_branch = clone, arm_a, arm_b
        result = hce.run_continuation({"cycle": None}, ledger=self.ledger,
                                      budget=_Budget(), root=Path("/tmp/hce-order"),
                                      deadline_check=lambda: False)
        self.assertEqual(order, ["clone", "A", "B"])
        self.assertEqual(result["A"]["status"], "RAN")
        self.assertEqual(result["B"]["status"], "RAN")

    def test_clone_failure_runs_neither_arm(self):
        order: list[str] = []
        hce.clone_branch = lambda main, branch: (order.append("clone"),
                                                 {"ok": False,
                                                  "reason_code": hce.REASON_BRANCH_ISOLATION})[1]
        hce.continue_round = lambda *a, **k: (order.append("A"), {})[1]
        hce.continue_branch = lambda *a, **k: (order.append("B"), {})[1]
        result = hce.run_continuation({"cycle": None}, ledger=self.ledger,
                                      budget=_Budget(), root=Path("/tmp/hce-order"),
                                      deadline_check=lambda: False)
        self.assertEqual(order, ["clone"])
        for arm in ("A", "B"):
            self.assertEqual(result[arm]["status"], "NOT_RUN")
            self.assertEqual(result[arm]["reason_code"], hce.REASON_BRANCH_ISOLATION)

    def test_an_arm_over_two_requests_stops_before_arm_b(self):
        order: list[str] = []
        hce.clone_branch = lambda main, branch: (order.append("clone"), {"ok": True})[1]
        hce.continue_round = lambda *a, **k: (order.append("A"),
                                              {"status": "RAN", "requests": 3})[1]
        hce.continue_branch = lambda *a, **k: (order.append("B"), {})[1]
        result = hce.run_continuation({"cycle": None}, ledger=self.ledger,
                                      budget=_Budget(), root=Path("/tmp/hce-order"),
                                      deadline_check=lambda: False)
        self.assertEqual(order, ["clone", "A"])
        self.assertEqual(result["B"]["status"], "NOT_RUN")
        self.assertEqual(result["B"]["reason_code"], hce.REASON_ARM_OVER_BUDGET)


class ArmCeilingTest(unittest.TestCase):
    """每臂上限必须落在两臂共享的**真实持久账本**上：第 3 次发送在 reserve 前就被拒。

    这里刻意不用「mock continue_round 返回 requests=3」来证明——决策与内容两个发送口都
    包着真的 ``BudgetedCognitionAdapter`` / ``BudgetedModelClient``，指向同一个
    ``LifeRequestBudget``，只在最外层套一个计次 stub transport（不联网）。
    """

    def setUp(self):
        import tempfile

        from life_request_budget import LifeRequestBudget

        self.state_root = Path(tempfile.mkdtemp(prefix="hce-arm-budget-"))
        self.budget = LifeRequestBudget(self.state_root, utc_day_max=300,
                                        run_window_max=hce.TOTAL_REQUEST_CAP,
                                        run_id=hce.RUN_ID)
        self.budget.ensure_window(allow_new=True)
        self.root = Path(tempfile.mkdtemp(prefix="hce-arm-ledger-"))
        self.ledger = hce.BatchLedger(self.root)
        self.ledger.save({"schema": "histchoice.batch.v1", "state": "prepared",
                          "stages": {name: {"start_calls": 0}
                                     for name in hce.STAGE_CAPS}})

    def _arm(self, sends: int = 3) -> tuple[dict, dict]:
        """造一臂：决策/内容各包一次真实持久预留；``sends`` 次尝试里第 3 次必须被拒。"""

        from life_request_budget import (BudgetedCognitionAdapter, BudgetedModelClient,
                                         RequestBudgetError)

        class _Inner:
            send_split_capable = True

            def __init__(self) -> None:
                self.calls = 0

            def invoke(self, request, *, schema_retry_index: int = 0):
                self.calls += 1
                return None

            def complete(self, *args, **kwargs):
                self.calls += 1
                return None

        decision_inner, content_inner = _Inner(), _Inner()
        decision = BudgetedCognitionAdapter(decision_inner, self.budget)
        content = BudgetedModelClient(content_inner, self.budget)
        state: dict = {"refusals": [], "decision_calls": decision_inner.calls,
                       "content_calls": content_inner.calls}
        inner_state = (decision_inner, content_inner)

        class _Cycle:
            def evaluate_once(self, **_kwargs):
                for index in range(sends):
                    try:
                        if index % 2 == 0:
                            decision.invoke(object())
                        else:
                            content.complete()
                    except RequestBudgetError as exc:
                        state["refusals"].append(exc.reason_code)
                return {"outcome": "NO_ACTION", "llm_calls": decision_inner.calls}

        return {"cycle": _Cycle()}, {**state, "inners": inner_state}

    def _arm_state(self, bundle_and_state: tuple[dict, dict]) -> dict:
        _bundle, state = bundle_and_state
        decision_inner, content_inner = state["inners"]
        return {"refusals": state["refusals"], "sent": decision_inner.calls
                + content_inner.calls}

    def test_third_request_in_one_arm_is_refused_before_sending(self):
        from life_request_budget import REASON_RUN_WINDOW_LIMIT

        bundle, _state = self._arm(sends=3)
        result = hce.continue_round(bundle, ledger=self.ledger, budget=self.budget,
                                    trigger="exp:cont:A")
        self.assertEqual(result["status"], "RAN")
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["arm_request_cap"], hce.ARM_REQUEST_CAP)
        # 第 3 次尝试在 reserve 处就被拒，transport 只被调用 2 次。
        self.assertEqual(self._arm_state((bundle, _state))["sent"], 2)
        self.assertIn(REASON_RUN_WINDOW_LIMIT, _state["refusals"])
        # 计数不退、原上限恢复。
        self.assertEqual(hce.BatchLedger.spent(self.budget.view()), 2)
        self.assertEqual(self.budget.run_window_max, hce.TOTAL_REQUEST_CAP)

    def test_each_arm_gets_its_own_two_and_the_cap_is_restored(self):
        from life_request_budget import REASON_RUN_WINDOW_LIMIT

        arm_a = self._arm(sends=3)
        first = hce.continue_round(arm_a[0], ledger=self.ledger, budget=self.budget,
                                   trigger="exp:cont:A")
        arm_b = self._arm(sends=3)
        second = hce.continue_round(arm_b[0], ledger=self.ledger, budget=self.budget,
                                    trigger="exp:cont:B")
        self.assertEqual([first["requests"], second["requests"]], [2, 2])
        self.assertEqual([first["arm_ceiling"], second["arm_ceiling"]], [2, 4])
        self.assertEqual(hce.BatchLedger.spent(self.budget.view()), 4)
        for arm in (arm_a, arm_b):
            self.assertEqual(self._arm_state(arm)["sent"], 2)
            self.assertIn(REASON_RUN_WINDOW_LIMIT, arm[1]["refusals"])
        self.assertEqual(self.budget.run_window_max, hce.TOTAL_REQUEST_CAP)

    def test_the_arm_cap_never_loosens_the_stage_ceiling(self):
        bundle, _state = self._arm(sends=1)
        with hce.stage_ceiling(self.budget, "continuation") as limits:
            stage_value = limits["ceiling"]
            result = hce.continue_round(bundle, ledger=self.ledger,
                                        budget=self.budget, trigger="exp:cont:A")
            self.assertEqual(result["arm_ceiling"], min(stage_value, hce.ARM_REQUEST_CAP))
            # 回合结束后回到阶段上限，而不是总上限。
            self.assertEqual(self.budget.run_window_max, stage_value)
        self.assertEqual(self.budget.run_window_max, hce.TOTAL_REQUEST_CAP)

    def test_the_cap_is_restored_when_the_round_raises(self):
        class _Boom:
            def evaluate_once(self, **_kwargs):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            hce.continue_round({"cycle": _Boom()}, ledger=self.ledger,
                               budget=self.budget, trigger="exp:cont:A")
        self.assertEqual(self.budget.run_window_max, hce.TOTAL_REQUEST_CAP)


class HistoryRecorderTest(unittest.TestCase):
    """真实发送前的可比性门：只忽略历史正文；裁剪或第二变量当场拒绝。"""

    def setUp(self):
        self.experiences = [{"artifact_ref": "artifact:a", "version_ref": "ver:1",
                             "text": "真实经历正文"}]
        baseline_payload = self._payload("真实经历正文")
        self.baseline = {
            "fingerprint": hce.prompt_identity(baseline_payload),
            "text_fingerprint": hce.prompt_text_identity(
                json.dumps(baseline_payload, ensure_ascii=False), self.experiences),
            "history_shape": hce.payload_history_shape(baseline_payload)}

    @staticmethod
    def _payload(text: str, **extra) -> dict:
        payload = {"candidates": [{"candidate_id": "cand:1", "legal_decisions": ["START"]}],
                   "prior_experiences": [{"artifact_ref": "artifact:a",
                                          "version_ref": "ver:1", "text": text}]}
        payload.update(extra)
        return payload

    def _record(self, payload: dict) -> None:
        recorder = hce.make_history_recorder(self.baseline, self.experiences)
        recorder(payload, json.dumps(payload, ensure_ascii=False))

    def test_the_baseline_itself_passes(self):
        self._record(self._payload("真实经历正文"))

    def test_trimmed_history_is_refused_before_send(self):
        with self.assertRaises(hce.HistoryGateError) as caught:
            self._record(self._payload("真实"))
        self.assertEqual(caught.exception.reason_code, hce.REASON_SECOND_VARIABLE)

    def test_dropped_history_row_is_refused(self):
        payload = self._payload("真实经历正文")
        payload["prior_experiences"] = []
        with self.assertRaises(hce.HistoryGateError):
            self._record(payload)

    def test_a_second_variable_outside_history_is_refused(self):
        with self.assertRaises(hce.HistoryGateError) as caught:
            self._record(self._payload("真实经历正文",
                                       situation_context={"world": {"place": "street"}}))
        self.assertEqual(caught.exception.reason_code, hce.REASON_SECOND_VARIABLE)


if __name__ == "__main__":
    unittest.main()
