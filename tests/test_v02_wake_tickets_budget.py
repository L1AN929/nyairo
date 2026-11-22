#!/usr/bin/env python3
"""07 章 §二 WakeTicket 持久唤醒票 + 07 章 §四 日 token 预算维度（T3-wave2）。

覆盖：
* 持久票落盘与重启重载（state 内有界票账，随 ``_save_state_atomic`` 原子落盘，
  ``_load_state_readonly`` 载入，容量截断防膨胀）；
* 同 coalesce_key 合并 -> folded+1 而非新建票；control/foreground 旁路票照记并标注 bypass；
* 背压延期 open 票与 lease 过期后的 expired 定期（只读对账投影内完成，零写入）；
* 只读对账投影：近期票清单 + folded 计数 + est_tokens 合计；
* life_request_budget 日 token 预算维度：fail-closed（超 50k 拒）、无票 est 按保守常数、
  墙钟回拨不重置、损坏即拒、无退款；票 est 合计经 reserve(est_tokens=...) 进预算的接缝。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v02_runtime_governance_migration as rm_engine  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402
import life_request_budget as lrb  # noqa: E402


class WakeTicketLedgerTest(unittest.TestCase):
    """持久票账：落盘、重启重载、合并折叠、旁路标注、背压延期票。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v02-wake-tickets-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _probe(self, family: str, now_iso: str, **extra: object) -> dict:
        code, body = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": family,
             "summary_cn": f"{family} 信号 @{now_iso}", **extra},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200)
        return body

    def _view(self, now_iso: str) -> dict:
        code, body = rm_view.read_v02_runtime_migration_view(
            self.day_root, now_iso=now_iso)
        self.assertEqual(code, 200)
        return body["wake_coalescer"]

    def _state_tickets(self) -> list[dict]:
        exists, state = rm_engine._load_state_readonly(self.day_root)
        self.assertTrue(exists)
        return state["wake_router"]["wake_tickets"]

    def test_effective_wake_persists_a_fired_ticket_and_survives_restart_reload(self) -> None:
        self._probe("ambient_weather", "2026-11-14T10:00:00+08:00")
        tickets = self._state_tickets()
        self.assertEqual(len(tickets), 1)
        ticket = tickets[0]
        self.assertEqual(ticket["ticket_id"], "wt-0001")
        self.assertEqual(ticket["status"], rm_engine.WAKE_TICKET_STATUS_FIRED)
        self.assertEqual(ticket["coalesce_key"], "ambient_weather")
        self.assertEqual(ticket["priority"], rm_engine.WAKE_TICKET_PRIORITY_BACKGROUND)
        self.assertEqual(ticket["folded_count"], 0)
        self.assertEqual(ticket["est_tokens"], rm_engine.DEFAULT_WAKE_TICKET_EST_TOKENS)
        self.assertIsNone(ticket["bypass"])
        # lease = 语义合并窗
        self.assertEqual(ticket["lease_until"], "2026-11-14T10:05:00+08:00")

        # "重启"：下一次变异从磁盘重载（apply_runtime_migration_mutation 先
        # _load_state_readonly），票仍在、票号续排，不重开重复票。
        self._probe("other_family", "2026-11-14T11:00:00+08:00")
        tickets = self._state_tickets()
        self.assertEqual([t["ticket_id"] for t in tickets], ["wt-0001", "wt-0002"])

        code, body = rm_view.read_v02_runtime_migration_view(
            self.day_root, now_iso="2026-11-14T11:00:01+08:00")
        self.assertEqual(code, 200)
        proj_tickets = body["wake_coalescer"]["wake_tickets"]
        self.assertEqual([t["ticket_id"] for t in proj_tickets], ["wt-0001", "wt-0002"])

    def test_same_coalesce_key_within_window_folds_into_one_ticket(self) -> None:
        for minute in (0, 1, 2):
            self._probe("ambient_weather", f"2026-11-14T10:0{minute}:00+08:00")
        tickets = self._state_tickets()
        self.assertEqual(len(tickets), 1, "同 key 窗内合并：不新建票")
        self.assertEqual(tickets[0]["folded_count"], 2)
        self.assertEqual(
            [f["fold_kind"] for f in tickets[0]["fold_trace"]],
            [rm_engine.WAKE_TICKET_FOLD_KIND_COALESCE,
             rm_engine.WAKE_TICKET_FOLD_KIND_COALESCE],
        )
        summary = self._view("2026-11-14T10:02:01+08:00")["wake_tickets_summary"]
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["by_status"]["fired"], 1)
        self.assertEqual(summary["folded_total"], 2)

    def test_control_and_foreground_bypass_tickets_are_recorded_with_bypass_flag(self) -> None:
        self._probe("urgent_knock", "2026-11-14T10:00:00+08:00", is_control=True)
        persisted = self._state_tickets()
        self.assertEqual(len(persisted), 1)
        # foreground 旁路经既有 in-memory 路由入口（HTTP coalesce_probe 白名单不收 lane，
        # foreground 由运行时事件通道产生）。
        coalescer = rm_engine.SemanticWakeCoalescer()
        probe = coalescer.route_event(
            family="partner_message", summary_cn="前台消息", lane="foreground_chat",
            now_iso="2026-11-14T10:00:01+08:00")
        self.assertEqual(probe["wake_disposition"], "FOREGROUND_GUARANTEED")
        tickets = coalescer.state["wake_router"]["wake_tickets"]
        self.assertEqual(len(tickets), 1)
        by_bypass = {t["bypass"]: t for t in persisted + tickets}
        self.assertIn(rm_engine.WAKE_TICKET_BYPASS_CONTROL, by_bypass)
        self.assertIn(rm_engine.WAKE_TICKET_BYPASS_FOREGROUND, by_bypass)
        control = by_bypass[rm_engine.WAKE_TICKET_BYPASS_CONTROL]
        foreground = by_bypass[rm_engine.WAKE_TICKET_BYPASS_FOREGROUND]
        self.assertEqual(control["priority"], rm_engine.WAKE_TICKET_PRIORITY_CONTROL)
        self.assertEqual(control["status"], rm_engine.WAKE_TICKET_STATUS_FIRED)
        self.assertEqual(foreground["priority"], rm_engine.WAKE_TICKET_PRIORITY_FOREGROUND)
        self.assertEqual(foreground["status"], rm_engine.WAKE_TICKET_STATUS_FIRED)
        # 旁路零防抖直通：无合并租约
        self.assertIsNone(control["lease_until"])
        self.assertIsNone(foreground["lease_until"])

    def test_quota_backpressure_defers_into_an_open_ticket_then_expires(self) -> None:
        # 6 张不同 family 的有效唤醒（小时上限 6），第 7 个新 family 被背压延期。
        for i in range(6):
            self._probe(f"family_{i}", f"2026-11-14T10:00:0{i}+08:00")
        self._probe("family_overflow", "2026-11-14T10:00:10+08:00")
        tickets = self._state_tickets()
        statuses = [t["status"] for t in tickets]
        self.assertEqual(statuses.count(rm_engine.WAKE_TICKET_STATUS_FIRED), 6)
        self.assertEqual(statuses.count(rm_engine.WAKE_TICKET_STATUS_OPEN), 1)
        deferred = tickets[-1]
        self.assertEqual(deferred["coalesce_key"], "family_overflow")
        self.assertEqual(deferred["lease_until"], "2026-11-14T10:05:10+08:00")

        # 租约内的后续同 family 背压事件折进这张 open 票（07 章：超限不丢弃，折叠进同类票）
        self._probe("family_overflow", "2026-11-14T10:01:10+08:00")
        tickets = self._state_tickets()
        self.assertEqual(len(tickets), 7, "折进既有延期票，不新建")
        self.assertEqual(tickets[-1]["folded_count"], 1)
        self.assertEqual(
            tickets[-1]["fold_trace"][0]["fold_kind"],
            rm_engine.WAKE_TICKET_FOLD_KIND_QUOTA,
        )

        # 只读对账投影：lease 已过的 open 票就地定期为 expired（零写入）
        before = self._snapshot()
        view = self._view("2026-11-14T10:30:00+08:00")
        self.assertEqual(before, self._snapshot(), "对账投影必须零写入")
        expired = [t for t in view["wake_tickets"]
                   if t["status"] == rm_engine.WAKE_TICKET_STATUS_EXPIRED]
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["coalesce_key"], "family_overflow")
        summary = view["wake_tickets_summary"]
        self.assertEqual(summary["by_status"]["expired"], 1)
        self.assertEqual(summary["est_tokens_total"],
                         7 * rm_engine.DEFAULT_WAKE_TICKET_EST_TOKENS)

    def _snapshot(self) -> dict:
        state_file = self.day_root / "phone" / "v02_runtime_migration.json"
        return json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}

    def test_ticket_ledger_is_capacity_capped(self) -> None:
        coalescer = rm_engine.SemanticWakeCoalescer()
        # 同一族每次都跨过合并窗 -> 每次都开新窗新票
        base = datetime(2026, 11, 14, 8, 0, 0, tzinfo=rm_engine.DEFAULT_TZ)
        for i in range(rm_engine.MAX_WAKE_TICKETS + 12):
            ts = (base + timedelta(minutes=10 * i)).isoformat(timespec="seconds")
            coalescer.route_event(family=f"fam_{i}", summary_cn=f"事件 {i}", now_iso=ts)
        tickets = coalescer.state["wake_router"]["wake_tickets"]
        self.assertEqual(len(tickets), rm_engine.MAX_WAKE_TICKETS)
        self.assertEqual(tickets[-1]["ticket_id"], f"wt-{rm_engine.MAX_WAKE_TICKETS + 12:04d}")
        self.assertEqual(tickets[0]["ticket_id"], f"wt-{12 + 1:04d}")

    def test_token_estimate_rolls_into_the_budget_via_the_projection_seam(self) -> None:
        """接缝演示：票 est 合计（对账投影）-> reserve(est_tokens=...) 进日 token 预算。"""
        self._probe("ambient_weather", "2026-11-14T10:00:00+08:00")
        self._probe("ambient_light", "2026-11-14T10:01:00+08:00")
        summary = self._view("2026-11-14T10:02:00+08:00")["wake_tickets_summary"]
        self.assertEqual(summary["est_tokens_total"],
                         2 * rm_engine.DEFAULT_WAKE_TICKET_EST_TOKENS)
        budget = lrb.LifeRequestBudget(
            self.day_root / "life_state",
            utc_day_max=24,
            daily_token_budget=lrb.DEFAULT_DAILY_TOKEN_BUDGET,
        )
        record = budget.reserve(lrb.REQUEST_KIND_DECISION,
                                est_tokens=summary["est_tokens_total"])
        self.assertEqual(record["utc_day_tokens"], 2 * rm_engine.DEFAULT_WAKE_TICKET_EST_TOKENS)


