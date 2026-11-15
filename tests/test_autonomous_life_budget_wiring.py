#!/usr/bin/env python3
"""07 章 §四 生产接线测试：票 est 合计 → reserve(est_tokens=...)；50k 日 token 预算生产启用。

覆盖：
* 生产 ``request_budget`` 默认启用 ``DEFAULT_DAILY_TOKEN_BUDGET``（50k）；env 旋钮
  ``CHIYO_AUTONOMOUS_DAILY_TOKEN_BUDGET`` 可减档/关闭，非法值拒绝启动；
* 启用前后行为一致证明：既有 24 次调用上限内逐次成功、token 记账逐次按保守常数
  累计到 48k，第 25 次仍由**调用上限**先拒绝（REQUEST_BUDGET_UTC_DAY_LIMIT），
  与"无 token 维度"的启用前基线逐字段一致——50k 维度在既有上限内零新拒绝；
* 票 est 合计经生产包装器 ``WakeTicketEstimateRequestBudget`` 进入 reserve
  （真实 wake 票账 → 2 票 4000；无票/票账不可读 → 保守常数 2000；显式 est 不被覆盖）；
* 包装器对 resident 段上限的属性**写**转发（utc_day_max/run_window_max 必须落到真实账本）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import autonomous_life_assembly as assembly  # noqa: E402
import life_request_budget as lrb  # noqa: E402
from components.life.scripts import (  # noqa: E402
    v02_runtime_governance_migration as rm_engine,
)
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402

ENV_BASE = {
    "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "24",
    "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "1000",
}
ENV_TOKEN_KNOB = "CHIYO_AUTONOMOUS_DAILY_TOKEN_BUDGET"


class ProductionTokenBudgetEnablementTest(unittest.TestCase):
    """生产 request_budget 的 50k 维度启用与宿主旋钮。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="assembly-budget-wiring-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _root(self, name: str) -> Path:
        root = self.tmp / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_request_budget_enables_the_daily_token_dimension_by_default(self) -> None:
        budget = assembly.request_budget(dict(ENV_BASE), self._root("default"))
        self.assertIsNotNone(budget)
        view = budget.view()
        self.assertEqual(view["daily_token_budget"], lrb.DEFAULT_DAILY_TOKEN_BUDGET)
        self.assertEqual(view["daily_token_budget"], 50_000)

    def test_no_ceilings_still_means_no_budget(self) -> None:
        self.assertIsNone(assembly.request_budget({}, self._root("unconfigured")))

    def test_env_knob_lowers_disables_and_refuses_garbage(self) -> None:
        lowered = assembly.request_budget(
            dict(ENV_BASE, **{ENV_TOKEN_KNOB: "12345"}), self._root("lowered"))
        self.assertEqual(lowered.view()["daily_token_budget"], 12_345)
        disabled = assembly.request_budget(
            dict(ENV_BASE, **{ENV_TOKEN_KNOB: "0"}), self._root("disabled"))
        self.assertIsNone(disabled.view()["daily_token_budget"],
                          "显式 0 关闭 token 维度（记账照常）")
        for bad in ("abc", "-5", "1.5"):
            with self.assertRaises(ValueError):
                assembly.request_budget(
                    dict(ENV_BASE, **{ENV_TOKEN_KNOB: bad}), self._root("bad"))

    def test_token_dimension_is_behavior_neutral_within_the_call_limit(self) -> None:
        """启用前后在既有 24 次调用上限内行为一致；50k 维度绝不抢跑。

        基线 = 生产 2026-11-14 之前的形态（同一 24 次调用上限、无 token 维度）。
        两者 24 次逐次成功、utc_day_tokens 逐次一致（保守常数 2000 累计到 48k），
        第 25 次都由调用上限以同一 reason_code 拒绝——启用 50k 在既有上限内零新拒绝。
        """
        baseline = lrb.LifeRequestBudget(self._root("baseline"),
                                         utc_day_max=24, run_window_max=1000)
        wired = assembly.request_budget(dict(ENV_BASE), self._root("wired"))
        for index in range(1, 25):
            before = baseline.reserve(lrb.REQUEST_KIND_DECISION)
            after = wired.reserve(lrb.REQUEST_KIND_DECISION)
            self.assertEqual(after["utc_day_calls"], index)
            self.assertEqual(after["utc_day_calls"], before["utc_day_calls"])
            self.assertEqual(after["reservation"]["est_tokens"],
                             lrb.DEFAULT_CONSERVATIVE_TOKEN_RESERVATION)
            self.assertEqual(after["utc_day_tokens"], before["utc_day_tokens"],
                             "启用前后 token 记账逐次一致（无票 → 保守常数）")
        self.assertEqual(wired.view()["utc_day_tokens"],
                         24 * lrb.DEFAULT_CONSERVATIVE_TOKEN_RESERVATION)
        self.assertLess(wired.view()["utc_day_tokens"], lrb.DEFAULT_DAILY_TOKEN_BUDGET,
                        "24 × 2000 = 48k < 50k：token 维度在调用上限内从不先绑定")
        for refused in (baseline, wired):
            with self.assertRaises(lrb.RequestBudgetError) as caught:
                refused.reserve(lrb.REQUEST_KIND_DECISION)
            self.assertEqual(caught.exception.reason_code, lrb.REASON_UTC_DAY_LIMIT,
                             "第 25 次由调用上限拒绝，而非 token 维度")


