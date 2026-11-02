#!/usr/bin/env python3
"""G2 A 路线离线准备驱动的单元测试（0 产品调用、0 网络、0 真实根）。

只覆盖驱动里可离线、可快速复跑的纯逻辑与门，不重跑 natural_v2 的整批（那是 heavy 的
``offline-check`` 最后一节）：影子构造/解析、20 对固定顺序、阶段/总量/单臂额度门、
预登记统计阈值与不完全出口、真实入口拒绝与已有 root 守卫。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"),
               str(REPO_ROOT / "components"), str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g2_next_offline as g2  # noqa: E402


class ShadowConstructionTest(unittest.TestCase):
    def test_construction_check_passes(self) -> None:
        result = g2.shadow_construction_check()
        self.assertTrue(result["ok"], result.get("blocking"))

    def test_pair_order_is_the_frozen_prereg_table(self) -> None:
        result = g2.shadow_construction_check()
        self.assertEqual(tuple(result["pair_order"]), g2.PREREG_PAIR_ORDER)
        self.assertEqual(result["pair_counts"], {"AB": 10, "BA": 10})
        self.assertEqual(len(result["pair_order"]), 20)


class BudgetGateTest(unittest.TestCase):
    def test_stage_total_and_arm_caps_all_reject(self) -> None:
        result = g2.budget_gate_check()
        self.assertTrue(result["ok"], result.get("blocking"))
        checks = result["checks"]
        self.assertTrue(checks["stage_cap_refuses_41st"])
        self.assertTrue(checks["total_cap_refuses_61st"])
        self.assertTrue(checks["arm_cap_is_two"])
        self.assertTrue(checks["arm_cap_refuses_third_send"])
        self.assertTrue(checks["arm_cap_restores_total_after"])

    def test_proposed_budget_is_52_not_authority(self) -> None:
        result = g2.proposed_budget_check()
        self.assertTrue(result["ok"], result.get("blocking"))
        self.assertEqual(result["proposed_total"], 52)
        # 如实暴露与 v1 阶段表的差异，不当成已设置。
        self.assertEqual(result["v1_continuation_cap"], 8)


class G2BudgetTest(unittest.TestCase):
    def test_52_is_held_by_the_shared_reserve(self) -> None:
        result = g2.g2_budget_gate_check()
        self.assertTrue(result["ok"], result.get("blocking"))
        self.assertEqual(result["ledger_calls"], 52)
        self.assertEqual(result["ledger_cap"], 52)
        checks = result["checks"]
        self.assertTrue(checks["fifty_third_refused_before_transport"])
        self.assertTrue(checks["failed_send_keeps_its_reservation"])
        self.assertTrue(checks["continuation_each_arm_refuses_3rd"])
        self.assertTrue(checks["unused_stage_slots_not_transferred"])

    def test_proposed_total_matches_v2_plan(self) -> None:
        result = g2.proposed_budget_check()
        self.assertTrue(result["checks"]["v2_plan_future_total_matches"])
        self.assertEqual(result["checks"]["v2_plan_future_continuation_matches"], True)


class StatisticsTest(unittest.TestCase):
    def test_threshold_and_incomplete_exits(self) -> None:
        result = g2.statistics_check()
        self.assertTrue(result["ok"], result.get("blocking"))
        self.assertLess(result["boundary"]["p"], 0.05)


class ProductEntryTest(unittest.TestCase):
    def test_real_entries_refuse_and_write_nothing(self) -> None:
        result = g2.product_entry_check()
        self.assertTrue(result["ok"], result.get("blocking"))
        self.assertEqual(result["refusal"]["reason_code"], g2.PRODUCT_RUN_NOT_AUTHORIZED)
        self.assertEqual(result["refusal"]["files_written"], 0)

    def test_cli_prepare_run_execute_refuse(self) -> None:
        for command in ("prepare", "run", "execute-approved"):
            self.assertEqual(g2.main([command]), g2.EXIT_REFUSED)
        self.assertEqual(g2.main([]), g2.EXIT_REFUSED)

    def test_existing_root_is_refused(self) -> None:
        import tempfile

        parent = Path(tempfile.mkdtemp(prefix="g2-next-test-guard-"))
        existing = parent / "already"
        existing.mkdir()
        refusal = g2.refuse_existing_root(existing)
        self.assertEqual(refusal["reason_code"], g2.EXISTING_ROOT_REFUSED)
        self.assertEqual(g2.refuse_existing_root(parent / "fresh"), {})


class _FakeMaterial:
    def __init__(self, candidate_ids):
        self.candidate_refs = list(candidate_ids)


class _FakeMaterializer:
    def __init__(self, candidates):
        self._candidates = list(candidates)

    def build_candidate_set(self, *, observed_at=None):
        return _FakeMaterial([row["candidate_id"] for row in self._candidates])


class _FakeCandidateRead:
    def __init__(self, candidates):
        self._by_id = {row["candidate_id"]: row for row in candidates}

    def get_candidate(self, candidate_id):
        return dict(self._by_id.get(str(candidate_id)) or {})


class _FakeRuntime:
    def __init__(self, activity_ref, candidates):
        self._activity_ref = activity_ref
        self.candidate_materializer = _FakeMaterializer(candidates)
        self.candidate_read = _FakeCandidateRead(candidates)

    def current_activity_ref(self):
        return self._activity_ref


WORK_A = "artifact:artifact:aaaa1111"
WORK_B = "artifact:artifact:bbbb2222"


def _fake_runtime():
    candidates = [
        {"candidate_id": "cand:same", "activity_ref": "actv:frozen",
         "target_refs": [WORK_A, "artifactversion:artifact-version:r1"]},
        {"candidate_id": "cand:other", "activity_ref": None,
         "target_refs": [WORK_B, "artifactversion:artifact-version:r2"]},
    ]
    return _FakeRuntime("actv:frozen", candidates)


def _shadow_for(candidate_id="cand:same", target=WORK_A, decision="CONTINUE"):
    return {
        "decision": decision,
        "selected_candidate_ref": candidate_id,
        "requested_rows": [{"candidate_id": candidate_id,
                            "target_refs": [target, "artifactversion:artifact-version:r1"],
                            "legal_decisions": ["DEFER", "CONTINUE"]}],
        "transport_calls": 1,
    }


class FrozenTargetGateTest(unittest.TestCase):
    def test_precheck_accepts_the_same_artifact_and_activity(self) -> None:
        result = g2._target_execution_precheck(
            _fake_runtime(), _shadow_for(), WORK_A, "actv:frozen", "2026-10-31T20:00:00+00:00")
        self.assertTrue(result["ok"], result.get("blocking"))

    def test_wrong_artifact_is_refused_without_fallback(self) -> None:
        result = g2._target_execution_precheck(
            _fake_runtime(), _shadow_for(), WORK_B, "actv:frozen",
            "2026-10-31T20:00:00+00:00")
        self.assertFalse(result["ok"])
        self.assertIn("shadow_candidate_targets_the_frozen_work", result["blocking"])
        # 门只拒，不换成另一件作品。
        self.assertEqual(result["candidate_id"], "cand:same")

    def test_wrong_activity_is_refused(self) -> None:
        result = g2._target_execution_precheck(
            _fake_runtime(), _shadow_for(), WORK_A, "actv:not-the-frozen-activity",
            "2026-10-31T20:00:00+00:00")
        self.assertFalse(result["ok"])
        self.assertIn("foreground_is_the_frozen_activity", result["blocking"])

    def test_non_continue_shadow_choice_is_refused(self) -> None:
        result = g2._target_execution_precheck(
            _fake_runtime(), _shadow_for(decision="NO_ACTION"), WORK_A,
            "actv:frozen", "2026-10-31T20:00:00+00:00")
        self.assertFalse(result["ok"])
        self.assertIn("shadow_decision_is_continue", result["blocking"])


class ScriptedContinueTransportTest(unittest.TestCase):
    def _prompt(self, target, legal=("DEFER", "CONTINUE")):
        import json as _json
        return _json.dumps({"candidates": [
            {"candidate_id": "cand:same", "target_refs": [target],
             "legal_decisions": list(legal)}]})

    def test_same_target_gets_continue(self) -> None:
        transport = g2._ScriptedContinueTransport(WORK_A)
        transport.complete(system_prompt="", user_prompt=self._prompt(WORK_A))
        self.assertEqual(transport.matched, ["cand:same"])

    def test_other_target_gets_no_action_not_fallback(self) -> None:
        transport = g2._ScriptedContinueTransport(WORK_A)
        transport.complete(system_prompt="", user_prompt=self._prompt(WORK_B))
        self.assertEqual(transport.matched, [])
        self.assertEqual(transport.requested_rows[0]["candidate_id"], "cand:same")


class MechanicBudgetTest(unittest.TestCase):
    def test_48_mechanical_calls_and_53rd_refusal(self) -> None:
        import tempfile
        from life_request_budget import LifeRequestBudget, REASON_RUN_WINDOW_LIMIT

        state = Path(tempfile.mkdtemp(prefix="g2-next-mechanic-"))
        budget = LifeRequestBudget(state, utc_day_max=300, run_window_max=52,
                                   run_id="run:g2-mechanic-test")
        budget.ensure_window(allow_new=True)
        rows = g2._mechanical_48_on(budget)
        self.assertEqual(budget.view()["run_window_calls"], 48)
        self.assertEqual(sum(1 for row in rows if row["sent"]), 47)
        self.assertFalse(rows[7]["sent"])  # 失败预留不退，仍然占格。

        for _ in range(4):
            self.assertTrue(g2._send_through_adapter(budget, g2._CountingSender())["sent"])
        self.assertEqual(budget.view()["run_window_calls"], 52)

        sender = g2._CountingSender()
        refused = g2._send_through_adapter(budget, sender)
        self.assertFalse(refused["sent"])
        self.assertEqual(sender.calls, 0)
        self.assertEqual(refused["reason_code"], REASON_RUN_WINDOW_LIMIT)

        reopened = LifeRequestBudget(state, utc_day_max=300, run_window_max=52,
                                     run_id="run:g2-mechanic-test")
        window = reopened.ensure_window()
        self.assertEqual(window["state"], "existing")
        self.assertEqual(int(window["calls"]), 52)
        self.assertEqual(int(reopened.view()["run_window_calls"]), 52)


if __name__ == "__main__":
    unittest.main()