class DailyTokenBudgetTest(unittest.TestCase):
    """日 token 预算维度：fail-closed、保守常数、回拨不重置、损坏即拒、无退款。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lrb-token-budget-"))
        self.root = self.tmp / "state"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _budget(self, **kwargs: object) -> lrb.LifeRequestBudget:
        kwargs.setdefault("daily_token_budget", lrb.DEFAULT_DAILY_TOKEN_BUDGET)
        return lrb.LifeRequestBudget(self.root, **kwargs)

    def test_refuses_when_the_daily_token_budget_is_spent(self) -> None:
        budget = self._budget(utc_day_max=24)
        budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=30_000)
        with self.assertRaises(lrb.RequestBudgetError) as overshoot:
            budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=30_000)
        self.assertEqual(overshoot.exception.reason_code, lrb.REASON_DAILY_TOKEN_BUDGET)
        view = budget.view()
        self.assertEqual(view["utc_day_tokens"], 30_000, "预检越界直接拒，不落账")
        self.assertEqual(view["daily_token_budget"], lrb.DEFAULT_DAILY_TOKEN_BUDGET)
        budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=20_000)
        self.assertEqual(budget.view()["utc_day_tokens"], 50_000)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=1)
        self.assertEqual(caught.exception.reason_code, lrb.REASON_DAILY_TOKEN_BUDGET)
        self.assertEqual(budget.view()["utc_day_tokens"], 50_000, "拒绝不落账（无退款路径）")

    def test_missing_estimate_is_charged_the_conservative_constant(self) -> None:
        # 24 次保守常数（48_000）低于 50_000：调用上限先绑定，token 维度不抢跑。
        # （补丁常量取自账本读数一次，常量 lambda 绝不能再调 view()——否则
        #  view -> _utc_day -> view 无限递归并 flock 自锁。）
        budget = self._budget(utc_day_max=24)
        record = budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(record["reservation"]["est_tokens"],
                         lrb.DEFAULT_CONSERVATIVE_TOKEN_RESERVATION)
        self.assertEqual(budget.view()["utc_day_tokens"],
                         lrb.DEFAULT_CONSERVATIVE_TOKEN_RESERVATION)
        day_key = budget.view()["utc_day"]
        with mock.patch.object(lrb, "_utc_day", lambda moment=None: day_key):
            for _ in range(23):
                budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(budget.view()["utc_day_tokens"],
                         24 * lrb.DEFAULT_CONSERVATIVE_TOKEN_RESERVATION)
        check = budget.check()
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason_code"], lrb.REASON_UTC_DAY_LIMIT)

    def test_explicit_none_daily_token_budget_keeps_call_only_accounting(self) -> None:
        budget = lrb.LifeRequestBudget(self.root, utc_day_max=300)
        for _ in range(30):
            budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=10_000)
        view = budget.view()
        self.assertIsNone(view["daily_token_budget"])
        self.assertEqual(view["utc_day_calls"], 30, "未配置 token 维度 -> 不设该上限")
        self.assertEqual(view["utc_day_tokens"], 300_000, "记账照常，供对账读")

    def test_wall_clock_rollback_does_not_reset_the_token_counter(self) -> None:
        budget = self._budget(utc_day_max=24)
        initial_day = budget.view()["utc_day"]
        midnight = datetime.strptime(initial_day, "%Y-%m-%d")
        next_day = (midnight + timedelta(days=1)).strftime("%Y-%m-%d")
        prev_day = (midnight - timedelta(days=1)).strftime("%Y-%m-%d")
        day_one = midnight.replace(hour=12, tzinfo=timezone.utc)
        day_two = day_one + timedelta(days=1)

        def clock(day: str):
            return mock.patch.object(lrb, "_utc_day", lambda moment=None: day)

        with clock(initial_day):
            budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=40_000)
        with clock(next_day):
            budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=5_000)
            view = budget.view()
            self.assertEqual(view["utc_day_tokens"], 5_000)
            self.assertEqual(view["day_tokens"][initial_day], 40_000)
            with clock(prev_day):
                rolled = budget.view()
                self.assertEqual(rolled["utc_day"], next_day, "回拨不开新账也不清零")
                self.assertEqual(rolled["utc_day_tokens"], 5_000)
                self.assertNotIn(prev_day, rolled["day_tokens"])
                flipped = budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=5_000)
                self.assertEqual(flipped["utc_day"], next_day)
                self.assertEqual(budget.view()["utc_day_tokens"], 10_000)

    def test_corrupt_ledger_fails_closed_also_for_the_token_dimension(self) -> None:
        budget = self._budget(utc_day_max=24)
        budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=10)
        self.root.joinpath(lrb.BUDGET_FILENAME).write_text("{not json", encoding="utf-8")
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=10)
        self.assertEqual(caught.exception.reason_code, lrb.REASON_STATE_CORRUPT)
        verdict = budget.check()
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], lrb.REASON_STATE_CORRUPT)

    def test_no_refund_api_and_invalid_estimates_are_refused(self) -> None:
        budget = self._budget(utc_day_max=24)
        self.assertFalse(hasattr(budget, "release"))
        self.assertFalse(hasattr(budget, "refund"))
        self.assertIn("no release API", budget.view()["refund_policy"])
        for bad in (-1, "2000", 1.5, True):
            with self.assertRaises(ValueError):
                budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=bad)
        self.assertEqual(budget.view()["utc_day_tokens"], 0, "非法 est 不落账")
        # est_tokens=0 合法（零预估照常占一次调用）
        record = budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=0)
        self.assertEqual(record["reservation"]["est_tokens"], 0)

    def test_ticket_estimates_summed_into_the_budget_refuse_over_50k_fail_closed(self) -> None:
        """票 est 汇总入预算：逐票 reserve，即将越过 50k 时预检即拒（fail-closed）。"""
        budget = self._budget(utc_day_max=24)
        accepted = 0
        for est in (20_000, 20_000, 20_000, 20_000):
            try:
                budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=est)
                accepted += 1
            except lrb.RequestBudgetError as exc:
                self.assertEqual(exc.reason_code, lrb.REASON_DAILY_TOKEN_BUDGET)
                break
        self.assertEqual(accepted, 2, "40k 后第三张 20k 票在预检阶段越界被拒")
        self.assertEqual(budget.view()["utc_day_tokens"], 40_000)


if __name__ == "__main__":
    unittest.main()