class WakeTicketEstimateWiringTest(unittest.TestCase):
    """票 est 合计经生产包装器进入每一次 reserve。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="assembly-ticket-est-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _instance(self, name: str) -> Path:
        day_root = self.tmp / name
        (day_root / "life" / "state").mkdir(parents=True, exist_ok=True)
        return day_root

    def _probe(self, day_root: Path, family: str, now_iso: str) -> None:
        code, body = rm_view.mutate_v02_runtime_migration_view(
            day_root,
            {"action": "coalesce_probe", "family": family,
             "summary_cn": f"{family} 信号 @{now_iso}"},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200)

    def test_wake_ticket_est_sum_charges_the_production_reserve(self) -> None:
        day_root = self._instance("wired")
        self._probe(day_root, "ambient_weather", "2026-11-14T10:00:00+08:00")
        self._probe(day_root, "ambient_light", "2026-11-14T10:01:00+08:00")
        code, body = rm_view.read_v02_runtime_migration_view(
            day_root, now_iso="2026-11-14T10:02:00+08:00")
        self.assertEqual(code, 200)
        summary = body["wake_coalescer"]["wake_tickets_summary"]
        self.assertEqual(summary["est_tokens_total"],
                         2 * rm_engine.DEFAULT_WAKE_TICKET_EST_TOKENS)

        budget = assembly.request_budget(dict(ENV_BASE), day_root / "life" / "state")
        record = budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(
            record["reservation"]["est_tokens"],
            summary["est_tokens_total"],
            "生产 reserve 的 est = 当期票账 est_tokens 合计（对账投影同源）")
        self.assertEqual(record["utc_day_tokens"], summary["est_tokens_total"])

    def test_explicit_est_tokens_are_never_overridden(self) -> None:
        day_root = self._instance("explicit")
        self._probe(day_root, "ambient_weather", "2026-11-14T10:00:00+08:00")
        budget = assembly.request_budget(dict(ENV_BASE), day_root / "life" / "state")
        record = budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=7)
        self.assertEqual(record["reservation"]["est_tokens"], 7,
                         "调用方显式给出的 est 优先于票账读数")

    def test_unreadable_ticket_state_falls_back_to_the_conservative_constant(self) -> None:
        day_root = self._instance("broken")
        phone = day_root / "phone"
        phone.mkdir(parents=True, exist_ok=True)
        (phone / "v02_runtime_migration.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(assembly.wake_ticket_est_total(day_root),
                          "票账损坏只回 None（保守常数），绝不猜一个更大的数")
        budget = assembly.request_budget(dict(ENV_BASE), day_root / "life" / "state")
        record = budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(record["reservation"]["est_tokens"],
                         lrb.DEFAULT_CONSERVATIVE_TOKEN_RESERVATION)

    def test_no_phone_dir_skips_the_reader_entirely(self) -> None:
        state_root = self.tmp / "plain" / "life" / "state"
        state_root.mkdir(parents=True)
        self.assertIsNone(assembly.wake_ticket_est_total(self.tmp / "plain"))
        self.assertIsNone(assembly._wake_ticket_day_root(state_root))
        budget = assembly.request_budget(dict(ENV_BASE), state_root)
        record = budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(record["reservation"]["est_tokens"],
                         lrb.DEFAULT_CONSERVATIVE_TOKEN_RESERVATION)


class WrapperForwardingTest(unittest.TestCase):
    """包装器除 reserve 外全部转发：resident 段上限的属性写必须落到真实账本。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="assembly-wrapper-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_segment_cap_attribute_writes_land_on_the_real_ledger(self) -> None:
        root = self.tmp / "cap"
        root.mkdir()
        budget = assembly.request_budget(dict(ENV_BASE), root)
        self.assertEqual(budget.utc_day_max, 24, "读转发")
        self.assertEqual(budget.run_window_max, 1000)
        # resident._apply_segment_cap 的写法：min(configured, used + cap) 直接赋值
        budget.utc_day_max = 10
        budget.run_window_max = 5
        self.assertEqual(budget.utc_day_max, 10)
        self.assertEqual(budget.view()["utc_day_max"], 10,
                         "写必须落到真实账本，读面看得到")
        self.assertEqual(budget.view()["run_window_max"], 5)
        self.assertTrue(budget.check()["allowed"])

    def test_view_check_failstop_and_attempt_guard_are_forwarded(self) -> None:
        root = self.tmp / "forward"
        root.mkdir()
        budget = assembly.request_budget(
            dict(ENV_BASE, NYA02_BOUNDED_FAILSTOP="1"), root)
        self.assertTrue(budget.failstop, "failstop 属性读转发")
        budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(budget.view()["utc_day_calls"], 1)
        self.assertEqual(budget.check()["reason_code"], "")
        budget.check_failstop()
        guard = budget.attempt_guard()
        with guard:
            pass
        budget.note_failure("PROVIDER_OR_TRANSPORT_FAILURE")
        with self.assertRaises(lrb.RequestBudgetError):
            budget.reserve(lrb.REQUEST_KIND_DECISION)

    def test_reserve_is_the_only_injected_hop(self) -> None:
        root = self.tmp / "hop"
        root.mkdir()
        budget = assembly.request_budget(dict(ENV_BASE), root)
        self.assertIsInstance(budget, assembly.WakeTicketEstimateRequestBudget)
        record = budget.reserve()  # 无 kind：内层默认 decision
        self.assertEqual(record["reservation"]["kind"], lrb.REQUEST_KIND_DECISION)


if __name__ == "__main__":
    unittest.main()
