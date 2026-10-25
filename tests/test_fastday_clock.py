"""显式压缩演示时钟（``demo_business_clock``）的最小回归。

只证明机制，不冒充自主生活：

* 普通生产路径默认是 REAL_TIME，不产生任何 demo 文件；
* rate 的非法值具名拒绝，不静默回退；
* 一个逻辑日按业务时钟推进、horizon 钳位、冻结/恢复不倒退、重启不补跑；
* 一个域只允许一个 authority，域不匹配/损坏/缺失一律 fail closed；
* 实验额外上限 <=82 持久、跨重启/跨真实 UTC 日不刷新，96 总上限不被提高；
* 业务时间推进时，真实墙钟、真实 UTC 日与真实过期判定不变。

`_monotonic`/`wall` 都是注入的脚本替身；不联网、不调用模型、不写旧实例。
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_cycle as lifecycle  # noqa: E402
import demo_business_clock as dbc  # noqa: E402
import fast_day  # noqa: E402
import life_request_budget as lrb  # noqa: E402

WALL = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)


class _Mono:
    """A scripted monotonic clock: the test moves it explicitly."""

    def __init__(self, value: float = 1000.0) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="nyairo-fastday-clock-", dir="/tmp"))


class ProductionDefaultTest(unittest.TestCase):
    def test_no_demo_clock_on_the_default_path(self):
        root = _root()
        # 没有 prepare：业务客户端读不到任何时钟，具名 fail closed，而不是退回墙钟。
        with self.assertRaises(dbc.BusinessClockError) as caught:
            dbc.BusinessClockClient(dbc.clock_path(root)).now()
        self.assertEqual(caught.exception.reason_code, dbc.REASON_CLOCK_MISSING)
        self.assertFalse(dbc.clock_path(root).exists())
        # 普通路径没有 fastday.json，CLI status 也拒绝把它当演示实例。
        payload = fast_day._status_payload(root)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason_code"], fast_day.REASON_MODE_MISSING)


class RateValidationTest(unittest.TestCase):
    def test_rejects_zero_negative_nan_inf_and_over_bound(self):
        for bad in (0, -1, -0.5, float("nan"), float("inf"), float("-inf"), 24.0001, True):
            with self.assertRaises(dbc.BusinessClockError, msg=repr(bad)) as caught:
                dbc.validate_rate(bad)
            self.assertEqual(caught.exception.reason_code, dbc.REASON_CLOCK_RATE_INVALID)
        self.assertEqual(dbc.validate_rate(12), 12.0)
        self.assertEqual(dbc.validate_rate(24), 24.0)


class RootGateTest(unittest.TestCase):
    def test_prepare_requires_a_fresh_explicit_root(self):
        root = _root()
        (root / "already-there.txt").write_text("x", encoding="utf-8")
        refused = fast_day.prepare(root, run_id="d1", real_seconds=7200,
                                   logical_seconds=86400, max_new_requests=82)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], fast_day.REASON_ROOT_FRESH_REQUIRED)

        relative = fast_day.prepare(Path("relative-root"), run_id="d1", real_seconds=7200,
                                    logical_seconds=86400, max_new_requests=82)
        self.assertEqual(relative["reason_code"], fast_day.REASON_ROOT_REQUIRED)

        reserved = fast_day.prepare(Path("/root/nyairo-day-20261022/new"), run_id="d1",
                                    real_seconds=7200, logical_seconds=86400,
                                    max_new_requests=82)
        self.assertEqual(reserved["reason_code"], fast_day.REASON_ROOT_RESERVED)

        fresh = _root()
        ok = fast_day.prepare(fresh, run_id="d1", real_seconds=7200,
                              logical_seconds=86400, max_new_requests=82)
        self.assertTrue(ok["ok"], ok)
        again = fast_day.prepare(fresh, run_id="d1", real_seconds=7200,
                                 logical_seconds=86400, max_new_requests=82)
        self.assertEqual(again["reason_code"], fast_day.REASON_ROOT_FRESH_REQUIRED)


class ClockArithmeticTest(unittest.TestCase):
    def _authority(self, root: Path, mono: _Mono, *, rate: float = 12.0,
                   logical_seconds: float = 86400.0, pid: int = 4242,
                   host: str = "test-host", wall=None):
        return dbc.BusinessClockAuthority(
            root, run_id="r1", rate=rate, logical_seconds=logical_seconds,
            monotonic=mono, wall=wall or (lambda: WALL), pid=pid, host=host)

    def test_rate_and_horizon_clamp(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0, wall=lambda: WALL)
        mono = _Mono()
        authority = self._authority(root, mono)
        authority.acquire()
        start = authority.now()
        mono.advance(10.0)
        self.assertEqual((authority.now() - start).total_seconds(), 120.0)
        mono.advance(10_000.0)
        # 24 逻辑小时就是 horizon：再多真实时间也只钳在 horizon，不越界。
        self.assertEqual((authority.now() - start).total_seconds(), 86400.0)
        self.assertTrue(authority.exhausted)

    def test_freeze_resume_does_not_regress_or_catch_up(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0, wall=lambda: WALL)
        mono = _Mono()
        authority = self._authority(root, mono)
        authority.acquire()
        start = authority.now()
        mono.advance(5.0)
        frozen = authority.freeze(reason="operator")
        at_freeze = authority.now()
        self.assertEqual(frozen["state"], dbc.STATE_FROZEN)
        # 冻结期间真实时间照走，业务时间不动：不把离线/暂停算成她的生活。
        mono.advance(500.0)
        self.assertEqual(authority.now(), at_freeze)
        self.assertGreater(authority.now(), start)
        authority.resume()
        mono.advance(1.0)
        self.assertEqual((authority.now() - at_freeze).total_seconds(), 12.0)

    def test_restart_freezes_downtime_and_never_rewinds(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0, wall=lambda: WALL)
        mono = _Mono()
        first = self._authority(root, mono, pid=1111, host="host-a")
        first.acquire()
        mono.advance(10.0)
        first.checkpoint(ticks=3)
        checkpoint = first.now()
        # 上一进程消失（另一个 host 且心跳早已过期）：新的 authority 从检查点续，
        # 不按墙钟补跑，也不倒退。
        mono.advance(10_000.0)
        second = self._authority(
            root, mono, pid=2222, host="host-b",
            wall=lambda: WALL + timedelta(seconds=10_000))
        report = second.acquire()
        self.assertTrue(report["took_over"])
        self.assertTrue(report["downtime_frozen"])
        self.assertGreaterEqual(second.now(), checkpoint)
        self.assertEqual((second.now() - checkpoint).total_seconds(), 0.0)
        mono.advance(1.0)
        self.assertEqual((second.now() - checkpoint).total_seconds(), 12.0)

    def test_one_authority_per_domain_and_domain_mismatch(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0, wall=lambda: WALL)
        mono = _Mono()
        # pid 1 在同一台 Linux 上一定存活：模拟“另一个仍活着的 authority”。
        first = self._authority(root, mono, pid=1)
        first.acquire()
        import os

        other = dbc.BusinessClockAuthority(
            root, run_id="r1", rate=12.0, monotonic=mono, wall=lambda: WALL,
            pid=os.getpid(), host=first.host)
        with self.assertRaises(dbc.BusinessClockError) as caught:
            other.acquire()
        self.assertEqual(caught.exception.reason_code, dbc.REASON_CLOCK_AUTHORITY_HELD)
        # 域不匹配：客户端具名拒绝，不读成同域。
        client = dbc.BusinessClockClient(dbc.clock_path(root),
                                         expected_domain="fastday:other")
        with self.assertRaises(dbc.BusinessClockError) as mismatch:
            client.now()
        self.assertEqual(mismatch.exception.reason_code, dbc.REASON_CLOCK_DOMAIN_MISMATCH)

    def test_corrupt_clock_fails_closed(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0, wall=lambda: WALL)
        path = dbc.clock_path(root)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["rate"] = 0
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(dbc.BusinessClockError) as caught:
            dbc.BusinessClockClient(path).load()
        self.assertEqual(caught.exception.reason_code, dbc.REASON_CLOCK_RATE_INVALID)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(dbc.BusinessClockError) as broken:
            dbc.BusinessClockClient(path).load()
        self.assertEqual(broken.exception.reason_code, dbc.REASON_CLOCK_CORRUPT)
        path.unlink()
        with self.assertRaises(dbc.BusinessClockError) as missing:
            dbc.BusinessClockClient(path).now()
        self.assertEqual(missing.exception.reason_code, dbc.REASON_CLOCK_MISSING)


class RealSecurityDomainTest(unittest.TestCase):
    def test_logical_advance_leaves_real_utc_and_ledger_untouched(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=24.0, wall=lambda: WALL)
        mono = _Mono()
        authority = dbc.BusinessClockAuthority(
            root, run_id="r1", rate=24.0, monotonic=mono, wall=lambda: WALL, pid=777)
        authority.acquire()
        logical_start = authority.now()
        real_start = dbc.real_now()
        mono.advance(3600.0)  # 业务上过去 24 小时
        self.assertGreaterEqual((authority.now() - logical_start).total_seconds(), 24 * 3600)
        # 真实墙钟几乎没动：业务快进没有被写回系统时间。
        self.assertLess((dbc.real_now() - real_start).total_seconds(), 30.0)
        # 请求账本的真实 UTC 日仍由真实墙钟决定（不经过业务时钟）。
        ledger_day = lrb._utc_day()
        self.assertEqual(ledger_day, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.assertNotEqual(ledger_day, (WALL + timedelta(days=1)).strftime("%Y-%m-%d"))

    def test_request_ledger_utc_day_stays_real_after_a_logical_day(self):
        """真实账本的 UTC 日上限不被业务快进重置：已用满就仍拒。"""

        root = _root()
        dbc.create_clock(root, run_id="r1", rate=24.0, wall=lambda: WALL)
        mono = _Mono()
        authority = dbc.BusinessClockAuthority(
            root, run_id="r1", rate=24.0, monotonic=mono, wall=lambda: WALL, pid=779)
        authority.acquire()
        budget = lrb.LifeRequestBudget(root / "ledger", utc_day_max=1,
                                       run_window_max=96, run_id="r1")
        budget.reserve(kind=lrb.REQUEST_KIND_DECISION)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.reserve(kind=lrb.REQUEST_KIND_DECISION)
        self.assertEqual(caught.exception.reason_code, lrb.REASON_UTC_DAY_LIMIT)
        mono.advance(3600.0)  # 业务上过去 24 小时
        self.assertGreater((authority.now() - WALL).total_seconds(), 23 * 3600)
        with self.assertRaises(lrb.RequestBudgetError) as again:
            budget.reserve(kind=lrb.REQUEST_KIND_DECISION)
        self.assertEqual(again.exception.reason_code, lrb.REASON_UTC_DAY_LIMIT)

    def test_real_expiry_still_refuses_after_logical_advance(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=24.0, wall=lambda: WALL)
        mono = _Mono()
        authority = dbc.BusinessClockAuthority(
            root, run_id="r1", rate=24.0, monotonic=mono, wall=lambda: WALL, pid=778)
        authority.acquire()
        # 真实域里 1 小时有效的 grant；业务时钟快进一天不能把它续命。
        grant_expires = dbc.real_now() + timedelta(seconds=3600)
        mono.advance(3600.0)
        self.assertLess(dbc.real_now(), grant_expires)
        self.assertGreater(authority.now(), grant_expires)  # 只有逻辑时间越过了
        # 用一个真实语义的过期判定：真实 now 才是权威。
        self.assertTrue(dbc.real_now() < grant_expires)
        # 已经真实过期的 grant 仍然被拒。
        expired = dbc.real_now() - timedelta(seconds=1)
        self.assertFalse(dbc.real_now() < expired)


class ExperimentCapTest(unittest.TestCase):
    def test_cap_persists_across_restart_and_real_utc_day(self):
        root = _root()
        cap = dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=82)
        cap.create()
        self.assertEqual(dbc.HARD_CONFIG_MAX, 96)
        first = cap.view({"state": "ok", "run_id": "r1", "run_window_calls": 14})
        self.assertEqual(first["used"], 14)
        self.assertEqual(first["remaining"], 68)
        self.assertTrue(first["allowed"])
        # 重启：新对象读同一份文件，用量不刷新。
        again = dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=82)
        self.assertEqual(again.view({"state": "ok", "run_id": "r1",
                                     "run_window_calls": 14})["remaining"], 68)
        # 真实 UTC 跨日把「当日计数」归零、甚至账本被回滚：高水位不退钱。
        rolled = again.view({"state": "ok", "run_id": "r1", "run_window_calls": 0})
        self.assertEqual(rolled["used"], 14)
        self.assertEqual(rolled["remaining"], 68)

    def test_cap_cannot_be_raised_and_unreadable_ledger_fails_closed(self):
        root = _root()
        with self.assertRaises(dbc.BusinessClockError) as caught:
            dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=83)
        self.assertEqual(caught.exception.reason_code, dbc.REASON_EXPERIMENT_CAP_INVALID)
        with self.assertRaises(dbc.BusinessClockError) as zero:
            dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=0)
        self.assertEqual(zero.exception.reason_code, dbc.REASON_EXPERIMENT_CAP_INVALID)
        cap = dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=82)
        cap.create()
        unreadable = cap.view({"state": "corrupt", "run_window_calls": None})
        self.assertFalse(unreadable["allowed"])
        self.assertIsNone(unreadable["remaining"])
        self.assertEqual(unreadable["reason_code"], dbc.REASON_EXPERIMENT_CAP_UNREADABLE)
        # 文件本身被改大到 96/100：读取方拒绝，不把 96 总上限当成实验上限。
        path = dbc.experiment_path(root)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["max_new_requests"] = 96
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(dbc.BusinessClockError):
            dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=82).load()

    def test_exhausted_cap_refuses(self):
        root = _root()
        dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=3).create()
        cap = dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=3)
        view = cap.view({"state": "ok", "run_id": "r1", "run_window_calls": 3})
        self.assertFalse(view["allowed"])
        self.assertEqual(view["remaining"], 0)


class ReservationBoundaryTest(unittest.TestCase):
    """实验硬上限必须落在**每一次预留**上，而且任何回滚都不退款。"""

    def test_every_reservation_is_checked_against_the_experiment_cap(self):
        root = _root()
        dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=2).create()
        cap = dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=2)
        budget = lrb.LifeRequestBudget(root / "state", utc_day_max=96,
                                       run_window_max=96, run_id="r1")
        guarded = dbc.CappedRequestBudget(budget, cap)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        guarded.reserve(lrb.REQUEST_KIND_CONTENT)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            guarded.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(caught.exception.reason_code, dbc.REASON_EXPERIMENT_CAP_EXHAUSTED)
        # 预留即计费：两次成功之后账本与高水位都是 2，失败的那次没有写进去。
        self.assertEqual(guarded.view()["used"], 2)
        self.assertEqual(budget.view()["run_window_calls"], 2)

    def test_a_ledger_from_another_run_fails_closed(self):
        root = _root()
        dbc.ExperimentRequestCap(root, run_id="fastday-a", max_new_requests=5).create()
        cap = dbc.ExperimentRequestCap(root, run_id="fastday-a", max_new_requests=5)
        foreign = cap.view({"state": "ok", "run_id": "run:someone-else",
                            "run_window_calls": 0,
                            "windows": {"run:someone-else": {"calls": 0}}})
        self.assertFalse(foreign["allowed"])
        self.assertEqual(foreign["ledger"], "RUN_MISMATCH")
        other_window = cap.view({"state": "ok", "run_id": "",
                                 "run_window_calls": None,
                                 "windows": {"run:someone-else": {"calls": 3}}})
        self.assertFalse(other_window["allowed"])
        self.assertEqual(other_window["ledger"], "RUN_MISMATCH")

    def test_rollback_and_corruption_never_refund(self):
        root = _root()
        dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=3).create()
        cap = dbc.ExperimentRequestCap(root, run_id="r1", max_new_requests=3)
        budget = lrb.LifeRequestBudget(root / "state", utc_day_max=96,
                                       run_window_max=96, run_id="r1")
        guarded = dbc.CappedRequestBudget(budget, cap)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        # 账本被回滚到 0（比闸门自己记的 reserved_calls 还少）：这不是「还剩 1 次」，
        # 而是持久状态被回滚过。下一次预留前必须具名拒绝，不读成 0、不补写账本、不退款。
        rolled = budget.path.read_text(encoding="utf-8")
        data = json.loads(rolled)
        data["windows"]["r1"]["calls"] = 0
        budget.path.write_text(json.dumps(data), encoding="utf-8")
        view = guarded.view()
        self.assertFalse(view["allowed"])
        self.assertEqual(view["ledger"], "ROLLED_BACK")
        self.assertIsNone(view["used"])
        self.assertIsNone(view["remaining"])
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            guarded.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(caught.exception.reason_code,
                         dbc.REASON_EXPERIMENT_LEDGER_ROLLBACK)
        # 没有自动补写账本：回滚后的账本仍是 0，预留没有被悄悄写回去。
        self.assertEqual(budget.view()["run_window_calls"], 0)
        # 已有预留后把账本整个删掉：同样拒绝，不把缺失读成全新 0。
        budget.path.unlink()
        with self.assertRaises(lrb.RequestBudgetError) as deleted:
            guarded.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(deleted.exception.reason_code,
                         dbc.REASON_EXPERIMENT_LEDGER_ROLLBACK)
        self.assertFalse(budget.path.exists())
        # 账本损坏：不是 0，而是不可读 -> 拒绝。
        budget.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            guarded.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertIn(caught.exception.reason_code,
                      {lrb.REASON_STATE_CORRUPT, dbc.REASON_EXPERIMENT_CAP_UNREADABLE})

    def test_first_reservation_without_a_ledger_is_allowed_once(self):
        """全新实例第一次预留时账本缺失是合法的 0 初始化，不算回滚。"""

        root = _root()
        dbc.ExperimentRequestCap(root, run_id="fresh", max_new_requests=2).create()
        cap = dbc.ExperimentRequestCap(root, run_id="fresh", max_new_requests=2)
        budget = lrb.LifeRequestBudget(root / "state", utc_day_max=96,
                                       run_window_max=96, run_id="fresh")
        guarded = dbc.CappedRequestBudget(budget, cap)
        self.assertEqual(guarded.view()["ledger"], "OK")
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(guarded.view()["used"], 1)

    def test_concurrent_reservations_never_exceed_the_cap(self):
        """两个 writer 并发预留：上限检查与账本写入在同一把锁里，最多 cap 次成功。"""

        import threading

        root = _root()
        dbc.ExperimentRequestCap(root, run_id="race", max_new_requests=3).create()
        budget = lrb.LifeRequestBudget(root / "state", utc_day_max=96,
                                       run_window_max=96, run_id="race")
        cap = dbc.ExperimentRequestCap(root, run_id="race", max_new_requests=3)
        guarded = dbc.CappedRequestBudget(budget, cap)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            for _ in range(5):
                try:
                    guarded.reserve(lrb.REQUEST_KIND_DECISION)
                    with lock:
                        outcomes.append("ok")
                except lrb.RequestBudgetError as exc:
                    with lock:
                        outcomes.append(exc.reason_code)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.count("ok"), 3, outcomes)
        self.assertEqual(budget.view()["run_window_calls"], 3)
        self.assertEqual(guarded.view()["used"], 3)
        self.assertFalse(guarded.view()["allowed"])


class ExplicitLimitBindingTest(unittest.TestCase):
    """显式新授权总限（300/历史 14/新增 286）的绑定与默认 96/82 的兼容。

    总限只绑定到**这一个**新实例的元数据；默认路径仍是 96/0/82，任何抬高/越界都在
    预留或读取时具名拒绝，不静默换成新额度。
    """

    RUN_ID = "run:fastday-b"

    def test_default_96_and_82_stay_the_legacy_contract(self):
        root = _root()
        cap = dbc.ExperimentRequestCap(root, run_id=self.RUN_ID, max_new_requests=82)
        created = cap.create()
        self.assertEqual(created["authorized_total_limit"], 96)
        self.assertEqual(created["historical_requests"], 0)
        self.assertEqual(created["hard_config_max"], 96)
        view = cap.view({"state": "ok", "run_id": self.RUN_ID, "run_window_calls": 14})
        self.assertEqual(view["max_new_requests"], 82)
        self.assertEqual(view["authorized_total_limit"], 96)
        self.assertEqual(view["historical_requests"], 0)
        self.assertEqual(view["remaining"], 68)
        # 旧元数据没有新字段：按 96/0 解释，不升级、不改写文件。
        legacy = _root()
        path = dbc.experiment_path(legacy)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "fastday_experiment_request_cap", "schema_version": 1,
            "run_id": "r1", "max_new_requests": 82, "hard_config_max": 96,
            "baseline_run_window_calls": 0, "used_high_water": 0, "reserved_calls": 0,
        }), encoding="utf-8")
        old = dbc.ExperimentRequestCap(legacy, run_id="r1", max_new_requests=82)
        view = old.view({"state": "ok", "run_id": "r1", "run_window_calls": 0})
        self.assertTrue(view["allowed"])
        self.assertEqual(view["authorized_total_limit"], 96)
        self.assertEqual(view["historical_requests"], 0)

    def test_explicit_300_14_286_is_bound_to_the_instance(self):
        root = _root()
        cap = dbc.ExperimentRequestCap(root, run_id=self.RUN_ID,
                                       max_new_requests=286, total_limit=300,
                                       historical_requests=14)
        created = cap.create()
        self.assertEqual(created["authorized_total_limit"], 300)
        self.assertEqual(created["historical_requests"], 14)
        self.assertEqual(created["max_new_requests"], 286)
        # 历史基线只用于证明「历史+新增<=总限」，不写成本 run 的真实计数。
        self.assertEqual(created["baseline_run_window_calls"], 0)
        view = cap.view({"state": "ok", "run_id": self.RUN_ID, "run_window_calls": 0})
        self.assertEqual(view["authorized_total_limit"], 300)
        self.assertEqual(view["remaining"], 286)
        self.assertTrue(view["allowed"])

    def test_out_of_bounds_and_raised_metadata_are_refused(self):
        root = _root()
        invalid = [
            dict(max_new_requests=286, total_limit=300, historical_requests=15),
            dict(max_new_requests=287, total_limit=300, historical_requests=14),
            dict(max_new_requests=300, total_limit=300, historical_requests=14),
            dict(max_new_requests=286, total_limit=301, historical_requests=0),
            dict(max_new_requests=0, total_limit=300, historical_requests=14),
            dict(max_new_requests=-1, total_limit=300, historical_requests=14),
            dict(max_new_requests=286, total_limit=300, historical_requests=-1),
            dict(max_new_requests=286, total_limit=0, historical_requests=0),
            dict(max_new_requests=286, total_limit=300, historical_requests=14.0),
            dict(max_new_requests=286, total_limit=300.0, historical_requests=14),
            dict(max_new_requests=True, total_limit=300, historical_requests=14),
        ]
        for kwargs in invalid:
            with self.assertRaises(dbc.BusinessClockError, msg=repr(kwargs)) as caught:
                dbc.ExperimentRequestCap(root, run_id=self.RUN_ID, **kwargs)
            self.assertEqual(caught.exception.reason_code,
                             dbc.REASON_EXPERIMENT_CAP_INVALID)
        # 已存在的上限文件不能原地抬高：只改 max_new_requests 或授权总限/历史基线，
        # 读取都具名拒绝；不会把改过的文件当新额度。
        dbc.ExperimentRequestCap(root, run_id=self.RUN_ID, max_new_requests=286,
                                 total_limit=300, historical_requests=14).create()
        path = dbc.experiment_path(root)
        original = json.loads(path.read_text(encoding="utf-8"))
        for changes in ({"max_new_requests": 300},
                        {"max_new_requests": 300, "authorized_total_limit": 314},
                        {"historical_requests": 0},
                        {"authorized_total_limit": 96}):
            data = dict(original)
            data.update(changes)
            path.write_text(json.dumps(data), encoding="utf-8")
            cap = dbc.ExperimentRequestCap(root, run_id=self.RUN_ID,
                                           max_new_requests=286, total_limit=300,
                                           historical_requests=14)
            with self.assertRaises(dbc.BusinessClockError, msg=repr(changes)):
                cap.load()
        path.write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(
            dbc.ExperimentRequestCap(root, run_id=self.RUN_ID, max_new_requests=286,
                                     total_limit=300, historical_requests=14
                                     ).load()["max_new_requests"], 286)

    def test_prepare_refuses_invalid_limits_before_creating_the_root(self):
        combos = [
            dict(request_total_limit=-1, historical_requests=0, max_new_requests=1),
            dict(request_total_limit=301, historical_requests=0, max_new_requests=1),
            dict(request_total_limit=300, historical_requests=14, max_new_requests=287),
            dict(request_total_limit=300, historical_requests=20, max_new_requests=290),
            dict(request_total_limit=96, historical_requests=0, max_new_requests=96),
            dict(request_total_limit=300, historical_requests=14, max_new_requests=286,
                 max_cognition_calls_per_day=0),
        ]
        for index, combo in enumerate(combos):
            base = _root() / ("instance-%d" % index)
            kwargs = {"run_id": "limits-%d" % index, "real_seconds": 7200,
                      "logical_seconds": 86400}
            kwargs.update(combo)
            refused = fast_day.prepare(base, **kwargs)
            self.assertFalse(refused["ok"], refused)
            self.assertEqual(refused["reason_code"],
                             fast_day.REASON_REQUEST_LIMIT_INVALID, refused)
            self.assertFalse(base.exists(), combo)
        # 显式 300/14/286 在全新根上被接受（clock-only：只绑时钟与额度）。
        accepted = fast_day.prepare(_root(), run_id="limits-ok", real_seconds=7200,
                                    logical_seconds=86400, max_new_requests=286,
                                    request_total_limit=300, historical_requests=14)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["experiment"]["max_new_requests"], 286)
        self.assertEqual(accepted["experiment"]["authorized_total_limit"], 300)
        self.assertEqual(accepted["experiment"]["historical_requests"], 14)
        self.assertEqual(accepted["request_total_limit"], 300)

    def test_the_287th_reservation_is_refused_before_the_transport(self):
        root = _root()

        class _Inner:
            send_split_capable = True

            def __init__(self) -> None:
                self.calls = 0

            def invoke(self, request, *, schema_retry_index: int = 0):
                self.calls += 1
                return {"ok": True}

        dbc.ExperimentRequestCap(root, run_id=self.RUN_ID, max_new_requests=286,
                                 total_limit=300, historical_requests=14).create()
        ledger = lrb.LifeRequestBudget(root / "state", utc_day_max=300,
                                       run_window_max=300, run_id=self.RUN_ID)
        ledger.ensure_window(allow_new=True)
        cap = dbc.ExperimentRequestCap(root, run_id=self.RUN_ID, max_new_requests=286,
                                       total_limit=300, historical_requests=14)
        guarded = dbc.CappedRequestBudget(ledger, cap)
        cycle_budget = lifecycle.RoundBudget(root / "state", max_rounds_per_day=48,
                                             max_cognition_calls_per_day=300,
                                             requests=guarded)
        # 空账本：认知 0 且可用（不是把 300 读成已用尽）。
        self.assertEqual(cycle_budget.cognition_used_today(), 0)
        self.assertEqual(cycle_budget.cognition_remaining(), 300)
        self.assertEqual(cycle_budget.may_start_wake(), (True, "ALLOWED"))

        inner = _Inner()
        adapter = lrb.BudgetedCognitionAdapter(inner, guarded)
        adapter.invoke({"kind": "decision"})
        self.assertEqual(inner.calls, 1)
        self.assertEqual(ledger.view()["utc_day_calls"], 1)
        self.assertEqual(guarded.view()["utc_day_calls"], 1)
        self.assertEqual(guarded.view()["experiment_cap"]["used"], 1)
        self.assertEqual(cycle_budget.cognition_used_today(), 1)
        self.assertEqual(cycle_budget.cognition_remaining(), 299)
        for _ in range(285):
            adapter.invoke({"kind": "decision"})
        self.assertEqual(inner.calls, 286)
        self.assertEqual(ledger.view()["utc_day_calls"], 286)
        self.assertEqual(guarded.view()["experiment_cap"]["used"], 286)
        self.assertEqual(guarded.view()["experiment_cap"]["remaining"], 0)
        self.assertEqual(cycle_budget.cognition_remaining(), 14)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            adapter.invoke({"kind": "decision"})
        self.assertEqual(caught.exception.reason_code,
                         dbc.REASON_EXPERIMENT_CAP_EXHAUSTED)
        # 第 287 次在发送之前就被拒：真实 transport 计数没有超过 286。
        self.assertEqual(inner.calls, 286)
        self.assertEqual(ledger.view()["utc_day_calls"], 286)


class WrappedBudgetReadSurfaceTest(unittest.TestCase):
    """真实 fastday 装配的再现：实验闸门不能遮蔽 ``LifeRequestBudget`` 的读面。

    真实 run 的认知预算不是单独的对象：resident 用 ``CappedRequestBudget`` 包住
    实例账本后交给 cycle 的 ``RoundBudget``。包装器只回实验视图时，
    ``RoundBudget.cognition_used_today`` 读不到 ``utc_day_calls``，把空账本读成
    「96 已用尽」，每个 wake 在发请求前就 BLOCKED（2026-10-23 fastday-a 的首个
    故障）。这里用真对象、真文件、真锁复现这条装配；发送面是离线替身，不发请求。
    """

    RUN_ID = "run:fastday-read-surface"

    def _guarded(self, root: Path, *, cap_limit: int = 82):
        dbc.ExperimentRequestCap(root, run_id=self.RUN_ID,
                                 max_new_requests=cap_limit).create()
        ledger = lrb.LifeRequestBudget(root / "state", utc_day_max=96,
                                       run_window_max=96, run_id=self.RUN_ID)
        ledger.ensure_window(allow_new=True)
        cap = dbc.ExperimentRequestCap(root, run_id=self.RUN_ID,
                                       max_new_requests=cap_limit)
        return ledger, dbc.CappedRequestBudget(ledger, cap)

    def _cycle_budget(self, root: Path, guarded) -> "lifecycle.RoundBudget":
        return lifecycle.RoundBudget(root / "state", max_rounds_per_day=32,
                                     max_cognition_calls_per_day=96,
                                     requests=guarded)

    def test_empty_ledger_reads_as_available_not_96_exhausted(self):
        root = _root()
        _, guarded = self._guarded(root)
        view = guarded.view()
        # 包装器必须保留账本的读面：缺了 utc_day_calls 就是修复前的 96/0 误报。
        self.assertEqual(view.get("utc_day_calls"), 0)
        # ensure_window 已打开本 run 的窗口：账本可读，计数为 0。
        self.assertEqual(view.get("state"), "ok")
        cycle_budget = self._cycle_budget(root, guarded)
        self.assertEqual(cycle_budget.cognition_used_today(), 0)
        self.assertEqual(cycle_budget.cognition_remaining(), 96)
        self.assertEqual(cycle_budget.may_start_wake(), (True, "ALLOWED"))
        snapshot = cycle_budget.snapshot()
        self.assertEqual(snapshot["cognition_used_today"], 0)
        self.assertEqual(snapshot["cognition_remaining_today"], 96)
        self.assertEqual(snapshot["cognition_counter_source"], "life_request_budget")
        # 实验额度仍然是自己的具名视图，不是账本的 UTC 日计数冒充。
        self.assertEqual(snapshot["requests"]["experiment_cap"]["used"], 0)
        self.assertEqual(snapshot["requests"]["experiment_cap"]["remaining"], 82)

    def test_one_cognition_send_reserves_the_real_and_experiment_counters_together(self):
        root = _root()
        ledger, guarded = self._guarded(root)

        class _Inner:
            """离线发送替身：不建 socket、不碰 provider。"""

            send_split_capable = True

            def __init__(self) -> None:
                self.calls = 0

            def invoke(self, request, *, schema_retry_index: int = 0):
                self.calls += 1
                return {"ok": True}

        inner = _Inner()
        adapter = lrb.BudgetedCognitionAdapter(inner, guarded)
        adapter.invoke({"kind": "decision"})
        self.assertEqual(inner.calls, 1)  # 只到了本地替身
        self.assertEqual(ledger.view()["utc_day_calls"], 1)
        self.assertEqual(guarded.view()["utc_day_calls"], 1)
        self.assertEqual(guarded.view()["experiment_cap"]["used"], 1)
        cycle_budget = self._cycle_budget(root, guarded)
        self.assertEqual(cycle_budget.cognition_used_today(), 1)
        self.assertEqual(cycle_budget.cognition_remaining(), 95)

    def test_cap_exhaustion_and_corruption_still_fail_closed(self):
        root = _root()
        ledger, guarded = self._guarded(root, cap_limit=1)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            guarded.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(caught.exception.reason_code, dbc.REASON_EXPERIMENT_CAP_EXHAUSTED)
        # 坏账不是 0：认知预算按既有 fail-closed 语义报「已耗尽」，而不是免费额度。
        ledger.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(guarded.view()["state"], "corrupt")
        self.assertIsNone(guarded.view()["utc_day_calls"])
        cycle_budget = self._cycle_budget(root, guarded)
        self.assertEqual(cycle_budget.cognition_used_today(), 96)
        self.assertEqual(cycle_budget.cognition_remaining(), 0)
        self.assertEqual(cycle_budget.may_start_wake(),
                         (False, "COGNITION_BUDGET_EXHAUSTED"))
        with self.assertRaises(lrb.RequestBudgetError):
            guarded.reserve(lrb.REQUEST_KIND_DECISION)

    def test_rollback_and_a_foreign_run_still_refuse(self):
        root = _root()
        ledger, guarded = self._guarded(root, cap_limit=3)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        data = json.loads(ledger.path.read_text(encoding="utf-8"))
        data["windows"][self.RUN_ID]["calls"] = 0
        ledger.path.write_text(json.dumps(data), encoding="utf-8")
        view = guarded.view()
        self.assertEqual(view["ledger"], "ROLLED_BACK")
        self.assertFalse(view["allowed"])
        self.assertIsNone(view["used"])
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            guarded.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(caught.exception.reason_code,
                         dbc.REASON_EXPERIMENT_LEDGER_ROLLBACK)
        # 另一个 run 的账本/上限：具名拒绝，不把别人的窗口当自己的额度。
        other = _root()
        dbc.ExperimentRequestCap(other, run_id="run:someone-else",
                                 max_new_requests=3).create()
        cap = dbc.ExperimentRequestCap(other, run_id="run:someone-else",
                                       max_new_requests=3)
        foreign = cap.view({"state": "ok", "run_id": self.RUN_ID,
                            "run_window_calls": 0})
        self.assertFalse(foreign["allowed"])
        self.assertEqual(foreign["ledger"], "RUN_MISMATCH")

    def test_a_logical_day_does_not_advance_the_real_ledger_or_the_cap(self):
        root = _root()
        _, guarded = self._guarded(root)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        dbc.create_clock(root, run_id=self.RUN_ID, rate=24.0, wall=lambda: WALL)
        mono = _Mono()
        authority = dbc.BusinessClockAuthority(
            root, run_id=self.RUN_ID, rate=24.0, monotonic=mono,
            wall=lambda: WALL, pid=781)
        authority.acquire()
        mono.advance(3600.0)  # 业务上过去 24 小时
        self.assertGreater((authority.now() - WALL).total_seconds(), 23 * 3600)
        view = guarded.view()
        self.assertEqual(view["utc_day_calls"], 1)
        self.assertEqual(view["utc_day"], lrb._utc_day())
        self.assertEqual(view["experiment_cap"]["used"], 1)
        guarded.reserve(lrb.REQUEST_KIND_DECISION)
        view = guarded.view()
        self.assertEqual(view["utc_day_calls"], 2)
        self.assertEqual(view["experiment_cap"]["used"], 2)


class WallBudgetTest(unittest.TestCase):
    """真实墙钟累计：暂停/重启/崩溃都不能把已经花掉的墙钟借回来。"""

    class _Wall:
        def __init__(self, value: datetime = WALL) -> None:
            self.value = value

        def __call__(self) -> datetime:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value = self.value + timedelta(seconds=float(seconds))

    def test_consumed_wall_is_cumulative_across_pause_and_restart(self):
        root = _root()
        wall = self._Wall()
        ledger = dbc.WallBudget(root, run_id="w1", now=wall)
        self.assertEqual(ledger.create()["consumed_seconds"], 0)
        first = dbc.WallBudget(root, run_id="w1", now=wall)
        first.open()
        wall.advance(40.0)          # 运行
        # 暂停：逻辑时钟冻结，墙钟照走；这一段仍然属于本次真实运行。
        wall.advance(20.0)
        closed = first.close()
        self.assertEqual(closed["consumed_seconds"], 60.0)
        self.assertFalse(closed["open"])
        self.assertEqual(closed["remaining_seconds"], dbc.MAX_RUN_REAL_SECONDS - 60.0)
        # 同 run 重启：新的对象读同一份账本，剩余额度不因重启恢复。
        second = dbc.WallBudget(root, run_id="w1", now=wall)
        restart = second.recover()
        self.assertEqual(restart["recovered_seconds"], 0.0)
        self.assertEqual(restart["consumed_seconds"], 60.0)
        second.open()
        wall.advance(15.0)
        self.assertEqual(second.close()["consumed_seconds"], 75.0)
        # 每次都受剩余额度截断：单次不会超过当前剩余。
        self.assertLessEqual(75.0, dbc.MAX_RUN_REAL_SECONDS)

    def test_crashed_segment_is_recovered_conservatively(self):
        root = _root()
        wall = self._Wall()
        dbc.WallBudget(root, run_id="w2", now=wall).create()
        runner = dbc.WallBudget(root, run_id="w2", now=wall)
        runner.open()
        wall.advance(30.0)
        # 进程被 kill：没有 close；下一次启动按墙钟把整段保守扣掉（含停机时间）。
        wall.advance(10.0)
        restarted = dbc.WallBudget(root, run_id="w2", now=wall)
        recovered = restarted.recover()
        self.assertEqual(recovered["recovered_seconds"], 40.0)
        self.assertEqual(recovered["consumed_seconds"], 40.0)
        self.assertFalse(recovered["open"])
        restarted.open()
        wall.advance(5.0)
        self.assertEqual(restarted.close()["consumed_seconds"], 45.0)

    def test_a_live_segment_is_not_stolen_by_a_second_start(self):
        import socket

        root = _root()
        wall = self._Wall()
        ledger = dbc.WallBudget(root, run_id="w4", now=wall)
        ledger.create()
        ledger.open()
        path = dbc.wall_path(root)
        data = json.loads(path.read_text(encoding="utf-8"))
        # 同机仍存活的段主（pid 1）：第二个 start 的 recover 必须具名拒绝，
        # 不能把正在运行的墙钟段扣掉清空。
        data["open_pid"] = 1
        data["open_host"] = socket.gethostname()
        path.write_text(json.dumps(data), encoding="utf-8")
        wall.advance(5.0)
        with self.assertRaises(dbc.BusinessClockError) as held:
            dbc.WallBudget(root, run_id="w4", now=wall).recover()
        self.assertEqual(held.exception.reason_code, dbc.REASON_WALL_BUDGET_UNREADABLE)
        self.assertIsNotNone(json.loads(path.read_text(encoding="utf-8"))["open_since_utc"])
        # 段主已死：下一次启动按墙钟保守扣掉整段。
        data["open_pid"] = 999999
        path.write_text(json.dumps(data), encoding="utf-8")
        wall.advance(5.0)
        recovered = dbc.WallBudget(root, run_id="w4", now=wall).recover()
        self.assertGreaterEqual(recovered["recovered_seconds"], 9)
        self.assertFalse(recovered["open"])

    def test_exhausted_unreadable_and_foreign_wall_ledgers_fail_closed(self):
        root = _root()
        wall = self._Wall()
        ledger = dbc.WallBudget(root, run_id="w3", now=wall)
        ledger.create()
        ledger.open()
        wall.advance(dbc.MAX_RUN_REAL_SECONDS)
        view = ledger.close()
        self.assertEqual(view["consumed_seconds"], dbc.MAX_RUN_REAL_SECONDS)
        self.assertEqual(view["remaining_seconds"], 0.0)
        self.assertFalse(view["allowed"])
        # 已用完：再次 open 仍然被上层按 remaining=0 拒绝（open 本身只管段标记）。
        dbc.wall_path(root).write_text("{not json", encoding="utf-8")
        with self.assertRaises(dbc.BusinessClockError) as corrupt:
            dbc.WallBudget(root, run_id="w3", now=wall).view()
        self.assertEqual(corrupt.exception.reason_code, dbc.REASON_WALL_BUDGET_UNREADABLE)
        # 另一个 run 的墙钟账本不能复用（身份不符 fail closed）。
        other = _root()
        dbc.WallBudget(other, run_id="someone-else", now=wall).create()
        with self.assertRaises(dbc.BusinessClockError) as foreign:
            dbc.WallBudget(other, run_id="w3", now=wall).view()
        self.assertEqual(foreign.exception.reason_code, dbc.REASON_WALL_BUDGET_UNREADABLE)
        # 首次缺失按 0 初始化，不是不可读。
        fresh = dbc.WallBudget(_root(), run_id="w3", now=wall)
        self.assertEqual(fresh.view()["consumed_seconds"], 0.0)
        self.assertTrue(fresh.view()["allowed"])


class RootContainmentTest(unittest.TestCase):
    def test_path_escape_and_symlink_escape_are_refused(self):
        root = _root()
        outside = _root() / "state"
        self.assertFalse(fast_day._inside_root(root, outside))
        self.assertTrue(fast_day._inside_root(root, root / "state"))
        refused = fast_day.prepare(root, run_id="escape", real_seconds=7200,
                                   logical_seconds=86400, max_new_requests=82,
                                   state_root=outside)
        self.assertEqual(refused["reason_code"], fast_day.REASON_STATE_ROOT_OUTSIDE)
        # 符号链接指向根外也算逃逸。
        linked = root / "linked"
        root.mkdir(parents=True, exist_ok=True)
        linked.symlink_to(outside.parent, target_is_directory=True)
        self.assertFalse(fast_day._inside_root(root, linked / "state"))


class _StubCycle:
    """只为 runner 的 due/安静路径提供最小接口，不调用模型。"""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.enabled = True
        self.calls: list[dict] = []

    class _Budget:
        def may_start_wake(self):
            return True, "ALLOWED"

        def snapshot(self):
            return {}

    budget = _Budget()

    def evaluate_once(self, **kwargs):
        self.calls.append(kwargs)
        return {"outcome": "NO_MATERIAL", "llm_calls": 0}

    def maintain_waiting(self, *, observed_at=None):
        return {"state": "OK", "checked": 0}

    def status(self):
        return {"budget": {}}


class BusinessClockFlowsIntoRunnerTest(unittest.TestCase):
    def test_runner_windows_and_records_use_the_business_clock(self):
        import autonomous_life_runner as alr

        state_root = _root()
        cycle = _StubCycle(state_root)
        logical = [WALL]
        wake_runner = alr.AutonomousLifeRunner(
            cycle, state_root=state_root, env={}, interval_minutes=10,
            clock=lambda: logical[0], enabled=True)
        # 第一次没有上次检查点 -> 一定 due；之后同一逻辑时刻才是 NOT_DUE。
        first = wake_runner.run_due(act=False)
        self.assertNotEqual(first.status, alr.WAKE_NOT_DUE)
        self.assertEqual(len(cycle.calls), 1)
        self.assertEqual(cycle.calls[0]["observed_at"],
                         logical[0].isoformat())
        logical[0] = WALL + timedelta(minutes=5)
        not_due = wake_runner.run_due(act=False)
        self.assertEqual(not_due.status, alr.WAKE_NOT_DUE)
        # 业务时钟走到 10 分钟后，自然窗口重新打开——而不是等真实墙钟。
        logical[0] = WALL + timedelta(minutes=11)
        due_again = wake_runner.run_due(act=False)
        self.assertNotEqual(due_again.status, alr.WAKE_NOT_DUE)
        self.assertEqual(len(cycle.calls), 2)
        self.assertEqual(cycle.calls[1]["observed_at"],
                         (WALL + timedelta(minutes=11)).isoformat())


class ResidentClockRefusalTest(unittest.TestCase):
    def test_demo_mode_without_a_clock_refuses_before_any_runtime(self):
        import autonomous_life_resident as resident

        state_root = _root() / "state"
        instance = resident.AutonomousLifeResident(
            state_root=state_root,
            environment={"NYA02_FASTDAY_CLOCK": str(_root() / "missing" / "clock.json"),
                         "NYA02_FASTDAY_DOMAIN": "fastday:x"},
            poll_seconds=1)
        started = instance.start()
        self.assertFalse(started["ok"])
        self.assertEqual(started["reason"], dbc.REASON_CLOCK_MISSING)
        self.assertIsNone(instance.runtime)

    def test_domain_mismatch_refuses(self):
        import autonomous_life_resident as resident

        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0, wall=lambda: WALL)
        instance = resident.AutonomousLifeResident(
            state_root=root / "state",
            environment={"NYA02_FASTDAY_CLOCK": str(dbc.clock_path(root)),
                         "NYA02_FASTDAY_DOMAIN": "fastday:someone-else"},
            poll_seconds=1)
        started = instance.start()
        self.assertFalse(started["ok"])
        self.assertEqual(started["reason"], dbc.REASON_CLOCK_DOMAIN_MISMATCH)


class RealModeModelGateTest(unittest.TestCase):
    """真实模式缺模型配置要具名不可用，而不是静默装配确定性占位。"""

    def test_missing_profile_is_named_and_a_real_profile_is_reported_without_secrets(self):
        empty = _root()
        refused = fast_day.real_model_state(
            {"NYA02_PROFILE_HOME": str(empty), "CHIYO_PROFILE_HOME": str(empty)})
        self.assertFalse(refused["usable"])
        self.assertEqual(refused["reason"], "NO_REAL_MODEL_CONFIG")

        usable = fast_day.real_model_state({
            "NYA02_MODEL_BASE_URL": "http://127.0.0.1:9/v1",
            "NYA02_MODEL_NAME": "test-model",
            "NYA02_MODEL_API_KEY": "sk-not-a-real-key",
            "NYA02_MODEL_PROVIDER": "custom",
            "NYA02_PROFILE_HOME": str(empty),
        })
        self.assertTrue(usable["usable"], usable)
        self.assertEqual(usable["model"], "test-model")
        self.assertTrue(usable["api_key_present"])
        # 诊断里永不回传密钥本身。
        self.assertNotIn("sk-not-a-real-key", json.dumps(usable))


if __name__ == "__main__":
    unittest.main()
